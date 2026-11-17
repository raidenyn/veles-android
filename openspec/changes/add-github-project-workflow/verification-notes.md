# Verification notes: add-github-project-workflow (tasks 8.1–8.4)

Real-environment run on 2026-11-17, from the worktree at `11ce2f0`, then `53c8cab` and `15bd1b6` (see
*Defects found*; each fix was re-copied to the scratch repository before the rows that follow it).

- Scratch repository: `raidenyn-shadow/scratch-project-flow` (default branch `main`). Private, except
  **public from 16:05 to 16:18 UTC** for the Action rows (approved by the user; see *Token permissions*).
- Scratch project: user project 2 of `raidenyn-shadow` (private; fields Status [Backlog, Ready, In
  Progress, In Review, Blocked, Done] and the number field "Implementation order"; built-in workflows:
  *Item closed* disabled, *Auto-add sub-issues to project* enabled, no *Auto-add to project*).
- Agent side, two logins: (1) the project owner's `gh` login (classic, scopes `project`, `repo`, `read:org`,
  `gist`, `admin:public_key`) — rounds 1 and 2; (2) the test PAT below — round 3, marked "(PAT)".
- Action side: `.github/workflows/project-flow.yml` copied byte-identical onto the scratch `main`.
- Tokens tried for `PROJECT_TOKEN`: PAT 1, a classic token of **another account** with `project,
  public_repo, repo:status, repo_deployment`; PAT 2, a classic token of the **project owner** with the same
  scopes (no `repo`). Neither token was printed or stored; each went in as the secret over stdin, and PAT 2
  reached the agent commands only as the environment of single commands.

## Outcome

Every transition passed through the **real Action** on real events (round 3), with PAT 2 on the repository
made temporarily public, and through the agent commands. With the repository private again, PAT 2 cannot
read it: the Action ran green but with `sync: partial (23 unreadable …)` and wrote nothing, and the agent
commands stop. Three defects found: two diagnostic ones were fixed, and one design question is recorded,
not fixed.

## 8.1 Setup

