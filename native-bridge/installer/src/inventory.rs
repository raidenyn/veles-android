//! Strict payload-inventory verification.
//!
//! An inventory is authenticated package data. Before any file is opened, every
//! path is normalized and bounded: relative components only, no drive prefix,
//! alternate data stream, NUL/control character, Windows separator alias,
//! trailing-dot/space alias or case-folded duplicate. Only then are sizes and
//! SHA-256 digests checked against the actual files below a caller-supplied root.

use std::collections::HashSet;
use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

use crate::model::{
    Digest, FailureCode, InstallFailure, InstallResult, InventoryEntry, MAX_DOCUMENT_BYTES,
    MAX_INVENTORY_BYTES, MAX_INVENTORY_ENTRIES, MAX_PATH_BYTES, canonical_json,
};

/// The only supported package-inventory document schema generation.
pub const INVENTORY_SCHEMA_VERSION: u32 = 1;
/// Package inventory document file name at the package root.
pub const PACKAGE_INVENTORY_FILE: &str = "inventory.json";
/// Payload directory name at the package root.
pub const PACKAGE_PAYLOAD_DIR: &str = "payload";
/// Sealed signed-byte inventory document at the package root.
///
/// Present exactly when signing changed the bytes of at least one payload file.
/// `inventory.json` is then still the *unsigned canonical* identity (its digest
/// is the descriptor's `payload_id`); this document lists the bytes actually
/// shipped and installed, and its digest is asserted by the OS-authenticated
/// carrier. See [`verify_correspondence`].
pub const SEALED_INVENTORY_FILE: &str = "sealed-inventory.json";

/// The strict package-inventory document.
///
/// The descriptor's `payload_id` is the SHA-256 of the *exact bytes* of this
/// document as produced by [`write_inventory`]; readers never re-serialize
/// untrusted input to recompute it. No public constructor exists, so an
/// inventory document cannot be assembled without the same normalization and
/// bound checks `verify_inventory` applies.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct InventoryDocument {
    schema: u32,
    entries: Vec<InventoryEntry>,
}

/// Normalize and validate one relative inventory path.
///
/// The accepted form is the canonical one: forward-slash separated, no empty,
/// `.` or `..` component, no component ending in `.` or a space, and no
/// platform alias characters. The returned value is the canonical rendering used
/// to join against the install root.
pub fn normalize_relative_path(path: &str) -> InstallResult<String> {
    if path.is_empty() || path.len() > MAX_PATH_BYTES {
        return Err(unsafe_path());
    }
    if path.starts_with('/')
        || path.contains('\\')
        || path.contains(':')
        || path.chars().any(char::is_control)
    {
        return Err(unsafe_path());
    }
    let mut normalized = String::with_capacity(path.len());
    for (index, component) in path.split('/').enumerate() {
        if component.is_empty()
            || component == "."
            || component == ".."
            || component.ends_with('.')
            || component.ends_with(' ')
        {
            return Err(unsafe_path());
        }
        if index > 0 {
            normalized.push('/');
        }
        normalized.push_str(component);
    }
    Ok(normalized)
}

/// Verify that every entry matches a regular file below `root`.
///
/// This is a declared-entry check, **not** a tree enumeration: files below
/// `root` that are absent from `entries` are never noticed, and an empty
/// `entries` slice verifies against any directory. A caller that needs "the root
/// holds exactly this inventory" - for example to reject a planted DLL next to
/// the executable - must enumerate the tree itself and compare it with
/// `entries`; extra-file and enumeration checks are the caller's obligation
/// (`authenticate_package` in Task 4 does this for the package payload).
///
/// Structural violations (path policy, counts, total size) are rejected before
/// any file access; then each declared size and SHA-256 must match the file on
/// disk. Symlinks and non-regular files are never opened.
pub fn verify_inventory(root: &Path, entries: &[InventoryEntry]) -> InstallResult<()> {
    let normalized_paths = validate_entries(entries)?;

    let root_metadata = fs::symlink_metadata(root).map_err(|_| io_failure())?;
    if !root_metadata.is_dir() {
        return Err(io_failure());
    }

    for (entry, relative) in entries.iter().zip(normalized_paths) {
        verify_entry(root, &relative, entry)?;
    }
    Ok(())
}

