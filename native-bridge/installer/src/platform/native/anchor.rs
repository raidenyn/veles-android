//! Opened, no-follow directory handles (Windows / macOS only).
//!
//! [`ValidatedPath`](super::super::ValidatedPath) captures the identity of every
//! ancestor, but re-stating a path to the OS for each mutation re-walks a chain a
//! concurrent writer could have redirected between the check and the call. This
//! module opens that chain **once**, component by component, with no-follow OS
//! calls, and keeps the resulting directory handle alive: every native mutation
//! is then expressed relative to that handle (`openat`/`renameat`/`unlinkat`/
//! `mkdirat` on macOS, `NtCreateFile`/`NtSetInformationFile` with
//! `RootDirectory` on Windows), so one swapped component cannot redirect it.
//!
//! Each opened directory is compared with the identity validation captured
//! (device/inode on macOS, volume serial plus file index on Windows). A handle
//! naming a different object than the one that was validated is refused.

use std::path::Path;

use super::super::{FileIdentity, InstallResult, io_failure, unsafe_path};

/// One opened directory that anchored mutations are performed relative to.
#[derive(Debug)]
pub(crate) struct DirectoryHandle {
    raw: isize,
    identity: FileIdentity,
}

impl DirectoryHandle {
    /// The identity the OS reported for the opened object.
    pub(crate) fn identity(&self) -> &FileIdentity {
        &self.identity
    }
}

/// Open `anchor` and then each `component`, verifying every opened directory
/// against the identity captured during validation.
pub(crate) fn open_chain(
    anchor: &Path,
    components: &[&str],
    captured: &[FileIdentity],
) -> InstallResult<DirectoryHandle> {
    if captured.len() != components.len() + 1 {
        return Err(unsafe_path());
    }
    let mut current = platform::open_absolute(anchor)?;
    if current.identity != captured[0] {
        return Err(unsafe_path());
    }
    for (index, component) in components.iter().enumerate() {
        if component.is_empty() || *component == "." || *component == ".." {
            return Err(unsafe_path());
        }
        let next = platform::open_below(&current, component)?;
        if next.identity != captured[index + 1] {
            return Err(unsafe_path());
        }
        current = next;
    }
    Ok(current)
}

/// The macOS half: `open`/`openat` with `O_NOFOLLOW | O_DIRECTORY`.
#[cfg(unix)]
mod platform {
    use std::ffi::{CString, OsStr, c_char, c_int};
    use std::io;
    use std::mem::ManuallyDrop;
    use std::os::unix::ffi::OsStrExt;
    use std::os::unix::fs::MetadataExt;
    use std::os::unix::io::FromRawFd;
    use std::path::Path;

    use super::super::super::{FileIdentity, InstallFailure};
    use super::{DirectoryHandle, InstallResult, io_failure, unsafe_path};

    const O_RDONLY: c_int = 0;
    /// Darwin `O_DIRECTORY`.
    const O_DIRECTORY: c_int = 0x10_0000;
    /// Darwin `O_NOFOLLOW`.
    const O_NOFOLLOW: c_int = 0x0100;
    /// Darwin `O_CLOEXEC`.
    const O_CLOEXEC: c_int = 0x100_0000;
    const ELOOP: i32 = 62;
    const ENOTDIR: i32 = 20;
    const ENOENT: i32 = 2;

    unsafe extern "C" {
        fn open(path: *const c_char, flags: c_int, ...) -> c_int;
        fn openat(directory: c_int, path: *const c_char, flags: c_int, ...) -> c_int;
        fn close(fd: c_int) -> c_int;
    }

    fn c_name(name: &OsStr) -> InstallResult<CString> {
        CString::new(name.as_bytes()).map_err(|_| unsafe_path())
    }

    fn classify(error: io::Error) -> InstallFailure {
        match error.raw_os_error() {
            Some(ELOOP) | Some(ENOTDIR) | Some(ENOENT) => unsafe_path(),
            _ => io_failure(),
        }
    }

    /// The identity of the object the descriptor names, from the descriptor.
    fn identity_of(fd: c_int) -> InstallResult<FileIdentity> {
        // SAFETY: the descriptor is owned by the caller for the whole call; the
        // `ManuallyDrop` wrapper keeps `metadata` from closing it.
        let file = ManuallyDrop::new(unsafe { std::fs::File::from_raw_fd(fd) });
        let metadata = file.metadata().map_err(|_| io_failure())?;
        Ok(FileIdentity {
            device: metadata.dev(),
            inode: metadata.ino(),
        })
    }

