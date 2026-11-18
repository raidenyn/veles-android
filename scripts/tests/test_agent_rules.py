import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from agent_rules import (  # noqa: E402
    Decision,
    Event,
    EventError,
    FAILURE_NOTICE,
    FAIL_AFTER_WRITE,
    FileLock,
    LockTimeout,
    RuleError,
    Store,
    StoreError,
    CheckoutError,
    checkout_for,
    discover_checkout,
    hook_main,
    context_key,
    decide,
    handle,
    load_rules,
    parse_event,
    parse_rule,
    render,
    render_pointer_block,
)

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

    def test_oversized_repeat_regex(self):
        with self.assertRaises(RuleError):
            parse_rule("x.md", rule_text(lines=['pointer: "p"', 'gates: ["a{99999999999}"]']))

    def test_deeply_nested_regex(self):
        pattern = "(?:" * 3000 + "a" + ")" * 3000
        with self.assertRaises(RuleError):
            parse_rule("x.md", rule_text(lines=['pointer: "p"', 'commands: ["%s"]' % pattern]))

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

    def test_body_boundary_counts_original_crlf_bytes(self):
        # 4,094 characters plus CRLF are 4,096 bytes on disk and pass; one more character is 4,097.
        parse_rule("x.md", rule_text(body="a" * 4094 + "\r\n"))
        with self.assertRaises(RuleError):
            parse_rule("x.md", rule_text(body="a" * 4095 + "\r\n"))

    def test_body_boundary_counts_original_crlf_file(self):
        crlf = rule_text(body="a" * 4094 + "\r\n").replace("\n", "\r\n").replace("\r\r\n", "\r\n")
        self.assertEqual(len(parse_rule("x.md", crlf).body.encode("utf-8")), 4095)
        over = rule_text(body="a" * 4095 + "\r\n").replace("\n", "\r\n").replace("\r\r\n", "\r\n")
        with self.assertRaises(RuleError):
            parse_rule("x.md", over)

    def test_body_boundary_counts_lone_cr(self):
        parse_rule("x.md", rule_text(body="a" * 4095 + "\r"))
        with self.assertRaises(RuleError):
            parse_rule("x.md", rule_text(body="a" * 4096 + "\r"))

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
        # The markers and one line per rule, in name order: no sentence and no blank line.
        self.assertEqual(
            block,
            "<!-- hooked-rules:begin -->\n"
            "- First — .agents/hooked-rules/repo-a.md\n"
            "- Second — .agents/hooked-rules/repo-b.md\n"
            "<!-- hooked-rules:end -->",
        )

    def test_pointer_write_then_check(self):
        self.rule("repo-a.md", "First")
        self.agents_text("# Agents\n\nIntro.\n")
        self.assertEqual(self.run_cli("--check").returncode, 1)
        self.assertEqual(self.run_cli("--write").returncode, 0)
        self.assertEqual(
            self.agents_text(),
            "# Agents\n\nIntro.\n\n## Hook-delivered rules\n<!-- hooked-rules:begin -->\n"
            "- First — .agents/hooked-rules/repo-a.md\n<!-- hooked-rules:end -->\n",
        )
        self.assertEqual(self.run_cli("--check").returncode, 0)

    def test_pointer_old_layout_is_stale_and_rewritten(self):
        # The earlier layout (a blank line after the heading and a fixed sentence) is stale.
        self.rule("repo-a.md", "First")
        old = (
            "# Agents\n\n## Hook-delivered rules\n\n<!-- hooked-rules:begin -->\n"
            "If your tool did not deliver a rule below, read its file yourself at the moment it names.\n\n"
            "- First — .agents/hooked-rules/repo-a.md\n<!-- hooked-rules:end -->\n\n## After\n"
        )
        self.agents_text(old)
        self.assertEqual(self.run_cli("--check").returncode, 1)
        self.assertEqual(self.run_cli("--write").returncode, 0)
        self.assertEqual(
            self.agents_text(),
            "# Agents\n\n## Hook-delivered rules\n<!-- hooked-rules:begin -->\n"
            "- First — .agents/hooked-rules/repo-a.md\n<!-- hooked-rules:end -->\n\n## After\n",
        )
        self.assertEqual(self.run_cli("--check").returncode, 0)

    def test_pointer_blank_line_after_heading_fails_and_write_repairs_it(self):
        self.rule("repo-a.md", "First")
        self.agents_text("# Agents\n")
        self.assertEqual(self.run_cli("--write").returncode, 0)
        good = self.agents_text()
        self.agents_text(good.replace("## Hook-delivered rules\n", "## Hook-delivered rules\n\n"))
        self.assertEqual(self.run_cli("--check").returncode, 1)
        self.assertEqual(self.run_cli("--write").returncode, 0)
        self.assertEqual(self.agents_text(), good)

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

    def test_pointer_check_fails_without_heading_and_write_repairs_it(self):
        self.rule("repo-a.md", "First")
        self.agents_text("# Agents\n")
        self.assertEqual(self.run_cli("--write").returncode, 0)
        good = self.agents_text()
        self.agents_text(good.replace("## Hook-delivered rules\n", ""))
        self.assertNotIn("## Hook-delivered rules", self.agents_text())
        self.assertEqual(self.run_cli("--check").returncode, 1)
        self.assertEqual(self.run_cli("--write").returncode, 0)
        self.assertEqual(self.agents_text(), good)
        self.assertEqual(self.run_cli("--check").returncode, 0)

    def test_pointer_check_fails_when_heading_is_not_before_the_block(self):
        self.rule("repo-a.md", "First")
        self.agents_text("# Agents\n")
        self.assertEqual(self.run_cli("--write").returncode, 0)
        good = self.agents_text()
        moved = good.replace("## Hook-delivered rules\n", "") + "\n## Hook-delivered rules\n"
        self.agents_text(moved)
        self.assertEqual(self.run_cli("--check").returncode, 1)

    def test_pointer_containing_marker_text_is_idempotent(self):
        self.rule("repo-a.md", "See <!-- hooked-rules:end --> and <!-- hooked-rules:begin --> here")
        self.agents_text("# Agents\n")
        self.assertEqual(self.run_cli("--write").returncode, 0)
        once = self.agents_text()
        self.assertEqual(self.run_cli("--write").returncode, 0)
        self.assertEqual(self.agents_text(), once)
        self.assertEqual(self.run_cli("--check").returncode, 0)

    def test_pointer_unbalanced_markers_fail(self):
        self.rule("repo-a.md", "First")
        self.agents_text("# Agents\n\n## Hook-delivered rules\n<!-- hooked-rules:end -->\n")
        self.assertEqual(self.run_cli("--check").returncode, 1)
        self.assertEqual(self.run_cli("--write").returncode, 1)

    def test_stale_block_is_replaced_in_place_for_every_line_ending(self):
        self.rule("repo-a.md", "Fresh")
        template = (
            "# Agents\n\n## Hook-delivered rules\n"
            "<!-- hooked-rules:begin -->\n"
            "- %s \u2014 .agents/hooked-rules/repo-a.md\n"
            "<!-- hooked-rules:end -->\n\n## After\n\nKept.\n"
        )
        for eol in ("\n", "\r\n", "\r"):
            with self.subTest(eol=repr(eol)):
                with open(self.agents, "w", encoding="utf-8", newline="") as handle:
                    handle.write((template % "Stale").replace("\n", eol))
                self.assertEqual(self.run_cli("--check").returncode, 1)
                self.assertEqual(self.run_cli("--write").returncode, 0)
                with open(self.agents, "r", encoding="utf-8", newline="") as handle:
                    text = handle.read()
                self.assertEqual(text, (template % "Fresh").replace("\n", eol))
                self.assertEqual(text.count("<!-- hooked-rules:begin -->"), 1)
                self.assertEqual(self.run_cli("--check").returncode, 0)

    def test_missing_block_is_appended_in_the_file_line_ending(self):
        self.rule("repo-a.md", "First")
        for eol in ("\n", "\r\n", "\r"):
            with self.subTest(eol=repr(eol)):
                with open(self.agents, "w", encoding="utf-8", newline="") as handle:
                    handle.write("# Agents\n".replace("\n", eol))
                self.assertEqual(self.run_cli("--write").returncode, 0)
                with open(self.agents, "r", encoding="utf-8", newline="") as handle:
                    text = handle.read()
                self.assertEqual(text.replace("\r\n", "\n").replace("\r", "\n").count("\n"), text.count(eol))
                self.assertEqual(self.run_cli("--check").returncode, 0)

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


