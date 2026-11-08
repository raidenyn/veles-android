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

- **Release descriptor**: `release-windows.json` is tracked with the development allowlist
  ID and the self-signed publisher fingerprint (`sha256:<leaf DER>`). Plan Task 16 replaces the
  allowlist's production entry with the real Web Store item ID.
- **Tool identities**: the MSVC and Windows SDK entries in `tools.lock.json` are pinned by
  version only from the approved `windows-2025` runner (see below); the digests are deferred
  and must not be invented.
- **macOS**: the DER-entitlements decoder, notarization and a Developer ID identity.

## Consequences

The gate is honest about what a self-signed signature proves: the bytes are signed by the
pinned leaf, the timestamp exists and the signed tree corresponds to the unsigned one.
It does not prove that any other machine trusts that leaf. Replacing the self-signed
identity with a CA certificate later requires no gate change: clearing
`VELES_WIN_TRUST_SELF_SIGNED` returns the gate to the OS-trusted-chain path.

## Self-signed identity generated (2026-11-08)

Generated with a two-level chain (root CA, pathlen 1, plus a code-signing leaf with the
Code Signing EKU; two-year validity). A single self-signed end-entity does not work:
WinVerifyTrust returns `TRUST_E_BASIC_CONSTRAINTS (0x80096019)`.

| Value | |
|---|---|
| Publisher (`sha256:` leaf DER) | `sha256:e34913a76c5ac08260e4577862a3b7be6dcd9d95f52d5af9ac4f3d277cf24c26` |
| Leaf SHA-1 thumbprint | `39F904EC9C78D951CDFD001D18FA6CE44E3A02FE` |
| Root SHA-1 thumbprint | `C83F853AC2679E8318280088996639EB7B694BC8` |
| Timestamp URL | `http://timestamp.digicert.com` (signtool rejects the https form) |

Storage (owner action; the development CI account has no repository-admin rights):
environment `native-release` secrets `VELES_WIN_PFX_BASE64`, `VELES_WIN_PFX_PASSWORD`,
`VELES_WIN_ROOT_CER_BASE64`; variables `VELES_WIN_CERT_SHA1`, `VELES_WIN_PUBLISHER`,
`VELES_WIN_TIMESTAMP_URL`, `VELES_WIN_TRUST_SELF_SIGNED=1`. The private key never enters
the repository; delete the machine copy after the secrets are stored.

## Tool identity pin (2026-11-08)

Observed on the approved `windows-2025` runner and pinned by version only:

- MSVC: `cl` 19.51.36260, linker 14.51.36260.0, toolset 14.51.36231.
- Windows SDK: 10.0.26100.0, UCRT 10.0.26100.0.

The toolset and SDK digest definitions are deferred; strengthening this pin is a
standing improvement, not a completion requirement.
