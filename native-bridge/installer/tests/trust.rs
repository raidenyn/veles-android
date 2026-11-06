//! Portable trust-decision contract tests for the OTP-25 installer (Task 4).
//!
//! These tests exercise the *decision layer* with injected OS evidence: an
//! injected [`TrustProof`] stands in for what a native adapter reports after
//! WinVerifyTrust (Windows) or Security.framework (macOS). No Windows or macOS
//! trust API runs here, so a passing test is portable policy evidence only and
//! is never represented as native signing, notarization or acceptance proof.
//!
//! The package layout exercised by these tests is the engine package contract:
//!
//! ```text
//! <package>/inventory.json        strict inventory document; SHA-256 == payload_id
//! <package>/payload/<paths>       exact inventoried payload files (closed tree)
//! <package>/carrier.exe | .app/   platform-signed policy carrier (adapter-internal)
//! ```

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicU64, Ordering};

use veles_bridge_installer::{
    Channel, Digest, FailureCode, FakePlatform, Faults, InstallPaths, InventoryEntry, Operation,
    PRODUCTION_HOST_NAME, PlatformCall, PortablePlatform, ReleaseDescriptor, ReleasePolicy,
    TRUST_ASSESSMENT_MACOS_GATEKEEPER, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, Target, TrustProof,
    assessment_target, authenticate_package, authorize_package_policy, canonical_json,
    is_accepted_assessment, write_carrier, write_inventory,
};

const PROD_EXTENSION_ID: &str = "mnopabcdefghijklmnopabcdefghijkl";
const PUBLISHER: &str = "Publisher One";
const OTHER_PUBLISHER: &str = "Publisher Two";

fn digest(byte: u8) -> Digest {
    Digest::from_bytes([byte; 32])
}

fn baseline_descriptor(payload_id: Digest) -> ReleaseDescriptor {
    ReleaseDescriptor {
        schema: 1,
        channel: Channel::Production,
        version: "1.2.3".to_owned(),
        source_commit: "0123456789abcdef0123456789abcdef01234567".to_owned(),
        target: Target::WindowsX64,
        minimum_os: "11".to_owned(),
        extension_id: PROD_EXTENSION_ID.to_owned(),
        host_name: PRODUCTION_HOST_NAME.to_owned(),
        bundle_id: PRODUCTION_HOST_NAME.to_owned(),
        api_min: 1,
        api_max: 2,
        payload_id,
        policy: ReleasePolicy {
            publishers: vec![PUBLISHER.to_owned()],
            denied_payloads: Vec::new(),
            rollback_payloads: Vec::new(),
            epoch: 4,
        },
    }
}

/// A complete engine package plus the descriptor/carrier its signature covers.
struct Package {
    root: PathBuf,
    descriptor: ReleaseDescriptor,
    carrier: Vec<u8>,
}

impl Package {
    fn build(root: &Path, mutate: impl FnOnce(&mut ReleaseDescriptor)) -> Self {
        let payload = root.join("payload");
        fs::create_dir_all(&payload).expect("payload root");
        let files: &[(&str, &[u8], bool)] = &[
            ("host.exe", b"host payload", true),
            ("licenses/NOTICE", b"notice text", false),
        ];
        let mut entries = Vec::new();
        for (relative, content, executable) in files {
            let path = payload.join(relative);
            fs::create_dir_all(path.parent().expect("payload file parent"))
                .expect("payload directory");
            fs::write(&path, content).expect("payload file");
            entries.push(InventoryEntry {
                path: (*relative).to_owned(),
                size: content.len() as u64,
                sha256: Digest::of(content),
                executable: *executable,
            });
        }
        let inventory = write_inventory(&entries).expect("canonical inventory document");

        let mut descriptor = baseline_descriptor(Digest::of(&inventory));
        mutate(&mut descriptor);
        descriptor
            .validate()
            .expect("the fixture descriptor is a valid release identity");
        fs::write(root.join("inventory.json"), &inventory).expect("inventory file");
        let carrier =
            write_carrier(&canonical_json(&descriptor).expect("descriptor JSON")).expect("carrier");

        Self {
            root: root.to_path_buf(),
            descriptor,
            carrier,
        }
    }
}

