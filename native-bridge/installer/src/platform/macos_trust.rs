//! Portable macOS trust policy (Task 4).
//!
//! The macOS adapter itself needs Security.framework and CoreFoundation and only
//! compiles on macOS. Everything that *decides* what the evidence means - the
//! policy-derived publisher designated requirement, the Team ID shape rule, the
//! fixed Gatekeeper assessment invocation and the Security.framework status
//! table - is pure, so it lives here and is compiled for the adapter *and* for
//! `cargo test` on every host. Each mapping below is asserted by a unit test
//! instead of being taken on faith on a native runner.

use std::ffi::OsString;
use std::io::Read;
use std::path::Path;
use std::process::{Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, mpsc};
use std::thread;
use std::time::{Duration, Instant};

use super::TrustVerdict;
use crate::model::Channel;

/// Prefix of the macOS publisher identity used in the release policy
/// (`team:<TEAMID>`).
pub(super) const TEAM_PUBLISHER_PREFIX: &str = "team:";

/// Absolute path of the documented Gatekeeper assessment tool.
///
/// The path is fixed and absolute (no `PATH` lookup) and the argv below is
/// passed as separate arguments (no shell), so a package path can never select
/// the executable or be re-parsed as an option.
pub(super) const SPCTL_PATH: &str = "/usr/sbin/spctl";

/// Upper bound on one Gatekeeper assessment, end to end (spawn, run, drain).
///
/// The assessment can consult the network (notarization and revocation data), so
/// the bound is not tiny, but an installer must never wait forever.
pub(super) const SPCTL_TIMEOUT: Duration = Duration::from_secs(30);

/// Bound on captured `spctl` output: one budget shared by stdout and stderr
/// together. Both streams are drained concurrently and completely so the child
/// never blocks on a full pipe, but no more than this many bytes are retained.
pub(super) const MAX_SPCTL_OUTPUT_BYTES: usize = 64 * 1024;

/// The Team ID named by a `team:<ID>` publisher, when it is well formed.
pub(super) fn team_publisher(publisher: &str) -> Option<&str> {
    let team = publisher.strip_prefix(TEAM_PUBLISHER_PREFIX)?;
    is_valid_team_id(team).then_some(team)
}

/// Every Team ID a release policy authorizes.
///
/// A policy that lists no well-formed `team:<ID>` publisher authorizes no macOS
/// publisher at all; the adapter denies rather than pinning to a Team ID it
/// merely observed.
pub(super) fn authorized_teams(publishers: &[String]) -> Vec<&str> {
    publishers
        .iter()
        .filter_map(|publisher| team_publisher(publisher))
        .collect()
}

/// Whether the OS-observed Team ID is one the release policy authorizes.
pub(super) fn is_authorized_team(publishers: &[String], team_id: &str) -> bool {
    is_valid_team_id(team_id) && authorized_teams(publishers).contains(&team_id)
}

/// A Team ID is exactly ten uppercase ASCII letters or digits.
///
/// This is Apple's documented Team ID shape. Enforcing it before interpolation
/// also means a Team ID can never inject designated-requirement syntax.
pub(super) fn is_valid_team_id(team_id: &str) -> bool {
    team_id.len() == 10
        && team_id
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit())
}

/// Build the publisher designated requirement for an authorized Team ID.
///
/// Returns `None` for anything that is not a well-formed Team ID, so the
/// requirement can never be built from free-form OS strings.
///
/// The caller authorizes the observed Team ID against the release policy first
/// ([`is_authorized_team`]); pinning the requirement to that authorized ID is
/// then equivalent to an OR over every authorized Team ID, while the requirement
/// text stays a single, well-formed team pin.
///
/// Production additionally requires the Developer ID intermediate
/// (`1.2.840.113635.100.6.2.6`) and Developer ID Application leaf
/// (`1.2.840.113635.100.6.1.13`) markers; development/fixture builds accept the
/// Apple-anchored leaf team identity only, so ad-hoc fixtures stay testable.
pub(super) fn publisher_requirement(channel: Channel, team_id: &str) -> Option<String> {
    if !is_valid_team_id(team_id) {
        return None;
    }
    if channel.is_production() {
        Some(format!(
            "anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */ \
             and certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */ \
             and certificate leaf[subject.OU] = \"{team_id}\""
        ))
    } else {
        Some(format!(
            "anchor apple generic and certificate leaf[subject.OU] = \"{team_id}\""
        ))
    }
}

