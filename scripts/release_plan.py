#!/usr/bin/env python3
"""Release planner: per-component versions from tags + Conventional Commits via git-cliff.

Stdlib only; Python 3.8 compatible. Each release component (`.github/components.json` nodes with
`release: true`) is versioned from its own latest `<tag-prefix>X.Y.Z` tag reachable from HEAD, using
the pinned git-cliff over the component's release paths (`components.release_paths`). Untagged
components start at `initial-version` with notes limited to `<since>..HEAD` (git-cliff is never asked
for `--bumped-version` without a tag; cliff.toml has no `initial_tag`).

A component may declare `legacy-release` (`paths` plus an `epoch-path`): commits whose tree lacks
the epoch path are planned with the legacy paths in a second pass, with the new-layout commits
passed as `--skip-commit`, and merged back by commit id.

A component is RELEASE when its next version differs from its current one (an untagged component is
RELEASE at its initial-version); otherwise CARRY. Fails closed on shallow repositories and on tags
that no published combined release manifest accounts for (newer than listed, or not listed at all).
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


def _tree_has(repo: Path, sha: str, path: str) -> bool:
    """Whether the commit's tree contains `path` (`git cat-file -e <sha>:<path>`)."""
    result = subprocess.run(
        ["git", "cat-file", "-e", "%s:%s" % (sha, path)],
        cwd=str(repo), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _range_commits(repo: Path, lower: Optional[str]) -> List[str]:
    """Commit ids in `<lower>..HEAD` (all reachable when lower is None)."""
    span = "%s..HEAD" % lower if lower else "HEAD"
    return [line for line in _git(repo, "rev-list", span).splitlines() if line]


def legacy_commits(repo: Path, tag: Optional[str], epoch_path: str) -> List[str]:
    """Old-layout commits in `<tag>..HEAD`: those whose tree lacks `epoch_path`.

    `tag` is the lower bound of the range (the component tag or the map's `since`); without it
    every commit reachable from HEAD is in the old-layout epoch.
    """
    return [sha for sha in _range_commits(repo, tag) if not _tree_has(repo, sha, epoch_path)]


def legacy_skip_args(repo: Path, tag: Optional[str], epoch_path: str) -> List[str]:
    """`--skip-commit` for every new-layout commit in `<tag>..HEAD`, so only old ones remain."""
    args = []  # type: List[str]
    for sha in _range_commits(repo, tag):
        if _tree_has(repo, sha, epoch_path):
            args += ["--skip-commit", sha]
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


def _commit_groups(context_text: str) -> Dict[str, str]:
    """Commit id -> git-cliff group from a `--context` payload; first occurrence wins."""
    groups = {}  # type: Dict[str, str]
    for release in json.loads(context_text or "[]"):
        for commit in release.get("commits") or []:
            cid = commit.get("id")
            if cid and cid not in groups:
                groups[cid] = commit.get("group")
    return groups


def _reasons(context_text: str) -> Dict[str, int]:
    counts = {}  # type: Dict[str, int]
    for group in _commit_groups(context_text).values():
        reason = REASONS.get(group)
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    return {k: counts[k] for k in REASON_ORDER if k in counts}


def _merge_reasons(first: Dict[str, int], second: Dict[str, int]) -> Dict[str, int]:
    """Union of two reason counts, in the canonical reason order."""
    return {
        key: first.get(key, 0) + second.get(key, 0)
        for key in REASON_ORDER
        if first.get(key) or second.get(key)
    }


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
        if tag and (previous is None or name not in previous):
            raise PlanError(
                "%s: tag %s exists but no published combined release manifest lists %s; "
                "see %s (re-run the failed publish job, or delete the orphaned tags and draft)"
                % (name, tag, name, RUNBOOK)
            )
        if previous is not None and name in previous:
            listed = str(previous[name].get("version", ""))
            if current is None or _semver(current) < _semver(listed):
                raise PlanError(
                    "%s: the previous combined release lists %s but no %s%s tag is reachable from HEAD; see %s"
                    % (name, listed, prefix, listed, RUNBOOK)
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
        context = _cliff(cliff, repo, common + ["--unreleased", "--context", span])
        main_commits = _commit_groups(context)
        reasons = _reasons(context)
        # Legacy pass: old-layout commits keep their attribution while the component moves.
        legacy_added = False
        legacy = node.get("legacy-release")
        lower = tag if tag else cmap["since"]
        if legacy:
            old = legacy_commits(repo, lower, legacy["epoch-path"])
            if old:
                old_ids = set(old)
                skip_args = legacy_skip_args(repo, lower, legacy["epoch-path"])
                # Deduplicate by commit id: commits the main pass already matched are skipped here.
                for cid in main_commits:
                    if cid in old_ids:
                        skip_args += ["--skip-commit", cid]
                legacy_common = _include_args(legacy["paths"]) + skip_args + [
                    "--tag-pattern", "^%s[0-9]+\\.[0-9]+\\.[0-9]+$" % re.escape(prefix),
                ]
                legacy_context = _cliff(
                    cliff, repo, legacy_common + ["--unreleased", "--context", span]
                )
                if _commit_groups(legacy_context):
                    legacy_added = True
                    legacy_notes = _cliff(
                        cliff, repo, legacy_common + ["--unreleased", "--strip", "all", span]
                    ).strip()
                    notes = "\n".join(part for part in (notes, legacy_notes) if part)
                    reasons = _merge_reasons(reasons, _reasons(legacy_context))
        if tag and not legacy_added:
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
        elif tag:
            # The legacy pass contributed commits, which --bumped-version cannot see.
            nxt = _expected_bump(current, reasons)
        else:
            nxt = node["initial-version"]
        # Untagged components (never released) start at initial-version; tagged ones are in the manifest.
        action = "RELEASE" if nxt != current else "CARRY"
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
    """Tags to push for RELEASE components only; every CARRY component's tag stays where it is."""
    refs = []
    for name, c in plan["components"].items():
        if c["action"] != "RELEASE":
            continue
        if c["next"] == c["current"]:
            raise PlanError("%s: RELEASE without a new version (%s)" % (name, c["next"]))
        refs.append("refs/tags/" + c["tag"])
    return refs


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
