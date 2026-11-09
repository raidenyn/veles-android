#!/usr/bin/env python3
"""Release planner: per-component versions from tags + Conventional Commits via git-cliff.

Stdlib only; Python 3.8 compatible. Each release component (`.github/components.json` nodes with
`release: true`) is versioned from its own latest `<tag-prefix>X.Y.Z` tag reachable from HEAD, using
the pinned git-cliff over the component's release paths (`components.release_paths`). Untagged
components start at `initial-version` with notes limited to `<since>..HEAD` (git-cliff is never asked
for `--bumped-version` without a tag; cliff.toml has no `initial_tag`).

A component is RELEASE when its next version differs from its current one, or when the previous
combined release's manifest lacks it (bootstrap); otherwise CARRY. Fails closed on shallow
repositories and on tags that no published combined release accounts for.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # `python3 -I` does not add it
import components  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CLIFF_CONFIG = REPO / "cliff.toml"
RUNBOOK = "docs/releasing.md"
DATE_TAG_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}(\.\d+)?$")
SEMVER = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
DISPLAY = {"android": "Android", "web-extension": "Web extension", "installer": "Installer"}
# git-cliff groups (cliff.toml commit_parsers) -> bump reasons.
REASONS = {"Breaking": "breaking", "Features": "feat", "Bug Fixes": "fix", "Performance": "perf"}
REASON_ORDER = ("breaking", "feat", "fix", "perf")


class PlanError(Exception):
    pass


@dataclass
class Component:
    name: str
    display: str
    current: Optional[str]
    next: str
    action: str  # "RELEASE" | "CARRY"
    tag: str
    notes: str
    reasons: Dict[str, int] = field(default_factory=dict)


@dataclass
class Plan:
    release_tag: str
    previous_release: Optional[str]
    components: List[Component]
    commit: str = ""

    @property
    def any_release(self) -> bool:
        return any(c.action == "RELEASE" for c in self.components)


def _semver(version: str):
    match = re.match("^%s$" % SEMVER, version)
    if not match:
        raise PlanError("not a semantic version: %r" % version)
    return tuple(int(part) for part in match.groups())


def _run(args, cwd, what):
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    try:
        result = subprocess.run(
            [str(a) for a in args], cwd=str(cwd), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        )
    except OSError as error:
        raise PlanError("%s: cannot run %s: %s" % (what, args[0], error))
    if result.returncode != 0:
        raise PlanError("%s failed (exit %d): %s" % (what, result.returncode, result.stderr.strip()))
    return result.stdout


def _git(repo, *args):
    return _run(["git"] + list(args), repo, "git " + " ".join(args)).strip()


def _cliff(cliff, repo, args):
    return _run([cliff, "--config", CLIFF_CONFIG] + list(args), repo, "git-cliff " + " ".join(args))


def latest_combined_release(rows: List[dict]) -> Optional[str]:
    """Newest published (not draft, not prerelease) release whose tag is a combined date tag."""
    candidates = [
        r for r in rows
        if DATE_TAG_RE.match(str(r.get("tagName", "")))
        and not r.get("isDraft") and not r.get("isPrerelease") and r.get("publishedAt")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (str(r["publishedAt"]), r["tagName"]))["tagName"]


def next_release_tag(today: date, existing: List[str]) -> str:
    base = today.strftime("%Y.%m.%d")
    used = []
    for tag in existing:
        if tag == base:
            used.append(1)
        elif tag.startswith(base + ".") and DATE_TAG_RE.match(tag):
            used.append(int(tag[len(base) + 1:]))
    if not used:
        return base
    return "%s.%d" % (base, max(used) + 1)


def _date_tags(repo: Path) -> List[str]:
    """Date tags already used locally or on origin, so a stray tag is never reused."""
    tags = [t for t in _git(repo, "tag", "--list").splitlines() if DATE_TAG_RE.match(t)]
    if "origin" in _git(repo, "remote").split():
        for line in _git(repo, "ls-remote", "--tags", "origin").splitlines():
            ref = line.split("\t", 1)[-1]
            if ref.startswith("refs/tags/"):
                name = ref[len("refs/tags/"):]
                if name.endswith("^{}"):
                    name = name[:-3]
                if DATE_TAG_RE.match(name):
                    tags.append(name)
    return tags


def latest_component_tag(repo: Path, prefix: str) -> Optional[str]:
    pattern = re.compile("^%s%s$" % (re.escape(prefix), SEMVER))
    tags = [
        t for t in _git(repo, "tag", "--merged", "HEAD", "--list", prefix + "*").splitlines()
        if pattern.match(t)
    ]
    if not tags:
        return None
    return max(tags, key=lambda t: _semver(t[len(prefix):]))


def _include_args(paths):
    args = []
    for p in paths:
        args += ["--include-path", p + "**" if p.endswith("/") else p]
    return args


def _expected_bump(current: str, reasons: Dict[str, int]) -> str:
    major, minor, patch = _semver(current)
    if reasons.get("breaking"):
        return "%d.0.0" % (major + 1)
    if reasons.get("feat"):
        return "%d.%d.0" % (major, minor + 1)
    if reasons.get("fix") or reasons.get("perf"):
        return "%d.%d.%d" % (major, minor, patch + 1)
    return current


def _reasons(context_text: str) -> Dict[str, int]:
    counts = {}  # type: Dict[str, int]
    data = json.loads(context_text or "[]")
    for release in data:
        for commit in release.get("commits") or []:
            reason = REASONS.get(commit.get("group"))
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
    return {k: counts[k] for k in REASON_ORDER if k in counts}


def make_plan(
    repo: Path,
    cmap: dict,
    today: date,
    release_tags: List[str],
    previous_manifest: Optional[dict],
    cliff: Path,
) -> Plan:
    repo = Path(repo)
    if _git(repo, "rev-parse", "--is-shallow-repository") == "true":
        raise PlanError("shallow repository: fetch full history and tags (actions/checkout fetch-depth: 0)")
    commit = _git(repo, "rev-parse", "HEAD")
    release_tag = next_release_tag(today, list(release_tags) + _date_tags(repo))
    previous = (previous_manifest or {}).get("components", {}) if previous_manifest else None
    result = []
    for name, node in cmap["nodes"].items():
        if not node.get("release"):
            continue
        prefix = node["tag-prefix"]
        tag = latest_component_tag(repo, prefix)
        current = tag[len(prefix):] if tag else None
        if previous is not None and name in previous:
            listed = str(previous[name].get("version", ""))
            if current is None or _semver(current) < _semver(listed):
                raise PlanError(
                    "%s: the previous combined release lists %s but no %s%s tag is reachable from HEAD"
                    % (name, listed, prefix, listed)
                )
            if _semver(current) > _semver(listed):
                raise PlanError(
                    "%s: orphaned tag %s is newer than %s in the previous combined release; "
                    "see %s (re-run the failed publish job, or delete the orphaned tags and draft)"
                    % (name, tag, listed, RUNBOOK)
                )
        common = _include_args(components.release_paths(name, cmap)) + [
            "--tag-pattern", "^%s[0-9]+\\.[0-9]+\\.[0-9]+$" % re.escape(prefix),
        ]
        if tag:
            span = "%s..HEAD" % tag
        else:
            span = "%s..HEAD" % cmap["since"]
        notes = _cliff(cliff, repo, common + ["--unreleased", "--strip", "all", span]).strip()
        reasons = _reasons(_cliff(cliff, repo, common + ["--unreleased", "--context", span]))
        if tag:
            bumped = _cliff(cliff, repo, common + ["--bumped-version"]).strip()
            if not bumped.startswith(prefix):
                raise PlanError("%s: unexpected git-cliff --bumped-version output %r" % (name, bumped))
            nxt = bumped[len(prefix):]
            expected = _expected_bump(current, reasons)
            if _semver(nxt) != _semver(expected):
                raise PlanError(
                    "%s: git-cliff bumped %s to %s but the commits since %s imply %s"
                    % (name, current, nxt, tag, expected)
                )
        else:
            nxt = node["initial-version"]
        listed_before = previous is not None and name in previous
        action = "RELEASE" if nxt != current or not listed_before else "CARRY"
        result.append(Component(
            name=name, display=DISPLAY.get(name, name), current=current, next=nxt,
            action=action, tag=prefix + nxt, notes=notes, reasons=reasons,
        ))
    previous_release = previous_manifest.get("release") if previous_manifest else None
    return Plan(release_tag=release_tag, previous_release=previous_release, components=result, commit=commit)


def plan_to_json(plan: Plan) -> dict:
    return {
        "release_tag": plan.release_tag,
        "previous_release": plan.previous_release,
        "commit": plan.commit,
        "any_release": plan.any_release,
        "components": {
            c.name: {"current": c.current, "next": c.next, "action": c.action, "tag": c.tag}
            for c in plan.components
        },
    }


def release_refs(plan: dict) -> List[str]:
    """Tags to push for RELEASE components only; a version that already has its tag (bootstrap
    fill) and every CARRY component's tag stay where they are."""
    return [
        "refs/tags/" + c["tag"]
        for c in plan["components"].values()
        if c["action"] == "RELEASE" and c["next"] != c["current"]
    ]


