#!/usr/bin/env python3
"""verify: run the affected nodes' commands, the task gate by default and `--branch` before a push.

Standard library only, Python 3.8 compatible, run as `python3 -I scripts/verify.py`. The node
commands live in `scripts/verify-commands.json` (design D2); this module loads and validates that
table, owns the injected clock/wait/runner/git/filesystem seams, and wires the CLI modes. See
`openspec/changes/verify-affected-nodes-stop-hook/design.md` D1-D14.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import io
import json
import os
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(SCRIPTS)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import agent_rules  # noqa: E402
import branch_pass  # noqa: E402
import components  # noqa: E402

TABLE_PATH = os.path.join(SCRIPTS, "verify-commands.json")
COMPONENT_MAP_PATH = os.path.join(REPO, ".github", "components.json")
GIT_TIMEOUT_SECONDS = 120
GRADLE_HOME_PREFIX = "{gradle_user_home}/"
# A list-only target resolver runs under its own short limit (design D1/D4/D9).
RESOLVER_SECONDS = 60
# The last 45 seconds of a run are reserved for termination, settlement and the report (D9).
DEADLINE_RESERVE_SECONDS = 45
# The table configures no per-step time limit, so only the run deadline cuts a step short (D9).
STEP_LIMIT_SECONDS = float("inf")
# Contention and a deadline that passed between two steps (D9/D10).
CONTENTION = "verification already running"
ORPHAN_CONTENTION = "a previous verification's process group could not be stopped"
NOT_REACHED_REASON = "the run deadline was reached before every step ran"
# Running (D9): the poll interval, the TERM-to-KILL grace, and the tail the report shows.
POLL_SECONDS = 0.1
TERM_GRACE_SECONDS = 10.0
# A killed group can still hold a zombie grandchild for a moment; give it this long to settle.
KILL_SETTLE_SECONDS = 1.0
TAIL_LINES = 60
TAIL_CHARS = 300
# Orphan cleanup (D9): SIGKILL after the grace, contention when a group is still alive after 15 s.
ORPHAN_GRACE_SECONDS = 10.0
ORPHAN_CONFIRM_SECONDS = 15.0
RUNNING_VERSION = 1
# A short state lock (design D9): retried through the injected wait for at most 10 seconds.
STATE_LOCK_SECONDS = 10.0
# The report (D10) and the run directories it keeps.
SUMMARY_CAP = 8192
KEEP_RUNS = 10
LAST_RUN_VERSION = 1
LAST_RUN_POINTER = "build/verify/last-run.json"
# Remembered passes (design D6): the canonical document's version and the environment allowlist.
FINGERPRINT_VERSION = 1
PASSES_POINTER = "passes.json"
ENV_VARS = frozenset((
    "PATH", "HOME", "JAVA_HOME", "JAVA_OPTS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS",
    "ANDROID_HOME", "ANDROID_SDK_ROOT", "ANDROID_NDK_HOME", "CI", "RUSTFLAGS", "RUSTC",
))
ENV_PREFIXES = (
    "GRADLE_", "ORG_GRADLE_", "JAVA_TOOL_", "NODE_", "NPM_CONFIG_", "npm_config_",
    "CARGO_", "RUSTUP_", "VELES_", "PLAYWRIGHT_",
)
GRADLE_USER_HOME_OPTION = "gradle.user.home"
DEVICE_NOTE = "NOT RUN (device test; real-device rule)"
BRANCH_ONLY_NOTE = "NOT RUN (branch gate only: %s)"
NONE_RELATED_NOTE = "tests: none related (%s)"
TARGET_RESOLUTION_TIMEOUT = "target resolution reached the deadline reserve"
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

# The Stop hook (design D11/D12): the outer deadline, the watchdog's grace, the event cap, the
# per-session backstop, the reservation grace, the repeat bound and the state-file caps.
HOOK_RUN_BUDGET_SECONDS = 1740.0
HOOK_OUTER_SECONDS = 1740.0
HOOK_KILL_GRACE_SECONDS = 30.0
EVENT_CAP = 1048576
HOOK_STATE_VERSION = 2
HOOK_STATE_POINTER = "hook-state.json"
MAX_SESSION_SECONDS = 3600.0
MAX_SESSION_BLOCKS = 5
MIN_RUN_SECONDS = 60.0
MAX_REPEATS = 3
RESERVATION_GRACE_SECONDS = 120.0
KEEP_SESSIONS = 50
SYNOPSIS_CAP = 2048
HOOK_CONTENTION = "verification already running; nothing was checked"
HOOK_LIMIT_NOTICE = "verify: this session's hook limits were reached; not running the task gate"
HOOK_INCOMPLETE_NOTICE = "verify: the task gate stopped at its deadline; nothing was blocked"
HOOK_BLOCK_LEAD = ("verify: the task gate found problems; fix them and stop again "
                   "(attempt %d of %d)")
HOOK_REPEAT_LEAD = ("verify: the task gate still has problems; not blocking again "
                    "(attempt %d of %d)")


class TableError(ValueError):
    """The command table is invalid; the message names the problem and the offending id."""


class _DeadlineReached(Exception):
    """A filesystem walk stopped inside the run's reserved termination window (design D9)."""


class _FingerprintUnreadable(Exception):
    """A fingerprint read could not complete; the answer is "no hit", never an error (D6)."""


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
    # The step's log path, so the report can name it in a failure block (design D10).
    log: Optional[str] = None


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
    # The launcher's checkout discovery (design D11); injected so tests never run git.
    discover: Callable[[str], Optional[Tuple[str, str]]] = agent_rules.discover_checkout


Run = Callable[[List[str], str, Dict[str, str], str, float, bool], Outcome]
# With `capture=True` the runner returns the command's stdout and stderr separately in the
# Outcome (in addition to logging them), so a probe can read `java -version` from stderr and a
# version command's first line from stdout (design D7/D9).


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

    def lstat(self, path):
        return os.lstat(path)

    def islink(self, path):
        return os.path.islink(path)

    def is_executable(self, path):
        return os.path.isfile(path) and os.access(path, os.X_OK)


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

    def head(self):
        """The HEAD commit, which a branch pass is bound to (design D2)."""
        return self._read("rev-parse", "HEAD").decode("utf-8", "replace").strip()

    def status_porcelain(self):
        """`git status --porcelain -z` as text; empty for a clean tree (design D2's completeness)."""
        return self._read("status", "--porcelain", "-z").decode("utf-8", "surrogateescape")

    def commit(self, ref):
        """The full sha `ref` names, when it names a commit (design D3's base check)."""
        return self._read("rev-parse", "--verify", "--quiet",
                          ref + "^{commit}").decode("utf-8", "replace").strip()

    def ls_files(self):
        """Every index entry as path -> (mode, blob id), from `git ls-files -s -z` (design D6)."""
        entries = {}
        for record in self._read("ls-files", "-s", "-z").split(b"\0"):
            if not record:
                continue
            meta, separator, path = record.partition(b"\t")
            if not separator:
                continue
            parts = meta.decode("ascii", "replace").split()
            if len(parts) != 3 or parts[2] != "0":
                continue  # an unmerged entry has no stable index content
            entries[path.decode("utf-8", "surrogateescape")] = (parts[0], parts[1])
        return entries


class Processes:
    """The operating-system process API the runner drives (design D9); tests inject a fake."""

    def spawn(self, argv, cwd, env, stdout, stderr):
        """`argv` in its own session (one process group) with stdin closed and open outputs."""
        options = {}
        if os.name == "posix":
            options["start_new_session"] = True
        else:  # Windows: one process group per call, as design D9 asks
            options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=stdout, stderr=stderr, **options)

    def signal_group(self, pgid, sig):
        """Send `sig` to the whole group; a group that is already gone is not an error."""
        try:
            if os.name == "posix":
                os.killpg(pgid, sig)
            else:
                os.kill(pgid, sig)
        except ProcessLookupError:
            pass

    def group_alive(self, pgid):
        """True while any process of the group exists (design D9's `killpg(pgid, 0)` check)."""
        try:
            if os.name == "posix":
                os.killpg(pgid, 0)
            else:
                os.kill(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _write_json(path, document):
    """Write `document` atomically: a temporary file in the same directory, then `os.replace`."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".verify.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _running_path_for(log_path):
    """`running.json` and the run id for a log at `<verify>/runs/<run id>/<step>.log` (D9)."""
    run_dir = os.path.dirname(os.path.abspath(log_path))
    verify_dir = os.path.dirname(os.path.dirname(run_dir))
    return os.path.join(verify_dir, "running.json"), os.path.basename(run_dir)


def _running_groups(path):
    """Every `{"run": <id>, "pgid": <int>}` entry in `running.json`; anything else is ignored."""
    document = _read_json(path)
    if not isinstance(document, dict):
        return []
    entries = document.get("groups")
    if not isinstance(entries, list):
        return []
    groups = []
    for entry in entries:
        if (isinstance(entry, dict) and isinstance(entry.get("run"), str)
                and isinstance(entry.get("pgid"), int)
                and not isinstance(entry.get("pgid"), bool)):
            groups.append({"run": entry["run"], "pgid": entry["pgid"]})
    return groups


def _write_running(path, groups):
    """Save the running groups; no entries means no file (D9: removed once every group is gone)."""
    if not groups:
        try:
            os.unlink(path)
        except OSError:
            return
        return
    _write_json(path, {"version": RUNNING_VERSION, "groups": groups})


def _record_running(log_path, pgid):
    """Record one running group; a write that cannot complete is best-effort, never fatal."""
    path, run_id = _running_path_for(log_path)
    groups = [group for group in _running_groups(path)
              if not (group["run"] == run_id and group["pgid"] == pgid)]
    groups.append({"run": run_id, "pgid": pgid})
    try:
        _write_running(path, groups)
    except OSError:
        pass


def _forget_running(log_path, pgid):
    """Remove one reaped group from `running.json`; best-effort, like `_record_running`."""
    path, run_id = _running_path_for(log_path)
    groups = [group for group in _running_groups(path)
              if not (group["run"] == run_id and group["pgid"] == pgid)]
    try:
        _write_running(path, groups)
    except OSError:
        pass


def _stream_bytes(handle):
    try:
        handle.seek(0)
        return handle.read()
    except (OSError, ValueError, AttributeError):
        return b""


def _lines(data):
    return data.decode("utf-8", "replace").splitlines() if data else []


def _tail(log_path):
    """The last `TAIL_LINES` lines of the log, each cut to `TAIL_CHARS` characters (design D9)."""
    try:
        with open(log_path, "rb") as handle:
            data = handle.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return [line[:TAIL_CHARS] for line in lines[-TAIL_LINES:]]


class Runner:
    """One argv in its own process group under a deadline (design D9); the running step adds `run`.

    `run` starts the argv in its own session with stdin closed, merges stdout and stderr into the
    step's log (`capture=True` keeps the two streams apart in the returned `Outcome` for a
    resolver or version command), polls through the injected wait, and at `cutoff` sends SIGTERM
    to the group, waits the ten-second grace, sends SIGKILL and reaps: a `timeout`. The group is
    recorded in `running.json` while it runs, so the next run-lock holder can clean up after a
    dead run, and removed once it has been reaped.
    """

    def __init__(self, clock, wait, processes=None):
        self.clock = clock
        self.wait = wait
        self.processes = processes or Processes()

    def run(self, argv, cwd, env, log_path, cutoff, capture):
        started = self.clock.now()
        if started >= cutoff:
            return Outcome("not_reached",
                           error="the deadline reserve was reached before the command ran",
                           log=log_path)
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        merged = out = err = None
        try:
            merged = open(log_path, "wb")
            if capture:
                out = tempfile.TemporaryFile()
                err = tempfile.TemporaryFile()
                stdout_target, stderr_target = out, err
            else:
                stdout_target, stderr_target = merged, subprocess.STDOUT
            try:
                process = self.processes.spawn(list(argv), cwd, env, stdout_target,
                                               stderr_target)
            except OSError as error:
                return Outcome("not_run", log=log_path,
                               error="cannot run %s: %s"
                                     % (argv[0] if argv else "<empty argv>", error))
            reaped = False
            try:
                _record_running(log_path, process.pid)
                code, timed_out = self._wait(process, cutoff)
                if timed_out:
                    code = self._terminate(process)
                reaped = True
            finally:
                # D9: the record goes only once the group is gone, not merely its leader. A
                # surviving member (or a daemon) stays recorded so the next lock holder can clean
                # it up, and nothing signals a group from a live run.
                if reaped and not self.processes.group_alive(process.pid):
                    _forget_running(log_path, process.pid)
            if capture:
                stdout_data = _stream_bytes(out)
                stderr_data = _stream_bytes(err)
                merged.write(stdout_data)
                merged.write(stderr_data)
                merged.flush()
            else:
                stdout_data = stderr_data = b""
            elapsed = self.clock.now() - started
            tail = _tail(log_path)
            if timed_out:
                return Outcome("timeout", code=code, tail=tail, elapsed_s=elapsed,
                               error="the step reached its cutoff", log=log_path,
                               stdout=_lines(stdout_data) if capture else None,
                               stderr=_lines(stderr_data) if capture else None)
            return Outcome("ok" if code == 0 else "fail", code=code, tail=tail,
                           elapsed_s=elapsed, log=log_path,
                           stdout=_lines(stdout_data) if capture else None,
                           stderr=_lines(stderr_data) if capture else None)
        finally:
            for handle in (merged, out, err):
                if handle is not None:
                    try:
                        handle.close()
                    except OSError:
                        pass

    def _wait(self, process, cutoff):
        """Poll until the process exits (`(code, False)`) or the cutoff passes (`(None, True)`)."""
        while True:
            code = process.poll()
            if code is not None:
                return code, False
            if self.clock.now() >= cutoff:
                return None, True
            self.wait.sleep(POLL_SECONDS)

    def _terminate(self, process):
        """SIGTERM, the ten-second grace, SIGKILL, reap (design D9); returns the reaped code.

        The grace and the SIGKILL decision belong to the whole process group, not to the leader:
        a leader that exits on SIGTERM must not cut the grace short while a member that ignored
        it is still alive. A dead leader that has not been reaped is a zombie and still counts as
        a group member, so `poll` reaps it before every `group_alive` check — otherwise a leader
        that honours SIGTERM would sit out the whole grace and be sent a pointless SIGKILL.
        """
        self.processes.signal_group(process.pid, signal.SIGTERM)
        grace_end = self.clock.now() + TERM_GRACE_SECONDS
        while True:
            process.poll()
            if not self.processes.group_alive(process.pid):
                break
            if self.clock.now() >= grace_end:
                self.processes.signal_group(process.pid, signal.SIGKILL)
                break
            self.wait.sleep(POLL_SECONDS)
        code = process.wait()
        # SIGKILL is immediate for live members, but an orphaned grandchild can still be a
        # zombie for a moment; give the group a bounded moment to settle before it is forgotten.
        settle_end = self.clock.now() + KILL_SETTLE_SECONDS
        while True:
            process.poll()
            if not self.processes.group_alive(process.pid):
                break
            if self.clock.now() >= settle_end:
                break
            self.wait.sleep(POLL_SECONDS)
        return code


def _lock_fd(fd):
    """One non-blocking exclusive lock attempt on `fd`; an OSError means the lock is held."""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover - no file locking on this platform
        raise OSError("this platform has no file locking")


def _unlock_fd(fd):
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    elif msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def _try_lock(path):
    """A held descriptor for `path`, or None when the single non-blocking try failed (D9)."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _lock_fd(fd)
    except OSError:
        os.close(fd)
        return None
    return fd


