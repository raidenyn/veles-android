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

/// [`open_regular_below`] for a file an OS trust assessment will read through the
/// returned handle. The walk is identical; where the OS has sharing modes
/// (Windows) the final file is held `FILE_SHARE_READ`-only so its bytes cannot
/// change under the assessment.
#[cfg_attr(not(any(windows, test)), allow(dead_code))]
pub(super) fn open_for_assessment_below(
    root: &Path,
    parts: &[&str],
    between: &mut dyn FnMut(),
) -> InstallResult<File> {
    imp::open_for_assessment_below(root, parts, between)
}

// The `open(2)`/`errno` constants below are written out per target because this
// crate takes no `libc` dependency. They are listed only for the Linux
// architectures whose values were checked (the asm-generic set, plus the 32/64-bit
// ARM set that differs for `O_DIRECTORY`/`O_NOFOLLOW`); mips, powerpc, sparc and
// alpha use different numbers and are refused (see `unsupported` below) instead of
// being handed a silently wrong flag.
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
    // `O_NOFOLLOW` is shared with the rest of the crate (one audited table), so the
    // anchored walk and the single-file opens cannot disagree about it.
    #[cfg(all(
        any(target_os = "linux", target_os = "android"),
        not(any(target_arch = "arm", target_arch = "aarch64"))
    ))]
    mod flags {
        pub const O_DIRECTORY: i32 = 0o200_000;
    }
    #[cfg(all(
        any(target_os = "linux", target_os = "android"),
        any(target_arch = "arm", target_arch = "aarch64")
    ))]
    mod flags {
        pub const O_DIRECTORY: i32 = 0o040_000;
    }
    #[cfg(any(target_os = "macos", target_os = "ios"))]
    mod flags {
        pub const O_DIRECTORY: i32 = 0x10_0000;
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
    use crate::platform::NO_FOLLOW as O_NOFOLLOW;
    use flags::O_DIRECTORY;

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

    /// Unix has no sharing modes: the assessment handle is the anchored handle.
    #[cfg_attr(not(test), allow(dead_code))]
    pub(super) fn open_for_assessment_below(
        root: &Path,
        parts: &[&str],
        between: &mut dyn FnMut(),
    ) -> InstallResult<File> {
        open_regular_below(root, parts, between)
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

    pub(super) fn open_for_assessment_below(
        root: &Path,
        parts: &[&str],
        between: &mut dyn FnMut(),
    ) -> InstallResult<File> {
        crate::platform::anchored_open_assessment_below(root, parts, between)
    }
}

/// Any other target has no audited handle-relative open here, and this module
/// does not pretend otherwise. A component-by-component pathname check followed by
/// one open of the whole path checks one object and opens another (the race this
/// module exists to remove), and the process-wide no-follow flag is only known for
/// the audited targets, so a "best effort" walk would give a false sense of
/// containment. The target is refused outright with
/// [`FailureCode::UnsupportedTarget`]: nothing under the package root is opened, and
/// the `between` seam is never reached.
///
/// Compiled for tests on every host so the refusal is exercised on the audited
/// targets too; only unsupported targets *use* it.
#[cfg(any(
    test,
    not(any(
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
    ))
))]
mod unsupported {
    use std::fs::File;
    use std::path::Path;

    use crate::model::{FailureCode, InstallFailure, InstallResult};

    #[allow(dead_code)]
    pub(super) fn open_regular_below(
        _root: &Path,
        _parts: &[&str],
        _between: &mut dyn FnMut(),
    ) -> InstallResult<File> {
        Err(InstallFailure::new(FailureCode::UnsupportedTarget))
    }
}

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
    pub(super) use super::unsupported::open_regular_below;
    pub(super) use super::unsupported::open_regular_below as open_for_assessment_below;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::FailureCode;

    /// N3: a target without an audited anchored open must refuse the read, not fall
    /// back to a pathname walk that re-opens a path it already checked.
    #[test]
    fn a_target_without_an_anchored_open_refuses_and_opens_nothing() {
        let base = std::env::temp_dir().join(format!("veles-unsupported-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("payload");
        std::fs::create_dir_all(root.join("dir")).expect("tree");
        std::fs::write(root.join("dir/host"), b"the package's host").expect("host");

        let mut reached_the_race_window = false;
        let outcome = unsupported::open_regular_below(&root, &["dir", "host"], &mut || {
            reached_the_race_window = true;
        });
        let _ = std::fs::remove_dir_all(&base);
        assert_eq!(
            outcome
                .expect_err("an unaudited target never opens a package entry")
                .code,
            FailureCode::UnsupportedTarget
        );
        assert!(
            !reached_the_race_window,
            "the refusal must precede any traversal, not follow a check-then-open walk"
        );
    }
}