class IsolatedGitTest(unittest.TestCase):
    """Runs git in a temporary directory that no developer configuration can influence."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="veles-agent-rules-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        config = os.path.join(self.tmp, "gitconfig-empty")
        with open(config, "w", encoding="utf-8") as handle:
            handle.write("")
        self._saved_env = {}
        for name, value in (("GIT_CONFIG_GLOBAL", config), ("GIT_CONFIG_NOSYSTEM", "1")):
            self._saved_env[name] = os.environ.get(name)
            os.environ[name] = value
        self.addCleanup(self._restore_git_env)

    def _restore_git_env(self):
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def make_dir(self, *parts):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def hooked_rules(self, root):
        os.makedirs(os.path.join(root, ".agents", "hooked-rules"), exist_ok=True)

    def git_init(self, root):
        subprocess.run(["git", "init", "-q", root], check=True)


CLAUDE_BASE = {"session_id": "s1", "cwd": "/work/repo"}


def claude(name, **fields):
    payload = dict(CLAUDE_BASE)
    payload["hook_event_name"] = name
    payload.update(fields)
    return payload


class ParseEventTest(unittest.TestCase):
    def test_parse_claude_pre_tool_use(self):
        event = parse_event(
            "claude",
            claude("PreToolUse", tool_name="Bash", tool_input={"command": "git commit -m x"}),
        )
        self.assertEqual(event.kind, "tool-before")
        self.assertEqual(event.command, "git commit -m x")
        self.assertEqual(event.tool, "Bash")
        self.assertEqual(event.session, "s1")
        self.assertEqual(event.cwd, "/work/repo")
        self.assertIsNone(event.agent)
        self.assertIsNone(event.ok)

    def test_parse_claude_post_bash_is_success(self):
        event = parse_event(
            "claude",
            claude("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}, tool_response={}),
        )
        self.assertEqual(event.kind, "tool-after")
        self.assertEqual(event.command, "ls")
        self.assertIs(event.ok, True)

    def test_parse_claude_post_skill_and_read(self):
        skill = parse_event(
            "claude",
            claude("PostToolUse", tool_name="Skill", tool_input={"skill": "opsx:apply"}),
        )
        self.assertEqual(skill.skill, "opsx:apply")
        self.assertIsNone(skill.command)
        read = parse_event(
            "claude",
            claude("PostToolUse", tool_name="Read", tool_input={"file_path": "/a/skills/x/SKILL.md"}),
        )
        self.assertEqual(read.path, "/a/skills/x/SKILL.md")
        self.assertIsNone(read.skill)

    def test_parse_claude_prompt_compact_and_agent(self):
        prompt = parse_event("claude", claude("UserPromptSubmit", prompt="/opsx:apply now"))
        self.assertEqual((prompt.kind, prompt.prompt), ("prompt", "/opsx:apply now"))
        compact = parse_event("claude", claude("PreCompact", trigger="auto", agent_id="a1"))
        self.assertEqual((compact.kind, compact.agent), ("compacted", "a1"))

    def test_parse_claude_rejects_unknown_or_malformed(self):
        for payload in (
            claude("SessionStart"),
            {"cwd": "/x", "session_id": "s"},
            claude("PreCompact", session_id=None),
            ["not", "a", "dict"],
        ):
            with self.assertRaises(EventError):
                parse_event("claude", payload)
        with self.assertRaises(EventError):
            parse_event("nope", claude("PreCompact"))

    def test_parse_plain_tool_before(self):
        event = parse_event(
            "plain",
            {
                "event": "tool-before", "session": "root", "agent": "child", "cwd": "/work/repo",
                "tool": "shell", "command": "git commit",
            },
        )
        self.assertEqual(event.kind, "tool-before")
        self.assertEqual((event.session, event.agent, event.tool, event.command),
                         ("root", "child", "shell", "git commit"))

    def test_parse_plain_void(self):
        event = parse_event(
            "plain", {"event": "void", "session": "root", "cwd": "/work/repo", "write_ids": [3, 4]}
        )
        self.assertEqual(event.kind, "void")
        self.assertEqual(list(event.write_ids), [3, 4])

    def test_parse_plain_after_fields(self):
        event = parse_event(
            "plain",
            {"event": "tool-after", "session": "r", "cwd": "/c", "ok": False, "skill": "x",
             "path": "p", "prompt": "q"},
        )
        self.assertEqual((event.ok, event.skill, event.path, event.prompt), (False, "x", "p", "q"))

    def test_parse_plain_void_requires_write_ids(self):
        base = {"event": "void", "session": "r", "cwd": "/c"}
        for write_ids in (None, "missing", [], 5, "1", {"a": 1}, ["1"], [1, "2"], [True], [1.5]):
            payload = dict(base)
            if write_ids != "missing":
                payload["write_ids"] = write_ids
            if write_ids == []:
                self.assertEqual(list(parse_event("plain", payload).write_ids), [])
                continue
            with self.assertRaises(EventError, msg=repr(write_ids)):
                parse_event("plain", payload)
        valid = parse_event("plain", dict(base, write_ids=[1, 2]))
        self.assertEqual(list(valid.write_ids), [1, 2])

    def test_parse_plain_rejects_bad_input(self):
        base = {"event": "prompt", "session": "r", "cwd": "/c"}
        bad = [
            dict(base, event="PreToolUse"),
            dict(base, event="allow"),
            dict(base, session=None),
            dict(base, cwd=3),
            dict(base, ok="yes"),
            dict(base, write_ids=["1"]),
            dict(base, write_ids=[True]),
            dict(base, write_ids=5),
            dict(base, command=7),
        ]
        for payload in bad:
            with self.assertRaises(EventError, msg=payload):
                parse_event("plain", payload)
        with self.assertRaises(EventError):
            parse_event("plain", ["x"])

    def test_large_tool_response(self):
        huge = "x" * (5 * 1024 * 1024)
        payload = claude(
            "PostToolUse", tool_name="Bash", tool_input={"command": "make"},
            tool_response={"stdout": huge, "stderr": "", "interrupted": False},
        )
        event = parse_event("claude", json.loads(json.dumps(payload)))
        self.assertIsInstance(event, Event)
        self.assertEqual(event.command, "make")
        self.assertIs(event.ok, True)
        for value in event:
            if isinstance(value, str):
                self.assertLess(len(value), 1000)
        self.assertLess(len(repr(event)), 1000)


class RenderTest(unittest.TestCase):
    KINDS = ("tool-before", "tool-after", "prompt", "compacted", "void")
    NAMES = {
        "tool-before": "PreToolUse", "tool-after": "PostToolUse",
        "prompt": "UserPromptSubmit", "compacted": "PreCompact", "void": "void",
    }

    def event(self, kind):
        return Event(kind, "s", None, "/c", None, None, None, None, None, None, None)

    def test_render_pass_is_empty(self):
        for kind in self.KINDS:
            for fmt in ("claude", "plain"):
                self.assertEqual(render(fmt, self.event(kind), Decision("pass", "", [])), "")

    def test_render_claude_deny_and_context(self):
        deny = json.loads(render("claude", self.event("tool-before"), Decision("deny", "stop", [])))
        self.assertEqual(
            deny,
            {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": "stop"}},
        )
        for kind in ("tool-before", "tool-after", "prompt"):
            context = json.loads(render("claude", self.event(kind), Decision("context", "hi", [])))
            self.assertEqual(
                context,
                {"hookSpecificOutput": {
                    "hookEventName": self.NAMES[kind], "additionalContext": "hi"}},
            )

    def test_render_claude_deny_needs_tool_before(self):
        with self.assertRaises(ValueError):
            render("claude", self.event("prompt"), Decision("deny", "stop", []))

    def test_render_never_allow(self):
        entries = [(("r.md", "ab" * 32), 7)]
        for kind in self.KINDS:
            for fmt in ("claude", "plain"):
                for decision in ("pass", "context", "deny"):
                    try:
                        out = render(fmt, self.event(kind), Decision(decision, "text", entries))
                    except ValueError:
                        continue
                    self.assertNotIn("allow", out, (kind, fmt, decision))
        self.assertNotIn("allow", render("claude", self.event("tool-before"),
                                         Decision("deny", "text", [])))

    def test_plain_entries(self):
        entries = [(("a.md", "11" * 32), 5), (("b.md", "22" * 32), 6)]
        out = json.loads(render("plain", self.event("tool-before"), Decision("deny", "why", entries)))
        self.assertEqual(
            out,
            {"decision": "deny", "text": "why",
             "entries": [{"rule": ["a.md", "11" * 32], "write_id": 5},
                         {"rule": ["b.md", "22" * 32], "write_id": 6}]},
        )

    def test_render_rejects_unknown(self):
        with self.assertRaises(ValueError):
            render("xml", self.event("prompt"), Decision("pass", "", []))
        with self.assertRaises(ValueError):
            render("plain", self.event("prompt"), Decision("allow", "", []))


class CheckoutForTest(IsolatedGitTest):
    def test_inside_checkout(self):
        root = self.make_dir("repo")
        self.git_init(root)
        self.hooked_rules(root)
        sub = self.make_dir("repo", "a", "b")
        result = checkout_for(sub)
        self.assertEqual(result, (root, os.path.join(root, ".git")))
        self.assertTrue(os.path.isabs(result[1]))

    def test_common_dir_of_linked_worktree(self):
        root = self.make_dir("repo")
        self.git_init(root)
        subprocess.run(
            ["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q",
             "--allow-empty", "-m", "init"],
            check=True,
        )
        self.hooked_rules(root)
        linked = os.path.join(self.tmp, "linked")
        subprocess.run(["git", "-C", root, "worktree", "add", "-q", "-b", "b", linked], check=True)
        self.hooked_rules(linked)
        self.assertEqual(checkout_for(linked), (linked, os.path.join(root, ".git")))

    def test_outside_checkout_passes(self):
        root = self.make_dir("repo")
        self.git_init(root)
        self.assertIsNone(checkout_for(root))
        self.assertIsNone(checkout_for(self.make_dir("plain")))

    def test_no_git_passes(self):
        root = self.make_dir("nogit")
        self.hooked_rules(root)
        os.environ["GIT_CEILING_DIRECTORIES"] = self.tmp
        self.addCleanup(os.environ.pop, "GIT_CEILING_DIRECTORIES", None)
        self.assertIsNone(checkout_for(root))

    def test_inherited_git_selection_variables_are_ignored(self):
        other = self.make_dir("other")
        self.git_init(other)
        self.hooked_rules(other)
        right = self.make_dir("right")
        self.git_init(right)
        self.hooked_rules(right)
        outside = self.make_dir("outside")
        overrides = {
            "GIT_DIR": os.path.join(other, ".git"),
            "GIT_WORK_TREE": other,
            "GIT_COMMON_DIR": os.path.join(other, ".git"),
        }
        for name, value in overrides.items():
            self._saved_env[name] = os.environ.get(name)
            os.environ[name] = value
        self.assertIsNone(checkout_for(outside))
        self.assertEqual(checkout_for(right), (right, os.path.join(right, ".git")))

    def test_missing_or_bad_cwd_passes(self):
        self.assertIsNone(checkout_for(os.path.join(self.tmp, "does-not-exist")))
        self.assertIsNone(checkout_for(""))
        self.assertIsNone(checkout_for(None))


IDENTITY_A = ("repo-commit-types.md", "a" * 64)
IDENTITY_B = ("repo-commit-types.md", "b" * 64)


def no_sleep(seconds):
    return None


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="veles-agent-store-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state_dir = os.path.join(self.tmp, "agent-rules")
        self.store = self.make_store()

    def make_store(self, **overrides):
        options = {"sleep": no_sleep, "lock_attempts": 3}
        options.update(overrides)
        return Store(self.state_dir, **options)

    def deliver(self, session, identity=IDENTITY_A, key=None, patterns=("git commit",)):
        with self.store.transaction(session) as state:
            state.put(key or session, identity, "delivered", set(patterns))

    def entries_of(self, session):
        with self.store.transaction(session) as state:
            return dict(state.entries)

    def test_atomic_roundtrip(self):
        with self.store.transaction("s1") as state:
            self.assertEqual(state.entries, {})
            state.put("s1", IDENTITY_A, "pending", {"b", "a"})
            state.bump("s1/agent")
        with self.store.transaction("s1") as state:
            entry = state.entries[("s1", IDENTITY_A)]
            self.assertEqual(entry["state"], "pending")
            self.assertEqual(entry["patterns"], {"a", "b"})
            self.assertEqual(entry["generation"], 0)
            self.assertIsInstance(entry["write_id"], int)
            self.assertEqual(state.generation("s1/agent"), 1)
        leftovers = [n for n in os.listdir(self.state_dir) if n.endswith(".tmp") or n.startswith("tmp")]
        self.assertEqual(leftovers, [])
        with open(self.store.session_path("s1"), encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(stored["entries"][0]["patterns"], ["a", "b"])

    def test_entry_records_generation_of_its_key(self):
        with self.store.transaction("s1") as state:
            state.bump("s1")
            state.bump("s1")
            state.put("s1", IDENTITY_A, "delivered", set())
            state.put("s1/a", IDENTITY_A, "delivered", set())
        entries = self.entries_of("s1")
        self.assertEqual(entries[("s1", IDENTITY_A)]["generation"], 2)
        self.assertEqual(entries[("s1/a", IDENTITY_A)]["generation"], 0)

    def test_failed_transaction_writes_nothing(self):
        with self.assertRaises(RuntimeError):
            with self.store.transaction("s1") as state:
                state.put("s1", IDENTITY_A, "delivered", set())
                raise RuntimeError("boom")
        self.assertFalse(os.path.exists(self.store.session_path("s1")))
        self.assertEqual(self.entries_of("s1"), {})

    def test_unchanged_transaction_does_not_write(self):
        with self.store.transaction("s1"):
            pass
        self.assertFalse(os.path.exists(self.store.session_path("s1")))

    def test_missing_session_file_is_a_fresh_session(self):
        self.assertEqual(self.entries_of("never-seen"), {})

    def write_session_file(self, session, content):
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.store.session_path(session), "w", encoding="utf-8") as handle:
            handle.write(content)

    def test_invalid_session_file_raises(self):
        self.deliver("s1")
        entry = {"key": "s1", "name": "n", "sha256": "a" * 64, "state": "delivered",
                 "patterns": [], "generation": 0, "write_id": 1}

        def state(**changes):
            document = {"version": 1, "session": "s1", "generations": {}, "entries": [dict(entry)]}
            for name, value in changes.items():
                document[name] = value
            return json.dumps(document)

        bad_entry = lambda **c: state(entries=[dict(entry, **c)])  # noqa: E731
        cases = [
            "{not json",
            "[]",
            "",
            state(generations=[]),
            state(generations={"s1": "x"}),
            state(generations={"s1": -1}),
            state(generations={"s1": True}),
            state(entries={}),
            state(version=2),
            state(version=True),
            state(version=1.0),
            state(session="other"),
            '{"version": 1, "generations": {}, "entries": []}',
            "[" * 100000 + "]" * 100000,
            '{"version": 1, "version": 1, "session": "s1", "generations": {}, "entries": []}',
            '{"version": 1, "session": "s1", "generations": {"a": 0, "a": 5}, "entries": []}',
            state(entries=[dict(entry)]).replace('"state": "delivered"', '"state": "pending", "state": "delivered"'),
            bad_entry(state="gone"),
            bad_entry(patterns="git"),
            bad_entry(patterns=[1]),
            bad_entry(generation="0"),
            bad_entry(write_id=-3),
            bad_entry(write_id=None),
            bad_entry(key=1),
            bad_entry(sha256=None),
            state(entries=[dict(entry), dict(entry)]),
            state(entries=[3]),
        ]
        for content in cases:
            self.write_session_file("s1", content)
            with self.assertRaises(StoreError, msg=content):
                with self.store.transaction("s1"):
                    self.fail("the body must not run on invalid state")

    def test_unreadable_session_file_raises(self):
        os.makedirs(self.state_dir)
        os.mkdir(self.store.session_path("s1"))  # a directory where the file belongs
        with self.assertRaises(StoreError):
            with self.store.transaction("s1"):
                self.fail("the body must not run")

    def test_non_utf8_session_file_raises(self):
        os.makedirs(self.state_dir)
        with open(self.store.session_path("s1"), "wb") as handle:
            handle.write(b"\xff\xfe\x00")
        with self.assertRaises(StoreError):
            with self.store.transaction("s1"):
                self.fail("the body must not run")

    def test_lock_timeout_is_a_store_error(self):
        self.assertTrue(issubclass(LockTimeout, StoreError))

    def test_write_ids_monotonic(self):
        seen = [self.store.next_write_id()]
        for index in range(55):
            self.deliver("s%02d" % index)
            seen.append(self.store.next_write_id())
        self.store.prune("s54")
        seen.append(self.store.next_write_id())
        self.deliver("s00")
        seen.append(self.store.next_write_id())
        self.assertEqual(seen, sorted(set(seen)))
        ids = [e["write_id"] for e in self.entries_of("s00").values()]
        self.assertGreater(min(ids), seen[-3])

    def test_write_ids_survive_a_new_store_object(self):
        first = self.store.next_write_id()
        self.assertGreater(self.make_store().next_write_id(), first)

    def test_lock_contention(self):
        sleeps = []
        store = self.make_store(sleep=sleeps.append, lock_attempts=4)
        with FileLock(store.session_lock_path("s1"), no_sleep, 1):
            with self.assertRaises(LockTimeout):
                with store.transaction("s1"):
                    self.fail("the body must not run without the lock")
        self.assertEqual(len(sleeps), 3)  # sleeps between tries, none after the last
        with store.transaction("s1"):
            pass  # released again

    def test_lock_is_per_session(self):
        with FileLock(self.store.session_lock_path("s1"), no_sleep, 1):
            self.deliver("s2")

    def test_lock_released_after_body_error(self):
        with self.assertRaises(RuntimeError):
            with self.store.transaction("s1"):
                raise RuntimeError("boom")
        self.deliver("s1")

    def test_per_key_generation(self):
        with self.store.transaction("s") as state:
            self.assertEqual(state.generation("s"), 0)
            state.bump("s/a")
            self.assertEqual(state.generation("s/a"), 1)
            self.assertEqual(state.generation("s"), 0)
            self.assertEqual(state.generation("s/b"), 0)
        with self.store.transaction("s") as state:
            self.assertEqual(state.generation("s/a"), 1)
            self.assertEqual(state.generation("s"), 0)

    def test_bump_all_bumps_main_and_every_known_key(self):
        with self.store.transaction("s") as state:
            state.put("s/a", IDENTITY_A, "delivered", set())
            state.bump("s/b")
        with self.store.transaction("s") as state:
            state.bump_all()
        with self.store.transaction("s") as state:
            self.assertEqual(state.generation("s"), 1)
            self.assertEqual(state.generation("s/a"), 1)
            self.assertEqual(state.generation("s/b"), 2)
            self.assertEqual(state.generation("s/never-seen"), 0)

    def test_prune_keeps_50(self):
        for index in range(52):
            self.deliver("s%02d" % index)
        self.store.prune("s51")
        for index in range(52):
            exists = os.path.exists(self.store.session_path("s%02d" % index))
            self.assertEqual(exists, index >= 2, "s%02d" % index)

    def test_prune_order_is_by_counter_not_by_name_or_time(self):
        for index in range(51):
            self.deliver("s%02d" % index)
        self.deliver("s00")  # newest write now, though its name sorts first
        self.store.prune("s00")
        self.assertTrue(os.path.exists(self.store.session_path("s00")))
        self.assertFalse(os.path.exists(self.store.session_path("s01")))
        self.assertTrue(os.path.exists(self.store.session_path("s02")))

    def test_prune_skips_busy(self):
        for index in range(52):
            self.deliver("s%02d" % index)
        with FileLock(self.store.session_lock_path("s00"), no_sleep, 1):
            self.store.prune("s51")
        self.assertTrue(os.path.exists(self.store.session_path("s00")))
        self.assertFalse(os.path.exists(self.store.session_path("s01")))

    def test_prune_never_current(self):
        for index in range(52):
            self.deliver("s%02d" % index)
        self.store.prune("s00")  # the oldest session is the current one
        self.assertTrue(os.path.exists(self.store.session_path("s00")))
        self.assertFalse(os.path.exists(self.store.session_path("s01")))
        self.assertFalse(os.path.exists(self.store.session_path("s02")))
        self.assertTrue(os.path.exists(self.store.session_path("s03")))

    def test_prune_holds_no_index_lock_while_acquiring_victim_locks(self):
        probes = []
        state_dir = self.state_dir

        class ProbingLock(FileLock):
            def __enter__(self):
                try:
                    with FileLock(os.path.join(state_dir, "index.lock"), no_sleep, 1):
                        probes.append("free")
                except LockTimeout:
                    probes.append("held")
                return FileLock.__enter__(self)

        class Probing(Store):
            def victim_lock(self, stem):
                return ProbingLock(os.path.join(self.state_dir, stem + ".lock"), no_sleep, 1)

        for index in range(53):
            self.deliver("s%02d" % index)
        Probing(state_dir, sleep=no_sleep, lock_attempts=3).prune("s52")
        self.assertEqual(probes, ["free"] * 3)

    def test_concurrent_prunes_leave_exactly_50(self):
        for index in range(51):
            self.deliver("s%02d" % index)
        other = self.make_store()
        state_dir = self.state_dir

        class Interleaved(Store):
            def victim_lock(self, stem):
                if not getattr(self, "ran", False):
                    self.ran = True
                    other.prune("s00")  # a whole second prune between the first one's snapshot and lock
                return Store.victim_lock(self, stem)

        Interleaved(state_dir, sleep=no_sleep, lock_attempts=3).prune("s50")
        remaining = [i for i in range(51) if os.path.exists(self.store.session_path("s%02d" % i))]
        self.assertEqual(len(remaining), 50)
        self.assertIn(0, remaining)  # the second prune's current session
        self.assertIn(50, remaining)

    def test_prune_without_index_does_nothing(self):
        self.store.prune("s1")
        self.assertFalse(os.path.exists(self.store.index_path()))
        self.assertFalse(os.path.exists(os.path.join(self.state_dir, "initialized")))

    def test_prune_skips_a_victim_whose_unlink_fails(self):
        for index in range(53):
            self.deliver("s%02d" % index)
        stuck = self.store.session_path("s00")
        real_unlink = os.unlink

        def unlink(path, *args, **kwargs):
            if path == stuck:
                raise PermissionError("denied")
            return real_unlink(path, *args, **kwargs)

        with mock.patch("os.unlink", side_effect=unlink):
            self.store.prune("s52")
        self.assertTrue(os.path.exists(stuck))
        self.assertFalse(os.path.exists(self.store.session_path("s01")))
        self.assertFalse(os.path.exists(self.store.session_path("s02")))
        self.store.prune("s52")  # the skipped victim is still indexed and goes next time
        self.assertFalse(os.path.exists(stuck))

    def test_prune_index_lock_timeout_propagates(self):
        self.deliver("s1")
        with FileLock(self.store.index_lock_path(), no_sleep, 1):
            with self.assertRaises(LockTimeout):
                self.store.prune("s1")

    def test_prune_does_nothing_at_or_below_the_limit(self):
        for index in range(50):
            self.deliver("s%02d" % index)
        self.store.prune("s49")
        for index in range(50):
            self.assertTrue(os.path.exists(self.store.session_path("s%02d" % index)))

    def test_pruned_session_restarts(self):
        with self.store.transaction("old") as state:
            state.bump("old")
            state.put("old", IDENTITY_A, "delivered", set())
        for index in range(50):
            self.deliver("s%02d" % index)
        self.store.prune("s49")
        with self.store.transaction("old") as state:
            self.assertEqual(state.entries, {})
            self.assertEqual(state.generation("old"), 0)

    def test_two_sessions_isolated(self):
        self.deliver("one", IDENTITY_A)
        self.deliver("two", IDENTITY_B)
        self.assertEqual(list(self.entries_of("one")), [("one", IDENTITY_A)])
        self.assertEqual(list(self.entries_of("two")), [("two", IDENTITY_B)])
        with self.store.transaction("one") as state:
            state.bump_all()
        with self.store.transaction("two") as state:
            self.assertEqual(state.generation("two"), 0)

    def test_rule_identity_differs(self):
        self.deliver("s1", IDENTITY_A)
        entries = self.entries_of("s1")
        self.assertIn(("s1", IDENTITY_A), entries)
        self.assertNotIn(("s1", IDENTITY_B), entries)

    def test_session_ids_with_path_characters_stay_inside_the_store(self):
        self.deliver("../../escape/x")
        self.assertEqual(os.path.dirname(self.store.session_path("../../escape/x")), self.state_dir)
        self.assertEqual(len(self.entries_of("../../escape/x")), 1)
        self.assertEqual(os.listdir(self.tmp), ["agent-rules"])

    def test_deleted_index_after_ids_raises_and_issues_nothing(self):
        self.assertEqual(self.store.next_write_id(), 1)
        self.assertEqual(self.store.next_write_id(), 2)
        os.unlink(self.store.index_path())
        with self.assertRaises(StoreError) as caught:
            self.store.next_write_id()
        self.assertIn("agent-rules", str(caught.exception))
        self.assertFalse(os.path.exists(self.store.index_path()))
        with self.assertRaises(StoreError):
            self.deliver("s1")
        with self.assertRaises(StoreError):
            self.store.prune("s1")

    def test_deleted_index_with_session_files_raises(self):
        self.deliver("s1")
        os.unlink(self.store.index_path())
        os.unlink(os.path.join(self.state_dir, "initialized"))
        with self.assertRaises(StoreError):
            self.store.next_write_id()

    def test_invalid_index_raises(self):
        self.store.next_write_id()
        good_stem = "a" * 64
        cases = [
            "{nope",
            "[]",
            "",
            json.dumps({"write_id": 1, "order": 1}),
            json.dumps({"write_id": -1, "order": 1, "sessions": {}}),
            json.dumps({"write_id": True, "order": 1, "sessions": {}}),
            json.dumps({"write_id": "1", "order": 1, "sessions": {}}),
            json.dumps({"write_id": 1, "order": 1.5, "sessions": {}}),
            json.dumps({"write_id": 1, "order": 1, "sessions": []}),
            json.dumps({"write_id": 1, "order": 1, "sessions": {good_stem: "x"}}),
            json.dumps({"write_id": 1, "order": 1, "sessions": {good_stem: -1}}),
        ]
        for content in cases:
            with open(self.store.index_path(), "w", encoding="utf-8") as handle:
                handle.write(content)
            with self.assertRaises(StoreError, msg=content):
                self.store.next_write_id()
            with open(self.store.index_path(), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), content)  # never rewritten

    def test_index_stems_must_be_canonical_digests(self):
        self.store.next_write_id()
        for stem in ("../../etc/passwd", "A" * 64, "a" * 63, "a" * 65, "g" * 64, "", "a" * 64 + "/x",
                     "a" * 64 + "\n"):
            with open(self.store.index_path(), "w", encoding="utf-8") as handle:
                json.dump({"write_id": 1, "order": 1, "sessions": {stem: 1}}, handle)
            for call in (self.store.next_write_id, lambda: self.store.prune("s1")):
                with self.assertRaises(StoreError, msg=repr(stem)):
                    call()

    def test_fresh_store_initialises_once(self):
        self.assertEqual(self.store.next_write_id(), 1)
        self.assertTrue(os.path.exists(os.path.join(self.state_dir, "initialized")))
        self.assertEqual(self.make_store().next_write_id(), 2)

    def test_zero_index_then_marker_before_the_first_id(self):
        state_dir = self.state_dir
        seen = []

        class Watching(Store):
            def _write_index(self, index):
                seen.append((index["write_id"], os.path.exists(os.path.join(state_dir, "initialized"))))
                Store._write_index(self, index)

        self.assertEqual(Watching(state_dir, sleep=no_sleep).next_write_id(), 1)
        self.assertEqual(seen, [(0, False), (1, True)])

    def test_marker_write_failure_self_heals(self):
        class Failing(Store):
            def _write_marker(self):
                raise OSError("disk full")

        failing = Failing(self.state_dir, sleep=no_sleep, lock_attempts=3)
        with self.assertRaises(OSError):
            failing.next_write_id()
        self.assertTrue(os.path.exists(self.store.index_path()))
        self.assertFalse(os.path.exists(os.path.join(self.state_dir, "initialized")))
        self.assertEqual(self.store.next_write_id(), 1)
        self.assertTrue(os.path.exists(os.path.join(self.state_dir, "initialized")))

    def test_marker_with_missing_index_raises(self):
        self.store.next_write_id()
        os.unlink(self.store.index_path())
        self.assertTrue(os.path.exists(os.path.join(self.state_dir, "initialized")))
        with self.assertRaises(StoreError):
            self.store.next_write_id()

    def test_valid_index_without_marker_gets_a_marker(self):
        self.store.next_write_id()
        os.unlink(os.path.join(self.state_dir, "initialized"))
        self.assertEqual(self.store.next_write_id(), 2)
        self.assertTrue(os.path.exists(os.path.join(self.state_dir, "initialized")))

    def test_duplicate_index_members_raise(self):
        self.store.next_write_id()
        for content in (
            '{"write_id": 42, "write_id": 1, "order": 0, "sessions": {}}',
            '{"write_id": 1, "order": 0, "sessions": {"%s": 1, "%s": 2}}' % ("a" * 64, "a" * 64),
        ):
            with open(self.store.index_path(), "w", encoding="utf-8") as handle:
                handle.write(content)
            with self.assertRaises(StoreError, msg=content):
                self.store.next_write_id()

    def test_deeply_nested_index_raises_store_error(self):
        self.store.next_write_id()
        with open(self.store.index_path(), "w", encoding="utf-8") as handle:
            handle.write("[" * 100000 + "]" * 100000)
        with self.assertRaises(StoreError):
            self.store.next_write_id()

    def stat_failing_for_marker(self, error):
        marker = os.path.join(self.state_dir, "initialized")
        real_stat = os.stat

        def stat(path, *args, **kwargs):
            if path == marker:
                raise error
            return real_stat(path, *args, **kwargs)

        return mock.patch("os.stat", side_effect=stat)

    def test_marker_stat_error_is_not_a_fresh_store(self):
        self.store.next_write_id()
        os.unlink(self.store.index_path())
        with self.stat_failing_for_marker(PermissionError("denied")):
            with self.assertRaises(StoreError):
                self.store.next_write_id()
        self.assertFalse(os.path.exists(self.store.index_path()))

    def test_marker_in_listing_blocks_reinitialisation(self):
        self.store.next_write_id()
        os.unlink(self.store.index_path())
        with self.stat_failing_for_marker(FileNotFoundError("lies")):
            with self.assertRaises(StoreError):
                self.store.next_write_id()
        self.assertFalse(os.path.exists(self.store.index_path()))

    def test_absent_marker_in_an_empty_directory_initialises(self):
        os.makedirs(self.state_dir)
        self.assertEqual(self.store.next_write_id(), 1)

    def test_unreadable_store_directory_is_not_a_fresh_store(self):
        with mock.patch("os.listdir", side_effect=PermissionError("denied")):
            with self.assertRaises(StoreError):
                self.store.next_write_id()
        self.assertFalse(os.path.exists(self.store.index_path()))

    def test_ids_stay_monotonic_across_prune_and_new_store(self):
        for index in range(53):
            self.deliver("s%02d" % index)
        last = self.store.next_write_id()
        self.store.prune("s52")
        self.assertGreater(self.make_store().next_write_id(), last)

    def test_index_registered_before_session_file_is_published(self):
        state_dir = self.state_dir
        order = []

        class Watching(Store):
            def _publish(self, session, text):
                with open(os.path.join(state_dir, "index.json"), encoding="utf-8") as handle:
                    order.append(self._stem(session) in json.load(handle)["sessions"])
                Store._publish(self, session, text)

        store = Watching(state_dir, sleep=no_sleep, lock_attempts=3)
        with store.transaction("s1") as state:
            state.put("s1", IDENTITY_A, "delivered", set())
        self.assertEqual(order, [True])

    def test_publish_failure_leaves_a_consistent_store(self):
        class Failing(Store):
            def _publish(self, session, text):
                raise OSError("disk full")

        failing = Failing(self.state_dir, sleep=no_sleep, lock_attempts=3)
        with self.assertRaises(OSError):
            with failing.transaction("s1") as state:
                state.put("s1", IDENTITY_A, "delivered", set())
        self.assertFalse(os.path.exists(self.store.session_path("s1")))
        self.assertEqual(self.entries_of("s1"), {})  # the index entry without a file is harmless
        self.deliver("s1")
        self.assertEqual(len(self.entries_of("s1")), 1)
        for index in range(52):
            self.deliver("t%02d" % index)
        self.store.prune("t51")  # a ghost index entry does not break pruning
        self.assertFalse(os.path.exists(self.store.session_path("s1")))

    def test_index_failure_publishes_no_unindexed_file(self):
        class Failing(Store):
            def _touch(self, session):
                raise StoreError("index unavailable")

        failing = Failing(self.state_dir, sleep=no_sleep, lock_attempts=3)
        with self.assertRaises(StoreError):
            with failing.transaction("s1") as state:
                state.put("s1", IDENTITY_A, "delivered", set())
        self.assertFalse(os.path.exists(self.store.session_path("s1")))

    def test_unencodable_session_id_raises(self):
        for session in ("\ud800", "a\udfffb", 5, None):
            with self.assertRaises(StoreError, msg=repr(session)):
                with self.store.transaction(session):
                    self.fail("the body must not run")
            with self.assertRaises(StoreError, msg=repr(session)):
                self.store.prune(session)

    def test_distinct_session_ids_never_share_a_file(self):
        self.assertNotEqual(self.store.session_path("\ufffd"), self.store.session_path("a"))
        self.deliver("\ufffd")
        with self.assertRaises(StoreError):
            self.deliver("\ud800")
        self.assertEqual(len(self.entries_of("\ufffd")), 1)


class FileLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="veles-agent-lock-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "x.lock")

    def test_exclusive_and_reusable(self):
        with FileLock(self.path, no_sleep, 1):
            with self.assertRaises(LockTimeout):
                FileLock(self.path, no_sleep, 1).__enter__()
        with FileLock(self.path, no_sleep, 1):
            pass

    def test_creates_missing_directory(self):
        nested = os.path.join(self.tmp, "a", "b", "x.lock")
        with FileLock(nested, no_sleep, 1):
            self.assertTrue(os.path.exists(nested))


GIT_COMMIT_GATE = r"\bgit\b[^;&|\n]*?\scommit\b"
PR_CREATE_GATE = r"\bgh\s+pr\s+create\b"
PR_EDIT_GATE = r"\bgh\s+pr\s+edit\b"
FINALIZE_COMMAND = r"\bopenspec\s+instructions\s+finalize\b"


def quoted(value):
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


def flow(values):
    return "[%s]" % ", ".join(quoted(value) for value in values)


def make_rule(name, body="Commit types guide.\n", skills=("openspec-apply-change", "opsx:apply"),
              commands=(FINALIZE_COMMAND,), gates=(GIT_COMMIT_GATE, PR_CREATE_GATE, PR_EDIT_GATE)):
    lines = ['pointer: "Read %s."' % name]
    if skills:
        lines.append("skills: " + flow(skills))
    if commands:
        lines.append("commands: " + flow(commands))
    if gates:
        lines.append("gates: " + flow(gates))
    return parse_rule(name, "---\n" + "\n".join(lines) + "\n---\n" + body)


def event(kind, session="s1", agent=None, cwd="/work/repo", tool=None, command=None, ok=None,
          skill=None, path=None, prompt=None, write_ids=None):
    return Event(kind, session, agent, cwd, tool, command, ok, skill, path, prompt, write_ids)


def bash_before(command, **fields):
    return event("tool-before", tool="Bash", command=command, **fields)


def bash_after(command, ok=True, **fields):
    return event("tool-after", tool="Bash", command=command, ok=ok, **fields)


class EngineTest(unittest.TestCase):
    """``decide`` against a real temporary store, one transaction per event as ``handle`` runs it."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="veles-agent-engine-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = Store(os.path.join(self.tmp, "agent-rules"), sleep=no_sleep, lock_attempts=2)
        self.rules = [make_rule("repo-commit-types.md")]

    def run_event(self, ev, rules=None):
        with self.store.transaction(ev.session) as state:
            return decide(self.rules if rules is None else rules, state, context_key(ev), ev)

    def entries(self, session="s1"):
        with self.store.transaction(session) as state:
            return dict(
                (identity, entry) for identity, entry in state.entries.items()
                if entry["generation"] >= state.generation(identity[0])
            )

    def entry(self, rule=None, key="s1"):
        rule = rule or self.rules[0]
        return self.entries(key.split("/")[0]).get((key, rule.identity))