fn proof(signer: &str, assessment: &str, carrier: &[u8]) -> TrustProof {
    TrustProof::new(signer, assessment, carrier.to_vec()).expect("fixture proof")
}

fn platform_with(trust: Option<TrustProof>) -> FakePlatform {
    FakePlatform::with_faults(Faults {
        trust,
        ..Faults::default()
    })
}

/// Authentication is read-only: no mutation and no registration call may occur.
fn assert_no_registration_writes(platform: &FakePlatform) {
    assert!(
        platform.mutations().is_empty(),
        "authentication attempted a state mutation: {:?}",
        platform.mutations()
    );
    for call in platform.calls() {
        assert!(
            !matches!(
                call,
                PlatformCall::PublishManifest
                    | PlatformCall::PublishRegistration
                    | PlatformCall::RemoveRegistration
                    | PlatformCall::ReplaceManifest
                    | PlatformCall::ReadRegistration
            ),
            "authentication touched the registration boundary: {call:?}"
        );
    }
}

fn registration_snapshot(paths: &InstallPaths) -> RegistrationSnapshot {
    RegistrationSnapshot {
        root: snapshot_tree(paths.root()),
        registration: fs::read(paths.registration_path()).ok(),
    }
}

#[derive(Debug, PartialEq, Eq)]
struct RegistrationSnapshot {
    root: BTreeMap<PathBuf, Vec<u8>>,
    registration: Option<Vec<u8>>,
}

fn snapshot_tree(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
    let mut snapshot = BTreeMap::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(directory) = stack.pop() {
        let Ok(entries) = fs::read_dir(&directory) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let Ok(metadata) = fs::symlink_metadata(&path) else {
                continue;
            };
            if metadata.is_dir() {
                stack.push(path);
            } else {
                let relative = path
                    .strip_prefix(root)
                    .map(Path::to_path_buf)
                    .unwrap_or_else(|_| path.clone());
                snapshot.insert(relative, fs::read(&path).unwrap_or_default());
            }
        }
    }
    snapshot
}

/// Publish a synthetic *prior* installation into the injected per-user layout.
///
/// The file is written directly so this fixture does not depend on the portable
/// adapter's registration primitives (which are deliberately platform stubs).
fn seed_prior_installation(home: &Path, local: &Path) -> InstallPaths {
    let paths = InstallPaths::for_user(Target::WindowsX64, Channel::Production, home, Some(local))
        .expect("injected per-user layout");
    let executable = paths
        .versions()
        .join("1.0.0-1111111111111111111111111111111111111111111111111111111111111111")
        .join("host.exe");
    fs::create_dir_all(executable.parent().expect("prior version directory"))
        .expect("prior version directory");
    fs::write(&executable, b"prior host").expect("prior host");
    let manifest = serde_json::json!({
        "name": paths.registration_name(),
        "description": "prior installation",
        "path": executable.to_str().expect("UTF-8 fixture path"),
        "type": "stdio",
        "allowed_origins": [
            format!("chrome-extension://{PROD_EXTENSION_ID}/"),
        ],
    });
    fs::create_dir_all(
        paths
            .registration_path()
            .parent()
            .expect("registration parent"),
    )
    .expect("registration parent");
    fs::write(
        paths.registration_path(),
        serde_json::to_vec(&manifest).expect("manifest JSON"),
    )
    .expect("prior registration");
    paths
}

#[test]
fn valid_signature_wrong_publisher_denied() {
    let temp = TempRoot::new("wrong-publisher");
    let package = Package::build(&temp.path.join("package"), |_| {});

    let wrong = platform_with(Some(proof(
        OTHER_PUBLISHER,
        TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
        &package.carrier,
    )));
    let failure = authenticate_package(&wrong, &package.root, &package.descriptor)
        .expect_err("a valid signature by an untrusted publisher must be denied");
    assert_eq!(failure.code, FailureCode::TrustDenied);
    assert_no_registration_writes(&wrong);
    assert_eq!(wrong.calls(), vec![PlatformCall::VerifyTrust]);

    // Positive control: the same package authenticates when the OS-verified
    // signer is in the authenticated descriptor's publisher policy.
    let trusted = platform_with(Some(proof(
        PUBLISHER,
        TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
        &package.carrier,
    )));
    let authenticated = authenticate_package(&trusted, &package.root, &package.descriptor)
        .expect("a trusted publisher authenticates");
    assert_eq!(authenticated.descriptor(), &package.descriptor);
    assert_eq!(authenticated.signer(), PUBLISHER);
    assert_eq!(
        authenticated.assessment(),
        TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE
    );
    assert_eq!(
        authenticated.package().path(),
        fs::canonicalize(&package.root).expect("canonical package root")
    );
    assert_eq!(authenticated.payload().len(), 2);
    for source in authenticated.payload() {
        source
            .revalidate()
            .expect("the authenticated payload is still the exact bytes");
    }
    assert_no_registration_writes(&trusted);
}

