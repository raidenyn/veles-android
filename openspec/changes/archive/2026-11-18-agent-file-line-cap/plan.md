# Agent-File 40-Line Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make 40 lines a hard, machine-checked cap on every `AGENTS.md`, move the displaced prose into docs pages, and keep every checker, spec and CI contract intact.

**Architecture:** Five new docs pages hold the narrative that leaves the guidance files; existing docs pages absorb the release, CI, supply-chain and platform detail. The nine over-cap `AGENTS.md` files are rewritten as short contracts. `check_agent_files.py` gains one 40-line rule, the skill-list source moves to `docs/openspec-skills.md`, the plugin-range guard and CODEOWNERS list cover the new instruction pages, and the tests get root/nested boundary cases.

**Tech Stack:** Python 3.8 stdlib (checker and tests), Markdown docs, GitHub CODEOWNERS, OpenSpec 2.

**Spec:** `openspec/changes/agent-file-line-cap/` — `brainstorm.md`, `proposal.md`, `design.md`, `specs/*/spec.md`, `tasks.md`.

## Global Constraints

- Every tracked `AGENTS.md` (root and nested) MUST be at most 40 lines; 41 is a hard error. Count `len(text.splitlines())` on the CRLF-normalized text.
- Error text: `has <n> lines; the AGENTS.md cap is 40 lines`.
- Do not edit `openspec/specs/` or any generated `openspec-*` skill; the archive applies the deltas.
- Keep these checker contracts: the node map, the eight `repo-*` listings, the `opencode-rules` mention in the root and `CONTRIBUTING.md`, the hook pointer block, the `global-overrides` entries, the rule frontmatter/size checks and the symlink/skill parity.
- Keep these strings in `native-bridge/host/AGENTS.md` **and** `docs/reproducible-builds.md`: `bridgeHostToolchainCheck`, `bridgeHostFormat`, `bridgeHostLint`, `bridgeHostTest`, `bridgeHostBuild`, `native-host-windows`, `native-host-macos`, `native-host.cdx.json`.
- Keep the headings `## Signing identities (Android)` and `## Signing identity and TCC (macOS)`; other files cite them.
- Commit type: `docs(agents): ...` for this guidance-only change; `test(agent-files): ...` for the test-only commit. Run every command from the repository root.
- The 40-line policy is a hard cap: a change that adds a node, a `repo-*` rule, a hooked rule or a root line must compress the root or move a fact to docs.
- Intermediate commits after Tasks 6–7 may leave the agent-file check red, because the skill list has moved but the checker still reads `docs/AGENTS.md`. Task 9 makes it green. Nothing is pushed before Task 11 passes.

## Review Focus

- A fact removed from an `AGENTS.md` is not present in its destination → the `traceability.md` table is the guard; check each row after the move.
- The root grows past 40 after `pointers --write` regenerates the block, or a new `repo-*` path wraps onto an extra line → rerun `wc -l` after every root edit.
- `docs/AGENTS.md` loses one of its spec-required statements (the plugin sentence, the skills link, the root-cap statement) → the checker does not enforce them; review them by hand.
- The clean fixture root loses its headroom and unrelated tests gain a second finding → keep it near 35 lines and rerun the whole suite.
- `**/AGENTS.md` and the new CODEOWNERS lines interact under GitHub's last-match rule → run `check_codeowners` through the suite.
- A missing `docs/openspec-skills.md` would otherwise disable skill parity silently → Task 9 adds an explicit missing-file error and its negative test.

---

### Task 1: Android architecture page

**Files:**
- Create: `docs/android.md`
- Source: `android/AGENTS.md` (sections Architecture Overview through Adding a New Bank Handler)

**Interfaces:**
- Produces: the anchor file `docs/android.md`, linked from `android/AGENTS.md` in Task 6.