/// Serialize the canonical package-inventory document for `entries`.
///
/// The returned exact bytes are the input whose SHA-256 is the release
/// descriptor's `payload_id`; this is the single document writer so packaging
/// and authentication cannot disagree about the encoding.
pub fn write_inventory(entries: &[InventoryEntry]) -> InstallResult<Vec<u8>> {
    validate_entries(entries)?;
    canonical_json(&InventoryDocument {
        schema: INVENTORY_SCHEMA_VERSION,
        entries: entries.to_vec(),
    })
}

/// Strictly parse a package-inventory document.
///
/// The document is data, never code. Unknown/duplicate fields, another schema,
/// out-of-bound counts/totals and non-canonical paths are rejected before any
/// filesystem access. The caller must bind the exact document bytes to an
/// authenticated digest before trusting the listed content.
pub fn parse_inventory(bytes: &[u8]) -> InstallResult<Vec<InventoryEntry>> {
    if bytes.is_empty() || bytes.len() > MAX_DOCUMENT_BYTES {
        return Err(invalid_package());
    }
    let document: InventoryDocument =
        serde_json::from_slice(bytes).map_err(|_| invalid_package())?;
    if document.schema != INVENTORY_SCHEMA_VERSION {
        return Err(invalid_package());
    }
    validate_entries(&document.entries)?;
    Ok(document.entries)
}

/// Whether the image behind `reader` (whose total length is `len`) is a PE or
/// Mach-O image, the only formats a code-signing step rewrites in place.
///
/// This is a classification, not a parser: it decides whether a changed digest is
/// *allowed to be* a signing mutation. Whether the change is exactly the
/// documented signature mutation is the release-time correspondence report's job
/// (`scripts/correspondence.py`); whether the signature is trusted is the OS's.
///
/// The work is bounded regardless of the file: at most 64 bytes of head, and for a
/// DOS (`MZ`) image one 4-byte read at the declared PE offset. The offset is a
/// `u32` the file itself supplies, so it is validated against `len` (the opened
/// handle's real size) *before* any seek; an offset whose signature would not fit
/// inside the file is simply "not a PE". A legitimate linker may place the PE
/// header anywhere the DOS stub leaves room for, including past the first page, so
/// no fixed prefix length is assumed.
pub fn is_signable_image<R: Read + Seek>(reader: &mut R, len: u64) -> InstallResult<bool> {
    reader.seek(SeekFrom::Start(0)).map_err(|_| io_failure())?;
    let mut head = [0u8; DOS_HEADER_BYTES];
    let available = len.min(DOS_HEADER_BYTES as u64) as usize;
    reader
        .read_exact(&mut head[..available])
        .map_err(|_| io_failure())?;
    let head = &head[..available];

    if head.len() >= DOS_HEADER_BYTES && head.starts_with(b"MZ") {
        let mut raw = [0u8; 4];
        raw.copy_from_slice(&head[0x3c..0x40]);
        let pe_offset = u64::from(u32::from_le_bytes(raw));
        // `pe_offset + 4 <= len`, written without overflow.
        if len < 4 || pe_offset > len - 4 {
            return Ok(false);
        }
        reader
            .seek(SeekFrom::Start(pe_offset))
            .map_err(|_| io_failure())?;
        let mut signature = [0u8; 4];
        reader
            .read_exact(&mut signature)
            .map_err(|_| io_failure())?;
        return Ok(&signature == b"PE\0\0");
    }
    Ok(matches!(
        head.get(..4),
        Some([0xcf, 0xfa, 0xed, 0xfe])
            | Some([0xfe, 0xed, 0xfa, 0xcf])
            | Some([0xca, 0xfe, 0xba, 0xbe])
    ))
}

/// Bytes of the DOS header that locate the PE header (`e_lfanew` at `0x3c`).
const DOS_HEADER_BYTES: usize = 0x40;

/// [`is_signable_image`] for one inventory entry, read through the anchored
/// traversal of [`open_entry`] on the opened handle itself.
pub(crate) fn entry_is_signable_image(root: &Path, relative: &str) -> InstallResult<bool> {
    let mut file = open_entry(root, relative)?;
    let len = file.metadata().map_err(|_| io_failure())?.len();
    is_signable_image(&mut file, len)
}

