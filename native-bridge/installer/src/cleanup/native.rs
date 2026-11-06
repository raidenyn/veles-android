//! Native cleanup channel and self-verification (Windows / macOS only).
//!
//! The installer hands the helper three inherited handles: standard input (the
//! bounded request), standard output (the authenticated completion), one
//! **parent reference** and, on Windows, one **release pipe**. The helper:
//!
//! * proves standard input/output are pipes, not files or a console;
//! * authenticates the *inherited parent reference* as the recorded parent
//!   (`GetProcessId` + `GetProcessTimes` on Windows, `LOCAL_PEERPID` on macOS)
//!   instead of reopening a pid — including the case where the parent already
//!   exited, which is accepted only for the recorded pid because a live process
//!   with that pid is compared by start marker;
//! * waits until the parent **exits**: a real process exit is observed as an OS
//!   event (`WaitForSingleObject` on Windows, a `kqueue` `EVFILT_PROC`/`NOTE_EXIT`
//!   registration on macOS taken **before** the release byte is read). The one
//!   release byte on the release pipe / socketpair, or a closed channel, is a
//!   protocol step only ("the parent will not mutate again"): it is never
//!   reported as "the parent exited" and never permits deletion. If the exit
//!   registration cannot be taken the run fails closed with `cleanup-pending`;
//! * bounds every read, write, hash and signature assessment by the run
//!   deadline, with worker threads for the operations the OS may block for longer
//!   than the deadline inside a single call;
//! * removes exact owned identities through the adapter's anchored
//!   [`CleanupRoot`](crate::platform::MacosCleanupRoot) implementation.
//!
//! The helper never deletes its own image: it only exits, and the installer
//! deletes the staged image afterwards (see `remove_helper_after_exit` and
//! [`spawn_helper`], the installer side of the handoff).

use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::time::Instant;

use sha2::{Digest as _, Sha256};

use super::{
    CleanupChannel, CleanupCompletion, CleanupFailure, CleanupFileSystem, CleanupStatus,
    CleanupTree, HelperRun, MAX_CLEANUP_FRAME_BYTES, MAX_CLEANUP_TIMEOUT, ParentEvidence,
    ParentIdentity, ParentState, RemoveOutcome, accept_completion, execute_cleanup, parse_request,
};
use crate::model::Digest;

/// Run one cleanup capability on the native channel.
pub(super) fn run() -> i32 {
    let own = std::env::current_exe().unwrap_or_default();
    let mut channel = native_channel();
    let mut fs = native_fs(own);
    let completion: CleanupCompletion = execute_cleanup(&mut channel, &mut fs, MAX_CLEANUP_TIMEOUT);
    i32::from(completion.status != CleanupStatus::Complete)
}

/// SHA-256 of one regular file, stopped at `deadline`.
///
/// The helper hashes its own image here; an image large enough to outlast the
/// cleanup budget must fail closed, not extend the run. The reads happen in
/// bounded chunks that check the deadline between them **on a worker thread**,
/// and the caller waits with `recv_timeout`: a read that blocks inside the OS
/// (a stalled volume, a filter driver) therefore cannot extend the run past its
/// bound. The worker is abandoned at the deadline and cannot outlive the helper,
/// which exits immediately afterwards.
fn hash_bounded(path: &Path, deadline: Instant) -> io::Result<Digest> {
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return Err(io::Error::other("cleanup deadline elapsed"));
    }
    let (sender, receiver) = std::sync::mpsc::channel();
    let path = path.to_path_buf();
    let spawned = std::thread::Builder::new()
        .name("cleanup-hash".to_owned())
        .spawn(move || {
            let outcome = (|| {
                let mut file = crate::platform::open_no_follow(&path)?;
                if !file.metadata()?.is_file() {
                    return Err(io::Error::other("not a regular file"));
                }
                let mut hasher = Sha256::new();
                let mut buffer = [0u8; 64 * 1024];
                loop {
                    if Instant::now() >= deadline {
                        return Err(io::Error::other("cleanup deadline elapsed"));
                    }
                    let read = file.read(&mut buffer)?;
                    if read == 0 {
                        break;
                    }
                    hasher.update(&buffer[..read]);
                }
                Ok(Digest::from_bytes(hasher.finalize().into()))
            })();
            let _ = sender.send(outcome);
        });
    if spawned.is_err() {
        return Err(io::Error::other("could not start the hash worker"));
    }
    match receiver.recv_timeout(remaining) {
        Ok(outcome) => outcome,
        Err(_) => Err(io::Error::other("cleanup deadline elapsed")),
    }
}

/// The OS signature assessment, run in a worker so a stalled verifier cannot
/// outlast the cleanup budget.
fn signature_bounded(
    path: &Path,
    deadline: Instant,
    assess: impl Fn(&Path) -> Result<String, CleanupFailure> + Send + 'static,
) -> Result<String, CleanupFailure> {
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return Err(CleanupFailure);
    }
    let (sender, receiver) = std::sync::mpsc::channel();
    let path = path.to_path_buf();
    let spawned = std::thread::Builder::new()
        .name("cleanup-signature".to_owned())
        .spawn(move || {
            let _ = sender.send(assess(&path));
        });
    if spawned.is_err() {
        return Err(CleanupFailure);
    }
    match receiver.recv_timeout(remaining) {
        Ok(result) => result,
        Err(_) => Err(CleanupFailure),
    }
}

#[cfg(windows)]
use windows_impl::{WindowsChannel, WindowsCleanupFs};

#[cfg(windows)]
fn native_channel() -> WindowsChannel {
    WindowsChannel
}

#[cfg(target_os = "macos")]
use macos_impl::{MacosChannel, MacosCleanupFs};

#[cfg(target_os = "macos")]
fn native_channel() -> MacosChannel {
    MacosChannel::new()
}

#[cfg(windows)]
fn native_fs(own: PathBuf) -> WindowsCleanupFs {
    WindowsCleanupFs::new(own)
}

#[cfg(target_os = "macos")]
fn native_fs(own: PathBuf) -> MacosCleanupFs {
    MacosCleanupFs { own_path: own }
}

// ---------------------------------------------------------------------------
// Windows
// ---------------------------------------------------------------------------

#[cfg(windows)]
mod windows_impl {
    use super::*;
    use std::os::windows::io::AsRawHandle;
    use windows::Win32::Foundation::{
        CloseHandle, DUPLICATE_HANDLE_OPTIONS, DuplicateHandle, ERROR_BROKEN_PIPE, HANDLE,
        WAIT_OBJECT_0, WAIT_TIMEOUT,
    };
    use windows::Win32::Storage::FileSystem::{FILE_TYPE_PIPE, GetFileType, ReadFile, WriteFile};
    use windows::Win32::System::Pipes::{CreatePipe, PeekNamedPipe};
    use windows::Win32::System::Threading::{
        CreateProcessW, DeleteProcThreadAttributeList, EXTENDED_STARTUPINFO_PRESENT,
        GetCurrentProcess, GetCurrentProcessId, GetExitCodeProcess, GetProcessTimes, INFINITE,
        InitializeProcThreadAttributeList, LPPROC_THREAD_ATTRIBUTE_LIST,
        PROC_THREAD_ATTRIBUTE_HANDLE_LIST, PROCESS_INFORMATION, STARTF_USESTDHANDLES,
        STARTUPINFOEXW, UpdateProcThreadAttribute, WaitForSingleObject,
    };

