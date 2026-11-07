#!/usr/bin/env python3
"""Assemble the UNSIGNED installer input tree and the A/B publication (OTP-25 Task 14).

Python 3.12+ standard library only. No network, no compiler, no signer. This module consumes
already-built executables and an approved release config; it never builds, signs, notarizes or
compares anything against a platform tool.

Commands (exit 0 success, 1 a refusal the build must treat as a blocker, 2 invalid invocation)::

    check-config --config F --channel production|fixture --platform windows|macos
    origin       --config F
    assemble     --platform P --channel C --config F --commit SHA --binaries DIR --output NEW
                 [--frontend EXE --packager package-macos.sh]
    verify-carrier --carrier F --descriptor F --channel C [--image F]
    finalize     --platform P --slot-a DIR --slot-b DIR --output NEW --commit SHA

Fail-closed rules this module owns:

* The channel is chosen by the CALLER (the Gradle task), never by the config. A config whose
  ``channel`` differs from the requested one is refused, so a fixture config can never produce a
  production artifact and a production config can never be packaged as the fixture. The
  development channel is never packageable here.
* The config carries release identity and policy only. ``source_commit`` and ``payload_id`` are
  computed (git commit given by the caller; SHA-256 of the exact ``inventory.json`` bytes) and are
  refused when they appear in the input.
* ``finalize`` requires two distinct, independently produced slot directories built for the same
  commit and compares them with Task 9's inventory comparison. A mismatch publishes nothing but an
  explicit FAIL report and is reported as a release blocker (never retried or papered over).

``finalize`` writes ``tree.tar`` (a mode-preserving, timestamp- and owner-free tar of the slot-A
tree), ``provenance.json`` (the exact shape ``release_gate.verify_inputs`` reads), ``report-ab.json``
and ``SHA256SUMS``.

The Windows engine's policy carrier is a resource that must be embedded into ``carrier.exe`` on a
Windows runner (``embed-carrier.ps1``). Two documents are published for the engine build, never
built on the fly by it:

* ``carrier.bin`` carries the zeroed 32-byte sealed-digest placeholder Task 13 fills in place;
* ``embedded-carrier.bin`` is the same descriptor in an unsealed document, which is the exact
  document ``VELES_EMBEDDED_CARRIER`` compiles into the engine. It deliberately is not the
  placeholder, so a resource locator that matches the placeholder cannot confuse the compiled-in
  copy with the resource Task 13 seals.

The macOS tree packages the unsealed document as ``carrier.app/Contents/Resources/veles-policy-carrier``;
``embedded-carrier.bin`` at the output root is byte-identical to it. ``verify-carrier`` checks either
document against the exact descriptor, the channel and, when given, a built image. The resource
embedding itself is NOT RUN on this host.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_inputs  # noqa: E402

SCHEMA = 1
PLATFORM_TARGET = {"windows": "windows-x86_64", "macos": "macos-arm64"}
CHANNEL_IDENTITY = {
    "production": "me.nagaev.veles.bridge",
    "fixture": "me.nagaev.veles.bridge.fixture",
}
PACKAGEABLE_CHANNELS = ("production", "fixture")
MINIMUM_OS = {"windows": (11,), "macos": (26, 0)}
PLACEHOLDER_EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"
CONFIG_KEYS = {
    "schema", "channel", "version", "target", "minimum_os", "extension_id", "host_name",
    "bundle_id", "api_min", "api_max", "policy",
}
POLICY_KEYS = {"publishers", "denied_payloads", "rollback_payloads", "epoch"}
# Computed by the build; an input that carries them is rejected rather than trusted.
COMPUTED_KEYS = ("source_commit", "payload_id")
MAX_PUBLISHERS = 64
MAX_PUBLISHER_BYTES = 512
MAX_POLICY_DIGESTS = 4096

COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
OS_RE = re.compile(r"^(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*)){0,2}$")
EXTENSION_RE = re.compile(r"^[a-p]{32}$")

CARRIER_MAGIC = b"VELESPC1"
CARRIER_VERSION = 1
CARRIER_HEADER_LEN = 32
CARRIER_FLAG_SEALED = 1
SEALED_DIGEST_LEN = 32

APP = "Veles Bridge Installer.app"
MAC_PACKAGE = f"{APP}/Contents/Resources/package"
# The closed set of built executables each platform's tree is made from. Anything else in the
# binaries directory (a test driver, a stray tool) is refused.
BINARIES = {
    # uninstall.exe is the NSIS-generated, still UNSIGNED uninstaller (generate-uninstaller.ps1). It is
    # part of the inventoried payload as maintenance/uninstall.exe (Task 13's payload view), so it must
    # exist before the inventory and therefore the payload id are computed.
    "windows": ("veles-bridge-maintenance.exe", "veles-bridge-cleanup.exe", "veles-native-host.exe", "uninstall.exe"),
    "macos": ("veles-bridge-maintenance", "veles-native-host"),
}


class AssemblyError(Exception):
    """A refusal the build must treat as a blocker; nothing is published."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _unique(pairs: list[tuple[str, Any]]) -> dict:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_config(path: Path) -> dict:
    """Strict JSON object (duplicate keys rejected)."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique)
    except OSError as error:
        raise ValueError(f"cannot read {path}: {error.strerror}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def _valid_publisher(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value != ""
        and len(value.encode("utf-8")) <= MAX_PUBLISHER_BYTES
        and value.strip() == value
        and not any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in value)
    )


def _digest_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= MAX_POLICY_DIGESTS
        and all(isinstance(item, str) and HEX64.match(item) for item in value)
        and len(set(value)) == len(value)
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def check_config(config: dict, *, channel: str, platform: str) -> list[str]:
    """Why this config may not be packaged as ``channel`` for ``platform`` (empty: it may)."""
    if channel not in PACKAGEABLE_CHANNELS:
        raise ValueError(f"channel {channel!r} is not packageable (production or fixture only)")
    if platform not in PLATFORM_TARGET:
        raise ValueError(f"unknown platform {platform!r}")
    problems: list[str] = []
    keys = set(config)
    for name in COMPUTED_KEYS:
        if name in keys:
            problems.append(f"{name} is computed by the build and must not appear in the config")
    for name in sorted(keys - CONFIG_KEYS - set(COMPUTED_KEYS)):
        problems.append(f"unknown field {name!r}")
    for name in sorted(CONFIG_KEYS - keys):
        problems.append(f"missing field {name!r}")
    if problems:
        return problems
    if config["schema"] != SCHEMA or not _is_int(config["schema"]):
        problems.append("schema must be 1")
    if config["channel"] != channel:
        problems.append(f"config channel {config['channel']!r} is not the requested channel {channel!r}")
    identity = CHANNEL_IDENTITY[channel]
    for name in ("host_name", "bundle_id"):
        if config[name] != identity:
            problems.append(f"{name} must be {identity!r} for the {channel} channel")
    if config["target"] != PLATFORM_TARGET[platform]:
        problems.append(f"target {config['target']!r} is not {PLATFORM_TARGET[platform]!r}")
    if not (isinstance(config["version"], str) and VERSION_RE.match(config["version"])):
        problems.append("version must be three unsigned numeric components without leading zeros")
    minimum = config["minimum_os"]
    if not (isinstance(minimum, str) and OS_RE.match(minimum)):
        problems.append("minimum_os is malformed")
    else:
        parts = tuple(int(part) for part in minimum.split("."))
        floor = MINIMUM_OS[platform]
        if parts + (0,) * (len(floor) - len(parts)) < floor:
            problems.append(f"minimum_os {minimum} is below the supported floor {'.'.join(map(str, floor))}")
    extension = config["extension_id"]
    if not (isinstance(extension, str) and EXTENSION_RE.match(extension)) or extension == PLACEHOLDER_EXTENSION_ID:
        problems.append("extension_id must be one real 32-character a-p Chrome extension ID (not the placeholder)")
    api_min, api_max = config["api_min"], config["api_max"]
    if not (_is_int(api_min) and _is_int(api_max) and 1 <= api_min <= api_max <= 1000):
        problems.append("api_min/api_max must be integers with 1 <= api_min <= api_max <= 1000")
    policy = config["policy"]
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        problems.append("policy must have exactly publishers, denied_payloads, rollback_payloads and epoch")
    else:
        publishers = policy["publishers"]
        if not (
            isinstance(publishers, list)
            and 1 <= len(publishers) <= MAX_PUBLISHERS
            and all(_valid_publisher(item) for item in publishers)
            and len(set(publishers)) == len(publishers)
        ):
            problems.append("policy.publishers must be 1-64 unique trimmed publisher identities without control characters")
        for name in ("denied_payloads", "rollback_payloads"):
            if not _digest_list(policy[name]):
                problems.append(f"policy.{name} must be unique lowercase SHA-256 digests")
        if not (_is_int(policy["epoch"]) and policy["epoch"] >= 0):
            problems.append("policy.epoch must be a non-negative integer")
    return problems


def tracked_problems(path: Path) -> list[str]:
    """Why ``path`` is not a tracked, unmodified build input of the current commit (empty: it is)."""
    path = Path(path)
    directory = path.resolve().parent
    try:
        listed = subprocess.run(["git", "ls-files", "--error-unmatch", "--", path.name], cwd=directory, capture_output=True, text=True, check=False)
        changed = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", path.name], cwd=directory, capture_output=True, text=True, check=False)
    except OSError as error:
        return [f"cannot ask git about {path}: {error.strerror}"]
    problems = []
    if listed.returncode != 0:
        problems.append(f"{path} is not a git-tracked file; the release config is a tracked build input")
    elif changed.returncode != 0:
        problems.append(f"{path} differs from HEAD; commit the config before building from it")
    return problems


def origin(config: dict) -> str:
    """The single literal origin of a config's extension (the host's compiled-in allowlist)."""
    extension = config.get("extension_id")
    if not (isinstance(extension, str) and EXTENSION_RE.match(extension)) or extension == PLACEHOLDER_EXTENSION_ID:
        raise ValueError("the config has no real extension_id")
    return f"chrome-extension://{extension}/"


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inventory_document(entries: list[dict]) -> bytes:
    """The engine's ``write_inventory`` bytes: schema 1 and path-byte-ordered entries."""
    ordered = sorted(
        ({"executable": bool(e["executable"]), "path": e["path"], "sha256": e["sha256"], "size": int(e["size"])} for e in entries),
        key=lambda e: e["path"].encode("utf-8"),
    )
    return canonical_json({"entries": ordered, "schema": SCHEMA})


