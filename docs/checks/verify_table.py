#!/usr/bin/env python3
"""Drift check: the command table matches the node docs (design D13).

Called from `check_agent_files.py`, inside the docs offline guard. It reads only
`.github/components.json`, `scripts/verify-commands.json` and the docs those name, and every
finding's message is the fix. The structural subset is reimplemented here (not imported from
`scripts/verify.py`), so the docs check stays a standalone stdlib module. Stdlib only; Python
3.8 compatible.
"""
import collections
import json
import os
import re
import shlex

Finding = collections.namedtuple("Finding", "severity path message")
DocCommand = collections.namedtuple("DocCommand", "argv cwd line")
_Entry = collections.namedtuple("_Entry", "argv cwd line section")

TABLE_PATH = "scripts/verify-commands.json"
COMPONENTS_PATH = ".github/components.json"

SHELL_INFOS = ("", "bash", "sh", "shell")
COMMAND_KINDS = ("check", "test", "device", "platform")
REQUIREMENT_KINDS = ("tool", "path", "generated")
# Lines that are shell structure, not a command a reader can copy verbatim.
KEYWORDS = ("if", "then", "else", "elif", "fi", "for", "do", "done", "while", "case", "esac")
NO_COMMANDS = ("echo", "exit", "set", "export", "source", ".", ":")
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
TASK_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]*$")
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
FENCE_RE = re.compile(r"^\s*```(.*)$")
RUN_FROM_RE = re.compile(r"Run from `([^`]+)`")
RULE_RE = re.compile(r"\.agents/rules/([A-Za-z0-9._-]+\.md)")
BUILD_SECTIONS = ("Build & Test", "Commands")
GLOB_CHARS = ("*", "?")


# ---------------------------------------------------------------------------------------------
# Reading a doc


def _strip_comment(line):
    """Remove a shell comment that starts outside quotes."""
    quote = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            continue
        if char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
    return line.rstrip()


def _join_dir(cwd, target):
    if target.startswith("/"):
        return target.lstrip("/") or "."
    joined = os.path.normpath(os.path.join(cwd, target))
    return joined if joined not in ("", ".") else "."


def _read_entries(text):
    """Every command in the shell fences, with its working directory and section."""
    entries = []
    section = ""
    last_paragraph = ""
    paragraph = []
    cwd = "."
    in_fence = False
    info = ""
    pending = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not in_fence:
            match = FENCE_RE.match(line)
            if match:
                # Track every fence, so a closing ``` of a `text` block is not mistaken for the
                # empty-info opening of a shell block.
                info = match.group(1).strip()
                in_fence = True
                paragraph_text = " ".join(paragraph)
                if paragraph_text:
                    last_paragraph = paragraph_text
                paragraph = []
                if info in SHELL_INFOS:
                    cwd = "."
                    found = RUN_FROM_RE.search(last_paragraph)
                    if found:
                        cwd = found.group(1).strip().rstrip("/") or "."
                continue
            heading = HEADING_RE.match(line)
            if heading:
                section = heading.group(2).strip()
                last_paragraph = ""
                paragraph = []
                continue
            if not stripped:
                if paragraph:
                    last_paragraph = " ".join(paragraph)
                    paragraph = []
                continue
            paragraph.append(stripped)
            continue
        if stripped.startswith("```"):
            in_fence = False
            pending = None
            continue
        if info not in SHELL_INFOS:
            continue
        if pending is not None:
            chunk = pending + " " + stripped
            pending = None
        else:
            chunk = line
        if chunk.rstrip().endswith("\\"):
            pending = chunk.rstrip()[:-1].rstrip()
            continue
        command = _strip_comment(chunk).strip()
        if not command:
            continue
        try:
            argv = shlex.split(command)
        except ValueError:
            continue
        if not argv:
            continue
        if argv[0] == "cd":
            if len(argv) > 1:
                cwd = _join_dir(cwd, argv[1])
            continue
        if argv[0] in KEYWORDS or argv[0] in NO_COMMANDS:
            continue
        if ASSIGNMENT_RE.match(argv[0]):
            continue
        entries.append(_Entry(argv, cwd, command, section))
    return entries


def read_doc_commands(text, doc):
    """The documented commands of ``text``, in order (``doc`` names the file in findings)."""
    del doc  # the caller names the path in its findings; kept for the published interface
    return [
        DocCommand(entry.argv, entry.cwd, entry.line) for entry in _read_entries(text)
    ]


