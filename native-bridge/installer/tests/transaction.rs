//! Journaled staging, activation and crash-recovery contract tests (OTP-25 Task 6).
//!
//! Portable evidence only. [`Faults::panic_at_mutation`] injects a simulated
//! process death at every durable mutation boundary, which interrupts the
//! transaction *without* running any cleanup - exactly like a killed installer.
//! [`recover`] must then reconcile the durable journal with verified live state:
//! it either completes the authenticated staged activation or restores the last
//! verified version, and it never trusts a journal marker alone.
//!
//! Native HKCU publication, Windows `ReplaceFileW` and macOS sealing remain
//! Task 8 evidence; these fixtures are synthetic and portable.

use std::collections::BTreeMap;
use std::fs;
use std::panic::{self, AssertUnwindSafe};
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicU64, Ordering};

use veles_bridge_installer::{
    Channel, Digest, FailureCode, FakePlatform, Faults, HostFault, HostState, InstallPaths,
    InventoryEntry, JOURNAL_SCHEMA_VERSION, Journal, JournalClaim, JournalState, ManifestSnapshot,
    NativeManifest, Operation, PRODUCTION_HOST_NAME, Phase, Platform, PlatformCall,
    PolicyCarrierRefs, PortablePlatform, Receipt, ReceiptState, RegistrationPreimage,
    ReleaseDescriptor, ReleasePolicy, STATE_CLEAN, STATE_INSTALLED, STATE_RECOVERED_ACTIVE,
    STATE_RECOVERED_PREVIOUS, StateDocument, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, Target,
    TrustProof, Version, WindowsPreimage, authenticate_package, canonical_json, extension_origin,
    install, parse_manifest, path_is_within, recover, write_carrier, write_inventory,
};

const EXTENSION_ID: &str = "mnopabcdefghijklmnopabcdefghijkl";
const PUBLISHER: &str = "Publisher One";
const HOST_V1: &[u8] = b"v1 host payload";
const NOTICE_V1: &[u8] = b"v1 licence notice";
const HOST_V2: &[u8] = b"v2 host payload";
const NOTICE_V2: &[u8] = b"v2 licence notice";

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
            "veles-installer-task6-transaction-{}-{unique}",
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

fn windows_paths(temp: &TempRoot) -> InstallPaths {
    let home = temp.path().join("Test User Å/用户");
    let local = temp.path().join("Local AppData/用户 Å");
    InstallPaths::for_user(Target::WindowsX64, Channel::Production, &home, Some(&local))
        .expect("valid injected Windows roots")
}

/// One authenticated release package on disk plus the evidence that covers it.
struct Package {
    root: PathBuf,
    descriptor: ReleaseDescriptor,
    proof: TrustProof,
}

fn build_package(
    temp: &TempRoot,
    name: &str,
    version: &str,
    host: &[u8],
    notice: &[u8],
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

    let descriptor = ReleaseDescriptor {
        schema: 1,
        channel: Channel::Production,
        version: version.to_owned(),
        source_commit: "0123456789abcdef0123456789abcdef01234567".to_owned(),
        target: Target::WindowsX64,
        minimum_os: "11".to_owned(),
        extension_id: EXTENSION_ID.to_owned(),
        host_name: PRODUCTION_HOST_NAME.to_owned(),
        bundle_id: PRODUCTION_HOST_NAME.to_owned(),
        api_min: 1,
        api_max: 2,
        payload_id: Digest::of(&inventory),
        policy: ReleasePolicy {
            publishers: vec![PUBLISHER.to_owned()],
            denied_payloads: Vec::new(),
            rollback_payloads: Vec::new(),
            epoch: 1,
        },
    };
    descriptor
        .validate()
        .expect("the fixture descriptor is a valid release identity");

    let carrier =
        write_carrier(&canonical_json(&descriptor).expect("descriptor JSON")).expect("carrier");
    // The signed carrier artifact is retained with the version so a rollback can
    // re-run the OS verification; the fixture's copy also stands in for the
    // native signed binary.
    fs::write(root.join("carrier.exe"), &carrier).expect("carrier artifact");
    let proof = TrustProof::new(PUBLISHER, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, carrier)
        .expect("fixture trust proof");

    Package {
        root,
        descriptor,
        proof,
    }
}

