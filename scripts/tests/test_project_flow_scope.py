"""A missing gh token scope is a named stop (missing-scope) with the exact fix command, on every path a
gh failure can surface from: reads, writes and the PROJECT_URL lookup. The error texts are the ones gh
and GitHub print for `gh api` (not only the `gh project` wording). No network, no clock."""
import contextlib
import io
import os
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_flow  # noqa: E402
from project_flow import GhError, Stop  # noqa: E402
from project_flow_fake import FakeTransport, board, issue, item, item_response  # noqa: E402

URL = "https://github.com/users/owner/projects/2"
REPO = "owner/repo"
VARIABLE = "repos/owner/repo/actions/variables/PROJECT_URL"
ENABLED = (VARIABLE, {"name": "PROJECT_URL", "value": URL})
DEFAULTS = os.path.join(os.path.dirname(SCRIPTS), ".github", "project-flow.json")
USER = (lambda c: c["kind"] == "rest" and c["path"] == "user", {"login": "me"})
PROJECT_FIX = "stop: missing-scope: run gh auth refresh -s project"

# `gh api graphql` without the project scope: GitHub's INSUFFICIENT_SCOPES error as gh prints it, and
# the response body gh leaves on stdout.
GRAPHQL_TEXT = (
    "gh: Your token has not been granted the required scopes to execute this query. The 'projectV2' "
    "field requires one of the following scopes: ['read:project'], but your token has only been "
    "granted the: ['gist', 'read:org', 'repo'] scopes. Please modify your token's scopes at: "
    "https://github.com/settings/tokens.")
GRAPHQL_BODY = (
    '{"data":{"user":{"projectV2":null}},"errors":[{"type":"INSUFFICIENT_SCOPES","locations":'
    '[{"line":2,"column":3}],"message":"Your token has not been granted the required scopes to '
    "execute this query. The 'projectV2' field requires one of the following scopes: "
    "['read:project'], but your token has only been granted the: ['repo'] scopes. Please modify "
    "your token's scopes at: https://github.com/settings/tokens.\"}]}")
# `gh api -X ...` for a REST operation that needs a scope the token lacks.
REST_TEXT = ('gh: This API operation needs the "project" scope. To request it, run:  gh auth refresh '
             '-h github.com -s project (HTTP 403)')
REST_REPO_TEXT = ('gh: This API operation needs the "repo" scope. To request it, run:  gh auth '
                  'refresh -h github.com -s repo (HTTP 403)')
GH_PROJECT_TEXT = "error: your authentication token is missing required scopes [project]"


def graphql_scope():
    return GhError(GRAPHQL_TEXT, GRAPHQL_BODY)


