# Secret Scanning (gitleaks pre-commit) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refuse staged secrets locally with a pinned, digest-verified gitleaks run from `.githooks/pre-commit`, and document push protection plus the hook as the enforcement model.

**Architecture:** The git-cliff install task in `build.gradle.kts` becomes one shared pinned-tool task used by `installGitCliff` and the new `installGitleaks`. A POSIX `sh` hook resolves the binary, scans staged changes with the repository's `.gitleaks.toml`, and blocks only on a dedicated leak exit code. Offline stdlib tests drive the hook in temporary repositories through `scriptsTest`.

**Tech Stack:** Gradle Kotlin DSL, POSIX `sh`, gitleaks v8.30.1, Python 3.8+ stdlib `unittest`.

**Spec:** `openspec/changes/secret-scanning-gitleaks/specs/secret-scanning/spec.md`, with `design.md` (D1–D9) and `tasks.md` in the same directory.

## Global Constraints

- gitleaks version `8.30.1`; platform keys `linux_x64`, `linux_arm64`, `darwin_arm64`, `darwin_x64`, `windows_x64`.
- Install path `build/release-tools/gitleaks/gitleaks` (`gitleaks.exe` on Windows); git-cliff stays at `build/release-tools/git-cliff/`.
- Digest verified before any extraction; mismatch deletes the archive and fails naming both digests.
- `installGitCliff` keeps its name, description, output path, pins and behaviour.
- Hook: `VELES_GITLEAKS` when set and non-empty, no fallback; leak exit code `3` (unless 3.1 finds it taken); only that code blocks.
- No secret value is ever printed, recorded or committed; full-history results are counts only.
- Tests: stdlib only, no network, no sleep, no wall clock (`.agents/rules/repo-no-time-based-tests.md`).
- No write to the issue; the PR carries a non-closing `Refs` reference.
- Never touch signing keys (`.agents/rules/repo-signing-keys.md`).
- Commit subjects are Conventional Commits (`scripts/commitlint.py`).

## Review Focus

- Repository path with spaces: the hook must quote `$root`, `$VELES_GITLEAKS` and the config path. Pinned in Task 3 by running the hook in a temp repo whose path contains a space.
- Commit from a subdirectory: the hook must scan with the working directory at `$root`. Pinned in Task 3 by staging from a subdirectory in the clean and refused cases.
- First commit in a fresh repository (no `HEAD`): the staged scan must still work. The temp repos in Task 3 start empty, so the refused and clean cases cover it.
- Staged deletion only: an empty staged diff must pass, not error. Pinned in Task 3 (`test_staged_deletion_passes`).
- Windows checkout with CRLF in `.gitleaks.toml`: the config must still load. Covered by review only: CI is Ubuntu, and `.gitattributes` decides the line endings.

---

### Task 0: Ask the owner first

Covers tasks.md 1.1 (the ask). Task 4 Step 1 depends on the answer.

- [ ] **Step 1: Send the request now.** Before Task 1, ask the owner to check Settings → Code security, then enable secret scanning and push protection or decline. Work continues while the owner decides. Task 5 Step 1 records the answer.

### Task 1: Shared pinned-tool install task and `installGitleaks`

Covers tasks.md 2.1–2.8; design D2, D3.

**Files:**
- Modify: `build.gradle.kts:167-296` (release-tooling section, `InstallGitCliffTask`, `installGitCliff`, `scriptsTest`)
- Modify: `scripts/release-tools.toml`

**Interfaces:**
- Produces: task `installGitleaks` → `build/release-tools/gitleaks/gitleaks[.exe]`; `scriptsTest` sets env `VELES_GITLEAKS` to that path and `dependsOn(installGitCliff, installGitleaks)`.
- Produces: `abstract class InstallPinnedToolTask` with `@Input` properties `toolName`, `version`, `downloadUrl`, `archiveName`, `archiveEntry`, `binaryName`, `sha256`, `versionArgs: ListProperty<String>`, `expectedVersionOutput`; `@OutputDirectory installDir`; `@Internal downloadDir`. A zip is chosen when `archiveName` ends in `.zip`.
- Produces: `hostPlatform(): Pair<String, String>?` (os in `linux|mac|windows`, arch in `x86_64|aarch64`), and per-tool maps `gitCliffTarget` / `gitleaksTarget` derived from it.