/// Authenticate one fixture package through Task 4's only constructor.
fn authenticate(package: &Package) -> veles_bridge_installer::AuthenticatedPackage {
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

fn manifest_bytes(paths: &InstallPaths, executable: &Path, description: &str) -> Vec<u8> {
    canonical_json(&NativeManifest {
        name: paths.registration_name().to_owned(),
        description: description.to_owned(),
        path: executable.to_str().expect("UTF-8 fixture path").to_owned(),
        kind: "stdio".to_owned(),
        allowed_origins: vec![extension_origin(EXTENSION_ID).expect("valid fixture origin")],
    })
    .expect("canonical manifest")
}

/// Every file below `root` with its exact bytes; directories are empty entries.
fn snapshot(root: &Path) -> BTreeMap<String, Vec<u8>> {
    fn walk(base: &Path, directory: &Path, out: &mut BTreeMap<String, Vec<u8>>) {
        let entries = fs::read_dir(directory).expect("snapshot directory");
        for entry in entries {
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Scenario {
    Fresh,
    Upgrade,
}

impl Scenario {
    fn label(self) -> &'static str {
        match self {
            Self::Fresh => "fresh install",
            Self::Upgrade => "upgrade",
        }
    }
}

/// A pristine per-scenario baseline: a new temp root, the v1 baseline install
/// (upgrade only) and the authenticated v2 package ready to install.
struct Fixture {
    temp: TempRoot,
    paths: InstallPaths,
    package: veles_bridge_installer::AuthenticatedPackage,
    proof: TrustProof,
    prior_version_dir: Option<PathBuf>,
    prior_payload_id: Option<Digest>,
    new_version_dir: PathBuf,
}

impl Fixture {
    fn new(scenario: Scenario) -> Self {
        let temp = TempRoot::new();
        let paths = windows_paths(&temp);
        let v1 = build_package(&temp, "v1", "1.0.0", HOST_V1, NOTICE_V1);
        let v2 = build_package(&temp, "v2", "2.0.0", HOST_V2, NOTICE_V2);
        let prior_version_dir = version_dir(&paths, "1.0.0", v1.descriptor.payload_id);
        let new_version_dir = version_dir(&paths, "2.0.0", v2.descriptor.payload_id);

        let (prior_version_dir, prior_payload_id) = match scenario {
            Scenario::Fresh => (None, None),
            Scenario::Upgrade => {
                // A complete previous installation, committed by a clean install.
                let platform = FakePlatform::with_faults(Faults {
                    host_state: HostState::Quiescent,
                    trust: Some(v1.proof.clone()),
                    ..Faults::default()
                });
                let report = install(&platform, &paths, authenticate(&v1))
                    .expect("the previous version installs");
                assert_eq!(report.state, STATE_INSTALLED);
                (Some(prior_version_dir), Some(v1.descriptor.payload_id))
            }
        };

        Self {
            package: authenticate(&v2),
            proof: v2.proof.clone(),
            temp,
            paths,
            prior_version_dir,
            prior_payload_id,
            new_version_dir,
        }
    }

    fn platform(&self) -> FakePlatform {
        self.platform_with(Faults::default())
    }

    fn platform_with(&self, faults: Faults) -> FakePlatform {
        FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            trust: Some(self.proof.clone()),
            ..faults
        })
    }

    fn payload_id(&self) -> Digest {
        self.package.descriptor().payload_id
    }

    fn registration(&self) -> Option<Vec<u8>> {
        fs::read(self.paths.registration_path()).ok()
    }

    fn receipt(&self) -> Option<Receipt> {
        match Receipt::load(&PortablePlatform::new(), &self.paths)
            .expect("the committed receipt is readable")
        {
            ReceiptState::None => None,
            ReceiptState::Committed(receipt) => Some(*receipt),
            ReceiptState::Unreadable | ReceiptState::Unsupported => {
                panic!("the committed receipt must be interpretable")
            }
        }
    }

    /// The durable journal slot, read through the platform boundary.
    fn journal_state(&self) -> JournalState {
        Journal::load(&PortablePlatform::new(), &self.paths).expect("the journal is readable")
    }

    fn assert_journal_closed(&self, context: &str) {
        assert!(
            matches!(self.journal_state(), JournalState::None),
            "{context}: the transaction is closed"
        );
    }

    fn assert_previous_complete(&self, context: &str) {
        let directory = self
            .prior_version_dir
            .as_ref()
            .expect("the upgrade fixture has a previous version");
        assert_eq!(
            fs::read(directory.join("host.exe")).expect("previous host survives"),
            HOST_V1,
            "{context}: the previous version is retained complete"
        );
        assert_eq!(
            fs::read(directory.join("licenses/NOTICE")).expect("previous notice survives"),
            NOTICE_V1,
            "{context}: the previous version notice survives"
        );
    }

    fn assert_new_complete(&self, context: &str) {
        assert_eq!(
            fs::read(self.new_version_dir.join("host.exe")).expect("new host exists"),
            HOST_V2,
            "{context}: the new host payload is complete"
        );
        assert_eq!(
            fs::read(self.new_version_dir.join("licenses/NOTICE")).expect("new notice exists"),
            NOTICE_V2,
            "{context}: the new notice payload is complete"
        );
        let inventory =
            fs::read(self.new_version_dir.join("inventory.json")).expect("installed inventory");
        assert_eq!(
            Digest::of(&inventory),
            self.payload_id(),
            "{context}: the installed inventory is the authenticated payload identity"
        );
    }

    /// The single invariant after any interruption plus recovery: exactly one
    /// complete registration is live (or none for a failed first install), the
    /// selected executable is a complete payload, and no partial document exists.
    fn assert_settled(&self, scenario: Scenario, context: &str) {
        match (self.registration(), scenario) {
            (None, Scenario::Fresh) => {
                assert!(
                    !self.new_version_dir.exists(),
                    "{context}: a failed first install removes only its newly owned assets"
                );
                assert!(
                    self.receipt().is_none(),
                    "{context}: no receipt is committed without an active install"
                );
            }
            (None, Scenario::Upgrade) => {
                panic!("{context}: the previous version must never be absent")
            }
            (Some(bytes), _) => {
                let manifest = parse_manifest(&bytes).unwrap_or_else(|_| {
                    panic!("{context}: the live registration is complete JSON")
                });
                assert_eq!(manifest.name, self.paths.registration_name());
                assert_eq!(manifest.kind, "stdio");
                let executable = manifest
                    .executable()
                    .unwrap_or_else(|_| panic!("{context}: the manifest selects an absolute path"));
                assert!(
                    path_is_within(self.paths.root(), &executable),
                    "{context}: the executable stays inside the install root"
                );
                let content = fs::read(&executable)
                    .unwrap_or_else(|_| panic!("{context}: the selected executable exists"));
                if content == HOST_V2 {
                    self.assert_new_complete(context);
                    let receipt = self
                        .receipt()
                        .unwrap_or_else(|| panic!("{context}: an active install has a receipt"));
                    assert_eq!(receipt.payload_id, self.payload_id(), "{context}");
                    assert_eq!(
                        receipt.operation,
                        Operation::Install,
                        "{context}: the receipt records the operation"
                    );
                    assert_eq!(
                        receipt.ownership.manifest_sha256,
                        Digest::of(&bytes),
                        "{context}: the receipt names the exact committed manifest bytes"
                    );
                    if self.prior_version_dir.is_some() {
                        self.assert_previous_complete(context);
                    }
                } else if content == HOST_V1 {
                    self.assert_previous_complete(context);
                    assert!(
                        !self.new_version_dir.exists(),
                        "{context}: a rolled-back upgrade removes the newly staged version"
                    );
                } else {
                    panic!("{context}: the executable is neither the previous nor the new host");
                }
            }
        }

        self.assert_journal_closed(context);
    }

    /// Pre-existing files this installer does not own; the fixed root layout
    /// exists before a first install because the lock creates it.
    fn seed_unrelated_assets(&self, context: &str) -> BTreeMap<String, Vec<u8>> {
        for (relative, content) in [
            ("versions/unrelated.txt", b"not ours".as_slice()),
            ("maintenance/helper.bin", b"prior maintenance".as_slice()),
            ("state/unrelated-state.json", b"{}\n".as_slice()),
        ] {
            let path = self.paths.root().join(relative);
            fs::create_dir_all(path.parent().expect("asset parent")).expect(context);
            fs::write(&path, content).expect(context);
        }
        [
            "versions/unrelated.txt",
            "maintenance/helper.bin",
            "state/unrelated-state.json",
        ]
        .into_iter()
        .map(|relative| {
            (
                relative.to_owned(),
                fs::read(self.paths.root().join(relative)).expect(context),
            )
        })
        .collect()
    }

    fn assert_unrelated_assets(&self, expected: &BTreeMap<String, Vec<u8>>, context: &str) {
        for (relative, content) in expected {
            assert_eq!(
                &fs::read(self.paths.root().join(relative)).expect(context),
                content,
                "{context}: unowned file {relative} is preserved"
            );
        }
    }

    /// A journal a crashed process could have left behind: it claims this
    /// installer owns the registration preimage and is mid-activation.
    fn crafted_journal(&self, preimage: &[u8]) -> Journal {
        self.crafted_journal_with_windows(preimage, None)
    }

    /// The same crafted journal with an explicit HKCU preimage, as a native
    /// Windows adapter would have journaled it.
    fn crafted_journal_with_windows(
        &self,
        preimage: &[u8],
        windows: Option<(&str, Option<&str>)>,
    ) -> Journal {
        let executable = self
            .prior_version_dir
            .as_ref()
            .expect("the upgrade fixture has a previous version")
            .join("host.exe");
        let next_manifest = manifest_bytes(&self.paths, &executable, "crashed transaction");
        let registration_preimage = match windows {
            Some((key, value)) => RegistrationPreimage::new(Digest::of(preimage))
                .with_windows(key.to_owned(), value.map(str::to_owned))
                .expect("bounded Windows preimage"),
            None => RegistrationPreimage::new(Digest::of(preimage)),
        };
        Journal {
            schema: JOURNAL_SCHEMA_VERSION,
            transaction_id: "0123456789abcdef0123456789abcdef".to_owned(),
            operation: Operation::Install,
            phase: Phase::RegistrationPublished,
            target: self.paths.target(),
            channel: self.paths.channel(),
            root: self
                .paths
                .root()
                .to_str()
                .expect("UTF-8 install root")
                .to_owned(),
            version: "2.0.0".to_owned(),
            payload_id: self.payload_id(),
            old_version: Some("1.0.0".to_owned()),
            old_payload_id: self.prior_payload_id,
            old_carrier_sha256: Some(Digest::of(b"prior carrier")),
            manifest: ManifestSnapshot::new(next_manifest).expect("bounded manifest"),
            old_manifest: Some(ManifestSnapshot::new(preimage.to_vec()).expect("bounded manifest")),
            preimage: Some(registration_preimage),
            policy: PolicyCarrierRefs::new(
                Digest::of(b"descriptor"),
                Digest::of(b"carrier"),
                PUBLISHER,
                TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
                1,
            )
            .expect("bounded policy references"),
            release_policy: ReleasePolicy {
                publishers: vec![PUBLISHER.to_owned()],
                denied_payloads: Vec::new(),
                rollback_payloads: Vec::new(),
                epoch: 1,
            },
            created: vec![JournalClaim::file(
                format!("versions/2.0.0-{}/host.exe", self.payload_id().to_hex()),
                Digest::of(HOST_V2),
                HOST_V2.len() as u64,
                false,
            )],
        }
    }
}

/// The exact mutation sequence of one clean transaction for `scenario`.
fn clean_mutations(scenario: Scenario) -> Vec<PlatformCall> {
    let fixture = Fixture::new(scenario);
    let platform = fixture.platform();
    let report = install(&platform, &fixture.paths, fixture.package.clone())
        .expect("the clean transaction succeeds");
    assert_eq!(report.state, STATE_INSTALLED, "{}", scenario.label());
    platform.mutations()
}

fn position_of(mutations: &[PlatformCall], call: PlatformCall) -> usize {
    mutations
        .iter()
        .position(|candidate| *candidate == call)
        .unwrap_or_else(|| panic!("the clean transaction uses {call:?}: {mutations:?}"))
}

/// Dies *before* each whole platform mutation (a state-document replacement, a
/// payload write, a directory creation, a publication step). It does not die
/// inside one: see `crash_inside_every_durable_primitive` for the file-create /
/// write / flush / rename / unlink boundaries within each call.
#[test]
fn crash_before_every_platform_mutation() {
    for scenario in [Scenario::Fresh, Scenario::Upgrade] {
        let sequence = clean_mutations(scenario);
        let total = sequence.len();
        // Every durable step class the brief names must actually be crossed,
        // including the two separately journaled publication steps and state
        // document replacement.
        for call in [
            PlatformCall::WriteDurable,
            PlatformCall::WriteStateDocument,
            PlatformCall::CreateDirectory,
            PlatformCall::PublishManifest,
            PlatformCall::PublishRegistration,
        ] {
            assert!(
                sequence.contains(&call),
                "{}: the transaction crosses {call:?}: {sequence:?}",
                scenario.label()
            );
        }
        assert!(
            total >= 10,
            "{}: the transaction has several durable boundaries: {total}",
            scenario.label()
        );

        // `1..=total` dies before each durable mutation; `total + 1` dies after
        // the last one (no injection, so the transaction simply completes).
        let mut observed = std::collections::BTreeSet::new();
        for attempt in 1..=total + 1 {
            let fixture = Fixture::new(scenario);
            let context = format!(
                "{}: interrupted at durable attempt {attempt}/{total}",
                scenario.label()
            );
            let crashed = attempt <= total;
            let faults = if crashed {
                Faults {
                    panic_at_mutation: Some(attempt),
                    ..Faults::default()
                }
            } else {
                Faults::default()
            };
            let platform = fixture.platform_with(faults);
            let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
                install(&platform, &fixture.paths, fixture.package.clone())
            }));

            if crashed {
                assert!(
                    outcome.is_err(),
                    "{context}: the injected death interrupts the transaction"
                );
                assert!(
                    attempt == 1 || !matches!(fixture.journal_state(), JournalState::None),
                    "{context}: durable journal intent precedes the first asset mutation"
                );

                // A fresh process: no injected fault, no inherited memory.
                let recovering = fixture.platform();
                let report = recover(&recovering, &fixture.paths)
                    .unwrap_or_else(|failure| panic!("{context}: recovery failed: {failure:?}"));
                assert!(
                    report.failure.is_none(),
                    "{context}: recovery completes without a pending failure: {report:?}"
                );
                observed.insert(report.state.clone());
                if attempt == 1 {
                    assert_eq!(
                        report.state, STATE_CLEAN,
                        "{context}: nothing was durable yet"
                    );
                } else {
                    // A first install can only be rolled back to "nothing
                    // installed"; an upgrade returns to its previous version or
                    // completes the staged activation.
                    assert!(
                        report.state == STATE_CLEAN
                            || report.state == STATE_RECOVERED_ACTIVE
                            || report.state == STATE_RECOVERED_PREVIOUS,
                        "{context}: recovery reconciles the pending transaction: {report:?}"
                    );
                    if scenario == Scenario::Upgrade {
                        assert_ne!(
                            report.state, STATE_CLEAN,
                            "{context}: an upgrade never recovers to an empty installation"
                        );
                    }
                }
            } else {
                let report = outcome
                    .expect("no injected death after the last boundary")
                    .unwrap_or_else(|failure| panic!("{context}: {failure:?}"));
                assert_eq!(report.state, STATE_INSTALLED, "{context}");
                assert_eq!(
                    report.active_payload,
                    Some(fixture.payload_id()),
                    "{context}: the completed install is active"
                );
            }

            fixture.assert_settled(scenario, &context);
        }

        // Both recovery outcomes must actually be exercised: an interrupted
        // upgrade is restored, and any interrupted activation is completed.
        assert!(
            observed.contains(STATE_RECOVERED_ACTIVE),
            "{}: a complete activation is finished by recovery: {observed:?}",
            scenario.label()
        );
        assert!(
            observed.contains(STATE_CLEAN) || observed.contains(STATE_RECOVERED_PREVIOUS),
            "{}: an interrupted rollback is restored: {observed:?}",
            scenario.label()
        );
        if scenario == Scenario::Upgrade {
            assert!(
                observed.contains(STATE_RECOVERED_PREVIOUS),
                "upgrade: the previous version is restored before activation: {observed:?}"
            );
        }
    }
}