/// Publisher identity for a self-signed completion leaf: the SHA-256 of the
/// certificate DER (`sha256:<64 lowercase hex>`), the macOS counterpart of the
/// Windows publisher fingerprint.
pub(super) const CERTIFICATE_PUBLISHER_PREFIX: &str = "sha256:";

/// The certificate digest named by a `sha256:<hex>` publisher, when well formed.
pub(super) fn certificate_publisher(publisher: &str) -> Option<&str> {
    let digest = publisher.strip_prefix(CERTIFICATE_PUBLISHER_PREFIX)?;
    is_lower_hex(digest, 32).then_some(digest)
}

/// Every certificate digest a policy authorizes.
pub(super) fn authorized_certificates(publishers: &[String]) -> Vec<&str> {
    publishers
        .iter()
        .filter_map(|publisher| certificate_publisher(publisher))
        .collect()
}

/// Whether the leaf-certificate SHA-256 the OS observed is authorized.
pub(super) fn is_authorized_certificate(publishers: &[String], digest_sha256: &str) -> bool {
    is_lower_hex(digest_sha256, 32) && authorized_certificates(publishers).contains(&digest_sha256)
}

/// Build the leaf-pinned requirement for a self-signed completion identity.
///
/// `H"..."` is Apple's SHA-1 certificate hash (20 bytes). The requirement
/// deliberately carries no `anchor apple generic`: a self-signed acceptance
/// leaf has no Apple-anchored chain, and the pin is the whole authorization.
/// Returns `None` for anything that is not 40 lowercase hex characters, so the
/// requirement can never be built from free-form text.
pub(super) fn certificate_requirement(certificate_sha1: &str) -> Option<String> {
    is_lower_hex(certificate_sha1, 20)
        .then(|| format!("certificate leaf = H\"{certificate_sha1}\""))
}

/// Exactly `bytes` lowercase hexadecimal characters.
fn is_lower_hex(text: &str, bytes: usize) -> bool {
    text.len() == bytes * 2
        && text
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

/// The fixed Gatekeeper assessment argv for `carrier`.
/// `--assess --type exec` is the documented execution-policy assessment; the
/// carrier path is one argument, so it cannot introduce further options through
/// word splitting.
pub(super) fn assessment_argv(carrier: &Path) -> Vec<OsString> {
    let mut argv = vec![
        OsString::from("--assess"),
        OsString::from("--type"),
        OsString::from("exec"),
        OsString::from("--verbose"),
    ];
    argv.push(carrier.as_os_str().to_owned());
    argv
}

/// What a Gatekeeper assessment established.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum SpctlOutcome {
    /// `spctl` accepted the execution policy.
    Accepted,
    /// `spctl` explicitly rejected the code.
    Denied,
    /// The assessment could not be formed (spawn failure, timeout, error exit,
    /// indeterminate output): no trust evidence exists.
    Unavailable,
}

/// Classify one Gatekeeper assessment result.
///
/// `status` is the process exit status (`None` when the process could not be
/// spawned, waited for, or exceeded the timeout) and `output` is the captured
/// stdout/stderr. `spctl` documents exit 0 as accepted and 2 as rejected; any
/// other non-zero status is an assessment *error*, not a denial. The captured
/// text is only consulted to *tighten* an unknown non-zero status into a denial
/// when `spctl` itself printed its rejection verdict - it can never turn an
/// error into an acceptance.
pub(super) fn classify_assessment(status: Option<i32>, output: &[u8]) -> SpctlOutcome {
    match status {
        Some(0) => SpctlOutcome::Accepted,
        Some(2) => SpctlOutcome::Denied,
        Some(_) if output.windows(8).any(|window| window == b"rejected") => SpctlOutcome::Denied,
        _ => SpctlOutcome::Unavailable,
    }
}

/// Poll interval while waiting for the assessment child.
#[cfg_attr(not(any(target_os = "macos", unix)), allow(dead_code))]
const POLL_INTERVAL: Duration = Duration::from_millis(25);

/// What one bounded child run produced.
#[cfg_attr(not(any(target_os = "macos", unix)), allow(dead_code))]
pub(super) struct Captured {
    /// The exit status, or `None` when the child could not be waited for or was
    /// killed at the deadline (no verdict may be invented from that).
    pub(super) status: Option<ExitStatus>,
    /// Captured stdout followed by stderr.
    pub(super) output: Vec<u8>,
}

