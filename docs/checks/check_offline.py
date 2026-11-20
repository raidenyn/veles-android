#!/usr/bin/env python3
"""Docs offline guard: `docs/checks/` scripts import stdlib and call only `git`.

Runs as the first step of `docs / agent-files` (spec: the `ci-pipeline` capability,
decouple-docs-ci design D1). It lists the tracked files of `docs/checks/` with
`git ls-files` and parses each `*.py` file with `ast`, so an import or a process call is
seen as the syntax it is, not as a spelling. Stdlib only; Python 3.8 compatible.
"""
import argparse
import ast
import collections
import os
import subprocess
import sys

Finding = collections.namedtuple("Finding", "path line message")

# Modules a docs check may import. Anything else must be a sibling module in docs/checks/.
STDLIB_ALLOWED = frozenset(
    (
        "__future__",
        "argparse",
        "ast",
        "collections",
        "contextlib",
        "copy",
        "importlib",
        "io",
        "json",
        "math",
        "os",
        "re",
        "shutil",
        "subprocess",
        "sys",
        "tempfile",
        "unittest",
    )
)

# `subprocess` attributes that may be referenced without starting a process.
SUBPROCESS_CONSTANTS = frozenset(
    ("PIPE", "DEVNULL", "STDOUT", "CalledProcessError", "CompletedProcess")
)

# `subprocess` functions that start a process.
PROCESS_FUNCTIONS = frozenset(
    ("run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput")
)

# `os` attributes that start a process, matched as name prefixes on both `os.X` and
# `from os import X`.
OS_PROCESS_PREFIXES = ("system", "popen", "exec", "spawn", "posix_spawn", "fork")

# `git` global options that point a command at another configuration or program.
GIT_FORBIDDEN_GLOBAL_EXACT = frozenset(("-c", "-C", "--config", "--exec-path", "--git-dir"))
GIT_FORBIDDEN_GLOBAL_PREFIXES = frozenset(
    (
        "--config=",
        "--config-env",
        "--exec-path=",
        "--git-dir=",
        "--upload-pack",
        "--receive-pack",
    )
)

# `git` subcommands that reach the network or run another program. The tree uses only ls-files,
# status, config, init, add, commit and cat-file; anything that fetches, pushes, spawns a foreign
# VCS or talks to a mail/imap server is named here.
GIT_FORBIDDEN_SUBCOMMANDS = frozenset(
    (
        "clone",
        "fetch",
        "fetch-pack",
        "pull",
        "push",
        "send-pack",
        "http-fetch",
        "http-push",
        "submodule",
        "ls-remote",
        "remote",
        "archive",
        "maintenance",
        "svn",
        "p4",
        "send-email",
        "imap-send",
    )
)

# Module names that must be imported directly, never reached through another module's attribute
# (`shutil.os.system`, `tempfile._os.popen`). The direct name is handled by the rules below.
MODULE_NAMES = ("os", "_os", "sys", "subprocess", "builtins")

# (file basename, enclosing top-level function) pairs that may use `importlib`.
IMPORTLIB_EXEMPT = (
    ("check_agent_files.py", "_load_engine"),
    ("test_check_agent_files.py", "_load_engine"),
)


