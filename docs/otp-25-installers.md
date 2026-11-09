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

A green native release run (per-platform `PASS` from `release-native-installers.yml`) means
the selected platform built, signed and passed the internal verification gate in that run.
It is **not** acceptance-matrix completion and does not change any row above. A Windows-only
run may succeed as a signed Windows candidate while macOS and all 32 physical cases remain
`BLOCKED`; the existing Windows-only completion scope and macOS blockers are unchanged.

## Signing identity for this completion (owner decision, 2026-11-08)

The project completes with a **self-signed** signing identity. A publicly trusted CA
certificate is deferred; it will be obtained outside project completion. The gates were
modified to accept that identity explicitly, not quietly:

- The Windows sign stage reads `VELES_WIN_TRUST_SELF_SIGNED=1` and
  `VELES_WIN_ROOT_CER_BASE64`. It imports the supplied root as an **ephemeral, recorded
  trust anchor** only for the verification step, then removes it. The leaf SHA-1/SHA-256
  pins, the RFC 3161 timestamp and the signed-content correspondence checks are unchanged.
- Every Windows signature record carries `public_trust: false`, `trust_mode: "self-signed"`
  and the anchor's SHA-256. `validate` refuses a self-signed signature that does not record
  its anchor, and refuses an unrecorded trust mode.
- **Not claimed:** public trust, CA chain, SmartScreen reputation, Gatekeeper, notarization
  or stapling. A self-signed completion build is for acceptance testing, never general
  distribution.
- An acceptance machine must trust the anchor for the engine's `WinVerifyTrust` call:
  `certutil -addstore -f Root <root.cer>` as an administrator, recorded in the run's
  environment notes.
- macOS is covered by the separate [2026-11-09 decision](decisions/2026-11-09-self-signed-macos-acceptance.md):
  a self-signed leaf is authorized by its SHA-256 fingerprint, the DER-entitlements slot is decoded and
  cross-checked, and notarization, stapling and Gatekeeper are recorded as not attempted or not claimed.
  A self-signed macOS candidate is not publicly trusted and is not notarized.

### Extension identity (development for now)

The installer gate resolves the extension id from a single file,
`native-bridge/installer/config/extension-ids.json`. A release config names an alias
(`production`), and the descriptor carries the resolved raw 32-character id. The
`production` entry currently holds the repository development id so packaging and the
signing gates are not blocked on a Web Store listing. Replace that one entry with the
real Store item id at the final stage (plan Task 16); no other installer config changes.

## For users

Installation is per user, needs no administrator password and never runs a service.

1. Download the **signed** installer for your OS from the official Veles release page
   (the project publishes none yet; do not use unsigned CI or fixture packages).
   Completion builds under the 2026-11-08 decision are signed with a project self-signed
   identity and are **not publicly trusted**; they are acceptance candidates, not a
   general distribution.
   Windows: run the signed setup executable. macOS: open the signed disk image and run
   *Veles Bridge Installer*. Neither asks for elevation; if one appears, stop and report it.
2. Choose **Install**. If a host is running the installer asks you to **close every Veles
   connector tab in Chrome (all Chrome profiles) and choose Retry**. The installer never
   closes Chrome or any other program for you.
3. Open the Veles connector in Chrome. **The host starts on demand**: Chrome launches it when
   the connector opens and it exits when the tab closes. The installer never starts a host and
   installs no service, launch agent, scheduled task or autostart entry. Bluetooth permission
   is still yours to grant; installing does not prove it.

**Maintenance app (repair, update, roll back, uninstall).** Open the signed installer for
maintenance; its UI runs the engine the package carries. Install publishes a verified,
journaled copy of the maintenance engine and the signed uninstaller at one persistent path
set below the install root, and both frontends and the engine use exactly those paths:

| OS | Install root | Persistent maintenance paths | Also reachable from |
|---|---|---|---|
| Windows | `%LOCALAPPDATA%\Veles\NativeBridge` | `…\maintenance\veles-bridge-maintenance.exe`, `…\maintenance\uninstall.exe` | Settings > Apps > Installed apps > *Veles Native Bridge* |
| macOS | `~/Library/Application Support/Veles/NativeBridge` | `…/maintenance/veles-bridge-maintenance` | re-running the signed installer |

