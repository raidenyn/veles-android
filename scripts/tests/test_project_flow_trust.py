"""Closing pull requests count only when trusted: a same-repository branch (the head repository is the
issue's repository) or an author associated as OWNER, MEMBER or COLLABORATOR. An untrusted PR (an
outsider's fork) makes no issue In Review and gets no milestone; it is reported. No network, no clock."""
import contextlib
import io
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_flow  # noqa: E402
from project_flow import Config, Facts, PullRef, Stop  # noqa: E402
from project_flow_fake import (FakeTransport, board, closing_issues, issue, item,  # noqa: E402
                               item_response, milestone, pr, status_written)

URL = "https://github.com/users/owner/projects/2"
REPO = "owner/repo"
FORK = "stranger/repo"
CFG = Config("Status", {"backlog": "Backlog", "ready": "Ready", "in_progress": "In Progress",
                        "in_review": "In Review", "blocked": "Blocked", "done": "Done"},
             "Implementation order", "blocked")
ENABLED = ("repos/owner/repo/actions/variables/PROJECT_URL", {"name": "PROJECT_URL", "value": URL})
OPEN_MILESTONES = ("repos/owner/repo/milestones?state=open", [])


def facts(*prs, assignees=()):
    return Facts(item_id="PVTI_item1", content_id="I_issue1", repo=REPO, number=1, state="OPEN",
                 labels=frozenset(), assignees=frozenset(assignees), closing_prs=tuple(prs),
                 blockers=(), order=1.0, status=None, parent=None, sub_issues=(), milestone=None,
                 database_id=1001)


def pull(head_repo, association, repo=REPO, number=9):
    return PullRef(repo, number, "OPEN", False, None, head_repo, association)


class DeriveTrustTest(unittest.TestCase):
    def role(self, *prs, **kw):
        return project_flow.derive(facts(*prs, **kw), CFG)[0]

    def test_outsider_fork_pr_is_ignored(self):
        for association in ("NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER",
                            "MANNEQUIN", "SOMETHING_NEW"):
            with self.subTest(association=association):
                self.assertEqual(self.role(pull(FORK, association)), "ready")
                self.assertEqual(self.role(pull(FORK, association), assignees=["me"]), "in_progress")

    def test_same_repository_branch_counts(self):
        self.assertEqual(self.role(pull(REPO, "NONE")), "in_review")
        self.assertEqual(self.role(pull("Owner/Repo", "CONTRIBUTOR")), "in_review")

    def test_associated_author_fork_counts(self):
        for association in ("OWNER", "MEMBER", "COLLABORATOR"):
            with self.subTest(association=association):
                self.assertEqual(self.role(pull("someone/fork", association)), "in_review")

    def test_null_head_repository_counts_only_through_the_association(self):
        self.assertEqual(self.role(pull(None, "MEMBER")), "in_review")
        self.assertEqual(self.role(pull(None, "NONE")), "ready")

    def test_cross_repository_pr_counts_through_the_association(self):
        # A PR in another repository closing this issue: its branch is not in the issue's repository.
        self.assertEqual(self.role(pull("other/repo", "NONE", repo="other/repo")), "ready")
        self.assertEqual(self.role(pull("other/repo", "MEMBER", repo="other/repo")), "in_review")

    def test_inputs_change_with_the_association_and_head(self):
        base = facts(pull(FORK, "NONE"))
        self.assertNotEqual(facts(pull(FORK, "COLLABORATOR")).inputs(), base.inputs())
        self.assertNotEqual(facts(pull(REPO, "NONE")).inputs(), base.inputs())
        self.assertNotEqual(facts(pull(None, "NONE")).inputs(), base.inputs())
        self.assertEqual(facts(pull(FORK, "NONE")).inputs(), base.inputs())


class ReaderTrustTest(unittest.TestCase):
    def read(self, node):
        fake = FakeTransport([("query ProjectItem(", item_response(node))])
        return project_flow.read_item(fake, node["id"], CFG)

    def assert_incomplete(self, node):
        with self.assertRaises(Stop) as caught:
            self.read(node)
        self.assertEqual(caught.exception.reason, "incomplete-read")

    def test_fields_are_selected(self):
        for query in (project_flow.ITEMS_QUERY, project_flow.ITEM_QUERY,
                      project_flow._CONNECTION_NODES["closedByPullRequestsReferences"]):
            self.assertIn("headRepository { nameWithOwner }", query)
            self.assertIn("authorAssociation", query)

    def test_fields_are_read(self):
        node = item(issue(1, prs=[pr(9, head_repo=FORK, association="NONE"),
                                  pr(10, head_repo=None, association="MEMBER")]))
        got = self.read(node).closing_prs
        self.assertEqual([(p.head_repo, p.author_association) for p in got],
                         [(FORK, "NONE"), (None, "MEMBER")])

    def test_unknown_association_is_read_and_untrusted(self):
        node = item(issue(1, prs=[pr(9, head_repo=FORK, association="SPONSOR")]))
        got = self.read(node)
        self.assertEqual(got.closing_prs[0].author_association, "SPONSOR")
        self.assertEqual(project_flow.derive(got, CFG)[0], "ready")

    def test_malformed_fields_are_incomplete(self):
        for label, change in (
                ("association missing", lambda n: n.pop("authorAssociation")),
                ("association not a string", lambda n: n.update(authorAssociation=3)),
                ("association null", lambda n: n.update(authorAssociation=None)),
                ("head missing", lambda n: n.pop("headRepository")),
                ("head not an object", lambda n: n.update(headRepository=FORK)),
                ("head without a name", lambda n: n.update(headRepository={}))):
            with self.subTest(label):
                node = item(issue(1, prs=[pr(9)]))
                change(node["content"]["closedByPullRequestsReferences"]["nodes"][0])
                self.assert_incomplete(node)


