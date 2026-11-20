#!/usr/bin/env python3
"""Hook-delivered agent rules: the rule parser, the pointer block and the hook event contract.

Standard library only, Python 3.8+, run as ``python3 -I scripts/agent_rules.py``.

A hooked rule is one file in ``.agents/hooked-rules/``. Its frontmatter uses the restricted
grammar of ``docs/checks/check_agent_files.py``: ``key: scalar`` lines and flat lists of
double-quoted strings in which only the ``\\"`` and ``\\\\`` escapes are allowed, extended with a
double-quoted ``pointer``. This module is the one parser for hooked rules: the agent-file check
imports it by path for ``.agents/hooked-rules/``, while ``.agents/rules/`` frontmatter keeps the
checker's own parser.
"""

import argparse
import collections
import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None

RULES_DIR = ".agents/hooked-rules"
AGENTS_FILE = "AGENTS.md"
BODY_LIMIT = 4096
# The most bytes read from one rule file; a larger file is an error. A valid rule is far smaller (the
# body cap plus its frontmatter), so the cap only bounds what a damaged or hostile file can cost.
RULE_FILE_LIMIT = 65536

POINTER_HEADING = "## Hook-delivered rules"
POINTER_BEGIN = "<!-- hooked-rules:begin -->"
POINTER_END = "<!-- hooked-rules:end -->"

SCALAR_KEYS = ("pointer",)
LIST_KEYS = ("skills", "commands", "gates", "stops")
RULE_KEYS = SCALAR_KEYS + LIST_KEYS

# The predicates a rule may name under `stops`; the stop gate implements each one.
FINALIZE_PENDING = "superspec-finalize-pending"
STOP_PREDICATES = (FINALIZE_PENDING,)

FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(?: (.*))?$")
FRONTMATTER_ITEM = r'"(?:[^"\\]|\\.)*"'
FRONTMATTER_QUOTED_RE = re.compile(r"^" + FRONTMATTER_ITEM + r"$")
FRONTMATTER_FLOW_RE = re.compile(
    r"^\[(" + FRONTMATTER_ITEM + r"(?:, " + FRONTMATTER_ITEM + r")*)\]$"
)
FRONTMATTER_BLOCK_RE = re.compile(r"^  - (" + FRONTMATTER_ITEM + r")$")


class RuleError(Exception):
    """A hooked rule file that does not follow the grammar."""


# identity is (file name, sha256 hex of the raw file bytes); skills is a list of strings,
# commands and gates are lists of compiled regexes, and stops is a tuple of predicate names.
Rule = collections.namedtuple(
    "Rule", ["name", "identity", "pointer", "skills", "commands", "gates", "stops", "body"]
)


def _quoted(item):
    """Decode one double-quoted string, accepting only the ``\\"`` and ``\\\\`` escapes."""
    body = item[1:-1]
    characters = []
    index = 0
    while index < len(body):
        character = body[index]
        if character == "\\":
            escape = body[index + 1]
            if escape not in ('"', "\\"):
                raise RuleError("unsupported escape sequence \\%s" % escape)
            characters.append(escape)
            index += 2
        else:
            characters.append(character)
            index += 1
    return "".join(characters)


def _flow(value):
    """A one-line list of double-quoted strings, for example ``["a", "b"]``."""
    if value == "[]":
        raise RuleError("a list must hold at least one item")
    match = FRONTMATTER_FLOW_RE.match(value)
    if match is None:
        raise RuleError("a list must be written as double-quoted strings, got %r" % value)
    return [_quoted(item) for item in re.findall(FRONTMATTER_ITEM, match.group(1))]


def _scalar(key, value):
    """A double-quoted scalar; plain scalars are outside the hooked-rule grammar."""
    if value is None or FRONTMATTER_QUOTED_RE.match(value) is None:
        raise RuleError("`%s` must be a double-quoted string, got %r" % (key, value))
    return _quoted(value)


def _split(text):
    """Return ``(values, body, original_body)`` for a rule's text, or raise ``RuleError``.

    The text may use CRLF or a lone CR and start with a UTF-8 BOM. The body is everything after
    the closing ``---`` line, unchanged apart from the newline normalisation; ``original_body``
    is the same text before that normalisation, which is what the size cap counts.
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    pieces = re.split(r"(\r\n|\r|\n)", text)  # line, terminator, line, terminator, ..., line
    lines = pieces[0::2]
    start = None
    for index, line in enumerate(lines):
        if line.rstrip():
            start = index
            break
    if start is None or lines[start].rstrip() != "---":
        raise RuleError("frontmatter must start with a `---` line")
    values = {}
    block_key = None
    for index in range(start + 1, len(lines)):
        line = lines[index].rstrip()
        if line == "---":
            if block_key is not None and not values[block_key]:
                raise RuleError("`%s` has no list items" % block_key)
            return values, "\n".join(lines[index + 1:]), "".join(pieces[2 * index + 2:])
        if block_key is not None:
            item = FRONTMATTER_BLOCK_RE.match(line)
            if item is not None:
                values[block_key].append(_quoted(item.group(1)))
                continue
            if not values[block_key]:
                raise RuleError("`%s` has no list items" % block_key)
            block_key = None
        if not line:
            continue
        if "\t" in line:
            raise RuleError("tabs are not allowed in frontmatter: %r" % line)
        if line.startswith(" "):
            raise RuleError("nested mappings and indentation are not supported: %r" % line)
        match = FRONTMATTER_KEY_RE.match(line)
        if match is None:
            raise RuleError("expected `key: value` or `key:` in frontmatter: %r" % line)
        key = match.group(1)
        value = match.group(2)
        if key not in RULE_KEYS:
            raise RuleError("unsupported frontmatter key %r" % key)
        if key in values:
            raise RuleError("duplicate frontmatter key %r" % key)
        if key in SCALAR_KEYS:
            values[key] = _scalar(key, value)
        elif value is None:
            values[key] = []
            block_key = key
        elif value.startswith("["):
            values[key] = _flow(value)
        else:
            raise RuleError("`%s` must be a list of double-quoted strings, got %r" % (key, value))
    raise RuleError("frontmatter is missing its closing `---` line")


def _regexes(key, patterns):
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except (re.error, OverflowError, RecursionError) as error:
            raise RuleError(
                "`%s` holds an invalid regex %r: %s: %s"
                % (key, pattern[:80], type(error).__name__, error)
            )
    return compiled


def rule_text(body):
    """The text a rule contributes when it is emitted: its body without leading or trailing blank lines.

    ``body`` already has LF line endings (``_split``). A blank line is one that is empty or holds only
    whitespace. Nothing else changes, so Markdown indentation and trailing spaces inside the text stay.
    """
    lines = body.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def parse_rule(name, text):
    """Parse one hooked rule file's text into a ``Rule``, or raise ``RuleError``.

    The identity hash covers the text as given (the raw file, before newline normalisation). A body
    with no text (empty or only blank lines) is an error: a gate would deny with no reason and a moment
    would credit a rule it never showed. Every `stops` entry must name one of ``STOP_PREDICATES``.
    """
    values, body, original_body = _split(text)
    pointer = values.get("pointer")
    if pointer is None:
        raise RuleError("`pointer` is required")
    if not pointer.strip():
        raise RuleError("`pointer` must not be empty")
    skills = values.get("skills", [])
    commands = _regexes("commands", values.get("commands", []))
    gates = _regexes("gates", values.get("gates", []))
    stops = tuple(values.get("stops", []))
    for stop in stops:
        if stop not in STOP_PREDICATES:
            raise RuleError("unknown stop predicate %r" % (stop,))
    if not (skills or commands or gates or stops):
        raise RuleError(
            "a rule needs at least one entry in `skills`, `commands`, `gates` or `stops`"
        )
    size = len(original_body.encode("utf-8", "surrogatepass"))
    if size > BODY_LIMIT:
        raise RuleError("the body is %d bytes, over the %d byte limit" % (size, BODY_LIMIT))
    if not rule_text(body):
        raise RuleError("the body is empty; a rule needs text to show at its moments and gates")
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
    return Rule(name, (name, digest), pointer, skills, commands, gates, stops, body)


def _read_rule_file(path):
    """The bytes of the regular file ``path``, read without following a symlink; RuleError otherwise.

    The type is checked with ``lstat`` and again on the open descriptor (``O_NOFOLLOW`` and
    ``O_NONBLOCK`` where the platform has them, so a swapped-in symlink or FIFO cannot redirect or block
    the read). At most ``RULE_FILE_LIMIT`` + 1 bytes are read.
    """
    mode = os.lstat(path).st_mode
    if stat.S_ISLNK(mode):
        raise RuleError("is a symlink; hooked rules are regular files")
    if not stat.S_ISREG(mode):
        raise RuleError("is not a regular file; hooked rules are regular files")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuleError("is not a regular file; hooked rules are regular files")
        chunks = []
        size = 0
        while size <= RULE_FILE_LIMIT:
            chunk = os.read(fd, RULE_FILE_LIMIT + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    finally:
        os.close(fd)
    if size > RULE_FILE_LIMIT:
        raise RuleError("is larger than %d bytes" % RULE_FILE_LIMIT)
    return b"".join(chunks)


def load_rules(root):
    """Return ``(rules sorted by name, [(path, message), ...])`` for ``root``'s hooked rules.

    Every ``*.md`` entry of the directory is a rule: one that is a symlink, not a regular file, larger
    than ``RULE_FILE_LIMIT`` bytes, unreadable or invalid is an error for its path.
    """
    directory = os.path.join(root, *RULES_DIR.split("/"))
    rules = []
    errors = []
    if not os.path.isdir(directory):
        return rules, errors
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(directory, name)
        relative = "%s/%s" % (RULES_DIR, name)
        try:
            raw = _read_rule_file(path)
            rules.append(parse_rule(name, raw.decode("utf-8")))
        except UnicodeDecodeError as error:
            errors.append((relative, "is not valid UTF-8: %s" % error))
        except (IOError, OSError) as error:
            errors.append((relative, "cannot be read: %s" % error))
        except RuleError as error:
            errors.append((relative, str(error)))
    return rules, errors


def render_pointer_block(rules):
    """The generated block, markers included: one line per rule and nothing else.

    Each rule's ``pointer`` says when to read its file, so the block needs no sentence. Every rule line starts with ``- `` so it can never equal a marker line, whatever text its
    pointer holds; the block is still checked, so a change here cannot break that silently.
    """
    lines = [POINTER_BEGIN]
    for rule in sorted(rules, key=lambda item: item.name):
        lines.append("- %s \u2014 %s/%s" % (rule.pointer, RULES_DIR, rule.name))
    lines.append(POINTER_END)
    for line in lines[1:-1]:
        if line in (POINTER_BEGIN, POINTER_END):
            raise RuleError("a pointer line must not equal a hooked-rules marker line")
    return "\n".join(lines)


LINE_BREAK_RE = re.compile(r"(\r\n|\r|\n)")


def _normalise(text):
    """``text`` with every line ending turned into LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _line_ending(text):
    """The first line ending of ``text``, the style a rewrite keeps; LF for a file without one."""
    match = LINE_BREAK_RE.search(text)
    return match.group(1) if match else "\n"


