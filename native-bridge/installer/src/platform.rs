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
//! but it is **not** the production adapter: Windows HKCU publication, macOS code
//! sealing and OS trust assessment are stubs that fail closed, and it has no
//! process table, so host inspection stays `Unknown`. The native adapters
//! (`WindowsPlatform`, `MacosPlatform`) implement trust assessment (Task 4) and
//! real host inspection (Task 5); Task 8 completes native filesystem, registry
//! and cleanup. [`FakePlatform`] wraps the same primitives with fault injection
//! and call recording.

use std::collections::HashSet;
use std::ffi::{OsStr, OsString};
use std::fs::{self, File, Metadata, OpenOptions, TryLockError};
use std::io::{self, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::{Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

use crate::inventory::{
    PACKAGE_INVENTORY_FILE, PACKAGE_PAYLOAD_DIR, normalize_relative_path, parse_inventory,
    verify_inventory,
};
use crate::model::{
    Channel, Digest, FailureCode, InstallFailure, InstallResult, InventoryEntry,
    MAX_DOCUMENT_BYTES, MAX_INVENTORY_ENTRIES, MAX_PUBLISHER_BYTES, ReleaseDescriptor, Target,
    validate_extension_id,
};
use crate::paths::{
    InstallPaths, MAINTENANCE_DIR, MAX_ROOT_BYTES, STAGING_DIR, STATE_DIR, VERSIONS_DIR,
};
use crate::policy::{CARRIER_HEADER_LEN, read_carrier};

#[cfg(target_os = "macos")]
mod macos;
#[cfg(windows)]
mod windows;
// The PE carrier-resource parser is Windows-only in production but portable in
// tests, so its coverage checks are exercised on every host.
#[cfg(any(windows, test))]
mod pe;
// The pure publisher/status logic of both native adapters is compiled for its
// own target *and* for `cargo test`, so every security-relevant mapping is
// asserted on any host instead of being trusted on a native runner.
#[cfg(any(target_os = "macos", test))]
mod macos_trust;
#[cfg(any(windows, test))]
mod windows_trust;

#[cfg(target_os = "macos")]
pub use macos::MacosPlatform;
#[cfg(windows)]
pub use windows::WindowsPlatform;

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

/// Which user a process observation is attributed to.
///
/// The per-user install root is private to the installing user (`0700` on Unix,
/// the user's own ACLs on Windows), so a process positively attributed to another
/// user cannot execute an owned image. Attribution is only consulted for a
/// process that could still be ours: an image that resolves *outside* the owned
/// trees is ruled out first, so an unrelated protected system process never has
/// to be attributed to a user and never blocks the installer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProcessScope {
    /// Runs as the installing user: may execute files in the per-user root.
    InstallingUser,
    /// Positively attributed to another user.
    OtherUser,
    /// Not positively attributed to any user.
    ///
    /// Either the OS would not say, or the image was already outside the owned
    /// trees so attribution was not needed. This never rules a process out: it
    /// stays a candidate when its image is inside the owned trees, or when its
    /// image could not be resolved either, and then it blocks instead of being
    /// treated as another user's process.
    Unknown,
}

/// What the OS established about one process's executable image.
///
/// The variants are *evidence*, never a decision: the classifier applies the same
/// owned-tree rules to every value. The distinction between a resolved path and a
/// bare image name exists because the native adapters must not need a process
/// handle for every process on the machine. A system, protected (PPL) or
/// higher-privilege process may be impossible to open for a non-elevated
/// installer, and that must not make every install report
/// [`HostState::Unknown`] forever.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProcessImage {
    /// The absolute executable file path, resolved through a process handle
    /// (Windows `QueryFullProcessImageNameW`, macOS `proc_pidpath`).
    Path(PathBuf),
    /// Only the OS-reported image *name* is known, because no process handle was
    /// opened.
    ///
    /// Windows reports this base name - never a full path - in
    /// `SystemProcessInformation.ImageName` for every process. It is enough to
    /// rule a process out (a process executing an owned image always reports that
    /// image's file name), but never enough to prove the image is ours.
    Name(OsString),
    /// The OS could not resolve the image at all.
    Unresolved,
}

/// Identity of one OS process.
///
/// A PID is not an identity: the OS recycles it as soon as the process exits, so
/// a snapshot entry can stop describing anything before its image is resolved.
/// No decision is made from the snapshot, though: every candidate is re-read by
/// PID and classified from the process that holds the PID *now*, so a recycled
/// PID is neither mistaken for the observed process nor able to hide a host that
/// now runs under it. No OS start marker is retained, because nothing compares
/// it - the fresh re-read is the PID-reuse defence.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct ProcessIdentity {
    /// The process identifier.
    pub pid: u32,
}

/// One process as the OS process table reported it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProcessObservation {
    /// The process this observation describes.
    pub identity: ProcessIdentity,
    /// The user the OS attributed the process to, when it could.
    pub scope: ProcessScope,
    /// What the OS could establish about the executable image.
    ///
    /// [`ProcessImage::Unresolved`] (and [`ProcessImage::Name`] for a process that
    /// is not positively another user's) is a potential owned host, so it blocks
    /// as [`HostState::Unknown`] - unless the image resolves outside the owned
    /// trees, which rules the process out whatever its user.
    pub image: ProcessImage,
}

/// Upper bound on one process-table snapshot; a larger table is untrustworthy.
const MAX_PROCESS_OBSERVATIONS: usize = 1 << 16;

