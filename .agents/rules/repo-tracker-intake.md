---
trigger: always_on
---

Before picking up tracked work, read the sections Next task and tracker.md of `docs/superspec-workflow.md`.

- **Scope:** applies only when the user names a GitHub Project or view URL, an issue URL or an issue number; otherwise nothing is tracked and no board or issue write happens.
- **Select:** read every item to the last page, take the first item after the last completed one, and announce the chosen task and the previous completed task.
- **Check first:** the issue's declared dependencies are completed and it is not already In Progress, except a resume of an active change recorded in that change's `tracker.md`; ask on any ambiguity, boundary disagreement or unrecoverable order.
- **Read the issue** and follow its links; put the substantive findings into the brainstorm.
- **Record first:** write `tracker.md` right after the change is created and before any status write.
- **In Progress** is set when the change is created; **In Review** only after finalize confirms an open PR and it is recorded in `tracker.md`.
- **Statuses** match by name; if the Status field or an option is missing, stop and ask.
- **Outcomes:** handle `merge-locally`, `discarded` and `kept-as-is` as *tracker.md* says; Done belongs to *Closeout*, never to this rule.
- **Issue-only intake:** resolve the repository explicitly, use the only project that contains the issue, ask when there are several, and with none write to no board.
- **Scopes:** report a missing `gh` scope with the exact fix command; never work around it.
- **Reconcile first:** at the start of every intake, for In Review items whose archived `tracker.md` records a merged PR, close the issue first and set Done last; write nothing to the repository, and report instead of writing on the procedure's report-only cases.
- **No identifiers:** name no project, project number, issue number, RFC path, field id or board login; use placeholders at most.
