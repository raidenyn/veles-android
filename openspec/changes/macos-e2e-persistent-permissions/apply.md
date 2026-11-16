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
- `native-bridge/host/scripts/macos-test-bundle.sh` and `test_macos_test_bundle.sh`: the produced app
  and identity record are renamed from `VelesOtp23PoC.app`/`.identity.txt` to
  `VelesOtpDebug.app`/`.identity.txt`, and `CFBundleName` is `VelesOtpDebug`. The bundle identifier
  and signing requirement are unchanged, so the existing TCC grant is reused. The shell test also
  asserts the new `CFBundleName`.
- `native-bridge/host/scripts/accdrv/macid.py` and `test_driver.py`: the acceptance driver accepts
  both the canonical `VelesOtpDebug.app` and the legacy `VelesOtp23PoC.app`, keeping the bundle
  identifier, team, authority and Apple-anchor checks; a legacy-acceptance test was added.
- `openspec/changes/macos-e2e-persistent-permissions/`: planning artifacts, review records, the
  evidence record, the preserved probe/driver harness and the raw TCC windows.

No host code, CI, credential, signing-identity or signing-requirement change.

## Task completion

All tasks 1.1–5.2 in `tasks.md` are complete. The rule was created, the agent-file check observed to
fail on the missing listing and then pass after the two `AGENTS.md` updates, the specs were checked
against the rule, and the real-Mac run was recorded.

## Verification performed

- `python3 docs/checks/check_agent_files.py --root .` — exit 0.
- `python3 -m unittest discover -s docs/checks -p 'test_*.py'` — 79 tests OK.
- `bash native-bridge/host/scripts/test_macos_test_bundle.sh` — 69 checks, 0 failed.
- `cd native-bridge/host/scripts && python3 -m unittest test_driver test_anchor` — 220 tests OK
  (including the new legacy-app-name acceptance test).
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
- The test bundle was renamed from `VelesOtp23PoC.app` to `VelesOtpDebug.app` after the first
  verification. The same host binary was repackaged and re-verified: the designated requirement,
  scan, and TCC window results are unchanged. The acceptance driver now accepts both names, keeping
  its signing checks; its full suite passes.
- The branch was not pushed and no pull request was opened: the request was the rule and its
  verification, not a release.