/// The OS process table, as the injected boundary for host inspection.
///
/// Native adapters implement this with Windows
/// `NtQuerySystemInformation(SystemProcessInformation)`/token/process-image calls
/// and with macOS `libproc`; the portable tests inject a scripted table. Adapters
/// report what they could read (a resolved path, or only the OS image name when no
/// handle was opened) rather than guessing an executable path.
pub trait ProcessTable {
    /// One snapshot of the process table.
    ///
    /// `paths` gives the adapter the owned trees so it can rule a process out
    /// from its image alone, without the user attribution that may be
    /// unavailable for an unrelated protected process. The result may therefore
    /// be empty when every process is irrelevant; it is never a substitute for
    /// reading the OS table, and an adapter must report a table it could not read
    /// (including a zero-length OS snapshot) as an error rather than as "no
    /// host".
    fn observe(&self, paths: &InstallPaths) -> io::Result<Vec<ProcessObservation>>;
    /// The process that currently holds `pid`, or `None` when it has exited.
    ///
    /// This is the PID-reuse check: the caller re-classifies the returned
    /// observation instead of trusting the snapshot entry, so an adapter must
    /// report the process the PID holds *now* rather than repeat a cached entry.
    /// "Present but unreadable" is an error or an unattributed observation, not
    /// `None`: only a PID that is gone may be reported as exited.
    fn recheck(&self, paths: &InstallPaths, pid: u32) -> io::Result<Option<ProcessObservation>>;
}

/// How one resolved image path relates to the owned payload trees.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ImageClass {
    /// Inside `versions/` or `maintenance/`: an owned host is executing.
    Owned,
    /// Somewhere else: not an owned image.
    Foreign,
    /// Not resolved, or not an absolute path: cannot be ruled out.
    Unresolved,
}

/// The decision after re-reading one candidate PID.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Rechecked {
    /// The process holding the PID now executes an owned image.
    Active,
    /// The process exited or its image is foreign / another user's.
    Inactive,
    /// The process cannot be read, so it cannot be ruled out.
    Unknown,
}

/// The owned executable bases (`versions/` and `maintenance/`) with canonical
/// aliases, plus the base names of the files below them.
///
/// macOS reports a process image through the resolved filesystem (`/var` is a
/// symlink to `/private/var`), so the lexical base and its canonical form are
/// both accepted there. Windows compares the verbatim `\\?\` prefix with the DOS
/// prefix by normalising it in [`comparable_components`], so a canonicalised
/// base is a usable second spelling on every target. A matching *file name* is
/// never evidence: only containment below an owned tree is. The collected names
/// are used the other way round - to rule a process out - because a Windows
/// process that cannot be opened is only reported by its image file name.
pub(crate) struct OwnedImages {
    bases: Vec<PathBuf>,
    /// Base names of the files below the owned trees, as the OS stores them.
    names: Vec<OsString>,
}

impl OwnedImages {
    /// Capture the owned bases and the base names of the files below them.
    ///
    /// Fails when the owned trees cannot be enumerated: the name set is what lets
    /// a protected process be ruled out without opening it, so an incomplete set
    /// could rule a live host out. Callers turn the error into
    /// [`HostState::Unknown`].
    pub(crate) fn capture(paths: &InstallPaths) -> io::Result<Self> {
        let mut bases = Vec::with_capacity(4);
        for base in [paths.versions(), paths.maintenance()] {
            bases.push(base.to_path_buf());
            if let Ok(canonical) = fs::canonicalize(base)
                && canonical.as_path() != base
            {
                bases.push(canonical);
            }
        }
        Ok(Self {
            bases,
            names: owned_image_names(paths)?,
        })
    }

    pub(crate) fn classify(&self, image: Option<&Path>) -> ImageClass {
        let Some(image) = image else {
            return ImageClass::Unresolved;
        };
        // A relative image cannot be shown to be outside the owned trees.
        if !image.is_absolute() {
            return ImageClass::Unresolved;
        }
        if self.bases.iter().any(|base| path_is_within(base, image)) {
            ImageClass::Owned
        } else {
            ImageClass::Foreign
        }
    }

    /// Whether an OS-reported image *name* could be the name of an owned host.
    ///
    /// Windows reports a process it could not open by its
    /// `SystemProcessInformation.ImageName`, which is the base name of the image
    /// file (never a full path) and, when the kernel has to fall back to the
    /// legacy `EPROCESS.ImageFileName`, only its first 15 characters. A reported
    /// name is therefore at most a *prefix* of the name an owned host would
    /// report, and a process is ruled out only when no owned executable's name
    /// starts with it.
    ///
    /// An empty name means the OS reports no image file at all (the idle process
    /// and other kernel-created processes), so the process cannot be executing a
    /// file below the owned trees.
    pub(crate) fn may_be_owned_name(&self, reported: &OsStr) -> bool {
        let reported = reported.to_string_lossy();
        // The contract is a bare file name; a path-shaped value is reduced to its
        // last component instead of being silently ruled out.
        let reported = reported.rsplit(['\\', '/']).next().unwrap_or_default();
        if reported.is_empty() {
            return false;
        }
        self.names
            .iter()
            .any(|name| name_starts_with(&name.to_string_lossy(), reported))
    }
}

