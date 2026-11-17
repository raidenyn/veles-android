# Verification notes: add-github-project-workflow (tasks 8.1–8.4)

Real-environment run on 2026-11-17, from the worktree at `11ce2f0` (then `53c8cab`, see *Defects found*).

- Scratch repository: `raidenyn-shadow/scratch-project-flow` (private, default branch `main`).
- Scratch project: user project 2 of `raidenyn-shadow` (fields Status [Backlog, Ready, In Progress, In
  Review, Blocked, Done] and the number field "Implementation order"; built-in workflows: *Item closed*
  disabled, *Auto-add sub-issues to project* enabled, no *Auto-add to project*).
- Agent side: `python3 -I scripts/project_flow.py` from the worktree with the local `gh` login of the
  project owner (classic token, scopes `project`, `repo`, `read:org`, `gist`, `admin:public_key`).
- Action side: `.github/workflows/project-flow.yml` copied byte-identical onto the scratch `main`.

## Outcome in one paragraph

Every derivation, claim, follow-up and `next` scenario passed on real GitHub through the **agent-mode**
`sync`. The **Action could not run a sync**: the `PROJECT_TOKEN` provided for the test belongs to another
account than the project owner and carries `project, public_repo, repo:status, repo_deployment` — not
`repo`. It cannot see the private scratch repository or the owner's private user project, so the first
configured Action run failed visibly (`stop: incomplete-read`, exit 3). The secret was then deleted, and
every later event ran the workflow as a configured-off no-op. The Action-written transitions are therefore
**not verified** and need a re-run with a token of the project owner (`project` + `repo`).

## 8.1 Setup

