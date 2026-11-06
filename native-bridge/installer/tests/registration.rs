//! Portable registration-ownership contract tests (OTP-25 Task 3).
//!
//! The installer must never claim, overwrite or remove a registration that a
//! forged receipt, a foreign target or another product supplied. These fixtures
//! are synthetic and portable; native HKCU/macOS behaviour is Task 8's evidence.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicU64, Ordering};

use veles_bridge_installer::{
    Channel, Digest, FailureCode, FakePlatform, InstallPaths, OwnedClaim, OwnershipRecord,
    Platform, PlatformCall, Registration, Target, claim_owned_entries, claim_owned_registration,
};

const EXTENSION_ID: &str = "mnopabcdefghijklmnopabcdefghijkl";

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
            "veles-installer-task3-registration-{}-{unique}",
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

fn manifest_bytes(paths: &InstallPaths, executable: &Path) -> Vec<u8> {
    let executable = serde_json::to_string(executable.to_str().expect("UTF-8 fixture executable"))
        .expect("JSON string");
    let text = format!(
        "{{\"name\":{},\"description\":\"Veles bridge fixture\",\"path\":{executable},\"type\":\"stdio\",\"allowed_origins\":[\"chrome-extension://{EXTENSION_ID}/\"]}}\n",
        serde_json::to_string(paths.registration_name()).expect("JSON string")
    );
    text.into_bytes()
}

fn record(paths: &InstallPaths, manifest: &[u8], owned: Vec<OwnedClaim>) -> OwnershipRecord {
    OwnershipRecord {
        schema: 1,
        target: paths.target(),
        channel: paths.channel(),
        root: paths.root().to_str().expect("UTF-8 root").to_owned(),
        manifest_sha256: Digest::of(manifest),
        owned,
    }
}

/// Every file below `root` with its exact bytes; directories are recorded as
/// empty entries. A foreign state must survive byte-for-byte.
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

