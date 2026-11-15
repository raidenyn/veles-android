# Superspec Review Gate, Tracker Intake and Closeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Apply tracks progress in `tasks.md`: when a plan task finishes, tick the `tasks.md` items it lists under "Implements".

**Goal:** Add four small always-on rules, one procedure doc, `config.yaml` hooks and schema-fork edits so a superspec session picks up the next board task, has every planning file reviewed by independent subagents, stops before apply, and automates the PR closeout; and enforce the new size cap and CODEOWNERS coverage in the agent-file check.

**Architecture:** Policy is four `repo-*` rules (roster, review gate, tracker intake, closeout), each tiny; detail lives in `docs/superspec-workflow.md`; thin hooks live in `openspec/config.yaml` and the repo-owned `superspec` schema fork. The only code is the stdlib-Python agent-file check and its unittest suite; everything else is Markdown and YAML.

**Tech Stack:** Markdown rules and docs, YAML (`openspec/config.yaml`, `schema.yaml`), Python 3 standard library (`unittest`), `gh` and `openspec` CLIs.

**Spec:** this change directory: `proposal.md`, `design.md` (decisions D1 to D13), `tasks.md`, and `specs/superspec-review-gate/`, `specs/superspec-tracker-intake/`, `specs/superspec-closeout/`, `specs/agent-guidance-layout/`. Executors read both the spec files and this plan.

## Global Constraints

- Rule files: `.agents/rules/repo-review-models.md`, `repo-superspec-review-gate.md`, `repo-tracker-intake.md`, `repo-superspec-closeout.md`; frontmatter is exactly `trigger: always_on` (no `globs`, no `paths`); each at most **4,096 bytes**, target about 2 KB; LF line endings.
- Cap numbers (copy exactly): 5 rounds per reviewer per review pass; confirmation and revalidation passes 2 rounds; cosmetic-edit confirmation 1 round; 5 CI fix attempts per distinct failure; node rules at most 24,000 bytes.
- Only `repo-review-models.md` names models or reviewer labels. The checked scope is the other `.agents/rules/`, `openspec/schemas/`, `openspec/config.yaml`, `openspec/specs/`, `docs/superspec-workflow.md` and `docs/AGENTS.md`; `openspec/changes/**` and historical documents elsewhere under `docs/` are exempt.
- No rule, schema file, `config.yaml` or `docs/superspec-workflow.md` names a concrete project, project number, issue number, RFC path or field id. Concrete board identifiers appear only in `spikes.md`.
- Never edit `.agents/skills/` or `.claude/skills/`, and never edit a `superpowers:*` skill.
- Commit headers are `type(scope): subject`, at most 100 characters, type one of `feat fix perf refactor docs test build ci chore style revert`, scope lowercase. Commit only the files a task lists.
- The hash command (normative, used wherever a hash is computed):
  `python3 -I -c "import hashlib,re,sys;t=open(sys.argv[1],'rb').read().decode('utf-8').replace('\r\n','\n').replace('\r','\n');t=re.sub(r'(?m)^([ \t]*[-*] )\[[xX ]\]',r'\1[ ]',t);print(hashlib.sha256(t.encode('utf-8')).hexdigest())" FILE`
