//! Portable WinVerifyTrust status classification (Task 4).
//!
//! Compiled for the Windows adapter *and* for `cargo test` on every host, so the
//! mapping from a `WinVerifyTrust` status to a fail-closed verdict is asserted
//! by unit tests instead of being taken on faith on a Windows runner.

use super::TrustVerdict;

/// Classify a `WinVerifyTrust` status.
///
/// Known signature/chain/policy denials and explicit revocation are
/// [`TrustVerdict::Deny`]. An assessment that cannot be formed - offline
/// revocation data, a broken trust provider, or an unknown status - is
/// [`TrustVerdict::Unavailable`] rather than being guessed as an explicit denial:
/// it still blocks activation, but it does not claim the code was rejected.
pub(super) fn classify_winverifytrust_status(status: i32) -> TrustVerdict {
    match status as u32 {
        TRUST_E_NOSIGNATURE
        | TRUST_E_EXPLICIT_DISTRUST
        | TRUST_E_SUBJECT_NOT_TRUSTED
        | TRUST_E_SUBJECT_FORM_UNKNOWN
        | TRUST_E_BAD_DIGEST
        | TRUST_E_CERT_SIGNATURE
        | TRUST_E_TIME_STAMP
        | TRUST_E_FAIL
        | CERT_E_REVOKED
        | CERT_E_UNTRUSTEDROOT
        | CERT_E_UNTRUSTEDTESTROOT
        | CERT_E_CHAINING
        | CERT_E_EXPIRED
        | CERT_E_WRONG_USAGE
        | CERT_E_CRITICAL
        | CERT_E_PURPOSE
        | CERT_E_ISSUERCHAINING
        | CERT_E_PATHLENCONST
        | CERT_E_UNTRUSTEDCA
        | CERT_E_INVALID_POLICY
        | CERT_E_INVALID_NAME
        | CERT_E_CN_NO_MATCH => TrustVerdict::Deny,
        CERT_E_REVOCATION_FAILURE
        | CRYPT_E_REVOCATION_OFFLINE
        | TRUST_E_PROVIDER_UNKNOWN
        | TRUST_E_ACTION_UNKNOWN
        | TRUST_E_SYSTEM_ERROR => TrustVerdict::Unavailable,
        _ => TrustVerdict::Unavailable,
    }
}

const TRUST_E_PROVIDER_UNKNOWN: u32 = 0x800B_0001;
const TRUST_E_ACTION_UNKNOWN: u32 = 0x800B_0002;
const TRUST_E_SUBJECT_FORM_UNKNOWN: u32 = 0x800B_0003;
const TRUST_E_SUBJECT_NOT_TRUSTED: u32 = 0x800B_0004;
const TRUST_E_NOSIGNATURE: u32 = 0x800B_0100;
const CERT_E_EXPIRED: u32 = 0x800B_0101;
const CERT_E_PATHLENCONST: u32 = 0x800B_0104;
const CERT_E_CRITICAL: u32 = 0x800B_0105;
const CERT_E_PURPOSE: u32 = 0x800B_0106;
const CERT_E_ISSUERCHAINING: u32 = 0x800B_0107;
const CERT_E_UNTRUSTEDROOT: u32 = 0x800B_0109;
const CERT_E_CHAINING: u32 = 0x800B_010A;
const TRUST_E_FAIL: u32 = 0x800B_010B;
const CERT_E_REVOKED: u32 = 0x800B_010C;
const CERT_E_UNTRUSTEDTESTROOT: u32 = 0x800B_010D;
const CERT_E_REVOCATION_FAILURE: u32 = 0x800B_010E;
const CERT_E_CN_NO_MATCH: u32 = 0x800B_010F;
const CERT_E_WRONG_USAGE: u32 = 0x800B_0110;
const TRUST_E_EXPLICIT_DISTRUST: u32 = 0x800B_0111;
const CERT_E_UNTRUSTEDCA: u32 = 0x800B_0112;
const CERT_E_INVALID_POLICY: u32 = 0x800B_0113;
const CERT_E_INVALID_NAME: u32 = 0x800B_0114;
const CRYPT_E_REVOCATION_OFFLINE: u32 = 0x8009_2013;
const TRUST_E_SYSTEM_ERROR: u32 = 0x8009_6001;
const TRUST_E_CERT_SIGNATURE: u32 = 0x8009_6004;
const TRUST_E_TIME_STAMP: u32 = 0x8009_6005;
const TRUST_E_BAD_DIGEST: u32 = 0x8009_6010;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signature_chain_and_policy_denials_are_denied() {
        // Independent literals (not the module constants) so a mistyped value is
        // caught rather than asserted against itself.
        let denied: &[u32] = &[
            0x800B_0003, // TRUST_E_SUBJECT_FORM_UNKNOWN
            0x800B_0004, // TRUST_E_SUBJECT_NOT_TRUSTED
            0x800B_0100, // TRUST_E_NOSIGNATURE
            0x800B_0101, // CERT_E_EXPIRED
            0x800B_0104, // CERT_E_PATHLENCONST
            0x800B_0105, // CERT_E_CRITICAL
            0x800B_0106, // CERT_E_PURPOSE
            0x800B_0107, // CERT_E_ISSUERCHAINING
            0x800B_0109, // CERT_E_UNTRUSTEDROOT
            0x800B_010A, // CERT_E_CHAINING
            0x800B_010B, // TRUST_E_FAIL
            0x800B_010C, // CERT_E_REVOKED
            0x800B_010D, // CERT_E_UNTRUSTEDTESTROOT
            0x800B_010F, // CERT_E_CN_NO_MATCH
            0x800B_0110, // CERT_E_WRONG_USAGE
            0x800B_0111, // TRUST_E_EXPLICIT_DISTRUST
            0x800B_0112, // CERT_E_UNTRUSTEDCA
            0x800B_0113, // CERT_E_INVALID_POLICY
            0x800B_0114, // CERT_E_INVALID_NAME
            0x8009_6004, // TRUST_E_CERT_SIGNATURE
            0x8009_6005, // TRUST_E_TIME_STAMP
            0x8009_6010, // TRUST_E_BAD_DIGEST
        ];
        for status in denied {
            assert_eq!(
                classify_winverifytrust_status(*status as i32),
                TrustVerdict::Deny,
                "status {status:#010x} must be an explicit denial"
            );
        }
    }

    #[test]
    fn unformed_assessments_and_unknown_statuses_are_unavailable() {
        let unavailable: &[u32] = &[
            0x800B_0001, // TRUST_E_PROVIDER_UNKNOWN
            0x800B_0002, // TRUST_E_ACTION_UNKNOWN
            0x800B_010E, // CERT_E_REVOCATION_FAILURE
            0x8009_2013, // CRYPT_E_REVOCATION_OFFLINE
            0x8009_6001, // TRUST_E_SYSTEM_ERROR
            0x8000_4005, // E_ACCESSDENIED
            0xDEAD_BEEF, // unknown
        ];
        for status in unavailable {
            assert_eq!(
                classify_winverifytrust_status(*status as i32),
                TrustVerdict::Unavailable,
                "status {status:#010x} must not be guessed as a denial"
            );
        }
    }
}