- [ ] **Step 1: Write the page from the 137-line source.** Move, verbatim where possible, these sections: intro and independent-build note, Architecture Overview/Core Flow, Bank Handler Configuration, Test Screen, Sensitive notifications (Android 15+), Package Structure, Navigation, Testing Approach and Adding a New Bank Handler. Add the fixture build command `cd android && ./gradlew --init-script config/android_keys/fixture-signing.gradle assembleDebug assembleDebugAndroidTest`, the backup rule, the `otp23-fixture.keystore`/`fixture-signing.gradle` names and a one-line pointer to `docs/releasing.md` for `androidGitVersion`.
- [ ] **Step 2: Check every moved section has a heading.**

Run: `grep -n '^#' docs/android.md`
Expected: headings for each section above; no `TBD`.

- [ ] **Step 3: Commit**

```bash
git add docs/android.md
git commit -m "docs(agents): move Android architecture out of AGENTS.md"
```

### Task 2: Agent-guidance page and the hook link

**Files:**
- Create: `docs/agent-guidance.md`
- Modify: `docs/hook-delivered-rules.md` (add the root-cap link)

**Interfaces:**
- Produces: `docs/agent-guidance.md`, linked from `docs/AGENTS.md`, `CONTRIBUTING.md` and the new-pages fixtures.

- [ ] **Step 1: Move the checker and adding-a-rule-or-node sections.** From `docs/AGENTS.md` take "The agent-file check" and "Adding a rule or a node". The Gate B command stays in `docs/AGENTS.md`; move only its explanatory paragraph, if any. Include the check run commands and exit semantics, the rule/node checklists, the 40-line cap procedure and the statement that the check is structural.
- [ ] **Step 2: Add the policy links.** Link `docs/AGENTS.md`'s root-cap section for the hooked-rule pointer growth and `docs/opencode-rules.md` for the untrusted-checkout warning. Add a link to the root-cap section in `docs/hook-delivered-rules.md` near its pointer-block discussion.
- [ ] **Step 3: Verify no stale ownership claim remains.**

Run: `grep -n "docs/AGENTS.md" docs/agent-guidance.md`
Expected: only links, no sentence that says the detail lives there.

- [ ] **Step 4: Commit**

```bash
git add docs/agent-guidance.md docs/hook-delivered-rules.md
git commit -m "docs(agents): add the agent-guidance page and hook-cap link"
```

### Task 3: OpenSpec workflow and the skill list

**Files:**
- Create: `docs/openspec-workflow.md`
- Create: `docs/openspec-skills.md`
- Source: `docs/AGENTS.md` (OpenSpec version/update, the superspec fork, the workflow list, the skill list)

**Interfaces:**
- Produces: `docs/openspec-skills.md` with the exact `## OpenSpec skills` heading and one backticked skill per bullet; Task 9 points `DOCUMENTED_SKILLS` at it.

- [ ] **Step 1: Write `docs/openspec-workflow.md`.** Move the CLI range *link* (the range itself stays in `CONTRIBUTING.md`), the Linux/macOS update procedure, the idempotence proof, the generated-skill rule and the six fork divergences. State that `(schema version v5)` lives in `docs/AGENTS.md` and name it explicitly instead of "this file". Keep the `/opsx:` grep command.
- [ ] **Step 2: Write `docs/openspec-skills.md`.** Put the workflow list (`propose, explore, new, continue, apply, ff, sync, archive, bulk-archive, verify, update`) and then the exact heading `## OpenSpec skills` followed by the 11 bullets from the current `docs/AGENTS.md`.
- [ ] **Step 3: Verify the parser shape.**

Run: `grep -c '^## OpenSpec skills$\|^- `openspec-' docs/openspec-skills.md`
Expected: 12.

- [ ] **Step 4: Commit**

```bash
git add docs/openspec-workflow.md docs/openspec-skills.md
git commit -m "docs(openspec): move the update procedure and skill list to docs"
```

### Task 4: OpenCode plugin page

**Files:**
- Create: `docs/opencode-rules.md`
- Source: `docs/AGENTS.md` (OpenCode and the opencode-rules plugin)

**Interfaces:**
- Produces: `docs/opencode-rules.md`, linked from the root, `docs/AGENTS.md`, `CONTRIBUTING.md` and `docs/agent-guidance.md`.