    /// The start marker of one process, read from **the inherited handle**.
    fn handle_start(handle: HANDLE) -> Option<u64> {
        let mut creation = Default::default();
        let mut exit = Default::default();
        let mut kernel = Default::default();
        let mut user = Default::default();
        // SAFETY: the handle is live and every out-parameter is a live FILETIME.
        unsafe { GetProcessTimes(handle, &mut creation, &mut exit, &mut kernel, &mut user) }
            .ok()?;
        Some((u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime))
    }

    fn handle_of(handle: u64) -> Option<HANDLE> {
        let handle = HANDLE(handle as *mut core::ffi::c_void);
        (!handle.is_invalid()).then_some(handle)
    }

    /// The start marker of **this** process.
    pub(super) fn process_start(_pid: u32) -> Option<u64> {
        // SAFETY: `GetCurrentProcess` is a pseudo-handle that needs no close.
        // SAFETY: `GetCurrentProcess` is a pseudo-handle.
        handle_start(unsafe { GetCurrentProcess() })
    }

    /// Whether `handle` is a pipe (`FILE_TYPE_PIPE`).
    fn is_pipe(handle: HANDLE) -> bool {
        // SAFETY: the handle is a live standard or inherited handle.
        unsafe { GetFileType(handle) == FILE_TYPE_PIPE }
    }

    /// Read one bounded request/completion frame from a pipe, polling so the
    /// deadline is honoured and stopping at EOF.
    fn read_frame_bounded(handle: HANDLE, limit: usize, deadline: Instant) -> io::Result<Vec<u8>> {
        let mut bytes = Vec::new();
        let mut buffer = [0u8; 4096];
        loop {
            if bytes.len() > limit {
                return Err(io::Error::other("frame is too large"));
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(io::Error::other("cleanup deadline elapsed"));
            }
            // `PeekNamedPipe` is the documented, non-consuming way to ask a pipe
            // whether the writer has produced bytes and whether it is still
            // connected; waiting on a *synchronous* pipe handle is not a reliable
            // data-available signal, so the wait is a bounded poll.
            let mut available = 0u32;
            // SAFETY: `handle` is a live pipe and `available` is a live
            // out-parameter; no data buffer is supplied, so nothing is consumed.
            let peeked =
                unsafe { PeekNamedPipe(handle, None, 0, None, Some(&mut available), None) };
            if peeked.is_err() {
                let error = io::Error::last_os_error();
                if error.raw_os_error() == Some(ERROR_BROKEN_PIPE.0 as i32) {
                    // The writer closed: this is the end of the frame.
                    return Ok(bytes);
                }
                return Err(error);
            }
            if available == 0 {
                std::thread::sleep(remaining.min(std::time::Duration::from_millis(10)));
                continue;
            }
            let mut read = 0u32;
            let mut target = &mut buffer[..];
            // SAFETY: the handle is live, `target` is a live output buffer and no
            // overlapped operation is requested.
            let result = unsafe { ReadFile(handle, Some(&mut target), Some(&mut read), None) };
            if result.is_err() {
                let error = io::Error::last_os_error();
                if error.raw_os_error() == Some(ERROR_BROKEN_PIPE.0 as i32) {
                    return Ok(bytes);
                }
                return Err(error);
            }
            if read == 0 {
                return Ok(bytes);
            }
            bytes.extend_from_slice(&buffer[..read as usize]);
        }
    }

