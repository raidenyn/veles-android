# macOS E2E persistent Bluetooth permission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a node-scoped macOS rule so real Bluetooth E2E runs reuse one granted TCC identity
across rebuilds, binary changes and paths instead of prompting every session.

**Architecture:** Documentation-only. One `macos-*` rule file holds the identity, launch and evidence
procedure; the macOS node lists it; the host node carries a mandatory read-by-hand handoff. Two spec
deltas encode the layout and testing requirements. No code, CI, credential or signing-identity change.

**Tech Stack:** Markdown agent rules, `codesign`, the macOS unified log (`log show`), Chrome Native
Messaging or LaunchServices, OpenSpec 2.0, the `docs` Python checks.

**Spec:** `openspec/changes/macos-e2e-persistent-permissions/design.md` and `specs/` in the same
change directory. The evidence record is `evidence/macos-tcc-reuse-2026-11-16.md` with raw windows
in `evidence/logs/`.

## Global Constraints

- The rule file is `.agents/rules/macos-test-app-tcc.md`; frontmatter keys are only `trigger`,
  `globs`, `paths`; `globs` and `paths` are exactly `native-bridge/installer/macos/**`; the file is
  under 24,000 bytes.
- The rule is listed by `native-bridge/installer/macos/AGENTS.md`; no root `repo-*` rule is added and
  the root `AGENTS.md` is unchanged.
- Canonical identity: bundle `VelesOtp23PoC.app`, identifier `me.nagaev.veles.otp23.poc`, certificate
  `Apple Development: yuriy@nagaev.me (ZWYYS8X98S)`, Team `7T39M26Z99`.
- Accepted designated-requirement baseline, quoted verbatim in the rule:

```text
identifier "me.nagaev.veles.otp23.poc" and anchor apple generic and certificate
leaf[subject.CN] = "Apple Development: yuriy@nagaev.me (ZWYYS8X98S)" and
certificate 1[field.1.2.840.113635.100.6.2.1] /* exists */
```

- Never prescribe `tccutil reset`, ad-hoc signing, a changed bundle identifier/certificate to make a
  build run, or a bare executable from a terminal/SSH session.
- The evidence procedure must retain the service-scoped superset predicate
  (`... OR eventMessage CONTAINS "kTCCServiceBluetoothAlways"`), require the positive-control records,
  and treat an unvalidated capture as BLOCKED.

## Review Focus

- A run that merely looks authorized because a person answered a prompt during the scan: the rule
  must not call `scan_state started` proof of reuse; the validated log window is the proof.
- A path-only log filter dropping a pathless `TCCDEvent` grant write: the rule must keep the
  service-scoped superset and search grant writes separately.
- A replaced certificate that keeps the same designated requirement: reuse, not a forced new grant.
- A planned first-use grant versus an unexpected `notDetermined` during a reuse run: distinct
  behaviors, neither permitting a reset-and-retry.
- A host-node agent that never reads the macOS-scoped rule: the host `AGENTS.md` must mandate reading
  it by hand.

---

### Task 1: Add the macOS rule

**Files:**
- Create: `.agents/rules/macos-test-app-tcc.md`
- Read: `openspec/changes/macos-e2e-persistent-permissions/design.md` D1–D5,
  `native-bridge/installer/macos/AGENTS.md`, `native-bridge/host/scripts/macos-test-bundle.sh`

**Interfaces:**
- Produces: the rule file name and content that Task 2 lists and the checks validate.

- [x] **Step 1: Create the file with the exact frontmatter**

```markdown
---
trigger: glob
globs:
  - "native-bridge/installer/macos/**"
paths:
  - "native-bridge/installer/macos/**"
---
```

- [x] **Step 2: Write the build section**

State the canonical bundle/identifier/certificate/team, the packaging script, the baseline
designated requirement above, GUI-session signing, path independence, and the ad-hoc prohibition.
Use the exact values from Global Constraints; do not invent a second identity.

- [x] **Step 3: Write the launch section**

