//! Canonical (unsigned) identity versus sealed signed-byte inventory (fix round F7).
//!
//! The release descriptor is compiled in *before* signing, so `payload_id` names
//! the unsigned canonical inventory. Signing then rewrites PE / Mach-O files, so
//! the bytes actually shipped are described by a second, sealed inventory whose
//! digest the OS-authenticated carrier asserts. These tests build that shape the
//! way the signing pipeline does and prove the engine authenticates, installs,
//! rolls back and refuses tampering across the two identities.
//!
//! The carrier bytes are assembled by hand in `carrier_bytes` (not with the
//! crate's writer) so the byte-level contract - header flag bit 0 plus a trailing
//! 32-byte digest - is what is under test.

use std::fs;
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicU64, Ordering};

use veles_bridge_installer::{
    Channel, Digest, FailureCode, FakePlatform, Faults, HostState, InstallPaths, InventoryEntry,
    Operation, PRODUCTION_HOST_NAME, ReleaseDescriptor, ReleasePolicy, STATE_ROLLED_BACK,
    TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, Target, TrustProof, Version, authenticate_package,
    canonical_json, install, run_operation, verify_correspondence, write_inventory,
};

const EXTENSION_ID: &str = "mnopabcdefghijklmnopabcdefghijkl";
const PUBLISHER: &str = "Publisher One";
const NOTICE: &[u8] = b"licence notice";

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct Temp(PathBuf);

impl Temp {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "veles-installer-identity-{}-{}",
            process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).expect("temp root");
        Self(path)
    }
}

impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// A PE-looking image: `MZ`, `e_lfanew` = 0x40 and a `PE\0\0` signature.
fn pe_image(tail: &[u8]) -> Vec<u8> {
    let mut image = vec![0u8; 0x80];
    image[..2].copy_from_slice(b"MZ");
    image[0x3c..0x40].copy_from_slice(&0x40u32.to_le_bytes());
    image[0x40..0x44].copy_from_slice(b"PE\0\0");
    image.extend_from_slice(tail);
    image
}

fn entry(path: &str, bytes: &[u8], executable: bool) -> InventoryEntry {
    InventoryEntry {
        path: path.to_owned(),
        size: bytes.len() as u64,
        sha256: Digest::of(bytes),
        executable,
    }
}

/// The bytes of a carrier: descriptor section, optionally a trailing sealed digest.
fn carrier_bytes(descriptor: &ReleaseDescriptor, sealed: Option<Digest>) -> Vec<u8> {
    let document = canonical_json(descriptor).expect("descriptor JSON");
    let mut carrier = Vec::new();
    carrier.extend_from_slice(b"VELESPC1");
    carrier.extend_from_slice(&1u16.to_le_bytes());
    carrier.extend_from_slice(&u16::from(sealed.is_some()).to_le_bytes());
    carrier.extend_from_slice(&32u32.to_le_bytes());
    carrier.extend_from_slice(&32u64.to_le_bytes());
    carrier.extend_from_slice(&(document.len() as u64).to_le_bytes());
    carrier.extend_from_slice(&document);
    if let Some(sealed) = sealed {
        carrier.extend_from_slice(sealed.as_bytes());
    }
    carrier
}

/// Knobs for the tampering negatives.
#[derive(Default, Clone)]
struct Mutations {
    /// Signed NOTICE bytes differ from the canonical ones.
    change_notice: bool,
    /// The sealed inventory marks the host non-executable.
    unmark_host: bool,
    /// The sealed inventory drops the NOTICE file.
    drop_notice: bool,
    /// The carrier does not assert the sealed digest although the file exists.
    omit_carrier_assertion: bool,
    /// The carrier asserts a digest, but no sealed document is shipped.
    omit_sealed_document: bool,
    /// The carrier asserts a different digest than the shipped document has.
    wrong_assertion: bool,
    /// The shipped host differs from the sealed inventory's hash.
    tamper_host_after_sealing: bool,
    /// A later policy generation that keeps this earlier release rollback-eligible.
    upgrade_from: Option<Digest>,
}

struct Package {
    root: PathBuf,
    descriptor: ReleaseDescriptor,
    proof: TrustProof,
    canonical: Vec<u8>,
    sealed: Vec<u8>,
    signed_host: Vec<u8>,
    unsigned_host: Vec<u8>,
}

