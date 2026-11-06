//! Injected OS boundary for the per-user installer.
//!
//! This module owns the opaque handles that later tasks pass around:
//! [`ValidatedPath`] (validated ancestry plus captured file identity),
//! [`OwnedEntry`] (an ownership claim that survived content validation),
//! [`InstallLock`] (the exclusive per-user lock), [`PackageSource`],
//! [`TrustProof`] and [`Registration`]. Mutations only accept those handles,
//! never raw receipt-supplied strings.
//!
//! [`ValidatedPath`] captures the identity (device/inode on Unix, canonical path
//! on other targets) of the anchor and every ancestor. Each mutation revalidates
//! that ancestry immediately before acting, so a parent swapped for a symlink or
//! reparse point, or a directory replaced by a different one, fails closed. A
//! revalidation still cannot prove the absence of every race: the native
//! adapters (Tasks 4, 5 and 8) use handle-relative no-follow OS calls.
//!
//! [`PortablePlatform`] is the std-only implementation used by portable tests on
//! Linux. It performs real filesystem IO, locking and registration-file reads,
//! but it is **not** the production adapter: Windows HKCU publication, macOS
//! code sealing, host process inspection and OS trust assessment are stubs that
//! fail closed until Tasks 4, 5 and 8 implement them. [`FakePlatform`] wraps the
//! same primitives with fault injection and call recording.

use std::ffi::{OsStr, OsString};
use std::fs::{self, File, Metadata, OpenOptions, TryLockError};
use std::io::{self, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::{Mutex, MutexGuard};

use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

use crate::inventory::normalize_relative_path;
use crate::model::{
    Channel, Digest, FailureCode, InstallFailure, InstallResult, MAX_DOCUMENT_BYTES,
    MAX_INVENTORY_ENTRIES, MAX_PUBLISHER_BYTES, ReleaseDescriptor, Target, validate_extension_id,
};
use crate::paths::{
    InstallPaths, MAINTENANCE_DIR, MAX_ROOT_BYTES, STAGING_DIR, STATE_DIR, VERSIONS_DIR,
};
use crate::policy::CARRIER_HEADER_LEN;

/// The only ownership-record schema generation supported at this boundary.
pub const OWNERSHIP_SCHEMA_VERSION: u32 = 1;
/// A registration manifest is a bounded JSON document.
pub const MAX_MANIFEST_PATH_BYTES: usize = 32_767;

#[cfg(all(unix, any(target_os = "linux", target_os = "android")))]
const NO_FOLLOW: i32 = 0o400_000;
#[cfg(all(
    unix,
    any(target_os = "macos", target_os = "ios", target_os = "freebsd")
))]
const NO_FOLLOW: i32 = 0x0100;
#[cfg(all(
    unix,
    not(any(
        target_os = "linux",
        target_os = "android",
        target_os = "macos",
        target_os = "ios",
        target_os = "freebsd"
    ))
))]
const NO_FOLLOW: i32 = 0;

#[cfg(windows)]
const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0400;

/// Classification of a validated path at construction time.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PathKind {
    /// An existing regular file; its identity is captured.
    File,
    /// An existing directory; its identity is captured.
    Directory,
    /// A path that must not exist yet and will be created as a regular file.
    NewFile,
    /// A path that must not exist yet and will be created as a directory.
    NewDirectory,
}

/// Whether an owned version currently blocks installer mutation.
///
/// Task 5 implements the real process classification; this boundary only
/// defines the value so every later operation can block on non-quiescent state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HostState {
    /// No process executing an owned host version was observed.
    Quiescent,
    /// An owned host is running; the installer must not mutate.
    Active,
    /// Inspection was inaccessible or ambiguous; treated as active.
    Unknown,
}

#[cfg(unix)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct FileIdentity {
    device: u64,
    inode: u64,
}

#[cfg(unix)]
fn identity_of(_path: &Path, metadata: &Metadata) -> InstallResult<FileIdentity> {
    use std::os::unix::fs::MetadataExt;

    Ok(FileIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
    })
}

#[cfg(not(unix))]
#[derive(Debug, Clone, PartialEq, Eq)]
struct FileIdentity {
    canonical: PathBuf,
}

#[cfg(not(unix))]
fn identity_of(path: &Path, _metadata: &Metadata) -> InstallResult<FileIdentity> {
    fs::canonicalize(path)
        .map(|canonical| FileIdentity { canonical })
        .map_err(|_| io_failure())
}

#[derive(Debug, Clone)]
struct AncestorIdentity {
    path: PathBuf,
    identity: FileIdentity,
}

/// A path below a trusted anchor whose ancestry and file identity were captured.
///
/// The anchor is canonicalized once (the fixed install root or the fixed
/// registration directory obtained from OS known folders). Every component below
/// it is traversed without following links, and each mutation revalidates the
/// captured identities before touching the filesystem.
#[derive(Debug, Clone)]
pub struct ValidatedPath {
    anchor: PathBuf,
    relative: String,
    rendered: PathBuf,
    kind: PathKind,
    ancestors: Vec<AncestorIdentity>,
    target: Option<FileIdentity>,
}

impl ValidatedPath {
    /// Validate `relative` below the fixed install root.
    pub fn new(paths: &InstallPaths, relative: &Path, kind: PathKind) -> InstallResult<Self> {
        let relative = normalize_relative(relative)?;
        Self::build(paths.root().to_path_buf(), relative, kind)
    }

    /// Validate an absolute path that must resolve inside the fixed install root.
    ///
    /// Containment uses path components (ASCII case-insensitive on Windows),
    /// never a case-sensitive string prefix.
    pub fn within(paths: &InstallPaths, absolute: &Path, kind: PathKind) -> InstallResult<Self> {
        let relative = relative_to_root(paths.root(), absolute).ok_or_else(unsafe_path)?;
        Self::new(paths, &relative, kind)
    }

    /// Validate the channel's Native Messaging registration path.
    ///
    /// The registration lives below the fixed Chrome/host directory rather than
    /// below the install root, so the fixed registration directory becomes the
    /// anchor.
    pub fn registration(paths: &InstallPaths, kind: PathKind) -> InstallResult<Self> {
        let registration = paths.registration_path();
        let parent = registration.parent().ok_or_else(unsafe_path)?;
        let name = registration.file_name().ok_or_else(unsafe_path)?;
        let name = name.to_str().ok_or_else(unsafe_path)?;
        Self::build(parent.to_path_buf(), name.to_owned(), kind)
    }

