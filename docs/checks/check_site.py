#!/usr/bin/env python3
"""Documentation site guard (spec: the `docs-site` capability of the onboard-mintlify-docs change).

Computes the set of files the documentation site publishes from the tracked files, the
`.mintignore` allowlist and the site's default ignores, and checks that set. Stdlib only;
Python 3.8 compatible.
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys

Finding = collections.namedtuple("Finding", "severity path message")

# Names the site never publishes. As in gitignore, a slash-less entry matches any path
# segment: folders at any depth and file names in any folder.
DEFAULT_IGNORES = (
    ".git",
    ".github",
    ".claude",
    ".agents",
    ".idea",
    ".vscode",
    ".cursor",
    ".mintlify",
    "node_modules",
    "README.md",
    "LICENSE.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
)


def _git(root, *args):
    """Run a git query in ``root`` and return its stdout decoded as text."""
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


def reincluded_paths(mintignore_text):
    """Exact paths of the ``!/path`` lines, without the leading ``!/`` and trailing ``/``."""
    paths = set()
    for line in mintignore_text.split("\n"):
        line = line.rstrip("\r").rstrip()
        if line.startswith("!/") and len(line) > 2:
            paths.add(line[2:].rstrip("/"))
    return paths


def _default_ignored(path):
    return any(segment in DEFAULT_IGNORES for segment in path.split("/"))


def published_files(root):
    """Sorted tracked paths the site publishes."""
    tracked = _split_z(_git(root, "ls-files", "-z"))
    mintignore = os.path.join(root, ".mintignore")
    if not os.path.isfile(mintignore):
        return []
    ignored = set(
        _split_z(_git(root, "ls-files", "-z", "-c", "-i", "--exclude-from=.mintignore"))
    )
    with open(mintignore, encoding="utf-8", errors="surrogateescape") as handle:
        reincluded = reincluded_paths(handle.read())
    return sorted(
        path
        for path in tracked
        if path not in ignored and (path in reincluded or not _default_ignored(path))
    )


MINTIGNORE_FORMS = (
    re.compile(r"^/\*$"),
    re.compile(r"^!/[^*!\s]+/?$"),
    re.compile(r"^/[^*!\s]+/\*$"),
)

IMPORT_RE = re.compile(r'^import\s+\w+\s+from\s+["\']([^"\']+)["\'];?\s*$', re.M)

NAV_KEYS = ("pages", "groups", "products", "tabs", "anchors", "dropdowns")
WRAPPER_RE = re.compile(r"^docs/dev/specs/([^/]+)\.mdx$")


def check_mintignore(root):
    """Errors for a missing `.mintignore` or a line outside the three allowed forms."""
    path = os.path.join(root, ".mintignore")
    if not os.path.isfile(path):
        return [Finding("error", ".mintignore", "is missing")]
    with open(path, encoding="utf-8", errors="surrogateescape") as handle:
        text = handle.read()
    findings = []
    first = True
    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip("\r").rstrip()
        if not line or line.startswith("#"):
            continue
        forms = MINTIGNORE_FORMS[:1] if first else MINTIGNORE_FORMS[1:]
        first = False
        if not any(form.match(line) for form in forms):
            findings.append(
                Finding(
                    "error",
                    ".mintignore:%d" % number,
                    'line "%s" is not an allowed form' % line,
                )
            )
    return findings


def nav_pages(navigation):
    """Every string item of a ``pages`` list at any depth, in order."""
    pages = []

    def walk(node):
        if isinstance(node, dict):
            for key in NAV_KEYS:
                value = node.get(key)
                if isinstance(value, list):
                    for item in value:
                        if key == "pages" and isinstance(item, str):
                            pages.append(item)
                        else:
                            walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(navigation)
    return pages


def imports(text):
    return IMPORT_RE.findall(text)


def _read(root, path):
    with open(os.path.join(root, *path.split("/")), encoding="utf-8", errors="replace") as h:
        return h.read()


def _asset_refs(data):
    """(label, value) pairs for the logo and favicon settings."""
    refs = []
    for key in ("logo", "favicon"):
        value = data.get(key)
        if isinstance(value, str):
            refs.append((key, value))
        elif isinstance(value, dict):
            for mode in ("light", "dark"):
                if isinstance(value.get(mode), str):
                    refs.append(("%s.%s" % (key, mode), value[mode]))
    return refs


def check_structure(root, published):
    """Structural errors of the site: navigation, imports, wrappers and assets."""
    findings = []
    docs_json = os.path.join(root, "docs.json")
    if not os.path.isfile(docs_json):
        return [Finding("error", "docs.json", "is missing")]
    try:
        data = json.loads(_read(root, "docs.json"))
    except ValueError as error:
        return [Finding("error", "docs.json", "is not valid JSON: %s" % error)]
    if not isinstance(data, dict):
        return [Finding("error", "docs.json", "is not a JSON object")]

    published_set = set(published)
    pages = nav_pages(data.get("navigation", {}))
    seen = set()
    for page in pages:
        if page in seen:
            findings.append(
                Finding("error", "docs.json", 'navigation lists page "%s" twice' % page)
            )
        seen.add(page)
        if page + ".md" not in published_set and page + ".mdx" not in published_set:
            findings.append(
                Finding(
                    "error",
                    "docs.json",
                    'navigation page "%s" has no published .md or .mdx file' % page,
                )
            )

    tracked = set(_split_z(_git(root, "ls-files", "-z")))
    nav_set = set(pages)
    for path in published:
        if not path.endswith(".mdx"):
            continue
        found = imports(_read(root, path))
        for target in found:
            if not target.startswith("/"):
                findings.append(
                    Finding("error", path, 'import "%s": imports must be root-relative' % target)
                )
            elif target[1:] not in tracked:
                findings.append(
                    Finding("error", path, 'import "%s" resolves to no tracked file' % target)
                )
        match = WRAPPER_RE.match(path)
        if match:
            expected = "/openspec/specs/%s/spec.md" % match.group(1)
            if found != [expected]:
                findings.append(
                    Finding("error", path, "must import only %s" % expected)
                )
            if path[:-4] not in nav_set:
                findings.append(
                    Finding("error", path, "wrapper is not listed in the navigation")
                )

    for label, value in _asset_refs(data):
        if value.lstrip("/") not in published_set:
            findings.append(
                Finding("error", "docs.json", '%s "%s" is not a published file' % (label, value))
            )
    return findings


def check_repo(root):
    """All findings for the repository at ``root``."""
    findings = check_mintignore(root)
    if findings and findings[0].message == "is missing":
        return findings
    published = published_files(root)
    return findings + check_structure(root, published)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check the documentation site.")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    args = parser.parse_args(argv)
    try:
        findings = check_repo(args.root)
    except (OSError, RuntimeError) as error:
        print("error %s: %s" % (args.root, " ".join(str(error).split())))
        return 1
    for finding in findings:
        print("%s %s: %s" % (finding.severity, finding.path, finding.message))
    return 1 if any(finding.severity == "error" for finding in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
