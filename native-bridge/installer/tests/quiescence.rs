//! Portable host-quiescence contract tests for the OTP-25 installer (Task 5).
//!
//! These tests exercise the *classification and bounded-wait* layer with
//! injected process observations: a [`ProcessTable`] stands in for what the
//! native adapters enumerate with the Windows
//! `NtQuerySystemInformation(SystemProcessInformation)`/token/process-image APIs
//! or with macOS `libproc`. No Windows or macOS process API runs here, so a
//! passing test is portable quiescence-policy evidence only and is never
//! represented as native process-inspection proof.
//!
//! The named tests pin the rules the installer must not weaken:
//!
//! * every Chrome profile and every *retained prior version* counts, so a host
//!   executing any owned image blocks mutation;
//! * an inaccessible candidate that runs as the installing user is `Unknown`
//!   (blocks), never `Quiescent`, while a process positively attributed to
//!   another user does not have to be readable at all - and an image outside the
//!   owned trees rules a process out before its user is ever consulted;
//! * a process the installer cannot open at all is decided from the OS-reported
//!   image *name*: when no executable below the owned trees has that name the
//!   process is ruled out, so the protected system processes of a stock machine
//!   (`System`, `Memory Compression`, PPL services) cannot block every install,
//!   while a name an owned executable does have still blocks;
//! * process identity is the executable image (not a name) and is rechecked
//!   against PID reuse, so a recycled PID neither keeps blocking on its own nor
//!   hides an owned image that now runs under it;
//! * a host launched after the quiescence check is caught by the recheck before
//!   cleanup and every possibly live payload version stays on disk.
//!
//! The block path only ever inspects: it never calls a mutation (the [`Platform`]
//! boundary has no termination method at all) and it reports `HostActive` or
//! `InspectionDenied` after a bounded wait of at most ten seconds.
//!
//! The native integration step in the Task 5 brief - start a fixture child at an
//! owned test path plus one unrelated child and prove only the owned child
//! blocks - needs a Windows or macOS runner and is BLOCKED on this host. It is
//! recorded in the Task 5 report and not simulated here.

use std::collections::HashMap;
use std::ffi::OsString;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process;
use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use veles_bridge_installer::{
    Channel, FailureCode, FakePlatform, HostState, InstallLock, InstallPaths, InstallResult,
    OwnedEntry, PackageSource, Platform, PlatformCall, ProcessIdentity, ProcessImage,
    ProcessObservation, ProcessScope, ProcessTable, QUIESCENCE_TIMEOUT, Registration,
    ReleaseDescriptor, Target, TrustProof, ValidatedPath, inspect_host_processes,
    require_quiescent, require_quiescent_within,
};

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

struct TempRoot {
    path: PathBuf,
}

