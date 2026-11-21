"""The branch-pass record: the path, version-1 format, atomic writer and check in one module.

`scripts/verify.py --branch` writes the record after a complete run; the hooked-rules engine
(`scripts/agent_rules.py`) checks it before a `git push`. One module owns both sides so they
cannot disagree (design D2). Standard library only, Python 3.8+, imported under `-I`.

This module deliberately imports nothing from `agent_rules`: `agent_rules` imports this module,
and a back-import would load a second engine copy inside the hook budget. The worktree is
resolved here through `git rev-parse --show-toplevel`, which matches `discover_checkout`'s
result for a checkout.
"""
import json
import os
import subprocess
import tempfile
import time
from typing import List, Optional, Tuple

RECORD_VERSION = 1
DEFAULT_GIT_TIMEOUT = 2.0
TIMED_OUT = "proof timed out"
RECORD_DIRECTORY = os.path.join("build", "verify")
RECORD_NAME = "branch-pass.json"


class RecordError(Exception):
    """A branch-pass record that exists but cannot be read as version 1."""


class _MissingRecord(RecordError):
    """No record at the path: a directory that is absent or a file that was deleted."""


def default_run_git(args, cwd, timeout) -> Tuple[int, str, str]:
    """Run a read-only git query in `cwd` with the inherited `GIT_*` overrides removed.

    Returns `(returncode, stdout, stderr)` as text; a failure to start git or a timeout is a
    non-zero return with the reason in stderr, so callers only ever decide on the tuple
    (verify.py's `Git` class is the same style).
    """
    if timeout is not None and timeout <= 0:
        return 124, "", "git %s timed out" % " ".join(args)
    environment = dict((name, value) for name, value in os.environ.items()
                       if not name.startswith("GIT_"))
    environment["LC_ALL"] = "C"
    try:
        completed = subprocess.run(
            ["git", "-C", cwd] + list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return 1, "", "git %s could not run: %s" % (" ".join(args), error)
    return (completed.returncode,
            (completed.stdout or b"").decode("utf-8", "replace"),
            (completed.stderr or b"").decode("utf-8", "replace"))


def record_path(cwd: str, run_git=default_run_git,
                timeout: float = DEFAULT_GIT_TIMEOUT) -> Optional[str]:
    """The record path of the worktree holding `cwd`, or None when `cwd` is not a checkout."""
    returncode, stdout, _ = run_git(["rev-parse", "--show-toplevel"], cwd, timeout)
    if returncode != 0:
        return None
    lines = stdout.splitlines()
    if not lines:
        return None
    toplevel = lines[0].strip()
    if not toplevel or not os.path.isabs(toplevel):
        return None
    return os.path.join(toplevel, RECORD_DIRECTORY, RECORD_NAME)


def _atomic_write(path, text):
    """Write `text` to `path` through a temporary file in the same directory and `os.replace`."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="tmp-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write(cwd: str, head: str, base: str, nodes: List[str]) -> None:
    """Write the version-1 record of a complete branch run in the worktree holding `cwd`."""
    path = record_path(cwd)
    if path is None:
        raise RuntimeError("not a git checkout: %s" % (cwd,))
    document = {
        "version": RECORD_VERSION,
        "head": head,
        "base": base,
        "nodes": list(nodes),
    }
    _atomic_write(path, json.dumps(document, sort_keys=True) + "\n")


def delete(cwd: str) -> None:
    """Delete the record of the worktree holding `cwd`; a missing record is fine."""
    path = record_path(cwd)
    if path is None:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _read(path):
    """The version-1 record at `path`; `_MissingRecord` when absent, `RecordError` otherwise."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        raise _MissingRecord("no record at %s" % (path,))
    except (OSError, UnicodeError) as error:
        raise RecordError(str(error))
    try:
        document = json.loads(text)
    except ValueError as error:
        raise RecordError("invalid JSON: %s" % (error,))
    if not isinstance(document, dict):
        raise RecordError("not a JSON object")
    version = document.get("version")
    if version != RECORD_VERSION:
        raise RecordError("version %r is not %d" % (version, RECORD_VERSION))
    for key in ("head", "base"):
        value = document.get(key)
        if not isinstance(value, str) or not value:
            raise RecordError("%s is missing" % (key,))
    nodes = document.get("nodes")
    if not isinstance(nodes, list) or not all(isinstance(node, str) for node in nodes):
        raise RecordError("nodes is missing")
    return document


def _timeout(deadline, now):
    """The time left before the predicate deadline for the next git call (design D3)."""
    if deadline is None:
        return DEFAULT_GIT_TIMEOUT
    return max(0.0, deadline - now())


def _git_failure(args, returncode, stderr, deadline, now):
    """The check's answer for a failed git call: the deadline first, else git's own message."""
    if deadline is not None and now() >= deadline:
        return False, TIMED_OUT
    text = (stderr or "").strip()
    if not text:
        text = "git %s failed with exit %d" % (" ".join(args), returncode)
    return False, text


def check(cwd: str, deadline: float, now=time.monotonic,
          run_git=default_run_git) -> Tuple[bool, str]:
    """`(ok, message)`: does a branch pass exist for `cwd`'s HEAD and merge base with origin/master?

    `deadline` is on the `now` clock; each git call gets `max(0, deadline - now())` and a clock
    already at the deadline is a deny ("proof timed out") without running git. Never raises for
    a git failure or an unreadable record: both are `(False, message)`.
    """
    timeout = _timeout(deadline, now)
    if timeout <= 0.0:
        return False, TIMED_OUT
    path = record_path(cwd, run_git=run_git, timeout=timeout)
    if path is None:
        return False, "no branch pass in %s: not a git checkout" % (cwd,)

    args = ["rev-parse", "HEAD"]
    timeout = _timeout(deadline, now)
    if timeout <= 0.0:
        return False, TIMED_OUT
    returncode, stdout, stderr = run_git(args, cwd, timeout)
    if returncode != 0:
        return _git_failure(args, returncode, stderr, deadline, now)
    head = stdout.strip()
    if not head:
        return False, "git %s printed no commit" % (" ".join(args),)

    try:
        document = _read(path)
    except _MissingRecord:
        return False, "no branch pass for HEAD %s" % (head[:12],)
    except RecordError as error:
        return False, "branch pass record unreadable: %s" % (error,)

    if document["head"] != head:
        return False, "pass is for %s, HEAD is %s" % (document["head"][:12], head[:12])

    args = ["merge-base", "origin/master", "HEAD"]
    timeout = _timeout(deadline, now)
    if timeout <= 0.0:
        return False, TIMED_OUT
    returncode, stdout, stderr = run_git(args, cwd, timeout)
    if returncode != 0:
        return _git_failure(args, returncode, stderr, deadline, now)
    base = stdout.strip()
    if not base:
        return False, "git %s printed no commit" % (" ".join(args),)
    if document["base"] != base:
        return False, "pass base is %s, merge base is %s" % (
            document["base"][:12], base[:12])
    return True, ""
