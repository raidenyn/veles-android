"""Tests for the docs offline guard: imports, process calls, git terms, output.

Each fixture is a throwaway git repository (initialised and staged, never committed), so tests
exercise the same git call the real run uses. The guard is imported and called in-process, so
this file never starts it through subprocess. Stdlib only; Python 3.8 compatible.
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
REPO = os.path.dirname(os.path.dirname(CHECKS))
sys.path.insert(0, CHECKS)

from check_offline import (  # noqa: E402
    GIT_FORBIDDEN_GLOBAL_EXACT,
    GIT_FORBIDDEN_GLOBAL_PREFIXES,
    GIT_FORBIDDEN_SUBCOMMANDS,
    Finding,
    main,
    scan,
)


def _write(root, files):
    for name, content in files.items():
        path = os.path.join(root, *name.split("/"))
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if isinstance(content, bytes):
            with open(path, "wb") as handle:
                handle.write(content)
        else:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)


def make_repo(files):
    """Write ``files`` into a fresh git repository and stage them."""
    root = tempfile.mkdtemp(prefix="check-offline-")
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    _write(root, files)
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git"] + args, cwd=root, env=env, check=True)
    return root


class GuardTestCase(unittest.TestCase):
    def repo(self, files):
        root = make_repo(files)
        self.addCleanup(shutil.rmtree, root)
        return root

    def findings(self, files):
        return scan(self.repo(files))

    def assertRejected(self, source, *needles):
        found = self.findings({"docs/checks/check_x.py": source})
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0].path, "docs/checks/check_x.py")
        for needle in needles:
            self.assertIn(needle, found[0].message)


class RealTreeTests(unittest.TestCase):
    def test_real_tree_passes(self):
        self.assertEqual(scan(REPO), [])


class RejectedFormTests(GuardTestCase):
    def test_rejects_network_import(self):
        self.assertRejected("import urllib.request\n", "urllib")

    def test_rejects_star_import(self):
        self.assertRejected("from os.path import *\n", "star import")

    def test_rejects_os_alias(self):
        self.assertRejected("import os as o\n", "alias")

    def test_rejects_subprocess_alias(self):
        self.assertRejected("import subprocess as s\n", "alias")

    def test_rejects_from_subprocess_import(self):
        self.assertRejected("from subprocess import run\n", "from subprocess")

    def test_rejects_subprocess_reference_outside_call(self):
        self.assertRejected("r = subprocess.run\n", "subprocess.run")

    def test_rejects_process_call_without_positional_argv(self):
        self.assertRejected('subprocess.run(args=["git"])\n', "positional")

    def test_rejects_process_call_with_non_git_argv(self):
        self.assertRejected('subprocess.run(["curl", u])\n', "git")

    def test_rejects_shell_keyword(self):
        self.assertRejected('subprocess.run(["git"], shell=True)\n', "shell")

    def test_rejects_executable_keyword(self):
        self.assertRejected(
            'subprocess.run(["git"], executable="/usr/bin/curl")\n', "executable"
        )

    def test_rejects_keyword_splat(self):
        self.assertRejected('subprocess.run(["git"], **kw)\n', "keyword")

    def test_rejects_list_token_after_non_constant(self):
        self.assertRejected('subprocess.run(["git", sub, "clone"])\n', "clone")

    def test_rejects_helper_with_constant_root(self):
        self.assertRejected('_git("/r", "clone")\n', "clone")

    def test_rejects_os_through_an_allowed_module(self):
        for source in (
            'shutil.os.system("curl x")\n',
            'os.path.os.system("curl x")\n',
            'tempfile._os.popen("x")\n',
        ):
            with self.subTest(source=source):
                self.assertRejected(source, "os")

    def test_rejects_git_dir_equals(self):
        self.assertRejected(
            'subprocess.run(["git", "--git-dir=/x", "status"])\n', "--git-dir="
        )

    def test_rejects_forbidden_git_global_option(self):
        self.assertRejected(
            'subprocess.run(["git", "-c", "core.sshCommand=x", "status"])\n', "-c"
        )

    def test_rejects_forbidden_git_subcommand_through_helper(self):
        self.assertRejected('_git(root, "clone", url)\n', "clone")

    def test_rejects_forbidden_git_global_prefix_through_helper(self):
        self.assertRejected('_git(root, "--exec-path=/tmp", "status")\n', "--exec-path")

    def test_rejects_os_system_call(self):
        self.assertRejected('os.system("x")\n', "os.system")

    def test_rejects_os_system_reference(self):
        self.assertRejected("f = os.system\n", "os.system")

    def test_rejects_getattr_on_os(self):
        self.assertRejected('getattr(os, "system")\n', "getattr")

    def test_rejects_sys_modules(self):
        self.assertRejected("sys.modules\n", "sys.modules")

    def test_rejects_vars(self):
        self.assertRejected("vars(os)\n", "vars")

    def test_rejects_eval(self):
        self.assertRejected('eval("1")\n', "eval")

    def test_rejects_unlisted_importlib_use(self):
        source = 'import importlib.util\n\ndef other():\n    return importlib.util.find_spec("x")\n'
        self.assertRejected(source, "importlib")

    def test_rejects_unparsable_file(self):
        self.assertRejected("def (\n", "parse")

    def test_rejects_tracked_non_python_file(self):
        found = self.findings({"docs/checks/run.sh": "#!/bin/sh\n"})
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0].path, "docs/checks/run.sh")
        self.assertIn("not a Python file", found[0].message)

    def test_rejects_syntax_error_line(self):
        found = self.findings({"docs/checks/check_x.py": "x = 1\n\ndef (\n"})
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0].line, 3)


class TableRejectedFormTests(GuardTestCase):
    CASES = [
        ("import os as o", "alias"),
        ("import sys as s", "alias"),
        ("from os import system", "system"),
        ("x.__dict__", "__dict__"),
        ("import builtins", "builtins"),
        ("globals()", "globals"),
        ("locals()", "locals"),
        ('exec("1")', "exec"),
        ('compile("1", "f", "exec")', "compile"),
        ('__import__("os")', "__import__"),
        ("getattr(m, name)", "getattr"),
        ('subprocess.Popen(["curl"])', "git"),
        ('subprocess.check_output(["curl"])', "git"),
        ('import importlib.util\n\nimportlib.util.find_spec("x")', "importlib"),
    ]

    def test_rejects_every_listed_form(self):
        for source, needle in self.CASES:
            with self.subTest(source=source):
                self.assertRejected(source + "\n", needle)

    def test_rejects_every_forbidden_git_global_option(self):
        for token in sorted(GIT_FORBIDDEN_GLOBAL_EXACT):
            source = 'subprocess.run(["git", "%s", "status"])\n' % token
            with self.subTest(token=token):
                self.assertRejected(source, token)

    def test_rejects_every_forbidden_git_global_prefix(self):
        for prefix in sorted(GIT_FORBIDDEN_GLOBAL_PREFIXES):
            token = prefix + "value"
            source = 'subprocess.run(["git", "%s", "status"])\n' % token
            with self.subTest(prefix=prefix):
                self.assertRejected(source, prefix)

    def test_rejects_every_forbidden_git_subcommand(self):
        for subcommand in sorted(GIT_FORBIDDEN_SUBCOMMANDS):
            source = 'subprocess.run(["git", "%s"])\n' % subcommand
            with self.subTest(subcommand=subcommand):
                self.assertRejected(source, subcommand)


class AcceptedFormTests(GuardTestCase):
    def test_accepts_allowed_forms(self):
        source = (
            "import re\n"
            "import subprocess\n"
            "\n"
            "def _git(root, *args):\n"
            '    return subprocess.run(["git"] + list(args), cwd=root)\n'
            "\n"
            "def calls(root, args, m):\n"
            '    subprocess.run(["git"] + args, cwd=root)\n'
            '    subprocess.run(["git", "ls-files"], cwd=root)\n'
            '    _git(root, "ls-files", "-z", "-c", "-i")\n'
            "    subprocess.PIPE\n"
            '    subprocess.CalledProcessError(1, "git")\n'
            '    re.compile("x")\n'
            '    getattr(m, "NAME", None)\n'
            "    import check_y\n"
        )
        found = self.findings(
            {
                "docs/checks/check_x.py": source,
                "docs/checks/check_y.py": "",
                "docs/checks/AGENTS.md": "guidance\n",
            }
        )
        self.assertEqual(found, [])

    def test_accepts_shell_false_and_config_subcommand(self):
        source = (
            "import subprocess\n"
            "\n"
            'subprocess.run(["git", "config", "core.autocrlf", "false"], shell=False)\n'
        )
        self.assertEqual(self.findings({"docs/checks/check_x.py": source}), [])


class FindingTests(GuardTestCase):
    def test_finding_has_path_and_line(self):
        found = self.findings(
            {"docs/checks/check_x.py": "x = 1\ny = 2\nimport urllib.request\n"}
        )
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0].path, "docs/checks/check_x.py")
        self.assertEqual(found[0].line, 3)
        self.assertIsInstance(found[0], Finding)

    def test_unknown_module_message_names_allow_list(self):
        found = self.findings({"docs/checks/check_x.py": "import pathlib\n"})
        self.assertEqual(len(found), 1, found)
        self.assertIn("pathlib", found[0].message)
        self.assertIn("STDLIB_ALLOWED", found[0].message)

    def test_crlf_and_bom_parse(self):
        found = self.findings({"docs/checks/check_x.py": b"\xef\xbb\xbfimport os\r\n"})
        self.assertEqual(found, [])

    def test_findings_are_sorted(self):
        found = self.findings(
            {
                "docs/checks/check_b.py": "import urllib\n",
                "docs/checks/check_a.py": "x = 1\nimport urllib\n",
            }
        )
        self.assertEqual(
            [(finding.path, finding.line) for finding in found],
            [("docs/checks/check_a.py", 2), ("docs/checks/check_b.py", 1)],
        )


class MainTests(GuardTestCase):
    def run_main(self, argv):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_main_exit_codes(self):
        clean = self.repo({"docs/checks/check_x.py": "import os\n"})
        dirty = self.repo({"docs/checks/check_x.py": "import urllib.request\n"})
        self.assertEqual(self.run_main(["--root", clean])[0], 0)
        self.assertEqual(self.run_main(["--root", dirty])[0], 1)
        self.assertEqual(self.run_main(["--root", "/nonexistent"])[0], 2)

    def test_main_prints_finding_location(self):
        dirty = self.repo({"docs/checks/check_x.py": "import urllib.request\n"})
        code, out, _ = self.run_main(["--root", dirty])
        self.assertEqual(code, 1)
        lines = out.splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("error docs/checks/check_x.py:1: "), lines)
        self.assertIn("urllib", lines[0])


if __name__ == "__main__":
    unittest.main()
