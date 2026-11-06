//! Native filesystem orchestration shared by the Windows and macOS adapters.
//!
//! The two native adapters differ only in the OS primitives; everything that
//! decides *what* is written where is asserted once here so a security-relevant
//! ordering cannot drift between the platforms:
//!
//! * every existing layout directory must be owned by this user and private
//!   before it is reused ([`NativeFs::validate_private_directory`]); a missing
//!   component is created and validated **relative to an anchored parent
//!   handle**, never by re-walking the rendered path, and the lock file is opened
//!   relative to the anchored root;
//! * durable writes create or truncate one validated file, write every byte,
//!   flush, read the exact bytes back and flush the parent directory;
//! * [`Platform::replace_durable`](super::Platform::replace_durable) and the state
//!   documents stage into a **freshly created** sibling on the destination
//!   volume, flush and read it back, then replace the destination directory entry
//!   atomically. The staging name is never re-adopted, so a planted file at a
//!   staging name can never be truncated or renamed over the destination;
//! * the manifest is staged next to its live destination, so the publishing
//!   rename never crosses a volume;
//! * manifest publication and registry publication stay two ordered steps, and
//!   the registry half re-reads the live manifest before writing;
//! * the manifest half applies the owned-preimage refusal *before* the first
//!   mutation, so a registration this installer does not own is left intact;
//! * removal revalidates ancestry, re-hashes the exact owned content immediately
//!   before the unlink, and unlinks **through the captured parent handle** after
//!   comparing the opened leaf's identity with the captured one;
//! * a non-empty directory is `CleanupPending` rather than a recursive delete.
//!
//! The [`NativeFs`] trait is the primitive boundary each adapter implements with
//! its own OS calls (`NtCreateFile`/`NtSetInformationFile` on Windows, no-follow
//! descriptors plus `renameat(2)`/`unlinkat(2)`/`fsync(2)` on macOS).
//! [`StdNativeFs`] is the same orchestration over `std`, used by portable tests
//! so the shared decisions are exercised on every host even though the native
//! primitives themselves only compile for their own target.
//!
//! # Durability
//!
//! The contract is *per platform*, and it is stated here rather than implied:
//!
//! * macOS: a file's bytes are durable after `fsync(2)`; a directory entry is
//!   durable after its parent directory's `fsync(2)`. Both are performed.
//! * Windows: a file's bytes are durable after `FlushFileBuffers` on its handle.
//!   A rename is performed with `NtSetInformationFile(FileRenameInformation)` on
//!   the opened source handle with `RootDirectory` set to the opened destination
//!   directory.
//!
//!   **Directory durability is not claimed.** There is no public Win32 API that
//!   documents a POSIX-style directory-entry flush, and `FlushFileBuffers` is
//!   documented for *files*: issuing it on a directory handle is only a
//!   best-effort ordering barrier, not a supported guarantee that a directory
//!   entry survives power loss. The adapter therefore
//!   (a) never claims POSIX-equivalent durability on Windows (no
//!   `REPLACEFILE_WRITE_THROUGH`/`MOVEFILE_WRITE_THROUGH` implication either),
//!   (b) fails closed when the barrier is refused — the flush maps to
//!   `CleanupPending`, never a silent success, so a caller that needs the entry
//!   durable reports a pending cleanup instead of a completed install, and
//!   (c) only commits the durable intent after it, so a lost directory entry is
//!   detected by the journal/receipt on the next run (the staged sibling and the
//!   live document are re-examined, never assumed), rather than being silently
//!   reported as a finished installation.

use std::fs;
use std::io::{self, Read, Seek, SeekFrom};
use std::path::Path;

use super::{
    InstallLock, OwnedEntry, OwnedKind, PackageKind, PackageSource, PathKind, Registration,
    StateDocument, ValidatedPath, carrier_entries_with, foreign_state, hash_file, identity_of,
    invalid_input, io_failure, is_link_like, is_owned_registration_preimage, read_no_follow,
    registration_target, replace_file_checked, state_handle, state_relative, unsafe_path,
    validate_registration_manifest, write_durable_checked,
};
use crate::model::{Digest, FailureCode, InstallFailure, InstallResult};
use crate::paths::{InstallPaths, STATE_DIR};

// Anchored, no-follow directory handles (Windows / macOS only): validation opens
// the trusted chain once and every native mutation is performed relative to it.
#[cfg(any(windows, target_os = "macos"))]
pub(crate) mod anchor;