def render_notes(plan: Plan, previous_manifest: Optional[dict]) -> str:
    since = (previous_manifest or {}).get("release") or plan.previous_release
    lines = ["| Component | Version | Status |", "|---|---|---|"]
    for c in plan.components:
        status = "new" if c.action == "RELEASE" else "unchanged since %s" % since
        lines.append("| %s | %s | %s |" % (c.display, c.next, status))
    for c in plan.components:
        if c.action != "RELEASE":
            continue
        lines += ["", "## %s %s" % (c.display, c.next), ""]
        lines.append(c.notes if c.notes else "No user-facing changes since the previous version.")
    lines += [
        "",
        "## Verify downloads",
        "",
        "Every asset is listed with its SHA-256 in `SHA256SUMS` and `release-manifest.json`:",
        "",
        "```",
        "sha256sum --check --ignore-missing SHA256SUMS",
        "gh attestation verify <asset> --owner raidenyn",
        "```",
        "",
        "Unchanged components are carried byte-for-byte from the release that introduced them.",
    ]
    return "\n".join(lines) + "\n"


def render_summary(plan: Plan) -> str:
    lines = [
        "### Release plan for %s" % plan.release_tag,
        "",
        "Previous combined release: %s" % (plan.previous_release or "none"),
        "",
        "| Component | Current | Next | Action | Reasons |",
        "|---|---|---|---|---|",
    ]
    for c in plan.components:
        reasons = ", ".join("%s: %d" % (k, v) for k, v in c.reasons.items())
        lines.append("| %s | %s | %s | %s | %s |" % (c.display, c.current or "-", c.next, c.action, reasons))
    refs = [r[len("refs/tags/"):] for r in release_refs(plan_to_json(plan))]
    if not plan.any_release:
        lines += ["", "Nothing to release."]
    else:
        lines += ["", "Tags to create: %s" % (", ".join("`%s`" % t for t in refs) or "none (existing tags reused)")]
    return "\n".join(lines) + "\n"


