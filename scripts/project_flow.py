#!/usr/bin/env python3
"""project_flow: derive GitHub Project statuses from facts (stdlib only).

Run as `python3 -I scripts/project_flow.py <sync|next|start|followup|check> ...`.
Exit 0 success, 1 a gh error, 2 usage, 3 a stop (`stop: <reason>: <detail>` on stdout;
`check` with setup facts ends in `stop: setup-incomplete`; `start` ends in `stop: collision` when
another login is assigned and `stop: assignment-failed` when its own assignment did not take effect;
`stop: write-failed` when a write was rejected: sync after attempting and printing every other write,
next and start before any selection or claim, followup after its created:/missing: lines; every
reason is in STOP_REASONS). sync, next, start and followup run
only on the project the repository variable PROJECT_URL names; each public entry point gates itself.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol

DEFAULT_CONFIG = ".github/project-flow.json"
ROLE_KEYS = ("backlog", "ready", "in_progress", "in_review", "blocked", "done")
CONFIG_KEYS = ("status_field", "roles", "order_field", "blocked_label")
STOP_REASONS = (
    "not-enabled", "variable-unreadable", "nothing-ready", "tie", "missing-order",
    "incomplete-read", "not-ready", "collision", "missing-option", "config-invalid",
    "resume-disagrees", "no-order-room", "parent-unordered", "parent-as-blocker",
    "missing-scope", "setup-incomplete", "assignment-failed", "write-failed",
)
SCOPE_FIX = "run gh auth refresh -s project"

_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_ISSUE_REF = re.compile(r"#\d+")
_NODE_ID = re.compile(r"[A-Z]{1,4}_[A-Za-z0-9_-]{8,}")
_DIGITS = re.compile(r"\d+")


# GraphQL queries. Every connection reads `first: $first`, which defaults to 100; only the
# fixture recording passes a smaller page size. `OWNER` is replaced by `user` or `organization`.
_PAGE = "pageInfo { hasNextPage endCursor }"
# A closing pull request: its head repository and author association decide whether it is trusted.
_PULL_NODE = ("number state isDraft repository { nameWithOwner } headRepository { nameWithOwner } "
              "authorAssociation milestone { title }")
_ISSUE_FACTS = """
fragment IssueFacts on Issue {
  id databaseId number state stateReason(enableDuplicate: true)
  repository { nameWithOwner }
  milestone { title }
  labels(first: $first) { %(page)s nodes { name } }
  assignees(first: $first) { %(page)s nodes { login } }
  closedByPullRequestsReferences(first: $first, includeClosedPrs: true) {
    %(page)s nodes { %(pull)s } }
  blockedBy(first: $first) {
    %(page)s nodes { number state stateReason(enableDuplicate: true) repository { nameWithOwner } } }
  parent { number repository { nameWithOwner } }
  subIssues(first: $first) { %(page)s nodes { number repository { nameWithOwner } } }
}""" % {"page": _PAGE, "pull": _PULL_NODE}
_ITEM_FIELDS = """
  id type
  status: fieldValueByName(name: $status) { __typename
    ... on ProjectV2ItemFieldSingleSelectValue { name optionId } }
  order: fieldValueByName(name: $order) { __typename ... on ProjectV2ItemFieldNumberValue { number } }
  content { __typename ... on Issue { ...IssueFacts } }"""
FIELDS_QUERY = """query ProjectFields($login: String!, $number: Int!, $status: String!, $order: String!) {
  OWNER(login: $login) { projectV2(number: $number) { id
    status: field(name: $status) { __typename ... on ProjectV2SingleSelectField { id name options { id name } } }
    order: field(name: $order) { __typename ... on ProjectV2Field { id name dataType }
      ... on ProjectV2SingleSelectField { id } ... on ProjectV2IterationField { id } }
  } }
}"""
WORKFLOWS_QUERY = """query ProjectWorkflows($login: String!, $number: Int!, $first: Int = 100, $after: String) {
  OWNER(login: $login) { projectV2(number: $number) {
    workflows(first: $first, after: $after) { %s nodes { name enabled } }
  } }
}""" % _PAGE
ITEMS_QUERY = """query ProjectItems($login: String!, $number: Int!, $status: String!, $order: String!,
    $first: Int = 100, $after: String) {
  OWNER(login: $login) { projectV2(number: $number) {
    items(first: $first, after: $after) { %s nodes { %s } }
  } }
  rateLimit { cost remaining resetAt }
}""" % (_PAGE, _ITEM_FIELDS) + _ISSUE_FACTS
ITEM_QUERY = """query ProjectItem($item: ID!, $status: String!, $order: String!, $first: Int = 100) {
  node(id: $item) { ... on ProjectV2Item { %s
    project { order: field(name: $order) { __typename ... on ProjectV2Field { id dataType }
      ... on ProjectV2SingleSelectField { id } ... on ProjectV2IterationField { id } } } } }
}""" % _ITEM_FIELDS + _ISSUE_FACTS
# The field(name:) lookups whose NOT_FOUND means "the project has no such field": the complete
# response paths from the data root, per query, passed to the transport as `absent_ok`.
FIELDS_ABSENT_OK = {owner: frozenset({(owner, "projectV2", "status"), (owner, "projectV2", "order")})
                    for owner in ("user", "organization")}
ITEM_ABSENT_OK = frozenset({("node", "project", "order")})
# Follow-up page of one nested connection of an issue, by the issue node id.
_CONNECTION_NODES = {
    "labels": "nodes { name }",
    "assignees": "nodes { login }",
    "closedByPullRequestsReferences": "nodes { %s }" % _PULL_NODE,
    "blockedBy": (
        "nodes { number state stateReason(enableDuplicate: true) repository { nameWithOwner } }"),
    "subIssues": "nodes { number repository { nameWithOwner } }",
    "projectItems": "nodes { id project { id } }",
}
_CONNECTION_ARGS = {"closedByPullRequestsReferences": ", includeClosedPrs: true"}
ISSUE_CONNECTION_QUERY = """query IssueConnection_%(name)s($id: ID!, $first: Int = 100, $after: String) {
  node(id: $id) { ... on Issue { %(name)s(first: $first, after: $after%(args)s) { %(page)s %(nodes)s } } }
}"""
TEXT_QUERY = """query IssueText($owner: String!, $name: String!, $number: Int!, $first: Int = 100,
    $after: String) {
  repository(owner: $owner, name: $name) { issue(number: $number) { body
    comments(first: $first, after: $after) { %s nodes { body } } } }
}""" % _PAGE
CLOSING_ISSUES_QUERY = """query PullClosingIssues($owner: String!, $name: String!, $number: Int!,
    $first: Int = 100, $after: String) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) {
    closingIssuesReferences(first: $first, after: $after) {
      %s nodes { number repository { nameWithOwner } milestone { title } } } } }
}""" % _PAGE
SUB_ISSUES_QUERY = """query IssueSubIssues($owner: String!, $name: String!, $number: Int!,
    $first: Int = 100, $after: String) {
  repository(owner: $owner, name: $name) { issue(number: $number) {
    subIssues(first: $first, after: $after) { %s nodes { number repository { nameWithOwner } } } } }
}""" % _PAGE
# The links of one issue followup creates or resumes, and the projects it is on.
FOLLOWUP_ISSUE_QUERY = """query FollowupIssue($owner: String!, $name: String!, $number: Int!,
    $first: Int = 100) {
  repository(owner: $owner, name: $name) { issue(number: $number) { ...IssueFacts
    projectItems(first: $first) { %s nodes { id project { id } } } } }
}""" % _PAGE + _ISSUE_FACTS


class GhError(Exception):
    def __init__(self, message: str, stdout: str = ""):
        super().__init__(message)
        self.message = message
        self.stdout = stdout  # what gh printed on stdout before failing


class Stop(Exception):
    def __init__(self, reason: str, detail: str = ""):
        if reason not in STOP_REASONS:
            raise ValueError("unknown stop reason: %r" % (reason,))
        super().__init__("%s: %s" % (reason, detail))
        self.reason = reason
        self.detail = detail

    def __eq__(self, other):
        return isinstance(other, Stop) and (self.reason, self.detail) == (other.reason, other.detail)

    def __hash__(self):
        return hash((self.reason, self.detail))


class Transport(Protocol):
    def graphql(self, query: str, variables: dict, *, absent_ok: frozenset = frozenset()) -> dict: ...

    def rest(self, method: str, path: str, body: Optional[dict] = None) -> Any: ...


class GhTransport:
    """Shells to the gh CLI."""

    @staticmethod
    def _run(args, stdin=None):
        try:
            done = subprocess.run(["gh"] + args, input=stdin, capture_output=True, text=True)
        except OSError as exc:
            raise GhError("cannot run gh: %s" % exc)
        if done.returncode != 0:
            raise GhError((done.stderr or done.stdout).strip() or "gh exited %d" % done.returncode,
                          done.stdout or "")
        return done.stdout

    def graphql(self, query: str, variables: Optional[dict] = None, *,
                absent_ok: frozenset = frozenset()) -> dict:
        """absent_ok: the full response paths of field lookups whose absence (NOT_FOUND, null)
        is an answer, not a failure. The request always goes as one JSON body on stdin, so every
        variable keeps its JSON type (null, string, number, boolean, list, object); gh's -f/-F would
        send a float or a list as a string."""
        body = json.dumps({"query": query, "variables": dict(variables or {})})
        try:
            return self._decode(self._run(["api", "graphql", "--input", "-"], body))
        except GhError as err:
            absent = _absent_field_response(err.stdout, absent_ok)
            if absent is None:
                raise
            return absent

    def rest(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        args = ["api", "-X", method, path]
        stdin = None
        if body is not None:
            args += ["--input", "-"]
            stdin = json.dumps(body)
        return self._decode(self._run(args, stdin))

    @staticmethod
    def _decode(text: str) -> Any:
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except ValueError as exc:
            raise GhError("invalid JSON from gh: %s" % exc)


def _absent_field_response(stdout: str, absent_ok: frozenset) -> Optional[dict]:
    """GitHub answers `field(name:)` for a field the project lacks with a null value and a
    NOT_FOUND error, and gh exits 1. When every error is a NOT_FOUND whose complete path is one
    the caller listed in `absent_ok`, and the value there is null, the data is the answer: return
    it with the errors moved to `_errors`. Anything else is None (the failure stands)."""
    if not absent_ok:
        return None
    try:
        response = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(response, dict) or not isinstance(response.get("data"), dict):
        return None
    errors = response.get("errors")
    if not isinstance(errors, list) or not errors:
        return None
    for error in errors:
        if not isinstance(error, dict) or error.get("type") != "NOT_FOUND":
            return None
        path = error.get("path")
        if not isinstance(path, list) or tuple(path) not in absent_ok:
            return None
        value = response["data"]
        for key in path:
            if not isinstance(value, dict) or key not in value:
                return None
            value = value[key]
        if value is not None:
            return None
    return {"data": response["data"], "_errors": errors}


@dataclass(frozen=True)
class Config:
    status_field: str
    roles: dict
    order_field: str
    blocked_label: str


def _bad_value(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty string"
    if _URL.search(value):
        return "must not be a URL"
    if _ISSUE_REF.search(value):
        return "must not contain an issue reference"
    if value != value.strip():
        return "must not have leading or trailing whitespace"
    if _NODE_ID.fullmatch(value):
        return "must not be a node id"
    if _DIGITS.fullmatch(value):
        return "must not be all digits"
    return None


def validate_config(raw: Any) -> list:
    if not isinstance(raw, dict):
        return ["config must be a JSON object"]
    errors = []
    for key in raw:
        if key not in CONFIG_KEYS:
            errors.append("%s: unknown key" % key)
    for key in ("status_field", "order_field", "blocked_label"):
        if key not in raw:
            errors.append("%s: missing" % key)
        else:
            problem = _bad_value(raw[key])
            if problem:
                errors.append("%s: %s" % (key, problem))
    roles = raw.get("roles")
    if roles is None:
        errors.append("roles: missing")
    elif not isinstance(roles, dict):
        errors.append("roles: must be an object")
    else:
        for key in roles:
            if key not in ROLE_KEYS:
                errors.append("roles.%s: unknown role" % key)
        for key in ROLE_KEYS:
            if key not in roles:
                errors.append("roles.%s: missing" % key)
            else:
                problem = _bad_value(roles[key])
                if problem:
                    errors.append("roles.%s: %s" % (key, problem))
    return errors


def load_config(path: str) -> Config:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except OSError as exc:
        raise Stop("config-invalid", "cannot read %s: %s" % (path, exc.strerror or exc))
    except ValueError as exc:
        raise Stop("config-invalid", "%s is not valid JSON: %s" % (path, exc))
    errors = validate_config(raw)
    if errors:
        raise Stop("config-invalid", "; ".join(errors))
    return Config(raw["status_field"], dict(raw["roles"]), raw["order_field"], raw["blocked_label"])


# The scope-error wordings gh and GitHub print: `gh project ...` ("missing required scopes"), a GraphQL
# INSUFFICIENT_SCOPES error ("Your token has not been granted the required scopes to execute this query.
# The 'projectV2' field requires one of the following scopes: ['read:project'] ...") and a REST 403
# ('This API operation needs the "<scope>" scope. To request it, run: gh auth refresh ...').
_SCOPE_MARKERS = ("missing required scopes", "INSUFFICIENT_SCOPES",
                  "has not been granted the required scopes")
_SCOPE_NAME = r"[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*)?"
_REST_SCOPE = re.compile(r'needs the "(%s)" scope' % _SCOPE_NAME)
_GRAPHQL_SCOPES = re.compile(r"requires one of the following scopes: \[([^\]]*)\]")
_PROJECT_SCOPES = ("project", "read:project")


def _scope_fix(scopes: list) -> str:
    """The fix command for the scopes an error names: project when it names a project scope or
    none; otherwise the first scope it names."""
    if not scopes or any(scope in _PROJECT_SCOPES for scope in scopes):
        return SCOPE_FIX
    return "run gh auth refresh -s %s" % scopes[0]


def map_gh_error(e: GhError) -> Optional[Stop]:
    """Stop("missing-scope") with the exact fix command when gh failed for a missing token scope."""
    text = "%s\n%s" % (getattr(e, "message", str(e)), getattr(e, "stdout", "") or "")
    rest = _REST_SCOPE.search(text)
    if rest:
        return Stop("missing-scope", _scope_fix([rest.group(1)]))
    if any(marker in text for marker in _SCOPE_MARKERS):
        listed = _GRAPHQL_SCOPES.search(text)
        names = re.findall(r"'(%s)'" % _SCOPE_NAME, listed.group(1)) if listed else []
        return Stop("missing-scope", _scope_fix(names))
    return None


def _scope_errors(response: Any) -> Optional[GhError]:
    """A GhError for a GraphQL response whose errors include INSUFFICIENT_SCOPES, else None."""
    errors = response.get("errors") if isinstance(response, dict) else None
    if not isinstance(errors, list):
        return None
    scoped = [e for e in errors if isinstance(e, dict) and e.get("type") == "INSUFFICIENT_SCOPES"]
    if not scoped:
        return None
    return GhError("INSUFFICIENT_SCOPES: " + "; ".join(str(e.get("message", "")) for e in scoped))


@dataclass(frozen=True)
class Blocker:
    repo: str
    number: int
    state: str
    state_reason: Optional[str]


@dataclass(frozen=True)
class PullRef:
    repo: str
    number: int
    state: str  # OPEN | CLOSED | MERGED
    is_draft: bool
    milestone: Optional[str]
    head_repo: Optional[str]  # the head repository's owner/name; None when GitHub returns null
    author_association: str   # GitHub's authorAssociation, verbatim (an unknown value is untrusted)


# The author associations whose closing pull requests count from any head repository.
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")


def trusted(p: PullRef, issue_repo: str) -> bool:
    """A closing PR counts (In Review, milestone copy) only from a branch of the issue's own
    repository or from an author associated as OWNER, MEMBER or COLLABORATOR; a null head repository
    counts only through the association. Anyone can open a fork PR whose body closes an issue."""
    same_repo = p.head_repo is not None and p.head_repo.lower() == issue_repo.lower()
    return same_repo or p.author_association in TRUSTED_ASSOCIATIONS


@dataclass(frozen=True)
class Facts:
    item_id: str
    content_id: str
    repo: str
    number: int
    state: str
    labels: frozenset
    assignees: frozenset
    closing_prs: tuple
    blockers: tuple
    order: Optional[float]
    status: Optional[str]
    parent: Optional[tuple]
    sub_issues: tuple
    milestone: Optional[str]
    database_id: int

    def inputs(self) -> tuple:
        """The derivation inputs, order-independent; the Writer compares these."""
        return (
            self.state,
            tuple(sorted(self.labels)),
            tuple(sorted(self.assignees)),
            tuple(sorted((p.repo, p.number, p.state, p.is_draft, p.head_repo or "",
                          p.author_association) for p in self.closing_prs)),
            tuple(sorted((b.repo, b.number, b.state, b.state_reason or "") for b in self.blockers)),
        )


@dataclass
class Board:
    project_id: str
    status_field_id: str
    options: dict
    order_field_id: Optional[str]
    items: list
    unreadable: list  # item ids; the reason for each is in `reasons`
    workflows: dict
    reasons: dict = field(default_factory=dict)  # item id -> why it could not be read
    rate_cost: int = 0                     # summed GraphQL cost of the items pages
    rate_remaining: Optional[int] = None   # the last remaining budget reported, None if unknown


_PROJECT_URL = re.compile(
    r"https://github\.com/(?:users|orgs)/([A-Za-z0-9][A-Za-z0-9-]*)/projects/(\d+)(?:/views/\d+)?/?")


def derive(f: Facts, cfg: Config) -> tuple[str, list[str]]:
    """The role key a Facts snapshot maps to, plus report lines. Pure; first match wins."""
    if f.state == "CLOSED":
        return "done", []
    # An abandoned blocker is reported for every open issue, whichever rule decides the role.
    lines = []
    for b in f.blockers:
        if b.state == "CLOSED" and b.state_reason == "NOT_PLANNED":
            lines.append("blocker %s#%d closed as not planned" % (b.repo, b.number))
        elif b.state == "CLOSED" and b.state_reason == "DUPLICATE":
            lines.append("blocker %s#%d closed as duplicate" % (b.repo, b.number))
    if cfg.blocked_label.lower() in f.labels:
        return "blocked", lines
    if any(p.state == "OPEN" and not p.is_draft and trusted(p, f.repo) for p in f.closing_prs):
        return "in_review", lines
    if f.assignees:
        return "in_progress", lines
    if all(b.state == "CLOSED" and b.state_reason == "COMPLETED" for b in f.blockers):
        return "ready", lines
    return "backlog", lines


def parse_project_url(url: str) -> tuple:
    """`https://github.com/{users|orgs}/<owner>/projects/<n>[/views/<v>]` -> (owner, n)."""
    bare = re.split(r"[?#]", url.strip(), maxsplit=1)[0]
    match = _PROJECT_URL.fullmatch(bare)
    if not match:
        raise Stop("config-invalid",
                   "project URL must look like https://github.com/users/<owner>/projects/<n>")
    return match.group(1), int(match.group(2))


def _split_repo(repo: str) -> tuple:
    owner, sep, name = repo.partition("/")
    if not sep or not owner or not name or "/" in name:
        raise Stop("incomplete-read", "not an owner/name repository: %s" % repo)
    return owner, name


def _query(t: Transport, query: str, variables: dict, what: str,
           absent_ok: frozenset = frozenset()) -> dict:
    """One GraphQL read. A scope error propagates (main maps it); any other failure, or a
    response with errors or without data, is an incomplete read. absent_ok: see GhTransport."""
    try:
        response = t.graphql(query, variables, absent_ok=absent_ok)
    except GhError as err:
        if map_gh_error(err) is not None:
            raise
        raise Stop("incomplete-read", "%s: %s" % (what, err.message))
    scope_error = _scope_errors(response)
    if scope_error is not None:
        raise scope_error
    if not isinstance(response, dict) or response.get("errors") or not isinstance(response.get("data"), dict):
        raise Stop("incomplete-read", "%s: no data in the response" % what)
    return response["data"]


def _dig(data: Any, path: tuple, what: str) -> Any:
    for key in path:
        if not isinstance(data, dict) or data.get(key) is None:
            raise Stop("incomplete-read", "%s: missing %s" % (what, key))
        data = data[key]
    return data


def _connection(data: Any, what: str) -> tuple:
    """(nodes, hasNextPage, endCursor) of a connection, checked."""
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list) \
            or not isinstance(data.get("pageInfo"), dict):
        raise Stop("incomplete-read", "%s: malformed connection" % what)
    page = data["pageInfo"]
    more = page.get("hasNextPage")
    if more not in (True, False) or (more and not page.get("endCursor")):
        raise Stop("incomplete-read", "%s: malformed page info" % what)
    if any(not isinstance(node, dict) for node in data["nodes"]):
        raise Stop("incomplete-read", "%s: unreadable node" % what)
    return data["nodes"], more, page.get("endCursor")


def _paginate(t: Transport, query: str, variables: dict, path: tuple, what: str,
              on_page=None) -> list:
    """Every node of the connection at `path`, following hasNextPage to the last page.
    on_page(data): called with each page's data root."""
    nodes, after = [], None
    while True:
        page_vars = dict(variables)
        if after is not None:
            page_vars["after"] = after
        data = _query(t, query, page_vars, what)
        if on_page is not None:
            on_page(data)
        page_nodes, more, after = _connection(_dig(data, path, what), what)
        nodes.extend(page_nodes)
        if not more:
            return nodes


