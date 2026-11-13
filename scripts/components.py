#!/usr/bin/env python3
"""Component map: path ownership, change-based node selection and map checks.

Stdlib only; Python 3.8 compatible. See .github/components.json.
"""
import argparse
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join(REPO, ".github", "components.json")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
ZERO_SHA = "0" * 40


def _check_paths(owner, paths):
    if not isinstance(paths, list):
        raise ValueError("%s: paths must be a list" % owner)
    for p in paths:
        if not isinstance(p, str) or not p:
            raise ValueError("%s: invalid path %r" % (owner, p))
        if any(c in p for c in "*?[]{}"):
            raise ValueError("%s: wildcard in path %r (use a directory prefix or exact file)" % (owner, p))
        if p.startswith("/") or ".." in p.split("/") or "\\" in p:
            raise ValueError("%s: path %r must be repo-relative with forward slashes" % (owner, p))


def _check_node_names(owner, names, nodes):
    if not isinstance(names, list) or not names:
        raise ValueError("%s: must be a non-empty list of node names" % owner)
    for name in names:
        if not isinstance(name, str) or name not in nodes:
            raise ValueError("%s: unknown node %r" % (owner, name))


def parse_map(text):
    cmap = json.loads(text)
    if not SHA_RE.match(str(cmap.get("since", ""))):
        raise ValueError("since: must be a full 40-hex commit SHA")
    for key in ("global", "ci-global", "unowned"):
        _check_paths(key, cmap.get(key, []))
        cmap.setdefault(key, [])
    nodes = cmap.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        raise ValueError("nodes: must be a non-empty object")
    for name, node in nodes.items():
        if not isinstance(node, dict):
            raise ValueError("%s: node must be an object" % name)
        _check_paths(name, node.get("paths"))
        if "ci-paths" in node:
            _check_paths(name + " ci-paths", node["ci-paths"])
        if "release-paths" in node:
            _check_paths(name + " release-paths", node["release-paths"])
        if "jobs" in node:
            jobs = node["jobs"]
            if not isinstance(jobs, list) or not all(isinstance(j, str) and j for j in jobs):
                raise ValueError("%s: jobs must be a list of job names" % name)
        if "legacy-release" in node:
            legacy = node["legacy-release"]
            if not isinstance(legacy, dict):
                raise ValueError("%s: legacy-release must be an object" % name)
            _check_paths(name + " legacy-release", legacy.get("paths"))
            epoch = legacy.get("epoch-path")
            if not isinstance(epoch, str) or not epoch:
                raise ValueError("%s: legacy-release epoch-path must be a path" % name)
            _check_paths(name + " legacy-release epoch-path", [epoch])
        if not isinstance(node.get("release"), bool):
            raise ValueError("%s: 'release' must be true or false" % name)
        if node["release"]:
            for field in ("tag-prefix", "initial-version"):
                if not node.get(field):
                    raise ValueError("%s: release node is missing %s" % (name, field))
            if not SEMVER_RE.match(node["initial-version"]):
                raise ValueError("%s: initial-version %r is not semver" % (name, node["initial-version"]))
    for key in ("global-overrides", "retired-paths"):
        section = cmap.get(key, {})
        if not isinstance(section, dict):
            raise ValueError("%s: must be an object" % key)
        cmap[key] = section
    for path, names in cmap["global-overrides"].items():
        _check_paths("global-overrides", [path])
        if path.endswith("/"):
            raise ValueError("global-overrides: key %r must be an exact file name" % path)
        _check_node_names("global-overrides " + path, names, nodes)
    for prefix, names in cmap["retired-paths"].items():
        _check_paths("retired-paths", [prefix])
        _check_node_names("retired-paths " + prefix, names, nodes)
    return cmap


def load_map(path):
    with open(str(path), encoding="utf-8") as handle:
        return parse_map(handle.read())


def _matches(file, paths):
    for p in paths:
        if p.endswith("/"):
            if file.startswith(p):
                return True
        elif file == p:
            return True
    return False


def owner_nodes(file, cmap):
    """None: global or unaccounted (select all); a set (possibly empty): accounted."""
    for path, names in cmap.get("global-overrides", {}).items():
        if file == path:
            return set(names)
    if _matches(file, cmap["global"]) or _matches(file, cmap["ci-global"]):
        return None
    owners = {
        n for n, node in cmap["nodes"].items()
        if _matches(file, node["paths"]) or _matches(file, node.get("ci-paths", []))
    }
    for prefix, names in cmap.get("retired-paths", {}).items():
        if _matches(file, [prefix]):
            owners.update(names)
    if owners:
        return owners
    for node in cmap["nodes"].values():
        if _matches(file, node.get("release-paths", [])):
            return set()
    if _matches(file, cmap["unowned"]):
        return set()
    return None


def affected(files, cmap):
    names = list(cmap["nodes"])
    if files is None:
        return {n: True for n in names}
    result = {n: False for n in names}
    for f in files:
        owners = owner_nodes(f, cmap)
        if owners is None:
            return {n: True for n in names}
        for n in owners:
            result[n] = True
    return result


def affected_for_node(node, files, cmap):
    """Whether one node is selected by files; None (unknown diff) selects it."""
    return affected(files, cmap)[node]


def _sorted_mapping(section):
    return {key: sorted(names) for key, names in sorted(section.items())}


