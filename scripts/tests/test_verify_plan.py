"""Plans, related-test strategies and `--list` (design D4, D5 and D1).

Every case either drives `plan()` over the repository's real table (so the table stays the
authority for ordering, the producer chains and the four strategies) or over a small fixture
table written to a temporary repository, for shapes the real table does not have yet (a
generated-output consumer, `files-arg`, a node with a device and a platform step). The clock,
runner, git and filesystem are injected; nothing sleeps or reads the wall clock.
"""
import json
import os
import sys
import tempfile
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS)
REPO = os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, TESTS)

import components  # noqa: E402
from verify import Fs, Outcome, Selection, load_table, main, plan, resolve_targets  # noqa: E402
from verifyfake import FakeFs, FakeGit, FakeRunner, make_context  # noqa: E402

BASE = "a" * 40
ZERO = "0" * 40
DEVICE_NOTE = "NOT RUN (device test; real-device rule)"


def command(step_id, kind="check", run=("run-step",), **extra):
    entry = {"id": step_id, "kind": kind, "run": list(run), "reuse": False}
    entry.update(extra)
    return entry


def producer(step_id, run, **extra):
    entry = {"id": step_id, "cwd": ".", "run": list(run), "reuse": False}
    entry.update(extra)
    return entry


def generated(req_id, path, producer_id):
    return {"kind": "generated", "path": path, "producer": producer_id,
            "fix": "run the producer"}


def real_table():
    cmap = components.load_map(os.path.join(REPO, ".github", "components.json"))
    return load_table(os.path.join(REPO, "scripts", "verify-commands.json"), cmap)


def real_step(table, node, step_id):
    for step in table.nodes[node].commands:
        if step.id == step_id:
            return step
    raise AssertionError("%r is not a command of %r" % (step_id, node))


def selection(files, nodes):
    return Selection(base=BASE, files=list(files),
                     reasons={node: files[0] for node in nodes} if files else {})


def planned_ids(node_plan):
    return [planned.step.id for planned in node_plan.steps]


class StepClock:
    """An injected clock whose every read advances, to cross the reserve inside a walk."""

    def __init__(self, start, step):
        self.value = float(start)
        self.step = float(step)

    def now(self):
        self.value += self.step
        return self.value

    def advance(self, seconds):
        self.value += seconds
        return self.value


