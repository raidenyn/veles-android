//! Repair, upgrade, rollback and uninstall contract tests (OTP-25 Task 7).
//!
//! These tests drive the operation layer end to end over [`FakePlatform`] and
//! real portable files under an injected per-user root. They pin the maintenance
//! rules the installer must not weaken:
//!
//! * a same-version source repairs missing/damaged managed files and restores the
//!   exact registration without re-running trust, while a conflicting (foreign)
//!   registration is reported and never overwritten;
//! * an older installer never silently downgrades an installed release, and a
//!   repair package that is not the installed release asks for the signed
//!   download;
//! * a rollback must satisfy both the retained signed policy and the persisted
//!   one, so a stale signature cannot erase a recorded payload denial;
//! * uninstall removes only owned paths, is idempotent, preserves foreign files
//!   and a foreign registration, and never claims success while an owned host is
//!   still running (it journals the partial removal and reports
//!   `cleanup-pending`);
//! * no operation touches a Chrome profile or re-verifies trust on its own, and a
//!   receipt can never authorize a path outside the fixed layout.
//!
//! Native Windows/macOS HKCU publication, `ReplaceFileW` atomicity, Gatekeeper
//! sealing and the physical acceptance matrix remain out of scope here and are
//! recorded as BLOCKED in the Task 7 report: this host is Linux x86_64.

use std::collections::BTreeMap;
use std::fs;
use std::panic::{self, AssertUnwindSafe};
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::time::Duration;

use veles_bridge_installer::journal::{MaintenancePreimage, MaintenancePublication};
use veles_bridge_installer::{
    AuthenticatedPackage, Channel, Digest, FailureCode, FakePlatform, Faults, HostState,
    InstallFailure, InstallLock, InstallPaths, InstallResult, InventoryEntry,
    JOURNAL_SCHEMA_VERSION, Journal, JournalClaim, JournalState, MAX_INVENTORY_BYTES,
    ManifestSnapshot, NativeManifest, Operation, PRODUCTION_HOST_NAME, Phase, Platform,
    PlatformCall, PortablePlatform, RETAINED_FILE_NAME, RETAINED_SCHEMA_VERSION, Receipt,
    ReceiptState, Registration, ReleaseDescriptor, ReleasePolicy, RemovalCoordinator,
    RemovalRequest, RetainedInstall, STATE_ABSENT, STATE_DAMAGED, STATE_FOREIGN, STATE_REPAIRED,
    STATE_ROLLED_BACK, STATE_UNINSTALLED, StateDocument, TRUST_ASSESSMENT_MACOS_GATEKEEPER,
    TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, Target, TrustProof, ValidatedPath, Version, WarningCode,
    authenticate_package, canonical_json, cleanup_staging_directory, extension_origin,
    parse_manifest, path_is_within, recover, run_operation, run_operation_within, write_carrier,
    write_inventory,
};

const EXTENSION_ID: &str = "mnopabcdefghijklmnopabcdefghijkl";
const PUBLISHER: &str = "Publisher One";
const HOST_V1: &[u8] = b"v1 host payload";
const NOTICE_V1: &[u8] = b"v1 licence notice";
const HOST_V2: &[u8] = b"v2 host payload";
const NOTICE_V2: &[u8] = b"v2 licence notice";
const HOST_V3: &[u8] = b"v3 host payload";
const NOTICE_V3: &[u8] = b"v3 licence notice";
const PROFILE_SENTINEL: &[u8] = b"{\"profile\":\"not the installer's business\"}\n";
/// A short, explicit bound so a blocked quiescence wait cannot stall the suite.
const SHORT_WAIT: Duration = Duration::from_millis(50);

fn failure_code<T: std::fmt::Debug>(
    result: Result<T, veles_bridge_installer::InstallFailure>,
) -> FailureCode {
    result.expect_err("expected an installer failure").code
}

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

struct TempRoot {
    path: PathBuf,
}

impl TempRoot {
    fn new() -> Self {
        let unique = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "veles-installer-task7-operations-{}-{unique}",
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

/// A valid owned manifest selecting `executable`, as an injected registration
/// would carry it.
fn manifest_selecting(paths: &InstallPaths, executable: &Path) -> Vec<u8> {
    canonical_json(&NativeManifest {
        name: paths.registration_name().to_owned(),
        description: "injected earlier release".to_owned(),
        path: executable.to_str().expect("UTF-8 fixture path").to_owned(),
        kind: "stdio".to_owned(),
        allowed_origins: vec![extension_origin(EXTENSION_ID).expect("valid fixture origin")],
    })
    .expect("canonical manifest")
}

/// The payload identity of a `(host, notice)` payload, exactly as the installer
/// computes it from the canonical unsigned inventory document.
fn payload_id(host: &[u8], notice: &[u8]) -> Digest {
    Digest::of(
        &write_inventory(&[
            InventoryEntry {
                path: "host.exe".to_owned(),
                size: host.len() as u64,
                sha256: Digest::of(host),
                executable: true,
            },
            InventoryEntry {
                path: "licenses/NOTICE".to_owned(),
                size: notice.len() as u64,
                sha256: Digest::of(notice),
                executable: false,
            },
        ])
        .expect("canonical fixture inventory"),
    )
}

fn policy(epoch: u64, denied: Vec<Digest>, rollback: Vec<Digest>) -> ReleasePolicy {
    ReleasePolicy {
        publishers: vec![PUBLISHER.to_owned()],
        denied_payloads: denied,
        rollback_payloads: rollback,
        epoch,
    }
}

/// One authenticated release package on disk plus the evidence that covers it.
struct Package {
    root: PathBuf,
    descriptor: ReleaseDescriptor,
    proof: TrustProof,
}

fn build_package(
    temp: &TempRoot,
    target: Target,
    name: &str,
    version: &str,
    host: &[u8],
    notice: &[u8],
    policy: ReleasePolicy,
) -> Package {
    build_package_with(
        temp,
        target,
        name,
        version,
        host,
        notice,
        policy,
        PUBLISHER,
        (1, 2),
    )
}

#[allow(clippy::too_many_arguments)]
fn build_package_with(
    temp: &TempRoot,
    target: Target,
    name: &str,
    version: &str,
    host: &[u8],
    notice: &[u8],
    policy: ReleasePolicy,
    signer: &str,
    api: (u32, u32),
) -> Package {
    build_package_from(
        temp,
        target,
        name,
        version,
        &[("host.exe", host, true), ("licenses/NOTICE", notice, false)],
        policy,
        signer,
        api,
    )
}

/// One authenticated release package built from an explicit payload file list
/// of `(relative path, bytes, executable)`.
#[allow(clippy::too_many_arguments)]
fn build_package_from(
    temp: &TempRoot,
    target: Target,
    name: &str,
    version: &str,
    files: &[(&str, &[u8], bool)],
    policy: ReleasePolicy,
    signer: &str,
    api: (u32, u32),
) -> Package {
    let root = temp.path().join("packages").join(name);
    let payload = root.join("payload");
    let mut entries = Vec::new();
    for (relative, bytes, executable) in files {
        let path = payload.join(relative);
        fs::create_dir_all(path.parent().expect("payload parent")).expect("payload tree");
        fs::write(&path, bytes).expect("payload file");
        entries.push(InventoryEntry {
            path: (*relative).to_owned(),
            size: bytes.len() as u64,
            sha256: Digest::of(bytes),
            executable: *executable,
        });
    }
    let inventory = write_inventory(&entries).expect("canonical inventory document");
    fs::write(root.join("inventory.json"), &inventory).expect("inventory document");

    let (minimum_os, assessment) = match target {
        Target::WindowsX64 => ("11".to_owned(), TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE),
        Target::MacosArm64 => ("26.0".to_owned(), TRUST_ASSESSMENT_MACOS_GATEKEEPER),
    };
    let descriptor = ReleaseDescriptor {
        schema: 1,
        channel: Channel::Production,
        version: version.to_owned(),
        source_commit: "0123456789abcdef0123456789abcdef01234567".to_owned(),
        target,
        minimum_os,
        extension_id: EXTENSION_ID.to_owned(),
        host_name: PRODUCTION_HOST_NAME.to_owned(),
        bundle_id: PRODUCTION_HOST_NAME.to_owned(),
        api_min: api.0,
        api_max: api.1,
        payload_id: Digest::of(&inventory),
        policy,
    };
    descriptor
        .validate()
        .expect("the fixture descriptor is a valid release identity");

    let carrier =
        write_carrier(&canonical_json(&descriptor).expect("descriptor JSON")).expect("carrier");
    // The same signed carrier artifact a native package holds. The installer
    // copies it into the immutable version and a rollback re-verifies that copy.
    match target {
        Target::WindowsX64 => {
            fs::write(root.join("carrier.exe"), &carrier).expect("carrier artifact");
        }
        Target::MacosArm64 => {
            let resources = root.join("carrier.app/Contents/Resources");
            fs::create_dir_all(&resources).expect("carrier bundle");
            fs::write(resources.join("veles-policy-carrier"), &carrier).expect("carrier resource");
            // The carrier bundle's own engine: the persistent maintenance engine is
            // published from this authenticated artifact.
            let macos = root.join("carrier.app/Contents/MacOS");
            fs::create_dir_all(&macos).expect("carrier executable directory");
            fs::write(
                macos.join("veles-bridge-maintenance"),
                b"macOS maintenance engine",
            )
            .expect("carrier engine");
        }
    }
    let proof = TrustProof::new(signer, assessment, carrier).expect("fixture trust proof");

    Package {
        root,
        descriptor,
        proof,
    }
}

/// Authenticate one fixture package through Task 4's only constructor.
fn authenticate(package: &Package) -> AuthenticatedPackage {
    let platform = FakePlatform::with_faults(Faults {
        trust: Some(package.proof.clone()),
        ..Faults::default()
    });
    authenticate_package(&platform, &package.root, &package.descriptor)
        .expect("the fixture package authenticates")
}

fn version_dir(paths: &InstallPaths, version: &str, payload_id: Digest) -> PathBuf {
    paths.version_dir(
        &Version::parse(version).expect("fixture version"),
        payload_id,
    )
}

/// The form a validated native handle renders for an existing path.
#[track_caller]
fn canonical_dir(path: &Path) -> PathBuf {
    fs::canonicalize(path)
        .unwrap_or_else(|error| panic!("canonicalize {}: {error}", path.display()))
}

fn receipt(paths: &InstallPaths) -> Option<Receipt> {
    match Receipt::load(&PortablePlatform::new(), paths).expect("the receipt is readable") {
        ReceiptState::None => None,
        ReceiptState::Committed(receipt) => Some(*receipt),
        ReceiptState::Unreadable | ReceiptState::Unsupported => {
            panic!("the committed receipt must be interpretable")
        }
    }
}

fn journal_state(paths: &InstallPaths) -> JournalState {
    veles_bridge_installer::Journal::load(&PortablePlatform::new(), paths)
        .expect("the journal is readable")
}

/// Every file below `root` with its exact bytes; directories are empty entries.
fn snapshot(root: &Path) -> BTreeMap<String, Vec<u8>> {
    fn walk(base: &Path, directory: &Path, out: &mut BTreeMap<String, Vec<u8>>) {
        for entry in fs::read_dir(directory).expect("snapshot directory") {
            let entry = entry.expect("snapshot entry");
            let path = entry.path();
            let relative = path
                .strip_prefix(base)
                .expect("path below snapshot root")
                .to_string_lossy()
                .into_owned();
            let metadata = fs::symlink_metadata(&path).expect("snapshot metadata");
            if metadata.is_dir() {
                out.insert(relative, Vec::new());
                walk(base, &path, out);
            } else {
                out.insert(relative, fs::read(&path).expect("snapshot bytes"));
            }
        }
    }

    let mut out = BTreeMap::new();
    walk(root, root, &mut out);
    out
}

fn changed_paths(
    before: &BTreeMap<String, Vec<u8>>,
    after: &BTreeMap<String, Vec<u8>>,
) -> Vec<String> {
    let mut changed: Vec<String> = before
        .keys()
        .chain(after.keys())
        .filter(|key| before.get(*key) != after.get(*key))
        .cloned()
        .collect();
    changed.sort();
    changed.dedup();
    changed
}

/// Assert every changed path is inside the fixed install root or is exactly the
/// fixed registration file: no Chrome profile, no unrelated tree, ever.
fn assert_changes_confined(
    temp: &TempRoot,
    paths: &InstallPaths,
    before: &BTreeMap<String, Vec<u8>>,
    after: &BTreeMap<String, Vec<u8>>,
    context: &str,
) {
    let render = |path: &Path| {
        path.strip_prefix(temp.path())
            .expect("path below the temp root")
            .to_string_lossy()
            .into_owned()
    };
    let root = render(paths.root());
    let registration = render(paths.registration_path());
    let separator = std::path::MAIN_SEPARATOR;
    for changed in changed_paths(before, after) {
        assert!(
            changed == root
                || changed.starts_with(&format!("{root}{separator}"))
                || changed == registration,
            "{context}: {changed} is not inside the install root or the fixed registration"
        );
    }
}

/// Give a fixture staging directory the same owner-only protection the installer
/// applies when it creates one. A directory made with `create_dir_all` inherits
/// the temporary directory's DACL (and, under a full administrator token, its
/// `BUILTIN\Administrators` owner), which the staging check must refuse; the
/// fixture wants to exercise the *contents* refusal, not the DACL refusal.
#[cfg(windows)]
fn protect_staging_fixture(directory: &Path) {
    let user = std::env::var("USERNAME").expect("USERNAME is set on Windows");
    let owner = process::Command::new("icacls")
        .arg(directory)
        .args(["/setowner", &user])
        .status()
        .expect("icacls is a standard Windows tool");
    assert!(owner.success(), "could not set the staging fixture owner");
    let dacl = process::Command::new("icacls")
        .arg(directory)
        .args(["/inheritance:r", "/grant:r", &format!("{user}:(OI)(CI)F")])
        .status()
        .expect("icacls is a standard Windows tool");
    assert!(
        dacl.success(),
        "could not give the staging fixture the installer's private DACL"
    );
}

/// A fixture: one injected per-user layout plus a quiescent, trusted installer.
struct Fixture {
    temp: TempRoot,
    paths: InstallPaths,
    target: Target,
    /// The OS evidence of every release this fixture built: whichever installed
    /// carrier a platform is asked to verify, the OS verifies that one.
    proofs: std::cell::RefCell<Vec<TrustProof>>,
}

impl Fixture {
    fn windows() -> Self {
        let temp = TempRoot::new();
        let home = temp.path().join("Test User Å/用户");
        let local = temp.path().join("Local AppData/用户 Å");
        let paths =
            InstallPaths::for_user(Target::WindowsX64, Channel::Production, &home, Some(&local))
                .expect("valid injected Windows roots");
        Self {
            temp,
            paths,
            target: Target::WindowsX64,
            proofs: std::cell::RefCell::new(Vec::new()),
        }
    }

    fn macos() -> Self {
        let temp = TempRoot::new();
        let home = temp.path().join("Test User Å/用户");
        let paths = InstallPaths::for_user(Target::MacosArm64, Channel::Production, &home, None)
            .expect("valid injected macOS roots");
        // Chrome creates its Native Messaging directory the first time it runs.
        fs::create_dir_all(
            paths
                .registration_path()
                .parent()
                .expect("registration parent"),
        )
        .expect("Chrome registration directory");
        Self {
            temp,
            paths,
            target: Target::MacosArm64,
            proofs: std::cell::RefCell::new(Vec::new()),
        }
    }

    fn package(
        &self,
        name: &str,
        version: &str,
        host: &[u8],
        notice: &[u8],
        policy: ReleasePolicy,
    ) -> Package {
        let package = build_package(&self.temp, self.target, name, version, host, notice, policy);
        self.proofs.borrow_mut().push(package.proof.clone());
        package
    }

    #[allow(clippy::too_many_arguments)]
    fn package_with(
        &self,
        name: &str,
        version: &str,
        host: &[u8],
        notice: &[u8],
        policy: ReleasePolicy,
        signer: &str,
        api: (u32, u32),
    ) -> Package {
        let package = build_package_with(
            &self.temp,
            self.target,
            name,
            version,
            host,
            notice,
            policy,
            signer,
            api,
        );
        self.proofs.borrow_mut().push(package.proof.clone());
        package
    }

    /// A package built from an explicit payload list, for maintenance-path fixtures.
    fn package_from(
        &self,
        name: &str,
        version: &str,
        files: &[(&str, &[u8], bool)],
        policy: ReleasePolicy,
    ) -> Package {
        let package = build_package_from(
            &self.temp,
            self.target,
            name,
            version,
            files,
            policy,
            PUBLISHER,
            (1, 2),
        );
        self.proofs.borrow_mut().push(package.proof.clone());
        package
    }

    /// A platform with extra faults but the same vouched-for releases.
    fn platform_with(&self, faults: Faults) -> FakePlatform {
        FakePlatform::with_faults(Faults {
            trust_by_carrier: self.proofs.borrow().clone(),
            ..faults
        })
    }

    fn platform(&self) -> FakePlatform {
        // An install over a prior release authenticates that release's installed
        // carrier in place, so the fake OS must be able to vouch for every release
        // this fixture has built (it still vouches for nothing else).
        FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            trust_by_carrier: self.proofs.borrow().clone(),
            ..Faults::default()
        })
    }

    /// A platform whose retained-carrier OS verification accepts `proof`.
    ///
    /// A rollback re-runs the OS trust path over the retained signed carrier; the
    /// fake stands in for that native evidence, so a rollback test must supply the
    /// proof of the release being re-activated (never a stored metadata string).
    fn platform_trusting(&self, proof: &TrustProof) -> FakePlatform {
        FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            trust: Some(proof.clone()),
            trust_by_carrier: self.proofs.borrow().clone(),
            ..Faults::default()
        })
    }

    fn install(&self, package: &Package) -> veles_bridge_installer::InstallReport {
        run_operation(
            &self.platform(),
            &self.paths,
            Operation::Install,
            Some(authenticate(package)),
        )
        .expect("the fixture release installs")
    }

    fn registration(&self) -> Vec<u8> {
        fs::read(self.paths.registration_path()).expect("the fixture registers a manifest")
    }

    /// A fake Chrome profile, so an operation that reached into it would be seen.
    fn seed_chrome_profile(&self) -> PathBuf {
        let profile = self
            .paths
            .user_home()
            .join("Library/Application Support/Google/Chrome/Default/Preferences");
        fs::create_dir_all(profile.parent().expect("profile parent")).expect("profile directory");
        fs::write(&profile, PROFILE_SENTINEL).expect("profile sentinel");
        profile
    }

    /// Files this installer does not own, inside its own layout.
    fn seed_unowned_assets(&self) -> BTreeMap<String, Vec<u8>> {
        for (relative, content) in [
            ("versions/unrelated.txt", b"not ours".as_slice()),
            (
                "maintenance/prior-helper.bin",
                b"prior maintenance".as_slice(),
            ),
            ("state/unrelated-state.json", b"{}\n".as_slice()),
        ] {
            let path = self.paths.root().join(relative);
            fs::create_dir_all(path.parent().expect("asset parent")).expect("asset parent");
            fs::write(&path, content).expect("unowned asset");
        }
        self.unowned_assets()
    }

    fn unowned_assets(&self) -> BTreeMap<String, Vec<u8>> {
        [
            "versions/unrelated.txt",
            "maintenance/prior-helper.bin",
            "state/unrelated-state.json",
        ]
        .into_iter()
        .map(|relative| {
            (
                relative.to_owned(),
                fs::read(self.paths.root().join(relative)).expect("unowned asset"),
            )
        })
        .collect()
    }

    fn assert_unowned_assets(&self, expected: &BTreeMap<String, Vec<u8>>, context: &str) {
        for (relative, content) in expected {
            assert_eq!(
                &fs::read(self.paths.root().join(relative)).expect(context),
                content,
                "{context}: unowned file {relative} is preserved"
            );
        }
    }

    /// A well-formed registration another product owns: its executable is
    /// outside our install root.
    fn foreign_registration(&self) -> Vec<u8> {
        let foreign_directory = self.temp.path().join("foreign");
        fs::create_dir_all(&foreign_directory).expect("foreign directory");
        let executable = foreign_directory.join("host.exe");
        fs::write(&executable, b"foreign host").expect("foreign executable");
        canonical_json(&veles_bridge_installer::NativeManifest {
            name: self.paths.registration_name().to_owned(),
            description: "foreign product".to_owned(),
            path: executable.to_str().expect("UTF-8 fixture path").to_owned(),
            kind: "stdio".to_owned(),
            allowed_origins: vec![extension_origin(EXTENSION_ID).expect("valid fixture origin")],
        })
        .expect("canonical foreign manifest")
    }
}

/// A [`Platform`] that delegates every call to [`FakePlatform`] and answers host
/// inspection from a script: the first `quiescent_until` inspections are
/// quiescent, after that an owned host has (re)launched.
///
/// Every method is delegated, including state documents and publication: a
/// wrapper that let default trait methods bypass `FakePlatform` would neither
/// record calls nor apply its faults, and the operation tests would silently
/// prove nothing about the boundary.
struct RelaunchingPlatform {
    inner: FakePlatform,
    inspections: AtomicUsize,
    quiescent_until: usize,
}

impl RelaunchingPlatform {
    fn new(quiescent_until: usize) -> Self {
        Self {
            inner: FakePlatform::with_faults(Faults {
                host_state: HostState::Quiescent,
                ..Faults::default()
            }),
            inspections: AtomicUsize::new(0),
            quiescent_until,
        }
    }

    fn calls(&self) -> Vec<PlatformCall> {
        self.inner.calls()
    }
}

impl Platform for RelaunchingPlatform {
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock> {
        self.inner.lock(paths)
    }

    fn write_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        self.inner.write_durable(path, bytes)
    }

    fn replace_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        self.inner.replace_durable(path, bytes)
    }

    fn write_state(
        &self,
        paths: &InstallPaths,
        document: StateDocument,
        bytes: &[u8],
    ) -> InstallResult<()> {
        self.inner.write_state(paths, document, bytes)
    }

    fn read_state(
        &self,
        paths: &InstallPaths,
        document: StateDocument,
    ) -> InstallResult<Option<Vec<u8>>> {
        self.inner.read_state(paths, document)
    }

    fn quarantine_state(&self, paths: &InstallPaths, document: StateDocument) -> InstallResult<()> {
        self.inner.quarantine_state(paths, document)
    }

    fn create_directory(&self, path: &ValidatedPath) -> InstallResult<()> {
        self.inner.create_directory(path)
    }

    fn replace_manifest(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        self.inner.replace_manifest(staged, live)
    }

    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>> {
        self.inner.read_registration(paths)
    }

    fn publish_manifest(&self, paths: &InstallPaths, value: &Registration) -> InstallResult<()> {
        self.inner.publish_manifest(paths, value)
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
        // Observe through the inner platform so the call log stays complete.
        let _ = self.inner.inspect_hosts(paths)?;
        let seen = self.inspections.fetch_add(1, Ordering::SeqCst);
        Ok(if seen < self.quiescent_until {
            HostState::Quiescent
        } else {
            HostState::Active
        })
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
        source: &veles_bridge_installer::PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        self.inner.verify_trust(source, expected)
    }

    fn carrier_entries(
        &self,
        source: &veles_bridge_installer::PackageSource,
    ) -> InstallResult<Vec<veles_bridge_installer::CarrierEntry>> {
        self.inner.carrier_entries(source)
    }

    fn install_carrier(
        &self,
        source: &veles_bridge_installer::PackageSource,
        destination: &ValidatedPath,
    ) -> InstallResult<()> {
        self.inner.install_carrier(source, destination)
    }

    fn remove_owned(&self, entry: &veles_bridge_installer::OwnedEntry) -> InstallResult<()> {
        self.inner.remove_owned(entry)
    }

    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()> {
        self.inner.flush_parent(path)
    }
}

/// A [`Platform`] that delegates every call to [`FakePlatform`] and writes a
/// conflicting, structurally owned registration during the first host
/// inspection: the conflict appears while an operation is waiting for
/// quiescence, after the pre-wait ownership snapshot.
struct InterveningPlatform {
    inner: FakePlatform,
    registration: Vec<u8>,
    planted: AtomicUsize,
}

impl InterveningPlatform {
    fn new(registration: Vec<u8>) -> Self {
        Self {
            inner: FakePlatform::with_faults(Faults {
                host_state: HostState::Quiescent,
                ..Faults::default()
            }),
            registration,
            planted: AtomicUsize::new(0),
        }
    }

    fn calls(&self) -> Vec<PlatformCall> {
        self.inner.calls()
    }
}

impl Platform for InterveningPlatform {
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock> {
        self.inner.lock(paths)
    }

    fn write_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        self.inner.write_durable(path, bytes)
    }

    fn replace_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        self.inner.replace_durable(path, bytes)
    }

    fn write_state(
        &self,
        paths: &InstallPaths,
        document: StateDocument,
        bytes: &[u8],
    ) -> InstallResult<()> {
        self.inner.write_state(paths, document, bytes)
    }

    fn read_state(
        &self,
        paths: &InstallPaths,
        document: StateDocument,
    ) -> InstallResult<Option<Vec<u8>>> {
        self.inner.read_state(paths, document)
    }

    fn quarantine_state(&self, paths: &InstallPaths, document: StateDocument) -> InstallResult<()> {
        self.inner.quarantine_state(paths, document)
    }

    fn create_directory(&self, path: &ValidatedPath) -> InstallResult<()> {
        self.inner.create_directory(path)
    }

    fn replace_manifest(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        self.inner.replace_manifest(staged, live)
    }

    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>> {
        self.inner.read_registration(paths)
    }

    fn publish_manifest(&self, paths: &InstallPaths, value: &Registration) -> InstallResult<()> {
        self.inner.publish_manifest(paths, value)
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
        // Observe through the inner platform so the call log stays complete, then
        // replace the registration exactly once: the conflict appears while the
        // operation waits for quiescence.
        let state = self.inner.inspect_hosts(paths)?;
        if self.planted.fetch_add(1, Ordering::SeqCst) == 0 {
            fs::write(paths.registration_path(), &self.registration)
                .expect("intervening registration");
        }
        Ok(state)
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
        source: &veles_bridge_installer::PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        self.inner.verify_trust(source, expected)
    }

    fn carrier_entries(
        &self,
        source: &veles_bridge_installer::PackageSource,
    ) -> InstallResult<Vec<veles_bridge_installer::CarrierEntry>> {
        self.inner.carrier_entries(source)
    }

    fn install_carrier(
        &self,
        source: &veles_bridge_installer::PackageSource,
        destination: &ValidatedPath,
    ) -> InstallResult<()> {
        self.inner.install_carrier(source, destination)
    }

    fn remove_owned(&self, entry: &veles_bridge_installer::OwnedEntry) -> InstallResult<()> {
        self.inner.remove_owned(entry)
    }

    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()> {
        self.inner.flush_parent(path)
    }
}

/// Behaviours the cleanup/prune contract tests need to inject at a precise
/// boundary, while every other call stays a real [`FakePlatform`] operation.
enum Hook {
    /// Fail removing one exact owned path (an installer-private staging artifact).
    FailRemoval { path: String },
    /// Replace the live registration once it names an executable under `prefix`.
    Reregister {
        when_executable_under: String,
        replacement: Vec<u8>,
    },
    /// Die (as a killed process would) before the first removal under `prefix`.
    CrashBeforeRemoval { prefix: String },
    /// A host launches while the payload is being staged: every inspection from
    /// the first staged write on reports an owned host running.
    LaunchAfterStaging,
    /// Replace `state/staging` with a symlink to `outside` before inspection `at`.
    #[cfg(unix)]
    SymlinkStaging { outside: PathBuf, at: usize },
    /// Replace `state/staging` with a regular file before inspection `at`.
    ReplaceStagingWithFile { at: usize },
    /// Replace `state/staging` with a symlink to `target` before inspection `at`.
    #[cfg(unix)]
    SymlinkStagingTo { target: PathBuf, at: usize },
}

/// A [`Platform`] that delegates every call to [`FakePlatform`] and applies one
/// injected [`Hook`]. Every method is delegated so the inner call log and faults
/// stay complete.
struct HookedPlatform {
    inner: FakePlatform,
    hook: Hook,
    inspections: AtomicUsize,
    fired: AtomicUsize,
}

impl HookedPlatform {
    fn new(hook: Hook) -> Self {
        Self::trusting(hook, Vec::new())
    }