class ContextKeyTest(unittest.TestCase):
    def test_main_and_subagent_keys(self):
        self.assertEqual(context_key(event("prompt")), "s1")
        self.assertEqual(context_key(event("prompt", agent="a7")), "s1/a7")
        self.assertEqual(context_key(event("prompt", agent="")), "s1")


class MomentTest(EngineTest):
    def assert_moment(self, decision):
        rule = self.rules[0]
        self.assertEqual(decision.kind, "context")
        self.assertEqual(decision.text, "Commit types guide.")
        entry = self.entry()
        self.assertEqual(entry["state"], "delivered")
        self.assertEqual(decision.entries, [(rule.identity, entry["write_id"])])

    def test_skill_tool_moment(self):
        self.assert_moment(self.run_event(event("tool-after", tool="Skill", skill="openspec-apply-change")))
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "pass")

    def test_namespaced_skill_tool_moment(self):
        self.assert_moment(self.run_event(event("tool-after", tool="Skill", skill="opsx:apply")))

    def test_other_skill_is_not_a_moment(self):
        decision = self.run_event(event("tool-after", tool="Skill", skill="opsx:archive"))
        self.assertEqual(decision.kind, "pass")
        self.assertEqual(self.entries(), {})

    def test_slash_prompt_moment(self):
        self.assert_moment(self.run_event(event("prompt", prompt="/opsx:apply hook-delivered-rules")))
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "pass")

    def test_slash_prompt_alone_and_with_leading_space(self):
        self.assert_moment(self.run_event(event("prompt", prompt="/openspec-apply-change")))
        self.assert_moment(self.run_event(event("prompt", prompt="  /opsx:apply\nnow")))

    def test_plain_prompt_text_is_not_a_moment(self):
        for prompt in (
            "please run opsx:apply now",
            "use /opsx:apply for this",
            "opsx:apply",
            "/opsx:applyx",
            "<task-notification>/opsx:apply</task-notification>",
            "",
            None,
        ):
            decision = self.run_event(event("prompt", prompt=prompt))
            self.assertEqual(decision.kind, "pass", prompt)
        self.assertEqual(self.entries(), {})

    def test_skill_md_read_moment(self):
        for path in (
            "/work/repo/.agents/skills/openspec-apply-change/SKILL.md",
            ".agents/skills/openspec-apply-change/SKILL.md",
            "C:\\work\\repo\\.agents\\skills\\openspec-apply-change\\SKILL.md",
            "/work/repo/.claude/skills/apply/SKILL.md",
        ):
            session = "read-%d" % len(path)
            decision = self.run_event(event("tool-after", session=session, tool="Read", path=path))
            self.assertEqual(decision.kind, "context", path)
            self.assertEqual(self.entry(key=session)["state"], "delivered")

    def make_skill(self, *parts):
        directory = os.path.join(self.tmp, "repo", *parts)
        os.makedirs(directory)
        path = os.path.join(directory, "SKILL.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("skill\n")
        return directory

    def symlink(self, target, *parts):
        link = os.path.join(self.tmp, "repo", *parts)
        os.makedirs(os.path.dirname(link), exist_ok=True)
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        return link

    def read(self, path, rules=None):
        return self.run_event(event("tool-after", tool="Read", path=path), rules=rules)

    def test_skill_md_read_by_real_path(self):
        target = self.make_skill(".agents", "skills", "openspec-apply-change")
        link = self.symlink(target, ".claude", "skills", "openspec-apply-change")
        self.assertEqual(self.read(os.path.join(link, "SKILL.md")).kind, "context")
        whole = self.symlink(os.path.join(self.tmp, "repo", ".agents", "skills"), ".claude", "linked")
        self.assertEqual(self.read(os.path.join(whole, "openspec-apply-change", "SKILL.md")).kind, "context")

    def test_skill_md_lookalike_resolving_elsewhere_is_not_a_moment(self):
        elsewhere = self.make_skill("docs", "notes")
        link = self.symlink(elsewhere, ".claude", "skills", "openspec-apply-change")
        self.assertEqual(self.read(os.path.join(link, "SKILL.md")).kind, "pass")
        file_link = self.symlink(os.path.join(elsewhere, "SKILL.md"),
                                 ".agents", "skills", "opsx", "SKILL.md")
        self.assertEqual(self.read(file_link).kind, "pass")
        self.assertEqual(self.entries(), {})

    def test_skill_md_symlink_to_another_skill_credits_that_skill_only(self):
        apply_rule = make_rule("repo-a.md", body="A\n", skills=("openspec-apply-change",))
        other_rule = make_rule("repo-b.md", body="B\n", skills=("other-skill",))
        target = self.make_skill(".agents", "skills", "other-skill")
        link = self.symlink(target, ".claude", "skills", "openspec-apply-change")
        decision = self.read(os.path.join(link, "SKILL.md"), rules=[apply_rule, other_rule])
        self.assertEqual(decision.kind, "context")
        self.assertEqual(decision.text, "B")
        self.assertEqual([identity for identity, _ in decision.entries], [other_rule.identity])
        self.assertIsNone(self.entry(apply_rule))

    def test_other_reads_are_not_moments(self):
        for path in (
            "/work/repo/.agents/skills/openspec-archive-change/SKILL.md",
            "/work/repo/.agents/skills/openspec-apply-change/README.md",
            "/work/repo/.agents/hooked-rules/repo-commit-types.md",
            "/work/repo/xskills/openspec-apply-change/SKILL.md",
            "/work/repo/skills/openspec-apply-change/SKILL.md.bak",
        ):
            self.assertEqual(self.run_event(event("tool-after", tool="Read", path=path)).kind, "pass", path)
        self.assertEqual(self.entries(), {})

    def test_command_moment_success(self):
        command = "openspec instructions finalize --change x"
        self.assert_moment(self.run_event(bash_after(command)))
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "pass")

    def test_command_moment_matches_anywhere_in_the_string(self):
        self.assert_moment(self.run_event(bash_after("cd a && openspec instructions finalize --json")))

    def test_command_moment_failure(self):
        for ok in (False, None):
            decision = self.run_event(bash_after("openspec instructions finalize --change x", ok=ok))
            self.assertEqual(decision.kind, "pass")
        self.assertEqual(self.entries(), {})
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "deny")

    def test_command_before_is_not_a_moment(self):
        decision = self.run_event(bash_before("openspec instructions finalize --change x"))
        self.assertEqual(decision.kind, "pass")
        self.assertEqual(self.entries(), {})

    def test_background_launch(self):
        payload = claude(
            "PostToolUse", tool_name="Bash",
            tool_input={"command": "openspec instructions finalize --change x", "run_in_background": True},
            tool_response={"backgroundTaskId": "b1"},
        )
        self.assert_moment(self.run_event(parse_event("claude", payload)))
        self.assert_moment(self.run_event(bash_after("openspec instructions finalize --change x &")))

    def test_gate_only_rule_has_no_moments(self):
        rule = make_rule("repo-gate-only.md", skills=(), commands=())
        for ev in (
            event("tool-after", tool="Skill", skill="opsx:apply"),
            event("prompt", prompt="/opsx:apply"),
            bash_after("git commit -m x"),
        ):
            self.assertEqual(self.run_event(ev, rules=[rule]).kind, "pass")
        self.assertEqual(self.entries(), {})

    def test_moment_redelivers_with_a_fresh_write_id(self):
        first = self.run_event(event("prompt", prompt="/opsx:apply"))
        second = self.run_event(event("prompt", prompt="/opsx:apply"))
        self.assertEqual(second.kind, "context")
        self.assertGreater(second.entries[0][1], first.entries[0][1])

    def test_moment_confirms_a_pending_rule(self):
        self.assertEqual(self.run_event(bash_before("gh pr create --fill")).kind, "deny")
        self.run_event(event("prompt", prompt="/opsx:apply"))
        self.assertEqual(self.entry()["state"], "delivered")
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "pass")

    def test_moment_in_a_subagent_is_not_credited_to_the_parent(self):
        self.run_event(event("prompt", agent="a1", prompt="/opsx:apply"))
        self.assertEqual(self.entry(key="s1/a1")["state"], "delivered")
        self.assertIsNone(self.entry())
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "deny")
        self.assertEqual(self.run_event(bash_before("git commit -m x", agent="a1")).kind, "pass")

    def test_other_identity_is_not_credited(self):
        self.run_event(event("prompt", prompt="/opsx:apply"))
        sibling = [make_rule("repo-commit-types.md", body="A different guide.\n")]
        self.assertEqual(self.run_event(bash_before("git commit -m x"), rules=sibling).kind, "deny")