    /// Write one frame with the deadline honoured.
    ///
    /// The pipe can hold more than one buffer, but a reader that stopped reading
    /// would block `WriteFile` forever. The write therefore runs on a worker
    /// thread and the caller waits with `recv_timeout`: the queue is exactly one
    /// frame and a blocked write can never extend the run. The worker owns its
    /// **own duplicate** of the handle, so a timeout never leaves the caller
    /// holding a handle it cannot close (and closing the caller's copy never
    /// pulls the handle out from under a blocked `WriteFile`).
    ///
    /// The duplicate is an RAII `Handle` moved into the closure, not a raw
    /// integer: if `spawn` fails the closure — and with it the handle — is
    /// dropped here, so a refused thread creation cannot leak a handle that no
    /// worker would ever close.
    fn write_frame_bounded(handle: HANDLE, frame: &[u8], deadline: Instant) -> io::Result<()> {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(io::Error::other("cleanup deadline elapsed"));
        }
        let worker = duplicate_for_worker(handle)?;
        let (sender, receiver) = std::sync::mpsc::channel();
        let frame = frame.to_vec();
        let spawned = std::thread::Builder::new()
            .name("cleanup-write".to_owned())
            .spawn(move || {
                let handle = worker;
                let outcome = (|| {
                    let mut written = 0usize;
                    while written < frame.len() {
                        // One chunk per call: a fragment below PIPE_BUF is atomic.
                        let chunk = (frame.len() - written).min(1024);
                        let mut count = 0u32;
                        // SAFETY: the handle is owned by this worker and the chunk
                        // is inside `frame`.
                        let result = unsafe {
                            WriteFile(
                                handle.raw(),
                                Some(&frame[written..written + chunk]),
                                Some(&mut count),
                                None,
                            )
                        };
                        if result.is_err() || count == 0 {
                            return Err(());
                        }
                        written += count as usize;
                    }
                    Ok(())
                })();
                // `handle` is an RAII owner: it closes the duplicate exactly once
                // here, on success and on every error path.
                let _ = sender.send(outcome);
            });
        if spawned.is_err() {
            return Err(io::Error::other("could not start the write worker"));
        }
        match receiver.recv_timeout(remaining) {
            Ok(Ok(())) => Ok(()),
            Ok(Err(())) => Err(io::Error::other("the completion channel refused the frame")),
            Err(_) => Err(io::Error::other("cleanup deadline elapsed")),
        }
    }

    /// Wait until the parent exits or releases the capability on its pipe.
    ///
    /// The process exit is a real OS event (`WaitForSingleObject`) and is the
    /// only state that allows the helper to delete anything. The release byte is
    /// a **protocol** step that means "the parent will not mutate again", not
    /// that it exited: it is reported as [`ParentState::Released`], which the run
    /// treats as a pending `ParentAlive`.
    fn wait_released(process: HANDLE, release: HANDLE, deadline: Instant) -> ParentState {
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return ParentState::Alive;
            }
            // The process exit is the only exit evidence, and it is also the
            // crashed-parent case: the capability is released even if the release
            // pipe was never written.
            // SAFETY: the handle is inherited and names the parent process.
            match unsafe { WaitForSingleObject(process, 0) } {
                WAIT_OBJECT_0 => return ParentState::Exited,
                WAIT_TIMEOUT => {}
                _ => return ParentState::Unavailable,
            }
            match release_state(release) {
                Ok(true) => {
                    // The release is a protocol step. Confirm the process state
                    // once more (a crash often breaks the pipe in the same
                    // instant) and otherwise report the still-alive parent.
                    // SAFETY: the handle is inherited and names the parent.
                    return match unsafe { WaitForSingleObject(process, 0) } {
                        WAIT_OBJECT_0 => ParentState::Exited,
                        WAIT_TIMEOUT => ParentState::Released,
                        _ => ParentState::Unavailable,
                    };
                }
                Ok(false) => {}
                Err(_) => return ParentState::Unavailable,
            }
            std::thread::sleep(remaining.min(std::time::Duration::from_millis(10)));
        }
    }

    /// Whether the parent released the capability on the release pipe.
    ///
    /// The installer writes one byte as its last act before it stops mutating, so
    /// a byte is "released"; a broken pipe means the parent exited (or closed)
    /// before writing it, which is also "released". Neither is exit evidence —
    /// the process handle is.
    fn release_state(release: HANDLE) -> io::Result<bool> {
        let mut available = 0u32;
        // SAFETY: `release` is the inherited read end of the release pipe and
        // `available` is a live out-parameter.
        let peeked = unsafe { PeekNamedPipe(release, None, 0, None, Some(&mut available), None) };
        if peeked.is_err() {
            let error = io::Error::last_os_error();
            if error.raw_os_error() == Some(ERROR_BROKEN_PIPE.0 as i32) {
                return Ok(true);
            }
            return Err(error);
        }
        Ok(available != 0)
    }

    pub(crate) struct WindowsChannel;

    impl CleanupChannel for WindowsChannel {
        fn is_inherited_pipe(&self) -> bool {
            let input = std::io::stdin().as_raw_handle();
            let output = std::io::stdout().as_raw_handle();
            // SAFETY: both are valid standard handles for this process.
            is_pipe(HANDLE(input)) && is_pipe(HANDLE(output))
        }

        fn parent_handle(&mut self, handle: u64) -> Option<ParentEvidence> {
            let handle = handle_of(handle)?;
            // SAFETY: the handle is inherited and names the parent for this call.
            let pid = unsafe { GetProcessId(handle) };
            if pid == 0 {
                return None;
            }
            // A process handle stays queryable after the process exits, so the
            // start marker is always available here (no `Exited` fallback).
            Some(ParentEvidence::Exact(ParentIdentity {
                pid,
                start: handle_start(handle)?,
            }))
        }

        fn parent_state(
            &mut self,
            parent_handle: u64,
            release_handle: u64,
            deadline: Instant,
        ) -> ParentState {
            let Some(process) = handle_of(parent_handle) else {
                return ParentState::Unavailable;
            };
            let Some(release) = handle_of(release_handle) else {
                // Windows requires the dedicated release pipe.
                return ParentState::Unavailable;
            };
            if !is_pipe(release) {
                return ParentState::Unavailable;
            }
            wait_released(process, release, deadline)
        }

        fn read_request(&mut self, limit: usize, deadline: Instant) -> io::Result<Vec<u8>> {
            read_frame_bounded(HANDLE(std::io::stdin().as_raw_handle()), limit, deadline)
        }

        fn write_completion(&mut self, frame: &[u8], deadline: Instant) -> io::Result<()> {
            write_frame_bounded(HANDLE(std::io::stdout().as_raw_handle()), frame, deadline)
        }
    }

    pub(crate) struct WindowsCleanupFs {
        pub(crate) own_path: PathBuf,
    }

    impl WindowsCleanupFs {
        pub(crate) fn new(own_path: PathBuf) -> Self {
            Self { own_path }
        }
    }

    struct WindowsTree(crate::platform::WindowsCleanupRoot);

    impl CleanupTree for WindowsTree {
        fn remove_file(
            &mut self,
            relative: &str,
            size: u64,
            sha256: Digest,
            deadline: Instant,
        ) -> RemoveOutcome {
            self.0.remove_file(relative, size, sha256, deadline)
        }

        fn remove_link(
            &mut self,
            relative: &str,
            size: u64,
            sha256: Digest,
            deadline: Instant,
        ) -> RemoveOutcome {
            self.0.remove_link(relative, size, sha256, deadline)
        }

        fn remove_directory(&mut self, relative: &str) -> RemoveOutcome {
            self.0.remove_directory(relative)
        }
    }

    impl CleanupFileSystem for WindowsCleanupFs {
        fn own_path(&self) -> io::Result<PathBuf> {
            Ok(self.own_path.clone())
        }

        fn hash_file(&self, path: &Path, deadline: Instant) -> io::Result<Digest> {
            hash_bounded(path, deadline)
        }

        fn signature(&self, path: &Path, deadline: Instant) -> Result<String, CleanupFailure> {
            signature_bounded(path, deadline, |path| {
                crate::platform::windows_verify_own_signature(path).map_err(|_| CleanupFailure)
            })
        }

        fn open_root(&mut self, root: &Path) -> Result<Box<dyn CleanupTree + '_>, CleanupFailure> {
            let root =
                crate::platform::WindowsCleanupRoot::open(root).map_err(|_| CleanupFailure)?;
            Ok(Box::new(WindowsTree(root)))
        }
    }

    // -----------------------------------------------------------------------
    // The installer side: spawn the staged helper, release, wait, authenticate.
    // -----------------------------------------------------------------------

    /// One inherited-handle run of the staged helper.
    ///
    /// The installer creates three pipes and one duplicated process handle,
    /// passes **only** those to the child (`PROC_THREAD_ATTRIBUTE_HANDLE_LIST`),
    /// writes the request, closes the release pipe as its last act, waits for the
    /// exit and reads the single completion frame. Every step is bounded by the
    /// run deadline.
    pub(super) fn spawn_helper(
        run: &HelperRun<'_>,
    ) -> crate::model::InstallResult<CleanupCompletion> {
        let pending =
            || crate::model::InstallFailure::new(crate::model::FailureCode::CleanupPending);
        let deadline = Instant::now() + run.timeout.min(MAX_CLEANUP_TIMEOUT);
        let parent = process_start(0)
            .map(|start| ParentIdentity {
                pid: unsafe { GetCurrentProcessId() },
                start,
            })
            .ok_or_else(pending)?;

        // Inheritable child ends, non-inheritable parent ends. Each handle is
        // owned (RAII) the instant it exists, so a failure at any later step
        // cannot leak an earlier pipe end.
        let (child_request, parent_request) = create_pipe().map_err(|_| pending())?;
        let (parent_completion, child_completion) = create_pipe().map_err(|_| pending())?;
        let (child_release, parent_release) = create_pipe().map_err(|_| pending())?;
        parent_request.disinherit();
        parent_completion.disinherit();
        parent_release.disinherit();

        // The parent process handle: identity for the helper, never usable to
        // terminate it (query + synchronize only).
        let parent_process = duplicate_process_handle().map_err(|_| pending())?;

        let request = run.request(
            parent,
            parent_process.raw().0 as u64,
            child_release.raw().0 as u64,
        );
        let frame = serde_json::to_vec(&request).map_err(|_| pending())?;
        let frame = {
            // The installer validates exactly what it is about to send.
            parse_request(&frame)?;
            frame
        };

        // The child inherits exactly these handles. The standard handles are part
        // of the list too: with `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` nothing else
        // is inherited, and the helper reads/writes its request and completion on
        // the very descriptors named here.
        let child = spawn_child(
            run.helper_path,
            child_request.raw(),
            child_completion.raw(),
            &[
                child_request.raw(),
                child_completion.raw(),
                child_release.raw(),
                parent_process.raw(),
            ],
        )
        .map_err(|_| pending())?;
        // The child's ends are now duplicated into the child: close ours.
        drop(child_request);
        drop(child_completion);
        drop(child_release);

        // Request out, then release: after this the installer only waits. The
        // write worker owns its own duplicate of the handle, so dropping our copy
        // here (including on a timeout) is safe.
        if write_frame_bounded(parent_request.raw(), &frame, deadline).is_err() {
            return Err(pending());
        }
        drop(parent_request);
        // One byte on the release pipe is the release (it cannot block: one byte
        // into an empty pipe buffer), and closing the pipe afterwards is safe
        // because the identity comes from the process handle, not the pipe.
        if write_frame_bounded(parent_release.raw(), &[1u8], deadline).is_err() {
            return Err(pending());
        }
        drop(parent_release);

        let mut process = child;
        let status = wait_child(process.handle(), deadline);
        if status != ParentState::Exited {
            // The helper did not exit inside the bound: nothing is claimed and the
            // image is left alone (it is still mapped).
            let _ = process.wait(0);
            return Err(pending());
        }
        let frame = read_frame_bounded(parent_completion.raw(), MAX_CLEANUP_FRAME_BYTES, deadline)
            .map_err(|_| pending())?;
        let remaining_ms = u32::try_from(
            deadline
                .saturating_duration_since(Instant::now())
                .as_millis(),
        )
        .unwrap_or(u32::MAX - 1);
        let code = process.wait(remaining_ms);
        let completion = accept_completion(&frame, run.capability, run.transaction)?;
        // A successful completion must also be the helper's own exit status.
        if completion.status == CleanupStatus::Complete && code.unwrap_or(1) != 0 {
            return Err(pending());
        }
        Ok(completion)
    }

    /// One owned Win32 handle, closed exactly once on every path.
    ///
    /// Every `CreatePipe`/`DuplicateHandle` result is wrapped the moment it
    /// exists, so a later failure in the setup cannot leak an earlier pipe end.
    struct Handle(HANDLE);

    // SAFETY: a Win32 handle is a process-wide reference to a kernel object; using
    // it (and closing it) from another thread is valid, and ownership stays
    // unique because the value is moved into the worker, never shared or copied.
    unsafe impl Send for Handle {}

    impl Handle {
        fn raw(&self) -> HANDLE {
            self.0
        }

        /// Clear `HANDLE_FLAG_INHERIT`: this end must never reach the child.
        fn disinherit(&self) {
            use windows::Win32::Foundation::{HANDLE_FLAG_INHERIT, SetHandleInformation};
            // SAFETY: the handle is live and owned by this value.
            let _ =
                unsafe { SetHandleInformation(self.0, HANDLE_FLAG_INHERIT.0, Default::default()) };
        }
    }

    impl Drop for Handle {
        fn drop(&mut self) {
            // SAFETY: the handle is owned by this value.
            unsafe {
                let _ = CloseHandle(self.0);
            }
        }
    }

    /// Duplicate one pipe handle for a write worker thread.
    ///
    /// The worker may still be blocked inside `WriteFile` when the caller's
    /// deadline expires. Giving it its own duplicate (closed by the worker on
    /// every path) means the caller can drop its handle without closing the
    /// object underneath the worker, and no handle is leaked to avoid that.
    fn duplicate_for_worker(handle: HANDLE) -> io::Result<Handle> {
        use windows::Win32::Foundation::GENERIC_WRITE;

        let mut duplicate = HANDLE::default();
        let current = unsafe { GetCurrentProcess() };
        // SAFETY: the source handle is live, `duplicate` is a live out-parameter
        // and the current process is the target process. The duplicate carries
        // only the write access the frame write uses.
        unsafe {
            DuplicateHandle(
                current,
                handle,
                current,
                &mut duplicate,
                GENERIC_WRITE.0,
                false,
                DUPLICATE_HANDLE_OPTIONS(0),
            )
        }
        .map_err(|_| io::Error::other("could not duplicate the frame handle"))?;
        Ok(Handle(duplicate))
    }

    /// One anonymous pipe: `(read, write)`, both inheritable on creation and
    /// owned from the moment they exist.
    fn create_pipe() -> windows::core::Result<(Handle, Handle)> {
        use windows::Win32::Security::SECURITY_ATTRIBUTES;
        let attributes = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: std::ptr::null_mut(),
            bInheritHandle: TRUE,
        };
        let mut read = HANDLE::default();
        let mut write = HANDLE::default();
        // SAFETY: both are live out-parameters and `attributes` outlives the call.
        unsafe { CreatePipe(&mut read, &mut write, Some(&attributes), 0) }?;
        Ok((Handle(read), Handle(write)))
    }

    /// `SYNCHRONIZE` process access (0x0010_0000): wait on the process only.
    const SYNCHRONIZE_ACCESS: u32 = 0x0010_0000;

    /// A `PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE` duplicate of this
    /// process, inheritable by the child.
    ///
    /// The access mask is explicit: `DUPLICATE_SAME_ACCESS` would copy the
    /// pseudo-handle's full access, handing the helper termination and memory
    /// rights over the installer that it never needs. Query and synchronize are
    /// exactly what the helper's identity check and wait use.
    fn duplicate_process_handle() -> windows::core::Result<Handle> {
        use windows::Win32::Foundation::HANDLE_FLAG_INHERIT;
        use windows::Win32::System::Threading::PROCESS_QUERY_LIMITED_INFORMATION;
        let mut duplicate = HANDLE::default();
        // SAFETY: `GetCurrentProcess` is a pseudo-handle that needs no close.
        let current = unsafe { GetCurrentProcess() };
        // SAFETY: the source handle is the current-process pseudo handle and
        // `duplicate` is a live out-parameter.
        unsafe {
            DuplicateHandle(
                current,
                current,
                current,
                &mut duplicate,
                PROCESS_QUERY_LIMITED_INFORMATION.0 | SYNCHRONIZE_ACCESS,
                true,
                DUPLICATE_HANDLE_OPTIONS(0),
            )
        }?;
        // Own the duplicate immediately: a refused `SetHandleInformation` closes
        // it on the error path instead of leaking the handle.
        let handle = Handle(duplicate);
        // SAFETY: the handle is live and owned by `handle`.
        unsafe { SetHandleInformation(handle.raw(), HANDLE_FLAG_INHERIT.0, HANDLE_FLAG_INHERIT) }?;
        Ok(handle)
    }

    /// Start the helper with exactly the listed inherited handles.
    fn spawn_child(
        helper: &Path,
        stdin: HANDLE,
        stdout: HANDLE,
        inheritable: &[HANDLE],
    ) -> windows::core::Result<Child> {
        use windows::Win32::System::Threading::PROCESS_CREATION_FLAGS;

        let application = wide(helper.as_os_str());
        let mut command_line = wide(helper.as_os_str());
        if command_line.is_empty() {
            return Err(windows::core::Error::empty());
        }
        // The command line has no arguments: the request is the inherited pipe.
        let mut info = STARTUPINFOEXW::default();
        info.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
        info.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        info.StartupInfo.hStdInput = stdin;
        info.StartupInfo.hStdOutput = stdout;
        info.StartupInfo.hStdError = HANDLE(std::ptr::null_mut());

        // The attribute list names exactly the handles the child inherits.
        let mut size = 0usize;
        // SAFETY: a `None` list with a zero length only asks for the size.
        let _ = unsafe { InitializeProcThreadAttributeList(None, 1, None, &mut size) };
        let mut storage = vec![0usize; size.div_ceil(size_of::<usize>())];
        let list = LPPROC_THREAD_ATTRIBUTE_LIST(storage.as_mut_ptr().cast());
        // SAFETY: `storage` is at least `size` bytes and the out-parameter is live.
        unsafe { InitializeProcThreadAttributeList(Some(list), 1, None, &mut size) }
            .map_err(|_| windows::core::Error::empty())?;
        let attributes = Attributes { list, storage };
        // SAFETY: `inheritable` is a live slice of live handles and the attribute
        // list was initialized for exactly one attribute.
        unsafe {
            UpdateProcThreadAttribute(
                attributes.list,
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                Some(inheritable.as_ptr() as *const core::ffi::c_void),
                std::mem::size_of_val(inheritable),
                None,
                None,
            )
        }?;
        let mut process = PROCESS_INFORMATION::default();
        // SAFETY: every pointer is live for the call; the command line is a
        // mutable NUL-terminated buffer and the child inherits only the listed
        // handles.
        unsafe {
            CreateProcessW(
                windows::core::PCWSTR(application.as_ptr()),
                Some(windows::core::PWSTR(command_line.as_mut_ptr())),
                None,
                None,
                true,
                PROCESS_CREATION_FLAGS(EXTENDED_STARTUPINFO_PRESENT.0),
                None,
                windows::core::PCWSTR::null(),
                &info.StartupInfo,
                &mut process,
            )
        }?;
        drop(attributes);
        // SAFETY: the thread handle was just created and is owned here.
        unsafe {
            let _ = CloseHandle(process.hThread);
        }
        Ok(Child {
            process: process.hProcess,
        })
    }

    /// The attribute-list storage and the list pointer into it.
    struct Attributes {
        list: LPPROC_THREAD_ATTRIBUTE_LIST,
        storage: Vec<usize>,
    }

    impl Drop for Attributes {
        fn drop(&mut self) {
            // SAFETY: the list was initialized into `storage` above.
            unsafe { DeleteProcThreadAttributeList(self.list) };
            let _ = &self.storage;
        }
    }

    /// One spawned helper process.
    struct Child {
        process: HANDLE,
    }

    impl Child {
        fn handle(&self) -> HANDLE {
            self.process
        }

        /// Wait for the exit code, bounded; `None` when it did not exit in time.
        fn wait(&mut self, timeout_ms: u32) -> Option<u32> {
            // SAFETY: the handle is a live process handle.
            match unsafe { WaitForSingleObject(self.process, timeout_ms) } {
                WAIT_OBJECT_0 => {
                    let mut code = 0u32;
                    // SAFETY: the process exited and `code` is live.
                    unsafe { GetExitCodeProcess(self.process, &mut code) }.ok()?;
                    Some(code)
                }
                _ => None,
            }
        }
    }

    impl Drop for Child {
        fn drop(&mut self) {
            // SAFETY: the handle is owned by this value.
            unsafe {
                let _ = CloseHandle(self.process);
            }
        }
    }

    fn wait_child(process: HANDLE, deadline: Instant) -> ParentState {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return ParentState::Alive;
        }
        let millis = u32::try_from(remaining.as_millis()).unwrap_or(INFINITE);
        // SAFETY: the handle is a live process handle.
        match unsafe { WaitForSingleObject(process, millis) } {
            WAIT_OBJECT_0 => ParentState::Exited,
            WAIT_TIMEOUT => ParentState::Alive,
            _ => ParentState::Unavailable,
        }
    }

    fn wide(text: &std::ffi::OsStr) -> Vec<u16> {
        use std::os::windows::ffi::OsStrExt;

        text.encode_wide().chain([0]).collect()
    }

    use windows::Win32::Foundation::{SetHandleInformation, TRUE};
    use windows::Win32::System::Threading::GetProcessId;
}

