//! Bounded cleanup-capability protocol tests (OTP-25 Task 8).
//!
//! These exercise the production validation and deletion ordering
//! (`veles_bridge_installer::cleanup::execute_cleanup`) over a real temporary
//! root with a scripted inherited pipe. They are portable evidence: the request
//! framing, forged-channel/pid-reuse refusal, deadline handling, authenticated
//! completion, anchored relative removal and the installer-side helper-image
//! removal run on this host. They are **not** evidence for the Windows/macOS
//! pipe-peer and self-signature APIs or for the OS-level anchored traversal;
//! those live in the native adapters and in `tests/native_*.rs`.

use std::cell::Cell;
use std::fs;
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use veles_bridge_installer::cleanup::{
    CleanupChannel, CleanupCompletion, CleanupEntry, CleanupReason, CleanupRequest, CleanupStatus,
    HelperImage, ParentIdentity, ParentState, SignaturePolicy, StdCleanupFs, encode_hex,
    execute_cleanup, remove_helper_after_exit,
};
use veles_bridge_installer::{Digest, OwnedKind};

const PUBLISHER: &str = "team:ABCDE12345";
const TRANSACTION: [u8; 32] = [0x11; 32];
const CAPABILITY: [u8; 32] = [0x22; 32];
/// The inherited parent-process handle value the request names.
const PARENT_HANDLE: u64 = 3;

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
        parent_handle: PARENT_HANDLE,
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
    /// The inherited handle value this double accepts as the parent.
    handle: u64,
    state: ParentState,
    calls: Cell<usize>,
    read_deadline: Cell<Option<Instant>>,
    written: Vec<u8>,
}

impl ScriptedChannel {
    fn new(request: Vec<u8>, parent: ParentIdentity, state: ParentState) -> Self {
        Self {
            request,
            is_pipe: true,
            peer: Some(parent),
            handle: PARENT_HANDLE,
            state,
            calls: Cell::new(0),
            read_deadline: Cell::new(None),
            written: Vec::new(),
        }
    }
}

impl CleanupChannel for ScriptedChannel {
    fn is_inherited_pipe(&self) -> bool {
        self.is_pipe
    }

    fn parent_handle(&self, handle: u64) -> Option<ParentIdentity> {
        (handle == self.handle).then_some(self.peer).flatten()
    }

    fn parent_state(&mut self, _handle: u64, deadline: Instant) -> ParentState {
        self.calls.set(self.calls.get() + 1);
        match self.state {
            // A real handle blocks until it signals or the deadline elapses.
            ParentState::Alive => {
                std::thread::sleep(deadline.saturating_duration_since(Instant::now()));
                ParentState::Alive
            }
            other => other,
        }
    }

    fn read_request(&mut self, limit: usize, deadline: Instant) -> std::io::Result<Vec<u8>> {
        self.read_deadline.set(Some(deadline));
        if self.request.len() > limit {
            return Err(std::io::Error::other("request frame is too large"));
        }
        Ok(self.request.clone())
    }

    fn write_completion(&mut self, frame: &[u8], deadline: Instant) -> std::io::Result<()> {
        if Instant::now() >= deadline {
            return Err(std::io::Error::other("cleanup deadline elapsed"));
        }
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

    // 2. An inherited handle that names a different process (a reused pid, or a
    //    handle for somebody else) is not the parent: pid *and* start marker must
    //    match the handle's identity.
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

    // 2b. A request that names a handle the process does not actually hold is
    //     refused the same way: the handle value is authenticated, never trusted.
    let mut forged = request(&fixture);
    forged.parent_handle = PARENT_HANDLE + 1;
    let mut channel = ScriptedChannel::new(frame(&forged), forged.parent, ParentState::Exited);
    let mut fs = trusted_fs(&fixture);
    let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_secs(1));
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

    // 4. A legitimate cleanup is not rejected because the parent exited after it
    //    signalled: the inherited handle reports `Exited` and removal proceeds.
    let temp = TempRoot::new("exited-after-signalling");
    let fixture = build_fixture(&temp);
    let valid = request(&fixture);
    let mut channel = ScriptedChannel::new(frame(&valid), valid.parent, ParentState::Exited);
    let mut fs = trusted_fs(&fixture);
    let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_secs(1));
    assert_eq!(completion.status, CleanupStatus::Complete);
    assert_eq!(completion.removed_files, 2);
    assert_eq!(completion.removed_directories, 2);
    assert!(!fixture.root.join("versions/1.0.0-test/host.exe").exists());
    assert!(!fixture.root.join("versions").exists());
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