class GateTest(EngineTest):
    def assert_denied(self, command, **fields):
        decision = self.run_event(bash_before(command, **fields))
        self.assertEqual(decision.kind, "deny", command)
        self.assertEqual(decision.text, "Commit types guide.")
        return decision

    def assert_passes(self, command, **fields):
        decision = self.run_event(bash_before(command, **fields))
        self.assertEqual(decision.kind, "pass", command)
        return decision

    def test_block_once_then_retry(self):
        decision = self.assert_denied('git commit -m "feat: x"')
        entry = self.entry()
        self.assertEqual(entry["state"], "pending")
        self.assertEqual(entry["patterns"], {GIT_COMMIT_GATE})
        self.assertEqual(decision.entries, [(self.rules[0].identity, entry["write_id"])])
        self.assert_passes('git commit -m "docs: x"')
        self.assertEqual(self.entry()["state"], "delivered")
        self.assert_passes('git commit -m "docs: y"')
        self.assert_passes("gh pr create --fill")

    def test_accumulated_pending(self):
        self.assert_denied("git commit -m x")
        self.assert_denied("gh pr create --fill")
        self.assertEqual(self.entry()["patterns"], {GIT_COMMIT_GATE, PR_CREATE_GATE})
        self.assert_passes("git commit -m x")
        self.assertEqual(self.entry()["state"], "delivered")

    def test_sibling_denied(self):
        self.assert_denied("git commit -m x")
        self.assert_denied("gh pr edit 5 --title x")
        self.assertEqual(self.entry()["state"], "pending")
        self.assertEqual(self.entry()["patterns"], {GIT_COMMIT_GATE, PR_EDIT_GATE})

    def test_overlap_patterns(self):
        self.assert_denied("git commit -m x && gh pr create --fill")
        self.assertEqual(self.entry()["patterns"], {GIT_COMMIT_GATE, PR_CREATE_GATE})
        self.assert_passes("gh pr create --fill")

    def test_matched_forms(self):
        forms = (
            'git -C "work tree" commit',
            "git -c k=v commit",
            "git --no-pager commit",
            'git --git-dir="a b/.git" commit',
            "git --work-tree . commit",
            "GIT_AUTHOR_NAME=x git commit",
            "git add -A && git commit -m x",
            'bash -c "git commit -m x"',
            "git commit --amend",
            "gh pr create --fill",
            "gh pr edit 5 --title x",
        )
        for index, form in enumerate(forms):
            with self.subTest(form=form):
                self.assert_denied(form, session="form-%d" % index)

    def test_boundary_semicolon(self):
        # Accepted out-of-scope form (design section 6): the quoted `;` reads as a shell operator.
        self.assert_passes('git -C "work;tree" commit')
        self.assertEqual(self.entries(), {})

    def test_ungated_commands_pass_without_state(self):
        for command in ("git status", "ls -la", "gh pr view 5", ""):
            self.assert_passes(command)
        self.assertEqual(self.entries(), {})

    def test_non_shell_tool_before_passes(self):
        decision = self.run_event(event("tool-before", tool="Read", path="/x"))
        self.assertEqual(decision.kind, "pass")

    def test_tool_after_never_gates(self):
        self.assertEqual(self.run_event(bash_after("git commit -m x")).kind, "pass")
        self.assertEqual(self.entries(), {})

    def test_pending_entry_keeps_its_patterns_when_denied_again(self):
        self.assert_denied("gh pr create --fill")
        self.assert_denied("gh pr edit 5")
        self.assert_denied("git commit -m x")
        self.assertEqual(self.entry()["patterns"], {GIT_COMMIT_GATE, PR_CREATE_GATE, PR_EDIT_GATE})

    def test_confirm_records_a_new_write_id(self):
        denied = self.assert_denied("git commit -m x")
        self.assert_passes("git commit -m y")
        self.assertGreater(self.entry()["write_id"], denied.entries[0][1])


