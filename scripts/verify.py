#!/usr/bin/env python3
"""verify: run the affected nodes' commands, the task gate by default and `--branch` before a push.

Standard library only, Python 3.8 compatible, run as `python3 -I scripts/verify.py`. The node
commands live in `scripts/verify-commands.json` (design D2); this module loads and validates that
table, owns the injected clock/wait/runner/git/filesystem seams, and wires the CLI modes. See
`openspec/changes/verify-affected-nodes-stop-hook/design.md` D1-D14.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

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
# A list-only target resolver runs under its own short limit (design D1/D4/D9).
RESOLVER_SECONDS = 60
# The last 45 seconds of a run are reserved for termination, settlement and the report (D9).
DEADLINE_RESERVE_SECONDS = 45
DEVICE_NOTE = "NOT RUN (device test; real-device rule)"
BRANCH_ONLY_NOTE = "NOT RUN (branch gate only: %s)"
NONE_RELATED_NOTE = "tests: none related (%s)"
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
# `related.run` expansion (design D4): a bare `{targets}` item becomes one argv item per resolved
# target, and an item that contains `{targets}` is copied once per target with the placeholder
# replaced -- so `--tests={targets}` produces one `--tests=<fqn>` flag per target, and
# `python3 -m unittest {targets}` receives every target as its own argv item.
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

    def commit(self, ref):
        """The full sha `ref` names, when it names a commit (design D3's base check)."""
        return self._read("rev-parse", "--verify", "--quiet",
                          ref + "^{commit}").decode("utf-8", "replace").strip()


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


# --- selection (design D3) ------------------------------------------------------------------


BASE_FIX = "run `git fetch origin master`, or pass `--base <sha>` with a commit that exists"
GIT_READ_FIX = "check that git can read this checkout (`git status`), then run verify again"
UNACCOUNTED_FIX = "add them to .github/components.json as paths, global or unowned"


class SelectionError(Exception):
    """Selection cannot proceed; the CLI exits 2 and the hook blocks with `.fix` (design D3/D12)."""

    def __init__(self, kind, paths=None, fix="", base=None, detail=None):
        self.kind = kind
        self.paths = list(paths or [])
        self.fix = fix
        self.base = base
        self.detail = detail
        if kind == "unaccounted":
            message = "not owned, global or unowned: %s" % ", ".join(self.paths)
        elif kind == "base":
            message = "cannot resolve the base: %s" % (detail or self.base or "origin/master")
        elif kind == "git":
            message = "git read failed: %s" % (detail or "unknown error")
        else:
            message = detail or kind
        super().__init__(message)

    @property
    def identity(self):
        """The repeat key's last element: the base itself, else a digest of the sorted paths."""
        if self.kind == "base":
            key = self.base or "origin/master"
        else:
            joined = "\n".join(sorted(self.paths))
            key = hashlib.sha256(joined.encode("utf-8")).hexdigest()
        return ("selection", self.kind, key)


@dataclass(frozen=True)
class Selection:
    """Changed files and the selected nodes, each with the first file that selected it."""

    base: str
    files: List[str]
    reasons: Dict[str, str]
    only_unowned: bool = False


def _resolve_base(git: Git, base: Optional[str]) -> str:
    """The base commit: `--base` resolved to a commit, else `origin/master`'s merge base."""
    label = base or "origin/master"
    try:
        if base:
            resolved = git.commit(base).strip()
        else:
            resolved = git.merge_base("origin/master").strip()
    except GitError as error:
        raise SelectionError("base", fix=BASE_FIX, base=label, detail=str(error))
    if not resolved:
        raise SelectionError("base", fix=BASE_FIX, base=label,
                             detail="%s is not a commit" % label)
    return resolved


def select(git: Git, cmap: dict, base: Optional[str] = None) -> Selection:
    """Changed files -> selected nodes with a reason each (design D3)."""
    resolved = _resolve_base(git, base)
    try:
        files = set(git.diff_names(resolved))
        files.update(git.staged())
        files.update(git.unstaged())
        files.update(git.untracked())
    except GitError as error:
        raise SelectionError("git", fix=GIT_READ_FIX, detail=str(error))
    files = sorted(files)
    bad = components.unaccounted(files, cmap)
    if bad:
        raise SelectionError("unaccounted", paths=bad, fix=UNACCOUNTED_FIX)
    reasons = {}
    all_nodes = list(cmap["nodes"])
    for path in files:
        owners = components.owner_nodes(path, cmap)
        if owners is None:  # global or unaccounted (unaccounted was ruled out above)
            owners = all_nodes
        for node in owners:
            if node not in reasons:
                reasons[node] = path
    return Selection(base=resolved, files=files, reasons=reasons,
                     only_unowned=bool(files) and not reasons)


# --- plans, generated outputs and related tests (design D4/D5) ------------------------------


@dataclass
class PlannedStep:
    """One argv the plan will run, with the consumer whose chain placed a producer here."""

    step: Step
    argv: List[str]
    consumer_of: Optional[str] = None


@dataclass
class NodePlan:
    steps: List[PlannedStep] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    unresolved: List[Tuple[str, Outcome]] = field(default_factory=list)


@dataclass
class Plan:
    nodes: Dict[str, NodePlan] = field(default_factory=dict)


def _abs_path(ctx, relative):
    return os.path.normpath(os.path.join(ctx.repo, relative))


def _relative(ctx, full):
    """`full` relative to the repository root, or "" when it lies outside it."""
    relative = os.path.relpath(full, ctx.repo).replace(os.sep, "/")
    if relative == ".." or relative.startswith("../"):
        return ""
    return relative


def _norm_target(value):
    """A target in the plan's canonical form: forward slashes, no `./`, no trailing `/`."""
    value = value.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    value = value.rstrip("/")
    return os.path.normpath(value).replace(os.sep, "/")


def _exists(ctx, relative):
    return bool(relative) and ctx.fs.exists(_abs_path(ctx, relative))


def _isdir(ctx, relative):
    return bool(relative) and ctx.fs.isdir(_abs_path(ctx, relative))


def _under(path, prefix):
    trimmed = prefix.rstrip("/")
    return path == trimmed or path.startswith(trimmed + "/")


def _past_cutoff(ctx):
    """True once starting more work would eat the run's reserved termination time (D9)."""
    return (ctx.deadline is not None
            and ctx.clock.now() >= ctx.deadline - DEADLINE_RESERVE_SECONDS)


def _resolver_cutoff(ctx):
    cutoff = ctx.clock.now() + RESOLVER_SECONDS
    if ctx.deadline is not None:
        cutoff = min(cutoff, ctx.deadline - DEADLINE_RESERVE_SECONDS)
    return cutoff


def _stem(relative):
    name = relative.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def _glob_regex(pattern):
    """A path glob; `**` crosses directories and `<stem>` matches inside a name (design D4)."""
    out = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern.startswith("<stem>", index):
            out.append("[^/]*")
            index += 6
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("^" + "".join(out) + "$")


def _walk(ctx, root):
    """Every regular file under `root`, repository-relative; no hit once the reserve is reached."""
    if not root:
        return []
    found = []
    for dirpath, _dirnames, filenames in ctx.fs.walk(_abs_path(ctx, root)):
        for name in filenames:
            found.append(_relative(ctx, os.path.join(dirpath, name)))
        if _past_cutoff(ctx):
            return []
    return sorted(name for name in found if name)


def _expand_template(ctx, pattern):
    """The existing files matching a test-file template (its `**` and `*` are wildcards)."""
    if "*" not in pattern and "<stem>" not in pattern:
        return [pattern] if _exists(ctx, pattern) else []
    regex = _glob_regex(pattern)
    fixed = []
    for part in pattern.split("/"):
        if "*" in part or "<stem>" in part:
            break
        fixed.append(part)
    if not fixed:
        return []
    return [name for name in _walk(ctx, "/".join(fixed)) if regex.match(name)]


def _kotlin_class(ctx, relative):
    """`package` line plus the file name, the FQN Gradle `--tests` takes, or None."""
    try:
        text = ctx.fs.read_text(_abs_path(ctx, relative))
    except (OSError, KeyError):
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("package "):
            package = stripped[len("package "):].strip().rstrip(";").strip()
            if package:
                return package + "." + _stem(relative)
            break
    return None


def _as_target(ctx, relative):
    if not relative.endswith(".kt"):
        return relative
    return _kotlin_class(ctx, relative)


def _dedupe(values):
    seen = set()
    unique = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _self_targets(test, changed, ctx):
    regex = _glob_regex(test.related["tests"])
    return _dedupe([path for path in changed if _exists(ctx, path) and regex.match(path)])


def _name_map_targets(test, changed, ctx):
    related = test.related
    sources = _glob_regex(related["sources"])
    tests = _glob_regex(related["tests"])
    targets = []
    for path in changed:
        if not _exists(ctx, path):
            continue  # a deleted or moved-away path selects the node, never a target
        if tests.match(path):
            target = _as_target(ctx, path)
            if target:
                targets.append(target)
        elif sources.match(path):
            pattern = related["tests"].replace("<stem>", _stem(path))
            for found in _expand_template(ctx, pattern):
                target = _as_target(ctx, found)
                if target:
                    targets.append(target)
    return _dedupe(targets)


def _crate_targets(test, changed, ctx):
    targets = []
    for prefix in test.related["crates"]:
        if not _isdir(ctx, prefix):
            continue
        if any(_under(path, prefix) for path in changed):
            targets.append(prefix)
    return targets


def _expand_changed(template, changed):
    argv = []
    for item in template:
        if "{changed}" not in item:
            argv.append(item)
        elif item == "{changed}":
            argv.extend(changed)
        else:
            argv.extend(item.replace("{changed}", value) for value in changed)
    return argv


def _existing_target(ctx, test, value):
    """A resolver's line as a repository-relative path that exists, else None."""
    if os.path.isabs(value):
        candidates = [_relative(ctx, os.path.normpath(value))]
    else:
        candidates = [_norm_target(value)]
        if test.cwd not in (".", ""):
            candidates.append(_norm_target(test.cwd + "/" + value))
    for candidate in candidates:
        if _exists(ctx, candidate):
            return candidate
    return None


def _files_arg_targets(test, changed, ctx, resolve):
    """Run the list-only resolver and read its targets from stdout only (design D4/D9)."""
    related = test.related
    files = [path for path in changed
             if _exists(ctx, path) and any(_under(path, prefix) for prefix in related["match"])]
    if not files:
        return [], None
    argv = _expand_changed(related["resolve"],
                           [_relative_to_cwd(test, path) for path in files])
    outcome = resolve(argv, _abs_path(ctx, test.cwd), dict(ctx.env),
                      os.path.join(ctx.run_dir, test.id + ".resolve.log"),
                      _resolver_cutoff(ctx), True)
    if outcome.kind != "ok":
        if outcome.kind == "timeout":
            return [], outcome
        return [], replace(outcome, kind="not_run",
                           error=outcome.error or "the resolver did not complete")
    targets = []
    for line in outcome.stdout or []:
        value = line.strip()
        if not value:
            continue
        target = _existing_target(ctx, test, value)
        if target is not None:
            targets.append(target)
    return _dedupe(targets), None


def _within_allowlist(test, ctx):
    """The files named by `<file>:<key>` (design D4's `within`), or None when absent.

    An unreadable allowlist yields an empty set: no target may run without it, and the caller
    turns that into a `tests: none related` note rather than guessing.
    """
    within = (test.related or {}).get("within")
    if not within:
        return None
    file_part, _separator, key = within.partition(":")
    candidates = []
    if test.cwd not in (".", ""):
        candidates.append(_norm_target(test.cwd + "/" + file_part))
    candidates.append(_norm_target(file_part))
    relative = None
    for candidate in candidates:
        if _exists(ctx, candidate):
            relative = candidate
            break
    if relative is None:
        return set()
    try:
        data = json.loads(ctx.fs.read_text(_abs_path(ctx, relative)))
    except (OSError, KeyError, ValueError):
        return set()
    value = data
    for name in key.split("."):
        if not isinstance(value, dict) or name not in value:
            return set()
        value = value[name]
    if isinstance(value, str):
        try:
            tokens = shlex.split(value)
        except ValueError:
            tokens = value.split()
    elif isinstance(value, list):
        tokens = [str(item) for item in value]
    else:
        return set()
    base = relative.rsplit("/", 1)[0] if "/" in relative else ""
    allowed = set()
    for token in tokens:
        allowed.add(_norm_target(token))
        if base:
            allowed.add(_norm_target(base + "/" + token))
    return allowed


def _relative_to_cwd(test, target):
    """`{targets}` items are relative to the step's `cwd`; a FQN is passed through unchanged."""
    cwd = test.cwd
    if cwd in (".", ""):
        return target
    if target == cwd:
        return "."
    if target.startswith(cwd + "/"):
        return target[len(cwd) + 1:]
    return target


def resolve_targets(test, changed, ctx, resolve):
    """An explicit list of existing test targets (design D4), or the resolver's failure.

    Returns `(targets, None)` on success and `([], outcome)` when a list-only resolver did not
    complete: `timeout` when it was cut off, `not_run` for any other failure (design D4/D9).
    A `crate` strategy returns the matched crate prefixes, which `plan` maps through
    `related.crates` (it is the one strategy with no `run` template).
    """
    related = test.related or {}
    strategy = related.get("strategy")
    if strategy == "files-arg":
        targets, failure = _files_arg_targets(test, changed, ctx, resolve)
        if failure is not None:
            return [], failure
    elif strategy == "name-map":
        targets = _name_map_targets(test, changed, ctx)
    elif strategy == "self":
        targets = _self_targets(test, changed, ctx)
    elif strategy == "crate":
        targets = _crate_targets(test, changed, ctx)
    else:
        raise ValueError("unknown related strategy %r" % (strategy,))
    allow = _within_allowlist(test, ctx)
    if allow is not None:
        targets = [target for target in targets if _norm_target(target) in allow]
    if strategy == "crate":
        return targets, None
    return [_relative_to_cwd(test, target) for target in targets], None


def _expand_targets(template, targets):
    """R8: `--tests={targets}` repeats once per target; a bare `{targets}` is one item each."""
    argv = []
    for item in template:
        if "{targets}" not in item:
            argv.append(item)
        elif item == "{targets}":
            argv.extend(targets)
        else:
            argv.extend(item.replace("{targets}", target) for target in targets)
    return argv


def _narrowed_argvs(test, targets):
    related = test.related
    if related.get("strategy") == "crate":
        argvs = []
        for target in targets:
            argv = list(related["crates"][target])
            if argv not in argvs:
                argvs.append(argv)
        return argvs
    return [_expand_targets(related["run"], targets)]


def _needs_generated(table, step):
    return any(table.requirements[req_id].get("kind") == "generated"
               for req_id in step.requires)


def _producer_chain(table, step):
    """Every producer `step` needs, dependencies first, deduplicated within this one chain."""
    chain = []
    seen = set()

    def visit(current):
        for req_id in current.requires:
            requirement = table.requirements[req_id]
            if requirement.get("kind") != "generated":
                continue
            build = table.producers[requirement["producer"]]
            if build.id in seen:
                continue
            seen.add(build.id)
            visit(build)
            chain.append(build)

    visit(step)
    return chain


def _platform_label(platforms):
    known = set(platforms)
    if known == {"macos"}:
        return "macOS only"
    if known == {"windows"}:
        return "Windows only"
    names = [name for name in ("macOS", "Windows")
             if (name == "macOS" and "macos" in known) or (name == "Windows" and "windows" in known)]
    return " and ".join(names or sorted(known)) + " only"


def _platform_note(step):
    return "NOT RUN (%s; CI job %s)" % (_platform_label(step.platforms), step.ci)


def plan(table, sel, gate, ctx, resolve):
    """Nodes × gate → ordered steps, expected-skip notes and unresolved target lists (design D4)."""
    if gate not in ("task", "branch"):
        raise ValueError("unknown gate %r" % (gate,))
    result = Plan(nodes={})
    for node in table.nodes:
        if node not in sel.reasons:
            continue
        node_plan = NodePlan()
        early = []
        late = []
        for step in table.nodes[node].commands:
            if step.kind == "device":
                node_plan.notes.append(DEVICE_NOTE)
                continue
            if step.kind == "platform":
                node_plan.notes.append(_platform_note(step))
                continue
            if step.kind == "test":
                if gate == "branch":
                    argvs = [list(step.run)]
                elif step.related is not None:
                    targets, failure = resolve_targets(step, sel.files, ctx, resolve)
                    if failure is not None:
                        node_plan.unresolved.append((step.id, failure))
                        continue
                    if not targets:
                        # The note names the run's changed files: what this test covered nothing of.
                        node_plan.notes.append(NONE_RELATED_NOTE % ", ".join(sel.files))
                        continue
                    argvs = _narrowed_argvs(step, targets)
                else:
                    node_plan.notes.append(BRANCH_ONLY_NOTE % step.id)
                    continue
            else:
                argvs = [list(step.run)]
            (late if _needs_generated(table, step) else early).append((step, argvs))
        # D4/D5: steps that need no generated output first, in table order; then each consumer
        # with its whole producer chain immediately before it, every time it runs.
        for step, argvs in early:
            for argv in argvs:
                node_plan.steps.append(PlannedStep(step=step, argv=argv))
        for step, argvs in late:
            for build in _producer_chain(table, step):
                node_plan.steps.append(PlannedStep(step=build, argv=list(build.run),
                                                   consumer_of=step.id))
            for argv in argvs:
                node_plan.steps.append(PlannedStep(step=step, argv=argv))
        result.nodes[node] = node_plan
    return result


def _outcome_reason(outcome):
    if outcome.error:
        return outcome.error
    if outcome.fix:
        return outcome.fix
    if outcome.tail:
        return outcome.tail[-1].strip()
    if outcome.kind == "timeout":
        return "the resolver timed out"
    return "the resolver did not complete (%s)" % outcome.kind


def _argv_text(argv):
    return " ".join(shlex.quote(item) for item in argv)


def render_plan(sel, result):
    """`--list`'s lines: each node, its reason, every step's argv and cwd, notes, unresolved."""
    lines = []
    for node, node_plan in result.nodes.items():
        lines.append("%s: selected by %s" % (node, sel.reasons.get(node, "")))
        for planned in node_plan.steps:
            lines.append("  %s: cd %s && %s" % (
                planned.step.id, planned.step.cwd, _argv_text(planned.argv)))
        for step_id, outcome in node_plan.unresolved:
            lines.append("  %s: targets unresolved: %s" % (step_id, _outcome_reason(outcome)))
        for note in node_plan.notes:
            lines.append("  %s" % note)
    return lines


def _list_mode(args, ctx, cmap, table):
    """D1: selection and planning only; no probe, producer or command runs."""
    gate = "branch" if args.branch else "task"
    try:
        selection_result = select(ctx.git, cmap, args.base)
    except SelectionError as error:
        ctx.log("verify: %s" % error)
        if error.fix:
            ctx.log("verify: fix: %s" % error.fix)
        return 2
    if not selection_result.files:
        ctx.log("verify: nothing changed against %s" % selection_result.base)
        return 0
    if selection_result.only_unowned:
        ctx.log("verify: only unowned paths changed (%s)"
                % ", ".join(selection_result.files))
        return 0
    result = plan(table, selection_result, gate, ctx, ctx.runner.run)
    for line in render_plan(selection_result, result):
        ctx.log(line)
    return 0


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
        table = load_table(os.path.join(ctx.repo, "scripts", "verify-commands.json"), cmap)
    except TableError as error:
        ctx.log("verify: %s" % error)
        return 2
    except (OSError, ValueError) as error:
        ctx.log("verify: %s" % error)
        return 2
    if args.list_only:
        return _list_mode(args, ctx, cmap, table)
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
