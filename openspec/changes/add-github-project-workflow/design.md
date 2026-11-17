## Context

Tracked work (a session started from a GitHub Project, a view or an issue) is today run entirely by the agent
under `.agents/rules/repo-tracker-intake.md`, `.agents/rules/repo-superspec-closeout.md` and the *Next task*,
*tracker.md* and *Closeout* sections of `docs/superspec-workflow.md`. The agent picks the next task, sets In
Progress and In Review, keeps `tracker.md`, closes the issue and sets Done last, and reconciles merged work at
the next intake.

What the current board shows:

- The procedure spends most of its length recovering an order. The "Execution order" view has no sort field,
  so the agent falls back to board position, while the board also carries an `Implementation order` number
  field that the position does not match.
- Status has Backlog, Ready, In Progress, In Review, Blocked and Done; nothing is ever moved to Ready.
- Dependencies are partly native ("blocked by" links) and partly prose in issue bodies.
- The project's built-in workflows are off except auto-adding sub-issues, so Done depends on the agent.
- Milestones are used as component labels, which the `Area` field already covers.
- The board only stays correct while a session runs: an issue closed by hand never promotes its dependents.

The user wants the loop from pickup to merge to need as little human bookkeeping as possible, every artifact
(issue, PR, project item, milestone) cross-referenced the way GitHub recommends, and a setup that carries to
later projects without project-specific code.

## Goals / Non-Goals

**Goals:**

- Every status is a fact, not a judgment. It is derived from the issue state, the blocked label, the PRs,
  the assignees and the native blockers (D2), and the board reflects it without a session running.
- The next task is the Ready, unassigned item with the lowest order value; board position stops mattering.
- GitHub keeps every status current. The agent's only status act is the claim: assign, then In Progress.
- A PR closes its issue by closing keyword, carries the issue's milestone, and appears on the issue's project
  item through the built-in *Linked pull requests* field.
- Follow-up work found during a change is either finished in the change or filed as a fully linked issue.
- One deterministic implementation of the rules, shared by the GitHub Action and the agent.
- No project-specific identifiers in the repository; a new project needs settings, not code.

**Non-Goals:**

- Starting the next task automatically after a merge. Each task starts with a new session opened by the user;
  chaining is a separate, later change.
- Merging. The user merges.
- Creating, closing or choosing milestones. Planning owns them.
- Creating or renaming project fields or Status options.
- An MCP server. `gh` plus a repository script is the interface.

**Anti-goals** (failure even if it works):

- Automation that overrides a fact the user set: the blocked label, an assignment, a closed issue, a draft PR.
- Silent guesses: a tie, a missing order value or an ambiguous scope is reported, never resolved by guessing.
- Anything that needs this board's ids, number or owner written into a committed file.

## Decisions

### D1. Split of responsibility (approach B with In Review in the Action)

