//! Compile-time-only fixture trust for DEBUG installers (OTP-25 PR path).
//!
//! These tests are compiled only when the `debug-fixture-trust` cargo feature is enabled
//! (`#![cfg(feature = "debug-fixture-trust")]`): the adapter does not exist in a release
//! build at all. They prove the properties the debug producer relies on:
//!
//! * the adapter accepts the fixture channel/identity only, never production or development;
//! * the proof it returns is the fixed fixture publisher and the target's accepted assessment,
//!   so the unchanged `authenticate_package`/`assess_host` decision layer accepts the package;
//! * the feature cannot be enabled in a release profile or for the production channel (the
//!   build script refuses the combination), so a release binary cannot activate it through an
//!   argument, environment variable or descriptor field.
//!
//! The native `verify_trust`/`verify_host` dispatch that reads the carrier bytes without OS
//! validation lives in the Windows/macOS adapters (`src/platform/windows.rs`,
//! `src/platform/macos.rs`) and is NOT RUN on this Linux host; the portable half below is.

#![cfg(feature = "debug-fixture-trust")]

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};

use veles_bridge_installer::platform::debug_trust::{
    FIXTURE_PUBLISHER, host_assessment, trust_proof,
};
use veles_bridge_installer::{
    Channel, Digest, FIXTURE_HOST_NAME, FailureCode, FakePlatform, Faults, HostFault,
    InventoryEntry, PRODUCTION_HOST_NAME, ReleaseDescriptor, ReleasePolicy,
    TRUST_ASSESSMENT_MACOS_GATEKEEPER, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, Target, TrustProof,
    authenticate_package, canonical_json, write_carrier, write_inventory, write_sealed_carrier,
};

const EXTENSION_ID: &str = "ffffffffffffffffffffffffffffffff";
static COUNTER: AtomicU64 = AtomicU64::new(0);

struct Temp(PathBuf);

