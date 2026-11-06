//! Bounded, single-use cleanup capability for owned-file removal (Task 8).
//!
//! Deleting the last files of an installation while the installer itself may
//! still hold handles cannot be done safely in-process. Instead the installer
//! stages a **signed cleanup helper** into a private temporary directory outside
//! the removable root, hands it an inherited request pipe, an inherited
//! completion pipe and an inherited **parent-process handle**, then exits. The
//! helper:
//!
//! 1. reads one bounded request frame from the inherited pipe, with a deadline
//!    (never a path, a recursive delete target or a command line);
//! 2. refuses a channel that is not an inherited OS pipe, or whose inherited
//!    parent handle does not name the process the request records (a pid is not
//!    an identity: the handle's process id *and* start marker must match, so a
//!    reused pid is rejected);
//! 3. verifies its own absolute path, its own image digest and its own OS
//!    signature against the request's pinned publisher *before* touching
//!    anything, with a bounded hash and a bounded signature assessment;
//! 4. waits **on that inherited handle** - not by reopening a pid - until the
//!    parent *releases the capability*, bounded by [`MAX_CLEANUP_TIMEOUT`]
//!    (10s). The installer writes one release byte as its last act before it
//!    waits, and a parent that exits (or crashes) closes the channel, which the
//!    helper observes as EOF; on macOS the same socketpair carries the peer
//!    identity and the release. The helper therefore does **not** require the
//!    installer to exit first: the installer writes the request, releases, then
//!    waits for the helper ([`run_cleanup_helper`]);
//! 5. opens the install root as an anchored traversal root and deletes only the
//!    exact listed owned identities **relative to that handle**: every component
//!    is opened without following links, the leaf's opened identity is compared
//!    with the one that was hashed, and only then is that exact object unlinked.
//!    A pre-existing link at `root/versions` therefore cannot redirect a
//!    deletion, and a leaf swapped between hashing and unlinking is preserved.
//!    A missing entry is idempotent, a mismatched entry is preserved, and
//!    directories are removed only when empty (there is no recursive delete
//!    anywhere in this module);
//! 6. writes one authenticated completion frame (HMAC-SHA256 keyed by the
//!    request's single-use capability) with a write deadline and exits non-zero
//!    unless the status is `complete`.
//!
//! The helper never deletes its own image. It only exits; the installer entry
//! point that spawned it ([`run_cleanup_helper`]) waits for the authenticated
//! completion **and** the process exit, deletes the staged image
//! ([`remove_helper_after_exit`]), verifies that the path is gone and only then
//! reports success. If any of those steps fails, the result is
//! `CleanupPending`, never a completed uninstall.
//!
//! Nothing here terminates a process, invokes a shell, registers an autostart
//! task or falls back to reboot-time deletion.

use std::fmt;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

use crate::inventory::normalize_relative_path;
use crate::model::{Digest, FailureCode, InstallFailure, InstallResult, MAX_PATH_BYTES};
use crate::paths::{MAX_ROOT_BYTES, ROOT_DIR_DEVELOPMENT, ROOT_DIR_FIXTURE, ROOT_DIR_PRODUCTION};
use crate::platform::OwnedKind;

/// Completed cleanup frames are this schema.
pub const CLEANUP_SCHEMA_VERSION: u32 = 1;
/// Wire magic that identifies this protocol generation.
pub const CLEANUP_MAGIC: &str = "veles-cleanup-v1";
/// Hard ceiling for one cleanup run, from the first byte read to the last byte
/// written. Nothing in this module may extend it.
pub const MAX_CLEANUP_TIMEOUT: Duration = Duration::from_secs(10);
/// Bound on one request/completion frame.
pub const MAX_CLEANUP_FRAME_BYTES: usize = 256 * 1024;
/// Bound on the entries one request may name.
pub const MAX_CLEANUP_ENTRIES: usize = 4096;
/// Bound on the capability/transaction byte strings (hex-encoded 32 bytes).
const CAPABILITY_BYTES: usize = 32;
/// Bounded grace for the one completion write after the work deadline elapsed.
///
/// The work deadline (the request's, or the fixed ceiling) bounds waiting and
/// removal; a run that has to report `ParentAlive` or `Deadline` must still be
/// able to deliver that verdict. The grace is itself bounded and never extends
/// past [`MAX_CLEANUP_TIMEOUT`].
const COMPLETION_WRITE_GRACE: Duration = Duration::from_millis(500);

/// The identity of one OS process: a pid is not enough, because the OS recycles
/// it. `start` is the OS start marker for that pid.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ParentIdentity {
    /// Process identifier.
    pub pid: u32,
    /// OS start marker in the adapter's units (creation time / bsd starttime).
    pub start: u64,
}

/// One exact owned entry the helper may remove.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CleanupEntry {
    /// `/`-joined path below the install root.
    pub path: String,
    /// File, directory or symbolic link.
    pub kind: OwnedKind,
    /// Exact size for a file *and* for a link (the target's byte length); zero
    /// for a directory.
    pub size: u64,
    /// Exact SHA-256 for a file (its content) or for a link (its target bytes);
    /// absent for a directory.
    pub sha256: Option<Digest>,
}