def run_cli(fake, *argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = project_flow.main(list(argv) + ["--project", URL, "--repo", REPO, "--config",
                                               os.path.join(os.path.dirname(SCRIPTS), ".github",
                                                            "project-flow.json")], fake, env={})
    return code, out.getvalue()


class ReconcileTrustTest(unittest.TestCase):
    def test_outsider_fork_pr_leaves_the_item_ready_and_next_selects_it(self):
        node = item(issue(1, prs=[pr(9, head_repo=FORK, association="NONE")]), status="Ready", order=1)
        fake = FakeTransport([ENABLED] + board([node, item(issue(2), status="Ready", order=2)])
                             + [OPEN_MILESTONES])
        code, out = run_cli(fake, "next")
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.mutations, [])
        self.assertIn("owner/repo#1: ignored untrusted PR owner/repo#9\n", out)
        self.assertEqual(out.splitlines()[-1], "next: owner/repo#1 order=1")

    def test_trusted_pr_moves_the_item_to_in_review(self):
        for label, kw in (("same repository", {"head_repo": REPO, "association": "CONTRIBUTOR"}),
                          ("collaborator fork", {"head_repo": FORK, "association": "COLLABORATOR"}),
                          ("member, null head", {"head_repo": None, "association": "MEMBER"})):
            with self.subTest(label):
                node = item(issue(1, prs=[pr(9, **kw)]), status="Ready", order=1)
                fake = FakeTransport([ENABLED] + board([node]) + [
                    ("query ProjectItem(", item_response(node)), ("mutation", status_written()),
                    ("closingIssuesReferences", closing_issues([(1, None)])), OPEN_MILESTONES])
                code, out = run_cli(fake, "sync")
                self.assertEqual(code, 0, out)
                self.assertIn("write: owner/repo#1 Status: Ready -> In Review", out)
                self.assertNotIn("untrusted", out)

    def test_untrusted_null_head_pr_is_ignored(self):
        node = item(issue(1, prs=[pr(9, head_repo=None, association="NONE")]), status="Ready", order=1)
        fake = FakeTransport([ENABLED] + board([node]) + [OPEN_MILESTONES])
        code, out = run_cli(fake, "sync")
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.mutations, [])
        self.assertIn("owner/repo#1: ignored untrusted PR owner/repo#9\n", out)

    def test_milestone_copy_skips_untrusted_prs(self):
        # Issue 1 is closed with milestone M1; the outsider's merged fork PR gets nothing, the
        # member's merged PR gets M1.
        node = item(issue(1, state="CLOSED", state_reason="COMPLETED", milestone="M1",
                          prs=[pr(9, state="MERGED", head_repo=FORK, association="NONE"),
                               pr(10, state="MERGED", head_repo=FORK, association="MEMBER")]),
                    status="Done")
        fake = FakeTransport([ENABLED] + board([node]) + [
            ("closingIssuesReferences", {"data": {"repository": {"pullRequest": {
                "closingIssuesReferences": {"nodes": [{"number": 1, "repository": {
                    "nameWithOwner": REPO}, "milestone": {"title": "M1"}}],
                    "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}}),
            ("milestones?state=all", [milestone(4, "M1")]),
            ("repos/owner/repo/issues/10", {"number": 10, "milestone": None}),
            (lambda c: c["kind"] == "rest" and c["method"] == "PATCH", {"number": 10}),
            OPEN_MILESTONES])
        code, out = run_cli(fake, "sync")
        self.assertEqual(code, 0, out)
        self.assertEqual([m["path"] for m in fake.mutations], ["repos/owner/repo/issues/10"])
        closing_reads = [c for c in fake.calls if "closingIssuesReferences" in (c.get("query") or "")]
        self.assertEqual([c["variables"]["number"] for c in closing_reads], [10])
        self.assertIn("owner/repo#1: ignored untrusted PR owner/repo#9\n", out)
        self.assertIn("write: owner/repo#10 milestone: M1", out)
        self.assertEqual(fake.responses, [])

    def test_guarded_write_skips_when_the_association_changed(self):
        before = item(issue(1, prs=[pr(9, head_repo=FORK, association="COLLABORATOR")]),
                      status="Ready", order=1)
        after = item(issue(1, prs=[pr(9, head_repo=FORK, association="NONE")]), status="Ready", order=1)
        fake = FakeTransport(board([before]) + [("query ProjectItem(", item_response(after)),
                                                ("closingIssuesReferences", closing_issues([(1, None)])),
                                                OPEN_MILESTONES])
        result = project_flow._reconcile(fake, CFG, URL, mode="agent", repo=None, dry_run=False)
        self.assertEqual(fake.mutations, [])
        self.assertIn("skipped owner/repo#1: inputs changed", result.reports)


if __name__ == "__main__":
    unittest.main()
