#!/usr/bin/env python3
"""Tool-identity contract for the OTP-25 standalone installers.

Python 3.12+ standard library only. This module owns ``config/tools.lock.json``:
the exact Rust, Xcode/macOS SDK and NSIS identities the reproducible installer
builds must present, plus the Windows compiler/SDK identities that have to be
read from the approved build runner before a Windows native build may proceed.

``validate_tools(lock, observed) -> list[str]`` returns one message per
violation and an empty list only when the supplied inventory matches the lock.
A missing, mismatched or unresolved fingerprint always blocks: there is no
warning mode, no ``*``/``latest`` pin and no fallback that treats an unknown
toolchain as acceptable. The mandatory tool set, its target applicability and
the required fingerprint formats come from ``REQUIRED_TOOLS`` below, never from
the lock, so deleting or hollowing out a lock entry cannot make a build valid.

An observed inventory is a JSON object shaped like::

    {
      "target": "macos-arm64",
      "tools": {"rust": {"channel": "1.98.0", "profile": "minimal", ...}, ...},
      "archives": {"nsis": {"version": "3.08", "sha256": "..."}}
    }

``archives`` may be omitted when the caller verifies the archive digest
elsewhere (``provision-nsis.ps1`` does); when it is present it must match.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA = 1

LOCK_PATH = Path(__file__).resolve().parents[1] / "config" / "tools.lock.json"

# The already-reviewed NSIS 3.08 archive (archive mirror, not a Tauri library).
NSIS_URL = "https://github.com/tauri-apps/binary-releases/releases/download/nsis-3/nsis-3.zip"
NSIS_SHA256 = "1bb9fc85ee5b220d3869325dbb9d191dfe6537070f641c30fbb275c97051fd0c"
NSIS_VERSION = "3.08"
NSIS_ARCHIVE_FILE = "nsis-3.zip"
NSIS_EXTRACT_ROOT = "nsis-3.08"
NSIS_MAKENSIS = "nsis-3.08/makensis.exe"
NSIS_VERSION_ARGUMENT = "/VERSION"

TARGET_NAMES = ("windows-x86_64", "macos-arm64")
LOCK_KEYS = {"schema", "reviewed", "targets", "tools", "archives"}
TARGET_KEYS = {"runner", "build_target", "developer_dir", "minimum_os"}
TOOL_KEYS = {"targets", "identity", "required_unresolved"}
UNRESOLVED_KEYS = {"reason", "must_supply"}
ARCHIVE_KEYS = {
    "version",
    "url",
    "sha256",
    "archive_file",
    "extract_root",
    "makensis",
    "version_argument",
}

# Values that would make a pin meaningless. "*" is also rejected as a substring.
INEXACT_VALUES = frozenset(
    {"", "*", "latest", "current", "stable", "nightly", "any", "unspecified", "tbd", "todo"}
)

# Payload the standalone installer toolchain must never contain. The reviewed
# NSIS URL is served by the tauri-apps mirror organisation, but no Tauri plugin
# or WiX toolset is downloaded, expanded or copied.
FORBIDDEN_PAYLOAD_TOKENS = ("wix", "nsis_tauri_utils")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
# Windows compiler/SDK versions are not three-component semvers: the Windows SDK
# and UCRT report four components ("10.0.26100.0") and so do cl.exe/link.exe
# ("19.44.35211.0"), while some MSVC toolsets still report three
# ("14.44.35207"). Accept exactly three or four numeric components, so a real
# inventory from the approved runner can satisfy the lock.
_WINDOWS_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:\.\d+)?$")
_BUILD_ID = re.compile(r"^[0-9A-Za-z]+$")
_APPLE_VERSION = re.compile(r"^[0-9A-Za-z]+(?:[._][0-9A-Za-z]+)*$")

# Every tool identity a reproducible installer build must present, its target
# applicability and the exact fingerprints (and their formats) it must carry.
# This table is the contract and is deliberately independent of the lock file:
# a build cannot become valid by deleting or hollowing out a lock entry.
# ``None`` means "exact non-placeholder string"; a compiled pattern enforces a
# fingerprint format (versions and SHA-256 digests).
#
# Owner decision 2026-11-08: the Windows MSVC and SDK identities are pinned by
# version only. Their digests have no agreed definition yet; do not invent one.
# This is a deliberate, documented weakening to be improved later.
REQUIRED_TOOLS: dict[str, dict[str, Any]] = {
    "rust": {
        "targets": ("windows-x86_64", "macos-arm64"),
        "fingerprints": {
            "channel": _SEMVER,
            "profile": None,
            "components": None,
            "targets": None,
        },
    },
    "xcode": {
        "targets": ("macos-arm64",),
        "fingerprints": {"version": _APPLE_VERSION, "build": _BUILD_ID},
    },
    "macos_sdk": {
        "targets": ("macos-arm64",),
        "fingerprints": {"version": _APPLE_VERSION},
    },
    "msvc": {
        "targets": ("windows-x86_64",),
        "fingerprints": {
            "cl_version": _WINDOWS_VERSION,
            "linker_version": _WINDOWS_VERSION,
            "msvc_toolset_version": _WINDOWS_VERSION,
        },
    },
    "windows_sdk": {
        "targets": ("windows-x86_64",),
        "fingerprints": {
            "windows_sdk_version": _WINDOWS_VERSION,
            "ucrt_version": _WINDOWS_VERSION,
        },
    },
}


class ToolLockError(ValueError):
    """The tool lock is not strict JSON (for example a duplicate key)."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ToolLockError(f"duplicate JSON key {key!r}")
        obj[key] = value
    return obj