// ---------------------------------------------------------------------------
// macOS
// ---------------------------------------------------------------------------

#[cfg(target_os = "macos")]
mod macos_impl {
    use super::*;
    use std::ffi::c_int;
    use std::os::unix::io::{AsRawFd, FromRawFd, OwnedFd, RawFd};
    use std::os::unix::process::CommandExt;

    const SOL_LOCAL: c_int = 0;
    const LOCAL_PEERPID: c_int = 0x002;
    const POLLIN: i16 = 0x0001;
    const POLLOUT: i16 = 0x0004;
    const POLLERR: i16 = 0x0008;
    const POLLHUP: i16 = 0x0010;
    const EINTR: i32 = 4;
    const ESRCH: i32 = 3;
    const AF_UNIX: c_int = 1;
    const SOCK_STREAM: c_int = 1;
    /// Darwin `EVFILT_PROC`.
    const EVFILT_PROC: i16 = -5;
    /// `kevent(2)` flags.
    const EV_ADD: u16 = 0x0001;
    const EV_ENABLE: u16 = 0x0004;
    const EV_ONESHOT: u16 = 0x0010;
    const EV_ERROR: u16 = 0x4000;
    const EV_EOF: u16 = 0x8000;
    /// `EVFILT_PROC` flag: the watched process exited.
    const NOTE_EXIT: u32 = 0x8000_0000;
    /// The one-byte release signal the installer sends on the socketpair.
    const RELEASE: [u8; 1] = [0x01];
    /// The fixed descriptor the child receives its release/identity end on.
    const CHILD_HANDLE_FD: RawFd = 3;

