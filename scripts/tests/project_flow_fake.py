"""Test fake for the project_flow transport: records calls, serves canned responses."""
import os
import sys

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from project_flow import GhError  # noqa: E402


class FakeTransport:
    """Serves responses in order, or by matcher; records every call and mutation.

    responses: list of (matcher, response). A matcher is None (matches any call), a
    string (substring of the query or path) or a callable taking the call dict. A
    response that is an Exception instance is raised (use GhError for gh failures).
    A matched entry is consumed once served.
    """

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []
        self.mutations = []
        self._hooks = []

    def on_call(self, hook):
        """Register hook(call) to run on every call, before the response is chosen."""
        self._hooks.append(hook)

    @staticmethod
    def _matches(matcher, call):
        if matcher is None:
            return True
        if callable(matcher):
            return bool(matcher(call))
        text = call.get("query") or call.get("path") or ""
        return matcher in text

    def _serve(self, call):
        self.calls.append(call)
        if call["kind"] == "rest" and call["method"].upper() != "GET":
            self.mutations.append(call)
        elif call["kind"] == "graphql" and call["query"].lstrip().startswith("mutation"):
            self.mutations.append(call)
        for hook in self._hooks:
            hook(call)
        for index, (matcher, response) in enumerate(self.responses):
            if self._matches(matcher, call):
                del self.responses[index]
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError("FakeTransport: no response for %r" % (call,))

    def graphql(self, query, variables=None, *, absent_ok=frozenset()):
        return self._serve({"kind": "graphql", "query": query, "variables": variables or {},
                            "absent_ok": absent_ok})

    def rest(self, method, path, body=None):
        return self._serve({"kind": "rest", "method": method, "path": path, "body": body})


# Builders: GraphQL-shaped dicts in the shape of the recorded fixtures under
# fixtures/project_flow/, which read_board and read_item parse.

REPO = "owner/repo"
STATUS_OPTIONS = ("Backlog", "Ready", "In Progress", "In Review", "Blocked", "Done")


def _conn(nodes, more=False, cursor="cursor-next"):
    return {"nodes": list(nodes),
            "pageInfo": {"hasNextPage": bool(more), "endCursor": cursor if more else None}}


def _ref(ref):
    """(repo, number) or number -> {number, repository}."""
    repo, number = ref if isinstance(ref, tuple) else (REPO, ref)
    return {"number": number, "repository": {"nameWithOwner": repo}}


def pr(number, state="OPEN", draft=False, repo=REPO, milestone=None):
    """A closedByPullRequestsReferences node."""
    return {"number": number, "state": state, "isDraft": draft,
            "repository": {"nameWithOwner": repo},
            "milestone": {"title": milestone} if milestone else None}


def blocker(number, state="CLOSED", reason="COMPLETED", repo=REPO):
    """A blockedBy node; an open blocker has reason None."""
    return {"number": number, "state": state, "stateReason": reason,
            "repository": {"nameWithOwner": repo}}


def issue(number, repo=REPO, state="OPEN", state_reason=None, labels=(), assignees=(), prs=(),
          blockers=(), parent=None, sub_issues=(), milestone=None, node_id=None, database_id=None,
          more=()):
    """An Issue content node (IssueFacts). blockers: blocker() dicts or (number, state, reason)
    tuples; parent and sub_issues: number or (repo, number); more: connection names whose
    pageInfo.hasNextPage is true."""
    blockers = [b if isinstance(b, dict) else blocker(*b) for b in blockers]
    return {
        "__typename": "Issue",
        "id": node_id or "I_issue%d" % number,
        "databaseId": database_id if database_id is not None else 1000 + number,
        "number": number, "state": state, "stateReason": state_reason,
        "repository": {"nameWithOwner": repo},
        "milestone": {"title": milestone} if milestone else None,
        "labels": _conn([{"name": n} for n in labels], "labels" in more),
        "assignees": _conn([{"login": n} for n in assignees], "assignees" in more),
        "closedByPullRequestsReferences": _conn(prs, "closedByPullRequestsReferences" in more),
        "blockedBy": _conn(blockers, "blockedBy" in more),
        "parent": _ref(parent) if parent is not None else None,
        "subIssues": _conn([_ref(r) for r in sub_issues], "subIssues" in more),
    }