def parse_lock(text: str) -> dict:
    """Parse lock text, rejecting duplicate JSON keys."""
    return json.loads(text, object_pairs_hook=_unique_object)


def load_lock(path: Path | str = LOCK_PATH) -> dict:
    """Read and parse the checked-in tool lock."""
    return parse_lock(Path(path).read_text(encoding="utf-8"))


def validate_lock(lock: Any) -> list[str]:
    """Return every structural or pin violation in a lock document."""
    if not isinstance(lock, dict):
        return ["tools.lock.json must be a JSON object"]

    errors: list[str] = []
    unknown = sorted(str(key) for key in set(lock) - LOCK_KEYS)
    if unknown:
        errors.append(f"lock has unknown fields: {', '.join(unknown)}")
    for key in sorted(LOCK_KEYS - set(lock)):
        errors.append(f"lock is missing {key!r}")
    if lock.get("schema") != SCHEMA:
        errors.append(f"lock schema must be {SCHEMA}, found {lock.get('schema')!r}")
    reviewed = lock.get("reviewed")
    if not isinstance(reviewed, str) or not _DATE.fullmatch(reviewed):
        errors.append("lock must record the review date as YYYY-MM-DD")
    errors.extend(_inexact_pin_errors(lock))

    targets = lock.get("targets")
    if not isinstance(targets, dict):
        errors.append("lock targets must be a JSON object")
        targets = {}
    tools = lock.get("tools")
    if not isinstance(tools, dict):
        errors.append("lock tools must be a JSON object")
        tools = {}
    archives = lock.get("archives")
    if not isinstance(archives, dict):
        errors.append("lock archives must be a JSON object")
        archives = {}

    errors.extend(_validate_targets(targets))
    errors.extend(_validate_tools(tools, set(targets)))
    errors.extend(_validate_archives(archives))
    return errors


def validate_tools(lock: dict, observed: dict) -> list[str]:
    """Compare an observed build inventory against the tool lock.

    Returns an empty list only when the lock itself is valid and every identity
    that applies to the observed target matches exactly. Any ``required_unresolved``
    entry blocks: its fingerprints must be supplied before a real build.
    """
    errors = validate_lock(lock)
    if errors:
        # Comparing against a malformed lock would hide the structural problem.
        return errors
    return _validate_observation(lock, observed)


def _is_exact_string(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and value.lower() not in INEXACT_VALUES
        and "*" not in value
        and "latest" not in value.lower()
    )


