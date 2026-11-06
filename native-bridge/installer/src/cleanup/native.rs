//! Native cleanup channel and self-verification (Windows / macOS only).
//!
//! The installer hands the helper three inherited handles: standard input (the
//! bounded request), standard output (the authenticated completion) and one
//! **parent-process handle** named by the request. The helper:
//!
//! * proves standard input/output are pipes, not files or a console;
//! * authenticates the *inherited handle* as the recorded parent
//!   (`GetProcessId` + `GetProcessTimes` on Windows, `LOCAL_PEERPID` on macOS)
//!   instead of reopening a pid;
//! * waits on that handle until the process exits (`WaitForSingleObject` /
//!   `poll(2)` EOF), so a parent that exits after signalling is observed as
//!   exited rather than as a stale or recycled pid;
//! * bounds every read, write, hash and signature assessment by the run
//!   deadline;
//! * removes exact owned identities through the adapter's anchored
//!   [`CleanupRoot`](crate::platform::MacosCleanupRoot) implementation.
//!
//! The helper never deletes its own image: it only exits, and the installer
//! deletes the staged image afterwards (see `remove_helper_after_exit`).

use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::time::Instant;

use sha2::{Digest as _, Sha256};

use super::{
    CleanupChannel, CleanupCompletion, CleanupFailure, CleanupFileSystem, CleanupStatus,
    CleanupTree, MAX_CLEANUP_TIMEOUT, ParentIdentity, ParentState, RemoveOutcome, execute_cleanup,
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

#[cfg(windows)]
fn native_channel() -> WindowsChannel {
    WindowsChannel
}

#[cfg(target_os = "macos")]
fn native_channel() -> MacosChannel {
    MacosChannel
}

#[cfg(windows)]
fn native_fs(own: PathBuf) -> WindowsCleanupFs {
    WindowsCleanupFs::new(own)
}

#[cfg(target_os = "macos")]
fn native_fs(own: PathBuf) -> MacosCleanupFs {
    MacosCleanupFs { own_path: own }
}

/// SHA-256 of one regular file, stopped at `deadline`.
///
/// The helper hashes its own image here; an image large enough to outlast the
/// cleanup budget must fail closed, not extend the run.
fn hash_bounded(path: &Path, deadline: Instant) -> io::Result<Digest> {
    let mut file = crate::platform::open_no_follow(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() {
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

// ---------------------------------------------------------------------------
// Windows
// ---------------------------------------------------------------------------

#[cfg(windows)]
mod windows_impl {
    use super::*;
    use std::os::windows::io::AsRawHandle;
    use windows::Win32::Foundation::{HANDLE, WAIT_OBJECT_0, WAIT_TIMEOUT};
    use windows::Win32::Storage::FileSystem::{FILE_TYPE_PIPE, GetFileType, ReadFile};
    use windows::Win32::System::Pipes::PeekNamedPipe;
    use windows::Win32::System::Threading::{GetProcessId, GetProcessTimes, WaitForSingleObject};

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

    pub(crate) struct WindowsChannel;

    impl CleanupChannel for WindowsChannel {
        fn is_inherited_pipe(&self) -> bool {
            let input = std::io::stdin().as_raw_handle();
            let output = std::io::stdout().as_raw_handle();
            // SAFETY: both are valid standard handles for this process.
            let input_type = unsafe { GetFileType(HANDLE(input)) };
            let output_type = unsafe { GetFileType(HANDLE(output)) };
            input_type == FILE_TYPE_PIPE && output_type == FILE_TYPE_PIPE
        }

        fn parent_handle(&self, handle: u64) -> Option<ParentIdentity> {
            let handle = handle_of(handle)?;
            // SAFETY: the handle is inherited and names the parent for this call.
            let pid = unsafe { GetProcessId(handle) };
            if pid == 0 {
                return None;
            }
            Some(ParentIdentity {
                pid,
                start: handle_start(handle)?,
            })
        }

        fn parent_state(&mut self, handle: u64, deadline: Instant) -> ParentState {
            let Some(handle) = handle_of(handle) else {
                return ParentState::Unavailable;
            };
            let remaining = deadline.saturating_duration_since(Instant::now());
            let millis = u32::try_from(remaining.as_millis()).unwrap_or(u32::MAX - 1);
            // SAFETY: the handle is inherited and names the parent process.
            match unsafe { WaitForSingleObject(handle, millis) } {
                WAIT_OBJECT_0 => ParentState::Exited,
                WAIT_TIMEOUT => ParentState::Alive,
                _ => ParentState::Unavailable,
            }
        }

        fn read_request(&mut self, limit: usize, deadline: Instant) -> io::Result<Vec<u8>> {
            let input = HANDLE(std::io::stdin().as_raw_handle());
            let mut bytes = Vec::new();
            let mut buffer = [0u8; 4096];
            loop {
                if bytes.len() > limit {
                    return Err(io::Error::other("request frame is too large"));
                }
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero() {
                    return Err(io::Error::other("cleanup deadline elapsed"));
                }
                // `PeekNamedPipe` is the documented, non-consuming way to ask an
                // anonymous pipe whether the writer has produced bytes (and
                // whether it is still connected). Waiting on a *synchronous* pipe
                // handle is not a reliable data-available signal, so the wait is
                // a bounded poll instead.
                let mut available = 0u32;
                // SAFETY: `input` is the inherited read end of the request pipe
                // and `available` is a live out-parameter; no data buffer is
                // supplied, so nothing is consumed.
                let peeked =
                    unsafe { PeekNamedPipe(input, None, 0, None, Some(&mut available), None) };
                if peeked.is_err() {
                    // A broken pipe is the writer closing: return what arrived.
                    return Ok(bytes);
                }
                if available == 0 {
                    std::thread::sleep(remaining.min(std::time::Duration::from_millis(10)));
                    continue;
                }
                let mut read = 0u32;
                let mut target = &mut buffer[..];
                // SAFETY: the handle is live, `target` is a live output buffer and
                // no overlapped operation is requested.
                let result = unsafe { ReadFile(input, Some(&mut target), Some(&mut read), None) };
                if result.is_err() {
                    return Err(io::Error::last_os_error());
                }
                if read == 0 {
                    return Ok(bytes);
                }
                bytes.extend_from_slice(&buffer[..read as usize]);
            }
        }

        fn write_completion(&mut self, frame: &[u8], deadline: Instant) -> io::Result<()> {
            if Instant::now() >= deadline {
                return Err(io::Error::other("cleanup deadline elapsed"));
            }
            // The frame is far smaller than the pipe buffer, so one small write
            // cannot block behind a full pipe.
            use std::io::Write as _;
            let mut stdout = std::io::stdout();
            stdout.write_all(frame)?;
            stdout.flush()
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
}

#[cfg(windows)]
use windows_impl::{WindowsChannel, WindowsCleanupFs};

// ---------------------------------------------------------------------------
// macOS
// ---------------------------------------------------------------------------

#[cfg(target_os = "macos")]
mod macos_impl {
    use super::*;
    use std::ffi::c_int;

    const SOL_LOCAL: c_int = 0;
    const LOCAL_PEERPID: c_int = 0x002;
    const POLLIN: i16 = 0x0001;
    const EINTR: i32 = 4;

    #[repr(C)]
    struct PollFd {
        fd: c_int,
        events: i16,
        revents: i16,
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
    }

    fn descriptor_is_pipe(fd: c_int) -> bool {
        use std::mem::ManuallyDrop;
        use std::os::unix::fs::FileTypeExt;
        use std::os::unix::io::FromRawFd;

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

    pub(crate) struct MacosChannel;

    impl CleanupChannel for MacosChannel {
        fn is_inherited_pipe(&self) -> bool {
            // Both standard descriptors must be a pipe/socket, never a regular
            // file or a terminal.
            descriptor_is_pipe(0) && descriptor_is_pipe(1)
        }

        fn parent_handle(&self, handle: u64) -> Option<ParentIdentity> {
            let fd = c_int::try_from(handle).ok()?;
            // The inherited handle must itself be a socketpair end, and its peer
            // must still be a live process; the pid comes from that handle.
            if !descriptor_is_pipe(fd) {
                return None;
            }
            let pid = peer_pid(fd)?;
            Some(ParentIdentity {
                pid,
                start: crate::platform::macos_process_start(pid)?,
            })
        }

        fn parent_state(&mut self, handle: u64, deadline: Instant) -> ParentState {
            let Ok(fd) = c_int::try_from(handle) else {
                return ParentState::Unavailable;
            };
            // A socketpair peer that exits closes its end: our end becomes
            // readable with EOF. That is a property of the inherited handle, so
            // no pid is reopened.
            loop {
                let remaining = deadline.saturating_duration_since(Instant::now());
                let timeout = c_int::try_from(remaining.as_millis()).unwrap_or(c_int::MAX);
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
                if ready == 0 {
                    return ParentState::Alive;
                }
                let mut byte = [0u8; 1];
                // SAFETY: the descriptor is live and the buffer is live.
                let count = unsafe { read(fd, byte.as_mut_ptr(), 1) };
                if count == 0 {
                    return ParentState::Exited;
                }
                if count < 0 {
                    if io::Error::last_os_error().raw_os_error() == Some(EINTR) {
                        continue;
                    }
                    return ParentState::Unavailable;
                }
                // The liveness peer never sends bytes; anything else is not the
                // channel this capability expects.
                return ParentState::Unavailable;
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
                return Err(io::Error::other("request frame is too large"));
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
        const POLLOUT: i16 = 0x0004;

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
}

#[cfg(target_os = "macos")]
use macos_impl::{MacosChannel, MacosCleanupFs};
