"""Tests for project_flow sync, Writer and preflight on FakeTransport boards. No network, no clock."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_flow  # noqa: E402
from project_flow import Config, GhError, Stop  # noqa: E402
from project_flow_fake import (  # noqa: E402
    FakeTransport, blocker, board, closing_issues, issue, item, item_response, milestone, pr, rate,
    status_written)

URL = "https://github.com/users/owner/projects/2"
REPO = "owner/repo"
CFG = Config("Status", {"backlog": "Backlog", "ready": "Ready", "in_progress": "In Progress",
                        "in_review": "In Review", "blocked": "Blocked", "done": "Done"},
             "Implementation order", "blocked")
SCOPE_ERROR = GhError("Your token has not been granted the required scopes; missing required scopes")


def open_milestones(repo=REPO, milestones=()):
    return ("repos/%s/milestones?state=open" % repo, list(milestones))


def all_milestones(repo=REPO, milestones=()):
    return ("repos/%s/milestones?state=all" % repo, list(milestones))


def pr_issue(number, repo=REPO, title=None):
    """The REST issue view of a PR, as the Writer re-reads it."""
    return ("repos/%s/issues/%d" % (repo, number),
            {"number": number, "milestone": {"title": title, "number": 1} if title else None})


def fresh(node):
    return ("query ProjectItem(", item_response(node))


def status_mutations(fake):
    return [m for m in fake.mutations if m["kind"] == "graphql"
            and "updateProjectV2ItemFieldValue" in m["query"]]


def patches(fake):
    return [m for m in fake.mutations if m["kind"] == "rest"]


def run_sync(fake, mode="agent", repo=None, dry_run=False, only=None):
    return project_flow._reconcile(fake, CFG, URL, mode=mode, repo=repo, dry_run=dry_run, only=only)


class SyncStatusTest(unittest.TestCase):
    def test_writes_only_differences(self):
        stale = item(issue(3, state="CLOSED", state_reason="COMPLETED"), status="In Progress")
        fake = FakeTransport(board([item(issue(1), status="Ready"),
                                    item(issue(2, assignees=["me"]), status="In Progress"),
                                    stale])
                             + [fresh(stale), ("mutation", status_written("PVTI_item3")),
                                open_milestones()])
        result = run_sync(fake)
        mutations = status_mutations(fake)
        self.assertEqual(len(mutations), 1)
        self.assertEqual(len(fake.mutations), 1)
        variables = mutations[0]["variables"]
        self.assertEqual(variables["item"], "PVTI_item3")
        self.assertEqual(variables["option"], "opt-Done")
        self.assertEqual(variables["project"], "PVT_project1")
        self.assertEqual(variables["field"], "PVTSSF_field1")
        self.assertEqual(len(result.writes), 1)
        self.assertTrue(result.writes[0].startswith("write: "), result.writes)

    def test_second_run_no_write(self):
        first_items = [item(issue(1, assignees=["me"], prs=[pr(9)], milestone="M1"), status="In Progress"),
                       item(issue(2), status="Backlog")]
        fake = FakeTransport(board(first_items)
                             + [fresh(first_items[0]), fresh(first_items[1]),
                                ("mutation", status_written()), ("mutation", status_written()),
                                ("closingIssuesReferences", closing_issues([(1, "M1")])),
                                pr_issue(9), all_milestones(milestones=[milestone(4, "M1")]),
                                ("repos/owner/repo/issues/9", {"number": 9}),
                                open_milestones()])
        run_sync(fake)
        self.assertEqual(len(fake.mutations), 3)
        # The second run sees the state the first run wrote.
        second_items = [item(issue(1, assignees=["me"], prs=[pr(9, milestone="M1")], milestone="M1"),
                             status="In Review"),
                        item(issue(2), status="Ready")]
        fake2 = FakeTransport(board(second_items) + [open_milestones()])
        result = run_sync(fake2)
        self.assertEqual(fake2.mutations, [])
        self.assertEqual(result.writes, [])

    def test_dry_run_writes_nothing(self):
        stale = item(issue(1), status="Backlog")
        fake = FakeTransport(board([stale]) + [fresh(stale), open_milestones()])
        result = run_sync(fake, dry_run=True)
        self.assertEqual(fake.mutations, [])
        self.assertEqual(len(result.writes), 1)
        self.assertTrue(result.writes[0].startswith("would write: "), result.writes)

    def test_unreadable_item_not_written_others_are(self):
        broken = item(issue(1, blockers=[blocker(5)], more=("blockedBy",)), status="Backlog")
        stale = item(issue(2), status="Backlog")
        fake = FakeTransport(board([broken, stale])
                             + [("IssueConnection_blockedBy", GhError("HTTP 502")),
                                fresh(stale), ("mutation", status_written("PVTI_item2")),
                                open_milestones()])
        result = run_sync(fake)
        self.assertEqual([m["variables"]["item"] for m in status_mutations(fake)], ["PVTI_item2"])
        self.assertEqual(result.unreadable, ["PVTI_item1"])
        self.assertTrue(any("PVTI_item1" in line for line in result.reports), result.reports)

    def test_derive_report_lines_reach_reports(self):
        node = item(issue(1, blockers=[blocker(5, reason="NOT_PLANNED")]), status="Backlog")
        result = run_sync(FakeTransport(board([node]) + [open_milestones()]))
        self.assertTrue(any("not planned" in line for line in result.reports), result.reports)

    def test_fresh_read_skips_changed_item(self):
        stale = item(issue(1), status="Backlog")
        fake = FakeTransport(board([stale]) + [open_milestones()])

        def assign_between_reads(call):
            if call["kind"] == "graphql" and "query ProjectItem(" in call["query"]:
                fake.responses.insert(0, fresh(item(issue(1, assignees=["other"]), status="Backlog")))
        fake.on_call(assign_between_reads)
        result = run_sync(fake)
        self.assertEqual(fake.mutations, [])
        self.assertEqual(result.writes, [])
        self.assertTrue(any("inputs changed" in line for line in result.reports), result.reports)

    def test_fresh_read_skips_same_role_input_change(self):
        node = item(issue(1, blockers=[blocker(5, state="OPEN", reason=None)]), status="Ready")
        changed = item(issue(1, blockers=[blocker(5, state="OPEN", reason=None),
                                          blocker(6, state="OPEN", reason=None)]), status="Ready")
        fake = FakeTransport(board([node]) + [fresh(changed), open_milestones()])
        self.assertEqual(project_flow.derive(
            project_flow.read_item(FakeTransport([fresh(changed)]), "PVTI_item1", CFG), CFG)[0], "backlog")
        result = run_sync(fake)
        self.assertEqual(fake.mutations, [])
        self.assertTrue(any("inputs changed" in line for line in result.reports), result.reports)

    def test_fresh_read_failure_skips_write(self):
        stale = item(issue(1), status="Backlog")
        fake = FakeTransport(board([stale]) + [("query ProjectItem(", GhError("HTTP 502")),
                                               open_milestones()])
        result = run_sync(fake)
        self.assertEqual(fake.mutations, [])
        self.assertTrue(any("skipped" in line and "owner/repo#1" in line for line in result.reports),
                        result.reports)


class SyncMilestoneTest(unittest.TestCase):
    def test_dropped_event_still_reconciled(self):
        node = item(issue(1, assignees=["me"], prs=[pr(9)], milestone="M1"), status="In Progress")
        fake = FakeTransport(board([node])
                             + [fresh(node), ("mutation", status_written()),
                                ("closingIssuesReferences", closing_issues([(1, "M1")])),
                                pr_issue(9), all_milestones(milestones=[milestone(2, "M0"), milestone(4, "M1")]),
                                ("repos/owner/repo/issues/9", {"number": 9}), open_milestones()])
        run_sync(fake)
        self.assertEqual(status_mutations(fake)[0]["variables"]["option"], "opt-In Review")
        self.assertEqual(len(patches(fake)), 1)
        patch = patches(fake)[0]
        self.assertEqual((patch["method"], patch["path"], patch["body"]),
                         ("PATCH", "repos/owner/repo/issues/9", {"milestone": 4}))

    def test_merged_pr_gets_milestone(self):
        node = item(issue(1, state="CLOSED", state_reason="COMPLETED", prs=[pr(9, state="MERGED")],
                          milestone="M1"), status="Done")
        fake = FakeTransport(board([node])
                             + [("closingIssuesReferences", closing_issues([(1, "M1")])),
                                pr_issue(9), all_milestones(milestones=[milestone(4, "M1")]),
                                ("repos/owner/repo/issues/9", {"number": 9}), open_milestones()])
        result = run_sync(fake)
        self.assertEqual([(m["path"], m["body"]) for m in fake.mutations],
                         [("repos/owner/repo/issues/9", {"milestone": 4})])
        self.assertTrue(any("M1" in line for line in result.writes), result.writes)

    def test_closed_pr_is_not_touched(self):
        node = item(issue(1, prs=[pr(9, state="CLOSED")], milestone="M1"), status="Ready")
        fake = FakeTransport(board([node]) + [open_milestones()])
        run_sync(fake)
        self.assertEqual(fake.mutations, [])

    def test_pr_with_milestone_unchanged(self):
        node = item(issue(1, prs=[pr(9, milestone="M0")], milestone="M1"), status="In Review")
        fake = FakeTransport(board([node]) + [open_milestones()])
        run_sync(fake)
        self.assertEqual(fake.mutations, [])
        self.assertFalse(any("closingIssuesReferences" in (c.get("query") or "") for c in fake.calls))

    def test_conflicting_milestones_reported_not_set(self):
        one = item(issue(1, prs=[pr(9)], milestone="M1"), status="In Review")
        two = item(issue(2, prs=[pr(9)], milestone="M2"), status="In Review")
        fake = FakeTransport(board([one, two])
                             + [("closingIssuesReferences", closing_issues([(1, "M1"), (2, "M2")])),
                                open_milestones()])
        result = run_sync(fake)
        self.assertEqual(fake.mutations, [])
        lines = [line for line in result.reports if "owner/repo#9" in line]
        self.assertEqual(len(lines), 1, result.reports)  # read once, reported once
        self.assertIn("M1", lines[0])
        self.assertIn("M2", lines[0])

    def test_pr_closing_off_project_issue_conflict(self):
        node = item(issue(1, prs=[pr(9)], milestone="X"), status="In Review")
        fake = FakeTransport(board([node])
                             + [("closingIssuesReferences",
                                 closing_issues([(1, "X"), (("other/repo", 5), "Y")])),
                                open_milestones()])
        result = run_sync(fake)
        self.assertEqual(fake.mutations, [])
        self.assertTrue(any("conflict" in line and "X" in line and "Y" in line
                            for line in result.reports), result.reports)

    def test_pr_closing_issue_unreadable_no_milestone(self):
        node = item(issue(1, prs=[pr(9)], milestone="M1"), status="In Review")
        fake = FakeTransport(board([node])
                             + [("closingIssuesReferences", GhError("HTTP 502")), open_milestones()])
        result = run_sync(fake)
        self.assertEqual(fake.mutations, [])
        self.assertTrue(any("owner/repo#9" in line for line in result.reports), result.reports)

    def test_missing_milestone_in_pr_repo_reported(self):
        node = item(issue(1, prs=[pr(9, repo="owner/other")], milestone="M1"), status="In Review")
        fake = FakeTransport(board([node])
                             + [("closingIssuesReferences", closing_issues([(1, "M1")])),
                                pr_issue(9, repo="owner/other"),
                                all_milestones("owner/other", [milestone(1, "M9")]), open_milestones()])
        result = run_sync(fake)
        self.assertEqual(fake.mutations, [])
        self.assertTrue(any("not found" in line and "owner/other" in line for line in result.reports),
                        result.reports)
        self.assertEqual(project_flow._summary(result),
                         "sync: partial (0 unreadable, 0 conflicts, 1 skipped)")

    def test_pr_milestone_set_between_read_and_patch_skipped(self):
        node = item(issue(1, prs=[pr(9)], milestone="M1"), status="In Review")
        fake = FakeTransport(board([node])
                             + [("closingIssuesReferences", closing_issues([(1, "M1")])),
                                all_milestones(milestones=[milestone(4, "M1")]),
                                pr_issue(9, title="M0"), open_milestones()])
        result = run_sync(fake)
        self.assertEqual(fake.mutations, [])
        self.assertTrue(any("milestone already set" in line for line in result.reports), result.reports)

    def test_pr_milestone_set_during_milestone_lookup_skipped(self):
        """The PR re-read is the last read before the PATCH: a milestone set while the
        repository's milestones are being looked up is not overwritten."""
        node = item(issue(1, prs=[pr(9)], milestone="M1"), status="In Review")
        fake = FakeTransport(board([node])
                             + [("closingIssuesReferences", closing_issues([(1, "M1")])),
                                all_milestones(milestones=[milestone(4, "M1")]),
                                pr_issue(9), ("repos/owner/repo/issues/9", {"number": 9}),
                                open_milestones()])

        def set_milestone_during_lookup(call):
            if call["kind"] == "rest" and "milestones?state=all" in call["path"]:
                fake.responses.insert(0, pr_issue(9, title="M0"))
        fake.on_call(set_milestone_during_lookup)
        result = run_sync(fake)
        self.assertEqual(patches(fake), [])
        self.assertTrue(any("milestone already set" in line for line in result.reports), result.reports)
        lookup = next(i for i, c in enumerate(fake.calls)
                      if c["kind"] == "rest" and "milestones?state=all" in c["path"])
        reread = next(i for i, c in enumerate(fake.calls)
                      if c["kind"] == "rest" and c["path"] == "repos/owner/repo/issues/9")
        self.assertLess(lookup, reread)

    def test_milestone_lookup_follows_pages(self):
        node = item(issue(1, prs=[pr(9)], milestone="M1"), status="In Review")
        page1 = [milestone(n, "Old %d" % n) for n in range(1, 101)]
        fake = FakeTransport(board([node])
                             + [("closingIssuesReferences", closing_issues([(1, "M1")])), pr_issue(9),
                                ("milestones?state=all&per_page=100&page=1", page1),
                                ("milestones?state=all&per_page=100&page=2", [milestone(200, "M1")]),
                                ("repos/owner/repo/issues/9", {"number": 9}), open_milestones()])
        run_sync(fake)
        self.assertEqual([m["body"] for m in patches(fake)], [{"milestone": 200}])


