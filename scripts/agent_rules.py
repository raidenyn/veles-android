#!/usr/bin/env python3
"""Hook-delivered agent rules: the rule parser and the root AGENTS.md pointer block.

Standard library only, Python 3.8+, run as ``python3 -I scripts/agent_rules.py``.

A hooked rule is one file in ``.agents/hooked-rules/``. Its frontmatter uses the restricted
grammar of ``docs/checks/check_agent_files.py`` (copied here, not shared, until the checker
switches to this parser): ``key: scalar`` lines and flat lists of double-quoted strings in which
only the ``\\"`` and ``\\\\`` escapes are allowed, extended with a double-quoted ``pointer``.
"""

import argparse
import collections
import hashlib
import os
import re
import sys

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
