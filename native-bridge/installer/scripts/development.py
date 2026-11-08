#!/usr/bin/env python3
"""Development registration for the OTP-25 native bridge (Task 12).

``register-dev.{sh,ps1}`` and ``remove-dev.{sh,ps1}`` are thin wrappers around this
single implementation. Python 3.12+ standard library only.

What this is: *developer registration* of a separately compiled development host
for an unpacked extension. What it is not: distribution signature acceptance. A dev
build is never accepted as a signed release, and nothing here changes production
trust.

Command line (both commands, nothing else is accepted)::

    development.py register --extension-id <32 a-p chars> --host-binary <path>
    development.py remove   --extension-id <32 a-p chars> --host-binary <path>

There is no flag for an install root, host name, bundle ID, origin, channel,
manifest path or maintenance binary: the development channel (host name
``me.nagaev.veles.bridge.dev``, root ``NativeBridgeDev``) is selected inside the
dev-compiled maintenance engine, never by an argument, and a production binary
cannot select it at runtime.

The dev build directory (the directory holding ``--host-binary``) must contain:

* ``veles-dev-build.json`` - the build descriptor, strict JSON with exactly these
  fields: ``schema`` (1), ``channel`` ("development"), ``host_name``
  (``me.nagaev.veles.bridge.dev``), ``extension_id``, ``allowed_origins`` (exactly
  ``["chrome-extension://<extension_id>/"]``), ``host_file``, ``host_sha256``,
  ``maintenance_file``, ``maintenance_sha256``, ``package_dir``.
* the host executable, the dev-compiled maintenance engine and the dev package
  directory named by the descriptor.

"Authenticated" here means digest-bound and cross-checked, not signed: the
descriptor's SHA-256 values must equal the actual host and maintenance bytes, the
host must embed exactly the one declared ``chrome-extension://`` origin (its
compile-time allowlist) and no other, the maintenance engine must embed the dev
host name and the same origin, and the package payload must contain the same host
bytes. A mismatch is a refusal before the engine is started. Embedded-literal
scanning is a preflight, not proof of the effective allowlist (the same caveat as
the host acceptance tooling): the real evidence is Chrome launching the host.

Exit codes: 0 engine reported success, 1 refusal or engine failure, 2 invalid
invocation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePath

# The wrappers run this file with `python -I`, which does not prepend the script
# directory; make the sibling module importable explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import assemble_unsigned
from typing import Any

PRODUCTION_HOST_NAME = "me.nagaev.veles.bridge"
DEV_HOST_NAME = "me.nagaev.veles.bridge.dev"
FIXTURE_HOST_NAME = "me.nagaev.veles.bridge.fixture"
PRODUCTION_ROOT_DIR = "NativeBridge"
DEV_ROOT_DIR = "NativeBridgeDev"
PLACEHOLDER_EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"

BUILD_DESCRIPTOR_NAME = "veles-dev-build.json"
MAX_DESCRIPTOR_BYTES = 64 * 1024
MAX_BINARY_BYTES = 256 * 1024 * 1024
ENGINE_TIMEOUT_SECONDS = 300
MAX_REPORT_BYTES = 64 * 1024

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_INVALID = 2

_ID_RE = re.compile(r"^[a-p]{32}$")
# Exactly the shape the host's compile-time allowlist validates (host.rs). The bare scheme
# literal the host also carries is not an origin and is deliberately not matched.
_ORIGIN_RE = re.compile(rb"chrome-extension://[a-p]{32}/")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DESCRIPTOR_FIELDS = frozenset(
    {
        "schema",
        "channel",
        "host_name",
        "extension_id",
        "allowed_origins",
        "host_file",
        "host_sha256",
        "maintenance_file",
        "maintenance_sha256",
        "package_dir",
    }
)
_COMMANDS = frozenset({"register", "remove"})
# The approved production identity source is the SAME file the producers read: build.gradle.kts defaults
# installerReleaseConfig to config/release-<platform>.json for each producer platform. Every such file
# that exists contributes its production extension ID, which the dev commands refuse.
_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
PRODUCTION_CONFIG_NAMES = ("release-windows.json", "release-macos.json")


class Refusal(Exception):
    """A dev command was refused; the message is safe to print (no payload bytes)."""


class InvalidInvocation(Exception):
    """The argument vector is outside the closed grammar."""


def origin_for(extension_id: str) -> str:
    return f"chrome-extension://{extension_id}/"


def validate_extension_id(value: object, *, production_ids: frozenset[str] | set[str] = frozenset()) -> str:
    """One bare 32-character a-p ID that is neither the placeholder nor a production ID."""
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise Refusal("extension id must be exactly one 32-character a-p Chrome extension id")
    if value == PLACEHOLDER_EXTENSION_ID:
        raise Refusal("the repository placeholder id is not an explicit unpacked-extension id")
    if value in production_ids:
        raise Refusal("the production extension id is not accepted by development commands")
    return value


def production_extension_ids(config_dir: Path = _CONFIG_DIR) -> frozenset[str]:
    """Extension IDs every approved producer release config names (empty when none exists).

    A config that exists but cannot be read or parsed is a refusal, never an empty set: an
    unreadable approval must not turn the production ID into an acceptable dev ID.
    """
    found: set[str] = set()
    for name in PRODUCTION_CONFIG_NAMES:
        try:
            with (config_dir / name).open("rb") as handle:
                raw = handle.read(MAX_DESCRIPTOR_BYTES + 1)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise Refusal(f"approved release config {name} is unreadable") from error
        if len(raw) > MAX_DESCRIPTOR_BYTES:
            raise Refusal(f"approved release config {name} is oversized")
        try:
            config = json.loads(raw)
        except ValueError as error:
            raise Refusal(f"approved release config {name} is not valid JSON") from error
        if not isinstance(config, dict):
            raise Refusal(f"approved release config {name} must be an object")
        if config.get("channel") == "production" and isinstance(config.get("extension_id"), str):
            raw = config["extension_id"]
            try:
                resolved = assemble_unsigned.resolve_extension_id(raw)
            except ValueError as error:
                raise Refusal(f"approved release config {name} has an unreadable extension allowlist") from error
            if resolved is None:
                # A raw id is a declared production id even if the packaging allowlist
                # does not name it yet; an unknown alias or a malformed value is refused.
                if not re.fullmatch(r"[a-p]{32}", raw):
                    raise Refusal(
                        f"approved release config {name} names an extension id that is neither an allowlist alias nor a 32-character id"
                    )
                resolved = raw
            found.add(resolved)
    return frozenset(found)


def parse_args(argv: list[str]) -> tuple[str, str, str]:
    """Return ``(command, extension_id, host_binary)`` or raise ``InvalidInvocation``."""
    if not argv or argv[0] not in _COMMANDS:
        raise InvalidInvocation
    command, rest = argv[0], argv[1:]
    if len(rest) != 4:
        raise InvalidInvocation
    values: dict[str, str] = {}
    for flag, value in (rest[0:2], rest[2:4]):
        if flag not in ("--extension-id", "--host-binary") or flag in values:
            raise InvalidInvocation
        if value == "" or (flag == "--host-binary" and value.startswith("-")):
            raise InvalidInvocation
        values[flag] = value
    if set(values) != {"--extension-id", "--host-binary"}:
        raise InvalidInvocation
    return command, values["--extension-id"], values["--host-binary"]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _open_regular(path: Path, what: str) -> int:
    """Open a regular, non-symlink file read-only and return the descriptor."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise Refusal(f"{what} is missing, unreadable or a symbolic link") from error
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        raise Refusal(f"{what} is not a regular file")
    if info.st_size > MAX_BINARY_BYTES:
        os.close(descriptor)
        raise Refusal(f"{what} exceeds the size bound")
    return descriptor