# What _locate_block found: where the begin marker line starts, where the end marker line ends
# (before its line break), whether the heading is the line right before the begin marker, and where
# the heading line starts when it is the last non-blank line before the marker (None otherwise).
BlockLocation = collections.namedtuple("BlockLocation", ["begin", "end", "heading_ok", "heading"])


def _locate_block(text):
    """Find the block in ``text`` by its standalone marker lines, or return None when absent.

    Lines end in LF, CRLF or a lone CR. A marker is recognised only when the whole line equals it,
    so marker text inside a pointer line is ignored. Raises ``RuleError`` for any other marker layout than
    one begin line followed by one end line.
    """
    pieces = LINE_BREAK_RE.split(text)  # line, break, line, break, ..., line
    lines = []
    position = 0
    for index in range(0, len(pieces), 2):
        lines.append((position, pieces[index]))
        position += len(pieces[index]) + (len(pieces[index + 1]) if index + 1 < len(pieces) else 0)
    begins = [i for i, (_, content) in enumerate(lines) if content == POINTER_BEGIN]
    ends = [i for i, (_, content) in enumerate(lines) if content == POINTER_END]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise RuleError(
            "%s must hold exactly one hooked-rules begin line followed by one end line" % AGENTS_FILE
        )
    previous = begins[0] - 1
    while previous >= 0 and not lines[previous][1].strip():
        previous -= 1
    heading = lines[previous][0] if previous >= 0 and lines[previous][1] == POINTER_HEADING else None
    heading_ok = heading is not None and previous == begins[0] - 1
    end_position, end_content = lines[ends[0]]
    return BlockLocation(
        lines[begins[0]][0], end_position + len(end_content), heading_ok, heading
    )


def _apply_block(text, block):
    """``text`` with the block replaced between its markers, or appended under its heading.

    A block that is already current right under its heading leaves the text untouched; blank lines
    between the heading and the begin marker are removed, and a missing heading is written back in
    front of the begin marker.
    """
    location = _locate_block(text)
    eol = _line_ending(text)
    if location is None:
        if not text or text.endswith(eol + eol):
            separator = ""
        elif text.endswith(eol):
            separator = eol
        else:
            separator = eol + eol
        return "%s%s%s%s%s%s" % (
            text, separator, POINTER_HEADING, eol, block.replace("\n", eol), eol,
        )
    if location.heading_ok and _normalise(text[location.begin:location.end]) == block:
        return text
    start = location.begin if location.heading is None else location.heading
    return text[:start] + POINTER_HEADING + eol + block.replace("\n", eol) + text[location.end:]


def _read_agents(path):
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _pointers(args):
    root = os.path.abspath(args.root)
    path = os.path.join(root, AGENTS_FILE)
    rules, errors = load_rules(root)
    if errors:
        for rule_path, message in errors:
            sys.stderr.write("%s: %s\n" % (rule_path, message))
        return 1
    try:
        current = _read_agents(path)
    except (IOError, OSError, UnicodeDecodeError) as error:
        sys.stderr.write("%s cannot be read: %s\n" % (AGENTS_FILE, error))
        return 1
    try:
        block = render_pointer_block(rules)
    except RuleError as error:
        sys.stderr.write("%s\n" % error)
        return 1
    if args.check:
        try:
            location = _locate_block(current)
        except RuleError as error:
            sys.stderr.write("%s\n" % error)
            return 1
        if location is None:
            sys.stderr.write(
                "%s has no hooked-rules block; run `agent_rules.py pointers --write`\n" % AGENTS_FILE
            )
            return 1
        if not location.heading_ok:
            sys.stderr.write(
                "%s lacks the `%s` heading on the line before the hooked-rules block; "
                "run `agent_rules.py pointers --write`\n" % (AGENTS_FILE, POINTER_HEADING)
            )
            return 1
        if _normalise(current[location.begin:location.end]) != block:
            sys.stderr.write(
                "the hooked-rules block in %s is stale; run `agent_rules.py pointers --write`\n"
                % AGENTS_FILE
            )
            return 1
        return 0
    try:
        updated = _apply_block(current, block)
    except RuleError as error:
        sys.stderr.write("%s\n" % error)
        return 1
    if updated != current:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(updated)
    return 0


EVENT_KINDS = ("tool-before", "tool-after", "prompt", "compacted", "stop", "void")
DECISION_KINDS = ("pass", "context", "deny", "block")
# Variables that select or relocate the repository; git honours them over ``-C``, so a host started from
# a git hook could otherwise point the engine at an unrelated checkout. GIT_CEILING_DIRECTORIES is kept:
# it can only make discovery fail, which passes silently.
GIT_SELECTION_VARIABLES = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX",
)
GIT_TIMEOUT_SECONDS = 5  # a runtime bound on the one git call, not test timing
# The first stderr line (LC_ALL=C) of a ``git rev-parse`` run outside a work tree: a pass, not a failure.
NON_CHECKOUT_DIAGNOSTICS = (
    "fatal: not a git repository",
    "fatal: this operation must be run in a work tree",
)

# Claude Code's hook_event_name for each engine event; ``void`` has no Claude Code form.
CLAUDE_EVENTS = {
    "PreToolUse": "tool-before",
    "PostToolUse": "tool-after",
    "UserPromptSubmit": "prompt",
    "PreCompact": "compacted",
    "Stop": "stop",
}
CLAUDE_NAMES = dict((kind, name) for name, kind in CLAUDE_EVENTS.items())


class EventError(Exception):
    """A hook event that is malformed or not one the engine handles."""


# One normalized hook event. Only the fields the engine needs are kept: a tool response, which can be
# megabytes, is never copied. ``write_ids`` is a list of ints on a ``void`` event and None otherwise;
# ``stop_hook_active`` is Claude Code's flag on a ``Stop`` and None everywhere else.
Event = collections.namedtuple(
    "Event",
    ["kind", "session", "agent", "cwd", "tool", "command", "ok", "skill", "path", "prompt",
     "write_ids", "stop_hook_active"],
)
Event.__new__.__defaults__ = (None,)  # a construction that predates stop_hook_active still works

# kind is one of DECISION_KINDS; entries is a list of (rule identity, write id).
Decision = collections.namedtuple("Decision", ["kind", "text", "entries"])


def _text(payload, key, required=False):
    value = payload.get(key)
    if value is None:
        if required:
            raise EventError("the event has no `%s`" % key)
        return None
    if not isinstance(value, str):
        raise EventError("`%s` must be a string" % key)
    if required and not value:
        raise EventError("`%s` must not be empty" % key)
    return value


def _flag(payload, key):
    """The boolean member ``key`` of ``payload``, or None; a non-boolean value is ignored."""
    value = payload.get(key)
    return value if isinstance(value, bool) else None