- [ ] **Step 1: Move the plugin guidance.** Install/config (point at `opencode.json`, never repeat the range), `opencode plugin list`/`update`, the two-instance caveat, the repeated-read caveat, the plugin-unavailable state and the tool-neutral untrusted-checkout warning: `.claude/settings.json` and `opencode.json` can make a tool install and run code, and the hooks run a script on every shell command.
- [ ] **Step 2: Check no version range leaked.**

Run: `grep -nE 'opencode-rules@[^ ]+' docs/opencode-rules.md`
Expected: no output (the range is only in `opencode.json`).

- [ ] **Step 3: Commit**

```bash
git add docs/opencode-rules.md
git commit -m "docs(agents): add the OpenCode plugin page"
```

### Task 5: Existing docs absorb the release, CI and supply-chain detail

**Files:**
- Modify: `docs/ci.md` (git hooks, workflow ownership facts, host CI artifacts)
- Modify: `docs/releasing.md` (commit types, versions/tags, `VELES_*_VERSION`, `androidGitVersion`)
- Modify: `docs/reproducible-builds.md` (pins, 0/1/2 contract, policy checks, six SBOMs, host target directory/artifacts)
- Modify: `docs/desktop-ble-poc.md` (only missing macOS identity/TCC history)

**Interfaces:**
- Produces: the single homes for the facts removed from `scripts/AGENTS.md`, `verify/AGENTS.md`, `native-bridge/host/AGENTS.md` and `macos/AGENTS.md`.

- [ ] **Step 1: Add the missing `docs/ci.md` facts.** The `installGitHooks` behaviour (`core.hooksPath=.githooks`, `:app:preBuild`, only when unset, skipped under `CI=true`), any "one build per invocation" fact not in the tree/table, and the host CI artifact names with the note that they are check artifacts, never release assets.
- [ ] **Step 2: Add the missing `docs/releasing.md` facts.** The full commit-type/bump explanation (including that guidance-only changes are `docs`), the three tags, the `VELES_EXTENSION_VERSION`/`VELES_INSTALLER_VERSION`/`VELES_HOST_VERSION` injection and the `androidGitVersion` `prefix = "android-v"`, `codeFormat = "MMNNPP"`.
- [ ] **Step 3: Add the missing `docs/reproducible-builds.md` facts.** The exact Node 26.8.1/npm 11.19.0 pin, the `verify/package.json` (6.0.1 / 5.0.1) and `verify/cargo-tools.toml` (0.5.9 / 0.20.2) pins, the 0/1/2 contract, the install-script and remote-code policy details and the six SBOMs. Keep the host task/artifact/SBOM tokens.
- [ ] **Step 4: Add only missing macOS history to `docs/desktop-ble-poc.md`.** Do not copy the canonical identity rules, the exact bundle identifier, the no-fallback rule or the publish-only signing rule out of the node.
- [ ] **Step 5: Verify the host build-contract tokens survived.**

Run: `grep -cE 'bridgeHostToolchainCheck|bridgeHostFormat|bridgeHostLint|bridgeHostTest|bridgeHostBuild|native-host-windows|native-host-macos|native-host.cdx.json' docs/reproducible-builds.md`
Expected: 8 (each token at least once).

- [ ] **Step 6: Commit**

```bash
git add docs/ci.md docs/releasing.md docs/reproducible-builds.md docs/desktop-ble-poc.md
git commit -m "docs(agents): absorb release, CI and supply-chain detail"
```

### Task 6: Rewrite the root, Android and docs guidance

**Files:**
- Modify: `AGENTS.md`
- Modify: `android/AGENTS.md`
- Modify: `docs/AGENTS.md`

**Interfaces:**
- Consumes: `docs/android.md`, `docs/agent-guidance.md`, `docs/openspec-workflow.md`, `docs/openspec-skills.md`, `docs/opencode-rules.md`.
- Produces: the 39-line root, the 33-line Android file and the ~27-line docs file.