/// Upper bound on the owned files whose names are collected.
const MAX_OWNED_IMAGE_NAMES: usize = 1 << 12;
/// Upper bound on the owned-tree walk depth.
const MAX_OWNED_IMAGE_DEPTH: usize = 16;

/// The base names of every non-directory entry below `versions/` and
/// `maintenance/`.
///
/// The walk is read-only, bounded and never follows a link: an owned tree that
/// contains one, or an entry it cannot read, is not the private tree this
/// installer wrote, so no name list can be trusted and the inspection fails
/// closed instead of ruling a live host out.
fn owned_image_names(paths: &InstallPaths) -> io::Result<Vec<OsString>> {
    let mut names = Vec::new();
    let mut pending = vec![
        (paths.versions().to_path_buf(), 0usize),
        (paths.maintenance().to_path_buf(), 0),
    ];
    while let Some((directory, depth)) = pending.pop() {
        let entries = match fs::read_dir(&directory) {
            Ok(entries) => entries,
            // A tree that does not exist yet holds no executable.
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(_) => return Err(io::Error::other("owned image directory is unreadable")),
        };
        for entry in entries {
            let entry = entry.map_err(|_| io::Error::other("owned image entry is unreadable"))?;
            if names.len() >= MAX_OWNED_IMAGE_NAMES {
                return Err(io::Error::other("implausible owned image tree"));
            }
            let metadata = fs::symlink_metadata(entry.path())
                .map_err(|_| io::Error::other("owned image entry is unreadable"))?;
            if is_link_like(&metadata) {
                return Err(io::Error::other("owned image tree contains a link"));
            }
            if metadata.is_dir() {
                if depth >= MAX_OWNED_IMAGE_DEPTH {
                    return Err(io::Error::other("owned image tree is too deep"));
                }
                pending.push((entry.path(), depth + 1));
            } else {
                names.push(entry.file_name());
            }
        }
    }
    Ok(names)
}

/// Whether `name` begins with `prefix`, ignoring ASCII case.
fn name_starts_with(name: &str, prefix: &str) -> bool {
    if prefix.chars().count() > name.chars().count() {
        return false;
    }
    name.chars()
        .zip(prefix.chars())
        .all(|(name, prefix)| name.eq_ignore_ascii_case(&prefix))
}

/// Whether one observation could be a process executing an owned image.
///
/// The image is resolved *before* the user attribution, and that ordering is the
/// point: a process whose image is outside the owned trees cannot be executing an
/// owned host whatever user it runs as, so an unrelated process that the OS will
/// not attribute (a hardened or system process) must not block the installer.
/// Only an owned image - or an image that could not be resolved, or a process that
/// could not be opened at all and therefore only has a name - still needs the
/// attribution; there, a positive "another user" rules the process out while
/// "installing user" or "unknown" leaves it a potential host.
fn is_candidate(owned: &OwnedImages, observation: &ProcessObservation) -> bool {
    match &observation.image {
        // A resolved path inside the trees is a host once the user allows it; a
        // path outside them rules the process out without any attribution.
        ProcessImage::Path(image) => match owned.classify(Some(image)) {
            ImageClass::Foreign => false,
            ImageClass::Owned | ImageClass::Unresolved => {
                observation.scope != ProcessScope::OtherUser
            }
        },
        // No handle was opened, so the OS-reported file name is the only evidence:
        // a name that no owned executable has rules the process out, and a name an
        // owned executable could have cannot.
        ProcessImage::Name(name) => {
            owned.may_be_owned_name(name) && observation.scope != ProcessScope::OtherUser
        }
        // Nothing resolved: it cannot be ruled out.
        ProcessImage::Unresolved => observation.scope != ProcessScope::OtherUser,
    }
}

/// Classify the OS process table against the fixed install layout.
///
/// A candidate is a process that could be executing an owned `versions/` or
/// `maintenance/` image; see `is_candidate` for the image-first ordering that
/// keeps unrelated, unattributable system processes from blocking. Every
/// candidate is re-read by PID immediately before the decision: an entry that
/// exited is dropped, and a recycled PID is classified by the process that holds
/// it now. Any candidate that cannot be ruled out makes the result
/// [`HostState::Unknown`] instead of claiming quiescence.
///
/// The function is read-only. It never terminates a process or touches the
/// install tree, so every possibly live version stays on disk for the caller.
pub fn inspect_host_processes(paths: &InstallPaths, table: &impl ProcessTable) -> HostState {
    let observations = match table.observe(paths) {
        Ok(observations) if observations.len() <= MAX_PROCESS_OBSERVATIONS => observations,
        // An unreadable or implausibly large process table cannot prove
        // quiescence. (An adapter may rule irrelevant processes out before it
        // returns, so an empty result is legitimate; an empty OS snapshot is the
        // adapter's own error.)
        _ => return HostState::Unknown,
    };
    let owned = match OwnedImages::capture(paths) {
        Ok(owned) => owned,
        // Without the owned file names a protected process cannot be ruled out,
        // so quiescence cannot be claimed.
        Err(_) => return HostState::Unknown,
    };
    let mut unknown = false;
    for observation in &observations {
        if !is_candidate(&owned, observation) {
            continue;
        }
        match recheck_candidate(table, paths, &owned, observation) {
            Rechecked::Active => return HostState::Active,
            Rechecked::Unknown => unknown = true,
            Rechecked::Inactive => {}
        }
    }
    if unknown {
        HostState::Unknown
    } else {
        HostState::Quiescent
    }
}