impl TempRoot {
    fn new() -> Self {
        let unique = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "veles-installer-task5-quiescence-{}-{unique}",
            process::id()
        ));
        if path.exists() {
            fs::remove_dir_all(&path).expect("stale temp root");
        }
        fs::create_dir_all(&path).expect("temp root");
        Self { path }
    }

    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TempRoot {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

/// Injected Windows known-folder roots, as in the earlier task suites: the
/// portable layout is target-selected, so a Windows layout is exercised on any
/// host and production never accepts these roots from a CLI or environment.
fn windows_paths(temp: &TempRoot) -> InstallPaths {
    let home = temp.path().join("Test User Å/用户");
    let local = temp.path().join("Local AppData/用户 Å");
    InstallPaths::for_user(Target::WindowsX64, Channel::Production, &home, Some(&local))
        .expect("valid injected Windows roots")
}

/// Write one owned executable and return its canonical (OS-real) path, which is
/// the form a process table reports for a running image.
fn write_executable(path: &Path) -> PathBuf {
    fs::create_dir_all(path.parent().expect("executable parent")).expect("executable parent");
    fs::write(path, b"owned payload").expect("owned payload");
    fs::canonicalize(path).expect("canonical owned payload")
}

fn version_image(paths: &InstallPaths, version: &str, name: &str) -> PathBuf {
    write_executable(&paths.versions().join(version).join(name))
}

fn owned(pid: u32, image: &Path) -> ProcessObservation {
    ProcessObservation {
        identity: ProcessIdentity { pid },
        scope: ProcessScope::InstallingUser,
        image: ProcessImage::Path(image.to_path_buf()),
    }
}

fn foreign_image(pid: u32, image: &Path) -> ProcessObservation {
    ProcessObservation {
        identity: ProcessIdentity { pid },
        scope: ProcessScope::InstallingUser,
        image: ProcessImage::Path(fs::canonicalize(image).expect("canonical foreign image")),
    }
}

/// A same-user process whose image the OS could not resolve.
fn unresolved(pid: u32) -> ProcessObservation {
    ProcessObservation {
        identity: ProcessIdentity { pid },
        scope: ProcessScope::InstallingUser,
        image: ProcessImage::Unresolved,
    }
}

/// A process positively attributed to another user.
fn other_user(pid: u32) -> ProcessObservation {
    ProcessObservation {
        identity: ProcessIdentity { pid },
        scope: ProcessScope::OtherUser,
        image: ProcessImage::Unresolved,
    }
}

/// A process the OS could not attribute to any user, with its resolved image.
fn unattributed(pid: u32, image: Option<&Path>) -> ProcessObservation {
    ProcessObservation {
        identity: ProcessIdentity { pid },
        scope: ProcessScope::Unknown,
        image: image.map_or(ProcessImage::Unresolved, |path| {
            ProcessImage::Path(path.to_path_buf())
        }),
    }
}

/// A process the OS could not attribute *and* the installer could not open, so
/// only the OS-reported image file name exists (Windows
/// `SystemProcessInformation.ImageName`; empty for a process with no image file).
fn name_only(pid: u32, name: &str) -> ProcessObservation {
    ProcessObservation {
        identity: ProcessIdentity { pid },
        scope: ProcessScope::Unknown,
        image: ProcessImage::Name(OsString::from(name)),
    }
}

/// A scripted process table: successive snapshots, the last one repeating, plus
/// explicit recheck answers (a live process rechecks to its own observation).
#[derive(Debug, Default)]
struct ScriptedTable {
    tables: Mutex<Vec<Vec<ProcessObservation>>>,
    last: Mutex<Vec<ProcessObservation>>,
    rechecks: Mutex<HashMap<u32, Option<ProcessObservation>>>,
    fail_rechecks: Mutex<bool>,
}

impl ScriptedTable {
    fn new(tables: Vec<Vec<ProcessObservation>>) -> Self {
        Self {
            tables: Mutex::new(tables),
            last: Mutex::new(Vec::new()),
            rechecks: Mutex::new(HashMap::new()),
            fail_rechecks: Mutex::new(false),
        }
    }

    /// Answer the recheck of `pid` with `current` (`None` = the process exited).
    fn with_recheck(self, pid: u32, current: Option<ProcessObservation>) -> Self {
        self.rechecks
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .insert(pid, current);
        self
    }

    /// Make every recheck fail, modelling an OS that cannot re-read the process.
    fn with_failing_rechecks(self) -> Self {
        *Self::lock(&self.fail_rechecks) = true;
        self
    }

    fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
        mutex
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}

impl ProcessTable for ScriptedTable {
    fn observe(&self, _paths: &InstallPaths) -> io::Result<Vec<ProcessObservation>> {
        let mut tables = Self::lock(&self.tables);
        let table = if tables.len() > 1 {
            tables.remove(0)
        } else {
            tables.first().cloned().unwrap_or_default()
        };
        *Self::lock(&self.last) = table.clone();
        Ok(table)
    }

    fn recheck(&self, _paths: &InstallPaths, pid: u32) -> io::Result<Option<ProcessObservation>> {
        if *Self::lock(&self.fail_rechecks) {
            return Err(io::Error::other("process recheck unavailable"));
        }
        if let Some(current) = Self::lock(&self.rechecks).get(&pid) {
            return Ok(current.clone());
        }
        Ok(Self::lock(&self.last)
            .iter()
            .find(|observation| observation.identity.pid == pid)
            .cloned())
    }
}

/// A table whose snapshot cannot be read at all.
struct FailingTable;

impl ProcessTable for FailingTable {
    fn observe(&self, _paths: &InstallPaths) -> io::Result<Vec<ProcessObservation>> {
        Err(io::Error::other("process table unavailable"))
    }

    fn recheck(&self, _paths: &InstallPaths, _pid: u32) -> io::Result<Option<ProcessObservation>> {
        Err(io::Error::other("process table unavailable"))
    }
}

/// A [`Platform`] that records calls through [`FakePlatform`] and performs real
/// host classification over a scripted process table.
///
/// `FakePlatform` supplies the fault injection, mutation counter and call log of
/// the other task suites. Its own `inspect_hosts` only records the call, so this
/// wrapper observes through it and then classifies with the real portable rules.
struct ScriptedPlatform {
    inner: FakePlatform,
    table: ScriptedTable,
}

impl ScriptedPlatform {
    fn new(table: ScriptedTable) -> Self {
        Self {
            inner: FakePlatform::new(),
            table,
        }
    }

    fn mutation_count(&self) -> usize {
        self.inner.mutation_count()
    }

    fn calls(&self) -> Vec<PlatformCall> {
        self.inner.calls()
    }

    fn inspection_count(&self) -> usize {
        self.calls()
            .iter()
            .filter(|call| **call == PlatformCall::InspectHosts)
            .count()
    }

    /// Whether the whole block path only inspected: no write, rename,
    /// registration publication/removal or owned-entry removal was attempted.
    fn only_inspected(&self) -> bool {
        self.calls()
            .iter()
            .all(|call| *call == PlatformCall::InspectHosts)
    }
}

impl Platform for ScriptedPlatform {
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock> {
        self.inner.lock(paths)
    }

    fn write_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        self.inner.write_durable(path, bytes)
    }

    fn replace_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        self.inner.replace_durable(path, bytes)
    }

    fn replace_manifest(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        self.inner.replace_manifest(staged, live)
    }

    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>> {
        self.inner.read_registration(paths)
    }

    fn publish_registration(
        &self,
        paths: &InstallPaths,
        value: &Registration,
    ) -> InstallResult<()> {
        self.inner.publish_registration(paths, value)
    }

    fn remove_registration(
        &self,
        paths: &InstallPaths,
        expected: &Registration,
    ) -> InstallResult<()> {
        self.inner.remove_registration(paths, expected)
    }

    fn inspect_hosts(&self, paths: &InstallPaths) -> InstallResult<HostState> {
        let _ = self.inner.inspect_hosts(paths)?;
        Ok(inspect_host_processes(paths, &self.table))
    }

    fn apply_launch_permissions(
        &self,
        path: &ValidatedPath,
        executable: bool,
    ) -> InstallResult<()> {
        self.inner.apply_launch_permissions(path, executable)
    }

    fn check_launch_permissions(
        &self,
        path: &ValidatedPath,
        executable: bool,
    ) -> InstallResult<()> {
        self.inner.check_launch_permissions(path, executable)
    }

    fn verify_host(
        &self,
        root: &std::path::Path,
        executable: &str,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<veles_bridge_installer::HostAssessment> {
        self.inner.verify_host(root, executable, expected)
    }

    fn verify_trust(
        &self,
        source: &PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        self.inner.verify_trust(source, expected)
    }

    fn remove_owned(&self, entry: &OwnedEntry) -> InstallResult<()> {
        self.inner.remove_owned(entry)
    }

    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()> {
        self.inner.flush_parent(path)
    }
}

