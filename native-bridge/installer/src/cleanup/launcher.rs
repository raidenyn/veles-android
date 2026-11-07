//! Short-lived cleanup launcher (OTP-25 Task 10, the Task 8 carry-forward).
//!
//! The cleanup helper deletes only after it observed the **exit** of the process
//! whose identity its request records, and the long-lived installer must stay
//! alive to read the authenticated completion and delete the helper image (a
//! running image cannot be deleted on Windows). Those two facts only fit together
//! if the process the helper watches is *not* the installer. The launcher is that
//! process:
//!
//! ```text
//! installer ──spawn (fixed argv)──▶ launcher ──spawn──▶ helper
//!     │  one launch frame on stdin        │  request + release bytes on pipes
//!     │  (the completion pipe's child     │  inherited completion pipe as stdout
//!     │   end is inherited by handle)     │  its OWN process handle as "parent"
//!     │◀── one status frame on stdout ────┘  then the launcher exits
//!     │
//!     ├─ waits for the launcher to exit (bounded)
//!     ├─ waits for the helper's exit (bounded), reads the ONE completion frame
//!     │  from the completion pipe it kept, authenticates it
//!     └─ deletes the helper image and verifies it is gone - only then success
//! ```
//!
//! The helper therefore sees its recorded parent (the launcher) exit while the
//! installer is still running, and the installer never reports success before an
//! authenticated `complete`, the helper's exit and the image's absence.
//!
//! This module holds the portable, testable part: the two bounded wire frames and
//! their strict validation. The Windows process plumbing lives in
//! `cleanup::native`; its **native execution is BLOCKED on this Linux host** and
//! compiles only for the Windows target. macOS gets its launcher with the Task 11
//! frontend; until then a non-Windows host answers `CleanupPending`, which is the
//! fail-closed verdict the Task 8 ruling already named.
//!
//! The launcher takes no command line operand and no environment value: its input
//! is one frame on an inherited pipe. The frame names the helper by absolute path
//! **and** exact SHA-256, the request is validated by the same strict
//! [`parse_request`] the helper applies, and the helper then re-verifies its own
//! image, digest and pinned publisher signature before touching anything. A frame
//! cannot carry a command, a recursive delete target or a URL.

use serde::{Deserialize, Serialize};

use super::{
    CLEANUP_MAGIC, CLEANUP_SCHEMA_VERSION, CleanupRequest, HelperRun, MAX_CLEANUP_FRAME_BYTES,
    ParentIdentity, parse_request,
};
use crate::model::{FailureCode, InstallFailure, InstallResult};

/// The only argument the launcher mode accepts (and it accepts nothing else).
pub const CLEANUP_LAUNCHER_ARG: &str = "--internal-cleanup-launcher";
/// Launch/status frames are this schema.
pub const LAUNCH_SCHEMA_VERSION: u32 = 1;
/// Wire magic of the launch frame.
pub const LAUNCH_MAGIC: &str = "veles-cleanup-launch-v1";
/// Wire magic of the status frame.
pub const LAUNCH_STATUS_MAGIC: &str = "veles-cleanup-launched-v1";
/// Bound on the launch frame: it carries one request.
pub const MAX_LAUNCH_FRAME_BYTES: usize = MAX_CLEANUP_FRAME_BYTES + 1024;
/// Bound on the status frame.
pub const MAX_LAUNCH_STATUS_BYTES: usize = 512;

/// The identity placeholders a launch frame carries.
///
/// The installer cannot know the launcher's identity before it exists, so the
/// frame carries fixed placeholders that the launcher *replaces* with its own
/// process identity and freshly created handles. A frame with any other value is
/// refused, so the frame has exactly one canonical form.
const PLACEHOLDER_PARENT: ParentIdentity = ParentIdentity { pid: 1, start: 0 };
const PLACEHOLDER_PARENT_HANDLE: u64 = 1;
const PLACEHOLDER_RELEASE_HANDLE: u64 = 2;

/// The one frame the installer writes to the launcher's standard input.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LaunchRequest {
    /// [`LAUNCH_SCHEMA_VERSION`].
    pub schema: u32,
    /// [`LAUNCH_MAGIC`].
    pub magic: String,
    /// The value, in the launcher's handle table, of the inherited **child end**
    /// of the completion pipe the installer keeps the read end of. The launcher
    /// hands it on to the helper as the helper's standard output.
    pub completion_handle: u64,
    /// The cleanup request with placeholder parent identity and handles.
    pub request: CleanupRequest,
}

