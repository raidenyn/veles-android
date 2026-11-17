# GitHub Project workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive every GitHub Project status from facts on GitHub with one shared stdlib script, run it from a repository Action on every event and from the agent at intake, and move the tracked-work procedure onto it, generic for any project.

**Architecture:** `scripts/project_flow.py` is one file:
- a transport layer (`gh api`, or a fake in tests);
- a read layer that builds per-item `Facts` and fails closed;
- a pure `derive()`;
- a `Writer` that re-reads before every write and honours `--dry-run`;
- the subcommands `sync`, `next`, `start`, `followup` and `check`.

`.github/workflows/project-flow.yml` runs `sync --mode action` on every event. The rules, the doc, the skill and the schema fork call the same commands.

**Tech Stack:** Python 3 stdlib (`unittest`, `subprocess`, `json`, `dataclasses`), the `gh` CLI (GraphQL and REST), GitHub Actions YAML, Node `node:test` (`verify/test`), Markdown guidance.

**Spec:** `proposal.md`, `design.md` (D1–D13) and `specs/github-project-automation/spec.md`, `specs/superspec-tracker-intake/spec.md` and `specs/superspec-closeout/spec.md` in this change directory. `tasks.md` is the task list this plan decomposes.

## Global Constraints

- **Derivation**, first match wins:
  0. closed → `done`;
  1. blocked label → `blocked`;
  2. an open, non-draft closing PR → `in_review`;
  3. an assignee → `in_progress`;
  4. every blocker closed as `COMPLETED` → `ready`, else `backlog`.

  A blocker with `stateReason` `NOT_PLANNED` or `DUPLICATE` keeps `backlog` and is reported. The previous Status is never an input.
- **Role keys:** exactly `backlog`, `ready`, `in_progress`, `in_review`, `blocked`, `done`. Options are matched by name; labels case-insensitively.
- **Config:** `.github/project-flow.json` holds only `status_field`, `roles`, `order_field` and `blocked_label`. The defaults are `"Status"`, `{"backlog":"Backlog","ready":"Ready","in_progress":"In Progress","in_review":"In Review","blocked":"Blocked","done":"Done"}`, `"Implementation order"` and `"blocked"`. Any value matching a URL, `#\d+`, a node id (`^[A-Z]{1,4}_[A-Za-z0-9_-]{8,}$`) or an all-digit string is invalid.
- **Settings:** the repository variable `PROJECT_URL` and the secret `PROJECT_TOKEN`. Nothing else names the project.
- **Exit reasons** (stdout line `stop: <reason>: <detail>`, exit code 3): `not-enabled`, `variable-unreadable`, `nothing-ready`, `tie`, `missing-order`, `incomplete-read`, `not-ready`, `collision`, `missing-option`, `config-invalid`, `resume-disagrees`, `no-order-room`, `parent-unordered`, `parent-as-blocker`, `missing-scope`. Exit code 0 means success, 2 means usage.
- **Order allocation:** steps 0.1, 0.01 and 0.001, with values compared after `round(x, 3)`.
- **Action triggers:**
  - `issues`: closed, reopened, labeled, unlabeled, assigned, unassigned;
  - `pull_request_target`: opened, reopened, ready_for_review, converted_to_draft, closed;
  - a daily `schedule`;
  - `workflow_dispatch`.

  Never `edited`. One concurrency group with `cancel-in-progress: false`. Checkout of the base branch only. Actions pinned by SHA plus version comment, as in `pr-title.yml`.
- **Scripts:** run as `python3 -I scripts/project_flow.py ...`, which puts no sibling imports on the path, so the tool is a single file.
- **Tests:** `scripts/tests/test_project_flow*.py`, run by `./gradlew scriptsTest` (`python3 -I -m unittest discover -s scripts/tests`). No network, no `time.sleep`, no wall clock.
- **Test imports:** every test module starts as `scripts/tests/test_last_green.py` does: `SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`, `sys.path.insert(0, SCRIPTS)`, and `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))`. Then `import project_flow` and `from project_flow_fake import FakeTransport, board, issue, pr, item`.
- **Commits:** before each commit step, stage exactly the files listed in that task's **Files** block (`git add <those paths>`). Never `git commit -a`.
- **Trusted code:** the workflow always checks out `${{ github.event.repository.default_branch }}`, whatever the event, so no event, including `workflow_dispatch` from another ref, runs non-default code with the token.
- **Identifiers:** no concrete project number, issue number, project URL, owner, RFC path or node id in the rules, skill, schema, config, `openspec/config.yaml` or docs.
- **Commits:** every subject is `type(scope): description`. Guidance-only commits are `docs`/`chore`; the script is `feat(scripts)`.
- **Before each push:** run `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` and every selected node's checks.

## Review Focus

- **A view URL:** the user passes `…/projects/N/views/2` while `PROJECT_URL` is `…/projects/N`. They must count as the same project after normalization (Task 6).
- **Label case:** a label spelled `Blocked` when the config says `blocked`. It still derives `blocked`, because GitHub label names are case-insensitive (Task 3).
- **A cross-repository closing PR:** a PR in repository B closes an issue in A while the Action runs in A. The issue derives `in_review`, but the milestone is not written to B's PR (Task 4).
- **A token without the `project` scope:** the output is `stop: missing-scope` with the fix `gh auth refresh -s project`, not a stack trace (Task 1).
- **Float noise:** order values 15.1 and 15.100000001 are a tie for `next`, and `15.1 + 0.1` allocates 15.2, not 15.200000000000001 (Tasks 7 and 8).

---

### Task 0: Scratch access

