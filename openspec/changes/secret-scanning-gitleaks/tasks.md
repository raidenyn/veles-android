## 1. Owner action: GitHub secret scanning and push protection

- [ ] 1.1 Ask the owner to check Settings → Code security (or `gh api repos/<owner>/<repo> --jq .security_and_analysis` with an admin token) and to enable secret scanning and push protection or decline; record the answer for the PR description (design D9).
- [ ] 1.2 If the owner declines, stop before the PR leaves draft and ask the user to decide again on a CI scan job; a chosen job is first added to this change's plan and its planning files re-reviewed (spec "Enforcement model follows the owner's answer").

## 2. Pinned gitleaks install

- [ ] 2.1 Generalise `InstallGitCliffTask` in `build.gradle.kts` into one `InstallPinnedToolTask` with the inputs listed in design D2, and a single OS/arch resolver with a per-tool platform-key map; keep `installGitCliff`'s name, description, output path and behaviour. A wrong reported version fails naming the expected and reported versions; an unsupported platform fails naming the OS and architecture and installs nothing.
- [ ] 2.2 Add `gitleaks = "8.30.1"` and the five `gitleaks-<os>_<arch>` digests to `scripts/release-tools.toml`, cross-checked against the release asset digest API and `gitleaks_8.30.1_checksums.txt`; reword the header comment to cover both tools and each cross-check source (design D3).
- [ ] 2.3 Register `installGitleaks` (installs to `build/release-tools/gitleaks/`); confirm and record in the task notes the archive entry path and the `gitleaks version` output of the pinned release, and set the expected version output from it.
- [ ] 2.4 Update the release-tooling section comment and the `scriptsTest` description in `build.gradle.kts` to name both tools; make `scriptsTest` depend on `installGitleaks` and set `VELES_GITLEAKS` to the installed binary.
- [ ] 2.5 Run `./gradlew installGitCliff installGitleaks` on Linux x64 and confirm both binaries report their pinned versions.
- [ ] 2.6 Digest-mismatch check: delete `build/release-tools/gitleaks/` first (the shared task keeps today's behaviour of clearing the install directory only after the digest passes), temporarily change the host platform's gitleaks digest, run `./gradlew installGitleaks`, confirm it fails naming both digests and leaves no binary or archive, then restore the digest, confirm `git diff scripts/release-tools.toml` shows no leftover edit, and record the result (a deliberate one-off check, design Risks).
- [ ] 2.7 Wrong-version check: temporarily change the expected gitleaks version output, run `./gradlew installGitleaks`, confirm it fails naming both versions, restore and confirm `git diff build.gradle.kts` shows no leftover edit, re-run `./gradlew installGitleaks` so a verified binary is installed before group 3, and record. The unsupported-platform branch is review-checked only: no supported host can emulate an unsupported one cheaply.
- [ ] 2.8 Run `node --test verify/test/workflow-contracts.test.mjs` and confirm it passes unchanged.

## 3. Pre-commit hook

- [ ] 3.1 Read `gitleaks git --help` (and `gitleaks --help`) of the pinned binary; confirm the staged-scan subcommand, `--redact`, `--verbose`, `--no-banner`, `--exit-code` and `--config`, which stream carries the per-finding report and the summary, and, from the pinned version's documented exit codes (README "Exit Codes" at the `v8.30.1` tag), that gitleaks itself never exits 3 (its own codes are 1 and 126); record the values, and pick another leak code if 3 is taken.
- [ ] 3.2 Write `.githooks/pre-commit` (POSIX `sh`, executable bit tracked, only shell builtins besides `git` and the binary) with binary resolution per design D4: root from `git rev-parse --show-toplevel`; `VELES_GITLEAKS` when set and non-empty, with no fallback, else the build path with and without `.exe`; warn naming `./gradlew installGitleaks` and exit 0 when absent or not executable.
- [ ] 3.3 Add the scan: working directory `$root`, staged changes, `--redact --verbose --no-banner --exit-code 3`, `--config "$root/.gitleaks.toml"` when it exists, both streams to stderr.
- [ ] 3.4 Add the outcomes: exit 3 blocks with the remove/allowlist/`--no-verify` bypass message and exits 1; exit 0 passes; any other non-zero code warns naming the code and that the scan did not run, and exits 0.

## 4. Scan configuration and history triage

- [ ] 4.1 Run the pinned gitleaks over the full history with `--redact` and the default rules; triage every finding as tracked test data, history-only false positive or real secret. On any real secret: stop, report the path and rule (never the value) to the owner for rotation, and do not continue this group.
- [ ] 4.2 Write `.gitleaks.toml` with `[extend] useDefault = true` and one `[[allowlists]]` entry per flagged tracked test-data path, each the narrowest path regex with a description of what the data is and why it is not a secret (design D5); use the allowlist syntax the pinned version documents.
- [ ] 4.3 If history-only false positives remain, add their fingerprints to `.gitleaksignore`, one per line, with a comment line naming the reason.
- [ ] 4.4 Re-run the full-history scan with the repository config and confirm it reports no finding; record the counts (total, per category) for the PR, never any value.

## 5. Tests

- [ ] 5.1 Write `scripts/tests/test_pre_commit_hook.py` (stdlib `unittest`, no network, no sleep, no clock) per design D7: temporary git repository with local identity and a copy of `.gitleaks.toml`; the project's hook run by absolute path; `HOME` set to the temporary directory, `GIT_CONFIG_NOSYSTEM=1`, `PATH` holding only a `git` symlink.
- [ ] 5.2 Refused case: stage `aws_access_key_id = <id>` with the ID assembled at runtime from pieces, a random-looking base32 body not ending in `EXAMPLE`; assert non-zero exit, the rule ID and the file name and `--no-verify` in the combined output, and the ID in neither stream. Confirm the expected `aws-access-token` rule fires.
- [ ] 5.3 Clean case: stage ordinary text; assert exit 0.
- [ ] 5.4 Missing case: `VELES_GITLEAKS` at a non-existent path and an executable stub (bit set and checked) exiting 3 at `<tmp repo>/build/release-tools/gitleaks/gitleaks`; assert exit 0 and `./gradlew installGitleaks` in stderr. A second assertion points `VELES_GITLEAKS` at an existing file without the executable bit and expects the same warning and exit 0 (skipped on Windows).
- [ ] 5.5 Tool-error case: `VELES_GITLEAKS` at an executable `#!/bin/sh` stub that exits 2; assert exit 0 and a warning that the scan did not run naming exit code `2`; skip on Windows.
- [ ] 5.6 Per-test skips: every case needs `sh` and `git`; refused and clean also need `VELES_GITLEAKS` naming an executable binary.
- [ ] 5.7 Run `./gradlew scriptsTest` and confirm every suite passes, including all four hook cases.

## 6. Docs, rule and component map

- [ ] 6.1 `docs/ci.md` "Git hooks": the `pre-commit` hook, `./gradlew installGitleaks`, the four outcomes, the enforcement model matching the owner's answer, the follow-ups (CODEOWNERS for the scanner config; an always-run CI scan job when the chosen model has none), and that `scriptsTest` now also downloads gitleaks.
- [ ] 6.2 `CONTRIBUTING.md` "Git Hooks": run `./gradlew installGitleaks` once per clone or worktree; the pre-commit scan only warns until it is installed.
- [ ] 6.3 `scripts/AGENTS.md`: name the pinned gitleaks next to git-cliff in the overview and add `./gradlew installGitleaks` to the Build & Test block, staying within the 40-line cap.
- [ ] 6.4 `.agents/rules/repo-signing-keys.md`: add one line pointing at the pre-commit scanner and push protection; keep the rule short.
- [ ] 6.5 `.github/components.json`: add `.gitleaks.toml` and `.gitleaksignore` to the `tooling` node's paths; run `python3 -I scripts/components.py check`.

## 7. Real-checkout check and pre-push checks

- [ ] 7.1 In this worktree, with `core.hooksPath=.githooks`: make the change's own next task commit and confirm the hook passes it (the commit stays), attempt to commit a runtime-generated fake key in a scratch file (refused, value not shown, then unstage and delete it), and rename the binary away to confirm the warning, then restore it and confirm the hook scans again; record what ran. No device or browser run applies: the change has no Android, extension or shared-logic behaviour.
- [ ] 7.2 Run the checks of every node `python3 -I scripts/components.py changed --base "$(git merge-base origin/master HEAD)"` selects (at least `tooling` and `docs`), including `./gradlew scriptsTest`, `node --test verify/test/workflow-contracts.test.mjs` and the docs agent-file check, and confirm they pass.

## 8. Pull request

- [ ] 8.1 Open the PR as a draft with a Conventional Commits title and a body that records: the owner's push-protection answer (and the user's renewed CI decision if declined), the full-history scan counts (no values), any allowlisted path beyond the three expected ones (`protocol/` conformance fixtures, `rust/veles-crypto/tests/protocol_fixtures.rs`, `native-bridge/feasibility/evidence/**`; design D5), the digest- and version-mismatch checks, what the real-checkout run in 7.1 did, and a non-closing `Refs` reference to the issue; nothing is written to the issue.
- [ ] 8.2 Keep the PR in draft until 1.1 has an answer and, on a decline, 1.2 is settled.