    /// Convenience: a new regular file below the install root.
    pub fn new_file(paths: &InstallPaths, relative: &Path) -> InstallResult<Self> {
        Self::new(paths, relative, PathKind::NewFile)
    }

    /// Convenience: an existing regular file below the install root.
    pub fn existing_file(paths: &InstallPaths, relative: &Path) -> InstallResult<Self> {
        Self::new(paths, relative, PathKind::File)
    }

    /// Convenience: a new directory below the install root.
    pub fn new_directory(paths: &InstallPaths, relative: &Path) -> InstallResult<Self> {
        Self::new(paths, relative, PathKind::NewDirectory)
    }

    /// Convenience: an existing directory below the install root.
    pub fn existing_directory(paths: &InstallPaths, relative: &Path) -> InstallResult<Self> {
        Self::new(paths, relative, PathKind::Directory)
    }

    /// Re-adopt the same relative path as an existing file or directory.
    ///
    /// After writing a [`PathKind::NewFile`] handle the caller must adopt the
    /// created file so the rename/removal boundary captures its identity.
    pub fn adopt(&self, kind: PathKind) -> InstallResult<Self> {
        if !matches!(kind, PathKind::File | PathKind::Directory) {
            return Err(invalid_input());
        }
        Self::build(self.anchor.clone(), self.relative.clone(), kind)
    }

    /// The canonical anchor directory.
    pub fn anchor(&self) -> &Path {
        &self.anchor
    }

    /// The normalized `/`-joined path below the anchor.
    pub fn relative(&self) -> &str {
        &self.relative
    }

    /// The absolute rendered path for OS calls and diagnostics.
    pub fn rendered(&self) -> &Path {
        &self.rendered
    }

    /// The construction-time kind.
    pub fn kind(&self) -> PathKind {
        self.kind
    }

    /// Revalidate the full ancestry and the leaf.
    pub fn revalidate(&self) -> InstallResult<()> {
        self.revalidate_ancestors()?;
        match self.kind {
            PathKind::File | PathKind::Directory => {
                let metadata = fs::symlink_metadata(&self.rendered).map_err(|_| unsafe_path())?;
                if is_link_like(&metadata) {
                    return Err(unsafe_path());
                }
                let type_matches = match self.kind {
                    PathKind::File => metadata.is_file(),
                    PathKind::Directory => metadata.is_dir(),
                    _ => false,
                };
                if !type_matches {
                    return Err(unsafe_path());
                }
                if Some(identity_of(&self.rendered, &metadata)?) != self.target {
                    return Err(unsafe_path());
                }
            }
            PathKind::NewFile | PathKind::NewDirectory => {
                match fs::symlink_metadata(&self.rendered) {
                    Ok(_) => return Err(unsafe_path()),
                    Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                    Err(_) => return Err(io_failure()),
                }
            }
        }
        Ok(())
    }

    fn revalidate_ancestors(&self) -> InstallResult<()> {
        for ancestor in &self.ancestors {
            let metadata = fs::symlink_metadata(&ancestor.path).map_err(|_| unsafe_path())?;
            if is_link_like(&metadata) || !metadata.is_dir() {
                return Err(unsafe_path());
            }
            if identity_of(&ancestor.path, &metadata)? != ancestor.identity {
                return Err(unsafe_path());
            }
        }
        Ok(())
    }

    fn build(anchor: PathBuf, relative: String, kind: PathKind) -> InstallResult<Self> {
        let metadata = fs::symlink_metadata(&anchor).map_err(|_| io_failure())?;
        if is_link_like(&metadata) || !metadata.is_dir() {
            return Err(unsafe_path());
        }
        let anchor = fs::canonicalize(&anchor).map_err(|_| io_failure())?;
        let metadata = fs::symlink_metadata(&anchor).map_err(|_| io_failure())?;
        if is_link_like(&metadata) || !metadata.is_dir() {
            return Err(unsafe_path());
        }

        let mut ancestors = vec![AncestorIdentity {
            path: anchor.clone(),
            identity: identity_of(&anchor, &metadata)?,
        }];
        let parts: Vec<&str> = relative.split('/').collect();
        let mut rendered = anchor.clone();
        let mut target = None;
        for (index, part) in parts.iter().enumerate() {
            rendered.push(part);
            let last = index + 1 == parts.len();
            if !last {
                let metadata = fs::symlink_metadata(&rendered).map_err(|error| {
                    if error.kind() == io::ErrorKind::NotFound {
                        unsafe_path()
                    } else {
                        io_failure()
                    }
                })?;
                if is_link_like(&metadata) || !metadata.is_dir() {
                    return Err(unsafe_path());
                }
                ancestors.push(AncestorIdentity {
                    path: rendered.clone(),
                    identity: identity_of(&rendered, &metadata)?,
                });
                continue;
            }
            match fs::symlink_metadata(&rendered) {
                Ok(metadata) => {
                    if is_link_like(&metadata) {
                        return Err(unsafe_path());
                    }
                    let type_matches = match kind {
                        PathKind::File => metadata.is_file(),
                        PathKind::Directory => metadata.is_dir(),
                        PathKind::NewFile | PathKind::NewDirectory => false,
                    };
                    if !type_matches {
                        return Err(unsafe_path());
                    }
                    target = Some(identity_of(&rendered, &metadata)?);
                }
                Err(error) if error.kind() == io::ErrorKind::NotFound => {
                    if matches!(kind, PathKind::File | PathKind::Directory) {
                        return Err(io_failure());
                    }
                }
                Err(_) => return Err(io_failure()),
            }
        }

        Ok(Self {
            anchor,
            relative,
            rendered,
            kind,
            ancestors,
            target,
        })
    }
}

/// Whether `candidate` is strictly below `root` using OS path semantics.
///
/// Both paths must be absolute and free of `.`/`..`. Windows components compare
/// ASCII case-insensitively (a deliberate fail-closed approximation of
/// `CompareStringOrdinal`); Unix compares exactly.
pub fn path_is_within(root: &Path, candidate: &Path) -> bool {
    match (
        comparable_components(root),
        comparable_components(candidate),
    ) {
        (Some(root), Some(candidate)) => {
            candidate.len() > root.len()
                && root
                    .iter()
                    .zip(candidate.iter())
                    .all(|(root, candidate)| os_eq(root, candidate))
        }
        _ => false,
    }
}