- [ ] **Step 1:** Ask the user for a scratch repository and a scratch user-owned project, and for permission to create test issues, PRs, milestones and labels there and to add a `PROJECT_TOKEN` secret. Record their names only in the session, never in a committed file. If refused, stop. Tasks 2 and 12 need them.

### Task 1: Transport, config and fake

**Files:**
- Create: `scripts/project_flow.py`
- Create: `.github/project-flow.json`
- Create: `scripts/tests/project_flow_fake.py`
- Test: `scripts/tests/test_project_flow_config.py`

**Interfaces:**
- Produces:
  - `class Transport(Protocol)`: `graphql(query: str, variables: dict) -> dict`, `rest(method: str, path: str, body: dict | None = None) -> Any`.
  - `class GhTransport(Transport)`: shells to `gh api graphql -f query=… -F/-f …` or `gh api -X METHOD path --input -`, and raises `GhError(message: str)`.
  - `class Stop(Exception)`: `reason: str`, `detail: str`.
  - `@dataclass(frozen=True) class Config`: `status_field: str`, `roles: dict[str, str]`, `order_field: str`, `blocked_label: str`.
  - `load_config(path: str) -> Config`, which raises `Stop("config-invalid", …)`.
  - `validate_config(raw: dict) -> list[str]`.
  - `map_gh_error(e: GhError) -> Stop | None`: a message containing `missing required scopes` maps to `Stop("missing-scope", "run gh auth refresh -s project")`, anything else to `None`.
  - `main(argv: list[str], transport: Transport | None = None) -> int`.
- Produces (tests): `FakeTransport(responses: list[tuple[matcher, response]])`, which records every call in `.calls` and every mutation in `.mutations`, serves responses in order, and can raise `GhError` for a matcher; it also offers `on_call(hook)` to mutate state between calls. Plus the builders `issue(...)`, `pr(...)`, `item(...)` and `board(...)`, which return GraphQL-shaped dicts.

- [ ] **Step 1: Write failing tests** in `test_project_flow_config.py`:
  - `test_defaults_file_loads`: `load_config(".github/project-flow.json").roles["in_review"] == "In Review"`.
  - `test_unknown_role_rejected`: an error naming `roles.wip`.
  - `test_missing_role_rejected`.
  - `test_identifier_rejected`: for each of `"https://github.com/users/x/projects/3"`, `"PVT_kwHOAbc12345"`, `"#12"` and `"3"`, `validate_config` returns one error.
  - `test_main_config_invalid_exit`: `main(["check","--config",bad])` returns 3 and prints `stop: config-invalid`.
  - `test_missing_scope_maps_to_stop`: `map_gh_error(GhError("…missing required scopes [project]…"))` returns `Stop("missing-scope", "run gh auth refresh -s project")`. The CLI-through-`sync` case is `test_missing_scope_message_via_sync` in Task 4.
- [ ] **Step 2: Run** `python3 -I -m unittest discover -s scripts/tests -p 'test_project_flow_config.py' -v`. Expect FAIL (no module).
- [ ] **Step 3: Implement** the interfaces above in `scripts/project_flow.py`, and write `.github/project-flow.json` with the defaults from *Global Constraints*. Use `argparse` subcommands `sync | next | start | followup | check`. The common flags are `--project URL`, `--repo OWNER/NAME`, `--mode action|agent` (default `agent`), `--config PATH` (default `.github/project-flow.json`) and `--dry-run`. `main` maps `Stop` to exit 3 and its line, and maps a `GhError` containing `missing required scopes` to `missing-scope`.
- [ ] **Step 4: Run** the Step 2 command. Expect PASS.
- [ ] **Step 5: Commit** `git add scripts/project_flow.py scripts/tests/project_flow_fake.py scripts/tests/test_project_flow_config.py .github/project-flow.json && git commit -m "feat(scripts): add the project_flow transport and config"`

### Task 2: Complete reads and recorded fixtures

**Files:**
- Modify: `scripts/project_flow.py`
- Create: `scripts/tests/fixtures/project_flow/*.json`
- Test: `scripts/tests/test_project_flow_read.py`

**Interfaces:**
- Consumes: `Transport`, `Config`, `Stop` (Task 1).
- Produces:
  - `@dataclass class Blocker`: `repo: str`, `number: int`, `state: str`, `state_reason: str | None`.
  - `@dataclass class PullRef`: `repo`, `number`, `state` (`OPEN | CLOSED | MERGED`), `is_draft: bool`, `milestone: str | None`.
  - `@dataclass class Facts`: `item_id`, `content_id`, `repo`, `number`, `state`, `labels: frozenset[str]` (lowercased), `assignees: frozenset[str]`, `closing_prs: tuple[PullRef, ...]`, `blockers: tuple[Blocker, ...]`, `order: float | None`, `status: str | None` (option name), `parent: tuple[str, int] | None`, `sub_issues: tuple[tuple[str, int], ...]`, `milestone: str | None`, `database_id: int` (the REST issue id used by the sub-issue and dependency endpoints).
  - `Facts.inputs() -> tuple`: the derivation inputs (state, labels, assignees, closing-PR states and draft flags, blocker states and reasons). The `Writer` compares these, not the derived role.
  - `@dataclass class Board`: `project_id`, `status_field_id`, `options: dict[str, str]` (name → option id), `order_field_id: str | None`, `items: list[Facts]`, `unreadable: list[str]` (item ids with a reason), `workflows: dict[str, bool]`.
  - `read_board(t: Transport, project_url: str, cfg: Config) -> Board`.
  - `read_item(t, item_id: str, cfg) -> Facts`, which raises `Stop("incomplete-read")`.
  - `parse_project_url(url) -> tuple[owner: str, number: int]`, which accepts `/users/` and `/orgs/` and strips `/views/<n>`.
  - `read_text(t, repo, number) -> str`: the issue body plus every comment, paginated to the end; raises `Stop("incomplete-read")`. Used only by `check`.
  - `read_pr_closing_issues(t, repo, number) -> list[tuple[str, int, str | None]]`: every issue the PR closes, with its milestone title, whether or not it is on the project. Reads `pullRequest.closingIssuesReferences(first: 100)` to the last page and raises `Stop("incomplete-read")`. Used by the milestone copy.
  - `read_descendants(t, repo, number) -> set[tuple[str, int]]`: the sub-issues at every depth, whether or not they are on the project, paginated to the end; raises `Stop("incomplete-read")`. Used only by `followup`.

