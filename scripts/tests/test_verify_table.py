"""The command table (scripts/verify-commands.json) and its validation (design D2).

One case per validation rule; each asserts TableError naming the offending node, command or
producer. The CLI case proves an invalid table exits 2 before anything runs.
"""
import copy
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

import verify  # noqa: E402
from verify import TableError, load_table, main  # noqa: E402
from verifyfake import make_context  # noqa: E402

GRADLE_HOME = [
    {"path": "{gradle_user_home}/gradle.properties"},
    {"path": "{gradle_user_home}/init.d/"},
]
CMAP = {"nodes": {"android": {}, "tooling": {}}}
ZERO_SHA = "0" * 40


def minimal_cmap():
    return {
        "since": ZERO_SHA,
        "nodes": {
            "android": {"release": False, "paths": ["android/"]},
            "tooling": {"release": False, "paths": ["scripts/"]},
        },
    }


def valid_table():
    """A small table that passes every rule; tests mutate one thing at a time."""
    return {
        "version": 1,
        "requirements": {
            "java-home": {
                "kind": "tool",
                "resolver": "java-home",
                "args": {},
                "fix": "install a JDK 21",
            },
            "node": {
                "kind": "tool",
                "resolver": "path-tool",
                "args": {"program": "node", "version_cmd": ["node", "--version"], "min": "22"},
                "fix": "install Node 22",
            },
            "python": {
                "kind": "tool",
                "resolver": "path-tool",
                "args": {"program": "python3", "version_cmd": ["python3", "--version"], "min": "3.8"},
                "fix": "install Python 3.8",
            },
            "rust.wasm": {
                "kind": "generated",
                "path": "web-extension/rust-wasm/pkg",
                "producer": "rust.wasm",
                "fix": "run ./gradlew rustWasm",
            },
        },
        "producers": [
            {
                "id": "rust.wasm",
                "cwd": ".",
                "run": ["./gradlew", "rustWasm"],
                "requires": ["java-home"],
                "inputs": GRADLE_HOME,
            }
        ],
        "nodes": {
            "android": {
                "commands": [
                    {
                        "id": "android.lint",
                        "kind": "check",
                        "cwd": "android",
                        "run": ["./gradlew", "lintDebug"],
                        "requires": ["java-home"],
                        "inputs": GRADLE_HOME,
                    }
                ]
            },
            "tooling": {
                "commands": [
                    {
                        "id": "tooling.scripts-test",
                        "kind": "test",
                        "cwd": ".",
                        "run": ["./gradlew", "scriptsTest"],
                        "requires": ["java-home"],
                        "inputs": GRADLE_HOME,
                    },
                    {
                        "id": "tooling.python-test",
                        "kind": "test",
                        "cwd": ".",
                        "run": ["python3", "-I", "-m", "unittest", "discover"],
                        "requires": ["python"],
                        "inputs": [],
                    },
                ]
            },
        },
    }


def command(table, node="android", index=0):
    return table["nodes"][node]["commands"][index]


def load(table, cmap=None):
    with tempfile.TemporaryDirectory() as root:
        path = write_table(root, table)
        return load_table(path, CMAP if cmap is None else cmap)