/// The bounded request frame the installer writes into the inherited pipe.
///
/// It names owned identities only. There is no recursion, no wildcard, no
/// absolute entry path and no command to run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CleanupRequest {
    /// [`CLEANUP_SCHEMA_VERSION`].
    pub schema: u32,
    /// [`CLEANUP_MAGIC`].
    pub magic: String,
    /// Hex transaction id (32 bytes) the completion is bound to.
    pub transaction: String,
    /// Single-use secret key for completion authentication (hex 32 bytes).
    pub capability: String,
    /// Absolute install root the entries are relative to.
    pub root: String,
    /// Absolute path the helper image must have.
    pub helper_path: String,
    /// Exact SHA-256 of the helper image.
    pub helper_sha256: Digest,
    /// Pinned OS publisher/signer identity of the helper.
    pub publisher: String,
    /// The installer process that spawned the helper.
    pub parent: ParentIdentity,
    /// The **inherited handle** that names that exact parent process.
    ///
    /// Windows: an inherited `DuplicateHandle`d process handle with
    /// `PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE`. macOS: an inherited
    /// descriptor of a `socketpair` whose peer is the parent (`LOCAL_PEERPID`),
    /// which also carries the release below. The helper authenticates the peer
    /// over this handle, so it never reopens a pid.
    pub parent_handle: u64,
    /// The inherited **release** handle: Windows only, `0` on macOS.
    ///
    /// The installer holds the write end of a pipe whose read end is this handle,
    /// writes **one byte** to it as its last act before it waits for the helper,
    /// and never writes again. A byte (or a broken pipe, meaning the parent
    /// exited) is "the parent will not mutate anything again", which is what the
    /// helper waits for. A request naming a handle the process does not hold, or
    /// the wrong kind of object, is `ParentUnavailable` (never a silent proceed).
    /// On macOS the socketpair named by `parent_handle` is both the identity and
    /// the release channel (one byte on it is the release), so this field must be
    /// `0`.
    pub release_handle: u64,
    /// Deadline in milliseconds, clamped to [`MAX_CLEANUP_TIMEOUT`].
    pub deadline_ms: u64,
    /// Exact owned entries, leaves first and directories deepest-first.
    pub entries: Vec<CleanupEntry>,
}

/// The outcome the helper reports.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum CleanupStatus {
    /// Every listed identity was removed (or already absent) and the parent
    /// exited.
    Complete,
    /// Something could not be established; the installer must keep reporting a
    /// pending cleanup and never a completed uninstall.
    CleanupPending,
}

/// Why a run did not complete.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum CleanupReason {
    /// The request frame was unreadable, oversized or invalid.
    InvalidRequest,
    /// The channel is not an inherited pipe, or its inherited parent handle does
    /// not name the recorded parent (including a reused pid).
    ForgedChannel,
    /// The helper's own path, digest or signature did not match the request (or
    /// could not be verified inside the deadline).
    SelfMismatch,
    /// The parent was still alive at the deadline.
    ParentAlive,
    /// The inherited parent handle could not be used.
    ParentUnavailable,
    /// The install root could not be opened as an anchored traversal root.
    RootUnavailable,
    /// The deadline elapsed while removing entries.
    Deadline,
    /// One or more entries were absent/did not match and were preserved.
    Leftover,
    /// The completion could not be written over the channel.
    ChannelFailure,
}

/// One authenticated completion frame.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CleanupCompletion {
    /// [`CLEANUP_SCHEMA_VERSION`].
    pub schema: u32,
    /// [`CLEANUP_MAGIC`].
    pub magic: String,
    /// Echo of the request transaction id.
    pub transaction: String,
    /// The verdict; only `Complete` may be shown as a completed uninstall.
    pub status: CleanupStatus,
    /// The reason when the status is `CleanupPending`.
    pub reason: Option<CleanupReason>,
    /// Files actually removed (or already absent).
    pub removed_files: u32,
    /// Empty directories actually removed (or already absent).
    pub removed_directories: u32,
    /// Entries left because their identity did not match.
    pub preserved: u32,
    /// HMAC-SHA256 over [`CleanupCompletion::mac_input`], keyed by the request
    /// capability. `None` only when the request could not be parsed.
    pub mac: Option<String>,
}

impl CleanupCompletion {
    fn new(transaction: &str) -> Self {
        Self {
            schema: CLEANUP_SCHEMA_VERSION,
            magic: CLEANUP_MAGIC.to_owned(),
            transaction: transaction.to_owned(),
            status: CleanupStatus::CleanupPending,
            reason: None,
            removed_files: 0,
            removed_directories: 0,
            preserved: 0,
            mac: None,
        }
    }

    fn pending(transaction: &str, reason: CleanupReason) -> Self {
        let mut completion = Self::new(transaction);
        completion.reason = Some(reason);
        completion
    }

    /// The exact bytes the MAC covers.
    ///
    /// A deterministic, field-ordered encoding is used instead of the JSON frame
    /// so a parent never has to re-serialize an untrusted document to check it.
    pub fn mac_input(&self) -> Vec<u8> {
        let status = match self.status {
            CleanupStatus::Complete => "complete",
            CleanupStatus::CleanupPending => "cleanup-pending",
        };
        let reason = self
            .reason
            .map(|reason| format!("{reason:?}"))
            .unwrap_or_default();
        format!(
            "{CLEANUP_MAGIC}\n{}\n{status}\n{reason}\n{}\n{}\n{}\n",
            self.transaction, self.removed_files, self.removed_directories, self.preserved,
        )
        .into_bytes()
    }

    /// Whether `mac` authenticates this completion under `capability`.
    pub fn verify(&self, capability: &[u8]) -> bool {
        self.mac
            .as_deref()
            .and_then(decode_hex_32)
            .is_some_and(|mac| mac[..] == hmac_sha256(capability, &self.mac_input())[..])
    }
}

/// What the inherited parent handle proves about the recorded parent.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ParentEvidence {
    /// The handle names exactly this process: its pid **and** its start marker
    /// were read from the handle itself.
    Exact(ParentIdentity),
    /// The handle names this pid, but the process has already exited, so its
    /// start marker can no longer be read from the OS. This is accepted only when
    /// the pid equals the request's parent pid: the pid comes from the OS for the
    /// inherited handle (never from the request), and a *live* process with that
    /// pid is reported as [`ParentEvidence::Exact`] and compared by start marker,
    /// so a recycled pid cannot be accepted here.
    Exited { pid: u32 },
}

/// The state of the capability's parent side.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ParentState {
    /// The parent has not released the capability yet; the helper must keep
    /// waiting (it is alive, or it holds the release handle open).
    Alive,
    /// The parent released the capability: it closed the inherited release
    /// channel as its last act, or the process exited.
    Exited,
    /// The OS would not say; the helper must not act.
    Unavailable,
}

