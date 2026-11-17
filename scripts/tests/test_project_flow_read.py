"""Tests for project_flow.py complete reads, on recorded fixtures. No network."""
import dataclasses
import json
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_flow  # noqa: E402
from project_flow import Config, GhError, Stop  # noqa: E402
from project_flow_fake import FakeTransport, blocker, board, issue, item, pr  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "project_flow")
URL = "https://github.com/users/owner/projects/2"
CFG = Config("Status", {"backlog": "Backlog", "ready": "Ready", "in_progress": "In Progress",
                        "in_review": "In Review", "blocked": "Blocked", "done": "Done"},
             "Implementation order", "blocked")
SCOPE_ERROR = GhError("Your token has not been granted the required scopes; missing required scopes")


def fixture(name):
    with open(os.path.join(FIXTURES, name + ".json"), encoding="utf-8") as handle:
        return json.load(handle)


def recorded_board():
    return [("query ProjectFields", fixture("fields")),
            ("query ProjectWorkflows", fixture("workflows")),
            ("query ProjectItems", fixture("items_page1")),
            ("query ProjectItems", fixture("items_page2"))]


class ReadBoardTest(unittest.TestCase):
    def test_reads_all_pages(self):
        fake = FakeTransport(recorded_board())
        result = project_flow.read_board(fake, URL, CFG)
        # 5 issues on page 1, 2 issues and a draft card on page 2; the draft is ignored.
        self.assertEqual(len(result.items), 7)
        self.assertEqual([f.number for f in result.items], [1, 2, 3, 4, 5, 6, 8])
        self.assertEqual(result.unreadable, [])
        item_calls = [c for c in fake.calls if "query ProjectItems" in c["query"]]
        self.assertNotIn("after", item_calls[0]["variables"])
        self.assertEqual(item_calls[1]["variables"]["after"],
                         fixture("items_page1")["data"]["user"]["projectV2"]["items"]["pageInfo"]["endCursor"])
        for call in fake.calls:
            self.assertNotIn("first", call["variables"])  # production pages at the default 100
            self.assertIn("user(login: $login)", call["query"])

    def test_board_fields_and_workflows(self):
        result = project_flow.read_board(FakeTransport(recorded_board()), URL, CFG)
        self.assertEqual(result.project_id, "PVT_project1")
        self.assertEqual(result.status_field_id, "PVTSSF_field1")
        self.assertEqual(result.order_field_id, "PVTF_field1")
        self.assertEqual(result.options["In Review"], "opt4")
        self.assertEqual(len(result.options), 6)
        self.assertIs(result.workflows["Auto-add sub-issues to project"], True)
        self.assertIs(result.workflows["Item closed"], False)
        by_number = {f.number: f for f in result.items}
        self.assertEqual(by_number[6].order, 20.5)
        self.assertEqual(by_number[6].status, "Backlog")
        self.assertIsNone(by_number[4].status)
        self.assertIsNone(by_number[4].order)
        self.assertEqual(by_number[5].sub_issues, (("owner/repo", 1), ("owner/repo", 6)))
        self.assertEqual(by_number[6].sub_issues, (("owner/repo", 7),))
        self.assertEqual(by_number[8].item_id, "PVTI_item7")

    def test_failing_page_is_incomplete(self):
        responses = recorded_board()
        responses[3] = ("query ProjectItems", GhError("HTTP 502"))
        with self.assertRaises(Stop) as caught:
            project_flow.read_board(FakeTransport(responses), URL, CFG)
        self.assertEqual(caught.exception.reason, "incomplete-read")

    def test_missing_page_data_is_incomplete(self):
        responses = recorded_board()
        responses[3] = ("query ProjectItems", {"data": {"user": {"projectV2": None}}})
        with self.assertRaises(Stop) as caught:
            project_flow.read_board(FakeTransport(responses), URL, CFG)
        self.assertEqual(caught.exception.reason, "incomplete-read")

    def test_scope_error_is_not_masked(self):
        responses = recorded_board()
        responses[3] = ("query ProjectItems", SCOPE_ERROR)
        with self.assertRaises(GhError):
            project_flow.read_board(FakeTransport(responses), URL, CFG)

    def test_partial_blocker_read_marks_item_unreadable(self):
        partial = issue(1, blockers=[(2, "CLOSED", "COMPLETED")], more=("blockedBy",))
        items = [item(partial), item(issue(2)), item(issue(3))]
        fake = FakeTransport(board(items) + [("IssueConnection_blockedBy", GhError("HTTP 502"))])
        result = project_flow.read_board(fake, URL, CFG)
        self.assertIn("PVTI_item1", result.unreadable)
        self.assertIn("PVTI_item1", result.reasons)
        self.assertEqual([f.item_id for f in result.items], ["PVTI_item2", "PVTI_item3"])

    def test_nested_follow_up_pages_are_merged(self):
        main = fixture("issue_facts")["data"]["node"]["content"]
        main["blockedBy"] = fixture("blockers_page1")["data"]["node"]["blockedBy"]
        node = item(main, item_id="PVTI_item1")
        fake = FakeTransport(board([node]) + [("IssueConnection_blockedBy", fixture("blockers_page2"))])
        result = project_flow.read_board(fake, URL, CFG)
        reasons = [b.state_reason for b in result.items[0].blockers]
        self.assertEqual(reasons, ["DUPLICATE", "NOT_PLANNED", "COMPLETED"])
        follow = [c for c in fake.calls if "IssueConnection_blockedBy" in c["query"]][0]
        self.assertEqual(follow["variables"], {"id": "I_issue1", "after": "cursor1"})

    def test_redacted_item_is_unreadable(self):
        items = [item(None, item_id="PVTI_hidden", type="REDACTED"), item(issue(2)),
                 item(None, type="PULL_REQUEST", item_id="PVTI_pr")]
        result = project_flow.read_board(FakeTransport(board(items)), URL, CFG)
        self.assertEqual(result.unreadable, ["PVTI_hidden"])
        self.assertEqual([f.number for f in result.items], [2])

    def test_org_fallback(self):
        responses = [("query ProjectFields", GhError("Could not resolve to a User with the login of 'o'."))]
        responses += board([item(issue(1))], owner_key="organization")
        fake = FakeTransport(responses)
        result = project_flow.read_board(fake, "https://github.com/orgs/o/projects/3", CFG)
        self.assertEqual([f.number for f in result.items], [1])
        self.assertIn("user(login: $login)", fake.calls[0]["query"])
        for call in fake.calls[1:]:
            self.assertIn("organization(login: $login)", call["query"])

    def test_missing_status_field_stops(self):
        responses = board([])
        responses[0] = ("query ProjectFields",
                        {"data": {"user": {"projectV2": {"id": "PVT_project1", "status": None, "order": None}}}})
        with self.assertRaises(Stop) as caught:
            project_flow.read_board(FakeTransport(responses), URL, CFG)
        self.assertEqual(caught.exception.reason, "missing-option")

    def test_missing_order_field_is_none(self):
        result = project_flow.read_board(FakeTransport(board([], order=False)), URL, CFG)
        self.assertIsNone(result.order_field_id)


