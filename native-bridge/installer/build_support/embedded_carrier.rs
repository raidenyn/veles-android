//! The build-time release-identity channel, shared by the build script and the
//! tests that prove it.
//!
//! Release packaging sets [`ENV`] to the authenticated carrier document it is
//! shipping. The build embeds the exact bytes; an unset or empty channel embeds
//! nothing, which is the honest state of an unreleased build. The channel is a
//! build input only: the shipped executable never reads a file or the environment
//! to discover its release identity.
//!
//! This module is `include!`d by `build.rs` (which cannot depend on the crate it
//! builds) and by the integration tests, so the exact rule is tested rather than
//! remembered.

use std::fs;
use std::path::Path;

/// The build-time variable naming the carrier document to embed.
pub const ENV: &str = "VELES_EMBEDDED_CARRIER";
/// Magic of the bounded policy carrier document (`CARRIER_MAGIC` in `src/policy.rs`).
pub const CARRIER_MAGIC: &[u8; 8] = b"VELESPC1";
/// Upper bound of a carrier document: header + 256 KiB descriptor + digest slot.
pub const MAX_BYTES: usize = 32 + 256 * 1024 + 32;

/// The exact bytes the build-time channel supplies.
///
/// `Ok(None)` means "this build embeds no release identity": the variable is
/// unset, empty, or names an empty file. Anything else is read exactly once and
/// must be a bounded carrier document; a missing, unreadable, oversized or
/// malformed input fails the build instead of silently shipping an engine that
/// ignores the release identity packaging meant to compile in.
pub fn channel_bytes(value: Option<&str>) -> Result<Option<Vec<u8>>, String> {
    let Some(value) = value else {
        return Ok(None);
    };
    if value.is_empty() {
        return Ok(None);
    }
    let path = Path::new(value);
    let metadata =
        fs::metadata(path).map_err(|error| format!("{ENV} names an unreadable file ({error})"))?;
    if !metadata.is_file() {
        return Err(format!("{ENV} must name a regular carrier document"));
    }
    if metadata.len() == 0 {
        return Ok(None);
    }
    if metadata.len() > MAX_BYTES as u64 {
        return Err(format!("{ENV} names a document above {MAX_BYTES} bytes"));
    }
    let bytes = fs::read(path).map_err(|error| format!("{ENV} could not be read ({error})"))?;
    if bytes.len() != metadata.len() as usize {
        return Err(format!("{ENV} changed while it was read"));
    }
    if !bytes.starts_with(CARRIER_MAGIC) {
        return Err(format!("{ENV} is not a VELESPC1 carrier document"));
    }
    Ok(Some(bytes))
}
