# Read-only spike evidence

Evidence for tasks 1.1 to 1.4. Each section records the commands actually run, the output
observed and a conclusion. Commands marked **unexecuted** were recorded from the installed
`gh` 2.104.0 (help text and its published source) but were deliberately **not** run: no write
reached GitHub while these spikes were collected.

Environment: Linux, `gh` 2.104.0, `openspec` 2.0.0, `python3` 3.14.4, repository
`raidenyn/veles-android`, default branch `master`. Board (supplied by the user, never inferred):
<https://github.com/users/raidenyn/projects/3/views/2> — owner `raidenyn` (a user account),
project number `3`, view `2`. Authentication precheck:

```bash
gh auth status
```

```text
github.com
  ✓ Logged in to github.com account raidenyn-shadow (/home/dev/.config/gh/hosts.yml)
  - Active account: true
  - Git operations protocol: ssh
  - Token: gho_************************************
  - Token scopes: 'admin:public_key', 'gist', 'project', 'read:org', 'repo'
```

The `project` scope covers `read:project`, so the read queries and the write forms below need no
further authorization. The account is not a repository admin:
`gh api repos/raidenyn/veles-android --jq .permissions` →
`{"admin":false,"maintain":false,"pull":true,"push":true,"triage":true}`.

Concrete identifiers (owner, project, view, item, field and option ids, issue and PR numbers)
appear in this file only. The rules, schema, `config.yaml` and `docs/superspec-workflow.md`
must cite the queries by section name and name none of them.

## 1.1 Board queries

### Command

(a) All items in default `POSITION` order, with issue state and Status, paged with `after:$cursor`
until `hasNextPage` is false:

```bash
gh api graphql -f query='
query($o: String!, $n: Int!, $cursor: String) {
  user(login: $o) {
    projectV2(number: $n) {
      id
      title
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content { ... on Issue { number state title } }
          fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
        }
      }
    }
  }
}' -f o=raidenyn -F n=3
```