/// Re-read the process that holds the candidate's PID and decide from *that*
/// observation.
///
/// The OS may recycle a PID the moment its process exits, so the snapshot entry
/// is only a candidate pointer. The fresh observation decides: a PID recycled by
/// a foreign image or another user is inactive, while a recycled PID that now
/// executes an owned image is active. That is why the decision is made from the
/// process holding the PID now and never from the executable name.
fn recheck_candidate(
    table: &impl ProcessTable,
    paths: &InstallPaths,
    owned: &OwnedImages,
    observed: &ProcessObservation,
) -> Rechecked {
    let current = match table.recheck(paths, observed.identity.pid) {
        // The candidate exited; nothing executes its image any more.
        Ok(None) => return Rechecked::Inactive,
        Ok(Some(current)) => current,
        // The process cannot be re-read, so it cannot be ruled out.
        Err(_) => return Rechecked::Unknown,
    };
    if !is_candidate(owned, &current) {
        // A PID recycled by a foreign image or another user's process.
        return Rechecked::Inactive;
    }
    match &current.image {
        ProcessImage::Path(image) => match owned.classify(Some(image)) {
            ImageClass::Owned => Rechecked::Active,
            // `is_candidate` already ruled a foreign image out.
            ImageClass::Foreign => Rechecked::Inactive,
            ImageClass::Unresolved => Rechecked::Unknown,
        },
        // Still no handle: the name allows the process to be ours, so it cannot
        // be ruled out, and a nameless or unreadable image is no better.
        ProcessImage::Name(_) | ProcessImage::Unresolved => Rechecked::Unknown,
    }
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
/// `CompareStringOrdinal`) and a verbatim `\\?\` prefix compares equal to the
/// equivalent DOS/UNC prefix, so a canonicalised path can be compared with a
/// path an OS API reported. Unix compares exactly.
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
            Component::Prefix(prefix) => parts.push(comparable_prefix(prefix.as_os_str())),
            Component::RootDir => parts.push(OsString::from("/")),
            Component::Normal(part) => parts.push(part.to_os_string()),
            Component::CurDir | Component::ParentDir => return None,
        }
    }
    Some(parts)
}

/// One comparable drive/UNC prefix.
///
/// `fs::canonicalize` returns the verbatim form (`\\?\C:\...`, `\\?\UNC\...`)
/// while the OS known folders and `QueryFullProcessImageNameW` report the DOS
/// form (`C:\...`, `\\server\share\...`). Both spell the same volume, so the
/// verbatim marker is normalised away before comparison; otherwise a canonical
/// path could never be compared with an OS-reported one.
#[cfg(windows)]
fn comparable_prefix(prefix: &OsStr) -> OsString {
    let text = prefix.to_string_lossy();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        OsString::from(format!(r"\\{rest}"))
    } else if let Some(rest) = text.strip_prefix(r"\\?\") {
        OsString::from(rest)
    } else {
        prefix.to_os_string()
    }
}

#[cfg(not(windows))]
fn comparable_prefix(prefix: &OsStr) -> OsString {
    prefix.to_os_string()
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

/// Fail-closed classification of one native trust probe.
///
/// Both native adapters decide only two things about an OS verdict: it is an
/// explicit denial, or no assessment could be formed. An accepted verdict is not
/// representable here, so no caller can accidentally turn "unknown" into
/// "trusted".
#[cfg(any(windows, target_os = "macos", test))]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum TrustVerdict {
    /// The OS formed an explicit denial (bad signature, wrong publisher,
    /// revocation, failed requirement): fail without retry.
    Deny,
    /// The assessment could not be formed (offline revocation data, helper
    /// failure, unknown status): fail closed but retryable.
    Unavailable,
}

#[cfg(any(windows, target_os = "macos"))]
impl TrustVerdict {
    /// The installer failure this verdict maps to.
    pub(crate) fn failure(self) -> InstallFailure {
        match self {
            Self::Deny => InstallFailure::new(FailureCode::TrustDenied),
            Self::Unavailable => InstallFailure::new(FailureCode::TrustUnavailable),
        }
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

/// Native assessment verdict: WinVerifyTrust accepted the Authenticode
/// signature, its certificate chain and the chain's revocation state.
pub const TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE: &str = "windows-authenticode-trusted";
/// Native assessment verdict: the macOS adapter accepted the strict
/// code/resource seal, the policy-pinned designated requirement and the
/// Gatekeeper assessment.
pub const TRUST_ASSESSMENT_MACOS_GATEKEEPER: &str = "macos-gatekeeper-assessed";
/// Windows package member holding the Authenticode-signed policy carrier binary.
pub const PACKAGE_WINDOWS_CARRIER: &str = "carrier.exe";
/// macOS package member holding the sealed signed policy-carrier app bundle.
pub const PACKAGE_MACOS_CARRIER: &str = "carrier.app";
/// Upper bound on directory entries walked while checking a package payload tree.
const MAX_PACKAGE_TREE_ENTRIES: usize = 1 << 20;

/// The release target a native assessment verdict is evidence for.
///
/// The vocabulary is closed: only a native adapter can produce these values and
/// each verdict belongs to exactly one target/adapter. Any other string
/// (including an adapter-specific "revoked"/"unsigned" string or a future
/// verdict this build does not understand) has no target and fails closed.
pub fn assessment_target(assessment: &str) -> Option<Target> {
    match assessment {
        TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE => Some(Target::WindowsX64),
        TRUST_ASSESSMENT_MACOS_GATEKEEPER => Some(Target::MacosArm64),
        _ => None,
    }
}

/// Whether `assessment` is an accepted native verdict **for `target`**.
///
/// Binding the verdict to the descriptor target keeps the decision layer
/// self-defending: a `macos-gatekeeper-assessed` proof can never satisfy a
/// Windows release (or the reverse), even if an adapter or an injected proof
/// reports a verdict from the other platform.
pub fn is_accepted_assessment(assessment: &str, target: Target) -> bool {
    assessment_target(assessment) == Some(target)
}

/// One payload file of an [`AuthenticatedPackage`], bound to its exact content.
///
/// The handle is created only after the inventory document was authenticated by
/// the descriptor digest and every declared file matched size and SHA-256. It
/// names the package source, not a destination: staging (Task 6) must call
/// [`AuthenticatedSource::revalidate`] immediately before copying, because the
/// package directory is user-writable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthenticatedSource {
    relative: String,
    path: PathBuf,
    size: u64,
    sha256: Digest,
    executable: bool,
}

impl AuthenticatedSource {
    /// Normalized `/`-joined payload path (the installed inventory path).
    pub fn relative(&self) -> &str {
        &self.relative
    }