const WRITE_DURABLE_POINTS: [&str; 4] = [
    "write_durable: created, empty",
    "write_durable: written, not flushed",
    "write_durable: flushed, not read back",
    "write_durable: verified, parent entry not flushed",
];
const REPLACE_FILE_POINTS: [&str; 2] = [
    "replace_file: revalidated, live entry untouched",
    "replace_file: renamed, parent not flushed",
];

/// The named crash points each mutation of a clean **install** crosses on the
/// portable adapter, written out by hand. This is the inventory the crash test is
/// held to; it is deliberately independent of the implementation's own counting.
///
/// Explicitly *not* covered, so the test makes no claim about them:
/// * `PublishRegistration` - the portable adapter has no durable registry
///   primitive (registration is the manifest file, written by `PublishManifest`);
///   native HKCU publication is a Windows-only adapter and NOT RUN on this host.
/// * the bundle-tree (`.app`) form of `InstallCarrier` - the fixture carrier is a
///   single file; the native metadata-preserving copies are NOT RUN.
/// * removal, quarantine and uninstall primitives - the install transaction does
///   not cross them; an unclassified call fails the test rather than passing it.
fn expected_boundaries(call: PlatformCall) -> Vec<&'static str> {
    match call {
        PlatformCall::WriteDurable | PlatformCall::InstallCarrier => WRITE_DURABLE_POINTS.to_vec(),
        PlatformCall::WriteStateDocument | PlatformCall::PublishManifest => WRITE_DURABLE_POINTS
            .into_iter()
            .chain(REPLACE_FILE_POINTS)
            .collect(),
        PlatformCall::CreateDirectory => {
            vec!["create_directory: created, permissions not yet applied"]
        }
        #[cfg(unix)]
        PlatformCall::SetLaunchPermissions => vec![
            "apply_launch_permissions: opened, mode unchanged",
            "apply_launch_permissions: mode set, not flushed",
            "apply_launch_permissions: mode flushed, not re-checked",
        ],
        // No permission bits to set on a non-Unix portable adapter.
        #[cfg(not(unix))]
        PlatformCall::SetLaunchPermissions => Vec::new(),
        PlatformCall::PublishRegistration => Vec::new(),
        other => panic!("the install inventory does not classify {other:?}"),
    }
}

/// Every mutation's crossings, in order, equal the hand-written inventory.
fn assert_boundary_inventory(platform: &FakePlatform, label: &str) {
    let mutations = platform.mutations();
    let log = platform.boundary_log();
    for (index, call) in mutations.iter().enumerate() {
        let observed: Vec<String> = log
            .iter()
            .filter(|crossing| crossing.mutation == index + 1)
            .map(|crossing| {
                assert_eq!(
                    crossing.call, *call,
                    "{label}: crossing attributed to a call"
                );
                format!("{}: {}", crossing.primitive, crossing.point)
            })
            .collect();
        assert_eq!(
            observed,
            expected_boundaries(*call),
            "{label}: mutation {} ({call:?}) crossed an unexpected set of crash points",
            index + 1
        );
    }
    assert!(
        log.iter()
            .all(|crossing| crossing.mutation >= 1 && crossing.mutation <= mutations.len()),
        "{label}: a crash point was crossed outside any mutation"
    );
}

/// Dies at every named durable boundary *inside* the platform primitives that a
/// clean install crosses on the portable adapter (see [`expected_boundaries`] for
/// the hand-written inventory and for what it does not cover): after a file is
/// created, written, flushed, read back, renamed, after a directory is created and
/// around the launch-permission change.
#[test]
fn crash_inside_every_durable_primitive() {
    for scenario in [Scenario::Fresh, Scenario::Upgrade] {
        // The clean run counts the boundaries the transaction crosses.
        let counted = Fixture::new(scenario);
        let platform = counted.platform();
        install(&platform, &counted.paths, counted.package.clone()).expect("clean install");
        let total = platform.boundary_count();
        // The oracle is the explicit inventory below, not a count the implementation
        // produced: an omitted crash point changes the named sequence and fails.
        assert_boundary_inventory(&platform, scenario.label());
        assert_eq!(
            total,
            platform
                .mutations()
                .iter()
                .map(|call| expected_boundaries(*call).len())
                .sum::<usize>(),
            "{}: every boundary is accounted for",
            scenario.label()
        );

        let mut observed = std::collections::BTreeSet::new();
        for boundary in 1..=total {
            let fixture = Fixture::new(scenario);
            let context = format!(
                "{}: died at durable boundary {boundary}/{total}",
                scenario.label()
            );
            let dying = fixture.platform_with(Faults {
                panic_at_boundary: Some(boundary),
                ..Faults::default()
            });
            let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
                install(&dying, &fixture.paths, fixture.package.clone())
            }));
            assert!(
                outcome.is_err(),
                "{context}: the injected death interrupts the transaction"
            );

            // A fresh process with no inherited memory reconciles it. A death
            // before the first journal byte is durable has nothing to reconcile.
            let recovering = fixture.platform();
            let report = recover(&recovering, &fixture.paths)
                .unwrap_or_else(|failure| panic!("{context}: recovery failed: {failure:?}"));
            assert!(report.failure.is_none(), "{context}: {report:?}");
            observed.insert(report.state.clone());
            fixture.assert_settled(scenario, &context);
            fixture.assert_journal_closed(&context);

            // The next run starts cleanly, whatever debris the death left.
            if fixture.registration().is_none_or(|bytes| {
                parse_manifest(&bytes)
                    .and_then(|manifest| manifest.executable())
                    .is_ok_and(|executable| fs::read(executable).ok() != Some(HOST_V2.to_vec()))
            }) {
                let retry = fixture.platform();
                install(&retry, &fixture.paths, fixture.package.clone()).unwrap_or_else(
                    |failure| panic!("{context}: the next install must succeed: {failure:?}"),
                );
                fixture.assert_new_complete(&context);
            }
        }
        assert!(
            observed.contains(STATE_RECOVERED_ACTIVE),
            "{}: a death after activation is finished forward: {observed:?}",
            scenario.label()
        );
    }
}

#[test]
fn journal_claim_cannot_override_foreign_live_manifest() {
    let fixture = Fixture::new(Scenario::Upgrade);
    let owned = fixture
        .registration()
        .expect("the previous install registered");

    // Another product takes the fixed registration name over: its manifest
    // selects an executable outside our install root.
    let foreign_directory = fixture.temp.path().join("foreign");
    fs::create_dir_all(&foreign_directory).expect("foreign directory");
    let foreign_executable = foreign_directory.join("host.exe");
    fs::write(&foreign_executable, b"foreign host").expect("foreign executable");
    let foreign_manifest = manifest_bytes(&fixture.paths, &foreign_executable, "foreign product");
    fs::write(fixture.paths.registration_path(), &foreign_manifest)
        .expect("foreign live registration");

    // The refusal happens before any staging write: only reads and the lock.
    let before = snapshot(fixture.temp.path());
    let platform = fixture.platform();
    assert_eq!(
        failure_code(install(&platform, &fixture.paths, fixture.package.clone())),
        FailureCode::ForeignState,
        "an install never overwrites a foreign live registration"
    );
    assert!(
        platform.mutations().is_empty(),
        "the refusal precedes every mutation: {:?}",
        platform.mutations()
    );
    assert_eq!(
        snapshot(fixture.temp.path()),
        before,
        "the foreign registration and every owned file stay byte-for-byte intact"
    );

    // A durable journal that claims the owned preimage is exactly the state a
    // killed installer leaves behind. It cannot make a foreign manifest ours.
    let journal = fixture.crafted_journal(&owned);
    fs::write(
        fixture.paths.journal_path(),
        journal.to_bytes().expect("bounded journal document"),
    )
    .expect("crafted journal");
    let before = snapshot(fixture.temp.path());

    let platform = fixture.platform();
    assert_eq!(
        failure_code(recover(&platform, &fixture.paths)),
        FailureCode::ForeignState,
        "recovery inspects the verified live manifest, not the journal claim"
    );
    assert!(
        platform.mutations().is_empty(),
        "a refused recovery never mutates: {:?}",
        platform.mutations()
    );
    assert_eq!(
        snapshot(fixture.temp.path()),
        before,
        "the foreign registration and the pending journal are untouched"
    );
    assert_eq!(
        fs::read(fixture.paths.registration_path()).expect("foreign registration survives"),
        foreign_manifest
    );
}

