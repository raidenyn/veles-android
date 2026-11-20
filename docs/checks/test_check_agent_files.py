"""Tests for the repository agent-file layout check (parts 1, 2 and 3).

Each fixture is a throwaway git repository, so tests exercise the same git calls the
real run uses without touching this checkout. Stdlib only; Python 3.8 compatible.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

CHECKS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CHECKS)

from check_agent_files import (  # noqa: E402
    CODEOWNER_SAMPLE_PATHS,
    NESTED_EXCLUSIVE_DOCS,
    NODE_FILES,
    NOTICE_NESTED,
    NOTICE_ROOT,
    SCOPES,
    check_always_on_rules,
    check_budget,
    check_claude_hooks,
    check_codeowners,
    check_hooked_rules,
    check_links,
    check_opencode,
    check_plugin_mentions,
    check_pointer_block,
    check_repo,
    check_stop_predicates,
    list_files,
    list_links,
    main,
    parse_documented_skills,
    parse_frontmatter,
)

# The real engine: the fixture repositories carry a copy of it, and the fixture pointer block is
# rendered with it, so the checker and the runtime are tested against the same parser.
ENGINE_PATH = os.path.join(os.path.dirname(os.path.dirname(CHECKS)), "scripts", "agent_rules.py")


def _load_engine():
    spec = importlib.util.spec_from_file_location("fixture_agent_rules", ENGINE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENGINE = _load_engine()
with open(ENGINE_PATH, "r", encoding="utf-8", newline="") as _handle:
    ENGINE_TEXT = _handle.read()

# The brief's literals, repeated here so a wrong constant cannot satisfy its own fixture.
EXPECTED_NOTICE_ROOT = (
    "> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there."
)
EXPECTED_NOTICE_NESTED = (
    "> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; "
    "if you started in this folder, read this folder's rule files listed below by hand."
)
EXPECTED_NODE_FILES = (
    "android/AGENTS.md",
    "web-extension/AGENTS.md",
    "rust/AGENTS.md",
    "protocol/AGENTS.md",
    "native-bridge/AGENTS.md",
    "native-bridge/host/AGENTS.md",
    "native-bridge/installer/AGENTS.md",
    "native-bridge/installer/macos/AGENTS.md",
    "native-bridge/installer/windows/AGENTS.md",
    "verify/AGENTS.md",
    "scripts/AGENTS.md",
    "docs/AGENTS.md",
)
EXPECTED_EXCLUSIVE = ("docs/AGENTS.md", "native-bridge/AGENTS.md")
# The brief's scope map, repeated here so a wrong constant cannot satisfy its own fixture.
EXPECTED_SCOPES = {
    "repo": None,
    "android": "android/**",
    "web-extension": "web-extension/**",
    "rust": "rust/**",
    "protocol": "protocol/**",
    "native-bridge": "native-bridge/**",
    "host": "native-bridge/host/**",
    "installer": "native-bridge/installer/**",
    "macos": "native-bridge/installer/macos/**",
    "windows": "native-bridge/installer/windows/**",
    "verify": "verify/**",
    "scripts": "scripts/**",
    "docs": "docs/**",
}
EXPECTED_SCOPE_AGENTS = {
    "repo": "AGENTS.md",
    "android": "android/AGENTS.md",
    "web-extension": "web-extension/AGENTS.md",
    "rust": "rust/AGENTS.md",
    "protocol": "protocol/AGENTS.md",
    "native-bridge": "native-bridge/AGENTS.md",
    "host": "native-bridge/host/AGENTS.md",
    "installer": "native-bridge/installer/AGENTS.md",
    "macos": "native-bridge/installer/macos/AGENTS.md",
    "windows": "native-bridge/installer/windows/AGENTS.md",
    "verify": "verify/AGENTS.md",
    "scripts": "scripts/AGENTS.md",
    "docs": "docs/AGENTS.md",
}
RULE_SIZE_MAX = 24000
REPO_RULE_SIZE_MAX = 4096
SIZE_CAP_CASES = (  # (case, rule name, scope, size, expected size findings)
    ("repo at its cap", "repo-no-time-based-tests.md", "repo", REPO_RULE_SIZE_MAX, 0),
    ("repo over its cap", "repo-no-time-based-tests.md", "repo", REPO_RULE_SIZE_MAX + 1, 1),
    ("repo far over", "repo-no-time-based-tests.md", "repo", 30000, 1),
    ("repo over the node cap", "repo-no-time-based-tests.md", "repo", RULE_SIZE_MAX + 1, 1),
    ("node over the repo cap", "android-cap.md", "android", REPO_RULE_SIZE_MAX + 1, 0),
    ("node at its cap", "android-cap.md", "android", RULE_SIZE_MAX, 0),
    ("node over its cap", "android-cap.md", "android", RULE_SIZE_MAX + 1, 1),
)
# The brief's documented OpenSpec skill list, repeated here so a wrong parser cannot satisfy
# its own fixture. `docs/openspec-skills.md` is the authoritative source at check time.
EXPECTED_OPENSPEC_SKILLS = (
    "openspec-apply-change",
    "openspec-archive-change",
    "openspec-bulk-archive-change",
    "openspec-continue-change",
    "openspec-explore",
    "openspec-ff-change",
    "openspec-new-change",
    "openspec-propose",
    "openspec-sync-specs",
    "openspec-update-change",
    "openspec-verify-change",
)
# The Global Constraints override targets, repeated here verbatim.
EXPECTED_OVERRIDE_TARGETS = {
    "android/AGENTS.md": ["docs"],
    "web-extension/AGENTS.md": ["docs"],
    "rust/AGENTS.md": ["docs"],
    "protocol/AGENTS.md": ["docs"],
    "native-bridge/host/AGENTS.md": ["docs", "native-host"],
    "native-bridge/installer/AGENTS.md": ["docs"],
    "native-bridge/installer/macos/AGENTS.md": ["docs"],
    "native-bridge/installer/windows/AGENTS.md": ["docs"],
    "verify/AGENTS.md": ["docs"],
    "scripts/AGENTS.md": ["docs"],
    ".github/CODEOWNERS": ["docs"],
    ".github/components.json": ["tooling", "docs"],
    ".github/workflows/check-docs.yml": ["docs", "tooling"],
    "scripts/verify-commands.json": ["docs", "tooling"],
}
EXPECTED_CODEOWNER_PATTERNS = (
    "/opencode.json",
    "/.claude/",
    "/.mcp.json",
    "/.agents/",
    "**/AGENTS.md",
    "/docs/superspec-workflow.md",
    "/docs/agent-guidance.md",
    "/docs/openspec-workflow.md",
    "/docs/opencode-rules.md",
    "/openspec/config.yaml",
    "/openspec/schemas/",
    "/scripts/agent_rules.py",
    "/scripts/opencode-hooked-rules/",
)
HOOK_COMMAND = 'python3 -I "$CLAUDE_PROJECT_DIR/scripts/agent_rules.py" hook --format claude'
# The verify hook that `Stop` holds after the engine command: design D11 fixes both texts and
# timeouts, so they are repeated here rather than imported from the module under test.
VERIFY_COMMAND = 'python3 -I "$CLAUDE_PROJECT_DIR/scripts/verify.py" --hook claude || true'
VERIFY_TIMEOUT = 1800
# (event, matcher) per hook; a matcher of None means the entry carries no `matcher` key.
EXPECTED_HOOKS = (
    ("PreToolUse", "Bash"),
    ("PostToolUse", "Bash|Skill|Read"),
    ("UserPromptSubmit", None),
    ("PreCompact", None),
    ("Stop", None),
)
OPENCODE_PACKAGE = "opencode-rules@>=2.0.0 <3"
LOCAL_PLUGIN = "./scripts/opencode-hooked-rules"
LOCAL_PLUGIN_INDEX = "scripts/opencode-hooked-rules/index.js"
# The three always-on `repo-*` rules the root AGENTS.md lists, once the five workflow rules moved
# to `.agents/hooked-rules/`.
EXPECTED_REPO_RULES = (
    "repo-no-time-based-tests.md",
    "repo-tracker-intake.md",
    "repo-signing-keys.md",
)
# Every rule that moved; the value is its file name under `.agents/hooked-rules/`.
EXPECTED_MOVED_RULES = {
    "repo-conventional-commits.md": "repo-commit-types.md",
    "repo-pre-push-checks.md": "repo-pre-push-checks.md",
    "repo-real-device-testing.md": "repo-real-device-testing.md",
    "repo-review-models.md": "repo-review-models.md",
    "repo-superspec-closeout.md": "repo-superspec-closeout.md",
    "repo-superspec-review-gate.md": "repo-superspec-review-gate.md",
}
# The brief's predicate registry and delivery-budget trigger commands, repeated here so a wrong
# constant cannot satisfy its own fixture.
EXPECTED_STOP_PREDICATES = ("superspec-finalize-pending",)
EXPECTED_BUDGET_COMMANDS = tuple(
    "openspec instructions %s" % artifact
    for artifact in (
        "brainstorm",
        "proposal",
        "design",
        "specs",
        "tasks",
        "plan",
        "apply",
        "verify",
        "finalize",
    )
) + (
    "openspec new change x",
    "openspec instructions verify && openspec instructions finalize",
)
BUDGET_SKILL = "budget-probe"
MOVED_RULE = ".agents/rules/repo-conventional-commits.md"
HOOKED_DIR = ".agents/hooked-rules/"
HOOKED_NAME = "repo-commit-types.md"
HOOKED_PATH = HOOKED_DIR + HOOKED_NAME
HOOKED_POINTER = "Fixture commit types: read before `git commit`."
# Every hooked rule the clean fixture carries: one per moved rule's new path, so a deleted target
# is a missing rule and not an otherwise empty directory.
HOOKED_TARGETS = tuple(sorted(set(EXPECTED_MOVED_RULES.values())))
HOOKED_POINTER_OTHER = "Fixture workflow rule: read before acting."
ENGINE_FILE = "scripts/agent_rules.py"


def hooked_rule_text(pointer=HOOKED_POINTER, lines=None, body="Fixture body.\n"):
    """A hooked rule; ``lines`` replaces the frontmatter lines after `pointer`."""
    if lines is None:
        lines = ["gates:", '  - "\\\\bgit\\\\b[^;&|\\\\n]*?\\\\scommit\\\\b"']
    head = ['pointer: "%s"' % pointer] if pointer is not None else []
    return "---\n" + "\n".join(head + list(lines)) + "\n---\n" + body


def fixture_hooked_rules():
    """The clean fixture's ``{file name: text}`` for every moved rule's target."""
    texts = {}
    for name in HOOKED_TARGETS:
        pointer = HOOKED_POINTER if name == HOOKED_NAME else HOOKED_POINTER_OTHER
        texts[name] = hooked_rule_text(pointer=pointer)
    return texts


def budget_rule_text(skills=(), stops=(), size=0):
    """A hooked rule whose emitted text (``rule_text``) is exactly ``size`` ASCII bytes.

    The body is ``size`` bytes plus its newline, well under the engine's 4,096-byte body cap; the
    triggers list only the given skills or stops, so the rule is matched by that trigger alone.
    """
    if not 0 < size <= 4095:
        raise AssertionError("the emitted text must be 1..4095 bytes, got %d" % size)
    lines = []
    if skills:
        lines.extend(["skills:"] + ['  - "%s"' % skill for skill in skills])
    if stops:
        lines.extend(["stops:"] + ['  - "%s"' % stop for stop in stops])
    return hooked_rule_text(lines=lines, body="x" * size + "\n")


def pointer_block(rule_texts=None):
    """The engine's pointer block over ``{name: text}`` (the fixture rules by default)."""
    if rule_texts is None:
        rule_texts = fixture_hooked_rules()
    rules = [ENGINE.parse_rule(name, text) for name, text in sorted(rule_texts.items())]
    return ENGINE.render_pointer_block(rules)


def skill_section(skills):
    lines = ["## OpenSpec skills", ""]
    lines.extend("- `%s`" % name for name in skills)
    return lines


def nested_text(
    node, notice=EXPECTED_NOTICE_NESTED, rules=True, target_lines=None, commands=None
):
    lines = ["# %s/AGENTS.md" % node, ""]
    if notice is not None:
        lines.extend([notice, ""])
    lines.extend(["## Commands", ""])
    if commands:
        lines.extend(["Run from the repository root:", "", "```bash"])
        lines.extend(commands)
        lines.extend(["```", ""])
    else:
        lines.extend(["None.", ""])
    if rules:
        lines.extend(["## Rules", "", "Follow `.agents/rules/`."])
    else:
        lines.extend(["## Notes", "", "No rules section here."])
    if target_lines is not None:
        if len(lines) > target_lines:
            raise AssertionError("base nested text is already %d lines" % len(lines))
        lines.extend(["Filler line for the line cap."] * (target_lines - len(lines)))
    return "\n".join(lines) + "\n"


def contributing_text(mention=True):
    lines = ["# Contributing", ""]
    if mention:
        lines.extend(
            [
                "- **OpenCode** needs the `opencode-rules` plugin at the range pinned in `opencode.json`.",
                "",
            ]
        )
    lines.extend(["## Build", "", "Run the build."])
    return "\n".join(lines) + "\n"


def opencode_text(
    package=OPENCODE_PACKAGE,
    plugins=None,
    extra=None,
    directories=(".agents/rules",),
):
    if plugins is None:
        plugins = [
            {"package": package, "options": {"additionalRuleDirectories": list(directories)}},
            LOCAL_PLUGIN,
        ]
    data = {"$schema": "https://opencode.ai/config.json", "plugins": plugins}
    if extra:
        data.update(extra)
    return json.dumps(data, indent=2) + "\n"