def _release_fd(fd):
    if fd is None:
        return
    try:
        _unlock_fd(fd)
    except OSError:
        pass
    os.close(fd)  # closing also drops a flock


class RunLock:
    """`build/verify/lock`: one non-blocking flock try; a held lock means contention (design D9)."""

    def __init__(self, path):
        self.path = path
        self.fd = None

    def try_acquire(self):
        """True when this call holds the lock; a single try, so a holder is contention at once."""
        if self.fd is not None:
            return True
        fd = _try_lock(self.path)
        if fd is None:
            return False
        self.fd = fd
        return True

    def release(self):
        fd, self.fd = self.fd, None
        _release_fd(fd)


class StateLock:
    """A short state lock (`hook-state.json`, the machine config) retried through the wait (D9).

    `acquire()` retries non-blocking tries through the injected wait for at most `seconds` (10)
    and never past the run's deadline; failing that it returns False, which the caller reports
    as an internal error.
    """

    def __init__(self, path, ctx, seconds=STATE_LOCK_SECONDS):
        self.path = path
        self.clock = ctx.clock
        self.wait = ctx.wait
        self.deadline = ctx.deadline
        self.seconds = seconds
        self.fd = None

    def acquire(self):
        if self.fd is not None:
            return True
        started = None
        while True:
            fd = _try_lock(self.path)
            if fd is not None:
                self.fd = fd
                return True
            now = self.clock.now()
            if started is None:
                started = now
            limit = started + self.seconds
            if self.deadline is not None:
                limit = min(limit, self.deadline)
            if now >= limit:
                return False
            self.wait.sleep(min(POLL_SECONDS, limit - now))

    def release(self):
        fd, self.fd = self.fd, None
        _release_fd(fd)


def _verify_dir(ctx):
    """`build/verify`: the parent of this run's `runs/<run id>` directory (design D9/D10)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(ctx.run_dir)))


def _processes(ctx):
    return getattr(ctx.runner, "processes", None) or Processes()


def cleanup_orphans(ctx, run_id):
    """Kill the groups a dead run left behind; only the run-lock holder calls this (design D9).

    Every group recorded in `running.json` under another run id belongs to a dead run: it gets
    SIGTERM, then SIGKILL after the grace, and is confirmed gone when `killpg(pgid, 0)` raises
    ProcessLookupError. Every recorded group gone clears those entries and returns True; a group
    still alive after 15 seconds is contention and returns False, leaving its entry in place.
    """
    path = os.path.join(_verify_dir(ctx), "running.json")
    groups = _running_groups(path)
    own = [group for group in groups if group["run"] == run_id]
    others = [group for group in groups if group["run"] != run_id]
    if not others:
        return True
    processes = _processes(ctx)
    started = ctx.clock.now()
    for group in others:
        processes.signal_group(group["pgid"], signal.SIGTERM)
    grace_end = started + ORPHAN_GRACE_SECONDS
    confirm_end = started + ORPHAN_CONFIRM_SECONDS
    killed = False
    while True:
        alive = [group for group in others if processes.group_alive(group["pgid"])]
        if not alive:
            try:
                _write_running(path, own)
            except OSError:
                pass
            return True
        now = ctx.clock.now()
        if now >= confirm_end:
            try:
                _write_running(path, own + alive)
            except OSError:
                pass
            return False
        if now >= grace_end and not killed:
            for group in alive:
                processes.signal_group(group["pgid"], signal.SIGKILL)
            killed = True
        ctx.wait.sleep(POLL_SECONDS)


@dataclass
class NodeResult:
    """One node's outcome for the report (design D10).

    `failures` carries every `FAIL`, `timeout` and unexpected `NOT RUN` as
    `(step id, outcome, the step's re-run command)`; an expected `NOT RUN` (device, platform,
    branch-only, none related, nothing planned, not reached) is a note or the reason only, which
    is how `exit_code` tells them apart.
    """

    status: str
    reason: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    failures: List[Tuple[str, Outcome, str]] = field(default_factory=list)


@dataclass
class RunOutcome:
    """One gate's result (design D1/D4/D9/D10), enough for the CLI and the hook to settle.

    `errors` is what the CLI prints before the node lines and what makes it exit 2 (a selection
    error, a contended run, an unreadable config). `problems` is the report-ordered repeat
    identity of every blocking result: `(node, step id, outcome kind)`, or the selection
    identity. `incomplete` is True when the deadline left steps not reached; a blocking problem
    still wins over it (design D12).
    """

    results: Dict[str, NodeResult] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    selection: Optional[Selection] = None
    plan: Optional[Plan] = None
    contended: bool = False
    problems: List[tuple] = field(default_factory=list)
    incomplete: bool = False
    # The step records `last-run.json` holds, for the hook's "what finished, what was cut off,
    # what was not reached" summary (design D9/D12).
    steps: List[dict] = field(default_factory=list)


def _utf8_len(text):
    return len(text.encode("utf-8"))


def _truncate_bytes(text, cap):
    data = text.encode("utf-8")
    if len(data) <= cap:
        return text
    return data[:cap].decode("utf-8", "ignore")


def _status_line(node, result):
    if result.reason:
        return "%s: %s (%s)" % (node, result.status, result.reason)
    return "%s: %s" % (node, result.status)


OUTCOME_LABELS = {"fail": "FAIL", "timeout": "TIMEOUT", "not_run": "NOT RUN",
                  "not_reached": "not reached", "ok": "OK"}


def _outcome_label(kind):
    return OUTCOME_LABELS.get(kind, kind.upper())


def _failure_block(step_id, outcome, rerun):
    """One failure block (design D10): its header lines, the `tail:` lead and the tail lines.

    The header carries the step id, the outcome's error and fix, the re-run command and the log
    path; the tail is the outcome's last lines, already capped. Keeping the two apart lets the
    summary trim the oldest tail lines when the block does not fit, instead of losing the header.
    """
    header = ["  %s: %s" % (step_id, _outcome_label(outcome.kind))]
    if outcome.kind == "fail" and outcome.code is not None:
        header[0] += " (exit %d)" % outcome.code
    detail = outcome.error or ""
    for line in detail.splitlines():
        header.append("    " + line)
    if outcome.fix and outcome.fix not in detail:
        header.append("    fix: %s" % outcome.fix)
    if rerun:
        header.append("    rerun: %s" % rerun)
    if outcome.log:
        header.append("    log: %s" % outcome.log)
    tail = [line[:TAIL_CHARS] for line in outcome.tail[-TAIL_LINES:]]
    return header, ("    tail:" if tail else ""), ["      " + line for line in tail]


def _block_lines(header, lead, tail):
    return header + ([lead] + tail if tail else [])


def render(results, errors, cap=SUMMARY_CAP):
    """The run summary (design D10): a status line per node, its notes, then the failure details.

    The cap counts UTF-8 bytes. Every node keeps its status line and the pointer to `last-run.json`
    always survives. Failure blocks are added in report order next; the first block that does not
    fit keeps its header (step, error/fix, re-run command, log path) and only its oldest tail lines
    are trimmed, and later blocks are dropped as D10 says. Notes are optional and are kept in node
    order only from the budget the failure details leave over.
    """
    head = ["verify: %s" % error for error in errors]
    nodes = sorted(results)
    status = [_status_line(node, results[node]) for node in nodes]
    notes = [["  " + note for note in results[node].notes] for node in nodes]
    blocks = []
    for node in nodes:
        for step_id, outcome, rerun in results[node].failures:
            blocks.append(_failure_block(step_id, outcome, rerun))
    lines = list(head)
    for line, group in zip(status, notes):
        lines.append(line)
        lines.extend(group)
    text = "\n".join(lines)
    complete = "\n".join([text] + ["\n".join(_block_lines(*block)) for block in blocks])
    if _utf8_len(complete) <= cap:
        return complete
    pointer = ("verify: the summary was capped at %d bytes; the full report is in %s"
               % (cap, LAST_RUN_POINTER))
    # The status lines and the pointer are mandatory; the failure details come next and notes last.
    mandatory = "\n".join(head + status)
    if _utf8_len(mandatory) + 1 + _utf8_len(pointer) > cap:
        return _truncate_bytes(mandatory, cap)
    budget = cap - _utf8_len(mandatory) - 1 - _utf8_len(pointer)
    chosen = []
    used = 0
    for header, lead, tail in blocks:
        whole = "\n".join(_block_lines(header, lead, tail))
        if used + _utf8_len(whole) + 1 <= budget:
            chosen.append(whole)
            used += _utf8_len(whole) + 1
            continue
        block = list(header)
        if tail:
            kept = []
            for line in reversed(tail):
                candidate = "\n".join(header + [lead] + [line] + kept)
                if used + _utf8_len(candidate) + 1 > budget:
                    break
                kept.insert(0, line)
            if kept:
                block = header + [lead] + kept
        candidate = "\n".join(block)
        if used + _utf8_len(candidate) + 1 <= budget:
            chosen.append(candidate)
            used += _utf8_len(candidate) + 1
        break
    budget -= used
    kept_notes = []
    for group in notes:
        kept = []
        for note in group:
            if _utf8_len(note) + 1 <= budget:
                kept.append(note)
                budget -= _utf8_len(note) + 1
        kept_notes.append(kept)
    lines = list(head)
    for line, kept in zip(status, kept_notes):
        lines.append(line)
        lines.extend(kept)
    return _truncate_bytes("\n".join(lines + chosen + [pointer]), cap)


def exit_code(results, errors):
    """D10's precedence: 1 for a failure or timeout, else 2 for an error or an unexpected NOT RUN."""
    blocked = False
    unexpected = False
    for result in results.values():
        if result.status == "FAIL":
            blocked = True
        for _step_id, outcome, _rerun in result.failures:
            if outcome.kind in ("fail", "timeout"):
                blocked = True
            elif outcome.kind == "not_run":
                unexpected = True
    if blocked:
        return 1
    if errors or unexpected:
        return 2
    return 0


def prune_runs(ctx):
    """Keep the newest `KEEP_RUNS` run directories and this run's own (design D10)."""
    runs_dir = os.path.join(_verify_dir(ctx), "runs")
    try:
        names = sorted(name for name in os.listdir(runs_dir)
                       if os.path.isdir(os.path.join(runs_dir, name)))
    except OSError:
        return []
    current = os.path.basename(os.path.normpath(ctx.run_dir))
    keep = set(names[-KEEP_RUNS:]) if KEEP_RUNS > 0 else set()
    keep.add(current)
    removed = []
    for name in names:
        if name in keep:
            continue
        shutil.rmtree(os.path.join(runs_dir, name), ignore_errors=True)
        removed.append(name)
    return removed


def write_last_run(ctx, results, steps, elapsed_s=None):
    """Write `build/verify/last-run.json` (design D10) and keep the newest ten run directories.

    Version 1 holds the run id and directory, every node's status with its notes, one record per
    step with its own id, status, `elapsed_s`, re-run command and log path, and the time the whole
    invocation took (`elapsed_s`, measured by `run_gate`).
    """
    document = {
        "version": LAST_RUN_VERSION,
        "run": os.path.basename(os.path.normpath(ctx.run_dir)),
        "run_dir": _relative(ctx, os.path.abspath(ctx.run_dir))
                   or os.path.abspath(ctx.run_dir),
        "elapsed_s": None if elapsed_s is None else float(elapsed_s),
        "nodes": {},
        "steps": [],
    }
    for node in sorted(results):
        result = results[node]
        document["nodes"][node] = {
            "status": result.status,
            "reason": result.reason,
            "notes": list(result.notes),
            "failures": [step_id for step_id, _outcome, _rerun in result.failures],
        }
    for step in steps:
        document["steps"].append({
            "id": step.get("id"),
            "status": step.get("status"),
            "elapsed_s": float(step.get("elapsed_s") or 0.0),
            "rerun": step.get("rerun"),
            "log": step.get("log"),
        })
    _write_json(os.path.join(_verify_dir(ctx), "last-run.json"), document)
    prune_runs(ctx)


def _step_rerun(step, argv):
    """The exact re-run command of design D10: `cd <cwd> && <argv>`, every item quoted."""
    return "cd %s && %s" % (shlex.quote(step.cwd or "."), _argv_text(list(argv)))


def _not_run_reason(step_id, outcome):
    message = outcome.error or outcome.fix or "the requirement is missing"
    return "%s: %s" % (step_id, message.splitlines()[0].strip())


def _output_requirements(table, producer_id):
    """The generated requirements a producer must have created after it succeeds (D5)."""
    return [(req_id, requirement) for req_id, requirement in table.requirements.items()
            if requirement.get("kind") == "generated"
            and requirement.get("producer") == producer_id]


def _consumer_blocked(table, step, ctx, producer_outcomes):
    """The `not_run` a consumer earns when a producer failed or its output is absent (D4/D5)."""
    for req_id in step.requires:
        requirement = table.requirements[req_id]
        if requirement.get("kind") != "generated":
            continue
        producer = requirement.get("producer")
        previous = producer_outcomes.get(producer)
        if previous is not None and previous.kind != "ok":
            detail = (previous.error or previous.kind or "it did not run").splitlines()[0]
            return Outcome(
                "not_run", fix=requirement["fix"],
                error="the producer %s did not succeed (%s)\nfix: %s"
                      % (producer, detail, requirement["fix"]))
        if not ctx.fs.isdir(_abs_path(ctx, requirement["path"])):
            return Outcome(
                "not_run", fix=requirement["fix"],
                error="generated output %s is missing\nfix: %s"
                      % (requirement["path"], requirement["fix"]))
    return None


def _probe_step_requirements(table, step, ctx, env):
    """Probe a step's tool and path requirements; generated ones follow their producer (D5/D7)."""
    for req_id in step.requires:
        requirement = table.requirements[req_id]
        if requirement.get("kind") == "generated":
            continue
        outcome, _resolved = probe(req_id, requirement, ctx, env, ctx.runner.run)
        if outcome.kind != "ok":
            return outcome
    return None


def _run_step(step, argv, ctx, env, log_path):
    """One command or producer through the runner, classified and with its log path (D9)."""
    outcome = ctx.runner.run(list(argv), _abs_path(ctx, step.cwd), dict(env), log_path,
                             step_cutoff(ctx, STEP_LIMIT_SECONDS), False)
    outcome = classify_env_failure(step, outcome, log_path)
    if outcome.log is None:
        outcome = replace(outcome, log=log_path)
    return outcome


def _producer_outcome(table, step, argv, ctx, env, log_path):
    """A producer is a step like any other; after success its whole output must exist (D5)."""
    outcome = _run_step(step, argv, ctx, env, log_path)
    if outcome.kind != "ok":
        return outcome
    for _req_id, requirement in _output_requirements(table, step.id):
        if not ctx.fs.isdir(_abs_path(ctx, requirement["path"])):
            return Outcome(
                "not_run", log=log_path, fix=requirement["fix"],
                error="the producer %s did not create %s\nfix: %s"
                      % (step.id, requirement["path"], requirement["fix"]))
    return outcome


def _record_step(steps, step, argv, outcome):
    steps.append({
        "id": step.id,
        "status": outcome.kind,
        "elapsed_s": float(outcome.elapsed_s or 0.0),
        "rerun": _step_rerun(step, argv),
        "log": outcome.log,
    })