def write_table(root, table):
    path = os.path.join(root, "scripts", "verify-commands.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(table, handle)
    return path


def write_raw(root, text):
    path = os.path.join(root, "scripts", "verify-commands.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def real_table():
    cmap = verify.components.load_map(os.path.join(REPO, ".github", "components.json"))
    return load_table(os.path.join(REPO, "scripts", "verify-commands.json"), cmap)


class TableValidationTest(unittest.TestCase):
    def assertInvalid(self, table, *fragments):
        with self.assertRaises(TableError) as caught:
            load(table)
        message = str(caught.exception)
        for fragment in fragments:
            self.assertIn(fragment, message)
        return message

    def test_valid_fixture_loads(self):
        table = load(valid_table())
        self.assertEqual(set(table.nodes), {"android", "tooling"})
        self.assertIn("rust.wasm", table.producers)
        self.assertEqual(table.producers["rust.wasm"].kind, "producer")

    def test_nodes_must_equal_map(self):
        extra = valid_table()
        extra["nodes"]["protocol"] = {"commands": [], "none": "nothing to run"}
        self.assertInvalid(extra, "protocol")
        missing = valid_table()
        del missing["nodes"]["tooling"]
        self.assertInvalid(missing, "tooling")

    def test_duplicate_id(self):
        table = valid_table()
        table["nodes"]["tooling"]["commands"].append(copy.deepcopy(command(table)))
        self.assertInvalid(table, "android.lint")

    def test_unknown_requirement_ref(self):
        table = valid_table()
        command(table)["requires"] = ["nope"]
        self.assertInvalid(table, "android.lint", "nope")

    def test_unknown_producer_ref(self):
        table = valid_table()
        table["requirements"]["rust.wasm"]["producer"] = "missing.producer"
        self.assertInvalid(table, "rust.wasm", "missing.producer")

    def test_unknown_kind(self):
        table = valid_table()
        command(table)["kind"] = "lint"
        self.assertInvalid(table, "android.lint", "lint")

    def test_unknown_resolver(self):
        table = valid_table()
        table["requirements"]["java-home"]["resolver"] = "which-java"
        self.assertInvalid(table, "java-home", "which-java")

    def test_strategy_fields_exact(self):
        missing = valid_table()
        command(missing, "tooling", 1)["related"] = {
            "strategy": "name-map",
            "tests": "scripts/tests/test_<stem>.py",
            "run": ["python3", "-I", "{targets}"],
        }
        self.assertInvalid(missing, "tooling.python-test", "sources")

        extra = valid_table()
        command(extra, "tooling", 1)["related"] = {
            "strategy": "self",
            "tests": "scripts/tests/test_*.py",
            "run": ["python3", "-I", "{targets}"],
            "match": ["scripts/"],
        }
        self.assertInvalid(extra, "tooling.python-test", "match")

        ok = valid_table()
        command(ok, "tooling", 1)["related"] = {
            "strategy": "name-map",
            "sources": "scripts/*.py",
            "tests": "scripts/tests/test_<stem>.py",
            "within": "package.json:scripts.test",
            "run": ["python3", "-I", "{targets}"],
        }
        load(ok)

    def test_platform_needs_platforms_and_ci(self):
        no_platforms = valid_table()
        command(no_platforms)["kind"] = "platform"
        self.assertInvalid(no_platforms, "android.lint", "platforms")

        no_ci = valid_table()
        command(no_ci)["kind"] = "platform"
        command(no_ci)["platforms"] = ["macos"]
        self.assertInvalid(no_ci, "android.lint", "ci")

        complete = valid_table()
        command(complete)["kind"] = "platform"
        command(complete)["platforms"] = ["macos", "windows"]
        command(complete)["ci"] = "publish-release / build-macos"
        load(complete)

    def test_inputs_or_reuse_false_on_command(self):
        table = valid_table()
        del command(table)["inputs"]
        self.assertInvalid(table, "android.lint")

        allowed = valid_table()
        del command(allowed)["inputs"]
        command(allowed)["reuse"] = False
        load(allowed)

    def test_inputs_or_reuse_false_on_producer(self):
        table = valid_table()
        del table["producers"][0]["inputs"]
        self.assertInvalid(table, "rust.wasm")

        allowed = valid_table()
        del allowed["producers"][0]["inputs"]
        allowed["producers"][0]["reuse"] = False
        load(allowed)

    def test_reusable_step_declares_tools(self):
        cases = [
            ("gradlew", ["./gradlew", "rustWasm"], [], "java-home"),
            ("node", ["node", "--test", "x.mjs"], [], "node"),
            ("npm", ["npm", "test"], [], "npm"),
            ("npx", ["npx", "vitest", "run"], [], "npx"),
            ("python3", ["python3", "-m", "unittest"], [], "python3"),
        ]
        for name, argv, requires, expected in cases:
            with self.subTest(program=name):
                table = valid_table()
                command(table)["run"] = argv
                command(table)["requires"] = requires
                self.assertInvalid(table, "android.lint", expected)

        # Each branch passes once its requirement is declared.
        for name, argv, requirement in [
            ("gradlew", ["./gradlew", "lintDebug"], "java-home"),
            ("node", ["node", "--test", "x.mjs"], "node"),
            ("python3", ["python3", "-m", "unittest"], "python"),
        ]:
            with self.subTest(covered=name):
                table = valid_table()
                command(table)["run"] = argv
                command(table)["requires"] = [requirement]
                load(table)

        # Other programs are the table author's responsibility and are not validated by name.
        other = valid_table()
        command(other)["run"] = ["git", "status", "--porcelain"]
        command(other)["requires"] = []
        load(other)

        # A related run is executed too, so its program needs its tool requirement.
        related = valid_table()
        command(related, "tooling", 1)["requires"] = ["python"]
        command(related, "tooling", 1)["related"] = {
            "strategy": "self",
            "tests": "scripts/tests/test_*.py",
            "run": ["npx", "vitest", "run", "{targets}"],
        }
        self.assertInvalid(related, "tooling.python-test", "npx")

    def test_cwd_outside_repo(self):
        for cwd in ("../outside", "/tmp", "android/../../outside"):
            with self.subTest(cwd=cwd):
                table = valid_table()
                command(table)["cwd"] = cwd
                self.assertInvalid(table, "android.lint")

    def test_targets_placeholder_only_in_related(self):
        table = valid_table()
        command(table)["run"] = ["./gradlew", "lintDebug", "{targets}"]
        self.assertInvalid(table, "android.lint", "{targets}")

        producer = valid_table()
        producer["producers"][0]["run"] = ["./gradlew", "rustWasm", "{changed}"]
        self.assertInvalid(producer, "rust.wasm", "{changed}")

        allowed = valid_table()
        command(allowed, "tooling", 1)["related"] = {
            "strategy": "self",
            "tests": "scripts/tests/test_*.py",
            "run": ["python3", "-I", "{targets}"],
        }
        load(allowed)

    def test_env_match_needs_fix(self):
        exit_without_fix = valid_table()
        command(exit_without_fix)["env_exit"] = [{"code": 2}]
        self.assertInvalid(exit_without_fix, "android.lint", "fix")

        pattern_without_fix = valid_table()
        command(pattern_without_fix)["env_patterns"] = [{"regex": "no such tool"}]
        self.assertInvalid(pattern_without_fix, "android.lint", "fix")

        complete = valid_table()
        command(complete)["env_exit"] = [{"code": 2, "fix": "install the tool"}]
        command(complete)["env_patterns"] = [{"regex": "no such tool", "fix": "install the tool"}]
        load(complete)

    def test_run_must_be_nonempty_strings(self):
        for run in ([], ["npm", ""], ["npm", 3], "npm test"):
            with self.subTest(run=run):
                table = valid_table()
                command(table)["run"] = run
                self.assertInvalid(table, "android.lint")

    def test_loader_rejects_doc_and_excluded(self):
        cases = [
            (
                "doc",
                lambda table: command(table).update({"doc": "android/AGENTS.md"}),
                "android.lint",
            ),
            (
                "excluded",
                lambda table: table["nodes"]["android"].update({"excluded": []}),
                "android",
            ),
        ]
        for field, mutate, entry in cases:
            with self.subTest(field=field):
                table = valid_table()
                mutate(table)
                with tempfile.TemporaryDirectory() as root:
                    write_table(root, table)
                    map_dir = os.path.join(root, ".github")
                    os.makedirs(map_dir)
                    with open(
                        os.path.join(map_dir, "components.json"), "w", encoding="utf-8"
                    ) as handle:
                        json.dump(minimal_cmap(), handle)
                    lines = []
                    context = make_context(repo=root, log=lines.append)
                    code = main([], context)
                self.assertEqual(code, 2)
                self.assertTrue(
                    any(entry in line and field in line for line in lines), lines
                )
                self.assertEqual(context.runner.calls, [])
                self.assertEqual(context.git.calls, [])

    def test_invalid_related_on_check(self):
        table = valid_table()
        command(table)["related"] = {
            "strategy": "self",
            "tests": "android/app/src/test/**/*.kt",
            "run": ["python3", "{targets}"],
        }
        self.assertInvalid(table, "android.lint", "test")


class StrictJsonTest(unittest.TestCase):
    def test_duplicate_keys_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = write_raw(
                root,
                '{"version": 1, "version": 1, "requirements": {}, "producers": [], "nodes": {}}',
            )
            with self.assertRaises(TableError) as caught:
                load_table(path, CMAP)
        self.assertIn("version", str(caught.exception))

    def test_non_finite_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = write_raw(root, '{"version": NaN}')
            with self.assertRaises(TableError) as caught:
                load_table(path, CMAP)
        self.assertIn("NaN", str(caught.exception))


class CommandLineTest(unittest.TestCase):
    def test_invalid_table_cli_exit_2(self):
        table = valid_table()
        command(table)["requires"] = ["missing-requirement"]
        with tempfile.TemporaryDirectory() as root:
            write_table(root, table)
            map_dir = os.path.join(root, ".github")
            os.makedirs(map_dir)
            with open(os.path.join(map_dir, "components.json"), "w", encoding="utf-8") as handle:
                json.dump(minimal_cmap(), handle)
            lines = []
            context = make_context(repo=root, log=lines.append)
            code = main([], context)
        self.assertEqual(code, 2)
        self.assertTrue(any("android.lint" in line for line in lines), lines)
        self.assertEqual(context.runner.calls, [])
        self.assertEqual(context.git.calls, [])

    def test_real_table_valid(self):
        cmap = verify.components.load_map(os.path.join(REPO, ".github", "components.json"))
        table = real_table()
        self.assertEqual(set(table.nodes), set(cmap["nodes"]))
        ids = [step.id for node in table.nodes.values() for step in node.commands]
        ids.extend(table.producers)
        self.assertEqual(len(ids), len(set(ids)))
        used = set()
        for node in table.nodes.values():
            self.assertTrue(node.commands or node.none)
            for step in node.commands:
                used.update(step.requires)
                if step.kind == "platform":
                    self.assertTrue(step.platforms, step.id)
                    self.assertTrue(step.ci, step.id)
        for producer in table.producers.values():
            used.update(producer.requires)
        self.assertEqual(set(table.requirements), used)
        self.assertIn("rust.wasm", table.producers)
        self.assertIn("web-extension.build", table.producers)
        browser = [step for step in table.nodes["web-extension"].commands
                   if step.id == "web-extension.browser"][0]
        self.assertEqual(browser.kind, "test")
        self.assertIn("rust.wasm", browser.requires)
        self.assertIn("web-extension.build", browser.requires)
        bridge = {step.id: step for step in table.nodes["bridge-scaffold"].commands}
        self.assertEqual(bridge["bridge.test"].kind, "test")
        self.assertNotIn("bridge.install", bridge)

    def test_narrowed_run_templates_support_many_targets(self):
        """A related run must never put two targets into one flag or one positional name (D4)."""
        table = real_table()
        android = {step.id: step for step in table.nodes["android"].commands}
        self.assertEqual(android["android.unit-tests"].related["run"],
                         ["./gradlew", "testDebugUnitTest", "--tests={targets}"])
        for node, step_id in (("tooling", "tooling.scripts-test"), ("docs", "docs.tests")):
            steps = {step.id: step for step in table.nodes[node].commands}
            self.assertEqual(steps[step_id].related["run"],
                             ["python3", "-m", "unittest", "{targets}"])
        for node in table.nodes.values():
            for step in node.commands:
                run = (step.related or {}).get("run") or []
                for index, item in enumerate(run):
                    if item == "{targets}" and index and run[index - 1] in ("--tests", "-p", "-k"):
                        self.fail("%s passes every target into one %r flag" % (step.id, run[index - 1]))


if __name__ == "__main__":
    unittest.main()