#[test]
fn offline_required_assessment_unavailable_preserves_prior() {
    let temp = TempRoot::new("unavailable");
    let package = Package::build(&temp.path.join("package"), |_| {});
    let paths = seed_prior_installation(&temp.path.join("home"), &temp.path.join("local"));
    let before = registration_snapshot(&paths);

    // `Faults::trust == None` is exactly "the required OS assessment could not be
    // formed": there is no evidence, so no trust can be claimed.
    let unavailable = FakePlatform::new();
    let failure = authenticate_package(&unavailable, &package.root, &package.descriptor)
        .expect_err("an unavailable required assessment must block activation");
    assert_eq!(failure.code, FailureCode::TrustUnavailable);
    assert_no_registration_writes(&unavailable);
    assert_eq!(unavailable.calls(), vec![PlatformCall::VerifyTrust]);

    // The previous installation is byte-for-byte untouched, and the failure does
    // not claim a fresh security assessment of it.
    assert_eq!(registration_snapshot(&paths), before);
    assert!(
        !failure.prior_usable,
        "this layer cannot know a prior install exists, so it never claims it"
    );

    // The portable adapter never fabricates OS trust either.
    let failure =
        authenticate_package(&PortablePlatform::new(), &package.root, &package.descriptor)
            .expect_err("the portable adapter must fail closed");
    assert_eq!(failure.code, FailureCode::TrustUnavailable);
    assert_eq!(registration_snapshot(&paths), before);
}

#[test]
fn explicit_revocation_and_malformed_carrier_rejected() {
    let temp = TempRoot::new("revocation");
    let package = Package::build(&temp.path.join("package"), |_| {});

    // An adapter that observed an explicit OS denial/revocation reports it as an
    // error; it must never be downgraded to a success with a warning.
    let revoked = FakePlatform::with_faults(Faults {
        trust_denied: true,
        ..Faults::default()
    });
    let failure = authenticate_package(&revoked, &package.root, &package.descriptor)
        .expect_err("an explicit OS revocation blocks activation");
    assert_eq!(failure.code, FailureCode::TrustDenied);
    assert_no_registration_writes(&revoked);

    // Scope of this case: an adapter that (wrongly) reported an *accepted*
    // assessment over bytes that are not a policy carrier at all. That tests the
    // decision layer's parsing, not the rejection of a well-formed unsigned
    // carrier: the OS refusing a real unsigned `carrier.exe` / `carrier.app` is
    // asserted by the native tests in `tests/native_windows.rs` and
    // `tests/native_macos.rs` (NOT RUN on this host).
    let malformed = platform_with(Some(proof(
        PUBLISHER,
        TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
        b"not a veles policy carrier",
    )));
    let failure = authenticate_package(&malformed, &package.root, &package.descriptor)
        .expect_err("bytes that are not a policy carrier are denied");
    assert_eq!(failure.code, FailureCode::TrustDenied);
    assert_no_registration_writes(&malformed);

    // A proof without carrier bytes is not constructible in the first place.
    assert_eq!(
        TrustProof::new(PUBLISHER, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, Vec::new())
            .expect_err("empty carrier")
            .code,
        FailureCode::InvalidInput
    );

    // A signed carrier for a *different* release than the caller authorized is a
    // trust denial, not a silent acceptance of the embedded descriptor.
    let other = Package::build(&temp.path.join("other-package"), |descriptor| {
        descriptor.version = "1.2.4".to_owned();
    });
    let mismatched = platform_with(Some(proof(
        PUBLISHER,
        TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
        &other.carrier,
    )));
    let failure = authenticate_package(&mismatched, &package.root, &package.descriptor)
        .expect_err("a carrier for another release is denied");
    assert_eq!(failure.code, FailureCode::TrustDenied);
    assert_no_registration_writes(&mismatched);

    // A verdict outside the native assessment vocabulary fails closed.
    let unaccepted = platform_with(Some(proof(
        PUBLISHER,
        "windows-authenticode-revoked",
        &package.carrier,
    )));
    let failure = authenticate_package(&unaccepted, &package.root, &package.descriptor)
        .expect_err("an unaccepted assessment verdict is denied");
    assert_eq!(failure.code, FailureCode::TrustDenied);
    assert_no_registration_writes(&unaccepted);

    // The Windows verdict authenticates this Windows release. (The same proof
    // with the macOS verdict is denied: see
    // `assessment_verdict_is_bound_to_descriptor_target`.)
    let accepted = platform_with(Some(proof(
        PUBLISHER,
        TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
        &package.carrier,
    )));
    authenticate_package(&accepted, &package.root, &package.descriptor)
        .expect("an accepted native assessment authenticates");
    assert_no_registration_writes(&accepted);
}

