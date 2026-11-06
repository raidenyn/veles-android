//! Portable contract tests for the fixed per-user install layout, the safe
//! write/rename boundary and the exclusive installer lock (OTP-25 Task 3).
//!
//! Everything here runs against injected temporary roots on the test host. A
//! passing test is portable IO/layout evidence only; it is never Windows
//! registry, macOS sealing or native acceptance proof (Tasks 4, 5 and 8).

use std::fs;
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicU64, Ordering};

use veles_bridge_installer::{
    Channel, Digest, FailureCode, FakePlatform, Faults, InstallPaths, PathKind, Platform,
    PlatformCall, PortablePlatform, ROOT_DIR_DEVELOPMENT, ROOT_DIR_FIXTURE, ROOT_DIR_PRODUCTION,
    Target, ValidatedPath, parse_version, path_is_within,
};

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
            "veles-installer-task3-safeio-{}-{unique}",
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

/// Injected Windows known-folder roots: a Unicode profile name and a distinct
/// Unicode LocalAppData root. Production never accepts these from a CLI or an
/// environment variable; they are test-only inputs to `for_user`.
fn windows_paths(temp: &TempRoot, channel: Channel) -> InstallPaths {
    let home = temp.path().join("Test User Å/用户");
    let local = temp.path().join("Local AppData/用户 Å");
    InstallPaths::for_user(Target::WindowsX64, channel, &home, Some(&local))
        .expect("valid injected Windows roots")
}

#[test]
fn partial_write_retries_and_readback_matches() {
    let temp = TempRoot::new();
    let paths = windows_paths(&temp, Channel::Production);

    let platform = FakePlatform::with_faults(Faults {
        // Every `write` is allowed to make progress with at most three bytes,
        // so the writer must loop instead of assuming one call wrote everything.
        short_write_limit: Some(3),
        ..Faults::default()
    });
    let lock = platform.lock(&paths).expect("first lock");
    let staged = ValidatedPath::new_file(&paths, Path::new("state/staging/receipt.json"))
        .expect("staged path under the fixed root");

    // Binary bytes (not valid JSON) plus the canonical trailing newline: the
    // write boundary must be byte-exact, never text-normalized.
    let mut bytes = vec![
        0x00, 0xff, 0x80, 0x01, b'{', b'"', b'x', b'"', b':', b'1', b'}',
    ];
    bytes.push(b'\n');
    platform
        .write_durable(&staged, &bytes)
        .expect("durable write survives injected short writes");

    let on_disk = fs::read(staged.rendered()).expect("read back staged bytes");
    assert_eq!(on_disk, bytes, "every byte must be written exactly once");
    assert_eq!(on_disk.len(), bytes.len(), "no truncation or padding");
    assert_eq!(on_disk.last(), Some(&b'\n'), "newline is preserved");
    assert_eq!(
        Digest::of(&on_disk),
        Digest::of(&bytes),
        "the on-disk SHA-256 matches the intended bytes"
    );
    assert!(
        platform.short_write_count() > 1,
        "the fixture must inject real short writes (got {})",
        platform.short_write_count()
    );
    assert_eq!(
        platform.mutations(),
        vec![PlatformCall::WriteDurable],
        "exactly one state mutation occurred"
    );

    platform.flush_parent(&staged).expect("parent flushes");
    drop(lock);

    // A read-back that does not match the written bytes is a hard IO failure,
    // not a silent success.
    let platform = FakePlatform::with_faults(Faults {
        corrupt_readback: true,
        ..Faults::default()
    });
    let _lock = platform.lock(&paths).expect("relock after release");
    let staged = ValidatedPath::new_file(&paths, Path::new("state/staging/other.json"))
        .expect("second staged path");
    assert_eq!(
        failure_code(platform.write_durable(&staged, b"payload\n")),
        FailureCode::Io,
        "a mismatched read-back fails closed"
    );
}

