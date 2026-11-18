#!/usr/bin/env python3
"""Agent-file layout check.

Checks the legacy files, the AGENTS.md notices and line caps, node-map parity, rule files,
the `.claude/` symlinks and skill set, `opencode.json`, `.github/CODEOWNERS`, the
`opencode-rules` mentions and the component overrides. Stdlib only; Python 3.8 compatible;
run from the `docs / agent-files` CI check.
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys

Finding = collections.namedtuple("Finding", "severity path message")

NOTICE_ROOT = (
    "> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there."
)
NOTICE_NESTED = (
    "> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; "
    "if you started in this folder, read this folder's rule files listed below by hand."
)

NODE_FILES = (
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

# Nested files that deliberately own the whole folder and so need no component override.
NESTED_EXCLUSIVE_DOCS = ("docs/AGENTS.md", "native-bridge/AGENTS.md")

ROOT_AGENTS = "AGENTS.md"
COMPONENTS_JSON = ".github/components.json"

CLAUDE_RULES = ".claude/rules"
CLAUDE_RULES_TARGET = "../.agents/rules"
CLAUDE_SKILLS = ".claude/skills"
SKILLS_DIR = ".agents/skills"
SKILLS_MARKER = ".openspec-target"
SKILL_TARGET = "../../.agents/skills/%s"
SETTINGS_JSON = ".claude/settings.json"
DOCUMENTED_SKILLS = "docs/AGENTS.md"

OPENCODE_JSON = "opencode.json"
OPENCODE_KEYS = ("$schema", "plugins")
PLUGIN_NAME = "opencode-rules"
PLUGIN_OPTION_DIRS = "additionalRuleDirectories"

CODEOWNERS = ".github/CODEOWNERS"
CODEOWNER_PATTERNS = (
    "/opencode.json",
    "/.claude/",
    "/.agents/",
    "**/AGENTS.md",
    "/docs/superspec-workflow.md",
    "/openspec/config.yaml",
    "/openspec/schemas/",
    "/scripts/agent_rules.py",
    "/scripts/opencode-hooked-rules/",
)
CODEOWNER_MCP_PATTERN = "/.mcp.json"
MCP_JSON = ".mcp.json"
# One representative path per required pattern, used to apply GitHub's last-match rule.
CODEOWNER_SAMPLE_PATHS = (
    ("/opencode.json", "opencode.json"),
    ("/.claude/", ".claude/settings.json"),
    ("/.agents/", ".agents/rules/repo-conventional-commits.md"),
    ("**/AGENTS.md", "android/AGENTS.md"),
    ("**/AGENTS.md", "native-bridge/host/AGENTS.md"),
    ("/docs/superspec-workflow.md", "docs/superspec-workflow.md"),
    ("/openspec/config.yaml", "openspec/config.yaml"),
    ("/openspec/schemas/", "openspec/schemas/superspec/schema.yaml"),
    ("/scripts/agent_rules.py", "scripts/agent_rules.py"),
    ("/scripts/opencode-hooked-rules/", "scripts/opencode-hooked-rules/index.js"),
)

# The Claude Code hooks that deliver the hooked rules: event -> expected matcher (None: no
# `matcher` key). Each event has exactly one entry holding exactly one command.
CLAUDE_HOOK_COMMAND = 'python3 -I "$CLAUDE_PROJECT_DIR/scripts/agent_rules.py" hook --format claude'
CLAUDE_HOOK_TIMEOUT = 5
CLAUDE_HOOKS = (
    ("PreToolUse", "Bash"),
    ("PostToolUse", "Bash|Skill|Read"),
    ("UserPromptSubmit", None),
    ("PreCompact", None),
)

PLUGIN_MENTION_FILES = (ROOT_AGENTS, "CONTRIBUTING.md")
PLUGIN_RANGE_DOCS = (ROOT_AGENTS, "CONTRIBUTING.md", DOCUMENTED_SKILLS)
PLUGIN_RANGE_RE = re.compile(r"opencode-rules@[^\s`\"'<>()\[\],;]+")

ROOT_LINE_WARN = 60
ROOT_LINE_MAX = 120

NODE_MAP_RE = re.compile(r"^-\s+`([^`\s]+)/`\s+—")

LEGACY_BASENAMES = ("CLAUDE.md", "CLAUDE.local.md")
LEGACY_PATHS = (".opencode/", ".claude/commands/")

RULES_DIR = ".agents/rules"
RULE_SIZE_MAX = 24000
# A `repo-*` rule is always on, so the whole rule sits in every session's context: its cap is the
# always-on context budget, not the node-rule cap.
REPO_RULE_SIZE_MAX = 4096
RULE_KEYS = ("trigger", "globs", "paths")

# Scope -> folder pattern. `repo` rules are always on and so have no pattern.
SCOPES = {
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

# The AGENTS.md that must list a scope's rule files (the root file for `repo`).
SCOPE_AGENTS = {
    "repo": ROOT_AGENTS,
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

RULE_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_./\\~-])\.agents/rules/([A-Za-z0-9._-]+\.md)(?![A-Za-z0-9._-])"
)

# Exact `global-overrides` targets from the change's Global Constraints. Only entries present
# in the component map are compared: a missing entry is `_check_overrides`'s own finding.
OVERRIDE_TARGETS = dict(
    (path, ("docs",))
    for path in NODE_FILES
    if path not in NESTED_EXCLUSIVE_DOCS and path != "native-bridge/host/AGENTS.md"
)
OVERRIDE_TARGETS.update(
    {
        "native-bridge/host/AGENTS.md": ("docs", "native-host"),
        ".github/CODEOWNERS": ("docs",),
        ".github/components.json": ("tooling", "docs"),
        ".github/workflows/check-docs.yml": ("docs", "tooling", "installer", "supply-chain"),
    }
)

# The documented-skill and semver grammars are deliberately small and explicit.
SKILL_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
SKILL_NAME_RE = re.compile(r"^`([^`]+)`$")
DIGITS_RE = re.compile(r"^[0-9]+$")
DIST_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
WILDCARD_PARTS = ("x", "X", "*")
OPERATORS = (">=", "<=", ">", "<", "=", "^", "~")
SUPPORTED_LOW = (2, 0, 0)
SUPPORTED_HIGH = (3, 0, 0)

# The restricted frontmatter grammar: `key: scalar`, `key: ["a", "b"]` or `key:` plus
# `  - "a"` block items. Anything richer is rejected instead of being guessed at.
FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(?: (.*))?$")
FRONTMATTER_ITEM = r'"(?:[^"\\]|\\.)*"'
FRONTMATTER_FLOW_RE = re.compile(
    r"^\[(" + FRONTMATTER_ITEM + r"(?:, " + FRONTMATTER_ITEM + r")*)\]$"
)
FRONTMATTER_BLOCK_RE = re.compile(r'^  - (' + FRONTMATTER_ITEM + r")$")


def _git(root, *args):
    """Run a git query in ``root`` and return its stdout decoded as text."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "git %s failed in %s: %s"
            % (" ".join(args), root, result.stderr.decode("utf-8", "replace").strip())
        )
    return result.stdout.decode("utf-8", "surrogateescape")