    fn wrap(fd: c_int) -> InstallResult<DirectoryHandle> {
        let identity = identity_of(fd)?;
        Ok(DirectoryHandle {
            raw: fd as isize,
            identity,
        })
    }

    pub(super) fn open_absolute(path: &Path) -> InstallResult<DirectoryHandle> {
        let name = c_name(path.as_os_str())?;
        // SAFETY: `name` is NUL-terminated and outlives the call.
        let fd = unsafe {
            open(
                name.as_ptr(),
                O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC,
            )
        };
        if fd < 0 {
            return Err(classify(io::Error::last_os_error()));
        }
        match wrap(fd) {
            Ok(handle) => Ok(handle),
            Err(failure) => {
                // SAFETY: `fd` was just opened and is owned here.
                unsafe {
                    let _ = close(fd);
                }
                Err(failure)
            }
        }
    }

    pub(super) fn open_below(
        parent: &DirectoryHandle,
        component: &str,
    ) -> InstallResult<DirectoryHandle> {
        let name = CString::new(component.as_bytes()).map_err(|_| unsafe_path())?;
        // SAFETY: `parent` is a live directory descriptor and `name` is
        // NUL-terminated and outlives the call.
        let fd = unsafe {
            openat(
                parent.raw as c_int,
                name.as_ptr(),
                O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC,
            )
        };
        if fd < 0 {
            return Err(classify(io::Error::last_os_error()));
        }
        match wrap(fd) {
            Ok(handle) => Ok(handle),
            Err(failure) => {
                // SAFETY: `fd` was just opened and is owned here.
                unsafe {
                    let _ = close(fd);
                }
                Err(failure)
            }
        }
    }

    impl DirectoryHandle {
        /// The raw descriptor, borrowed for the duration of one OS call.
        pub(crate) fn fd(&self) -> c_int {
            self.raw as c_int
        }
    }

    impl Drop for DirectoryHandle {
        fn drop(&mut self) {
            if self.raw >= 0 {
                // SAFETY: the descriptor is owned by this handle.
                unsafe {
                    let _ = close(self.fd());
                }
            }
        }
    }
}