def run_main(argv, fake, env=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = project_flow.main(argv + ["--config", DEFAULTS], fake, env={} if env is None else env)
    return code, out.getvalue(), err.getvalue()


class MapGhErrorTest(unittest.TestCase):
    def test_every_scope_wording_maps_to_missing_scope(self):
        for label, error in (("gh project", GhError(GH_PROJECT_TEXT)),
                             ("graphql stderr", GhError(GRAPHQL_TEXT)),
                             ("graphql stdout only", GhError("gh: exit 1", GRAPHQL_BODY)),
                             ("INSUFFICIENT_SCOPES", GhError("INSUFFICIENT_SCOPES: no")),
                             ("rest project", GhError(REST_TEXT))):
            with self.subTest(label):
                self.assertEqual(project_flow.map_gh_error(error),
                                 Stop("missing-scope", "run gh auth refresh -s project"))

    def test_rest_scope_other_than_project_names_that_scope(self):
        self.assertEqual(project_flow.map_gh_error(GhError(REST_REPO_TEXT)),
                         Stop("missing-scope", "run gh auth refresh -s repo"))

    def test_graphql_scope_other_than_project_names_that_scope(self):
        text = GRAPHQL_TEXT.replace("['read:project']", "['read:org']", 1)
        self.assertEqual(project_flow.map_gh_error(GhError(text)),
                         Stop("missing-scope", "run gh auth refresh -s read:org"))

    def test_other_errors_are_not_scope_errors(self):
        for text in ("gh: HTTP 403", "gh: Not Found (HTTP 404)", "boom",
                     "gh: Resource not accessible by integration (HTTP 403)"):
            self.assertIsNone(project_flow.map_gh_error(GhError(text)), text)

    def test_insufficient_scopes_in_a_returned_response(self):
        # A response that carries the error instead of a gh failure is the same stop, not a read
        # with no data.
        fake = FakeTransport([("query ProjectFields", {"data": {"user": {"projectV2": None}},
                                                       "errors": [{"type": "INSUFFICIENT_SCOPES",
                                                                   "message": GRAPHQL_TEXT}]})])
        with self.assertRaises(GhError) as ctx:
            project_flow.read_board(fake, URL, project_flow.load_config(DEFAULTS))
        self.assertEqual(project_flow.map_gh_error(ctx.exception).reason, "missing-scope")


class ScopeThroughMainTest(unittest.TestCase):
    """Each public command ends with the missing-scope stop, never incomplete-read, variable-unreadable
    or write-failed."""

    def assert_scope_stop(self, code, out, fix=PROJECT_FIX):
        self.assertEqual(code, 3, out)
        self.assertEqual(out.rstrip().splitlines()[-1], fix, out)
        for other in ("incomplete-read", "variable-unreadable", "write-failed"):
            self.assertNotIn("stop: " + other, out)

    def test_sync_board_read(self):
        for error in (graphql_scope(), GhError(GH_PROJECT_TEXT)):
            with self.subTest(error=error.message[:20]):
                fake = FakeTransport([(None, error)])
                code, out, _ = run_main(["sync", "--project", URL, "--mode", "action", "--repo", REPO],
                                        fake, env={"PROJECT_URL": URL})
                self.assert_scope_stop(code, out)

    def test_sync_variable_lookup(self):
        for text, fix in ((REST_TEXT, PROJECT_FIX),
                          (REST_REPO_TEXT, "stop: missing-scope: run gh auth refresh -s repo")):
            with self.subTest(fix=fix):
                fake = FakeTransport([(VARIABLE, GhError(text))])
                code, out, _ = run_main(["sync", "--project", URL, "--repo", REPO], fake)
                self.assert_scope_stop(code, out, fix)

    def test_sync_status_write(self):
        stale = item(issue(1), status="Backlog", order=1)
        fake = FakeTransport([ENABLED] + board([stale]) + [
            ("query ProjectItem(", item_response(stale)), ("mutation", graphql_scope())])
        code, out, _ = run_main(["sync", "--project", URL, "--repo", REPO], fake)
        self.assert_scope_stop(code, out)
        self.assertNotIn("failed write", out)

    def test_next_board_read(self):
        # The user candidate fails on the scope: it is never retried as an organization and
        # reported as a project that cannot be read.
        fake = FakeTransport([ENABLED, ("query ProjectFields", graphql_scope())])
        code, out, _ = run_main(["next", "--project", URL, "--repo", REPO], fake)
        self.assert_scope_stop(code, out)
        self.assertEqual(fake.responses, [])

    def test_start_assignment_write(self):
        node = item(issue(1), status="Ready", order=1)
        fake = FakeTransport([ENABLED] + board([node]) + [
            ("query ProjectItem(", item_response(node)), USER, ("/assignees", GhError(REST_TEXT))])
        code, out, _ = run_main(["start", "#1", "--project", URL, "--repo", REPO], fake)
        self.assert_scope_stop(code, out)
        self.assertNotIn("started:", out)

    def test_start_status_write(self):
        node = item(issue(1), status="Ready", order=1)
        fake = FakeTransport([ENABLED] + board([node]) + [
            ("query ProjectItem(", item_response(node)), USER, ("/assignees", {"number": 1}),
            ("mutation", graphql_scope())])
        code, out, _ = run_main(["start", "#1", "--project", URL, "--repo", REPO], fake)
        self.assert_scope_stop(code, out)

    def test_followup_create(self):
        nodes = [item(issue(15), status="In Progress", order=15)]
        fake = FakeTransport([ENABLED] + board(nodes) + [
            ("query IssueSubIssues", {"data": {"repository": {"issue": {"subIssues": {
                "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}}),
            (lambda c: c["kind"] == "rest" and c["method"] == "POST", GhError(REST_TEXT))])
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write("body\n")
        self.addCleanup(os.unlink, handle.name)
        code, out, _ = run_main(["followup", "--project", URL, "--repo", REPO, "--parent", "#15",
                                 "--title", "t", "--body-file", handle.name, "--pr",
                                 "https://github.com/owner/repo/pull/1", "--change-dir", "c",
                                 "--found-at", "f"], fake)
        self.assert_scope_stop(code, out)

    def test_check_board_read(self):
        fake = FakeTransport([("query ProjectFields", graphql_scope())])
        code, out, _ = run_main(["check", "--project", URL], fake)
        self.assert_scope_stop(code, out)


if __name__ == "__main__":
    unittest.main()