/// The journal is a closed document: unknown phases and schemas are rejected, a
/// manifest digest must bind its bytes, and an uninterpretable document is a
/// distinct slot recovery can reconcile.
#[test]
fn journal_rejects_unknown_phase_and_unbound_manifest() {
    let temp = TempRoot::new();
    let paths = windows_paths(&temp);
    let platform = FakePlatform::with_faults(Faults {
        host_state: HostState::Quiescent,
        ..Faults::default()
    });
    let _lock = platform
        .lock(&paths)
        .expect("lock creates the private layout");

    let version_directory = format!("versions/1.0.0-{}", Digest::of(b"payload").to_hex());
    let manifest = manifest_bytes(
        &paths,
        &paths.root().join(&version_directory).join("host.exe"),
        "journal fixture",
    );
    let journal = Journal {
        schema: JOURNAL_SCHEMA_VERSION,
        transaction_id: "0123456789abcdef0123456789abcdef".to_owned(),
        operation: Operation::Install,
        phase: Phase::Prepared,
        target: paths.target(),
        channel: paths.channel(),
        root: paths
            .root()
            .to_str()
            .expect("UTF-8 install root")
            .to_owned(),
        version: "1.0.0".to_owned(),
        payload_id: Digest::of(b"payload"),
        old_version: None,
        old_payload_id: None,
        old_carrier_sha256: None,
        manifest: ManifestSnapshot::new(manifest).expect("bounded manifest"),
        old_manifest: None,
        preimage: None,
        policy: PolicyCarrierRefs::new(
            Digest::of(b"descriptor"),
            Digest::of(b"carrier"),
            PUBLISHER,
            TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
            1,
        )
        .expect("bounded policy references"),
        release_policy: ReleasePolicy {
            publishers: vec![PUBLISHER.to_owned()],
            denied_payloads: Vec::new(),
            rollback_payloads: Vec::new(),
            epoch: 1,
        },
        created: vec![
            JournalClaim::directory(version_directory.clone()),
            JournalClaim::file(
                format!("{version_directory}/host.exe"),
                Digest::of(b"host"),
                4,
                true,
            ),
        ],
    };

    let bytes = journal.to_bytes().expect("bounded journal document");
    assert_eq!(
        Journal::parse(&bytes, &paths)
            .expect("a valid journal parses")
            .phase,
        Phase::Prepared
    );
    let text = String::from_utf8(bytes).expect("UTF-8 document");
    assert!(
        text.contains("\"phase\":\"prepared\""),
        "the canonical document names the phase: {text}"
    );

    // An unknown phase is rejected rather than interpreted as a nearby state.
    assert_eq!(
        failure_code(Journal::parse(
            text.replace("\"phase\":\"prepared\"", "\"phase\":\"teleported\"")
                .as_bytes(),
            &paths
        )),
        FailureCode::InvalidInput
    );
    // An unknown journal schema is rejected.
    assert_eq!(
        failure_code(Journal::parse(
            text.replace("\"schema\":1", "\"schema\":2").as_bytes(),
            &paths
        )),
        FailureCode::InvalidInput
    );
    // Manifest bytes that no longer match their digest are rejected.
    let mut tampered = text.clone().into_bytes();
    let start = text
        .find("\"bytes_hex\":\"")
        .expect("the document records manifest bytes")
        + "\"bytes_hex\":\"".len();
    tampered[start] = if tampered[start] == b'0' { b'1' } else { b'0' };
    assert_eq!(
        failure_code(Journal::parse(&tampered, &paths)),
        FailureCode::InvalidInput
    );

    // An empty journal is "no transaction"; an uninterpretable one is a distinct
    // slot that recovery reconciles instead of failing forever.
    fs::write(paths.journal_path(), b"").expect("empty journal");
    assert!(matches!(
        Journal::load(&platform, &paths).expect("an empty journal is not a document"),
        JournalState::None
    ));
    fs::write(paths.journal_path(), b"{\"schema\":1}\n").expect("unreadable journal");
    assert!(matches!(
        Journal::load(&platform, &paths).expect("a torn journal is still readable bytes"),
        JournalState::Unreadable
    ));
}

#[test]
fn registry_publish_failure_restores_preimage() {
    // A first install whose manifest publication fails removes only the assets
    // it created.
    {
        let fixture = Fixture::new(Scenario::Fresh);
        let unrelated = fixture.seed_unrelated_assets("seed unrelated assets");
        let sequence = clean_mutations(Scenario::Fresh);
        let publish = position_of(&sequence, PlatformCall::PublishManifest) + 1;
        let platform = fixture.platform_with(Faults {
            fail_mutation_at: Some(publish),
            ..Faults::default()
        });
        let failure = install(&platform, &fixture.paths, fixture.package.clone())
            .expect_err("a failed manifest publication fails the install");
        assert_eq!(
            failure.code,
            FailureCode::Io,
            "the injected publication failure is reported unchanged"
        );
        assert!(
            !failure.prior_usable,
            "a failed first install leaves no usable previous installation"
        );
        assert!(
            fixture.registration().is_none(),
            "the failed first install removes the registration it created"
        );
        assert!(
            !fixture.new_version_dir.exists(),
            "the failed first install removes its newly staged version"
        );
        assert!(
            fixture.receipt().is_none(),
            "no receipt is committed for a failed first install"
        );
        fixture.assert_journal_closed("failed first install");
        fixture.assert_unrelated_assets(&unrelated, "failed first install");

        // Recovery after the documented failure is a no-op, not a repair.
        let platform = fixture.platform();
        let report = recover(&platform, &fixture.paths).expect("nothing is pending");
        assert_eq!(report.state, STATE_CLEAN);
        assert_eq!(report.active_payload, None);
        assert!(
            platform.mutations().is_empty(),
            "a settled installation needs no recovery mutation: {:?}",
            platform.mutations()
        );
    }

    // An upgrade whose manifest publication fails restores the exact previous
    // manifest bytes.
    {
        let fixture = Fixture::new(Scenario::Upgrade);
        let preimage = fixture.registration().expect("previous registration bytes");
        let sequence = clean_mutations(Scenario::Upgrade);
        let publish = position_of(&sequence, PlatformCall::PublishManifest) + 1;
        let platform = fixture.platform_with(Faults {
            fail_mutation_at: Some(publish),
            ..Faults::default()
        });
        let failure = install(&platform, &fixture.paths, fixture.package.clone())
            .expect_err("a failed manifest publication fails the upgrade");
        assert_eq!(failure.code, FailureCode::Io);
        assert!(
            failure.prior_usable,
            "the previous installation stays complete and usable"
        );
        assert_eq!(
            fixture.registration().expect("registration restored"),
            preimage,
            "the previous manifest bytes are restored exactly"
        );
        fixture.assert_previous_complete("failed upgrade");
        assert!(
            !fixture.new_version_dir.exists(),
            "the failed upgrade removes only its newly staged version"
        );
        assert_ne!(
            fixture
                .receipt()
                .expect("the previous receipt survives")
                .payload_id,
            fixture.payload_id(),
            "the failed upgrade does not commit a receipt for the new payload"
        );
        fixture.assert_journal_closed("failed upgrade");

        let platform = fixture.platform();
        let report = recover(&platform, &fixture.paths).expect("nothing is pending");
        assert_eq!(report.state, STATE_CLEAN);
        assert_eq!(report.active_payload, fixture.prior_payload_id);
    }

    // The real partial-publication shape: the manifest was already replaced when
    // the separate registry publication failed. The upgrade must still restore
    // the old manifest bytes, and the previous version must stay usable.
    {
        let fixture = Fixture::new(Scenario::Upgrade);
        let preimage = fixture.registration().expect("previous registration bytes");
        let sequence = clean_mutations(Scenario::Upgrade);
        let registry = position_of(&sequence, PlatformCall::PublishRegistration) + 1;
        let platform = fixture.platform_with(Faults {
            fail_mutation_at: Some(registry),
            ..Faults::default()
        });
        let failure = install(&platform, &fixture.paths, fixture.package.clone())
            .expect_err("a failed registry publication fails the upgrade");
        assert_eq!(failure.code, FailureCode::Io);
        assert!(
            failure.prior_usable,
            "the previous installation stays complete and usable"
        );
        // The failure genuinely happened after the manifest rename: the forward
        // publication ran, then rollback republished both halves.
        let mutations = platform.mutations();
        assert!(
            mutations
                .iter()
                .filter(|call| **call == PlatformCall::PublishManifest)
                .count()
                >= 2,
            "the manifest was replaced before the registry failure: {mutations:?}"
        );
        assert_eq!(
            fixture.registration().expect("registration restored"),
            preimage,
            "the partial publication is rolled back to the exact previous bytes"
        );
        fixture.assert_previous_complete("partial publication");
        assert!(
            !fixture.new_version_dir.exists(),
            "the failed upgrade removes only its newly staged version"
        );
        assert_ne!(
            fixture
                .receipt()
                .expect("the previous receipt survives")
                .payload_id,
            fixture.payload_id()
        );
        fixture.assert_journal_closed("partial publication");
    }

    // An interrupted activation whose staged payload no longer verifies is
    // restored from the journaled preimage instead of being claimed complete.
    {
        let fixture = Fixture::new(Scenario::Upgrade);
        let preimage = fixture.registration().expect("previous registration bytes");
        let sequence = clean_mutations(Scenario::Upgrade);
        let registry = position_of(&sequence, PlatformCall::PublishRegistration) + 1;
        let platform = fixture.platform_with(Faults {
            panic_at_mutation: Some(registry),
            ..Faults::default()
        });
        let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
            install(&platform, &fixture.paths, fixture.package.clone())
        }));
        assert!(
            outcome.is_err(),
            "the death is injected after the manifest was activated but before the registry step"
        );
        let live = parse_manifest(
            &fs::read(fixture.paths.registration_path()).expect("the new manifest is live"),
        )
        .expect("the activated manifest is complete JSON");
        assert_eq!(
            fs::read(live.executable().expect("absolute executable")).expect("live executable"),
            HOST_V2,
            "the activation completed before the injected death"
        );
        // The journal records a complete staged payload; the disk no longer
        // agrees. Recovery must not infer success from the journal marker.
        fs::remove_file(fixture.new_version_dir.join("licenses/NOTICE"))
            .expect("damage the staged payload");

        let platform = fixture.platform();
        let report = recover(&platform, &fixture.paths).expect("recovery restores the preimage");
        assert_eq!(report.state, STATE_RECOVERED_PREVIOUS);
        assert_eq!(
            report.active_payload, fixture.prior_payload_id,
            "the previous payload is active again"
        );
        assert_eq!(
            fixture.registration().expect("registration restored"),
            preimage,
            "the journaled preimage is restored byte-for-byte"
        );
        fixture.assert_previous_complete("restored preimage");
        assert!(
            !fixture.new_version_dir.exists(),
            "the unverifiable staged version is removed"
        );
        fixture.assert_journal_closed("restored preimage");
    }
}

