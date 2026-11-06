//! Retained release metadata for rollback.
//!
//! A rollback must not depend on the user still having the older signed
//! download: the authenticated carrier and the API metadata of every installed
//! release are persisted *inside that release's immutable version directory* at
//! install time. The committed receipt claims that exact file, so a later
//! rollback can verify the retained bytes against the installation it already
//! committed and re-derive the release identity without trusting a package the
//! caller supplies now.
//!
//! The document is data, never code: it carries the exact carrier bytes (hex) and
//! the strict descriptor. It deliberately carries **no** signer or assessment
//! string: those are trust verdicts, and a rollback re-establishes them by
//! running the same OS signature/assessment path over the signed carrier artifact
//! retained next to this document (see
//! [`crate::platform::Platform::verify_retained`]). The locally stored carrier is
//! only cross-checked against that fresh OS evidence; it can never authorize a
//! signer by itself.

use serde::{Deserialize, Serialize};

use crate::model::{
    FailureCode, InstallFailure, InstallResult, MAX_DOCUMENT_BYTES, ReleaseDescriptor,
    canonical_json,
};
use crate::policy::{CARRIER_HEADER_LEN, read_carrier};

/// The only supported retained-metadata schema generation.
///
/// Generation 2 removed the stored signer/assessment strings: a rollback must
/// derive them from fresh OS verification of the retained signed carrier, never
/// from this document.
pub const RETAINED_SCHEMA_VERSION: u32 = 2;
/// File name of the retained release metadata inside a version directory.
pub const RETAINED_FILE_NAME: &str = "installer-retained.json";
/// The retained document is bounded to 1 MiB (a carrier and its descriptor).
pub const MAX_RETAINED_BYTES: usize = 1024 * 1024;

/// Authenticated release metadata retained with an immutable version.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetainedInstall {
    pub schema: u32,
    /// The strict authenticated release descriptor.
    pub descriptor: ReleaseDescriptor,
    /// The exact authenticated carrier bytes, lowercase hex encoded.
    pub carrier_hex: String,
}

impl RetainedInstall {
    /// Capture the authenticated metadata of `package`.
    pub fn from_package(package: &crate::platform::AuthenticatedPackage) -> InstallResult<Self> {
        let value = Self {
            schema: RETAINED_SCHEMA_VERSION,
            descriptor: package.descriptor().clone(),
            carrier_hex: encode_hex(package.proof().carrier()),
        };
        value.validate()?;
        Ok(value)
    }

    /// The exact authenticated carrier bytes, decoded.
    pub fn carrier(&self) -> InstallResult<Vec<u8>> {
        decode_hex(&self.carrier_hex)
            .ok_or_else(|| InstallFailure::new(FailureCode::InvalidPackage))
    }

    /// The host API window this retained release supports.
    pub fn api_window(&self) -> (u32, u32) {
        (self.descriptor.api_min, self.descriptor.api_max)
    }

    /// Validate bounds and re-derive the authentication binding.
    pub fn validate(&self) -> InstallResult<()> {
        if self.schema != RETAINED_SCHEMA_VERSION {
            return Err(invalid_package());
        }
        self.descriptor.validate()?;
        let carrier = self.carrier()?;
        if carrier.len() > CARRIER_HEADER_LEN + MAX_DOCUMENT_BYTES {
            return Err(invalid_package());
        }
        if read_carrier(&carrier)? != self.descriptor {
            return Err(invalid_package());
        }
        if self.to_bytes()?.len() > MAX_RETAINED_BYTES {
            return Err(invalid_package());
        }
        Ok(())
    }

    /// Strictly parse a retained document.
    pub fn parse(bytes: &[u8]) -> InstallResult<Self> {
        if bytes.is_empty() || bytes.len() > MAX_RETAINED_BYTES {
            return Err(invalid_package());
        }
        let value: Self = serde_json::from_slice(bytes).map_err(|_| invalid_package())?;
        value.validate()?;
        Ok(value)
    }

    /// The canonical bounded document bytes.
    pub fn to_bytes(&self) -> InstallResult<Vec<u8>> {
        let bytes = canonical_json(self)?;
        if bytes.len() > MAX_RETAINED_BYTES {
            return Err(invalid_package());
        }
        Ok(bytes)
    }
}

fn encode_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}

fn decode_hex(text: &str) -> Option<Vec<u8>> {
    if !text.len().is_multiple_of(2) {
        return None;
    }
    let bytes = text.as_bytes();
    let (pairs, remainder) = bytes.as_chunks::<2>();
    if !remainder.is_empty() {
        return None;
    }
    let mut out = Vec::with_capacity(pairs.len());
    for pair in pairs {
        let high = hex_value(pair[0])?;
        let low = hex_value(pair[1])?;
        out.push((high << 4) | low);
    }
    Some(out)
}

const fn hex_value(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        _ => None,
    }
}

fn invalid_package() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidPackage)
}
