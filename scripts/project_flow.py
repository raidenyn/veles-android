#!/usr/bin/env python3
"""project_flow: derive GitHub Project statuses from facts (stdlib only).

Run as `python3 -I scripts/project_flow.py <sync|next|start|followup|check> ...`.
Exit 0 success, 2 usage, 3 a stop (`stop: <reason>: <detail>` on stdout).
"""
import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Optional, Protocol

DEFAULT_CONFIG = ".github/project-flow.json"
ROLE_KEYS = ("backlog", "ready", "in_progress", "in_review", "blocked", "done")
CONFIG_KEYS = ("status_field", "roles", "order_field", "blocked_label")
STOP_REASONS = (
    "not-enabled", "variable-unreadable", "nothing-ready", "tie", "missing-order",
    "incomplete-read", "not-ready", "collision", "missing-option", "config-invalid",
    "resume-disagrees", "no-order-room", "parent-unordered", "parent-as-blocker",
    "missing-scope",
)
SCOPE_FIX = "run gh auth refresh -s project"

_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_ISSUE_REF = re.compile(r"#\d+")
_NODE_ID = re.compile(r"^[A-Z]{1,4}_[A-Za-z0-9_-]{8,}$")
_DIGITS = re.compile(r"^\d+$")


class GhError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Stop(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__("%s: %s" % (reason, detail))
        self.reason = reason
        self.detail = detail

    def __eq__(self, other):
        return isinstance(other, Stop) and (self.reason, self.detail) == (other.reason, other.detail)

    def __hash__(self):
        return hash((self.reason, self.detail))


class Transport(Protocol):
    def graphql(self, query: str, variables: dict) -> dict: ...

    def rest(self, method: str, path: str, body: Optional[dict] = None) -> Any: ...


class GhTransport:
    """Shells to the gh CLI."""

    @staticmethod
    def _run(args, stdin=None):
        try:
            done = subprocess.run(["gh"] + args, input=stdin, capture_output=True, text=True)
        except OSError as exc:
            raise GhError("cannot run gh: %s" % exc)
        if done.returncode != 0:
            raise GhError((done.stderr or done.stdout).strip() or "gh exited %d" % done.returncode)
        return done.stdout

    def graphql(self, query: str, variables: Optional[dict] = None) -> dict:
        args = ["api", "graphql", "-f", "query=" + query]
        for name, value in (variables or {}).items():
            if isinstance(value, (int, bool)) and not isinstance(value, str):
                args += ["-F", "%s=%s" % (name, json.dumps(value))]
            else:
                args += ["-f", "%s=%s" % (name, value)]
        return self._decode(self._run(args))

    def rest(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        args = ["api", "-X", method, path]
        stdin = None
        if body is not None:
            args += ["--input", "-"]
            stdin = json.dumps(body)
        return self._decode(self._run(args, stdin))

    @staticmethod
    def _decode(text: str) -> Any:
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except ValueError as exc:
            raise GhError("invalid JSON from gh: %s" % exc)


@dataclass(frozen=True)
class Config:
    status_field: str
    roles: dict
    order_field: str
    blocked_label: str


def _bad_value(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty string"
    if _URL.search(value):
        return "must not be a URL"
    if _ISSUE_REF.search(value):
        return "must not contain an issue reference"
    if _NODE_ID.match(value):
        return "must not be a node id"
    if _DIGITS.match(value):
        return "must not be all digits"
    return None


def validate_config(raw: Any) -> list:
    if not isinstance(raw, dict):
        return ["config must be a JSON object"]
    errors = []
    for key in raw:
        if key not in CONFIG_KEYS:
            errors.append("%s: unknown key" % key)
    for key in ("status_field", "order_field", "blocked_label"):
        if key not in raw:
            errors.append("%s: missing" % key)
        else:
            problem = _bad_value(raw[key])
            if problem:
                errors.append("%s: %s" % (key, problem))
    roles = raw.get("roles")
    if roles is None:
        errors.append("roles: missing")
    elif not isinstance(roles, dict):
        errors.append("roles: must be an object")
    else:
        for key in roles:
            if key not in ROLE_KEYS:
                errors.append("roles.%s: unknown role" % key)
        for key in ROLE_KEYS:
            if key not in roles:
                errors.append("roles.%s: missing" % key)
            else:
                problem = _bad_value(roles[key])
                if problem:
                    errors.append("roles.%s: %s" % (key, problem))
    return errors


def load_config(path: str) -> Config:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except OSError as exc:
        raise Stop("config-invalid", "cannot read %s: %s" % (path, exc.strerror or exc))
    except ValueError as exc:
        raise Stop("config-invalid", "%s is not valid JSON: %s" % (path, exc))
    errors = validate_config(raw)
    if errors:
        raise Stop("config-invalid", "; ".join(errors))
    return Config(raw["status_field"], dict(raw["roles"]), raw["order_field"], raw["blocked_label"])


def map_gh_error(e: GhError) -> Optional[Stop]:
    if "missing required scopes" in getattr(e, "message", str(e)):
        return Stop("missing-scope", SCOPE_FIX)
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="project_flow.py")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("sync", "next", "start", "followup", "check"):
        p = sub.add_parser(name)
        p.add_argument("--project", metavar="URL")
        p.add_argument("--repo", metavar="OWNER/NAME")
        p.add_argument("--mode", choices=("action", "agent"), default="agent")
        p.add_argument("--config", default=DEFAULT_CONFIG, metavar="PATH")
        p.add_argument("--dry-run", action="store_true")
    return parser


def _check(args) -> int:
    load_config(args.config)
    if args.project:
        print("usage: check with --project is not implemented yet", file=sys.stderr)
        return 2
    print("config ok: %s" % args.config)
    return 0


def main(argv: list, transport: Optional[Transport] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        if args.command == "check":
            return _check(args)
        print("usage: %s is not implemented yet" % args.command, file=sys.stderr)
        return 2
    except Stop as stop:
        print("stop: %s: %s" % (stop.reason, stop.detail))
        return 3
    except GhError as err:
        stop = map_gh_error(err)
        if stop is None:
            print("error: %s" % err.message, file=sys.stderr)
            return 1
        print("stop: %s: %s" % (stop.reason, stop.detail))
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], GhTransport()))
