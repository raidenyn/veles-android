//! Injected OS boundary for the per-user installer.
//!
//! This module owns the opaque handles that later tasks pass around:
//! [`ValidatedPath`] (validated ancestry plus captured file identity),
//! [`OwnedEntry`] (an ownership claim that survived content validation),
//! [`InstallLock`] (the exclusive per-user lock), [`PackageSource`],
//! [`TrustProof`] and [`Registration`]. Mutations only accept those handles,
//! never raw receipt-supplied strings.
//!
//! [`ValidatedPath`] captures the identity (device/inode on Unix, volume serial
//! plus file index on Windows) of the anchor and every ancestor, and of an
//! existing leaf. Each native mutation then opens that captured chain with
//! no-follow OS calls, verifies every opened component against the captured
//! identity and acts **relative to the resulting handle**
//! (`openat`/`renameat`/`unlinkat` on macOS, `NtCreateFile`/`NtSetInformationFile`
//! with `RootDirectory` on Windows), so a component swapped after validation
//! fails closed and a leaf swapped between hashing and unlinking is preserved.
//!
//! The guarantee is stated exactly: identity is verified immediately before each
//! mutation and a mismatch aborts, removing only what the call created. Creation
//! is handle-relative as well (the Windows carrier destination is created with an
//! exclusive `NtCreateFile` below the anchored parent and populated through that
//! handle), so no pathname is re-resolved for it. A **same-user** attacker who can
//! already write inside the private `0700` root is deliberately out of scope (the
//! Task 2 policy): on macOS it can rename an entry between the identity re-read
//! and the name-based `unlinkat`, and the platform offers no `funlinkat` that would
//! close that window. The installer reports the mismatch instead of claiming
//! atomicity it does not have. A path above the mutation anchor is walked with
//! no-follow/`FILE_OPEN_REPARSE_POINT` semantics and is never canonicalised
//! first, so a link planted there is refused rather than resolved into a tree the
//! installer did not create.
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
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

use crate::inventory::{
    PACKAGE_INVENTORY_FILE, PACKAGE_PAYLOAD_DIR, SEALED_INVENTORY_FILE, entry_is_signable_image,
    normalize_relative_path, parse_inventory, verify_correspondence, verify_inventory,
};
use crate::model::{
    Channel, Digest, FailureCode, InstallFailure, InstallResult, InventoryEntry,
    MAX_DOCUMENT_BYTES, MAX_INVENTORY_BYTES, MAX_INVENTORY_ENTRIES, MAX_PATH_BYTES,
    MAX_PUBLISHER_BYTES, ReleaseDescriptor, Target, validate_extension_id,
};
use crate::paths::{
    InstallPaths, JOURNAL_FILE, MAINTENANCE_DIR, MAX_ROOT_BYTES, RECEIPT_FILE, STAGING_DIR,
    STATE_DIR, VERSIONS_DIR,
};
use crate::policy::{CarrierContents, MAX_CARRIER_DOCUMENT_BYTES, read_carrier_contents};

// Shared native-file orchestration: the decisions both native adapters must make
// identically (durable writes, atomic replacement, ordered publication, exact
// removal) live here; each adapter supplies only the OS primitives.
#[cfg(target_os = "macos")]
mod macos;
#[cfg_attr(not(any(windows, target_os = "macos", test)), allow(dead_code))]
mod native;
#[cfg(windows)]
mod windows;
#[cfg(windows)]
pub(crate) use windows::{anchored_open_assessment_below, anchored_open_regular_below};
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
#[doc(hidden)]
// Exposed for the native acceptance tests, which prove the anchored traversal
// and identity-bound deletion directly; not used by the installer engine.
pub use macos::CleanupRoot as MacosCleanupRoot;
#[cfg(target_os = "macos")]
pub use macos::MacosPlatform;
#[cfg(target_os = "macos")]
pub(crate) use macos::{macos_process_start, macos_verify_own_signature};
pub use native::StdNativeFs;
#[cfg(windows)]
#[doc(hidden)]
// Exposed for the native acceptance tests; not used by the installer engine.
pub use windows::CleanupRoot as WindowsCleanupRoot;
#[cfg(windows)]
pub use windows::WindowsPlatform;
#[cfg(windows)]
pub(crate) use windows::windows_verify_own_signature;
#[cfg(windows)]
pub use windows::{PRODUCTION_REGISTRY_PREFIX, RegistryScope, RegistryView, TEST_REGISTRY_PREFIX};

/// The only ownership-record schema generation supported at this boundary.
pub const OWNERSHIP_SCHEMA_VERSION: u32 = 1;
/// A registration manifest is a bounded JSON document.
pub const MAX_MANIFEST_PATH_BYTES: usize = 32_767;

/// `O_NOFOLLOW` for the audited Linux architectures. The value is per architecture
/// family: the asm-generic set uses `0o400_000`, while 32/64-bit ARM uses
/// `0o100_000` (where `0o400_000` is `O_LARGEFILE`). One constant for every Linux
/// target is wrong on at least ARM, and on mips/powerpc/sparc/alpha `0o400_000` is
/// not even a no-follow flag (on powerpc it is `O_DIRECT`, which rejects ordinary
/// reads), so unaudited architectures do not build instead of being handed a flag
/// that either protects nothing or breaks ordinary files.
#[cfg(all(
    unix,
    any(target_os = "linux", target_os = "android"),
    any(
        target_arch = "x86",
        target_arch = "x86_64",
        target_arch = "riscv64",
        target_arch = "loongarch64",
        target_arch = "s390x",
    )
))]
pub(crate) const NO_FOLLOW: i32 = 0o400_000;
#[cfg(all(
    unix,
    any(target_os = "linux", target_os = "android"),
    any(target_arch = "arm", target_arch = "aarch64")
))]
pub(crate) const NO_FOLLOW: i32 = 0o100_000;
#[cfg(all(
    any(target_os = "linux", target_os = "android"),
    not(any(
        target_arch = "x86",
        target_arch = "x86_64",
        target_arch = "riscv64",
        target_arch = "loongarch64",
        target_arch = "s390x",
        target_arch = "arm",
        target_arch = "aarch64",
    ))
))]
compile_error!(
    "no audited O_NOFOLLOW value for this Linux architecture; refusing to build with a \
     flag that may protect nothing (or break ordinary reads) instead of guessing"
);
#[cfg(all(
    unix,
    any(target_os = "macos", target_os = "ios", target_os = "freebsd")
))]
pub(crate) const NO_FOLLOW: i32 = 0x0100;
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
compile_error!(
    "no audited O_NOFOLLOW value for this Unix target; refusing to build with a flag \
     that protects nothing instead of defaulting to zero"
);

#[cfg(windows)]
pub(crate) const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0400;

/// One installer state document addressed through the platform boundary.
///
/// State documents are replaced atomically (stage, flush, rename, flush the
/// parent) so a process death can leave the previous document or the new one,
/// never a truncated JSON envelope. Reads also go through the boundary so a
/// test can model a read fault.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum StateDocument {
    /// The transaction journal (`state/journal.json`).
    Journal,
    /// The committed receipt (`state/receipt.json`).
    Receipt,
}

impl StateDocument {
    /// The fixed file name below `state/`.
    pub const fn file_name(self) -> &'static str {
        match self {
            Self::Journal => JOURNAL_FILE,
            Self::Receipt => RECEIPT_FILE,
        }
    }

    /// The durable quarantine name an uninterpretable document is moved to.
    ///
    /// The fixed layout has no unlink primitive for state documents; keeping the
    /// rejected bytes under a distinct name preserves them for diagnostics while
    /// releasing the live slot.
    pub fn quarantine_file_name(self) -> String {
        format!("{}.quarantine", self.file_name())
    }
}

/// Classification of a validated path at construction time.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PathKind {
    /// An existing regular file; its identity is captured.
    File,
    /// An existing directory; its identity is captured.
    Directory,
    /// An existing symbolic link; the link's own identity is captured and the
    /// target is never followed. Only a signed carrier bundle may own one.
    Symlink,
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
                // The only links this installer ever writes into the owned trees
                // are the contained links of a signed carrier bundle (for example
                // a framework's `Versions/Current`). Such a link is neither
                // followed nor descended into - its target is an ordinary entry
                // enumerated on its own path - and it contributes no name. Any
                // other link is not the private tree this installer wrote.
                if is_sanctioned_carrier_link(paths, &entry.path()) {
                    continue;
                }
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

/// Whether `link` is a contained link of an installed signed carrier artifact.
///
/// Sanctioned means all of: the link sits strictly inside
/// `versions/<release>/<carrier artifact>/` or `maintenance/<x>/<carrier
/// artifact>/` of this installation (the artifact name is fixed per target), and
/// its stored target - read as text, never resolved - stays inside that artifact
/// root under the same confinement rule the carrier enumeration applied when the
/// link was journaled ([`confine_link_target`]). Anything else, including a link
/// directly under a version directory, is unsafe.
fn is_sanctioned_carrier_link(paths: &InstallPaths, link: &Path) -> bool {
    let Ok(relative) = link.strip_prefix(paths.root()) else {
        return false;
    };
    let mut parts = Vec::new();
    for component in relative.components() {
        let Component::Normal(part) = component else {
            return false;
        };
        let Some(part) = part.to_str() else {
            return false;
        };
        parts.push(part);
    }
    if parts.len() < 4
        || !matches!(parts[0], VERSIONS_DIR | MAINTENANCE_DIR)
        || parts[2] != package_carrier_name(paths.target())
    {
        return false;
    }
    let inside_artifact = parts[3..].join("/");
    link_target_text(link)
        .and_then(|target| confine_link_target(&inside_artifact, &target))
        .is_ok()
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

/// Whether `path`'s final component is one of the installer's fixed layout
/// directory names.
///
/// A cleanup helper only ever opens an installer-owned layout directory as its
/// traversal root, so an arbitrary accessible directory cannot be named by a
/// request (or be opened by a mistaken call). This is a *shape* check, not proof
/// of ownership: every entry below the root is still bound to its exact identity.
#[cfg_attr(not(any(windows, target_os = "macos")), allow(dead_code))]
pub(crate) fn is_layout_root(path: &Path) -> bool {
    matches!(
        path.file_name().and_then(|name| name.to_str()),
        Some(crate::paths::ROOT_DIR_PRODUCTION)
            | Some(crate::paths::ROOT_DIR_DEVELOPMENT)
            | Some(crate::paths::ROOT_DIR_FIXTURE)
    )
}

#[cfg(unix)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct FileIdentity {
    pub(crate) device: u64,
    pub(crate) inode: u64,
}

#[cfg(unix)]
fn identity_of(_path: &Path, metadata: &Metadata) -> InstallResult<FileIdentity> {
    use std::os::unix::fs::MetadataExt;

    Ok(FileIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
    })
}

/// The effective user id of this process (used to prove directory ownership).
#[cfg(unix)]
pub(crate) fn effective_user_id() -> u32 {
    unsafe extern "C" {
        fn geteuid() -> u32;
    }

    // SAFETY: geteuid has no preconditions and cannot fail.
    unsafe { geteuid() }
}

/// The identity of one file-system object on Windows: the volume it lives on and
/// its per-volume file index, both read from an opened handle.
#[cfg(not(unix))]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct FileIdentity {
    pub(crate) volume: u32,
    pub(crate) index: u64,
}

/// The identity of one object, read from **an opened handle**.
///
/// A file index is allocated per volume and is not reused while the object
/// exists, so it identifies the object the handle was opened on rather than the
/// string that was walked. This is the identity every mutation is bound to.
#[cfg_attr(not(any(windows, target_os = "macos")), allow(dead_code))]
#[cfg(unix)]
pub(crate) fn identity_of_file(file: &File) -> InstallResult<FileIdentity> {
    use std::os::unix::fs::MetadataExt;

    let metadata = file.metadata().map_err(|_| io_failure())?;
    Ok(FileIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
    })
}

/// [`identity_of_file`] on Windows: volume serial plus file index.
#[cfg_attr(not(any(windows, target_os = "macos")), allow(dead_code))]
#[cfg(windows)]
pub(crate) fn identity_of_file(file: &File) -> InstallResult<FileIdentity> {
    use ::windows::Win32::Foundation::HANDLE;
    use ::windows::Win32::Storage::FileSystem::{
        BY_HANDLE_FILE_INFORMATION, GetFileInformationByHandle,
    };

    let mut information = BY_HANDLE_FILE_INFORMATION::default();
    // SAFETY: `file` is a live handle and the out-structure is correctly sized.
    unsafe {
        GetFileInformationByHandle(
            HANDLE(std::os::windows::io::AsRawHandle::as_raw_handle(file)),
            &mut information,
        )
    }
    .map_err(|_| io_failure())?;
    let index =
        (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow);
    if index == 0 {
        // A file system that reports no stable index cannot be identity-bound.
        return Err(unsafe_path());
    }
    Ok(FileIdentity {
        volume: information.dwVolumeSerialNumber,
        index,
    })
}