def sized_body(size, letter):
    return letter * (size - 1) + "\n"


class CapTest(EngineTest):
    def setUp(self):
        super(CapTest, self).setUp()
        self.rules = [
            make_rule("repo-%s.md" % letter, body=sized_body(4000, letter)) for letter in "abc"
        ]

    def test_join_format(self):
        rules = [make_rule("repo-b.md", body="\n  Second.  \n\n"), make_rule("repo-a.md", body="First.\n")]
        decision = self.run_event(event("prompt", prompt="/opsx:apply"), rules=rules)
        self.assertEqual(decision.text, "First.\n\nSecond.")

    def test_filename_order(self):
        rules = [make_rule("repo-c.md", body="C\n"), make_rule("repo-a.md", body="A\n"),
                 make_rule("repo-b.md", body="B\n")]
        decision = self.run_event(bash_before("git commit -m x"), rules=rules)
        self.assertEqual(decision.text, "A\n\nB\n\nC")
        self.assertEqual([identity[0] for identity, _ in decision.entries],
                         ["repo-a.md", "repo-b.md", "repo-c.md"])

    def test_cap_moment(self):
        decision = self.run_event(event("prompt", prompt="/opsx:apply"))
        self.assertEqual(decision.kind, "context")
        self.assertEqual(decision.text, "a" * 3999 + "\n\n" + "b" * 3999)
        self.assertLessEqual(len(decision.text.encode("utf-8")), 9000)
        self.assertEqual([identity[0] for identity, _ in decision.entries], ["repo-a.md", "repo-b.md"])
        self.assertEqual(self.entry(self.rules[0])["state"], "delivered")
        self.assertEqual(self.entry(self.rules[1])["state"], "delivered")
        self.assertIsNone(self.entry(self.rules[2]))

    def test_cap_counts_utf8_bytes(self):
        rules = [make_rule("repo-a.md", body="\u00e9" * 2000 + "\n"),
                 make_rule("repo-b.md", body="\u00e9" * 2000 + "\n"),
                 make_rule("repo-c.md", body="\u00e9" * 600 + "\n")]
        decision = self.run_event(event("prompt", prompt="/opsx:apply"), rules=rules)
        # 4000 + 2 + 4000 bytes fit; adding 2 + 1200 more would pass 9,000.
        self.assertEqual([identity[0] for identity, _ in decision.entries], ["repo-a.md", "repo-b.md"])

    def test_cap_skips_a_misfit_and_keeps_a_later_rule_that_fits(self):
        rules = [make_rule("repo-a.md", body=sized_body(4000, "a")),
                 make_rule("repo-b.md", body=sized_body(4000, "b")),
                 make_rule("repo-c.md", body=sized_body(4000, "c")),
                 make_rule("repo-d.md", body="d" * 900 + "\n")]
        decision = self.run_event(event("prompt", prompt="/opsx:apply"), rules=rules)
        self.assertEqual([identity[0] for identity, _ in decision.entries],
                         ["repo-a.md", "repo-b.md", "repo-d.md"])
        self.assertLessEqual(len(decision.text.encode("utf-8")), 9000)

    def test_cap_denial(self):
        first = self.run_event(bash_before("git commit -m x"))
        self.assertEqual(first.kind, "deny")
        self.assertEqual([identity[0] for identity, _ in first.entries], ["repo-a.md", "repo-b.md"])
        self.assertEqual(self.entry(self.rules[0])["state"], "pending")
        self.assertEqual(self.entry(self.rules[1])["state"], "pending")
        self.assertIsNone(self.entry(self.rules[2]))
        retry = self.run_event(bash_before("git commit -m x"))
        self.assertEqual(retry.kind, "deny")
        self.assertEqual(retry.text, "c" * 3999)
        self.assertEqual([identity[0] for identity, _ in retry.entries], ["repo-c.md"])
        self.assertEqual(self.entry(self.rules[0])["state"], "delivered")
        self.assertEqual(self.entry(self.rules[1])["state"], "delivered")
        self.assertEqual(self.entry(self.rules[2])["state"], "pending")
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "pass")