def list_files(root):
    """Tracked and untracked-but-not-ignored paths, without directory entries.

    ``-z`` keeps git from quoting non-ASCII or Windows paths; entries ending in ``/``
    are nested git worktrees, not files.
    """
    raw = _git(root, "ls-files", "-z", "-co", "--exclude-standard")
    return [path for path in raw.split("\0") if path and not path.endswith("/")]


def index_entries(root):
    """Map of tracked path -> ``(index mode, blob sha)``, for example ``("120000", …)``."""
    entries = {}
    for entry in _git(root, "ls-files", "-s", "-z").split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        fields = meta.split()
        if path and len(fields) >= 2:
            entries[path] = (fields[0], fields[1])
    return entries


def tracked_modes(root):
    """Map of tracked path -> index mode, for example ``120000`` for a symlink."""
    return {path: entry[0] for path, entry in index_entries(root).items()}


def list_links(root):
    """Paths that are symlinks: index mode for tracked ones, the filesystem for new ones."""
    tracked = tracked_modes(root)
    links = set()
    for path in list_files(root):
        if path in tracked:
            if tracked[path] == "120000":
                links.add(path)
        elif os.path.islink(os.path.join(root, path)):
            links.add(path)
    return sorted(links)


def _read_text(root, path):
    """Normalised file text, or None when the file cannot be read (CRLF becomes LF)."""
    try:
        with open(
            os.path.join(root, path),
            "r",
            encoding="utf-8",
            errors="surrogateescape",
            newline="",
        ) as handle:
            return handle.read().replace("\r\n", "\n")
    except (IOError, OSError):
        return None


def _frontmatter_scalar(value):
    """One plain, unquoted scalar; every other YAML form is outside the grammar."""
    if not value or value != value.strip():
        raise ValueError("a plain scalar must not be empty or padded with whitespace: %r" % value)
    if ":" in value:
        raise ValueError("a plain scalar must not contain `:`: %r" % value)
    if "&" in value or "*" in value:
        raise ValueError("anchors and aliases are not supported: %r" % value)
    if value[0] in "'\"[]{}|>!%@`#,":
        raise ValueError("unsupported YAML construct: %r" % value)
    return value


def _frontmatter_quoted(item):
    """Decode one double-quoted list item, accepting only the ``\\"`` and ``\\\\`` escapes."""
    body = item[1:-1]
    characters = []
    index = 0
    while index < len(body):
        character = body[index]
        if character == "\\":
            escape = body[index + 1]
            if escape not in ('"', "\\"):
                raise ValueError("unsupported escape sequence \\%s" % escape)
            characters.append(escape)
            index += 2
        else:
            characters.append(character)
            index += 1
    return "".join(characters)


def _frontmatter_flow(value):
    """A one-line list of double-quoted strings, for example ``["a", "b"]``."""
    if value == "[]":
        raise ValueError("a list must hold at least one item")
    match = FRONTMATTER_FLOW_RE.match(value)
    if match is None:
        raise ValueError("a list must be written as double-quoted strings, got %r" % value)
    return [_frontmatter_quoted(item) for item in re.findall(FRONTMATTER_ITEM, match.group(1))]