/// Discard exactly one leaf that a carrier install created.
///
/// `created` is the identity read from the handle that created the destination;
/// `current` re-reads what now occupies the leaf (relative to the same anchored
/// parent) and `delete` removes the object that was re-read. Only the recorded
/// object may be removed:
///
/// * the leaf no longer holds that object (a file that appeared at the name, a
///   link, a directory, an unreadable identity) → nothing is deleted and the
///   caller is told `ForeignState`; the journaled cleanup then reports the
///   leftover (`LeftoverFiles` / `CleanupPending`) instead of this call
///   destroying foreign content;
/// * the leaf still holds it but the deletion is refused → the leftover is
///   reported as `CleanupPending`;
/// * the object is removed → the caller is still told `ForeignState`.
///
/// This function is **only** usable on a carrier install's failure path: it
/// exists because the destination could not be proven to be the object the
/// install created, so it never reports success. A successful removal must not
/// turn a detected mismatch into a successful installation — the caller has to
/// surface `ForeignState` and let the transaction roll back.
#[cfg(any(windows, test))]
pub(crate) fn discard_created_leaf<C, D>(
    created: FileIdentity,
    current: C,
    delete: D,
) -> InstallResult<()>
where
    C: FnOnce() -> InstallResult<Option<FileIdentity>>,
    D: FnOnce() -> InstallResult<()>,
{
    let current = current().ok().flatten();
    if current != Some(created) {
        return Err(foreign_state());
    }
    match delete() {
        // The object was removed, but the install still failed: deleting our own
        // half-written carrier is not evidence that the destination is ours.
        Ok(()) => Err(foreign_state()),
        Err(_) => Err(InstallFailure::new(FailureCode::CleanupPending)),
    }
}