/// The inherited channel the helper reads its request from and writes the
/// completion to.
///
/// The native implementations also prove that this is an OS pipe and who the
/// peer is; a test double scripts the same facts.
pub trait CleanupChannel {
    /// Whether both ends are inherited anonymous/named pipe handles rather than
    /// a regular file, console or other redirect.
    fn is_inherited_pipe(&self) -> bool;
    /// What the **inherited parent handle** proves about the recorded parent,
    /// when the OS reports anything at all.
    fn parent_handle(&self, handle: u64) -> Option<ParentEvidence>;
    /// Block until the parent releases the capability (`parent_handle` or
    /// `release_handle` signals, or the process exits), or until `deadline`;
    /// `Alive` is returned only when the deadline elapsed.
    fn parent_state(
        &mut self,
        parent_handle: u64,
        release_handle: u64,
        deadline: Instant,
    ) -> ParentState;
    /// Read one bounded request frame; must stop at the frame bound *and* at the
    /// deadline.
    fn read_request(&mut self, limit: usize, deadline: Instant) -> io::Result<Vec<u8>>;
    /// Write one bounded completion frame; must stop at the deadline.
    fn write_completion(&mut self, frame: &[u8], deadline: Instant) -> io::Result<()>;
}

/// The result of removing one exact entry.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RemoveOutcome {
    /// Removed now.
    Removed,
    /// Already absent (idempotent).
    Absent,
    /// Present but not the exact owned identity: preserved, never deleted.
    Preserved,
    /// The run deadline elapsed before the work could finish: the entry was not
    /// (completely) examined, so nothing is claimed about it.
    Deferred,
}

/// The install root, opened once as an anchored traversal root.
///
/// Every relative removal is performed component by component from this handle
/// with no-follow opens, and the leaf that is unlinked is the one whose identity
/// was hashed. A path is never rebuilt and handed to a path-based unlink.
pub trait CleanupTree {
    /// Remove one regular file with exactly this size and content digest.
    ///
    /// Hashing must stop at `deadline` and return [`RemoveOutcome::Deferred`]: a
    /// single very large file may not extend the run past its bound.
    fn remove_file(
        &mut self,
        relative: &str,
        size: u64,
        sha256: Digest,
        deadline: Instant,
    ) -> RemoveOutcome;
    /// Remove one symbolic link whose target bytes have this size and digest. The
    /// target is never followed.
    fn remove_link(
        &mut self,
        relative: &str,
        size: u64,
        sha256: Digest,
        deadline: Instant,
    ) -> RemoveOutcome;
    /// Remove one directory only when it is empty.
    fn remove_directory(&mut self, relative: &str) -> RemoveOutcome;
}

/// The file and self-verification operations the helper needs.
pub trait CleanupFileSystem {
    /// This process's own absolute image path.
    fn own_path(&self) -> io::Result<PathBuf>;
    /// SHA-256 of one regular file, bounded by `deadline`.
    fn hash_file(&self, path: &Path, deadline: Instant) -> io::Result<Digest>;
    /// Verify one image's OS signature and return its publisher identity, bounded
    /// by `deadline`.
    fn signature(&self, path: &Path, deadline: Instant) -> Result<String, CleanupFailure>;
    /// Open the install root as an anchored traversal root.
    fn open_root(&mut self, root: &Path) -> Result<Box<dyn CleanupTree + '_>, CleanupFailure>;
}

/// A helper-side failure with no partial credit.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CleanupFailure;

impl fmt::Display for CleanupFailure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("cleanup verification failed")
    }
}

impl std::error::Error for CleanupFailure {}

/// Run one cleanup capability to completion and write the authenticated result.
///
/// `timeout` is clamped to [`MAX_CLEANUP_TIMEOUT`]. The returned value is the
/// exact frame content; [`CleanupCompletion::verify`] proves it was produced by
/// this request's capability.
pub fn execute_cleanup<C: CleanupChannel, F: CleanupFileSystem>(
    channel: &mut C,
    fs: &mut F,
    timeout: Duration,
) -> CleanupCompletion {
    run(channel, fs, timeout.min(MAX_CLEANUP_TIMEOUT))
}

fn write_completion<C: CleanupChannel>(
    channel: &mut C,
    completion: &CleanupCompletion,
    deadline: Instant,
) -> io::Result<()> {
    let frame = completion_frame(completion)?;
    channel.write_completion(&frame, deadline)
}

fn completion_frame(completion: &CleanupCompletion) -> io::Result<Vec<u8>> {
    let frame = serde_json::to_vec(completion)?;
    if frame.len() > MAX_CLEANUP_FRAME_BYTES {
        return Err(io::Error::other("completion frame is too large"));
    }
    Ok(frame)
}