fn build(temp: &Temp, name: &str, version: &str, mutations: &Mutations) -> Package {
    let root = temp.0.join("packages").join(name);
    let payload = root.join("payload");
    fs::create_dir_all(payload.join("licenses")).expect("payload tree");

    let unsigned_host = pe_image(format!("compiled host {version}").as_bytes());
    // Signing appends a certificate table and rewrites the checksum.
    let signed_host = pe_image(format!("compiled host {version} + signature").as_bytes());
    let signed_notice: Vec<u8> = if mutations.change_notice {
        b"licence notice (edited while signing)".to_vec()
    } else {
        NOTICE.to_vec()
    };

    let canonical_entries = [
        entry("host.exe", &unsigned_host, true),
        entry("licenses/NOTICE", NOTICE, false),
    ];
    let mut sealed_entries = vec![entry("host.exe", &signed_host, !mutations.unmark_host)];
    if !mutations.drop_notice {
        sealed_entries.push(entry("licenses/NOTICE", &signed_notice, false));
    }
    let canonical = write_inventory(&canonical_entries).expect("canonical inventory");
    let sealed = write_inventory(&sealed_entries).expect("sealed inventory");

    let on_disk_host = if mutations.tamper_host_after_sealing {
        pe_image(b"a different, attacker supplied host")
    } else {
        signed_host.clone()
    };
    fs::write(payload.join("host.exe"), &on_disk_host).expect("host payload");
    fs::write(payload.join("licenses/NOTICE"), &signed_notice).expect("notice payload");
    fs::write(root.join("inventory.json"), &canonical).expect("canonical document");
    if !mutations.omit_sealed_document {
        fs::write(root.join("sealed-inventory.json"), &sealed).expect("sealed document");
    }

    let descriptor = ReleaseDescriptor {
        schema: 1,
        channel: Channel::Production,
        version: version.to_owned(),
        source_commit: "0123456789abcdef0123456789abcdef01234567".to_owned(),
        target: Target::WindowsX64,
        minimum_os: "11".to_owned(),
        extension_id: EXTENSION_ID.to_owned(),
        host_name: PRODUCTION_HOST_NAME.to_owned(),
        bundle_id: PRODUCTION_HOST_NAME.to_owned(),
        api_min: 1,
        api_max: 2,
        // The unsigned canonical identity: fixed before signing.
        payload_id: Digest::of(&canonical),
        policy: ReleasePolicy {
            publishers: vec![PUBLISHER.to_owned()],
            denied_payloads: Vec::new(),
            rollback_payloads: vec![mutations.upgrade_from.unwrap_or(Digest::of(&canonical))],
            epoch: if mutations.upgrade_from.is_some() {
                2
            } else {
                1
            },
        },
    };
    descriptor.validate().expect("valid descriptor");

    let asserted = if mutations.omit_carrier_assertion {
        None
    } else if mutations.wrong_assertion {
        Some(Digest::of(b"some other sealed inventory"))
    } else {
        Some(Digest::of(&sealed))
    };
    let carrier = carrier_bytes(&descriptor, asserted);
    fs::write(root.join("carrier.exe"), &carrier).expect("carrier artifact");
    let proof = TrustProof::new(PUBLISHER, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, carrier)
        .expect("trust proof");

    Package {
        root,
        descriptor,
        proof,
        canonical,
        sealed,
        signed_host,
        unsigned_host,
    }
}

fn platform_for(package: &Package) -> FakePlatform {
    FakePlatform::with_faults(Faults {
        trust: Some(package.proof.clone()),
        host_state: HostState::Quiescent,
        ..Faults::default()
    })
}

fn authenticate(
    package: &Package,
) -> Result<veles_bridge_installer::AuthenticatedPackage, veles_bridge_installer::InstallFailure> {
    authenticate_package(&platform_for(package), &package.root, &package.descriptor)
}

fn windows_paths(temp: &Temp) -> InstallPaths {
    let home = temp.0.join("home");
    let local = temp.0.join("local");
    InstallPaths::for_user(Target::WindowsX64, Channel::Production, &home, Some(&local))
        .expect("injected Windows roots")
}