#[cfg(not(unix))]
fn identity_of(path: &Path, _metadata: &Metadata) -> InstallResult<FileIdentity> {
    use ::windows::Win32::Foundation::INVALID_HANDLE_VALUE;
    use ::windows::Win32::Storage::FileSystem::{
        CreateFileW, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
        FILE_READ_ATTRIBUTES, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
    };
    use ::windows::core::PCWSTR;
    use std::os::windows::io::FromRawHandle;

    let wide: Vec<u16> = {
        use std::os::windows::ffi::OsStrExt;

        path.as_os_str().encode_wide().chain([0]).collect()
    };
    // SAFETY: `wide` is NUL-terminated and outlives the call. The reparse-point
    // flag identifies the link itself for a link, and backup semantics allow a
    // directory to be opened for its attributes.
    let handle = unsafe {
        CreateFileW(
            PCWSTR(wide.as_ptr()),
            FILE_READ_ATTRIBUTES.0,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
    }
    .map_err(|error| {
        if error.code() == ::windows::core::HRESULT::from_win32(2) {
            unsafe_path()
        } else {
            io_failure()
        }
    })?;
    if handle == INVALID_HANDLE_VALUE {
        return Err(io_failure());
    }
    // SAFETY: the handle is live and owned here; File closes it exactly once.
    let file = unsafe { File::from_raw_handle(handle.0) };
    let identity = identity_of_file(&file)?;
    Ok(identity)
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
///
/// On the native targets every mutation is performed relative to a **directory
/// handle opened at mutation time** by walking the captured chain with no-follow
/// OS calls ([`ValidatedPath::dir_handle`]). Each opened component's identity is
/// compared with the one validation captured, so a component swapped after
/// validation fails closed, and the mutation itself addresses the opened object
/// rather than re-walking the path. The handle is deliberately not cached: a
/// receipt may claim thousands of entries and holding one descriptor each would
/// exhaust the process's descriptor limit.
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

    /// A handle for one sibling of this path below the same anchor.
    ///
    /// The name must be a single normal component: a caller cannot turn this
    /// into a general path constructor, and the sibling inherits the captured
    /// ancestry so a later mutation revalidates the same trusted chain.
    pub(crate) fn sibling(&self, name: &str, kind: PathKind) -> InstallResult<Self> {
        if name.is_empty()
            || name.len() > 255
            || name.contains(['/', '\\'])
            || name.contains(':')
            || name.chars().any(char::is_control)
        {
            return Err(unsafe_path());
        }
        let parent = match self.relative.rfind('/') {
            Some(index) => &self.relative[..index],
            None => "",
        };
        let relative = if parent.is_empty() {
            name.to_owned()
        } else {
            format!("{parent}/{name}")
        };
        Self::build(self.anchor.clone(), relative, kind)
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

    /// Open the captured parent directory chain and return its handle.
    ///
    /// The walk starts at the canonical anchor and opens one component at a time
    /// with no-follow OS calls; every opened directory is compared with the
    /// identity captured during validation. A mutation must be performed through
    /// this handle, never by re-walking the rendered path.
    #[cfg(any(windows, target_os = "macos"))]
    pub(crate) fn dir_handle(&self) -> InstallResult<native::anchor::DirectoryHandle> {
        let identities: Vec<FileIdentity> = self
            .ancestors
            .iter()
            .map(|ancestor| ancestor.identity)
            .collect();
        let components: Vec<&str> = self
            .relative
            .split('/')
            .take(self.relative.split('/').count().saturating_sub(1))
            .collect();
        let handle = native::anchor::open_chain(&self.anchor, &components, &identities)?;
        if Some(handle.identity()) != self.ancestors.last().map(|ancestor| &ancestor.identity) {
            return Err(unsafe_path());
        }
        Ok(handle)
    }

    /// The single leaf component addressed below [`Self::dir_handle`].
    #[cfg(any(windows, target_os = "macos"))]
    pub(crate) fn leaf_name(&self) -> InstallResult<&OsStr> {
        self.rendered.file_name().ok_or_else(unsafe_path)
    }

    /// The identity captured at validation, if the leaf existed.
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    pub(crate) fn captured_identity(&self) -> Option<&FileIdentity> {
        self.target.as_ref()
    }

    /// Revalidate the full ancestry and the leaf.
    pub fn revalidate(&self) -> InstallResult<()> {
        self.revalidate_ancestors()?;
        match self.kind {
            PathKind::File | PathKind::Directory | PathKind::Symlink => {
                let metadata = fs::symlink_metadata(&self.rendered).map_err(|_| unsafe_path())?;
                if self.kind != PathKind::Symlink && is_link_like(&metadata) {
                    return Err(unsafe_path());
                }
                let type_matches = match self.kind {
                    PathKind::File => metadata.is_file(),
                    PathKind::Directory => metadata.is_dir(),
                    // A link's own type, never the type it points at.
                    PathKind::Symlink => metadata.file_type().is_symlink(),
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

    /// Verify the metadata of an **already opened** leaf against this handle.
    ///
    /// This is the identity half of an anchored mutation: the adapter opens the
    /// leaf relative to the captured parent handle with no-follow semantics and
    /// compares what the OS opened with what validation captured. A name swapped
    /// after validation therefore cannot lend its identity to the operation.
    #[cfg_attr(not(any(windows, target_os = "macos")), allow(dead_code))]
    pub(crate) fn verify_open(
        &self,
        metadata: &Metadata,
        identity: &FileIdentity,
    ) -> InstallResult<()> {
        let type_matches = match self.kind {
            PathKind::File => !is_link_like(metadata) && metadata.is_file(),
            PathKind::Directory => !is_link_like(metadata) && metadata.is_dir(),
            PathKind::Symlink => metadata.file_type().is_symlink(),
            PathKind::NewFile | PathKind::NewDirectory => false,
        };
        if !type_matches {
            return Err(unsafe_path());
        }
        match &self.target {
            Some(expected) if expected == identity => Ok(()),
            _ => Err(unsafe_path()),
        }
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
                    if kind != PathKind::Symlink && is_link_like(&metadata) {
                        return Err(unsafe_path());
                    }
                    let type_matches = match kind {
                        PathKind::File => metadata.is_file(),
                        PathKind::Directory => metadata.is_dir(),
                        PathKind::Symlink => metadata.file_type().is_symlink(),
                        PathKind::NewFile | PathKind::NewDirectory => false,
                    };
                    if !type_matches {
                        return Err(unsafe_path());
                    }
                    target = Some(identity_of(&rendered, &metadata)?);
                }
                Err(error) if error.kind() == io::ErrorKind::NotFound => {
                    if matches!(
                        kind,
                        PathKind::File | PathKind::Directory | PathKind::Symlink
                    ) {
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

pub(crate) fn same_path(left: &Path, right: &Path) -> bool {
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

pub(crate) fn is_link_like(metadata: &Metadata) -> bool {
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

    /// A symbolic-link claim bound to the exact bytes of its target path.
    pub fn symlink(path: impl Into<String>, target: &str) -> Self {
        Self {
            path: path.into(),
            kind: OwnedKind::Symlink,
            sha256: Some(Digest::of(target.as_bytes())),
        }
    }
}

/// Whether an owned entry is a file, a directory or a symbolic link.
///
/// A symlink claim is bound to the exact bytes of its target path (its content
/// digest) and is always removed as a link: the target is never followed, so an
/// owned bundle link can never become a delete outside the install root.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum OwnedKind {
    File,
    Directory,
    /// A symbolic link whose target bytes are the owned content.
    Symlink,
}

impl OwnedKind {
    /// Whether entries of this kind are removed *after* every leaf below them.
    pub fn is_directory(self) -> bool {
        matches!(self, Self::Directory)
    }

    /// Whether the owned content is the exact bytes of a link target.
    pub fn is_link(self) -> bool {
        matches!(self, Self::Symlink)
    }

    /// Whether this kind carries a content digest.
    pub fn has_digest(self) -> bool {
        matches!(self, Self::File | Self::Symlink)
    }
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
                OwnedKind::Symlink if claim.sha256.is_none() => return Err(invalid_input()),
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
    /// For a file this transaction may have been writing when the process died:
    /// the exact size a complete write would produce. A shorter file is a prefix
    /// of our own staged bytes and may be removed without a digest match.
    incomplete_size: Option<u64>,
}

impl OwnedEntry {
    /// Turn an untrusted claim into a handle.
    ///
    /// Returns `None` when the claimed path is already absent (missing owned
    /// files are idempotent). A present path whose kind, content or ancestry does
    /// not match the claim is `ForeignState`/`UnsafePath` and is never deleted.
    pub fn from_claim(paths: &InstallPaths, claim: &OwnedClaim) -> InstallResult<Option<Self>> {
        Self::from_claim_allowing_short(paths, claim, None)
    }

    /// Turn a claim the journal marked incomplete into a removable handle.
    ///
    /// Staging writes the bytes sequentially, so a process death mid-write leaves
    /// a strict prefix of the staged file: the path may be removed when its
    /// current size is *smaller* than `expected_size`. An equal-size file is
    /// still held to the exact digest, and a longer file is foreign. The caller
    /// must additionally constrain the path to this transaction's own version
    /// directory.
    pub fn incomplete_file(
        paths: &InstallPaths,
        claim: &OwnedClaim,
        expected_size: u64,
    ) -> InstallResult<Option<Self>> {
        if claim.kind != OwnedKind::File {
            return Err(invalid_input());
        }
        Self::from_claim_allowing_short(paths, claim, Some(expected_size))
    }

    /// Adopt one fixed installer-private artifact for removal.
    ///
    /// Unlike a receipt claim, these are not in `versions/`/`maintenance/`: they
    /// are the installer's own state documents and emptied private directories.
    /// Only the closed set of fixed paths is eligible, so an arbitrary caller
    /// string can never turn cleanup into a delete. The handle captures the full
    /// ancestry and the content digest; [`Platform::remove_owned`] revalidates and
    /// re-hashes immediately before the unlink.
    pub fn private_layout(
        paths: &InstallPaths,
        relative: &str,
        kind: OwnedKind,
    ) -> InstallResult<Option<Self>> {
        let normalized = normalize_relative_path(relative).map_err(|_| unsafe_path())?;
        if !is_private_artifact(&normalized, kind) {
            return Err(unsafe_path());
        }
        let absolute = paths.root().join(Path::new(&normalized));
        match fs::symlink_metadata(&absolute) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(_) => return Err(io_failure()),
            Ok(_) => {}
        }
        let path = match kind {
            OwnedKind::File => ValidatedPath::new(paths, Path::new(&normalized), PathKind::File)?,
            OwnedKind::Symlink => {
                ValidatedPath::new(paths, Path::new(&normalized), PathKind::Symlink)?
            }
            OwnedKind::Directory => {
                ValidatedPath::new(paths, Path::new(&normalized), PathKind::Directory)?
            }
        };
        let claim = OwnedClaim {
            path: normalized,
            kind,
            sha256: match kind {
                OwnedKind::File => Some(hash_owned_file(path.rendered())?),
                OwnedKind::Symlink => Some(link_target_digest(path.rendered())?),
                OwnedKind::Directory => None,
            },
        };
        Ok(Some(Self {
            path,
            claim,
            incomplete_size: None,
        }))
    }

    fn from_claim_allowing_short(
        paths: &InstallPaths,
        claim: &OwnedClaim,
        incomplete_size: Option<u64>,
    ) -> InstallResult<Option<Self>> {
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
            OwnedKind::Symlink => PathKind::Symlink,
            OwnedKind::Directory => PathKind::Directory,
        };
        let path = ValidatedPath::new(paths, Path::new(&normalized), kind)?;
        let mut short = false;
        match claim.kind {
            OwnedKind::File => {
                let expected = claim.sha256.ok_or_else(foreign_state)?;
                let length = fs::symlink_metadata(path.rendered())
                    .map_err(|_| io_failure())?
                    .len();
                if let Some(expected_size) = incomplete_size
                    && length < expected_size
                {
                    short = true;
                } else if hash_owned_file(path.rendered())? != expected {
                    return Err(foreign_state());
                }
            }
            // A link is bound to the exact bytes of its target: the link is never
            // followed, so a redirected target cannot lend a foreign inode ours.
            OwnedKind::Symlink => {
                let expected = claim.sha256.ok_or_else(foreign_state)?;
                if incomplete_size.is_some() || link_target_digest(path.rendered())? != expected {
                    return Err(foreign_state());
                }
            }
            OwnedKind::Directory => {
                if incomplete_size.is_some() {
                    return Err(invalid_input());
                }
            }
        }
        Ok(Some(Self {
            path,
            claim: claim.clone(),
            incomplete_size: if short { incomplete_size } else { None },
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

/// The fixed installer-private files cleanup may remove.
///
/// These are the installer's own state slots, never receipt claims. The list is
/// closed on purpose: cleanup must not become a general delete primitive.
const PRIVATE_LAYOUT_FILES: [&str; 3] = [
    "state/journal.json",
    "state/receipt.json",
    "state/staging/registration.json",
];

/// The fixed installer-private directories cleanup may remove when empty.
const PRIVATE_LAYOUT_DIRECTORIES: [&str; 3] = ["state/staging", "versions", "maintenance"];

/// Whether `normalized` is one of the fixed installer-private layout paths.
fn is_private_artifact(normalized: &str, kind: OwnedKind) -> bool {
    let same = |candidate: &str| {
        if cfg!(windows) {
            normalized.eq_ignore_ascii_case(candidate)
        } else {
            normalized == candidate
        }
    };
    match kind {
        OwnedKind::File => PRIVATE_LAYOUT_FILES.iter().any(|candidate| same(candidate)),
        OwnedKind::Directory => PRIVATE_LAYOUT_DIRECTORIES
            .iter()
            .any(|candidate| same(candidate)),
        // The private layout never contains a link of its own; a link at a fixed
        // slot is foreign content and is preserved.
        OwnedKind::Symlink => false,
    }
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
            || carrier.len() > MAX_CARRIER_DOCUMENT_BYTES
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

/// OS-verified signer/assessment evidence for the **host** executable or bundle.
///
/// Distinct from [`TrustProof`] on purpose: it proves nothing about policy and
/// carries no carrier bytes. It answers one question - did the OS assess this
/// host code (its signature, publisher, resource seal and execution assessment)
/// as acceptable - and the engine then checks the signer against the
/// authenticated publisher policy.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostAssessment {
    signer: String,
    assessment: String,
}

impl HostAssessment {
    /// Construct host evidence from the OS signer identity and verdict.
    pub fn new(signer: impl Into<String>, assessment: impl Into<String>) -> InstallResult<Self> {
        let signer = signer.into();
        let assessment = assessment.into();
        if signer.is_empty()
            || signer.len() > MAX_PUBLISHER_BYTES
            || signer.chars().any(char::is_control)
            || assessment.is_empty()
            || assessment.len() > MAX_PUBLISHER_BYTES
            || assessment.chars().any(char::is_control)
        {
            return Err(invalid_input());
        }
        Ok(Self { signer, assessment })
    }

    /// The OS-verified signer/publisher identity of the host code.
    pub fn signer(&self) -> &str {
        &self.signer
    }

    /// The OS assessment verdict for the host code.
    pub fn assessment(&self) -> &str {
        &self.assessment
    }
}

/// Require the OS to assess the host code under `root` and the engine to accept
/// the result.
///
/// `root` is the directory the host lives in - the package `payload/` directory
/// before install, the immutable version directory after it - and `executable`
/// the normalized relative path of the one executable the manifest will select.
/// The adapter assesses that file (and, on macOS, the enclosing bundle with all
/// nested code). Denial and unavailability are returned unchanged; an assessment
/// that is not this target's accepted verdict, or a signer the authenticated
/// publisher policy does not list, is `TrustDenied`. Nothing is mutated.
pub fn assess_host(
    platform: &impl Platform,
    root: &Path,
    executable: &str,
    expected: &ReleaseDescriptor,
    publishers: &[String],
) -> InstallResult<()> {
    let assessment = platform.verify_host(root, executable, expected)?;
    if !is_accepted_assessment(assessment.assessment(), expected.target) {
        return Err(trust_denied());
    }
    if !publishers
        .iter()
        .any(|publisher| publisher == assessment.signer())
    {
        return Err(trust_denied());
    }
    Ok(())
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

/// The signed policy-carrier artifact name for a release target.
///
/// The same name is used inside an authenticated package and inside the retained
/// immutable version directory, so `verify_retained` can hand the version
/// directory to the identical OS verification path a fresh package uses.
pub const fn package_carrier_name(target: Target) -> &'static str {
    match target {
        Target::WindowsX64 => PACKAGE_WINDOWS_CARRIER,
        Target::MacosArm64 => PACKAGE_MACOS_CARRIER,
    }
}

/// Upper bound on the entries a signed carrier artifact may install.
pub const MAX_CARRIER_ENTRIES: usize = 4096;
/// Upper bound on the bytes a signed carrier artifact may install (64 MiB).
pub const MAX_CARRIER_BYTES: usize = 64 * 1024 * 1024;

/// One entry [`Platform::install_carrier`] creates for a signed carrier artifact.
///
/// The engine journals every entry before the first mutation and claims it in the
/// committed receipt, so the artifact is owned - and later verifiable in place -
/// without the engine ever walking or copying its bytes itself.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CarrierEntry {
    /// `/`-joined path below the artifact root; empty for the root itself.
    relative: String,
    kind: OwnedKind,
    size: u64,
    sha256: Option<Digest>,
    /// Exact link target for a [`OwnedKind::Symlink`] entry.
    target: Option<String>,
}

impl CarrierEntry {
    /// Construct one validated artifact entry.
    ///
    /// A directory carries no digest; a file must carry its exact SHA-256 and
    /// size. Relative paths use the same policy as every other owned path.
    pub fn new(
        relative: &str,
        kind: OwnedKind,
        size: u64,
        sha256: Option<Digest>,
    ) -> InstallResult<Self> {
        if kind == OwnedKind::Symlink {
            // A link's content is its target: use [`CarrierEntry::link`].
            return Err(invalid_input());
        }
        if relative.len() > MAX_PATH_BYTES {
            return Err(unsafe_path());
        }
        if !relative.is_empty() {
            let normalized = normalize_relative_path(relative).map_err(|_| unsafe_path())?;
            if normalized != relative {
                return Err(unsafe_path());
            }
        }
        match kind {
            OwnedKind::File if sha256.is_none() => return Err(invalid_input()),
            OwnedKind::Directory if sha256.is_some() => return Err(invalid_input()),
            _ => {}
        }
        Ok(Self {
            relative: relative.to_owned(),
            kind,
            size,
            sha256,
            target: None,
        })
    }

    /// Construct one validated symbolic-link entry.
    ///
    /// The link content is its target path, so the entry's digest and size are
    /// derived from the target bytes. `target` must already have been proven to
    /// stay inside the artifact root (see [`confine_link_target`]).
    pub fn link(relative: &str, target: &str) -> InstallResult<Self> {
        if relative.is_empty() || target.is_empty() || target.len() > MAX_PATH_BYTES {
            return Err(unsafe_path());
        }
        let normalized = normalize_relative_path(relative).map_err(|_| unsafe_path())?;
        if normalized != relative {
            return Err(unsafe_path());
        }
        Ok(Self {
            relative: relative.to_owned(),
            kind: OwnedKind::Symlink,
            size: target.len() as u64,
            sha256: Some(Digest::of(target.as_bytes())),
            target: Some(target.to_owned()),
        })
    }

    /// The `/`-joined path below the artifact root (empty for the root).
    pub fn relative(&self) -> &str {
        &self.relative
    }

    /// The entry kind.
    pub fn kind(&self) -> OwnedKind {
        self.kind
    }

    /// The file size (zero for directories, the target length for links).
    pub fn size(&self) -> u64 {
        self.size
    }

    /// The content digest (`None` for directories).
    pub fn sha256(&self) -> Option<Digest> {
        self.sha256
    }

    /// The exact target of a symbolic-link entry.
    pub fn target(&self) -> Option<&str> {
        self.target.as_deref()
    }
}
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

    /// Open the source once and return its bytes, bound to the authenticated
    /// identity.
    ///
    /// The handle is inspected first (a linked, non-regular or wrong-sized file
    /// is refused before any buffer exists), at most `size + 1` bytes are read
    /// through that same handle and the digest is compared before the bytes are
    /// returned. A source enlarged or replaced after authentication therefore
    /// can neither exhaust memory nor reach staging: it is `ForeignState`.
    pub fn read_verified(&self) -> InstallResult<Vec<u8>> {
        let mut file = match open_no_follow(&self.path) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Err(foreign_state()),
            Err(_) => return Err(io_failure()),
        };
        let metadata = file.metadata().map_err(|_| io_failure())?;
        if !metadata.is_file() {
            return Err(unsafe_path());
        }
        if metadata.len() != self.size || self.size > MAX_INVENTORY_BYTES {
            return Err(foreign_state());
        }
        let bytes = match read_bounded(&mut file, self.size, Some(metadata.len())) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == io::ErrorKind::InvalidData => {
                return Err(foreign_state());
            }
            Err(_) => return Err(io_failure()),
        };
        if bytes.len() as u64 != self.size || Digest::of(&bytes) != self.sha256 {
            return Err(foreign_state());
        }
        Ok(bytes)
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
        if metadata.len() != self.size {
            return Err(foreign_state());
        }
        // Bounded by the authenticated size, so growth after the check above cannot
        // extend the work; any length other than the size is a mismatch.
        let hashed = hash_file_bounded(&self.path, self.size)?;
        if hashed.len != self.size || hashed.digest != self.sha256 {
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
    /// Exact bytes of `inventory.json`: the unsigned canonical identity, whose
    /// digest is the descriptor's `payload_id`.
    canonical_inventory: Vec<u8>,
    /// Exact bytes of `sealed-inventory.json` when signing changed any file; its
    /// digest is the one the OS-authenticated carrier asserts.
    sealed_inventory: Option<Vec<u8>>,
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
    ///
    /// This is the digest of the *unsigned canonical* inventory. It is stable
    /// across code signing; the bytes actually installed are described by
    /// [`AuthenticatedPackage::payload`] (and the sealed inventory, when any).
    pub fn payload_id(&self) -> Digest {
        self.descriptor.payload_id
    }

    /// The exact canonical (unsigned) inventory document bytes.
    pub fn canonical_inventory(&self) -> &[u8] {
        &self.canonical_inventory
    }

    /// The exact sealed signed-byte inventory document, when signing changed any
    /// file. Its digest is asserted by the OS-authenticated carrier.
    pub fn sealed_inventory(&self) -> Option<&[u8]> {
        self.sealed_inventory.as_deref()
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
    let contents = authenticated_descriptor(&proof, expected)?;
    let (payload, canonical_inventory, sealed_inventory) =
        authenticated_payload(&source, &contents.descriptor, contents.sealed_inventory)?;

    // The carrier's signature authenticates the policy, not the code that will
    // run. The one executable the manifest will select is assessed by the OS in
    // its package form and by a publisher the authenticated policy lists.
    let mut executables = payload.iter().filter(|source| source.executable());
    if let (Some(host), None) = (executables.next(), executables.next()) {
        assess_host(
            platform,
            &source.path().join(PACKAGE_PAYLOAD_DIR),
            host.relative(),
            &contents.descriptor,
            &contents.descriptor.policy.publishers,
        )?;
    }

    Ok(AuthenticatedPackage {
        source,
        proof,
        descriptor: contents.descriptor,
        payload,
        canonical_inventory,
        sealed_inventory,
    })
}

fn authenticated_descriptor(
    proof: &TrustProof,
    expected: &ReleaseDescriptor,
) -> InstallResult<CarrierContents> {
    // The verdict must be one this engine accepts *for this release target*: a
    // macOS Gatekeeper proof cannot authenticate a Windows descriptor.
    if !is_accepted_assessment(proof.assessment(), expected.target) {
        return Err(trust_denied());
    }
    let contents = read_carrier_contents(proof.carrier()).map_err(|_| trust_denied())?;
    if &contents.descriptor != expected {
        return Err(trust_denied());
    }
    if !contents
        .descriptor
        .policy
        .publishers
        .iter()
        .any(|publisher| publisher == proof.signer())
    {
        return Err(trust_denied());
    }
    Ok(contents)
}

/// Read the canonical inventory (and the sealed one, when the carrier asserts it)
/// and authenticate the payload tree against the *installed-byte* inventory.
///
/// Two identities are kept apart on purpose:
///
/// * `inventory.json` is the **unsigned canonical** inventory. Its digest must be
///   the descriptor's `payload_id`; the descriptor is signature-covered and was
///   fixed before signing.
/// * `sealed-inventory.json` lists the bytes actually shipped. It exists exactly
///   when the carrier asserts a sealed digest, and then hashes to that digest.
///   Without a sealed digest the shipped bytes ARE the canonical inventory.
///
/// The payload tree is verified (and must be closed) against the shipped bytes;
/// [`verify_correspondence`] ties the two inventories together.
fn authenticated_payload(
    source: &PackageSource,
    descriptor: &ReleaseDescriptor,
    sealed_digest: Option<Digest>,
) -> InstallResult<AuthenticatedPayload> {
    let payload_root = source.path().join(PACKAGE_PAYLOAD_DIR);
    let canonical_bytes = read_package_document(&source.path().join(PACKAGE_INVENTORY_FILE))?
        .ok_or_else(invalid_package)?;
    // The descriptor (itself signature-covered) authenticates the exact document
    // bytes; no caller argument can substitute another inventory.
    if Digest::of(&canonical_bytes) != descriptor.payload_id {
        return Err(trust_denied());
    }
    let canonical = parse_inventory(&canonical_bytes)?;

    let sealed_path = source.path().join(SEALED_INVENTORY_FILE);
    let sealed_bytes = read_package_document(&sealed_path)?;
    let (entries, sealed_bytes) = match (sealed_digest, sealed_bytes) {
        (None, None) => (canonical.clone(), None),
        // A sealed document nothing authenticates, or an asserted digest with no
        // document, is never interpreted.
        (None, Some(_)) | (Some(_), None) => return Err(trust_denied()),
        (Some(asserted), Some(bytes)) => {
            if Digest::of(&bytes) != asserted {
                return Err(trust_denied());
            }
            let sealed = parse_inventory(&bytes)?;
            verify_correspondence(&canonical, &sealed, |relative| {
                entry_is_signable_image(&payload_root, relative)
            })
            .map_err(|_| trust_denied())?;
            (sealed, Some(bytes))
        }
    };

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
    Ok((payload, canonical_bytes, sealed_bytes))
}

/// Payload sources, canonical inventory bytes, optional sealed inventory bytes.
type AuthenticatedPayload = (Vec<AuthenticatedSource>, Vec<u8>, Option<Vec<u8>>);

/// Read one bounded package document; absent is `None`, oversize is invalid.
fn read_package_document(path: &Path) -> InstallResult<Option<Vec<u8>>> {
    match read_no_follow(path, MAX_DOCUMENT_BYTES as u64) {
        Ok(bytes) => Ok(Some(bytes)),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        // Longer than the document bound: rejected from the handle's length (or
        // from the limit-plus-one read), never buffered.
        Err(error) if error.kind() == io::ErrorKind::InvalidData => Err(invalid_package()),
        Err(_) => Err(io_failure()),
    }
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
    /// Apply and verify the launch permission of one staged payload file.
    ///
    /// `path` is the handle of a file this transaction just wrote (adopted as
    /// [`PathKind::File`] so its identity is captured). The implementation opens
    /// the leaf without following links, verifies the opened object against the
    /// captured identity, sets owner-only permissions through that handle
    /// (`0700` when `executable`, `0600` otherwise on Unix), flushes, and reads
    /// the final permission back from the handle. On Windows launch is decided by
    /// the user-only ACL the file was created with, so the call verifies the
    /// object and changes nothing.
    fn apply_launch_permissions(
        &self,
        path: &ValidatedPath,
        executable: bool,
    ) -> InstallResult<()> {
        apply_launch_permissions_checked(path, executable)
    }
    /// Re-verify (without changing anything) that `path` is launchable exactly as
    /// the authenticated inventory says. Run immediately before the activation
    /// mutation, so a permission lost between staging and activation fails closed.
    fn check_launch_permissions(
        &self,
        path: &ValidatedPath,
        executable: bool,
    ) -> InstallResult<()> {
        check_launch_permissions_checked(path, executable)
    }
    /// Verify the OS signature, publisher and execution assessment of the **host**
    /// code, not the policy carrier.
    ///
    /// `root` is the directory holding the host (`payload/` of a package, or the
    /// immutable version directory of an installed release) and `executable` the
    /// normalized relative path of the executable the manifest selects. On macOS
    /// the adapter must assess the enclosing `.app` bundle with all nested code
    /// and its resource seal; on Windows the Authenticode signature, chain and
    /// revocation state of the executable. An explicit OS denial is `TrustDenied`,
    /// an assessment that cannot be formed is `TrustUnavailable`; neither may be
    /// reported as success. The adapter performs no filesystem mutation.
    ///
    /// The default fails closed so a platform without a real host assessment can
    /// never authorize an installation.
    fn verify_host(
        &self,
        root: &Path,
        executable: &str,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<HostAssessment> {
        let _ = (root, executable, expected);
        Err(InstallFailure::new(FailureCode::TrustUnavailable))
    }
    /// Replace one owned file's bytes without writing through its inode.
    ///
    /// Unlike [`Platform::write_durable`] on an existing path, this stages the
    /// bytes into a fresh sibling file with `create_new`, flushes and reads them
    /// back, then renames the sibling over the target and flushes the parent. A
    /// target that was replaced by a hard link therefore cannot rewrite the
    /// linked data: only the destination directory entry is replaced. A missing
    /// target is created directly (still `create_new`, never an overwrite).
    fn replace_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        replace_durable_checked(path, bytes)
    }
    /// Atomically replace one installer state document.
    ///
    /// The bytes are staged into a sibling file on the same volume, flushed and
    /// read back, then renamed onto the fixed state path and the parent directory
    /// is flushed. A reader (and a crash) therefore observes the previous
    /// document or the complete new one, never a partially written envelope. An
    /// empty byte slice durably releases the document.
    fn write_state(
        &self,
        paths: &InstallPaths,
        document: StateDocument,
        bytes: &[u8],
    ) -> InstallResult<()> {
        write_state_atomic(paths, document, bytes, None, false, false).map(|_| ())
    }
    /// Read one installer state document exactly, or `None` when absent/empty.
    fn read_state(
        &self,
        paths: &InstallPaths,
        document: StateDocument,
    ) -> InstallResult<Option<Vec<u8>>> {
        read_state_document(paths, document)
    }
    /// Move an uninterpretable state document to its fixed quarantine name.
    fn quarantine_state(&self, paths: &InstallPaths, document: StateDocument) -> InstallResult<()> {
        quarantine_state_document(paths, document)
    }
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
    ///
    /// A native Windows adapter populates the returned [`Registration`] with the
    /// HKCU key and previous default value it captured before publication (see
    /// [`WindowsPreimage`]), so the transaction can journal a complete preimage.
    /// The portable adapter has no registry and leaves it `None`.
    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>>;
    /// Publish the manifest file that selects the executable (step one).
    ///
    /// The manifest must name this channel and an executable inside an owned
    /// `versions/` or `maintenance/` directory. A live registration may be
    /// replaced only when its exact bytes equal `value` (idempotent republish)
    /// or it is a previously published owned manifest (an upgrade). Any other
    /// live registration yields `ForeignState` and is left byte-for-byte
    /// intact; this layer never assumes the caller established ownership.
    ///
    /// Publication is split in two ordered calls so each is a separately
    /// journaled step: this call replaces the manifest file, and
    /// [`Platform::publish_registration`] then publishes the OS registration
    /// (HKCU on Windows). The transaction journals between them, so a crash leaves
    /// either the old manifest, the new manifest with the old registry value, or
    /// both new - never bytes that disagree with the registry.
    fn publish_manifest(&self, paths: &InstallPaths, value: &Registration) -> InstallResult<()> {
        publish_manifest_checked(paths, value)
    }
    /// Publish the OS registration for an already-published manifest (step two).
    ///
    /// On Windows this writes the fixed HKCU value that points Chrome at the
    /// manifest; the portable adapter has no registry and only verifies that the
    /// manifest selected by `value` is already live, so the ordering is modelled
    /// on every host. The owned-preimage refusal is applied here as well: a live
    /// manifest this installer does not own is `ForeignState` and nothing is
    /// written.
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
    /// Verify the OS signer/assessment of a **retained** signed carrier artifact.
    ///
    /// `source` is the immutable version directory. It holds the exact signed
    /// carrier artifact (`carrier.exe` on Windows, `carrier.app` on macOS) that
    /// [`Platform::install_carrier`] installed there in place, next to the payload
    /// and the retained metadata. The adapter must apply the *same* OS
    /// signature/Gatekeeper path as [`Platform::verify_trust`] to that artifact
    /// and never trust the stored metadata's claims: a rollback accepts the
    /// returned signer/assessment only. `source` is never a copy made by the
    /// engine and never the original download.
    ///
    /// The default fails closed (`TrustUnavailable`) so a platform without a real
    /// retained-carrier verification can never authorize a rollback.
    fn verify_retained(
        &self,
        source: &PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        let _ = (source, expected);
        Err(InstallFailure::new(FailureCode::TrustUnavailable))
    }
    /// The exact entries [`Platform::install_carrier`] will create for `source`.
    ///
    /// `source` is the authenticated carrier artifact itself (`carrier.exe` as a
    /// file, `carrier.app` as a directory), never the package root. The engine
    /// journals every returned path before the first mutation and claims it in
    /// the receipt, so the list must be complete and deterministic. This call is
    /// read-only; it performs no filesystem mutation.
    ///
    /// The default fails closed: no portable adapter may describe a carrier it
    /// cannot also install with its signature-bearing metadata intact.
    fn carrier_entries(&self, source: &PackageSource) -> InstallResult<Vec<CarrierEntry>> {
        let _ = source;
        Err(InstallFailure::new(FailureCode::UnsupportedTarget))
    }
    /// Install the authenticated carrier artifact at `destination`.
    ///
    /// `destination` is the absent artifact root inside the immutable version
    /// directory (`carrier.exe` as a `NewFile`, `carrier.app` as a
    /// `NewDirectory`). The implementation must preserve everything the OS
    /// signature assessment covers - extended attributes, resource forks and
    /// link targets on macOS, the Authenticode image plus file attributes and
    /// timestamps on Windows - so the installed artifact still verifies in place
    /// later.
    ///
    /// The default fails closed ([`FailureCode::UnsupportedTarget`]): a portable
    /// byte copy would authenticate at its source and then fail the retained
    /// verification, so only a native adapter may install a carrier. Task 8
    /// implements it with an exclusive anchored create plus a bounded write and
    /// `SetFileInformationByHandle` on Windows, and `copyfile(3)` on macOS.
    fn install_carrier(
        &self,
        source: &PackageSource,
        destination: &ValidatedPath,
    ) -> InstallResult<()> {
        let _ = (source, destination);
        Err(InstallFailure::new(FailureCode::UnsupportedTarget))
    }
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
        publish_registry_checked(paths, value)
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

/// One crossing of a crash point, as recorded by an armed probe.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BoundaryCrossing {
    /// 1-based ordinal of the platform mutation attempt that crossed it.
    pub mutation: usize,
    /// The platform call that was in progress.
    pub call: PlatformCall,
    /// The durable primitive containing the crash point (e.g. `write_durable`).
    pub primitive: &'static str,
    /// The named point inside that primitive (e.g. `written, not flushed`).
    pub point: &'static str,
}

/// The armed probe: where crossings are logged, which crossing dies, and the call
/// in progress.
struct Probe {
    log: Arc<Mutex<Vec<BoundaryCrossing>>>,
    panic_at: Option<usize>,
    mutation: usize,
    call: PlatformCall,
}

thread_local! {
    /// The armed crash-boundary probe of the [`FakePlatform`] call in progress.
    static PROBE: std::cell::RefCell<Option<Probe>> = const { std::cell::RefCell::new(None) };
}

/// A named point inside a durable primitive at which a process could die.
///
/// A no-op unless a [`FakePlatform`] armed the probe for the current call. When
/// armed, every crossing is logged (primitive and point by name, so a test can
/// compare the log with an explicit inventory instead of a count the
/// implementation produced), and the crossing numbered `panic_at_boundary` panics:
/// a simulated process death *inside* a file creation, write, flush, rename,
/// directory flush or removal, with no caller cleanup running.
fn crash_point(primitive: &'static str, point: &'static str) {
    PROBE.with(|probe| {
        if let Some(probe) = probe.borrow().as_ref() {
            let crossing = {
                let mut log = probe
                    .log
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                log.push(BoundaryCrossing {
                    mutation: probe.mutation,
                    call: probe.call,
                    primitive,
                    point,
                });
                log.len()
            };
            if probe.panic_at == Some(crossing) {
                panic!("injected process death at durable boundary {crossing}");
            }
        }
    });
}

/// Arms the probe for the duration of one platform call, disarming on drop
/// (including while a simulated death unwinds).
struct ProbeGuard;

impl ProbeGuard {
    fn arm(
        log: &Arc<Mutex<Vec<BoundaryCrossing>>>,
        panic_at: Option<usize>,
        mutation: usize,
        call: PlatformCall,
    ) -> Self {
        PROBE.with(|probe| {
            *probe.borrow_mut() = Some(Probe {
                log: Arc::clone(log),
                panic_at,
                mutation,
                call,
            });
        });
        Self
    }
}

impl Drop for ProbeGuard {
    fn drop(&mut self) {
        PROBE.with(|probe| *probe.borrow_mut() = None);
    }
}

/// A process table of a machine on which nothing is running.
struct NoProcesses;

impl ProcessTable for NoProcesses {
    fn observe(&self, _paths: &InstallPaths) -> io::Result<Vec<ProcessObservation>> {
        Ok(Vec::new())
    }

    fn recheck(&self, _paths: &InstallPaths, _pid: u32) -> io::Result<Option<ProcessObservation>> {
        Ok(None)
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
    /// Proofs for installed carriers, one per release. A real OS verifies the carrier
    /// artifact it is handed, so `verify_retained` returns the proof whose carrier
    /// equals the carrier installed in the version directory; `trust` is the fallback
    /// when none matches (the single-release case).
    pub trust_by_carrier: Vec<TrustProof>,
    /// Force `verify_trust` to report an explicit OS denial/revocation.
    pub trust_denied: bool,
    /// Simulate a process death at the N-th state mutation (1-based).
    ///
    /// Unlike `fail_mutation_at` this panics instead of returning an error, so
    /// no caller error path or cleanup runs: the transaction is left exactly as
    /// a killed installer would leave it.
    pub panic_at_mutation: Option<usize>,
    /// Simulate a process death *during* the N-th durable write (1-based).
    ///
    /// The first half of the bytes is written and flushed, then the process dies.
    /// For a payload file that leaves a short file; for an atomic state document
    /// it leaves the previous document and a torn staging sibling.
    pub tear_at_mutation: Option<usize>,
    /// Fail the N-th state-document write *after* its rename succeeded (1-based).
    ///
    /// Models a durable replacement whose parent flush failed: the new bytes are
    /// live, but the call reports an error.
    pub fail_flush_after_state_write_at: Option<usize>,
    /// Fail the N-th state-document read (1-based); read faults are modelable
    /// because every state read goes through the platform boundary.
    pub fail_state_read_at: Option<usize>,
    /// Make `apply_launch_permissions` a no-op, as a platform that failed to
    /// carry the executable bit would: the pre-activation check must then refuse.
    pub skip_launch_permissions: bool,
    /// Simulate a process death at the N-th durable *boundary* (1-based), counted
    /// inside the platform primitives: after a file is created, after its bytes
    /// are written, after the flush, after the read-back, around every rename and
    /// directory flush, around every removal. Unlike `panic_at_mutation`, which
    /// dies before a whole platform call, this dies in the middle of one.
    pub panic_at_boundary: Option<usize>,
    /// Make `inspect_hosts` run the real owned-tree classification
    /// ([`inspect_host_processes`]) over a machine with no running process,
    /// instead of returning `host_state`. Install, rollback and cleanup then pass
    /// or fail on what is actually installed below `versions/` and
    /// `maintenance/`.
    pub inspect_owned_tree: bool,
    /// Host assessment returned by `verify_host`. `None` derives it from the
    /// injected `trust` proof (the host is signed like the carrier).
    pub host: Option<HostFault>,
}

/// What an injected `verify_host` reports.
#[derive(Debug, Clone)]
pub enum HostFault {
    /// The OS denies the host code (unsigned, revoked, bad seal, wrong signer).
    Denied,
    /// The OS cannot form an assessment (offline revocation data, helper failure).
    Unavailable,
    /// The OS assesses the host with this signer and verdict.
    Assessed { signer: String, assessment: String },
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
            trust_by_carrier: Vec::new(),
            trust_denied: false,
            panic_at_mutation: None,
            tear_at_mutation: None,
            fail_flush_after_state_write_at: None,
            fail_state_read_at: None,
            skip_launch_permissions: false,
            inspect_owned_tree: false,
            panic_at_boundary: None,
            host: None,
        }
    }
}

/// One call observed by [`FakePlatform`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PlatformCall {
    Lock,
    WriteDurable,
    WriteStateDocument,
    ReadStateDocument,
    QuarantineStateDocument,
    CreateDirectory,
    ReplaceManifest,
    ReadRegistration,
    PublishManifest,
    PublishRegistration,
    RemoveRegistration,
    InspectHosts,
    VerifyTrust,
    VerifyRetained,
    VerifyHost,
    CarrierEntries,
    InstallCarrier,
    SetLaunchPermissions,
    CheckLaunchPermissions,
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
                | Self::WriteStateDocument
                | Self::QuarantineStateDocument
                | Self::CreateDirectory
                | Self::InstallCarrier
                | Self::SetLaunchPermissions
                | Self::ReplaceManifest
                | Self::PublishManifest
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
    state_writes: usize,
    state_reads: usize,
    published_preimages: Vec<Option<WindowsPreimage>>,
    retained_verifications: Vec<PathBuf>,
    host_verifications: Vec<(PathBuf, String)>,
}

/// A fault-injectable [`Platform`] over the portable primitives.
#[derive(Debug)]
pub struct FakePlatform {
    faults: Faults,
    state: Mutex<FakeState>,
    boundaries: Arc<Mutex<Vec<BoundaryCrossing>>>,
}

impl FakePlatform {
    pub fn new() -> Self {
        Self::with_faults(Faults::default())
    }

    pub fn with_faults(faults: Faults) -> Self {
        Self {
            faults,
            state: Mutex::new(FakeState::default()),
            boundaries: Arc::new(Mutex::new(Vec::new())),
        }
    }

    /// How many durable boundaries the primitives crossed so far.
    pub fn boundary_count(&self) -> usize {
        self.boundary_log().len()
    }

    /// Every named crash point crossed so far, in order.
    pub fn boundary_log(&self) -> Vec<BoundaryCrossing> {
        self.boundaries
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone()
    }

    /// Run one platform call with the crash-boundary probe armed.
    ///
    /// Called right after `begin_mutation`, so the last recorded call and the
    /// mutation counter name the call whose primitive is about to run.
    fn probed<R>(&self, call: impl FnOnce() -> R) -> R {
        let (mutation, current) = {
            let state = self.fake_state();
            (state.mutations, state.calls.last().copied())
        };
        let current = current.unwrap_or(PlatformCall::Lock);
        let _armed = ProbeGuard::arm(
            &self.boundaries,
            self.faults.panic_at_boundary,
            mutation,
            current,
        );
        call()
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

    /// The Windows preimage carried by every registration publication call.
    pub fn published_preimages(&self) -> Vec<Option<WindowsPreimage>> {
        self.fake_state().published_preimages.clone()
    }

    /// The `(root, executable)` pairs handed to `verify_host`, in order.
    pub fn host_verifications(&self) -> Vec<(PathBuf, String)> {
        self.fake_state().host_verifications.clone()
    }

    /// The version directories handed to `verify_retained`, in order.
    pub fn retained_verifications(&self) -> Vec<PathBuf> {
        self.fake_state().retained_verifications.clone()
    }

    fn fake_state(&self) -> MutexGuard<'_, FakeState> {
        self.state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn observe(&self, call: PlatformCall) {
        self.fake_state().calls.push(call);
    }

    fn begin_mutation(&self, call: PlatformCall) -> InstallResult<usize> {
        let mut state = self.fake_state();
        state.calls.push(call);
        state.mutations += 1;
        let mutation = state.mutations;
        if self.faults.fail_mutation_at == Some(mutation) {
            return Err(io_failure());
        }
        if self.faults.panic_at_mutation == Some(mutation) {
            // A simulated process death: the guard is dropped by unwinding, the
            // mutex is left poisoned (the accessor recovers it) and no caller
            // error path or cleanup runs.
            panic!("injected process death at state mutation {mutation}");
        }
        Ok(mutation)
    }

    /// Whether this mutation is the one that dies after a partial write.
    fn tears_at(&self, mutation: usize) -> bool {
        self.faults.tear_at_mutation == Some(mutation)
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
        let mutation = self.begin_mutation(PlatformCall::WriteDurable)?;
        if self.tears_at(mutation) {
            let _ = write_partial(path, bytes);
            panic!("injected partial payload write at state mutation {mutation}");
        }
        let short = self.probed(|| {
            write_durable_checked(
                path,
                bytes,
                self.faults.short_write_limit,
                self.faults.corrupt_readback,
            )
        })?;
        self.fake_state().short_writes += short;
        Ok(())
    }

    fn replace_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::WriteDurable)?;
        self.probed(|| replace_durable_checked(path, bytes))
    }

    fn write_state(
        &self,
        paths: &InstallPaths,
        document: StateDocument,
        bytes: &[u8],
    ) -> InstallResult<()> {
        let mutation = self.begin_mutation(PlatformCall::WriteStateDocument)?;
        let write_index = {
            let mut state = self.fake_state();
            state.state_writes += 1;
            state.state_writes
        };
        if self.tears_at(mutation) {
            // A non-atomic writer would have torn the live document; the atomic
            // boundary only leaves a torn staging sibling. The panic models the
            // process death either way.
            let _ = tear_state_staging(paths, document, bytes);
            panic!("injected partial state write at state mutation {mutation}");
        }
        let fail_after_rename = self.faults.fail_flush_after_state_write_at == Some(write_index);
        let short = self.probed(|| {
            write_state_atomic(
                paths,
                document,
                bytes,
                self.faults.short_write_limit,
                self.faults.corrupt_readback,
                fail_after_rename,
            )
        })?;
        self.fake_state().short_writes += short;
        Ok(())
    }

    fn read_state(
        &self,
        paths: &InstallPaths,
        document: StateDocument,
    ) -> InstallResult<Option<Vec<u8>>> {
        self.observe(PlatformCall::ReadStateDocument);
        let read_index = {
            let mut state = self.fake_state();
            state.state_reads += 1;
            state.state_reads
        };
        if self.faults.fail_state_read_at == Some(read_index) {
            return Err(io_failure());
        }
        read_state_document(paths, document)
    }

    fn quarantine_state(&self, paths: &InstallPaths, document: StateDocument) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::QuarantineStateDocument)?;
        self.probed(|| quarantine_state_document(paths, document))
    }

    fn create_directory(&self, path: &ValidatedPath) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::CreateDirectory)?;
        self.probed(|| create_private_directory(path))
    }

    fn replace_manifest(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::ReplaceManifest)?;
        self.probed(|| replace_manifest_checked(staged, live))
    }

    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>> {
        self.observe(PlatformCall::ReadRegistration);
        read_registration_from(paths.registration_path())
    }

    fn publish_manifest(&self, paths: &InstallPaths, value: &Registration) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::PublishManifest)?;
        self.probed(|| publish_manifest_checked(paths, value))
    }

    fn publish_registration(
        &self,
        paths: &InstallPaths,
        value: &Registration,
    ) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::PublishRegistration)?;
        self.fake_state()
            .published_preimages
            .push(value.windows_preimage().cloned());
        publish_registry_checked(paths, value)
    }

    fn remove_registration(
        &self,
        paths: &InstallPaths,
        expected: &Registration,
    ) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::RemoveRegistration)?;
        self.probed(|| remove_registration_checked(paths, expected))
    }

    fn inspect_hosts(&self, paths: &InstallPaths) -> InstallResult<HostState> {
        self.observe(PlatformCall::InspectHosts);
        if self.faults.inspect_owned_tree {
            return Ok(inspect_host_processes(paths, &NoProcesses));
        }
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

    fn verify_retained(
        &self,
        source: &PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        self.observe(PlatformCall::VerifyRetained);
        self.fake_state()
            .retained_verifications
            .push(source.path().to_path_buf());
        // The retained artifact must actually have been preserved: the fake
        // cannot assess a signature, but it can refuse a version directory that
        // never received the signed carrier the native path would verify.
        let carrier = source.path().join(package_carrier_name(expected.target));
        let present = match expected.target {
            Target::WindowsX64 => fs::symlink_metadata(&carrier).is_ok_and(|m| m.is_file()),
            Target::MacosArm64 => fs::symlink_metadata(&carrier).is_ok_and(|m| m.is_dir()),
        };
        if !present {
            return Err(InstallFailure::new(FailureCode::InvalidPackage));
        }
        if self.faults.trust_denied {
            return Err(InstallFailure::new(FailureCode::TrustDenied));
        }
        if let Ok(installed) = fs::read(&carrier)
            && let Some(proof) = self
                .faults
                .trust_by_carrier
                .iter()
                .find(|proof| proof.carrier() == installed.as_slice())
        {
            return Ok(proof.clone());
        }
        self.faults
            .trust
            .clone()
            .ok_or_else(|| InstallFailure::new(FailureCode::TrustUnavailable))
    }

    fn apply_launch_permissions(
        &self,
        path: &ValidatedPath,
        executable: bool,
    ) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::SetLaunchPermissions)?;
        if self.faults.skip_launch_permissions {
            return Ok(());
        }
        self.probed(|| apply_launch_permissions_checked(path, executable))
    }

    fn check_launch_permissions(
        &self,
        path: &ValidatedPath,
        executable: bool,
    ) -> InstallResult<()> {
        self.observe(PlatformCall::CheckLaunchPermissions);
        check_launch_permissions_checked(path, executable)
    }

    fn verify_host(
        &self,
        root: &Path,
        executable: &str,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<HostAssessment> {
        self.observe(PlatformCall::VerifyHost);
        self.fake_state()
            .host_verifications
            .push((root.to_path_buf(), executable.to_owned()));
        if self.faults.trust_denied {
            return Err(InstallFailure::new(FailureCode::TrustDenied));
        }
        match &self.faults.host {
            Some(HostFault::Denied) => Err(InstallFailure::new(FailureCode::TrustDenied)),
            Some(HostFault::Unavailable) => Err(InstallFailure::new(FailureCode::TrustUnavailable)),
            Some(HostFault::Assessed { signer, assessment }) => {
                HostAssessment::new(signer.clone(), assessment.clone())
            }
            // No scripted host verdict: the fake host is signed like the carrier
            // when carrier evidence was injected, and otherwise by the first
            // publisher the authenticated policy lists, with this target's
            // accepted verdict. Tests that need a denial, an outage or a foreign
            // signer script it with [`HostFault`].
            None => match self.faults.trust.as_ref() {
                Some(proof) => HostAssessment::new(proof.signer(), proof.assessment()),
                None => {
                    let signer = expected
                        .policy
                        .publishers
                        .first()
                        .ok_or_else(|| InstallFailure::new(FailureCode::TrustUnavailable))?;
                    let assessment = match expected.target {
                        Target::WindowsX64 => TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
                        Target::MacosArm64 => TRUST_ASSESSMENT_MACOS_GATEKEEPER,
                    };
                    HostAssessment::new(signer.clone(), assessment)
                }
            },
        }
    }

    fn carrier_entries(&self, source: &PackageSource) -> InstallResult<Vec<CarrierEntry>> {
        self.observe(PlatformCall::CarrierEntries);
        carrier_entries_checked(source)
    }

    fn install_carrier(
        &self,
        source: &PackageSource,
        destination: &ValidatedPath,
    ) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::InstallCarrier)?;
        // Contract-test implementation only: a byte copy is sufficient here
        // because the fake makes no macOS signature claim. The native adapters
        // (Task 8) perform the metadata-preserving install.
        self.probed(|| install_carrier_bytes(source, destination))
    }

    fn remove_owned(&self, entry: &OwnedEntry) -> InstallResult<()> {
        self.begin_mutation(PlatformCall::RemoveOwned)?;
        self.probed(|| remove_owned_entry(entry))
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
    crash_point("create_directory", "created, permissions not yet applied");
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
    crash_point("write_durable", "created, empty");

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
    crash_point("write_durable", "written, not flushed");
    file.sync_all().map_err(|_| io_failure())?;
    drop(file);
    crash_point("write_durable", "flushed, not read back");

    let readback = read_no_follow(path.rendered(), bytes.len() as u64).map_err(|_| io_failure())?;
    if corrupt_readback || readback != bytes {
        return Err(io_failure());
    }
    crash_point("write_durable", "verified, parent entry not flushed");
    if let Some(parent) = path.rendered().parent() {
        flush_directory(parent)?;
    }
    Ok(short_writes)
}