def _parse_claude(payload):
    name = payload.get("hook_event_name")
    kind = CLAUDE_EVENTS.get(name) if isinstance(name, str) else None
    if kind is None:
        raise EventError("unsupported Claude Code hook event %r" % (name,))
    tool = command = skill = path = prompt = ok = None
    if kind in ("tool-before", "tool-after"):
        tool = _text(payload, "tool_name", required=True)
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        if tool == "Bash":
            command = _text(tool_input, "command")
            if kind == "tool-after":
                ok = True  # a failing Bash call fires PostToolUseFailure instead (spike.md)
        elif kind == "tool-after" and tool == "Skill":
            skill = _text(tool_input, "skill")
        elif kind == "tool-after" and tool == "Read":
            path = _text(tool_input, "file_path")
    elif kind == "prompt":
        prompt = _text(payload, "prompt")
    return Event(
        kind,
        _text(payload, "session_id", required=True),
        _text(payload, "agent_id"),
        _text(payload, "cwd", required=True),
        tool, command, ok, skill, path, prompt, None,
        _flag(payload, "stop_hook_active") if kind == "stop" else None,
    )


def _parse_plain(payload):
    kind = payload.get("event")
    if kind not in EVENT_KINDS:
        raise EventError("`event` must be one of %s, got %r" % (", ".join(EVENT_KINDS), kind))
    ok = payload.get("ok")
    if ok is not None and not isinstance(ok, bool):
        raise EventError("`ok` must be a boolean")
    write_ids = payload.get("write_ids")
    if write_ids is not None or kind == "void":
        if not isinstance(write_ids, list) or not all(
            isinstance(item, int) and not isinstance(item, bool) for item in write_ids
        ):
            raise EventError("`write_ids` must be a list of integers")
        write_ids = list(write_ids)
    return Event(
        kind,
        _text(payload, "session", required=True),
        _text(payload, "agent"),
        _text(payload, "cwd", required=True),
        _text(payload, "tool"),
        _text(payload, "command"),
        ok,
        _text(payload, "skill"),
        _text(payload, "path"),
        _text(payload, "prompt"),
        write_ids,
        _flag(payload, "stop_hook_active") if kind == "stop" else None,
    )


def parse_event(fmt, payload):
    """Turn one decoded hook payload (``fmt`` is ``"claude"`` or ``"plain"``) into an ``Event``.

    Raises ``EventError`` for a payload the engine cannot handle; the caller fails open.
    """
    if fmt not in ("claude", "plain"):
        raise EventError("unknown event format %r" % (fmt,))
    if not isinstance(payload, dict):
        raise EventError("the event must be a JSON object")
    return _parse_claude(payload) if fmt == "claude" else _parse_plain(payload)


def render(fmt, event, decision):
    """The text the engine prints for ``decision``: ``""`` for a pass, JSON otherwise.

    A decision is never a permission grant: Claude Code's ``allow`` is never written. ``block`` is only
    valid for a ``stop`` event, where Claude Code reads ``{"decision": "block", "reason": ...}`` and the
    plain format carries the stop record's entry. Raises ``ValueError`` for a decision or format that
    has no form.
    """
    if decision.kind not in DECISION_KINDS:
        raise ValueError("unknown decision %r" % (decision.kind,))
    if fmt not in ("claude", "plain"):
        raise ValueError("unknown output format %r" % (fmt,))
    if decision.kind == "block" and event.kind != "stop":
        raise ValueError("a block is only valid for a stop event, not %r" % (event.kind,))
    if decision.kind == "pass":
        return ""
    if fmt == "plain":
        return json.dumps({
            "decision": decision.kind,
            "text": decision.text,
            "entries": [
                {"rule": [identity[0], identity[1]], "write_id": write_id}
                for identity, write_id in decision.entries
            ],
        })
    if decision.kind == "block":
        return json.dumps({"decision": "block", "reason": decision.text})
    if decision.kind == "deny":
        if event.kind != "tool-before":
            raise ValueError("Claude Code can only deny a tool-before event, not %r" % event.kind)
        return json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": decision.text,
        }})
    name = CLAUDE_NAMES.get(event.kind)
    if name is None or event.kind == "compacted":
        return ""  # Claude Code has no context channel for a compaction, and no void event
    return json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": decision.text}})


class CheckoutError(Exception):
    """Checkout discovery that failed for a reason other than "not a checkout"."""


def discover_checkout(cwd):
    """``(toplevel, common_dir)`` of the checkout holding ``cwd``; None only for a genuine non-checkout.

    One ``git rev-parse`` answers; ``common_dir`` is absolute, resolved against ``cwd`` when git prints
    it relative. None means pass silently: git reports "not a git repository" or the top level has no
    ``.agents/hooked-rules/`` directory. Only the first line of git's stderr is classified, anchored at
    its start (``NON_CHECKOUT_DIAGNOSTICS``), so a quoted path cannot pass for a diagnostic.
    ``CheckoutError`` for anything else: an invalid ``cwd`` (``fatal: cannot change to``), git
    missing or timing out, any other git failure or unexpected output. git runs under ``LC_ALL=C`` so
    its message is not translated.
    """
    if not isinstance(cwd, str) or not cwd or "\0" in cwd:
        raise CheckoutError("invalid cwd %r" % (cwd,))
    environment = dict(
        (name, value) for name, value in os.environ.items() if name not in GIT_SELECTION_VARIABLES
    )
    environment["LC_ALL"] = "C"
    environment.pop("LANGUAGE", None)
    try:
        completed = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel", "--git-common-dir"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise CheckoutError("git could not run: %s: %s" % (type(error).__name__, error))
    if completed.returncode != 0:
        diagnostic = (completed.stderr or b"").decode("utf-8", "replace")
        first_line = diagnostic.splitlines()[0] if diagnostic else ""
        if first_line.startswith(NON_CHECKOUT_DIAGNOSTICS):
            return None
        raise CheckoutError(
            "git rev-parse failed with exit %d: %s"
            % (completed.returncode, (completed.stderr or b"").decode("utf-8", "replace").strip())
        )
    try:
        lines = completed.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise CheckoutError("git printed paths that are not UTF-8")
    if len(lines) != 2 or not lines[0] or not lines[1]:
        raise CheckoutError("unexpected git rev-parse output %r" % (lines,))
    toplevel = lines[0]
    common_dir = os.path.normpath(os.path.join(os.path.abspath(cwd), lines[1]))
    if not os.path.isdir(os.path.join(toplevel, *RULES_DIR.split("/"))):
        return None
    return toplevel, common_dir


def checkout_for(cwd):
    """``discover_checkout``, with every failure read as None (pass silently)."""
    try:
        return discover_checkout(cwd)
    except CheckoutError:
        return None


STATE_VERSION = 2
KEEP_SESSIONS = 50
KEEP_CHANGES = 20  # the session change set keeps this many names, newest last
KEEP_STOPS = 40  # the session stop records keep this many, oldest first
LOCK_RETRY_SECONDS = 0.1
ENTRY_STATES = ("delivered", "pending")
STOP_VERIFY_STATES = ("none", "pass")
# A change name as openspec writes it: the bound keeps every name in a state file a plain token.
CHANGE_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
# The `--change` argument of an openspec command: `--change demo`, `--change=demo` and both quoted
# forms. The lookahead refuses a longer name rather than taking a 64-character prefix of it.
CHANGE_ARG_RE = re.compile(r"--change(?:=|\s+)[\"']?([a-z0-9][a-z0-9-]{0,63})(?![a-z0-9-])")
# A command that runs openspec at all; only its successful calls record a change name.
OPENSPEC_COMMAND_RE = re.compile(r"\bopenspec\b")
INITIALIZED_MARKER = "initialized"
STEM_RE = re.compile(r"[0-9a-f]{64}")
SESSION_FILE_RE = re.compile(r"[0-9a-f]{64}\.json")


class StoreError(Exception):
    """A delivery-state store that cannot be used safely: the caller passes without delivering."""


class LockTimeout(StoreError):
    """A lock that stayed busy for every bounded, non-blocking try."""


class FileLock(object):
    """An exclusive lock on one file, taken by non-blocking tries separated by ``sleep``.

    ``attempts`` tries are made; ``sleep(LOCK_RETRY_SECONDS)`` runs between two tries, never after the
    last one, and ``LockTimeout`` follows the last failed try. POSIX uses ``fcntl.flock``; Windows uses
    ``msvcrt.locking`` on the first byte (a lock past the end of an empty file is allowed there).
    """

    def __init__(self, path, sleep=time.sleep, attempts=50):
        self.path = path
        self.sleep = sleep
        self.attempts = max(1, attempts)
        self.fd = None

    def _try(self):
        """The open descriptor when the lock is taken, None when it is busy or the file was replaced."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(fd)
            return None
        if fcntl is not None:
            # A pruner may have unlinked the file between our open and our lock; a lock on an
            # unlinked file excludes nobody, so take it again on the file that is now there.
            try:
                same = os.path.samestat(os.fstat(fd), os.stat(self.path))
            except OSError:
                same = False
            if not same:
                os.close(fd)
                return None
        return fd

    def __enter__(self):
        for attempt in range(self.attempts):
            if attempt:
                self.sleep(LOCK_RETRY_SECONDS)
            fd = self._try()
            if fd is not None:
                self.fd = fd
                return self
        raise LockTimeout(self.path)

    def __exit__(self, *exc_info):
        fd, self.fd = self.fd, None
        if fd is None:
            return False
        try:
            if fcntl is None:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            os.close(fd)  # closing also drops a flock
        return False


def _atomic_write(path, text):
    """Write ``text`` to ``path`` through a temporary file in the same directory and ``os.replace``."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="tmp-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _no_duplicates(pairs):
    """``object_pairs_hook`` that refuses a repeated member name, which json would resolve silently."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate member %r" % (key,))
        result[key] = value
    return result


def _read_json(path):
    """The JSON object in ``path``; None only when the file does not exist, StoreError otherwise.

    Duplicate object members, bad UTF-8, invalid JSON and nesting too deep for the decoder all raise
    StoreError.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle, object_pairs_hook=_no_duplicates)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError) as error:
        raise StoreError("cannot read %s: %s" % (path, error))
    if not isinstance(data, dict):
        raise StoreError("%s does not hold a JSON object" % path)
    return data


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _change_names(value):
    """A version 2 file's change set: distinct change names, newest last; StoreError otherwise."""
    if not isinstance(value, list) or not all(
        isinstance(name, str) and CHANGE_NAME_RE.fullmatch(name) for name in value
    ):
        raise StoreError("session state has an invalid change set")
    if len(set(value)) != len(value):
        raise StoreError("session state change set names a change twice")
    return list(value)


