# GitHub Project setup

How to connect a GitHub Project (the new Projects, not classic boards) to the project flow: the
`scripts/project_flow.py` tool, the `.github/workflows/project-flow.yml` workflow and the `github-project` agent
skill. It works for any project; nothing here names a concrete one. The rules the agents follow are in
`docs/superspec-workflow.md` (*Next task*, *tracker.md*, *Closeout*); the agent commands are in
`.agents/skills/github-project/SKILL.md`.

## What the flow does

Every Status is derived from facts on GitHub, never set by hand. For an item whose content is an issue, the
first matching rule wins:

0. the issue is closed → Done;
1. the issue carries the blocked label → Blocked;
2. an open, trusted pull request that is not a draft closes the issue → In Review;
3. the issue has an assignee → In Progress;
4. every native "blocked by" issue is closed as completed, or there is none → Ready; otherwise → Backlog.

A closing pull request is trusted when its branch is in the issue's repository or its author is an owner,
member or collaborator. A fork pull request from anyone else moves no Status and gets no milestone; `sync`
reports it as `ignored untrusted PR <owner>/<repo>#<n>`.

`project_flow.py sync` makes the board match these rules. The workflow runs it on issue and pull request events
and once a day, and the agents run it at intake and at the closeout. It also copies an issue's milestone onto
the pull request that closes it, and reports open milestones that have no open issue left. Draft-issue and
pull-request cards are ignored.

## Fields and Status options

The project needs:

- a **single-select Status field** with one option for each role: Backlog, Ready, In Progress, In Review,
  Blocked and Done;
- a **number field** for the order (the next task is the Ready, unassigned issue with the lowest value);
- a **label** in each repository for Blocked.

The names live in `.github/project-flow.json`, which holds names only — never a project number, an owner, a
field id or an option id:

```json
{
  "status_field": "Status",
  "roles": {
    "backlog": "Backlog",
    "ready": "Ready",
    "in_progress": "In Progress",
    "in_review": "In Review",
    "blocked": "Blocked",
    "done": "Done"
  },
  "order_field": "<order field name>",
  "blocked_label": "blocked"
}
```

To use other names, change the values, never the keys. Options are matched by name, and the tool never
creates or renames a field, an option or a milestone: a missing one stops it with `missing-option`.
`python3 -I scripts/project_flow.py check` validates the file offline.

## Built-in project workflows

Built-in workflows are switched on in the project's UI only (the project menu, *Workflows*); the API cannot
enable them.

- **Item closed** → set Status to Done. `check` reports it as a setup fact while it is disabled or missing. It
  is a redundancy: `sync` derives Done from the closed issue anyway, but the built-in workflow sets it at once.
- **Auto-add to project**, filtered to **issues only** (for example `is:issue`), so a pull request never gets a
  card of its own and every task has exactly one card. `check` only hints at it: cards may also be added by
  hand.

Other built-in workflows that set a conflicting Status can cause temporary discrepancies and extra writes;
the next successful `sync` derives the Status again.

## Variable and secret

- **`PROJECT_URL`** (repository variable): the URL of the project the repository serves, in the form
  `https://github.com/users/<owner>/projects/<n>` or `https://github.com/orgs/<owner>/projects/<n>`. It is the
  enable switch. While it is unset, or names another project, the workflow is a successful no-op with a notice,
  and `sync`, `next`, `start` and `followup` stop with `not-enabled`. The agent commands read it over the REST
  API from the repository given as `--repo`, so the user's `gh` token needs read access to the repository's
  Actions variables; otherwise they stop with `variable-unreadable`.
- **`PROJECT_TOKEN`** (repository secret): the token the workflow uses to write the project and to read and
  edit the repository's issues and pull requests. The default `GITHUB_TOKEN` cannot write a user-owned project.
  Only the workflow uses it; the agents use the user's own `gh` login, which needs the `project` scope
  (`gh auth refresh -s project`).

**Verified permissions.** Against a real user-owned project:

- A **classic token of the project owner with the `project` and `public_repo` scopes** ran the workflow
  (as `PROJECT_TOKEN`) and every agent command on a **public** repository.
- A classic token with `project` and `repo` ran every agent command on a **private** repository. That such a
  token runs the workflow on a private repository is expected but **not verified**.
- **Fine-grained tokens are not verified.**

