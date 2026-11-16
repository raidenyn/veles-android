## Context

Real macOS E2E Bluetooth runs (Chrome extension → Native Messaging host → phone) currently ask for
Bluetooth access at the start of every session. The macOS test bundles are ad-hoc signed, so their
designated requirement (DR) is a per-build `cdhash`; a rebuild no longer matches the stored grant and
macOS presents the permission dialog again. Launching from an SSH shell also defeats the grant
because TCC attributes the Bluetooth request to the remote shell (`sshd-keygen-wrapper`), not to the
app. The result is a manual click per session and unattended macOS runs that cannot start.

The repository already has a canonical macOS signing/TCC node
(`native-bridge/installer/macos/AGENTS.md`) and a test packaging script
(`native-bridge/host/scripts/macos-test-bundle.sh`) that signs the production host with the approved
Apple Development identity. The missing piece is node-scoped operational guidance, not code, CI or
credentials.

## Goals / Non-Goals

**Goals:**

- One node-scoped rule that makes a real macOS Bluetooth E2E session reuse an already-granted TCC
  identity, with a person approving Bluetooth at most once per identity.
- A verification method that proves identity stability and detects a prompt or a grant write with
  existing macOS facilities, without a new tool.
- Keep macOS signing/TCC guidance canonical in the macOS node, with a mandatory handoff where the
  Bluetooth test bundle actually lives (the host node).
- Update the two affected specs so the layout and testing requirements match the rule.

**Non-Goals:**

- No new signing identity or certificate, no key export, no keychain ACL change, no TCC reset.
- No host code, CI, credential or signing-identity change; the test-bundle script's app name changes,
  and no new probe or diagnostic command is added.
- No change to the production distribution identity (CI credentials, Developer ID, notarization).
- No root-level (`repo-*`) rule and no Windows guidance.

## Decisions

### D1. Rule location and scope

Add `.agents/rules/macos-test-app-tcc.md` with the `macos` scope
(`native-bridge/installer/macos/**`) and list it in `native-bridge/installer/macos/AGENTS.md`.

The macOS node file is already the canonical home of macOS signing/TCC rules, and the repository
spec forbids macOS facts at the root. The rule is listed by its scope's `AGENTS.md`, as the agent-file
check requires, and is not an always-on `repo-*` rule.

### D2. Mandatory handoff from the host node

`native-bridge/host/AGENTS.md` gets an explicit instruction to read
`.agents/rules/macos-test-app-tcc.md` by hand before any macOS Bluetooth E2E run, because the macOS
glob does not deliver the rule under `native-bridge/host/`. The rule text is not copied into the host
file; the host keeps its one-line pointer to the canonical macOS guidance.

Without the handoff, the node whose script and host cause the problem would not load the rule. The
alternative — widening the rule to `native-bridge/**` or root — would load macOS TCC guidance for
Windows and Tauri work and contradicts the platform-home spec.

### D3. One test identity

The canonical Bluetooth test vehicle stays `VelesOtpDebug.app`, bundle identifier
`me.nagaev.veles.otp23.poc`, signed with `Apple Development: yuriy@nagaev.me (ZWYYS8X98S)`
(Team `7T39M26Z99`), carrying the production `veles-native-host` as
`native-bridge/host/scripts/macos-test-bundle.sh` already does. A fresh output directory per build is
fine; the grant is keyed by identity, not path.

A production-shaped bundle identifier (for example `me.nagaev.veles.bridge`) is a second fixed
identity with its own one-time grant, used only when a run genuinely needs that identifier. The test
identity is test-vehicle provenance, never evidence for the production distribution identity.

### D4. Supported launch contract

Primary route: Chrome starts the Native Messaging host through a registered manifest whose `path` is
the executable inside the canonical signed test bundle; the manifest name is independent of the
bundle identifier. A GUI app or a driver that is itself the test subject is launched through
LaunchServices (`open`) from the logged-in GUI session and spawns the host from inside the same
signed bundle. A headless stdio host cannot be driven by `open` alone.

Executing `Contents/MacOS/<exe>` or a bare `target/...` binary from a terminal or SSH session is
forbidden: the one measured SSH case attributed the request to `sshd-keygen-wrapper`; other
non-LaunchServices terminal contexts are unverified and treated the same way.

### D5. Verification uses existing evidence, with coverage

Before launch, compare `codesign -d -r-` against the **accepted designated requirement recorded in
`.agents/rules/macos-test-app-tcc.md`** (the rule quotes the current baseline verbatim), and run
`codesign --verify --strict` and `codesign -dvvv` for the identifier, team and authority. A
requirement that is not a `cdhash` but differs from the recorded baseline is an identity change, not
reuse; the first signed build records the baseline once. The current baseline for the canonical test
identity is:

