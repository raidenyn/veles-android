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
//!
//! # Adversary and scope
//!
//! The guarantee is: every mutation is performed relative to an opened,
//! identity-verified directory handle, the identity is checked **immediately
//! before** the mutation, and a mismatch aborts instead of proceeding. Creation
//! and population are handle-relative too: the Windows carrier destination is
//! created with an exclusive `NtCreateFile` below the anchored parent and filled
//! through that handle, so no pathname is resolved for it. A component above the
//! anchor is opened with `O_NOFOLLOW`/`FILE_OPEN_REPARSE_POINT`; a link there is
//! refused, never followed, and the path is never canonicalised first (a
//! canonicalised walk would silently accept a tree the link points at).
//!
//! **Out of scope (Task 2 policy, reconfirmed for this task):** a *same-user*
//! attacker who can already write inside the private `0700` root. On macOS
//! `unlinkat` deletes a directory *entry*, so such an attacker can rename an
//! entry between the identity re-read and the unlink; macOS has no `funlinkat`
//! that would bind the unlink to the verified object. The installer does not try
//! to win that race: it verifies identity immediately before the deletion,
//! refuses to overwrite an unexpected entry where the OS allows it, and reports
//! the mismatch rather than claiming atomicity it cannot provide. (On Windows
//! the deletion itself is handle-bound, and the carrier destination is created
//! through the anchor, so the same residual does not exist there.) The threats
//! actually defended against are a *different* principal, a link planted in place
//! of an installer-owned directory, and a crash between steps — not a malicious
//! same-user administrator.

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