    /// As `new`, with an OS that vouches for the installed carriers of `proofs`: an
    /// install over a prior release authenticates that release in place.
    fn trusting(hook: Hook, proofs: Vec<TrustProof>) -> Self {
        Self {
            inner: FakePlatform::with_faults(Faults {
                host_state: HostState::Quiescent,
                trust_by_carrier: proofs,
                ..Faults::default()
            }),
            hook,
            inspections: AtomicUsize::new(0),
            fired: AtomicUsize::new(0),
        }
    }
}

impl Platform for HookedPlatform {
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock> {
        self.inner.lock(paths)
    }

    fn write_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        self.inner.write_durable(path, bytes)
    }

    fn replace_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        self.inner.replace_durable(path, bytes)
    }

    fn write_state(
        &self,
        paths: &InstallPaths,
        document: StateDocument,
        bytes: &[u8],
    ) -> InstallResult<()> {
        self.inner.write_state(paths, document, bytes)
    }

    fn read_state(
        &self,
        paths: &InstallPaths,
        document: StateDocument,
    ) -> InstallResult<Option<Vec<u8>>> {
        self.inner.read_state(paths, document)
    }

    fn quarantine_state(&self, paths: &InstallPaths, document: StateDocument) -> InstallResult<()> {
        self.inner.quarantine_state(paths, document)
    }

    fn create_directory(&self, path: &ValidatedPath) -> InstallResult<()> {
        self.inner.create_directory(path)
    }

    fn replace_manifest(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        self.inner.replace_manifest(staged, live)
    }

    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>> {
        self.inner.read_registration(paths)
    }

    fn publish_manifest(&self, paths: &InstallPaths, value: &Registration) -> InstallResult<()> {
        self.inner.publish_manifest(paths, value)
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
        let state = self.inner.inspect_hosts(paths)?;
        let inspection = self.inspections.fetch_add(1, Ordering::SeqCst);
        let _ = inspection;
        match &self.hook {
            Hook::Reregister {
                when_executable_under,
                replacement,
            } if self.fired.load(Ordering::SeqCst) == 0 => {
                if let Some(live) = self.inner.read_registration(paths)?
                    && parse_manifest(live.manifest()).is_ok_and(|manifest| {
                        manifest
                            .executable()
                            .is_ok_and(|path| path.starts_with(when_executable_under))
                    })
                {
                    fs::write(paths.registration_path(), replacement)
                        .expect("injected re-registration");
                    self.fired.fetch_add(1, Ordering::SeqCst);
                }
            }
            Hook::LaunchAfterStaging
                if self.inner.calls().contains(&PlatformCall::WriteDurable) =>
            {
                return Ok(HostState::Active);
            }
            #[cfg(unix)]
            Hook::SymlinkStaging { outside, at } if inspection == *at => {
                let staging = paths.staging();
                let moved = paths.state().join("staging.before-cleanup");
                fs::rename(staging, &moved).expect("move staging aside");
                #[cfg(unix)]
                std::os::unix::fs::symlink(outside, staging).expect("staging symlink");
            }
            Hook::ReplaceStagingWithFile { at } if inspection == *at => {
                let staging = paths.staging();
                fs::remove_dir(staging).expect("remove the empty staging directory");
                fs::write(staging, b"foreign staging path").expect("staging file");
            }
            #[cfg(unix)]
            Hook::SymlinkStagingTo { target, at } if inspection == *at => {
                let staging = paths.staging();
                fs::remove_dir(staging).expect("remove the empty staging directory");
                std::os::unix::fs::symlink(target, staging).expect("staging symlink");
            }
            _ => {}
        }
        Ok(state)
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
        source: &veles_bridge_installer::PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        self.inner.verify_trust(source, expected)
    }

    fn carrier_entries(
        &self,
        source: &veles_bridge_installer::PackageSource,
    ) -> InstallResult<Vec<veles_bridge_installer::CarrierEntry>> {
        self.inner.carrier_entries(source)
    }

    fn install_carrier(
        &self,
        source: &veles_bridge_installer::PackageSource,
        destination: &ValidatedPath,
    ) -> InstallResult<()> {
        self.inner.install_carrier(source, destination)
    }

    fn verify_retained(
        &self,
        source: &veles_bridge_installer::PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        self.inner.verify_retained(source, expected)
    }

    fn remove_owned(&self, entry: &veles_bridge_installer::OwnedEntry) -> InstallResult<()> {
        match &self.hook {
            Hook::FailRemoval { path } if entry.claim().path == *path => {
                Err(InstallFailure::new(FailureCode::Io))
            }
            Hook::CrashBeforeRemoval { prefix }
                if entry.claim().path.starts_with(prefix)
                    && self.fired.fetch_add(1, Ordering::SeqCst) == 0 =>
            {
                panic!(
                    "injected process death before pruning {}",
                    entry.claim().path
                );
            }
            _ => self.inner.remove_owned(entry),
        }
    }

    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()> {
        self.inner.flush_parent(path)
    }
}

/// R2: `prior_usable` on a failure the operation did not classify, and the state
/// `inspect` reports, are the same predicate recovery uses: claims, resources,
/// launch permission and the manifest's selected executable - not merely "the
/// executable is a regular file".
#[test]
fn prior_usable_and_inspect_share_one_complete_usability_predicate() {
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;

    type Damage = fn(&Path);
    #[allow(unused_mut)]
    let mut damages: Vec<(&str, Damage)> = vec![
        ("deleted resource", |version| {
            fs::remove_file(version.join("licenses/NOTICE")).expect("delete a resource");
        }),
        ("altered resource", |version| {
            fs::write(version.join("licenses/NOTICE"), b"tampered").expect("alter a resource");
        }),
        ("altered, still-regular executable", |version| {
            fs::write(version.join("host.exe"), b"replaced by something else")
                .expect("alter the host");
        }),
    ];
    #[cfg(unix)]
    damages.push(("stripped execute bit", |version| {
        fs::set_permissions(version.join("host.exe"), fs::Permissions::from_mode(0o600))
            .expect("strip the bit");
    }));
    let mut wrong: Vec<String> = Vec::new();
    for (name, damage) in damages {
        let fixture = Fixture::windows();
        let paths = &fixture.paths;
        let v1 = fixture.package(
            "v1",
            "1.0.0",
            HOST_V1,
            NOTICE_V1,
            policy(1, Vec::new(), Vec::new()),
        );
        fixture.install(&v1);
        let version = version_dir(paths, "1.0.0", v1.descriptor.payload_id);

        // Healthy baseline: both the wrapper and inspect call it usable.
        let healthy = run_operation(&fixture.platform(), paths, Operation::Install, None)
            .expect_err("an install needs a package");
        assert!(healthy.prior_usable, "{name}: healthy installation");
        let inspected = run_operation(&fixture.platform(), paths, Operation::Inspect, None)
            .expect("inspect reports");
        assert!(inspected.prior_usable, "{name}: healthy inspect");

        damage(&version);

        let failure = run_operation(&fixture.platform(), paths, Operation::Install, None)
            .expect_err("an install needs a package");
        assert_eq!(failure.code, FailureCode::InvalidInput, "{name}");
        if failure.prior_usable {
            wrong.push(format!(
                "{name}: a damaged installation was reported prior_usable in a failure"
            ));
        }
        let inspected = run_operation(&fixture.platform(), paths, Operation::Inspect, None)
            .expect("inspect reports");
        if inspected.state != STATE_DAMAGED
            || inspected.prior_usable
            || inspected.failure != Some(FailureCode::RepairPackageRequired)
        {
            wrong.push(format!("{name}: inspect reported {inspected:?}"));
        }
    }
    assert!(wrong.is_empty(), "inconsistent usability: {wrong:#?}");
}

#[test]
fn same_version_repairs_missing_files_not_trust() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let installed = fixture.install(&v1);
    assert_eq!(installed.state, veles_bridge_installer::STATE_INSTALLED);
    let p1 = v1.descriptor.payload_id;
    let installed_dir = version_dir(paths, "1.0.0", p1);
    let saved_registration = fixture.registration();
    let saved_receipt = receipt(paths).expect("the install commits a receipt");
    let profile = fixture.seed_chrome_profile();

    // A healthy install: repair revalidates everything and restores nothing. It
    // never re-runs the OS trust assessment of the local tree.
    let platform = fixture.platform();
    let healthy = run_operation(&platform, paths, Operation::Repair, Some(authenticate(&v1)))
        .expect("a healthy installation repairs without error");
    assert_eq!(healthy.operation, Operation::Repair);
    assert_eq!(healthy.state, STATE_REPAIRED);
    assert_eq!(healthy.active_payload, Some(p1));
    assert!(healthy.prior_usable);
    assert!(healthy.failure.is_none());
    assert!(
        platform.mutations().is_empty(),
        "a healthy repair mutates nothing: {:?}",
        platform.mutations()
    );
    assert!(!platform.calls().contains(&PlatformCall::VerifyTrust));

    // Damage the managed tree: one managed file and the registration are gone.
    fs::remove_file(installed_dir.join("licenses/NOTICE")).expect("remove owned notice");
    fs::remove_file(paths.registration_path()).expect("remove registration");
    // A same-version *install* rerun repairs the installation rather than
    // staging another version or failing on the existing version directory.
    let platform = fixture.platform();
    let repaired = run_operation(
        &platform,
        paths,
        Operation::Install,
        Some(authenticate(&v1)),
    )
    .expect("a same-version repair restores missing managed files");
    assert_eq!(repaired.operation, Operation::Repair);
    assert_eq!(repaired.state, STATE_REPAIRED);
    assert_eq!(repaired.active_payload, Some(p1));
    assert_eq!(
        fs::read(installed_dir.join("licenses/NOTICE")).expect("restored notice"),
        NOTICE_V1,
        "repair restores the authenticated bytes of the missing file"
    );
    assert_eq!(
        fs::read(installed_dir.join("host.exe")).expect("host payload"),
        HOST_V1
    );
    assert_eq!(
        fs::read(paths.registration_path()).expect("restored registration"),
        saved_registration,
        "repair restores the exact committed registration bytes"
    );
    assert_eq!(
        receipt(paths).expect("the receipt survives a repair"),
        saved_receipt,
        "repair is a file restoration, not a new transaction or trust record"
    );
    assert_eq!(
        fs::read_dir(paths.versions())
            .expect("versions dir")
            .count(),
        1,
        "repair never stages another version"
    );
    assert!(
        !platform.calls().contains(&PlatformCall::VerifyTrust),
        "repair does not re-assess trust of local content"
    );
    assert_eq!(fs::read(&profile).expect("profile"), PROFILE_SENTINEL);

    // A conflicting registration is reported, never overwritten, and the damaged
    // managed file is left alone rather than "repaired" on top of foreign state.
    let foreign = fixture.foreign_registration();
    fs::write(paths.registration_path(), &foreign).expect("foreign registration");
    fs::remove_file(installed_dir.join("licenses/NOTICE")).expect("remove owned notice again");
    let before = snapshot(fixture.temp.path());
    let platform = fixture.platform();
    let failure = run_operation(&platform, paths, Operation::Repair, Some(authenticate(&v1)))
        .expect_err("a conflicting registration is not silently replaced");
    assert_eq!(failure.code, FailureCode::ForeignState);
    assert!(
        !failure.prior_usable,
        "a foreign registration means the installation is not usable"
    );
    assert!(
        platform.mutations().is_empty(),
        "the conflict is refused before any mutation: {:?}",
        platform.mutations()
    );
    assert_eq!(snapshot(fixture.temp.path()), before);
    assert_eq!(
        fs::read(paths.registration_path()).expect("foreign registration"),
        foreign
    );
    assert_eq!(
        fs::read(&profile).expect("profile untouched"),
        PROFILE_SENTINEL
    );
}

/// Residual (a), investigated end to end: can a restriction that ONLY a repair
/// package's signed policy carries reach the persisted state - and so be lost,
/// because no retained signed evidence would back it?
///
/// It cannot be reached. A repair package for the installed version and payload
/// whose signed policy differs (here: one extra denial, same epoch, so it is a pure
/// tightening that `effective_policy(Repair)` would accept) has a different
/// descriptor and therefore a different retained document and carrier digest than
/// the installation claims. The repair plan refuses that as foreign state before any
/// write, on an undamaged and on a damaged installation, through both the Repair
/// operation and the same-version Install route. The refusal is pre-existing
/// repair-tightening behaviour, not something added to make this test pass.
#[test]
fn a_repair_package_carrying_a_new_restriction_is_refused_before_any_write() {
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let newly_denied = Digest::of(b"a payload only the repair package denies");
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), vec![p1]),
    );
    // The same release identity, signed with one more denial at the same epoch.
    let tighter = fixture.package(
        "v1-tighter",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, vec![newly_denied], vec![p1]),
    );
    assert_eq!(tighter.descriptor.payload_id, v1.descriptor.payload_id);
    assert_ne!(
        Digest::of(tighter.proof.carrier()),
        Digest::of(v1.proof.carrier())
    );
    fixture.install(&v1);
    let installed_dir = version_dir(paths, "1.0.0", p1);

    for damaged in [false, true] {
        if damaged {
            fs::remove_file(installed_dir.join("licenses/NOTICE")).expect("remove owned notice");
        }
        for operation in [Operation::Repair, Operation::Install] {
            let context = format!("damaged={damaged} {operation:?}");
            let before = snapshot(fixture.temp.path());
            let saved = receipt(paths).expect("receipt");
            let platform = fixture.platform();
            let outcome = run_operation(&platform, paths, operation, Some(authenticate(&tighter)));
            let failure = outcome.expect_err(&format!(
                "{context}: the repair-only restriction must not be accepted"
            ));
            assert!(
                failure.code == FailureCode::ForeignState,
                "{context}: refused as {:?}",
                failure.code
            );
            assert!(
                platform.mutations().is_empty(),
                "{context}: nothing is written: {:?}",
                platform.mutations()
            );
            assert_eq!(
                snapshot(fixture.temp.path()),
                before,
                "{context}: byte-identical tree"
            );
            assert_eq!(receipt(paths).expect("receipt"), saved, "{context}");
            assert!(
                !receipt(paths)
                    .expect("receipt")
                    .release_policy
                    .denies(&newly_denied),
                "{context}: the restriction was neither persisted nor half-applied"
            );
        }
    }
}

#[test]
fn older_install_does_not_silent_downgrade() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(1, Vec::new(), Vec::new()),
    );
    let p2 = v2.descriptor.payload_id;
    let installed = fixture.install(&v2);
    assert_eq!(installed.state, veles_bridge_installer::STATE_INSTALLED);
    assert_eq!(installed.active_payload, Some(p2));

    let v1_dir = version_dir(paths, "1.0.0", v1.descriptor.payload_id);
    let registration = fixture.registration();
    let saved_receipt = receipt(paths).expect("the install commits a receipt");

    // An older installer is refused before it can touch anything.
    let platform = fixture.platform();
    let failure = run_operation(
        &platform,
        paths,
        Operation::Install,
        Some(authenticate(&v1)),
    )
    .expect_err("an older installer never downgrades silently");
    assert_eq!(failure.code, FailureCode::PolicyDenied);
    assert!(
        failure.prior_usable,
        "the newer installation is untouched and still usable"
    );
    assert!(
        platform.mutations().is_empty(),
        "the downgrade is refused before any mutation: {:?}",
        platform.mutations()
    );
    assert!(
        !v1_dir.exists(),
        "the older payload is never staged over the installed release"
    );
    assert_eq!(fixture.registration(), registration);
    assert_eq!(receipt(paths).expect("receipt"), saved_receipt);
    assert_eq!(saved_receipt.payload_id, p2);

    // An older source is not a repair source either: ask for the signed download.
    let platform = fixture.platform();
    assert_eq!(
        failure_code(run_operation(
            &platform,
            paths,
            Operation::Repair,
            Some(authenticate(&v1))
        )),
        FailureCode::RepairPackageRequired
    );
    assert!(platform.mutations().is_empty());
    assert!(!v1_dir.exists());

    // A newer installer is an ordinary upgrade: the previous version is retained.
    let v3 = fixture.package(
        "v3",
        "3.0.0",
        HOST_V3,
        NOTICE_V3,
        policy(1, Vec::new(), Vec::new()),
    );
    let p3 = v3.descriptor.payload_id;
    let platform = fixture.platform();
    let upgraded = run_operation(
        &platform,
        paths,
        Operation::Install,
        Some(authenticate(&v3)),
    )
    .expect("a newer release upgrades");
    assert_eq!(upgraded.state, veles_bridge_installer::STATE_INSTALLED);
    assert_eq!(upgraded.active_payload, Some(p3));
    let upgraded_receipt = receipt(paths).expect("upgrade commits a receipt");
    assert_eq!(upgraded_receipt.version, "3.0.0");
    assert_eq!(
        upgraded_receipt
            .prior
            .as_ref()
            .map(|prior| prior.payload_id),
        Some(p2),
        "the upgraded install retains exactly one verified prior version"
    );
    assert_eq!(
        fs::read(version_dir(paths, "2.0.0", p2).join("host.exe")).expect("retained prior"),
        HOST_V2
    );
}

#[test]
fn rollback_rejects_known_denial() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let p2 = payload_id(HOST_V2, NOTICE_V2);

    // The retained 1.0.0 signature knows no denial and permits its own payload.
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), vec![p1]),
    );
    // The installed 2.0.0 policy records the denial and still permits a rollback.
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, vec![p1], vec![p1]),
    );
    assert_eq!(v1.descriptor.payload_id, p1);
    assert_eq!(v2.descriptor.payload_id, p2);
    assert!(
        v1.descriptor.policy.denied_payloads.is_empty(),
        "the stale signature cannot know the later denial"
    );
    fixture.install(&v1);
    fixture.install(&v2);

    let v1_dir = version_dir(paths, "1.0.0", p1);
    let registration = fixture.registration();
    let before = snapshot(fixture.temp.path());

    // A correctly signed older package cannot erase the recorded denial: the
    // persisted policy and the retained policy are both applied.
    let platform = fixture.platform_trusting(&v1.proof);
    let failure = run_operation(
        &platform,
        paths,
        Operation::Rollback,
        Some(authenticate(&v1)),
    )
    .expect_err("a denied payload is never rolled back");
    assert_eq!(failure.code, FailureCode::PolicyDenied);
    assert!(
        failure.prior_usable,
        "the denied rollback leaves the installed release usable"
    );
    assert!(
        platform.mutations().is_empty(),
        "the denial is decided before any mutation: {:?}",
        platform.mutations()
    );
    assert_eq!(snapshot(fixture.temp.path()), before);
    assert_eq!(receipt(paths).expect("receipt").payload_id, p2);
    assert_eq!(fixture.registration(), registration);
    assert_eq!(
        fs::read(v1_dir.join("host.exe")).expect("retained prior payload"),
        HOST_V1,
        "the rejected rollback still retains the prior version"
    );

    // The retained carrier makes rollback independent of a supplied package, but
    // the persisted denial still applies to the recorded prior target.
    let platform = fixture.platform_trusting(&v1.proof);
    let failure = run_operation(&platform, paths, Operation::Rollback, None)
        .expect_err("a denied retained target is never re-activated");
    assert_eq!(failure.code, FailureCode::PolicyDenied);
    assert!(platform.mutations().is_empty());

    // With nothing installed there is no retained target at all.
    let empty = Fixture::windows();
    let platform = empty.platform();
    let failure = run_operation(&platform, &empty.paths, Operation::Rollback, None)
        .expect_err("rollback needs a retained prior installation");
    assert_eq!(failure.code, FailureCode::NoRollback);
    assert!(platform.mutations().is_empty());
}

#[test]
fn missing_repair_source_requests_signed_download() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let p1 = v1.descriptor.payload_id;

    // Nothing is installed: repair has no source and asks for the signed release.
    let platform = fixture.platform();
    let failure = run_operation(&platform, paths, Operation::Repair, None)
        .expect_err("a repair without a package cannot trust local content");
    assert_eq!(failure.code, FailureCode::RepairPackageRequired);
    assert!(!failure.prior_usable);
    assert!(platform.mutations().is_empty());
    let absent = run_operation(&fixture.platform(), paths, Operation::Inspect, None)
        .expect("inspect reads an empty layout");
    assert_eq!(absent.state, STATE_ABSENT);
    assert!(absent.active_payload.is_none());
    assert!(absent.failure.is_none());

    fixture.install(&v1);
    let installed_dir = version_dir(paths, "1.0.0", p1);

    // A damaged active payload is still repaired only from a signed source: the
    // installer does not trust or go online for local recovery content.
    fs::remove_file(installed_dir.join("host.exe")).expect("remove the managed host");
    let platform = fixture.platform();
    let failure = run_operation(&platform, paths, Operation::Repair, None)
        .expect_err("damaged local content is not trusted");
    assert_eq!(failure.code, FailureCode::RepairPackageRequired);
    assert!(!failure.prior_usable);
    assert!(
        !installed_dir.join("host.exe").exists(),
        "nothing is restored without an authenticated source"
    );
    assert!(platform.mutations().is_empty());

    // A package for another version is not a repair source either.
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(1, Vec::new(), Vec::new()),
    );
    let platform = fixture.platform();
    assert_eq!(
        failure_code(run_operation(
            &platform,
            paths,
            Operation::Repair,
            Some(authenticate(&v2))
        )),
        FailureCode::RepairPackageRequired
    );
    assert!(platform.mutations().is_empty());

    // A package that changes after authentication is no longer a repair source:
    // the engine re-reads and re-hashes every source, and asks for a fresh signed
    // download instead of trusting the altered bytes.
    let authenticated = authenticate(&v1);
    fs::write(
        v1.root.join("payload/host.exe"),
        b"tampered after authentication",
    )
    .expect("tampered package source");
    let platform = fixture.platform();
    assert_eq!(
        failure_code(run_operation(
            &platform,
            paths,
            Operation::Repair,
            Some(authenticated)
        )),
        FailureCode::RepairPackageRequired,
        "an altered repair source asks for a fresh signed download"
    );
    assert!(
        !installed_dir.join("host.exe").exists(),
        "no altered source bytes are ever installed"
    );

    // Inspect reports the damage and the required signed download, and still
    // mutates nothing; it exposes only bounded metadata.
    let before = snapshot(fixture.temp.path());
    let platform = fixture.platform();
    let report = run_operation(&platform, paths, Operation::Inspect, None).expect("inspect reads");
    assert_eq!(report.operation, Operation::Inspect);
    assert_eq!(report.state, STATE_DAMAGED);
    assert_eq!(report.failure, Some(FailureCode::RepairPackageRequired));
    assert_eq!(report.active_payload, Some(p1));
    assert!(!report.prior_usable);
    assert!(platform.mutations().is_empty());
    assert!(!platform.calls().contains(&PlatformCall::VerifyTrust));
    assert_eq!(snapshot(fixture.temp.path()), before);
}

#[test]
fn uninstall_twice_preserves_foreign_files_and_registration() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let p1 = v1.descriptor.payload_id;
    fixture.install(&v1);
    let installed_dir = version_dir(paths, "1.0.0", p1);
    let unowned = fixture.seed_unowned_assets();
    let profile = fixture.seed_chrome_profile();
    let saved_registration = fixture.registration();

    // A foreign registration is reported and preserved; nothing is removed,
    // because the installer cannot prove the path is still its own.
    let foreign = fixture.foreign_registration();
    fs::write(paths.registration_path(), &foreign).expect("foreign registration");
    let before = snapshot(fixture.temp.path());
    let platform = fixture.platform();
    let failure = run_operation(&platform, paths, Operation::Uninstall, None)
        .expect_err("a foreign registration is never removed");
    assert_eq!(failure.code, FailureCode::ForeignState);
    assert!(
        platform.mutations().is_empty(),
        "the foreign registration check precedes every mutation: {:?}",
        platform.mutations()
    );
    assert_eq!(snapshot(fixture.temp.path()), before);
    assert_eq!(
        fs::read(paths.registration_path()).expect("foreign registration"),
        foreign
    );
    let inspected = run_operation(&fixture.platform(), paths, Operation::Inspect, None)
        .expect("inspect reports the conflicting registration");
    assert_eq!(inspected.state, STATE_FOREIGN);
    assert_eq!(inspected.failure, Some(FailureCode::ForeignState));
    fs::write(paths.registration_path(), &saved_registration).expect("restore registration");
    let before = snapshot(fixture.temp.path());
    let platform = fixture.platform();
    let report = run_operation(&platform, paths, Operation::Uninstall, None)
        .expect("the owned registration uninstalls");
    assert_eq!(report.operation, Operation::Uninstall);
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(!report.prior_usable);
    assert!(report.active_payload.is_none());
    assert!(report.failure.is_none());
    assert_eq!(
        report.warnings,
        vec![WarningCode::LeftoverFiles],
        "unowned files left inside the private layout are reported, not deleted"
    );
    assert!(
        !paths.registration_path().exists(),
        "the owned registration is removed"
    );
    assert!(!installed_dir.exists(), "the owned payload is removed");
    assert!(receipt(paths).is_none(), "the receipt is removed last");
    assert!(
        matches!(journal_state(paths), JournalState::None),
        "a completed uninstall releases its journal"
    );
    fixture.assert_unowned_assets(&unowned, "after uninstall");
    assert_eq!(
        fs::read(&profile).expect("profile untouched"),
        PROFILE_SENTINEL
    );
    let after = snapshot(fixture.temp.path());
    assert_changes_confined(&fixture.temp, paths, &before, &after, "uninstall");

    // Uninstalling twice is idempotent for everything this product owns. The
    // preserved foreign files are reported again because they still block
    // private-layout removal; the second run only removes the lock file it had to
    // create, and the third changes nothing at all.
    let platform = fixture.platform();
    let again = run_operation(&platform, paths, Operation::Uninstall, None)
        .expect("a second uninstall is idempotent");
    assert_eq!(again.state, STATE_UNINSTALLED);
    assert_eq!(again.warnings, vec![WarningCode::LeftoverFiles]);
    assert!(
        platform
            .mutations()
            .iter()
            .all(|call| *call == PlatformCall::RemoveOwned),
        "only the installer's own fixed layout is removed: {:?}",
        platform.mutations()
    );
    fixture.assert_unowned_assets(&unowned, "after the second uninstall");
    let settled = snapshot(fixture.temp.path());
    let platform = fixture.platform();
    let third = run_operation(&platform, paths, Operation::Uninstall, None)
        .expect("the third uninstall is idempotent");
    assert_eq!(third.state, STATE_UNINSTALLED);
    assert!(
        platform
            .mutations()
            .iter()
            .all(|call| *call == PlatformCall::RemoveOwned),
        "{:?}",
        platform.mutations()
    );
    assert_eq!(snapshot(fixture.temp.path()), settled);
    fixture.assert_unowned_assets(&unowned, "after the third uninstall");
    let _ = after;

    // A damaged install whose registration was removed by hand still uninstalls
    // its owned payload: the receipt, kept until the end, is the removal intent.
    let damaged = Fixture::windows();
    let damaged_package = damaged.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let damaged_payload = damaged_package.descriptor.payload_id;
    damaged.install(&damaged_package);
    fs::remove_file(damaged.paths.registration_path()).expect("remove registration");
    let report = run_operation(
        &damaged.platform(),
        &damaged.paths,
        Operation::Uninstall,
        None,
    )
    .expect("an install without a registration still uninstalls");
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(!version_dir(&damaged.paths, "1.0.0", damaged_payload).exists());
    assert!(receipt(&damaged.paths).is_none());

    // A crafted receipt cannot authorize a path outside the fixed layout.
    for forged_claim in ["../outside.txt", "versions/../../outside.txt"] {
        let forged = Fixture::windows();
        let package = forged.package(
            "v1",
            "1.0.0",
            HOST_V1,
            NOTICE_V1,
            policy(1, Vec::new(), Vec::new()),
        );
        let payload = package.descriptor.payload_id;
        forged.install(&package);
        let outside = forged.temp.path().join("outside.txt");
        fs::write(&outside, b"user data").expect("outside file");
        let mut document: serde_json::Value =
            serde_json::from_slice(&fs::read(forged.paths.receipt_path()).expect("receipt bytes"))
                .expect("receipt JSON");
        document["ownership"]["owned"]
            .as_array_mut()
            .expect("owned claims")
            .push(serde_json::json!({
                "path": forged_claim,
                "kind": "file",
                "sha256": Digest::of(b"user data"),
            }));
        fs::write(
            forged.paths.receipt_path(),
            serde_json::to_vec(&document).expect("forged receipt JSON"),
        )
        .expect("forged receipt");
        let platform = forged.platform();
        let failure = run_operation(&platform, &forged.paths, Operation::Uninstall, None)
            .expect_err("a forged receipt is refused, never executed as authority");
        assert_eq!(
            failure.code,
            FailureCode::RecoveryRequired,
            "{forged_claim}"
        );
        assert!(platform.mutations().is_empty(), "{forged_claim}");
        assert_eq!(
            fs::read(&outside).expect("outside file intact"),
            b"user data",
            "{forged_claim}: no claim authorizes a path outside the layout"
        );
        assert!(
            version_dir(&forged.paths, "1.0.0", payload).exists(),
            "{forged_claim}: the owned payload is untouched"
        );
    }

    // The macOS layout puts the registration in Chrome's Native Messaging
    // directory: only that one file may be touched, never the profile beside it.
    let mac = Fixture::macos();
    let mac_package = mac.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    mac.install(&mac_package);
    let hosts = mac
        .paths
        .registration_path()
        .parent()
        .expect("hosts directory")
        .to_path_buf();
    let sibling = hosts.join("another-product.json");
    fs::write(&sibling, b"another product").expect("sibling manifest");
    let profile = mac.seed_chrome_profile();
    let before = snapshot(mac.temp.path());
    run_operation(&mac.platform(), &mac.paths, Operation::Uninstall, None)
        .expect("the macOS layout uninstalls");
    assert!(!mac.paths.registration_path().exists());
    assert_eq!(
        fs::read(&sibling).expect("sibling untouched"),
        b"another product",
        "another product's registration is preserved"
    );
    assert_eq!(
        fs::read(&profile).expect("profile untouched"),
        PROFILE_SENTINEL
    );
    let after = snapshot(mac.temp.path());
    assert_changes_confined(&mac.temp, &mac.paths, &before, &after, "macOS uninstall");
}

