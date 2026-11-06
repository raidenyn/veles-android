//! Native cleanup channel and self-verification (Windows / macOS only).
//!
//! The installer hands the helper two inherited pipe handles as its standard
//! input (request) and standard output (completion), so a request can never be
//! supplied through a command line or a path. The channel implementation proves
//! the handles really are pipes and attributes the request writer to the parent
//! the request names, including that parent's start marker (a pid alone is not
//! an identity). The file system implementation removes exact owned identities
//! and arms the helper's own deletion with the OS's native semantics.

use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use super::{
    CleanupChannel, CleanupCompletion, CleanupFailure, CleanupFileSystem, CleanupStatus,
    MAX_CLEANUP_TIMEOUT, ParentIdentity, ParentState, RemoveOutcome, execute_cleanup,
};
use crate::model::Digest;
use crate::platform::hash_file;

/// Run one cleanup capability on the native channel.
pub(super) fn run() -> i32 {
    let own = std::env::current_exe().unwrap_or_default();
    let mut channel = native_channel();
    let mut fs = native_fs(own);
    let completion: CleanupCompletion = execute_cleanup(&mut channel, &mut fs, MAX_CLEANUP_TIMEOUT);
    i32::from(completion.status != CleanupStatus::Complete)
}

/// SHA-256 of one file, bounded by the caller's size check.
fn hash_bytes(path: &Path) -> std::io::Result<Digest> {
    hash_file(path).map_err(|_| std::io::Error::other("file is not a readable regular file"))
}

