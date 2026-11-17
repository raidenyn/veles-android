# Verification notes: add-github-project-workflow (tasks 8.1–8.4)

Real-environment run on 2026-11-17, from the worktree at `11ce2f0`, then `53c8cab` and `15bd1b6` (see
*Defects found*; each fix was re-copied to the scratch repository before the rows that follow it).

- Scratch repository: `raidenyn-shadow/scratch-project-flow` (default branch `main`). Private, except
  **public from 16:05 to 16:18 UTC** for the Action rows (approved by the user; see *Token permissions*).
- Scratch project: user project 2 of `raidenyn-shadow` (private; fields Status [Backlog, Ready, In
  Progress, In Review, Blocked, Done] and the number field "Implementation order"; built-in workflows:
  *Item closed* disabled, *Auto-add sub-issues to project* enabled, no *Auto-add to project*).
- Agent side, two logins: (1) the project owner's `gh` login (classic, scopes `project`, `repo`, `read:org`,
  `gist`, `admin:public_key`) — rounds 1 and 2; (2) the test PAT below — round 3, marked "(PAT 2)".
- Action side: `.github/workflows/project-flow.yml` copied byte-identical onto the scratch `main`.
- Tokens tried for `PROJECT_TOKEN`: PAT 1, a classic token of **another account** with `project,
  public_repo, repo:status, repo_deployment`; PAT 2, a classic token of the **project owner** with the same
  scopes (no `repo`). Neither token was printed or stored; each went in as the secret over stdin, and PAT 2
  reached the agent commands only as the environment of single commands.

## Outcome

63 scenarios, enumerated in the single table below: **26 executed by the real Action** on real GitHub
events or `workflow_dispatch` (each row cites its run and its event type) and **37 executed by agent
commands** on real GitHub (each row gives the command and its output). **0 failed**: 61 pass, and two Action
rows are observations that exposed defects (row 25: defects 2 and 3; row 26: defect 3, recorded, not fixed).
Row 63 passes only after the fix for defect 2. The Action rows that write (6–24) ran with PAT 2 while the
repository was public; with the repository private again, PAT 2 cannot read it (rows 25, 26, 63).

## 8.1 Setup