/// Claim up to `want` bytes of the shared capture budget.
#[cfg_attr(not(any(target_os = "macos", unix)), allow(dead_code))]
fn claim(budget: &AtomicUsize, want: usize) -> usize {
    let mut current = budget.load(Ordering::Relaxed);
    loop {
        let take = current.min(want);
        if take == 0 {
            return 0;
        }
        match budget.compare_exchange(current, current - take, Ordering::AcqRel, Ordering::Relaxed)
        {
            Ok(_) => return take,
            Err(actual) => current = actual,
        }
    }
}

/// Drain `reader` to the end, retaining only what the shared `budget` allows.
///
/// A child must be drained completely (a full pipe blocks it in `write`), so the
/// reader keeps reading after the budget is spent and simply discards the rest.
#[cfg_attr(not(any(target_os = "macos", unix)), allow(dead_code))]
fn drain(reader: &mut impl Read, budget: &AtomicUsize) -> Vec<u8> {
    let mut retained = Vec::new();
    let mut chunk = [0u8; 8192];
    loop {
        match reader.read(&mut chunk) {
            Ok(0) => return retained,
            Ok(read) => {
                let keep = claim(budget, read);
                retained.extend_from_slice(&chunk[..keep]);
            }
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
            Err(_) => return retained,
        }
    }
}

/// Run `command` to completion within `timeout`, capturing at most `budget` bytes
/// of stdout and stderr **combined**.
///
/// Both pipes are drained concurrently while the child runs, so output larger
/// than a pipe buffer cannot stall it. One deadline covers the whole run: at
/// expiry the child is killed and reaped, and collecting the captured output
/// never waits past it, even when a grandchild keeps a pipe open.
#[cfg_attr(not(any(target_os = "macos", unix)), allow(dead_code))]
pub(super) fn run_bounded(
    command: &mut Command,
    timeout: Duration,
    budget: usize,
) -> std::io::Result<Captured> {
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let deadline = Instant::now() + timeout;
    let mut child = command.spawn()?;

    let budget = Arc::new(AtomicUsize::new(budget));
    let (sender, receiver) = mpsc::channel::<(bool, Vec<u8>)>();
    let mut readers = 0usize;
    if let Some(mut stdout) = child.stdout.take() {
        let (budget, sender) = (Arc::clone(&budget), sender.clone());
        thread::spawn(move || {
            let _ = sender.send((true, drain(&mut stdout, &budget)));
        });
        readers += 1;
    }
    if let Some(mut stderr) = child.stderr.take() {
        let (budget, sender) = (Arc::clone(&budget), sender.clone());
        thread::spawn(move || {
            let _ = sender.send((false, drain(&mut stderr, &budget)));
        });
        readers += 1;
    }
    drop(sender);

    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
                thread::sleep(POLL_INTERVAL);
            }
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
        }
    };

    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    for _ in 0..readers {
        let wait = deadline.saturating_duration_since(Instant::now());
        match receiver.recv_timeout(wait) {
            Ok((true, bytes)) => stdout = bytes,
            Ok((false, bytes)) => stderr = bytes,
            // The deadline passed (or a reader is gone): keep what exists. A
            // reader still blocked on a pipe some other process holds open is
            // abandoned, never joined.
            Err(_) => break,
        }
    }
    stdout.extend(stderr);
    Ok(Captured {
        status,
        output: stdout,
    })
}

