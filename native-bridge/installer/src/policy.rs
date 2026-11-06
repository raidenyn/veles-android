//! Strict descriptor parsing, bounded policy-carrier reads and conservative
//! repair/rollback policy composition.
//!
//! The carrier is the persistent policy carrier: a versioned, bounded read-only
//! section that points at the embedded strict descriptor. This module only
//! locates and parses that data; platform authenticity (signature/resource
//! coverage) is supplied by the trust layer and is never assumed here, and a
//! carrier is verified as data, never executed.

use std::collections::HashSet;

use crate::model::{
    FailureCode, InstallFailure, InstallResult, MAX_DOCUMENT_BYTES, Operation, ReleaseDescriptor,
    ReleasePolicy,
};
use crate::platform::AuthenticatedPackage;

/// Carrier magic; the trailing `1` is the container family, not the version.
pub const CARRIER_MAGIC: [u8; 8] = *b"VELESPC1";
/// Supported carrier header version.
pub const CARRIER_VERSION: u16 = 1;
/// Versioned header length in bytes.
pub const CARRIER_HEADER_LEN: usize = 32;
const CARRIER_FLAGS: u16 = 0;

/// Parse a strict release descriptor from untrusted bytes.
///
/// Unknown/duplicate fields, non-canonical digests, out-of-bounds values and
/// origin/identity mismatches are rejected. A descriptor above 256 KiB is never
/// interpreted.
pub fn parse_descriptor(bytes: &[u8]) -> InstallResult<ReleaseDescriptor> {
    if bytes.is_empty() || bytes.len() > MAX_DOCUMENT_BYTES {
        return Err(invalid_package());
    }
    let descriptor: ReleaseDescriptor =
        serde_json::from_slice(bytes).map_err(|_| invalid_package())?;
    descriptor.validate()?;
    Ok(descriptor)
}

/// Locate and parse the descriptor embedded in a bounded, versioned carrier
/// section. Authenticity of the carrier bytes is out of scope for this function.
pub fn read_carrier(carrier: &[u8]) -> InstallResult<ReleaseDescriptor> {
    if carrier.len() < CARRIER_HEADER_LEN {
        return Err(invalid_package());
    }
    if carrier[..8] != CARRIER_MAGIC {
        return Err(invalid_package());
    }
    let version = read_u16(carrier, 8);
    let flags = read_u16(carrier, 10);
    let header_len = read_u32(carrier, 12);
    if version != CARRIER_VERSION
        || flags != CARRIER_FLAGS
        || header_len as usize != CARRIER_HEADER_LEN
    {
        return Err(invalid_package());
    }

    let offset = read_u64(carrier, 16);
    let length = read_u64(carrier, 24);
    if length == 0 || length > MAX_DOCUMENT_BYTES as u64 || offset < CARRIER_HEADER_LEN as u64 {
        return Err(invalid_package());
    }
    let end = offset.checked_add(length).ok_or_else(invalid_package)?;
    let start = usize::try_from(offset).map_err(|_| invalid_package())?;
    let end = usize::try_from(end).map_err(|_| invalid_package())?;
    let section = carrier.get(start..end).ok_or_else(invalid_package)?;
    parse_descriptor(section)
}

/// Serialize a descriptor into a carrier section. Used by packaging and tests so
/// the encoding has exactly one definition.
pub fn write_carrier(descriptor: &[u8]) -> InstallResult<Vec<u8>> {
    if descriptor.is_empty() || descriptor.len() > MAX_DOCUMENT_BYTES {
        return Err(InstallFailure::new(FailureCode::InvalidInput));
    }
    let length = u64::try_from(descriptor.len())
        .map_err(|_| InstallFailure::new(FailureCode::InvalidInput))?;
    let mut carrier = Vec::with_capacity(CARRIER_HEADER_LEN + descriptor.len());
    carrier.extend_from_slice(&CARRIER_MAGIC);
    carrier.extend_from_slice(&CARRIER_VERSION.to_le_bytes());
    carrier.extend_from_slice(&CARRIER_FLAGS.to_le_bytes());
    carrier.extend_from_slice(&(CARRIER_HEADER_LEN as u32).to_le_bytes());
    carrier.extend_from_slice(&(CARRIER_HEADER_LEN as u64).to_le_bytes());
    carrier.extend_from_slice(&length.to_le_bytes());
    carrier.extend_from_slice(descriptor);
    Ok(carrier)
}

fn read_u16(bytes: &[u8], start: usize) -> u16 {
    let mut raw = [0u8; 2];
    raw.copy_from_slice(&bytes[start..start + 2]);
    u16::from_le_bytes(raw)
}

