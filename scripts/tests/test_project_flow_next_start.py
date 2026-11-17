"""Tests for project_flow next (selection by order value) and start (claim by assignment), on
FakeTransport boards. No network, no sleep, no wall clock."""
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
from project_flow_fake import FakeTransport, board, issue, item, item_response, status_written  # noqa: E402

URL = "https://github.com/users/owner/projects/2"
OTHER = "https://github.com/users/owner/projects/3"
REPO = "owner/repo"
VARIABLE = "repos/owner/repo/actions/variables/PROJECT_URL"
CFG = Config("Status", {"backlog": "Backlog", "ready": "Ready", "in_progress": "In Progress",
                        "in_review": "In Review", "blocked": "Blocked", "done": "Done"},
             "Implementation order", "blocked")
MILESTONES = ("repos/owner/repo/milestones?state=open", [])
NOT_FOUND = GhError("gh: Not Found (HTTP 404)")
ENABLED = (VARIABLE, {"name": "PROJECT_URL", "value": URL})
USER = (lambda c: c["kind"] == "rest" and c["path"] == "user", {"login": "me"})
ASSIGNED = ("/assignees", {"number": 1})


def fresh(node):
    return ("query ProjectItem(", item_response(node))


def ready(number, order, **kw):
    return item(issue(number, **kw), status="Ready", order=order)


def quiet(fn, *args, **kwargs):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = fn(*args, **kwargs)
    return result, out.getvalue()


def run_next(nodes, *, skip_unordered=False, dry_run=False, extra=()):
    fake = FakeTransport([ENABLED] + board(nodes) + list(extra) + [MILESTONES])
    result, out = quiet(project_flow.next_task, fake, CFG, URL, REPO,
                        skip_unordered=skip_unordered, dry_run=dry_run)
    return result, fake, out


