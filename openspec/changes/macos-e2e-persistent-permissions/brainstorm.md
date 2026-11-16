## Design Summary

Make real macOS E2E Bluetooth testing reuse one already-granted TCC identity instead of triggering a
permission prompt every session. The rule lives in the repo's macOS node guidance, not in the
always-on root rules, and prescribes one fixed app identity, one signing identity, one supported
launch route, and a preflight built from evidence the repository already produces.

The change is documentation and test-bundle naming: a node-scoped rule, its listing in the macOS node
`AGENTS.md`, an explicit mandatory handoff in the host node `AGENTS.md` (the Bluetooth test bundle and
its packaging script live in the host node, but the rule is macOS-scoped and is not auto-delivered
there), and the test identity changes from `VelesOtp23PoC.app` / `me.nagaev.veles.otp23.poc` to
`VelesOtpDebug.app` / `me.nagaev.veles.debug`. The certificate and team stay; the new identifier is a
fresh TCC identity, so the retired `me.nagaev.veles.otp23.poc` grant is neither reused nor reset and
the owner accepts one new grant. It adds no host code, no CI job, no credential and no new probe
binary.

## Alternatives Considered

### Alternative A: Keep ad-hoc signing and accept the prompt (status quo)
- **Approach**: every test build is packaged as an ad-hoc-signed `.app` (as the OTP-10 macOS run did
  with `VelesNativeBridge.app`), so macOS sees every build as a different app.
- **Pros**: no certificate or account dependency; any machine can build; no keychain access needed.
- **Cons**: the designated requirement (DR) is `cdhash H"..."`, which changes on every rebuild, so
  the stored grant no longer matches and macOS asks again. Every session needs a person at the Mac.
- **Why not chosen**: this is exactly the reported problem. Evidence on the Mac: the existing
  `VelesNativeBridge.app` is ad-hoc with DR `cdhash H"561c66cd..."`; two ad-hoc builds of the same
  probe produced two different cdhash requirements.

### Alternative B: Introduce a long-lived project self-signed certificate for test builds
- **Approach**: create one self-signed code-signing certificate on the Mac (like the installer's
  acceptance identity) and sign all test bundles with it.
- **Pros**: no Apple account or team; a long validity reduces renewal churn; the DR is a stable
  certificate hash.
- **Cons**: adds a second project identity and a trust/setup step next to the already-approved Apple
  Development identity; the private key still lives in the login keychain, so signing still needs the
  GUI session; it diverges from the node's existing canonical signing rule without evidence that
  Apple Development is insufficient.
- **Why not chosen**: the approved Apple Development identity is already present on the Mac and
  already yields a stable DR across rebuilds. A self-signed test identity is a possible later
  migration, not a second parallel identity now.

### Alternative C: One approved identity, one launch route, existing evidence (chosen)
- **Approach**: build the test bundle with a fixed bundle identifier and the approved Apple
  Development certificate, never ad-hoc; run the host that Chrome starts from inside that bundle via
  the Native Messaging manifest; verify the signature/DR before launch and read the first
  `start_scan` result as the authorization observation.
- **Pros**: reuses the existing one-time grant; survives rebuilds, binary changes and path changes;
  needs no new certificate, trust anchor, TCC reset or probe; matches the production launch path.
- **Cons**: signing needs the GUI session's keychain; a changed bundle id, certificate/team or macOS
  account can change the identity and need one new person-approved grant.
- **Why not chosen**: chosen.

## Agreed Approach

Adopt Alternative C and write it as `.agents/rules/macos-test-app-tcc.md`, scoped to the macOS node
(`native-bridge/installer/macos/**`) and listed in that node's `AGENTS.md`. Because its principal
consumer is the host node, `native-bridge/host/AGENTS.md` gets an explicit instruction to read the
rule by hand before any macOS Bluetooth E2E run and to treat it as mandatory; the rule text is not
copied into the host file. The rule states:

1. **One identity.** The canonical Bluetooth test bundle is `VelesOtpDebug.app`, bundle identifier
   `me.nagaev.veles.debug`, signed with the node's recorded Apple Development certificate
   (`Apple Development: yuriy@nagaev.me (ZWYYS8X98S)`, Team `7T39M26Z99`); the production host
   executable is embedded in it, as `native-bridge/host/scripts/macos-test-bundle.sh` already does.
   This test identity is test-vehicle provenance, not evidence for the production distribution
   identity (CI credentials, Developer ID, notarization).
2. **Sign for stability.** Keep the bundle identifier and signing identity fixed; never ad-hoc,
   never "Sign to Run Locally", never a per-build certificate. A DR of `cdhash H"..."` is a defect.
