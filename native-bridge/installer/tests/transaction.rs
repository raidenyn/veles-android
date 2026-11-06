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
    Channel, Digest, FailureCode, FakePlatform, Faults, HostState, InstallPaths, InventoryEntry,
    JOURNAL_SCHEMA_VERSION, Journal, ManifestSnapshot, NativeManifest, Operation, OwnedClaim,
    PRODUCTION_HOST_NAME, Phase, Platform, PlatformCall, PolicyCarrierRefs, Receipt,
    RegistrationPreimage, ReleaseDescriptor, ReleasePolicy, STATE_CLEAN, STATE_INSTALLED,
    STATE_RECOVERED_ACTIVE, STATE_RECOVERED_PREVIOUS, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
    Target, TrustProof, Version, authenticate_package, canonical_json, extension_origin, install,
    parse_manifest, path_is_within, recover, write_carrier, write_inventory,
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
        Receipt::load(&self.paths).expect("the committed receipt is readable")
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

        assert!(
            Journal::load(&self.paths)
                .expect("the journal is readable")
                .is_none(),
            "{context}: the transaction is closed after recovery"
        );
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
        let executable = self
            .prior_version_dir
            .as_ref()
            .expect("the upgrade fixture has a previous version")
            .join("host.exe");
        let next_manifest = manifest_bytes(&self.paths, &executable, "crashed transaction");
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
            manifest: ManifestSnapshot::new(next_manifest).expect("bounded manifest"),
            old_manifest: Some(ManifestSnapshot::new(preimage.to_vec()).expect("bounded manifest")),
            preimage: Some(RegistrationPreimage::new(Digest::of(preimage))),
            policy: PolicyCarrierRefs::new(
                Digest::of(b"descriptor"),
                Digest::of(b"carrier"),
                PUBLISHER,
                TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
                1,
            )
            .expect("bounded policy references"),
            created: vec![OwnedClaim::file(
                format!("versions/2.0.0-{}/host.exe", self.payload_id().to_hex()),
                Digest::of(HOST_V2),
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

#[test]
fn crash_at_every_durable_boundary() {
    for scenario in [Scenario::Fresh, Scenario::Upgrade] {
        let sequence = clean_mutations(scenario);
        let total = sequence.len();
        // Every durable step class the brief names must actually be crossed.
        for call in [
            PlatformCall::WriteDurable,
            PlatformCall::CreateDirectory,
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
                    attempt == 1
                        || Journal::load(&fixture.paths)
                            .expect("the journal is readable")
                            .is_some(),
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
/// manifest digest must bind its bytes, and an unreadable document fails closed.
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
        created: vec![
            OwnedClaim::directory(version_directory.clone()),
            OwnedClaim::file(format!("{version_directory}/host.exe"), Digest::of(b"host")),
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

    // An empty journal is "no transaction"; an unreadable one is not.
    fs::write(paths.journal_path(), b"").expect("empty journal");
    assert!(
        Journal::load(&paths)
            .expect("an empty journal is not a document")
            .is_none()
    );
    fs::write(paths.journal_path(), b"{\"schema\":1}\n").expect("unreadable journal");
    assert_eq!(
        failure_code(Journal::load(&paths)),
        FailureCode::RecoveryRequired,
        "an uninterpretable journal fails closed with recovery guidance"
    );
}

#[test]
fn registry_publish_failure_restores_preimage() {
    // A first install that cannot publish removes only the assets it created.
    {
        let fixture = Fixture::new(Scenario::Fresh);
        let unrelated = fixture.seed_unrelated_assets("seed unrelated assets");
        let sequence = clean_mutations(Scenario::Fresh);
        let publish = position_of(&sequence, PlatformCall::PublishRegistration) + 1;
        let platform = fixture.platform_with(Faults {
            fail_mutation_at: Some(publish),
            ..Faults::default()
        });
        let failure = install(&platform, &fixture.paths, fixture.package.clone())
            .expect_err("a failed registry publication fails the install");
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
        assert!(
            Journal::load(&fixture.paths)
                .expect("journal readable")
                .is_none(),
            "the rolled-back transaction releases its journal"
        );
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

    // An upgrade that cannot publish restores the exact previous manifest bytes.
    {
        let fixture = Fixture::new(Scenario::Upgrade);
        let preimage = fixture.registration().expect("previous registration bytes");
        let sequence = clean_mutations(Scenario::Upgrade);
        let publish = position_of(&sequence, PlatformCall::PublishRegistration) + 1;
        let platform = fixture.platform_with(Faults {
            fail_mutation_at: Some(publish),
            ..Faults::default()
        });
        let failure = install(&platform, &fixture.paths, fixture.package.clone())
            .expect_err("a failed registry publication fails the upgrade");
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
        assert!(
            Journal::load(&fixture.paths)
                .expect("journal readable")
                .is_none(),
            "the rolled-back transaction releases its journal"
        );

        let platform = fixture.platform();
        let report = recover(&platform, &fixture.paths).expect("nothing is pending");
        assert_eq!(report.state, STATE_CLEAN);
        assert_eq!(report.active_payload, fixture.prior_payload_id);
    }

    // An interrupted activation whose staged payload no longer verifies is
    // restored from the journaled preimage instead of being claimed complete.
    {
        let fixture = Fixture::new(Scenario::Upgrade);
        let preimage = fixture.registration().expect("previous registration bytes");
        let sequence = clean_mutations(Scenario::Upgrade);
        let publish = position_of(&sequence, PlatformCall::PublishRegistration) + 1;
        let platform = fixture.platform_with(Faults {
            panic_at_mutation: Some(publish + 1),
            ..Faults::default()
        });
        let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
            install(&platform, &fixture.paths, fixture.package.clone())
        }));
        assert!(
            outcome.is_err(),
            "the death is injected after the manifest was activated"
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
        assert!(
            Journal::load(&fixture.paths)
                .expect("journal readable")
                .is_none(),
            "the restored transaction releases its journal"
        );
    }
}