/// The OS primitives a native adapter supplies.
///
/// Every mutation that names a [`ValidatedPath`] receives the handle validation
/// captured, including the **opened parent directory**. The primitive must
/// perform the operation relative to that handle and verify the opened leaf's
/// identity; re-walking the rendered path would re-open the race the handle
/// exists to close. Path-addressed methods exist only for work outside the
/// validated tree (creating the fixed layout, reading a package source).
pub(crate) trait NativeFs {
    /// Acquire the exclusive per-user lock (the layout must already exist).
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock>;
    /// Verify one **existing** installer-owned directory.
    ///
    /// The directory must be a real directory owned by the current user with no
    /// access for anybody else (POSIX mode/DACL). A pre-existing directory that
    /// fails this is `UnsafePath`: reusing it would let another principal plant
    /// or replace entries the installer later trusts.
    fn validate_private_directory(&self, path: &Path) -> InstallResult<()>;
    /// Create-new or truncate one validated file, write every byte, flush it and
    /// read the exact bytes back through the same no-follow boundary.
    ///
    /// The returned identity is the object the call created (for
    /// [`PathKind::NewFile`]) or the verified object it truncated (for
    /// [`PathKind::File`]), read from the opened handle. An existing file is only
    /// truncated **after** its identity was verified, and a create-new failure
    /// removes only the object this call created.
    fn write_durable(
        &self,
        path: &ValidatedPath,
        bytes: &[u8],
    ) -> InstallResult<super::FileIdentity>;
    /// Atomically replace the `live` directory entry with the `staged` one.
    ///
    /// The two paths are siblings on the destination volume; `live` may be
    /// absent (first publication). The implementation must never write through
    /// the destination inode, so another name for the same file keeps its bytes.
    fn replace_file(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()>;
    /// Read one regular file exactly, refusing to follow a final link.
    fn read_file(&self, path: &Path) -> InstallResult<Vec<u8>>;
    /// SHA-256 of one regular file read through the no-follow boundary.
    fn hash_file(&self, path: &Path) -> InstallResult<Digest>;
    /// Flush the directory that holds `path`, through its captured handle.
    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()>;
    /// Create one owner-private directory addressed by path (layout only).
    fn create_directory(&self, path: &Path) -> InstallResult<()>;
    /// Create `path`'s leaf relative to its validated parent handle.
    #[cfg_attr(not(any(windows, target_os = "macos")), allow(dead_code))]
    fn create_directory_at(&self, path: &ValidatedPath) -> InstallResult<()>;
    /// Unlink a regular file or a symbolic link **at its validated handle**.
    ///
    /// The link's target is never followed and no other name is touched. The
    /// implementation binds the unlink to the identity it opened (device/inode or
    /// volume/file id), so a leaf swapped after validation is preserved.
    fn remove_entry(&self, path: &ValidatedPath) -> InstallResult<()>;
    /// Remove one *empty* directory; `CleanupPending` when it is not empty.
    fn remove_directory(&self, path: &ValidatedPath) -> InstallResult<()>;
    /// Install the signed carrier artifact with every metadata class the OS
    /// signature assessment covers preserved.
    fn install_carrier(
        &self,
        source: &PackageSource,
        destination: &ValidatedPath,
    ) -> InstallResult<()>;
}

/// Create the fixed private layout with the adapter's native directory security.
///
/// An existing directory is never simply adopted: it must still be owned by this
/// user and private, otherwise `UnsafePath`.
pub(crate) fn native_ensure_layout(fs: &impl NativeFs, paths: &InstallPaths) -> InstallResult<()> {
    // The `Veles` container is installer-owned too; everything above it is an OS
    // known folder and is deliberately not validated (and never re-written). A
    // missing intermediate component (a fresh home, `Library/Application
    // Support`) is created with the adapter's private-directory security, one
    // validated component at a time — never with `create_dir_all`'s ambient mode.
    if let Some(parent) = paths.root().parent() {
        let mut missing = Vec::new();
        let mut current = parent;
        loop {
            match fs::symlink_metadata(current) {
                Ok(metadata) => {
                    if current == parent {
                        // The `Veles` container is installer-owned: it must be a
                        // real, private directory.
                        if is_link_like(&metadata) || !metadata.is_dir() {
                            return Err(unsafe_path());
                        }
                    } else {
                        // An OS/user known folder above it may legitimately be a
                        // link (a relocated `Application Support`, a junctioned
                        // `%LOCALAPPDATA%`): it is only required to resolve to a
                        // directory, and it is never mutated.
                        let resolved = fs::metadata(current).map_err(|_| io_failure())?;
                        if !resolved.is_dir() {
                            return Err(unsafe_path());
                        }
                    }
                    break;
                }
                Err(error) if error.kind() == io::ErrorKind::NotFound => {
                    missing.push(current.to_path_buf());
                    current = current.parent().ok_or_else(io_failure)?;
                }
                Err(_) => return Err(io_failure()),
            }
        }
        for directory in missing.iter().rev() {
            fs.create_directory(directory)?;
        }
        fs.validate_private_directory(parent)?;
    }
    for directory in [
        paths.root(),
        paths.versions(),
        paths.maintenance(),
        paths.state(),
        paths.staging(),
    ] {
        match fs::symlink_metadata(directory) {
            Ok(metadata) => {
                if is_link_like(&metadata) || !metadata.is_dir() {
                    return Err(unsafe_path());
                }
                fs.validate_private_directory(directory)?;
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                fs.create_directory(directory)?;
            }
            Err(_) => return Err(io_failure()),
        }
    }
    Ok(())
}

/// The exclusive per-user lock through the native open semantics.
pub(crate) fn native_lock(fs: &impl NativeFs, paths: &InstallPaths) -> InstallResult<InstallLock> {
    match fs::symlink_metadata(paths.lock_path()) {
        Ok(metadata) if is_link_like(&metadata) || !metadata.is_file() => {
            return Err(unsafe_path());
        }
        Ok(_) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(_) => return Err(io_failure()),
    }
    fs.lock(paths)
}

/// Write all bytes durably through the native boundary.
///
/// The returned identity is the object the write created or truncated, read from
/// the handle the primitive used: callers that stage a file bind their later
/// rename/removal to it instead of re-reading the name.
pub(crate) fn native_write_durable(
    fs: &impl NativeFs,
    path: &ValidatedPath,
    bytes: &[u8],
) -> InstallResult<super::FileIdentity> {
    path.revalidate()?;
    fs.write_durable(path, bytes)
}

/// A one-shot token that makes each staging/quarantine name unique.
pub(crate) fn unique_token() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let counter = COUNTER.fetch_add(1, Ordering::Relaxed);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_nanos())
        .unwrap_or(0);
    format!("{:x}-{:x}-{counter:x}", std::process::id(), nanos)
}