def _finish_node(result, entry, failed, unexpected, not_reached, ran):
    """D10's node status precedence: FAIL over an unexpected NOT RUN, else the planned-skip text."""
    if failed:
        result.status = "FAIL"
        return result
    if unexpected:
        result.status = "NOT RUN"
        for step_id, outcome, _rerun in result.failures:
            if outcome.kind == "not_run":
                result.reason = _not_run_reason(step_id, outcome)
                break
        return result
    if not_reached:
        result.status = "NOT RUN"
        result.reason = NOT_REACHED_REASON
        return result
    if not ran:
        result.status = "NOT RUN"
        result.reason = "nothing planned: %s" % (
            (entry.none if entry is not None else None)
            or "no check and no related test for the changed files")
        return result
    result.status = "PASS"
    return result


def _effective_environment(ctx):
    """The inherited environment with the machine config applied (design D8)."""
    config = Config(ctx.common_dir, repo=ctx.repo, fs=ctx.fs, wait=ctx.wait.sleep).load()
    return effective_env(ctx.env, config)


def _apply_sdk_warning(ctx, env, planned):
    """D8: the `local.properties` mismatch is a note under the `android` node when it is planned."""
    node_plan = planned.nodes.get("android")
    if node_plan is None:
        return
    warning = sdk_dir_warning(ctx, env)
    if warning and warning not in node_plan.notes:
        node_plan.notes.append(warning)


# --- remembered passes (design D5, D6) -------------------------------------------------------


@dataclass(frozen=True)
class FingerprintInput:
    """Everything that decided one node's result, ready to be hashed (design D6).

    `generated` maps a consumed output directory (repository-relative) to its snapshot digest;
    a `None` value means the snapshot could not be taken, which makes the node non-reusable.
    """

    gate: str
    node: str
    base: str
    changed: List[str]
    node_plan: NodePlan
    entry_digest: str
    env: Dict[str, str]
    config_digest: Optional[str]
    resolved: Dict[str, str]
    local_properties: Optional[str]
    generated: Dict[str, str]


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _digest_json(document):
    """sha256 over canonical JSON: UTF-8, sorted keys, the separators design D6 names."""
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(encoded)


def _mode_text(mode):
    return mode if isinstance(mode, str) else format(int(mode), "o")


def _component_map(ctx):
    """The component map the owner filter needs; an unreadable map is "no hit" (D6)."""
    try:
        return components.load_map(os.path.join(ctx.repo, ".github", "components.json"))
    except (OSError, ValueError):
        return None


def _gradle_user_home(env):
    """Gradle's user home, or None when `GRADLE_OPTS`/`JAVA_OPTS` redirect it (design D6)."""
    for name in ("GRADLE_OPTS", "JAVA_OPTS"):
        if GRADLE_USER_HOME_OPTION in (env.get(name) or ""):
            return None
    home = (env.get("GRADLE_USER_HOME") or "").strip()
    if home:
        return home
    user = (env.get("HOME") or "").strip() or os.path.expanduser("~")
    return os.path.join(user, ".gradle")


def _env_vars(env):
    """The allowlisted build-affecting variables, present ones only (design D6)."""
    names = sorted(
        name for name in env
        if name in ENV_VARS or any(name.startswith(prefix) for prefix in ENV_PREFIXES)
    )
    return dict((name, env[name]) for name in names)


def _optional_digest(ctx, path):
    """sha256 of an optional file: None when absent, `_UNREADABLE` when it cannot be read."""
    if not ctx.fs.exists(path):
        return None
    try:
        return _sha256(ctx.fs.read_bytes(path))
    except (OSError, KeyError):
        return _UNREADABLE


_UNREADABLE = object()


def _files_digest(node, changed, ctx):
    """sha256 of the sorted `[path, mode, id]` list for the paths that can select the node.

    A tracked, unmodified file contributes its index mode and blob id (`git ls-files -s -z`); a
    modified, staged or untracked one its working-tree mode and content sha256; a deleted one
    `deleted` (design D6).
    """
    cmap = _component_map(ctx)
    if cmap is None:
        return None
    try:
        index = ctx.git.ls_files()
    except (GitError, AttributeError):
        return None
    if not isinstance(index, dict):
        return None
    entries = []
    try:
        for path in sorted(set(index) | set(changed)):
            owners = components.owner_nodes(path, cmap)
            if owners is not None and node not in owners:
                continue
            full = _abs_path(ctx, path)
            if path in changed:
                if not ctx.fs.exists(full):
                    entries.append([path, "deleted", None])
                    continue
                info = ctx.fs.lstat(full)
                entries.append([path, _mode_text(info.st_mode),
                                _sha256(ctx.fs.read_bytes(full))])
            else:
                mode, blob = index[path]
                entries.append([path, mode, blob])
    except (OSError, KeyError):
        return None
    return _digest_json(entries)


def _stat_value(info):
    """The stat-mode input digest: device, inode, mode, size and status-change time (D6)."""
    return _digest_json([getattr(info, "st_dev", 0), getattr(info, "st_ino", 0),
                         info.st_mode, info.st_size, getattr(info, "st_ctime_ns", 0)])


def _input_walk(ctx, full, label):
    """`(label, stat)` entries for every file and directory under one declared input."""
    yield label, ctx.fs.lstat(full)
    if not ctx.fs.isdir(full):
        return
    for dirpath, dirnames, filenames in ctx.fs.walk(full):
        for name in dirnames:
            item = os.path.join(dirpath, name)
            yield _join_label(label, full, item), ctx.fs.lstat(item)
        for name in filenames:
            item = os.path.join(dirpath, name)
            yield _join_label(label, full, item), ctx.fs.lstat(item)
        if _past_cutoff(ctx):
            raise _DeadlineReached()


def _input_files(ctx, full, label):
    """`(label, stat, bytes)` for every regular file under one declared content input."""
    if not ctx.fs.isdir(full):
        yield label, ctx.fs.stat(full), ctx.fs.read_bytes(full)
        return
    for dirpath, _dirnames, filenames in ctx.fs.walk(full):
        for name in filenames:
            item = os.path.join(dirpath, name)
            if ctx.fs.islink(item):
                raise _FingerprintUnreadable()
            yield _join_label(label, full, item), ctx.fs.stat(item), ctx.fs.read_bytes(item)
        if _past_cutoff(ctx):
            raise _DeadlineReached()


def _join_label(label, root, item):
    relative = os.path.relpath(item, root).replace(os.sep, "/")
    return label.rstrip("/") + "/" + relative if relative != "." else label


def _inputs_digest(node_plan, env, ctx):
    """sha256 of the sorted `[path, mode, size, digest]` input list (design D6)."""
    entries = {}
    home = _UNREADABLE
    try:
        for planned in node_plan.steps:
            for entry in planned.step.inputs:
                path = entry.get("path")
                if not path:
                    continue
                mode = entry.get("mode", "content")
                if path.startswith(GRADLE_HOME_PREFIX):
                    if home is _UNREADABLE:
                        home = _gradle_user_home(env)
                    if home is None:
                        return None  # gradle.user.home is redirected: not reusable this run
                    rest = path[len(GRADLE_HOME_PREFIX):]
                    full = os.path.normpath(os.path.join(home, rest))
                    label = GRADLE_HOME_PREFIX + rest
                else:
                    full = _abs_path(ctx, path)
                    label = os.path.normpath(path).replace(os.sep, "/")
                if not ctx.fs.exists(full):
                    continue
                if mode == "stat":
                    for item, info in _input_walk(ctx, full, label):
                        entries[(item, "stat")] = [
                            item, "stat", info.st_size, _stat_value(info)]
                else:
                    for item, info, data in _input_files(ctx, full, label):
                        # Content mode covers the file's mode, size and content (design D6).
                        entries[(item, "content")] = [
                            item, "content", info.st_size,
                            _digest_json([_mode_text(info.st_mode), _sha256(data)])]
    except (_DeadlineReached, _FingerprintUnreadable, OSError, KeyError):
        return None
    return _digest_json([entries[key] for key in sorted(entries)])


def _fingerprint_parts(fi, ctx):
    """The hashed `files` and `inputs` parts, or None when the node is not reusable (D6)."""
    if any(planned.step.reuse is False for planned in fi.node_plan.steps):
        return None
    if any(value is None for value in fi.generated.values()):
        return None
    files = _files_digest(fi.node, fi.changed, ctx)
    if files is None:
        return None
    inputs = _inputs_digest(fi.node_plan, fi.env, ctx)
    if inputs is None:
        return None
    return files, inputs


def _fingerprint_document(fi, files, inputs):
    document = {
        "v": FINGERPRINT_VERSION,
        "gate": fi.gate,
        "node": fi.node,
        "base": fi.base,
        "plan": [[planned.step.id, planned.step.cwd, list(planned.argv)]
                 for planned in fi.node_plan.steps],
        "entry": fi.entry_digest,
        "env": {
            "vars": _env_vars(fi.env),
            "config": fi.config_digest,
            "resolved": dict(fi.resolved),
            "local_properties": fi.local_properties,
        },
        "files": files,
        "inputs": inputs,
        "generated": dict(fi.generated),
    }
    return _digest_json(document)


def fingerprint(fi, ctx):
    """The remembered-pass key (design D6), or None when the node is not reusable this run."""
    parts = _fingerprint_parts(fi, ctx)
    if parts is None:
        return None
    return _fingerprint_document(fi, parts[0], parts[1])


def snapshot_tree(path, ctx):
    """sha256 over one generated output tree, or None for a link, special file or failed read.

    The digest is over the sorted `[relative path, mode, sha256]` list of every regular file
    (design D6); an absent directory is None, so a removed output never matches.
    """
    if not ctx.fs.exists(path) or not ctx.fs.isdir(path) or ctx.fs.islink(path):
        return None
    entries = []
    try:
        for dirpath, dirnames, filenames in ctx.fs.walk(path):
            for name in list(dirnames) + list(filenames):
                if ctx.fs.islink(os.path.join(dirpath, name)):
                    return None
            for name in filenames:
                item = os.path.join(dirpath, name)
                info = ctx.fs.stat(item)
                if not stat.S_ISREG(info.st_mode):
                    return None
                relative = os.path.relpath(item, path).replace(os.sep, "/")
                entries.append([relative, info.st_mode, _sha256(ctx.fs.read_bytes(item))])
            if _past_cutoff(ctx):
                raise _DeadlineReached()
    except (_DeadlineReached, OSError, KeyError):
        return None
    entries.sort()
    return _digest_json(entries)


class Passes:
    """`build/verify/passes.json`: `{"<gate>/<node>": "<digest>"}`, only the hook reads it (D6).

    Every read that cannot complete is a miss; a failed write is best-effort and never turns a
    pass into a failure.
    """

    def __init__(self, path):
        self.path = path

    def _read(self):
        document = _read_json(self.path)
        if not isinstance(document, dict):
            return {}
        return dict((key, value) for key, value in document.items()
                    if isinstance(key, str) and isinstance(value, str))

    @staticmethod
    def _key(gate, node):
        return "%s/%s" % (gate, node)

    def get(self, gate, node):
        return self._read().get(self._key(gate, node))

    def put(self, gate, node, digest):
        document = self._read()
        document[self._key(gate, node)] = digest
        try:
            _write_json(self.path, document)
        except OSError:
            pass

    def drop(self, gate, node):
        document = self._read()
        key = self._key(gate, node)
        if key not in document:
            return
        del document[key]
        try:
            _write_json(self.path, document)
        except OSError:
            pass


def entry_digest(table, node):
    """sha256 of the canonical node entry with its producers and requirements (design D6)."""
    entry = table.nodes.get(node)
    if entry is None:
        return None
    requirements = {}
    producers = {}

    def add(step):
        for req_id in step.requires:
            if req_id in requirements:
                continue
            requirement = table.requirements.get(req_id)
            if requirement is None:
                return False
            requirements[req_id] = requirement
            if requirement.get("kind") == "generated":
                producer = table.producers.get(requirement.get("producer"))
                if producer is None:
                    return False
                if producer.id not in producers:
                    producers[producer.id] = asdict(producer)
                    if not add(producer):
                        return False
        return True

    commands = []
    for step in entry.commands:
        commands.append(asdict(step))
        if not add(step):
            return None
    return _digest_json({"node": node, "commands": commands,
                         "producers": producers, "requirements": requirements})


def _fingerprint_input(node, gate, sel, node_plan, table, ctx, env, resolved, generated):
    """Build one `FingerprintInput`, or None when a file it needs cannot be read (D6)."""
    config = _optional_digest(
        ctx, os.path.join(os.path.realpath(ctx.common_dir), "veles-verify", "tools.json"))
    if config is _UNREADABLE:
        return None
    local = _optional_digest(ctx, os.path.join(ctx.repo, "android", "local.properties"))
    if local is _UNREADABLE:
        return None
    entry = entry_digest(table, node)
    if entry is None:
        return None
    return FingerprintInput(
        gate=gate,
        node=node,
        base=sel.base,
        changed=list(sel.files),
        node_plan=node_plan,
        entry_digest=entry,
        env=dict(env),
        config_digest=config,
        resolved=dict(resolved),
        local_properties=local,
        generated=dict(generated),
    )


def _probe_resolved(table, node_plan, ctx, env):
    """`requirement id -> "<real path>@<version output>"` for every planned tool (D6/D7)."""
    resolved = {}
    for planned in node_plan.steps:
        for req_id in planned.step.requires:
            requirement = table.requirements.get(req_id)
            if requirement is None or requirement.get("kind") != "tool":
                continue
            if req_id in resolved:
                continue
            outcome, value = probe(req_id, requirement, ctx, env, ctx.runner.run)
            if outcome.kind != "ok":
                return None
            if value is not None:
                resolved[req_id] = value
    return resolved


def _consumed_outputs(table, node_plan):
    """The generated output directories the node's planned steps consume, in plan order."""
    paths = []
    for planned in node_plan.steps:
        for req_id in planned.step.requires:
            requirement = table.requirements.get(req_id) or {}
            if requirement.get("kind") == "generated" and requirement["path"] not in paths:
                paths.append(requirement["path"])
    return paths


def remembered(node, gate, sel, plan, table, ctx):
    """The hook-only lookup (design D6): True when this node's stored pass still matches.

    It re-probes the node's requirements, takes the generated digests from the outputs as they
    are now and compares the rebuilt key with `Passes.get`. A probe the runner cut off returns
    `(False, (<step id>, <timeout Outcome>))`; any other read that cannot finish is a plain miss.
    """
    node_plan = plan.nodes.get(node)
    if node_plan is None:
        return False, None
    try:
        env = _effective_environment(ctx)
    except ConfigError:
        return False, None
    resolved = {}
    probed = set()
    for planned in node_plan.steps:
        for req_id in planned.step.requires:
            if req_id in probed:
                continue
            probed.add(req_id)
            requirement = table.requirements.get(req_id)
            if requirement is None:
                return False, None
            outcome, value = probe(req_id, requirement, ctx, env, ctx.runner.run)
            if outcome.kind == "timeout":
                return False, (planned.step.id, outcome)
            if outcome.kind != "ok":
                return False, None
            if value is not None:
                resolved[req_id] = value
    generated = {}
    for relative in _consumed_outputs(table, node_plan):
        digest = snapshot_tree(_abs_path(ctx, relative), ctx)
        if digest is None:
            return False, None
        generated[relative] = digest
    fi = _fingerprint_input(node, gate, sel, node_plan, table, ctx, env, resolved, generated)
    if fi is None:
        return False, None
    digest = fingerprint(fi, ctx)
    if digest is None:
        return False, None
    stored = Passes(os.path.join(_verify_dir(ctx), PASSES_POINTER)).get(gate, node)
    return (stored is not None and stored == digest), None


