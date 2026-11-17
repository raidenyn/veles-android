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
FIELD_QUERY = 'query { user(login: "o") { projectV2(number: 2) { id status: field(name: "Status") ' \
              '{ __typename } order: field(name: "NoSuchFieldXYZ") { __typename } } } }'


class GhTransportNotFoundTest(unittest.TestCase):
    def graphql(self, code, payload, query=FIELD_QUERY):
        stdout = payload if isinstance(payload, str) else json.dumps(payload)
        with mock.patch.object(project_flow.subprocess, "run",
                               return_value=completed(code, stdout, "gh: Could not resolve")):
            return GhTransport().graphql(query, {})

    def test_absent_field_lookup_returns_data(self):
        result = self.graphql(1, ABSENT_FIELD)
        self.assertEqual(result["data"], ABSENT_FIELD["data"])
        self.assertNotIn("errors", result)
        self.assertEqual(result["_errors"], ABSENT_FIELD["errors"])

    def test_unaliased_field_lookup_returns_data(self):
        payload = json.loads(json.dumps(ABSENT_FIELD))
        payload["data"]["user"]["projectV2"] = {"id": "PVT_project1", "field": None}
        payload["errors"][0]["path"] = ["user", "projectV2", "field"]
        result = self.graphql(1, payload, 'query { user(login: "o") { projectV2(number: 2) { id '
                                          'field(name: "X") { __typename } } } }')
        self.assertIsNone(result["data"]["user"]["projectV2"]["field"])

    def test_not_found_owner_still_raises(self):
        payload = {"data": {"user": None},
                   "errors": [{"type": "NOT_FOUND", "path": ["user"],
                               "message": "Could not resolve to a User with the login of 'o'."}]}
        with self.assertRaises(GhError):
            self.graphql(1, payload)

    def test_mixed_errors_still_raise(self):
        payload = json.loads(json.dumps(ABSENT_FIELD))
        payload["errors"].append({"type": "FORBIDDEN", "path": ["user", "projectV2", "status"],
                                  "message": "no"})
        with self.assertRaises(GhError):
            self.graphql(1, payload)

    def test_not_found_with_non_null_value_still_raises(self):
        payload = json.loads(json.dumps(ABSENT_FIELD))
        payload["data"]["user"]["projectV2"]["order"] = {"__typename": "ProjectV2Field"}
        with self.assertRaises(GhError):
            self.graphql(1, payload)

    def test_non_json_failure_still_raises(self):
        with self.assertRaises(GhError):
            self.graphql(1, "HTTP 502: bad gateway")

    def test_failure_without_data_still_raises(self):
        with self.assertRaises(GhError):
            self.graphql(1, {"errors": [{"type": "NOT_FOUND", "path": ["user", "projectV2", "order"],
                                         "message": "x"}]})

    def test_absent_fields_through_read_board(self):
        """The whole read: an absent order field is None; an absent Status field is missing-option."""
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
