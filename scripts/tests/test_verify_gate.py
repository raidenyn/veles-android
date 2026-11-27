"""The verify gate's command: an edit lints one file, every other trigger execs the task gate."""
import io
import json
import subprocess
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
        self.assertEqual(self.argvs("rust/a.rs"), ([["rustfmt", "--check", "--edition", "2024", "rust/a.rs"]], 0))
        self.assertEqual(self.argvs("scripts/x.py"), ([], 1))
        self.assertEqual(self.argvs("docs.json"), ([], 1))

    def test_vendored_code_is_not_linted(self):
        for rel in ("native-bridge/host/vendor/bluest/src/lib.rs", "vendor/a.py", "x/vendor/b.sh"):
            self.assertEqual(self.argvs(rel), ([], 0), rel)

    def test_files_without_a_single_file_lint(self):
        for rel in ("android/app/src/main/A.kt", "docs/verify.md", "build.gradle.kts"):
            self.assertEqual(self.argvs(rel), ([], 0), rel)


class EditFilesConfig(unittest.TestCase):
    """`verify-gate.editFiles` and the kinds `edit_checks` lints must not drift apart."""

    def globs(self):
        path = os.path.join(os.path.dirname(SCRIPTS), ".agents", "harness.json")
        with open(path) as handle:
            return json.load(handle)["verify-gate"]["editFiles"]

    def test_the_configured_globs_match_the_edit_checks(self):
        self.assertEqual(self.globs(), verify_gate.edit_globs())

    def test_only_the_glob_kinds_reach_a_lint(self):
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo)
        for glob in self.globs():
            rel = glob.replace("**/", "src/").replace("*", "x")
            path = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write("#!/bin/sh\n")
        with mock.patch.object(verify_gate, "REPO", repo):
            for glob in self.globs():
                rel = glob.replace("**/", "src/").replace("*", "x")
                with self.subTest(glob=glob):
                    self.assertTrue(any(verify_gate.edit_checks(rel)), rel)


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
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = verify_gate.lint(rel)
        return status, out.getvalue() + err.getvalue()

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

    def test_missing_package_linter_is_skipped_silently(self):
        self.write("web-extension/src/a.ts", "export {}\n")
        with mock.patch.object(verify_gate.subprocess, "run") as run:
            self.assertEqual(self.lint("web-extension/src/a.ts"), (0, ""))
        run.assert_not_called()

    def test_failing_command_fails(self):
        self.write("a.sh", "echo\n")
        with mock.patch.object(verify_gate.subprocess, "run", return_value=mock.Mock(returncode=2)) as run:
            self.assertEqual(self.lint("a.sh")[0], 1)
        run.assert_called_once_with(["sh", "-n", "a.sh"], cwd=self.repo)


class Shells(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo)
        patcher = mock.patch.object(verify_gate, "REPO", self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)

    def shell(self, first_line):
        path = os.path.join(self.repo, "s.sh")
        with open(path, "w") as handle:
            handle.write(first_line + "echo\n")
        return verify_gate.shell_of(path)

    def test_the_shebang_picks_the_shell(self):
        cases = {
            "": "sh",
            "#!/bin/sh\n": "sh",
            "#!/bin/dash\n": "sh",
            "#!/bin/bash\n": "bash",
            "#!/usr/bin/env bash\n": "bash",
            "#!/usr/bin/env -S bash -e\n": "bash",
            "#!/bin/zsh\n": "zsh",
            "#!/usr/bin/python3\n": None,
            "#!\n": "sh",
        }
        for first_line, expected in cases.items():
            self.assertEqual(self.shell(first_line), expected, first_line)

    def test_a_bash_script_is_checked_by_bash(self):
        self.shell("#!/usr/bin/env bash\n")
        commands, _ = verify_gate.edit_checks("s.sh")
        self.assertEqual([argv for _, argv in commands], [["bash", "-n", "s.sh"]])

    def test_an_unknown_interpreter_is_skipped(self):
        self.shell("#!/usr/bin/python3\n")
        self.assertEqual(verify_gate.edit_checks("s.sh"), ([], []))

    def test_real_bash_syntax_passes_under_bash(self):
        path = os.path.join(self.repo, "b.sh")
        with open(path, "w") as handle:
            handle.write("#!/usr/bin/env bash\nif [[ -n x ]]; then a=(1 2); echo ${a[@]}; fi\n")
        self.assertEqual(verify_gate.lint("b.sh"), 0)

    def test_a_json_file_with_a_bom_passes(self):
        with open(os.path.join(self.repo, "a.json"), "wb") as handle:
            handle.write(b"\xef\xbb\xbf{}")
        self.assertEqual(verify_gate.lint("a.json"), 0)