def parse_frontmatter(text):
    """Parse the restricted frontmatter of a rule file, or raise ``ValueError``.

    Tolerates CRLF (and a lone CR) and strips a leading UTF-8 BOM. Values are plain, unquoted
    scalars or lists of double-quoted strings. Anchors, aliases, nested maps, duplicate keys,
    unquoted list items, unsupported escapes, tabs, empty lists and any other YAML construct are
    rejected rather than misread, so a malformed rule can never silently pass the check.
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = [
        line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    start = None
    for index, line in enumerate(lines):
        if line:
            start = index
            break
    if start is None or lines[start] != "---":
        raise ValueError("frontmatter must start with a `---` line")
    values = {}
    block_key = None
    for line in lines[start + 1:]:
        if line == "---":
            if block_key is not None and not values[block_key]:
                raise ValueError("`%s` has no list items" % block_key)
            return values
        if block_key is not None:
            item = FRONTMATTER_BLOCK_RE.match(line)
            if item is not None:
                values[block_key].append(_frontmatter_quoted(item.group(1)))
                continue
            if not values[block_key]:
                raise ValueError("`%s` has no list items" % block_key)
            block_key = None
        if not line:
            continue
        if "\t" in line:
            raise ValueError("tabs are not allowed in frontmatter: %r" % line)
        if line.startswith(" "):
            raise ValueError("nested mappings and indentation are not supported: %r" % line)
        match = FRONTMATTER_KEY_RE.match(line)
        if match is None:
            raise ValueError("expected `key: value` or `key:` in frontmatter: %r" % line)
        key = match.group(1)
        value = match.group(2)
        if key not in RULE_KEYS:
            raise ValueError("unsupported frontmatter key %r" % key)
        if key in values:
            raise ValueError("duplicate frontmatter key %r" % key)
        if value is None:
            values[key] = []
            block_key = key
        elif value.startswith("["):
            values[key] = _frontmatter_flow(value)
        else:
            values[key] = _frontmatter_scalar(value)
    raise ValueError("frontmatter is missing its closing `---` line")


def _check_legacy(files):
    findings = []
    for path in sorted(files):
        basename = path.rsplit("/", 1)[-1]
        if basename in LEGACY_BASENAMES:
            findings.append(
                Finding("error", path, "is a legacy agent file; agent guidance lives in AGENTS.md")
            )
        elif basename == "CONTRIBUTION.md":
            findings.append(Finding("error", path, "is a legacy misspelling of CONTRIBUTING.md"))
        elif path.startswith(LEGACY_PATHS):
            findings.append(
                Finding("error", path, "is under a legacy agent tooling path; remove it")
            )
    return findings


def _check_root(text):
    if text is None:
        return [Finding("error", ROOT_AGENTS, "is missing")]
    findings = []
    if NOTICE_ROOT not in text:
        findings.append(Finding("error", ROOT_AGENTS, "is missing the run-from-root notice"))
    lines = len(text.splitlines())
    if lines > ROOT_LINE_MAX:
        findings.append(
            Finding(
                "error",
                ROOT_AGENTS,
                "has %d lines; the root AGENTS.md hard limit is %d lines"
                % (lines, ROOT_LINE_MAX),
            )
        )
    elif lines > ROOT_LINE_WARN:
        findings.append(
            Finding(
                "warning",
                ROOT_AGENTS,
                "has %d lines; the root AGENTS.md cap is %d lines" % (lines, ROOT_LINE_WARN),
            )
        )
    return findings


def _has_rules_section(text):
    return any(line.strip() == "## Rules" for line in text.splitlines())


def _check_nested_files(root, tracked_nested):
    """Validate every nested agent file: the 12 required ones plus any other tracked one."""
    findings = []
    for path in sorted(set(NODE_FILES) | set(tracked_nested)):
        text = _read_text(root, path)
        if text is None:
            if path in NODE_FILES:
                findings.append(Finding("error", path, "is a required AGENTS.md and is missing"))
            continue
        if NOTICE_NESTED not in text:
            findings.append(Finding("error", path, "is missing the nested run-from-root notice"))
        if not _has_rules_section(text):
            findings.append(Finding("error", path, "is missing a `## Rules` section"))
    return findings


def _node_map(root_text):
    """Paths claimed by the root node map lines of the form ``- `<dir>/` — …``."""
    listed = set()
    if root_text is None:
        return listed
    for line in root_text.splitlines():
        match = NODE_MAP_RE.match(line)
        if match:
            node = match.group(1).strip("/")
            if node:
                listed.add(node + "/AGENTS.md")
    return listed


def _check_node_map(root_text, tracked_nested):
    listed = _node_map(root_text)
    tracked = set(tracked_nested)
    findings = []
    for path in sorted(listed - tracked):
        findings.append(
            Finding(
                "error",
                path,
                "is listed in the root node map but is not a tracked AGENTS.md",
            )
        )
    for path in sorted(tracked - listed):
        findings.append(Finding("error", path, "is not listed in the root node map"))
    return findings


def _global_overrides(root):
    """``global-overrides`` from the component map, or (None, problem description)."""
    text = _read_text(root, COMPONENTS_JSON)
    if text is None:
        return None, "cannot be read"
    try:
        data = json.loads(text)
    except ValueError as error:
        return None, "is not valid JSON (%s)" % error
    overrides = data.get("global-overrides") if isinstance(data, dict) else None
    if not isinstance(overrides, dict):
        return None, "has no global-overrides object"
    return overrides, None


def _check_overrides(root, tracked_nested):
    overrides, problem = _global_overrides(root)
    findings = []
    if problem is not None:
        findings.append(Finding("error", COMPONENTS_JSON, problem))
        overrides = {}
    for path in tracked_nested:
        if path in NESTED_EXCLUSIVE_DOCS:
            continue
        if path not in overrides:
            findings.append(
                Finding(
                    "error",
                    path,
                    "has no entry in .github/components.json global-overrides",
                )
            )
    for path in sorted(OVERRIDE_TARGETS):
        if path not in overrides:
            continue
        value = overrides[path]
        expected = OVERRIDE_TARGETS[path]
        if (
            not isinstance(value, list)
            or len(value) != len(expected)
            or not all(isinstance(item, str) for item in value)
            or sorted(value) != sorted(expected)
        ):
            findings.append(
                Finding(
                    "error",
                    path,
                    "maps to %s in .github/components.json global-overrides; expected exactly %s"
                    % (json.dumps(value), json.dumps(sorted(expected))),
                )
            )
    return findings


def _scope_for(name):
    """The longest known scope that prefixes ``name`` followed by ``-``, or None.

    Longest-match, never ``split("-", 1)``: ``web-extension-x`` is a ``web-extension`` rule and
    ``host-x`` is a ``host`` rule, not a ``repo`` or ``native`` one.
    """
    candidates = [scope for scope in SCOPES if name.startswith(scope + "-")]
    if not candidates:
        return None
    return max(candidates, key=len)


def _rule_references(root):
    """Map each scope's AGENTS.md path to the rule paths it mentions."""
    references = {}
    for agents_path in SCOPE_AGENTS.values():
        text = _read_text(root, agents_path)
        if text is None:
            continue
        names = RULE_REFERENCE_RE.findall(text)
        if names:
            references[agents_path] = set(RULES_DIR + "/" + name for name in names)
    return references


