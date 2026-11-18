import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from agent_rules import (  # noqa: E402
    Decision,
    Event,
    EventError,
    RuleError,
    checkout_for,
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

    def test_pointer_check_fails_without_heading_and_write_repairs_it(self):
        self.rule("repo-a.md", "First")
        self.agents_text("# Agents\n")
        self.assertEqual(self.run_cli("--write").returncode, 0)
        good = self.agents_text()
        self.agents_text(good.replace("## Hook-delivered rules\n\n", ""))
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
        moved = good.replace("## Hook-delivered rules\n\n", "") + "\n## Hook-delivered rules\n"
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
        self.agents_text("# Agents\n\n## Hook-delivered rules\n\n<!-- hooked-rules:end -->\n")
        self.assertEqual(self.run_cli("--check").returncode, 1)
        self.assertEqual(self.run_cli("--write").returncode, 1)

    def test_stale_block_is_replaced_in_place_for_every_line_ending(self):
        self.rule("repo-a.md", "Fresh")
        template = (
            "# Agents\n\n## Hook-delivered rules\n\n"
            "<!-- hooked-rules:begin -->\n"
            "If your tool did not deliver a rule below, read its file yourself at the moment it names.\n\n"
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

    def test_missing_or_bad_cwd_passes(self):
        self.assertIsNone(checkout_for(os.path.join(self.tmp, "does-not-exist")))
        self.assertIsNone(checkout_for(""))
        self.assertIsNone(checkout_for(None))