fn run<C: CleanupChannel, F: CleanupFileSystem>(
    channel: &mut C,
    fs: &mut F,
    timeout: Duration,
) -> CleanupCompletion {
    let started = Instant::now();
    // The one hard bound of the work: reading, verification, waiting and removal
    // all share it. The single completion write gets a bounded grace below.
    let hard = started + timeout;
    let write_deadline = (hard + COMPLETION_WRITE_GRACE).min(started + MAX_CLEANUP_TIMEOUT);

    // 1. One bounded request frame. Nothing is authenticated before it parses.
    let bytes = match channel.read_request(MAX_CLEANUP_FRAME_BYTES, hard) {
        Ok(bytes) => bytes,
        // No capability was received, so this frame cannot be authenticated; it
        // is still written so the parent can distinguish a refusal from silence.
        Err(_) => {
            return finish(
                channel,
                CleanupCompletion::pending("", CleanupReason::InvalidRequest),
                None,
                write_deadline,
            );
        }
    };
    let request = match parse_request(&bytes) {
        Ok(request) => request,
        Err(_) => {
            return finish(
                channel,
                CleanupCompletion::pending("", CleanupReason::InvalidRequest),
                None,
                write_deadline,
            );
        }
    };
    // The request's own deadline may shorten the fixed ceiling, never extend it.
    let deadline = hard.min(started + Duration::from_millis(request.deadline_ms));
    let capability = match decode_hex_32(&request.capability) {
        Some(capability) => capability,
        None => {
            return finish(
                channel,
                CleanupCompletion::pending(&request.transaction, CleanupReason::InvalidRequest),
                None,
                write_deadline,
            );
        }
    };

    // 2. The capability arrived over an inherited pipe, and the inherited parent
    // handle names exactly the recorded process. A file redirect, console or a
    // recycled pid (the handle's start marker differs) is not that process. A
    // parent that exited after handing over the capability is still *provable*:
    // the handle's pid comes from the OS and a live process with that pid is
    // compared by start marker, so the exited case can only match the recorded
    // parent pid.
    let authenticated = match channel.parent_handle(request.parent_handle) {
        Some(ParentEvidence::Exact(identity)) => identity == request.parent,
        Some(ParentEvidence::Exited { pid }) => pid == request.parent.pid,
        None => false,
    };
    if !channel.is_inherited_pipe() || !authenticated {
        return finish(
            channel,
            CleanupCompletion::pending(&request.transaction, CleanupReason::ForgedChannel),
            Some(&capability),
            write_deadline,
        );
    }

    // 3. The helper is the exact signed image the request names.
    if !verify_self(&request, fs, deadline) {
        return finish(
            channel,
            CleanupCompletion::pending(&request.transaction, CleanupReason::SelfMismatch),
            Some(&capability),
            write_deadline,
        );
    }

    // 4. Wait for the parent to *release* the capability on the inherited
    // handles. No pid is reopened, so a parent that exited after signalling is
    // observed as released rather than as a stale pid, and the installer does not
    // have to exit before the helper can proceed.
    match channel.parent_state(request.parent_handle, request.release_handle, deadline) {
        ParentState::Exited => {}
        ParentState::Alive => {
            return finish(
                channel,
                CleanupCompletion::pending(&request.transaction, CleanupReason::ParentAlive),
                Some(&capability),
                write_deadline,
            );
        }
        ParentState::Unavailable => {
            return finish(
                channel,
                CleanupCompletion::pending(&request.transaction, CleanupReason::ParentUnavailable),
                Some(&capability),
                write_deadline,
            );
        }
    }

    // 5. Remove exactly the listed owned identities, relative to one opened root.
    let mut completion = CleanupCompletion::new(&request.transaction);
    let mut root = match fs.open_root(Path::new(&request.root)) {
        Ok(root) => root,
        Err(_) => {
            completion.reason = Some(CleanupReason::RootUnavailable);
            return finish(channel, completion, Some(&capability), write_deadline);
        }
    };
    for entry in &request.entries {
        if Instant::now() >= deadline {
            completion.reason = Some(CleanupReason::Deadline);
            return finish(channel, completion, Some(&capability), write_deadline);
        }
        let outcome = match entry.kind {
            OwnedKind::File => match entry.sha256 {
                Some(sha256) => root.remove_file(&entry.path, entry.size, sha256, deadline),
                None => RemoveOutcome::Preserved,
            },
            OwnedKind::Symlink => match entry.sha256 {
                Some(sha256) => root.remove_link(&entry.path, entry.size, sha256, deadline),
                None => RemoveOutcome::Preserved,
            },
            OwnedKind::Directory => root.remove_directory(&entry.path),
        };
        if outcome == RemoveOutcome::Deferred {
            // The deadline elapsed inside this entry: nothing is claimed about
            // it, and the completion is a pending `Deadline`.
            completion.reason = Some(CleanupReason::Deadline);
            return finish(channel, completion, Some(&capability), write_deadline);
        }
        match (outcome, entry.kind.is_directory()) {
            (RemoveOutcome::Removed | RemoveOutcome::Absent, true) => {
                completion.removed_directories += 1;
            }
            (RemoveOutcome::Removed | RemoveOutcome::Absent, false) => {
                completion.removed_files += 1;
            }
            (RemoveOutcome::Preserved, _) => completion.preserved += 1,
            (RemoveOutcome::Deferred, _) => unreachable!("handled above"),
        }
    }
    drop(root);

    completion.reason = (completion.preserved != 0).then_some(CleanupReason::Leftover);
    completion.status = if completion.preserved == 0 && Instant::now() < deadline {
        CleanupStatus::Complete
    } else {
        CleanupStatus::CleanupPending
    };
    if completion.status == CleanupStatus::CleanupPending && completion.reason.is_none() {
        completion.reason = Some(CleanupReason::Deadline);
    }
    // The completion write keeps the run's hard ceiling, not the (possibly
    // already elapsed) work deadline: a run that reports `ParentAlive` must still
    // be able to deliver that verdict.
    finish(channel, completion, Some(&capability), write_deadline)
}

/// Sign (when possible), write and return one completion.
fn finish<C: CleanupChannel>(
    channel: &mut C,
    mut completion: CleanupCompletion,
    capability: Option<&[u8]>,
    deadline: Instant,
) -> CleanupCompletion {
    if completion.status == CleanupStatus::Complete && completion.reason.is_some() {
        // A completion with a reason is never a success.
        completion.status = CleanupStatus::CleanupPending;
    }
    if let Some(capability) = capability {
        completion.mac = Some(encode_hex(&hmac_sha256(
            capability,
            &completion.mac_input(),
        )));
    }
    if write_completion(channel, &completion, deadline).is_err() {
        // The parent cannot learn of a success that was never delivered.
        completion.status = CleanupStatus::CleanupPending;
        completion.reason = Some(CleanupReason::ChannelFailure);
        completion.mac = None;
    }
    completion
}

/// Verify the helper's own path, exact image digest and OS signature.
fn verify_self<F: CleanupFileSystem>(request: &CleanupRequest, fs: &F, deadline: Instant) -> bool {
    let expected = Path::new(&request.helper_path);
    let Ok(own) = fs.own_path() else {
        return false;
    };
    let (Ok(own), Ok(expected)) = (canonical(&own), canonical(expected)) else {
        return false;
    };
    if !crate::platform::same_path(&own, &expected) {
        return false;
    }
    match fs.hash_file(&own, deadline) {
        Ok(digest) if digest == request.helper_sha256 => {}
        _ => return false,
    }
    matches!(fs.signature(&own, deadline), Ok(publisher) if publisher == request.publisher)
}

fn canonical(path: &Path) -> Result<PathBuf, CleanupFailure> {
    std::fs::canonicalize(path).map_err(|_| CleanupFailure)
}

