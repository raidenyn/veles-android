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
    FIXTURE_PUBLISHER, host_assessment, macos_carrier_from_package, require_fixture, trust_proof,
    windows_carrier_from_image,
};
use veles_bridge_installer::{
    Channel, Digest, FIXTURE_HOST_NAME, FailureCode, FakePlatform, Faults, HostAssessment,
    HostFault, HostState, InstallFailure, InstallLock, InstallPaths, InstallResult, InventoryEntry,
    OwnedEntry, PACKAGE_MACOS_CARRIER, PACKAGE_WINDOWS_CARRIER, PRODUCTION_HOST_NAME,
    PackageSource, Platform, PortablePlatform, Registration, ReleaseDescriptor, ReleasePolicy,
    TRUST_ASSESSMENT_MACOS_GATEKEEPER, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, Target, TrustProof,
    ValidatedPath, authenticate_package, canonical_json, normalize_relative_path, path_is_within,
    write_carrier, write_inventory, write_sealed_carrier,
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

/// The cleanup helper's compile-time fixture signature path is bound to the fixture root and the
/// exact deterministic staging image for its transaction; nothing else is accepted.
#[test]
fn the_fixture_helper_path_is_bound_to_the_fixture_root_and_transaction() {
    use veles_bridge_installer::platform::debug_trust::{
        fixture_cleanup_helper_path, fixture_helper_publisher,
    };

    let temp = Temp::new("helper-path");
    let root = temp.path().join("Veles").join("NativeBridgeFixture");
    fs::create_dir_all(&root).expect("fixture root");
    let transaction = "ab".repeat(32);
    let helper = fixture_cleanup_helper_path(&root, &transaction).expect("bound helper path");
    fs::create_dir_all(helper.parent().expect("stage parent")).expect("stage dir");
    fs::write(&helper, b"unsigned-helper").expect("helper");
    assert_eq!(
        fixture_helper_publisher(&helper, &root, &transaction),
        Some(FIXTURE_PUBLISHER)
    );

    // The production install root never selects the fixture path.
    let production = temp.path().join("Veles").join("NativeBridge");
    fs::create_dir_all(&production).expect("production root");
    assert_eq!(
        fixture_helper_publisher(&helper, &production, &transaction),
        None
    );

    // A helper anywhere else is refused even with the fixture root and transaction.
    let arbitrary = temp.path().join("elsewhere").join("helper");
    fs::create_dir_all(arbitrary.parent().expect("parent")).expect("dir");
    fs::write(&arbitrary, b"unsigned-helper").expect("helper");
    assert_eq!(
        fixture_helper_publisher(&arbitrary, &root, &transaction),
        None
    );

    // A different transaction names a different staging directory.
    let other = "cd".repeat(32);
    assert_eq!(fixture_helper_publisher(&helper, &root, &other), None);

    // A malformed transaction is never a fixture helper identity.
    assert_eq!(fixture_cleanup_helper_path(&root, "ab"), None);
    assert_eq!(fixture_cleanup_helper_path(&production, &transaction), None);

    // A full-length transaction with a non-hex character is still not a transaction id, and the
    // helper path for that transaction is refused.
    let non_hex = format!("{}zz", "ab".repeat(31));
    assert_eq!(non_hex.len(), 64);
    assert_eq!(fixture_cleanup_helper_path(&root, &non_hex), None);
    assert_eq!(fixture_helper_publisher(&helper, &root, &non_hex), None);
    assert_eq!(
        fixture_helper_publisher(&helper, &root, &format!("{}cd", "ab".repeat(31))),
        Some(FIXTURE_PUBLISHER),
        "a different valid transaction only changes the staging directory"
    );

    // The fixture root must sit directly below a directory named `Veles`, not merely end in
    // `NativeBridgeFixture`.
    let elsewhere_parent = temp.path().join("Other").join("NativeBridgeFixture");
    fs::create_dir_all(&elsewhere_parent).expect("wrong-parent fixture root");
    assert_eq!(
        fixture_cleanup_helper_path(&elsewhere_parent, &transaction),
        None
    );
    assert_eq!(
        fixture_helper_publisher(&helper, &elsewhere_parent, &transaction),
        None
    );
}

/// The debug fixture adapter as the native Windows/macOS adapters implement it: the carrier bytes
/// are read from the package on disk, extracted by the same `debug_trust` helpers the native
/// adapters call, and turned into evidence by `trust_proof`/`host_assessment`. Only the OS signature
/// assessment is replaced; no `TrustProof` is injected.
struct DebugFixturePlatform {
    inner: PortablePlatform,
}

impl DebugFixturePlatform {
    fn new() -> Self {
        Self {
            inner: PortablePlatform::new(),
        }
    }
}

impl Platform for DebugFixturePlatform {
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock> {
        self.inner.lock(paths)
    }

    fn write_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        self.inner.write_durable(path, bytes)
    }

    fn replace_manifest(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        self.inner.replace_manifest(staged, live)
    }

    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>> {
        self.inner.read_registration(paths)
    }

    fn publish_registration(
        &self,
        paths: &InstallPaths,
        value: &Registration,
    ) -> InstallResult<()> {
        self.inner.publish_registration(paths, value)
    }

    fn remove_registration(
        &self,
        paths: &InstallPaths,
        expected: &Registration,
    ) -> InstallResult<()> {
        self.inner.remove_registration(paths, expected)
    }

    fn inspect_hosts(&self, paths: &InstallPaths) -> InstallResult<HostState> {
        self.inner.inspect_hosts(paths)
    }

    fn remove_owned(&self, entry: &OwnedEntry) -> InstallResult<()> {
        self.inner.remove_owned(entry)
    }

    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()> {
        self.inner.flush_parent(path)
    }

    fn verify_trust(
        &self,
        source: &PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        match expected.target {
            Target::WindowsX64 => {
                let image = fs::read(source.path().join(PACKAGE_WINDOWS_CARRIER))
                    .map_err(|_| InstallFailure::new(FailureCode::InvalidPackage))?;
                let bytes = windows_carrier_from_image(&image)?;
                trust_proof(Target::WindowsX64, expected, bytes)
            }
            Target::MacosArm64 => {
                let bytes = macos_carrier_from_package(source.path())?;
                trust_proof(Target::MacosArm64, expected, bytes)
            }
        }
    }

    fn verify_host(
        &self,
        root: &Path,
        executable: &str,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<HostAssessment> {
        let relative = normalize_relative_path(executable)?;
        let path = root.join(&relative);
        if !path_is_within(root, &path) || !path.is_file() {
            return Err(InstallFailure::new(FailureCode::InvalidPackage));
        }
        match expected.target {
            Target::WindowsX64 => host_assessment(Target::WindowsX64, expected),
            Target::MacosArm64 => host_assessment(Target::MacosArm64, expected),
        }
    }
}

// The geometry of the synthetic PE32+ image the Windows tests feed the `pe` reader.
const PE_OFFSET: usize = 0x40;
const PE_OPTIONAL_LEN: usize = 0xF0;
const PE_OPTIONAL: usize = PE_OFFSET + 4 + 20;
const PE_SECTION_TABLE: usize = PE_OPTIONAL + PE_OPTIONAL_LEN;
const PE_DIRECTORY_BASE: usize = PE_OPTIONAL + 112;
const PE_RAW_OFFSET: usize = 0x200;
const PE_RAW_LEN: usize = 0x400;
const PE_SECTION_VA: u32 = 0x1000;
const PE_CARRIER_IN_SECTION: usize = 0xA0;
const PE_CERTIFICATE_LEN: usize = 16;
const PE_RESOURCE_TYPE_RCDATA: u32 = 10;
const PE_RESOURCE_NAME: &str = "VELES_POLICY_CARRIER";
/// Offset of the language directory's single data entry inside the resource section.
const PE_CARRIER_DATA_ENTRY: usize = 0x88;

fn put_u16(image: &mut [u8], offset: usize, value: u16) {
    image[offset..offset + 2].copy_from_slice(&value.to_le_bytes());
}

fn put_u32(image: &mut [u8], offset: usize, value: u32) {
    image[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
}

/// A minimal PE32+ image whose only section carries an `RT_RCDATA`/`VELES_POLICY_CARRIER`
/// resource and whose terminal certificate table ends the file. Test support only; mirrors the
/// fixture the `pe` unit tests use.
fn minimal_pe_image(carrier: &[u8]) -> Vec<u8> {
    let certificate_offset = PE_RAW_OFFSET + PE_RAW_LEN;
    let mut image = vec![0u8; certificate_offset + PE_CERTIFICATE_LEN];
    image[..2].copy_from_slice(b"MZ");
    put_u32(&mut image, 0x3C, PE_OFFSET as u32);
    image[PE_OFFSET..PE_OFFSET + 4].copy_from_slice(b"PE\0\0");
    let coff = PE_OFFSET + 4;
    put_u16(&mut image, coff, 0x8664);
    put_u16(&mut image, coff + 2, 1);
    put_u16(&mut image, coff + 16, PE_OPTIONAL_LEN as u16);
    put_u16(&mut image, PE_OPTIONAL, 0x20b);
    put_u32(&mut image, PE_OPTIONAL + 108, 16);
    put_u32(&mut image, PE_DIRECTORY_BASE + 16, PE_SECTION_VA);
    put_u32(&mut image, PE_DIRECTORY_BASE + 20, 0x100);
    put_u32(
        &mut image,
        PE_DIRECTORY_BASE + 32,
        certificate_offset as u32,
    );
    put_u32(
        &mut image,
        PE_DIRECTORY_BASE + 36,
        PE_CERTIFICATE_LEN as u32,
    );
    image[PE_SECTION_TABLE..PE_SECTION_TABLE + 5].copy_from_slice(b".rsrc");
    put_u32(&mut image, PE_SECTION_TABLE + 8, PE_RAW_LEN as u32);
    put_u32(&mut image, PE_SECTION_TABLE + 12, PE_SECTION_VA);
    put_u32(&mut image, PE_SECTION_TABLE + 16, PE_RAW_LEN as u32);
    put_u32(&mut image, PE_SECTION_TABLE + 20, PE_RAW_OFFSET as u32);

    let resource = |offset: usize| PE_RAW_OFFSET + offset;
    put_u16(&mut image, resource(0) + 14, 1);
    put_u32(&mut image, resource(0x10), PE_RESOURCE_TYPE_RCDATA);
    put_u32(&mut image, resource(0x14), 0x8000_0018);
    put_u16(&mut image, resource(0x18) + 12, 1);
    put_u32(&mut image, resource(0x28), 0x8000_0040);
    put_u32(&mut image, resource(0x2C), 0x8000_0070);
    let name: Vec<u16> = PE_RESOURCE_NAME.encode_utf16().collect();
    put_u16(&mut image, resource(0x40), name.len() as u16);
    for (index, unit) in name.iter().enumerate() {
        put_u16(&mut image, resource(0x42 + index * 2), *unit);
    }
    put_u16(&mut image, resource(0x70) + 14, 1);
    put_u32(&mut image, resource(0x80), 0x409);
    put_u32(&mut image, resource(0x84), 0x88);
    put_u32(
        &mut image,
        resource(PE_CARRIER_DATA_ENTRY),
        PE_SECTION_VA + PE_CARRIER_IN_SECTION as u32,
    );
    put_u32(
        &mut image,
        resource(PE_CARRIER_DATA_ENTRY + 4),
        carrier.len() as u32,
    );
    image[resource(PE_CARRIER_IN_SECTION)..resource(PE_CARRIER_IN_SECTION) + carrier.len()]
        .copy_from_slice(carrier);
    image
}

/// A genuinely unsigned image, exactly like the Windows debug producer writes it: the named
/// resource is embedded, but the certificate directory is empty and no certificate bytes follow
/// the last section. The release reader refuses this image by design; the debug adapter must not.
fn unsigned_pe_image(carrier: &[u8]) -> Vec<u8> {
    let mut image = minimal_pe_image(carrier);
    image.truncate(PE_RAW_OFFSET + PE_RAW_LEN);
    put_u32(&mut image, PE_DIRECTORY_BASE + 32, 0);
    put_u32(&mut image, PE_DIRECTORY_BASE + 36, 0);
    image
}

/// Build the on-disk package document set (payload, canonical/sealed inventory, descriptor and the
/// platform carrier artifact). The package root is `<root>/package`.
fn write_real_package(
    root: &Path,
    target: Target,
    descriptor: &ReleaseDescriptor,
    payload: &[u8],
    canonical_inventory: &[u8],
    sealed_inventory: Option<&[u8]>,
    carrier_document: &[u8],
) -> PathBuf {
    let package = root.join("package");
    let payload_root = package.join("payload");
    fs::create_dir_all(&payload_root).expect("payload root");
    let payload_name = match target {
        Target::WindowsX64 => "veles-native-host.exe",
        Target::MacosArm64 => "veles-native-host",
    };
    fs::write(payload_root.join(payload_name), payload).expect("payload host");
    fs::write(package.join("inventory.json"), canonical_inventory).expect("canonical inventory");
    if let Some(sealed) = sealed_inventory {
        fs::write(package.join("sealed-inventory.json"), sealed).expect("sealed inventory");
    }
    fs::write(
        package.join("descriptor.json"),
        canonical_json(descriptor).expect("descriptor JSON"),
    )
    .expect("descriptor");
    match target {
        Target::WindowsX64 => {
            fs::write(
                package.join(PACKAGE_WINDOWS_CARRIER),
                minimal_pe_image(carrier_document),
            )
            .expect("carrier image");
        }
        Target::MacosArm64 => {
            let resources = package
                .join(PACKAGE_MACOS_CARRIER)
                .join("Contents")
                .join("Resources");
            fs::create_dir_all(&resources).expect("carrier bundle");
            fs::write(resources.join("veles-policy-carrier"), carrier_document)
                .expect("carrier resource");
        }
    }
    package
}

struct RealSealedWindowsPackage {
    package: PathBuf,
    descriptor: ReleaseDescriptor,
    signed_payload: Vec<u8>,
    sealed_inventory: Vec<u8>,
    carrier: Vec<u8>,
}

/// A real Windows fixture package whose payload is "ad-hoc signed": canonical inventory for the
/// unsigned bytes, sealed inventory for the signed bytes, and a carrier asserting the sealed digest.
fn sealed_windows_package(temp: &Temp) -> RealSealedWindowsPackage {
    let target = Target::WindowsX64;
    // A signable image (Mach-O magic) so the signed-byte correspondence may differ.
    let unsigned = b"\xcf\xfa\xed\xfe-unsigned-fixture-host".to_vec();
    let signed_payload = [unsigned.as_slice(), b"-AD-HOC-SIGNATURE"].concat();
    let canonical = write_inventory(&[InventoryEntry {
        path: "veles-native-host.exe".to_owned(),
        size: unsigned.len() as u64,
        sha256: Digest::of(&unsigned),
        executable: true,
    }])
    .expect("canonical inventory");
    let sealed = write_inventory(&[InventoryEntry {
        path: "veles-native-host.exe".to_owned(),
        size: signed_payload.len() as u64,
        sha256: Digest::of(&signed_payload),
        executable: true,
    }])
    .expect("sealed inventory");
    let mut descriptor = descriptor(Channel::Fixture, target);
    descriptor.payload_id = Digest::of(&canonical);
    let carrier = write_sealed_carrier(
        &canonical_json(&descriptor).expect("descriptor JSON"),
        Digest::of(&sealed),
    )
    .expect("sealed carrier");
    let package = write_real_package(
        temp.path(),
        target,
        &descriptor,
        &signed_payload,
        &canonical,
        Some(&sealed),
        &carrier,
    );
    RealSealedWindowsPackage {
        package,
        descriptor,
        signed_payload,
        sealed_inventory: sealed,
        carrier,
    }
}

/// The same real package, but with the carrier image the Windows debug producer actually ships:
/// an unsigned `carrier.exe` with an empty certificate directory.
fn unsigned_windows_package(temp: &Temp) -> RealSealedWindowsPackage {
    let fixture = sealed_windows_package(temp);
    fs::write(
        fixture.package.join(PACKAGE_WINDOWS_CARRIER),
        unsigned_pe_image(&fixture.carrier),
    )
    .expect("unsigned carrier image");
    fixture
}

fn must_deny(package: &Path, expected: &ReleaseDescriptor) -> FailureCode {
    authenticate_package(&DebugFixturePlatform::new(), package, expected)
        .expect_err("the package must be refused")
        .code
}

#[test]
fn a_real_windows_fixture_package_authenticates_through_the_fixture_adapter() {
    let temp = Temp::new("real-windows");
    let fixture = sealed_windows_package(&temp);
    let authenticated = authenticate_package(
        &DebugFixturePlatform::new(),
        &fixture.package,
        &fixture.descriptor,
    )
    .expect("the real on-disk fixture package authenticates");
    assert_eq!(authenticated.signer(), FIXTURE_PUBLISHER);
    assert_eq!(
        authenticated.assessment(),
        TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE
    );
    assert_eq!(
        authenticated.sealed_inventory(),
        Some(fixture.sealed_inventory.as_slice())
    );
    assert_eq!(
        authenticated.payload()[0].sha256(),
        Digest::of(&fixture.signed_payload)
    );
}

#[test]
fn an_unsigned_windows_debug_carrier_authenticates_only_through_the_debug_adapter() {
    let temp = Temp::new("unsigned-windows");
    let fixture = unsigned_windows_package(&temp);
    let image = fs::read(fixture.package.join(PACKAGE_WINDOWS_CARRIER)).expect("carrier image");
    // The Windows debug producer never signs `carrier.exe`, so its certificate directory is empty:
    // exactly the image the release reader refuses (see the `pe` unit tests, which assert that on
    // this same geometry). The debug reader must still return the exact embedded document.
    let extracted =
        windows_carrier_from_image(&image).expect("the debug reader reads an unsigned image");
    assert_eq!(extracted, fixture.carrier);

    // The unchanged decision layer authenticates the real on-disk package; only the OS signature
    // assessment is replaced, never the package/carrier binding.
    let authenticated = authenticate_package(
        &DebugFixturePlatform::new(),
        &fixture.package,
        &fixture.descriptor,
    )
    .expect("the unsigned debug package authenticates");
    assert_eq!(authenticated.signer(), FIXTURE_PUBLISHER);
    assert_eq!(
        authenticated.assessment(),
        TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE
    );
    assert_eq!(
        authenticated.sealed_inventory(),
        Some(fixture.sealed_inventory.as_slice())
    );
    assert_eq!(
        authenticated.payload()[0].sha256(),
        Digest::of(&fixture.signed_payload)
    );
}

#[test]
fn a_windows_debug_carrier_with_a_malformed_resource_is_refused() {
    let temp = Temp::new("unsigned-malformed");
    let fixture = unsigned_windows_package(&temp);
    let image = fs::read(fixture.package.join(PACKAGE_WINDOWS_CARRIER)).expect("carrier image");
    windows_carrier_from_image(&image).expect("baseline unsigned image");
    // Point the carrier data entry past the section's raw data: the bytes are no longer image
    // content, so the bounded reader must refuse them rather than slice arbitrary file bytes.
    let mut malformed = image;
    put_u32(
        &mut malformed,
        PE_RAW_OFFSET + PE_CARRIER_DATA_ENTRY,
        PE_SECTION_VA + PE_RAW_LEN as u32 + 0x100,
    );
    assert_eq!(
        windows_carrier_from_image(&malformed)
            .expect_err("a malformed resource")
            .code,
        FailureCode::TrustDenied
    );
    // A truncated image is not a PE image either.
    let mut truncated =
        fs::read(fixture.package.join(PACKAGE_WINDOWS_CARRIER)).expect("carrier image");
    truncated.truncate(0x20);
    assert_eq!(
        windows_carrier_from_image(&truncated)
            .expect_err("a truncated image")
            .code,
        FailureCode::TrustDenied
    );
}

#[test]
fn a_real_macos_fixture_package_authenticates_through_the_fixture_adapter() {
    let temp = Temp::new("real-macos");
    let payload = b"\xcf\xfa\xed\xfe-macos-fixture-host";
    let canonical = write_inventory(&[InventoryEntry {
        path: "veles-native-host".to_owned(),
        size: payload.len() as u64,
        sha256: Digest::of(payload),
        executable: true,
    }])
    .expect("canonical inventory");
    let mut descriptor = descriptor(Channel::Fixture, Target::MacosArm64);
    descriptor.payload_id = Digest::of(&canonical);
    let carrier = carrier(&descriptor);
    let package = write_real_package(
        temp.path(),
        Target::MacosArm64,
        &descriptor,
        payload,
        &canonical,
        None,
        &carrier,
    );
    let authenticated = authenticate_package(&DebugFixturePlatform::new(), &package, &descriptor)
        .expect("the real macOS fixture package authenticates");
    assert_eq!(authenticated.signer(), FIXTURE_PUBLISHER);
    assert_eq!(
        authenticated.assessment(),
        TRUST_ASSESSMENT_MACOS_GATEKEEPER
    );
}

/// A real macOS fixture package, its descriptor and the carrier document it carries.
fn macos_fixture_package(temp: &Temp) -> (PathBuf, ReleaseDescriptor, Vec<u8>) {
    let payload = b"\xcf\xfa\xed\xfe-macos-fixture-host";
    let canonical = write_inventory(&[InventoryEntry {
        path: "veles-native-host".to_owned(),
        size: payload.len() as u64,
        sha256: Digest::of(payload),
        executable: true,
    }])
    .expect("canonical inventory");
    let mut descriptor = descriptor(Channel::Fixture, Target::MacosArm64);
    descriptor.payload_id = Digest::of(&canonical);
    let carrier = carrier(&descriptor);
    let package = write_real_package(
        temp.path(),
        Target::MacosArm64,
        &descriptor,
        payload,
        &canonical,
        None,
        &carrier,
    );
    (package, descriptor, carrier)
}

/// The debug adapter validates `carrier.app` before reading a resource byte: the carrier bundle must
/// be an existing, non-link directory below the package root, exactly as the release adapter
/// requires. A linked bundle - even one that resolves inside the same package - is refused.
#[cfg(unix)]
#[test]
fn a_macos_debug_package_with_a_resolving_carrier_bundle_link_is_refused() {
    use std::os::unix::fs::symlink;

    let temp = Temp::new("macos-linked-bundle");
    let (package, descriptor, _carrier) = macos_fixture_package(&temp);
    let bundle = package.join(PACKAGE_MACOS_CARRIER);
    let relocated = package.join("relocated-carrier.app");
    fs::rename(&bundle, &relocated).expect("relocated carrier bundle");
    symlink(&relocated, &bundle).expect("linked carrier bundle");

    // Before the carrier validation was restored the resolved bundle was read and the package
    // authenticated; a link is never the carrier this installer packaged.
    assert_eq!(
        macos_carrier_from_package(&package)
            .expect_err("a linked carrier bundle")
            .code,
        FailureCode::TrustDenied
    );
    assert_eq!(must_deny(&package, &descriptor), FailureCode::TrustDenied);
}

/// The carrier resource itself must be contained in that validated bundle and is never read through
/// a link, so a link that resolves to a document elsewhere in the package is refused.
#[cfg(unix)]
#[test]
fn a_macos_debug_package_with_a_resolving_carrier_resource_link_is_refused() {
    use std::os::unix::fs::symlink;

    let temp = Temp::new("macos-linked-resource");
    let (package, descriptor, carrier_document) = macos_fixture_package(&temp);
    let resource = package
        .join(PACKAGE_MACOS_CARRIER)
        .join("Contents")
        .join("Resources")
        .join("veles-policy-carrier");
    let relocated = package.join("relocated-carrier-document.bin");
    fs::write(&relocated, &carrier_document).expect("relocated carrier document");
    fs::remove_file(&resource).expect("removed carrier resource");
    symlink(&relocated, &resource).expect("linked carrier resource");

    assert_eq!(
        macos_carrier_from_package(&package)
            .expect_err("a linked carrier resource")
            .code,
        FailureCode::TrustDenied
    );
    assert_eq!(must_deny(&package, &descriptor), FailureCode::TrustDenied);
}

#[test]
fn a_real_fixture_package_is_denied_when_the_payload_or_carrier_changes() {
    let temp = Temp::new("real-mutated-payload");
    let fixture = sealed_windows_package(&temp);
    let payload_path = fixture
        .package
        .join("payload")
        .join("veles-native-host.exe");
    let mut bytes = fs::read(&payload_path).expect("payload");
    bytes[8] ^= 0xff;
    fs::write(&payload_path, &bytes).expect("mutated payload");
    assert_eq!(
        must_deny(&fixture.package, &fixture.descriptor),
        FailureCode::TrustDenied
    );

    let temp = Temp::new("real-mutated-carrier");
    let fixture = sealed_windows_package(&temp);
    let carrier_path = fixture.package.join(PACKAGE_WINDOWS_CARRIER);
    let image = fs::read(&carrier_path).expect("carrier image");
    let embedded = windows_carrier_from_image(&image).expect("embedded carrier");
    let mut mutated = embedded;
    let last = mutated.len() - 1;
    mutated[last] ^= 0xff; // the sealed-inventory digest no longer matches the document
    fs::write(&carrier_path, minimal_pe_image(&mutated)).expect("mutated carrier image");
    assert_eq!(
        must_deny(&fixture.package, &fixture.descriptor),
        FailureCode::TrustDenied
    );
}

#[test]
fn a_real_fixture_package_is_denied_for_a_descriptor_other_than_the_compiled_in_one() {
    let temp = Temp::new("real-other-descriptor");
    let fixture = sealed_windows_package(&temp);
    let mut other = fixture.descriptor.clone();
    other.version = "9.9.9".to_owned();
    other.validate().expect("valid other descriptor");
    assert_eq!(
        must_deny(&fixture.package, &other),
        FailureCode::TrustDenied
    );
}

#[test]
fn a_real_package_claiming_production_identity_never_uses_the_debug_path() {
    assert_eq!(
        require_fixture(&descriptor(Channel::Production, Target::WindowsX64))
            .expect_err("production is never a fixture")
            .code,
        FailureCode::TrustDenied
    );

    let temp = Temp::new("real-production");
    let payload = b"\xcf\xfa\xed\xfe-production-host";
    let canonical = write_inventory(&[InventoryEntry {
        path: "veles-native-host.exe".to_owned(),
        size: payload.len() as u64,
        sha256: Digest::of(payload),
        executable: true,
    }])
    .expect("canonical inventory");
    let mut production = descriptor(Channel::Production, Target::WindowsX64);
    production.payload_id = Digest::of(&canonical);
    let carrier = write_carrier(&canonical_json(&production).expect("descriptor JSON"))
        .expect("production carrier");
    let package = write_real_package(
        temp.path(),
        Target::WindowsX64,
        &production,
        payload,
        &canonical,
        None,
        &carrier,
    );
    // The adapter reads the real carrier from the package and then refuses the production
    // identity, so the package never authenticates through the debug path.
    assert_eq!(must_deny(&package, &production), FailureCode::TrustDenied);
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