def read_file(path: Path, what: str, *, limit: int = MAX_BINARY_BYTES) -> bytes:
    descriptor = _open_regular(path, what)
    with os.fdopen(descriptor, "rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise Refusal(f"{what} exceeds the size bound")
    return data


def load_descriptor(build_dir: Path) -> dict[str, Any]:
    raw = read_file(build_dir / BUILD_DESCRIPTOR_NAME, "dev build descriptor", limit=MAX_DESCRIPTOR_BYTES)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, ValueError) as error:
        raise Refusal("dev build descriptor is not strict JSON") from error
    if not isinstance(value, dict) or set(value) != _DESCRIPTOR_FIELDS:
        raise Refusal("dev build descriptor must be an object with exactly the documented fields")
    return value


def _safe_name(value: object, what: str) -> str:
    if not isinstance(value, str) or not _FILE_NAME_RE.fullmatch(value) or value in {".", ".."}:
        raise Refusal(f"{what} must be a bare file name")
    return value


def _check_not_production_root(path: Path) -> None:
    """A dev host must not live inside (or be) a production install root."""
    parts = [part for part in PurePath(os.path.normcase(str(path))).parts]
    folded = [part.casefold() for part in parts]
    target = PRODUCTION_ROOT_DIR.casefold()
    for index, part in enumerate(folded[:-1]):
        if part == target and index > 0 and folded[index - 1] == "veles":
            raise Refusal("the host binary lies inside a production install root")


def _origins_in(data: bytes) -> set[bytes]:
    return set(_ORIGIN_RE.findall(data))