# ---------------------------------------------------------------------------------------------
# Matching helpers


def _task_only(argv):
    return (
        isinstance(argv, list)
        and len(argv) > 1
        and argv[0] == "./gradlew"
        and all(isinstance(item, str) and TASK_RE.match(item) for item in argv[1:])
    )


def _has_glob(token):
    return any(char in token for char in GLOB_CHARS)


def _glob_match(pattern, text):
    expression = "".join(
        "[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char)
        for char in pattern
    )
    return re.match("^" + expression + "$", text) is not None


def _glob_tokens(document, entry):
    """Match a documented argv whose glob token may absorb one or more entry tokens."""
    if not document:
        return not entry
    token = document[0]
    if _has_glob(token):
        for count in range(1, len(entry) + 1):
            if all(_glob_match(token, item) for item in entry[:count]):
                if _glob_tokens(document[1:], entry[count:]):
                    return True
        return False
    if not entry or entry[0] != token:
        return False
    return _glob_tokens(document[1:], entry[1:])


def _argv_match(document, argv):
    """A documented argv covers an entry's argv, allowing a documented shell glob."""
    if document == argv:
        return True
    if not any(_has_glob(token) for token in document):
        return False
    return _glob_tokens(document, argv)


def _documented(doc_entry, argv):
    if _argv_match(doc_entry.argv, argv):
        return True
    return _task_only(doc_entry.argv) and _task_only(argv) and set(argv[1:]) <= set(
        doc_entry.argv[1:]
    )


def _entry_label(entry):
    if entry.get("id"):
        return entry["id"]
    return " ".join(entry.get("line") or []) or "entry"


# ---------------------------------------------------------------------------------------------
# Structural subset (design D2, the part the drift check needs)


def _structure(cmap, table):
    findings = []
    if not isinstance(table, dict):
        return [Finding("error", TABLE_PATH, "is not a JSON object; fix scripts/verify-commands.json")], False
    if table.get("version") != 1:
        return [
            Finding("error", TABLE_PATH, "has no `\"version\": 1`; add it to scripts/verify-commands.json")
        ], False
    requirements = table.get("requirements")
    producers = table.get("producers")
    nodes = table.get("nodes")
    complete = True
    if not isinstance(requirements, dict):
        findings.append(
            Finding("error", TABLE_PATH, "has no requirements object; add one (it may be empty)")
        )
        requirements = {}
        complete = False
    if not isinstance(producers, list):
        findings.append(
            Finding("error", TABLE_PATH, "has no producers list; add one (it may be empty)")
        )
        producers = []
        complete = False
    if not isinstance(nodes, dict):
        findings.append(Finding("error", TABLE_PATH, "has no nodes object; add every component-map node"))
        return findings, False

    expected = set(cmap.get("nodes") or {})
    if expected and set(nodes) != expected:
        missing = sorted(expected - set(nodes))
        extra = sorted(set(nodes) - expected)
        parts = []
        if missing:
            parts.append("missing %s" % ", ".join("`%s`" % name for name in missing))
        if extra:
            parts.append("extra %s" % ", ".join("`%s`" % name for name in extra))
        findings.append(
            Finding(
                "error",
                TABLE_PATH,
                "nodes differ from .github/components.json (%s); make them exactly its nodes"
                % " and ".join(parts),
            )
        )

    producer_ids = set()
    for index, producer in enumerate(producers):
        if not isinstance(producer, dict):
            findings.append(
                Finding("error", TABLE_PATH, "producer %d is not an object; fix it" % index)
            )
            continue
        identifier = producer.get("id")
        if not isinstance(identifier, str) or not identifier:
            findings.append(
                Finding("error", TABLE_PATH, "producer %d has no id; give it one" % index)
            )
        elif identifier in producer_ids:
            findings.append(
                Finding("error", TABLE_PATH, "duplicate id `%s`; give every command and producer a unique id" % identifier)
            )
        else:
            producer_ids.add(identifier)
        findings.extend(_step_structure(producer, _entry_label(producer), requirements))

    ids = set(producer_ids)
    for node, entry in sorted(nodes.items()):
        if not isinstance(entry, dict):
            findings.append(Finding("error", TABLE_PATH, "node `%s` is not an object; fix it" % node))
            continue
        commands = entry.get("commands", [])
        excluded = entry.get("excluded", [])
        if not isinstance(commands, list):
            findings.append(
                Finding("error", TABLE_PATH, "node `%s` has no commands list; add one" % node)
            )
            commands = []
        for command in commands:
            if not isinstance(command, dict):
                findings.append(
                    Finding("error", TABLE_PATH, "a `%s` command is not an object; fix it" % node)
                )
                continue
            identifier = command.get("id")
            if not isinstance(identifier, str) or not identifier:
                findings.append(
                    Finding("error", TABLE_PATH, "a `%s` command has no id; give it one" % node)
                )
            elif identifier in ids:
                findings.append(
                    Finding("error", TABLE_PATH, "duplicate id `%s`; give every command and producer a unique id" % identifier)
                )
            else:
                ids.add(identifier)
            kind = command.get("kind")
            if kind not in COMMAND_KINDS:
                findings.append(
                    Finding(
                        "error",
                        TABLE_PATH,
                        "`%s` has unknown kind %r; use check, test, device or platform"
                        % (_entry_label(command), kind),
                    )
                )
            findings.extend(_step_structure(command, _entry_label(command), requirements))
        if not isinstance(excluded, list):
            findings.append(
                Finding("error", TABLE_PATH, "node `%s` has no excluded list; add one" % node)
            )
            excluded = []
        for entry_item in excluded:
            if not isinstance(entry_item, dict):
                findings.append(
                    Finding("error", TABLE_PATH, "a `%s` exclusion is not an object; fix it" % node)
                )
                continue
            if not isinstance(entry_item.get("doc"), str) or not entry_item.get("doc"):
                findings.append(
                    Finding(
                        "error",
                        TABLE_PATH,
                        "an exclusion in `%s` has no doc; name the doc whose line it excludes"
                        % node,
                    )
                )
            if not isinstance(entry_item.get("line"), str) or not entry_item.get("line"):
                findings.append(
                    Finding(
                        "error",
                        TABLE_PATH,
                        "an exclusion in `%s` has no line; copy the documented command"
                        % node,
                    )
                )

    for identifier, requirement in sorted(requirements.items()):
        if not isinstance(requirement, dict):
            findings.append(
                Finding("error", TABLE_PATH, "requirement `%s` is not an object; fix it" % identifier)
            )
            continue
        kind = requirement.get("kind")
        if kind not in REQUIREMENT_KINDS:
            findings.append(
                Finding(
                    "error",
                    TABLE_PATH,
                    "requirement `%s` has unknown kind %r; use tool, path or generated"
                    % (identifier, kind),
                )
            )
        if kind == "generated":
            producer = requirement.get("producer")
            if producer not in producer_ids:
                findings.append(
                    Finding(
                        "error",
                        TABLE_PATH,
                        "requirement `%s` names unknown producer %r; add the producer or fix the reference"
                        % (identifier, producer),
                    )
                )
    return findings, complete