/// 1-based mutation index of the first call of `call` after `after` (0-based).
fn mutation_after(mutations: &[PlatformCall], after: usize, call: PlatformCall) -> usize {
    mutations[after..]
        .iter()
        .position(|candidate| *candidate == call)
        .map(|offset| after + offset + 1)
        .unwrap_or_else(|| {
            panic!("the clean transaction crosses {call:?} after {after}: {mutations:?}")
        })
}

/// Whether an install failure is the documented post-commit outcome.
fn assert_committed_or_cleanup_pending<T: std::fmt::Debug>(
    result: &Result<T, veles_bridge_installer::InstallFailure>,
    context: &str,
) {
    if let Err(failure) = result {
        assert_eq!(
            failure.code,
            FailureCode::CleanupPending,
            "{context}: a committed install only ever reports cleanup-pending, never a rollback: {failure:?}"
        );
        assert!(
            failure.prior_usable,
            "{context}: a committed install never reports prior_usable == false: {failure:?}"
        );
    }
}

#[test]
fn committed_install_is_never_rolled_back_by_post_receipt_errors() {
    // Error injection (not process death) at every post-receipt boundary: the
    // journal phase writes and the journal release. The receipt is the commit
    // point, so the install must finish forward and never restore the previous
    // version.
    for scenario in [Scenario::Fresh, Scenario::Upgrade] {
        let sequence = clean_mutations(scenario);
        let registry = position_of(&sequence, PlatformCall::PublishRegistration);
        // After the registry mutation: registration-published journal, receipt,
        // receipt-committed journal, cleanup-pending journal, journal release.
        // Offsets are 1-based mutation indices relative to the 0-based registry
        // position, so 4..=6 are the post-commit boundaries.
        for offset in [4usize, 5, 6] {
            let fixture = Fixture::new(scenario);
            let context = format!(
                "{}: error at post-receipt boundary {offset}",
                scenario.label()
            );
            let platform = fixture.platform_with(Faults {
                fail_mutation_at: Some(registry + offset),
                ..Faults::default()
            });
            let result = install(&platform, &fixture.paths, fixture.package.clone());
            assert_committed_or_cleanup_pending(&result, &context);

            // Either outcome leaves the new payload live and the receipt naming
            // it; the previous version is retained for an upgrade.
            let receipt = fixture
                .receipt()
                .unwrap_or_else(|| panic!("{context}: the committed receipt is durable"));
            assert_eq!(receipt.payload_id, fixture.payload_id(), "{context}");
            assert_eq!(
                receipt.manifest_sha256,
                Digest::of(&fixture.registration().expect("the new manifest is live")),
                "{context}: the receipt names the live manifest bytes"
            );
            fixture.assert_new_complete(&context);
            if scenario == Scenario::Upgrade {
                fixture.assert_previous_complete(&context);
            }

            // A following recovery converges (completes or is a no-op).
            let platform = fixture.platform();
            let report = recover(&platform, &fixture.paths)
                .unwrap_or_else(|failure| panic!("{context}: recovery failed: {failure:?}"));
            assert!(
                report.state == STATE_CLEAN
                    || report.state == STATE_RECOVERED_ACTIVE
                    || report.state == STATE_RECOVERED_PREVIOUS,
                "{context}: recovery converges: {report:?}"
            );
            fixture.assert_journal_closed(&context);
        }

        // The receipt write itself is still before the commit point (the fault
        // happens before the write): the previous state is restored.
        let fixture = Fixture::new(scenario);
        let context = format!("{}: error at the receipt write", scenario.label());
        let previous_registration = fixture.registration();
        let platform = fixture.platform_with(Faults {
            fail_mutation_at: Some(registry + 3),
            ..Faults::default()
        });
        let failure = install(&platform, &fixture.paths, fixture.package.clone())
            .expect_err("a failed receipt write is a pre-commit failure");
        assert_eq!(failure.code, FailureCode::Io, "{context}");
        match scenario {
            Scenario::Fresh => {
                assert_eq!(
                    fixture.receipt(),
                    None,
                    "{context}: no receipt was committed"
                );
                assert_eq!(
                    fixture.registration(),
                    None,
                    "{context}: a first install rolls back"
                );
            }
            Scenario::Upgrade => {
                assert_ne!(
                    fixture
                        .receipt()
                        .expect("previous receipt survives")
                        .payload_id,
                    fixture.payload_id(),
                    "{context}: no receipt for the new payload"
                );
                assert_eq!(
                    fixture.registration(),
                    previous_registration,
                    "{context}: the previous manifest is restored"
                );
                fixture.assert_previous_complete(&context);
            }
        }
        fixture.assert_journal_closed(&context);
    }
}

#[test]
fn durable_replacement_whose_flush_fails_is_treated_as_committed() {
    // The state write replaced the document but reported an error: the receipt is
    // durable, so the install must finish forward, never roll back.
    for index in [5usize, 8] {
        // 5 = receipt write, 8 = journal release on a fresh transaction.
        let fixture = Fixture::new(Scenario::Fresh);
        let context = format!("state write {index} fails after its rename");
        let platform = fixture.platform_with(Faults {
            fail_flush_after_state_write_at: Some(index),
            ..Faults::default()
        });
        let result = install(&platform, &fixture.paths, fixture.package.clone());
        assert_committed_or_cleanup_pending(&result, &context);
        let receipt = fixture
            .receipt()
            .unwrap_or_else(|| panic!("{context}: the receipt is durable"));
        assert_eq!(receipt.payload_id, fixture.payload_id(), "{context}");
        assert_eq!(
            fixture.registration().expect("the new manifest is live"),
            manifest_bytes(
                &fixture.paths,
                &fixture.new_version_dir.join("host.exe"),
                "Veles bridge native messaging host"
            ),
            "{context}: the new registration was never rolled back"
        );

        let platform = fixture.platform();
        let report = recover(&platform, &fixture.paths)
            .unwrap_or_else(|failure| panic!("{context}: recovery failed: {failure:?}"));
        assert_eq!(
            report.active_payload,
            Some(fixture.payload_id()),
            "{context}: the committed payload stays active"
        );
        fixture.assert_journal_closed(&context);
    }
}

#[test]
fn tear_mid_payload_write_converges_and_second_run_succeeds() {
    for scenario in [Scenario::Fresh, Scenario::Upgrade] {
        let fixture = Fixture::new(scenario);
        let context = format!("{}: torn payload write", scenario.label());
        let sequence = clean_mutations(scenario);
        // The first payload write: the version directory was created before it.
        let host_write = position_of(&sequence, PlatformCall::WriteDurable) + 1;
        let platform = fixture.platform_with(Faults {
            tear_at_mutation: Some(host_write),
            ..Faults::default()
        });
        let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
            install(&platform, &fixture.paths, fixture.package.clone())
        }));
        assert!(outcome.is_err(), "{context}: the injected death interrupts");
        // The staged file is a short prefix of the authenticated bytes.
        let staged = fixture.new_version_dir.join("host.exe");
        let length = fs::metadata(&staged)
            .unwrap_or_else(|_| panic!("{context}: the partial payload exists"))
            .len();
        assert!(
            length > 0 && length < HOST_V2.len() as u64,
            "{context}: the file is short ({length} of {})",
            HOST_V2.len()
        );
        assert!(matches!(fixture.journal_state(), JournalState::Pending(_)));

        let platform = fixture.platform();
        let report = recover(&platform, &fixture.paths)
            .unwrap_or_else(|failure| panic!("{context}: recovery failed: {failure:?}"));
        if scenario == Scenario::Upgrade {
            assert_eq!(report.state, STATE_RECOVERED_PREVIOUS, "{context}");
            fixture.assert_previous_complete(&context);
        } else {
            assert_eq!(report.state, STATE_CLEAN, "{context}");
        }
        assert!(
            !fixture.new_version_dir.exists(),
            "{context}: the short staging write is removable"
        );
        fixture.assert_journal_closed(&context);

        // A second run converges instead of blocking forever.
        let platform = fixture.platform();
        let report = install(&platform, &fixture.paths, fixture.package.clone())
            .unwrap_or_else(|failure| panic!("{context}: second install failed: {failure:?}"));
        assert_eq!(report.state, STATE_INSTALLED, "{context}");
        fixture.assert_journal_closed(&context);
    }
}

