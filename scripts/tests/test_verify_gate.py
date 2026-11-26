"""The verify gate's command: an edit lints one file, every other trigger execs the task gate."""
import io
import subprocess
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

    def test_other_triggers_run_the_task_gate_with_passes_only_on_a_dirty_tree(self):
        fake = mock.Mock()
        fake.real_context.return_value = mock.Mock(repo="/r")
        for env in ({"VERIFY_TRIGGER": "task"}, {"VERIFY_TRIGGER": "turn-end"}, {}):
            for clean in (True, False):
                with mock.patch.object(verify_gate, "load_verify", return_value=fake), \
                        mock.patch.object(verify_gate, "tree_clean", return_value=clean), \
                        mock.patch.object(verify_gate, "wait_limit_seconds", return_value=7), \
                        mock.patch.object(verify_gate, "full_run", return_value=0) as run:
                    self.assertEqual(verify_gate.main(env), 0)
                run.assert_called_once_with(fake, fake.real_context.return_value,
                                            use_passes=not clean, wait_limit=7)


class Outcome:
    def __init__(self, contended=False, errors=(), selection=None):
        self.contended = contended
        self.errors = list(errors)
        self.results = {}
        self.selection = selection


class FakeVerify:
    """`run_gate` answers from a list of outcomes; the rest is what `full_run` reads."""

    TableError = ValueError

    def __init__(self, outcomes, code=0):
        self.outcomes = list(outcomes)
        self.calls = []
        self.code = code
        self.components = mock.Mock()
        self.load_table = mock.Mock(return_value="table")

    def run_gate(self, gate, base, ctx, table, cmap, use_passes=False):
        self.calls.append((gate, base, use_passes))
        return self.outcomes.pop(0)

    def render(self, results, errors):
        return "node: PASS"

    def exit_code(self, results, errors):
        return self.code


class Ctx:
    """An injected clock that moves only when `wait.sleep` is called."""

    def __init__(self):
        self.repo = "/r"
        self.now = 0.0
        self.lines = []
        self.clock = mock.Mock(now=lambda: self.now)
        self.wait = mock.Mock(sleep=self.sleep)
        self.log = self.lines.append

    def sleep(self, seconds):
        self.now += seconds


class FullRun(unittest.TestCase):
    def test_runs_the_task_gate_with_the_requested_passes(self):
        for use_passes in (True, False):
            fake, ctx = FakeVerify([Outcome()], code=1), Ctx()
            self.assertEqual(verify_gate.full_run(fake, ctx, use_passes, wait_limit=10), 1)
            self.assertEqual(fake.calls, [("task", None, use_passes)])
            self.assertEqual(ctx.lines, ["node: PASS"])

    def test_contention_waits_for_the_lock_then_runs(self):
        fake, ctx = FakeVerify([Outcome(contended=True), Outcome(contended=True), Outcome()]), Ctx()
        self.assertEqual(verify_gate.full_run(fake, ctx, True, wait_limit=10), 0)
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(ctx.now, 2 * verify_gate.POLL_SECONDS)

    def test_contention_past_the_wait_limit_fails_and_says_so(self):
        busy = [Outcome(contended=True, errors=["verification already running"]) for _ in range(10)]
        fake, ctx = FakeVerify(busy), Ctx()
        self.assertEqual(verify_gate.full_run(fake, ctx, True, wait_limit=3), 2)
        self.assertEqual(ctx.lines, ["verify: verification already running"])
        self.assertLessEqual(ctx.now, 3 + verify_gate.POLL_SECONDS)

    def test_nothing_changed_passes_with_its_line(self):
        fake, ctx = FakeVerify([Outcome(selection=mock.Mock(files=[], base="abc"))]), Ctx()
        self.assertEqual(verify_gate.full_run(fake, ctx, True, wait_limit=10), 0)
        self.assertEqual(ctx.lines, ["verify: nothing changed against abc"])

    def test_an_invalid_table_fails(self):
        fake, ctx = FakeVerify([]), Ctx()
        fake.load_table.side_effect = ValueError("bad table")
        self.assertEqual(verify_gate.full_run(fake, ctx, True, wait_limit=10), 2)
        self.assertEqual(ctx.lines, ["verify: bad table"])


class Settings(unittest.TestCase):
    def write(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.addCleanup(os.unlink, handle.name)
        handle.write(text)
        handle.close()
        return handle.name

    def test_wait_limit_is_the_gate_timeout_less_the_margin(self):
        path = self.write('{"verify-gate": {"timeoutSeconds": 1740}}')
        self.assertEqual(verify_gate.wait_limit_seconds(path), 1740 - verify_gate.WAIT_MARGIN_SECONDS)

    def test_wait_limit_falls_back_to_the_gate_default(self):
        expected = verify_gate.DEFAULT_TIMEOUT_SECONDS - verify_gate.WAIT_MARGIN_SECONDS
        for text in ('{"verify-gate": {}}', "{bad", '{"verify-gate": {"timeoutSeconds": "x"}}'):
            self.assertEqual(verify_gate.wait_limit_seconds(self.write(text)), expected, text)
        self.assertEqual(verify_gate.wait_limit_seconds("/nonexistent/harness.json"), expected)

    def test_tree_clean_reads_git_status(self):
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        self.assertTrue(verify_gate.tree_clean(repo))
        with open(os.path.join(repo, "a.txt"), "w") as handle:
            handle.write("x\n")
        self.assertFalse(verify_gate.tree_clean(repo))


class RealModule(unittest.TestCase):
    def test_the_real_verify_module_has_what_full_run_uses(self):
        verify = verify_gate.load_verify()
        for name in ("run_gate", "render", "exit_code", "load_table", "real_context", "TableError"):
            self.assertTrue(hasattr(verify, name), name)
        self.assertTrue(hasattr(verify.components, "load_map"))
        self.assertIn("use_passes", verify.run_gate.__code__.co_varnames)


if __name__ == "__main__":
    unittest.main()