def _issue_connection(t: Transport, issue: dict, name: str, what: str) -> list:
    """The nodes of one nested issue connection, with per-issue follow-up pages."""
    nodes, more, after = _connection(issue.get(name), "%s %s" % (what, name))
    nodes = list(nodes)
    if more:
        query = ISSUE_CONNECTION_QUERY % {"name": name, "args": _CONNECTION_ARGS.get(name, ""),
                                          "page": _PAGE, "nodes": _CONNECTION_NODES[name]}
        while more:
            data = _query(t, query, {"id": issue["id"], "after": after}, "%s %s" % (what, name))
            page_nodes, more, after = _connection(_dig(data, ("node", name), what), what)
            nodes.extend(page_nodes)
    return nodes


_MISSING = object()
_ISSUE_STATES = ("OPEN", "CLOSED")
_PR_STATES = ("OPEN", "CLOSED", "MERGED")
_CLOSED_REASONS = ("COMPLETED", "NOT_PLANNED", "DUPLICATE")
_OPEN_REASONS = (None, "REOPENED")


def _field(obj: Any, key: str, kind: Any, what: str, nullable: bool = False) -> Any:
    """obj[key], which must be present and of `kind`; None only when `nullable`. A missing
    key or a wrong type is an incomplete read, never a default."""
    value = obj.get(key, _MISSING) if isinstance(obj, dict) else _MISSING
    if value is _MISSING:
        raise Stop("incomplete-read", "%s: missing %s" % (what, key))
    if value is None and nullable:
        return None
    if kind is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        ok = isinstance(value, kind)
    if not ok:
        raise Stop("incomplete-read", "%s: %s has the wrong type" % (what, key))
    return value