#[test]
fn chrome_relaunch_during_remove_reports_cleanup_pending() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let p1 = v1.descriptor.payload_id;
    fixture.install(&v1);
    let installed_dir = version_dir(paths, "1.0.0", p1);
    let unowned = fixture.seed_unowned_assets();

    // The first inspection is quiescent, so removal is authorized; Chrome then
    // launches the host again before the payload is deleted.
    let platform = RelaunchingPlatform::new(1);
    let failure = run_operation_within(&platform, paths, Operation::Uninstall, None, SHORT_WAIT)
        .expect_err("a relaunched owned host defers removal");
    assert_eq!(failure.code, FailureCode::CleanupPending);
    assert!(
        !failure.prior_usable,
        "the registration is already gone, so the connector is not usable"
    );

    // The registration removal happened, the payload did not.
    assert!(!paths.registration_path().exists());
    assert_eq!(
        fs::read(installed_dir.join("host.exe")).expect("retained payload"),
        HOST_V1,
        "a possibly executing host is never removed"
    );
    assert!(
        receipt(paths).is_some(),
        "the receipt is removed only at the end"
    );

    // The partial uninstall is durably journaled, so a retry finishes it and
    // recovery never mistakes it for an activation to complete or roll back.
    match journal_state(paths) {
        JournalState::Pending(journal) => {
            assert_eq!(journal.operation, Operation::Uninstall);
            assert_eq!(
                journal.phase,
                veles_bridge_installer::Phase::CleanupPending,
                "the deferred removal is recorded as pending cleanup"
            );
        }
        other => panic!("a partial uninstall leaves a durable journal, not {other:?}"),
    }
    assert_eq!(
        failure_code(veles_bridge_installer::recover(
            &PortablePlatform::new(),
            paths
        )),
        FailureCode::RecoveryRequired,
        "recovery does not re-activate a version for a partial uninstall"
    );
    assert!(
        platform
            .calls()
            .iter()
            .filter(|call| **call == PlatformCall::InspectHosts)
            .count()
            >= 2,
        "removal rechecks quiescence after the registration is gone"
    );
    assert!(!platform.calls().contains(&PlatformCall::VerifyTrust));
    // Inspection tells the frontend to resume the uninstall, never that the
    // product is still healthy.
    let inspected = run_operation(&fixture.platform(), paths, Operation::Inspect, None)
        .expect("inspect reads a partial uninstall");
    assert_eq!(inspected.state, STATE_DAMAGED);
    assert_eq!(inspected.failure, Some(FailureCode::CleanupPending));
    assert!(!inspected.prior_usable);
    fixture.assert_unowned_assets(&unowned, "after the deferred removal");

    // Once the host exits, the retried uninstall completes idempotently.
    let platform = fixture.platform();
    let report = run_operation(&platform, paths, Operation::Uninstall, None)
        .expect("the retried uninstall completes");
    assert_eq!(report.operation, Operation::Uninstall);
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(!installed_dir.exists());
    assert!(receipt(paths).is_none());
    assert!(matches!(journal_state(paths), JournalState::None));
    fixture.assert_unowned_assets(&unowned, "after the retried uninstall");
    let platform = fixture.platform();
    let again = run_operation(&platform, paths, Operation::Uninstall, None)
        .expect("the third uninstall is idempotent");
    assert_eq!(again.state, STATE_UNINSTALLED);
    assert!(
        platform
            .mutations()
            .iter()
            .all(|call| *call == PlatformCall::RemoveOwned),
        "only the installer's own fixed layout is removed: {:?}",
        platform.mutations()
    );
}

#[test]
fn rollback_activates_the_retained_verified_payload() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let p2 = payload_id(HOST_V2, NOTICE_V2);
    // Both signed policies make the retained 1.0.0 payload rollback-eligible.
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), vec![p1]),
    );
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, Vec::new(), vec![p1]),
    );
    fixture.install(&v1);
    fixture.install(&v2);
    let retained_v1 = version_dir(paths, "1.0.0", p1);
    let retained_v2 = version_dir(paths, "2.0.0", p2);
    let before = snapshot(fixture.temp.path());

    let platform = fixture.platform_trusting(&v1.proof);
    let report = run_operation(
        &platform,
        paths,
        Operation::Rollback,
        Some(authenticate(&v1)),
    )
    .expect("the retained, rollback-eligible prior release is re-activated");
    assert_eq!(report.operation, Operation::Rollback);
    assert_eq!(report.state, STATE_ROLLED_BACK);
    assert_eq!(report.active_payload, Some(p1));
    assert!(report.prior_usable);
    assert!(report.failure.is_none());

    // The live registration selects the retained version's executable again.
    let live = fixture.registration();
    let manifest = parse_manifest(&live).expect("the live registration is complete JSON");
    assert_eq!(manifest.name, paths.registration_name());
    let executable = manifest.executable().expect("absolute executable");
    assert!(
        path_is_within(paths.root(), &executable),
        "the rolled-back executable stays inside the install root"
    );
    assert_eq!(fs::read(&executable).expect("rolled-back host"), HOST_V1);

    // Both verified versions are retained: the rollback target and the release
    // it came from, for a later upgrade or a complete uninstall.
    assert_eq!(fs::read(retained_v1.join("host.exe")).expect("v1"), HOST_V1);
    assert_eq!(
        fs::read(retained_v2.join("host.exe")).expect("v2 retained"),
        HOST_V2,
        "a rollback never removes the release it rolled back from"
    );
    assert_eq!(
        fs::read_dir(paths.versions())
            .expect("versions dir")
            .count(),
        2
    );

    // The receipt names the rolled-back release as current, the other as the
    // retained prior, and keeps the live policy epoch with the composed rules.
    let receipt = receipt(paths).expect("the rollback commits a receipt");
    assert_eq!(receipt.operation, Operation::Rollback);
    assert_eq!(receipt.version, "1.0.0");
    assert_eq!(receipt.payload_id, p1);
    assert_eq!(receipt.manifest_sha256, Digest::of(&live));
    assert_eq!(
        receipt.prior.as_ref().map(|prior| prior.payload_id),
        Some(p2)
    );
    assert_eq!(
        receipt.release_policy.epoch, 2,
        "the live policy generation is kept, never regressed by the older package"
    );
    assert!(receipt.release_policy.permits_rollback(&p1));
    assert!(
        matches!(journal_state(paths), JournalState::None),
        "the rollback releases its journal"
    );
    assert!(!platform.calls().contains(&PlatformCall::VerifyTrust));
    let after = snapshot(fixture.temp.path());
    assert_changes_confined(&fixture.temp, paths, &before, &after, "rollback");

    // The receipt now retains 2.0.0 as its prior: 1.0.0 is no longer a rollback
    // target, and the refusal mutates nothing.
    let platform = fixture.platform_trusting(&v1.proof);
    let failure = run_operation(
        &platform,
        paths,
        Operation::Rollback,
        Some(authenticate(&v1)),
    )
    .expect_err("the rolled-back-to release is no longer the retained prior");
    assert_eq!(failure.code, FailureCode::NoRollback);
    assert!(failure.prior_usable);
    assert!(platform.mutations().is_empty());
    assert_eq!(fixture.registration(), live);

    // The rolled-back-to version's retained metadata and signed carrier are
    // owned claims of the new receipt. Without them a later repair would see
    // unclaimed files and uninstall could never remove the version directory.
    for (version, payload_id) in [("1.0.0", p1), ("2.0.0", p2)] {
        let directory = paths.version_dir_relative(
            &Version::parse(version).expect("fixture version"),
            payload_id,
        );
        for relative in [
            format!("{directory}/{RETAINED_FILE_NAME}"),
            format!("{directory}/carrier.exe"),
        ] {
            assert!(
                receipt
                    .ownership
                    .owned
                    .iter()
                    .any(|claim| claim.path == relative),
                "the rollback receipt keeps its claim on {relative}"
            );
        }
    }
    // Uninstall can remove both retained versions: the version directory is not
    // left with an unclaimed retained document.
    let platform = fixture.platform();
    let report = run_operation(&platform, paths, Operation::Uninstall, None)
        .expect("the rolled-back installation uninstalls completely");
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(
        !report.warns(WarningCode::LeftoverFiles),
        "{:?}",
        report.warnings
    );
    assert!(
        !paths.versions().exists(),
        "the owned versions directory is removed, not left empty"
    );
}

#[test]
fn interrupted_rollback_keeps_the_retained_payload_and_settles() {
    // Measure the durable boundaries of one clean rollback, then die at each.
    let probe = Fixture::windows();
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let probe_v1 = probe.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), vec![p1]),
    );
    let probe_v2 = probe.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, Vec::new(), vec![p1]),
    );
    probe.install(&probe_v1);
    probe.install(&probe_v2);
    let platform = probe.platform_trusting(&probe_v1.proof);
    run_operation(
        &platform,
        &probe.paths,
        Operation::Rollback,
        Some(authenticate(&probe_v1)),
    )
    .expect("the probe rollback completes");
    let total = platform.mutations().len();
    assert!(
        total >= 4,
        "a rollback crosses several durable boundaries: {total}"
    );

    let mut observed = std::collections::BTreeSet::new();
    for attempt in 1..=total {
        let fixture = Fixture::windows();
        let paths = &fixture.paths;
        let v1 = fixture.package(
            "v1",
            "1.0.0",
            HOST_V1,
            NOTICE_V1,
            policy(1, Vec::new(), vec![p1]),
        );
        let v2 = fixture.package(
            "v2",
            "2.0.0",
            HOST_V2,
            NOTICE_V2,
            policy(2, Vec::new(), vec![p1]),
        );
        fixture.install(&v1);
        fixture.install(&v2);
        let retained_v1 = version_dir(paths, "1.0.0", p1);
        let retained_v2 = version_dir(paths, "2.0.0", payload_id(HOST_V2, NOTICE_V2));
        let context = format!("rollback interrupted at durable attempt {attempt}/{total}");

        let crashing = FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            panic_at_mutation: Some(attempt),
            trust: Some(v1.proof.clone()),
            trust_by_carrier: fixture.proofs.borrow().clone(),
            ..Faults::default()
        });
        let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
            run_operation(
                &crashing,
                paths,
                Operation::Rollback,
                Some(authenticate(&v1)),
            )
        }));
        assert!(outcome.is_err(), "{context}: the injected death interrupts");

        // A failed or interrupted rollback never deletes its own target.
        assert_eq!(
            fs::read(retained_v1.join("host.exe")).expect("retained target"),
            HOST_V1,
            "{context}: the retained rollback target survives"
        );

        // A fresh process reconciles to exactly one complete installation. A
        // recovery that completes a release re-authenticates it, so the OS
        // evidence of the release being re-activated is supplied.
        let recovering = fixture.platform_trusting(&v1.proof);
        let report = recover(&recovering, paths)
            .unwrap_or_else(|failure| panic!("{context}: recovery failed: {failure:?}"));
        assert!(report.failure.is_none(), "{context}: {report:?}");
        assert!(
            report.state == veles_bridge_installer::STATE_RECOVERED_ACTIVE
                || report.state == veles_bridge_installer::STATE_RECOVERED_PREVIOUS
                || report.state == veles_bridge_installer::STATE_CLEAN,
            "{context}: the interrupted rollback is reconciled: {report:?}"
        );
        observed.insert(report.state.clone());
        let live = fs::read(paths.registration_path())
            .unwrap_or_else(|_| panic!("{context}: one registration is always live"));
        let manifest = parse_manifest(&live)
            .unwrap_or_else(|_| panic!("{context}: the live registration is complete JSON"));
        let executable = manifest
            .executable()
            .unwrap_or_else(|_| panic!("{context}: the manifest selects an executable"));
        let content = fs::read(&executable)
            .unwrap_or_else(|_| panic!("{context}: the selected executable exists"));
        assert!(
            content == HOST_V1 || content == HOST_V2,
            "{context}: the selected executable is one of the two verified versions"
        );
        assert!(
            retained_v1.join("host.exe").exists() && retained_v2.join("host.exe").exists(),
            "{context}: both verified versions stay retained"
        );
        assert!(
            receipt(paths).is_some(),
            "{context}: an installed release always has a committed receipt"
        );
        assert!(
            matches!(journal_state(paths), JournalState::None),
            "{context}: recovery releases the journal"
        );
    }

    // Both recovery outcomes are exercised: an interruption before activation
    // restores the previous registration, a committed one finishes forward.
    assert!(
        observed.contains(veles_bridge_installer::STATE_RECOVERED_ACTIVE),
        "a committed rollback is finished by recovery: {observed:?}"
    );
    assert!(
        observed.contains(veles_bridge_installer::STATE_CLEAN)
            || observed.contains(veles_bridge_installer::STATE_RECOVERED_PREVIOUS),
        "an uncommitted rollback is reconciled without activating it: {observed:?}"
    );
}

/// R1: recovering an interrupted rollback re-establishes, from authenticated
/// evidence, what the ordinary rollback requires - on both sides of the receipt.
///
/// * Before the receipt: the effective policy is recomputed, and the target must be
///   rollback-eligible under it (not merely "not denied").
/// * After the receipt: the committed policy must not be broader than the signed
///   descriptor of the release it activates (publishers, rollback list), nor drop a
///   denial that descriptor carries - even when journal and receipt agree.
#[test]
fn recovery_of_an_interrupted_rollback_re_establishes_policy_and_eligibility() {
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let denied = Digest::of(b"a payload the retained release denies");
    let build = |fixture: &Fixture| {
        let v1 = fixture.package(
            "v1",
            "1.0.0",
            HOST_V1,
            NOTICE_V1,
            policy(1, vec![denied], vec![p1]),
        );
        let v2 = fixture.package(
            "v2",
            "2.0.0",
            HOST_V2,
            NOTICE_V2,
            policy(2, Vec::new(), vec![p1]),
        );
        (v1, v2)
    };

    let probe = Fixture::windows();
    let (probe_v1, probe_v2) = build(&probe);
    probe.install(&probe_v1);
    probe.install(&probe_v2);
    let platform = probe.platform_trusting(&probe_v1.proof);
    run_operation(
        &platform,
        &probe.paths,
        Operation::Rollback,
        Some(authenticate(&probe_v1)),
    )
    .expect("the probe rollback completes");
    let total = platform.mutations().len();

    type Forge = fn(&mut veles_bridge_installer::ReleasePolicy, Digest);
    let committed_forgeries: [(&str, Forge); 3] = [
        ("an added publisher", |policy, _| {
            policy.publishers.push("Attacker Publisher".to_owned());
        }),
        ("a dropped denial", |policy, denied| {
            policy.denied_payloads.retain(|digest| *digest != denied);
        }),
        ("a widened rollback list", |policy, _| {
            policy.rollback_payloads.push(Digest::of(b"anything"));
        }),
    ];
    let activated_forgeries: [(&str, Forge); 1] = [(
        "a target the current policy does not make eligible",
        |policy, _| {
            policy.rollback_payloads.clear();
        },
    )];

    // One fresh layout per (death point, forgery): a recovery that wrongly completes
    // must not contaminate the next case.
    let mut wrong: Vec<String> = Vec::new();
    let mut forged_before_receipt = 0;
    let mut forged_after_receipt = 0;
    for attempt in 1..=total {
        #[allow(clippy::needless_range_loop)]
        for forgery_index in 0..3 {
            let fixture = Fixture::windows();
            let paths = &fixture.paths;
            let (v1, v2) = build(&fixture);
            fixture.install(&v1);
            fixture.install(&v2);
            let crashing = FakePlatform::with_faults(Faults {
                host_state: HostState::Quiescent,
                panic_at_mutation: Some(attempt),
                trust: Some(v1.proof.clone()),
                trust_by_carrier: fixture.proofs.borrow().clone(),
                ..Faults::default()
            });
            let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
                run_operation(
                    &crashing,
                    paths,
                    Operation::Rollback,
                    Some(authenticate(&v1)),
                )
            }));
            assert!(outcome.is_err(), "attempt {attempt}: the death interrupts");

            let JournalState::Pending(journal) = journal_state(paths) else {
                break;
            };
            let current = receipt(paths).expect("a receipt always exists");
            let committed = current.payload_id == v1.descriptor.payload_id;
            let activated =
                !committed && Digest::of(&fixture.registration()) == journal.manifest.sha256();
            let (name, forge) = if committed {
                committed_forgeries[forgery_index]
            } else if activated && forgery_index == 0 {
                activated_forgeries[0]
            } else {
                break;
            };

            let context = format!(
                "attempt {attempt} ({}): {name}",
                if committed {
                    "receipt committed"
                } else {
                    "activated, no receipt"
                }
            );
            let writer = fixture.platform_trusting(&v1.proof);
            let mut forged_journal = (*journal).clone();
            forge(&mut forged_journal.release_policy, denied);
            forged_journal
                .write(&writer, paths)
                .expect("forged journal");
            let mut forged_receipt = current.clone();
            forge(&mut forged_receipt.release_policy, denied);
            forged_receipt
                .write(&writer, paths)
                .expect("forged receipt");
            let before = snapshot(fixture.temp.path());

            let recovering = fixture.platform_trusting(&v1.proof);
            match recover(&recovering, paths) {
                Ok(report) => wrong.push(format!(
                    "{context}: recovery completed a policy the signed evidence does not support: {:?}",
                    report.state
                )),
                Err(_) => {
                    if !recovering.mutations().is_empty()
                        || snapshot(fixture.temp.path()) != before
                    {
                        wrong.push(format!("{context}: a refused recovery changed state"));
                    }
                }
            }
            if committed {
                forged_after_receipt += 1;
            } else {
                forged_before_receipt += 1;
            }
        }
    }
    assert!(
        forged_before_receipt >= 1 && forged_after_receipt >= 1,
        "both windows were exercised: before={forged_before_receipt} after={forged_after_receipt}"
    );
    assert!(
        wrong.is_empty(),
        "unsupported policies were completed: {wrong:#?}"
    );
}

/// N2: the policy a recovery or rollback composes *against* is the committed
/// installation's, and that is stored in an editable receipt. A denial carried only
/// by the release being replaced (here v2) must survive a rollback to v1 even when
/// the receipt and the journal are both edited to drop it - before the receipt is
/// replaced (the operand is the old receipt), after it (the operand is gone from the
/// receipt, but the replaced release stays retained and signed), and in an ordinary
/// rollback that needs no crash at all.
#[test]
fn a_denial_only_the_replaced_release_signs_cannot_be_edited_out_of_state() {
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let denied = Digest::of(b"a payload only the newer release denies");
    let build = |fixture: &Fixture| {
        let v1 = fixture.package(
            "v1",
            "1.0.0",
            HOST_V1,
            NOTICE_V1,
            policy(1, Vec::new(), vec![p1]),
        );
        let v2 = fixture.package(
            "v2",
            "2.0.0",
            HOST_V2,
            NOTICE_V2,
            policy(2, vec![denied], vec![p1]),
        );
        (v1, v2)
    };
    // The OS verifies whichever installed carrier it is handed.
    let trusting = |v1: &Package, v2: &Package, panic_at: Option<usize>| {
        FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            trust: Some(v1.proof.clone()),
            trust_by_carrier: vec![v1.proof.clone(), v2.proof.clone()],
            panic_at_mutation: panic_at,
            ..Faults::default()
        })
    };

    let probe = Fixture::windows();
    let (probe_v1, probe_v2) = build(&probe);
    probe.install(&probe_v1);
    probe.install(&probe_v2);
    let platform = trusting(&probe_v1, &probe_v2, None);
    run_operation(
        &platform,
        &probe.paths,
        Operation::Rollback,
        Some(authenticate(&probe_v1)),
    )
    .expect("the unedited rollback completes");
    assert!(
        receipt(&probe.paths)
            .expect("receipt")
            .release_policy
            .denies(&denied),
        "the rollback carries the denial only the replaced release signed"
    );
    let total = platform.mutations().len();

    let mut wrong: Vec<String> = Vec::new();
    let mut before_receipt = 0;
    let mut after_receipt = 0;
    for attempt in 1..=total {
        for forge in [false, true] {
            let fixture = Fixture::windows();
            let paths = &fixture.paths;
            let (v1, v2) = build(&fixture);
            fixture.install(&v1);
            fixture.install(&v2);
            let crashing = trusting(&v1, &v2, Some(attempt));
            let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
                run_operation(
                    &crashing,
                    paths,
                    Operation::Rollback,
                    Some(authenticate(&v1)),
                )
            }));
            assert!(outcome.is_err(), "attempt {attempt}: the death interrupts");
            let JournalState::Pending(journal) = journal_state(paths) else {
                continue;
            };
            let current = receipt(paths).expect("a receipt always exists");
            let committed = current.payload_id == v1.descriptor.payload_id;
            let activated =
                !committed && Digest::of(&fixture.registration()) == journal.manifest.sha256();
            if !committed && !activated {
                continue;
            }
            let context = format!(
                "attempt {attempt} ({}, {})",
                if committed {
                    "receipt committed"
                } else {
                    "activated, no receipt"
                },
                if forge {
                    "denial edited out"
                } else {
                    "control"
                }
            );
            if forge {
                let writer = trusting(&v1, &v2, None);
                let mut forged_journal = (*journal).clone();
                forged_journal
                    .release_policy
                    .denied_payloads
                    .retain(|d| *d != denied);
                forged_journal
                    .write(&writer, paths)
                    .expect("forged journal");
                let mut forged_receipt = current.clone();
                forged_receipt
                    .release_policy
                    .denied_payloads
                    .retain(|d| *d != denied);
                forged_receipt
                    .write(&writer, paths)
                    .expect("forged receipt");
            }
            let before = snapshot(fixture.temp.path());
            let recovering = trusting(&v1, &v2, None);
            let result = recover(&recovering, paths);
            match (forge, result) {
                (false, Err(failure)) => wrong.push(format!(
                    "{context}: honest recovery was refused: {:?}",
                    failure.code
                )),
                (false, Ok(_)) => {}
                (true, Ok(report)) => wrong.push(format!(
                    "{context}: recovery completed with the signed denial gone: {:?}",
                    report.state
                )),
                (true, Err(_)) => {
                    if !recovering.mutations().is_empty() || snapshot(fixture.temp.path()) != before
                    {
                        wrong.push(format!("{context}: a refused recovery changed state"));
                    }
                }
            }
            if forge {
                if committed {
                    after_receipt += 1;
                } else {
                    before_receipt += 1;
                }
            }
        }
    }
    assert!(
        before_receipt >= 1 && after_receipt >= 1,
        "both windows were exercised: before={before_receipt} after={after_receipt}"
    );
    assert!(wrong.is_empty(), "{wrong:#?}");
}

/// N5: after an interrupted v2 -> v1 rollback has committed its receipt, the evidence
/// that v2 was replaced is `receipt.prior` and `journal.old_*`. An editor who also
/// removes v2's only denial can rewrite BOTH to name v1: the two editable references
/// still agree, so a recovery that accepts their agreement authenticates v1 twice,
/// never establishes v2's restrictions, and completes with the denial gone. This
/// mutates the denial (as the existing regression does) and, jointly, ONLY the
/// identities of the replaced release - plus, in the second form, the old-manifest
/// evidence rewritten to match, which only the strict ordering can then refuse.
#[test]
fn a_rollback_target_cannot_be_rewritten_as_the_release_it_replaced() {
    #[derive(Clone, Copy, Debug)]
    enum Rewrite {
        /// Honest state: recovery must complete.
        Control,
        /// Denial removed; `receipt.prior` and `journal.old_*` both name v1.
        Identities,
        /// As above, and the old-manifest evidence (journal bytes, preimage digest and
        /// `receipt.prior.manifest_sha256`) is rewritten to v1's own manifest.
        IdentitiesAndManifest,
        /// Nothing but the old-manifest evidence changes: the replaced release is still
        /// honestly v2 (strictly newer than the target), yet the journalled manifest
        /// selects v1. Only the binding of the authenticated identity to that manifest
        /// can refuse this.
        ManifestOnly,
    }
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let denied = Digest::of(b"a payload only the newer release denies");
    let build = |fixture: &Fixture| {
        let v1 = fixture.package(
            "v1",
            "1.0.0",
            HOST_V1,
            NOTICE_V1,
            policy(1, Vec::new(), vec![p1]),
        );
        let v2 = fixture.package(
            "v2",
            "2.0.0",
            HOST_V2,
            NOTICE_V2,
            policy(2, vec![denied], vec![p1]),
        );
        (v1, v2)
    };
    let trusting = |v1: &Package, v2: &Package, panic_at: Option<usize>| {
        FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            trust: Some(v1.proof.clone()),
            trust_by_carrier: vec![v1.proof.clone(), v2.proof.clone()],
            panic_at_mutation: panic_at,
            ..Faults::default()
        })
    };

    let probe = Fixture::windows();
    let (probe_v1, probe_v2) = build(&probe);
    probe.install(&probe_v1);
    probe.install(&probe_v2);
    let counting = trusting(&probe_v1, &probe_v2, None);
    run_operation(
        &counting,
        &probe.paths,
        Operation::Rollback,
        Some(authenticate(&probe_v1)),
    )
    .expect("the unedited rollback completes");
    let total = counting.mutations().len();

    let mut wrong: Vec<String> = Vec::new();
    let mut exercised = 0;
    for attempt in 1..=total {
        for rewrite in [
            Rewrite::Control,
            Rewrite::Identities,
            Rewrite::IdentitiesAndManifest,
            Rewrite::ManifestOnly,
        ] {
            let fixture = Fixture::windows();
            let paths = &fixture.paths;
            let (v1, v2) = build(&fixture);
            fixture.install(&v1);
            fixture.install(&v2);
            let crashing = trusting(&v1, &v2, Some(attempt));
            let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
                run_operation(
                    &crashing,
                    paths,
                    Operation::Rollback,
                    Some(authenticate(&v1)),
                )
            }));
            assert!(outcome.is_err(), "attempt {attempt}: the death interrupts");
            let JournalState::Pending(journal) = journal_state(paths) else {
                continue;
            };
            let current = receipt(paths).expect("a receipt always exists");
            // Only the window after the receipt names the replaced release.
            if current.payload_id != v1.descriptor.payload_id {
                continue;
            }
            let context = format!("attempt {attempt} ({rewrite:?})");
            if !matches!(rewrite, Rewrite::Control) {
                let writer = trusting(&v1, &v2, None);
                let mut forged_journal = (*journal).clone();
                let mut forged_receipt = current.clone();
                let prior = forged_receipt
                    .prior
                    .as_mut()
                    .expect("a rollback has a prior");
                if !matches!(rewrite, Rewrite::ManifestOnly) {
                    forged_journal
                        .release_policy
                        .denied_payloads
                        .retain(|d| *d != denied);
                    forged_receipt
                        .release_policy
                        .denied_payloads
                        .retain(|d| *d != denied);
                    forged_journal.old_version = Some(v1.descriptor.version.clone());
                    forged_journal.old_payload_id = Some(v1.descriptor.payload_id);
                    forged_journal.old_carrier_sha256 = Some(Digest::of(v1.proof.carrier()));
                    prior.version = v1.descriptor.version.clone();
                    prior.payload_id = v1.descriptor.payload_id;
                    prior.carrier_sha256 = Digest::of(v1.proof.carrier());
                }
                if matches!(
                    rewrite,
                    Rewrite::IdentitiesAndManifest | Rewrite::ManifestOnly
                ) {
                    let own = fixture.registration();
                    let snapshot = veles_bridge_installer::ManifestSnapshot::new(own)
                        .expect("v1's own manifest");
                    prior.manifest_sha256 = Some(snapshot.sha256());
                    forged_journal
                        .preimage
                        .as_mut()
                        .expect("a rollback journals a preimage")
                        .manifest_sha256 = snapshot.sha256();
                    forged_journal.old_manifest = Some(snapshot);
                }
                forged_journal
                    .write(&writer, paths)
                    .expect("forged journal");
                forged_receipt
                    .write(&writer, paths)
                    .expect("forged receipt");
            }
            let before = snapshot(fixture.temp.path());
            let recovering = trusting(&v1, &v2, None);
            let result = recover(&recovering, paths);
            match (rewrite, result) {
                (Rewrite::Control, Err(failure)) => wrong.push(format!(
                    "{context}: honest recovery was refused: {:?}",
                    failure.code
                )),
                (Rewrite::Control, Ok(_)) => {}
                (_, Ok(report)) => wrong.push(format!(
                    "{context}: recovery completed on rewritten history: {:?}",
                    report.state
                )),
                (_, Err(_)) => {
                    if !recovering.mutations().is_empty() || snapshot(fixture.temp.path()) != before
                    {
                        wrong.push(format!("{context}: a refused recovery changed state"));
                    }
                }
            }
            if !matches!(rewrite, Rewrite::Control) {
                exercised += 1;
            }
        }
    }
    assert!(
        exercised >= 2,
        "the post-receipt window was exercised: {exercised}"
    );
    assert!(wrong.is_empty(), "{wrong:#?}");
}