def codeowners_text(patterns=None):
    patterns = tuple(EXPECTED_CODEOWNER_PATTERNS if patterns is None else patterns)
    return "# Agent guidance and configuration.\n" + "".join(
        "%-24s @raidenyn\n" % pattern for pattern in patterns
    )


def hooks_block(drop=None, matchers=None, commands=None, timeouts=None):
    """The expected `hooks` block, with per-event overrides for the negative tests.

    ``drop`` names events to omit; ``matchers`` and ``commands`` replace a value; ``timeouts``
    replaces the timeout, and a value of ``False`` omits the key. The overrides apply to the
    event's first command, so `Stop` keeps its verify command second.
    """
    block = {}
    for event, matcher in EXPECTED_HOOKS:
        if drop is not None and event == drop:
            continue
        matcher = (matchers or {}).get(event, matcher)
        command = {"type": "command", "command": (commands or {}).get(event, HOOK_COMMAND)}
        timeout = (timeouts or {}).get(event, 5)
        if timeout is not False:
            command["timeout"] = timeout
        hooks = [command]
        if event == "Stop":
            hooks.append(
                {"type": "command", "command": VERIFY_COMMAND, "timeout": VERIFY_TIMEOUT}
            )
        entry = {"hooks": hooks}
        if matcher is not None:
            entry = {"matcher": matcher, "hooks": hooks}
        block[event] = [entry]
    return block


def settings_text(hooks=None, extra=None):
    data = {"enabledPlugins": {}, "hooks": hooks_block() if hooks is None else hooks}
    if extra:
        data.update(extra)
    return json.dumps(data, indent=2) + "\n"


def root_text(
    dirs=None,
    target_lines=None,
    notice=EXPECTED_NOTICE_ROOT,
    mention=True,
    block=None,
    heading=True,
):
    """The fixture root AGENTS.md; ``block`` replaces the pointer block, ``False`` omits it."""
    dirs = list(EXPECTED_NODE_FILES if dirs is None else dirs)
    lines = ["# AGENTS.md", ""]
    if notice is not None:
        lines.extend([notice, ""])
    lines.extend(["## Node map", ""])
    for path in dirs:
        node = path[: -len("/AGENTS.md")]
        lines.append("- `%s/` — the %s node." % (node, node))
    if mention:
        lines.extend(
            [
                "",
                "## Tools",
                "",
                "- **OpenCode** needs the `opencode-rules` plugin at the range pinned in `opencode.json`.",
            ]
        )
    lines.extend(["", "## Rules", "", "Repo-wide rules live in `.agents/rules/`:"])
    for index in range(0, len(EXPECTED_REPO_RULES), 2):
        lines.append(
            ", ".join(
                "`.agents/rules/%s`" % name
                for name in EXPECTED_REPO_RULES[index:index + 2]
            )
        )
    if block is not False:
        lines.append("")
        if heading:
            lines.append("## Hook-delivered rules")
        lines.extend((pointer_block() if block is None else block).split("\n"))
    if target_lines is not None:
        while len(lines) < target_lines:
            lines.append("Filler line for the line cap.")
        if len(lines) != target_lines:
            raise AssertionError("base root text is already %d lines" % len(lines))
    return "\n".join(lines) + "\n"


def default_overrides():
    overrides = {
        path: ["docs"] for path in EXPECTED_NODE_FILES if path not in EXPECTED_EXCLUSIVE
    }
    for path, target in EXPECTED_OVERRIDE_TARGETS.items():
        overrides[path] = list(target)
    return overrides


FIXTURE_NODES = {"docs": {"paths": ["docs/"]}}
# The smallest table the drift check accepts: one node, one documented command whose doc is
# that node's AGENTS.md, named in the component map so the node sets agree.
FIXTURE_TABLE = {
    "version": 1,
    "requirements": {},
    "producers": [],
    "nodes": {
        "docs": {
            "commands": [
                {
                    "id": "docs.offline-check",
                    "kind": "check",
                    "cwd": ".",
                    "run": ["python3", "docs/checks/check_offline.py", "--root", "."],
                    "doc": "docs/AGENTS.md",
                    "requires": [],
                    "inputs": [],
                }
            ],
            "excluded": [],
        }
    },
}


def components_json(overrides=None):
    return json.dumps(
        {
            "since": "0" * 40,
            "nodes": FIXTURE_NODES,
            "global-overrides": default_overrides() if overrides is None else overrides,
        },
        indent=2,
    ) + "\n"


def rule_text(frontmatter, body=""):
    """A rule file whose frontmatter is ``frontmatter`` verbatim (no leading `---`)."""
    return "---\n" + frontmatter + "---\n" + body


def repo_rule_text(trigger="always_on"):
    return rule_text("trigger: %s\n" % trigger)


def node_rule_text(pattern, trigger="glob"):
    return rule_text(
        'trigger: %s\nglobs:\n  - "%s"\npaths:\n  - "%s"\n' % (trigger, pattern, pattern)
    )


def listed(text, *names):
    """The AGENTS.md text with rule-file mentions appended to its last (``## Rules``) section."""
    lines = text.rstrip("\n").split("\n") + [""]
    for name in names:
        lines.append("- `.agents/rules/%s` — scoped rule for this folder." % name)
    return "\n".join(lines) + "\n"


def with_rule(files, name, text, scope):
    """Add a rule file and list it in the AGENTS.md of its scope."""
    files[".agents/rules/" + name] = text
    agents_path = EXPECTED_SCOPE_AGENTS[scope]
    files[agents_path] = listed(files[agents_path], name)
    return files