/// The whole run is bounded to ten seconds even when the caller asks for longer:
/// the frame read already receives the clamped deadline.
#[test]
fn cleanup_is_bounded_to_ten_seconds() {
    let temp = TempRoot::new("bounded");
    let fixture = build_fixture(&temp);
    let valid = request(&fixture);
    let mut channel = ScriptedChannel::new(frame(&valid), valid.parent, ParentState::Exited);
    let started = Instant::now();
    let mut fs = trusted_fs(&fixture);
    let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_secs(60));
    assert_eq!(
        completion.status,
        CleanupStatus::Complete,
        "fixture is removable"
    );
    let read_deadline = channel.read_deadline.get().expect("read deadline was set");
    assert!(
        read_deadline <= started + Duration::from_secs(10) + Duration::from_millis(250),
        "the request read must be bounded by the ten-second ceiling"
    );
    // A request that asks for a longer deadline is invalid, not clamped silently.
    let mut long = request(&fixture);
    let temp = TempRoot::new("bounded-request");
    let fixture = build_fixture(&temp);
    long.root = fixture.root.to_str().expect("utf-8 root").to_owned();
    long.helper_path = fixture.helper.to_str().expect("utf-8 helper").to_owned();
    long.helper_sha256 = fixture.helper_sha256;
    long.entries = fixture.entries.clone();
    long.deadline_ms = 60_000;
    let mut channel = ScriptedChannel::new(frame(&long), long.parent, ParentState::Exited);
    let mut fs = trusted_fs(&fixture);
    let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_secs(1));
    assert_eq!(completion.reason, Some(CleanupReason::InvalidRequest));
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

/// A pre-existing link at a component of the root must not redirect a deletion.
///
/// The portable reference refuses to traverse any linked component; the native
/// adapters hold an opened directory handle and cannot be redirected at all.
#[test]
fn cleanup_never_traverses_a_linked_root_component() {
    #[cfg(unix)]
    {
        let temp = TempRoot::new("linked-root");
        let fixture = build_fixture(&temp);
        // Replace the whole owned tree with a link to a tree outside the install
        // root that contains a file with exactly the requested identity.
        let outside = temp.path().join("outside");
        fs::create_dir_all(outside.join("1.0.0-test")).expect("outside tree");
        fs::write(outside.join("1.0.0-test/host.exe"), b"host-image").expect("outside host");
        fs::remove_dir_all(fixture.root.join("versions")).expect("drop owned tree");
        std::os::unix::fs::symlink(&outside, fixture.root.join("versions")).expect("planted link");

        let valid = request(&fixture);
        let mut channel = ScriptedChannel::new(frame(&valid), valid.parent, ParentState::Exited);
        let mut fs = trusted_fs(&fixture);
        let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_secs(1));
        assert_eq!(completion.status, CleanupStatus::CleanupPending);
        assert!(
            outside.join("1.0.0-test/host.exe").exists(),
            "a deletion must never be redirected through a linked component"
        );
        assert!(
            fs::symlink_metadata(fixture.root.join("versions")).is_ok(),
            "the planted link is preserved, not treated as an owned directory"
        );
    }
    #[cfg(not(unix))]
    {
        // Symbolic links are a unix fixture capability here; the native Windows
        // adapter refuses any reparse point with `UnsafePath` (see the native
        // test), so there is no portable equivalent on a non-unix host.
    }
}

/// The helper never deletes its own image; the installer does, after exit.
#[test]
fn cleanup_leaves_the_helper_image_for_the_installer() {
    let temp = TempRoot::new("helper-image");
    let fixture = build_fixture(&temp);
    let valid = request(&fixture);
    let mut channel = ScriptedChannel::new(frame(&valid), valid.parent, ParentState::Exited);
    let mut fs = trusted_fs(&fixture);
    let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_secs(1));
    assert_eq!(completion.status, CleanupStatus::Complete);
    assert!(
        fixture.helper.exists(),
        "the helper only exits: its image is removed by the installer"
    );
}

struct ScriptedImage {
    path: PathBuf,
    remove_fails: bool,
}

