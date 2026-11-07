//! The outside-root removal coordinator (OTP-25 F7).
//!
//! An uninstall run by the *installed* maintenance engine is executing an image
//! inside the very tree it must remove, and Windows refuses to delete a mapped
//! image. The bounded cleanup helper exists for exactly that case, but the helper
//! must run from outside the removable root and be supervised by a short-lived
//! launcher. This module stages both images in a private directory outside the
//! install root, drives the Task 10 launcher protocol, and accepts a result only
//! when
//!
//! 1. the platform obtained an authenticated helper completion (and removed the
//!    helper image, observing its absence), and
//! 2. every owned entry the coordinator was given is *observed* absent.
//!
//! Anything else is [`FailureCode::CleanupPending`]. The coordinator never claims
//! a removal it did not see.
//!
//! Native execution is BLOCKED on this Linux host: the launcher protocol is
//! Windows-only and every other host answers `cleanup-pending`, which is the
//! fail-closed verdict. The staging, ordering and observation logic here is
//! portable and is exercised by the portable suite through a scripted platform.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::cleanup::{CleanupEntry, HelperRun, MAX_CLEANUP_ENTRIES, MAX_CLEANUP_TIMEOUT};
use crate::model::{Digest, FailureCode, InstallFailure, InstallResult};
use crate::paths::{InstallPaths, cleanup_helper_file};
use crate::platform::{OwnedEntry, OwnedKind, Platform};

/// Everything the coordinator needs to name one helper run.
pub struct RemovalRequest<'a> {
    /// The installed, authenticated cleanup helper image.
    pub helper_source: &'a Path,
    /// Its exact SHA-256.
    pub helper_sha256: Digest,
    /// The OS-verified publisher the helper must report.
    pub publisher: &'a str,
    /// The transaction the completion is bound to.
    pub transaction: &'a str,
}

/// A staged helper/launcher pair outside the install root.
#[derive(Debug)]
pub struct RemovalCoordinator {
    directory: PathBuf,
    launcher: PathBuf,
    helper: PathBuf,
    helper_sha256: Digest,
    publisher: String,
    transaction: String,
    timeout: Duration,
}

impl RemovalCoordinator {
    /// Stage a byte copy of the cleanup helper and a launcher image outside the
    /// root, or `None` when this installation carries no cleanup helper.
    ///
    /// The staging directory is created exclusively (an existing one is never
    /// reused) with owner-only permissions. Both copies are read back and checked
    /// before use; the helper's exact digest is the one the receipt claims.
    pub fn stage(
        paths: &InstallPaths,
        request: &RemovalRequest<'_>,
    ) -> InstallResult<Option<Self>> {
        if !request.helper_source.is_file() {
            return Ok(None);
        }
        let directory = staging_directory(request.transaction)?;
        let launcher_source = launcher_source(paths)?;
        let (launcher, helper) = copy_images(&directory, &launcher_source, request)?;
        Ok(Some(Self {
            directory,
            launcher,
            helper,
            helper_sha256: request.helper_sha256,
            publisher: request.publisher.to_owned(),
            transaction: request.transaction.to_owned(),
            timeout: MAX_CLEANUP_TIMEOUT,
        }))
    }

    /// Run the helper for the owned entries still present and require observed
    /// removal of every one of them.
    pub fn complete(
        &self,
        platform: &impl Platform,
        paths: &InstallPaths,
        remaining: &[OwnedEntry],
    ) -> InstallResult<()> {
        let capability = new_capability()?;
        let entries = remaining_entries(remaining)?;
        let run = HelperRun {
            helper_path: &self.helper,
            helper_sha256: self.helper_sha256,
            publisher: &self.publisher,
            transaction: &self.transaction,
            capability: &capability,
            root: paths.root(),
            entries,
            timeout: self.timeout,
        };
        platform
            .run_cleanup(&run, &self.launcher)
            .map_err(deferred_removal)?;
        // The staged images and directory are the installer's own residue outside
        // the root: their absence must be observed too.
        self.discard()?;
        for entry in remaining {
            if OwnedEntry::from_claim(paths, entry.claim())?.is_some() {
                return Err(pending());
            }
        }
        Ok(())
    }

    /// Remove the staging directory and verify it is gone.
    fn discard(&self) -> InstallResult<()> {
        let _ = fs::remove_file(&self.helper);
        let _ = fs::remove_file(&self.launcher);
        let _ = fs::remove_dir(&self.directory);
        match fs::symlink_metadata(&self.directory) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            _ => Err(pending()),
        }
    }
}

impl Drop for RemovalCoordinator {
    fn drop(&mut self) {
        // Best effort on every path that never reached `discard`; the explicit
        // path above is the one whose absence is observed.
        let _ = fs::remove_file(&self.helper);
        let _ = fs::remove_file(&self.launcher);
        let _ = fs::remove_dir(&self.directory);
    }
}