```text
identifier "me.nagaev.veles.otp23.poc" and anchor apple generic and certificate
leaf[subject.CN] = "Apple Development: yuriy@nagaev.me (ZWYYS8X98S)" and
certificate 1[field.1.2.840.113635.100.6.2.1] /* exists */
```

The first `start_scan` proves authorization when it reports `scan_state started`, but not that no
prompt was answered: authorization may become determined while the scan waits, and
`permission_pending` is a timeout classification, not a pre-scan check.

Reuse is evidenced by a **validated** unified TCC log window:

- Retain a broad service-scoped window from just before the launch to after the first scan result:
  `log show --start <before> --end <after> --style ndjson --info --predicate 'process == "tccd" AND
  (eventMessage CONTAINS "<bundle id>" OR eventMessage CONTAINS "<executable path>" OR eventMessage
  CONTAINS "kTCCServiceBluetoothAlways")'`. The service clause is required: a `TCCDEvent` grant write
  carries the identifier and service but may carry no executable path, so a path-only filter would
  silently drop it.
- Positive control: the retained window contains `AUTHREQ_SUBJECT subject=<bundle id>`, its
  `staticCode` record and a `Handling access request to kTCCServiceBluetoothAlways` for that subject.
- Prompt search over the retained window: any `AUTHREQ_PROMPTING` for `kTCCServiceBluetoothAlways`
  naming the subject or its responsible process, and any `CFUserNotification` response for it.
- Grant-write search as a **separate** identifier/service query over the same window: a `TCCDEvent`
  `type=Create`/`Delete` with `service=kTCCServiceBluetoothAlways` and `identifier=<bundle id>`.
- Only when coverage is present and the prompt and grant-write searches are empty is the grant
  reused.

An empty, unreadable, wrongly filtered or verbosity-limited capture is INCONCLUSIVE/BLOCKED, never
"no prompt". If log visibility is unavailable, the attended operator observation is used and reuse is
not claimed. `Allowed (User Consent)` in a `Handling access request` record is the stored consent and
is not itself a new grant; a new grant is the `TCCDEvent` `Create`.

The recorded experiment keeps the exact predicates and the raw windows: `grant-v1.ndjson` (one
`AUTHREQ_PROMPTING` and one `TCCDEvent Create`), `reuse-v2.ndjson`, `reuse-copy.ndjson` and
`real-host.ndjson` (coverage records, no prompt and no grant write), and `ssh-negative.ndjson`
(subject `sshd-keygen-wrapper`). See `evidence/macos-tcc-reuse-2026-11-16.md` and
`evidence/logs/`.

A one-time Allow is expected only for a new identity: first use, a changed bundle id, a different
macOS account, an explicit reset, or a replaced/renewed certificate or team **if the DR comparison
shows it changed**.

### D6. Spec deltas

- `agent-guidance-layout`: modify *Platform facts live in one canonical folder* so macOS TCC guidance
  covers the macOS node folder including its `macos-*` rule file, and the host still points there
  instead of copying it.
- `real-device-testing`: add a requirement that macOS Bluetooth test apps reuse one granted TCC
  identity, with scenarios for rebuild reuse, ad-hoc rejection, launch-path attribution, and an
  inconclusive log capture.

## Risks / Trade-offs

- **A replaced or renewed Apple Development certificate changes the DR** → compare
  `codesign -d -r-` before and after; if it changed, plan one person-approved grant and revalidate.
- **A second production-shaped bundle id is a second identity** → prefer the canonical test id; use
  the second only when the run requires it and plan its one-time grant.
- **The host node does not auto-load the macOS rule** → the explicit mandatory handoff in
  `native-bridge/host/AGENTS.md`; the macOS node remains canonical.
- **TCC log retention, verbosity or permissions vary** → the rule accepts only a validated window and
  records BLOCKED when coverage cannot be established; an attended prompt is also a visible failure.
- **Test signing is confused with production provenance** → the rule and specs state that the test
  identity is not the production distribution identity.
- **A genuinely new identity still needs a person** → one-time setup per identity, not a per-session
  prompt; the grant then survives rebuilds.

## Migration Plan

Documentation and test-bundle naming commit: the rule, the two `AGENTS.md` updates, the app-name
rename in `macos-test-bundle.sh` and its shell test, the `accdrv/macid.py` acceptance of the new and
legacy names with its test, and the spec deltas. There is no
deployment, data migration or rollback beyond reverting the commit. Existing Mac test apps remain
usable; a session that switches to the canonical bundle uses the grant created during the
investigation, or one new Allow if its identity differs.

## Open Questions

- Whether a future change should add a diagnostic subcommand that reports authorization without a
  scan; not required now.
- Whether a long-lived self-signed test identity should replace Apple Development if certificate
  replacement becomes disruptive; a separate decision with its own migration.
