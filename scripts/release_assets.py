#!/usr/bin/env python3
"""Combined-release assets: carry verification, staging and draft verification.

Stdlib only; Python 3.8 compatible. Asset names (exactly, see design D4):
  veles-android-<v>.apk, veles-android-<v>-mapping.txt, veles-web-extension-<v>.zip,
  veles-installer-windows-<v>.exe, veles-installer-macos-<v>.dmg, release-manifest.json, SHA256SUMS.
release-manifest.json:
  {"schema":1,"release":...,"commit":...,"components":{name:{"version":...,"assets":{name:sha256}}}}
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

MANIFEST = "release-manifest.json"
SUMS = "SHA256SUMS"
# Per component: (asset name template, glob that selects the one matching build output).
LAYOUT = {
    "android": [("veles-android-{v}.apk", "*.apk"), ("veles-android-{v}-mapping.txt", "*mapping*.txt")],
    "web-extension": [("veles-web-extension-{v}.zip", "*.zip")],
    "installer": [("veles-installer-windows-{v}.exe", "*.exe"), ("veles-installer-macos-{v}.dmg", "*.dmg")],
}


class AssetError(Exception):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_assets(component: str, version: str) -> List[str]:
    if component not in LAYOUT:
        raise AssetError("unknown release component %r" % component)
    return [template.format(v=version) for template, _ in LAYOUT[component]]


def _files(directory: Path) -> List[str]:
    if not Path(directory).is_dir():
        raise AssetError("%s is not a directory" % directory)
    return sorted(p.name for p in Path(directory).iterdir())


def _read_sums(path: Path) -> Dict[str, str]:
    out = {}
    with open(str(path), encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\r\n")
            if not line:
                continue
            digest, _, name = line.partition("  ")
            out[name.lstrip("*")] = digest.lower()
    return out


def _listed(manifest: dict, component: str):
    entry = (manifest or {}).get("components", {}).get(component)
    if not isinstance(entry, dict) or "version" not in entry or not isinstance(entry.get("assets"), dict):
        raise AssetError("%s: not listed in the previous release-manifest.json" % component)
    version = str(entry["version"])
    assets = entry["assets"]
    expected = expected_assets(component, version)
    if sorted(assets) != sorted(expected):
        raise AssetError(
            "%s: manifest assets %s are not the expected %s" % (component, sorted(assets), sorted(expected))
        )
    return version, assets


def _check_digests(component: str, assets: Dict[str, str], directory: Path):
    for name, digest in sorted(assets.items()):
        path = Path(directory) / name
        if not path.is_file():
            raise AssetError("%s: carried asset %s is missing" % (component, name))
        actual = sha256_file(path)
        if actual != str(digest).lower():
            raise AssetError("%s: carried asset %s digest %s != manifest %s" % (component, name, actual, digest))


def verify_carry(manifest: dict, component: str, directory: Path, sums: Optional[Path] = None) -> None:
    """`directory` must hold exactly the component's assets listed in the previous manifest, each
    with the listed digest (and, when given, the same digest in the previous SHA256SUMS)."""
    _, assets = _listed(manifest, component)
    present = _files(directory)
    missing = sorted(set(assets) - set(present))
    if missing:
        raise AssetError("%s: carried assets missing: %s" % (component, ", ".join(missing)))
    extra = sorted(set(present) - set(assets))
    if extra:
        raise AssetError("%s: unexpected files next to carried assets: %s" % (component, ", ".join(extra)))
    _check_digests(component, assets, directory)
    if sums is not None:
        listed = _read_sums(sums)
        for name, digest in assets.items():
            if listed.get(name) != str(digest).lower():
                raise AssetError("%s: %s differs between release-manifest.json and SHA256SUMS" % (component, name))


def _select(component: str, version: str, directory: Path) -> Dict[str, Path]:
    """Map each expected asset name to the single build output matching its glob."""
    files = [p for p in sorted(Path(directory).iterdir()) if p.is_file()] if Path(directory).is_dir() else None
    if files is None:
        raise AssetError("%s: input %s is not a directory" % (component, directory))
    chosen = {}
    for template, pattern in LAYOUT[component]:
        matches = [p for p in files if fnmatch.fnmatch(p.name, pattern)]
        if len(matches) != 1:
            raise AssetError(
                "%s: expected exactly one %s in %s, found %s" % (component, pattern, directory, [p.name for p in matches])
            )
        chosen[template.format(v=version)] = matches[0]
    return chosen


def stage(
    plan: dict,
    inputs: Dict[str, Path],
    carried: Path,
    out: Path,
    previous_manifest: Optional[dict] = None,
) -> Dict[str, str]:
    """Copy new (RELEASE) and carried (CARRY) assets into `out` under their release names, then write
    release-manifest.json and SHA256SUMS. Returns {asset name: sha256} for every staged file."""
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise AssetError("output directory %s is not empty" % out)
    sources = {}  # type: Dict[str, Path]
    entries = {}
    for component, info in plan["components"].items():
        if component not in LAYOUT:
            raise AssetError("unknown release component %r in plan" % component)
        if info["action"] == "RELEASE":
            version = info["next"]
            if component not in inputs:
                raise AssetError("%s is RELEASE but no build input was given" % component)
            chosen = _select(component, version, Path(inputs[component]))
        elif info["action"] == "CARRY":
            version = info["current"]
            if not version:
                raise AssetError("%s is CARRY without a current version" % component)
            chosen = {name: Path(carried) / name for name in expected_assets(component, version)}
            if previous_manifest is not None:
                listed_version, assets = _listed(previous_manifest, component)
                if listed_version != version:
                    raise AssetError("%s: carried %s but previous manifest lists %s" % (component, version, listed_version))
                _check_digests(component, assets, Path(carried))
            for name, path in chosen.items():
                if not path.is_file():
                    raise AssetError("%s: carried asset %s is missing" % (component, name))
        else:
            raise AssetError("%s: unknown action %r" % (component, info["action"]))
        sources.update(chosen)
        entries[component] = {"version": version, "names": list(chosen)}
    out.mkdir(parents=True, exist_ok=True)
    digests = {}
    for name, source in sources.items():
        shutil.copyfile(str(source), str(out / name))
        digests[name] = sha256_file(out / name)
    manifest = {
        "schema": 1,
        "release": plan["release_tag"],
        "commit": plan.get("commit", ""),
        "components": {
            component: {"version": e["version"], "assets": {n: digests[n] for n in e["names"]}}
            for component, e in entries.items()
        },
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digests[MANIFEST] = sha256_file(out / MANIFEST)
    (out / SUMS).write_text("".join("%s  %s\n" % (digests[n], n) for n in sorted(digests)), encoding="utf-8")
    digests[SUMS] = sha256_file(out / SUMS)
    return digests


def verify_draft(expected: Dict[str, str], directory: Path) -> None:
    present = _files(directory)
    missing = sorted(set(expected) - set(present))
    extra = sorted(set(present) - set(expected))
    if missing or extra:
        raise AssetError("draft assets differ: missing %s, unexpected %s" % (missing, extra))
    for name, digest in sorted(expected.items()):
        actual = sha256_file(Path(directory) / name)
        if actual != digest:
            raise AssetError("draft asset %s digest %s != recorded %s" % (name, actual, digest))


def _read_json(path):
    with open(str(path), encoding="utf-8") as handle:
        return json.load(handle)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="release_assets.py")
    sub = parser.add_subparsers(dest="command")
    sub.required = True
    carry = sub.add_parser("verify-carry", help="verify a carried component's downloaded assets")
    carry.add_argument("--manifest-json", required=True)
    carry.add_argument("--component", required=True)
    carry.add_argument("--dir", required=True)
    carry.add_argument("--sha256sums", help="previous release's SHA256SUMS to cross-check")
    st = sub.add_parser("stage", help="stage all release assets, release-manifest.json and SHA256SUMS")
    st.add_argument("--plan-json", required=True)
    st.add_argument("--input", action="append", default=[], metavar="COMPONENT=DIR")
    st.add_argument("--carried", required=True)
    st.add_argument("--manifest-json", help="previous release-manifest.json (re-verifies carried digests)")
    st.add_argument("--out", required=True)
    st.add_argument("--digests-out", help="write {name: sha256} JSON for verify-draft")
    draft = sub.add_parser("verify-draft", help="verify downloaded draft assets against recorded digests")
    draft.add_argument("--expected-json", required=True)
    draft.add_argument("--dir", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-carry":
            verify_carry(_read_json(args.manifest_json), args.component, Path(args.dir),
                         Path(args.sha256sums) if args.sha256sums else None)
            print("release_assets: %s carried assets verified" % args.component)
        elif args.command == "stage":
            inputs = {}
            for item in args.input:
                component, sep, directory = item.partition("=")
                if not sep or not component or not directory:
                    raise AssetError("--input must be COMPONENT=DIR, got %r" % item)
                inputs[component] = Path(directory)
            previous = _read_json(args.manifest_json) if args.manifest_json else None
            digests = stage(_read_json(args.plan_json), inputs, Path(args.carried), Path(args.out), previous or None)
            text = json.dumps(digests, indent=2, sort_keys=True) + "\n"
            if args.digests_out:
                Path(args.digests_out).write_text(text, encoding="utf-8")
            else:
                sys.stdout.write(text)
        else:
            verify_draft(_read_json(args.expected_json), Path(args.dir))
            print("release_assets: draft assets match the recorded digests")
    except (AssetError, OSError, ValueError, KeyError) as error:
        print("release_assets: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
