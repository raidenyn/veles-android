"""Tests for the command-table check (design D1): its structure and its duplicates.

Every fixture is a plain directory: the check reads files only, so no git repository is
needed. Stdlib only; Python 3.8 compatible.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

CHECKS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CHECKS)

from verify_table import DocCommand, findings, read_doc_commands  # noqa: E402

SINCE = "0" * 40


def _write(root, rel, text):
    full = os.path.join(root, *rel.split("/"))
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _node(name):
    return {
        "id": name + ".lint",
        "kind": "check",
        "cwd": name,
        "run": ["./gradlew", "lintDebug"],
        "requires": [],
        "inputs": [],
    }


def _table(nodes, producers=None):
    table = {
        "version": 1,
        "requirements": {},
        "producers": list(producers or []),
        "nodes": {},
    }
    for name in nodes:
        table["nodes"][name] = {"commands": [_node(name)]}
    return table


def _doc(name):
    """A default doc whose build line is not in the table, so it is never a duplicate."""
    return (
        "# %s/AGENTS.md\n\n## Build & Test\n\nRun from `%s/`:\n\n```bash\n./gradlew assembleDebug\n```\n"
        "\n## Rules\n\nNone.\n" % (name, name)
    )


def _producer(pid, run):
    return {"id": pid, "cwd": ".", "run": list(run), "requires": [], "inputs": []}


class Fixture(object):
    """A temporary tree with a component map, a table and its docs."""

    def __init__(self, tmp, nodes, table, docs, overrides=None):
        self.root = tempfile.mkdtemp(prefix="verify-table-", dir=tmp)
        cmap = {
            "since": SINCE,
            "nodes": dict((name, {"paths": [name + "/"]}) for name in nodes),
        }
        if overrides is not None:
            cmap["global-overrides"] = overrides
        _write(self.root, ".github/components.json", json.dumps(cmap, indent=2) + "\n")
        _write(self.root, "scripts/verify-commands.json", json.dumps(table, indent=2) + "\n")
        for path, text in docs.items():
            _write(self.root, path, text)

    def findings(self):
        return findings(self.root)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="veles-verify-table-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def fixture(self, nodes=("android",), table=None, docs=None, overrides=None):
        if table is None:
            table = _table(nodes)
        if docs is None:
            docs = dict((name + "/AGENTS.md", _doc(name)) for name in nodes)
        return Fixture(self.tmp, nodes, table, docs, overrides)

    def assert_finding(self, findings_list, path, fragment):
        matching = [
            finding
            for finding in findings_list
            if finding.path == path and fragment in finding.message
        ]
        self.assertEqual(
            len(matching),
            1,
            "expected one finding for %s containing %r, got %r"
            % (path, fragment, findings_list),
        )
        self.assertEqual(matching[0].severity, "error", matching[0])
        return matching[0].message


class ReadingTest(unittest.TestCase):
    def commands(self, text):
        return read_doc_commands(text, "AGENTS.md")

    def test_only_shell_fences(self):
        text = (
            "```bash\nbash-command\n```\n"
            "```sh\nsh-command\n```\n"
            "```shell\nshell-command\n```\n"
            "```\nempty-info-command\n```\n"
            "```text\ntext-command\n```\n"
            "```console\nconsole-command\n```\n"
            "```powershell\npowershell-command\n```\n"
        )
        self.assertEqual(
            [command.argv for command in self.commands(text)],
            [
                ["bash-command"],
                ["sh-command"],
                ["shell-command"],
                ["empty-info-command"],
            ],
        )

    def test_comments_stripped(self):
        text = (
            "```bash\n"
            "./gradlew lintDebug   # the gate\n"
            "python3 x.py --message \"a # b\"\n"
            "# a whole-line comment\n"
            "```\n"
        )
        commands = self.commands(text)
        self.assertEqual(
            [command.argv for command in commands],
            [["./gradlew", "lintDebug"], ["python3", "x.py", "--message", "a # b"]],
        )
        self.assertEqual(commands[0].line, "./gradlew lintDebug")

    def test_continuation_joined(self):
        text = (
            "```bash\n"
            "log show --info \\\n"
            "  --predicate 'process == \"tccd\" OR \\\n"
            "  eventMessage CONTAINS \"x\"'\n"
            "```\n"
        )
        commands = self.commands(text)
        self.assertEqual(len(commands), 1, commands)
        self.assertEqual(
            commands[0].argv,
            ["log", "show", "--info", "--predicate", 'process == "tccd" OR eventMessage CONTAINS "x"'],
        )
        self.assertNotIn("\\", commands[0].line)
        self.assertNotIn("  ", commands[0].line)

    def test_cd_sets_cwd(self):
        text = (
            "## Build & Test\n\n"
            "First block:\n\n"
            "```bash\ncd web-extension\nnpm test\n```\n"
            "Second block:\n\n"
            "```bash\nnpm run build\n```\n"
        )
        commands = self.commands(text)
        self.assertEqual(
            [(command.argv, command.cwd) for command in commands],
            [(["npm", "test"], "web-extension"), (["npm", "run", "build"], ".")],
        )
        self.assertEqual(commands[0].line, "npm test")

    def test_keywords_and_assignments_skipped(self):
        text = (
            "```bash\n"
            "if true; then\n"
            "  FOO=bar ./run.sh\n"
            "./run.sh\n"
            "fi\n"
            "for x in a b; do\n"
            "  echo \"$x\"\n"
            "done\n"
            "while false; do\n"
            "  :\n"
            "done\n"
            "exit 1\n"
            "```\n"
        )
        self.assertEqual([command.argv for command in self.commands(text)], [["./run.sh"]])

    def test_run_from_sentence(self):
        text = (
            "## Build & Test\n\n"
            "Run from `android/`; the root alias cannot carry arguments.\n\n"
            "```bash\n"
            "./gradlew lintDebug\n"
            "```\n"
        )
        commands = self.commands(text)
        self.assertEqual([(command.argv, command.cwd) for command in commands],
                         [(["./gradlew", "lintDebug"], "android")])

    def test_root_default(self):
        text = "## Commands\n\nRun from the repository root:\n\n```bash\n./gradlew lintDebug\n```\n"
        commands = self.commands(text)
        self.assertEqual([(command.argv, command.cwd) for command in commands],
                         [(["./gradlew", "lintDebug"], ".")])
        self.assertIsInstance(commands[0], DocCommand)


class StructuralTest(Base):
    def test_structural_subset(self):
        # Duplicate ids in two nodes.
        table = _table(("android", "rust"))
        table["nodes"]["rust"]["commands"][0]["id"] = "android.lint"
        found = self.fixture(nodes=("android", "rust"), table=table).findings()
        self.assert_finding(found, "scripts/verify-commands.json", "duplicate id")

        # An unknown kind.
        table = _table(("android",))
        table["nodes"]["android"]["commands"][0]["kind"] = "banana"
        found = self.fixture(table=table).findings()
        self.assert_finding(found, "scripts/verify-commands.json", "unknown kind")

        # An unresolved requirement reference.
        table = _table(("android",))
        table["nodes"]["android"]["commands"][0]["requires"] = ["ghost"]
        found = self.fixture(table=table).findings()
        self.assert_finding(found, "scripts/verify-commands.json", "unknown requirement")

        # Neither inputs nor "reuse": false.
        table = _table(("android",))
        del table["nodes"]["android"]["commands"][0]["inputs"]
        found = self.fixture(table=table).findings()
        self.assert_finding(found, "scripts/verify-commands.json", "reuse")

        # The table's nodes must equal the component map's nodes.
        table = _table(("android", "rust"))
        found = self.fixture(nodes=("android",), table=table).findings()
        self.assert_finding(found, "scripts/verify-commands.json", "nodes differ")

    def test_doc_key_rejected(self):
        table = _table(("android",))
        table["nodes"]["android"]["commands"][0]["doc"] = "android/AGENTS.md"
        message = self.assert_finding(
            self.fixture(table=table).findings(), "scripts/verify-commands.json", "`doc` field"
        )
        self.assertIn("android.lint", message)

        table = _table(("android",))
        table["producers"] = [_producer("rust.wasm", ["./gradlew", "rustWasm"])]
        table["producers"][0]["doc"] = "rust/AGENTS.md"
        message = self.assert_finding(
            self.fixture(table=table).findings(), "scripts/verify-commands.json", "`doc` field"
        )
        self.assertIn("rust.wasm", message)

    def test_excluded_key_rejected(self):
        table = _table(("android",))
        table["nodes"]["android"]["excluded"] = []
        message = self.assert_finding(
            self.fixture(table=table).findings(), "scripts/verify-commands.json", "`excluded` list"
        )
        self.assertIn("`android`", message)


class DuplicateTest(Base):
    def test_doc_repeats_check(self):
        table = _table(("android",))
        template = table["nodes"]["android"]["commands"][0]
        table["nodes"]["android"]["commands"] = [
            dict(template, id="android." + task, run=["./gradlew", task])
            for task in ("spotlessCheck", "detekt", "lintDebug")
        ]
        docs = {
            "android/AGENTS.md": (
                "## Build & Test\n\nRun from `android/`:\n\n"
                "```bash\n./gradlew spotlessCheck detekt lintDebug\n```\n"
            )
        }
        found = self.fixture(table=table, docs=docs).findings()
        self.assertEqual(len(found), 1, found)
        message = self.assert_finding(
            found,
            "android/AGENTS.md",
            "`./gradlew spotlessCheck detekt lintDebug` is run by verify.py",
        )
        for identifier in ("android.spotlessCheck", "android.detekt", "android.lintDebug"):
            self.assertIn(identifier, message)
        self.assertIn("remove it from the doc", message)

    def test_targeted_line_allowed(self):
        table = _table(("android",))
        table["nodes"]["android"]["commands"][0]["run"] = ["./gradlew", "testDebugUnitTest"]
        docs = {
            "android/AGENTS.md": (
                "## Build & Test\n\nRun from `android/`:\n\n```bash\n"
                './gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"'
                "\n```\n"
            )
        }
        self.assertEqual(self.fixture(table=table, docs=docs).findings(), [])

    def test_build_and_producer_lines_allowed(self):
        table = _table(("rust",))
        table["producers"] = [_producer("rust.wasm", ["./gradlew", "rustWasm"])]
        docs = {
            "rust/AGENTS.md": (
                "## Build & Test\n\n```bash\n./gradlew rustWasm\n./gradlew rustJni\n```\n"
            )
        }
        self.assertEqual(
            self.fixture(nodes=("rust",), table=table, docs=docs).findings(), []
        )

    def test_documented_command_missing_from_table_passes(self):
        table = _table(("web-extension",))
        table["nodes"]["web-extension"]["commands"][0]["run"] = ["npm", "run", "lint"]
        docs = {
            "web-extension/AGENTS.md": (
                "## Build & Test\n\nRun from `web-extension/`:\n\n```bash\nnpm run test:e2e\n```\n"
            )
        }
        self.assertEqual(
            self.fixture(nodes=("web-extension",), table=table, docs=docs).findings(), []
        )

    def test_wrong_working_directory_not_duplicate(self):
        table = _table(("android",))
        table["nodes"]["android"]["commands"][0]["run"] = ["./gradlew", "testDebugUnitTest"]
        docs = {
            "android/AGENTS.md": (
                "## Build & Test\n\n```bash\n./gradlew testDebugUnitTest\n```\n"
            )
        }
        # The doc line runs at the repository root; the entry runs from android/.
        self.assertEqual(self.fixture(table=table, docs=docs).findings(), [])

    def test_excluded_setup_line_passes(self):
        table = _table(("tooling",))
        table["nodes"]["tooling"]["commands"][0]["run"] = ["./gradlew", "scriptsTest"]
        docs = {
            "tooling/AGENTS.md": (
                "## Build & Test\n\n```bash\n./gradlew installGitCliff\n```\n"
            )
        }
        self.assertEqual(
            self.fixture(nodes=("tooling",), table=table, docs=docs).findings(), []
        )

    def test_value_less_gradle_option_ignored(self):
        table = _table(("android",))
        for flag in ("-q", "--offline"):
            with self.subTest(flag=flag):
                docs = {
                    "android/AGENTS.md": (
                        "## Build & Test\n\nRun from `android/`:\n\n```bash\n"
                        "./gradlew %s lintDebug\n```\n" % flag
                    )
                }
                found = self.fixture(table=table, docs=docs).findings()
                message = self.assert_finding(
                    found, "android/AGENTS.md", "is run by verify.py"
                )
                self.assertIn("android.lint", message)

    def test_quoted_duplicate(self):
        docs = {
            "android/AGENTS.md": (
                "## Build & Test\n\nRun from `android/`:\n\n```bash\n./gradlew 'lintDebug'\n```\n"
            )
        }
        found = self.fixture(docs=docs).findings()
        message = self.assert_finding(found, "android/AGENTS.md", "is run by verify.py")
        self.assertIn("android.lint", message)

    def test_rule_line_is_scanned(self):
        docs = {
            "android/AGENTS.md": (
                "# android/AGENTS.md\n\n## Rules\n\n"
                "- `.agents/rules/android-gate.md` — the gate.\n"
            ),
            ".agents/rules/android-gate.md": (
                "## Gate\n\nRun from `android/`:\n\n```bash\n./gradlew lintDebug\n```\n"
            ),
        }
        found = self.fixture(docs=docs).findings()
        self.assert_finding(found, ".agents/rules/android-gate.md", "is run by verify.py")


if __name__ == "__main__":
    unittest.main()
