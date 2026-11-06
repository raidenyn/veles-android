//! Bounded, single-use cleanup capability for owned-file removal (Task 8).
//!
//! Deleting an executing Windows maintenance binary, or removing the last files
//! of an installation while the installer itself may still hold handles, cannot
//! be done safely in-process. Instead the installer stages a **signed cleanup
//! helper** into a private temporary directory outside the removable root, hands
//! it an inherited pipe and exits. The helper:
//!
//! 1. reads one bounded request frame from the inherited pipe (never a path, a
//!    recursive delete target or a command line);
//! 2. refuses a channel that is not an inherited OS pipe, or whose peer is not
//!    the process the request names (a pid is not an identity: the peer's start
//!    marker must match too, so a reused pid is rejected);
//! 3. verifies its own absolute path, its own image digest and its own OS
//!    signature against the request's pinned publisher *before* touching
//!    anything;
//! 4. waits for the parent to exit, bounded by [`MAX_CLEANUP_TIMEOUT`] (10s),
//!    re-reading the recorded parent identity so a reused pid can neither keep
//!    it waiting nor make it act while the real parent still runs;
//! 5. deletes only the exact listed owned identities - size and SHA-256 are
//!    re-checked immediately before each unlink, a missing entry is idempotent,
//!    a mismatched entry is preserved, and directories are removed only when
//!    empty (there is no recursive delete anywhere in this module);
//! 6. arms its own deletion with native OS deletion semantics (on Windows the
//!    delete-on-close disposition, on macOS `unlink`), and reports
//!    `cleanup-pending` - never `complete` - when any of that could not be
//!    established;
//! 7. writes one authenticated completion frame (HMAC-SHA256 keyed by the
//!    request's single-use capability) and exits non-zero unless the status is
//!    `complete`.
//!
//! Nothing here terminates a process, invokes a shell, registers an autostart
//! task or falls back to reboot-time deletion.

use std::fmt;
use std::io;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

use crate::inventory::normalize_relative_path;
use crate::model::{Digest, FailureCode, InstallFailure, InstallResult, MAX_PATH_BYTES};
use crate::paths::{MAX_ROOT_BYTES, ROOT_DIR_DEVELOPMENT, ROOT_DIR_FIXTURE, ROOT_DIR_PRODUCTION};
use crate::platform::{OwnedKind, path_is_within, same_path};

/// Completed cleanup frames are this schema.
pub const CLEANUP_SCHEMA_VERSION: u32 = 1;
/// Wire magic that identifies this protocol generation.
pub const CLEANUP_MAGIC: &str = "veles-cleanup-v1";
/// Hard ceiling for one cleanup run.
pub const MAX_CLEANUP_TIMEOUT: Duration = Duration::from_secs(10);
/// Bound on one request/completion frame.
pub const MAX_CLEANUP_FRAME_BYTES: usize = 256 * 1024;
/// Bound on the entries one request may name.
pub const MAX_CLEANUP_ENTRIES: usize = 4096;
/// Bound on the capability/transaction byte strings (hex-encoded 32 bytes).
const CAPABILITY_BYTES: usize = 32;
/// Poll interval while waiting for the parent.
const PARENT_POLL_INTERVAL: Duration = Duration::from_millis(25);

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
    /// File or directory.
    pub kind: OwnedKind,
    /// Exact size for a file (zero for a directory).
    pub size: u64,
    /// Exact SHA-256 for a file (absent for a directory).
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
    /// Deadline in milliseconds, clamped to [`MAX_CLEANUP_TIMEOUT`].
    pub deadline_ms: u64,
    /// Exact owned entries, files first and directories deepest-first.
    pub entries: Vec<CleanupEntry>,
}

/// The outcome the helper reports.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum CleanupStatus {
    /// Every listed identity was removed (or already absent), the parent exited,
    /// and the helper's own deletion was armed.
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
    /// The channel is not an inherited pipe, or its peer is not the named parent
    /// (including a reused pid).
    ForgedChannel,
    /// The helper's own path, digest or signature did not match the request.
    SelfMismatch,
    /// The parent was still alive at the deadline.
    ParentAlive,
    /// The parent identity could not be read.
    ParentUnavailable,
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