#[test]
fn foreign_registration_is_not_claimed_from_receipt() {
    let temp = TempRoot::new();
    let paths = windows_paths(&temp);
    let platform = FakePlatform::new();
    let _lock = platform
        .lock(&paths)
        .expect("lock creates the private layout");

    // A plausible owned payload exists, so the forged receipt can list a real
    // in-root claim; the decisive violation is the live manifest's target.
    let owned_relative = format!(
        "versions/1.2.3-{}/host.exe",
        Digest::of(b"owned-host").to_hex()
    );
    let owned_executable = paths.versions().join(
        owned_relative
            .strip_prefix("versions/")
            .expect("versions-relative fixture"),
    );
    fs::create_dir_all(owned_executable.parent().expect("version directory"))
        .expect("version directory");
    fs::write(&owned_executable, b"owned-host").expect("owned fixture executable");

    // A foreign product points the same registration name at an executable
    // outside our fixed root. It bypasses `publish_registration` on purpose:
    // the file represents state that already existed on disk.
    let foreign_directory = temp.path().join("foreign");
    fs::create_dir_all(&foreign_directory).expect("foreign directory");
    let foreign_executable = foreign_directory.join("host.exe");
    fs::write(&foreign_executable, b"foreign-host").expect("foreign executable");
    let foreign_manifest = manifest_bytes(&paths, &foreign_executable);
    fs::write(paths.registration_path(), &foreign_manifest).expect("foreign live registration");

    let forged = record(
        &paths,
        &foreign_manifest,
        vec![OwnedClaim::file(&owned_relative, Digest::of(b"owned-host"))],
    );
    let before = snapshot(temp.path());
    let result = claim_owned_registration(&platform, &paths, &forged);
    assert_eq!(
        failure_code(result),
        FailureCode::ForeignState,
        "a manifest that selects a foreign executable is not ours"
    );
    assert!(
        platform.mutations().is_empty(),
        "claiming is read-only: {:?}",
        platform.mutations()
    );
    assert!(platform.calls().contains(&PlatformCall::ReadRegistration));
    assert_eq!(
        snapshot(temp.path()),
        before,
        "foreign registration and every owned/unowned file stay byte-for-byte intact"
    );
    assert_eq!(
        fs::read(&foreign_executable).expect("foreign executable survives"),
        b"foreign-host"
    );

    // A receipt for another root/channel is refused without reading or writing
    // anything.
    let mut wrong_channel = forged.clone();
    wrong_channel.channel = Channel::Development;
    assert_eq!(
        failure_code(claim_owned_registration(&platform, &paths, &wrong_channel)),
        FailureCode::ForeignState
    );
    let mut wrong_root = forged;
    wrong_root.root = paths
        .root()
        .join("elsewhere")
        .to_string_lossy()
        .into_owned();
    assert_eq!(
        failure_code(claim_owned_registration(&platform, &paths, &wrong_root)),
        FailureCode::ForeignState
    );
    assert_eq!(snapshot(temp.path()), before);

    // A forged ownership claim that escapes the root is never an owned handle.
    let escaping = OwnershipRecord {
        schema: 1,
        target: paths.target(),
        channel: paths.channel(),
        root: paths.root().to_str().expect("UTF-8 root").to_owned(),
        manifest_sha256: Digest::of(&foreign_manifest),
        owned: vec![OwnedClaim::file(
            "../foreign/host.exe",
            Digest::of(b"foreign-host"),
        )],
    };
    assert_eq!(
        failure_code(claim_owned_entries(&paths, &escaping)),
        FailureCode::UnsafePath
    );

    // A claim whose content no longer matches its recorded digest is foreign.
    let replaced = record(
        &paths,
        &foreign_manifest,
        vec![OwnedClaim::file(
            &owned_relative,
            Digest::of(b"not-the-owned-content"),
        )],
    );
    assert_eq!(
        failure_code(claim_owned_entries(&paths, &replaced)),
        FailureCode::ForeignState,
        "ownership is content-specific, not a path-string claim"
    );

    // An owned version *directory* is a legitimate directory claim even though
    // an executable must be a file below it.
    let version_directory = owned_relative
        .rsplit_once('/')
        .expect("version directory prefix")
        .0
        .to_owned();
    let with_directory = record(
        &paths,
        &foreign_manifest,
        vec![
            OwnedClaim::file(&owned_relative, Digest::of(b"owned-host")),
            OwnedClaim::directory(version_directory),
        ],
    );
    with_directory
        .validate(&paths)
        .expect("a version directory claim is in scope");

    // Manifest publication refuses a manifest naming a foreign executable and
    // leaves the registration absent.
    let foreign_registration = Registration::new(foreign_manifest.clone()).expect("bounded");
    assert_eq!(
        failure_code(platform.publish_manifest(&paths, &foreign_registration)),
        FailureCode::UnsafePath
    );
    assert_eq!(
        fs::read(paths.registration_path()).expect("read registration"),
        foreign_manifest,
        "a refused publish leaves the live foreign registration byte-for-byte intact"
    );

    // Positive control: the same receipt claims a registration whose manifest
    // points inside a version directory, and removal requires the exact bytes.
    // The foreign registration above must first be resolved explicitly (an
    // operator or a prior uninstall); publish itself must never overwrite it.
    fs::remove_file(paths.registration_path()).expect("resolve the foreign registration");
    let owned_manifest = manifest_bytes(&paths, &owned_executable);
    let registration = Registration::new(owned_manifest.clone()).expect("bounded");
    platform
        .publish_manifest(&paths, &registration)
        .expect("publishing an owned manifest succeeds");
    platform
        .publish_registration(&paths, &registration)
        .expect("the registry step accepts the published manifest");
    assert_eq!(
        fs::read(paths.registration_path()).expect("published manifest"),
        owned_manifest
    );

    let owned_record = record(
        &paths,
        &owned_manifest,
        vec![OwnedClaim::file(&owned_relative, Digest::of(b"owned-host"))],
    );
    let claimed = claim_owned_registration(&platform, &paths, &owned_record)
        .expect("an owned registration is claimable");
    assert_eq!(claimed.manifest(), owned_manifest.as_slice());

    let mut tampered_manifest = owned_manifest.clone();
    tampered_manifest.push(b' ');
    let tampered = Registration::new(tampered_manifest).expect("bounded");
    assert_eq!(
        failure_code(platform.remove_registration(&paths, &tampered)),
        FailureCode::ForeignState
    );
    assert_eq!(
        fs::read(paths.registration_path()).expect("registration untouched"),
        owned_manifest,
        "a mismatched removal expectation changes nothing"
    );

    platform
        .remove_registration(&paths, &claimed)
        .expect("exact expected bytes are removable");
    assert!(
        platform.read_registration(&paths).expect("read").is_none(),
        "the owned registration is gone"
    );
}