#[test]
fn tear_mid_state_write_keeps_a_complete_previous_journal() {
    let fixture = Fixture::new(Scenario::Upgrade);
    let sequence = clean_mutations(Scenario::Upgrade);
    // Tear the journal at the Staged phase write: staging is complete, but the
    // journal a reader sees is still the complete Prepared document.
    let staged_write = mutation_after(
        &sequence,
        sequence
            .iter()
            .rposition(|call| *call == PlatformCall::WriteDurable)
            .expect("payload writes"),
        PlatformCall::WriteStateDocument,
    );
    let platform = fixture.platform_with(Faults {
        tear_at_mutation: Some(staged_write),
        ..Faults::default()
    });
    let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
        install(&platform, &fixture.paths, fixture.package.clone())
    }));
    assert!(outcome.is_err());
    let JournalState::Pending(journal) = fixture.journal_state() else {
        panic!("the previous complete journal is still readable");
    };
    assert_eq!(
        journal.phase,
        Phase::Prepared,
        "the torn staging sibling never replaced the live journal"
    );
    assert!(
        journal
            .created
            .iter()
            .all(|claim| claim.incomplete
                || claim.kind == veles_bridge_installer::OwnedKind::Directory),
        "a Prepared journal marks file staging incomplete"
    );

    let platform = fixture.platform();
    let report = recover(&platform, &fixture.paths).expect("recovery converges");
    assert_eq!(report.state, STATE_RECOVERED_PREVIOUS);
    assert!(!fixture.new_version_dir.exists());
    fixture.assert_journal_closed("torn state write");

    let platform = fixture.platform();
    install(&platform, &fixture.paths, fixture.package.clone())
        .expect("the next run converges after recovery");
}

#[test]
fn torn_journal_is_reconciled_quarantined_and_lets_the_next_run_start() {
    // A fresh layout: the torn journal cannot be interpreted, and verified live
    // state is empty, so recovery quarantines it and reports a clean layout.
    let fixture = Fixture::new(Scenario::Fresh);
    let platform = fixture.platform();
    let lock = platform.lock(&fixture.paths).expect("lock");
    drop(lock);
    let torn = b"{\"schema\":1,\"transaction_id\":\"0123456789abcdef";
    fs::write(fixture.paths.journal_path(), torn).expect("torn journal");
    assert!(matches!(
        Journal::load(&PortablePlatform::new(), &fixture.paths).expect("bytes readable"),
        JournalState::Unreadable
    ));

    // An install refuses to stack a transaction on top of it (only the lock).
    let before = snapshot(fixture.temp.path());
    let refused = fixture.platform();
    assert_eq!(
        failure_code(install(&refused, &fixture.paths, fixture.package.clone())),
        FailureCode::RecoveryRequired
    );
    assert!(
        refused.mutations().is_empty(),
        "a pending (here torn) journal blocks the install before mutation: {:?}",
        refused.mutations()
    );
    assert_eq!(
        snapshot(fixture.temp.path()),
        before,
        "a refusal mutates nothing"
    );

    let report = recover(&fixture.platform(), &fixture.paths)
        .expect("a torn journal with unambiguous live state is reconciled");
    assert_eq!(report.state, STATE_CLEAN);
    assert!(matches!(fixture.journal_state(), JournalState::None));
    assert!(
        fixture
            .paths
            .state()
            .join(StateDocument::Journal.quarantine_file_name())
            .exists(),
        "the uninterpretable bytes are preserved in quarantine"
    );
    install(&fixture.platform(), &fixture.paths, fixture.package.clone())
        .expect("the next run starts after quarantine");
}

#[test]
fn torn_receipt_of_an_upgrade_is_not_rebuilt_from_an_unverifiable_journal() {
    // The previous receipt carries the policy the committed installation
    // enforced. With it unreadable, the journal's composed policy cannot be
    // re-derived from authenticated inputs, so recovery fails closed instead of
    // rebuilding a receipt on the journal's say-so (it used to).
    let fixture = Fixture::new(Scenario::Upgrade);
    let sequence = clean_mutations(Scenario::Upgrade);
    let registry = position_of(&sequence, PlatformCall::PublishRegistration) + 1;
    let platform = fixture.platform_with(Faults {
        panic_at_mutation: Some(registry),
        ..Faults::default()
    });
    let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
        install(&platform, &fixture.paths, fixture.package.clone())
    }));
    assert!(outcome.is_err(), "the manifest was activated");
    fs::write(fixture.paths.receipt_path(), b"{\"schema\":1,\"transac").expect("torn receipt");
    let before = snapshot(fixture.temp.path());

    let platform = fixture.platform();
    assert_eq!(
        failure_code(recover(&platform, &fixture.paths)),
        FailureCode::RecoveryRequired
    );
    assert!(platform.mutations().is_empty());
    assert_eq!(snapshot(fixture.temp.path()), before);
    assert!(matches!(fixture.journal_state(), JournalState::Pending(_)));
}

#[test]
fn torn_receipt_of_a_first_install_is_quarantined_and_rebuilt_from_the_pending_journal() {
    // A first install has no previous policy to compose against: the
    // authenticated descriptor's own policy is the expectation, so the pending
    // journal can be completed and the damaged receipt preserved.
    let fixture = Fixture::new(Scenario::Fresh);
    let sequence = clean_mutations(Scenario::Fresh);
    let registry = position_of(&sequence, PlatformCall::PublishRegistration) + 1;
    let platform = fixture.platform_with(Faults {
        panic_at_mutation: Some(registry),
        ..Faults::default()
    });
    let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
        install(&platform, &fixture.paths, fixture.package.clone())
    }));
    assert!(outcome.is_err(), "the manifest was activated");
    fs::write(fixture.paths.receipt_path(), b"{\"schema\":1,\"transac").expect("torn receipt");

    let platform = fixture.platform();
    let report = recover(&platform, &fixture.paths)
        .expect("the authenticated staged release completes the first install");
    assert_eq!(report.state, STATE_RECOVERED_ACTIVE);
    let receipt = fixture.receipt().expect("the receipt was rebuilt");
    assert_eq!(receipt.payload_id, fixture.payload_id());
    assert!(
        fixture
            .paths
            .state()
            .join(StateDocument::Receipt.quarantine_file_name())
            .exists(),
        "the torn receipt bytes are preserved"
    );
    fixture.assert_journal_closed("torn receipt");
}

#[test]
fn a_receipt_naming_an_unknown_operation_is_not_rebuilt_over_in_a_first_install() {
    // The first-install rebuild path may replace a *torn* receipt. A receipt that
    // parses as JSON but declares an operation this engine does not know is a
    // newer engine's document, not a torn one, and must survive byte for byte.
    let fixture = Fixture::new(Scenario::Fresh);
    let sequence = clean_mutations(Scenario::Fresh);
    let registry = position_of(&sequence, PlatformCall::PublishRegistration) + 1;
    let platform = fixture.platform_with(Faults {
        panic_at_mutation: Some(registry),
        ..Faults::default()
    });
    let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
        install(&platform, &fixture.paths, fixture.package.clone())
    }));
    assert!(outcome.is_err(), "the manifest was activated");
    let future =
        b"{\"schema\":1,\"operation\":\"sealing\",\"transaction_id\":\"from a newer engine\"}";
    fs::write(fixture.paths.receipt_path(), future).expect("future receipt");
    let before = snapshot(fixture.temp.path());

    let platform = fixture.platform();
    assert_eq!(
        failure_code(recover(&platform, &fixture.paths)),
        FailureCode::UnsupportedState
    );
    assert!(platform.mutations().is_empty());
    assert_eq!(snapshot(fixture.temp.path()), before, "nothing was touched");
    assert_eq!(
        fs::read(fixture.paths.receipt_path()).expect("receipt kept"),
        future
    );
    assert!(matches!(fixture.journal_state(), JournalState::Pending(_)));
    assert!(
        !fixture
            .paths
            .state()
            .join(StateDocument::Receipt.quarantine_file_name())
            .exists()
    );
}