class RearmTest(EngineTest):
    def deliver(self, agent=None):
        decision = self.run_event(event("prompt", agent=agent, prompt="/opsx:apply"))
        self.assertEqual(decision.kind, "context")
        return decision

    def test_rearm_main(self):
        self.deliver()
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "pass")
        decision = self.run_event(event("compacted"))
        self.assertEqual(decision, Decision("pass", "", []))
        self.assertIsNone(self.entry())
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "deny")

    def test_rearm_voids_pending(self):
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "deny")
        self.run_event(event("compacted"))
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "deny")

    def test_rearm_child_keeps_parent(self):
        self.deliver()
        self.deliver(agent="a1")
        self.run_event(event("compacted", agent="a1"))
        self.assertIsNone(self.entry(key="s1/a1"))
        self.assertEqual(self.entry()["state"], "delivered")
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "pass")
        self.assertEqual(self.run_event(bash_before("git commit -m x", agent="a1")).kind, "deny")

    def test_rearm_child_keeps_sibling_child(self):
        self.deliver(agent="a1")
        self.deliver(agent="a2")
        self.run_event(event("compacted", agent="a1"))
        self.assertEqual(self.entry(key="s1/a2")["state"], "delivered")

    def test_rearm_unidentified_all(self):
        self.deliver()
        self.deliver(agent="a1")
        self.deliver(agent="a2")
        self.run_event(event("compacted"))
        for key in ("s1", "s1/a1", "s1/a2"):
            self.assertIsNone(self.entry(key=key), key)
            agent = key.split("/")[1] if "/" in key else None
            self.assertEqual(self.run_event(bash_before("git commit -m x", agent=agent)).kind, "deny")

    def test_rearm_leaves_other_sessions(self):
        self.deliver()
        self.run_event(event("prompt", session="s2", prompt="/opsx:apply"))
        self.run_event(event("compacted"))
        self.assertEqual(self.entry(key="s2")["state"], "delivered")

    def test_rearm_credits_nothing(self):
        self.run_event(event("compacted"))
        self.run_event(event("compacted", agent="a1"))
        self.assertEqual(self.entries(), {})
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "deny")

    def test_moment_after_rearm_delivers_again(self):
        self.deliver()
        self.run_event(event("compacted"))
        self.deliver()
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "pass")


class VoidTest(EngineTest):
    def void(self, write_ids, **fields):
        decision = self.run_event(event("void", write_ids=write_ids, **fields))
        self.assertEqual(decision, Decision("pass", "", []))

    def test_void_exact(self):
        delivered = self.run_event(event("prompt", prompt="/opsx:apply"))
        self.void([delivered.entries[0][1]])
        self.assertIsNone(self.entry())
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "deny")

    def test_void_stale_id_is_a_no_op(self):
        denied = self.run_event(bash_before("git commit -m x"))
        self.run_event(bash_before("git commit -m y"))  # a sibling confirmed it with a newer id
        current = self.entry()
        self.void([denied.entries[0][1]])
        self.assertEqual(self.entry(), current)
        self.assertEqual(self.run_event(bash_before("git commit -m z")).kind, "pass")

    def test_void_other_key_is_a_no_op(self):
        delivered = self.run_event(event("prompt", prompt="/opsx:apply"))
        self.void([delivered.entries[0][1]], agent="a1")
        self.assertEqual(self.entry()["state"], "delivered")

    def test_void_pruned_session_is_a_no_op(self):
        self.void([1, 2, 3], session="gone")
        self.assertFalse(os.path.exists(self.store.session_path("gone")))
        self.assertEqual(self.entries("gone"), {})

    def test_void_removes_only_the_named_entries(self):
        rules = [make_rule("repo-a.md", body="A\n"), make_rule("repo-b.md", body="B\n")]
        self.rules = rules
        decision = self.run_event(event("prompt", prompt="/opsx:apply"))
        self.void([decision.entries[1][1], 999999])
        self.assertEqual(self.entry(rules[0])["state"], "delivered")
        self.assertIsNone(self.entry(rules[1]))

    def test_void_of_a_pending_entry(self):
        denied = self.run_event(bash_before("git commit -m x"))
        self.void([denied.entries[0][1]])
        self.assertIsNone(self.entry())
        self.assertEqual(self.run_event(bash_before("git commit -m x")).kind, "deny")

    def test_void_empty_list(self):
        self.run_event(event("prompt", prompt="/opsx:apply"))
        self.void([])
        self.assertEqual(self.entry()["state"], "delivered")


def quick_store(state_dir):
    return Store(state_dir, sleep=no_sleep, lock_attempts=2)


class CheckoutFixture(IsolatedGitTest):
    """A temporary checkout with one hooked rule, and helpers to run events through ``handle``."""

    def setUp(self):
        super(CheckoutFixture, self).setUp()
        self.root = self.make_dir("repo")
        self.git_init(self.root)
        self.hooked_rules(self.root)
        self.rule = make_rule("repo-commit-types.md")
        self.write_rule("repo-commit-types.md", self.rule_source())
        self.state_dir = os.path.join(self.root, ".git", "agent-rules")
        self.store = quick_store(self.state_dir)

    def rule_source(self, body="Commit types guide.\n"):
        lines = [
            'pointer: "Read repo-commit-types.md."',
            "skills: " + flow(("openspec-apply-change", "opsx:apply")),
            "commands: " + flow((FINALIZE_COMMAND,)),
            "gates: " + flow((GIT_COMMIT_GATE, PR_CREATE_GATE, PR_EDIT_GATE)),
        ]
        return "---\n" + "\n".join(lines) + "\n---\n" + body

    def write_rule(self, name, data):
        path = os.path.join(self.root, ".agents", "hooked-rules", name)
        with open(path, "wb") as handle_:
            handle_.write(data if isinstance(data, bytes) else data.encode("utf-8"))

    def call(self, payload, fmt="plain", env=None, store_factory=quick_store):
        text = payload if isinstance(payload, str) else json.dumps(payload)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            out, code = handle(text, fmt, env or {}, store_factory)
        self.stderr = stderr.getvalue()
        return out, code

    def plain(self, kind, **fields):
        payload = {"event": kind, "session": "s1", "cwd": self.root}
        payload.update(fields)
        return payload

    def claude_event(self, name, **fields):
        payload = {"hook_event_name": name, "session_id": "s1", "cwd": self.root}
        payload.update(fields)
        return payload

    def claude_bash(self, name, command):
        return self.claude_event(name, tool_name="Bash", tool_input={"command": command})

    def entries(self, session="s1"):
        with self.store.transaction(session) as state:
            return dict(state.entries)

    def assert_plain_notice(self, out, code):
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"decision": "context", "text": FAILURE_NOTICE, "entries": []})

    def assert_silent_notice(self, out, code):
        self.assertEqual((out, code), ("", 0))
        self.assertIn(FAILURE_NOTICE, self.stderr)