| Step | Result |
| --- | --- |
| Clone into the scratchpad; copy `scripts/project_flow.py`, `.github/project-flow.json`, `.github/workflows/project-flow.yml` from `HEAD`; push to `main` | done (script SHA-256 equal to `HEAD`'s); re-copied after `53c8cab` and after `15bd1b6` |
| Blocked label | created `blocked` in the scratch repository (the guide's setup step 1) |
| `check --project` before enabling | `fact: built-in workflow 'Item closed' is disabled; enable it in the project UI (Workflows)`, `warning: consider enabling Auto-add to project …`, `stop: setup-incomplete: 1 setup facts need attention`, exit 3 — the expected setup fact; not worked around. Same result with PAT 2 |
| Agent command before enabling | `sync` → `stop: not-enabled: PROJECT_URL is not set; …`, exit 3 (owner login and PAT 2) |
| `PROJECT_URL` set last | after the secret, after `check` and after the "merged between runs" setup, in each round |

## 8.3 Scenarios

Conventions: `pf <cmd>` is `python3 -I scripts/project_flow.py <cmd> --project
https://github.com/users/raidenyn-shadow/projects/2 --repo raidenyn-shadow/scratch-project-flow` run from
the worktree (`check` without `--repo`); `R` is `raidenyn-shadow/scratch-project-flow` and `…#n` in an
output line is `R#n`. A run id is `https://github.com/raidenyn-shadow/scratch-project-flow/actions/runs/<id>`;
"event" is the run's `event` as `gh run view --json event` reports it, with the action that fired it. Every
agent command used the owner's login unless marked **(PAT 2)**. Each event was waited for before the next,
except in rows 23 and 25.

| # | Scenario | Executed by | Evidence | Observed | Result |
| --- | --- | --- | --- | --- | --- |
| 1 | no-op, `PROJECT_URL` unset (round 1, secret set) | real Action, `workflow_dispatch` | run 44117276392: `##[notice]project-flow not configured`; checkout and reconcile steps `skipped` | success, nothing written | pass |
| 2 | no-op, `PROJECT_URL` unset, real events (round 1) | real Action, `pull_request_target` (PR #15 opened; PR #15 merged), `issues` (#14 closed) | runs 44117452172, 44117468873, 44117477728: `##[notice]project-flow not configured` | success, nothing written | pass |
| 3 | no-op, `PROJECT_URL` unset, real events (round 3, PAT 2 secret set) | real Action, `pull_request_target` (PR #27 opened; merged), `issues` (#26 closed) | runs 44121943455, 44121965619, 44121977979: `##[notice]project-flow not configured` | success, nothing written | pass |
| 4 | no-op, secret absent (`PROJECT_URL` set) | real Action, `issues` (#17 closed), `pull_request_target` (PR #20 opened as draft; `ready_for_review`; `converted_to_draft`; closed) | runs 44118472146, 44118620968, 44118658159, 44118720473, 44118836438: `##[notice]project-flow not configured`; 44118836438's checkout and reconcile steps `skipped` | success, nothing written | pass |
| 5 | a token that cannot see the project fails visibly (PAT 1) | real Action, `workflow_dispatch` | run 44117634152: `stop: incomplete-read: cannot read project raidenyn-shadow/2 as a user or an organization: project fields: gh: Could not resolve to an Organization …`, exit 3 | failure | pass (the message was defect 1) |
| 6 | Done and milestone copy for a PR opened and merged between runs | real Action, `workflow_dispatch` after `PROJECT_URL` was set (PR #27, `Closes #26`, #26 in `M3 e2e`, opened and merged while unset) | run 44122097447: `write: …#26 Status: (none) -> Done`, `write: …#27 milestone: M3 e2e`, `sync: complete` | #26 Done; PR #27 milestone `M3 e2e` | pass |
| 7 | empty-milestone report | real Action, `workflow_dispatch` | run 44122097447: `milestone 'M3 e2e' in … is complete: no open issues; not closed` | milestone left open | pass |
| 8 | checkout ref on dispatch | real Action, `workflow_dispatch` | run 44122097447: `ref: main`, `git checkout --progress --force -B main refs/remotes/origin/main` | default branch | pass |
| 9 | new items get a derived Status | real Action, `workflow_dispatch` (#28–#31 added with orders 61–64) | run 44122317280: `write: …#28 Status: (none) -> Ready` and the same for #29, #30, #31 | Ready | pass |
| 10 | Ready → Backlog on a native blocker add | real Action, `workflow_dispatch` (links #28 ← #29 and #31 ← #30 through `POST …/dependencies/blocked_by`; GitHub emits no event for them) | run 44122410951: `write: …#28 Status: Ready -> Backlog`, `write: …#31 Status: Ready -> Backlog` | Backlog | pass |
| 11 | Backlog → Ready when the blocker closes as completed | real Action, `issues` (`gh issue close 29 --reason completed`) | run 44122530443: `write: …#28 Status: Backlog -> Ready`, `write: …#29 Status: Ready -> Done` | Ready | pass |
| 12 | a not-planned blocker keeps Backlog, with a report | real Action, `issues` (`gh issue close 30 --reason "not planned"`) | run 44122614347: `write: …#30 Status: Ready -> Done`, `…#31: blocker …#30 closed as not planned`; no write for #31 | Backlog | pass |
| 13 | the assignment event after a claim changes nothing | real Action, `issues` (assigned by row 60's `start`) | run 44122752603: `no changes written`, `sync: complete` | In Progress (written by `start`) | pass |
| 14 | a draft PR without the keyword changes nothing | real Action, `pull_request_target` (`gh pr create --draft`, PR #32) | run 44122872273: `no changes written` | In Progress | pass |
| 15 | ready for review → In Review | real Action, `pull_request_target` (`gh pr edit 32 --body-file` with `Closes #28`, then `gh pr ready 32` at 16:11:45) | run 44122966816 (created 16:11:48): `write: …#28 Status: In Progress -> In Review` | In Review; the closing reference was already linked | pass |
| 16 | milestone copy onto an open PR | real Action, `pull_request_target` (same event) | run 44122966816: `write: …#32 milestone: M3 e2e` | PR #32 milestone `M3 e2e` | pass |
| 17 | back to draft → In Progress | real Action, `pull_request_target` (`gh pr ready 32 --undo`) | run 44123140643: `write: …#28 Status: In Review -> In Progress` | In Progress | pass |
| 18 | blocked label on → Blocked | real Action, `issues` (`gh issue edit 28 --add-label blocked`) | run 44123233158: `write: …#28 Status: In Progress -> Blocked` | Blocked | pass |
| 19 | blocked label off → derived again | real Action, `issues` (`--remove-label blocked`) | run 44123321456: `write: …#28 Status: Blocked -> In Progress` | In Progress | pass |
| 20 | ready again → In Review | real Action, `pull_request_target` (`gh pr ready 32`) | run 44123427211: `write: …#28 Status: In Progress -> In Review` | In Review | pass |
| 21 | merge with the closing keyword → Done, written by the Action | real Action, `pull_request_target` (`gh pr merge 32 --squash`) and `issues` (#28 closed by it) | run 44123516027: `write: …#28 Status: In Review -> Done`, `milestone 'M3 e2e' … is complete`; run 44123524379: `no changes written` | Done (*Item closed* disabled) | pass |
| 22 | idempotence | real Action, `workflow_dispatch` with nothing changed | run 44123833203: `no changes written`, `sync: complete` | — | pass |
| 23 | a burst of events | real Action, four `issues` events within ~3 s (blocked label on #8, on #6, off #8, off #6) | run 44123663609: `no changes written`; 44123674671 and 44123678302 `cancelled`; 44123682354: `no changes written` | #6, #8 Ready; Blocked never written (the first run read the board after the last edit) | pass |
| 24 | closes of throwaway issues → Done | real Action, `issues` (#34 closed, ran; #33 closed, cancelled; #31 closed, ran) | run 44124044441: `write: …#31 Status: Backlog -> Done`, `…#33 Status: Ready -> Done`, `…#34 Status: Backlog -> Done`; 44124052399 `cancelled`; 44124058012: `no changes written` | Done | pass |
| 25 | a token without `repo` on a private repository (PAT 2, repository private again) | real Action, `workflow_dispatch` | run 44124206732: 23 × `unreadable …: content has the wrong type; not written`, `sync: partial (23 unreadable, 0 conflicts, 0 skipped)`, conclusion success | green, nothing written | observed: defects 2 and 3 |
| 26 | the same after `15bd1b6` | real Action, `workflow_dispatch` | run 44124856228: 23 × `content not readable (null): a card from a repository the token cannot see; …`, `sync: partial (23 unreadable …)`, conclusion success | green, nothing written | observed: defect 3 (recorded, not fixed) |
| 27 | setup check before enabling | agent command | `pf check` → `fact: built-in workflow 'Item closed' is disabled; enable it in the project UI (Workflows)`, `warning: consider enabling Auto-add to project …`, `stop: setup-incomplete: 1 setup facts need attention`, exit 3 | expected setup fact | pass |
| 28 | setup check with PAT 2 | agent command (PAT 2) | `pf check` → the same `fact:` and `warning:` lines, `stop: setup-incomplete: …`, exit 3 | — | pass |
| 29 | gated command before enabling | agent command | `pf sync` → `stop: not-enabled: PROJECT_URL is not set; set the repository variable PROJECT_URL to this project to enable it`, exit 3 | — | pass |
| 30 | first preview after enabling | agent command | `pf sync --dry-run` → `would write: …#4 Status: (none) -> Done`, `…#5 (none) -> In Review`, `…#6 Backlog -> Ready`, `…#14 (none) -> Done`, `…#9/#10 milestone: M1 scratch`, `…#15 milestone: M2 e2e`, `sync: complete`, exit 0 | nothing written | pass |
| 31 | first sync: Done and milestone copy for a PR merged between runs, empty-milestone report | agent command (PR #15, `Closes #14`, #14 in `M2 e2e`, opened and merged while unset) | `pf sync` → `write: …#14 Status: (none) -> Done`, `write: …#15 milestone: M2 e2e`, `milestone 'M2 e2e' in … is complete: no open issues; not closed`, `sync: complete` | #14 Done; PR #15 `M2 e2e` | pass |
| 32 | new items get a derived Status | agent command | `pf sync` → `write: …#16 Status: (none) -> Ready` and the same for #17, #18, #19 | Ready | pass |
| 33 | Ready → Backlog on a native blocker add | agent command (links #16 ← #17, #19 ← #18) | `pf sync` → `write: …#16 Status: Ready -> Backlog`, `write: …#19 Status: Ready -> Backlog` | Backlog | pass |
| 34 | refused Backlog claim (open blocker) | agent command | `pf start R#16` → `stop: not-ready: …#16 is Backlog: open blockers …#17`, exit 3 | Backlog | pass |
| 35 | Backlog → Ready on a completed close | agent command (`gh issue close 17 --reason completed`) | `pf sync` → `write: …#16 Status: Backlog -> Ready`, `write: …#17 Status: Ready -> Done` | Ready | pass |
| 36 | a not-planned blocker keeps Backlog, with a report | agent command (`gh issue close 18 --reason "not planned"`) | same `pf sync` → `write: …#18 Status: Ready -> Done`, `…#19: blocker …#18 closed as not planned`; no write for #19 | Backlog | pass |
| 37 | refused claim behind a not-planned blocker | agent command | `pf start R#19` → `stop: not-ready: …#19 is Backlog: blocker …#18 closed as not planned`, exit 3 | Backlog | pass |
| 38 | claim | agent command | `pf start R#16` → `write: …#16 assignees: +raidenyn-shadow`, `write: …#16 Status: Ready -> In Progress`, `started: …#16`, exit 0 | In Progress, assigned | pass |
| 39 | a draft PR without the keyword changes nothing | agent command (`gh pr create --draft`, PR #20) | `pf sync` → no line for #16 or #20, `sync: complete` | In Progress | pass |
| 40 | ready for review → In Review, milestone copy onto the open PR | agent command (`gh pr edit 20 --body-file` with `Closes #16`, `gh pr ready 20`, `pf sync` started < 1 s later, then again) | first `pf sync` → `write: …#16 Status: In Progress -> In Review`, `write: …#20 milestone: M2 e2e`; second → nothing for #16 | In Review | pass |
| 41 | back to draft → In Progress | agent command (`gh pr ready 20 --undo`) | `pf sync` → `write: …#16 Status: In Review -> In Progress` | In Progress | pass |
| 42 | blocked label on → Blocked; claim refused | agent command (`gh issue edit 16 --add-label blocked`) | `pf sync` → `write: …#16 Status: In Progress -> Blocked`; `pf start R#16` → `stop: not-ready: …#16 is Blocked` | Blocked | pass |
| 43 | blocked label off → derived again | agent command (`--remove-label blocked`) | `pf sync` → `write: …#16 Status: Blocked -> In Progress` | In Progress | pass |
| 44 | merge with the closing keyword → Done | agent command (`gh pr ready 20`, `pf sync`, `gh pr merge 20 --squash`, `pf sync`) | `write: …#16 Status: In Progress -> In Review`; after the merge `write: …#16 Status: In Review -> Done`, `milestone 'M2 e2e' in … is complete: …` | Done | pass |
| 45 | Blocked set by hand without the label is undone | agent command (`gh project item-edit` Status Blocked on #8) | `pf sync` → `write: …#8 Status: Blocked -> Ready` | Ready | pass |
| 46 | `followup` | agent command | `pf followup --parent R#21 --title "pf e2e followup one" --body-file <file> --pr https://github.com/R/pull/20 --change-dir openspec/changes/e2e --found-at "verification step 8.3"` → `created: #22`, `write: …#22 sub-issue of …#21`, `write: …#22 milestone: M1 scratch`, `skipped add_blocked_by: no blockers given`, `skipped add_project_item: already on the project`, `write: …#22 Implementation order: 15.1` | order 15.1 (parent 15; the next non-descendant value 20.5) | pass |
| 47 | `followup` interrupted, then `--resume` | agent command | the same with `--title "pf e2e followup two" --blocker R#19`, SIGKILLed right after `created: #23` (no parent, blocker or item yet); then the same arguments plus `--resume 23` → `resuming: …#23`, `sub-issue of …#21`, `milestone: M1 scratch`, `blocked by …#19`, `skipped add_project_item: already on the project`, `Implementation order: 15.2` | order 15.2 | pass |
| 48 | a second `--resume` is a no-op | agent command | the same `--resume 23` → five `skipped …` lines (`already a sub-issue`, `milestone already 'M1 scratch'`, `every blocker already linked`, `already on the project`, `'Implementation order' already set`), exit 0 | — | pass |
| 49 | `--resume` with a disagreeing parent | agent command | the same with `--parent R#5 --resume 23` → `stop: resume-disagrees: …#23: it is a sub-issue of …#21, not of …#5; nothing written`, exit 3 | — | pass |
| 50 | `next` normal pick | agent command | `pf next` → `next: …#21 order=15`, exit 0 | — | pass |
| 51 | `next` tie | agent command (#22's order set to 15) | `pf next` → `stop: tie: …#21 and …#22 share the lowest 'Implementation order' value 15`, exit 3 | — | pass |
| 52 | `next` missing-order, and `--skip-unordered` | agent command (new Ready #24 without an order) | `pf next --skip-unordered` → `skipped …#24: no 'Implementation order' value`, `next: …#21 order=15`; `pf next` → `stop: missing-order: Ready, unassigned items without a 'Implementation order' value: …#24`, exit 3 | — | pass (an earlier `pf next` right after `gh project item-add` did not see #24 yet: observation 3) |
| 53 | `next` with an empty Ready set | agent command (blocked label on #6, #8, #21, #22) | `pf next` → `write: …#6 Status: Ready -> Blocked` (and #8, #21, #22), `stop: nothing-ready: no Ready, unassigned open issue on the project`, exit 3 | — | pass |
| 54 | ready for review → In Review on the first sync (second lag observation) | agent command (`pf start R#22`; draft PR #25; `gh pr edit` with `Closes #22` + `gh pr ready`, 3.4 s; `pf sync` at once) | `write: …#22 Status: In Progress -> In Review`; PR #25 then had milestone `M1 scratch` | In Review | pass |
| 55 | a PR closed unmerged | agent command (`gh pr close 25`) | `pf sync` → `write: …#22 Status: In Review -> In Progress` | In Progress | pass |
| 56 | closes → Done | agent command (#22, #23, #21, #19 closed as not planned) | `pf sync` → `write: …#19 Status: Backlog -> Done`, `…#21 Ready -> Done`, `…#22 In Progress -> Done`, `…#23 Backlog -> Done` | Done | pass |
| 57 | idempotence | agent command | `pf sync` → `no changes written`, `sync: complete` | — | pass |
| 58 | gated command after `PROJECT_URL` was unset | agent command | `pf next` → `stop: not-enabled: PROJECT_URL is not set; …`, exit 3 | — | pass |
| 59 | refused claim (PAT 2) | agent command (PAT 2) | `pf start R#31` → `stop: not-ready: …#31 is Backlog: blocker …#30 closed as not planned`, exit 3 | Backlog | pass |
| 60 | claim (PAT 2) | agent command (PAT 2) | `pf start R#28` → `write: …#28 assignees: +raidenyn-shadow`, `write: …#28 Status: Ready -> In Progress`, `started: …#28` | In Progress | pass |
| 61 | `followup` with a blocker and the script's own add-to-project (PAT 2) | agent command (PAT 2) | `pf followup --parent R#33 --title "pf action followup" --body-file <file> --blocker R#31 --pr https://github.com/R/pull/32 --change-dir openspec/changes/e2e --found-at "verification step 8.3"` → `created: #34`, `sub-issue of …#33`, `skipped set_milestone: …#33 has no milestone`, `blocked by …#31`, `added to the project`, `Implementation order: 70.1` | order 70.1 | pass |
| 62 | `next` (PAT 2) | agent command (PAT 2) | `pf next` → `write: …#33 Status: (none) -> Ready`, `write: …#34 Status: (none) -> Backlog`, `next: …#6 order=20.5` | — | pass |
| 63 | a token without `repo` on a private repository (PAT 2) | agent command (PAT 2) | before `15bd1b6`: `pf sync --dry-run` → `stop: not-enabled: PROJECT_URL is not set; …` although it was set (defect 2); after: `stop: missing-scope: run gh auth refresh -s repo`, exit 3 | stops, writes nothing | pass after the fix (defect 2) |

## Observations

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

Scratch repository `raidenyn-shadow/scratch-project-flow` and user project 2 of `raidenyn-shadow`. All 63
scenarios, each with its run URL or command output, are in
`openspec/changes/add-github-project-workflow/verification-notes.md`: 26 executed by the real Action, 37 by
agent commands on real GitHub; 0 failed (61 pass, 2 Action observations exposed defects).

Through the real Action (classic PAT of the project owner, `project` + `public_repo`, repository
temporarily public): no-op with `PROJECT_URL` unset and with the secret absent, on real events; Ready →
Backlog on a native blocker add (dispatch: GitHub emits no dependency event); Backlog → Ready on a completed
blocker close; a not-planned blocker keeps Backlog with a report; a draft PR changes nothing; ready → In
Review; back to draft → In Progress; the blocked label on and off; a merge with `Closes #` → Done, written by
the Action; the milestone copy onto an open PR and onto a PR opened and merged between runs; the
empty-milestone report; idempotence; a burst of events; the default branch checked out on dispatch.

Through agent commands: the same Status transitions with `sync`; `start` claims and refused claims;
`followup` (15 → 15.1, 70.1), an interrupted one completed by `--resume` (15.2), a no-op resume and
`resume-disagrees`; `next` normal pick, tie, missing-order and an empty Ready set; `check`. The closing
keyword was linked by the first `sync` each time (3/3).

Not verified: a `repo`-scoped PAT running the workflow on a private repository, fine-grained tokens,
fork-PR trust, the schedule payload, a blocker invisible to the token, a GraphQL missing-scope text.
Fixed during the run: the unreadable-project error names both owner reads; a repository the token cannot
see is no longer reported as an unset `PROJECT_URL`. Open: a token that loses repository access leaves the
workflow green with `sync: partial`.
```