class EmptyMilestoneTest(unittest.TestCase):
    def test_empty_milestone_reported_not_closed(self):
        done = item(issue(1, state="CLOSED", state_reason="COMPLETED", milestone="M1"), status="Done")
        fake = FakeTransport(board([done])
                             + [open_milestones(milestones=[milestone(4, "M1", 0, 2)])])
        result = run_sync(fake)
        self.assertEqual(fake.mutations, [])
        lines = [line for line in result.reports if "M1" in line]
        self.assertEqual(len(lines), 1, result.reports)
        self.assertIn("complete", lines[0])

    def test_empty_milestone_requires_project_issue(self):
        node = item(issue(1, state="CLOSED", state_reason="COMPLETED", milestone="M1"), status="Done")
        fake = FakeTransport(board([node])
                             + [open_milestones(milestones=[milestone(5, "M2", 0, 3)])])
        result = run_sync(fake)
        self.assertFalse(any("complete" in line for line in result.reports), result.reports)

    def test_milestone_with_open_issues_not_reported(self):
        node = item(issue(1, state="CLOSED", state_reason="COMPLETED", milestone="M1"), status="Done")
        fake = FakeTransport(board([node]) + [open_milestones(milestones=[milestone(4, "M1", 1, 2)])])
        result = run_sync(fake)
        self.assertFalse(any("complete" in line for line in result.reports), result.reports)

    def test_unreadable_milestones_reported(self):
        node = item(issue(1), status="Ready")
        fake = FakeTransport(board([node]) + [("milestones?state=open", GhError("HTTP 502"))])
        result = run_sync(fake)
        self.assertTrue(any("milestones" in line for line in result.reports), result.reports)