fn version_dir(paths: &InstallPaths, version: &str, payload_id: Digest) -> PathBuf {
    paths.version_dir(&Version::parse(version).expect("version"), payload_id)
}

#[test]
fn signed_host_authenticates_against_canonical_identity_and_sealed_bytes() {
    let temp = Temp::new();
    let package = build(&temp, "signed", "1.0.0", &Mutations::default());
    assert_ne!(
        package.unsigned_host, package.signed_host,
        "the fixture host really changed when it was signed"
    );

    let authenticated = authenticate(&package).expect("a correctly sealed package authenticates");
    // Identity is the unsigned canonical inventory ...
    assert_eq!(authenticated.payload_id(), Digest::of(&package.canonical));
    assert_eq!(authenticated.canonical_inventory(), package.canonical);
    // ... while the shipped files are the signed bytes, listed by the sealed one.
    assert_eq!(authenticated.sealed_inventory(), Some(&package.sealed[..]));
    let host = authenticated
        .payload()
        .iter()
        .find(|source| source.relative() == "host.exe")
        .expect("host entry");
    assert_eq!(host.sha256(), Digest::of(&package.signed_host));
    assert_ne!(host.sha256(), Digest::of(&package.unsigned_host));
}

#[test]
fn signed_package_installs_keeping_both_identities_distinct() {
    let temp = Temp::new();
    let paths = windows_paths(&temp);
    let package = build(&temp, "signed", "1.0.0", &Mutations::default());
    let authenticated = authenticate(&package).expect("authenticates");
    let platform = platform_for(&package);

    let report = install(&platform, &paths, authenticated).expect("a signed package installs");
    assert_eq!(report.active_payload, Some(Digest::of(&package.canonical)));

    let version = version_dir(&paths, "1.0.0", Digest::of(&package.canonical));
    assert_eq!(
        fs::read(version.join("host.exe")).expect("installed host"),
        package.signed_host,
        "the installed host is the signed file"
    );
    assert_eq!(
        fs::read(version.join("inventory.json")).expect("installed canonical inventory"),
        package.canonical,
        "inventory.json keeps the canonical identity the descriptor binds"
    );
    assert_eq!(
        fs::read(version.join("sealed-inventory.json")).expect("installed sealed inventory"),
        package.sealed
    );
}

#[test]
fn rollback_reverifies_a_sealed_release_across_both_identities() {
    let temp = Temp::new();
    let paths = windows_paths(&temp);
    let v1 = build(&temp, "v1", "1.0.0", &Mutations::default());
    let v2 = build(
        &temp,
        "v2",
        "2.0.0",
        &Mutations {
            upgrade_from: Some(v1.descriptor.payload_id),
            ..Mutations::default()
        },
    );
    install(
        &platform_for(&v1),
        &paths,
        authenticate(&v1).expect("v1 authenticates"),
    )
    .expect("v1 installs");
    install(
        &platform_for(&v2),
        &paths,
        authenticate(&v2).expect("v2 authenticates"),
    )
    .expect("v2 installs");

    let report = run_operation(&platform_for(&v1), &paths, Operation::Rollback, None)
        .expect("the retained signed release is re-verified and re-activated");
    assert_eq!(report.state, STATE_ROLLED_BACK);
    assert_eq!(report.active_payload, Some(v1.descriptor.payload_id));
}

#[test]
fn sealed_document_without_a_carrier_assertion_is_denied() {
    let temp = Temp::new();
    let package = build(
        &temp,
        "unasserted",
        "1.0.0",
        &Mutations {
            omit_carrier_assertion: true,
            ..Mutations::default()
        },
    );
    assert_eq!(
        authenticate(&package)
            .expect_err("unauthenticated sealed data")
            .code,
        FailureCode::TrustDenied
    );
}

#[test]
fn carrier_assertion_without_a_sealed_document_is_denied() {
    let temp = Temp::new();
    let package = build(
        &temp,
        "missing",
        "1.0.0",
        &Mutations {
            omit_sealed_document: true,
            ..Mutations::default()
        },
    );
    assert_eq!(
        authenticate(&package)
            .expect_err("asserted but absent")
            .code,
        FailureCode::TrustDenied
    );
}