- [ ] **Step 1: Record fixtures** from the Task 0 scratch project with `gh api graphql` (the queries written in Step 4) into `scripts/tests/fixtures/project_flow/`:
  - `items_page1.json` and `items_page2.json` (`hasNextPage` true, then false);
  - `issue_facts.json`, covering `blockedBy` with `COMPLETED`, `NOT_PLANNED` and `DUPLICATE` reasons, `closedByPullRequestsReferences(includeClosedPrs:true)` with a draft and a ready PR, assignees, labels, `parent`, `subIssues` at two depths, and `milestone`;
  - `fields.json`;
  - `workflows.json`;
  - `milestones.json` (REST).

  Then replace logins, owners and node ids with placeholders (`owner`, `PVTI_item1`, …) by a scripted `sed` whose output is reviewed; no token or real id remains (`grep -E 'gh[pousr]_|PVT_kw'` finds nothing).
- [ ] **Step 2: Write failing tests** in `test_project_flow_read.py`:
  - `test_reads_all_pages`: two item pages give `len(board.items) == <sum>`.
  - `test_failing_page_is_incomplete`: the second page raises `GhError`, so `read_board` raises `Stop("incomplete-read")`.
  - `test_partial_blocker_read_marks_item_unreadable`: an item's `blockedBy.pageInfo.hasNextPage` is true and the follow-up page fails; that item id is in `board.unreadable` and absent from `items`, and the other items are present.
  - `test_facts_parse_fixture`: the `issue_facts.json` blockers' state reasons equal `["COMPLETED","NOT_PLANNED","DUPLICATE"]`, and the labels are lowercased.
  - `test_parse_project_url_strips_view`: `parse_project_url(".../users/o/projects/3/views/2") == ("o", 3)`.
  - `test_read_text_paginates_comments`, and `test_read_text_failing_page_incomplete`.
  - `test_read_pr_closing_issues_paginates_and_fails_closed`.
  - `test_read_descendants_two_depths_off_project`: a grandchild not on the project is included.
  - `test_read_descendants_failing_page_incomplete`.
- [ ] **Step 3: Run** `python3 -I -m unittest discover -s scripts/tests -p 'test_project_flow_read.py' -v`. Expect FAIL.
- [ ] **Step 4: Implement** `read_board` and `read_item`. Use one items query with `fieldValueByName` for the status and order fields and the issue facts inline, at `first: 100` everywhere. Follow every `pageInfo.hasNextPage`, for the items and for each nested connection per item, with a per-item follow-up query. Try `user(login:)` and fall back to `organization(login:)`. Never treat a missing page as empty.
- [ ] **Step 5: Run** the Step 3 command. Expect PASS.
- [ ] **Step 6: Commit** `git add scripts/project_flow.py scripts/tests/project_flow_fake.py scripts/tests/test_project_flow_read.py scripts/tests/fixtures/project_flow/ && git commit -m "feat(scripts): read project facts completely and fail closed"`

### Task 3: Derivation

**Files:**
- Modify: `scripts/project_flow.py`
- Test: `scripts/tests/test_project_flow_derive.py`

**Interfaces:**
- Consumes: `Facts`, `Config` (Tasks 1–2).
- Produces: `derive(f: Facts, cfg: Config) -> tuple[str, list[str]]` (a role key, plus report lines).

- [ ] **Step 1: Write failing tests**, one per scenario of *Every status is derived from facts on GitHub*:
  - `test_last_blocker_closes_ready`: blocker `CLOSED/COMPLETED`, no assignee → `ready`.
  - `test_not_planned_blocker_backlog_reported`: → `backlog`, and one report contains `not planned`.
  - `test_duplicate_blocker_backlog_reported`.
  - `test_open_blocker_backlog`.
  - `test_draft_pr_assigned_in_progress`, then `test_ready_pr_in_review`.
  - `test_pr_closed_unmerged_back_to_in_progress`: a `CLOSED` non-draft PR is ignored → `in_progress`.
  - `test_blocked_label_wins_over_pr`.
  - `test_blocked_label_case_insensitive`: label `Blocked` with config `blocked` → `blocked`.
  - `test_closed_issue_done_even_with_label`.
  - `test_previous_status_ignored`: `status="Done"` on an open, unassigned issue with no blockers → `ready`.
  - `test_assigned_without_pr_in_progress`.
- [ ] **Step 2: Run** `python3 -I -m unittest discover -s scripts/tests -p 'test_project_flow_derive.py' -v`. Expect FAIL.
- [ ] **Step 3: Implement** `derive` as a pure function, following rules 0–4 from *Global Constraints* exactly.
- [ ] **Step 4: Run** the Step 2 command. Expect PASS.
- [ ] **Step 5: Commit** `git add scripts/project_flow.py scripts/tests/test_project_flow_derive.py && git commit -m "feat(scripts): derive project statuses from GitHub facts"`

### Task 4: Writer and `sync` (scopes, milestones)

**Files:**
- Modify: `scripts/project_flow.py`
- Test: `scripts/tests/test_project_flow_sync.py`