def selection_spec(cmap, node):
    """Normalised selection inputs for node: equal specs keep evidence usable."""
    entry = cmap["nodes"][node]
    return {
        "paths": sorted(entry["paths"]),
        "ci-paths": sorted(entry.get("ci-paths", [])),
        "jobs": sorted(entry.get("jobs", [])),
        "global": sorted(cmap["global"]),
        "ci-global": sorted(cmap["ci-global"]),
        "global-overrides": _sorted_mapping(cmap.get("global-overrides", {})),
        "retired-paths": _sorted_mapping(cmap.get("retired-paths", {})),
        "unowned": sorted(cmap["unowned"]),
    }


def unaccounted(files, cmap):
    return [
        f for f in files
        if owner_nodes(f, cmap) is None
        and not _matches(f, cmap["global"]) and not _matches(f, cmap["ci-global"])
    ]


def release_paths(node, cmap):
    """Node paths plus release-paths plus global; never ci-paths, ci-global, retired or legacy paths."""
    entry = cmap["nodes"][node]
    out = []
    for p in entry["paths"] + entry.get("release-paths", []) + cmap["global"]:
        if p not in out:
            out.append(p)
    return out


def _git(args, cwd=None):
    return subprocess.run(
        ["git"] + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True
    )


def changed_files(base, head="HEAD", cwd=None):
    """Changed paths base..head without rename detection; None when the base is unusable."""
    if not base or set(base) == {"0"}:
        return None
    if _git(["cat-file", "-e", base + "^{commit}"], cwd).returncode != 0:
        return None
    res = _git(["diff", "--name-only", "--no-renames", "-z", base, head], cwd)
    if res.returncode != 0:
        return None
    return [f for f in res.stdout.split("\0") if f]


def _write_outputs(result, path):
    text = "".join("%s=%s\n" % (n, "true" if v else "false") for n, v in result.items())
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)


def _write_summary(result, reasons, path):
    """Markdown table: one row per node, from the same decision as the outputs."""
    if not path:
        return
    lines = ["| node | selection | base | reason |", "| --- | --- | --- | --- |"]
    for node, selected in result.items():
        base, reason = reasons[node]
        lines.append(
            "| %s | %s | %s | %s |" % (node, "run" if selected else "skip", base, reason)
        )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _load_json_object(path, what):
    with open(str(path), encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("%s must be a JSON object" % what)
    return data


def _select_nodes(cmap, files, node_head, bases, notes, cwd):
    """Result and (base, reason) per node from the full diff and optional per-node evidence."""
    result = {}
    reasons = {}
    for node in cmap["nodes"]:
        note = notes.get(node)
        if note is not None:
            result[node] = affected_for_node(node, files, cmap)
            reasons[node] = ("none", note)
            continue
        if bases is None or node not in bases:
            result[node] = affected_for_node(node, files, cmap)
            reasons[node] = ("none", "never passed")
            continue
        base = bases[node]
        node_files = changed_files(base, node_head, cwd)
        if node_files is None:
            result[node] = affected_for_node(node, files, cmap)
            reasons[node] = ("none", "fallback")
            continue
        selected = affected_for_node(node, node_files, cmap)
        result[node] = selected
        reasons[node] = (base, ("changed since %s" if selected else "unchanged since %s") % base)
    return result, reasons


def main(argv=None, cwd=None):
    parser = argparse.ArgumentParser(prog="components.py")
    parser.add_argument("--map", default=MAP_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    ch = sub.add_parser("changed")
    ch.add_argument("--base", default=None)
    ch.add_argument("--head", default="HEAD")
    ch.add_argument("--node-head", default=None)
    ch.add_argument("--node-bases", default=None)
    ch.add_argument("--evidence-notes", default=None)
    ch.add_argument("--summary-out", default=None)
    ch.add_argument("--all", dest="all_nodes", action="store_true")
    ch.add_argument("--github-output", default=None)
    args = parser.parse_args(argv)
    cwd = cwd or REPO
    try:
        cmap = load_map(args.map)
        bases = _load_json_object(args.node_bases, "--node-bases") if getattr(args, "node_bases", None) else None
        notes = (
            _load_json_object(args.evidence_notes, "--evidence-notes")
            if getattr(args, "evidence_notes", None) else {}
        )
    except (ValueError, OSError) as error:
        print("components: invalid input: %s" % error, file=sys.stderr)
        return 1
    if args.command == "check":
        listing = _git(["ls-files", "-z"], cwd)
        if listing.returncode != 0:
            print("components: git ls-files failed: %s" % listing.stderr.strip(), file=sys.stderr)
            return 1
        tracked = [f for f in listing.stdout.split("\0") if f]
        bad = unaccounted(tracked, cmap)
        for f in bad:
            print("components: not owned, global or unowned: %s" % f, file=sys.stderr)
        return 1 if bad else 0
    files = changed_files(args.base, args.head, cwd)
    if files is None:
        result = {n: True for n in cmap["nodes"]}
        _write_outputs(result, args.github_output)
        _write_summary(result, {n: ("none", "fallback") for n in result}, args.summary_out)
        return 0
    bad = unaccounted(files, cmap)
    if bad or args.all_nodes:
        result = {n: True for n in cmap["nodes"]}
        _write_outputs(result, args.github_output)
        _write_summary(result, {n: ("none", "fallback") for n in result}, args.summary_out)
        if bad:
            for f in bad:
                print("components: not owned, global or unowned: %s" % f, file=sys.stderr)
            return 1
        return 0
    node_head = args.node_head or args.head
    result, reasons = _select_nodes(cmap, files, node_head, bases, notes, cwd)
    _write_outputs(result, args.github_output)
    _write_summary(result, reasons, args.summary_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