fn relative_to_root(root: &Path, candidate: &Path) -> Option<PathBuf> {
    let root_components = comparable_components(root)?;
    let candidate_components = comparable_components(candidate)?;
    if candidate_components.len() <= root_components.len() {
        return None;
    }
    if !root_components
        .iter()
        .zip(candidate_components.iter())
        .all(|(root, candidate)| os_eq(root, candidate))
    {
        return None;
    }
    let mut relative = PathBuf::new();
    for component in &candidate_components[root_components.len()..] {
        relative.push(component);
    }
    Some(relative)
}

fn comparable_components(path: &Path) -> Option<Vec<OsString>> {
    if !path.is_absolute() {
        return None;
    }
    let mut parts = Vec::new();
    for component in path.components() {
        match component {
            Component::Prefix(prefix) => parts.push(prefix.as_os_str().to_os_string()),
            Component::RootDir => parts.push(OsString::from("/")),
            Component::Normal(part) => parts.push(part.to_os_string()),
            Component::CurDir | Component::ParentDir => return None,
        }
    }
    Some(parts)
}

#[cfg(windows)]
fn os_eq(left: &OsStr, right: &OsStr) -> bool {
    let left = left.to_string_lossy();
    let right = right.to_string_lossy();
    left.as_ref().eq_ignore_ascii_case(right.as_ref())
}

#[cfg(not(windows))]
fn os_eq(left: &OsStr, right: &OsStr) -> bool {
    left == right
}

fn same_path(left: &Path, right: &Path) -> bool {
    match (comparable_components(left), comparable_components(right)) {
        (Some(left), Some(right)) => {
            left.len() == right.len()
                && left
                    .iter()
                    .zip(right.iter())
                    .all(|(left, right)| os_eq(left, right))
        }
        _ => false,
    }
}

fn normalize_relative(relative: &Path) -> InstallResult<String> {
    let mut parts = Vec::new();
    for component in relative.components() {
        match component {
            Component::Normal(part) => {
                let text = part.to_str().ok_or_else(unsafe_path)?;
                parts.push(text.to_owned());
            }
            _ => return Err(unsafe_path()),
        }
    }
    if parts.is_empty() {
        return Err(unsafe_path());
    }
    normalize_relative_path(&parts.join("/")).map_err(|_| unsafe_path())
}

fn is_link_like(metadata: &Metadata) -> bool {
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;

        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return true;
        }
    }
    false
}

/// The exclusive per-user installer lock. Dropping it releases the OS lock.
#[derive(Debug)]
pub struct InstallLock {
    path: PathBuf,
    file: File,
}

impl InstallLock {
    /// The fixed lock path this guard holds.
    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for InstallLock {
    fn drop(&mut self) {
        let _ = self.file.unlock();
    }
}

fn acquire_lock(paths: &InstallPaths) -> InstallResult<InstallLock> {
    ensure_layout(paths)?;
    match fs::symlink_metadata(paths.lock_path()) {
        Ok(metadata) if is_link_like(&metadata) || !metadata.is_file() => {
            return Err(unsafe_path());
        }
        Ok(_) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(_) => return Err(io_failure()),
    }
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;

        options.mode(0o600).custom_flags(NO_FOLLOW);
    }
    let file = options.open(paths.lock_path()).map_err(|error| {
        if error.kind() == io::ErrorKind::AlreadyExists {
            unsafe_path()
        } else {
            io_failure()
        }
    })?;
    match file.try_lock() {
        Ok(()) => Ok(InstallLock {
            path: paths.lock_path().to_path_buf(),
            file,
        }),
        Err(TryLockError::WouldBlock) => Err(InstallFailure::new(FailureCode::Busy)),
        Err(TryLockError::Error(_)) => Err(io_failure()),
    }
}

fn ensure_layout(paths: &InstallPaths) -> InstallResult<()> {
    // Trusted OS-known-folder ancestors (for example macOS `/var` ->
    // `/private/var`) may legitimately be symlinks. Only the installer-owned root
    // and the directories below it are traversed strictly.
    if fs::create_dir_all(paths.root()).is_err() {
        return match fs::symlink_metadata(paths.root()) {
            Ok(metadata) if is_link_like(&metadata) || !metadata.is_dir() => Err(unsafe_path()),
            _ => Err(io_failure()),
        };
    }
    // The `Veles` container holds every channel root and is installer-owned too.
    if let Some(parent) = paths.root().parent() {
        ensure_private_directory(parent)?;
    }
    for path in [
        paths.root(),
        paths.versions(),
        paths.maintenance(),
        paths.state(),
        paths.staging(),
    ] {
        ensure_private_directory(path)?;
    }
    Ok(())
}

fn ensure_private_directory(path: &Path) -> InstallResult<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if is_link_like(&metadata) || !metadata.is_dir() {
                return Err(unsafe_path());
            }
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
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
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
                Err(_) => return Err(io_failure()),
            }
        }
        Err(_) => return Err(io_failure()),
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(|_| io_failure())?;
    }
    Ok(())
}

fn flush_directory(path: &Path) -> InstallResult<()> {
    #[cfg(unix)]
    {
        let file = File::open(path).map_err(|_| io_failure())?;
        file.sync_all().map_err(|_| io_failure())
    }
    #[cfg(not(unix))]
    {
        // Windows needs a directory handle opened with FILE_FLAG_BACKUP_SEMANTICS;
        // Task 8 implements that in the native adapter.
        let _ = path;
        Ok(())
    }
}

/// Exact manifest bytes and, on Windows, the HKCU value they replaced.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Registration {
    manifest: Vec<u8>,
    windows_preimage: Option<WindowsPreimage>,
}

impl Registration {
    /// Wrap exact manifest bytes. Structural validation happens at publish time.
    pub fn new(manifest: Vec<u8>) -> InstallResult<Self> {
        if manifest.is_empty() || manifest.len() > MAX_DOCUMENT_BYTES {
            return Err(invalid_package());
        }
        Ok(Self {
            manifest,
            windows_preimage: None,
        })
    }

    /// Attach the exact Windows HKCU preimage captured before publication.
    pub fn with_windows_preimage(mut self, preimage: WindowsPreimage) -> Self {
        self.windows_preimage = Some(preimage);
        self
    }

