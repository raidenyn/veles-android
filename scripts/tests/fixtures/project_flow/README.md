# project_flow fixtures

Responses recorded with `GhTransport` from a scratch repository and a scratch user project, using the
query constants in `scripts/project_flow.py`, then scrubbed. Each query reads `first: $first`, which
defaults to 100; the recording passed a smaller page size so that pagination is real. Status and order
values carry `__typename`, which tells a number value from a value of a field of another type:

| File | Query | Variables | Shows |
| --- | --- | --- | --- |
| `fields.json` | `FIELDS_QUERY` | `Status`, `Implementation order` | project id, Status options, NUMBER order field |
| `workflows.json` | `WORKFLOWS_QUERY` | default | built-in workflows with `enabled` |
| `items_page1.json`, `items_page2.json` | `ITEMS_QUERY` | `first: 5` | `hasNextPage` true, then false; a draft issue card; recorded before the query selected `rateLimit`, which the reader treats as optional (absent: cost 0, remaining unknown) |
| `issue_facts.json` | `ITEM_QUERY` | default | item 1 with its project's order field type (NUMBER): blockers closed as DUPLICATE, NOT_PLANNED, COMPLETED; a draft and a ready closing PR; assignee; mixed-case labels; parent; milestone |
| `blockers_page1.json`, `blockers_page2.json` | `ISSUE_CONNECTION_QUERY` for `blockedBy` | `first: 2` | a nested connection's follow-up pages |
| `text_page1.json`, `text_page2.json` | `TEXT_QUERY` | issue 1, `first: 2` | body and three comments over two pages |
| `closing_issues_page1.json`, `closing_issues_page2.json` | `CLOSING_ISSUES_QUERY` | PR 10, `first: 1` | a PR closing issues 1 and 5 |
| `sub_issues_<n>.json` | `SUB_ISSUES_QUERY` | issues 5, 1, 6, 7 | 5 has 1 and 6; 6 has 7, which is not on the project |
| `milestones.json` | REST `repos/{owner}/{repo}/milestones?state=all` | | trimmed to the fields the tool reads |

Added by hand after recording, when the queries started selecting them: each closing pull request's
`headRepository` (its own repository: a same-repository branch) and `authorAssociation` (`OWNER`, the
scratch owner opened them).

Scrubbing: node ids became `PVT_project1`, `PVTI_item<n>`, `PVTSSF_field1`, `PVTF_field1`, `I_issue<n>`
and `MI_milestone1`; cursors became `cursor<n>`; option ids `opt<n>`; database ids `1001`...; the login and
owner became `owner` and the repository `owner/repo`. Items 1 to 6 hold issues 1 to 6 (`I_issue<n>`,
database id `100<n>`); item 7 holds issue 8 (`I_issue7`, `1007`); item 8 is a draft card. Issue 7 is a
sub-issue of 6 and is not on the project. Re-record with the same queries and scrub again;
a grep for GitHub token prefixes, real project or issue node ids (`PVT_` or `I_` followed by `kw`) and the real login must find nothing.
