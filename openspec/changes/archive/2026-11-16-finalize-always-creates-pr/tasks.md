## 1. Closeout rule

- [x] 1.1 In `.agents/rules/repo-superspec-closeout.md`, rephrase the **Start** bullet: finalize always creates the pull request when the feature branch has none, never asks and never skips, reuses an existing pre-review PR, and confirms it is open; a PR that cannot be created or confirmed stops finalize with no outcome and no closeout.
- [x] 1.2 In the same rule, state in the fixed-order sentence that the pull request always exists before the closeout starts, and rephrase the **Local merge** bullet so it applies only when the user explicitly asks for that flow.
- [x] 1.3 Confirm the rule stays at most 4,096 bytes with `wc -c .agents/rules/repo-superspec-closeout.md` and keeps LF line endings.

## 2. Procedure

- [x] 2.1 In `docs/superspec-workflow.md` *Closeout*, rephrase "When it starts": finalize creates the PR when none exists without asking, confirms it open, then records it in `tracker.md` for tracked work, sets In Review and hands off; creation or confirmation failure stops finalize with no outcome.
- [x] 2.2 Check the rest of *Closeout* and *Monitoring* for statements that a PR may be absent on the canonical path; leave only the escape-hatch local-merge wording.

## 3. Superspec schema fork

- [x] 3.1 In `openspec/schemas/superspec/schema.yaml`, replace the finalize instruction's optional-PR framing: the finalize artifact description, the top description, the optional-precondition paragraph, the v5 description entry and the active apply/verify PASS summaries (`schema.yaml:269–274` and `schema.yaml:861–867`) now say finalize always creates the PR when none exists and reuses an existing pre-review PR.
- [x] 3.2 Reorder the finalize instruction's git-side steps so the receipt is fail-closed: push the feature branch, create the PR when none exists with a generated Conventional Commit title and the standard body (no prompt, no custom-title choice, no skip), confirm it is open, post or edit the onboarding comment, then write `finalize.md` with the true `pr-created` or `pr-updated` outcome and the comment result, commit and push the receipt, record the PR in `tracker.md` and set In Review, and hand off.
- [x] 3.3 In the same instruction, make a failed creation or a non-open confirmation an error: report it, record no outcome, and do not start the closeout; make clear that `merge-locally`, `kept-as-is` and `discarded` are only for an explicitly user-chosen non-canonical flow.
- [x] 3.4 Update the escape-hatch paragraph: it may still hand-write `finalize.md`, but "no pre-review PR" is not an escape-hatch reason.
- [x] 3.5 In `openspec/schemas/superspec/templates/finalize.md`, add that `pr-created` and `pr-updated` are the canonical outcomes and that canonical finalize always leaves an open PR; keep `merge-locally`, `kept-as-is` and `discarded` as explicit non-canonical outcomes.
- [x] 3.6 In `openspec/schemas/superspec/README.md`, update the endpoint row and both usage flows to "creates or updates the PR", without touching historical version narratives.
- [x] 3.7 In `openspec/schemas/superspec/INTEGRATION.md`, update the current canonical sequence (steps 8-13), the DAG description, the escape-hatch note, the golden-path summary and the no-PR-variant paragraph so the canonical path always creates the PR; leave historical v3/v4 narrative sections intact.

## 4. Fork-divergence record

- [x] 4.1 In `docs/AGENTS.md` divergence item 3, add the always-create-PR step (no prompt, no skip) and the receipt written only after open-PR confirmation, so a schema reinstall re-applies them.

## 5. Verification

- [x] 5.1 Search the changed current-behavior text for `Offer to open`, `PR creation prompt`, `skip PR creation`, `No PR exists`, `optionally open PR`, `updates PR if one exists` and `comment if PR exists`; expect no hits outside the historical v3/v4 narrative sections.
- [x] 5.2 Run `python3 docs/checks/check_agent_files.py --root .`.
- [x] 5.3 Run `python3 -m unittest discover -s docs/checks -p 'test_*.py'`.
- [x] 5.4 Run `openspec validate --all`.
- [x] 5.5 Re-read the rule, spec delta, procedure, schema instruction, template, README, integration guide and divergence list together; confirm the always-create behavior, fail-closed receipt, tracker ordering and escape-hatch restriction agree, with no new workflow step.