def _check_rule_frontmatter(path, scope, frontmatter):
    """Per-kind frontmatter rules: ``repo`` is always on, a node rule is scoped to its folder."""
    findings = []
    pattern = SCOPES[scope]
    if pattern is None:
        if frontmatter.get("trigger") != "always_on":
            findings.append(Finding("error", path, "must have frontmatter `trigger: always_on`"))
        for key in ("globs", "paths"):
            if key in frontmatter:
                findings.append(
                    Finding("error", path, "must not set `%s`; `repo` rules are always on" % key)
                )
        return findings
    if frontmatter.get("trigger") != "glob":
        findings.append(Finding("error", path, "must have frontmatter `trigger: glob`"))
    folder = pattern[:-len("**")]
    values = {}
    for key in ("globs", "paths"):
        if key not in frontmatter:
            findings.append(
                Finding("error", path, "must set `%s` to patterns under `%s`" % (key, folder))
            )
            continue
        value = frontmatter[key]
        if not isinstance(value, list):
            findings.append(Finding("error", path, "`%s` must be a YAML list, not a scalar" % key))
            continue
        if any("{" in item or "}" in item for item in value):
            findings.append(Finding("error", path, "`%s` must not use brace expansion" % key))
            continue
        if any(".." in item.split("/") for item in value):
            findings.append(
                Finding("error", path, "`%s` must not use parent-directory traversal" % key)
            )
            continue
        outside = [item for item in value if not item.startswith(folder)]
        if outside:
            findings.append(
                Finding(
                    "error",
                    path,
                    "`%s` must list patterns under `%s`, got %s"
                    % (key, folder, ", ".join('"%s"' % item for item in outside)),
                )
            )
            continue
        values[key] = value
    if len(values) == 2 and values["globs"] != values["paths"]:
        findings.append(Finding("error", path, "`paths` must match `globs`"))
    return findings


def check_rules(root, files):
    """Check `.agents/rules/`: flat `<scope>-<topic>.md` names, size, frontmatter and listings."""
    findings = []
    rules = {}
    for path in sorted(files):
        if not path.startswith(RULES_DIR + "/"):
            continue
        relative = path[len(RULES_DIR) + 1:]
        if "/" in relative:
            findings.append(
                Finding("error", path, "is in a subdirectory; `.agents/rules/` is flat")
            )
            continue
        if not relative.endswith(".md"):
            findings.append(
                Finding("error", path, "does not end with `.md`; name rules `<scope>-<topic>.md`")
            )
            continue
        name = relative[:-len(".md")]
        scope = _scope_for(name)
        if scope is None:
            findings.append(
                Finding("error", path, "has no known scope; name rules `<scope>-<topic>.md`")
            )
            continue
        if not name[len(scope) + 1:]:
            findings.append(
                Finding("error", path, "has an empty topic; name rules `<scope>-<topic>.md`")
            )
            continue
        rules[path] = scope
        cap = REPO_RULE_SIZE_MAX if scope == "repo" else RULE_SIZE_MAX
        try:
            size = os.path.getsize(os.path.join(root, path))
        except OSError:
            size = None
        if size is not None and size > cap:
            findings.append(
                Finding(
                    "error",
                    path,
                    "is %d bytes; the rule size cap is %d bytes" % (size, cap),
                )
            )
            # Do not read a file that already failed the cap: parsing it is wasted work at best
            # and unbounded memory at worst. The listing check below still applies.
            continue
        text = _read_text(root, path)
        if text is None:
            findings.append(Finding("error", path, "cannot be read"))
            continue
        try:
            frontmatter = parse_frontmatter(text)
        except ValueError as error:
            findings.append(Finding("error", path, "has invalid frontmatter: %s" % error))
            continue
        findings.extend(_check_rule_frontmatter(path, scope, frontmatter))

    references = _rule_references(root)
    for path, scope in sorted(rules.items()):
        agents_path = SCOPE_AGENTS[scope]
        if path not in references.get(agents_path, set()):
            findings.append(Finding("error", path, "is not listed in %s" % agents_path))
    known = set(files)
    for agents_path in sorted(references):
        for path in sorted(references[agents_path]):
            if path not in known:
                findings.append(
                    Finding("error", path, "is referenced by %s but does not exist" % agents_path)
                )
    return findings


def parse_documented_skills(text):
    """Skill names from the bullets under the `## OpenSpec skills` heading of `docs/AGENTS.md`.

    Bullets are backticked names (``- `openspec-propose```); a bullet without backticks uses
    its first token. The section ends at the next level-two heading, and a missing section
    parses to an empty tuple rather than raising.
    """
    names = []
    in_section = False
    normal = text.replace("\r\n", "\n").replace("\r", "\n")
    for line in normal.split("\n"):
        if line.strip() == "## OpenSpec skills":
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("## "):
            break
        match = SKILL_BULLET_RE.match(line.strip())
        if match is None:
            continue
        item = match.group(1).strip()
        if not item:
            continue
        wrapped = SKILL_NAME_RE.match(item)
        names.append(wrapped.group(1) if wrapped else item.split()[0])
    return tuple(names)


def _link_standin(path, platform):
    """A regular-file stand-in for a committed symlink: an error, or a Windows warning."""
    severity = "warning" if platform == "win32" else "error"
    return Finding(
        severity,
        path,
        "is not a symlink in this checkout; set `core.symlinks=true` and enable Developer Mode "
        "on Windows so git checks the link out",
    )


