"""Tests for GhTransport argument encoding and FakeTransport behaviour. No network."""
import os
import sys
import unittest
from unittest import mock

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_flow  # noqa: E402
from project_flow import GhError, GhTransport  # noqa: E402
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
