//! Unsupported and ambiguous transaction state (fix round F3).
//!
//! A journal this engine cannot interpret is either *unsupported* (written by a
//! future engine: a schema or phase it does not know) or *corrupt* (a torn or
//! foreign document). Neither may be released as "nothing was in flight" while
//! the layout still holds assets a transaction created.

use std::fs;
use std::path::PathBuf;
use std::process;
use std::sync::atomic::{AtomicU64, Ordering};

use veles_bridge_installer::{
    Channel, FailureCode, FakePlatform, Faults, HostState, InstallPaths, JournalState, Operation,
    Platform, ReceiptState, StateDocument, Target, recover, run_operation,
};
use veles_bridge_installer::{Journal, Receipt};

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct Temp(PathBuf);

impl Temp {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "veles-installer-recovery-state-{}-{}",
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

struct Layout {
    _temp: Temp,
    paths: InstallPaths,
    platform: FakePlatform,
}

fn layout() -> Layout {
    let temp = Temp::new();
    let home = temp.0.join("home");
    let local = temp.0.join("local");
    let paths =
        InstallPaths::for_user(Target::WindowsX64, Channel::Production, &home, Some(&local))
            .expect("injected Windows roots");
    let platform = FakePlatform::with_faults(Faults {
        host_state: HostState::Quiescent,
        ..Faults::default()
    });
    // Taking and dropping the lock creates the private layout.
    drop(platform.lock(&paths).expect("layout"));
    Layout {
        _temp: temp,
        paths,
        platform,
    }
}

fn journal_path(layout: &Layout) -> PathBuf {
    layout.paths.root().join("state/journal.json")
}

/// A staged-but-unpublished version directory: an asset a first install created.
fn stage_orphan_version(layout: &Layout) -> PathBuf {
    let version = layout.paths.versions().join("1.0.0-orphaned-payload");
    fs::create_dir_all(&version).expect("version dir");
    fs::write(version.join("host.exe"), b"staged host").expect("staged host");
    version
}

#[test]
fn a_future_journal_schema_is_unsupported_not_clean() {
    let layout = layout();
    let future = br#"{"schema":2,"transaction_id":"from a newer engine"}"#;
    fs::write(journal_path(&layout), future).expect("future journal");

    assert!(matches!(
        Journal::load(&layout.platform, &layout.paths).expect("classified"),
        JournalState::Unsupported
    ));
    let failure = recover(&layout.platform, &layout.paths).expect_err("never discarded");
    assert_eq!(failure.code, FailureCode::UnsupportedState);
    assert_eq!(
        fs::read(journal_path(&layout)).expect("journal kept"),
        future,
        "an older engine must leave a newer engine's pending transaction untouched"
    );
    assert!(
        !layout
            .paths
            .root()
            .join("state")
            .join(StateDocument::Journal.quarantine_file_name())
            .exists(),
        "nothing was quarantined"
    );
    assert!(layout.platform.mutations().is_empty());
}

#[test]
fn an_unknown_phase_is_unsupported_not_corrupt() {
    let layout = layout();
    let future = br#"{"schema":1,"phase":"sealing","transaction_id":"x"}"#;
    fs::write(journal_path(&layout), future).expect("future journal");
    assert!(matches!(
        Journal::load(&layout.platform, &layout.paths).expect("classified"),
        JournalState::Unsupported
    ));
    assert_eq!(
        recover(&layout.platform, &layout.paths)
            .expect_err("unknown phase")
            .code,
        FailureCode::UnsupportedState
    );
}

#[test]
fn every_mutating_operation_refuses_an_unsupported_journal() {
    let layout = layout();
    fs::write(journal_path(&layout), br#"{"schema":9}"#).expect("future journal");
    for operation in [Operation::Rollback, Operation::Uninstall] {
        let failure = run_operation(&layout.platform, &layout.paths, operation, None)
            .expect_err("an unsupported journal blocks the operation");
        assert_eq!(failure.code, FailureCode::UnsupportedState, "{operation:?}");
    }
    assert_eq!(
        fs::read(journal_path(&layout)).expect("journal kept"),
        br#"{"schema":9}"#
    );
}

#[test]
fn a_future_receipt_schema_is_unsupported() {
    let layout = layout();
    fs::write(
        layout.paths.root().join("state/receipt.json"),
        br#"{"schema":2}"#,
    )
    .expect("future receipt");
    assert!(matches!(
        Receipt::load(&layout.platform, &layout.paths).expect("classified"),
        ReceiptState::Unsupported
    ));
}

#[test]
fn a_corrupt_journal_beside_staged_assets_is_not_a_clean_layout() {
    let layout = layout();
    let version = stage_orphan_version(&layout);
    // A torn first-install journal: no receipt and no manifest exist yet, but the
    // transaction had already staged its payload.
    fs::write(journal_path(&layout), b"{\"schema\":1,\"transaction_id\":").expect("torn journal");

    let failure = recover(&layout.platform, &layout.paths)
        .expect_err("absence of registration is not proof that no assets exist");
    assert_eq!(failure.code, FailureCode::RecoveryRequired);
    assert!(
        version.join("host.exe").exists(),
        "the staged version is not orphaned or deleted on a guess"
    );
    assert!(
        journal_path(&layout).exists(),
        "the uninterpretable journal stays until the assets are reconciled"
    );
    assert!(layout.platform.mutations().is_empty());
}

#[test]
fn a_corrupt_journal_in_a_clean_layout_is_still_quarantined() {
    let layout = layout();
    fs::write(journal_path(&layout), b"not json at all").expect("torn journal");
    let report = recover(&layout.platform, &layout.paths)
        .expect("a torn journal with nothing outstanding must not block every run");
    assert_eq!(report.state, "clean");
    assert!(!journal_path(&layout).exists());
}