def item(content, item_id=None, status=None, order=None, type=None):
    """A ProjectV2Item node. content None (with type) is a draft or redacted card."""
    if type is None:
        type = "ISSUE" if content is not None else "DRAFT_ISSUE"
    if item_id is None:
        item_id = "PVTI_item%d" % content["number"] if content else "PVTI_draft"
    if content is None and type == "DRAFT_ISSUE":
        content = {"__typename": "DraftIssue"}
    return {"id": item_id, "type": type,
            "status": ({"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": status,
                        "optionId": "opt-" + status} if status else None),
            "order": ({"__typename": "ProjectV2ItemFieldNumberValue", "number": float(order)}
                      if order is not None else None),
            "content": content,
            # Read by ITEM_QUERY only; ITEMS_QUERY responses carry it harmlessly.
            "project": item_project()}


def item_project(order_type="NUMBER"):
    """The `project` of an ITEM_QUERY node: the order field and its data type (None: no field)."""
    if order_type is None:
        return {"order": None}
    return {"order": {"__typename": "ProjectV2Field", "id": "PVTF_field1", "dataType": order_type}}


def items_page(nodes, more=False, cursor="cursor-next", owner_key="user"):
    """One ITEMS_QUERY response."""
    return {"data": {owner_key: {"projectV2": {"items": _conn(nodes, more, cursor)}}}}


def fields(options=STATUS_OPTIONS, order=True, owner_key="user", status_name="Status",
           order_name="Implementation order"):
    """One FIELDS_QUERY response."""
    status = {"__typename": "ProjectV2SingleSelectField", "id": "PVTSSF_field1", "name": status_name,
              "options": [{"id": "opt-" + o, "name": o} for o in options]}
    order_field = ({"__typename": "ProjectV2Field", "id": "PVTF_field1", "name": order_name,
                    "dataType": "NUMBER"} if order else None)
    return {"data": {owner_key: {"projectV2": {"id": "PVT_project1", "status": status,
                                               "order": order_field}}}}


def workflows(enabled=None, owner_key="user"):
    """One WORKFLOWS_QUERY response; enabled maps name -> bool."""
    enabled = {"Item closed": True, "Auto-add to project": True} if enabled is None else enabled
    nodes = [{"name": n, "enabled": e} for n, e in enabled.items()]
    return {"data": {owner_key: {"projectV2": {"workflows": _conn(nodes)}}}}


def board(items, page_size=None, options=STATUS_OPTIONS, order=True, enabled=None, owner_key="user"):
    """FakeTransport responses for one read_board: fields, workflows, then the item pages."""
    items = list(items)
    size = page_size or max(len(items), 1)
    chunks = [items[i:i + size] for i in range(0, len(items), size)] or [[]]
    responses = [("query ProjectFields", fields(options, order, owner_key)),
                 ("query ProjectWorkflows", workflows(enabled, owner_key))]
    for index, chunk in enumerate(chunks):
        more = index < len(chunks) - 1
        responses.append(("query ProjectItems",
                          items_page(chunk, more, "cursor%d" % (index + 1), owner_key)))
    return responses


def item_response(node):
    """One ITEM_QUERY response for an item() node."""
    return {"data": {"node": node}}


def closing_issues(refs, more=False, cursor="cursor-next"):
    """One CLOSING_ISSUES_QUERY response; refs: (number or (repo, number), milestone title or None)."""
    nodes = [dict(_ref(ref), milestone={"title": title} if title else None) for ref, title in refs]
    return {"data": {"repository": {"pullRequest": {"closingIssuesReferences": _conn(nodes, more, cursor)}}}}


def status_written(item_id="PVTI_item1"):
    """An updateProjectV2ItemFieldValue response."""
    return {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": item_id}}}}


def milestone(number, title, open_issues=1, closed_issues=0):
    """A REST milestone object, trimmed like fixtures/project_flow/milestones.json."""
    return {"number": number, "title": title, "state": "open",
            "open_issues": open_issues, "closed_issues": closed_issues}


__all__ = ["FakeTransport", "GhError", "issue", "pr", "blocker", "item", "item_project", "items_page",
           "fields", "workflows", "board", "item_response", "closing_issues", "status_written",
           "milestone"]
