"""Convergence of project_flow sync under concurrent writers, on FakeTransport boards.

GitHub Projects has no compare-and-set, so a stale write must be skipped by the Writer's fresh
read or undone by the next sync. Each test changes the facts between a read and a write with an
on_call hook; a claim racing a sync runs `start` on its own fake from inside the sync's hook, so
the order the writes land in is the order of the calls. No network, no clock."""
import contextlib
import io
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
    return project_flow._reconcile(fake, CFG, URL, mode="agent", repo=None, dry_run=dry_run)


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


VARIABLE = "repos/owner/repo/actions/variables/PROJECT_URL"
ENABLED = (VARIABLE, {"name": "PROJECT_URL", "value": URL})
ASSIGNED = ("/assignees", {"number": 1})


def user(login):
    return (lambda c: c["kind"] == "rest" and c["path"] == "user", {"login": login})


def quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        result = fn(*args, **kwargs)
    return result, out.getvalue()


def start(fake, dry_run=False):
    return quiet(project_flow.start, fake, CFG, URL, "owner/repo", ("owner/repo", 1), force=False,
                 dry_run=dry_run)


def claim_responses(before, after, login="me"):
    """A complete start on item 1: the board and fresh read show `before`, the re-read and the
    final reconcile show `after`."""
    return ([ENABLED] + board([before]) + [fresh(before), user(login), ASSIGNED,
                                           ("mutation", status_written())]
            + [fresh(after)] + board([after]))


def log_mutations(fake, name, log):
    def hook(call):
        if call in fake.mutations and call is fake.mutations[-1]:
            log.append((name, "status " + call["variables"]["option"] if call["kind"] == "graphql"
                        else "assign " + call["body"]["assignees"][0]))
    fake.on_call(hook)


def before_first_mutation(fake, action):
    """Run action() once, when `fake` sends its first mutation, before it lands."""
    done = []

    def hook(call):
        if not done and call in fake.mutations:
            done.append(True)
            action()
    fake.on_call(hook)


