"""Remembered passes (design D5, D6): the fingerprint, generated snapshots and passes.json.

Every case injects the clock, the wait, the git reader, the runner and the filesystem. The table
is a small fixture whose only node is `web-extension`, so a changed `web-extension/src/` path
selects exactly that node; the component map used by the fingerprint's owner filter is the
repository's real `.github/components.json`. Nothing sleeps, spawns a real process or reads the
wall clock.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

TESTS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS)
REPO = os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, TESTS)

import branch_pass  # noqa: E402
import components  # noqa: E402
from verify import (  # noqa: E402
    FingerprintInput,
    NodePlan,
    Outcome,
    Passes,
    PlannedStep,
    Step,
    fingerprint,
    load_table,
    run_gate,
    snapshot_tree,
)
from verifyfake import FakeClock, FakeFs, FakeGit, FakeRunner, FakeStat, FakeWait, make_context  # noqa: E402

BASE = "a" * 40
EXT = "web-extension/src/background.ts"
TEST_FILE = "web-extension/test/background.test.ts"
PKG = "web-extension/rust-wasm/pkg"
PKG_FILE = PKG + "/package.json"
NODE_MODULES = "web-extension/node_modules"
CLEAN = "web-extension/src/clean.ts"
JDK = "/fake/jdk21"
JAVA = JDK + "/bin/java"
BIN = "/fake/bin"
RUN = "run-1"

GRADLE_HOME = [{"path": "{gradle_user_home}/gradle.properties"},
               {"path": "{gradle_user_home}/init.d/"}]
NODE_MODULES_STAT = [{"path": NODE_MODULES, "mode": "stat"}]


def fixture_table():
    """A small valid table: two checks and one related test, one generated output."""
    return {
        "version": 1,
        "requirements": {
            "java-home": {
                "kind": "tool", "resolver": "java-home", "args": {},
                "fix": "install a JDK 21",
            },
            "node": {
                "kind": "tool", "resolver": "path-tool",
                "args": {"program": "node", "version_cmd": ["node", "--version"], "min": "22"},
                "fix": "install Node 22",
            },
            "npm": {
                "kind": "tool", "resolver": "path-tool",
                "args": {"program": "npm", "version_cmd": ["npm", "--version"]},
                "fix": "install npm",
            },
            "web-extension.node-modules": {
                "kind": "path", "path": NODE_MODULES, "fix": "run npm ci",
            },
            "rust.wasm": {
                "kind": "generated", "path": PKG, "producer": "rust.wasm",
                "fix": "run ./gradlew rustWasm",
            },
        },
        "producers": [{
            "id": "rust.wasm", "cwd": ".", "run": ["./gradlew", "rustWasm"],
            "doc": "scripts/AGENTS.md", "requires": ["java-home"], "inputs": GRADLE_HOME,
        }],
        "nodes": {
            "web-extension": {"commands": [
                {
                    "id": "web-extension.lint", "kind": "check", "cwd": "web-extension",
                    "run": ["npm", "run", "lint"], "doc": "scripts/AGENTS.md",
                    "requires": ["node", "npm", "web-extension.node-modules"],
                    "inputs": NODE_MODULES_STAT,
                },
                {
                    "id": "web-extension.typecheck", "kind": "check", "cwd": "web-extension",
                    "run": ["npm", "run", "typecheck"], "doc": "scripts/AGENTS.md",
                    "requires": ["node", "npm", "web-extension.node-modules", "rust.wasm"],
                    "inputs": NODE_MODULES_STAT,
                },
                {
                    "id": "web-extension.unit", "kind": "test", "cwd": "web-extension",
                    "run": ["npm", "test"], "doc": "scripts/AGENTS.md",
                    "requires": ["node", "npm", "web-extension.node-modules", "rust.wasm"],
                    "inputs": NODE_MODULES_STAT,
                    "related": {
                        "strategy": "name-map",
                        "sources": "web-extension/src/**/*.ts",
                        "tests": "web-extension/test/**/<stem>.test.ts",
                        "run": ["npm", "test", "{targets}"],
                    },
                },
            ]},
        },
    }


def fixture_cmap():
    return {"nodes": {"web-extension": {"paths": ["web-extension/"]}},
            "global": [], "ci-global": [], "unowned": []}


def real_table():
    """The repository's real table, for the producer-input cases."""
    cmap = components.load_map(os.path.join(REPO, ".github", "components.json"))
    return load_table(os.path.join(REPO, "scripts", "verify-commands.json"), cmap)


