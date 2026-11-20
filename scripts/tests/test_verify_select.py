"""Selection (design D3): the base, the four git reads, unaccounted paths and reasons.

Every case drives `select` through `FakeGit`. The component map is the repository's real
`.github/components.json`, because the expected node sets are its rules: exact
`global-overrides` win, `rust/` also selects `android`, `web-extension/src/` also selects
`protocol` and `supply-chain`, and a global path selects every node.
"""
import hashlib
import os
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS)
REPO = os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, TESTS)

import components  # noqa: E402
from verify import Git, GitError, SelectionError, select  # noqa: E402
from verifyfake import FakeGit  # noqa: E402

BASE = "a" * 40
COMPONENT_MAP = os.path.join(REPO, ".github", "components.json")


def load_cmap():
    return components.load_map(COMPONENT_MAP)


def git_for(**overrides):
    overrides.setdefault("base", BASE)
    return FakeGit(**overrides)


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.cmap = load_cmap()

    def run_select(self, git, base=None):
        return select(git, self.cmap, base)

    def test_union_of_four_reads(self):
        # Committed (against the branch base), staged, unstaged and untracked paths all count.
        git = git_for(
            diff=["scripts/committed.py"],
            staged=["scripts/staged.py"],
            unstaged=["scripts/unstaged.py"],
            untracked=["scripts/untracked.py"],
        )
        selection = self.run_select(git)
        self.assertEqual(selection.base, BASE)
        self.assertEqual(
            selection.files,
            ["scripts/committed.py", "scripts/staged.py",
             "scripts/unstaged.py", "scripts/untracked.py"],
        )
        self.assertEqual(selection.reasons, {"tooling": "scripts/committed.py"})
        self.assertFalse(selection.only_unowned)
        self.assertEqual(
            git.calls,
            [("merge_base", "origin/master"), ("diff_names", BASE),
             ("staged",), ("unstaged",), ("untracked",)],
        )

    def test_deleted_path_counts(self):
        # A deleted path is a diff path that no longer exists on disk; select must not stat it.
        path = "scripts/deleted_test.py"
        selection = self.run_select(git_for(diff=[path]))
        self.assertEqual(selection.files, [path])
        self.assertEqual(selection.reasons, {"tooling": path})

    def test_move_counts_both_sides(self):
        # `--no-renames` reports a move as its old path plus its new path; both select nodes.
        old = "web-extension/src/moved.ts"
        new = "native-bridge/host/src/moved.rs"
        selection = self.run_select(git_for(diff=[new, old]))
        self.assertEqual(selection.files, [new, old])
        self.assertEqual(selection.reasons["web-extension"], old)
        self.assertEqual(selection.reasons["native-host"], new)
        # Both paths select these nodes; the reason is the first one in sorted order.
        self.assertEqual(selection.reasons["protocol"], new)
        self.assertEqual(selection.reasons["supply-chain"], new)

    def test_extension_src_selects_three(self):
        path = "web-extension/src/background.ts"
        selection = self.run_select(git_for(unstaged=[path]))
        self.assertEqual(set(selection.reasons),
                         {"web-extension", "protocol", "supply-chain"})
        for node in ("web-extension", "protocol", "supply-chain"):
            self.assertEqual(selection.reasons[node], path)
        self.assertFalse(selection.only_unowned)

    def test_rust_selects_android_with_reason(self):
        path = "rust/veles-crypto/src/lib.rs"
        selection = self.run_select(git_for(diff=[path]))
        self.assertIn("android", selection.reasons)
        self.assertEqual(selection.reasons["android"], path)

    def test_global_path_selects_all(self):
        path = "gradle/libs.versions.toml"
        selection = self.run_select(git_for(diff=[path]))
        self.assertEqual(set(selection.reasons), set(self.cmap["nodes"]))
        for reason in selection.reasons.values():
            self.assertEqual(reason, path)

    def test_exact_override_wins(self):
        # `.github/` is global, but this exact file's override wins before path ownership.
        path = ".github/workflows/check-android.yml"
        selection = self.run_select(git_for(diff=[path]))
        self.assertEqual(set(selection.reasons), {"android", "tooling", "installer"})
        for reason in selection.reasons.values():
            self.assertEqual(reason, path)

    def test_unaccounted_is_error(self):
        path = "mystery/file.txt"
        with self.assertRaises(SelectionError) as caught:
            self.run_select(git_for(diff=[path]))
        error = caught.exception
        self.assertEqual(error.kind, "unaccounted")
        self.assertEqual(error.paths, [path])
        self.assertIn("not owned, global or unowned", str(error))
        self.assertIn(path, str(error))
        self.assertIn(".github/components.json", error.fix)
        self.assertEqual(
            error.identity,
            ("selection", "unaccounted", hashlib.sha256(path.encode("utf-8")).hexdigest()),
        )

    def test_select_no_origin_master(self):
        git = git_for(
            errors={"merge_base": GitError("fatal: Not a valid object name origin/master")})
        with self.assertRaises(SelectionError) as caught:
            self.run_select(git)
        error = caught.exception
        self.assertEqual(error.kind, "base")
        self.assertIn("git fetch origin master", error.fix)
        self.assertIn("--base", error.fix)
        self.assertEqual(error.identity, ("selection", "base", "origin/master"))
        # Nothing is read from the working tree once the base is unusable.
        self.assertEqual(git.calls, [("merge_base", "origin/master")])

    def test_explicit_base_is_resolved_to_a_commit(self):
        git = git_for(diff=["scripts/a.py"], commit="b" * 40)
        selection = self.run_select(git, base="topic-base")
        self.assertEqual(selection.base, "b" * 40)
        self.assertIn(("commit", "topic-base"), git.calls)
        self.assertNotIn(("merge_base", "origin/master"), git.calls)
        self.assertEqual(selection.reasons, {"tooling": "scripts/a.py"})

    def test_explicit_base_not_a_commit(self):
        git = git_for(errors={"commit": GitError("fatal: Needed a single revision")})
        with self.assertRaises(SelectionError) as caught:
            self.run_select(git, base="deadbeef")
        error = caught.exception
        self.assertEqual(error.kind, "base")
        self.assertIn("git fetch origin master", error.fix)
        self.assertIn("--base", error.fix)
        self.assertEqual(error.identity, ("selection", "base", "deadbeef"))

    def test_failed_git_read(self):
        git = git_for(diff=["scripts/a.py"],
                      errors={"unstaged": GitError("fatal: bad read")})
        with self.assertRaises(SelectionError) as caught:
            self.run_select(git)
        error = caught.exception
        self.assertEqual(error.kind, "git")
        self.assertTrue(error.fix)
        self.assertIn("fatal: bad read", str(error))
        self.assertEqual(error.identity[0:2], ("selection", "git"))

    def test_nothing_changed(self):
        selection = self.run_select(git_for())
        self.assertEqual(selection.base, BASE)
        self.assertEqual(selection.files, [])
        self.assertEqual(selection.reasons, {})
        self.assertFalse(selection.only_unowned)

    def test_only_unowned(self):
        path = "site/index.html"
        selection = self.run_select(git_for(diff=[path]))
        self.assertEqual(selection.files, [path])
        self.assertEqual(selection.reasons, {})
        self.assertTrue(selection.only_unowned)


class RecordingGit(Git):
    """A real `Git` with only its subprocess read replaced, to pin the four D3 argv forms."""

    def __init__(self):
        Git.__init__(self, "/repo")
        self.reads = []

    def _read(self, *args):
        self.reads.append(args)
        return b"a.py\0b/c.py\0"


class GitReadsTest(unittest.TestCase):
    def test_reads_are_nul_delimited_without_renames(self):
        git = RecordingGit()
        self.assertEqual(git.diff_names("abc123"), ["a.py", "b/c.py"])
        self.assertEqual(git.staged(), ["a.py", "b/c.py"])
        self.assertEqual(git.unstaged(), ["a.py", "b/c.py"])
        self.assertEqual(git.untracked(), ["a.py", "b/c.py"])
        self.assertEqual(
            git.reads,
            [
                ("diff", "--name-only", "--no-renames", "-z", "abc123", "HEAD"),
                ("diff", "--cached", "--name-only", "--no-renames", "-z"),
                ("diff", "--name-only", "--no-renames", "-z"),
                ("ls-files", "--others", "--exclude-standard", "-z"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