- [ ] **Step 1: Add the pins.** In `scripts/release-tools.toml` add `gitleaks = "8.30.1"` and five `gitleaks-<key> = "<sha256>"` lines. Take each digest from `gh api repos/gitleaks/gitleaks/releases/tags/v8.30.1 --jq '.assets[] | "\(.name) \(.digest)"'` and confirm it matches the same line in `gitleaks_8.30.1_checksums.txt`. Expected Linux x64 digest: `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`. Reword the header comment to cover both tools and their cross-check sources.
- [ ] **Step 2: Generalise the task.** Rename `InstallGitCliffTask` to `InstallPinnedToolTask` with the inputs above. Keep the existing body's order: download, then digest check (delete the archive and `error("SHA-256 mismatch for <archive>: expected <x>, got <y>")` on failure), then clear `installDir`, then copy only `archiveEntry` flattened to its name, then set the executable bit. Then run `binary + versionArgs` and check that the trimmed stdout equals `expectedVersionOutput`; otherwise fail with `"Expected '<expectedVersionOutput>' from <toolName> at <binary>, got: '<reported>'"`.
- [ ] **Step 3: Re-register `installGitCliff`.** Pass the existing values: the URL `https://github.com/orhun/git-cliff/releases/download/v<ver>/git-cliff-<ver>-<target>.<tar.gz|zip>`, the entry `git-cliff-<ver>/git-cliff[.exe]`, `versionArgs = ["--version"]` and the expected output `git-cliff <ver>`. Keep the same description and output directory.
- [ ] **Step 4: Inspect the gitleaks archive.** Download `gitleaks_8.30.1_linux_x64.tar.gz` into the scratchpad and run `tar tzf` on it. Then run the extracted binary's `version`. Record the entry path (expected `gitleaks`) and the output (expected `8.30.1`) in `apply.md`.
- [ ] **Step 5: Register `installGitleaks`.** Use the URL `https://github.com/gitleaks/gitleaks/releases/download/v<ver>/gitleaks_<ver>_<key>.<tar.gz|zip>`. Set the entry and expected output to the values recorded in Step 4, and `versionArgs = ["version"]`. Set `installDir` to `releaseToolsDir/gitleaks` and `downloadDir` to `releaseToolsDir/downloads`. An unsupported host makes `requireNotNull` fail with `"No pinned gitleaks archive for <os.name>/<os.arch>"`.
- [ ] **Step 6: Wire `scriptsTest`.** Add `dependsOn(installGitleaks)` and the `VELES_GITLEAKS` environment variable. Update the section comment and the description so both name both tools.
- [ ] **Step 7: Verify the installs.** Run `./gradlew installGitCliff installGitleaks`. Expected: BUILD SUCCESSFUL, and `build/release-tools/gitleaks/gitleaks version` prints the pinned version.
- [ ] **Step 8: Check a digest mismatch.**
  1. Run `rm -rf build/release-tools/gitleaks`.
  2. Change the `gitleaks-linux_x64` digest to 64 zeros.
  3. Run `./gradlew installGitleaks`. Expected: FAIL with "SHA-256 mismatch", no `build/release-tools/gitleaks/gitleaks`, and no `gitleaks_8.30.1_*` archive in `downloads/` (the git-cliff archive there is expected).
  4. Restore the digest. `git diff scripts/release-tools.toml` must show only the Step 1 additions.
  5. Record the result in `apply.md`.
- [ ] **Step 9: Check a version mismatch.**
  1. Temporarily set the gitleaks expected output to `0.0.0`.
  2. Run `./gradlew installGitleaks`. Expected: FAIL with "Expected '0.0.0' from gitleaks … got: '8.30.1'".
  3. Restore it and confirm `git diff build.gradle.kts` holds only the intended edits.
  4. Run `./gradlew installGitleaks` again and confirm it passes.
  5. Record the result in `apply.md`.
- [ ] **Step 10: Run the contracts.** Run `node --test verify/test/workflow-contracts.test.mjs`. Expected: all pass.
- [ ] **Step 11: Commit.** `git add build.gradle.kts scripts/release-tools.toml`, then `git commit -m "build(tooling): add pinned gitleaks install via shared pinned-tool task"`.

### Task 2: History scan, triage and `.gitleaks.toml`

Covers tasks.md 4.1–4.4; design D5, D6. Runs before the hook tests, because they copy this config.

**Files:**
- Create: `.gitleaks.toml`
- Create (only if needed): `.gitleaksignore`
- Modify: `.github/components.json` (`nodes.tooling.paths`: add `.gitleaks.toml`, `.gitleaksignore`)