/// Parse and strictly validate one request frame.
pub fn parse_request(bytes: &[u8]) -> InstallResult<CleanupRequest> {
    if bytes.is_empty() || bytes.len() > MAX_CLEANUP_FRAME_BYTES {
        return Err(invalid_request());
    }
    let request: CleanupRequest = serde_json::from_slice(bytes).map_err(|_| invalid_request())?;
    if request.schema != CLEANUP_SCHEMA_VERSION
        || request.magic != CLEANUP_MAGIC
        || request.transaction.len() != CAPABILITY_BYTES * 2
        || decode_hex_32(&request.transaction).is_none()
        || decode_hex_32(&request.capability).is_none()
        || request.publisher.is_empty()
        || request.publisher.len() > crate::model::MAX_PUBLISHER_BYTES
        || request.publisher.chars().any(char::is_control)
        || request.parent.pid == 0
        || request.parent_handle == 0
        || request.release_handle == request.parent_handle
        || request.deadline_ms == 0
        || request.deadline_ms > MAX_CLEANUP_TIMEOUT.as_millis() as u64
        || request.entries.len() > MAX_CLEANUP_ENTRIES
    {
        return Err(invalid_request());
    }
    let root = absolute_install_root(&request.root)?;
    let helper = Path::new(&request.helper_path);
    if !helper.is_absolute()
        || request.helper_path.len() > MAX_ROOT_BYTES
        || request.helper_path.chars().any(char::is_control)
        || crate::platform::path_is_within(&root, helper)
    {
        // The helper always lives outside the removable root.
        return Err(invalid_request());
    }
    let mut seen = std::collections::HashSet::with_capacity(request.entries.len());
    for entry in &request.entries {
        let normalized = normalize_relative_path(&entry.path).map_err(|_| invalid_request())?;
        if normalized != entry.path || entry.path.len() > MAX_PATH_BYTES {
            return Err(invalid_request());
        }
        if !seen.insert(entry.path.to_ascii_lowercase()) {
            return Err(invalid_request());
        }
        match entry.kind {
            OwnedKind::File | OwnedKind::Symlink if entry.sha256.is_none() => {
                return Err(invalid_request());
            }
            OwnedKind::Directory if entry.sha256.is_some() || entry.size != 0 => {
                return Err(invalid_request());
            }
            _ => {}
        }
    }
    // A directory may only be removed after everything below it, so a request
    // can never ask the helper to delete a file inside an already removed
    // directory (or rely on ordering the helper would have to invent).
    for (index, entry) in request.entries.iter().enumerate() {
        if !entry.kind.is_directory() {
            continue;
        }
        let prefix = format!("{}/", entry.path);
        if request.entries[index + 1..]
            .iter()
            .any(|later| later.path.starts_with(&prefix))
        {
            return Err(invalid_request());
        }
    }
    Ok(request)
}

/// The absolute root must be an installer-owned layout directory.
fn absolute_install_root(root: &str) -> InstallResult<PathBuf> {
    let path = Path::new(root);
    if !path.is_absolute()
        || root.len() > MAX_ROOT_BYTES
        || root.chars().any(char::is_control)
        || root == "/"
    {
        return Err(invalid_request());
    }
    let owned = path.file_name().and_then(|name| name.to_str());
    if !matches!(
        owned,
        Some(ROOT_DIR_PRODUCTION) | Some(ROOT_DIR_DEVELOPMENT) | Some(ROOT_DIR_FIXTURE)
    ) {
        return Err(invalid_request());
    }
    Ok(path.to_path_buf())
}

fn invalid_request() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidInput)
}

fn decode_hex_32(text: &str) -> Option<[u8; CAPABILITY_BYTES]> {
    if text.len() != CAPABILITY_BYTES * 2 {
        return None;
    }
    let text = text.as_bytes();
    let mut bytes = [0u8; CAPABILITY_BYTES];
    for (index, byte) in bytes.iter_mut().enumerate() {
        let high = hex_nibble(text[index * 2])?;
        let low = hex_nibble(text[index * 2 + 1])?;
        *byte = (high << 4) | low;
    }
    Some(bytes)
}

fn hex_nibble(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

/// Lower-case hex encoding.
pub fn encode_hex(bytes: &[u8]) -> String {
    use std::fmt::Write as _;

    let mut text = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        let _ = write!(text, "{byte:02x}");
    }
    text
}

/// HMAC-SHA256 (RFC 2104) over one completion encoding.
///
/// Implemented here because the installer pins only `sha2`; adding an HMAC crate
/// just for one fixed construction would widen the reviewed dependency set.
pub fn hmac_sha256(key: &[u8], message: &[u8]) -> [u8; 32] {
    let mut block = [0u8; 64];
    if key.len() > block.len() {
        block[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        block[..key.len()].copy_from_slice(key);
    }
    let mut inner = Sha256::new();
    let mut outer = Sha256::new();
    let mut inner_pad = [0x36u8; 64];
    let mut outer_pad = [0x5cu8; 64];
    for index in 0..64 {
        inner_pad[index] ^= block[index];
        outer_pad[index] ^= block[index];
    }
    inner.update(inner_pad);
    inner.update(message);
    let inner = inner.finalize();
    outer.update(outer_pad);
    outer.update(inner);
    outer.finalize().into()
}

// ---------------------------------------------------------------------------
// The installer-side half: the helper image is *not* deleted by the helper.
// ---------------------------------------------------------------------------

/// The installer's view of the staged helper image.
pub trait HelperImage {
    /// Delete the staged helper image; an absent image is success.
    fn remove(&mut self, path: &Path) -> io::Result<()>;
    /// Whether the image still exists (following no links).
    fn exists(&self, path: &Path) -> io::Result<bool>;
}

/// The `std` implementation of [`HelperImage`] used by the installer.
#[derive(Debug, Default)]
pub struct StdHelperImage;

impl HelperImage for StdHelperImage {
    fn remove(&mut self, path: &Path) -> io::Result<()> {
        match std::fs::remove_file(path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error),
        }
    }

    fn exists(&self, path: &Path) -> io::Result<bool> {
        match std::fs::symlink_metadata(path) {
            Ok(_) => Ok(true),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
            Err(error) => Err(error),
        }
    }
}

/// The installer-side completion of a cleanup capability.
///
/// Called only after the installer observed the helper's exit. It first
/// authenticates the completion frame against the capability **and** the expected
/// transaction id, then deletes the staged image and verifies it is gone. The
/// installer — not the helper — performs that deletion; anything else is
/// `CleanupPending`, never a completed uninstall.
pub fn remove_helper_after_exit(
    image: &mut impl HelperImage,
    helper_path: &Path,
    completion: &CleanupCompletion,
    capability: &[u8],
    transaction: &str,
) -> InstallResult<()> {
    if completion.status != CleanupStatus::Complete
        || completion.transaction != transaction
        || !completion.verify(capability)
    {
        return Err(InstallFailure::new(FailureCode::CleanupPending));
    }
    let verified = image
        .remove(helper_path)
        .and_then(|()| image.exists(helper_path))
        .map_err(|_| InstallFailure::new(FailureCode::CleanupPending))?;
    if verified {
        return Err(InstallFailure::new(FailureCode::CleanupPending));
    }
    Ok(())
}