State the Chrome Native Messaging manifest `path` contract (inside the signed bundle, name
independent of the identifier), the LaunchServices route for an app or driver, the prohibition on
`Contents/MacOS/<exe>`, `swift run` and bare `target/...` binaries from a terminal or SSH session, the
measured `sshd-keygen-wrapper` attribution, and that other terminal contexts are unverified.

- [x] **Step 4: Write the verification section**

Include the three `codesign` commands compared with the baseline; the `log show --start --end --style
ndjson --info` predicate with the bundle id OR executable path OR `kTCCServiceBluetoothAlways`;
the coverage records (`AUTHREQ_SUBJECT`, `staticCode`, `Handling access request to
kTCCServiceBluetoothAlways`); the separate prompt search (`AUTHREQ_PROMPTING`,
`CFUserNotification`) and grant-write search (`TCCDEvent type=Create`/`Delete`, service + identifier);
the `scan_state started` caveat; the `Allowed (User Consent)` clarification; and BLOCKED when the
capture is unvalidated.

- [x] **Step 5: Write the migration and never sections**

State the new-identity conditions (first use, changed bundle id, different macOS account, explicit
reset, replaced/renewed certificate/team only if the DR comparison changed), the planned first-grant
exception, the production-shaped second identity, the DR-comparison migration, and the three
prohibitions (`tccutil reset`, ad-hoc/certificate swap, reporting `notDetermined`/`denied` as tested).
State the stop rule explicitly: an unexpected `notDetermined`, `permission_pending` or `denied` during
a reuse run stops it; there is no unplanned retry and no reset, and the identity or launch path is
fixed first. Only the planned first-use grant is an allowed prompt.

- [x] **Step 6: Verify the file shape**

Run: `head -8 .agents/rules/macos-test-app-tcc.md && wc -c .agents/rules/macos-test-app-tcc.md`
Expected: the frontmatter above; a byte count below 24000.

- [x] **Step 7: Run the agent-file check and observe the expected failure**

Run: `python3 docs/checks/check_agent_files.py --root .`
Expected: a finding that `.agents/rules/macos-test-app-tcc.md` is not listed in
`native-bridge/installer/macos/AGENTS.md` (the failing test for Task 2).

### Task 2: List the rule and add the host handoff

**Files:**
- Modify: `native-bridge/installer/macos/AGENTS.md` (`## Rules` and the TCC section)
- Modify: `native-bridge/host/AGENTS.md` (`## Platform frontends`)

**Interfaces:**
- Consumes: the rule path and name from Task 1.
- Produces: the listings the agent-file check enforces and the handoff the host agent reads.

- [x] **Step 1: Replace the macOS node's `## Rules` "None." with the rule bullet**

The bullet names `.agents/rules/macos-test-app-tcc.md` and says it covers the fixed test-app identity,
the supported launch route and the one-time Bluetooth grant for real E2E runs.

- [x] **Step 2: Reference the rule from the macOS node's TCC section**

Add one sentence after the signing-identity bullets: real macOS Bluetooth E2E runs follow the rule
(one fixed identifier and signing identity, LaunchServices or Chrome launch, no TCC reset).

- [x] **Step 3: Add the mandatory handoff to the host node**

In `## Platform frontends`, keep the one-line pointer to `native-bridge/installer/macos/AGENTS.md`
and add that before any macOS Bluetooth E2E run the agent must read
`.agents/rules/macos-test-app-tcc.md` by hand, because the macOS scope does not deliver it under
`native-bridge/host/`. Do not copy the rule text.

- [x] **Step 4: Run the agent-file check**

Run: `python3 docs/checks/check_agent_files.py --root .`
Expected: exit 0 with no finding for the new rule or the listings.

### Task 3: Static and consistency checks

**Files:** none modified.

**Interfaces:**
- Consumes: Tasks 1–2 and the change's spec deltas.

- [x] **Step 1: Check the rule against the specs**