**Interfaces:**
- Produces: `.gitleaks.toml` at the repository root, loaded by the hook with `--config`.

- [ ] **Step 1: Scan the full history.** Run `build/release-tools/gitleaks/gitleaks git --redact --no-banner --report-format json --report-path <scratchpad>/history.json .`. Count the findings and group them by `File` and `RuleID`. Never print or copy `Secret` or `Match`; they are redacted, and they stay out of every recorded output.
- [ ] **Step 2: Triage every finding.** Sort each one into (a) tracked test data, (b) a history-only path (`git ls-files --error-unmatch <file>` fails), (c) a real secret, or (d) a tracked non-test-data false positive (prose, source logic or harness code). **On any (c): stop. Report the path and rule to the owner for rotation and do nothing else in this task.**
- [ ] **Step 3: Write `.gitleaks.toml`.** Start it with `[extend]` and `useDefault = true`. Add one path-only `[[allowlists]]` block per group (a) path, each with a `description` that says what the data is and why it is not secret, and `paths = ['''<narrowest regex>''']`. For each group (d) file, add an entry with `condition = "AND"`, `regexTarget = "line"`, the exact path and a literal or anchored regex for the benign token (never a wildcard), so a flagged secret on any other line is still detected. Use the syntax the pinned README documents, and record that the pinned binary accepts `condition` and `regexTarget`. Then run the negative control for each (d) file: add a runtime-generated flagged line to that file itself, stage it, run the hook (or `gitleaks git --staged --redact --config .gitleaks.toml`), confirm the report names the new line; restore with `git restore --staged --worktree <file>` and confirm `git status --porcelain <file>` is empty; record the result. If triage finds no (d) entry, record the control as not applicable.
- [ ] **Step 4: Write `.gitleaksignore`, if needed.** For each (b) finding, add its `Fingerprint` line, under a `#` comment that gives the reason.
- [ ] **Step 5: Re-run the scan.** Run the Step 1 command with `--config .gitleaks.toml`. Expected: `no leaks found`, exit 0. Record the counts in `apply.md`: the total and categories (a), (b), (c) and (d).
- [ ] **Step 6: Register the files.** Add both files to `nodes.tooling.paths` in `.github/components.json`. Run `python3 -I scripts/components.py check`. Expected: exit 0.
- [ ] **Step 7: Commit.** `git add .gitleaks.toml .github/components.json` (and `.gitleaksignore` if it exists), then `git commit -m "build(tooling): add gitleaks config with justified test-data allowlist"`.

### Task 3: Pre-commit hook and its tests

Covers tasks.md 3.1–3.4 and 5.1–5.7; design D4, D7.

**Files:**
- Create: `.githooks/pre-commit` (mode 100755)
- Create: `scripts/tests/test_pre_commit_hook.py`

**Interfaces:**
- Consumes: `VELES_GITLEAKS` set by `scriptsTest` (Task 1), and `.gitleaks.toml` (Task 2).
- Produces: hook exit codes: 0 pass or warn, 1 block. Stderr messages contain the literal strings `./gradlew installGitleaks` (missing binary), `--no-verify` (block), and `scan did not run` plus the exit code (tool error).