/// Replace one owned file without writing through the existing inode.
///
/// The new bytes are staged into a fresh sibling with `create_new` (so a planted
/// link or directory can never be adopted), flushed and read back, then renamed
/// over the destination entry. A destination that is a hard link keeps every
/// other name untouched: only this directory entry is replaced.
fn replace_durable_checked(path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
    path.revalidate()?;
    if path.kind() == PathKind::NewFile {
        return write_durable_checked(path, bytes, None, false).map(|_| ());
    }
    if path.kind() != PathKind::File {
        return Err(unsafe_path());
    }
    let live = path.rendered();
    let parent = live.parent().ok_or_else(unsafe_path)?;
    let name = live.file_name().ok_or_else(unsafe_path)?;
    let staging = parent.join(format!(".{}.installer-new", name.to_string_lossy()));
    match fs::symlink_metadata(&staging) {
        Ok(metadata) if is_link_like(&metadata) || !metadata.is_file() => {
            return Err(unsafe_path());
        }
        Ok(_) => fs::remove_file(&staging).map_err(|_| io_failure())?,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(_) => return Err(io_failure()),
    }

    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;

        options.mode(0o600).custom_flags(NO_FOLLOW);
    }
    let file = options.open(&staging).map_err(|error| {
        if error.kind() == io::ErrorKind::AlreadyExists {
            unsafe_path()
        } else {
            io_failure()
        }
    })?;
    crash_point("replace_durable", "staging sibling created, empty");
    let mut short_writes = 0;
    {
        let mut writer = &file;
        write_all_retrying(&mut writer, bytes, &mut short_writes).map_err(|_| io_failure())?;
    }
    crash_point("replace_durable", "staging bytes written, not flushed");
    file.sync_all().map_err(|_| io_failure())?;
    drop(file);

    let readback = match read_no_follow(&staging, bytes.len() as u64) {
        Ok(readback) => readback,
        Err(_) => {
            let _ = fs::remove_file(&staging);
            return Err(io_failure());
        }
    };
    if readback != bytes {
        let _ = fs::remove_file(&staging);
        return Err(io_failure());
    }
    // Revalidate immediately before the rename so a swapped ancestor fails closed.
    path.revalidate()?;
    crash_point(
        "replace_durable",
        "staged and verified, live entry untouched",
    );
    fs::rename(&staging, live).map_err(|_| {
        let _ = fs::remove_file(&staging);
        io_failure()
    })?;
    crash_point("replace_durable", "renamed, parent not flushed");
    flush_directory(parent)
}