Page again with `-f cursor=<pageInfo.endCursor from the previous response>` added to the same call
(the brief's `after:$cursor` form).

(b) The project's views, with sort keys, grouping and filter:

```bash
gh api graphql -f query='
query($o: String!, $n: Int!) {
  user(login: $o) {
    projectV2(number: $n) {
      views(first: 20) {
        nodes {
          number name layout filter
          sortByFields(first: 5) { nodes { direction field { ... on ProjectV2FieldCommon { name } } } }
          groupByFields(first: 5) { nodes { ... on ProjectV2FieldCommon { name } } }
        }
      }
    }
  }
}' -f o=raidenyn -F n=3
```

(c) One issue's project memberships:

```bash
gh api graphql -f query='
query($o: String!, $r: String!, $num: Int!) {
  repository(owner: $o, name: $r) {
    issue(number: $num) {
      number
      title
      projectItems(first: 10) { nodes { id project { number title } } }
    }
  }
}' -f o=raidenyn -f r=veles-android -F num=93
```

(d) A value for a view's single sort field and for a simple single-field filter, with pagination
(`fieldValueByName` plus the matching value type; `SINGLE_SELECT` → `name`/`optionId`,
`NUMBER` → `number`):

```bash
gh api graphql -f query='
query($o: String!, $n: Int!, $cursor: String) {
  user(login: $o) {
    projectV2(number: $n) {
      items(first: 10, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          content { ... on Issue { number } }
          statusValue: fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name optionId } }
          orderValue: fieldValueByName(name: "Implementation order") { ... on ProjectV2ItemFieldNumberValue { number } }
        }
      }
    }
  }
}' -f o=raidenyn -F n=3
```

Second page: the same call with `-f cursor=Y3Vyc29yOnYyOpKqMDAwMDAwMDAuMc4N_f9x`. Filtering is done
server-side by passing the filter string as the `query` argument:

```bash
gh api graphql -f query='
query($o: String!, $n: Int!, $q: String!) {
  user(login: $o) { projectV2(number: $n) { items(first: 1, query: $q) { totalCount } } }
}' -f o=raidenyn -F n=3 -f q="status:Backlog"
```

(e) The Status field's metadata and option ids, as the write command needs them:

```bash
gh api graphql -f query='
query($o: String!, $n: Int!) {
  user(login: $o) {
    projectV2(number: $n) {
      id
      field(name: "Status") { ... on ProjectV2SingleSelectField { id name options { id name } } }
    }
  }
}' -f o=raidenyn -F n=3
```

Field types (the same call also returns the option lists used above):

```bash
gh api graphql -f query='
query($o: String!, $n: Int!) {
  user(login: $o) {
    projectV2(number: $n) {
      fields(first: 30) {
        nodes {
          ... on ProjectV2FieldCommon { id name dataType }
          ... on ProjectV2SingleSelectField { options { id name } }
        }
      }
    }
  }
}' -f o=raidenyn -F n=3
```

The ordering capability, by introspection:

```bash
gh api graphql -f query='
{
  __type(name: "ProjectV2") {
    fields { name args { name type { name kind ofType { name } } } }
  }
}'
```

```bash
gh api graphql -f query='
{
  __type(name: "ProjectV2ItemOrder") {
    name kind inputFields { name type { name kind ofType { name } } }
  }
}'
gh api graphql -f query='
{ __type(name: "ProjectV2ItemOrderField") { name kind enumValues { name } } }'
```

The organization fallback required by the brief:

```bash
gh api graphql -f query='
query($o: String!, $n: Int!) {
  organization(login: $o) { projectV2(number: $n) { id title } }
}' -f o=raidenyn -F n=3
```

### Result

(a) `id` `PVT_kwHOAETbkc4BhcMP`, `title` `"Veles OTP external devices"`, 30 items,
`hasNextPage: false`, `endCursor: Y3Vyc29yOnYyOpK5MDAwMDAwMDAuMDM1NzE0Mjg1NzE0Mjg1N84Q1Hco`
— the whole board is one page, so the "page to the last page" loop terminates after one request.
Item issue numbers in the order returned: `80 81 82 83 84 85 86 87 88 89 90 91 92 93 94 95 96 97
98 99 100 101 127 102 125 103 104 105 106 120`. Issues 80–92 are `CLOSED` with Status `Done`;
93–106, 127, 125 and 120 are `OPEN` with Status `Backlog`. The order is manual: 127, 125 and 120
sit inside the 93–106 number sequence, so `POSITION` order is not issue-number order. The item id
of issue 93 is `PVTI_lAHOAETbkc4BhcMPzg3-AAA` (used by the write forms in 1.2). A second run with
the same arguments and a narrower selection (`nodes { content { ... on Issue { number } } }`)
returned the same 30 issue numbers in the same order and the same cursor; because `hasNextPage`
was already `false` there was no second page to fetch — the `after:$cursor` loop still has to be
coded for the general case, and form (d) below exercises it.

(b) Four views, output abbreviated because it is one JSON object per view:

```text
number 2  name "Execution order"  layout TABLE_LAYOUT  filter null  sortByFields []  groupByFields []
number 3  name "Delivery board"   layout BOARD_LAYOUT  filter null  sortByFields []  groupByFields []
number 4  name "Security gates"   layout TABLE_LAYOUT  filter null  sortByFields []  groupByFields []
number 5  name "By phase"         layout TABLE_LAYOUT  filter null  sortByFields []  groupByFields []
```

The board URL points at view 2, which has no sort, no grouping and no filter: its order is the
project's default `POSITION` order that query (a) already returns.

(c) One membership: `{"id": "PVTI_lAHOAETbkc4BhcMPzg3-AAA", "project": {"number": 3, "title":
"Veles OTP external devices"}}` for issue 93, so issue-only intake resolves to project 3.

(d) First page: `hasNextPage: true`, `endCursor: Y3Vyc29yOnYyOpKqMDAwMDAwMDAuMc4N_f9x`, and
`80 Done 1.0` through `89 Done 10.0`. Second page: `hasNextPage: true`, `endCursor:
Y3Vyc29yOnYyOpKrMDAwMDAwMDAuMDXODf4Axg==`, and `90 Done 11.0` … `92 Done 13.0`,
`93 Backlog 14.0` … `99 Backlog 20.0`. Both value types resolve through `fieldValueByName`;
`optionId` is available on the single-select value. The filter tests, all with no error:
`status:Backlog` → `totalCount 17`; `area:Android` → `5`; `status:Backlog,Ready area:Android` →
`3`; `status:NoSuchOption` → `0`; `bogus:value` → `0`; `notafield:foo` → `0`; `no:such` → `30`
(all items — the `no:` prefix is a real qualifier and an unknown value under it is ignored). An
unknown or misspelled filter term is therefore never reported as an error: it silently matches
nothing or everything.

Introspection output for the `items` and `views` arguments (the `field` lookup is omitted):

```text
items:
   {"name": "after", "type": {"name": "String", "kind": "SCALAR", "ofType": null}}
   {"name": "before", "type": {"name": "String", "kind": "SCALAR", "ofType": null}}
   {"name": "first", "type": {"name": "Int", "kind": "SCALAR", "ofType": null}}
   {"name": "last", "type": {"name": "Int", "kind": "SCALAR", "ofType": null}}
   {"name": "orderBy", "type": {"name": "ProjectV2ItemOrder", "kind": "INPUT_OBJECT", "ofType": null}}
   {"name": "archivedStates", "type": {"name": null, "kind": "LIST", "ofType": {"name": null}}}
   {"name": "query", "type": {"name": "String", "kind": "SCALAR", "ofType": null}}
views:
   {"name": "after", "type": {"name": "String", "kind": "SCALAR", "ofType": null}}
   {"name": "before", "type": {"name": "String", "kind": "SCALAR", "ofType": null}}
   {"name": "first", "type": {"name": "Int", "kind": "SCALAR", "ofType": null}}
   {"name": "last", "type": {"name": "Int", "kind": "SCALAR", "ofType": null}}
   {"name": "orderBy", "type": {"name": "ProjectV2ViewOrder", "kind": "INPUT_OBJECT", "ofType": null}}
```

`ProjectV2ItemOrder` is an input object of two non-null fields (formatted from the response):

```text
{"name": "ProjectV2ItemOrder", "kind": "INPUT_OBJECT", "inputFields": [
  {"name": "field", "type": {"name": null, "kind": "NON_NULL", "ofType": {"name": "ProjectV2ItemOrderField"}}},
  {"name": "direction", "type": {"name": null, "kind": "NON_NULL", "ofType": {"name": "OrderDirection"}}}]}
```

`ProjectV2ItemOrderField` has exactly one enum value, `POSITION`.

(d, organization fallback) `user(login: "raidenyn")` works for the board (it is a user-owned
project). `organization(login: "raidenyn")` fails (exit 1), so the fallback is not the working
branch here. The exact output:

```text
{"data":{"organization":null},"errors":[{"type":"NOT_FOUND","path":["organization"],"locations":[{"line":3,"column":3}],"message":"Could not resolve to an Organization with the login of 'raidenyn'."}]}
gh: Could not resolve to an Organization with the login of 'raidenyn'.
```

(the first line is stdout, the second is stderr).

(e)

```json
{"id": "PVT_kwHOAETbkc4BhcMP",
 "field": {"id": "PVTSSF_lAHOAETbkc4BhcMPzhgX7CQ", "name": "Status", "options": [
   {"id": "b041803b", "name": "Backlog"}, {"id": "8b5f13b8", "name": "Ready"},
   {"id": "50bb73dd", "name": "In Progress"}, {"id": "ff26d693", "name": "In Review"},
   {"id": "dbdc76fd", "name": "Blocked"}, {"id": "4b06252b", "name": "Done"}]}}
```

Project field data types, as printed by the `fields(first: 30)` call (`name dataType
option-count`):

```text
Title TITLE 0
Assignees ASSIGNEES 0
Status SINGLE_SELECT 6
Labels LABELS 0
Linked pull requests LINKED_PULL_REQUESTS 0
Milestone MILESTONE 0
Repository REPOSITORY 0
Reviewers REVIEWERS 0
Parent issue PARENT_ISSUE 0
Sub-issues progress SUB_ISSUES_PROGRESS 0
Created CREATED 0
Updated UPDATED 0
Closed CLOSED 0
Implementation order NUMBER 0
Phase SINGLE_SELECT 7
Area SINGLE_SELECT 5
Release scope SINGLE_SELECT 2
Gate SINGLE_SELECT 4
```

(f) What is recoverable and what is not:

- View 2 carries no sort, grouping or filter, so picking the next task needs no reconstruction:
  query (a)'s order is the board order.
- `views` exposes `sortByFields`, `groupByFields` and `filter` for any view, so the *settings* are
  always readable. The API cannot *apply* a field sort: `orderBy` accepts only
  `{field: POSITION, direction: ASC|DESC}`, so a view's field sort has to be reproduced by
  fetching `fieldValueByName` values (as in (d)) and sorting locally.
- A filter string can be handed to `items(query:)` verbatim (`status:Backlog`,
  `status:Backlog,Ready area:Android` both work), which gives the matching items in `POSITION`
  order. But the filter is unvalidated: an unknown qualifier or option silently matches nothing or
  everything (`bogus:value` → 0, `no:such` → 30) instead of returning an error, so a compound or
  unfamiliar filter cannot be trusted on its own. No view on this board carries a filter, so
  verbatim passthrough of a *view's* `filter` value could not be exercised end to end.

### Conclusion

`user(login:)` + `projectV2(number:)` is the working read path for this board (the
`organization(login:)` fallback errors for a user account); the four views have no sort, grouping
or filter, and the API can read sort/group/filter settings and per-item values but can only order
by `POSITION`, while filter strings are accepted unvalidated — so several sort keys, any grouping
and any filter that is not a single simple field match stay on the ask-the-user fallback of D8.

## 1.2 Pull request and checks

### Command

Read the base branch's required checks from rulesets (works for a non-admin token):

```bash
gh api repos/raidenyn/veles-android/rules/branches/master
```

The classic branch-protection endpoint, for comparison:

```bash
gh api repos/raidenyn/veles-android/branches/master/protection/required_status_checks
gh api repos/raidenyn/veles-android/branches/master/protection
```

One monitoring read (a polling cycle, not one command), on PR 110:

```bash
gh pr view 110 --json state,headRefOid,statusCheckRollup,reviewDecision,reviews,comments
gh api repos/raidenyn/veles-android/pulls/110/comments --paginate
gh api graphql -f query='
query($o: String!, $r: String!, $num: Int!) {
  repository(owner: $o, name: $r) {
    pullRequest(number: $num) {
      reviewThreads(first: 100) {
        totalCount
        nodes {
          isResolved
          isOutdated
          path
          line
          comments(first: 50) { nodes { author { login } body createdAt } }
        }
      }
    }
  }
}' -f o=raidenyn -f r=veles-android -F num=110
```

Comment trust:

```bash
gh api repos/raidenyn/veles-android/collaborators/raidenyn-shadow/permission
gh api repos/raidenyn/veles-android/collaborators/torvalds/permission
```

**Unexecuted write forms — recorded, never run** (issue 93, project 3, Status field
`PVTSSF_lAHOAETbkc4BhcMPzhgX7CQ`, item `PVTI_lAHOAETbkc4BhcMPzg3-AAA`):

```bash
# Set Status by node ids (single-select option: In Progress 50bb73dd, Done 4b06252b)
gh project item-edit --id PVTI_lAHOAETbkc4BhcMPzg3-AAA \
  --project-id PVT_kwHOAETbkc4BhcMP \
  --field-id PVTSSF_lAHOAETbkc4BhcMPzhgX7CQ \
  --single-select-option-id 50bb73dd

# The same write as a GraphQL mutation (input shape checked by introspection)
gh api graphql -f query='
mutation($p: ID!, $i: ID!, $f: ID!, $v: String!) {
  updateProjectV2ItemFieldValue(
    input: {projectId: $p, itemId: $i, fieldId: $f, value: {singleSelectOptionId: $v}}
  ) { projectV2Item { id } }
}' -f p=PVT_kwHOAETbkc4BhcMP -f i=PVTI_lAHOAETbkc4BhcMPzg3-AAA \
   -f f=PVTSSF_lAHOAETbkc4BhcMPzhgX7CQ -f v=50bb73dd

# Close the issue with a closing comment
gh issue close 93 --reason completed --comment "Closeout: merged in PR #<n>."
```

### Result

`rules/branches/master` → exit 0, five rules from ruleset `26950949` (`raidenyn/veles-android`):
`deletion`, `non_fast_forward`, `pull_request`, `required_status_checks`, `required_linear_history`.
The required checks are exactly:

```json
{"type": "required_status_checks",
 "parameters": {"strict_required_status_checks_policy": true,
   "do_not_enforce_on_create": false,
   "required_status_checks": [{"context": "pr-title", "integration_id": 15368},
                              {"context": "ci-ok", "integration_id": 15368}]}}
```

The `pull_request` rule requires one approving review, code-owner review, stale-review dismissal,
thread resolution and squash-only merges. This is the only working source: the classic endpoints
return the permission error, on an account whose repository permissions are
`{"admin":false,"maintain":false,"pull":true,"push":true,"triage":true}`:

```text
{"message":"Not Found","documentation_url":"https://docs.github.com/rest/branches/branch-protection#get-status-checks-protection","status":"404"}
gh: Not Found (HTTP 404)
{"message":"Not Found","documentation_url":"https://docs.github.com/rest/branches/branch-protection#get-branch-protection","status":"404"}
gh: Not Found (HTTP 404)
```

A 404 here means "no classic branch protection, or not enough permission": the two are not
distinguishable, so the rulesets call is authoritative and, when it also fails, the closeout must
ask the user (D10).

`gh pr view 110 --json …` → `state MERGED`, `headRefOid 097455257c0dcd020c7228d84ed8031129439e28`,
`reviewDecision APPROVED`, 34 reviews (33 `COMMENTED`, one `APPROVED`; each review carries
`author.login`, `state` and `commit.oid`, so a review can be tied to the head it saw), 0
conversation comments, and a rollup of 6 checks: `release-build SKIPPED`, `lint-check SUCCESS`,
`unit-tests SUCCESS`, `instrumented-tests SUCCESS`, `web-extension SUCCESS`, `rust SUCCESS`. The
same call on PR 151 → `MERGED`, `headRefOid dab0261acf9468357c4e18d14e794615e3c0638a`,
`APPROVED`, one `APPROVED` review, 0 comments, and a 23-entry rollup whose `ci-ok` entry is
`{"__typename": "CheckRun", "conclusion": "SUCCESS", "status": "COMPLETED", "name": "ci-ok",
"detailsUrl": "…/actions/runs/43603979049/job/132474624878"}`. On the open PR 112 the same call →
`OPEN`, `reviewDecision REVIEW_REQUIRED`, no reviews, and a rollup with **no `ci-ok` entry at all**
(only `lint-check`, `unit-tests`, `instrumented-tests`, `web-extension`, `rust` and skipped jobs),
so a required check that never ran is simply absent and must count as not green.

`pulls/110/comments --paginate` → 38 inline review comments, each with `id`, `user.login`,
`path`, `line`, `body` (`line` is `null` for an outdated comment); the same call on PR 151 → `[]`.
GraphQL `reviewThreads` on 110 → `totalCount: 15`, nodes such as
`{"isResolved": true, "isOutdated": true, "path": "build.gradle.kts", "line": null}` with two to
four comments each; on PR 151 → `totalCount: 0`. Of the 77 most recently updated PRs, 16 carry
threads, so the thread shape above is real data, not a guess.

Collaborator permission: `raidenyn-shadow` → `"permission": "write"` (`role_name` `write`).
A user with no access returns **`read`, not 404**: `torvalds` →
`{"permission":"read","user":{…},"role_name":"read"}` with
`{"admin":false,"maintain":false,"push":false,"triage":false,"pull":true}`. The trust test must
therefore compare the returned `permission` value (`admin`, `maintain` or `write`), never the HTTP
status.

What each call of one polling cycle supplies:

| Call | Supplies |
|---|---|
| `gh pr view <n> --json state,headRefOid,statusCheckRollup,reviewDecision,reviews,comments` | lifecycle (`OPEN`/`MERGED`/`CLOSED`), current head commit, every check's name and conclusion, the review decision, reviews with their author and reviewed commit, conversation comments |
| `gh api repos/{owner}/{repo}/pulls/<n>/comments --paginate` | inline code-review comments with file, line and body |
| GraphQL `reviewThreads` | each thread's `isResolved`/`isOutdated`, file, line and comments |
| `gh api repos/{owner}/{repo}/rules/branches/{base}` | the required check contexts (read once per base branch) |
| `gh api repos/{owner}/{repo}/collaborators/{login}/permission` | the commenter's permission, for the trust filter |

**Unexecuted write forms — recorded, never run.** No output exists for these; they were not
executed. `gh project item-edit --help` confirms the flags `--id`, `--project-id`, `--field-id`
and `--single-select-option-id`, and introspection confirms
`UpdateProjectV2ItemFieldValueInput {projectId: ID!, itemId: ID!, fieldId: ID!, value:
ProjectV2FieldValue!}`. `gh issue close --help` confirms `-c, --comment` and
`-r, --reason {completed|not planned|duplicate}`.

Missing `project` scope — **not reproduced in execution**: the token holds `project`, and removing
it would edit the user's `gh` auth, which a read-only spike must not do. What follows is source
inspection of the installed version, labelled as such; the procedure doc should present it as gh's
text, not as an observed transcript.

Source inspection of the installed `gh` 2.104.0 (`gh version 2.104.0 (2026-11-12)`):

```bash
curl -sS https://raw.githubusercontent.com/cli/cli/v2.104.0/pkg/cmd/project/shared/queries/queries.go \
  | grep -n -B12 -A5 "missing required scopes"
```

```text
1704-	if gerr, ok := errors.AsType[api.GraphQLError](err); ok {
1705-		missing := set.NewStringSet()
1706-		for _, e := range gerr.Errors {
1707-			if e.Type != "INSUFFICIENT_SCOPES" {
1708-				continue
1709-			}
1710-			missing.AddValues(requiredScopesFromServerMessage(e.Message))
1711-		}
1712-		if missing.Len() > 0 {
1713-			s := missing.ToSlice()
1714-			// TODO: this duplicates parts of generateScopesSuggestion
1715-			return fmt.Errorf(
1716:				"error: your authentication token is missing required scopes %v\n"+
1717-					"To request it, run:  gh auth refresh -s %s",
1718-				s,
1719-				strings.Join(s, ","))
1720-		}
1721-	}
```

That branch runs on a GraphQL `INSUFFICIENT_SCOPES` error and formats the scopes GitHub names into
`%v` (a slice, which renders as `[project]` for one element) and `%s`. Rendering the template for a
project write — again **not executed**:

```text
error: your authentication token is missing required scopes [project]
To request it, run:  gh auth refresh -s project
```

```bash
curl -sS https://raw.githubusercontent.com/cli/cli/v2.104.0/api/client.go \
  | grep -n -B6 -A4 "This API operation needs"
```

```text
398-	for s := range strings.SplitSeq(endpointNeedsScopes, ",") {
399-		s = strings.TrimSpace(s)
400-		if _, gotScope := gotScopes[s]; s == "" || gotScope {
401-			continue
402-		}
403-		return fmt.Sprintf(
404:			"This API operation needs the %[1]q scope. To request it, run:  gh auth refresh -h %[2]s -s %[1]s",
405-			s,
406-			ghauth.NormalizeHostname(hostname),
407-		)
408-	}
```

Rendered with `%[1]q` → `"project"` and the hostname `github.com` (**not executed**):

```text
This API operation needs the "project" scope. To request it, run:  gh auth refresh -h github.com -s project
```

The `gh api graphql` path differs: GraphQL errors arrive in a 200 body and are printed as
`gh: <server message>` (the form observed above with the organization `NOT_FOUND`), so there the
user sees GitHub's own scope message rather than either template.

The fix command in both cases is `gh auth refresh -s project`.

### Conclusion

Required checks come from `rules/branches/{base}` (`pr-title` and `ci-ok` here) because the
classic protection endpoints return `404 Not Found` for a non-admin token without distinguishing
"absent" from "forbidden"; one cycle of `gh pr view --json`, the paginated `pulls/<n>/comments`
call, the GraphQL `reviewThreads` query, the rules call and the per-commenter permission call
covers lifecycle, head, checks, reviews, threads and trusted comments, with a missing required
check read as not green, a commenter's `read` permission counted as untrusted, and the three
write forms plus the missing-scope text recorded unexecuted.

## 1.3 Hash command

### Command

The normative command from the plan's Global Constraints, unchanged:

```bash
python3 -I -c "import hashlib,re,sys;t=open(sys.argv[1],'rb').read().decode('utf-8').replace('\r\n','\n').replace('\r','\n');t=re.sub(r'(?m)^([ \t]*[-*] )\[[xX ]\]',r'\1[ ]',t);print(hashlib.sha256(t.encode('utf-8')).hexdigest())" FILE
```

Sample generation, in a scratch directory outside the repository (`/tmp/opencode/hash-spike`):

```bash
cd /tmp/opencode/hash-spike
printf '%s\n' '- [ ] a' '* [ ] b' '  - [ ] c' > baseline.md      # LF, unticked
sed 's/$/\r/' baseline.md > crlf.md                             # only the line endings change
sed -e 's/- \[ \] a/- [x] a/' -e 's/\* \[ \] b/* [X] b/' \
    -e 's/- \[ \] c/- [x] c/' baseline.md > ticked.md           # only the checkbox states change
sed 's/\* \[ \] b/* [ ] bb/' baseline.md > changed.md           # only one word changes
for f in baseline.md crlf.md ticked.md changed.md; do
  printf '%s ' "$f"
  python3 -I -c "import hashlib,re,sys;t=open(sys.argv[1],'rb').read().decode('utf-8').replace('\r\n','\n').replace('\r','\n');t=re.sub(r'(?m)^([ \t]*[-*] )\[[xX ]\]',r'\1[ ]',t);print(hashlib.sha256(t.encode('utf-8')).hexdigest())" "$f"
done
```

Extra normalization probes (tab indentation, and the `+` marker the design says is *not*
normalized):

```bash
printf '\t- [ ] t\n' > tab-open.md; printf '\t- [x] t\n' > tab-done.md
printf '+ [ ] a\n' > plus.md; printf -- '- [ ] a\n' > dash.md
```

The change's planning files:

```bash
cd /home/dev/.t3/worktrees/veles-android/t3-c93af898-efe5b9588f6c9c28
for f in brainstorm.md proposal.md design.md; do
  printf '%s ' "$f"
  python3 -I -c "import hashlib,re,sys;t=open(sys.argv[1],'rb').read().decode('utf-8').replace('\r\n','\n').replace('\r','\n');t=re.sub(r'(?m)^([ \t]*[-*] )\[[xX ]\]',r'\1[ ]',t);print(hashlib.sha256(t.encode('utf-8')).hexdigest())" "openspec/changes/extend-superspec-workflow/$f"
done
```

Checkbox normalization on real planning content: the change's own `plan.md` and `tasks.md` hold
68 and 31 unticked `- [ ]` items, and the sample above covers the `- [x]`, `* [X]` and nested
`  - [ ]` forms the design's grammar names.

Reading the recorded approvals — the last `Approved content` line of each reviewer's record:

```bash
cd openspec/changes/extend-superspec-workflow
for f in reviews/{brainstorm,proposal,design}-{sol,opus}.md; do
  echo "== $f"; grep -n "Approved content" "$f" | tail -n 1
done
```

and comparing each computed value with the full recorded hash mechanically (same normative
one-liner, same records):

```bash
for a in brainstorm proposal design; do
  computed=$(python3 -I -c "import hashlib,re,sys;t=open(sys.argv[1],'rb').read().decode('utf-8').replace('\r\n','\n').replace('\r','\n');t=re.sub(r'(?m)^([ \t]*[-*] )\[[xX ]\]',r'\1[ ]',t);print(hashlib.sha256(t.encode('utf-8')).hexdigest())" "$a.md")
  for r in reviews/$a-sol.md reviews/$a-opus.md; do
    recorded=$(grep "Approved content: $a.md" "$r" | tail -n 1 | sed -E 's/.*sha256 ([0-9a-f]{64}).*/\1/')
    if [ "$computed" = "$recorded" ]; then
      echo "MATCH    $a.md $r $computed"
    else
      echo "MISMATCH $a.md $r computed=$computed recorded=$recorded"
    fi
  done
done
```

### Result

The four samples, and that only one change of content moves the hash:

```text
baseline.md 2c0df23be2c5aaa4a5db887c6af876efb92a912ee987d2390ad5cbd73eb57c61
crlf.md     2c0df23be2c5aaa4a5db887c6af876efb92a912ee987d2390ad5cbd73eb57c61
ticked.md   2c0df23be2c5aaa4a5db887c6af876efb92a912ee987d2390ad5cbd73eb57c61
changed.md  82f034238267e2a5cd79bc9564783f6915324cebd229757cfa490d5ca0e2228b
```

The variants really differ only in the intended way:
`diff <(tr -d '\r' < crlf.md) baseline.md` is empty; `diff baseline.md ticked.md` shows the three
box states (`- [x] a`, `* [X] b`, `  - [x] c`); `diff baseline.md changed.md` shows only
`* [ ] b` → `* [ ] bb`. Extra probes: `tab-open.md` and `tab-done.md` both
`5db90f78d8dbe36c709231a4d6eb5bc3c7276bd0383a854b89bfba0060249437` (a tab-indented box is
normalized), while `dash.md` `e4020b38aef8b24176ab50b1234944d669af801acfeb2e7209dd07c94a917404`
and `plus.md` `fdd6d7eb60762068b3981a08697e1934aa433170f842ccf147696b9718dbde78` differ (the `+`
marker is a real edit, as the design says).

The change's planning files:

```text
brainstorm.md a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661
proposal.md   8ec310f828da45f2eea046d672379083b8335a56b0ecab5a00df303c1e824946
design.md     dbe2950ea84021b3d17c2d5e8662fcc3d465bf64d8aeb8e0fc1da62c4ac23431
```

The last `Approved content` line of each record, full text as read:

```text
== reviews/brainstorm-sol.md
95:Approved content: brainstorm.md sha256 a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661 (raw bytes).
== reviews/brainstorm-opus.md
103:Approved content: brainstorm.md sha256 a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661 (format normalization of the earlier "Final state" line; no new review)
== reviews/proposal-sol.md
41:Approved content: proposal.md sha256 8ec310f828da45f2eea046d672379083b8335a56b0ecab5a00df303c1e824946
== reviews/proposal-opus.md
31:Approved content: proposal.md sha256 8ec310f828da45f2eea046d672379083b8335a56b0ecab5a00df303c1e824946
== reviews/design-sol.md
62:The user accepted the final state (option 1). Approved content: design.md sha256 dbe2950ea84021b3d17c2d5e8662fcc3d465bf64d8aeb8e0fc1da62c4ac23431. Late edits not seen by Sol: the four low clarifications from Opus pass 3.
== reviews/design-opus.md
58:The user accepted the final state (option 1). Approved content: design.md sha256 dbe2950ea84021b3d17c2d5e8662fcc3d465bf64d8aeb8e0fc1da62c4ac23431. Late edits not seen by Sol: the four low clarifications from Opus pass 3.
```

The mechanical comparison, comparing the full 64-character values:

```text
MATCH    brainstorm.md reviews/brainstorm-sol.md a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661
MATCH    brainstorm.md reviews/brainstorm-opus.md a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661
MATCH    proposal.md reviews/proposal-sol.md 8ec310f828da45f2eea046d672379083b8335a56b0ecab5a00df303c1e824946
MATCH    proposal.md reviews/proposal-opus.md 8ec310f828da45f2eea046d672379083b8335a56b0ecab5a00df303c1e824946
MATCH    design.md reviews/design-sol.md dbe2950ea84021b3d17c2d5e8662fcc3d465bf64d8aeb8e0fc1da62c4ac23431
MATCH    design.md reviews/design-opus.md dbe2950ea84021b3d17c2d5e8662fcc3d465bf64d8aeb8e0fc1da62c4ac23431
```

All six are full-value matches: each computed hash equals the complete recorded hash, not a
prefix of it. (The two design records name the user-accepted final state.)

Portability: `python3 -I` (3.14.4 here) reports `isolated 1 no_user_site 1 ignore_environment 1`,
and the one-liner imports only `hashlib`, `re` and `sys` — no `sha256sum`, no `sed`, no shell
feature beyond passing the file path, so it behaves the same on macOS and Windows.

### Conclusion

The normative hash command reproduces every last recorded approval hash, gives one value for LF,
CRLF and checkbox-ticked copies (including nested and tab-indented boxes) and a different value
for a one-word edit, and depends only on `python3 -I` and the standard library.

## 1.4 Validate tolerance

### Command

```bash
cd /home/dev/.t3/worktrees/veles-android/t3-c93af898-efe5b9588f6c9c28
echo 'placeholder: throwaway file for spike 1.4' > openspec/changes/extend-superspec-workflow/tracker.md
openspec validate --all 2>&1 | tee /tmp/opencode/spike1/validate.txt
grep -c "tracker\.md" /tmp/opencode/spike1/validate.txt
grep -c "reviews/" /tmp/opencode/spike1/validate.txt
grep -ci "error" /tmp/opencode/spike1/validate.txt
rm openspec/changes/extend-superspec-workflow/tracker.md
```

The throwaway `tracker.md` was present only for the validate run and was deleted afterwards
(`git status --porcelain` was empty again). The change directory already contained `reviews/` with
twelve records during the run.

### Result

`openspec validate --all` (2.0.0) exited 0 with `Totals: 16 passed, 0 failed (16 items)`, and the
change itself was validated with the throwaway file in place:

```text
✓ change/extend-superspec-workflow
Totals: 16 passed, 0 failed (16 items)
```

The 55 warning lines are all pre-existing content warnings — `Requirement text is very long (>500
characters)` and `Purpose section is still a placeholder` — on specs, not about files in the change
directory. `grep -c "tracker\.md"` → `1`, and that single line is the pre-existing requirement
title warning `⚠ [WARNING] superspec-tracker-intake/spec.md: ADDED "tracker.md records the tracked
association": …`, not a complaint about the throwaway file. `grep -c "reviews/"` → `0`; no line
mentions `reviews/`, and no line contains `error`.

### Conclusion

`openspec validate --all` accepts a change directory containing `tracker.md` and `reviews/` —
neither is validated, neither produces a warning, and adding them cannot break validation.