- [ ] **Step 1: Confirm the CLI.** Run `gitleaks git --help` and read the README "Exit Codes" section at `v8.30.1`. Confirm the flags `--staged`, `--redact`, `--verbose`, `--no-banner`, `--exit-code` and `--config`, the exit codes 1 and 126, and which stream carries the report and the summary. Record these in `apply.md`. If 3 is already taken, pick another free code and use it everywhere this plan says 3.
- [ ] **Step 2: Write the failing tests.** Create `scripts/tests/test_pre_commit_hook.py`, modelled on `test_commit_msg_hook.py`.
  - A helper `make_repo()` creates a temp directory whose name contains a space, runs `git init` in it, sets the local `user.name`/`user.email`, and copies `.gitleaks.toml` in.
  - A helper `run_hook(repo, gitleaks, cwd=None)` runs `[sh, HOOK]` with `cwd` (default `repo`). Its env is `HOME=<tmp>`, `GIT_CONFIG_NOSYSTEM=1`, `PATH=<dir with git symlink>`, plus `VELES_GITLEAKS` when given.
  - A module constant `FAKE_KEY = "AKIA" + "".join(<fixed pieces giving 16 random-looking base32 chars>)` is deterministic and computed once; it must not end in `EXAMPLE`.
  - The tests:
    - `test_refused`: stage `config.ini` holding `aws_access_key_id = <FAKE_KEY>` from a subdirectory `cwd`. Assert returncode ≠ 0, `aws-access-token` and `config.ini` and `--no-verify` in stdout+stderr, and `FAKE_KEY` in neither stream.
    - `test_clean_passes`: stage `hello.txt`; returncode 0.
    - `test_staged_deletion_passes`: commit a file with `--no-verify`, `git rm` it, run the hook; returncode 0.
    - `test_missing_binary_warns`: set `VELES_GITLEAKS=<tmp>/nope`, place an executable stub `#!/bin/sh\nexit 3` at `<repo>/build/release-tools/gitleaks/gitleaks` and assert its executable bit. Assert returncode 0 and `./gradlew installGitleaks` in stderr. Then point `VELES_GITLEAKS` at a non-executable file; same assertions (this sub-check skips on Windows).
    - `test_tool_error_warns`: a stub `#!/bin/sh\nexit 2`; assert returncode 0, and `scan did not run` and `2` in stderr. Skip on Windows.
    - Skips: the class skips without `sh`/`git`. `test_refused`, `test_clean_passes` and `test_staged_deletion_passes` also skip unless `VELES_GITLEAKS` names an executable file.