- [ ] **Step 1: Rewrite the root `AGENTS.md` to at most 40 lines.** Keep the title, a 2-line purpose, the exact root notice, `## Node map` with the 12 one-line bullets, `## Rules` with the eight paths two per line, one line that OpenCode needs the pinned `opencode-rules` plugin pointing at `docs/opencode-rules.md`, one line for the `/init` and parent-`CLAUDE.md` warning, and the `## Hook-delivered rules` block. Link `CONTRIBUTING.md` and `docs/agent-guidance.md`.
- [ ] **Step 2: Rewrite `android/AGENTS.md` to at most 40 lines.** Keep the purpose and `docs/android.md` link, the `## Signing identities (Android)` block with the keystore/init names, the Build & Test code block, the NDK/rustup and `protocolJvmTest` lines, the `docs/releasing.md` version link and the rules list.
- [ ] **Step 3: Rewrite `docs/AGENTS.md` to at most 40 lines (target about 27).** Keep the purpose and links, the one-sentence `opencode-rules` requirement with the page link, the `docs/openspec-skills.md` link, the check and Gate B commands, the exit semantics, the short hook section that states the root cap and one pointer line per rule, `(schema version v5)` and the rules/notice. Remove the skill and workflow lists.
- [ ] **Step 4: Regenerate the pointer block and count.**

```bash
python3 -I scripts/agent_rules.py pointers --write
git ls-files '*AGENTS.md' | xargs wc -l
```
Expected: root 39–40, `android/AGENTS.md` ≤40, `docs/AGENTS.md` ≤40; no other file changed.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md android/AGENTS.md docs/AGENTS.md
git commit -m "docs(agents): shrink the root, Android and docs guidance to 40 lines"
```

### Task 7: Rewrite the remaining guidance files

**Files:**
- Modify: `scripts/AGENTS.md`, `verify/AGENTS.md`, `native-bridge/AGENTS.md`, `native-bridge/host/AGENTS.md`, `native-bridge/installer/macos/AGENTS.md`, `rust/AGENTS.md`

**Interfaces:**
- Consumes: `docs/ci.md`, `docs/releasing.md`, `docs/reproducible-builds.md`, `docs/otp-23-requirements-matrix.md`, `docs/otp-25-installers.md`, `docs/desktop-ble-poc.md`.

- [ ] **Step 1: Rewrite `scripts/AGENTS.md` and `verify/AGENTS.md`.** Keep the compact command blocks, the project-flow exit codes, the guidance-only-`docs` sentence and the 0/1/2 contract; link `docs/ci.md`, `docs/releasing.md` and `docs/reproducible-builds.md`.
- [ ] **Step 2: Confirm the Tauri rationale has a home.** Check that `docs/otp-23-requirements-matrix.md` D-01 still records the Tauri decision; if it does, `native-bridge/AGENTS.md` keeps only the one-line warning and the link. Rewrite `native-bridge/AGENTS.md`, `native-bridge/host/AGENTS.md` and `native-bridge/installer/macos/AGENTS.md`. Keep the commands, pins, the eight host tokens, the canonical identity statement and both cited headings; compress the prose.
- [ ] **Step 3: Rewrite `rust/AGENTS.md`.** Keep the commands, the output notes and the required statement that Android CI also runs for its changes with the `android-lint-before-push.md` pointer.
- [ ] **Step 4: Count every file and commit.**

```bash
git ls-files '*AGENTS.md' | xargs wc -l
```
Expected: every file ≤40.

```bash
git add scripts/AGENTS.md verify/AGENTS.md native-bridge/AGENTS.md native-bridge/host/AGENTS.md native-bridge/installer/macos/AGENTS.md rust/AGENTS.md
git commit -m "docs(agents): shrink the remaining node guidance to 40 lines"
```

### Task 8: Add the 40-line cap to the checker (TDD)

**Files:**
- Modify: `docs/checks/check_agent_files.py` (constants, helper, `_check_root`, `_check_nested_files`)
- Modify: `docs/checks/test_check_agent_files.py` (`test_constants_are_exact`, delete `test_root_line_boundaries`, new boundary test, fixture root slimming)

**Interfaces:**
- Produces: `AGENTS_LINE_MAX = 40` and `_check_line_cap(path, text) -> list[Finding]`.

- [ ] **Step 1: Write the failing tests and slim the fixture root.** In `test_constants_are_exact`, replace the two root-cap assertions with `self.assertEqual(getattr(check_module, "AGENTS_LINE_MAX", None), 40)`. Delete `test_root_line_boundaries`. Add:

```python
def test_agents_line_boundaries(self):
    for path, target, expected in (
        ("AGENTS.md", 40, 0), ("AGENTS.md", 41, 1),
        ("rust/AGENTS.md", 40, 0), ("rust/AGENTS.md", 41, 1),
    ):
        with self.subTest(path=path, lines=target):
            files = clean_files()
            files[path] = (
                root_text(target_lines=target)
                if path == "AGENTS.md"
                else nested_text("rust", target_lines=target)
            )
            findings = check_repo(self.repo(files))
            cap = [f for f in findings if f.path == path
                   and "the AGENTS.md cap is 40 lines" in f.message]
            self.assertEqual(len(cap), expected, findings)
            if expected:
                self.assertEqual(cap[0].severity, "error")
