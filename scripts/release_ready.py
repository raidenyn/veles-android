#!/usr/bin/env python3
"""Publish readiness: decides from `toJSON(needs)` and plan.json whether `draft` may run.

Stdlib only; Python 3.8 compatible. Ready only when every ALWAYS job succeeded and, per component,
each of its jobs succeeded (RELEASE) or was skipped (CARRY). Anything else blocks drafting.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List

COMPONENT_JOBS = {
    "android": ["build-android", "sign-android"],
    "web-extension": ["build-web-extension"],
    "installer": [
        "build-windows", "verify-windows", "sign-windows",
        "build-macos", "verify-macos", "sign-macos", "gate-installer",
    ],
}
ALWAYS = ["guard", "plan", "supply-chain", "carry"]
WANT = {"RELEASE": "success", "CARRY": "skipped"}


def _result(needs: dict, job: str):
    entry = needs.get(job)
    return entry.get("result") if isinstance(entry, dict) else None


def ready(needs: dict, plan: dict) -> List[str]:
    """Problems that block drafting; an empty list means ready."""
    problems = []
    for job in ALWAYS:
        result = _result(needs, job)
        if result != "success":
            problems.append("%s: expected success, got %s" % (job, result or "missing"))
    planned = plan.get("components") or {}
    for component in sorted(set(planned) - set(COMPONENT_JOBS)):
        problems.append("%s: unknown component in plan" % component)
    any_release = False
    for component, jobs in COMPONENT_JOBS.items():
        info = planned.get(component)
        action = info.get("action") if isinstance(info, dict) else None
        if action not in WANT:
            problems.append("%s: plan action is %s, expected RELEASE or CARRY" % (component, action or "missing"))
            continue
        any_release = any_release or action == "RELEASE"
        for job in jobs:
            result = _result(needs, job)
            if result != WANT[action]:
                problems.append(
                    "%s (%s %s): expected %s, got %s" % (job, component, action, WANT[action], result or "missing")
                )
    if not any_release:
        problems.append("plan: nothing to release")
    return problems


def _read_json(path):
    with open(str(path), encoding="utf-8") as handle:
        return json.load(handle)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="release_ready.py")
    sub = parser.add_subparsers(dest="command")
    sub.required = True
    cmd = sub.add_parser("ready", help="exit 0 only when drafting may proceed")
    cmd.add_argument("--needs-json", required=True, help="file containing toJSON(needs)")
    cmd.add_argument("--plan-json", required=True)
    args = parser.parse_args(argv)
    try:
        problems = ready(_read_json(args.needs_json), _read_json(args.plan_json))
    except (OSError, ValueError, AttributeError) as error:
        print("release_ready: %s" % error, file=sys.stderr)
        return 1
    for problem in problems:
        print("release_ready: not ready: %s" % problem, file=sys.stderr)
    if problems:
        return 1
    print("release_ready: ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