| Step | Result |
| --- | --- |
| Clone into the scratchpad; copy `scripts/project_flow.py`, `.github/project-flow.json`, `.github/workflows/project-flow.yml` from `HEAD`; push to `main` | done (script SHA-256 equal to `HEAD`'s); re-copied after the fix in `53c8cab` |
| Blocked label | created `blocked` in the scratch repository (the guide's setup step 1) |
| `PROJECT_TOKEN` secret | set from the secret file over stdin; deleted again after the failed run (see Outcome) |
| `check --project` before enabling | `fact: built-in workflow 'Item closed' is disabled; enable it in the project UI (Workflows)`, `warning: consider enabling Auto-add to project …`, `stop: setup-incomplete: 1 setup facts need attention`, exit 3 — the expected setup fact; not worked around |
| Agent command before enabling | `sync` → `stop: not-enabled: PROJECT_URL is not set; …`, exit 3 |
| `PROJECT_URL` set last | set after the check and after the "merged between runs" scenario; first `sync --dry-run` printed the expected writes, `sync: complete` |

## 8.3 Transitions

Run URLs are under `https://github.com/raidenyn-shadow/scratch-project-flow/actions/runs/`. "agent sync" is
`project_flow.py sync --project <URL> --repo <scratch repo>` (agent mode) run right after the trigger; the
real event's Action run was a no-op (see the Action rows), so every Status below was written by the agent
sync, never by the Action and never by a built-in workflow.

| Transition | How triggered | Run / command output | Observed status | Pass/fail |
| --- | --- | --- | --- | --- |
| Ready → Backlog on blocker add | native `POST …/issues/16/dependencies/blocked_by` (no GitHub event exists for it), then agent sync | `write: …#16 Status: Ready -> Backlog` | Backlog | pass (agent) |
| refused Backlog claim | `start <repo>#16` | `stop: not-ready: …#16 is Backlog: open blockers …#17`, exit 3 | Backlog unchanged | pass |
| Backlog → Ready on blocker closed completed | `gh issue close 17 --reason completed` (real `issues.closed`; run no-op), agent sync | `write: …#16 Status: Backlog -> Ready`, `write: …#17 Status: Ready -> Done` | Ready | pass (agent) |
| blocker closed as not planned keeps Backlog + report | `gh issue close 18 --reason "not planned"`, agent sync | `…#19: blocker …#18 closed as not planned`; no write for #19 | Backlog | pass (agent) |
| refused claim behind a not-planned blocker | `start <repo>#19` | `stop: not-ready: …#19 is Backlog: blocker …#18 closed as not planned` | Backlog | pass |
| claim | `start <repo>#16` (agent mode, `--repo`) | `write: …#16 assignees: +<login>`, `write: …#16 Status: Ready -> In Progress`, `started: …#16` | In Progress, assigned | pass |
| draft PR without keyword | `gh pr create --draft` (#20, same-repo branch) | agent sync: no write | In Progress | pass (agent) |
| draft → ready → In Review | `gh pr edit 20 --body-file` (`Closes #16`) + `gh pr ready 20`, agent sync started < 1 s later | `write: …#16 Status: In Progress -> In Review`, `write: …#20 milestone: M2 e2e` | In Review | pass (agent) |
| back to draft → In Progress | `gh pr ready 20 --undo` (real `converted_to_draft`; run no-op), agent sync | `write: …#16 Status: In Review -> In Progress` | In Progress | pass (agent) |
| blocked label on | `gh issue edit 16 --add-label blocked`, agent sync | `write: …#16 Status: In Progress -> Blocked`; `start` then refuses: `…#16 is Blocked` | Blocked | pass (agent) |
| blocked label off | `--remove-label blocked`, agent sync | `write: …#16 Status: Blocked -> In Progress` | In Progress (derived again) | pass (agent) |
| Blocked set by hand without the label is undone | `gh project item-edit` Status Blocked on #8, agent sync | `write: …#8 Status: Blocked -> Ready` | Ready | pass (agent) |
| merge with the closing keyword → Done | `gh pr ready 20` (In Review), `gh pr merge 20 --squash` → #16 `CLOSED/COMPLETED`, agent sync | `write: …#16 Status: In Review -> Done` — written by the **agent sync**; *Item closed* is disabled and the Action was a no-op | Done | pass (agent); Action write not verified |
| milestone copy onto an open PR | #16 has milestone `M2 e2e`; PR #20 opened without one | `write: …#20 milestone: M2 e2e`; same for PR #25 (`M1 scratch`) | PR milestone set | pass (agent) |
| milestone copy onto a PR opened and merged between runs | `PROJECT_URL` unset; PR #15 (`Closes #14`, #14 in `M2 e2e`) opened and merged; then `PROJECT_URL` set and agent sync | `write: …#14 Status: (none) -> Done`, `write: …#15 milestone: M2 e2e` | merged PR got the milestone | pass (agent) |
| empty-milestone report | after #14 (and later #16) closed | `milestone 'M2 e2e' in … is complete: no open issues; not closed` (disappeared while #16 was open in `M2 e2e`, back after its merge) | milestone left open | pass |
| PR closed unmerged | `gh pr close 25` on a ready PR closing #22 (assigned), agent sync | `write: …#22 Status: In Review -> In Progress` | In Progress | pass (agent) |
| `followup` | `followup --parent <repo>#21 (order 15) --title … --pr <PR URL> …` | `created: #22`, sub-issue, `milestone: M1 scratch`, `skipped add_project_item: already on the project` (the project's *Auto-add sub-issues* added it), `Implementation order: 15.1` | order 15.1 | pass |
| `followup` interrupted, then `--resume` | second follow-up with `--blocker <repo>#19`, the process killed (SIGKILL) right after `created: #23`; then the same arguments plus `--resume 23` | resume: sub-issue, milestone, `blocked by …#19`, `already on the project`, `Implementation order: 15.2`; a second resume skipped all five steps; a resume with another `--parent` → `stop: resume-disagrees: … it is a sub-issue of …#21, not of …#5; nothing written` | order 15.2 | pass (the `missing:` lines of an in-process failure were not produced: the run was killed) |
| order allocation 15 → 15.1 → 15.2 | as above, next value above 15 held by a non-descendant: 20.5 | 15.1, then 15.2 | — | pass |
| `next` normal pick | `next` | `next: …#21 order=15` | — | pass |
| `next` tie | #22 order set to 15 | `stop: tie: …#21 and …#22 share the lowest 'Implementation order' value 15` | — | pass |
| `next` missing-order | new Ready issue #24 without an order | `stop: missing-order: Ready, unassigned items without a 'Implementation order' value: …#24`; `--skip-unordered` → `skipped …#24: no 'Implementation order' value`, `next: …#21 order=15` | — | pass (see observation 3) |
| `next` empty Ready set | blocked label on every Ready item | `stop: nothing-ready: no Ready, unassigned open issue on the project` | — | pass (labels removed; Statuses derived back to Ready) |
| sync idempotence | a sync with nothing changed | `no changes written`, `sync: complete` | — | pass |
| Action no-op, `PROJECT_URL` unset | `workflow_dispatch` 44117276392; real events 44117452172 (PR opened), 44117468873 (PR closed), 44117477728 (issue closed) | `::notice::project-flow not configured`; checkout and reconcile steps skipped | success | pass |
| Action no-op, secret absent | every real event after the secret was deleted, e.g. 44118620968 (draft PR opened), 44118658159 (`ready_for_review`), 44118720473 (`converted_to_draft`), 44118836438 (PR closed), 44118472146 (issue closed) | same notice; checkout and reconcile skipped | success | pass |
| Action with a token that cannot see the project | `workflow_dispatch` 44117634152 | checkout `ref: main`; `stop: incomplete-read: cannot read project raidenyn-shadow/2 as a user or an organization: project fields: gh: Could not resolve to an Organization …`, exit 3 | failure (visible) | pass as "fails visibly"; message defect, see below |
| Action-written transitions (Backlog ↔ Ready, In Review, Blocked, Done, milestone copy) | — | — | — | **not verified**: no usable `PROJECT_TOKEN` |

### Observations

1. **Closing-keyword lag (UNVERIFIED (c))**: twice (`gh pr edit` + `gh pr ready`, then `sync` started at
   once — about 3.4 s after the edit began on the second try) the very first `sync` already read the closing
   reference and wrote In Review. No second `sync` was needed in these two observations; the skill's one
   retry stays as a guard.
2. **Event coverage**: real `pull_request_target` runs fired for `opened`, `ready_for_review`,
   `converted_to_draft` and `closed`, and `issues` runs for `closed`, `labeled`, `unlabeled`, `assigned`,
   `unassigned`. Adding a native dependency fired nothing (as designed: daily run or dispatch). The body edit
   fired nothing (no `edited` trigger, as designed).
3. **Read-after-write lag on project items**: a `next` run a second or two after `gh project item-add` did
   not see the new item yet (it picked #21 instead of stopping with `missing-order`); the next run saw it. A
   snapshot-consistent selection, as `next` documents, but worth knowing when an item was just added.
4. **Concurrency**: in bursts of events the `project-flow` group cancels the superseded *pending* runs (5 of
   the 34 runs `cancelled`); the surviving run reconciles everything, so nothing is lost, but the Actions list
   shows cancelled runs.
5. **Checkout ref (UNVERIFIED (d))**: on `workflow_dispatch` the checkout step resolved
   `github.event.repository.default_branch` to `main` (`git checkout --progress --force -B main
   refs/remotes/origin/main`). The `schedule` payload was not observed (no 04:23 UTC run in the window).

## 8.2 Token permissions

| Operation | Endpoint | Agent login (classic, `project` + `repo` + others) | Test `PROJECT_TOKEN` (other account; `project, public_repo, repo:status, repo_deployment`) |
| --- | --- | --- | --- |
| read the project (fields, workflows, items) | GraphQL `user.projectV2` | works | fails: `NOT_FOUND` "Could not resolve to a ProjectV2 with the number 2." (the project is private to its owner) |
| write an item field (Status, order) | GraphQL `updateProjectV2ItemFieldValue` | works | not reached |
| add an item | GraphQL `addProjectV2ItemById` | works (followup when not auto-added: dry run only; the real runs found the item auto-added) | not reached |
| read the repository / an issue / milestones | REST `GET repos/…`, `…/issues/<n>`, `…/milestones` | works; `X-Accepted-OAuth-Scopes: repo` on the issue and milestone reads | fails: `Not Found (HTTP 404)` plus gh's hint `This API operation needs the "repo" scope. To request it, run:  gh auth refresh -h github.com -s repo` |
| assign (claim) | REST `POST …/issues/<n>/assignees` | works | not reached |
| PR milestone PATCH | REST `PATCH …/issues/<pr>` | works | not reached |
| sub-issue / dependency | REST `POST …/sub_issues`, `POST …/dependencies/blocked_by` | works | not reached |
| read `PROJECT_URL` | REST `GET …/actions/variables/PROJECT_URL` | works | not used (the Action reads it from `vars`) |
| create an issue | REST `POST …/issues` | works | not reached |

Endpoints other than the issue and milestone reads returned an empty `X-Accepted-OAuth-Scopes`, so the
headers do not narrow the minimum further. Verified configuration: the agent with a classic token holding
`project` and `repo` (the extra scopes of the login were not removed, so their necessity is not tested). A
working `PROJECT_TOKEN` is **not verified**.

## Not verified

- Every Status or milestone written **by the Action** (no `PROJECT_TOKEN` that can see the project was
  available; the provided token belongs to another account and lacks `repo`).
- Fine-grained tokens (none was provided).
- (a) The real text of a GraphQL `INSUFFICIENT_SCOPES` error. Observed only the REST hint above, which
  `map_gh_error` matches (`needs the "repo" scope`). A project the token cannot see answers `NOT_FOUND`, not a
  scope error, so it maps to `incomplete-read`, not `missing-scope`.
- (b) A blocker the token cannot see (`totalCount` vs `nodes`): no private blocker of another owner was
  available.
- (d) The `schedule` payload's `default_branch` fallback (no scheduled run observed).
- (e) Fork-PR trust: no second account to open a fork PR.
- The `missing:` lines of an in-process `followup` failure (the resume was exercised after a kill instead).
- `start`'s `collision` and `assignment-failed` stops (single account).

## Defects found

1. **Fixed — misleading error when the project cannot be read** (`53c8cab`, `fix(scripts): name both owner
   errors when a project cannot be read`). Evidence: run 44117634152 printed only the organization fallback's
   error (`Could not resolve to an Organization …`), hiding the real cause (`Could not resolve to a
   ProjectV2 with the number 2.` as a user). Fix: `read_board` keeps the user read's error and the stop now
   reads `cannot read project <owner>/<n>; as a user: …; as an organization: …`; regression test
   `test_both_owner_reads_failing_name_both_reasons`. Re-copied to the scratch repository; the Action
   re-run is blocked by the token.
2. **Setup input, not code — the provided `PROJECT_TOKEN`**: it is a classic token of another account with
   `project, public_repo, repo:status, repo_deployment`, not `project` + `repo` of the project owner.
   Proposed: issue a classic token from the project owner's account with `project` and `repo` and re-run the
   Action rows. The token should be revoked.

## Scratch artifacts

| Artifact | State left |
| --- | --- |
| Commits on scratch `main`: the three copied files (from `11ce2f0`), a throwaway diagnostic workflow added and removed (it printed the token's login and scopes, never the token), the re-copied script (`53c8cab`), two squash merges (PRs #15, #20) | kept |
| Label `blocked`, milestone `M2 e2e` (open) | kept |
| Issues #14, #16 (closed completed by merges); #17 (closed completed); #18, #19, #21, #22, #23, #24 (closed not planned) | closed, on the project, Done |
| PRs #15, #20 (merged), #25 (closed unmerged); branches deleted | closed |
| Fixture items written by sync: #4 → Done, #5 → In Review, #6 → Ready; PRs #9, #10 got milestone `M1 scratch` | kept (the fixtures were recorded before; a re-recording sees these values) |
| `PROJECT_TOKEN` secret | deleted |
| `PROJECT_URL` variable | unset again (it was unset when the run started) |

## PR description block

```markdown
### Real-environment verification

Scratch repository `raidenyn-shadow/scratch-project-flow` (private) and user project 2 of
`raidenyn-shadow`; details in `openspec/changes/add-github-project-workflow/verification-notes.md`.

Passed on real GitHub with agent-mode `sync`/`start`/`followup`/`next` (classic token, `project` + `repo`):
Backlog ↔ Ready on a native blocker add and a completed close; a not-planned blocker keeps Backlog with a
report; `start` claim and a refused Backlog claim; draft PR → ready → In Review → draft → In Progress; the
blocked label on and off; a merge with `Closes #` → Done; the milestone copy, including a PR opened and
merged between runs; the empty-milestone report; `followup` (15 → 15.1) and an interrupted one completed by
`--resume` (15.2); `next` normal pick, tie, missing-order and an empty Ready set; the Action as a
successful no-op with `PROJECT_URL` unset and with the secret absent, on real events.

Not verified: every write made by the Action itself (the test token could not see the project), fine-grained
tokens, fork-PR trust, the schedule payload and a blocker invisible to the token.
Fixed during the run: the unreadable-project error now names both owner reads.
```