def _check_required_link(root, path, expected, entries, platform):
    """One required symlink: present, committed as a link, and pointing at `expected`."""
    full = os.path.join(root, path)
    entry = entries.get(path)
    if entry is None:
        if os.path.islink(full):
            actual = os.readlink(full)
        elif os.path.isdir(full):
            return [
                Finding("error", path, "is a real directory, not a symlink to `%s`" % expected)
            ]
        elif os.path.lexists(full):
            return [_link_standin(path, platform)]
        else:
            return [Finding("error", path, "is missing; expected a symlink to `%s`" % expected)]
        if actual != expected:
            return [Finding("error", path, "points to `%s`; expected `%s`" % (actual, expected))]
        return []
    if entry[0] != "120000":
        return [
            Finding("error", path, "is committed as a regular file, not a symlink to `%s`" % expected)
        ]
    if os.path.islink(full):
        actual = os.readlink(full)
        if actual != expected:
            return [Finding("error", path, "points to `%s`; expected `%s`" % (actual, expected))]
        return []
    if not os.path.lexists(full):
        return [
            Finding(
                "error", path, "is missing from the worktree; expected a symlink to `%s`" % expected
            )
        ]
    findings = [_link_standin(path, platform)]
    actual = _git(root, "cat-file", "-p", entry[1])
    if actual != expected:
        findings.append(Finding("error", path, "points to `%s`; expected `%s`" % (actual, expected)))
    return findings


def _skill_names(files):
    """Top-level `openspec-*` and other skill directories in `.agents/skills/`."""
    names = set()
    prefix = SKILLS_DIR + "/"
    for path in files:
        if not path.startswith(prefix):
            continue
        name = path[len(prefix):].split("/", 1)[0]
        if name and name != SKILLS_MARKER:
            names.add(name)
    return sorted(names)


def _claude_skill_entries(files):
    """Immediate skill names under `.claude/skills/`, from every descendant path.

    A real directory has no index entry of its own, so names must come from the first path
    segment of its contents; otherwise a tool-specific skill copy can hide behind the orphan
    check. Unlike `.agents/skills/`, there is no canonical marker file here: a
    `.claude/skills/.openspec-target` entry is a skill-shaped copy and must be checked.
    """
    names = set()
    prefix = CLAUDE_SKILLS + "/"
    for path in files:
        if not path.startswith(prefix):
            continue
        name = path[len(prefix):].split("/", 1)[0]
        if name:
            names.add(name)
    return sorted(names)


def check_links(root, files, platform=sys.platform):
    """Check the `.claude/` symlinks and the skill set against `docs/AGENTS.md`."""
    entries = index_entries(root)
    findings = []
    findings.extend(_check_required_link(root, CLAUDE_RULES, CLAUDE_RULES_TARGET, entries, platform))
    settings_entry = entries.get(SETTINGS_JSON)
    settings_is_link = os.path.islink(os.path.join(root, SETTINGS_JSON))
    if settings_entry is not None and settings_entry[0] == "120000":
        settings_is_link = True
    if settings_is_link:
        findings.append(
            Finding("error", SETTINGS_JSON, "must not be a symlink; keep it a regular file")
        )

    skills = _skill_names(files)
    for name in skills:
        findings.extend(
            _check_required_link(
                root, CLAUDE_SKILLS + "/" + name, SKILL_TARGET % name, entries, platform
            )
        )
    known = set(skills)
    for name in _claude_skill_entries(files):
        if name in known:
            continue
        findings.append(
            Finding(
                "error",
                CLAUDE_SKILLS + "/" + name,
                "is an orphan entry; `.agents/skills/%s` does not exist" % name,
            )
        )

    documented_text = _read_text(root, DOCUMENTED_SKILLS)
    if documented_text is not None:
        documented = set(parse_documented_skills(documented_text))
        present = set(name for name in skills if name.startswith("openspec-"))
        for name in sorted(documented - present):
            findings.append(
                Finding(
                    "error",
                    SKILLS_DIR + "/" + name,
                    "is listed under `## OpenSpec skills` in docs/AGENTS.md but is missing "
                    "from .agents/skills",
                )
            )
        for name in sorted(present - documented):
            findings.append(
                Finding(
                    "error",
                    SKILLS_DIR + "/" + name,
                    "is not listed under `## OpenSpec skills` in docs/AGENTS.md",
                )
            )
    return findings


def _bump(parts, index):
    """The version just after `parts` when its component at `index` is incremented."""
    values = list(parts)
    values[index] += 1
    for later in range(index + 1, 3):
        values[later] = 0
    return tuple(values)


def _version(text):
    """``(parts, specified, wildcard)`` for a version token, or None outside the grammar."""
    bits = text.split(".")
    if not 1 <= len(bits) <= 3:
        return None
    parts = []
    specified = 0
    wildcard = False
    for bit in bits:
        if bit in WILDCARD_PARTS:
            wildcard = True
            parts.append(0)
            continue
        if wildcard or DIGITS_RE.match(bit) is None:
            return None
        parts.append(int(bit))
        specified += 1
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts), specified, wildcard


def _next_patch(parts):
    """The smallest stable version above `parts`: the exclusive lower bound made inclusive."""
    return (parts[0], parts[1], parts[2] + 1)


def _previous_patch(parts):
    """The largest stable version below `parts`, as an exclusive upper bound made inclusive.

    For an `x.y.0` boundary no largest stable version exists (the patch is unbounded), so the
    discrete predecessor `x.y.-1` stands in: it compares below every real `x.y.*` version and
    above every earlier minor, which is all the boundary and singleton checks need.
    """
    return (parts[0], parts[1], parts[2] - 1)


