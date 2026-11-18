"""Tests for the documentation site guard: the published-set computation.

Each fixture is a throwaway git repository (initialised and staged, never committed), so tests
exercise the same git calls the real run uses. Stdlib only; Python 3.8 compatible.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

CHECKS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CHECKS)

from check_site import main, published_files  # noqa: E402


def _write(root, files):
    for name, text in files.items():
        path = os.path.join(root, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)


def make_repo(files, untracked=None):
    """Write ``files`` into a fresh git repository, stage them, then write ``untracked``."""
    root = tempfile.mkdtemp(prefix="check-site-")
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    _write(root, files)
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git"] + args, cwd=root, env=env, check=True)
    _write(root, untracked or {})
    return root


class PublishedSetTests(unittest.TestCase):
    def repo(self, files, untracked=None):
        root = make_repo(files, untracked)
        self.addCleanup(shutil.rmtree, root)
        return root

    def test_allowlist_publishes_only_reincluded(self):
        root = self.repo(
            {
                ".mintignore": "/*\n!/docs.json\n!/docs/\n/docs/*\n!/docs/user/\n",
                "docs.json": "{}\n",
                "docs/user/a.md": "a\n",
                "docs/otp-1.md": "b\n",
                "AGENTS.md": "c\n",
            }
        )
        self.assertEqual(published_files(root), ["docs.json", "docs/user/a.md"])

    def test_untracked_page_is_unpublished(self):
        root = self.repo(
            {".mintignore": "/*\n!/docs/\n", "docs/user/a.md": "a\n"},
            {"docs/user/new.md": "n\n"},
        )
        self.assertNotIn("docs/user/new.md", published_files(root))
        self.assertIn("docs/user/a.md", published_files(root))

    def test_default_ignore_dropped_without_reinclude(self):
        root = self.repo(
            {
                ".mintignore": "/*\n!/docs/\n",
                "docs/node_modules/x.md": "x\n",
                "docs/README.md": "r\n",
                "docs/ok.md": "o\n",
            }
        )
        self.assertEqual(published_files(root), ["docs/ok.md"])

    def test_default_ignore_kept_with_exact_reinclude(self):
        root = self.repo(
            {".mintignore": "/*\n!/CONTRIBUTING.md\n", "CONTRIBUTING.md": "c\n"}
        )
        self.assertEqual(published_files(root), ["CONTRIBUTING.md"])

    def test_path_with_space_and_unicode(self):
        name = "docs/my page é.md"
        root = self.repo({".mintignore": "/*\n!/docs/\n", name: "p\n"})
        self.assertEqual(published_files(root), [name])

    def test_missing_mintignore_returns_empty(self):
        root = self.repo({"docs/a.md": "a\n"})
        self.assertEqual(published_files(root), [])

    def test_main_outside_git_repository(self):
        root = tempfile.mkdtemp(prefix="check-site-")
        self.addCleanup(shutil.rmtree, root)
        _write(root, {".mintignore": "/*\n"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--root", root])
        lines = out.getvalue().splitlines()
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("error "))
        self.assertIn("git ls-files", lines[0])


if __name__ == "__main__":
    unittest.main()