```

In `root_text`, emit the repo rules two per line so the base is about 35 lines:

```python
for index in range(0, len(EXPECTED_REPO_RULES), 2):
    lines.append(", ".join(
        "`.agents/rules/%s`" % name
        for name in EXPECTED_REPO_RULES[index:index + 2]
    ))
```

- [ ] **Step 2: Run the boundary test and confirm it fails.**

Run: `python3 -m unittest discover -s docs/checks -p 'test_*.py' -k test_agents_line_boundaries -v`
Expected: FAIL (no `AGENTS_LINE_MAX`; `nested_text` has no `target_lines`).

- [ ] **Step 3: Add the constant and the helper.** Delete `ROOT_LINE_WARN`/`ROOT_LINE_MAX` and insert:

```python
AGENTS_LINE_MAX = 40


def _check_line_cap(path, text):
    """One error when a guidance file is over the 40-line cap; a missing file is the caller's finding."""
    if text is None:
        return []
    lines = len(text.splitlines())
    if lines > AGENTS_LINE_MAX:
        return [
            Finding(
                "error",
                path,
                "has %d lines; the AGENTS.md cap is %d lines" % (lines, AGENTS_LINE_MAX),
            )
        ]
    return []
```

- [ ] **Step 4: Call it from both paths.** In `_check_root`, after the notice check, replace the 60/120 block with `findings.extend(_check_line_cap(ROOT_AGENTS, text))`. In `_check_nested_files`, after the `## Rules` check, add `findings.extend(_check_line_cap(path, text))`.
- [ ] **Step 5: Add `target_lines` to `nested_text`.** Add the keyword parameter and, after the body, `if target_lines is not None: lines.extend(["Filler line for the line cap."] * (target_lines - len(lines)))`. Assert the base is not longer.
- [ ] **Step 6: Run the full checker suite.**

Run: `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`
Expected: PASS in the fixtures; the real tree stays red until Task 9 moves `DOCUMENTED_SKILLS`.

- [ ] **Step 7: Commit**

```bash
git add docs/checks/check_agent_files.py docs/checks/test_check_agent_files.py
git commit -m "docs(agents): enforce the 40-line cap on every AGENTS.md"
```

### Task 9: Move the skill-list source, extend the guards, and update the fixtures

**Files:**
- Modify: `docs/checks/check_agent_files.py` (`DOCUMENTED_SKILLS`, `check_links` missing-file error, `PLUGIN_RANGE_DOCS`, `parse_documented_skills` docstring, `check_links` docstring/messages, `CODEOWNER_PATTERNS`, `CODEOWNER_SAMPLE_PATHS`)
- Modify: `.github/CODEOWNERS`
- Modify: `docs/checks/test_check_agent_files.py` (`clean_files`, `nested_text`, `EXPECTED_CODEOWNER_PATTERNS`, skill tests, plugin-range test, missing-skills test)