/// The installed cleanup helper image of a receipt claim set, if this release
/// carries one and it is present with its claimed identity.
pub fn cleanup_helper_source(
    paths: &InstallPaths,
    claims: &[crate::platform::OwnedClaim],
) -> InstallResult<Option<(PathBuf, Digest)>> {
    let name = cleanup_helper_file(paths.target());
    for claim in claims {
        if claim.kind != OwnedKind::File {
            continue;
        }
        let matches_name = claim.path == name || claim.path.ends_with(&format!("/{name}"));
        if !matches_name {
            continue;
        }
        let Some(digest) = claim.sha256 else {
            continue;
        };
        if let Some(entry) = OwnedEntry::from_claim(paths, claim)? {
            return Ok(Some((entry.path().rendered().to_path_buf(), digest)));
        }
    }
    Ok(None)
}

fn staging_directory(transaction: &str) -> InstallResult<PathBuf> {
    let suffix: String = transaction.chars().take(16).collect();
    let directory = std::env::temp_dir().join(format!("veles-bridge-cleanup-{suffix}"));
    fs::create_dir(&directory).map_err(|_| pending())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))
            .map_err(|_| pending())?;
    }
    Ok(directory)
}

/// The image that understands the launcher mode: the published maintenance engine
/// when it exists, otherwise this process's own image (an external engine run).
fn launcher_source(paths: &InstallPaths) -> InstallResult<PathBuf> {
    let published = paths.maintenance_engine_path();
    if published.is_file() {
        return Ok(published);
    }
    std::env::current_exe().map_err(|_| pending())
}

fn copy_images(
    directory: &Path,
    launcher_source: &Path,
    request: &RemovalRequest<'_>,
) -> InstallResult<(PathBuf, PathBuf)> {
    let extension = if cfg!(windows) { ".exe" } else { "" };
    let launcher = directory.join(format!("launcher{extension}"));
    let helper = directory.join(format!("helper{extension}"));
    copy_verified(launcher_source, &launcher, None)?;
    copy_verified(request.helper_source, &helper, Some(request.helper_sha256))?;
    Ok((launcher, helper))
}

fn copy_verified(source: &Path, destination: &Path, expected: Option<Digest>) -> InstallResult<()> {
    let metadata = fs::metadata(source).map_err(|_| pending())?;
    if !metadata.is_file() || metadata.len() > crate::model::MAX_INVENTORY_BYTES {
        return Err(pending());
    }
    fs::copy(source, destination).map_err(|_| pending())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(destination, fs::Permissions::from_mode(0o700))
            .map_err(|_| pending())?;
    }
    let copied = fs::read(destination).map_err(|_| pending())?;
    if copied.len() as u64 != metadata.len() {
        return Err(pending());
    }
    if let Some(expected) = expected
        && Digest::of(&copied) != expected
    {
        return Err(pending());
    }
    Ok(())
}

/// The exact helper entries for the owned paths that are still present.
fn remaining_entries(remaining: &[OwnedEntry]) -> InstallResult<Vec<CleanupEntry>> {
    if remaining.len() > MAX_CLEANUP_ENTRIES {
        return Err(pending());
    }
    let mut entries = Vec::with_capacity(remaining.len());
    for entry in remaining {
        let claim = entry.claim();
        let metadata = fs::symlink_metadata(entry.path().rendered()).map_err(|_| pending())?;
        let (kind, size, sha256): (OwnedKind, u64, Option<Digest>) = match claim.kind {
            OwnedKind::File => (
                OwnedKind::File,
                metadata.len(),
                Some(claim.sha256.ok_or_else(pending)?),
            ),
            OwnedKind::Symlink => (
                OwnedKind::Symlink,
                metadata.len(),
                Some(claim.sha256.ok_or_else(pending)?),
            ),
            OwnedKind::Directory => (OwnedKind::Directory, 0, None),
        };
        entries.push(CleanupEntry {
            path: claim.path.clone(),
            kind,
            size,
            sha256,
        });
    }
    // Leaves first and directories deepest-first is the order the helper requires.
    entries.sort_by(|left, right| {
        right
            .kind
            .is_directory()
            .cmp(&left.kind.is_directory())
            .then_with(|| {
                right
                    .path
                    .matches('/')
                    .count()
                    .cmp(&left.path.matches('/').count())
            })
            .then_with(|| left.path.cmp(&right.path))
    });
    Ok(entries)
}

/// A fresh 32-byte single-use completion key from the operating system.
fn new_capability() -> InstallResult<[u8; 32]> {
    let mut key = [0u8; 32];
    #[cfg(unix)]
    {
        use std::io::Read;
        fs::File::open("/dev/urandom")
            .and_then(|mut source| source.read_exact(&mut key))
            .map_err(|_| pending())?;
        Ok(key)
    }
    #[cfg(windows)]
    {
        // SAFETY: the buffer is a live 32-byte array and the system-preferred RNG
        // needs no algorithm handle.
        let status = unsafe {
            windows::Win32::Security::Cryptography::BCryptGenRandom(
                None,
                &mut key,
                windows::Win32::Security::Cryptography::BCRYPT_USE_SYSTEM_PREFERRED_RNG,
            )
        };
        if status.is_ok() { Ok(key) } else { Err(pending()) }
    }
    #[cfg(not(any(unix, windows)))]
    {
        Err(pending())
    }
}

fn pending() -> InstallFailure {
    InstallFailure::new(FailureCode::CleanupPending)
}

fn deferred_removal(failure: InstallFailure) -> InstallFailure {
    match failure.code {
        FailureCode::UnsafePath | FailureCode::RecoveryRequired => failure,
        _ => pending(),
    }
}