/// A **fresh** staging sibling of `path` that can never be an existing name.
///
/// The name carries a process-unique token and the write below is an exclusive
/// create, so a file planted at a predictable staging name is never adopted,
/// truncated or renamed over the destination.
fn staging_sibling(path: &ValidatedPath, suffix: &str) -> InstallResult<ValidatedPath> {
    let name = path
        .rendered()
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(unsafe_path)?;
    // Keep the staging component well inside the single-component bound even for
    // a long destination name.
    let stem: String = name.chars().take(64).collect();
    path.sibling(
        &format!(".{stem}.{}.{suffix}", unique_token()),
        PathKind::NewFile,
    )
}

/// Discard a staging sibling **this call created**, identified by `created`.
///
/// The identity is the one the write reported for the object it created, so a
/// file somebody else planted at the same name can never be adopted and deleted:
/// [`NativeFs::remove_entry`] re-verifies the name against the captured identity
/// and preserves anything else.
fn discard_staging(fs: &impl NativeFs, staging: &ValidatedPath, created: &super::FileIdentity) {
    if staging.captured_identity() != Some(created) {
        return;
    }
    let _ = fs.remove_entry(staging);
}

/// Stage `bytes` into a fresh sibling of `live`, then atomically replace `live`.
///
/// A staging name is one-shot, so it must be absent before the write: a name that
/// somebody else already occupies is never adopted, truncated, renamed over the
/// destination or deleted. The write itself removes only the object it created
/// (its identity), so a failed write cannot destroy foreign content either.
fn stage_and_replace(
    fs: &impl NativeFs,
    live: &ValidatedPath,
    bytes: &[u8],
    suffix: &str,
) -> InstallResult<()> {
    let staging = staging_sibling(live, suffix)?;
    match fs::symlink_metadata(staging.rendered()) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        // A fresh one-shot name is already occupied: refuse without touching it.
        // The caller keeps its durable intent and retries with a new name.
        Ok(_) => return Err(InstallFailure::new(FailureCode::CleanupPending)),
        Err(_) => return Err(io_failure()),
    }
    let created = native_write_durable(fs, &staging, bytes)?;
    let staged = staging.adopt(PathKind::File)?;
    if staged.captured_identity() != Some(&created) {
        // The name no longer holds the object this call wrote (and verified):
        // publishing it would install foreign bytes.
        discard_staging(fs, &staged, &created);
        return Err(InstallFailure::new(FailureCode::CleanupPending));
    }
    match native_replace_file(fs, &staged, live) {
        Ok(()) => Ok(()),
        Err(failure) => {
            // The staged object is ours and now unreferenced: remove exactly it.
            discard_staging(fs, &staged, &created);
            Err(failure)
        }
    }
}

/// Replace one owned file without writing through its inode.
pub(crate) fn native_replace_durable(
    fs: &impl NativeFs,
    path: &ValidatedPath,
    bytes: &[u8],
) -> InstallResult<()> {
    path.revalidate()?;
    if path.kind() == PathKind::NewFile {
        return native_write_durable(fs, path, bytes).map(|_| ());
    }
    if path.kind() != PathKind::File {
        return Err(unsafe_path());
    }
    stage_and_replace(fs, path, bytes, "installer-new")
}