def sized_rule(size, pattern=None):
    """A valid rule padded with body text to exactly ``size`` bytes.

    Without ``pattern`` it is a `repo-*` rule; with one it is a node rule for that scope.
    """
    base = repo_rule_text() if pattern is None else node_rule_text(pattern)
    padding = size - len(base)
    if padding < 0:
        raise AssertionError("size %d is smaller than the %d-byte header" % (size, len(base)))
    filler = "x" * 79 + "\n"
    text = base + filler * (padding // len(filler)) + "x" * (padding % len(filler))
    if len(text) != size:
        raise AssertionError("fixture is %d bytes, expected %d" % (len(text), size))
    return text


def clean_files(skills=None):
    skills = list(EXPECTED_OPENSPEC_SKILLS if skills is None else skills)
    files = {
        "AGENTS.md": root_text(),
        "CONTRIBUTING.md": contributing_text(),
        ".github/components.json": components_json(),
        ".github/CODEOWNERS": codeowners_text(),
        "opencode.json": opencode_text(),
        ".claude/settings.json": settings_text(),
        ".agents/skills/.openspec-target": "claude\n",
        LOCAL_PLUGIN_INDEX: "export default {}\n",
        ENGINE_FILE: ENGINE_TEXT,
        "scripts/verify-commands.json": json.dumps(FIXTURE_TABLE, indent=2) + "\n",
    }
    for name, text in fixture_hooked_rules().items():
        files[HOOKED_DIR + name] = text
    for name in EXPECTED_REPO_RULES:
        files[".agents/rules/" + name] = repo_rule_text()
    for path in EXPECTED_NODE_FILES:
        node = path[: -len("/AGENTS.md")]
        files[path] = nested_text(node)
    files["docs/AGENTS.md"] = nested_text(
        "docs", commands=["python3 docs/checks/check_offline.py --root ."]
    )
    files["docs/openspec-skills.md"] = "\n".join(skill_section(skills)) + "\n"
    files["docs/agent-guidance.md"] = "# Agent-file guidance\n\nThe 40-line policy.\n"
    files["docs/openspec-workflow.md"] = "# OpenSpec workflow\n\nThe update procedure.\n"
    files["docs/opencode-rules.md"] = "# OpenCode\n\nThe plugin guidance.\n"
    files["docs/android.md"] = "# Android\n\nThe architecture.\n"
    for name in skills:
        files[".agents/skills/%s/SKILL.md" % name] = "# %s\n\nGenerated skill fixture.\n" % name
    return files


def default_symlinks(skills=None):
    skills = list(EXPECTED_OPENSPEC_SKILLS if skills is None else skills)
    links = {".claude/rules": "../.agents/rules"}
    for name in skills:
        links[".claude/skills/" + name] = "../../.agents/skills/" + name
    return links


def write_text(root, rel, text):
    full = os.path.join(root, rel)
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def make_repo(tmp, files, symlinks=None):
    """Create a committed fixture repository under ``tmp`` and return its path."""
    root = tempfile.mkdtemp(prefix="repo-", dir=tmp)
    config = os.path.join(tmp, "gitconfig-empty")
    with open(config, "w", encoding="utf-8") as handle:
        handle.write("")

    env = dict(os.environ)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX"):
        env.pop(name, None)
    env.update(
        {
            "GIT_CONFIG_GLOBAL": config,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
        }
    )

    def git(*args):
        result = subprocess.run(
            ["git"] + list(args),
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise AssertionError(
                "git %s failed: %s" % (" ".join(args), result.stderr.decode("utf-8", "replace"))
            )
        return result.stdout.decode("utf-8", "replace")

    git("init", "-q", "-b", "master")
    git("config", "core.autocrlf", "false")
    git("config", "core.symlinks", "true")
    for rel, text in files.items():
        write_text(root, rel, text)
    for rel, target in (symlinks or {}).items():
        full = os.path.join(root, rel)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        os.symlink(target, full)
    git("add", "-A")
    git("commit", "-q", "--no-verify", "-m", "test: fixture")
    return root


def git_output(root, *args):
    """Run git in ``root`` with the test process's (isolated) environment."""
    result = subprocess.run(
        ["git"] + list(args), cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode != 0:
        raise AssertionError(
            "git %s failed: %s" % (" ".join(args), result.stderr.decode("utf-8", "replace"))
        )
    return result.stdout.decode("utf-8", "replace")


class AgentFileCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="veles-agent-files-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # Isolate every git call this process makes, including the checker's own queries, so a
        # developer's global config, excludes file or attributes cannot change the results.
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

    def repo(self, files=None, symlinks=None, skills=None):
        if files is None:
            files = clean_files(skills)
        if symlinks is None:
            symlinks = default_symlinks(skills)
        return make_repo(self.tmp, files, symlinks)

    def assert_finding(self, findings, severity, path, fragment):
        matching = [
            finding for finding in findings if finding.path == path and fragment in finding.message
        ]
        self.assertEqual(
            len(matching),
            1,
            "expected one finding for %s containing %r, got %r" % (path, fragment, findings),
        )
        self.assertEqual(matching[0].severity, severity, matching[0])

    def test_constants_are_exact(self):
        import check_agent_files as check_module

        self.assertEqual(NOTICE_ROOT, EXPECTED_NOTICE_ROOT)
        self.assertEqual(NOTICE_NESTED, EXPECTED_NOTICE_NESTED)
        self.assertEqual(tuple(NODE_FILES), EXPECTED_NODE_FILES)
        self.assertEqual(tuple(NESTED_EXCLUSIVE_DOCS), EXPECTED_EXCLUSIVE)
        self.assertEqual(getattr(check_module, "RULE_SIZE_MAX", None), RULE_SIZE_MAX)
        self.assertEqual(getattr(check_module, "REPO_RULE_SIZE_MAX", None), REPO_RULE_SIZE_MAX)
        self.assertEqual(getattr(check_module, "AGENTS_LINE_MAX", None), 40)
        self.assertEqual(tuple(check_module.ALWAYS_ON_REPO_RULES), EXPECTED_REPO_RULES)
        self.assertEqual(tuple(check_module.CLAUDE_HOOKS), EXPECTED_HOOKS)
        self.assertEqual(getattr(check_module, "CLAUDE_VERIFY_COMMAND", None), VERIFY_COMMAND)
        self.assertEqual(getattr(check_module, "CLAUDE_VERIFY_TIMEOUT", None), VERIFY_TIMEOUT)
        self.assertEqual(tuple(check_module.BUDGET_COMMANDS), EXPECTED_BUDGET_COMMANDS)
        self.assertEqual(
            dict(check_module.MOVED_RULES),
            dict(
                (name, ".agents/hooked-rules/" + target)
                for name, target in EXPECTED_MOVED_RULES.items()
            ),
        )

    def test_clean_fixture_passes(self):
        root = self.repo()
        self.assertEqual(check_repo(root), [])
        self.assertEqual(check_repo(root, platform="win32"), [])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--root", root]), 0)
        self.assertEqual(output.getvalue(), "")

    def test_clean_fixture_has_no_findings(self):
        # The fixture carries a minimal valid command table and the docs it references, so the
        # drift check is clean on the same tree the other checks call clean.
        self.assertEqual(check_repo(self.repo()), [])

    def test_nested_claude_md_fails(self):
        files = clean_files()
        files["android/CLAUDE.md"] = "# Legacy guidance\n"
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", "android/CLAUDE.md", "legacy agent file")
        self.assertEqual(len(findings), 1, findings)

    def test_claude_local_and_dot_claude_md_fail(self):
        files = clean_files()
        files["CLAUDE.md"] = "# Legacy\n"
        files["CLAUDE.local.md"] = "# Local legacy\n"
        files[".claude/CLAUDE.md"] = "# Nested legacy\n"
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", "CLAUDE.md", "legacy agent file")
        self.assert_finding(findings, "error", "CLAUDE.local.md", "legacy agent file")
        self.assert_finding(findings, "error", ".claude/CLAUDE.md", "legacy agent file")
        self.assertEqual(len(findings), 3, findings)

    def test_contribution_md_fails(self):
        files = clean_files()
        files["CONTRIBUTION.md"] = "# Wrong spelling\n"
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", "CONTRIBUTION.md", "CONTRIBUTING.md")
        self.assertEqual(len(findings), 1, findings)

    def test_opencode_dir_and_claude_commands_fail(self):
        files = clean_files()
        files[".opencode/agent/reviewer.md"] = "---\n---\n"
        files[".claude/commands/fix-issue.md"] = "# Fix\n"
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".opencode/agent/reviewer.md", "legacy agent tooling path")
        self.assert_finding(findings, "error", ".claude/commands/fix-issue.md", "legacy agent tooling path")
        self.assertEqual(len(findings), 2, findings)

    def test_agents_line_boundaries(self):
        cases = (
            ("AGENTS.md", 40, 0),
            ("AGENTS.md", 41, 1),
            ("rust/AGENTS.md", 40, 0),
            ("rust/AGENTS.md", 41, 1),
        )
        for path, target, expected in cases:
            with self.subTest(path=path, lines=target):
                files = clean_files()
                files[path] = (
                    root_text(target_lines=target)
                    if path == "AGENTS.md"
                    else nested_text("rust", target_lines=target)
                )
                findings = check_repo(self.repo(files))
                cap = [
                    finding
                    for finding in findings
                    if finding.path == path
                    and "the AGENTS.md cap is 40 lines" in finding.message
                ]
                self.assertEqual(len(cap), expected, findings)
                if expected:
                    self.assertEqual(cap[0].severity, "error")

    def test_nested_missing_rules_section_fails(self):
        files = clean_files()
        files["scripts/AGENTS.md"] = nested_text("scripts", rules=False)
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", "scripts/AGENTS.md", "missing a `## Rules` section")
        self.assertEqual(len(findings), 1, findings)

    def test_crlf_agents_md_notice_still_matches(self):
        files = clean_files()
        files["AGENTS.md"] = root_text().replace("\n", "\r\n")
        files["web-extension/AGENTS.md"] = nested_text("web-extension").replace("\n", "\r\n")
        self.assertEqual(check_repo(self.repo(files)), [])

    def test_missing_notice_fails(self):
        files = clean_files()
        files["AGENTS.md"] = root_text(notice=None)
        files["rust/AGENTS.md"] = nested_text("rust", notice=None)
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", "AGENTS.md", "missing the run-from-root notice")
        self.assert_finding(findings, "error", "rust/AGENTS.md", "missing the nested run-from-root notice")
        self.assertEqual(len(findings), 2, findings)

    def test_missing_node_file_fails(self):
        files = clean_files()
        del files["verify/AGENTS.md"]
        files["AGENTS.md"] = root_text(dirs=[p for p in EXPECTED_NODE_FILES if p != "verify/AGENTS.md"])
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", "verify/AGENTS.md", "required AGENTS.md")
        self.assertEqual(len(findings), 1, findings)

    def test_unlisted_nested_agents_md_fails(self):
        files = clean_files()
        files["extra/AGENTS.md"] = nested_text("extra")
        overrides = default_overrides()
        overrides["extra/AGENTS.md"] = ["docs"]
        files[".github/components.json"] = components_json(overrides)
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", "extra/AGENTS.md", "not listed in the root node map")
        self.assertEqual(len(findings), 1, findings)

    def test_extra_nested_agents_md_without_notice_or_rules_fails(self):
        # A tracked nested AGENTS.md beyond the fixed 12 is still a nested agent file: listing it
        # in the node map and the component map must not excuse missing notice and `## Rules`.
        files = clean_files()
        files["extra/AGENTS.md"] = "# extra/AGENTS.md\n\n## Commands\n\nNone.\n"
        files["AGENTS.md"] = root_text(dirs=list(EXPECTED_NODE_FILES) + ["extra/AGENTS.md"])
        overrides = default_overrides()
        overrides["extra/AGENTS.md"] = ["docs"]
        files[".github/components.json"] = components_json(overrides)
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", "extra/AGENTS.md", "missing the nested run-from-root notice"
        )
        self.assert_finding(findings, "error", "extra/AGENTS.md", "missing a `## Rules` section")
        self.assertEqual(len(findings), 2, findings)

    def test_listed_missing_file_fails(self):
        files = clean_files()
        files["AGENTS.md"] = root_text(dirs=list(EXPECTED_NODE_FILES) + ["ghost/AGENTS.md"])
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", "ghost/AGENTS.md", "listed in the root node map")
        self.assertEqual(len(findings), 1, findings)

    def test_nested_without_override_fails(self):
        overrides = default_overrides()
        del overrides["android/AGENTS.md"]
        files = clean_files()
        files[".github/components.json"] = components_json(overrides)
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", "android/AGENTS.md", "global-overrides")
        self.assertEqual(len(findings), 1, findings)

    def test_main_reports_findings(self):
        files = clean_files()
        files["CLAUDE.md"] = "# Legacy\n"
        root = self.repo(files)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--root", root])
        self.assertEqual(code, 1)
        printed = output.getvalue().splitlines()
        self.assertEqual(len(printed), 1, printed)
        self.assertTrue(printed[0].startswith("error CLAUDE.md: "), printed)

    def test_invalid_root_reports_environment_failure(self):
        missing = os.path.join(self.tmp, "does-not-exist")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--root", missing])
        self.assertEqual(code, 1)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("error %s: " % missing), lines)

        broken = os.path.join(self.tmp, "broken-repo")
        os.makedirs(broken)
        write_text(broken, ".git", "gitdir: /nonexistent/veles\n")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--root", broken])
        self.assertEqual(code, 1)
        printed = output.getvalue()
        self.assertTrue(printed.startswith("error %s: " % broken), printed)
        # Assert our own diagnostic, not Git's version-specific wording.
        self.assertIn("git ls-files", printed)
        self.assertIn("failed in %s" % broken, printed)

    def test_list_files_ignores_ignored_and_keeps_untracked(self):
        files = clean_files()
        files[".gitignore"] = "build/\n"
        root = self.repo(files, symlinks={"docs/link.md": "AGENTS.md"})
        write_text(root, "untracked.md", "# New\n")
        write_text(root, "build/out.txt", "ignored\n")
        listed = list_files(root)
        self.assertIn("AGENTS.md", listed)
        self.assertIn("untracked.md", listed)
        self.assertNotIn("build/out.txt", listed)
        self.assertIn("docs/link.md", listed)
        self.assertEqual([path for path in listed if path.endswith("/")], [])
        self.assertIn("docs/link.md", list_links(root))
        self.assertNotIn("AGENTS.md", list_links(root))

    def test_list_files_drops_nested_worktree_directory_entry(self):
        root = self.repo()
        nested = os.path.join(root, "nested-wt")
        os.makedirs(nested)
        # A nested repository is enough: git reports it as the directory entry `nested-wt/`,
        # never as files inside it, so list_files must drop the entry rather than call it a file.
        git_output(nested, "init", "-q")
        raw = git_output(root, "ls-files", "-z", "-co", "--exclude-standard").split("\0")
        self.assertIn("nested-wt/", raw)
        listed = list_files(root)
        self.assertNotIn("nested-wt/", listed)
        self.assertEqual([path for path in listed if path.startswith("nested-wt/")], [])

    def test_untracked_symlink_is_reported_as_link(self):
        root = self.repo()
        os.symlink("AGENTS.md", os.path.join(root, "new-link.md"))
        write_text(root, "new-file.md", "# New\n")
        self.assertIn("new-link.md", list_files(root))
        self.assertIn("new-link.md", list_links(root))
        self.assertNotIn("new-file.md", list_links(root))

    def test_scopes_map_is_exact(self):
        self.assertEqual(SCOPES, EXPECTED_SCOPES)

    def test_frontmatter_parses_supported_forms(self):
        block = '---\ntrigger: glob\nglobs:\n  - "android/**"\npaths:\n  - "android/**"\n---\n'
        self.assertEqual(
            parse_frontmatter(block),
            {"trigger": "glob", "globs": ["android/**"], "paths": ["android/**"]},
        )
        flow = (
            '---\ntrigger: glob\nglobs: ["android/**", "android/app/**"]\n'
            'paths: ["android/**"]\n---\n'
        )
        self.assertEqual(
            parse_frontmatter(flow),
            {"trigger": "glob", "globs": ["android/**", "android/app/**"], "paths": ["android/**"]},
        )
        spaced = "---\n\ntrigger: always_on\n\n---\n"
        self.assertEqual(parse_frontmatter(spaced), {"trigger": "always_on"})

    def test_frontmatter_decodes_quoted_escapes(self):
        text = (
            '---\ntrigger: glob\nglobs: ["android/\\"quoted\\"/**", "android/\\\\x"]\n'
            'paths:\n  - "android/\\\\y"\n---\n'
        )
        self.assertEqual(
            parse_frontmatter(text),
            {
                "trigger": "glob",
                "globs": ['android/"quoted"/**', "android/\\x"],
                "paths": ["android/\\y"],
            },
        )

    def test_frontmatter_rejects_unsupported_yaml(self):
        cases = {
            "anchor": "---\ntrigger: &anchor glob\n---\n",
            "alias": "---\ntrigger: *anchor\n---\n",
            "double-quoted scalar": '---\ntrigger: "always_on"\n---\n',
            "colon in value": "---\ntrigger: x: y\n---\n",
            "two spaces before a value": "---\ntrigger:  &anchor glob\n---\n",
            "nested map": "---\ntrigger:\n  glob: x\n---\n",
            "duplicate key": "---\ntrigger: always_on\ntrigger: glob\n---\n",
            "unquoted block item": "---\ntrigger: glob\nglobs:\n  - android/**\n---\n",
            "unquoted flow item": "---\ntrigger: glob\nglobs: [android/**]\n---\n",
            "invalid flow escape": '---\ntrigger: glob\nglobs: ["android/\\q"]\n---\n',
            "invalid block escape": '---\ntrigger: glob\nglobs:\n  - "android/\\q"\n---\n',
            "empty block list": '---\ntrigger: glob\nglobs:\npaths:\n  - "android/**"\n---\n',
            "tab indentation": '---\ntrigger: always_on\n\tglobs: ["android/**"]\n---\n',
            "single-quoted scalar": "---\ntrigger: 'always_on'\n---\n",
            "missing opening delimiter": "trigger: always_on\n---\n",
            "missing closing delimiter": "---\ntrigger: always_on\n",
        }
        for label, text in cases.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    parse_frontmatter(text)

    def test_frontmatter_strips_bom(self):
        text = "\ufeff" + node_rule_text("android/**")
        self.assertEqual(
            parse_frontmatter(text),
            {"trigger": "glob", "globs": ["android/**"], "paths": ["android/**"]},
        )
        files = with_rule(clean_files(), "android-bom.md", text, "android")
        self.assertEqual(check_repo(self.repo(files)), [])

    def test_crlf_rule_parses(self):
        lf = node_rule_text("android/**")
        crlf = lf.replace("\n", "\r\n")
        self.assertEqual(parse_frontmatter(crlf), parse_frontmatter(lf))
        self.assertEqual(parse_frontmatter(crlf)["globs"], ["android/**"])
        files = with_rule(clean_files(), "android-crlf.md", crlf, "android")
        self.assertEqual(check_repo(self.repo(files)), [])

    def test_rule_in_subdirectory_fails(self):
        files = clean_files()
        files[".agents/rules/android/extra.md"] = node_rule_text("android/**")
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".agents/rules/android/extra.md", "subdirectory")
        self.assertEqual(len(findings), 1, findings)

    def test_unknown_scope_name_fails(self):
        cases = (
            ("unknown-topic.md", "known scope"),
            ("README.md", "known scope"),
            ("android-topic.txt", "does not end with `.md`"),
        )
        for name, fragment in cases:
            with self.subTest(name=name):
                files = clean_files()
                files[".agents/rules/" + name] = repo_rule_text()
                findings = check_repo(self.repo(files))
                self.assert_finding(findings, "error", ".agents/rules/" + name, fragment)
                self.assertEqual(len(findings), 1, findings)

    def test_scope_resolved_by_longest_known_prefix(self):
        cases = (
            ("web-extension-x.md", "web-extension"),
            ("native-bridge-x.md", "native-bridge"),
            ("host-x.md", "host"),
        )
        for name, scope in cases:
            with self.subTest(name=name):
                pattern = EXPECTED_SCOPES[scope]
                files = with_rule(clean_files(), name, node_rule_text(pattern), scope)
                self.assertEqual(check_repo(self.repo(files)), [])

    def test_rule_size_caps(self):
        for case, name, scope, size, expected in SIZE_CAP_CASES:
            with self.subTest(case=case):
                files = with_rule(clean_files(), name, sized_rule(size, EXPECTED_SCOPES[scope]), scope)
                findings = check_repo(self.repo(files))
                if expected == 0:
                    self.assertEqual(findings, [])
                else:
                    self.assert_finding(findings, "error", ".agents/rules/" + name, "size cap")
                    self.assertEqual(len(findings), 1, findings)

    def test_unknown_frontmatter_key_fails(self):
        text = rule_text("trigger: always_on\nkeywords: otp\n")
        files = clean_files()
        files[".agents/rules/repo-no-time-based-tests.md"] = text
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", ".agents/rules/repo-no-time-based-tests.md", "keywords"
        )
        self.assertEqual(len(findings), 1, findings)

    def test_repo_rule_with_globs_fails(self):
        for key in ("globs", "paths"):
            with self.subTest(key=key):
                text = rule_text("trigger: always_on\n%s: [\"android/**\"]\n" % key)
                files = clean_files()
                files[".agents/rules/repo-no-time-based-tests.md"] = text
                findings = check_repo(self.repo(files))
                self.assert_finding(
                    findings,
                    "error",
                    ".agents/rules/repo-no-time-based-tests.md",
                    "must not set `%s`" % key,
                )
                self.assertEqual(len(findings), 1, findings)

    def test_repo_rule_must_be_always_on(self):
        files = clean_files()
        files[".agents/rules/repo-no-time-based-tests.md"] = repo_rule_text("glob")
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", ".agents/rules/repo-no-time-based-tests.md", "always_on"
        )
        self.assertEqual(len(findings), 1, findings)

    def test_node_rule_globs_must_be_list(self):
        # A quoted glob is outside the frontmatter grammar and fails as invalid frontmatter;
        # a plain scalar is a YAML string and fails the list requirement.
        cases = (
            (
                'trigger: glob\nglobs: "android/**"\npaths:\n  - "android/**"\n',
                "invalid frontmatter",
            ),
            (
                'trigger: glob\nglobs: android\npaths:\n  - "android/**"\n',
                "`globs` must be a YAML list",
            ),
        )
        for frontmatter, fragment in cases:
            with self.subTest(fragment=fragment):
                files = with_rule(
                    clean_files(), "android-string-globs.md", rule_text(frontmatter), "android"
                )
                findings = check_repo(self.repo(files))
                self.assert_finding(
                    findings, "error", ".agents/rules/android-string-globs.md", fragment
                )
                self.assertEqual(len(findings), 1, findings)

    def test_node_rule_scalar_paths_fails(self):
        cases = (
            (
                'trigger: glob\nglobs:\n  - "android/**"\npaths: "android/**"\n',
                "invalid frontmatter",
            ),
            (
                'trigger: glob\nglobs:\n  - "android/**"\npaths: android\n',
                "`paths` must be a YAML list",
            ),
        )
        for frontmatter, fragment in cases:
            with self.subTest(fragment=fragment):
                files = with_rule(
                    clean_files(), "android-string-paths.md", rule_text(frontmatter), "android"
                )
                findings = check_repo(self.repo(files))
                self.assert_finding(
                    findings, "error", ".agents/rules/android-string-paths.md", fragment
                )
                self.assertEqual(len(findings), 1, findings)

    def test_node_rule_globs_paths_must_match(self):
        text = rule_text('trigger: glob\nglobs:\n  - "android/**"\npaths:\n  - "android/app/**"\n')
        files = with_rule(clean_files(), "android-mismatch.md", text, "android")
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings,
            "error",
            ".agents/rules/android-mismatch.md",
            "`paths` must match `globs`",
        )
        self.assertEqual(len(findings), 1, findings)

    def test_node_rule_pattern_must_be_inside_scope(self):
        files = with_rule(
            clean_files(), "rust-wrong-pattern.md", node_rule_text("android/**"), "rust"
        )
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings,
            "error",
            ".agents/rules/rust-wrong-pattern.md",
            "`globs` must list patterns under `rust/`",
        )
        self.assert_finding(
            findings,
            "error",
            ".agents/rules/rust-wrong-pattern.md",
            "`paths` must list patterns under `rust/`",
        )
        self.assertEqual(len(findings), 2, findings)

    def test_node_rule_accepts_multiple_patterns_inside_scope(self):
        patterns = (
            '  - "native-bridge/installer/macos/**"\n'
            '  - "native-bridge/host/**"\n'
        )
        text = rule_text("trigger: glob\nglobs:\n" + patterns + "paths:\n" + patterns)
        files = with_rule(clean_files(), "native-bridge-multi.md", text, "native-bridge")
        findings = check_repo(self.repo(files))
        self.assertEqual(findings, [])

    def test_node_rule_rejects_parent_directory_traversal(self):
        text = rule_text(
            'trigger: glob\nglobs:\n  - "android/../rust/**"\n'
            'paths:\n  - "android/../rust/**"\n'
        )
        files = with_rule(clean_files(), "android-traversal.md", text, "android")
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings,
            "error",
            ".agents/rules/android-traversal.md",
            "`globs` must not use parent-directory traversal",
        )
        self.assert_finding(
            findings,
            "error",
            ".agents/rules/android-traversal.md",
            "`paths` must not use parent-directory traversal",
        )
        self.assertEqual(len(findings), 2, findings)

    def test_node_rule_rejects_pattern_outside_scope(self):
        text = rule_text(
            'trigger: glob\nglobs:\n  - "native-bridge/installer/macos/**"\n'
            '  - "native-bridge/host/**"\n'
            'paths:\n  - "native-bridge/installer/macos/**"\n'
            '  - "native-bridge/host/**"\n'
        )
        files = with_rule(clean_files(), "macos-cross.md", text, "macos")
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings,
            "error",
            ".agents/rules/macos-cross.md",
            "`globs` must list patterns under `native-bridge/installer/macos/`",
        )
        self.assert_finding(
            findings,
            "error",
            ".agents/rules/macos-cross.md",
            "`paths` must list patterns under `native-bridge/installer/macos/`",
        )
        self.assertEqual(len(findings), 2, findings)

    def test_brace_expansion_fails(self):
        text = rule_text(
            'trigger: glob\nglobs:\n  - "android/{app,core}/**"\n'
            'paths:\n  - "android/{app,core}/**"\n'
        )
        files = with_rule(clean_files(), "android-braces.md", text, "android")
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", ".agents/rules/android-braces.md", "`globs` must not use brace expansion"
        )
        self.assert_finding(
            findings, "error", ".agents/rules/android-braces.md", "`paths` must not use brace expansion"
        )
        self.assertEqual(len(findings), 2, findings)

    def test_node_rule_requires_trigger_glob(self):
        cases = {
            "missing": rule_text('globs:\n  - "android/**"\npaths:\n  - "android/**"\n'),
            "always_on": rule_text(
                'trigger: always_on\nglobs:\n  - "android/**"\npaths:\n  - "android/**"\n'
            ),
        }
        for label, text in cases.items():
            with self.subTest(label):
                files = with_rule(clean_files(), "android-trigger.md", text, "android")
                findings = check_repo(self.repo(files))
                self.assert_finding(
                    findings, "error", ".agents/rules/android-trigger.md", "trigger: glob"
                )
                self.assertEqual(len(findings), 1, findings)

    def test_rule_not_listed_by_scope_agents_md_fails(self):
        # Cross-folder "read this rule" pointers neither count as a listing nor fail the check.
        files = clean_files()
        files[".agents/rules/android-test.md"] = node_rule_text("android/**")
        for agents_path in ("AGENTS.md", "rust/AGENTS.md", "protocol/AGENTS.md"):
            files[agents_path] = listed(files[agents_path], "android-test.md")
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", ".agents/rules/android-test.md", "is not listed in android/AGENTS.md"
        )
        self.assertEqual(len(findings), 1, findings)
        files["android/AGENTS.md"] = listed(files["android/AGENTS.md"], "android-test.md")
        self.assertEqual(check_repo(self.repo(files)), [])

    def test_repo_rules_listed_by_root(self):
        # The root, not a node, lists the three always-on rules. Dropping a mention is reported
        # both by the rule's own listing check and by the always-on listing check.
        files = clean_files()
        files["AGENTS.md"] = root_text().replace("`.agents/rules/repo-tracker-intake.md`", "`BUILD.md`")
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", ".agents/rules/repo-tracker-intake.md", "is not listed in AGENTS.md"
        )
        self.assert_finding(
            findings, "error", "AGENTS.md", "does not list `.agents/rules/repo-tracker-intake.md`"
        )
        self.assertEqual(len(findings), 2, findings)
        files["AGENTS.md"] = root_text()
        self.assertEqual(check_repo(self.repo(files)), [])

    def test_extra_repo_rule_rejected(self):
        # `.agents/rules/` holds exactly the three always-on `repo-*` rules; a fourth would compete
        # for the context budget, so it must be hook-delivered or scoped to a node instead.
        files = with_rule(clean_files(), "repo-extra.md", repo_rule_text(), "repo")
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", ".agents/rules/repo-extra.md", "the three always-on"
        )
        self.assertEqual(len(findings), 1, findings)

    def test_missing_always_on_listing_rejected(self):
        files = clean_files()
        files["AGENTS.md"] = root_text().replace("`.agents/rules/repo-signing-keys.md`", "`BUILD.md`")
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", "AGENTS.md", "does not list `.agents/rules/repo-signing-keys.md`"
        )

    def test_listed_rule_file_missing_fails(self):
        files = clean_files()
        files["android/AGENTS.md"] = listed(files["android/AGENTS.md"], "android-ghost.md")
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".agents/rules/android-ghost.md", "does not exist")
        self.assertEqual(len(findings), 1, findings)

    def test_reference_must_match_whole_rule_path(self):
        # `.agents/rules/android-x.md.bak` is not a mention of `android-x.md`: it must not satisfy
        # the listing, and it must not be reported as a missing rule file either.
        files = clean_files()
        files[".agents/rules/android-x.md"] = node_rule_text("android/**")
        files["android/AGENTS.md"] = listed(files["android/AGENTS.md"], "android-x.md.bak")
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", ".agents/rules/android-x.md", "is not listed in android/AGENTS.md"
        )
        self.assertEqual(len(findings), 1, findings)

        files["android/AGENTS.md"] = listed(files["android/AGENTS.md"], "android-x.md")
        self.assertEqual(check_repo(self.repo(files)), [])

    def test_reference_prefix_does_not_fail_as_missing(self):
        files = clean_files()
        files["android/AGENTS.md"] = listed(files["android/AGENTS.md"], "android-ghost.md.bak")
        self.assertEqual(check_repo(self.repo(files)), [])

    def test_empty_topic_fails(self):
        files = clean_files()
        files[".agents/rules/android-.md"] = node_rule_text("android/**")
        files["android/AGENTS.md"] = listed(files["android/AGENTS.md"], "android-.md")
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".agents/rules/android-.md", "empty topic")
        self.assertEqual(len(findings), 1, findings)

    def test_oversized_rule_skips_content_parsing(self):
        # An always-on rule keeps its name: the oversize check must not become a layout finding too.
        for name, scope, cap in (("repo-no-time-based-tests.md", "repo", REPO_RULE_SIZE_MAX),
                                 ("android-huge.md", "android", RULE_SIZE_MAX)):
            with self.subTest(name=name):
                text = "not a frontmatter block\n" + "y" * cap + "\n"
                files = with_rule(clean_files(), name, text, scope)
                findings = check_repo(self.repo(files))
                self.assert_finding(findings, "error", ".agents/rules/" + name, "size cap")
                self.assertEqual(len(findings), 1, findings)

    # Hooked rules: `.agents/hooked-rules/` is parsed by the engine the fixture carries.

    def hooked_findings(self, text, name=HOOKED_NAME):
        files = clean_files()
        files[HOOKED_DIR + name] = text
        return check_repo(self.repo(files))

    def assert_single(self, findings, path, fragment):
        self.assert_finding(findings, "error", path, fragment)
        self.assertEqual(len(findings), 1, findings)

    def test_hooked_unknown_key(self):
        text = hooked_rule_text(lines=['sticky: "yes"', 'gates: ["\\\\bgit\\\\b"]'])
        self.assert_single(self.hooked_findings(text), HOOKED_PATH, "unsupported frontmatter key 'sticky'")

    def test_hooked_duplicate_key(self):
        text = hooked_rule_text(lines=['gates: ["a"]', 'gates: ["b"]'])
        self.assert_single(self.hooked_findings(text), HOOKED_PATH, "duplicate frontmatter key 'gates'")

    def test_hooked_empty_list(self):
        text = hooked_rule_text(lines=["gates: []", 'skills: ["opsx:apply"]'])
        self.assert_single(self.hooked_findings(text), HOOKED_PATH, "at least one item")

    def test_hooked_bad_regex(self):
        text = hooked_rule_text(lines=['gates: ["(unbalanced"]'])
        self.assert_single(self.hooked_findings(text), HOOKED_PATH, "invalid regex")

    def test_hooked_no_trigger(self):
        text = hooked_rule_text(lines=[])
        self.assert_single(self.hooked_findings(text), HOOKED_PATH, "at least one entry")

    def test_hooked_missing_pointer(self):
        text = hooked_rule_text(pointer=None, lines=['gates: ["\\\\bgit\\\\b"]'])
        self.assert_single(self.hooked_findings(text), HOOKED_PATH, "`pointer` is required")

    def test_unknown_stop_predicate_rejected(self):
        text = hooked_rule_text(lines=['stops: ["not-a-predicate"]'])
        files = clean_files()
        files[HOOKED_PATH] = text
        root = self.repo(files)
        findings = check_repo(root)
        # One finding, from the registry check: the generic invalid-rule finding stays away.
        self.assert_single(findings, HOOKED_PATH, "outside the engine registry")
        self.assertIn("superspec-finalize-pending", findings[0].message)
        self.assertIn("not-a-predicate", findings[0].message)
        self.assertEqual(check_stop_predicates(root), findings)

    def hooked_body(self, size):
        """The fixture rule with a body of exactly ``size`` UTF-8 bytes (a multi-byte character included)."""
        head = "é\n"  # two bytes plus a newline
        filler = "x" * 79 + "\n"
        padding = size - len(head.encode("utf-8"))
        body = head + filler * (padding // len(filler)) + "x" * (padding % len(filler))
        self.assertEqual(len(body.encode("utf-8")), size)
        return hooked_rule_text(body=body)

    def test_hooked_body_4096_ok(self):
        # The file is over the 4,096-byte `repo-*` cap of `.agents/rules/`; only the body counts.
        text = self.hooked_body(4096)
        self.assertGreater(len(text.encode("utf-8")), REPO_RULE_SIZE_MAX)
        self.assertEqual(self.hooked_findings(text), [])

    def test_hooked_body_4097(self):
        self.assert_single(self.hooked_findings(self.hooked_body(4097)), HOOKED_PATH, "4096 byte limit")

    def test_hooked_rule_not_judged_as_repo_rule(self):
        # The fixture rule has no `trigger`, is listed in no `## Rules` section and is still clean.
        root = self.repo()
        self.assertNotIn("trigger", clean_files()[HOOKED_PATH])
        self.assertNotIn(HOOKED_PATH, root_text().replace(HOOKED_PATH, ""))
        self.assertEqual(check_repo(root), [])
        self.assertEqual(check_hooked_rules(root, list_files(root)), [])
        self.assertEqual(check_pointer_block(root), [])

    def test_hooked_nested_dir(self):
        findings = self.hooked_findings(hooked_rule_text(), name="x/repo-nested.md")
        self.assert_single(findings, HOOKED_DIR + "x/repo-nested.md", "subdirectory")

    def test_hooked_bad_scope_name(self):
        for name, fragment in (
            ("commit-types.md", "no known scope"),
            ("repo-.md", "empty topic"),
            ("repo-notes.txt", "does not end with `.md`"),
        ):
            with self.subTest(name=name):
                self.assert_single(self.hooked_findings("x\n", name=name), HOOKED_DIR + name, fragment)

    def test_hooked_symlink_fails(self):
        files = clean_files()
        del files[HOOKED_PATH]
        files["docs/rule-source.md"] = hooked_rule_text()
        symlinks = default_symlinks()
        symlinks[HOOKED_PATH] = "../../docs/rule-source.md"
        findings = check_repo(self.repo(files, symlinks))
        self.assert_single(findings, HOOKED_PATH, "symlink")

    def test_hooked_not_utf8_fails(self):
        root = self.repo()
        with open(os.path.join(root, HOOKED_PATH), "wb") as handle:
            handle.write(b"---\npointer: \"\xff\"\ngates: [\"a\"]\n---\n")
        self.assert_single(check_repo(root), HOOKED_PATH, "UTF-8")

    def test_hooked_engine_missing_is_one_finding(self):
        files = clean_files()
        del files[ENGINE_FILE]
        self.assert_single(check_repo(self.repo(files)), ENGINE_FILE, "cannot be imported")

    def test_hooked_engine_unimportable_is_one_finding(self):
        for text in ("def broken(:\n", "raise SystemExit(3)\n", "RULES_DIR = 1\n"):
            with self.subTest(text=text):
                files = clean_files()
                files[ENGINE_FILE] = text
                self.assert_single(check_repo(self.repo(files)), ENGINE_FILE, "cannot be imported")

    def test_retained_block_with_missing_engine_reports_it_once(self):
        files = clean_files()
        del files[HOOKED_PATH]
        del files[ENGINE_FILE]
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ENGINE_FILE, "cannot be imported")
        self.assert_finding(findings, "error", HOOKED_PATH, "the moved rule must exist")
        self.assertEqual(len(findings), 2, findings)

    def test_lone_marker_with_missing_engine_reports_it_once(self):
        files = clean_files()
        del files[HOOKED_PATH]
        del files[ENGINE_FILE]
        files["AGENTS.md"] = root_text(block=False) + "\n<!-- hooked-rules:end -->\n"
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ENGINE_FILE, "cannot be imported")
        self.assert_finding(findings, "error", HOOKED_PATH, "the moved rule must exist")
        self.assertEqual(len(findings), 2, findings)

    def test_on_disk_hooked_file_with_missing_engine_reports_it_once(self):
        files = clean_files()
        del files[ENGINE_FILE]
        files["AGENTS.md"] = root_text(block=False)
        files[".gitignore"] = ".agents/hooked-rules/\n"
        root = self.repo(files)
        write_text(root, HOOKED_PATH, hooked_rule_text())
        findings = check_repo(root)
        self.assert_finding(findings, "error", ENGINE_FILE, "cannot be imported")
        for name in HOOKED_TARGETS:
            self.assert_finding(findings, "error", HOOKED_DIR + name, "the moved rule must exist")
        self.assertEqual(len(findings), len(HOOKED_TARGETS) + 1, findings)

    def test_retained_block_with_a_vanished_rule_is_stale(self):
        files = clean_files()
        del files[HOOKED_PATH]
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", "AGENTS.md", "stale")
        self.assert_finding(findings, "error", HOOKED_PATH, "the moved rule must exist")
        self.assertEqual(len(findings), 2, findings)

    def test_ignored_hooked_file_rejected_by_loader_is_one_finding(self):
        files = clean_files()
        files[".gitignore"] = ".agents/hooked-rules/junk.md\n"
        root = self.repo(files)
        write_text(root, HOOKED_DIR + "junk.md", "not a rule\n")
        self.assertNotIn(HOOKED_DIR + "junk.md", list_files(root))
        self.assert_single(check_repo(root), HOOKED_DIR + "junk.md", "frontmatter")

    def test_loader_error_not_duplicated_for_a_listed_file(self):
        # The per-file check already reports the tracked bad rule; the loader's error is not repeated.
        findings = self.hooked_findings(hooked_rule_text(lines=['gates: ["("]']))
        self.assert_single(findings, HOOKED_PATH, "invalid regex")

    def test_marker_found_in_a_damaged_root_agents_md(self):
        # A damaged byte elsewhere in the root cannot hide a retained marker: the hooked-rule check
        # still needs the engine and reports it, next to the vanished rule's missing target.
        files = clean_files()
        del files[HOOKED_PATH]
        del files[ENGINE_FILE]
        files["AGENTS.md"] = root_text(block=False) + "\n<!-- hooked-rules:begin -->\n"
        root = self.repo(files)
        with open(os.path.join(root, "AGENTS.md"), "ab") as handle:
            handle.write(b"A damaged byte: \xff\n")
        findings = check_repo(root)
        self.assert_finding(findings, "error", ENGINE_FILE, "cannot be imported")
        self.assert_finding(findings, "error", HOOKED_PATH, "the moved rule must exist")
        self.assertEqual(len(findings), 2, findings)

    def test_no_pointer_block_with_missing_engine_is_still_reported(self):
        # Six required hooked rules need the engine even with no pointer block, so the configured
        # hooks never run an absent parser.
        files = clean_files()
        del files[ENGINE_FILE]
        files["AGENTS.md"] = root_text(block=False)
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ENGINE_FILE, "cannot be imported")
        self.assertEqual(len(findings), 1, findings)

    def test_pointer_block_stale(self):
        files = clean_files()
        files[HOOKED_PATH] = hooked_rule_text(pointer="A changed pointer.")
        self.assert_single(check_repo(self.repo(files)), "AGENTS.md", "stale")
        texts = fixture_hooked_rules()
        texts[HOOKED_NAME] = files[HOOKED_PATH]
        files["AGENTS.md"] = root_text(block=pointer_block(texts))
        self.assertEqual(check_repo(self.repo(files)), [])

    def test_pointer_block_stale_for_a_new_rule(self):
        files = clean_files()
        files[HOOKED_DIR + "docs-extra.md"] = hooked_rule_text(pointer="Extra.")
        self.assert_single(check_repo(self.repo(files)), "AGENTS.md", "stale")

    def test_pointer_block_crlf_passes(self):
        files = clean_files()
        files["AGENTS.md"] = root_text().replace("\n", "\r\n")
        self.assertEqual(check_repo(self.repo(files)), [])

    def test_pointer_block_missing(self):
        cases = (
            ("no block", root_text(block=False), "no hooked-rules pointer block"),
            ("no heading", root_text(heading=False), "Hook-delivered rules"),
            (
                "blank line after the heading",
                root_text().replace("## Hook-delivered rules\n", "## Hook-delivered rules\n\n"),
                "Hook-delivered rules",
            ),
            ("no end marker", root_text().replace(ENGINE.POINTER_END + "\n", ""), "begin line"),
            (
                "second begin marker",
                root_text() + ENGINE.POINTER_BEGIN + "\n",
                "begin line",
            ),
        )
        for case, text, fragment in cases:
            with self.subTest(case=case):
                files = clean_files()
                files["AGENTS.md"] = text
                self.assert_single(check_repo(self.repo(files)), "AGENTS.md", fragment)

    def test_old_rule_present(self):
        files = clean_files()
        files[MOVED_RULE] = repo_rule_text()
        self.assert_single(
            check_repo(self.repo(files)), MOVED_RULE, ".agents/hooked-rules/repo-commit-types.md"
        )

    def test_old_rule_listed(self):
        files = clean_files()
        files["AGENTS.md"] = listed(files["AGENTS.md"], "repo-conventional-commits.md")
        self.assert_single(
            check_repo(self.repo(files)), "AGENTS.md", ".agents/hooked-rules/repo-commit-types.md"
        )

    def test_old_rule_listed_by_a_node(self):
        files = clean_files()
        files["scripts/AGENTS.md"] = listed(files["scripts/AGENTS.md"], "repo-conventional-commits.md")
        self.assert_single(check_repo(self.repo(files)), "scripts/AGENTS.md", "now lives at")

    def test_old_rule_present_and_listed(self):
        files = with_rule(clean_files(), "repo-conventional-commits.md", repo_rule_text(), "repo")
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", MOVED_RULE, "now lives at")
        self.assert_finding(findings, "error", "AGENTS.md", "now lives at")
        self.assertEqual(len(findings), 2, findings)

    def test_moved_rule_left_behind(self):
        for name, target in sorted(EXPECTED_MOVED_RULES.items()):
            with self.subTest(name=name):
                files = clean_files()
                files[".agents/rules/" + name] = repo_rule_text()
                findings = check_repo(self.repo(files))
                self.assert_finding(
                    findings,
                    "error",
                    ".agents/rules/" + name,
                    ".agents/hooked-rules/" + target,
                )
                self.assertEqual(len(findings), 1, findings)

    def test_moved_rule_target_missing(self):
        # A moved rule has one home: its `.agents/hooked-rules/` target. Deleting it must fail even
        # when the pointer block is regenerated over the remaining rules, as it then matches again
        # and no listing or pointer check would notice the vanished rule.
        for name, target in sorted(EXPECTED_MOVED_RULES.items()):
            with self.subTest(name=name):
                files = clean_files()
                del files[HOOKED_DIR + target]
                texts = fixture_hooked_rules()
                del texts[target]
                files["AGENTS.md"] = root_text(block=pointer_block(texts))
                findings = check_repo(self.repo(files))
                self.assert_finding(
                    findings, "error", HOOKED_DIR + target, "the moved rule must exist"
                )
                self.assertEqual(len(findings), 1, findings)

    def test_root_lists_the_three_always_on_repo_rules(self):
        text = root_text()
        for name in EXPECTED_REPO_RULES:
            self.assertIn(".agents/rules/" + name, text)
        self.assertEqual(len(EXPECTED_REPO_RULES), 3)

    def test_check_functions_accept_the_real_tree_shape(self):
        root = self.repo()
        files = list_files(root)
        self.assertEqual(check_links(root, files, "linux"), [])
        self.assertEqual(check_opencode(root), [])
        self.assertEqual(check_codeowners(root), [])
        self.assertEqual(check_claude_hooks(root), [])
        self.assertEqual(check_plugin_mentions(root), [])
        self.assertEqual(check_hooked_rules(root, files), [])
        self.assertEqual(check_pointer_block(root), [])
        self.assertEqual(check_stop_predicates(root), [])
        self.assertEqual(check_budget(root), [])

    def budget_root(self, sizes, **kwargs):
        """A fixture repository whose only budget-triggered rules are ``sizes`` bytes each."""
        files = clean_files()
        for index, size in enumerate(sizes):
            files[HOOKED_DIR + "repo-budget-%d.md" % index] = budget_rule_text(
                size=size, **kwargs
            )
        return self.repo(files)

    def test_budget_at_cap_passes(self):
        # 2,998 + 3,000 + 2,998 bytes and two separators are exactly the 9,000-byte cap.
        root = self.budget_root([2998, 3000, 2998], skills=[BUDGET_SKILL])
        self.assertEqual(check_budget(root), [])
        # The fixture really is at the cap: the engine's own texts total exactly 9,000 bytes.
        rules, errors = ENGINE.load_rules(root)
        self.assertEqual(errors, [])
        texts = [ENGINE.rule_text(rule.body) for rule in rules if BUDGET_SKILL in rule.skills]
        self.assertEqual(len(texts), 3)
        self.assertEqual(len(ENGINE.RULE_SEPARATOR.join(texts).encode("utf-8")), 9000)

    def test_budget_over_cap_fails(self):
        # One byte more than the cap fails, and the message names the trigger and its rules.
        root = self.budget_root([2998, 3000, 2999], skills=[BUDGET_SKILL])
        findings = check_budget(root)
        self.assertEqual(len(findings), 1, findings)
        self.assert_finding(findings, "error", HOOKED_DIR.rstrip("/"), "skill:" + BUDGET_SKILL)
        self.assert_finding(findings, "error", HOOKED_DIR.rstrip("/"), "9,001 bytes")
        self.assert_finding(findings, "error", HOOKED_DIR.rstrip("/"), "9,000-byte delivery budget")
        self.assert_finding(findings, "error", HOOKED_DIR.rstrip("/"), "repo-budget-2.md")

    def test_stop_text_counted(self):
        # Two 4,000-byte rules naming the predicate and the 342-byte stop line fit. Three rules of
        # about 2,900 bytes are 8,705 bytes with separators, under the cap, so only the stop line
        # (which carries a 64-character change name) can push that trigger over it.
        fits = self.budget_root([4000, 4000], stops=[EXPECTED_STOP_PREDICATES[0]])
        self.assertEqual(check_budget(fits), [])
        over = self.budget_root([2900, 2900, 2901], stops=[EXPECTED_STOP_PREDICATES[0]])
        findings = check_budget(over)
        self.assertEqual(len(findings), 1, findings)
        self.assert_finding(
            findings, "error", HOOKED_DIR.rstrip("/"), "stop:" + EXPECTED_STOP_PREDICATES[0]
        )
        self.assert_finding(findings, "error", HOOKED_DIR.rstrip("/"), "repo-budget-2.md")

    def test_documented_skill_section_parses(self):
        text = "# x\n\n## OpenSpec skills\n\n- `alpha`\n- `beta`\n\n## Next\n\n- `gamma`\n"
        self.assertEqual(parse_documented_skills(text), ("alpha", "beta"))
        self.assertEqual(
            parse_documented_skills("\n".join(skill_section(EXPECTED_OPENSPEC_SKILLS))),
            EXPECTED_OPENSPEC_SKILLS,
        )
        self.assertEqual(parse_documented_skills(""), ())

    def test_missing_rules_symlink_fails(self):
        symlinks = default_symlinks()
        del symlinks[".claude/rules"]
        findings = check_repo(self.repo(symlinks=symlinks))
        self.assert_finding(findings, "error", ".claude/rules", "is missing")
        self.assertEqual(len(findings), 1, findings)

    def test_rules_regular_file_fails_on_linux_warns_on_windows(self):
        # The index still records mode 120000; only the worktree is a regular-file stand-in,
        # which is what a Windows checkout without core.symlinks/Developer Mode produces.
        root = self.repo()
        os.unlink(os.path.join(root, ".claude/rules"))
        write_text(root, ".claude/rules", "../.agents/rules")
        findings = check_repo(root, platform="linux")
        self.assert_finding(findings, "error", ".claude/rules", "is not a symlink")
        self.assertEqual(len(findings), 1, findings)

        findings = check_repo(root, platform="win32")
        self.assert_finding(findings, "warning", ".claude/rules", "is not a symlink")
        self.assert_finding(findings, "warning", ".claude/rules", "core.symlinks=true")
        self.assert_finding(findings, "warning", ".claude/rules", "Developer Mode")
        self.assertEqual(len(findings), 1, findings)

    def test_wrong_symlink_target_fails(self):
        symlinks = default_symlinks()
        symlinks[".claude/rules"] = "../.agents/rulez"
        findings = check_repo(self.repo(symlinks=symlinks))
        self.assert_finding(findings, "error", ".claude/rules", "points to")
        self.assertEqual(len(findings), 1, findings)

    def test_skill_without_link_fails(self):
        files = clean_files()
        files[".agents/skills/local-skill/SKILL.md"] = "# local\n"
        findings = check_repo(self.repo(files, default_symlinks()))
        self.assert_finding(findings, "error", ".claude/skills/local-skill", "is missing")
        self.assertEqual(len(findings), 1, findings)

    def test_untracked_skill_with_link_is_not_a_false_positive(self):
        root = self.repo()
        write_text(root, ".agents/skills/local-skill/SKILL.md", "# local\n")
        os.symlink(
            "../../.agents/skills/local-skill", os.path.join(root, ".claude/skills/local-skill")
        )
        self.assertEqual(check_repo(root), [])

    def test_orphan_claude_skill_link_fails(self):
        symlinks = default_symlinks()
        symlinks[".claude/skills/openspec-ghost"] = "../../.agents/skills/openspec-ghost"
        findings = check_repo(self.repo(symlinks=symlinks))
        self.assert_finding(findings, "error", ".claude/skills/openspec-ghost", "orphan")
        self.assertEqual(len(findings), 1, findings)

    def test_tracked_ghost_skill_directory_fails(self):
        # A committed real directory has no index entry of its own; only its descendants expose
        # the name, so the reverse parity check must derive names from paths under the directory.
        files = clean_files()
        files[".claude/skills/openspec-ghost/SKILL.md"] = "# ghost\n"
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".claude/skills/openspec-ghost", "orphan")
        self.assertEqual(len(findings), 1, findings)

    def test_untracked_ghost_skill_directory_fails(self):
        root = self.repo()
        write_text(root, ".claude/skills/openspec-ghost/SKILL.md", "# ghost\n")
        findings = check_repo(root)
        self.assert_finding(findings, "error", ".claude/skills/openspec-ghost", "orphan")
        self.assertEqual(len(findings), 1, findings)

    def test_regular_file_replacing_skill_link_fails(self):
        files, symlinks = clean_files(), default_symlinks()
        del symlinks[".claude/skills/openspec-apply-change"]
        files[".claude/skills/openspec-apply-change"] = "# not a link\n"
        findings = check_repo(self.repo(files, symlinks))
        self.assert_finding(
            findings, "error", ".claude/skills/openspec-apply-change", "regular file"
        )
        self.assertEqual(len(findings), 1, findings)

    def test_wrong_skill_link_target_fails(self):
        symlinks = default_symlinks()
        symlinks[".claude/skills/openspec-apply-change"] = (
            "../../.agents/skills/openspec-archive-change"
        )
        findings = check_repo(self.repo(symlinks=symlinks))
        self.assert_finding(findings, "error", ".claude/skills/openspec-apply-change", "points to")
        self.assertEqual(len(findings), 1, findings)

    def test_skill_set_must_equal_documented_list(self):
        extra = "openspec-extra-change"
        files, symlinks = clean_files(), default_symlinks()
        files[".agents/skills/%s/SKILL.md" % extra] = "# extra\n"
        symlinks[".claude/skills/" + extra] = "../../.agents/skills/" + extra
        findings = check_repo(self.repo(files, symlinks))
        self.assert_finding(
            findings, "error", ".agents/skills/" + extra, "not listed under `## OpenSpec skills`"
        )
        self.assertEqual(len(findings), 1, findings)

        # Adding the skill and its doc bullet together passes.
        skills = list(EXPECTED_OPENSPEC_SKILLS) + [extra]
        self.assertEqual(check_repo(self.repo(clean_files(skills), default_symlinks(skills))), [])

        # A skill that still exists but was dropped from the doc fails in the other direction.
        dropped = "openspec-verify-change"
        files, symlinks = clean_files(), default_symlinks()
        del files[".agents/skills/%s/SKILL.md" % dropped]
        del symlinks[".claude/skills/" + dropped]
        findings = check_repo(self.repo(files, symlinks))
        self.assert_finding(
            findings,
            "error",
            ".agents/skills/" + dropped,
            "is listed under `## OpenSpec skills` in docs/openspec-skills.md but is missing",
        )
        self.assertEqual(len(findings), 1, findings)

        # Updating the doc together with the skill set passes.
        skills = [name for name in EXPECTED_OPENSPEC_SKILLS if name != dropped]
        self.assertEqual(check_repo(self.repo(clean_files(skills), default_symlinks(skills))), [])

    def test_missing_documented_skill_list_fails(self):
        files = clean_files()
        del files["docs/openspec-skills.md"]
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", "docs/openspec-skills.md", "expected OpenSpec skill list"
        )
        self.assertEqual(len(findings), 1, findings)

    def test_settings_json_symlink_fails(self):
        files = clean_files()
        del files[".claude/settings.json"]
        symlinks = default_symlinks()
        symlinks[".claude/settings.json"] = "settings-target.json"
        findings = check_repo(self.repo(files, symlinks))
        self.assert_finding(findings, "error", ".claude/settings.json", "must not be a symlink")
        self.assertEqual(len(findings), 1, findings)

    def test_settings_worktree_symlink_fails(self):
        # The index still records a regular file (100644); only the unstaged worktree entry is
        # a symlink, so the index-only link test would miss it.
        root = self.repo()
        os.unlink(os.path.join(root, ".claude/settings.json"))
        os.symlink("settings-target.json", os.path.join(root, ".claude/settings.json"))
        findings = check_repo(root)
        self.assert_finding(findings, "error", ".claude/settings.json", "must not be a symlink")
        self.assertEqual(len(findings), 1, findings)

    def test_openspec_target_marker_excluded(self):
        root = self.repo()
        self.assertEqual(check_repo(root), [])
        self.assertIn(".agents/skills/.openspec-target", list_files(root))
        self.assertFalse(any(".openspec-target" in found.path for found in check_repo(root)))
        files = clean_files()
        del files[".agents/skills/.openspec-target"]
        self.assertEqual(check_repo(self.repo(files)), [])

    def test_dot_claude_skill_marker_directory_fails(self):
        # Only the canonical `.agents/skills/.openspec-target` marker is exempt; the same name
        # under `.claude/skills/` is a tool-specific skill directory and must be rejected.
        files = clean_files()
        files[".claude/skills/.openspec-target/SKILL.md"] = "# marker\n"
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".claude/skills/.openspec-target", "orphan")
        self.assertEqual(
            [finding for finding in findings if finding.path == ".agents/skills/.openspec-target"],
            [],
            findings,
        )
        self.assertEqual(len(findings), 1, findings)

    def test_override_absent(self):
        overrides = default_overrides()
        del overrides["scripts/verify-commands.json"]
        files = clean_files()
        files[".github/components.json"] = components_json(overrides)
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings,
            "error",
            "scripts/verify-commands.json",
            "has no entry in .github/components.json global-overrides",
        )
        self.assertEqual(len(findings), 1, findings)

    def test_override_wrong_owners(self):
        overrides = default_overrides()
        overrides["scripts/verify-commands.json"] = ["docs"]
        files = clean_files()
        files[".github/components.json"] = components_json(overrides)
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", "scripts/verify-commands.json", "expected exactly"
        )
        self.assertEqual(len(findings), 1, findings)

    def test_override_target_sets_exact(self):
        cases = (
            ("native-bridge/host/AGENTS.md", ["docs"], "native-bridge/host/AGENTS.md", "native-host"),
            ("android/AGENTS.md", ["android"], "android/AGENTS.md", "expected exactly"),
            (
                ".github/workflows/check-docs.yml",
                ["docs"],
                ".github/workflows/check-docs.yml",
                "tooling",
            ),
            (".github/components.json", ["tooling"], ".github/components.json", "expected exactly"),
        )
        for key, target, path, fragment in cases:
            with self.subTest(key=key):
                overrides = default_overrides()
                overrides[key] = target
                files = clean_files()
                files[".github/components.json"] = components_json(overrides)
                findings = check_repo(self.repo(files))
                self.assert_finding(findings, "error", path, "global-overrides")
                self.assert_finding(findings, "error", path, fragment)
                self.assertEqual(len(findings), 1, findings)

    def test_codeowners_missing_entry_fails(self):
        for pattern in (
            "/.claude/",
            "**/AGENTS.md",
            "/.agents/",
            "/docs/superspec-workflow.md",
            "/docs/agent-guidance.md",
            "/docs/openspec-workflow.md",
            "/docs/opencode-rules.md",
            "/openspec/config.yaml",
            "/openspec/schemas/",
            "/scripts/agent_rules.py",
            "/scripts/opencode-hooked-rules/",
        ):
            with self.subTest(pattern=pattern):
                patterns = [item for item in EXPECTED_CODEOWNER_PATTERNS if item != pattern]
                files = clean_files()
                files[".github/CODEOWNERS"] = codeowners_text(patterns)
                findings = check_repo(self.repo(files))
                self.assert_finding(findings, "error", ".github/CODEOWNERS", pattern)
                self.assertEqual(len(findings), 1, findings)

        # `/.mcp.json` is required only when that file exists.
        without_mcp = [item for item in EXPECTED_CODEOWNER_PATTERNS if item != "/.mcp.json"]
        files = clean_files()
        files[".github/CODEOWNERS"] = codeowners_text(without_mcp)
        self.assertEqual(check_repo(self.repo(files)), [])
        files[".mcp.json"] = "{}\n"
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".github/CODEOWNERS", "/.mcp.json")
        self.assertEqual(len(findings), 1, findings)

        files = clean_files()
        del files[".github/CODEOWNERS"]
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".github/CODEOWNERS", "is missing")
        self.assertEqual(len(findings), 1, findings)

    def test_codeowners_pattern_without_owner_fails(self):
        # A bare pattern matches files but assigns no reviewer, so it is not coverage.
        patterns = [item for item in EXPECTED_CODEOWNER_PATTERNS if item != "/.agents/"]
        files = clean_files()
        files[".github/CODEOWNERS"] = codeowners_text(patterns) + "/.agents/\n"
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", ".github/CODEOWNERS", "has no owner on the `/.agents/` pattern"
        )
        self.assertEqual(len(findings), 1, findings)

    def test_codeowners_later_ownerless_rule_removes_ownership(self):
        # GitHub uses the last matching rule, so a later bare line overrides an earlier owner.
        files = clean_files()
        files[".github/CODEOWNERS"] = codeowners_text() + "/opencode.json\n"
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".github/CODEOWNERS", "`/opencode.json` pattern")
        self.assert_finding(findings, "error", ".github/CODEOWNERS", "last matching rule")
        self.assertEqual(len(findings), 1, findings)

    def test_codeowners_ownerless_wildcard_removes_all_ownership(self):
        files = clean_files()
        files[".github/CODEOWNERS"] = codeowners_text() + "*\n"
        findings = check_repo(self.repo(files))
        # One finding per sample path, not per required pattern: the clean fixture has no
        # `.mcp.json`, so that pattern's sample is never added.
        self.assertEqual(len(findings), len(CODEOWNER_SAMPLE_PATHS), findings)
        self.assertTrue(all(finding.path == ".github/CODEOWNERS" for finding in findings), findings)
        self.assertTrue(
            any("last matching rule `*`" in finding.message for finding in findings), findings
        )
        for sample in (
            "docs/superspec-workflow.md",
            "openspec/config.yaml",
            "openspec/schemas/superspec/schema.yaml",
        ):
            self.assert_finding(findings, "error", ".github/CODEOWNERS", sample)

    def test_codeowners_broader_line_does_not_count(self):
        # A broader `/openspec/` line covers the files but is not the required literal pattern.
        patterns = [item for item in EXPECTED_CODEOWNER_PATTERNS if item != "/openspec/schemas/"]
        files = clean_files()
        files[".github/CODEOWNERS"] = codeowners_text(patterns) + "/openspec/ @raidenyn\n"
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".github/CODEOWNERS", "/openspec/schemas/")
        self.assertEqual(len(findings), 1, findings)

    def test_codeowners_restating_line_with_owner_passes(self):
        files = clean_files()
        files[".github/CODEOWNERS"] = codeowners_text() + "/opencode.json @raidenyn\n"
        self.assertEqual(check_repo(self.repo(files)), [])

    def test_codeowners_ownerless_directory_rule_without_slash_fails(self):
        # GitHub treats a directory pattern without a trailing slash as covering its contents.
        cases = (
            ("/.claude", ".claude/settings.json"),
            ("/.agents", ".agents/hooked-rules/repo-commit-types.md"),
            ("/android", "android/AGENTS.md"),
        )
        for pattern, sample in cases:
            with self.subTest(pattern=pattern, sample=sample):
                files = clean_files()
                files[".github/CODEOWNERS"] = codeowners_text() + pattern + "\n"
                findings = check_repo(self.repo(files))
                self.assert_finding(
                    findings,
                    "error",
                    ".github/CODEOWNERS",
                    "last matching rule `%s`" % pattern,
                )
                self.assertEqual(len(findings), 1, findings)

    def test_codeowners_owned_directory_rule_without_slash_passes(self):
        for pattern in ("/.claude", "/.agents", "/android"):
            with self.subTest(pattern=pattern):
                files = clean_files()
                files[".github/CODEOWNERS"] = codeowners_text() + pattern + " @raidenyn\n"
                self.assertEqual(check_repo(self.repo(files)), [])

    def test_codeowners_engine_missing(self):
        patterns = [item for item in EXPECTED_CODEOWNER_PATTERNS if item != "/scripts/agent_rules.py"]
        files = clean_files()
        files[".github/CODEOWNERS"] = codeowners_text(patterns)
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", ".github/CODEOWNERS", "is missing the `/scripts/agent_rules.py`"
        )
        self.assertEqual(len(findings), 1, findings)

    def test_codeowners_plugin_missing(self):
        patterns = [
            item for item in EXPECTED_CODEOWNER_PATTERNS if item != "/scripts/opencode-hooked-rules/"
        ]
        files = clean_files()
        files[".github/CODEOWNERS"] = codeowners_text(patterns)
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings,
            "error",
            ".github/CODEOWNERS",
            "is missing the `/scripts/opencode-hooked-rules/`",
        )
        self.assertEqual(len(findings), 1, findings)

    def test_codeowners_engine_and_plugin_without_owner_fail(self):
        cases = (
            ("/scripts/agent_rules.py", "scripts/agent_rules.py"),
            ("/scripts/opencode-hooked-rules/", "scripts/opencode-hooked-rules/index.js"),
        )
        for pattern, sample in cases:
            with self.subTest(pattern=pattern):
                patterns = [item for item in EXPECTED_CODEOWNER_PATTERNS if item != pattern]
                files = clean_files()
                files[".github/CODEOWNERS"] = codeowners_text(patterns) + pattern + "\n"
                findings = check_repo(self.repo(files))
                self.assert_finding(
                    findings,
                    "error",
                    ".github/CODEOWNERS",
                    "has no owner on the `%s` pattern for `%s`" % (pattern, sample),
                )
                self.assertEqual(len(findings), 1, findings)

    def test_codeowners_engine_and_plugin_shadowed_fail(self):
        # A later ownerless rule shadows the earlier owned entry for the engine and the plugin.
        for pattern in ("/scripts/agent_rules.py", "/scripts/opencode-hooked-rules/"):
            with self.subTest(pattern=pattern):
                files = clean_files()
                files[".github/CODEOWNERS"] = codeowners_text() + pattern + "\n"
                findings = check_repo(self.repo(files))
                self.assert_finding(
                    findings, "error", ".github/CODEOWNERS", "`%s` pattern" % pattern
                )
                self.assert_finding(findings, "error", ".github/CODEOWNERS", "last matching rule")
                self.assertEqual(len(findings), 1, findings)

    def test_hooks_clean(self):
        self.assertEqual(check_claude_hooks(self.repo()), [])
        self.assertEqual(check_repo(self.repo()), [])

    def test_stop_hook_required(self):
        files = clean_files()
        files[".claude/settings.json"] = settings_text(hooks_block(drop="Stop"))
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".claude/settings.json", "hook Stop is missing")
        self.assertEqual(len(findings), 1, findings)

    def test_stop_holds_verify_hook(self):
        # The otherwise valid fixture passes, and the two Stop commands are the fixed pair.
        files = clean_files()
        stop = json.loads(files[".claude/settings.json"])["hooks"]["Stop"]
        self.assertEqual(
            [command["command"] for command in stop[0]["hooks"]], [HOOK_COMMAND, VERIFY_COMMAND]
        )
        self.assertEqual(
            [command["timeout"] for command in stop[0]["hooks"]], [5, VERIFY_TIMEOUT]
        )
        self.assertEqual(check_claude_hooks(self.repo(files)), [])
        self.assertEqual(check_repo(self.repo(files)), [])

    def test_stop_without_verify(self):
        block = hooks_block()
        block["Stop"][0]["hooks"].pop()
        files = clean_files()
        files[".claude/settings.json"] = settings_text(block)
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings,
            "error",
            ".claude/settings.json",
            "hook Stop must hold exactly two commands",
        )
        self.assertEqual(len(findings), 1, findings)

    def test_stop_verify_first(self):
        block = hooks_block()
        block["Stop"][0]["hooks"].reverse()
        files = clean_files()
        files[".claude/settings.json"] = settings_text(block)
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".claude/settings.json", "hook Stop has command")
        self.assert_finding(
            findings, "error", ".claude/settings.json", "expected %r" % VERIFY_COMMAND
        )
        self.assertEqual(len(findings), 4, findings)

    def test_stop_third_command(self):
        block = hooks_block()
        block["Stop"][0]["hooks"].append({"type": "command", "command": "true", "timeout": 5})
        files = clean_files()
        files[".claude/settings.json"] = settings_text(block)
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings,
            "error",
            ".claude/settings.json",
            "hook Stop must hold exactly two commands",
        )
        self.assertEqual(len(findings), 1, findings)

    def test_stop_wrong_timeout(self):
        block = hooks_block()
        block["Stop"][0]["hooks"][1]["timeout"] = 5
        files = clean_files()
        files[".claude/settings.json"] = settings_text(block)
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".claude/settings.json", "hook Stop has verify command timeout")
        self.assert_finding(findings, "error", ".claude/settings.json", "expected 1800")
        self.assertEqual(len(findings), 1, findings)

    def test_other_event_second_command(self):
        for event, _ in EXPECTED_HOOKS:
            if event == "Stop":
                continue
            with self.subTest(event=event):
                block = hooks_block()
                block[event][0]["hooks"].append(
                    {"type": "command", "command": "true", "timeout": 5}
                )
                files = clean_files()
                files[".claude/settings.json"] = settings_text(block)
                findings = check_repo(self.repo(files))
                self.assert_finding(
                    findings,
                    "error",
                    ".claude/settings.json",
                    "hook %s must hold exactly one command" % event,
                )
                self.assertEqual(len(findings), 1, findings)

    def test_subagent_stop_rejected(self):
        # A subagent stop is not a delivery moment; wiring it would run the stop gate twice.
        block = hooks_block()
        block["SubagentStop"] = [
            {
                "hooks": [
                    {"type": "command", "command": HOOK_COMMAND, "timeout": 5}
                ]
            }
        ]
        files = clean_files()
        files[".claude/settings.json"] = settings_text(block)
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".claude/settings.json", "SubagentStop")
        self.assertEqual(len(findings), 1, findings)

    def test_hooks_settings_unreadable_or_without_hooks_fail(self):
        files = clean_files()
        del files[".claude/settings.json"]
        findings = check_claude_hooks(self.repo(files))
        self.assert_finding(findings, "error", ".claude/settings.json", "is missing")
        self.assertEqual(len(findings), 1, findings)
        for name, text, fragment in (
            ("invalid", "{not json", "is not valid JSON"),
            ("array", "[]\n", "must be a JSON object"),
            ("no hooks", json.dumps({"enabledPlugins": {}}) + "\n", "has no `hooks` block"),
            ("hooks list", json.dumps({"hooks": []}) + "\n", "has no `hooks` block"),
        ):
            with self.subTest(name=name):
                files = clean_files()
                files[".claude/settings.json"] = text
                findings = check_repo(self.repo(files))
                self.assert_finding(findings, "error", ".claude/settings.json", fragment)
                self.assertEqual(len(findings), 1, findings)

    def test_hooks_extra_entry_fails(self):
        for event, matcher in EXPECTED_HOOKS:
            with self.subTest(event=event, kind="second entry"):
                block = hooks_block()
                extra = {"hooks": [{"type": "command", "command": "true", "timeout": 5}]}
                block[event].append(extra)
                files = clean_files()
                files[".claude/settings.json"] = settings_text(block)
                findings = check_repo(self.repo(files))
                self.assert_finding(findings, "error", ".claude/settings.json", "hook %s " % event)
                self.assert_finding(findings, "error", ".claude/settings.json", "exactly one")
                self.assertEqual(len(findings), 1, findings)
            with self.subTest(event=event, kind="second command"):
                block = hooks_block()
                block[event][0]["hooks"].append({"type": "command", "command": "true", "timeout": 5})
                files = clean_files()
                files[".claude/settings.json"] = settings_text(block)
                findings = check_repo(self.repo(files))
                self.assert_finding(findings, "error", ".claude/settings.json", "hook %s " % event)
                self.assert_finding(
                    findings,
                    "error",
                    ".claude/settings.json",
                    "exactly two" if event == "Stop" else "exactly one",
                )
                self.assertEqual(len(findings), 1, findings)

    def test_hooks_malformed_entry_fails(self):
        for name, value in (("string", "x"), ("empty list", []), ("entry not object", ["x"])):
            with self.subTest(name=name):
                block = hooks_block()
                block["PreToolUse"] = value
                files = clean_files()
                files[".claude/settings.json"] = settings_text(block)
                findings = check_repo(self.repo(files))
                self.assert_finding(findings, "error", ".claude/settings.json", "hook PreToolUse")
                self.assertEqual(len(findings), 1, findings)
        block = hooks_block()
        block["PreToolUse"][0]["hooks"][0]["type"] = "prompt"
        files = clean_files()
        files[".claude/settings.json"] = settings_text(block)
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", ".claude/settings.json", "hook PreToolUse has type")
        self.assertEqual(len(findings), 1, findings)

    def test_hooks_timeout_must_be_the_integer_five(self):
        for name, value in (("string", "5"), ("float", 5.0), ("bool", True), ("other", 6)):
            with self.subTest(name=name):
                files = clean_files()
                files[".claude/settings.json"] = settings_text(
                    hooks_block(timeouts={"PreCompact": value})
                )
                findings = check_repo(self.repo(files))
                self.assert_finding(
                    findings, "error", ".claude/settings.json", "hook PreCompact has timeout"
                )
                self.assertEqual(len(findings), 1, findings)

    def _hooks_findings_for_bytes(self, data):
        root = self.repo()
        with open(os.path.join(root, ".claude/settings.json"), "wb") as handle:
            handle.write(data)
        return check_repo(root)

    def test_hooks_settings_invalid_utf8_fails(self):
        valid = settings_text().encode("utf-8")
        findings = self._hooks_findings_for_bytes(valid.replace(b'"enabledPlugins"', b'"enabled\xffPlugins"'))
        self.assert_finding(findings, "error", ".claude/settings.json", "is not valid UTF-8")
        self.assertEqual(len(findings), 1, findings)

    def test_hooks_settings_non_json_constant_fails(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                text = settings_text(extra={"limit": 0}).replace('"limit": 0', '"limit": ' + constant)
                findings = self._hooks_findings_for_bytes(text.encode("utf-8"))
                self.assert_finding(findings, "error", ".claude/settings.json", "non-JSON constant")
                self.assertEqual(len(findings), 1, findings)

    def test_hooks_settings_duplicate_key_fails(self):
        text = settings_text()
        # A second `PreToolUse` member would silently replace the first (last wins).
        duplicate = text.replace('"PreToolUse": [', '"PreToolUse": [], "PreToolUse": [', 1)
        findings = self._hooks_findings_for_bytes(duplicate.encode("utf-8"))
        self.assert_finding(findings, "error", ".claude/settings.json", "duplicate key `PreToolUse`")
        self.assertEqual(len(findings), 1, findings)
        nested = text.replace('"type": "command",', '"type": "command", "type": "command",', 1)
        findings = self._hooks_findings_for_bytes(nested.encode("utf-8"))
        self.assert_finding(findings, "error", ".claude/settings.json", "duplicate key `type`")
        self.assertEqual(len(findings), 1, findings)

    def test_hooks_settings_duplicate_surrogate_key_is_ascii_and_printable(self):
        # `"\ud800"` is ASCII on disk and passes UTF-8 decoding, but decodes to a lone surrogate
        # that a strict stdout cannot encode, so the finding must carry an escaped form.
        text = settings_text().replace(
            '"enabledPlugins": {}', '"enabledPlugins": {}, "\\ud800": 1, "\\ud800": 2', 1
        )
        self.assertIn('"\\ud800": 1, "\\ud800": 2', text)
        root = self.repo()
        with open(os.path.join(root, ".claude/settings.json"), "wb") as handle:
            handle.write(text.encode("utf-8"))
        findings = check_repo(root)
        self.assert_finding(findings, "error", ".claude/settings.json", "duplicate key")
        self.assertEqual(len(findings), 1, findings)
        message = findings[0].message
        self.assertIn("\\ud800", message)
        self.assertTrue(message.isascii() and "\n" not in message, message)
        raw = io.BytesIO()
        stdout = io.TextIOWrapper(raw, encoding="utf-8", errors="strict", newline="\n")
        with contextlib.redirect_stdout(stdout):
            code = main(["--root", root])
        stdout.flush()
        self.assertEqual(code, 1)
        self.assertIn(b"duplicate key", raw.getvalue())

    def test_hooks_reject_any_extra_key_on_a_hook_command(self):
        for key, value in (
            ("async", True),
            ("async", False),
            ("onFailure", "ignore"),
            ("if", "Bash(git *)"),
            ("shell", "powershell"),
            ("statusMessage", "x"),
        ):
            for event, _ in EXPECTED_HOOKS:
                with self.subTest(key=key, value=value, event=event):
                    block = hooks_block()
                    block[event][0]["hooks"][0][key] = value
                    files = clean_files()
                    files[".claude/settings.json"] = settings_text(block)
                    findings = check_repo(self.repo(files))
                    self.assert_finding(
                        findings, "error", ".claude/settings.json", "hook %s has unexpected key `%s`" % (event, key)
                    )
                    self.assertEqual(len(findings), 1, findings)

    def test_hooks_reject_any_extra_key_on_a_matcher_group(self):
        for event, _ in EXPECTED_HOOKS:
            with self.subTest(event=event):
                block = hooks_block()
                block[event][0]["disabled"] = True
                files = clean_files()
                files[".claude/settings.json"] = settings_text(block)
                findings = check_repo(self.repo(files))
                self.assert_finding(
                    findings, "error", ".claude/settings.json", "hook %s entry has unexpected key `disabled`" % event
                )
                self.assertEqual(len(findings), 1, findings)

    def test_hooks_exact_keys_pass(self):
        # The clean block has exactly type, command and timeout, and matcher plus hooks (or hooks alone).
        block = hooks_block()
        for event, matcher in EXPECTED_HOOKS:
            self.assertEqual(sorted(block[event][0]["hooks"][0]), ["command", "timeout", "type"])
            if event == "Stop":
                self.assertEqual(
                    sorted(block[event][0]["hooks"][1]), ["command", "timeout", "type"]
                )
            expected = ["hooks", "matcher"] if matcher is not None else ["hooks"]
            self.assertEqual(sorted(block[event][0]), expected)
        self.assertEqual(check_claude_hooks(self.repo()), [])

    def test_disable_all_hooks_truthy_fails(self):
        for value in (True, 1, "yes", ["x"], {"a": 1}):
            with self.subTest(value=value):
                files = clean_files()
                files[".claude/settings.json"] = settings_text(extra={"disableAllHooks": value})
                findings = check_repo(self.repo(files))
                self.assert_finding(findings, "error", ".claude/settings.json", "disableAllHooks")
                self.assertEqual(len(findings), 1, findings)

    def test_disable_all_hooks_falsy_passes(self):
        for value in (False, 0, "", None, [], {}):
            with self.subTest(value=value):
                files = clean_files()
                files[".claude/settings.json"] = settings_text(extra={"disableAllHooks": value})
                self.assertEqual(check_repo(self.repo(files)), [])

    def test_hooks_with_missing_engine_fail(self):
        files = clean_files()
        del files[ENGINE_FILE]
        findings = check_claude_hooks(self.repo(files))
        self.assert_finding(findings, "error", ENGINE_FILE, "is missing; the Claude Code hooks")
        self.assertEqual(len(findings), 1, findings)
        # With hooked rules present too, the engine is still reported once.
        self.assert_single(check_repo(self.repo(files)), ENGINE_FILE, "")

    def test_missing_engine_without_hooks_is_not_a_hook_finding(self):
        files = clean_files()
        del files[ENGINE_FILE]
        files[".claude/settings.json"] = json.dumps({"enabledPlugins": {}}) + "\n"
        findings = check_claude_hooks(self.repo(files))
        self.assert_finding(findings, "error", ".claude/settings.json", "has no `hooks` block")
        self.assertEqual(len(findings), 1, findings)

    def test_hooks_keep_other_settings(self):
        files = clean_files()
        files[".claude/settings.json"] = settings_text(extra={"model": "x"})
        self.assertEqual(check_repo(self.repo(files)), [])

    def test_opencode_extra_key_fails(self):
        files = clean_files()
        files["opencode.json"] = opencode_text(extra={"model": "x"})
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", "opencode.json", "unexpected key `model`")
        self.assertEqual(len(findings), 1, findings)

    def pinned_entry(self):
        return {
            "package": OPENCODE_PACKAGE,
            "options": {"additionalRuleDirectories": [".agents/rules"]},
        }

    def test_opencode_two_entries_ok(self):
        for plugins in (
            [self.pinned_entry(), LOCAL_PLUGIN],
            [LOCAL_PLUGIN, self.pinned_entry()],
        ):
            with self.subTest(plugins=plugins):
                files = clean_files()
                files["opencode.json"] = opencode_text(plugins=plugins)
                self.assertEqual(check_repo(self.repo(files)), [])

    def test_opencode_third_plugin(self):
        files = clean_files()
        files["opencode.json"] = opencode_text(
            plugins=[self.pinned_entry(), LOCAL_PLUGIN, "./scripts/other-plugin"]
        )
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", "opencode.json", "exactly two")
        self.assertEqual(len(findings), 1, findings)

    def test_opencode_second_plugin_fails(self):
        # A second `opencode-rules` entry, the local entry twice and a lone entry all fail.
        cases = (
            ([self.pinned_entry(), self.pinned_entry()], "`opencode-rules` entry"),
            ([self.pinned_entry(), LOCAL_PLUGIN, LOCAL_PLUGIN], "exactly two"),
            ([LOCAL_PLUGIN, LOCAL_PLUGIN], "`opencode-rules` entry"),
            ([self.pinned_entry()], "exactly two"),
            ([], "exactly two"),
        )
        for plugins, fragment in cases:
            with self.subTest(plugins=plugins):
                files = clean_files()
                files["opencode.json"] = opencode_text(plugins=plugins)
                findings = check_repo(self.repo(files))
                self.assert_finding(findings, "error", "opencode.json", fragment)

    def test_opencode_duplicate_local_entry(self):
        files = clean_files()
        files["opencode.json"] = opencode_text(plugins=[LOCAL_PLUGIN, LOCAL_PLUGIN])
        findings = check_repo(self.repo(files))
        self.assert_finding(findings, "error", "opencode.json", "local plugin `%s` is declared 2 times" % LOCAL_PLUGIN)

    def test_opencode_missing_local_entry(self):
        files = clean_files()
        files["opencode.json"] = opencode_text(plugins=[self.pinned_entry(), self.pinned_entry()])
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", "opencode.json", "has no local plugin `%s`" % LOCAL_PLUGIN
        )
        self.assert_finding(findings, "error", "opencode.json", "second `opencode-rules` entry")

    def test_opencode_wrong_local_string(self):
        for wrong in (
            "./scripts/opencode-hooked-rules/index.js",
            "./scripts/opencode-hooked-rules/",
            "scripts/opencode-hooked-rules",
            "./scripts/other",
            "",
        ):
            with self.subTest(entry=wrong):
                files = clean_files()
                files["opencode.json"] = opencode_text(plugins=[self.pinned_entry(), wrong])
                findings = check_repo(self.repo(files))
                self.assert_finding(
                    findings, "error", "opencode.json", "plugin string `%s` is not `%s`" % (wrong, LOCAL_PLUGIN)
                )

    def test_opencode_other_entry_types(self):
        for wrong in (7, None, ["x"], {"package": "other-plugin@1.0.0"}):
            with self.subTest(entry=wrong):
                files = clean_files()
                files["opencode.json"] = opencode_text(plugins=[self.pinned_entry(), wrong])
                findings = check_repo(self.repo(files))
                self.assertTrue(findings)
                self.assertTrue(all(f.path == "opencode.json" for f in findings), findings)

    def test_opencode_local_dir_missing_index(self):
        files = clean_files()
        del files[LOCAL_PLUGIN_INDEX]
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", "opencode.json", "local plugin `%s` has no `index.js`" % LOCAL_PLUGIN
        )
        self.assertEqual(len(findings), 1, findings)

    def test_opencode_local_dir_with_only_other_files(self):
        files = clean_files()
        del files[LOCAL_PLUGIN_INDEX]
        files["scripts/opencode-hooked-rules/index.test.mjs"] = "// test\n"
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", "opencode.json", "local plugin `%s` has no `index.js`" % LOCAL_PLUGIN
        )

    def test_opencode_unpinned_fails(self):
        cases = (
            ("", "no version constraint"),
            ("@latest", "dist-tag"),
            ("@*", "unbounded"),
            ("@^1.0.0", "below 2.0.0"),
            ("@>=1.0.0 <3", "below 2.0.0"),
            ("@<=2.9.9", "below 2.0.0"),
            ("@2.1.0", "exact pin"),
            ("@=2.1.0 <3", "exact pin"),
            ("@>=2.1.0 <=2.1.0", "exact pin"),
            ("@2.1.0 >=2 <3", "exact pin"),
            ("@>2.1.0 <2.1.2", "exact pin"),
            ("@>=2.1.0 <2.1.1", "exact pin"),
            ("@>2.1.0 <=2.1.1", "exact pin"),
            ("@>2.0.0 <2.0.2", "exact pin"),
            ("@>=2.0.0 <4", "3.0.0 and above"),
            ("@3.x", "3.0.0 and above"),
            ("@>=3.0.0", "no upper bound"),
            ("@^2.0.0-beta.1", "not a semver range"),
            ("@v2.0.0", "`v` prefix"),
        )
        for suffix, fragment in cases:
            with self.subTest(spec=suffix):
                files = clean_files()
                files["opencode.json"] = opencode_text(package="opencode-rules" + suffix)
                findings = check_repo(self.repo(files))
                self.assert_finding(findings, "error", "opencode.json", fragment)
                self.assertEqual(len(findings), 1, findings)

    def test_opencode_range_accepted(self):
        specs = (
            ">=2.0.0 <3",
            ">= 2.0.0 < 3",
            ">1 <3",
            "^2.0.0",
            "^2",
            "~2.1.0",
            "2.x",
            "2.1.x",
            ">=2.1.0 <3.0.0",
            "~2",
            ">=2.0.0 <2.1.0",
            ">2.0.0 <2.0.3",
            ">=2.0.0 <=2.9.9",
            ">2.9.0 <3",
        )
        for spec in specs:
            with self.subTest(spec=spec):
                files = clean_files()
                files["opencode.json"] = opencode_text(package="opencode-rules@" + spec)
                self.assertEqual(check_repo(self.repo(files)), [])

    def test_opencode_missing_rules_dir_fails(self):
        for directories in ((".agents/other",), ()):
            with self.subTest(directories=directories):
                files = clean_files()
                files["opencode.json"] = opencode_text(directories=directories)
                findings = check_repo(self.repo(files))
                self.assert_finding(
                    findings,
                    "error",
                    "opencode.json",
                    "`options.additionalRuleDirectories` must contain `.agents/rules`",
                )
                self.assertEqual(len(findings), 1, findings)

    def test_plugin_mention_required_in_root_and_contributing(self):
        files = clean_files()
        files["AGENTS.md"] = root_text(mention=False)
        files["CONTRIBUTING.md"] = contributing_text(mention=False)
        findings = check_repo(self.repo(files))
        self.assert_finding(
            findings, "error", "AGENTS.md", "does not mention the `opencode-rules` plugin"
        )
        self.assert_finding(
            findings, "error", "CONTRIBUTING.md", "does not mention the `opencode-rules` plugin"
        )
        self.assertEqual(len(findings), 2, findings)

    def test_plugin_range_not_repeated_in_docs(self):
        for path in (
            "AGENTS.md",
            "CONTRIBUTING.md",
            "docs/AGENTS.md",
            "docs/opencode-rules.md",
            "docs/agent-guidance.md",
        ):
            with self.subTest(path=path):
                files = clean_files()
                files[path] += "\nThe plugin is `opencode-rules@2.x` here.\n"
                findings = check_repo(self.repo(files))
                self.assert_finding(findings, "error", path, "opencode-rules@2.x")
                self.assertEqual(len(findings), 1, findings)