/// Residual (b): an install over a prior release, and a repair, used to compose
/// against the policy stored in the editable receipt. They now compose only against a
/// policy that the prior release's AUTHENTICATED signed carrier bounds, and refuse with
/// `RecoveryRequired` - nothing written - when it does not.
///
/// Two erasures are covered, each editing nothing but the receipt policy:
/// * a denial the INSTALLED release signs (the receipt is broader than its own
///   signed carrier); and
/// * a denial only the release a rollback REPLACED signs (the receipt is within the
///   installed release's signed policy, so only the replaced release's retained
///   carrier can show the denial is gone).
#[test]
fn install_and_repair_refuse_a_receipt_policy_the_signed_carrier_does_not_bound() {
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let denied = Digest::of(b"a payload some signed release denies");

    // --- installed release's own denial ---------------------------------------
    {
        let fixture = Fixture::windows();
        let paths = &fixture.paths;
        let v1 = fixture.package(
            "v1",
            "1.0.0",
            HOST_V1,
            NOTICE_V1,
            policy(1, vec![denied], vec![p1]),
        );
        // At the same epoch with the denial gone: acceptable ONLY if the receipt's
        // (forged) policy is believed to be what the installation enforces.
        let v2 = fixture.package(
            "v2",
            "2.0.0",
            HOST_V2,
            NOTICE_V2,
            policy(1, Vec::new(), vec![p1]),
        );
        fixture.install(&v1);
        let mut forged = receipt(paths).expect("receipt");
        forged.release_policy.denied_payloads.clear();
        forged.write(&fixture.platform(), paths).expect("forged");
        fs::remove_file(version_dir(paths, "1.0.0", p1).join("licenses/NOTICE"))
            .expect("damage the installed release");

        for (operation, package) in [(Operation::Install, &v2), (Operation::Repair, &v1)] {
            let before = snapshot(fixture.temp.path());
            let platform = fixture.platform();
            let outcome = run_operation(&platform, paths, operation, Some(authenticate(package)));
            let context = format!("installed denial dropped, {operation:?}");
            assert_eq!(
                failure_code(outcome),
                FailureCode::RecoveryRequired,
                "{context}"
            );
            assert!(platform.mutations().is_empty(), "{context}");
            assert_eq!(snapshot(fixture.temp.path()), before, "{context}");
        }
    }

    // --- a denial only the replaced release signs -----------------------------
    {
        let fixture = Fixture::windows();
        let paths = &fixture.paths;
        let v1 = fixture.package(
            "v1",
            "1.0.0",
            HOST_V1,
            NOTICE_V1,
            policy(1, Vec::new(), vec![p1]),
        );
        let v2 = fixture.package(
            "v2",
            "2.0.0",
            HOST_V2,
            NOTICE_V2,
            policy(2, vec![denied], vec![p1]),
        );
        // Same epoch as the rollback receipt, denial absent: believed only if the
        // receipt is.
        let v3 = fixture.package(
            "v3",
            "3.0.0",
            HOST_V3,
            NOTICE_V3,
            policy(2, Vec::new(), vec![p1]),
        );
        fixture.install(&v1);
        fixture.install(&v2);
        run_operation(
            &fixture.platform_trusting(&v1.proof),
            paths,
            Operation::Rollback,
            Some(authenticate(&v1)),
        )
        .expect("the honest rollback completes");
        let mut forged = receipt(paths).expect("receipt");
        assert!(forged.release_policy.denies(&denied), "rollback kept it");
        forged
            .release_policy
            .denied_payloads
            .retain(|d| *d != denied);
        forged.write(&fixture.platform(), paths).expect("forged");
        fs::remove_file(version_dir(paths, "1.0.0", p1).join("licenses/NOTICE"))
            .expect("damage the installed release");

        // Install: the receipt keeps the rolled-back epoch and only the denial is gone.
        let before = snapshot(fixture.temp.path());
        let platform = fixture.platform();
        let outcome = run_operation(
            &platform,
            paths,
            Operation::Install,
            Some(authenticate(&v3)),
        );
        assert_eq!(
            failure_code(outcome),
            FailureCode::RecoveryRequired,
            "replaced denial dropped, Install"
        );
        assert!(platform.mutations().is_empty());
        assert_eq!(snapshot(fixture.temp.path()), before);

        // Repair: a rolled-back release can only be repaired against a receipt at its
        // own (lower) epoch, so the edit lowers the epoch with the denial. Without the
        // replaced release's retained carrier nothing refuses this.
        forged.release_policy.epoch = v1.descriptor.policy.epoch;
        forged.policy.epoch = v1.descriptor.policy.epoch;
        forged.write(&fixture.platform(), paths).expect("forged");
        let before = snapshot(fixture.temp.path());
        let platform = fixture.platform();
        let outcome = run_operation(&platform, paths, Operation::Repair, Some(authenticate(&v1)));
        assert_eq!(
            failure_code(outcome),
            FailureCode::RecoveryRequired,
            "replaced denial dropped, Repair"
        );
        assert!(platform.mutations().is_empty());
        assert_eq!(snapshot(fixture.temp.path()), before);
    }
}

/// Residual (b), the recorded consequence: when the prior release cannot be
/// authenticated at all, an upgrade is refused with nothing written instead of
/// composing against receipt state. Here every claimed byte is intact (the older
/// claim check passes) but the OS cannot vouch for the installed carrier.
#[test]
fn an_install_over_a_prior_the_os_cannot_authenticate_is_refused_untouched() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, Vec::new(), Vec::new()),
    );
    fixture.install(&v1);
    let before = snapshot(fixture.temp.path());

    for (label, faults) in [
        (
            "no assessment can be formed",
            Faults {
                host_state: HostState::Quiescent,
                trust_by_carrier: vec![v2.proof.clone()],
                ..Faults::default()
            },
        ),
        (
            "the OS denies the installed carrier",
            Faults {
                host_state: HostState::Quiescent,
                trust_denied: true,
                ..Faults::default()
            },
        ),
    ] {
        let platform = FakePlatform::with_faults(faults);
        let outcome = run_operation(
            &platform,
            paths,
            Operation::Install,
            Some(authenticate(&v2)),
        );
        let code = failure_code(outcome);
        assert!(
            matches!(
                code,
                FailureCode::TrustUnavailable | FailureCode::TrustDenied
            ),
            "{label}: the OS verdict is reported unchanged: {code:?}"
        );
        assert!(platform.mutations().is_empty(), "{label}");
        assert_eq!(snapshot(fixture.temp.path()), before, "{label}");
    }
    // Once the OS can vouch for it again, the same upgrade proceeds.
    fixture.install(&v2);
    assert_eq!(
        receipt(paths).expect("receipt").version,
        "2.0.0",
        "the refusal was availability-only: nothing was damaged"
    );
}

/// Residual (b) in recovery: an interrupted upgrade recomposes its policy against the
/// previous receipt, so it first authenticates the previous release too. A previous
/// release the OS can no longer vouch for leaves the journal pending, untouched.
#[test]
fn recovering_an_interrupted_upgrade_authenticates_the_previous_release() {
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let build = |fixture: &Fixture| {
        let v1 = fixture.package(
            "v1",
            "1.0.0",
            HOST_V1,
            NOTICE_V1,
            policy(1, Vec::new(), vec![p1]),
        );
        let v2 = fixture.package(
            "v2",
            "2.0.0",
            HOST_V2,
            NOTICE_V2,
            policy(2, Vec::new(), vec![p1]),
        );
        (v1, v2)
    };
    let probe = Fixture::windows();
    let (probe_v1, probe_v2) = build(&probe);
    probe.install(&probe_v1);
    let counting = probe.platform();
    run_operation(
        &counting,
        &probe.paths,
        Operation::Install,
        Some(authenticate(&probe_v2)),
    )
    .expect("the unedited upgrade completes");
    let total = counting.mutations().len();

    let mut exercised = 0;
    let mut wrong: Vec<String> = Vec::new();
    for attempt in 1..=total {
        let fixture = Fixture::windows();
        let paths = &fixture.paths;
        let (v1, v2) = build(&fixture);
        fixture.install(&v1);
        let crashing = FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            trust_by_carrier: vec![v1.proof.clone(), v2.proof.clone()],
            panic_at_mutation: Some(attempt),
            ..Faults::default()
        });
        let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
            run_operation(
                &crashing,
                paths,
                Operation::Install,
                Some(authenticate(&v2)),
            )
        }));
        assert!(outcome.is_err(), "attempt {attempt}: the death interrupts");
        let JournalState::Pending(journal) = journal_state(paths) else {
            continue;
        };
        let committed = receipt(paths).expect("receipt").payload_id == v2.descriptor.payload_id;
        let activated =
            !committed && Digest::of(&fixture.registration()) == journal.manifest.sha256();
        if !activated {
            continue;
        }
        let before = snapshot(fixture.temp.path());
        // The OS vouches for the staged v2 but not for the previous release.
        let recovering = FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            trust_by_carrier: vec![v2.proof.clone()],
            ..Faults::default()
        });
        match recover(&recovering, paths) {
            Ok(report) => wrong.push(format!(
                "attempt {attempt}: recovered over an unauthenticated previous release: {:?}",
                report.state
            )),
            Err(_) => {
                if !recovering.mutations().is_empty() || snapshot(fixture.temp.path()) != before {
                    wrong.push(format!(
                        "attempt {attempt}: a refused recovery changed state"
                    ));
                }
            }
        }
        // Control: with the previous release vouched for, the same state recovers.
        let honest = FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            trust_by_carrier: vec![v1.proof.clone(), v2.proof.clone()],
            ..Faults::default()
        });
        if let Err(failure) = recover(&honest, paths) {
            wrong.push(format!(
                "attempt {attempt}: honest recovery was refused: {:?}",
                failure.code
            ));
        }
        exercised += 1;
    }
    assert!(exercised >= 1, "the activated, pre-receipt window was hit");
    assert!(wrong.is_empty(), "{wrong:#?}");
}

/// N2, no crash needed: editing the one committed receipt must not let an ordinary
/// rollback compose against a policy that forgot the current release's signed denial.
#[test]
fn an_ordinary_rollback_refuses_a_receipt_edited_to_forget_the_current_denial() {
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let denied = Digest::of(b"a payload only the newer release denies");
    let trusting = |v1: &Package, v2: &Package| {
        FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            trust: Some(v1.proof.clone()),
            trust_by_carrier: vec![v1.proof.clone(), v2.proof.clone()],
            ..Faults::default()
        })
    };
    let fixture = Fixture::windows();
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), vec![p1]),
    );
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, vec![denied], vec![p1]),
    );
    fixture.install(&v1);
    fixture.install(&v2);
    let writer = trusting(&v1, &v2);
    let mut forged = receipt(&fixture.paths).expect("receipt");
    forged
        .release_policy
        .denied_payloads
        .retain(|d| *d != denied);
    forged
        .write(&writer, &fixture.paths)
        .expect("forged receipt");
    let before = snapshot(fixture.temp.path());
    let platform = trusting(&v1, &v2);
    let outcome = run_operation(
        &platform,
        &fixture.paths,
        Operation::Rollback,
        Some(authenticate(&v1)),
    );
    assert!(
        outcome.is_err(),
        "a rollback composed against an edited receipt must be refused: {outcome:?}"
    );
    assert!(platform.mutations().is_empty());
    assert_eq!(snapshot(fixture.temp.path()), before);
}

#[test]
fn rollback_requires_signer_authorized_by_both_policies() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let p2 = payload_id(HOST_V2, NOTICE_V2);

    // The retained 1.0.0 package is signed by Publisher Two and its own signed
    // policy permits both publishers for that payload.
    let v1 = fixture.package_with(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        ReleasePolicy {
            publishers: vec![PUBLISHER.to_owned(), "Publisher Two".to_owned()],
            denied_payloads: Vec::new(),
            rollback_payloads: vec![p1],
            epoch: 1,
        },
        "Publisher Two",
        (1, 2),
    );
    // The persisted 2.0.0 policy trusts only Publisher One but still lists the
    // prior payload as rollback-eligible, so the publisher intersection is
    // non-empty while excluding the actual signer.
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, Vec::new(), vec![p1]),
    );
    assert_eq!(v1.proof.signer(), "Publisher Two");
    assert_eq!(v1.descriptor.payload_id, p1);
    assert_eq!(v2.descriptor.payload_id, p2);
    fixture.install(&v1);
    fixture.install(&v2);

    let registration = fixture.registration();
    let saved_receipt = receipt(paths).expect("the install commits a receipt");
    let before = snapshot(fixture.temp.path());

    // A retained package signed by a publisher the persisted policy revoked is
    // never accepted just because some other publisher is still trusted.
    let platform = fixture.platform_trusting(&v1.proof);
    let failure = run_operation(
        &platform,
        paths,
        Operation::Rollback,
        Some(authenticate(&v1)),
    )
    .expect_err("a signer outside the persisted policy is denied");
    assert_eq!(failure.code, FailureCode::PolicyDenied);
    assert!(
        failure.prior_usable,
        "the denied rollback leaves the installed release usable"
    );
    assert!(
        platform.mutations().is_empty(),
        "the signer check precedes every mutation: {:?}",
        platform.mutations()
    );
    assert_eq!(snapshot(fixture.temp.path()), before);
    assert_eq!(fixture.registration(), registration);
    assert_eq!(
        receipt(paths).expect("receipt"),
        saved_receipt,
        "the persisted policy and installation are unchanged"
    );
    assert_eq!(
        fs::read(version_dir(paths, "1.0.0", p1).join("host.exe")).expect("retained v1"),
        HOST_V1
    );
}

#[cfg(unix)]
#[test]
fn repair_rejects_hard_linked_managed_file() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let p1 = v1.descriptor.payload_id;
    fixture.install(&v1);
    let installed_dir = version_dir(paths, "1.0.0", p1);

    // An unrelated file (a Chrome-profile sentinel) that a damaged managed path
    // has been replaced with a hard link to.
    let unrelated = fixture.temp.path().join("chrome-profile/Preferences");
    fs::create_dir_all(unrelated.parent().expect("profile parent")).expect("profile directory");
    fs::write(&unrelated, PROFILE_SENTINEL).expect("unrelated sentinel");
    let managed = installed_dir.join("licenses/NOTICE");
    fs::remove_file(&managed).expect("remove managed notice");
    fs::hard_link(&unrelated, &managed).expect("hard link managed notice");

    let registration = fixture.registration();
    let platform = fixture.platform();
    let report = run_operation(&platform, paths, Operation::Repair, Some(authenticate(&v1)))
        .expect("a multiply linked managed file is replaced, not written through");
    assert_eq!(report.state, STATE_REPAIRED);
    assert_eq!(
        fs::read(&unrelated).expect("unrelated target"),
        PROFILE_SENTINEL,
        "the unrelated hard-link target is byte-for-byte untouched"
    );
    assert_eq!(
        fs::read(&managed).expect("restored managed notice"),
        NOTICE_V1,
        "the managed name now holds the authenticated bytes"
    );
    {
        use std::os::unix::fs::MetadataExt;
        assert_ne!(
            fs::metadata(&managed).expect("managed metadata").ino(),
            fs::metadata(&unrelated).expect("unrelated metadata").ino(),
            "the managed name no longer shares the unrelated inode"
        );
    }
    assert_eq!(fixture.registration(), registration);
    assert!(
        !platform.calls().contains(&PlatformCall::VerifyTrust),
        "a damaged local tree never becomes trust input"
    );
}

#[test]
fn repair_rejects_an_intervening_registration() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let p1 = v1.descriptor.payload_id;
    fixture.install(&v1);
    let installed_dir = version_dir(paths, "1.0.0", p1);
    fs::remove_file(paths.registration_path()).expect("remove registration");
    fs::remove_file(installed_dir.join("licenses/NOTICE")).expect("remove managed notice");

    // A structurally owned manifest appears while repair waits for quiescence.
    // Its executable exists, so the old owned-preimage publication would have
    // accepted and overwritten it.
    let planted_executable = paths.versions().join("planted/host.exe");
    fs::create_dir_all(planted_executable.parent().expect("planted parent"))
        .expect("planted directory");
    fs::write(&planted_executable, b"planted host").expect("planted executable");
    let planted = canonical_json(&veles_bridge_installer::NativeManifest {
        name: paths.registration_name().to_owned(),
        description: "intervening product".to_owned(),
        path: planted_executable
            .to_str()
            .expect("UTF-8 planted path")
            .to_owned(),
        kind: "stdio".to_owned(),
        allowed_origins: vec![extension_origin(EXTENSION_ID).expect("fixture origin")],
    })
    .expect("planted manifest");

    let platform = InterveningPlatform::new(planted.clone());
    let failure = run_operation(&platform, paths, Operation::Repair, Some(authenticate(&v1)))
        .expect_err("a registration that appeared during the wait is a conflict");
    assert_eq!(failure.code, FailureCode::ForeignState);
    assert!(
        !failure.prior_usable,
        "the committed registration is no longer the live one"
    );
    assert_eq!(
        fs::read(paths.registration_path()).expect("intervening registration"),
        planted,
        "the intervening registration is preserved byte-for-byte"
    );
    assert!(
        !platform.calls().contains(&PlatformCall::PublishManifest),
        "repair refuses to publish over the changed preimage"
    );
    // The managed tree may already have been restored; the conflict is about the
    // registration, which is never replaced.
    assert_eq!(
        fs::read(installed_dir.join("licenses/NOTICE")).expect("restored managed notice"),
        NOTICE_V1
    );

    // The same rule applies when a committed registration was observed live and
    // is replaced during the wait: the committed preimage is no longer what is
    // there, so repair refuses and preserves the replacement.
    let second = Fixture::windows();
    let second_paths = &second.paths;
    second.install(&v1);
    let different_executable = second_paths.versions().join("planted/host.exe");
    fs::create_dir_all(different_executable.parent().expect("planted parent"))
        .expect("planted directory");
    fs::write(&different_executable, b"planted host").expect("planted executable");
    let replacement = canonical_json(&veles_bridge_installer::NativeManifest {
        name: second_paths.registration_name().to_owned(),
        description: "intervening replacement".to_owned(),
        path: different_executable
            .to_str()
            .expect("UTF-8 planted path")
            .to_owned(),
        kind: "stdio".to_owned(),
        allowed_origins: vec![extension_origin(EXTENSION_ID).expect("fixture origin")],
    })
    .expect("replacement manifest");
    let platform = InterveningPlatform::new(replacement.clone());
    let failure = run_operation(
        &platform,
        second_paths,
        Operation::Repair,
        Some(authenticate(&v1)),
    )
    .expect_err("a replaced committed preimage is a conflict");
    assert_eq!(failure.code, FailureCode::ForeignState);
    assert_eq!(
        fs::read(second_paths.registration_path()).expect("replacement registration"),
        replacement,
        "the intervening replacement is preserved byte-for-byte"
    );
    assert!(!platform.calls().contains(&PlatformCall::PublishManifest));
}

#[test]
fn rollback_uses_retained_authenticated_carrier_without_a_package() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), vec![p1]),
    );
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, Vec::new(), vec![p1]),
    );
    fixture.install(&v1);
    fixture.install(&v2);

    // No package and no original download: the authenticated carrier retained in
    // the immutable version directory is sufficient.
    let report = run_operation(
        &fixture.platform_trusting(&v1.proof),
        paths,
        Operation::Rollback,
        None,
    )
    .expect("the retained verified prior release re-activates");
    assert_eq!(report.state, STATE_ROLLED_BACK);
    assert_eq!(report.active_payload, Some(p1));
    assert!(report.failure.is_none());
    assert!(!report.warns(WarningCode::CompatibilityWindow));
    let live = fixture.registration();
    let executable = parse_manifest(&live)
        .expect("live registration")
        .executable()
        .expect("absolute executable");
    assert_eq!(fs::read(&executable).expect("rolled-back host"), HOST_V1);
}

#[test]
fn damaged_retained_metadata_requests_repair_not_foreign_state() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), vec![p1]),
    );
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, Vec::new(), vec![p1]),
    );
    fixture.install(&v1);
    fixture.install(&v2);

    // Damage the retained carrier metadata: the target can no longer be proven,
    // so the answer is repair guidance with the installed release preserved.
    let retained = version_dir(paths, "1.0.0", p1).join(RETAINED_FILE_NAME);
    let saved_retained = fs::read(&retained).expect("retained metadata");
    fs::write(&retained, b"{\"schema\":1}\n").expect("damaged retained metadata");
    let registration = fixture.registration();
    let saved_receipt = receipt(paths).expect("receipt");
    let before = snapshot(fixture.temp.path());

    // The installed release (v2) is authenticated first; the target is the damaged one.
    let platform = fixture.platform_trusting(&v2.proof);
    let failure = run_operation(&platform, paths, Operation::Rollback, None)
        .expect_err("damaged retained metadata is not a rollback target");
    assert_eq!(failure.code, FailureCode::NoRollback);
    assert!(
        failure.prior_usable,
        "the installed release is still live and usable"
    );
    assert!(platform.mutations().is_empty());
    assert_eq!(snapshot(fixture.temp.path()), before);
    assert_eq!(fixture.registration(), registration);
    assert_eq!(receipt(paths).expect("receipt"), saved_receipt);

    // A damaged retained payload file is the same guidance, not foreign state.
    fs::write(&retained, &saved_retained).expect("restore metadata");
    fs::write(
        version_dir(paths, "1.0.0", p1).join("host.exe"),
        b"tampered",
    )
    .expect("damaged retained payload");
    let platform = fixture.platform_trusting(&v1.proof);
    let failure = run_operation(&platform, paths, Operation::Rollback, None)
        .expect_err("a damaged retained payload is not a rollback target");
    assert_eq!(failure.code, FailureCode::NoRollback);
    assert!(platform.mutations().is_empty());
    assert_eq!(fixture.registration(), registration);
}

#[test]
fn retention_keeps_exactly_one_prior_version() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let p2 = payload_id(HOST_V2, NOTICE_V2);
    let p3 = payload_id(HOST_V3, NOTICE_V3);
    let hosts = [HOST_V1, HOST_V2, HOST_V3, b"v4 host payload"];
    let notices = [NOTICE_V1, NOTICE_V2, NOTICE_V3, b"v4 licence notice"];

    // Start from v3 so boundedness is proven with more than one historical
    // version already on disk.
    for (index, (host, notice)) in hosts[..3].iter().zip(notices[..3].iter()).enumerate() {
        let version = format!("{}.0.0", index + 1);
        let package = fixture.package(
            &format!("v{}", index + 1),
            &version,
            host,
            notice,
            policy(1, Vec::new(), Vec::new()),
        );
        fixture.install(&package);
    }
    let p4 = payload_id(hosts[3], notices[3]);
    let v4 = fixture.package(
        "v4",
        "4.0.0",
        hosts[3],
        notices[3],
        policy(1, Vec::new(), Vec::new()),
    );
    assert_eq!(v4.descriptor.payload_id, p4);
    fixture.install(&v4);

    // Exactly the active version and its immediately prior version survive.
    assert!(
        version_dir(paths, "3.0.0", p3).exists(),
        "the immediately prior version is retained"
    );
    assert!(
        version_dir(paths, "4.0.0", p4).exists(),
        "the active version is retained"
    );
    assert!(
        !version_dir(paths, "1.0.0", p1).exists(),
        "a superseded version directory is removed"
    );
    assert!(
        !version_dir(paths, "2.0.0", p2).exists(),
        "the older retained version is removed when superseded"
    );
    assert_eq!(
        fs::read_dir(paths.versions())
            .expect("versions dir")
            .count(),
        2,
        "retention is bounded to the active and one prior version"
    );

    let receipt = receipt(paths).expect("the upgrade commits a receipt");
    assert_eq!(
        receipt.prior.as_ref().map(|prior| prior.payload_id),
        Some(p3)
    );
    let claimed_versions: std::collections::BTreeSet<String> = receipt
        .ownership
        .owned
        .iter()
        .filter_map(|claim| {
            let rest = claim.path.strip_prefix("versions/")?;
            Some(rest.split('/').next().unwrap_or(rest).to_owned())
        })
        .collect();
    let expected: std::collections::BTreeSet<String> = [
        format!("3.0.0-{}", p3.to_hex()),
        format!("4.0.0-{}", p4.to_hex()),
    ]
    .into_iter()
    .collect();
    assert_eq!(
        claimed_versions, expected,
        "the receipt never carries a superseded version claim"
    );
}

/// A host that launches *after* the operation began - while the new payload is
/// being staged - must be caught by the recheck immediately before activation.
/// This drives the real install transaction: delete that recheck from
/// `transaction::run` and the install publishes over a running host and this test
/// fails (it succeeds instead of reporting `HostActive`).
#[test]
fn late_host_launch_before_activation_aborts_the_install_and_preserves_the_live_release() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, Vec::new(), Vec::new()),
    );
    let p1 = v1.descriptor.payload_id;
    fixture.install(&v1);
    let live_before = fixture.registration();
    let receipt_before = receipt(paths).expect("v1 is committed");
    let retained_v1 = version_dir(paths, "1.0.0", p1);

    let platform =
        HookedPlatform::trusting(Hook::LaunchAfterStaging, fixture.proofs.borrow().clone());
    let failure = run_operation(
        &platform,
        paths,
        Operation::Install,
        Some(authenticate(&v2)),
    )
    .expect_err("a host launched during staging blocks the activation");
    assert_eq!(failure.code, FailureCode::HostActive);
    assert!(
        failure.prior_usable,
        "the live release is untouched and still verified"
    );

    // Nothing was activated, and nothing was deleted while a host ran.
    let calls = platform.inner.calls();
    for forbidden in [
        PlatformCall::PublishManifest,
        PlatformCall::PublishRegistration,
        PlatformCall::RemoveOwned,
    ] {
        assert!(
            !calls.contains(&forbidden),
            "{forbidden:?} ran although an owned host was running"
        );
    }
    assert_eq!(fixture.registration(), live_before);
    assert_eq!(receipt(paths), Some(receipt_before));
    assert_eq!(
        fs::read(retained_v1.join("host.exe")).expect("live payload"),
        HOST_V1,
        "the live payload is preserved"
    );
    // The staged version is retained (cleanup is deferred, never forced) and the
    // journal says so, so recovery - not a guess - settles it once the host exits.
    assert!(matches!(journal_state(paths), JournalState::Pending(_)));

    let report = recover(&fixture.platform(), paths).expect("recovery after the host exited");
    assert_eq!(
        report.state,
        veles_bridge_installer::STATE_RECOVERED_PREVIOUS
    );
    assert_eq!(fixture.registration(), live_before);
    assert!(matches!(journal_state(paths), JournalState::None));
    run_operation(
        &fixture.platform(),
        paths,
        Operation::Install,
        Some(authenticate(&v2)),
    )
    .expect("the install can be retried once the host is gone");
}

