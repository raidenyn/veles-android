#!/usr/bin/env python3
"""verify: run the affected nodes' commands, the task gate by default and `--branch` before a push.

Standard library only, Python 3.8 compatible, run as `python3 -I scripts/verify.py`. The node
commands live in `scripts/verify-commands.json` (design D2); this module loads and validates that
table, owns the injected clock/wait/runner/git/filesystem seams, and wires the CLI modes. See
`openspec/changes/verify-affected-nodes-stop-hook/design.md` D1-D14.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(SCRIPTS)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import agent_rules  # noqa: E402
import components  # noqa: E402

TABLE_PATH = os.path.join(SCRIPTS, "verify-commands.json")
COMPONENT_MAP_PATH = os.path.join(REPO, ".github", "components.json")
GIT_TIMEOUT_SECONDS = 120
GRADLE_HOME_PREFIX = "{gradle_user_home}/"
STEP_KINDS = ("check", "test", "device", "platform")
REQUIREMENT_KINDS = ("tool", "path", "generated")
RESOLVERS = (
    "java-home",
    "android-sdk",
    "android-ndk",
    "path-tool",
    "rustup-toolchain",
    "playwright-chromium",
)
# A related strategy names exactly these fields (plus the optional `within`).
STRATEGY_FIELDS = {
    "files-arg": ("match", "resolve", "run"),
    "name-map": ("sources", "tests", "run"),
    "self": ("tests", "run"),
    "crate": ("crates",),
}
# The fixed tool map of design D6: a reusable step must declare these as `tool` requirements.
# `./gradlew` needs the JDK Gradle itself would use; these programs' identity changes a result.
# Anything else (git, grep inside a script, the tools a shell script calls) is the table author's
# responsibility and is deliberately not validated by name.
PROGRAM_REQUIREMENTS = {
    "./gradlew": "java-home",
    "node": "path-tool",
    "npm": "path-tool",
    "npx": "path-tool",
    "python3": "path-tool",
}


class TableError(ValueError):
    """The command table is invalid; the message names the problem and the offending id."""


@dataclass(frozen=True)
class Outcome:
    """One resolver, probe, producer or step result (design D9/D10)."""

    kind: str
    code: Optional[int] = None
    tail: List[str] = field(default_factory=list)
    fix: Optional[str] = None
    error: Optional[str] = None
    stdout: Optional[List[str]] = None
    stderr: Optional[List[str]] = None
    elapsed_s: float = 0.0


def _quiet(line):
    pass


@dataclass
class Context:
    """Everything a run touches, injected so unit tests never sleep, spawn or read the clock."""

    git: object
    runner: object
    clock: object
    wait: object
    fs: object
    env: Dict[str, str]
    repo: str
    common_dir: str
    run_dir: str
    deadline: Optional[float] = None
    log: Callable[[str], None] = _quiet


Run = Callable[[List[str], str, Dict[str, str], str, float, bool], Outcome]


@dataclass(frozen=True)
class Step:
    """One command or producer of the table (producers carry `kind="producer"`)."""

    id: str
    kind: str
    cwd: str = "."
    run: List[str] = field(default_factory=list)
    doc: str = ""
    requires: List[str] = field(default_factory=list)
    inputs: List[dict] = field(default_factory=list)
    reuse: bool = True
    env_exit: List[dict] = field(default_factory=list)
    env_patterns: List[dict] = field(default_factory=list)
    platforms: List[str] = field(default_factory=list)
    ci: Optional[str] = None
    related: Optional[dict] = None


@dataclass(frozen=True)
class NodeEntry:
    commands: List[Step]
    excluded: List[dict]
    none: Optional[str] = None


@dataclass(frozen=True)
class Table:
    requirements: Dict[str, dict]
    producers: Dict[str, Step]
    nodes: Dict[str, NodeEntry]


class Clock:
    """The wall clock, injected as `ctx.clock.now()`."""

    def now(self):
        return time.time()


class Wait:
    """The sleep primitive, injected as `ctx.wait.sleep(seconds)`."""

    def sleep(self, seconds):
        time.sleep(seconds)


class Fs:
    """Filesystem reads behind one seam (design D6/D4); tests inject `verifyfake.FakeFs`."""

    def exists(self, path):
        return os.path.exists(path)

    def isdir(self, path):
        return os.path.isdir(path)

    def listdir(self, path):
        return sorted(os.listdir(path))

    def read_bytes(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def read_text(self, path):
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()

    def walk(self, top):
        for root, dirnames, filenames in os.walk(top):
            yield root, sorted(dirnames), sorted(filenames)

    def stat(self, path):
        return os.stat(path)


class GitError(Exception):
    """A read-only git query failed (design D3 turns this into a selection error)."""


class Git:
    """Read-only git queries (design D3). Every call runs in `cwd` with `GIT_*` overrides removed."""

    def __init__(self, cwd):
        self.cwd = cwd

    def _read(self, *args):
        environment = dict((name, value) for name, value in os.environ.items()
                           if not name.startswith("GIT_"))
        environment["LC_ALL"] = "C"
        try:
            completed = subprocess.run(
                ["git", "-C", self.cwd] + list(args),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise GitError("git %s could not run: %s" % (" ".join(args), error))
        if completed.returncode != 0:
            raise GitError("git %s failed with exit %d: %s" % (
                " ".join(args), completed.returncode,
                (completed.stderr or b"").decode("utf-8", "replace").strip()))
        return completed.stdout

    @staticmethod
    def _paths(raw):
        return [part.decode("utf-8", "surrogateescape") for part in raw.split(b"\0") if part]

    def diff_names(self, base):
        return self._paths(self._read(
            "diff", "--name-only", "--no-renames", "-z", base, "HEAD"))

    def staged(self):
        return self._paths(self._read("diff", "--cached", "--name-only", "--no-renames", "-z"))

    def unstaged(self):
        return self._paths(self._read("diff", "--name-only", "--no-renames", "-z"))

    def untracked(self):
        return self._paths(self._read("ls-files", "--others", "--exclude-standard", "-z"))

    def merge_base(self, ref):
        return self._read("merge-base", ref, "HEAD").decode("utf-8", "replace").strip()


class Runner:
    """One argv in its own process group under a deadline (design D9); the running step adds `run`."""

    def __init__(self, clock, wait):
        self.clock = clock
        self.wait = wait

    def run(self, argv, cwd, env, log_path, cutoff, capture):
        raise NotImplementedError("Runner.run is added with the running step (design D9)")


# --- the table (design D2) -------------------------------------------------------------------


def _fail(where, message):
    raise TableError("%s: %s" % (where, message))


def _expect_dict(value, where):
    if not isinstance(value, dict):
        _fail(where, "must be a JSON object")
    return value


def _expect_list(value, where):
    if not isinstance(value, list):
        _fail(where, "must be a list")
    return value


def _expect_str(value, where):
    if not isinstance(value, str) or not value:
        _fail(where, "must be a non-empty string")
    return value


def _string_list(value, where):
    _expect_list(value, where)
    for item in value:
        _expect_str(item, where)
    return list(value)


def _known_fields(record, allowed, where):
    extra = sorted(set(record) - set(allowed))
    if extra:
        _fail(where, "unknown field(s) %s" % ", ".join(extra))


def _repo_relative(value, where, repo):
    """A repository-relative path that resolves inside `repo`."""
    _expect_str(value, where)
    if os.path.isabs(value) or "\\" in value:
        _fail(where, "%r must be a repository-relative path with forward slashes" % value)
    normalized = os.path.normpath(value)
    if normalized == ".." or normalized.startswith(".." + os.sep):
        _fail(where, "%r is outside the repository" % value)
    full = os.path.normpath(os.path.join(repo, normalized))
    if full != repo and not full.startswith(repo + os.sep):
        _fail(where, "%r is outside the repository" % value)
    return normalized


def _input_path(value, where, repo):
    _expect_str(value, where)
    if value.startswith(GRADLE_HOME_PREFIX):
        rest = value[len(GRADLE_HOME_PREFIX):]
        if not rest or rest.startswith("/") or ".." in rest.split("/"):
            _fail(where, "%r is not a valid {gradle_user_home} path" % value)
        return value
    return _repo_relative(value, where, repo)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TableError("duplicate JSON key %r" % key)
        result[key] = value
    return result


def _reject_constant(name):
    raise TableError("%s is not valid JSON" % name)


def _read_strict_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        raise TableError("%s: %s" % (path, error))
    try:
        return json.loads(text, object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except TableError:
        raise
    except ValueError as error:
        raise TableError("%s: %s" % (path, error))


def _check_tool_args(where, resolver, args, repo):
    if resolver == "path-tool":
        _expect_str(args.get("program"), where + " args.program")
        _string_list(args.get("version_cmd"), where + " args.version_cmd")
        _known_fields(args, ("program", "version_cmd", "min", "exact", "pin_file", "cwd"), where)
        for key in ("min", "exact", "pin_file", "cwd"):
            if key in args:
                _expect_str(args[key], "%s args.%s" % (where, key))
    elif resolver == "rustup-toolchain":
        _repo_relative(args.get("toolchain_file"), where + " args.toolchain_file", repo)
        _known_fields(args, ("toolchain_file",), where)


def _load_requirements(raw, repo, path):
    if raw is None:
        raw = {}
    _expect_dict(raw, "%s: requirements" % path)
    requirements = {}
    generated = []
    for req_id in sorted(raw):
        record = raw[req_id]
        where = "requirement %r" % req_id
        _expect_dict(record, where)
        _known_fields(record, ("kind", "resolver", "args", "path", "producer", "fix"), where)
        kind = record.get("kind")
        if kind not in REQUIREMENT_KINDS:
            _fail(where, "unknown kind %r" % (kind,))
        fix = _expect_str(record.get("fix"), where + " fix")
        if kind == "tool":
            resolver = record.get("resolver")
            if resolver not in RESOLVERS:
                _fail(where, "unknown resolver %r" % (resolver,))
            args = record.get("args", {})
            _expect_dict(args, where + " args")
            _check_tool_args(where, resolver, args, repo)
            requirements[req_id] = {"kind": kind, "resolver": resolver,
                                    "args": dict(args), "fix": fix}
        elif kind == "path":
            path_value = _repo_relative(record.get("path"), where + " path", repo)
            requirements[req_id] = {"kind": kind, "path": path_value, "fix": fix}
        else:
            path_value = _repo_relative(record.get("path"), where + " path", repo)
            producer = _expect_str(record.get("producer"), where + " producer")
            generated.append((req_id, producer))
            requirements[req_id] = {"kind": kind, "path": path_value,
                                    "producer": producer, "fix": fix}
    return requirements, generated


def _load_env_matches(record, where):
    exits = []
    for entry in _expect_list(record.get("env_exit", []), where + " env_exit"):
        entry_where = where + " env_exit"
        _expect_dict(entry, entry_where)
        _known_fields(entry, ("code", "fix"), entry_where)
        code = entry.get("code")
        if not isinstance(code, int) or isinstance(code, bool):
            _fail(entry_where, "code must be an integer")
        fix = _expect_str(entry.get("fix"), entry_where + " fix")
        exits.append({"code": code, "fix": fix})
    patterns = []
    for entry in _expect_list(record.get("env_patterns", []), where + " env_patterns"):
        entry_where = where + " env_patterns"
        _expect_dict(entry, entry_where)
        _known_fields(entry, ("regex", "fix"), entry_where)
        regex = _expect_str(entry.get("regex"), entry_where + " regex")
        try:
            re.compile(regex)
        except re.error as error:
            _fail(entry_where, "regex %r is invalid: %s" % (regex, error))
        fix = _expect_str(entry.get("fix"), entry_where + " fix")
        patterns.append({"regex": regex, "fix": fix})
    return exits, patterns


def _load_inputs(record, where, repo):
    if "inputs" not in record:
        return None
    inputs = []
    for entry in _expect_list(record["inputs"], where + " inputs"):
        entry_where = where + " inputs"
        _expect_dict(entry, entry_where)
        _known_fields(entry, ("path", "mode"), entry_where)
        path = _input_path(entry.get("path"), entry_where + " path", repo)
        mode = entry.get("mode", "content")
        if mode not in ("content", "stat"):
            _fail(entry_where, "unknown mode %r" % (mode,))
        inputs.append({"path": path, "mode": mode})
    return inputs


def _load_related(record, where, repo):
    related = record.get("related")
    if related is None:
        return None
    _expect_dict(related, where + " related")
    strategy = related.get("strategy")
    if strategy not in STRATEGY_FIELDS:
        _fail(where, "unknown related strategy %r" % (strategy,))
    fields = STRATEGY_FIELDS[strategy]
    missing = [name for name in fields if name not in related]
    if missing:
        _fail(where, "related strategy %r needs %s" % (strategy, ", ".join(missing)))
    _known_fields(related, ["strategy", "within"] + list(fields), where + " related")
    for name in ("match", "resolve", "run"):
        if name in related:
            _string_list(related[name], "%s related.%s" % (where, name))
            if name == "run" and not related[name]:
                _fail(where, "related.run must be a non-empty list")
    for name in ("sources", "tests"):
        if name in related:
            _expect_str(related[name], "%s related.%s" % (where, name))
    if "crates" in related:
        crates = _expect_dict(related["crates"], where + " related.crates")
        for prefix in sorted(crates):
            _repo_relative(prefix, "%s related.crates" % where, repo)
            _string_list(crates[prefix], "%s related.crates[%r]" % (where, prefix))
            if not crates[prefix]:
                _fail(where, "related.crates[%r] must be a non-empty argv" % (prefix,))
    if "within" in related:
        within = _expect_str(related["within"], where + " related.within")
        if ":" not in within or not within.split(":", 1)[0] or not within.split(":", 1)[1]:
            _fail(where, "related.within must be <file>:<key>, not %r" % (within,))
    return dict(related)


def _check_placeholders(run, where):
    for item in run:
        for token in ("{targets}", "{changed}"):
            if token in item:
                _fail(where, "%s is only allowed inside related" % token)


def _executed_argvs(step):
    argvs = [step.run]
    related = step.related or {}
    if related.get("run"):
        argvs.append(related["run"])
    for argv in (related.get("crates") or {}).values():
        argvs.append(argv)
    return argvs


def _declares_tool(requirement_ids, requirements, resolver, program):
    for req_id in requirement_ids:
        requirement = requirements[req_id]
        if requirement.get("kind") != "tool" or requirement.get("resolver") != resolver:
            continue
        if program is None:
            return True
        if (requirement.get("args") or {}).get("program") == program:
            return True
    return False


def _check_reusable_tools(step, requirements):
    if step.reuse is False:
        return
    for argv in _executed_argvs(step):
        if not argv:
            continue
        program = argv[0]
        if program not in PROGRAM_REQUIREMENTS:
            continue
        resolver = PROGRAM_REQUIREMENTS[program]
        expected = None if resolver == "java-home" else program
        if _declares_tool(step.requires, requirements, resolver, expected):
            continue
        if expected is None:
            _fail(step.id, "is reusable but declares no java-home tool requirement "
                           "for its ./gradlew invocation")
        _fail(step.id, "is reusable but declares no path-tool requirement for %r" % expected)


def _load_step(record, step_id, kind, requirements, repo, where, producer=False):
    allowed = ["id", "kind", "cwd", "run", "doc", "requires", "inputs", "reuse",
               "env_exit", "env_patterns", "ci"]
    if producer:
        allowed.remove("kind")
    else:
        allowed.extend(["platforms", "related"])
    _known_fields(record, allowed, where)

    run = _string_list(record.get("run"), where + " run")
    if not run:
        _fail(where, "run must be a non-empty list of strings")
    _check_placeholders(run, where)
    cwd = _repo_relative(record.get("cwd", "."), where + " cwd", repo)
    doc = _expect_str(record.get("doc"), where + " doc")
    requires = _string_list(record.get("requires", []), where + " requires")
    inputs = _load_inputs(record, where, repo)
    reuse = record.get("reuse", True)
    if not isinstance(reuse, bool):
        _fail(where, "reuse must be true or false")
    if inputs is None and reuse is not False:
        _fail(where, 'must declare inputs or "reuse": false')
    exits, patterns = _load_env_matches(record, where)

    platforms = []
    ci = record.get("ci")
    if kind == "platform":
        platforms = _string_list(record.get("platforms"), where + " platforms")
        if not platforms:
            _fail(where, "a platform command must name its platforms")
        _expect_str(ci, where + " ci")
    else:
        if "platforms" in record:
            _fail(where, "only a platform command names platforms")
        if ci is not None:
            _expect_str(ci, where + " ci")

    related = None
    if "related" in record:
        if kind != "test":
            _fail(where, "related is only allowed on a test command")
        related = _load_related(record, where, repo)

    for req_id in requires:
        if req_id not in requirements:
            _fail(where, "requires unknown requirement %r" % req_id)

    step = Step(
        id=step_id,
        kind="producer" if producer else kind,
        cwd=cwd,
        run=run,
        doc=doc,
        requires=requires,
        inputs=[] if inputs is None else inputs,
        reuse=reuse,
        env_exit=exits,
        env_patterns=patterns,
        platforms=platforms,
        ci=ci,
        related=related,
    )
    _check_reusable_tools(step, requirements)
    return step


def _load_excluded(raw, repo, where):
    excluded = []
    for entry in _expect_list(raw, where + " excluded"):
        entry_where = where + " excluded"
        _expect_dict(entry, entry_where)
        _known_fields(entry, ("doc", "cwd", "line", "why"), entry_where)
        doc = _expect_str(entry.get("doc"), entry_where + " doc")
        line = _expect_str(entry.get("line"), entry_where + " line")
        why = _expect_str(entry.get("why"), entry_where + " why")
        cwd = _repo_relative(entry.get("cwd", "."), entry_where + " cwd", repo)
        excluded.append({"doc": doc, "cwd": cwd, "line": line, "why": why})
    return excluded


def _load_nodes(raw, requirements, repo, cmap, seen):
    _expect_dict(raw, "nodes")
    expected = set((cmap or {}).get("nodes") or {})
    actual = set(raw)
    if actual != expected:
        parts = []
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing:
            parts.append("missing node(s) %s" % ", ".join(missing))
        if extra:
            parts.append("unknown node(s) %s" % ", ".join(extra))
        _fail("nodes", "%s (the component map defines %s)"
              % ("; ".join(parts), ", ".join(sorted(expected))))
    nodes = {}
    for name in sorted(raw):
        record = raw[name]
        where = "node %r" % name
        _expect_dict(record, where)
        _known_fields(record, ("commands", "excluded", "none"), where)
        commands = []
        for entry in _expect_list(record.get("commands", []), where + " commands"):
            _expect_dict(entry, where + " command")
            step_id = _expect_str(entry.get("id"), where + " command id")
            command_where = "command %r" % step_id
            if step_id in seen:
                _fail(command_where, "duplicate id (already used by %s)" % seen[step_id])
            seen[step_id] = command_where
            kind = entry.get("kind")
            if kind not in STEP_KINDS:
                _fail(command_where, "unknown kind %r" % (kind,))
            commands.append(_load_step(entry, step_id, kind, requirements, repo,
                                       command_where, producer=False))
        excluded = _load_excluded(record.get("excluded", []), repo, where)
        none = record.get("none")
        if commands:
            if none is not None:
                _fail(where, "'none' is only allowed when commands is empty")
        else:
            _expect_str(none, where + " none")
        nodes[name] = NodeEntry(commands=commands, excluded=excluded, none=none)
    return nodes


def load_table(path, cmap):
    """Load and fully validate `path` against the component map `cmap` (design D2)."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    data = _read_strict_json(path)
    _expect_dict(data, path)
    _known_fields(data, ("version", "requirements", "producers", "nodes"), path)
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        _fail(path, "version must be 1")
    requirements, generated = _load_requirements(data.get("requirements"), repo, path)

    producers_raw = data.get("producers", [])
    _expect_list(producers_raw, "%s: producers" % path)
    producers = {}
    seen = {}
    for entry in producers_raw:
        _expect_dict(entry, "%s: producer" % path)
        step_id = _expect_str(entry.get("id"), "%s: producer id" % path)
        where = "producer %r" % step_id
        if step_id in seen:
            _fail(where, "duplicate id (already used by %s)" % seen[step_id])
        seen[step_id] = where
        producers[step_id] = _load_step(entry, step_id, "producer", requirements, repo,
                                        where, producer=True)
    for req_id, producer in generated:
        if producer not in producers:
            _fail("requirement %r" % req_id, "names unknown producer %r" % producer)
    nodes = _load_nodes(data.get("nodes"), requirements, repo, cmap, seen)
    return Table(requirements=requirements, producers=producers, nodes=nodes)


