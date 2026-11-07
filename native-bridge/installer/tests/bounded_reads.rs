//! Read-limit regression tests (fix round F4).
//!
//! Every untrusted-length read must be rejected from the length of the *opened
//! handle* (or a strict limit-plus-one read) before an oversized buffer exists.
//! An unbounded `read_to_end` also ends in a rejection - after buffering the whole
//! document - so the observable here is the largest single allocation the
//! operation requested, measured by the global allocation probe.

#[path = "support/alloc_probe.rs"]
mod alloc_probe;

use std::fs::{self, File};
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicU64, Ordering};

use veles_bridge_installer::{
    Channel, Digest, FailureCode, FakePlatform, Faults, InstallPaths, InventoryEntry, JournalState,
    MAX_DOCUMENT_BYTES, PRODUCTION_HOST_NAME, Platform, ReceiptState, ReleaseDescriptor,
    ReleasePolicy, StateDocument, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, Target, TrustProof,
    authenticate_package, canonical_json, write_carrier, write_inventory,
};
use veles_bridge_installer::{Journal, Receipt};

const EXTENSION_ID: &str = "mnopabcdefghijklmnopabcdefghijkl";
const PUBLISHER: &str = "Publisher One";
/// A sparse file this large costs nothing on disk but 64 MiB to buffer.
const OVERSIZE: u64 = 64 * 1024 * 1024;
/// Far below the oversize file, far above any legitimate bounded buffer here.
const ALLOCATION_CEILING: usize = 4 * 1024 * 1024;

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct Temp(PathBuf);