#[test]
fn two_profiles_and_prior_version_block() {
    let temp = TempRoot::new();
    let paths = windows_paths(&temp);
    let current = version_image(&paths, "1.2.3-aaaaaaaa", "host");
    let prior = version_image(&paths, "1.2.2-bbbbbbbb", "host");
    let foreign = temp.path().join("elsewhere").join("host");
    fs::create_dir_all(foreign.parent().expect("foreign parent")).expect("foreign parent");
    fs::write(&foreign, b"unrelated").expect("foreign image");

    // Two Chrome profiles each run the host from the same owned version.
    let two_profiles = ScriptedTable::new(vec![vec![owned(4100, &current), owned(4200, &current)]]);
    assert_eq!(
        inspect_host_processes(&paths, &two_profiles),
        HostState::Active
    );

    // The retained prior version is still executing: that blocks too, because
    // cleanup would otherwise pull the image out from under it.
    let prior_version = ScriptedTable::new(vec![vec![owned(4300, &prior)]]);
    assert_eq!(
        inspect_host_processes(&paths, &prior_version),
        HostState::Active
    );

    // The same executable base name in a foreign directory is not ours.
    let same_name_elsewhere = ScriptedTable::new(vec![vec![foreign_image(4400, &foreign)]]);
    assert_eq!(
        inspect_host_processes(&paths, &same_name_elsewhere),
        HostState::Quiescent
    );

    // The bounded installer wait reports HostActive, retries instead of trusting
    // one sample, stays inside the fixed ten-second ceiling and mutates nothing.
    let platform = ScriptedPlatform::new(ScriptedTable::new(vec![vec![
        owned(4100, &current),
        owned(4200, &current),
        owned(4300, &prior),
    ]]));
    let started = Instant::now();
    let failure = require_quiescent_within(&platform, &paths, Duration::from_millis(150))
        .expect_err("two profiles and a prior version must block");
    let elapsed = started.elapsed();
    assert_eq!(failure.code, FailureCode::HostActive);
    assert!(
        elapsed <= Duration::from_secs(10),
        "quiescence wait exceeded the ceiling: {elapsed:?}"
    );
    assert!(QUIESCENCE_TIMEOUT <= Duration::from_secs(10));
    assert!(
        platform.only_inspected(),
        "block path called {:#?}",
        platform.calls()
    );
    assert_eq!(platform.mutation_count(), 0);
    assert!(
        platform.inspection_count() >= 2,
        "active hosts must be re-inspected, not sampled once"
    );

    // Every possibly live version is retained byte-for-byte.
    assert_eq!(
        fs::read(&current).expect("current payload"),
        b"owned payload"
    );
    assert_eq!(fs::read(&prior).expect("prior payload"), b"owned payload");
    assert_eq!(
        fs::read_dir(paths.versions())
            .expect("versions dir")
            .count(),
        2
    );
}