/// Atomically replace one file and flush the destination directory.
pub(crate) fn native_replace_file(
    fs: &impl NativeFs,
    staged: &ValidatedPath,
    live: &ValidatedPath,
) -> InstallResult<()> {
    if staged.kind() != PathKind::File {
        return Err(unsafe_path());
    }
    if !matches!(live.kind(), PathKind::File | PathKind::NewFile) {
        return Err(unsafe_path());
    }
    staged.revalidate()?;
    live.revalidate()?;
    fs.replace_file(staged, live)?;
    fs.flush_parent(live)
}

/// Atomically replace one installer state document.
pub(crate) fn native_write_state(
    fs: &impl NativeFs,
    paths: &InstallPaths,
    document: StateDocument,
    bytes: &[u8],
) -> InstallResult<()> {
    let live = state_handle(paths, &state_relative(document))?;
    stage_and_replace(fs, &live, bytes, "tmp")
}

/// Read one state document exactly, rejecting links and non-files.
pub(crate) fn native_read_state(
    fs: &impl NativeFs,
    paths: &InstallPaths,
    document: StateDocument,
) -> InstallResult<Option<Vec<u8>>> {
    let absolute = paths.root().join(state_relative(document));
    match fs::symlink_metadata(&absolute) {
        Ok(metadata) if is_link_like(&metadata) || !metadata.is_file() => Err(unsafe_path()),
        Ok(_) => fs.read_file(&absolute).map(Some),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err(io_failure()),
    }
}

/// Move an uninterpretable state document to its fixed quarantine name.
pub(crate) fn native_quarantine_state(
    fs: &impl NativeFs,
    paths: &InstallPaths,
    document: StateDocument,
) -> InstallResult<()> {
    let live_relative = state_relative(document);
    match fs::symlink_metadata(paths.root().join(&live_relative)) {
        Ok(metadata) if is_link_like(&metadata) || !metadata.is_file() => {
            return Err(unsafe_path());
        }
        Ok(_) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(_) => return Err(io_failure()),
    }
    let live = ValidatedPath::new(paths, &live_relative, PathKind::File)?;
    let quarantine_relative = Path::new(STATE_DIR).join(document.quarantine_file_name());
    let quarantine = state_handle(paths, &quarantine_relative)?;
    native_replace_file(fs, &live, &quarantine)
}

/// Read the live manifest file exactly, or `None` when absent.
pub(crate) fn native_read_registration(
    fs: &impl NativeFs,
    paths: &InstallPaths,
) -> InstallResult<Option<Registration>> {
    read_registration_with(fs, paths.registration_path())
}

pub(crate) fn read_registration_with(
    fs: &impl NativeFs,
    path: &Path,
) -> InstallResult<Option<Registration>> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if is_link_like(&metadata) || !metadata.is_file() {
                return Err(foreign_state());
            }
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err(io_failure()),
    }
    let bytes = fs.read_file(path).map_err(|_| io_failure())?;
    Registration::new(bytes)
        .map(Some)
        .map_err(|_| foreign_state())
}

/// Publish the manifest file (publication step one).
pub(crate) fn native_publish_manifest(
    fs: &impl NativeFs,
    paths: &InstallPaths,
    value: &Registration,
) -> InstallResult<()> {
    validate_registration_manifest(paths, value.manifest())?;
    // A publish may replace our own previous manifest (an upgrade), but it must
    // never silently steal a registration another product owns. Check the live
    // manifest before staging anything so a refusal mutates nothing.
    if let Some(live) = native_read_registration(fs, paths)?
        && !value.matches_published(&live)
        && !is_owned_registration_preimage(paths, &live)
    {
        return Err(foreign_state());
    }
    native_ensure_layout(fs, paths)?;
    ensure_registration_parent(paths)?;
    let live = registration_target(paths)?;
    // Stage next to the live destination: the publishing rename must never cross
    // a volume (the macOS Chrome registration lives outside the install root).
    stage_and_replace(fs, &live, value.manifest(), "installer-new")
}

/// Create the fixed registration directory when Chrome has not created it yet.
///
/// The registration lives outside the install root (`~/Library/Application
/// Support/Google/Chrome/NativeMessagingHosts` on macOS, the install root on
/// Windows), so it is not part of the private layout. Creating the chain is
/// bounded to that one fixed path; an existing final directory must be a real
/// directory, never a link, and no other path is created.
fn ensure_registration_parent(paths: &InstallPaths) -> InstallResult<()> {
    let parent = paths.registration_path().parent().ok_or_else(unsafe_path)?;
    match fs::symlink_metadata(parent) {
        Ok(metadata) => {
            if is_link_like(&metadata) || !metadata.is_dir() {
                return Err(unsafe_path());
            }
            Ok(())
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            fs::create_dir_all(parent).map_err(|_| io_failure())?;
            let metadata = fs::symlink_metadata(parent).map_err(|_| io_failure())?;
            if is_link_like(&metadata) || !metadata.is_dir() {
                return Err(unsafe_path());
            }
            Ok(())
        }
        Err(_) => Err(io_failure()),
    }
}

