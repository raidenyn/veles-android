## Why

The closeout definition says to "wait for approval and merge", which reads as if approval is the finish line and the merge takes care of itself. Real repositories also block a merge until the branch is updated from master or another condition is met, so an agent that only watches checks and reviews can stop working too early. After the merge, the board and issue updates are described only as "Done", not as follow-ups that belong to the closeout. The rule and its copies should say plainly that the closeout monitors until merged and then completes the applicable follow-ups.

## What Changes

**Closeout waiting phase**
- From: answer comments and wait for approval and merge; "Keep waiting" runs until merged, closed or stop, with no duty to meet the repository's merge requirements.
- To: answer comments and monitor to merge, meeting the repository's merge requirements (for example a required update from master) and re-checking after the resulting push; the monitoring reads include the pull request's merge state so a blocked merge is visible.
- Reason: approval is not the only gate; a pull request can stay blocked while its checks are green.
- Impact: non-breaking clarification of the rule, spec, procedure and finalize hand-off.

**Post-merge follow-ups**
- From: "Done" only after a merge and a completed closeout, with the issue closed first and Status written last.
- To: after a merge perform the applicable follow-ups; for tracked work close the issue first and write Status Done last, and report a merge that arrived before the closeout completed with nothing marked Done.
- Reason: follow-up work belongs to the merge, not to an implied "Done" step.
- Impact: non-breaking rephrase; no new workflow step.

**Finalize hand-off**
- From: "then wait for approval and merge".
- To: "then monitor to merge and complete the applicable follow-ups".
- Reason: keep the generated hand-off consistent with the rule.
- Impact: the superspec schema fork's finalize template, its instruction text and the integration doc.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `superspec-closeout`: the waiting requirement monitors to merge and covers the repository's merge blockers; the Done requirement becomes the post-merge follow-up requirement.

## Impact

- `.agents/rules/repo-superspec-closeout.md`
- `openspec/specs/superspec-closeout/spec.md` (delta)
- `docs/superspec-workflow.md` (*Closeout* and *Monitoring*)
- `openspec/schemas/superspec/templates/finalize.md`, `openspec/schemas/superspec/schema.yaml` and `openspec/schemas/superspec/INTEGRATION.md`
- No code, tests, dependencies or CI changes; the rule stays well under its 4,096-byte cap.
