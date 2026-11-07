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

/// Prefix of the private outside-root staging directory.
const STAGING_PREFIX: &str = "veles-bridge-cleanup-";
/// The bounded identity of one staging directory, so a retry can recognise its
/// own crash residue instead of reusing or deleting foreign content.
const STAGING_MARKER: &str = ".veles-cleanup-stage";
const STAGING_MARKER_SCHEMA: &str = "veles-cleanup-stage-v1";

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
        let launcher_source = launcher_source(paths)?;
        let directory = staging_directory(paths, request)?;
        // Ownership is acquired before the first byte is copied: from here on, an
        // error or a drop removes this directory, its marker and the two named
        // images. Foreign content is never recursed into.
        let (launcher, helper) = image_paths(&directory);
        let coordinator = Self {
            directory,
            launcher,
            helper,
            helper_sha256: request.helper_sha256,
            publisher: request.publisher.to_owned(),
            transaction: request.transaction.to_owned(),
            timeout: MAX_CLEANUP_TIMEOUT,
        };
        copy_verified(&launcher_source, &coordinator.launcher, None)?;
        copy_verified(
            request.helper_source,
            &coordinator.helper,
            Some(request.helper_sha256),
        )?;
        Ok(Some(coordinator))
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
        self.discard_observed()?;
        for entry in remaining {
            if OwnedEntry::from_claim(paths, entry.claim())?.is_some() {
                return Err(pending());
            }
        }
        Ok(())
    }

    /// Remove this coordinator's staging artifact and require the observed
    /// absence of the directory. Used on every success path, including one that
    /// never needed the helper: a best-effort `Drop` is not evidence of cleanup.
    pub fn discard_observed(&self) -> InstallResult<()> {
        remove_known_stage(&self.directory)
    }
}

impl Drop for RemovalCoordinator {
    fn drop(&mut self) {
        // Best effort on every path that never reached `discard_observed`; that
        // explicit path is the one whose absence is observed and required.
        let _ = remove_known_stage(&self.directory);
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

/// The fixed staging path for one installation and transaction. Deterministic so
/// a retry can recognise the residue of a crash, and distinct per install root so
/// independent installations never collide in the shared temporary directory.
pub fn cleanup_staging_directory(paths: &InstallPaths, transaction: &str) -> PathBuf {
    let root = paths.root().to_string_lossy();
    let root_prefix = Digest::of(root.as_bytes()).to_hex();
    let suffix: String = transaction.chars().take(16).collect();
    std::env::temp_dir().join(format!("{STAGING_PREFIX}{}-{suffix}", &root_prefix[..8]))
}

/// Create the staging directory, reconciling only our own crash residue.
fn staging_directory(paths: &InstallPaths, request: &RemovalRequest<'_>) -> InstallResult<PathBuf> {
    let directory = cleanup_staging_directory(paths, request.transaction);
    reconcile_staging(paths, &directory, request)?;
    fs::create_dir(&directory).map_err(|_| pending())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))
            .map_err(|_| pending())?;
    }
    ensure_staging_is_safe(paths, &directory)?;
    // The marker is written before any image: a crash after this point is
    // identifiable as ours. A failed marker write removes the new directory.
    if write_marker(paths, request, &directory).is_err() {
        let _ = remove_known_stage(&directory);
        return Err(pending());
    }
    Ok(directory)
}

/// Remove a staging directory left by this same transaction, or refuse to touch
/// anything else.
fn reconcile_staging(
    paths: &InstallPaths,
    directory: &Path,
    request: &RemovalRequest<'_>,
) -> InstallResult<()> {
    match fs::symlink_metadata(directory) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(_) => return Err(pending()),
        Ok(metadata) if metadata.is_dir() && !metadata.file_type().is_symlink() => {}
        Ok(_) => return Err(pending()),
    }
    ensure_staging_is_safe(paths, directory)?;
    match fs::symlink_metadata(directory.join(STAGING_MARKER)) {
        Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => {
            let expected = marker_bytes(paths, request)?;
            let actual = fs::read(directory.join(STAGING_MARKER)).map_err(|_| pending())?;
            if actual != expected {
                // Another installation or transaction, or a forged marker: keep
                // the directory and fail closed.
                return Err(pending());
            }
        }
        Ok(_) => return Err(pending()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            // A crash between `create_dir` and the marker leaves an empty
            // directory; anything else is not ours.
            let empty = fs::read_dir(directory)
                .map(|mut entries| entries.next().is_none())
                .unwrap_or(false);
            if !empty {
                return Err(pending());
            }
        }
        Err(_) => return Err(pending()),
    }
    remove_known_stage(directory)
}

/// The bounded identity of one staging directory. The temporary path is fixed,
/// so its contents must prove whose they are before a retry may remove them.
fn marker_bytes(paths: &InstallPaths, request: &RemovalRequest<'_>) -> InstallResult<Vec<u8>> {
    let root = paths.root().to_str().ok_or_else(pending)?;
    Ok(format!(
        "{STAGING_MARKER_SCHEMA}\n{}\n{}\n{}\n",
        request.transaction,
        request.helper_sha256.to_hex(),
        root
    )
    .into_bytes())
}

fn write_marker(
    paths: &InstallPaths,
    request: &RemovalRequest<'_>,
    directory: &Path,
) -> InstallResult<()> {
    let bytes = marker_bytes(paths, request)?;
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(directory.join(STAGING_MARKER))
        .map_err(|_| pending())?;
    use std::io::Write;
    file.write_all(&bytes).map_err(|_| pending())?;
    file.sync_all().map_err(|_| pending())
}

/// Require the staging directory to be outside the removable root and private.
fn ensure_staging_is_safe(paths: &InstallPaths, directory: &Path) -> InstallResult<()> {
    let metadata = fs::symlink_metadata(directory).map_err(|_| pending())?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(pending());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Err(pending());
        }
    }
    let root = fs::canonicalize(paths.root()).unwrap_or_else(|_| paths.root().to_path_buf());
    let staged = fs::canonicalize(directory).map_err(|_| pending())?;
    if staged.starts_with(&root) || root.starts_with(&staged) {
        // A temporary directory inside (or containing) the removable root is not
        // outside-root staging: the helper would be removing itself.
        return Err(pending());
    }
    Ok(())
}

/// Remove only the two named images, the marker and the now-empty directory.
/// Foreign entries are preserved: `remove_dir` then fails and the call reports
/// pending rather than recursing.
fn remove_known_stage(directory: &Path) -> InstallResult<()> {
    let (launcher, helper) = image_paths(directory);
    for path in [launcher, helper, directory.join(STAGING_MARKER)] {
        match fs::symlink_metadata(&path) {
            Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_file() => {
                return Err(pending());
            }
            Ok(_) => fs::remove_file(&path).map_err(|_| pending())?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(_) => return Err(pending()),
        }
    }
    match fs::remove_dir(directory) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(pending()),
    }
}

/// The two staged image names (the platform executable suffix applies).
fn image_paths(directory: &Path) -> (PathBuf, PathBuf) {
    let extension = if cfg!(windows) { ".exe" } else { "" };
    (
        directory.join(format!("launcher{extension}")),
        directory.join(format!("helper{extension}")),
    )
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
        if status.is_ok() {
            Ok(key)
        } else {
            Err(pending())
        }
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