/// The Windows half: `CreateFileW`/`NtCreateFile` with
/// `FILE_FLAG_OPEN_REPARSE_POINT`, plus `DuplicateHandle`.
#[cfg(windows)]
mod platform {
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle};
    use std::path::Path;

    use windows::Wdk::Foundation::OBJECT_ATTRIBUTES;
    use windows::Wdk::Storage::FileSystem::{
        FILE_OPEN_FOR_BACKUP_INTENT, FILE_OPEN_REPARSE_POINT, FILE_SYNCHRONOUS_IO_NONALERT,
        NTCREATEFILE_CREATE_DISPOSITION, NTCREATEFILE_CREATE_OPTIONS, NtCreateFile,
    };
    use windows::Win32::Foundation::{CloseHandle, HANDLE, OBJ_CASE_INSENSITIVE, UNICODE_STRING};
    use windows::Win32::Storage::FileSystem::{
        BY_HANDLE_FILE_INFORMATION, CreateFileW, FILE_ATTRIBUTE_DIRECTORY,
        FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
        FILE_GENERIC_READ, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE,
        GetFileInformationByHandle, OPEN_EXISTING,
    };
    use windows::Win32::System::IO::IO_STATUS_BLOCK;
    use windows::core::PCWSTR;

    use super::super::super::FileIdentity;
    use super::{DirectoryHandle, InstallResult, io_failure, unsafe_path};

    /// `NtCreateFile`'s `FILE_OPEN` disposition.
    const FILE_OPEN: NTCREATEFILE_CREATE_DISPOSITION = NTCREATEFILE_CREATE_DISPOSITION(1);
    const FILE_DIRECTORY_FILE: NTCREATEFILE_CREATE_OPTIONS = NTCREATEFILE_CREATE_OPTIONS(0x1);
    /// `GENERIC_READ | GENERIC_WRITE`: the directory handle is also the anchor a
    /// directory flush is issued on, which requires write access.
    const GENERIC_READ_WRITE: u32 = 0x8000_0000 | 0x4000_0000;

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().collect()
    }

    pub(super) fn open_absolute(path: &Path) -> InstallResult<DirectoryHandle> {
        let wide_path: Vec<u16> = path.as_os_str().encode_wide().chain([0]).collect();
        // The reparse-point flag opens a link itself; the attribute check below
        // then refuses it instead of silently following it.
        // SAFETY: `wide_path` is NUL-terminated and outlives the call.
        let handle = unsafe {
            CreateFileW(
                PCWSTR(wide_path.as_ptr()),
                FILE_GENERIC_READ.0 | 0x4000_0000,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                None,
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                None,
            )
        }
        .map_err(|_| unsafe_path())?;
        // SAFETY: CreateFileW transferred ownership of the handle.
        let handle = unsafe { OwnedHandle::from_raw_handle(handle.0) };
        wrap(handle)
    }

    pub(super) fn open_below(
        parent: &DirectoryHandle,
        component: &str,
    ) -> InstallResult<DirectoryHandle> {
        let name: Vec<u16> = wide(component);
        if name.is_empty() {
            return Err(unsafe_path());
        }
        let unicode = UNICODE_STRING {
            Length: (name.len() * 2) as u16,
            MaximumLength: (name.len() * 2) as u16,
            Buffer: windows::core::PWSTR(name.as_ptr() as *mut u16),
        };
        let attributes = OBJECT_ATTRIBUTES {
            Length: std::mem::size_of::<OBJECT_ATTRIBUTES>() as u32,
            RootDirectory: parent.handle(),
            ObjectName: &unicode,
            Attributes: OBJ_CASE_INSENSITIVE,
            SecurityDescriptor: std::ptr::null(),
            SecurityQualityOfService: std::ptr::null(),
        };
        let mut status_block = IO_STATUS_BLOCK::default();
        let mut opened = HANDLE::default();
        // SAFETY: every pointer is live for the call, `name`/`attributes` outlive
        // it and no EA buffer is supplied. `RootDirectory` makes the single
        // component relative to the already-verified parent.
        let status = unsafe {
            NtCreateFile(
                &mut opened,
                windows::Win32::Storage::FileSystem::FILE_ACCESS_RIGHTS(GENERIC_READ_WRITE),
                &attributes,
                &mut status_block,
                None,
                Default::default(),
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                FILE_OPEN,
                FILE_DIRECTORY_FILE
                    | FILE_OPEN_REPARSE_POINT
                    | FILE_OPEN_FOR_BACKUP_INTENT
                    | FILE_SYNCHRONOUS_IO_NONALERT,
                None,
                0,
            )
        };
        if status.0 < 0 || opened.is_invalid() {
            return Err(unsafe_path());
        }
        // SAFETY: the handle was just opened and is owned here.
        let handle = unsafe { OwnedHandle::from_raw_handle(opened.0) };
        wrap(handle)
    }

    /// Take ownership of one opened directory handle after verifying its type.
    fn wrap(handle: OwnedHandle) -> InstallResult<DirectoryHandle> {
        let mut information = BY_HANDLE_FILE_INFORMATION::default();
        // SAFETY: the handle is live and the out-structure is correctly sized.
        unsafe { GetFileInformationByHandle(HANDLE(handle.as_raw_handle()), &mut information) }
            .map_err(|_| io_failure())?;
        if information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT.0 != 0
            || information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY.0 == 0
        {
            return Err(unsafe_path());
        }
        let index =
            (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow);
        if index == 0 {
            // The file system reports no stable file index, so the opened object
            // cannot be tied to the identity validation captured.
            return Err(unsafe_path());
        }
        let raw = handle.as_raw_handle() as isize;
        std::mem::forget(handle);
        Ok(DirectoryHandle {
            raw,
            identity: FileIdentity {
                volume: information.dwVolumeSerialNumber,
                index,
            },
        })
    }

    impl DirectoryHandle {
        /// The raw handle, borrowed for the duration of one OS call.
        pub(crate) fn handle(&self) -> HANDLE {
            HANDLE(self.raw as RawHandle)
        }
    }

    impl Drop for DirectoryHandle {
        fn drop(&mut self) {
            if self.raw > 0 {
                // SAFETY: the handle is owned by this value.
                unsafe {
                    let _ = CloseHandle(self.handle());
                }
            }
        }
    }
}
