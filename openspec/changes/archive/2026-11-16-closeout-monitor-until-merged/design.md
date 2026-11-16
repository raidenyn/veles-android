## Context

The closeout policy lives in four places that must agree: the always-on rule `.agents/rules/repo-superspec-closeout.md`, the `superspec-closeout` capability spec, the *Closeout* and *Monitoring* sections of `docs/superspec-workflow.md`, and the finalize next-step text in the `superspec` schema fork. Today the rule's order says "answer comments and wait for approval and merge", and the waiting bullet ends at "merged, closed or the user says stop" without saying that a repository can also require the branch to be updated from master before merging. The post-merge work is described only as "Done", which hides the follow-up steps behind the final board write.

The definition is documentation-only: no code, no CI change and no new workflow step.

## Goals / Non-Goals

**Goals:**

- Make the merge, not approval, the end of monitoring.
- Require the agent to meet the repository's merge requirements while waiting, including a required update from the default branch, and to return to babysitting after each push.
- Make the applicable post-merge follow-ups explicit, keeping the tracked-work order of closing the issue first and writing Status Done last.
- Keep the rule short (under its 4,096-byte cap) and the four copies consistent.

**Non-Goals:**

- Adding merge automation, auto-merge, a merge queue, or any new closeout step.
- Changing the merge prohibition, the exactly-once sync/archive, the comment trust rules or the bounded CI fixing.
- Reworking the tracker intake, the reviewer roster or the schema's git-side closeout.

## Decisions

- **D1 — Endpoint is the merge.** Replace "wait for approval and merge" with "monitor to merge" in the order sentence and the waiting bullet. Approval is one signal monitoring observes, not a stopping point.
- **D2 — Merge requirements are part of monitoring.** Add the repository's merge requirements to the waiting duty and add `mergeStateStatus` to the polling read. `BEHIND` means the branch must be updated from the base: update it (`gh pr update-branch`, or merge the base branch and push), then return to babysitting. Conflicts, a missing or unreadable merge state, `UNKNOWN`, or another blocker the agent cannot resolve is reported to the user instead of waited on silently.
- **D3 — Follow-ups follow the merge.** Replace the "Done" bullet with "After the merge": perform the applicable follow-ups, for tracked work close the issue first and write Status Done last, and report a merge that arrived before the closeout finished with nothing marked Done.
- **D4 — Edit every copy in one change.** The rule, the spec requirement, the procedure's checklist and state handling, and the finalize hand-off are rephrased together so no copy keeps the old ending. The spec delta modifies the closeout-order requirement and renames the monitoring and post-merge requirements.
- **D5 — No separate mechanism.** The work uses the existing watch/poll and `gh` reads; it adds no script, state machine or new artifact.

## Risks / Trade-offs

- **Wording drift between the four copies** → one change edits all copies; the plan's grep checks the old phrase is gone from every operational file and the new terms appear.
- **`gh pr update-branch` unavailable or denied** → fall back to merging the base branch and pushing, and report the blocker if neither works.
- **A merge queue or auto-merge hides the exact blocker** → the agent still never merges; an unrecognized blocker is reported rather than guessed at.
- **The rule grows past its cap** → the plan measures `wc -c` after the edit; the expected result is about 2.3 KB against 4,096.

## Migration Plan

None: this is a documentation and policy change. Revert the closeout commit to roll back. The spec sync and archive happen once in the closeout, as usual.

## Open Questions

None.