class HandleTest(CheckoutFixture):
    """``handle`` end to end, as the ``hook`` subcommand runs it."""

    def test_plain_deny_then_pass(self):
        out, code = self.call(self.plain("tool-before", command='git commit -m "feat: x"'))
        self.assertEqual(code, 0)
        response = json.loads(out)
        entry = self.entries()[("s1", self.rule.identity)]
        self.assertEqual(response, {
            "decision": "deny",
            "text": "Commit types guide.",
            "entries": [{"rule": list(self.rule.identity), "write_id": entry["write_id"]}],
        })
        self.assertTrue(os.path.isfile(self.store.session_path("s1")))
        self.assertEqual(self.call(self.plain("tool-before", command='git commit -m "docs: x"')), ("", 0))
        self.assertEqual(self.entries()[("s1", self.rule.identity)]["state"], "delivered")
        self.assertEqual(self.stderr, "")

    def test_plain_moment_then_void(self):
        out, code = self.call(self.plain("tool-after", tool="skill", skill="opsx:apply"))
        response = json.loads(out)
        self.assertEqual(response["decision"], "context")
        write_id = response["entries"][0]["write_id"]
        self.assertEqual(self.call(self.plain("void", write_ids=[write_id])), ("", 0))
        self.assertEqual(self.entries(), {})
        self.assertEqual(json.loads(self.call(self.plain("tool-before", command="git commit"))[0])["decision"],
                         "deny")

    def test_claude_deny_and_context(self):
        out, code = self.call(self.claude_bash("PreToolUse", "git commit -m x"), fmt="claude")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Commit types guide.",
        }})
        self.assertNotIn("allow", out)
        out, code = self.call(
            self.claude_event("PostToolUse", tool_name="Skill", tool_input={"skill": "opsx:apply"}),
            fmt="claude",
        )
        self.assertEqual(json.loads(out), {"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": "Commit types guide.",
        }})

    def test_claude_compacted_renders_nothing_and_rearms(self):
        self.call(self.claude_event("UserPromptSubmit", prompt="/opsx:apply"), fmt="claude")
        self.assertEqual(self.call(self.claude_event("PreCompact", trigger="manual"), fmt="claude"), ("", 0))
        out, _ = self.call(self.claude_bash("PreToolUse", "git commit -m x"), fmt="claude")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_plain_compacted_renders_nothing(self):
        self.assertEqual(self.call(self.plain("compacted", agent="a1")), ("", 0))

    def test_outside_checkout_passes_and_writes_nothing(self):
        plain = self.make_dir("plain-dir")
        bare = self.make_dir("no-rules")
        self.git_init(bare)
        for cwd in (plain, bare):
            payload = {"event": "tool-before", "session": "s1", "cwd": cwd, "command": "git commit"}
            self.assertEqual(self.call(payload), ("", 0))
            self.assertEqual(self.stderr, "")
        self.assertFalse(os.path.exists(os.path.join(bare, ".git", "agent-rules")))

    def test_no_rules_passes_and_writes_nothing(self):
        os.unlink(os.path.join(self.root, ".agents", "hooked-rules", "repo-commit-types.md"))
        self.assertEqual(self.call(self.plain("tool-before", command="git commit")), ("", 0))
        self.assertFalse(os.path.exists(self.state_dir))

    def test_pass_writes_no_state(self):
        self.assertEqual(self.call(self.plain("tool-before", command="git status")), ("", 0))
        self.assertFalse(os.path.exists(self.store.session_path("s1")))

    def test_malformed_event_plain(self):
        for text in ("not json", "[1, 2]", json.dumps({"event": "tool-before"}), "", "{\"event\": "):
            with self.subTest(text=text):
                out, code = self.call(text)
                self.assert_plain_notice(out, code)
                self.assertTrue(self.stderr.strip())
                self.assertEqual(len(self.stderr.strip().splitlines()), 1, self.stderr)

    def test_malformed_event_claude(self):
        for text in ("not json", "", "[]", json.dumps({"hook_event_name": ["PreToolUse"]})):
            with self.subTest(text=text):
                self.assert_silent_notice(*self.call(text, fmt="claude"))

    def test_claude_failure_with_a_context_channel(self):
        for name in ("PostToolUse", "UserPromptSubmit"):
            out, code = self.call(json.dumps({"hook_event_name": name, "cwd": self.root}), fmt="claude")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), {"hookSpecificOutput": {
                "hookEventName": name, "additionalContext": FAILURE_NOTICE,
            }})

    def test_claude_failure_without_a_context_channel(self):
        for name in ("PreToolUse", "PreCompact", "SessionStart"):
            text = json.dumps({"hook_event_name": name, "cwd": self.root})
            self.assert_silent_notice(*self.call(text, fmt="claude"))

    def test_unknown_format(self):
        self.assert_silent_notice(*self.call(self.plain("tool-before", command="git commit"), fmt="xml"))

    def test_unreadable_rule(self):
        self.write_rule("repo-broken.md", b"---\npointer: \"\xff\xfe\"\ngates: [\"git\"]\n---\nx\n")
        out, code = self.call(self.plain("tool-before", command="git commit"))
        self.assert_plain_notice(out, code)
        self.assertIn("repo-broken.md", self.stderr)
        self.assert_silent_notice(*self.call(self.claude_bash("PreToolUse", "git commit"), fmt="claude"))
        out, code = self.call(self.claude_bash("PostToolUse", "openspec instructions finalize"), fmt="claude")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["additionalContext"], FAILURE_NOTICE)
        self.assertFalse(os.path.exists(self.store.session_path("s1")))

    def test_invalid_rule_grammar(self):
        self.write_rule("repo-bad.md", "---\npointer: \"p\"\nsticky: yes\n---\n")
        self.assert_plain_notice(*self.call(self.plain("tool-before", command="git commit")))

    def test_corrupt_state(self):
        os.makedirs(self.state_dir, exist_ok=True)
        path = self.store.session_path("s1")
        with open(path, "w", encoding="utf-8") as handle_:
            handle_.write("{not json")
        self.assert_plain_notice(*self.call(self.plain("tool-before", command="git commit")))
        self.assert_silent_notice(*self.call(self.claude_bash("PreToolUse", "git commit"), fmt="claude"))
        with open(path, "r", encoding="utf-8") as handle_:
            self.assertEqual(handle_.read(), "{not json")

    def test_lock_timeout_passes(self):
        with FileLock(self.store.session_lock_path("s1"), no_sleep, 1):
            self.assert_plain_notice(*self.call(self.plain("tool-before", command="git commit")))
            self.assertIn("LockTimeout", self.stderr)
        self.assertFalse(os.path.exists(self.store.session_path("s1")))

    def test_any_store_error_passes(self):
        def broken(state_dir):
            raise RuntimeError("boom")

        def raw_os_error(state_dir):
            store = quick_store(state_dir)
            store._publish = mock.Mock(side_effect=OSError("disk full"))
            return store

        for factory in (broken, raw_os_error):
            self.assert_plain_notice(*self.call(self.plain("tool-before", command="git commit"),
                                                store_factory=factory))
        self.assertFalse(os.path.exists(self.store.session_path("s1")))

    def test_render_error_passes(self):
        with mock.patch("agent_rules.decide", return_value=Decision("deny", "x", [])):
            out, code = self.call(self.claude_event("UserPromptSubmit", prompt="/opsx:apply"), fmt="claude")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["additionalContext"], FAILURE_NOTICE)
        self.assertEqual(code, 0)

    def test_prune_failure_is_swallowed(self):
        for error in (StoreError("index busy"), LockTimeout("index.lock"), OSError("gone")):
            def factory(state_dir, error=error):
                store = quick_store(state_dir)
                store.prune = mock.Mock(side_effect=error)
                return store

            out, code = self.call(self.plain("tool-before", session="p-%s" % type(error).__name__,
                                             command="git commit"), store_factory=factory)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["decision"], "deny")

    def test_prune_runs_after_the_transaction(self):
        seen = []

        def factory(state_dir):
            store = quick_store(state_dir)

            def prune(session):
                with FileLock(store.session_lock_path(session), no_sleep, 1):
                    seen.append(session)  # the session lock is free again
            store.prune = prune
            return store

        self.call(self.plain("tool-before", command="git commit"), store_factory=factory)
        self.assertEqual(seen, ["s1"])

    def test_fail_after_write_switch(self):
        out, code = self.call(self.plain("tool-before", command="git commit -m x"),
                              env={FAIL_AFTER_WRITE: "1"})
        self.assertEqual((out, code), ("", 1))
        self.assertIn(FAIL_AFTER_WRITE, self.stderr)
        self.assertEqual(self.entries()[("s1", self.rule.identity)]["state"], "pending")
        # Stale credit (accepted residual): the first later call confirms, the second passes as delivered.
        self.assertEqual(self.call(self.plain("tool-before", command="git commit -m y")), ("", 0))
        self.assertEqual(self.call(self.plain("tool-before", command="git commit -m z")), ("", 0))
        out, code = self.call(self.claude_bash("PostToolUse", "openspec instructions finalize"),
                              fmt="claude", env={FAIL_AFTER_WRITE: "1"})
        self.assertEqual((out, code), ("", 1))

    def test_fail_after_write_needs_exactly_one(self):
        for value in ("0", "", "true"):
            out, code = self.call(self.plain("tool-before", session="v%r" % value, command="git commit"),
                                  env={FAIL_AFTER_WRITE: value})
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["decision"], "deny")

    def test_state_lives_in_the_common_dir_of_a_linked_worktree(self):
        subprocess.run(
            ["git", "-C", self.root, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q",
             "--allow-empty", "-m", "init"],
            check=True,
        )
        linked = os.path.join(self.tmp, "linked")
        subprocess.run(["git", "-C", self.root, "worktree", "add", "-q", "-b", "b", linked], check=True)
        os.makedirs(os.path.join(linked, ".agents", "hooked-rules"))
        shutil.copy(os.path.join(self.root, ".agents", "hooked-rules", "repo-commit-types.md"),
                    os.path.join(linked, ".agents", "hooked-rules", "repo-commit-types.md"))
        payload = {"event": "tool-before", "session": "s9", "cwd": linked, "command": "git commit"}
        self.assertEqual(json.loads(self.call(payload)[0])["decision"], "deny")
        self.assertTrue(os.path.isfile(self.store.session_path("s9")))


