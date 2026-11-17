"""Tests for project_flow.derive: a pure function of Facts and Config, no network or clock."""
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(SCRIPTS, "tests"))

import project_flow  # noqa: E402
from project_flow import Blocker, Config, Facts, PullRef  # noqa: E402

REPO = "o/r"
CFG = Config("Status", {}, "Order", "blocked")


def facts(state="OPEN", labels=(), assignees=(), prs=(), blockers=(), status=None):
    return Facts(
        item_id="PVTI_1", content_id="I_1", repo=REPO, number=1, state=state,
        labels=frozenset(n.lower() for n in labels), assignees=frozenset(assignees),
        closing_prs=tuple(prs), blockers=tuple(blockers), order=None, status=status,
        parent=None, sub_issues=(), milestone=None, database_id=1001)


def pull(state="OPEN", draft=False, number=9):
    return PullRef(REPO, number, state, draft, None)


def blk(state="CLOSED", reason="COMPLETED", number=5):
    return Blocker(REPO, number, state, reason)


class DeriveTest(unittest.TestCase):
    def test_last_blocker_closes_ready(self):
        role, _ = project_flow.derive(facts(blockers=[blk()]), CFG)
        self.assertEqual(role, "ready")

    def test_not_planned_blocker_backlog_reported(self):
        role, lines = project_flow.derive(facts(blockers=[blk(reason="NOT_PLANNED")]), CFG)
        self.assertEqual(role, "backlog")
        self.assertEqual(sum("not planned" in l for l in lines), 1)

    def test_duplicate_blocker_backlog_reported(self):
        role, lines = project_flow.derive(facts(blockers=[blk(reason="DUPLICATE")]), CFG)
        self.assertEqual(role, "backlog")
        self.assertTrue(any("duplicate" in l for l in lines))

    def test_open_blocker_backlog(self):
        role, _ = project_flow.derive(facts(blockers=[blk("OPEN", None)]), CFG)
        self.assertEqual(role, "backlog")

    def test_draft_pr_assigned_in_progress(self):
        f = facts(assignees=["a"], prs=[pull(draft=True)])
        self.assertEqual(project_flow.derive(f, CFG)[0], "in_progress")

    def test_ready_pr_in_review(self):
        f = facts(assignees=["a"], prs=[pull()])
        self.assertEqual(project_flow.derive(f, CFG)[0], "in_review")

    def test_pr_closed_unmerged_back_to_in_progress(self):
        f = facts(assignees=["a"], prs=[pull(state="CLOSED")])
        self.assertEqual(project_flow.derive(f, CFG)[0], "in_progress")

    def test_blocked_label_wins_over_pr(self):
        f = facts(labels=["blocked"], assignees=["a"], prs=[pull()])
        self.assertEqual(project_flow.derive(f, CFG)[0], "blocked")

    def test_blocked_label_case_insensitive(self):
        self.assertEqual(project_flow.derive(facts(labels=["Blocked"]), CFG)[0], "blocked")
        cfg = Config("Status", {}, "Order", "Blocked")
        self.assertEqual(project_flow.derive(facts(labels=["blocked"]), cfg)[0], "blocked")

    def test_closed_issue_done_even_with_label(self):
        f = facts(state="CLOSED", labels=["blocked"], assignees=["a"], prs=[pull()])
        self.assertEqual(project_flow.derive(f, CFG)[0], "done")

    def test_previous_status_ignored(self):
        self.assertEqual(project_flow.derive(facts(status="Done"), CFG)[0], "ready")

    def test_assigned_without_pr_in_progress(self):
        self.assertEqual(project_flow.derive(facts(assignees=["a"]), CFG)[0], "in_progress")


if __name__ == "__main__":
    unittest.main()