def _semver_problem(spec):
    """Why `spec` is not a range inside `>=2.0.0 <3`, or None when it is.

    Uses npm's comparator-set semantics: whitespace around an operator is ignored, and a
    partial version in an inequality comparator covers the whole next component
    (`>1` is `>=2.0.0`, `>1.2` is `>=1.3.0`, `<2` is `<2.0.0`, `<=2.1` is `<=2.1.x`).
    Caret, tilde and wildcards keep their npm intervals.

    Each comparator contributes a lower and an upper bound with its inclusivity. The strictest
    bounds are combined, then normalized to the smallest and largest stable version the range
    admits (exclusive lower becomes the next patch, exclusive upper the previous patch). The
    range is accepted only when both bounds exist, the smallest is not above the largest, the
    two are not the same version (a singleton is a disguised pin) and both lie inside
    `[2.0.0, 3.0.0)`.
    """
    text = spec.strip()
    if not text:
        return "has no version constraint"
    if "||" in text:
        return "uses `||`; a single range is required"
    if text in WILDCARD_PARTS:
        return "`%s` is unbounded; it admits versions outside the supported 2.x range" % text
    if len(text) > 1 and text[0] == "v" and DIGITS_RE.match(text[1]):
        return "uses a `v` prefix; write the range without it"
    if DIST_TAG_RE.match(text):
        return "`%s` is a dist-tag, not a semver range" % text

    text = re.sub(r"(>=|<=|>|<|=|\^|~)\s+", r"\1", text)
    lower = None  # (version, inclusive)
    upper = None
    for token in text.split():
        operator = ""
        body = token
        for candidate in OPERATORS:
            if token.startswith(candidate):
                operator = candidate
                body = token[len(candidate):]
                break
        parsed = _version(body)
        if parsed is None:
            return "`%s` is not a semver range" % token
        parts, specified, wildcard = parsed
        if specified == 0:
            return "`%s` is unbounded; it admits versions outside the supported 2.x range" % token
        full = specified == 3 and not wildcard
        if operator in ("", "="):
            token_lower = (parts, True)
            token_upper = (parts, True) if full else (_bump(parts, specified - 1), False)
        elif operator == "^":
            token_lower = (parts, True)
            if parts[0] > 0:
                token_upper = (_bump(parts, 0), False)
            elif parts[1] > 0:
                token_upper = (_bump(parts, 1), False)
            else:
                token_upper = (_bump(parts, 2), False)
        elif operator == "~":
            token_lower = (parts, True)
            token_upper = (_bump(parts, 0 if specified == 1 else 1), False)
        elif operator == ">=":
            token_lower = (parts, True)
            token_upper = None
        elif operator == ">":
            # npm's partial comparator: `>1` is `>=2.0.0`, `>1.2` is `>=1.3.0`; a full
            # version stays exclusive (`>2.1.0` does not admit 2.1.0).
            if full:
                token_lower = (parts, False)
            else:
                token_lower = (_bump(parts, specified - 1), True)
            token_upper = None
        elif operator == "<":
            # Partial versions zero-fill, so `<2` is already `<2.0.0`.
            token_lower = None
            token_upper = (parts, False)
        else:  # "<="
            token_lower = None
            if full:
                token_upper = (parts, True)
            else:
                # The inclusive end of a partial version is the next component's start.
                token_upper = (_bump(parts, specified - 1), False)
        if token_lower is not None:
            # At the same version an exclusive bound is stricter than an inclusive one.
            if lower is None or (token_lower[0], not token_lower[1]) > (
                lower[0],
                not lower[1],
            ):
                lower = token_lower
        if token_upper is not None:
            # At the same version an exclusive bound is stricter than an inclusive one.
            if upper is None or (token_upper[0], token_upper[1]) < (upper[0], upper[1]):
                upper = token_upper

    if lower is None:
        return "admits versions below 2.0.0"
    smallest = lower[0] if lower[1] else _next_patch(lower[0])
    if upper is None:
        return "has no upper bound; it admits 3.0.0 and above"
    largest = upper[0] if upper[1] else _previous_patch(upper[0])
    if smallest > largest:
        return "admits no version"
    if smallest == largest:
        return "is an exact pin; use a range"
    if smallest < SUPPORTED_LOW:
        return "admits versions below 2.0.0"
    if largest >= SUPPORTED_HIGH:
        return "admits 3.0.0 and above"
    return None


def _check_opencode_package(package):
    """One plugin package string: `opencode-rules` plus a range inside `>=2.0.0 <3`."""
    name, separator, spec = package.partition("@")
    if not separator:
        return [
            Finding(
                "error",
                OPENCODE_JSON,
                "plugin `%s` has no version constraint; pin `%s` to a supported range"
                % (package, PLUGIN_NAME),
            )
        ]
    if name != PLUGIN_NAME:
        return [Finding("error", OPENCODE_JSON, "plugin `%s` is not `%s`" % (name, PLUGIN_NAME))]
    problem = _semver_problem(spec)
    if problem is None:
        return []
    return [
        Finding(
            "error",
            OPENCODE_JSON,
            "plugin `%s` is not a supported range: %s" % (package, problem),
        )
    ]