#[test]
fn inaccessible_candidate_is_unknown_not_quiescent() {
    let temp = TempRoot::new();
    let paths = windows_paths(&temp);

    // A same-user process whose image cannot be read could be executing an owned
    // version, so it is Unknown (blocks), never Quiescent.
    let inaccessible = ScriptedTable::new(vec![vec![unresolved(5100)]]);
    assert_eq!(
        inspect_host_processes(&paths, &inaccessible),
        HostState::Unknown
    );

    // A process positively attributed to another user cannot execute the
    // user-private root, so its unreadable image does not have to block: the
    // inspector never reads unrelated protected system processes' images.
    let foreign_user = ScriptedTable::new(vec![vec![other_user(5200)]]);
    assert_eq!(
        inspect_host_processes(&paths, &foreign_user),
        HostState::Quiescent
    );

    // The bounded wait maps Unknown to InspectionDenied, again with no mutation.
    let platform = ScriptedPlatform::new(ScriptedTable::new(vec![vec![unresolved(5100)]]));
    let failure = require_quiescent_within(&platform, &paths, Duration::from_millis(100))
        .expect_err("an unresolved same-user image must block");
    assert_eq!(failure.code, FailureCode::InspectionDenied);
    assert!(platform.only_inspected());
    assert_eq!(platform.mutation_count(), 0);

    // An inspection that cannot be performed at all is Unknown, never a claimed
    // quiescent state.
    assert_eq!(
        inspect_host_processes(&paths, &FailingTable),
        HostState::Unknown
    );
}

#[test]
fn foreign_image_is_decided_before_user_attribution() {
    let temp = TempRoot::new();
    let paths = windows_paths(&temp);
    let current = version_image(&paths, "4.0.0-ffffffff", "host");
    let foreign = temp.path().join("bin").join("host");
    fs::create_dir_all(foreign.parent().expect("foreign parent")).expect("foreign parent");
    fs::write(&foreign, b"unrelated").expect("foreign image");

    // A process the OS cannot attribute is irrelevant when its image is outside
    // the owned trees: no uid is needed to rule it out, so an unrelated
    // protected system process cannot keep the installer blocked.
    let unknown_scope_foreign = ScriptedTable::new(vec![vec![unattributed(6700, Some(&foreign))]]);
    assert_eq!(
        inspect_host_processes(&paths, &unknown_scope_foreign),
        HostState::Quiescent
    );

    // The same unattributable process with an owned image is a potential host:
    // the image is inside our private root, so missing attribution blocks.
    let unknown_scope_owned = ScriptedTable::new(vec![vec![unattributed(6800, Some(&current))]]);
    assert_eq!(
        inspect_host_processes(&paths, &unknown_scope_owned),
        HostState::Active
    );

    // Without any resolved image the process cannot be ruled out either.
    let unknown_scope_unresolved = ScriptedTable::new(vec![vec![unattributed(6900, None)]]);
    assert_eq!(
        inspect_host_processes(&paths, &unknown_scope_unresolved),
        HostState::Unknown
    );

    // A recheck that resolves the PID to a foreign image clears the candidate;
    // one that resolves it to an owned image blocks.
    let reused_foreign = ScriptedTable::new(vec![vec![unattributed(7000, None)]])
        .with_recheck(7000, Some(unattributed(7000, Some(&foreign))));
    assert_eq!(
        inspect_host_processes(&paths, &reused_foreign),
        HostState::Quiescent
    );
    let reused_owned = ScriptedTable::new(vec![vec![unattributed(7010, None)]])
        .with_recheck(7010, Some(unattributed(7010, Some(&current))));
    assert_eq!(
        inspect_host_processes(&paths, &reused_owned),
        HostState::Active
    );
}