    /// Absolute path of the package source file.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Authenticated file size.
    pub fn size(&self) -> u64 {
        self.size
    }

    /// Authenticated content digest.
    pub fn sha256(&self) -> Digest {
        self.sha256
    }

    /// Whether the authenticated inventory marks this file executable.
    pub fn executable(&self) -> bool {
        self.executable
    }

    /// Re-hash the source file immediately before copying it.
    ///
    /// A missing, replaced, linked or content-changed source is a failure; the
    /// caller must never stage bytes that no longer match the authenticated
    /// inventory.
    pub fn revalidate(&self) -> InstallResult<()> {
        let metadata = match fs::symlink_metadata(&self.path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Err(foreign_state()),
            Err(_) => return Err(io_failure()),
        };
        if is_link_like(&metadata) || !metadata.is_file() {
            return Err(unsafe_path());
        }
        if metadata.len() != self.size || hash_file(&self.path)? != self.sha256 {
            return Err(foreign_state());
        }
        Ok(())
    }
}

/// A package whose OS signature, embedded policy carrier, descriptor, publisher
/// and complete payload inventory were verified against a caller-authorized
/// release descriptor.
///
/// There is deliberately no public constructor: every value comes from
/// [`authenticate_package`], which is the only place that turns injected OS
/// evidence into package authority.
#[derive(Debug, Clone)]
pub struct AuthenticatedPackage {
    source: PackageSource,
    proof: TrustProof,
    descriptor: ReleaseDescriptor,
    payload: Vec<AuthenticatedSource>,
}

impl AuthenticatedPackage {
    /// The canonical package root the evidence was gathered from.
    pub fn package(&self) -> &PackageSource {
        &self.source
    }

    /// The exact authenticated OS evidence and carrier bytes.
    pub fn proof(&self) -> &TrustProof {
        &self.proof
    }

    /// The authenticated descriptor (equal to the caller-authorized descriptor).
    pub fn descriptor(&self) -> &ReleaseDescriptor {
        &self.descriptor
    }

    /// The OS-verified signer identity (an exact publisher-policy string).
    pub fn signer(&self) -> &str {
        self.proof.signer()
    }

    /// The OS assessment verdict.
    pub fn assessment(&self) -> &str {
        self.proof.assessment()
    }

    /// The complete authenticated payload inventory with source handles.
    pub fn payload(&self) -> &[AuthenticatedSource] {
        &self.payload
    }

