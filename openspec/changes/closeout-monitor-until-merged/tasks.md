## 1. Rule

- [x] 1.1 In `.agents/rules/repo-superspec-closeout.md`, rephrase the fixed-order sentence to end: "answer comments and monitor to merge, then the applicable follow-ups (the issue and the board for tracked work)".
- [x] 1.2 Replace the "Keep waiting" bullet with a "Monitor to merge" bullet: wait until merged, closed or the user says stop; meet the repository's merge requirements (for example update from master when required) and return to babysitting after any push; never merge unless asked; prefer an event-driven watch, else poll coarsely with backoff and report only state changes.
- [x] 1.3 Replace the "Done" bullet with an "After the merge" bullet: do the applicable follow-ups, for tracked work close the issue first and write Status Done last, and report a merge that came before the closeout finished with nothing marked Done.
- [x] 1.4 Check the rule stays under its 4,096-byte cap with `wc -c`.

## 2. Procedure

- [x] 2.1 In `docs/superspec-workflow.md` *Closeout*, update checklist steps 6 and 7: monitor to merge while meeting merge requirements (such as a required update from master) and never merge unless asked; then do the applicable follow-ups for merged work, Done last.
- [x] 2.2 In *Monitoring*, add the repository's merge requirements and the merge to what entering monitoring mode says it waits for, keeping the merged-or-closed-or-stop endpoint.
- [x] 2.3 In the polling-cycle table, add `mergeStateStatus` to the `gh pr view --json` read and describe its values (for example `BEHIND`, `DIRTY`, `BLOCKED`, `CLEAN`).
- [x] 2.4 In *What to do with the state*, add merge-requirement handling: a `BEHIND` branch is updated from the base and returns to babysitting; a blocker the agent cannot resolve, including conflicts, a missing or unreadable result or an unknown state, is reported instead of waited on silently. Change the Merged bullet to say the applicable follow-ups are done after a completed closeout.

## 3. Finalize hand-off

- [x] 3.1 In `openspec/schemas/superspec/templates/finalize.md`, change both `pr-created` and `pr-updated` next-step lines to end "then monitor to merge and complete the applicable follow-ups".
- [x] 3.2 In `openspec/schemas/superspec/schema.yaml`, update the finalize instruction's next-step text and the v5 description to the same ending.
- [x] 3.3 In `openspec/schemas/superspec/INTEGRATION.md`, update step 13 to the same ending.
- [x] 3.4 Search the changed operational files only — `.agents/rules/repo-superspec-closeout.md`, `docs/superspec-workflow.md`, `openspec/schemas/superspec/templates/finalize.md`, `openspec/schemas/superspec/schema.yaml` and `openspec/schemas/superspec/INTEGRATION.md` — for "wait for approval and merge"; expect no hits.

## 4. Verification

- [x] 4.1 Run `python3 docs/checks/check_agent_files.py --root .` from the repository root.
- [x] 4.2 Run `python3 -m unittest discover -s docs/checks -p 'test_*.py'`.
- [x] 4.3 Run `openspec validate --all`.
- [x] 4.4 Re-read the changed rule, spec delta, procedure sections and hand-off text together; confirm the order, the merge prohibition, the exactly-once archive and the issue-first/Status-last follow-ups agree, with no new workflow step.