    /// Exact bytes of the Native Messaging manifest.
    pub fn manifest(&self) -> &[u8] {
        &self.manifest
    }

    /// SHA-256 of the exact manifest bytes.
    pub fn manifest_digest(&self) -> Digest {
        Digest::of(&self.manifest)
    }

    /// The optional Windows HKCU preimage (restore state, never identity).
    pub fn windows_preimage(&self) -> Option<&WindowsPreimage> {
        self.windows_preimage.as_ref()
    }

    /// Whether `live` publishes exactly the manifest bytes this value expects.
    ///
    /// The Windows preimage is deliberately excluded: it records how to restore
    /// a previous value, not which registration is owned.
    pub fn matches_published(&self, live: &Registration) -> bool {
        self.manifest == live.manifest
    }
}

/// The exact HKCU value a Windows publication replaced.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WindowsPreimage {
    key: String,
    previous_value: Option<String>,
}

impl WindowsPreimage {
    /// Record one HKCU key and its previous default value (or absence).
    pub fn new(key: impl Into<String>, previous_value: Option<String>) -> InstallResult<Self> {
        let key = key.into();
        if key.is_empty()
            || key.len() > MAX_ROOT_BYTES
            || key.chars().any(char::is_control)
            || previous_value
                .as_deref()
                .is_some_and(|value| value.len() > MAX_MANIFEST_PATH_BYTES || value.contains('\0'))
        {
            return Err(invalid_input());
        }
        Ok(Self {
            key,
            previous_value,
        })
    }

    /// The exact registry key below HKCU.
    pub fn key(&self) -> &str {
        &self.key
    }

    /// The previous default value, or `None` when the key/value was absent.
    pub fn previous_value(&self) -> Option<&str> {
        self.previous_value.as_deref()
    }
}

/// Strict Native Messaging manifest used for registration ownership checks.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeManifest {
    pub name: String,
    pub description: String,
    pub path: String,
    #[serde(rename = "type")]
    pub kind: String,
    pub allowed_origins: Vec<String>,
}

impl NativeManifest {
    /// The absolute executable path this manifest selects.
    pub fn executable(&self) -> InstallResult<PathBuf> {
        let path = Path::new(&self.path);
        if !path.is_absolute()
            || self.path.len() > MAX_MANIFEST_PATH_BYTES
            || self.path.chars().any(char::is_control)
        {
            return Err(unsafe_path());
        }
        for component in path.components() {
            match component {
                Component::Prefix(_) | Component::RootDir => {}
                Component::Normal(part) => {
                    // No `..`, alternate data streams or drive-relative forms.
                    let text = part.to_string_lossy();
                    if text.is_empty() || text.contains(':') || text.contains('\\') {
                        return Err(unsafe_path());
                    }
                }
                Component::CurDir | Component::ParentDir => return Err(unsafe_path()),
            }
        }
        Ok(path.to_path_buf())
    }
}

/// Parse one strict Native Messaging manifest.
pub fn parse_manifest(bytes: &[u8]) -> InstallResult<NativeManifest> {
    if bytes.is_empty() || bytes.len() > MAX_DOCUMENT_BYTES {
        return Err(invalid_package());
    }
    let manifest: NativeManifest = serde_json::from_slice(bytes).map_err(|_| invalid_package())?;
    if manifest.name.is_empty()
        || manifest.name.len() > MAX_PUBLISHER_BYTES
        || manifest.name.chars().any(char::is_control)
        || manifest.description.is_empty()
        || manifest.description.len() > MAX_PUBLISHER_BYTES
        || manifest.description.chars().any(char::is_control)
        || manifest.kind != "stdio"
    {
        return Err(invalid_package());
    }
    if manifest.allowed_origins.len() != 1 || !is_valid_origin(&manifest.allowed_origins[0]) {
        return Err(origin_rejected());
    }
    manifest.executable()?;
    Ok(manifest)
}

fn is_valid_origin(origin: &str) -> bool {
    origin
        .strip_prefix("chrome-extension://")
        .and_then(|rest| rest.strip_suffix('/'))
        .is_some_and(|extension_id| validate_extension_id(extension_id).is_ok())
}

/// One path the install owned when its receipt was committed.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OwnedClaim {
    /// Normalized relative path below the install root.
    pub path: String,
    pub kind: OwnedKind,
    /// Exact content digest for files; `None` for directories.
    pub sha256: Option<Digest>,
}

impl OwnedClaim {
    /// A file claim bound to exact content.
    pub fn file(path: impl Into<String>, sha256: Digest) -> Self {
        Self {
            path: path.into(),
            kind: OwnedKind::File,
            sha256: Some(sha256),
        }
    }

    /// A directory claim (removal succeeds only when the directory is empty).
    pub fn directory(path: impl Into<String>) -> Self {
        Self {
            path: path.into(),
            kind: OwnedKind::Directory,
            sha256: None,
        }
    }
}

/// Whether an owned entry is a file or a directory.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum OwnedKind {
    File,
    Directory,
}

/// The ownership section of a committed receipt.
///
/// A receipt is untrusted input. It never authorizes a raw path: every claim is
/// re-normalized, scoped to `versions/` or `maintenance/`, and content-checked
/// before it becomes an [`OwnedEntry`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OwnershipRecord {
    pub schema: u32,
    pub target: Target,
    pub channel: Channel,
    /// Exact absolute install root this record was committed for.
    pub root: String,
    /// SHA-256 of the exact committed registration manifest.
    pub manifest_sha256: Digest,
    /// Paths this installer owns, relative to the root.
    pub owned: Vec<OwnedClaim>,
}

impl OwnershipRecord {
    /// Validate that this record claims *this* installation and only in-root,
    /// in-scope paths. Content and live registration are checked separately.
    pub fn validate(&self, paths: &InstallPaths) -> InstallResult<()> {
        if self.schema != OWNERSHIP_SCHEMA_VERSION {
            return Err(invalid_input());
        }
        if self.target != paths.target() || self.channel != paths.channel() {
            return Err(foreign_state());
        }
        if !same_path(paths.root(), Path::new(&self.root)) {
            return Err(foreign_state());
        }
        if self.owned.len() > MAX_INVENTORY_ENTRIES {
            return Err(invalid_input());
        }
        let mut seen = std::collections::HashSet::with_capacity(self.owned.len());
        for claim in &self.owned {
            let normalized = normalize_relative_path(&claim.path).map_err(|_| unsafe_path())?;
            if !seen.insert(normalized.to_lowercase()) {
                return Err(unsafe_path());
            }
            if !is_owned_claim_scope(&normalized) {
                return Err(unsafe_path());
            }
            match claim.kind {
                OwnedKind::File if claim.sha256.is_none() => return Err(invalid_input()),
                OwnedKind::Directory if claim.sha256.is_some() => return Err(invalid_input()),
                _ => {}
            }
        }
        Ok(())
    }
}