#[test]
fn crash_during_recover_plus_second_recovery_converges() {
    let fixture = Fixture::new(Scenario::Upgrade);
    let sequence = clean_mutations(Scenario::Upgrade);
    let publish = position_of(&sequence, PlatformCall::PublishManifest) + 1;
    let platform = fixture.platform_with(Faults {
        panic_at_mutation: Some(publish),
        ..Faults::default()
    });
    let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
        install(&platform, &fixture.paths, fixture.package.clone())
    }));
    assert!(outcome.is_err(), "the install died before activation");

    // A valid pending journal blocks another install before any mutation.
    let refused = fixture.platform();
    assert_eq!(
        failure_code(install(&refused, &fixture.paths, fixture.package.clone())),
        FailureCode::RecoveryRequired,
        "an install refuses to stack on a pending transaction"
    );
    assert!(
        refused.mutations().is_empty(),
        "the refusal happens before mutation: {:?}",
        refused.mutations()
    );

    // The first recovery dies before its first removal; the pending journal is
    // unchanged and the second recovery completes.
    let crashing = fixture.platform_with(Faults {
        panic_at_mutation: Some(1),
        ..Faults::default()
    });
    let outcome = panic::catch_unwind(AssertUnwindSafe(|| recover(&crashing, &fixture.paths)));
    assert!(outcome.is_err(), "the recovery itself was interrupted");
    assert!(matches!(fixture.journal_state(), JournalState::Pending(_)));

    let platform = fixture.platform();
    let report = recover(&platform, &fixture.paths).expect("the second recovery converges");
    assert_eq!(report.state, STATE_RECOVERED_PREVIOUS);
    fixture.assert_previous_complete("second recovery");
    assert!(!fixture.new_version_dir.exists());
    fixture.assert_journal_closed("second recovery");

    install(&fixture.platform(), &fixture.paths, fixture.package.clone())
        .expect("the install succeeds after recovery");
}

#[test]
fn preimage_with_a_missing_executable_is_not_published() {
    let fixture = Fixture::new(Scenario::Upgrade);
    let missing = fixture
        .paths
        .versions()
        .join("1.0.0-deadbeef")
        .join("host.exe");
    let old_manifest = manifest_bytes(&fixture.paths, &missing, "missing preimage");
    let mut journal = fixture.crafted_journal(&old_manifest);
    // The journaled preimage names a manifest whose executable is gone.
    fs::write(
        fixture.paths.journal_path(),
        journal.to_bytes().expect("bounded journal"),
    )
    .expect("plant journal");
    // The live registration is absent so recovery takes the restore path.
    fs::remove_file(fixture.paths.registration_path()).expect("remove live registration");
    journal.old_manifest = Some(ManifestSnapshot::new(old_manifest).expect("bounded"));

    let platform = fixture.platform();
    assert_eq!(
        failure_code(recover(&platform, &fixture.paths)),
        FailureCode::RecoveryRequired,
        "a preimage that cannot work is never published"
    );
    assert!(
        platform.mutations().is_empty(),
        "the refusal mutates nothing: {:?}",
        platform.mutations()
    );
    assert!(
        matches!(fixture.journal_state(), JournalState::Pending(_)),
        "the journal is retained for repair guidance"
    );
}

#[test]
fn windows_preimage_round_trips_through_the_platform_boundary() {
    let fixture = Fixture::new(Scenario::Upgrade);
    let old_bytes = fixture.registration().expect("previous registration bytes");
    let journal = fixture.crafted_journal_with_windows(
        &old_bytes,
        Some(("Software\\Veles\\Host", Some("old-value"))),
    );
    fs::write(
        fixture.paths.journal_path(),
        journal.to_bytes().expect("bounded journal"),
    )
    .expect("plant journal");
    // The new manifest was activated, then the transaction died; recovery must
    // restore the journaled preimage, carrying the HKCU record into the platform
    // call rather than writing registry values itself.
    fs::write(fixture.paths.registration_path(), journal.manifest.bytes())
        .expect("new manifest live");

    let platform = fixture.platform();
    let report = recover(&platform, &fixture.paths).expect("recovery restores the preimage");
    assert_eq!(report.state, STATE_RECOVERED_PREVIOUS);
    assert_eq!(
        fixture.registration().expect("old manifest restored"),
        old_bytes
    );
    let preimages = platform.published_preimages();
    let last = preimages.last().expect("publication was called");
    let expected = WindowsPreimage::new(
        "Software\\Veles\\Host".to_owned(),
        Some("old-value".to_owned()),
    )
    .expect("bounded preimage");
    assert_eq!(
        last.as_ref(),
        Some(&expected),
        "the journaled Windows preimage is republished through the platform call"
    );
    fixture.assert_journal_closed("windows preimage");
}

#[test]
fn short_write_limit_still_produces_complete_state_and_payloads() {
    let fixture = Fixture::new(Scenario::Upgrade);
    let platform = fixture.platform_with(Faults {
        short_write_limit: Some(1),
        ..Faults::default()
    });
    let report = install(&platform, &fixture.paths, fixture.package.clone())
        .expect("short writes are retried, never truncating a document");
    assert_eq!(report.state, STATE_INSTALLED);
    assert!(
        platform.short_write_count() > 0,
        "the fixture actually exercised short writes"
    );
    fixture.assert_new_complete("short writes");
    let receipt = fixture.receipt().expect("a complete receipt is durable");
    assert_eq!(receipt.payload_id, fixture.payload_id());
    fixture.assert_journal_closed("short writes");
    let report = recover(&fixture.platform(), &fixture.paths).expect("nothing is pending");
    assert_eq!(report.state, STATE_CLEAN);
}

#[test]
fn state_read_faults_are_modelable_through_the_boundary() {
    let fixture = Fixture::new(Scenario::Fresh);
    let platform = FakePlatform::with_faults(Faults {
        fail_state_read_at: Some(1),
        ..Faults::default()
    });
    assert_eq!(
        failure_code(Journal::load(&platform, &fixture.paths)),
        FailureCode::Io,
        "a state read fault surfaces as an IO failure"
    );
}

// ---------------------------------------------------------------------------
// F1: recovery authenticates what it completes. F2: damage is never "usable".
// ---------------------------------------------------------------------------

/// Crash right after the registry publication, before the receipt write: the new
/// manifest is live, the journal says `registration-published` and nothing has
/// been committed. This is the state recovery must NOT complete on the strength
/// of the journal alone.
fn crash_before_receipt(scenario: Scenario) -> Fixture {
    let registry = position_of(
        &clean_mutations(scenario),
        PlatformCall::PublishRegistration,
    );
    let fixture = Fixture::new(scenario);
    let platform = fixture.platform_with(Faults {
        panic_at_mutation: Some(registry + 3),
        ..Faults::default()
    });
    let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
        install(&platform, &fixture.paths, fixture.package.clone())
    }));
    assert!(
        outcome.is_err(),
        "the injected death interrupts the install"
    );
    assert!(
        matches!(fixture.journal_state(), JournalState::Pending(_)),
        "the journal is pending"
    );
    assert_ne!(
        fixture.receipt().map(|receipt| receipt.payload_id),
        Some(fixture.payload_id()),
        "the new release is not committed"
    );
    fixture
}

#[test]
fn recovery_preserves_an_os_denial_of_the_staged_release() {
    for scenario in [Scenario::Fresh, Scenario::Upgrade] {
        let fixture = crash_before_receipt(scenario);
        let live_before = fixture.registration();
        let before = snapshot(fixture.temp.path());

        // The OS no longer accepts the staged carrier: nothing may be completed.
        let denied = fixture.platform_with(Faults {
            trust_denied: true,
            ..Faults::default()
        });
        assert_eq!(
            failure_code(recover(&denied, &fixture.paths)),
            FailureCode::TrustDenied,
            "{}: a denial is preserved, not completed over",
            scenario.label()
        );

        // The assessment cannot be formed (offline revocation data): same rule.
        let unavailable = FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            ..Faults::default()
        });
        assert_eq!(
            failure_code(recover(&unavailable, &fixture.paths)),
            FailureCode::TrustUnavailable,
            "{}",
            scenario.label()
        );

        assert!(denied.mutations().is_empty(), "{}", scenario.label());
        assert!(unavailable.mutations().is_empty(), "{}", scenario.label());
        assert_eq!(fixture.registration(), live_before, "{}", scenario.label());
        assert_eq!(
            snapshot(fixture.temp.path()),
            before,
            "{}: a refused recovery changes nothing on disk",
            scenario.label()
        );
        assert!(matches!(fixture.journal_state(), JournalState::Pending(_)));
    }
}

#[test]
fn recovery_does_not_complete_a_release_the_os_did_not_authenticate_for_this_journal() {
    let fixture = crash_before_receipt(Scenario::Upgrade);

    // A genuinely signed carrier - of a different release - is not evidence for
    // this journal's payload.
    let other = build_package(
        &fixture.temp,
        "other",
        "9.0.0",
        b"other host",
        b"other notice",
    );
    let before = snapshot(fixture.temp.path());
    let platform = FakePlatform::with_faults(Faults {
        host_state: HostState::Quiescent,
        trust: Some(other.proof.clone()),
        ..Faults::default()
    });
    assert_eq!(
        failure_code(recover(&platform, &fixture.paths)),
        FailureCode::TrustDenied
    );
    assert!(platform.mutations().is_empty());
    let after = snapshot(fixture.temp.path());
    let changed: Vec<&String> = after
        .keys()
        .chain(before.keys())
        .filter(|key| after.get(*key) != before.get(*key))
        .collect();
    assert!(
        changed.is_empty(),
        "a refused recovery changed: {changed:?}"
    );
}