#[test]
fn assessment_verdict_is_bound_to_descriptor_target() {
    let temp = TempRoot::new("assessment-target");
    let windows = Package::build(&temp.path.join("windows-package"), |_| {});
    let macos = Package::build(&temp.path.join("macos-package"), |descriptor| {
        descriptor.target = Target::MacosArm64;
        descriptor.minimum_os = "26.0".to_owned();
    });

    // Correct pairings authenticate.
    for (package, assessment) in [
        (&windows, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE),
        (&macos, TRUST_ASSESSMENT_MACOS_GATEKEEPER),
    ] {
        let platform = platform_with(Some(proof(PUBLISHER, assessment, &package.carrier)));
        authenticate_package(&platform, &package.root, &package.descriptor)
            .expect("a verdict for its own release target authenticates");
        assert_no_registration_writes(&platform);
    }

    // A verdict from the other platform is never evidence for this release, even
    // with a correctly signed carrier for exactly this descriptor and an
    // authorized publisher. The decision layer binds the verdict itself.
    for (package, assessment) in [
        (&windows, TRUST_ASSESSMENT_MACOS_GATEKEEPER),
        (&macos, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE),
    ] {
        let platform = platform_with(Some(proof(PUBLISHER, assessment, &package.carrier)));
        assert_eq!(
            authenticate_package(&platform, &package.root, &package.descriptor)
                .expect_err("a verdict for the other platform is not evidence")
                .code,
            FailureCode::TrustDenied
        );
        assert_no_registration_writes(&platform);
    }

    // The binding is part of the exported, closed vocabulary.
    assert_eq!(
        assessment_target(TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE),
        Some(Target::WindowsX64)
    );
    assert_eq!(
        assessment_target(TRUST_ASSESSMENT_MACOS_GATEKEEPER),
        Some(Target::MacosArm64)
    );
    assert_eq!(assessment_target("windows-authenticode-revoked"), None);
    assert_eq!(assessment_target(""), None);
    assert!(is_accepted_assessment(
        TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
        Target::WindowsX64
    ));
    assert!(is_accepted_assessment(
        TRUST_ASSESSMENT_MACOS_GATEKEEPER,
        Target::MacosArm64
    ));
    assert!(!is_accepted_assessment(
        TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
        Target::MacosArm64
    ));
    assert!(!is_accepted_assessment(
        TRUST_ASSESSMENT_MACOS_GATEKEEPER,
        Target::WindowsX64
    ));
    assert!(!is_accepted_assessment("", Target::WindowsX64));
}

