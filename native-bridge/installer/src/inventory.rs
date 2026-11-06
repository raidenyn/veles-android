//! Strict payload-inventory verification.
//!
//! An inventory is authenticated package data. Before any file is opened, every
//! path is normalized and bounded: relative components only, no drive prefix,
//! alternate data stream, NUL/control character, Windows separator alias,
//! trailing-dot/space alias or case-folded duplicate. Only then are sizes and
//! SHA-256 digests checked against the actual files below a caller-supplied root.

use std::collections::HashSet;
use std::fs::{self, File};
use std::io::Read;
use std::path::Path;

use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

use crate::model::{
    Digest, FailureCode, InstallFailure, InstallResult, InventoryEntry, MAX_DOCUMENT_BYTES,
    MAX_INVENTORY_BYTES, MAX_INVENTORY_ENTRIES, MAX_PATH_BYTES, canonical_json,
};
use crate::platform::{is_link_like, open_no_follow};

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

/// Bytes of a file head that classify a PE / Mach-O image.
pub(crate) const SIGNABLE_HEAD_BYTES: u64 = 4096;

/// Whether `head` (the first bytes of a file) starts a PE or Mach-O image, the only
/// formats a code-signing step rewrites in place.
///
/// This is a classification, not a parser: it decides whether a changed digest is
/// *allowed to be* a signing mutation. Whether the change is exactly the
/// documented signature mutation is the release-time correspondence report's job
/// (`scripts/correspondence.py`); whether the signature is trusted is the OS's.
pub fn is_signable_image(head: &[u8]) -> bool {
    if head.len() >= 0x40 && head.starts_with(b"MZ") {
        let mut raw = [0u8; 4];
        raw.copy_from_slice(&head[0x3c..0x40]);
        let pe_offset = u32::from_le_bytes(raw) as usize;
        return head
            .get(pe_offset..pe_offset.saturating_add(4))
            .is_some_and(|signature| signature == b"PE\0\0");
    }
    matches!(
        head.get(..4),
        Some([0xcf, 0xfa, 0xed, 0xfe])
            | Some([0xfe, 0xed, 0xfa, 0xcf])
            | Some([0xca, 0xfe, 0xba, 0xbe])
    )
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

/// Open one inventory entry for reading, walking every component below `root`.
///
/// Each ancestor (and the leaf) is inspected with `symlink_metadata` before it is
/// descended into or opened: a link, junction or reparse point at *any* level is
/// `UnsafePath`, so a directory that merely resolves outside `root` can never be
/// read through. The leaf is then opened without following a final link and the
/// open handle must itself be a regular file. A missing component is
/// `InvalidPackage` (the inventory names something the package does not hold).
pub(crate) fn open_entry(root: &Path, relative: &str) -> InstallResult<File> {
    let parts: Vec<&str> = relative.split('/').collect();
    let mut current = root.to_path_buf();
    for (index, part) in parts.iter().enumerate() {
        current.push(part);
        let metadata = fs::symlink_metadata(&current).map_err(|error| match error.kind() {
            std::io::ErrorKind::NotFound => invalid_package(),
            _ => io_failure(),
        })?;
        if is_link_like(&metadata) {
            return Err(unsafe_path());
        }
        let is_leaf = index + 1 == parts.len();
        if (is_leaf && !metadata.file_type().is_file())
            || (!is_leaf && !metadata.file_type().is_dir())
        {
            return Err(unsafe_path());
        }
    }
    let file = open_no_follow(&current).map_err(|error| match error.kind() {
        std::io::ErrorKind::NotFound => invalid_package(),
        _ => io_failure(),
    })?;
    if !file.metadata().map_err(|_| io_failure())?.is_file() {
        return Err(unsafe_path());
    }
    Ok(file)
}

/// The first `limit` bytes of one inventory entry (a file-format classification
/// input), read through the anchored traversal of [`open_entry`].
pub(crate) fn read_entry_head(root: &Path, relative: &str, limit: u64) -> InstallResult<Vec<u8>> {
    let file = open_entry(root, relative)?;
    let mut head = Vec::new();
    file.take(limit)
        .read_to_end(&mut head)
        .map_err(|_| io_failure())?;
    Ok(head)
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