#[test]
fn a_sealed_document_that_is_not_the_asserted_one_is_denied() {
    let temp = Temp::new();
    let package = build(
        &temp,
        "swapped",
        "1.0.0",
        &Mutations {
            wrong_assertion: true,
            ..Mutations::default()
        },
    );
    assert_eq!(
        authenticate(&package).expect_err("digest mismatch").code,
        FailureCode::TrustDenied
    );
}

#[test]
fn signing_may_only_change_signable_images() {
    let temp = Temp::new();
    for (name, mutations) in [
        (
            "notice-edited",
            Mutations {
                change_notice: true,
                ..Mutations::default()
            },
        ),
        (
            "host-unmarked",
            Mutations {
                unmark_host: true,
                ..Mutations::default()
            },
        ),
        (
            "notice-dropped",
            Mutations {
                drop_notice: true,
                ..Mutations::default()
            },
        ),
    ] {
        let package = build(&temp, name, "1.0.0", &mutations);
        assert_eq!(
            authenticate(&package)
                .expect_err("a self-consistent but non-corresponding sealed inventory")
                .code,
            FailureCode::TrustDenied,
            "{name}"
        );
    }
}

#[test]
fn shipped_bytes_must_match_the_sealed_hashes() {
    let temp = Temp::new();
    let package = build(
        &temp,
        "tampered",
        "1.0.0",
        &Mutations {
            tamper_host_after_sealing: true,
            ..Mutations::default()
        },
    );
    assert_eq!(
        authenticate(&package).expect_err("tampered host").code,
        FailureCode::TrustDenied
    );
}

#[test]
fn macos_signature_artifacts_are_the_only_sealed_only_paths() {
    let host = |path: &str, executable: bool| entry(path, b"x", executable);
    let canonical = [host("Veles.app/Contents/MacOS/host", true)];
    let sealed_ok = [
        host("Veles.app/Contents/MacOS/host", true),
        host("Veles.app/Contents/_CodeSignature/CodeResources", false),
        host("Veles.app/Contents/CodeResources", false),
    ];
    verify_correspondence(&canonical, &sealed_ok, |_| Ok(true))
        .expect("a bundle seal and its stapled ticket are signature artifacts");

    for (name, sealed) in [
        (
            "planted file",
            vec![
                host("Veles.app/Contents/MacOS/host", true),
                host("Veles.app/Contents/MacOS/extra", false),
            ],
        ),
        (
            "seal outside a bundle",
            vec![
                host("Veles.app/Contents/MacOS/host", true),
                host("Other/_CodeSignature/CodeResources", false),
            ],
        ),
        (
            "ticket without a seal",
            vec![
                host("Veles.app/Contents/MacOS/host", true),
                host("Veles.app/Contents/CodeResources", false),
            ],
        ),
        (
            "executable seal",
            vec![
                host("Veles.app/Contents/MacOS/host", true),
                host("Veles.app/Contents/_CodeSignature/CodeResources", true),
            ],
        ),
    ] {
        assert!(
            verify_correspondence(&canonical, &sealed, |_| Ok(true)).is_err(),
            "{name}"
        );
    }
}

#[test]
fn an_unchanged_package_needs_no_sealed_inventory() {
    // The legacy shape: nothing was rewritten by signing, so the shipped bytes are
    // the canonical inventory and no sealed document exists.
    let temp = Temp::new();
    let root = temp.0.join("plain");
    let payload = root.join("payload");
    fs::create_dir_all(&payload).expect("payload");
    fs::write(payload.join("host.exe"), b"plain host").expect("host");
    let canonical = write_inventory(&[entry("host.exe", b"plain host", true)]).expect("inventory");
    fs::write(root.join("inventory.json"), &canonical).expect("document");
    let descriptor = ReleaseDescriptor {
        payload_id: Digest::of(&canonical),
        ..build(&temp, "template", "1.0.0", &Mutations::default()).descriptor
    };
    let carrier = carrier_bytes(&descriptor, None);
    let proof =
        TrustProof::new(PUBLISHER, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, carrier).expect("proof");
    let platform = FakePlatform::with_faults(Faults {
        trust: Some(proof),
        ..Faults::default()
    });
    let package = authenticate_package(&platform, &root, &descriptor).expect("plain package");
    assert!(package.sealed_inventory().is_none());
    let _ = Path::new("unused");
}