def _record_pass(node, gate, base, ctx, cmap, node_plan, table, env, resolved, generated,
                 pre_source):
    """Write a pass only when fresh observations reproduce the pre-run source key (D6)."""
    if pre_source is None or resolved is None:
        return
    if any(value is None for value in generated.values()):
        return
    try:
        fresh = select(ctx.git, cmap, base)
    except SelectionError:
        return
    fresh_input = _fingerprint_input(node, gate, fresh, node_plan, table, ctx, env,
                                    resolved, {})
    if fresh_input is None:
        return
    parts = _fingerprint_parts(fresh_input, ctx)
    if parts is None:
        return
    if _fingerprint_document(fresh_input, parts[0], parts[1]) != pre_source:
        return
    digest = _fingerprint_document(replace(fresh_input, generated=dict(generated)),
                                   parts[0], parts[1])
    Passes(os.path.join(_verify_dir(ctx), PASSES_POINTER)).put(gate, node, digest)


def _branch_state(ctx):
    """`(HEAD, git status --porcelain)` for the branch pass, or None when git cannot read it."""
    try:
        return ctx.git.head(), ctx.git.status_porcelain()
    except (GitError, AttributeError):
        return None


def _branch_pass_complete(result, deadline, base, before, after):
    """Whether a `--branch` run may leave a branch-pass record (design D2).

    A complete run exits 0, was given no `--deadline` and no `--base`, left no step not reached
    and saw a clean tree under the same HEAD before selection and after the last step. `before`
    and `after` are `(head, porcelain)` tuples, or None when git could not be read.
    """
    if deadline is not None or base is not None:
        return False
    if exit_code(result.results, result.errors) != 0:
        return False
    if any(step.get("status") == "not_reached" for step in result.steps):
        return False
    if before is None or after is None or before != after:
        return False
    head, porcelain = before
    return bool(head) and not porcelain.strip()


def _write_branch_pass(ctx, base, outcome, before, after):
    """Write the record of a complete branch run; an incomplete one leaves none behind."""
    selection = outcome.selection
    if selection is None or not _branch_pass_complete(
            outcome, ctx.deadline, base, before, after):
        return
    branch_pass.write(ctx.repo, after[0], selection.base, sorted(selection.reasons))


def run_gate(gate, base, ctx, table, cmap, use_passes=False):
    """One gate end to end, in the fixed order of design D1/D4/D5/D6/D9/D10.

    Take the run lock (tried once; held is contention) -> `cleanup_orphans` -> `select` -> `plan`
    (resolvers) -> per node in table order, per step: probe, run, classify, stopping the node at
    a command `FAIL` -> `write_last_run` -> release the lock. Once `deadline - 45` has passed no
    step, probe or resolver starts: it and every later step are `not_reached`, which is reported
    and makes the run incomplete but is not a problem.

    `use_passes` is true only in hook mode: a node whose stored key still matches is skipped as
    `PASS (remembered)`. A task-gate pass may be recorded; `--branch` never writes, and a manual
    task-gate run never reads. A record is written only when fresh observations after the run
    reproduce the source-side key computed before it.

    A branch run deletes the previous branch-pass record as soon as it holds the run lock and
    writes a new one only when the run is complete (design D2).
    """
    started = ctx.clock.now()
    outcome = RunOutcome()
    lock = RunLock(os.path.join(_verify_dir(ctx), "lock"))
    if not lock.try_acquire():
        outcome.contended = True
        outcome.errors.append(CONTENTION)
        return outcome
    try:
        # Every branch run that holds the lock drops the old proof first; only a complete run
        # leaves a new one, and a contended run never gets here (design D2).
        if gate == "branch":
            branch_pass.delete(ctx.repo)
        before = _branch_state(ctx) if gate == "branch" else None
        run_id = os.path.basename(os.path.normpath(ctx.run_dir))
        if not cleanup_orphans(ctx, run_id):
            outcome.contended = True
            outcome.errors.append(ORPHAN_CONTENTION)
            return outcome
        try:
            env = _effective_environment(ctx)
        except ConfigError as error:
            outcome.errors.append("cannot read the machine config: %s" % error)
            return outcome
        try:
            selection_result = select(ctx.git, cmap, base)
        except SelectionError as error:
            outcome.errors.append(str(error))
            if error.fix:
                outcome.errors.append("fix: %s" % error.fix)
            outcome.problems.append(error.identity)
            return outcome
        outcome.selection = selection_result
        # The list-only resolvers read `ctx.env`; give them the effective environment too (D8),
        # then leave the injected context as the caller passed it.
        inherited = ctx.env
        ctx.env = env
        try:
            planned = plan(table, selection_result, gate, ctx, ctx.runner.run)
        finally:
            ctx.env = inherited
        outcome.plan = planned
        _apply_sdk_warning(ctx, env, planned)
        if not selection_result.files or selection_result.only_unowned:
            # Zero-work outcomes print their own line; no node is ever a pass (design D10).
            after = _branch_state(ctx) if gate == "branch" else None
            write_last_run(ctx, {}, [], elapsed_s=ctx.clock.now() - started)
            if gate == "branch":
                _write_branch_pass(ctx, base, outcome, before, after)
            return outcome

        passes = Passes(os.path.join(_verify_dir(ctx), PASSES_POINTER))
        lookup = bool(use_passes) and gate == "task"
        recordable = gate == "task"
        step_records = []
        number = {}
        for node, entry in table.nodes.items():
            node_plan = planned.nodes.get(node)
            if node_plan is None:
                continue
            result = NodeResult(status="PASS")
            result.notes.extend(node_plan.notes)
            ran = failed = unexpected = not_reached = stopped = False
            producer_outcomes = {}
            step_by_id = dict((command.id, command) for command in entry.commands)
            # The reserve is checked before anything can start: a node reached after it must not
            # probe (a never-started probe is `not_reached`, not a blocking timeout) and cannot
            # be remembered, so every planned step is recorded as not reached below.
            reserve = _past_cutoff(ctx)

            if lookup and not reserve:
                hit, failure = remembered(node, gate, selection_result, planned, table, ctx)
                if failure is not None:
                    # A requirement probe the runner cut off is that step's blocking timeout
                    # (design D6/D9/D12).
                    step_id, failure_outcome = failure
                    step = step_by_id.get(step_id) or table.producers.get(step_id)
                    rerun = _step_rerun(step, step.run) if step is not None else None
                    failed = True
                    result.failures.append((step_id, failure_outcome, rerun))
                    step_records.append({
                        "id": step_id,
                        "status": failure_outcome.kind,
                        "elapsed_s": float(failure_outcome.elapsed_s or 0.0),
                        "rerun": rerun,
                        "log": failure_outcome.log,
                    })
                    outcome.results[node] = _finish_node(
                        result, entry, failed, unexpected, not_reached, ran)
                    passes.drop(gate, node)
                    continue
                if hit:
                    result.status = "PASS (remembered)"
                    outcome.results[node] = result
                    continue

            # The source side of a possible record is hashed before the run, then again from
            # fresh observations after it (design D6's stability rule). A plan with any
            # `"reuse": false` step is never recordable, so it needs no hashing at all.
            record_node = recordable and not reserve and all(
                planned.step.reuse is not False for planned in node_plan.steps)
            pre_source = None
            resolved_pre = None
            if record_node:
                resolved_pre = _probe_resolved(table, node_plan, ctx, env)
                if resolved_pre is not None:
                    pre_input = _fingerprint_input(
                        node, gate, selection_result, node_plan, table, ctx, env,
                        resolved_pre, {})
                    if pre_input is not None:
                        pre_source = fingerprint(pre_input, ctx)
            generated = {}
            snapshots_bad = False

            # A resolver that did not complete is that test's result; the node keeps its other
            # steps (design D4). One the reserve stopped before it started is `not_reached`.
            for step_id, resolver_outcome in node_plan.unresolved:
                step = step_by_id.get(step_id)
                rerun = _step_rerun(step, step.run) if step is not None else None
                # The captured result says which case this is: a resolver the reserve stopped
                # before it started is `not_reached`; one that actually ran and was cut off or
                # failed stays the blocking timeout/NOT RUN it earned, even if earlier nodes'
                # steps pushed the clock past the reserve since (design D9/D10).
                if resolver_outcome.error == TARGET_RESOLUTION_TIMEOUT:
                    not_reached = True
                    step_records.append({
                        "id": step_id, "status": "not_reached", "elapsed_s": 0.0,
                        "rerun": rerun, "log": None})
                    continue
                if resolver_outcome.kind == "timeout":
                    failed = True
                else:
                    unexpected = True
                result.failures.append((step_id, resolver_outcome, rerun))
                step_records.append({
                    "id": step_id,
                    "status": resolver_outcome.kind,
                    "elapsed_s": float(resolver_outcome.elapsed_s or 0.0),
                    "rerun": rerun,
                    "log": resolver_outcome.log,
                })

            for planned_step in node_plan.steps:
                step = planned_step.step
                count = number.get(step.id, 0) + 1
                number[step.id] = count
                log_path = os.path.join(ctx.run_dir, "%s.%d.log" % (step.id, count))
                rerun = _step_rerun(step, planned_step.argv)
                if _past_cutoff(ctx):
                    # The reserve wins over a previous FAIL: the step was not reached, not
                    # skipped by the node's own failure (design D9).
                    not_reached = True
                    step_records.append({
                        "id": step.id, "status": "not_reached", "elapsed_s": 0.0,
                        "rerun": rerun, "log": None})
                    continue
                if stopped:
                    step_records.append({
                        "id": step.id, "status": "skipped", "elapsed_s": 0.0,
                        "rerun": rerun, "log": None})
                    continue

                if record_node:
                    for req_id in step.requires:
                        requirement = table.requirements[req_id]
                        if requirement.get("kind") != "generated":
                            continue
                        relative = requirement["path"]
                        digest = snapshot_tree(_abs_path(ctx, relative), ctx)
                        if digest is None:
                            snapshots_bad = True
                        elif relative in generated and generated[relative] != digest:
                            # D5/D6: two consumers saw different output in one run.
                            snapshots_bad = True
                        else:
                            generated[relative] = digest

                blocked = _consumer_blocked(table, step, ctx, producer_outcomes)
                if blocked is not None:
                    step_outcome = blocked
                else:
                    probe_outcome = _probe_step_requirements(table, step, ctx, env)
                    if probe_outcome is not None:
                        step_outcome = probe_outcome
                    elif step.kind == "producer":
                        step_outcome = _producer_outcome(
                            table, step, planned_step.argv, ctx, env, log_path)
                    else:
                        step_outcome = _run_step(step, planned_step.argv, ctx, env, log_path)

                if step.kind == "producer":
                    producer_outcomes[step.id] = step_outcome
                if step_outcome.kind in ("fail", "timeout"):
                    failed = True
                    result.failures.append((step.id, step_outcome, rerun))
                    if step.kind != "producer":
                        # A command failure stops this node; a producer's failure only stops its
                        # consumers (design D4).
                        stopped = True
                elif step_outcome.kind == "not_run":
                    unexpected = True
                    result.failures.append((step.id, step_outcome, rerun))
                elif step_outcome.kind == "not_reached":
                    # A probe used the last of the reserve before the step started: the runner
                    # refused it, and this is the same not-reached report as the check above.
                    not_reached = True
                else:
                    ran = True
                _record_step(step_records, step, planned_step.argv, step_outcome)

            outcome.results[node] = _finish_node(
                result, entry, failed, unexpected, not_reached, ran)
            if not_reached:
                outcome.incomplete = True
            if recordable:
                status = outcome.results[node].status
                if status == "PASS" and not snapshots_bad:
                    _record_pass(node, gate, base, ctx, cmap, node_plan, table, env,
                                 resolved_pre, generated, pre_source)
                elif status in ("FAIL", "NOT RUN"):
                    # D6: a node's record is deleted on its FAIL or NOT RUN.
                    passes.drop(gate, node)

        for node in sorted(outcome.results):
            for step_id, step_outcome, _rerun in outcome.results[node].failures:
                outcome.problems.append((node, step_id, step_outcome.kind))
        outcome.steps = step_records
        after = _branch_state(ctx) if gate == "branch" else None
        write_last_run(ctx, outcome.results, step_records,
                       elapsed_s=ctx.clock.now() - started)
        if gate == "branch":
            _write_branch_pass(ctx, base, outcome, before, after)
        return outcome
    finally:
        lock.release()


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
        if base is not None:
            # `--base ""` is a base selection error, never a silent fallback to origin/master.
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


def step_cutoff(ctx, limit):
    """The earlier of the step's own limit and the run deadline's termination reserve (D9)."""
    cutoff = ctx.clock.now() + limit
    if ctx.deadline is not None:
        cutoff = min(cutoff, ctx.deadline - DEADLINE_RESERVE_SECONDS)
    return cutoff