def _step_structure(step, label, requirements):
    findings = []
    run = step.get("run")
    if not isinstance(run, list) or not run or not all(isinstance(item, str) for item in run):
        findings.append(
            Finding("error", TABLE_PATH, "`%s` has no run argv; give it a non-empty list of strings" % label)
        )
    if not isinstance(step.get("doc"), str) or not step.get("doc"):
        findings.append(
            Finding(
                "error",
                TABLE_PATH,
                "`%s` has no doc; name the node's AGENTS.md or a rule it lists" % label,
            )
        )
    requires = step.get("requires", [])
    if not isinstance(requires, list):
        findings.append(
            Finding("error", TABLE_PATH, "`%s` has a non-list requires; make it a list" % label)
        )
    else:
        for requirement in requires:
            if requirement not in requirements:
                findings.append(
                    Finding(
                        "error",
                        TABLE_PATH,
                        "`%s` requires unknown requirement `%s`; add it to requirements or fix the reference"
                        % (label, requirement),
                    )
                )
    if not isinstance(step.get("inputs"), list) and step.get("reuse") is not False:
        findings.append(
            Finding(
                "error",
                TABLE_PATH,
                "`%s` declares neither inputs nor \"reuse\": false; add its inputs or set reuse to false"
                % label,
            )
        )
    return findings


# ---------------------------------------------------------------------------------------------
# Docs


def _read_text(root, rel):
    try:
        with open(os.path.join(root, *rel.split("/")), encoding="utf-8", newline="") as handle:
            return handle.read()
    except OSError:
        return None


def _read_json(root, rel):
    text = _read_text(root, rel)
    if text is None:
        return None, "is missing; add it"
    try:
        return json.loads(text), None
    except ValueError as error:
        return None, "is not valid JSON (%s); fix it" % error