def check_opencode(root):
    """Check `opencode.json`: only `$schema`/`plugins`, one pinned plugin and the rule dirs."""
    text = _read_text(root, OPENCODE_JSON)
    if text is None:
        return [Finding("error", OPENCODE_JSON, "is missing")]
    try:
        data = json.loads(text)
    except ValueError as error:
        return [Finding("error", OPENCODE_JSON, "is not valid JSON (%s)" % error)]
    if not isinstance(data, dict):
        return [Finding("error", OPENCODE_JSON, "must be a JSON object")]

    findings = []
    for key in sorted(data):
        if key not in OPENCODE_KEYS:
            findings.append(
                Finding(
                    "error",
                    OPENCODE_JSON,
                    "has unexpected key `%s`; only `$schema` and `plugins` are allowed" % key,
                )
            )
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        findings.append(Finding("error", OPENCODE_JSON, "has no `plugins` list"))
        return findings
    if len(plugins) != 1:
        findings.append(
            Finding(
                "error",
                OPENCODE_JSON,
                "declares %d plugins; exactly one `opencode-rules` entry is required" % len(plugins),
            )
        )
        return findings
    plugin = plugins[0]
    if not isinstance(plugin, dict):
        findings.append(Finding("error", OPENCODE_JSON, "plugin entry must be a JSON object"))
        return findings
    package = plugin.get("package")
    if not isinstance(package, str):
        findings.append(Finding("error", OPENCODE_JSON, "plugin entry has no `package` string"))
    else:
        findings.extend(_check_opencode_package(package))
    options = plugin.get("options")
    directories = options.get(PLUGIN_OPTION_DIRS) if isinstance(options, dict) else None
    if not isinstance(directories, list) or RULES_DIR not in directories:
        findings.append(
            Finding(
                "error",
                OPENCODE_JSON,
                "`options.%s` must contain `%s`" % (PLUGIN_OPTION_DIRS, RULES_DIR),
            )
        )
    return findings


def _codeowners_rules(text):
    """``(pattern, owner tokens)`` for each `.github/CODEOWNERS` rule line, comments ignored."""
    rules = []
    normal = text.replace("\r\n", "\n").replace("\r", "\n")
    for line in normal.split("\n"):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        rules.append((fields[0], fields[1:]))
    return rules


def _has_owner(owners):
    """True when a rule line assigns a reviewer (`@user`, `@org/team` or an email address)."""
    return any("@" in owner for owner in owners)


def _codeowner_glob(pattern):
    """A regex for one CODEOWNERS pattern under GitHub's last-match semantics.

    A leading `/` anchors at the repository root, a trailing `/` covers a directory and its
    contents, `**` spans any depth, `*` stays within one path segment, and an unanchored
    pattern may match at any depth. A non-wildcard pattern also covers descendants of the
    directory it names, with or without a trailing slash.
    """
    anchored = pattern.startswith("/")
    body = pattern[1:] if anchored else pattern
    directory = body.endswith("/")
    body = body.rstrip("/")
    wildcard = "*" in body or "?" in body
    translated = []
    index = 0
    while index < len(body):
        if body.startswith("**/", index):
            translated.append("(?:.*/)?")
            index += 3
        elif body.startswith("**", index):
            translated.append(".*")
            index += 2
        elif body[index] == "*":
            translated.append("[^/]*")
            index += 1
        elif body[index] == "?":
            translated.append("[^/]")
            index += 1
        else:
            translated.append(re.escape(body[index]))
            index += 1
    expression = "".join(translated)
    if not anchored:
        expression = "(?:.*/)?" + expression
    if directory or not wildcard:
        expression += "(?:/.*)?"
    return re.compile("^" + expression + "$")


def check_codeowners(root):
    """Check that `.github/CODEOWNERS` effectively covers the agent guidance and configuration.

    GitHub applies the last matching rule, so ownership is resolved per representative path
    in file order: a later ownerless line removes an earlier owner. A bare pattern with no
    owner token matches files but assigns no reviewer, so it is not coverage.
    """
    text = _read_text(root, CODEOWNERS)
    if text is None:
        return [Finding("error", CODEOWNERS, "is missing")]
    rules = _codeowners_rules(text)
    present = set(pattern.rstrip("/") for pattern, _ in rules)
    required = list(CODEOWNER_PATTERNS)
    samples = list(CODEOWNER_SAMPLE_PATHS)
    if os.path.exists(os.path.join(root, MCP_JSON)):
        required.append(CODEOWNER_MCP_PATTERN)
        samples.append((CODEOWNER_MCP_PATTERN, MCP_JSON))
    findings = []
    for pattern in required:
        if pattern.rstrip("/") not in present:
            findings.append(
                Finding("error", CODEOWNERS, "is missing the `%s` pattern" % pattern)
            )
    matchers = [(_codeowner_glob(pattern), pattern, owners) for pattern, owners in rules]
    for pattern, path in samples:
        if pattern.rstrip("/") not in present:
            continue
        owners = ()
        last_match = None
        for regex, rule_pattern, rule_owners in matchers:
            if regex.match(path):
                owners = rule_owners
                last_match = rule_pattern
        if last_match is None:
            findings.append(
                Finding(
                    "error",
                    CODEOWNERS,
                    "has no owner on the `%s` pattern for `%s`; no rule matches that path"
                    % (pattern, path),
                )
            )
        elif not _has_owner(owners):
            findings.append(
                Finding(
                    "error",
                    CODEOWNERS,
                    "has no owner on the `%s` pattern for `%s`; the last matching rule `%s` "
                    "has no owner" % (pattern, path, last_match),
                )
            )
    return findings


class _StrictJsonError(ValueError):
    """A JSON document that Python accepts but strict JSON does not."""


def _reject_json_constant(name):
    raise _StrictJsonError("has the non-JSON constant `%s`" % name)


def _strict_object(pairs):
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise _StrictJsonError("has a duplicate key `%s`" % key)
        seen.add(key)
    return dict(pairs)