class FactsTest(unittest.TestCase):
    def facts(self):
        fake = FakeTransport([("query ProjectItem(", fixture("issue_facts"))])
        return project_flow.read_item(fake, "PVTI_item1", CFG)

    def test_facts_parse_fixture(self):
        facts = self.facts()
        self.assertEqual([b.state_reason for b in facts.blockers][::-1],
                         ["COMPLETED", "NOT_PLANNED", "DUPLICATE"])
        self.assertEqual(sorted(b.state_reason for b in facts.blockers),
                         sorted(["COMPLETED", "NOT_PLANNED", "DUPLICATE"]))
        self.assertEqual(facts.labels, frozenset({"bug", "feature-x"}))
        self.assertEqual(facts.assignees, frozenset({"owner"}))
        self.assertEqual(facts.item_id, "PVTI_item1")
        self.assertEqual(facts.content_id, "I_issue1")
        self.assertEqual((facts.repo, facts.number, facts.state), ("owner/repo", 1, "OPEN"))
        self.assertEqual(facts.database_id, 1001)
        self.assertEqual(facts.parent, ("owner/repo", 5))
        self.assertEqual(facts.milestone, "M1 scratch")
        self.assertEqual(facts.status, "In Review")
        self.assertEqual(facts.order, 10.0)
        self.assertEqual(facts.closing_prs, (
            project_flow.PullRef("owner/repo", 9, "OPEN", True, None),
            project_flow.PullRef("owner/repo", 10, "OPEN", False, None)))
        self.assertEqual(facts.blockers[0], project_flow.Blocker("owner/repo", 4, "CLOSED", "DUPLICATE"))

    def test_inputs_cover_the_derivation_facts(self):
        facts = self.facts()
        inputs = facts.inputs()
        self.assertEqual(inputs, self.facts().inputs())
        changed = dataclasses.replace(
            facts, closing_prs=(project_flow.PullRef("owner/repo", 9, "OPEN", False, None),))
        self.assertNotEqual(changed.inputs(), inputs)
        reordered = dataclasses.replace(facts, blockers=facts.blockers[::-1])
        self.assertEqual(reordered.inputs(), inputs)
        moved = dataclasses.replace(facts, status="Ready", order=99.0)
        self.assertEqual(moved.inputs(), inputs)

    def test_read_item_failure_is_incomplete(self):
        with self.assertRaises(Stop) as caught:
            project_flow.read_item(FakeTransport([("query ProjectItem(", GhError("HTTP 502"))]),
                                   "PVTI_item1", CFG)
        self.assertEqual(caught.exception, Stop("incomplete-read", caught.exception.detail))

    def test_read_item_partial_follow_up_is_incomplete(self):
        node = item(issue(1, labels=["a"], more=("labels",)))
        fake = FakeTransport([("query ProjectItem(", {"data": {"node": node}}),
                              ("IssueConnection_labels", GhError("HTTP 502"))])
        with self.assertRaises(Stop) as caught:
            project_flow.read_item(fake, "PVTI_item1", CFG)
        self.assertEqual(caught.exception.reason, "incomplete-read")

    def test_read_item_follows_pages(self):
        node = item(issue(1, prs=[pr(3, draft=True)], more=("closedByPullRequestsReferences",)))
        page2 = {"data": {"node": {"closedByPullRequestsReferences": {
            "nodes": [pr(4, state="MERGED")], "pageInfo": {"hasNextPage": False, "endCursor": "x"}}}}}
        fake = FakeTransport([("query ProjectItem(", {"data": {"node": node}}),
                              ("IssueConnection_closedByPullRequestsReferences", page2)])
        facts = project_flow.read_item(fake, "PVTI_item1", CFG)
        self.assertEqual([(p.number, p.state, p.is_draft) for p in facts.closing_prs],
                         [(3, "OPEN", True), (4, "MERGED", False)])
        self.assertIn("includeClosedPrs: true", fake.calls[1]["query"])

    def test_read_item_missing_node_is_incomplete(self):
        with self.assertRaises(Stop) as caught:
            project_flow.read_item(FakeTransport([("query ProjectItem(", {"data": {"node": None}})]),
                                   "PVTI_gone", CFG)
        self.assertEqual(caught.exception.reason, "incomplete-read")

    def test_blocker_builder_open(self):
        node = item(issue(1, blockers=[blocker(2, state="OPEN", reason=None)]))
        facts = project_flow.read_item(FakeTransport([("query ProjectItem(", {"data": {"node": node}})]),
                                       "PVTI_item1", CFG)
        self.assertEqual(facts.blockers, (project_flow.Blocker("owner/repo", 2, "OPEN", None),))


