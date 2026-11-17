"""Tests for the project_flow enable gate (PROJECT_URL) and the setup check, on FakeTransport.
No network, no sleep, no wall clock."""
import contextlib
import io
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_flow  # noqa: E402
from project_flow import Config, GhError, Stop  # noqa: E402
from project_flow_fake import FakeTransport, blocker, board, issue, item  # noqa: E402

URL = "https://github.com/users/owner/projects/2"
OTHER = "https://github.com/users/owner/projects/3"
REPO = "owner/repo"
VARIABLE = "repos/owner/repo/actions/variables/PROJECT_URL"
CFG = Config("Status", {"backlog": "Backlog", "ready": "Ready", "in_progress": "In Progress",
                        "in_review": "In Review", "blocked": "Blocked", "done": "Done"},
             "Implementation order", "blocked")
DEFAULTS = os.path.join(os.path.dirname(SCRIPTS), ".github", "project-flow.json")
NOT_FOUND = GhError("gh: Not Found (HTTP 404)")
FORBIDDEN = GhError("gh: Resource not accessible by integration (HTTP 403)")


def variable(value):
    return (VARIABLE, {"name": "PROJECT_URL", "value": value})


def text(body, comments=()):
    """One TEXT_QUERY response."""
    return ("query IssueText", {"data": {"repository": {"issue": {
        "body": body, "comments": {"nodes": [{"body": c} for c in comments],
                                   "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}})


def run_main(argv, fake, env=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = project_flow.main(argv, fake, env=env if env is not None else {})
    return code, out.getvalue(), err.getvalue()


class EnabledUrlTest(unittest.TestCase):
    def test_unset_variable_not_enabled(self):
        fake = FakeTransport([(VARIABLE, NOT_FOUND)])
        self.assertIsNone(project_flow.enabled_url(fake, REPO, mode="agent", env={}))
        fake = FakeTransport([(VARIABLE, NOT_FOUND)])
        with self.assertRaises(Stop) as ctx:
            project_flow.require_enabled(fake, URL, REPO, mode="agent", env={})
        self.assertEqual(ctx.exception.reason, "not-enabled")
        self.assertIn("PROJECT_URL", ctx.exception.detail)
        with self.assertRaises(Stop) as ctx:
            project_flow.require_enabled(FakeTransport(), URL, REPO, mode="action", env={})
        self.assertEqual(ctx.exception.reason, "not-enabled")

    def test_action_mode_reads_env_agent_mode_reads_variable(self):
        fake = FakeTransport()
        self.assertEqual(project_flow.enabled_url(fake, REPO, mode="action", env={"PROJECT_URL": URL}), URL)
        self.assertEqual(project_flow.enabled_url(fake, REPO, mode="action", env={"PROJECT_URL": ""}), None)
        self.assertEqual(fake.calls, [])
        fake = FakeTransport([variable(OTHER)])
        self.assertEqual(project_flow.enabled_url(fake, REPO, mode="agent", env={"PROJECT_URL": URL}), OTHER)
        self.assertEqual([(c["kind"], c["method"], c["path"]) for c in fake.calls],
                         [("rest", "GET", VARIABLE)])

    def test_other_project_not_enabled(self):
        with self.assertRaises(Stop) as ctx:
            project_flow.require_enabled(FakeTransport([variable(OTHER)]), URL, REPO, mode="agent", env={})
        self.assertEqual(ctx.exception.reason, "not-enabled")
        for other in ("https://github.com/users/someone/projects/2",
                      "https://github.com/orgs/owner/projects/22"):
            with self.assertRaises(Stop) as ctx:
                project_flow.require_enabled(FakeTransport(), URL, REPO, mode="action",
                                             env={"PROJECT_URL": other})
            self.assertEqual(ctx.exception.reason, "not-enabled")

    def test_view_url_matches_project_url(self):
        view = URL + "/views/2"
        project_flow.require_enabled(FakeTransport([variable(URL)]), view, REPO, mode="agent", env={})
        project_flow.require_enabled(FakeTransport([variable(view)]), URL, REPO, mode="agent", env={})
        project_flow.require_enabled(FakeTransport(), URL + "/", REPO, mode="action",
                                     env={"PROJECT_URL": URL + "/views/7?layout=board"})

    def test_variable_read_error_reported(self):
        with self.assertRaises(Stop) as ctx:
            project_flow.enabled_url(FakeTransport([(VARIABLE, FORBIDDEN)]), REPO, mode="agent", env={})
        self.assertEqual(ctx.exception, Stop("variable-unreadable", "needs the Actions variables read permission"))
        with self.assertRaises(Stop) as ctx:
            project_flow.enabled_url(FakeTransport([(VARIABLE, {"name": "PROJECT_URL"})]), REPO,
                                     mode="agent", env={})
        self.assertEqual(ctx.exception.reason, "variable-unreadable")

    def test_malformed_variable_not_enabled(self):
        with self.assertRaises(Stop) as ctx:
            project_flow.require_enabled(FakeTransport([variable("not a url")]), URL, REPO,
                                         mode="agent", env={})
        self.assertEqual(ctx.exception.reason, "not-enabled")


class GateTest(unittest.TestCase):
    def test_sync_refuses_when_not_enabled(self):
        fake = FakeTransport([(VARIABLE, NOT_FOUND)])
        code, out, _ = run_main(["sync", "--project", URL, "--repo", REPO], fake)
        self.assertEqual(code, 3)
        self.assertIn("stop: not-enabled", out)
        self.assertEqual(len(fake.calls), 1)  # only the variable read; no board read, no write

    def test_sync_runs_when_enabled(self):
        fake = FakeTransport([variable(URL + "/views/1")] + board([]) +
                             [("milestones?state=open", [])])
        code, out, _ = run_main(["sync", "--project", URL, "--repo", REPO], fake)
        self.assertEqual(code, 0, out)
        self.assertIn("no changes written", out)
        self.assertEqual(fake.mutations, [])

    def test_gated_commands_refuse_when_not_enabled(self):
        """Every gated command refuses an unset or a different PROJECT_URL before any other call.
        """
        for command in ("sync", "next", "start", "followup"):
            self.assertIn(command, project_flow.GATED_COMMANDS)
        self.assertNotIn("check", project_flow.GATED_COMMANDS)
        positional = {"start": [REPO + "#1"],
                      "followup": ["--parent", "#1", "--title", "T", "--body-file", DEFAULTS,
                                   "--pr", "https://github.com/owner/repo/pull/2",
                                   "--change-dir", "openspec/changes/x", "--found-at", "here"]}
        cases = (("agent", [(VARIABLE, NOT_FOUND)], {}),
                 ("agent", [variable(OTHER)], {}),
                 ("action", [], {}),
                 ("action", [], {"PROJECT_URL": OTHER}))
        for command in project_flow.GATED_COMMANDS:
            for mode, responses, env in cases:
                with self.subTest(command=command, mode=mode, env=env, responses=responses):
                    fake = FakeTransport(list(responses))
                    code, out, _ = run_main([command] + positional.get(command, []) + [
                        "--project", URL, "--repo", REPO, "--mode", mode, "--config", DEFAULTS], fake, env)
                    self.assertEqual(code, 3)
                    self.assertIn("stop: not-enabled", out)
                    self.assertEqual(len(fake.calls), len(responses))
                    self.assertEqual(fake.mutations, [])

    def test_direct_sync_refuses_when_not_enabled(self):
        """The public sync() gates itself: a direct call reads only the variable, never the board."""
        cases = (("agent", [(VARIABLE, NOT_FOUND)], {}),
                 ("agent", [variable(OTHER)], {}),
                 ("action", [], {}),
                 ("action", [], {"PROJECT_URL": OTHER}))
        for mode, responses, env in cases:
            with self.subTest(mode=mode, env=env, responses=responses):
                fake = FakeTransport(list(responses))
                with self.assertRaises(Stop) as ctx:
                    project_flow.sync(fake, CFG, URL, mode=mode, repo=REPO, env=env, dry_run=False)
                self.assertEqual(ctx.exception.reason, "not-enabled")
                self.assertEqual(len(fake.calls), len(responses))
                self.assertFalse(any(c["kind"] == "graphql" for c in fake.calls))
                self.assertEqual(fake.mutations, [])

    def test_direct_sync_runs_when_enabled(self):
        fake = FakeTransport([variable(URL)] + board([]))  # empty board: no repo, no milestone read
        result = project_flow.sync(fake, CFG, URL + "/views/3", mode="agent", repo=REPO, env={},
                                   dry_run=False)
        self.assertEqual(result.writes, [])
        self.assertEqual(fake.responses, [])

    def test_sync_reads_variable_once(self):
        fake = FakeTransport([variable(URL)] + board([]) + [("milestones?state=open", [])])
        code, _, _ = run_main(["sync", "--project", URL, "--repo", REPO], fake)
        self.assertEqual(code, 0)
        self.assertEqual(len([c for c in fake.calls if c.get("path") == VARIABLE]), 1)

    def test_gated_command_needs_repo(self):
        code, out, _ = run_main(["sync", "--project", URL], FakeTransport())
        self.assertEqual(code, 3)
        self.assertIn("stop: config-invalid", out)


class CheckTest(unittest.TestCase):
    def test_check_offline_no_transport_calls(self):
        fake = FakeTransport()
        result = project_flow.check(fake, CFG, None)
        self.assertEqual(fake.calls, [])
        self.assertEqual((result.facts, result.warnings), ([], []))
        code, out, _ = run_main(["check", "--config", DEFAULTS], fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.calls, [])

    def test_check_clean_board(self):
        fake = FakeTransport(board([item(issue(1, blockers=[(12, "OPEN", None)]), order=1),
                                    item(issue(2, assignees=["a"]), status="In Progress", order=2),
                                    item(issue(3, labels=["Blocked"]), status="Blocked", order=3),
                                    item(issue(4, state="CLOSED", state_reason="COMPLETED"))])
                             + [text("Blocked by #12"), text("plain"), text("")])
        result = project_flow.check(fake, CFG, URL + "/views/2")
        self.assertEqual((result.facts, result.warnings), ([], []))
        self.assertEqual(fake.mutations, [])

    def _check(self, items, *, enabled=None, order=True, options=None, texts=None):
        kwargs = {"enabled": enabled, "order": order}
        if options is not None:
            kwargs["options"] = options
        fake = FakeTransport(board(items, **kwargs) +
                             [text(t) for t in (texts if texts is not None else [""] * len(items))])
        result = project_flow.check(fake, CFG, URL)
        self.assertEqual(fake.mutations, [])
        return result

    def test_check_facts(self):
        cases = {
            "workflow disabled": dict(items=[item(issue(1), order=1)],
                                      enabled={"Item closed": False, "Auto-add to project": True}),
            "missing order field": dict(items=[item(issue(1))], order=False),
            "open item without order": dict(items=[item(issue(1))]),
            "In Progress without assignee": dict(items=[item(issue(1), status="In Progress", order=1)]),
            "Blocked without label": dict(items=[item(issue(1), status="Blocked", order=1)]),
        }
        for name, case in cases.items():
            with self.subTest(name):
                result = self._check(**case)
                self.assertEqual(len(result.facts), 1, result.facts)
                self.assertEqual(result.warnings, [])

    def test_check_fact_texts(self):
        result = self._check([item(issue(1), order=1)], enabled={"Item closed": False})
        self.assertEqual(len(result.facts), 1, result.facts)
        self.assertIn("'Item closed'", result.facts[0])
        self.assertIn("enable it in the project UI (Workflows)", result.facts[0])
        self.assertEqual(len(result.warnings), 1, result.warnings)
        self.assertIn("Auto-add to project", result.warnings[0])
        result = self._check([item(issue(1), order=1)], options=("Backlog", "Ready", "Done"))
        self.assertEqual(len(result.facts), 3, result.facts)
        self.assertTrue(any("'In Progress'" in f for f in result.facts))

    def test_check_auto_add_is_a_hint_never_a_fact(self):
        """The real project lists these workflows and no Auto-add to project: a manually populated
        board with Item closed enabled has clean facts and one hint."""
        real = {"Auto-add sub-issues to project": True, "Auto-close issue": False,
                "Item added to project": False, "Item closed": True,
                "Pull request linked to issue": False, "Pull request merged": False}
        for enabled in (real, dict(real, **{"Auto-add to project": False})):
            with self.subTest(enabled=enabled):
                result = self._check([item(issue(1), order=1)], enabled=enabled)
                self.assertEqual(result.facts, [])
                self.assertEqual(len(result.warnings), 1, result.warnings)
                self.assertIn("consider enabling Auto-add to project, issues only", result.warnings[0])
                self.assertIn("cards can also be added manually", result.warnings[0])
        result = self._check([item(issue(1), order=1)],
                             enabled=dict(real, **{"Auto-add to project": True}))
        self.assertEqual((result.facts, result.warnings), ([], []))

    def test_check_item_closed_missing_is_a_fact(self):
        result = self._check([item(issue(1), order=1)], enabled={"Auto-add to project": True})
        self.assertEqual(len(result.facts), 1, result.facts)
        self.assertIn("'Item closed' is missing", result.facts[0])

    def test_check_missing_status_field_is_a_fact(self):
        fields = project_flow_fields_without_status()
        fake = FakeTransport([("query ProjectFields", fields)])
        result = project_flow.check(fake, CFG, URL)
        self.assertEqual(len(result.facts), 1, result.facts)
        self.assertIn("'Status'", result.facts[0])

    def test_check_unreadable_item_is_a_fact(self):
        result = self._check([item(None, item_id="PVTI_x", type="REDACTED")], texts=[])
        self.assertEqual(len(result.facts), 1, result.facts)
        self.assertIn("PVTI_x", result.facts[0])

    def test_check_prose_warning(self):
        result = self._check([item(issue(5), order=1)], texts=["Depends on #12"])
        self.assertEqual(result.facts, [])
        self.assertEqual(len(result.warnings), 1, result.warnings)
        self.assertIn("owner/repo#5", result.warnings[0])
        self.assertIn("#12", result.warnings[0])

    def test_check_prose_matches_native_blockers(self):
        native = [blocker(12, "OPEN", None), blocker(7, "OPEN", None, repo="other/lib")]
        result = self._check(
            [item(issue(5, blockers=native), order=1)],
            texts=["Depends on #12. Blocked by other/lib#7. after #9, AFTER Owner/Repo#12"])
        self.assertEqual(result.facts, [])
        self.assertEqual(len(result.warnings), 1, result.warnings)
        self.assertIn("#9", result.warnings[0])

    def test_check_prose_in_comments_and_closed_items_skipped(self):
        fake = FakeTransport(board([item(issue(5), order=1),
                                    item(issue(6, state="CLOSED", state_reason="COMPLETED"))])
                             + [text("body", comments=["this is blocked by #3"])])
        result = project_flow.check(fake, CFG, URL)
        self.assertEqual(len(result.warnings), 1, result.warnings)
        self.assertEqual(fake.responses, [])

    def test_check_notice_text(self):
        for result in (project_flow.check(FakeTransport(), CFG, None),
                       self._check([item(issue(1), order=1)])):
            self.assertIn("does not prove", result.notice)
            self.assertIn("PROJECT_URL", result.notice)

    def test_main_check_with_project(self):
        fake = FakeTransport(board([item(issue(1))]) + [text("Depends on #12")])
        code, out, _ = run_main(["check", "--config", DEFAULTS, "--project", URL], fake)
        self.assertEqual(code, 3)
        self.assertIn("fact: ", out)
        self.assertIn("warning: ", out)
        self.assertIn("does not prove", out)
        self.assertTrue(out.rstrip().endswith("stop: setup-incomplete: 1 setup facts need attention"), out)
        self.assertEqual(fake.mutations, [])

    def test_main_check_warnings_alone_exit_zero(self):
        fake = FakeTransport(board([item(issue(1), order=1)]) + [text("Depends on #12")])
        code, out, _ = run_main(["check", "--config", DEFAULTS, "--project", URL], fake)
        self.assertEqual(code, 0, out)
        self.assertIn("warning: ", out)
        self.assertIn("facts clean", out)
        self.assertNotIn("stop:", out)

    def test_setup_incomplete_is_a_named_reason(self):
        self.assertIn("setup-incomplete", project_flow.STOP_REASONS)
        self.assertIn("assignment-failed", project_flow.STOP_REASONS)
        self.assertIn("write-failed", project_flow.STOP_REASONS)
        self.assertEqual(len(project_flow.STOP_REASONS), 18)


def project_flow_fields_without_status():
    from project_flow_fake import fields
    response = fields()
    response["data"]["user"]["projectV2"]["status"] = None
    return response


if __name__ == "__main__":
    unittest.main()