/// A validated owned path with its exact kind and content claim.
#[derive(Debug, Clone)]
pub struct OwnedEntry {
    path: ValidatedPath,
    claim: OwnedClaim,
}

impl OwnedEntry {
    /// Turn an untrusted claim into a handle.
    ///
    /// Returns `None` when the claimed path is already absent (missing owned
    /// files are idempotent). A present path whose kind, content or ancestry does
    /// not match the claim is `ForeignState`/`UnsafePath` and is never deleted.
    pub fn from_claim(paths: &InstallPaths, claim: &OwnedClaim) -> InstallResult<Option<Self>> {
        let normalized = normalize_relative_path(&claim.path).map_err(|_| unsafe_path())?;
        if !is_owned_claim_scope(&normalized) {
            return Err(unsafe_path());
        }
        let absolute = paths.root().join(Path::new(&normalized));
        match fs::symlink_metadata(&absolute) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(_) => return Err(io_failure()),
            Ok(_) => {}
        }
        let kind = match claim.kind {
            OwnedKind::File => PathKind::File,
            OwnedKind::Directory => PathKind::Directory,
        };
        let path = ValidatedPath::new(paths, Path::new(&normalized), kind)?;
        if claim.kind == OwnedKind::File {
            let expected = claim.sha256.ok_or_else(foreign_state)?;
            if hash_file(path.rendered())? != expected {
                return Err(foreign_state());
            }
        }
        Ok(Some(Self {
            path,
            claim: claim.clone(),
        }))
    }

    /// The validated path handle.
    pub fn path(&self) -> &ValidatedPath {
        &self.path
    }

    /// The original claim.
    pub fn claim(&self) -> &OwnedClaim {
        &self.claim
    }
}

/// Validate every owned claim and return handles for the ones still present.
pub fn claim_owned_entries(
    paths: &InstallPaths,
    record: &OwnershipRecord,
) -> InstallResult<Vec<OwnedEntry>> {
    record.validate(paths)?;
    let mut entries = Vec::with_capacity(record.owned.len());
    for claim in &record.owned {
        if let Some(entry) = OwnedEntry::from_claim(paths, claim)? {
            entries.push(entry);
        }
    }
    Ok(entries)
}

/// Claim the live registration for this receipt, or fail with `ForeignState`.
///
/// The receipt cannot make a registration ours by naming it. The live manifest
/// must (1) hash to the receipt's exact manifest digest, (2) carry this channel's
/// host name, (3) select an executable inside an owned `versions/` or
/// `maintenance/` directory, and (4) be listed as a file claim in the receipt.
/// The function is read-only.
pub fn claim_owned_registration(
    platform: &impl Platform,
    paths: &InstallPaths,
    record: &OwnershipRecord,
) -> InstallResult<Registration> {
    record.validate(paths)?;
    let live = platform
        .read_registration(paths)?
        .ok_or_else(foreign_state)?;
    if live.manifest_digest() != record.manifest_sha256 {
        return Err(foreign_state());
    }
    let manifest = parse_manifest(live.manifest()).map_err(|_| foreign_state())?;
    if manifest.name != paths.registration_name() {
        return Err(foreign_state());
    }
    let executable = manifest.executable().map_err(|_| foreign_state())?;
    let normalized = executable_in_owned_scope(paths, &executable).map_err(|_| foreign_state())?;
    let claimed = record.owned.iter().any(|claim| {
        claim.kind == OwnedKind::File
            && normalize_relative_path(&claim.path)
                .map(|claim| claim == normalized)
                .unwrap_or(false)
    });
    if !claimed {
        return Err(foreign_state());
    }
    Ok(live)
}

fn is_owned_claim_scope(normalized: &str) -> bool {
    matches!(
        normalized.split('/').next(),
        Some(VERSIONS_DIR) | Some(MAINTENANCE_DIR)
    )
}

fn is_owned_executable_scope(normalized: &str) -> bool {
    let mut parts = normalized.split('/');
    match parts.next() {
        Some(VERSIONS_DIR) => parts.next().is_some() && parts.next().is_some(),
        Some(MAINTENANCE_DIR) => parts.next().is_some(),
        _ => false,
    }
}

/// A user-selected package input for trust verification.
#[derive(Debug, Clone)]
pub struct PackageSource {
    path: PathBuf,
    kind: PackageKind,
}

/// Whether a package source is a single file or a directory/app bundle.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PackageKind {
    File,
    Directory,
}

impl PackageSource {
    /// Open an absolute, existing file or directory (never a symlink leaf).
    pub fn open(path: &Path) -> InstallResult<Self> {
        if !path.is_absolute() || path.to_str().is_none() {
            return Err(invalid_input());
        }
        let metadata = fs::symlink_metadata(path).map_err(|_| io_failure())?;
        if is_link_like(&metadata) {
            return Err(unsafe_path());
        }
        let kind = if metadata.is_file() {
            PackageKind::File
        } else if metadata.is_dir() {
            PackageKind::Directory
        } else {
            return Err(unsafe_path());
        };
        let path = fs::canonicalize(path).map_err(|_| io_failure())?;
        Ok(Self { path, kind })
    }

    /// The canonical package path.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// The package kind.
    pub fn kind(&self) -> PackageKind {
        self.kind
    }
}

/// OS-verified signer/assessment evidence and the authenticated carrier bytes.
///
/// This deliberately contains no descriptor: an unchecked descriptor is never a
/// trust proof. Task 4 constructs the authenticated package from this evidence.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrustProof {
    signer: String,
    assessment: String,
    carrier: Vec<u8>,
}