#[test]
fn unopenable_process_without_an_owned_image_name_does_not_block() {
    let temp = TempRoot::new();
    let paths = windows_paths(&temp);

    // A non-elevated installer cannot open the `System` pseudo-process, `Memory
    // Compression` or a PPL service. The OS still reports their image file names,
    // and an empty owned root contains no executable with those names, so they are
    // ruled out without a handle: the stock-machine case must not be permanently
    // `InspectionDenied`.
    let stock_machine = ScriptedTable::new(vec![vec![
        name_only(4, "System"),
        name_only(5_020, "MemCompression"),
        name_only(5_030, "svchost.exe"),
        name_only(5_040, ""),
    ]]);
    assert_eq!(
        inspect_host_processes(&paths, &stock_machine),
        HostState::Quiescent
    );

    // A populated root changes nothing for those names: none of them is an owned
    // executable, so no handle is opened and nothing blocks.
    let current = version_image(&paths, "5.0.0-11111111", "host");
    let long_name = write_executable(
        &paths
            .versions()
            .join("5.0.0-11111111")
            .join("veles-bridge-host.exe"),
    );
    assert_eq!(
        inspect_host_processes(&paths, &stock_machine),
        HostState::Quiescent
    );

    // The kernel's legacy `ImageFileName` fallback truncates to 15 characters, so a
    // prefix of an owned executable's name is still a possible owned host.
    let truncated = ScriptedTable::new(vec![vec![name_only(5_100, "veles-bridge-ho")]]);
    assert_eq!(
        inspect_host_processes(&paths, &truncated),
        HostState::Unknown
    );

    // A name an owned executable actually has cannot be ruled out: the process may
    // be executing it, and being unable to open it is not evidence to the contrary.
    let matching_name = ScriptedTable::new(vec![vec![name_only(5_200, "host")]]);
    assert_eq!(
        inspect_host_processes(&paths, &matching_name),
        HostState::Unknown
    );
    let platform = ScriptedPlatform::new(ScriptedTable::new(vec![vec![name_only(5_200, "host")]]));
    let failure = require_quiescent_within(&platform, &paths, Duration::from_millis(100))
        .expect_err("an unopenable process with an owned image name must block");
    assert_eq!(failure.code, FailureCode::InspectionDenied);
    assert!(platform.only_inspected());
    assert_eq!(platform.mutation_count(), 0);

    // The safety property is unchanged for a resolved image: an owned-root image
    // whose user cannot be attributed is an active host, not a name to ignore.
    let owned_unknown_user = ScriptedTable::new(vec![vec![unattributed(5_300, Some(&current))]]);
    assert_eq!(
        inspect_host_processes(&paths, &owned_unknown_user),
        HostState::Active
    );

    // A name-only candidate whose PID was recycled by an unrelated program stops
    // blocking, so a stale name cannot pin the installer forever either.
    let foreign = temp.path().join("bin").join("host");
    fs::create_dir_all(foreign.parent().expect("foreign parent")).expect("foreign parent");
    fs::write(&foreign, b"unrelated").expect("foreign image");
    let recycled = ScriptedTable::new(vec![vec![name_only(5_400, "host")]])
        .with_recheck(5_400, Some(foreign_image(5_400, &foreign)));
    assert_eq!(
        inspect_host_processes(&paths, &recycled),
        HostState::Quiescent
    );

    // Nothing above mutated a possibly live payload.
    assert_eq!(
        fs::read(&current).expect("current payload"),
        b"owned payload"
    );
    assert_eq!(
        fs::read(&long_name).expect("long payload"),
        b"owned payload"
    );
}