/// Write the first half of `bytes` durably, then return: a process that dies
/// here leaves exactly the short file a non-atomic writer would.
fn write_partial(path: &ValidatedPath, bytes: &[u8]) -> io::Result<()> {
    path.revalidate()
        .map_err(|_| io::Error::other("partial write revalidation"))?;
    let file = open_for_write(path)?;
    let half = bytes.len() / 2;
    (&file).write_all(&bytes[..half])?;
    file.sync_all()
}

/// Render one state-document path below `state/`.
fn state_relative(document: StateDocument) -> std::path::PathBuf {
    Path::new(STATE_DIR).join(document.file_name())
}

/// Render the staging sibling used to replace a state document atomically.
fn state_staging_relative(document: StateDocument) -> std::path::PathBuf {
    Path::new(STATE_DIR).join(format!("{}.tmp", document.file_name()))
}

/// Open one state path as a validated handle: an existing regular file, or a
/// path that must not exist yet. A link, directory or unreadable metadata is a
/// typed failure, never "absent".
fn state_handle(paths: &InstallPaths, relative: &Path) -> InstallResult<ValidatedPath> {
    let absolute = paths.root().join(relative);
    match fs::symlink_metadata(&absolute) {
        Ok(metadata) if is_link_like(&metadata) || !metadata.is_file() => Err(unsafe_path()),
        Ok(_) => ValidatedPath::new(paths, relative, PathKind::File),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            ValidatedPath::new(paths, relative, PathKind::NewFile)
        }
        Err(_) => Err(io_failure()),
    }
}

