//! Native Windows filesystem and HKCU registration evidence (OTP-25 Task 8).
//!
//! These tests must run on a **Windows** runner under a standard, non-elevated
//! user account. They are compiled only on Windows and are explicitly ignored on
//! every other host: the Linux/macOS executor records them as BLOCKED, never as
//! passes.
//!
//! Every test uses a private temporary install root and a test-only HKCU subkey
//! below `Software\Veles\InstallerTests`. A person's production
//! `Software\Google\Chrome\NativeMessagingHosts` key is never read or written.

#![cfg(windows)]

use std::fs;
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicU64, Ordering};

use veles_bridge_installer::{
    Channel, FailureCode, InstallPaths, NativeManifest, PathKind, Platform, Registration,
    RegistryView, StateDocument, Target, ValidatedPath, WindowsPlatform, extension_origin,
};

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

struct TempRoot {
    path: PathBuf,
}

impl TempRoot {
    fn new(tag: &str) -> Self {
        let unique = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "veles-installer-task8-windows-{tag}-{}-{unique}",
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

/// The production install layout, but rooted below the temporary directory.
fn windows_paths(temp: &TempRoot) -> InstallPaths {
    let home = temp.path().join("Test User Å/用户");
    let local = temp.path().join("Local AppData/用户 Å");
    InstallPaths::for_user(Target::WindowsX64, Channel::Fixture, &home, Some(&local))
        .expect("layout")
}

fn test_platform() -> WindowsPlatform {
    let subkey = format!(
        "otp25-{}-{}",
        process::id(),
        TEMP_COUNTER.fetch_add(1, Ordering::Relaxed)
    );
    WindowsPlatform::for_tests(&subkey).expect("test registry scope")
}

/// A canonical manifest selecting one owned executable.
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
#[ignore = "requires Windows runner; native evidence BLOCKED on this executor"]
fn windows_hkcu_manifest_swap_and_partial_registry_failure() {
    let temp = TempRoot::new("hkcu");
    let paths = windows_paths(&temp);
    let platform = test_platform();
    // The test scope can never address Chrome's production key.
    assert!(platform.registry_scope().is_test());
    assert!(
        !platform
            .registry_key(&paths)
            .to_ascii_lowercase()
            .contains(r"google\chrome")
    );

    let lock = platform.lock(&paths).expect("lock");

    // Two immutable versions, each with a complete host image.
    for version in ["1.0.0-aaaa", "1.0.0-bbbb"] {
        let directory =
            ValidatedPath::new_directory(&paths, Path::new(&format!("versions/{version}")))
                .expect("version dir handle");
        platform.create_directory(&directory).expect("version dir");
        let executable =
            ValidatedPath::new_file(&paths, Path::new(&format!("versions/{version}/host.exe")))
                .expect("host handle");
        platform
            .write_durable(&executable, b"host-image")
            .expect("host image");
    }

    // Publication step one: the manifest file, with the exact HKCU preimage.
    let first = registration(&paths, "versions/1.0.0-aaaa/host.exe");
    platform.publish_manifest(&paths, &first).expect("manifest");
    let live = platform
        .read_registration(&paths)
        .expect("live registration")
        .expect("manifest exists");
    let preimage = live.windows_preimage().expect("HKCU preimage");
    assert_eq!(preimage.key(), platform.registry_key(&paths));
    assert_eq!(preimage.previous_value(), None);

    // Publication step two: the value Chrome resolves. The exact absolute
    // manifest path must be visible in *every* view, which proves the key is not
    // WOW64-redirected and this is not a guessed path.
    platform
        .publish_registration(&paths, &first)
        .expect("registry publication");
    let manifest_path = paths
        .registration_path()
        .to_str()
        .expect("utf-8 manifest path");
    for view in [
        RegistryView::Default,
        RegistryView::Native64,
        RegistryView::Native32,
    ] {
        assert_eq!(
            platform
                .read_registry_value(&paths, view)
                .expect("registry read")
                .as_deref(),
            Some(manifest_path),
            "the same HKCU value must be visible in {view:?}"
        );
    }

    // An owned upgrade swaps the one manifest atomically; the value is unchanged.
    let second = registration(&paths, "versions/1.0.0-bbbb/host.exe");
    platform
        .publish_manifest(&paths, &second)
        .expect("manifest swap");
    assert_eq!(
        platform
            .read_registration(&paths)
            .expect("live registration")
            .expect("manifest")
            .manifest(),
        second.manifest()
    );

    // Partial failure: the manifest half succeeded, then the live manifest was
    // replaced by different (still owned) bytes. The registry half re-reads the
    // live manifest, refuses, and leaves the previously published value intact.
    let other = registration(&paths, "versions/1.0.0-aaaa/host.exe");
    let staged_path = Path::new("state/staging/registration.json");
    let staged = ValidatedPath::new_file(&paths, staged_path).expect("staging handle");
    platform
        .write_durable(&staged, other.manifest())
        .expect("staged bytes");
    let staged = staged.adopt(PathKind::File).expect("adopt staged");
    let live_handle =
        ValidatedPath::registration(&paths, PathKind::File).expect("registration handle");
    platform
        .replace_manifest(&staged, &live_handle)
        .expect("replace manifest");
    assert!(platform.publish_registration(&paths, &second).is_err());
    assert_eq!(
        platform
            .read_registry_value(&paths, RegistryView::Default)
            .expect("registry read")
            .as_deref(),
        Some(manifest_path),
        "a refused registry half must not touch the published value"
    );

    // A live registration this installer does not own is never stolen.
    platform.publish_manifest(&paths, &second).expect("restore");
    let foreign = br#"{"name":"me.nagaev.veles.bridge","description":"Veles","path":"C:\\Program Files\\Other\\host.exe","type":"stdio","allowed_origins":["chrome-extension://mnopabcdefghijklmnopabcdefghijkl/"]}"#;
    platform
        .write_durable(&live_handle, foreign)
        .expect("foreign live bytes");
    let failure = platform
        .publish_manifest(&paths, &second)
        .expect_err("a foreign registration must be refused");
    assert_eq!(failure.code, FailureCode::ForeignState);
    assert_eq!(
        fs::read(paths.registration_path()).expect("foreign bytes"),
        foreign,
        "the refused registration is left byte-for-byte intact"
    );

    // Removal takes our value and manifest, and is idempotent.
    platform.publish_manifest(&paths, &second).expect("restore");
    platform
        .publish_registration(&paths, &second)
        .expect("republish");
    platform
        .remove_registration(&paths, &second)
        .expect("remove registration");
    assert_eq!(
        platform
            .read_registry_value(&paths, RegistryView::Default)
            .expect("registry read"),
        None
    );
    assert!(!paths.registration_path().exists());
    platform
        .remove_registration(&paths, &second)
        .expect("removal is idempotent");

    drop(lock);
}

#[test]
#[ignore = "requires Windows runner; native evidence BLOCKED on this executor"]
fn non_admin_unicode_paths() {
    // Must run as a standard, non-elevated account: every mutation below targets
    // `%LOCALAPPDATA%`/temporary paths below the user profile only, never
    // `%ProgramFiles%`, `%PROGRAMDATA%` or HKCU keys outside the test subtree.
    let temp = TempRoot::new("unicode");
    let paths = windows_paths(&temp);
    let platform = test_platform();
    assert!(
        paths
            .root()
            .to_str()
            .expect("utf-8 root")
            .contains("用户 Å")
    );

    let _lock = platform.lock(&paths).expect("lock");

    // Durable bytes and state documents survive non-ASCII/space components.
    let file = ValidatedPath::new_file(&paths, Path::new("versions/1.0.0-aaaa/host.exe"))
        .expect("host handle");
    let directory = ValidatedPath::new_directory(&paths, Path::new("versions/1.0.0-aaaa"))
        .expect("version dir");
    platform.create_directory(&directory).expect("version dir");
    platform
        .write_durable(&file, b"unicode-host")
        .expect("durable write");
    assert_eq!(
        fs::read(paths.root().join("versions/1.0.0-aaaa/host.exe")).expect("read back"),
        b"unicode-host"
    );
    platform
        .write_state(&paths, StateDocument::Journal, b"{\"schema\":1}")
        .expect("state write");
    assert_eq!(
        platform
            .read_state(&paths, StateDocument::Journal)
            .expect("state read"),
        Some(b"{\"schema\":1}".to_vec())
    );

    // The Unicode manifest path is what Chrome resolves from HKCU.
    let registration = registration(&paths, "versions/1.0.0-aaaa/host.exe");
    platform
        .publish_manifest(&paths, &registration)
        .expect("manifest");
    platform
        .publish_registration(&paths, &registration)
        .expect("registry publication");
    let stored = platform
        .read_registry_value(&paths, RegistryView::Default)
        .expect("registry read")
        .expect("value");
    assert_eq!(stored, paths.registration_path().to_str().expect("utf-8"));
    assert_eq!(
        fs::read(paths.registration_path()).expect("manifest file"),
        registration.manifest()
    );
}