/// The manifest-ordering half of registration publication, shared by both
/// adapters.
///
/// The adapter calls this *before* it touches its OS registration. It re-reads
/// the live manifest, applies the same owned-preimage refusal as step one and
/// requires the live bytes to be exactly the manifest that was just published;
/// anything else is a refusal, and no registry value or file is written.
pub(crate) fn require_published_manifest(
    fs: &impl NativeFs,
    paths: &InstallPaths,
    value: &Registration,
) -> InstallResult<()> {
    validate_registration_manifest(paths, value.manifest())?;
    let Some(live) = native_read_registration(fs, paths)? else {
        return Err(io_failure());
    };
    if !value.matches_published(&live) {
        if !is_owned_registration_preimage(paths, &live) {
            return Err(foreign_state());
        }
        // Ours, but not the manifest that was just published: the two ordered
        // steps ran out of order (or another writer intervened).
        return Err(io_failure());
    }
    Ok(())
}

/// Remove the manifest file only when its exact bytes equal `expected`.
pub(crate) fn native_remove_manifest(
    fs: &impl NativeFs,
    paths: &InstallPaths,
    expected: &Registration,
) -> InstallResult<()> {
    let Some(current) = native_read_registration(fs, paths)? else {
        return Ok(());
    };
    if !expected.matches_published(&current) {
        return Err(foreign_state());
    }
    let live = ValidatedPath::registration(paths, PathKind::File)?;
    live.revalidate()?;
    fs.remove_entry(&live)?;
    fs.flush_parent(&live)
}

/// Enumerate exactly what a native carrier install will create.
pub(crate) fn native_carrier_entries(
    fs: &impl NativeFs,
    source: &PackageSource,
) -> InstallResult<Vec<super::CarrierEntry>> {
    carrier_entries_with(source, |path| fs.hash_file(path))
}

/// Install one carrier artifact through the adapter's metadata-preserving copy.
pub(crate) fn native_install_carrier(
    fs: &impl NativeFs,
    source: &PackageSource,
    destination: &ValidatedPath,
) -> InstallResult<()> {
    match source.kind() {
        PackageKind::File if destination.kind() != PathKind::NewFile => Err(invalid_input()),
        PackageKind::Directory if destination.kind() != PathKind::NewDirectory => {
            Err(invalid_input())
        }
        _ => {
            destination.revalidate()?;
            fs.install_carrier(source, destination)
        }
    }
}

/// Remove one validated owned entry, re-hashing its exact content first.
pub(crate) fn native_remove_owned(fs: &impl NativeFs, entry: &OwnedEntry) -> InstallResult<()> {
    entry.path.revalidate()?;
    match entry.claim.kind {
        OwnedKind::File => {
            let expected = entry.claim.sha256.ok_or_else(foreign_state)?;
            let short = match entry.incomplete_size {
                Some(expected_size) => {
                    let length = fs::symlink_metadata(entry.path.rendered())
                        .map_err(|_| io_failure())?
                        .len();
                    length < expected_size
                }
                None => false,
            };
            if !short && fs.hash_file(entry.path.rendered())? != expected {
                return Err(foreign_state());
            }
            fs.remove_entry(&entry.path)?;
        }
        // The link is bound to its exact target bytes; its target is never read
        // as content and never unlinked.
        OwnedKind::Symlink => {
            let expected = entry.claim.sha256.ok_or_else(foreign_state)?;
            if crate::platform::link_target_digest(entry.path.rendered())? != expected {
                return Err(foreign_state());
            }
            fs.remove_entry(&entry.path)?;
        }
        OwnedKind::Directory => fs.remove_directory(&entry.path)?,
    }
    fs.flush_parent(&entry.path)
}

/// Flush the parent directory of one validated path.
pub(crate) fn native_flush_parent(fs: &impl NativeFs, path: &ValidatedPath) -> InstallResult<()> {
    path.revalidate_ancestors()?;
    fs.flush_parent(path)
}

/// The portable reference implementation of [`NativeFs`].
///
/// This is deliberately public: it is the implementation the portable tests use
/// to exercise the shared native orchestration on any host. It is **not** a
/// production adapter: it performs no OS registration and no signature
/// assessment, and the production binaries never select it.
#[derive(Debug, Default)]
pub struct StdNativeFs;

impl NativeFs for StdNativeFs {
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock> {
        super::acquire_lock(paths)
    }

