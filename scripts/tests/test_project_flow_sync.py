"""Tests for project_flow sync, Writer and preflight on FakeTransport boards. No network, no clock."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_flow  # noqa: E402
from project_flow import Config, GhError, Stop  # noqa: E402
from project_flow_fake import (  # noqa: E402
    FakeTransport, blocker, board, closing_issues, issue, item, item_response, milestone, pr,
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
    return project_flow.sync(fake, CFG, URL, mode=mode, repo=repo, dry_run=dry_run, only=only)


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


class MainSyncTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"status_field": CFG.status_field, "roles": CFG.roles,
                   "order_field": CFG.order_field, "blocked_label": CFG.blocked_label}, handle)
        handle.close()
        self.config = handle.name
        self.addCleanup(os.unlink, handle.name)

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


if __name__ == "__main__":
    unittest.main()