/// Open one absolute directory by walking **every** component from the file
/// system root with no-follow semantics.
///
/// This is the entry point for a path the caller has not already resolved (the
/// cleanup helper opening the install root named by a request, the installer
/// creating a layout component). The path is **not** canonicalised first: doing
/// so would resolve a planted link above the root and then walk the *outside*
/// tree the link points at, which is exactly the redirect this guard must
/// refuse. Instead the lexical components are opened one at a time with
/// `O_NOFOLLOW`/`FILE_OPEN_REPARSE_POINT` from the file-system/volume root, so
/// any link component fails closed with `UnsafePath`. The returned handle is the
/// anchor every mutation is performed relative to.
///
/// A caller that has a canonical path (one whose ancestors were already resolved
/// by validation, such as [`ValidatedPath`](super::super::ValidatedPath)) passes
/// that path; a caller that has a path with a link anywhere above it is refused,
/// even when the link resolves to a directory. That is deliberate: the installer
/// only mutates a tree it created with no links in it.
pub(crate) fn open_anchored(path: &Path) -> InstallResult<DirectoryHandle> {
    if !path.is_absolute() {
        return Err(unsafe_path());
    }
    platform::open_walked(path)
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

    /// Open an absolute path by walking every component from `/` with
    /// `O_NOFOLLOW`, so no component can be a link that redirects the walk.
    ///
    /// The path is used exactly as given (never canonicalised): the first
    /// component must be the root and every other one a normal component, so a
    /// relative path or one containing `.`/`..` cannot escape the walk.
    pub(super) fn open_walked(path: &Path) -> InstallResult<DirectoryHandle> {
        use std::path::Component;

        let mut components = path.components();
        if !matches!(components.next(), Some(Component::RootDir)) {
            return Err(unsafe_path());
        }
        let root_name = CString::new("/").map_err(|_| unsafe_path())?;
        // SAFETY: the literal is NUL-terminated and outlives the call.
        let root = unsafe { open(root_name.as_ptr(), O_RDONLY | O_DIRECTORY | O_CLOEXEC) };
        if root < 0 {
            return Err(io_failure());
        }
        let mut current = match wrap(root) {
            Ok(handle) => handle,
            Err(failure) => {
                // SAFETY: `root` was just opened and is owned here.
                unsafe {
                    let _ = close(root);
                }
                return Err(failure);
            }
        };
        for component in components {
            match component {
                Component::Normal(name) => {
                    let name = name.to_str().ok_or_else(unsafe_path)?;
                    if name.is_empty() || name == "." || name == ".." {
                        return Err(unsafe_path());
                    }
                    current = open_below(&current, name)?;
                }
                // A second root, `.`/`..` or a platform prefix: none may appear
                // in the absolute path this walk accepts.
                _ => return Err(unsafe_path()),
            }
        }
        Ok(current)
    }

    impl DirectoryHandle {
        /// Transfer the opened descriptor to the caller (the handle stops
        /// closing it on drop).
        pub(crate) fn into_fd(self) -> c_int {
            let raw = self.raw as c_int;
            std::mem::forget(self);
            raw
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
        FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE, GetFileInformationByHandle,
        OPEN_EXISTING,
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
    /// `GENERIC_READ`: enough to traverse into a child with `RootDirectory`.
    const GENERIC_READ_ONLY: u32 = 0x8000_0000;

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().collect()
    }

    pub(super) fn open_absolute(path: &Path) -> InstallResult<DirectoryHandle> {
        open_absolute_with(path, GENERIC_READ_WRITE)
    }

    /// [`open_absolute`] with an explicit access mask: the walk above the anchor
    /// only needs read/traverse access (a standard user cannot open `C:\` for
    /// write), while the anchor itself is opened with the access its mutations
    /// need.
    fn open_absolute_with(path: &Path, access: u32) -> InstallResult<DirectoryHandle> {
        let wide_path: Vec<u16> = path.as_os_str().encode_wide().chain([0]).collect();
        // The reparse-point flag opens a link itself; the attribute check below
        // then refuses it instead of silently following it.
        // SAFETY: `wide_path` is NUL-terminated and outlives the call.
        let handle = unsafe {
            CreateFileW(
                PCWSTR(wide_path.as_ptr()),
                access,
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

    /// Open an absolute path by walking every component from its volume root
    /// with `FILE_OPEN_REPARSE_POINT`, so no component can be a reparse point
    /// that redirects the walk.
    ///
    /// The path is used exactly as given (never canonicalised), so a reparse
    /// point above the anchor is refused instead of resolved.
    ///
    /// Only the anchor itself (the last component) is opened with write access:
    /// the components above it are read/traverse only, because a standard user
    /// cannot open `C:\` or `%USERPROFILE%` for writing and the walk has no
    /// reason to ask for it.
    pub(super) fn open_walked(path: &Path) -> InstallResult<DirectoryHandle> {
        use std::path::{Component, Prefix};

        let mut components = path.components();
        // A drive-letter root (`C:\`) is the only anchor this adapter can open
        // component by component; a UNC share or a verbatim device path is
        // refused rather than walked with an unverified ancestor.
        let volume = match components.next() {
            Some(Component::Prefix(prefix)) => match prefix.kind() {
                Prefix::Disk(letter) | Prefix::VerbatimDisk(letter) => {
                    format!("{}:\\", char::from(letter))
                }
                _ => return Err(unsafe_path()),
            },
            _ => return Err(unsafe_path()),
        };
        // A drive-relative path (`C:foo`) has no root component and must never
        // be treated as a child of the volume root.
        if !matches!(components.next(), Some(Component::RootDir)) {
            return Err(unsafe_path());
        }
        let mut names = Vec::new();
        for component in components {
            match component {
                Component::Normal(name) => {
                    let name = name.to_str().ok_or_else(unsafe_path)?;
                    if name.is_empty() || name == "." || name == ".." {
                        return Err(unsafe_path());
                    }
                    names.push(name.to_owned());
                }
                _ => return Err(unsafe_path()),
            }
        }
        let mut current = open_absolute_with(
            Path::new(&volume),
            if names.is_empty() {
                GENERIC_READ_WRITE
            } else {
                GENERIC_READ_ONLY
            },
        )?;
        for (index, name) in names.iter().enumerate() {
            let access = if index + 1 == names.len() {
                GENERIC_READ_WRITE
            } else {
                GENERIC_READ_ONLY
            };
            current = open_below_with(&current, name, access)?;
        }
        Ok(current)
    }

    impl DirectoryHandle {
        /// Transfer the opened handle to the caller (the handle stops closing it
        /// on drop).
        pub(crate) fn into_file(self) -> std::fs::File {
            let raw = self.raw as RawHandle;
            // SAFETY: `raw` is the live handle this value owns; forgetting the
            // value transfers that ownership to the returned `File`.
            unsafe {
                std::mem::forget(self);
                std::fs::File::from_raw_handle(raw)
            }
        }
    }

    pub(super) fn open_below(
        parent: &DirectoryHandle,
        component: &str,
    ) -> InstallResult<DirectoryHandle> {
        open_below_with(parent, component, GENERIC_READ_WRITE)
    }

    fn open_below_with(
        parent: &DirectoryHandle,
        component: &str,
        access: u32,
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
                windows::Win32::Storage::FileSystem::FILE_ACCESS_RIGHTS(access),
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