    fn validate_private_directory(&self, path: &Path) -> InstallResult<()> {
        let metadata = fs::symlink_metadata(path).map_err(|_| io_failure())?;
        if is_link_like(&metadata) || !metadata.is_dir() {
            return Err(unsafe_path());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::{MetadataExt, PermissionsExt};

            if metadata.uid() != super::effective_user_id() {
                return Err(unsafe_path());
            }
            if metadata.permissions().mode() & 0o077 != 0 {
                return Err(unsafe_path());
            }
        }
        Ok(())
    }

    fn write_durable(
        &self,
        path: &ValidatedPath,
        bytes: &[u8],
    ) -> InstallResult<super::FileIdentity> {
        write_durable_checked(path, bytes, None, false)?;
        // The reference implementation is path-based by construction (it is not a
        // production adapter): the identity is re-read from the name it wrote.
        let metadata = fs::symlink_metadata(path.rendered()).map_err(|_| io_failure())?;
        identity_of(path.rendered(), &metadata)
    }

    fn replace_file(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        replace_file_checked(staged, live)
    }

    fn read_file(&self, path: &Path) -> InstallResult<Vec<u8>> {
        read_no_follow(path).map_err(|_| io_failure())
    }

    fn hash_file(&self, path: &Path) -> InstallResult<Digest> {
        hash_file(path)
    }

    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()> {
        let parent = path.rendered().parent().ok_or_else(unsafe_path)?;
        super::flush_directory(parent)
    }

    fn create_directory(&self, path: &Path) -> InstallResult<()> {
        #[cfg(unix)]
        let mut builder = fs::DirBuilder::new();
        #[cfg(not(unix))]
        let builder = fs::DirBuilder::new();
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;

            builder.mode(0o700);
        }
        match builder.create(path) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
                return Err(unsafe_path());
            }
            Err(_) => return Err(io_failure()),
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;

            fs::set_permissions(path, fs::Permissions::from_mode(0o700))
                .map_err(|_| io_failure())?;
        }
        Ok(())
    }

    fn create_directory_at(&self, path: &ValidatedPath) -> InstallResult<()> {
        if path.kind() != PathKind::NewDirectory {
            return Err(invalid_input());
        }
        path.revalidate()?;
        NativeFs::create_directory(self, path.rendered())?;
        self.flush_parent(path)
    }

    fn remove_entry(&self, path: &ValidatedPath) -> InstallResult<()> {
        if !matches!(path.kind(), PathKind::File | PathKind::Symlink) {
            return Err(unsafe_path());
        }
        path.revalidate()?;
        fs::remove_file(path.rendered()).map_err(|_| io_failure())
    }

    fn remove_directory(&self, path: &ValidatedPath) -> InstallResult<()> {
        path.revalidate()?;
        match fs::remove_dir(path.rendered()) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) if error.kind() == io::ErrorKind::DirectoryNotEmpty => {
                Err(InstallFailure::new(FailureCode::CleanupPending))
            }
            Err(_) => Err(io_failure()),
        }
    }

    fn install_carrier(
        &self,
        source: &PackageSource,
        destination: &ValidatedPath,
    ) -> InstallResult<()> {
        super::install_carrier_bytes(source, destination)
    }
}

