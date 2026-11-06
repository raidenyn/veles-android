#!/usr/bin/env python3
"""Deterministic, checkout-path independent input inventories (OTP-25 Task 9).

Python 3.12+ standard library only; no network, no subprocess, no platform tools.

``build_inventory(root)`` turns a clean input tree into a canonical dictionary
that two independent clean builds can be compared on byte for byte::

    {"schema": 1,
     "entries": [{"path", "size", "sha256", "executable"}, ...],   # sorted by path
     "empty_directories": ["dir/", ...],                           # sorted
     "inventory_sha256": "<hex of the canonical JSON of the two keys above>"}

The entry shape is the Rust ``InventoryEntry`` (path, size, SHA-256, executable);
the path policy is the one ``src/inventory.rs::normalize_relative_path`` enforces.
Nothing host-specific enters an inventory: not the absolute root, not mtimes or
ownership, not permission bits other than "any execute bit set". Symlinks and
non-regular files, case-folded duplicates, unsafe names and anything past the
entry/size/depth bounds are rejected (``InventoryError``), never skipped.

``build_archive_inventory(path)`` produces the same dictionary for a ``.zip`` or
``.tar`` payload without extracting it, rejecting duplicate members, traversal,
links and decompression beyond the declared sizes.

Rejections carry a stable ``InventoryError.code`` (``symlink``, ``special-file``,
``unsafe-path``, ``case-folded-alias``, ``duplicate-archive-member``,
``bound-exceeded``, ``unauthenticated-data``, ...) that comparison reports reuse.
Case aliases are checked on every path component, not only on whole paths.

CLI (exit 0 equal / 0 written, 1 inventories differ or a tree is rejected, 2 invalid
input; ``compare --report`` always replaces any stale report: PASS/FAIL/ERROR)::

    package_inputs.py inventory ROOT [--output FILE]
    package_inputs.py compare FIRST SECOND [--report FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
import stat
import sys
import tarfile
import zipfile
import zlib
from pathlib import Path
from typing import Any

SCHEMA = 1
MAX_ENTRIES = 4096  # model.rs MAX_INVENTORY_ENTRIES
MAX_TOTAL_BYTES = 256 * 1024 * 1024  # model.rs MAX_INVENTORY_BYTES
MAX_PATH_BYTES = 1024  # model.rs MAX_PATH_BYTES
MAX_DEPTH = 32
MAX_ARCHIVE_MEMBER_BYTES = MAX_TOTAL_BYTES
CHUNK = 64 * 1024


class InputError(Exception):
    """The caller supplied something unusable (missing/unreadable root, bad option).

    CLI exit code 2: this is "invalid input or environment", never a comparison
    verdict.
    """


class InventoryError(ValueError):
    """The tree or archive itself is not an acceptable deterministic input.

    A defect in well-formed input (symlink, alias name, duplicate member, bound
    exceeded). Callers turn this into a FAILED comparison, never into a pass.
    ``code`` is the stable machine-readable reason the comparison reports use:
    ``symlink``, ``special-file``, ``unsafe-path``, ``case-folded-alias``,
    ``duplicate-archive-member``, ``bound-exceeded``, ``encrypted-member``,
    ``unauthenticated-data``, ``malformed-archive`` or ``changed-during-inventory``.
    """

    def __init__(self, message: str, code: str = "inventory-rejected") -> None:
        super().__init__(message)
        self.code = code


def canonical_json(value: Any) -> bytes:
    """Sorted-key compact UTF-8 JSON with a trailing newline (the Rust encoding)."""
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def normalize_relative_path(path: str) -> str:
    """Python mirror of ``normalize_relative_path`` in ``src/inventory.rs``."""
    if not path or len(path.encode("utf-8", "surrogatepass")) > MAX_PATH_BYTES:
        raise InventoryError(f"path empty or longer than {MAX_PATH_BYTES} bytes", "unsafe-path")
    if path.startswith("/") or "\\" in path or ":" in path or any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        raise InventoryError(f"unsafe path: {path!r}", "unsafe-path")
    try:
        path.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InventoryError(f"path is not valid UTF-8: {path!r}", "unsafe-path") from error
    for component in path.split("/"):
        if component in ("", ".", "..") or component.endswith((".", " ")):
            raise InventoryError(f"unsafe path component in {path!r}", "unsafe-path")
    return path


def _finish(entries: list[dict], empty_directories: list[str]) -> dict:
    entries.sort(key=lambda entry: entry["path"].encode("utf-8"))
    empty_directories.sort(key=lambda value: value.encode("utf-8"))
    # Every path component is checked, not just whole paths: ``docs/a`` next to
    # ``Docs/b`` collides on a case-insensitive checkout even though no two *file*
    # paths fold together. Each folded prefix maps to exactly one spelling and kind.
    seen: dict[str, tuple[str, str]] = {}
    listed = [(entry["path"], "file") for entry in entries] + [(d.rstrip("/"), "directory") for d in empty_directories]
    for name, kind in listed:
        parts = name.split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            prefix_kind = kind if index == len(parts) else "directory"
            key = prefix.casefold()
            if key not in seen:
                seen[key] = (prefix, prefix_kind)
                continue
            spelling, existing_kind = seen[key]
            if spelling != prefix:
                raise InventoryError(f"case-folded alias: {spelling!r} / {prefix!r}", "case-folded-alias")
            if existing_kind != prefix_kind:
                raise InventoryError(f"{prefix!r} is both a file and a directory", "duplicate-archive-member")
            if index == len(parts):
                raise InventoryError(f"duplicate path: {prefix!r}", "duplicate-archive-member")
    body = {"schema": SCHEMA, "entries": entries, "empty_directories": empty_directories}
    return {**body, "inventory_sha256": hashlib.sha256(canonical_json(body)).hexdigest()}


def _hash_open_file(fd: int, expected_size: int) -> str:
    digest = hashlib.sha256()
    remaining = expected_size
    with os.fdopen(fd, "rb", closefd=True) as handle:
        while remaining > 0:
            chunk = handle.read(min(CHUNK, remaining))
            if not chunk:
                raise InventoryError("file shrank while hashing", "changed-during-inventory")
            digest.update(chunk)
            remaining -= len(chunk)
        if handle.read(1):
            raise InventoryError("file grew while hashing", "changed-during-inventory")
    return digest.hexdigest()


def build_inventory(root: Path) -> dict:
    """Inventory of the regular files below ``root`` (see module docstring)."""
    root = Path(root)
    try:
        root_stat = os.lstat(root)
    except OSError as error:
        raise InputError(f"cannot read input root {root}: {error.strerror}") from error
    if not stat.S_ISDIR(root_stat.st_mode):
        raise InputError(f"input root is not a directory: {root}")

    entries: list[dict] = []
    empty_directories: list[str] = []
    total = 0
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    pending: list[tuple[Path, str, int]] = [(root, "", 0)]
    while pending:
        directory, prefix, depth = pending.pop()
        if depth > MAX_DEPTH:
            raise InventoryError(f"directory nesting deeper than {MAX_DEPTH}", "bound-exceeded")
        try:
            with os.scandir(directory) as scanner:
                children = sorted(scanner, key=lambda item: item.name)
        except OSError as error:
            raise InputError(f"cannot list {directory}: {error.strerror}") from error
        if not children and prefix:
            empty_directories.append(normalize_relative_path(prefix.rstrip("/")) + "/")
        for child in children:
            relative = prefix + child.name
            normalize_relative_path(relative)
            info = child.stat(follow_symlinks=False)
            mode = info.st_mode
            if stat.S_ISLNK(mode):
                raise InventoryError(f"symbolic link in input tree: {relative}", "symlink")
            if stat.S_ISDIR(mode):
                pending.append((Path(child.path), relative + "/", depth + 1))
                continue
            if not stat.S_ISREG(mode):
                raise InventoryError(f"non-regular file in input tree: {relative}", "special-file")
            if len(entries) >= MAX_ENTRIES:
                raise InventoryError(f"more than {MAX_ENTRIES} entries", "bound-exceeded")
            total += info.st_size
            if total > MAX_TOTAL_BYTES:
                raise InventoryError(f"inventory larger than {MAX_TOTAL_BYTES} bytes", "bound-exceeded")
            try:
                fd = os.open(child.path, flags)
            except OSError as error:
                raise InventoryError(f"cannot open {relative} without following links: {error.strerror}", "symlink") from error
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != info.st_size:
                os.close(fd)
                raise InventoryError(f"{relative} changed while being inventoried", "changed-during-inventory")
            entries.append(
                {
                    "path": relative,
                    "size": opened.st_size,
                    "sha256": _hash_open_file(fd, opened.st_size),
                    "executable": bool(mode & 0o111),
                }
            )
    return _finish(entries, empty_directories)


def _read_bounded(stream, declared: int, name: str) -> str:
    if declared < 0 or declared > MAX_ARCHIVE_MEMBER_BYTES:
        raise InventoryError(f"archive member {name!r} declares an unreasonable size", "bound-exceeded")
    digest = hashlib.sha256()
    remaining = declared
    while remaining > 0:
        chunk = stream.read(min(CHUNK, remaining))
        if not chunk:
            raise InventoryError(f"archive member {name!r} is shorter than declared", "malformed-archive")
        digest.update(chunk)
        remaining -= len(chunk)
    if stream.read(1):
        raise InventoryError(f"archive member {name!r} is longer than declared", "malformed-archive")
    return digest.hexdigest()


def _hash_zip_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> str:
    # Closed on every path, including a read that raises mid-stream.
    with archive.open(member) as stream:
        return _read_bounded(stream, member.file_size, member.filename)


def build_archive_inventory(path: Path) -> dict:
    """Inventory a ``.zip`` or ``.tar`` payload without extracting it."""
    path = Path(path)
    try:
        info = os.lstat(path)
    except OSError as error:
        raise InputError(f"cannot read archive {path}: {error.strerror}") from error
    if not stat.S_ISREG(info.st_mode):
        raise InputError(f"archive is not a regular file: {path}")
    suffix = path.suffix.lower()
    if suffix not in (".zip", ".tar"):
        raise InputError(f"unsupported archive type {suffix!r} (expected .zip or .tar)")
    entries: list[dict] = []
    empty_directories: list[str] = []
    seen: set[str] = set()
    total = 0

    def admit(name: str, size: int, digest_reader, executable: bool) -> None:
        nonlocal total
        directory = name.endswith("/")
        normalized = normalize_relative_path(name.rstrip("/"))
        if normalized in seen:
            raise InventoryError(f"duplicate archive member: {normalized!r}", "duplicate-archive-member")
        seen.add(normalized)
        if directory:
            empty_directories.append(normalized + "/")
            return
        if len(entries) >= MAX_ENTRIES:
            raise InventoryError(f"more than {MAX_ENTRIES} archive members", "bound-exceeded")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise InventoryError(f"archive larger than {MAX_TOTAL_BYTES} bytes", "bound-exceeded")
        entries.append({"path": normalized, "size": size, "sha256": digest_reader(), "executable": executable})

    try:
        if suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) > MAX_ENTRIES * 2:
                    raise InventoryError("too many archive members", "bound-exceeded")
                if members and min(member.header_offset for member in members) != 0:
                    # zipfile silently tolerates a prefix (self-extractor style); that
                    # prefix is unauthenticated content the inventory would never see.
                    raise InventoryError("data before the first archive member", "unauthenticated-data")
                for member in members:
                    # Unix mode bits are only meaningful when the writer was Unix (create_system 3).
                    unix_mode = member.external_attr >> 16 if member.create_system == 3 else 0
                    if member.flag_bits & 0x1:
                        raise InventoryError(f"encrypted archive member: {member.filename!r}", "encrypted-member")
                    # Many writers (including ``ZipFile.writestr``) store permission bits with no
                    # file-type bits; only an explicit non-regular type is a link or special file.
                    file_type = stat.S_IFMT(unix_mode)
                    if file_type == stat.S_IFLNK:
                        raise InventoryError(f"symbolic link member: {member.filename!r}", "symlink")
                    if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                        raise InventoryError(f"special member: {member.filename!r}", "special-file")
                    admit(
                        member.filename,
                        member.file_size,
                        lambda m=member: _hash_zip_member(archive, m),
                        bool(unix_mode & 0o111) and not member.is_dir(),
                    )
        else:
            with tarfile.open(path, "r:") as archive:
                for count, member in enumerate(archive):
                    if count > MAX_ENTRIES * 2:
                        raise InventoryError("too many archive members", "bound-exceeded")
                    if member.isdir():
                        admit(member.name.rstrip("/") + "/", 0, lambda: "", False)
                    elif member.isreg():
                        admit(
                            member.name,
                            member.size,
                            lambda m=member: _read_bounded(archive.extractfile(m), m.size, m.name),
                            bool(member.mode & 0o111),
                        )
                    elif member.issym() or member.islnk():
                        raise InventoryError(f"link member: {member.name!r}", "symlink")
                    else:
                        raise InventoryError(f"special member: {member.name!r}", "special-file")
                _require_zero_tail(path, archive.offset)
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError, RuntimeError, zlib.error, lzma.LZMAError) as error:
        raise InventoryError(f"malformed archive {path.name}: {error}", "malformed-archive") from error
    # Directory members that actually contain files are not "empty".
    populated = {parent for entry in entries for parent in _parents(entry["path"])}
    empty_directories = [d for d in empty_directories if d.rstrip("/") not in populated]
    return _finish(entries, empty_directories)


def _require_zero_tail(path: Path, offset: int) -> None:
    """Everything after the tar end-of-archive marker must be zero padding.

    ``tarfile`` stops at the first zero block and ignores the rest, so a second
    payload could ride behind the marker without ever entering the inventory.
    """
    with open(path, "rb") as handle:
        handle.seek(offset)
        while chunk := handle.read(CHUNK):
            if chunk.strip(b"\x00"):
                raise InventoryError("non-zero data after the end-of-archive marker", "unauthenticated-data")


def _parents(path: str):
    parts = path.split("/")[:-1]
    for index in range(1, len(parts) + 1):
        yield "/".join(parts[:index])


def compare_inventories(first: dict, second: dict) -> list[dict]:
    """Exact differences between two inventories; empty list means identical."""
    a = {entry["path"]: entry for entry in first["entries"]}
    b = {entry["path"]: entry for entry in second["entries"]}
    out: list[dict] = []
    for path in sorted(set(a) | set(b)):
        if path not in b:
            out.append({"code": "only_in_first", "path": path})
        elif path not in a:
            out.append({"code": "only_in_second", "path": path})
        else:
            left, right = a[path], b[path]
            if left["sha256"] != right["sha256"] or left["size"] != right["size"]:
                out.append({"code": "content", "path": path, "first": left["sha256"], "second": right["sha256"]})
            if left["executable"] != right["executable"]:
                out.append({"code": "executable", "path": path, "first": left["executable"], "second": right["executable"]})
    da, db = set(first["empty_directories"]), set(second["empty_directories"])
    for path in sorted(da - db):
        out.append({"code": "only_in_first", "path": path})
    for path in sorted(db - da):
        out.append({"code": "only_in_second", "path": path})
    return out


def _load(path: Path) -> dict:
    return build_archive_inventory(path) if Path(path).is_file() else build_inventory(path)


def _write(path: Path | None, data: bytes) -> None:
    if path is None:
        sys.stdout.buffer.write(data)
        return
    if not path.parent.is_dir() or path.is_dir():
        raise InputError(f"cannot write {path}")
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    except OSError as error:
        raise InputError(f"cannot write {path}: {error.strerror}") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    inventory = sub.add_parser("inventory")
    inventory.add_argument("root", type=Path)
    inventory.add_argument("--output", type=Path)
    compare = sub.add_parser("compare")
    compare.add_argument("first", type=Path)
    compare.add_argument("second", type=Path)
    compare.add_argument("--report", type=Path)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        return 2 if exit_.code else 0
    try:
        if args.command == "inventory":
            _write(args.output, canonical_json(_load(args.root)))
            return 0
        report = _compare_report(args.first, args.second)
        if args.report is not None:
            _write(args.report, canonical_json(report))
        return 0 if report["verdict"] == "PASS" else 1
    except InputError as error:
        print(f"package_inputs: invalid input: {error}", file=sys.stderr)
        if args.command == "compare":
            _write_error_report(args.report, str(error))
        return 2
    except InventoryError as error:
        print(f"package_inputs: unacceptable input: {error}", file=sys.stderr)
        return 1


def _compare_report(first_path: Path, second_path: Path) -> dict:
    """The comparison report; a rejected tree is a FAIL document, never a stale/absent one."""
    inventories: dict[str, dict] = {}
    rejections: list[dict] = []
    for side, path in (("first", first_path), ("second", second_path)):
        try:
            inventories[side] = _load(path)
        except InventoryError as error:
            rejections.append({"code": error.code, "side": side, "message": str(error)})
    if rejections:
        return {"schema": SCHEMA, "verdict": "FAIL", "differences": rejections, "input_hashes": {}}
    differences = compare_inventories(inventories["first"], inventories["second"])
    return {
        "schema": SCHEMA,
        "verdict": "PASS" if not differences else "FAIL",
        "differences": differences,
        "input_hashes": {
            "first": inventories["first"]["inventory_sha256"],
            "second": inventories["second"]["inventory_sha256"],
        },
    }


def _write_error_report(path: Path | None, message: str) -> None:
    """Replace any stale report with an explicit ERROR document (best effort)."""
    if path is None:
        return
    try:
        _write(path, canonical_json({"schema": SCHEMA, "verdict": "ERROR", "error": message}))
    except InputError:
        pass


if __name__ == "__main__":
    sys.exit(main())