def descriptor_document(config: dict, commit: str, payload_id: str) -> bytes:
    if not COMMIT_RE.match(commit or ""):
        raise ValueError("commit must be a full lowercase git commit id")
    if not HEX64.match(payload_id or ""):
        raise ValueError("payload_id must be a lowercase SHA-256")
    descriptor = dict(config)
    descriptor["source_commit"] = commit
    descriptor["payload_id"] = payload_id
    return canonical_json(descriptor)


def carrier_document(descriptor: bytes, *, sealed_placeholder: bool) -> bytes:
    """The ``policy.rs`` carrier: header, descriptor and (optionally) a zeroed 32-byte digest slot."""
    if not descriptor:
        raise ValueError("empty descriptor")
    flags = CARRIER_FLAG_SEALED if sealed_placeholder else 0
    header = (
        CARRIER_MAGIC
        + struct.pack("<HHIQQ", CARRIER_VERSION, flags, CARRIER_HEADER_LEN, CARRIER_HEADER_LEN, len(descriptor))
    )
    return header + descriptor + (b"\x00" * SEALED_DIGEST_LEN if sealed_placeholder else b"")


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _regular_file(path: Path, what: str) -> Path:
    try:
        info = path.lstat()
    except OSError as error:
        raise AssemblyError(f"{what} is missing: {path}") from error
    if not stat.S_ISREG(info.st_mode):
        raise AssemblyError(f"{what} is not a regular file (symlinks are refused): {path}")
    if info.st_size == 0:
        raise AssemblyError(f"{what} is empty: {path}")
    return path