#[test]
fn recovery_rederives_the_effective_policy_instead_of_trusting_the_journal() {
    let fixture = crash_before_receipt(Scenario::Fresh);
    let platform = fixture.platform();
    let JournalState::Pending(journal) = fixture.journal_state() else {
        panic!("pending journal");
    };
    // A journal that claims a wider publisher set than the authenticated
    // descriptor's policy: self-consistent, but not what the signature covers.
    let mut forged = (*journal).clone();
    forged
        .release_policy
        .publishers
        .push("Attacker Publisher".to_owned());
    forged
        .write(&platform, &fixture.paths)
        .expect("forged journal written");

    let recovering = fixture.platform();
    let failure = recover(&recovering, &fixture.paths).expect_err("a forged policy is refused");
    assert_eq!(failure.code, FailureCode::RecoveryRequired);
    assert!(
        fixture.receipt().is_none(),
        "nothing was committed on the forged journal's authority"
    );
}

/// A crash after the receipt is durable but before the journal is released: the
/// receipt names the new release and the journal is still pending.
fn crash_after_receipt(scenario: Scenario) -> Fixture {
    let registry = position_of(
        &clean_mutations(scenario),
        PlatformCall::PublishRegistration,
    );
    let fixture = Fixture::new(scenario);
    let platform = fixture.platform_with(Faults {
        // Mutation registry+1 is the registration publication, +2 the journal phase,
        // +3 the receipt write, +4 the next journal phase write (this one dies).
        panic_at_mutation: Some(registry + 4),
        ..Faults::default()
    });
    let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
        install(&platform, &fixture.paths, fixture.package.clone())
    }));
    assert!(
        outcome.is_err(),
        "the injected death interrupts the install"
    );
    assert!(matches!(fixture.journal_state(), JournalState::Pending(_)));
    assert_eq!(
        fixture.receipt().map(|receipt| receipt.payload_id),
        Some(fixture.payload_id()),
        "the receipt for the new release is committed"
    );
    fixture
}

/// R1: after the receipt is committed, journal and receipt can be edited to agree
/// with each other while disagreeing with the signed policy. Comparing the journal
/// with the receipt proves two state documents agree; only the OS-authenticated
/// descriptor says what an install may enforce, and an install enforces exactly the
/// incoming descriptor's policy.
#[test]
fn recovery_after_the_receipt_rederives_the_install_policy_from_the_signed_descriptor() {
    type Widen = fn(&mut veles_bridge_installer::ReleasePolicy);
    let widenings: [(&str, Widen); 2] = [
        ("an added publisher", |policy| {
            policy.publishers.push("Attacker Publisher".to_owned());
        }),
        ("a widened rollback list", |policy| {
            policy.rollback_payloads.push(Digest::of(b"anything"));
        }),
    ];
    let mut wrong: Vec<String> = Vec::new();
    for scenario in [Scenario::Fresh, Scenario::Upgrade] {
        for (name, widen) in widenings {
            let context = format!("{}: {name}", scenario.label());
            let fixture = crash_after_receipt(scenario);
            let platform = fixture.platform();

            let JournalState::Pending(journal) = fixture.journal_state() else {
                panic!("pending journal");
            };
            let mut forged_journal = (*journal).clone();
            widen(&mut forged_journal.release_policy);
            forged_journal
                .write(&platform, &fixture.paths)
                .expect("forged journal");
            let mut forged_receipt = fixture.receipt().expect("committed receipt");
            widen(&mut forged_receipt.release_policy);
            forged_receipt
                .write(&platform, &fixture.paths)
                .expect("forged receipt");
            let before = snapshot(fixture.temp.path());

            let recovering = fixture.platform();
            match recover(&recovering, &fixture.paths) {
                Ok(report) => wrong.push(format!(
                    "{context}: completed a policy the signature does not cover: {}",
                    report.state
                )),
                Err(failure) => {
                    if failure.code != FailureCode::RecoveryRequired
                        || !recovering.mutations().is_empty()
                        || snapshot(fixture.temp.path()) != before
                        || !matches!(fixture.journal_state(), JournalState::Pending(_))
                    {
                        wrong.push(format!("{context}: refused wrongly: {failure:?}"));
                    }
                }
            }
        }
    }
    assert!(
        wrong.is_empty(),
        "unsupported policies were completed: {wrong:#?}"
    );
}

#[test]
fn recovery_reassesses_the_installed_host_before_completing() {
    let fixture = crash_before_receipt(Scenario::Fresh);
    let platform = fixture.platform_with(Faults {
        host: Some(HostFault::Denied),
        ..Faults::default()
    });
    assert_eq!(
        failure_code(recover(&platform, &fixture.paths)),
        FailureCode::TrustDenied
    );
    assert!(fixture.receipt().is_none());
}

#[test]
fn recover_without_a_journal_does_not_call_a_damaged_installation_usable() {
    let fixture = Fixture::new(Scenario::Fresh);
    install(&fixture.platform(), &fixture.paths, fixture.package.clone()).expect("installs");
    let healthy = recover(&fixture.platform(), &fixture.paths).expect("healthy layout");
    assert_eq!(healthy.state, STATE_CLEAN);
    assert!(healthy.prior_usable);

    // The selected host disappears; registration and receipt are intact.
    fs::remove_file(fixture.new_version_dir.join("host.exe")).expect("delete the host");
    let report = recover(&fixture.platform(), &fixture.paths).expect("a report, not a fault");
    assert_ne!(report.state, STATE_CLEAN, "{report:?}");
    assert!(!report.prior_usable, "{report:?}");
    assert_eq!(report.failure, Some(FailureCode::RepairPackageRequired));
}

#[test]
fn a_missing_dependency_is_damage_too() {
    let fixture = Fixture::new(Scenario::Fresh);
    install(&fixture.platform(), &fixture.paths, fixture.package.clone()).expect("installs");
    fs::remove_file(fixture.new_version_dir.join("licenses/NOTICE")).expect("delete a resource");
    let report = recover(&fixture.platform(), &fixture.paths).expect("a report");
    assert!(!report.prior_usable, "{report:?}");
    assert_ne!(report.state, STATE_CLEAN);
}

/// R2: a corrupt journal over a *committed* installation is reconciled only after the
/// installation is shown to be whole; a damaged one keeps its recovery intent
/// (the journal bytes) instead of being quarantined and then reported damaged.
#[test]
fn a_torn_journal_beside_a_damaged_installation_is_not_quarantined() {
    for damage in ["delete host", "alter host", "delete resource"] {
        let fixture = Fixture::new(Scenario::Fresh);
        install(&fixture.platform(), &fixture.paths, fixture.package.clone()).expect("installs");
        let version = fixture.new_version_dir.clone();
        match damage {
            "delete host" => fs::remove_file(version.join("host.exe")).expect("delete"),
            "alter host" => fs::write(version.join("host.exe"), b"tampered").expect("alter"),
            _ => fs::remove_file(version.join("licenses/NOTICE")).expect("delete"),
        }
        let torn = b"{\"schema\":1,\"transaction_id\":\"0123456789abcdef";
        fs::write(fixture.paths.journal_path(), torn).expect("torn journal");
        let before = snapshot(fixture.temp.path());

        let platform = fixture.platform();
        let failure = recover(&platform, &fixture.paths)
            .expect_err("the committed installation is damaged: recovery intent must be kept");
        assert_eq!(failure.code, FailureCode::RecoveryRequired, "{damage}");
        assert!(platform.mutations().is_empty(), "{damage}");
        assert_eq!(
            snapshot(fixture.temp.path()),
            before,
            "{damage}: nothing changed, including the journal bytes"
        );
        assert!(
            !fixture
                .paths
                .state()
                .join(StateDocument::Journal.quarantine_file_name())
                .exists(),
            "{damage}: the journal was not quarantined"
        );
    }
}

#[test]
fn a_torn_journal_beside_a_whole_installation_is_still_quarantined() {
    let fixture = Fixture::new(Scenario::Fresh);
    install(&fixture.platform(), &fixture.paths, fixture.package.clone()).expect("installs");
    fs::write(fixture.paths.journal_path(), b"{\"schema\":1,\"trans").expect("torn journal");
    let report = recover(&fixture.platform(), &fixture.paths).expect("whole installation");
    assert_eq!(report.state, STATE_CLEAN);
    assert!(report.prior_usable, "{report:?}");
    assert!(
        fixture
            .paths
            .state()
            .join(StateDocument::Journal.quarantine_file_name())
            .exists()
    );
}

#[test]
fn an_unverified_previous_release_never_clears_recovery_intent() {
    for damage in ["delete host", "alter host", "delete resource"] {
        let scenario = Scenario::Upgrade;
        let sequence = clean_mutations(scenario);
        let publish = position_of(&sequence, PlatformCall::PublishManifest);
        let fixture = Fixture::new(scenario);
        let platform = fixture.platform_with(Faults {
            // Dies at the manifest replacement: the OLD manifest is still live
            // and the journal is pending.
            panic_at_mutation: Some(publish + 1),
            ..Faults::default()
        });
        let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
            install(&platform, &fixture.paths, fixture.package.clone())
        }));
        assert!(outcome.is_err(), "{damage}");
        let previous = fixture.prior_version_dir.clone().expect("previous version");
        match damage {
            "delete host" => fs::remove_file(previous.join("host.exe")).expect("delete"),
            "alter host" => fs::write(previous.join("host.exe"), b"tampered").expect("alter"),
            _ => fs::remove_file(previous.join("licenses/NOTICE")).expect("delete"),
        }

        let recovering = fixture.platform();
        let failure = recover(&recovering, &fixture.paths)
            .expect_err("the previous release cannot be shown to work");
        assert_eq!(failure.code, FailureCode::RecoveryRequired, "{damage}");
        assert!(!failure.prior_usable, "{damage}");
        assert!(
            matches!(fixture.journal_state(), JournalState::Pending(_)),
            "{damage}: recovery intent is kept"
        );
    }
}