def _is_safe_relative_archive_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if value.startswith("/") or "\\" in value or ":" in value:
        return False
    segments = value.split("/")
    return all(segment and segment not in {".", ".."} for segment in segments)


def _inexact_pin_errors(value: Any, path: str = "lock") -> list[str]:
    """Reject wildcard and placeholder pins anywhere in the document."""
    errors: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            errors.extend(_inexact_pin_errors(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_inexact_pin_errors(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        lowered = value.lower()
        if "*" in value:
            errors.append(f"{path} must not use a wildcard pin: {value!r}")
        elif "latest" in lowered:
            errors.append(f"{path} must not follow a moving 'latest' alias: {value!r}")
        elif value == value.strip() and lowered in INEXACT_VALUES:
            errors.append(f"{path} must be an exact pin, not {value!r}")
    return errors


def _validate_targets(targets: dict) -> list[str]:
    errors: list[str] = []
    if set(targets) != set(TARGET_NAMES):
        errors.append(f"lock targets must be exactly {list(TARGET_NAMES)}, found {sorted(targets)}")
    for name in sorted(targets):
        entry = targets[name]
        if not isinstance(entry, dict):
            errors.append(f"target {name!r} must be a JSON object")
            continue
        unknown = sorted(set(entry) - TARGET_KEYS)
        if unknown:
            errors.append(f"target {name!r} has unknown fields: {', '.join(unknown)}")
        for field in ("runner", "build_target"):
            if not _is_exact_string(entry.get(field)):
                errors.append(f"target {name!r} must pin an exact {field}")
        minimum_os = entry.get("minimum_os")
        if minimum_os is not None and not _is_exact_string(minimum_os):
            errors.append(f"target {name!r} minimum_os must be an exact version")
        if name == "macos-arm64":
            developer_dir = entry.get("developer_dir")
            if not _is_exact_string(developer_dir) or not str(developer_dir).startswith("/Applications/Xcode_"):
                errors.append("target 'macos-arm64' must pin the absolute Xcode developer directory")
            if minimum_os is None:
                errors.append("target 'macos-arm64' must pin the minimum macOS version")
    return errors


def _validate_tools(tools: dict, target_names: set[str]) -> list[str]:
    errors: list[str] = []

    # The mandatory tool set and its applicability are fixed by the plan, not by
    # the lock: deleting an inconvenient entry, or hollowing it out to a
    # placeholder identity, cannot shrink what a real build must present.
    for name in sorted(REQUIRED_TOOLS):
        if name not in tools:
            errors.append(
                f"lock must record the mandatory {name!r} tool identity for "
                f"{', '.join(REQUIRED_TOOLS[name]['targets'])}"
            )
    for name in sorted(set(tools) - set(REQUIRED_TOOLS)):
        lowered = str(name).lower()
        if any(token in lowered for token in FORBIDDEN_PAYLOAD_TOKENS):
            errors.append(
                f"tool {name!r} is not part of the standalone installer toolchain: "
                "no WiX toolset or Tauri NSIS plugin is permitted"
            )
        else:
            errors.append(
                f"tool {name!r} is not a reviewed installer tool; the lock pins exactly "
                f"{sorted(REQUIRED_TOOLS)}"
            )

    for name in sorted(tools):
        requirement = REQUIRED_TOOLS.get(name)
        if requirement is None:
            continue  # unknown tools are already rejected above
        entry = tools[name]
        if not isinstance(entry, dict):
            errors.append(f"tool {name!r} must be a JSON object")
            continue
        unknown = sorted(set(entry) - TOOL_KEYS)
        if unknown:
            errors.append(f"tool {name!r} has unknown fields: {', '.join(unknown)}")

        tool_targets = entry.get("targets")
        if not isinstance(tool_targets, list) or not tool_targets:
            errors.append(f"tool {name!r} must list the targets it applies to")
            continue
        if len(set(tool_targets)) != len(tool_targets):
            errors.append(f"tool {name!r} lists a duplicate target")
        for target in tool_targets:
            if target not in target_names:
                errors.append(f"tool {name!r} names unlocked target {target!r}")
        required_targets = list(requirement["targets"])
        if set(tool_targets) != set(required_targets):
            errors.append(
                f"tool {name!r} must apply to exactly {required_targets}, found {sorted(tool_targets)}"
            )

        identity = entry.get("identity")
        unresolved = entry.get("required_unresolved")
        if (identity is None) == (unresolved is None):
            errors.append(f"tool {name!r} must pin exactly one of identity or required_unresolved")
            continue
        if identity is not None:
            errors.extend(_pinned_identity_errors(name, identity, requirement))
        else:
            errors.extend(_unresolved_identity_errors(name, unresolved, requirement))

    return errors


def _pinned_identity_errors(name: str, identity: Any, requirement: dict) -> list[str]:
    """A pinned identity must contain exactly the mandatory fingerprints, well formed."""
    if not isinstance(identity, dict) or not identity:
        return [f"tool {name!r} identity must be a non-empty JSON object"]
    fingerprints = requirement["fingerprints"]
    errors: list[str] = []
    missing = [field for field in fingerprints if field not in identity]
    if missing:
        errors.append(
            f"tool {name!r} identity is missing the mandatory fingerprints: {', '.join(missing)}"
        )
    unexpected = sorted(set(identity) - set(fingerprints))
    if unexpected:
        errors.append(f"tool {name!r} identity has unreviewed fields: {', '.join(unexpected)}")
    for field in fingerprints:
        if field not in identity:
            continue
        value = identity[field]
        if not _is_exact_string(value):
            errors.append(f"tool {name!r} field {field!r} must be an exact string pin, found {value!r}")
            continue
        pattern = fingerprints[field]
        if pattern is not None and not pattern.fullmatch(value):
            errors.append(
                f"tool {name!r} field {field!r} is not a valid fingerprint: {value!r}"
            )
    return errors


def _unresolved_identity_errors(name: str, unresolved: Any, requirement: dict) -> list[str]:
    """An unresolved identity must demand every mandatory fingerprint (fails closed)."""
    if not isinstance(unresolved, dict):
        return [f"tool {name!r} required_unresolved must be a JSON object"]
    errors: list[str] = []
    unknown = sorted(set(unresolved) - UNRESOLVED_KEYS)
    if unknown:
        errors.append(f"tool {name!r} required_unresolved has unknown fields: {', '.join(unknown)}")
    reason = unresolved.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        errors.append(f"tool {name!r} must explain why its identity is unresolved")
    must_supply = unresolved.get("must_supply")
    if not isinstance(must_supply, list) or not must_supply:
        errors.append(f"tool {name!r} must list the fingerprints that have to be supplied")
        return errors
    for field in must_supply:
        if not isinstance(field, str) or not field.strip():
            errors.append(f"tool {name!r} required_unresolved lists an empty fingerprint name")
    if len(set(must_supply)) != len(must_supply):
        errors.append(f"tool {name!r} required_unresolved lists a duplicate fingerprint")
    required = set(requirement["fingerprints"])
    supplied = {str(field) for field in must_supply if isinstance(field, str) and field.strip()}
    missing = sorted(required - supplied)
    if missing:
        errors.append(
            f"tool {name!r} required_unresolved must list every mandatory fingerprint: "
            f"missing {', '.join(missing)}"
        )
    unexpected = sorted(supplied - required)
    if unexpected:
        errors.append(
            f"tool {name!r} required_unresolved lists unreviewed fingerprints: {', '.join(unexpected)}"
        )
    return errors


def _validate_archives(archives: dict) -> list[str]:
    errors: list[str] = []
    for name in sorted(archives):
        if name == "nsis":
            continue
        lowered = str(name).lower()
        if any(token in lowered for token in FORBIDDEN_PAYLOAD_TOKENS):
            errors.append(
                f"archive {name!r} is not permitted: the standalone installer extracts the "
                "reviewed NSIS archive only (no WiX toolset, no Tauri NSIS plugin)"
            )
        else:
            errors.append(
                f"archive {name!r} is not permitted: the standalone installer extracts "
                "exactly the one reviewed NSIS archive"
            )

    nsis = archives.get("nsis")
    if not isinstance(nsis, dict):
        errors.append("lock must pin the 'nsis' archive")
        return errors
    unknown = sorted(set(nsis) - ARCHIVE_KEYS)
    if unknown:
        errors.append(f"archive 'nsis' has unknown fields: {', '.join(unknown)}")
    for field in sorted(ARCHIVE_KEYS - set(nsis)):
        errors.append(f"archive 'nsis' is missing {field!r}")

    expected = {
        "version": NSIS_VERSION,
        "url": NSIS_URL,
        "sha256": NSIS_SHA256,
        "archive_file": NSIS_ARCHIVE_FILE,
        "extract_root": NSIS_EXTRACT_ROOT,
        "makensis": NSIS_MAKENSIS,
        "version_argument": NSIS_VERSION_ARGUMENT,
    }
    for field, value in expected.items():
        actual = nsis.get(field)
        if actual != value:
            errors.append(f"archive 'nsis' field {field!r} must be the reviewed pin {value!r}, found {actual!r}")

    sha256 = nsis.get("sha256")
    if not isinstance(sha256, str) or not _HEX64.fullmatch(sha256):
        errors.append("archive 'nsis' sha256 must be 64 lowercase hex characters")
    url = nsis.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        errors.append("archive 'nsis' url must be an https URL")
    for field in ("archive_file", "extract_root", "makensis"):
        value = nsis.get(field)
        if not _is_safe_relative_archive_path(value):
            errors.append(f"archive 'nsis' field {field!r} must be a relative path without escaping segments")
        if isinstance(value, str):
            lowered = value.lower()
            for token in FORBIDDEN_PAYLOAD_TOKENS:
                if token in lowered:
                    errors.append(f"archive 'nsis' field {field!r} must not reference {token}: {value!r}")
    return errors


def _validate_observation(lock: dict, observed: Any) -> list[str]:
    if not isinstance(observed, dict):
        return ["observed tool inventory must be a JSON object"]
    target = observed.get("target")
    if target not in lock["targets"]:
        return [
            "observed inventory must name the build target "
            f"({', '.join(sorted(lock['targets']))}), found {target!r}"
        ]

    errors: list[str] = []
    observed_tools = observed.get("tools", {})
    if not isinstance(observed_tools, dict):
        errors.append("observed tools must be a JSON object")
        observed_tools = {}

    for name in sorted(lock["tools"]):
        entry = lock["tools"][name]
        if target not in entry["targets"]:
            continue
        if "required_unresolved" in entry:
            unresolved = entry["required_unresolved"]
            errors.append(
                f"tool {name!r} identity is required but unresolved: supply "
                f"{', '.join(unresolved['must_supply'])} from the approved build runner; "
                "reproducible native builds are blocked until the lock records that identity"
            )
            continue
        identity = entry["identity"]
        seen = observed_tools.get(name)
        if not isinstance(seen, dict):
            errors.append(
                f"missing observed identity for tool {name!r} "
                f"(required fields: {', '.join(sorted(identity))})"
            )
            continue
        for field in sorted(identity):
            expected = identity[field]
            if field not in seen:
                errors.append(f"missing observed {name}.{field} (the lock pins {expected!r})")
            elif str(seen[field]) != expected:
                errors.append(
                    f"tool mismatch: {name}.{field} observed {seen[field]!r} "
                    f"but the lock pins {expected!r}"
                )

    archives = observed.get("archives")
    if archives is not None:
        if not isinstance(archives, dict):
            errors.append("observed archives must be a JSON object")
        else:
            for name in sorted(lock["archives"]):
                entry = lock["archives"][name]
                seen = archives.get(name)
                if not isinstance(seen, dict):
                    errors.append(f"missing observed archive {name!r} identity")
                    continue
                for field in ("version", "sha256"):
                    expected = entry[field]
                    if field not in seen:
                        errors.append(f"missing observed archive {name}.{field} (the lock pins {expected!r})")
                    elif str(seen[field]) != expected:
                        errors.append(
                            f"archive mismatch: {name}.{field} observed {seen[field]!r} "
                            f"but the lock pins {expected!r}"
                        )
    return errors
