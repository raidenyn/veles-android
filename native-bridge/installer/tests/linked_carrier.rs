//! Sanctioned carrier links versus host quiescence (fix round F9).
//!
//! A signed macOS carrier bundle legitimately contains contained symbolic links
//! (a framework's `Versions/Current`). The installer copies the bundle into the
//! immutable version directory, so every later quiescence inspection walks it.
//! That walk used to reject *any* link, which made inspection `Unknown` for every
//! release that carried one: install, cleanup and recovery were all blocked
//! while no host ran at all. These tests drive the real owned-tree
//! classification through the install / uninstall orchestration.
//!
//! Unix only: the fixtures need symbolic links, and the macOS carrier is the
//! only artifact that carries them.
#![cfg(unix)]

use std::fs;
use std::os::unix::fs::symlink;
use std::path::PathBuf;
use std::process;
use std::sync::atomic::{AtomicU64, Ordering};

use veles_bridge_installer::{
    Channel, Digest, FakePlatform, Faults, HostState, InstallPaths, InventoryEntry, Operation,
    PRODUCTION_HOST_NAME, ReleaseDescriptor, ReleasePolicy, STATE_UNINSTALLED,
    TRUST_ASSESSMENT_MACOS_GATEKEEPER, Target, TrustProof, authenticate_package, canonical_json,
    inspect_host_processes, install, run_operation, write_carrier, write_inventory,
};

const EXTENSION_ID: &str = "mnopabcdefghijklmnopabcdefghijkl";
const PUBLISHER: &str = "Team One";

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct Temp(PathBuf);