/// The state of one process, as the OS reports it for *this* process.
///
/// The installer fills [`CleanupRequest::parent`] with it so the helper can prove
/// that the inherited handle names the installer itself. `None` on a host that
/// cannot supply a start marker (a non-product host, where the helper fails
/// closed before that check anyway).
pub fn parent_identity() -> Option<ParentIdentity> {
    let pid = std::process::id();
    process_start(pid).map(|start| ParentIdentity { pid, start })
}

/// One installed cleanup run.
///
/// Everything the installer must know to run the capability: the exact staged
/// helper, the single-use capability and the exact owned entries. The helper
/// never receives a command, a recursive target or a path to delete.
#[derive(Debug, Clone)]
pub struct HelperRun<'a> {
    /// The staged helper image (outside the removable root).
    pub helper_path: &'a Path,
    /// The exact SHA-256 of the staged image.
    pub helper_sha256: Digest,
    /// The pinned publisher the helper's own signature must report.
    pub publisher: &'a str,
    /// The transaction the completion is bound to (hex, 32 bytes).
    pub transaction: &'a str,
    /// The single-use completion key (32 bytes).
    pub capability: &'a [u8; CAPABILITY_BYTES],
    /// The installer-owned install root the entries are relative to.
    pub root: &'a Path,
    /// The exact owned entries, leaves first and directories deepest-first.
    pub entries: Vec<CleanupEntry>,
    /// The whole-run bound, clamped to [`MAX_CLEANUP_TIMEOUT`].
    pub timeout: Duration,
}

impl HelperRun<'_> {
    /// The request frame for one set of inherited handle values.
    fn request(
        &self,
        parent: ParentIdentity,
        parent_handle: u64,
        release_handle: u64,
    ) -> CleanupRequest {
        let deadline = self.timeout.min(MAX_CLEANUP_TIMEOUT);
        CleanupRequest {
            schema: CLEANUP_SCHEMA_VERSION,
            magic: CLEANUP_MAGIC.to_owned(),
            transaction: self.transaction.to_owned(),
            capability: encode_hex(self.capability),
            root: self.root.to_string_lossy().into_owned(),
            helper_path: self.helper_path.to_string_lossy().into_owned(),
            helper_sha256: self.helper_sha256,
            publisher: self.publisher.to_owned(),
            parent,
            parent_handle,
            release_handle,
            deadline_ms: deadline.as_millis() as u64,
            entries: self.entries.clone(),
        }
    }
}

/// Run one cleanup capability end to end and only report success when the
/// installer's own obligations are met.
///
/// This is the entry point the uninstall/update operation calls. It stages the
/// request, spawns the staged helper with its inherited channels, closes the
/// release handle as its last act, waits for the process to exit, reads the one
/// completion frame, authenticates it against the capability and transaction,
/// then deletes the helper image and verifies that its path is gone
/// ([`remove_helper_after_exit`]). Any deviation — no completion, an
/// unauthenticated or pending completion, a helper that did not exit inside the
/// deadline, an image that cannot be removed or is still present — is
/// `CleanupPending`; a completed uninstall is never reported on partial evidence.
pub fn run_cleanup_helper(run: &HelperRun<'_>) -> InstallResult<CleanupCompletion> {
    let (completion, verdict) = run_cleanup_helper_detailed(run);
    verdict?;
    let completion = completion.ok_or_else(|| InstallFailure::new(FailureCode::CleanupPending))?;
    Ok(completion)
}

/// [`run_cleanup_helper`] with the evidence kept.
///
/// Returns the completion that was read and authenticated (if any) together with
/// the installer's verdict, so a caller can report *why* a cleanup is pending
/// instead of only that it is. The verdict is `Ok(())` only when the completion
/// was `complete`, the staged image was removed and its absence was observed.
pub fn run_cleanup_helper_detailed(
    run: &HelperRun<'_>,
) -> (Option<CleanupCompletion>, InstallResult<()>) {
    let completion = match spawn_helper(run) {
        Ok(completion) => completion,
        Err(failure) => return (None, Err(failure)),
    };
    let mut image = StdHelperImage;
    let verdict = remove_helper_after_exit(
        &mut image,
        run.helper_path,
        &completion,
        run.capability,
        run.transaction,
    );
    (Some(completion), verdict)
}

/// The start marker of one process, where the host can report one.
#[cfg(any(windows, target_os = "macos"))]
fn process_start(pid: u32) -> Option<u64> {
    native::process_start(pid)
}

/// The start marker of one process, where the host can report one.
#[cfg(not(any(windows, target_os = "macos")))]
fn process_start(_pid: u32) -> Option<u64> {
    // A non-product host has no OS process identity to offer; the helper channel
    // on such a host refuses every request before this value is used.
    None
}

/// Authenticate one completion frame read from the helper.
fn accept_completion(
    frame: &[u8],
    capability: &[u8; CAPABILITY_BYTES],
    transaction: &str,
) -> InstallResult<CleanupCompletion> {
    if frame.is_empty() || frame.len() > MAX_CLEANUP_FRAME_BYTES {
        return Err(InstallFailure::new(FailureCode::CleanupPending));
    }
    let completion: CleanupCompletion = serde_json::from_slice(frame)
        .map_err(|_| InstallFailure::new(FailureCode::CleanupPending))?;
    if completion.transaction != transaction || !completion.verify(capability) {
        return Err(InstallFailure::new(FailureCode::CleanupPending));
    }
    Ok(completion)
}

#[cfg(any(windows, target_os = "macos"))]
fn spawn_helper(run: &HelperRun<'_>) -> InstallResult<CleanupCompletion> {
    native::spawn_helper(run)
}

#[cfg(not(any(windows, target_os = "macos")))]
fn spawn_helper(run: &HelperRun<'_>) -> InstallResult<CleanupCompletion> {
    portable_spawn::run(run)
}

