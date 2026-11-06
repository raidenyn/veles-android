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
//! The document is data, never code: it carries the exact carrier bytes (hex),
//! the OS-verified signer and assessment strings, and the strict descriptor. On
//! read, the carrier is parsed again and must reproduce the descriptor exactly,
//! the assessment must be one accepted for the descriptor target, and the signer
//! must be listed by the descriptor's own policy. OS signature/assessment
//! evidence was established when the release was installed; re-running a native
//! trust engine on a retained file is Task 8's scope, so this document binds the
//! bytes that evidence covered instead of inventing a fresh assessment.

use serde::{Deserialize, Serialize};

use crate::model::{
    FailureCode, InstallFailure, InstallResult, MAX_DOCUMENT_BYTES, MAX_PUBLISHER_BYTES,
    ReleaseDescriptor, canonical_json,
};
use crate::platform::{AuthenticatedPackage, is_accepted_assessment};
use crate::policy::{CARRIER_HEADER_LEN, read_carrier};

/// The only supported retained-metadata schema generation.
pub const RETAINED_SCHEMA_VERSION: u32 = 1;
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
    /// The OS-verified signer identity recorded at install time.
    pub signer: String,
    /// The OS assessment verdict recorded at install time.
    pub assessment: String,
    /// The exact authenticated carrier bytes, lowercase hex encoded.
    pub carrier_hex: String,
}

impl RetainedInstall {
    /// Capture the authenticated metadata of `package`.
    pub fn from_package(package: &AuthenticatedPackage) -> InstallResult<Self> {
        let value = Self {
            schema: RETAINED_SCHEMA_VERSION,
            descriptor: package.descriptor().clone(),
            signer: package.signer().to_owned(),
            assessment: package.assessment().to_owned(),
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
        for value in [&self.signer, &self.assessment] {
            if value.is_empty()
                || value.len() > MAX_PUBLISHER_BYTES
                || value.chars().any(char::is_control)
            {
                return Err(invalid_package());
            }
        }
        if !is_accepted_assessment(&self.assessment, self.descriptor.target) {
            return Err(invalid_package());
        }
        if !self
            .descriptor
            .policy
            .publishers
            .iter()
            .any(|publisher| publisher == &self.signer)
        {
            return Err(invalid_package());
        }
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