Re-read `specs/agent-guidance-layout/spec.md` and `specs/real-device-testing/spec.md`; confirm the
rule states the same identity baseline, launch routes, coverage/prompt/grant-write searches, planned
first grant, stop-and-fix rule for unexpected `notDetermined`/`permission_pending`/`denied`, no-reset
rule and DR-conditional migration.

- [x] **Step 2: Run the docs unit tests**

Run: `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`
Expected: OK.

- [x] **Step 3: Validate the change and the spec tree**

Run: `openspec validate macos-e2e-persistent-permissions && openspec validate --all`
Expected: the change is valid; the whole tree reports 23 passed, 0 failed (pre-existing warnings are
allowed).

- [x] **Step 4: Confirm the scope did not widen**

Run: `git diff --stat` and `git status --short`
Expected: only the rule, the two `AGENTS.md` files and the change directory; no root `AGENTS.md`,
`.github/components.json`, CI, code or credential change.

### Task 4: Real Mac verification

**Files:**
- Append: `openspec/changes/macos-e2e-persistent-permissions/evidence/macos-tcc-reuse-2026-11-16.md`
- Add: a fresh raw window under `evidence/logs/` if the run is new

**Interfaces:**
- Consumes: the canonical host source and `native-bridge/host/scripts/macos-test-bundle.sh`.

- [x] **Step 1: Build and package the current host**

On the Mac, in the GUI session: build `veles-native-host` (release binary under the host target
directory, for example `build/native-bridge/host-target/release/veles-native-host`), then run
`native-bridge/host/scripts/macos-test-bundle.sh -b <binary> -o <fresh output directory>`; the script
rejects a populated output directory, so use a new one.

- [x] **Step 2: Verify the signature against the baseline**

Run: `codesign --verify --strict <app> && codesign -dvvv <app> && codesign -d -r- <app>`
Expected: the approved identifier/team/authority and a designated requirement equal to the baseline.

- [x] **Step 3: Run the pinned verification harness and capture the window**

Use the preserved evidence harness in `evidence/mac-tcc-probe/` (its README has the exact commands).
Build the driver with `xcrun swiftc -O -o <out>/driver driver.swift`, package it with
`zsh make-driver-bundle.sh <out>/driver <app>/Contents/MacOS/veles-native-host <app>
<out>/driver-bundle` in the GUI session, and launch it through LaunchServices with
`open -n --stdout ... --stderr ... <out>/driver-bundle/VelesOtp23PoC.app --args
"<app>/Contents/MacOS/veles-native-host" "<out>/result.json" "<origin>"`. The origin must match the
host's build-time allowlist: use `chrome-extension://abcdefghijklmnopabcdefghijklmnop/` for a default
build, or the exact `VELES_ALLOWED_ORIGINS` value. The driver spawns the real host from the signed
bundle and drives `hello → start_scan → stop_scan → shutdown`; it is evidence tooling, not a
production tool. Capture the TCC window with the rule's predicate and save the raw NDJSON under
`evidence/logs/`.

- [x] **Step 4: Judge the evidence**

Confirm the coverage records are present, the prompt search and the grant-write search are empty,
and the scan reports `scan_state started`; otherwise record BLOCKED and do not claim reuse.

- [x] **Step 5: Record the run**

Append the commands, executable and cdhash values, designated requirement, window bounds, log file
names and timestamps to `evidence/macos-tcc-reuse-2026-11-16.md`.

### Task 5: Commit

**Files:** all changed files.

- [x] **Step 1: Review the diff**

Run: `git diff --stat && git diff -- .agents native-bridge/installer/macos/AGENTS.md
native-bridge/host/AGENTS.md`
Expected: documentation only, no secrets or host-specific paths in the rule.

- [x] **Step 2: Commit**

```bash
git add .agents/rules/macos-test-app-tcc.md native-bridge/installer/macos/AGENTS.md \
  native-bridge/host/AGENTS.md openspec/changes/macos-e2e-persistent-permissions
git commit -m "docs(rules): reuse one macOS Bluetooth test identity"
```