3. **Launch through the supported route.** The production-shaped E2E route is Chrome starting the
   Native Messaging host whose registered manifest `path` is the executable inside the canonical
   signed test bundle; the manifest name is independent of the bundle identifier. For an app that is
   itself the test subject or a driver, launch it through LaunchServices (`open`) from the logged-in
   GUI session and have it spawn the host from inside the same signed bundle. A headless stdio host
   cannot be driven by `open` alone, and the process that touches CoreBluetooth must be the
   executable inside the signed bundle. Never execute `Contents/MacOS/<exe>` or a bare `target/...`
   binary from a terminal or SSH session: the one measured case attributed the request to
   `sshd-keygen-wrapper`, and other non-LaunchServices terminal contexts are unverified.
4. **Verify, don't reset.** Before launch: `codesign --verify --strict`, `codesign -dvvv` (approved
   identifier/team/authority) and `codesign -d -r-` (an identity requirement, not `cdhash`) prove the
   identity is stable; they do not prove the grant is present. The ordinary first `start_scan` proves
   the app is authorized when it reports `scan_state started`, but it does **not** by itself prove
   that no prompt was answered: authorization may become determined while the scan waits. The
   separate evidence for reuse is a **validated** unified TCC log window: capture from just before
   the launch to after the first scan result with `log show --info`, filtered on the bundle
   identifier and on the responsible process/executable path. The capture is meaningful only when it
   contains the expected `tccd` access records for that subject in the window (the positive control);
   a capture that is empty, unreadable, wrongly filtered, verbosity-limited or otherwise unvalidated
   is INCONCLUSIVE/BLOCKED, never "no prompt". With coverage established, the absence of
   `AUTHREQ_PROMPTING`/`CFUserNotification` and of a grant-write event (`TCCDEvent` `Create`/`Delete`)
   is the reuse evidence. Where log visibility is unavailable (permissions, retention, info level),
   record BLOCKED and rely on the attended operator observation; do not claim reuse. An unattended run
   records the validated window; in an attended run a prompt is also a visible failure and the
   operator reports it. `scan_failed` with `stage: permission_pending` means authorization was still
   undetermined when the scan budget expired (a prompt may have been waiting, or the launch path was
   wrong) — stop and fix, never retry. An optional in-app `CBManager.authorization` read may confirm
   `allowedAlways`; it reports current state, not when the grant was created. A one-time Allow is
   expected only for a new identity: first use, a changed bundle id, a different macOS account, an
   explicit reset, or a replaced/renewed certificate or team **if the DR comparison shows it
   changed**; record it. Never `tccutil reset` or re-toggle to "get a prompt back".
5. **Treat a changed identity as a migration.** A new certificate, team, bundle id or account can
   change the TCC identity. Compare the DR before and after; if it changed, plan one person-approved
   grant, revalidate, then reuse that identity. Rebuilding the same executable does not change the
   DR.

The rule is deliberately tooling-neutral: it names macOS mechanisms (`codesign`, LaunchServices,
Chrome Native Messaging, `CBManager.authorization`, System Settings) and the repository's approved
identity/script, not the machine-specific remote-control tooling used to reach a Mac.

## Key Decisions

- **Node scope, not root.** The rule is a `macos-*` rule listed by
  `native-bridge/installer/macos/AGENTS.md` (the file already declared canonical for macOS signing
  and TCC), with a mandatory read-by-hand instruction in `native-bridge/host/AGENTS.md` because the
  macOS glob does not deliver the rule under `native-bridge/host/`.
- **Reuse the approved certificate and team; migrate the identity once.** No new certificate, no key
  export, no keychain changes. The identifier move to `me.nagaev.veles.debug` is a new TCC identity:
  the owner accepts one fresh grant, and the retired `me.nagaev.veles.otp23.poc` grant is neither
  reused nor reset.
- **No new tool, and no negative claim without coverage.** The preflight is the existing signature
  checks plus a validated unified TCC log window (a positive control proves the capture observed the
  run); an unevidenced or unreadable window is BLOCKED, not "no prompt". The host's own first
  `start_scan` is an authorization observation, not proof by itself that the grant pre-existed.
- **Test the mechanism, not just the signature.** The investigation proved the full chain on the Mac:
  one grant, then a different signed binary, a copied path and the real production host all reused it
  with no prompt (the TCC log showed no prompt or grant event after the one-time Allow); ad-hoc builds
  and SSH launches defeated it.
- **Documentation, naming and test-tool change.** The packaging script and the acceptance driver
  change only the app name and identifier; the host implementation and the signing identity/team stay
  the same. The missing piece for persistent permissions was guidance, not host code.

## Open Questions

- Whether a future change should add a small diagnostic subcommand that reports
  `CBManager.authorization` without a scan. It is not required now: a validated TCC log window
  already distinguishes a reused grant from a prompt answered during the run.
- The TCC log window's retention and verbosity are local to the Mac's OS version and log settings;
  the rule treats an unvalidated window as BLOCKED, and a future tool could make the coverage check
  mechanical.
- A future change may revisit Alternative B (a long-lived self-signed test identity) if Apple
  Development certificate replacement or renewal becomes disruptive; that is a separate decision
  with its own migration and does not change this rule's invariant.