def _enum(obj: Any, key: str, allowed: tuple, what: str) -> Any:
    value = obj.get(key, _MISSING) if isinstance(obj, dict) else _MISSING
    if value is _MISSING or value not in allowed:
        raise Stop("incomplete-read", "%s: unexpected %s %r" % (
            what, key, None if value is _MISSING else value))
    return value


def _ref(node: Any, what: str) -> tuple:
    repository = _field(node, "repository", dict, what)
    return _field(repository, "nameWithOwner", str, what), _field(node, "number", int, what)


def _title(obj: Any, what: str) -> Optional[str]:
    """The milestone title, None for an explicit null milestone."""
    milestone = _field(obj, "milestone", dict, what, nullable=True)
    return None if milestone is None else _field(milestone, "title", str, what + " milestone")


def _blocker(node: dict, what: str) -> Blocker:
    repo, number = _ref(node, what)
    state = _enum(node, "state", _ISSUE_STATES, what)
    reasons = _CLOSED_REASONS if state == "CLOSED" else _OPEN_REASONS
    return Blocker(repo, number, state, _enum(node, "stateReason", reasons, what))


def _pull(node: dict, what: str) -> PullRef:
    repo, number = _ref(node, what)
    head = _field(node, "headRepository", dict, what, nullable=True)
    return PullRef(repo, number, _enum(node, "state", _PR_STATES, what),
                   _field(node, "isDraft", bool, what), _title(node, what),
                   None if head is None else _field(head, "nameWithOwner", str, what + " head"),
                   _field(node, "authorAssociation", str, what))


_NUMBER_VALUE = "ProjectV2ItemFieldNumberValue"
_SELECT_VALUE = "ProjectV2ItemFieldSingleSelectValue"


_OTHER_FIELDS = ("ProjectV2SingleSelectField", "ProjectV2IterationField")
# Every member of GitHub's ProjectV2FieldType enum, the type of ProjectV2Field.dataType. Only NUMBER
# can hold the order; a string outside this set is not a field type GitHub is known to return.
_FIELD_DATA_TYPES = frozenset((
    "ASSIGNEES", "LINKED_PULL_REQUESTS", "REVIEWERS", "LABELS", "MILESTONE", "REPOSITORY", "TITLE",
    "TEXT", "SINGLE_SELECT", "MULTI_SELECT", "NUMBER", "DATE", "ITERATION", "TRACKS", "TRACKED_BY",
    "ISSUE_TYPE", "PARENT_ISSUE", "SUB_ISSUES_PROGRESS", "CREATED", "UPDATED", "CLOSED"))


def _number_field_id(field_node: Any, what: str) -> Optional[str]:
    """The order field's id when it is a NUMBER field, None when it is absent (GitHub's explicit
    null) or a well-formed field of another type: a single-select or iteration field, or a
    ProjectV2Field of another known data type, each with its id. Anything else is an incomplete read."""
    if field_node is None:
        return None
    if not isinstance(field_node, dict):
        raise Stop("incomplete-read", "%s: order field metadata is not an object" % what)
    kind = _field(field_node, "__typename", str, what + " order field")
    if kind != "ProjectV2Field" and kind not in _OTHER_FIELDS:
        raise Stop("incomplete-read", "%s: unknown order field type %r" % (what, kind))
    field_id = _field(field_node, "id", str, what + " order field")
    if kind in _OTHER_FIELDS:
        return None
    data_type = _field(field_node, "dataType", str, what + " order field")
    if data_type not in _FIELD_DATA_TYPES:
        raise Stop("incomplete-read", "%s: unknown order field data type %r" % (what, data_type))
    return field_id if data_type == "NUMBER" else None


def _facts(t: Transport, node: dict, what: str, order_is_number: bool) -> Facts:
    """Facts of one project item whose content is an issue, reading every page.
    order_is_number: the configured order field exists and is a NUMBER field."""
    item_id = _field(node, "id", str, what)
    issue = _field(node, "content", dict, what)
    if issue.get("__typename") != "Issue":
        raise Stop("incomplete-read", "%s: content is not a readable issue" % what)
    content_id = _field(issue, "id", str, what)
    database_id = _field(issue, "databaseId", int, what)
    repo, number = _ref(issue, what)
    state = _enum(issue, "state", _ISSUE_STATES, what)
    milestone = _title(issue, what)
    parent = _field(issue, "parent", dict, what, nullable=True)
    status_value = _field(node, "status", dict, what, nullable=True)
    status = None
    if status_value is not None:
        if status_value.get("__typename") != _SELECT_VALUE:
            raise Stop("incomplete-read", "%s: status value is not a single-select value" % what)
        status = _field(status_value, "name", str, what + " status")
    order_value = _field(node, "order", dict, what, nullable=True)
    order = None
    if order_value is not None and order_is_number:
        # On a NUMBER field a value must be a typed number; {} or another type is a broken read.
        if order_value.get("__typename") != _NUMBER_VALUE:
            raise Stop("incomplete-read", "%s: order value is not a number value" % what)
        order = float(_field(order_value, "number", float, what + " order"))
    # Off a NUMBER field there is no order (reported through Board.order_field_id).
    labels = _issue_connection(t, issue, "labels", what)
    assignees = _issue_connection(t, issue, "assignees", what)
    prs = _issue_connection(t, issue, "closedByPullRequestsReferences", what)
    blockers = _issue_connection(t, issue, "blockedBy", what)
    subs = _issue_connection(t, issue, "subIssues", what)
    return Facts(
        item_id=item_id, content_id=content_id, repo=repo, number=number, state=state,
        labels=frozenset(_field(n, "name", str, what + " label").lower() for n in labels),
        assignees=frozenset(_field(n, "login", str, what + " assignee") for n in assignees),
        closing_prs=tuple(_pull(n, what + " closing PR") for n in prs),
        blockers=tuple(_blocker(n, what + " blocker") for n in blockers),
        order=order, status=status,
        parent=None if parent is None else _ref(parent, what + " parent"),
        sub_issues=tuple(_ref(n, what + " sub-issue") for n in subs),
        milestone=milestone, database_id=database_id,
    )


def _owner_query(query: str, owner_key: str) -> str:
    return query.replace("OWNER(login:", owner_key + "(login:", 1)


def read_board(t: Transport, project_url: str, cfg: Config) -> Board:
    """Every item of the project with its facts. A failed or partial page stops the whole read
    (incomplete-read); a failed follow-up for one item puts that item in `unreadable`."""
    login, number = parse_project_url(project_url)
    names = {"status": cfg.status_field, "order": cfg.order_field}
    base = {"login": login, "number": number}
    owner_key, project = None, None
    for candidate in ("user", "organization"):
        try:
            data = _query(t, _owner_query(FIELDS_QUERY, candidate), dict(base, **names),
                          "project fields", FIELDS_ABSENT_OK[candidate])
            project = _dig(data, (candidate, "projectV2"), "project fields")
        except Stop as stop:
            if candidate == "organization":
                raise Stop("incomplete-read", "cannot read project %s/%d as a user or an organization: %s"
                           % (login, number, stop.detail))
            continue
        owner_key = candidate
        break
    status = project.get("status")
    if not isinstance(status, dict) or status.get("__typename") != "ProjectV2SingleSelectField":
        raise Stop("missing-option", "single-select field %r not found on the project" % cfg.status_field)
    order_id = _number_field_id(_field(project, "order", dict, "project fields", nullable=True),
                                "project fields")
    try:
        options = {o["name"]: o["id"] for o in status["options"]}
        project_id, status_id = project["id"], status["id"]
    except (KeyError, TypeError):
        raise Stop("incomplete-read", "project fields: malformed response")
    flows = _paginate(t, _owner_query(WORKFLOWS_QUERY, owner_key), base,
                      (owner_key, "projectV2", "workflows"), "project workflows")
    workflows = {_field(w, "name", str, "project workflow"): _field(w, "enabled", bool, "project workflow")
                 for w in flows}
    rate = {"cost": 0, "remaining": None}

    def note_rate(data: dict) -> None:
        """Best effort: a missing or malformed rateLimit is unknown, never an incomplete read."""
        limit = data.get("rateLimit")
        if not isinstance(limit, dict):
            return
        cost, remaining = limit.get("cost"), limit.get("remaining")
        if isinstance(cost, int) and not isinstance(cost, bool):
            rate["cost"] += cost
        if isinstance(remaining, int) and not isinstance(remaining, bool):
            rate["remaining"] = remaining

    nodes = _paginate(t, _owner_query(ITEMS_QUERY, owner_key), dict(base, **names),
                      (owner_key, "projectV2", "items"), "project items", note_rate)
    board = Board(project_id, status_id, options, order_id, [], [], workflows,
                  rate_cost=rate["cost"], rate_remaining=rate["remaining"])
    for node in nodes:
        item_id = _field(node, "id", str, "project items")
        kind = node.get("type")
        if kind in ("DRAFT_ISSUE", "PULL_REQUEST"):
            continue
        try:
            if kind != "ISSUE":
                raise Stop("incomplete-read", "item %s: content not readable (%s)" % (item_id, kind))
            board.items.append(_facts(t, node, "item %s" % item_id, order_id is not None))
        except Stop as stop:
            if stop.reason != "incomplete-read":
                raise
            board.unreadable.append(item_id)
            board.reasons[item_id] = stop.detail
    return board