def _add_hook_tests():
    """One missing, wrong-matcher, wrong-command and missing-timeout test per hook."""

    def build(name, event, expected_matcher, kind):
        def test(self):
            files = clean_files()
            if kind == "missing":
                hooks = hooks_block(drop=event)
                fragment = "hook %s is missing" % event
            elif kind == "matcher":
                # Where no matcher is expected a stray one is wrong; elsewhere a different one is.
                wrong = "Write" if expected_matcher is None else expected_matcher + "|Write"
                hooks = hooks_block(matchers={event: wrong})
                fragment = "hook %s has matcher" % event
            elif kind == "command":
                hooks = hooks_block(commands={event: "python3 scripts/agent_rules.py hook"})
                fragment = "hook %s has command" % event
            else:
                hooks = hooks_block(timeouts={event: False})
                fragment = "hook %s has no timeout" % event
            files[".claude/settings.json"] = settings_text(hooks)
            findings = check_repo(self.repo(files))
            self.assert_finding(findings, "error", ".claude/settings.json", fragment)
            self.assertEqual(len(findings), 1, findings)

        test.__name__ = name
        return test

    for event, matcher in EXPECTED_HOOKS:
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()
        for kind, prefix in (
            ("missing", "test_hook_missing_"),
            ("matcher", "test_hook_wrong_matcher_"),
            ("command", "test_hook_wrong_command_"),
            ("timeout", "test_hook_missing_timeout_"),
        ):
            name = prefix + snake
            setattr(AgentFileCheckTest, name, build(name, event, matcher, kind))


_add_hook_tests()


if __name__ == "__main__":
    unittest.main()