class FixturePlanTest(unittest.TestCase):
    """A plan over a fixture table written into a temporary repository."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name

    def abs(self, relative):
        return os.path.join(self.repo, relative)

    def write_table(self, commands, producers=None, requirements=None, node="tooling"):
        table = {
            "version": 1,
            "requirements": dict(requirements or {}),
            "producers": list(producers or []),
            "nodes": {node: {"commands": list(commands)}},
        }
        path = self.abs(os.path.join("scripts", "verify-commands.json"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(table, handle)
        return load_table(path, {"nodes": {node: {}}})

    def write_cmap(self, node="tooling", paths=None):
        directory = self.abs(".github")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "components.json"), "w", encoding="utf-8") as handle:
            json.dump({"since": ZERO,
                       "nodes": {node: {"release": False,
                                        "paths": list(paths or [node + "/"])}}}, handle)

    def context(self, files=(), runner=None, deadline=None, fs_files=None, clock=None):
        entries = dict((self.abs(name), content)
                       for name, content in (fs_files or {}).items())
        for name in files:
            entries.setdefault(self.abs(name), b"")
        fs = FakeFs(files=entries)
        lines = []
        overrides = {"clock": clock} if clock is not None else {}
        context = make_context(repo=self.repo, fs=fs,
                               runner=runner or FakeRunner(), deadline=deadline,
                               log=lines.append, **overrides)
        self.lines = lines
        return context


class OrderingTest(FixturePlanTest):
    def test_order_independent_steps_first(self):
        # D4.1-D4.2: the checks and tests that need no generated output keep table order and run
        # before any producer; each consumer then carries its whole producer chain right before it.
        table = self.write_table(
            [
                command("tooling.typecheck", run=("check-type",), requires=["dist"]),
                command("tooling.format", run=("check-format",)),
                command("tooling.test", kind="test", run=("run-tests",), requires=["dist"]),
                command("tooling.lint", run=("check-lint",)),
            ],
            producers=[producer("build", ("make", "dist"))],
            requirements={"dist": generated("dist", "dist", "build")},
        )
        context = self.context()
        result = plan(table, selection(["tooling/a.py"], ["tooling"]), "branch", context,
                      context.runner.run)
        node = result.nodes["tooling"]
        self.assertEqual(planned_ids(node),
                         ["tooling.format", "tooling.lint", "build", "tooling.typecheck",
                          "build", "tooling.test"])
        self.assertEqual([planned.consumer_of for planned in node.steps],
                         [None, None, "tooling.typecheck", None,
                          "tooling.test", None])
        self.assertEqual(node.steps[2].argv, ["make", "dist"])
        self.assertEqual(node.steps[3].argv, ["check-type"])
        self.assertEqual(node.unresolved, [])

    def test_chain_before_each_consumer(self):
        # D5: every consumer gets rust.wasm then web-extension.build immediately before it, each time.
        table = real_table()
        context = make_context(repo=REPO, fs=Fs())
        result = plan(table, selection(["web-extension/src/background.ts"], ["web-extension"]),
                      "branch", context, context.runner.run)
        steps = result.nodes["web-extension"].steps
        ids = [planned.step.id for planned in steps]
        for consumer in ("web-extension.bundle", "web-extension.browser"):
            index = ids.index(consumer)
            self.assertEqual(ids[index - 2:index], ["rust.wasm", "web-extension.build"],
                             "%s must follow its whole producer chain" % consumer)
            self.assertEqual([planned.consumer_of for planned in steps[index - 2:index]],
                             [consumer, consumer])
        self.assertEqual(steps[ids.index("rust.wasm")].argv, ["./gradlew", "rustWasm"])
        self.assertEqual(steps[ids.index("web-extension.build")].argv, ["npm", "run", "build"])
        self.assertEqual(
            steps[ids.index("web-extension.build")].step.cwd, "web-extension")

    def test_branch_gate_all_tests(self):
        # D4.3: the branch gate plans every test unnarrowed, so no resolver runs.
        table = real_table()
        context = make_context(repo=REPO, fs=Fs())
        result = plan(table, selection(["web-extension/src/background.ts"], ["web-extension"]),
                      "branch", context, context.runner.run)
        node = result.nodes["web-extension"]
        for step_id in ("web-extension.unit", "web-extension.bundle", "web-extension.browser"):
            planned = [entry for entry in node.steps if entry.step.id == step_id]
            self.assertEqual(len(planned), 1, step_id)
            self.assertEqual(planned[0].argv, real_step(table, "web-extension", step_id).run)
        self.assertEqual(context.runner.calls, [])
        self.assertEqual(node.unresolved, [])

    def test_task_gate_related_only(self):
        # D4.3: the task gate plans the narrowed step and nothing wider.
        table = real_table()
        context = make_context(repo=REPO, fs=Fs())
        result = plan(table, selection(["scripts/components.py"], ["tooling"]), "task",
                      context, context.runner.run)
        node = result.nodes["tooling"]
        narrowed = [entry for entry in node.steps if entry.step.id == "tooling.scripts-test"]
        self.assertEqual(len(narrowed), 1)
        self.assertEqual(narrowed[0].argv,
                         ["python3", "-m", "unittest", "scripts/tests/test_components.py"])
        self.assertNotIn(["./gradlew", "scriptsTest"],
                         [entry.argv for entry in node.steps])
        self.assertFalse(any("none related" in note for note in node.notes))

    def test_test_without_related_branch_only_note(self):
        # D4.3 last clause: a test with no `related` is a branch-only note in the task gate.
        table = real_table()
        context = make_context(repo=REPO, fs=Fs())
        result = plan(table, selection(["native-bridge/host/src/lib.rs"], ["native-host"]),
                      "task", context, context.runner.run)
        node = result.nodes["native-host"]
        self.assertNotIn("native-host.build-contract", planned_ids(node))
        self.assertEqual(node.notes, ["NOT RUN (branch gate only: native-host.build-contract)"])

    def test_device_and_platform_notes(self):
        # D4.4: device and platform steps are expected-skip notes with the documented texts.
        table = self.write_table(
            [
                command("tooling.connected", kind="device", run=("gradlew", "connected")),
                command("tooling.sign", kind="platform", run=("sign",),
                        platforms=["macos"], ci="publish-release / build-macos"),
            ]
        )
        context = self.context()
        result = plan(table, selection(["tooling/a.py"], ["tooling"]), "task", context,
                      context.runner.run)
        node = result.nodes["tooling"]
        self.assertEqual(node.steps, [])
        self.assertEqual(node.notes,
                         [DEVICE_NOTE, "NOT RUN (macOS only; CI job publish-release / build-macos)"])


class StrategyTest(FixturePlanTest):
    def test_name_map_self_target(self):
        # D4: a changed file matching the `tests` glob is its own target, and only that one.
        table = real_table()
        context = make_context(repo=REPO, fs=Fs())
        result = plan(table, selection(["scripts/tests/test_components.py"], ["tooling"]),
                      "task", context, context.runner.run)
        narrowed = [entry for entry in result.nodes["tooling"].steps
                    if entry.step.id == "tooling.scripts-test"]
        self.assertEqual(len(narrowed), 1)
        self.assertEqual(narrowed[0].argv,
                         ["python3", "-m", "unittest", "scripts/tests/test_components.py"])

    def test_name_map_source_maps_every_matching_test(self):
        # D4 name-map: one source maps through the template to every existing test file it names.
        table = self.write_table(
            [
                command("tooling.scripts-test", kind="test", run=("cmd",),
                        related={"strategy": "name-map", "sources": "scripts/*.py",
                                 "tests": "scripts/tests/test_<stem>*.py",
                                 "run": ["cmd", "{targets}"]}),
            ]
        )
        files = ["scripts/verify.py", "scripts/tests/test_verify_select.py",
                 "scripts/tests/test_verify_table.py"]
        context = self.context(files=files)
        result = plan(table, selection(["scripts/verify.py"], ["tooling"]), "task", context,
                      context.runner.run)
        self.assertEqual(result.nodes["tooling"].steps[0].argv,
                         ["cmd", "scripts/tests/test_verify_select.py",
                          "scripts/tests/test_verify_table.py"])

    def test_android_fqn_from_existing_file(self):
        # D4 name-map: the existing test file's `package` line and name give the class, never a guess.
        table = real_table()
        context = make_context(repo=REPO, fs=Fs())
        changed = "android/app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt"
        result = plan(table, selection([changed], ["android"]), "task", context,
                      context.runner.run)
        narrowed = [entry for entry in result.nodes["android"].steps
                    if entry.step.id == "android.unit-tests"]
        self.assertEqual(len(narrowed), 1)
        self.assertEqual(narrowed[0].argv,
                         ["./gradlew", "testDebugUnitTest",
                          "--tests=me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"])
        self.assertEqual(narrowed[0].step.cwd, "android")

    def test_crate_uses_gradle_task(self):
        # D4 crate: the changed path's crate maps to its documented Gradle test task.
        table = real_table()
        context = make_context(repo=REPO, fs=Fs())
        result = plan(table, selection(["native-bridge/host/src/lib.rs"], ["native-host"]),
                      "task", context, context.runner.run)
        narrowed = [entry for entry in result.nodes["native-host"].steps
                    if entry.step.id == "native-host.test"]
        self.assertEqual(len(narrowed), 1)
        self.assertEqual(narrowed[0].argv, ["./gradlew", "bridgeHostTest"])
        self.assertEqual(narrowed[0].step.cwd, ".")

    def test_within_filters_all_strategies(self):
        # D4: `within` is optional for every strategy and intersects each target list.
        table = self.write_table(
            [
                command("tooling.name-map", kind="test", run=("run-name-map",),
                        related={"strategy": "name-map", "sources": "src/*.ts",
                                 "tests": "test/<stem>.test.ts", "within": "allow.json:files",
                                 "run": ["run-name-map", "{targets}"]}),
                command("tooling.self", kind="test", run=("run-self",),
                        related={"strategy": "self", "tests": "test/*.ts",
                                 "within": "allow.json:files",
                                 "run": ["run-self", "{targets}"]}),
                command("tooling.files-arg", kind="test", run=("run-files",),
                        related={"strategy": "files-arg", "match": ["src/"],
                                 "resolve": ["list", "{changed}"],
                                 "within": "allow.json:files",
                                 "run": ["run-files", "{targets}"]}),
                command("tooling.crate", kind="test", run=("run-crate",),
                        related={"strategy": "crate",
                                 "crates": {"crates/a/": ["run-crate-a"],
                                            "crates/b/": ["run-crate-b"]},
                                 "within": "allow.json:files"}),
            ],
        )
        files = ["allow.json", "crates/a/x.rs", "crates/b/x.rs", "src/x.ts", "src/y.ts",
                 "test/x.test.ts", "test/y.test.ts"]
        runner = FakeRunner(stdout={("list", "src/x.ts", "src/y.ts"):
                                    ["test/x.test.ts", "test/y.test.ts"]})
        context = self.context(files=files, runner=runner,
                               fs_files={"allow.json": b'{"files": "test/x.test.ts crates/a/"}'})
        changed = ["crates/a/x.rs", "crates/b/x.rs", "src/x.ts", "src/y.ts",
                   "test/x.test.ts", "test/y.test.ts"]
        result = plan(table, selection(changed, ["tooling"]), "task", context,
                      context.runner.run)
        node = result.nodes["tooling"]

        name_map = [entry for entry in node.steps if entry.step.id == "tooling.name-map"]
        self.assertEqual([entry.argv for entry in name_map],
                         [["run-name-map", "test/x.test.ts"]])
        self_found = [entry for entry in node.steps if entry.step.id == "tooling.self"]
        self.assertEqual([entry.argv for entry in self_found],
                         [["run-self", "test/x.test.ts"]])
        files_arg = [entry for entry in node.steps if entry.step.id == "tooling.files-arg"]
        self.assertEqual([entry.argv for entry in files_arg],
                         [["run-files", "test/x.test.ts"]])
        crate = [entry for entry in node.steps if entry.step.id == "tooling.crate"]
        self.assertEqual([entry.argv for entry in crate], [["run-crate-a"]])
        self.assertFalse(any("none related" in note for note in node.notes))

    def test_none_related_note(self):
        # D4: an empty target list is a note naming the changed files, never a run.
        table = real_table()
        context = make_context(repo=REPO, fs=Fs())
        result = plan(table, selection(["web-extension/eslint.config.js"], ["web-extension"]),
                      "task", context, context.runner.run)
        node = result.nodes["web-extension"]
        self.assertIn("tests: none related (web-extension/eslint.config.js)", node.notes)
        planned = set(planned_ids(node))
        for test_id in ("web-extension.unit", "web-extension.bundle", "web-extension.browser"):
            self.assertNotIn(test_id, planned)
        self.assertEqual(node.unresolved, [])

    def test_within_failure_unresolved(self):
        # D4/spec: a `within` allowlist that cannot be read is an unexpected NOT RUN, never a
        # `none related` note that would hide the test's targets forever.
        cases = {
            "missing": (None, "the file does not exist"),
            "invalid json": (b"{", "invalid JSON"),
            "missing key": (b'{"scripts": {"test:browser": "node x.mjs"}}',
                            "scripts.test is missing"),
            "not a script": (b'{"scripts": {"test": 7}}',
                             "scripts.test is not a script string or list"),
        }
        for name, (content, reason) in cases.items():
            with self.subTest(case=name):
                table = self.write_table(
                    [
                        command("tooling.unit", kind="test", cwd="web-extension",
                                run=("cmd",),
                                related={"strategy": "name-map",
                                         "sources": "web-extension/src/*.ts",
                                         "tests": "web-extension/test/<stem>.test.ts",
                                         "within": "package.json:scripts.test",
                                         "run": ["cmd", "{targets}"]}),
                    ]
                )
                fs_files = {} if content is None else {
                    "web-extension/package.json": content}
                context = self.context(
                    files=["web-extension/src/a.ts", "web-extension/test/a.test.ts"],
                    fs_files=fs_files)
                result = plan(table, selection(["web-extension/src/a.ts"], ["tooling"]),
                              "task", context, context.runner.run)
                node = result.nodes["tooling"]
                self.assertEqual(node.steps, [])
                self.assertEqual([step_id for step_id, _ in node.unresolved], ["tooling.unit"])
                outcome = node.unresolved[0][1]
                self.assertEqual(outcome.kind, "not_run")
                self.assertIn("within web-extension/package.json:scripts.test", outcome.error)
                self.assertIn(reason, outcome.error)
                self.assertFalse(any("none related" in note for note in node.notes))

    def test_deadline_before_resolution_is_unresolved(self):
        # D9/D4: inside the reserve no resolution starts, and that is a timeout, not a note.
        table = self.write_table(
            [
                command("tooling.unit", kind="test", run=("cmd",),
                        related={"strategy": "name-map", "sources": "src/*.ts",
                                 "tests": "test/<stem>.test.ts",
                                 "run": ["cmd", "{targets}"]}),
            ]
        )
        context = self.context(files=["src/a.ts", "test/a.test.ts"], deadline=1000.0)
        context.clock.value = 1000.0 - 45.0
        result = plan(table, selection(["src/a.ts"], ["tooling"]), "task", context,
                      context.runner.run)
        node = result.nodes["tooling"]
        self.assertEqual(node.steps, [])
        outcome = node.unresolved[0][1]
        self.assertEqual(outcome.kind, "timeout")
        self.assertEqual(outcome.error, "target resolution reached the deadline reserve")
        self.assertFalse(any("none related" in note for note in node.notes))

    def test_deadline_during_walk_is_unresolved(self):
        # D9: a walk that crosses the reserve stops with a timeout, never a "no hit" note.
        table = self.write_table(
            [
                command("tooling.unit", kind="test", run=("cmd",),
                        related={"strategy": "name-map", "sources": "src/*.ts",
                                 "tests": "test/**/<stem>.test.ts",
                                 "run": ["cmd", "{targets}"]}),
            ]
        )
        clock = StepClock(54.0, 0.5)  # deadline 100 -> the reserve starts at 55
        context = self.context(files=["src/a.ts", "test/a.test.ts"], deadline=100.0,
                               clock=clock)
        result = plan(table, selection(["src/a.ts"], ["tooling"]), "task", context,
                      context.runner.run)
        node = result.nodes["tooling"]
        self.assertEqual(node.steps, [])
        outcome = node.unresolved[0][1]
        self.assertEqual(outcome.kind, "timeout")
        self.assertEqual(outcome.error, "target resolution reached the deadline reserve")
        self.assertFalse(any("none related" in note for note in node.notes))

    def test_targets_flag_expands_per_target(self):
        # R8: an item holding {targets} repeats once per target with the placeholder replaced;
        # a bare {targets} item stays one plain argv item per target.
        table = self.write_table(
            [
                command("tooling.flag", kind="test", run=("flag-run",),
                        related={"strategy": "name-map", "sources": "src/*.ts",
                                 "tests": "test/<stem>.test.ts",
                                 "run": ["./gradlew", "t", "--tests={targets}"]}),
                command("tooling.bare", kind="test", run=("bare-run",),
                        related={"strategy": "name-map", "sources": "src/*.ts",
                                 "tests": "test/<stem>.test.ts",
                                 "run": ["cmd", "{targets}"]}),
            ]
        )
        changed = ["src/a.ts", "src/b.ts"]
        files = changed + ["test/a.test.ts", "test/b.test.ts"]
        context = self.context(files=files)
        result = plan(table, selection(changed, ["tooling"]), "task", context,
                      context.runner.run)
        node = result.nodes["tooling"]
        flag = [entry.argv for entry in node.steps if entry.step.id == "tooling.flag"]
        self.assertEqual(flag, [["./gradlew", "t", "--tests=test/a.test.ts",
                                 "--tests=test/b.test.ts"]])
        bare = [entry.argv for entry in node.steps if entry.step.id == "tooling.bare"]
        self.assertEqual(bare, [["cmd", "test/a.test.ts", "test/b.test.ts"]])

    def test_argv_expansion_unusual_names(self):
        # Unusual names stay exactly one argv item each, after the template's own `--`.
        table = self.write_table(
            [
                command("tooling.self", kind="test", run=("npx", "vitest", "run", "--"),
                        related={"strategy": "self", "tests": "test/*.ts",
                                 "run": ["npx", "vitest", "run", "--", "{targets}"]}),
            ]
        )
        changed = ["test/-x.ts", "test/a b.ts", 'test/it\'s "q".ts']
        context = self.context(files=changed)
        targets, outcome = resolve_targets(table.nodes["tooling"].commands[0], changed,
                                           context, context.runner.run)
        self.assertIsNone(outcome)
        self.assertEqual(targets, changed)
        result = plan(table, selection(changed, ["tooling"]), "task", context,
                      context.runner.run)
        argv = result.nodes["tooling"].steps[0].argv
        self.assertEqual(len(argv), 7)
        self.assertEqual(argv[:4], ["npx", "vitest", "run", "--"])
        self.assertEqual(argv[4:], changed)

    def test_deleted_test_not_targeted(self):
        # D4: a deleted path selects nodes but is never a target, and the note names it.
        table = self.write_table(
            [
                command("tooling.self", kind="test", run=("cmd",),
                        related={"strategy": "self", "tests": "test/*.ts",
                                 "run": ["cmd", "{targets}"]}),
            ]
        )
        context = self.context(files=["test/kept.test.ts"])
        step = table.nodes["tooling"].commands[0]
        targets, outcome = resolve_targets(step, ["test/gone.test.ts"], context,
                                           context.runner.run)
        self.assertEqual(targets, [])
        self.assertIsNone(outcome)
        result = plan(table, selection(["test/gone.test.ts"], ["tooling"]), "task", context,
                      context.runner.run)
        node = result.nodes["tooling"]
        self.assertEqual(node.steps, [])
        self.assertEqual(node.notes, ["tests: none related (test/gone.test.ts)"])

    def test_resolver_failure_unexpected_not_run(self):
        # D4/spec: a failing resolver is an unexpected NOT RUN, never a `none related` note.
        table = self.write_table(
            [
                command("tooling.files-arg", kind="test", cwd="web-extension",
                        run=("npx", "vitest", "run"),
                        related={"strategy": "files-arg",
                                 "match": ["web-extension/src/"],
                                 "resolve": ["list", "{changed}"],
                                 "run": ["npx", "vitest", "run", "{targets}"]}),
            ]
        )
        changed = ["web-extension/src/a.ts"]
        runner = FakeRunner(script={("list", "src/a.ts"):
                                    Outcome("fail", code=1, error="vitest exploded")})
        context = self.context(files=changed, runner=runner)
        result = plan(table, selection(changed, ["tooling"]), "task", context,
                      context.runner.run)
        node = result.nodes["tooling"]
        self.assertEqual(node.steps, [])
        self.assertEqual([step_id for step_id, _ in node.unresolved],
                         ["tooling.files-arg"])
        outcome = node.unresolved[0][1]
        self.assertEqual(outcome.kind, "not_run")
        self.assertEqual(outcome.error, "vitest exploded")
        self.assertFalse(any("none related" in note for note in node.notes))

    def test_resolver_timeout_outcome(self):
        # D9/D4: a cut-off resolver is a timeout outcome, and no narrowed step is planned.
        table = self.write_table(
            [
                command("tooling.files-arg", kind="test", cwd="web-extension",
                        related={"strategy": "files-arg",
                                 "match": ["web-extension/src/"],
                                 "resolve": ["list", "{changed}"],
                                 "run": ["npx", "vitest", "run", "{targets}"]}),
            ]
        )
        changed = ["web-extension/src/a.ts"]
        runner = FakeRunner(script={("list", "src/a.ts"):
                                    Outcome("timeout", elapsed_s=60.0)})
        context = self.context(files=changed, runner=runner, deadline=100.0)
        step = table.nodes["tooling"].commands[0]
        targets, outcome = resolve_targets(step, changed, context, context.runner.run)
        self.assertEqual(targets, [])
        self.assertEqual(outcome.kind, "timeout")
        self.assertEqual(runner.cutoffs, [55.0])  # min(now + 60, deadline - 45)
        result = plan(table, selection(changed, ["tooling"]), "task", context,
                      context.runner.run)
        node = result.nodes["tooling"]
        self.assertEqual(node.steps, [])
        self.assertEqual(node.unresolved[0][1].kind, "timeout")


class ListingTest(FixturePlanTest):
    def test_list_unresolved_targets(self):
        # D1: --list prints the unresolved line in place of the narrowed argv, exits 0 and runs
        # nothing but the resolver.
        node = "web-extension"
        self.write_table(
            [
                command("web-extension.unit", kind="test", cwd="web-extension",
                        run=("npx", "vitest", "run"),
                        related={"strategy": "files-arg",
                                 "match": ["web-extension/src/"],
                                 "resolve": ["list", "{changed}"],
                                 "run": ["npx", "vitest", "run", "{targets}"]}),
            ],
            node=node,
        )
        self.write_cmap(node=node)
        changed = ["web-extension/src/a.ts"]
        runner = FakeRunner(script={("list", "src/a.ts"):
                                    Outcome("not_run", error="vitest is not installed")})
        context = self.context(files=changed, runner=runner)
        context.git = FakeGit(diff=changed, base=BASE)
        code = main(["--list"], context)
        self.assertEqual(code, 0)
        self.assertTrue(any("targets unresolved: vitest is not installed" in line
                            for line in self.lines), self.lines)
        self.assertTrue(any("selected by" in line for line in self.lines), self.lines)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0]["argv"], ["list", "src/a.ts"])
        self.assertEqual(runner.calls[0]["cwd"], self.abs("web-extension"))
        self.assertTrue(runner.calls[0]["capture"])
        self.assertFalse(any("web-extension.unit: cd" in line for line in self.lines),
                         self.lines)


class RealTablePlanTest(unittest.TestCase):
    def test_real_table_extension_branch_plan(self):
        # The extension's branch inventory: unit, bundle and browser, each consumer after its chain.
        table = real_table()
        ctx = make_context(repo=REPO, fs=Fs())
        result = plan(table, selection(["web-extension/src/background.ts"], ["web-extension"]),
                      "branch", ctx, ctx.runner.run)
        ids = [planned.step.id for planned in result.nodes["web-extension"].steps]
        for step_id in ("web-extension.unit", "web-extension.bundle", "web-extension.browser"):
            self.assertIn(step_id, ids)
        for consumer in ("web-extension.bundle", "web-extension.browser"):
            index = ids.index(consumer)
            self.assertEqual(ids[index - 2:index], ["rust.wasm", "web-extension.build"])

    def test_real_table_bridge_split(self):
        # `bridgeTest` is a test split from the checks of the same documented line; in the task
        # gate it is branch-only because it has no `related`.
        table = real_table()
        ctx = make_context(repo=REPO, fs=Fs())
        changed = ["native-bridge/package.json"]
        branch = plan(table, selection(changed, ["bridge-scaffold"]), "branch", ctx,
                      ctx.runner.run)
        ids = planned_ids(branch.nodes["bridge-scaffold"])
        self.assertEqual(ids, ["bridge.format", "bridge.lint", "bridge.typecheck",
                               "bridge.rust-format", "bridge.rust-lint", "bridge.test"])
        test_step = [planned for planned in branch.nodes["bridge-scaffold"].steps
                     if planned.step.id == "bridge.test"][0]
        self.assertEqual(test_step.step.kind, "test")
        self.assertEqual(test_step.argv, ["./gradlew", "bridgeTest"])

        task = plan(table, selection(changed, ["bridge-scaffold"]), "task", ctx,
                    ctx.runner.run)
        node = task.nodes["bridge-scaffold"]
        self.assertNotIn("bridge.test", planned_ids(node))
        self.assertEqual(node.notes, ["NOT RUN (branch gate only: bridge.test)"])

    def test_real_table_connected_is_device(self):
        # D4.4: the connected test is never planned; the android node carries the device note.
        table = real_table()
        ctx = make_context(repo=REPO, fs=Fs())
        changed = ["android/app/src/main/AndroidManifest.xml"]
        for gate in ("branch", "task"):
            result = plan(table, selection(changed, ["android"]), gate, ctx, ctx.runner.run)
            node = result.nodes["android"]
            self.assertNotIn("android.connected-tests", planned_ids(node))
            self.assertIn(DEVICE_NOTE, node.notes)
            self.assertEqual(node.unresolved, [])


if __name__ == "__main__":
    unittest.main()