def _node_docs(root, cmap):
    """Each component-map node's AGENTS.md, from the first node path that has one."""
    docs = {}
    nodes = cmap.get("nodes")
    if not isinstance(nodes, dict):
        return docs
    for name, node in sorted(nodes.items()):
        if not isinstance(node, dict):
            continue
        paths = node.get("paths")
        if not isinstance(paths, list):
            continue
        for path in paths:
            if not isinstance(path, str) or not path:
                continue
            folder = path if path.endswith("/") else os.path.dirname(path)
            while folder:
                candidate = folder.rstrip("/") + "/AGENTS.md"
                if os.path.isfile(os.path.join(root, *candidate.split("/"))):
                    docs[name] = candidate
                    break
                parent = os.path.dirname(folder.rstrip("/"))
                folder = "" if parent in ("", ".") else parent
            if name in docs:
                break
    return docs


def _listed_rules(root, doc):
    if not doc:
        return []
    text = _read_text(root, doc)
    if text is None:
        return []
    section = ""
    names = []
    for line in text.splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            section = heading.group(2).strip()
            continue
        if section == "Rules":
            names.extend(RULE_RE.findall(line))
    return sorted(set(".agents/rules/" + name for name in names))


def _node_allows(node_doc, doc, rules):
    if doc == node_doc or doc in rules:
        return True
    if not node_doc:
        return False
    folder = os.path.dirname(node_doc)
    return bool(folder) and doc.startswith(folder + "/") and doc.endswith("/AGENTS.md")


# ---------------------------------------------------------------------------------------------
# The check


def findings(root):
    """Drift findings for the repository at ``root``; each message is the fix."""
    cmap, cmap_problem = _read_json(root, COMPONENTS_PATH)
    if cmap_problem is not None or not isinstance(cmap, dict):
        return []  # the agent-file check already reports a missing or invalid component map
    table, table_problem = _read_json(root, TABLE_PATH)
    if table_problem is not None:
        return [Finding("error", TABLE_PATH, table_problem)]

    found, complete = _structure(cmap, table)
    if not complete:
        return found

    node_docs = _node_docs(root, cmap)
    node_rules = dict(
        (node, _listed_rules(root, doc)) for node, doc in node_docs.items()
    )
    producers = [producer for producer in table.get("producers", []) if isinstance(producer, dict)]
    nodes = table["nodes"]

    cache = {}

    def entries_for(doc):
        if doc not in cache:
            text = _read_text(root, doc)
            cache[doc] = [] if text is None else _read_entries(text)
        return cache[doc]

    any_doc = set(node_docs.values())
    for rules in node_rules.values():
        any_doc.update(rules)

    # Forward: every command and producer is a documented command of its doc, same directory.
    by_doc = collections.defaultdict(list)
    for node, entry in sorted(nodes.items()):
        if not isinstance(entry, dict):
            continue
        primary = node_docs.get(node)
        rules = node_rules.get(node, [])
        for command in entry.get("commands", []) or []:
            if not isinstance(command, dict):
                continue
            doc = command.get("doc")
            if isinstance(doc, str) and doc and primary:
                if not _node_allows(primary, doc, rules):
                    found.append(
                        Finding(
                            "error",
                            TABLE_PATH,
                            "`%s` names `%s`, which is not `%s`'s AGENTS.md or a rule it lists; "
                            "name the node doc or one of its rules"
                            % (_entry_label(command), doc, primary),
                        )
                    )
                elif _read_text(root, doc) is None:
                    _check_doc_exists(root, doc, found, _entry_label(command))
            _forward(command, entries_for, found)
        for exclusion in entry.get("excluded", []) or []:
            if not isinstance(exclusion, dict):
                continue
            doc = exclusion.get("doc")
            if isinstance(doc, str) and doc and primary and not _node_allows(primary, doc, rules):
                found.append(
                    Finding(
                        "error",
                        TABLE_PATH,
                        "an exclusion in `%s` names `%s`, which is not `%s`'s AGENTS.md or a rule "
                        "it lists; name the node doc or one of its rules" % (node, doc, primary),
                    )
                )
            line = exclusion.get("line")
            if isinstance(doc, str) and doc and isinstance(line, str) and line:
                try:
                    argv = shlex.split(line)
                except ValueError:
                    argv = None
                if argv is not None:
                    by_doc[doc].append(
                        {
                            "argv": argv,
                            "cwd": exclusion.get("cwd", "."),
                            "excluded": True,
                        }
                    )
    for producer in producers:
        doc = producer.get("doc")
        if isinstance(doc, str) and doc:
            if doc not in any_doc:
                found.append(
                    Finding(
                        "error",
                        TABLE_PATH,
                        "producer `%s` names `%s`, which is not a node's AGENTS.md or a rule one "
                        "lists; name an existing node doc or rule" % (_entry_label(producer), doc),
                    )
                )
            else:
                _check_doc_exists(root, doc, found, _entry_label(producer))
        _forward(producer, entries_for, found)

    for node, entry in sorted(nodes.items()):
        if not isinstance(entry, dict):
            continue
        for command in entry.get("commands", []) or []:
            if not isinstance(command, dict):
                continue
            doc = command.get("doc")
            if isinstance(doc, str) and doc:
                by_doc[doc].append(
                    {"argv": list(command.get("run") or []), "cwd": command.get("cwd", "."), "excluded": False}
                )
    for producer in producers:
        doc = producer.get("doc")
        if isinstance(doc, str) and doc:
            by_doc[doc].append(
                {"argv": list(producer.get("run") or []), "cwd": producer.get("cwd", "."), "excluded": False}
            )

    # Reverse: every line of a node's AGENTS.md and of the rules its `## Rules` lists.
    checked = set()
    for node, doc in sorted(node_docs.items()):
        if doc not in checked and _read_text(root, doc) is not None:
            _reverse(doc, entries_for(doc), by_doc.get(doc, []), found)
            checked.add(doc)
        for rule in node_rules.get(node, []):
            if rule in checked or _read_text(root, rule) is None:
                continue
            _reverse(rule, entries_for(rule), by_doc.get(rule, []), found)
            checked.add(rule)
    return found