class ParseProjectUrlTest(unittest.TestCase):
    def test_parse_project_url_strips_view(self):
        self.assertEqual(project_flow.parse_project_url("https://github.com/users/o/projects/3/views/2"),
                         ("o", 3))

    def test_parse_project_url_orgs(self):
        self.assertEqual(project_flow.parse_project_url("https://github.com/orgs/acme/projects/12/"),
                         ("acme", 12))

    def test_parse_project_url_rejects_other_urls(self):
        for url in ("https://github.com/acme/repo/issues/3", "https://github.com/users/o/projects/x",
                    "users/o/projects/3", "https://example.com/users/o/projects/3"):
            with self.assertRaises(Stop, msg=url) as caught:
                project_flow.parse_project_url(url)
            self.assertEqual(caught.exception.reason, "config-invalid")


class ReadTextTest(unittest.TestCase):
    def test_read_text_paginates_comments(self):
        fake = FakeTransport([("query IssueText", fixture("text_page1")),
                              ("query IssueText", fixture("text_page2"))])
        text = project_flow.read_text(fake, "owner/repo", 1)
        for part in ("pf main item", "First comment", "Second comment", "Third comment"):
            self.assertIn(part, text)
        self.assertLess(text.index("First"), text.index("Third"))
        self.assertEqual(fake.calls[0]["variables"], {"owner": "owner", "name": "repo", "number": 1})
        self.assertEqual(fake.calls[1]["variables"]["after"], "cursor6")

    def test_read_text_failing_page_incomplete(self):
        fake = FakeTransport([("query IssueText", fixture("text_page1")),
                              ("query IssueText", GhError("HTTP 502"))])
        with self.assertRaises(Stop) as caught:
            project_flow.read_text(fake, "owner/repo", 1)
        self.assertEqual(caught.exception.reason, "incomplete-read")


