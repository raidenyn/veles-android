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

/// The only supported package-inventory document schema generation.
pub const INVENTORY_SCHEMA_VERSION: u32 = 1;
/// Package inventory document file name at the package root.
pub const PACKAGE_INVENTORY_FILE: &str = "inventory.json";
/// Payload directory name at the package root.
pub const PACKAGE_PAYLOAD_DIR: &str = "payload";

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

fn verify_entry(root: &Path, relative: &str, entry: &InventoryEntry) -> InstallResult<()> {
    let path = root.join(relative);
    let metadata = fs::symlink_metadata(&path).map_err(|error| match error.kind() {
        std::io::ErrorKind::NotFound => invalid_package(),
        _ => io_failure(),
    })?;
    if !metadata.file_type().is_file() {
        return Err(unsafe_path());
    }
    if metadata.len() != entry.size {
        return Err(invalid_package());
    }
    if hash_file(&path)? != entry.sha256 {
        return Err(invalid_package());
    }
    Ok(())
}

fn hash_file(path: &Path) -> InstallResult<Digest> {
    let mut file = File::open(path).map_err(|error| match error.kind() {
        std::io::ErrorKind::NotFound => invalid_package(),
        _ => io_failure(),
    })?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer).map_err(|_| io_failure())?;
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
