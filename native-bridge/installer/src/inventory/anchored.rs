//! Anchored, directory-handle-relative opens for package inventory traversal.
//!
//! A pathname walk (`symlink_metadata` on each component, then one open of the whole
//! path) checks one object and opens another: a writer that swaps an ancestor for a
//! link between the two redirects the read outside the payload root. Here the root
//! is opened once without following a link, and every component is opened *relative
//! to the handle of its parent* with no-follow semantics (`openat` +
//! `O_NOFOLLOW | O_DIRECTORY` on Unix, `NtCreateFile` + `RootDirectory` +
//! `FILE_OPEN_REPARSE_POINT` on Windows). Each handle is checked for what it is
//! (directory, regular file, not a reparse point) and then retained as the anchor
//! for the next step, so what is finally read is the file reached through the very
//! handles that were checked; replacing a directory afterwards changes nothing the
//! walk already holds.
//!
//! Scope: this protects reads of the *package source*, which an untrusted party may
//! be writing while the installer runs. It makes no claim about the 0700 private
//! install root (same-user races there are an accepted exclusion).

use std::fs::File;
use std::path::Path;

use crate::model::InstallResult;

/// Open `parts` (already-normalized, non-empty path components) below `root` and
/// return the leaf as an open regular file.
///
/// `between` runs once, after every ancestor directory is open and before the leaf
/// is: the moment a racing writer would swap an ancestor. Production passes a
/// no-op; a test uses it to do exactly that swap.
///
/// Errors: a missing component is `InvalidPackage`; a link/reparse point at any
/// level or a leaf that is not a regular file is `UnsafePath`; anything else `Io`.
pub(super) fn open_regular_below(
    root: &Path,
    parts: &[&str],
    between: &mut dyn FnMut(),
) -> InstallResult<File> {
    imp::open_regular_below(root, parts, between)
}

// The `open(2)`/`errno` constants below are written out per target because this
// crate takes no `libc` dependency. They are listed only for the Linux
// architectures whose values were checked (the asm-generic set, plus the 32/64-bit
// ARM set that differs for `O_DIRECTORY`/`O_NOFOLLOW`); mips, powerpc, sparc and
// alpha use different numbers and take the pathname fallback below instead of a
// silently wrong flag.
#[cfg(any(
    all(
        any(target_os = "linux", target_os = "android"),
        any(
            target_arch = "x86",
            target_arch = "x86_64",
            target_arch = "arm",
            target_arch = "aarch64",
            target_arch = "riscv64",
            target_arch = "loongarch64",
            target_arch = "s390x",
        )
    ),
    target_os = "macos",
    target_os = "ios"
))]
mod imp {
    use std::ffi::CString;
    use std::fs::{File, OpenOptions};
    use std::io;
    use std::os::fd::{AsRawFd, FromRawFd};
    use std::os::raw::{c_char, c_int};
    use std::os::unix::fs::OpenOptionsExt;
    use std::path::Path;

    use crate::model::{FailureCode, InstallFailure, InstallResult};