impl TrustProof {
    /// Construct proof from the OS signer identity, assessment verdict and the
    /// exact authenticated carrier bytes.
    pub fn new(
        signer: impl Into<String>,
        assessment: impl Into<String>,
        carrier: Vec<u8>,
    ) -> InstallResult<Self> {
        let signer = signer.into();
        let assessment = assessment.into();
        if signer.is_empty()
            || signer.len() > MAX_PUBLISHER_BYTES
            || signer.chars().any(char::is_control)
            || assessment.is_empty()
            || assessment.len() > MAX_PUBLISHER_BYTES
            || assessment.chars().any(char::is_control)
            || carrier.is_empty()
            || carrier.len() > CARRIER_HEADER_LEN + MAX_DOCUMENT_BYTES
        {
            return Err(invalid_input());
        }
        Ok(Self {
            signer,
            assessment,
            carrier,
        })
    }

    /// The OS-verified signer/publisher identity.
    pub fn signer(&self) -> &str {
        &self.signer
    }

    /// The OS assessment verdict.
    pub fn assessment(&self) -> &str {
        &self.assessment
    }

    /// The exact authenticated policy carrier bytes.
    pub fn carrier(&self) -> &[u8] {
        &self.carrier
    }
}

/// The injected OS boundary. Tasks 4, 5 and 8 implement the native adapters.
pub trait Platform {
    /// Acquire the exclusive per-user installer lock (nonblocking).
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock>;
    /// Write all bytes, flush and read the exact bytes back.
    fn write_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()>;
    /// Atomically replace the one manifest that selects the executable.
    fn replace_manifest(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()>;
    /// Read the live registration exactly, or `None` when absent.
    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>>;
    /// Publish exact manifest bytes (and, natively on Windows, the HKCU value).
    ///
    /// The manifest must name this channel and an executable inside an owned
    /// `versions/` or `maintenance/` directory. The caller must establish
    /// ownership of any pre-existing registration (see
    /// [`claim_owned_registration`]) before asking to replace it.
    fn publish_registration(&self, paths: &InstallPaths, value: &Registration)
    -> InstallResult<()>;
    /// Remove only the registration whose exact bytes match `expected`.
    fn remove_registration(
        &self,
        paths: &InstallPaths,
        expected: &Registration,
    ) -> InstallResult<()>;
    /// Classify whether an owned host is running.
    fn inspect_hosts(&self, paths: &InstallPaths) -> InstallResult<HostState>;
    /// Verify the OS signer/assessment and authenticated carrier coverage.
    fn verify_trust(
        &self,
        source: &PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof>;
    /// Remove one validated owned entry.
    fn remove_owned(&self, entry: &OwnedEntry) -> InstallResult<()>;
    /// Flush the parent directory of a validated path.
    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()>;
}

/// Std-only platform used by portable tests.
///
/// It performs real filesystem writes, renames, locking and registration-file
/// IO. Trust assessment and host inspection fail closed; Windows HKCU
/// publication and macOS sealing are not implemented here.
#[derive(Debug, Default)]
pub struct PortablePlatform;

impl PortablePlatform {
    pub fn new() -> Self {
        Self
    }
}

impl Platform for PortablePlatform {
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock> {
        acquire_lock(paths)
    }

    fn write_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        write_durable_checked(path, bytes, None, false).map(|_| ())
    }

    fn replace_manifest(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        replace_manifest_checked(staged, live)
    }

    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>> {
        read_registration_from(paths.registration_path())
    }

    fn publish_registration(
        &self,
        paths: &InstallPaths,
        value: &Registration,
    ) -> InstallResult<()> {
        publish_registration_checked(paths, value)
    }

    fn remove_registration(
        &self,
        paths: &InstallPaths,
        expected: &Registration,
    ) -> InstallResult<()> {
        remove_registration_checked(paths, expected)
    }

    fn inspect_hosts(&self, _paths: &InstallPaths) -> InstallResult<HostState> {
        // Task 5 supplies real process classification. Unknown blocks mutation.
        Ok(HostState::Unknown)
    }

    fn verify_trust(
        &self,
        _source: &PackageSource,
        _expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        // Task 4 supplies WinVerifyTrust/Security.framework evidence.
        Err(InstallFailure::new(FailureCode::TrustUnavailable))
    }

    fn remove_owned(&self, entry: &OwnedEntry) -> InstallResult<()> {
        remove_owned_entry(entry)
    }

    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()> {
        flush_parent_checked(path)
    }
}

/// Fault injection knobs for [`FakePlatform`].
#[derive(Debug, Clone)]
pub struct Faults {
    /// Cap every write at this many bytes so the writer must loop.
    pub short_write_limit: Option<usize>,
    /// Pretend the read-back did not match the written bytes.
    pub corrupt_readback: bool,
    /// Fail the N-th state mutation (1-based) before it does anything.
    pub fail_mutation_at: Option<usize>,
    /// Make the next lock acquisition return `Busy`.
    pub lock_busy: bool,
    /// Host state returned by `inspect_hosts` (`Unknown` by default).
    pub host_state: HostState,
    /// Trust proof returned by `verify_trust` (`TrustUnavailable` by default).
    pub trust: Option<TrustProof>,
}

impl Default for Faults {
    fn default() -> Self {
        Self {
            short_write_limit: None,
            corrupt_readback: false,
            fail_mutation_at: None,
            lock_busy: false,
            host_state: HostState::Unknown,
            trust: None,
        }
    }
}

/// One call observed by [`FakePlatform`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PlatformCall {
    Lock,
    WriteDurable,
    ReplaceManifest,
    ReadRegistration,
    PublishRegistration,
    RemoveRegistration,
    InspectHosts,
    VerifyTrust,
    RemoveOwned,
    FlushParent,
}

impl PlatformCall {
    /// Whether the call can change installer-owned state.
    ///
    /// `Lock` only creates the private layout and lock file; `FlushParent` is
    /// durability, not state. Both are excluded so ownership tests can assert
    /// "zero mutations" while still holding the lock.
    pub fn is_mutation(self) -> bool {
        matches!(
            self,
            Self::WriteDurable
                | Self::ReplaceManifest
                | Self::PublishRegistration
                | Self::RemoveRegistration
                | Self::RemoveOwned
        )
    }
}

#[derive(Debug, Default)]
struct FakeState {
    calls: Vec<PlatformCall>,
    mutations: usize,
    short_writes: usize,
}

/// A fault-injectable [`Platform`] over the portable primitives.
#[derive(Debug)]
pub struct FakePlatform {
    faults: Faults,
    state: Mutex<FakeState>,
}