def stop_of(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            fn(*args, **kwargs)
        except Stop as stop:
            return stop
    raise AssertionError("no Stop raised")


def run_start(responses, *, force=False, dry_run=False, target=(REPO, 1)):
    fake = FakeTransport([ENABLED] + list(responses))
    _, out = quiet(project_flow.start, fake, CFG, URL, REPO, target, force=force, dry_run=dry_run)
    return fake, out


def kinds(fake):
    """The mutations as short labels, in order."""
    labels = []
    for m in fake.mutations:
        if m["kind"] == "rest":
            labels.append("%s %s %r" % (m["method"], m["path"], m["body"]))
        else:
            labels.append("status " + m["variables"]["option"])
    return labels


class NextTest(unittest.TestCase):
    def test_ordinary_pick_by_value(self):
        for nodes in ([ready(1, 22.5), ready(2, 16), ready(3, 17)],
                      [ready(2, 16), ready(3, 17), ready(1, 22.5)],
                      [ready(3, 17), ready(1, 22.5), ready(2, 16)]):
            with self.subTest(order=[n["order"]["number"] for n in nodes]):
                chosen, fake, out = run_next(nodes)
                self.assertEqual((chosen.repo, chosen.number), (REPO, 2))
                self.assertIn("next: owner/repo#2 order=16", out)
                self.assertEqual(fake.mutations, [])

    def test_assigned_ready_skipped(self):
        # Item 1 still says Ready but is assigned: never selected; its own sync write is listed.
        stale = ready(1, 1, assignees=["someone"])
        chosen, fake, out = run_next([stale, ready(2, 5)], dry_run=True, extra=[fresh(stale)])
        self.assertEqual(chosen.number, 2)
        self.assertEqual(fake.mutations, [])
        self.assertIn("would write: owner/repo#1 Status: Ready -> In Progress", out)

    def test_tie_names_both(self):
        fake = FakeTransport([ENABLED] + board([ready(1, 4), ready(2, 4), ready(3, 9)]) + [MILESTONES])
        stop = stop_of(project_flow.next_task, fake, CFG, URL, REPO, skip_unordered=False, dry_run=False)
        self.assertEqual(stop.reason, "tie")
        self.assertIn("owner/repo#1", stop.detail)
        self.assertIn("owner/repo#2", stop.detail)
        self.assertNotIn("#3", stop.detail)

    def test_float_noise_is_tie(self):
        fake = FakeTransport([ENABLED] + board([ready(1, 15.1), ready(2, 15.100000001)]) + [MILESTONES])
        stop = stop_of(project_flow.next_task, fake, CFG, URL, REPO, skip_unordered=False, dry_run=False)
        self.assertEqual(stop.reason, "tie")
        self.assertIn("#1", stop.detail)
        self.assertIn("#2", stop.detail)

    def test_nothing_ready(self):
        nodes = [item(issue(1, blockers=[(5, "OPEN", None)]), status="Backlog", order=1),
                 item(issue(2, assignees=["me"]), status="In Progress", order=2),
                 item(issue(3, state="CLOSED", state_reason="COMPLETED"), status="Done", order=3)]
        fake = FakeTransport([ENABLED] + board(nodes) + [MILESTONES])
        stop = stop_of(project_flow.next_task, fake, CFG, URL, REPO, skip_unordered=False, dry_run=False)
        self.assertEqual(stop.reason, "nothing-ready")
        self.assertEqual(fake.mutations, [])

    def test_missing_order_stops_and_lists(self):
        nodes = [ready(1, 3), ready(2, None), ready(4, None)]
        fake = FakeTransport([ENABLED] + board(nodes) + [MILESTONES])
        stop = stop_of(project_flow.next_task, fake, CFG, URL, REPO, skip_unordered=False, dry_run=False)
        self.assertEqual(stop.reason, "missing-order")
        self.assertIn("owner/repo#2", stop.detail)
        self.assertIn("owner/repo#4", stop.detail)
        self.assertNotIn("#1", stop.detail)

    def test_skip_unordered_selects_rest(self):
        chosen, _, out = run_next([ready(1, None), ready(2, 8), ready(3, 7)], skip_unordered=True)
        self.assertEqual(chosen.number, 3)
        self.assertIn("next: owner/repo#3 order=7", out)
        self.assertIn("owner/repo#1", out)  # the skipped unordered item is reported
        # It still stops on every other reason.
        fake = FakeTransport([ENABLED] + board([ready(1, None), ready(2, 8), ready(3, 8)]) + [MILESTONES])
        stop = stop_of(project_flow.next_task, fake, CFG, URL, REPO, skip_unordered=True, dry_run=False)
        self.assertEqual(stop.reason, "tie")
        fake = FakeTransport([ENABLED] + board([ready(1, None)]) + [MILESTONES])
        stop = stop_of(project_flow.next_task, fake, CFG, URL, REPO, skip_unordered=True, dry_run=False)
        self.assertEqual(stop.reason, "nothing-ready")

    def test_incomplete_read_stops(self):
        nodes = [ready(1, 1), item(None, item_id="PVTI_hidden", type="REDACTED")]
        fake = FakeTransport([ENABLED] + board(nodes) + [MILESTONES])
        stop = stop_of(project_flow.next_task, fake, CFG, URL, REPO, skip_unordered=True, dry_run=False)
        self.assertEqual(stop.reason, "incomplete-read")
        self.assertIn("PVTI_hidden", stop.detail)
        self.assertEqual(fake.mutations, [])

    def test_next_needs_order_field(self):
        fake = FakeTransport([ENABLED] + board([ready(1, 1)], order=False))
        stop = stop_of(project_flow.next_task, fake, CFG, URL, REPO, skip_unordered=False, dry_run=False)
        self.assertEqual(stop.reason, "missing-option")
        self.assertEqual(fake.mutations, [])

    def test_direct_next_refuses_when_not_enabled(self):
        for responses in ([(VARIABLE, NOT_FOUND)], [(VARIABLE, {"name": "PROJECT_URL", "value": OTHER})]):
            fake = FakeTransport(list(responses))
            stop = stop_of(project_flow.next_task, fake, CFG, URL, REPO, skip_unordered=False,
                           dry_run=False)
            self.assertEqual(stop.reason, "not-enabled")
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(fake.mutations, [])

    def test_main_next(self):
        fake = FakeTransport([ENABLED] + board([ready(1, 2), ready(2, 1)]) + [MILESTONES])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = project_flow.main(["next", "--project", URL, "--repo", REPO], fake, env={})
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("next: owner/repo#2 order=1", out.getvalue())
        fake = FakeTransport([ENABLED] + board([ready(1, None)]) + [MILESTONES])
        with contextlib.redirect_stdout(io.StringIO()):
            code = project_flow.main(["next", "--project", URL, "--repo", REPO], fake, env={})
        self.assertEqual(code, 3)


class StartTest(unittest.TestCase):
    def test_start_claims(self):
        node = ready(1, 3)
        claimed = item(issue(1, assignees=["me"]), status="In Progress", order=3)
        fake, out = run_start(board([node]) + [fresh(node), USER, ASSIGNED,
                                               ("mutation", status_written()), fresh(claimed)]
                              + board([claimed]))
        self.assertEqual(kinds(fake), [
            "POST repos/owner/repo/issues/1/assignees {'assignees': ['me']}",
            "status opt-In Progress"])
        self.assertIn("write: owner/repo#1 Status: Ready -> In Progress", out)
        self.assertEqual(fake.responses, [])

    def test_start_refuses_backlog_names_blockers(self):
        node = item(issue(1, blockers=[(5, "OPEN", None), (6, "CLOSED", "COMPLETED"),
                                       (7, "CLOSED", "NOT_PLANNED")]), status="Backlog", order=3)
        fake = FakeTransport([ENABLED] + board([node]) + [fresh(node)])
        stop = stop_of(project_flow.start, fake, CFG, URL, REPO, (REPO, 1), force=False, dry_run=False)
        self.assertEqual(stop.reason, "not-ready")
        self.assertIn("#5", stop.detail)
        self.assertIn("#7", stop.detail)
        self.assertNotIn("#6", stop.detail)
        self.assertEqual(fake.mutations, [])

    def test_start_force_allows(self):
        node = item(issue(1, blockers=[(5, "OPEN", None)]), status="Backlog", order=3)
        claimed = item(issue(1, blockers=[(5, "OPEN", None)], assignees=["me"]), status="In Progress",
                       order=3)
        fake, _ = run_start(board([node]) + [fresh(node), USER, ASSIGNED, ("mutation", status_written()),
                                              fresh(claimed)] + board([claimed]), force=True)
        self.assertEqual(kinds(fake), [
            "POST repos/owner/repo/issues/1/assignees {'assignees': ['me']}",
            "status opt-In Progress"])

    def test_start_collision(self):
        node = ready(1, 3)
        both = item(issue(1, assignees=["me", "other"]), status="In Progress", order=3)
        fake = FakeTransport([ENABLED] + board([node]) + [fresh(node), USER, ASSIGNED,
                                                          ("mutation", status_written()), fresh(both)])
        stop = stop_of(project_flow.start, fake, CFG, URL, REPO, (REPO, 1), force=False, dry_run=False)
        self.assertEqual(stop.reason, "collision")
        self.assertIn("other", stop.detail)
        self.assertEqual(len(fake.mutations), 2)  # no reconcile after the collision
        self.assertEqual(fake.responses, [])

    def test_start_refuses_assigned_in_review_blocked_done(self):
        cases = {
            "assigned": item(issue(1, assignees=["someone"]), status="In Progress", order=1),
            "in review": item(issue(1, prs=[{"number": 9, "state": "OPEN", "isDraft": False,
                                             "repository": {"nameWithOwner": REPO}, "milestone": None}]),
                              status="In Review", order=1),
            "blocked": item(issue(1, labels=["blocked"]), status="Blocked", order=1),
            "done": item(issue(1, state="CLOSED", state_reason="COMPLETED"), status="Done", order=1),
            "stale ready, assigned": item(issue(1, assignees=["someone"]), status="Ready", order=1),
        }
        for name, node in cases.items():
            with self.subTest(name):
                fake = FakeTransport([ENABLED] + board([node]) + [fresh(node)])
                stop = stop_of(project_flow.start, fake, CFG, URL, REPO, (REPO, 1), force=False,
                               dry_run=False)
                self.assertEqual(stop.reason, "not-ready")
                self.assertEqual(fake.mutations, [])

    def test_start_derives_afresh(self):
        # The board read says Ready, the fresh read shows a new open blocker: refused.
        node = ready(1, 3)
        blocked_now = item(issue(1, blockers=[(5, "OPEN", None)]), status="Ready", order=3)
        fake = FakeTransport([ENABLED] + board([node]) + [fresh(blocked_now)])
        stop = stop_of(project_flow.start, fake, CFG, URL, REPO, (REPO, 1), force=False, dry_run=False)
        self.assertEqual(stop.reason, "not-ready")
        self.assertIn("#5", stop.detail)

    def test_start_not_on_project(self):
        fake = FakeTransport([ENABLED] + board([ready(2, 1)]))
        stop = stop_of(project_flow.start, fake, CFG, URL, REPO, (REPO, 1), force=False, dry_run=False)
        self.assertEqual(stop.reason, "not-ready")
        self.assertEqual(fake.mutations, [])

    def test_direct_start_refuses_when_not_enabled(self):
        for responses in ([(VARIABLE, NOT_FOUND)], [(VARIABLE, {"name": "PROJECT_URL", "value": OTHER})]):
            fake = FakeTransport(list(responses))
            stop = stop_of(project_flow.start, fake, CFG, URL, REPO, (REPO, 1), force=True, dry_run=False)
            self.assertEqual(stop.reason, "not-enabled")
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(fake.mutations, [])

    def test_main_start(self):
        node = ready(1, 3)
        claimed = item(issue(1, assignees=["me"]), status="In Progress", order=3)
        for target in ("owner/repo#1", "#1"):
            with self.subTest(target=target):
                fake = FakeTransport([ENABLED] + board([node]) + [
                    fresh(node), USER, ASSIGNED, ("mutation", status_written()), fresh(claimed)]
                    + board([claimed]))
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = project_flow.main(["start", target, "--project", URL, "--repo", REPO],
                                             fake, env={})
                self.assertEqual(code, 0, out.getvalue())
                self.assertEqual(len(fake.mutations), 2)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(project_flow.main(["start", "nonsense", "--project", URL, "--repo", REPO],
                                               FakeTransport(), env={}), 2)


if __name__ == "__main__":
    unittest.main()