**Interfaces:**
- Consumes: `read_board`, `read_item`, `derive` (Tasks 2–3).
- Produces:
  - `preflight(board: Board, cfg, *, needs_order: bool) -> None`: raises `Stop("missing-option")` naming every configured option missing from `board.options`, and, when `needs_order` is true, a missing order field. **Every writing command calls it before its first mutation:** `sync` and `start` with `needs_order=False`, `next` and `followup` with `needs_order=True`.
  - `class Writer(t, board: Board, cfg, dry_run: bool, out: list[str])`, with:
    - `set_role(f: Facts, role: str) -> bool`: re-reads with `read_item` and skips with the report `skipped <item>: inputs changed` when `fresh.inputs() != f.inputs()`, even if the role would be the same; otherwise it writes with `updateProjectV2ItemFieldValue`.
    - `set_order(item_id, value: float)`;
    - `set_pr_milestone(repo, number, milestone_title) -> bool`: re-reads the PR first and skips with `skipped: milestone already set` when it now has one;
    - every method appends `write: …` or `would write: …` to `out`.
  - `sync(t, cfg, project_url, *, mode: str, repo: str | None, dry_run: bool, only: tuple[str, int] | None = None) -> SyncResult` (`writes: list[str]`, `reports: list[str]`, `unreadable: list[str]`). `only` limits the status and milestone pass to one issue; `start` uses it.

- [ ] **Step 1: Write failing tests** (FakeTransport boards):
  - `test_writes_only_differences`: a 3-item board with 1 stale status gives exactly 1 `updateProjectV2ItemFieldValue` mutation.
  - `test_second_run_no_write`: two `sync` runs on the same fake state give 0 mutations in the second.
  - `test_dropped_event_still_reconciled`: an open ready PR and an issue still `In Progress` → the item becomes In Review, and the PR milestone is copied from the issue.
  - `test_merged_pr_gets_milestone`: a `MERGED` PR with no milestone gets the issue's milestone.
  - `test_pr_with_milestone_unchanged`.
  - `test_conflicting_milestones_reported_not_set`.
  - `test_missing_milestone_in_pr_repo_reported`.
  - `test_empty_milestone_reported_not_closed`: REST milestones show `open_issues: 0, closed_issues: 2`; the report contains `complete`, and no milestone PATCH happens.
  - `test_unreadable_item_not_written_others_are`.
  - `test_action_mode_scope`: two repositories A and B with `mode="action", repo="o/A"` → writes only items whose `repo=="o/A"`, blockers in B are read, and a PR in B (Review Focus) gets no milestone write.
  - `test_agent_mode_covers_all`.
  - `test_fresh_read_skips_changed_item`: the FakeTransport `on_call` hook assigns the issue between the board read and `read_item`; the write is skipped, and the report says `inputs changed`.
  - `test_fresh_read_skips_same_role_input_change`: a second open blocker is added between the reads; the role is still `backlog`, but the inputs differ, so the write is skipped.
  - `test_pr_milestone_set_between_read_and_patch_skipped`.
  - `test_missing_scope_message_via_sync`: the fake raises the scope `GhError` on the first read, and `main(["sync",…])` prints `stop: missing-scope: run gh auth refresh -s project` and returns 3.
  - `test_preflight_order_field_only_when_needed`: with no order field, `sync` proceeds, while `preflight(..., needs_order=True)` raises `missing-option`.
  - `test_pr_closing_off_project_issue_conflict`: the PR closes project issue A (milestone X) and off-project issue B (milestone Y); a conflict is reported and no milestone is set.
  - `test_pr_closing_issue_unreadable_no_milestone`: `read_pr_closing_issues` fails; the PR is reported, and no milestone is set.
  - `test_missing_option_no_writes`: item 1 needs a writable change and item 2 needs the missing `in_review` option; `Stop("missing-option")`, and `fake.mutations == []`.
  - `test_empty_milestone_requires_project_issue`: a repository milestone with `open_issues: 0` whose closed issues are not on the project is not reported.
- [ ] **Step 2: Run** `python3 -I -m unittest discover -s scripts/tests -p 'test_project_flow_sync.py' -v`. Expect FAIL.
- [ ] **Step 3: Implement** `Writer` and `sync`:
  - the milestone copy covers PRs found through the issues' `closing_prs` (open or merged, same-repository PRs only in action mode). For each PR, `read_pr_closing_issues` gives the full set of closing issues. If the read fails, report and skip. The distinct non-null milestone titles must number exactly one, or the conflict is reported and nothing is set. Resolve the milestone number by title through REST `repos/{repo}/milestones?state=all`, and set it with `PATCH repos/{repo}/issues/{pr}`;
  - the empty-milestone report reads REST `repos/{repo}/milestones?state=open` and reports a milestone only when `open_issues == 0` **and** at least one board item is a closed issue with that milestone;
  - never call close/create/rename endpoints.
- [ ] **Step 4: Run** the Step 2 command. Expect PASS.
- [ ] **Step 5: Commit** `git add scripts/project_flow.py scripts/tests/project_flow_fake.py scripts/tests/test_project_flow_sync.py && git commit -m "feat(scripts): reconcile project statuses and PR milestones in sync"`

### Task 5: Convergence and dry-run suites

**Files:**
- Test: `scripts/tests/test_project_flow_convergence.py`
- Modify: `scripts/project_flow.py` only if a test exposes a defect.

**Interfaces:**
- Consumes: `sync`, `Writer` (Task 4). Only the sync-only cases live here. The cases that need `next`, `start` or `followup` are written red in Task 7 Step 1 and Task 8 Step 1, which name them.

Before Step 1, temporarily break the convergence path (for example, make `sync` read the previous Status), so the new tests are seen failing. Restore it afterwards.

