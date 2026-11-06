//! Bounded cleanup-capability protocol tests (OTP-25 Task 8).
//!
//! These exercise the production validation and deletion ordering
//! ([`veles_bridge_installer::cleanup::execute_cleanup`]) over a real temporary
//! root with a scripted inherited pipe. They are portable evidence: the request
//! framing, forged-channel/pid-reuse refusal, deadline handling, authenticated
//! completion and exact-identity removal run on this host. They are **not**
//! evidence for the Windows/macOS pipe-peer and self-signature APIs; those are
//! in `native_windows.rs`/`native_macos.rs` and are native-only.

use std::cell::Cell;
use std::fs;
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use veles_bridge_installer::cleanup::{
    CleanupChannel, CleanupCompletion, CleanupEntry, CleanupReason, CleanupRequest, CleanupStatus,
    ParentIdentity, ParentState, StdCleanupFs, encode_hex, execute_cleanup,
};
use veles_bridge_installer::{Digest, OwnedKind};

const PUBLISHER: &str = "team:ABCDE12345";
const TRANSACTION: [u8; 32] = [0x11; 32];
const CAPABILITY: [u8; 32] = [0x22; 32];

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

struct TempRoot {
    path: PathBuf,
}

impl TempRoot {
    fn new(tag: &str) -> Self {
        let unique = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "veles-installer-task8-cleanup-{tag}-{}-{unique}",
            process::id()
        ));
        if path.exists() {
            fs::remove_dir_all(&path).expect("stale temp root");
        }
        fs::create_dir_all(&path).expect("temp root");
        Self { path }
    }

    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TempRoot {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

/// The exact install root the cleanup request names.
fn install_root(temp: &TempRoot) -> PathBuf {
    temp.path().join("Veles").join("NativeBridge")
}

/// A private helper directory outside the removable root.
fn helper_path(temp: &TempRoot) -> PathBuf {
    temp.path()
        .join("private")
        .join("veles-cleanup")
        .join("cleanup.bin")
}

struct Fixture {
    root: PathBuf,
    helper: PathBuf,
    helper_sha256: Digest,
    entries: Vec<CleanupEntry>,
}

/// Create an owned tree plus the staged helper, and describe both exactly.
fn build_fixture(temp: &TempRoot) -> Fixture {
    let root = install_root(temp);
    fs::create_dir_all(root.join("versions/1.0.0-test")).expect("version dir");
    fs::write(root.join("versions/1.0.0-test/host.exe"), b"host-image").expect("host");
    fs::write(root.join("versions/1.0.0-test/data.bin"), b"owned-data").expect("data");
    let helper = helper_path(temp);
    fs::create_dir_all(helper.parent().expect("helper parent")).expect("helper dir");
    fs::write(&helper, b"signed-cleanup-helper").expect("helper");
    let entries = vec![
        entry("versions/1.0.0-test/host.exe", b"host-image"),
        entry("versions/1.0.0-test/data.bin", b"owned-data"),
        CleanupEntry {
            path: "versions/1.0.0-test".to_owned(),
            kind: OwnedKind::Directory,
            size: 0,
            sha256: None,
        },
        CleanupEntry {
            path: "versions".to_owned(),
            kind: OwnedKind::Directory,
            size: 0,
            sha256: None,
        },
    ];
    Fixture {
        root,
        helper,
        helper_sha256: Digest::of(b"signed-cleanup-helper"),
        entries,
    }
}

fn entry(path: &str, bytes: &[u8]) -> CleanupEntry {
    CleanupEntry {
        path: path.to_owned(),
        kind: OwnedKind::File,
        size: bytes.len() as u64,
        sha256: Some(Digest::of(bytes)),
    }
}

fn request(fixture: &Fixture) -> CleanupRequest {
    CleanupRequest {
        schema: 1,
        magic: "veles-cleanup-v1".to_owned(),
        transaction: encode_hex(&TRANSACTION),
        capability: encode_hex(&CAPABILITY),
        root: fixture.root.to_str().expect("utf-8 root").to_owned(),
        helper_path: fixture.helper.to_str().expect("utf-8 helper").to_owned(),
        helper_sha256: fixture.helper_sha256,
        publisher: PUBLISHER.to_owned(),
        parent: ParentIdentity {
            pid: 4242,
            start: 1_000_000,
        },
        deadline_ms: 2_000,
        entries: fixture.entries.clone(),
    }
}

fn frame(request: &CleanupRequest) -> Vec<u8> {
    serde_json::to_vec(request).expect("request frame")
}

struct ScriptedChannel {
    request: Vec<u8>,
    is_pipe: bool,
    peer: Option<ParentIdentity>,
    states: Vec<ParentState>,
    calls: Cell<usize>,
    written: Vec<u8>,
}

impl ScriptedChannel {
    fn new(request: Vec<u8>, parent: ParentIdentity, state: ParentState) -> Self {
        Self {
            request,
            is_pipe: true,
            peer: Some(parent),
            states: vec![state],
            calls: Cell::new(0),
            written: Vec::new(),
        }
    }
}

impl CleanupChannel for ScriptedChannel {
    fn is_inherited_pipe(&self) -> bool {
        self.is_pipe
    }

    fn peer(&self) -> Option<ParentIdentity> {
        self.peer
    }

    fn parent_state(&self, _parent: &ParentIdentity) -> ParentState {
        let index = self.calls.get();
        self.calls.set(index + 1);
        self.states
            .get(index)
            .copied()
            .unwrap_or_else(|| *self.states.last().expect("scripted states"))
    }

    fn read_request(&mut self, limit: usize) -> std::io::Result<Vec<u8>> {
        if self.request.len() > limit {
            return Err(std::io::Error::other("request frame is too large"));
        }
        Ok(self.request.clone())
    }

    fn write_completion(&mut self, frame: &[u8]) -> std::io::Result<()> {
        self.written.extend_from_slice(frame);
        Ok(())
    }
}

fn trusted_fs(fixture: &Fixture) -> StdCleanupFs {
    StdCleanupFs::trusted_fixture(fixture.helper.clone(), PUBLISHER)
}

/// Parse and authenticate the frame the helper wrote.
fn written_completion(channel: &ScriptedChannel) -> CleanupCompletion {
    let completion: CleanupCompletion =
        serde_json::from_slice(&channel.written).expect("completion frame");
    assert!(
        completion.verify(&CAPABILITY),
        "completion must be authenticated by the request capability"
    );
    assert_eq!(completion.transaction, encode_hex(&TRANSACTION));
    completion
}

#[test]
fn cleanup_rejects_forged_pipe_and_pid_reuse() {
    // 1. A request delivered on anything that is not an inherited pipe is
    //    refused, and nothing is removed.
    let temp = TempRoot::new("forged-pipe");
    let fixture = build_fixture(&temp);
    let forged = request(&fixture);
    let mut channel = ScriptedChannel::new(frame(&forged), forged.parent, ParentState::Exited);
    channel.is_pipe = false;
    let mut fs = trusted_fs(&fixture);
    let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_secs(1));
    assert_eq!(completion.status, CleanupStatus::CleanupPending);
    assert_eq!(completion.reason, Some(CleanupReason::ForgedChannel));
    assert_eq!(completion.removed_files, 0);
    assert!(fixture.root.join("versions/1.0.0-test/host.exe").exists());
    let framed = written_completion(&channel);
    assert_eq!(framed.reason, Some(CleanupReason::ForgedChannel));

    // 2. A pid that now belongs to a different process is not the parent: the
    //    peer identity (pid *and* start marker) must match exactly.
    let temp = TempRoot::new("pid-reuse");
    let fixture = build_fixture(&temp);
    let forged = request(&fixture);
    let mut channel = ScriptedChannel::new(frame(&forged), forged.parent, ParentState::Exited);
    channel.peer = Some(ParentIdentity {
        pid: forged.parent.pid,
        start: forged.parent.start + 1,
    });
    let mut fs = trusted_fs(&fixture);
    let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_secs(1));
    assert_eq!(completion.status, CleanupStatus::CleanupPending);
    assert_eq!(completion.reason, Some(CleanupReason::ForgedChannel));
    assert!(fixture.root.join("versions/1.0.0-test/host.exe").exists());

    // 3. A request whose root is not an installer-owned layout directory is
    //    invalid, whatever the channel looks like.
    let temp = TempRoot::new("foreign-root");
    let fixture = build_fixture(&temp);
    let mut forged = request(&fixture);
    forged.root = temp.path().to_str().expect("utf-8 root").to_owned();
    let mut channel = ScriptedChannel::new(frame(&forged), forged.parent, ParentState::Exited);
    let mut fs = trusted_fs(&fixture);
    let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_secs(1));
    assert_eq!(completion.status, CleanupStatus::CleanupPending);
    assert_eq!(completion.reason, Some(CleanupReason::InvalidRequest));
    assert!(fixture.root.join("versions/1.0.0-test/host.exe").exists());

    // 4. Once the peer matches, a *recycled* pid observed while waiting means
    //    the recorded parent exited, so cleanup proceeds instead of waiting on
    //    a process that is not the parent.
    let temp = TempRoot::new("reused-while-waiting");
    let fixture = build_fixture(&temp);
    let valid = request(&fixture);
    let mut channel = ScriptedChannel::new(frame(&valid), valid.parent, ParentState::Reused);
    let mut fs = trusted_fs(&fixture);
    let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_secs(1));
    assert_eq!(completion.status, CleanupStatus::Complete);
    assert_eq!(completion.removed_files, 2);
    assert_eq!(completion.removed_directories, 2);
    assert!(!fixture.root.join("versions/1.0.0-test/host.exe").exists());
    assert!(!fixture.root.join("versions").exists());
    assert!(!fixture.helper.exists(), "the helper deletes itself");
    let framed = written_completion(&channel);
    assert_eq!(framed.status, CleanupStatus::Complete);
}