class Main(unittest.TestCase):
    def test_edit_lints_the_file(self):
        with mock.patch.object(verify_gate, "lint", return_value=1) as lint:
            self.assertEqual(verify_gate.main({"VERIFY_TRIGGER": "edit", "VERIFY_FILE": "a.py"}), 1)
        lint.assert_called_once_with("a.py")

    def test_other_triggers_run_the_task_gate(self):
        fake = mock.Mock()
        for env in ({"VERIFY_TRIGGER": "task"}, {"VERIFY_TRIGGER": "turn-end"}, {}):
            with mock.patch.object(verify_gate, "load_verify", return_value=fake), \
                    mock.patch.object(verify_gate, "gate_timeout_seconds", return_value=7), \
                    mock.patch.object(verify_gate, "full_run", return_value=0) as run:
                self.assertEqual(verify_gate.main(env), 0)
            run.assert_called_once_with(fake, fake.real_context.return_value, 7)


class Outcome:
    def __init__(self, contended=False, errors=(), selection=None, incomplete=False):
        self.contended = contended
        self.errors = list(errors)
        self.results = {}
        self.selection = selection
        self.incomplete = incomplete


CONTENDED = "verification already running"
ORPHANS = "a previous verification's process group could not be stopped"


class FakeVerify:
    """`run_gate` answers from a list of outcomes; the rest is what `full_run` reads."""

    TableError = ValueError
    CONTENTION = CONTENDED

    def __init__(self, outcomes, code=0, proof=False):
        self.outcomes = list(outcomes)
        self.calls = []
        self.code = code
        self.components = mock.Mock()
        self.load_table = mock.Mock(return_value="table")
        self.branch_pass = mock.Mock()
        self.branch_pass.check.return_value = (proof, "message")

    def run_gate(self, gate, base, ctx, table, cmap, use_passes=False):
        self.calls.append((gate, base, use_passes, ctx.deadline))
        return self.outcomes.pop(0)

    def render(self, results, errors):
        return "node: PASS"

    def exit_code(self, results, errors):
        return self.code


class Ctx:
    """An injected clock that moves only when `wait.sleep` is called."""

    def __init__(self):
        self.repo = "/r"
        self.now = 1000.0
        self.lines = []
        self.deadline = None
        self.clock = mock.Mock(now=lambda: self.now)
        self.wait = mock.Mock(sleep=self.sleep)
        self.log = self.lines.append

    def sleep(self, seconds):
        self.now += seconds