class ScopeTest(unittest.TestCase):
    def two_repo_board(self):
        a_item = item(issue(1, repo="o/A", blockers=[blocker(7, repo="o/B")],
                            prs=[pr(11, state="MERGED", repo="o/A")],
                            milestone="MA"), status="Backlog")
        b_item = item(issue(2, repo="o/B", prs=[pr(9, repo="o/B")], milestone="MB"), status="Backlog")
        return a_item, b_item

    def test_action_mode_scope(self):
        a_item, b_item = self.two_repo_board()
        fake = FakeTransport(board([a_item, b_item])
                             + [fresh(a_item), ("mutation", status_written()),
                                ("closingIssuesReferences", closing_issues([((("o/A"), 1), "MA")])),
                                pr_issue(11, repo="o/A"), all_milestones("o/A", [milestone(3, "MA")]),
                                ("repos/o/A/issues/11", {"number": 11}), open_milestones("o/A")])
        result = run_sync(fake, mode="action", repo="o/A")
        self.assertEqual([m["variables"]["item"] for m in status_mutations(fake)], ["PVTI_item1"])
        # A's item is derived Ready from the closed blocker in B.
        self.assertEqual(status_mutations(fake)[0]["variables"]["option"], "opt-Ready")
        self.assertEqual([m["path"] for m in patches(fake)], ["repos/o/A/issues/11"])
        for call in fake.calls:
            self.assertNotIn("o/B", call.get("path") or "")
            if call["kind"] == "graphql" and "closingIssuesReferences" in call["query"]:
                self.assertEqual(call["variables"]["name"], "A")
        self.assertEqual(len(result.writes), 2)

    def test_agent_mode_covers_all(self):
        a_item, b_item = self.two_repo_board()
        fake = FakeTransport(board([a_item, b_item])
                             + [fresh(a_item), fresh(b_item),
                                ("mutation", status_written()), ("mutation", status_written()),
                                ("closingIssuesReferences", closing_issues([(("o/A", 1), "MA")])),
                                ("closingIssuesReferences", closing_issues([(("o/B", 2), "MB")])),
                                pr_issue(11, repo="o/A"), pr_issue(9, repo="o/B"),
                                all_milestones("o/A", [milestone(3, "MA")]),
                                all_milestones("o/B", [milestone(6, "MB")]),
                                ("repos/o/A/issues/11", {"number": 11}),
                                ("repos/o/B/issues/9", {"number": 9}),
                                open_milestones("o/A"), open_milestones("o/B")])
        run_sync(fake)
        self.assertEqual(sorted(m["variables"]["item"] for m in status_mutations(fake)),
                         ["PVTI_item1", "PVTI_item2"])
        self.assertEqual(sorted(m["path"] for m in patches(fake)),
                         ["repos/o/A/issues/11", "repos/o/B/issues/9"])

    def test_action_mode_needs_repo(self):
        for repo in (None, "", "A", "o/A/x"):
            with self.assertRaises(Stop) as caught:
                run_sync(FakeTransport([]), mode="action", repo=repo)
            self.assertEqual(caught.exception.reason, "config-invalid", repo)

    def test_only_limits_status_and_milestone_pass(self):
        one = item(issue(1, prs=[pr(9)], milestone="M1"), status="Backlog")
        two = item(issue(2, prs=[pr(10)], milestone="M1"), status="Backlog")
        fake = FakeTransport(board([one, two])
                             + [fresh(one), ("mutation", status_written()),
                                ("closingIssuesReferences", closing_issues([(1, "M1")])),
                                pr_issue(9), all_milestones(milestones=[milestone(4, "M1")]),
                                ("repos/owner/repo/issues/9", {"number": 9})])
        run_sync(fake, only=(REPO, 1))
        self.assertEqual([m["variables"]["item"] for m in status_mutations(fake)], ["PVTI_item1"])
        self.assertEqual([m["path"] for m in patches(fake)], ["repos/owner/repo/issues/9"])


