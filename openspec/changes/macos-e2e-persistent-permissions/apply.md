# Apply — macOS E2E persistent Bluetooth permission

**Iteration**: 1
**Applied at**: 2026-11-16 11:50 +07
**Implementer**: OpenCode (`ollama-cloud/deepseek-v4.1-flash:1017`)

## What changed

- **New** `.agents/rules/macos-test-app-tcc.md` (6,874 bytes): the canonical macOS test-app identity,
  the accepted designated-requirement baseline, the supported launch routes, the coverage-validated
  TCC log check, the one-time grant and migration rules, and the prohibitions.
- `native-bridge/installer/macos/AGENTS.md`: the rule is listed under `## Rules` and referenced from
  the signing/TCC section, which remains the macOS canonical home.
- `native-bridge/host/AGENTS.md`: the platform-frontends pointer now instructs the agent to read the
  macOS rule by hand before any macOS Bluetooth E2E run, because the macOS scope does not deliver it
  under `native-bridge/host/`.
- `openspec/changes/macos-e2e-persistent-permissions/`: planning artifacts, review records, the
  evidence record, the preserved probe/driver harness and the raw TCC windows.

No code, CI, credential, signing-identity or packaging-script change.

## Task completion

All tasks 1.1–5.2 in `tasks.md` are complete. The rule was created, the agent-file check observed to
fail on the missing listing and then pass after the two `AGENTS.md` updates, the specs were checked
against the rule, and the real-Mac run was recorded.

## Verification performed

- `python3 docs/checks/check_agent_files.py --root .` — exit 0.
- `python3 -m unittest discover -s docs/checks -p 'test_*.py'` — 79 tests OK.
- `python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py'` — 25 tests
  OK (the build-contract gate reads the edited host `AGENTS.md`).
- `./gradlew bridgeHostToolchainCheck bridgeHostFormat bridgeHostLint bridgeHostTest` — BUILD
  SUCCESSFUL (the changed-node selection includes `native-host` through its `AGENTS.md` override).
- `openspec validate --all` — 23 passed, 0 failed.
- Real Mac: rebuilt `veles-native-host` from `ab15de4` (source sha256 `a1314f77…`, post-sign
  `e7f5f654…`, cdhash `e1264e69…`), packaged with `macos-test-bundle.sh` into a fresh directory, and
  confirmed the designated requirement equals the baseline. The pinned driver launched the real host
  from that bundle; the transcript is `ready → scan_state started → stopped → shutdown_complete`
  (exit 0), and the 45-record TCC window has 4 `AUTHREQ_SUBJECT` and 3 Bluetooth handling records
  with 0 `AUTHREQ_PROMPTING`, 0 `CFUserNotification` and 0 `TCCDEvent` `Create`/`Delete`.

## Deviations

- The Mac verification used the preserved evidence harness (a LaunchServices driver) rather than a
  Chrome Native Messaging run. The rule names Chrome as the primary route and the harness as evidence
  tooling; no product code or identity changed.
- The branch was not pushed and no pull request was opened: the request was the rule and its
  verification, not a release.
