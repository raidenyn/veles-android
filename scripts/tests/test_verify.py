"""The verify CLI end to end: both gates, `--list`, the report and the exit codes (D1, D4-D10).

Every case drives `run_gate` or `main` over the repository's real table with a fake git reader,
a fake runner and a fake filesystem, except the resolver-timeout case, which needs a table shape
the real table does not have yet and writes a small fixture table. The clock, wait, runner,
git and filesystem are injected; log files and `last-run.json` go to a temporary directory, so
nothing sleeps, spawns a process, reads the wall clock or writes into the checkout.
"""
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from unittest import mock

TESTS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS)
REPO = os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, TESTS)

import branch_pass  # noqa: E402
import components  # noqa: E402
from verify import (  # noqa: E402
    GitError,
    Outcome,
    RunLock,
    exit_code,
    load_table,
    main,
    render,
    run_gate,
    run_id,
)
from verifyfake import FakeClock, FakeFs, FakeGit, FakeRunner, FakeWait, make_context  # noqa: E402

BASE = "a" * 40
EXT = "web-extension/src/background.ts"
EXT_CONFIG = "web-extension/eslint.config.js"
ANDROID_SOURCE = "android/app/src/main/java/me/nagaev/veles/RegexMessageHandler.kt"
ANDROID_TEST = "android/app/src/test/java/me/nagaev/veles/RegexMessageHandlerTest.kt"
TOOLING = "scripts/components.py"
JDK = "/fake/jdk21"
JAVA = JDK + "/bin/java"
BIN = "/fake/bin"
NDK_PIN = "26.1.10909125"
RUN = "run-1"

DEVICE_NOTE = "NOT RUN (device test; real-device rule)"


def real_table():
    cmap = components.load_map(os.path.join(REPO, ".github", "components.json"))
    return load_table(os.path.join(REPO, "scripts", "verify-commands.json"), cmap), cmap


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


class ScriptedRunner(FakeRunner):
    """A fake runner that records when each call started and can act on a chosen call.

    `on_call(clock, argv, outcome, cutoff, log_path)` may advance the injected clock and return
    a replacement `Outcome`; that models a step that reached its cutoff or a deadline that passed
    between two steps without sleeping.
    """

    def __init__(self, clock, script=None, stdout=None, on_call=None):
        super().__init__(script, stdout)
        self.clock = clock
        self.on_call = on_call
        self.started = []

    def run(self, argv, cwd, env, log_path, cutoff, capture):
        self.started.append((self.clock.now(), tuple(argv), cutoff))
        outcome = super().run(argv, cwd, env, log_path, cutoff, capture)
        if self.on_call is not None:
            replacement = self.on_call(self.clock, tuple(argv), outcome, cutoff, log_path)
            if replacement is not None:
                outcome = replacement
        return outcome