#[test]
fn late_host_launch_after_removal_reports_cleanup_pending() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let p1 = v1.descriptor.payload_id;
    fixture.install(&v1);
    let installed_dir = version_dir(paths, "1.0.0", p1);

    // The first two inspections are quiescent: the registration is removed and
    // the payload is deleted. Chrome then launches a host again, which only the
    // terminal inspection sees.
    let platform = RelaunchingPlatform::new(2);
    let failure = run_operation_within(&platform, paths, Operation::Uninstall, None, SHORT_WAIT)
        .expect_err("a host that launches after removal defers completion");
    assert_eq!(failure.code, FailureCode::CleanupPending);
    assert!(!paths.registration_path().exists());
    assert!(
        !installed_dir.exists(),
        "the payload was removed before the late launch was observed"
    );
    assert!(
        receipt(paths).is_some(),
        "durable state is kept until completion is established"
    );
    match journal_state(paths) {
        JournalState::Pending(journal) => {
            assert_eq!(journal.operation, Operation::Uninstall);
        }
        other => panic!("a deferred completion keeps its journal, not {other:?}"),
    }
    assert!(
        platform
            .calls()
            .iter()
            .filter(|call| **call == PlatformCall::InspectHosts)
            .count()
            >= 3,
        "removal performs a terminal quiescence check after deletion"
    );

    // Once the host is gone the retry establishes completion and releases state.
    let report = run_operation(&fixture.platform(), paths, Operation::Uninstall, None)
        .expect("the retried uninstall completes");
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(report.warnings.is_empty());
    assert!(receipt(paths).is_none());
    assert!(matches!(journal_state(paths), JournalState::None));

    // The path that uninstalls with nothing installed still performs the terminal
    // inspection and reports success without mutating anything.
    let empty = Fixture::windows();
    let platform = empty.platform();
    let report = run_operation(&platform, &empty.paths, Operation::Uninstall, None)
        .expect("nothing installed uninstalls");
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(report.warnings.is_empty());
    assert!(
        platform.calls().contains(&PlatformCall::InspectHosts),
        "the empty uninstall still establishes quiescence"
    );
    assert!(
        platform
            .mutations()
            .iter()
            .all(|call| *call == PlatformCall::RemoveOwned),
        "the empty uninstall only removes the fixed layout it created: {:?}",
        platform.mutations()
    );
    assert!(!empty.paths.root().exists());

    // An owned host that is still running defers even the empty uninstall.
    let active = FakePlatform::with_faults(Faults {
        host_state: HostState::Active,
        ..Faults::default()
    });
    let failure = run_operation_within(
        &active,
        &empty.paths,
        Operation::Uninstall,
        None,
        SHORT_WAIT,
    )
    .expect_err("an active owned host defers completion");
    assert_eq!(failure.code, FailureCode::CleanupPending);
    assert!(active.mutations().is_empty());
}

#[test]
fn rollback_warns_when_the_retained_api_window_does_not_overlap() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let v1 = fixture.package_with(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), vec![p1]),
        PUBLISHER,
        (1, 1),
    );
    let v2 = fixture.package_with(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, Vec::new(), vec![p1]),
        PUBLISHER,
        (2, 3),
    );
    fixture.install(&v1);
    fixture.install(&v2);

    let report = run_operation(
        &fixture.platform_trusting(&v1.proof),
        paths,
        Operation::Rollback,
        None,
    )
    .expect("the retained prior re-activates with a compatibility warning");
    assert_eq!(report.state, STATE_ROLLED_BACK);
    assert!(
        report.warns(WarningCode::CompatibilityWindow),
        "a retained API window outside the tested one is reported: {:?}",
        report.warnings
    );
}

#[test]
fn report_warnings_are_additive_bounded_and_deduplicated() {
    // A report written before the field existed still deserializes: warnings are
    // additive and default to empty.
    let legacy = br#"{"operation":"inspect","state":"absent","prior_usable":false,"active_payload":null,"failure":null}"#;
    let report: veles_bridge_installer::InstallReport =
        serde_json::from_slice(legacy).expect("a legacy report remains readable");
    assert!(report.warnings.is_empty());

    // Duplicates are collapsed and the bound is enforced at construction.
    let mut report = report.with_warnings([WarningCode::LeftoverFiles; 3]);
    report.warnings.push(WarningCode::CleanupPending);
    assert_eq!(
        report.warnings,
        vec![WarningCode::LeftoverFiles, WarningCode::CleanupPending]
    );
    let bounded = report.with_warnings(std::iter::repeat_n(WarningCode::CompatibilityWindow, 32));
    assert_eq!(bounded.warnings.len(), 3);
    assert!(bounded.warns(WarningCode::CompatibilityWindow));

    // A document with duplicate or over-bound warnings is rejected rather than
    // allowed to grow without limit.
    let duplicated = br#"{"operation":"inspect","state":"absent","prior_usable":false,"active_payload":null,"failure":null,"warnings":["leftover-files","leftover-files"]}"#;
    assert!(serde_json::from_slice::<veles_bridge_installer::InstallReport>(duplicated).is_err());
    let too_many = format!(
        "{{\"operation\":\"inspect\",\"state\":\"absent\",\"prior_usable\":false,\"active_payload\":null,\"failure\":null,\"warnings\":[{}]}}",
        (0..9)
            .map(|_| "\"cleanup-pending\"")
            .collect::<Vec<_>>()
            .join(",")
    );
    assert!(
        serde_json::from_str::<veles_bridge_installer::InstallReport>(&too_many).is_err(),
        "the warning list is bounded"
    );
}

/// F4: a retained metadata document and its receipt claim digest can both be
/// rewritten, but the retained signed carrier is still verified by the OS. The
/// forged signer/policy is never accepted.
#[test]
fn forged_retained_metadata_and_receipt_hash_is_rejected() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), vec![p1]),
    );
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, Vec::new(), vec![p1]),
    );
    fixture.install(&v1);
    fixture.install(&v2);

    let retained_relative = format!("versions/1.0.0-{}/{}", p1.to_hex(), RETAINED_FILE_NAME);
    let retained_path = paths.root().join(&retained_relative);
    let genuine_metadata = fs::read(&retained_path).expect("retained metadata");
    let genuine_receipt = receipt(paths).expect("receipt");

    // Forge the metadata to claim a different publisher set and a matching,
    // self-consistent carrier digest, then rewrite the receipt's claim digest
    // for that file. Only fresh OS verification of the in-place retained carrier
    // can tell this apart from the real release.
    let mut forged_descriptor = v1.descriptor.clone();
    forged_descriptor
        .policy
        .publishers
        .push("Forged Publisher".to_owned());
    let forged_carrier =
        write_carrier(&canonical_json(&forged_descriptor).expect("forged descriptor JSON"))
            .expect("forged carrier");
    let forged = RetainedInstall {
        schema: RETAINED_SCHEMA_VERSION,
        descriptor: forged_descriptor,
        carrier_path: paths
            .root()
            .join(format!("versions/1.0.0-{}/carrier.exe", p1.to_hex()))
            .to_str()
            .expect("UTF-8 fixture path")
            .to_owned(),
        carrier_sha256: Digest::of(&forged_carrier),
    };
    let forged_bytes = forged.to_bytes().expect("forged metadata bytes");
    fs::write(&retained_path, &forged_bytes).expect("forged retained metadata");
    let mut forged_receipt = genuine_receipt.clone();
    for claim in &mut forged_receipt.ownership.owned {
        if claim.path == retained_relative {
            claim.sha256 = Some(Digest::of(&forged_bytes));
        }
    }
    forged_receipt
        .write(&PortablePlatform::new(), paths)
        .expect("forged receipt with a matching claim digest");

    let registration = fixture.registration();
    let before = snapshot(fixture.temp.path());
    let platform = fixture.platform_trusting(&v1.proof);
    let failure = run_operation(&platform, paths, Operation::Rollback, None)
        .expect_err("the OS verdict does not match the forged metadata");
    assert_eq!(
        failure.code,
        FailureCode::TrustDenied,
        "forged signer/policy metadata is a trust failure"
    );
    assert!(
        platform.calls().contains(&PlatformCall::VerifyRetained),
        "the retained carrier goes through the OS verification path"
    );
    assert!(!platform.calls().contains(&PlatformCall::VerifyTrust));
    assert!(platform.mutations().is_empty());
    assert_eq!(snapshot(fixture.temp.path()), before);
    assert_eq!(fixture.registration(), registration);

    // Even a metadata document identical to the OS-verified carrier cannot
    // substitute bytes for the receipt-claimed carrier digest.
    fs::write(&retained_path, &genuine_metadata).expect("restore genuine metadata");
    let mut digest_forged = genuine_receipt;
    digest_forged
        .prior
        .as_mut()
        .expect("a retained prior")
        .carrier_sha256 = Digest::of(b"forged carrier bytes");
    digest_forged
        .write(&PortablePlatform::new(), paths)
        .expect("receipt with a forged carrier digest");
    let platform = fixture.platform_trusting(&v1.proof);
    let failure = run_operation(&platform, paths, Operation::Rollback, None)
        .expect_err("a carrier that does not match the committed digest is rejected");
    assert_eq!(failure.code, FailureCode::TrustDenied);
    assert!(platform.mutations().is_empty());
}

/// The retained metadata references the carrier installed *in place* inside the
/// immutable version directory. A rollback verifies that exact artifact - never
/// a copy and never the original download.
#[test]
fn rollback_verifies_the_installed_carrier_in_place() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), vec![p1]),
    );
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, Vec::new(), vec![p1]),
    );
    fixture.install(&v1);
    fixture.install(&v2);

    // The retained document names the carrier already installed in the version
    // directory; it stores no second copy of its bytes.
    let retained_relative = format!("versions/1.0.0-{}/{}", p1.to_hex(), RETAINED_FILE_NAME);
    let retained_bytes = fs::read(paths.root().join(&retained_relative))
        .expect("the retained metadata is installed");
    let retained = RetainedInstall::parse(&retained_bytes).expect("valid retained metadata");
    let installed_carrier = version_dir(paths, "1.0.0", p1).join("carrier.exe");
    assert_eq!(retained.schema, RETAINED_SCHEMA_VERSION);
    assert_eq!(
        retained.carrier_path,
        installed_carrier
            .to_str()
            .expect("UTF-8 fixture path")
            .to_owned(),
        "the retained metadata references the in-place carrier"
    );
    assert_eq!(
        retained.carrier_sha256,
        Digest::of(v1.proof.carrier()),
        "the retained digest is the authenticated carrier document"
    );
    assert!(
        !String::from_utf8_lossy(&retained_bytes).contains("carrier_hex"),
        "the metadata embeds no carrier copy"
    );
    assert_eq!(
        fs::read(&installed_carrier).expect("the installed carrier exists"),
        v1.proof.carrier(),
        "the installed artifact is the authenticated carrier"
    );

    // Remove the original package's carrier: a rollback that needed the
    // download, or a copy of it, could not succeed now.
    fs::remove_file(v1.root.join("carrier.exe")).expect("drop the original carrier artifact");

    let platform = fixture.platform_trusting(&v1.proof);
    let report = run_operation(&platform, paths, Operation::Rollback, None)
        .expect("the in-place carrier still verifies");
    assert_eq!(report.state, STATE_ROLLED_BACK);
    assert_eq!(report.active_payload, Some(p1));
    assert!(
        platform.calls().contains(&PlatformCall::VerifyRetained),
        "rollback re-runs the OS verification path"
    );
    assert!(!platform.calls().contains(&PlatformCall::VerifyTrust));
    assert!(
        !platform.mutations().contains(&PlatformCall::InstallCarrier),
        "rollback installs or copies no carrier"
    );
    assert_eq!(
        platform.retained_verifications(),
        vec![
            canonical_dir(&version_dir(paths, "2.0.0", payload_id(HOST_V2, NOTICE_V2))),
            canonical_dir(&version_dir(paths, "1.0.0", p1))
        ],
        "the OS path receives the version directory holding each in-place carrier: \
         the installed release whose signed policy bounds the receipt, then the target"
    );
    assert_eq!(
        fs::read(&installed_carrier).expect("the in-place carrier survives"),
        v1.proof.carrier()
    );
}

/// A carrier that was replaced after install cannot be authenticated in place:
/// the rollback is refused before any mutation.
#[test]
fn replaced_installed_carrier_is_rejected_before_rollback() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), vec![p1]),
    );
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, Vec::new(), vec![p1]),
    );
    fixture.install(&v1);
    fixture.install(&v2);

    let installed_carrier = version_dir(paths, "1.0.0", p1).join("carrier.exe");
    fs::write(&installed_carrier, b"replaced carrier artifact").expect("replaced carrier");

    let registration = fixture.registration();
    let saved_receipt = receipt(paths).expect("receipt");
    let before = snapshot(fixture.temp.path());
    let platform = fixture.platform_trusting(&v1.proof);
    let failure = run_operation(&platform, paths, Operation::Rollback, None)
        .expect_err("a replaced in-place carrier is not a rollback target");
    assert_eq!(failure.code, FailureCode::NoRollback);
    assert!(failure.prior_usable, "the installed release stays usable");
    assert!(platform.mutations().is_empty());
    assert_eq!(snapshot(fixture.temp.path()), before);
    assert_eq!(fixture.registration(), registration);
    assert_eq!(receipt(paths).expect("receipt"), saved_receipt);
}

/// The portable platform cannot install a carrier: a byte copy would drop the
/// metadata the platform signature covers, so the default fails closed before
/// any transaction starts.
#[test]
fn portable_platform_refuses_to_install_a_carrier() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let failure =
        veles_bridge_installer::install(&PortablePlatform::new(), paths, authenticate(&v1))
            .expect_err("the portable adapter has no carrier install implementation");
    assert_eq!(failure.code, FailureCode::UnsupportedTarget);
    assert!(
        !paths.versions().exists()
            || fs::read_dir(paths.versions()).expect("versions").count() == 0,
        "nothing was staged before the refusal"
    );
    assert!(matches!(journal_state(paths), JournalState::None));
}

/// F6: an owned staging artifact that cannot be deleted is a pending cleanup,
/// never a completed uninstall, and the durable receipt/journal survive so the
/// retry can finish.
#[test]
fn uninstall_reports_cleanup_pending_for_an_owned_staging_artifact() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&v1);
    let artifact = paths.staging().join("registration.json");
    fs::write(&artifact, b"staged registration manifest").expect("owned staging artifact");

    let platform = HookedPlatform::new(Hook::FailRemoval {
        path: "state/staging/registration.json".to_owned(),
    });
    let failure = run_operation_within(&platform, paths, Operation::Uninstall, None, SHORT_WAIT)
        .expect_err("an owned artifact that cannot be removed is not success");
    assert_eq!(failure.code, FailureCode::CleanupPending);
    assert!(artifact.exists(), "the artifact is preserved for the retry");
    assert!(
        receipt(paths).is_some(),
        "the durable removal intent survives a deferred cleanup"
    );
    match journal_state(paths) {
        JournalState::Pending(journal) => assert_eq!(journal.operation, Operation::Uninstall),
        other => panic!("the uninstall journal must stay pending, not {other:?}"),
    }

    let platform = fixture.platform();
    let report = run_operation_within(&platform, paths, Operation::Uninstall, None, SHORT_WAIT)
        .expect("the retried uninstall removes the artifact");
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(
        !artifact.exists(),
        "the owned artifact is removed by the retry"
    );
    assert!(receipt(paths).is_none());
    assert!(matches!(journal_state(paths), JournalState::None));
}

/// A staging path replaced while the uninstall runs is foreign content: it is
/// preserved *and* reported, never silently counted as an empty private slot.
#[test]
fn replaced_staging_paths_are_reported_as_leftovers() {
    // A regular file at `state/staging` is not an empty directory.
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&v1);
    let platform = HookedPlatform::new(Hook::ReplaceStagingWithFile { at: 1 });
    let report = run_operation_within(&platform, paths, Operation::Uninstall, None, SHORT_WAIT)
        .expect("a foreign staging file does not block the owned removal");
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(
        report.warns(WarningCode::LeftoverFiles),
        "a replaced staging path is a leftover: {:?}",
        report.warnings
    );
    let replaced = fs::symlink_metadata(paths.staging()).expect("the foreign staging path remains");
    assert!(replaced.is_file(), "the foreign staging file is preserved");
    assert_eq!(
        fs::read(paths.staging()).expect("foreign staging bytes"),
        b"foreign staging path"
    );

    // A symlink to an *empty* directory is a link, not an empty owned directory.
    #[cfg(unix)]
    {
        let fixture = Fixture::windows();
        let paths = &fixture.paths;
        let v1 = fixture.package(
            "v1",
            "1.0.0",
            HOST_V1,
            NOTICE_V1,
            policy(1, Vec::new(), Vec::new()),
        );
        fixture.install(&v1);
        let outside = fixture.temp.path().join("empty-outside");
        fs::create_dir_all(&outside).expect("empty outside directory");
        let platform = HookedPlatform::new(Hook::SymlinkStagingTo {
            target: outside.clone(),
            at: 1,
        });
        let report = run_operation_within(&platform, paths, Operation::Uninstall, None, SHORT_WAIT)
            .expect("a foreign staging link does not block the owned removal");
        assert_eq!(report.state, STATE_UNINSTALLED);
        assert!(
            report.warns(WarningCode::LeftoverFiles),
            "a staging symlink is a leftover even when its target is empty: {:?}",
            report.warnings
        );
        let metadata =
            fs::symlink_metadata(paths.staging()).expect("the foreign staging link remains");
        assert!(
            metadata.file_type().is_symlink(),
            "the staging path stays a link, never an owned directory"
        );
        assert_eq!(
            fs::read_dir(&outside).expect("outside directory").count(),
            0,
            "nothing was written through the link"
        );
    }
}

/// F6: foreign content inside a claimed version directory is preserved and
/// reported as leftovers; the rest of the uninstall still completes.
#[test]
fn uninstall_preserves_foreign_content_inside_a_claimed_version_directory() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&v1);
    let installed = version_dir(paths, "1.0.0", p1);
    let foreign = installed.join("foreign.txt");
    fs::write(&foreign, b"not ours").expect("foreign file inside the claimed version");

    let platform = fixture.platform();
    let report = run_operation(&platform, paths, Operation::Uninstall, None)
        .expect("foreign content does not block the owned removal");
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(
        report.warns(WarningCode::LeftoverFiles),
        "foreign content is reported: {:?}",
        report.warnings
    );
    assert_eq!(
        fs::read(&foreign).expect("foreign file preserved"),
        b"not ours"
    );
    assert!(
        !installed.join("host.exe").exists(),
        "the owned payload is removed"
    );
    assert!(receipt(paths).is_none());
    assert!(matches!(journal_state(paths), JournalState::None));

    // The second uninstall reports the same leftovers and mutates nothing.
    let platform = fixture.platform();
    let again = run_operation(&platform, paths, Operation::Uninstall, None)
        .expect("the second uninstall is idempotent");
    assert_eq!(again.warnings, vec![WarningCode::LeftoverFiles]);
    assert_eq!(
        fs::read(&foreign).expect("foreign file preserved"),
        b"not ours"
    );
    assert!(
        platform
            .mutations()
            .iter()
            .all(|call| *call == PlatformCall::RemoveOwned),
        "only the installer's own fixed layout is removed: {:?}",
        platform.mutations()
    );
}

/// C-B: pruning re-reads the live registration immediately before each deletion.
/// A retained version that becomes live during the quiescence wait survives.
#[test]
fn pruning_rechecks_the_live_registration_before_deleting() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let p2 = payload_id(HOST_V2, NOTICE_V2);
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, Vec::new(), Vec::new()),
    );
    let v3 = fixture.package(
        "v3",
        "3.0.0",
        HOST_V3,
        NOTICE_V3,
        policy(3, Vec::new(), Vec::new()),
    );
    let p3 = v3.descriptor.payload_id;
    fixture.install(&v1);
    fixture.install(&v2);

    // At the prune inspection the live registration already names the new v3
    // manifest; replace it with an owned manifest selecting the soon-superseded
    // v1 executable, as a launcher re-activating the old release would.
    let retained_v1 = version_dir(paths, "1.0.0", p1);
    let replacement = manifest_selecting(paths, &retained_v1.join("host.exe"));
    let platform = HookedPlatform::trusting(
        Hook::Reregister {
            when_executable_under: version_dir(paths, "3.0.0", p3)
                .to_str()
                .expect("UTF-8 fixture path")
                .to_owned(),
            replacement,
        },
        fixture.proofs.borrow().clone(),
    );
    let failure = run_operation(
        &platform,
        paths,
        Operation::Install,
        Some(authenticate(&v3)),
    )
    .expect_err("the prune aborts when the live registration changed");
    assert!(
        failure.code == FailureCode::CleanupPending || failure.code == FailureCode::ForeignState,
        "the changed activation is refused: {failure:?}"
    );
    assert_eq!(
        fs::read(retained_v1.join("host.exe")).expect("retained v1 payload"),
        HOST_V1,
        "the version that became live is never deleted"
    );
    assert_eq!(
        fs::read(retained_v1.join("licenses/NOTICE")).expect("retained v1 notice"),
        NOTICE_V1
    );
    assert_eq!(
        receipt(paths)
            .expect("the previous receipt survives")
            .version,
        "2.0.0",
        "the aborted upgrade never commits"
    );
    assert!(
        version_dir(paths, "2.0.0", p2).join("host.exe").exists(),
        "the previous release is untouched"
    );
}

/// F7: a crash after activation but before the prune pass leaves superseded
/// version claims in the replaced receipt. Recovery prunes them with the same
/// guards, so exactly one prior version remains and nothing is orphaned.
#[test]
fn recovery_prunes_superseded_versions_before_it_commits() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let p2 = payload_id(HOST_V2, NOTICE_V2);
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, Vec::new(), Vec::new()),
    );
    let v3 = fixture.package(
        "v3",
        "3.0.0",
        HOST_V3,
        NOTICE_V3,
        policy(3, Vec::new(), Vec::new()),
    );
    let p3 = v3.descriptor.payload_id;
    fixture.install(&v1);
    fixture.install(&v2);
    let superseded = version_dir(paths, "1.0.0", p1);

    let crashing = HookedPlatform::trusting(
        Hook::CrashBeforeRemoval {
            prefix: format!("versions/1.0.0-{}", p1.to_hex()),
        },
        fixture.proofs.borrow().clone(),
    );
    let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
        run_operation(
            &crashing,
            paths,
            Operation::Install,
            Some(authenticate(&v3)),
        )
    }));
    assert!(outcome.is_err(), "the injected death interrupts the prune");
    assert!(
        superseded.join("host.exe").exists(),
        "the crash happens before the superseded version is deleted"
    );
    assert_eq!(
        receipt(paths)
            .expect("the previous receipt survives")
            .version,
        "2.0.0"
    );

    let recovering = fixture.platform_trusting(&v3.proof);
    let report = recover(&recovering, paths).expect("recovery completes the interrupted install");
    assert_eq!(report.state, veles_bridge_installer::STATE_RECOVERED_ACTIVE);
    let committed = receipt(paths).expect("recovery commits the new receipt");
    assert_eq!(committed.version, "3.0.0");
    assert_eq!(
        committed.prior.as_ref().map(|prior| prior.version.as_str()),
        Some("2.0.0"),
        "exactly one prior version remains"
    );
    assert!(
        !superseded.exists(),
        "the superseded version is pruned instead of being orphaned without a claim"
    );
    assert!(version_dir(paths, "2.0.0", p2).join("host.exe").exists());
    assert!(version_dir(paths, "3.0.0", p3).join("host.exe").exists());
    assert_eq!(
        fs::read_dir(paths.versions())
            .expect("versions dir")
            .count(),
        2
    );
    assert!(
        !committed
            .ownership
            .owned
            .iter()
            .any(|claim| claim.path.starts_with("versions/1.0.0-")),
        "the committed receipt never names an orphaned version"
    );
}

/// C-C: cleanup validates ancestry immediately before every delete. Swapping
/// `state/staging` for a symlink must preserve the file it points at.
#[cfg(unix)]
#[test]
fn cleanup_never_deletes_through_a_swapped_ancestor() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&v1);

    let outside = fixture.temp.path().join("outside");
    fs::create_dir_all(&outside).expect("outside directory");
    let sentinel = outside.join("registration.json");
    fs::write(&sentinel, b"outside registration").expect("outside registration");

    let platform = HookedPlatform::new(Hook::SymlinkStaging {
        outside: outside.clone(),
        at: 1,
    });
    let report = run_operation_within(&platform, paths, Operation::Uninstall, None, SHORT_WAIT)
        .expect("the swapped ancestor is preserved as foreign content");
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(report.warns(WarningCode::LeftoverFiles));
    assert_eq!(
        fs::read(&sentinel).expect("outside registration preserved"),
        b"outside registration",
        "cleanup never deletes through a swapped ancestor"
    );
    assert!(sentinel.exists());
    assert!(receipt(paths).is_none());
}

/// Policy evidence that a rollback replaced a newer release must not depend on the
/// editable `operation` tags. Every case below mutates ONLY operation tags and
/// policy/epoch fields of an honest v2 -> v1 rollback (v1 signs no denial at epoch 1,
/// v2 signs `denied` at epoch 2) and must be refused with nothing written:
///
/// * a live install over the rolled-back receipt, tag changed to `Install` and the
///   v2-only denial removed while the epoch-2 stays (an epoch-2 v3 without the
///   denial would otherwise pass);
/// * a live repair of v1 against a receipt whose tag, epoch and policy are v1's;
/// * recovery after the receipt commit with BOTH operation tags changed to `Install`
///   and both persisted policies/epochs set to v1's signed policy; and
/// * recovery before the receipt commit with the journal tag changed to `Install`
///   and the journal policy set to v1's.
fn n6_denied() -> Digest {
    Digest::of(b"a payload only the newer release denies")
}

/// v1 (epoch 1, no denial) and v2 (epoch 2, signs `n6_denied`).
fn n6_build(fixture: &Fixture) -> (Package, Package) {
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), vec![p1]),
    );
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, vec![n6_denied()], vec![p1]),
    );
    (v1, v2)
}

#[test]
fn an_edited_operation_tag_cannot_hide_the_release_a_rollback_replaced_in_a_live_install() {
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let denied = n6_denied();
    let build = n6_build;
    {
        let fixture = Fixture::windows();
        let paths = &fixture.paths;
        let (v1, v2) = build(&fixture);
        // Same epoch as the rollback receipt, the replaced release's denial absent.
        let v3 = fixture.package(
            "v3",
            "3.0.0",
            HOST_V3,
            NOTICE_V3,
            policy(2, Vec::new(), vec![p1]),
        );
        fixture.install(&v1);
        fixture.install(&v2);
        run_operation(
            &fixture.platform_trusting(&v1.proof),
            paths,
            Operation::Rollback,
            Some(authenticate(&v1)),
        )
        .expect("the honest rollback completes");
        let honest = receipt(paths).expect("receipt");
        assert!(honest.release_policy.denies(&denied), "rollback kept it");
        assert_eq!(honest.release_policy.epoch, 2);

        // Install: only the tag and the denial are edited; the epoch stays 2.
        let mut forged = honest.clone();
        forged.operation = Operation::Install;
        forged
            .release_policy
            .denied_payloads
            .retain(|d| *d != denied);
        forged.write(&fixture.platform(), paths).expect("forged");
        let before = snapshot(fixture.temp.path());
        let platform = fixture.platform();
        let outcome = run_operation(
            &platform,
            paths,
            Operation::Install,
            Some(authenticate(&v3)),
        );
        assert_eq!(
            failure_code(outcome),
            FailureCode::RecoveryRequired,
            "install over a receipt whose tag and policy were edited"
        );
        assert!(platform.mutations().is_empty());
        assert_eq!(snapshot(fixture.temp.path()), before);

        // The recorded prior is as editable as the tag: naming an older release
        // there cannot hide that the receipt still owns the newer one's directory.
        forged
            .prior
            .as_mut()
            .expect("a rollback has a prior")
            .version = "0.5.0".to_owned();
        forged.write(&fixture.platform(), paths).expect("forged");
        let before = snapshot(fixture.temp.path());
        let platform = fixture.platform();
        let outcome = run_operation(
            &platform,
            paths,
            Operation::Install,
            Some(authenticate(&v3)),
        );
        assert_eq!(
            failure_code(outcome),
            FailureCode::RecoveryRequired,
            "install over a receipt whose tag, policy and prior were edited"
        );
        assert!(platform.mutations().is_empty());
        assert_eq!(snapshot(fixture.temp.path()), before);

        // And the other way round: the recorded prior is honest (strictly newer) but
        // the receipt no longer owns the newer release's directory.
        forged.prior = honest.prior.clone();
        let newer = format!("versions/2.0.0-{}", v2.descriptor.payload_id.to_hex());
        forged
            .ownership
            .owned
            .retain(|claim| claim.path != newer && !claim.path.starts_with(&format!("{newer}/")));
        forged.write(&fixture.platform(), paths).expect("forged");
        let before = snapshot(fixture.temp.path());
        let platform = fixture.platform();
        let outcome = run_operation(
            &platform,
            paths,
            Operation::Install,
            Some(authenticate(&v3)),
        );
        assert_eq!(
            failure_code(outcome),
            FailureCode::RecoveryRequired,
            "install over a receipt whose tag, policy and claims were edited"
        );
        assert!(platform.mutations().is_empty());
        assert_eq!(snapshot(fixture.temp.path()), before);
    }

    // --- control: the honest receipt still lets a higher-epoch release in -------
    {
        let fixture = Fixture::windows();
        let paths = &fixture.paths;
        let (v1, v2) = build(&fixture);
        let v3 = fixture.package(
            "v3",
            "3.0.0",
            HOST_V3,
            NOTICE_V3,
            policy(3, Vec::new(), vec![p1]),
        );
        fixture.install(&v1);
        fixture.install(&v2);
        run_operation(
            &fixture.platform_trusting(&v1.proof),
            paths,
            Operation::Rollback,
            Some(authenticate(&v1)),
        )
        .expect("the honest rollback completes");
        run_operation(
            &fixture.platform(),
            paths,
            Operation::Install,
            Some(authenticate(&v3)),
        )
        .expect("a higher-epoch release installs over the honest rollback receipt");
    }
}