def _closed_binaries(directory: Path, platform: str) -> dict[str, Path]:
    try:
        present = sorted(p.name for p in directory.iterdir())
    except OSError as error:
        raise AssemblyError(f"cannot list binaries directory {directory}") from error
    wanted = sorted(BINARIES[platform])
    if present != wanted:
        raise AssemblyError(f"binaries directory must hold exactly {wanted}; found {present}")
    return {name: _regular_file(directory / name, name) for name in wanted}


def _write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def _copy_file(source: Path, destination: Path, mode: int) -> None:
    _write(destination, source.read_bytes(), mode)


def _payload_entries(payload: Path) -> list[dict]:
    inventory = package_inputs.build_inventory(payload)
    if inventory["empty_directories"]:
        raise AssemblyError("the payload has empty directories the inventory cannot represent")
    return inventory["entries"]


def _prepare_config(config: Path, channel: str, platform: str, commit: str) -> dict:
    if channel not in PACKAGEABLE_CHANNELS:
        raise AssemblyError(f"channel {channel!r} is not packageable")
    if not COMMIT_RE.match(commit or ""):
        raise AssemblyError("commit must be a full lowercase git commit id")
    try:
        value = load_config(config)
    except ValueError as error:
        raise AssemblyError(str(error)) from error
    problems = check_config(value, channel=channel, platform=platform)
    if problems:
        raise AssemblyError("release config refused: " + "; ".join(problems))
    return value