def read_item(t: Transport, item_id: str, cfg: Config) -> Facts:
    """A fresh, complete read of one item; any failure raises Stop("incomplete-read")."""
    what = "item %s" % item_id
    data = _query(t, ITEM_QUERY, {"item": item_id, "status": cfg.status_field,
                                   "order": cfg.order_field}, what, ITEM_ABSENT_OK)
    node = _dig(data, ("node",), what)
    if node.get("type") != "ISSUE":
        raise Stop("incomplete-read", "%s: not an issue item (%s)" % (what, node.get("type")))
    project = _field(node, "project", dict, what)
    order_field = _field(project, "order", dict, what + " project", nullable=True)
    return _facts(t, node, what, _number_field_id(order_field, what) is not None)


def read_text(t: Transport, repo: str, number: int) -> str:
    """The issue body and every comment body, in order."""
    owner, name = _split_repo(repo)
    variables = {"owner": owner, "name": name, "number": number}
    what = "text of %s#%d" % (repo, number)
    data = _query(t, TEXT_QUERY, variables, what)
    issue = _dig(data, ("repository", "issue"), what)
    parts = [_field(issue, "body", str, what)]
    nodes, more, after = _connection(issue.get("comments"), what)
    if more:
        nodes = nodes + _paginate(t, TEXT_QUERY, dict(variables, after=after),
                                  ("repository", "issue", "comments"), what)
    parts.extend(_field(node, "body", str, what + " comment") for node in nodes)
    return "\n\n".join(parts)


def read_pr_closing_issues(t: Transport, repo: str, number: int) -> list:
    """Every issue the PR closes, on the project or not: (repo, number, milestone title)."""
    owner, name = _split_repo(repo)
    what = "closing issues of %s#%d" % (repo, number)
    nodes = _paginate(t, CLOSING_ISSUES_QUERY, {"owner": owner, "name": name, "number": number},
                      ("repository", "pullRequest", "closingIssuesReferences"), what)
    return [_ref(node, what) + (_title(node, what),) for node in nodes]


def read_descendants(t: Transport, repo: str, number: int) -> set:
    """The sub-issues at every depth, on the project or not."""
    found, queue, seen = set(), [(repo, number)], {(repo, number)}
    while queue:
        parent_repo, parent_number = queue.pop(0)
        owner, name = _split_repo(parent_repo)
        what = "sub-issues of %s#%d" % (parent_repo, parent_number)
        nodes = _paginate(t, SUB_ISSUES_QUERY,
                          {"owner": owner, "name": name, "number": parent_number},
                          ("repository", "issue", "subIssues"), what)
        for node in nodes:
            ref = _ref(node, what)
            found.add(ref)
            if ref not in seen:
                seen.add(ref)
                queue.append(ref)
    found.discard((repo, number))
    return found


SET_FIELD_MUTATION = """mutation SetStatus($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, fieldId: $field,
      value: {singleSelectOptionId: $option}}) { projectV2Item { id } }
}"""
SET_ORDER_MUTATION = """mutation SetOrder($project: ID!, $item: ID!, $field: ID!, $value: Float!) {
  updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, fieldId: $field,
      value: {number: $value}}) { projectV2Item { id } }
}"""
ADD_ITEM_MUTATION = """mutation AddProjectItem($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } }
}"""
_REST_PAGE = 100


def preflight(board: Board, cfg: Config, *, needs_order: bool) -> None:
    """Stop("missing-option") naming every configured Status option the project lacks and, when
    the command allocates order values, a missing NUMBER order field. Runs before any mutation."""
    missing = ["Status option %r (%s)" % (cfg.roles[key], key)
               for key in ROLE_KEYS if cfg.roles[key] not in board.options]
    if needs_order and board.order_field_id is None:
        missing.append("number field %r" % cfg.order_field)
    if missing:
        raise Stop("missing-option", "not on the project: " + ", ".join(missing))


def _rest(t: Transport, method: str, path: str, what: str, body: Optional[dict] = None) -> Any:
    """One REST call; a scope error propagates, any other failure is an incomplete read."""
    try:
        return t.rest(method, path, body)
    except GhError as err:
        if map_gh_error(err) is not None:
            raise
        raise Stop("incomplete-read", "%s: %s" % (what, err.message))


def _rest_pages(t: Transport, path: str, what: str) -> list:
    """Every element of a paged REST list, page by page until a short page."""
    found, page = [], 1
    while True:
        chunk = _rest(t, "GET", "%s&per_page=%d&page=%d" % (path, _REST_PAGE, page), what)
        if not isinstance(chunk, list) or any(not isinstance(e, dict) for e in chunk):
            raise Stop("incomplete-read", "%s: not a list of objects" % what)
        found.extend(chunk)
        if len(chunk) < _REST_PAGE:
            return found
        page += 1


def _milestones(t: Transport, repo: str, state: str) -> list:
    """(number, title, open_issues) of every milestone of the repository in `state`."""
    what = "%s milestones" % repo
    return [(_field(m, "number", int, what), _field(m, "title", str, what),
             _field(m, "open_issues", int, what) if state == "open" else None)
            for m in _rest_pages(t, "repos/%s/milestones?state=%s" % (repo, state), what)]


def _write_failed(stop: Stop) -> Stop:
    """A failed mutating call is a rejected write (write-failed), not an incomplete read."""
    return Stop("write-failed", stop.detail) if stop.reason == "incomplete-read" else stop


def _mutate(t: Transport, query: str, variables: dict, what: str) -> None:
    """One field-value mutation; a failure or an unconfirmed response raises write-failed."""
    try:
        data = _query(t, query, variables, what)
        _dig(data, ("updateProjectV2ItemFieldValue", "projectV2Item", "id"), what)
    except Stop as stop:
        raise _write_failed(stop)


def _rest_write(t: Transport, method: str, path: str, what: str, body: Optional[dict] = None) -> Any:
    """One mutating REST call; a failure (other than a scope error) raises write-failed."""
    try:
        return _rest(t, method, path, what, body)
    except Stop as stop:
        raise _write_failed(stop)


