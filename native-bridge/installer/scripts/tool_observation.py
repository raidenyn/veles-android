#!/usr/bin/env python3
"""Observe the native build tools on THIS machine and hold them to ``config/tools.lock.json``.

Python 3.12+ standard library only.

    tool_observation.py observe --target windows-x86_64|macos-arm64 --output observed.json
    tool_observation.py check   --target T --observed observed.json [--lock tools.lock.json]

``observe`` runs the real tools (``rustc``, ``rustup``, ``xcodebuild`` on macOS; ``cl``, ``link`` and the
Visual Studio / Windows SDK environment on Windows) and writes an inventory in the shape
``release_config.validate_tools`` consumes. ``check`` runs ``validate_tools``; there is no warning mode.
A missing, mismatched or unresolved identity - in particular the Windows compiler/SDK identities the lock
still records as ``required_unresolved`` - exits 1 and the production producer, the unsigned-input
verification and the release gate all stop on it. Exit 2 means the environment could not be observed at
all (a tool is absent), which blocks just the same.

WHAT THIS DOES NOT PROVE. The parsers below are written from the documented output of each tool and have
NOT been checked against real tool output on this host (it is Linux; neither Xcode nor MSVC exists here).
The Windows observation collects versions only: the toolset/SDK digests the lock requires
(``msvc_toolset_sha256``, ``windows_sdk_sha256``) have no agreed definition yet, so they are never invented
here and the lock keeps them unresolved until the owner defines and records them from the approved runner.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import release_config

Runner = Callable[[list[str]], "tuple[int, str]"]

# Components rustup always installs; the lock pins only the optional ones.
_BASE_COMPONENTS = frozenset({"cargo", "rustc", "rust-std"})
_RUST_VERSION = re.compile(r"^rustc (\d+\.\d+\.\d+)(?: |$)")
_XCODE_VERSION = re.compile(r"^Xcode (\d+(?:\.\d+)*)$", re.MULTILINE)
_XCODE_BUILD = re.compile(r"^Build version ([0-9A-Za-z]+)$", re.MULTILINE)
_SDK_NAME = re.compile(r"\((macosx\d+(?:\.\d+)*)\)")
_CL_VERSION = re.compile(r"Compiler Version (\d+(?:\.\d+){1,3})")
_LINK_VERSION = re.compile(r"Linker Version (\d+(?:\.\d+){1,3})")


class ObservationError(Exception):
    """A tool could not be run or its output was not recognised (blocks, never defaults)."""


def system_runner(argv: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise ObservationError(f"cannot run {argv[0]}: {error}") from error
    return done.returncode, done.stdout + done.stderr


def _run(run: Runner, argv: list[str]) -> str:
    code, text = run(argv)
    if code != 0:
        raise ObservationError(f"{' '.join(argv)} exited {code}")
    return text


def parse_rust(version: str, profile: str, components: str, targets: str, known_triples: list[str]) -> dict[str, str]:
    match = _RUST_VERSION.match(version.strip())
    if match is None:
        raise ObservationError("rustc --version output is not recognised")
    names: set[str] = set()
    for line in components.split():
        base = line
        for triple in known_triples:
            if line.endswith(f"-{triple}"):
                base = line[: -len(triple) - 1]
                break
        if base not in _BASE_COMPONENTS:
            names.add(base)
    installed = sorted(t for t in targets.split() if t)
    if not installed or not profile.strip():
        raise ObservationError("rustup did not report a profile and installed targets")
    return {
        "channel": match.group(1),
        "profile": profile.strip(),
        "components": ",".join(sorted(names)),
        "targets": ",".join(installed),
    }


def parse_xcode(version_text: str) -> dict[str, str]:
    version, build = _XCODE_VERSION.search(version_text), _XCODE_BUILD.search(version_text)
    if version is None or build is None:
        raise ObservationError("xcodebuild -version output is not recognised")
    return {"version": version.group(1), "build": build.group(1)}


def parse_macos_sdk(sdk_text: str) -> dict[str, str]:
    names = _SDK_NAME.findall(sdk_text)
    if len(names) != 1:
        raise ObservationError("xcodebuild -version -sdk macosx did not name exactly one macosx SDK")
    return {"version": names[0]}


def parse_msvc(cl_banner: str, link_banner: str, env: Mapping[str, str]) -> dict[str, str]:
    cl, link = _CL_VERSION.search(cl_banner), _LINK_VERSION.search(link_banner)
    toolset = env.get("VCToolsVersion", "")
    if cl is None or link is None or not toolset:
        raise ObservationError("cl, link and VCToolsVersion must all be observable (run inside the VS developer environment)")
    return {"cl_version": cl.group(1), "linker_version": link.group(1), "msvc_toolset_version": toolset}


def parse_windows_sdk(env: Mapping[str, str]) -> dict[str, str]:
    sdk, ucrt = env.get("WindowsSDKVersion", "").strip("\\/"), env.get("UCRTVersion", "").strip("\\/")
    if not sdk or not ucrt:
        raise ObservationError("WindowsSDKVersion and UCRTVersion must be set (run inside the VS developer environment)")
    return {"windows_sdk_version": sdk, "ucrt_version": ucrt}


def observe(target: str, run: Runner | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The tool inventory this machine presents for ``target``; raises ``ObservationError`` if unobservable."""
    run = system_runner if run is None else run
    env = os.environ if env is None else env
    lock = release_config.load_lock()
    if target not in lock["targets"]:
        raise ObservationError(f"unknown target {target!r}")
    triples = [t["build_target"] for t in lock["targets"].values()]
    tools: dict[str, dict[str, str]] = {
        "rust": parse_rust(
            _run(run, ["rustc", "--version"]),
            _run(run, ["rustup", "show", "profile"]),
            _run(run, ["rustup", "component", "list", "--installed"]),
            _run(run, ["rustup", "target", "list", "--installed"]),
            triples,
        )
    }
    if target == "macos-arm64":
        version_text = _run(run, ["xcodebuild", "-version"])
        tools["xcode"] = parse_xcode(version_text)
        tools["macos_sdk"] = parse_macos_sdk(_run(run, ["xcodebuild", "-version", "-sdk", "macosx"]))
    else:
        # cl prints its banner to stderr and exits non-zero without input; only the banner matters.
        cl_banner = run(["cl"])[1]
        link_banner = run(["link"])[1]
        tools["msvc"] = parse_msvc(cl_banner, link_banner, env)
        tools["windows_sdk"] = parse_windows_sdk(env)
    return {"target": target, "tools": tools}


def check(observed: Any, target: str, lock: dict | None = None) -> list[str]:
    """Why this inventory may not be used to build or release ``target`` (empty: it matches the lock)."""
    lock = release_config.load_lock() if lock is None else lock
    errors = release_config.validate_tools(lock, observed)
    if isinstance(observed, dict) and observed.get("target") != target:
        errors.append(f"observed inventory is for {observed.get('target')!r}, not {target!r}")
    return errors


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ObservationError(f"cannot read {path}: {error}") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    obs = sub.add_parser("observe")
    obs.add_argument("--target", choices=release_config.TARGET_NAMES, required=True)
    obs.add_argument("--output", type=Path, required=True)
    chk = sub.add_parser("check")
    chk.add_argument("--target", choices=release_config.TARGET_NAMES, required=True)
    chk.add_argument("--observed", type=Path, required=True)
    chk.add_argument("--lock", type=Path, default=release_config.LOCK_PATH)
    args = parser.parse_args(argv)
    try:
        if args.command == "observe":
            inventory = observe(args.target)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            return 0
        errors = check(_load(args.observed), args.target, release_config.load_lock(args.lock))
    except (ObservationError, release_config.ToolLockError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2
    for error in errors:
        print(f"BLOCKED: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