#[test]
fn ancestor_swap_and_symlink_block() {
    let temp = TempRoot::new();
    let paths = windows_paths(&temp, Channel::Production);
    let platform = FakePlatform::new();
    let _lock = platform
        .lock(&paths)
        .expect("lock creates the private layout");

    // Traversal, absolute and platform-alias forms never become handles.
    for bad in [
        "../escape.json",
        "state/../../escape.json",
        "/etc/passwd",
        "state\\alias.json",
        "state/x:y",
    ] {
        assert_eq!(
            failure_code(ValidatedPath::new_file(&paths, Path::new(bad))),
            FailureCode::UnsafePath,
            "path {bad:?} must be rejected"
        );
    }

    let staged = ValidatedPath::new_file(&paths, Path::new("state/staging/manifest.json"))
        .expect("validated staged path");
    assert!(
        ValidatedPath::within(
            &paths,
            &paths.state().join("inside.json"),
            PathKind::NewFile
        )
        .is_ok(),
        "an absolute path inside the root becomes a handle"
    );
    assert_eq!(
        failure_code(ValidatedPath::within(
            &paths,
            &temp.path().join("outside/inside.json"),
            PathKind::NewFile
        )),
        FailureCode::UnsafePath,
        "an absolute path outside the root never becomes a handle"
    );
    let state = paths.state().to_path_buf();
    let moved_state = paths.root().join("state-real");
    fs::rename(&state, &moved_state).expect("move the real state directory aside");
    let outside = temp.path().join("outside");
    fs::create_dir_all(&outside).expect("outside directory");

    #[cfg(unix)]
    {
        // A parent swapped for a symlink after validation must fail on the next
        // mutation and must never write through the link.
        std::os::unix::fs::symlink(&outside, &state).expect("symlink state");
        assert_eq!(
            failure_code(platform.write_durable(&staged, b"escaped\n")),
            FailureCode::UnsafePath,
            "the revalidated mutation rejects a symlinked ancestor"
        );
        assert!(
            !outside.join("manifest.json").exists(),
            "nothing may be written through the swapped ancestor"
        );
        assert!(
            !moved_state.join("manifest.json").exists(),
            "nothing may be written into the moved directory either"
        );
        assert_eq!(
            failure_code(ValidatedPath::new_file(
                &paths,
                Path::new("state/staging/after-swap.json")
            )),
            FailureCode::UnsafePath,
            "new handles also refuse a symlinked ancestor"
        );

        fs::remove_file(&state).expect("remove symlink");
        fs::rename(&moved_state, &state).expect("restore state directory");

        // Swap a validated ancestor for a *different* real directory. The
        // captured file identity (device/inode) no longer matches.
        let victim = ValidatedPath::new_file(&paths, Path::new("state/staging/victim.json"))
            .expect("validated victim path");
        let staging = paths.staging().to_path_buf();
        let moved_staging = paths.state().join("staging-real");
        fs::rename(&staging, &moved_staging).expect("move staging aside");
        fs::create_dir(&staging).expect("replacement staging directory");
        assert_eq!(
            failure_code(platform.write_durable(&victim, b"wrong parent\n")),
            FailureCode::UnsafePath,
            "ancestor identity changes are detected even without a symlink"
        );
        assert!(
            !staging.join("victim.json").exists(),
            "the replacement directory stays untouched"
        );

        // A symlinked final component is refused for both new and existing
        // handles.
        let outside_file = temp.path().join("outside.json");
        fs::write(&outside_file, b"outside\n").expect("outside file");
        std::os::unix::fs::symlink(&outside_file, staging.join("linked.json"))
            .expect("symlink final component");
        assert_eq!(
            failure_code(ValidatedPath::existing_file(
                &paths,
                Path::new("state/staging/linked.json")
            )),
            FailureCode::UnsafePath
        );
        assert_eq!(
            failure_code(ValidatedPath::new_file(
                &paths,
                Path::new("state/staging/linked.json")
            )),
            FailureCode::UnsafePath
        );
        assert_eq!(
            fs::read(&outside_file).expect("outside file readable"),
            b"outside\n",
            "the symlink target remains untouched"
        );
    }

    #[cfg(not(unix))]
    {
        let _ = (&outside, &staged);
    }
}

