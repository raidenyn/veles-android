#!/usr/bin/env python3
"""The verify gate's one command (`verify-gate.command` in `.agents/harness.json`).

The gate (`.agents/harness/verify-gate/`) sets VERIFY_TRIGGER. On `edit` this lints the one file in
VERIFY_FILE, where it is a lintable file. Every other trigger (`task`, `turn-end`, a run by hand) runs
the task gate of `scripts/verify.py` in this process:

- with uncommitted changes it reads the remembered passes, as the earlier turn-end hook did, so a node
  whose inputs are unchanged since it passed is `PASS (remembered)`, under a deadline that stops it
  cleanly before the gate's timeout;
- on a clean tree it passes at once when the branch pass already proves HEAD; otherwise it is the plain
  manual run, which never reads remembered passes and writes the branch pass the push gate checks;
- while another run holds `build/verify/lock` it waits for that run, for at most half of its budget,
  then runs (the other run's records make that cheap), instead of failing.

Stdlib only; Python 3.8 compatible. See docs/verify.md, "The verify gate".
"""
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")
HARNESS_JSON = os.path.join(REPO, ".agents", "harness.json")
# The run's budget is the gate's `timeoutSeconds` less this margin, so it ends before the gate kills
# the command. At most half of the budget goes to waiting for a held lock; the rest is the run's.
WAIT_MARGIN_SECONDS = 60
DEFAULT_TIMEOUT_SECONDS = 900  # the gate's default `timeoutSeconds`
POLL_SECONDS = 2
PROOF_CHECK_SECONDS = 30  # git budget for reading an existing branch pass
SHELLS = {"sh": "sh", "dash": "sh", "bash": "bash", "zsh": "zsh"}
# npm packages whose own eslint and prettier check their files.
NPM_PACKAGES = ("web-extension/", "native-bridge/")
NPM_EXCLUDED = ("native-bridge/host/", "native-bridge/installer/")
NPM_SUFFIXES = (".ts", ".tsx", ".js", ".mjs", ".cjs")
JS_SUFFIXES = (".js", ".mjs", ".cjs")
# Every suffix a single-file check lints without a package linter.
EDIT_SUFFIXES = (".py", ".json", ".sh", ".rs") + JS_SUFFIXES


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
        json.loads(handle.read().decode("utf-8-sig"))  # a UTF-8 BOM is valid JSON input


IN_PROCESS_CHECKS = {".py": _syntax(_python), ".json": _syntax(_json)}


def edit_globs():
    """The `verify-gate.editFiles` globs matching every kind `edit_checks` lints."""
    globs = ["**/*" + suffix for suffix in EDIT_SUFFIXES]
    for package in NPM_PACKAGES:
        globs.extend(package + "**/*" + suffix for suffix in NPM_SUFFIXES if suffix not in EDIT_SUFFIXES)
    return globs


def shell_of(path):
    """The shell whose `-n` checks this script: from its shebang, `sh` without one, None if unknown."""
    try:
        with open(path, "rb") as handle:
            first = handle.readline().decode("utf-8", "replace").strip()
    except OSError:
        return None
    if not first.startswith("#!"):
        return "sh"
    words = first[2:].split()
    if not words:
        return "sh"
    name = os.path.basename(words[0])
    if name == "env":
        rest = [word for word in words[1:] if not word.startswith("-")]
        name = os.path.basename(rest[0]) if rest else ""
    return SHELLS.get(name)


def vendored(rel):
    """Third-party code copied into the tree: it follows its own formatting, so it is not linted."""
    return rel.startswith("vendor/") or "/vendor/" in rel


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
    if vendored(rel):
        return commands, in_process
    suffix = os.path.splitext(rel)[1]
    package = _npm_package(rel)
    if package and suffix in NPM_SUFFIXES:
        bin_dir = os.path.join(REPO, package, "node_modules", ".bin")
        inner = rel[len(package):]
        for tool, args in (("eslint", [inner]), ("prettier", ["--check", inner])):
            commands.append((os.path.join(REPO, package), [os.path.join(bin_dir, tool)] + args))
    elif suffix in JS_SUFFIXES:
        commands.append((REPO, ["node", "--check", rel]))
    elif suffix in IN_PROCESS_CHECKS:
        in_process.append(IN_PROCESS_CHECKS[suffix])
    elif suffix == ".sh":
        shell = shell_of(os.path.join(REPO, rel))
        if shell is not None:
            commands.append((REPO, [shell, "-n", rel]))
    elif suffix == ".rs":
        commands.append((REPO, ["rustfmt", "--check", "--edition", "2024", rel]))
    return commands, in_process


