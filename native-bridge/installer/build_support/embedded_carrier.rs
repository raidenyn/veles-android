//! The build-time release-identity channel, shared by the build script and the
//! tests that prove it.
//!
//! Release packaging sets [`ENV`] to the authenticated carrier document it assembled
//! and verified, and [`CHANNEL_ENV`] to the channel being built. The build embeds the
//! exact bytes; a production build without a carrier fails the build rather than
//! shipping an engine that cannot authenticate a package, while a fixture build
//! without one is the honest fixture state (the engine refuses at run time) and the
//! development channel never embeds a release carrier. The channel is a build input
//! only: the shipped executable never reads a file or the environment to discover its
//! release identity.
//!
//! This module is `include!`d by `build.rs` (which cannot depend on the crate it
//! builds) and by the integration tests, so the exact rule is tested rather than
//! remembered.

use std::fs;
use std::path::Path;

/// The build-time variable naming the carrier document to embed.
pub const ENV: &str = "VELES_EMBEDDED_CARRIER";
/// The build-time variable naming the channel this engine is built for.
///
/// The producer always sets it. A production build without [`ENV`] is a build error rather than
/// an engine that later refuses every install; a fixture build without one is the honest fixture
/// state (the engine refuses install/repair at run time); the development channel never embeds a
/// release carrier. Unset means "an unreleased build", which keeps a plain `cargo build` and the
/// crate's own tests working.
pub const CHANNEL_ENV: &str = "VELES_BUILD_CHANNEL";
/// Magic of the bounded policy carrier document (`CARRIER_MAGIC` in `src/policy.rs`).
pub const CARRIER_MAGIC: &[u8; 8] = b"VELESPC1";
/// Upper bound of a carrier document: header + 256 KiB descriptor + digest slot.
pub const MAX_BYTES: usize = 32 + 256 * 1024 + 32;
/// Fixed carrier header length (the descriptor offset).
const HEADER_LEN: usize = 32;

/// The channel field in a canonical descriptor. Canonical JSON has no whitespace, and a `"` inside
/// a string value is escaped, so this exact sequence can only be the descriptor's channel field.
const PRODUCTION_CHANNEL_FIELD: &[u8] = b"\"channel\":\"production\"";
/// The fixture counterpart of [`PRODUCTION_CHANNEL_FIELD`].
const FIXTURE_CHANNEL_FIELD: &[u8] = b"\"channel\":\"fixture\"";

/// The bounded descriptor section of a carrier document, or `None` when it is not one.
fn descriptor_section(bytes: &[u8]) -> Option<&[u8]> {
    if bytes.len() < HEADER_LEN || &bytes[..8] != CARRIER_MAGIC {
        return None;
    }
    let offset = u64::from_le_bytes(bytes[16..24].try_into().ok()?) as usize;
    let length = u64::from_le_bytes(bytes[24..32].try_into().ok()?) as usize;
    if offset < HEADER_LEN || length == 0 || length > MAX_BYTES - HEADER_LEN {
        return None;
    }
    let end = offset.checked_add(length)?;
    bytes.get(offset..end)
}

fn contains(haystack: &[u8], needle: &[u8]) -> bool {
    haystack
        .windows(needle.len())
        .any(|window| window == needle)
}

/// The channel a build declares through [`CHANNEL_ENV`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BuildChannel {
    /// No channel declared (a local `cargo build` or the crate's own tests).
    Unspecified,
    Production,
    Fixture,
    Development,
}

/// Parse [`CHANNEL_ENV`]. Anything other than the three published channels is refused.
pub fn build_channel(value: Option<&str>) -> Result<BuildChannel, String> {
    match value.map(str::trim) {
        None | Some("") => Ok(BuildChannel::Unspecified),
        Some("production") => Ok(BuildChannel::Production),
        Some("fixture") => Ok(BuildChannel::Fixture),
        Some("development") => Ok(BuildChannel::Development),
        Some(other) => Err(format!(
            "{CHANNEL_ENV} must name production, fixture or development (got {other:?})"
        )),
    }
}

/// Apply the channel rule to the transported bytes.
///
/// * production: the authenticated carrier is required, and its absence fails the build;
/// * fixture: optional; when present it must be a *fixture* carrier, so a fixture build can never
///   embed production identity;
/// * development: a release carrier is refused;
/// * unspecified: legacy/unreleased behaviour, optional.
///
/// The channel check here reads the canonical descriptor's channel field as exact bytes. It is a
/// fail-closed belt: the producer also runs `assemble_unsigned.py verify-carrier`, which parses the
/// descriptor strictly, before this build.
pub fn resolve(value: Option<&str>, channel: Option<&str>) -> Result<Option<Vec<u8>>, String> {
    let channel = build_channel(channel)?;
    let bytes = channel_bytes(value)?;
    let Some(bytes) = bytes else {
        return match channel {
            BuildChannel::Production => Err(format!(
                "a production-channel build must embed the authenticated carrier: set {ENV} to the \
                 verified carrier document assembled before this build"
            )),
            _ => Ok(None),
        };
    };
    if channel == BuildChannel::Development {
        return Err(format!(
            "the development channel must not embed a release carrier ({ENV} is set)"
        ));
    }
    if channel != BuildChannel::Unspecified {
        let section = descriptor_section(&bytes)
            .ok_or_else(|| format!("{ENV} is not a bounded carrier document"))?;
        let (wanted, name) = match channel {
            BuildChannel::Production => (PRODUCTION_CHANNEL_FIELD, "production"),
            BuildChannel::Fixture => (FIXTURE_CHANNEL_FIELD, "fixture"),
            BuildChannel::Development | BuildChannel::Unspecified => unreachable!(),
        };
        if !contains(section, wanted) {
            return Err(format!(
                "the carrier does not belong to the {name} channel; a {name} build must not embed \
                 another channel's release identity"
            ));
        }
    }
    Ok(Some(bytes))
}

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
