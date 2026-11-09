#!/usr/bin/env python3
"""Conventional Commits validator (stdlib only, Python 3.8 compatible).

CLI: python3 -I scripts/commitlint.py (--file PATH | --message TEXT) [--allow-git-generated]
Exit codes: 0 valid, 1 invalid (reasons on stderr), 2 usage.
"""
import re
import sys

TYPES = "feat|fix|perf|refactor|docs|test|build|ci|chore|style|revert"
HEADER_RE = re.compile(
    r"^(?P<type>" + TYPES + r")(?P<scope>\([a-z0-9][a-z0-9./-]*\))?(?P<bang>!)?: \S.*$"
)
BREAKING_RE = re.compile(r"^(BREAKING CHANGE|BREAKING-CHANGE):", re.MULTILINE)
GIT_GENERATED_RE = re.compile(r'^(Merge |Revert "|fixup! |squash! |amend! )')
MAX_HEADER = 100


def _normalise(message):
    text = message.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in text.split("\n") if not ln.startswith("#")]
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def validate(message, *, allow_git_generated=False):
    """Return a list of reasons the message is invalid (empty = valid)."""
    lines = _normalise(message)
    if not lines or not lines[0].strip():
        return ["commit message is empty"]
    header = lines[0]
    if allow_git_generated and GIT_GENERATED_RE.match(header):
        return []
    reasons = []
    match = HEADER_RE.match(header)
    if not match:
        reasons.append(
            "header must match '<type>(<scope>)!: <subject>' with type one of "
            + ", ".join(TYPES.split("|"))
            + " and a lowercase scope: %r" % header
        )
    if len(header) > MAX_HEADER:
        reasons.append(
            "header is %d characters; the maximum is %d" % (len(header), MAX_HEADER)
        )
    if len(lines) > 1 and lines[1].strip():
        reasons.append("the second line must be blank (separate header from body)")
    body = "\n".join(lines[1:])
    if BREAKING_RE.search(body) and not (match and match.group("bang")):
        if match:
            example = "%s%s!: ..." % (match.group("type"), match.group("scope") or "")
        else:
            example = "<type>(<scope>)!: ..."
        reasons.append(
            "a BREAKING CHANGE footer requires '!' in the header, e.g. '%s'" % example
        )
    return reasons


def main(argv):
    file_path = None
    message = None
    allow = False
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--allow-git-generated":
            allow = True
        elif arg in ("--file", "--message") and args:
            value = args.pop(0)
            if arg == "--file":
                file_path = value
            else:
                message = value
        else:
            sys.stderr.write("usage: commitlint.py (--file PATH | --message TEXT) [--allow-git-generated]\n")
            return 2
    if (file_path is None) == (message is None):
        sys.stderr.write("usage: commitlint.py (--file PATH | --message TEXT) [--allow-git-generated]\n")
        return 2
    if file_path is not None:
        try:
            with open(file_path, "r", encoding="utf-8", newline="") as handle:
                message = handle.read()
        except OSError as exc:
            sys.stderr.write("cannot read %s: %s\n" % (file_path, exc))
            return 2
    reasons = validate(message, allow_git_generated=allow)
    for reason in reasons:
        sys.stderr.write("commitlint: %s\n" % reason)
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