class PreflightTest(unittest.TestCase):
    def test_preflight_order_field_only_when_needed(self):
        stale = item(issue(1), status="Backlog")
        fake = FakeTransport(board([stale], order=False)
                             + [fresh(stale), ("mutation", status_written()), open_milestones()])
        run_sync(fake)
        self.assertEqual(len(fake.mutations), 1)
        result = project_flow.read_board(FakeTransport(board([], order=False)), URL, CFG)
        project_flow.preflight(result, CFG, needs_order=False)
        with self.assertRaises(Stop) as caught:
            project_flow.preflight(result, CFG, needs_order=True)
        self.assertEqual(caught.exception.reason, "missing-option")
        self.assertIn("Implementation order", caught.exception.detail)

    def test_preflight_names_every_missing_option(self):
        result = project_flow.read_board(
            FakeTransport(board([], options=("Backlog", "Ready", "Done"), order=False)), URL, CFG)
        with self.assertRaises(Stop) as caught:
            project_flow.preflight(result, CFG, needs_order=True)
        for name in ("In Progress", "In Review", "Blocked", "Implementation order"):
            self.assertIn(name, caught.exception.detail)

    def test_missing_option_no_writes(self):
        options = ("Backlog", "Ready", "In Progress", "Blocked", "Done")
        fake = FakeTransport(board([item(issue(1), status="Backlog"),
                                    item(issue(2, prs=[pr(9)]), status="Backlog")], options=options))
        with self.assertRaises(Stop) as caught:
            run_sync(fake)
        self.assertEqual(caught.exception.reason, "missing-option")
        self.assertIn("In Review", caught.exception.detail)
        self.assertEqual(fake.mutations, [])


