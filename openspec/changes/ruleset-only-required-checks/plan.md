# Ruleset-only required checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the superspec closeout take its required-check inventory from GitHub rulesets only, and document the classic branch-protection contexts it ignores.

**Architecture:** Documentation-only change to three files that must agree: the procedure in `docs/superspec-workflow.md`, the always-on closeout rule, and (already in this change) the spec delta. No code, workflow or permission change.

**Tech Stack:** Markdown, `gh` CLI, the `docs` node checks (standard-library Python), OpenSpec 2.x.

**Spec:** `openspec/changes/ruleset-only-required-checks/specs/superspec-closeout/spec.md`

## Global Constraints

- Classic branch protection is never read and never contributes a check; a `404` from it is not evidence.
- The ignored classic contexts are exactly `lint-check`, `unit-tests`, `instrumented-tests`.
- Fail-closed: an unreadable ruleset response, a response with no `required_status_checks` rule, or an empty context list is not green, and the agent asks the user.
- `repo-*` rule files are capped at 4,096 bytes (`docs/AGENTS.md`); the file is 2,062 bytes today.
- Conventional Commits for every commit (`.agents/rules/repo-conventional-commits.md`).
- Do not edit generated files under `.agents/skills/openspec-*` or anything under `reviews/`.

## Review Focus

- A reader must not conclude that ignoring classic protection proves no classic protection exists; the text says the policy is deliberate and the endpoint's `404` proves nothing.
- A ruleset that requires one of the three retired names must still count. The union sentence keeps it, and the ignore sentence is scoped to classic rules only.

---

### Task 1: Ruleset-only discovery in the workflow procedure

**Files:**
- Modify: `docs/superspec-workflow.md:501-529` (section *Required checks and green*)
- Modify: `docs/superspec-workflow.md:627` (required-checks row of the *One polling cycle* table)

**Interfaces:**
- Consumes: the approved spec delta in this change (`Green means required checks succeeded on the current head`).
- Produces: the documented procedure the closeout rule cites; Task 2's rule text must match it.

- [ ] **Step 1: Replace the discovery block and the union paragraph**

Replace `docs/superspec-workflow.md:501-522` (from `### Required checks and green` through the
`An unestablished inventory is not green.` line) with:

````markdown
### Required checks and green

The ruleset response for the pull request's base branch is the whole inventory of required checks:

```bash
gh api repos/<owner>/<repository>/rules/branches/<base>
```

The required set is the **union** of the `required_status_checks` contexts every applicable ruleset reports in
that response. Read it once per base branch and reuse it across polling cycles.

Classic branch protection is **ignored**, never merged: do not read
`repos/<owner>/<repository>/branches/<base>/protection` or its `required_status_checks` endpoint. A
`404 Not Found` from it proves nothing — a token without repository admin gets one that does not distinguish
"no protection" from "no permission" — and it never adds a required check. Ignore the retired classic contexts
`lint-check`, `unit-tests` and `instrumented-tests` even when a classic rule still lists them: the CI reports
those checks as `<node> / <check>`, so a bare classic context of that name can never be satisfied and would hang
green forever. A ruleset that names one of them still counts, by the union rule above.

When the response cannot be read, reports no `required_status_checks` rule, or reports an empty context list,
the inventory is not established: stop and ask the user, use the answered inventory, and never treat the
missing rule as "nothing is required". An unestablished inventory is not green.
````

- [ ] **Step 2: Verify the stale two-source wording is gone**

Run: `grep -n "protection/required_status_checks\|every source that can apply" docs/superspec-workflow.md`
Expected: no output.

- [ ] **Step 3: Replace the polling-table required-checks row**

Replace line 627 with:

````markdown
| `gh api repos/<owner>/<repository>/rules/branches/<base>` | the base branch's required check contexts: the ruleset response is the whole inventory, read once per base branch |
````

Run: `sed -n '627p' docs/superspec-workflow.md`
Expected: the new row, with no mention of the classic endpoint.

- [ ] **Step 4: Commit**

```bash
git add docs/superspec-workflow.md
git commit -m "docs(superspec): read required checks from rulesets only"
```

### Task 2: Align the closeout rule

**Files:**
- Modify: `.agents/rules/repo-superspec-closeout.md:10` (the *Green is fail-closed* bullet)

**Interfaces:**
- Consumes: Task 1's procedure wording (same endpoint, same ignored names).
- Produces: the always-on rule text the spec's requirement is enforced through.

- [ ] **Step 1: Replace the bullet**

Replace the `- **Green is fail-closed:**` bullet (line 10) with:

```markdown
- **Green is fail-closed:** the inventory is the base branch's ruleset response
  (`repos/<owner>/<repository>/rules/branches/<base>`); classic branch protection and its retired contexts
  (`lint-check`, `unit-tests`, `instrumented-tests`) are ignored. Every required check has a successful result
  for the current head commit; a missing, pending or unknown check, an unreadable or empty ruleset response,
  and a missing `required_status_checks` rule are not green.
```

- [ ] **Step 2: Verify the size cap and frontmatter**

Run: `wc -c .agents/rules/repo-superspec-closeout.md && head -3 .agents/rules/repo-superspec-closeout.md`
Expected: fewer than 4096 bytes, and the frontmatter still reads `---\ntrigger: always_on\n---`.

- [ ] **Step 3: Commit**

```bash
git add .agents/rules/repo-superspec-closeout.md
git commit -m "docs(superspec): ignore classic protection in the closeout rule"
```

### Task 3: Repository verification

**Files:**
- Test: none (documentation); the checks below are the tests.

**Interfaces:**
- Consumes: both edited files.
- Produces: evidence for the change's verify step.

- [ ] **Step 1: Confirm no reader is left on the classic endpoint**

Run: `grep -rn "protection/required_status_checks\|classic branch protection" docs .agents`
Expected: only the new sentences, each saying classic protection is ignored or never read. Any hit that reads
the endpoint, or that describes the union of sources, is a miss from Task 1.

- [ ] **Step 2: Run the docs node agent-file check**

Run: `python3 docs/checks/check_agent_files.py --root .`
Expected: exit 0, no error findings.

- [ ] **Step 3: Run the docs node unit tests**

Run: `python3 -m unittest discover -s docs/checks -p 'test_*.py'`
Expected: OK.

- [ ] **Step 4: Validate the change**

Run: `openspec validate ruleset-only-required-checks --strict`
Expected: `Change 'ruleset-only-required-checks' is valid`.

- [ ] **Step 5: Run the selected node checks**

Run: `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)`
Expected: one `node=true|false` line per component with `docs=true` and every other node `false` — the change
touches only `docs/` and `openspec/` (a `false` line means that node is not selected). Then run the checks the
`docs` node's `docs/AGENTS.md` lists, which are steps 2 and 3 above.
