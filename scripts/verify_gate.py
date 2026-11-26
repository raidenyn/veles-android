#!/usr/bin/env python3
"""The verify gate's one command (`verify-gate.command` in `.agents/harness.json`).

The gate (`.agents/harness/verify-gate/`) sets VERIFY_TRIGGER. On `edit` this lints the one file in
VERIFY_FILE, where it is a lintable file. Every other trigger (`task`, `turn-end`, a run by hand) runs
the task gate of `scripts/verify.py` in this process:

- with uncommitted changes it reads the remembered passes, as the earlier turn-end hook did, so a node
  whose inputs are unchanged since it passed is `PASS (remembered)`;
- on a clean tree it is the plain manual run, which never reads them and writes the branch pass the
  push gate checks;
- while another run holds `build/verify/lock` it waits for that run, then runs (its records make the
  remembered run cheap), instead of failing.

Stdlib only; Python 3.8 compatible. See docs/verify.md, "The verify gate".
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")
HARNESS_JSON = os.path.join(REPO, ".agents", "harness.json")
# How long a run waits for a contended lock: the gate's own timeout less this margin, so the wait
# ends (and reports the contention) before the gate kills the command.
WAIT_MARGIN_SECONDS = 60
DEFAULT_TIMEOUT_SECONDS = 900  # the gate's default `timeoutSeconds`
POLL_SECONDS = 2
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


def tree_clean(repo):
    """True when `git status --porcelain` is empty: nothing a branch pass would reject."""
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0 and not result.stdout.strip()


def wait_limit_seconds(path=HARNESS_JSON):
    """The gate's `timeoutSeconds` less `WAIT_MARGIN_SECONDS`; never negative."""
    try:
        with open(path) as handle:
            timeout = json.load(handle)["verify-gate"].get("timeoutSeconds", DEFAULT_TIMEOUT_SECONDS)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        timeout = DEFAULT_TIMEOUT_SECONDS
    return max(0, timeout - WAIT_MARGIN_SECONDS)


def full_run(verify, ctx, use_passes, wait_limit):
    """The task gate, as `verify.main` runs it, with `use_passes` and a wait on contention.

    Returns the exit code `verify.py` would: 0, 1 for a `FAIL` or timeout, 2 for a selection error,
    an unexpected `NOT RUN` or a contention that outlasted `wait_limit`.
    """
    try:
        cmap = verify.components.load_map(os.path.join(ctx.repo, ".github", "components.json"))
        table = verify.load_table(os.path.join(ctx.repo, "scripts", "verify-commands.json"), cmap)
    except (verify.TableError, OSError, ValueError) as error:
        ctx.log("verify: %s" % error)
        return 2
    waited_until = ctx.clock.now() + wait_limit
    while True:
        result = verify.run_gate("task", None, ctx, table, cmap, use_passes=use_passes)
        if not result.contended:
            break
        if ctx.clock.now() >= waited_until:
            for error in result.errors:
                ctx.log("verify: %s" % error)
            return 2
        ctx.wait.sleep(POLL_SECONDS)
    if result.errors:
        for line in verify.render(result.results, result.errors).splitlines():
            ctx.log(line)
        return verify.exit_code(result.results, result.errors)
    selection = result.selection
    if selection is not None and not selection.files:
        ctx.log("verify: nothing changed against %s" % selection.base)
        return 0
    if selection is not None and selection.only_unowned:
        ctx.log("verify: only unowned paths changed (%s)" % ", ".join(selection.files))
        return 0
    for line in verify.render(result.results, result.errors).splitlines():
        ctx.log(line)
    return verify.exit_code(result.results, result.errors)


def load_verify():
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    import verify  # noqa: E402  (scripts/verify.py)

    return verify


def main(env=None):
    env = os.environ if env is None else env
    if env.get("VERIFY_TRIGGER") == "edit":
        rel = env.get("VERIFY_FILE", "")
        return lint(rel) if rel else 0
    verify = load_verify()
    ctx = verify.real_context()
    return full_run(verify, ctx, use_passes=not tree_clean(ctx.repo), wait_limit=wait_limit_seconds())


if __name__ == "__main__":
    sys.exit(main())
