# Decision: self-signed macOS acceptance identity

Date: 2026-11-09
Status: accepted (owner)
Scope: macOS signing and verification for OTP-25 project completion

## Context

The macOS release path required an Apple-issued `Developer ID Application`
identity, notarization, stapling and a Gatekeeper acceptance. The owner cannot
obtain an Apple Developer ID before project completion, so
`release-native-installers.yml` failed at the first macOS step (the missing
`config/release-macos.json`) and would have failed later at the certificate
gate. The 2026-11-08 self-signed decision deliberately covered only Windows.

The macOS trust path has two independent requirements:

1. The descriptor publisher: macOS names an Apple team (`team:<TEAMID>`), which a
   self-signed certificate cannot provide (`TeamIdentifier=not set`).
2. The DER entitlements correspondence gate: `correspondence.py` refused
   (`macho-der-entitlements-unsupported`) the DER entitlements slot that a signed
   bundle carries, so **no** signed macOS candidate could have passed.

## Decision

1. `correspondence.py` decodes the DER entitlements slot (Apple's
   `CESerializeCFDictionary` encoding: an application-16 wrapper with an INTEGER
   version and a context-16 dictionary, or a universal SET of entries) and
   requires it to equal the XML slot through the same canonical comparison. A
   disagreement is `macho-entitlements-disagree`; an undecodable slot stays a
   fail-closed refusal.
2. The engine's macOS trust layer accepts a **self-signed** leaf when the
   descriptor policy lists its SHA-256 as `sha256:<64 lowercase hex>`. The
   designated requirement is the 20-byte certificate pin Apple's requirement
   language supports: `certificate leaf = H"<leaf SHA-1>"`, with no
   `anchor apple generic`. A present but unauthorized Team ID is still refused
   and is never downgraded to the certificate path.
3. The signing gate accepts the mode only when the workflow declares
   `VELES_MAC_TRUST_SELF_SIGNED=1` and `VELES_MAC_PUBLISHER` equals the leaf
   fingerprint in the approved descriptor. It requires the P12, its password and
   the identity, and no Apple team or notary key.
4. Every signed component records `public_trust: false`,
   `trust_mode: "self-signed"` and the leaf fingerprint. The disk image records
   `notarization: {status: "not-attempted"}`, `staple: {validated: false}` and
   `gatekeeper: {accepted: false, claimed: false}`. `validate` refuses a
   self-signed record that claims any of those, and refuses a self-signed
   signature whose leaf is not the approved publisher.
5. **Not claimed:** public trust, Apple Developer ID, notarization, stapling,
   Gatekeeper acceptance, SmartScreen-equivalent reputation. A completion build is
   an acceptance candidate, never a general distribution.

## Acceptance machine prerequisite

The installer app and the installed host are signed by the same self-signed
certificate. On the acceptance Mac, that certificate may be trusted once before
opening the disk image:

```sh
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain leaf.pem
```

Alternatively, open the installer with the Finder's **Open** (right-click) and
clear `com.apple.quarantine` from the mounted app when macOS refuses the first
launch. The installer engine itself does not run `spctl` in this mode: the pinned
signature and the sealed carrier are the evidence, and the run records that
Gatekeeper was not assessed.

## Identity

One **single** self-signed code-signing certificate is generated once
(`basicConstraints CA:FALSE`, `keyUsage digitalSignature`, `extendedKeyUsage
codeSigning`; 730-day validity). The leaf is its own anchor: there is no separate
root certificate. The private key never enters the repository; delete the machine
copy after the secrets are stored.

| Value | |
|---|---|
| CN (identity name) | `Veles Self-Signed Acceptance (not a release signer)` |
| Publisher (`sha256:` certificate DER) | `sha256:9d0d1948de28df32ef23091fab0805894d531863ca0b42c44f9ed1b4eb042cc6` |
| Requirement pin (SHA-1) | `24ac699b7ac5aa84f555461adc30b776bbbaa370` |

**Why not a root plus leaf?** On the hosted `macos-26` image (macOS 26.7.2,
`macos-26-arm64` 20261102) `codesign --sign` refuses a certificate whose chain does
not validate: it prints `Warning: unable to build chain to self-signed root for
signer` and then fails with `errSecInternalComponent`. A two-level chain (separate
root plus leaf) fails that way; a single self-signed certificate signs and
`codesign --verify --strict` passes. The same two-level identity signs on macOS
26.5, so the constraint is the runner OS. Trusting the separate root headlessly is
not available (the user-domain `security add-trusted-cert` waits for interactive
authorization), so the identity is one certificate. Reproduced on that runner in a
credential-free probe.

Storage in the `native-release` environment: secrets `VELES_MAC_P12_BASE64`,
`VELES_MAC_P12_PASSWORD`; variables `VELES_MAC_IDENTITY`, `VELES_MAC_PUBLISHER`,
`VELES_MAC_TRUST_SELF_SIGNED=1`. `VELES_MAC_TEAM_ID` and the `VELES_NOTARY_*`
values stay unset.

## Consequences

- A macOS release dispatch can now build, verify, sign and assemble a candidate
  without an Apple account. The run is honest about what it did not obtain.
- Replacing the self-signed leaf with a Developer ID later requires no gate
  change, but the descriptor must be updated too: clear `VELES_MAC_TRUST_SELF_SIGNED`,
  replace the `sha256:` publisher in `config/release-macos.json` with
  `team:<TEAMID>`, set `VELES_MAC_TEAM_ID` and the notary inputs, and the gate
  returns to the notarized Developer ID path.
- The acceptance matrix remains BLOCKED: a self-signed candidate is not physical
  acceptance evidence, and every row still needs independent adjudication.