| Step | Result |
| --- | --- |
| Clone into the scratchpad; copy `scripts/project_flow.py`, `.github/project-flow.json`, `.github/workflows/project-flow.yml` from `HEAD`; push to `main` | done (script SHA-256 equal to `HEAD`'s); re-copied after `53c8cab` and after `15bd1b6` |
| Blocked label | created `blocked` in the scratch repository (the guide's setup step 1) |
| `check --project` before enabling | `fact: built-in workflow 'Item closed' is disabled; enable it in the project UI (Workflows)`, `warning: consider enabling Auto-add to project …`, `stop: setup-incomplete: 1 setup facts need attention`, exit 3 — the expected setup fact; not worked around. Same result with PAT 2 |
| Agent command before enabling | `sync` → `stop: not-enabled: PROJECT_URL is not set; …`, exit 3 (owner login and PAT 2) |
| `PROJECT_URL` set last | after the secret, after `check` and after the "merged between runs" setup, in each round |

## 8.3 Transitions through the real Action (round 3: PAT 2, repository public)

Run URLs are `https://github.com/raidenyn-shadow/scratch-project-flow/actions/runs/<id>`. Each event was
waited for before the next one, so each run below belongs to one event.

| Transition | How triggered | Action run | Observed status | Pass/fail |
| --- | --- | --- | --- | --- |
| no-op, `PROJECT_URL` unset (secret present) | real events: PR #27 opened, merged; issue #26 closed | 44121943455, 44121965619, 44121977979: `::notice::project-flow not configured`, checkout and reconcile skipped | success, nothing written | pass |
| milestone copy onto a PR opened and merged between runs; Done for its issue | PR #27 (`Closes #26`, #26 in `M3 e2e`) opened and merged while unset; then `PROJECT_URL` set and `workflow_dispatch` | 44122097447: `write: …#26 Status: (none) -> Done`, `write: …#27 milestone: M3 e2e` | #26 Done; PR #27 milestone `M3 e2e` | pass |
| empty-milestone report | same run | 44122097447: `milestone 'M3 e2e' in … is complete: no open issues; not closed` | milestone left open | pass |
| checkout ref on `workflow_dispatch` | same run | `ref: main`; `git checkout --progress --force -B main refs/remotes/origin/main` | default branch checked out | pass |
| new items get a Status | four new issues added with orders 61–64; `workflow_dispatch` | 44122317280: four `Status: (none) -> Ready` | Ready | pass |
| Ready → Backlog on blocker add | native `blocked_by` links #28 ← #29 and #31 ← #30 (GitHub emits no event for a dependency, so `workflow_dispatch`, the documented path) | 44122410951: `…#28 Status: Ready -> Backlog`, `…#31 Status: Ready -> Backlog` | Backlog | pass |
| Backlog → Ready on blocker closed completed | `gh issue close 29 --reason completed` (real `issues.closed`) | 44122530443: `…#28 Status: Backlog -> Ready`, `…#29 Status: Ready -> Done` | Ready | pass |
| not-planned blocker keeps Backlog + report | `gh issue close 30 --reason "not planned"` | 44122614347: `…#30 Status: Ready -> Done`, `…#31: blocker …#30 closed as not planned`, no write for #31 | Backlog | pass |
| refused Backlog claim (PAT) | `start <repo>#31` | `stop: not-ready: …#31 is Backlog: blocker …#30 closed as not planned`, exit 3 | Backlog | pass |
| claim (PAT) | `start <repo>#28` → real `issues.assigned` | command: `assignees: +raidenyn-shadow`, `Status: Ready -> In Progress`, `started: …#28`; run 44122752603: `no changes written` (agrees) | In Progress | pass |
| draft PR without keyword | `gh pr create --draft` #32 (same-repo branch) | 44122872273: `no changes written` | In Progress | pass |
| draft → ready → In Review, milestone copy onto the open PR | `gh pr edit 32 --body-file` (`Closes #28`) + `gh pr ready 32` (16:11:45) | 44122966816 (created 16:11:48): `…#28 Status: In Progress -> In Review`, `…#32 milestone: M3 e2e` | In Review; PR milestone `M3 e2e` | pass |
| back to draft → In Progress | `gh pr ready 32 --undo` | 44123140643: `…#28 Status: In Review -> In Progress` | In Progress | pass |
| blocked label on | `gh issue edit 28 --add-label blocked` | 44123233158: `…#28 Status: In Progress -> Blocked` | Blocked | pass |
| blocked label off | `--remove-label blocked` | 44123321456: `…#28 Status: Blocked -> In Progress` | In Progress (derived again) | pass |
| ready again | `gh pr ready 32` | 44123427211: `…#28 Status: In Progress -> In Review` | In Review | pass |
| merge with the closing keyword → Done | `gh pr merge 32 --squash` → #28 `CLOSED/COMPLETED` | 44123516027 (PR closed): `…#28 Status: In Review -> Done` — **written by the Action** (*Item closed* disabled); 44123524379 (issue closed): `no changes written` | Done | pass |
| empty-milestone report after the merge | same runs | `milestone 'M3 e2e' … is complete` | — | pass |
| dropped events (a burst) | four label edits on #8 and #6 within ~3 s | 44123663609 ran and already read the final state; 44123674671 and 44123678302 **cancelled** (superseded pending runs of the concurrency group); 44123682354 ran: `no changes written` | #6, #8 Ready; Blocked never written | pass |
| idempotence | `workflow_dispatch` with nothing changed | 44123833203: `no changes written`, `sync: complete` | — | pass |
| `followup` (PAT) | `followup --parent <repo>#33 (order 70) --blocker <repo>#31 …` | `created: #34`, sub-issue, `skipped set_milestone: … has no milestone`, `blocked by …#31`, `added to the project` (this time the script's own `addProjectV2ItemById`), `Implementation order: 70.1` | order 70.1 | pass |
| `next` (PAT) | `next` | writes the two new items' Statuses, `next: …#6 order=20.5` | — | pass |
| closes of throwaways | three `gh issue close` in a row | 44124044441: three `-> Done`; 44124052399 cancelled; 44124058012 `no changes written` | Done | pass |

### Agent-command rows (rounds 1–2, the owner's login; the Action was a no-op then)

| Transition | How triggered | Command output | Observed status | Pass/fail |
| --- | --- | --- | --- | --- |
| Ready → Backlog / Backlog → Ready / not-planned report | native link, `gh issue close` (completed, not planned), agent `sync` | `#16 Ready -> Backlog`, `#16 Backlog -> Ready`, `#19: blocker #18 closed as not planned` | as derived | pass |
| refused Backlog claims | `start #16`, `start #19` | `stop: not-ready: … is Backlog: open blockers …#17` / `… blocker …#18 closed as not planned` | — | pass |
| claim | `start #16` | `assignees: +<login>`, `Ready -> In Progress`, `started:` | In Progress | pass |
| draft ↔ ready, blocked label, merge → Done, milestone copy (open PR and between runs), empty-milestone report | as in round 3, with agent `sync` after each step | the same writes as round 3 | as derived | pass |
| Blocked set by hand without the label is undone | `gh project item-edit` Status Blocked on #8, `sync` | `#8 Status: Blocked -> Ready` | Ready | pass |
| PR closed unmerged | `gh pr close 25` (ready PR closing the assigned #22), `sync` | `#22 Status: In Review -> In Progress` | In Progress | pass |
| `followup` | parent #21 (order 15) | `created: #22`, sub-issue, `milestone: M1 scratch`, `skipped add_project_item: already on the project` (auto-added by *Auto-add sub-issues*), `Implementation order: 15.1` | 15.1 | pass |
| `followup` interrupted + `--resume` | second follow-up with `--blocker #19`, killed (SIGKILL) right after `created: #23`; same arguments + `--resume 23` | sub-issue, milestone, `blocked by …#19`, `already on the project`, `Implementation order: 15.2`; a second resume skipped all five steps; a resume with another `--parent` → `stop: resume-disagrees: … it is a sub-issue of …#21, not of …#5; nothing written` | 15.2 | pass |
| order allocation 15 → 15.1 → 15.2 | the next value above 15 held by a non-descendant: 20.5 | 15.1, then 15.2 | — | pass |
| `next` normal / tie / missing-order / empty Ready set | normal; #22 order set equal to #21's; an unordered Ready #24; blocked label on every Ready item | `next: …#21 order=15`; `stop: tie: …#21 and …#22 share the lowest 'Implementation order' value 15`; `stop: missing-order: … #24` (and `--skip-unordered` → `skipped …#24`, `next: …#21`); `stop: nothing-ready: …` | — | pass |
| Action no-op, secret absent | real events after the secret was deleted (round 2), e.g. 44118620968, 44118658159, 44118720473, 44118836438, 44118472146 | notice, checkout and reconcile skipped | success | pass |

### Observations

1. **Closing-keyword lag (UNVERIFIED (c))**: three times (`gh pr edit` + `gh pr ready`, then a `sync` at
   once — twice the agent's, once the Action run created 3 s after `gh pr ready`) the first `sync` already
   read the closing reference and wrote In Review. No second `sync` was needed; the skill's one retry stays
   as a guard.
2. **Event coverage**: runs fired for `pull_request_target` `opened`, `ready_for_review`,
   `converted_to_draft`, `closed`, and `issues` `closed`, `labeled`, `unlabeled`, `assigned`, `unassigned`.
   A native dependency and a body edit fired nothing (as designed: dispatch or the daily run).
3. **Read-after-write lag on project items**: a `next` run a second or two after `gh project item-add` did
   not see the new item yet; the next run did. Snapshot-consistent, as `next` documents.
4. **Concurrency**: bursts cancel the superseded pending runs (8 of the 61 runs in all three rounds); a run that
   starts reads the board when it starts, so it reconciles to the latest facts and nothing is lost.
5. **Checkout ref (UNVERIFIED (d))**: `workflow_dispatch` resolves `github.event.repository.default_branch`
   to `main`. No `schedule` run happened in the window (cron 04:23 UTC), so its payload is not verified.

## 8.2 Token permissions

| Operation | Endpoint | Owner login (`project` + `repo` + others) | PAT 2, owner, `project` + `public_repo` (+ `repo:status`, `repo_deployment`) — **public** repository | PAT 2 — **private** repository | PAT 1, other account, same scopes — private repository |
| --- | --- | --- | --- | --- | --- |
| read the project (fields, workflows, items) | GraphQL `user.projectV2` | works | works (Action and agent) | the project reads, but every issue card's `content` is `null` | `NOT_FOUND` "Could not resolve to a ProjectV2 with the number 2." (private user project of another account) |
| write an item field (Status, order) | GraphQL `updateProjectV2ItemFieldValue` | works | works (Action and agent) | not reached (nothing readable) | not reached |
| add an item | GraphQL `addProjectV2ItemById` | not exercised (items were auto-added) | works (agent `followup`) | not tried | not reached |
| read issues, milestones, closing PRs | REST and GraphQL | works | works | issue content `null` | `Not Found (HTTP 404)` |
| assign (claim) | REST `POST …/issues/<n>/assignees` | works | works (agent `start`) | not tried | not reached |
| PR milestone PATCH | REST `PATCH …/issues/<pr>` | works | works (Action) | not reached | not reached |
| sub-issue, dependency | REST `POST …/sub_issues`, `…/dependencies/blocked_by` | works | works (agent `followup`) | not tried | not reached |
| create an issue | REST `POST …/issues` | works | works (agent `followup`) | not tried | not reached |
| read `PROJECT_URL` | REST `GET …/actions/variables/PROJECT_URL` | works | works (404 while unset; value when set) | plain `Not Found (HTTP 404)`, no scope hint — read as "not set" before `15bd1b6`; now `stop: missing-scope: run gh auth refresh -s repo` (the repository read carries gh's hint) | — |

What was verified: a **classic token with `project` and `public_repo`** (of the project owner) runs the
workflow and every agent command on a **public** repository; the owner's login with `project` and `repo`
runs every agent command on a private one. That a `repo`-scoped PAT runs the **workflow** on a private
repository is **not verified** (no such PAT was provided): it is implied by the private-repository failure
above, not observed. The REST `X-Accepted-OAuth-Scopes` header was empty for most endpoints (only the issue
and milestone reads list `repo`), so it does not narrow the minimum further.

## Not verified

- A classic PAT with `repo` running the **workflow** on a **private** repository (none provided).
- Fine-grained tokens (none provided).
- (a) The real text of a GraphQL `INSUFFICIENT_SCOPES` error. Observed only the REST hint `This API
  operation needs the "repo" scope. To request it, run:  gh auth refresh -h github.com -s repo`, which
  `map_gh_error` maps to `stop: missing-scope: run gh auth refresh -s repo`.
- (b) A blocker the token cannot see (`totalCount` vs `nodes`): no private blocker of another owner.
- (d) The `schedule` payload's `default_branch` fallback (no scheduled run in the window).
- (e) Fork-PR trust (no second account to open a fork PR).
- The `missing:` lines of an in-process `followup` failure (the resume ran after a kill).
- `start`'s `collision` and `assignment-failed` stops (one account).

## Defects found

1. **Fixed — misleading error when the project cannot be read** (`53c8cab`). Evidence: run 44117634152 (PAT
   1) printed only the organization fallback's error (`Could not resolve to an Organization …`) and hid the
   user read's `Could not resolve to a ProjectV2 with the number 2.` Fix: the stop names both reads;
   regression test `test_both_owner_reads_failing_name_both_reasons`.
2. **Fixed — a repository the token cannot see read as "PROJECT_URL is not set"; null cards read as "wrong
   type"** (`15bd1b6`). Evidence: with the repository private again, `sync --dry-run` with PAT 2 stopped with
   `not-enabled: PROJECT_URL is not set` although it was set (the variable read is a plain 404), and run
   44124206732 listed 23 items as `content has the wrong type`. Fix: after a 404 on the variable the
   repository is read; only a visible repository makes the variable "unset" (now `stop: missing-scope: run
   gh auth refresh -s repo`); an ISSUE card with `content: null` names the access remedy (re-run: run
   44124856228, `content not readable (null): a card from a repository the token cannot see; …`). Tests:
   `test_variable_404_on_an_invisible_repository_is_not_unset`,
   `test_variable_404_on_a_visible_repository_is_unset`, `test_null_issue_content_names_the_remedy`.
   Residual: the skill's issue-only intake step reads the variable with plain `gh api` and still treats a
   404 as "none"; the owner's login normally has `repo`, so it is recorded, not changed.
3. **Recorded, not fixed (design) — a token that cannot read the repository leaves the workflow green.**
   Evidence: runs 44124206732 and 44124856228 succeeded (exit 0) with `sync: partial (23 unreadable, …)` and
   wrote nothing; the run fails visibly only when the project itself is unreadable (44117634152). `sync`
   exits 0 on a partial run by design. Proposed: in `--mode action`, exit non-zero (or emit a warning
   annotation) when every item is unreadable or when `sync: partial`, so that a token that loses access is
   noticed; the setup guide's "an expired token makes the workflow fail visibly" holds for a dead token, not
   for one that lost repository access.

## Scratch artifacts

| Artifact | State left |
| --- | --- |
| Commits on scratch `main`: the copied files (from `11ce2f0`), a throwaway diagnostic workflow added and removed (it printed PAT 1's login and scopes, never the token), the script re-copied from `53c8cab` and `15bd1b6`, squash merges of PRs #15, #20, #27, #32 | kept |
| Label `blocked`; milestones `M2 e2e`, `M3 e2e` (open) | kept |
| Issues #14, #16, #26, #28 (closed by merges); #17, #29 (closed completed); #18, #19, #21–#24, #30, #31, #33, #34 (closed not planned) | closed, Done |
| PRs #15, #20, #27, #32 (merged), #25 (closed unmerged); their branches deleted | closed |
| Fixture items written by sync: #4 → Done, #5 → In Review, #6 → Ready; PRs #9, #10 got milestone `M1 scratch` | kept (a fixture re-recording sees these values) |
| Repository visibility | private again (`gh repo view --json visibility` → `PRIVATE`); public 16:05–16:18 UTC. While public, the commit author e-mail of the initial commit and the squash merges was visible |
| `PROJECT_TOKEN` secret | deleted |
| `PROJECT_URL` variable | unset (it was unset when the run started) |

## PR description block

```markdown
### Real-environment verification

Scratch repository `raidenyn-shadow/scratch-project-flow` and user project 2 of `raidenyn-shadow`; every
row with its run URL is in `openspec/changes/add-github-project-workflow/verification-notes.md`.

Passed through the real Action on real events (classic PAT of the project owner, `project` +
`public_repo`, repository temporarily public): Backlog ↔ Ready on a native blocker add (dispatch: GitHub
emits no dependency event) and a completed close; a not-planned blocker keeps Backlog with a report; draft PR
→ ready → In Review → draft → In Progress; the blocked label on and off; a merge with `Closes #` → Done,
written by the Action; the milestone copy onto an open PR and onto a PR opened and merged between runs; the
empty-milestone report; a no-op with `PROJECT_URL` unset and with the secret absent; idempotence; a burst of
events (superseded runs cancelled, the final state reconciled); the default branch checked out on dispatch.

Passed with the agent commands: `start` claim and refused Backlog claims; `followup` (15 → 15.1, 70.1)
and an interrupted one completed by `--resume` (15.2), plus `resume-disagrees`; `next` normal pick, tie,
missing-order and an empty Ready set. The closing keyword was linked by the first `sync` every time (3/3).

Not verified: a `repo`-scoped PAT running the workflow on a private repository, fine-grained tokens,
fork-PR trust, the schedule payload and a blocker invisible to the token.
Fixed during the run: the unreadable-project error names both owner reads; a repository the token cannot
see is no longer reported as an unset `PROJECT_URL`. Open: a token that loses repository access leaves the
workflow green with `sync: partial`.
```