def _read_json(path):
    with open(str(path), encoding="utf-8") as handle:
        return json.load(handle)


def _write(path, text, append=False):
    with open(str(path), "a" if append else "w", encoding="utf-8") as handle:
        handle.write(text)


def _default_cliff():
    env = os.environ.get("VELES_GIT_CLIFF")
    if env:
        return env
    suffix = ".exe" if os.name == "nt" else ""
    return str(REPO / "build" / "release-tools" / "git-cliff" / ("git-cliff" + suffix))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="release_plan.py")
    parser.add_argument("--map", default=components.MAP_PATH)
    parser.add_argument("--repo", default=str(REPO))
    parser.add_argument("--git-cliff", default=None, help="git-cliff binary (default: $VELES_GIT_CLIFF or build/release-tools)")
    sub = parser.add_subparsers(dest="command")
    sub.required = True
    latest = sub.add_parser("latest", help="print the latest published combined release tag, or nothing")
    latest.add_argument("--releases-json", required=True)
    plan_cmd = sub.add_parser("plan", help="compute plan.json, GitHub outputs, summary and notes")
    plan_cmd.add_argument("--releases-json", required=True, help="gh release list --json tagName,isDraft,isPrerelease,publishedAt")
    plan_cmd.add_argument("--manifest-json", required=True, help="previous release-manifest.json, or {} when none")
    plan_cmd.add_argument("--out", required=True)
    plan_cmd.add_argument("--summary")
    plan_cmd.add_argument("--notes")
    plan_cmd.add_argument("--today", help="UTC date YYYY-MM-DD (default: today)")
    plan_cmd.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    plan_cmd.add_argument("--require-release", action="store_true", help="exit 1 when nothing is RELEASE")
    tags = sub.add_parser("tags", help="print refs/tags/<tag> of each RELEASE component, one per line")
    tags.add_argument("--plan-json", required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "latest":
            tag = latest_combined_release(_read_json(args.releases_json))
            if tag:
                print(tag)
            return 0
        if args.command == "tags":
            for ref in release_refs(_read_json(args.plan_json)):
                print(ref)
            return 0
        rows = _read_json(args.releases_json)
        previous = _read_json(args.manifest_json) or None
        latest_tag = latest_combined_release(rows)
        if previous is not None:
            if previous.get("schema") != 1:
                raise PlanError("previous release-manifest.json has unsupported schema %r" % previous.get("schema"))
            if previous.get("release") != latest_tag:
                raise PlanError(
                    "previous release-manifest.json is for %r but the latest combined release is %r"
                    % (previous.get("release"), latest_tag)
                )
        elif latest_tag is not None:
            raise PlanError("combined release %s has no release-manifest.json" % latest_tag)
        today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else datetime.now(timezone.utc).date()
        cmap = components.load_map(args.map)
        plan = make_plan(
            Path(args.repo), cmap, today, [str(r.get("tagName", "")) for r in rows], previous,
            Path(args.git_cliff or _default_cliff()),
        )
    except (PlanError, ValueError, OSError) as error:
        print("release_plan: %s" % error, file=sys.stderr)
        return 1

    data = plan_to_json(plan)
    _write(args.out, json.dumps(data, indent=2, sort_keys=True) + "\n")
    if args.summary:
        _write(args.summary, render_summary(plan), append=True)
    if args.notes:
        _write(args.notes, render_notes(plan, previous))
    outputs = []
    for c in plan.components:
        outputs += ["%s=%s" % (c.name, c.action), "%s-version=%s" % (c.name, c.next)]
    outputs += ["any-release=%s" % ("true" if plan.any_release else "false"), "release-tag=%s" % plan.release_tag]
    text = "".join(line + "\n" for line in outputs)
    if args.github_output:
        _write(args.github_output, text, append=True)
    else:
        sys.stdout.write(text)
    if args.require_release and not plan.any_release:
        print("release_plan: nothing to release", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