def _stop_records(value):
    """A version 2 file's stop records, oldest first; StoreError for an invalid record."""
    if not isinstance(value, list):
        raise StoreError("session state has no stops list")
    records = []
    for item in value:
        if not isinstance(item, dict):
            raise StoreError("a stop record is not an object")
        change = item.get("change")
        iteration = _count(item.get("iteration"))
        verify_state = item.get("verify_state")
        write_id = _count(item.get("write_id"))
        if not isinstance(change, str) or CHANGE_NAME_RE.fullmatch(change) is None:
            raise StoreError("a stop record has an invalid change name")
        if iteration is None or write_id is None:
            raise StoreError("a stop record has an invalid iteration or write id")
        if verify_state not in STOP_VERIFY_STATES:
            raise StoreError("a stop record has an invalid verify state")
        records.append({
            "change": change, "iteration": iteration,
            "verify_state": verify_state, "write_id": write_id,
        })
    return records


class SessionState(object):
    """One session's delivery state, read and written only inside ``Store.transaction``.

    ``entries`` maps ``(key, rule identity)`` to
    ``{"state", "patterns", "triggers", "generation", "write_id"}``. A key is ``session`` for the main
    context and ``"session/agent"`` for a subagent; each key has its own generation, 0 until bumped.
    ``changes`` (newest last) and ``stops`` (oldest first) are session-wide, not per key.
    """

    def __init__(self, store, session, generations, entries, changes=None, stops=None):
        self.store = store
        self.session = session
        self.generations = generations
        self.entries = entries
        self.changes = [] if changes is None else changes
        self.stops = [] if stops is None else stops
        self.wrote = False  # set by ``Store.transaction`` once it published a changed state

    @classmethod
    def load(cls, store, session, data):
        """The state held in ``data`` (None for a session never written); StoreError when invalid.

        A version 1 file is a version 2 file whose entries have no triggers and whose change set and
        stop records are empty.
        """
        generations = {}
        entries = {}
        changes = []
        stops = []
        if data is not None:
            version = data.get("version")
            if type(version) is not int or version not in (1, STATE_VERSION):
                raise StoreError("unsupported session state version %r" % (version,))
            if data.get("session") != session:
                raise StoreError("the stored state belongs to session %r" % (data.get("session"),))
            raw, items = data.get("generations"), data.get("entries")
            if not isinstance(raw, dict) or not isinstance(items, list):
                raise StoreError("session state has no generations object and entries list")
            for key, value in raw.items():
                if _count(value) is None:
                    raise StoreError("invalid generation for %r" % (key,))
                generations[key] = value
            for item in items:
                identity, entry = cls._entry(item, version)
                if identity in entries:
                    raise StoreError("duplicate entry %r" % (identity,))
                entries[identity] = entry
            if version > 1:
                changes = _change_names(data.get("changes"))
                stops = _stop_records(data.get("stops"))
        return cls(store, session, generations, entries, changes, stops)

    @staticmethod
    def _entry(item, version):
        if not isinstance(item, dict):
            raise StoreError("a state entry is not an object")
        key, name, digest = item.get("key"), item.get("name"), item.get("sha256")
        patterns = item.get("patterns")
        if not (isinstance(key, str) and isinstance(name, str) and isinstance(digest, str)):
            raise StoreError("a state entry has an invalid key or rule identity")
        if item.get("state") not in ENTRY_STATES:
            raise StoreError("a state entry has an invalid state")
        if not isinstance(patterns, list) or not all(isinstance(pattern, str) for pattern in patterns):
            raise StoreError("a state entry has invalid patterns")
        triggers = item.get("triggers", [])
        if not isinstance(triggers, list) or not all(
            isinstance(trigger, str) for trigger in triggers
        ):
            raise StoreError("a state entry has invalid triggers")
        if version > 1 and "triggers" not in item:
            raise StoreError("a version 2 state entry has no triggers")
        generation, write_id = _count(item.get("generation")), _count(item.get("write_id"))
        if generation is None or write_id is None:
            raise StoreError("a state entry has an invalid generation or write id")
        return (key, (name, digest)), {
            "state": item["state"],
            "patterns": set(patterns),
            "triggers": set(triggers),
            "generation": generation,
            "write_id": write_id,
        }

    def dump(self):
        entries = []
        for (key, (name, digest)), entry in sorted(self.entries.items()):
            entries.append({
                "key": key,
                "name": name,
                "sha256": digest,
                "state": entry["state"],
                "patterns": sorted(entry["patterns"]),
                "triggers": sorted(entry["triggers"]),
                "generation": entry["generation"],
                "write_id": entry["write_id"],
            })
        return json.dumps({
            "version": STATE_VERSION,
            "session": self.session,
            "generations": self.generations,
            "changes": list(self.changes),
            "stops": [dict(record) for record in self.stops],
            "entries": entries,
        }, sort_keys=True, indent=1) + "\n"

    def generation(self, key):
        return self.generations.get(key, 0)

    def bump(self, key):
        """Void every entry of ``key`` and no other key; the change set and stop records stay."""
        self.generations[key] = self.generation(key) + 1

    def bump_all(self):
        """Bump the main key and every subagent key this session has used."""
        keys = set(self.generations) | set(entry_key for entry_key, _ in self.entries)
        keys.add(self.session)
        for key in keys:
            self.bump(key)

    def put(self, key, identity, state, patterns, triggers=()):
        """Record ``state`` for ``(key, identity)`` at the key's generation, with a fresh write id.

        An entry of the same generation keeps its patterns and triggers and gains the new ones; an
        entry of an older generation (voided by a re-arm) is replaced, not merged.
        """
        if state not in ENTRY_STATES:
            raise ValueError("unknown entry state %r" % (state,))
        identity = (key, tuple(identity))
        generation = self.generation(key)
        entry = self.entries.get(identity)
        if entry is not None and entry["generation"] == generation:
            patterns = entry["patterns"] | set(patterns)
            triggers = entry["triggers"] | set(triggers)
        else:
            patterns, triggers = set(patterns), set(triggers)
        self.entries[identity] = {
            "state": state,
            "patterns": patterns,
            "triggers": triggers,
            "generation": generation,
            "write_id": self.store.next_write_id(),
        }