#[test]
fn an_edited_operation_tag_cannot_hide_the_release_a_rollback_replaced_in_a_repair() {
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let denied = n6_denied();
    {
        let fixture = Fixture::windows();
        let paths = &fixture.paths;
        let (v1, v2) = n6_build(&fixture);
        fixture.install(&v1);
        fixture.install(&v2);
        run_operation(
            &fixture.platform_trusting(&v1.proof),
            paths,
            Operation::Rollback,
            Some(authenticate(&v1)),
        )
        .expect("the honest rollback completes");
        let mut forged = receipt(paths).expect("receipt");
        assert!(forged.release_policy.denies(&denied), "rollback kept it");
        forged.operation = Operation::Install;
        // Repair: tag, epoch and policy all become v1's own signed policy.
        forged.release_policy = v1.descriptor.policy.clone();
        forged.policy.epoch = v1.descriptor.policy.epoch;
        forged.write(&fixture.platform(), paths).expect("forged");
        fs::remove_file(version_dir(paths, "1.0.0", p1).join("licenses/NOTICE"))
            .expect("damage the installed release");
        let before = snapshot(fixture.temp.path());
        let platform = fixture.platform();
        let outcome = run_operation(&platform, paths, Operation::Repair, Some(authenticate(&v1)));
        assert_eq!(
            failure_code(outcome),
            FailureCode::RecoveryRequired,
            "repair over a receipt whose tag, epoch and policy were edited"
        );
        assert!(platform.mutations().is_empty());
        assert_eq!(snapshot(fixture.temp.path()), before);
    }
}

#[test]
fn an_edited_operation_tag_cannot_hide_the_release_a_rollback_replaced_in_recovery() {
    let denied = n6_denied();
    let _ = denied;
    let build = n6_build;
    // --- recovery of an interrupted rollback, tags and policies edited ----------
    let trusting = |v1: &Package, v2: &Package, panic_at: Option<usize>| {
        FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            trust: Some(v1.proof.clone()),
            trust_by_carrier: vec![v1.proof.clone(), v2.proof.clone()],
            panic_at_mutation: panic_at,
            ..Faults::default()
        })
    };
    let probe = Fixture::windows();
    let (probe_v1, probe_v2) = build(&probe);
    probe.install(&probe_v1);
    probe.install(&probe_v2);
    let counting = trusting(&probe_v1, &probe_v2, None);
    run_operation(
        &counting,
        &probe.paths,
        Operation::Rollback,
        Some(authenticate(&probe_v1)),
    )
    .expect("the unedited rollback completes");
    let total = counting.mutations().len();

    let mut wrong: Vec<String> = Vec::new();
    let mut after_receipt = 0;
    let mut before_receipt = 0;
    for attempt in 1..=total {
        for forge in [false, true] {
            let fixture = Fixture::windows();
            let paths = &fixture.paths;
            let (v1, v2) = build(&fixture);
            fixture.install(&v1);
            fixture.install(&v2);
            let crashing = trusting(&v1, &v2, Some(attempt));
            let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
                run_operation(
                    &crashing,
                    paths,
                    Operation::Rollback,
                    Some(authenticate(&v1)),
                )
            }));
            assert!(outcome.is_err(), "attempt {attempt}: the death interrupts");
            let JournalState::Pending(journal) = journal_state(paths) else {
                continue;
            };
            let current = receipt(paths).expect("a receipt always exists");
            let committed = current.payload_id == v1.descriptor.payload_id;
            let activated =
                !committed && Digest::of(&fixture.registration()) == journal.manifest.sha256();
            if !committed && !activated {
                continue;
            }
            let context = format!(
                "attempt {attempt} ({}, {})",
                if committed {
                    "receipt committed"
                } else {
                    "activated, no receipt"
                },
                if forge {
                    "tags and policy edited"
                } else {
                    "control"
                }
            );
            if forge {
                let writer = trusting(&v1, &v2, None);
                let mut forged_journal = (*journal).clone();
                forged_journal.operation = Operation::Install;
                forged_journal.release_policy = v1.descriptor.policy.clone();
                forged_journal.policy.epoch = v1.descriptor.policy.epoch;
                forged_journal
                    .write(&writer, paths)
                    .expect("forged journal");
                if committed {
                    let mut forged_receipt = current.clone();
                    forged_receipt.operation = Operation::Install;
                    forged_receipt.release_policy = v1.descriptor.policy.clone();
                    forged_receipt.policy.epoch = v1.descriptor.policy.epoch;
                    forged_receipt
                        .write(&writer, paths)
                        .expect("forged receipt");
                }
            }
            let before = snapshot(fixture.temp.path());
            let recovering = trusting(&v1, &v2, None);
            match (forge, recover(&recovering, paths)) {
                (false, Err(failure)) => wrong.push(format!(
                    "{context}: honest recovery was refused: {:?}",
                    failure.code
                )),
                (false, Ok(_)) => {}
                (true, Ok(report)) => wrong.push(format!(
                    "{context}: recovery completed on edited tags and policy: {:?}",
                    report.state
                )),
                (true, Err(_)) => {
                    if !recovering.mutations().is_empty() || snapshot(fixture.temp.path()) != before
                    {
                        wrong.push(format!("{context}: a refused recovery changed state"));
                    }
                }
            }
            if forge {
                if committed {
                    after_receipt += 1;
                } else {
                    before_receipt += 1;
                }
            }
        }
    }
    assert!(
        before_receipt >= 1 && after_receipt >= 1,
        "both windows were exercised: before={before_receipt} after={after_receipt}"
    );
    assert!(wrong.is_empty(), "{wrong:#?}");
}

/// Two different signed releases may share identical canonical payload bytes (for
/// example a policy-only maintenance release): the payload id hashes the inventory,
/// while version and policy live in the separately signed descriptor and carrier. The
/// release ordering, not the payload id, tells a rollback's target from the release
/// it replaced. v1.0.0 and v2.0.0 here share one payload, so:
///
/// * an uninterrupted v2 -> v1 rollback must be followed by a higher-epoch upgrade;
/// * recovery of the interrupted rollback must complete in EVERY crash window, and
///   the installation must then still accept that upgrade; and
/// * rewriting the replaced release as the target itself (the N5 substitution) must
///   still be refused after the receipt commit. The version ordering and the carrier
///   inequality both refuse it; the ordering is defence in depth, not shown to be
///   independently necessary (removing it leaves this suite passing).
#[test]
fn releases_sharing_a_payload_roll_back_recover_and_upgrade() {
    let p = payload_id(HOST_V1, NOTICE_V1);
    let denied = Digest::of(b"a payload only the newer release denies");
    // Identical payload bytes, different signed descriptors.
    let build = |fixture: &Fixture| {
        let v1 = fixture.package(
            "v1",
            "1.0.0",
            HOST_V1,
            NOTICE_V1,
            policy(1, Vec::new(), vec![p]),
        );
        let v2 = fixture.package(
            "v2",
            "2.0.0",
            HOST_V1,
            NOTICE_V1,
            policy(2, vec![denied], vec![p]),
        );
        let v3 = fixture.package(
            "v3",
            "3.0.0",
            HOST_V3,
            NOTICE_V3,
            policy(3, Vec::new(), vec![p]),
        );
        (v1, v2, v3)
    };
    let trusting = |fixture: &Fixture, panic_at: Option<usize>| {
        FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            trust_by_carrier: fixture.proofs.borrow().clone(),
            panic_at_mutation: panic_at,
            ..Faults::default()
        })
    };

    // Uninterrupted rollback, then the forward upgrade at a higher epoch.
    let probe = Fixture::windows();
    let (v1, v2, v3) = build(&probe);
    assert_eq!(v1.descriptor.payload_id, v2.descriptor.payload_id);
    assert_ne!(v1.proof.carrier(), v2.proof.carrier());
    probe.install(&v1);
    probe.install(&v2);
    let counting = trusting(&probe, None);
    run_operation(
        &counting,
        &probe.paths,
        Operation::Rollback,
        Some(authenticate(&v1)),
    )
    .expect("a rollback between releases that share a payload completes");
    let total = counting.mutations().len();
    let rolled_back = receipt(&probe.paths).expect("receipt");
    assert_eq!(rolled_back.version, "1.0.0");
    assert_eq!(rolled_back.operation, Operation::Rollback);
    let mut wrong: Vec<String> = Vec::new();
    match run_operation(
        &trusting(&probe, None),
        &probe.paths,
        Operation::Install,
        Some(authenticate(&v3)),
    ) {
        Ok(_) => assert_eq!(receipt(&probe.paths).expect("receipt").version, "3.0.0"),
        Err(failure) => wrong.push(format!(
            "uninterrupted rollback: the higher-epoch upgrade was refused: {:?}",
            failure.code
        )),
    }

    let mut after_receipt = 0;
    let mut substituted = 0;
    for attempt in 1..=total {
        for rewrite in [false, true] {
            let fixture = Fixture::windows();
            let paths = &fixture.paths;
            let (v1, v2, v3) = build(&fixture);
            fixture.install(&v1);
            fixture.install(&v2);
            let crashing = trusting(&fixture, Some(attempt));
            let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
                run_operation(
                    &crashing,
                    paths,
                    Operation::Rollback,
                    Some(authenticate(&v1)),
                )
            }));
            assert!(outcome.is_err(), "attempt {attempt}: the death interrupts");
            let JournalState::Pending(journal) = journal_state(paths) else {
                continue;
            };
            let current = receipt(paths).expect("a receipt always exists");
            let committed = current.version == "1.0.0";
            if rewrite && !committed {
                continue;
            }
            let context = format!("attempt {attempt} (committed={committed}, rewrite={rewrite})");
            if rewrite {
                // The N5 substitution: the denial removed and the replaced release
                // rewritten as the target itself. The payload ids now agree, so the
                // refusal rests on the combined version-ordering and carrier-identity
                // guards, neither of which is shown to be necessary on its own.
                let writer = trusting(&fixture, None);
                let mut forged_journal = (*journal).clone();
                let mut forged_receipt = current.clone();
                for policy in [
                    &mut forged_journal.release_policy,
                    &mut forged_receipt.release_policy,
                ] {
                    policy.denied_payloads.retain(|d| *d != denied);
                }
                forged_journal.old_version = Some(v1.descriptor.version.clone());
                forged_journal.old_payload_id = Some(v1.descriptor.payload_id);
                forged_journal.old_carrier_sha256 = Some(Digest::of(v1.proof.carrier()));
                let prior = forged_receipt.prior.as_mut().expect("a rollback prior");
                prior.version = v1.descriptor.version.clone();
                prior.payload_id = v1.descriptor.payload_id;
                prior.carrier_sha256 = Digest::of(v1.proof.carrier());
                forged_journal
                    .write(&writer, paths)
                    .expect("forged journal");
                forged_receipt
                    .write(&writer, paths)
                    .expect("forged receipt");
            }
            let before = snapshot(fixture.temp.path());
            let recovering = trusting(&fixture, None);
            match (rewrite, recover(&recovering, paths)) {
                (false, Err(failure)) => {
                    wrong.push(format!(
                        "{context}: honest recovery was refused: {:?}",
                        failure.code
                    ));
                    continue;
                }
                (false, Ok(_)) => {}
                (true, Ok(report)) => {
                    wrong.push(format!(
                        "{context}: recovery completed on rewritten history: {:?}",
                        report.state
                    ));
                    continue;
                }
                (true, Err(_)) => {
                    if !recovering.mutations().is_empty() || snapshot(fixture.temp.path()) != before
                    {
                        wrong.push(format!("{context}: a refused recovery changed state"));
                    }
                    substituted += 1;
                    continue;
                }
            }
            if committed {
                after_receipt += 1;
            }
            // The recovered installation still accepts the higher-epoch upgrade.
            if let Err(failure) = run_operation(
                &trusting(&fixture, None),
                paths,
                Operation::Install,
                Some(authenticate(&v3)),
            ) {
                wrong.push(format!(
                    "{context}: the upgrade after recovery was refused: {:?}",
                    failure.code
                ));
            }
        }
    }
    assert!(wrong.is_empty(), "{wrong:#?}");
    assert!(
        after_receipt >= 1 && substituted >= 1,
        "the post-receipt windows were exercised: recovered={after_receipt} refused={substituted}"
    );
}

/// A committed receipt carries the operation of the very transaction that wrote it.
/// Retagging only ONE of the two selects the install-completion branch (journal) or
/// leaves a rollback history tagged as an install (receipt); both are refused with
/// nothing written, in every post-receipt crash window.
#[test]
fn a_journal_and_receipt_that_disagree_on_the_operation_are_refused() {
    #[derive(Clone, Copy, Debug)]
    enum Retag {
        /// Journal tag and both policies are edited; the receipt keeps `Rollback`.
        JournalOnly,
        /// Only the receipt tag is edited.
        ReceiptOnly,
    }
    let trusting = |v1: &Package, v2: &Package, panic_at: Option<usize>| {
        FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            trust: Some(v1.proof.clone()),
            trust_by_carrier: vec![v1.proof.clone(), v2.proof.clone()],
            panic_at_mutation: panic_at,
            ..Faults::default()
        })
    };
    let probe = Fixture::windows();
    let (probe_v1, probe_v2) = n6_build(&probe);
    probe.install(&probe_v1);
    probe.install(&probe_v2);
    let counting = trusting(&probe_v1, &probe_v2, None);
    run_operation(
        &counting,
        &probe.paths,
        Operation::Rollback,
        Some(authenticate(&probe_v1)),
    )
    .expect("the unedited rollback completes");
    let total = counting.mutations().len();

    let mut wrong: Vec<String> = Vec::new();
    let mut exercised = 0;
    for attempt in 1..=total {
        for retag in [Retag::JournalOnly, Retag::ReceiptOnly] {
            let fixture = Fixture::windows();
            let paths = &fixture.paths;
            let (v1, v2) = n6_build(&fixture);
            fixture.install(&v1);
            fixture.install(&v2);
            let crashing = trusting(&v1, &v2, Some(attempt));
            let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
                run_operation(
                    &crashing,
                    paths,
                    Operation::Rollback,
                    Some(authenticate(&v1)),
                )
            }));
            assert!(outcome.is_err(), "attempt {attempt}: the death interrupts");
            let JournalState::Pending(journal) = journal_state(paths) else {
                continue;
            };
            let current = receipt(paths).expect("a receipt always exists");
            if current.payload_id != v1.descriptor.payload_id {
                continue;
            }
            let writer = trusting(&v1, &v2, None);
            match retag {
                Retag::JournalOnly => {
                    let mut forged_journal = (*journal).clone();
                    forged_journal.operation = Operation::Install;
                    forged_journal.release_policy = v1.descriptor.policy.clone();
                    forged_journal.policy.epoch = v1.descriptor.policy.epoch;
                    forged_journal
                        .write(&writer, paths)
                        .expect("forged journal");
                    let mut forged_receipt = current.clone();
                    forged_receipt.release_policy = v1.descriptor.policy.clone();
                    forged_receipt.policy.epoch = v1.descriptor.policy.epoch;
                    forged_receipt
                        .write(&writer, paths)
                        .expect("forged receipt");
                }
                Retag::ReceiptOnly => {
                    let mut forged_receipt = current.clone();
                    forged_receipt.operation = Operation::Install;
                    forged_receipt
                        .write(&writer, paths)
                        .expect("forged receipt");
                }
            }
            let before = snapshot(fixture.temp.path());
            let recovering = trusting(&v1, &v2, None);
            let context = format!("attempt {attempt} ({retag:?})");
            match recover(&recovering, paths) {
                Ok(report) => wrong.push(format!(
                    "{context}: recovery completed over disagreeing tags: {:?}",
                    report.state
                )),
                Err(_) => {
                    if !recovering.mutations().is_empty() || snapshot(fixture.temp.path()) != before
                    {
                        wrong.push(format!("{context}: a refused recovery changed state"));
                    }
                }
            }
            exercised += 1;
        }
    }
    assert!(wrong.is_empty(), "{wrong:#?}");
    assert!(exercised >= 2, "the post-receipt window was exercised");
}

/// The history a rollback leaves must be read from authenticated versions, never from
/// a receipt field that nothing authenticates, and never without the journal's own
/// downgrade record.
///
/// After a v2 -> v1 rollback interrupted after the receipt commit, both operation tags
/// are retagged `Install` and both policies/epochs set to v1's (the exact edit round 5
/// closed), AND one more edit is made so the receipt no longer shows the downgrade:
///
/// * `VersionMasked` (N6-A): ONLY `receipt.version` becomes `3.0.0`. Every claim, the
///   recorded prior and the whole v2 directory stay. Judged against v3, neither the
///   prior v2 nor the owned v2 directory is "newer", yet the journal authenticates v1.
/// * `HistoryRemoved` (N6-B): `receipt.version` stays honest, but `receipt.prior` and
///   every ownership claim under v2's version directory are removed. The journal still
///   records `old_version = 2.0.0`, the old manifest and the complete v2 directory.
///
/// * `VersionMaskedJournalLowered`: `VersionMasked` and the journal's `old_version`
///   lowered too. Every v2 reference in the receipt stays; only the unauthenticated
///   receipt version hides them, so the receipt must be made to name the release the
///   journal authenticates.
///
/// All must be refused before any mutation, in every post-receipt crash window.
#[test]
fn a_receipt_edited_to_hide_the_downgrade_cannot_make_recovery_install_the_older_release() {
    #[derive(Clone, Copy, Debug)]
    enum Mask {
        VersionMasked,
        HistoryRemoved,
        /// `VersionMasked` plus the journal's `old_version` lowered, so the journal's
        /// own downgrade record is gone too. The receipt still owns v2 and records it
        /// as the prior: only the receipt's unauthenticated version hides them.
        VersionMaskedJournalLowered,
    }
    let trusting = |v1: &Package, v2: &Package, panic_at: Option<usize>| {
        FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            trust: Some(v1.proof.clone()),
            trust_by_carrier: vec![v1.proof.clone(), v2.proof.clone()],
            panic_at_mutation: panic_at,
            ..Faults::default()
        })
    };
    let probe = Fixture::windows();
    let (probe_v1, probe_v2) = n6_build(&probe);
    probe.install(&probe_v1);
    probe.install(&probe_v2);
    let counting = trusting(&probe_v1, &probe_v2, None);
    run_operation(
        &counting,
        &probe.paths,
        Operation::Rollback,
        Some(authenticate(&probe_v1)),
    )
    .expect("the unedited rollback completes");
    let total = counting.mutations().len();

    let mut wrong: Vec<String> = Vec::new();
    let mut exercised = [0usize; 3];
    for attempt in 1..=total {
        for (index, mask) in [
            Mask::VersionMasked,
            Mask::HistoryRemoved,
            Mask::VersionMaskedJournalLowered,
        ]
        .into_iter()
        .enumerate()
        {
            let fixture = Fixture::windows();
            let paths = &fixture.paths;
            let (v1, v2) = n6_build(&fixture);
            fixture.install(&v1);
            fixture.install(&v2);
            let crashing = trusting(&v1, &v2, Some(attempt));
            let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
                run_operation(
                    &crashing,
                    paths,
                    Operation::Rollback,
                    Some(authenticate(&v1)),
                )
            }));
            assert!(outcome.is_err(), "attempt {attempt}: the death interrupts");
            let JournalState::Pending(journal) = journal_state(paths) else {
                continue;
            };
            let current = receipt(paths).expect("a receipt always exists");
            if current.payload_id != v1.descriptor.payload_id {
                continue;
            }
            let writer = trusting(&v1, &v2, None);
            let mut forged_journal = (*journal).clone();
            forged_journal.operation = Operation::Install;
            forged_journal.release_policy = v1.descriptor.policy.clone();
            forged_journal.policy.epoch = v1.descriptor.policy.epoch;
            if matches!(mask, Mask::VersionMaskedJournalLowered) {
                forged_journal.old_version = Some("0.5.0".to_owned());
            }
            forged_journal
                .write(&writer, paths)
                .expect("forged journal");
            let mut forged_receipt = current.clone();
            forged_receipt.operation = Operation::Install;
            forged_receipt.release_policy = v1.descriptor.policy.clone();
            forged_receipt.policy.epoch = v1.descriptor.policy.epoch;
            match mask {
                Mask::VersionMasked | Mask::VersionMaskedJournalLowered => {
                    forged_receipt.version = "3.0.0".to_owned();
                }
                Mask::HistoryRemoved => {
                    forged_receipt.prior = None;
                    forged_receipt
                        .ownership
                        .owned
                        .retain(|claim| !claim.path.starts_with("versions/2.0.0-"));
                }
            }
            forged_receipt
                .write(&writer, paths)
                .expect("forged receipt");
            // The evidence the edits must not have disturbed.
            assert_eq!(journal.old_version.as_deref(), Some("2.0.0"));
            let before = snapshot(fixture.temp.path());
            let recovering = trusting(&v1, &v2, None);
            let context = format!("attempt {attempt} ({mask:?})");
            match recover(&recovering, paths) {
                Ok(report) => wrong.push(format!(
                    "{context}: recovery installed the older release over a recorded \
                     downgrade: {:?}",
                    report.state
                )),
                Err(_) => {
                    if !recovering.mutations().is_empty() || snapshot(fixture.temp.path()) != before
                    {
                        wrong.push(format!("{context}: a refused recovery changed state"));
                    }
                }
            }
            exercised[index] += 1;
        }
    }
    assert!(wrong.is_empty(), "{wrong:#?}");
    assert!(
        exercised.iter().all(|count| *count >= 1),
        "the post-receipt window was exercised for every edit: {exercised:?}"
    );
}

// -- F3: host selection is separate from executable permission --------------------------------
//
// The Windows producer ships THREE executable files (the Native Messaging host, the cleanup helper and the
// uninstaller). The engine used to equate "executable" with "the one host" and refused that legitimate
// payload with `invalid-package`. These tests feed the producer's real payload shape through the whole
// engine: install, inspect, repair, upgrade and rollback.

const HELPER: &[u8] = b"cleanup helper image";
const UNINSTALLER: &[u8] = b"signed uninstaller image";
const WINDOWS_HOST: &str = "veles-native-host.exe";

fn assembled_files<'a>(host: &'a [u8], notice: &'a [u8]) -> Vec<(&'static str, &'a [u8], bool)> {
    // The exact entries `assemble_unsigned.py` writes for Windows - three executables and a notice - in the
    // producer's sorted-by-path order, so the host is the LAST executable listed, not the first.
    vec![
        ("licenses/NOTICE", notice, false),
        ("maintenance/uninstall.exe", UNINSTALLER, true),
        ("veles-bridge-cleanup.exe", HELPER, true),
        (WINDOWS_HOST, host, true),
    ]
}

impl Fixture {
    fn assembled(&self, name: &str, version: &str, host: &[u8], policy: ReleasePolicy) -> Package {
        self.assembled_from(name, version, &assembled_files(host, NOTICE_V1), policy)
    }

    fn assembled_from(
        &self,
        name: &str,
        version: &str,
        files: &[(&str, &[u8], bool)],
        policy: ReleasePolicy,
    ) -> Package {
        let package = build_package_from(
            &self.temp,
            self.target,
            name,
            version,
            files,
            policy,
            PUBLISHER,
            (1, 2),
        );
        self.proofs.borrow_mut().push(package.proof.clone());
        package
    }
}

fn assembled_payload_id(host: &[u8]) -> Digest {
    let entries: Vec<InventoryEntry> = assembled_files(host, NOTICE_V1)
        .into_iter()
        .map(|(path, bytes, executable)| InventoryEntry {
            path: path.to_owned(),
            size: bytes.len() as u64,
            sha256: Digest::of(bytes),
            executable,
        })
        .collect();
    Digest::of(&write_inventory(&entries).expect("canonical fixture inventory"))
}

#[test]
fn the_producers_three_executable_windows_payload_installs_repairs_upgrades_and_rolls_back() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let p1 = assembled_payload_id(HOST_V1);
    let v1 = fixture.assembled("asm-v1", "1.0.0", HOST_V1, policy(1, Vec::new(), vec![p1]));
    let v2 = fixture.assembled("asm-v2", "2.0.0", HOST_V2, policy(2, Vec::new(), vec![p1]));

    // Install: the host - not a helper or the uninstaller - is what the registration selects.
    let installed = fixture.install(&v1);
    assert_eq!(installed.state, veles_bridge_installer::STATE_INSTALLED);
    let manifest = parse_manifest(&fixture.registration()).expect("complete registration");
    let selected = manifest.executable().expect("absolute executable");
    let v1_dir = version_dir(paths, "1.0.0", p1);
    assert_eq!(selected, v1_dir.join(WINDOWS_HOST));
    for support in ["veles-bridge-cleanup.exe", "maintenance/uninstall.exe"] {
        assert!(
            v1_dir.join(support).is_file(),
            "support executable {support} is installed"
        );
    }

    // Inspect: the installation is complete and usable.
    let platform = fixture.platform();
    let inspected = run_operation(&platform, paths, Operation::Inspect, None).expect("inspect");
    assert_eq!(inspected.state, veles_bridge_installer::STATE_INSTALLED);
    assert!(inspected.prior_usable, "{inspected:?}");

    // Repair: a damaged managed file (the cleanup helper) is restored from the same-version package.
    fs::remove_file(v1_dir.join("veles-bridge-cleanup.exe")).expect("damage a support file");
    let platform = fixture.platform();
    let repaired = run_operation(&platform, paths, Operation::Repair, Some(authenticate(&v1)))
        .expect("repair of the producer's payload");
    assert_eq!(repaired.state, STATE_REPAIRED);
    assert_eq!(
        fs::read(v1_dir.join("veles-bridge-cleanup.exe")).expect("restored helper"),
        HELPER
    );

    // Upgrade, then rollback to the retained release.
    fixture.install(&v2);
    let platform = fixture.platform_trusting(&v1.proof);
    let rolled = run_operation(
        &platform,
        paths,
        Operation::Rollback,
        Some(authenticate(&v1)),
    )
    .expect("rollback of the producer's payload");
    assert_eq!(rolled.state, STATE_ROLLED_BACK);
    let manifest = parse_manifest(&fixture.registration()).expect("complete registration");
    assert_eq!(
        manifest.executable().expect("absolute executable"),
        v1_dir.join(WINDOWS_HOST)
    );
    let platform = fixture.platform();
    assert!(
        run_operation(&platform, paths, Operation::Inspect, None)
            .expect("inspect")
            .prior_usable
    );
}

#[test]
fn an_executable_that_is_neither_the_host_nor_a_named_support_role_is_an_ambiguous_payload() {
    let fixture = Fixture::windows();
    let mut files = assembled_files(HOST_V1, NOTICE_V1);
    files.push(("extra-tool.exe", b"unlisted executable", true));
    let package =
        fixture.assembled_from("extra", "1.0.0", &files, policy(1, Vec::new(), Vec::new()));
    let platform = FakePlatform::with_faults(Faults {
        trust: Some(package.proof.clone()),
        host_state: HostState::Quiescent,
        ..Faults::default()
    });
    let outcome = authenticate_package(&platform, &package.root, &package.descriptor).and_then(
        |authenticated| {
            run_operation(
                &platform,
                &fixture.paths,
                Operation::Install,
                Some(authenticated),
            )
        },
    );
    assert_eq!(failure_code(outcome), FailureCode::InvalidPackage);
    assert!(
        !fixture.paths.versions().exists()
            || fs::read_dir(fixture.paths.versions()).unwrap().count() == 0,
        "nothing is installed for an ambiguous payload"
    );
}

