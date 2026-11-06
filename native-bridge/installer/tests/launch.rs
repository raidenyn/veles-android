//! Launch permission and host-code assessment (fix round F5 + F6).
//!
//! F5: the authenticated inventory records which file is executable; staging must
//! carry that across the platform boundary, apply it through the verified
//! destination handle and prove it before the manifest can select the file.
//! F6: the OS signature / publisher assessment must cover the *host* executable
//! (or bundle) in its package form and again in its installed form, not only the
//! signed policy carrier.
//!
//! Portable evidence only. A passing test here never claims a native Authenticode
//! or Gatekeeper result: those adapters compile for the Windows and macOS targets
//! but cannot run on this host.

use std::fs;
#[cfg(unix)]
use std::path::Path;
use std::path::PathBuf;
use std::process;
use std::sync::atomic::{AtomicU64, Ordering};

#[cfg(unix)]
use veles_bridge_installer::STATE_REPAIRED;
use veles_bridge_installer::{
    Channel, Digest, FailureCode, FakePlatform, Faults, HostFault, HostState, InstallPaths,
    InventoryEntry, Operation, PRODUCTION_HOST_NAME, PlatformCall, ReleaseDescriptor,
    ReleasePolicy, STATE_ROLLED_BACK, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, Target, TrustProof,
    Version, authenticate_package, canonical_json, install, run_operation, write_carrier,
    write_inventory,
};

const EXTENSION_ID: &str = "mnopabcdefghijklmnopabcdefghijkl";
const PUBLISHER: &str = "Publisher One";

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct Temp(PathBuf);

impl Temp {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "veles-installer-launch-{}-{}",
            process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).expect("temp root");
        Self(path)
    }
}

impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

struct Package {
    root: PathBuf,
    descriptor: ReleaseDescriptor,
    proof: TrustProof,
}

fn build(temp: &Temp, name: &str) -> Package {
    build_release(temp, name, "1.0.0", 1, Vec::new(), false)
}

fn build_release(
    temp: &Temp,
    name: &str,
    version: &str,
    epoch: u64,
    mut rollback: Vec<Digest>,
    rollback_self: bool,
) -> Package {
    let root = temp.0.join("packages").join(name);
    let payload = root.join("payload");
    fs::create_dir_all(payload.join("licenses")).expect("payload tree");
    let host = format!("host {version}").into_bytes();
    fs::write(payload.join("host.exe"), &host).expect("host");
    fs::write(payload.join("licenses/NOTICE"), b"notice").expect("notice");
    let inventory = write_inventory(&[
        InventoryEntry {
            path: "host.exe".to_owned(),
            size: host.len() as u64,
            sha256: Digest::of(&host),
            executable: true,
        },
        InventoryEntry {
            path: "licenses/NOTICE".to_owned(),
            size: 6,
            sha256: Digest::of(b"notice"),
            executable: false,
        },
    ])
    .expect("inventory");
    fs::write(root.join("inventory.json"), &inventory).expect("inventory file");
    if rollback_self {
        rollback.push(Digest::of(&inventory));
    }
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
            rollback_payloads: rollback,
            epoch,
        },
    };
    let carrier = write_carrier(&canonical_json(&descriptor).expect("json")).expect("carrier");
    fs::write(root.join("carrier.exe"), &carrier).expect("carrier artifact");
    let proof =
        TrustProof::new(PUBLISHER, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, carrier).expect("proof");
    Package {
        root,
        descriptor,
        proof,
    }
}

fn paths(temp: &Temp) -> InstallPaths {
    let home = temp.0.join("home");
    let local = temp.0.join("local");
    InstallPaths::for_user(Target::WindowsX64, Channel::Production, &home, Some(&local))
        .expect("injected Windows roots")
}

fn platform(package: &Package, faults: Faults) -> FakePlatform {
    FakePlatform::with_faults(Faults {
        trust: Some(package.proof.clone()),
        host_state: HostState::Quiescent,
        ..faults
    })
}

fn version_dir(paths: &InstallPaths, package: &Package) -> PathBuf {
    paths.version_dir(
        &Version::parse(&package.descriptor.version).expect("version"),
        package.descriptor.payload_id,
    )
}

#[cfg(unix)]
fn mode(path: &Path) -> u32 {
    use std::os::unix::fs::PermissionsExt;

    fs::metadata(path).expect("metadata").permissions().mode() & 0o777
}

#[cfg(unix)]
#[test]
fn staged_executable_keeps_its_launch_permission() {
    let temp = Temp::new();
    let paths = paths(&temp);
    let package = build(&temp, "exec");
    let fake = platform(&package, Faults::default());
    let authenticated =
        authenticate_package(&fake, &package.root, &package.descriptor).expect("authenticates");
    install(&fake, &paths, authenticated).expect("installs");

    let version = version_dir(&paths, &package);
    assert_eq!(
        mode(&version.join("host.exe")),
        0o700,
        "the authenticated executable is launchable by its owner only"
    );
    assert_eq!(
        mode(&version.join("licenses/NOTICE")),
        0o600,
        "non-executable payload stays non-executable and owner-only"
    );
}

#[cfg(unix)]
#[test]
fn a_lost_launch_permission_is_refused_before_activation() {
    let temp = Temp::new();
    let paths = paths(&temp);
    let package = build(&temp, "no-exec");
    let good = platform(&package, Faults::default());
    let authenticated =
        authenticate_package(&good, &package.root, &package.descriptor).expect("authenticates");

    // A platform that failed to carry the executable bit must not get to publish.
    let broken = platform(
        &package,
        Faults {
            skip_launch_permissions: true,
            ..Faults::default()
        },
    );
    let failure = install(&broken, &paths, authenticated).expect_err("not launchable");
    assert_eq!(failure.code, FailureCode::Io);
    assert!(
        !broken.calls().contains(&PlatformCall::PublishManifest),
        "activation must not run for a host without its launch permission"
    );
    assert!(
        broken
            .calls()
            .contains(&PlatformCall::CheckLaunchPermissions),
        "the permission is re-verified immediately before activation"
    );
    assert!(!paths.registration_path().exists());
}