#[test]
fn unicode_paths_and_busy_lock() {
    let temp = TempRoot::new();
    let home = temp.path().join("Test User Å/用户");
    let local = temp.path().join("Local AppData/用户 Å");

    let production =
        InstallPaths::for_user(Target::WindowsX64, Channel::Production, &home, Some(&local))
            .expect("valid production layout");
    assert_eq!(
        production.root(),
        local.join("Veles").join(ROOT_DIR_PRODUCTION),
        "the Windows root is the exact known-folder join"
    );
    assert_eq!(
        production.root(),
        local.join("Veles/NativeBridge"),
        "the fixed production root is stable"
    );
    assert_eq!(production.registration_name(), "me.nagaev.veles.bridge");
    assert_eq!(
        production.registration_path(),
        production.root().join("me.nagaev.veles.bridge.json"),
        "Windows keeps the manifest at its fixed root"
    );
    assert_eq!(production.state(), production.root().join("state"));
    assert_eq!(production.staging(), production.state().join("staging"));
    assert_eq!(
        production.lock_path(),
        production.state().join("installer.lock")
    );
    assert_eq!(
        production.journal_path(),
        production.state().join("journal.json")
    );
    assert_eq!(
        production.receipt_path(),
        production.state().join("receipt.json")
    );
    assert_eq!(production.versions(), production.root().join("versions"));
    assert_eq!(
        production.maintenance(),
        production.root().join("maintenance")
    );
    assert!(production.root().is_absolute(), "roots stay absolute");
    let rendered = production
        .root()
        .to_str()
        .expect("the rendered root is UTF-8 safe");
    assert!(rendered.contains("Test User Å") || rendered.contains("用户 Å"));
    let json = serde_json::to_string(rendered).expect("UTF-8 paths render as JSON");
    assert_eq!(
        serde_json::from_str::<String>(&json).expect("JSON round-trips"),
        rendered
    );

    let development = InstallPaths::for_user(
        Target::WindowsX64,
        Channel::Development,
        &home,
        Some(&local),
    )
    .expect("valid development layout");
    let fixture = InstallPaths::for_user(Target::WindowsX64, Channel::Fixture, &home, Some(&local))
        .expect("valid fixture layout");
    assert_eq!(
        development.root(),
        local.join("Veles").join(ROOT_DIR_DEVELOPMENT)
    );
    assert_eq!(fixture.root(), local.join("Veles").join(ROOT_DIR_FIXTURE));
    assert_ne!(production.root(), development.root());
    assert_ne!(production.root(), fixture.root());
    assert_ne!(development.root(), fixture.root());
    assert!(
        path_is_within(production.root(), production.versions()),
        "components below the root are inside it"
    );
    assert!(
        !path_is_within(production.root(), production.root()),
        "the root is not strictly inside itself"
    );
    assert!(
        !path_is_within(production.root(), &local.join("Veles/NativeBridgeDev")),
        "containment is a component boundary, not a string prefix"
    );
    assert_eq!(
        fixture.registration_name(),
        "me.nagaev.veles.bridge.fixture",
        "the fixture uses the Task 2 fixture identity"
    );
    assert_eq!(
        fixture.registration_path(),
        fixture.root().join("me.nagaev.veles.bridge.fixture.json")
    );

    let version = parse_version("1.2.3").expect("valid version");
    let payload = Digest::of(b"payload");
    assert_eq!(
        production.version_dir(&version, payload),
        production
            .versions()
            .join(format!("1.2.3-{}", payload.to_hex())),
        "version directories are the release and payload identity"
    );

    // macOS uses the home directory; LocalAppData is Windows-only and must not
    // be silently ignored.
    let mac = InstallPaths::for_user(Target::MacosArm64, Channel::Production, &home, None)
        .expect("valid macOS layout");
    assert_eq!(
        mac.root(),
        home.join("Library/Application Support/Veles/NativeBridge")
    );
    assert_eq!(
        mac.registration_path(),
        home.join("Library/Application Support/Google/Chrome/NativeMessagingHosts/me.nagaev.veles.bridge.json")
    );
    assert_eq!(
        failure_code(InstallPaths::for_user(
            Target::MacosArm64,
            Channel::Production,
            &home,
            Some(&local)
        )),
        FailureCode::InvalidInput,
        "a Windows-only injected root is rejected on macOS"
    );

    // Missing or relative injected roots are refused; there is no environment
    // variable fallback.
    assert_eq!(
        failure_code(InstallPaths::for_user(
            Target::WindowsX64,
            Channel::Production,
            &home,
            None
        )),
        FailureCode::InvalidInput
    );
    assert_eq!(
        failure_code(InstallPaths::for_user(
            Target::WindowsX64,
            Channel::Production,
            Path::new("relative/home"),
            Some(&local)
        )),
        FailureCode::InvalidInput
    );
    assert_eq!(
        failure_code(InstallPaths::for_user(
            Target::WindowsX64,
            Channel::Production,
            &home,
            Some(Path::new("relative/local"))
        )),
        FailureCode::InvalidInput
    );
    assert_eq!(
        failure_code(InstallPaths::for_user(
            Target::WindowsX64,
            Channel::Production,
            &home,
            Some(&local.join(".."))
        )),
        FailureCode::InvalidInput
    );

    // The lock is exclusive per user and nonblocking: a second acquisition is
    // Busy, and release makes the same path lockable again.
    let platform = PortablePlatform::new();
    let lock = platform.lock(&production).expect("first exclusive lock");
    assert_eq!(
        failure_code(platform.lock(&production)),
        FailureCode::Busy,
        "a concurrent installer observes Busy"
    );
    drop(lock);
    let second = platform
        .lock(&production)
        .expect("the lock is reusable after release");
    assert_eq!(second.path(), production.lock_path());
}