impl Temp {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "veles-installer-linked-carrier-{}-{}",
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

fn paths(temp: &Temp) -> InstallPaths {
    let home = temp.0.join("home");
    // Chrome's own directory, which the installer registers into but never creates.
    fs::create_dir_all(home.join("Library/Application Support/Google/Chrome/NativeMessagingHosts"))
        .expect("Chrome native messaging directory");
    InstallPaths::for_user(Target::MacosArm64, Channel::Production, &home, None)
        .expect("injected macOS root")
}

struct Package {
    root: PathBuf,
    descriptor: ReleaseDescriptor,
    proof: TrustProof,
}

/// A macOS package whose `carrier.app` holds a framework with the usual contained
/// links.
fn build(temp: &Temp) -> Package {
    let root = temp.0.join("package");
    let payload = root.join("payload");
    fs::create_dir_all(payload.join("Veles.app/Contents/MacOS")).expect("payload");
    fs::write(
        payload.join("Veles.app/Contents/MacOS/host"),
        b"mach-o host",
    )
    .expect("host");
    let inventory = write_inventory(&[InventoryEntry {
        path: "Veles.app/Contents/MacOS/host".to_owned(),
        size: 11,
        sha256: Digest::of(b"mach-o host"),
        executable: true,
    }])
    .expect("inventory");
    fs::write(root.join("inventory.json"), &inventory).expect("inventory file");
    let descriptor = ReleaseDescriptor {
        schema: 1,
        channel: Channel::Production,
        version: "1.0.0".to_owned(),
        source_commit: "0123456789abcdef0123456789abcdef01234567".to_owned(),
        target: Target::MacosArm64,
        minimum_os: "26.0".to_owned(),
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
    let carrier = write_carrier(&canonical_json(&descriptor).expect("json")).expect("carrier");

    let app = root.join("carrier.app/Contents");
    fs::create_dir_all(app.join("Resources")).expect("resources");
    fs::write(app.join("Resources/veles-policy-carrier"), &carrier).expect("carrier resource");
    // The carrier bundle's own engine is the persistent maintenance engine the
    // installer publishes under `maintenance/`.
    fs::create_dir_all(app.join("MacOS")).expect("carrier executable directory");
    fs::write(
        app.join("MacOS/veles-bridge-maintenance"),
        b"mach-o maintenance engine",
    )
    .expect("carrier engine");
    let framework = app.join("Frameworks/Helper.framework");
    fs::create_dir_all(framework.join("Versions/A")).expect("framework");
    fs::write(framework.join("Versions/A/Helper"), b"framework binary").expect("binary");
    symlink("A", framework.join("Versions/Current")).expect("Versions/Current");
    symlink("Versions/Current/Helper", framework.join("Helper")).expect("Helper");

    let proof =
        TrustProof::new(PUBLISHER, TRUST_ASSESSMENT_MACOS_GATEKEEPER, carrier).expect("proof");
    Package {
        root,
        descriptor,
        proof,
    }
}

fn platform(package: &Package) -> FakePlatform {
    FakePlatform::with_faults(Faults {
        trust: Some(package.proof.clone()),
        host_state: HostState::Quiescent,
        // Real owned-tree classification over a machine with no running host.
        inspect_owned_tree: true,
        ..Faults::default()
    })
}

#[test]
fn install_and_uninstall_pass_quiescence_with_a_linked_carrier() {
    let temp = Temp::new();
    let paths = paths(&temp);
    let package = build(&temp);
    let platform = platform(&package);
    let authenticated =
        authenticate_package(&platform, &package.root, &package.descriptor).expect("authenticates");

    install(&platform, &paths, authenticated)
        .expect("a sanctioned carrier link must not make inspection Unknown");
    let version = paths.version_dir(
        &veles_bridge_installer::Version::parse("1.0.0").expect("version"),
        package.descriptor.payload_id,
    );
    assert!(
        fs::symlink_metadata(
            version.join("carrier.app/Contents/Frameworks/Helper.framework/Versions/Current")
        )
        .expect("installed link")
        .file_type()
        .is_symlink(),
        "the installed carrier keeps its link"
    );

    let report = run_operation(&platform, &paths, Operation::Uninstall, None)
        .expect("cleanup of a linked carrier is not blocked by inspection");
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(!version.exists(), "the whole owned version tree is removed");
}

#[test]
fn only_contained_carrier_links_are_sanctioned() {
    let temp = Temp::new();
    let paths = paths(&temp);
    let package = build(&temp);
    let platform = platform(&package);
    let authenticated =
        authenticate_package(&platform, &package.root, &package.descriptor).expect("authenticates");
    install(&platform, &paths, authenticated).expect("installs");
    let version = paths.version_dir(
        &veles_bridge_installer::Version::parse("1.0.0").expect("version"),
        package.descriptor.payload_id,
    );

    // The installed, validated links are fine.
    let quiet =
        |paths: &InstallPaths| inspect_host_processes(paths, &NoProcesses) == HostState::Quiescent;
    assert!(quiet(&paths));

    let hostile: &[(&str, PathBuf, &str)] = &[
        (
            "escaping target",
            version.join("carrier.app/Contents/escape"),
            "../../../../../../../etc",
        ),
        (
            "absolute target",
            version.join("carrier.app/Contents/absolute"),
            "/usr/bin",
        ),
        (
            "link beside the host payload",
            version.join("Veles.app/Contents/MacOS/sneaky"),
            "host",
        ),
        (
            "link directly under the version directory",
            version.join("current"),
            "carrier.app",
        ),
    ];
    for (name, link, target) in hostile {
        symlink(target, link).unwrap_or_else(|_| panic!("{name}: plant"));
        assert!(
            !quiet(&paths),
            "{name}: a link that is not a contained carrier link must keep inspection Unknown"
        );
        fs::remove_file(link).expect("unplant");
        assert!(quiet(&paths), "{name}: back to quiescent once removed");
    }
}

/// A process table of a machine on which nothing runs.
struct NoProcesses;

impl veles_bridge_installer::ProcessTable for NoProcesses {
    fn observe(
        &self,
        _paths: &InstallPaths,
    ) -> std::io::Result<Vec<veles_bridge_installer::ProcessObservation>> {
        Ok(Vec::new())
    }

    fn recheck(
        &self,
        _paths: &InstallPaths,
        _pid: u32,
    ) -> std::io::Result<Option<veles_bridge_installer::ProcessObservation>> {
        Ok(None)
    }
}