    #[repr(C)]
    struct PollFd {
        fd: c_int,
        events: i16,
        revents: i16,
    }

    /// `struct kevent` on 64-bit Darwin (32 bytes).
    #[repr(C)]
    #[derive(Clone, Copy)]
    struct Kevent {
        ident: usize,
        filter: i16,
        flags: u16,
        fflags: u32,
        data: isize,
        udata: *mut std::ffi::c_void,
    }

    impl Default for Kevent {
        fn default() -> Self {
            Self {
                ident: 0,
                filter: 0,
                flags: 0,
                fflags: 0,
                data: 0,
                udata: std::ptr::null_mut(),
            }
        }
    }

    #[repr(C)]
    struct Timespec {
        tv_sec: i64,
        tv_nsec: i64,
    }

    unsafe extern "C" {
        fn getsockopt(
            socket: c_int,
            level: c_int,
            name: c_int,
            value: *mut std::ffi::c_void,
            length: *mut u32,
        ) -> c_int;
        fn poll(fds: *mut PollFd, count: u64, timeout: c_int) -> c_int;
        fn read(fd: c_int, buffer: *mut u8, count: usize) -> isize;
        fn write(fd: c_int, buffer: *const u8, count: usize) -> isize;
        fn socketpair(domain: c_int, kind: c_int, protocol: c_int, fds: *mut c_int) -> c_int;
        fn dup2(from: c_int, to: c_int) -> c_int;
        fn close(fd: c_int) -> c_int;
        fn fcntl(fd: c_int, command: c_int, ...) -> c_int;
        fn kqueue() -> c_int;
        fn kevent(
            kqueue: c_int,
            changelist: *const Kevent,
            nchanges: c_int,
            eventlist: *mut Kevent,
            nevents: c_int,
            timeout: *const Timespec,
        ) -> c_int;
    }