impl FakePlatform {
    pub fn new() -> Self {
        Self::with_faults(Faults::default())
    }

    pub fn with_faults(faults: Faults) -> Self {
        Self {
            faults,
            state: Mutex::new(FakeState::default()),
        }
    }

    /// All observed calls, in order.
    pub fn calls(&self) -> Vec<PlatformCall> {
        self.fake_state().calls.clone()
    }

    /// Observed state-changing calls, in order.
    pub fn mutations(&self) -> Vec<PlatformCall> {
        self.fake_state()
            .calls
            .iter()
            .copied()
            .filter(|call| call.is_mutation())
            .collect()
    }

    /// Number of attempted state mutations (including injected failures).
    pub fn mutation_count(&self) -> usize {
        self.fake_state().mutations
    }

    /// Number of short writes the injected writer produced.
    pub fn short_write_count(&self) -> usize {
        self.fake_state().short_writes
    }

    fn fake_state(&self) -> MutexGuard<'_, FakeState> {
        self.state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn observe(&self, call: PlatformCall) {
        self.fake_state().calls.push(call);
    }

    fn begin_mutation(&self, call: PlatformCall) -> InstallResult<()> {
        let mut state = self.fake_state();
        state.calls.push(call);
        state.mutations += 1;
        if self.faults.fail_mutation_at == Some(state.mutations) {
            return Err(io_failure());
        }
        Ok(())
    }
}

impl Default for FakePlatform {
    fn default() -> Self {
        Self::new()
    }
}

impl Platform for FakePlatform {
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock> {
        self.observe(PlatformCall::Lock);
        if self.faults.lock_busy {
            return Err(InstallFailure::new(FailureCode::Busy));
        }
        acquire_lock(paths)
    }

    fn write_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::WriteDurable)?;
        let short = write_durable_checked(
            path,
            bytes,
            self.faults.short_write_limit,
            self.faults.corrupt_readback,
        )?;
        self.fake_state().short_writes += short;
        Ok(())
    }

    fn replace_manifest(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::ReplaceManifest)?;
        replace_manifest_checked(staged, live)
    }

    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>> {
        self.observe(PlatformCall::ReadRegistration);
        read_registration_from(paths.registration_path())
    }

    fn publish_registration(
        &self,
        paths: &InstallPaths,
        value: &Registration,
    ) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::PublishRegistration)?;
        publish_registration_checked(paths, value)
    }

    fn remove_registration(
        &self,
        paths: &InstallPaths,
        expected: &Registration,
    ) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::RemoveRegistration)?;
        remove_registration_checked(paths, expected)
    }

    fn inspect_hosts(&self, _paths: &InstallPaths) -> InstallResult<HostState> {
        self.observe(PlatformCall::InspectHosts);
        Ok(self.faults.host_state)
    }

    fn verify_trust(
        &self,
        _source: &PackageSource,
        _expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        self.observe(PlatformCall::VerifyTrust);
        self.faults
            .trust
            .clone()
            .ok_or_else(|| InstallFailure::new(FailureCode::TrustUnavailable))
    }

    fn remove_owned(&self, entry: &OwnedEntry) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::RemoveOwned)?;
        remove_owned_entry(entry)
    }

    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()> {
        self.observe(PlatformCall::FlushParent);
        flush_parent_checked(path)
    }
}

fn write_durable_checked(
    path: &ValidatedPath,
    bytes: &[u8],
    short_write_limit: Option<usize>,
    corrupt_readback: bool,
) -> InstallResult<usize> {
    path.revalidate()?;
    let file = open_for_write(path).map_err(|error| {
        if error.kind() == io::ErrorKind::AlreadyExists {
            unsafe_path()
        } else {
            io_failure()
        }
    })?;

    let mut short_writes = 0;
    match short_write_limit {
        Some(limit) => {
            let mut writer = LimitedWriter::new(&file, limit);
            write_all_retrying(&mut writer, bytes, &mut short_writes).map_err(|_| io_failure())?;
        }
        None => {
            let mut writer = &file;
            write_all_retrying(&mut writer, bytes, &mut short_writes).map_err(|_| io_failure())?;
        }
    }
    file.sync_all().map_err(|_| io_failure())?;
    drop(file);

    let readback = read_no_follow(path.rendered()).map_err(|_| io_failure())?;
    if corrupt_readback || readback != bytes {
        return Err(io_failure());
    }
    if let Some(parent) = path.rendered().parent() {
        flush_directory(parent)?;
    }
    Ok(short_writes)
}

fn open_for_write(path: &ValidatedPath) -> io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true).write(true);
    match path.kind() {
        PathKind::NewFile => {
            options.create_new(true);
        }
        PathKind::File => {
            options.truncate(true);
        }
        PathKind::Directory | PathKind::NewDirectory => {
            return Err(io::Error::from(io::ErrorKind::InvalidInput));
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;

        options.mode(0o600).custom_flags(NO_FOLLOW);
    }
    options.open(path.rendered())
}

#[derive(Debug)]
struct LimitedWriter<W: Write> {
    inner: W,
    limit: usize,
}

impl<W: Write> LimitedWriter<W> {
    fn new(inner: W, limit: usize) -> Self {
        Self {
            inner,
            limit: limit.max(1),
        }
    }
}

impl<W: Write> Write for LimitedWriter<W> {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let end = bytes.len().min(self.limit);
        self.inner.write(&bytes[..end])
    }

    fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }
}

fn write_all_retrying(
    writer: &mut impl Write,
    bytes: &[u8],
    short_writes: &mut usize,
) -> io::Result<()> {
    let mut offset = 0;
    while offset < bytes.len() {
        match writer.write(&bytes[offset..]) {
            Ok(0) => return Err(io::Error::from(io::ErrorKind::WriteZero)),
            Ok(written) => {
                if written < bytes.len() - offset {
                    *short_writes += 1;
                }
                offset += written;
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

fn read_no_follow(path: &Path) -> io::Result<Vec<u8>> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;

        options.custom_flags(NO_FOLLOW);
    }
    let mut file = options.open(path)?;
    if !file.metadata()?.is_file() {
        return Err(io::Error::from(io::ErrorKind::InvalidInput));
    }
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)?;
    Ok(bytes)
}

fn hash_file(path: &Path) -> InstallResult<Digest> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;

        options.custom_flags(NO_FOLLOW);
    }
    let mut file = options.open(path).map_err(|_| io_failure())?;
    if !file.metadata().map_err(|_| io_failure())?.is_file() {
        return Err(unsafe_path());
    }
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer).map_err(|_| io_failure())?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(Digest::from_bytes(hasher.finalize().into()))
}

