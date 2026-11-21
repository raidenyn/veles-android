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
    """An injected `run_git`: fixed answers, recorded calls, and an optional injected clock.

    `advance` moves the clock during each call, so the timeout computed before a call differs from
    the next one's; `failures` maps a call's args tuple to its `(returncode, stdout, stderr)`.
    """

    def __init__(self, toplevel="", head="", base="", clock=None, advance=0.0, failures=None):
        self.toplevel = toplevel
        self.head = head
        self.base = base
        self.clock = clock
        self.advance = advance
        self.failures = dict(failures or {})
        self.calls = []

    def __call__(self, args, cwd, timeout):
        self.calls.append((list(args), cwd, timeout))
        if self.clock is not None:
            self.clock.value += self.advance
        failure = self.failures.get(tuple(args))
        if failure is not None:
            return failure
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

    def passing_checkout(self):
        """A fixture whose HEAD is one commit past origin/master, with a matching pass written."""
        repo = gitfixture.make_repo(self)
        repo.git("update-ref", "refs/remotes/origin/master", repo.init)
        head = repo.commit("feat(android): change the app")
        base = repo.git("merge-base", "origin/master", "HEAD")
        branch_pass.write(str(repo.path), head, base, NODES)
        return repo, head, base

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
            ("a boolean version", json.dumps(
                {"version": True, "head": head, "base": base, "nodes": NODES})),
            ("non-list nodes", json.dumps(
                {"version": 1, "head": head, "base": base, "nodes": "android"})),
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
        repo, head, base = self.passing_checkout()

        # A clock already at the deadline denies without running git.
        expired = RecordingGit(str(repo.path), head, base)
        self.clock.value = 10.0
        self.assertEqual((False, "proof timed out"),
                         self.check(str(repo.path), deadline=5.0, run_git=expired))
        self.assertEqual([], expired.calls)

        # Each call gets the time left at that moment, not one timeout computed at entry.
        self.clock.value = 0.0
        recording = RecordingGit(str(repo.path), head, base,
                                 clock=self.clock, advance=1.0)
        self.assertEqual((True, ""),
                         self.check(str(repo.path), deadline=2.5, run_git=recording))
        self.assertEqual([2.5, 1.5, 0.5],
                         [timeout for _, _, timeout in recording.calls])
        for args, cwd, timeout in recording.calls:
            self.assertEqual(str(repo.path), cwd)

        # A deadline reached between two calls denies before the next call runs.
        self.clock.value = 0.0
        stopping = RecordingGit(str(repo.path), head, base,
                                clock=self.clock, advance=1.0)
        self.assertEqual((False, "proof timed out"),
                         self.check(str(repo.path), deadline=1.5, run_git=stopping))
        self.assertEqual(["rev-parse --show-toplevel", "rev-parse HEAD"],
                         [" ".join(args) for args, _, _ in stopping.calls])
        self.assertEqual([1.5, 0.5],
                         [timeout for _, _, timeout in stopping.calls])

    def test_check_toplevel_git_failure(self):
        repo = gitfixture.make_repo(self)
        stderr = "fatal: could not read repository"

        failing = RecordingGit(failures={
            ("rev-parse", "--show-toplevel"): (128, "", stderr + "\n"),
        })
        ok, message = self.check(str(repo.path), run_git=failing)
        self.assertFalse(ok)
        self.assertEqual(stderr, message)
        self.assertEqual(1, len(failing.calls))

        # Only git's own "not a repository" diagnostic means "no branch pass here".
        not_a_checkout = RecordingGit(failures={
            ("rev-parse", "--show-toplevel"): (
                128, "", "fatal: not a git repository (or any parent): .git\n"),
        })
        ok, message = self.check(str(repo.path), run_git=not_a_checkout)
        self.assertFalse(ok)
        self.assertEqual(
            "no branch pass in %s: not a git checkout" % str(repo.path), message)

    def test_check_toplevel_timeout_past_deadline(self):
        repo = gitfixture.make_repo(self)
        timing_out = RecordingGit(
            clock=self.clock,
            advance=1.0,
            failures={("rev-parse", "--show-toplevel"): (
                1, "", "git rev-parse --show-toplevel could not run: timed out")},
        )

        ok, message = self.check(str(repo.path), deadline=0.5, run_git=timing_out)

        self.assertFalse(ok)
        self.assertEqual("proof timed out", message)
        self.assertEqual(1, len(timing_out.calls))

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