/// Atomically replace one installer state document.
///
/// The bytes are written to a sibling staging file on the same volume (all
/// bytes, flush, read-back), renamed onto the live path and the parent directory
/// is flushed. A crash therefore leaves the previous complete document or the
/// new complete one; the staging sibling is inert and is re-adopted next time.
fn write_state_atomic(
    paths: &InstallPaths,
    document: StateDocument,
    bytes: &[u8],
    short_write_limit: Option<usize>,
    corrupt_readback: bool,
    fail_after_rename: bool,
) -> InstallResult<usize> {
    let staging_relative = state_staging_relative(document);
    let staging = state_handle(paths, &staging_relative)?;
    let short = write_durable_checked(&staging, bytes, short_write_limit, corrupt_readback)?;
    let staging = staging.adopt(PathKind::File)?;
    let live = state_handle(paths, &state_relative(document))?;
    replace_file_checked(&staging, &live)?;
    if fail_after_rename {
        // The replacement is durable, but the caller observes a failure: this is
        // the parent-flush-after-rename shape the transaction must treat as a
        // possible commit, never as licence to roll back.
        return Err(io_failure());
    }
    Ok(short)
}

/// Write half of a state document to its staging sibling, modelling a process
/// death in the middle of the (atomic) replacement. The live path is untouched.
fn tear_state_staging(
    paths: &InstallPaths,
    document: StateDocument,
    bytes: &[u8],
) -> InstallResult<()> {
    let staging = state_handle(paths, &state_staging_relative(document))?;
    write_partial(&staging, bytes).map_err(|_| io_failure())
}

/// Read one installer state document exactly, rejecting links and non-files.
///
/// An absent document is `None`; a present but empty document is reported as
/// `Some(empty)` so the caller can apply its own "released" convention.
fn read_state_document(
    paths: &InstallPaths,
    document: StateDocument,
) -> InstallResult<Option<Vec<u8>>> {
    let absolute = paths.root().join(state_relative(document));
    match fs::symlink_metadata(&absolute) {
        Ok(metadata) if is_link_like(&metadata) || !metadata.is_file() => Err(unsafe_path()),
        // At most the document bound plus one byte is buffered: an overlong
        // document is handed to the caller's parser as uninterpretable state.
        Ok(_) => read_no_follow_capped(&absolute, MAX_DOCUMENT_BYTES as u64)
            .map(Some)
            .map_err(|_| io_failure()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err(io_failure()),
    }
}

/// Move an uninterpretable state document to its fixed quarantine name.
///
/// The rename is atomic on one volume, so the live slot is released without an
/// unlink primitive and the rejected bytes stay available for diagnostics.
fn quarantine_state_document(paths: &InstallPaths, document: StateDocument) -> InstallResult<()> {
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
    replace_file_checked(&live, &quarantine)
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
        PathKind::Directory | PathKind::NewDirectory | PathKind::Symlink => {
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

/// Read one file through a single no-follow handle, never buffering more than
/// `limit` bytes.
///
/// The handle is inspected first: a regular file whose reported length exceeds
/// `limit` is rejected before any buffer exists. A length that lies (a file that
/// grows after the check) is still bounded, because the read itself is limited to
/// `limit + 1` bytes and any overflow is an error. [`io::ErrorKind::InvalidData`]
/// means "longer than the limit".
pub(crate) fn read_no_follow(path: &Path, limit: u64) -> io::Result<Vec<u8>> {
    let mut file = open_no_follow(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() {
        return Err(io::Error::from(io::ErrorKind::InvalidInput));
    }
    read_bounded(&mut file, limit, Some(metadata.len()))
}

/// Like [`read_no_follow`], but an overlong file yields its first `limit + 1`
/// bytes instead of an error, so the caller's own `len > limit` classification
/// (an uninterpretable document, not an IO fault) stays in charge.
pub(crate) fn read_no_follow_capped(path: &Path, limit: u64) -> io::Result<Vec<u8>> {
    let mut file = open_no_follow(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() {
        return Err(io::Error::from(io::ErrorKind::InvalidInput));
    }
    read_up_to(&mut file, limit, Some(metadata.len()))
}

/// Read `reader` to its end, failing with [`io::ErrorKind::InvalidData`] when it
/// holds more than `limit` bytes. At most `limit + 1` bytes are ever buffered.
pub(crate) fn read_bounded(
    reader: &mut impl Read,
    limit: u64,
    length_hint: Option<u64>,
) -> io::Result<Vec<u8>> {
    if length_hint.is_some_and(|length| length > limit) {
        return Err(io::Error::from(io::ErrorKind::InvalidData));
    }
    let bytes = read_up_to(reader, limit, length_hint)?;
    if bytes.len() as u64 > limit {
        return Err(io::Error::from(io::ErrorKind::InvalidData));
    }
    Ok(bytes)
}

fn read_up_to(reader: &mut impl Read, limit: u64, length_hint: Option<u64>) -> io::Result<Vec<u8>> {
    // The initial capacity is clamped by the limit, never taken from the hint
    // alone: a lying length cannot size the buffer.
    let capacity = length_hint.map_or(0, |length| length.min(limit.saturating_add(1)));
    let mut bytes = Vec::with_capacity(usize::try_from(capacity).unwrap_or(0));
    reader
        .take(limit.saturating_add(1))
        .read_to_end(&mut bytes)?;
    Ok(bytes)
}

/// Open one file for reading without following a final symbolic link or reparse
/// point. Shared by the hashing paths and the bounded cleanup helper.
pub(crate) fn open_no_follow(path: &Path) -> io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;

        options.custom_flags(NO_FOLLOW);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;

        // `FILE_FLAG_OPEN_REPARSE_POINT`: open a link itself instead of its
        // target; the caller then refuses the reparse attribute explicitly.
        options.custom_flags(0x0020_0000);
    }
    let file = options.open(path)?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;

        if file.metadata()?.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(io::Error::from(io::ErrorKind::InvalidInput));
        }
    }
    Ok(file)
}

/// The largest file this installer can legitimately own.
///
/// A payload file is bounded by the whole inventory ([`MAX_INVENTORY_BYTES`]) and a
/// carrier file by [`MAX_CARRIER_BYTES`], so no authenticated claim names a larger
/// one. Ownership records carry digests, not sizes, so this is the work bound for
/// claim creation and verification: a file that grows (or was swapped for a huge
/// one) after the claim was written is refused instead of being streamed to EOF.
pub(crate) const MAX_OWNED_FILE_BYTES: u64 = MAX_INVENTORY_BYTES;

/// The digest of the first `min(len, limit + 1)` bytes of a file, and how many bytes
/// that was.
///
/// `len > limit` means the file is longer than the bound; `digest` then covers only
/// its first `limit + 1` bytes and must not be treated as the file's identity.
#[derive(Debug, Clone, Copy)]
pub(crate) struct HashedFile {
    pub(crate) digest: Digest,
    pub(crate) len: u64,
}

impl HashedFile {
    /// The digest, or `ForeignState` when the file was longer than `limit` - a file
    /// larger than any claim this installer could have written is not ours.
    pub(crate) fn digest_within(self, limit: u64) -> InstallResult<Digest> {
        if self.len > limit {
            return Err(foreign_state());
        }
        Ok(self.digest)
    }
}

/// SHA-256 of at most `limit + 1` bytes read from one opened regular file.
///
/// The handle that is inspected is the handle that is read, and the read is
/// `take(limit + 1)`: the work is bounded by the caller's bound even if the file
/// grows while it is hashed. This is the only file hasher the engine and the
/// native adapters use.
pub(crate) fn hash_file_bounded(path: &Path, limit: u64) -> InstallResult<HashedFile> {
    let file = open_no_follow(path).map_err(|_| io_failure())?;
    if !file.metadata().map_err(|_| io_failure())?.is_file() {
        return Err(unsafe_path());
    }
    hash_reader(file, limit).map_err(|_| io_failure())
}

pub(crate) fn hash_reader(reader: impl Read, limit: u64) -> io::Result<HashedFile> {
    let mut reader = reader.take(limit.saturating_add(1));
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    let mut len = 0u64;
    loop {
        let read = reader.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        len += read as u64;
        hasher.update(&buffer[..read]);
    }
    Ok(HashedFile {
        digest: Digest::from_bytes(hasher.finalize().into()),
        len,
    })
}

/// SHA-256 of one owned file under [`MAX_OWNED_FILE_BYTES`]; `ForeignState` if it
/// is larger than any owned file can be.
pub(crate) fn hash_owned_file(path: &Path) -> InstallResult<Digest> {
    hash_file_bounded(path, MAX_OWNED_FILE_BYTES)?.digest_within(MAX_OWNED_FILE_BYTES)
}

/// The exact bytes a symbolic link stores as its target.
///
/// A link's content *is* its target path: hashing those bytes is how an owned
/// link claim is verified without ever resolving (and therefore following) it.
pub(crate) fn link_target_bytes(target: &OsStr) -> InstallResult<Vec<u8>> {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;

        Ok(target.as_bytes().to_vec())
    }
    #[cfg(not(unix))]
    {
        target
            .to_str()
            .map(|text| text.as_bytes().to_vec())
            .ok_or_else(unsafe_path)
    }
}

