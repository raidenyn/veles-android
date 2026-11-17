#!/usr/bin/env python3
"""project_flow: derive GitHub Project statuses from facts (stdlib only).

Run as `python3 -I scripts/project_flow.py <sync|next|start|followup|check> ...`.
Exit 0 success, 2 usage, 3 a stop (`stop: <reason>: <detail>` on stdout).
"""
import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

DEFAULT_CONFIG = ".github/project-flow.json"
ROLE_KEYS = ("backlog", "ready", "in_progress", "in_review", "blocked", "done")
CONFIG_KEYS = ("status_field", "roles", "order_field", "blocked_label")
STOP_REASONS = (
    "not-enabled", "variable-unreadable", "nothing-ready", "tie", "missing-order",
    "incomplete-read", "not-ready", "collision", "missing-option", "config-invalid",
    "resume-disagrees", "no-order-room", "parent-unordered", "parent-as-blocker",
    "missing-scope",
)
SCOPE_FIX = "run gh auth refresh -s project"

_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_ISSUE_REF = re.compile(r"#\d+")
_NODE_ID = re.compile(r"[A-Z]{1,4}_[A-Za-z0-9_-]{8,}")
_DIGITS = re.compile(r"\d+")


# GraphQL queries. Every connection reads `first: $first`, which defaults to 100; only the
# fixture recording passes a smaller page size. `OWNER` is replaced by `user` or `organization`.
_PAGE = "pageInfo { hasNextPage endCursor }"
_ISSUE_FACTS = """
fragment IssueFacts on Issue {
  id databaseId number state stateReason(enableDuplicate: true)
  repository { nameWithOwner }
  milestone { title }
  labels(first: $first) { %(page)s nodes { name } }
  assignees(first: $first) { %(page)s nodes { login } }
  closedByPullRequestsReferences(first: $first, includeClosedPrs: true) {
    %(page)s nodes { number state isDraft repository { nameWithOwner } milestone { title } } }
  blockedBy(first: $first) {
    %(page)s nodes { number state stateReason(enableDuplicate: true) repository { nameWithOwner } } }
  parent { number repository { nameWithOwner } }
  subIssues(first: $first) { %(page)s nodes { number repository { nameWithOwner } } }
}""" % {"page": _PAGE}
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
}""" % (_PAGE, _ITEM_FIELDS) + _ISSUE_FACTS
ITEM_QUERY = """query ProjectItem($item: ID!, $status: String!, $order: String!, $first: Int = 100) {
  node(id: $item) { ... on ProjectV2Item { %s
    project { order: field(name: $order) { __typename ... on ProjectV2Field { id dataType }
      ... on ProjectV2SingleSelectField { id } ... on ProjectV2IterationField { id } } } } }
}""" % _ITEM_FIELDS + _ISSUE_FACTS
# Follow-up page of one nested connection of an issue, by the issue node id.
_CONNECTION_NODES = {
    "labels": "nodes { name }",
    "assignees": "nodes { login }",
    "closedByPullRequestsReferences": (
        "nodes { number state isDraft repository { nameWithOwner } milestone { title } }"),
    "blockedBy": (
        "nodes { number state stateReason(enableDuplicate: true) repository { nameWithOwner } }"),
    "subIssues": "nodes { number repository { nameWithOwner } }",
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


class GhError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


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
    def graphql(self, query: str, variables: dict) -> dict: ...

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
            raise GhError((done.stderr or done.stdout).strip() or "gh exited %d" % done.returncode)
        return done.stdout

    def graphql(self, query: str, variables: Optional[dict] = None) -> dict:
        args = ["api", "graphql", "-f", "query=" + query]
        for name, value in (variables or {}).items():
            if value is None or isinstance(value, (int, bool)):
                args += ["-F", "%s=%s" % (name, json.dumps(value))]  # None -> null
            else:
                args += ["-f", "%s=%s" % (name, value)]
        return self._decode(self._run(args))

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


def map_gh_error(e: GhError) -> Optional[Stop]:
    if "missing required scopes" in getattr(e, "message", str(e)):
        return Stop("missing-scope", SCOPE_FIX)
    return None


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
            tuple(sorted((p.repo, p.number, p.state, p.is_draft) for p in self.closing_prs)),
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


_PROJECT_URL = re.compile(
    r"https://github\.com/(?:users|orgs)/([A-Za-z0-9][A-Za-z0-9-]*)/projects/(\d+)(?:/views/\d+)?/?")


def derive(f: Facts, cfg: Config) -> tuple:
    """The role key a Facts snapshot maps to, plus report lines. Pure; first match wins."""
    if f.state == "CLOSED":
        return "done", []
    if cfg.blocked_label.lower() in f.labels:
        return "blocked", []
    if any(p.state == "OPEN" and not p.is_draft for p in f.closing_prs):
        return "in_review", []
    if f.assignees:
        return "in_progress", []
    lines = []
    for b in f.blockers:
        if b.state == "CLOSED" and b.state_reason == "NOT_PLANNED":
            lines.append("blocker %s#%d closed as not planned" % (b.repo, b.number))
        elif b.state == "CLOSED" and b.state_reason == "DUPLICATE":
            lines.append("blocker %s#%d closed as duplicate" % (b.repo, b.number))
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


def _query(t: Transport, query: str, variables: dict, what: str) -> dict:
    """One GraphQL read. A scope error propagates (main maps it); any other failure, or a
    response with errors or without data, is an incomplete read."""
    try:
        response = t.graphql(query, variables)
    except GhError as err:
        if map_gh_error(err) is not None:
            raise
        raise Stop("incomplete-read", "%s: %s" % (what, err.message))
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


def _paginate(t: Transport, query: str, variables: dict, path: tuple, what: str) -> list:
    """Every node of the connection at `path`, following hasNextPage to the last page."""
    nodes, after = [], None
    while True:
        page_vars = dict(variables)
        if after is not None:
            page_vars["after"] = after
        data = _query(t, query, page_vars, what)
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
    return PullRef(repo, number, _enum(node, "state", _PR_STATES, what),
                   _field(node, "isDraft", bool, what), _title(node, what))


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
                          "project fields")
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
    nodes = _paginate(t, _owner_query(ITEMS_QUERY, owner_key), dict(base, **names),
                      (owner_key, "projectV2", "items"), "project items")
    board = Board(project_id, status_id, options, order_id, [], [], workflows)
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
                                   "order": cfg.order_field}, what)
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
    return parser


def _check(args) -> int:
    load_config(args.config)
    if args.project:
        print("usage: check with --project is not implemented yet", file=sys.stderr)
        return 2
    print("config ok: %s" % args.config)
    return 0


def main(argv: list, transport: Optional[Transport] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        if args.command == "check":
            return _check(args)
        print("usage: %s is not implemented yet" % args.command, file=sys.stderr)
        return 2
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