    /// `fcntl(2)`'s `F_SETFD` / `FD_CLOEXEC` on Darwin.
    const F_SETFD: c_int = 2;
    const FD_CLOEXEC: c_int = 1;

    /// The start marker of one process.
    pub(super) fn process_start(pid: u32) -> Option<u64> {
        crate::platform::macos_process_start(pid)
    }

    fn descriptor_is_pipe(fd: c_int) -> bool {
        use std::mem::ManuallyDrop;
        use std::os::unix::fs::FileTypeExt;

        // SAFETY: the descriptor is borrowed for the duration of the call.
        let file = ManuallyDrop::new(unsafe { std::fs::File::from_raw_fd(fd) });
        match file.metadata() {
            Ok(metadata) => metadata.file_type().is_fifo() || metadata.file_type().is_socket(),
            Err(_) => false,
        }
    }

    fn peer_pid(fd: c_int) -> Option<u32> {
        let mut pid: c_int = 0;
        let mut length = std::mem::size_of::<c_int>() as u32;
        // SAFETY: the descriptor is the inherited liveness socket and
        // `pid`/`length` are live out-parameters.
        let result = unsafe {
            getsockopt(
                fd,
                SOL_LOCAL,
                LOCAL_PEERPID,
                (&mut pid as *mut c_int).cast(),
                &mut length,
            )
        };
        if result != 0 || pid <= 0 {
            return None;
        }
        Some(pid as u32)
    }

    /// One `kqueue` registration for the peer process's **real exit event**.
    ///
    /// The registration is taken in `parent_handle`, i.e. before the helper
    /// consumes the release byte, so an exit that happens around the protocol
    /// step cannot be missed. The release byte is never treated as exit evidence;
    /// this event is.
    struct ExitWatch {
        kqueue: OwnedFd,
        /// An exit was delivered at registration time.
        delivered: bool,
    }

    fn is_exit(event: &Kevent) -> bool {
        event.filter == EVFILT_PROC && event.fflags & NOTE_EXIT != 0
    }

    impl ExitWatch {
        /// Register `pid` for `NOTE_EXIT` and report an exit that already
        /// happened. `ESRCH` is returned as an error so the caller can distinguish
        /// "the kernel has no such process" from any other failure.
        fn watch(pid: u32) -> io::Result<Self> {
            // SAFETY: `kqueue` has no preconditions.
            let kqueue = unsafe { kqueue() };
            if kqueue < 0 {
                return Err(io::Error::last_os_error());
            }
            // SAFETY: the descriptor was just created and is owned here.
            let kqueue = unsafe { OwnedFd::from_raw_fd(kqueue) };
            let change = Kevent {
                ident: pid as usize,
                filter: EVFILT_PROC,
                flags: EV_ADD | EV_ENABLE | EV_ONESHOT,
                fflags: NOTE_EXIT,
                ..Kevent::default()
            };
            let mut events = [Kevent::default(); 1];
            let zero = Timespec {
                tv_sec: 0,
                tv_nsec: 0,
            };
            // SAFETY: the change/event buffers are live and correctly sized and
            // the timeout is a zero-valued `timespec`.
            let count = unsafe {
                kevent(
                    kqueue.as_raw_fd(),
                    &change,
                    1,
                    events.as_mut_ptr(),
                    1,
                    &zero,
                )
            };
            if count < 0 {
                return Err(io::Error::last_os_error());
            }
            if count > 0 && events[0].flags & EV_ERROR != 0 {
                // The registration itself was refused; `data` carries the errno.
                return Err(io::Error::from_raw_os_error(events[0].data as i32));
            }
            Ok(Self {
                kqueue,
                delivered: count > 0 && is_exit(&events[0]),
            })
        }

        /// Whether the exit event has been delivered (never blocks).
        fn observed(&mut self) -> io::Result<bool> {
            if self.delivered {
                return Ok(true);
            }
            let mut events = [Kevent::default(); 1];
            let zero = Timespec {
                tv_sec: 0,
                tv_nsec: 0,
            };
            // SAFETY: the kqueue is live, the change list is empty and the event
            // buffer is live and correctly sized.
            let count = unsafe {
                kevent(
                    self.kqueue.as_raw_fd(),
                    std::ptr::null(),
                    0,
                    events.as_mut_ptr(),
                    1,
                    &zero,
                )
            };
            if count < 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(count > 0 && (is_exit(&events[0]) || events[0].flags & EV_EOF != 0))
        }
    }

    pub(crate) struct MacosChannel {
        /// The peer's exit registration, taken before the release byte is read.
        exit: Option<ExitWatch>,
        /// The peer was already observed as exited (a `NOTE_EXIT` delivered at
        /// registration time, or a kernel `ESRCH` for the peer pid).
        exited: bool,
        /// No exit registration could be taken and no exit was observed. There
        /// is then no exit evidence at all, so the run must fail closed instead
        /// of treating the release byte as one.
        exit_unavailable: bool,
    }

    impl MacosChannel {
        pub(crate) fn new() -> Self {
            Self {
                exit: None,
                exited: false,
                exit_unavailable: false,
            }
        }
    }

    impl CleanupChannel for MacosChannel {
        fn is_inherited_pipe(&self) -> bool {
            // Both standard descriptors must be a pipe/socket, never a regular
            // file or a terminal.
            descriptor_is_pipe(0) && descriptor_is_pipe(1)
        }

        fn parent_handle(&mut self, handle: u64) -> Option<ParentEvidence> {
            let fd = c_int::try_from(handle).ok()?;
            // The inherited handle must itself be a socketpair end; its peer pid
            // is captured by the kernel when the pair was created.
            if !descriptor_is_pipe(fd) {
                return None;
            }
            let pid = peer_pid(fd)?;
            // Take the exit registration **now**, before any release byte is
            // consumed, so the exit event can never be missed.
            let watch = ExitWatch::watch(pid);
            match crate::platform::macos_process_start(pid) {
                Some(start) => {
                    // The identity is exact. The exit gate still needs a real
                    // registration: without one there is no exit evidence, and a
                    // refused registration must fail closed in `parent_state`
                    // rather than let a release byte stand in for an exit.
                    match watch {
                        Ok(watch) => {
                            self.exited = watch.delivered;
                            self.exit = Some(watch);
                        }
                        Err(_) => self.exit_unavailable = true,
                    }
                    Some(ParentEvidence::Exact(ParentIdentity { pid, start }))
                }
                None => match watch {
                    Ok(mut watch) => {
                        // The start marker cannot be read, so identity is only
                        // provable when the OS says this peer really exited.
                        let exited = watch.observed().unwrap_or(false);
                        self.exited = exited;
                        self.exit = Some(watch);
                        exited.then_some(ParentEvidence::Exited { pid })
                    }
                    // The kernel reports no such process: the peer the inherited
                    // socket named exited and was reaped. A recycled pid would be
                    // live (start marker readable, then mismatched), never this.
                    Err(error) if error.raw_os_error() == Some(ESRCH) => {
                        self.exited = true;
                        self.exit = None;
                        Some(ParentEvidence::Exited { pid })
                    }
                    Err(_) => {
                        self.exit_unavailable = true;
                        None
                    }
                },
            }
        }