- Test command for the check: `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v` from the repository root; check command: `python3 docs/checks/check_agent_files.py --root .`.
- List each new rule path in the root `AGENTS.md` `## Rules` sentence, one path per line (wrap only between paths; the check's reference regex cannot match a path split across lines).
- Every commit in the rules section must leave the agent-file check passing (a `repo-*` rule is listed in the root `AGENTS.md` in the commit that creates it).

## Review Focus

Inputs and conditions the specs imply that no task's own happy path exercises; each has a test or check in the task that owns it.

1. A node rule between 4,097 and 24,000 bytes must not be flagged (the cap is for `repo-*` only). Task 2.
2. A `repo-*` rule over its cap with broken frontmatter reports only the size finding, never also a parsing finding. Task 2.
3. A broader CODEOWNERS line such as `/openspec/` does not satisfy the required literal pattern `/openspec/schemas/`. Task 3.
4. A later ownerless wildcard line in CODEOWNERS removes ownership for the three new sample paths as well. Task 3.
5. A CRLF copy and a checkbox-ticked copy of a file hash the same as the LF, unticked original, while a one-word edit changes the hash. Task 4.

---

## Task 1: Read-only spikes

**Implements:** `tasks.md` 1.1, 1.2, 1.3, 1.4.

**Files:**
- Create: `openspec/changes/extend-superspec-workflow/spikes.md`

**Interfaces:**
- Produces: `spikes.md`, with one section per spike, each holding the exact command, the relevant result and a one-line conclusion. Tasks 4 (procedure doc) cite these results by section name; concrete board identifiers stay here.

- [ ] **Step 1: Create `spikes.md` with four empty sections** titled "1.1 Board queries", "1.2 Pull request and checks", "1.3 Hash command", "1.4 Validate tolerance", each with the subheadings Command, Result, Conclusion.
- [ ] **Step 2: Board queries (1.1).** The controller (not the task subagent) obtains the board URL from the user before dispatching this task (never infer it) and confirms `gh auth status` shows the `read:project` scope; the task receives the URL in its brief. Keep the concrete identifiers in `spikes.md` only; if `user(login:$o)` returns null, retry with `organization(login:$o)` and record which one worked. Using `gh api graphql -f query=... -f o=<owner> -F n=<number>`, run: (a) `user(login:$o){projectV2(number:$n){items(first:100){pageInfo{hasNextPage endCursor} nodes{content{... on Issue{number state}} fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name}}}}}}` and page with `after:$cursor` until `hasNextPage` is false; (b) the same project's `views(first:20){nodes{number name layout filter sortByFields(first:5){nodes{direction field{... on ProjectV2FieldCommon{name}}}} groupByFields(first:5){nodes{... on ProjectV2FieldCommon{name}}}}}`; (c) an issue's `projectItems(first:10){nodes{project{number title}}}`; (d) how to read each item's value for a view's single sort field and for a simple single-field filter (`fieldValueByName` with the matching value type), with pagination; (e) the Status field's metadata and option ids, `projectV2(number:$n){id field(name:"Status"){... on ProjectV2SingleSelectField{id name options{id name}}}}`, which the write command needs; (f) which view configurations cannot be recovered (several sort keys, grouping, a compound filter) and so use the ask-the-user fallback. Record the queries that worked and a conclusion for each.
- [ ] **Step 3: Pull request and checks (1.2).** Record the working form of: `gh api repos/{owner}/{repo}/rules/branches/{base}` and `gh api repos/{owner}/{repo}/branches/{base}/protection/required_status_checks` (and the error without permission); `gh pr view <n> --json state,headRefOid,statusCheckRollup,reviewDecision,reviews,comments` (lifecycle, head commit, checks, review decision, reviews and conversation comments) **and** the inline code-review comments and threads (`gh api repos/{owner}/{repo}/pulls/<n>/comments --paginate` and the GraphQL `reviewThreads` for resolved state); record the complete monitoring read as the exact set of calls and what each supplies (one polling cycle, not one command); `gh api repos/{owner}/{repo}/collaborators/{login}/permission`. Record **without running** the write forms, marked "unexecuted": setting a project item's Status (`gh project item-edit` with `--id --project-id --field-id --single-select-option-id`, or the GraphQL mutation), `gh issue close <n> --comment <text>`, and the exact message `gh` prints when the `project` scope is missing plus the fix command (`gh auth refresh -s project`).
- [ ] **Step 4: Hash command (1.3).** In the scratchpad directory create a baseline LF file containing the three checkbox forms unticked (`- [ ] a`, `* [ ] b`, `  - [ ] c`) and derive three variants by changing only one thing each: CRLF line endings; the boxes ticked (`- [x] a`, `* [X] b`, `  - [x] c`); one word changed. Record the generation commands in `spikes.md` so Task 4 can recreate the files. Run the hash command from Global Constraints on each. Expected: the baseline, CRLF and ticked variants print the same value; the changed-word variant differs. Also run it on this change's `brainstorm.md`, `proposal.md` and `design.md` and compare with the last `Approved content` lines in `reviews/`. Record the commands and values.
- [ ] **Step 5: Validate tolerance (1.4).** Create a throwaway `openspec/changes/extend-superspec-workflow/tracker.md` containing a one-line placeholder, run `openspec validate --all`, record whether any error mentions `tracker.md` or `reviews/`, then delete the throwaway file.
- [ ] **Step 6: Commit.**

```bash
git add openspec/changes/extend-superspec-workflow/spikes.md
git commit -m "docs(openspec): record read-only spike evidence for extend-superspec-workflow"
```

## Task 2: Cap `repo-*` rules at 4,096 bytes and compact the size tests

**Implements:** `tasks.md` 2.1, 2.2, 2.3 and the size bullet of 2.6.

**Files:**
- Modify: `docs/checks/check_agent_files.py` (constants near `RULE_SIZE_MAX`; `check_rules`)
- Modify: `docs/checks/test_check_agent_files.py` (imports; `sized_rule`; replace three tests)
- Modify: `docs/AGENTS.md` ("Adding a rule or a node" size bullet)

**Interfaces:**
- Produces: `REPO_RULE_SIZE_MAX = 4096` in `check_agent_files.py` (independently pinned by test-local constants through `getattr`, not imported); test helper `sized_rule(size, pattern=None) -> str` (a valid rule of exactly `size` bytes; a node-scoped rule when `pattern` is given, else a `repo-*` rule).
- Consumes: existing `with_rule`, `clean_files`, `check_repo`, `assert_finding`, `EXPECTED_SCOPES`, `node_rule_text`, `repo_rule_text`.

- [ ] **Step 1: Inventory.** Run `grep -n "sized_rule(\|RULE_SIZE_MAX" docs/checks/test_check_agent_files.py`. The tests to replace are `test_rule_size_boundaries`, `test_rule_over_24000_bytes_fails` and `test_oversized_rule_skips_content_parsing`; confirm nothing else builds a `repo-*` rule over 4,096 bytes.
- [ ] **Step 2: Write the compacted failing tests.** Change `sized_rule` to `sized_rule(size, pattern=None)` using `node_rule_text(pattern)` as the base when `pattern` is given (otherwise `repo_rule_text()`), keeping the 79-character filler logic. Define `REPO_RULE_SIZE_MAX = 4096` at module level next to the test file's own `RULE_SIZE_MAX = 24000` (the test file keeps its own copies so a wrong constant cannot satisfy its own fixture; do **not** import the new constant), and put `SIZE_CAP_CASES` at module level there too. Extend `test_constants_are_exact` with `import check_agent_files as check_module` and `self.assertEqual(getattr(check_module, "RULE_SIZE_MAX", None), RULE_SIZE_MAX)` and the same for `REPO_RULE_SIZE_MAX` (the `getattr` default makes the red stage a failing assertion, not an ImportError). Replace the three old tests with:

```python
SIZE_CAP_CASES = (  # (case, rule name, scope, size, expected size findings)
    ("repo at its cap", "repo-cap.md", "repo", REPO_RULE_SIZE_MAX, 0),
    ("repo over its cap", "repo-cap.md", "repo", REPO_RULE_SIZE_MAX + 1, 1),
    ("repo far over", "repo-cap.md", "repo", 30000, 1),
    ("repo over the node cap", "repo-cap.md", "repo", RULE_SIZE_MAX + 1, 1),
    ("node over the repo cap", "android-cap.md", "android", REPO_RULE_SIZE_MAX + 1, 0),
    ("node at its cap", "android-cap.md", "android", RULE_SIZE_MAX, 0),
    ("node over its cap", "android-cap.md", "android", RULE_SIZE_MAX + 1, 1),
)

def test_rule_size_caps(self):
    for case, name, scope, size, expected in SIZE_CAP_CASES:
        with self.subTest(case=case):
            files = with_rule(clean_files(), name, sized_rule(size, EXPECTED_SCOPES[scope]), scope)
            findings = check_repo(self.repo(files))
            if expected == 0:
                self.assertEqual(findings, [])
            else:
                self.assert_finding(findings, "error", ".agents/rules/" + name, "size cap")
                self.assertEqual(len(findings), 1, findings)

def test_oversized_rule_skips_content_parsing(self):
    for name, scope, cap in (("repo-huge.md", "repo", REPO_RULE_SIZE_MAX),
                             ("android-huge.md", "android", RULE_SIZE_MAX)):
        with self.subTest(name=name):
            text = "not a frontmatter block\n" + "y" * cap + "\n"
            files = with_rule(clean_files(), name, text, scope)
            findings = check_repo(self.repo(files))
            self.assert_finding(findings, "error", ".agents/rules/" + name, "size cap")
            self.assertEqual(len(findings), 1, findings)
```

- [ ] **Step 3: Run the new tests to verify they fail.** Run `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`. Expected: `test_constants_are_exact` fails (the module has no `REPO_RULE_SIZE_MAX` yet) and the `repo` over-cap rows of `test_rule_size_caps` and `test_oversized_rule_skips_content_parsing` fail with no size finding; the other rows pass.
- [ ] **Step 4: Implement the cap in `check_agent_files.py`.** Add `REPO_RULE_SIZE_MAX = 4096` beside `RULE_SIZE_MAX`. In `check_rules`, choose `cap = REPO_RULE_SIZE_MAX if scope == "repo" else RULE_SIZE_MAX` and use it for the size comparison and in the existing message (`"is %d bytes; the rule size cap is %d bytes"`); keep the `continue` that skips content parsing for an oversized rule.
- [ ] **Step 5: Run the full suite.** Expected: all tests pass. Record in the commit message body which old test each case replaces: `test_rule_size_boundaries` is the cap and cap+1 rows, `test_rule_over_24000_bytes_fails` is the "repo far over" row, `test_oversized_rule_skips_content_parsing` is the two-row loop.
- [ ] **Step 6: Update `docs/AGENTS.md`.** In the size bullet under "Adding a rule or a node" say a rule is at most 24,000 bytes and a `repo-*` rule at most 4,096 bytes (the always-on context budget).
- [ ] **Step 7: Run the check on the real tree.** `python3 docs/checks/check_agent_files.py --root .` Expected: no errors (existing `repo-*` rules are about 0.3 KB).
- [ ] **Step 8: Commit.**

```bash
git add docs/checks/check_agent_files.py docs/checks/test_check_agent_files.py docs/AGENTS.md
git commit -m "feat(docs): cap repo-wide agent rules at 4,096 bytes and compact the size tests"
```

## Task 3: Require CODEOWNERS coverage for the workflow instruction files

**Implements:** `tasks.md` 2.4, 2.5 and the unit-suite run of 2.6.

**Files:**
- Modify: `docs/checks/check_agent_files.py` (`CODEOWNER_PATTERNS`, `CODEOWNER_SAMPLE_PATHS`)
- Modify: `docs/checks/test_check_agent_files.py` (`EXPECTED_CODEOWNER_PATTERNS`, three tests, imports)
- Modify: `.github/CODEOWNERS`

**Interfaces:**
- Consumes: Task 2 left the suite green.
- Produces: required patterns `/docs/superspec-workflow.md`, `/openspec/config.yaml`, `/openspec/schemas/` with sample paths `docs/superspec-workflow.md`, `openspec/config.yaml`, `openspec/schemas/superspec/schema.yaml` (exactly one per new pattern).

- [ ] **Step 1: Write the failing tests.** In the test file: append the three patterns to `EXPECTED_CODEOWNER_PATTERNS` and to the pattern loop of `test_codeowners_missing_entry_fails` (each missing pattern gives one finding naming it); import `CODEOWNER_SAMPLE_PATHS`; change `test_codeowners_ownerless_wildcard_removes_all_ownership` to assert `len(findings) == len(CODEOWNER_SAMPLE_PATHS)` (the check emits one finding per sample path, not per required pattern; the count will be 8; the clean fixture has no `.mcp.json`, so that pattern's sample is never added) and, pinning Review Focus 4, that a finding names each of the three new sample paths; add `test_codeowners_broader_line_does_not_count` (patterns minus `/openspec/schemas/` plus `/openspec/` yields exactly one finding naming `/openspec/schemas/`).
- [ ] **Step 2: Run to verify failure.** Expected: the new missing-pattern cases fail because the check does not require the new patterns yet, and the wildcard test fails on the missing findings for the three new sample paths (its count assertion still passes while there are five samples).
- [ ] **Step 3: Implement.** Add the three patterns to `CODEOWNER_PATTERNS` and the three sample-path tuples (`(pattern, path)`) to `CODEOWNER_SAMPLE_PATHS`.
- [ ] **Step 4: Run the check on the real tree.** Expected: errors only about the three missing CODEOWNERS patterns, proving the real file is now the gap.
- [ ] **Step 5: Update `.github/CODEOWNERS`.** Add the three entries with the same owner token as the existing agent-guidance lines (aligned in the same column style) and reword the comment block at the top to cover "instruction files that agents follow" in general.
- [ ] **Step 6: Run the check and the complete suite.** Expected: both pass.
- [ ] **Step 7: Commit (one commit: patterns, tests and the CODEOWNERS entries together).**

```bash
git add docs/checks/check_agent_files.py docs/checks/test_check_agent_files.py .github/CODEOWNERS
git commit -m "feat(docs): require code-owner coverage for the workflow instruction files"
```

## Task 4: The procedure document

**Implements:** `tasks.md` 3.1, 3.2, 3.3, 3.4, 3.5.

**Files:**
- Create: `docs/superspec-workflow.md`

**Interfaces:**
- Consumes: `spikes.md` results (Task 1).
- Produces: exactly these level-two headings, in this order, which the rules cite by name: `## Reviewer brief`, `## Review passes and caps`, `## Revision binding`, `## Escalation`, `## Next task`, `## tracker.md`, `## Closeout`, `## Monitoring`.

- [ ] **Step 1: Create the file with a one-paragraph intro and the eight headings.** The intro says the file is read on demand by the four rules and names no model and no project.
- [ ] **Step 2: Write *Reviewer brief*, *Escalation*, *Review passes and caps*.** Reviewer brief: the inputs (artifact, change directory, upstream artifacts), the three questions (satisfies its inputs, what a builder would still need to know, what is wrong or risky), the critical/high/medium/low grading, the approve or changes-required verdict, read-only. Escalation: the six-part plain-language template (what the artifact is trying to do, the unresolved finding in ordinary words, what the reviewer argues, what the agent argues, the options with a recommendation, the decision needed) and "then wait". Passes and caps: the decision table from `design.md` D5 as a Markdown table, plus the pass definition, rebuttal rule and the confirmation, cosmetic and revalidation rules from `specs/superspec-review-gate/spec.md`, and that the agent may escalate a single-reviewer choice to a two-reviewer preset for a complicated artifact, stating why, and never downgrades the user's choice.
- [ ] **Step 3: Write *Revision binding*.** The record location and header fields (artifact, reviewer, the session's reviewers in order, cap, upstream hashes), the body format (one section per pass, one table per round with severity, finding and disposition, and the literal approval line `Approved content: <artifact> sha256 <hash>`, which starts the line and may be followed by a note after the hash), `custom` and `custom-N` labels with the custom model named in the header, **the audit checklist** (for every planning file an approved, current record from every reviewer listed in the header's preset; own and upstream hashes recomputed and matching; the full upstream set; no open critical or high finding; each failure is FAIL), the `closeout-docs` artifact id with upstream `none` and hashes keyed by repo-relative path, the upstream definition (every earlier planning file), the one hash per spec file rule, the normalization grammar, the **hash command copied verbatim from Global Constraints**, and the rule that a recorded hash is never recomputed or rewritten to silence an audit.
- [ ] **Step 3a: Verify the command from the document (Review Focus 5).** Extract the command from the file, recreate the four scratch files from the generation commands in `spikes.md` and run it on each. Expected: baseline, CRLF and ticked equal; changed-word different.
- [ ] **Step 4: Write *Next task* and *tracker.md*.** The order, completion (OR of Status Done and issue closed; drafts and PRs by Status only), last-completed-item disagreement rule, successor, boundaries, simple-filter narrowing, resume rule, issue-only intake, the reconcile procedure with its report-only cases, the rule that substantive findings from the issue's links go into the brainstorm, the `tracker.md` fields (repository, project, issue, source documents, creation date, implementation PR), the status transitions with their guards (In Progress after `tracker.md`; In Review only after an open PR is confirmed and recorded; stop and ask on a missing Status option; the missing-scope message) and the write command forms from `spikes.md`, written generically with `<owner>`, `<number>` placeholders.
- [ ] **Step 5: Write *Closeout* and *Monitoring*.** The ordered checklist from `specs/superspec-closeout/spec.md`: required-check discovery with per-head-commit evidence (commands from `spikes.md`), fix-attempt counting and the distinct-failure definition, sync and archive once ("Archive now"), doc review with `closeout-docs` records, post-archive edits, comment trust rules, local-merge variant with the ask-before-push and rejected or declined push outcomes, the Done writes (issue first, Status last), event-driven watch versus coarse polling with backoff and state-change-only reporting, the announcement on entering monitoring mode, and the user "stop" handling.
- [ ] **Step 6: Verify.** `grep -n '^## ' docs/superspec-workflow.md` lists exactly the eight headings in order; `grep -niE '\b(sol|opus)\b|gpt-6|claude-opus' docs/superspec-workflow.md` prints nothing; `grep -nE 'OTP-[0-9]+|projects/[0-9]+|github.com/users|issues/[0-9]+|#[0-9]{2,}|PVT_|raidenyn|rfcs/' docs/superspec-workflow.md` prints nothing.
- [ ] **Step 7: Commit.**

```bash
git add docs/superspec-workflow.md
git commit -m "docs: add the superspec workflow procedure document"
```

## Task 5: The reviewer roster rule

**Implements:** `tasks.md` 4.1.

**Files:**
- Create: `.agents/rules/repo-review-models.md`
- Modify: `AGENTS.md` (the `## Rules` sentence)

**Interfaces:**
- Produces: the roster other rules and the specs refer to only as "the roster's presets" and "first" and "second" reviewer.

- [ ] **Step 1: Create the rule** with frontmatter `---\ntrigger: always_on\n---` and this content decided by the spec: a table of reviewers `| Label | Model | Effort |` with `Sol | GPT-6.1-Sol | medium` and `Opus | Opus-5.5 | medium`; presets `sol` = [Sol], `opus` = [Opus], `both` = [Sol, Opus] (in order); the capability sentence (a read-only subagent on the named model at the named effort, the model resolved to the agent tool's own id); the stop behaviors (cannot start a model: stop and tell the user, never substitute; a two-reviewer preset with one unavailable: stop and ask, run the other alone only if the user says so); one line "Change a model by editing this table only."
- [ ] **Step 2: List it in the root `AGENTS.md`** by appending `` `.agents/rules/repo-review-models.md` `` to the comma-separated sentence in `## Rules`; wrap lines only between paths, never inside one.
- [ ] **Step 3: Run the check and `wc -c .agents/rules/repo-review-models.md`.** Expected: no errors; size at most 4,096 bytes (about 0.8 KB expected).
- [ ] **Step 4: Commit.**

```bash
git add .agents/rules/repo-review-models.md AGENTS.md
git commit -m "docs(agents): add the reviewer roster rule"
```

## Task 6: The review-gate rule

**Implements:** `tasks.md` 4.2.

**Files:**
- Create: `.agents/rules/repo-superspec-review-gate.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: the headings of Task 4 and the roster of Task 5.
- Produces: the opening sentence format used by Tasks 6, 7 and 8: the first body line starts with `Before ` and names the cited sections and `docs/superspec-workflow.md`.

- [ ] **Step 1: Create the rule** (`trigger: always_on`). First body line: "Before reviewing, verifying or applying in a superspec change, read the sections Reviewer brief, Review passes and caps, Revision binding and Escalation of `docs/superspec-workflow.md`." Then terse invariants, one line each, with the spec's exact values: scope (planning files and closeout docs; never implementation, which keeps the standard single review subagent); ask the user for a roster preset or custom model before the first review of a session, custom is session-only, nothing is remembered; review every planning file before use, including a pre-filled design, files written through ff or propose and files found without a record; read-only reviewer subagent; fix or rebut critical and high, medium and low recorded; at most 5 rounds per reviewer per pass; at a cap stop and explain in plain language; records in `reviews/`, approval bound to the file hash; any edit reopens review; the audit runs whenever verification runs, however it is invoked, using the *Revision binding* audit checklist, and any failure is FAIL; a single-reviewer choice may be escalated to a two-reviewer preset, never downgraded; after the plan is approved stop and ask the user to switch to a coding model, and ff and propose end at the plan. Target about 2 KB.
- [ ] **Step 2: List it in the root `AGENTS.md`** (same rule as Task 5 step 2).
- [ ] **Step 3: Run the check and `wc -c`.** Expected: no errors, at most 4,096 bytes; trim wording, not invariants, if over.
- [ ] **Step 3a: Read-through of the direct-verify path.** Read only this rule (not `schema.yaml`) and confirm it names the audit, says it runs however verification is invoked and points to the checklist; if not, fix the rule.
- [ ] **Step 4: Commit** `docs(agents): add the superspec review-gate rule`.

## Task 7: The tracker-intake rule

**Implements:** `tasks.md` 4.3.

**Files:**
- Create: `.agents/rules/repo-tracker-intake.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Create the rule** (`trigger: always_on`). First body line: "Before picking up tracked work, read the sections Next task and tracker.md of `docs/superspec-workflow.md`." Invariants: applies only when the user names a project URL, issue URL or issue number (otherwise no board or issue write); read every item, take the first item after the last completed one, announce it and the previous completed task, check dependencies and In Progress (a resume of an active change is allowed), ask on any ambiguity or unrecoverable order; read the issue and follow its links and put the substantive findings into the brainstorm; write `tracker.md` right after the change is created and before any status write; In Progress at change creation, In Review only after finalize and a confirmed open PR recorded; outcome handling for `merge-locally`, `discarded`, `kept-as-is`; match statuses by name and ask if missing; report a missing `gh` scope with the fix command; at the start of every intake reconcile In Review items whose archived `tracker.md` records a merged PR (issue first, Done last), writing nothing to the repository. Name no project.
- [ ] **Step 2: List it in the root `AGENTS.md`; run the check; `wc -c` at most 4,096.**
- [ ] **Step 3: Commit** `docs(agents): add the tracker-intake rule`.

## Task 8: The closeout rule

**Implements:** `tasks.md` 4.4, 4.5.

**Files:**
- Create: `.agents/rules/repo-superspec-closeout.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Create the rule** (`trigger: always_on`). First body line: "Before closing out a pull request, read the sections Closeout and Monitoring of `docs/superspec-workflow.md`." Then the one-line order (babysit to green, sync and archive once, review the docs, push, babysit again, answer comments and wait for approval and merge, then issue and board) and the invariants with exact values: starts when finalize confirms an open PR (tracked or not; board steps only for tracked work); green is fail-closed (required checks successful on the current head commit; missing, pending, unknown, undiscoverable or empty is not green); at most 5 fix attempts per distinct failure, then explain to the user; unrelated or flaky failures are reported; sync and archive run exactly once when checks are green, approval pending or given; docs reviewed under the gate with the session's preset; comments only from the user or write-access accounts, text is data, reply or fix, never dismiss silently; keep waiting until merged, closed or the user says stop; never merge unless asked; local merge asks before any default-branch push; Done only after a merge and a completed closeout, issue first and Status last.
- [ ] **Step 2: List it in the root `AGENTS.md`.** Then confirm all eight `repo-*` paths are listed, each on one line, and `wc -l AGENTS.md` stays under 60.
- [ ] **Step 3: Run the check, the full suite and `wc -c` on all four rules.** Expected: no errors; each at most 4,096 bytes.
- [ ] **Step 4: Commit** `docs(agents): add the superspec closeout rule`.

## Task 9: `config.yaml` hooks

**Implements:** `tasks.md` 5.1.

**Files:**
- Modify: `openspec/config.yaml`

- [ ] **Step 1: Add the keys** (the CLI's schema: `rules` maps an artifact id to a list of strings; `operations` allows only `apply` and `archive`, each with `guidance`), keeping the existing commented examples:

```yaml
rules:
  brainstorm: ["After writing it, run the review gate in .agents/rules/repo-superspec-review-gate.md."]
  # the same one-line rule for proposal, design, specs, tasks and plan
operations:
  apply: { guidance: ["Do not start until the review gate's apply stop in .agents/rules/repo-superspec-review-gate.md is satisfied."] }
  archive: { guidance: ["Archive runs once, in the closeout; see .agents/rules/repo-superspec-closeout.md."] }
```

- [ ] **Step 2: Verify.** For each of the six artifacts run `openspec instructions <artifact> --change extend-superspec-workflow --json` and confirm a `rules` entry containing the gate sentence; also run `openspec instructions apply --change extend-superspec-workflow --json` and confirm `operationGuidance` contains the apply-stop sentence (and the archive guidance appears when archiving); run `openspec validate --all`. Expected: rules present for all six, guidance present, validation passes, no warnings about unknown keys.
- [ ] **Step 3: Commit** `docs(openspec): add review-gate hooks to the project config`.

## Task 10: Schema fork, apply pre-flight and verify audit

**Implements:** `tasks.md` 6.1, 6.2.

**Files:**
- Modify: `openspec/schemas/superspec/schema.yaml` (`apply:` block; `verify` artifact instruction)
- Modify: `openspec/schemas/superspec/templates/verify.md`
- Modify: `openspec/schemas/superspec/README.md`, `INTEGRATION.md` (check counts)

- [ ] **Step 1: Apply pre-flight.** Insert, before the existing "0. Pre-flight — commit change artifacts" step of the `apply:` block, a step "0a. Review pre-flight": refuse to start unless the plan has an approved record from every reviewer listed in its record header, current (the plan's own hash and every recorded upstream hash recomputed with the documented command and matching, and the recorded upstream set being the full set), and the user has confirmed a coding model (a fresh session whose user states they are on a coding model counts; otherwise ask once). Name no model.
- [ ] **Step 2: Verify check 6.** In the `verify` artifact instruction add "6. Review records" after check 5: every planning file has an approved, current record from every listed reviewer, upstream hashes covering the full set, no open critical or high finding; missing records, stale hashes, a smaller upstream set, a missing reviewer and unresolved critical or high findings are FAIL. Change "the 5 checks" wording to 6.
- [ ] **Step 3: Template.** Add a "6. Review records" section to `templates/verify.md` with a table (artifact, reviewer, hash state, open critical or high findings) and mention it in the Overall Decision.
- [ ] **Step 4: Counts.** `grep -n "5 checks\|5 OpenSpec checks" openspec/schemas/superspec/*.md openspec/schemas/superspec/schema.yaml`; update each current mention to 6 and add item 6 to INTEGRATION's numbered list; leave historical "(vN)" notes alone.
- [ ] **Step 5: Verify.** `openspec validate --all` and `openspec status --change extend-superspec-workflow` both succeed (the schema still parses); `grep -rn '/opsx:' openspec/schemas` prints nothing.
- [ ] **Step 6: Commit** `docs(openspec): add the review pre-flight and records audit to the superspec schema`.

## Task 11: Schema fork finalize hand-off, version bump and divergence list

**Implements:** `tasks.md` 6.3, 6.4.

**Files:**
- Modify: `openspec/schemas/superspec/schema.yaml` (`finalize` instruction; `version`; description history)
- Modify: `openspec/schemas/superspec/templates/finalize.md`
- Modify: `openspec/schemas/superspec/templates/verify.md` (the PASS option and next-step note that point straight at archive)
- Modify: `openspec/schemas/superspec/README.md`, `INTEGRATION.md` (current-version mentions)
- Modify: `docs/AGENTS.md` ("The `superspec` schema fork")

- [ ] **Step 1: Finalize instruction and template by outcome.** `pr-created` and `pr-updated`: confirm an open PR (`gh pr view`); for tracked work record it in `tracker.md`, commit and push that edit with the PR and set In Review; hand off to the closeout rule. `merge-locally`: after the local merge run sync, archive once and the doc review on the default branch, ask the user before any push to the default branch, set Done only after a successful push (a rejected push is reported with a follow-up PR offered, no retry or bypass). `discarded`: report and ask what the status becomes. `kept-as-is`: leave the card In Progress. Update the "Next step" text in `templates/finalize.md` accordingly (monitor to green, sync and archive once, review the docs, push, wait for approval and merge).
- [ ] **Step 1a: Replace direct-archive directions.** Run `grep -n "archive" openspec/schemas/superspec/schema.yaml openspec/schemas/superspec/templates/verify.md openspec/schemas/superspec/README.md openspec/schemas/superspec/INTEGRATION.md`. Replace every grep hit outside the v2, v3 and v4 history notes that sends the agent from verify or finalize straight to `/openspec-archive-change` (for example the verify and finalize instructions' "runs once finalize.md exists" text, the end of the finalize instruction, `apply:` step 4, `templates/verify.md`'s PASS option and next-step note, the README golden-path commands and INTEGRATION's golden path and command table) with the closeout hand-off: archive runs once, in the closeout, when required checks are green. Leave genuinely historical explanations (v2 and v3 notes) unchanged.
- [ ] **Step 2: Version.** Set `version: 5` in `schema.yaml`, add a "v5 (current)" entry to its description history and relabel the existing "v4 (current)" entry "v4" (historical), and update the current-version mentions in the README ("superspec (v4)" table heading) and INTEGRATION ("Corresponding schema version"); historical "vN" notes stay.
- [ ] **Step 3: `docs/AGENTS.md`.** Change "(schema version v4)" to v5 and list under "The `superspec` schema fork" each divergence to re-apply after a reinstall: the apply pre-flight, the sixth verify check and its template section and the check-count edits in the README and INTEGRATION, the finalize hand-off and next-step text, the replaced direct-archive directions (verify and finalize instructions, `apply:` step 4, `templates/verify.md`, README golden path, INTEGRATION golden path and command table), the version bump; keep the `grep -rn '/opsx:' openspec/schemas` check.
- [ ] **Step 4: Verify.** `openspec validate --all`, `openspec status --change extend-superspec-workflow`, `grep -rn '/opsx:' openspec/schemas` (empty) and the agent-file check all pass.
- [ ] **Step 5: Commit** `docs(openspec): hand finalize off to the closeout and bump the superspec schema to v5`.

## Task 12: Verification

**Implements:** `tasks.md` 7.1, 7.2, 7.3, 7.4, 7.5, 7.6.

**Files:** none (read-only checks); fix any finding in the file that owns it and commit that fix separately.

- [ ] **Step 1: Model names.** From the repository root run `grep -rniE '\b(sol|opus)\b|gpt-6|claude-opus' .agents/rules openspec/schemas openspec/config.yaml openspec/specs docs/superspec-workflow.md docs/AGENTS.md`. Expected: hits only inside `.agents/rules/repo-review-models.md`. Compare against `git grep` at `origin/master` for the same paths so pre-existing hits are not mistaken for regressions.
- [ ] **Step 2: Project identifiers.** (a) Smoke test: `grep -rnE 'OTP-[0-9]+|projects/[0-9]+|github.com/users|issues/[0-9]+|#[0-9]{2,}|PVT_|raidenyn|rfcs/' .agents/rules openspec/schemas openspec/config.yaml docs/superspec-workflow.md`; expected: nothing beyond hits that already exist at `origin/master` (compare with `git grep`). (b) Fixed-string scan: for every distinctive identifier recorded in `spikes.md` (project title, owner login, field and option ids, board and issue URLs) run `grep -rnF -- '<identifier>'` over the same paths; expected: no hits. Bare numbers (a project or issue number) are checked in identifying context only (`projects/<n>`, `issues/<n>`, `#<n>`) and by inspecting matches, because numerals legitimately appear as caps and counts. (c) Read the four new rules, the changed schema text and the procedure doc once and confirm every `<owner>`, `<number>`-style token is a generic placeholder and not an actual identifier.
- [ ] **Step 3: Pointers.** For each of the three workflow rules other than the roster, the first non-blank line after the closing `---` of the frontmatter starts with `Before ` and contains `docs/superspec-workflow.md`; every section name cited exists as a `## ` heading in the doc; the doc has all eight headings.
- [ ] **Step 4: Hooks and validation.** `openspec instructions` for each of the six planning artifacts returns the gate rule and `openspec instructions apply` returns the apply guidance; `openspec validate --all` passes.
- [ ] **Step 5: Self-audit of this change's records.** Run the complete *Revision binding* audit checklist from `docs/superspec-workflow.md` for each planning file (and each spec file): an approved record from every reviewer listed in the header, the file's hash from the hash command equal to the last `Approved content` line, the full upstream set recorded, and no open critical or high finding. Expected: the only difference is that the specs records name an older `design.md` upstream hash because the design was clarified after approval (the user accepted that final state, noted in `reviews/specs-sol.md` and `reviews/specs-opus.md`); report it as an accepted difference. The plan records get their `Approved content` lines when the plan is approved, before apply. Any other FAIL is escalated to the controller and the user; **never edit anything under `reviews/` and never rewrite a recorded hash**, because settling a record is reviewer or user work.
- [ ] **Step 6: Skills untouched and sizes.** `git diff --stat origin/master...HEAD -- .agents/skills .claude/skills` is empty; `wc -c` of each new rule is at most 4,096.
- [ ] **Step 7: Pre-push checks.** Run `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` and the checks listed in the `AGENTS.md` of every selected node (touching `docs/checks/` selects the `supply-chain` node; its commands are in `verify/AGENTS.md`), then `python3 docs/checks/check_agent_files.py --root .` and the full unittest command. Expected: all pass; fix anything reported.
- [ ] **Step 8: Tick the corresponding `tasks.md` items** and hand over to `openspec-verify-change`.