class Writer:
    """Every write sync, start, next and followup make. Each method appends `write: ...`,
    `would write: ...` (dry run), `skipped ...` or `failed write ...` to `out`. A rejected write
    raises Stop("write-failed"); set_role and set_pr_milestone record it in `write_failures`
    instead, so the caller attempts every other write first."""

    def __init__(self, t: Transport, board: Board, cfg: Config, dry_run: bool, out: list):
        self.t, self.board, self.cfg, self.dry_run, self.out = t, board, cfg, dry_run, out
        self._milestone_numbers = {}  # repo -> {title: number}
        self.fresh = {}   # item id -> the Facts of its guarded fresh read
        self.failed = {}  # item id -> why its guarded fresh read failed
        self.write_failures = []  # (issue or PR ref, why) of every rejected write
        self.written = {}  # item id -> the Status option written (on a dry run: to be written)

    def _record_failure(self, ref: str, stop: Stop) -> None:
        why = stop.detail[len(ref) + 2:] if stop.detail.startswith(ref + ": ") else stop.detail
        self.write_failures.append((ref, why))
        self.out.append("failed write %s: %s" % (ref, why))

    def _emit(self, text: str) -> None:
        self.out.append(("would write: " if self.dry_run else "write: ") + text)

    def set_role(self, f: Facts, role: str) -> bool:
        """Write the role's Status option, after a fresh read shows the derivation inputs unchanged."""
        ref = "%s#%d" % (f.repo, f.number)
        try:
            fresh = read_item(self.t, f.item_id, self.cfg)
        except Stop as stop:
            if stop.reason != "incomplete-read":
                raise
            self.failed[f.item_id] = stop.detail
            self.out.append("skipped %s: unreadable: %s" % (ref, stop.detail))
            return False
        self.fresh[f.item_id] = fresh
        if fresh.inputs() != f.inputs():
            self.out.append("skipped %s: inputs changed" % ref)
            return False
        if fresh.status == self.cfg.roles[role]:
            return False
        try:
            self.write_role(fresh, role)
        except Stop as stop:
            if stop.reason != "write-failed":
                raise
            self._record_failure(ref, stop)
            return False
        return True

    def write_role(self, f: Facts, role: str) -> None:
        """Write the role's Status option without the fresh-read guard (start's own claim); a failed
        write raises Stop. f.status is the Status the write replaces."""
        ref = "%s#%d" % (f.repo, f.number)
        name = self.cfg.roles[role]
        if not self.dry_run:
            _mutate(self.t, SET_FIELD_MUTATION, {
                "project": self.board.project_id, "item": f.item_id,
                "field": self.board.status_field_id, "option": self.board.options[name]}, ref)
        self.written[f.item_id] = name
        self._emit("%s Status: %s -> %s" % (ref, f.status or "(none)", name))

    def add_assignee(self, repo: str, number: int, login: str) -> None:
        """Add `login` to the issue's assignees with the add-assignees operation, which never
        replaces the existing ones; a failed write raises Stop."""
        ref = "%s#%d" % (repo, number)
        if not self.dry_run:
            _rest_write(self.t, "POST", "repos/%s/issues/%d/assignees" % (repo, number), ref,
                        {"assignees": [login]})
        self._emit("%s assignees: +%s" % (ref, login))

    def set_order(self, item_id: Optional[str], value: float, label: Optional[str] = None) -> None:
        """label: how the item is named in the output (followup's dry run has no item id yet)."""
        if self.board.order_field_id is None:
            raise Stop("missing-option", "number field %r not on the project" % self.cfg.order_field)
        if not self.dry_run:
            _mutate(self.t, SET_ORDER_MUTATION, {
                "project": self.board.project_id, "item": item_id,
                "field": self.board.order_field_id, "value": float(value)}, "item %s" % item_id)
        self._emit("%s %s: %s" % (label or item_id, self.cfg.order_field, value))

    def create_issue(self, repo: str, title: str, body: str) -> Optional[tuple]:
        """Create the issue; (number, the whole response), None on a dry run. Only the number is
        checked here, so the caller can report it before it checks the rest of the response."""
        created = None
        if not self.dry_run:
            what = "create issue in %s" % repo
            response = _rest_write(self.t, "POST", "repos/%s/issues" % repo, what,
                                   {"title": title, "body": body})
            created = (_field(response, "number", int, what), response)
        self._emit("create issue in %s: %s" % (repo, title))
        return created

    def add_sub_issue(self, parent: tuple, ref: str, issue_id: Optional[int]) -> None:
        """Attach the issue (by its REST id) as a sub-issue of `parent` (repo, number)."""
        if not self.dry_run:
            _rest_write(self.t, "POST", "repos/%s/issues/%d/sub_issues" % parent, ref,
                        {"sub_issue_id": issue_id})
        self._emit("%s sub-issue of %s#%d" % ((ref,) + tuple(parent)))

    def set_issue_milestone(self, repo: str, number: Optional[int], ref: str, milestone_number: int,
                            title: str) -> None:
        if not self.dry_run:
            _rest_write(self.t, "PATCH", "repos/%s/issues/%d" % (repo, number), ref,
                        {"milestone": milestone_number})
        self._emit("%s milestone: %s" % (ref, title))

    def add_blocked_by(self, repo: str, number: Optional[int], ref: str, blocker: tuple,
                       blocker_id: int) -> None:
        """A native blocked-by link from the issue to `blocker` (repo, number), by its REST id."""
        if not self.dry_run:
            _rest_write(self.t, "POST", "repos/%s/issues/%d/dependencies/blocked_by" % (repo, number),
                        ref, {"issue_id": blocker_id})
        self._emit("%s blocked by %s#%d" % ((ref,) + tuple(blocker)))

    def add_project_item(self, content_id: Optional[str], ref: str) -> Optional[str]:
        """Add the issue to the project; the new item id, None on a dry run."""
        item_id = None
        if not self.dry_run:
            try:
                data = _query(self.t, ADD_ITEM_MUTATION, {"project": self.board.project_id,
                                                           "content": content_id}, ref)
                item_id = _field(_dig(data, ("addProjectV2ItemById", "item"), ref), "id", str, ref)
            except Stop as stop:
                raise _write_failed(stop)
        self._emit("%s added to the project" % ref)
        return item_id

    def set_pr_milestone(self, repo: str, number: int, milestone_title: str) -> bool:
        """Set the PR's milestone by title, unless the repository has no milestone of that title
        or a fresh read shows the PR already has one (both reported). The milestone lookup comes
        first, so the PR re-read is the last read before the PATCH."""
        ref = "%s#%d" % (repo, number)
        try:
            if repo not in self._milestone_numbers:
                self._milestone_numbers[repo] = {
                    title: num for num, title, _ in _milestones(self.t, repo, "all")}
            milestone_number = self._milestone_numbers[repo].get(milestone_title)
            if milestone_number is None:
                self.out.append("skipped %s: milestone %r not found in %s" % (ref, milestone_title, repo))
                return False
            current = _rest(self.t, "GET", "repos/%s/issues/%d" % (repo, number), ref)
            if not isinstance(current, dict) or "milestone" not in current:
                raise Stop("incomplete-read", "%s: no milestone in the response" % ref)
            if current["milestone"] is not None:
                self.out.append("skipped %s: milestone already set" % ref)
                return False
        except Stop as stop:
            if stop.reason != "incomplete-read":
                raise
            self.out.append("skipped %s: unreadable: %s" % (ref, stop.detail))
            return False
        if not self.dry_run:
            try:
                _rest_write(self.t, "PATCH", "repos/%s/issues/%d" % (repo, number), ref,
                            {"milestone": milestone_number})
            except Stop as stop:
                if stop.reason != "write-failed":
                    raise
                self._record_failure(ref, stop)
                return False
        self._emit("%s milestone: %s" % (ref, milestone_title))
        return True


@dataclass
class SyncResult:
    writes: list
    reports: list
    unreadable: list  # item ids unreadable on the board read or on a guarded fresh read
    reasons: dict = field(default_factory=dict)  # item id -> why it could not be read
    fresh: dict = field(default_factory=dict)    # item id -> Facts of its fresh read, when newer
    failed: list = field(default_factory=list)   # (issue or PR ref, why) of every rejected write
    ready: list = field(default_factory=list)    # `ready: <repo>#<n> order=<v>` lines
    rate_cost: int = 0                           # see Board
    rate_remaining: Optional[int] = None


_WRITE_PREFIXES = ("write: ", "would write: ")


def sync(t: Transport, cfg: Config, project_url: str, *, mode: str, repo: Optional[str],
         env: Mapping[str, str], dry_run: bool, only: Optional[tuple] = None) -> SyncResult:
    """The gated public entry point: Stop("not-enabled") unless PROJECT_URL names project_url,
    checked before any board read; then the reconciliation (_reconcile)."""
    require_enabled(t, project_url, repo, mode=mode, env=env)
    return _reconcile(t, cfg, project_url, mode=mode, repo=repo, dry_run=dry_run, only=only)


def _reconcile(t: Transport, cfg: Config, project_url: str, *, mode: str, repo: Optional[str],
               dry_run: bool, only: Optional[tuple] = None,
               board: Optional[Board] = None) -> SyncResult:
    """Ungated; only for callers that already ran require_enabled. The complete reconciliation:
    every in-scope item's Status, the milestone of every open or merged in-scope PR closing a board
    issue, and the report of empty open milestones.
    mode "action" writes only items and PRs of `repo`; "agent" covers the whole project.
    `only` (repo, number) limits the status and milestone pass to one issue and skips the
    milestone report. `board`: a board the caller just read and preflighted (next), else read here."""
    if mode not in ("action", "agent"):
        raise Stop("config-invalid", "unknown mode %r" % mode)
    if mode == "action":
        owner, _, name = (repo or "").partition("/")
        if not owner or not name or "/" in name:
            raise Stop("config-invalid", "action mode needs --repo OWNER/NAME")
    if board is None:
        board = read_board(t, project_url, cfg)
    preflight(board, cfg, needs_order=False)

    out = []
    writer = Writer(t, board, cfg, dry_run, out)
    try:
        return _reconcile_pass(t, cfg, board, writer, out, mode=mode, repo=repo, only=only)
    except BaseException:
        # A fatal error after a completed write: print the writes made so far before it propagates,
        # so no completed write goes unreported.
        for line in out:
            if line.startswith(_WRITE_PREFIXES):
                print(line)
        sys.stdout.flush()
        raise


def _reconcile_pass(t: Transport, cfg: Config, board: Board, writer: "Writer", out: list, *,
                    mode: str, repo: Optional[str], only: Optional[tuple]) -> SyncResult:
    """The body of _reconcile on a read and preflighted board; `out` is the writer's output."""
    def in_scope(r: str) -> bool:
        return mode == "agent" or r == repo

    reports = []
    for item_id in board.unreadable:
        reports.append("unreadable %s: %s; not written" % (item_id, board.reasons.get(item_id, "")))
    targets = [f for f in board.items
               if only is None or (f.repo, f.number) == tuple(only)]
    if only is not None and not targets:
        reports.append("%s#%d: not a readable issue on the project" % tuple(only))

    for f in targets:
        if not in_scope(f.repo):
            continue
        role, lines = derive(f, cfg)
        reports.extend("%s#%d: %s" % (f.repo, f.number, line) for line in lines)
        # An untrusted closing PR (see trusted) neither makes the issue In Review nor gets a milestone.
        reports.extend("%s#%d: ignored untrusted PR %s#%d" % (f.repo, f.number, p.repo, p.number)
                       for p in f.closing_prs
                       if p.state in ("OPEN", "MERGED") and not trusted(p, f.repo))
        if f.status != cfg.roles[role]:
            writer.set_role(f, role)

    pulls = {}
    for f in targets:
        for p in f.closing_prs:
            if p.state in ("OPEN", "MERGED") and p.milestone is None and in_scope(p.repo) \
                    and trusted(p, f.repo):
                pulls.setdefault((p.repo, p.number), p)
    for (pr_repo, pr_number) in pulls:
        ref = "%s#%d" % (pr_repo, pr_number)
        try:
            closing = read_pr_closing_issues(t, pr_repo, pr_number)
        except Stop as stop:
            if stop.reason != "incomplete-read":
                raise
            reports.append("%s: closing issues unreadable, no milestone set: %s" % (ref, stop.detail))
            continue
        titles = sorted({title for _, _, title in closing if title is not None})
        if len(titles) > 1:
            reports.append("%s: conflicting milestones %s; none set" % (ref, ", ".join(titles)))
        elif titles:
            writer.set_pr_milestone(pr_repo, pr_number, titles[0])

    # Every item that is Ready once this reconciliation's writes land, whoever wrote it: the facts
    # derive Ready and the Status is Ready (read, or written by this run; on a dry run, to be written).
    ready = []
    for f in targets:
        if not in_scope(f.repo):
            continue
        latest = writer.fresh.get(f.item_id, f)
        status = writer.written.get(f.item_id, latest.status)
        if status == cfg.roles["ready"] and derive(latest, cfg)[0] == "ready":
            ready.append(latest)
    ready.sort(key=lambda f: (f.order is None, round(f.order, 3) if f.order is not None else 0.0,
                              f.number, f.repo.lower()))
    ready_lines = ["ready: %s#%d order=%s" % (f.repo, f.number,
                                             "-" if f.order is None else _order_text(f.order))
                   for f in ready]

    if only is None:
        repos = sorted({f.repo for f in board.items if in_scope(f.repo)})
        for r in repos:
            closed = {f.milestone for f in board.items
                      if f.repo == r and f.state == "CLOSED" and f.milestone is not None}
            try:
                found = _milestones(t, r, "open")
            except Stop as stop:
                if stop.reason != "incomplete-read":
                    raise
                reports.append("%s milestones unreadable: %s" % (r, stop.detail))
                continue
            for _, title, open_issues in found:
                if open_issues == 0 and title in closed:
                    reports.append("milestone %r in %s is complete: no open issues; not closed"
                                   % (title, r))

    writes = [line for line in out if line.startswith(_WRITE_PREFIXES)]
    reports = [line for line in out if not line.startswith(_WRITE_PREFIXES)] + reports
    unreadable = list(dict.fromkeys(list(board.unreadable) + list(writer.failed)))
    reasons = {i: board.reasons.get(i) or writer.failed.get(i, "") for i in unreadable}
    return SyncResult(writes, reports, unreadable, reasons, dict(writer.fresh),
                      list(writer.write_failures), ready_lines, board.rate_cost,
                      board.rate_remaining)


