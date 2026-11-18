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

import json  # noqa: E402

from check_site import check_repo, main, nav_pages, published_files  # noqa: E402


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


MINTIGNORE = (
    "/*\n!/docs.json\n!/docs/\n/docs/*\n!/docs/user/\n!/docs/dev/\n!/docs/assets/\n"
    "!/openspec/\n/openspec/*\n!/openspec/specs/\n"
)


def docs_json(**extra):
    data = {
        "navigation": {
            "groups": [{"group": "G", "pages": ["docs/user/a", "docs/dev/specs/x"]}]
        },
        "logo": "docs/assets/veles-icon.png",
        "favicon": "docs/assets/veles-icon.png",
    }
    data.update(extra)
    return json.dumps(data)


def base_site():
    return {
        ".mintignore": MINTIGNORE,
        "docs.json": docs_json(),
        "docs/user/a.md": "a\n",
        "docs/dev/specs/x.mdx": 'import X from "/openspec/specs/x/spec.md";\n\n<X />\n',
        "openspec/specs/x/spec.md": "spec\n",
        "docs/assets/veles-icon.png": "png",
    }


class StructureTests(unittest.TestCase):
    def repo_files(self, files):
        root = make_repo(files)
        self.addCleanup(shutil.rmtree, root)
        return root

    def findings(self, files):
        return [
            "%s %s: %s" % f for f in check_repo(self.repo_files(files))
        ]

    def assertOne(self, files, *needles):
        lines = self.findings(files)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("error "), lines)
        for needle in needles:
            self.assertIn(needle, lines[0])

    def with_docs_json(self, text):
        files = base_site()
        files["docs.json"] = text
        return files

    def test_base_site_is_clean(self):
        self.assertEqual(check_repo(self.repo_files(base_site())), [])

    def test_missing_docs_json(self):
        files = base_site()
        del files["docs.json"]
        self.assertOne(files, "docs.json")

    def test_invalid_docs_json(self):
        self.assertOne(self.with_docs_json("{not json"), "docs.json")

    def test_nav_page_without_file(self):
        data = json.loads(docs_json())
        data["navigation"]["groups"][0]["pages"].append("docs/user/nope")
        self.assertOne(self.with_docs_json(json.dumps(data)), "docs/user/nope")

    def test_nav_page_unpublished(self):
        files = base_site()
        files["docs/other/b.md"] = "b\n"
        data = json.loads(docs_json())
        data["navigation"]["groups"][0]["pages"].append("docs/other/b")
        files["docs.json"] = json.dumps(data)
        self.assertOne(files, "docs/other/b")

    def test_duplicate_nav_page(self):
        data = json.loads(docs_json())
        data["navigation"]["groups"][0]["pages"].append("docs/user/a")
        self.assertOne(self.with_docs_json(json.dumps(data)), "docs/user/a", "twice")

    def test_import_missing_file(self):
        files = base_site()
        files["docs/user/b.mdx"] = 'import Y from "/openspec/nope.md";\n'
        self.assertOne(files, "docs/user/b.mdx", "/openspec/nope.md")

    def test_wrapper_import_mismatch(self):
        files = base_site()
        files["openspec/specs/y/spec.md"] = "y\n"
        files["docs/dev/specs/x.mdx"] = 'import Y from "/openspec/specs/y/spec.md";\n'
        self.assertOne(files, "docs/dev/specs/x.mdx", "/openspec/specs/x/spec.md")

    def test_wrapper_without_import(self):
        files = base_site()
        files["docs/dev/specs/x.mdx"] = "no import\n"
        self.assertOne(files, "docs/dev/specs/x.mdx")

    def test_relative_import(self):
        files = base_site()
        files["docs/user/b.mdx"] = 'import X from "./x.md";\n'
        data = json.loads(docs_json())
        data["navigation"]["groups"][0]["pages"].append("docs/user/b")
        files["docs.json"] = json.dumps(data)
        self.assertOne(files, "docs/user/b.mdx", "root-relative")

    def test_wrapper_not_in_nav(self):
        files = base_site()
        files["docs/dev/specs/z.mdx"] = 'import Z from "/openspec/specs/z/spec.md";\n'
        files["openspec/specs/z/spec.md"] = "z\n"
        self.assertOne(files, "docs/dev/specs/z.mdx", "navigation")

    def test_logo_unpublished(self):
        files = base_site()
        files["docs.json"] = docs_json(logo="docs/missing.png")
        self.assertOne(files, "logo")

    def test_favicon_unpublished(self):
        files = base_site()
        files["docs.json"] = docs_json(favicon="/docs/missing.png")
        self.assertOne(files, "favicon")

    def test_logo_object_light_dark(self):
        both = {"light": "docs/assets/veles-icon.png", "dark": "/docs/assets/veles-icon.png"}
        files = base_site()
        files["docs.json"] = docs_json(logo=both)
        self.assertEqual(check_repo(self.repo_files(files)), [])
        files["docs.json"] = docs_json(logo=dict(both, dark="docs/missing.png"))
        self.assertOne(files, "logo.dark")

    def test_missing_mintignore(self):
        files = base_site()
        del files[".mintignore"]
        root = self.repo_files(files)
        found = check_repo(root)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].path, ".mintignore")
        self.assertIn("is missing", found[0].message)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--root", root])
        self.assertEqual(code, 1)
        self.assertEqual(len(out.getvalue().splitlines()), 1)

    def test_mintignore_empty_or_comment_only(self):
        for text in ("", "# only a comment\n\n"):
            files = base_site()
            files[".mintignore"] = text
            lines = self.findings(files)
            self.assertEqual(
                lines[0],
                'error .mintignore: has no patterns; the first pattern must be "/*"',
            )
            self.assertEqual(len([l for l in lines if ".mintignore" in l]), 1, lines)

    def test_mintignore_bad_form(self):
        files = base_site()
        files[".mintignore"] = "/*\ndocs/**\n"
        lines = self.findings(files)
        self.assertTrue(any(l.startswith("error .mintignore:2: ") for l in lines), lines)

    def test_mintignore_first_line_not_root(self):
        files = base_site()
        files[".mintignore"] = "# comment\n!/docs/\n"
        lines = self.findings(files)
        self.assertTrue(any(l.startswith("error .mintignore:2: ") for l in lines), lines)

    def test_mintignore_crlf_and_trailing_space(self):
        files = base_site()
        files[".mintignore"] = MINTIGNORE.replace("\n", " \r\n")
        self.assertEqual(check_repo(self.repo_files(files)), [])

    def test_nav_walks_nested_groups_and_product_pages(self):
        navigation = {
            "products": [
                {"product": "P", "icon": "i", "pages": ["p/one"]},
                {
                    "group": "G",
                    "pages": ["g/two", {"group": "Inner", "pages": ["g/three"]}],
                },
            ]
        }
        self.assertEqual(nav_pages(navigation), ["p/one", "g/two", "g/three"])


if __name__ == "__main__":
    unittest.main()