class DiscoveryFailureTest(CheckoutFixture):
    def test_missing_git_gives_the_notice(self):
        for error in (FileNotFoundError("git"), subprocess.TimeoutExpired(["git"], 5)):
            with mock.patch("agent_rules.subprocess.run", side_effect=error):
                self.assert_plain_notice(*self.call(self.plain("tool-before", command="git commit")))
                self.assertIn("CheckoutError", self.stderr)
                self.assertIsNone(checkout_for(self.root))

    def test_unexpected_git_failure_gives_the_notice(self):
        failed = subprocess.CompletedProcess(["git"], 128, b"", b"fatal: detected dubious ownership\n")
        with mock.patch("agent_rules.subprocess.run", return_value=failed):
            self.assert_plain_notice(*self.call(self.plain("tool-before", command="git commit")))
        with self.assertRaises(CheckoutError):
            discover_checkout(self.make_dir("does", "not", "exist") + "\0")

    def test_genuine_non_checkout_is_silent(self):
        plain_dir = self.make_dir("plain-dir")
        os.environ["GIT_CEILING_DIRECTORIES"] = self.tmp
        self.addCleanup(os.environ.pop, "GIT_CEILING_DIRECTORIES", None)
        self.assertIsNone(discover_checkout(plain_dir))
        payload = {"event": "tool-before", "session": "s1", "cwd": plain_dir, "command": "git commit"}
        self.assertEqual(self.call(payload), ("", 0))
        self.assertEqual(self.stderr, "")

    def test_classified_by_the_diagnostic_line_only(self):
        lookalike = os.path.join(self.tmp, "missing", "not a git repository")
        with self.assertRaises(CheckoutError):
            discover_checkout(lookalike)
        payload = {"event": "tool-before", "session": "s1", "cwd": lookalike, "command": "git commit"}
        self.assert_plain_notice(*self.call(payload))

    def test_git_metadata_directory_is_silent(self):
        inside = os.path.join(self.root, ".git", "refs")
        self.assertIsNone(discover_checkout(inside))
        payload = {"event": "tool-before", "session": "s1", "cwd": inside, "command": "git commit"}
        self.assertEqual(self.call(payload), ("", 0))
        self.assertEqual(self.stderr, "")

    def test_diagnostic_must_start_the_first_line(self):
        for stderr in (b"warning: x\nfatal: not a git repository\n",
                       b"error: fatal: not a git repository\n",
                       b"fatal: cannot change to 'fatal: not a git repository': No such file\n"):
            failed = subprocess.CompletedProcess(["git"], 128, b"", stderr)
            with mock.patch("agent_rules.subprocess.run", return_value=failed):
                with self.assertRaises(CheckoutError, msg=stderr):
                    discover_checkout(self.root)
        for stderr in (b"fatal: not a git repository (or any parent up to mount point /)\n",
                       b"fatal: this operation must be run in a work tree\n"):
            failed = subprocess.CompletedProcess(["git"], 128, b"", stderr)
            with mock.patch("agent_rules.subprocess.run", return_value=failed):
                self.assertIsNone(discover_checkout(self.root), stderr)

    def test_localized_git_still_reads_as_non_checkout(self):
        plain_dir = self.make_dir("plain-dir")
        os.environ["GIT_CEILING_DIRECTORIES"] = self.tmp
        self.addCleanup(os.environ.pop, "GIT_CEILING_DIRECTORIES", None)
        with mock.patch.dict(os.environ, {"LANG": "de_DE.UTF-8", "LC_ALL": "de_DE.UTF-8",
                                          "LANGUAGE": "de"}):
            self.assertIsNone(discover_checkout(plain_dir))


class FailingStream(object):
    def __init__(self, error):
        self.error = error
        self.buffer = self

    def read(self, *args):
        raise self.error

    def write(self, *args):
        raise self.error

    def flush(self):
        raise self.error


class HookMainTest(CheckoutFixture):
    def run_main(self, argv, stdin_bytes=b"", stdin=None, stdout=None):
        out = io.StringIO()
        stdin = stdin or mock.Mock(buffer=io.BytesIO(stdin_bytes))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = hook_main(argv, stdin=stdin, stdout=stdout or out, env={}, store_factory=quick_store)
        self.stderr = stderr.getvalue()
        return code, out.getvalue()

    def test_usage_errors_fail_open(self):
        stdin = FailingStream(AssertionError("stdin must not be read"))
        for argv in (["--format", "claude", "--bogus"], ["--format"], ["--format", "xml"], [],
                     ["--format", "plain", "extra"]):
            with self.subTest(argv=argv):
                code, out = self.run_main(argv, stdin=stdin)
                self.assertEqual((code, out), (0, ""))
                self.assertIn(FAILURE_NOTICE, self.stderr)
                self.assertIn("usage", self.stderr)

    def test_failing_stdin_fails_open(self):
        for error in (OSError("stdin closed"), ValueError("I/O operation on closed file")):
            code, out = self.run_main(["--format", "plain"], stdin=FailingStream(error))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["text"], FAILURE_NOTICE)
            code, out = self.run_main(["--format", "claude"], stdin=FailingStream(error))
            self.assertEqual((code, out), (0, ""))
            self.assertIn(FAILURE_NOTICE, self.stderr)

    def test_undecodable_stdin_fails_open(self):
        code, out = self.run_main(["--format", "plain"], stdin_bytes=b"\xff\xfe")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["text"], FAILURE_NOTICE)

    def test_failing_stdout_fails_open(self):
        for index, error in enumerate((BrokenPipeError("pipe"), OSError("gone"))):
            payload = json.dumps(self.plain("tool-before", session="out-%d" % index,
                                            command="git commit -m x")).encode("utf-8")
            code, out = self.run_main(["--format", "plain"], stdin_bytes=payload,
                                      stdout=FailingStream(error))
            self.assertEqual(code, 0)
            self.assertIn(FAILURE_NOTICE, self.stderr)

    def test_decision_is_written(self):
        payload = json.dumps(self.plain("tool-before", command="git commit -m x")).encode("utf-8")
        code, out = self.run_main(["--format", "plain"], stdin_bytes=payload)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["decision"], "deny")
        self.assertTrue(out.endswith("\n"))


class HookCliTest(CheckoutFixture):
    def run_cli(self, stdin, *args):
        return subprocess.run(
            [sys.executable, "-I", CLI, "hook"] + list(args),
            input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def test_cli_plain_deny(self):
        payload = json.dumps(self.plain("tool-before", command="git commit -m x")).encode("utf-8")
        completed = self.run_cli(payload, "--format", "plain")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout.decode("utf-8"))["decision"], "deny")

    def test_cli_claude_pass_prints_nothing(self):
        payload = json.dumps(self.claude_bash("PreToolUse", "ls")).encode("utf-8")
        completed = self.run_cli(payload, "--format", "claude")
        self.assertEqual((completed.returncode, completed.stdout), (0, b""))

    def test_cli_bad_input_fails_open(self):
        for stdin in (b"\xff\xfe not utf-8", b"{"):
            completed = self.run_cli(stdin, "--format", "claude")
            self.assertEqual((completed.returncode, completed.stdout), (0, b""))
            self.assertIn(FAILURE_NOTICE.encode("utf-8"), completed.stderr)
            completed = self.run_cli(stdin, "--format", "plain")
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(json.loads(completed.stdout.decode("utf-8"))["text"], FAILURE_NOTICE)

    def test_cli_missing_or_unknown_format_fails_open(self):
        payload = json.dumps(self.claude_bash("PreToolUse", "git commit")).encode("utf-8")
        for args in ((), ("--format", "xml")):
            completed = self.run_cli(payload, *args)
            self.assertEqual((completed.returncode, completed.stdout), (0, b""), args)
            self.assertIn(FAILURE_NOTICE.encode("utf-8"), completed.stderr)

    def test_cli_usage_errors_exit_zero(self):
        for args in (("--format", "claude", "--bogus"), ("--format",), ("--format", "xml"), ()):
            completed = self.run_cli(b"", *args)
            self.assertEqual((completed.returncode, completed.stdout), (0, b""), args)
            self.assertIn(FAILURE_NOTICE.encode("utf-8"), completed.stderr)

    def test_cli_pointers_usage_error_still_exits_two(self):
        completed = subprocess.run([sys.executable, "-I", CLI, "pointers", "--bogus"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(completed.returncode, 2)
        completed = subprocess.run([sys.executable, "-I", CLI, "--bogus"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(completed.returncode, 2)

    def test_cli_hook_help_exits_zero(self):
        completed = self.run_cli(b"", "--help")
        self.assertEqual(completed.returncode, 0)
        self.assertIn(b"--format", completed.stdout)

    def run_broken(self, args, stdin, broken):
        """Run the CLI with each stream in ``broken`` connected to a pipe whose reader is gone."""
        pipes = {}
        for name in broken:
            read_end, write_end = os.pipe()
            os.close(read_end)
            pipes[name] = write_end
        try:
            completed = subprocess.run(
                [sys.executable, "-I", CLI, "hook"] + list(args), input=stdin,
                stdout=pipes.get("stdout", subprocess.PIPE), stderr=pipes.get("stderr", subprocess.PIPE),
            )
        finally:
            for write_end in pipes.values():
                os.close(write_end)
        return completed

    def test_cli_broken_streams_exit_zero(self):
        scenarios = (
            ("usage", ("--format", "claude", "--bogus"), lambda index: b""),
            ("help", ("--help",), lambda index: b""),
            ("valid", ("--format", "plain"), lambda index: json.dumps(
                self.plain("tool-before", session="broken-%d" % index, command="git commit -m x")
            ).encode("utf-8")),
            ("fail-open", ("--format", "plain"), lambda index: b"garbage"),
            ("fail-open-claude", ("--format", "claude"), lambda index: b"garbage"),
        )
        index = 0
        for label, args, stdin in scenarios:
            for broken in (("stdout",), ("stderr",), ("stdout", "stderr")):
                index += 1
                with self.subTest(scenario=label, broken=broken):
                    completed = self.run_broken(args, stdin(index), broken)
                    self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_cli_help_goes_to_stdout(self):
        completed = self.run_cli(b"", "--help")
        self.assertEqual(completed.returncode, 0)
        self.assertIn(b"--format", completed.stdout)
        self.assertNotIn(FAILURE_NOTICE.encode("utf-8"), completed.stderr)

    def test_cli_fail_after_write_exits_one(self):
        payload = json.dumps(self.plain("tool-before", command="git commit -m x")).encode("utf-8")
        environment = dict(os.environ)
        environment[FAIL_AFTER_WRITE] = "1"
        completed = subprocess.run(
            [sys.executable, "-I", CLI, "hook", "--format", "plain"],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
        )
        self.assertEqual((completed.returncode, completed.stdout), (1, b""))
        self.assertTrue(os.path.isfile(self.store.session_path("s1")))


REPO_ROOT = os.path.dirname(SCRIPTS)
COMMIT_TYPES_RULE = os.path.join(REPO_ROOT, ".agents", "hooked-rules", "repo-commit-types.md")
TYPE_LINE_PREFIX = "- `"


class RealRepoRuleTest(unittest.TestCase):
    """The repository's own hooked rules, read from this checkout."""

    def real_rule(self):
        with open(COMMIT_TYPES_RULE, "r", encoding="utf-8", newline="") as handle:
            return parse_rule("repo-commit-types.md", handle.read())

    def test_real_rules_load_without_errors(self):
        rules, errors = load_rules(REPO_ROOT)
        self.assertEqual(errors, [])
        self.assertIn("repo-commit-types.md", [rule.name for rule in rules])

    def test_commit_types_covers_commitlint(self):
        import commitlint

        names = []
        for line in self.real_rule().body.split("\n"):
            # A type line is a bullet starting with a backticked type name and an em dash.
            if line.startswith(TYPE_LINE_PREFIX) and "` — " in line:
                names.append(line[len(TYPE_LINE_PREFIX):line.index("` — ")])
        expected = commitlint.TYPES.split("|")
        self.assertEqual(len(expected), 11)
        self.assertEqual(sorted(names), sorted(expected))
        self.assertEqual(len(names), len(set(names)))

    def test_commit_types_rule_triggers(self):
        rule = self.real_rule()
        self.assertEqual(rule.skills, ["openspec-apply-change", "opsx:apply"])
        self.assertEqual(
            [pattern.pattern for pattern in rule.commands],
            ["\\bopenspec\\s+instructions\\s+finalize\\b"],
        )
        self.assertEqual(
            [pattern.pattern for pattern in rule.gates],
            ["\\bgit\\b[^;&|\\n]*?\\scommit\\b", "\\bgh\\s+pr\\s+(create|edit)\\b"],
        )
        self.assertIn("type(scope): description", rule.pointer)
        self.assertIn("type(scope): description", rule.body)

    def test_commit_types_reserves_breaking_markers_for_feat(self):
        body = " ".join(self.real_rule().body.split())
        self.assertIn(
            "Use `!` or a `BREAKING CHANGE:` footer only with `feat`: any `!` bumps the major version "
            "of the touched components, and major bumps are reserved for features.",
            body,
        )
        self.assertNotIn("whatever the type", body)

    def test_real_pointer_block_is_current(self):
        completed = subprocess.run(
            [sys.executable, "-I", CLI, "pointers", "--check", "--root", REPO_ROOT],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
