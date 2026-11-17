---
name: github-project
description: Run tracked work on a GitHub Project with scripts/project_flow.py. Use when the user names a GitHub Project or view URL, an issue URL or an issue number as the source of work ("pick up the next task from <project URL>", "work on <owner>/<repo>#<n>"), and at finalize and after the merge of a tracked change.
---

# github-project

The commands for tracked work. The rules they serve are in `docs/superspec-workflow.md`: read *Next task* and
*tracker.md* before intake, and *Closeout* and *Monitoring* before finalize and the closeout. This skill
restates none of them; when a step below and the doc seem to differ, the doc wins. Project setup is in
`docs/github-project-setup.md`.

Placeholders: `<project URL>` is the project the user named (a view URL works too); `<this repo>` is this
checkout's repository, whose variable `PROJECT_URL` enables the project; `<owner>/<repo>#<n>` is the tracked
issue, in its own repository. Every command runs from the repository root and prints
`stop: <reason>: <detail>` with exit 3 when it stops; the response to each reason is the table in *Next task*,
*Selecting the task*.

```bash
THIS_REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)   # <this repo>
PF="python3 -I scripts/project_flow.py"
```

## Intake

1. **Scope precheck.** `gh auth status` must list the `project` scope. If it does not, report it with
   `gh auth refresh -s project` and stop.
2. **Tracked or not** (*Next task*, *When this applies*). Without a named source, stop using this skill.
3. **Issue-only intake** (*Next task*, *Issue-only intake*): list the issue's projects, then read which project
   is enabled (a 404 means none):

   ```bash
   gh api graphql -f query='
   query($o: String!, $r: String!, $num: Int!) {
     repository(owner: $o, name: $r) { issue(number: $num) {
       projectItems(first: 20) { pageInfo { hasNextPage } nodes { project { title url } } } } }
   }' -f o=<owner> -f r=<repo> -F num=<n>
   gh api "repos/$THIS_REPO/actions/variables/PROJECT_URL" --jq .value
   ```

4. **Pickup** without a named task:

   ```bash
   $PF next --project "<project URL>" --repo "$THIS_REPO"
   # only after the user agrees to skip items without an order value:
   $PF next --project "<project URL>" --repo "$THIS_REPO" --skip-unordered
   ```

   Success ends with `next: <owner>/<repo>#<n> order=<value>`.
5. **Resume check** before any claim: an active change that records the issue is a resume (no `start`).

   ```bash
   grep -l "Issue: <owner>/<repo>#<n>$" openspec/changes/*/tracker.md
   ```

6. **Announce and read** (*Next task*, *Naming, announcing and reading*): `gh issue view <n> --repo
   <owner>/<repo> --comments`, then its links, parent and sub-issues; findings go into the brainstorm.
7. **Create the change, then write `tracker.md`** with the fields in *tracker.md*, *Purpose and fields*.
8. **Claim** (*Next task*, *The claim*):

   ```bash
   $PF start <owner>/<repo>#<n> --project "<project URL>" --repo "$THIS_REPO"
   # only after the user explicitly authorizes claiming an item that is not Ready:
   $PF start <owner>/<repo>#<n> --project "<project URL>" --repo "$THIS_REPO" --force
   ```

   Success ends with `started: <owner>/<repo>#<n>`. Add `--dry-run` to any writing command to print the writes
   without making them.

## During the change

9. **Pre-review PR** (if one is opened before finalize): always a draft.

   ```bash
   gh pr create --draft --title "<type>(<scope>): <change-name>" --body-file <body.md>
   ```

10. **Blocked or released** (*tracker.md*, *Statuses and finalize outcomes*); the label name is `blocked_label`
    in `.github/project-flow.json`:

    ```bash
    gh issue edit <n> --repo <owner>/<repo> --add-label <blocked label>     # Blocked
    gh issue edit <n> --repo <owner>/<repo> --remove-label <blocked label>  # derived again
    gh issue edit <n> --repo <owner>/<repo> --remove-assignee @me            # release the claim
    $PF sync --project "<project URL>" --repo "$THIS_REPO"
    ```

11. **Follow-up issue** (*Next task*, *Follow-up work*). Write the body to a file first; `--blocker` repeats
    per blocker and never names the parent; the issue is created in `--repo`:

    ```bash
    $PF followup --project "<project URL>" --repo "$THIS_REPO" \
      --parent <owner>/<repo>#<n> --title "<title>" --body-file <body.md> \
      [--blocker <owner>/<repo>#<m> ...] --pr <PR URL> \
      --change-dir openspec/changes/<change-name> --found-at "<file:line or step>"
    ```

    It prints `created: #<m>` at once. On a later failure it prints `missing: <steps>` and
    `resume with the same arguments and --resume <m>`: re-run the **exact** command line with `--resume <m>`
    added. Add every follow-up to the PR body.

## Finalize (tracked work)

12. **PR body** (*Closeout*, *When it starts*), set before the PR is marked ready:

    ```markdown
    Closes <owner>/<repo>#<n>

    ## Follow-up issues
    - <owner>/<repo>#<m> — <title>
    ```

    ```bash
    gh pr edit <pr> --body-file <body.md>      # when the PR already exists
    gh pr ready <pr>                            # when it is a draft
    gh pr view <pr> --json state,isDraft        # must report "OPEN" and false
    ```

13. **Record the PR** in `tracker.md`, commit and push it with the PR.
14. **Confirm In Review** without writing it: run one `sync`, then read the item's Status (the field name is
    `status_field` in `.github/project-flow.json`):

    ```bash
    $PF sync --project "<project URL>" --repo "$THIS_REPO"
    gh api graphql -f query='
    query($o: String!, $r: String!, $num: Int!) {
      repository(owner: $o, name: $r) { issue(number: $num) { state
        projectItems(first: 20) { pageInfo { hasNextPage } nodes { project { url }
          fieldValueByName(name: "<status field>") {
            ... on ProjectV2ItemFieldSingleSelectValue { name } } } } } }
    }' -f o=<owner> -f r=<repo> -F num=<n>
    ```

## After the merge (*Closeout*, *Done, last: the post-merge check*)

15. Check the issue, close it only if the merge did not, then sync once and confirm Done with the query in
    step 14:

    ```bash
    gh issue view <n> --repo <owner>/<repo> --json state
    gh issue close <n> --repo <owner>/<repo> --reason completed --comment "Completed by <PR URL>"
    $PF sync --project "<project URL>" --repo "$THIS_REPO"
    ```

    Report the `sync` writes to Ready and the follow-up issues from the PR body.
16. **Local merge** (*Closeout*, *Local merge*), only after the push to the default branch succeeded:

    ```bash
    gh issue close <n> --repo <owner>/<repo> --reason completed --comment "Completed by <pushed commit SHA>"
    $PF sync --project "<project URL>" --repo "$THIS_REPO"
    ```

## Setup check

17. `$PF check` validates `.github/project-flow.json` offline; `$PF check --project "<project URL>"` lists the
    setup facts (`fact:`) and warnings (`warning:`) and stops with `setup-incomplete` while any fact remains.
