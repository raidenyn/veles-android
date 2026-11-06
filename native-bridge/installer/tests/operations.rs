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
    InstallLock, InstallPaths, InstallResult, InventoryEntry, JournalState, Operation,
    PRODUCTION_HOST_NAME, Platform, PlatformCall, PortablePlatform, Receipt, ReceiptState,
    Registration, ReleaseDescriptor, ReleasePolicy, STATE_ABSENT, STATE_DAMAGED, STATE_FOREIGN,
    STATE_REPAIRED, STATE_ROLLED_BACK, STATE_UNINSTALLED, StateDocument,
    TRUST_ASSESSMENT_MACOS_GATEKEEPER, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, Target, TrustProof,
    ValidatedPath, Version, authenticate_package, canonical_json, extension_origin, parse_manifest,
    path_is_within, recover, run_operation, run_operation_within, write_carrier, write_inventory,
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
        api_min: 1,
        api_max: 2,
        payload_id: Digest::of(&inventory),
        policy,
    };
    descriptor
        .validate()
        .expect("the fixture descriptor is a valid release identity");

    let carrier =
        write_carrier(&canonical_json(&descriptor).expect("descriptor JSON")).expect("carrier");
    let proof = TrustProof::new(PUBLISHER, assessment, carrier).expect("fixture trust proof");

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
        ReceiptState::Unreadable => panic!("the committed receipt must be interpretable"),
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
        build_package(&self.temp, self.target, name, version, host, notice, policy)
    }

    fn platform(&self) -> FakePlatform {
        FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
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

    fn verify_trust(
        &self,
        source: &veles_bridge_installer::PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        self.inner.verify_trust(source, expected)
    }

    fn remove_owned(&self, entry: &veles_bridge_installer::OwnedEntry) -> InstallResult<()> {
        self.inner.remove_owned(entry)
    }

    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()> {
        self.inner.flush_parent(path)
    }
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
    let platform = fixture.platform();
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

    // No retained package means no rollback target at all.
    let platform = fixture.platform();
    let failure = run_operation(&platform, paths, Operation::Rollback, None)
        .expect_err("rollback needs a retained authenticated package");
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

    // Uninstalling twice is idempotent and changes nothing at all.
    let platform = fixture.platform();
    let again = run_operation(&platform, paths, Operation::Uninstall, None)
        .expect("a second uninstall is idempotent");
    assert_eq!(again.state, STATE_UNINSTALLED);
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

    let platform = fixture.platform();
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
    let platform = fixture.platform();
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
    let platform = probe.platform();
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

        // A fresh process reconciles to exactly one complete installation.
        let recovering = fixture.platform();
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