#[cfg(unix)]
#[test]
fn repair_restores_a_lost_launch_permission() {
    use std::os::unix::fs::PermissionsExt;

    let temp = Temp::new();
    let paths = paths(&temp);
    let package = build(&temp, "repair");
    let fake = platform(&package, Faults::default());
    install(
        &fake,
        &paths,
        authenticate_package(&fake, &package.root, &package.descriptor).expect("authenticates"),
    )
    .expect("installs");
    let host = version_dir(&paths, &package).join("host.exe");
    fs::set_permissions(&host, fs::Permissions::from_mode(0o600)).expect("strip the bit");

    let repair = platform(&package, Faults::default());
    let report = run_operation(
        &repair,
        &paths,
        Operation::Repair,
        Some(authenticate_package(&repair, &package.root, &package.descriptor).expect("again")),
    )
    .expect("repair succeeds");
    assert_eq!(report.state, STATE_REPAIRED);
    assert_eq!(mode(&host), 0o700);
}

fn denied_host(package: &Package, host: HostFault) -> FakePlatform {
    platform(
        package,
        Faults {
            host: Some(host),
            ..Faults::default()
        },
    )
}

#[test]
fn host_code_the_os_rejects_is_refused_at_authentication() {
    let temp = Temp::new();
    let package = build(&temp, "host-denied");
    for (name, host, expected) in [
        (
            "unsigned/revoked",
            HostFault::Denied,
            FailureCode::TrustDenied,
        ),
        (
            "assessment unavailable",
            HostFault::Unavailable,
            FailureCode::TrustUnavailable,
        ),
        (
            "unlisted publisher",
            HostFault::Assessed {
                signer: "Somebody Else".to_owned(),
                assessment: TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE.to_owned(),
            },
            FailureCode::TrustDenied,
        ),
        (
            "another platform's verdict",
            HostFault::Assessed {
                signer: PUBLISHER.to_owned(),
                assessment: veles_bridge_installer::TRUST_ASSESSMENT_MACOS_GATEKEEPER.to_owned(),
            },
            FailureCode::TrustDenied,
        ),
    ] {
        let platform = denied_host(&package, host);
        let failure =
            authenticate_package(&platform, &package.root, &package.descriptor).expect_err(name);
        assert_eq!(
            failure.code, expected,
            "{name}: a signed carrier does not vouch for the host code"
        );
        assert!(platform.mutations().is_empty());
    }
}

#[test]
fn installed_host_is_assessed_before_the_manifest_selects_it() {
    let temp = Temp::new();
    let paths = paths(&temp);
    let package = build(&temp, "installed-host");
    let good = platform(&package, Faults::default());
    let authenticated = authenticate_package(&good, &package.root, &package.descriptor)
        .expect("the package host passes");

    // The installed copy is what Chrome will launch: an OS refusal of *that* form
    // stops the transaction before activation, whatever the package form said.
    let refusing = denied_host(&package, HostFault::Denied);
    let failure = install(&refusing, &paths, authenticated.clone()).expect_err("denied");
    assert_eq!(failure.code, FailureCode::TrustDenied);
    assert!(!refusing.calls().contains(&PlatformCall::PublishManifest));
    assert!(!paths.registration_path().exists());

    let version = version_dir(&paths, &package);
    install(&good, &paths, authenticated).expect("installs");
    let verifications = good.host_verifications();
    assert_eq!(
        verifications,
        vec![
            (package.root.join("payload"), "host.exe".to_owned()),
            (version, "host.exe".to_owned()),
        ],
        "the package form and the installed form are both assessed"
    );
    let calls = good.calls();
    let assessed_installed = calls
        .iter()
        .rposition(|call| *call == PlatformCall::VerifyHost)
        .expect("installed assessment");
    let published = calls
        .iter()
        .position(|call| *call == PlatformCall::PublishManifest)
        .expect("publication");
    assert!(
        assessed_installed < published,
        "the installed host is assessed before activation"
    );
}

#[test]
fn rollback_reassesses_the_retained_host() {
    let temp = Temp::new();
    let paths = paths(&temp);
    let v1 = build_release(&temp, "v1", "1.0.0", 1, Vec::new(), true);
    let v1_id = v1.descriptor.payload_id;
    let v2 = build_release(&temp, "v2", "2.0.0", 2, vec![v1_id], false);
    for package in [&v1, &v2] {
        let fake = platform(package, Faults::default());
        install(
            &fake,
            &paths,
            authenticate_package(&fake, &package.root, &package.descriptor).expect("authenticates"),
        )
        .expect("installs");
    }
    let live_before = fs::read(paths.registration_path()).expect("live registration");

    let refusing = denied_host(&v1, HostFault::Denied);
    let failure = run_operation(&refusing, &paths, Operation::Rollback, None)
        .expect_err("the retained host is no longer acceptable");
    assert_eq!(failure.code, FailureCode::TrustDenied);
    assert_eq!(
        fs::read(paths.registration_path()).expect("live registration"),
        live_before,
        "a refused rollback leaves the live registration alone"
    );

    let good = platform(&v1, Faults::default());
    let report = run_operation(&good, &paths, Operation::Rollback, None).expect("rolls back");
    assert_eq!(report.state, STATE_ROLLED_BACK);
    assert!(
        good.host_verifications()
            .iter()
            .any(|(root, executable)| root == &version_dir(&paths, &v1) && executable == "host.exe")
    );
}