def lint(rel):
    """Lint one edited file; returns the exit status (0 when clean, deleted or not lintable)."""
    path = os.path.join(REPO, rel)
    if not os.path.isfile(path):
        return 0  # a deleted or renamed file has nothing to lint
    commands, in_process = edit_checks(rel)
    status = 0
    notes = []
    for check in in_process:
        for line in check(path):
            print(line)
            status = 1
    for cwd, argv in commands:
        if os.path.isabs(argv[0]) and not os.path.exists(argv[0]):
            # Dependencies not installed: the edit lint cannot run, the task gate reports it.
            notes.append("verify_gate: %s is missing; skipped the edit lint of %s" % (argv[0], rel))
            continue
        try:
            result = subprocess.run(argv, cwd=cwd)
        except OSError as error:
            notes.append("verify_gate: cannot run %s: %s" % (argv[0], error))
            continue
        if result.returncode != 0:
            status = 1
    if status:
        # A pass prints nothing, because the gate adds a passing edit's output to the model.
        for note in notes:
            print(note, file=sys.stderr)
    return status


def tree_clean(repo):
    """True when `git status --porcelain` is empty, as `verify.py` reads it for the branch pass."""
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal", "--ignore-submodules=none"],
        cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0 and not result.stdout.strip()


def gate_timeout_seconds(path=HARNESS_JSON):
    """The gate's `timeoutSeconds` from `.agents/harness.json`, else the gate's default."""
    try:
        with open(path) as handle:
            timeout = json.load(handle)["verify-gate"].get("timeoutSeconds", DEFAULT_TIMEOUT_SECONDS)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        timeout = DEFAULT_TIMEOUT_SECONDS
    return timeout


def proof_covers_head(verify, repo):
    """True when a branch pass already proves HEAD; only meaningful on a clean tree."""
    try:
        ok, _message = verify.branch_pass.check(repo, time.monotonic() + PROOF_CHECK_SECONDS)
    except Exception:  # an unreadable record is no proof; the run decides
        return False
    return ok


def full_run(verify, ctx, timeout, clean=tree_clean):
    """The task gate, as `verify.main` runs it, with remembered passes and a wait on contention.

    Each attempt reads the tree again. On a clean tree an existing branch pass for HEAD is enough
    (a fresh run would delete it first); otherwise it is the plain run, which writes one. With
    uncommitted changes it reads the remembered passes, under a deadline that stops it cleanly
    before the gate's timeout. A held run lock is waited for, for at most half of the budget; an
    orphan that cannot be stopped is not waited for.

    Returns the exit code `verify.py` would: 0, 1 for a `FAIL` or timeout, 2 for a selection error,
    an unexpected `NOT RUN`, a contention or a run its deadline cut short.
    """
    try:
        cmap = verify.components.load_map(os.path.join(ctx.repo, ".github", "components.json"))
        table = verify.load_table(os.path.join(ctx.repo, "scripts", "verify-commands.json"), cmap)
    except (verify.TableError, OSError, ValueError) as error:
        ctx.log("verify: %s" % error)
        return 2
    budget = max(0, timeout - WAIT_MARGIN_SECONDS)
    started = ctx.clock.now()
    wait_until = started + budget / 2.0
    while True:
        dirty = not clean(ctx.repo)
        if not dirty and proof_covers_head(verify, ctx.repo):
            ctx.log("verify: the branch pass already covers HEAD on this clean tree; nothing to run")
            return 0
        # The plain run must have no deadline, or it writes no branch pass; the gate's timeout
        # bounds it. The remembered run writes none anyway, so it gets one and stops cleanly.
        ctx.deadline = started + budget if dirty else None
        result = verify.run_gate("task", None, ctx, table, cmap, use_passes=dirty)
        held = result.contended and verify.CONTENTION in result.errors
        if not held or ctx.clock.now() >= wait_until:
            break
        ctx.wait.sleep(POLL_SECONDS)
    if result.contended:
        for error in result.errors:
            ctx.log("verify: %s" % error)
        return 2
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
    code = verify.exit_code(result.results, result.errors)
    if code == 0 and result.incomplete:
        # `verify.py` counts steps its deadline left unrun as no problem; the gate would record that
        # as a full pass, so it is a failure here.
        ctx.log("verify: the task gate stopped at its deadline before every step ran; run it again")
        return 2
    return code


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
    return full_run(verify, verify.real_context(), gate_timeout_seconds())


if __name__ == "__main__":
    sys.exit(main())