impl HelperImage for ScriptedImage {
    fn remove(&mut self, _path: &Path) -> std::io::Result<()> {
        if self.remove_fails {
            return Err(std::io::Error::other("image is still mapped"));
        }
        match fs::remove_file(&self.path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error),
        }
    }

    fn exists(&self, path: &Path) -> std::io::Result<bool> {
        match fs::symlink_metadata(path) {
            Ok(_) => Ok(true),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
            Err(error) => Err(error),
        }
    }
}

/// A helper image the installer cannot delete is a pending cleanup, never a
/// completed uninstall — and a successful deletion must be observed, not assumed.
#[test]
fn cleanup_reports_pending_when_the_helper_image_cannot_be_removed() {
    let temp = TempRoot::new("pending-image");
    let fixture = build_fixture(&temp);
    let valid = request(&fixture);
    let mut channel = ScriptedChannel::new(frame(&valid), valid.parent, ParentState::Exited);
    let mut fs = trusted_fs(&fixture);
    let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_secs(1));
    assert_eq!(completion.status, CleanupStatus::Complete);

    // Deletion refused by the OS: pending, and the image is still there.
    let mut failing = ScriptedImage {
        path: fixture.helper.clone(),
        remove_fails: true,
    };
    let failure = remove_helper_after_exit(
        &mut failing,
        &fixture.helper,
        &completion,
        &CAPABILITY,
        &encode_hex(&TRANSACTION),
    )
    .expect_err("an undeletable helper image is a pending cleanup");
    assert_eq!(
        failure.code,
        veles_bridge_installer::FailureCode::CleanupPending
    );
    assert!(fixture.helper.exists());

    // Deletion succeeds but the path is still present: also pending.
    struct Vanishing {
        path: PathBuf,
    }
    impl HelperImage for Vanishing {
        fn remove(&mut self, _path: &Path) -> std::io::Result<()> {
            Ok(())
        }
        fn exists(&self, _path: &Path) -> std::io::Result<bool> {
            Ok(self.path.exists())
        }
    }
    let mut vanishing = Vanishing {
        path: fixture.helper.clone(),
    };
    assert_eq!(
        remove_helper_after_exit(
            &mut vanishing,
            &fixture.helper,
            &completion,
            &CAPABILITY,
            &encode_hex(&TRANSACTION),
        )
        .expect_err("an image that is still present is not removed")
        .code,
        veles_bridge_installer::FailureCode::CleanupPending
    );

    // Real deletion removes the image and reports success.
    let mut working = ScriptedImage {
        path: fixture.helper.clone(),
        remove_fails: false,
    };
    remove_helper_after_exit(
        &mut working,
        &fixture.helper,
        &completion,
        &CAPABILITY,
        &encode_hex(&TRANSACTION),
    )
    .expect("the installer removes the staged helper image");
    assert!(!fixture.helper.exists());

    // A pending completion can never remove the image.
    let mut pending = CleanupCompletion {
        schema: 1,
        magic: "veles-cleanup-v1".to_owned(),
        transaction: completion.transaction.clone(),
        status: CleanupStatus::CleanupPending,
        reason: Some(CleanupReason::Leftover),
        removed_files: 0,
        removed_directories: 0,
        preserved: 1,
        mac: None,
    };
    pending.status = CleanupStatus::CleanupPending;
    assert_eq!(
        remove_helper_after_exit(
            &mut working,
            &fixture.helper,
            &pending,
            &CAPABILITY,
            &encode_hex(&TRANSACTION),
        )
        .expect_err("a pending completion never removes the image")
        .code,
        veles_bridge_installer::FailureCode::CleanupPending
    );
}

/// The signature policy is closed: without an OS assessment a run fails closed.
#[test]
fn cleanup_without_signature_evidence_fails_closed() {
    let temp = TempRoot::new("signature");
    let fixture = build_fixture(&temp);
    let valid = request(&fixture);
    let mut channel = ScriptedChannel::new(frame(&valid), valid.parent, ParentState::Exited);
    let mut fs = StdCleanupFs::new(fixture.helper.clone());
    assert_eq!(fs.signature_policy(), SignaturePolicy::Unavailable);
    let completion = execute_cleanup(&mut channel, &mut fs, Duration::from_secs(1));
    assert_eq!(completion.status, CleanupStatus::CleanupPending);
    assert_eq!(completion.reason, Some(CleanupReason::SelfMismatch));
    assert!(fixture.root.join("versions/1.0.0-test/host.exe").exists());
}