class Store(object):
    """The per-session JSON state under ``state_dir``, with a store-level index.

    Files: ``<stem>.json`` and ``<stem>.lock`` per session (``stem`` is the sha256 of the strictly
    UTF-8 encoded session id, so no session id can name another path or share a file with another id),
    ``index.json`` / ``index.lock``, and an ``initialized`` marker. The index holds the write-id
    counter and a recency counter that orders the sessions for pruning; no clock is read.

    Failure policy: a missing session file is a fresh session; anything else wrong with the state, and
    a missing or invalid index in a store that was initialised before, raises ``StoreError`` and
    allocates nothing, so counters can never restart. The remedy is to remove the ``agent-rules``
    directory (delivery state only; the next event starts afresh). Stray ``tmp-*.tmp`` files left by a
    crash are harmless and may be removed offline; the store never sweeps them.

    Errors: ``StoreError`` (including ``LockTimeout``) is raised by ``transaction`` on entry (an invalid
    session id, a busy lock, invalid stored state) and on commit (index or publish failures that are
    store errors), by ``SessionState.put`` through ``next_write_id``, by ``next_write_id`` itself,
    by ``prune`` when the index lock stays busy or the index is invalid, and by ``session_path`` and
    ``session_lock_path`` for an invalid id. Callers must therefore guard the whole transaction and
    the prune that follows it. Raw ``OSError`` from opening a lock file or an atomic write is not
    wrapped, so callers fail open on any ``Exception``.

    Lock order: a session lock may be held while taking the index lock, never the other way round, and
    no code path acquires a session lock while holding the index lock. The index lock is only held for
    one short read-modify-write.
    """

    def __init__(self, state_dir, sleep=time.sleep, lock_attempts=50):
        self.state_dir = state_dir
        self.sleep = sleep
        self.lock_attempts = lock_attempts

    @staticmethod
    def _stem(session):
        if not isinstance(session, str):
            raise StoreError("a session id must be a string")
        try:
            raw = session.encode("utf-8")
        except UnicodeEncodeError:
            raise StoreError("a session id that cannot be encoded as UTF-8")
        return hashlib.sha256(raw).hexdigest()

    def session_path(self, session):
        return os.path.join(self.state_dir, self._stem(session) + ".json")

    def session_lock_path(self, session):
        return os.path.join(self.state_dir, self._stem(session) + ".lock")

    def index_path(self):
        return os.path.join(self.state_dir, "index.json")

    def index_lock_path(self):
        return os.path.join(self.state_dir, "index.lock")

    def _lock(self, path):
        return FileLock(path, self.sleep, self.lock_attempts)

    def victim_lock(self, stem):
        """A lock on a prune victim (named by its file stem) that fails at once when busy."""
        return FileLock(os.path.join(self.state_dir, stem + ".lock"), self.sleep, 1)

    def _marker_present(self):
        """Whether the marker file exists; only FileNotFoundError means absent, other errors raise."""
        try:
            os.stat(os.path.join(self.state_dir, INITIALIZED_MARKER))
        except FileNotFoundError:
            return False
        except OSError as error:
            raise StoreError("cannot inspect the delivery-state marker in %s: %s" % (self.state_dir, error))
        return True

    def _initialized(self):
        """Whether this store was used before: the marker, or any session file, exists.

        The marker counts when it stats or when the directory listing names it. StoreError when that
        cannot be checked, rather than assuming a fresh store.
        """
        if self._marker_present():
            return True
        try:
            names = os.listdir(self.state_dir)
        except FileNotFoundError:
            return False
        except OSError as error:
            raise StoreError("cannot inspect the delivery-state directory %s: %s" % (self.state_dir, error))
        return any(name == INITIALIZED_MARKER or SESSION_FILE_RE.fullmatch(name) for name in names)

    def _write_marker(self):
        _atomic_write(os.path.join(self.state_dir, INITIALIZED_MARKER), "initialized\n")

    def _load_index(self, create):
        """The validated index; call with the index lock held.

        A brand-new store (no marker, no session file) yields a fresh index; with ``create`` the
        zero-counter index is published first and the marker second, both before any id is issued, so
        an interruption between them heals on the next call. A valid index that lacks its marker gets
        it with ``create``. A missing index in a used store, or an invalid one, raises StoreError.
        """
        data = _read_json(self.index_path())
        if data is None:
            if self._initialized():
                raise StoreError(
                    "the delivery-state index %s is missing; remove the agent-rules directory "
                    "%s to start afresh" % (self.index_path(), self.state_dir)
                )
            fresh = {"write_id": 0, "order": 0, "sessions": {}}
            if create:
                self._write_index(fresh)
                self._write_marker()
            return fresh
        write_id, order, raw = _count(data.get("write_id")), _count(data.get("order")), data.get("sessions")
        valid = write_id is not None and order is not None and isinstance(raw, dict)
        if valid:
            valid = all(
                isinstance(stem, str) and STEM_RE.fullmatch(stem) and _count(value) is not None
                for stem, value in raw.items()
            )
        if not valid:
            raise StoreError(
                "the delivery-state index %s is invalid; remove the agent-rules directory %s to "
                "start afresh" % (self.index_path(), self.state_dir)
            )
        if create and not self._marker_present():
            self._write_marker()
        return {"write_id": write_id, "order": order, "sessions": dict(raw)}

    def _write_index(self, index):
        _atomic_write(self.index_path(), json.dumps(index, sort_keys=True, indent=1) + "\n")

    def next_write_id(self):
        """The next write id; the counter lives in the index and is never reused."""
        with self._lock(self.index_lock_path()):
            index = self._load_index(create=True)
            index["write_id"] += 1
            self._write_index(index)
            return index["write_id"]

    def _touch(self, session):
        """Register ``session`` in the index as the most recently written one."""
        stem = self._stem(session)
        with self._lock(self.index_lock_path()):
            index = self._load_index(create=True)
            index["order"] += 1
            index["sessions"][stem] = index["order"]
            self._write_index(index)

    def _publish(self, session, text):
        _atomic_write(self.session_path(session), text)

    @contextlib.contextmanager
    def transaction(self, session):
        """Hold the session lock, yield its ``SessionState`` and write it atomically when it changed.

        A body that raises writes nothing. ``LockTimeout`` is raised, with the body not run, when the lock
        stays busy for ``lock_attempts`` tries; ``StoreError`` when the stored state is invalid. The
        index registers the session before the file is published, so a published file is always
        indexed; an index entry without a file is harmless. ``state.wrote`` tells the caller afterwards
        whether the state was published.
        """
        self._stem(session)
        with self._lock(self.session_lock_path(session)):
            state = SessionState.load(self, session, _read_json(self.session_path(session)))
            before = state.dump()
            yield state
            after = state.dump()
            if after != before:
                self._touch(session)
                self._publish(session, after)
                state.wrote = True

    @staticmethod
    def _victims(sessions, current):
        """The stems outside the newest 50 by index order, oldest first, minus the current session.

        Only sessions outside the newest 50 are ever victims: skipping the current one (or, later, a
        busy one) leaves a temporary surplus rather than evicting one of the newest 50 instead.
        """
        surplus = len(sessions) - KEEP_SESSIONS
        if surplus <= 0:
            return []
        oldest_first = sorted(sessions, key=lambda stem: sessions[stem])
        return [stem for stem in oldest_first[:surplus] if stem != current]

    def prune(self, current_session):
        """Keep the newest 50 sessions by index order; never the current one; skip busy ones.

        Call it after a session transaction that wrote (``state.wrote``), once that transaction has
        released its lock. A first look at the index plans the victims. Each victim lock is then tried
        once, without waiting, and with only that lock held the index is re-read under its lock and the
        victim re-checked, so two concurrent prunes cannot together remove more than the surplus.
        Orphan lock files are removed last (``_prune_orphan_lock``).
        """
        current = self._stem(current_session)
        with self._lock(self.index_lock_path()):
            planned = self._victims(self._load_index(create=False)["sessions"], current)
        for stem in planned:
            self._prune_one(stem, current)
        for stem in self._orphan_lock_stems(current):
            self._prune_orphan_lock(stem)

    def _orphan_lock_stems(self, current):
        """Stems of ``<stem>.lock`` files whose session the index does not hold, the current one excepted.

        A pass that writes nothing still creates its session's lock file, and on Windows a pruned
        victim's lock file stays, so these are swept here. The candidates are only planned: each one is
        re-checked under its own lock.
        """
        try:
            names = os.listdir(self.state_dir)
        except OSError:
            return []
        with self._lock(self.index_lock_path()):
            indexed = self._load_index(create=False)["sessions"]
        stems = []
        for name in sorted(names):
            stem = name[:-len(".lock")] if name.endswith(".lock") else None
            if stem and STEM_RE.fullmatch(stem) and stem != current and stem not in indexed:
                stems.append(stem)
        return stems

    def _prune_orphan_lock(self, stem):
        """Remove one orphan lock file, best effort; a busy lock (a session in use) stays.

        The lock is tried once without waiting and with no index lock held; then the index is re-read
        under its lock, and the file is removed only if the session is still not indexed. POSIX removes
        it while holding the lock (``FileLock`` re-checks the inode); Windows cannot delete an open
        file, so it is removed after the release, and a removal that fails because another process has
        just opened it leaves it in place.
        """
        path = os.path.join(self.state_dir, stem + ".lock")
        removable = False
        try:
            with self.victim_lock(stem):
                with self._lock(self.index_lock_path()):
                    removable = stem not in self._load_index(create=False)["sessions"]
                if removable and fcntl is not None:
                    os.unlink(path)
                    return
        except (LockTimeout, OSError):
            return
        if removable:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _prune_one(self, stem, current):
        """Prune one victim, best effort: busy or failing victims stay where they are.

        A busy victim lock (LockTimeout), or an OSError before the session file is gone (taking the
        lock, removing the file), leaves the victim's file and its index record in place. An OSError
        after the file is gone (writing the index) leaves a stale index record without a file, which is
        harmless and is pruned on a later pass. StoreError from the index lock or contents propagates.
        """
        with contextlib.ExitStack() as stack:
            try:
                lock = stack.enter_context(self.victim_lock(stem))
            except (LockTimeout, OSError):
                return
            try:
                with self._lock(self.index_lock_path()):
                    index = self._load_index(create=False)
                    if stem not in self._victims(index["sessions"], current):
                        return
                    try:
                        os.unlink(os.path.join(self.state_dir, stem + ".json"))
                    except FileNotFoundError:
                        pass
                    del index["sessions"][stem]
                    self._write_index(index)
                if fcntl is not None:
                    # POSIX only: Windows cannot delete an open file. FileLock re-checks the inode.
                    try:
                        os.unlink(lock.path)
                    except OSError:
                        pass
            except OSError:
                return


CONTEXT_CAP = 9000  # UTF-8 bytes of the emitted text, below Claude Code's 10,000-character context limit
RULE_SEPARATOR = "\n\n"  # one blank line between two rule texts