class ClaimConvergenceTest(unittest.TestCase):
    def test_stale_ready_after_claim_converges(self):
        # Sync A reads item 1 unassigned (its blocker just completed) and re-reads it unchanged;
        # start claims it before A's Ready write lands; A's write lands last.
        unblocked = item(issue(1, blockers=[(5, "CLOSED", "COMPLETED")]), status="Backlog", order=1)
        claimed = item(issue(1, blockers=[(5, "CLOSED", "COMPLETED")], assignees=["me"]),
                       status="In Progress", order=1)
        log = []
        claim = FakeTransport(claim_responses(unblocked, claimed))
        sync_a = FakeTransport(board([unblocked]) + [fresh(unblocked), ("mutation", status_written()),
                                                     MILESTONES])
        before_first_mutation(sync_a, lambda: start(claim))
        log_mutations(claim, "start", log)
        log_mutations(sync_a, "sync A", log)
        run_sync(sync_a)
        self.assertEqual(log, [("start", "assign me"), ("start", "status opt-In Progress"),
                               ("sync A", "status opt-Ready")])

        # The board now says Ready on an assigned item. next skips it (dry run lists the repair) ...
        stale = item(issue(1, blockers=[(5, "CLOSED", "COMPLETED")], assignees=["me"]),
                     status="Ready", order=1)
        other = item(issue(2), status="Ready", order=2)
        nxt = FakeTransport([ENABLED] + board([stale, other]) + [fresh(stale), MILESTONES])
        chosen, out = quiet(project_flow.next_task, nxt, CFG, URL, "owner/repo", skip_unordered=False,
                            dry_run=True)
        self.assertEqual(chosen.number, 2)
        self.assertIn("would write: owner/repo#1 Status: Ready -> In Progress", out)
        self.assertEqual(nxt.mutations, [])
        # ... and the next sync writes In Progress.
        repair = FakeTransport(board([stale, other]) + [fresh(stale), ("mutation", status_written()),
                                                        MILESTONES])
        run_sync(repair)
        self.assertEqual(options(repair), ["opt-In Progress"])

    def test_backlog_demotion_racing_claim(self):
        # Start claims a Ready item; a sync that saw a newly added open blocker demotes it to
        # Backlog. Assignment outranks blockers, so the next sync restores In Progress.
        free = item(issue(1), status="Ready", order=1)
        claimed = item(issue(1, assignees=["me"]), status="In Progress", order=1)
        gated = item(issue(1, blockers=[(7, "OPEN", None)]), status="Ready", order=1)
        log = []
        claim = FakeTransport(claim_responses(free, claimed))
        sync_b = FakeTransport(board([gated]) + [fresh(gated), ("mutation", status_written()), MILESTONES])
        before_first_mutation(sync_b, lambda: start(claim))
        log_mutations(claim, "start", log)
        log_mutations(sync_b, "sync B", log)
        run_sync(sync_b)
        self.assertEqual(log[-1], ("sync B", "status opt-Backlog"))
        demoted = item(issue(1, blockers=[(7, "OPEN", None)], assignees=["me"]), status="Backlog", order=1)
        repair = FakeTransport(board([demoted]) + [fresh(demoted), ("mutation", status_written()),
                                                   MILESTONES])
        run_sync(repair)
        self.assertEqual(options(repair), ["opt-In Progress"])

        # When the claim lands before the demoting sync's fresh read, that write is skipped.
        racing = FakeTransport(board([gated]) + [MILESTONES])
        change_at_fresh_read(racing, item(issue(1, blockers=[(7, "OPEN", None)], assignees=["me"]),
                                          status="In Progress", order=1))
        result = run_sync(racing)
        self.assertEqual(racing.mutations, [])
        self.assertTrue(any("inputs changed" in line for line in result.reports), result.reports)

    def test_two_accounts_claim(self):
        # Account "other" claims between my assignment and my re-read: the other claim completes
        # with only itself visible, my re-read sees both and stops. At most one proceeds.
        free = item(issue(1), status="Ready", order=1)
        theirs = FakeTransport(claim_responses(free, item(issue(1, assignees=["other"]),
                                                          status="In Progress", order=1), login="other"))
        mine = FakeTransport([ENABLED] + board([free]) + [
            fresh(free), user("me"), ASSIGNED, ("mutation", status_written()),
            fresh(item(issue(1, assignees=["me", "other"]), status="In Progress", order=1))])
        before_first_mutation(mine, lambda: start(theirs))
        with self.assertRaises(project_flow.Stop) as ctx:
            start(mine)
        self.assertEqual(ctx.exception.reason, "collision")
        self.assertIn("other", ctx.exception.detail)
        self.assertEqual(theirs.responses, [])  # the other claim ran to the end
        self.assertEqual(mine.responses, [])    # no reconcile after the collision

    def test_next_uses_facts_refreshed_during_reconciliation(self):
        # Item 1 derives Ready from the board read; before its guarded fresh read it is assigned
        # (or gains an open blocker). The write is skipped and next must not select it.
        unassigned = item(issue(1), status="Backlog", order=1)
        later = item(issue(2), status="Ready", order=2)
        for name, changed in (("assigned", item(issue(1, assignees=["someone"]), status="Backlog", order=1)),
                              ("blocked", item(issue(1, blockers=[(7, "OPEN", None)]), status="Backlog",
                                               order=1))):
            with self.subTest(name):
                fake = FakeTransport([ENABLED] + board([unassigned, later]) + [MILESTONES])
                change_at_fresh_read(fake, changed)
                chosen, out = quiet(project_flow.next_task, fake, CFG, URL, "owner/repo",
                                    skip_unordered=False, dry_run=False)
                self.assertEqual(chosen.number, 2)
                self.assertIn("inputs changed", out)
                self.assertEqual(fake.mutations, [])
                # Alone on the board, nothing is Ready.
                fake = FakeTransport([ENABLED] + board([unassigned]) + [MILESTONES])
                change_at_fresh_read(fake, changed)
                with self.assertRaises(project_flow.Stop) as ctx:
                    quiet(project_flow.next_task, fake, CFG, URL, "owner/repo", skip_unordered=False,
                          dry_run=False)
                self.assertEqual(ctx.exception.reason, "nothing-ready")
                self.assertEqual(fake.mutations, [])

    def test_fresh_read_failure_stops_next_not_sync(self):
        # Item 1 is readable on the board read; its fresh read during reconciliation fails.
        node = item(issue(1), status="Backlog", order=1)
        failing = ("query ProjectItem(", project_flow.GhError("gh: HTTP 502"))
        fake = FakeTransport([ENABLED] + board([node, item(issue(2), status="Ready", order=2)])
                             + [failing, MILESTONES])
        with self.assertRaises(project_flow.Stop) as ctx:
            quiet(project_flow.next_task, fake, CFG, URL, "owner/repo", skip_unordered=False, dry_run=False)
        self.assertEqual(ctx.exception.reason, "incomplete-read")
        self.assertIn("PVTI_item1", ctx.exception.detail)
        self.assertEqual(fake.mutations, [])
        # sync reports the same failure structurally and still completes.
        fake = FakeTransport(board([node]) + [failing, MILESTONES])
        result = run_sync(fake)
        self.assertEqual(result.unreadable, ["PVTI_item1"])
        self.assertTrue(any("unreadable" in line for line in result.reports), result.reports)

    def test_next_dry_run_no_mutation(self):
        nodes = [item(issue(1), status="Backlog", order=4),
                 item(issue(2, state="CLOSED", state_reason="COMPLETED"), status="Ready", order=1)]
        fake = FakeTransport([ENABLED] + board(nodes) + [fresh(nodes[0]), fresh(nodes[1]), MILESTONES])
        chosen, out = quiet(project_flow.next_task, fake, CFG, URL, "owner/repo", skip_unordered=False,
                            dry_run=True)
        self.assertEqual(fake.mutations, [])
        self.assertEqual(chosen.number, 1)
        self.assertIn("would write: owner/repo#1 Status: Backlog -> Ready", out)
        self.assertIn("would write: owner/repo#2 Status: Ready -> Done", out)
        self.assertNotIn("\nwrite: ", "\n" + out)

    def test_start_dry_run_no_mutation(self):
        node = item(issue(1), status="Ready", order=1)
        fake = FakeTransport([ENABLED] + board([node]) + [fresh(node), user("me")])
        _, out = start(fake, dry_run=True)
        self.assertEqual(fake.mutations, [])
        self.assertIn("would write: owner/repo#1 assignees: +me", out)
        self.assertIn("would write: owner/repo#1 Status: Ready -> In Progress", out)
        self.assertNotIn("\nwrite: ", "\n" + out)
        self.assertEqual(fake.responses, [])

    def test_followup_dry_run_lists_six_steps_no_mutation(self):
        nodes = [item(issue(15, milestone="M3"), status="In Progress", order=15),
                 item(issue(16), status="Backlog", order=16),
                 item(issue(20), status="Ready", order=3)]
        no_subs = ("query IssueSubIssues", {"data": {"repository": {"issue": {"subIssues": {
            "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}})
        fake = FakeTransport([ENABLED] + board(nodes) + [
            no_subs, ("milestones?state=all", [{"number": 3, "title": "M3", "state": "open",
                                                "open_issues": 1, "closed_issues": 0}])])
        body = os.path.join(os.path.dirname(SCRIPTS), ".github", "project-flow.json")
        number, out = quiet(project_flow.followup, fake, CFG, URL, "owner/repo",
                            parent=("owner/repo", 15), title="Follow-up", body_file=body,
                            blockers=[("owner/repo", 20)], pr="https://github.com/owner/repo/pull/7",
                            change_dir="openspec/changes/x", found_at="here", resume=None,
                            dry_run=True)
        self.assertEqual(fake.mutations, [])
        self.assertEqual(fake.responses, [])
        would = [line for line in out.splitlines() if line.startswith("would write: ")]
        self.assertEqual(len(would), 6, out)
        for text in ("create issue", "sub-issue of owner/repo#15", "milestone: M3",
                     "blocked by owner/repo#20", "project", "Implementation order: 15.1"):
            self.assertTrue(any(text in line for line in would), (text, would))
        self.assertNotIn("\nwrite: ", "\n" + out)
        self.assertNotIn("created:", out)


if __name__ == "__main__":
    unittest.main()