# --- the CLI ------------------------------------------------------------------------------


class UsageError(Exception):
    """A command line the verifier cannot act on; main returns 2."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def make_parser():
    parser = _ArgumentParser(
        prog="verify.py",
        description="Run the affected nodes' checks (task gate by default; --branch before a push).",
    )
    gate = parser.add_mutually_exclusive_group()
    gate.add_argument("--branch", action="store_true",
                      help="run every selected node's checks and all of its tests")
    gate.add_argument("--hook", choices=("claude",),
                      help="run as the Claude Code Stop hook")
    parser.add_argument("--list", "--dry-run", dest="list_only", action="store_true",
                        help="print the selection and the plan without running it")
    parser.add_argument("--base", metavar="SHA", help="diff against this commit instead of origin/master's merge base")
    parser.add_argument("--deadline", type=float, metavar="EPOCH",
                        help="internal: absolute deadline in clock seconds")
    parser.add_argument("--dispatched", action="store_true",
                        help="internal: the hook gate owner; never re-dispatch")
    parser.add_argument("--report-fd", type=int, metavar="FD",
                        help="internal: the pipe the hook gate owner reports its deadline on")
    subcommands = parser.add_subparsers(dest="command")
    config = subcommands.add_parser("config", help="machine tool config")
    config_commands = config.add_subparsers(dest="config_command")
    setter = config_commands.add_parser("set", help="validate and save one key")
    setter.add_argument("key")
    setter.add_argument("value")
    config_commands.add_parser("show", help="print the config and what it resolves to")
    return parser


def _mode_name(args):
    if args.hook:
        return "hook"
    if args.list_only:
        return "list"
    if args.branch:
        return "branch"
    return "task"


def _unwired(args, ctx):
    """Selection, plans, probes, running and the hook land in Tasks 4-8 and 13."""
    ctx.log("verify: the %s mode is not built yet" % _mode_name(args))
    return 2


def _config_mode(args, ctx):
    """The machine config lands with the probe step (design D8)."""
    ctx.log("verify: config is not built yet")
    return 2


def main(argv, ctx):
    """Parse the CLI, validate the table, then dispatch. Returns the process exit code."""
    parser = make_parser()
    try:
        args = parser.parse_args(list(argv))
    except UsageError as error:
        ctx.log("verify: %s" % error)
        return 2
    except SystemExit as exit_code:  # --help and --version
        return exit_code.code if isinstance(exit_code.code, int) else 0
    if args.command == "config":
        if args.branch or args.list_only or args.hook:
            ctx.log("verify: config cannot be combined with a gate")
            return 2
        if args.config_command not in ("set", "show"):
            ctx.log("verify: config needs 'set <key> <value>' or 'show'")
            return 2
        return _config_mode(args, ctx)
    if args.hook and args.list_only:
        ctx.log("verify: --list cannot be combined with --hook")
        return 2
    if args.deadline is not None:
        ctx.deadline = args.deadline
    try:
        cmap = components.load_map(os.path.join(ctx.repo, ".github", "components.json"))
        load_table(os.path.join(ctx.repo, "scripts", "verify-commands.json"), cmap)
    except TableError as error:
        ctx.log("verify: %s" % error)
        return 2
    except (OSError, ValueError) as error:
        ctx.log("verify: %s" % error)
        return 2
    return _unwired(args, ctx)


def _stderr(line):
    print(line, file=sys.stderr)


def real_context(env=None):
    """The production wiring: real clock, wait, git, filesystem and runner for this checkout."""
    clock = Clock()
    wait = Wait()
    environ = dict(os.environ if env is None else env)
    discovered = agent_rules.discover_checkout(os.getcwd())
    repo = discovered[0] if discovered else os.getcwd()
    common_dir = os.path.realpath(discovered[1]) if discovered else os.path.realpath(repo)
    return Context(
        git=Git(repo),
        runner=Runner(clock, wait),
        clock=clock,
        wait=wait,
        fs=Fs(),
        env=environ,
        repo=repo,
        common_dir=common_dir,
        run_dir=os.path.join(repo, "build", "verify", "runs", "manual"),
        deadline=None,
        log=_stderr,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], real_context()))
