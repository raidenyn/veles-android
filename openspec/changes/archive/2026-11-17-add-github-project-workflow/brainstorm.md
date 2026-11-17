## Design Summary

Tracked work moves from "the agent does all board bookkeeping" to "GitHub does the bookkeeping, the agent does
the judgment". The full design is in `design.md`; in short:

- **Ready means every blocker is closed.** Blockers are GitHub's native "blocked by" links. Open issues in
  Backlog/Ready move between the two automatically. Blocked is for started work stuck on something outside
  the board. It is set by adding the `blocked` label, so it survives any automatic write.
- **Next task** = the Ready item with the lowest order value (the `Implementation order` field by default).
  Board position and view filters stop mattering. Ties, missing order values and an empty Ready set stop and
  ask.
- **Who sets what:**
  - the agent claims the task (assigns it and sets In Progress) when the change is created;
  - a GitHub Action sets In Review when a non-draft PR that closes the issue is open, and back to In Progress
    when it goes to draft or closes unmerged;
  - the built-in project workflow *Item closed* sets Done when the merged PR's closing keyword closes the
    issue;
  - the same Action keeps Backlog/Ready in sync on issue events, daily, on demand, and at every agent intake;
  - every sync also re-derives In Progress and In Review from the PRs open at that moment, so a missed event or
    a removed closing keyword is repaired;
  - claiming a task means assigning it (GitHub's own convention), so In Progress is derived from the assignee.
    With no atomic writes on Projects, every status is computed from facts (PRs, assignees, blockers), and a
    stale write is repaired by the next sync instead of sticking. `next` never picks an assigned item, and
    `start` stops if someone else is assigned too. Simultaneous claims from one account are accepted as
    undetected, since the user starts every session.
- **Cross-references:**
  - PRs use closing keywords and are not added as project items (one card per task);
  - the Action copies the issue's milestone onto the PR;
  - follow-up issues are sub-issues.
- **Milestones** mark a demonstrable feature that can span components. Planning sets them on issues; the
  agent never creates, chooses or closes them, and the Action reports a milestone with no open issues left.
- **Follow-up work:** finish everything not blocked by an open decision first. Small work, meaning work that
  doesn't change the approved design beyond the review gate's minor threshold and needs no new brainstorm, is
  done in the same change. Larger work becomes a new linked issue: sub-issue, same milestone, real blockers
  only, order = after the parent's existing follow-ups (0.1, then 0.01 steps), and a body pointing to where it was found. When it's ambiguous, ask.
- **Scope of a session:** it ends after the merge and the board updates. The next task starts with a new
  session from the user; chaining is a later change.
- **Packaging:**
  - one stdlib Python script (`scripts/project_flow.py`) used by both the Action and the agent;
  - one skill (`github-project`) holding the procedure;
  - the rules shrink to short always-on constraints;
  - a generic setup guide;
  - configuration as names in a committed JSON file, with the project URL in a repository variable and the
    token in a secret, so no identifiers are committed.

## Alternatives Considered

### Alternative A: Agent does everything (tidied current procedure)
- **Approach**: Keep all board writes in the agent, packaged as rules plus a skill, recomputing Ready at
  intake and closeout.
- **Pros**: No secret, no CI; one place to read the behaviour.
- **Cons**: The board is only correct while a session runs; an issue closed by hand never promotes its
  dependents; every write depends on the agent following a long procedure.
- **Why not chosen**: Fails the "human independent, deterministic where possible" goal.

### Alternative B: GitHub does bookkeeping, agent does judgment (In Review owned by the agent)
- **Approach**:
  - built-in workflows for Done and auto-add;
  - an Action for Ready/Backlog and the milestone copy;
  - the agent sets In Progress and In Review.
- **Pros**: Deterministic Ready and Done; In Review is set exactly when finalize decides.
- **Cons**: In Review would miss PRs opened outside a session and stay an agent-procedure step.
- **Why not chosen**: Refined into the agreed approach. Once pre-review PRs are drafts, "a non-draft PR closes
  the issue" is a clean, observable event, so In Review moved to the Action.

### Alternative C: Everything event-driven, including In Progress
- **Approach**: Actions set In Progress on the first push or PR as well.
- **Pros**: The agent writes nothing to the board.
- **Cons**: A session claims a task before anything exists on GitHub, so In Progress would lag by the whole
  brainstorm and planning review, and a second session could pick the same task. Working around it needs an
  artificial GitHub signal from the agent, which is the agent setting the status indirectly.
- **Why not chosen**: In Progress is the transition the user flagged as important, and only the agent knows
  when it happens.

Also considered:
- **GitHub's MCP server instead of `gh` plus a script**: rejected, so the Action and the agent share one
  deterministic implementation.
- **Third-party "dependencies" Actions that parse prose**: rejected in favour of native links.

## Agreed Approach

Alternative B, with In Review moved to the Action. The agent owns In Progress, because only it knows when work
starts. GitHub owns every transition it can observe: Ready/Backlog from blocker state, In Review from
non-draft closing PRs, and Done from the issue closing. This makes the board correct without a session
running, keeps the agent's board writes to In Progress and follow-up issues, and stays generic because the
only per-project inputs are settings.

## Key Decisions

- Every status is derived from facts, in priority order:
  1. closed → Done;
  2. blocked label → Blocked;
  3. an open, non-draft closing PR → In Review;
  4. assigned → In Progress;
  5. every native blocker closed → Ready, else → Backlog.
- Prose dependencies don't count; converting them is a one-time setup step.
- Next task = lowest order value among Ready items; stop on a tie, a missing value or none Ready.
- The agent claims a task after writing `tracker.md`, by assigning it and setting In Progress. The Action derives In Review from open non-draft
  closing PRs. The built-in *Item closed* sets Done.
- Pre-review PRs are drafts; finalize marks the PR ready for review.
- PRs link by closing keyword and are not project items. The Action copies the issue's milestone onto the PR.
- Milestones are feature-level and cross-component, owned by planning, and reported (not closed) when empty.
- Follow-ups: finish undecided-free work first; small work stays in the change; bigger work becomes a linked
  sub-issue with order = after the parent's existing follow-ups (0.1, then 0.01 steps); ask when unsure.
- A session stops after the merge and the board updates; there's no automatic next task.
- One stdlib Python script serves the Action and the agent. A `github-project` skill holds the procedure, and
  the rules shrink.
- Configuration: field/option names in `.github/project-flow.json`; `PROJECT_URL` repository variable;
  `PROJECT_TOKEN` secret.
- Applying the setup to the current board is the last step of this change, right after merge, with the user
  approving each board write. The repository variable `PROJECT_URL` is the enable switch and is set last. Until
  it names the project, the Action, `sync`, `next` and `start` refuse it, so an unconverted board is never
  synced or picked from. The old procedure is not kept as a fallback.
- Real-environment verification uses a scratch repository and a scratch project under the user's account,
  driving the full loop through the real Action.
- Builder calls:
  - the Action manages only its own repository's issues;
  - a blocker closed as not planned or duplicate doesn't count;
  - follow-up order values step by 0.1, then 0.01, then 0.001, and otherwise ask;
  - the repository's superspec schema fork is edited directly.

## Open Questions

- The minimum token type and permissions that can write a user-owned project (classic PAT with `project`, or a
  fine-grained PAT if it supports user projects) are to be confirmed when the setup guide is written.