/// SHA-256 of one symbolic link's target bytes (never the target's content).
pub(crate) fn link_target_digest(path: &Path) -> InstallResult<Digest> {
    let target = fs::read_link(path).map_err(|_| unsafe_path())?;
    Ok(Digest::of(&link_target_bytes(target.as_os_str())?))
}

/// The recorded target of one symbolic link, or `None` when it is not UTF-8.
///
/// Carrier links are recorded as text because the journal and the ownership
/// record are JSON; a non-UTF-8 target is refused by the enumeration instead of
/// being stored lossily.
pub(crate) fn link_target_text(path: &Path) -> InstallResult<String> {
    let target = fs::read_link(path).map_err(|_| unsafe_path())?;
    target.to_str().map(str::to_owned).ok_or_else(unsafe_path)
}

/// Owner-only permission for a staged payload file.
#[cfg(unix)]
const fn launch_mode(executable: bool) -> u32 {
    if executable { 0o700 } else { 0o600 }
}

/// Open the leaf of `path` without following links and prove it is the object
/// validation captured.
fn open_verified_leaf(path: &ValidatedPath) -> InstallResult<File> {
    path.revalidate()?;
    let file = open_no_follow(path.rendered()).map_err(|_| unsafe_path())?;
    let metadata = file.metadata().map_err(|_| io_failure())?;
    if !metadata.is_file() {
        return Err(unsafe_path());
    }
    path.verify_open(&metadata, &identity_of_file(&file)?)?;
    Ok(file)
}

fn apply_launch_permissions_checked(path: &ValidatedPath, executable: bool) -> InstallResult<()> {
    let file = open_verified_leaf(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        crash_point("apply_launch_permissions", "opened, mode unchanged");
        // `fchmod` on the verified handle: never a path that could be swapped.
        file.set_permissions(fs::Permissions::from_mode(launch_mode(executable)))
            .map_err(|_| io_failure())?;
        crash_point("apply_launch_permissions", "mode set, not flushed");
        file.sync_all().map_err(|_| io_failure())?;
        crash_point("apply_launch_permissions", "mode flushed, not re-checked");
    }
    #[cfg(not(unix))]
    {
        let _ = executable;
        drop(file);
    }
    check_launch_permissions_checked(path, executable)
}

fn check_launch_permissions_checked(path: &ValidatedPath, executable: bool) -> InstallResult<()> {
    let file = open_verified_leaf(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        let mode = file
            .metadata()
            .map_err(|_| io_failure())?
            .permissions()
            .mode()
            & 0o777;
        if mode != launch_mode(executable) {
            return Err(io_failure());
        }
    }
    #[cfg(not(unix))]
    {
        let _ = (executable, file);
    }
    Ok(())
}

fn replace_manifest_checked(staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
    if staged.kind() != PathKind::File {
        return Err(unsafe_path());
    }
    if !matches!(live.kind(), PathKind::File | PathKind::NewFile) {
        return Err(unsafe_path());
    }
    replace_file_checked(staged, live)
}

/// Atomically rename one validated file onto another, flushing the destination
/// directory.
///
/// `staged` must be an existing regular file; `live` may be absent (a first
/// publication) or an existing regular file (a replacement, an upgrade or an
/// atomic state-document update). Both handles are revalidated immediately
/// before the rename, so a swapped ancestor fails closed.
fn replace_file_checked(staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
    staged.revalidate()?;
    live.revalidate()?;

    #[cfg(unix)]
    {
        crash_point("replace_file", "revalidated, live entry untouched");
        fs::rename(staged.rendered(), live.rendered()).map_err(|_| io_failure())?;
        crash_point("replace_file", "renamed, parent not flushed");
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
    let bytes = read_no_follow_capped(path, MAX_DOCUMENT_BYTES as u64).map_err(|_| io_failure())?;
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

fn publish_manifest_checked(paths: &InstallPaths, value: &Registration) -> InstallResult<()> {
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

/// The registry/manifest-registration half of publication.
///
/// The portable adapter has no registry, so this step verifies the ordering the
/// native adapters rely on: the manifest `value` names must already be the live
/// registration. Anything else means the two halves ran out of order (or another
/// writer intervened) and no registry value may be written.
fn publish_registry_checked(paths: &InstallPaths, value: &Registration) -> InstallResult<()> {
    validate_registration_manifest(paths, value.manifest())?;
    let Some(live) = read_registration_from(paths.registration_path())? else {
        return Err(io_failure());
    };
    // The same owned-preimage rule as the manifest step: never write the registry
    // for a manifest another product owns.
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
    crash_point("remove_registration", "verified, still registered");
    fs::remove_file(live.rendered()).map_err(|_| io_failure())?;
    crash_point("remove_registration", "unregistered, parent not flushed");
    if let Some(parent) = live.rendered().parent() {
        flush_directory(parent)?;
    }
    Ok(())
}

/// Enumerate one carrier artifact for [`Platform::carrier_entries`].
///
/// The walk mirrors what a metadata-preserving install will create; it is
/// read-only and never copies bytes. Links and special files are refused, and
/// entry/byte bounds apply before anything is installed.
fn carrier_entries_checked(source: &PackageSource) -> InstallResult<Vec<CarrierEntry>> {
    carrier_entries_with(source, hash_file_bounded)
}

/// Prove that a link target stays inside the artifact root.
///
/// The target is interpreted the way the OS will interpret it — relative to the
/// link's own directory — and every `..` is resolved against that directory. A
/// target that climbs above the artifact root, or that is absolute (including a
/// Windows drive/UNC/verbatim form), is refused: a copied link must never point
/// a later reader outside the signed bundle.
fn confine_link_target(relative: &str, target: &str) -> InstallResult<()> {
    if target.is_empty() || target.len() > MAX_PATH_BYTES || target.chars().any(char::is_control) {
        return Err(unsafe_path());
    }
    if target.starts_with('/') || target.starts_with('\\') || target.contains(':') {
        return Err(unsafe_path());
    }
    let parent = match relative.rfind('/') {
        Some(index) => &relative[..index],
        None => "",
    };
    let mut depth: Vec<&str> = if parent.is_empty() {
        Vec::new()
    } else {
        parent.split('/').collect()
    };
    for part in target.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                if depth.pop().is_none() {
                    return Err(unsafe_path());
                }
            }
            other if other.contains('\\') => return Err(unsafe_path()),
            other => depth.push(other),
        }
    }
    Ok(())
}

/// [`carrier_entries_checked`] over a caller-supplied content hash.
///
/// The native adapters pass their own no-follow hasher so enumeration and
/// install agree on the bytes they cover, while the portable adapter and the
/// fake keep the `std` hasher.
pub(crate) fn carrier_entries_with(
    source: &PackageSource,
    hash: impl Fn(&Path, u64) -> InstallResult<HashedFile>,
) -> InstallResult<Vec<CarrierEntry>> {
    let metadata = fs::symlink_metadata(source.path()).map_err(|_| invalid_package())?;
    if is_link_like(&metadata) {
        return Err(unsafe_path());
    }
    let mut entries = Vec::new();
    let mut total = 0u64;
    match source.kind() {
        PackageKind::File => {
            if !metadata.is_file() || metadata.len() == 0 {
                return Err(invalid_package());
            }
            total = metadata.len();
            if total > MAX_CARRIER_BYTES as u64 {
                return Err(invalid_package());
            }
            let digest = hash_carrier_file(&hash, source.path(), metadata.len())?;
            entries.push(CarrierEntry::new(
                "",
                OwnedKind::File,
                metadata.len(),
                Some(digest),
            )?);
        }
        PackageKind::Directory => {
            if !metadata.is_dir() {
                return Err(invalid_package());
            }
            entries.push(CarrierEntry::new("", OwnedKind::Directory, 0, None)?);
            walk_carrier(source.path(), "", &mut entries, &mut total, &hash)?;
        }
    }
    Ok(entries)
}

/// Hash one carrier file whose length the walk just read, reading at most that
/// length plus one byte. A file that grew or shrank since the length check is not
/// the file the entry describes: refusing it keeps both the hashing work and the
/// recorded (size, digest) pair honest.
fn hash_carrier_file(
    hash: &impl Fn(&Path, u64) -> InstallResult<HashedFile>,
    path: &Path,
    length: u64,
) -> InstallResult<Digest> {
    let hashed = hash(path, length)?;
    if hashed.len != length {
        return Err(invalid_package());
    }
    Ok(hashed.digest)
}

fn walk_carrier(
    directory: &Path,
    relative: &str,
    entries: &mut Vec<CarrierEntry>,
    total: &mut u64,
    hash: &impl Fn(&Path, u64) -> InstallResult<HashedFile>,
) -> InstallResult<()> {
    let mut children: Vec<_> = fs::read_dir(directory)
        .map_err(|_| io_failure())?
        .map(|entry| entry.map_err(|_| io_failure()))
        .collect::<Result<_, _>>()?;
    children.sort_by_key(|entry| entry.file_name());
    for child in children {
        if entries.len() >= MAX_CARRIER_ENTRIES {
            return Err(invalid_package());
        }
        let name = child.file_name();
        let name = name.to_str().ok_or_else(unsafe_path)?;
        let child_relative = if relative.is_empty() {
            name.to_owned()
        } else {
            format!("{relative}/{name}")
        };
        let path = child.path();
        let metadata = fs::symlink_metadata(&path).map_err(|_| io_failure())?;
        if metadata.file_type().is_symlink() {
            // A bundle link is recorded as a link entry, never followed: the
            // exact target bytes are the owned content and the target must stay
            // inside the artifact root.
            let target = link_target_text(&path)?;
            confine_link_target(&child_relative, &target)?;
            *total = total
                .checked_add(target.len() as u64)
                .ok_or_else(invalid_package)?;
            if *total > MAX_CARRIER_BYTES as u64 {
                return Err(invalid_package());
            }
            entries.push(CarrierEntry::link(&child_relative, &target)?);
        } else if metadata.is_dir() {
            entries.push(CarrierEntry::new(
                &child_relative,
                OwnedKind::Directory,
                0,
                None,
            )?);
            walk_carrier(&path, &child_relative, entries, total, hash)?;
        } else if metadata.is_file() {
            *total = total
                .checked_add(metadata.len())
                .ok_or_else(invalid_package)?;
            if *total > MAX_CARRIER_BYTES as u64 {
                return Err(invalid_package());
            }
            let digest = hash_carrier_file(hash, &path, metadata.len())?;
            entries.push(CarrierEntry::new(
                &child_relative,
                OwnedKind::File,
                metadata.len(),
                Some(digest),
            )?);
        } else {
            return Err(unsafe_path());
        }
    }
    Ok(())
}