def validate_dev_build(
    extension_id: str,
    host_binary: Path,
    *,
    production_ids: frozenset[str] | set[str],
    need_package: bool,
) -> tuple[Path, Path | None]:
    """Validate the dev build and return ``(maintenance_path, package_dir | None)``."""
    extension_id = validate_extension_id(extension_id, production_ids=production_ids)
    host_binary = Path(os.path.abspath(host_binary))
    _check_not_production_root(host_binary)
    build_dir = host_binary.parent
    descriptor = load_descriptor(build_dir)

    expected_origin = origin_for(extension_id)
    if descriptor["schema"] != 1 or isinstance(descriptor["schema"], bool):
        raise Refusal("unsupported dev build descriptor schema")
    if descriptor["channel"] != "development":
        raise Refusal("the build descriptor is not a development build")
    if descriptor["host_name"] != DEV_HOST_NAME:
        raise Refusal("the build descriptor names a host other than the development host")
    if descriptor["extension_id"] != extension_id:
        raise Refusal("the build descriptor was made for a different extension id")
    if descriptor["allowed_origins"] != [expected_origin]:
        raise Refusal("the build descriptor must allow exactly the one declared extension origin")

    host_file = _safe_name(descriptor["host_file"], "host_file")
    maintenance_file = _safe_name(descriptor["maintenance_file"], "maintenance_file")
    if host_file != host_binary.name:
        raise Refusal("the host binary is not the build descriptor's host file")
    package_name = _safe_name(descriptor["package_dir"], "package_dir")
    for field in ("host_sha256", "maintenance_sha256"):
        if not isinstance(descriptor[field], str) or not _SHA_RE.fullmatch(descriptor[field]):
            raise Refusal(f"{field} must be a lowercase SHA-256 digest")

    host_bytes = read_file(host_binary, "host binary")
    if hashlib.sha256(host_bytes).hexdigest() != descriptor["host_sha256"]:
        raise Refusal("the host binary does not match the build descriptor digest")
    origins = _origins_in(host_bytes)
    if origins != {expected_origin.encode()}:
        raise Refusal("the host binary's embedded origin allowlist does not equal the declared extension origin")

    maintenance_path = build_dir / maintenance_file
    maintenance_bytes = read_file(maintenance_path, "dev maintenance binary")
    if hashlib.sha256(maintenance_bytes).hexdigest() != descriptor["maintenance_sha256"]:
        raise Refusal("the dev maintenance binary does not match the build descriptor digest")
    if DEV_HOST_NAME.encode() not in maintenance_bytes:
        raise Refusal("the maintenance binary was not compiled for the development host")
    if _origins_in(maintenance_bytes) != {expected_origin.encode()}:
        raise Refusal("the maintenance binary was compiled for a different extension origin")
    if not os.access(maintenance_path, os.X_OK):
        raise Refusal("the dev maintenance binary is not executable")

    package_dir: Path | None = None
    if need_package:
        package_dir = build_dir / package_name
        if package_dir.is_symlink() or not package_dir.is_dir():
            raise Refusal("the dev package directory is missing")
        payload = package_dir / "payload" / host_file
        if hashlib.sha256(read_file(payload, "packaged host binary")).hexdigest() != descriptor["host_sha256"]:
            raise Refusal("the packaged host binary is not the descriptor's host")
        package_dir = Path(os.path.abspath(package_dir))
    return maintenance_path, package_dir


def _engine_environment() -> dict[str, str]:
    """The inherited environment minus anything that could steer the engine."""
    return {key: value for key, value in os.environ.items() if not key.upper().startswith("VELES_")}


def run_engine(maintenance: Path, arguments: list[str]) -> int:
    try:
        completed = subprocess.run(
            [str(maintenance), *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=_engine_environment(),
            timeout=ENGINE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        print("development: the dev maintenance engine could not be run", file=sys.stderr)
        return EXIT_REFUSED
    sys.stdout.write(completed.stdout[:MAX_REPORT_BYTES].decode("utf-8", errors="replace"))
    sys.stdout.flush()
    return EXIT_OK if completed.returncode == 0 else EXIT_REFUSED


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        command, extension_id, host_binary = parse_args(arguments)
    except InvalidInvocation:
        print("development: status=invalid-invocation", file=sys.stderr)
        return EXIT_INVALID
    try:
        maintenance, package = validate_dev_build(
            extension_id,
            Path(host_binary),
            production_ids=production_extension_ids(),
            need_package=command == "register",
        )
    except Refusal as refusal:
        print(f"development: refused: {refusal}", file=sys.stderr)
        return EXIT_REFUSED
    if command == "register":
        assert package is not None
        return run_engine(maintenance, ["install", "--package", str(package)])
    return run_engine(maintenance, ["uninstall"])


if __name__ == "__main__":
    sys.exit(main())