        fn parent_state(
            &mut self,
            parent_handle: u64,
            release_handle: u64,
            deadline: Instant,
        ) -> ParentState {
            let Ok(fd) = c_int::try_from(parent_handle) else {
                return ParentState::Unavailable;
            };
            if release_handle != 0 {
                // On macOS the socketpair itself is the release channel; a request
                // that names a second release handle is not this platform's shape.
                return ParentState::Unavailable;
            }
            if self.exited {
                // Observed at registration time (`NOTE_EXIT` or a kernel `ESRCH`
                // for the peer): the parent is gone and can never mutate again.
                return ParentState::Exited;
            }
            if self.exit_unavailable || self.exit.is_none() {
                // No exit registration exists, so no exit can ever be observed.
                // The release byte alone is not exit evidence: fail closed.
                return ParentState::Unavailable;
            }
            loop {
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero() {
                    return ParentState::Alive;
                }
                // Poll the release channel in short slices and check the kqueue
                // between them (a kqueue descriptor is not relied on as pollable).
                let timeout = c_int::try_from(
                    remaining
                        .min(std::time::Duration::from_millis(10))
                        .as_millis(),
                )
                .unwrap_or(10);
                let mut descriptor = PollFd {
                    fd,
                    events: POLLIN,
                    revents: 0,
                };
                // SAFETY: `descriptor` is a live pollfd and the timeout is bounded.
                let ready = unsafe { poll(&mut descriptor, 1, timeout) };
                if ready < 0 {
                    if io::Error::last_os_error().raw_os_error() == Some(EINTR) {
                        continue;
                    }
                    return ParentState::Unavailable;
                }
                // A real exit event is the only exit evidence.
                if let Some(exit) = self.exit.as_mut() {
                    match exit.observed() {
                        Ok(true) => {
                            self.exited = true;
                            return ParentState::Exited;
                        }
                        Ok(false) => {}
                        // The watch can no longer report an exit: no exit evidence
                        // remains, so the run fails closed instead of falling back
                        // to the release channel.
                        Err(_) => {
                            self.exit = None;
                            self.exit_unavailable = true;
                            return ParentState::Unavailable;
                        }
                    }
                }
                if ready == 0 {
                    continue;
                }
                if descriptor.revents & (POLLIN | POLLHUP | POLLERR) == 0 {
                    continue;
                }
                let mut byte = [0u8; 1];
                // SAFETY: the descriptor is live and the buffer is live.
                let count = unsafe { read(fd, byte.as_mut_ptr(), 1) };
                if count < 0 {
                    if io::Error::last_os_error().raw_os_error() == Some(EINTR) {
                        continue;
                    }
                    return ParentState::Unavailable;
                }
                // One release byte, or EOF (the peer closed its end): the
                // capability is released as a **protocol step**. It is not exit
                // evidence, so it cannot permit deletion. Re-check the exit
                // registration once (an exit may have been delivered in the same
                // instant) and then report `Released`, i.e. "still alive".
                if let Some(exit) = self.exit.as_mut()
                    && exit.observed().unwrap_or(false)
                {
                    self.exited = true;
                    return ParentState::Exited;
                }
                return ParentState::Released;
            }
        }

        fn read_request(&mut self, limit: usize, deadline: Instant) -> io::Result<Vec<u8>> {
            read_bounded(0, limit, deadline)
        }

        fn write_completion(&mut self, frame: &[u8], deadline: Instant) -> io::Result<()> {
            write_bounded(1, frame, deadline)
        }
    }

    /// A bounded, chunked read from one descriptor.
    fn read_bounded(fd: c_int, limit: usize, deadline: Instant) -> io::Result<Vec<u8>> {
        let mut bytes = Vec::new();
        let mut buffer = [0u8; 4096];
        loop {
            if bytes.len() > limit {
                return Err(io::Error::other("frame is too large"));
            }
            if !wait_for(fd, POLLIN, deadline)? {
                return Err(io::Error::other("cleanup deadline elapsed"));
            }
            // SAFETY: the descriptor is live and `buffer` is a live output buffer.
            let count = unsafe { read(fd, buffer.as_mut_ptr(), buffer.len()) };
            if count < 0 {
                if io::Error::last_os_error().raw_os_error() == Some(EINTR) {
                    continue;
                }
                return Err(io::Error::last_os_error());
            }
            if count == 0 {
                return Ok(bytes);
            }
            bytes.extend_from_slice(&buffer[..count as usize]);
        }
    }

    /// A bounded, chunked write to one descriptor.
    fn write_bounded(fd: c_int, frame: &[u8], deadline: Instant) -> io::Result<()> {
        let mut written = 0usize;
        while written < frame.len() {
            if !wait_for(fd, POLLOUT, deadline)? {
                return Err(io::Error::other("cleanup deadline elapsed"));
            }
            // One small write at a time: a fragment below PIPE_BUF is atomic.
            let chunk = (frame.len() - written).min(1024);
            // SAFETY: the descriptor is live and the chunk is inside `frame`.
            let count = unsafe { write(fd, frame[written..].as_ptr(), chunk) };
            if count < 0 {
                if io::Error::last_os_error().raw_os_error() == Some(EINTR) {
                    continue;
                }
                return Err(io::Error::last_os_error());
            }
            written += count as usize;
        }
        Ok(())
    }