/// Compare the exact bytes read back through a handle that just wrote them.
///
/// The handle is rewound explicitly, so a shared file pointer left anywhere by a
/// previous call can never make an earlier write look complete.
pub(crate) fn read_back(file: &mut fs::File, expected: &[u8]) -> io::Result<()> {
    file.seek(SeekFrom::Start(0))?;
    let mut bytes = Vec::with_capacity(expected.len());
    file.read_to_end(&mut bytes)?;
    if bytes == expected {
        Ok(())
    } else {
        Err(io::Error::other("read-back mismatch"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Channel, Target};
    use crate::platform::{OwnedClaim, StateDocument, parse_manifest};
    use std::path::PathBuf;

    struct TempRoot(PathBuf);

    impl TempRoot {
        fn new(tag: &str) -> Self {
            let unique = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir().join(format!(
                "veles-native-{tag}-{}-{unique}",
                std::process::id()
            ));
            let _ = std::fs::remove_dir_all(&path);
            std::fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }

    impl Drop for TempRoot {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn layout(root: &Path) -> InstallPaths {
        InstallPaths::for_user(Target::MacosArm64, Channel::Fixture, root, None).expect("layout")
    }

    fn manifest(paths: &InstallPaths, executable: &str) -> Vec<u8> {
        crate::model::canonical_json(&crate::platform::NativeManifest {
            name: paths.registration_name().to_owned(),
            description: "Veles Native Messaging bridge".to_owned(),
            path: paths.root().join(executable).to_str().unwrap().to_owned(),
            kind: "stdio".to_owned(),
            allowed_origins: vec![
                crate::model::extension_origin("mnopabcdefghijklmnopabcdefghijkl").unwrap(),
            ],
        })
        .unwrap()
    }

    #[test]
    fn sibling_names_cannot_escape_the_parent() {
        let temp = TempRoot::new("sibling");
        let paths = layout(&temp.0);
        let fs = StdNativeFs;
        native_ensure_layout(&fs, &paths).unwrap();
        let handle = ValidatedPath::existing_directory(&paths, Path::new("state")).unwrap();
        assert!(handle.sibling("..", PathKind::NewFile).is_err());
        assert!(handle.sibling("a/b", PathKind::NewFile).is_err());
        assert!(handle.sibling("ok.tmp", PathKind::NewFile).is_ok());
    }

    /// A staging sibling is created fresh every time: an existing file at a
    /// guessing-friendly staging name is never adopted, truncated, deleted or
    /// renamed over the destination.
    #[test]
    fn staging_names_are_fresh_and_never_adopted() {
        let temp = TempRoot::new("staging");
        let paths = layout(&temp.0);
        let fs = StdNativeFs;
        native_ensure_layout(&fs, &paths).unwrap();
        let live = ValidatedPath::new_file(&paths, Path::new("state/receipt.json")).unwrap();
        let first = staging_sibling(&live, "tmp").unwrap();
        let second = staging_sibling(&live, "tmp").unwrap();
        assert_ne!(first.relative(), second.relative());
        // A planted file at the first staging name cannot make the next write
        // adopt it: the name is one-shot, and the write is an exclusive create.
        std::fs::write(first.rendered(), b"planted").unwrap();
        assert_eq!(
            native_write_durable(&fs, &first, b"ours").unwrap_err().code,
            FailureCode::UnsafePath
        );
        // The planted file is neither adopted nor removed: a failed staging write
        // only ever discards the exact object the write created.
        assert_eq!(std::fs::read(first.rendered()).unwrap(), b"planted");
        assert!(first.rendered().exists());
    }

    /// The shared native orchestration, exercised over `std` on every host.
    ///
    /// This is portable evidence for the ordering both native adapters delegate
    /// to; it is not a native registry/rename/ACL verdict.
    #[test]
    fn native_state_and_manifest_orchestration_is_atomic_and_owned() {
        let temp = TempRoot::new("orchestration");
        let paths = layout(&temp.0);
        let fs = StdNativeFs;

        // Lock creates the private layout, then state documents round-trip.
        let lock = native_lock(&fs, &paths).expect("lock");
        native_write_state(&fs, &paths, StateDocument::Journal, b"first").unwrap();
        assert_eq!(
            native_read_state(&fs, &paths, StateDocument::Journal).unwrap(),
            Some(b"first".to_vec())
        );
        native_write_state(&fs, &paths, StateDocument::Journal, b"second").unwrap();
        assert_eq!(
            native_read_state(&fs, &paths, StateDocument::Journal).unwrap(),
            Some(b"second".to_vec())
        );
        // No staging sibling survives a completed replacement.
        let staged: Vec<_> = std::fs::read_dir(paths.state())
            .unwrap()
            .map(|entry| entry.unwrap().file_name())
            .filter(|name| name.to_string_lossy().contains("installer-new"))
            .collect();
        assert!(staged.is_empty(), "staging siblings must be renamed away");
        drop(lock);

        // A manifest selecting an owned executable publishes, and the second
        // ordered step accepts exactly those bytes.
        native_ensure_layout(&fs, &paths).unwrap();
        std::fs::create_dir_all(paths.versions().join("1.0.0-test")).unwrap();
        let host = paths.versions().join("1.0.0-test").join("host.exe");
        std::fs::write(&host, b"host").unwrap();
        let bytes = manifest(&paths, "versions/1.0.0-test/host.exe");
        parse_manifest(&bytes).unwrap();
        let registration = Registration::new(bytes.clone()).unwrap();
        native_publish_manifest(&fs, &paths, &registration).unwrap();
        require_published_manifest(&fs, &paths, &registration).unwrap();
        assert_eq!(
            native_read_registration(&fs, &paths)
                .unwrap()
                .unwrap()
                .manifest(),
            bytes.as_slice()
        );
        // The manifest is staged next to its live destination, so the publishing
        // rename never leaves the destination directory (and its volume).
        assert!(
            !paths
                .state()
                .join("staging")
                .join("registration.json")
                .exists()
        );
        let leftover_in_state: Vec<_> = std::fs::read_dir(paths.state())
            .unwrap()
            .map(|entry| entry.unwrap().file_name())
            .filter(|name| name.to_string_lossy().contains("registration.json"))
            .collect();
        assert!(
            leftover_in_state.is_empty(),
            "the manifest must not be staged inside the install state directory"
        );

        // The manifest can be withdrawn exactly, and the state quarantine keeps
        // rejected bytes under the fixed name instead of unlinking them.
        native_remove_manifest(&fs, &paths, &registration).unwrap();
        assert!(native_read_registration(&fs, &paths).unwrap().is_none());
        native_publish_manifest(&fs, &paths, &registration).unwrap();
        native_quarantine_state(&fs, &paths, StateDocument::Journal).unwrap();
        assert_eq!(
            native_read_state(&fs, &paths, StateDocument::Journal).unwrap(),
            None
        );
        assert!(paths.state().join("journal.json.quarantine").exists());

        // Durable replacement over a hard link replaces only the directory entry.
        let first = ValidatedPath::existing_file(&paths, Path::new("versions/1.0.0-test/host.exe"))
            .unwrap();
        let linked = paths.versions().join("1.0.0-test").join("host-link.exe");
        std::fs::hard_link(first.rendered(), &linked).unwrap();
        native_replace_durable(&fs, &first, b"replaced-host").unwrap();
        assert_eq!(std::fs::read(first.rendered()).unwrap(), b"replaced-host");
        assert_eq!(std::fs::read(&linked).unwrap(), b"host");
        let mut replaced = std::fs::File::open(first.rendered()).unwrap();
        read_back(&mut replaced, b"replaced-host").unwrap();
        native_flush_parent(&fs, &first).unwrap();

        // A signed carrier file is enumerated with its exact digest and installed
        // through the adapter boundary.
        let carrier = temp.0.join("carrier.exe");
        std::fs::write(&carrier, b"carrier-image").unwrap();
        let package = PackageSource::open(&carrier).unwrap();
        let entries = native_carrier_entries(&fs, &package).unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].relative(), "");
        let destination =
            ValidatedPath::new_file(&paths, Path::new("versions/1.0.0-test/carrier.exe")).unwrap();
        native_install_carrier(&fs, &package, &destination).unwrap();
        assert_eq!(
            std::fs::read(paths.versions().join("1.0.0-test").join("carrier.exe")).unwrap(),
            b"carrier-image"
        );

        // A foreign live registration is refused before anything is written.
        std::fs::write(
            paths.registration_path(),
            r#"{"name":"me.nagaev.veles.bridge","description":"foreign","path":"C:\\other\\host.exe","type":"stdio","allowed_origins":["chrome-extension://mnopabcdefghijklmnopabcdefghijkl/"]}"#,
        )
        .unwrap();
        let failure = native_publish_manifest(&fs, &paths, &registration).unwrap_err();
        assert_eq!(failure.code, FailureCode::ForeignState);
        assert!(native_read_registration(&fs, &paths).unwrap().is_some());

        // Exact removal: a matching claim is removed, a foreign one is preserved.
        std::fs::write(
            paths.versions().join("1.0.0-test").join("data.bin"),
            b"owned",
        )
        .unwrap();
        let claim = OwnedClaim::file("versions/1.0.0-test/data.bin", Digest::of(b"owned"));
        let entry = OwnedEntry::from_claim(&paths, &claim).unwrap().unwrap();
        std::fs::write(
            paths.versions().join("1.0.0-test").join("data.bin"),
            b"changed",
        )
        .unwrap();
        assert_eq!(
            native_remove_owned(&fs, &entry).unwrap_err().code,
            FailureCode::ForeignState
        );
        std::fs::write(
            paths.versions().join("1.0.0-test").join("data.bin"),
            b"owned",
        )
        .unwrap();
        let entry = OwnedEntry::from_claim(&paths, &claim).unwrap().unwrap();
        native_remove_owned(&fs, &entry).unwrap();
        assert!(
            !paths
                .versions()
                .join("1.0.0-test")
                .join("data.bin")
                .exists()
        );

        // An owned symbolic link is removed as a link: its target keeps its bytes
        // and a changed target is preserved instead of unlinked.
        let target = paths.versions().join("1.0.0-test").join("target.bin");
        std::fs::write(&target, b"target").unwrap();
        #[cfg(unix)]
        {
            let link = paths.versions().join("1.0.0-test").join("link");
            std::os::unix::fs::symlink("../1.0.0-test/target.bin", &link).unwrap();
            let claim = OwnedClaim::symlink("versions/1.0.0-test/link", "../1.0.0-test/target.bin");
            let entry = OwnedEntry::from_claim(&paths, &claim).unwrap().unwrap();
            native_remove_owned(&fs, &entry).unwrap();
            assert!(!link.exists());
            assert_eq!(std::fs::read(&target).unwrap(), b"target");
        }

        // A non-empty directory is never recursively deleted.
        let directory = OwnedClaim::directory("versions/1.0.0-test");
        let entry = OwnedEntry::from_claim(&paths, &directory).unwrap().unwrap();
        assert_eq!(
            native_remove_owned(&fs, &entry).unwrap_err().code,
            FailureCode::CleanupPending
        );
    }
}