def context_key(event):
    """The delivery-state key of ``event``'s context: the session, or ``session/agent`` for a subagent."""
    if event.agent:
        return "%s/%s" % (event.session, event.agent)
    return event.session


def _live(state, key, identity):
    """The entry for ``(key, identity)``, or None when absent or voided by a later re-arm."""
    entry = state.entries.get((key, tuple(identity)))
    if entry is None or entry["generation"] < state.generation(key):
        return None
    return entry


# The fixed `opsx:` skill table: an `opsx:<x>` command runs the `openspec-<x>-change` skill, except
# for the two skills that do not follow that shape. It covers the skills the rules list; no rule
# lists `opsx:sync`, whose skill is `openspec-sync-specs`, so the generic form is never used for it.
SKILL_ALIASES = {
    "opsx:propose": "openspec-propose",
    "opsx:explore": "openspec-explore",
}


def canonical_skill(name):
    """The canonical skill name of ``name``: the alias table, else the generic ``opsx:<x>`` form."""
    if name in SKILL_ALIASES:
        return SKILL_ALIASES[name]
    if name.startswith("opsx:"):
        return "openspec-%s-change" % name[len("opsx:"):]
    return name


def _skill_dirs(rule):
    """The skill directory names a rule answers to: each skill, and its part after the last ``:``."""
    return set(rule.skills) | set(skill.rsplit(":", 1)[-1] for skill in rule.skills)


def _prompt_skill(prompt):
    """The ``<skill>`` of a prompt whose first word is ``/<skill>``, or None."""
    if not prompt:
        return None
    words = prompt.split(None, 1)
    if not words or not words[0].startswith("/") or len(words[0]) < 2:
        return None
    return words[0][1:]


def _read_skill_dirs(event):
    """The ``<name>`` when the read path's real path ends in ``/skills/<name>/SKILL.md``, else nothing.

    Only the resolved path counts, so a lookalike path that resolves elsewhere earns no credit. A
    relative path is joined to ``cwd``; backslashes become slashes first, for Windows-style paths.
    """
    try:
        base = event.cwd if isinstance(event.cwd, str) else ""
        real = os.path.realpath(os.path.join(base, event.path.replace("\\", "/")))
    except (OSError, ValueError):
        return set()
    parts = real.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[-1] == "SKILL.md" and parts[-3] == "skills" and parts[-2]:
        return set([parts[-2]])
    return set()


def _moment_rules(rules, event):
    """``[(rule, triggers)]`` for the rules whose moment ``event`` is.

    The triggers are the ones this event matched, as a frozenset of ``"skill:<canonical name>"`` and
    ``"command:<pattern source>"``: a skill tool call, a ``/<skill>`` prompt, a SKILL.md read, or a
    successful command.
    """
    if event.kind == "prompt":
        skill = _prompt_skill(event.prompt)
        if skill is None:
            return []
        trigger = "skill:" + canonical_skill(skill)
        return [(rule, frozenset([trigger])) for rule in rules if skill in rule.skills]
    if event.kind != "tool-after":
        return []
    matched = []
    read_dirs = _read_skill_dirs(event) if event.path else set()
    for rule in rules:
        triggers = set()
        if event.skill is not None and event.skill in rule.skills:
            triggers.add("skill:" + canonical_skill(event.skill))
        triggers.update("skill:" + directory for directory in read_dirs & _skill_dirs(rule))
        if event.command is not None and event.ok is True:
            triggers.update(
                "command:" + pattern.pattern
                for pattern in rule.commands
                if pattern.search(event.command)
            )
        if triggers:
            matched.append((rule, frozenset(triggers)))
    return matched


def _utf8_size(text):
    return len(text.encode("utf-8", "surrogatepass"))


def _capped(rules, prefix=""):
    """``(included rules, text)``: rules in name order, each whole, the text at most CONTEXT_CAP bytes.

    Each rule contributes ``rule_text(rule.body)`` (never empty: ``parse_rule`` rejects a blank body),
    and the texts are joined with one blank line (``RULE_SEPARATOR``). ``prefix`` (the stop gate's
    engine line) comes first, counts against the cap, and is followed by the same separator. The cap
    counts the UTF-8 bytes of exactly the text returned. A rule that does not fit is left out and the
    next one is still tried.
    """
    included = []
    parts = []
    size = _utf8_size(prefix)
    for rule in sorted(rules, key=lambda item: item.name):
        part = rule_text(rule.body)
        if not part:
            continue  # unreachable for a parsed rule; a rule with no text is never credited
        separator = RULE_SEPARATOR if parts or prefix else ""
        added = _utf8_size(separator) + _utf8_size(part)
        if size + added > CONTEXT_CAP:
            continue
        included.append(rule)
        parts.append(part)
        size += added
    joined = prefix + (RULE_SEPARATOR if prefix and parts else "") + RULE_SEPARATOR.join(parts)
    return included, joined


def _recorded(state, key, rules):
    return [(rule.identity, state.entries[(key, rule.identity)]["write_id"]) for rule in rules]


def _deliver_moment(state, key, rule, matched):
    """Record ``rule`` as delivered for this context, keeping its gate patterns and adding triggers."""
    entry = _live(state, key, rule.identity)
    patterns = entry["patterns"] if entry is not None else set()
    state.put(key, rule.identity, "delivered", patterns, matched)


def record_changes(state, event):
    """Add the change names of a successful ``openspec`` shell call to the session change set.

    The set is session-wide, not per context, so a subagent's call counts too. Each name moves to the
    newest end (an already recorded name included) and only the ``KEEP_CHANGES`` newest are kept.
    An event that is not a successful ``openspec`` command records nothing.
    """
    if event.kind != "tool-after" or event.ok is not True or not event.command:
        return
    if OPENSPEC_COMMAND_RE.search(event.command) is None:
        return
    for name in CHANGE_ARG_RE.findall(event.command):
        if name in state.changes:
            state.changes.remove(name)
        state.changes.append(name)
    if len(state.changes) > KEEP_CHANGES:
        del state.changes[:len(state.changes) - KEEP_CHANGES]


# Stop predicates
#
# A rule's `stops` entries name predicates the stop gate asks about the checkout. This section holds
# the one the registry has, `superspec-finalize-pending`, which answers whether a named change has an
# apply receipt and no finalize receipt. It reads receipts as plain files: no subprocess and no clock.

CHANGES_DIR = ("openspec", "changes")  # where openspec keeps a change's receipts and its archive
RECEIPT_LIMIT = 65536  # bytes read from one receipt; a larger receipt makes its change not pending
WORKTREE_LIMIT = 32  # linked worktrees visited, in `gitdir` file-name order (sorted)
ARCHIVE_PREFIX_RE = re.compile(r"\d{4}-\d{2}-\d{2}-")
ITERATION_RE = re.compile(r"^\*\*Iteration\*\*:\s*`?(\d+)`?", re.M)
FAIL_TICKED_RE = re.compile(r"^- \[[xX]\] ❌ FAIL", re.M)
PASS_TICKED_RE = re.compile(r"^- \[[xX]\] (✅ PASS|⚠\ufe0f? PASS WITH WARNINGS)", re.M)

# One change the session named that waits for finalize: the newest apply iteration and the verify
# state at that iteration, `"none"` or `"pass"` (an entry of ``STOP_VERIFY_STATES``).
PendingChange = collections.namedtuple("PendingChange", ["name", "iteration", "verify_state"])


class _OversizedReceipt(Exception):
    """A receipt larger than ``RECEIPT_LIMIT``: its change is not pending, silently."""


def _core_bare(config_path):
    """Whether the repository config at ``config_path`` sets ``bare`` true in its ``[core]`` section.

    Git's config syntax is only skimmed for the one value: a ``bare`` key is true when it is written
    as ``true``, ``yes``, ``on`` or ``1``, or with no value at all. An unreadable config counts as not
    bare, which can only add a main-tree root that ``change_roots`` skips when it does not exist.
    """
    try:
        with open(config_path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return False
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and "]" in stripped:
            header = stripped[1:stripped.index("]")].split(None, 1)
            section = header[0].strip('"').lower() if header else None
            continue
        if section != "core":
            continue
        key, separator, value = stripped.partition("=")
        if key.strip().lower() != "bare":
            continue
        if not separator:
            return True
        value = re.split(r"[#;]", value, maxsplit=1)[0].strip().strip('"').lower()
        return value in ("true", "yes", "on", "1")
    return False


def _main_working_tree(common_dir):
    """The main working tree that shares ``common_dir``; None for a bare repository or a non-``.git``.

    A non-bare repository's common directory is its main working tree's ``.git`` directory, so the main
    tree is its parent. A bare repository's common directory is the repository itself and has no tree.
    """
    if not isinstance(common_dir, str) or not common_dir:
        return None
    normalised = os.path.normpath(common_dir)
    if os.path.basename(normalised) != ".git":
        return None
    if _core_bare(os.path.join(normalised, "config")):
        return None
    return os.path.dirname(normalised)


def _linked_worktree_roots(common_dir):
    """The linked worktrees of ``common_dir``, in ``gitdir`` file-name order, at most 32.

    Git links a worktree by writing ``<common-dir>/worktrees/<name>/gitdir`` with the path of the
    worktree's own ``.git`` file; the worktree root is the parent of that path. An entry without a
    ``gitdir`` file is not a worktree and is skipped. A directory or link that cannot be read raises
    ``StoreError``: the predicate cannot then answer, and the stop fails open with the notice.
    """
    directory = os.path.join(common_dir, "worktrees")
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        return []
    except OSError as error:
        raise StoreError("cannot read the worktree directory %s: %s" % (directory, error))
    roots = []
    for name in names[:WORKTREE_LIMIT]:
        link = os.path.join(directory, name, "gitdir")
        try:
            with open(link, "r", encoding="utf-8") as handle:
                target = handle.read().strip()
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError) as error:
            raise StoreError("cannot read the worktree link %s: %s" % (link, error))
        if not target:
            continue
        if not os.path.isabs(target):
            target = os.path.join(os.path.dirname(link), target)
        roots.append(os.path.dirname(target))
    return roots