    // `open(2)` flag values are per OS (and, on Linux, per architecture family).
    #[cfg(all(
        any(target_os = "linux", target_os = "android"),
        not(any(target_arch = "arm", target_arch = "aarch64"))
    ))]
    mod flags {
        pub const O_DIRECTORY: i32 = 0o200_000;
        pub const O_NOFOLLOW: i32 = 0o400_000;
    }
    #[cfg(all(
        any(target_os = "linux", target_os = "android"),
        any(target_arch = "arm", target_arch = "aarch64")
    ))]
    mod flags {
        pub const O_DIRECTORY: i32 = 0o040_000;
        pub const O_NOFOLLOW: i32 = 0o100_000;
    }
    #[cfg(any(target_os = "macos", target_os = "ios"))]
    mod flags {
        pub const O_DIRECTORY: i32 = 0x10_0000;
        pub const O_NOFOLLOW: i32 = 0x0100;
    }
    #[cfg(any(target_os = "linux", target_os = "android"))]
    const O_CLOEXEC: c_int = 0o2_000_000;
    #[cfg(any(target_os = "macos", target_os = "ios"))]
    const O_CLOEXEC: c_int = 0x100_0000;
    /// A FIFO or device named as the leaf must not block the open.
    #[cfg(any(target_os = "linux", target_os = "android"))]
    const O_NONBLOCK: c_int = 0o4_000;
    #[cfg(any(target_os = "macos", target_os = "ios"))]
    const O_NONBLOCK: c_int = 0x0004;
    use flags::{O_DIRECTORY, O_NOFOLLOW};

    #[cfg(any(target_os = "linux", target_os = "android"))]
    const ELOOP: i32 = 40;
    #[cfg(any(target_os = "macos", target_os = "ios"))]
    const ELOOP: i32 = 62;
    const ENOENT: i32 = 2;
    const ENOTDIR: i32 = 20;

    unsafe extern "C" {
        fn openat(directory: c_int, path: *const c_char, flags: c_int, ...) -> c_int;
    }

    fn failure(code: FailureCode) -> InstallFailure {
        InstallFailure::new(code)
    }

    /// Map the errno of a refused no-follow open into the installer vocabulary.
    fn classify(error: &io::Error) -> InstallFailure {
        match error.raw_os_error() {
            Some(ENOENT) => failure(FailureCode::InvalidPackage),
            // `O_NOFOLLOW` on a link, or `O_DIRECTORY` on a non-directory.
            Some(ELOOP) | Some(ENOTDIR) => failure(FailureCode::UnsafePath),
            _ => failure(FailureCode::Io),
        }
    }

    fn open_below(parent: &File, name: &str, directory: bool) -> InstallResult<File> {
        let name = CString::new(name).map_err(|_| failure(FailureCode::UnsafePath))?;
        let mut flags = O_NOFOLLOW | O_CLOEXEC;
        if directory {
            flags |= O_DIRECTORY;
        } else {
            flags |= O_NONBLOCK;
        }
        // SAFETY: `parent` is a live descriptor, `name` is a NUL-terminated name with
        // no separator (a single normalized component), and the returned descriptor
        // is owned exclusively by the `File` built from it below.
        let descriptor = unsafe { openat(parent.as_raw_fd(), name.as_ptr(), flags) };
        if descriptor < 0 {
            return Err(classify(&io::Error::last_os_error()));
        }
        // SAFETY: `descriptor` was just returned by `openat` and is not shared.
        Ok(unsafe { File::from_raw_fd(descriptor) })
    }

    pub(super) fn open_regular_below(
        root: &Path,
        parts: &[&str],
        between: &mut dyn FnMut(),
    ) -> InstallResult<File> {
        // The root itself is opened without following a link: a `payload` replaced by
        // a link is refused instead of silently becoming the anchor.
        let mut current = OpenOptions::new()
            .read(true)
            .custom_flags(O_DIRECTORY | O_NOFOLLOW)
            .open(root)
            .map_err(|error| match error.raw_os_error() {
                Some(ELOOP) | Some(ENOTDIR) => failure(FailureCode::UnsafePath),
                _ => failure(FailureCode::Io),
            })?;
        let Some((leaf, ancestors)) = parts.split_last() else {
            return Err(failure(FailureCode::UnsafePath));
        };
        for name in ancestors {
            current = open_below(&current, name, true)?;
        }
        between();
        let file = open_below(&current, leaf, false)?;
        if !file
            .metadata()
            .map_err(|_| failure(FailureCode::Io))?
            .is_file()
        {
            return Err(failure(FailureCode::UnsafePath));
        }
        Ok(file)
    }
}

#[cfg(windows)]
mod imp {
    use std::fs::File;
    use std::path::Path;

    use crate::model::InstallResult;

    pub(super) fn open_regular_below(
        root: &Path,
        parts: &[&str],
        between: &mut dyn FnMut(),
    ) -> InstallResult<File> {
        crate::platform::anchored_open_regular_below(root, parts, between)
    }
}

/// Any other target (including a Linux architecture whose `open(2)` constants were
/// not checked) has no audited handle-relative open here. It keeps the
/// component-by-component pathname check (a link at any level is refused when it is
/// *observed*), which does not defend against a swap after the check.
#[cfg(not(any(
    windows,
    all(
        any(target_os = "linux", target_os = "android"),
        any(
            target_arch = "x86",
            target_arch = "x86_64",
            target_arch = "arm",
            target_arch = "aarch64",
            target_arch = "riscv64",
            target_arch = "loongarch64",
            target_arch = "s390x",
        )
    ),
    target_os = "macos",
    target_os = "ios"
)))]
mod imp {
    use std::fs::{self, File};
    use std::path::Path;

    use crate::model::{FailureCode, InstallFailure, InstallResult};
    use crate::platform::{is_link_like, open_no_follow};

    pub(super) fn open_regular_below(
        root: &Path,
        parts: &[&str],
        between: &mut dyn FnMut(),
    ) -> InstallResult<File> {
        let mut current = root.to_path_buf();
        for (index, part) in parts.iter().enumerate() {
            current.push(part);
            let metadata = fs::symlink_metadata(&current).map_err(|error| match error.kind() {
                std::io::ErrorKind::NotFound => InstallFailure::new(FailureCode::InvalidPackage),
                _ => InstallFailure::new(FailureCode::Io),
            })?;
            let is_leaf = index + 1 == parts.len();
            if is_link_like(&metadata)
                || (is_leaf && !metadata.file_type().is_file())
                || (!is_leaf && !metadata.file_type().is_dir())
            {
                return Err(InstallFailure::new(FailureCode::UnsafePath));
            }
        }
        between();
        let file = open_no_follow(&current).map_err(|_| InstallFailure::new(FailureCode::Io))?;
        if !file
            .metadata()
            .map_err(|_| InstallFailure::new(FailureCode::Io))?
            .is_file()
        {
            return Err(InstallFailure::new(FailureCode::UnsafePath));
        }
        Ok(file)
    }
}
