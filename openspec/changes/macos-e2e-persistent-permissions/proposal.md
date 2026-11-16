## Why

Real macOS E2E Bluetooth runs currently ask for Bluetooth access at the start of every session.
Although the canonical test-bundle script already signs with the approved Apple Development identity,
real runs have also used ad-hoc-signed bundles or bare executables; their TCC requirement is a
per-build cdhash, so a rebuild no longer matches the stored grant and macOS prompts again. Launching
from an SSH shell also defeats the grant. A person must click Allow per session, and unattended macOS
runs cannot start.

## What Changes

**macOS Bluetooth test app identity**
- From: a run may build or use an ad-hoc-signed bundle (or a bare executable) and ask for Bluetooth;
  the grant cannot be reused.
- To: one node-scoped rule fixes the bundle identifier, the approved Apple Development identity, the
  supported launch route and a validated evidence check, so a one-time grant is reused across
  rebuilds and paths.
- Reason: removes a manual, per-session prompt and makes unattended macOS E2E possible.
- Impact: non-breaking, documentation, agent guidance and the test-bundle script's app name; no host
  code, CI, signing or credential change.

**Specific edits**
- Add `.agents/rules/macos-test-app-tcc.md` (macOS scope) and list it in
  `native-bridge/installer/macos/AGENTS.md`.
- Add the mandatory read-by-hand handoff in `native-bridge/host/AGENTS.md`.
- Rename the app produced by `native-bridge/host/scripts/macos-test-bundle.sh` (and its shell test)
  from `VelesOtp23PoC.app` to `VelesOtpDebug.app`; the bundle identifier and signing requirement stay
  unchanged, so the existing TCC grant is reused.
- Accept both the canonical and legacy app names in `native-bridge/host/scripts/accdrv/macid.py`,
  keeping its bundle-identifier and signature checks, and add a legacy-acceptance test.
- Update the `agent-guidance-layout` and `real-device-testing` specs.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `agent-guidance-layout`: macOS TCC guidance may live in the macOS node's `macos-*` rule file, still
  canonical in that node; the host points there instead of copying it.
- `real-device-testing`: macOS Bluetooth test apps must reuse one granted TCC identity and verify
  reuse with validated evidence.

## Impact

Documentation, agent guidance and test-bundle naming/path acceptance:
`.agents/rules/macos-test-app-tcc.md`, `native-bridge/installer/macos/AGENTS.md`,
`native-bridge/host/AGENTS.md`, `native-bridge/host/scripts/macos-test-bundle.sh` and its shell test,
`native-bridge/host/scripts/accdrv/macid.py` and its driver test, the two spec deltas, and the change
directory. No host implementation, API, dependency, CI, signing or credential change. Verification:
the agent-file check and its tests, the test-bundle shell test, the acceptance-driver unit tests, plus
the recorded Mac experiment in `evidence/macos-tcc-reuse-2026-11-16.md` (one grant reused by a
different binary, a copied path, the real host under the old name, and the renamed app; ad-hoc and SSH
launches defeated it).