#[test]
fn owned_publish_over_foreign_registration_is_refused() {
    let temp = TempRoot::new();
    let paths = windows_paths(&temp);
    let platform = FakePlatform::new();
    let _lock = platform
        .lock(&paths)
        .expect("lock creates the private layout");

    // Our own payload exists and is publishable in isolation.
    let owned_relative = format!(
        "versions/1.2.3-{}/host.exe",
        Digest::of(b"owned-host").to_hex()
    );
    let owned_executable = paths.versions().join(
        owned_relative
            .strip_prefix("versions/")
            .expect("versions-relative fixture"),
    );
    fs::create_dir_all(owned_executable.parent().expect("version directory"))
        .expect("version directory");
    fs::write(&owned_executable, b"owned-host").expect("owned fixture executable");

    // The live registration at our fixed path already belongs to another
    // product: its target is outside our root.
    let foreign_directory = temp.path().join("foreign");
    fs::create_dir_all(&foreign_directory).expect("foreign directory");
    let foreign_executable = foreign_directory.join("host.exe");
    fs::write(&foreign_executable, b"foreign-host").expect("foreign executable");
    let foreign_manifest = manifest_bytes(&paths, &foreign_executable);
    fs::write(paths.registration_path(), &foreign_manifest).expect("foreign live registration");

    let owned_manifest = manifest_bytes(&paths, &owned_executable);
    let owned = Registration::new(owned_manifest.clone()).expect("bounded");
    let before = snapshot(temp.path());
    assert_eq!(
        failure_code(platform.publish_manifest(&paths, &owned)),
        FailureCode::ForeignState,
        "an owned manifest may not replace a foreign live registration"
    );
    assert_eq!(
        failure_code(platform.publish_registration(&paths, &owned)),
        FailureCode::ForeignState,
        "the registry step applies the same owned-preimage refusal"
    );
    assert_eq!(
        snapshot(temp.path()),
        before,
        "the refused publish changes no bytes anywhere, including the live path"
    );
    assert_eq!(
        fs::read(paths.registration_path()).expect("foreign registration survives"),
        foreign_manifest,
        "the foreign registration stays byte-for-byte intact"
    );

    // Once the foreign registration is explicitly resolved, an owned publish
    // succeeds, and a later owned release may replace that owned preimage.
    fs::remove_file(paths.registration_path()).expect("remove the foreign registration");
    platform
        .publish_manifest(&paths, &owned)
        .expect("a clean path accepts the owned manifest");
    platform
        .publish_registration(&paths, &owned)
        .expect("the registry step accepts the published manifest");
    assert_eq!(
        fs::read(paths.registration_path()).expect("published manifest"),
        owned_manifest
    );

    let next_relative = format!(
        "versions/1.2.4-{}/host.exe",
        Digest::of(b"next-owned-host").to_hex()
    );
    let next_executable = paths.versions().join(
        next_relative
            .strip_prefix("versions/")
            .expect("versions-relative fixture"),
    );
    fs::create_dir_all(next_executable.parent().expect("version directory"))
        .expect("version directory");
    fs::write(&next_executable, b"next-owned-host").expect("next fixture executable");
    let next_manifest = manifest_bytes(&paths, &next_executable);
    let next = Registration::new(next_manifest.clone()).expect("bounded");
    platform
        .publish_manifest(&paths, &next)
        .expect("an owned preimage is replaceable by an upgrade");
    platform
        .publish_registration(&paths, &next)
        .expect("the registry step accepts the upgraded manifest");
    assert_eq!(
        fs::read(paths.registration_path()).expect("upgraded manifest"),
        next_manifest
    );
}

/// Publication is two ordered platform calls: the manifest file first, then the
/// registry/manifest-registration step. The registry step must never accept a
/// manifest that is not already live.
#[test]
fn registry_step_requires_the_published_manifest() {
    let temp = TempRoot::new();
    let paths = windows_paths(&temp);
    let platform = FakePlatform::new();
    let _lock = platform
        .lock(&paths)
        .expect("lock creates the private layout");

    let owned_relative = format!(
        "versions/1.2.3-{}/host.exe",
        Digest::of(b"ordered-host").to_hex()
    );
    let owned_executable = paths.versions().join(
        owned_relative
            .strip_prefix("versions/")
            .expect("versions-relative fixture"),
    );
    fs::create_dir_all(owned_executable.parent().expect("version directory"))
        .expect("version directory");
    fs::write(&owned_executable, b"ordered-host").expect("owned fixture executable");

    let manifest = manifest_bytes(&paths, &owned_executable);
    let registration = Registration::new(manifest.clone()).expect("bounded");
    assert_eq!(
        failure_code(platform.publish_registration(&paths, &registration)),
        FailureCode::Io,
        "the registry step refuses before the manifest is live"
    );
    assert!(
        platform.read_registration(&paths).expect("read").is_none(),
        "the registry step wrote nothing"
    );

    platform
        .publish_manifest(&paths, &registration)
        .expect("the manifest step publishes");
    platform
        .publish_registration(&paths, &registration)
        .expect("the registry step accepts the live manifest");
    let mutations = platform.mutations();
    assert_eq!(
        &mutations[mutations.len() - 2..],
        &[
            PlatformCall::PublishManifest,
            PlatformCall::PublishRegistration
        ],
        "the two publication steps are separately observable and ordered"
    );
    assert_eq!(
        fs::read(paths.registration_path()).expect("published manifest"),
        manifest
    );
}
