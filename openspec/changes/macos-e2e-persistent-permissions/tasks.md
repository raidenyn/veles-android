## 1. Rule file

- [x] 1.1 Create `.agents/rules/macos-test-app-tcc.md` with frontmatter `trigger: glob`, `globs` and
  `paths` both exactly `native-bridge/installer/macos/**` (no brace expansion).
- [x] 1.2 Write the "Build the canonical test app" section: bundle `VelesOtp23PoC.app`, identifier
  `me.nagaev.veles.otp23.poc`, Apple Development `yuriy@nagaev.me (ZWYYS8X98S)`, Team `7T39M26Z99`,
  `native-bridge/host/scripts/macos-test-bundle.sh`, the exact baseline designated requirement,
  GUI-session signing, no ad-hoc, and path independence.
- [x] 1.3 Write the "Launch the way production does" section: Chrome Native Messaging manifest `path`
  inside the signed bundle, LaunchServices for an app or driver, never a bare executable from a
  terminal or SSH session, the measured `sshd-keygen-wrapper` case and the unverified-context caveat.
- [x] 1.4 Write the "Verify that the grant is reused" section: the three `codesign` checks compared
  with the baseline, the broad service-scoped `log show` predicate, the coverage records, the separate
  prompt and grant-write searches, the scan-result caveat, `Allowed (User Consent)` clarification and
  the BLOCKED outcome.
- [x] 1.5 Write the "One-time grant and identity migration" and "Never" sections: new-identity
  conditions, DR comparison, the production-shaped second identity, and the prohibitions on
  `tccutil reset`, ad-hoc substitution and unplanned retries.

## 2. Node guidance and handoff

- [x] 2.1 In `native-bridge/installer/macos/AGENTS.md`, list `.agents/rules/macos-test-app-tcc.md`
  under `## Rules` and reference it from the "Signing identity and TCC (macOS)" section.
- [x] 2.2 In `native-bridge/host/AGENTS.md`, add the mandatory read-by-hand instruction for macOS
  Bluetooth E2E runs and keep the one-line pointer to the macOS node; do not copy the rule text.

## 3. Consistency

- [x] 3.1 Check the delivered rule against the two spec deltas: identity and baseline, launch routes,
  log coverage, no reset or unplanned retry, and the planned first-grant exception.
- [x] 3.2 Confirm that no root `AGENTS.md`, `.github/components.json`, CI, code or platform-identity
  change is needed; no new node or scope is introduced.

## 4. Static checks

- [x] 4.1 `python3 docs/checks/check_agent_files.py --root .` exits 0 with no finding for the new
  rule or its listings.
- [x] 4.2 `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v` passes.
- [x] 4.3 `openspec validate macos-e2e-persistent-permissions` and `openspec validate --all` pass.
- [x] 4.4 `wc -c .agents/rules/macos-test-app-tcc.md` is below 24,000 bytes.

## 5. Real Mac verification

- [x] 5.1 On the Mac, build the current `veles-native-host` (its release binary comes from the host
  target directory, e.g. `build/native-bridge/host-target/release/veles-native-host`), then run
  `native-bridge/host/scripts/macos-test-bundle.sh -b <binary> -o <fresh output directory>` in the
  GUI session. The script rejects a populated output directory, so use a new one per run. Record
  `codesign -d -r-` and confirm it equals the baseline.
- [x] 5.2 Launch the rebuilt binary through a supported route and capture the TCC window; confirm the
  coverage records are present, the prompt and grant-write searches are empty, and the first scan
  starts without a prompt.
- [x] 5.3 Append the run (commands, executable and cdhash values, designated requirement, log files,
  timestamps) to the change evidence.

## 6. Commit

- [x] 6.1 Confirm `git diff --stat` is documentation-only and commit as
  `docs(rules): reuse one macOS Bluetooth test identity`.