**Interfaces:**
- Produces: `DOCUMENTED_SKILLS = "docs/openspec-skills.md"`; a missing skills page is an error; `PLUGIN_RANGE_DOCS` covers the three docs; CODEOWNERS covers the three new instruction pages.

- [ ] **Step 1: Change the source and the wording.**

```python
DOCUMENTED_SKILLS = "docs/openspec-skills.md"
PLUGIN_RANGE_DOCS = (
    ROOT_AGENTS,
    "CONTRIBUTING.md",
    "docs/AGENTS.md",
    "docs/opencode-rules.md",
    "docs/agent-guidance.md",
)
```
Update `parse_documented_skills`'s docstring, the `check_links` docstring ("…against `docs/AGENTS.md`") and the two `check_links` messages from `docs/AGENTS.md` to `docs/openspec-skills.md`.

- [ ] **Step 2: Fail closed when the skills page is missing.** In `check_links`, replace `if documented_text is not None:` with a branch that appends `Finding("error", DOCUMENTED_SKILLS, "is missing; it holds the expected OpenSpec skill list")` when the text is None, and runs the existing comparison otherwise.
- [ ] **Step 3: Extend the CODEOWNERS constants and the file.**

```python
# CODEOWNER_PATTERNS
"/docs/agent-guidance.md",
"/docs/openspec-workflow.md",
"/docs/opencode-rules.md",

# CODEOWNER_SAMPLE_PATHS
("/docs/agent-guidance.md", "docs/agent-guidance.md"),
("/docs/openspec-workflow.md", "docs/openspec-workflow.md"),
("/docs/opencode-rules.md", "docs/opencode-rules.md"),
```
Add one `@raidenyn` line per path to `.github/CODEOWNERS`, and add the three paths to the test constant `EXPECTED_CODEOWNER_PATTERNS` so `codeowners_text()` emits them.

- [ ] **Step 4: Move the skill fixture and update the docs fixtures.** Remove the `skills` parameter and the skill block from `nested_text`. In `clean_files()`, write `docs/openspec-skills.md` with `"\n".join(skill_section(skills)) + "\n"`, and write short `docs/agent-guidance.md`, `docs/openspec-workflow.md`, `docs/opencode-rules.md` and `docs/android.md` files. Change the docs fixture call to `nested_text(node)`. Update the test comment at the skill-list fixture to name `docs/openspec-skills.md`.
- [ ] **Step 5: Update the assertions.** `test_documented_skill_section_parses` parses the new page text; the two `is listed under "## OpenSpec skills" in docs/AGENTS.md` assertions become `docs/openspec-skills.md`. Add:

```python
def test_missing_documented_skill_list_fails(self):
    files = clean_files()
    del files["docs/openspec-skills.md"]
    findings = check_repo(self.repo(files))
    self.assert_finding(
        findings, "error", "docs/openspec-skills.md", "expected OpenSpec skill list"
    )
```

- [ ] **Step 6: Add the three patterns to the missing-coverage loop and extend the plugin-range test.** In `test_codeowners_missing_entry_fails`, include the three new patterns. In `test_plugin_range_not_repeated_in_docs`, iterate `("AGENTS.md", "CONTRIBUTING.md", "docs/AGENTS.md", "docs/opencode-rules.md", "docs/agent-guidance.md")`.
- [ ] **Step 7: Run the full suite and expect green.**

Run: `python3 docs/checks/check_agent_files.py --root . && python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`
Expected: the real-tree checker prints nothing and exits 0, and the suite passes.

- [ ] **Step 8: Commit**

```bash
git add docs/checks/check_agent_files.py docs/checks/test_check_agent_files.py .github/CODEOWNERS
git commit -m "docs(agents): read skills from docs and protect the new instruction pages"
```

### Task 10: Cross-references

