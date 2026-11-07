#!/usr/bin/env python3
"""Protected signing orchestration and the authenticated release gate (OTP-25 Task 13).

Python 3.12+ standard library only.

This module is the single place that decides *whether* a native installer input may be
signed (``authorize``, ``check_stage_inputs``, ``missing_credentials``), *what commands
run* (``run_stage``, ``assemble``, ``verify_unsigned_inputs`` -- every native tool goes
through an injected ``runner`` so the contract is testable without any native tool) and
*whether a finished evidence bundle may be published* (``validate``).

Hard limits, stated once:

* Nothing here verifies a signature, chain, timestamp, notarization ticket or Gatekeeper
  verdict itself. It starts the native tool, checks its exit status and parses its report
  text. Parsing assumptions for ``signtool``/``codesign``/``notarytool``/``stapler``/``spctl``
  output are written from published documentation and have NOT been checked against real
  tool output on this host (no such tool exists here).
* The Windows publisher is the leaf-certificate fingerprint ``sha256:<DER digest>`` the engine authorizes at
  runtime (``VELES_WIN_PUBLISHER``), never a display name. The gate exports the imported leaf (``certutil
  -store My <id> <file>``, syntax unverified) and requires both its SHA-1 to equal the configured thumbprint
  and its SHA-256 to equal the approved fingerprint; ``signtool`` prints only SHA-1, so each signed file is
  bound to that certificate through the SHA-1 leaf hash it reports (residual: the file-to-certificate step
  rests on SHA-1 text and was never observed against real ``signtool`` output).
* Executable/resource correspondence is delegated to the Task 9 CLI
  (``correspondence.py``); this module never parses PE or Mach-O content.
* There is no unsigned fallback: a stage that cannot sign leaves no output tree, no
  ``SHA256SUMS`` and no scratch directory, and exits non-zero.
* Secrets are only ever ``EnvRef`` objects (resolved by the runner at exec time) or
  files in a private scratch directory that is removed before the stage returns.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
INSTALLER_DIR = HERE.parent
sys.path.insert(0, str(HERE))

import package_inputs  # noqa: E402  (Task 9 inventory library; sibling module)
import release_config  # noqa: E402  (tool-identity lock; sibling module)

SCHEMA = 1
TARGETS = ("windows-x86_64", "macos-arm64")
PLATFORM_TARGET = {"windows": "windows-x86_64", "macos": "macos-arm64"}
STAGES = ("verify-inputs", "sign-nested", "assemble", "sign-outer")
PROTECTED_ENVIRONMENT = "native-release"
TAG_REF = re.compile(r"^refs/tags/(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
WIN_PUBLISHER_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
THUMBPRINT_RE = re.compile(r"^[0-9A-Fa-f]{40}$")
TEAM_ID_RE = re.compile(r"^[0-9A-Z]{10}$")
CARRIER_MAGIC = b"VELESPC1"
CARRIER_HEADER_LEN = 32
SEALED_DIGEST_LEN = 32
CARRIER_FLAG_SEALED = 1

APP = "Veles Bridge Installer.app"
HOST_APP = f"{APP}/Contents/Resources/package/payload/Veles Native Bridge.app"
CARRIER_APP = f"{APP}/Contents/Resources/package/carrier.app"
MAC_PACKAGE = f"{APP}/Contents/Resources/package"
MAC_PAYLOAD = f"{MAC_PACKAGE}/payload"
DMG_NAME = "Veles Bridge Installer.dmg"
SETUP_NAME = "setup.exe"
WIN_PAYLOAD = "package/payload"
WIN_PACKAGE = "package"
UNINSTALLER = "uninstall.exe"

_WIN = (
    "VELES_WIN_PFX_BASE64",
    "VELES_WIN_PFX_PASSWORD",
    "VELES_WIN_CERT_SHA1",
    "VELES_WIN_PUBLISHER",
    "VELES_WIN_TIMESTAMP_URL",
)
_MAC = ("VELES_MAC_P12_BASE64", "VELES_MAC_P12_PASSWORD", "VELES_MAC_IDENTITY", "VELES_MAC_TEAM_ID")
_NOTARY = ("VELES_NOTARY_KEY_BASE64", "VELES_NOTARY_KEY_ID", "VELES_NOTARY_ISSUER")
CREDENTIALS: dict[tuple[str, str], tuple[str, ...]] = {
    ("windows", "nested"): _WIN,
    ("windows", "outer"): _WIN,
    ("macos", "nested"): _MAC,
    ("macos", "outer"): _MAC + _NOTARY,
}
ALL_CREDENTIAL_NAMES = frozenset(_WIN + _MAC + _NOTARY)


class PlanError(Exception):
    """The static signing plan itself is wrong (programming error, not an input error)."""


class StageFailure(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class EnvRef:
    """A command-line value resolved from the environment by the runner, never by us."""

    name: str


@dataclass
class RunResult:
    returncode: int
    output: str = ""


@dataclass
class Outcome:
    ok: bool
    code: str = ""
    detail: str = ""
    fragment: dict = field(default_factory=dict)
    cleanup_failures: list[str] = field(default_factory=list)


@dataclass
class SignRequest:
    platform: str
    stage: str  # "nested" | "outer"
    inputs: Path
    output: Path
    scratch: Path
    reports: Path
    expected_commit: str
    descriptor_path: Path
    boundary: dict
    recipe_digest: str


@dataclass(frozen=True)
class Component:
    role: str
    path: str  # relative to the stage tree
    kind: str  # "pe" | "macho" | "tree"
    entitled: bool = False  # signed with / compared against the host entitlements policy
    # Tree-relative subtrees compared on their own and left out of this tree's comparison: Task 9
    # applies ONE entitlement policy per tree, so a bundle that contains the entitled host app is
    # compared without it (the host app has its own comparison under its own policy).
    exclude: tuple[str, ...] = ()


COMPONENTS: dict[str, list[Component]] = {
    # The carrier is signed LAST: the sealed-inventory digest is written into it after
    # every inventoried payload image is signed, and the carrier's own signature covers it.
    "windows": [
        Component("host", f"{WIN_PAYLOAD}/veles-native-host.exe", "pe"),
        Component("cleanup-helper", f"{WIN_PAYLOAD}/veles-bridge-cleanup.exe", "pe"),
        Component("uninstaller", UNINSTALLER, "pe"),
        Component("engine", f"{WIN_PACKAGE}/carrier.exe", "pe"),
    ],
    # Inside-out: an executable, then the bundle that contains it, innermost bundle first.
    "macos": [
        Component("host", f"{HOST_APP}/Contents/MacOS/veles-native-host", "macho", entitled=True),
        Component("host-bundle", HOST_APP, "tree", entitled=True),
        Component("engine", f"{CARRIER_APP}/Contents/MacOS/veles-bridge-maintenance", "macho"),
        Component("engine-bundle", CARRIER_APP, "tree"),
        Component("installer", f"{APP}/Contents/MacOS/VelesBridgeInstaller", "macho"),
        Component("installer-bundle", APP, "tree", exclude=(f"{HOST_APP}", f"{CARRIER_APP}")),
    ],
}
# The carrier document: a resource of the engine image (Windows) or a file in carrier.app (macOS).
CARRIER_PATH = {"windows": f"{WIN_PACKAGE}/carrier.exe", "macos": f"{CARRIER_APP}/Contents/Resources/veles-policy-carrier"}
# Components that are signed after the seal is written (they contain or are the carrier).
SEALED_AFTER = {"windows": "engine", "macos": "engine"}

RECIPE_FILES = {
    "windows": ("windows/installer.nsi", "scripts/provision-nsis.ps1", "config/tools.lock.json"),
    "macos": (
        "scripts/package-macos.sh",
        "macos/Info.plist",
        "macos/CarrierInfo.plist",
        "macos/HostInfo.plist",
        "macos/Installer.entitlements",
        "macos/Host.entitlements",
        "config/tools.lock.json",
    ),
}
HOST_ENTITLEMENTS = INSTALLER_DIR / "macos" / "Host.entitlements"
NSIS_RECIPE = INSTALLER_DIR / "windows" / "installer.nsi"
CORRESPONDENCE = HERE / "correspondence.py"
MAX_TOOL_OUTPUT = 1 << 20
BOUNDARY_KEYS = {
    "stage",
    "commit",
    "descriptor_digest",
    "recipe_digest",
    "input_inventory_sha256",
    "output_inventory_sha256",
    "credentials",
}
PREDECESSOR = {"nested": "verify-inputs", "outer": "assemble"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicates)


def inventory_digest(tree: Path) -> str:
    """Checkout-path independent digest of a tree (Task 9 ``build_inventory``)."""
    return package_inputs.build_inventory(tree)["inventory_sha256"]


def descriptor_set_digest(digests: dict[str, str]) -> str:
    """The single value the owner approves: a digest over every target's descriptor digest."""
    return sha256_bytes(canonical_json({"descriptors": dict(sorted(digests.items())), "schema": SCHEMA}))


def recipe_digest(root: Path, platform: str) -> str:
    lines = []
    for rel in RECIPE_FILES[platform]:
        path = root / rel
        if not path.is_file() or path.is_symlink():
            raise StageFailure("recipe-missing", f"recipe file is missing: {rel}")
        lines.append(f"{rel}\0{sha256_file(path)}\n")
    return sha256_bytes("".join(lines).encode())


def entitlements_digest(entitlements: dict) -> str:
    return sha256_bytes(json.dumps(entitlements, sort_keys=True, separators=(",", ":")).encode())


def policy_digest(policy: dict) -> str:
    return sha256_bytes(canonical_json(policy))


def check_inside_out(components: Sequence[Component]) -> None:
    """A path may only be signed after everything it contains has been signed."""
    for index, component in enumerate(components):
        for later in components[index + 1 :]:
            if later.path.startswith(component.path + "/"):
                raise PlanError(f"{component.path!r} is signed before {later.path!r}, which it contains")


check_inside_out(COMPONENTS["macos"])
check_inside_out(COMPONENTS["windows"])


# ---------------------------------------------------------------------------
# Authorization, credentials, inputs
# ---------------------------------------------------------------------------


def authorize(env: dict[str, str], descriptor_digest: str, expected_commit: str, *, credentialed: bool = True) -> list[str]:
    """Why this process must not touch a private key (empty list: it may).

    ``credentialed=False`` is for the jobs that hold no credential (input verification, container
    assembly): they still require the trusted event, ref, commit and descriptor digest, but they do not
    run in the protected environment and therefore cannot be asked for it.
    """
    problems = []
    if env.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        problems.append(f"event {env.get('GITHUB_EVENT_NAME')!r} may not sign; only a protected workflow_dispatch may")
    if env.get("GITHUB_HEAD_REF") or env.get("GITHUB_BASE_REF"):
        problems.append("pull-request context may not sign")
    if not TAG_REF.match(env.get("GITHUB_REF", "")):
        problems.append(f"ref {env.get('GITHUB_REF')!r} is not an immutable X.Y.Z release tag")
    if not COMMIT_RE.match(expected_commit or ""):
        problems.append("expected commit is not a full commit id")
    elif env.get("GITHUB_SHA") != expected_commit:
        problems.append("workflow commit differs from the expected commit")
    if credentialed and env.get("VELES_SIGNING_ENVIRONMENT") != PROTECTED_ENVIRONMENT:
        problems.append(f"not running in the protected environment {PROTECTED_ENVIRONMENT!r}")
    if not SHA256_RE.match(descriptor_digest or ""):
        problems.append("descriptor digest is not a sha256")
    if credentialed:
        approved = env.get("VELES_APPROVED_DESCRIPTOR_SHA256", "")
        if not SHA256_RE.match(approved):
            problems.append("no approved descriptor digest was supplied")
        elif approved != descriptor_digest:
            problems.append("the descriptor is not the approved descriptor")
    if env.get("VELES_DESCRIPTOR_SHA256") != descriptor_digest:
        problems.append("the claimed descriptor digest is not the digest of the descriptor file")
    return problems


