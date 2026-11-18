#!/usr/bin/env python3
"""Documentation site guard (spec: the `docs-site` capability of the onboard-mintlify-docs change).

Computes the set of files the documentation site publishes from the tracked files, the
`.mintignore` allowlist and the site's default ignores, and checks that set. Stdlib only;
Python 3.8 compatible.
"""
import argparse
import collections
import os
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


def check_repo(root):
    """All findings for the repository at ``root``."""
    findings = []
    published_files(root)
    return findings


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
