## Design Summary

The closeout definition currently ends its waiting phase at "wait for approval and merge" and treats the merge as the trigger for Done. That understates what monitoring is for: a pull request can also need the branch updated from master or another repository condition met before it can merge. The revised definition makes the endpoint explicit — the agent monitors until the pull request is **merged** — and requires it to meet the repository's merge requirements while waiting. After the merge it performs the applicable follow-ups, for tracked work closing the issue first and writing Status Done last.

The change is a rephrase, not a new workflow: the rule, the capability spec, the procedure's *Closeout* and *Monitoring* sections, and the finalize hand-off text all describe the same ordered closeout, using "monitor to merge" and "applicable follow-ups" instead of "wait for approval and merge" and "the issue and the board".

## Alternatives Considered

### Alternative A: Change only the rule file
- **Approach**: Edit `.agents/rules/repo-superspec-closeout.md` and nothing else.
- **Pros**: Smallest diff; no schema or procedure edits.
- **Cons**: The spec still defines the old order, and the procedure and finalize hand-off still say "wait for approval and merge"; the rule would contradict the definition it implements.
- **Why not chosen**: The repository treats these files as one definition; a rule-only edit would leave the contradiction in place.

### Alternative B: Consistent rephrase across the definition and its copies
- **Approach**: Update the rule, the `superspec-closeout` spec, the *Closeout* and *Monitoring* procedure sections, and the finalize next-step text in the schema fork and its integration doc. Keep the existing order, merge prohibition, issue-then-Done order and early-merge reporting.
- **Pros**: One coherent definition; no behavior beyond the requested clarification; small, reviewable wording changes.
- **Cons**: Touches the schema fork and its recorded divergence text.
- **Why not chosen**: Chosen for consistency; the extra files are copies of the same definition.

### Alternative C: Enumerate merge conditions in the rule
- **Approach**: Add a checklist of repository conditions (branch update, required checks, approvals, merge queue, …) to the rule.
- **Pros**: More explicit for known repositories.
- **Cons**: An open-ended list ages badly and adds ad-hoc content the user asked to avoid.
- **Why not chosen**: The general duty plus one concrete example ("for example update from master when required") keeps the rule short and covers unknown conditions.

## Agreed Approach

Use Alternative B. The rule's fixed order becomes: babysit to green, sync and archive once, review the docs, push, babysit again, answer comments and monitor to merge (meeting the repository's merge requirements, such as a required update from master), then the applicable follow-ups, with the issue closed first and Status Done last for tracked work. "Keep waiting" becomes "Monitor to merge"; "Done" becomes "After the merge" and covers the applicable follow-ups plus reporting a merge that arrived before the closeout finished. The spec requirement, the procedure sections and the finalize hand-off are rephrased to match.

## Key Decisions

- Approval is not the endpoint; the merge is. Monitoring continues through branch updates and any other repository merge requirement, and after each push returns to babysitting.
- The agent still never merges unless the user asks, and a pull request closed without merging still ends monitoring and is reported with nothing marked Done.
- Post-merge work is described as "applicable follow-ups", with the existing tracked-work order kept: issue first, Status Done last.
- No new rule, capability, workflow step or merge automation is introduced.

## Open Questions

None.