class ScriptedRunner(FakeRunner):
    """A fake runner whose `on_call` can advance the clock or mutate the fixture mid-run."""

    def __init__(self, clock, script=None, stdout=None, on_call=None):
        super().__init__(script, stdout)
        self.clock = clock
        self.on_call = on_call

    def run(self, argv, cwd, env, log_path, cutoff, capture):
        outcome = super().run(argv, cwd, env, log_path, cutoff, capture)
        if self.on_call is not None:
            replacement = self.on_call(self.clock, tuple(argv), outcome, cutoff, log_path)
            if replacement is not None:
                outcome = replacement
        return outcome


class UnreadableFs(FakeFs):
    """A filesystem where one named file cannot be read (design D6: no hit, never a crash)."""

    def __init__(self, unreadable, **kwargs):
        super().__init__(**kwargs)
        self.unreadable = unreadable

    def read_bytes(self, path):
        if self._norm(path).endswith(self.unreadable):
            raise OSError("permission denied")
        return super().read_bytes(path)


class FingerprintFixture(unittest.TestCase):
    """The fixture table, a temporary `build/verify/` and a provisioned fake machine."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = os.path.realpath(temporary.name)
        self.verify_dir = os.path.join(self.tmp, "build", "verify")
        self.run_dir = os.path.join(self.verify_dir, "runs", RUN)
        os.makedirs(self.run_dir)
        self.passes_path = os.path.join(self.verify_dir, "passes.json")
        self.last_run_path = os.path.join(self.verify_dir, "last-run.json")
        self.clock = FakeClock(0.0)
        self.lines = []
        self.table = self.load_fixture_table()
        # A branch run writes the branch-pass record through git's worktree root; pin it here.
        patcher = mock.patch.object(
            branch_pass, "record_path", return_value=os.path.join(self.tmp, "branch-pass.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def load_fixture_table(self):
        root = os.path.join(self.tmp, "table")
        os.makedirs(os.path.join(root, "scripts"))
        path = os.path.join(root, "scripts", "verify-commands.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(fixture_table(), handle)
        return load_table(path, fixture_cmap())

    # -- the fake machine ---------------------------------------------------------------

    def path(self, relative):
        return os.path.join(REPO, relative)

    def env(self):
        return {"PATH": BIN, "HOME": "/home/fake", "JAVA_HOME": JDK}

    def environment(self, jdk=True, pkg=b'{"name":"pkg"}\n'):
        files = {
            BIN + "/node": b"",
            BIN + "/npm": b"",
            self.path(".java-version"): b"21.0.12\n",
            self.path(EXT): b"export const background = 1;\n",
            self.path(TEST_FILE): b"",
            self.path(NODE_MODULES + "/.keep"): b"",
            self.path(PKG_FILE): pkg,
            self.path("rust/veles-crypto/src/lib.rs"): b"// rust\n",
        }
        if jdk:
            files[JAVA] = b""
            files[JDK + "/release"] = b"JAVA_VERSION=21.0.12\n"
        return files

    def machine(self):
        return {
            (JAVA, "-version"): Outcome(
                "ok", stderr=['openjdk version "21.0.12" 2024-07-16']),
            ("node", "--version"): Outcome("ok", stdout=["v22.1.0"]),
            ("npm", "--version"): Outcome("ok", stdout=["11.19.0"]),
        }

    def context(self, git=None, runner=None, files=None, links=None, stats=None, env=None,
                deadline=None, clock=None):
        clock = clock or self.clock
        return make_context(
            repo=REPO,
            common_dir=os.path.join(self.tmp, ".git"),
            git=git or FakeGit(diff=[EXT], base=BASE),
            runner=runner or ScriptedRunner(clock, script=self.machine()),
            clock=clock,
            wait=FakeWait(clock),
            fs=FakeFs(files=self.environment() if files is None else files,
                      links=links, stats=stats),
            env=self.env() if env is None else env,
            run_dir=self.run_dir,
            deadline=deadline,
            log=self.lines.append,
        )

    # -- the fingerprint input ----------------------------------------------------------

    def fingerprint_input(self, node_plan=None, changed=None, base=BASE, env=None,
                          config_digest=None, resolved=None, local_properties=None,
                          generated=None, entry="entry", node="web-extension", gate="task"):
        return FingerprintInput(
            gate=gate,
            node=node,
            base=base,
            changed=list([EXT] if changed is None else changed),
            node_plan=node_plan or NodePlan(steps=[]),
            entry_digest=entry,
            env=dict(self.env() if env is None else env),
            config_digest=config_digest,
            resolved=dict(resolved or {}),
            local_properties=local_properties,
            generated=dict(generated or {}),
        )

    @staticmethod
    def plan_with(*steps):
        return NodePlan(steps=[PlannedStep(step=step, argv=list(step.run)) for step in steps])

    def read_passes(self):
        try:
            with open(self.passes_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def seed_pass(self, digest, gate="task", node="web-extension"):
        passes = Passes(self.passes_path)
        passes.put(gate, node, digest)
        return passes

    def step_calls(self, runner):
        return [call["argv"] for call in runner.calls if not call["capture"]]


class FingerprintKeyTest(FingerprintFixture):
    def test_key_changes_on_edit(self):
        # D6 `files`: a changed selecting path contributes its working-tree content.
        first = self.context()
        second = self.context(files=self.environment())
        second.fs.files[self.path(EXT)] = b"export const background = 2;\n"
        fi = self.fingerprint_input()
        self.assertNotEqual(fingerprint(fi, first), fingerprint(fi, second))

    def test_key_changes_on_base(self):
        ctx = self.context()
        self.assertNotEqual(fingerprint(self.fingerprint_input(base=BASE), ctx),
                            fingerprint(self.fingerprint_input(base="b" * 40), ctx))

    def test_key_changes_on_config(self):
        ctx = self.context()
        self.assertNotEqual(
            fingerprint(self.fingerprint_input(config_digest="a" * 64), ctx),
            fingerprint(self.fingerprint_input(config_digest="b" * 64), ctx))

    def test_key_changes_on_tool_version(self):
        ctx = self.context()
        self.assertNotEqual(
            fingerprint(self.fingerprint_input(
                resolved={"node": "/fake/bin/node@v22.1.0"}), ctx),
            fingerprint(self.fingerprint_input(
                resolved={"node": "/fake/bin/node@v22.2.0"}), ctx))

    def test_fingerprint_unreadable_file_is_no_hit(self):
        # D6: a read that cannot complete is "no hit", never an error or a pass.
        ctx = self.context()
        ctx.fs = UnreadableFs("background.ts", files=self.environment())
        self.assertIsNone(fingerprint(self.fingerprint_input(), ctx))


class SnapshotTest(FingerprintFixture):
    def snapshot(self, ctx, relative=PKG):
        return snapshot_tree(self.path(relative), ctx)

    def test_removed_output_is_missing_requirement(self):
        ctx = self.context()
        digest = self.snapshot(ctx)
        self.assertIsNotNone(digest)
        removed = self.context(files={
            name: data for name, data in self.environment().items()
            if not name.startswith(self.path(PKG) + "/")})
        self.assertIsNone(self.snapshot(removed))
        fi = self.fingerprint_input(generated={PKG: None})
        self.assertIsNone(fingerprint(fi, removed))

    def test_replaced_package_invalidates(self):
        first = self.context()
        second = self.context(files=self.environment(pkg=b'{"name":"other"}\n'))
        one, two = self.snapshot(first), self.snapshot(second)
        self.assertNotEqual(one, two)
        self.assertNotEqual(
            fingerprint(self.fingerprint_input(generated={PKG: one}), first),
            fingerprint(self.fingerprint_input(generated={PKG: two}), second))

    def test_link_in_output_not_reusable(self):
        # D6: a symlink or special file anywhere in the output makes the node non-reusable.
        ctx = self.context(links={self.path(PKG + "/bad"): "/etc/passwd"})
        self.assertIsNone(self.snapshot(ctx))
        self.assertIsNone(fingerprint(
            self.fingerprint_input(generated={PKG: None}), ctx))


class InputTest(FingerprintFixture):
    def stat_plan(self):
        return self.plan_with(Step(
            id="web-extension.lint", kind="check", cwd="web-extension",
            run=["npm", "run", "lint"], inputs=[{"path": NODE_MODULES, "mode": "stat"}]))

    def test_edited_dependency_ctime_invalidates(self):
        # D6 `inputs` stat mode: the status-change time of every file and directory counts.
        dependency = self.path(NODE_MODULES + "/a.js")
        files = self.environment()
        files[dependency] = b"module.exports = 1;\n"
        first = self.context(
            files=files, stats={dependency: FakeStat(mode=0o100644, size=20, ctime_ns=1)})
        second = self.context(
            files=files, stats={dependency: FakeStat(mode=0o100644, size=20, ctime_ns=2)})
        fi = self.fingerprint_input(node_plan=self.stat_plan())
        self.assertNotEqual(fingerprint(fi, first), fingerprint(fi, second))

    def gradle_plan(self):
        return self.plan_with(Step(
            id="web-extension.lint", kind="check", cwd="web-extension",
            run=["npm", "run", "lint"],
            inputs=[{"path": "{gradle_user_home}/gradle.properties"}]))

    def test_gradle_user_home_resolved(self):
        # D6: `{gradle_user_home}` resolves as Gradle does: GRADLE_USER_HOME, else $HOME/.gradle.
        files = self.environment()
        files["/fake/gradle/gradle.properties"] = b"org.gradle.parallel=true\n"
        files["/home/fake/.gradle/gradle.properties"] = b"org.gradle.caching=true\n"
        explicit = self.context(files=files, env={
            "PATH": BIN, "HOME": "/home/fake", "JAVA_HOME": JDK,
            "GRADLE_USER_HOME": "/fake/gradle"})
        fallback = self.context(files=files, env={
            "PATH": BIN, "HOME": "/home/fake", "JAVA_HOME": JDK})
        fi = self.fingerprint_input(node_plan=self.gradle_plan(), env={
            "PATH": BIN, "HOME": "/home/fake", "JAVA_HOME": JDK,
            "GRADLE_USER_HOME": "/fake/gradle"})
        self.assertIsNotNone(fingerprint(fi, explicit))
        changed = self.context(files=dict(files), env=explicit.env)
        changed.fs.files["/fake/gradle/gradle.properties"] = b"org.gradle.parallel=false\n"
        self.assertNotEqual(fingerprint(fi, explicit), fingerprint(fi, changed))
        self.assertIsNotNone(fingerprint(
            self.fingerprint_input(node_plan=self.gradle_plan(),
                                   env={"PATH": BIN, "HOME": "/home/fake"}), fallback))

    def test_gradle_home_in_options_not_reusable(self):
        # D6: GRADLE_OPTS or JAVA_OPTS setting gradle.user.home is the stated non-reusable case.
        ctx = self.context()
        fi = self.fingerprint_input(
            node_plan=self.gradle_plan(),
            env={"PATH": BIN, "HOME": "/home/fake",
                 "GRADLE_OPTS": "-Dgradle.user.home=/elsewhere"})
        self.assertIsNone(fingerprint(fi, ctx))
        java_opts = self.fingerprint_input(
            node_plan=self.gradle_plan(),
            env={"PATH": BIN, "HOME": "/home/fake",
                 "JAVA_OPTS": "-Dgradle.user.home=/elsewhere"})
        self.assertIsNone(fingerprint(java_opts, ctx))


class ReuseGuardTest(FingerprintFixture):
    def test_reuse_false_producer_not_recorded(self):
        # D6: any planned step with `"reuse": false` is never recorded.
        plan = self.plan_with(Step(
            id="rust.wasm", kind="producer", cwd=".", run=["./gradlew", "rustWasm"],
            reuse=False, inputs=[{"path": "rust/"}]))
        ctx = self.context()
        self.assertIsNone(fingerprint(self.fingerprint_input(node_plan=plan), ctx))


class RunGateRecordTest(FingerprintFixture):
    def manual(self, **kwargs):
        context = self.context(**kwargs)
        outcome = run_gate("task", None, context, self.table, fixture_cmap())
        return context, outcome

    def test_stable_task_pass_recorded(self):
        # D6/D10: a stable task-gate pass writes one record keyed `<gate>/<node>`.
        self.manual()
        document = self.read_passes()
        self.assertIsNotNone(document)
        self.assertIn("task/web-extension", document)
        self.assertEqual(len(document["task/web-extension"]), 64)
        with open(self.last_run_path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["nodes"]["web-extension"]["status"], "PASS")

    def test_record_dropped_on_fail(self):
        self.manual()
        self.assertIn("task/web-extension", self.read_passes())
        script = self.machine()
        script[("npm", "run", "lint")] = Outcome("fail", code=1, tail=["lint error"])
        _context, outcome = self.manual(
            runner=ScriptedRunner(self.clock, script=script))
        self.assertEqual(outcome.results["web-extension"].status, "FAIL")
        self.assertNotIn("task/web-extension", self.read_passes() or {})

    def test_record_dropped_on_not_run(self):
        self.manual()
        self.assertIn("task/web-extension", self.read_passes())
        _context, outcome = self.manual(
            files=self.environment(jdk=False), env={
                "PATH": BIN, "HOME": "/home/fake"})
        self.assertEqual(outcome.results["web-extension"].status, "NOT RUN")
        self.assertNotIn("task/web-extension", self.read_passes() or {})

    def test_incomplete_node_not_recorded(self):
        # The lint step passes, the later steps are not reached: no record is written.
        deadline = 1000.0

        def on_call(clock, argv, outcome, cutoff, log_path):
            if argv == ("npm", "run", "lint"):
                clock.value = deadline - 45.0 + 1.0
            return None

        runner = ScriptedRunner(self.clock, script=self.machine(), on_call=on_call)
        _context, outcome = self.manual(runner=runner, deadline=deadline)
        self.assertEqual(outcome.results["web-extension"].status, "NOT RUN")
        self.assertTrue(outcome.incomplete)
        self.assertNotIn("task/web-extension", self.read_passes() or {})

    def test_branch_never_writes(self):
        context = self.context()
        outcome = run_gate("branch", None, context, self.table, fixture_cmap())
        self.assertEqual(outcome.results["web-extension"].status, "PASS")
        self.assertFalse(os.path.exists(self.passes_path))

    def test_manual_never_reads(self):
        # A manual task-gate run may write but never reads: a matching record is not a reason to
        # skip, so the failing command still runs and the node still fails.
        self.manual()
        self.assertIn("task/web-extension", self.read_passes())
        script = self.machine()
        script[("npm", "run", "lint")] = Outcome("fail", code=1, tail=["lint error"])
        runner = ScriptedRunner(self.clock, script=script)
        _context, outcome = self.manual(runner=runner)
        self.assertEqual(outcome.results["web-extension"].status, "FAIL")
        self.assertIn(["npm", "run", "lint"], self.step_calls(runner))


class RunGateStabilityTest(FingerprintFixture):
    def manual(self, **kwargs):
        context = self.context(**kwargs)
        outcome = run_gate("task", None, context, self.table, fixture_cmap())
        return context, outcome

    def assert_no_record(self):
        self.assertNotIn("task/web-extension", self.read_passes() or {})

    def test_edit_during_run_no_record(self):
        context = self.context()
        seen = {}

        def on_call(clock, argv, outcome, cutoff, log_path):
            if argv == ("npm", "run", "lint") and not seen:
                seen["done"] = True
                context.fs.files[self.path(EXT)] = b"export const background = 3;\n"
            return None

        context.runner.on_call = on_call
        outcome = run_gate("task", None, context, self.table, fixture_cmap())
        self.assertEqual(outcome.results["web-extension"].status, "PASS")
        self.assert_no_record()

    def test_clean_tracked_file_edited_during_run_no_record(self):
        # A clean tracked file contributes its index mode and blob id before the run; edited
        # while the commands run, the next observation contributes working-tree content instead.
        context = self.context(git=FakeGit(
            diff=[EXT], base=BASE,
            index={CLEAN: ("100644", "c" * 40)}))
        context.fs.files[self.path(CLEAN)] = b"export const clean = 1;\n"

        def on_call(clock, argv, outcome, cutoff, log_path):
            if argv == ("npm", "run", "lint"):
                context.git.unstaged_paths.append(CLEAN)
                context.fs.files[self.path(CLEAN)] = b"export const clean = 2;\n"
            return None

        context.runner.on_call = on_call
        outcome = run_gate("task", None, context, self.table, fixture_cmap())
        self.assertEqual(outcome.results["web-extension"].status, "PASS")
        self.assert_no_record()

    def test_new_selecting_file_during_run_no_record(self):
        context = self.context()

        def on_call(clock, argv, outcome, cutoff, log_path):
            if argv == ("npm", "run", "lint"):
                new = "web-extension/src/new.ts"
                context.git.untracked_paths.append(new)
                context.fs.files[self.path(new)] = b"export const fresh = 1;\n"
            return None

        context.runner.on_call = on_call
        outcome = run_gate("task", None, context, self.table, fixture_cmap())
        self.assertEqual(outcome.results["web-extension"].status, "PASS")
        self.assert_no_record()

    def test_snapshots_disagree_not_reusable(self):
        # D5/D6: two consumers capture the same output at different content: the node passes but
        # is not recordable.
        context = self.context()
        calls = {"wasm": 0}

        def on_call(clock, argv, outcome, cutoff, log_path):
            if argv == ("./gradlew", "rustWasm"):
                calls["wasm"] += 1
                if calls["wasm"] == 2:
                    context.fs.files[self.path(PKG_FILE)] = b'{"name":"rebuilt"}\n'
            return None

        context.runner.on_call = on_call
        outcome = run_gate("task", None, context, self.table, fixture_cmap())
        self.assertEqual(outcome.results["web-extension"].status, "PASS")
        self.assert_no_record()


class RememberedLookupTest(FingerprintFixture):
    def test_unchanged_node_skipped(self):
        # D6: after a stable pass, the same input is remembered: no command or producer runs and
        # both the NodeResult and last-run.json say PASS (remembered).
        manual = self.context()
        first = run_gate("task", None, manual, self.table, fixture_cmap())
        self.assertEqual(first.results["web-extension"].status, "PASS")
        self.assertIn("task/web-extension", self.read_passes())

        script = self.machine()
        for argv in (("npm", "run", "lint"), ("npm", "run", "typecheck"), ("npm", "test"),
                     ("./gradlew", "rustWasm")):
            script[argv] = Outcome("fail", code=1, tail=["should not run"])
        runner = ScriptedRunner(self.clock, script=script)
        context = self.context(runner=runner)
        outcome = run_gate("task", None, context, self.table, fixture_cmap(), use_passes=True)
        self.assertEqual(outcome.results["web-extension"].status, "PASS (remembered)")
        self.assertEqual(self.step_calls(runner), [])
        with open(self.last_run_path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["nodes"]["web-extension"]["status"],
                             "PASS (remembered)")

    def test_probe_timeout_on_lookup_blocks(self):
        # D9/D6: a requirement probe the runner cut off during the lookup is that step's blocking
        # timeout, with identity (node, step id, "timeout"); no command runs.
        script = self.machine()
        script[("node", "--version")] = Outcome("timeout", error="the step reached its cutoff")
        runner = ScriptedRunner(self.clock, script=script)
        context = self.context(runner=runner)
        outcome = run_gate("task", None, context, self.table, fixture_cmap(), use_passes=True)
        self.assertEqual(outcome.results["web-extension"].status, "FAIL")
        self.assertIn(("web-extension", "web-extension.lint", "timeout"), outcome.problems)
        self.assertEqual(self.step_calls(runner), [])

    # -- run_gate-level invalidation: the lookup must re-read, not replay a record ---------

    def record_pass(self):
        context = self.context()
        first = run_gate("task", None, context, self.table, fixture_cmap())
        self.assertEqual(first.results["web-extension"].status, "PASS")
        self.assertIn("task/web-extension", self.read_passes())

    def lookup(self, **kwargs):
        runner = ScriptedRunner(self.clock, script=self.machine())
        context = self.context(runner=runner, **kwargs)
        outcome = run_gate("task", None, context, self.table, fixture_cmap(),
                           use_passes=True)
        return runner, outcome

    def assert_commands_ran(self, runner, outcome):
        self.assertEqual(outcome.results["web-extension"].status, "PASS")
        self.assertIn(["npm", "run", "lint"], self.step_calls(runner))

    def test_rebuilt_output_not_remembered(self):
        # Important: a rebuilt generated output must make the lookup miss and run the commands.
        self.record_pass()
        runner, outcome = self.lookup(files=self.environment(pkg=b'{"name":"rebuilt"}\n'))
        self.assert_commands_ran(runner, outcome)

    def test_changed_tool_output_not_remembered(self):
        # Important: an upgraded Node changes `env.resolved`, so the lookup must miss.
        self.record_pass()
        script = self.machine()
        script[("node", "--version")] = Outcome("ok", stdout=["v22.2.0"])
        runner = ScriptedRunner(self.clock, script=script)
        context = self.context(runner=runner)
        outcome = run_gate("task", None, context, self.table, fixture_cmap(),
                           use_passes=True)
        self.assert_commands_ran(runner, outcome)

    def test_edited_source_not_remembered(self):
        # A changed selecting path changes `files`; the lookup must miss.
        self.record_pass()
        files = self.environment()
        files[self.path(EXT)] = b"export const background = 2;\n"
        runner, outcome = self.lookup(files=files)
        self.assert_commands_ran(runner, outcome)


class RustProducerInputTest(FingerprintFixture):
    """The rust producers declare what the build reads, not its own build-output trees."""

    def rust_plan(self, producer="rust.wasm"):
        table = real_table()
        step = table.producers[producer]
        return NodePlan(steps=[PlannedStep(step=step, argv=list(step.run))])

    def crate_files(self):
        files = self.environment()
        files[self.path("rust/veles-crypto/src/lib.rs")] = b"// one\n"
        files[self.path("rust/Cargo.toml")] = b"[workspace]\n"
        files[self.path("rust/Cargo.lock")] = b"# lock\n"
        return files

    def test_producer_inputs_exclude_build_trees(self):
        table = real_table()
        for producer in ("rust.wasm", "rust.hostJni"):
            with self.subTest(producer=producer):
                paths = [entry["path"] for entry in table.producers[producer].inputs]
                self.assertNotIn("rust/", paths)
                self.assertNotIn("protocol/", paths)
                self.assertIn("rust/veles-crypto", paths)
                self.assertIn("rust/Cargo.lock", paths)
                self.assertIn("rust/toolchain-tools.toml", paths)
                self.assertIn("{gradle_user_home}/gradle.properties", paths)

    def test_rust_build_outputs_do_not_invalidate(self):
        # rust/.gradle and rust/build are written by every Gradle run; they must not be inputs,
        # or the web-extension node is never recordable.
        plan = self.rust_plan()
        clean = self.context(files=self.crate_files())
        noisy_files = dict(self.crate_files())
        noisy_files[self.path(
            "rust/.gradle/9.5.0/executionHistory/executionHistory.bin")] = b"history"
        noisy_files[self.path(
            "rust/.gradle/buildOutputCleanup/buildOutputCleanup.lock")] = b"lock"
        noisy_files[self.path("rust/build/tmp/compile/x")] = b"tmp"
        noisy = self.context(files=noisy_files)
        fi = self.fingerprint_input(node_plan=plan, changed=[], entry="entry")
        key = fingerprint(fi, clean)
        self.assertIsNotNone(key)
        self.assertEqual(key, fingerprint(fi, noisy))

    def test_crate_source_change_invalidates(self):
        plan = self.rust_plan()
        files = self.crate_files()
        files[self.path("rust/veles-crypto/src/lib.rs")] = b"// two\n"
        fi = self.fingerprint_input(node_plan=plan, changed=[], entry="entry")
        self.assertNotEqual(fingerprint(fi, self.context(files=self.crate_files())),
                            fingerprint(fi, self.context(files=files)))


class PassesTest(unittest.TestCase):
    def test_get_put_drop_round_trip(self):
        # D6 records: `build/verify/passes.json`, `{"<gate>/<node>": "<digest>"}`; a malformed or
        # absent file is a miss, never an error.
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "passes.json")
            passes = Passes(path)
            self.assertIsNone(passes.get("task", "web-extension"))
            passes.put("task", "web-extension", "a" * 64)
            self.assertEqual(passes.get("task", "web-extension"), "a" * 64)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("not json")
            self.assertIsNone(Passes(path).get("task", "web-extension"))
            Passes(path).put("task", "web-extension", "b" * 64)
            self.assertEqual(Passes(path).get("task", "web-extension"), "b" * 64)
            Passes(path).drop("task", "web-extension")
            self.assertIsNone(Passes(path).get("task", "web-extension"))


if __name__ == "__main__":
    unittest.main()