// CSCommon.h OSStatus values. The SecBase certificate values
// (-67818/-67820/-67843/-67844) are the long-standing Security.framework
// certificate codes.
const ERR_SEC_CS_UNIMPLEMENTED: i32 = -67072;
const ERR_SEC_CS_INVALID_OBJECT_REF: i32 = -67071;
const ERR_SEC_CS_INVALID_FLAGS: i32 = -67070;
const ERR_SEC_CS_OBJECT_REQUIRED: i32 = -67069;
const ERR_SEC_CS_STATIC_CODE_NOT_FOUND: i32 = -67068;
const ERR_SEC_CS_UNSIGNED: i32 = -67062;
const ERR_SEC_CS_SIGNATURE_FAILED: i32 = -67061;
const ERR_SEC_CS_SIGNATURE_NOT_VERIFIABLE: i32 = -67060;
const ERR_SEC_CS_SIGNATURE_UNSUPPORTED: i32 = -67059;
const ERR_SEC_CS_BAD_DICTIONARY_FORMAT: i32 = -67058;
const ERR_SEC_CS_RESOURCES_NOT_SEALED: i32 = -67057;
const ERR_SEC_CS_RESOURCES_NOT_FOUND: i32 = -67056;
const ERR_SEC_CS_RESOURCES_INVALID: i32 = -67055;
const ERR_SEC_CS_BAD_RESOURCE: i32 = -67054;
const ERR_SEC_CS_RESOURCE_RULES_INVALID: i32 = -67053;
const ERR_SEC_CS_REQ_INVALID: i32 = -67052;
const ERR_SEC_CS_REQ_UNSUPPORTED: i32 = -67051;
const ERR_SEC_CS_REQ_FAILED: i32 = -67050;
const ERR_SEC_CS_BAD_OBJECT_FORMAT: i32 = -67049;
const ERR_SEC_CS_INTERNAL_ERROR: i32 = -67048;
const ERR_SEC_CS_SIGNATURE_INVALID: i32 = -67045;
const ERR_SEC_CS_HELPER_FAILED: i32 = -67019;
const ERR_SEC_CS_BAD_BUNDLE_FORMAT: i32 = -67028;
const ERR_SEC_CS_BAD_MAIN_EXECUTABLE: i32 = -67010;
const ERR_SEC_CS_UNSEALED_APP_ROOT: i32 = -67014;
const ERR_SEC_CS_RESOURCE_NOT_SUPPORTED: i32 = -67016;
const ERR_SEC_CS_UNSEALED_FRAMEWORK_ROOT: i32 = -67008;
const ERR_SEC_CS_WEAK_RESOURCE_ENVELOPE: i32 = -67007;
const ERR_SEC_CS_CANCELLED: i32 = -67006;
const ERR_SEC_CS_INVALID_SYMLINK: i32 = -67003;
const ERR_SEC_CS_NOT_APP_LIKE: i32 = -67002;
const ERR_SEC_CS_SIGNATURE_UNTRUSTED: i32 = -66996;
const ERR_SEC_CS_INVALID_TEAM_IDENTIFIER: i32 = -66998;
const ERR_SEC_CS_BAD_TEAM_IDENTIFIER: i32 = -66997;
const ERR_SEC_CS_REVOKED_NOTARIZATION: i32 = -66992;
const ERR_SEC_CS_UNSIGNED_NESTED_CODE: i32 = -67022;
const ERR_SEC_CS_BAD_NESTED_CODE: i32 = -67021;
const ERR_SEC_CS_STATIC_CODE_CHANGED: i32 = -67034;
const ERR_SEC_CERTIFICATE_EXPIRED: i32 = -67818;
const ERR_SEC_CERTIFICATE_REVOKED: i32 = -67820;
const ERR_SEC_NOT_TRUSTED: i32 = -67843;
const ERR_SEC_TRUST_SETTING_DENY: i32 = -67844;

