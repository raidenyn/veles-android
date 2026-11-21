"""Shared fakes for the verify unit tests: injected clock, wait, git, runner and filesystem.

Every test of `scripts/verify.py` uses these instead of the wall clock, a sleep, a real process or
the real filesystem, so `.agents/rules/repo-no-time-based-tests.md` holds.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import Dict, List, Optional

TESTS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS)
REPO = os.path.dirname(SCRIPTS)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from verify import Context, Outcome  # noqa: E402

ZERO_SHA = "0" * 40


class FakeClock:
    """Injected time: `now` starts at `now` and only moves when something advances it."""

    def __init__(self, now=0.0):
        self.value = float(now)

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds
        return self.value


class FakeWait:
    """Injected sleep: records each wait and advances the injected clock instead of sleeping."""

    def __init__(self, clock):
        self.clock = clock
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.clock.advance(seconds)


class FakeGit:
    """Injected git reads (design D3). `errors` maps a method name to an exception to raise.

    `index` is what `git ls-files -s -z` reports: path -> (mode, blob id), used by the
    remembered-pass fingerprint (design D6).
    """

    def __init__(self, diff=None, staged=None, unstaged=None, untracked=None, base=None,
                 errors=None, commit=None, index=None, head=None, porcelain=None):
        self.diff = list(diff or [])
        self.staged_paths = list(staged or [])
        self.unstaged_paths = list(unstaged or [])
        self.untracked_paths = list(untracked or [])
        self.base = base or ZERO_SHA
        self.commit_sha = commit
        self.errors = dict(errors or {})
        self.index = dict(index or {})
        # The branch-pass identity: the HEAD commit and `git status --porcelain`, both mutable so
        # a test's runner can change them during a run.
        self.head_sha = head or ("b" * 40)
        self.porcelain = "" if porcelain is None else porcelain
        self.calls = []

    def _maybe_raise(self, name):
        error = self.errors.get(name)
        if error is not None:
            raise error

    def diff_names(self, base):
        self.calls.append(("diff_names", base))
        self._maybe_raise("diff_names")
        return list(self.diff)

    def staged(self):
        self.calls.append(("staged",))
        self._maybe_raise("staged")
        return list(self.staged_paths)

    def unstaged(self):
        self.calls.append(("unstaged",))
        self._maybe_raise("unstaged")
        return list(self.unstaged_paths)

    def untracked(self):
        self.calls.append(("untracked",))
        self._maybe_raise("untracked")
        return list(self.untracked_paths)

    def merge_base(self, ref):
        self.calls.append(("merge_base", ref))
        self._maybe_raise("merge_base")
        return self.base

    def head(self):
        """The HEAD commit; mutable through `head_sha` for a run that changes it."""
        self.calls.append(("head",))
        self._maybe_raise("head")
        return self.head_sha

    def status_porcelain(self):
        """`git status --porcelain`; empty for a clean tree, mutable for a dirty run."""
        self.calls.append(("status_porcelain",))
        self._maybe_raise("status_porcelain")
        return self.porcelain

    def commit(self, ref):
        self.calls.append(("commit", ref))
        self._maybe_raise("commit")
        return self.commit_sha or ref

    def ls_files(self):
        self.calls.append(("ls_files",))
        self._maybe_raise("ls_files")
        return dict(self.index)


class FakeRunner:
    """Injected process runner: scripted outcomes keyed by argv, calls and cutoffs recorded."""

    def __init__(self, script=None, stdout=None):
        self.script = dict(script or {})
        self.stdout = dict(stdout or {})
        self.calls = []
        self.cutoffs = []

    def run(self, argv, cwd, env, log_path, cutoff, capture):
        key = tuple(argv)
        self.calls.append({
            "argv": list(argv), "cwd": cwd, "env": dict(env), "log": log_path,
            "cutoff": cutoff, "capture": capture,
        })
        self.cutoffs.append(cutoff)
        outcome = self.script.get(key)
        if outcome is None:
            outcome = Outcome("ok", code=0)
        if capture and key in self.stdout:
            outcome = replace(outcome, stdout=list(self.stdout[key]))
        return outcome


class FakeStat:
    """The `os.stat_result` fields the fingerprint reads (design D6)."""

    def __init__(self, mode=0o100644, size=0, ctime_ns=0, dev=0, ino=0):
        self.st_mode = mode
        self.st_size = size
        self.st_ctime_ns = ctime_ns
        self.st_dev = dev
        self.st_ino = ino


class FakeFs:
    """Injected filesystem: files as `path -> bytes`, plus modes, symlinks and stat overrides."""

    def __init__(self, files=None, modes=None, links=None, stats=None):
        self.files = dict(files or {})
        self.modes = dict(modes or {})
        self.links = dict(links or {})
        self.stats = dict(stats or {})

    @staticmethod
    def _norm(path):
        return os.path.normpath(path).replace(os.sep, "/")

    def exists(self, path):
        path = self._norm(path)
        return path in self.files or path in self.links or self.isdir(path)

    def isdir(self, path):
        path = self._norm(path).rstrip("/")
        return any(name.startswith(path + "/") for name in self.files)

    def isfile(self, path):
        return self._norm(path) in self.files

    def islink(self, path):
        return self._norm(path) in self.links

    def listdir(self, path):
        path = self._norm(path).rstrip("/")
        prefix = path + "/"
        names = set()
        for name in list(self.files) + list(self.links):
            if name.startswith(prefix):
                names.add(name[len(prefix):].split("/", 1)[0])
        return sorted(names)

    def read_bytes(self, path):
        return self.files[self._norm(path)]

    def read_text(self, path):
        return self.read_bytes(path).decode("utf-8")

    def walk(self, top):
        top = self._norm(top).rstrip("/")
        directories = {top}
        for name in list(self.files) + list(self.links):
            parts = name.split("/")
            for index in range(1, len(parts)):
                candidate = "/".join(parts[:index])
                if candidate == top or candidate.startswith(top + "/"):
                    directories.add(candidate)
        for directory in sorted(directories):
            if directory != top and not directory.startswith(top + "/"):
                continue
            entries = self.listdir(directory)
            dirnames = [entry for entry in entries
                        if self.isdir(directory + "/" + entry)]
            filenames = [entry for entry in entries
                         if not self.isdir(directory + "/" + entry)]
            yield directory, dirnames, filenames

    def stat(self, path):
        path = self._norm(path)
        if path in self.stats:
            return self.stats[path]
        if path in self.files:
            return FakeStat(mode=self.modes.get(path, 0o100644), size=len(self.files[path]))
        if self.isdir(path):
            return FakeStat(mode=self.modes.get(path, 0o40755), size=0)
        raise FileNotFoundError(path)

    def lstat(self, path):
        path = self._norm(path)
        if path in self.stats:
            return self.stats[path]
        if path in self.links:
            return FakeStat(mode=0o120777, size=len(self.links[path]))
        return self.stat(path)

    def is_executable(self, path):
        """Fixture files are executable unless their recorded mode says otherwise."""
        path = self._norm(path)
        return path in self.files and bool(self.modes.get(path, 0o100755) & 0o111)


def make_context(**overrides):
    """A Context wired to fakes; any field can be overridden by keyword."""
    clock = overrides.pop("clock", None) or FakeClock(0.0)
    wait = overrides.pop("wait", None) or FakeWait(clock)
    repo = overrides.pop("repo", REPO)
    lines = overrides.pop("log_lines", [])
    defaults = {
        "git": FakeGit(),
        "runner": FakeRunner(),
        "clock": clock,
        "wait": wait,
        "fs": FakeFs(),
        "env": {"PATH": "/usr/bin"},
        "repo": repo,
        "common_dir": repo,
        "run_dir": os.path.join(repo, "build", "verify", "runs", "test-run"),
        "deadline": None,
        "log": lines.append,
    }
    defaults.update(overrides)
    context = Context(**defaults)
    context.log_lines = lines
    return context