/// Whether `path` is a signature artifact a macOS signing step *adds*: the bundle
/// seal `Contents/_CodeSignature/CodeResources` or a stapled ticket
/// `Contents/CodeResources` inside a `*.app` bundle. These are the only paths the
/// Task 9 tree comparator allows to exist on the signed side only.
fn signature_artifact_kind(path: &str) -> Option<(String, bool)> {
    let parts: Vec<&str> = path.split('/').collect();
    let count = parts.len();
    let is_bundle = |name: &str| name.len() > 4 && name.ends_with(".app");
    if count >= 4
        && parts[count - 1] == "CodeResources"
        && parts[count - 2] == "_CodeSignature"
        && parts[count - 3] == "Contents"
        && is_bundle(parts[count - 4])
    {
        return Some((parts[..count - 3].join("/"), true));
    }
    if count >= 3
        && parts[count - 1] == "CodeResources"
        && parts[count - 2] == "Contents"
        && is_bundle(parts[count - 3])
    {
        return Some((parts[..count - 2].join("/"), false));
    }
    None
}

/// Verify that `sealed` (the signed bytes) corresponds to `canonical` (the
/// unsigned identity) in the only ways a signing step may change a package.
///
/// * Every canonical path exists in the sealed inventory with the same
///   `executable` bit; nothing is dropped or renamed.
/// * A path whose size or digest differs must be a signable image
///   (`is_signable_image`, queried on the *signed* file through `signable`).
/// * A path present only in the sealed inventory must be a bundle seal or a
///   stapled ticket beside a seal, and is never executable.
///
/// The check is structural on purpose. Neither inventory contains the unsigned
/// file bytes after signing, so the engine cannot prove the byte-level mutation
/// is exactly an Authenticode/Mach-O signature - that proof is the signing
/// gate's correspondence report. What the engine adds is the binding: the
/// canonical identity is authenticated by the descriptor, the sealed digest by the
/// carrier, and the two cannot disagree about which files exist, which are
/// executable, or which kinds of file changed.
pub fn verify_correspondence(
    canonical: &[InventoryEntry],
    sealed: &[InventoryEntry],
    mut signable: impl FnMut(&str) -> InstallResult<bool>,
) -> InstallResult<()> {
    validate_entries(canonical)?;
    validate_entries(sealed)?;
    let sealed_by_path: std::collections::HashMap<&str, &InventoryEntry> = sealed
        .iter()
        .map(|entry| (entry.path.as_str(), entry))
        .collect();
    let canonical_paths: HashSet<&str> =
        canonical.iter().map(|entry| entry.path.as_str()).collect();

    for entry in canonical {
        let Some(signed) = sealed_by_path.get(entry.path.as_str()) else {
            return Err(invalid_package());
        };
        if signed.executable != entry.executable {
            return Err(invalid_package());
        }
        if (signed.size, signed.sha256) != (entry.size, entry.sha256) && !signable(&entry.path)? {
            return Err(invalid_package());
        }
    }

    let mut seals: HashSet<String> = HashSet::new();
    let mut tickets: Vec<String> = Vec::new();
    for entry in sealed {
        if canonical_paths.contains(entry.path.as_str()) {
            continue;
        }
        let Some((bundle, is_seal)) = signature_artifact_kind(&entry.path) else {
            return Err(invalid_package());
        };
        if entry.executable {
            return Err(invalid_package());
        }
        if is_seal {
            seals.insert(bundle);
        } else {
            tickets.push(bundle);
        }
    }
    if tickets.iter().any(|bundle| !seals.contains(bundle)) {
        return Err(invalid_package());
    }
    Ok(())
}

/// Structural validation shared by every inventory reader/writer.
///
/// Returns the normalized `/`-joined paths in declaration order.
fn validate_entries(entries: &[InventoryEntry]) -> InstallResult<Vec<String>> {
    if entries.len() > MAX_INVENTORY_ENTRIES {
        return Err(invalid_input());
    }

    let mut normalized_paths = Vec::with_capacity(entries.len());
    let mut seen: HashSet<String> = HashSet::with_capacity(entries.len());
    let mut total: u64 = 0;
    for entry in entries {
        let normalized = normalize_relative_path(&entry.path)?;
        if !seen.insert(normalized.to_lowercase()) {
            // Duplicate member or a case-folded alias of an earlier member.
            return Err(unsafe_path());
        }
        total = total.checked_add(entry.size).ok_or_else(invalid_input)?;
        if total > MAX_INVENTORY_BYTES {
            return Err(invalid_input());
        }
        normalized_paths.push(normalized);
    }
    Ok(normalized_paths)
}

mod anchored;

/// Open one inventory entry for reading through an anchored traversal of `root`.
///
/// `root` is opened once without following a link, and each component is then
/// opened *relative to its parent's handle* with no-follow semantics (see
/// [`anchored`]): a link, junction or reparse point at any level is `UnsafePath`,
/// the leaf must be a regular file, and an ancestor swapped after it was opened
/// cannot redirect the read because the walk holds the handles it checked. A
/// missing component is `InvalidPackage` (the inventory names something the
/// package does not hold). The returned handle is the one to hash or assess.
pub(crate) fn open_entry(root: &Path, relative: &str) -> InstallResult<File> {
    open_entry_with(root, relative, &mut || {})
}