def _git(root, *args):
    """Run a git query in ``root`` and return its stdout decoded as surrogate text."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "git %s failed in %s: %s"
            % (" ".join(args), root, result.stderr.decode("utf-8", "replace").strip())
        )
    return result.stdout.decode("utf-8", "surrogateescape")


def _split_z(output):
    return [entry for entry in output.split("\0") if entry]


def _os_process_name(name):
    return any(name.startswith(prefix) for prefix in OS_PROCESS_PREFIXES)


def _starts_with_git(node):
    return (
        isinstance(node, ast.List)
        and bool(node.elts)
        and isinstance(node.elts[0], ast.Constant)
        and node.elts[0].value == "git"
    )


def _is_git_argv(node):
    if _starts_with_git(node):
        return True
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Add)
        and _starts_with_git(node.left)
    )


def _constant_strings(nodes):
    values = []
    for node in nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.append(node.value)
    return values


class _Visitor(ast.NodeVisitor):
    """Findings for one parsed ``docs/checks`` file.

    The guard catches mistakes and obvious evasion, not a determined author: environment
    variables (``GIT_EXEC_PATH``, ``GIT_CONFIG_PARAMETERS``, ``GIT_SSH_COMMAND``), ``git config``
    keys and module rebinding are outside it, and review covers them.
    """

    def __init__(self, path, siblings, called):
        self.path = path
        self.basename = path.rsplit("/", 1)[-1]
        self.siblings = siblings
        self.called = called
        self.findings = []
        self.top_function = "<module>"
        self.depth = 0

    def add(self, node, message):
        self.findings.append(Finding(self.path, getattr(node, "lineno", 1), message))

    def visit_FunctionDef(self, node):
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node):
        self._visit_function(node)

    def _visit_function(self, node):
        previous = self.top_function
        if self.depth == 0:
            self.top_function = node.name
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1
        self.top_function = previous

    def visit_Import(self, node):
        for alias in node.names:
            top = alias.name.split(".")[0]
            if alias.asname is not None and alias.name in ("os", "subprocess", "sys"):
                self.add(
                    node,
                    "imports %r with an alias; aliases of os, subprocess and sys are forbidden"
                    % alias.name,
                )
            elif top == "importlib":
                if alias.name != "importlib.util" or alias.asname is not None:
                    self.add(node, "only `import importlib.util` is allowed, not %r" % alias.name)
            elif top not in STDLIB_ALLOWED and top not in self.siblings:
                self.add(
                    node,
                    "imports %r, which is not in STDLIB_ALLOWED or a sibling module" % top,
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        module = node.module or ""
        top = module.split(".")[0]
        for alias in node.names:
            if alias.name == "*":
                self.add(node, "star import from %r is forbidden" % (module or "."))
            elif module == "subprocess":
                self.add(
                    node,
                    "from subprocess import %r is forbidden; pass argv to subprocess.run" % alias.name,
                )
            elif module == "os" and _os_process_name(alias.name):
                self.add(
                    node,
                    "from os import %r is forbidden; os process functions are not allowed"
                    % alias.name,
                )
        if node.level == 0:
            if top == "importlib":
                self.add(node, "only `import importlib.util` is allowed, not %r" % module)
            elif top not in STDLIB_ALLOWED and top not in self.siblings:
                self.add(
                    node,
                    "imports %r, which is not in STDLIB_ALLOWED or a sibling module" % top,
                )
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr in MODULE_NAMES and not (
            isinstance(node.value, ast.Name) and node.value.id == node.attr
        ):
            self.add(
                node,
                "re-exports the %s module through another module; import it directly" % node.attr,
            )
        if isinstance(node.value, ast.Name):
            name = node.value.id
            if name == "os" and _os_process_name(node.attr):
                self.add(
                    node,
                    "os.%s references a process function; os process calls are forbidden"
                    % node.attr,
                )
            elif name == "sys" and node.attr == "modules":
                self.add(node, "sys.modules is forbidden")
            elif name == "subprocess":
                if node.attr not in SUBPROCESS_CONSTANTS and id(node) not in self.called:
                    self.add(
                        node,
                        "references subprocess.%s outside a call; aliasing is forbidden"
                        % node.attr,
                    )
            elif name == "importlib":
                if (self.basename, self.top_function) not in IMPORTLIB_EXEMPT:
                    self.add(node, "importlib use outside IMPORTLIB_EXEMPT is forbidden")
        if node.attr in ("__dict__", "__builtins__"):
            self.add(node, "%s is forbidden" % node.attr)
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in ("__builtins__", "builtins"):
            self.add(node, "%s is forbidden" % node.id)
        self.generic_visit(node)

    def visit_List(self, node):
        if _starts_with_git(node):
            self.check_git_tokens(node, _constant_strings(node.elts[1:]))
        self.generic_visit(node)

    def visit_Call(self, node):
        function = node.func
        if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
            if function.value.id == "subprocess":
                self.check_subprocess_call(node, function.attr)
        if isinstance(function, ast.Name):
            if function.id in ("vars", "globals", "locals"):
                self.add(node, "%s() is forbidden" % function.id)
            elif function.id in ("eval", "exec", "compile", "__import__"):
                self.add(node, "%s() is forbidden" % function.id)
            elif function.id == "getattr":
                self.check_getattr(node)
            elif function.id in ("_git", "git"):
                args = node.args[1:] if function.id == "_git" else node.args
                self.check_git_tokens(node, _constant_strings(args))
        self.generic_visit(node)

    def check_subprocess_call(self, node, attr):
        if attr in SUBPROCESS_CONSTANTS:
            return
        if attr not in PROCESS_FUNCTIONS:
            self.add(node, "subprocess.%s(...) is not an allowed subprocess call" % attr)
            return
        for keyword in node.keywords:
            if keyword.arg is None:
                self.add(node, "subprocess.%s(...) must not expand a keyword mapping" % attr)
                break
            if keyword.arg == "executable":
                self.add(node, "subprocess.%s(...) must not pass executable=" % attr)
                break
            if keyword.arg == "shell" and not (
                isinstance(keyword.value, ast.Constant) and keyword.value.value is False
            ):
                self.add(node, "subprocess.%s(...) must not pass shell=" % attr)
                break
        if not node.args:
            self.add(node, "subprocess.%s(...) needs its argv as the first positional argument" % attr)
        elif not _is_git_argv(node.args[0]):
            self.add(
                node,
                'subprocess.%s(...) first argument must be a literal list starting with "git"' % attr,
            )

    def check_getattr(self, node):
        if len(node.args) < 2:
            self.add(node, "getattr with fewer than two arguments is forbidden")
            return
        first, second = node.args[0], node.args[1]
        if isinstance(first, ast.Name) and first.id in ("os", "subprocess", "sys", "importlib"):
            self.add(node, "getattr(%s, ...) is forbidden" % first.id)
        elif not (isinstance(second, ast.Constant) and isinstance(second.value, str)):
            self.add(node, "getattr with a non-constant attribute name is forbidden")

    def check_git_tokens(self, node, tokens):
        for token in tokens:
            if token.startswith("-"):
                if token in GIT_FORBIDDEN_GLOBAL_EXACT or any(
                    token.startswith(prefix) for prefix in GIT_FORBIDDEN_GLOBAL_PREFIXES
                ):
                    self.add(node, "git global option %r is forbidden" % token)
                continue
            if token in GIT_FORBIDDEN_SUBCOMMANDS:
                self.add(node, "git subcommand %r is forbidden" % token)
            return


def _collect_called_attributes(tree):
    called = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ):
            called.add(id(node.func))
    return called


def scan(root):
    """Findings for every tracked file in ``docs/checks/`` under ``root``."""
    tracked = _split_z(_git(root, "ls-files", "-z", "docs/checks"))
    python_files = [path for path in tracked if path.endswith(".py")]
    siblings = set(path.rsplit("/", 1)[-1][:-3] for path in python_files)
    findings = []
    for path in sorted(tracked):
        if not path.endswith(".py"):
            if path.rsplit("/", 1)[-1] != "AGENTS.md":
                findings.append(Finding(path, 1, "is not a Python file"))
            continue
        full = os.path.join(root, *path.split("/"))
        with open(full, "rb") as handle:
            data = handle.read()
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            findings.append(Finding(path, 1, "is not valid UTF-8: %s" % error))
            continue
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as error:
            findings.append(Finding(path, error.lineno or 1, "does not parse: %s" % error.msg))
            continue
        visitor = _Visitor(path, siblings, _collect_called_attributes(tree))
        visitor.visit(tree)
        findings.extend(visitor.findings)
    return sorted(findings, key=lambda finding: (finding.path, finding.line))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check that docs check scripts stay offline.")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    args = parser.parse_args(argv)
    try:
        findings = scan(args.root)
    except (OSError, RuntimeError) as error:
        print("error %s: %s" % (args.root, " ".join(str(error).split())), file=sys.stderr)
        return 2
    for finding in findings:
        print("error %s:%d: %s" % (finding.path, finding.line, finding.message))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