class GateFixture(unittest.TestCase):
    """The real table, a temporary `build/verify/`, and a fully provisioned fake machine."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = os.path.realpath(temporary.name)
        self.verify_dir = os.path.join(self.tmp, "build", "verify")
        self.run_dir = os.path.join(self.verify_dir, "runs", RUN)
        os.makedirs(self.run_dir)
        self.last_run_path = os.path.join(self.verify_dir, "last-run.json")
        self.clock = FakeClock(0.0)
        self.lines = []
        self.table, self.cmap = real_table()
        # A branch run writes the branch-pass record through git's worktree root; pin it to the
        # temporary directory so no test writes into the checkout it runs from.
        patcher = mock.patch.object(
            branch_pass, "record_path", return_value=os.path.join(self.tmp, "branch-pass.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- the fake machine ---------------------------------------------------------------

    def path(self, relative):
        return os.path.join(REPO, relative)

    def env(self, jdk=True, chromium=True):
        env = {"PATH": BIN, "HOME": "/home/fake"}
        if jdk:
            env["JAVA_HOME"] = JDK
        if chromium:
            env["VELES_TEST_CHROMIUM"] = "/fake/chromium"
        return env

    def environment(self, jdk=True, rustup=True, wasm=True, dist=True, android=True,
                    unit_test=True, extra=None):
        """The files a fully provisioned machine and checkout have, as `FakeFs` sees them."""
        files = {
            BIN + "/node": b"",
            BIN + "/npm": b"",
            BIN + "/npx": b"",
            BIN + "/python3": b"",
            BIN + "/cargo-deny": b"",
            BIN + "/cargo-cyclonedx": b"",
            "/fake/chromium": b"",
            self.path(".java-version"): b"21.0.12\n",
            self.path("rust/ndk-version"): (NDK_PIN + "\n").encode("utf-8"),
            self.path("verify/node_modules/.keep"): b"",
            self.path("web-extension/node_modules/.keep"): b"",
            self.path("android/local.properties"): b"sdk.dir=/fake/sdk\n",
            "/fake/sdk/platform-tools/.keep": b"",
        }
        if jdk:
            files[JAVA] = b""
            files[JDK + "/release"] = b"JAVA_VERSION=21.0.12\n"
        if rustup:
            files[BIN + "/rustup"] = b""
            files[self.path("rust/rust-toolchain.toml")] = (
                b'[toolchain]\nchannel = "1.98.0"\n')
        if wasm:
            files[self.path("web-extension/rust-wasm/pkg/package.json")] = b"{}"
        if dist:
            files[self.path("web-extension/dist/index.html")] = b""
        if android:
            files["/fake/sdk/ndk/" + NDK_PIN + "/source.properties"] = (
                ("Pkg.Revision=" + NDK_PIN + "\n").encode("utf-8"))
            files[self.path(ANDROID_TEST)] = (
                b"package me.nagaev.veles\n\nclass RegexMessageHandlerTest\n")
        if unit_test:
            files[self.path("web-extension/package.json")] = json.dumps(
                {"scripts": {"test": "vitest run test/background.test.ts"}}).encode("utf-8")
            files[self.path("web-extension/test/background.test.ts")] = b""
        files[self.path("scripts/tests/test_components.py")] = b""
        files.update(extra or {})
        return files

    def machine(self, **overrides):
        """The version-command answers of a fully provisioned machine."""
        script = {
            (JAVA, "-version"): Outcome(
                "ok", stderr=['openjdk version "21.0.12" 2024-07-16']),
            ("node", "--version"): Outcome("ok", stdout=["v22.1.0"]),
            ("npm", "--version"): Outcome("ok", stdout=["11.19.0"]),
            ("npx", "--version"): Outcome("ok", stdout=["11.19.0"]),
            ("python3", "--version"): Outcome("ok", stdout=["Python 3.11.2"]),
            (BIN + "/rustup", "show", "active-toolchain"): Outcome(
                "ok", stdout=["1.98.0-x86_64-unknown-linux-gnu (default)"]),
            ("cargo-deny", "--version"): Outcome("ok", stdout=["cargo-deny 0.16.1"]),
            ("cargo-cyclonedx", "cyclonedx", "--version"): Outcome(
                "ok", stdout=["cargo-cyclonedx-cyclonedx 0.5.7"]),
        }
        script.update(overrides)
        return script

    # -- the context --------------------------------------------------------------------

    def context(self, git=None, runner=None, files=None, deadline=None, clock=None,
                env=None, repo=None, run_dir=None):
        clock = clock or self.clock
        root = repo or REPO
        git = git or FakeGit(diff=[EXT], base=BASE)
        entries = dict(self.environment() if files is None else files)
        # A changed path has to exist for a strategy to treat it as a target rather than a
        # deleted path; the fake checkout has every changed file.
        changed = (list(getattr(git, "diff", [])) + list(getattr(git, "staged_paths", []))
                   + list(getattr(git, "unstaged_paths", []))
                   + list(getattr(git, "untracked_paths", [])))
        for relative in changed:
            entries.setdefault(os.path.join(root, relative), b"")
        return make_context(
            repo=root,
            common_dir=os.path.join(self.tmp, ".git"),
            git=git,
            runner=runner or ScriptedRunner(clock, script=self.machine()),
            clock=clock,
            wait=FakeWait(clock),
            fs=FakeFs(files=entries),
            env=self.env() if env is None else env,
            run_dir=run_dir or self.run_dir,
            deadline=deadline,
            log=self.lines.append,
        )

    def step_calls(self, runner):
        """The step invocations (probes and resolvers use `capture=True`)."""
        return [call["argv"] for call in runner.calls if not call["capture"]]

    def last_run(self):
        return read_json(self.last_run_path)

    def assert_line(self, needle):
        self.assertTrue(any(needle in line for line in self.lines),
                        "missing %r in %r" % (needle, self.lines))


class RunIdTest(unittest.TestCase):
    def test_run_id_uses_the_injected_clock(self):
        # D10: `<UTC yyyymmddThhmmss>-<pid>`; the clock is injected, so this reads no wall clock.
        identifier = run_id(FakeClock(0.0))
        self.assertTrue(identifier.startswith("19700101T000000-"), identifier)
        self.assertTrue(identifier.endswith("-%d" % os.getpid()), identifier)


class PassingRunTest(GateFixture):
    def test_passing_run_exit_0(self):
        # Spec "One verify command with two gates": a clean task gate exits 0, and the hook needs
        # a measured `elapsed_s` in last-run.json.
        runner = ScriptedRunner(self.clock, script=self.machine())
        context = self.context(runner=runner)
        self.assertEqual(main([], context), 0)
        self.assert_line("web-extension: PASS")
        self.assertNotIn("verify: FAIL", "\n".join(self.lines))
        self.assert_line("protocol: NOT RUN (nothing planned:")
        document = self.last_run()
        self.assertEqual(document["version"], 1)
        self.assertEqual(document["nodes"]["web-extension"]["status"], "PASS")
        self.assertIsInstance(document["elapsed_s"], float)
        self.assertTrue(document["steps"])

    def test_lint_failure_exit_1(self):
        # D10/spec "Failure summary": exit 1, the step, its re-run command and its log path.
        script = self.machine()
        script[("npm", "run", "lint")] = Outcome("fail", code=1, tail=["lint error"])
        runner = ScriptedRunner(self.clock, script=script)
        context = self.context(runner=runner)
        self.assertEqual(main([], context), 1)
        text = "\n".join(self.lines)
        self.assertIn("web-extension: FAIL", text)
        self.assertIn("web-extension.lint: FAIL (exit 1)", text)
        self.assertIn("cd web-extension && npm run lint", text)
        self.assertIn(os.path.join(self.run_dir, "web-extension.lint.1.log"), text)
        # The node stopped at its first failing command.
        self.assertNotIn(["npm", "run", "typecheck"], self.step_calls(runner))

    def test_missing_tool_exit_2(self):
        # Spec "Missing tool exit": every step that could run passed; the missing JDK is NOT RUN,
        # so the command exits 2, never 0, and the fix names the pin file.
        runner = ScriptedRunner(self.clock, script=self.machine())
        context = self.context(runner=runner, files=self.environment(jdk=False, rustup=False),
                               env=self.env(jdk=False))
        self.assertEqual(main([], context), 2)
        text = "\n".join(self.lines)
        self.assertIn("web-extension: NOT RUN", text)
        self.assertIn(".java-version", text)
        self.assertIn("JDK 21", text)


class ProducerTest(GateFixture):
    def test_formatting_does_not_wait_on_wasm(self):
        # Spec "Formatting does not wait on WASM": the Rust toolchain is missing, the extension's
        # format and lint checks still run, `rust.wasm` is NOT RUN with its fix, and the steps that
        # need the package do not run.
        runner = ScriptedRunner(self.clock, script=self.machine())
        context = self.context(runner=runner, files=self.environment(jdk=False, rustup=False),
                               env=self.env(jdk=False))
        outcome = run_gate("task", None, context, self.table, self.cmap)
        calls = self.step_calls(runner)
        self.assertIn(["npm", "run", "format:check"], calls)
        self.assertIn(["npm", "run", "lint"], calls)
        self.assertNotIn(["npm", "run", "typecheck"], calls)
        self.assertNotIn(["npx", "vitest", "run", "test/background.test.ts"], calls)
        failures = {step_id: entry for step_id, entry, _rerun in
                    outcome.results["web-extension"].failures}
        self.assertIn("rust.wasm", failures)
        self.assertEqual(failures["rust.wasm"].kind, "not_run")
        self.assertIn(".java-version", failures["rust.wasm"].error)
        self.assertEqual(exit_code(outcome.results, outcome.errors), 2)

    def test_producer_failure(self):
        # Spec "Producer failure": a failed `rustWasm` makes the extension FAIL naming it; the
        # checks that need no output ran, the consumers did not.
        script = self.machine()
        script[("./gradlew", "rustWasm")] = Outcome("fail", code=1, tail=["compile error"])
        runner = ScriptedRunner(self.clock, script=script)
        context = self.context(runner=runner)
        outcome = run_gate("task", None, context, self.table, self.cmap)
        result = outcome.results["web-extension"]
        self.assertEqual(result.status, "FAIL")
        self.assertIn("rust.wasm", [step_id for step_id, _outcome, _rerun in result.failures])
        calls = self.step_calls(runner)
        self.assertIn(["npm", "run", "format:check"], calls)
        self.assertIn(["npm", "run", "lint"], calls)
        self.assertNotIn(["npm", "run", "typecheck"], calls)
        self.assertNotIn(["npx", "vitest", "run", "test/background.test.ts"], calls)
        self.assertEqual(exit_code(outcome.results, outcome.errors), 1)
        text = render(outcome.results, outcome.errors)
        self.assertIn("rust.wasm: FAIL (exit 1)", text)

    def test_rebuilt_between_consumers(self):
        # Spec "Rebuilt between consumers": in --branch, every consumer carries its whole chain,
        # so the bundle suite is preceded by `rust.wasm` and `web-extension.build` again.
        runner = ScriptedRunner(self.clock, script=self.machine())
        context = self.context(runner=runner)
        outcome = run_gate("branch", None, context, self.table, self.cmap)
        calls = self.step_calls(runner)
        for consumer in (["npm", "run", "test:bundle"], ["npm", "run", "test:browser"]):
            index = calls.index(consumer)
            self.assertEqual(calls[index - 2:index],
                             [["./gradlew", "rustWasm"], ["npm", "run", "build"]])
        self.assertEqual(exit_code(outcome.results, outcome.errors), 0)

    def test_producer_without_output(self):
        # D5: a producer that succeeded but created no output leaves the consumer NOT RUN with
        # the requirement's fix.
        runner = ScriptedRunner(self.clock, script=self.machine())
        context = self.context(runner=runner, files=self.environment(wasm=False))
        outcome = run_gate("task", None, context, self.table, self.cmap)
        failures = {step_id: entry for step_id, entry, _rerun in
                    outcome.results["web-extension"].failures}
        self.assertEqual(failures["web-extension.typecheck"].kind, "not_run")
        self.assertIn("./gradlew rustWasm", failures["web-extension.typecheck"].fix)
        self.assertNotIn(["npm", "run", "typecheck"], self.step_calls(runner))
        self.assertEqual(exit_code(outcome.results, outcome.errors), 2)

    def test_producer_invocations_logged_separately(self):
        # D9/D10: each invocation has its own log, so two consumers leave two `rust.wasm` logs.
        runner = ScriptedRunner(self.clock, script=self.machine())
        context = self.context(runner=runner)
        run_gate("task", None, context, self.table, self.cmap)
        first = os.path.join(self.run_dir, "rust.wasm.1.log")
        second = os.path.join(self.run_dir, "rust.wasm.2.log")
        called = [call["log"] for call in runner.calls
                  if call["argv"] == ["./gradlew", "rustWasm"]]
        self.assertEqual(called, [first, second])
        logs = [step["log"] for step in self.last_run()["steps"] if step["id"] == "rust.wasm"]
        self.assertEqual(logs, [first, second])


class FailureIsolationTest(GateFixture):
    def test_mixed_failure_and_skip(self):
        # Spec "Mixed failure and skip": the lint check is NOT RUN without a JDK, the raw-`Log`
        # guard fails, the node is FAIL, its details keep the lint skip with its fix, exit 1.
        script = self.machine()
        script[("android/scripts/check-raw-log.sh",)] = Outcome(
            "fail", code=1, tail=["::error::android.util.Log"])
        runner = ScriptedRunner(self.clock, script=script)
        context = self.context(
            git=FakeGit(diff=[ANDROID_SOURCE], base=BASE),
            runner=runner, files=self.environment(jdk=False, rustup=False),
            env=self.env(jdk=False))
        outcome = run_gate("task", None, context, self.table, self.cmap)
        result = outcome.results["android"]
        self.assertEqual(result.status, "FAIL")
        failures = {step_id: entry for step_id, entry, _rerun in result.failures}
        self.assertEqual(failures["android.lint"].kind, "not_run")
        self.assertIn(".java-version", failures["android.lint"].error)
        self.assertIn("JDK 21", failures["android.lint"].fix)
        self.assertEqual(failures["android.raw-log"].kind, "fail")
        self.assertEqual(exit_code(outcome.results, outcome.errors), 1)
        text = render(outcome.results, outcome.errors)
        self.assertIn("android.lint: NOT RUN", text)
        self.assertIn(failures["android.lint"].fix, text)


class DeadlineTest(GateFixture):
    def test_timeout_names_finished_and_unreached(self):
        # D9: the step cut off at the reserve is a `timeout`, and every later step is
        # `not_reached`; the node is never a pass.
        deadline = 1000.0

        def on_call(clock, argv, outcome, cutoff, log_path):
            if argv == ("npm", "run", "lint"):
                clock.value = cutoff
                return Outcome("timeout", code=-15, tail=["killed"], log=log_path,
                               error="the step reached its cutoff")
            return None

        runner = ScriptedRunner(self.clock, script=self.machine(), on_call=on_call)
        context = self.context(runner=runner, deadline=deadline)
        outcome = run_gate("task", None, context, self.table, self.cmap)
        result = outcome.results["web-extension"]
        self.assertEqual(result.status, "FAIL")
        timed_out = [entry for step_id, entry, _rerun in result.failures
                     if entry.kind == "timeout"]
        self.assertEqual(len(timed_out), 1)
        self.assertEqual(exit_code(outcome.results, outcome.errors), 1)
        self.assertTrue(outcome.incomplete)
        statuses = dict((step["id"], step["status"]) for step in self.last_run()["steps"])
        self.assertEqual(statuses["web-extension.format"], "ok")
        self.assertEqual(statuses["web-extension.lint"], "timeout")
        self.assertEqual(statuses["web-extension.mac-comparisons"], "not_reached")

    def test_deadline_between_steps_not_blocking(self):
        # D9/known limit: a deadline that expires between two steps reports the later work as
        # `not_reached`, not as a blocking timeout; the exit code is unaffected and the run is
        # incomplete.
        deadline = 1000.0

        def on_call(clock, argv, outcome, cutoff, log_path):
            if argv == ("npm", "run", "format:check"):
                clock.value = deadline - 45.0 + 1.0
            return None

        runner = ScriptedRunner(self.clock, script=self.machine(), on_call=on_call)
        context = self.context(runner=runner, deadline=deadline)
        outcome = run_gate("task", None, context, self.table, self.cmap)
        self.assertEqual(outcome.problems, [])
        self.assertTrue(outcome.incomplete)
        self.assertEqual(exit_code(outcome.results, outcome.errors), 0)
        self.assertNotEqual(outcome.results["web-extension"].status, "PASS")
        statuses = dict((step["id"], step["status"]) for step in self.last_run()["steps"])
        self.assertEqual(statuses["web-extension.format"], "ok")
        self.assertEqual(statuses["web-extension.lint"], "not_reached")

    def test_no_launch_inside_reserve(self):
        # D9: no step, probe or resolver starts once `deadline - 45` has passed; every cutoff of
        # a call that did start is at or before that boundary.
        deadline = 1000.0
        reserve = deadline - 45.0

        def on_call(clock, argv, outcome, cutoff, log_path):
            if argv == ("npm", "run", "format:check"):
                clock.value = reserve + 1.0
            return None

        runner = ScriptedRunner(self.clock, script=self.machine(), on_call=on_call)
        context = self.context(runner=runner, deadline=deadline)
        run_gate("task", None, context, self.table, self.cmap)
        self.assertTrue(runner.started)
        for started, _argv, cutoff in runner.started:
            self.assertLess(started, reserve)
            self.assertLessEqual(cutoff, reserve)
        self.assertNotIn(["npm", "run", "lint"], self.step_calls(runner))

        # A run already inside the reserve launches nothing at all, not even a resolver.
        late_clock = FakeClock(reserve + 1.0)
        late = ScriptedRunner(late_clock, script=self.machine())
        context = self.context(runner=late, clock=late_clock, deadline=deadline)
        outcome = run_gate("task", None, context, self.table, self.cmap)
        self.assertEqual(late.calls, [])
        self.assertEqual(outcome.problems, [])
        self.assertTrue(outcome.incomplete)


class SelectionAndLockTest(GateFixture):
    def test_selection_error_exit_2_runs_nothing(self):
        # D3/spec "Unaccounted path": a base that cannot be resolved exits 2 with the fix and
        # runs nothing at all.
        runner = ScriptedRunner(self.clock, script=self.machine())
        git = FakeGit(errors={"merge_base": GitError("no origin/master")})
        context = self.context(git=git, runner=runner)
        self.assertEqual(main([], context), 2)
        self.assertEqual(runner.calls, [])
        text = "\n".join(self.lines)
        self.assertIn("cannot resolve the base", text)
        self.assertIn("git fetch origin master", text)
        self.assertIn("--base", text)

    def test_empty_base_is_selection_error(self):
        # Task 4 edge case: `--base ""` is a base selection error, never a fallback to
        # origin/master.
        runner = ScriptedRunner(self.clock, script=self.machine())
        context = self.context(runner=runner)
        self.assertEqual(main(["--base", ""], context), 2)
        self.assertEqual(runner.calls, [])
        self.assert_line("cannot resolve the base")

    def test_list_takes_run_lock(self):
        # D1/D9: `--list` takes the same run lock; a held lock is exit 2 and no resolver runs.
        lock = RunLock(os.path.join(self.verify_dir, "lock"))
        self.assertTrue(lock.try_acquire())
        self.addCleanup(lock.release)
        runner = ScriptedRunner(self.clock, script=self.machine())
        context = self.context(runner=runner)
        self.assertEqual(main(["--list"], context), 2)
        self.assert_line("verification already running")
        self.assertEqual(runner.calls, [])

    def test_list_selection_error(self):
        # Spec "Dry run with a selection error": exit 2, the fix, and no plan.
        git = FakeGit(errors={"merge_base": GitError("no origin/master")})
        context = self.context(git=git)
        self.assertEqual(main(["--list"], context), 2)
        self.assert_line("git fetch origin master")
        self.assertFalse(any("selected by" in line for line in self.lines), self.lines)

    def test_contended_exit_2(self):
        # D10: a second run while one holds build/verify/lock exits 2 at once.
        lock = RunLock(os.path.join(self.verify_dir, "lock"))
        self.assertTrue(lock.try_acquire())
        self.addCleanup(lock.release)
        context = self.context()
        self.assertEqual(main([], context), 2)
        self.assert_line("verification already running")


class ResolverTest(GateFixture):
    """A fixture table, because the real extension resolver never times out on its own."""

    def fixture(self):
        self.fixture_repo = os.path.join(self.tmp, "fixture")
        os.makedirs(os.path.join(self.fixture_repo, "scripts"))
        table = {
            "version": 1,
            "requirements": {},
            "producers": [],
            "nodes": {"web-extension": {"commands": [{
                "id": "web-extension.unit",
                "kind": "test",
                "cwd": "web-extension",
                "run": ["npx", "vitest", "run"],
                "doc": "scripts/AGENTS.md",
                "reuse": False,
                "related": {
                    "strategy": "files-arg",
                    "match": ["web-extension/src/"],
                    "resolve": ["resolve", "{changed}"],
                    "run": ["npx", "vitest", "run", "{targets}"],
                },
            }]}},
        }
        path = os.path.join(self.fixture_repo, "scripts", "verify-commands.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(table, handle)
        cmap = {"nodes": {"web-extension": {"paths": ["web-extension/"]}},
                "global": [], "ci-global": [], "unowned": []}
        return load_table(path, cmap), cmap

    def two_node_fixture(self):
        """An earlier node whose check can use up the reserve, then the extension node."""
        repo = os.path.join(self.tmp, "later-fixture")
        os.makedirs(os.path.join(repo, "scripts"))
        command = {"cwd": ".", "doc": "scripts/AGENTS.md", "reuse": False}
        table = {
            "version": 1,
            "requirements": {},
            "producers": [],
            "nodes": {
                "aaa": {"commands": [
                    dict(command, id="aaa.slow", kind="check", run=["slow"]),
                    dict(command, id="aaa.after", kind="check", run=["after"]),
                ]},
                "web-extension": {"commands": [{
                    "id": "web-extension.unit",
                    "kind": "test",
                    "cwd": "web-extension",
                    "run": ["npx", "vitest", "run"],
                    "doc": "scripts/AGENTS.md",
                    "reuse": False,
                    "related": {
                        "strategy": "files-arg",
                        "match": ["web-extension/src/"],
                        "resolve": ["resolve", "{changed}"],
                        "run": ["npx", "vitest", "run", "{targets}"],
                    },
                }]},
            },
        }
        path = os.path.join(repo, "scripts", "verify-commands.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(table, handle)
        cmap = {"nodes": {"aaa": {"paths": ["aaa/"]},
                          "web-extension": {"paths": ["web-extension/"]}},
                "global": [], "ci-global": [], "unowned": []}
        return load_table(path, cmap), cmap, repo

    def two_node_context(self, repo, runner, deadline=None, clock=None):
        os.makedirs(os.path.join(repo, "build", "verify", "runs", RUN))
        return self.context(
            git=FakeGit(diff=[EXT, "aaa/file.txt"], base=BASE), runner=runner,
            repo=repo, files={os.path.join(repo, EXT): b"",
                              os.path.join(repo, "aaa/file.txt"): b""},
            run_dir=os.path.join(repo, "build", "verify", "runs", RUN),
            deadline=deadline, clock=clock)

    def past_the_reserve(self, deadline):
        def on_call(clock, argv, outcome, cutoff, log_path):
            if argv == ("slow",):
                clock.value = deadline - 45.0 + 1.0
            return None
        return on_call

    def test_resolver_timeout_stays_blocking_after_the_reserve(self):
        # Important 1: the resolver was killed at its own cutoff during `plan`; an earlier node's
        # step then pushes the clock past the reserve, but the captured timeout still blocks.
        table, cmap, repo = self.two_node_fixture()
        deadline = 1000.0
        runner = ScriptedRunner(self.clock, script={
            ("resolve", "src/background.ts"): Outcome(
                "timeout", error="the step reached its cutoff"),
        }, on_call=self.past_the_reserve(deadline))
        context = self.two_node_context(repo, runner, deadline=deadline)
        outcome = run_gate("task", None, context, table, cmap)
        self.assertEqual(exit_code(outcome.results, outcome.errors), 1)
        self.assertIn(("web-extension", "web-extension.unit", "timeout"), outcome.problems)
        self.assertTrue(outcome.incomplete)
        self.assertNotIn(["npx", "vitest", "run"], self.step_calls(runner))

    def test_resolver_failure_stays_blocking_after_the_reserve(self):
        # Important 1: a resolver that ran and failed is an unexpected NOT RUN (exit 2), even
        # when a later node reads its result after the reserve has passed.
        table, cmap, repo = self.two_node_fixture()
        deadline = 1000.0
        runner = ScriptedRunner(self.clock, script={
            ("resolve", "src/background.ts"): Outcome(
                "not_run", error="vitest is not installed"),
        }, on_call=self.past_the_reserve(deadline))
        context = self.two_node_context(repo, runner, deadline=deadline)
        outcome = run_gate("task", None, context, table, cmap)
        self.assertEqual(exit_code(outcome.results, outcome.errors), 2)
        self.assertIn(("web-extension", "web-extension.unit", "not_run"), outcome.problems)
        self.assertTrue(outcome.incomplete)

    def test_resolver_not_started_in_reserve_is_not_blocking(self):
        # The other case: resolution itself never started because the plan ran inside the
        # reserve. That is `not_reached`, reported and incomplete, but not a problem.
        table, cmap, repo = self.two_node_fixture()
        deadline = 1000.0
        clock = FakeClock(deadline - 45.0 + 1.0)
        runner = ScriptedRunner(clock)
        context = self.two_node_context(repo, runner, deadline=deadline, clock=clock)
        outcome = run_gate("task", None, context, table, cmap)
        self.assertEqual(runner.calls, [])
        self.assertEqual(outcome.problems, [])
        self.assertEqual(exit_code(outcome.results, outcome.errors), 0)
        self.assertTrue(outcome.incomplete)
        document = read_json(os.path.join(repo, "build", "verify", "last-run.json"))
        statuses = dict((step["id"], step["status"]) for step in document["steps"])
        self.assertEqual(statuses["web-extension.unit"], "not_reached")

    def test_resolver_uses_effective_env(self):
        # D8: the list-only resolver is a command the verifier runs, so the machine config's
        # `path_prepend` applies to it too.
        table, cmap = self.fixture()
        files = {
            os.path.join(self.tmp, ".git", "veles-verify", "tools.json"): json.dumps(
                {"version": 1, "path_prepend": "/fake/prepend"}).encode("utf-8"),
            os.path.join(self.fixture_repo, EXT): b"",
            os.path.join(self.fixture_repo, "web-extension/test/background.test.ts"): b"",
        }
        runner = ScriptedRunner(self.clock, script={
            ("resolve", "src/background.ts"): Outcome(
                "ok", stdout=["test/background.test.ts"]),
        })
        os.makedirs(os.path.join(self.fixture_repo, "build", "verify", "runs", RUN))
        context = self.context(
            git=FakeGit(diff=[EXT], base=BASE), runner=runner,
            repo=self.fixture_repo,
            run_dir=os.path.join(self.fixture_repo, "build", "verify", "runs", RUN),
            files=files)
        outcome = run_gate("task", None, context, table, cmap)
        self.assertTrue(runner.calls)
        path = runner.calls[0]["env"]["PATH"]
        self.assertTrue(path.startswith("/fake/prepend" + os.pathsep), path)
        self.assertIn(["npx", "vitest", "run", "test/background.test.ts"],
                      self.step_calls(runner))
        self.assertEqual(exit_code(outcome.results, outcome.errors), 0)

    def test_resolver_timeout_end_to_end(self):
        # D4/D5: a resolver the runner kills at its cutoff is a `timeout` problem, exits 1, and
        # the narrowed vitest call never happens.
        table, cmap = self.fixture()
        runner = ScriptedRunner(self.clock, script={
            ("resolve", "src/background.ts"): Outcome(
                "timeout", error="the step reached its cutoff"),
        })
        os.makedirs(os.path.join(self.fixture_repo, "build", "verify", "runs", RUN))
        context = self.context(
            git=FakeGit(diff=[EXT], base=BASE), runner=runner,
            repo=self.fixture_repo,
            run_dir=os.path.join(self.fixture_repo, "build", "verify", "runs", RUN),
            files={os.path.join(self.fixture_repo, EXT): b""})
        outcome = run_gate("task", None, context, table, cmap)
        self.assertEqual(exit_code(outcome.results, outcome.errors), 1)
        self.assertEqual(outcome.problems[0], ("web-extension", "web-extension.unit", "timeout"))
        self.assertNotIn(["npx", "vitest", "run"], self.step_calls(runner))


class OutputTest(GateFixture):
    def test_device_note_in_output(self):
        # Spec "Device test outside both gates": the device command is a note, not a run, and it
        # does not change the exit code.
        runner = ScriptedRunner(self.clock, script=self.machine())
        context = self.context(git=FakeGit(diff=[ANDROID_SOURCE], base=BASE), runner=runner)
        self.assertEqual(main([], context), 0)
        text = "\n".join(self.lines)
        self.assertIn("android: PASS", text)
        self.assertIn("  " + DEVICE_NOTE, text)
        self.assertNotIn("connectedDebugAndroidTest", self.step_calls(runner))

    def test_sdk_dir_warning_in_run(self):
        # D8: when local.properties names a different SDK from the effective one, the android
        # node's notes carry the warning (the resolver still uses Gradle's choice).
        files = self.environment(extra={
            "/fake/other-sdk/platform-tools/.keep": b"",
            "/fake/other-sdk/ndk/" + NDK_PIN + "/source.properties":
                ("Pkg.Revision=" + NDK_PIN + "\n").encode("utf-8"),
        })
        env = self.env()
        env["ANDROID_HOME"] = "/fake/other-sdk"
        runner = ScriptedRunner(self.clock, script=self.machine())
        context = self.context(git=FakeGit(diff=[ANDROID_SOURCE], base=BASE),
                               runner=runner, files=files, env=env)
        self.assertEqual(main([], context), 0)
        self.assert_line("android/local.properties sets sdk.dir=/fake/sdk")


class CliScenarioTest(GateFixture):
    def test_task_gate_narrows_tests(self):
        # Spec "Task gate narrows tests": `scripts/components.py` runs its own test file, not the
        # whole scripts suite.
        runner = ScriptedRunner(self.clock, script=self.machine())
        context = self.context(git=FakeGit(diff=[TOOLING], base=BASE), runner=runner)
        self.assertEqual(main([], context), 0)
        calls = self.step_calls(runner)
        self.assertIn(["python3", "-m", "unittest", "scripts/tests/test_components.py"], calls)
        self.assertNotIn(["./gradlew", "scriptsTest"], calls)

    def test_branch_gate_runs_every_linux_test(self):
        # Spec "Branch gate runs every Linux test": the same branch runs `./gradlew scriptsTest`
        # and the node test files.
        runner = ScriptedRunner(self.clock, script=self.machine())
        context = self.context(git=FakeGit(diff=[TOOLING], base=BASE), runner=runner)
        self.assertEqual(main(["--branch"], context), 0)
        calls = self.step_calls(runner)
        self.assertIn(["./gradlew", "scriptsTest"], calls)
        self.assertIn(["node", "--test", "scripts/check-mac-comparisons.test.mjs"], calls)
        self.assertNotIn(["python3", "-m", "unittest", "scripts/tests/test_components.py"], calls)

    def test_nothing_changed(self):
        # Spec "Nothing changed": the base is named, no node line is printed, exit 0.
        context = self.context(git=FakeGit(diff=[], base=BASE))
        self.assertEqual(main([], context), 0)
        self.assert_line("verify: nothing changed against " + BASE)
        self.assertFalse(any(": PASS" in line or ": FAIL" in line for line in self.lines),
                         self.lines)

    def test_no_related_test(self):
        # Spec "No related test": the checks run, the tests do not, and the node lists the files
        # that had no related test.
        runner = ScriptedRunner(self.clock, script=self.machine())
        context = self.context(git=FakeGit(diff=[EXT_CONFIG], base=BASE), runner=runner)
        self.assertEqual(main([], context), 0)
        self.assert_line("tests: none related (web-extension/eslint.config.js)")
        self.assertFalse(any("vitest" in argv for argv in self.step_calls(runner)),
                         self.step_calls(runner))


if __name__ == "__main__":
    unittest.main()