class WriterTest(unittest.TestCase):
    def test_set_order(self):
        result = project_flow.read_board(FakeTransport(board([])), URL, CFG)
        fake = FakeTransport([("mutation", status_written())])
        out = []
        project_flow.Writer(fake, result, CFG, False, out).set_order("PVTI_item1", 2.5)
        self.assertEqual(len(fake.mutations), 1)
        self.assertEqual(fake.mutations[0]["variables"]["value"], 2.5)
        self.assertEqual(fake.mutations[0]["variables"]["field"], "PVTF_field1")
        self.assertTrue(out[0].startswith("write: "), out)
        dry = []
        project_flow.Writer(FakeTransport([]), result, CFG, True, dry).set_order("PVTI_item1", 2.5)
        self.assertTrue(dry[0].startswith("would write: "), dry)


class CliCase(unittest.TestCase):
    """A config file on disk and the public CLI in action mode; holds no tests."""

    def setUp(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"status_field": CFG.status_field, "roles": CFG.roles,
                   "order_field": CFG.order_field, "blocked_label": CFG.blocked_label}, handle)
        handle.close()
        self.config = handle.name
        self.addCleanup(os.unlink, handle.name)

    def main_sync(self, fake, *extra, repo=REPO):
        """The public CLI in action mode, PROJECT_URL in the environment: (exit code, stdout)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = project_flow.main(["sync", "--project", URL, "--config", self.config,
                                      "--mode", "action", "--repo", repo] + list(extra), fake,
                                     env={"PROJECT_URL": URL})
        return code, out.getvalue()


class MainSyncTest(CliCase):
    def test_missing_scope_message_via_sync(self):
        fake = FakeTransport([(None, SCOPE_ERROR)])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = project_flow.main(["sync", "--project", URL, "--config", self.config,
                                      "--mode", "action", "--repo", REPO], fake,
                                     env={"PROJECT_URL": URL})
        self.assertEqual(code, 3)
        self.assertIn("stop: missing-scope: run gh auth refresh -s project", out.getvalue())

    def test_sync_prints_writes_and_reports(self):
        stale = item(issue(1), status="Backlog")
        fake = FakeTransport([("repos/%s/actions/variables/PROJECT_URL" % REPO,
                               {"name": "PROJECT_URL", "value": URL})]
                             + board([stale]) + [fresh(stale), open_milestones()])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = project_flow.main(["sync", "--project", URL, "--repo", REPO, "--config", self.config,
                                      "--dry-run"], fake, env={})
        self.assertEqual(code, 0)
        self.assertIn("would write: ", out.getvalue())
        self.assertEqual(fake.mutations, [])

    def test_sync_without_project_is_usage(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = project_flow.main(["sync", "--config", self.config], FakeTransport([]))
        self.assertEqual(code, 2)

    def test_action_mode_sync_writes_through_the_cli(self):
        stale = item(issue(1), status="Backlog", order=2)
        fake = FakeTransport(board([stale]) + [fresh(stale), ("mutation", status_written()),
                                               open_milestones()])
        code, out = self.main_sync(fake)
        self.assertEqual(code, 0, out)
        self.assertEqual([m["variables"]["option"] for m in status_mutations(fake)], ["opt-Ready"])
        self.assertIn("write: owner/repo#1 Status: Backlog -> Ready\n", out)
        self.assertNotIn("no changes", out)
        self.assertEqual(fake.responses, [])
        # No repository-variable read: action mode takes PROJECT_URL from the environment.
        self.assertFalse(any("actions/variables" in (c.get("path") or "") for c in fake.calls))


class WriteFailedTest(CliCase):
    """A rejected mutation is a named stop (write-failed), never a skipped line and exit 0."""

    def test_failed_status_write_stops_sync(self):
        stale = item(issue(1), status="Backlog", order=2)
        fake = FakeTransport(board([stale]) + [fresh(stale), ("mutation", GhError("gh: HTTP 403")),
                                               open_milestones()])
        code, out = self.main_sync(fake)
        self.assertEqual(code, 3, out)
        lines = out.splitlines()
        self.assertEqual(lines[-1], "stop: write-failed: 1 write(s) failed")
        self.assertTrue(any(line.startswith("failed write owner/repo#1") and "HTTP 403" in line
                            for line in lines), out)
        self.assertNotIn("no changes", out)
        self.assertNotIn("ready: owner/repo#1", out)  # the write did not land

    def test_failed_write_does_not_stop_other_writes(self):
        one = item(issue(1), status="Backlog", order=1)
        two = item(issue(2), status="Backlog", order=2)

        def writes(item_id):
            return lambda c: c["kind"] == "graphql" and c["query"].lstrip().startswith("mutation") \
                and c["variables"]["item"] == item_id
        fake = FakeTransport(board([one, two]) + [
            fresh(one), fresh(two), (writes("PVTI_item1"), GhError("gh: HTTP 403")),
            (writes("PVTI_item2"), status_written("PVTI_item2")), open_milestones()])
        code, out = self.main_sync(fake)
        self.assertEqual(code, 3, out)
        self.assertEqual([m["variables"]["item"] for m in status_mutations(fake)],
                         ["PVTI_item1", "PVTI_item2"])
        self.assertIn("write: owner/repo#2 Status: Backlog -> Ready", out)
        self.assertIn("failed write owner/repo#1", out)
        self.assertLess(out.index("write: owner/repo#2"), out.index("stop: write-failed"))
        self.assertTrue(out.rstrip().endswith("stop: write-failed: 1 write(s) failed"), out)
        self.assertEqual(fake.responses, [])

    def test_failed_milestone_patch_stops_sync(self):
        node = item(issue(1, prs=[pr(9)], milestone="M1"), status="In Review")
        fake = FakeTransport(board([node]) + [
            ("closingIssuesReferences", closing_issues([(1, "M1")])),
            all_milestones(milestones=[milestone(4, "M1")]), pr_issue(9),
            (lambda c: c["kind"] == "rest" and c["method"] == "PATCH", GhError("gh: HTTP 403")),
            open_milestones()])
        code, out = self.main_sync(fake)
        self.assertEqual(code, 3, out)
        self.assertEqual(len(patches(fake)), 1)
        self.assertIn("failed write owner/repo#9", out)
        self.assertTrue(out.rstrip().endswith("stop: write-failed: 1 write(s) failed"), out)

    def test_result_records_failures_structurally(self):
        stale = item(issue(1), status="Backlog")
        fake = FakeTransport(board([stale]) + [fresh(stale), ("mutation", GhError("gh: HTTP 403")),
                                               open_milestones()])
        result = run_sync(fake)
        self.assertEqual(result.writes, [])
        self.assertEqual([ref for ref, _ in result.failed], ["owner/repo#1"])
        self.assertIn("HTTP 403", result.failed[0][1])

    def test_completed_write_printed_before_a_fatal_error(self):
        # Item 1 is written; item 2's guarded read then fails fatally (a scope error): the
        # completed write is still on stdout before the stop line.
        one = item(issue(1), status="Backlog", order=1)
        two = item(issue(2), status="Backlog", order=2)
        fake = FakeTransport(board([one, two]) + [fresh(one), ("mutation", status_written())])

        def scope_error_on_second_read(call):
            if call["kind"] == "graphql" and "query ProjectItem(" in call["query"] \
                    and call["variables"]["item"] == "PVTI_item2":
                fake.responses.insert(0, ("query ProjectItem(", SCOPE_ERROR))
        fake.on_call(scope_error_on_second_read)
        code, out = self.main_sync(fake)
        self.assertEqual(code, 3, out)
        self.assertEqual(len(status_mutations(fake)), 1)
        self.assertIn("write: owner/repo#1 Status: Backlog -> Ready\n", out)
        self.assertLess(out.index("write: owner/repo#1"), out.index("stop: missing-scope"))


class ReadyLinesTest(CliCase):
    """`ready: <repo>#<n> order=<v>` for every item Ready after the reconciliation."""

    def test_already_ready_item_is_listed(self):
        # Written Ready by someone else before this sync: no write, still listed.
        nodes = [item(issue(1), status="Ready", order=5)]
        fake = FakeTransport(board(nodes) + [open_milestones()])
        code, out = self.main_sync(fake)
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.mutations, [])
        self.assertIn("ready: owner/repo#1 order=5\n", out)

    def test_sorted_by_order_then_number_unordered_last(self):
        nodes = [item(issue(4), status="Ready", order=2), item(issue(3), status="Ready"),
                 item(issue(2), status="Ready", order=2), item(issue(1), status="Ready", order=1.5),
                 item(issue(5, assignees=["me"]), status="In Progress", order=0)]
        fake = FakeTransport(board(nodes) + [open_milestones()])
        code, out = self.main_sync(fake)
        self.assertEqual(code, 0, out)
        ready = [line for line in out.splitlines() if line.startswith("ready: ")]
        self.assertEqual(ready, ["ready: owner/repo#1 order=1.5", "ready: owner/repo#2 order=2",
                                 "ready: owner/repo#4 order=2", "ready: owner/repo#3 order=-"])

    def test_equal_order_sorts_by_number_before_repository(self):
        nodes = [item(issue(5, repo="a/first"), status="Ready", order=2),
                 item(issue(3, repo="z/last"), status="Ready", order=2)]
        fake = FakeTransport(board(nodes) + [open_milestones("a/first"), open_milestones("z/last")])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = project_flow.main(["sync", "--project", URL, "--config", self.config, "--repo", REPO],
                                     FakeTransport([("actions/variables/PROJECT_URL",
                                                     {"name": "PROJECT_URL", "value": URL})]
                                                   + fake.responses), env={})
        self.assertEqual(code, 0, out.getvalue())
        ready = [line for line in out.getvalue().splitlines() if line.startswith("ready: ")]
        self.assertEqual(ready, ["ready: z/last#3 order=2", "ready: a/first#5 order=2"])

    def test_written_ready_listed_and_stale_ready_not(self):
        promoted = item(issue(1), status="Backlog", order=1)
        demoted = item(issue(2, assignees=["me"]), status="Ready", order=2)
        fake = FakeTransport(board([promoted, demoted]) + [
            fresh(promoted), fresh(demoted), ("mutation", status_written()),
            ("mutation", status_written("PVTI_item2")), open_milestones()])
        code, out = self.main_sync(fake)
        self.assertEqual(code, 0, out)
        ready = [line for line in out.splitlines() if line.startswith("ready: ")]
        self.assertEqual(ready, ["ready: owner/repo#1 order=1"])

    def test_action_mode_lists_only_its_repository(self):
        nodes = [item(issue(1, repo="o/A"), status="Ready", order=1),
                 item(issue(2, repo="o/B"), status="Ready", order=2)]
        fake = FakeTransport(board(nodes) + [open_milestones("o/A")])
        code, out = self.main_sync(fake, repo="o/A")
        self.assertEqual(code, 0, out)
        ready = [line for line in out.splitlines() if line.startswith("ready: ")]
        self.assertEqual(ready, ["ready: o/A#1 order=1"])

    def test_dry_run_lists_what_would_be_ready(self):
        nodes = [item(issue(1), status="Backlog", order=3)]
        fake = FakeTransport(board(nodes) + [fresh(nodes[0]), open_milestones()])
        code, out = self.main_sync(fake, "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.mutations, [])
        self.assertIn("would write: owner/repo#1 Status: Backlog -> Ready", out)
        self.assertIn("ready: owner/repo#1 order=3\n", out)


class RateLimitTest(CliCase):
    """The items query reads the GraphQL rate limit; sync prints its total as the last line."""

    def test_items_query_selects_rate_limit_only_there(self):
        self.assertIn("rateLimit { cost remaining resetAt }", project_flow.ITEMS_QUERY)
        for query in (project_flow.FIELDS_QUERY, project_flow.ITEM_QUERY,
                      project_flow.WORKFLOWS_QUERY):
            self.assertNotIn("rateLimit", query)

    def test_cost_accumulates_across_pages(self):
        nodes = [item(issue(1), status="Ready", order=1), item(issue(2), status="Ready", order=2)]
        fake = FakeTransport(board(nodes, page_size=1, rate_limits=[rate(1, 4999), rate(2, 4997)])
                             + [open_milestones()])
        code, out = self.main_sync(fake)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines()[-1], "rate-limit: cost=3 remaining=4997")

    def test_missing_or_malformed_rate_limit_is_unknown(self):
        for limits in ([None], [{"cost": "x", "remaining": None}], ["nope"]):
            with self.subTest(limits=limits):
                fake = FakeTransport(board([item(issue(1), status="Ready", order=1)],
                                           rate_limits=limits) + [open_milestones()])
                code, out = self.main_sync(fake)
                self.assertEqual(code, 0, out)
                self.assertEqual(out.splitlines()[-1], "rate-limit: cost=0 remaining=-")

    def test_rate_limit_line_precedes_a_stop(self):
        stale = item(issue(1), status="Backlog")
        fake = FakeTransport(board([stale], rate_limits=[rate(1, 10)])
                             + [fresh(stale), ("mutation", GhError("gh: HTTP 403")), open_milestones()])
        code, out = self.main_sync(fake)
        self.assertEqual(code, 3, out)
        self.assertEqual(out.splitlines()[-2:], ["rate-limit: cost=1 remaining=10",
                                                 "stop: write-failed: 1 write(s) failed"])


class SummaryLineTest(CliCase):
    """`sync: complete` only when nothing was unreadable, conflicting, skipped or rejected; otherwise
    `sync: partial (...)`. It precedes `rate-limit:`, which stays the last line before any stop."""

    def summary(self, out):
        lines = out.splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("rate-limit: "))
        return lines[index - 1]

    def test_clean_run_is_complete(self):
        fake = FakeTransport(board([item(issue(1), status="Ready", order=1)]) + [open_milestones()])
        code, out = self.main_sync(fake)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines()[-2:], ["sync: complete", "rate-limit: cost=0 remaining=-"])

    def test_dry_run_with_writes_is_complete(self):
        stale = item(issue(1), status="Backlog", order=1)
        fake = FakeTransport(board([stale]) + [fresh(stale), open_milestones()])
        code, out = self.main_sync(fake, "--dry-run")
        self.assertEqual((code, self.summary(out)), (0, "sync: complete"), out)

    def test_partial_cases_exit_zero(self):
        conflict = [item(issue(1, prs=[pr(9)], milestone="M1"), status="In Review"),
                    item(issue(2, prs=[pr(9)], milestone="M2"), status="In Review")]
        changed_before = item(issue(1), status="Backlog", order=1)
        changed_after = item(issue(1, assignees=["x"]), status="Backlog", order=1)
        cases = {
            "unreadable item": (
                board([item(issue(1), status="Ready"), item(None, item_id="PVTI_hidden", type="REDACTED")])
                + [open_milestones()], "sync: partial (1 unreadable, 0 conflicts, 0 skipped)"),
            "guarded read failed": (
                board([changed_before]) + [("query ProjectItem(", GhError("gh: HTTP 502")),
                                           open_milestones()],
                "sync: partial (1 unreadable, 0 conflicts, 0 skipped)"),
            "conflicting milestones": (
                board(conflict) + [("closingIssuesReferences", closing_issues([(1, "M1"), (2, "M2")])),
                                   open_milestones()],
                "sync: partial (0 unreadable, 1 conflicts, 0 skipped)"),
            "closing issues unreadable": (
                board(conflict[:1]) + [("closingIssuesReferences", GhError("gh: HTTP 502")),
                                       open_milestones()],
                "sync: partial (0 unreadable, 0 conflicts, 1 skipped)"),
            "PR re-read unreadable": (
                board(conflict[:1]) + [("closingIssuesReferences", closing_issues([(1, "M1")])),
                                       all_milestones(milestones=[milestone(4, "M1")]),
                                       ("repos/owner/repo/issues/9", GhError("gh: HTTP 502")),
                                       open_milestones()],
                "sync: partial (0 unreadable, 0 conflicts, 1 skipped)"),
            "milestone not found": (
                board(conflict[:1]) + [("closingIssuesReferences", closing_issues([(1, "M1")])),
                                       all_milestones(milestones=[milestone(4, "M9")]),
                                       open_milestones()],
                "sync: partial (0 unreadable, 0 conflicts, 1 skipped)"),
            "milestones unreadable": (
                board([item(issue(1), status="Ready")]) + [("milestones?state=open", GhError("gh: HTTP 502"))],
                "sync: partial (0 unreadable, 0 conflicts, 1 skipped)"),
            "inputs changed": (
                board([changed_before]) + [fresh(changed_after), open_milestones()],
                "sync: partial (0 unreadable, 0 conflicts, 1 skipped)"),
        }
        for label, (responses, expected) in cases.items():
            with self.subTest(label):
                code, out = self.main_sync(FakeTransport(responses))
                self.assertEqual(code, 0, out)
                self.assertEqual(self.summary(out), expected, out)
                self.assertTrue(out.splitlines()[-1].startswith("rate-limit: "), out)

    def test_failed_write_is_partial_and_stops_after_rate_limit(self):
        stale = item(issue(1), status="Backlog", order=1)
        fake = FakeTransport(board([stale]) + [fresh(stale), ("mutation", GhError("gh: HTTP 403")),
                                               open_milestones()])
        code, out = self.main_sync(fake)
        self.assertEqual(code, 3, out)
        self.assertEqual(out.splitlines()[-3:], [
            "sync: partial (0 unreadable, 0 conflicts, 1 skipped)", "rate-limit: cost=0 remaining=-",
            "stop: write-failed: 1 write(s) failed"])



class SafeOutputTest(CliCase):
    """Untrusted text (titles, gh error texts) cannot start a line, fake one or close a fence."""

    PAYLOAD = "M1\n::error title=x::pwned\r\n```\u2028::set-output name=a::b\x1b[2J"

    def assert_inert(self, out):
        for line in out.splitlines():
            self.assertFalse(line.startswith("::"), line)
            self.assertNotIn("`", line)
            self.assertNotIn("\x1b", line)

    def test_safe(self):
        safe = project_flow._safe
        self.assertEqual(safe("a\nb\rc\td\x1be\u2028f\x85g"), "a b c d e f g")
        self.assertEqual(safe("```x`"), "'''x'")
        self.assertEqual(safe("::error::x"), "::error::x")  # harmless once it cannot start a line
        self.assertEqual(safe("x" * 500), "x" * 197 + "...")
        self.assertEqual(len(safe("x" * 500, limit=None)), 500)
        self.assertEqual(safe(safe(self.PAYLOAD)), safe(self.PAYLOAD))

    def test_milestone_title_in_a_write_line(self):
        node = item(issue(1, prs=[pr(9)], milestone=self.PAYLOAD), status="In Review")
        fake = FakeTransport(board([node]) + [
            ("closingIssuesReferences", closing_issues([(1, self.PAYLOAD)])),
            all_milestones(milestones=[milestone(4, self.PAYLOAD)]), pr_issue(9),
            ("repos/owner/repo/issues/9", {"number": 9}), open_milestones()])
        code, out = self.main_sync(fake)
        self.assertEqual(code, 0, out)
        self.assertEqual([m["body"] for m in patches(fake)], [{"milestone": 4}])  # matched raw
        self.assertIn("write: owner/repo#9 milestone: M1 ::error title=x::pwned  ''' ::set-output "
                      "name=a::b [2J\n", out)
        self.assert_inert(out)

    def test_conflicting_titles_and_gh_error_text(self):
        one = item(issue(1, prs=[pr(9)], milestone="A`"), status="In Review")
        two = item(issue(2, prs=[pr(9)], milestone=self.PAYLOAD), status="In Review")
        partial = item(issue(3, labels=["a"], more=("labels",)), status="Ready")
        fake = FakeTransport(board([one, two, partial]) + [
            ("IssueConnection_labels", GhError("gh: HTTP 502\n::error::spoofed ```")),
            ("closingIssuesReferences", closing_issues([(1, "A`"), (2, self.PAYLOAD)])),
            open_milestones()])
        code, out = self.main_sync(fake)
        self.assertEqual(code, 0, out)
        self.assertIn("unreadable PVTI_item3: ", out)
        self.assertIn("HTTP 502 ::error::spoofed '''", out)
        self.assertIn("conflicting milestones A', M1 ::error", out)
        self.assert_inert(out)

    def test_main_error_line_on_stderr(self):
        err = io.StringIO()
        failing = mock.Mock(side_effect=GhError("boom\n::error::x `y`"))
        with mock.patch.dict(project_flow._COMMANDS, {"sync": failing}), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = project_flow.main(["sync", "--project", URL, "--config", self.config, "--mode",
                                      "action", "--repo", REPO], FakeTransport([]),
                                     env={"PROJECT_URL": URL})
        self.assertEqual(code, 1)
        self.assertEqual(err.getvalue(), "error: boom ::error::x 'y'\n")


if __name__ == "__main__":
    unittest.main()