def _order_text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _print_result(result: SyncResult) -> None:
    for line in result.writes + result.reports:
        print(line)


def _stop_on_failed_writes(result: SyncResult) -> None:
    """Stop("write-failed") when the reconciliation had a rejected write; it is incomplete. Each
    rejected write is already on stdout as a `failed write <ref>: <why>` line."""
    if result.failed:
        raise Stop("write-failed", "%d write(s) failed" % len(result.failed))


def next_task(t: Transport, cfg: Config, project_url: str, repo: str, *, skip_unordered: bool,
              dry_run: bool, mode: str = "agent", env: Optional[Mapping[str, str]] = None) -> Facts:
    """The gated public entry point of `next`: sync, then the Ready, unassigned, open-issue item
    with the lowest order value (compared after round(x, 3)); prints `next: <repo>#<n> order=<v>`.
    Stops (never chooses) on a partial board, nothing Ready, an unordered candidate (unless
    skip_unordered) or a tie. Views and board positions are never read."""
    require_enabled(t, project_url, repo, mode=mode, env={} if env is None else env)
    board = read_board(t, project_url, cfg)
    preflight(board, cfg, needs_order=True)
    result = _reconcile(t, cfg, project_url, mode=mode, repo=repo, dry_run=dry_run, board=board)
    _print_result(result)
    _stop_on_failed_writes(result)  # never select after a failed reconciliation
    # Any item unreadable on the board read or on a fresh read during the reconciliation.
    if result.unreadable:
        raise Stop("incomplete-read", "unreadable items, nothing selected: " + ", ".join(
            "%s (%s)" % (i, result.reasons.get(i, "")) for i in result.unreadable))
    # Derived from the freshest facts this run read (a fresh read during the reconciliation replaces
    # the board read), so a stale Ready Status never counts and a dry run selects what the
    # reconciliation would leave Ready. This is a snapshot-consistent selection, not a linearizable
    # claim: a claim that lands after these reads is not seen here; start's re-read of the assignees
    # after its own assignment is what detects it (collision).
    latest = [result.fresh.get(f.item_id, f) for f in board.items]
    candidates = [f for f in latest
                  if f.state == "OPEN" and not f.assignees and derive(f, cfg)[0] == "ready"]
    if not candidates:
        raise Stop("nothing-ready", "no Ready, unassigned open issue on the project")
    unordered = ["%s#%d" % (f.repo, f.number) for f in candidates if f.order is None]
    if unordered and not skip_unordered:
        raise Stop("missing-order", "Ready, unassigned items without a %r value: %s"
                   % (cfg.order_field, ", ".join(unordered)))
    for ref in unordered:
        print("skipped %s: no %r value" % (ref, cfg.order_field))
    ordered = [f for f in candidates if f.order is not None]
    if not ordered:
        raise Stop("nothing-ready", "no Ready, unassigned item has a %r value" % cfg.order_field)
    lowest = min(round(f.order, 3) for f in ordered)
    chosen = [f for f in ordered if round(f.order, 3) == lowest]
    if len(chosen) > 1:
        raise Stop("tie", "%s share the lowest %r value %s" % (
            " and ".join("%s#%d" % (f.repo, f.number) for f in chosen), cfg.order_field,
            _order_text(lowest)))
    f = chosen[0]
    print("next: %s#%d order=%s" % (f.repo, f.number, _order_text(f.order)))
    return f


def _authenticated_login(t: Transport) -> str:
    user = _rest(t, "GET", "user", "authenticated user")
    if not isinstance(user, dict) or not isinstance(user.get("login"), str) or not user["login"]:
        raise Stop("incomplete-read", "authenticated user: no login in the response")
    return user["login"]


def _not_ready(f: Facts, cfg: Config, role: str, lines: list) -> str:
    ref = "%s#%d" % (f.repo, f.number)
    if role == "backlog":
        open_blockers = ["%s#%d" % (b.repo, b.number) for b in f.blockers if b.state == "OPEN"]
        parts = []
        if open_blockers:
            parts.append("open blockers " + ", ".join(open_blockers))
        parts.extend(lines)  # blockers closed as not planned or duplicate
        return "%s is %s: %s" % (ref, cfg.roles[role], "; ".join(parts) or "blocked")
    if role == "in_progress":
        return "%s is assigned to %s" % (ref, ", ".join(sorted(f.assignees)))
    return "%s is %s" % (ref, cfg.roles[role])


def start(t: Transport, cfg: Config, project_url: str, repo: str, issue: tuple, *, force: bool,
          dry_run: bool, mode: str = "agent", env: Optional[Mapping[str, str]] = None) -> None:
    """The gated public entry point of `start`: claim the issue by assignment. Derive its status
    afresh and refuse anything not Ready unless `force`; add the authenticated user as an assignee
    (never replacing), set In Progress, re-read the assignees (any other login: collision), then
    reconcile that item. A dry run lists the two writes and stops there."""
    require_enabled(t, project_url, repo, mode=mode, env={} if env is None else env)
    issue_repo, number = issue
    ref = "%s#%d" % (issue_repo, number)
    board = read_board(t, project_url, cfg)
    preflight(board, cfg, needs_order=False)
    found = [f for f in board.items if (f.repo.lower(), f.number) == (issue_repo.lower(), number)]
    if not found:
        if board.unreadable:
            raise Stop("incomplete-read", "%s not found among the readable items; unreadable: %s"
                       % (ref, ", ".join(board.unreadable)))
        raise Stop("not-ready", "%s is not an issue on the project" % ref)
    current = read_item(t, found[0].item_id, cfg)
    role, lines = derive(current, cfg)
    if role != "ready":
        if not force:
            raise Stop("not-ready", _not_ready(current, cfg, role, lines))
        print("forced: " + _not_ready(current, cfg, role, lines))
    login = _authenticated_login(t)
    out = []
    writer = Writer(t, board, cfg, dry_run, out)
    try:
        writer.add_assignee(current.repo, current.number, login)
        writer.write_role(current, "in_progress")
    finally:
        for line in out:
            print(line)
    if dry_run:
        return
    after = read_item(t, current.item_id, cfg)
    others = sorted(a for a in after.assignees if a.lower() != login.lower())
    if others:
        raise Stop("collision", "%s is also assigned to %s; ask before continuing"
                   % (ref, ", ".join(others)))
    if login.lower() not in {a.lower() for a in after.assignees}:
        raise Stop("assignment-failed", "%s: %s is not assigned after the claim; GitHub drops an "
                   "assignee without push access to %s" % (ref, login, current.repo))
    result = _reconcile(t, cfg, project_url, mode=mode, repo=repo, dry_run=False,
                        only=(current.repo, current.number))
    _print_result(result)
    _stop_on_failed_writes(result)
    print("started: %s" % ref)


def allocate_order(parent: Facts, items: list, descendants: set) -> float:
    """The order value of a new follow-up of `parent` (design D7). p is the parent's value; n the
    lowest value above p held by an item that is not a descendant of the parent, else p + 1; v the
    largest value in [p, n). The first of v + 0.1, v + 0.01, v + 0.001 below n and unused wins.
    Every value is compared after round(x, 3)."""
    ref = "%s#%d" % (parent.repo, parent.number)
    if parent.order is None:
        raise Stop("parent-unordered", "%s has no order value; ask the user" % ref)
    p = round(parent.order, 3)
    below = {(r.lower(), n) for r, n in descendants}
    used = {round(f.order, 3) for f in items if f.order is not None}
    above = [round(f.order, 3) for f in items if f.order is not None and round(f.order, 3) > p
             and (f.repo.lower(), f.number) not in below]
    n = min(above) if above else round(p + 1, 3)
    v = max([x for x in used if p <= x < n] + [p])
    for step in (0.1, 0.01, 0.001):
        candidate = round(v + step, 3)
        if candidate < n and candidate not in used:
            return candidate
    raise Stop("no-order-room", "no free value between %s and %s for a follow-up of %s; ask the user"
               % (_order_text(v), _order_text(n), ref))


FOLLOWUP_STEPS = ("add_sub_issue", "set_milestone", "add_blocked_by", "add_project_item", "set_order")


@dataclass(frozen=True)
class IssueLinks:
    """The links of an issue followup created or resumes; repos as GitHub returns them."""
    database_id: int
    content_id: str
    parent: Optional[tuple]
    milestone: Optional[str]
    blockers: tuple
    items: dict  # project id -> item id


def _issue_links(t: Transport, repo: str, number: int) -> IssueLinks:
    owner, name = _split_repo(repo)
    what = "%s#%d" % (repo, number)
    data = _query(t, FOLLOWUP_ISSUE_QUERY, {"owner": owner, "name": name, "number": number}, what)
    issue = _dig(data, ("repository", "issue"), what)
    parent = _field(issue, "parent", dict, what, nullable=True)
    blockers = _issue_connection(t, issue, "blockedBy", what)
    items = _issue_connection(t, issue, "projectItems", what)
    return IssueLinks(
        database_id=_field(issue, "databaseId", int, what), content_id=_field(issue, "id", str, what),
        parent=None if parent is None else _ref(parent, what + " parent"),
        milestone=_title(issue, what),
        blockers=tuple(_ref(n, what + " blocker") for n in blockers),
        items={_field(_field(n, "project", dict, what + " project item"), "id", str, what): _field(
            n, "id", str, what + " project item") for n in items})


def _key(ref: tuple) -> tuple:
    return ref[0].lower(), ref[1]


def _followup_body(body_file: str, pr: str, change_dir: str, found_at: str) -> str:
    try:
        with open(body_file, encoding="utf-8") as handle:
            text = handle.read().rstrip()
    except OSError as exc:
        raise Stop("config-invalid", "cannot read the body file %s: %s" % (body_file, exc.strerror or exc))
    return "%s\n\n---\n- Found in pull request: %s\n- Change directory: `%s`\n- Found at: %s\n" % (
        text, pr, change_dir, found_at)