#[test]
fn support_executables_never_stand_in_for_a_missing_or_unlaunchable_host() {
    let fixture = Fixture::windows();
    type Files<'a> = Vec<(&'a str, &'a [u8], bool)>;
    let cases: Vec<(&str, Files)> = vec![
        (
            "host without launch permission",
            vec![
                (WINDOWS_HOST, HOST_V1, false),
                ("veles-bridge-cleanup.exe", HELPER, true),
                ("licenses/NOTICE", NOTICE_V1, false),
            ],
        ),
        (
            "only support executables",
            vec![
                ("veles-bridge-cleanup.exe", HELPER, true),
                ("maintenance/uninstall.exe", UNINSTALLER, true),
                ("licenses/NOTICE", NOTICE_V1, false),
            ],
        ),
    ];
    for (index, (label, files)) in cases.iter().enumerate() {
        let package = fixture.assembled_from(
            &format!("bad-{index}"),
            "1.0.0",
            files,
            policy(1, Vec::new(), Vec::new()),
        );
        let platform = FakePlatform::with_faults(Faults {
            trust: Some(package.proof.clone()),
            host_state: HostState::Quiescent,
            ..Faults::default()
        });
        let outcome = authenticate_package(&platform, &package.root, &package.descriptor).and_then(
            |authenticated| {
                run_operation(
                    &platform,
                    &fixture.paths,
                    Operation::Install,
                    Some(authenticated),
                )
            },
        );
        assert_eq!(
            failure_code(outcome),
            FailureCode::InvalidPackage,
            "{label}"
        );
    }
}

#[test]
fn recovery_of_an_interrupted_rollback_of_the_producers_payload_selects_the_host_not_a_helper() {
    // The sorted inventory lists the uninstaller and the cleanup helper BEFORE the host, so a recovery that
    // took "the first executable" would rebuild the wrong manifest and report recovery-required.
    let p1 = assembled_payload_id(HOST_V1);
    let build = |fixture: &Fixture| {
        let v1 = fixture.assembled("asm-v1", "1.0.0", HOST_V1, policy(1, Vec::new(), vec![p1]));
        let v2 = fixture.assembled("asm-v2", "2.0.0", HOST_V2, policy(2, Vec::new(), vec![p1]));
        fixture.install(&v1);
        fixture.install(&v2);
        (v1, v2)
    };
    let probe = Fixture::windows();
    let (probe_v1, _) = build(&probe);
    let platform = probe.platform_trusting(&probe_v1.proof);
    run_operation(
        &platform,
        &probe.paths,
        Operation::Rollback,
        Some(authenticate(&probe_v1)),
    )
    .expect("the probe rollback completes");
    let total = platform.mutations().len();

    for attempt in 1..=total {
        let fixture = Fixture::windows();
        let (v1, _v2) = build(&fixture);
        let context = format!("producer-payload rollback interrupted at {attempt}/{total}");
        let crashing = FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            panic_at_mutation: Some(attempt),
            trust: Some(v1.proof.clone()),
            trust_by_carrier: fixture.proofs.borrow().clone(),
            ..Faults::default()
        });
        let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
            run_operation(
                &crashing,
                &fixture.paths,
                Operation::Rollback,
                Some(authenticate(&v1)),
            )
        }));
        assert!(outcome.is_err(), "{context}: the injected death interrupts");
        let recovering = fixture.platform_trusting(&v1.proof);
        let report = recover(&recovering, &fixture.paths)
            .unwrap_or_else(|failure| panic!("{context}: recovery failed: {failure:?}"));
        assert!(report.failure.is_none(), "{context}: {report:?}");
        let manifest = parse_manifest(
            &fs::read(fixture.paths.registration_path()).expect("live registration"),
        )
        .expect("complete registration");
        let selected = manifest.executable().expect("absolute executable");
        assert_eq!(
            selected.file_name().and_then(|name| name.to_str()),
            Some(WINDOWS_HOST),
            "{context}: the host, never a support executable, is what Chrome would launch"
        );
    }
}

// -- F5: an ordinary interrupted activation is settled by the next maintenance command ---------------
//
// `recover()` existed only as a library/test API; install, repair and rollback refused a pending journal, so
// "Retry to recover" was false guidance. These tests drive the SHIPPING dispatch - `cli::run`, the entry point
// of every frontend - through a process death and a restart.

fn cli_install(
    platform: &FakePlatform,
    fixture: &Fixture,
    package: &Package,
) -> (i32, InstallReport) {
    use veles_bridge_installer::cli::{self, Context};
    let context = Context {
        platform,
        paths: &fixture.paths,
        expected: Some(&package.descriptor),
        quiescence: Some(SHORT_WAIT),
    };
    let arguments = vec![
        std::ffi::OsString::from("install"),
        std::ffi::OsString::from("--package"),
        package.root.clone().into_os_string(),
    ];
    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    let exit = cli::run(&arguments, &context, &mut stdout, &mut stderr);
    let report: InstallReport = serde_json::from_slice(&stdout).expect("one strict report");
    (exit, report)
}

type InstallReport = veles_bridge_installer::InstallReport;

#[test]
fn a_cli_restart_after_an_interrupted_upgrade_settles_it_and_completes_the_retry() {
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let build = |fixture: &Fixture| {
        let v1 = fixture.package(
            "v1",
            "1.0.0",
            HOST_V1,
            NOTICE_V1,
            policy(1, Vec::new(), vec![p1]),
        );
        let v2 = fixture.package(
            "v2",
            "2.0.0",
            HOST_V2,
            NOTICE_V2,
            policy(2, Vec::new(), vec![p1]),
        );
        fixture.install(&v1);
        (v1, v2)
    };
    let probe = Fixture::windows();
    let (_, probe_v2) = build(&probe);
    let platform = probe.platform_trusting(&probe_v2.proof);
    let (exit, report) = cli_install(&platform, &probe, &probe_v2);
    assert_eq!(
        (exit, report.state.as_str()),
        (0, veles_bridge_installer::STATE_INSTALLED),
        "{report:?}"
    );
    let total = platform.mutations().len();
    assert!(
        total >= 4,
        "an upgrade crosses several durable boundaries: {total}"
    );

    let p2 = payload_id(HOST_V2, NOTICE_V2);
    let mut interrupted_states = std::collections::BTreeSet::new();
    for attempt in 1..=total {
        let fixture = Fixture::windows();
        let (_v1, v2) = build(&fixture);
        let context = format!("upgrade interrupted at durable attempt {attempt}/{total}");
        let crashing = FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            panic_at_mutation: Some(attempt),
            trust: Some(v2.proof.clone()),
            trust_by_carrier: fixture.proofs.borrow().clone(),
            ..Faults::default()
        });
        let outcome =
            panic::catch_unwind(AssertUnwindSafe(|| cli_install(&crashing, &fixture, &v2)));
        assert!(
            outcome.is_err(),
            "{context}: the injected death interrupts the CLI"
        );

        // A fresh process, the very same command: the user's "Retry".
        let restarted = fixture.platform_trusting(&v2.proof);
        let (exit, report) = cli_install(&restarted, &fixture, &v2);
        assert_eq!(
            exit, 0,
            "{context}: the retry must complete, not stay blocked: {report:?}"
        );
        assert!(report.failure.is_none(), "{context}: {report:?}");
        assert_eq!(
            report.active_payload,
            Some(p2),
            "{context}: the requested release is active"
        );
        interrupted_states.insert(report.state.clone());
        assert!(
            matches!(journal_state(&fixture.paths), JournalState::None),
            "{context}: the retry leaves no pending journal"
        );
        let manifest =
            parse_manifest(&fs::read(fixture.paths.registration_path()).expect("registration"))
                .expect("complete registration");
        assert_eq!(
            fs::read(manifest.executable().expect("executable")).expect("host"),
            HOST_V2,
            "{context}: the live host is the requested release"
        );
        assert_eq!(
            receipt(&fixture.paths).expect("receipt").payload_id,
            p2,
            "{context}"
        );
    }
    assert!(!interrupted_states.is_empty());
}

#[test]
fn recovery_preflight_never_hides_a_foreign_registration() {
    // Recovery refuses a registration another product owns, and the maintenance command stays refused with
    // nothing touched - the preflight settles only what is provably this installer's own.
    let p1 = payload_id(HOST_V1, NOTICE_V1);
    let build = |fixture: &Fixture| {
        let v1 = fixture.package(
            "v1",
            "1.0.0",
            HOST_V1,
            NOTICE_V1,
            policy(1, Vec::new(), vec![p1]),
        );
        let v2 = fixture.package(
            "v2",
            "2.0.0",
            HOST_V2,
            NOTICE_V2,
            policy(2, Vec::new(), vec![p1]),
        );
        fixture.install(&v1);
        (v1, v2)
    };
    let probe = Fixture::windows();
    let (_, probe_v2) = build(&probe);
    let platform = probe.platform_trusting(&probe_v2.proof);
    cli_install(&platform, &probe, &probe_v2);
    let total = platform.mutations().len();

    let fixture = Fixture::windows();
    let (_v1, v2) = build(&fixture);
    let crashing = FakePlatform::with_faults(Faults {
        host_state: HostState::Quiescent,
        panic_at_mutation: Some(total.min(3)),
        trust: Some(v2.proof.clone()),
        trust_by_carrier: fixture.proofs.borrow().clone(),
        ..Faults::default()
    });
    assert!(
        panic::catch_unwind(AssertUnwindSafe(|| cli_install(&crashing, &fixture, &v2))).is_err()
    );
    assert!(matches!(
        journal_state(&fixture.paths),
        JournalState::Pending(_)
    ));
    fs::write(
        fixture.paths.registration_path(),
        b"{\"name\":\"someone else's manifest\"}\n",
    )
    .expect("a foreign product takes the registration path");
    let before = snapshot(fixture.temp.path());
    let restarted = fixture.platform_trusting(&v2.proof);
    let (exit, report) = cli_install(&restarted, &fixture, &v2);
    assert_eq!(exit, 1, "{report:?}");
    assert_eq!(
        report.failure,
        Some(FailureCode::ForeignState),
        "{report:?}"
    );
    assert_eq!(
        snapshot(fixture.temp.path()),
        before,
        "the refusal touches nothing"
    );
}

#[test]
fn an_interrupted_removal_is_finished_only_by_uninstall_never_by_the_recovery_preflight() {
    let fixture = Fixture::windows();
    let v1 = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let v2 = fixture.package(
        "v2",
        "2.0.0",
        HOST_V2,
        NOTICE_V2,
        policy(2, Vec::new(), Vec::new()),
    );
    fixture.install(&v1);
    // Die in the middle of an uninstall, after it journalled its removal.
    let probe = Fixture::windows();
    let probe_v1 = probe.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    probe.install(&probe_v1);
    let counting = probe.platform();
    run_operation(&counting, &probe.paths, Operation::Uninstall, None).expect("probe uninstall");
    let total = counting.mutations().len();
    assert!(total >= 3, "{total}");
    let crashing = FakePlatform::with_faults(Faults {
        host_state: HostState::Quiescent,
        panic_at_mutation: Some(2),
        trust_by_carrier: fixture.proofs.borrow().clone(),
        ..Faults::default()
    });
    assert!(
        panic::catch_unwind(AssertUnwindSafe(|| run_operation(
            &crashing,
            &fixture.paths,
            Operation::Uninstall,
            None
        )))
        .is_err()
    );
    assert!(matches!(
        journal_state(&fixture.paths),
        JournalState::Pending(_)
    ));

    let before = snapshot(fixture.temp.path());
    let platform = fixture.platform_trusting(&v2.proof);
    let (exit, report) = cli_install(&platform, &fixture, &v2);
    assert_eq!(exit, 1, "{report:?}");
    assert_eq!(
        report.failure,
        Some(FailureCode::RecoveryRequired),
        "{report:?}"
    );
    assert_eq!(
        snapshot(fixture.temp.path()),
        before,
        "an install never resumes a removal"
    );

    // The user's way forward is uninstall itself, which owns resumable removal.
    let platform = fixture.platform();
    let report = run_operation(&platform, &fixture.paths, Operation::Uninstall, None)
        .expect("uninstall resumes");
    assert_eq!(report.state, STATE_UNINSTALLED);
}

// ---------------------------------------------------------------------------
// Persistent maintenance publication (OTP-25 F6)
// ---------------------------------------------------------------------------

/// The uninstaller fixture the Windows package ships below `maintenance/`.
const UNINSTALLER_V1: &[u8] = b"the signed uninstaller of release one";
const UNINSTALLER_V2: &[u8] = b"the signed uninstaller of release two";

fn maintenance_files(uninstaller: &'static [u8]) -> Vec<(&'static str, &'static [u8], bool)> {
    vec![
        ("host.exe", HOST_V1, true),
        ("licenses/NOTICE", NOTICE_V1, false),
        ("maintenance/uninstall.exe", uninstaller, true),
    ]
}

fn claimed(receipt: &Receipt, relative: &str) -> Option<Digest> {
    receipt
        .ownership
        .owned
        .iter()
        .find(|claim| {
            claim.kind == veles_bridge_installer::OwnedKind::File && claim.path == relative
        })
        .and_then(|claim| claim.sha256)
}

/// The installed tree advertises the persistent maintenance paths the frontends
/// invoke: the engine itself and the signed uninstaller. They are published (not
/// only buried inside `versions/`), verified and owned by the receipt.
#[test]
fn install_publishes_the_maintenance_engine_and_uninstaller_to_the_advertised_paths() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let package = fixture.package_from(
        "v1",
        "1.0.0",
        &maintenance_files(UNINSTALLER_V1),
        policy(1, Vec::new(), Vec::new()),
    );
    let payload = package.descriptor.payload_id;
    fixture.install(&package);

    let engine_relative = "maintenance/veles-bridge-maintenance.exe";
    let uninstaller_relative = "maintenance/uninstall.exe";
    let engine =
        fs::read(paths.root().join(engine_relative)).expect("the persistent engine exists");
    assert_eq!(
        engine,
        package.proof.carrier(),
        "the engine is the authenticated carrier image, byte for byte"
    );
    assert_eq!(
        fs::read(paths.root().join(uninstaller_relative)).expect("the persistent uninstaller"),
        UNINSTALLER_V1
    );
    let committed = receipt(paths).expect("the receipt is committed");
    for (relative, bytes) in [
        (engine_relative, package.proof.carrier()),
        (uninstaller_relative, UNINSTALLER_V1),
    ] {
        assert_eq!(
            claimed(&committed, relative),
            Some(Digest::of(bytes)),
            "{relative} is a verified owned claim"
        );
    }
    // The retained recovery asset is still inside the immutable version too.
    let installed = version_dir(paths, "1.0.0", payload);
    assert_eq!(
        fs::read(installed.join(uninstaller_relative)).expect("the version copy"),
        UNINSTALLER_V1
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        for relative in [engine_relative, uninstaller_relative] {
            assert_eq!(
                fs::metadata(paths.root().join(relative))
                    .expect("published file")
                    .permissions()
                    .mode()
                    & 0o777,
                0o700,
                "{relative} keeps its owner-only launch permission"
            );
        }
    }
}

/// macOS publishes the engine from its authenticated carrier bundle at the one
/// path the Swift frontend resolves.
#[test]
fn macos_publishes_the_maintenance_engine_from_the_carrier_bundle() {
    let fixture = Fixture::macos();
    let paths = &fixture.paths;
    let package = fixture.package(
        "v1",
        "1.0.0",
        HOST_V1,
        NOTICE_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&package);
    assert_eq!(
        fs::read(paths.root().join("maintenance/veles-bridge-maintenance"))
            .expect("the persistent engine exists"),
        b"macOS maintenance engine"
    );
    let committed = receipt(paths).expect("the receipt is committed");
    assert_eq!(
        claimed(&committed, "maintenance/veles-bridge-maintenance"),
        Some(Digest::of(b"macOS maintenance engine"))
    );
}

/// A file this installer does not own at a persistent path is never replaced:
/// the owned-preimage rule applies to maintenance publication exactly as it does
/// to the registration.
#[test]
fn a_foreign_file_at_a_persistent_maintenance_path_blocks_the_install() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    fs::create_dir_all(paths.maintenance()).expect("private maintenance directory");
    let foreign_path = paths.root().join("maintenance/uninstall.exe");
    fs::write(&foreign_path, b"another product's file").expect("foreign file");
    let package = fixture.package_from(
        "v1",
        "1.0.0",
        &maintenance_files(UNINSTALLER_V1),
        policy(1, Vec::new(), Vec::new()),
    );
    let payload = package.descriptor.payload_id;
    let before = snapshot(fixture.temp.path());
    let platform = fixture.platform();
    let failure = run_operation(
        &platform,
        paths,
        Operation::Install,
        Some(authenticate(&package)),
    )
    .expect_err("a foreign persistent path is not replaced");
    assert_eq!(failure.code, FailureCode::ForeignState);
    assert_eq!(
        fs::read(&foreign_path).expect("the foreign file is preserved"),
        b"another product's file"
    );
    assert!(
        !version_dir(paths, "1.0.0", payload).exists(),
        "nothing is staged when the owned preimage is not established"
    );
    assert!(
        receipt(paths).is_none(),
        "no receipt is committed for a refused install"
    );
    assert!(!paths.registration_path().exists());
    let _ = before;
}

/// An upgrade replaces the persistent assets; a failed activation afterwards
/// restores the previous release's exact bytes, never leaving the newer engine
/// advertised next to an older installation.
#[test]
fn an_upgrade_replaces_the_persistent_assets_and_a_failed_activation_restores_them() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package_from(
        "v1",
        "1.0.0",
        &maintenance_files(UNINSTALLER_V1),
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&v1);
    let v2 = fixture.package_from(
        "v2",
        "2.0.0",
        &maintenance_files(UNINSTALLER_V2),
        policy(1, Vec::new(), Vec::new()),
    );

    // The OS denies the installed host after publication and before activation.
    let platform = fixture.platform_with(Faults {
        host_state: HostState::Quiescent,
        host: Some(veles_bridge_installer::HostFault::Denied),
        ..Faults::default()
    });
    let failure = run_operation(
        &platform,
        paths,
        Operation::Install,
        Some(authenticate(&v2)),
    )
    .expect_err("a denied host assessment fails the activation");
    assert_eq!(failure.code, FailureCode::TrustDenied);
    assert_eq!(
        fs::read(paths.root().join("maintenance/uninstall.exe")).expect("restored uninstaller"),
        UNINSTALLER_V1,
        "the failed upgrade restored the previous persistent uninstaller"
    );
    assert_eq!(
        receipt(paths)
            .expect("the previous receipt survives")
            .version,
        "1.0.0"
    );

    // The honest upgrade publishes the newer assets and claims them.
    fixture.install(&v2);
    assert_eq!(
        fs::read(paths.root().join("maintenance/uninstall.exe")).expect("upgraded uninstaller"),
        UNINSTALLER_V2
    );
    let committed = receipt(paths).expect("the receipt is committed");
    assert_eq!(committed.version, "2.0.0");
    assert_eq!(
        claimed(&committed, "maintenance/uninstall.exe"),
        Some(Digest::of(UNINSTALLER_V2))
    );
}

/// Repair restores a damaged persistent maintenance file from the same-version
/// authenticated package, and inspect tells the truth in between.
#[test]
fn repair_restores_a_damaged_persistent_uninstaller() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let package = fixture.package_from(
        "v1",
        "1.0.0",
        &maintenance_files(UNINSTALLER_V1),
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&package);
    let persistent = paths.root().join("maintenance/uninstall.exe");
    fs::write(&persistent, b"damaged by a partial overwrite").expect("damaged persistent file");
    let inspected = run_operation(&fixture.platform(), paths, Operation::Inspect, None)
        .expect("inspect reports the damage");
    assert_eq!(inspected.state, STATE_DAMAGED);

    let repaired = run_operation(
        &fixture.platform(),
        paths,
        Operation::Repair,
        Some(authenticate(&package)),
    )
    .expect("the same-version package repairs the persistent asset");
    assert_eq!(repaired.state, STATE_REPAIRED);
    assert_eq!(
        fs::read(&persistent).expect("restored bytes"),
        UNINSTALLER_V1
    );
}

/// After a rollback the persistent engine is the newer release's (it is never
/// downgraded); a later repair still restores that claimed identity from the
/// retained copy rather than refusing the whole installation.
#[test]
fn repair_restores_a_damaged_persistent_asset_of_a_retained_newer_release() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    // The payload identity is fixed by the bytes, so the provisional build names
    // the rollback-eligible identity the real release's policy must carry.
    let provisional = fixture.package_from(
        "v1-provisional",
        "1.0.0",
        &maintenance_files(UNINSTALLER_V1),
        policy(1, Vec::new(), Vec::new()),
    );
    let p1 = provisional.descriptor.payload_id;
    let v1 = fixture.package_from(
        "v1",
        "1.0.0",
        &maintenance_files(UNINSTALLER_V1),
        policy(1, Vec::new(), vec![p1]),
    );
    assert_eq!(v1.descriptor.payload_id, p1);
    fixture.install(&v1);
    let v2 = fixture.package_from(
        "v2",
        "2.0.0",
        &maintenance_files(UNINSTALLER_V2),
        policy(1, Vec::new(), vec![p1]),
    );
    fixture.install(&v2);
    let rolled = run_operation(&fixture.platform(), paths, Operation::Rollback, None)
        .expect("the retained release rolls back");
    assert_eq!(rolled.state, STATE_ROLLED_BACK);
    assert_eq!(
        fs::read(paths.root().join("maintenance/uninstall.exe")).expect("the newer uninstaller"),
        UNINSTALLER_V2,
        "rollback does not downgrade the persistent maintenance assets"
    );

    let persistent = paths.root().join("maintenance/uninstall.exe");
    fs::write(&persistent, b"damaged after the rollback").expect("damaged persistent file");
    let repaired = run_operation(
        &fixture.platform(),
        paths,
        Operation::Repair,
        Some(authenticate(&v1)),
    )
    .expect("the installed release's package repairs its persistent asset");
    assert_eq!(repaired.state, STATE_REPAIRED);
    assert_eq!(
        fs::read(&persistent).expect("restored bytes"),
        UNINSTALLER_V2,
        "the retained newer copy supplies the claimed identity"
    );
}

// ---------------------------------------------------------------------------
// Maintenance recovery ownership/authentication (N2)
// ---------------------------------------------------------------------------

/// A pending upgrade journal as a crash after maintenance publication but before
/// activation would leave it: the committed receipt still names the previous
/// release, the journal names the new one, and the persistent files hold the new
/// publication until recovery restores their verified preimages.
fn maintenance_crash_journal(
    paths: &InstallPaths,
    prior: &Receipt,
    v2_payload: Digest,
    maintenance: Vec<MaintenancePublication>,
) -> Journal {
    let version_relative = paths.version_dir_relative(
        &Version::parse("2.0.0").expect("fixture version"),
        v2_payload,
    );
    let new_manifest = ManifestSnapshot::new(manifest_selecting(
        paths,
        &paths.root().join(&version_relative).join("host.exe"),
    ))
    .expect("bounded new manifest");
    let live = fs::read(paths.registration_path()).expect("the previous registration is live");
    Journal {
        schema: JOURNAL_SCHEMA_VERSION,
        transaction_id: "fedcba9876543210fedcba9876543210".to_owned(),
        operation: Operation::Install,
        phase: Phase::Staged,
        target: paths.target(),
        channel: paths.channel(),
        root: paths
            .root()
            .to_str()
            .expect("UTF-8 install root")
            .to_owned(),
        version: "2.0.0".to_owned(),
        payload_id: v2_payload,
        old_version: Some(prior.version.clone()),
        old_payload_id: Some(prior.payload_id),
        old_carrier_sha256: Some(prior.policy.carrier_sha256),
        manifest: new_manifest,
        old_manifest: Some(ManifestSnapshot::new(live.clone()).expect("bounded previous manifest")),
        preimage: Some(veles_bridge_installer::RegistrationPreimage::new(
            Digest::of(&live),
        )),
        policy: prior.policy.clone(),
        release_policy: prior.release_policy.clone(),
        created: Vec::new(),
        maintenance,
    }
}

/// The state-mutation ordinal whose pre-call crash leaves the two persistent
/// files published but the previous registration live: the crash window between
/// `publish_maintenance` and the first manifest publication.
fn maintenance_publication_crash_window() -> usize {
    let probe = Fixture::windows();
    let probe_v1 = probe.package_from(
        "v1",
        "1.0.0",
        &maintenance_files(UNINSTALLER_V1),
        policy(1, Vec::new(), Vec::new()),
    );
    let probe_v2 = probe.package_from(
        "v2",
        "2.0.0",
        &maintenance_files(UNINSTALLER_V2),
        policy(1, Vec::new(), Vec::new()),
    );
    probe.install(&probe_v1);
    let clean = probe.platform();
    run_operation(
        &clean,
        &probe.paths,
        Operation::Install,
        Some(authenticate(&probe_v2)),
    )
    .expect("the probe upgrade completes");
    let total = clean.mutations().len();

    let uninstaller_relative = "maintenance/uninstall.exe";
    for attempt in 1..=total {
        let fixture = Fixture::windows();
        let paths = &fixture.paths;
        let v1 = fixture.package_from(
            "v1",
            "1.0.0",
            &maintenance_files(UNINSTALLER_V1),
            policy(1, Vec::new(), Vec::new()),
        );
        let v2 = fixture.package_from(
            "v2",
            "2.0.0",
            &maintenance_files(UNINSTALLER_V2),
            policy(1, Vec::new(), Vec::new()),
        );
        fixture.install(&v1);
        let trust = fixture.proofs.borrow().clone();
        let crashing = FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            panic_at_mutation: Some(attempt),
            trust_by_carrier: trust,
            ..Faults::default()
        });
        let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
            run_operation(
                &crashing,
                paths,
                Operation::Install,
                Some(authenticate(&v2)),
            )
        }));
        assert!(outcome.is_err(), "attempt {attempt} did not die");
        let pending = match journal_state(paths) {
            JournalState::Pending(journal) => *journal,
            _ => continue,
        };
        let published = fs::read(paths.root().join(uninstaller_relative))
            .is_ok_and(|bytes| bytes == UNINSTALLER_V2);
        let live_is_old = fs::read(paths.registration_path())
            .is_ok_and(|bytes| Digest::of(&bytes) != pending.manifest.sha256());
        if published
            && live_is_old
            && receipt(paths).is_some_and(|receipt| receipt.version == "1.0.0")
        {
            return attempt;
        }
    }
    panic!("a crash window after maintenance publication exists");
}

/// Rebuild the crash window on a fresh fixture and return the durable journal.
fn crash_after_maintenance_publication(
    fixture: &Fixture,
    v1: &Package,
    v2: &Package,
    attempt: usize,
) -> Journal {
    let paths = &fixture.paths;
    fixture.install(v1);
    let trust = fixture.proofs.borrow().clone();
    let crashing = FakePlatform::with_faults(Faults {
        host_state: HostState::Quiescent,
        panic_at_mutation: Some(attempt),
        trust_by_carrier: trust,
        ..Faults::default()
    });
    let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
        run_operation(&crashing, paths, Operation::Install, Some(authenticate(v2)))
    }));
    assert!(outcome.is_err(), "the selected crash window dies");
    match journal_state(paths) {
        JournalState::Pending(journal) => *journal,
        other => panic!("expected a pending journal: {other:?}"),
    }
}

