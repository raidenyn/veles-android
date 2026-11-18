#!/usr/bin/env python3
"""Hook-delivered agent rules: the rule parser, the pointer block and the hook event contract.

Standard library only, Python 3.8+, run as ``python3 -I scripts/agent_rules.py``.

A hooked rule is one file in ``.agents/hooked-rules/``. Its frontmatter uses the restricted
grammar of ``docs/checks/check_agent_files.py`` (copied here, not shared, until the checker
switches to this parser): ``key: scalar`` lines and flat lists of double-quoted strings in which
only the ``\\"`` and ``\\\\`` escapes are allowed, extended with a double-quoted ``pointer``.
"""

import argparse
import collections
import contextlib
import hashlib
import json
import os
import re
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

POINTER_HEADING = "## Hook-delivered rules"
POINTER_BEGIN = "<!-- hooked-rules:begin -->"
POINTER_END = "<!-- hooked-rules:end -->"
POINTER_SENTENCE = (
    "If your tool did not deliver a rule below, read its file yourself at the moment it names."
)

SCALAR_KEYS = ("pointer",)
LIST_KEYS = ("skills", "commands", "gates")
RULE_KEYS = SCALAR_KEYS + LIST_KEYS

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
# commands and gates are lists of compiled regexes.
Rule = collections.namedtuple(
    "Rule", ["name", "identity", "pointer", "skills", "commands", "gates", "body"]
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


def parse_rule(name, text):
    """Parse one hooked rule file's text into a ``Rule``, or raise ``RuleError``.

    The identity hash covers the text as given (the raw file, before newline normalisation).
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
    if not (skills or commands or gates):
        raise RuleError("a rule needs at least one entry in `skills`, `commands` or `gates`")
    size = len(original_body.encode("utf-8", "surrogatepass"))
    if size > BODY_LIMIT:
        raise RuleError("the body is %d bytes, over the %d byte limit" % (size, BODY_LIMIT))
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
    return Rule(name, (name, digest), pointer, skills, commands, gates, body)


def load_rules(root):
    """Return ``(rules sorted by name, [(path, message), ...])`` for ``root``'s hooked rules."""
    directory = os.path.join(root, *RULES_DIR.split("/"))
    rules = []
    errors = []
    if not os.path.isdir(directory):
        return rules, errors
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not name.endswith(".md") or not os.path.isfile(path):
            continue
        relative = "%s/%s" % (RULES_DIR, name)
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
            rules.append(parse_rule(name, raw.decode("utf-8")))
        except UnicodeDecodeError as error:
            errors.append((relative, "is not valid UTF-8: %s" % error))
        except (IOError, OSError) as error:
            errors.append((relative, "cannot be read: %s" % error))
        except RuleError as error:
            errors.append((relative, str(error)))
    return rules, errors


def render_pointer_block(rules):
    """The generated block, markers included: the fixed sentence and one line per rule.

    Every rule line starts with ``- `` so it can never equal a marker line, whatever text its
    pointer holds; the block is still checked, so a change here cannot break that silently.
    """
    lines = [POINTER_BEGIN, POINTER_SENTENCE, ""]
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
# (before its line break) and whether the heading is the last non-blank line before the begin marker.
BlockLocation = collections.namedtuple("BlockLocation", ["begin", "end", "heading_ok"])


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
    heading_ok = previous >= 0 and lines[previous][1] == POINTER_HEADING
    end_position, end_content = lines[ends[0]]
    return BlockLocation(lines[begins[0]][0], end_position + len(end_content), heading_ok)


def _apply_block(text, block):
    """``text`` with the block replaced between its markers, or appended under its heading.

    A block that is already current under its heading leaves the text untouched; a missing
    heading is written back in front of the begin marker.
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
            text, separator, POINTER_HEADING, eol + eol, block.replace("\n", eol), eol,
        )
    if location.heading_ok and _normalise(text[location.begin:location.end]) == block:
        return text
    heading = "" if location.heading_ok else POINTER_HEADING + eol + eol
    return text[:location.begin] + heading + block.replace("\n", eol) + text[location.end:]


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
                "%s lacks the `%s` heading before the hooked-rules block; "
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


EVENT_KINDS = ("tool-before", "tool-after", "prompt", "compacted", "void")
DECISION_KINDS = ("pass", "context", "deny")
# Variables that select or relocate the repository; git honours them over ``-C``, so a host started from
# a git hook could otherwise point the engine at an unrelated checkout. GIT_CEILING_DIRECTORIES is kept:
# it can only make discovery fail, which passes silently.
GIT_SELECTION_VARIABLES = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX",
)
GIT_TIMEOUT_SECONDS = 5  # a runtime bound on the one git call, not test timing

# Claude Code's hook_event_name for each engine event; ``void`` has no Claude Code form.
CLAUDE_EVENTS = {
    "PreToolUse": "tool-before",
    "PostToolUse": "tool-after",
    "UserPromptSubmit": "prompt",
    "PreCompact": "compacted",
}
CLAUDE_NAMES = dict((kind, name) for name, kind in CLAUDE_EVENTS.items())


class EventError(Exception):
    """A hook event that is malformed or not one the engine handles."""


# One normalized hook event. Only the fields the engine needs are kept: a tool response, which can be
# megabytes, is never copied. ``write_ids`` is a list of ints on a ``void`` event and None otherwise.
Event = collections.namedtuple(
    "Event",
    ["kind", "session", "agent", "cwd", "tool", "command", "ok", "skill", "path", "prompt", "write_ids"],
)

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

    A decision is never a permission grant: Claude Code's ``allow`` is never written. Raises
    ``ValueError`` for a decision or format that has no form.
    """
    if decision.kind not in DECISION_KINDS:
        raise ValueError("unknown decision %r" % (decision.kind,))
    if fmt not in ("claude", "plain"):
        raise ValueError("unknown output format %r" % (fmt,))
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


def checkout_for(cwd):
    """``(toplevel, common_dir)`` of the checkout holding ``cwd``, or None.

    One ``git rev-parse`` answers; ``common_dir`` is absolute, resolved against ``cwd`` when git prints
    it relative. None means pass silently: git failed (no repository, a bad ``cwd``) or the top level has
    no ``.agents/hooked-rules/`` directory.
    """
    if not isinstance(cwd, str) or not cwd or "\0" in cwd:
        return None
    environment = dict(
        (name, value) for name, value in os.environ.items() if name not in GIT_SELECTION_VARIABLES
    )
    try:
        completed = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel", "--git-common-dir"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if completed.returncode != 0:
        return None
    try:
        lines = completed.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    if len(lines) != 2 or not lines[0] or not lines[1]:
        return None
    toplevel = lines[0]
    common_dir = os.path.normpath(os.path.join(os.path.abspath(cwd), lines[1]))
    if not os.path.isdir(os.path.join(toplevel, *RULES_DIR.split("/"))):
        return None
    return toplevel, common_dir


STATE_VERSION = 1
KEEP_SESSIONS = 50
LOCK_RETRY_SECONDS = 0.1
ENTRY_STATES = ("delivered", "pending")
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


class SessionState(object):
    """One session's delivery state, read and written only inside ``Store.transaction``.

    ``entries`` maps ``(key, rule identity)`` to ``{"state", "patterns", "generation", "write_id"}``.
    A key is ``session`` for the main context and ``"session/agent"`` for a subagent; each key has its
    own generation, 0 until bumped.
    """

    def __init__(self, store, session, generations, entries):
        self.store = store
        self.session = session
        self.generations = generations
        self.entries = entries

    @classmethod
    def load(cls, store, session, data):
        """The state held in ``data`` (None for a session never written); StoreError when invalid."""
        generations = {}
        entries = {}
        if data is not None:
            version = data.get("version")
            if type(version) is not int or version != STATE_VERSION:
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
                identity, entry = cls._entry(item)
                if identity in entries:
                    raise StoreError("duplicate entry %r" % (identity,))
                entries[identity] = entry
        return cls(store, session, generations, entries)

    @staticmethod
    def _entry(item):
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
        generation, write_id = _count(item.get("generation")), _count(item.get("write_id"))
        if generation is None or write_id is None:
            raise StoreError("a state entry has an invalid generation or write id")
        return (key, (name, digest)), {
            "state": item["state"],
            "patterns": set(patterns),
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
                "generation": entry["generation"],
                "write_id": entry["write_id"],
            })
        return json.dumps({
            "version": STATE_VERSION,
            "session": self.session,
            "generations": self.generations,
            "entries": entries,
        }, sort_keys=True, indent=1) + "\n"

    def generation(self, key):
        return self.generations.get(key, 0)

    def bump(self, key):
        """Void every entry of ``key`` and no other key."""
        self.generations[key] = self.generation(key) + 1

    def bump_all(self):
        """Bump the main key and every subagent key this session has used."""
        keys = set(self.generations) | set(entry_key for entry_key, _ in self.entries)
        keys.add(self.session)
        for key in keys:
            self.bump(key)

    def put(self, key, identity, state, patterns):
        """Record ``state`` for ``(key, identity)`` at the key's generation, with a fresh write id."""
        if state not in ENTRY_STATES:
            raise ValueError("unknown entry state %r" % (state,))
        self.entries[(key, tuple(identity))] = {
            "state": state,
            "patterns": set(patterns),
            "generation": self.generation(key),
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
        indexed; an index entry without a file is harmless.
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

    @staticmethod
    def _victims(sessions, current):
        """The stems beyond the newest 50, oldest first, the current session never among them."""
        surplus = len(sessions) - KEEP_SESSIONS
        if surplus <= 0:
            return []
        oldest_first = sorted(sessions, key=lambda stem: sessions[stem])
        return [stem for stem in oldest_first if stem != current][:surplus]

    def prune(self, current_session):
        """Keep the newest 50 sessions by index order; never the current one; skip busy ones.

        Call it after the session transaction has released its lock. A first look at the index plans
        the victims. Each victim lock is then tried once, without waiting, and with only that lock
        held the index is re-read under its lock and the victim re-checked, so two concurrent prunes
        cannot together remove more than the surplus.
        """
        current = self._stem(current_session)
        with self._lock(self.index_lock_path()):
            planned = self._victims(self._load_index(create=False)["sessions"], current)
        for stem in planned:
            self._prune_one(stem, current)

    def _prune_one(self, stem, current):
        """Prune one victim, best effort: busy or failing victims stay where they are.

        A busy victim lock (LockTimeout) or any OSError on the victim leaves it in place with no index
        change, since the file is removed before the index entry. StoreError from the index lock
        or contents propagates.
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


CONTEXT_CAP = 9000  # bytes of joined rule text, below Claude Code's 10,000-character context limit
RULE_SEPARATOR = "\n\n"


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
    """The ``<name>`` of each ``/skills/<name>/SKILL.md`` suffix of the read path, given and real."""
    raw = event.path.replace("\\", "/")
    candidates = [raw]
    try:
        base = event.cwd if isinstance(event.cwd, str) else ""
        candidates.append(os.path.realpath(os.path.join(base, raw)).replace("\\", "/"))
    except (OSError, ValueError):
        pass
    names = set()
    for candidate in candidates:
        parts = candidate.split("/")
        if len(parts) >= 3 and parts[-1] == "SKILL.md" and parts[-3] == "skills" and parts[-2]:
            names.add(parts[-2])
    return names


def _moment_rules(rules, event):
    """The rules whose moment ``event`` is: a skill tool call, a ``/<skill>`` prompt, a SKILL.md read, or
    a successful command."""
    if event.kind == "prompt":
        skill = _prompt_skill(event.prompt)
        return [rule for rule in rules if skill is not None and skill in rule.skills]
    if event.kind != "tool-after":
        return []
    matched = []
    read_dirs = _read_skill_dirs(event) if event.path else set()
    for rule in rules:
        if event.skill is not None and event.skill in rule.skills:
            matched.append(rule)
        elif read_dirs & _skill_dirs(rule):
            matched.append(rule)
        elif event.command is not None and event.ok is True and any(
            pattern.search(event.command) for pattern in rule.commands
        ):
            matched.append(rule)
    return matched


def _capped(rules):
    """``(included rules, joined text)``: rules in name order, each whole, the text at most CONTEXT_CAP bytes.

    A rule that does not fit is left out and the next one is still tried.
    """
    included = []
    parts = []
    size = 0
    for rule in sorted(rules, key=lambda item: item.name):
        part = rule.body.strip()
        added = len(part.encode("utf-8", "surrogatepass"))
        if parts and part:
            added += len(RULE_SEPARATOR)
        if size + added > CONTEXT_CAP:
            continue
        included.append(rule)
        if part:
            parts.append(part)
        size += added
    return included, RULE_SEPARATOR.join(parts)


def _recorded(state, key, rules):
    return [(rule.identity, state.entries[(key, rule.identity)]["write_id"]) for rule in rules]


def decide(rules, state, key, event):
    """The ``Decision`` for ``event`` in context ``key``; records it in ``state`` (one transaction).

    Moments (``tool-after``, ``prompt``) add the matched rules as context and mark them delivered. Gates
    (``tool-before`` with a command) deny a call matching a rule not delivered in this context, record
    the denied rules as pending with the matched patterns, and confirm a pending rule when a call matches
    one of its pending patterns. Only rules included under the cap are recorded.
    """
    if event.kind in ("prompt", "tool-after"):
        included, text = _capped(_moment_rules(rules, event))
        if not included:
            return Decision("pass", "", [])
        for rule in included:
            state.put(key, rule.identity, "delivered", set())
        return Decision("context", text, _recorded(state, key, included))
    if event.kind == "tool-before":
        return _gate(rules, state, key, event)
    return Decision("pass", "", [])


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
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