/// The portable reference spawn: a bounded wait plus a bounded frame read.
///
/// It is the non-product host implementation of [`spawn_helper`]; the Windows and
/// macOS adapters hand over a process handle / socketpair and a release pipe
/// instead (`cleanup::native`). It exists so the installer-side ordering can be
/// exercised on every host, and it is never selected by a shipped target.
#[cfg(not(any(windows, target_os = "macos")))]
mod portable_spawn {
    use super::*;
    use std::io::{Read, Write};
    use std::process::{Command, Stdio};

    pub(super) fn run(run: &HelperRun<'_>) -> InstallResult<CleanupCompletion> {
        let pending = || InstallFailure::new(FailureCode::CleanupPending);
        let deadline = Instant::now() + run.timeout.min(MAX_CLEANUP_TIMEOUT);
        let parent = parent_identity().unwrap_or(ParentIdentity {
            pid: std::process::id(),
            start: 0,
        });
        let request = run.request(parent, 3, 0);
        let frame = serde_json::to_vec(&request).map_err(|_| pending())?;
        // The installer validates the request it is about to send exactly as the
        // helper will, so a malformed capability fails here, not in the helper.
        parse_request(&frame)?;
        let mut child = Command::new(run.helper_path)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| pending())?;
        {
            let mut stdin = child.stdin.take().ok_or_else(pending)?;
            stdin.write_all(&frame).map_err(|_| pending())?;
            // Closing the request channel is the release: after this the installer
            // will not write again.
            drop(stdin);
        }
        // Wait for the process, bounded by the deadline. If it does not exit in
        // time nothing is claimed and no image is removed.
        let mut status = None;
        while Instant::now() < deadline {
            match child.try_wait() {
                Ok(Some(exit)) => {
                    status = Some(exit);
                    break;
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(5)),
                Err(_) => return Err(pending()),
            }
        }
        let Some(status) = status else {
            // Do not terminate the helper: it is left to finish on its own and the
            // cleanup is reported as pending.
            return Err(pending());
        };
        let mut frame = Vec::new();
        if let Some(mut stdout) = child.stdout.take() {
            // Bounded read: one frame, never more.
            stdout
                .by_ref()
                .take(MAX_CLEANUP_FRAME_BYTES as u64 + 1)
                .read_to_end(&mut frame)
                .map_err(|_| pending())?;
        }
        let completion = accept_completion(&frame, run.capability, run.transaction)?;
        // The authenticated completion and the process exit must agree: a helper
        // that reports success but did not exit successfully is not a success.
        if completion.status == CleanupStatus::Complete && !status.success() {
            return Err(pending());
        }
        Ok(completion)
    }
}

// ---------------------------------------------------------------------------
// The portable reference implementation (test-only; never a production path).
// ---------------------------------------------------------------------------

/// Which signature policy a [`StdCleanupFs`] applies to its own image.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignaturePolicy {
    /// The OS signature was not assessed; every run fails closed.
    Unavailable,
    /// A portable test double: no OS assessment exists on this host, so the
    /// caller's fixture is asserted to be the helper. Never selected by a
    /// production binary (the native adapters use their own implementation).
    TrustedFixture,
}

/// A portable [`CleanupFileSystem`] over `std`, used by the portable protocol
/// tests and the non-product `cargo build` of the helper binary.
///
/// Its traversal is a lexical + `lstat` approximation of the anchored native
/// walk: it is honest test evidence for the *decisions*, not a claim that a
/// path-based walk is anchored. The production adapters use OS directory handles.
#[derive(Debug, Clone)]
pub struct StdCleanupFs {
    own_path: PathBuf,
    policy: SignaturePolicy,
    publisher: String,
}

impl StdCleanupFs {
    /// A filesystem whose OS signature policy is `Unavailable`.
    pub fn new(own_path: PathBuf) -> Self {
        Self {
            own_path,
            policy: SignaturePolicy::Unavailable,
            publisher: String::new(),
        }
    }

    /// A portable test double: `publisher` is asserted without an OS assessment.
    pub fn trusted_fixture(own_path: PathBuf, publisher: impl Into<String>) -> Self {
        Self {
            own_path,
            policy: SignaturePolicy::TrustedFixture,
            publisher: publisher.into(),
        }
    }

    /// The closed signature policy this reference implementation applies.
    pub fn signature_policy(&self) -> SignaturePolicy {
        self.policy
    }
}

impl CleanupFileSystem for StdCleanupFs {
    fn own_path(&self) -> io::Result<PathBuf> {
        Ok(self.own_path.clone())
    }

    fn hash_file(&self, path: &Path, deadline: Instant) -> io::Result<Digest> {
        if Instant::now() >= deadline {
            return Err(io::Error::other("deadline elapsed"));
        }
        crate::platform::hash_file(path)
            .map_err(|_| io::Error::other("file is not a readable regular file"))
    }

    fn signature(&self, _path: &Path, deadline: Instant) -> Result<String, CleanupFailure> {
        if Instant::now() >= deadline {
            return Err(CleanupFailure);
        }
        match self.policy {
            SignaturePolicy::TrustedFixture => Ok(self.publisher.clone()),
            SignaturePolicy::Unavailable => Err(CleanupFailure),
        }
    }

    fn open_root(&mut self, root: &Path) -> Result<Box<dyn CleanupTree + '_>, CleanupFailure> {
        let metadata = std::fs::symlink_metadata(root).map_err(|_| CleanupFailure)?;
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            return Err(CleanupFailure);
        }
        Ok(Box::new(StdCleanupTree {
            root: root.to_path_buf(),
        }))
    }
}

/// SHA-256 of one file with a deadline; `Ok(None)` means the deadline elapsed.
fn hash_file_with_deadline(path: &Path, deadline: Instant) -> io::Result<Option<Digest>> {
    use std::io::Read as _;

    let mut file = crate::platform::open_no_follow(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        if Instant::now() >= deadline {
            return Ok(None);
        }
        let read = file.read(&mut buffer)?;
        if read == 0 {
            return Ok(Some(Digest::from_bytes(hasher.finalize().into())));
        }
        hasher.update(&buffer[..read]);
    }
}

struct StdCleanupTree {
    root: PathBuf,
}