- [ ] **Step 1: Write the sync-only interleaving tests** with `on_call` hooks:
  - `test_stale_write_over_blocked_converges`.
  - `test_stale_write_over_done_converges`.
  - `test_pr_event_racing_sync`.
- [ ] **Step 2: Write** `test_sync_dry_run_two_changes`: two `would write:` lines and `fake.mutations == []`.
- [ ] **Step 3: Run** `python3 -I -m unittest discover -s scripts/tests -p 'test_project_flow_convergence.py' -v` against the temporarily broken path. Expect FAIL. Then restore it and run again. Expect PASS.
- [ ] **Step 4: Commit** `git add scripts/tests/test_project_flow_convergence.py && git commit -m "test(scripts): cover project_flow sync convergence and dry run"`

### Task 6: Enable gate and `check`

**Files:**
- Modify: `scripts/project_flow.py`
- Test: `scripts/tests/test_project_flow_check.py`

**Interfaces:**
- Consumes: `read_board`, `Config`, `parse_project_url`.
- Produces:
  - `enabled_url(t, repo: str, *, mode: str, env: Mapping[str, str]) -> str | None`. With `mode == "action"` it reads `env["PROJECT_URL"]`; with `mode == "agent"` it reads REST `repos/{repo}/actions/variables/PROJECT_URL`. A 404 means `None`; any other error raises `Stop("variable-unreadable", "needs the Actions variables read permission")`.
  - `require_enabled(t, project_url, repo, *, mode, env)`, which raises `Stop("not-enabled")` unless both URLs normalize (via `parse_project_url`) to the same pair.
  - `check(t, cfg, project_url: str | None) -> CheckResult` (`facts: list[str]`, `warnings: list[str]`, `notice: str`).

- [ ] **Step 1: Write failing tests**:
  - `test_unset_variable_not_enabled`.
  - `test_action_mode_reads_env_agent_mode_reads_variable`.
  - `test_other_project_not_enabled`.
  - `test_view_url_matches_project_url` (Review Focus).
  - `test_variable_read_error_reported` (a 403 → `variable-unreadable`).
  - `test_sync_next_start_followup_refuse_when_not_enabled`.
  - `test_check_offline_no_transport_calls`: `check(None)` → `fake.calls == []`.
  - `test_check_facts`: these each produce one fact:
    - workflows `Item closed` disabled;
    - a missing order field;
    - an open item without order;
    - In Progress without an assignee;
    - Blocked without the label.
  - `test_check_prose_warning`: a body `"Depends on #12"` with no native link → one warning and no fact.
  - `test_check_notice_text`: the notice contains `does not prove` and `PROJECT_URL`.
  - Guard-related cases are covered by the contract test in Task 9: a missing variable and a missing secret both give `configured=false`.
- [ ] **Step 2: Run** `python3 -I -m unittest discover -s scripts/tests -p 'test_project_flow_check.py' -v`. Expect FAIL.
- [ ] **Step 3: Implement** the gate and call `require_enabled` at the start of `sync`, `next`, `start` and `followup`. Implement `check`. Prose detection uses the regex `(?i)\b(blocked by|depends on|after)\s+((?:[\w.-]+/[\w.-]+)?#\d+)` against the body and comments, then compares with the native `blockers`.
- [ ] **Step 4: Run** the Step 2 command. Expect PASS.
- [ ] **Step 5: Commit** `git add scripts/project_flow.py scripts/tests/test_project_flow_check.py && git commit -m "feat(scripts): gate project_flow on PROJECT_URL and add the setup check"`

### Task 7: `next` and `start`

**Files:**
- Modify: `scripts/project_flow.py`
- Test: `scripts/tests/test_project_flow_next_start.py`

**Interfaces:**
- Consumes: `sync`, `require_enabled`, `Writer`, `derive`.
- Produces:
  - `next_task(t, cfg, project_url, repo, *, skip_unordered: bool, dry_run: bool) -> Facts` (raises `Stop`), printing `next: <repo>#<n> order=<v>`.
  - `start(t, cfg, project_url, repo, issue: tuple[str, int], *, force: bool, dry_run: bool) -> None`. The login comes from REST `user`. It adds with `POST repos/{r}/issues/{n}/assignees {"assignees":[login]}`.

- [ ] **Step 1: Write failing tests**:
  - `test_ordinary_pick_by_value`: values 16, 17 and 22.5 placed in any board position → 16.
  - `test_assigned_ready_skipped`.
  - `test_tie_names_both`: `stop: tie` names both numbers.
  - `test_float_noise_is_tie`: 15.1 and 15.100000001 (Review Focus).
  - `test_nothing_ready`.
  - `test_missing_order_stops_and_lists`.
  - `test_skip_unordered_selects_rest`.
  - `test_incomplete_read_stops`.
  - `test_start_claims`: the mutations are, in order, add-assignee then the status write.
  - `test_start_refuses_backlog_names_blockers`: `stop: not-ready` lists `#<blocker>`.
  - `test_start_force_allows`.
  - `test_start_collision`.
  - `test_start_refuses_assigned_in_review_blocked_done`.
  - Convergence cases in `test_project_flow_convergence.py`:
    - `test_stale_ready_after_claim_converges`: sync A reads (unassigned), `start` assigns and sets In Progress, then A's write lands as Ready. Then `next` skips the item, and the next `sync` writes `In Progress`.
    - `test_backlog_demotion_racing_claim`.
    - `test_two_accounts_claim`.
  - Dry-run cases: `test_next_dry_run_no_mutation` and `test_start_dry_run_no_mutation`.