def change_roots(toplevel, common_dir):
    """The checkouts to search for a change's receipts, in visit order, as unique real paths.

    The event's checkout (``toplevel``), then the main working tree of ``common_dir`` (unless the
    repository is bare), then the common directory's linked worktrees, each root read from its
    ``gitdir`` file (at most ``WORKTREE_LIMIT``). A candidate that is not an existing directory is
    skipped, and one real path is visited once however many names reach it, so a plain checkout is
    its ``toplevel`` alone.
    """
    candidates = [toplevel]
    main = _main_working_tree(common_dir)
    if main is not None:
        candidates.append(main)
    candidates.extend(_linked_worktree_roots(common_dir))
    roots = []
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate:
            continue
        try:
            real = os.path.realpath(candidate)
            if not os.path.isdir(real):
                continue
        except (OSError, ValueError):
            continue
        if real not in seen:
            seen.add(real)
            roots.append(real)
    return roots


def _receipt_text(path):
    """The text of the receipt at ``path``, or None when there is no such file.

    At most ``RECEIPT_LIMIT`` + 1 bytes are read, opening non-blocking where the platform allows it so
    a FIFO in a change directory cannot hold the hook. ``_OversizedReceipt`` for a larger file, and
    ``StoreError`` when the file cannot be read as UTF-8 text, so a damaged checkout fails open.
    """
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StoreError("cannot read the receipt %s: %s" % (path, error))
    try:
        chunks = []
        size = 0
        while size <= RECEIPT_LIMIT:
            chunk = os.read(fd, RECEIPT_LIMIT + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError as error:
        raise StoreError("cannot read the receipt %s: %s" % (path, error))
    finally:
        os.close(fd)
    if size > RECEIPT_LIMIT:
        raise _OversizedReceipt(path)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as error:
        raise StoreError("cannot read the receipt %s: %s" % (path, error))


def _iteration(text):
    """The ``**Iteration**`` number of a receipt; 0 when the line is missing or unparsable."""
    match = ITERATION_RE.search(text) if text else None
    if match is None:
        return 0
    try:
        return int(match.group(1))
    except ValueError:  # a digit run too long for int(), i.e. unparsable
        return 0


def _archived(root, name):
    """Whether ``root`` holds the directory ``openspec/changes/archive/<YYYY-MM-DD>-<name>/``.

    The name after the date must be exactly ``name``, so archiving `hook-stop-gate` does not clear
    `stop-gate`.
    """
    directory = os.path.join(root, *(CHANGES_DIR + ("archive",)))
    try:
        entries = os.listdir(directory)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise StoreError("cannot read the change archive %s: %s" % (directory, error))
    for entry in entries:
        prefix = ARCHIVE_PREFIX_RE.match(entry)
        if prefix is not None and entry[prefix.end():] == name:
            if os.path.isdir(os.path.join(directory, entry)):
                return True
    return False


def _change_pending(roots, name):
    """The ``PendingChange`` for one change, or None when it is not pending.

    Every root is searched, because a finalize receipt or an archived copy anywhere clears the change:
    the event's checkout may see a change another worktree finalized. The newest apply receipt is the
    highest iteration, the first root on a tie, and only its neighbouring ``verify.md`` decides
    whether the change failed verification. ``_OversizedReceipt`` propagates, and the caller reads it
    as "not pending".
    """
    newest = None  # (iteration, root) of the newest apply receipt seen
    for root in roots:
        if _archived(root, name):
            return None
        directory = os.path.join(root, *(CHANGES_DIR + (name,)))
        if _receipt_text(os.path.join(directory, "finalize.md")) is not None:
            return None
        text = _receipt_text(os.path.join(directory, "apply.md"))
        if text is None:
            continue
        iteration = _iteration(text)
        if newest is None or iteration > newest[0]:
            newest = (iteration, root)
    if newest is None:
        return None
    iteration, root = newest
    verify = _receipt_text(os.path.join(root, *(CHANGES_DIR + (name, "verify.md"))))
    verify_state = "none"
    if verify is not None and _iteration(verify) == iteration:
        if FAIL_TICKED_RE.search(verify):
            return None
        if PASS_TICKED_RE.search(verify):
            verify_state = "pass"
    return PendingChange(name, iteration, verify_state)


def finalize_pending(roots, changes):
    """The ``changes`` that need finalize, as ``PendingChange`` sorted by name.

    ``roots`` is a ``change_roots`` list and ``changes`` the session change set. A change is pending
    when some copy in some root has ``apply.md``, no copy has ``finalize.md``, no root has an archived
    copy, and the ``verify.md`` beside the newest ``apply.md`` is missing, records another iteration,
    or has no FAIL box ticked. A receipt over ``RECEIPT_LIMIT`` bytes makes only its own change not
    pending, silently; a name that is not a valid change name is ignored. ``StoreError`` (a recoverable
    engine failure) propagates, so the caller fails open rather than blocking on a damaged checkout.
    """
    pending = []
    for name in changes:
        if not isinstance(name, str) or CHANGE_NAME_RE.fullmatch(name) is None:
            continue
        try:
            found = _change_pending(roots, name)
        except _OversizedReceipt:
            continue
        if found is not None:
            pending.append(found)
    return sorted(pending, key=lambda change: change.name)


STOP_LINE = (
    "Change `{name}` has an apply receipt and no finalize receipt. Run verify if it has not run, "
    "then finalize now: it pushes and creates the PR without asking. If a rule requires you to ask "
    "the user, or the user told you to stop, say which and end your turn; the next stop is not "
    "blocked."
)


def _stop_gate(rules, state, event, roots):
    """The stop gate: block once per pending change phase, with the rule text and a stop record.

    A ``stop`` in the main context asks every rule naming the ``superspec-finalize-pending`` predicate
    about the session change set over ``roots`` (the ``change_roots`` list; None reads as no checkout).
    The first pending change by name whose phase (newest apply iteration and verify state) has no stop
    record is named in ``STOP_LINE``, followed by the text of the rules that fit under the cap; the
    phase's record is written in the same transaction, keeping the ``KEEP_STOPS`` newest, and the rules
    are not marked delivered. A subagent stop, a rule without the predicate, or no unrecorded pending
    change passes. ``stop_hook_active`` is not consulted: the per-phase records bound the blocks, and a
    phase that changed may block again even when the earlier block continued the turn.
    """
    if event.agent:
        return Decision("pass", "", [])
    gated = [rule for rule in rules if FINALIZE_PENDING in rule.stops]
    if not gated:
        return Decision("pass", "", [])
    recorded = set(
        (record["change"], record["iteration"], record["verify_state"]) for record in state.stops
    )
    for change in finalize_pending(roots or [], state.changes):
        if (change.name, change.iteration, change.verify_state) in recorded:
            continue
        _, text = _capped(gated, STOP_LINE.format(name=change.name))
        write_id = state.store.next_write_id()
        state.stops.append({
            "change": change.name,
            "iteration": change.iteration,
            "verify_state": change.verify_state,
            "write_id": write_id,
        })
        del state.stops[:max(0, len(state.stops) - KEEP_STOPS)]
        return Decision("block", text, [(("stop", change.name), write_id)])
    return Decision("pass", "", [])


def decide(rules, state, key, event, roots=None):
    """The ``Decision`` for ``event`` in context ``key``; records it in ``state`` (one transaction).

    Moments (``tool-after``, ``prompt``) add the matched rules as context and mark them delivered. A
    rule is skipped when its entry is delivered in this context's current generation and already holds
    every trigger the event matched; a different trigger delivers it again, and a pending entry never
    suppresses. Gates (``tool-before`` with a command) deny a call matching a rule not delivered in
    this context, record the denied rules as pending with the matched patterns, and confirm a pending
    rule when a call matches one of its pending patterns. Only rules included under the cap are
    recorded. A successful ``openspec`` shell call records its ``--change`` names in the session change
    set, on the pass path too. ``stop`` runs the stop gate (``_stop_gate``) when ``roots`` holds the
    ``change_roots`` list; ``compacted`` re-arms and ``void`` removes entries and stop records by write
    id; both pass.
    """
    if event.kind == "stop":
        return _stop_gate(rules, state, event, roots)
    if event.kind == "tool-after":
        record_changes(state, event)
    if event.kind in ("prompt", "tool-after"):
        fresh = []
        matched = {}
        for rule, triggers in _moment_rules(rules, event):
            entry = _live(state, key, rule.identity)
            if entry is not None and entry["state"] == "delivered" and triggers <= entry["triggers"]:
                continue
            fresh.append(rule)
            matched[rule.identity] = triggers
        included, text = _capped(fresh)
        if not included:
            return Decision("pass", "", [])
        for rule in included:
            _deliver_moment(state, key, rule, matched[rule.identity])
        return Decision("context", text, _recorded(state, key, included))
    if event.kind == "tool-before":
        return _gate(rules, state, key, event)
    if event.kind == "compacted":
        # An identified subagent re-arms only its own key; otherwise the whole session errs toward an
        # extra delivery (a main-context compaction never carries an agent id on either host). A bump
        # leaves the session change set and stop records alone.
        if event.agent:
            state.bump(key)
        else:
            state.bump_all()
    elif event.kind == "void":
        _void(state, key, event.write_ids or [])
    return Decision("pass", "", [])


def _void(state, key, write_ids):
    """Remove ``key``'s entries and the session's stop records that carry a ``write_ids`` id exactly.

    A newer entry or record with its own id survives, so a late ``void`` cannot undo a later block.
    """
    wanted = set(write_ids)
    for entry_key, identity in list(state.entries):
        if entry_key == key and state.entries[(entry_key, identity)]["write_id"] in wanted:
            del state.entries[(entry_key, identity)]
    state.stops[:] = [record for record in state.stops if record["write_id"] not in wanted]


def _gate(rules, state, key, event):
    if event.command is None:
        return Decision("pass", "", [])
    denied = {}
    for rule in rules:
        patterns = set(pattern.pattern for pattern in rule.gates if pattern.search(event.command))
        if not patterns:
            continue
        entry = _live(state, key, rule.identity)
        if entry is None:
            denied[rule.name] = (rule, patterns)
        elif entry["state"] == "pending":
            if patterns & entry["patterns"]:
                state.put(key, rule.identity, "delivered", entry["patterns"])
            else:
                denied[rule.name] = (rule, patterns | entry["patterns"])
    if not denied:
        return Decision("pass", "", [])
    included, text = _capped([rule for rule, _ in denied.values()])
    for rule in included:
        state.put(key, rule.identity, "pending", denied[rule.name][1])
    return Decision("deny", text, _recorded(state, key, included))


FAILURE_NOTICE = (
    "Rule hooks failed: read the hooked rules listed in AGENTS.md by hand at the moments they name."
)
FAIL_AFTER_WRITE = "VELES_AGENT_RULES_FAIL_AFTER_WRITE"


# Claude Code events whose output has a model-visible context channel. PreToolUse context without a
# permission decision reached the model on Claude Code 2.1.326 and left the permission flow untouched
# (docs/hook-delivered-rules.md); PreCompact has no such channel.
CLAUDE_CONTEXT_EVENTS = ("tool-before", "tool-after", "prompt")


STREAM_FDS = {"stdout": 1, "stderr": 2}


def _neutralise(name):
    """Point the real ``sys.<name>`` at the null device, so no later write or exit flush can fail."""
    try:
        null = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null, STREAM_FDS[name])
        finally:
            os.close(null)
    except OSError:
        pass
    try:
        setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    except OSError:
        pass