def assemble(
    *, platform: str, channel: str, config: Path, commit: str, binaries: Path, output: Path,
    frontend: Path | None, packager: Path | None,
) -> None:  # fmt: skip
    """Write ``output/tree`` (and, on Windows, ``output/carrier.bin``); ``output`` must not exist."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise AssemblyError(f"output must not exist: {output}")
    value = _prepare_config(Path(config), channel, platform, commit)
    files = _closed_binaries(Path(binaries), platform)
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        if platform == "windows":
            _assemble_windows(value, commit, files, scratch)
        else:
            _assemble_macos(value, commit, files, scratch, frontend, packager)
        os.rename(scratch, output)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise


def _assemble_windows(config: dict, commit: str, files: dict[str, Path], scratch: Path) -> None:
    package = scratch / "tree" / "package"
    payload = package / "payload"
    _copy_file(files["veles-native-host.exe"], payload / "veles-native-host.exe", 0o755)
    _copy_file(files["veles-bridge-cleanup.exe"], payload / "veles-bridge-cleanup.exe", 0o755)
    uninstaller = scratch / "tree" / "uninstall.exe"
    _copy_file(files["uninstall.exe"], uninstaller, 0o755)
    uninstall_data = uninstaller.read_bytes()
    entries = _payload_entries(payload) + [
        {"path": "maintenance/uninstall.exe", "size": len(uninstall_data), "sha256": sha256_bytes(uninstall_data), "executable": True}
    ]
    inventory = inventory_document(entries)
    descriptor = descriptor_document(config, commit, sha256_bytes(inventory))
    _write(package / "inventory.json", inventory, 0o644)
    _write(package / "descriptor.json", descriptor, 0o644)
    _copy_file(files["veles-bridge-maintenance.exe"], package / "carrier.exe", 0o755)
    # The document a Windows runner embeds as the VELES_POLICY_CARRIER resource of carrier.exe.
    _write(scratch / "carrier.bin", carrier_document(descriptor, sealed_placeholder=True), 0o644)
    # The exact document the engine compiles in (`VELES_EMBEDDED_CARRIER`). Unsealed on purpose:
    # Task 13's embedded carrier locator requires the sealed-placeholder header, so the compiled-in
    # copy can never be mistaken for (or sealed instead of) the resource.
    _write(scratch / "embedded-carrier.bin", carrier_document(descriptor, sealed_placeholder=False), 0o644)


def _assemble_macos(
    config: dict, commit: str, files: dict[str, Path], scratch: Path, frontend: Path | None, packager: Path | None,
) -> None:  # fmt: skip
    if frontend is None or packager is None:
        raise AssemblyError("the macOS assembly needs the built frontend and package-macos.sh")
    frontend = _regular_file(Path(frontend), "frontend executable")
    packager = _regular_file(Path(packager), "package-macos.sh")

    def run(tag: str, descriptor: bytes, inventory: bytes, carrier: bytes) -> Path:
        inputs = scratch / f"inputs-{tag}"
        inputs.mkdir()
        _write(inputs / "VERSION", f"{config['version']}\n".encode(), 0o644)
        _write(inputs / "BUNDLE_ID", f"{config['bundle_id']}\n".encode(), 0o644)
        _copy_file(frontend, inputs / "VelesBridgeInstaller", 0o755)
        _copy_file(files["veles-bridge-maintenance"], inputs / "veles-bridge-maintenance", 0o755)
        _copy_file(files["veles-native-host"], inputs / "veles-native-host", 0o755)
        _write(inputs / "veles-policy-carrier", carrier, 0o644)
        _write(inputs / "descriptor.json", descriptor, 0o644)
        _write(inputs / "inventory.json", inventory, 0o644)
        result = subprocess.run(
            ["bash", str(packager), "--inputs", str(inputs), "--output", str(scratch / f"pass-{tag}")],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        if result.returncode != 0:
            raise AssemblyError(f"package-macos.sh failed ({result.returncode}): {result.stderr.strip()[:400]}")
        return scratch / f"pass-{tag}" / "dmg-root"

    # Pass 1 only discovers the payload the packager lays out (host bundle, plist substitution).
    # The payload does not depend on the descriptor, inventory or carrier, so pass 2 must reproduce it.
    first = run("1", b"{}\n", b"{}\n", CARRIER_MAGIC)
    first_inventory = package_inputs.build_inventory(first / MAC_PACKAGE / "payload")
    entries = _payload_entries(first / MAC_PACKAGE / "payload")
    inventory = inventory_document(entries)
    descriptor = descriptor_document(config, commit, sha256_bytes(inventory))
    second = run("2", descriptor, inventory, carrier_document(descriptor, sealed_placeholder=False))
    second_inventory = package_inputs.build_inventory(second / MAC_PACKAGE / "payload")
    if first_inventory["inventory_sha256"] != second_inventory["inventory_sha256"]:
        raise AssemblyError("the packaged payload changed between the discovery and the final pass")
    shutil.copytree(second, scratch / "tree", symlinks=False)
    for leftover in scratch.iterdir():
        if leftover.name != "tree":
            shutil.rmtree(leftover)
    # Byte-identical to the document the tree packages in carrier.app; the engine build embeds this.
    _write(scratch / "embedded-carrier.bin", carrier_document(descriptor, sealed_placeholder=False), 0o644)


# ---------------------------------------------------------------------------
# Carrier verification
# ---------------------------------------------------------------------------


def verify_carrier(
    *, carrier: Path, descriptor: Path, channel: str, image: Path | None = None
) -> None:  # fmt: skip
    """Refuse unless ``carrier`` is the exact document for this descriptor and channel.

    The engine build consumes an *already verified* artifact: this is the check that runs between
    assembly and the build. ``image`` additionally requires the exact carrier bytes to occur exactly
    once in a built engine image, so the bytes the build embedded are provably the assembled ones.
    Nothing here trusts a length field before bounding it.
    """
    if channel not in PACKAGEABLE_CHANNELS:
        raise AssemblyError(f"channel {channel!r} is not packageable")
    data = _regular_file(Path(carrier), "carrier document").read_bytes()
    expected = _regular_file(Path(descriptor), "descriptor.json").read_bytes()
    if len(data) < CARRIER_HEADER_LEN or data[:8] != CARRIER_MAGIC:
        raise AssemblyError("the carrier document does not start with the carrier magic")
    version = int.from_bytes(data[8:10], "little")
    flags = int.from_bytes(data[10:12], "little")
    header_len = int.from_bytes(data[12:16], "little")
    offset = int.from_bytes(data[16:24], "little")
    length = int.from_bytes(data[24:32], "little")
    if version != CARRIER_VERSION or flags not in (0, CARRIER_FLAG_SEALED) or header_len != CARRIER_HEADER_LEN:
        raise AssemblyError("unsupported carrier header")
    end = offset + length
    if offset < CARRIER_HEADER_LEN or length == 0 or end > len(data):
        raise AssemblyError("the carrier descriptor section is out of bounds")
    if data[offset:end] != expected:
        raise AssemblyError("the carrier's descriptor is not the package descriptor")
    try:
        value = json.loads(expected)
    except ValueError as error:
        raise AssemblyError(f"descriptor.json is not valid JSON: {error}") from error
    if not isinstance(value, dict) or value.get("channel") != channel:
        raise AssemblyError(f"the carrier belongs to another channel, not {channel}")
    wanted = end + (SEALED_DIGEST_LEN if flags == CARRIER_FLAG_SEALED else 0)
    if len(data) != wanted:
        raise AssemblyError("unexpected bytes around the carrier document")
    if image is not None:
        built = _regular_file(Path(image), "engine image").read_bytes()
        found = built.count(data)
        if found != 1:
            raise AssemblyError(
                f"the engine image must contain exactly one copy of the assembled carrier (found {found})"
            )


# ---------------------------------------------------------------------------
# A/B finalization
# ---------------------------------------------------------------------------


def _descriptor_path(platform: str) -> str:
    return "package/descriptor.json" if platform == "windows" else f"{MAC_PACKAGE}/descriptor.json"


def _slot_tree(slot: Path, platform: str, commit: str) -> Path:
    tree = Path(slot) / "tree"
    if not tree.is_dir() or tree.is_symlink():
        raise AssemblyError(f"slot {slot} has no tree directory")
    descriptor = _regular_file(tree / _descriptor_path(platform), "descriptor.json")
    try:
        recorded = json.loads(descriptor.read_bytes()).get("source_commit")
    except (ValueError, AttributeError) as error:
        raise AssemblyError(f"slot {slot} has an unreadable descriptor") from error
    if recorded != commit:
        raise AssemblyError(f"slot {slot} was built for commit {recorded!r}, not {commit!r}")
    if platform == "windows":
        _regular_file(tree / "uninstall.exe", "uninstall.exe")
    return tree


def _tar_tree(tree: Path) -> bytes:
    """A deterministic GNU tar: byte-ordered, mode 0755/0644 from the execute bit, no time or owner."""
    nodes: list[tuple[str, Path, bool]] = []
    for current, directories, names in os.walk(tree, followlinks=False):
        base = Path(current)
        for name in directories:
            path = base / name
            if path.is_symlink():
                raise AssemblyError(f"symlink in tree: {path}")
            nodes.append((path.relative_to(tree).as_posix(), path, True))
        for name in names:
            path = base / name
            if path.is_symlink() or not path.is_file():
                raise AssemblyError(f"non-regular file in tree: {path}")
            nodes.append((path.relative_to(tree).as_posix(), path, False))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for relative, path, is_directory in sorted(nodes, key=lambda node: node[0].encode("utf-8")):
            info = tarfile.TarInfo(relative + ("/" if is_directory else ""))
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if is_directory:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            else:
                data = path.read_bytes()
                info.size = len(data)
                info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _check_tool_observation(platform: str, observation: Any) -> dict:
    """The observed build-tool inventory that is bound into the provenance (shape only here).

    The producer records what it observed; whether it matches ``config/tools.lock.json`` is decided by
    ``tool_observation.py check`` before a production build and again by the release gate, which never
    trusts the producer's own verdict.
    """
    if (
        not isinstance(observation, dict)
        or set(observation) != {"target", "tools"}
        or observation["target"] != PLATFORM_TARGET[platform]
        or not isinstance(observation["tools"], dict)
        or not observation["tools"]
        or not all(isinstance(entry, dict) for entry in observation["tools"].values())
    ):
        raise AssemblyError(f"tool observation must be {{target: {PLATFORM_TARGET[platform]}, tools: {{...}}}}")
    return observation


def finalize(*, platform: str, slot_a: Path, slot_b: Path, output: Path, commit: str, tool_observation: Any) -> None:
    """Compare two independent slots; publish ``tree.tar`` (from A), provenance and reports."""
    observation = _check_tool_observation(platform, tool_observation)
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise AssemblyError(f"output must not exist: {output}")
    if not COMMIT_RE.match(commit or ""):
        raise AssemblyError("commit must be a full lowercase git commit id")
    a_root, b_root = Path(slot_a).resolve(), Path(slot_b).resolve()
    if a_root == b_root or a_root in b_root.parents or b_root in a_root.parents:
        raise AssemblyError("slots A and B must be two distinct, unrelated directories (one build is not two)")
    tree_a = _slot_tree(slot_a, platform, commit)
    tree_b = _slot_tree(slot_b, platform, commit)
    try:
        inventory_a = package_inputs.build_inventory(tree_a)
        inventory_b = package_inputs.build_inventory(tree_b)
    except (OSError, package_inputs.InventoryError) as error:
        raise AssemblyError(f"a slot tree is not a valid inventory: {error}") from error
    differences = package_inputs.compare_inventories(inventory_a, inventory_b)
    report = {
        "schema": SCHEMA,
        "verdict": "PASS" if not differences else "FAIL",
        "differences": differences,
        "input_hashes": {"first": inventory_a["inventory_sha256"], "second": inventory_b["inventory_sha256"]},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        (scratch / "report-ab.json").write_bytes(canonical_json(report))
        if differences:
            os.rename(scratch, output)
            codes = sorted({str(item.get("code")) for item in differences})
            raise AssemblyError(
                "the two independent unsigned builds differ (" + ", ".join(codes) + "): release blocker, see report-ab.json"
            )
        descriptor = (tree_a / _descriptor_path(platform)).read_bytes()
        provenance = {
            "schema": SCHEMA,
            "source_commit": commit,
            "descriptor_digest": sha256_bytes(descriptor),
            "a_inventory_sha256": inventory_a["inventory_sha256"],
            "b_inventory_sha256": inventory_b["inventory_sha256"],
            "tool_observation": observation,
        }
        (scratch / "tree.tar").write_bytes(_tar_tree(tree_a))
        (scratch / "provenance.json").write_bytes(canonical_json(provenance))
        lines = [f"{sha256_bytes((scratch / name).read_bytes())}  {name}\n" for name in ("provenance.json", "report-ab.json", "tree.tar")]
        (scratch / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")
        os.rename(scratch, output)
    except AssemblyError:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-config")
    check.add_argument("--config", type=Path, required=True)
    check.add_argument("--channel", required=True)
    check.add_argument("--platform", required=True, choices=sorted(PLATFORM_TARGET))
    check.add_argument(
        "--require-tracked", action="store_true",
        help="also require the config to be a git-tracked file identical to HEAD (a build input, not a local edit)",
    )  # fmt: skip
    show = sub.add_parser("origin")
    show.add_argument("--config", type=Path, required=True)
    build = sub.add_parser("assemble")
    build.add_argument("--platform", required=True, choices=sorted(PLATFORM_TARGET))
    build.add_argument("--channel", required=True, choices=PACKAGEABLE_CHANNELS)
    build.add_argument("--config", type=Path, required=True)
    build.add_argument("--commit", required=True)
    build.add_argument("--binaries", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--frontend", type=Path)
    build.add_argument("--packager", type=Path)
    verify = sub.add_parser("verify-carrier")
    verify.add_argument("--carrier", type=Path, required=True)
    verify.add_argument("--descriptor", type=Path, required=True)
    verify.add_argument("--channel", required=True, choices=PACKAGEABLE_CHANNELS)
    verify.add_argument("--image", type=Path)
    final = sub.add_parser("finalize")
    final.add_argument("--platform", required=True, choices=sorted(PLATFORM_TARGET))
    final.add_argument("--slot-a", type=Path, required=True)
    final.add_argument("--slot-b", type=Path, required=True)
    final.add_argument("--output", type=Path, required=True)
    final.add_argument("--commit", required=True)
    final.add_argument("--tool-observation", type=Path, required=True,
                       help="observed build-tool inventory (tool_observation.py observe) bound into provenance.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exit_:
        return 2 if exit_.code else 0
    try:
        if args.command == "check-config":
            if args.channel not in PACKAGEABLE_CHANNELS:
                print(f"assemble_unsigned: channel {args.channel!r} is not packageable", file=sys.stderr)
                return 2
            problems = check_config(load_config(args.config), channel=args.channel, platform=args.platform)
            if args.require_tracked:
                problems += tracked_problems(args.config)
            for problem in problems:
                print(f"assemble_unsigned: {problem}", file=sys.stderr)
            return 1 if problems else 0
        if args.command == "origin":
            print(origin(load_config(args.config)))
            return 0
        if args.command == "assemble":
            assemble(
                platform=args.platform, channel=args.channel, config=args.config, commit=args.commit,
                binaries=args.binaries, output=args.output, frontend=args.frontend, packager=args.packager,
            )  # fmt: skip
            return 0
        if args.command == "verify-carrier":
            verify_carrier(carrier=args.carrier, descriptor=args.descriptor, channel=args.channel, image=args.image)
            return 0
        finalize(platform=args.platform, slot_a=args.slot_a, slot_b=args.slot_b, output=args.output, commit=args.commit,
                 tool_observation=load_config(args.tool_observation))
        return 0
    except AssemblyError as error:
        print(f"assemble_unsigned: refused: {error}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"assemble_unsigned: invalid input: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
