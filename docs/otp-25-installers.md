# OTP-25: per-user native-bridge installers

Authority: [issue #89](https://github.com/raidenyn/veles-android/issues/89),
`docs/superpowers/specs/2026-11-06-otp25-installers-design.md` and the plan
`docs/superpowers/plans/2026-11-06-otp25-installers.md`. Engine, frontends, packaging,
signing gate and acceptance tooling live in `native-bridge/installer/`
(`native-bridge/installer/README.md` is the developer map).

## Status — read this first

**Nothing here is accepted.** The installer is implemented and reviewed locally, but
**no physical acceptance exists**: no signed release, no standard non-admin account run,
no Windows or macOS walkthrough has been performed. The recorded matrix
(`native-bridge/installer/acceptance/matrix.json`, 32 cases = 16 families x 2 platforms) is:

**0 PASS, 0 FAIL, 32 BLOCKED, 0 NOT_RUN.**

* `BLOCKED` means a named prerequisite is absent (no Windows/macOS host, no standard test
  account, no approved release descriptor and signed candidate, no second release, no attended
  Bluetooth consent, no dev build, no trust fixtures). It is neither PASS nor FAIL.
* `NOT_RUN` is reserved for a case whose prerequisites exist but which nobody attempted.
* Implementation review and CI **do not close #89** and do not open Gates B/C/D
  (protected delivery); this document and the matrix also do not close it, and a green
  validator run does not close it either. BLE behaviour, permission-denial recovery of the connector and
  shared native-host cases stay with [#125](https://github.com/raidenyn/veles-android/issues/125)
  and [#127](https://github.com/raidenyn/veles-android/issues/127); the installer cases
  prove installer-owned facts only and never restate those gates.
* Validator success (`evidence.py validate` exit 0) is **necessary, never sufficient**. It
  cannot observe a machine. Acceptance is the owner's separate decision after independent
  adjudication of real evidence.

## For users

Installation is per user, needs no administrator password and never runs a service.

1. Download the **signed** installer for your OS from the official Veles release page
   (the project publishes none yet; do not use unsigned CI or fixture packages).
   Windows: run the signed setup executable. macOS: open the signed disk image and run
   *Veles Bridge Installer*. Neither asks for elevation; if one appears, stop and report it.
2. Choose **Install**. If a host is running the installer asks you to **close every Veles
   connector tab in Chrome (all Chrome profiles) and choose Retry**. The installer never
   closes Chrome or any other program for you.
3. Open the Veles connector in Chrome. **The host starts on demand**: Chrome launches it when
   the connector opens and it exits when the tab closes. The installer never starts a host and
   installs no service, launch agent, scheduled task or autostart entry. Bluetooth permission
   is still yours to grant; installing does not prove it.

**Maintenance app (repair, update, roll back, uninstall).** The installed maintenance tool
is under the install root's `maintenance/` folder:

| OS | Install root | Maintenance location | Also reachable from |
|---|---|---|---|
| Windows | `%LOCALAPPDATA%\Veles\NativeBridge` | `…\NativeBridge\maintenance\` | Settings > Apps > Installed apps > *Veles Native Bridge* |
| macOS | `~/Library/Application Support/Veles/NativeBridge` | `…/NativeBridge/maintenance/` | re-running the signed installer |

* **Repair** restores damaged managed files from the signed same-version installer. If the
  local recovery content is damaged the product asks for a **fresh signed download**; it does
  not fetch anything itself and there is no automatic updater.
* **Update** is a newer signed installer. **Roll back** returns to the retained verified
  version when one exists. An older installer never silently downgrades.
* **Uninstall** removes only this product's registration, manifest, versions, receipts and
  maintenance assets. Unrelated files are left and reported.
* **No trust reset.** None of these operations touches extension trust, Chrome profile data
  or the Bluetooth grant, and none should be worked around by resetting them. If macOS asks
  for Bluetooth consent again, answer the prompt; do not clear permissions to hide it.
* **Offline freshness limitation.** The installer is self-contained and does not contact a
  Veles service, but the operating system's signature/Gatekeeper checks may use the network.
  If a required check is unavailable, activation is blocked with retry guidance; the
  installer never disables revocation or Gatekeeper. A machine that stays offline cannot learn
  that a Veles release was newly revoked until it receives an OS update or a newer signed
  maintenance installer. Cached policy is not "globally fresh".

## For developers

* **Development is separate from production.** Use `register-dev.{sh,ps1}` / `remove-dev.{sh,ps1}`
  with the explicit unpacked extension ID and a separately compiled dev host. They use
  `me.nagaev.veles.bridge.dev` and the `NativeBridgeDev` root; they refuse production IDs,
  roots and binaries, and never touch the production registration. A dev host is not a signed
  release and is never acceptance evidence.
* The production extension ID is an external prerequisite. No `release-<platform>.json` is
  tracked, so `./gradlew bridgeInstallerUnsigned` fails closed on every runner; do not invent
  one. `bridgeInstallerPackageFixture` builds test-only fixture packages that cannot register
  the production host.
* Checks: `./gradlew bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest
  bridgeInstallerScriptsTest`. Native producers, signing and notarization run only on
  Windows/macOS runners and the protected `release-native-installers.yml`.

### Provenance and clocks

Each physical verdict must bind, from outside the run (the *anchor*): source commit,
release-descriptor digest, release-config digest, tool-lock digest, unsigned input digest,
signed package digest and signer identity. These come from the producers' `provenance.json`
and `SHA256SUMS` and the release gate's bundle, never from the test operator. The run adds
OS, architecture and Chrome version, the account (standard / fresh), the controlled actions
with UTC timestamps and observed returns, raw capture checksums, and the clock source with the
measured host offset (within 30 s) captured at the start. Output binary hashes and signature
verification output must appear in raw captures, not only as typed values.

## Acceptance evidence model

* **Cases** (`acceptance/cases.json`): stable ids `<windows|macos>.<family>`; outcomes
  `PASS/FAIL/BLOCKED/NOT_RUN`; each lists required controlled **actions**, required
  **observations**, blockers, issue obligations and the exact settling commands/manual steps.
* **Families (each required on both OSes).** clean-install (no elevation, per-user writes);
  launch (exact-origin Chrome launch, API 1<->1); repair, upgrade, rollback (retention of
  the grant); failed-upgrade (prior install still works); uninstall (host and cleanup-helper
  exit, no residue); fresh-user (no hidden state); origin-negative (unlisted origin rejected);
  dev-coexistence; multi-profile (all sessions closed, Chrome not killed); compatibility
  (overlap and no-overlap); permission-recovery (no reset); Unicode-path (spaces and non-ASCII);
  hygiene (no elevation, system files, autostart or extra endpoints); offline-trust (cached,
  unavailable, OS denial, denied payload, stale-policy rollback).
* **Bundle** (`evidence.py validate --run DIR --cases cases.json --anchor ANCHOR.json
  [--previous OLDDIR]`): `run.json`, hash-chained `ledger.jsonl`, `captures/`, and a separate
  `adjudication.json`. The validator refuses, among others:
  admin or elevated accounts as standard-user proof (the identity capture must positively show the
  declared `accounts[].user_name`, a complete group table and, on Windows, the Medium integrity
  label; empty, truncated or unrelated captures prove nothing); any binding that differs from the external anchor, an
  anchor stored inside the run, or a digest/signer that no raw capture contains
  (self-attested); capture checksum mismatch, missing, unlisted or escaping captures;
  missing required actions/observations; PASS for repair/upgrade/rollback/permission-recovery
  without distinct `grant-before`/`grant-after` observations that fall strictly before and
  strictly after the case's named maintenance action (`requires.retention_action`); an engine
  action whose observed return or captured InstallReport contradicts the case's `dispositions`
  (a clean maintenance result must exit 0 with no failure and no pending cleanup; an expected
  refusal must exit 1 with one of the listed failures); a broken, shortened or
  rewritten ledger; a PASS that does not acknowledge earlier FAILs; self-adjudication or an
  adjudication not bound to the exact `run.json` and ledger head; and publication of raw
  captures without an approved privacy review by someone other than the operator.
* **Append-only.** Failed, blocked and not-run attempts stay in the ledger forever.
  `--previous` proves a later bundle only extended an earlier one.
* **Privacy.** Raw captures can contain user names and paths. They are `withheld` by default;
  publication needs a dated, approved privacy review by a non-operator. No binaries,
  credentials, signing keys or whole-machine traces belong in ordinary commits.

Exit codes: `0` structurally valid, `1` invalid evidence, `2` missing input or environment.

## Standard-user walkthroughs (to be run by a person; not run here)

Prerequisites (all currently absent): a Windows host and a macOS host with Chrome, a
**standard non-administrator** account on each (plus a brand-new standard account for
fresh-user, and one whose profile path has a space and a non-ASCII character), the signed
candidate and the external anchor from the release gate, an attended person for Bluetooth
consent, and a second signed release for upgrade/rollback/compatibility.

1. Sign in as the standard user. Capture `whoami /groups` and `whoami /user` (Windows) or `id` (macOS), and declare the account name in `accounts[].user_name`.
2. Hash and verify the candidate; capture `Get-FileHash` + `signtool verify /pa /all /v`
   (Windows) or `shasum -a 256` + `codesign -dv --verbose=4` + `spctl --assess -vv` +
   `xcrun stapler validate` (macOS).
3. Snapshot before state: HKCU Native Messaging key and uninstall key / manifest file, install
   root file hashes, processes, windows, listening/outbound endpoints.
4. Perform the case's controlled actions exactly as listed in `cases.json`, capturing each
   output and the clock. An elevation prompt is a FAIL.
5. Snapshot after state; for retention cases capture the grant state before and after.
6. Write `run.json` and append to `ledger.jsonl` (including failures and blocked attempts), then
   hand the directory to an independent adjudicator, run `evidence.py validate`, and keep raw
   captures private until a privacy review approves publication.

## BLOCKED cases and what settles them

All 32 cases are BLOCKED. Each `cases.json` entry carries the exact commands and manual steps.
By family: every case needs a Windows/macOS host, a standard account and the signed candidate
(see `B-HOST`, `B-ACCOUNT`, `B-CANDIDATE`); in addition upgrade, failed-upgrade, rollback,
multi-profile, compatibility and offline-trust need `B-SECOND-RELEASE`; repair, upgrade,
rollback and permission-recovery need `B-ATTENDED`; dev-coexistence needs `B-DEV-BUILD`;
offline-trust needs `B-TRUST-FIXTURES`. Settling a case means a person performing it under a
standard account and an independent adjudicator confirming it; a script on this Linux host
cannot.