def _emit(name, text, stream=None):
    """The one guarded writer for the hook path: write and flush, and on failure silence the stream.

    ``stream`` defaults to the current ``sys.<name>``; a real standard stream that fails is redirected to
    the null device (``_neutralise``). Returns whether the text was written.
    """
    stream = getattr(sys, name) if stream is None else stream
    if stream is None:  # a standard stream that was closed when Python started
        return False
    try:
        stream.write(text)
        stream.flush()
        return True
    except (OSError, ValueError):
        if stream is getattr(sys, name) or stream is getattr(sys, "__%s__" % name):
            _neutralise(name)
        return False


def _stderr(text):
    """Write to stderr, best effort: a broken stderr must not turn a pass into a failure."""
    _emit("stderr", text)


def _diagnose(what, error):
    """One stderr line for ``error``."""
    message = " ".join(str(error).split())
    _stderr("agent_rules.py hook: %s: %s: %s\n" % (what, type(error).__name__, message))


def _failure_output(fmt, payload):
    """The read-by-hand notice where the host has a context channel; otherwise ``""`` and stderr."""
    if fmt == "plain":
        return render("plain", None, Decision("context", FAILURE_NOTICE, []))
    if fmt == "claude" and isinstance(payload, dict):
        name = payload.get("hook_event_name")
        if isinstance(name, str) and CLAUDE_EVENTS.get(name) in CLAUDE_CONTEXT_EVENTS:
            return json.dumps({"hookSpecificOutput": {
                "hookEventName": name, "additionalContext": FAILURE_NOTICE,
            }})
    _stderr(FAILURE_NOTICE + "\n")
    return ""


def handle(stdin_text, fmt, env, store_factory):
    """Run one hook event and return ``(stdout text, exit code)``; the exit code is never 2.

    The decision is made, rendered and recorded in one ``Store.transaction``; the prune follows a
    transaction that wrote, best effort. Every failure passes with the notice (``_failure_output``) and exit 0. Only the
    test-only ``FAIL_AFTER_WRITE`` switch returns ``("", 1)``, after the state was committed.
    """
    payload = None
    try:
        payload = json.loads(stdin_text)
        event = parse_event(fmt, payload)
        checkout = discover_checkout(event.cwd)
        if checkout is None:
            return "", 0
        toplevel, common_dir = checkout
        rules, errors = load_rules(toplevel)
        if errors:
            raise RuleError("; ".join("%s %s" % error for error in errors))
        if not rules:
            return "", 0
        store = store_factory(os.path.join(common_dir, "agent-rules"))
        # The stop gate's receipts are searched across the checkout and its worktrees; every other
        # event needs no git layout beyond the discovery above.
        roots = change_roots(toplevel, common_dir) if event.kind == "stop" else None
        with store.transaction(event.session) as state:
            # Rendering inside the transaction: a decision that cannot be printed is never recorded.
            output = render(fmt, event, decide(rules, state, context_key(event), event, roots))
        if state.wrote:
            try:
                store.prune(event.session)
            except Exception as error:  # pruning is housekeeping; the decision stands
                _diagnose("prune skipped", error)
        if env.get(FAIL_AFTER_WRITE) == "1":
            _stderr("agent_rules.py hook: %s=1: failing after the write\n" % FAIL_AFTER_WRITE)
            return "", 1
        return output, 0
    except Exception as error:
        _diagnose("failed open", error)
        return _failure_output(fmt, payload), 0


class _UsageError(Exception):
    """A ``hook`` command line argparse rejected."""


class _HookParser(argparse.ArgumentParser):
    """Raises on a usage error instead of exiting 2, which Claude Code would treat as a blocking hook."""

    def error(self, message):
        raise _UsageError(message)


def _hook_parser():
    # add_help=False: argparse's own help action writes and exits outside the guarded emitter.
    parser = _HookParser(
        prog="agent_rules.py hook", description="Decide one hook event read as JSON on stdin.", add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true", help="show this help message and exit")
    parser.add_argument("--format", choices=("claude", "plain"), help="the event format (required)")
    return parser


def hook_main(argv, stdin=None, stdout=None, env=None, store_factory=Store):
    """The ``hook`` subcommand: never exits 2, and fails open on usage, stdin and stdout errors.

    Every byte it prints goes through ``_emit``. Only the test-only ``FAIL_AFTER_WRITE`` switch returns 1.
    """
    stdin = sys.stdin if stdin is None else stdin
    env = os.environ if env is None else env
    parser = _hook_parser()
    try:
        args = parser.parse_args(argv)
        if not args.help and args.format is None:
            raise _UsageError("the following arguments are required: --format")
    except _UsageError as error:
        _stderr("%sagent_rules.py hook: error: %s\n%s\n" % (parser.format_usage(), error, FAILURE_NOTICE))
        return 0
    if args.help:
        _emit("stdout", parser.format_help(), stdout)
        return 0
    try:
        text = stdin.buffer.read().decode("utf-8", "replace")
    except Exception as error:
        _diagnose("stdin unreadable", error)
        output, code = _failure_output(args.format, None), 0
    else:
        output, code = handle(text, args.format, env, store_factory)
    if output and not _emit("stdout", output + "\n", stdout):
        _stderr("agent_rules.py hook: stdout unwritable\n%s\n" % FAILURE_NOTICE)
        return 0
    return code


def _default_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="agent_rules.py", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command")
    commands.required = True
    pointers = commands.add_parser("pointers", help="write or check the AGENTS.md pointer block")
    mode = pointers.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write the block")
    mode.add_argument("--check", action="store_true", help="exit 1 when the block is stale or missing")
    pointers.add_argument("--root", default=_default_root(), help="repository root")
    pointers.set_defaults(handler=_pointers)
    # Listed for --help only: ``hook`` is dispatched to ``hook_main`` before this parser runs.
    commands.add_parser("hook", help="decide one hook event read as JSON on stdin (--format claude|plain)")
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "hook":
        return hook_main(argv[1:])
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