#[test]
fn stale_signed_policy_cannot_relax_repair() {
    let temp = TempRoot::new("stale-policy");
    let package = Package::build(&temp.path.join("package"), |descriptor| {
        descriptor.policy.epoch = 4;
    });
    let platform = platform_with(Some(proof(
        PUBLISHER,
        TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
        &package.carrier,
    )));
    let authenticated = authenticate_package(&platform, &package.root, &package.descriptor)
        .expect("the signer and payload are authentic; staleness is a policy decision");

    // The live installation has already recorded a denial and moved to epoch 5.
    let live = ReleasePolicy {
        publishers: vec![PUBLISHER.to_owned()],
        denied_payloads: vec![digest(0x22)],
        rollback_payloads: Vec::new(),
        epoch: 5,
    };

    // A correctly signed older package must not be allowed to erase that denial.
    assert_eq!(
        authorize_package_policy(&authenticated, &live, Operation::Repair)
            .expect_err("a stale signed policy cannot relax repair")
            .code,
        FailureCode::PolicyDenied
    );
    assert_eq!(
        authorize_package_policy(&authenticated, &live, Operation::Install)
            .expect_err("an older installer cannot apply a stale policy")
            .code,
        FailureCode::PolicyDenied
    );

    // Inspect does not apply new policy and keeps the committed live policy.
    assert_eq!(
        authorize_package_policy(&authenticated, &live, Operation::Inspect)
            .expect("inspect applies no new policy"),
        live
    );

    // Positive control: an equal-epoch repair composition (no relaxation) works.
    let same_epoch = authenticated.descriptor().policy.clone();
    assert_eq!(
        authorize_package_policy(&authenticated, &same_epoch, Operation::Repair)
            .expect("an equal-epoch repair policy is acceptable"),
        same_epoch
    );

    // The policy source is the authenticated package, never a caller argument:
    // trust succeeded but no installer state was mutated while composing it.
    assert_no_registration_writes(&platform);
}

#[test]
fn tampered_payload_and_uninventoried_files_are_denied() {
    let temp = TempRoot::new("payload");
    let package = Package::build(&temp.path.join("package"), |_| {});
    let trusted_proof = || {
        proof(
            PUBLISHER,
            TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
            &package.carrier,
        )
    };

    // The authenticated inventory pins exact bytes: a same-size edit is denied.
    let host = package.root.join("payload").join("host.exe");
    fs::write(&host, b"HOST payload").expect("tampered payload");
    let platform = platform_with(Some(trusted_proof()));
    assert_eq!(
        authenticate_package(&platform, &package.root, &package.descriptor)
            .expect_err("tampered payload")
            .code,
        FailureCode::TrustDenied
    );
    assert_no_registration_writes(&platform);

    // An uninventoried file next to the payload (the Windows sideload shape) is
    // not authenticated, so the closed tree rejects it even though every declared
    // file still matches.
    fs::write(&host, b"host payload").expect("restored payload");
    fs::write(
        package.root.join("payload").join("planted.dll"),
        b"never inventoried",
    )
    .expect("planted payload");
    let platform = platform_with(Some(trusted_proof()));
    assert_eq!(
        authenticate_package(&platform, &package.root, &package.descriptor)
            .expect_err("uninventoried payload")
            .code,
        FailureCode::TrustDenied
    );
    assert_no_registration_writes(&platform);
    fs::remove_file(package.root.join("payload").join("planted.dll")).expect("remove planted");

    // The inventory document bytes are authenticated through payload_id; any
    // edit (even still-valid JSON) is denied.
    let mut inventory = fs::read(package.root.join("inventory.json")).expect("inventory bytes");
    inventory.push(b' ');
    fs::write(package.root.join("inventory.json"), inventory).expect("edited inventory");
    let platform = platform_with(Some(trusted_proof()));
    assert_eq!(
        authenticate_package(&platform, &package.root, &package.descriptor)
            .expect_err("edited inventory")
            .code,
        FailureCode::TrustDenied
    );
    assert_no_registration_writes(&platform);

    // A file is not an extracted engine package.
    let file = temp.path.join("package-file");
    fs::write(&file, b"not a package").expect("package file");
    let platform = platform_with(Some(trusted_proof()));
    assert_eq!(
        authenticate_package(&platform, &file, &package.descriptor)
            .expect_err("file package")
            .code,
        FailureCode::InvalidPackage
    );
    assert_no_registration_writes(&platform);
}

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

struct TempRoot {
    path: PathBuf,
}

impl TempRoot {
    fn new(name: &str) -> Self {
        let unique = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "veles-installer-task4-{name}-{}-{unique}",
            process::id()
        ));
        if path.exists() {
            fs::remove_dir_all(&path).expect("stale temp root");
        }
        fs::create_dir_all(&path).expect("temp root");
        Self { path }
    }
}

impl Drop for TempRoot {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}