def _disagreements(links: IssueLinks, parent: tuple, milestone: Optional[str], blockers: list) -> list:
    found = []
    if links.parent is not None and _key(links.parent) != _key(parent):
        found.append("it is a sub-issue of %s#%d, not of %s#%d" % (links.parent + tuple(parent)))
    if links.milestone is not None and links.milestone != milestone:
        found.append("its milestone is %r, the parent's is %r" % (links.milestone, milestone))
    wanted = {_key(b) for b in blockers}
    extra = ["%s#%d" % b for b in links.blockers if _key(b) not in wanted]
    if extra:
        found.append("it is blocked by %s, not given as blockers" % ", ".join(extra))
    return found


def followup(t: Transport, cfg: Config, project_url: str, repo: str, *, parent: tuple, title: str,
             body_file: str, blockers: list, pr: str, change_dir: str, found_at: str,
             resume: Optional[int], dry_run: bool, mode: str = "agent",
             env: Optional[Mapping[str, str]] = None) -> int:
    """The gated public entry point of `followup` (design D7): create a follow-up issue in `repo`
    and (1) print its number at once, (2) attach it as a sub-issue of `parent`, (3) give it the
    parent's milestone, (4) add native blocked-by links to `blockers` only, (5) add it to the
    project and (6) set its order value (allocate_order). Every read and check runs before the first
    write. After a failure past step 1 it prints `created: #<n>` and `missing: <steps>` and stops;
    `resume` (the issue number) with the original arguments completes only the missing steps, each
    checked first, and stops with resume-disagrees, writing nothing, when an existing link
    disagrees. Returns the issue number (0 for a dry run that creates nothing)."""
    require_enabled(t, project_url, repo, mode=mode, env={} if env is None else env)
    parent_ref = "%s#%d" % tuple(parent)
    if any(_key(b) == _key(parent) for b in blockers):
        raise Stop("parent-as-blocker", "%s is the parent; a follow-up is never blocked by its parent"
                   % parent_ref)
    blockers = list({_key(b): tuple(b) for b in blockers}.values())
    body = _followup_body(body_file, pr, change_dir, found_at)
    board = read_board(t, project_url, cfg)
    preflight(board, cfg, needs_order=True)
    if board.unreadable:
        raise Stop("incomplete-read", "unreadable items, no order allocated: " + ", ".join(
            "%s (%s)" % (i, board.reasons.get(i, "")) for i in board.unreadable))
    on_board = {_key((f.repo, f.number)): f for f in board.items}
    parent_facts = on_board.get(_key(parent))
    if parent_facts is None:
        raise Stop("parent-unordered", "%s is not an issue on the project; ask the user" % parent_ref)
    if parent_facts.order is None:
        raise Stop("parent-unordered", "%s has no order value; ask the user" % parent_ref)

    links, item_id, ordered = None, None, False
    if resume is not None:
        links = _issue_links(t, repo, resume)
        disagree = _disagreements(links, parent, parent_facts.milestone, blockers)
        if disagree:
            raise Stop("resume-disagrees", "%s#%d: %s; nothing written" % (repo, resume, "; ".join(disagree)))
        item_id = links.items.get(board.project_id)
        if item_id is not None:
            ordered = read_item(t, item_id, cfg).order is not None
    value = None
    if not ordered:
        value = allocate_order(parent_facts, board.items,
                               read_descendants(t, parent_facts.repo, parent_facts.number))
    milestone_number = None
    if parent_facts.milestone is not None and (links is None or links.milestone is None):
        numbers = {title_: number for number, title_, _ in _milestones(t, repo, "all")}
        milestone_number = numbers.get(parent_facts.milestone)
        if milestone_number is None:
            raise Stop("missing-option", "milestone %r of %s not found in %s"
                       % (parent_facts.milestone, parent_ref, repo))
    have = set() if links is None else {_key(b) for b in links.blockers}
    blocker_ids = {}
    for b in blockers:
        if _key(b) in have:
            continue
        known = on_board.get(_key(b))
        if known is not None:
            blocker_ids[b] = known.database_id
        else:
            what = "blocker %s#%d" % b
            blocker_ids[b] = _field(_rest(t, "GET", "repos/%s/issues/%d" % b, what), "id", int, what)

    out = []
    writer = Writer(t, board, cfg, dry_run, out)

    def flush():
        for line in out:
            print(line)
        del out[:]

    # Completion is sequentially idempotent: every link step re-reads the issue right before its
    # write, skips a link that already exists, refuses one that disagrees, and reconciles a
    # duplicate-link error through a fresh read. GitHub has no compare-and-set, so a concurrent
    # writer can still land between that last read and the write (a residual non-atomic window).
    number = resume
    created = None
    if resume is None:
        created = writer.create_issue(repo, title, body)
        flush()
        if created is not None:
            number = created[0]
            print("created: #%d" % number)
            sys.stdout.flush()
    ref = "%s#%s" % (repo, number if number is not None else "(new)")
    pending = list(FOLLOWUP_STEPS)
    issue_id = content_id = None
    failing = None  # a failure outside the pending steps

    def done(step: str, why: Optional[str] = None) -> None:
        if why is not None:
            out.append("skipped %s: %s" % (step, why))
        pending.remove(step)
        flush()

    def refresh() -> Optional[IssueLinks]:
        """The issue's links now; a newly observed disagreement stops before the write. None on a
        dry run of a new issue (there is nothing to read)."""
        if number is None:
            return None
        now = _issue_links(t, repo, number)
        disagree = _disagreements(now, parent, parent_facts.milestone, blockers)
        if disagree:
            raise Stop("resume-disagrees", "%s: %s; not written" % (ref, "; ".join(disagree)))
        return now

    def reconcile(stop: Stop, linked) -> None:
        """After a failed link write: satisfied when a fresh read shows the link, else re-raise."""
        if stop.reason not in ("incomplete-read", "write-failed") or number is None:
            raise stop
        now = refresh()
        if not linked(now):
            raise stop

    try:
        if resume is None:
            if created is not None:
                what = "create issue in %s" % repo
                failing = "create_issue (its response)"
                issue_id = _field(created[1], "id", int, what)
                content_id = _field(created[1], "node_id", str, what)
                failing = None
        else:
            issue_id, content_id = links.database_id, links.content_id
            print("resuming: %s#%d" % (repo, resume))
        if links is not None and links.parent is not None:
            done("add_sub_issue", "already a sub-issue of %s" % parent_ref)
        else:
            try:
                writer.add_sub_issue(tuple(parent), ref, issue_id)
            except Stop as stop:
                reconcile(stop, lambda now: now.parent is not None)  # refresh checked which parent
                out.append("skipped add_sub_issue: already a sub-issue of %s" % parent_ref)
            done("add_sub_issue")
        if parent_facts.milestone is not None and links is not None and links.milestone is not None:
            now = links  # set before this run: no write, nothing to refresh
        else:
            now = refresh() if parent_facts.milestone is not None else None
        if parent_facts.milestone is None:
            done("set_milestone", "%s has no milestone" % parent_ref)
        elif now is not None and now.milestone is not None:
            done("set_milestone", "milestone already %r" % now.milestone)
        else:
            if milestone_number is None:  # resumed with a milestone a concurrent writer removed
                numbers = {t_: n_ for n_, t_, _ in _milestones(t, repo, "all")}
                milestone_number = numbers.get(parent_facts.milestone)
                if milestone_number is None:
                    raise Stop("missing-option", "milestone %r of %s not found in %s"
                               % (parent_facts.milestone, parent_ref, repo))
            writer.set_issue_milestone(repo, number, ref, milestone_number, parent_facts.milestone)
            done("set_milestone")
        wrote = False
        for b in blockers:
            if b not in blocker_ids:
                continue
            now = refresh()
            if now is not None and _key(b) in {_key(x) for x in now.blockers}:
                out.append("skipped add_blocked_by: %s#%d already linked" % b)
                flush()
                continue
            try:
                writer.add_blocked_by(repo, number, ref, b, blocker_ids[b])
                wrote = True
            except Stop as stop:
                reconcile(stop, lambda now, b=b: _key(b) in {_key(x) for x in now.blockers})
                out.append("skipped add_blocked_by: %s#%d already linked" % b)
            flush()
        done("add_blocked_by", "no blockers given" if not blockers else
             None if wrote else "every blocker already linked")
        if item_id is None and number is not None:
            # The project's auto-add workflows may have added it since: look it up first.
            item_id = _issue_links(t, repo, number).items.get(board.project_id)
            if item_id is not None:
                done("add_project_item", "already on the project")
        if "add_project_item" in pending:
            if item_id is None:
                item_id = writer.add_project_item(content_id, ref)
                done("add_project_item")
            else:
                done("add_project_item", "already on the project")
        if ordered:
            done("set_order", "%r already set" % cfg.order_field)
        else:
            writer.set_order(item_id, value, label=ref)
            done("set_order")
    except (Stop, GhError) as err:
        flush()
        if number is not None and not dry_run:
            print("failed: %s: %s" % (failing or pending[0], err.detail if isinstance(err, Stop) else err.message))
            print("created: #%d" % number)
            print("missing: %s" % ", ".join(pending))
            print("resume with the same arguments and --resume %d" % number)
        raise
    return number or 0


VARIABLE_FIX = "needs the Actions variables read permission"
ENABLE_STEP = "set the repository variable PROJECT_URL to this project to enable it"


def enabled_url(t: Transport, repo: str, *, mode: str, env: Mapping[str, str]) -> Optional[str]:
    """The project PROJECT_URL names, None when it is unset. Mode "action" reads the environment;
    "agent" reads the repository variable over REST, where a 404 means unset and any other failure
    is reported, never guessed."""
    if mode == "action":
        return env.get("PROJECT_URL") or None
    if mode != "agent":
        raise Stop("config-invalid", "unknown mode %r" % mode)
    try:
        response = t.rest("GET", "repos/%s/actions/variables/PROJECT_URL" % repo)
    except GhError as err:
        scope = map_gh_error(err)
        if scope is not None:
            raise scope
        if "HTTP 404" in err.message:
            return None
        raise Stop("variable-unreadable", VARIABLE_FIX)
    if not isinstance(response, dict) or not isinstance(response.get("value"), str):
        raise Stop("variable-unreadable", VARIABLE_FIX)
    return response["value"] or None


