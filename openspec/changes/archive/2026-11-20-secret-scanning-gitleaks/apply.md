# Apply Receipt

**Change**: `secret-scanning-gitleaks`
**Iteration**: `1`
**Applied at**: `2026-11-20 14:19`
**Executor**: `subagent-driven-development`

---

## Workspace

- **Worktree**: `/home/dev/.t3/worktrees/veles-android/t3-8d4dbdb8-ab5e3b469bcac0e8`
- **Branch**: `chore/secret-scanning-gitleaks`

---

## Commits

- **Range**: `13d6704d..cf87adba`
- **Count**: `7`

---

## Tasks

- **Completed**: `34 of 34` checkboxes in tasks.md flipped to `- [x]`
- **Remaining**: `none`

---

## Notes

- Owner answer: GitHub secret scanning and push protection are **enabled** (owner report, 2026-11-20). The enforcement model is push protection plus the pre-commit hook, with no CI scan job; the always-run CI scan job and CODEOWNERS coverage are documented follow-ups.
- gitleaks `8.30.1`; the pinned archive entry is `gitleaks` at the archive root and `gitleaks version` prints the bare `8.30.1`.
- Digest mismatch fails naming both digests, deletes the archive and installs nothing; a wrong expected version fails naming both versions. Both checks were restored and confirmed with `git diff`, then the verified binary was reinstalled.
- Full-history scan: 234 findings over 2,284 commits — (a) 211 tracked test data, (b) 13 history-only paths, (c) 0 real secrets, (d) 10 tracked non-test-data false positives. With `.gitleaks.toml` the re-run reports no leaks. Counts only; no value was printed, recorded or committed. The evidence entry was narrowed to `.json`/`.ndjson`, and a fake key in `evidence/run.sh` now fires.
- Allowlisted paths beyond the three expected ones: the two Chrome extension manifests, `native-bridge/host/scripts/accdrv/registration.py`, the archived otp-09 plan, `docs/superpowers/plans/2026-06-30-sensitive-notification-redaction.md`, `verify/test/workflow-contracts.test.mjs` and `web-extension/test/session/client-harness.ts`. Each is line-scoped to its benign token and carries its reason in `.gitleaks.toml`. The line-scope negative control ran for every (d) file: a runtime-generated key on another line was blocked, the file restored with `git restore --staged --worktree`, and `git status --porcelain` confirmed empty.
- The docs and the signing rule state the limits: gitleaks and push protection catch recognised token patterns, not binary keystores (`*.jks`, `*.keystore`, `*.p12`, `*.pfx`, `*.bks`) or weak passwords; those stay protected by `.gitignore` (`android/config/android_keys/`). Push protection covers supported provider patterns only and can be bypassed with a stated reason.
- Hook CLI facts: `gitleaks git --staged --redact --verbose --no-banner --exit-code 3`; the finding report is on stdout and the summary on stderr; gitleaks itself exits 1 or 126, never 3, so 3 is the dedicated leak code.
- Real checkout (`core.hooksPath=.githooks`): the change's own commits passed the hook; a runtime-generated fake key was refused with the value redacted and absent from the output; with the binary renamed the hook warned and passed; restored, it scanned again.
- No device or browser run applies: the change is release tooling, a git hook and documentation, with no Android, web-extension or shared-logic behaviour.
- Environment-bound checks: `bridgeBuild` failed on this host for missing `glib-2.0` and `bridgePackage` did not run; Windows/macOS installer producers and signing/notarization are NOT RUN on Linux. The web-extension `package-output` test is time-bounded and flaked twice under host load; it passed unchanged on re-run and at the base commit.
- Checks: `./gradlew scriptsTest` 888 tests, docs 300 tests plus the doc checks, installer `bridgeInstallerScriptsTest` 581, `protocolCheck`, rust, native-host plus 25 contract tests, supply-chain plus 116 node tests, and web-extension all green as recorded in the PR; the exceptions are the environment-bound items above.
- Planning: the final review found the spec contradicted the line-scoped entries. The spec, design D5/D6, tasks 4.x and plan Task 2/5 now define path-only vs line-scoped entries, the test-data boundary, a specific line regex, four triage categories a–d, and the negative control. The amended files were re-reviewed by Opus (spec/design pass 3; tasks/plan pass 2, four rounds with the user's authorization to exceed the cap).

---

## Next step

Run `/openspec-verify-change` for `secret-scanning-gitleaks`, then finalize in the same turn.