impl StdCleanupTree {
    /// Resolve one relative entry below the root, refusing a link at every
    /// component. `None` means "absent" (an idempotent missing entry).
    fn resolve(&self, relative: &str) -> Result<Option<PathBuf>, CleanupFailure> {
        let normalized = normalize_relative_path(relative).map_err(|_| CleanupFailure)?;
        let mut path = self.root.clone();
        let parts: Vec<&str> = normalized.split('/').collect();
        for (index, part) in parts.iter().enumerate() {
            path.push(part);
            let last = index + 1 == parts.len();
            let metadata = match std::fs::symlink_metadata(&path) {
                Ok(metadata) => metadata,
                Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
                Err(_) => return Err(CleanupFailure),
            };
            let is_link = metadata.file_type().is_symlink();
            if last {
                continue;
            }
            if is_link || !metadata.is_dir() {
                // A link where a directory component is expected is never
                // traversed; the entry cannot be resolved safely.
                return Err(CleanupFailure);
            }
        }
        Ok(Some(path))
    }
}

impl CleanupTree for StdCleanupTree {
    fn remove_file(
        &mut self,
        relative: &str,
        size: u64,
        sha256: Digest,
        deadline: Instant,
    ) -> RemoveOutcome {
        let path = match self.resolve(relative) {
            Ok(Some(path)) => path,
            Ok(None) => return RemoveOutcome::Absent,
            Err(_) => return RemoveOutcome::Preserved,
        };
        let Ok(metadata) = std::fs::symlink_metadata(&path) else {
            return RemoveOutcome::Preserved;
        };
        if metadata.file_type().is_symlink() || !metadata.is_file() || metadata.len() != size {
            return RemoveOutcome::Preserved;
        }
        match hash_file_with_deadline(&path, deadline) {
            Ok(Some(digest)) if digest == sha256 => {}
            Ok(Some(_)) => return RemoveOutcome::Preserved,
            Ok(None) => return RemoveOutcome::Deferred,
            Err(_) => return RemoveOutcome::Preserved,
        }
        match std::fs::remove_file(&path) {
            Ok(()) => RemoveOutcome::Removed,
            Err(error) if error.kind() == io::ErrorKind::NotFound => RemoveOutcome::Absent,
            Err(_) => RemoveOutcome::Preserved,
        }
    }

    fn remove_link(
        &mut self,
        relative: &str,
        size: u64,
        sha256: Digest,
        _deadline: Instant,
    ) -> RemoveOutcome {
        let path = match self.resolve(relative) {
            Ok(Some(path)) => path,
            Ok(None) => return RemoveOutcome::Absent,
            Err(_) => return RemoveOutcome::Preserved,
        };
        let Ok(metadata) = std::fs::symlink_metadata(&path) else {
            return RemoveOutcome::Preserved;
        };
        if !metadata.file_type().is_symlink() || metadata.len() != size {
            return RemoveOutcome::Preserved;
        }
        match crate::platform::link_target_digest(&path) {
            Ok(digest) if digest == sha256 => {}
            _ => return RemoveOutcome::Preserved,
        }
        match std::fs::remove_file(&path) {
            Ok(()) => RemoveOutcome::Removed,
            Err(error) if error.kind() == io::ErrorKind::NotFound => RemoveOutcome::Absent,
            Err(_) => RemoveOutcome::Preserved,
        }
    }

    fn remove_directory(&mut self, relative: &str) -> RemoveOutcome {
        let path = match self.resolve(relative) {
            Ok(Some(path)) => path,
            Ok(None) => return RemoveOutcome::Absent,
            // A blocked component (a link where a directory is expected) is
            // preserved, never reported as an already-absent entry.
            Err(_) => return RemoveOutcome::Preserved,
        };
        match std::fs::remove_dir(&path) {
            Ok(()) => RemoveOutcome::Removed,
            Err(error) if error.kind() == io::ErrorKind::NotFound => RemoveOutcome::Absent,
            // Non-empty (or otherwise not removable): preserve and report.
            Err(_) => RemoveOutcome::Preserved,
        }
    }
}

/// The process entry point used by `src/bin/veles-bridge-cleanup.rs`.
///
/// Uses the host's real cleanup channel. The Windows and macOS builds verify
/// their own OS signature; any other host fails closed with a pending
/// completion, because no product target ships there.
pub fn run_cleanup_process() -> i32 {
    #[cfg(any(windows, target_os = "macos"))]
    {
        native::run()
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    {
        portable::run()
    }
}

#[cfg(not(any(windows, target_os = "macos")))]
mod portable {
    use super::*;
    use std::io::{Read, Write};

    struct StdioChannel;

    impl CleanupChannel for StdioChannel {
        fn is_inherited_pipe(&self) -> bool {
            false
        }

        fn parent_handle(&self, _handle: u64) -> Option<ParentEvidence> {
            None
        }

        fn parent_state(
            &mut self,
            _parent_handle: u64,
            _release_handle: u64,
            deadline: Instant,
        ) -> ParentState {
            // No process handle exists on a non-product host.
            std::thread::sleep(deadline.saturating_duration_since(Instant::now()));
            ParentState::Unavailable
        }

        fn read_request(&mut self, limit: usize, _deadline: Instant) -> io::Result<Vec<u8>> {
            let mut bytes = Vec::new();
            io::stdin().take(limit as u64 + 1).read_to_end(&mut bytes)?;
            if bytes.len() > limit {
                return Err(io::Error::other("request frame is too large"));
            }
            Ok(bytes)
        }

        fn write_completion(&mut self, frame: &[u8], _deadline: Instant) -> io::Result<()> {
            let mut stdout = io::stdout();
            stdout.write_all(frame)?;
            stdout.flush()
        }
    }

    /// Non-product host: no OS cleanup channel exists, so the helper can only
    /// report a pending cleanup.
    pub(super) fn run() -> i32 {
        let own = std::env::current_exe().unwrap_or_default();
        let mut channel = StdioChannel;
        let mut fs = StdCleanupFs::new(own);
        let completion = execute_cleanup(&mut channel, &mut fs, MAX_CLEANUP_TIMEOUT);
        i32::from(completion.status != CleanupStatus::Complete)
    }
}

#[cfg(any(windows, target_os = "macos"))]
mod native;
