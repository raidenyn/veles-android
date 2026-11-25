"""The verify gate's command: an edit lints one file, every other trigger execs the task gate."""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

TESTS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS)
sys.path.insert(0, SCRIPTS)

import verify_gate  # noqa: E402


class EditChecks(unittest.TestCase):
    def argvs(self, rel):
        commands, in_process = verify_gate.edit_checks(rel)
        return [argv for _, argv in commands], len(in_process)

    def test_npm_package_files_use_the_package_linters(self):
        argvs, _ = self.argvs("web-extension/src/content.ts")
        self.assertEqual([os.path.basename(argv[0]) for argv in argvs], ["eslint", "prettier"])
        self.assertEqual(argvs[0][1:], ["src/content.ts"])
        self.assertEqual(argvs[1][1:], ["--check", "src/content.ts"])
        argvs, _ = self.argvs("native-bridge/src/main.ts")
        self.assertEqual(argvs[0][1:], ["src/main.ts"])

    def test_native_host_and_installer_are_not_npm_packages(self):
        argvs, _ = self.argvs("native-bridge/installer/scripts/x.mjs")
        self.assertEqual(argvs, [["node", "--check", "native-bridge/installer/scripts/x.mjs"]])

    def test_other_kinds(self):
        self.assertEqual(self.argvs("scripts/x.mjs"), ([["node", "--check", "scripts/x.mjs"]], 0))
        self.assertEqual(self.argvs("a/b.sh"), ([["sh", "-n", "a/b.sh"]], 0))
        self.assertEqual(self.argvs("rust/a.rs"), ([["rustfmt", "--check", "--edition", "2024", "rust/a.rs"]], 0))
        self.assertEqual(self.argvs("scripts/x.py"), ([], 1))
        self.assertEqual(self.argvs("docs.json"), ([], 1))

    def test_files_without_a_single_file_lint(self):
        for rel in ("android/app/src/main/A.kt", "docs/verify.md", "build.gradle.kts"):
            self.assertEqual(self.argvs(rel), ([], 0), rel)


class Lint(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo)
        patcher = mock.patch.object(verify_gate, "REPO", self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)

    def lint(self, rel):
        out = io.StringIO()
        with redirect_stdout(out):
            status = verify_gate.lint(rel)
        return status, out.getvalue()

    def test_python_syntax_error_fails_and_names_the_file(self):
        self.write("scripts/bad.py", "x = (\n")
        status, out = self.lint("scripts/bad.py")
        self.assertEqual(status, 1)
        self.assertIn("scripts/bad.py", out)

    def test_invalid_json_fails(self):
        self.write("a.json", "{,}")
        self.assertEqual(self.lint("a.json")[0], 1)

    def test_clean_files_pass(self):
        self.write("scripts/ok.py", "x = 1\n")
        self.write("a.json", "{}")
        self.assertEqual(self.lint("scripts/ok.py"), (0, ""))
        self.assertEqual(self.lint("a.json"), (0, ""))

    def test_deleted_and_unlintable_files_pass(self):
        self.write("docs/a.md", "# x\n")
        self.assertEqual(self.lint("docs/gone.py"), (0, ""))
        self.assertEqual(self.lint("docs/a.md"), (0, ""))

    def test_missing_package_linter_is_skipped(self):
        self.write("web-extension/src/a.ts", "export {}\n")
        with mock.patch.object(verify_gate.subprocess, "run") as run:
            self.assertEqual(self.lint("web-extension/src/a.ts")[0], 0)
        run.assert_not_called()

    def test_failing_command_fails(self):
        self.write("a.sh", "echo\n")
        with mock.patch.object(verify_gate.subprocess, "run", return_value=mock.Mock(returncode=2)) as run:
            self.assertEqual(self.lint("a.sh")[0], 1)
        run.assert_called_once_with(["sh", "-n", "a.sh"], cwd=self.repo)


class Main(unittest.TestCase):
    def test_edit_lints_the_file(self):
        with mock.patch.object(verify_gate, "lint", return_value=1) as lint:
            self.assertEqual(verify_gate.main({"VERIFY_TRIGGER": "edit", "VERIFY_FILE": "a.py"}), 1)
        lint.assert_called_once_with("a.py")

    def test_other_triggers_exec_the_task_gate(self):
        for env in ({"VERIFY_TRIGGER": "task"}, {"VERIFY_TRIGGER": "turn-end"}, {}):
            with mock.patch.object(verify_gate.os, "execv") as execv:
                verify_gate.main(env)
            execv.assert_called_once_with(sys.executable, [sys.executable, "-I", verify_gate.VERIFY])
        self.assertEqual(verify_gate.VERIFY, os.path.join(verify_gate.REPO, "scripts", "verify.py"))


if __name__ == "__main__":
    unittest.main()
