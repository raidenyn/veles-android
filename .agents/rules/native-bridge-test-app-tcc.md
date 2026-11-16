---
trigger: glob
globs:
  - "native-bridge/installer/macos/**"
  - "native-bridge/host/**"
paths:
  - "native-bridge/installer/macos/**"
  - "native-bridge/host/**"
---

# macOS test apps: one identity, one Bluetooth grant

macOS stores a Bluetooth grant against the app's bundle identifier and its code-signing designated
requirement (DR). Keep both fixed and every test session reuses the grant a person approved once;
change either and macOS treats the build as a new app and asks again.

This rule applies to every macOS app under test from this repository, including the Native
Messaging host test bundle in `native-bridge/host/`.

## Build the canonical test app

- Build a real `.app` bundle with `NSBluetoothAlwaysUsageDescription`, and `LSUIElement` for a
  headless host. A bare executable has no bundle identity.
- Use one fixed bundle identifier and the node's one approved signing identity. For the Bluetooth
  test vehicle that is `VelesOtpDebug.app`, identifier `me.nagaev.veles.debug`, carrying the
  production `veles-native-host`, signed with the Apple Development certificate recorded in
  `native-bridge/installer/macos/AGENTS.md`; `native-bridge/host/scripts/macos-test-bundle.sh`
  builds exactly that.
- The accepted designated requirement for that identity is the baseline. Compare against it; do not
  infer stability from "not a cdhash":

```text
identifier "me.nagaev.veles.debug" and anchor apple generic and certificate
leaf[subject.CN] = "Apple Development: yuriy@nagaev.me (ZWYYS8X98S)" and
certificate 1[field.1.2.840.113635.100.6.2.1] /* exists */
```

- A fresh output directory per build is fine: the grant is keyed by identity, not by path.
- Sign from the logged-in GUI session. Over SSH the login keychain may be locked and `codesign` can
  fail with `errSecInternalComponent`; report that, and never change keychain ACLs or export keys.
- Never ad-hoc sign, never use Xcode's "Sign to Run Locally", and never give a build a different
  bundle identifier or certificate to make it run locally. An ad-hoc DR is `cdhash H"..."` (or a
  linker-generated identifier), so it changes with every build and cannot keep a grant.

## Launch the way production does

- The production-shaped route is Chrome starting the Native Messaging host through a registered
  manifest whose `path` is the executable inside the canonical signed test bundle. The manifest name
  is independent of the bundle identifier. Check that the manifest path resolves inside that bundle.
- An app that is itself the test subject, or a driver that spawns the host, is launched through
  LaunchServices from the logged-in GUI session (`open -n ...`) and spawns the host from inside the
  same signed bundle. A headless stdio host cannot be driven by `open` alone.
- Never start a Bluetooth path by executing `Contents/MacOS/<exe>`, `swift run`, or a bare
  `target/...` binary from a terminal or SSH session. TCC then attributes the request to the parent
  process (observed: `sshd-keygen-wrapper`), the app sees `notDetermined`, and the existing grant is
  not used even though it is still valid. Other non-LaunchServices terminal contexts are unverified
  and treated the same way.

## Verify that the grant is reused; do not recreate it

Run these before the session and compare with the baseline above:

```bash
codesign --verify --strict path/to/App.app
codesign -dvvv path/to/App.app     # Identifier, TeamIdentifier and Authority as approved
codesign -d -r- path/to/App.app    # must equal the accepted requirement, not `cdhash H"..."`
```

Then keep a broad TCC log window from just before the launch to after the first scan result:

```bash
log show --start "<before>" --end "<after>" --style ndjson --info \
  --predicate 'process == "tccd" AND (eventMessage CONTAINS "<bundle id>" OR \
  eventMessage CONTAINS "<executable path>" OR eventMessage CONTAINS "kTCCServiceBluetoothAlways")'
```

The service clause matters: a `TCCDEvent` grant write carries the service and identifier but may
carry no executable path, so a path-only filter would drop it.

- Coverage: the window must contain `AUTHREQ_SUBJECT subject=<bundle id>`, its `staticCode` record
  and a `Handling access request to kTCCServiceBluetoothAlways` for that subject. Without these, the
  capture is INCONCLUSIVE/BLOCKED, never "no prompt".
- Prompt search: no `AUTHREQ_PROMPTING` for `kTCCServiceBluetoothAlways` naming the subject or its
  responsible process, and no `CFUserNotification` response for it.
- Grant-write search: separately, no `TCCDEvent` `type=Create`/`Delete` with
  `service=kTCCServiceBluetoothAlways` and `identifier=<bundle id>`.
- `Allowed (User Consent)` in a `Handling access request` line is the stored consent, not a new
  click; a new grant is the `TCCDEvent Create`.
- The ordinary first `start_scan` reports `scan_state started` when the app is authorized, but it
  does not by itself prove that no prompt was answered: authorization may become determined while
  the scan waits. `scan_failed` with `stage: permission_pending` means authorization was still
  undetermined when the scan budget expired; stop and fix the identity or launch path, never retry.

If log visibility is unavailable (permissions, retention, info level), record BLOCKED and use the
attended operator observation; do not claim reuse.

## One-time grant and identity migration

A one-time Allow is expected only for a new identity: first use, a changed bundle id, a different
macOS account, an explicit reset, or a replaced/renewed certificate or team **if the DR comparison
shows it changed**. A planned, attended first-use run may complete its initial grant; record that it
happened and keep the identity. An unexpected `notDetermined`, `permission_pending` or `denied` during
a reuse run is not retried: stop, fix the identity or launch path, and then repeat.

The previous test identity `me.nagaev.veles.otp23.poc` is retired. Do not reuse or reset its grant;
the identifier change to `me.nagaev.veles.debug` is a new identity and needs one person-approved
grant, after which it is reused like any other identity.

A new certificate, team, bundle id or macOS account can change the identity. Compare the DR before
and after; if it changed, plan one person-approved grant, revalidate, then reuse that identity for
every later session. Rebuilding the same executable does not change the DR.

A production-shaped bundle identifier (for example `me.nagaev.veles.bridge`) is a second fixed
identity with its own one-time grant, used only when a run genuinely needs it. The test identity is
test-vehicle provenance, never evidence for the production distribution identity.

## Never

- Never `tccutil reset` (or toggle the Settings entry off and on) to "get the prompt back"; that
  destroys the grant this rule exists to reuse. A reset happens only when a person explicitly asks
  to observe a prompt.
- Never swap in ad-hoc signing, another certificate, another bundle identifier or a different user
  account to make a local build run.
- Never report a Bluetooth run as tested when it only reached `notDetermined` or `denied`, and never
  retry the same run hoping the prompt resolves itself.
