#!/usr/bin/env python3
"""Check: the command table's structure and no node doc repeats a command it runs (design D1).

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
RUN_COMMAND_KINDS = ("check", "test")
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
    """The tasks of a task-only `./gradlew` line, or None when the line is targeted.

    A value-less Gradle option (`-q`, `--offline`) starts with `-` and is ignored. Any
    remaining argument that is not a task name (for example the class after `--tests`) makes
    the line targeted, so it is never a duplicate.
    """
    if not isinstance(argv, list) or len(argv) < 2 or argv[0] != "./gradlew":
        return None
    if not all(isinstance(item, str) for item in argv):
        return None
    tasks = [item for item in argv[1:] if not item.startswith("-")]
    if not tasks or not all(TASK_RE.match(item) for item in tasks):
        return None
    return tasks


def _entry_label(entry):
    if entry.get("id"):
        return entry["id"]
    return " ".join(entry.get("line") or []) or "entry"


def _run_candidates(table):
    """The table's `check` and `test` commands, which no doc line may repeat."""
    candidates = []
    nodes = table.get("nodes") if isinstance(table, dict) else None
    if not isinstance(nodes, dict):
        return candidates
    for name in sorted(nodes):
        entry = nodes[name]
        if not isinstance(entry, dict):
            continue
        for command in entry.get("commands") or []:
            if not isinstance(command, dict):
                continue
            if command.get("kind") not in RUN_COMMAND_KINDS:
                continue
            run = command.get("run")
            if not isinstance(run, list) or not run:
                continue
            candidates.append(
                {"id": _entry_label(command), "argv": run, "cwd": command.get("cwd", ".")}
            )
    return candidates


def _unique(items):
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def _duplicating_ids(entry, candidates):
    """The ids of the check/test entries that make a documented line a duplicate.

    The line is a duplicate when its argv and cwd equal an entry's, or when it is a task-only
    `./gradlew` line and every task it names is run by entries from the same directory. An
    id appears once, in the order the line names the tasks.
    """
    same = [
        candidate["id"]
        for candidate in candidates
        if candidate["cwd"] == entry.cwd and candidate["argv"] == entry.argv
    ]
    if same:
        return _unique(same)
    wanted = _task_only(entry.argv)
    if wanted is None:
        return []
    provided = collections.defaultdict(list)
    for candidate in candidates:
        if candidate["cwd"] != entry.cwd:
            continue
        tasks = _task_only(candidate["argv"])
        if tasks is None:
            continue
        for task in tasks:
            if candidate["id"] not in provided[task]:
                provided[task].append(candidate["id"])
    if not all(task in provided for task in wanted):
        return []
    identifiers = []
    for task in wanted:
        for identifier in provided[task]:
            if identifier not in identifiers:
                identifiers.append(identifier)
    return identifiers


def duplicate_findings(table, docs):
    """The documented lines that repeat a command the verifier runs, each with the fix.

    ``docs`` maps each scanned doc's path to its entries (``_read_entries``). A line is a
    duplicate when its argv and working directory equal those of a `check` or `test` entry of
    any node, or when it is a task-only `./gradlew` line and every task it names is a task of
    such entries from the same working directory. A documented line the table does not hold is
    fine: the table must not require a doc entry, and the doc must not repeat one.
    """
    candidates = _run_candidates(table)
    found = []
    for doc in sorted(docs):
        for entry in docs[doc]:
            identifiers = _duplicating_ids(entry, candidates)
            if identifiers:
                found.append(
                    "%s: `%s` is run by verify.py (%s); remove it from the doc"
                    % (doc, entry.line, ", ".join(identifiers))
                )
    return found


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
        if not isinstance(commands, list):
            findings.append(
                Finding("error", TABLE_PATH, "node `%s` has no commands list; add one" % node)
            )
            commands = []
        if "excluded" in entry:
            findings.append(
                Finding(
                    "error",
                    TABLE_PATH,
                    "node `%s` has an `excluded` list; remove it (the table is the single "
                    "source of the commands it runs)" % node,
                )
            )
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
    if "doc" in step:
        findings.append(
            Finding(
                "error",
                TABLE_PATH,
                "`%s` has a `doc` field; remove it (the table is the single source of the "
                "commands it runs)" % label,
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


def _scanned_docs(root, cmap):
    """Every doc the check reads: a node's AGENTS.md and the rules its `## Rules` lists."""
    docs = []
    for doc in _node_docs(root, cmap).values():
        if doc not in docs:
            docs.append(doc)
        for rule in _listed_rules(root, doc):
            if rule not in docs:
                docs.append(rule)
    return docs


# ---------------------------------------------------------------------------------------------
# The check


def findings(root):
    """Findings for the repository at ``root``; each message is the fix."""
    cmap, cmap_problem = _read_json(root, COMPONENTS_PATH)
    if cmap_problem is not None or not isinstance(cmap, dict):
        return []  # the agent-file check already reports a missing or invalid component map
    table, table_problem = _read_json(root, TABLE_PATH)
    if table_problem is not None:
        return [Finding("error", TABLE_PATH, table_problem)]

    found, complete = _structure(cmap, table)
    if not complete:
        return found

    docs = {}
    for doc in _scanned_docs(root, cmap):
        text = _read_text(root, doc)
        if text is not None:
            docs[doc] = _read_entries(text)
    for message in duplicate_findings(table, docs):
        doc, _, detail = message.partition(": ")
        found.append(Finding("error", doc, detail))
    return found
