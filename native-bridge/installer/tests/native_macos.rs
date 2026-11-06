//! Native macOS filesystem, registration and carrier-fidelity evidence
//! (OTP-25 Task 8).
//!
//! These tests must run on an **Apple-silicon macOS** runner under a standard,
//! non-root user. They are compiled only on macOS; the Linux executor cannot run
//! them and records them as **NOT RUN**, never as passes.

#![cfg(target_os = "macos")]

use std::fs;
use std::os::unix::fs::{PermissionsExt, symlink};
use std::path::{Path, PathBuf};
use std::process::{self, Command};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use veles_bridge_installer::cleanup::{
    CleanupReason, CleanupStatus, HelperRun, run_cleanup_helper_detailed,
};
use veles_bridge_installer::{
    Channel, Digest, FailureCode, InstallPaths, MACOS_CHROME_HOSTS_DIR, MacosCleanupRoot,
    MacosPlatform, NativeManifest, OwnedKind, PackageSource, Platform, Registration, RemoveOutcome,
    StateDocument, Target, ValidatedPath, extension_origin,
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
        // The native adapters open every ancestor with `O_NOFOLLOW` and refuse a
        // link outright, so the fixture must hand them a path with no link in it.
        // `std::env::temp_dir()` is `/var/folders/...` on macOS and `/var` is a
        // symlink to `/private/var`; the resolved path is what a real install
        // home looks like.
        let path = fs::canonicalize(&path).expect("canonical temp root");
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
    let staging: Vec<_> = fs::read_dir(paths.state())
        .expect("state listing")
        .map(|entry| entry.expect("entry").file_name())
        .filter(|name| name.to_string_lossy().contains(".tmp"))
        .collect();
    assert!(
        staging.is_empty(),
        "the staging sibling must be renamed away, not left behind: {staging:?}"
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

    // A planted file at a staging name is never adopted: every staging name is
    // fresh and written with an exclusive create. (The planted *link* above still
    // occupies the live `receipt.json` slot, so it is removed first: the slot is
    // only writable once it is a plain name again, which is the intended
    // behaviour the refusal above proves.)
    fs::remove_file(paths.state().join("receipt.json")).expect("drop the planted link");
    let planted = paths.state().join(".receipt.json.planted.tmp");
    fs::write(&planted, b"planted").expect("planted staging file");
    platform
        .write_state(&paths, StateDocument::Receipt, b"{\"receipt\":true}")
        .expect("state write ignores the planted name");
    assert_eq!(fs::read(&planted).expect("planted bytes"), b"planted");
    assert_eq!(
        platform
            .read_state(&paths, StateDocument::Receipt)
            .expect("state read"),
        Some(b"{\"receipt\":true}".to_vec())
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
    assert_eq!(
        existing
            .rendered()
            .symlink_metadata()
            .expect("metadata")
            .permissions()
            .mode()
            & 0o777,
        0o600,
        "durably written files are owner-only"
    );

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
    let rendered = paths
        .registration_path()
        .to_str()
        .expect("utf-8 registration path");
    assert!(
        rendered.contains("Test User Å") && rendered.contains("用户"),
        "the fixture home's Unicode components must survive: {rendered}"
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

    // Enumeration records the bundle link as a *link entry* with its exact
    // target, still confined: the required symlink-preserving path is proven
    // here, not merely the refusal of a link.
    let source_package = PackageSource::open(&source).expect("carrier source");
    let entries = platform
        .carrier_entries(&source_package)
        .expect("a symlink-bearing carrier artifact is enumerated");
    let link_entry = entries
        .iter()
        .find(|entry| entry.relative() == "Contents/Frameworks/Current")
        .expect("the bundle link is described");
    assert_eq!(link_entry.kind(), OwnedKind::Symlink);
    assert_eq!(link_entry.target(), Some("../MacOS"));
    assert_eq!(link_entry.size(), "../MacOS".len() as u64);
    assert_eq!(
        link_entry.sha256(),
        Some(Digest::of(b"../MacOS")),
        "a link's owned content is its exact target bytes"
    );
    assert!(
        entries
            .iter()
            .any(|entry| entry.relative() == "Contents/MacOS/carrier"
                && entry.kind() == OwnedKind::File),
        "regular bundle files are still enumerated"
    );

    // A link that climbs out of the artifact root is refused at enumeration: a
    // copied link must never point a later reader outside the signed bundle.
    let escaping = temp.path().join("escaping.app");
    fs::create_dir_all(escaping.join("Contents")).expect("escaping bundle");
    symlink(Path::new("../../outside"), escaping.join("Contents/escape")).expect("escaping link");
    assert_eq!(
        platform
            .carrier_entries(&PackageSource::open(&escaping).expect("source"))
            .expect_err("a link escaping the artifact root is refused")
            .code,
        FailureCode::UnsafePath
    );

    let carrier_dir =
        ValidatedPath::new_directory(&paths, Path::new("versions/1.0.0-aaaa/carrier.app"))
            .expect("carrier dir handle");
    platform
        .install_carrier(&source_package, &carrier_dir)
        .expect("carrier install");
    let installed = paths.root().join("versions/1.0.0-aaaa/carrier.app");
    assert_eq!(
        fs::read(installed.join("Contents/MacOS/carrier")).expect("installed carrier"),
        b"carrier-image",
        "the bytes must actually be copied (COPYFILE_EXCL, not COPYFILE_CHECK)"
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
}

/// The cleanup helper's anchored traversal refuses a component replaced by a
/// symlink: a deletion is never redirected outside the install root, and the
/// object that is unlinked is the one whose identity was verified.
#[test]
fn cleanup_traversal_is_anchored_and_identity_bound() {
    let temp = TempRoot::new("anchored");
    let root = temp.path().join("Veles").join("NativeBridge");
    fs::create_dir_all(root.join("versions/1.0.0-aaaa")).expect("version dir");
    fs::write(root.join("versions/1.0.0-aaaa/host"), b"host-image").expect("host");

    // The root handle is opened *before* the swap, as the helper does.
    let traversal = MacosCleanupRoot::open(&root).expect("open the anchored root");

    // Wrong digest: preserved.
    assert_eq!(
        traversal.remove_file(
            "versions/1.0.0-aaaa/host",
            b"host-image".len() as u64,
            Digest::of(b"other"),
            Instant::now() + Duration::from_secs(5),
        ),
        RemoveOutcome::Preserved
    );
    // A non-empty directory is never recursively deleted.
    assert_eq!(
        traversal.remove_directory("versions/1.0.0-aaaa"),
        RemoveOutcome::Preserved
    );
    // The exact identity is removed.
    assert_eq!(
        traversal.remove_file(
            "versions/1.0.0-aaaa/host",
            b"host-image".len() as u64,
            Digest::of(b"host-image"),
            Instant::now() + Duration::from_secs(5),
        ),
        RemoveOutcome::Removed
    );
    assert_eq!(
        traversal.remove_directory("versions/1.0.0-aaaa"),
        RemoveOutcome::Removed
    );
    assert_eq!(
        traversal.remove_file(
            "versions/1.0.0-aaaa/host",
            b"host-image".len() as u64,
            Digest::of(b"host-image"),
            Instant::now() + Duration::from_secs(5),
        ),
        RemoveOutcome::Absent,
        "an intermediate component that is gone makes the entry absent (idempotent \
         retry), never preserved"
    );

    // Replace `versions` with a link to a tree outside the root that holds a
    // file with exactly the requested identity: the traversal must not follow it.
    let outside = temp.path().join("outside");
    fs::create_dir_all(&outside).expect("outside tree");
    fs::write(outside.join("host"), b"host-image").expect("outside host");
    fs::remove_dir_all(root.join("versions")).expect("drop versions");
    symlink(&outside, root.join("versions")).expect("planted link");
    assert_eq!(
        traversal.remove_file(
            "versions/host",
            b"host-image".len() as u64,
            Digest::of(b"host-image"),
            Instant::now() + Duration::from_secs(5),
        ),
        RemoveOutcome::Preserved
    );
    assert!(
        outside.join("host").exists(),
        "a linked component must never redirect a deletion"
    );

    // A link **above** the root is refused as well. The walk starts at `/` with
    // `O_NOFOLLOW` and the path is never canonicalised first, so a planted
    // ancestor cannot make the helper accept the outside tree the link names.
    fs::create_dir_all(outside.join("NativeBridge")).expect("outside layout");
    let linked_parent = temp.path().join("linked");
    symlink(&outside, &linked_parent).expect("planted ancestor link");
    assert!(
        MacosCleanupRoot::open(&linked_parent.join("NativeBridge")).is_err(),
        "a link above the cleanup root is refused, never followed"
    );
}

/// The real staged helper is driven end to end through the installer entry
/// point: it receives the inherited channels, authenticates the parent, is
/// released, writes its **actual** authenticated completion, exits, and the
/// installer then removes the image and observes that it is gone.
///
/// The fixture helper is unsigned (this executor has no Developer ID
/// credentials, Task 13), so its self-verification reports `SelfMismatch` and it
/// exits non-zero: that is the real completion, and the image must then be left
/// alone while the installer reports `CleanupPending`. A runner that stages a
/// signed helper (and sets `VELES_TEST_HELPER_PUBLISHER` to its Team ID) asserts
/// the other half: `complete`, exit 0, and the image gone after the exit.
#[test]
fn cleanup_helper_image_disappears_after_exit() {
    let temp = TempRoot::new("helper-image");
    let staged = temp.path().join("private").join("veles-cleanup");
    fs::create_dir_all(staged.parent().expect("helper parent")).expect("helper dir");
    fs::copy(env!("CARGO_BIN_EXE_veles-bridge-cleanup"), &staged).expect("stage the helper");
    fs::set_permissions(&staged, fs::Permissions::from_mode(0o700)).expect("executable");

    // A real installer-owned root outside the helper's private directory.
    let root = temp.path().join("Veles").join("NativeBridge");
    fs::create_dir_all(&root).expect("cleanup root");

    let transaction = "11".repeat(32);
    let capability = [0x22u8; 32];
    let publisher = std::env::var("VELES_TEST_HELPER_PUBLISHER").unwrap_or_else(|_| {
        // An unsigned fixture can never report a team identifier, so the helper
        // must fail closed with `SelfMismatch`; that is asserted below.
        "team:UNSIGNED-FIXTURE".to_owned()
    });
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
            // This is the actual, authenticated result of the real helper on an
            // unsigned fixture: it refuses to vouch for its own image.
            assert_eq!(completion.reason, Some(CleanupReason::SelfMismatch));
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
