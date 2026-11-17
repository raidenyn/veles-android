"""Tests for project_flow followup (six resumable steps) and allocate_order (design D7), on
FakeTransport boards. No network, no sleep, no wall clock."""
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
from project_flow import Config, Facts, GhError, Stop  # noqa: E402
from project_flow_fake import (FakeTransport, board, issue, item, item_response, milestone,  # noqa: E402
                               status_written)

URL = "https://github.com/users/owner/projects/2"
OTHER = "https://github.com/users/owner/projects/3"
REPO = "owner/repo"
VARIABLE = "repos/owner/repo/actions/variables/PROJECT_URL"
CFG = Config("Status", {"backlog": "Backlog", "ready": "Ready", "in_progress": "In Progress",
                        "in_review": "In Review", "blocked": "Blocked", "done": "Done"},
             "Implementation order", "blocked")
NOT_FOUND = GhError("gh: Not Found (HTTP 404)")
ENABLED = (VARIABLE, {"name": "PROJECT_URL", "value": URL})
PR_URL = "https://github.com/owner/repo/pull/77"
CHANGE_DIR = "openspec/changes/some-change"
FOUND_AT = "scripts/project_flow.py:123 (review round 2)"
NEW = 42
STEPS = ["create_issue", "add_sub_issue", "set_milestone", "add_blocked_by", "add_project_item",
         "set_order"]


def facts(number, order, repo=REPO):
    return Facts(item_id="PVTI_item%d" % number, content_id="I_issue%d" % number, repo=repo,
                 number=number, state="OPEN", labels=frozenset(), assignees=frozenset(),
                 closing_prs=(), blockers=(), order=order, status="Backlog", parent=None,
                 sub_issues=(), milestone=None, database_id=1000 + number)


def conn(nodes):
    return {"nodes": list(nodes), "pageInfo": {"hasNextPage": False, "endCursor": None}}


def subs(*numbers):
    """One SUB_ISSUES_QUERY response."""
    return ("query IssueSubIssues", {"data": {"repository": {"issue": {"subIssues": conn(
        {"number": n, "repository": {"nameWithOwner": REPO}} for n in numbers)}}}})


def state(number=NEW, parent=None, milestone_title=None, blockers=(), project_items=()):
    """One FOLLOWUP_ISSUE_QUERY response: the issue's links and its project items."""
    node = issue(number, parent=parent, milestone=milestone_title,
                 blockers=[(b, "OPEN", None) for b in blockers])
    node["projectItems"] = conn({"id": item_id, "project": {"id": project_id}}
                                for project_id, item_id in project_items)
    return ("query FollowupIssue", {"data": {"repository": {"issue": node}}})


def is_rest(method, suffix):
    return lambda c: c["kind"] == "rest" and c["method"] == method and c["path"].endswith(suffix)


CREATED = (is_rest("POST", "repos/owner/repo/issues"),
           {"number": NEW, "id": 9000 + NEW, "node_id": "I_new%d" % NEW})
SUB_ADDED = (is_rest("POST", "/sub_issues"), {"number": 15, "id": 1015})
MILESTONE_SET = (is_rest("PATCH", "/issues/%d" % NEW), {"number": NEW})
BLOCKED = (is_rest("POST", "/dependencies/blocked_by"), {"number": NEW})
ITEM_ADDED = ("mutation AddProjectItem",
              {"data": {"addProjectV2ItemById": {"item": {"id": "PVTI_new%d" % NEW}}}})
ORDER_SET = ("mutation SetOrder", status_written("PVTI_new%d" % NEW))
MILESTONES = ("milestones?state=all", [milestone(3, "M3")])


def parent_board(extra=(), parent_milestone="M3", parent_order=15):
    """The parent #15 (milestone M3), blocker #20 and the next unrelated item #16."""
    nodes = [item(issue(15, milestone=parent_milestone), status="In Progress", order=parent_order),
             item(issue(16), status="Backlog", order=16),
             item(issue(20), status="Ready", order=3)] + list(extra)
    return board(nodes)