def signer_policy(platform: str, env: dict[str, str], descriptor: bytes, expected_commit: str) -> tuple[dict, list[str]]:
    """The signing policy implied by the approved descriptor, plus the reasons it cannot be used."""
    problems: list[str] = []
    try:
        document = json.loads(descriptor, object_pairs_hook=_no_duplicates)
        publishers = document["policy"]["publishers"]
        if not isinstance(publishers, list) or not publishers or not all(isinstance(p, str) for p in publishers):
            raise ValueError("publishers")
        if document.get("source_commit") != expected_commit:
            problems.append("descriptor source_commit is not the expected commit")
        if document.get("target") != PLATFORM_TARGET[platform]:
            problems.append("descriptor is for another target")
    except (ValueError, KeyError, TypeError):
        return {}, ["descriptor is not a release descriptor with a publisher policy"]
    if platform == "windows":
        # The engine authorizes sha256:<leaf certificate DER digest> (platform/windows.rs); a display name
        # can never match at runtime and so is never an authorizable identity here either.
        publisher = env.get("VELES_WIN_PUBLISHER", "")
        if not WIN_PUBLISHER_RE.fullmatch(publisher):
            problems.append("the configured Windows publisher is not a sha256:<64 lowercase hex> leaf-certificate fingerprint")
        elif publisher not in publishers:
            problems.append("the configured Windows publisher is not in the approved descriptor policy")
        policy = {"platform": platform, "publisher": publisher, "timestamp": True}
    else:
        team = env.get("VELES_MAC_TEAM_ID", "")
        if f"team:{team}" not in publishers:
            problems.append("the configured Apple team is not in the approved descriptor policy")
        entitlements = plistlib.loads(HOST_ENTITLEMENTS.read_bytes())
        policy = {
            "platform": platform,
            "identity": env.get("VELES_MAC_IDENTITY", ""),
            "team_id": team,
            "hardened_runtime": True,
            "timestamp": True,
            "host_entitlements_sha256": entitlements_digest(entitlements),
            "host_entitlements_file_sha256": sha256_bytes(HOST_ENTITLEMENTS.read_bytes()),
        }
    return policy, problems


def missing_credentials(platform: str, stage: str, env: dict[str, str]) -> list[str]:
    return sorted(name for name in CREDENTIALS[(platform, stage)] if not (env.get(name) or "").strip())


def credential_format_errors(platform: str, env: dict[str, str]) -> list[str]:
    problems = []
    if platform == "windows":
        if not THUMBPRINT_RE.match(env.get("VELES_WIN_CERT_SHA1", "")):
            problems.append("VELES_WIN_CERT_SHA1 is not a 40-hex thumbprint")
        if not env.get("VELES_WIN_TIMESTAMP_URL", "").startswith("https://"):
            problems.append("VELES_WIN_TIMESTAMP_URL is not an https URL")
    else:
        if not TEAM_ID_RE.match(env.get("VELES_MAC_TEAM_ID", "")):
            problems.append("VELES_MAC_TEAM_ID is not a 10-character team id")
        if not env.get("VELES_MAC_IDENTITY", "").startswith("Developer ID Application:"):
            problems.append("VELES_MAC_IDENTITY is not a Developer ID Application identity")
    return problems


def _mode_exec(path: Path) -> bool:
    return bool(path.stat().st_mode & 0o111)


def payload_view(platform: str, root: Path) -> dict[str, Path]:
    """Inventoried payload files by payload-relative path (what the engine's inventory covers)."""
    view: dict[str, Path] = {}
    base = root / (WIN_PAYLOAD if platform == "windows" else MAC_PAYLOAD)
    if base.is_dir():
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            view[path.relative_to(base).as_posix()] = path
    if platform == "windows" and (root / UNINSTALLER).is_file():
        view["maintenance/uninstall.exe"] = root / UNINSTALLER
    return view