fn read_u32(bytes: &[u8], start: usize) -> u32 {
    let mut raw = [0u8; 4];
    raw.copy_from_slice(&bytes[start..start + 4]);
    u32::from_le_bytes(raw)
}

fn read_u64(bytes: &[u8], start: usize) -> u64 {
    let mut raw = [0u8; 8];
    raw.copy_from_slice(&bytes[start..start + 8]);
    u64::from_le_bytes(raw)
}

/// Compose the policy actually enforced for `operation`.
///
/// - Inspect/Uninstall apply no new policy and keep the committed live policy.
/// - Install accepts the incoming policy only at an equal epoch (identical
///   policy) or a higher epoch. A higher epoch is the explicit forward-change
///   signal; authenticated release authorization for that change belongs to the
///   trust layer.
/// - Repair must stay at the live epoch and may only tighten the policy.
/// - Rollback accepts an equal or older epoch but composes the conservative
///   union of denials and the intersection of publisher and rollback-eligible
///   sets, so an older package can never erase a known denial or broaden trust.
pub fn effective_policy(
    current: &ReleasePolicy,
    incoming: &ReleasePolicy,
    operation: Operation,
) -> InstallResult<ReleasePolicy> {
    current.validate()?;
    incoming.validate()?;
    match operation {
        Operation::Inspect | Operation::Uninstall => Ok(current.clone()),
        Operation::Install => {
            if incoming.epoch < current.epoch
                || (incoming.epoch == current.epoch && incoming != current)
            {
                return Err(policy_denied());
            }
            Ok(incoming.clone())
        }
        Operation::Repair => {
            if incoming.epoch != current.epoch || relaxes(current, incoming) {
                return Err(policy_denied());
            }
            merge(current, incoming)
        }
        Operation::Rollback => {
            if incoming.epoch > current.epoch {
                return Err(policy_denied());
            }
            merge(current, incoming)
        }
    }
}

/// Compose the policy an *authenticated* package may apply to `operation`.
///
/// The incoming policy is read from the authenticated descriptor owned by
/// [`AuthenticatedPackage`], never from a caller-supplied argument, so a caller
/// cannot supply a fresher policy than the signature actually covers. The
/// conservative [`effective_policy`] rules then apply: repair must stay at the
/// live epoch and may only tighten; rollback intersects publisher/rollback
/// eligibility and accumulates denials; install requires an equal-or-higher
/// epoch. A stale but correctly signed policy is `PolicyDenied`, never a
/// success with a warning.
pub fn authorize_package_policy(
    package: &AuthenticatedPackage,
    current: &ReleasePolicy,
    operation: Operation,
) -> InstallResult<ReleasePolicy> {
    effective_policy(current, &package.descriptor().policy, operation)
}

fn merge(current: &ReleasePolicy, incoming: &ReleasePolicy) -> InstallResult<ReleasePolicy> {
    let publishers: Vec<String> = current
        .publishers
        .iter()
        .filter(|publisher| incoming.publishers.contains(*publisher))
        .cloned()
        .collect();
    if publishers.is_empty() {
        return Err(policy_denied());
    }
    let mut denied_payloads = current.denied_payloads.clone();
    for digest in &incoming.denied_payloads {
        if !denied_payloads.contains(digest) {
            denied_payloads.push(*digest);
        }
    }
    let rollback_payloads = current
        .rollback_payloads
        .iter()
        .filter(|digest| incoming.rollback_payloads.contains(*digest))
        .copied()
        .collect();
    Ok(ReleasePolicy {
        publishers,
        denied_payloads,
        rollback_payloads,
        epoch: current.epoch,
    })
}

/// Any way `incoming` is more permissive than `current`.
fn relaxes(current: &ReleasePolicy, incoming: &ReleasePolicy) -> bool {
    let current_denied: HashSet<_> = current.denied_payloads.iter().copied().collect();
    let incoming_denied: HashSet<_> = incoming.denied_payloads.iter().copied().collect();
    let current_publishers: HashSet<&str> = current.publishers.iter().map(String::as_str).collect();
    let incoming_publishers: HashSet<&str> =
        incoming.publishers.iter().map(String::as_str).collect();
    let current_rollback: HashSet<_> = current.rollback_payloads.iter().copied().collect();
    let incoming_rollback: HashSet<_> = incoming.rollback_payloads.iter().copied().collect();
    !current_denied.is_subset(&incoming_denied)
        || !incoming_publishers.is_subset(&current_publishers)
        || !incoming_rollback.is_subset(&current_rollback)
}

fn invalid_package() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidPackage)
}

fn policy_denied() -> InstallFailure {
    InstallFailure::new(FailureCode::PolicyDenied)
}
