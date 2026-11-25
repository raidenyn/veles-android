#!/usr/bin/env python3
"""The verify gate's one command (`verify-gate.command` in `.agents/harness.json`).

The gate (`.agents/harness/verify-gate/`) sets VERIFY_TRIGGER. On `edit` this lints the one file in
VERIFY_FILE, where it is a lintable file; every other trigger (`task`, `turn-end`, a run by hand) execs
the task gate, `python3 -I scripts/verify.py`, which also writes the record the push gate checks.

Stdlib only; Python 3.8 compatible. See docs/verify.md, "The verify gate".
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERIFY = os.path.join(REPO, "scripts", "verify.py")
# npm packages whose own eslint and prettier check their files.
NPM_PACKAGES = ("web-extension/", "native-bridge/")
NPM_EXCLUDED = ("native-bridge/host/", "native-bridge/installer/")
NPM_SUFFIXES = (".ts", ".tsx", ".js", ".mjs", ".cjs")


def _syntax(compile_one):
    def check(path):
        try:
            compile_one(path)
            return []
        except (SyntaxError, ValueError) as error:
            return ["%s: %s" % (os.path.relpath(path, REPO), error)]

    return check


def _python(path):
    with open(path, "rb") as handle:
        compile(handle.read(), path, "exec", dont_inherit=True)


def _json(path):
    with open(path, "rb") as handle:
        json.loads(handle.read().decode("utf-8"))


def _npm_package(rel):
    if rel.startswith(NPM_EXCLUDED):
        return None
    return next((package for package in NPM_PACKAGES if rel.startswith(package)), None)


def edit_checks(rel):
    """The argv lists (with their cwd) that lint one repo-relative file, plus in-process checks.

    Returns `(commands, in_process)`: `commands` is a list of `(cwd, argv)`, `in_process` a list of
    callables taking the absolute path and returning problem lines. A file with no fast single-file
    check (Kotlin, Markdown, ...) gets neither; the task gate at a ticked task or turn end covers it.
    """
    commands, in_process = [], []
    package = _npm_package(rel)
    if package and rel.endswith(NPM_SUFFIXES):
        bin_dir = os.path.join(REPO, package, "node_modules", ".bin")
        inner = rel[len(package):]
        for tool, args in (("eslint", [inner]), ("prettier", ["--check", inner])):
            commands.append((os.path.join(REPO, package), [os.path.join(bin_dir, tool)] + args))
    elif rel.endswith((".js", ".mjs", ".cjs")):
        commands.append((REPO, ["node", "--check", rel]))
    elif rel.endswith(".py"):
        in_process.append(_syntax(_python))
    elif rel.endswith(".json"):
        in_process.append(_syntax(_json))
    elif rel.endswith(".sh"):
        commands.append((REPO, ["sh", "-n", rel]))
    elif rel.endswith(".rs"):
        commands.append((REPO, ["rustfmt", "--check", "--edition", "2024", rel]))
    return commands, in_process


def lint(rel):
    """Lint one edited file; returns the exit status (0 when clean, deleted or not lintable)."""
    path = os.path.join(REPO, rel)
    if not os.path.isfile(path):
        return 0  # a deleted or renamed file has nothing to lint
    commands, in_process = edit_checks(rel)
    status = 0
    for check in in_process:
        for line in check(path):
            print(line)
            status = 1
    for cwd, argv in commands:
        if os.path.isabs(argv[0]) and not os.path.exists(argv[0]):
            # Dependencies not installed: the edit lint cannot run, the task gate reports it.
            print("verify_gate: %s is missing; skipped the edit lint of %s" % (argv[0], rel), file=sys.stderr)
            continue
        try:
            result = subprocess.run(argv, cwd=cwd)
        except OSError as error:
            print("verify_gate: cannot run %s: %s" % (argv[0], error), file=sys.stderr)
            continue
        if result.returncode != 0:
            status = 1
    return status


def main(env=None):
    env = os.environ if env is None else env
    if env.get("VERIFY_TRIGGER") == "edit":
        rel = env.get("VERIFY_FILE", "")
        return lint(rel) if rel else 0
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, "-I", VERIFY])
    return 2  # not reached


if __name__ == "__main__":
    sys.exit(main())