def kind(m):
    if m["kind"] == "graphql":
        return "add_project_item" if "addProjectV2ItemById" in m["query"] else "set_order"
    if m["method"] == "PATCH":
        return "set_milestone"
    if m["path"].endswith("/sub_issues"):
        return "add_sub_issue"
    if m["path"].endswith("/dependencies/blocked_by"):
        return "add_blocked_by"
    return "create_issue"


def kinds(fake):
    return [kind(m) for m in fake.mutations]


def concurrent(fake, trigger, links):
    """When `fake` first sees a call matching `trigger`, a concurrent writer has changed the issue:
    the next FollowupIssue read serves `links` (put ahead of the queued reads)."""
    fired = []

    def hook(call):
        if not fired and trigger in (call.get("query") or call.get("path") or ""):
            fired.append(True)
            fake.responses.insert(0, links)
    fake.on_call(hook)


class BodyFile:
    def __enter__(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        handle.write("The follow-up work, described.\n")
        handle.close()
        self.path = handle.name
        return self.path

    def __exit__(self, *exc):
        os.unlink(self.path)


def run_followup(fake, *, parent=(REPO, 15), blockers=((REPO, 20),), resume=None, dry_run=False):
    with BodyFile() as path, contextlib.redirect_stdout(io.StringIO()) as out:
        try:
            result = project_flow.followup(
                fake, CFG, URL, REPO, parent=parent, title="Follow-up", body_file=path,
                blockers=list(blockers), pr=PR_URL, change_dir=CHANGE_DIR, found_at=FOUND_AT,
                resume=resume, dry_run=dry_run)
        except Stop as stop:
            return stop, out.getvalue()
    return result, out.getvalue()


class AllocateOrderTest(unittest.TestCase):
    def test_first_and_second(self):
        parent, unrelated = facts(15, 15), facts(16, 16)
        self.assertEqual(project_flow.allocate_order(parent, [parent, unrelated], set()), 15.1)
        first = facts(30, 15.1)
        self.assertEqual(project_flow.allocate_order(parent, [parent, unrelated, first], {(REPO, 30)}),
                         15.2)

    def test_nested(self):
        root, child, sibling = facts(15, 15), facts(30, 15.1), facts(31, 15.2)
        self.assertEqual(project_flow.allocate_order(child, [root, child, sibling, facts(16, 16)], set()),
                         15.11)

    def test_no_successor_uses_p_plus_one(self):
        parent = facts(15, 15)
        self.assertEqual(project_flow.allocate_order(parent, [facts(1, 3), parent], set()), 15.1)
        # A descendant above p never bounds n; with no other item n = p + 1 = 16, v = 15.9.
        self.assertEqual(project_flow.allocate_order(parent, [parent, facts(30, 15.9)], {(REPO, 30)}),
                         15.91)
        # A non-descendant above p + 1 is not n either way: n is the lowest value above p.
        self.assertEqual(project_flow.allocate_order(parent, [parent, facts(16, 40)], set()), 15.1)

    def test_no_room(self):
        parent = facts(15, 15)
        with self.assertRaises(Stop) as ctx:
            project_flow.allocate_order(parent, [parent, facts(16, 15.001)], set())
        self.assertEqual(ctx.exception.reason, "no-order-room")

    def test_parent_unordered(self):
        parent = facts(15, None)
        with self.assertRaises(Stop) as ctx:
            project_flow.allocate_order(parent, [parent, facts(16, 16)], set())
        self.assertEqual(ctx.exception.reason, "parent-unordered")
        # followup stops before any write and before reading the descendants.
        fake = FakeTransport([ENABLED] + parent_board(parent_order=None))
        stop, _ = run_followup(fake)
        self.assertEqual(stop.reason, "parent-unordered")
        self.assertEqual(fake.mutations, [])
        self.assertEqual(fake.responses, [])

    def test_float_rounding(self):
        parent = facts(15, 15)
        value = project_flow.allocate_order(parent, [parent, facts(30, 15.1), facts(16, 16)],
                                            {(REPO, 30)})
        self.assertEqual(value, 15.2)
        self.assertEqual(repr(value), "15.2")
        # Float noise on stored values is compared after round(x, 3).
        value = project_flow.allocate_order(facts(15, 15.0000001), [facts(30, 15.1000000002),
                                                                  facts(16, 16)], {(REPO, 30)})
        self.assertEqual(repr(value), "15.2")

    def test_incomplete_descendant_read_stops(self):
        fake = FakeTransport([ENABLED] + parent_board() + [
            ("query IssueSubIssues", GhError("gh: HTTP 502"))])
        stop, _ = run_followup(fake)
        self.assertEqual(stop.reason, "incomplete-read")
        self.assertEqual(fake.mutations, [])

    def test_unreadable_board_item_stops(self):
        hidden = item(None, item_id="PVTI_hidden", type="REDACTED")
        fake = FakeTransport([ENABLED] + parent_board(extra=[hidden]))
        stop, _ = run_followup(fake)
        self.assertEqual(stop.reason, "incomplete-read")
        self.assertIn("PVTI_hidden", stop.detail)
        self.assertEqual(fake.mutations, [])


def fresh_responses():
    """A complete run: each link step re-reads the issue right before its write."""
    return ([ENABLED] + parent_board() + [
        subs(), MILESTONES, CREATED, SUB_ADDED,
        state(parent=15), MILESTONE_SET,                       # refresh before step 3
        state(parent=15, milestone_title="M3"), BLOCKED,       # refresh before step 4 (#20)
        state(parent=15, milestone_title="M3", blockers=[20]),  # project item lookup before step 5
        ITEM_ADDED, ORDER_SET])


class FollowupTest(unittest.TestCase):
    def test_six_steps_in_order(self):
        fake = FakeTransport(fresh_responses())
        number, out = run_followup(fake)
        self.assertEqual(number, NEW)
        self.assertEqual(kinds(fake), STEPS)
        self.assertEqual(fake.responses, [])
        sub = fake.mutations[1]
        self.assertEqual((sub["path"], sub["body"]), ("repos/owner/repo/issues/15/sub_issues",
                                                      {"sub_issue_id": 9000 + NEW}))
        self.assertEqual(fake.mutations[2]["body"], {"milestone": 3})
        blocked = fake.mutations[3]
        self.assertEqual((blocked["path"], blocked["body"]),
                         ("repos/owner/repo/issues/%d/dependencies/blocked_by" % NEW, {"issue_id": 1020}))
        self.assertEqual(fake.mutations[4]["variables"]["content"], "I_new%d" % NEW)
        self.assertEqual(fake.mutations[5]["variables"]["value"], 15.1)
        self.assertEqual(fake.mutations[5]["variables"]["item"], "PVTI_new%d" % NEW)
        # The number is printed as soon as the issue exists, before the other writes.
        self.assertIn("created: #%d" % NEW, out)
        self.assertLess(out.index("created: #%d" % NEW), out.index("sub-issue"))

    def test_project_item_added_by_auto_add_is_not_added_again(self):
        responses = fresh_responses()
        index = responses.index(ITEM_ADDED)
        responses[index - 1] = state(parent=15, milestone_title="M3", blockers=[20],
                                     project_items=[("PVT_project1", "PVTI_auto")])
        del responses[index]
        fake = FakeTransport(responses)
        number, out = run_followup(fake)
        self.assertEqual(number, NEW)
        self.assertEqual(kinds(fake), ["create_issue", "add_sub_issue", "set_milestone",
                                       "add_blocked_by", "set_order"])
        self.assertEqual(fake.mutations[-1]["variables"]["item"], "PVTI_auto")
        self.assertIn("already on the project", out)

    def test_body_links(self):
        fake = FakeTransport(fresh_responses())
        run_followup(fake)
        body = fake.mutations[0]["body"]["body"]
        self.assertEqual(fake.mutations[0]["body"]["title"], "Follow-up")
        self.assertIn("The follow-up work, described.", body)
        self.assertIn(PR_URL, body)
        self.assertIn(CHANGE_DIR, body)
        self.assertIn(FOUND_AT, body)

    def test_parent_as_blocker_refused(self):
        for blockers in ([(REPO, 15)], [(REPO, 20), ("Owner/Repo", 15)]):
            with self.subTest(blockers=blockers):
                fake = FakeTransport([ENABLED])
                stop, _ = run_followup(fake, blockers=blockers)
                self.assertEqual(stop.reason, "parent-as-blocker")
                self.assertEqual(fake.mutations, [])
        fake = FakeTransport([ENABLED])
        with BodyFile() as path, contextlib.redirect_stdout(io.StringIO()) as out:
            code = project_flow.main(["followup", "--project", URL, "--repo", REPO, "--parent", "#15",
                                      "--title", "T", "--body-file", path, "--blocker", "#20",
                                      "--blocker", "owner/repo#15", "--pr", PR_URL,
                                      "--change-dir", CHANGE_DIR, "--found-at", FOUND_AT], fake, env={})
        self.assertEqual(code, 3)
        self.assertIn("stop: parent-as-blocker", out.getvalue())
        self.assertEqual(fake.mutations, [])

    def test_failure_after_create_prints_number_and_missing(self):
        responses = fresh_responses()
        responses[responses.index(ITEM_ADDED)] = ("mutation AddProjectItem", GhError("gh: HTTP 502"))
        responses.remove(ORDER_SET)
        fake = FakeTransport(responses)
        stop, out = run_followup(fake)
        self.assertIsInstance(stop, Stop)
        self.assertIn("created: #%d" % NEW, out)
        self.assertIn("missing: add_project_item, set_order", out)
        self.assertEqual(kinds(fake), STEPS[:5])
        # Through main: exit 3, the number and the missing steps on stdout.
        responses = fresh_responses()
        responses[responses.index(ITEM_ADDED)] = ("mutation AddProjectItem", GhError("gh: HTTP 502"))
        fake = FakeTransport(responses)
        with BodyFile() as path, contextlib.redirect_stdout(io.StringIO()) as out:
            code = project_flow.main(["followup", "--project", URL, "--repo", REPO, "--parent", "#15",
                                      "--title", "Follow-up", "--body-file", path, "--blocker", "#20",
                                      "--pr", PR_URL, "--change-dir", CHANGE_DIR, "--found-at",
                                      FOUND_AT], fake, env={})
        self.assertEqual(code, 3)
        self.assertIn("created: #%d" % NEW, out.getvalue())
        self.assertIn("missing: add_project_item, set_order", out.getvalue())

    def test_failure_on_sub_issue_names_every_later_step(self):
        responses = fresh_responses()
        index = responses.index(SUB_ADDED)
        # The POST fails and the fresh read shows no parent: the failure stands.
        responses[index:index + 1] = [(is_rest("POST", "/sub_issues"), GhError("gh: HTTP 502")),
                                      state()]
        fake = FakeTransport(responses)
        stop, out = run_followup(fake)
        self.assertIsInstance(stop, Stop)
        self.assertIn("created: #%d" % NEW, out)
        self.assertIn("missing: add_sub_issue, set_milestone, add_blocked_by, add_project_item, set_order",
                      out)
        self.assertEqual(kinds(fake), ["create_issue", "add_sub_issue"])

    def test_resume_completes_only_missing(self):
        # Created, attached, milestoned and blocked by #20 only; #21 and the project steps are missing.
        nodes = [item(issue(21), status="Ready", order=4)]
        resumed = state(parent=15, milestone_title="M3", blockers=[20])
        fake = FakeTransport([ENABLED] + parent_board(extra=nodes) + [
            resumed, subs(NEW), subs(), resumed,  # the refresh right before the #21 link
            (is_rest("POST", "/dependencies/blocked_by"), {"number": NEW}),
            state(parent=15, milestone_title="M3", blockers=[20, 21]), ITEM_ADDED, ORDER_SET])
        number, out = run_followup(fake, blockers=[(REPO, 20), (REPO, 21)], resume=NEW)
        self.assertEqual(number, NEW)
        self.assertEqual(kinds(fake), ["add_blocked_by", "add_project_item", "set_order"])
        self.assertEqual(fake.mutations[0]["body"], {"issue_id": 1021})
        self.assertEqual(fake.mutations[2]["variables"]["value"], 15.1)
        self.assertEqual(fake.responses, [])
        self.assertNotIn("create_issue", kinds(fake))

        # Everything done: resume writes nothing.
        on_board = item(issue(NEW, parent=15, milestone="M3", blockers=[(20, "OPEN", None)]),
                        item_id="PVTI_new%d" % NEW, status="Backlog", order=15.1)
        fake = FakeTransport([ENABLED] + parent_board(extra=[on_board]) + [
            state(parent=15, milestone_title="M3", blockers=[20],
                  project_items=[("PVT_project1", "PVTI_new%d" % NEW)]),
            ("query ProjectItem(", item_response(on_board))])
        number, out = run_followup(fake, resume=NEW)
        self.assertEqual(number, NEW)
        self.assertEqual(fake.mutations, [])
        self.assertEqual(fake.responses, [])

    def test_malformed_create_response_still_reports_number(self):
        # The issue exists (a number came back) but the response lacks its ids: the number and
        # every later step are still reported.
        responses = fresh_responses()
        responses[responses.index(CREATED)] = (CREATED[0], {"number": NEW})
        fake = FakeTransport(responses)
        stop, out = run_followup(fake)
        self.assertEqual(stop.reason, "incomplete-read")
        self.assertIn("created: #%d" % NEW, out)
        self.assertIn("failed: create_issue", out)
        self.assertIn("missing: add_sub_issue, set_milestone, add_blocked_by, add_project_item, set_order",
                      out)
        self.assertEqual(kinds(fake), ["create_issue"])

    def test_failed_write_names_the_step(self):
        responses = fresh_responses()
        responses[responses.index(ITEM_ADDED)] = ("mutation AddProjectItem", GhError("gh: HTTP 502"))
        _, out = run_followup(FakeTransport(responses))
        self.assertIn("failed: add_project_item: ", out)
        self.assertIn("HTTP 502", out)
        self.assertLess(out.index("failed: "), out.index("missing: "))

    def test_resume_milestone_set_concurrently_is_not_overwritten(self):
        # The resume read shows no milestone; while the descendants are read a concurrent writer
        # sets it. The refresh before step 3 sees it: no PATCH.
        for title, reason in (("M3", None), ("Other", "resume-disagrees")):
            with self.subTest(title=title):
                responses = [ENABLED] + parent_board() + [
                    state(parent=15, blockers=[20]), subs(), MILESTONES,
                    state(parent=15, milestone_title="M3", blockers=[20]), ITEM_ADDED, ORDER_SET]
                fake = FakeTransport(responses)
                concurrent(fake, "query IssueSubIssues",
                           state(parent=15, milestone_title=title, blockers=[20]))
                result, out = run_followup(fake, resume=NEW)
                self.assertNotIn("set_milestone", kinds(fake))
                if reason is None:
                    self.assertEqual(result, NEW)
                    self.assertIn("skipped set_milestone: milestone already 'M3'", out)
                    self.assertEqual(kinds(fake), ["add_project_item", "set_order"])
                else:
                    self.assertEqual(result.reason, reason)
                    self.assertIn("Other", result.detail)
                    self.assertEqual(fake.mutations, [])
                    self.assertIn("missing: set_milestone, add_blocked_by", out)

    def test_resume_blocker_added_concurrently_no_duplicate_post(self):
        # #21 is missing at the resume read and linked by someone else before the write.
        nodes = [item(issue(21), status="Ready", order=4)]
        fake = FakeTransport([ENABLED] + parent_board(extra=nodes) + [
            state(parent=15, milestone_title="M3", blockers=[20]), subs(),
            state(parent=15, milestone_title="M3", blockers=[20, 21]), ITEM_ADDED, ORDER_SET])
        concurrent(fake, "query IssueSubIssues", state(parent=15, milestone_title="M3", blockers=[20, 21]))
        number, out = run_followup(fake, blockers=[(REPO, 20), (REPO, 21)], resume=NEW)
        self.assertEqual(number, NEW)
        self.assertEqual(kinds(fake), ["add_project_item", "set_order"])
        self.assertIn("owner/repo#21 already linked", out)
        self.assertEqual(fake.responses, [])

    def test_duplicate_link_error_reconciles_through_fresh_read(self):
        # The link landed between the refresh and the POST: GitHub answers 422; a fresh read shows
        # the link, so the step is satisfied and the run goes on.
        dup_blocker = GhError("gh: Validation failed: Target issue has already been taken (HTTP 422)")
        responses = fresh_responses()
        index = responses.index(BLOCKED)
        responses[index:index + 1] = [(is_rest("POST", "/dependencies/blocked_by"), dup_blocker),
                                      state(parent=15, milestone_title="M3", blockers=[20])]
        fake = FakeTransport(responses)
        number, out = run_followup(fake)
        self.assertEqual(number, NEW)
        self.assertEqual(kinds(fake), STEPS)  # one blocker POST, never a second
        self.assertIn("owner/repo#20 already linked", out)
        self.assertEqual(fake.responses, [])
        # The same for the sub-issue link.
        dup_sub = GhError("gh: Issue may not contain duplicate sub-issues (HTTP 422)")
        responses = fresh_responses()
        index = responses.index(SUB_ADDED)
        responses[index:index + 1] = [(is_rest("POST", "/sub_issues"), dup_sub), state(parent=15)]
        fake = FakeTransport(responses)
        number, out = run_followup(fake)
        self.assertEqual(number, NEW)
        self.assertEqual(kinds(fake), STEPS)
        self.assertIn("skipped add_sub_issue: already a sub-issue of owner/repo#15", out)
        # A 422 whose fresh read shows another parent is a disagreement, not success.
        responses = fresh_responses()
        index = responses.index(SUB_ADDED)
        responses[index:index + 1] = [(is_rest("POST", "/sub_issues"), dup_sub), state(parent=99)]
        stop, out = run_followup(FakeTransport(responses))
        self.assertEqual(stop.reason, "resume-disagrees")
        self.assertIn("missing: add_sub_issue", out)

    def test_resume_disagreeing_parent(self):
        fake = FakeTransport([ENABLED] + parent_board() + [state(parent=99)])
        stop, out = run_followup(fake, resume=NEW)
        self.assertEqual(stop.reason, "resume-disagrees")
        self.assertIn("#99", stop.detail)
        self.assertEqual(fake.mutations, [])
        for name, resumed in (("milestone", state(parent=15, milestone_title="Other")),
                              ("blockers", state(parent=15, milestone_title="M3", blockers=[20, 33]))):
            with self.subTest(name):
                fake = FakeTransport([ENABLED] + parent_board() + [resumed])
                stop, _ = run_followup(fake, resume=NEW)
                self.assertEqual(stop.reason, "resume-disagrees")
                self.assertEqual(fake.mutations, [])

    def test_direct_followup_refuses_when_not_enabled(self):
        for responses in ([(VARIABLE, NOT_FOUND)], [(VARIABLE, {"name": "PROJECT_URL", "value": OTHER})]):
            fake = FakeTransport(list(responses))
            stop, _ = run_followup(fake)
            self.assertEqual(stop.reason, "not-enabled")
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(fake.mutations, [])

    def test_followup_is_gated(self):
        self.assertIn("followup", project_flow.GATED_COMMANDS)

    def test_main_followup(self):
        fake = FakeTransport(fresh_responses())
        with BodyFile() as path, contextlib.redirect_stdout(io.StringIO()) as out:
            code = project_flow.main(["followup", "--project", URL, "--repo", REPO, "--parent",
                                      "owner/repo#15", "--title", "Follow-up", "--body-file", path,
                                      "--blocker", "owner/repo#20", "--pr", PR_URL, "--change-dir",
                                      CHANGE_DIR, "--found-at", FOUND_AT], fake, env={})
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("created: #%d" % NEW, out.getvalue())
        self.assertEqual(kinds(fake), STEPS)


if __name__ == "__main__":
    unittest.main()