**Files:**
- Modify: `CONTRIBUTING.md`
- Verify: `AGENTS.md`, `scripts/AGENTS.md`, `docs/ci.md`, `docs/otp-11-debug-controls.md`, `docs/otp-23-lifecycle-scenarios.md`, `native-bridge/host/scripts/macos-test-bundle.sh`, `native-bridge/installer/macos/AGENTS.md`

- [ ] **Step 1: Repoint `CONTRIBUTING.md`.** The three `docs/AGENTS.md` references become `docs/agent-guidance.md`, `docs/opencode-rules.md` and `docs/openspec-workflow.md`; add the sentence that node files link their detail in `docs/`.
- [ ] **Step 2: Run the sweep.**

```bash
git grep -n "docs/AGENTS.md\|Signing identities (Android)\|Signing identity and TCC (macOS)\|The agent-file check\|Adding a rule or a node" -- \
  AGENTS.md CONTRIBUTING.md docs scripts native-bridge verify
```
Expected: only intentional links remain; `docs/otp-11-debug-controls.md` and `docs/otp-23-lifecycle-scenarios.md` still cite the two headings, and the root OpenCode line points at `docs/opencode-rules.md`.

- [ ] **Step 3: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs(agents): update the cross-references to the new pages"
```

### Task 11: Verification and traceability

**Files:**
- Create: `openspec/changes/agent-file-line-cap/traceability.md`

- [ ] **Step 1: Run the local checks.**

```bash
python3 docs/checks/check_agent_files.py --root .
python3 -m unittest discover -s docs/checks -p 'test_*.py' -v
python3 -I scripts/components.py check
python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py' -v
openspec validate agent-file-line-cap
```
Expected: the checker prints nothing and exits 0; the suites pass; `components.py check` passes; the host contract passes; OpenSpec validates.

- [ ] **Step 2: Run the model-name, schema and generated-skill checks.**

```bash
git grep -nE "GPT-6\.2-Sol|Opus-5\.5" -- .agents/rules openspec/schemas openspec/config.yaml openspec/specs docs/superspec-workflow.md docs/AGENTS.md docs/agent-guidance.md docs/openspec-workflow.md docs/opencode-rules.md
git grep -n "/opsx:" -- openspec/schemas
git diff --name-only "$(git merge-base origin/master HEAD)" -- .agents/skills .claude/skills
```
Expected: the first prints only the roster, the second nothing, the third nothing.

- [ ] **Step 3: Run the node checks selected by the change.**

```bash
python3 -I scripts/components.py changed --base "$(git merge-base origin/master HEAD)"
```
Run the checks listed in the `AGENTS.md` of every selected node (`docs`, `installer`, `native-host`, `supply-chain`, and anything else it reports).

- [ ] **Step 4: Write `traceability.md`.** Copy the `git ls-files '*AGENTS.md' | xargs wc -l` output, then a row per section of the nine rewritten files with its destination, and every deliberate drop with its reason. Include the final line counts and the D-01 confirmation from Task 7.
- [ ] **Step 5: Commit**

```bash
git add openspec/changes/agent-file-line-cap/traceability.md
git commit -m "docs(agents): record the migration traceability"
```

## Self-Review

- **Spec coverage:** every requirement in the four deltas maps to Task 8 (cap/check), Task 9 (skill source, missing-file error, plugin guard, CODEOWNERS, fixtures), Tasks 1–7 (content), or Tasks 10–11 (cross-references and verification). The root/node/platform/existing-content/OpenCode/Fail-open text is exercised by Tasks 1–7 and 9.
- **Type consistency:** `_check_line_cap(path, text)` is the only helper name; `AGENTS_LINE_MAX` is the only constant; `DOCUMENTED_SKILLS` points at `docs/openspec-skills.md` everywhere.
- **Review Focus:** each line has a task or step: traceability (11.4), root growth (6.4, 7.4), docs statements (6.3), fixture headroom (8.1), CODEOWNERS (9.3, 9.6), missing skills page (9.2, 9.5), CRLF (`_read_text` in 8.3).
- **Proportion:** the plan gives one code block for the cap, one for the boundary test and short fixture expressions; the docs work is specified by source section and destination, not transcribed.