- [ ] **Step 3: Run the tests to see them fail.** Run `VELES_GITLEAKS=$PWD/build/release-tools/gitleaks/gitleaks python3 -I -m unittest discover -s scripts/tests -p test_pre_commit_hook.py -v`. Expected: FAIL or ERROR, because the hook does not exist.
- [ ] **Step 4: Write `.githooks/pre-commit`.**
  - **Resolve the binary.** Use `root=$(git rev-parse --show-toplevel) || exit 1`. If `${VELES_GITLEAKS:-}` is non-empty, use it. Otherwise use `$root/build/release-tools/gitleaks/gitleaks`, or the same path with `.exe` when that one is the executable. If it is not `[ -x ]`, warn `pre-commit: gitleaks not installed; secret scan skipped. Run ./gradlew installGitleaks` and `exit 0`.
  - **Scan.** `cd "$root"`. Run the binary with `git --staged --redact --verbose --no-banner --exit-code 3`, plus `--config "$root/.gitleaks.toml"` when that file exists. Send its stdout to stderr with `1>&2` (stderr already goes there). Capture `rc=$?`.
  - **Act on the exit code.**
    - `3`: print the block message, which says to remove the value, or to allowlist a false positive in `.gitleaks.toml` with a reason, and that `git commit --no-verify` bypasses deliberately. Then `exit 1`.
    - `0`: `exit 0`.
    - Anything else: warn `pre-commit: gitleaks exited $rc; secret scan did not run` and `exit 0`.
  - Use only builtins besides `git` and the binary, and no `set -e` (the scanner's non-zero exit must reach `rc=$?`). Run `chmod +x` and `git update-index --chmod=+x`.
- [ ] **Step 5: Run the tests to see them pass.** Use the same command as Step 3. Expected: all tests in `test_pre_commit_hook` pass, with none skipped on Linux.
- [ ] **Step 6: Run the full suite.** Run `./gradlew scriptsTest`. Expected: BUILD SUCCESSFUL, every suite green.
- [ ] **Step 7: Commit.** `git add .githooks/pre-commit scripts/tests/test_pre_commit_hook.py`, then `git commit -m "build(tooling): add gitleaks pre-commit hook with offline tests"`. This commit also exercises the hook, because `core.hooksPath=.githooks`.

### Task 4: Docs and rule

Covers tasks.md 6.1–6.4; design D8, spec "Recorded enforcement model" and "Contributor guidance and component ownership".

**Files:**
- Modify: `docs/ci.md` ("Git hooks", ~line 233)
- Modify: `CONTRIBUTING.md` ("Git Hooks", ~line 50)
- Modify: `scripts/AGENTS.md` (overview line 4 and the Build & Test block)
- Modify: `.agents/rules/repo-signing-keys.md`

- [ ] **Step 1: Update `docs/ci.md` "Git hooks".** Cover:
  - the `pre-commit` hook and `./gradlew installGitleaks`;
  - the four outcomes: block, pass, warn when missing, warn on a tool error;
  - the enforcement model: push protection plus the hook, no CI scan job. Word it to match the owner's answer from Task 5 Step 1; if that answer is still pending, write the enabled case and revisit it in Task 5;
  - the follow-ups: CODEOWNERS for `.gitleaks.toml`/`.gitleaksignore`, and an always-run CI scan job;
  - that `scriptsTest` now also downloads gitleaks.
- [ ] **Step 2: Update `CONTRIBUTING.md` "Git Hooks".** Say to run `./gradlew installGitleaks` once per clone or worktree, and that until then the pre-commit scan only warns.
- [ ] **Step 3: Update `scripts/AGENTS.md`.** Name the pinned gitleaks next to git-cliff, and add `./gradlew installGitleaks      # pinned, SHA-256-verified gitleaks -> build/release-tools/`. Confirm with `wc -l` that the file stays at 40 lines or fewer.
- [ ] **Step 4: Update `.agents/rules/repo-signing-keys.md`.** Add one line: commits are scanned by `.githooks/pre-commit` (gitleaks, `./gradlew installGitleaks`) and pushes by GitHub push protection; a finding on a key is a stop, never an allowlist entry.
- [ ] **Step 5: Run the docs checks.** Run `python3 -I scripts/components.py changed --base "$(git merge-base origin/master HEAD)"`, then the `docs` node checks: `python3 docs/checks/check_offline.py --root .`, `python3 docs/checks/check_agent_files.py --root .`, `python3 docs/checks/check_site.py --root .` and `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`. Expected: all pass.
- [ ] **Step 6: Commit.** `git add docs/ci.md CONTRIBUTING.md scripts/AGENTS.md .agents/rules/repo-signing-keys.md`, then `git commit -m "docs(tooling): document the gitleaks pre-commit scan and enforcement model"`.

### Task 5: Owner answer, real-checkout check, pre-push checks and draft PR

Covers tasks.md 1.1–1.2, 7.1–7.2 and 8.1–8.2; design D1, D9.

**Files:**
- Modify: `openspec/changes/secret-scanning-gitleaks/apply.md` (notes only)

- [ ] **Step 1: Record the owner's answer.** Record the answer to the Task 0 request in `apply.md`. If it differs from the wording Task 4 wrote, re-edit `docs/ci.md` and re-run the Task 4 Step 5 checks. If they decline, stop and ask the user to decide again on a CI scan job. A job the user chooses is first added to this change's planning files and re-reviewed.
- [ ] **Step 2: Run the real-checkout check.** This worktree has `core.hooksPath=.githooks`.
  1. The Task 3 and Task 4 commits passed the hook. Note that in `apply.md`.
  2. Write `scratch.txt` with a key generated by `python3 -c` from pieces. Run `git add scratch.txt && git commit -m "test: scratch"`. Expected: refused, and the value is not shown. If it commits instead, that is a failure: run `git reset --soft HEAD~1` and fix the hook before going on.
  3. Run `git restore --staged scratch.txt && rm scratch.txt`.
  4. Rename `build/release-tools/gitleaks/gitleaks` and run `env -u VELES_GITLEAKS sh .githooks/pre-commit` directly (`git commit --dry-run` never runs hooks). Expected: the warning.
  5. Restore the binary and run `sh .githooks/pre-commit` again. Expected: it scans.
  6. Record everything in `apply.md`. No device or browser run applies (the change has no Android, extension or shared-logic behaviour).
- [ ] **Step 3: Run the pre-push checks.** Run `python3 -I scripts/components.py changed --base "$(git merge-base origin/master HEAD)"`, then every selected node's checks. That includes at least `./gradlew scriptsTest`, `node --test verify/test/workflow-contracts.test.mjs`, `python3 -I scripts/components.py check` and the docs checks. Expected: all pass.
- [ ] **Step 4: Open the draft PR.** The title is `build(tooling): add gitleaks pre-commit secret scanning`. The body records:
  - the owner's answer (and the user's decision, if the owner declined);
  - the history-scan counts by category a–d;
  - any allowlisted path beyond `protocol/` conformance fixtures, `rust/veles-crypto/tests/protocol_fixtures.rs` and `native-bridge/feasibility/evidence/**`, and for each line-scoped entry the file and the entry's `description` (not the token text);
  - the digest- and version-mismatch results and the line-scope negative control;
  - the real-checkout run;
  - `Refs <owner>/<repo>#<n>`.

  Write nothing to the issue.
- [ ] **Step 5: Keep it in draft.** The PR stays in draft until Step 1 has an answer and, on a decline, the user has decided.