fn replace_manifest_checked(staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
    if staged.kind() != PathKind::File {
        return Err(unsafe_path());
    }
    if !matches!(live.kind(), PathKind::File | PathKind::NewFile) {
        return Err(unsafe_path());
    }
    staged.revalidate()?;
    live.revalidate()?;

    #[cfg(unix)]
    {
        fs::rename(staged.rendered(), live.rendered()).map_err(|_| io_failure())?;
        if let Some(parent) = live.rendered().parent() {
            flush_directory(parent)?;
        }
        Ok(())
    }
    #[cfg(not(unix))]
    {
        // ReplaceFileW with the exact destination handle is Task 8's native adapter.
        Err(InstallFailure::new(FailureCode::UnsupportedTarget))
    }
}

fn read_registration_from(path: &Path) -> InstallResult<Option<Registration>> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if is_link_like(&metadata) || !metadata.is_file() {
                return Err(foreign_state());
            }
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err(io_failure()),
    }
    let bytes = read_no_follow(path).map_err(|_| io_failure())?;
    Registration::new(bytes)
        .map(Some)
        .map_err(|_| foreign_state())
}

fn registration_target(paths: &InstallPaths) -> InstallResult<ValidatedPath> {
    match fs::symlink_metadata(paths.registration_path()) {
        Ok(metadata) => {
            if is_link_like(&metadata) || !metadata.is_file() {
                return Err(unsafe_path());
            }
            ValidatedPath::registration(paths, PathKind::File)
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            ValidatedPath::registration(paths, PathKind::NewFile)
        }
        Err(_) => Err(io_failure()),
    }
}

fn validate_registration_manifest(
    paths: &InstallPaths,
    bytes: &[u8],
) -> InstallResult<NativeManifest> {
    let manifest = parse_manifest(bytes)?;
    if manifest.name != paths.registration_name() {
        return Err(InstallFailure::new(FailureCode::OriginRejected));
    }
    let executable = manifest.executable()?;
    executable_in_owned_scope(paths, &executable)?;
    Ok(manifest)
}

/// Canonicalize the fixed root and the selected executable, then require the
/// executable to be strictly inside an owned `versions/` or `maintenance/`
/// directory. Canonicalizing both sides keeps macOS `/var` -> `/private/var`
/// trusted-ancestor aliases from turning a valid owned path into a mismatch.
fn executable_in_owned_scope(paths: &InstallPaths, executable: &Path) -> InstallResult<String> {
    let root = fs::canonicalize(paths.root()).map_err(|_| io_failure())?;
    let executable = fs::canonicalize(executable).map_err(|_| unsafe_path())?;
    let relative = relative_to_root(&root, &executable).ok_or_else(unsafe_path)?;
    let normalized = normalize_relative(&relative)?;
    if !is_owned_executable_scope(&normalized) {
        return Err(unsafe_path());
    }
    Ok(normalized)
}

fn publish_registration_checked(paths: &InstallPaths, value: &Registration) -> InstallResult<()> {
    validate_registration_manifest(paths, value.manifest())?;
    ensure_layout(paths)?;
    let staged_relative = format!("{STATE_DIR}/{STAGING_DIR}/registration.json");
    // A previous attempt may have left the staging file behind. Re-adopt it as
    // an existing regular file; a symlink or directory is still rejected.
    let staged = match ValidatedPath::new(paths, Path::new(&staged_relative), PathKind::NewFile) {
        Ok(staged) => staged,
        Err(error) if error.code == FailureCode::UnsafePath => {
            ValidatedPath::existing_file(paths, Path::new(&staged_relative))?
        }
        Err(error) => return Err(error),
    };
    write_durable_checked(&staged, value.manifest(), None, false)?;
    let staged = staged.adopt(PathKind::File)?;
    let live = registration_target(paths)?;
    replace_manifest_checked(&staged, &live)
}

fn remove_registration_checked(paths: &InstallPaths, expected: &Registration) -> InstallResult<()> {
    // A missing registration (including a missing fixed registration parent,
    // e.g. Chrome never created its profile directory) is already uninstalled.
    let Some(current) = read_registration_from(paths.registration_path())? else {
        return Ok(());
    };
    if !expected.matches_published(&current) {
        return Err(foreign_state());
    }
    let live = ValidatedPath::registration(paths, PathKind::File)?;
    live.revalidate()?;
    fs::remove_file(live.rendered()).map_err(|_| io_failure())?;
    if let Some(parent) = live.rendered().parent() {
        flush_directory(parent)?;
    }
    Ok(())
}

fn remove_owned_entry(entry: &OwnedEntry) -> InstallResult<()> {
    entry.path.revalidate()?;
    match entry.claim.kind {
        OwnedKind::File => {
            let expected = entry.claim.sha256.ok_or_else(foreign_state)?;
            if hash_file(entry.path.rendered())? != expected {
                return Err(foreign_state());
            }
            fs::remove_file(entry.path.rendered()).map_err(|_| io_failure())?;
        }
        OwnedKind::Directory => match fs::remove_dir(entry.path.rendered()) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) if error.kind() == io::ErrorKind::DirectoryNotEmpty => {
                return Err(InstallFailure::new(FailureCode::CleanupPending));
            }
            Err(_) => return Err(io_failure()),
        },
    }
    if let Some(parent) = entry.path.rendered().parent() {
        flush_directory(parent)?;
    }
    Ok(())
}

fn flush_parent_checked(path: &ValidatedPath) -> InstallResult<()> {
    path.revalidate_ancestors()?;
    let parent = path.rendered().parent().ok_or_else(unsafe_path)?;
    flush_directory(parent)
}

fn invalid_input() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidInput)
}

fn invalid_package() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidPackage)
}

fn origin_rejected() -> InstallFailure {
    InstallFailure::new(FailureCode::OriginRejected)
}

fn unsafe_path() -> InstallFailure {
    InstallFailure::new(FailureCode::UnsafePath)
}

fn io_failure() -> InstallFailure {
    InstallFailure::new(FailureCode::Io)
}

fn foreign_state() -> InstallFailure {
    InstallFailure::new(FailureCode::ForeignState)
}