#[test]
fn cleanup_timeout_never_reports_uninstall_complete() {
    let temp = TempRoot::new("timeout");
    let fixture = build_fixture(&temp);
    let valid = request(&fixture);
    // The parent never exits: the helper must report pending, remove nothing and
    // still return within the bound.
    let mut channel = ScriptedChannel::new(frame(&valid), valid.parent, ParentState::Alive);
    let mut fs = trusted_fs(&fixture);
    let started = Instant::now();
    let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_millis(120));
    let elapsed = started.elapsed();
    assert_eq!(completion.status, CleanupStatus::CleanupPending);
    assert_ne!(completion.status, CleanupStatus::Complete);
    assert_eq!(completion.reason, Some(CleanupReason::ParentAlive));
    assert_eq!(completion.removed_files, 0);
    assert_eq!(completion.removed_directories, 0);
    assert!(
        fixture.root.join("versions/1.0.0-test/host.exe").exists(),
        "a pending cleanup must not remove owned files"
    );
    assert!(
        elapsed < Duration::from_secs(2),
        "the wait is bounded by the request deadline, not the 10s ceiling"
    );
    let framed = written_completion(&channel);
    assert_eq!(framed.status, CleanupStatus::CleanupPending);
    assert_eq!(framed.reason, Some(CleanupReason::ParentAlive));
}

