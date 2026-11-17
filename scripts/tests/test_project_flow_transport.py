"""Tests for GhTransport argument encoding and FakeTransport behaviour. No network."""
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_flow  # noqa: E402
from project_flow import Config, GhError, GhTransport  # noqa: E402
import project_flow_fake  # noqa: E402
from project_flow_fake import FakeTransport  # noqa: E402


class GhTransportEncodingTest(unittest.TestCase):
    def test_variable_encoding(self):
        with mock.patch.object(GhTransport, "_run", return_value="{}") as run:
            GhTransport().graphql("query Q", {"after": None, "s": "x=1", "n": 5, "b": True})
        args = run.call_args[0][0]
        self.assertEqual(
            args,
            ["api", "graphql", "-f", "query=query Q", "-F", "after=null", "-f", "s=x=1",
             "-F", "n=5", "-F", "b=true"],
        )


def completed(code, stdout, stderr=""):
    return subprocess.CompletedProcess(["gh"], code, stdout=stdout, stderr=stderr)


# Recorded live (read-only) from the scratch project: a field(name:) lookup of an absent field.
ABSENT_FIELD = {
    "data": {"user": {"projectV2": {"id": "PVT_project1",
                                    "status": {"__typename": "ProjectV2SingleSelectField"},
                                    "order": None}}},
    "errors": [{"type": "NOT_FOUND", "path": ["user", "projectV2", "order"],
                "locations": [{"line": 1, "column": 113}],
                "message": "Could not resolve to a Unions::ProjectV2FieldConfiguration with the name "
                           "NoSuchFieldXYZ"}]}
USER_FIELDS = project_flow.FIELDS_ABSENT_OK["user"]


class GhTransportNotFoundTest(unittest.TestCase):
    """gh exits 1 on a NOT_FOUND; the data is returned only for the caller's listed null paths."""

    def graphql(self, code, payload, absent_ok=USER_FIELDS):
        stdout = payload if isinstance(payload, str) else json.dumps(payload)
        with mock.patch.object(project_flow.subprocess, "run",
                               return_value=completed(code, stdout, "gh: Could not resolve")):
            return GhTransport().graphql("query Q { x }", {}, absent_ok=absent_ok)

    @staticmethod
    def error(path, kind="NOT_FOUND"):
        return {"type": kind, "path": list(path), "message": "x"}

    def payload(self, data, *errors):
        return {"data": data, "errors": list(errors)}

    def test_recorded_absent_field_returns_data(self):
        result = self.graphql(1, ABSENT_FIELD)
        self.assertEqual(result["data"], ABSENT_FIELD["data"])
        self.assertNotIn("errors", result)
        self.assertEqual(result["_errors"], ABSENT_FIELD["errors"])

    def test_listed_paths_are_exactly_the_field_lookups(self):
        self.assertEqual(project_flow.FIELDS_ABSENT_OK, {
            "user": frozenset({("user", "projectV2", "status"), ("user", "projectV2", "order")}),
            "organization": frozenset({("organization", "projectV2", "status"),
                                       ("organization", "projectV2", "order")})})
        self.assertEqual(project_flow.ITEM_ABSENT_OK, frozenset({("node", "project", "order")}))

    def test_each_real_absent_field_path_accepted(self):  # (a)
        for owner, listed in project_flow.FIELDS_ABSENT_OK.items():
            for key in ("status", "order"):
                project = {"id": "PVT_project1", "status": {"__typename": "ProjectV2SingleSelectField"},
                           "order": {"__typename": "ProjectV2Field"}}
                project[key] = None
                result = self.graphql(1, self.payload({owner: {"projectV2": project}},
                                                      self.error((owner, "projectV2", key))), listed)
                self.assertIsNone(result["data"][owner]["projectV2"][key], (owner, key))
        node = {"id": "PVTI_x", "order": None, "project": {"order": None}}
        result = self.graphql(1, self.payload({"node": node}, self.error(("node", "project", "order"))),
                              project_flow.ITEM_ABSENT_OK)
        self.assertIsNone(result["data"]["node"]["project"]["order"])

    def test_unlisted_not_found_raises(self):  # (b)
        data = {"user": None, "node": {"order": None, "project": {"order": {"id": "x"}}},
                "organization": {"projectV2": {"order": None}}}
        for path, listed in ((("user",), USER_FIELDS),  # owner lookup: the org fallback needs it
                             (("node", "order"), project_flow.ITEM_ABSENT_OK),
                             (("organization", "projectV2", "order"), USER_FIELDS),
                             (("user", "projectV2", "order"), frozenset())):
            with self.assertRaises(GhError, msg=repr(path)):
                self.graphql(1, self.payload(data, self.error(path)), listed)

    def test_listed_path_with_value_raises(self):  # (c)
        payload = json.loads(json.dumps(ABSENT_FIELD))
        payload["data"]["user"]["projectV2"]["order"] = {"__typename": "ProjectV2Field"}
        with self.assertRaises(GhError):
            self.graphql(1, payload)
        payload["data"]["user"]["projectV2"] = None  # the path does not resolve
        with self.assertRaises(GhError):
            self.graphql(1, payload)

    def test_other_error_types_at_listed_path_raise(self):  # (d)
        for kind in ("FORBIDDEN", "INSUFFICIENT_SCOPES", "UNAUTHORIZED", None):
            payload = self.payload({"user": {"projectV2": {"order": None}}},
                                   self.error(("user", "projectV2", "order"), kind))
            with self.assertRaises(GhError, msg=kind):
                self.graphql(1, payload)

    def test_mixed_errors_raise(self):  # (e)
        payload = json.loads(json.dumps(ABSENT_FIELD))
        payload["errors"].append(self.error(("user", "projectV2", "status"), "FORBIDDEN"))
        with self.assertRaises(GhError):
            self.graphql(1, payload)

    def test_node_field_not_listed_raises(self):  # (f): nothing reads the query text
        payload = self.payload({"node": {"field": None}}, self.error(("node", "field")))
        for listed in (frozenset(), USER_FIELDS, project_flow.ITEM_ABSENT_OK):
            with self.assertRaises(GhError):
                self.graphql(1, payload, listed)

    def test_default_accepts_nothing(self):
        with mock.patch.object(project_flow.subprocess, "run",
                               return_value=completed(1, json.dumps(ABSENT_FIELD))):
            with self.assertRaises(GhError):
                GhTransport().graphql("query Q { x }", {})

    def test_non_json_failure_raises(self):
        with self.assertRaises(GhError):
            self.graphql(1, "HTTP 502: bad gateway")

    def test_failure_without_data_raises(self):
        with self.assertRaises(GhError):
            self.graphql(1, {"errors": [self.error(("user", "projectV2", "order"))]})

    def test_absent_fields_through_read_board(self):
        """The whole read: an absent Status field is missing-option."""
        cfg = Config("Status", {k: k for k in project_flow.ROLE_KEYS}, "NoSuchFieldXYZ", "blocked")
        absent_status = json.loads(json.dumps(ABSENT_FIELD))
        absent_status["data"]["user"]["projectV2"]["status"] = None
        absent_status["errors"].append(dict(absent_status["errors"][0],
                                            path=["user", "projectV2", "status"]))
        with mock.patch.object(project_flow.subprocess, "run",
                               return_value=completed(1, json.dumps(absent_status))):
            with self.assertRaises(project_flow.Stop) as caught:
                project_flow.read_board(GhTransport(), "https://github.com/users/o/projects/2", cfg)
        self.assertEqual(caught.exception.reason, "missing-option")
        self.assertIn("Status", caught.exception.detail)

    def test_read_board_and_read_item_pass_their_paths(self):
        fake = FakeTransport(project_flow_fake.board([]))
        project_flow.read_board(fake, "https://github.com/users/o/projects/2", READ_CFG)
        self.assertEqual(fake.calls[0]["absent_ok"], project_flow.FIELDS_ABSENT_OK["user"])
        for call in fake.calls[1:]:
            self.assertEqual(call["absent_ok"], frozenset())
        node = project_flow_fake.item(project_flow_fake.issue(1))
        fake = FakeTransport([("query ProjectItem(", project_flow_fake.item_response(node))])
        project_flow.read_item(fake, node["id"], READ_CFG)
        self.assertEqual(fake.calls[0]["absent_ok"], project_flow.ITEM_ABSENT_OK)

    def test_org_fallback_passes_org_paths(self):
        responses = [("query ProjectFields", GhError("Could not resolve to a User"))]
        responses += project_flow_fake.board([], owner_key="organization")
        fake = FakeTransport(responses)
        project_flow.read_board(fake, "https://github.com/orgs/o/projects/2", READ_CFG)
        self.assertEqual(fake.calls[1]["absent_ok"], project_flow.FIELDS_ABSENT_OK["organization"])