/// A crash-time recovery must not overwrite a regular maintenance destination that
/// holds neither this transaction's authenticated published bytes nor the verified
/// preimage: a different file put there while the installer was stopped is foreign
/// state. Every destination is validated before the first write, so an earlier
/// valid one is not restored either.
#[test]
fn recovery_preserves_a_replaced_maintenance_destination_unchanged() {
    let attempt = maintenance_publication_crash_window();
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package_from(
        "v1",
        "1.0.0",
        &maintenance_files(UNINSTALLER_V1),
        policy(1, Vec::new(), Vec::new()),
    );
    let v2 = fixture.package_from(
        "v2",
        "2.0.0",
        &maintenance_files(UNINSTALLER_V2),
        policy(1, Vec::new(), Vec::new()),
    );
    let _journal = crash_after_maintenance_publication(&fixture, &v1, &v2, attempt);

    // The crash happened after publication: the persistent files hold the new
    // identity. Then a different regular file was put at the uninstaller path.
    let engine_relative = "maintenance/veles-bridge-maintenance.exe";
    let uninstaller_relative = "maintenance/uninstall.exe";
    assert_eq!(
        fs::read(paths.root().join(engine_relative)).expect("published engine"),
        v2.proof.carrier(),
        "the crash window is after the engine publication"
    );
    let foreign = b"not this installer's uninstaller";
    fs::write(paths.root().join(uninstaller_relative), foreign).expect("foreign replacement");

    let before = snapshot(fixture.temp.path());
    let failure = failure_code(recover(&fixture.platform(), paths));
    assert_eq!(
        failure,
        FailureCode::ForeignState,
        "a regular file that is neither the new nor the old identity is foreign state"
    );
    assert_eq!(
        fs::read(paths.root().join(uninstaller_relative)).expect("the foreign file remains"),
        foreign,
        "the foreign replacement is preserved byte for byte"
    );
    assert_eq!(
        fs::read(paths.root().join(engine_relative)).expect("the valid destination remains"),
        v2.proof.carrier(),
        "no destination is written before every destination has been validated"
    );
    assert_eq!(
        snapshot(fixture.temp.path()),
        before,
        "refusing a foreign destination mutates nothing"
    );
    assert_eq!(
        receipt(paths)
            .expect("the previous receipt survives")
            .version,
        "1.0.0"
    );
    assert!(
        matches!(journal_state(paths), JournalState::Pending(_)),
        "the durable intent is retained for an explicit resolution"
    );
}

/// A journal's description of the "new" publication is editable state, not
/// evidence. A crash after maintenance publication leaves the new bytes live; an
/// attacker then replaces one destination and rewrites the journal's sha/size to
/// describe that foreign file. Recovery must classify the destination from the
/// authenticated carrier/inventory identities, not from the journal, and must
/// preserve the foreign file. This is the verifier's adapted foreign-replacement
/// counterexample with the genuine staged release on disk.
#[test]
fn recovery_does_not_trust_a_journal_described_publication_identity() {
    let attempt = maintenance_publication_crash_window();
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package_from(
        "v1",
        "1.0.0",
        &maintenance_files(UNINSTALLER_V1),
        policy(1, Vec::new(), Vec::new()),
    );
    let v2 = fixture.package_from(
        "v2",
        "2.0.0",
        &maintenance_files(UNINSTALLER_V2),
        policy(1, Vec::new(), Vec::new()),
    );
    let mut journal = crash_after_maintenance_publication(&fixture, &v1, &v2, attempt);

    let engine_relative = "maintenance/veles-bridge-maintenance.exe";
    let uninstaller_relative = "maintenance/uninstall.exe";
    assert_eq!(
        fs::read(paths.root().join(engine_relative)).expect("the published engine"),
        v2.proof.carrier(),
        "the crash is after the engine publication"
    );
    let foreign = b"not this installer's uninstaller";
    fs::write(paths.root().join(uninstaller_relative), foreign).expect("foreign replacement");
    for publication in &mut journal.maintenance {
        if publication.path == uninstaller_relative {
            publication.sha256 = Digest::of(foreign);
            publication.size = foreign.len() as u64;
        }
    }
    journal
        .write(&PortablePlatform::new(), paths)
        .expect("the forged journal is durable");

    let before = snapshot(fixture.temp.path());
    let failure = failure_code(recover(&fixture.platform(), paths));
    assert_eq!(
        failure,
        FailureCode::RecoveryRequired,
        "a journal that disagrees with the authenticated carrier/inventory cannot authorize restoration"
    );
    assert_eq!(
        fs::read(paths.root().join(uninstaller_relative)).expect("the foreign file remains"),
        foreign,
        "a journal-described identity never authorizes overwriting the file it describes"
    );
    assert_eq!(
        fs::read(paths.root().join(engine_relative)).expect("the published engine remains"),
        v2.proof.carrier(),
        "no destination is restored before every destination is authenticated"
    );
    assert_eq!(
        snapshot(fixture.temp.path()),
        before,
        "refusing a foreign destination mutates nothing"
    );
    assert_eq!(
        receipt(paths)
            .expect("the previous receipt survives")
            .version,
        "1.0.0"
    );
    assert!(
        matches!(journal_state(paths), JournalState::Pending(_)),
        "the durable intent is retained for an explicit resolution"
    );
}

/// The preimage a recovery restores is never authorized by editable journal data
/// alone: it must be the retained copy of the identity the committed previous
/// receipt claims. A forged preimage/source is refused before any write.
#[test]
fn recovery_refuses_a_forged_maintenance_preimage_without_writing() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package_from(
        "v1",
        "1.0.0",
        &maintenance_files(UNINSTALLER_V1),
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&v1);
    let prior = receipt(paths).expect("the previous receipt is committed");
    let v2 = fixture.package_from(
        "v2",
        "2.0.0",
        &maintenance_files(UNINSTALLER_V2),
        policy(1, Vec::new(), Vec::new()),
    );
    let p1 = v1.descriptor.payload_id;
    let p2 = v2.descriptor.payload_id;
    let uninstaller_relative = "maintenance/uninstall.exe";
    let v1_dir = format!("versions/1.0.0-{}", p1.to_hex());
    let v2_dir = format!("versions/2.0.0-{}", p2.to_hex());

    // A regular file inside the retained version that the journal names as the
    // preimage, with a matching digest. Nothing binds it to the prior receipt.
    let forged = b"attacker-supplied preimage";
    fs::write(paths.root().join(&v1_dir).join("evil.bin"), forged).expect("forged preimage source");

    let journal = maintenance_crash_journal(
        paths,
        &prior,
        p2,
        vec![MaintenancePublication {
            path: uninstaller_relative.to_owned(),
            source: format!("{v2_dir}/{uninstaller_relative}"),
            sha256: Digest::of(UNINSTALLER_V2),
            size: UNINSTALLER_V2.len() as u64,
            executable: true,
            preimage: Some(MaintenancePreimage {
                source: format!("{v1_dir}/evil.bin"),
                sha256: Digest::of(forged),
                size: forged.len() as u64,
                executable: true,
            }),
        }],
    );
    fs::write(paths.root().join(uninstaller_relative), UNINSTALLER_V2)
        .expect("published uninstaller");
    journal
        .write(&PortablePlatform::new(), paths)
        .expect("the crash journal is durable");

    let failure = failure_code(recover(&fixture.platform(), paths));
    assert_eq!(
        failure,
        FailureCode::RecoveryRequired,
        "a preimage the previous receipt does not claim is not recovery authority"
    );
    assert_eq!(
        fs::read(paths.root().join(uninstaller_relative)).expect("the published file remains"),
        UNINSTALLER_V2,
        "a forged preimage causes no write"
    );
}

/// Forward completion accepts maintenance publications only when they are the
/// authenticated carrier/inventory identities. A journal whose published bytes
/// and journal fields agree with each other is not evidence: once the live
/// registration is the new one, a forged persistent file must stop completion.
#[test]
fn completion_refuses_a_forged_maintenance_publication() {
    let probe = Fixture::windows();
    let probe_v1 = probe.package_from(
        "v1",
        "1.0.0",
        &maintenance_files(UNINSTALLER_V1),
        policy(1, Vec::new(), Vec::new()),
    );
    let probe_v2 = probe.package_from(
        "v2",
        "2.0.0",
        &maintenance_files(UNINSTALLER_V2),
        policy(1, Vec::new(), Vec::new()),
    );
    probe.install(&probe_v1);
    let clean = probe.platform();
    run_operation(
        &clean,
        &probe.paths,
        Operation::Install,
        Some(authenticate(&probe_v2)),
    )
    .expect("the probe upgrade completes");
    let total = clean.mutations().len();

    // Find the crash window after the new registration is live but before the
    // receipt commits: that is the state recovery finishes forward.
    let mut chosen = None;
    for attempt in 1..=total {
        let fixture = Fixture::windows();
        let paths = &fixture.paths;
        let v1 = fixture.package_from(
            "v1",
            "1.0.0",
            &maintenance_files(UNINSTALLER_V1),
            policy(1, Vec::new(), Vec::new()),
        );
        let v2 = fixture.package_from(
            "v2",
            "2.0.0",
            &maintenance_files(UNINSTALLER_V2),
            policy(1, Vec::new(), Vec::new()),
        );
        fixture.install(&v1);
        let crashing = FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            panic_at_mutation: Some(attempt),
            trust_by_carrier: fixture.proofs.borrow().clone(),
            ..Faults::default()
        });
        let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
            run_operation(
                &crashing,
                paths,
                Operation::Install,
                Some(authenticate(&v2)),
            )
        }));
        assert!(outcome.is_err(), "attempt {attempt} did not die");
        let pending = match journal_state(paths) {
            JournalState::Pending(journal) => *journal,
            _ => continue,
        };
        let receipt_is_previous = receipt(paths)
            .map(|receipt| receipt.version == "1.0.0")
            .unwrap_or(true);
        let live_is_new = fs::read(paths.registration_path())
            .map(|bytes| Digest::of(&bytes) == pending.manifest.sha256())
            .unwrap_or(false);
        if receipt_is_previous && live_is_new {
            chosen = Some(attempt);
            break;
        }
    }
    let attempt = chosen.expect("a crash window before the receipt commit exists");

    // Rebuild that exact window and forge the persistent uninstaller plus the
    // journal fields that describe it.
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let v1 = fixture.package_from(
        "v1",
        "1.0.0",
        &maintenance_files(UNINSTALLER_V1),
        policy(1, Vec::new(), Vec::new()),
    );
    let v2 = fixture.package_from(
        "v2",
        "2.0.0",
        &maintenance_files(UNINSTALLER_V2),
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&v1);
    let crashing = FakePlatform::with_faults(Faults {
        host_state: HostState::Quiescent,
        panic_at_mutation: Some(attempt),
        trust_by_carrier: fixture.proofs.borrow().clone(),
        ..Faults::default()
    });
    let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
        run_operation(
            &crashing,
            paths,
            Operation::Install,
            Some(authenticate(&v2)),
        )
    }));
    assert!(outcome.is_err());

    let mut journal = match journal_state(paths) {
        JournalState::Pending(journal) => *journal,
        other => panic!("expected a pending journal: {other:?}"),
    };
    let forged = b"forged persistent engine";
    let uninstaller_relative = "maintenance/uninstall.exe";
    fs::write(paths.root().join(uninstaller_relative), forged).expect("forged publication");
    for publication in &mut journal.maintenance {
        if publication.path == uninstaller_relative {
            publication.sha256 = Digest::of(forged);
            publication.size = forged.len() as u64;
        }
    }
    journal
        .write(&PortablePlatform::new(), paths)
        .expect("the forged journal is durable");

    let failure = failure_code(recover(&fixture.platform(), paths));
    assert_eq!(
        failure,
        FailureCode::RecoveryRequired,
        "a publication that is not the authenticated carrier/inventory identity cannot complete"
    );
    assert_eq!(
        fs::read(paths.root().join(uninstaller_relative)).expect("the forged file remains"),
        forged,
        "refusing completion writes nothing"
    );
    assert_eq!(
        receipt(paths)
            .expect("the previous receipt survives")
            .version,
        "1.0.0"
    );
}

/// Outside-root staging acquires cleanup ownership before the first copy: a
/// failed copy (here an oversized helper image refused by the size bound) leaves
/// no directory or partial launcher behind (N4).
#[test]
fn a_failed_outside_root_stage_leaves_nothing_behind() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let oversized = fixture.temp.path().join("oversized-helper");
    let file = fs::File::create(&oversized).expect("oversized helper");
    file.set_len(MAX_INVENTORY_BYTES + 1)
        .expect("sparse helper");
    let request = RemovalRequest {
        helper_source: &oversized,
        helper_sha256: Digest::of(b""),
        publisher: PUBLISHER,
        transaction: "0123456789abcdef0123456789abcdef",
    };
    let failure = RemovalCoordinator::stage(paths, &request)
        .expect_err("an oversized helper image is refused");
    assert_eq!(failure.code, FailureCode::CleanupPending);
    assert!(
        !cleanup_staging_directory(paths, request.transaction).exists(),
        "the launcher copied before the failure is removed with its directory"
    );
}

/// A crash after staging leaves a directory with this transaction's marker; a
/// retry reconciles that residue and stages afresh. It never reuses it as-is and
/// never deletes a directory it cannot identify.
#[test]
fn staging_reconciles_its_own_crash_residue() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let helper = fixture.temp.path().join("helper-image");
    let helper_bytes = b"the signed cleanup helper";
    fs::write(&helper, helper_bytes).expect("helper image");
    let request = RemovalRequest {
        helper_source: &helper,
        helper_sha256: Digest::of(helper_bytes),
        publisher: PUBLISHER,
        transaction: "0123456789abcdef0123456789abcdef",
    };
    let directory = cleanup_staging_directory(paths, request.transaction);
    let _ = fs::remove_dir_all(&directory);

    let staged = RemovalCoordinator::stage(paths, &request)
        .expect("the first stage succeeds")
        .expect("the helper image is present");
    std::mem::forget(staged); // the process died: no destructor ran
    assert!(directory.exists(), "the crash residue is on disk");

    let restaged = RemovalCoordinator::stage(paths, &request)
        .expect("a retry reconciles its own residue and stages afresh")
        .expect("the helper image is present");
    assert!(directory.exists(), "a fresh staging directory exists");
    restaged.discard_observed().expect("observed disposal");
    assert!(
        !directory.exists(),
        "the disposable staging artifact is gone"
    );
}

/// Build the durable boundary an interrupted uninstall leaves after the in-process
/// file pass: the registration and every owned leaf are gone (including the
/// installed cleanup helper), the receipt and a `CleanupPending` journal survive,
/// and the real coordinator's outside-root stage was abandoned with no destructor.
fn crash_uninstall_after_owned_file_pass(
    fixture: &Fixture,
    package: &Package,
) -> (String, PathBuf) {
    let paths = &fixture.paths;
    fixture.install(package);
    let committed = receipt(paths).expect("committed receipt");
    let transaction = committed.transaction_id.clone();
    let helper = committed
        .ownership
        .owned
        .iter()
        .find(|claim| {
            claim.kind == veles_bridge_installer::OwnedKind::File
                && claim.path.ends_with("/veles-bridge-cleanup.exe")
        })
        .expect("the release carries a cleanup helper claim");
    let helper_sha256 = helper.sha256.expect("helper digest");
    let helper_path = paths.root().join(&helper.path);
    let request = RemovalRequest {
        helper_source: &helper_path,
        helper_sha256,
        publisher: PUBLISHER,
        transaction: &transaction,
    };
    let staged = RemovalCoordinator::stage(paths, &request)
        .expect("the coordinator stages the helper pair")
        .expect("the helper image is present");
    std::mem::forget(staged); // the process died: no destructor ran

    let live = fs::read(paths.registration_path()).expect("the live registration");
    fs::remove_file(paths.registration_path()).expect("the pass removed the registration");
    for claim in &committed.ownership.owned {
        if claim.kind == veles_bridge_installer::OwnedKind::Directory {
            continue;
        }
        match fs::remove_file(paths.root().join(&claim.path)) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => panic!("remove {}: {error}", claim.path),
        }
    }

    let journal = Journal {
        schema: JOURNAL_SCHEMA_VERSION,
        transaction_id: transaction.clone(),
        operation: Operation::Uninstall,
        phase: Phase::CleanupPending,
        target: paths.target(),
        channel: paths.channel(),
        root: committed.root.clone(),
        version: committed.version.clone(),
        payload_id: committed.payload_id,
        old_version: None,
        old_payload_id: None,
        old_carrier_sha256: None,
        manifest: ManifestSnapshot::new(live).expect("bounded live manifest"),
        old_manifest: None,
        preimage: None,
        policy: committed.policy.clone(),
        release_policy: committed.release_policy.clone(),
        created: committed
            .ownership
            .owned
            .iter()
            .map(|claim| JournalClaim {
                path: claim.path.clone(),
                kind: claim.kind,
                sha256: claim.sha256,
                size: None,
                incomplete: false,
            })
            .collect(),
        maintenance: Vec::new(),
    };
    journal
        .write(&PortablePlatform::new(), paths)
        .expect("the pending uninstall journal is durable");
    (
        transaction.clone(),
        cleanup_staging_directory(paths, &transaction),
    )
}

/// Shipping retry after the installed helper has already been removed: the stage
/// identity is durable (transaction plus the claimed helper digest), so a retry
/// reconciles and observes the outside-root stage even though no helper file is
/// left to stage again. A surviving stage is never clean (N4).
#[test]
fn a_retry_after_the_installed_helper_is_gone_still_disposes_the_stage() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let package = fixture.assembled(
        "uninstall-retry",
        "1.0.0",
        HOST_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let (_transaction, directory) = crash_uninstall_after_owned_file_pass(&fixture, &package);
    assert!(directory.exists(), "the crashed stage is on disk");
    let helper = receipt(paths)
        .expect("receipt")
        .ownership
        .owned
        .iter()
        .find(|claim| claim.path.ends_with("/veles-bridge-cleanup.exe"))
        .expect("helper claim")
        .path
        .clone();
    assert!(
        !paths.root().join(&helper).exists(),
        "the installed helper file is gone before the retry"
    );

    let report = run_operation(&fixture.platform(), paths, Operation::Uninstall, None)
        .expect("the retry completes the removal");
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(
        report.warnings.is_empty(),
        "nothing of ours survives, so the retry is clean: {:?}",
        report.warnings
    );
    assert!(
        !directory.exists(),
        "the outside-root stage is reconciled and observed gone without a helper file"
    );
    assert!(!paths.root().exists(), "the installation root is removed");
}

/// A stage that still holds a foreign entry after the helper is gone is preserved
/// and reported: a retry may only delete its own marked artifact.
#[test]
fn a_retry_without_the_helper_preserves_a_foreign_entry_in_its_stage() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let package = fixture.assembled(
        "uninstall-retry-foreign",
        "1.0.0",
        HOST_V1,
        policy(1, Vec::new(), Vec::new()),
    );
    let (_transaction, directory) = crash_uninstall_after_owned_file_pass(&fixture, &package);
    let foreign = directory.join("foreign.txt");
    fs::write(&foreign, b"not ours").expect("foreign staging content");

    let failure = failure_code(run_operation(
        &fixture.platform(),
        paths,
        Operation::Uninstall,
        None,
    ));
    assert_eq!(
        failure,
        FailureCode::CleanupPending,
        "an outside-root stage that still exists is not a clean uninstall"
    );
    assert_eq!(
        fs::read(&foreign).expect("foreign entry survives"),
        b"not ours"
    );
    assert!(
        directory.exists(),
        "the unidentifiable contents are preserved"
    );
    assert!(receipt(paths).is_some(), "the durable intent is retained");
    let _ = fs::remove_dir_all(&directory);
}

/// Observed disposal is not best effort: a directory that still holds foreign
/// content is preserved and reported, so it can never count as clean.
#[test]
fn observed_disposal_refuses_foreign_residue() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let helper = fixture.temp.path().join("helper-image");
    let helper_bytes = b"the signed cleanup helper";
    fs::write(&helper, helper_bytes).expect("helper image");
    let request = RemovalRequest {
        helper_source: &helper,
        helper_sha256: Digest::of(helper_bytes),
        publisher: PUBLISHER,
        transaction: "0123456789abcdef0123456789abcdef",
    };
    let directory = cleanup_staging_directory(paths, request.transaction);
    let _ = fs::remove_dir_all(&directory);
    let staged = RemovalCoordinator::stage(paths, &request)
        .expect("stage")
        .expect("helper present");
    let foreign = directory.join("not-ours.txt");
    fs::write(&foreign, b"foreign").expect("foreign residue");
    let failure = staged
        .discard_observed()
        .expect_err("residue is not a completed disposal");
    assert_eq!(failure.code, FailureCode::CleanupPending);
    assert_eq!(
        fs::read(&foreign).expect("foreign file survives"),
        b"foreign"
    );
    assert!(directory.exists(), "the unobserved directory is preserved");
    let _ = fs::remove_dir_all(&directory);
}

/// A staging failure must never be discarded and then reported as a clean
/// uninstall: the outside-root artifact is unaccounted for.
#[test]
fn uninstall_does_not_report_clean_when_outside_root_staging_fails() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let package = fixture.package_from(
        "v1",
        "1.0.0",
        &[
            ("host.exe", HOST_V1, true),
            ("licenses/NOTICE", NOTICE_V1, false),
            ("maintenance/uninstall.exe", UNINSTALLER_V1, true),
            (
                "veles-bridge-cleanup.exe",
                b"the signed cleanup helper",
                true,
            ),
        ],
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&package);
    let transaction = receipt(paths).expect("committed receipt").transaction_id;
    let directory = cleanup_staging_directory(paths, &transaction);
    let _ = fs::remove_dir_all(&directory);
    fs::create_dir_all(&directory).expect("unrecognisable residue");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))
            .expect("private residue");
    }
    #[cfg(windows)]
    protect_staging_fixture(&directory);
    fs::write(directory.join("foreign.txt"), b"not ours").expect("foreign content");

    let failure = failure_code(run_operation(
        &fixture.platform(),
        paths,
        Operation::Uninstall,
        None,
    ));
    // An unaccounted outside-root stage is never clean. On Windows the residue's
    // private DACL cannot be proven either, so the anchored open refuses it earlier as
    // UnsafePath; both codes fail closed and keep the durable intent.
    #[cfg(windows)]
    assert!(
        matches!(
            failure,
            FailureCode::CleanupPending | FailureCode::UnsafePath
        ),
        "an unaccounted outside-root stage is not clean: {failure:?}"
    );
    #[cfg(not(windows))]
    assert_eq!(
        failure,
        FailureCode::CleanupPending,
        "an unaccounted outside-root stage is not clean"
    );
    assert!(receipt(paths).is_some(), "the durable intent is retained");
    assert!(paths.root().exists(), "nothing was removed");
    assert!(
        directory.join("foreign.txt").exists(),
        "foreign staging content is preserved"
    );
    let _ = fs::remove_dir_all(&directory);
}

/// A clean uninstall that never needed the helper still disposes of the staged
/// pair through the observed path.
#[test]
fn a_clean_uninstall_disposes_the_outside_root_stage() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let package = fixture.package_from(
        "v1",
        "1.0.0",
        &[
            ("host.exe", HOST_V1, true),
            ("licenses/NOTICE", NOTICE_V1, false),
            ("maintenance/uninstall.exe", UNINSTALLER_V1, true),
            (
                "veles-bridge-cleanup.exe",
                b"the signed cleanup helper",
                true,
            ),
        ],
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&package);
    let transaction = receipt(paths).expect("committed receipt").transaction_id;
    let report = run_operation(&fixture.platform(), paths, Operation::Uninstall, None)
        .expect("the owned installation uninstalls");
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(report.warnings.is_empty());
    assert!(
        !cleanup_staging_directory(paths, &transaction).exists(),
        "the staged launcher/helper pair and directory are observed gone"
    );
}

/// A clean uninstall leaves no owned artifact behind: not the payload, not the
/// maintenance assets, not the lock, state, receipts or the private layout.
#[test]
fn a_clean_uninstall_removes_every_owned_layout_artifact() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let package = fixture.package_from(
        "v1",
        "1.0.0",
        &maintenance_files(UNINSTALLER_V1),
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&package);
    let report = run_operation(&fixture.platform(), paths, Operation::Uninstall, None)
        .expect("the owned installation uninstalls");
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(
        report.warnings.is_empty(),
        "nothing of ours is left, so nothing is a leftover: {:?}",
        report.warnings
    );
    assert!(!paths.lock_path().exists(), "the lock file is removed");
    assert!(!paths.receipt_path().exists(), "the receipt is removed");
    assert!(!paths.journal_path().exists(), "the journal is removed");
    assert!(!paths.state().exists(), "the state directory is removed");
    assert!(
        !paths.versions().exists(),
        "the versions directory is removed"
    );
    assert!(
        !paths.maintenance().exists(),
        "the maintenance directory is removed"
    );
    assert!(
        !paths.root().exists(),
        "the private install root is removed: {:?}",
        snapshot(fixture.temp.path())
    );
}

/// An owned file the running engine cannot remove is handed to the outside-root
/// cleanup helper; only its authenticated completion and the observed absence of
/// the file make the uninstall clean.
#[test]
fn the_cleanup_helper_completes_an_owned_removal_the_engine_could_not() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let package = fixture.package_from(
        "v1",
        "1.0.0",
        &[
            ("host.exe", HOST_V1, true),
            ("maintenance/uninstall.exe", UNINSTALLER_V1, true),
            (
                "veles-bridge-cleanup.exe",
                b"the signed cleanup helper",
                true,
            ),
        ],
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&package);
    let platform = fixture.platform_with(Faults {
        host_state: HostState::Quiescent,
        fail_remove_of: Some("maintenance/uninstall.exe".to_owned()),
        cleanup: Some(veles_bridge_installer::CleanupBehavior::Complete),
        ..Faults::default()
    });
    let report = run_operation(&platform, paths, Operation::Uninstall, None)
        .expect("the helper removes what the engine could not");
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert_eq!(
        platform.cleanup_runs(),
        vec![vec!["maintenance/uninstall.exe".to_owned()]],
        "the coordinator hands the exact remaining owned entry to the helper"
    );
    assert!(!paths.root().exists(), "the observed removal is clean");
}

/// If the helper removes the last owned file of an immutable version, the
/// directories that could not be removed before the helper must be retried
/// deepest-first. Otherwise uninstall reports success and erases its intent while
/// leaving owned empty version directories (and the root) behind (N3).
#[test]
fn the_cleanup_helper_finishes_owned_version_directories_before_teardown() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let package = fixture.package_from(
        "v1",
        "1.0.0",
        &[
            ("host.exe", HOST_V1, true),
            ("licenses/NOTICE", NOTICE_V1, false),
            ("maintenance/uninstall.exe", UNINSTALLER_V1, true),
            (
                "veles-bridge-cleanup.exe",
                b"the signed cleanup helper",
                true,
            ),
        ],
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&package);
    let p1 = package.descriptor.payload_id;
    let version_leaf = format!("versions/1.0.0-{}/host.exe", p1.to_hex());
    let platform = fixture.platform_with(Faults {
        host_state: HostState::Quiescent,
        fail_remove_of: Some(version_leaf.clone()),
        cleanup: Some(veles_bridge_installer::CleanupBehavior::Complete),
        ..Faults::default()
    });
    let report = run_operation(&platform, paths, Operation::Uninstall, None)
        .expect("the helper removes the last owned leaf and the directories are finished");
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(
        report.warnings.is_empty(),
        "no owned directory or root survives, so nothing is a leftover: {:?}",
        report.warnings
    );
    assert_eq!(
        platform.cleanup_runs(),
        vec![vec![version_leaf]],
        "the helper was asked to remove only the leaf the engine could not"
    );
    assert!(
        !version_dir(paths, "1.0.0", p1).exists(),
        "the owned empty version directory is removed after the helper"
    );
    assert!(
        !paths.versions().exists(),
        "the versions container is removed"
    );
    assert!(!paths.root().exists(), "the private root is removed");
}

/// A helper that reports an authenticated completion without removing the entry
/// is not evidence of removal: the uninstall stays pending and the durable
/// intent is retained for a retry.
#[test]
fn a_cleanup_helper_that_claims_completion_without_removal_is_not_clean() {
    let fixture = Fixture::windows();
    let paths = &fixture.paths;
    let package = fixture.package_from(
        "v1",
        "1.0.0",
        &[
            ("host.exe", HOST_V1, true),
            ("maintenance/uninstall.exe", UNINSTALLER_V1, true),
            (
                "veles-bridge-cleanup.exe",
                b"the signed cleanup helper",
                true,
            ),
        ],
        policy(1, Vec::new(), Vec::new()),
    );
    fixture.install(&package);
    let platform = fixture.platform_with(Faults {
        host_state: HostState::Quiescent,
        fail_remove_of: Some("maintenance/uninstall.exe".to_owned()),
        cleanup: Some(veles_bridge_installer::CleanupBehavior::ClaimedButLeft),
        ..Faults::default()
    });
    let failure = run_operation(&platform, paths, Operation::Uninstall, None)
        .expect_err("a claimed completion without removal is not a clean uninstall");
    assert_eq!(failure.code, FailureCode::CleanupPending);
    assert!(
        receipt(paths).is_some(),
        "the durable intent is retained for a retry"
    );
    assert!(
        matches!(journal_state(paths), JournalState::Pending(_)),
        "the interrupted removal is resumable"
    );
}