/// Classify a Security.framework status into the fail-closed vocabulary.
///
/// An explicit signature/resource/requirement/trust denial (and explicit
/// revocation or expiration) is [`TrustVerdict::Deny`]. A status that means the
/// assessment could not be formed (unimplemented, invalid call, missing code,
/// broken trust database, helper/internal failure, cancellation) and every
/// status this build does not know is [`TrustVerdict::Unavailable`]: it still
/// blocks activation, but it never claims the code was rejected.
pub(super) fn classify_security_status(status: i32) -> TrustVerdict {
    match status {
        ERR_SEC_CS_UNSIGNED
        | ERR_SEC_CS_SIGNATURE_FAILED
        | ERR_SEC_CS_SIGNATURE_UNSUPPORTED
        | ERR_SEC_CS_BAD_DICTIONARY_FORMAT
        | ERR_SEC_CS_RESOURCES_NOT_SEALED
        | ERR_SEC_CS_RESOURCES_NOT_FOUND
        | ERR_SEC_CS_RESOURCES_INVALID
        | ERR_SEC_CS_BAD_RESOURCE
        | ERR_SEC_CS_RESOURCE_RULES_INVALID
        | ERR_SEC_CS_REQ_INVALID
        | ERR_SEC_CS_REQ_UNSUPPORTED
        | ERR_SEC_CS_REQ_FAILED
        | ERR_SEC_CS_BAD_OBJECT_FORMAT
        | ERR_SEC_CS_SIGNATURE_INVALID
        | ERR_SEC_CS_BAD_BUNDLE_FORMAT
        | ERR_SEC_CS_BAD_MAIN_EXECUTABLE
        | ERR_SEC_CS_UNSEALED_APP_ROOT
        | ERR_SEC_CS_UNSEALED_FRAMEWORK_ROOT
        | ERR_SEC_CS_RESOURCE_NOT_SUPPORTED
        | ERR_SEC_CS_WEAK_RESOURCE_ENVELOPE
        | ERR_SEC_CS_INVALID_SYMLINK
        | ERR_SEC_CS_NOT_APP_LIKE
        | ERR_SEC_CS_SIGNATURE_UNTRUSTED
        | ERR_SEC_CS_INVALID_TEAM_IDENTIFIER
        | ERR_SEC_CS_BAD_TEAM_IDENTIFIER
        | ERR_SEC_CS_REVOKED_NOTARIZATION
        | ERR_SEC_CS_UNSIGNED_NESTED_CODE
        | ERR_SEC_CS_BAD_NESTED_CODE
        | ERR_SEC_CS_STATIC_CODE_CHANGED
        | ERR_SEC_CERTIFICATE_EXPIRED
        | ERR_SEC_CERTIFICATE_REVOKED
        | ERR_SEC_NOT_TRUSTED
        | ERR_SEC_TRUST_SETTING_DENY => TrustVerdict::Deny,
        ERR_SEC_CS_UNIMPLEMENTED
        | ERR_SEC_CS_INVALID_OBJECT_REF
        | ERR_SEC_CS_INVALID_FLAGS
        | ERR_SEC_CS_OBJECT_REQUIRED
        | ERR_SEC_CS_STATIC_CODE_NOT_FOUND
        | ERR_SEC_CS_SIGNATURE_NOT_VERIFIABLE
        | ERR_SEC_CS_INTERNAL_ERROR
        | ERR_SEC_CS_HELPER_FAILED
        | ERR_SEC_CS_CANCELLED => TrustVerdict::Unavailable,
        _ => TrustVerdict::Unavailable,
    }
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use super::*;

    fn publishers(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_owned()).collect()
    }

    #[test]
    fn team_publisher_identity_is_exactly_the_policy_prefix_plus_a_team_id() {
        assert_eq!(team_publisher("team:ABCDE12345"), Some("ABCDE12345"));
        assert_eq!(team_publisher("team:0123456789"), Some("0123456789"));
        // Not a macOS publisher identity.
        assert_eq!(team_publisher("Publisher One"), None);
        assert_eq!(team_publisher("sha256:abcd"), None);
        assert_eq!(team_publisher("team:"), None);
        // A wrong-shaped suffix never becomes a Team ID.
        assert_eq!(team_publisher("team:ABCDE1234"), None);
        assert_eq!(team_publisher("team:ABCDE123456"), None);
        assert_eq!(team_publisher("team:abcde12345"), None);
        assert_eq!(team_publisher("team:ABCDE 2345"), None);
        assert_eq!(team_publisher("team:ABCDE1234\""), None);
        assert_eq!(team_publisher("team:ABCDE12345 and anchor apple"), None);
        assert_eq!(team_publisher("TEAM:ABCDE12345"), None);
    }

    #[test]
    fn team_id_shape_is_ten_uppercase_alphanumerics() {
        assert!(is_valid_team_id("ABCDE12345"));
        assert!(is_valid_team_id("0123456789"));
        assert!(!is_valid_team_id(""));
        assert!(!is_valid_team_id("ABCDE1234"));
        assert!(!is_valid_team_id("ABCDE123456"));
        assert!(!is_valid_team_id("abcde12345"));
        assert!(!is_valid_team_id("ABCDE-2345"));
        assert!(!is_valid_team_id("ABCDE 2345"));
        assert!(!is_valid_team_id("ABCDE1234é"));
    }

    #[test]
    fn policy_authors_only_well_formed_team_publishers() {
        let policy = publishers(&[
            "team:ABCDE12345",
            "Publisher One",
            "team:abcde12345",
            "team:FGHIJ67890",
            "team:",
        ]);
        assert_eq!(authorized_teams(&policy), vec!["ABCDE12345", "FGHIJ67890"]);
        assert!(is_authorized_team(&policy, "ABCDE12345"));
        assert!(is_authorized_team(&policy, "FGHIJ67890"));
        // Observed but not authorized, malformed, or from another policy.
        assert!(!is_authorized_team(&policy, "KLMNO13579"));
        assert!(!is_authorized_team(&policy, "abcde12345"));
        assert!(!is_authorized_team(&policy, ""));
        // A policy with no macOS publisher authorizes no Team ID at all.
        assert!(authorized_teams(&publishers(&["Publisher One"])).is_empty());
        assert!(!is_authorized_team(
            &publishers(&["Publisher One"]),
            "ABCDE12345"
        ));
    }

    #[test]
    fn certificate_publishers_are_sha256_leaf_fingerprints() {
        let digest = "e34913a76c5ac08260e4577862a3b7be6dcd9d95f52d5af9ac4f3d277cf24c26";
        assert_eq!(
            certificate_publisher(&format!("sha256:{digest}")),
            Some(digest)
        );
        for invalid in [
            "".to_owned(),
            "sha256:".to_owned(),
            format!("sha256:{}", digest.to_uppercase()),
            format!("sha256:{}", &digest[..62]),
            format!("sha256:{}0", digest),
            format!("team:{digest}"),
            format!("sha256:{digest} "),
        ] {
            assert_eq!(certificate_publisher(&invalid), None, "{invalid}");
        }
    }

    #[test]
    fn policy_authors_only_well_formed_certificate_publishers() {
        let first = "e34913a76c5ac08260e4577862a3b7be6dcd9d95f52d5af9ac4f3d277cf24c26";
        let second = "b6f771b4d8fc23af8f854415036cdafe799265fc803f49b6b844aeedf43a3ba5";
        let policy = publishers(&[
            &format!("sha256:{first}"),
            "team:ABCDE12345",
            "Publisher One",
            &format!("sha256:{}", first.to_uppercase()),
        ]);
        assert_eq!(authorized_certificates(&policy), vec![first]);
        assert!(is_authorized_certificate(&policy, first));
        assert!(!is_authorized_certificate(&policy, second));
        assert!(!is_authorized_certificate(&policy, &first.to_uppercase()));
        assert!(!is_authorized_certificate(
            &publishers(&["team:ABCDE12345"]),
            first
        ));
    }

    #[test]
    fn self_signed_requirement_is_leaf_pinned_and_never_free_form() {
        let sha1 = "efdf48f3b802d2575fb1eb14b2543d5c7d187bbb";
        assert_eq!(
            certificate_requirement(sha1).as_deref(),
            Some("certificate leaf = H\"efdf48f3b802d2575fb1eb14b2543d5c7d187bbb\"")
        );
        for invalid in ["", "efdf", &sha1.to_uppercase(), &format!("{sha1}0"), "a b"] {
            assert_eq!(certificate_requirement(invalid), None, "{invalid}");
        }
        // The requirement carries no Apple anchor: a self-signed leaf has none.
        let requirement = certificate_requirement(sha1).expect("requirement");
        assert!(!requirement.contains("anchor apple generic"));
    }

    #[test]
    fn designated_requirement_is_apple_anchored_and_team_pinned() {
        assert_eq!(
            publisher_requirement(Channel::Production, "ABCDE12345").as_deref(),
            Some(
                "anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] \
                 /* exists */ and certificate leaf[field.1.2.840.113635.100.6.1.13] \
                 /* exists */ and certificate leaf[subject.OU] = \"ABCDE12345\""
            )
        );
        assert_eq!(
            publisher_requirement(Channel::Development, "ABCDE12345").as_deref(),
            Some("anchor apple generic and certificate leaf[subject.OU] = \"ABCDE12345\"")
        );
        assert_eq!(
            publisher_requirement(Channel::Fixture, "ABCDE12345").as_deref(),
            Some("anchor apple generic and certificate leaf[subject.OU] = \"ABCDE12345\"")
        );
        // No requirement is ever built from a string that is not a Team ID, so
        // requirement syntax cannot be injected.
        for invalid in ["", "abcde12345", "ABCDE1234", "ABCDE12345 or 1"] {
            assert_eq!(publisher_requirement(Channel::Production, invalid), None);
            assert_eq!(publisher_requirement(Channel::Development, invalid), None);
        }
    }

    #[test]
    fn gatekeeper_assessment_argv_is_fixed_absolute_and_uninterpreted() {
        let carrier = Path::new("/Users/dev/with space/package/carrier.app");
        assert_eq!(SPCTL_PATH, "/usr/sbin/spctl");
        // POSIX-absolute, not `Path::is_absolute`: this test also runs on hosts
        // whose path rules are not POSIX (a `cargo test` on Windows), where
        // `/usr/sbin/spctl` has no root component and would not be absolute.
        assert!(SPCTL_PATH.starts_with('/'));
        assert!(!SPCTL_PATH.contains('\0'));
        assert_eq!(
            assessment_argv(carrier),
            vec![
                OsString::from("--assess"),
                OsString::from("--type"),
                OsString::from("exec"),
                OsString::from("--verbose"),
                carrier.as_os_str().to_owned(),
            ]
        );
        // The path stays exactly one argument.
        assert_eq!(assessment_argv(carrier).len(), 5);
        // Generous enough for a network-backed assessment, but finite: an
        // installer decision never waits forever and a timeout kills the child.
        assert!(SPCTL_TIMEOUT >= Duration::from_secs(10));
        assert!(SPCTL_TIMEOUT <= Duration::from_secs(300));
    }

    #[test]
    fn accepted_assessment_requires_acceptance_not_absence_of_denial() {
        assert_eq!(classify_assessment(Some(0), b""), SpctlOutcome::Accepted);
        assert_eq!(
            classify_assessment(Some(0), b"carrier.app: accepted\n"),
            SpctlOutcome::Accepted
        );
        // `spctl` exit 2 is the documented rejection.
        assert_eq!(
            classify_assessment(Some(2), b"carrier.app: rejected\n"),
            SpctlOutcome::Denied
        );
        // An unknown non-zero status is an assessment error unless the tool
        // itself printed its rejection verdict.
        assert_eq!(
            classify_assessment(Some(3), b"carrier.app: cannot assess\n"),
            SpctlOutcome::Unavailable
        );
        assert_eq!(
            classify_assessment(Some(1), b"carrier.app: rejected (unsigned)\n"),
            SpctlOutcome::Denied
        );
        // A spawn failure, wait failure or timeout has no status at all.
        assert_eq!(classify_assessment(None, b""), SpctlOutcome::Unavailable);
        assert_eq!(
            classify_assessment(None, b"rejected"),
            SpctlOutcome::Unavailable
        );
        // Text that merely mentions acceptance never upgrades an error.
        assert_eq!(
            classify_assessment(Some(3), b"carrier.app: accepted until it failed\n"),
            SpctlOutcome::Unavailable
        );
    }

    #[test]
    fn captured_output_is_bounded_and_fully_drained() {
        let payload = vec![b'x'; MAX_SPCTL_OUTPUT_BYTES + 128];
        let mut reader = Cursor::new(payload);
        let budget = AtomicUsize::new(MAX_SPCTL_OUTPUT_BYTES);
        let captured = drain(&mut reader, &budget);
        assert_eq!(captured.len(), MAX_SPCTL_OUTPUT_BYTES);
        assert_eq!(reader.position(), (MAX_SPCTL_OUTPUT_BYTES + 128) as u64);
        assert!(captured.iter().all(|byte| *byte == b'x'));

        let mut small = Cursor::new(b"carrier.app: accepted\n".to_vec());
        let budget = AtomicUsize::new(MAX_SPCTL_OUTPUT_BYTES);
        assert_eq!(drain(&mut small, &budget), b"carrier.app: accepted\n");
    }

    #[cfg(unix)]
    fn shell(script: &str) -> Command {
        let mut command = Command::new("/bin/sh");
        command.arg("-c").arg(script);
        command
    }

    #[cfg(unix)]
    fn sleeper() -> Command {
        let mut command = Command::new("/bin/sleep");
        command.arg("30");
        command
    }

    #[cfg(unix)]
    #[test]
    fn a_child_that_fills_either_pipe_is_drained_while_it_runs() {
        // Sixteen megabytes on one stream overflow any pipe buffer (the kernel here buffers a few megabytes before blocking a writer): a parent that only
        // reads after the child exits leaves it blocked in write(2) until the
        // deadline, and the otherwise assessable result becomes "unavailable".
        for (stream, script) in [
            ("stdout", "head -c 16777216 /dev/zero"),
            ("stderr", "head -c 16777216 /dev/zero >&2"),
        ] {
            let started = Instant::now();
            let captured = run_bounded(
                &mut shell(script),
                Duration::from_secs(5),
                MAX_SPCTL_OUTPUT_BYTES,
            )
            .expect("spawns");
            assert!(
                started.elapsed() < Duration::from_secs(4),
                "{stream}: the child stalled on a full pipe until the deadline"
            );
            let status = captured.status.expect("the child exited on its own");
            assert_eq!(status.code(), Some(0), "{stream}");
            assert_eq!(
                captured.output.len(),
                MAX_SPCTL_OUTPUT_BYTES,
                "{stream}: capture stops at the budget"
            );
        }
    }

    #[cfg(unix)]
    #[test]
    fn both_streams_share_one_capture_budget() {
        // Each stream stays below any pipe buffer, so nothing can stall: the only
        // thing under test is that the budget covers stdout and stderr together.
        let captured = run_bounded(
            &mut shell("head -c 60000 /dev/zero; head -c 60000 /dev/zero >&2"),
            Duration::from_secs(5),
            MAX_SPCTL_OUTPUT_BYTES,
        )
        .expect("spawns");
        assert_eq!(captured.status.expect("exited").code(), Some(0));
        assert_eq!(
            captured.output.len(),
            MAX_SPCTL_OUTPUT_BYTES,
            "captured {} bytes against a {MAX_SPCTL_OUTPUT_BYTES} byte budget for both streams",
            captured.output.len()
        );
    }

    #[cfg(unix)]
    #[test]
    fn the_deadline_is_end_to_end_and_kills_the_child() {
        let started = Instant::now();
        let captured = run_bounded(
            &mut sleeper(),
            Duration::from_millis(300),
            MAX_SPCTL_OUTPUT_BYTES,
        )
        .expect("spawns");
        assert!(captured.status.is_none(), "a killed child has no verdict");
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "the run outlived its deadline: {:?}",
            started.elapsed()
        );
    }

    #[test]
    fn gatekeeper_limits_match_the_plan() {
        assert_eq!(SPCTL_TIMEOUT, Duration::from_secs(30));
        assert_eq!(MAX_SPCTL_OUTPUT_BYTES, 64 * 1024);
    }

    #[test]
    fn signature_and_policy_denials_are_denied() {
        // Independent literals (not the module constants) so a renumbered or
        // misplaced constant is caught rather than asserted against itself.
        let denied: &[i32] = &[
            -67062, // errSecCSUnsigned
            -67061, // errSecCSSignatureFailed
            -67059, // errSecCSSignatureUnsupported
            -67058, // errSecCSBadDictionaryFormat
            -67057, // errSecCSResourcesNotSealed
            -67056, // errSecCSResourcesNotFound
            -67055, // errSecCSResourcesInvalid
            -67054, // errSecCSBadResource
            -67053, // errSecCSResourceRulesInvalid
            -67052, // errSecCSReqInvalid
            -67051, // errSecCSReqUnsupported
            -67050, // errSecCSReqFailed
            -67049, // errSecCSBadObjectFormat
            -67045, // errSecCSSignatureInvalid
            -67028, // errSecCSBadBundleFormat
            -67010, // errSecCSBadMainExecutable
            -67014, // errSecCSUnsealedAppRoot
            -67008, // errSecCSUnsealedFrameworkRoot
            -67016, // errSecCSResourceNotSupported
            -67007, // errSecCSWeakResourceEnvelope
            -67003, // errSecCSInvalidSymlink
            -67002, // errSecCSNotAppLike
            -66996, // errSecCSSignatureUntrusted
            -66998, // errSecCSInvalidTeamIdentifier
            -66997, // errSecCSBadTeamIdentifier
            -66992, // errSecCSRevokedNotarization
            -67022, // errSecCSUnsignedNestedCode
            -67021, // errSecCSBadNestedCode
            -67034, // errSecCSStaticCodeChanged
            -67818, // errSecCertificateExpired
            -67820, // errSecCertificateRevoked
            -67843, // errSecNotTrusted
            -67844, // errSecTrustSettingDeny
        ];
        for status in denied {
            assert_eq!(
                classify_security_status(*status),
                TrustVerdict::Deny,
                "status {status} must be an explicit denial"
            );
        }
    }

    #[test]
    fn unformed_assessments_and_unknown_statuses_are_unavailable() {
        let unavailable: &[i32] = &[
            -67072, // errSecCSUnimplemented
            -67071, // errSecCSInvalidObjectRef
            -67070, // errSecCSInvalidFlags
            -67069, // errSecCSObjectRequired
            -67068, // errSecCSStaticCodeNotFound
            -67060, // errSecCSSignatureNotVerifiable
            -67048, // errSecCSInternalError
            -67019, // errSecCSHelperFailed
            -67006, // errSecCSCancelled
            -1,     // generic failure
            i32::MIN,
            i32::MAX,
        ];
        for status in unavailable {
            assert_eq!(
                classify_security_status(*status),
                TrustVerdict::Unavailable,
                "status {status} must not be guessed as a denial"
            );
        }
    }
}