/// [`open_entry`] with a seam: `between` runs after every ancestor directory is open
/// and before the leaf is opened - the instant a racing writer would swap an
/// ancestor. Production passes a no-op.
pub(crate) fn open_entry_with(
    root: &Path,
    relative: &str,
    between: &mut dyn FnMut(),
) -> InstallResult<File> {
    let parts: Vec<&str> = relative.split('/').collect();
    anchored::open_regular_below(root, &parts, between)
}

/// [`open_entry`] for a file an OS trust assessment will read through the returned
/// handle (the Windows host assessment). Same anchored walk and failure vocabulary;
/// on Windows the final file is additionally held `FILE_SHARE_READ`-only so no
/// writer, deleter or renamer can change the assessed bytes while it is held.
#[cfg_attr(not(any(windows, test)), allow(dead_code))]
pub(crate) fn open_entry_for_assessment(root: &Path, relative: &str) -> InstallResult<File> {
    let parts: Vec<&str> = relative.split('/').collect();
    anchored::open_for_assessment_below(root, &parts, &mut || {})
}

fn verify_entry(root: &Path, relative: &str, entry: &InventoryEntry) -> InstallResult<()> {
    let mut file = open_entry(root, relative)?;
    let metadata = file.metadata().map_err(|_| io_failure())?;
    if metadata.len() != entry.size {
        return Err(invalid_package());
    }
    if hash_open_file(&mut file, entry.size)? != entry.sha256 {
        return Err(invalid_package());
    }
    Ok(())
}

/// SHA-256 of at most `limit + 1` bytes from an already open handle: the work is
/// bounded by the declared size even if the file grows while it is hashed.
fn hash_open_file(file: &mut File, limit: u64) -> InstallResult<Digest> {
    let mut reader = file.take(limit.saturating_add(1));
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let read = reader.read(&mut buffer).map_err(|_| io_failure())?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(Digest::from_bytes(hasher.finalize().into()))
}

fn invalid_input() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidInput)
}

fn invalid_package() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidPackage)
}

fn unsafe_path() -> InstallFailure {
    InstallFailure::new(FailureCode::UnsafePath)
}