    /// Wait for `events` with the remaining deadline; `false` on timeout.
    fn wait_for(fd: c_int, events: i16, deadline: Instant) -> io::Result<bool> {
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Ok(false);
            }
            let timeout = c_int::try_from(remaining.as_millis()).unwrap_or(c_int::MAX);
            let mut descriptor = PollFd {
                fd,
                events,
                revents: 0,
            };
            // SAFETY: `descriptor` is a live pollfd and the timeout is bounded.
            let ready = unsafe { poll(&mut descriptor, 1, timeout) };
            if ready < 0 {
                if io::Error::last_os_error().raw_os_error() == Some(EINTR) {
                    continue;
                }
                return Err(io::Error::last_os_error());
            }
            return Ok(ready > 0);
        }
    }

    pub(crate) struct MacosCleanupFs {
        pub(crate) own_path: PathBuf,
    }

    struct MacosTree(crate::platform::MacosCleanupRoot);

    impl CleanupTree for MacosTree {
        fn remove_file(
            &mut self,
            relative: &str,
            size: u64,
            sha256: Digest,
            deadline: Instant,
        ) -> RemoveOutcome {
            self.0.remove_file(relative, size, sha256, deadline)
        }

        fn remove_link(
            &mut self,
            relative: &str,
            size: u64,
            sha256: Digest,
            deadline: Instant,
        ) -> RemoveOutcome {
            self.0.remove_link(relative, size, sha256, deadline)
        }

        fn remove_directory(&mut self, relative: &str) -> RemoveOutcome {
            self.0.remove_directory(relative)
        }
    }

    impl CleanupFileSystem for MacosCleanupFs {
        fn own_path(&self) -> io::Result<PathBuf> {
            Ok(self.own_path.clone())
        }

        fn hash_file(&self, path: &Path, deadline: Instant) -> io::Result<Digest> {
            hash_bounded(path, deadline)
        }

        fn signature(&self, path: &Path, deadline: Instant) -> Result<String, CleanupFailure> {
            signature_bounded(path, deadline, |path| {
                crate::platform::macos_verify_own_signature(path).map_err(|_| CleanupFailure)
            })
        }

        fn open_root(&mut self, root: &Path) -> Result<Box<dyn CleanupTree + '_>, CleanupFailure> {
            let root = crate::platform::MacosCleanupRoot::open(root).map_err(|_| CleanupFailure)?;
            Ok(Box::new(MacosTree(root)))
        }
    }

    // -----------------------------------------------------------------------
    // The installer side: spawn the staged helper, release, wait, authenticate.
    // -----------------------------------------------------------------------

    /// One inherited-handle run of the staged helper.
    ///
    /// A `socketpair` whose peer is the installer is both the identity the helper
    /// authenticates and the release signal: the installer closes its end as its
    /// last act before it waits, so the helper proceeds without the installer
    /// having to exit. The child receives that end on a fixed descriptor, and its
    /// standard input/output are the request/completion pipes.
    pub(super) fn spawn_helper(
        run: &HelperRun<'_>,
    ) -> crate::model::InstallResult<CleanupCompletion> {
        let pending =
            || crate::model::InstallFailure::new(crate::model::FailureCode::CleanupPending);
        let deadline = Instant::now() + run.timeout.min(MAX_CLEANUP_TIMEOUT);
        let parent = parent_start().ok_or_else(pending)?;

        let mut fds = [0 as c_int; 2];
        // SAFETY: `fds` is a live two-element output buffer.
        if unsafe { socketpair(AF_UNIX, SOCK_STREAM, 0, fds.as_mut_ptr()) } != 0 {
            return Err(pending());
        }
        // Both ends are owned from the moment they exist, so every error path
        // below closes them instead of leaking a socket descriptor.
        // SAFETY: both descriptors were just created and are owned here.
        let (installer_end, child_end) =
            unsafe { (OwnedFd::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) };
        // Neither end may reach the child by accident: the child gets exactly one
        // copy on `CHILD_HANDLE_FD` (via `dup2`, which clears the flag), and the
        // installer's own end must stay private — otherwise the child would hold a
        // duplicate of it and could never observe a peer EOF.
        // SAFETY: both descriptors are live and owned here.
        unsafe {
            let _ = fcntl(installer_end.as_raw_fd(), F_SETFD, FD_CLOEXEC);
            let _ = fcntl(child_end.as_raw_fd(), F_SETFD, FD_CLOEXEC);
        }
        let request = run.request(parent, CHILD_HANDLE_FD as u64, 0);
        let frame = serde_json::to_vec(&request).map_err(|_| pending())?;
        // The installer validates exactly what it is about to send.
        parse_request(&frame)?;

        let mut command = std::process::Command::new(run.helper_path);
        command
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null());
        let child_fd = child_end.as_raw_fd();
        // SAFETY: the closure only makes the async-signal-safe calls `dup2` and
        // `close` on descriptors created for this child.
        unsafe {
            command.pre_exec(move || {
                if dup2(child_fd, CHILD_HANDLE_FD) < 0 {
                    return Err(io::Error::last_os_error());
                }
                if child_fd != CHILD_HANDLE_FD {
                    let _ = close(child_fd);
                }
                Ok(())
            });
        }
        let mut child = match command.spawn() {
            Ok(child) => child,
            // Both owned descriptors close when this function returns.
            Err(_) => return Err(pending()),
        };
        // The child holds its own copy on `CHILD_HANDLE_FD`; this one must not
        // stay open, or the peer would never observe a close.
        drop(child_end);

        // The installer's own end of the identity/release socketpair. It stays
        // open until the run ends: the helper authenticates its *peer* over this
        // socket, so the peer must still exist while the helper verifies it. (A
        // crashed installer closes it, which the helper observes as EOF.)
        let installer_socket = installer_end;

        // Request out, then the release: after this the installer only waits.
        let stdin = child.stdin.take().ok_or_else(pending)?;
        let written = write_bounded(stdin.as_raw_fd(), &frame, deadline);
        drop(stdin);
        written.map_err(|_| pending())?;
        // One byte on the inherited socketpair is the release: the parent will not
        // mutate anything again. It cannot block (one byte, empty buffer) and it
        // is the installer's last act before it waits for the helper.
        write_bounded(installer_socket.as_raw_fd(), &RELEASE, deadline).map_err(|_| pending())?;

        let mut status = None;
        while Instant::now() < deadline {
            match child.try_wait() {
                Ok(Some(exit)) => {
                    status = Some(exit);
                    break;
                }
                Ok(None) => std::thread::sleep(std::time::Duration::from_millis(5)),
                Err(_) => return Err(pending()),
            }
        }
        let Some(status) = status else {
            // Do not terminate the helper: nothing is claimed and the image is left
            // alone (it is still mapped).
            return Err(pending());
        };
        let stdout = child.stdout.take().ok_or_else(pending)?;
        let frame = read_bounded(stdout.as_raw_fd(), MAX_CLEANUP_FRAME_BYTES, deadline)
            .map_err(|_| pending())?;
        drop(stdout);
        let completion = accept_completion(&frame, run.capability, run.transaction)?;
        // The authenticated completion and the process exit must agree.
        if completion.status == CleanupStatus::Complete && !status.success() {
            return Err(pending());
        }
        Ok(completion)
    }

    /// This process's identity (pid + BSD start marker).
    fn parent_start() -> Option<ParentIdentity> {
        let pid = std::process::id();
        process_start(pid).map(|start| ParentIdentity { pid, start })
    }
}

#[cfg(target_os = "macos")]
pub(super) fn spawn_helper(run: &HelperRun<'_>) -> crate::model::InstallResult<CleanupCompletion> {
    macos_impl::spawn_helper(run)
}

#[cfg(target_os = "macos")]
pub(super) fn process_start(pid: u32) -> Option<u64> {
    macos_impl::process_start(pid)
}

#[cfg(windows)]
pub(super) fn spawn_helper(run: &HelperRun<'_>) -> crate::model::InstallResult<CleanupCompletion> {
    windows_impl::spawn_helper(run)
}

#[cfg(windows)]
pub(super) fn process_start(pid: u32) -> Option<u64> {
    windows_impl::process_start(pid)
}