def _check_doc_exists(root, doc, found, label):
    if _read_text(root, doc) is None:
        found.append(
            Finding(
                "error",
                TABLE_PATH,
                "`%s` names `%s`, which does not exist; create the doc or fix the path"
                % (label, doc),
            )
        )
        return False
    return None


def _forward(step, entries_for, found):
    doc = step.get("doc")
    if not isinstance(doc, str) or not doc:
        return  # reported by the structural pass
    argv = step.get("run")
    cwd = step.get("cwd", ".")
    if not isinstance(argv, list):
        return
    documented = entries_for(doc)
    if any(entry.cwd == cwd and _documented(entry, argv) for entry in documented):
        return
    message = "`%s` runs `%s` from `%s`, but %s documents no matching command; add it there or fix the entry" % (
        _entry_label(step),
        " ".join(str(item) for item in argv),
        cwd,
        doc,
    )
    other = [entry.cwd for entry in documented if _documented(entry, argv) and entry.cwd != cwd]
    if other:
        message += " (the doc has it under `%s`)" % other[0]
    found.append(Finding("error", TABLE_PATH, message))


def _reverse(doc, entries, candidates, found):
    for entry in entries:
        if doc.endswith("AGENTS.md") and entry.section not in BUILD_SECTIONS:
            continue
        if _reverse_covered(entry, candidates):
            continue
        message = "documents `%s`; add it to scripts/verify-commands.json or exclude it" % entry.line
        other = None
        for candidate in candidates:
            if candidate["cwd"] == entry.cwd:
                continue
            if candidate["argv"] == entry.argv or (
                _task_only(entry.argv)
                and _task_only(candidate["argv"])
                and set(candidate["argv"][1:]) <= set(entry.argv[1:])
            ):
                other = candidate["cwd"]
                break
        if other is not None:
            message += " (scripts/verify-commands.json runs it from `%s`, but the doc says `%s`)" % (
                other,
                entry.cwd,
            )
        found.append(Finding("error", doc, message))


def _reverse_covered(entry, candidates):
    if any(
        candidate["excluded"] and candidate["argv"] == entry.argv and candidate["cwd"] == entry.cwd
        for candidate in candidates
    ):
        return True
    if _task_only(entry.argv):
        wanted = set(entry.argv[1:])
        union = set()
        for candidate in candidates:
            if candidate["cwd"] != entry.cwd or not _task_only(candidate["argv"]):
                continue
            if set(candidate["argv"][1:]) <= wanted:
                union |= set(candidate["argv"][1:])
        if union == wanted:
            return True
    return any(
        not candidate["excluded"]
        and candidate["cwd"] == entry.cwd
        and _argv_match(entry.argv, candidate["argv"])
        for candidate in candidates
    )
