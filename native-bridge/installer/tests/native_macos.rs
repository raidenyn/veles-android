//! Native macOS filesystem, registration and carrier-fidelity evidence
//! (OTP-25 Task 8).
//!
//! These tests must run on an **Apple-silicon macOS** runner under a standard,
//! non-root user. They are compiled only on macOS and are explicitly ignored
//! elsewhere: the Linux executor records them as BLOCKED, never as passes.

#![cfg(target_os = "macos")]

use std::fs;
use std::os::unix::fs::{PermissionsExt, symlink};
use std::path::{Path, PathBuf};
use std::process::{self, Command};
use std::sync::atomic::{AtomicU64, Ordering};

use veles_bridge_installer::{
    Channel, FailureCode, InstallPaths, MACOS_CHROME_HOSTS_DIR, MacosPlatform, NativeManifest,
    PackageSource, Platform, Registration, StateDocument, Target, ValidatedPath, extension_origin,
};

unsafe extern "C" {
    fn geteuid() -> u32;
}

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

struct TempRoot {
    path: PathBuf,
}

impl TempRoot {
    fn new(tag: &str) -> Self {
        let unique = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "veles-installer-task8-macos-{tag}-{}-{unique}",
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

fn macos_paths(temp: &TempRoot) -> InstallPaths {
    let home = temp.path().join("Test User Å/用户");
    InstallPaths::for_user(Target::MacosArm64, Channel::Fixture, &home, None).expect("layout")
}

fn registration(paths: &InstallPaths, executable: &str) -> Registration {
    let manifest = NativeManifest {
        name: paths.registration_name().to_owned(),
        description: "Veles Native Messaging bridge".to_owned(),
        path: paths
            .root()
            .join(executable)
            .to_str()
            .expect("utf-8 executable")
            .to_owned(),
        kind: "stdio".to_owned(),
        allowed_origins: vec![
            extension_origin("mnopabcdefghijklmnopabcdefghijkl").expect("extension id"),
        ],
    };
    Registration::new(serde_json::to_vec(&manifest).expect("manifest bytes")).expect("registration")
}

#[test]
#[ignore = "requires macOS runner; native evidence BLOCKED on this executor"]
fn macos_atomic_replace_parent_flush_and_no_symlink_follow() {
    let temp = TempRoot::new("atomic");
    let paths = macos_paths(&temp);
    let platform = MacosPlatform::new();
    let _lock = platform.lock(&paths).expect("lock");

    // A state document is replaced atomically: the live bytes are exactly the
    // new document and no staging sibling survives.
    platform
        .write_state(&paths, StateDocument::Journal, b"{\"phase\":\"first\"}")
        .expect("state write");
    assert_eq!(
        platform
            .read_state(&paths, StateDocument::Journal)
            .expect("state read"),
        Some(b"{\"phase\":\"first\"}".to_vec())
    );
    platform
        .write_state(&paths, StateDocument::Journal, b"{\"phase\":\"second\"}")
        .expect("state replace");
    assert_eq!(
        platform
            .read_state(&paths, StateDocument::Journal)
            .expect("state read"),
        Some(b"{\"phase\":\"second\"}".to_vec())
    );
    assert!(
        !paths.state().join("journal.json.tmp").exists(),
        "the staging sibling must be renamed away, not left behind"
    );

    // A planted symbolic link where a state document is expected is never
    // followed: the read, the write and the quarantine all fail closed and the
    // link target keeps its bytes.
    let victim = temp.path().join("victim.txt");
    fs::write(&victim, b"victim").expect("victim");
    symlink(&victim, paths.state().join("receipt.json")).expect("planted link");
    assert_eq!(
        platform
            .write_state(&paths, StateDocument::Receipt, b"{\"receipt\":true}")
            .expect_err("a linked state slot must be refused")
            .code,
        FailureCode::UnsafePath
    );
    assert_eq!(
        platform
            .read_state(&paths, StateDocument::Receipt)
            .expect_err("a linked state slot must be refused")
            .code,
        FailureCode::UnsafePath
    );
    assert_eq!(
        platform
            .quarantine_state(&paths, StateDocument::Receipt)
            .expect_err("a linked state slot must be refused")
            .code,
        FailureCode::UnsafePath
    );
    assert_eq!(fs::read(&victim).expect("victim bytes"), b"victim");
    let link = fs::symlink_metadata(paths.state().join("receipt.json")).expect("link metadata");
    assert!(
        link.file_type().is_symlink(),
        "the planted link is untouched"
    );

    // `replace_durable` stages a sibling and renames it, so a hard link to the
    // old inode keeps its bytes: the destination entry is replaced, not written
    // through.
    let version_dir = ValidatedPath::new_directory(&paths, Path::new("versions/1.0.0-aaaa"))
        .expect("version dir");
    platform
        .create_directory(&version_dir)
        .expect("version dir");
    let file = ValidatedPath::new_file(&paths, Path::new("versions/1.0.0-aaaa/data.bin"))
        .expect("new file handle");
    platform
        .write_durable(&file, b"old-bytes")
        .expect("durable write");
    let first = paths.root().join("versions/1.0.0-aaaa/data.bin");
    let linked = paths.root().join("versions/1.0.0-aaaa/data-link.bin");
    fs::hard_link(&first, &linked).expect("hard link");
    let existing = ValidatedPath::existing_file(&paths, Path::new("versions/1.0.0-aaaa/data.bin"))
        .expect("existing handle");
    platform
        .replace_durable(&existing, b"new-bytes")
        .expect("durable replace");
    assert_eq!(fs::read(&first).expect("replaced bytes"), b"new-bytes");
    assert_eq!(
        fs::read(&linked).expect("linked bytes"),
        b"old-bytes",
        "the old inode is never written through"
    );
    platform
        .flush_parent(&existing)
        .expect("parent flush must be a real fsync of the directory");

    // Every installer-owned directory is private to the user.
    for directory in [paths.root(), paths.state(), paths.versions()] {
        let mode = fs::symlink_metadata(directory)
            .expect("directory metadata")
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(mode, 0o700, "{directory:?} must be user-private");
    }
}

#[test]
#[ignore = "requires macOS runner; native evidence BLOCKED on this executor"]
fn non_admin_unicode_paths() {
    // SAFETY: geteuid has no preconditions.
    assert_ne!(unsafe { geteuid() }, 0, "this evidence must be non-root");
    let temp = TempRoot::new("unicode");
    let paths = macos_paths(&temp);
    let platform = MacosPlatform::new();
    let _lock = platform.lock(&paths).expect("lock");

    // Chrome resolves the per-user host registration at exactly this path, and
    // it must survive spaces and non-ASCII components.
    let expected = paths
        .user_home()
        .join("Library")
        .join("Application Support")
        .join(MACOS_CHROME_HOSTS_DIR)
        .join(format!("{}.json", paths.registration_name()));
    assert_eq!(paths.registration_path(), expected);
    assert!(
        paths
            .registration_path()
            .to_str()
            .expect("utf-8")
            .contains("用户 Å")
    );

    let directory = ValidatedPath::new_directory(&paths, Path::new("versions/1.0.0-aaaa"))
        .expect("version dir");
    platform.create_directory(&directory).expect("version dir");
    let executable = ValidatedPath::new_file(&paths, Path::new("versions/1.0.0-aaaa/host"))
        .expect("host handle");
    platform
        .write_durable(&executable, b"host-image")
        .expect("host image");

    let registration = registration(&paths, "versions/1.0.0-aaaa/host");
    platform
        .publish_manifest(&paths, &registration)
        .expect("manifest");
    platform
        .publish_registration(&paths, &registration)
        .expect("ordered registration check");
    let live = platform
        .read_registration(&paths)
        .expect("live registration")
        .expect("manifest");
    assert_eq!(live.manifest(), registration.manifest());
    assert!(
        live.windows_preimage().is_none(),
        "macOS has no HKCU preimage"
    );
    assert_eq!(
        fs::read(paths.registration_path()).expect("registration bytes"),
        registration.manifest()
    );

    // Carrier fidelity: the signed artifact is copied with extended attributes,
    // resource-fork-class metadata and symlink targets intact, because the seal
    // covers them and the retained verification later re-assesses the copy.
    let source = temp.path().join("carrier.app");
    fs::create_dir_all(source.join("Contents/MacOS")).expect("bundle tree");
    fs::create_dir_all(source.join("Contents/Frameworks")).expect("framework dir");
    fs::write(source.join("Contents/Info.plist"), b"<plist/>").expect("plist");
    fs::write(source.join("Contents/MacOS/carrier"), b"carrier-image").expect("carrier");
    symlink(
        Path::new("../MacOS"),
        source.join("Contents/Frameworks/Current"),
    )
    .expect("bundle symlink");
    let xattr = Command::new("/usr/bin/xattr")
        .args(["-w", "com.veles.task8", "fidelity"])
        .arg(source.join("Contents/MacOS/carrier"))
        .status()
        .expect("xattr tool");
    assert!(
        xattr.success(),
        "could not set the fixture extended attribute"
    );

    let carrier_dir =
        ValidatedPath::new_directory(&paths, Path::new("versions/1.0.0-aaaa/carrier.app"))
            .expect("carrier dir handle");
    platform
        .install_carrier(
            &PackageSource::open(&source).expect("carrier source"),
            &carrier_dir,
        )
        .expect("carrier install");
    let installed = paths.root().join("versions/1.0.0-aaaa/carrier.app");
    assert_eq!(
        fs::read(installed.join("Contents/MacOS/carrier")).expect("installed carrier"),
        b"carrier-image"
    );
    assert_eq!(
        fs::read_link(installed.join("Contents/Frameworks/Current")).expect("installed link"),
        PathBuf::from("../MacOS"),
        "bundle symlink targets are preserved, never followed"
    );
    let attribute = Command::new("/usr/bin/xattr")
        .args(["-p", "com.veles.task8"])
        .arg(installed.join("Contents/MacOS/carrier"))
        .output()
        .expect("xattr read");
    assert!(attribute.status.success(), "extended attribute was dropped");
    assert_eq!(
        String::from_utf8_lossy(&attribute.stdout).trim(),
        "fidelity",
        "extended attributes are part of the seal and must survive the copy"
    );

    // Enumeration describes exactly the symlink-free tree that install creates
    // and never escapes the artifact root.
    let refusal = platform
        .carrier_entries(&PackageSource::open(&source).expect("carrier source"))
        .expect_err("a symlink-bearing carrier artifact is refused at enumeration");
    assert_eq!(refusal.code, FailureCode::UnsafePath);
}
