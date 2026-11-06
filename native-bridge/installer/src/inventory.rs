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

use sha2::{Digest as _, Sha256};

use crate::model::{
    Digest, FailureCode, InstallFailure, InstallResult, InventoryEntry, MAX_INVENTORY_BYTES,
    MAX_INVENTORY_ENTRIES, MAX_PATH_BYTES,
};

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
/// `entries`; extra-file and enumeration checks are the caller's obligation.
///
/// Structural violations (path policy, counts, total size) are rejected before
/// any file access; then each declared size and SHA-256 must match the file on
/// disk. Symlinks and non-regular files are never opened.
pub fn verify_inventory(root: &Path, entries: &[InventoryEntry]) -> InstallResult<()> {
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

    let root_metadata = fs::symlink_metadata(root).map_err(|_| io_failure())?;
    if !root_metadata.is_dir() {
        return Err(io_failure());
    }

    for (entry, relative) in entries.iter().zip(normalized_paths) {
        verify_entry(root, &relative, entry)?;
    }
    Ok(())
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