impl LaunchRequest {
    /// The frame for one cleanup run.
    pub fn for_run(run: &HelperRun<'_>, completion_handle: u64) -> Self {
        Self {
            schema: LAUNCH_SCHEMA_VERSION,
            magic: LAUNCH_MAGIC.to_owned(),
            completion_handle,
            request: run.request(
                PLACEHOLDER_PARENT,
                PLACEHOLDER_PARENT_HANDLE,
                PLACEHOLDER_RELEASE_HANDLE,
            ),
        }
    }

    /// Encode the frame; refused when it would exceed the bound.
    pub fn encode(&self) -> InstallResult<Vec<u8>> {
        let bytes = serde_json::to_vec(self).map_err(|_| invalid())?;
        if bytes.len() > MAX_LAUNCH_FRAME_BYTES {
            return Err(invalid());
        }
        Ok(bytes)
    }

    /// Parse and strictly validate one launch frame from untrusted bytes.
    pub fn parse(bytes: &[u8]) -> InstallResult<Self> {
        if bytes.is_empty() || bytes.len() > MAX_LAUNCH_FRAME_BYTES {
            return Err(invalid());
        }
        let frame: Self = serde_json::from_slice(bytes).map_err(|_| invalid())?;
        if frame.schema != LAUNCH_SCHEMA_VERSION
            || frame.magic != LAUNCH_MAGIC
            || frame.completion_handle == 0
            || frame.request.schema != CLEANUP_SCHEMA_VERSION
            || frame.request.magic != CLEANUP_MAGIC
            || frame.request.parent != PLACEHOLDER_PARENT
            || frame.request.parent_handle != PLACEHOLDER_PARENT_HANDLE
            || frame.request.release_handle != PLACEHOLDER_RELEASE_HANDLE
        {
            return Err(invalid());
        }
        // Exactly the validation the helper will apply to the request it gets.
        let inner = serde_json::to_vec(&frame.request).map_err(|_| invalid())?;
        parse_request(&inner)?;
        Ok(frame)
    }

    /// The request the helper receives: the launcher's own identity and handles.
    ///
    /// `parent` is the **launcher** process - the process whose exit the helper
    /// waits for. The result is validated again before it is sent.
    pub fn into_helper_request(
        self,
        parent: ParentIdentity,
        parent_handle: u64,
        release_handle: u64,
    ) -> InstallResult<Vec<u8>> {
        let mut request = self.request;
        request.parent = parent;
        request.parent_handle = parent_handle;
        request.release_handle = release_handle;
        let bytes = serde_json::to_vec(&request).map_err(|_| invalid())?;
        parse_request(&bytes)?;
        Ok(bytes)
    }
}

/// The one frame the launcher writes to its standard output before it exits.
///
/// It names the helper process so the installer can wait for its exit without
/// holding a handle the launcher created: `(pid, start)` is an identity, so a
/// recycled pid is recognised and treated as "the helper is gone".
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LaunchStatus {
    /// [`LAUNCH_SCHEMA_VERSION`].
    pub schema: u32,
    /// [`LAUNCH_STATUS_MAGIC`].
    pub magic: String,
    /// The helper's process identifier.
    pub helper_pid: u32,
    /// The helper's OS start marker.
    pub helper_start: u64,
}

impl LaunchStatus {
    /// The status frame for a spawned helper.
    pub fn new(helper_pid: u32, helper_start: u64) -> Self {
        Self {
            schema: LAUNCH_SCHEMA_VERSION,
            magic: LAUNCH_STATUS_MAGIC.to_owned(),
            helper_pid,
            helper_start,
        }
    }

    /// Encode the frame.
    pub fn encode(&self) -> InstallResult<Vec<u8>> {
        serde_json::to_vec(self).map_err(|_| invalid())
    }

    /// Parse and validate one status frame.
    pub fn parse(bytes: &[u8]) -> InstallResult<Self> {
        if bytes.is_empty() || bytes.len() > MAX_LAUNCH_STATUS_BYTES {
            return Err(pending());
        }
        let status: Self = serde_json::from_slice(bytes).map_err(|_| pending())?;
        if status.schema != LAUNCH_SCHEMA_VERSION
            || status.magic != LAUNCH_STATUS_MAGIC
            || status.helper_pid == 0
        {
            return Err(pending());
        }
        Ok(status)
    }
}

fn invalid() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidInput)
}

fn pending() -> InstallFailure {
    InstallFailure::new(FailureCode::CleanupPending)
}