/// Byte-only carrier installation for [`FakePlatform`] contract tests.
///
/// This is deliberately **not** a macOS fidelity claim: the fake cannot assess a
/// signature and its copy drops extended attributes, exactly like the rejected
/// engine copy. Only the native adapters may install a real carrier, and those
/// fail closed here.
fn install_carrier_bytes(source: &PackageSource, destination: &ValidatedPath) -> InstallResult<()> {
    match source.kind() {
        PackageKind::File => {
            if destination.kind() != PathKind::NewFile {
                return Err(invalid_input());
            }
            let bytes = match read_no_follow(source.path(), MAX_CARRIER_BYTES as u64) {
                Ok(bytes) => bytes,
                Err(error) if error.kind() == io::ErrorKind::InvalidData => {
                    return Err(invalid_package());
                }
                Err(_) => return Err(io_failure()),
            };
            if bytes.is_empty() {
                return Err(invalid_package());
            }
            write_durable_checked(destination, &bytes, None, false).map(|_| ())
        }
        PackageKind::Directory => {
            if destination.kind() != PathKind::NewDirectory {
                return Err(invalid_input());
            }
            create_private_directory(destination)?;
            let mut entries = 0usize;
            let mut total = 0u64;
            copy_carrier_tree(
                source.path(),
                destination.rendered(),
                &mut entries,
                &mut total,
            )
        }
    }
}

fn copy_carrier_tree(
    source: &Path,
    destination: &Path,
    entries: &mut usize,
    total: &mut u64,
) -> InstallResult<()> {
    let mut children: Vec<_> = fs::read_dir(source)
        .map_err(|_| io_failure())?
        .map(|entry| entry.map_err(|_| io_failure()))
        .collect::<Result<_, _>>()?;
    children.sort_by_key(|entry| entry.file_name());
    for child in children {
        *entries += 1;
        if *entries >= MAX_CARRIER_ENTRIES {
            return Err(invalid_package());
        }
        let target = destination.join(child.file_name());
        if !path_is_within(destination, &target) {
            return Err(unsafe_path());
        }
        let path = child.path();
        let metadata = fs::symlink_metadata(&path).map_err(|_| io_failure())?;
        if metadata.file_type().is_symlink() {
            // The fake recreates the link from its recorded target and never
            // follows it. Link creation is a unix-only capability here; the fake
            // is contract-test scaffolding, not a product adapter.
            #[cfg(unix)]
            {
                let recorded = fs::read_link(&path).map_err(|_| io_failure())?;
                std::os::unix::fs::symlink(recorded, &target).map_err(|_| io_failure())?;
            }
            #[cfg(not(unix))]
            {
                let _ = &target;
                return Err(InstallFailure::new(FailureCode::UnsupportedTarget));
            }
        } else if metadata.is_dir() {
            fs::create_dir(&target).map_err(|_| io_failure())?;
            copy_carrier_tree(&path, &target, entries, total)?;
        } else if metadata.is_file() {
            *total = total
                .checked_add(metadata.len())
                .ok_or_else(invalid_package)?;
            if *total > MAX_CARRIER_BYTES as u64 {
                return Err(invalid_package());
            }
            let bytes = read_no_follow(&path, metadata.len()).map_err(|_| io_failure())?;
            fs::write(&target, &bytes).map_err(|_| io_failure())?;
        } else {
            return Err(unsafe_path());
        }
    }
    Ok(())
}

fn remove_owned_entry(entry: &OwnedEntry) -> InstallResult<()> {
    entry.path.revalidate()?;
    crash_point("remove_owned", "verified, still present");
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
            if !short && hash_owned_file(entry.path.rendered())? != expected {
                return Err(foreign_state());
            }
            fs::remove_file(entry.path.rendered()).map_err(|_| io_failure())?;
        }
        // The link itself is unlinked after its exact target bytes were verified;
        // the target's content is never read or removed.
        OwnedKind::Symlink => {
            let expected = entry.claim.sha256.ok_or_else(foreign_state)?;
            if link_target_digest(entry.path.rendered())? != expected {
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
    crash_point("remove_owned", "unlinked, parent not flushed");
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

#[cfg(test)]
mod tests {
    /// A directory-kind source holding `a.bin` (`len` bytes) and the temp root.
    fn carrier_with_one_file(name: &str, len: usize) -> (std::path::PathBuf, std::path::PathBuf) {
        let root = std::env::temp_dir().join(format!("{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).expect("carrier dir");
        let file = root.join("a.bin");
        std::fs::write(&file, vec![7u8; len]).expect("carrier file");
        (root, file)
    }

    /// R6: the carrier walk bounds a file's *metadata* length, then hashes. A file
    /// that grows between the two must not be hashed to its new end (unbounded
    /// work) nor recorded as an entry whose size and digest disagree.
    #[test]
    fn a_carrier_file_that_grows_after_its_length_check_is_refused() {
        use super::{PackageSource, carrier_entries_with, hash_file_bounded};

        let (root, file) = carrier_with_one_file("veles-carrier-grows", 1000);
        let source = PackageSource::open(&root).expect("source");
        let outcome = carrier_entries_with(&source, |path, limit| {
            // Grow the file by 64 MiB (sparse) after the walk read its length.
            let handle = std::fs::OpenOptions::new()
                .write(true)
                .open(&file)
                .expect("reopen");
            handle.set_len(64 * 1024 * 1024).expect("grow");
            let hashed = hash_file_bounded(path, limit)?;
            // The hasher itself stopped one byte past the bound it was given.
            assert_eq!(
                hashed.len,
                limit + 1,
                "hashing was not bounded by the length"
            );
            Ok(hashed)
        });
        let _ = std::fs::remove_dir_all(&root);
        assert_eq!(
            outcome
                .expect_err("a file that grew is not the file that was measured")
                .code,
            crate::model::FailureCode::InvalidPackage
        );
    }

    #[test]
    fn a_carrier_file_that_shrinks_after_its_length_check_is_refused() {
        use super::{PackageSource, carrier_entries_with, hash_file_bounded};

        let (root, file) = carrier_with_one_file("veles-carrier-shrinks", 1000);
        let source = PackageSource::open(&root).expect("source");
        let outcome = carrier_entries_with(&source, |path, limit| {
            std::fs::write(&file, b"short").expect("shrink");
            hash_file_bounded(path, limit)
        });
        let _ = std::fs::remove_dir_all(&root);
        assert!(outcome.is_err(), "{outcome:?}");
    }

    #[test]
    fn an_unchanged_carrier_file_is_recorded_with_its_measured_length() {
        use super::{PackageSource, carrier_entries_with, hash_file_bounded};

        let (root, _) = carrier_with_one_file("veles-carrier-stable", 1000);
        let source = PackageSource::open(&root).expect("source");
        let entries = carrier_entries_with(&source, hash_file_bounded).expect("entries");
        let _ = std::fs::remove_dir_all(&root);
        assert_eq!(entries.len(), 2, "the root directory and the file");
    }

    /// A reader that counts the bytes it hands out and would serve forever.
    struct Endless {
        served: std::cell::Cell<u64>,
    }

    impl std::io::Read for &Endless {
        fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
            buffer.fill(0);
            self.served.set(self.served.get() + buffer.len() as u64);
            Ok(buffer.len())
        }
    }

    #[test]
    fn hashing_never_pulls_more_than_the_bound_plus_one_byte_from_an_endless_source() {
        use super::hash_reader;

        for limit in [0u64, 1, 4095, 64 * 1024, 64 * 1024 + 1, 1 << 20] {
            let source = Endless {
                served: std::cell::Cell::new(0),
            };
            let hashed = hash_reader(&source, limit).expect("hash");
            assert_eq!(hashed.len, limit + 1, "limit {limit}");
            assert!(
                source.served.get() <= limit + 1 + 64 * 1024,
                "limit {limit}: {} bytes were requested from the source",
                source.served.get()
            );
        }
    }

    #[test]
    fn a_file_over_the_bound_is_foreign_not_hashed_to_its_end() {
        use super::hash_file_bounded;

        let (root, file) = carrier_with_one_file("veles-hash-bound", 100);
        let hashed = hash_file_bounded(&file, 99).expect("hash");
        assert_eq!(hashed.len, 100);
        assert_eq!(
            hashed.digest_within(99).expect_err("over the bound").code,
            crate::model::FailureCode::ForeignState
        );
        let exact = hash_file_bounded(&file, 100).expect("hash");
        assert_eq!(exact.len, 100);
        assert_eq!(
            exact.digest_within(100).expect("at the bound"),
            crate::model::Digest::of(&[7u8; 100])
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[cfg(windows)]
    #[test]
    fn verbatim_and_dos_prefixes_compare_equal() {
        use super::path_is_within;
        use std::path::Path;

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

    /// A leaf that no longer holds the object the carrier install created is
    /// never deleted: the install reports `ForeignState` and the file is
    /// preserved, so the journaled cleanup reports the leftover instead of this
    /// call destroying foreign content. An unreadable identity is equally
    /// foreign.
    #[test]
    fn mismatched_leaf_identity_is_foreign_and_preserved() {
        use super::{discard_created_leaf, identity_of_file};
        use crate::model::{FailureCode, InstallFailure};

        let unique = format!("veles-platform-discard-{}", std::process::id());
        let dir = std::env::temp_dir().join(unique);
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("temp dir");
        let path = dir.join("carrier");

        // The object the install created.
        std::fs::write(&path, b"created by the install").expect("created file");
        let created = {
            let file = std::fs::File::open(&path).expect("open created");
            identity_of_file(&file).expect("created identity")
        };
        // A different object now holds the same leaf.
        std::fs::remove_file(&path).expect("drop created");
        std::fs::write(&path, b"foreign content").expect("foreign file");
        let occupant = {
            let file = std::fs::File::open(&path).expect("open occupant");
            identity_of_file(&file).expect("occupant identity")
        };
        assert_ne!(created, occupant, "the replacement is a distinct object");

        let mut deleted = false;
        let failure = discard_created_leaf(
            created,
            || Ok(Some(occupant)),
            || {
                deleted = true;
                Ok(())
            },
        )
        .expect_err("a foreign occupant is not ours to delete");
        assert_eq!(failure.code, FailureCode::ForeignState);
        assert!(!deleted, "the deleter never runs for a foreign identity");
        assert_eq!(
            std::fs::read(&path).expect("preserved"),
            b"foreign content",
            "the foreign leaf is preserved"
        );

        // An identity that cannot be read proves nothing either.
        let mut deleted = false;
        assert_eq!(
            discard_created_leaf(
                created,
                || Err(InstallFailure::new(FailureCode::Io)),
                || {
                    deleted = true;
                    Ok(())
                },
            )
            .expect_err("an unreadable identity cannot prove ownership")
            .code,
            FailureCode::ForeignState
        );
        assert!(!deleted);

        std::fs::remove_dir_all(&dir).expect("temp dir cleanup");
    }

    /// A discard never turns a detected mismatch into a successful install.
    ///
    /// Removing the object the install itself created is the expected outcome,
    /// and it must still be reported as `ForeignState`: the destination could not
    /// be proven to be that object, so the carrier install may not return `Ok`.
    /// A refused removal is `CleanupPending` (the leftover must be cleaned up),
    /// never `ForeignState` and never `Ok`.
    #[test]
    fn discard_never_reports_the_install_as_successful() {
        use super::{discard_created_leaf, identity_of_file};
        use crate::model::{FailureCode, InstallFailure};

        let unique = format!("veles-platform-discard-refusal-{}", std::process::id());
        let dir = std::env::temp_dir().join(unique);
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("temp dir");
        let path = dir.join("carrier");
        std::fs::write(&path, b"ours").expect("ours");
        let ours = {
            let file = std::fs::File::open(&path).expect("open ours");
            identity_of_file(&file).expect("ours identity")
        };

        // The recorded object is removed, and the install still fails.
        let mut removed = false;
        let failure = discard_created_leaf(
            ours,
            || Ok(Some(ours)),
            || {
                removed = true;
                std::fs::remove_file(&path).map_err(|_| InstallFailure::new(FailureCode::Io))
            },
        )
        .expect_err("a successful discard is not a successful install");
        assert_eq!(failure.code, FailureCode::ForeignState);
        assert!(removed, "the recorded object is removed");
        assert!(!path.exists());

        // Ours, but the deletion is refused: the leftover is pending cleanup.
        assert_eq!(
            discard_created_leaf(
                ours,
                || Ok(Some(ours)),
                || Err(InstallFailure::new(FailureCode::Io)),
            )
            .expect_err("a refused deletion leaves a leftover")
            .code,
            FailureCode::CleanupPending
        );

        std::fs::remove_dir_all(&dir).expect("temp dir cleanup");
    }
}