The copies are taken from the authenticated installed carrier/package, are read back and
verified against the journaled identity, and are owned by the receipt: a foreign file at one
of those paths blocks the install instead of being overwritten, an upgrade replaces them and
restores the previous bytes if activation fails, repair restores a damaged copy, and uninstall
removes them. The immutable copies inside `versions/<version>-<id>/` stay as the retained
recovery assets.

* **Repair** restores damaged managed files from the signed installer **of the installed
  version**; with a newer installer the UI offers **Update** instead. If the local recovery
  content is damaged the product asks for a **fresh signed download** for that exact version;
  it does not fetch anything itself and there is no automatic updater.
* **Update** is a newer signed installer. **Roll back** returns to the retained verified
  version when one exists. An older installer never silently downgrades.
* **Interrupted change.** Install, Repair and Roll back first settle a pending activation from
  the authenticated journal, so choosing the action again (Retry) finishes or restores the
  previous state. Inspect stays read-only, and an interrupted removal is finished only by
  Uninstall. State that is foreign, unsupported or contradictory is refused, not reinterpreted.
* **Uninstall** removes only this product's registration, manifest, versions, receipts and
  maintenance assets, then tears down the private layout (receipt, journal, lock, `state/`,
  `versions/`, `maintenance/` and the empty install root). A run that cannot observe an owned
  removal reports **cleanup-pending** and keeps its durable intent, never a claim of success;
  unrelated files are left and reported. Windows cannot delete a mapped image while it runs,
  so the uninstaller (NSIS relaunches itself from `%TEMP%`) runs a staged copy of the
  maintenance engine from its own private plugin directory, outside the removable root. Both
  installed maintenance images are ordinary files by then, so an ordinary uninstall completes
  in one pass; a file genuinely held by another process still ends **cleanup-pending** and is
  finished by the outside-root cleanup helper on a retry.
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
* The production extension ID is an external prerequisite. The Windows production config is
  tracked (`config/release-windows.json`) and currently names the allowlist alias `production`,
  which resolves to the development ID until plan Task 16 replaces it with the real Store item
  ID. No macOS production config is tracked. `bridgeInstallerPackageFixture` builds test-only
  fixture packages that cannot register the production host.
* Checks: `./gradlew bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest
  bridgeInstallerScriptsTest`. Native producers, signing and notarization run only on
  Windows/macOS runners and the protected `release-native-installers.yml`.
* **Debug installers (pull requests).** Add the label `windows-installer-build` and/or
  `mac-installer-build`. A `raidenyn` approval of the secret-free
  `native-installer-debug` environment precedes any PR-controlled build; the builders
  compile the exact PR head SHA in debug configuration. Output is
  **DEBUG / NOT FOR RELEASE** and does not require release A/B reproducibility.
* **Signed release installers (owner-only).** Dispatch
  `release-native-installers.yml` manually from `master`, choosing `platform` =
  `windows`, `macos` or `both` (default `windows`). One protected `native-release` job
  per selected platform performs nested signing, assembly, outer signing and
  verification over inputs produced and verified in the same run. There is no release
  tag, manually supplied descriptor digest, external producer run ID or
  `release-build` label in this path. Owner setup, labels, environments and the secret
  audit are in `docs/native-installer-owner-setup.md`.
* `release-build.yml` still builds Android, web, Rust and supply-chain components; it no
  longer builds or gates native installers.

### Provenance and clocks

Each physical verdict must bind, from outside the run (the *anchor*): source commit,
release-descriptor digest, release-config digest, tool-lock digest, unsigned input digest,
signed package digest and signer identity (on Windows the signer is the leaf-certificate
fingerprint `sha256:<64 lowercase hex of the certificate DER>` that the engine's trust layer
authorizes, never a certificate display name; on macOS it is the Developer ID identity). These come from the producers' `provenance.json`
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

1. Sign in as the standard user. Capture `whoami /groups` and `whoami /user` (Windows) or `id` (macOS), and declare the account name in `accounts[].user_name`. The validator reads the documented English section headers and labels, so record the Windows run with an English-language UI; a localized capture is refused (fail closed) rather than guessed.
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
