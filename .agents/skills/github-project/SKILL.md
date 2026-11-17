---
name: github-project
description: Run tracked work on a GitHub Project with scripts/project_flow.py. Use when the user names a GitHub Project or view URL, an issue URL or an issue number as the source of work ("pick up the next task from <project URL>", "work on <owner>/<repo>#<n>"), and at finalize and after the merge of a tracked change.
---

# github-project

Commands only. The rules they serve are in `docs/superspec-workflow.md`: *Next task* and *tracker.md* before
intake, *Closeout* and *Monitoring* before finalize and the closeout. Each step names the section that decides
whether and when to run it; when this skill and the doc seem to differ, the doc wins. Project setup is in
`docs/github-project-setup.md`.

Placeholders: `<project URL>` is the project the user named (a view URL works too); `<owner>/<repo>#<n>` is the
tracked issue, in its own repository. Every command runs from the repository root. A stop prints
`stop: <reason>: <detail>` and exits 3; the response to each reason is the table in *Next task*, *Selecting
the task*.

```bash
THIS_REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)   # --repo: where PROJECT_URL is read and follow-ups are created
PF="python3 -I scripts/project_flow.py"
```

Add `--dry-run` to any writing command to print its writes without making them.

## Intake

1. **Scope precheck** — see *Next task*, *When this applies*:

   ```bash
   gh auth status                     # the token scopes must include project
   gh auth refresh -s project         # the fix to report when it does not
   ```

2. **Issue-only intake** — see *Next task*, *Issue-only intake*. List the issue's projects (page with
   `-f cursor=<endCursor>` while `hasNextPage` is true) and read the enabled project (HTTP 404: none):

   ```bash
   gh api graphql -f query='
   query($o: String!, $r: String!, $num: Int!, $cursor: String) {
     repository(owner: $o, name: $r) { issue(number: $num) {
       projectItems(first: 50, after: $cursor) {
         pageInfo { hasNextPage endCursor } nodes { project { title url } } } } }
   }' -f o=<owner> -f r=<repo> -F num=<n>
   gh api "repos/$THIS_REPO/actions/variables/PROJECT_URL" --jq .value
   ```

3. **Pickup** without a named task — see *Next task*, *Selecting the task*:

   ```bash
   $PF next --project "<project URL>" --repo "$THIS_REPO"
   $PF next --project "<project URL>" --repo "$THIS_REPO" --skip-unordered   # only after the user agrees (missing-order)
   ```

   Success ends with `next: <owner>/<repo>#<n> order=<value>`.

4. **Resume check** — see *Next task*, *Naming, announcing and reading*. Lists the active changes that record
   the issue:

   ```bash
   grep -l "Issue: <owner>/<repo>#<n>$" openspec/changes/*/tracker.md
   ```

5. **Read the issue** — see *Next task*, *Naming, announcing and reading*:

   ```bash
   gh issue view <n> --repo <owner>/<repo> --comments
   ```

6. **Write `tracker.md`** — the fields are in *tracker.md*, *Purpose and fields*.

7. **Claim** — see *Next task*, *The claim*:

   ```bash
   $PF start <owner>/<repo>#<n> --project "<project URL>" --repo "$THIS_REPO"
   $PF start <owner>/<repo>#<n> --project "<project URL>" --repo "$THIS_REPO" --force   # never without the user's explicit authorization (Next task)
   ```

   Success ends with `started: <owner>/<repo>#<n>`.

## During the change

8. **Draft PR** — see *Next task*, *Statuses are derived* and *Follow-up work*. The body is the template in
   step 11:

   ```bash
   gh pr create --draft --title "<type>(<scope>): <change-name>" --body-file <body.md>
   ```

9. **Blocked label and release** — see *tracker.md*, *Statuses and finalize outcomes*. The label name is
   `blocked_label` in `.github/project-flow.json`:

   ```bash
   gh issue edit <n> --repo <owner>/<repo> --add-label <blocked label>
   gh issue edit <n> --repo <owner>/<repo> --remove-label <blocked label>
   gh issue edit <n> --repo <owner>/<repo> --remove-assignee @me
   $PF sync --project "<project URL>" --repo "$THIS_REPO"
   ```

10. **Follow-up issue** — see *Next task*, *Follow-up work*. `--pr` takes the real URL of the change's PR
    (`gh pr view --json url --jq .url`; step 8 creates it when there is none). Write the body to a file;
    repeat `--blocker` per blocker:

    ```bash
    $PF followup --project "<project URL>" --repo "$THIS_REPO" \
      --parent <owner>/<repo>#<n> --title "<title>" --body-file <body.md> \
      [--blocker <owner>/<repo>#<m> ...] --pr <PR URL> \
      --change-dir openspec/changes/<change-name> --found-at "<file:line or step>"
    ```

    It prints `created: #<m>` at once. After a later failure it prints `missing: <steps>` and
    `resume with the same arguments and --resume <m>`: re-run the exact command line with `--resume <m>` added.
    Add the issue to the PR body (step 11).

## Finalize

11. **PR body** — see *Closeout*, *When it starts*:

    ```markdown
    Closes <owner>/<repo>#<n>

    ## Follow-up issues
    - <owner>/<repo>#<m> — <title>
    ```

    ```bash
    gh pr edit <pr> --body-file <body.md>
    gh pr ready <pr>
    gh pr view <pr> --json state,isDraft
    ```

12. **Record the PR** in `tracker.md` — see *tracker.md*, *Purpose and fields*.

13. **Sync and read the Status back** — see *Closeout*, *When it starts*. The field name is `status_field` in
    `.github/project-flow.json`:

    ```bash
    $PF sync --project "<project URL>" --repo "$THIS_REPO"
    gh api graphql -f query='
    query($o: String!, $r: String!, $num: Int!, $cursor: String) {
      repository(owner: $o, name: $r) { issue(number: $num) { state
        projectItems(first: 50, after: $cursor) { pageInfo { hasNextPage endCursor }
          nodes { project { url }
            fieldValueByName(name: "<status field>") {
              ... on ProjectV2ItemFieldSingleSelectValue { name } } } } } }
    }' -f o=<owner> -f r=<repo> -F num=<n>
    ```

    Take only the node whose `project.url` is the enabled project (the `PROJECT_URL` value). While it is not
    found and `hasNextPage` is true, repeat with `-f cursor=<endCursor>`. A node that is never found, a failed
    page or an empty Status confirms nothing: report "could not confirm" with the reason.

## After the merge

14. **Post-merge check** — see *Closeout*, *Done, last: the post-merge check*. Read the Status back as in
    step 13:

    ```bash
    gh issue view <n> --repo <owner>/<repo> --json state
    gh issue close <n> --repo <owner>/<repo> --reason completed --comment "Completed by <PR URL>"
    $PF sync --project "<project URL>" --repo "$THIS_REPO"
    ```

15. **Local merge** — see *Closeout*, *Local merge*:

    ```bash
    gh issue close <n> --repo <owner>/<repo> --reason completed --comment "Completed by <pushed commit SHA>"
    $PF sync --project "<project URL>" --repo "$THIS_REPO"
    ```

## Setup check

16. See `docs/github-project-setup.md`:

    ```bash
    $PF check                               # .github/project-flow.json, offline
    $PF check --project "<project URL>"     # fact: / warning: lines; stop: setup-incomplete while a fact remains
    ```
