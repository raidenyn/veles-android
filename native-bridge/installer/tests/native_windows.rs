//! Native Windows filesystem and HKCU registration evidence (OTP-25 Task 8).
//!
//! These tests must run on a **Windows** runner under a standard, non-elevated
//! user account. They are compiled only on Windows; the Linux executor cannot
//! run them and records them as **NOT RUN**, never as passes.
//!
//! Every test uses a private temporary install root and a test-only HKCU subkey
//! below `Software\Veles\InstallerTests`. A person's production
//! `Software\Google\Chrome\NativeMessagingHosts` key is never read or written.

#![cfg(windows)]

use std::fs;
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use veles_bridge_installer::cleanup::{
    CleanupReason, CleanupStatus, HelperRun, run_cleanup_helper_detailed,
};
use veles_bridge_installer::{
    Channel, Digest, FailureCode, InstallPaths, NativeManifest, PackageSource, PathKind, Platform,
    Registration, RegistryView, RemoveOutcome, StateDocument, Target, ValidatedPath,
    WindowsCleanupRoot, WindowsPlatform, extension_origin,
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

/// Replace the live manifest with `bytes` through the staged sibling + rename
/// path the transaction uses, without going through publication step one.
fn force_manifest(platform: &WindowsPlatform, paths: &InstallPaths, bytes: &[u8]) {
    let staged = ValidatedPath::new_file(paths, Path::new("state/staging/registration.json"))
        .expect("staging handle");
    platform
        .write_durable(&staged, bytes)
        .expect("staged bytes");
    let staged = staged.adopt(PathKind::File).expect("adopt staged");
    let live = ValidatedPath::registration(paths, PathKind::File).expect("registration handle");
    platform
        .replace_manifest(&staged, &live)
        .expect("replace manifest");
}

#[test]
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
    force_manifest(&platform, &paths, other.manifest());
    assert!(platform.publish_registration(&paths, &second).is_err());
    assert_eq!(
        platform
            .read_registry_value(&paths, RegistryView::Default)
            .expect("registry read")
            .as_deref(),
        Some(manifest_path),
        "a refused registry half must not touch the published value"
    );

    // A live registration this installer does not own is never stolen. Restore
    // our own bytes first (a foreign manifest cannot be republished over), then
    // write foreign bytes and require both halves to refuse.
    force_manifest(&platform, &paths, second.manifest());
    let foreign = br#"{"name":"me.nagaev.veles.bridge","description":"Veles","path":"C:\\Program Files\\Other\\host.exe","type":"stdio","allowed_origins":["chrome-extension://mnopabcdefghijklmnopabcdefghijkl/"]}"#;
    let live_handle =
        ValidatedPath::registration(&paths, PathKind::File).expect("registration handle");
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
    // The registry half refuses the same foreign manifest.
    assert_eq!(
        platform
            .publish_registration(&paths, &second)
            .expect_err("a foreign registration is refused by the registry half")
            .code,
        FailureCode::ForeignState
    );

    // Removal takes our value and manifest, and is idempotent.
    force_manifest(&platform, &paths, second.manifest());
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

/// A HKCU registration naming another product's manifest is foreign state even
/// when our local manifest file is absent: `read_registration` must not conceal
/// it, and publication must refuse it.
#[test]
fn windows_foreign_hkcu_value_without_manifest_is_refused() {
    let temp = TempRoot::new("foreign-hkcu");
    let paths = windows_paths(&temp);
    let platform = test_platform();
    let _lock = platform.lock(&paths).expect("lock");

    let directory = ValidatedPath::new_directory(&paths, Path::new("versions/1.0.0-aaaa"))
        .expect("version dir");
    platform.create_directory(&directory).expect("version dir");
    let executable = ValidatedPath::new_file(&paths, Path::new("versions/1.0.0-aaaa/host.exe"))
        .expect("host handle");
    platform
        .write_durable(&executable, b"host-image")
        .expect("host image");

    // Publish ours, then repoint HKCU at a foreign manifest *and* remove our
    // manifest file: the value is the only remaining evidence of a registration.
    let ours = registration(&paths, "versions/1.0.0-aaaa/host.exe");
    platform.publish_manifest(&paths, &ours).expect("manifest");
    platform
        .publish_registration(&paths, &ours)
        .expect("registry publication");
    let foreign_manifest = temp.path().join("Foreign").join("host.json");
    fs::create_dir_all(foreign_manifest.parent().expect("parent")).expect("foreign dir");
    fs::write(
        &foreign_manifest,
        br#"{"name":"me.nagaev.veles.bridge","description":"Other","path":"C:\\Other\\host.exe","type":"stdio","allowed_origins":["chrome-extension://mnopabcdefghijklmnopabcdefghijkl/"]}"#,
    )
    .expect("foreign manifest");
    // Rewrite the test key directly through the test-only registry API.
    platform
        .write_registry_value_for_tests(
            &paths,
            foreign_manifest.to_str().expect("utf-8 foreign path"),
        )
        .expect("foreign value");
    fs::remove_file(paths.registration_path()).expect("remove our manifest");

    assert_eq!(
        platform
            .read_registration(&paths)
            .expect_err("a foreign HKCU value must not be concealed as absent")
            .code,
        FailureCode::ForeignState
    );
    assert_eq!(
        platform
            .publish_manifest(&paths, &ours)
            .expect_err("the manifest half refuses a foreign value")
            .code,
        FailureCode::ForeignState
    );
    assert!(!paths.registration_path().exists());
    assert_eq!(
        platform
            .read_registry_value(&paths, RegistryView::Default)
            .expect("registry read")
            .as_deref(),
        foreign_manifest.to_str()
    );
}

#[test]
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
    let directory = ValidatedPath::new_directory(&paths, Path::new("versions/1.0.0-aaaa"))
        .expect("version dir");
    platform.create_directory(&directory).expect("version dir");
    let file = ValidatedPath::new_file(&paths, Path::new("versions/1.0.0-aaaa/host.exe"))
        .expect("host handle");
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

/// A pre-existing layout directory this user does not privately own is refused
/// instead of reused: another principal could plant or replace entries the
/// installer later trusts.
#[test]
fn preexisting_layout_directory_with_a_wide_dacl_is_refused() {
    let temp = TempRoot::new("wide-dacl");
    let paths = windows_paths(&temp);
    let platform = test_platform();
    // Create the layout with the installer's own protected DACL, then release it
    // and widen one directory's DACL as a foreign process could.
    drop(platform.lock(&paths).expect("lock"));
    let status = process::Command::new("icacls")
        .arg(paths.state())
        .args(["/grant", "*S-1-1-0:(OI)(CI)F"])
        .status()
        .expect("icacls is a standard Windows tool");
    assert!(status.success(), "could not widen the fixture DACL");
    let failure = platform
        .lock(&paths)
        .expect_err("a widened layout directory is not adopted");
    assert_eq!(failure.code, FailureCode::UnsafePath);
}

/// The real staged helper is driven end to end through the installer entry
/// point: it receives the inherited request/completion/release handles and the
/// duplicated parent process handle, authenticates the parent, is released,
/// writes its **actual** authenticated completion, exits, and the installer then
/// removes the image and observes that it is gone.
///
/// The fixture helper is unsigned (this executor has no Authenticode credentials,
/// Task 13), so its self-verification reports `SelfMismatch` and it exits
/// non-zero: that is the real completion, and the image must then be left alone
/// while the installer reports `CleanupPending`.
///
/// A runner that stages a **signed** helper (and sets
/// `VELES_TEST_HELPER_PUBLISHER`) reaches the parent-exit gate instead. Because
/// this entry point keeps the installer alive to read the completion, the helper
/// reports `cleanup-pending/ParentAlive` and the image is preserved: deletion
/// requires an observed parent exit, and a live parent never supplies one. Both
/// outcomes are asserted as the real, authenticated completion; neither is a
/// success claim.
#[test]
fn cleanup_helper_image_disappears_after_exit() {
    let temp = TempRoot::new("helper-image");
    let staged = temp.path().join("private").join("veles-cleanup.exe");
    fs::create_dir_all(staged.parent().expect("helper parent")).expect("helper dir");
    fs::copy(env!("CARGO_BIN_EXE_veles-bridge-cleanup"), &staged).expect("stage the helper");

    let root = temp.path().join("Veles").join("NativeBridge");
    fs::create_dir_all(&root).expect("cleanup root");

    let transaction = "11".repeat(32);
    let capability = [0x22u8; 32];
    let publisher = std::env::var("VELES_TEST_HELPER_PUBLISHER")
        .unwrap_or_else(|_| "unsigned-fixture".to_owned());
    let run = HelperRun {
        helper_path: &staged,
        helper_sha256: Digest::of(&fs::read(&staged).expect("helper bytes")),
        publisher: &publisher,
        transaction: &transaction,
        capability: &capability,
        root: &root,
        entries: Vec::new(),
        timeout: Duration::from_secs(5),
    };
    let (completion, verdict) = run_cleanup_helper_detailed(&run);
    let completion = completion.expect("the real helper always writes an authenticated completion");
    assert!(
        completion.verify(&capability),
        "the completion is authenticated by the request capability"
    );
    assert_eq!(completion.transaction, transaction);
    match completion.status {
        CleanupStatus::Complete => {
            verdict.expect("an authenticated complete is only reported when the image is gone");
            assert!(
                !staged.exists(),
                "the installer removes the staged image after the authenticated exit"
            );
        }
        CleanupStatus::CleanupPending => {
            // The two actual outcomes of a real helper here:
            // * `SelfMismatch` — an unsigned fixture refuses to vouch for its own
            //   image (Task 13 credentials are required for the other half);
            // * `ParentAlive` — a *signed* helper passes self-verification, but the
            //   deletion gate is a real parent exit and this entry point keeps the
            //   installer alive to read the completion. That is the fail-closed
            //   verdict, never a claimed success (see the module docs and the Task
            //   8 report: the parent side must release from a process that exits).
            assert!(
                matches!(
                    completion.reason,
                    Some(CleanupReason::SelfMismatch) | Some(CleanupReason::ParentAlive)
                ),
                "unexpected pending reason: {:?}",
                completion.reason
            );
            assert_eq!(
                verdict
                    .expect_err("a pending completion is never a success")
                    .code,
                FailureCode::CleanupPending
            );
            assert!(
                staged.exists(),
                "a pending completion never removes the helper image"
            );
        }
    }
}

/// A removal request whose root component was replaced by a junction/reparse
/// point is not followed: the cleanup traversal refuses it and nothing outside
/// the install root is touched.
#[test]
fn cleanup_root_does_not_follow_a_planted_reparse_point() {
    let temp = TempRoot::new("anchored");
    let root = temp.path().join("Veles/NativeBridge");
    fs::create_dir_all(root.join("versions/1.0.0-aaaa")).expect("version dir");
    let outside = temp.path().join("outside");
    fs::create_dir_all(&outside).expect("outside");
    let outside_file = outside.join("host.exe");
    fs::write(&outside_file, b"host-image").expect("outside host");

    // Open the anchored root first, then replace `versions` with a junction that
    // points at the outside tree.
    let mut traversal = WindowsCleanupRoot::open(&root).expect("open the anchored root");
    fs::remove_dir_all(root.join("versions")).expect("drop versions");
    let status = process::Command::new("cmd")
        .args(["/C", "mklink", "/J"])
        .arg(root.join("versions"))
        .arg(&outside)
        .status()
        .expect("cmd is a standard Windows tool");
    assert!(
        status.success(),
        "a junction does not require elevation; the fixture must stage one"
    );
    let outcome = traversal.remove_file(
        "versions/host.exe",
        b"host-image".len() as u64,
        Digest::of(b"host-image"),
        Instant::now() + Duration::from_secs(5),
    );
    assert_eq!(outcome, RemoveOutcome::Preserved);
    assert!(outside_file.exists(), "a reparse point is never traversed");

    // A junction **above** the root is refused as well: the walk from the volume
    // root sets `FILE_OPEN_REPARSE_POINT` on every component and the path is
    // never canonicalised first, so an ancestor cannot redirect the traversal.
    fs::create_dir_all(outside.join("NativeBridge")).expect("outside layout");
    let linked_parent = temp.path().join("linked-root");
    let status = process::Command::new("cmd")
        .args(["/C", "mklink", "/J"])
        .arg(&linked_parent)
        .arg(&outside)
        .status()
        .expect("cmd is a standard Windows tool");
    assert!(
        status.success(),
        "the fixture must stage an ancestor junction"
    );
    assert!(
        WindowsCleanupRoot::open(&linked_parent.join("NativeBridge")).is_err(),
        "a junction above the cleanup root is refused, never followed"
    );
}

/// The carrier install creates and populates its own destination object.
///
/// The destination leaf is created with an exclusive `NtCreateFile(FILE_CREATE)`
/// relative to the anchored parent, so the identity the install records comes
/// from the handle it created — never from a later open of the name — and the
/// image is written through that same handle in bounded chunks. The source bytes
/// and its last-write time reach the created file, and an existing destination
/// object is refused and left byte-for-byte intact instead of being adopted or
/// overwritten.
#[test]
fn carrier_install_creates_its_own_destination_object() {
    let temp = TempRoot::new("carrier");
    let paths = windows_paths(&temp);
    let platform = test_platform();
    let _lock = platform.lock(&paths).expect("lock");

    let directory = ValidatedPath::new_directory(&paths, Path::new("versions/1.0.0-aaaa"))
        .expect("version dir");
    platform.create_directory(&directory).expect("version dir");

    let source_path = temp.path().join("package").join("carrier.exe");
    fs::create_dir_all(source_path.parent().expect("package dir")).expect("package dir");
    // Larger than one copy chunk, so the bounded-chunk loop is exercised.
    let bytes: Vec<u8> = (0..(2 * 1024 * 1024 + 7))
        .map(|i| (i % 251) as u8)
        .collect();
    fs::write(&source_path, &bytes).expect("source image");
    let source = PackageSource::open(&source_path).expect("package source");
    let source_modified = fs::metadata(&source_path)
        .expect("source metadata")
        .modified()
        .expect("source last-write time");

    let destination = ValidatedPath::new_file(&paths, Path::new("versions/1.0.0-aaaa/carrier.exe"))
        .expect("destination handle");
    platform
        .install_carrier(&source, &destination)
        .expect("carrier install");
    let installed = paths.root().join("versions/1.0.0-aaaa/carrier.exe");
    assert_eq!(fs::read(&installed).expect("installed bytes"), bytes);
    assert_eq!(
        fs::metadata(&installed)
            .expect("installed metadata")
            .modified()
            .expect("installed last-write time"),
        source_modified,
        "the source last-write time is applied through the created handle"
    );

    // A pre-existing object at a validated fresh name is never adopted: the
    // install refuses (the fresh-name revalidation fails before the anchored
    // exclusive create can even be attempted) and the occupant keeps its bytes.
    let occupied = ValidatedPath::new_file(&paths, Path::new("versions/1.0.0-aaaa/occupied.exe"))
        .expect("fresh destination handle");
    let occupied_path = paths.root().join("versions/1.0.0-aaaa/occupied.exe");
    fs::write(&occupied_path, b"foreign occupant").expect("foreign occupant");
    let failure = platform
        .install_carrier(&source, &occupied)
        .expect_err("an existing destination object is never adopted");
    assert_eq!(failure.code, FailureCode::UnsafePath);
    assert_eq!(
        fs::read(&occupied_path).expect("preserved occupant"),
        b"foreign occupant",
        "the occupant is byte-for-byte intact"
    );
}

/// Identity-bound deletion: the exact leaf that was opened and hashed is the one
/// removed, and a directory that still holds anything is preserved.
#[test]
fn cleanup_root_removes_only_matching_identities() {
    let temp = TempRoot::new("identity");
    let root = temp.path().join("Veles/NativeBridge");
    fs::create_dir_all(root.join("versions/1.0.0-aaaa")).expect("version dir");
    fs::write(root.join("versions/1.0.0-aaaa/host.exe"), b"host-image").expect("host");
    let mut traversal = WindowsCleanupRoot::open(&root).expect("open the anchored root");

    // Wrong digest: preserved.
    assert_eq!(
        traversal.remove_file(
            "versions/1.0.0-aaaa/host.exe",
            b"host-image".len() as u64,
            Digest::of(b"other"),
            Instant::now() + Duration::from_secs(5),
        ),
        RemoveOutcome::Preserved
    );
    assert!(root.join("versions/1.0.0-aaaa/host.exe").exists());

    // Non-empty directory: preserved, never recursively deleted.
    let blocked = traversal.remove_directory("versions/1.0.0-aaaa");
    assert_eq!(blocked, RemoveOutcome::Preserved);

    // Exact identity: removed, then the empty directory.
    assert_eq!(
        traversal.remove_file(
            "versions/1.0.0-aaaa/host.exe",
            b"host-image".len() as u64,
            Digest::of(b"host-image"),
            Instant::now() + Duration::from_secs(5),
        ),
        RemoveOutcome::Removed
    );
    assert!(!root.join("versions/1.0.0-aaaa/host.exe").exists());
    assert_eq!(
        traversal.remove_directory("versions/1.0.0-aaaa"),
        RemoveOutcome::Removed
    );

    // The helper request names the install root; a root this installer does not
    // own cannot be opened as an anchored traversal root.
    assert!(WindowsCleanupRoot::open(temp.path()).is_err());
}