impl Temp {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "veles-installer-bounded-reads-{}-{}",
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

fn sparse(path: &Path, length: u64) {
    let file = File::create(path).expect("sparse file");
    file.set_len(length).expect("sparse length");
}

fn windows_paths(temp: &Temp) -> InstallPaths {
    let home = temp.0.join("home");
    let local = temp.0.join("local");
    InstallPaths::for_user(Target::WindowsX64, Channel::Production, &home, Some(&local))
        .expect("injected Windows roots")
}

fn descriptor(payload_id: Digest) -> ReleaseDescriptor {
    ReleaseDescriptor {
        schema: 1,
        channel: Channel::Production,
        version: "1.2.3".to_owned(),
        source_commit: "0123456789abcdef0123456789abcdef01234567".to_owned(),
        target: Target::WindowsX64,
        minimum_os: "11".to_owned(),
        extension_id: EXTENSION_ID.to_owned(),
        host_name: PRODUCTION_HOST_NAME.to_owned(),
        bundle_id: PRODUCTION_HOST_NAME.to_owned(),
        api_min: 1,
        api_max: 2,
        payload_id,
        policy: ReleasePolicy {
            publishers: vec![PUBLISHER.to_owned()],
            denied_payloads: Vec::new(),
            rollback_payloads: Vec::new(),
            epoch: 1,
        },
    }
}

#[test]
fn oversized_package_inventory_is_rejected_before_it_is_buffered() {
    let temp = Temp::new();
    let root = temp.0.join("package");
    let payload = root.join("payload");
    fs::create_dir_all(&payload).expect("payload");
    fs::write(payload.join("host.exe"), b"host").expect("host");
    // A valid, authenticated-looking carrier: only the inventory is hostile.
    let inventory = write_inventory(&[InventoryEntry {
        path: "host.exe".to_owned(),
        size: 4,
        sha256: Digest::of(b"host"),
        executable: true,
    }])
    .expect("inventory");
    let descriptor = descriptor(Digest::of(&inventory));
    let carrier = write_carrier(&canonical_json(&descriptor).expect("json")).expect("carrier");
    sparse(&root.join("inventory.json"), OVERSIZE);

    let proof =
        TrustProof::new(PUBLISHER, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, carrier).expect("proof");
    let platform = FakePlatform::with_faults(Faults {
        trust: Some(proof),
        ..Faults::default()
    });

    let (result, peak) =
        alloc_probe::largest_allocation(|| authenticate_package(&platform, &root, &descriptor));
    assert_eq!(
        result.expect_err("an oversized inventory is rejected").code,
        FailureCode::InvalidPackage
    );
    assert!(
        peak < ALLOCATION_CEILING,
        "an oversized inventory was buffered: largest allocation {peak} bytes"
    );
}

#[test]
fn oversized_state_documents_are_rejected_before_they_are_buffered() {
    let temp = Temp::new();
    let paths = windows_paths(&temp);
    let platform = FakePlatform::new();
    let _lock = platform.lock(&paths).expect("lock creates the layout");
    let state = paths.root().join("state");

    sparse(&state.join("journal.json"), OVERSIZE);
    sparse(&state.join("receipt.json"), OVERSIZE);

    let (bytes, peak) =
        alloc_probe::largest_allocation(|| platform.read_state(&paths, StateDocument::Journal));
    let bytes = bytes.expect("an oversized journal is readable as rejected state");
    assert!(
        bytes.is_none_or(|bytes| bytes.len() <= MAX_DOCUMENT_BYTES + 1),
        "read_state returned more than the strict limit plus one byte"
    );
    assert!(
        peak < ALLOCATION_CEILING,
        "an oversized journal was buffered: largest allocation {peak} bytes"
    );

    let (journal, peak) = alloc_probe::largest_allocation(|| Journal::load(&platform, &paths));
    assert!(matches!(
        journal.expect("load classifies"),
        JournalState::Unsupported
    ));
    assert!(peak < ALLOCATION_CEILING, "journal load buffered {peak}");

    let (receipt, peak) = alloc_probe::largest_allocation(|| Receipt::load(&platform, &paths));
    assert!(matches!(
        receipt.expect("load classifies"),
        ReceiptState::Unsupported
    ));
    assert!(peak < ALLOCATION_CEILING, "receipt load buffered {peak}");
}

#[test]
fn oversized_registration_manifest_is_rejected_before_it_is_buffered() {
    let temp = Temp::new();
    let paths = windows_paths(&temp);
    let platform = FakePlatform::new();
    let _lock = platform.lock(&paths).expect("lock creates the layout");
    sparse(paths.registration_path(), OVERSIZE);

    let (result, peak) = alloc_probe::largest_allocation(|| platform.read_registration(&paths));
    assert_eq!(
        result
            .expect_err("an oversized manifest is not a registration")
            .code,
        FailureCode::ForeignState
    );
    assert!(
        peak < ALLOCATION_CEILING,
        "an oversized registration was buffered: largest allocation {peak} bytes"
    );
}

#[test]
fn staging_reads_are_bound_to_the_authenticated_size() {
    let temp = Temp::new();
    let root = temp.0.join("package");
    let payload = root.join("payload");
    fs::create_dir_all(&payload).expect("payload");
    fs::write(payload.join("host.exe"), b"host").expect("host");
    let inventory = write_inventory(&[InventoryEntry {
        path: "host.exe".to_owned(),
        size: 4,
        sha256: Digest::of(b"host"),
        executable: true,
    }])
    .expect("inventory");
    fs::write(root.join("inventory.json"), &inventory).expect("inventory file");
    let descriptor = descriptor(Digest::of(&inventory));
    let carrier = write_carrier(&canonical_json(&descriptor).expect("json")).expect("carrier");
    let proof =
        TrustProof::new(PUBLISHER, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, carrier).expect("proof");
    let platform = FakePlatform::with_faults(Faults {
        trust: Some(proof),
        ..Faults::default()
    });
    let package = authenticate_package(&platform, &root, &descriptor).expect("authenticates");
    let source = &package.payload()[0];
    assert_eq!(source.read_verified().expect("intact source"), b"host");

    // The package directory is user-writable: the source grows after it was
    // authenticated and before staging reads it.
    sparse(&payload.join("host.exe"), OVERSIZE);
    let (result, peak) = alloc_probe::largest_allocation(|| source.read_verified());
    assert_eq!(
        result.expect_err("an enlarged source is refused").code,
        FailureCode::ForeignState
    );
    assert!(
        peak < ALLOCATION_CEILING,
        "an enlarged source was buffered: largest allocation {peak} bytes"
    );
}
