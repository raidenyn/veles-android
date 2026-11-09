//! The release identity compiled into the engine (OTP-25 F1).
//!
//! Release packaging assembles the carrier first and then rebuilds the engine with
//! `VELES_EMBEDDED_CARRIER`; the engine parses the strict descriptor out of those exact
//! bytes and authenticates every package against it. An unreleased build embeds nothing
//! and cannot install or repair, which is the honest answer rather than a package-supplied
//! identity. A carrier that belongs to another build channel is refused at run time, never
//! silently used.

use crate::model::{Channel, FailureCode, InstallFailure, InstallResult, ReleaseDescriptor};
use crate::policy::read_carrier;

/// The build-time variable release packaging sets to the carrier to embed.
pub const EMBEDDED_CARRIER_ENV: &str = "VELES_EMBEDDED_CARRIER";

/// Parse the release descriptor the build embedded.
///
/// * `None` (or empty bytes) is an unreleased build: `Ok(None)`, so the CLI
///   refuses install and repair with `policy-denied`.
/// * A carrier document is parsed exactly as the package carrier is: the header,
///   bounds, canonical descriptor and policy must all validate.
/// * Anything present but not a valid carrier is itself a refusal
///   (`policy-denied`), never a silent "no identity": a build that meant to embed
///   a release must not quietly behave like one that did not.
pub fn decode_embedded_carrier(bytes: Option<&[u8]>) -> InstallResult<Option<ReleaseDescriptor>> {
    let Some(bytes) = bytes else {
        return Ok(None);
    };
    if bytes.is_empty() {
        return Ok(None);
    }
    read_carrier(bytes)
        .map(Some)
        .map_err(|_| InstallFailure::new(FailureCode::PolicyDenied))
}

/// Parse the embedded carrier and require it to belong to `channel`.
///
/// This is the run-time half of the build-channel rule: even if a fixture or development engine
/// were built with a production carrier, it refuses instead of installing production identity under
/// its own channel's root. An absent carrier stays the honest unreleased state (`Ok(None)`).
pub fn decode_embedded_carrier_for_channel(
    bytes: Option<&[u8]>,
    channel: Channel,
) -> InstallResult<Option<ReleaseDescriptor>> {
    let Some(descriptor) = decode_embedded_carrier(bytes)? else {
        return Ok(None);
    };
    if descriptor.channel != channel {
        return Err(InstallFailure::new(FailureCode::PolicyDenied));
    }
    Ok(Some(descriptor))
}