    /// The authenticated payload identity.
    pub fn payload_id(&self) -> Digest {
        self.descriptor.payload_id
    }
}

/// Authenticate one extracted engine package against a caller-authorized release.
///
/// Contract, in order:
///
/// 1. The caller-supplied `expected` descriptor is structurally valid and is the
///    release authorization anchor.
/// 2. `package` is an existing directory and the platform adapter authenticates
///    its own signed policy carrier (`carrier.exe` on Windows, `carrier.app` on
///    macOS) and returns the OS signer/assessment plus the authenticated carrier
///    bytes. Any OS denial (`TrustDenied`) or unavailable required assessment
///    (`TrustUnavailable`) is returned unchanged.
/// 3. The assessment verdict belongs to the caller-authorized release target
///    (`windows-authenticode-trusted` to `windows-x86_64`,
///    `macos-gatekeeper-assessed` to `macos-arm64`) and is in the closed native
///    vocabulary, the carrier parses, its descriptor equals `expected` exactly,
///    and the OS-verified signer is listed in that authenticated policy's
///    publishers. Any mismatch is `TrustDenied`.
/// 4. `<package>/inventory.json` hashes to `descriptor.payload_id`, parses
///    strictly, matches every payload file's size and SHA-256, and `<package>/payload`
///    holds exactly the inventoried tree (no unreferenced, linked or special
///    file). A tampered payload is `TrustDenied`.
///
/// The function is read-only: it never locks, writes, publishes or removes and
/// cannot change a prior installation. Failures never claim a fresh security
/// assessment of a prior install (`prior_usable` stays unset for the operation
/// layer to decide from receipts).
pub fn authenticate_package(
    platform: &impl Platform,
    package: &Path,
    expected: &ReleaseDescriptor,
) -> InstallResult<AuthenticatedPackage> {
    expected.validate()?;
    let source = PackageSource::open(package)?;
    if source.kind() != PackageKind::Directory {
        return Err(invalid_package());
    }

    let proof = platform.verify_trust(&source, expected)?;
    let descriptor = authenticated_descriptor(&proof, expected)?;
    let payload = authenticated_payload(&source, &descriptor)?;

    Ok(AuthenticatedPackage {
        source,
        proof,
        descriptor,
        payload,
    })
}

fn authenticated_descriptor(
    proof: &TrustProof,
    expected: &ReleaseDescriptor,
) -> InstallResult<ReleaseDescriptor> {
    // The verdict must be one this engine accepts *for this release target*: a
    // macOS Gatekeeper proof cannot authenticate a Windows descriptor.
    if !is_accepted_assessment(proof.assessment(), expected.target) {
        return Err(trust_denied());
    }
    let descriptor = read_carrier(proof.carrier()).map_err(|_| trust_denied())?;
    if &descriptor != expected {
        return Err(trust_denied());
    }
    if !descriptor
        .policy
        .publishers
        .iter()
        .any(|publisher| publisher == proof.signer())
    {
        return Err(trust_denied());
    }
    Ok(descriptor)
}

fn authenticated_payload(
    source: &PackageSource,
    descriptor: &ReleaseDescriptor,
) -> InstallResult<Vec<AuthenticatedSource>> {
    let payload_root = source.path().join(PACKAGE_PAYLOAD_DIR);
    let inventory_path = source.path().join(PACKAGE_INVENTORY_FILE);
    let bytes = match read_no_follow(&inventory_path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Err(invalid_package()),
        Err(_) => return Err(io_failure()),
    };
    if bytes.len() > MAX_DOCUMENT_BYTES {
        return Err(invalid_package());
    }
    // The descriptor (itself signature-covered) authenticates the exact document
    // bytes; no caller argument can substitute another inventory.
    if Digest::of(&bytes) != descriptor.payload_id {
        return Err(trust_denied());
    }
    let entries = parse_inventory(&bytes)?;
    verify_inventory(&payload_root, &entries).map_err(|failure| match failure.code {
        FailureCode::InvalidPackage => trust_denied(),
        _ => failure,
    })?;
    verify_closed_payload_tree(&payload_root, &entries)?;

    let mut payload = Vec::with_capacity(entries.len());
    for entry in &entries {
        let relative = normalize_relative_path(&entry.path)?;
        payload.push(AuthenticatedSource {
            path: payload_root.join(Path::new(&relative)),
            relative,
            size: entry.size,
            sha256: entry.sha256,
            executable: entry.executable,
        });
    }
    Ok(payload)
}

/// Reject an unreferenced, linked or special file below the package payload.
///
/// `verify_inventory` only checks declared entries; the package itself can still
/// hide a planted DLL next to the host. This walk requires the payload tree to be
/// exactly the inventoried one, so an extra byte never reaches staging.
fn verify_closed_payload_tree(root: &Path, entries: &[InventoryEntry]) -> InstallResult<()> {
    let metadata = fs::symlink_metadata(root).map_err(|_| invalid_package())?;
    if is_link_like(&metadata) || !metadata.is_dir() {
        return Err(invalid_package());
    }

    let mut expected_files: HashSet<String> = HashSet::with_capacity(entries.len());
    let mut expected_directories: HashSet<String> = HashSet::with_capacity(entries.len());
    for entry in entries {
        let normalized = normalize_relative_path(&entry.path)?;
        expected_files.insert(normalized.clone());
        let parts: Vec<&str> = normalized.split('/').collect();
        let mut prefix = String::new();
        for part in &parts[..parts.len() - 1] {
            if !prefix.is_empty() {
                prefix.push('/');
            }
            prefix.push_str(part);
            expected_directories.insert(prefix.clone());
        }
    }

    let mut stack = vec![(root.to_path_buf(), String::new())];
    let mut visited = 0usize;
    while let Some((directory, prefix)) = stack.pop() {
        for entry in fs::read_dir(&directory).map_err(|_| io_failure())? {
            let entry = entry.map_err(|_| io_failure())?;
            visited += 1;
            if visited > MAX_PACKAGE_TREE_ENTRIES {
                return Err(invalid_package());
            }
            let name = entry.file_name();
            let name = name.to_str().ok_or_else(unsafe_path)?;
            let relative = if prefix.is_empty() {
                name.to_owned()
            } else {
                format!("{prefix}/{name}")
            };
            let metadata = fs::symlink_metadata(entry.path()).map_err(|_| io_failure())?;
            if is_link_like(&metadata) {
                return Err(unsafe_path());
            }
            if metadata.is_file() {
                if !expected_files.contains(&relative) {
                    return Err(trust_denied());
                }
            } else if metadata.is_dir() {
                if !expected_directories.contains(&relative) {
                    return Err(trust_denied());
                }
                stack.push((entry.path(), relative));
            } else {
                return Err(unsafe_path());
            }
        }
    }
    Ok(())
}

/// The injected OS boundary. Tasks 4, 5 and 8 implement the native adapters.
///
/// `inspect_hosts` classifies whether a process executing an owned host version
/// is running; [`require_quiescent`] is the bounded wait an operation uses before
/// any mutation.
pub trait Platform {
    /// Acquire the exclusive per-user installer lock (nonblocking).
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock>;
    /// Write all bytes, flush and read the exact bytes back.
    fn write_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()>;
    /// Create one private directory below the fixed install root.
    ///
    /// `path` is a validated [`PathKind::NewDirectory`] handle that was absent
    /// when it was validated; an existing leaf is `UnsafePath` rather than a
    /// silent adoption. Each ancestor is revalidated, the directory is created
    /// with owner-only permissions and its parent is flushed.
    ///
    /// The portable primitive is the default because directory creation is not
    /// OS-specific; a native adapter overrides it only to apply per-user ACLs
    /// (Task 8).
    fn create_directory(&self, path: &ValidatedPath) -> InstallResult<()> {
        create_private_directory(path)
    }
    /// Atomically replace the one manifest that selects the executable.
    fn replace_manifest(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()>;
    /// Read the live registration exactly, or `None` when absent.
    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>>;
    /// Publish exact manifest bytes (and, natively on Windows, the HKCU value).
    ///
    /// The manifest must name this channel and an executable inside an owned
    /// `versions/` or `maintenance/` directory. A live registration may be
    /// replaced only when its exact bytes equal `value` (idempotent republish)
    /// or it is a previously published owned manifest (an upgrade). Any other
    /// live registration yields `ForeignState` and is left byte-for-byte
    /// intact; this layer never assumes the caller established ownership.
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
    ///
    /// `source` is the canonical engine package root (a directory). The adapter
    /// locates its own signed policy carrier inside it (`carrier.exe` on Windows,
    /// `carrier.app` on macOS), proves the OS signature and assessment, extracts
    /// the embedded carrier bytes, and returns them with the OS-verified signer
    /// identity and assessment verdict. It performs no filesystem mutation.
    ///
    /// Classification is part of the contract: an explicit OS denial or
    /// revocation is `TrustDenied`; a required assessment that cannot be formed
    /// (offline revocation data unavailable, broken OS trust environment) is
    /// `TrustUnavailable`. Neither may be reported as success with a warning.
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

/// The longest an installer waits for owned hosts to exit.
///
/// The wait is bounded: an install decision never blocks forever on a Chrome
/// profile the user forgot to close. Nothing is terminated to shorten the wait -
/// the [`Platform`] boundary has no termination call at all - so the bound only
/// decides when the failure is reported.
pub const QUIESCENCE_TIMEOUT: Duration = Duration::from_secs(10);

/// Poll interval between quiescence inspections.
const QUIESCENCE_POLL_INTERVAL: Duration = Duration::from_millis(50);

/// Block until no process executes an owned host version, or fail with the
/// exact state.
///
/// * [`HostState::Quiescent`] returns `Ok`.
/// * [`HostState::Active`] is `HostActive` after [`QUIESCENCE_TIMEOUT`].
/// * [`HostState::Unknown`] is `InspectionDenied` after [`QUIESCENCE_TIMEOUT`].
///
/// The state is re-inspected until the bound elapses, so a host that exits while
/// the user closes the last tab lets the operation continue, while a host that
/// stays running (or an inspection that stays unreadable) fails visibly instead
/// of waiting forever. This function never mutates, terminates or removes
/// anything: on failure the caller still holds every possibly live payload, and
/// the failure must be reported rather than treated as a completed cleanup.
pub fn require_quiescent(platform: &impl Platform, paths: &InstallPaths) -> InstallResult<()> {
    require_quiescent_within(platform, paths, QUIESCENCE_TIMEOUT)
}

/// [`require_quiescent`] with an explicit bound, clamped to
/// [`QUIESCENCE_TIMEOUT`].
///
/// The explicit bound lets a caller ask for a shorter wait (and lets the portable
/// tests exercise the bounded loop without a ten-second stall). A longer request
/// is clamped: no caller may extend the install's wait past the fixed ceiling.
pub fn require_quiescent_within(
    platform: &impl Platform,
    paths: &InstallPaths,
    timeout: Duration,
) -> InstallResult<()> {
    let deadline = Instant::now() + timeout.min(QUIESCENCE_TIMEOUT);
    loop {
        match platform.inspect_hosts(paths)? {
            HostState::Quiescent => return Ok(()),
            HostState::Active => {
                if Instant::now() >= deadline {
                    return Err(InstallFailure::new(FailureCode::HostActive));
                }
            }
            HostState::Unknown => {
                if Instant::now() >= deadline {
                    return Err(InstallFailure::new(FailureCode::InspectionDenied));
                }
            }
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        thread::sleep(QUIESCENCE_POLL_INTERVAL.min(remaining));
    }
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
        // The portable adapter has no process table. `Unknown` blocks every
        // mutation instead of claiming quiescence.
        Ok(HostState::Unknown)
    }

    fn verify_trust(
        &self,
        _source: &PackageSource,
        _expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        // OS trust evidence is available only through the native adapters
        // (`WindowsPlatform`/`MacosPlatform`); the portable adapter never
        // fabricates a verdict.
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
    /// Force `verify_trust` to report an explicit OS denial/revocation.
    pub trust_denied: bool,
    /// Simulate a process death at the N-th state mutation (1-based).
    ///
    /// Unlike `fail_mutation_at` this panics instead of returning an error, so
    /// no caller error path or cleanup runs: the transaction is left exactly as
    /// a killed installer would leave it.
    pub panic_at_mutation: Option<usize>,
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
            trust_denied: false,
            panic_at_mutation: None,
        }
    }
}

/// One call observed by [`FakePlatform`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PlatformCall {
    Lock,
    WriteDurable,
    CreateDirectory,
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
                | Self::CreateDirectory
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
        if self.faults.panic_at_mutation == Some(state.mutations) {
            // A simulated process death: the guard is dropped by unwinding, the
            // mutex is left poisoned (the accessor recovers it) and no caller
            // error path or cleanup runs.
            panic!(
                "injected process death at state mutation {}",
                state.mutations
            );
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

    fn create_directory(&self, path: &ValidatedPath) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::CreateDirectory)?;
        create_private_directory(path)
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
        if self.faults.trust_denied {
            return Err(InstallFailure::new(FailureCode::TrustDenied));
        }
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

/// Create one owner-only directory below a trusted anchor.
///
/// The handle must be a `NewDirectory` (absent when validated): adopting an
/// existing directory is a separate decision, never a side effect of creating
/// one. The parent is flushed so the new name survives a crash.
fn create_private_directory(path: &ValidatedPath) -> InstallResult<()> {
    if path.kind() != PathKind::NewDirectory {
        return Err(invalid_input());
    }
    path.revalidate()?;
    #[cfg(unix)]
    let mut builder = fs::DirBuilder::new();
    #[cfg(not(unix))]
    let builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;

        builder.mode(0o700);
    }
    match builder.create(path.rendered()) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => return Err(unsafe_path()),
        Err(_) => return Err(io_failure()),
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        fs::set_permissions(path.rendered(), fs::Permissions::from_mode(0o700))
            .map_err(|_| io_failure())?;
    }
    if let Some(parent) = path.rendered().parent() {
        flush_directory(parent)?;
    }
    Ok(())
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
    // A publish may replace our own previous manifest (an upgrade), but it must
    // never silently steal a registration another product owns. Check the live
    // manifest before staging anything so a refusal mutates nothing.
    if let Some(live) = read_registration_from(paths.registration_path())?
        && !value.matches_published(&live)
        && !is_owned_registration_preimage(paths, &live)
    {
        return Err(foreign_state());
    }
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

/// Whether `value` is a registration this installer owns for `paths`.
///
/// A registration is owned only when it names this channel's host and selects an
/// executable inside the fixed root's `versions/` or `maintenance/` scope. The
/// check is lexical (the previous executable need not still exist) and
/// read-only; [`Platform::publish_registration`] applies the same rule before it
/// replaces anything. The transaction layer uses it to decide whether the live
/// registration it found is its own before it reports a recovery verdict.
pub fn registration_is_owned(paths: &InstallPaths, value: &Registration) -> bool {
    is_owned_registration_preimage(paths, value)
}

/// Whether an existing live registration is a previously published manifest of
/// this installer (an upgrade preimage) rather than a foreign product's.
///
/// A preimage is accepted only when it names this channel and selects an
/// executable inside the fixed root's owned `versions/` or `maintenance/`
/// scope. The check is lexical, so it does not require the previous executable
/// to still exist: a partial upgrade can leave a manifest whose payload was
/// already removed, and that manifest is still ours to replace. The root is
/// also compared in canonical form when it resolves, so a trusted-ancestor
/// alias (for example macOS `/var` -> `/private/var`) does not make our own
/// manifest look foreign.
fn is_owned_registration_preimage(paths: &InstallPaths, live: &Registration) -> bool {
    let Ok(manifest) = parse_manifest(live.manifest()) else {
        return false;
    };
    if manifest.name != paths.registration_name() {
        return false;
    }
    let Ok(executable) = manifest.executable() else {
        return false;
    };
    let owned_under = |root: &Path| {
        relative_to_root(root, &executable)
            .and_then(|relative| normalize_relative(&relative).ok())
            .is_some_and(|normalized| is_owned_executable_scope(&normalized))
    };
    owned_under(paths.root())
        || fs::canonicalize(paths.root())
            .map(|root| owned_under(&root))
            .unwrap_or(false)
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

fn trust_denied() -> InstallFailure {
    InstallFailure::new(FailureCode::TrustDenied)
}

#[cfg(all(test, windows))]
mod tests {
    use super::path_is_within;
    use std::path::Path;

    #[test]
    fn verbatim_and_dos_prefixes_compare_equal() {
        // `fs::canonicalize` returns the verbatim form; the OS known folders and
        // process image APIs report the DOS form. Both must compare as the same
        // volume, or an owned image could never be matched on Windows.
        assert!(path_is_within(
            Path::new(r"C:\Users\Test\AppData\Local\Veles\prod\versions"),
            Path::new(r"\\?\C:\Users\Test\AppData\Local\Veles\prod\versions\1.0\host.exe"),
        ));
        assert!(path_is_within(
            Path::new(r"\\?\C:\Users\Test\AppData\Local\Veles\prod\maintenance"),
            Path::new(r"c:\users\test\appdata\local\veles\prod\maintenance\cleanup.exe"),
        ));
        assert!(path_is_within(
            Path::new(r"\\server\share\Veles\maintenance"),
            Path::new(r"\\?\UNC\server\share\Veles\maintenance\cleanup.exe"),
        ));
        assert!(!path_is_within(
            Path::new(r"C:\Veles\versions"),
            Path::new(r"\\?\D:\Veles\versions\1.0\host.exe"),
        ));
    }
}