#[test]
fn pid_reuse_not_bound_to_name() {
    let temp = TempRoot::new();
    let paths = windows_paths(&temp);
    let current = version_image(&paths, "2.0.0-cccccccc", "host");
    let foreign = temp.path().join("bin").join("host");
    fs::create_dir_all(foreign.parent().expect("foreign parent")).expect("foreign parent");
    fs::write(&foreign, b"unrelated").expect("foreign image");

    // The base name is irrelevant: only the resolved image path identifies ours.
    let same_name = ScriptedTable::new(vec![vec![foreign_image(6100, &foreign)]]);
    assert_eq!(
        inspect_host_processes(&paths, &same_name),
        HostState::Quiescent
    );

    // The PID was recycled by an unrelated program, so the stale snapshot entry
    // cannot keep the installer blocked.
    let reused_foreign = ScriptedTable::new(vec![vec![owned(6100, &current)]])
        .with_recheck(6100, Some(foreign_image(6100, &foreign)));
    assert_eq!(
        inspect_host_processes(&paths, &reused_foreign),
        HostState::Quiescent
    );

    // The observed process simply exited: nothing is live, so nothing blocks.
    let exited = ScriptedTable::new(vec![vec![owned(6200, &current)]]).with_recheck(6200, None);
    assert_eq!(
        inspect_host_processes(&paths, &exited),
        HostState::Quiescent
    );

    // Conversely, a recycled PID that now executes an owned image blocks: the
    // recheck classifies the process that holds the PID now, so an inaccessible
    // snapshot entry cannot hide a newly launched owned host.
    let reused_owned = ScriptedTable::new(vec![vec![unresolved(6300)]])
        .with_recheck(6300, Some(owned(6300, &current)));
    assert_eq!(
        inspect_host_processes(&paths, &reused_owned),
        HostState::Active
    );

    // A recycled PID that is now another user's process cannot execute the
    // user-private root.
    let reused_other_user = ScriptedTable::new(vec![vec![owned(6400, &current)]])
        .with_recheck(6400, Some(other_user(6400)));
    assert_eq!(
        inspect_host_processes(&paths, &reused_other_user),
        HostState::Quiescent
    );

    // A live process whose recheck still reports the owned image is still ours.
    let live = ScriptedTable::new(vec![vec![owned(6500, &current)]]);
    assert_eq!(inspect_host_processes(&paths, &live), HostState::Active);

    // A recheck that cannot be answered is Unknown: the process might still be
    // our host, so a failed identity recheck never becomes Quiescent.
    let unreadable_recheck =
        ScriptedTable::new(vec![vec![owned(6600, &current)]]).with_failing_rechecks();
    assert_eq!(
        inspect_host_processes(&paths, &unreadable_recheck),
        HostState::Unknown
    );

    // Inspecting never removed or truncated an owned image.
    assert_eq!(
        fs::read(&current).expect("current payload"),
        b"owned payload"
    );
}

#[test]
fn late_launch_preserves_live_payload() {
    let temp = TempRoot::new();
    let paths = windows_paths(&temp);
    let current = version_image(&paths, "3.0.0-dddddddd", "host");
    let prior = version_image(&paths, "2.9.0-eeeeeeee", "host");
    let maintenance = write_executable(&paths.maintenance().join("veles-cleanup"));
    let unrelated = temp.path().join("bin").join("unrelated");
    fs::create_dir_all(unrelated.parent().expect("unrelated parent")).expect("unrelated parent");
    fs::write(&unrelated, b"unrelated").expect("unrelated image");

    let platform = ScriptedPlatform::new(ScriptedTable::new(vec![
        vec![foreign_image(7050, &unrelated)],
        vec![owned(7100, &current)],
    ]));

    // The installer's first check succeeds: no owned host is running.
    require_quiescent(&platform, &paths).expect("no owned host before the launch");

    // Chrome launches the host after that check. The recheck before cleanup must
    // catch it instead of removing the payload it is executing.
    let failure = require_quiescent_within(&platform, &paths, Duration::from_millis(120))
        .expect_err("a late launch must block cleanup");
    assert_eq!(failure.code, FailureCode::HostActive);
    assert!(platform.only_inspected());
    assert_eq!(platform.mutation_count(), 0);

    // Every possibly live version and the maintenance asset are retained.
    assert_eq!(
        fs::read(&current).expect("current payload"),
        b"owned payload"
    );
    assert_eq!(fs::read(&prior).expect("prior payload"), b"owned payload");
    assert_eq!(
        fs::read(&maintenance).expect("maintenance asset"),
        b"owned payload"
    );
    assert_eq!(
        fs::read_dir(paths.versions())
            .expect("versions dir")
            .count(),
        2
    );
}