impl Temp {
    fn new(tag: &str) -> Self {
        let unique = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join("debug-trust-tmp")
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

fn descriptor(channel: Channel, target: Target) -> ReleaseDescriptor {
    let identity = channel.identity();
    let descriptor = ReleaseDescriptor {
        schema: 1,
        channel,
        version: "0.1.0".to_owned(),
        source_commit: "0123456789abcdef0123456789abcdef01234567".to_owned(),
        target,
        minimum_os: match target {
            Target::WindowsX64 => "10".to_owned(),
            Target::MacosArm64 => "26.0".to_owned(),
        },
        extension_id: EXTENSION_ID.to_owned(),
        host_name: identity.host_name.to_owned(),
        bundle_id: identity.bundle_id.to_owned(),
        api_min: 1,
        api_max: 1,
        payload_id: Digest::of(b"payload"),
        policy: ReleasePolicy {
            publishers: vec![FIXTURE_PUBLISHER.to_owned()],
            denied_payloads: Vec::new(),
            rollback_payloads: Vec::new(),
            epoch: 1,
        },
    };
    descriptor.validate().expect("fixture descriptor");
    descriptor
}

fn carrier(descriptor: &ReleaseDescriptor) -> Vec<u8> {
    write_carrier(&canonical_json(descriptor).expect("descriptor JSON")).expect("carrier")
}

/// One complete engine package with a fixture payload and a (deliberately unsigned) carrier
/// artifact. `FakePlatform` never reads the artifact: it is the injected OS evidence.
fn fixture_package(root: &Path, descriptor: &ReleaseDescriptor) -> PathBuf {
    let package = root.join("package");
    let payload = package.join("payload");
    fs::create_dir_all(&payload).expect("payload root");
    let content = b"fixture host payload";
    fs::write(payload.join("veles-native-host.exe"), content).expect("host");
    let entries = vec![InventoryEntry {
        path: "veles-native-host.exe".to_owned(),
        size: content.len() as u64,
        sha256: Digest::of(content),
        executable: true,
    }];
    let inventory = write_inventory(&entries).expect("inventory");
    fs::write(package.join("inventory.json"), &inventory).expect("inventory file");
    let mut descriptor = descriptor.clone();
    descriptor.payload_id = Digest::of(&inventory);
    fs::write(package.join("carrier.exe"), carrier(&descriptor)).expect("carrier artifact");
    package
}

fn debug_platform(proof: TrustProof, host: Option<HostFault>) -> FakePlatform {
    FakePlatform::with_faults(Faults {
        trust: Some(proof),
        host,
        ..Faults::default()
    })
}

fn trust_denied(result: veles_bridge_installer::InstallResult<TrustProof>) -> FailureCode {
    result.expect_err("must be refused").code
}

#[test]
fn the_debug_adapter_accepts_only_the_fixture_channel_and_identity() {
    let bytes = b"VELESPC1-fixture-carrier".to_vec();
    for target in [Target::WindowsX64, Target::MacosArm64] {
        let fixture = descriptor(Channel::Fixture, target);
        let proof = trust_proof(target, &fixture, bytes.clone()).expect("fixture proof");
        assert_eq!(proof.signer(), FIXTURE_PUBLISHER);
        assert_eq!(proof.carrier(), bytes.as_slice());

        // Another channel's identity is refused before any carrier bytes are considered,
        // even when the descriptor calls itself "fixture" but names production identity.
        for channel in [Channel::Production, Channel::Development] {
            assert_eq!(
                trust_denied(trust_proof(
                    target,
                    &descriptor(channel, target),
                    bytes.clone()
                )),
                FailureCode::TrustDenied,
                "{channel:?} must never be accepted by the debug adapter"
            );
        }
        let mut forged = descriptor(Channel::Fixture, target);
        forged.host_name = PRODUCTION_HOST_NAME.to_owned();
        forged.bundle_id = PRODUCTION_HOST_NAME.to_owned();
        // `validate` would reject that identity, so the adapter is asked directly: the
        // explicit identity check (not the caller's validation) is the boundary here.
        assert_eq!(
            trust_denied(trust_proof(target, &forged, bytes.clone())),
            FailureCode::TrustDenied
        );

        // The target passed by the adapter must match the descriptor it is used for.
        let other = match target {
            Target::WindowsX64 => Target::MacosArm64,
            Target::MacosArm64 => Target::WindowsX64,
        };
        assert_eq!(
            trust_denied(trust_proof(other, &fixture, bytes.clone())),
            FailureCode::TrustDenied
        );

        // A descriptor whose authenticated policy does not list the fixed fixture publisher
        // cannot be made acceptable by the adapter.
        let mut foreign = descriptor(Channel::Fixture, target);
        foreign.policy.publishers = vec!["Somebody Else".to_owned()];
        assert_eq!(
            trust_denied(trust_proof(target, &foreign, bytes.clone())),
            FailureCode::TrustDenied
        );
    }
}

#[test]
fn the_fixture_host_identity_is_the_only_one_the_adapter_authorizes() {
    for target in [Target::WindowsX64, Target::MacosArm64] {
        let fixture = descriptor(Channel::Fixture, target);
        let assessment = host_assessment(target, &fixture).expect("fixture host assessment");
        assert_eq!(assessment.signer(), FIXTURE_PUBLISHER);
        assert_eq!(
            assessment.assessment(),
            match target {
                Target::WindowsX64 => TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
                Target::MacosArm64 => TRUST_ASSESSMENT_MACOS_GATEKEEPER,
            }
        );
        assert_eq!(
            host_assessment(target, &descriptor(Channel::Production, target))
                .expect_err("production is never assessed by the debug adapter")
                .code,
            FailureCode::TrustDenied
        );
    }
    assert_eq!(FIXTURE_HOST_NAME, Channel::Fixture.identity().host_name);
}

#[test]
fn the_unchanged_decision_layer_accepts_a_fixture_package_only_with_the_debug_proof() {
    let temp = Temp::new("package");
    let mut fixture = descriptor(Channel::Fixture, Target::WindowsX64);
    let package = fixture_package(temp.path(), &fixture);
    // The package is built from the inventory, so use the descriptor it actually carries.
    fixture.payload_id = Digest::of(&fs::read(package.join("inventory.json")).expect("inventory"));
    let carrier = carrier(&fixture);
    let proof = trust_proof(Target::WindowsX64, &fixture, carrier.clone()).expect("proof");

    let platform = debug_platform(proof.clone(), None);
    let authenticated = authenticate_package(&platform, &package, &fixture)
        .expect("the fixture package authenticates with the debug fixture proof");
    assert_eq!(authenticated.signer(), FIXTURE_PUBLISHER);
    assert_eq!(
        authenticated.assessment(),
        TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE
    );

    // A package descriptor that differs from the expected fixture descriptor is refused by
    // the unchanged decision layer even with the debug proof.
    let other = descriptor(Channel::Fixture, Target::WindowsX64);
    assert_eq!(
        authenticate_package(&platform, &package, &other)
            .expect_err("another descriptor is not the authenticated one")
            .code,
        FailureCode::TrustDenied
    );

    // A production policy listing a real publisher cannot be satisfied by the fixture proof.
    let mut production = descriptor(Channel::Production, Target::WindowsX64);
    production.payload_id = fixture.payload_id;
    assert_eq!(
        authenticate_package(&platform, &package, &production)
            .expect_err("production identity is never authenticated by the debug proof")
            .code,
        FailureCode::TrustDenied
    );

    // `assess_host` accepts exactly the fixture host assessment.
    let assessment = host_assessment(Target::WindowsX64, &fixture).expect("host assessment");
    let host_platform = debug_platform(
        proof,
        Some(HostFault::Assessed {
            signer: assessment.signer().to_owned(),
            assessment: assessment.assessment().to_owned(),
        }),
    );
    authenticate_package(&host_platform, &package, &fixture)
        .expect("the fixture host assessment is accepted by assess_host");
}

/// The debug producer ad-hoc signs the macOS payload and writes the signed-byte inventory, then
/// seals the carrier. The unchanged decision layer must accept exactly that package through the
/// sealed path, with the same proof the debug adapter returns.
#[test]
fn a_sealed_fixture_debug_package_authenticates_with_the_debug_proof() {
    let temp = Temp::new("sealed");
    let package = temp.path().join("package");
    let payload = package.join("payload");
    fs::create_dir_all(&payload).expect("payload root");
    // A signable image (a Mach-O magic the comparator recognizes), then "signed" bytes.
    let unsigned = b"\xcf\xfa\xed\xfe-unsigned-host".to_vec();
    fs::write(payload.join("veles-native-host"), &unsigned).expect("unsigned host");
    let canonical = write_inventory(&[InventoryEntry {
        path: "veles-native-host".to_owned(),
        size: unsigned.len() as u64,
        sha256: Digest::of(&unsigned),
        executable: true,
    }])
    .expect("canonical inventory");
    let mut descriptor = descriptor(Channel::Fixture, Target::MacosArm64);
    descriptor.payload_id = Digest::of(&canonical);

    let signed = [unsigned.as_slice(), b"AD-HOC-SIGNATURE"].concat();
    fs::write(payload.join("veles-native-host"), &signed).expect("signed host");
    let sealed = write_inventory(&[InventoryEntry {
        path: "veles-native-host".to_owned(),
        size: signed.len() as u64,
        sha256: Digest::of(&signed),
        executable: true,
    }])
    .expect("sealed inventory");
    fs::write(package.join("inventory.json"), &canonical).expect("canonical document");
    fs::write(package.join("sealed-inventory.json"), &sealed).expect("sealed document");
    let carrier = write_sealed_carrier(
        &canonical_json(&descriptor).expect("descriptor JSON"),
        Digest::of(&sealed),
    )
    .expect("sealed carrier");

    let proof = trust_proof(Target::MacosArm64, &descriptor, carrier).expect("proof");
    let authenticated = authenticate_package(&debug_platform(proof, None), &package, &descriptor)
        .expect("the sealed fixture debug package authenticates");
    assert_eq!(authenticated.sealed_inventory(), Some(sealed.as_slice()));
    assert_eq!(authenticated.payload()[0].sha256(), Digest::of(&signed));
}

fn clean_command() -> Command {
    let mut command = Command::new(env!("CARGO"));
    command
        .current_dir(env!("CARGO_MANIFEST_DIR"))
        .env_remove("VELES_EMBEDDED_CARRIER")
        .env_remove("VELES_BUILD_CHANNEL")
        .env_remove("VELES_WORKFLOW_RUN_ID")
        .env_remove("RUSTFLAGS")
        .env_remove("CARGO_ENCODED_RUSTFLAGS")
        .env_remove("CARGO_FEATURE_DEBUG_FIXTURE_TRUST");
    command
}

fn contains(haystack: &[u8], needle: &[u8]) -> bool {
    haystack
        .windows(needle.len())
        .any(|window| window == needle)
}

fn build(
    target: &Path,
    extra: &[&str],
    env: &[(&str, &str)],
    expect_success: bool,
) -> std::process::Output {
    let mut command = clean_command();
    command
        .args(["build", "--locked", "--target-dir"])
        .arg(target)
        .args(extra);
    for (name, value) in env {
        command.env(name, value);
    }
    let output = command.output().expect("nested cargo runs");
    assert_eq!(
        output.status.success(),
        expect_success,
        "cargo {extra:?} stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    output
}

fn binary_name(binary: &str) -> String {
    format!("{binary}{}", std::env::consts::EXE_SUFFIX)
}

/// The debug adapter is compiled out of every build that does not explicitly enable the
/// feature, so a release binary has no code path to activate (there is no runtime switch).
#[test]
fn the_debug_adapter_exists_only_when_the_feature_is_enabled() {
    let temp = Temp::new("feature");
    let plain = temp.path().join("plain");
    build(&plain, &["--bin", "veles-bridge-maintenance"], &[], true);
    let plain_bytes = fs::read(
        plain
            .join("debug")
            .join(binary_name("veles-bridge-maintenance")),
    )
    .expect("default maintenance binary");
    assert!(
        !contains(&plain_bytes, FIXTURE_PUBLISHER.as_bytes()),
        "a default build must not contain the fixture trust adapter"
    );

    // Both shipped helper binaries compile with the feature: the maintenance engine and the
    // outside-root cleanup helper are the same crate, so the fixture boundary covers both.
    let featured = temp.path().join("featured");
    build(
        &featured,
        &[
            "--features",
            "debug-fixture-trust",
            "--bin",
            "veles-bridge-maintenance",
            "--bin",
            "veles-bridge-cleanup",
        ],
        &[("VELES_BUILD_CHANNEL", "fixture")],
        true,
    );
    for binary in ["veles-bridge-maintenance", "veles-bridge-cleanup"] {
        assert!(
            featured.join("debug").join(binary_name(binary)).is_file(),
            "{binary} must build with the debug fixture trust feature"
        );
    }
}

/// The feature combination is refused by the build script, not by a runtime check: a release
/// profile build fails and a production-channel build fails, so neither can ship the adapter.
#[test]
fn the_debug_trust_feature_is_rejected_in_release_and_for_production() {
    let temp = Temp::new("refusal");

    let mut release = clean_command();
    let failed = release
        .args([
            "check",
            "--locked",
            "--release",
            "--features",
            "debug-fixture-trust",
            "--target-dir",
        ])
        .arg(temp.path().join("release"))
        .output()
        .expect("nested cargo runs");
    assert!(
        !failed.status.success(),
        "a release build must not enable debug trust"
    );
    let stderr = String::from_utf8_lossy(&failed.stderr);
    assert!(
        stderr.contains("debug-fixture-trust") && stderr.contains("release"),
        "the refusal must name the feature and the release profile: {stderr}"
    );

    let mut production = clean_command();
    let failed = production
        .args([
            "check",
            "--locked",
            "--features",
            "debug-fixture-trust",
            "--target-dir",
        ])
        .arg(temp.path().join("production"))
        .env("VELES_BUILD_CHANNEL", "production")
        .output()
        .expect("nested cargo runs");
    assert!(
        !failed.status.success(),
        "the production channel must not enable debug trust"
    );
    let stderr = String::from_utf8_lossy(&failed.stderr);
    assert!(
        stderr.contains("debug-fixture-trust") && stderr.contains("production"),
        "the refusal must name the feature and the production channel: {stderr}"
    );
}