class ReadPrClosingIssuesTest(unittest.TestCase):
    def test_read_pr_closing_issues_paginates_and_fails_closed(self):
        fake = FakeTransport([("query PullClosingIssues", fixture("closing_issues_page1")),
                              ("query PullClosingIssues", fixture("closing_issues_page2"))])
        self.assertEqual(project_flow.read_pr_closing_issues(fake, "owner/repo", 10),
                         [("owner/repo", 1, "M1 scratch"), ("owner/repo", 5, "M1 scratch")])
        self.assertEqual(fake.calls[1]["variables"]["after"], "cursor3")
        failing = FakeTransport([("query PullClosingIssues", fixture("closing_issues_page1")),
                                 ("query PullClosingIssues", GhError("HTTP 502"))])
        with self.assertRaises(Stop) as caught:
            project_flow.read_pr_closing_issues(failing, "owner/repo", 10)
        self.assertEqual(caught.exception.reason, "incomplete-read")


def sub_issue_responses():
    def for_number(number):
        return lambda call: ("query IssueSubIssues" in call["query"]
                             and call["variables"]["number"] == number)
    return [(for_number(n), fixture("sub_issues_%d" % n)) for n in (5, 1, 6, 7)]


class ReadDescendantsTest(unittest.TestCase):
    def test_read_descendants_two_depths_off_project(self):
        result = project_flow.read_descendants(FakeTransport(sub_issue_responses()), "owner/repo", 5)
        self.assertEqual(result, {("owner/repo", 1), ("owner/repo", 6), ("owner/repo", 7)})

    def test_read_descendants_failing_page_incomplete(self):
        responses = sub_issue_responses()
        responses[2] = (responses[2][0], GhError("HTTP 502"))
        with self.assertRaises(Stop) as caught:
            project_flow.read_descendants(FakeTransport(responses), "owner/repo", 5)
        self.assertEqual(caught.exception.reason, "incomplete-read")

    def test_read_descendants_follows_pages(self):
        page1 = {"data": {"repository": {"issue": {"subIssues": {
            "nodes": [{"number": 2, "repository": {"nameWithOwner": "owner/repo"}}],
            "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}}}}}
        page2 = {"data": {"repository": {"issue": {"subIssues": {
            "nodes": [{"number": 3, "repository": {"nameWithOwner": "other/repo"}}],
            "pageInfo": {"hasNextPage": False, "endCursor": "c2"}}}}}}
        empty = {"data": {"repository": {"issue": {"subIssues": {
            "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}}
        fake = FakeTransport([(None, page1), (None, page2), (None, empty), (None, empty)])
        self.assertEqual(project_flow.read_descendants(fake, "owner/repo", 1),
                         {("owner/repo", 2), ("other/repo", 3)})
        self.assertEqual(fake.calls[1]["variables"]["after"], "c1")
        self.assertEqual({(c["variables"]["owner"], c["variables"]["name"]) for c in fake.calls[2:]},
                         {("owner", "repo"), ("other", "repo")})


if __name__ == "__main__":
    unittest.main()