The token must belong to an account that can see both the project and the repository. A private
user-owned project is invisible to any other account, and the workflow then fails with
`stop: incomplete-read: cannot read project …`. A token without `repo` cannot read a private repository:
the agent commands stop with `missing-scope`, but the workflow still succeeds, reporting every card as
unreadable (`sync: partial`) and writing nothing, so read the job summary after changing the token.

**Security.** Anyone with write access to the repository can run a workflow on a branch that reads
`PROJECT_TOKEN`; that is GitHub's model for repository secrets. The project-flow workflow itself checks out only
the default branch and never runs pull request code, but limit the token to what the flow needs (a
fine-grained token where possible), and consider scoping the secret to an environment. An expired token makes
the workflow fail visibly.

A project that spans several repositories installs the workflow, the configuration, the variable and the
secret in each one. Each repository's workflow writes only the items whose issue lives in it (it still reads
blockers from anywhere), so their item sets do not overlap. The agent's commands cover the whole project.

## Conventions

A board that uses this flow follows these conventions:

- **Assignment means started.** Assign an issue only when work on it starts; `next` never selects an assigned
  issue, and removing the assignment releases it.
- **Blocked is the label.** Add the blocked label to work that started and got stuck on something outside the
  board; remove it to let the Status be derived again. Setting Blocked by hand without the label is undone.
- **Order is a field, not card position.** Reorder by editing the order field; dragging cards changes
  nothing. Follow-up issues get a value right after their parent's (for example 15.1, 15.2 after 15).
- **Pre-review pull requests are drafts.** A pull request opened for planning review before the
  implementation is a draft, so its issue stays In Progress; marking it ready for review moves the issue to In
  Review.
- **Link pull requests with a closing keyword** in the body (`Closes #<n>`, or `Closes <owner>/<repo>#<n>`
  across repositories). The keyword closes the issue only when the pull request merges into the default
  branch.
- **Milestones are feature-level** and set on the issues during planning; the flow copies them onto the pull
  requests and never creates or closes one.

## Converting prose dependencies

Only native "blocked by" links count. An issue whose dependency is written only as text ("blocked by #<n>",
"depends on #<n>", "after #<n>") is Ready as soon as its native blockers are done. Before enabling, convert every
such dependency into a native "blocked by" relationship on the issue, and remove links
to work that was abandoned: a blocker closed as not planned or as a duplicate keeps the item in Backlog.

`check --project <URL>` lists open issues whose body or comments mention a dependency without a matching
native link, as **warnings**. A clean warning list does not prove the conversion is complete: setting
`PROJECT_URL` is your attestation that the dependencies were reviewed and converted.

## Enable, in order

1. Create the fields, the Status options and the blocked label, and adjust `.github/project-flow.json` if your
   names differ from the committed ones.
2. Enable the built-in workflows (*Item closed*; *Auto-add to project*, issues only).
3. Add the `PROJECT_TOKEN` secret.
4. Convert the prose dependencies to native links.
5. Give every open item an order value.
6. Give every In Progress item an assignee and every Blocked item the blocked label, or move them.
7. Run `check` until it reports no fact and you have reviewed its warnings:

   ```bash
   python3 -I scripts/project_flow.py check --project <project URL>
   ```

   Each finding is printed as `fact: …` or `warning: …`; while any fact remains it ends with
   `stop: setup-incomplete` and exit 3.
8. Set the `PROJECT_URL` variable — **last**.
9. Reconcile once, previewing first:

   ```bash
   python3 -I scripts/project_flow.py sync --project <project URL> --repo <owner>/<repo> --dry-run
   python3 -I scripts/project_flow.py sync --project <project URL> --repo <owner>/<repo>
   ```

   or run the workflow by hand (`workflow_dispatch`).

## Items that stay unreadable

`sync` never writes an item it cannot read completely, and `next` and `followup` stop with `incomplete-read`
while any item is unreadable. `check --project <URL>` lists each one as a fact. Three causes stay until
someone acts; the reason names the remedy:

- **A `REDACTED` item**: a card from a repository the token cannot see. Remove the card from the project, or
  grant the token access to that repository.
- **A blocker closed without a state reason**: GitHub returns no reason for some issues closed before it
  recorded reasons, so the blocker is neither completed nor abandoned. Reopen the blocker and close it again,
  so that a reason is recorded.
- **Inaccessible blockers**: an issue counts more blockers than the token can read. Grant the token access to
  the blockers' repositories.

## Rollback

Unset `PROJECT_URL`. The workflow becomes a no-op and every agent command refuses the project with
`not-enabled`. Statuses already written are plain field values and need no cleanup. Remove the secret as well if
the flow will not come back.