class FullRun(unittest.TestCase):
    def run_full(self, fake, ctx, timeout=160, clean=lambda repo: False):
        return verify_gate.full_run(fake, ctx, timeout, clean=clean)

    def test_a_dirty_tree_reads_passes_under_a_deadline(self):
        fake, ctx = FakeVerify([Outcome()], code=1), Ctx()
        self.assertEqual(self.run_full(fake, ctx), 1)
        budget = 160 - verify_gate.WAIT_MARGIN_SECONDS
        self.assertEqual(fake.calls, [("task", None, True, 1000.0 + budget)])
        self.assertEqual(ctx.lines, ["node: PASS"])
        fake.branch_pass.check.assert_not_called()

    def test_a_clean_tree_is_the_plain_run_with_no_deadline(self):
        fake, ctx = FakeVerify([Outcome()]), Ctx()
        self.assertEqual(self.run_full(fake, ctx, clean=lambda repo: True), 0)
        self.assertEqual(fake.calls, [("task", None, False, None)])

    def test_a_clean_tree_already_proven_runs_nothing(self):
        fake, ctx = FakeVerify([], proof=True), Ctx()
        self.assertEqual(self.run_full(fake, ctx, clean=lambda repo: True), 0)
        self.assertEqual(fake.calls, [])
        self.assertIn("already covers HEAD", ctx.lines[0])

    def test_a_proof_check_that_raises_is_no_proof(self):
        fake, ctx = FakeVerify([Outcome()]), Ctx()
        fake.branch_pass.check.side_effect = RuntimeError("boom")
        self.assertEqual(self.run_full(fake, ctx, clean=lambda repo: True), 0)
        self.assertEqual(len(fake.calls), 1)

    def test_a_held_lock_is_waited_for_then_runs(self):
        busy = Outcome(contended=True, errors=[CONTENDED])
        fake, ctx = FakeVerify([busy, busy, Outcome()]), Ctx()
        self.assertEqual(self.run_full(fake, ctx), 0)
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(ctx.now, 1000.0 + 2 * verify_gate.POLL_SECONDS)

    def test_the_tree_is_read_again_after_the_wait(self):
        # The waited-for run was a manual run on the now clean tree, and it wrote the proof.
        states = iter([False, True])
        busy = Outcome(contended=True, errors=[CONTENDED])
        fake, ctx = FakeVerify([busy], proof=True), Ctx()
        self.assertEqual(self.run_full(fake, ctx, clean=lambda repo: next(states)), 0)
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("already covers HEAD", ctx.lines[-1])

    def test_the_wait_takes_at_most_half_of_the_budget(self):
        busy = [Outcome(contended=True, errors=[CONTENDED]) for _ in range(100)]
        fake, ctx = FakeVerify(busy), Ctx()
        self.assertEqual(self.run_full(fake, ctx, timeout=160), 2)
        half = (160 - verify_gate.WAIT_MARGIN_SECONDS) / 2.0
        self.assertLessEqual(ctx.now - 1000.0, half + verify_gate.POLL_SECONDS)
        self.assertGreaterEqual(ctx.now - 1000.0, half)
        self.assertEqual(ctx.lines, ["verify: " + CONTENDED])

    def test_an_orphan_that_cannot_be_stopped_is_not_waited_for(self):
        fake, ctx = FakeVerify([Outcome(contended=True, errors=[ORPHANS])]), Ctx()
        self.assertEqual(self.run_full(fake, ctx), 2)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(ctx.now, 1000.0)
        self.assertEqual(ctx.lines, ["verify: " + ORPHANS])

    def test_a_run_cut_short_by_its_deadline_fails(self):
        fake, ctx = FakeVerify([Outcome(incomplete=True)]), Ctx()
        self.assertEqual(self.run_full(fake, ctx), 2)
        self.assertIn("stopped at its deadline", ctx.lines[-1])

    def test_a_failure_wins_over_an_incomplete_run(self):
        fake, ctx = FakeVerify([Outcome(incomplete=True)], code=1), Ctx()
        self.assertEqual(self.run_full(fake, ctx), 1)

    def test_nothing_changed_passes_with_its_line(self):
        fake, ctx = FakeVerify([Outcome(selection=mock.Mock(files=[], base="abc"))]), Ctx()
        self.assertEqual(self.run_full(fake, ctx), 0)
        self.assertEqual(ctx.lines, ["verify: nothing changed against abc"])

    def test_an_invalid_table_fails(self):
        fake, ctx = FakeVerify([]), Ctx()
        fake.load_table.side_effect = ValueError("bad table")
        self.assertEqual(self.run_full(fake, ctx), 2)
        self.assertEqual(ctx.lines, ["verify: bad table"])


class Settings(unittest.TestCase):
    def write(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.addCleanup(os.unlink, handle.name)
        handle.write(text)
        handle.close()
        return handle.name

    def test_the_gate_timeout_is_read(self):
        self.assertEqual(verify_gate.gate_timeout_seconds(self.write('{"verify-gate": {"timeoutSeconds": 1740}}')), 1740)

    def test_the_gate_timeout_falls_back_to_the_gate_default(self):
        expected = verify_gate.DEFAULT_TIMEOUT_SECONDS
        for text in ('{"verify-gate": {}}', "{bad", '{"verify-gate": {"timeoutSeconds": "x"}}',
                     '{"verify-gate": {"timeoutSeconds": 0}}'):
            self.assertEqual(verify_gate.gate_timeout_seconds(self.write(text)), expected, text)
        self.assertEqual(verify_gate.gate_timeout_seconds("/nonexistent/harness.json"), expected)

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
        for name in ("run_gate", "render", "exit_code", "load_table", "real_context", "TableError",
                     "CONTENTION", "ORPHAN_CONTENTION"):
            self.assertTrue(hasattr(verify, name), name)
        self.assertTrue(hasattr(verify.components, "load_map"))
        self.assertTrue(hasattr(verify.branch_pass, "check"))
        self.assertIn("use_passes", verify.run_gate.__code__.co_varnames)
        self.assertIn("incomplete", verify.RunOutcome.__dataclass_fields__)
        self.assertNotEqual(verify.CONTENTION, verify.ORPHAN_CONTENTION)


if __name__ == "__main__":
    unittest.main()
