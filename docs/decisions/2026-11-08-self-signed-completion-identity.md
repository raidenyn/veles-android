# Decision: self-signed completion identity for the native installers

Date: 2026-11-08
Status: accepted (owner)
Scope: Windows signing and verification for OTP-25 project completion

## Context

The release gate authorized a Windows publisher by the leaf certificate's SHA-256
fingerprint and required `signtool verify /pa` to succeed, which needs a chain that the
runner trusts. No CA certificate exists, and the owner has decided not to acquire one
before the project completes. The certificate will be obtained later, outside project
completion.

## Decision

1. The Windows signing gate accepts a **self-signed** identity when the workflow declares
   it explicitly with `VELES_WIN_TRUST_SELF_SIGNED=1` and supplies the root certificate in
   `VELES_WIN_ROOT_CER_BASE64`.
2. The gate imports that root as an **ephemeral, recorded trust anchor** only for the
   verification step and removes it through the stage undo. It records, for every signed
   component, `public_trust: false`, `trust_mode: "self-signed"` and the anchor's SHA-256.
3. The leaf SHA-1 thumbprint check, the leaf SHA-256 publisher pin, the RFC 3161 timestamp
   requirement and the signed-content correspondence checks are unchanged. `validate`
   refuses a self-signed signature that does not record its anchor, and refuses an
   unrecorded trust mode.
4. Public trust, CA chain, SmartScreen reputation, Gatekeeper, notarization and stapling
   are **not claimed**. A completion build is an acceptance candidate, not a general
   distribution.
5. Acceptance machines must trust the anchor for the engine's `WinVerifyTrust` call
   (`certutil -addstore -f Root <root.cer>` as administrator); the run records that this
   was done.
6. macOS is out of scope: the signed macOS host remains blocked by the DER-entitlements
   correspondence gate, independently of the certificate.

## Changes

| File | Change |
|---|---|
| `native-bridge/installer/scripts/release_gate.py` | self-signed mode: optional-root requirement, anchor import/undo, trust record, signature validation |
| `.github/workflows/release-native-installers.yml` | passes `VELES_WIN_ROOT_CER_BASE64` and `VELES_WIN_TRUST_SELF_SIGNED` to both Windows sign stages |
| `native-bridge/installer/scripts/test_release_gate.py` | anchor recording/removal, root requirement and signature-anchor tests |
| `docs/otp-25-installers.md` | decision, scope and acceptance prerequisite |

## Still required for a complete signed candidate (not certificate-related)

- **Release descriptor**: an approved `release-windows.json` with a real extension ID and
  the self-signed publisher fingerprint (`sha256:<leaf DER>`).
- **Tool identities**: the MSVC and Windows SDK entries in `tools.lock.json` are still
  `required_unresolved`; `verify-inputs` blocks until they are observed on the approved
  `windows-2025` runner.
- **macOS**: the DER-entitlements decoder, notarization and a Developer ID identity.

## Consequences

The gate is honest about what a self-signed signature proves: the bytes are signed by the
pinned leaf, the timestamp exists and the signed tree corresponds to the unsigned one.
It does not prove that any other machine trusts that leaf. Replacing the self-signed
identity with a CA certificate later requires no gate change: clearing
`VELES_WIN_TRUST_SELF_SIGNED` returns the gate to the OS-trusted-chain path.