def require_enabled(t: Transport, project_url: str, repo: Optional[str], *, mode: str,
                    env: Mapping[str, str]) -> None:
    """Stop("not-enabled") unless PROJECT_URL names the same project as project_url; a view URL
    and the project URL are the same project."""
    wanted = parse_project_url(project_url)
    owner, _, name = (repo or "").partition("/")
    if not owner or not name or "/" in name:
        raise Stop("config-invalid", "the PROJECT_URL check needs --repo OWNER/NAME")
    enabled = enabled_url(t, repo, mode=mode, env=env)
    if enabled is None:
        raise Stop("not-enabled", "PROJECT_URL is not set; " + ENABLE_STEP)
    try:
        named = parse_project_url(enabled)
    except Stop:
        raise Stop("not-enabled", "PROJECT_URL is not a project URL; " + ENABLE_STEP)
    if (named[0].lower(), named[1]) != (wanted[0].lower(), wanted[1]):
        raise Stop("not-enabled", "PROJECT_URL names %s/%d, not %s/%d; %s"
                   % (named[0], named[1], wanted[0], wanted[1], ENABLE_STEP))


# The built-in project workflows the setup relies on (facts). Enabling one is possible only in the
# project UI; the API has no mutation for it.
REQUIRED_WORKFLOWS = ("Item closed",)
# Advised, never required: a board may be populated by hand, and a real project's workflow list need
# not contain this name at all.
ADVISED_WORKFLOW = "Auto-add to project"
AUTO_ADD_HINT = ("consider enabling Auto-add to project, issues only, so new issues reach the board; "
                 "cards can also be added manually")
_PROSE_DEPENDENCY = re.compile(r"(?i)\b(blocked by|depends on|after)\s+((?:[\w.-]+/[\w.-]+)?#\d+)")
CHECK_NOTICE = (
    "A clean warning list does not prove that prose dependencies were converted to native "
    "blocked-by links. Setting PROJECT_URL is your attestation that they were reviewed and converted.")


@dataclass
class CheckResult:
    facts: list
    warnings: list
    notice: str


def _prose_warnings(t: Transport, f: Facts) -> list:
    ref = "%s#%d" % (f.repo, f.number)
    try:
        body = read_text(t, f.repo, f.number)
    except Stop as stop:
        if stop.reason != "incomplete-read":
            raise
        return ["%s: text unreadable, dependency mentions not checked: %s" % (ref, stop.detail)]
    native = {(b.repo.lower(), b.number) for b in f.blockers}
    warnings, seen = [], set()
    for match in _PROSE_DEPENDENCY.finditer(body):
        target_repo, _, number = match.group(2).rpartition("#")
        key = ((target_repo or f.repo).lower(), int(number))
        if key in native or key in seen:
            continue
        seen.add(key)
        warnings.append("%s: mentions %r without a native blocked-by link" % (ref, match.group(0)))
    return warnings


def check(t: Transport, cfg: Config, project_url: Optional[str]) -> CheckResult:
    """The setup check. Without a project URL the (already validated) config is all there is and no
    call is made. With one: the facts that must be clean before enabling, and the heuristic
    warnings about prose dependencies. Reads only."""
    if project_url is None:
        return CheckResult([], [], CHECK_NOTICE)
    facts, warnings = [], []
    try:
        board = read_board(t, project_url, cfg)
    except Stop as stop:
        if stop.reason != "missing-option":
            raise
        return CheckResult([stop.detail], [], CHECK_NOTICE)
    for name in REQUIRED_WORKFLOWS:
        state = board.workflows.get(name)
        if state is not True:
            facts.append("built-in workflow %r is %s; enable it in the project UI (Workflows)"
                         % (name, "missing" if state is None else "disabled"))
    if board.workflows.get(ADVISED_WORKFLOW) is not True:
        warnings.append(AUTO_ADD_HINT)
    for key in ROLE_KEYS:
        if cfg.roles[key] not in board.options:
            facts.append("Status option %r (%s) is not on the project" % (cfg.roles[key], key))
    if board.order_field_id is None:
        facts.append("number field %r is not on the project" % cfg.order_field)
    for item_id in board.unreadable:
        facts.append("item %s is unreadable: %s" % (item_id, board.reasons.get(item_id, "")))
    for f in board.items:
        ref = "%s#%d" % (f.repo, f.number)
        if f.state == "OPEN" and board.order_field_id is not None and f.order is None:
            facts.append("%s: open item without a %r value" % (ref, cfg.order_field))
        if f.status == cfg.roles["in_progress"] and not f.assignees:
            facts.append("%s: %r without an assignee" % (ref, f.status))
        if f.status == cfg.roles["blocked"] and cfg.blocked_label.lower() not in f.labels:
            facts.append("%s: %r without the %r label" % (ref, f.status, cfg.blocked_label))
    for f in board.items:
        if f.state == "OPEN":
            warnings.extend(_prose_warnings(t, f))
    return CheckResult(facts, warnings, CHECK_NOTICE)


def _sync(args, transport: Transport, env: Mapping[str, str]) -> int:
    cfg = load_config(args.config)
    result = sync(transport, cfg, args.project, mode=args.mode, repo=args.repo, env=env,
                  dry_run=args.dry_run)
    for line in result.writes + result.reports + result.ready:
        print(line)
    if not result.writes and not result.failed:
        print("no changes" + (" would be written" if args.dry_run else " written"))
    print("rate-limit: cost=%d remaining=%s" % (
        result.rate_cost, "-" if result.rate_remaining is None else result.rate_remaining))
    _stop_on_failed_writes(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="project_flow.py")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("sync", "next", "start", "followup", "check"):
        p = sub.add_parser(name)
        p.add_argument("--project", metavar="URL")
        p.add_argument("--repo", metavar="OWNER/NAME")
        p.add_argument("--mode", choices=("action", "agent"), default="agent")
        p.add_argument("--config", default=DEFAULT_CONFIG, metavar="PATH")
        p.add_argument("--dry-run", action="store_true")
        if name == "next":
            p.add_argument("--skip-unordered", action="store_true",
                           help="select past Ready items without an order value (after the user agrees)")
        if name == "start":
            p.add_argument("issue", type=_issue_arg, metavar="OWNER/REPO#N")
            p.add_argument("--force", action="store_true",
                           help="claim an item that is not Ready (only after the user authorizes it)")
        if name == "followup":
            p.add_argument("--parent", type=_issue_arg, required=True, metavar="OWNER/REPO#N")
            p.add_argument("--title", required=True)
            p.add_argument("--body-file", required=True, metavar="PATH")
            p.add_argument("--blocker", type=_issue_arg, action="append", default=[],
                           metavar="OWNER/REPO#N", help="a native blocked-by link; repeat per blocker")
            p.add_argument("--pr", required=True, metavar="URL")
            p.add_argument("--change-dir", required=True, metavar="PATH")
            p.add_argument("--found-at", required=True, metavar="TEXT")
            p.add_argument("--resume", type=_number_arg, metavar="N",
                           help="complete the missing steps of this issue; pass the original arguments")
    return parser


def _number_arg(text: str) -> int:
    match = re.fullmatch(r"#?(\d+)", text.strip())
    if not match:
        raise argparse.ArgumentTypeError("expected an issue number, got %r" % text)
    return int(match.group(1))


_ISSUE_ARG = re.compile(r"(?:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?#?(\d+)")


def _issue_arg(text: str) -> tuple:
    """`owner/repo#n`, or `#n` / `n` for an issue of --repo (repo None until resolved)."""
    match = _ISSUE_ARG.fullmatch(text.strip())
    if not match:
        raise argparse.ArgumentTypeError("expected OWNER/REPO#N or #N, got %r" % text)
    return match.group(1), int(match.group(2))


def _next(args, transport: Transport, env: Mapping[str, str]) -> int:
    next_task(transport, load_config(args.config), args.project, args.repo,
              skip_unordered=args.skip_unordered, dry_run=args.dry_run, mode=args.mode, env=env)
    return 0


def _start(args, transport: Transport, env: Mapping[str, str]) -> int:
    issue_repo, number = args.issue
    start(transport, load_config(args.config), args.project, args.repo,
          (issue_repo or args.repo or "", number), force=args.force, dry_run=args.dry_run,
          mode=args.mode, env=env)
    return 0


def _followup(args, transport: Transport, env: Mapping[str, str]) -> int:
    def resolve(ref: tuple) -> tuple:
        return (ref[0] or args.repo or "", ref[1])

    followup(transport, load_config(args.config), args.project, args.repo,
             parent=resolve(args.parent), title=args.title, body_file=args.body_file,
             blockers=[resolve(b) for b in args.blocker], pr=args.pr, change_dir=args.change_dir,
             found_at=args.found_at, resume=args.resume, dry_run=args.dry_run, mode=args.mode, env=env)
    return 0


def _check(args, transport: Transport) -> int:
    """Exit 0 when the facts are clean (warnings alone never fail); Stop("setup-incomplete")
    after printing every finding when any fact is found."""
    cfg = load_config(args.config)
    print("config ok: %s" % args.config)
    if not args.project:
        return 0
    result = check(transport, cfg, args.project)
    for line in result.facts:
        print("fact: " + line)
    for line in result.warnings:
        print("warning: " + line)
    if not result.facts:
        print("facts clean")
    print(result.notice)
    if result.facts:
        raise Stop("setup-incomplete", "%d setup facts need attention" % len(result.facts))
    return 0


# The commands that act on a project only while PROJECT_URL names it. Each one's public entry point
# calls require_enabled itself before any other call; the CLI table test runs every name here.
GATED_COMMANDS = ("sync", "next", "start", "followup")
_COMMANDS = {"sync": _sync, "next": _next, "start": _start, "followup": _followup}


def main(argv: list, transport: Optional[Transport] = None,
         env: Optional[Mapping[str, str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    transport = transport if transport is not None else GhTransport()
    env = os.environ if env is None else env
    try:
        if args.command == "check":
            return _check(args, transport)
        if args.command not in _COMMANDS:
            print("usage: %s is not implemented yet" % args.command, file=sys.stderr)
            return 2
        if args.command in GATED_COMMANDS and not args.project:
            print("usage: %s needs --project URL" % args.command, file=sys.stderr)
            return 2
        return _COMMANDS[args.command](args, transport, env)
    except Stop as stop:
        print("stop: %s: %s" % (stop.reason, stop.detail))
        return 3
    except GhError as err:
        stop = map_gh_error(err)
        if stop is None:
            print("error: %s" % err.message, file=sys.stderr)
            return 1
        print("stop: %s: %s" % (stop.reason, stop.detail))
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], GhTransport()))
