import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from agent_rules import RuleError, load_rules, parse_rule, render_pointer_block  # noqa: E402

CLI = os.path.join(SCRIPTS, "agent_rules.py")

POINTER = "Commit and PR types: read before choosing a commit or PR type."
SKILLS_LINE = 'skills: ["opsx:apply"]'
COMMANDS_LINE = 'commands: ["\\\\bopenspec\\\\s+instructions\\\\s+finalize\\\\b"]'
GATES_LINE = 'gates: ["\\\\bgit\\\\b[^;&|\\\\n]*?\\\\scommit\\\\b"]'


def rule_text(pointer=POINTER, extra=None, body="Body.\n", lines=None):
    if lines is None:
        lines = ['pointer: "%s"' % pointer, SKILLS_LINE, COMMANDS_LINE, GATES_LINE]
    lines = list(lines) + list(extra or [])
    return "---\n" + "\n".join(lines) + "\n---\n" + body


class ParseRuleTest(unittest.TestCase):
    def test_valid_rule(self):
        text = rule_text()
        rule = parse_rule("repo-commit-types.md", text)
        self.assertEqual(rule.name, "repo-commit-types.md")
        self.assertEqual(rule.pointer, POINTER)
        self.assertEqual(rule.skills, ["opsx:apply"])
        self.assertEqual(
            [r.pattern for r in rule.commands], ["\\bopenspec\\s+instructions\\s+finalize\\b"]
        )
        self.assertEqual([r.pattern for r in rule.gates], ["\\bgit\\b[^;&|\\n]*?\\scommit\\b"])
        self.assertEqual(rule.body, "Body.\n")
        self.assertEqual(
            rule.identity,
            ("repo-commit-types.md", hashlib.sha256(text.encode("utf-8")).hexdigest()),
        )

    def test_block_lists_and_escapes(self):
        text = rule_text(
            lines=[
                'pointer: "Say \\"hi\\" with a \\\\ slash."',
                "skills:",
                '  - "a"',
                '  - "b:c"',
            ]
        )
        rule = parse_rule("x.md", text)
        self.assertEqual(rule.pointer, 'Say "hi" with a \\ slash.')
        self.assertEqual(rule.skills, ["a", "b:c"])
        self.assertEqual(rule.commands, [])
        self.assertEqual(rule.gates, [])

    def test_unsupported_escape(self):
        with self.assertRaises(RuleError):
            parse_rule("x.md", rule_text(lines=['pointer: "p"', 'skills: ["a\\n"]']))

    def test_unknown_key(self):
        with self.assertRaises(RuleError) as caught:
            parse_rule("x.md", rule_text(extra=["sticky: yes"]))
        self.assertIn("sticky", str(caught.exception))

    def test_duplicate_key(self):
        with self.assertRaises(RuleError):
            parse_rule("x.md", rule_text(extra=[GATES_LINE]))

    def test_empty_flow_list(self):
        with self.assertRaises(RuleError):
            parse_rule("x.md", rule_text(lines=['pointer: "p"', "gates: []"]))

    def test_bad_regex(self):
        with self.assertRaises(RuleError):
            parse_rule("x.md", rule_text(lines=['pointer: "p"', 'gates: ["(unbalanced"]']))

    def test_no_trigger(self):
        with self.assertRaises(RuleError):
            parse_rule("x.md", rule_text(lines=['pointer: "p"']))

    def test_missing_pointer(self):
        with self.assertRaises(RuleError):
            parse_rule("x.md", rule_text(lines=[SKILLS_LINE]))

    def test_pointer_must_be_double_quoted(self):
        with self.assertRaises(RuleError):
            parse_rule("x.md", rule_text(lines=["pointer: plain text", SKILLS_LINE]))

    def test_missing_frontmatter(self):
        with self.assertRaises(RuleError):
            parse_rule("x.md", "no frontmatter\n")

    def test_body_boundary(self):
        self.assertEqual(len(parse_rule("x.md", rule_text(body="a" * 4096)).body), 4096)
        with self.assertRaises(RuleError):
            parse_rule("x.md", rule_text(body="a" * 4097))

    def test_body_multibyte(self):
        body = "€" * 1366  # three bytes each, 4,098 in total
        self.assertEqual(len(body.encode("utf-8")), 4098)
        with self.assertRaises(RuleError):
            parse_rule("x.md", rule_text(body=body))

    def test_identity_raw_bytes(self):
        text = rule_text()
        lf = parse_rule("x.md", text)
        crlf = parse_rule("x.md", text.replace("\n", "\r\n"))
        self.assertNotEqual(lf.identity[1], crlf.identity[1])
        self.assertEqual(lf.pointer, crlf.pointer)
        self.assertEqual(lf.skills, crlf.skills)


class LoadRulesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        self.dir = os.path.join(self.root, ".agents", "hooked-rules")

    def write(self, name, data):
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, name), "wb") as handle:
            handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))

    def test_no_directory(self):
        self.assertEqual(load_rules(self.root), ([], []))

    def test_sorted_and_errors(self):
        self.write("repo-b.md", rule_text(pointer="B"))
        self.write("repo-a.md", rule_text(pointer="A"))
        self.write("repo-bad.md", rule_text(extra=["sticky: yes"]))
        self.write("notes.txt", "ignored")
        rules, errors = load_rules(self.root)
        self.assertEqual([r.name for r in rules], ["repo-a.md", "repo-b.md"])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], ".agents/hooked-rules/repo-bad.md")
        self.assertIn("sticky", errors[0][1])

    def test_identity_uses_raw_file_bytes(self):
        raw = rule_text().replace("\n", "\r\n").encode("utf-8")
        self.write("repo-a.md", raw)
        rules, errors = load_rules(self.root)
        self.assertEqual(errors, [])
        self.assertEqual(rules[0].identity, ("repo-a.md", hashlib.sha256(raw).hexdigest()))

    def test_invalid_utf8(self):
        self.write("repo-a.md", b"---\npointer: \"\xff\"\n---\n")
        rules, errors = load_rules(self.root)
        self.assertEqual(rules, [])
        self.assertEqual(len(errors), 1)


class PointerBlockTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        self.agents = os.path.join(self.root, "AGENTS.md")
        os.makedirs(os.path.join(self.root, ".agents", "hooked-rules"))

    def rule(self, name, pointer):
        path = os.path.join(self.root, ".agents", "hooked-rules", name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(rule_text(pointer=pointer))

    def agents_text(self, text=None):
        if text is not None:
            with open(self.agents, "w", encoding="utf-8") as handle:
                handle.write(text)
        with open(self.agents, "r", encoding="utf-8") as handle:
            return handle.read()

    def run_cli(self, mode):
        return subprocess.run(
            [sys.executable, "-I", CLI, "pointers", mode, "--root", self.root],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )

    def test_pointer_block_render(self):
        self.rule("repo-b.md", "Second")
        self.rule("repo-a.md", "First")
        rules, errors = load_rules(self.root)
        self.assertEqual(errors, [])
        block = render_pointer_block(rules)
        self.assertTrue(block.startswith("<!-- hooked-rules:begin -->\n"))
        self.assertTrue(block.endswith("<!-- hooked-rules:end -->"))
        self.assertIn(
            "If your tool did not deliver a rule below, read its file yourself at the moment it names.",
            block,
        )
        lines = block.split("\n")
        first = lines.index("- First — .agents/hooked-rules/repo-a.md")
        second = lines.index("- Second — .agents/hooked-rules/repo-b.md")
        self.assertLess(first, second)

    def test_pointer_write_then_check(self):
        self.rule("repo-a.md", "First")
        self.agents_text("# Agents\n\nIntro.\n")
        self.assertEqual(self.run_cli("--check").returncode, 1)
        self.assertEqual(self.run_cli("--write").returncode, 0)
        text = self.agents_text()
        self.assertTrue(text.startswith("# Agents\n\nIntro.\n"))
        self.assertIn("## Hook-delivered rules", text)
        self.assertIn("- First — .agents/hooked-rules/repo-a.md", text)
        self.assertEqual(self.run_cli("--check").returncode, 0)

    def test_pointer_check_stale(self):
        self.rule("repo-a.md", "First")
        self.agents_text("# Agents\n")
        self.assertEqual(self.run_cli("--write").returncode, 0)
        self.assertEqual(self.run_cli("--check").returncode, 0)
        self.rule("repo-a.md", "Edited")
        self.assertEqual(self.run_cli("--check").returncode, 1)

    def test_pointer_check_missing(self):
        self.rule("repo-a.md", "First")
        self.agents_text("# Agents\n\nNo markers here.\n")
        self.assertEqual(self.run_cli("--check").returncode, 1)

    def test_pointer_write_idempotent(self):
        self.rule("repo-a.md", "First")
        self.agents_text("# Agents\n\n## Rules\n\nText.\n")
        self.assertEqual(self.run_cli("--write").returncode, 0)
        once = self.agents_text()
        self.assertEqual(self.run_cli("--write").returncode, 0)
        self.assertEqual(self.agents_text(), once)

    def test_pointer_write_replaces_only_the_block(self):
        self.rule("repo-a.md", "First")
        self.agents_text("# Agents\n")
        self.assertEqual(self.run_cli("--write").returncode, 0)
        self.agents_text(self.agents_text() + "\n## After\n\nKept.\n")
        self.rule("repo-a.md", "Changed")
        self.assertEqual(self.run_cli("--write").returncode, 0)
        text = self.agents_text()
        self.assertIn("- Changed —", text)
        self.assertNotIn("- First —", text)
        self.assertTrue(text.endswith("## After\n\nKept.\n"))
        self.assertEqual(text.count("## Hook-delivered rules"), 1)

    def test_pointer_invalid_rule_blocks_write(self):
        self.rule("repo-a.md", "First")
        path = os.path.join(self.root, ".agents", "hooked-rules", "repo-bad.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(rule_text(extra=["sticky: yes"]))
        self.agents_text("# Agents\n")
        result = self.run_cli("--write")
        self.assertEqual(result.returncode, 1)
        self.assertIn("repo-bad.md", result.stderr)
        self.assertEqual(self.agents_text(), "# Agents\n")

    def test_commit_types_pointer_keeps_format(self):
        pointer = (
            "Every commit subject and PR title is a Conventional Commit "
            "`type(scope): description`; read the type guide before choosing the type."
        )
        self.rule("repo-commit-types.md", pointer)
        rules, errors = load_rules(self.root)
        self.assertEqual(errors, [])
        self.assertIn(
            "- %s — .agents/hooked-rules/repo-commit-types.md" % pointer,
            render_pointer_block(rules),
        )


if __name__ == "__main__":
    unittest.main()
