"""Tests for the command-table drift check (design D13).

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
        "doc": name + "/AGENTS.md",
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
        table["nodes"][name] = {"commands": [_node(name)], "excluded": []}
    return table


def _doc(name):
    return (
        "# %s/AGENTS.md\n\n## Build & Test\n\nRun from `%s/`:\n\n```bash\n./gradlew lintDebug\n```\n"
        "\n## Rules\n\nNone.\n" % (name, name)
    )


def _producer(pid, doc, run):
    return {"id": pid, "cwd": ".", "run": list(run), "doc": doc, "requires": [], "inputs": []}


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


class MatchingTest(Base):
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

        # A missing doc.
        table = _table(("android",))
        del table["nodes"]["android"]["commands"][0]["doc"]
        found = self.fixture(table=table).findings()
        self.assert_finding(found, "scripts/verify-commands.json", "has no doc")

        # Neither inputs nor "reuse": false.
        table = _table(("android",))
        del table["nodes"]["android"]["commands"][0]["inputs"]
        found = self.fixture(table=table).findings()
        self.assert_finding(found, "scripts/verify-commands.json", "reuse")

        # The table's nodes must equal the component map's nodes.
        table = _table(("android", "rust"))
        found = self.fixture(nodes=("android",), table=table).findings()
        self.assert_finding(found, "scripts/verify-commands.json", "nodes differ")

    def test_forward_exact_argv_and_cwd(self):
        docs = {
            "android/AGENTS.md": (
                "# android/AGENTS.md\n\n## Rules\n\n"
                "- `.agents/rules/android-lint-before-push.md` — the gate.\n"
            ),
            ".agents/rules/android-lint-before-push.md": (
                "## Gate\n\n```bash\npython3 docs/checks/check_offline.py --root .\n```\n"
            ),
        }
        table = _table(("android",))
        table["nodes"]["android"]["commands"][0].update(
            {
                "run": ["python3", "docs/checks/check_offline.py", "--root", "."],
                "cwd": ".",
                "doc": ".agents/rules/android-lint-before-push.md",
            }
        )
        self.assertEqual(
            self.fixture(table=table, docs=docs).findings(),
            [],
        )
        # The same argv from another working directory is not the documented command.
        table["nodes"]["android"]["commands"][0]["cwd"] = "android"
        found = self.fixture(table=table, docs=docs).findings()
        self.assert_finding(found, "scripts/verify-commands.json", "no matching command")

    def test_forward_gradle_task_subset(self):
        docs = {
            "android/AGENTS.md": (
                "## Build & Test\n\nRun from `android/`:\n\n"
                "```bash\n./gradlew spotlessCheck detekt lintDebug\n```\n"
            )
        }
        table = _table(("android",))
        template = table["nodes"]["android"]["commands"][0]
        table["nodes"]["android"]["commands"] = [
            dict(template, id="android." + task, run=["./gradlew", task])
            for task in ("spotlessCheck", "detekt", "lintDebug")
        ]
        self.assertEqual(self.fixture(table=table, docs=docs).findings(), [])
        table["nodes"]["android"]["commands"][2]["run"] = ["./gradlew", "lintSomething"]
        found = self.fixture(table=table, docs=docs).findings()
        self.assert_finding(found, "scripts/verify-commands.json", "no matching command")

    def test_reverse_missing_entry_message(self):
        docs = {
            "android/AGENTS.md": (
                "## Build & Test\n\nRun from `android/`:\n\n```bash\n./gradlew lintDebug\n"
                "npm run test:e2e\n```\n"
            )
        }
        found = self.fixture(docs=docs).findings()
        message = self.assert_finding(found, "android/AGENTS.md", "npm run test:e2e")
        self.assertIn("add it to scripts/verify-commands.json or exclude it", message)

    def test_wrong_working_directory(self):
        table = _table(("android",))
        table["nodes"]["android"]["commands"][0]["cwd"] = "."
        found = self.fixture(table=table).findings()
        messages = [finding.message for finding in found if finding.path == "android/AGENTS.md"]
        self.assertEqual(len(messages), 1, found)
        self.assertIn("`android`", messages[0])
        self.assertIn("from `.`", messages[0])

    def test_exclusion_first(self):
        docs = {
            "android/AGENTS.md": (
                "## Build & Test\n\nRun from `android/`:\n\n"
                "```bash\n./gradlew installGitCliff lintDebug\n```\n"
            )
        }
        table = _table(("android",))
        # Without the exclusion the union of the entries' tasks cannot cover the setup task.
        found = self.fixture(table=table, docs=docs).findings()
        self.assert_finding(found, "android/AGENTS.md", "installGitCliff")
        table["nodes"]["android"]["excluded"] = [
            {
                "doc": "android/AGENTS.md",
                "cwd": "android",
                "line": "./gradlew installGitCliff lintDebug",
                "why": "setup, not a gate",
            }
        ]
        self.assertEqual(self.fixture(table=table, docs=docs).findings(), [])

    def test_excluded_compound_line_covers_its_tasks(self):
        # The bridge shape: one documented line installs dependencies and runs four gates. The
        # exclusion names the whole line (D13 checks it first), and the gates are steps too.
        docs = {
            "android/AGENTS.md": (
                "## Build & Test\n\nRun from `android/`:\n\n```bash\n"
                "./gradlew installDeps gateFormat gateLint gateTypecheck gateTest\n"
                "```\n"
            )
        }
        table = _table(("android",))
        template = table["nodes"]["android"]["commands"][0]
        table["nodes"]["android"]["commands"] = [
            dict(template, id="android." + task, run=["./gradlew", task])
            for task in ("gateFormat", "gateLint", "gateTypecheck", "gateTest")
        ]
        table["nodes"]["android"]["excluded"] = [
            {
                "doc": "android/AGENTS.md",
                "cwd": "android",
                "line": "./gradlew installDeps gateFormat gateLint gateTypecheck gateTest",
                "why": "setup, not a gate: the dependency install",
            }
        ]
        self.assertEqual(self.fixture(table=table, docs=docs).findings(), [])

    def test_gradle_union_per_directory(self):
        docs = {
            "android/AGENTS.md": (
                "## Build & Test\n\nRun from `android/`:\n\n"
                "```bash\n./gradlew firstTask secondTask\n```\n"
            )
        }
        table = _table(("android",))
        table["nodes"]["android"]["commands"] = [
            {
                "id": "android.first",
                "kind": "check",
                "cwd": "android",
                "run": ["./gradlew", "firstTask"],
                "doc": "android/AGENTS.md",
                "requires": [],
                "inputs": [],
            },
            {
                "id": "android.second",
                "kind": "check",
                "cwd": ".",
                "run": ["./gradlew", "secondTask"],
                "doc": "android/AGENTS.md",
                "requires": [],
                "inputs": [],
            },
        ]
        found = self.fixture(table=table, docs=docs).findings()
        self.assert_finding(found, "android/AGENTS.md", "secondTask")
        # The same entries from the line's directory cover the line.
        table["nodes"]["android"]["commands"][1]["cwd"] = "android"
        self.assertEqual(self.fixture(table=table, docs=docs).findings(), [])

    def test_argument_line_needs_exact_entry(self):
        docs = {
            "android/AGENTS.md": (
                "## Build & Test\n\nRun from `android/`:\n\n```bash\n"
                "./gradlew testDebugUnitTest\n"
                "./gradlew testDebugUnitTest --tests \"me.nagaev.FocusedTest\"\n"
                "```\n"
            )
        }
        table = _table(("android",))
        table["nodes"]["android"]["commands"][0]["run"] = ["./gradlew", "testDebugUnitTest"]
        found = self.fixture(table=table, docs=docs).findings()
        self.assert_finding(found, "android/AGENTS.md", "testDebugUnitTest")
        table["nodes"]["android"]["excluded"] = [
            {
                "doc": "android/AGENTS.md",
                "cwd": "android",
                "line": './gradlew testDebugUnitTest --tests "me.nagaev.FocusedTest"',
                "why": "focused example",
            }
        ]
        self.assertEqual(self.fixture(table=table, docs=docs).findings(), [])

    def test_documented_glob_covers_expanded_argv(self):
        # The table lists the files explicitly; the doc keeps the shell glob the verifier's argv
        # cannot expand.
        docs = {
            "android/AGENTS.md": (
                "## Build & Test\n\n```bash\nnode --test verify/test/*.test.mjs\n```\n"
            )
        }
        table = _table(("android",))
        table["nodes"]["android"]["commands"][0]["cwd"] = "."
        table["nodes"]["android"]["commands"][0]["run"] = [
            "node",
            "--test",
            "verify/test/a.test.mjs",
            "verify/test/b.test.mjs",
        ]
        self.assertEqual(self.fixture(table=table, docs=docs).findings(), [])

    def test_producer_doc_ownership(self):
        nodes = ("android", "rust")
        docs = {
            "android/AGENTS.md": "# android/AGENTS.md\n\n## Rules\n\nNone.\n",
            "rust/AGENTS.md": "## Build & Test\n\n```bash\n./gradlew rustWasm\n```\n",
        }
        table = _table(nodes)
        table["nodes"]["android"]["commands"] = []
        table["nodes"]["rust"]["commands"] = []
        table["producers"] = [_producer("rust.wasm", "rust/AGENTS.md", ["./gradlew", "rustWasm"])]
        # A producer counts toward the reverse coverage of the node doc it names.
        self.assertEqual(self.fixture(nodes=nodes, table=table, docs=docs).findings(), [])
        # A producer's doc may name a rule another node lists.
        docs["android/AGENTS.md"] = (
            "# android/AGENTS.md\n\n## Rules\n\n"
            "- `.agents/rules/android-lint-before-push.md` — the gate.\n"
        )
        docs[".agents/rules/android-lint-before-push.md"] = (
            "## Gate\n\n```bash\n./gradlew rustWasm\n```\n"
        )
        docs["rust/AGENTS.md"] = "## Build & Test\n\nNone.\n"
        table["producers"][0]["doc"] = ".agents/rules/android-lint-before-push.md"
        self.assertEqual(self.fixture(nodes=nodes, table=table, docs=docs).findings(), [])
        # A doc that is neither an AGENTS.md nor a listed rule is a finding.
        table["producers"][0]["doc"] = "docs/notes.md"
        docs["docs/notes.md"] = "```bash\n./gradlew rustWasm\n```\n"
        found = self.fixture(nodes=nodes, table=table, docs=docs).findings()
        self.assert_finding(found, "scripts/verify-commands.json", "docs/notes.md")


if __name__ == "__main__":
    unittest.main()