/// The state of the recorded parent process.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ParentState {
    /// The same process is still alive; the helper must keep waiting.
    Alive,
    /// The recorded process exited.
    Exited,
    /// The pid now belongs to a different process (the recorded parent exited).
    Reused,
    /// The OS would not say; the helper must not act.
    Unavailable,
}

/// The inherited channel the helper reads its request from and writes the
/// completion to.
///
/// The native implementations also prove that this is an OS pipe and who the peer
/// is; a test double scripts the same facts.
pub trait CleanupChannel {
    /// Whether both ends are inherited anonymous/named pipe handles rather than
    /// a regular file, console or other redirect.
    fn is_inherited_pipe(&self) -> bool;
    /// The identity of the process at the other end, when the OS reports it.
    fn peer(&self) -> Option<ParentIdentity>;
    /// The current state of the recorded parent.
    fn parent_state(&self, parent: &ParentIdentity) -> ParentState;
    /// Read one bounded request frame; must stop at the frame bound.
    fn read_request(&mut self, limit: usize) -> io::Result<Vec<u8>>;
    /// Write one bounded completion frame.
    fn write_completion(&mut self, frame: &[u8]) -> io::Result<()>;
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
}

/// The file and self-verification operations the helper needs.
pub trait CleanupFileSystem {
    /// This process's own absolute image path.
    fn own_path(&self) -> io::Result<PathBuf>;
    /// SHA-256 of one regular file.
    fn hash_file(&self, path: &Path) -> io::Result<Digest>;
    /// Verify one image's OS signature and return its publisher identity.
    fn signature(&self, path: &Path) -> Result<String, CleanupFailure>;
    /// Remove one file only when size and content still match.
    fn remove_file_exact(&mut self, path: &Path, size: u64, sha256: Digest) -> RemoveOutcome;
    /// Remove one directory only when it is empty.
    fn remove_directory(&mut self, path: &Path) -> RemoveOutcome;
    /// Arm this helper's own deletion with native OS semantics.
    fn arm_self_delete(&mut self, path: &Path) -> Result<(), CleanupFailure>;
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
) -> io::Result<()> {
    let frame = completion_frame(completion)?;
    channel.write_completion(&frame)
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

    // 1. One bounded request frame. Nothing is authenticated before it parses.
    let bytes = match channel.read_request(MAX_CLEANUP_FRAME_BYTES) {
        Ok(bytes) => bytes,
        // No capability was received, so this frame cannot be authenticated; it
        // is still written so the parent can distinguish a refusal from silence.
        Err(_) => {
            return finish(
                channel,
                CleanupCompletion::pending("", CleanupReason::InvalidRequest),
                None,
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
            );
        }
    };
    // The caller's bound and the request's own deadline both apply; neither may
    // extend the fixed ceiling.
    let deadline = started + timeout.min(Duration::from_millis(request.deadline_ms));
    let capability = match decode_hex_32(&request.capability) {
        Some(capability) => capability,
        None => {
            return finish(
                channel,
                CleanupCompletion::pending(&request.transaction, CleanupReason::InvalidRequest),
                None,
            );
        }
    };

    // 2. The capability is an inherited pipe from the recorded parent. A file
    // redirect, console or another process (including a recycled pid) is not.
    if !channel.is_inherited_pipe() || channel.peer() != Some(request.parent) {
        return finish(
            channel,
            CleanupCompletion::pending(&request.transaction, CleanupReason::ForgedChannel),
            Some(&capability),
        );
    }

    // 3. The helper is the exact signed image the request names.
    if !verify_self(&request, fs) {
        return finish(
            channel,
            CleanupCompletion::pending(&request.transaction, CleanupReason::SelfMismatch),
            Some(&capability),
        );
    }

    // 4. Wait for the parent to exit, bounded.
    match wait_for_parent(channel, &request.parent, deadline) {
        WaitOutcome::Exited => {}
        WaitOutcome::Alive => {
            return finish(
                channel,
                CleanupCompletion::pending(&request.transaction, CleanupReason::ParentAlive),
                Some(&capability),
            );
        }
        WaitOutcome::Unavailable => {
            return finish(
                channel,
                CleanupCompletion::pending(&request.transaction, CleanupReason::ParentUnavailable),
                Some(&capability),
            );
        }
    }

    // 5. Remove exactly the listed owned identities.
    let root = Path::new(&request.root);
    let mut completion = CleanupCompletion::new(&request.transaction);
    for entry in &request.entries {
        if Instant::now() >= deadline {
            completion.reason = Some(CleanupReason::Deadline);
            return finish(channel, completion, Some(&capability));
        }
        let Ok(path) = resolved_entry(root, &entry.path) else {
            // The request validated, so this is a runtime race with the root
            // itself: preserve rather than delete anything unexpected.
            completion.preserved += 1;
            continue;
        };
        match entry.kind {
            OwnedKind::File => match entry.sha256 {
                Some(sha256) => match fs.remove_file_exact(&path, entry.size, sha256) {
                    RemoveOutcome::Removed | RemoveOutcome::Absent => {
                        completion.removed_files += 1;
                    }
                    RemoveOutcome::Preserved => completion.preserved += 1,
                },
                None => completion.preserved += 1,
            },
            OwnedKind::Directory => match fs.remove_directory(&path) {
                RemoveOutcome::Removed | RemoveOutcome::Absent => {
                    completion.removed_directories += 1;
                }
                RemoveOutcome::Preserved => completion.preserved += 1,
            },
        }
    }

    // 6. The helper removes itself, outside the removable root, with native OS
    // deletion semantics. Failing that is pending, never success.
    if fs.arm_self_delete(Path::new(&request.helper_path)).is_err() {
        completion.reason = Some(CleanupReason::SelfMismatch);
        return finish(channel, completion, Some(&capability));
    }
    completion.reason = (completion.preserved != 0).then_some(CleanupReason::Leftover);
    completion.status = if completion.preserved == 0 {
        CleanupStatus::Complete
    } else {
        CleanupStatus::CleanupPending
    };
    finish(channel, completion, Some(&capability))
}