- [ ] **Step 2: Run** `python3 -I -m unittest discover -s scripts/tests -p 'test_project_flow_next_start.py' -v`. Expect FAIL.
- [ ] **Step 3: Implement** `next_task` and `start` per the interfaces, using `round(order, 3)` for comparison. In `start`, call `sync(..., only=issue)` after the write.
- [ ] **Step 4: Run** the Step 2 command and the Task 5 suite. Expect PASS, including the `start` convergence cases.
- [ ] **Step 5: Commit** `git add scripts/project_flow.py scripts/tests/test_project_flow_next_start.py scripts/tests/test_project_flow_convergence.py && git commit -m "feat(scripts): select the next task by order value and claim by assignment"`

### Task 8: `followup` and order allocation

**Files:**
- Modify: `scripts/project_flow.py`
- Test: `scripts/tests/test_project_flow_followup.py`

**Interfaces:**
- Consumes: `Board`, `Writer`, `require_enabled`.
- Produces:
  - `allocate_order(parent: Facts, items: list[Facts], descendants: set[tuple[str, int]]) -> float`, which raises `Stop("parent-unordered" | "no-order-room")`.
  - `followup(t, cfg, project_url, repo, *, parent: tuple[str, int], title: str, body_file: str, blockers: list[tuple[str, int]], pr: str, change_dir: str, found_at: str, resume: int | None, dry_run: bool) -> int`, returning the issue number.
  - The CLI is `followup --parent OWNER/REPO#N --title T --body-file F --blocker OWNER/REPO#N... --pr URL --change-dir PATH --found-at TEXT [--resume N]`.

- [ ] **Step 1: Write failing tests**:
  - `test_first_and_second`: 15 → 15.1 → 15.2 (`n` = 16).
  - `test_nested`: 15.1 with sibling 15.2 → 15.11.
  - `test_no_successor_uses_p_plus_one`: the largest value 15 → 15.1.
  - `test_no_room`: 15 and 15.001 exist with `n` = 15.001 → `no-order-room`.
  - `test_parent_unordered`.
  - `test_incomplete_descendant_read_stops`.
  - `test_float_rounding`: `15.1 + 0.1` gives `15.2` exactly after `round(x, 3)` (Review Focus).
  - `test_six_steps_in_order`: the mutation kinds are `[create_issue, add_sub_issue, set_milestone, add_blocked_by, add_project_item, set_order]`.
  - `test_body_links`: the body contains the PR URL, the change dir and `found_at`.
  - `test_parent_as_blocker_refused`: `stop: parent-as-blocker`, with no mutation.
  - `test_failure_after_create_prints_number_and_missing`: the project add fails; the output has `created: #<n>` and `missing: add_project_item, set_order`.
  - `test_resume_completes_only_missing`: no second `create_issue`, and no duplicate sub-issue or blocker.
  - `test_resume_disagreeing_parent`: `stop: resume-disagrees`, with no mutation.
  - `test_followup_dry_run_lists_six_steps_no_mutation`, in `test_project_flow_convergence.py`.
- [ ] **Step 2: Run** `python3 -I -m unittest discover -s scripts/tests -p 'test_project_flow_followup.py' -v`. Expect FAIL.
- [ ] **Step 3: Implement** `allocate_order` per *Global Constraints* and design D7:
  - `n` is the lowest rounded value above `p` among items not in `descendants`, or `round(p + 1, 3)` when there is none;
  - `v` is the largest rounded value in `[p, n)`;
  - the result is the first of `v + 0.1`, `v + 0.01` and `v + 0.001`, rounded to 3 decimals, that is `< n` and unused;
  - otherwise it raises `Stop("no-order-room")`.

  The Step 1 tests pin every branch.
  Then implement `followup`:
  - **Steps:** REST `POST repos/{r}/issues`; `POST repos/{r}/issues/{parent}/sub_issues {"sub_issue_id": <issue.id>}`; `PATCH` milestone; `POST repos/{r}/issues/{n}/dependencies/blocked_by {"issue_id": <id>}`; GraphQL `addProjectV2ItemById`; `set_order`.
  - **Checks:** each step checks first whether it is already done.
  - **Resume:** `--resume` reads the issue and stops on any disagreement before writing.
  - **Descendants:** they come from `read_descendants`, and the REST ids come from `Facts.database_id` or the create response.
- [ ] **Step 4: Run** the Step 2 command and the Task 5 suite. Expect PASS.
- [ ] **Step 5: Commit** `git add scripts/project_flow.py scripts/tests/test_project_flow_followup.py scripts/tests/test_project_flow_convergence.py && git commit -m "feat(scripts): file linked, resumable follow-up issues"`

### Task 9: The Action and its contracts