def _entries(view: dict[str, Path], executable_from: dict[str, bool] | None = None) -> list[dict]:
    return [
        {
            "executable": executable_from[rel] if executable_from and rel in executable_from else _mode_exec(path),
            "path": rel,
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
        for rel, path in sorted(view.items(), key=lambda item: item[0].encode())
    ]


def _parse_engine_inventory(data: bytes) -> list[dict]:
    document = json.loads(data, object_pairs_hook=_no_duplicates)
    if set(document) != {"schema", "entries"} or document["schema"] != SCHEMA or not isinstance(document["entries"], list):
        raise ValueError("inventory document shape")
    for entry in document["entries"]:
        if set(entry) != {"path", "size", "sha256", "executable"}:
            raise ValueError("inventory entry shape")
        if not SHA256_RE.match(entry["sha256"]) or not isinstance(entry["size"], int):
            raise ValueError("inventory entry values")
    return document["entries"]


def verify_payload(view: dict[str, Path], inventory: list[dict], *, check_exec: bool = True) -> list[str]:
    """Every inventoried file exists with the inventoried bytes; nothing else is in the payload."""
    problems = []
    by_path = {entry["path"]: entry for entry in inventory}
    if len(by_path) != len(inventory):
        problems.append("inventory lists a path twice")
    for rel, path in view.items():
        entry = by_path.get(rel)
        if entry is None:
            problems.append(f"payload file {rel} is not in the inventory")
            continue
        if sha256_file(path) != entry["sha256"] or path.stat().st_size != entry["size"]:
            problems.append(f"payload file {rel} does not match its inventory entry")
        elif check_exec and _mode_exec(path) != entry["executable"]:
            problems.append(f"payload file {rel} has a different executable bit than the inventory")
    for rel in by_path:
        if rel not in view:
            problems.append(f"inventory names {rel}, which the payload does not contain")
    return problems


def _looks_executable(path: Path) -> bool:
    if _mode_exec(path):
        return True
    with path.open("rb") as handle:
        head = handle.read(4)
    return head[:2] == b"MZ" or head in {b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf", b"\xbe\xba\xfe\xca"}


def unexpected_executables(platform: str, root: Path) -> list[str]:
    allowed = {c.path for c in COMPONENTS[platform] if c.kind != "tree"}
    if platform == "windows":
        allowed.add(SETUP_NAME)
    found = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel not in allowed and rel != DMG_NAME and _looks_executable(path):
            found.append(rel)
    return found


def check_stage_inputs(request: SignRequest) -> list[str]:
    """Provenance + payload hashes, before any credential is decoded."""
    problems: list[str] = []
    boundary = request.boundary
    if not isinstance(boundary, dict) or set(boundary) != BOUNDARY_KEYS:
        return ["boundary record is missing or has unexpected fields"]
    expected_stage = PREDECESSOR[request.stage]
    if boundary["stage"] != expected_stage:
        problems.append(f"input boundary is stage {boundary['stage']!r}, not {expected_stage!r}")
    if boundary["credentials"] is not False:
        problems.append("input boundary was produced by a job that held credentials")
    if boundary["commit"] != request.expected_commit:
        problems.append("input boundary is for another commit")
    descriptor_sha = sha256_file(request.descriptor_path)
    if boundary["descriptor_digest"] != descriptor_sha:
        problems.append("input boundary is for another descriptor")
    if boundary["recipe_digest"] != request.recipe_digest:
        problems.append("input boundary was produced with another recipe")
    try:
        actual = inventory_digest(request.inputs)
    except package_inputs.InventoryError as error:
        return problems + [f"input tree is not a valid inventory: {error}"]
    if actual != boundary["output_inventory_sha256"]:
        problems.append("input tree does not match the inventory digest recorded by the previous job")
    for component in COMPONENTS[request.platform]:
        target = request.inputs / component.path
        if not (target.is_dir() if component.kind == "tree" else target.is_file()):
            problems.append(f"required component {component.path} is missing")
    if request.platform == "windows" and request.stage == "outer" and not (request.inputs / SETUP_NAME).is_file():
        problems.append("assembled setup.exe is missing")
    if request.platform == "macos" and request.stage == "outer" and not (request.inputs / DMG_NAME).is_file():
        problems.append("assembled disk image is missing")
    for rel in unexpected_executables(request.platform, request.inputs):
        problems.append(f"unexpected executable {rel} is not part of the signing plan")
    if problems:
        return problems
    package = request.inputs / (WIN_PACKAGE if request.platform == "windows" else MAC_PACKAGE)
    descriptor_in_package = package / "descriptor.json"
    if not descriptor_in_package.is_file() or sha256_file(descriptor_in_package) != descriptor_sha:
        problems.append("the descriptor inside the package is not the approved descriptor")
    inventory_file = package / ("inventory.json" if request.stage == "nested" else "sealed-inventory.json")
    try:
        entries = _parse_engine_inventory(inventory_file.read_bytes())
    except (OSError, ValueError, KeyError, TypeError) as error:
        return problems + [f"{inventory_file.name} is unreadable: {error}"]
    view = payload_view(request.platform, request.inputs)
    if request.stage == "nested":
        problems += verify_payload(view, entries)
    else:
        problems += _verify_signed_payload(view, entries, request.inputs, request.platform)
    return problems


def _verify_signed_payload(view: dict[str, Path], sealed: list[dict], root: Path, platform: str) -> list[str]:
    canonical_path = root / (WIN_PACKAGE if platform == "windows" else MAC_PACKAGE) / "inventory.json"
    try:
        canonical = _parse_engine_inventory(canonical_path.read_bytes())
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [f"inventory.json is unreadable: {error}"]
    problems = verify_payload(view, sealed, check_exec=False)
    expected_exec = {e["path"]: e["executable"] for e in canonical}
    for entry in sealed:
        if entry["path"] in expected_exec and entry["executable"] != expected_exec[entry["path"]]:
            problems.append(f"sealed inventory changes the executable bit of {entry['path']}")
    return problems


# ---------------------------------------------------------------------------
# Carrier sealing
# ---------------------------------------------------------------------------


def _u16(data: bytes, at: int) -> int:
    return int.from_bytes(data[at : at + 2], "little")


def _u64(data: bytes, at: int) -> int:
    return int.from_bytes(data[at : at + 8], "little")


def _locate_carrier(data: bytes, *, embedded: bool) -> int:
    if not embedded:
        if data[:8] != CARRIER_MAGIC:
            raise StageFailure("seal-failed", "the carrier document does not start with the carrier magic")
        return 0
    positions = [m.start() for m in re.finditer(re.escape(CARRIER_MAGIC), data)]
    if len(positions) != 1:
        raise StageFailure("seal-failed", f"expected exactly one carrier document in the image, found {len(positions)}")
    return positions[0]


def seal_carrier(data: bytes, sealed_inventory: bytes, expected_descriptor: bytes, *, embedded: bool) -> bytes:
    """Write the sealed-inventory digest into the carrier (see ``policy.rs`` ``CARRIER_FLAG_SEALED_INVENTORY``).

    * standalone document (macOS file): an unsealed document is upgraded in place;
    * document embedded in a signed image (Windows resource): the producer must have left a
      zeroed 32-byte placeholder; the bytes are overwritten in place and nothing is resized.
    """
    start = _locate_carrier(data, embedded=embedded)
    if len(data) < start + CARRIER_HEADER_LEN:
        raise StageFailure("seal-failed", "truncated carrier header")
    version, flags = _u16(data, start + 8), _u16(data, start + 10)
    offset, length = _u64(data, start + 16), _u64(data, start + 24)
    if version != 1 or flags not in (0, CARRIER_FLAG_SEALED) or int.from_bytes(data[start + 12 : start + 16], "little") != CARRIER_HEADER_LEN:
        raise StageFailure("seal-failed", "unsupported carrier header")
    section_start, section_end = start + offset, start + offset + length
    if offset < CARRIER_HEADER_LEN or length == 0 or section_end > len(data):
        raise StageFailure("seal-failed", "carrier descriptor section is out of bounds")
    if data[section_start:section_end] != expected_descriptor:
        raise StageFailure("seal-failed", "the carrier's descriptor is not the package descriptor")
    digest = hashlib.sha256(sealed_inventory).digest()
    if flags == CARRIER_FLAG_SEALED:
        slot = data[section_end : section_end + SEALED_DIGEST_LEN]
        if len(slot) != SEALED_DIGEST_LEN or any(slot):
            raise StageFailure("seal-failed", "the carrier is already sealed or has no zeroed digest slot")
        return data[:section_end] + digest + data[section_end + SEALED_DIGEST_LEN :]
    if embedded:
        raise StageFailure("seal-failed", "an embedded carrier needs a zeroed sealed-digest placeholder from its producer")
    if len(data) != section_end:
        raise StageFailure("seal-failed", "unexpected bytes after the carrier document")
    patched = bytearray(data)
    patched[10:12] = CARRIER_FLAG_SEALED.to_bytes(2, "little")
    return bytes(patched) + digest


def read_carrier_seal(data: bytes, *, embedded: bool) -> str | None:
    """The sealed-inventory digest recorded in a carrier, or None when it is not sealed."""
    start = _locate_carrier(data, embedded=embedded)
    flags = _u16(data, start + 10)
    end = start + _u64(data, start + 16) + _u64(data, start + 24)
    return data[end : end + SEALED_DIGEST_LEN].hex() if flags == CARRIER_FLAG_SEALED else None


def sealed_inventory_bytes(canonical: list[dict], signed_view: dict[str, Path]) -> bytes:
    """The signed-byte inventory in the engine's exact document shape (see ``verify_correspondence``)."""
    canonical_by_path = {e["path"]: e for e in canonical}
    entries = []
    for rel, path in sorted(signed_view.items(), key=lambda item: item[0].encode()):
        base = canonical_by_path.get(rel)
        if base is None and not _is_signature_artifact(rel):
            raise StageFailure("seal-failed", f"signing produced an unexpected payload file {rel}")
        entries.append(
            {
                "executable": base["executable"] if base else False,
                "path": rel,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    missing = set(canonical_by_path) - set(signed_view)
    if missing:
        raise StageFailure("seal-failed", f"signing dropped payload files: {sorted(missing)}")
    return canonical_json({"entries": entries, "schema": SCHEMA})


def _is_signature_artifact(rel: str) -> bool:
    parts = rel.split("/")
    return len(parts) >= 4 and parts[-1] == "CodeResources" and parts[-2] == "_CodeSignature" and parts[-3] == "Contents" and parts[-4].endswith(".app")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class SubprocessRunner:
    """The only place a child process is started. Secrets arrive as ``EnvRef`` and never as text."""

    def __init__(self, env: dict[str, str], *, timeout: int = 900):
        self.env, self.timeout = dict(env), timeout
        # Child tools get no credential and no token from the environment.
        self.child_env = {k: v for k, v in env.items() if not k.startswith("VELES_") and "TOKEN" not in k.upper()}

    def resolve(self, name: str) -> str | None:
        return shutil.which(name) if os.sep not in name and "/" not in name else name

    def identify(self, name: str) -> dict:
        path = self.resolve(name)
        if not path or not Path(path).is_file():
            return {"path": None, "sha256": None}
        return {"path": path, "sha256": sha256_file(Path(path))}

    def __call__(self, argv: Sequence[Any], *, capture: bool = False) -> RunResult:
        flat = [self.env[item.name] if isinstance(item, EnvRef) else str(item) for item in argv]
        exe = self.resolve(flat[0])
        if exe is None:
            return RunResult(127, f"{flat[0]}: command not found")
        try:
            done = subprocess.run(
                [exe, *flat[1:]],
                env=self.child_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return RunResult(124, "timed out")
        return RunResult(done.returncode, done.stdout[:MAX_TOOL_OUTPUT].decode("utf-8", "replace"))


def _shown(argv: Sequence[Any]) -> str:
    """A command line fit for a failure detail: credential references as ``$NAME``, keychain passwords masked."""
    parts = [f"${a.name}" if isinstance(a, EnvRef) else str(a) for a in argv]
    if parts and Path(parts[0]).stem == "security":
        for index, part in enumerate(parts[:-1]):
            if part in ("-p", "-k"):
                parts[index + 1] = "***"
    return " ".join(parts)[:240]


# ---------------------------------------------------------------------------
# Stage machinery
# ---------------------------------------------------------------------------


class Stage:
    """Runs commands, records what must be undone, and undoes it on every exit path."""

    def __init__(self, request: SignRequest, env: dict[str, str], runner: Callable[..., RunResult]):
        self.request, self.env, self.runner = request, env, runner
        self.undo: list[tuple[str, list[Any]]] = []
        self.cleanup_failures: list[str] = []
        self.tools: dict[str, dict] = {}

    def run(self, argv: Sequence[Any], *, ok_codes: tuple[int, ...] = (0,), what: str = "") -> RunResult:
        name = Path(str(argv[0])).stem
        if name not in self.tools:
            identify = getattr(self.runner, "identify", None)
            self.tools[name] = identify(name) if identify else {"path": None, "sha256": None}
        try:
            result = self.runner(argv)
        except Exception as error:  # noqa: BLE001 - a runner that cannot start a tool is a stage failure
            raise StageFailure("tool-error", f"{name} could not be run ({type(error).__name__})") from error
        if result.returncode not in ok_codes:
            raise StageFailure(what or "tool-failed", f"{name} exited {result.returncode}: {_shown(argv)}")
        return result

    def register_undo(self, label: str, argv: list[Any]) -> None:
        self.undo.append((label, argv))

    def cleanup(self) -> None:
        for label, argv in reversed(self.undo):
            try:
                result = self.runner(argv)
                if result.returncode != 0:
                    self.cleanup_failures.append(f"{label} exited {result.returncode}")
            except Exception as error:  # noqa: BLE001 - cleanup keeps going whatever one step does
                self.cleanup_failures.append(f"{label} raised {type(error).__name__}")
        self.undo.clear()

    def private_dir(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
        return path

    def write_secret(self, path: Path, value: str) -> Path:
        try:
            data = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise StageFailure("credential-invalid", f"{path.name} is not valid base64") from error
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return path

    def correspondence(self, unsigned: Path, signed: Path, kind: str, report: Path, *, entitlements: Path | None = None) -> dict:
        argv: list[Any] = [sys.executable, "-I", CORRESPONDENCE, "--unsigned", unsigned, "--signed", signed, "--kind", kind, "--report", report]
        if entitlements is not None:
            argv += ["--entitlements", entitlements]
        result = self.runner(argv)
        try:
            document = json.loads(report.read_text())
        except (OSError, ValueError) as error:
            raise StageFailure("correspondence-failed", f"no readable correspondence report for {unsigned.name}") from error
        if result.returncode != 0 or document.get("verdict") != "PASS":
            codes = sorted({d.get("code", "?") for d in document.get("differences", []) if isinstance(d, dict)}) or [document.get("error", "no-diagnostic")]
            raise StageFailure("correspondence-failed", f"{unsigned.name}: verdict {document.get('verdict')}: {', '.join(map(str, codes))}")
        return document


def _tree_view(root: Path, exclude: Sequence[str], dest: Path) -> Path:
    """A copy of ``root`` without the excluded (root-parent-relative) subtrees, for one Task 9 comparison."""
    skip = {(root.parent / rel).as_posix() for rel in exclude}
    shutil.copytree(root, dest, symlinks=True, ignore=lambda directory, names: [n for n in names if (Path(directory) / n).as_posix() in skip])
    return dest


def _fail(code: str, detail: str, cleanup_failures: list[str] | None = None) -> Outcome:
    return Outcome(False, code, detail, {}, list(cleanup_failures or []))


def _write_blocked(request: SignRequest, failure: StageFailure) -> None:
    request.reports.mkdir(parents=True, exist_ok=True)
    record = {"schema": SCHEMA, "platform": request.platform, "stage": request.stage, "status": "BLOCKED", "code": failure.code, "detail": failure.detail}
    (request.reports / f"{request.platform}-{request.stage}-blocked.json").write_bytes(canonical_json(record))


def _write_sums(root: Path, destination: Path) -> str:
    lines = [f"{sha256_file(p)}  {p.relative_to(root).as_posix()}\n" for p in sorted(p for p in root.rglob("*") if p.is_file())]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(lines))
    return sha256_bytes("".join(lines).encode())


def _copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, symlinks=True)


def _boundary(request: SignRequest, stage: str, out_digest: str, *, credentials: bool) -> dict:
    return {
        "stage": stage,
        "commit": request.expected_commit,
        "descriptor_digest": sha256_file(request.descriptor_path),
        "recipe_digest": request.recipe_digest,
        "input_inventory_sha256": request.boundary["output_inventory_sha256"],
        "output_inventory_sha256": out_digest,
        "credentials": credentials,
    }


def run_stage(request: SignRequest, env: dict[str, str], runner: Callable[..., RunResult]) -> Outcome:
    platform, stage = request.platform, request.stage
    if (platform, stage) not in CREDENTIALS:
        return _fail("bad-request", f"unknown stage {platform}/{stage}")
    try:
        descriptor = request.descriptor_path.read_bytes()
    except OSError:
        return _fail("not-authorized", "the descriptor file is unreadable")
    problems = authorize(env, sha256_bytes(descriptor), request.expected_commit)
    policy, policy_problems = signer_policy(platform, env, descriptor, request.expected_commit)
    problems += policy_problems
    if problems:
        return _fail("not-authorized", "; ".join(problems))
    absent = missing_credentials(platform, stage, env)
    if absent:
        return _fail("credential-missing", "missing: " + ", ".join(absent))
    malformed = credential_format_errors(platform, env)
    if malformed:
        return _fail("credential-invalid", "; ".join(malformed))
    try:
        input_problems = check_stage_inputs(request)
    except (OSError, package_inputs.InventoryError) as error:
        input_problems = [f"inputs could not be read: {error}"]
    if input_problems:
        return _fail("input-mismatch", "; ".join(input_problems))
    if request.output.exists() or request.scratch.exists():
        return _fail("bad-request", "output and scratch must not exist")
    reports_existed = request.reports.exists()

    stage_obj = Stage(request, env, runner)
    failure: StageFailure | None = None
    fragment: dict = {}
    try:
        request.scratch.mkdir(parents=True)
        request.scratch.chmod(0o700)
        flow = {("windows", "nested"): _windows_nested, ("windows", "outer"): _windows_outer, ("macos", "nested"): _macos_nested, ("macos", "outer"): _macos_outer}
        fragment = flow[(platform, stage)](stage_obj, policy)
        fragment["tools"] = dict(sorted(stage_obj.tools.items()))
    except StageFailure as error:
        failure = error
    except Exception as error:  # noqa: BLE001 - unexpected is still a failure that must clean up
        failure = StageFailure("internal-error", f"{type(error).__name__} while signing")
    finally:
        stage_obj.cleanup()
        _remove_tree(request.scratch, stage_obj)
    if stage_obj.cleanup_failures and failure is None:
        failure = StageFailure("cleanup-failed", "; ".join(stage_obj.cleanup_failures))
    if failure is None:
        try:
            digest = inventory_digest(request.output)
            fragment["boundary"] = _boundary(request, f"sign-{stage}", digest, credentials=True)
            fragment["platform"], fragment["stage"] = platform, f"sign-{stage}"
            # What this credentialed job itself saw; the bundle's context is built from these, never from the gate job.
            fragment["context"] = {
                "event_name": env.get("GITHUB_EVENT_NAME", ""),
                "ref": env.get("GITHUB_REF", ""),
                "sha": env.get("GITHUB_SHA", ""),
                "environment": env.get("VELES_SIGNING_ENVIRONMENT", ""),
                "pull_request": bool(env.get("GITHUB_HEAD_REF") or env.get("GITHUB_BASE_REF")),
            }
            request.reports.mkdir(parents=True, exist_ok=True)
            fragment["sums_sha256"] = _write_sums(request.output, request.reports / "SHA256SUMS")
            (request.reports / f"{platform}-{stage}.json").write_bytes(canonical_json(fragment))
        except (OSError, package_inputs.InventoryError, StageFailure) as error:
            failure = StageFailure("report-failed", str(error))
    if failure is not None:
        _remove_tree(request.output, stage_obj)
        if not reports_existed:
            # Nothing of a failed run survives except the BLOCKED marker written below.
            _remove_tree(request.reports, stage_obj)
        else:
            for name in ("SHA256SUMS", f"{platform}-{stage}.json"):
                (request.reports / name).unlink(missing_ok=True)
        try:
            _write_blocked(request, failure)
        except OSError:
            pass
        return _fail(failure.code, failure.detail, stage_obj.cleanup_failures)
    return Outcome(True, "", "", fragment, [])


def _remove_tree(path: Path, stage_obj: Stage) -> None:
    if not path.exists() and not path.is_symlink():
        return

    def onexc(function, target, error):  # noqa: ANN001
        try:
            os.chmod(target, 0o700)
            function(target)
        except OSError:
            stage_obj.cleanup_failures.append(f"could not remove {Path(target).name}")

    shutil.rmtree(path, onexc=onexc)


# -- Windows ------------------------------------------------------------------


def _windows_store(stage: Stage, policy: dict) -> str:
    """Import the signing certificate into an ephemeral per-user store entry; returns its thumbprint.

    The imported leaf certificate is exported (DER) and must (a) have the configured SHA-1 thumbprint and
    (b) hash, by SHA-256 of the DER, to the approved publisher fingerprint - the identity the engine
    authorizes at runtime. Nothing is signed with a certificate that fails either check.
    """
    thumb = stage.env["VELES_WIN_CERT_SHA1"]
    keydir = stage.private_dir(stage.request.scratch / "keys")
    pfx = stage.write_secret(keydir / "release.pfx", stage.env["VELES_WIN_PFX_BASE64"])
    # Registered before the import: a failed import may still have left material behind.
    stage.register_undo("certutil -delstore", ["certutil", "-user", "-delstore", "My", EnvRef("VELES_WIN_CERT_SHA1")])
    stage.run(["certutil", "-user", "-p", EnvRef("VELES_WIN_PFX_PASSWORD"), "-importPFX", pfx, "NoRoot"], what="credential-import-failed")
    pfx.unlink()
    listing = stage.run(["certutil", "-user", "-store", "My", EnvRef("VELES_WIN_CERT_SHA1")], what="credential-import-failed")
    if thumb.lower() not in re.sub(r"\s", "", listing.output).lower():
        raise StageFailure("credential-import-failed", "the imported certificate does not have the configured thumbprint")
    leaf = keydir / "leaf.cer"
    stage.run(["certutil", "-user", "-store", "My", EnvRef("VELES_WIN_CERT_SHA1"), leaf], what="credential-import-failed")
    try:
        der = leaf.read_bytes()
    except OSError:
        der = b""
    leaf.unlink(missing_ok=True)
    if not der or hashlib.sha1(der).hexdigest().lower() != thumb.lower():
        raise StageFailure("credential-import-failed", "the exported leaf certificate is not the one with the configured thumbprint")
    if f"sha256:{sha256_bytes(der)}" != policy["publisher"]:
        raise StageFailure("credential-import-failed", "the signing certificate does not hash to the approved publisher fingerprint")
    return thumb


def _signtool_sign(stage: Stage, target: Path) -> None:
    stage.run(
        [
            "signtool", "sign", "/fd", "sha256", "/td", "sha256",
            "/tr", EnvRef("VELES_WIN_TIMESTAMP_URL"), "/sha1", EnvRef("VELES_WIN_CERT_SHA1"), target,
        ],
        what="sign-failed",
    )  # fmt: skip


def parse_signtool_verify(output: str) -> dict:
    """Leaf signer name and timestamp presence from ``signtool verify /pa /v`` text (format unverified)."""
    chain = output.split("Signing Certificate Chain:", 1)[-1] if "Signing Certificate Chain:" in output else ""
    for stop in ("The signature is timestamped", "Timestamp Verified by", "Successfully verified"):
        chain = chain.split(stop, 1)[0]
    names = re.findall(r"Issued to:\s*(.+)", chain)
    leaf = re.findall(r"SHA1 hash:\s*([0-9A-Fa-f]{40})\b", chain)
    return {
        "identity": names[-1].strip() if names else None,
        "leaf_sha1": leaf[-1].lower() if leaf else None,
        "timestamped": "The signature is timestamped" in output,
    }


def _signtool_verify(stage: Stage, target: Path, policy: dict) -> dict:
    result = stage.run(["signtool", "verify", "/pa", "/v", target], what="signature-unverified")
    parsed = parse_signtool_verify(result.output)
    # The display name ("Issued to") is a label. Authorization is the leaf certificate: the file's leaf must be
    # the store certificate whose DER was checked against the approved fingerprint (_windows_store).
    if parsed["leaf_sha1"] is None or parsed["leaf_sha1"] != stage.env["VELES_WIN_CERT_SHA1"].lower():
        raise StageFailure("signature-unverified", f"{target.name}: not signed by the approved publisher certificate")
    if not parsed["timestamped"]:
        raise StageFailure("signature-unverified", f"{target.name}: no trusted timestamp")
    return {"verified": True, "timestamped": True, "identity": policy["publisher"]}


def _windows_nested(stage: Stage, policy: dict) -> dict:
    request = stage.request
    _windows_store(stage, policy)
    _copy_tree(request.inputs, request.output)
    out, pre = request.output, request.scratch / "pre"
    pre.mkdir()
    signatures: dict[str, dict] = {}
    descriptor = (request.inputs / WIN_PACKAGE / "descriptor.json").read_bytes()

    for component in COMPONENTS["windows"]:
        if component.role == SEALED_AFTER["windows"]:
            canonical = _parse_engine_inventory((request.inputs / WIN_PACKAGE / "inventory.json").read_bytes())
            sealed = sealed_inventory_bytes(canonical, payload_view("windows", out))
            (out / WIN_PACKAGE / "sealed-inventory.json").write_bytes(sealed)
            image = (request.inputs / component.path).read_bytes()
            (pre / "engine.exe").write_bytes(seal_carrier(image, sealed, descriptor, embedded=True))
            shutil.copy2(pre / "engine.exe", out / component.path)
            seal = {"status": "sealed", "sealed_inventory_sha256": sha256_bytes(sealed), "carrier_sha256": sha256_file(pre / "engine.exe")}
        else:
            shutil.copy2(out / component.path, pre / f"{component.role}.exe")
        _signtool_sign(stage, out / component.path)
        signatures[component.role] = _signtool_verify(stage, out / component.path, policy)

    components = []
    for component in COMPONENTS["windows"]:
        unsigned = pre / f"{component.role}.exe"
        report = request.reports / f"corr-windows-{component.role}.json"
        request.reports.mkdir(parents=True, exist_ok=True)
        stage.correspondence(unsigned, out / component.path, "pe", report)
        components.append(
            {
                "path": component.path,
                "role": component.role,
                "kind": "pe",
                "unsigned_sha256": sha256_file(unsigned),
                "signed_sha256": sha256_file(out / component.path),
                "signature": signatures[component.role],
                "correspondence": {"report": report.name, "verdict": "PASS"},
            }
        )
    return {"components": components, "seal": seal, "policy": policy}


def _extract_inventory(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


def _windows_outer(stage: Stage, policy: dict) -> dict:
    request = stage.request
    _windows_store(stage, policy)
    _copy_tree(request.inputs, request.output)
    out = request.output
    setup = out / SETUP_NAME
    pre = request.scratch / "pre"
    pre.mkdir()
    shutil.copy2(setup, pre / SETUP_NAME)
    pre_sha = sha256_file(setup)
    _signtool_sign(stage, setup)
    signature = _signtool_verify(stage, setup, policy)
    post_sha = sha256_file(setup)
    request.reports.mkdir(parents=True, exist_ok=True)
    report = request.reports / "corr-windows-setup.json"
    # The assembled setup is compared immediately before/after ITS OWN signature, as a PE. Its
    # compressed payload is never compared with the earlier unsigned container.
    stage.correspondence(pre / SETUP_NAME, setup, "pe", report)

    extract = stage.private_dir(request.scratch / "extract")
    stage.run(["7z", "x", "-y", f"-o{extract}", setup], what="extract-failed")
    extracted_package = extract / "$PLUGINSDIR" / "package"
    expected = _extract_inventory(out / WIN_PACKAGE)
    expected["payload/maintenance/uninstall.exe"] = sha256_file(out / UNINSTALLER)
    actual = _extract_inventory(extracted_package) if extracted_package.is_dir() else {}
    if actual != expected:
        differing = sorted({k for k in expected.keys() | actual.keys() if expected.get(k) != actual.get(k)})
        raise StageFailure("nested-hash-mismatch", "extracted payload differs from the signed inputs: " + ", ".join(differing[:8]))
    return {
        "containers": [
            {
                "path": SETUP_NAME,
                "kind": "nsis-setup",
                "pre_sign_sha256": pre_sha,
                "post_sign_sha256": post_sha,
                "signature": signature,
                "correspondence": {"report": report.name, "verdict": "PASS"},
                "nested_hashes_match": True,
                "nested_inventory_sha256": sha256_bytes(canonical_json(expected)),
            }
        ],
        "policy": policy,
    }


# -- macOS --------------------------------------------------------------------


def _macos_keychain(stage: Stage) -> Path:
    keydir = stage.private_dir(stage.request.scratch / "keys")
    p12 = stage.write_secret(keydir / "release.p12", stage.env["VELES_MAC_P12_BASE64"])
    keychain = keydir / "veles-signing.keychain-db"
    password = secrets.token_hex(24)  # protects only this throwaway keychain
    stage.register_undo("security delete-keychain", ["security", "delete-keychain", keychain])
    stage.run(["security", "create-keychain", "-p", password, keychain], what="credential-import-failed")
    stage.run(["security", "set-keychain-settings", "-lut", "3600", keychain], what="credential-import-failed")
    stage.run(["security", "unlock-keychain", "-p", password, keychain], what="credential-import-failed")
    stage.run(
        ["security", "import", p12, "-k", keychain, "-P", EnvRef("VELES_MAC_P12_PASSWORD"), "-T", "/usr/bin/codesign", "-f", "pkcs12"],
        what="credential-import-failed",
    )
    p12.unlink()
    stage.run(["security", "set-key-partition-list", "-S", "apple-tool:,apple:", "-s", "-k", password, keychain], what="credential-import-failed")
    return keychain


def parse_codesign_display(output: str) -> dict:
    """Facts from ``codesign -dvv`` text (format unverified against a real macOS)."""
    flags = re.search(r"flags=0x[0-9a-fA-F]+\(([^)]*)\)", output)
    authorities = re.findall(r"^Authority=(.+)$", output, re.M)
    team = re.search(r"^TeamIdentifier=(.+)$", output, re.M)
    return {
        "hardened_runtime": bool(flags and "runtime" in flags.group(1).split(",")),
        "authority": authorities[0].strip() if authorities else None,
        "timestamped": re.search(r"^Timestamp=.+$", output, re.M) is not None,
        "team_id": team.group(1).strip() if team else None,
    }


def _codesign_check(stage: Stage, target: Path, policy: dict, *, runtime: bool, entitlements: bool = False) -> dict:
    stage.run(["codesign", "--verify", "--strict", "--verbose=2", target], what="signature-unverified")
    display = parse_codesign_display(stage.run(["codesign", "-dvv", target], what="signature-unverified").output)
    if display["authority"] != policy["identity"] or display["team_id"] != policy["team_id"]:
        raise StageFailure("signature-unverified", f"{target.name}: not signed by the approved identity/team")
    if not display["timestamped"]:
        raise StageFailure("signature-unverified", f"{target.name}: no secure timestamp")
    if runtime and not display["hardened_runtime"]:
        raise StageFailure("signature-unverified", f"{target.name}: hardened runtime is not enabled")
    evidence = {"verified": True, "timestamped": True, "identity": display["authority"], "team_id": display["team_id"]}
    if runtime:
        evidence["hardened_runtime"] = True
    if entitlements:
        shown = stage.run(["codesign", "-d", "--entitlements", ":-", target], what="signature-unverified").output
        start = shown.find("<?xml")
        try:
            observed = plistlib.loads(shown[start:].encode()) if start >= 0 else None
        except (plistlib.InvalidFileException, ValueError, Exception):  # noqa: BLE001 - any parse failure is a mismatch
            observed = None
        expected = plistlib.loads(HOST_ENTITLEMENTS.read_bytes())
        if observed != expected:
            raise StageFailure("entitlements-mismatch", f"{target.name}: signed entitlements differ from Host.entitlements")
        evidence["entitlements_sha256"] = entitlements_digest(observed)
    return evidence


def _codesign(stage: Stage, target: Path, keychain: Path, *, runtime: bool, entitlements: bool = False) -> None:
    argv: list[Any] = ["codesign", "--force", "--timestamp"]
    if runtime:
        argv += ["--options", "runtime"]
    argv += ["--sign", EnvRef("VELES_MAC_IDENTITY"), "--keychain", keychain]
    if entitlements:
        argv += ["--entitlements", HOST_ENTITLEMENTS]
    stage.run([*argv, target], what="sign-failed")


def _macos_nested(stage: Stage, policy: dict) -> dict:
    request = stage.request
    keychain = _macos_keychain(stage)
    _copy_tree(request.inputs, request.output)
    out = request.output
    descriptor = (request.inputs / MAC_PACKAGE / "descriptor.json").read_bytes()
    unsigned_sealed = request.scratch / "unsigned-sealed"
    signatures: dict[str, dict] = {}
    seal: dict = {}
    sealed_done = False
    for component in COMPONENTS["macos"]:
        if component.role == SEALED_AFTER["macos"] and not sealed_done:
            canonical = _parse_engine_inventory((request.inputs / MAC_PACKAGE / "inventory.json").read_bytes())
            sealed = sealed_inventory_bytes(canonical, payload_view("macos", out))
            carrier_rel = CARRIER_PATH["macos"]
            carrier = seal_carrier((request.inputs / carrier_rel).read_bytes(), sealed, descriptor, embedded=False)
            (out / MAC_PACKAGE / "sealed-inventory.json").write_bytes(sealed)
            (out / carrier_rel).write_bytes(carrier)
            # The unsigned comparison baseline is the input plus exactly these two sealing edits.
            _copy_tree(request.inputs, unsigned_sealed)
            (unsigned_sealed / MAC_PACKAGE / "sealed-inventory.json").write_bytes(sealed)
            (unsigned_sealed / carrier_rel).write_bytes(carrier)
            seal = {"status": "sealed", "sealed_inventory_sha256": sha256_bytes(sealed), "carrier_sha256": sha256_bytes(carrier)}
            sealed_done = True
        target = out / component.path
        _codesign(stage, target, keychain, runtime=True, entitlements=component.entitled)
        signatures[component.role] = _codesign_check(stage, target, policy, runtime=True, entitlements=component.role == "host")
    request.reports.mkdir(parents=True, exist_ok=True)
    components = []
    for component in COMPONENTS["macos"]:
        report = request.reports / f"corr-macos-{component.role}.json"
        policy_file = HOST_ENTITLEMENTS if component.entitled else None
        before, after = unsigned_sealed / component.path, out / component.path
        if component.exclude:
            view = request.scratch / f"view-{component.role}"
            before = _tree_view(before, component.exclude, view / "unsigned" / before.name)
            after = _tree_view(after, component.exclude, view / "signed" / after.name)
        document = stage.correspondence(before, after, component.kind, report, entitlements=policy_file)
        keys = ("unsigned_sha256", "signed_sha256") if component.kind == "macho" else ("unsigned_inventory_sha256", "signed_inventory_sha256")
        components.append(
            {
                "path": component.path,
                "role": component.role,
                "kind": component.kind,
                "unsigned_sha256": document["input_hashes"][keys[0]],
                "signed_sha256": document["input_hashes"][keys[1]],
                "signature": signatures[component.role],
                "correspondence": {"report": report.name, "verdict": "PASS"},
            }
        )
    return {"components": components, "seal": seal, "policy": policy}


def _notary_args(key: Path) -> list[Any]:
    return ["--key", key, "--key-id", EnvRef("VELES_NOTARY_KEY_ID"), "--issuer", EnvRef("VELES_NOTARY_ISSUER")]


def _mount(stage: Stage, image: Path, mountpoint: Path) -> Path:
    mountpoint.mkdir(parents=True)
    stage.register_undo(f"hdiutil detach {mountpoint.name}", ["hdiutil", "detach", mountpoint])
    stage.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-noverify", "-mountpoint", mountpoint, image], what="mount-failed")
    return mountpoint


def _macos_outer(stage: Stage, policy: dict) -> dict:
    request = stage.request
    keychain = _macos_keychain(stage)
    notary_dir = stage.private_dir(request.scratch / "notary")
    key = stage.write_secret(notary_dir / "AuthKey.p8", stage.env["VELES_NOTARY_KEY_BASE64"])
    _copy_tree(request.inputs, request.output)
    out = request.output
    dmg = out / DMG_NAME
    pre = request.scratch / "pre"
    pre.mkdir()
    pre_dmg = pre / DMG_NAME
    shutil.copy2(dmg, pre_dmg)
    pre_sha = sha256_file(dmg)
    app_inventory = package_inputs.build_inventory(out / APP)
    # What is about to be signed must contain exactly the signed application tree.
    pre_mount = _mount(stage, pre_dmg, request.scratch / "mnt-pre")
    if _tree_digest(pre_mount / APP) != app_inventory["inventory_sha256"] or _extra_entries(pre_mount, {APP}):
        raise StageFailure("dmg-contents-mismatch", "the disk image does not contain exactly the signed installer app")

    stage.run(["codesign", "--force", "--timestamp", "--sign", EnvRef("VELES_MAC_IDENTITY"), "--keychain", keychain, dmg], what="sign-failed")
    submit = stage.run(["xcrun", "notarytool", "submit", dmg, *_notary_args(key), "--wait", "--output-format", "json"], what="notarization-failed")
    submitted = _json_object(submit.output)
    if submitted.get("status") != "Accepted" or not submitted.get("id"):
        raise StageFailure("notarization-failed", f"notary status is {submitted.get('status')!r}, not Accepted")
    log = stage.run(["xcrun", "notarytool", "log", str(submitted["id"]), *_notary_args(key)], what="notarization-failed")
    notary_log = _json_object(log.output)
    if notary_log.get("status") != "Accepted" or notary_log.get("issues"):
        raise StageFailure("notarization-failed", "the notary log is not a clean Accepted result")
    stage.run(["xcrun", "stapler", "staple", dmg], what="staple-failed")
    stage.run(["xcrun", "stapler", "validate", dmg], what="staple-failed")
    signature = _codesign_check(stage, dmg, policy, runtime=False)
    assessed = stage.run(["spctl", "--assess", "--type", "open", "--context", "context:primary-signature", "-vv", dmg], what="gatekeeper-rejected")
    app_assessed = stage.run(["spctl", "--assess", "--type", "exec", "-vv", out / APP], what="gatekeeper-rejected")
    for text in (assessed.output, app_assessed.output):
        if "accepted" not in text or "Notarized Developer ID" not in text:
            raise StageFailure("gatekeeper-rejected", "Gatekeeper did not accept a notarized Developer ID result")

    post_mount = _mount(stage, dmg, request.scratch / "mnt-post")
    request.reports.mkdir(parents=True, exist_ok=True)
    report = request.reports / "corr-macos-dmg-contents.json"
    # DMG: extract the payload and compare it as a tree (Task 9 does not parse containers).
    stage.correspondence(pre_mount, post_mount, "tree", report)
    return {
        "containers": [
            {
                "path": DMG_NAME,
                "kind": "dmg",
                "pre_sign_sha256": pre_sha,
                "post_sign_sha256": sha256_file(dmg),
                "signature": signature,
                "notarization": {"status": submitted["status"], "submission_id": str(submitted["id"]), "log_sha256": sha256_bytes(log.output.encode())},
                "staple": {"validated": True},
                "gatekeeper": {"accepted": True},
                "inner_app_gatekeeper": {"accepted": True},
                "contents_match": True,
                "correspondence": {"report": report.name, "verdict": "PASS"},
            }
        ],
        "policy": policy,
    }


def _tree_digest(path: Path) -> str:
    try:
        return inventory_digest(path)
    except (OSError, package_inputs.InventoryError):
        return ""


def _extra_entries(mount: Path, allowed: set[str]) -> list[str]:
    return sorted(p.name for p in mount.iterdir() if p.name not in allowed)


def _json_object(text: str) -> dict:
    start = text.find("{")
    try:
        value = json.loads(text[start:]) if start >= 0 else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# Jobs without credentials: verify unsigned inputs, assemble containers
# ---------------------------------------------------------------------------


def tools_lock() -> dict:
    """The reviewed tool-identity lock of the commit being released (tests substitute a synthetic one)."""
    return release_config.load_lock()


def tool_observation_problems(target: str, observation: Any) -> list[str]:
    """Why this observed build-tool inventory may not stand behind a ``target`` release (empty: it matches).

    There is no warning mode: a missing, mismatched or unresolved compiler/SDK identity (the Windows MSVC and SDK
    identities are recorded as ``required_unresolved`` until the owner supplies them) blocks.
    """
    if not isinstance(observation, dict):
        return ["tool observation is not an object"]
    try:
        problems = release_config.validate_tools(tools_lock(), observation)
    except (OSError, ValueError) as error:
        return [f"tool lock is unreadable: {error}"]
    if observation.get("target") != target:
        problems.append(f"tool observation is for {observation.get('target')!r}, not {target!r}")
    return problems


def verify_unsigned_inputs(
    *, platform: str, a: Path, b: Path, provenance: Path, descriptor_path: Path, env: dict[str, str],
    expected_commit: str, recipe: str, output: Path, reports: Path,
) -> Outcome:  # fmt: skip
    """Job 1: A/B unsigned equality, provenance and payload hashes. Runs with no credential."""
    if ALL_CREDENTIAL_NAMES & {k for k, v in env.items() if v}:
        return _fail("credentials-present", "the input-verification job must not hold signing credentials")
    try:
        descriptor = descriptor_path.read_bytes()
    except OSError:
        return _fail("not-authorized", "the descriptor file is unreadable")
    problems = authorize(env, sha256_bytes(descriptor), expected_commit, credentialed=False)
    if problems:
        return _fail("not-authorized", "; ".join(problems))
    if output.exists():
        return _fail("bad-request", "output must not exist")
    try:
        first = package_inputs.build_inventory(a)
        second = package_inputs.build_inventory(b)
    except (OSError, package_inputs.InventoryError) as error:
        return _fail("input-mismatch", f"unsigned input is not a valid inventory: {error}")
    differences = package_inputs.compare_inventories(first, second)
    reports.mkdir(parents=True, exist_ok=True)
    target = PLATFORM_TARGET[platform]
    ab = {"schema": SCHEMA, "verdict": "PASS" if not differences else "FAIL", "differences": differences, "input_hashes": {"first": first["inventory_sha256"], "second": second["inventory_sha256"]}}
    (reports / f"ab-{target}.json").write_bytes(canonical_json(ab))
    if differences:
        return _fail("ab-mismatch", "independent unsigned builds A and B differ: " + ", ".join(sorted({str(d.get('code')) for d in differences})))
    try:
        record = load_json(provenance)
        if (
            set(record) != {"schema", "source_commit", "descriptor_digest", "a_inventory_sha256", "b_inventory_sha256", "tool_observation"}
            or record["schema"] != SCHEMA
        ):
            raise ValueError("provenance shape")
    except (OSError, ValueError, TypeError) as error:
        return _fail("provenance-invalid", f"provenance is unreadable or malformed: {error}")
    claims = {
        "source_commit": expected_commit,
        "descriptor_digest": sha256_bytes(descriptor),
        "a_inventory_sha256": first["inventory_sha256"],
        "b_inventory_sha256": second["inventory_sha256"],
    }
    wrong = sorted(k for k, v in claims.items() if record.get(k) != v)
    if wrong:
        return _fail("provenance-invalid", "provenance disagrees with the inputs on: " + ", ".join(wrong))
    # Before any signing job can start: the tools that built these bytes must be the locked ones.
    tool_problems = tool_observation_problems(target, record["tool_observation"])
    if tool_problems:
        return _fail("tools-unverified", "; ".join(tool_problems[:6]))
    (reports / f"tools-{target}.json").write_bytes(canonical_json(record["tool_observation"]))
    request = SignRequest(platform, "nested", a, output, Path("."), reports, expected_commit, descriptor_path, {}, recipe)
    request.boundary = {
        "stage": "verify-inputs",
        "commit": expected_commit,
        "descriptor_digest": sha256_bytes(descriptor),
        "recipe_digest": recipe,
        "input_inventory_sha256": first["inventory_sha256"],
        "output_inventory_sha256": first["inventory_sha256"],
        "credentials": False,
    }
    structural = check_stage_inputs(request)
    if structural:
        return _fail("input-mismatch", "; ".join(structural))
    _copy_tree(a, output)  # the already-built A bytes, unchanged; nothing is rebuilt
    boundary = dict(request.boundary, stage="verify-inputs")
    fragment = {
        "platform": platform,
        "stage": "verify-inputs",
        "boundary": boundary,
        "ab_report": f"ab-{target}.json",
        "tool_observation": f"tools-{target}.json",
        "a_inventory_sha256": first["inventory_sha256"],
        "b_inventory_sha256": second["inventory_sha256"],
    }
    (reports / f"{platform}-verify-inputs.json").write_bytes(canonical_json(fragment))
    return Outcome(True, "", "", fragment)


def assemble(request: SignRequest, env: dict[str, str], runner: Callable[..., RunResult]) -> Outcome:
    """Job 3: build the container from the signed nested tree. Runs with NO credential."""
    present = sorted(name for name in ALL_CREDENTIAL_NAMES if env.get(name))
    if present:
        return _fail("credentials-present", "the assembly job must not hold signing credentials: " + ", ".join(present))
    boundary = request.boundary
    if not isinstance(boundary, dict) or set(boundary) != BOUNDARY_KEYS:
        return _fail("input-mismatch", "boundary record is missing or malformed")
    try:
        descriptor_sha = sha256_file(request.descriptor_path)
    except OSError:
        return _fail("not-authorized", "the descriptor file is unreadable")
    problems = authorize(env, descriptor_sha, request.expected_commit, credentialed=False)
    if boundary["stage"] != "sign-nested" or boundary["credentials"] is not True:
        problems.append("assembly must consume the credentialed nested-signing output")
    if boundary["commit"] != request.expected_commit:
        problems.append("boundary is for another commit")
    if boundary["descriptor_digest"] != sha256_file(request.descriptor_path):
        problems.append("boundary is for another descriptor")
    if boundary["recipe_digest"] != request.recipe_digest:
        problems.append("boundary was produced with another recipe")
    try:
        if inventory_digest(request.inputs) != boundary["output_inventory_sha256"]:
            problems.append("input tree does not match the digest recorded by the signing job")
        if recipe_digest(INSTALLER_DIR, request.platform) != request.recipe_digest:
            problems.append("the checked-out recipe is not the verified recipe")
    except (OSError, package_inputs.InventoryError, StageFailure) as error:
        problems.append(str(error))
    if problems:
        return _fail("input-mismatch", "; ".join(problems))
    if request.output.exists():
        return _fail("bad-request", "output must not exist")
    stage = Stage(request, env, runner)
    failure: StageFailure | None = None
    try:
        _copy_tree(request.inputs, request.output)
        if request.platform == "windows":
            tool = env.get("NSIS_PATH", "")
            if not tool:
                raise StageFailure("tool-missing", "NSIS_PATH is not set")
            version = stage.run([tool, "/VERSION"], what="tool-pin-mismatch").output.strip()
            if version != "v3.08":
                raise StageFailure("tool-pin-mismatch", f"makensis reports {version!r}, not the pinned v3.08")
            package = request.output / WIN_PACKAGE
            number = _descriptor_version(package / "descriptor.json")
            stage.run(
                [tool, "/NOCD", f"/DVERSION={number}", f"/DINPUT_DIR={package}", f"/DOUTFILE={request.output / SETUP_NAME}",
                 f"/DUNINSTALLER_SIGNED={request.output / UNINSTALLER}", NSIS_RECIPE],
                what="assemble-failed",
            )  # fmt: skip
            produced = request.output / SETUP_NAME
        else:
            staging = request.scratch / "dmg-root"
            staging.parent.mkdir(parents=True, exist_ok=True)
            staging.mkdir()
            _copy_tree(request.output / APP, staging / APP)
            produced = request.output / DMG_NAME
            stage.run(["hdiutil", "create", "-srcfolder", staging, "-volname", "Veles Bridge Installer", "-fs", "HFS+", "-format", "UDZO", "-ov", produced], what="assemble-failed")
        if not produced.is_file():
            raise StageFailure("assemble-failed", f"{produced.name} was not produced")
    except StageFailure as error:
        failure = error
    except Exception as error:  # noqa: BLE001
        failure = StageFailure("internal-error", f"{type(error).__name__} while assembling")
    finally:
        if request.scratch.exists():
            _remove_tree(request.scratch, stage)
    if failure is not None:
        _remove_tree(request.output, stage)
        return _fail(failure.code, failure.detail, stage.cleanup_failures)
    try:
        digest = inventory_digest(request.output)
    except package_inputs.InventoryError as error:
        _remove_tree(request.output, stage)
        return _fail("report-failed", str(error))
    fragment = {"platform": request.platform, "stage": "assemble", "tools": dict(sorted(stage.tools.items())), "boundary": _boundary(request, "assemble", digest, credentials=False)}
    request.reports.mkdir(parents=True, exist_ok=True)
    (request.reports / f"{request.platform}-assemble.json").write_bytes(canonical_json(fragment))
    return Outcome(True, "", "", fragment)


def _descriptor_version(path: Path) -> str:
    version = load_json(path).get("version", "")
    if not re.match(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", str(version)):
        raise StageFailure("assemble-failed", "descriptor version is not MAJOR.MINOR.PATCH")
    return str(version)


# ---------------------------------------------------------------------------
# Evidence bundle assembly and the gate
# ---------------------------------------------------------------------------

REQUIRED_ROLES = {"windows-x86_64": [c.role for c in COMPONENTS["windows"]], "macos-arm64": [c.role for c in COMPONENTS["macos"]]}
REQUIRED_TOOLS = {"windows-x86_64": ("signtool", "certutil", "7z"), "macos-arm64": ("codesign", "security", "xcrun", "spctl", "hdiutil")}
EVIDENCE_KEYS = {"schema", "source_commit", "descriptor_digest", "descriptors", "recipes", "policy_digest", "context", "targets", "files"}
REPORT_KEYS = {"schema", "target", "source_commit", "descriptor_digest", "recipe_digest", "policy_digest", "policy", "unsigned", "boundaries", "components", "containers", "seal", "tools"}
STAGE_CREDENTIALS = {"verify-inputs": False, "sign-nested": True, "assemble": False, "sign-outer": True}


def compose_target_report(*, target: str, commit: str, descriptor: str, recipe: str, fragments: dict[str, dict]) -> dict:
    """One target's report from its four stage fragments; refuses fragments that disagree."""
    missing = [stage for stage in STAGES if stage not in fragments]
    if missing:
        raise StageFailure("report-incomplete", f"missing stage fragments: {missing}")
    verify, nested, outer = fragments["verify-inputs"], fragments["sign-nested"], fragments["sign-outer"]
    if nested.get("policy") != outer.get("policy") or not nested.get("policy"):
        raise StageFailure("report-incomplete", "the two signing stages used different signing policies")
    tools: dict[str, dict] = {}
    for stage in STAGES:
        for name, identity in (fragments[stage].get("tools") or {}).items():
            if tools.setdefault(name, identity) != identity:
                raise StageFailure("report-incomplete", f"tool {name} changed identity between stages")
    prefix = lambda name: f"reports/{name}"  # noqa: E731
    components = [dict(c, correspondence=dict(c["correspondence"], report=prefix(c["correspondence"]["report"]))) for c in nested["components"]]
    containers = [dict(c, correspondence=dict(c["correspondence"], report=prefix(c["correspondence"]["report"]))) for c in outer["containers"]]
    return {
        "schema": SCHEMA,
        "target": target,
        "source_commit": commit,
        "descriptor_digest": descriptor,
        "recipe_digest": recipe,
        "policy_digest": policy_digest(nested["policy"]),
        "policy": nested["policy"],
        "unsigned": {
            "a_inventory_sha256": verify["a_inventory_sha256"],
            "b_inventory_sha256": verify["b_inventory_sha256"],
            "ab_report": prefix(verify["ab_report"]),
            "tool_observation": prefix(verify["tool_observation"]),
            "chosen": "A",
        },
        "boundaries": [fragments[stage]["boundary"] for stage in STAGES],
        "components": components,
        "containers": containers,
        "seal": nested["seal"],
        "tools": tools,
    }


def build_bundle(*, bundle: Path, target_reports: dict[str, Path], reports_root: Path, context: dict, commit: str, descriptors: dict[str, str], recipes: dict[str, str]) -> None:
    """Assemble ``evidence.json`` from stage outputs. ``reports_root`` holds every stage's reports."""
    bundle.mkdir(parents=True)
    files: dict[str, str] = {}
    for source in sorted(p for p in reports_root.rglob("*.json") if p.is_file()):
        rel = f"reports/{source.relative_to(reports_root).as_posix()}"
        (bundle / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, bundle / rel)
        files[rel] = sha256_file(bundle / rel)
    policies = {t: load_json(path)["policy"] for t, path in target_reports.items()}
    evidence = {
        "schema": SCHEMA,
        "source_commit": commit,
        "descriptor_digest": descriptor_set_digest(descriptors),
        "descriptors": descriptors,
        "recipes": recipes,
        "policy_digest": policy_digest(policies),
        "context": context,
        "targets": {t: f"reports/{p.name}" for t, p in target_reports.items()},
        "files": files,
    }
    (bundle / "evidence.json").write_bytes(canonical_json(evidence))


def validate(bundle: Path, expected_commit: str, expected_descriptor: str) -> list[str]:
    """Why this bundle may NOT be published (empty list: it is complete and independently verified)."""
    errors: list[str] = []
    try:
        evidence = load_json(bundle / "evidence.json")
    except (OSError, ValueError) as error:
        return [f"evidence.json is unreadable: {error}"]
    if not isinstance(evidence, dict):
        return ["evidence.json is not an object"]
    unknown = sorted(set(evidence) - EVIDENCE_KEYS)
    if unknown:
        errors.append(f"evidence has unknown fields (no waivers exist): {unknown}")
    missing = sorted(EVIDENCE_KEYS - set(evidence))
    if missing:
        return errors + [f"evidence is missing fields: {missing}"]
    if not COMMIT_RE.match(expected_commit or ""):
        errors.append("expected commit is not a full commit id")
    if evidence["schema"] != SCHEMA:
        errors.append("evidence schema is unsupported")
    if evidence["source_commit"] != expected_commit:
        errors.append("evidence commit differs from the expected commit")
    if evidence["descriptor_digest"] != expected_descriptor:
        errors.append("evidence descriptor digest differs from the expected descriptor")
    descriptors = evidence["descriptors"]
    if not isinstance(descriptors, dict) or set(descriptors) != set(TARGETS):
        errors.append("evidence descriptors must name exactly the release targets")
        descriptors = {}
    elif descriptor_set_digest(descriptors) != evidence["descriptor_digest"]:
        errors.append("evidence descriptor digest is not the digest of its per-target descriptors")
    recipes = evidence["recipes"] if isinstance(evidence["recipes"], dict) else {}
    if set(recipes) != set(TARGETS) or not all(SHA256_RE.match(str(v)) for v in recipes.values()):
        errors.append("evidence recipes must be a sha256 for exactly the release targets")
        recipes = {}
    errors += _validate_context(evidence["context"], expected_commit)
    errors += _validate_files(bundle, evidence)
    targets = evidence["targets"]
    if not isinstance(targets, dict) or set(targets) != set(TARGETS):
        errors.append(f"evidence targets must be exactly {list(TARGETS)}; got {sorted(targets) if isinstance(targets, dict) else targets}")
        targets = {t: p for t, p in (targets or {}).items() if t in TARGETS} if isinstance(targets, dict) else {}
    policies = {}
    for target, rel in sorted(targets.items()):
        report_errors, policy = _validate_target(bundle, evidence, target, rel, descriptors.get(target), recipes.get(target), expected_commit)
        errors += [f"{target}: {e}" for e in report_errors]
        policies[target] = policy
    if len(policies) == len(TARGETS) and all(isinstance(p, dict) for p in policies.values()) and policy_digest(policies) != evidence["policy_digest"]:
        errors.append("evidence policy digest is not the digest of the per-target signing policies")
    return errors


def _validate_context(context: Any, expected_commit: str) -> list[str]:
    if not isinstance(context, dict):
        return ["context is missing"]
    errors = []
    if context.get("event_name") != "workflow_dispatch" or context.get("pull_request") is not False:
        errors.append("event is not a protected workflow_dispatch (a pull request can never publish)")
    if context.get("environment") != PROTECTED_ENVIRONMENT:
        errors.append(f"protected environment is not {PROTECTED_ENVIRONMENT!r}")
    if not TAG_REF.match(str(context.get("ref", ""))):
        errors.append("ref is not an immutable release tag")
    if context.get("sha") != expected_commit:
        errors.append("context sha differs from the expected commit")
    return errors


def _validate_files(bundle: Path, evidence: dict) -> list[str]:
    errors = []
    files = evidence["files"]
    if not isinstance(files, dict):
        return ["evidence file index is malformed"]
    indexed = set()
    for rel, digest in sorted(files.items()):
        indexed.add(rel)
        path = bundle / rel
        if not path.is_file() or path.is_symlink():
            errors.append(f"indexed file {rel} is missing")
        elif sha256_file(path) != digest:
            errors.append(f"indexed file {rel} does not match its sha256")
    for path in sorted(p for p in bundle.rglob("*") if p.is_file()):
        rel = path.relative_to(bundle).as_posix()
        if rel != "evidence.json" and rel not in indexed:
            errors.append(f"unindexed file {rel} is not evidence")
    return errors


def _read_indexed(bundle: Path, evidence: dict, rel: str) -> tuple[Any, str | None]:
    if rel not in evidence["files"]:
        return None, f"report {rel} is not in the evidence index"
    try:
        return load_json(bundle / rel), None
    except (OSError, ValueError):
        return None, f"report {rel} is unreadable"


def _validate_target(bundle: Path, evidence: dict, target: str, rel: str, descriptor: str | None, recipe: str | None, commit: str) -> tuple[list[str], dict]:
    report, problem = _read_indexed(bundle, evidence, rel)
    if problem or not isinstance(report, dict):
        return [problem or "target report is not an object"], {}
    errors: list[str] = []
    extra, absent = sorted(set(report) - REPORT_KEYS), sorted(REPORT_KEYS - set(report))
    if extra:
        errors.append(f"report has unknown fields: {extra}")
    if absent:
        return errors + [f"report is missing fields: {absent}"], {}
    if report["target"] != target:
        errors.append(f"report names target {report['target']!r}")
    for field_name, expected in (("source_commit", commit), ("descriptor_digest", descriptor), ("recipe_digest", recipe)):
        if report[field_name] != expected:
            errors.append(f"report {field_name.replace('_digest', '')} differs from the evidence" if field_name != "source_commit" else "report source_commit differs from the expected commit")
    policy = report["policy"] if isinstance(report["policy"], dict) else {}
    if not policy or policy_digest(policy) != report["policy_digest"]:
        errors.append("report policy digest is not the digest of its signing policy")
    errors += _validate_unsigned_and_boundaries(bundle, evidence, report, descriptor)
    platform = "windows" if target.startswith("windows") else "macos"
    errors += _validate_components(bundle, evidence, report, platform, policy)
    errors += _validate_containers(bundle, evidence, report, platform, policy)
    seal = report["seal"]
    if not (isinstance(seal, dict) and seal.get("status") == "sealed" and SHA256_RE.match(str(seal.get("sealed_inventory_sha256"))) and SHA256_RE.match(str(seal.get("carrier_sha256")))):
        errors.append("seal evidence is missing or the sealed inventory was not bound into the carrier")
    tools = report["tools"]
    for name in REQUIRED_TOOLS[target]:
        identity = tools.get(name) if isinstance(tools, dict) else None
        if not (isinstance(identity, dict) and SHA256_RE.match(str(identity.get("sha256"))) and identity.get("path")):
            errors.append(f"tool identity for {name} is missing or has no recorded sha256")
    return errors, policy


def _validate_unsigned_and_boundaries(bundle: Path, evidence: dict, report: dict, descriptor: str | None) -> list[str]:
    errors = []
    unsigned = report["unsigned"]
    if not isinstance(unsigned, dict):
        return ["unsigned section is missing"]
    a, b = unsigned.get("a_inventory_sha256"), unsigned.get("b_inventory_sha256")
    if a != b or not SHA256_RE.match(str(a)):
        errors.append("A/B unsigned inventories are not equal")
    if unsigned.get("chosen") != "A":
        errors.append("the chosen signing input is not build A")
    ab, problem = _read_indexed(bundle, evidence, str(unsigned.get("ab_report")))
    if problem:
        errors.append(f"A/B {problem}")
    elif not (isinstance(ab, dict) and ab.get("verdict") == "PASS" and not ab.get("differences") and ab.get("input_hashes") == {"first": a, "second": b}):
        errors.append("A/B equality report is not a PASS over the claimed inventories")
    observation, problem = _read_indexed(bundle, evidence, str(unsigned.get("tool_observation")))
    if problem:
        errors.append(f"tool observation: {problem}")
    else:
        errors += [f"tool observation: {e}" for e in tool_observation_problems(str(report.get("target")), observation)]
    boundaries = report["boundaries"]
    if not isinstance(boundaries, list) or [x.get("stage") if isinstance(x, dict) else None for x in boundaries] != list(STAGES):
        return errors + ["boundary stages are not verify-inputs, sign-nested, assemble, sign-outer in order"]
    previous_output = None
    for index, boundary in enumerate(boundaries):
        label = boundary["stage"]
        if set(boundary) != BOUNDARY_KEYS:
            errors.append(f"boundary {label} has unexpected fields")
            continue
        if boundary["commit"] != report["source_commit"]:
            errors.append(f"boundary {label} is for another commit")
        if boundary["descriptor_digest"] != descriptor:
            errors.append(f"boundary {label} is for another descriptor")
        if boundary["recipe_digest"] != report["recipe_digest"]:
            errors.append(f"boundary {label} was produced with another recipe")
        if boundary["credentials"] is not STAGE_CREDENTIALS[label]:
            errors.append(f"boundary {label} has the wrong credential state (credentials only in the two signing jobs)")
        if index == 0 and boundary["input_inventory_sha256"] != a:
            errors.append("signing consumed an input other than the chosen build A")
        if previous_output is not None and boundary["input_inventory_sha256"] != previous_output:
            errors.append(f"transfer into {label} does not match the previous job's output digest")
        previous_output = boundary["output_inventory_sha256"]
    return errors


def _check_correspondence(bundle: Path, evidence: dict, entry: dict, label: str, kind: str, hashes: tuple[str, str], *, entitlements_sha: str | None = None, policy_file_sha: str | None = None) -> list[str]:
    ref = entry.get("correspondence")
    if not isinstance(ref, dict):
        return [f"{label}: correspondence evidence is missing"]
    document, problem = _read_indexed(bundle, evidence, str(ref.get("report")))
    if problem:
        return [f"{label}: correspondence {problem}"]
    if not isinstance(document, dict):
        return [f"{label}: correspondence report is not an object"]
    errors = []
    if document.get("verdict") != "PASS" or document.get("differences"):
        codes = sorted({str(d.get("code")) for d in document.get("differences", []) if isinstance(d, dict)}) or [str(document.get("error", document.get("verdict")))]
        errors.append(f"{label}: correspondence report is {document.get('verdict')}: {', '.join(codes)}")
    elif ref.get("verdict") != "PASS":
        errors.append(f"{label}: correspondence verdict is not PASS")
    if document.get("kind") != kind:
        errors.append(f"{label}: correspondence report is for kind {document.get('kind')!r}, not {kind!r}")
    got = document.get("input_hashes") if isinstance(document.get("input_hashes"), dict) else {}
    keys = ("unsigned_sha256", "signed_sha256") if kind in ("pe", "macho") else ("unsigned_inventory_sha256", "signed_inventory_sha256")
    if (got.get(keys[0]), got.get(keys[1])) != hashes:
        errors.append(f"{label}: correspondence report is not about this component's bytes")
    if policy_file_sha is not None:
        applied = document.get("entitlement_policy") if isinstance(document.get("entitlement_policy"), dict) else {}
        if applied.get("source") != "explicit" or applied.get("file_sha256") != policy_file_sha:
            errors.append(f"{label}: correspondence was not run under the declared entitlements policy")
    return errors


def _validate_components(bundle: Path, evidence: dict, report: dict, platform: str, policy: dict) -> list[str]:
    errors = []
    components = report["components"]
    if not isinstance(components, list):
        return ["components are missing"]
    by_role = {c.get("role"): c for c in components if isinstance(c, dict)}
    target = report["target"]
    for role in REQUIRED_ROLES[target]:
        if role not in by_role:
            errors.append(f"required role {role} was not signed")
    for role, entry in sorted(by_role.items(), key=lambda item: str(item[0])):
        label = f"component {role}"
        spec = next((c for c in COMPONENTS[platform] if c.role == role), None)
        if spec is None:
            errors.append(f"{label} is not part of the signing plan")
            continue
        unsigned, signed = entry.get("unsigned_sha256"), entry.get("signed_sha256")
        if not SHA256_RE.match(str(unsigned)) or not SHA256_RE.match(str(signed)) or unsigned == signed:
            errors.append(f"{label}: signing did not change the unsigned bytes (or hashes are malformed)")
        signature = entry.get("signature") if isinstance(entry.get("signature"), dict) else {}
        errors += _signature_errors(label, signature, platform, policy, runtime=True)
        if platform == "macos" and spec.role == "host" and signature.get("entitlements_sha256") != policy.get("host_entitlements_sha256"):
            errors.append(f"{label}: signed entitlements are not the declared host entitlements")
        errors += _check_correspondence(
            bundle, evidence, entry, label, spec.kind, (unsigned, signed),
            policy_file_sha=policy.get("host_entitlements_file_sha256") if spec.entitled else None,
        )  # fmt: skip
    return errors


def _signature_errors(label: str, signature: dict, platform: str, policy: dict, *, runtime: bool) -> list[str]:
    errors = []
    if signature.get("verified") is not True:
        errors.append(f"{label}: signature was not verified")
    if signature.get("timestamped") is not True:
        errors.append(f"{label}: no trusted timestamp")
    expected = policy.get("publisher") if platform == "windows" else policy.get("identity")
    if not expected or signature.get("identity") != expected:
        errors.append(f"{label}: signer is not the approved publisher identity")
    if platform == "macos":
        if signature.get("team_id") != policy.get("team_id"):
            errors.append(f"{label}: signing team is not the approved team")
        if runtime and signature.get("hardened_runtime") is not True:
            errors.append(f"{label}: hardened runtime is not enabled")
    return errors


def _validate_containers(bundle: Path, evidence: dict, report: dict, platform: str, policy: dict) -> list[str]:
    containers = report["containers"]
    kind = "nsis-setup" if platform == "windows" else "dmg"
    if not isinstance(containers, list) or [c.get("kind") for c in containers if isinstance(c, dict)] != [kind]:
        return [f"container {kind} is missing or extra containers are present"]
    entry = containers[0]
    errors = []
    pre, post = entry.get("pre_sign_sha256"), entry.get("post_sign_sha256")
    if not SHA256_RE.match(str(pre)) or not SHA256_RE.match(str(post)) or pre == post:
        errors.append("container was not changed by signing (or hashes are malformed)")
    signature = entry.get("signature") if isinstance(entry.get("signature"), dict) else {}
    errors += _signature_errors(f"container {kind}", signature, platform, policy, runtime=False)
    if platform == "windows":
        if entry.get("nested_hashes_match") is not True:
            errors.append("container nested hashes were not verified against the signed-input inventory")
        errors += _check_correspondence(bundle, evidence, entry, "container nsis-setup", "pe", (pre, post))
    else:
        notarization = entry.get("notarization") if isinstance(entry.get("notarization"), dict) else {}
        if notarization.get("status") != "Accepted" or not notarization.get("submission_id"):
            errors.append("container dmg is not notarized (status is not Accepted)")
        if not (isinstance(entry.get("staple"), dict) and entry["staple"].get("validated") is True):
            errors.append("container dmg is not stapled (staple validation is missing)")
        for field_name in ("gatekeeper", "inner_app_gatekeeper"):
            if not (isinstance(entry.get(field_name), dict) and entry[field_name].get("accepted") is True):
                errors.append(f"container dmg Gatekeeper assessment ({field_name}) did not accept")
        if entry.get("contents_match") is not True:
            errors.append("container dmg contents were not verified against the signed tree")
        ref = entry.get("correspondence")
        document, problem = _read_indexed(bundle, evidence, str(ref.get("report"))) if isinstance(ref, dict) else (None, "correspondence evidence is missing")
        if problem or not (isinstance(document, dict) and document.get("verdict") == "PASS" and document.get("kind") == "tree" and not document.get("differences")):
            codes = sorted({str(d.get("code")) for d in document.get("differences", []) if isinstance(d, dict)}) if isinstance(document, dict) else []
            verdict = document.get("verdict") if isinstance(document, dict) else problem
            errors.append(f"container dmg contents correspondence is not a PASS tree comparison ({verdict}: {', '.join(codes) or 'no code'})")
    return errors


# ---------------------------------------------------------------------------
# Command line (used by the thin sign-windows.ps1 / sign-macos.sh shims and the workflow)
# ---------------------------------------------------------------------------


def _request_args(parser: argparse.ArgumentParser) -> None:
    for name in ("inputs", "output", "scratch", "reports", "descriptor", "boundary"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--recipe-digest", required=True)


def _request(args: argparse.Namespace, platform: str, stage: str) -> SignRequest:
    record = load_json(args.boundary)
    # A previous job's fragment file carries its boundary record under "boundary"; a bare record is accepted too.
    if isinstance(record, dict) and isinstance(record.get("boundary"), dict):
        record = record["boundary"]
    return SignRequest(platform, stage, args.inputs, args.output, args.scratch, args.reports, args.commit, args.descriptor, record, args.recipe_digest)


def _cmd_bundle(args: argparse.Namespace, env: dict[str, str]) -> int:
    descriptors, recipes, targets = {}, {}, {}
    seen_fragments: list[dict] = []
    work = Path(tempfile.mkdtemp(prefix="gate-compose-"))
    try:
        for platform, target in PLATFORM_TARGET.items():
            descriptors[target] = sha256_file(getattr(args, f"descriptor_{platform}"))
            recipes[target] = recipe_digest(INSTALLER_DIR, platform)
            fragments = {}
            for stage, name in (("verify-inputs", "verify-inputs"), ("sign-nested", "nested"), ("assemble", "assemble"), ("sign-outer", "outer")):
                fragments[stage] = load_json(args.reports_root / f"{platform}-{name}.json")
                if stage in ("sign-nested", "sign-outer"):
                    seen_fragments.append(fragments[stage])
            report = compose_target_report(target=target, commit=args.commit, descriptor=descriptors[target], recipe=recipes[target], fragments=fragments)
            (work / f"{target}.json").write_bytes(canonical_json(report))
            targets[target] = work / f"{target}.json"
        contexts = [fragment.get("context") for fragment in seen_fragments]
        if not contexts or any(c != contexts[0] or not isinstance(c, dict) for c in contexts):
            raise StageFailure("report-incomplete", "the credentialed signing jobs did not all run in one recorded context")
        context = contexts[0]
        # Target reports are copied next to the stage reports so they are indexed like every other file.
        staging = work / "reports"
        shutil.copytree(args.reports_root, staging)
        for path in targets.values():
            shutil.copy2(path, staging / path.name)
        build_bundle(bundle=args.bundle, target_reports={t: staging / p.name for t, p in targets.items()}, reports_root=staging, context=context, commit=args.commit, descriptors=descriptors, recipes=recipes)
    except (StageFailure, OSError, KeyError, ValueError) as error:
        print(f"bundle: BLOCKED {error}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("bundle: ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    sign = sub.add_parser("sign")
    sign.add_argument("--platform", choices=("windows", "macos"), required=True)
    sign.add_argument("--stage", choices=("nested", "outer"), required=True)
    _request_args(sign)
    asm = sub.add_parser("assemble")
    asm.add_argument("--platform", choices=("windows", "macos"), required=True)
    _request_args(asm)
    ver = sub.add_parser("verify-inputs")
    ver.add_argument("--platform", choices=("windows", "macos"), required=True)
    for name in ("a", "b", "provenance", "descriptor", "output", "reports"):
        ver.add_argument(f"--{name}", type=Path, required=True)
    ver.add_argument("--commit", required=True)
    ver.add_argument("--recipe-digest", required=True)
    sdg = sub.add_parser("descriptor-set-digest")
    sdg.add_argument("--windows-sha256", required=True)
    sdg.add_argument("--macos-sha256", required=True)
    rec = sub.add_parser("recipe-digest")
    rec.add_argument("--platform", choices=("windows", "macos"), required=True)
    bun = sub.add_parser("bundle")
    bun.add_argument("--reports-root", type=Path, required=True)
    bun.add_argument("--bundle", type=Path, required=True)
    bun.add_argument("--commit", required=True)
    for platform in ("windows", "macos"):
        bun.add_argument(f"--descriptor-{platform}", type=Path, required=True)
    val = sub.add_parser("validate")
    val.add_argument("--bundle", type=Path, required=True)
    val.add_argument("--commit", required=True)
    val.add_argument("--descriptor-digest", required=True)
    args = parser.parse_args(argv)
    env = dict(os.environ)

    if args.command == "descriptor-set-digest":
        if not (SHA256_RE.match(args.windows_sha256) and SHA256_RE.match(args.macos_sha256)):
            print("descriptor-set-digest: both digests must be lowercase sha256", file=sys.stderr)
            return 2
        print(descriptor_set_digest({"windows-x86_64": args.windows_sha256, "macos-arm64": args.macos_sha256}))
        return 0
    if args.command == "recipe-digest":
        print(recipe_digest(INSTALLER_DIR, args.platform))
        return 0
    if args.command == "bundle":
        return _cmd_bundle(args, env)
    if args.command == "validate":
        errors = validate(args.bundle, args.commit, args.descriptor_digest)
        for error in errors:
            print(f"BLOCKED: {error}")
        print("ELIGIBLE" if not errors else f"NOT ELIGIBLE: {len(errors)} defect(s)")
        return 0 if not errors else 1
    if args.command == "verify-inputs":
        outcome = verify_unsigned_inputs(
            platform=args.platform, a=args.a, b=args.b, provenance=args.provenance, descriptor_path=args.descriptor, env=env,
            expected_commit=args.commit, recipe=args.recipe_digest, output=args.output, reports=args.reports,
        )  # fmt: skip
    elif args.command == "assemble":
        outcome = assemble(_request(args, args.platform, "outer"), env, SubprocessRunner(env))
    else:
        outcome = run_stage(_request(args, args.platform, args.stage), env, SubprocessRunner(env))
    if outcome.ok:
        print(f"{args.command}: ok")
        return 0
    print(f"{args.command}: BLOCKED {outcome.code}: {outcome.detail}", file=sys.stderr)
    for failure in outcome.cleanup_failures:
        print(f"cleanup failure: {failure}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