def _resolver_cutoff(ctx):
    return step_cutoff(ctx, RESOLVER_SECONDS)


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
    """Every regular file under `root`, repository-relative.

    Checking the deadline per directory matters more than finishing the walk, so a cut-off walk
    raises `_DeadlineReached`; the caller reports an unresolved test instead of a `none related`
    note, which would claim nothing is related when resolution simply did not finish (D4/D9).
    """
    if not root:
        return []
    found = []
    for dirpath, _dirnames, filenames in ctx.fs.walk(_abs_path(ctx, root)):
        for name in filenames:
            found.append(_relative(ctx, os.path.join(dirpath, name)))
        if _past_cutoff(ctx):
            raise _DeadlineReached()
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
    """The files named by `<file>:<key>` (design D4's `within`).

    Returns `(allowlist, None)`, `(None, None)` when the strategy has no `within`, and
    `(None, outcome)` when the allowlist cannot be read: it is an unexpected `not_run`, never an
    empty allowlist that would silently drop the tests behind a `tests: none related` note.
    """
    within = (test.related or {}).get("within")
    if not within:
        return None, None
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
    label = "%s:%s" % (relative or candidates[0], key)

    def failure(reason):
        return None, Outcome("not_run", error="within %s: %s" % (label, reason))

    if relative is None:
        return failure("the file does not exist")
    try:
        text = ctx.fs.read_text(_abs_path(ctx, relative))
    except (OSError, KeyError) as error:
        return failure("cannot be read: %s" % error)
    try:
        data = json.loads(text)
    except ValueError as error:
        return failure("invalid JSON: %s" % error)
    value = data
    for name in key.split("."):
        if not isinstance(value, dict) or name not in value:
            return failure("%s is missing" % key)
        value = value[name]
    if isinstance(value, str):
        try:
            tokens = shlex.split(value)
        except ValueError:
            tokens = value.split()
    elif isinstance(value, list):
        tokens = [str(item) for item in value]
    else:
        return failure("%s is not a script string or list" % key)
    base = relative.rsplit("/", 1)[0] if "/" in relative else ""
    allowed = set()
    for token in tokens:
        allowed.add(_norm_target(token))
        if base:
            allowed.add(_norm_target(base + "/" + token))
    return allowed, None


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

    Returns `(targets, None)` on success and `([], outcome)` when resolution did not complete:
    `timeout` when a resolver or the filesystem walk was cut off (and when the run is already
    inside the deadline reserve), `not_run` for a failed resolver or an unreadable `within`
    allowlist (design D4/D9). An unreadable allowlist is never an empty one: that would drop the
    tests behind a `tests: none related` note. A `crate` strategy returns the matched crate
    prefixes, which `plan` maps through `related.crates` (it has no `run` template).
    """
    related = test.related or {}
    strategy = related.get("strategy")
    if _past_cutoff(ctx):
        return [], Outcome("timeout", error=TARGET_RESOLUTION_TIMEOUT)
    try:
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
    except _DeadlineReached:
        return [], Outcome("timeout", error=TARGET_RESOLUTION_TIMEOUT)
    allow, failure = _within_allowlist(test, ctx)
    if failure is not None:
        return [], failure
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
    """D1: selection and planning only; resolvers run, nothing else does.

    It takes the same run lock (tried once; held is exit 2) and runs `cleanup_orphans` before any
    resolver, so every runner call and every `running.json` write happens under the lock.
    """
    gate = "branch" if args.branch else "task"
    lock = RunLock(os.path.join(_verify_dir(ctx), "lock"))
    if not lock.try_acquire():
        ctx.log("verify: %s" % CONTENTION)
        return 2
    try:
        run_id = os.path.basename(os.path.normpath(ctx.run_dir))
        if not cleanup_orphans(ctx, run_id):
            ctx.log("verify: %s" % ORPHAN_CONTENTION)
            return 2
        try:
            env = _effective_environment(ctx)
        except ConfigError as error:
            ctx.log("verify: cannot read the machine config: %s" % error)
            return 2
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
        inherited = ctx.env
        ctx.env = env
        try:
            result = plan(table, selection_result, gate, ctx, ctx.runner.run)
        finally:
            ctx.env = inherited
        _apply_sdk_warning(ctx, env, result)
        for line in render_plan(selection_result, result):
            ctx.log(line)
        return 0
    finally:
        lock.release()


# --- requirements, probes and the machine config (design D7, D8 and D2) ---------------------


class ConfigError(ValueError):
    """The machine config cannot be read, validated or written; the CLI exits 2 (design D8)."""


CONFIG_VERSION = 1
CONFIG_KEYS = ("java_home", "android_sdk", "android_ndk", "path_prepend")
# A short state lock: 100 non-blocking tries 0.1 s apart, the design's 10-second bound (D9).
CONFIG_LOCK_ATTEMPTS = 100
JAVA_PIN_FILE = ".java-version"
NDK_PIN_FILE = "rust/ndk-version"
JVM_ROOT = "/usr/lib/jvm"
SDK_ROOTS = ("$HOME/.android-sdk", "$HOME/Android/Sdk")
TOOL_ROOTS = ("$HOME/.cargo/bin", "$HOME/.nvm/versions/node/*/bin")
CANDIDATE_LIMIT = 5
PROBE_TIMEOUT = "the requirement probe reached the deadline reserve"
ASK_USER = "ask the user which one this project uses"
PLAYWRIGHT_QUERY = ["node", "-e",
                    "console.log(require('playwright-core').chromium.executablePath())"]


def _checkout_for(common_dir):
    """The checkout a git common dir belongs to, for `Config(common_dir)` alone.

    A linked worktree's common dir is the main checkout's `.git`, so the main checkout is the
    fallback; `verify.py` passes the run's own checkout explicitly (design D8).
    """
    base = os.path.basename(common_dir)
    if base == ".git" or base.endswith(".git"):
        return os.path.dirname(common_dir)
    return common_dir


class Config:
    """`<git-common-dir>/veles-verify/tools.json`: one machine config shared by every worktree."""

    def __init__(self, common_dir, repo=None, fs=None, wait=None):
        self.common_dir = os.path.realpath(common_dir)
        self.repo = os.path.realpath(repo) if repo else _checkout_for(self.common_dir)
        self.fs = fs or Fs()
        self.wait = wait or time.sleep
        self.directory = os.path.join(self.common_dir, "veles-verify")
        self.path = os.path.join(self.directory, "tools.json")
        self.lock_path = os.path.join(self.directory, "tools.json.lock")

    def load(self):
        """The saved keys, or `{}` without the file; a malformed file is a `ConfigError`."""
        try:
            text = self.fs.read_text(self.path)
        except (FileNotFoundError, KeyError):
            # A fake filesystem reports an absent file as KeyError; both mean "no file yet".
            return {}
        except OSError as error:
            raise ConfigError("cannot read %s: %s" % (self.path, error))
        try:
            data = json.loads(text)
        except ValueError as error:
            raise ConfigError("%s is not valid JSON: %s" % (self.path, error))
        if not isinstance(data, dict):
            raise ConfigError("%s must hold a JSON object" % self.path)
        version = data.get("version")
        if isinstance(version, bool) or version != CONFIG_VERSION:
            raise ConfigError("%s: version must be %d" % (self.path, CONFIG_VERSION))
        config = {}
        for key in sorted(set(data) - {"version"}):
            if key not in CONFIG_KEYS:
                raise ConfigError("%s: unknown key %r (known: %s)"
                                  % (self.path, key, ", ".join(CONFIG_KEYS)))
            value = data[key]
            if not isinstance(value, str) or not value:
                raise ConfigError("%s: %s must be a non-empty string" % (self.path, key))
            config[key] = value
        return config

    def set(self, key, value):
        """Validate `value` as the matching resolver does, then save it atomically."""
        if key not in CONFIG_KEYS:
            raise ConfigError("unknown key %r; use one of %s"
                              % (key, ", ".join(CONFIG_KEYS)))
        if not isinstance(value, str) or not value.strip():
            raise ConfigError("%s must be a non-empty value" % key)
        normalized = self._validated(key, value.strip())
        with self._lock():
            config = self.load()
            config[key] = normalized
            self._write(config)

    def show(self, env=None):
        """The path, every key with its value and resolved version, and the SDK warning."""
        config = self.load()
        effective = effective_env(dict(os.environ if env is None else env), config)
        suffix = "" if self.fs.exists(self.path) else " (no file yet)"
        lines = ["config: %s (version %d)%s" % (self.path, CONFIG_VERSION, suffix)]
        for key in CONFIG_KEYS:
            value = config.get(key)
            if not value:
                lines.append("%s: (not set)" % key)
                continue
            lines.append("%s: %s (%s)" % (key, value, _resolved_description(self.fs, key, value)))
        warning = _sdk_dir_warning(self.repo, effective, self.fs)
        if warning:
            lines.append(warning)
        return "\n".join(lines)

    def _validated(self, key, value):
        """The value as it is saved: an absolute, symlink-free path, checked against the pins."""
        path = os.path.realpath(os.path.abspath(os.path.expanduser(value)))
        if key == "java_home":
            if not self.fs.isdir(path) or not self.fs.exists(os.path.join(path, "bin", "java")):
                raise ConfigError("java_home %s is not a JDK: it has no bin/java" % path)
            found = _release_java_version(self.fs, path)
            required = self._pin(JAVA_PIN_FILE)
            if found is None:
                raise ConfigError(
                    "cannot read %s/release, so java_home %s cannot be validated "
                    "against %s; nothing was saved" % (path, path, JAVA_PIN_FILE))
            if _java_major(found) != _java_major(required):
                raise ConfigError(
                    "java_home %s reports %s (major %s), but %s pins %s (major %s); "
                    "nothing was saved" % (path, found, _java_major(found), JAVA_PIN_FILE,
                                           required, _java_major(required)))
        elif key == "android_sdk":
            if not _valid_sdk(self.fs, path):
                raise ConfigError(
                    "android_sdk %s is not an Android SDK: it has neither platform-tools "
                    "nor cmdline-tools; nothing was saved" % path)
        elif key == "android_ndk":
            required = self._pin(NDK_PIN_FILE)
            found = _source_revision(self.fs, path)
            if found != required:
                raise ConfigError(
                    "android_ndk %s reports Pkg.Revision %s, but %s pins %s; "
                    "nothing was saved" % (path, found or "missing", NDK_PIN_FILE, required))
        elif not self.fs.isdir(path):
            raise ConfigError("path_prepend %s is not an existing directory; nothing was saved"
                              % path)
        return path

    def _pin(self, relative):
        path = os.path.join(self.repo, relative)
        try:
            text = self.fs.read_text(path)
        except (OSError, KeyError) as error:
            raise ConfigError("cannot read %s: %s" % (path, error))
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            raise ConfigError("%s is empty" % path)
        return lines[0]

    @contextlib.contextmanager
    def _lock(self):
        lock = agent_rules.FileLock(self.lock_path, sleep=self.wait,
                                    attempts=CONFIG_LOCK_ATTEMPTS)
        try:
            with lock:
                yield
        except agent_rules.LockTimeout:
            raise ConfigError("the config lock %s stayed busy for 10 seconds" % self.lock_path)

    def _write(self, config):
        document = {"version": CONFIG_VERSION}
        document.update(config)
        text = json.dumps(document, indent=2, sort_keys=True) + "\n"
        try:
            os.makedirs(self.directory, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix="tools.json.", suffix=".tmp", dir=self.directory)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            except BaseException:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
        except OSError as error:
            raise ConfigError("cannot write %s: %s" % (self.path, error))


def effective_env(inherited, config):
    """The inherited environment with the config applied to the variables builds read (D8)."""
    env = dict(inherited)
    java_home = config.get("java_home")
    if java_home:
        env["JAVA_HOME"] = java_home
    sdk = config.get("android_sdk")
    if sdk:
        env["ANDROID_HOME"] = sdk
        env["ANDROID_SDK_ROOT"] = sdk
    ndk = config.get("android_ndk")
    if ndk:
        env["ANDROID_NDK_HOME"] = ndk
    prefix = []
    path_prepend = config.get("path_prepend")
    if path_prepend:
        prefix.append(path_prepend)
    effective_java = env.get("JAVA_HOME")
    if effective_java:
        prefix.append(os.path.join(effective_java, "bin"))
    inherited_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join(prefix + ([inherited_path] if inherited_path else []))
    return env


def _properties_get(fs, path, key):
    """One value of a `key=value` properties file (`release`, `source.properties`, `local.properties`)."""
    try:
        text = fs.read_text(path)
    except (OSError, KeyError):
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in ("#", "!"):
            continue
        if "=" in stripped:
            name, _, value = stripped.partition("=")
        elif ":" in stripped:
            name, _, value = stripped.partition(":")
        else:
            continue
        if name.strip() != key:
            continue
        value = _properties_unescape(value.strip())
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        return value
    return None


def _properties_unescape(value):
    out = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            out.append(value[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _release_java_version(fs, home):
    return _properties_get(fs, os.path.join(home, "release"), "JAVA_VERSION")


def _source_revision(fs, directory):
    return _properties_get(fs, os.path.join(directory, "source.properties"), "Pkg.Revision")


def _valid_sdk(fs, path):
    return (bool(path) and fs.isdir(path)
            and (fs.isdir(os.path.join(path, "platform-tools"))
                 or fs.isdir(os.path.join(path, "cmdline-tools"))))


def _local_sdk_dir(repo, fs, properties="android/local.properties"):
    sdk = _properties_get(fs, os.path.join(repo, properties), "sdk.dir")
    return sdk.strip() if sdk else None


def _sdk_dir_warning(repo, env, fs):
    """The `local.properties` warning shared by `config show` and a run's android node (D8)."""
    local = _local_sdk_dir(repo, fs)
    if not local:
        return None
    effective = (env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT") or "").strip()
    if not effective:
        return None
    if os.path.realpath(local) == os.path.realpath(effective):
        return None
    return ("android/local.properties sets sdk.dir=%s, but the effective Android SDK is %s; "
            "Gradle reads local.properties first, and verify.py never writes it"
            % (local, effective))


def sdk_dir_warning(ctx, env):
    """The warning a run reports under the android node (design D8); None when there is none."""
    return _sdk_dir_warning(ctx.repo, env, ctx.fs)


def _resolved_description(fs, key, value):
    if key == "java_home":
        version = _release_java_version(fs, value)
        return "version %s" % (version or "unknown")
    if key == "android_sdk":
        return "valid SDK" if _valid_sdk(fs, value) else "not a valid SDK"
    if key == "android_ndk":
        return "Pkg.Revision %s" % (_source_revision(fs, value) or "missing")
    return "directory"


JAVA_VERSION_BANNER = re.compile(r'\bversion\s+"([^"]+)"')
JAVA_BANNER_LINE = re.compile(r"^(?:openjdk|java)\b", re.IGNORECASE)
JAVA_VERSION_NUMBER = re.compile(r"\d+(?:[._]\d+)*")


def _java_version_text(text):
    """The version a JDK reports or is pinned to.

    A JVM may print `Picked up JAVA_TOOL_OPTIONS: …` (often holding numbers such as `-Xmx2g`)
    before the banner, so the banner's quoted version wins; a bare pin or `release` value such as
    `21.0.12` is taken as it is (design D7).
    """
    lines = (text or "").splitlines()
    for line in lines:
        match = JAVA_VERSION_BANNER.search(line)
        if match:
            return match.group(1)
    for line in lines:
        stripped = line.strip()
        if JAVA_BANNER_LINE.match(stripped):
            build = re.search(r"\bbuild\s+(\S+)", stripped)
            match = JAVA_VERSION_NUMBER.search(build.group(1) if build else stripped)
            if match:
                return match.group(0)
    match = JAVA_VERSION_NUMBER.search(text or "")
    return match.group(0) if match else None


def _java_major(text):
    """The major of a JDK version or of a `java -version` line (1.8 is 8, 21.0.12 is 21)."""
    version = _java_version_text(text)
    if not version:
        return None
    parts = version.split(".")
    if parts[0] == "1" and len(parts) > 1:
        major = re.match(r"\d+", parts[1])
    else:
        major = re.match(r"\d+", parts[0])
    return int(major.group(0)) if major else None


def _first_version(text):
    match = re.search(r"\d+(?:\.\d+)*", text or "")
    return match.group(0) if match else None


def _version_tuple(version):
    try:
        return tuple(int(part) for part in version.split("."))
    except (AttributeError, ValueError):
        return None


def _which(ctx, program, env, cwd):
    """The first executable `program` on the effective PATH, relative entries against `cwd`."""
    for entry in (env.get("PATH") or "").split(os.pathsep):
        if not entry:
            continue
        base = entry if os.path.isabs(entry) else os.path.join(cwd, entry)
        candidate = os.path.join(base, program)
        if (ctx.fs.exists(candidate) and not ctx.fs.isdir(candidate)
                and ctx.fs.is_executable(candidate)):
            return candidate
    return None


def _output_text(outcome):
    """The full captured output of a version command: stdout, then stderr, stripped."""
    lines = []
    for part in (outcome.stdout or [], outcome.stderr or []):
        for line in part:
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
    return "\n".join(lines)


def _first_line(text):
    return (text or "").splitlines()[0].strip() if (text or "").strip() else ""


def _scan_lines(entries, empty):
    """The bounded candidate scan lines: `path (version)` per entry, matches first."""
    if not entries:
        return "candidates: none found under %s" % empty
    lines = ["candidates:"]
    for path, version in entries:
        lines.append("  %s (%s)" % (path, version or "version unknown"))
    return "\n".join(lines)


def _bounded(entries, matches):
    """The bounded candidate view: at most `CANDIDATE_LIMIT`, the ones that satisfy the pin first."""
    preferred = [entry for entry in entries if matches(entry)]
    rest = [entry for entry in entries if entry not in preferred]
    return (preferred + rest)[:CANDIDATE_LIMIT]


def _fix_for_candidate(key, matching, req):
    """The exact fix: the one matching candidate, the ask-user text, else the required fix."""
    if len(matching) == 1:
        return "python3 -I scripts/verify.py config set %s %s" % (key, matching[0][0]), None
    if len(matching) > 1:
        listed = ", ".join(entry[0] for entry in matching[:CANDIDATE_LIMIT])
        return req["fix"], "Several candidates match (%s): %s." % (listed, ASK_USER)
    return req["fix"], None


def _probe_failure(resolver, required, found, scan, fix, extra=None):
    parts = ["%s: %s" % (resolver, required)]
    if found:
        parts.append(found)
    if scan:
        parts.append(scan)
    if extra:
        parts.append(extra)
    parts.append("fix: %s" % fix)
    return Outcome("not_run", error="\n".join(parts), fix=fix)


def _runner_failure(resolver, argv, outcome, req):
    if outcome.kind == "timeout":
        return Outcome("timeout", error="%s: `%s` %s"
                       % (resolver, _argv_text(argv), PROBE_TIMEOUT))
    detail = _output_text(outcome) or outcome.error or outcome.kind
    fix = req["fix"]
    return Outcome(
        "not_run",
        error="%s: `%s` did not complete (%s): %s\nfix: %s"
              % (resolver, _argv_text(argv), outcome.kind, detail, fix),
        fix=fix)


def _probe_log(ctx, req_id):
    return os.path.join(ctx.run_dir, "%s.probe.log" % req_id)


def _probe_run(ctx, run, argv, cwd, env, req_id):
    return run(list(argv), cwd, dict(env), _probe_log(ctx, req_id),
               _resolver_cutoff(ctx), True)


def _pin_value(ctx, relative, req, resolver):
    """The first non-empty line of a pin file, else the `not_run` outcome naming it."""
    path = os.path.join(ctx.repo, relative)
    try:
        text = ctx.fs.read_text(path)
    except (OSError, KeyError) as error:
        return None, Outcome(
            "not_run",
            error="%s: cannot read %s (%s)\nfix: %s"
                  % (resolver, relative, error, req["fix"]),
            fix=req["fix"])
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None, Outcome(
            "not_run",
            error="%s: %s is empty\nfix: %s" % (resolver, relative, req["fix"]),
            fix=req["fix"])
    return lines[0], None


def _read_repo_text(ctx, relative, req, resolver):
    path = os.path.join(ctx.repo, relative)
    try:
        return ctx.fs.read_text(path), None
    except (OSError, KeyError) as error:
        return None, Outcome(
            "not_run",
            error="%s: cannot read %s (%s)\nfix: %s"
                  % (resolver, relative, error, req["fix"]),
            fix=req["fix"])


def _java_candidates(ctx, major):
    if not ctx.fs.isdir(JVM_ROOT):
        return []
    entries = []
    seen = set()
    for name in ctx.fs.listdir(JVM_ROOT):
        path = os.path.join(JVM_ROOT, name)
        if not ctx.fs.isdir(path):
            continue
        real = os.path.realpath(path)
        if real in seen:
            continue
        version = _release_java_version(ctx.fs, path)
        if version is None and not ctx.fs.exists(os.path.join(path, "bin", "java")):
            continue
        seen.add(real)
        entries.append((real, version))
    return _bounded(entries, lambda entry: _java_major(entry[1] or "") == major)


def _sdk_candidate_roots(env, ctx):
    """The SDK directories the candidate scan knows about, with the local.properties one first."""
    roots = []
    for value in (_local_sdk_dir(ctx.repo, ctx.fs),
                  env.get("ANDROID_SDK_ROOT"), env.get("ANDROID_HOME"),
                  _expand_home(env, SDK_ROOTS[1]), _expand_home(env, SDK_ROOTS[0])):
        if value and value not in roots:
            roots.append(value)
    return roots


def _expand_home(env, root):
    home = env.get("HOME") or os.path.expanduser("~")
    if root.startswith("$HOME/"):
        return os.path.join(home, root[len("$HOME/"):])
    return root


def _android_sdk_candidates(ctx, env):
    entries = []
    seen = set()
    for path in _sdk_candidate_roots(env, ctx):
        real = os.path.realpath(path)
        if real in seen or not ctx.fs.isdir(real):
            continue
        seen.add(real)
        valid = _valid_sdk(ctx.fs, real)
        if valid:
            version = ("platform-tools" if ctx.fs.isdir(os.path.join(real, "platform-tools"))
                       else "cmdline-tools")
        else:
            version = "no platform-tools or cmdline-tools"
        entries.append((real, version))
    return _bounded(entries, lambda entry: _valid_sdk(ctx.fs, entry[0]))


def _ndk_candidates(ctx, env, pin):
    entries = []
    seen = set()

    def add(path):
        if not path:
            return
        real = os.path.realpath(path)
        if real in seen or not ctx.fs.isdir(real):
            return
        seen.add(real)
        entries.append((real, _source_revision(ctx.fs, real)))

    add((env.get("ANDROID_NDK_HOME") or "").strip())
    for sdk in _sdk_candidate_roots(env, ctx):
        ndk_dir = os.path.join(sdk, "ndk")
        if ctx.fs.isdir(ndk_dir):
            for name in ctx.fs.listdir(ndk_dir):
                add(os.path.join(ndk_dir, name))
    return _bounded(entries, lambda entry: entry[1] == pin)


def _path_tool_candidates(ctx, env, args, req_id, run):
    """The bounded scan for a missing or too-old PATH tool: `(bin dir, executable, version)`."""
    program = args["program"]
    entries = []
    cargo = _expand_home(env, TOOL_ROOTS[0])
    if ctx.fs.isdir(cargo) and ctx.fs.exists(os.path.join(cargo, program)):
        entries.append((cargo, os.path.join(cargo, program), None))
    nvm = _expand_home(env, "$HOME/.nvm/versions/node")
    if ctx.fs.isdir(nvm):
        for name in ctx.fs.listdir(nvm):
            bindir = os.path.join(nvm, name, "bin")
            executable = os.path.join(bindir, program)
            if not ctx.fs.exists(executable):
                continue
            version = None
            if name.startswith("v") and _first_version(name):
                version = _first_version(name)
            version_cmd = args.get("version_cmd")
            if version is None and version_cmd and version_cmd[0] == program:
                outcome = _probe_run(ctx, run, [executable] + version_cmd[1:], ctx.repo,
                                     env, req_id + ".candidate")
                if outcome.kind == "ok":
                    version = _first_version(_output_text(outcome))
            entries.append((bindir, executable, version))
    entries.sort()
    return _path_candidates_bounded(entries, args, ctx)


def _path_candidates_bounded(entries, args, ctx):
    def matches(entry):
        return _satisfies(args, entry[2], ctx)

    preferred = [entry for entry in entries if matches(entry)]
    rest = [entry for entry in entries if entry not in preferred]
    return (preferred + rest)[:CANDIDATE_LIMIT]


def _satisfies(args, version, ctx):
    if not version:
        return False
    if args.get("exact"):
        return _version_tuple(version) == _version_tuple(args["exact"])
    if args.get("min"):
        have, need = _version_tuple(version), _version_tuple(args["min"])
        return have is not None and need is not None and have >= need
    if args.get("pin_file"):
        pin, _failure = _pin_value(ctx, args["pin_file"], {"fix": ""}, "path-tool")
        return pin is not None and _version_tuple(version) == _version_tuple(pin)
    return True


def _requirement_text(args):
    if args.get("exact"):
        return "exactly %s" % args["exact"]
    if args.get("min"):
        return "%s or newer" % args["min"]
    if args.get("pin_file"):
        return "the version pinned in %s" % args["pin_file"]
    return "any version"


def probe(req_id, req, ctx, env, run):
    """One requirement under the step's effective environment (design D7).

    Returns `(outcome, resolved)` where `outcome.kind` is `ok` or `not_run` with `.fix`,
    `not_reached` when the reserve meant the probe never started, or `timeout` when a version
    command it started was cut off; `resolved` is `"<real path>@<version output>"` for the
    fingerprint (D6) and None otherwise. A version command runs through `run` under the deadline.
    """
    if _past_cutoff(ctx):
        # Never started, so it is not the runner's cut-off (design D9/D10): only a command that
        # ran and was terminated is a `timeout`.
        return Outcome("not_reached", error=PROBE_TIMEOUT), None
    kind = req.get("kind")
    if kind == "path":
        path = req["path"]
        if ctx.fs.exists(_abs_path(ctx, path)):
            return Outcome("ok"), None
        fix = req["fix"]
        return Outcome("not_run",
                       error="path %s is missing\nfix: %s" % (path, fix),
                       fix=fix), None
    if kind == "generated":
        path = req["path"]
        if ctx.fs.isdir(_abs_path(ctx, path)):
            return Outcome("ok"), None
        fix = req["fix"]
        return Outcome("not_run",
                       error="generated output %s is missing\nfix: %s" % (path, fix),
                       fix=fix), None
    if kind != "tool":
        raise ValueError("unknown requirement kind %r" % (kind,))
    resolver = req.get("resolver")
    if resolver == "java-home":
        return _probe_java_home(req_id, req, ctx, env, run)
    if resolver == "android-sdk":
        return _probe_android_sdk(req_id, req, ctx, env)
    if resolver == "android-ndk":
        return _probe_android_ndk(req_id, req, ctx, env)
    if resolver == "path-tool":
        return _probe_path_tool(req_id, req, ctx, env, run)
    if resolver == "rustup-toolchain":
        return _probe_rustup(req_id, req, ctx, env, run)
    if resolver == "playwright-chromium":
        return _probe_playwright(req_id, req, ctx, env, run)
    raise ValueError("unknown requirement resolver %r" % (resolver,))


def _probe_java_home(req_id, req, ctx, env, run):
    pin, failure = _pin_value(ctx, JAVA_PIN_FILE, req, "java-home")
    if failure is not None:
        return failure, None
    major = _java_major(pin)
    java_home = (env.get("JAVA_HOME") or "").strip()
    if java_home:
        java_bin = os.path.join(java_home, "bin", "java")
        if not ctx.fs.exists(java_bin):
            return _java_failure(ctx, env, req, pin, major,
                                 "JAVA_HOME is %s but it has no bin/java" % java_home), None
        found_label = "JAVA_HOME is %s" % java_home
    else:
        java_bin = _which(ctx, "java", env, ctx.repo)
        if java_bin is None:
            return _java_failure(
                ctx, env, req, pin, major,
                "JAVA_HOME is unset and no java is on PATH"), None
        java_home = os.path.dirname(os.path.dirname(java_bin))
        found_label = "java on PATH is %s" % java_bin
    argv = [java_bin, "-version"]
    outcome = _probe_run(ctx, run, argv, ctx.repo, env, req_id)
    if outcome.kind != "ok":
        return _runner_failure("java-home", argv, outcome, req), None
    text = _output_text(outcome)
    if _java_major(text) == major:
        return Outcome("ok"), os.path.realpath(java_home) + "@" + text
    found = "%s, reporting %s" % (found_label, _first_line(text) or "no version output")
    return _java_failure(ctx, env, req, pin, major, found), None


def _java_failure(ctx, env, req, pin, major, found):
    candidates = _java_candidates(ctx, major)
    matching = [entry for entry in candidates if _java_major(entry[1] or "") == major]
    fix, extra = _fix_for_candidate("java_home", matching, req)
    required = "%s pins %s (major %s)" % (JAVA_PIN_FILE, pin, major)
    scan = _scan_lines(candidates, JVM_ROOT)
    return _probe_failure("java-home", required, found, scan, fix, extra)


def _probe_android_sdk(req_id, req, ctx, env):
    local = _local_sdk_dir(ctx.repo, ctx.fs)
    source = None
    for label, value in (("android/local.properties sdk.dir", local),
                         ("ANDROID_HOME", env.get("ANDROID_HOME")),
                         ("ANDROID_SDK_ROOT", env.get("ANDROID_SDK_ROOT"))):
        if value and value.strip():
            source = (label, value.strip())
            break
    if source is None:
        return _android_sdk_failure(
            ctx, env, req,
            "android/local.properties has no sdk.dir, and ANDROID_HOME and "
            "ANDROID_SDK_ROOT are unset"), None
    # Gradle/AGP uses a set sdk.dir or ANDROID_HOME even when it is invalid: an invalid first
    # source is reported, never skipped in favour of one Gradle would not use (design D7/D8).
    label, path = source
    if _valid_sdk(ctx.fs, path):
        return Outcome("ok"), os.path.realpath(path) + "@"
    return _android_sdk_failure(
        ctx, env, req,
        "%s is %s, which has neither platform-tools nor cmdline-tools" % (label, path)), None


def _android_sdk_failure(ctx, env, req, found):
    candidates = _android_sdk_candidates(ctx, env)
    matching = [entry for entry in candidates if _valid_sdk(ctx.fs, entry[0])]
    fix, extra = _fix_for_candidate("android_sdk", matching, req)
    scan = _scan_lines(candidates, "$HOME/.android-sdk, $HOME/Android/Sdk")
    return _probe_failure("android-sdk", "an Android SDK directory", found, scan, fix, extra)


def _ndk_locations(ctx, env, pin):
    locations = []
    ndk_home = (env.get("ANDROID_NDK_HOME") or "").strip()
    if ndk_home:
        locations.append(("ANDROID_NDK_HOME", ndk_home))
    sdk_root = (env.get("ANDROID_SDK_ROOT") or "").strip()
    if sdk_root:
        locations.append(("ANDROID_SDK_ROOT", os.path.join(sdk_root, "ndk", pin)))
    android_home = (env.get("ANDROID_HOME") or "").strip()
    if android_home:
        locations.append(("ANDROID_HOME", os.path.join(android_home, "ndk", pin)))
    for properties in ("local.properties", "android/local.properties"):
        sdk = _local_sdk_dir(ctx.repo, ctx.fs, properties)
        if sdk:
            locations.append((properties + " sdk.dir", os.path.join(sdk, "ndk", pin)))
    return locations


def _probe_android_ndk(req_id, req, ctx, env):
    pin, failure = _pin_value(ctx, NDK_PIN_FILE, req, "android-ndk")
    if failure is not None:
        return failure, None
    locations = _ndk_locations(ctx, env, pin)
    selected = None
    for label, path in locations:
        if ctx.fs.isdir(path):
            selected = (label, path)
            break
    if selected is None:
        searched = ", ".join("%s %s" % (label, path) for label, path in locations) or "none"
        return _android_ndk_failure(
            ctx, env, req, pin,
            "no candidate directory exists (searched: %s)" % searched), None
    label, path = selected
    found = _source_revision(ctx.fs, path)
    if found == pin:
        return Outcome("ok"), os.path.realpath(path) + "@" + pin
    # D7: the first existing candidate is the answer; a revision mismatch there is NOT RUN,
    # never a fall-through to a candidate the Gradle build would not use.
    return _android_ndk_failure(
        ctx, env, req, pin,
        "%s (%s) reports Pkg.Revision %s, and the first existing candidate is the one "
        "the Gradle build uses" % (path, label, found or "missing")), None


def _android_ndk_failure(ctx, env, req, pin, found):
    candidates = _ndk_candidates(ctx, env, pin)
    matching = [entry for entry in candidates if entry[1] == pin]
    fix, extra = _fix_for_candidate("android_ndk", matching, req)
    required = "%s pins %s" % (NDK_PIN_FILE, pin)
    scan = _scan_lines(candidates, "$ANDROID_SDK_ROOT/ndk, $ANDROID_HOME/ndk, "
                                  "$HOME/.android-sdk/ndk, $HOME/Android/Sdk/ndk")
    return _probe_failure("android-ndk", required, found, scan, fix, extra)


def _probe_path_tool(req_id, req, ctx, env, run):
    args = req.get("args") or {}
    program = args["program"]
    cwd = _abs_path(ctx, args.get("cwd") or ".")
    executable = _which(ctx, program, env, cwd)
    required = "%s is required (%s)" % (program, _requirement_text(args))
    if executable is None:
        return _path_tool_failure(
            ctx, env, req, req_id, run, args,
            required, "%s is not on the effective PATH" % program), None
    argv = list(args["version_cmd"])
    outcome = _probe_run(ctx, run, argv, cwd, env, req_id)
    if outcome.kind != "ok":
        return _runner_failure("path-tool", argv, outcome, req), None
    text = _output_text(outcome)
    version = _first_version(text)
    if not _satisfies(args, version, ctx):
        found = "%s is %s" % (executable, text or "not reporting a version")
        return _path_tool_failure(ctx, env, req, req_id, run, args, required, found), None
    return Outcome("ok"), os.path.realpath(executable) + "@" + text


def _path_tool_failure(ctx, env, req, req_id, run, args, required, found):
    candidates = _path_tool_candidates(ctx, env, args, req_id, run)
    matching = [entry for entry in candidates if _satisfies(args, entry[2], ctx)]
    fix, extra = _fix_for_candidate("path_prepend", matching, req)
    lines = ["  %s (%s)" % (entry[1], entry[2] or "version unknown") for entry in candidates]
    scan = ("candidates:\n" + "\n".join(lines)) if lines else (
        "candidates: none found under " + ", ".join(TOOL_ROOTS))
    return _probe_failure("path-tool", required, found, scan, fix, extra)


def _toolchain_channel(text):
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            continue
        if "=" in stripped:
            name, _, value = stripped.partition("=")
            if name.strip() == "channel":
                return _properties_unescape(value.strip().strip('"'))
        elif stripped and not stripped.startswith("#"):
            return stripped
    return None


def _channel_matches(channel, active):
    first = active.split()[0] if active.split() else ""
    return first == channel or first.startswith(channel + "-")


def _probe_rustup(req_id, req, ctx, env, run):
    relative = req["args"]["toolchain_file"]
    text, failure = _read_repo_text(ctx, relative, req, "rustup-toolchain")
    if failure is not None:
        return failure, None
    channel = _toolchain_channel(text)
    if not channel:
        return Outcome("not_run",
                       error="rustup-toolchain: %s pins no channel\nfix: %s"
                             % (relative, req["fix"]),
                       fix=req["fix"]), None
    cwd = _abs_path(ctx, os.path.dirname(relative) or ".")
    rustup = _which(ctx, "rustup", env, cwd)
    required = "%s pins channel %s" % (relative, channel)
    if rustup is None:
        cargo = _expand_home(env, TOOL_ROOTS[0])
        scan = ("candidates:\n  %s" % cargo) if ctx.fs.isdir(cargo) else (
            "candidates: none found under " + TOOL_ROOTS[0])
        return _probe_failure("rustup-toolchain", required,
                              "rustup is not on the effective PATH", scan,
                              req["fix"]), None
    argv = [rustup, "show", "active-toolchain"]
    outcome = _probe_run(ctx, run, argv, cwd, env, req_id)
    if outcome.kind != "ok":
        return _runner_failure("rustup-toolchain", argv, outcome, req), None
    text = _output_text(outcome)
    active = _first_line(text)
    if _channel_matches(channel, active):
        return Outcome("ok"), os.path.realpath(rustup) + "@" + text
    return _probe_failure("rustup-toolchain", required,
                          "the active toolchain is %s" % (active or "unknown"),
                          None, req["fix"]), None


def _probe_playwright(req_id, req, ctx, env, run):
    explicit = (env.get("VELES_TEST_CHROMIUM") or "").strip()
    required = "the pinned playwright-core Chromium, or VELES_TEST_CHROMIUM"
    if explicit:
        if ctx.fs.exists(explicit) and not ctx.fs.isdir(explicit) \
                and ctx.fs.is_executable(explicit):
            return Outcome("ok"), os.path.realpath(explicit) + "@"
        return _probe_failure("playwright-chromium", required,
                              "VELES_TEST_CHROMIUM is %s, which is not an executable file"
                              % explicit, None, req["fix"]), None
    cwd = _abs_path(ctx, "web-extension")
    if _which(ctx, "node", env, cwd) is None:
        return _probe_failure("playwright-chromium", required,
                              "node is not on the effective PATH, so playwright-core cannot "
                              "report its Chromium", None, req["fix"]), None
    outcome = _probe_run(ctx, run, PLAYWRIGHT_QUERY, cwd, env, req_id)
    if outcome.kind != "ok":
        return _runner_failure("playwright-chromium", PLAYWRIGHT_QUERY, outcome, req), None
    reported = _first_line(_output_text(outcome))
    if not reported:
        return _probe_failure("playwright-chromium", required,
                              "playwright-core reported no executable path", None,
                              req["fix"]), None
    path = reported if os.path.isabs(reported) else os.path.join(cwd, reported)
    if ctx.fs.exists(path) and not ctx.fs.isdir(path) and ctx.fs.is_executable(path):
        return Outcome("ok"), os.path.realpath(path) + "@"
    return _probe_failure("playwright-chromium", required,
                          "%s does not exist or is not executable" % path, None,
                          req["fix"]), None


def _log_matches(path, regex):
    """True when any line of the whole log matches; a missing log never matches (D7)."""
    pattern = re.compile(regex)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if pattern.search(line):
                    return True
    except OSError:
        return False
    return False


def classify_env_failure(step, outcome, log_path):
    """Turn a failure the command's own environment entry explains into `not_run` (D7).

    `env_exit` codes are matched first, then `env_patterns`, against the whole log line by
    line -- a diagnostic at the start of a long log still classifies the failure.
    """
    if outcome.kind != "fail":
        return outcome
    for entry in step.env_exit:
        if outcome.code == entry["code"]:
            return replace(outcome, kind="not_run", fix=entry["fix"])
    for entry in step.env_patterns:
        if _log_matches(log_path, entry["regex"]):
            return replace(outcome, kind="not_run", fix=entry["fix"])
    return outcome


# --- the Stop hook (design D11/D12) ----------------------------------------------------------


@dataclass(frozen=True)
class HookAnswer:
    """What the hook tells Claude Code: keep working, pass with a notice, or pass silently."""

    kind: str
    text: Optional[str] = None


@dataclass(frozen=True)
class Admission:
    """One admitted run's reservation and deadline, or the notice that stood in for a run."""

    ok: bool
    reservation: Optional[str] = None
    deadline: Optional[float] = None
    notice: Optional[str] = None


class HookStateError(Exception):
    """The hook's session state cannot be locked; the hook fails open (design D12)."""


def _iso(seconds):
    """A state timestamp from the injected clock (design D12); no wall-clock read."""
    moment = datetime.datetime.fromtimestamp(float(seconds), datetime.timezone.utc)
    return moment.replace(tzinfo=None).isoformat()


def _epoch(text):
    """The seconds behind a state timestamp; a malformed one is 0, never an exception."""
    try:
        moment = datetime.datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return 0.0
    return moment.replace(tzinfo=datetime.timezone.utc).timestamp()


def read_event(stream, cap=EVENT_CAP):
    """The hook's stdin event, or None to fail open (design D11).

    At most `cap + 1` bytes are read, so an oversized stream is recognised without buffering it;
    invalid JSON, a non-object and an event without a non-empty `session_id` and `cwd` are all
    None, and every caller lets the stop through.
    """
    try:
        data = stream.read(cap + 1)
    except (OSError, ValueError):
        return None
    if data is None or len(data) > cap:
        return None
    try:
        event = json.loads(data)
    except (TypeError, ValueError):
        return None
    if not isinstance(event, dict):
        return None
    for key in ("session_id", "cwd"):
        value = event.get(key)
        if not isinstance(value, str) or not value:
            return None
    return event


class _LockView:
    """The `clock`/`wait`/`deadline` seam `StateLock` needs, injected into `HookState`."""

    def __init__(self, clock, wait, deadline):
        self.clock = clock
        self.wait = wait
        self.deadline = deadline


class HookState:
    """`<git-common-dir>/veles-verify/hook-state.json`, version 2, under its own lock (D12).

    One document is shared by every worktree, keyed `<host>:<session id>` (design D4), so a
    Claude and an OpenCode session id never collide. `admit` expires stale
    reservations, checks the session's remaining time and block slots and reserves a run's
    budget and one slot atomically; `settle` removes the reservation, charges the measured time
    and applies the outcome table. Every read that cannot complete starts from an empty state,
    so a damaged file never traps the agent; a write that cannot complete is an internal error
    the caller fails open on.
    """

    def __init__(self, common_dir, clock, wait=None, deadline=None):
        self.clock = clock
        # `StateLock` calls `.sleep`, so the default is the wait primitive, never `time.sleep`.
        self.wait = wait if wait is not None else Wait()
        self.deadline = deadline
        self.directory = os.path.join(os.path.realpath(common_dir), "veles-verify")
        self.path = os.path.join(self.directory, HOOK_STATE_POINTER)
        self.lock_path = self.path + ".lock"

    # -- the locked document -----------------------------------------------------------

    def _lock(self):
        lock = StateLock(self.lock_path, _LockView(self.clock, self.wait, self.deadline))
        if not lock.acquire():
            raise HookStateError("cannot lock %s" % self.lock_path)
        return lock

    def _read(self):
        document = _read_json(self.path)
        if not isinstance(document, dict) or document.get("v") != HOOK_STATE_VERSION:
            return {"v": HOOK_STATE_VERSION, "sessions": {}}
        sessions = document.get("sessions")
        document["sessions"] = dict(
            (key, value) for key, value in (sessions or {}).items()
            if isinstance(key, str) and isinstance(value, dict))
        return document

    def _save(self, document):
        _prune_sessions(document)
        _write_json(self.path, document)

    def _session(self, document, session_id, now):
        session = document["sessions"].get(session_id)
        if not isinstance(session, dict):
            session = {}
            document["sessions"][session_id] = session
        session.setdefault("blocks", 0)
        session.setdefault("spent_s", 0.0)
        session.setdefault("reserved", [])
        session.setdefault("repeat", {"id": None, "n": 0})
        session.setdefault("synopsis", None)
        session.setdefault("last_write", _iso(now))
        if not isinstance(session["reserved"], list):
            session["reserved"] = []
        if not isinstance(session["repeat"], dict):
            session["repeat"] = {"id": None, "n": 0}
        return session

    # -- admission (design D12) --------------------------------------------------------

    def admit(self, session_id, outer_deadline):
        """Expire first, then check the allowance and reserve a run's budget and block slot."""
        lock = self._lock()
        try:
            document = self._read()
            now = self.clock.now()
            session = self._session(document, session_id, now)
            self._expire(session, now)
            live = session["reserved"]
            spent = float(session.get("spent_s") or 0.0)
            left = MAX_SESSION_SECONDS - spent - sum(
                _number(entry.get("budget_s")) for entry in live)
            slots = (MAX_SESSION_BLOCKS - int(session.get("blocks") or 0) - len(live))
            if slots < 1 or left < MIN_RUN_SECONDS:
                session["last_write"] = _iso(now)
                self._save(document)
                return Admission(False, notice=_limit_notice(session))
            seconds = HOOK_RUN_BUDGET_SECONDS
            if outer_deadline is not None:
                seconds = min(seconds, _number(outer_deadline) - now)
            budget = max(0.0, min(HOOK_RUN_BUDGET_SECONDS, left, seconds))
            reservation = {
                "id": uuid.uuid4().hex,
                "budget_s": budget,
                "started": _iso(now),
                "pid": os.getpid(),
            }
            live.append(reservation)
            session["reserved"] = live
            session["last_write"] = _iso(now)
            self._save(document)
            return Admission(True, reservation=reservation["id"],
                             deadline=now + budget)
        finally:
            lock.release()

    @staticmethod
    def _expire(session, now):
        """Charge `budget + 120 s` to a reservation past its start + budget + 120 s (D12)."""
        kept = []
        for entry in session.get("reserved") or []:
            if not isinstance(entry, dict):
                continue
            budget = _number(entry.get("budget_s"))
            if now >= _epoch(entry.get("started")) + budget + RESERVATION_GRACE_SECONDS:
                session["spent_s"] = float(session.get("spent_s") or 0.0) \
                    + budget + RESERVATION_GRACE_SECONDS
            else:
                kept.append(entry)
        session["reserved"] = kept

    # -- settlement (design D12) -------------------------------------------------------

    def settle(self, session_id, reservation, outcome, elapsed_s, identity, summary):
        """Remove the reservation, charge its measured time and apply the outcome table."""
        lock = self._lock()
        try:
            document = self._read()
            now = self.clock.now()
            session = self._session(document, session_id, now)
            held = [entry for entry in session["reserved"]
                    if isinstance(entry, dict) and entry.get("id") == reservation]
            session["reserved"] = [
                entry for entry in session["reserved"]
                if not (isinstance(entry, dict) and entry.get("id") == reservation)]
            # Spec (tasks.md §13): every admitted run that started is charged its measured
            # time; contention cancels without a charge. An expired reservation was charged
            # budget + 120 s at admission, so a late settle never charges it twice.
            if held and outcome != "contention":
                session["spent_s"] = (float(session.get("spent_s") or 0.0)
                                      + max(0.0, float(elapsed_s or 0.0)))
            answer = self._answer(session, outcome, identity, summary)
            session["last_write"] = _iso(now)
            self._save(document)
            return answer
        finally:
            lock.release()

    def _answer(self, session, outcome, identity, summary):
        if outcome == "contention":
            return HookAnswer("notice", HOOK_CONTENTION)
        if outcome == "block":
            attempt = self._repeat(session, identity)
            text = _hook_text(HOOK_BLOCK_LEAD % (attempt, MAX_REPEATS), summary)
            session["synopsis"] = _truncate_bytes(summary or "", SYNOPSIS_CAP) or None
            if attempt <= MAX_REPEATS:
                session["blocks"] = int(session.get("blocks") or 0) + 1
                return HookAnswer("block", text)
            return HookAnswer("notice", _hook_text(
                HOOK_REPEAT_LEAD % (attempt, MAX_REPEATS), summary))
        if outcome == "clean":
            session["blocks"] = 0
            session["spent_s"] = 0.0
            session["repeat"] = {"id": None, "n": 0}
            session["synopsis"] = None
            return HookAnswer("silent")
        if outcome == "incomplete":
            return HookAnswer("notice", _hook_text(HOOK_INCOMPLETE_NOTICE, summary))
        # `no_work`, `internal_error` and an unknown outcome leave the counters as they are.
        return HookAnswer("silent")

    @staticmethod
    def _repeat(session, identity):
        """The repeat identity's next attempt: the same identity increments, another resets (D12)."""
        stored = session.get("repeat") or {}
        previous = int(_number(stored.get("n")))
        same = (identity is not None and stored.get("id") is not None
                and list(identity) == list(stored.get("id") or []))
        attempt = previous + 1 if same else 1
        session["repeat"] = {
            "id": list(identity) if identity is not None else None,
            "n": attempt,
        }
        return attempt


def _number(value):
    """A float from a JSON number, or 0.0; a malformed state value never raises."""
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _limit_notice(session):
    synopsis = session.get("synopsis")
    return _hook_text(HOOK_LIMIT_NOTICE, synopsis)


def _hook_text(lead, summary):
    """The one formatter for a block's reason and a notice: the lead, then the summary."""
    if summary:
        return lead + "\n\n" + summary
    return lead


def _prune_sessions(document):
    """Keep the newest 50 sessions by `last_write`, plus every session with a live reservation."""
    sessions = document.get("sessions") or {}
    if len(sessions) <= KEEP_SESSIONS:
        return
    live = set(key for key, value in sessions.items() if value.get("reserved"))
    others = sorted((key for key in sessions if key not in live),
                    key=lambda key: _epoch(sessions[key].get("last_write")), reverse=True)
    keep = live | set(others[:KEEP_SESSIONS])
    document["sessions"] = dict(
        (key, value) for key, value in sessions.items() if key in keep)


# The hook's step labels for the "what finished, what was cut off, what was not reached" line.
HOOK_STEP_LABELS = {
    "ok": "OK",
    "fail": "FAIL",
    "timeout": "TIMEOUT",
    "not_run": "NOT RUN",
    "not_reached": "not reached",
    "skipped": "skipped",
}


def _step_progress(steps):
    """One line naming every planned step's status, so a deadline names what was not reached."""
    if not steps:
        return ""
    parts = []
    for step in steps:
        status = step.get("status")
        parts.append("%s %s" % (step.get("id"), HOOK_STEP_LABELS.get(status, status)))
    return "steps: " + "; ".join(parts)


def _problem_summary(run):
    """The block reason or notice body: the D10 report, then the step progress line."""
    text = render(run.results, run.errors)
    progress = _step_progress(run.steps)
    if progress:
        text = (text + "\n" + progress) if text else progress
    return _truncate_bytes(text, SUMMARY_CAP)


def _hook_error(error):
    """One stderr line for an internal failure; the hook's output contract is one line."""
    if isinstance(error, BaseException):
        detail = str(error).strip().splitlines()
        first = detail[0].strip() if detail else ""
        text = "%s: %s" % (type(error).__name__, first) if first else type(error).__name__
    else:
        detail = str(error).strip().splitlines()
        text = detail[0].strip() if detail else "unknown error"
    return " ".join(("verify: the hook failed: " + text).splitlines())


def _close_fd(fd):
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def _report_deadline(report_fd, admission):
    """Write the admitted deadline (and nothing else) to the watchdog's pipe, then close it."""
    if report_fd is None:
        return
    try:
        if admission.ok and admission.deadline is not None:
            os.write(report_fd, ("%r\n" % float(admission.deadline)).encode("ascii"))
    except OSError:
        pass
    finally:
        _close_fd(report_fd)


def gate(event, deadline, report_fd, ctx, host):
    """The gate owner: admission, the reported deadline, the task gate and settlement (D11/D12).

    The hook state keys the session `<host>:<session id>` (design D4), so an OpenCode and a
    Claude session id never share counters; the event itself is left unchanged.
    """
    session_id = "%s:%s" % (host, event["session_id"])
    started = ctx.clock.now()
    state = HookState(ctx.common_dir, ctx.clock, wait=ctx.wait, deadline=deadline)
    try:
        admission = state.admit(session_id, deadline)
    except Exception:
        _close_fd(report_fd)
        raise
    _report_deadline(report_fd, admission)
    if not admission.ok:
        if admission.notice:
            return HookAnswer("notice", admission.notice)
        return HookAnswer("silent")
    try:
        ctx.deadline = admission.deadline
        cmap = components.load_map(os.path.join(ctx.repo, ".github", "components.json"))
        table = load_table(os.path.join(ctx.repo, "scripts", "verify-commands.json"), cmap)
        run = run_gate("task", None, ctx, table, cmap, use_passes=True)
    except Exception as error:
        _stderr(_hook_error(error))
        return state.settle(session_id, admission.reservation, "internal_error",
                            ctx.clock.now() - started, None, None)
    elapsed = max(0.0, ctx.clock.now() - started)
    if run.contended:
        return state.settle(session_id, admission.reservation, "contention", elapsed,
                            None, None)
    if run.problems:
        return state.settle(session_id, admission.reservation, "block", elapsed,
                            run.problems[0], _problem_summary(run))
    if run.errors:
        _stderr(_hook_error(run.errors[0]))
        return state.settle(session_id, admission.reservation, "internal_error", elapsed,
                            None, None)
    selection = run.selection
    if selection is not None and (not selection.files or selection.only_unowned):
        return state.settle(session_id, admission.reservation, "no_work", elapsed, None, None)
    if run.incomplete:
        return state.settle(session_id, admission.reservation, "incomplete", elapsed,
                            None, _problem_summary(run))
    return state.settle(session_id, admission.reservation, "clean", elapsed, None, None)


def _emit_answer(answer):
    """The hook's stdout: block JSON, notice JSON, or nothing for a pass."""
    if answer.kind == "block":
        document = {"decision": "block", "reason": answer.text or ""}
    elif answer.kind == "notice":
        document = {"systemMessage": answer.text or ""}
    else:
        return
    _write_stdout(json.dumps(document) + "\n")


def _write_stdout(data):
    """Write text or bytes to the process's stdout without ever raising into the hook."""
    text = data if isinstance(data, str) else data.decode("utf-8", "replace")
    try:
        stream = getattr(sys.stdout, "buffer", None)
        if stream is not None:
            stream.write(text.encode("utf-8"))
            stream.flush()
        else:
            sys.stdout.write(text)
            sys.stdout.flush()
    except (OSError, ValueError):
        pass


def _fd_of(handle):
    try:
        return handle.fileno()
    except (AttributeError, OSError, ValueError):
        return None


def _make_nonblocking(fd):
    try:
        os.set_blocking(fd, False)
    except (AttributeError, OSError):
        pass


def _read_ready(fd):
    """`(data, eof)` from a non-blocking read; `(None, False)` means "nothing yet"."""
    try:
        data = os.read(fd, 65536)
    except (BlockingIOError, InterruptedError):
        return None, False
    except OSError:
        return b"", True
    return data, data == b""


def _write_ready(fd, data):
    """`(written, dead)` for a non-blocking write; a closed pipe means "stop writing"."""
    try:
        return os.write(fd, data), False
    except (BlockingIOError, InterruptedError):
        return 0, False
    except OSError:
        return 0, True


def _unregister(selector, fd):
    try:
        selector.unregister(fd)
    except (KeyError, ValueError, OSError):
        pass


def _close_handle(handle):
    if handle is None:
        return
    try:
        handle.close()
    except (OSError, ValueError):
        pass


def _stop_child(child):
    """Kill and reap only this launcher's own direct child (design D11)."""
    try:
        child.kill()
    except (OSError, AttributeError):
        pass
    try:
        child.wait()
    except (OSError, AttributeError):
        pass


def _install_sigterm(child):
    """Install the launcher's SIGTERM handler and return it (design D4).

    The dispatched child runs in its own session, so killing the launcher alone never reaches
    it: the handler stops it with `_stop_child`, and `_watch` then sees the dead child and lets
    `launcher` return 0. It is installed only in the launcher, never in the `--dispatched` gate.
    """
    def handler(_signum, _frame):
        _stop_child(child)

    signal.signal(signal.SIGTERM, handler)
    return handler


def _watch(child, report_fd, event_bytes, ctx, outer):
    """Relay the child's stdout and write the event to it without ever blocking (D11).

    The report pipe, the child's stdout and the child's stdin are all non-blocking and driven
    from one selector, so a child that never reads its event or never stops writing cannot stall
    the launcher before its kill time. The kill time is the reported admitted deadline plus 30
    seconds, or the outer hook deadline plus 30 seconds (1,770) before one is reported.
    """
    selector = selectors.DefaultSelector()
    stdin_handle = getattr(child, "stdin", None)
    stdout_handle = getattr(child, "stdout", None)
    stdin_fd = _fd_of(stdin_handle)
    stdout_fd = _fd_of(stdout_handle)
    pending = event_bytes if stdin_fd is not None else b""
    report = b""
    reported = None
    stdin_closed = False
    stdout_closed = False
    stdout_done = stdout_fd is None
    _make_nonblocking(report_fd)
    selector.register(report_fd, selectors.EVENT_READ, "report")
    if stdout_fd is not None:
        _make_nonblocking(stdout_fd)
        selector.register(stdout_fd, selectors.EVENT_READ, "stdout")
    if stdin_fd is not None:
        _make_nonblocking(stdin_fd)
        if pending:
            selector.register(stdin_fd, selectors.EVENT_WRITE, "event")
        else:
            _close_handle(stdin_handle)
            stdin_closed = True
    try:
        while True:
            for key, mask in selector.select(0):
                if key.data == "report":
                    data, eof = _read_ready(report_fd)
                    if data:
                        report += data
                        if reported is None and b"\n" in report:
                            line = report.split(b"\n", 1)[0].strip()
                            try:
                                reported = float(line)
                            except ValueError:
                                reported = None
                    if eof:
                        _unregister(selector, report_fd)
                elif key.data == "stdout":
                    data, eof = _read_ready(stdout_fd)
                    if data:
                        _write_stdout(data)
                    if eof:
                        _unregister(selector, stdout_fd)
                        _close_handle(stdout_handle)
                        stdout_closed = True
                        stdout_done = True
                elif key.data == "event":
                    written, dead = _write_ready(stdin_fd, pending)
                    if written:
                        pending = pending[written:]
                    if dead or not pending:
                        # EOF on the child's stdin, so a child that does read can finish.
                        _unregister(selector, stdin_fd)
                        _close_handle(stdin_handle)
                        stdin_closed = True
            # A child that has exited cannot produce more stdout; once the relay is drained the
            # launcher is done, and a pipe another process still holds cannot stall it.
            if child.poll() is not None and stdout_done:
                break
            limit = ((reported if reported is not None else outer)
                     + HOOK_KILL_GRACE_SECONDS)
            if ctx.clock.now() >= limit:
                _stop_child(child)
                break
            ctx.wait.sleep(POLL_SECONDS)
    finally:
        try:
            selector.close()
        except OSError:
            pass
        if not stdin_closed:
            _close_handle(stdin_handle)
        if not stdout_closed:
            _close_handle(stdout_handle)


def launcher(event_bytes, ctx, spawn, host):
    """The watchdog (design D11): dispatch the active checkout's verifier, relay, enforce time.

    Always returns 0 and never writes to stdout itself. A separate clone, another repository, a
    non-checkout `cwd` and a checkout without `scripts/verify.py` let the stop through silently;
    an internal failure does too, with one line on stderr.

    The launcher's own checkout is the one holding this script (`REPO`, the parent of `SCRIPTS`),
    never the process cwd (design D11.3): run from outside any checkout, the hook still compares
    against the checkout it was installed in. The host (`claude` or `opencode`) is passed to the
    dispatched child unchanged, and SIGTERM stops that child before the launcher returns (D4).
    """
    event = read_event(io.BytesIO(event_bytes))
    if event is None:
        return 0
    try:
        outer = ctx.clock.now() + HOOK_OUTER_SECONDS
        own = ctx.discover(REPO)
        target = ctx.discover(event["cwd"])
        if not own or not target:
            return 0
        if os.path.realpath(own[1]) != os.path.realpath(target[1]):
            return 0
        target_repo = os.path.realpath(target[0])
        script = os.path.join(target_repo, "scripts", "verify.py")
        if not ctx.fs.exists(script):
            return 0
        read_fd, write_fd = os.pipe()
        argv = [sys.executable, "-I", script, "--hook", host, "--dispatched",
                "--deadline", str(outer), "--report-fd", str(write_fd)]
        try:
            child = spawn(argv, cwd=target_repo, start_new_session=True,
                          pass_fds=(write_fd,), stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE)
        except Exception:
            _close_fd(read_fd)
            _close_fd(write_fd)
            raise
        # The child owns the write end now; our copy must go or the pipe never sees EOF.
        _close_fd(write_fd)
        previous = signal.getsignal(signal.SIGTERM)
        try:
            _install_sigterm(child)
            _watch(child, read_fd, event_bytes, ctx, outer)
        except Exception:
            # A launcher failure still fails open, but this child is ours alone: stop it.
            _stop_child(child)
            raise
        finally:
            signal.signal(signal.SIGTERM, previous)
            _close_fd(read_fd)
    except Exception as error:
        _stderr(_hook_error(error))
        return 0
    return 0


def _stdin_bytes():
    """The hook's stdin as bytes, at most one byte over the cap so `read_event` can reject it."""
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    data = stream.read(EVENT_CAP + 1)
    if isinstance(data, str):
        return data.encode("utf-8")
    return data or b""


def _hook_mode(args, ctx):
    """The hook's entry point: dispatch, or run the gate when `--dispatched` (design D11)."""
    try:
        raw = _stdin_bytes()
        event = read_event(io.BytesIO(raw))
        if event is None:
            return 0
        if args.dispatched:
            deadline = args.deadline if args.deadline is not None else ctx.deadline
            if deadline is None:
                deadline = ctx.clock.now() + HOOK_OUTER_SECONDS
            answer = gate(event, deadline, args.report_fd, ctx, args.hook)
            _emit_answer(answer)
            return 0
        return launcher(raw, ctx, subprocess.Popen, args.hook)
    except Exception as error:
        _stderr(_hook_error(error))
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
    gate.add_argument("--hook", choices=("claude", "opencode"),
                      help="run as the host's Stop hook (Claude Code or OpenCode)")
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


def _config_mode(args, ctx):
    """`config set|show` over the machine config (design D8); validation happens before a write."""
    config = Config(ctx.common_dir, repo=ctx.repo, wait=ctx.wait.sleep)
    try:
        if args.config_command == "set":
            config.set(args.key, args.value)
        text = config.show()
    except ConfigError as error:
        ctx.log("verify: %s" % error)
        return 2
    for line in text.splitlines():
        ctx.log(line)
    return 0


def main(argv, ctx):
    """Parse the CLI, validate the table, then dispatch. Returns the process exit code."""
    parser = make_parser()
    try:
        args = parser.parse_args(list(argv))
    except UsageError as error:
        ctx.log("verify: %s" % error)
        return 2
    except SystemExit as stopped:  # --help and --version
        return stopped.code if isinstance(stopped.code, int) else 0
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
    if args.hook:
        return _hook_mode(args, ctx)
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
    result = run_gate("branch" if args.branch else "task", args.base, ctx, table, cmap)
    if result.contended:
        for error in result.errors:
            ctx.log("verify: %s" % error)
        return 2
    if result.errors:
        for line in render(result.results, result.errors).splitlines():
            ctx.log(line)
        return exit_code(result.results, result.errors)
    selection_result = result.selection
    if selection_result is not None and not selection_result.files:
        ctx.log("verify: nothing changed against %s" % selection_result.base)
        return 0
    if selection_result is not None and selection_result.only_unowned:
        ctx.log("verify: only unowned paths changed (%s)"
                % ", ".join(selection_result.files))
        return 0
    for line in render(result.results, result.errors).splitlines():
        ctx.log(line)
    return exit_code(result.results, result.errors)


def _stderr(line):
    print(line, file=sys.stderr)


def run_id(clock):
    """D10's run id, `<UTC yyyymmddThhmmss>-<pid>` from the injected clock (no wall-clock read)."""
    return "%s-%d" % (time.strftime("%Y%m%dT%H%M%S", time.gmtime(clock.now())), os.getpid())


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
        run_dir=os.path.join(repo, "build", "verify", "runs", run_id(clock)),
        deadline=None,
        log=_stderr,
    )


def _hook_requested(argv):
    """True when the command line asks for hook mode, before argparse has seen it."""
    return any(item == "--hook" or item.startswith("--hook=") for item in argv)


def _entry_point(argv):
    """The process entry: hook mode builds its context inside the fail-open guard (design D11.6).

    `real_context` discovers a checkout, which can raise for a `cwd` git cannot read; in hook
    mode that must be one stderr line, empty stdout and exit 0, never a traceback before the
    hook's own `try`.
    """
    if not _hook_requested(argv):
        return main(argv, real_context())
    try:
        return main(argv, real_context())
    except Exception as error:
        _stderr(_hook_error(error))
        return 0


if __name__ == "__main__":
    sys.exit(_entry_point(sys.argv[1:]))