/// Sign (when possible), write and return one completion.
fn finish<C: CleanupChannel>(
    channel: &mut C,
    mut completion: CleanupCompletion,
    capability: Option<&[u8]>,
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
    if write_completion(channel, &completion).is_err() {
        // The parent cannot learn of a success that was never delivered.
        completion.status = CleanupStatus::CleanupPending;
        completion.reason = Some(CleanupReason::ChannelFailure);
        completion.mac = None;
    }
    completion
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum WaitOutcome {
    Exited,
    Alive,
    Unavailable,
}

fn wait_for_parent<C: CleanupChannel>(
    channel: &C,
    parent: &ParentIdentity,
    deadline: Instant,
) -> WaitOutcome {
    loop {
        match channel.parent_state(parent) {
            // The recorded process exited, or its pid now belongs to someone
            // else: in both cases the process that spawned this helper is gone.
            ParentState::Exited | ParentState::Reused => return WaitOutcome::Exited,
            ParentState::Unavailable => return WaitOutcome::Unavailable,
            ParentState::Alive => {
                if Instant::now() >= deadline {
                    return WaitOutcome::Alive;
                }
                thread::sleep(
                    PARENT_POLL_INTERVAL.min(deadline.saturating_duration_since(Instant::now())),
                );
            }
        }
    }
}

/// Verify the helper's own path, exact image digest and OS signature.
fn verify_self<F: CleanupFileSystem>(request: &CleanupRequest, fs: &F) -> bool {
    let expected = Path::new(&request.helper_path);
    let Ok(own) = fs.own_path() else {
        return false;
    };
    let (Ok(own), Ok(expected)) = (canonical(&own), canonical(expected)) else {
        return false;
    };
    if !same_path(&own, &expected) {
        return false;
    }
    match fs.hash_file(&own) {
        Ok(digest) if digest == request.helper_sha256 => {}
        _ => return false,
    }
    matches!(fs.signature(&own), Ok(publisher) if publisher == request.publisher)
}

fn canonical(path: &Path) -> Result<PathBuf, CleanupFailure> {
    std::fs::canonicalize(path).map_err(|_| CleanupFailure)
}

/// Resolve one validated relative entry below the request root.
fn resolved_entry(root: &Path, relative: &str) -> Result<PathBuf, CleanupFailure> {
    let normalized = normalize_relative_path(relative).map_err(|_| CleanupFailure)?;
    let joined = root.join(Path::new(&normalized));
    if !path_is_within(root, &joined) {
        return Err(CleanupFailure);
    }
    Ok(joined)
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
        || path_is_within(&root, helper)
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
            OwnedKind::File if entry.sha256.is_none() => return Err(invalid_request()),
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
        if entry.kind != OwnedKind::Directory {
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
}

impl CleanupFileSystem for StdCleanupFs {
    fn own_path(&self) -> io::Result<PathBuf> {
        Ok(self.own_path.clone())
    }

    fn hash_file(&self, path: &Path) -> io::Result<Digest> {
        crate::platform::hash_file(path)
            .map_err(|_| io::Error::other("file is not a readable regular file"))
    }

    fn signature(&self, _path: &Path) -> Result<String, CleanupFailure> {
        match self.policy {
            SignaturePolicy::TrustedFixture => Ok(self.publisher.clone()),
            SignaturePolicy::Unavailable => Err(CleanupFailure),
        }
    }

    fn remove_file_exact(&mut self, path: &Path, size: u64, sha256: Digest) -> RemoveOutcome {
        let metadata = match std::fs::symlink_metadata(path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return RemoveOutcome::Absent,
            Err(_) => return RemoveOutcome::Preserved,
        };
        // Never follow a link and never remove a non-regular file.
        if metadata.file_type().is_symlink() || !metadata.is_file() || metadata.len() != size {
            return RemoveOutcome::Preserved;
        }
        match crate::platform::hash_file(path) {
            Ok(digest) if digest == sha256 => {}
            _ => return RemoveOutcome::Preserved,
        }
        match std::fs::remove_file(path) {
            Ok(()) => RemoveOutcome::Removed,
            Err(error) if error.kind() == io::ErrorKind::NotFound => RemoveOutcome::Absent,
            Err(_) => RemoveOutcome::Preserved,
        }
    }

    fn remove_directory(&mut self, path: &Path) -> RemoveOutcome {
        match std::fs::remove_dir(path) {
            Ok(()) => RemoveOutcome::Removed,
            Err(error) if error.kind() == io::ErrorKind::NotFound => RemoveOutcome::Absent,
            // Non-empty (or otherwise not removable): preserve and report.
            Err(_) => RemoveOutcome::Preserved,
        }
    }

    fn arm_self_delete(&mut self, path: &Path) -> Result<(), CleanupFailure> {
        match std::fs::remove_file(path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(_) => Err(CleanupFailure),
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

        fn peer(&self) -> Option<ParentIdentity> {
            None
        }

        fn parent_state(&self, _parent: &ParentIdentity) -> ParentState {
            ParentState::Unavailable
        }

        fn read_request(&mut self, limit: usize) -> io::Result<Vec<u8>> {
            let mut bytes = Vec::new();
            io::stdin().take(limit as u64 + 1).read_to_end(&mut bytes)?;
            if bytes.len() > limit {
                return Err(io::Error::other("request frame is too large"));
            }
            Ok(bytes)
        }

        fn write_completion(&mut self, frame: &[u8]) -> io::Result<()> {
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
