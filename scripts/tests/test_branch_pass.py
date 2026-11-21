"""The branch-pass record: its path, version-1 format, atomic writer and the push-gate check.

Every case uses a real fixture repository (`gitfixture.Repo`) or an injected `run_git`, and an
injected clock, so no test reads the wall clock or sleeps (design D2/D3).
"""
import json
import os
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, TESTS)

import branch_pass  # noqa: E402
import gitfixture  # noqa: E402

RECORD_RELATIVE = os.path.join("build", "verify", "branch-pass.json")
NODES = ["android", "rust"]


class Clock:
    """An injected `now` that only moves when a test moves it."""

    def __init__(self, now=0.0):
        self.value = float(now)

    def now(self):
        return self.value


class RecordingGit:
    """An injected `run_git`: fixed answers, and every call's working directory and timeout."""

    def __init__(self, toplevel, head, base):
        self.toplevel = toplevel
        self.head = head
        self.base = base
        self.calls = []

    def __call__(self, args, cwd, timeout):
        self.calls.append((list(args), cwd, timeout))
        if args == ["rev-parse", "--show-toplevel"]:
            return 0, self.toplevel + "\n", ""
        if args == ["rev-parse", "HEAD"]:
            return 0, self.head + "\n", ""
        if args == ["merge-base", "origin/master", "HEAD"]:
            return 0, self.base + "\n", ""
        raise AssertionError("unexpected git call %r" % (args,))


def short(sha):
    return sha[:12]


def record_file(repo):
    return os.path.join(os.path.realpath(str(repo.path)), RECORD_RELATIVE)


class BranchPassTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def check(self, cwd, deadline=5.0, run_git=None):
        if run_git is None:
            return branch_pass.check(cwd, deadline, now=self.clock.now)
        return branch_pass.check(cwd, deadline, now=self.clock.now, run_git=run_git)

    def test_write_then_check_passes(self):
        repo = gitfixture.make_repo(self)
        repo.git("update-ref", "refs/remotes/origin/master", repo.init)
        head = repo.commit("feat(android): change the app")
        base = repo.git("merge-base", "origin/master", "HEAD")

        branch_pass.write(str(repo.path), head, base, NODES)

        path = branch_pass.record_path(str(repo.path))
        self.assertEqual(record_file(repo), os.path.realpath(path))
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(
            {"version": 1, "head": head, "base": base, "nodes": NODES}, document)
        self.assertNotIn("time", document)
        self.assertEqual((True, ""), self.check(str(repo.path)))

    def test_check_no_record(self):
        repo = gitfixture.make_repo(self)
        self.assertFalse(os.path.exists(record_file(repo)))

        ok, message = self.check(str(repo.path))

        self.assertFalse(ok)
        self.assertEqual("no branch pass for HEAD %s" % short(repo.head()), message)

    def test_check_other_head(self):
        repo = gitfixture.make_repo(self)
        repo.git("update-ref", "refs/remotes/origin/master", repo.init)
        head = repo.commit("feat(android): change the app")
        branch_pass.write(str(repo.path), repo.init, repo.init, NODES)

        ok, message = self.check(str(repo.path))

        self.assertFalse(ok)
        self.assertEqual(
            "pass is for %s, HEAD is %s" % (short(repo.init), short(head)), message)

    def test_check_other_base(self):
        repo = gitfixture.make_repo(self)
        repo.git("update-ref", "refs/remotes/origin/master", repo.init)
        head = repo.commit("feat(android): change the app")
        base = repo.git("merge-base", "origin/master", "HEAD")
        # origin/master moves to the pass's HEAD: the merge base becomes HEAD, not `base`.
        repo.git("update-ref", "refs/remotes/origin/master", head)
        branch_pass.write(str(repo.path), head, base, NODES)

        ok, message = self.check(str(repo.path))

        self.assertFalse(ok)
        self.assertIn("base", message)
        self.assertIn(short(base), message)
        self.assertIn(short(head), message)

    def test_check_unreadable_record(self):
        repo = gitfixture.make_repo(self)
        repo.git("update-ref", "refs/remotes/origin/master", repo.init)
        head = repo.commit("feat(android): change the app")
        base = repo.git("merge-base", "origin/master", "HEAD")
        cases = [
            ("truncated JSON", '{"version": 1, "head": '),
            ("another version", json.dumps(
                {"version": 2, "head": head, "base": base, "nodes": NODES})),
        ]
        for label, text in cases:
            with self.subTest(record=label):
                branch_pass.write(str(repo.path), head, base, NODES)
                with open(record_file(repo), "w", encoding="utf-8") as handle:
                    handle.write(text)

                ok, message = self.check(str(repo.path))

                self.assertFalse(ok)
                self.assertTrue(
                    message.startswith("branch pass record unreadable: "), message)

    def test_check_git_failure(self):
        repo = gitfixture.make_repo(self)
        repo.git("update-ref", "refs/remotes/origin/master", repo.init)
        head = repo.commit("feat(android): change the app")
        base = repo.git("merge-base", "origin/master", "HEAD")
        branch_pass.write(str(repo.path), head, base, NODES)
        stderr = "fatal: Not a valid object name origin/master"

        def run_git(args, cwd, timeout):
            if args[0] == "merge-base":
                return 128, "", stderr + "\n"
            return branch_pass.default_run_git(args, cwd, timeout)

        ok, message = self.check(str(repo.path), run_git=run_git)

        self.assertFalse(ok)
        self.assertEqual(stderr, message)

    def test_check_deadline(self):
        repo = gitfixture.make_repo(self)
        repo.git("update-ref", "refs/remotes/origin/master", repo.init)
        head = repo.commit("feat(android): change the app")
        base = repo.git("merge-base", "origin/master", "HEAD")
        branch_pass.write(str(repo.path), head, base, NODES)

        expired = RecordingGit(str(repo.path), head, base)
        self.clock.value = 10.0
        self.assertEqual((False, "proof timed out"),
                         self.check(str(repo.path), deadline=5.0, run_git=expired))
        self.assertEqual([], expired.calls)

        recording = RecordingGit(str(repo.path), head, base)
        self.clock.value = 0.0
        self.assertEqual((True, ""),
                         self.check(str(repo.path), deadline=2.5, run_git=recording))
        self.assertEqual(3, len(recording.calls))
        for args, cwd, timeout in recording.calls:
            self.assertAlmostEqual(2.5, timeout, msg="git %s" % " ".join(args))
            self.assertEqual(str(repo.path), cwd)

    def test_check_from_linked_worktree(self):
        repo = gitfixture.make_repo(self)
        repo.git("update-ref", "refs/remotes/origin/master", repo.init)
        repo.commit("feat(android): change the app")
        worktree = os.path.join(repo.tmp, "linked")
        repo.git("worktree", "add", "-q", "-b", "side", worktree, "HEAD")
        repo.git("commit", "-q", "--no-verify", "--allow-empty",
                 "-m", "feat(android): side change", cwd=worktree)
        head = repo.git("rev-parse", "HEAD", cwd=worktree)
        base = repo.git("merge-base", "origin/master", "HEAD", cwd=worktree)

        branch_pass.write(worktree, head, base, NODES)

        path = os.path.realpath(branch_pass.record_path(worktree))
        self.assertEqual(os.path.join(os.path.realpath(worktree), RECORD_RELATIVE), path)
        self.assertEqual((True, ""), self.check(worktree))
        ok, message = self.check(str(repo.path))
        self.assertFalse(ok)
        self.assertEqual("no branch pass for HEAD %s" % short(repo.head()), message)

    def test_delete(self):
        repo = gitfixture.make_repo(self)
        head = repo.head()
        branch_pass.write(str(repo.path), head, head, NODES)
        self.assertTrue(os.path.exists(record_file(repo)))

        branch_pass.delete(str(repo.path))
        self.assertFalse(os.path.exists(record_file(repo)))
        branch_pass.delete(str(repo.path))  # a missing record is fine


if __name__ == "__main__":
    unittest.main()