fn io_failure() -> InstallFailure {
    InstallFailure::new(FailureCode::Io)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io;

    /// A synthetic image of `len` bytes that never materializes its content and
    /// counts every byte it is asked to produce.
    struct Synthetic {
        len: u64,
        position: u64,
        pe_offset: u32,
        served: u64,
    }

    impl Read for Synthetic {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            let mut produced = 0;
            while produced < buffer.len() && self.position < self.len {
                let at = self.position;
                buffer[produced] = match at {
                    0 => b'M',
                    1 => b'Z',
                    0x3c..=0x3f => self.pe_offset.to_le_bytes()[(at - 0x3c) as usize],
                    _ => {
                        let relative = at.wrapping_sub(u64::from(self.pe_offset));
                        if at >= u64::from(self.pe_offset) && relative < 4 {
                            b"PE\0\0"[relative as usize]
                        } else {
                            0
                        }
                    }
                };
                self.position += 1;
                produced += 1;
            }
            self.served += produced as u64;
            Ok(produced)
        }
    }

    impl Seek for Synthetic {
        fn seek(&mut self, to: SeekFrom) -> io::Result<u64> {
            match to {
                SeekFrom::Start(offset) => self.position = offset,
                _ => return Err(io::ErrorKind::Unsupported.into()),
            }
            Ok(self.position)
        }
    }

    #[test]
    fn classifying_a_pe_reads_a_constant_number_of_bytes_wherever_the_header_is() {
        for pe_offset in [0x40u32, 0x1000, 0x10_0000, 0x4000_0000] {
            let len = u64::from(pe_offset) + 0x200;
            let mut image = Synthetic {
                len,
                position: 0,
                pe_offset,
                served: 0,
            };
            assert!(is_signable_image(&mut image, len).expect("classified"));
            assert!(
                image.served <= (DOS_HEADER_BYTES as u64) + 4,
                "classification of a {len}-byte image read {} bytes",
                image.served
            );
        }
    }

    #[test]
    fn an_offset_whose_signature_does_not_fit_in_the_file_is_not_read_or_followed() {
        for (len, pe_offset) in [
            (0x40u64, 0x40u32),
            (0x80, 0x7e),
            (0x80, 0x7d),
            (0x80, u32::MAX),
        ] {
            let mut image = Synthetic {
                len,
                position: 0,
                pe_offset,
                served: 0,
            };
            let outcome = is_signable_image(&mut image, len).expect("classified");
            // Only offsets with four bytes of room inside the file can be a PE.
            assert_eq!(
                outcome,
                u64::from(pe_offset) + 4 <= len,
                "{len}/{pe_offset}"
            );
            assert!(image.served <= DOS_HEADER_BYTES as u64 + 4);
        }
    }

    #[test]
    fn short_and_foreign_files_are_not_signable() {
        for bytes in [
            &b""[..],
            b"M",
            b"MZ",
            b"#!/bin/sh\n",
            &[0xcf, 0xfa, 0xed][..],
        ] {
            let mut cursor = io::Cursor::new(bytes.to_vec());
            assert!(!is_signable_image(&mut cursor, bytes.len() as u64).expect("classified"));
        }
        let mut macho = io::Cursor::new(vec![0xcf, 0xfa, 0xed, 0xfe, 0, 0]);
        assert!(is_signable_image(&mut macho, 6).expect("classified"));
    }

    /// R4: the traversal must be anchored. A directory component swapped for a link
    /// to somewhere else *after* it was checked must not redirect the read.
    #[cfg(unix)]
    #[test]
    fn an_ancestor_swapped_after_it_was_checked_cannot_redirect_the_read() {
        let base = std::env::temp_dir().join(format!("veles-anchored-swap-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("payload");
        std::fs::create_dir_all(root.join("dir")).expect("package tree");
        std::fs::create_dir_all(base.join("outside")).expect("outside tree");
        std::fs::write(root.join("dir/host"), b"the package's host").expect("package host");
        std::fs::write(base.join("outside/host"), b"attacker-chosen bytes").expect("outside");

        let swap_root = root.clone();
        let outside = base.join("outside");
        let mut swapped = false;
        let outcome = open_entry_with(&root, "dir/host", &mut || {
            std::fs::rename(swap_root.join("dir"), swap_root.join("dir.moved")).expect("move");
            std::os::unix::fs::symlink(&outside, swap_root.join("dir")).expect("link");
            swapped = true;
        });
        assert!(
            swapped,
            "the seam ran between the ancestor checks and the leaf open"
        );
        let content = outcome.map(|mut file| {
            let mut bytes = Vec::new();
            file.read_to_end(&mut bytes).expect("read");
            bytes
        });
        let _ = std::fs::remove_dir_all(&base);
        match content {
            Err(_) => {}
            Ok(bytes) => assert_eq!(
                bytes, b"the package's host",
                "the read followed the swapped ancestor out of the payload root"
            ),
        }
    }

    /// The host-assessment open is the same anchored walk: an ancestor swapped after
    /// it was checked cannot redirect it, and every refusal keeps its code. (On
    /// Windows it additionally holds the leaf `FILE_SHARE_READ`-only; that is
    /// covered by the Windows-only test below, which this host cannot run.)
    #[cfg(unix)]
    #[test]
    fn the_assessment_open_keeps_the_anchored_walk() {
        use std::os::unix::fs::symlink;

        let base = std::env::temp_dir().join(format!("veles-assess-anchor-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("payload");
        std::fs::create_dir_all(root.join("dir")).expect("tree");
        std::fs::create_dir_all(base.join("outside")).expect("outside");
        std::fs::write(root.join("dir/host"), b"the package's host").expect("host");
        std::fs::write(base.join("outside/host"), b"attacker-chosen bytes").expect("outside");
        symlink(base.join("outside/host"), root.join("dir/linked")).expect("link");

        let mut bytes = Vec::new();
        open_entry_for_assessment(&root, "dir/host")
            .expect("an ordinary host opens")
            .read_to_end(&mut bytes)
            .expect("read");
        assert_eq!(bytes, b"the package's host");
        assert_eq!(
            open_entry_for_assessment(&root, "dir/linked")
                .expect_err("a linked leaf")
                .code,
            FailureCode::UnsafePath
        );
        assert_eq!(
            open_entry_for_assessment(&root, "dir/absent")
                .expect_err("absent")
                .code,
            FailureCode::InvalidPackage
        );

        // The same swap as the R4 test, through the assessment entry point.
        let parts = ["dir", "host"];
        let swap_root = root.clone();
        let outside = base.join("outside");
        let outcome = anchored::open_for_assessment_below(&root, &parts, &mut || {
            std::fs::rename(swap_root.join("dir"), swap_root.join("dir.moved")).expect("move");
            symlink(&outside, swap_root.join("dir")).expect("link");
        });
        let content = outcome.map(|mut file| {
            let mut bytes = Vec::new();
            file.read_to_end(&mut bytes).expect("read");
            bytes
        });
        let _ = std::fs::remove_dir_all(&base);
        if let Ok(bytes) = content {
            assert_eq!(
                bytes, b"the package's host",
                "followed the swapped ancestor"
            );
        }
    }

    /// N4 (Windows only - NOT RUN on this Linux host): the handle a host is assessed
    /// through excludes writers, deleters and renamers for as long as it is held, as
    /// the pre-anchoring `ReadOnlyFile::open` handle did, while the plain anchored
    /// read open (hashing) deliberately does not.
    #[cfg(windows)]
    #[test]
    fn the_assessment_handle_excludes_writers_deleters_and_renamers() {
        use std::os::windows::fs::OpenOptionsExt;

        let base = std::env::temp_dir().join(format!("veles-assess-share-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("payload");
        std::fs::create_dir_all(&root).expect("tree");
        let host = root.join("host.exe");
        std::fs::write(&host, b"host bytes").expect("host");

        let held = open_entry_for_assessment(&root, "host.exe").expect("assessment handle");
        let writer = std::fs::OpenOptions::new().write(true).open(&host);
        assert!(
            writer.is_err(),
            "a writer was admitted beside the assessment"
        );
        let deleter = std::fs::OpenOptions::new()
            .access_mode(0x0001_0000) // DELETE
            .open(&host);
        assert!(
            deleter.is_err(),
            "a deleter was admitted beside the assessment"
        );
        assert!(std::fs::remove_file(&host).is_err());
        assert!(std::fs::rename(&host, root.join("renamed.exe")).is_err());
        drop(held);
        // Control: once the assessment handle is released the file is writable again,
        // so the refusals above come from the held handle and nothing else.
        assert!(std::fs::OpenOptions::new().write(true).open(&host).is_ok());
        let _ = std::fs::remove_dir_all(&base);
    }

    /// The anchored walk keeps the failure vocabulary the callers (and the
    /// package-authentication tests) rely on.
    #[cfg(unix)]
    #[test]
    fn the_anchored_walk_classifies_every_refusal() {
        use std::os::unix::fs::symlink;

        let base =
            std::env::temp_dir().join(format!("veles-anchored-kinds-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("payload");
        std::fs::create_dir_all(root.join("dir")).expect("tree");
        std::fs::create_dir_all(base.join("outside")).expect("outside");
        std::fs::write(root.join("dir/file"), b"data").expect("file");
        std::fs::write(base.join("outside/file"), b"foreign").expect("outside file");
        symlink(base.join("outside"), root.join("linked-dir")).expect("dir link");
        symlink(base.join("outside/file"), root.join("dir/linked-file")).expect("file link");
        symlink(&root, base.join("linked-root")).expect("root link");
        // A FIFO leaf would block a blocking open forever.
        let fifo = root.join("dir/fifo");
        let status = std::process::Command::new("mkfifo").arg(&fifo).status();
        let have_fifo = status.is_ok_and(|status| status.success());

        let code = |root: &Path, relative: &str| {
            open_entry(root, relative)
                .map(|_| ())
                .expect_err(relative)
                .code
        };
        assert!(open_entry(&root, "dir/file").is_ok());
        assert_eq!(code(&root, "dir/absent"), FailureCode::InvalidPackage);
        assert_eq!(code(&root, "absent/file"), FailureCode::InvalidPackage);
        assert_eq!(code(&root, "linked-dir/file"), FailureCode::UnsafePath);
        assert_eq!(code(&root, "dir/linked-file"), FailureCode::UnsafePath);
        assert_eq!(
            code(&root, "dir"),
            FailureCode::UnsafePath,
            "a directory leaf"
        );
        assert_eq!(code(&root, "dir/file/below"), FailureCode::UnsafePath);
        assert_eq!(
            code(&base.join("linked-root"), "dir/file"),
            FailureCode::UnsafePath,
            "a linked root is never the anchor"
        );
        if have_fifo {
            assert_eq!(code(&root, "dir/fifo"), FailureCode::UnsafePath);
        }
        let _ = std::fs::remove_dir_all(&base);
    }
}
