"""Convergence of project_flow sync under concurrent writers, on FakeTransport boards.

GitHub Projects has no compare-and-set, so a stale write must be skipped by the Writer's fresh
read or undone by the next sync. Each test changes the facts between a read and a write with an
on_call hook. The cases that need next, start or followup live with those commands. No network,
no clock."""
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_flow  # noqa: E402
from project_flow import Config  # noqa: E402
from project_flow_fake import FakeTransport, board, issue, item, item_response, pr, status_written  # noqa: E402

URL = "https://github.com/users/owner/projects/2"
CFG = Config("Status", {"backlog": "Backlog", "ready": "Ready", "in_progress": "In Progress",
                        "in_review": "In Review", "blocked": "Blocked", "done": "Done"},
             "Implementation order", "blocked")
MILESTONES = ("repos/owner/repo/milestones?state=open", [])


def fresh(node):
    return ("query ProjectItem(", item_response(node))


def run_sync(fake, dry_run=False):
    return project_flow.sync(fake, CFG, URL, mode="agent", repo=None, dry_run=dry_run)


def options(fake):
    return [m["variables"]["option"] for m in fake.mutations
            if m["kind"] == "graphql" and "updateProjectV2ItemFieldValue" in m["query"]]


def change_at_fresh_read(fake, node):
    """When the Writer re-reads the item, serve `node`: the facts changed after the board read."""
    def hook(call):
        if call["kind"] == "graphql" and "query ProjectItem(" in call["query"]:
            fake.responses.insert(0, fresh(node))
    fake.on_call(hook)


class ConvergenceTest(unittest.TestCase):
    def test_stale_write_over_blocked_converges(self):
        # A stale writer left Ready on a blocked item: the next sync sets Blocked again.
        stale = item(issue(1, labels=["blocked"]), status="Ready")
        fake = FakeTransport(board([stale]) + [fresh(stale), ("mutation", status_written("PVTI_item1")),
                                               MILESTONES])
        run_sync(fake)
        self.assertEqual(options(fake), ["opt-Blocked"])

        # The label lands between the read and the write: the Writer skips, the next sync converges.
        before = item(issue(1), status="Backlog")
        labeled = item(issue(1, labels=["blocked"]), status="Backlog")
        racing = FakeTransport(board([before]) + [MILESTONES])
        change_at_fresh_read(racing, labeled)
        result = run_sync(racing)
        self.assertEqual(racing.mutations, [])
        self.assertTrue(any("inputs changed" in line for line in result.reports), result.reports)
        nxt = FakeTransport(board([labeled]) + [fresh(labeled), ("mutation", status_written("PVTI_item1")),
                                                MILESTONES])
        run_sync(nxt)
        self.assertEqual(options(nxt), ["opt-Blocked"])

    def test_stale_write_over_done_converges(self):
        # A stale writer left Ready on a closed, completed issue: the next sync sets Done again.
        stale = item(issue(1, state="CLOSED", state_reason="COMPLETED"), status="Ready")
        fake = FakeTransport(board([stale]) + [fresh(stale), ("mutation", status_written("PVTI_item1")),
                                               MILESTONES])
        run_sync(fake)
        self.assertEqual(options(fake), ["opt-Done"])

        # The issue closes between the read and the write: the Writer skips, the next sync sets Done.
        before = item(issue(1, assignees=["me"]), status="Ready")
        closed = item(issue(1, assignees=["me"], state="CLOSED", state_reason="COMPLETED"),
                      status="Ready")
        racing = FakeTransport(board([before]) + [MILESTONES])
        change_at_fresh_read(racing, closed)
        result = run_sync(racing)
        self.assertEqual(racing.mutations, [])
        self.assertTrue(any("inputs changed" in line for line in result.reports), result.reports)
        nxt = FakeTransport(board([closed]) + [fresh(closed), ("mutation", status_written("PVTI_item1")),
                                               MILESTONES])
        run_sync(nxt)
        self.assertEqual(options(nxt), ["opt-Done"])

    def test_pr_event_racing_sync(self):
        # The PR is marked ready after sync read it as a draft: no In Progress write lands over
        # the event, and the next sync derives In Review.
        draft = item(issue(1, assignees=["me"], prs=[pr(9, draft=True, milestone="M1")]), status="Ready")
        ready = item(issue(1, assignees=["me"], prs=[pr(9, draft=False, milestone="M1")]), status="Ready")
        racing = FakeTransport(board([draft]) + [MILESTONES])
        change_at_fresh_read(racing, ready)
        result = run_sync(racing)
        self.assertEqual(racing.mutations, [])
        self.assertTrue(any("inputs changed" in line for line in result.reports), result.reports)
        nxt = FakeTransport(board([ready]) + [fresh(ready), ("mutation", status_written("PVTI_item1")),
                                              MILESTONES])
        run_sync(nxt)
        self.assertEqual(options(nxt), ["opt-In Review"])

    def test_sync_dry_run_two_changes(self):
        nodes = [item(issue(1), status="Backlog"),
                 item(issue(2, state="CLOSED", state_reason="COMPLETED"), status="Ready")]
        fake = FakeTransport(board(nodes) + [fresh(nodes[0]), fresh(nodes[1]), MILESTONES])
        result = run_sync(fake, dry_run=True)
        self.assertEqual(fake.mutations, [])
        self.assertEqual(len(result.writes), 2)
        self.assertTrue(all(line.startswith("would write: ") for line in result.writes), result.writes)


if __name__ == "__main__":
    unittest.main()
