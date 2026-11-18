#!/usr/bin/env python3
"""Documentation site guard (spec: the `documentation-site` capability, onboard-mintlify-docs).

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
    # The default ignores always win: a `.mintignore` re-include cannot publish such a path.
    return sorted(
        path for path in tracked if path not in ignored and not _default_ignored(path)
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
    if first:
        findings.append(
            Finding("error", ".mintignore", 'has no patterns; the first pattern must be "/*"')
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
            elif target[1:] not in published_set:
                findings.append(
                    Finding(
                        "error",
                        path,
                        'import "%s" points at a missing or unpublished file' % target,
                    )
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


FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
BACKTICK_RUN_RE = re.compile(r"`+")


def _blank(text):
    """``text`` with every character except line breaks replaced by a space."""
    return re.sub(r"[^\n]", " ", text)


def _mask_fences(lines):
    out = []
    fence = None
    for line in lines:
        body = line.rstrip("\n")
        if fence is None:
            match = FENCE_OPEN_RE.match(body)
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                fence = (match.group(1)[0], len(match.group(1)))
                out.append(_blank(line))
            else:
                out.append(line)
        else:
            closer = re.match(r"^ {0,3}(%s{%d,})\s*$" % (re.escape(fence[0]), fence[1]), body)
            if closer:
                fence = None
            out.append(_blank(line))
    return out


def _mask_spans(paragraph):
    runs = list(BACKTICK_RUN_RE.finditer(paragraph))
    chars = list(paragraph)
    index = 0
    while index < len(runs):
        size = len(runs[index].group())
        for other in range(index + 1, len(runs)):
            if len(runs[other].group()) == size:
                for pos in range(runs[index].start(), runs[other].end()):
                    if chars[pos] != "\n":
                        chars[pos] = " "
                index = other
                break
        index += 1
    return "".join(chars)


def mask_code(text):
    """``text`` with fenced blocks and per-paragraph code spans replaced by spaces."""
    lines = _mask_fences(text.splitlines(True))
    result = []
    paragraph = []
    for line in lines + [None]:
        if line is not None and line.strip():
            paragraph.append(line)
            continue
        if paragraph:
            result.append(_mask_spans("".join(paragraph)))
            paragraph = []
        if line is not None:
            result.append(line)
    return "".join(result)


def scan_hazards(path, text, mdx):
    """Findings for input the site's MDX build rejects, outside code."""
    findings = []
    for number, line in enumerate(mask_code(text).split("\n"), 1):
        where = "%s:%d" % (path, number)
        for _ in range(line.count("<!--")):
            findings.append(Finding("error", where, "HTML comment breaks the MDX build"))
        if mdx:
            continue
        for match in re.finditer(r"<(?!!--)(\S)", line):
            findings.append(
                Finding(
                    "error",
                    where,
                    '"<" followed by "%s" breaks the MDX build' % match.group(1),
                )
            )
        if "{" in line or "}" in line:
            findings.append(
                Finding("warning", where, "brace may be read as an MDX expression")
            )
    return findings


def check_hazards(root, published):
    """Hazard findings for every published page and every file a published .mdx imports."""
    published_set = set(published)
    jobs = []
    seen = set()

    def add(path, mdx):
        if path not in seen:
            seen.add(path)
            jobs.append((path, mdx))

    for path in published:
        if path.endswith(".md"):
            add(path, False)
        elif path.endswith(".mdx"):
            add(path, True)
    for path in published:
        if path.endswith(".mdx"):
            for target in imports(_read(root, path)):
                name = target[1:] if target.startswith("/") else target
                if name in published_set and os.path.isfile(os.path.join(root, *name.split("/"))):
                    add(name, False)
    findings = []
    for path, mdx in jobs:
        findings.extend(scan_hazards(path, _read(root, path), mdx))
    return findings


SPEC_RE = re.compile(r"^openspec/specs/([^/]+)/spec\.md$")


def check_wrapper_coverage(root, published):
    """Warnings for published capability specs that have no tracked wrapper page."""
    tracked = set(_split_z(_git(root, "ls-files", "-z")))
    findings = []
    for path in published:
        match = SPEC_RE.match(path)
        if match and "docs/dev/specs/%s.mdx" % match.group(1) not in tracked:
            findings.append(
                Finding(
                    "warning",
                    "docs/dev/specs/%s.mdx" % match.group(1),
                    'capability "%s" has no wrapper page' % match.group(1),
                )
            )
    return findings


def check_repo(root):
    """All findings for the repository at ``root``."""
    findings = check_mintignore(root)
    if findings and findings[0].message == "is missing":
        return findings
    published = published_files(root)
    return (
        findings
        + check_structure(root, published)
        + check_hazards(root, published)
        + check_wrapper_coverage(root, published)
    )


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
