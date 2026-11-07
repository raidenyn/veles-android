//! The build-time release-identity seam (OTP-25 F1).
//!
//! Release packaging supplies the authenticated carrier document through
//! `VELES_EMBEDDED_CARRIER`; the build embeds its exact bytes and the engine
//! parses the descriptor out of them. An unreleased build (channel unset or
//! empty) embeds nothing and must refuse install/repair honestly. These tests
//! pin the producer-to-executable seam and the target dispatch that the Linux
//! development host can exercise; the macOS account-database lookup is
//! cfg-gated and NOT RUN here.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};

use veles_bridge_installer::{
    Channel, Digest, FailureCode, InstallPaths, ReleaseDescriptor, ReleasePolicy, Target,
    canonical_json, write_carrier,
};

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct Temp(PathBuf);

impl Temp {
    fn new(tag: &str) -> Self {
        let unique = COUNTER.fetch_add(1, Ordering::Relaxed);
        // The workspace target directory, not the system temp directory: the
        // build probe below needs a durable parent that survives the nested
        // cargo's own target lock, and this host's `/tmp` is a small tmpfs.
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join("f1-tmp")
            .join(format!("{tag}-{}-{unique}", std::process::id()));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).expect("temp dir");
        Self(path)
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

const EXTENSION_ID: &str = "mnopabcdefghijklmnopabcdefghijkl";

fn descriptor() -> ReleaseDescriptor {
    descriptor_for(Channel::Production)
}

fn descriptor_for(channel: Channel) -> ReleaseDescriptor {
    let payload_id = Digest::of(b"the fixture payload inventory");
    let identity = channel.identity();
    let descriptor = ReleaseDescriptor {
        schema: 1,
        channel,
        version: "1.0.0".to_owned(),
        source_commit: "0123456789abcdef0123456789abcdef01234567".to_owned(),
        target: Target::WindowsX64,
        minimum_os: "11".to_owned(),
        extension_id: EXTENSION_ID.to_owned(),
        host_name: identity.host_name.to_owned(),
        bundle_id: identity.bundle_id.to_owned(),
        api_min: 1,
        api_max: 1,
        payload_id,
        policy: ReleasePolicy {
            publishers: vec!["Publisher One".to_owned()],
            denied_payloads: Vec::new(),
            rollback_payloads: Vec::new(),
            epoch: 1,
        },
    };
    descriptor.validate().expect("fixture descriptor");
    descriptor
}

fn carrier() -> Vec<u8> {
    write_carrier(&canonical_json(&descriptor()).expect("descriptor JSON")).expect("carrier")
}

fn carrier_for(channel: Channel) -> Vec<u8> {
    write_carrier(&canonical_json(&descriptor_for(channel)).expect("descriptor JSON"))
        .expect("carrier")
}

fn contains(haystack: &[u8], needle: &[u8]) -> bool {
    haystack
        .windows(needle.len())
        .any(|window| window == needle)
}

/// The producer-to-executable seam: the build-time channel must put the exact
/// authenticated carrier bytes into the shipped executable, and an unset channel
/// must put nothing there. This is the check the whole-branch review found
/// missing: a resource writer's golden vector never proved the engine consumed
/// its own release identity.
#[test]
fn the_build_time_channel_embeds_the_carrier_in_the_executable() {
    let temp = Temp::new("embed");
    let carrier_path = temp.path().join("carrier.bin");
    let carrier_bytes = carrier();
    fs::write(&carrier_path, &carrier_bytes).expect("carrier document");

    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let target_dir = manifest.join("target").join("embedded-carrier-probe");
    let built = build_binary(&target_dir, Some(&carrier_path));
    let embedded = fs::read(&built).expect("built executable");
    assert!(
        contains(&embedded, &carrier_bytes),
        "the build-time channel must embed the exact authenticated carrier bytes"
    );

    // The same build without the channel: an unreleased build embeds nothing, so
    // no release identity can be smuggled in at runtime.
    let built = build_binary(&target_dir, None);
    let plain = fs::read(&built).expect("rebuilt executable");
    assert!(
        !contains(&plain, &carrier_bytes),
        "an unset build-time channel must embed no carrier"
    );
}

fn build_binary(target_dir: &Path, carrier: Option<&Path>) -> PathBuf {
    build_named_binary(target_dir, "veles-bridge-maintenance", carrier)
}

fn build_named_binary(target_dir: &Path, binary: &str, carrier: Option<&Path>) -> PathBuf {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut command = Command::new(env!("CARGO"));
    command
        .current_dir(manifest)
        .args(["build", "--locked", "--bin", binary, "--target-dir"])
        .arg(target_dir)
        .env_remove("VELES_EMBEDDED_CARRIER")
        .env_remove("VELES_BUILD_CHANNEL");
    if let Some(carrier) = carrier {
        command.env("VELES_EMBEDDED_CARRIER", carrier);
    }
    let output = command.output().expect("cargo runs");
    assert!(
        output.status.success(),
        "cargo build failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    build_binary_path(target_dir, binary)
}

/// The nested Cargo output name of one binary: the platform's executable suffix
/// belongs to the file name (`veles-bridge-maintenance.exe` on Windows), and a
/// read-back that omits it fails with NotFound even though it compiled (N5).
fn executable_name(binary: &str, suffix: &str) -> String {
    format!("{binary}{suffix}")
}

/// The exact nested Cargo output path for the host platform.
fn build_binary_path(target_dir: &Path, binary: &str) -> PathBuf {
    target_dir
        .join("debug")
        .join(executable_name(binary, std::env::consts::EXE_SUFFIX))
}

/// The read-back path must carry the platform executable suffix. The Windows
/// branch is exercised here by constructing the name with `.exe`, which is what
/// the nested Cargo build produces on that host; omitting it makes both read-back
/// tests fail with NotFound despite compiling. The real Windows run is NOT RUN on
/// this Linux host.
#[test]
fn the_nested_build_read_back_uses_the_platform_executable_suffix() {
    assert_eq!(
        executable_name("veles-bridge-maintenance", ".exe"),
        "veles-bridge-maintenance.exe"
    );
    assert_eq!(
        executable_name("veles-bridge-cleanup", ".exe"),
        "veles-bridge-cleanup.exe"
    );
    assert_eq!(
        executable_name("veles-bridge-maintenance", ""),
        "veles-bridge-maintenance"
    );
    // The shipping helper appends the compile-time suffix, so Windows resolves
    // `debug/<binary>.exe` exactly as the nested build wrote it.
    assert_eq!(
        build_binary_path(Path::new("probe-target"), "veles-bridge-cleanup"),
        Path::new("probe-target")
            .join("debug")
            .join(executable_name(
                "veles-bridge-cleanup",
                std::env::consts::EXE_SUFFIX
            ))
    );
    for suffix in [".exe", ""] {
        let name = executable_name("veles-bridge-cleanup", suffix);
        assert_eq!(
            name.strip_prefix("veles-bridge-cleanup"),
            Some(suffix),
            "the suffix is appended verbatim for {suffix:?}"
        );
    }
}

/// The cleanup helper is part of the inventoried payload, and the carrier is only
/// compiled into the maintenance engine. Rebuilding the engine with the carrier must
/// therefore leave the payload identity (and thus the carrier's descriptor) unchanged,
/// which is what makes the assemble -> rebuild -> re-assemble order sound.
#[test]
fn the_carrier_is_not_embedded_in_the_payload_cleanup_helper() {
    let temp = Temp::new("cleanup-payload");
    let carrier_path = temp.path().join("carrier.bin");
    let carrier_bytes = carrier();
    fs::write(&carrier_path, &carrier_bytes).expect("carrier document");

    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let target_dir = manifest
        .join("target")
        .join("embedded-carrier-cleanup-probe");
    let built = build_named_binary(&target_dir, "veles-bridge-cleanup", Some(&carrier_path));
    let helper = fs::read(&built).expect("built cleanup helper");
    assert!(
        !contains(&helper, &carrier_bytes),
        "the cleanup helper is payload, not the release identity carrier"
    );
}

/// The library seam: a carrier supplied through the channel is parsed into the
/// release descriptor the engine authenticates packages against; nothing and
/// empty bytes are honestly "this build embeds no descriptor"; malformed bytes
/// are a policy refusal, never a silent absence.
#[test]
fn a_supplied_carrier_is_parsed_and_an_empty_channel_refuses_honestly() {
    use veles_bridge_installer::embedded::decode_embedded_carrier;

    assert_eq!(
        decode_embedded_carrier(None).expect("absent channel is not a failure"),
        None
    );
    assert_eq!(
        decode_embedded_carrier(Some(&[])).expect("empty channel is not a failure"),
        None
    );
    let parsed = decode_embedded_carrier(Some(&carrier()))
        .expect("the carrier parses")
        .expect("a descriptor is present");
    assert_eq!(
        parsed,
        descriptor(),
        "the parsed identity is the supplied one"
    );

    for malformed in [
        b"not a carrier".as_slice(),
        // A correctly framed header with a truncated descriptor section.
        &carrier()[..40],
        // A carrier whose descriptor section is not JSON.
        &{
            let mut bytes = carrier();
            let length = bytes.len();
            bytes[length - 1] ^= 0xff;
            bytes
        }[..],
    ] {
        assert_eq!(
            decode_embedded_carrier(Some(malformed))
                .expect_err("a malformed embedded carrier is refused")
                .code,
            FailureCode::PolicyDenied
        );
    }
}

/// The engine consults its embedded descriptor: without one, an install is
/// `policy-denied`; with one, the same invocation proceeds past authentication.
#[test]
fn the_engine_uses_the_descriptor_it_parsed_from_the_channel() {
    use veles_bridge_installer::cli::{Context, Invocation, execute};
    use veles_bridge_installer::embedded::decode_embedded_carrier;
    use veles_bridge_installer::{FakePlatform, Operation};

    let temp = Temp::new("use");
    let home = temp.path().join("User");
    let local = temp.path().join("Local AppData");
    let paths =
        InstallPaths::for_user(Target::WindowsX64, Channel::Production, &home, Some(&local))
            .expect("injected roots");
    let package = temp.path().join("package");
    fs::create_dir_all(&package).expect("package dir");

    let invocation = Invocation {
        operation: Operation::Install,
        package: Some(package),
    };
    let platform = FakePlatform::new();

    // An unreleased build: no embedded identity, so there is nothing to
    // authenticate a package against.
    let absent = Context {
        platform: &platform,
        paths: &paths,
        expected: None,
        quiescence: None,
    };
    let report = execute(&absent, &invocation);
    assert_eq!(report.failure, Some(FailureCode::PolicyDenied));

    // The same invocation with the parsed embedded descriptor reaches package
    // authentication instead: an empty directory is not a valid package.
    let descriptor = decode_embedded_carrier(Some(&carrier()))
        .expect("parses")
        .expect("present");
    let embedded = Context {
        platform: &platform,
        paths: &paths,
        expected: Some(&descriptor),
        quiescence: None,
    };
    let report = execute(&embedded, &invocation);
    assert_ne!(
        report.failure,
        Some(FailureCode::PolicyDenied),
        "an embedded descriptor is used, not ignored: {:?}",
        report.failure
    );
}

/// The shipped binary must select its platform adapter by build target, not
/// fall back to the portable adapter for every non-Windows host.
#[test]
fn the_platform_dispatch_selects_by_target() {
    use veles_bridge_installer::host::{HostOs, PlatformKind, host_platform, platform_kind_for};

    assert_eq!(platform_kind_for(HostOs::Windows), PlatformKind::Windows);
    assert_eq!(platform_kind_for(HostOs::Macos), PlatformKind::Macos);
    assert_eq!(platform_kind_for(HostOs::Other), PlatformKind::Portable);
    assert_eq!(
        host_platform().kind(),
        platform_kind_for(HostOs::CURRENT),
        "the host adapter is the one its target selects"
    );
    assert_eq!(
        HostOs::CURRENT,
        if cfg!(windows) {
            HostOs::Windows
        } else if cfg!(target_os = "macos") {
            HostOs::Macos
        } else {
            HostOs::Other
        }
    );
    #[cfg(not(any(windows, target_os = "macos")))]
    assert_eq!(host_platform().kind(), PlatformKind::Portable);
}

/// macOS resolves its fixed layout from the account database, not from the
/// environment. NOT RUN on this Linux host: no macOS target is executable here.
#[cfg(target_os = "macos")]
#[test]
fn macos_resolves_its_layout_from_the_os_account_database() {
    let paths = veles_bridge_installer::cli::resolve_user_paths(Channel::Production)
        .expect("a macOS host resolves its layout");
    assert_eq!(paths.target(), Target::MacosArm64);
    assert!(
        paths
            .root()
            .to_str()
            .expect("UTF-8 root")
            .ends_with("Library/Application Support/Veles/NativeBridge")
    );
}

#[path = "../build_support/embedded_carrier.rs"]
mod channel;

/// The build-time channel transports exact bytes, and unset/empty/empty-file
/// inputs mean "this build embeds no release identity". A wrong input fails the
/// build instead of compiling an engine that ignores its identity.
#[test]
fn the_build_channel_transports_exact_bytes_and_refuses_a_wrong_input() {
    assert_eq!(channel::ENV, "VELES_EMBEDDED_CARRIER");
    assert_eq!(channel::channel_bytes(None).expect("unset"), None);
    assert_eq!(channel::channel_bytes(Some("")).expect("empty"), None);

    let temp = Temp::new("channel");
    let empty = temp.path().join("empty.bin");
    fs::write(&empty, b"").expect("empty file");
    assert_eq!(
        channel::channel_bytes(Some(empty.to_str().expect("UTF-8"))).expect("empty file"),
        None
    );

    let carrier_path = temp.path().join("carrier.bin");
    let carrier_bytes = carrier();
    fs::write(&carrier_path, &carrier_bytes).expect("carrier");
    assert_eq!(
        channel::channel_bytes(Some(carrier_path.to_str().expect("UTF-8"))).expect("carrier"),
        Some(carrier_bytes)
    );

    let not_a_carrier = temp.path().join("something-else.bin");
    fs::write(&not_a_carrier, b"not a carrier").expect("wrong document");
    for wrong in [
        temp.path().join("missing.bin"),
        temp.path().to_path_buf(),
        not_a_carrier,
    ] {
        assert!(
            channel::channel_bytes(Some(wrong.to_str().expect("UTF-8"))).is_err(),
            "a wrong channel input fails the build: {wrong:?}"
        );
    }
}

/// A production-channel build must embed the authenticated carrier: leaving it out is a
/// build error, not an engine that later answers `policy-denied`. A fixture build without
/// one is the honest fixture state; the development channel never ships a release carrier.
#[test]
fn the_build_channel_rule_fails_closed_for_a_production_build() {
    use channel::{BuildChannel, build_channel, resolve};

    assert_eq!(
        build_channel(None).expect("unset"),
        BuildChannel::Unspecified
    );
    assert_eq!(
        build_channel(Some("")).expect("empty"),
        BuildChannel::Unspecified
    );
    assert_eq!(
        build_channel(Some("production")).expect("production"),
        BuildChannel::Production
    );
    assert_eq!(
        build_channel(Some("fixture")).expect("fixture"),
        BuildChannel::Fixture
    );
    assert_eq!(
        build_channel(Some("development")).expect("development"),
        BuildChannel::Development
    );
    assert!(build_channel(Some("release")).is_err());

    assert_eq!(resolve(None, None).expect("unreleased"), None);
    assert_eq!(
        resolve(None, Some("fixture")).expect("fixture without a carrier"),
        None
    );
    assert!(
        resolve(None, Some("production"))
            .expect_err("production without a carrier")
            .contains("production")
    );
    assert!(
        resolve(None, Some("production"))
            .expect_err("production without a carrier")
            .contains(channel::ENV)
    );

    let temp = Temp::new("channel-rule");
    let carrier_path = temp.path().join("embedded-carrier.bin");
    let carrier_bytes = carrier();
    fs::write(&carrier_path, &carrier_bytes).expect("carrier");
    let path = carrier_path.to_str().expect("UTF-8");
    assert_eq!(
        resolve(Some(path), Some("production")).expect("production with a carrier"),
        Some(carrier_bytes.clone())
    );
    assert!(
        resolve(Some(path), Some("fixture"))
            .expect_err("a production carrier in a fixture build")
            .contains("fixture")
    );
    assert!(
        resolve(Some(path), Some("development"))
            .expect_err("development must not embed a release carrier")
            .contains("development")
    );

    // A fixture build must not embed a production carrier, and a production build must not embed
    // a fixture one: the declared channel and the carrier's channel must agree.
    let fixture_path = temp.path().join("fixture-carrier.bin");
    let fixture_bytes = carrier_for(Channel::Fixture);
    fs::write(&fixture_path, &fixture_bytes).expect("fixture carrier");
    let fixture = fixture_path.to_str().expect("UTF-8");
    assert_eq!(
        resolve(Some(fixture), Some("fixture")).expect("fixture carrier in a fixture build"),
        Some(fixture_bytes)
    );
    assert!(
        resolve(Some(fixture), Some("production"))
            .expect_err("a fixture carrier in a production build")
            .contains("production")
    );
}

/// A compiled-in carrier is only usable by the channel it belongs to. A fixture engine that
/// somehow embeds a production carrier refuses at run time, rather than installing production
/// identity under the fixture root.
#[test]
fn an_embedded_carrier_is_bound_to_its_build_channel() {
    use veles_bridge_installer::embedded::decode_embedded_carrier_for_channel;

    assert_eq!(
        decode_embedded_carrier_for_channel(None, Channel::Fixture).expect("absent"),
        None
    );
    assert_eq!(
        decode_embedded_carrier_for_channel(Some(&carrier()), Channel::Production)
            .expect("production carrier in a production build")
            .expect("present"),
        descriptor()
    );
    assert_eq!(
        decode_embedded_carrier_for_channel(Some(&carrier()), Channel::Fixture)
            .expect_err("a production carrier in a fixture build is refused")
            .code,
        FailureCode::PolicyDenied
    );
    assert_eq!(
        decode_embedded_carrier_for_channel(Some(&carrier()), Channel::Development)
            .expect_err("a production carrier in a development build is refused")
            .code,
        FailureCode::PolicyDenied
    );
}