/// A completion that could not be authenticated is never complete.
fn write_frame(frame: &[u8]) -> std::io::Result<()> {
    let mut stdout = std::io::stdout();
    stdout.write_all(frame)?;
    stdout.flush()
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

// ---------------------------------------------------------------------------
// Windows
// ---------------------------------------------------------------------------

#[cfg(windows)]
mod windows_impl {
    use super::*;
    use std::fs::File;
    use std::mem::size_of;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::{AsRawHandle, FromRawHandle};
    use windows::Win32::Foundation::{CloseHandle, ERROR_INVALID_PARAMETER, HANDLE};
    use windows::Win32::Storage::FileSystem::{
        CreateFileW, DELETE, FILE_ATTRIBUTE_NORMAL, FILE_DISPOSITION_INFO,
        FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE,
        FILE_TYPE_PIPE, FileDispositionInfo, GetFileType, OPEN_EXISTING,
        SetFileInformationByHandle,
    };
    use windows::Win32::System::Pipes::GetNamedPipeClientProcessId;
    use windows::Win32::System::Threading::{
        GetProcessTimes, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    use windows::core::PCWSTR;

    /// The start marker of one process: its kernel creation time.
    pub(crate) fn process_start(pid: u32) -> Option<u64> {
        // SAFETY: `pid` is an observed process id; `false` disables inheritance.
        let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) }.ok()?;
        let handle = OwnedProcess(handle);
        let mut creation = Default::default();
        let mut exit = Default::default();
        let mut kernel = Default::default();
        let mut user = Default::default();
        // SAFETY: the handle is live and every out-parameter is a live FILETIME.
        unsafe { GetProcessTimes(handle.0, &mut creation, &mut exit, &mut kernel, &mut user) }
            .ok()?;
        Some((u64::from(creation.dwHighDateTime) << 32) | u64::from(creation.dwLowDateTime))
    }

    struct OwnedProcess(HANDLE);

    impl Drop for OwnedProcess {
        fn drop(&mut self) {
            // SAFETY: the handle was returned by OpenProcess and is owned here.
            unsafe {
                let _ = CloseHandle(self.0);
            }
        }
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

        fn peer(&self) -> Option<ParentIdentity> {
            let input = std::io::stdin().as_raw_handle();
            let mut pid = 0u32;
            // The read end of the anonymous pipe is its server end, so the
            // client process id is the writer - the installer that spawned us.
            // SAFETY: `input` is a live pipe handle and `pid` is a live
            // out-parameter.
            unsafe { GetNamedPipeClientProcessId(HANDLE(input), &mut pid) }.ok()?;
            if pid == 0 {
                return None;
            }
            Some(ParentIdentity {
                pid,
                start: process_start(pid)?,
            })
        }

        fn parent_state(&self, parent: &ParentIdentity) -> ParentState {
            match process_start(parent.pid) {
                Some(start) if start == parent.start => ParentState::Alive,
                Some(_) => ParentState::Reused,
                None => {
                    // Distinguish "exited" from "cannot open": only a PID the OS
                    // says no longer exists may be reported as exited.
                    // SAFETY: `parent.pid` is an observed process id.
                    match unsafe {
                        OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, parent.pid)
                    } {
                        Ok(handle) => {
                            // SAFETY: the handle was just opened and is owned here.
                            let _ = OwnedProcess(handle);
                            ParentState::Unavailable
                        }
                        Err(error)
                            if error.code()
                                == windows::core::HRESULT::from_win32(
                                    ERROR_INVALID_PARAMETER.0,
                                ) =>
                        {
                            ParentState::Exited
                        }
                        Err(_) => ParentState::Unavailable,
                    }
                }
            }
        }

        fn read_request(&mut self, limit: usize) -> std::io::Result<Vec<u8>> {
            let mut bytes = Vec::new();
            std::io::stdin()
                .take(limit as u64 + 1)
                .read_to_end(&mut bytes)?;
            if bytes.len() > limit {
                return Err(std::io::Error::other("request frame is too large"));
            }
            Ok(bytes)
        }

        fn write_completion(&mut self, frame: &[u8]) -> std::io::Result<()> {
            write_frame(frame)
        }
    }

    pub(crate) struct WindowsCleanupFs {
        pub(crate) own_path: PathBuf,
        /// The self-delete disposition handle, held open until process exit.
        disposition: Option<File>,
    }

    impl WindowsCleanupFs {
        pub(crate) fn new(own_path: PathBuf) -> Self {
            Self {
                own_path,
                disposition: None,
            }
        }
    }

    impl CleanupFileSystem for WindowsCleanupFs {
        fn own_path(&self) -> std::io::Result<PathBuf> {
            Ok(self.own_path.clone())
        }

        fn hash_file(&self, path: &Path) -> std::io::Result<Digest> {
            hash_bytes(path)
        }

        fn signature(&self, path: &Path) -> Result<String, CleanupFailure> {
            crate::platform::windows_verify_own_signature(path).map_err(|_| CleanupFailure)
        }

        fn remove_file_exact(&mut self, path: &Path, size: u64, sha256: Digest) -> RemoveOutcome {
            use std::os::windows::fs::MetadataExt;

            let metadata = match std::fs::symlink_metadata(path) {
                Ok(metadata) => metadata,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    return RemoveOutcome::Absent;
                }
                Err(_) => return RemoveOutcome::Preserved,
            };
            if metadata.file_attributes() & crate::platform::FILE_ATTRIBUTE_REPARSE_POINT != 0
                || !metadata.is_file()
                || metadata.len() != size
            {
                return RemoveOutcome::Preserved;
            }
            match hash_file(path) {
                Ok(digest) if digest == sha256 => {}
                _ => return RemoveOutcome::Preserved,
            }
            match std::fs::remove_file(path) {
                Ok(()) => RemoveOutcome::Removed,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => RemoveOutcome::Absent,
                Err(_) => RemoveOutcome::Preserved,
            }
        }

        fn remove_directory(&mut self, path: &Path) -> RemoveOutcome {
            match std::fs::remove_dir(path) {
                Ok(()) => RemoveOutcome::Removed,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => RemoveOutcome::Absent,
                Err(_) => RemoveOutcome::Preserved,
            }
        }

        fn arm_self_delete(&mut self, path: &Path) -> Result<(), CleanupFailure> {
            let wide: Vec<u16> = path
                .as_os_str()
                .encode_wide()
                .chain([0])
                .collect::<Vec<u16>>();
            // Open our own image with DELETE and every sharing mode, so the
            // loader's existing mapping does not block the disposition.
            // SAFETY: `wide` is NUL-terminated and outlives the call.
            let handle = unsafe {
                CreateFileW(
                    PCWSTR(wide.as_ptr()),
                    DELETE.0,
                    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    None,
                    OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
                    None,
                )
            }
            .map_err(|_| CleanupFailure)?;
            if handle.is_invalid() {
                return Err(CleanupFailure);
            }
            // SAFETY: CreateFileW transferred the handle; File closes it once.
            let file = unsafe { File::from_raw_handle(handle.0) };
            let mut information = FILE_DISPOSITION_INFO { DeleteFile: true };
            // SAFETY: `file` is a live DELETE-capable handle and `information`
            // is a correctly sized FILE_DISPOSITION_INFO.
            unsafe {
                SetFileInformationByHandle(
                    HANDLE(file.as_raw_handle()),
                    FileDispositionInfo,
                    (&mut information as *mut FILE_DISPOSITION_INFO).cast(),
                    size_of::<FILE_DISPOSITION_INFO>() as u32,
                )
            }
            .map_err(|_| CleanupFailure)?;
            // The deletion is armed: the OS removes the image when this last
            // handle closes at process exit. No reboot fallback exists.
            self.disposition = Some(file);
            Ok(())
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
    use std::io;

    const _: () = assert!(std::mem::size_of::<FileStatus>() == 144);

    const S_IFMT: u32 = 0o170000;
    const S_IFIFO: u32 = 0o010000;
    const S_IFSOCK: u32 = 0o140000;
    const SOL_LOCAL: c_int = 0;
    const LOCAL_PEERPID: c_int = 0x002;

    #[repr(C)]
    struct FileStatus {
        st_dev: i32,
        st_mode: u16,
        st_nlink: u16,
        st_ino: u64,
        st_uid: u32,
        st_gid: u32,
        st_rdev: i32,
        st_atime: i64,
        st_atime_nsec: i64,
        st_mtime: i64,
        st_mtime_nsec: i64,
        st_ctime: i64,
        st_ctime_nsec: i64,
        st_birthtime: i64,
        st_birthtime_nsec: i64,
        st_size: i64,
        st_blocks: i64,
        st_blksize: i32,
        st_flags: u32,
        st_gen: u32,
        st_lspare: i32,
        st_qspare: [i64; 2],
    }

    unsafe extern "C" {
        fn fstat(fd: c_int, status: *mut FileStatus) -> c_int;
        fn getsockopt(
            socket: c_int,
            level: c_int,
            name: c_int,
            value: *mut std::ffi::c_void,
            length: *mut u32,
        ) -> c_int;
        fn kill(pid: c_int, signal: c_int) -> c_int;
    }

    const ESRCH: i32 = 3;

    pub(crate) struct MacosChannel;

    impl MacosChannel {
        fn descriptor_is_pipe(fd: c_int) -> bool {
            let mut status = std::mem::MaybeUninit::<FileStatus>::zeroed();
            // SAFETY: the out-buffer is live and sized by the OS definition.
            let result = unsafe { fstat(fd, status.as_mut_ptr()) };
            if result != 0 {
                return false;
            }
            // SAFETY: fstat initialized the structure on success.
            let status = unsafe { status.assume_init() };
            let kind = u32::from(status.st_mode) & S_IFMT;
            kind == S_IFIFO || kind == S_IFSOCK
        }
    }

    impl CleanupChannel for MacosChannel {
        fn is_inherited_pipe(&self) -> bool {
            // Both standard descriptors must be a pipe/socket, never a regular
            // file or a terminal.
            Self::descriptor_is_pipe(0) && Self::descriptor_is_pipe(1)
        }

        fn peer(&self) -> Option<ParentIdentity> {
            let mut pid: c_int = 0;
            let mut length = std::mem::size_of::<c_int>() as u32;
            // SAFETY: the descriptor is inherited stdin and `pid`/`length` are
            // live out-parameters.
            let result = unsafe {
                getsockopt(
                    0,
                    SOL_LOCAL,
                    LOCAL_PEERPID,
                    (&mut pid as *mut c_int).cast(),
                    &mut length,
                )
            };
            if result != 0 || pid <= 0 {
                return None;
            }
            Some(ParentIdentity {
                pid: pid as u32,
                start: process_start(pid as u32)?,
            })
        }

        fn parent_state(&self, parent: &ParentIdentity) -> ParentState {
            match process_start(parent.pid) {
                Some(start) if start == parent.start => ParentState::Alive,
                Some(_) => ParentState::Reused,
                None => {
                    // SAFETY: signal 0 performs only the existence/permission
                    // check and never delivers a signal.
                    let result = unsafe { kill(parent.pid as c_int, 0) };
                    let errno = std::io::Error::last_os_error().raw_os_error();
                    if result != 0 && errno == Some(ESRCH) {
                        // The only outcome that may be read as "exited".
                        ParentState::Exited
                    } else {
                        ParentState::Unavailable
                    }
                }
            }
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
            write_frame(frame)
        }
    }

    pub(crate) struct MacosCleanupFs {
        pub(crate) own_path: PathBuf,
    }

    impl CleanupFileSystem for MacosCleanupFs {
        fn own_path(&self) -> std::io::Result<PathBuf> {
            Ok(self.own_path.clone())
        }

        fn hash_file(&self, path: &Path) -> std::io::Result<Digest> {
            hash_bytes(path)
        }

        fn signature(&self, path: &Path) -> Result<String, CleanupFailure> {
            crate::platform::macos_verify_own_signature(path).map_err(|_| CleanupFailure)
        }

        fn remove_file_exact(&mut self, path: &Path, size: u64, sha256: Digest) -> RemoveOutcome {
            let metadata = match std::fs::symlink_metadata(path) {
                Ok(metadata) => metadata,
                Err(error) if error.kind() == io::ErrorKind::NotFound => {
                    return RemoveOutcome::Absent;
                }
                Err(_) => return RemoveOutcome::Preserved,
            };
            // lstat semantics: a symbolic link is never followed and never
            // removed as if it were the owned file.
            if metadata.file_type().is_symlink() || !metadata.is_file() || metadata.len() != size {
                return RemoveOutcome::Preserved;
            }
            match hash_file(path) {
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
                Err(_) => RemoveOutcome::Preserved,
            }
        }

        fn arm_self_delete(&mut self, path: &Path) -> Result<(), CleanupFailure> {
            // unlink(2) removes the name while the image is still mapped; the
            // process keeps running until it exits, and the name is gone.
            std::fs::remove_file(path).map_err(|_| CleanupFailure)?;
            match std::fs::symlink_metadata(path) {
                Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
                _ => Err(CleanupFailure),
            }
        }
    }

    /// Resolve the start marker of one pid, or `None` when it is not readable.
    fn process_start(pid: u32) -> Option<u64> {
        crate::platform::macos_process_start(pid)
    }
}

#[cfg(target_os = "macos")]
use macos_impl::{MacosChannel, MacosCleanupFs};