| Transition | Owner | Trigger |
| --- | --- | --- |
| added to project | built-in *Auto-add to project* | issue created in a linked repository |
| Backlog ↔ Ready | Action (and the agent at intake) | issue events (D8), schedule, manual run, intake |
| claim (assign) → In Progress | agent claims; Action and `sync` keep it derived | change created and `tracker.md` written |
| In Progress ↔ In Review | Action | PR opened, reopened, ready for review, converted to draft, closed (not edited; see D8) |
| → Done | built-in *Item closed*; `sync` repairs | the issue closes (the merged PR's closing keyword closes it) |
| Blocked | human or agent adds the **blocked label**; status follows | label added or removed |

Rejected alternatives are recorded in `brainstorm.md`. Claiming an issue means assigning it, and the
setup guide states that assignment means "started" on a board that uses this flow.

### D2. Status rules

Status options are matched **by name** through a role map (D6). For an item whose content is an issue, the
status is derived **only from facts on GitHub**, never from the previous Status. The first matching rule wins:

0. The issue is closed → Done. This repairs a stale write and agrees with the built-in *Item closed*.
1. The issue carries the **blocked label** (name in the config, default `blocked`) → Blocked. "Blocked" means
   work that started and got stuck on something outside the board. Adding the label is the way to set it, so
   the fact survives any concurrent write. A Blocked status without the label is reported by `check`, and the
   setup guide says to use the label.
2. At least one **open, non-draft** PR closes the issue → In Review.
3. Otherwise, the issue has an **assignee** → In Progress. Assignment is the claim, as GitHub's own convention
   has it ("assign yourself when you start").
4. Otherwise, every native blocker is closed as completed (or there are none) → Ready, else → Backlog.

Every status is therefore a function of facts on GitHub: the issue state, the label, the PRs, the assignees
and the blockers. A stale write is always undone by the next run.

An In Progress item with no assignee, and a Blocked item without the label, fall back to the derived status.
`check` reports both during setup (D12).

A blocker counts as closed only when it was closed as **completed**. A blocker closed as *not planned* or
*duplicate* keeps the item in Backlog and is reported ("remove the link or re-point it"), because silently
promoting work whose prerequisite was abandoned is a guess. *(Builder call.)*

**Repository scope** *(builder call):* the Action manages only items whose issue lives in its own repository,
while it reads blockers from any repository. A project that spans repositories installs the Action in each one;
the item sets are disjoint between Actions. The agent's `sync` at intake covers all items of the project.

Draft issues and PR items on the board are ignored. A write is
made only when the derived status differs, so every run is idempotent.

**Concurrent writers.** GitHub Projects has no compare-and-set, so the design does not try to make status
writes exclusive. It makes them **convergent** instead:

- Status is derived from the facts listed above, never from the previous Status.
- `start` claims by **assigning first**, using the add-assignees operation, which never replaces existing assignees. Then it writes In Progress.
- A stale `sync` that writes Ready over a just-claimed item leaves the assignee in place. The next run
  (`start`'s own follow-up `sync`, any Action event, the daily schedule, or the next intake) derives In
  Progress again.
- `next` never selects an assigned item, even if its Status still says Ready. So a stale Ready cannot cause a
  second pickup.
- Each write is still skipped when a fresh read shows that its inputs changed, which keeps churn low, but
  correctness does not depend on it.
- **Claims by different accounts:** `start` re-reads the assignees after assigning. If any other login is
  assigned, it reports a collision and stops to ask. Each `start` re-reads after its own write, so the later
  of two concurrent claims always sees both and stops; at most one proceeds.
- **Two sessions of the same account claiming the same item at the same moment are not detected.** The
  assignee is the same login. This is acceptable because every session is started by the user (the
  automatic next task is a non-goal). It is recorded as a prerequisite for the later automatic-chaining
  change, which needs an exclusive claim.
- The unit tests drive the interleavings with a transport that changes the facts between reads and writes,
  including a stale write landing over Blocked or Done, and two accounts claiming at once:
  - a stale Ready landing after `start` has read its own write back, which converges on the next `sync`;
  - Backlog demotion racing a claim;
  - a PR event racing a `sync`.

### D3. Next-task selection

The next task is the Ready, **unassigned** open-issue item with the **lowest order value** (D6). Selection first runs `sync`
(the full D2 derivation) so it never reads a stale board. It stops and reports instead of choosing when:

- the project is not the enabled one (D12): the given project URL differs from the repository variable
  `PROJECT_URL`, or the variable is unset;
- nothing is Ready;
- the lowest order value is shared by several Ready, unassigned items;
- a Ready item has no order value (it is reported and skipped; selection continues only if the user agrees);
- the items could not be read to the last page.

When the user names a task, it is used if it is Ready; if it is Backlog, In Progress (and not a resume
recorded in an active `tracker.md`), In Review, Blocked or Done, the agent says which and asks. Views,
view filters and board position are no longer read. This replaces steps 3–5 of today's *Next task*.

### D4. Cross-references

- The PR body contains a closing keyword for each issue it completes (`Closes #<n>`, or
  `Closes <owner>/<repo>#<n>` across repositories). This links the PR to the issue, fills the item's
  *Linked pull requests* field, and closes the issue on merge. PRs are **not** added as project items, so a
  task has one card.
- The Action copies the issue's milestone onto the PR when the PR has none. If the closed issues carry
  different milestones, or a same-named milestone is missing in the PR's repository, it reports and sets
  nothing.
- Follow-up issues are sub-issues of the originating issue (D7).
- **Empty milestones:** `sync` reads the open milestones of the repository. One with no open issues and at
  least one closed issue on the project is reported in the job summary and in the agent's output. It is never
  closed. Because this is part of `sync`, it also catches issues closed without a PR.

### D5. One script, two callers

A stdlib-only Python script, `scripts/project_flow.py`, holds every rule above and talks to GitHub through
`gh api` (GraphQL for projects, REST for issue dependencies and sub-issues). Subcommands:

| Subcommand | Caller | Effect |
| --- | --- | --- |
| `sync` | Action, agent | the **complete reconciliation**: the full D2 derivation (rules 0–4, first match wins) over every item, the milestone copy for every PR, open or merged, that closes an issue on the project (D4), so a PR that opened and merged between surviving runs is still covered, and the empty-milestone report. A missed event, a removed closing keyword or a dropped run is repaired by the next run |
| `next` | agent | `sync`, then D3; prints the chosen issue or the stop reason |
| `start <issue>` | agent | assign the issue to the authenticated user, set In Progress, then run `sync` for that item (refuses, after a fresh derivation, any item that is not Ready: Backlog, assigned, Blocked, Done or In Review. `--force` means the user explicitly authorized it, and the skill passes it only after asking) |
| `followup ...` | agent | create a follow-up issue per D7 |
| `check` | agent, CI | without a project: validate the config file (CI, no network). With a project URL: report the setup facts and the heuristic warnings (D12) |

Every writing subcommand supports `--dry-run`, prints what it did or would do, and exits non-zero with a
named reason on any stop case.

**Complete reads, fail closed.** Every paginated connection is followed to its last page: project items,
blockers, closing PRs, sub-issues at every depth, assignees, labels and milestones. If any read for an item
fails or comes back partial, that item gets **no write** and is reported. `next` then stops instead of
choosing, and `followup` stops instead of allocating an order value. A wrong Ready or a wrong order is worse
than a late one. Network access goes through one injectable transport so the tests run on
recorded fixtures with no network and no wall clock.

### D6. Configuration without identifiers

- **Committed** `.github/project-flow.json`: the Status field name, the role → option-name map (`backlog`,
  `ready`, `in_progress`, `in_review`, `blocked`, `done`), the order field name and the blocked label name. Names only, no ids,
  numbers or owners. Defaults match the current board (`Status`, `Implementation order`).
- **Repository variable** `PROJECT_URL`: the project the Action serves. The agent gets the project from the
  user's session as today. Moving to a new project means changing the variable and, if its names differ,
  the JSON file.
- **Repository secret** `PROJECT_TOKEN`: a token that can write the user-owned project and read and edit
  the repository's issues and PRs. The default `GITHUB_TOKEN` cannot write a user-owned project. The setup
  guide states the minimum permissions.
- `PROJECT_URL` is also the enable switch (D12). The agent reads it with
  `gh api repos/<owner>/<repo>/actions/variables/PROJECT_URL`, which needs the variables read permission.
  If the read fails, the agent reports it with the fix and does not guess.
- One project per repository at a time for the Action. Several concurrent projects are out of scope.

### D7. Follow-up work during a change

- Finish first whatever does not depend on an open decision.
- Work that does not change the approved planning files beyond what the review gate treats as minor, and
  needs no new brainstorm, is done in the same change.
- Anything larger becomes a new issue through `project_flow.py followup`:
  - a sub-issue of the originating issue;
  - the originating issue's milestone;
  - native "blocked by" links only to issues it genuinely waits on, never to its parent;
  - added to the project, with an order value computed as below;
  - a body linking the PR, the change directory and the exact place the work was found.
- The PR body lists every follow-up issue it created.

**Order value of a follow-up** *(builder call):*

A follow-up goes **after its parent's existing follow-ups** and before the next unrelated item.

- Let `p` be the parent's order value.
- Let `n` be the lowest order value above `p` held by an item that is **not** a sub-issue, at any depth, of
  the parent. If there is none, `n = p + 1`.
- Take `v`, the largest order value in `[p, n)`. That is the parent itself or one of its follow-ups.
- The new value is the first of `v + 0.1`, `v + 0.01` and `v + 0.001` that is strictly below `n` and not
  already used.
- Examples:
  - **Siblings:** the first follow-up of 15 gets 15.1 (`n` = 16, `v` = 15), and the second gets 15.2 (`v` =
    15.1).
  - **A follow-up of a follow-up:** a follow-up of 15.1 while its sibling 15.2 exists gets 15.11 (`n` = 15.2,
    `v` = 15.1, and 15.2 is not below `n`).
- If the parent has no order value, or none of the three steps fits, stop and ask the user.
- Two follow-ups allocated at the same moment can get the same value. `next` treats that as a tie and stops,
  so the clash is visible and never silently resolved.

**Resumable creation.** `followup` makes several writes:
1. create the issue;
2. attach it to its parent;
3. set the milestone;
4. add the blockers;
5. add it to the project;
6. set the order value.

It prints the new issue number as soon as step 1 succeeds. On any later failure it stops and names the
missing steps. `followup --resume <issue>` then completes them. It takes the **same arguments as the
original call** (parent, blockers, title and body), and the skill re-issues the exact command line it ran.
Nothing is persisted.

Each step checks whether it is already done before writing, so a retry never creates a second issue and never
duplicates a link. If an existing link disagrees with the arguments, `--resume` stops, reports it and changes
nothing. Examples: the issue already has a different parent, or a different milestone.
- Values are compared after rounding to 3 decimals.
- When size or scope is unclear, the agent asks the user.

### D8. The Action

`.github/workflows/project-flow.yml`:

- **Triggers:**
  - `issues` (closed, reopened, labeled, unlabeled, assigned, unassigned);
  - `pull_request_target` (opened, reopened, ready_for_review, converted_to_draft, closed);
  - `schedule` (daily, a safety net because GitHub emits no event for dependency changes);
  - `workflow_dispatch`.
- **No `edited` trigger.** The `commit-conventions` capability allows only `pr-title.yml` to trigger on
  `edited`. A closing keyword added or removed by a later body edit is picked up by the next PR or issue
  event, or by the daily `sync`. The agent writes the closing keyword when it opens the PR, so the normal path
  does not depend on edits.
- **Fork safety:** `pull_request_target` gives the job the secret, so the job checks out the **base**
  branch only and never runs PR code. It needs only PR metadata.
- **Runs:** every event runs the same `sync`, the complete reconciliation (D5), and writes a job summary.
  Nothing depends on which event fired.
- **Concurrency:** one group with `cancel-in-progress: false`. GitHub keeps at most one pending run per
  group and replaces an older pending run with a newer one, so events can be dropped during a burst. That is
  safe only because every run reconciles everything. The tests include a dropped-PR-event case: the PR's
  milestone is still copied by the surviving run.
- **No-op without setup:** with no `PROJECT_URL` or `PROJECT_TOKEN`, it exits successfully with a notice, so
  forks and unconfigured clones stay green.
- **Not a required check.**
- **Ownership:** owned by the `tooling` node in `.github/components.json`, which already owns `scripts/`. The
  tests live under `scripts/tests/` and run with `./gradlew scriptsTest`. The workflow is also added to the
  contract suites and the scanner scopes (`ci-pipeline`).

### D9. Draft discipline in the superspec workflow

A pre-review PR (planning review before implementation) is opened as a **draft**; finalize marks the PR ready
for review, which moves the card to In Review through the Action. Finalize no longer writes In Review itself;
it confirms the transition and reports if it did not happen.

The repository's `superspec` schema is already a repository-owned fork that carries tracker steps (finalize
sets In Review today). This change edits it directly:

- `openspec/schemas/superspec/schema.yaml` and `INTEGRATION.md` for the draft and confirm-not-write steps;
- the spec deltas for `superspec-tracker-intake` and `superspec-closeout`.

The new divergence is recorded in the fork list in `docs/AGENTS.md`. *(Builder call.)*

### D10. Agent packaging

- **Skill** `.agents/skills/github-project/SKILL.md` (with the `.claude/skills/github-project` symlink) holds
  the "how" of each step. The rules themselves stay in the doc (see the split below). The steps:
  - precheck `gh` scopes;
  - intake with `next` or a named task, and a resume check against active `tracker.md` files;
  - write `tracker.md`, then `start`;
  - PR body conventions;
  - follow-up handling;
  - the post-merge check.
  The name does not start with `openspec-`, so `openspec update` leaves it alone, and the agent-file check
  must accept it.
- **Rules** stay short and always on. They keep the opening sentence that the `agent-guidance-layout`
  capability requires (read the cited sections of `docs/superspec-workflow.md`), so that capability does not
  change:
  - `repo-tracker-intake.md` still opens with the pointer to *Next task* and *tracker.md*. Its bullets
    shrink to the scope rule, "load the `github-project` skill to run it", the no-identifiers rule and the
    scope-error rule;
  - `repo-superspec-closeout.md` replaces "close the issue, Done last" with the post-merge check (D11).
- **Doc and skill split:**
  - **What:** `docs/superspec-workflow.md` *Next task* and *tracker.md* keep the normative rules, shortened to
    the derivation, the selection rule, the claim, the follow-up rule and `tracker.md`. The headings stay.
  - **How:** the skill holds the commands to run, the PR-body template, the `followup` invocation and the
    post-merge check. It cites the doc and never restates its rules.
- **Setup guide:** a new `docs/github-project-setup.md` is the generic guide:
  - the fields and options a project needs;
  - the built-in workflows to enable;
  - the variable, the secret and its permissions;
  - how to convert prose dependencies to native links;
  - the milestone convention.
- **`tracker.md` stays** with the same fields. It is still the resume record and the issue-repository record.

### D11. After the merge

The closing keyword closes the issue and *Item closed* sets Done. Closeout then checks both:

- If the issue is still open, or the Status is not Done, the agent applies the old fallback (close the issue
  with a comment linking the PR, then Done last) and reports that the automation missed it.
- It then runs `sync` once so the newly unblocked items show as Ready in its final report.
- The archived-`tracker.md` reconcile at intake is removed; `sync` reports closed-but-not-Done items instead.

### D12. Applying it to the current board

The generic deliverables come first. Applying them to the current board is the last step of this change,
right after merge, done in the closeout with the user's go-ahead for each board write.

**No bootstrap exception is needed.** This change is not tracked work: the user named the board as an
example, not as the source of the task, so it has no `tracker.md` and finalize writes no board status. In
general, a project that is not enabled can never reach finalize through this flow, because `start` refuses
it. So "finalize confirms In Review" only ever runs against an enabled project. The D12 run is an explicit
post-merge step of this change's closeout, not a tracked transition.

**The enable switch is the repository variable `PROJECT_URL`.** It is set **last**, after the board is ready:
- the Action is a no-op without it;
- `next`, `sync` and `start` refuse, with a named reason, for any project other than the one it names;
- so a board whose prose dependencies are not yet converted can never be synced or picked from.

`check` reports what is missing on a board that is not yet set up. It splits its findings into two kinds.

**Facts** must be clean before enabling:
- Blocked items without the blocked label;
- the built-in workflow *Item closed* when it is missing or off, read from the project's
  `workflows { name enabled }` GraphQL connection (this query has been run against a real user-owned project).
  The *Auto-add to project* workflow is only an advisory hint, because enabling built-in workflows is possible
  only in the web UI and `sync` derives Done itself;
- the order field and the Status options named in the config;
- open items without an order value;
- In Progress items without an assignee.

**Heuristic warnings** are hints, not proof:
- open items whose body or comments mention "blocked by", "depends on", "after #" or an issue reference near
  such words without a matching native link.

A clean heuristic report does **not** prove the conversion is complete. The proof is the user: setting
`PROJECT_URL` is an explicit human attestation that the dependencies were reviewed, and the setup guide says
so. In this change's D12 run the agent proposes each conversion and the user approves it.

The old successor-of-last-completed procedure is not kept as a fallback. The steps, in order:

1. Enable the built-in workflows.
2. Add the secret.
3. Convert prose dependencies to native links.
4. Make sure every open item has an order value.
5. Run `check` until its facts are clean and the user has reviewed its warnings.
6. Set `PROJECT_URL`.
7. Run `sync`.

Re-planning the milestones is listed for the user, not automated.

### D13. Real-environment verification

`pull_request_target` and `schedule` run only from a default branch, so the Action cannot be exercised from
this feature branch. Verification therefore uses a **scratch repository and a scratch project under the user's
account**, with their own token:

- the workflow, the config and the script are copied there;
- the full loop is driven:
  - add and close blockers → Backlog ↔ Ready;
  - draft PR → ready → In Review, back to draft → In Progress;
  - merge → issue closed → Done;
  - milestone copy;
  - a follow-up via `followup`;
  - `next` on ties, missing order values and an empty Ready set.
- The real board and this repository's issues are not touched before merge.
- The PR states what ran, and where.

## Risks / Trade-offs

- [A new credential with write access to the board and the issues] → minimum permissions in the setup guide;
  the job never checks out or runs PR code; a no-op when the secret is absent. An expired token fails the
  Action visibly, never silently.
- [GitHub emits no event for dependency edits, so a blocker added to a Ready item shows up late] → the daily
  `sync`, the issue events in D8, and the `sync` that every intake runs before selecting.
- [Prose dependencies stop counting, so an issue whose only dependency is in text shows Ready] → `check`'s
  heuristic warnings, the setup guide, the user's attestation by setting `PROJECT_URL`, and the reviewed
  one-time conversion of the current board.
- [Draft discipline is load-bearing: a pre-review PR opened as ready-for-review flips its issue to In Review
  early] → the schema, the rule and the skill all open pre-review PRs as drafts, and the derivation reverses
  when the PR goes back to draft.
- [Closing keywords close the issue only on a merge into the default branch] → the post-merge check (D11)
  closes it and reports it.
- [Body edits do not trigger the Action, because of the `edited` restriction] → the next PR or issue event, or
  the daily `sync`; the agent writes the closing keyword when it opens the PR.
- [Same-account simultaneous claims go undetected] → accepted, because the user starts every session.
  Recorded as a prerequisite for automatic chaining.
- [Board users must change habits] → the setup guide states the conventions: assignment means started,
  Blocked is a label, order is a field (not card position), and pre-review PRs are drafts.
- [The dependency and sub-issue APIs are newer] → fixtures recorded from real responses, and the
  scratch-project run (D13).
- [Every event runs a full reconciliation, which costs API calls on large boards] → batched paginated GraphQL
  reads, writes only for changed items, and serialized runs that collapse bursts into one pending run. The
  job summary prints the GraphQL rate-limit cost, so growth is visible.

## Migration Plan

1. Land the generic deliverables:
   - the script and its tests;
   - the workflow, the config, the skill and the rule, doc and schema edits;
   - the setup guide;
   - the contract-suite and scanner updates;
   - `components.json`.
   With no `PROJECT_URL` set, the Action is a no-op.
2. Verify on the scratch repository and project (D13) before the PR leaves draft.
3. After merge, apply D12 to the current board with the user approving each write. `PROJECT_URL` is set last.

**Rollback:**
- Unsetting `PROJECT_URL` stops all automation at once: the Action becomes a no-op and the agent's commands
  refuse the project.
- Reverting the PR restores the old procedure.
- Board statuses written by the automation are plain field values and need no cleanup.

## Open Questions

- The minimum token type and permissions for writing a user-owned project, from the user's account: a
  classic PAT with `project`, or a fine-grained PAT if it supports user projects. To be settled empirically
  during D13 and written into the setup guide. Until then the guide names the classic PAT as the known-working
  option.