READ_CFG = Config("Status", {k: k for k in project_flow.ROLE_KEYS}, "Implementation order", "blocked")


class FakeTransportTest(unittest.TestCase):
    def test_matcher_selection_and_consumption(self):
        fake = FakeTransport([("alpha", 1), (lambda c: c["kind"] == "rest", 2), (None, 3)])
        self.assertEqual(fake.graphql("beta"), 3)  # skips non-matching entries
        self.assertEqual(fake.graphql("alpha"), 1)
        self.assertEqual(fake.rest("GET", "/p"), 2)
        with self.assertRaises(AssertionError):
            fake.graphql("alpha")  # each response is consumed once

    def test_unmatched_call_fails_loudly(self):
        fake = FakeTransport([("alpha", 1)])
        with self.assertRaises(AssertionError):
            fake.graphql("zzz")
        self.assertEqual(len(fake.calls), 1)

    def test_raises_gh_error_for_matcher(self):
        fake = FakeTransport([("boom", GhError("bad"))])
        with self.assertRaises(GhError):
            fake.graphql("boom")

    def test_on_call_hook_runs_between_calls(self):
        fake = FakeTransport([(None, "a"), (None, "b")])
        seen = []
        fake.on_call(lambda call: seen.append(len(fake.calls)))
        fake.graphql("q1")
        fake.graphql("q2")
        self.assertEqual(seen, [1, 2])

    def test_mutations_recorded(self):
        fake = FakeTransport([(None, 1), (None, 2), (None, 3)])
        fake.graphql("mutation { x }")
        fake.graphql("query { x }")
        fake.rest("PATCH", "/p", {"a": 1})
        self.assertEqual(len(fake.mutations), 2)


if __name__ == "__main__":
    unittest.main()