**Files:**
- Create: `.github/workflows/project-flow.yml`
- Modify: `verify/test/workflow-contracts.test.mjs`
- Modify: `verify/check-remote-code.mjs` (`REMOTE_CODE_SCOPES`)
- Modify: `.github/components.json` (the `tooling` node's `paths`)
- Test: `verify/test/workflow-contracts.test.mjs`, `scripts/tests/test_components.py` (existing)

**Interfaces:**
- Consumes: `python3 -I scripts/project_flow.py sync --mode action --repo "$GITHUB_REPOSITORY" --project "$PROJECT_URL"`, with the job summary appended to `$GITHUB_STEP_SUMMARY`.

- [ ] **Step 1: Write the failing contract test** `test('project-flow workflow reconciles on safe events only', …)` in `workflow-contracts.test.mjs`. It asserts the following, and each assertion has a negative fixture string that the helper rejects:
  - the `pull_request_target` types equal exactly `[opened, reopened, ready_for_review, converted_to_draft, closed]`;
  - no `edited` appears anywhere in `on:`;
  - every `actions/checkout` step has `ref: ${{ github.event.repository.default_branch }}`. Negative cases: a missing `ref`, `github.sha`, `github.ref`, and `github.event.pull_request.head.sha`;
  - no `head.sha` or `head.ref` appears;
  - every step after the guard has `if: steps.guard.outputs.configured == 'true'`;
  - the guard computes `configured` correctly: the test extracts the guard step's `run` script and executes it with `bash` four times, with `PROJECT_URL` empty or set and `HAS_TOKEN` `false` or `true`, and a temporary `GITHUB_OUTPUT` file. It expects `configured=false` for neither, URL only and token only, and `configured=true` only when both are present;
  - secrets referenced equal `{PROJECT_TOKEN}`;
  - `concurrency.cancel-in-progress` is `false`;
  - there is no `pip`, `npm` or `apt` install.
- [ ] **Step 2: Add** `'.github/workflows/project-flow.yml'` and `'scripts/project_flow.py'` to `REMOTE_CODE_SCOPES`. Run `cd verify && node --test test/workflow-contracts.test.mjs test/remote-code.test.mjs`. Expect FAIL: the workflow is missing, and the scope does not exist.
- [ ] **Step 3: Write** `project-flow.yml`:
  - `permissions: contents: read`;
  - one job on `ubuntu-latest`;
  - checkout pinned to the SHA used in `pr-title.yml`, with `ref: ${{ github.event.repository.default_branch }}`;
  - a guard step `id: guard`, with `env` `PROJECT_URL: ${{ vars.PROJECT_URL }}` and `HAS_TOKEN: ${{ secrets.PROJECT_TOKEN != '' }}`. It writes `configured=true|false` to `$GITHUB_OUTPUT`, and prints `::notice::project-flow not configured` when false;
  - every later step gated with `if: steps.guard.outputs.configured == 'true'`;
  - a step running `sync` with `GH_TOKEN: ${{ secrets.PROJECT_TOKEN }}` and `PROJECT_URL: ${{ vars.PROJECT_URL }}` passed through `env` (never interpolated into `run`), whose output is teed to `$GITHUB_STEP_SUMMARY`. `sync` appends `rateLimit { cost remaining }` from its GraphQL responses.

  It is not added to any `needs:` of `ci.yml`.
- [ ] **Step 4: Add exact `global-overrides` entries** to `.github/components.json`, because `.github/` is global and overrides are applied first:
  - `".github/workflows/project-flow.yml": ["tooling", "supply-chain"]` (supply-chain owns the scanner in `verify/`);
  - `".github/project-flow.json": ["tooling"]`.

  Add `test_project_flow_files_select_tooling` to `scripts/tests/test_components.py`, asserting `owner_nodes(".github/project-flow.json", CMAP) == {"tooling"}` (import it as the module does: `from components import owner_nodes` if it is not already imported) and the workflow's set. Run `python3 -I scripts/components.py check` and that test. Expect PASS.
- [ ] **Step 5: Run** `cd verify && npm test`, and `python3 -I -m unittest discover -s scripts/tests -p 'test_components.py'`. Expect PASS. Confirm that the `native-bridge/*/scripts/test_build_contract.py` suites read only their own workflows (`grep -n "workflows/" native-bridge/*/scripts/test_build_contract.py`), so no edit is needed. If one enumerates all workflows, extend it there.
- [ ] **Step 6: Commit** `git add .github/workflows/project-flow.yml .github/components.json verify/test/workflow-contracts.test.mjs verify/check-remote-code.mjs scripts/tests/test_components.py && git commit -m "feat(ci): add the project-flow reconciliation workflow"`

### Task 10: Guidance, schema fork and setup guide

**Files:**
- Modify: `docs/superspec-workflow.md` (*Next task*, *tracker.md*, *Closeout*, *Monitoring*; the headings unchanged)
- Create: `.agents/skills/github-project/SKILL.md`, and the symlink `.claude/skills/github-project -> ../../.agents/skills/github-project`
- Modify: `.agents/rules/repo-tracker-intake.md`, `.agents/rules/repo-superspec-closeout.md`
- Modify: `openspec/schemas/superspec/schema.yaml`, `openspec/schemas/superspec/INTEGRATION.md`, `openspec/schemas/superspec/templates/finalize.md`, `docs/AGENTS.md` (the fork divergence list)
- Create: `docs/github-project-setup.md`
- Modify: `scripts/AGENTS.md` (Build & Test plus a link)

**Interfaces:**
- Consumes: the CLI of Tasks 1–8 exactly: `sync`, `next [--skip-unordered]`, `start <owner/repo#n> [--force]`, `followup … [--resume N]`, `check [--project URL]`.

- [ ] **Step 1: Rewrite *Next task* and *tracker.md*** with the content listed in `tasks.md` 6.1, in prose plus the command lines, and with the stop-reason → agent-response table:

  | Reason | Agent response |
  | --- | --- |
  | `missing-order` | ask whether to `--skip-unordered` |
  | `not-ready` | describe the state and ask; `--force` only after explicit authorization |
  | any other | ask the user to name the task |

  Delete steps 2–5 of the old procedure and the *Reconcile at the start of every intake* subsection.
- [ ] **Step 2: Update *Closeout* and *Monitoring*** per `tasks.md` 6.2:
  - the finalize ready-for-review plus confirm-In-Review step;
  - the post-merge check;
  - the local merge, which closes the issue with a comment naming the pushed commit and then runs `sync`;
  - the stop report, which promises no later reconcile.
- [ ] **Step 3: Write `SKILL.md`** with frontmatter `name: github-project` and a `description` that triggers on tracked work from a project or issue. Its body is a numbered command checklist that cites the doc sections by name and restates no rule. Its PR-body template:

```markdown
Closes <owner>/<repo>#<n>

## Follow-up issues
- <owner>/<repo>#<m> — <title>
```

  Create the symlink with `ln -s ../../.agents/skills/github-project .claude/skills/github-project`.
- [ ] **Step 4: Edit the rules.**
  - `repo-tracker-intake.md`: keep its first sentence verbatim, and replace the bullets with the scope rule, "run the steps through the `github-project` skill", the no-identifiers rule and the missing-scope rule.
  - `repo-superspec-closeout.md`: per `tasks.md` 6.5.

  Run `wc -c .agents/rules/repo-*.md`. Expect every file to be at most 4096 bytes.
- [ ] **Step 5: Edit the schema fork:**
  - `schema.yaml` finalize: the pre-review PR is opened with `gh pr create --draft`; finalize runs `gh pr ready`, then `gh pr view --json state,isDraft`, which must report `OPEN` with `false`; it puts the closing keyword in the body, records the PR in `tracker.md`, runs `project_flow.py sync`, and confirms In Review without writing it;
  - the local-merge step closes the issue and syncs;
  - `INTEGRATION.md` gets the same;
  - `templates/finalize.md` keeps the order and replaces "set Done" with "close the issue if the merge did not, then run `project_flow.py sync`".

  Add a bullet for this divergence to the fork list in `docs/AGENTS.md`.
- [ ] **Step 6: Write `docs/github-project-setup.md`** with the sections listed in `tasks.md` 6.7, and the token section reading `<!-- minimum permissions: filled by verification -->` until Task 12. Add the `project_flow.py` lines and the guide link to `scripts/AGENTS.md`.
- [ ] **Step 7: Verify:**
  - `python3 -I docs/checks/check_agent_files.py --root .` → no findings;
  - `python3 -I -m unittest discover -s docs/checks -p 'test_*.py'` → OK;
  - `openspec validate --all` → `0 failed`;
  - `grep -n "^## \|^### " docs/superspec-workflow.md` still lists *Reviewer brief*, *Review passes and caps*, *Revision binding*, *Escalation*, *Next task*, *tracker.md*, *Closeout* and *Monitoring*;
  - `head -1` of each workflow rule still opens with its doc pointer.
- [ ] **Step 8: Commit** `git add docs/superspec-workflow.md .agents/skills/github-project/SKILL.md .claude/skills/github-project .agents/rules/repo-tracker-intake.md .agents/rules/repo-superspec-closeout.md openspec/schemas/superspec/schema.yaml openspec/schemas/superspec/INTEGRATION.md openspec/schemas/superspec/templates/finalize.md docs/AGENTS.md docs/github-project-setup.md scripts/AGENTS.md && git commit -m "docs(superspec): move tracked work onto derived project statuses"`

### Task 11: Local checks

- [ ] **Step 1:** Run `./gradlew installGitCliff scriptsTest` with the network on. Expect `BUILD SUCCESSFUL`.
- [ ] **Step 2:** With the network disabled, run `./gradlew --offline scriptsTest`. Expect `BUILD SUCCESSFUL` and every `test_project_flow*` test OK.
- [ ] **Step 3:** Run `cd verify && npm test`, `python3 -I native-bridge/host/scripts/test_build_contract.py`, `python3 -I native-bridge/installer/scripts/test_build_contract.py` and `python3 -I scripts/components.py check`. Expect all to pass.
- [ ] **Step 4: Run the identifier grep:**

```bash
grep -rnE 'projects/[0-9]+|PVT[A-Za-z]*_[A-Za-z0-9]{6,}|docs/rfcs/|#[0-9]{2,}' \
  .agents/rules .agents/skills/github-project openspec/config.yaml openspec/schemas/superspec \
  .github/project-flow.json docs/superspec-workflow.md docs/github-project-setup.md
```

  Expect no output; `<owner>`, `#<n>` and similar placeholders do not match.
- [ ] **Step 5:** Run `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` and every selected node's checks from its `AGENTS.md`. Expect all to pass.

### Task 12: Real-environment verification (before ready for review)

- [ ] **Step 1:** In the Task 0 scratch repository, copy in `scripts/project_flow.py`, `.github/project-flow.json` and `.github/workflows/project-flow.yml`. Set up the scratch project per `docs/github-project-setup.md`, with `PROJECT_URL` set last.
- [ ] **Step 2:** Create a fine-grained token limited to the scratch repository and try a `sync` write on the user-owned project. If GitHub refuses it, use a classic PAT with `project` and `repo`. Write the working minimum into the setup guide's token section, then repeat Task 11 Steps 2–5.
- [ ] **Step 3:** Drive and observe in the scratch project the transitions listed in `tasks.md` 8.3, through real events (not `workflow_dispatch` only). Record each as `transition | how triggered | Action run URL | observed status | pass/fail` in `verification-notes.md` in the change directory.
- [ ] **Step 4:** Put the scratch repository and project names and the table in the PR description. Confirm that `gh issue list` and the real board show no writes from this work. Commit `verification-notes.md` and the guide update: `git add openspec/changes/add-github-project-workflow/verification-notes.md docs/github-project-setup.md && git commit -m "docs(scripts): record the project-flow end-to-end verification"`.

### Task 13: Post-merge board migration (closeout follow-up)

- [ ] **Step 1:** After merge, run `check --project <board>` and show its facts and warnings to the user.
- [ ] **Step 2:** With approval for each write:
  - enable *Item closed* and *Auto-add to project* (issues only);
  - add `PROJECT_TOKEN`;
  - add the native links the user approves;
  - fill in missing order values;
  - turn manual Blocked statuses into the label.

  Re-run `check` until no fact remains.
- [ ] **Step 3:** The user sets `PROJECT_URL`. Run `sync` once and show the report. List the milestone re-planning for the user, and do not automate it.
