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

use veles_bridge_installer::{
    AuthenticatedPackage, Channel, Digest, FailureCode, FakePlatform, Faults, HostState,
    InstallFailure, InstallLock, InstallPaths, InstallResult, InventoryEntry, JournalState,
    NativeManifest, Operation, PRODUCTION_HOST_NAME, Platform, PlatformCall, PortablePlatform,
    RETAINED_FILE_NAME, RETAINED_SCHEMA_VERSION, Receipt, ReceiptState, Registration,
    ReleaseDescriptor, ReleasePolicy, RetainedInstall, STATE_ABSENT, STATE_DAMAGED, STATE_FOREIGN,
    STATE_REPAIRED, STATE_ROLLED_BACK, STATE_UNINSTALLED, StateDocument,
    TRUST_ASSESSMENT_MACOS_GATEKEEPER, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, Target, TrustProof,
    ValidatedPath, Version, WarningCode, authenticate_package, canonical_json, extension_origin,
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
    let root = temp.path().join("packages").join(name);
    let payload = root.join("payload");
    fs::create_dir_all(payload.join("licenses")).expect("payload tree");
    fs::write(payload.join("host.exe"), host).expect("host payload");
    fs::write(payload.join("licenses/NOTICE"), notice).expect("notice payload");

    let inventory = write_inventory(&[
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
    .expect("canonical inventory document");
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
    for changed in changed_paths(before, after) {
        assert!(
            changed == root || changed.starts_with(&format!("{root}/")) || changed == registration,
            "{context}: {changed} is not inside the install root or the fixed registration"
        );
    }
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

    fn platform(&self) -> FakePlatform {
        FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
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
        Self {
            inner: FakePlatform::with_faults(Faults {
                host_state: HostState::Quiescent,
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

    // Uninstalling twice is idempotent and changes nothing at all. The preserved
    // foreign files are reported again because they still block private-layout
    // removal.
    let platform = fixture.platform();
    let again = run_operation(&platform, paths, Operation::Uninstall, None)
        .expect("a second uninstall is idempotent");
    assert_eq!(again.state, STATE_UNINSTALLED);
    assert_eq!(again.warnings, vec![WarningCode::LeftoverFiles]);
    assert!(
        platform.mutations().is_empty(),
        "nothing is left to remove: {:?}",
        platform.mutations()
    );
    assert_eq!(snapshot(fixture.temp.path()), after);
    fixture.assert_unowned_assets(&unowned, "after the second uninstall");

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
    assert!(platform.mutations().is_empty());
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
    assert_eq!(
        fs::read_dir(paths.versions())
            .expect("versions dir")
            .count(),
        0
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

    let platform = HookedPlatform::new(Hook::LaunchAfterStaging);
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
    assert!(platform.mutations().is_empty());

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
            version_dir(paths, "2.0.0", payload_id(HOST_V2, NOTICE_V2)),
            version_dir(paths, "1.0.0", p1)
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
        platform.mutations().is_empty(),
        "nothing is left to remove: {:?}",
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
    let platform = HookedPlatform::new(Hook::Reregister {
        when_executable_under: version_dir(paths, "3.0.0", p3)
            .to_str()
            .expect("UTF-8 fixture path")
            .to_owned(),
        replacement,
    });
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

    let crashing = HookedPlatform::new(Hook::CrashBeforeRemoval {
        prefix: format!("versions/1.0.0-{}", p1.to_hex()),
    });
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