#[test]
fn cleanup_preserves_a_foreign_identity_and_reports_pending() {
    let temp = TempRoot::new("preserve");
    let fixture = build_fixture(&temp);
    // The exact bytes the receipt claimed are gone; the replacement is foreign
    // and must be preserved, with the overall cleanup reported as pending.
    fs::write(
        fixture.root.join("versions/1.0.0-test/data.bin"),
        b"replaced",
    )
    .expect("replace");
    let valid = request(&fixture);
    let mut channel = ScriptedChannel::new(frame(&valid), valid.parent, ParentState::Exited);
    let mut fs = trusted_fs(&fixture);
    let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_secs(1));
    assert_eq!(completion.status, CleanupStatus::CleanupPending);
    assert_eq!(completion.reason, Some(CleanupReason::Leftover));
    assert!(completion.preserved >= 1);
    assert!(
        fixture.root.join("versions/1.0.0-test/data.bin").exists(),
        "a mismatched identity is never deleted"
    );
    // The directory that still holds the preserved file is preserved too.
    assert!(fixture.root.join("versions/1.0.0-test").exists());
}

#[test]
fn cleanup_rejects_a_replaced_helper_image() {
    let temp = TempRoot::new("self");
    let fixture = build_fixture(&temp);
    let mut valid = request(&fixture);
    valid.helper_sha256 = Digest::of(b"some other binary");
    let mut channel = ScriptedChannel::new(frame(&valid), valid.parent, ParentState::Exited);
    let mut fs = trusted_fs(&fixture);
    let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_secs(1));
    assert_eq!(completion.status, CleanupStatus::CleanupPending);
    assert_eq!(completion.reason, Some(CleanupReason::SelfMismatch));
    assert_eq!(completion.removed_files, 0);
    assert!(fixture.root.join("versions/1.0.0-test/host.exe").exists());
    assert!(fixture.helper.exists());
}
