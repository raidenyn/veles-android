---
trigger: always_on
---

Before picking up tracked work, read the sections Next task and tracker.md of `docs/superspec-workflow.md`.

- **Scope:** work is tracked only when the user names a GitHub Project or view URL, an issue URL or an issue number as the source of the work, and only while the `PROJECT_URL` variable (repository-level, or in the environment named in `.github/project-flow.json`) names that project; a project named as an example is not tracked. Untracked work gets no `tracker.md`, no claim and no board or issue write.
- **Run the steps through the `github-project` skill:** it runs `scripts/project_flow.py` for the selection, the claim, `sync` and follow-ups; never write a Status except through the claim, and never pick a task by any other order.
- **No identifiers:** name no project, project number, issue number, RFC path, field id or board login; use placeholders at most.
- **Scopes:** report a missing `gh` scope with the exact fix command; never work around it.