def _hook_findings(event, expected_matcher, entries):
    """Findings for one event's entry list in `.claude/settings.json`."""
    def error(message):
        return [Finding("error", SETTINGS_JSON, "hook %s %s" % (event, message))]

    if not isinstance(entries, list) or not entries:
        return error("must be a non-empty list holding exactly one entry")
    if len(entries) != 1:
        return error(
            "has %d entries; expected exactly one entry with exactly one command" % len(entries)
        )
    entry = entries[0]
    if not isinstance(entry, dict):
        return error("entry must be an object")
    commands = entry.get("hooks")
    if not isinstance(commands, list) or len(commands) != 1:
        return error("must hold exactly one command in its entry")
    command = commands[0]
    if not isinstance(command, dict):
        return error("command must be an object")

    findings = []
    if expected_matcher is None:
        if "matcher" in entry:
            findings.extend(
                error("has matcher %r; expected none (no `matcher` key)" % (entry["matcher"],))
            )
    elif entry.get("matcher") != expected_matcher:
        findings.extend(
            error("has matcher %r; expected %r" % (entry.get("matcher"), expected_matcher))
        )
    if command.get("type") != "command":
        findings.extend(error("has type %r; expected 'command'" % (command.get("type"),)))
    if command.get("command") != CLAUDE_HOOK_COMMAND:
        findings.extend(
            error("has command %r; expected %r" % (command.get("command"), CLAUDE_HOOK_COMMAND))
        )
    timeout = command.get("timeout")
    if "timeout" not in command:
        findings.extend(error("has no timeout; expected %d" % CLAUDE_HOOK_TIMEOUT))
    elif (
        not isinstance(timeout, int) or isinstance(timeout, bool) or timeout != CLAUDE_HOOK_TIMEOUT
    ):
        findings.extend(error("has timeout %r; expected %d" % (timeout, CLAUDE_HOOK_TIMEOUT)))
    return findings


def check_claude_hooks(root):
    """Check that `.claude/settings.json` wires the hooked-rule engine into Claude Code.

    Each of the four events has exactly one entry with exactly one command, the engine's `hook`
    subcommand, the expected matcher (or none) and a 5-second timeout. Whether the engine may
    ever answer `allow` is a property of the engine, tested there, not here.
    """
    if os.path.islink(os.path.join(root, SETTINGS_JSON)):
        return []  # `check_links` reports the symlink; a dangling target is not a second error
    try:
        with open(os.path.join(root, SETTINGS_JSON), "rb") as handle:
            raw = handle.read()
    except (IOError, OSError):
        return [Finding("error", SETTINGS_JSON, "is missing")]
    # Claude Code may reject a file the lenient reader accepts and then load no hooks at all, so
    # this file is read strictly: UTF-8 only, no NaN/Infinity constants, no duplicate members.
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        return [Finding("error", SETTINGS_JSON, "is not valid UTF-8 (%s)" % error)]
    try:
        data = json.loads(
            text, parse_constant=_reject_json_constant, object_pairs_hook=_strict_object
        )
    except _StrictJsonError as error:
        return [Finding("error", SETTINGS_JSON, str(error))]
    except ValueError as error:
        return [Finding("error", SETTINGS_JSON, "is not valid JSON (%s)" % error)]
    if not isinstance(data, dict):
        return [Finding("error", SETTINGS_JSON, "must be a JSON object")]
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return [Finding("error", SETTINGS_JSON, "has no `hooks` block")]
    findings = []
    for event, matcher in CLAUDE_HOOKS:
        if event not in hooks:
            findings.append(Finding("error", SETTINGS_JSON, "hook %s is missing" % event))
            continue
        findings.extend(_hook_findings(event, matcher, hooks[event]))
    return findings


def check_plugin_mentions(root):
    """Check the `opencode-rules` mentions and that only `opencode.json` pins the range."""
    findings = []
    for path in PLUGIN_MENTION_FILES:
        text = _read_text(root, path)
        if text is None:
            findings.append(Finding("error", path, "is missing"))
            continue
        if PLUGIN_NAME not in text:
            findings.append(
                Finding("error", path, "does not mention the `%s` plugin" % PLUGIN_NAME)
            )
    for path in PLUGIN_RANGE_DOCS:
        text = _read_text(root, path)
        if text is None:
            continue
        for match in PLUGIN_RANGE_RE.finditer(text):
            findings.append(
                Finding(
                    "error",
                    path,
                    "repeats the plugin version `%s`; `opencode.json` is the single source "
                    "of the range" % match.group(0),
                )
            )
    return findings


def check_repo(root, platform=sys.platform):
    """Run every check against the repository at ``root`` and return its findings."""
    files = list_files(root)
    tracked = tracked_modes(root)
    tracked_nested = sorted(path for path in tracked if path.endswith("/AGENTS.md"))
    root_text = _read_text(root, ROOT_AGENTS)

    findings = []
    findings.extend(_check_legacy(files))
    findings.extend(_check_root(root_text))
    findings.extend(_check_nested_files(root, tracked_nested))
    findings.extend(_check_node_map(root_text, tracked_nested))
    findings.extend(_check_overrides(root, tracked_nested))
    findings.extend(check_rules(root, files))
    findings.extend(check_links(root, files, platform))
    findings.extend(check_opencode(root))
    findings.extend(check_codeowners(root))
    findings.extend(check_claude_hooks(root))
    findings.extend(check_plugin_mentions(root))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check the agent guidance layout.")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    parser.add_argument(
        "--platform", default=sys.platform, help="platform name used by platform-specific checks"
    )
    args = parser.parse_args(argv)
    try:
        findings = check_repo(args.root, args.platform)
    except (OSError, RuntimeError) as error:
        # A missing root, a non-repository directory or a failing git query is reported like any
        # other error, so callers see the same `severity path: message` shape and exit status 1.
        print("error %s: %s" % (args.root, " ".join(str(error).split())))
        return 1
    for finding in findings:
        print("%s %s: %s" % (finding.severity, finding.path, finding.message))
    return 1 if any(finding.severity == "error" for finding in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
