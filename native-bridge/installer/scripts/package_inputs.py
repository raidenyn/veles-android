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
links, and anything the archive carries that the inventory cannot account for.

Archive accounting (review F7-F9): the inventory is a claim about *every byte* of the
archive, so a size field is verified, never trusted as a truncating bound.

* ZIP is parsed here, not by ``zipfile`` (which stops a member's stream at its
  *declared* length and so hides surplus content). The physical layout must be
  exactly ``[local header + data]* + central directory + end record``: first member
  at offset 0, every member starting where the previous one ended, the central
  directory ending exactly at the end record, no archive/member comment, no data
  before, between or after. Only STORED and DEFLATE are accepted (other codecs have no
  enforceable output budget here); a stored member needs ``compressed == size``; a
  deflate stream is decoded under an output budget equal to its declared size and must
  end exactly there, consume exactly its compressed extent, and match the CRC-32.
  Directory members carry no payload. Local and central records must agree. Only
  timestamps, the Unix mode and a short allowlist of timestamp/owner extra fields are
  treated as normalisable metadata.
* TAR extension records (PAX / GNU long names) are bounded and rejected *before*
  ``tarfile`` reads them; PAX keys are limited to path/link/time/owner fields (a
  ``size`` override would desynchronise the header walk) and global, sparse and
  Solaris headers are refused. ``tarfile``'s view of every member must equal the raw
  header walk, and only zero padding may follow the end marker.
* The physical archive size, the member count and the central-directory size are
  checked before the corresponding bytes are read or allocated.

Rejections carry a stable ``InventoryError.code`` (``symlink``, ``special-file``,
``unsafe-path``, ``case-folded-alias``, ``duplicate-archive-member``,
``bound-exceeded``, ``unauthenticated-data``, ...) that comparison reports reuse.
Aliases are checked on every path component, not only on whole paths, under
Unicode canonical equivalence *and* case folding: ``caf\u00e9`` and ``cafe\u0301`` (and
``\u00c9`` / ``e\u0301``) name one file on a normalisation-insensitive macOS volume, so a
Linux inventory listing both could not be reproduced there. The original spelling is
kept in the inventory; only the collision key is normalised. Bounds count every node
(files and directories): at most ``MAX_ENTRIES`` files, ``2 * MAX_ENTRIES`` nodes and
``MAX_DEPTH`` directory components above any path, for trees and archives alike.

CLI (exit 0 equal / 0 written, 1 inventories differ or a tree is rejected, 2 invalid
input; ``compare --report`` always replaces any stale report: PASS/FAIL/ERROR)::

    package_inputs.py inventory ROOT [--output FILE]
    package_inputs.py compare FIRST SECOND [--report FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import struct
import sys
import tarfile
import unicodedata
import zlib
from pathlib import Path
from typing import Any

SCHEMA = 1
MAX_ENTRIES = 4096  # model.rs MAX_INVENTORY_ENTRIES
MAX_TOTAL_BYTES = 256 * 1024 * 1024  # model.rs MAX_INVENTORY_BYTES
MAX_PATH_BYTES = 1024  # model.rs MAX_PATH_BYTES
MAX_DEPTH = 32
MAX_ARCHIVE_MEMBER_BYTES = MAX_TOTAL_BYTES
ARCHIVE_ENTRY_OVERHEAD = 4096  # per-node allowance for headers/padding when bounding the physical size
MAX_TAR_METADATA_BYTES = 4096  # one PAX / GNU long-name record (a path is at most MAX_PATH_BYTES)
MAX_TAR_METADATA_TOTAL = 4 * 1024 * 1024  # all extension records of one archive
ZIP_MAX_EXTRA = 256  # bytes of (allowlisted) extra fields per header
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


def alias_key(component_path: str) -> str:
    """Collision key: canonical decomposition + full case folding (``NFD(casefold(NFD(x)))``)."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", component_path).casefold())


def check_depth(path: str, *, directory: bool) -> None:
    """At most ``MAX_DEPTH`` directory components above the node (the node itself if it is a directory)."""
    components = path.count("/") + 1
    if (components if directory else components - 1) > MAX_DEPTH:
        raise InventoryError(f"path nested deeper than {MAX_DEPTH} directories: {path[:80]!r}", "bound-exceeded")


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
            key = alias_key(prefix)
            if key not in seen:
                seen[key] = (prefix, prefix_kind)
                continue
            spelling, existing_kind = seen[key]
            if spelling != prefix:
                raise InventoryError(f"case/Unicode-normalisation alias: {spelling!r} / {prefix!r}", "case-folded-alias")
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
    nodes = 0
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    pending: list[tuple[Path, str, int]] = [(root, "", 0)]
    while pending:
        directory, prefix, depth = pending.pop()
        if depth > MAX_DEPTH:
            raise InventoryError(f"directory nesting deeper than {MAX_DEPTH}", "bound-exceeded")
        children = []
        try:
            with os.scandir(directory) as scanner:
                for item in scanner:
                    # Counted as they stream in, so a hostile directory is refused after
                    # ~2 * MAX_ENTRIES names instead of being materialised and sorted first.
                    nodes += 1
                    if nodes > MAX_ENTRIES * 2:
                        raise InventoryError(f"more than {MAX_ENTRIES * 2} files and directories", "bound-exceeded")
                    children.append(item)
        except OSError as error:
            raise InputError(f"cannot list {directory}: {error.strerror}") from error
        children.sort(key=lambda item: item.name)
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


def _bad(message: str, code: str = "malformed-archive") -> InventoryError:
    return InventoryError(message, code)


def _read_bounded(stream, declared: int, name: str) -> str:
    """SHA-256 of exactly ``declared`` bytes; fewer or more is a malformed archive."""
    if declared < 0 or declared > MAX_ARCHIVE_MEMBER_BYTES:
        raise InventoryError(f"archive member {name!r} declares an unreasonable size", "bound-exceeded")
    digest = hashlib.sha256()
    remaining = declared
    while remaining > 0:
        chunk = stream.read(min(CHUNK, remaining))
        if not chunk:
            raise _bad(f"archive member {name!r} is shorter than declared")
        digest.update(chunk)
        remaining -= len(chunk)
    if stream.read(1):
        raise _bad(f"archive member {name!r} is longer than declared")
    return digest.hexdigest()


# ------------------------------------------------------------------------------- ZIP

ZIP_EOCD = struct.Struct("<4s4H2LH")
ZIP_CENTRAL = struct.Struct("<4s6H3L5H2L")
ZIP_LOCAL = struct.Struct("<4s5H3L2H")
ZIP_METHOD_STORED, ZIP_METHOD_DEFLATE = 0, 8
ZIP_FLAG_ENCRYPTED, ZIP_FLAG_UTF8 = 0x1, 0x800
# Timestamp / owner extra fields (UT, ux, Ux, UX, NTFS): normalisable, content-free for the inventory.
ZIP_ALLOWED_EXTRA_IDS = (0x5455, 0x7875, 0x7855, 0x5855, 0x000A)


class _ZipMember:
    __slots__ = ("name", "method", "flags", "crc", "csize", "usize", "offset", "mode", "data_offset")


def _zip_extra_ok(extra: bytes, what: str) -> None:
    """The extra block must be whole ``(id, length, data)`` records of allowlisted ids."""
    cursor = 0
    while cursor < len(extra):
        if cursor + 4 > len(extra):
            raise _bad(f"truncated extra field in {what}")
        ident, length = struct.unpack_from("<HH", extra, cursor)
        if ident not in ZIP_ALLOWED_EXTRA_IDS:
            raise _bad(f"unrecognised zip extra field {ident:#06x} in {what}")
        cursor += 4 + length
    if cursor != len(extra):
        raise _bad(f"extra field overruns its header in {what}")


def _zip_locate_end(handle, size: int) -> tuple[int, tuple]:
    if size < ZIP_EOCD.size:
        raise _bad("too small to be a zip archive")
    handle.seek(size - ZIP_EOCD.size)
    tail = handle.read(ZIP_EOCD.size)
    if tail[:4] == b"PK\x05\x06":
        record = ZIP_EOCD.unpack(tail)
        if record[7] != 0:
            raise InventoryError("zip archive comment", "unauthenticated-data")
        return size - ZIP_EOCD.size, record
    # Not at the very end: say *why* (bounded window; an end record that is followed by
    # data or carries a comment is unauthenticated content, anything else is not a zip).
    window = min(size, ZIP_EOCD.size + 0xFFFF)
    handle.seek(size - window)
    if b"PK\x05\x06" in handle.read(window):
        raise InventoryError("data after the zip end record or a zip archive comment", "unauthenticated-data")
    raise _bad("no zip end record")


def _zip_parse_central(directory: bytes, count: int) -> list[_ZipMember]:
    members: list[_ZipMember] = []
    cursor = 0
    for _ in range(count):
        if cursor + ZIP_CENTRAL.size > len(directory):
            raise _bad("central directory is shorter than its member count")
        (signature, made, need, flags, method, _time, _date, crc, csize, usize, name_len, extra_len, comment_len, disk,
         _internal, external, offset) = ZIP_CENTRAL.unpack_from(directory, cursor)
        if signature != b"PK\x01\x02":
            raise _bad("bad central directory record")
        end = cursor + ZIP_CENTRAL.size + name_len + extra_len + comment_len
        if end > len(directory) or extra_len > ZIP_MAX_EXTRA or name_len > MAX_PATH_BYTES or name_len == 0:
            raise _bad("central directory record has implausible lengths")
        if comment_len:
            raise InventoryError("zip member comment", "unauthenticated-data")
        raw_name = directory[cursor + ZIP_CENTRAL.size : cursor + ZIP_CENTRAL.size + name_len]
        _zip_extra_ok(directory[cursor + ZIP_CENTRAL.size + name_len : end], "the central directory")
        if flags & ZIP_FLAG_ENCRYPTED:
            raise InventoryError("encrypted archive member", "encrypted-member")
        if method not in (ZIP_METHOD_STORED, ZIP_METHOD_DEFLATE):
            raise InventoryError(
                f"zip compression method {method} is not supported (only stored and deflate have an enforceable output bound)",
                "unsupported-compression",
            )
        if flags & ~ZIP_FLAG_UTF8:
            raise _bad(f"unsupported zip general-purpose flags {flags:#x}")
        if need > 20 or disk != 0:
            raise _bad("zip64, multi-disk or newer-feature archives are not supported")
        if csize in (0xFFFFFFFF,) or usize in (0xFFFFFFFF,) or offset == 0xFFFFFFFF:
            raise _bad("zip64 sizes are not supported")
        member = _ZipMember()
        try:
            member.name = raw_name.decode("utf-8" if flags & ZIP_FLAG_UTF8 else "ascii")
        except UnicodeDecodeError as error:
            raise InventoryError("zip member name is not valid UTF-8 (or undeclared non-ASCII)", "unsafe-path") from error
        member.method, member.flags, member.crc = method, flags, crc
        member.csize, member.usize, member.offset = csize, usize, offset
        # Unix mode bits are only meaningful when the writer was Unix (create_system 3).
        unix_mode = external >> 16 if (made >> 8) == 3 else 0
        member.mode = unix_mode
        file_type = stat.S_IFMT(unix_mode)
        is_directory_name = member.name.endswith("/")
        if file_type == stat.S_IFLNK:
            raise InventoryError(f"symbolic link member: {member.name!r}", "symlink")
        if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise InventoryError(f"special member: {member.name!r}", "special-file")
        dos_directory = bool(external & 0x10)
        if is_directory_name:
            if file_type == stat.S_IFREG:
                raise _bad(f"directory member {member.name!r} is typed as a regular file")
            if csize or usize or crc or method != ZIP_METHOD_STORED:
                raise _bad(f"directory member {member.name!r} carries a payload")
        elif file_type == stat.S_IFDIR or dos_directory:
            raise _bad(f"member {member.name!r} is typed as a directory but is not named like one")
        if method == ZIP_METHOD_STORED and csize != usize:
            raise _bad(f"stored member {member.name!r}: compressed size {csize} != size {usize}")
        members.append(member)
        cursor = end
    if cursor != len(directory):
        raise InventoryError("bytes in the central directory beyond its members", "unauthenticated-data")
    return members


def _zip_check_local(handle, member: _ZipMember, size: int) -> int:
    """Validate the local record against the central one; returns the end of the member's data."""
    name_bytes = member.name.encode("utf-8" if member.flags & ZIP_FLAG_UTF8 else "ascii")
    handle.seek(member.offset)
    head = handle.read(ZIP_LOCAL.size)
    if len(head) != ZIP_LOCAL.size:
        raise _bad("local header is truncated")
    (signature, need, flags, method, _time, _date, crc, csize, usize, name_len, extra_len) = ZIP_LOCAL.unpack(head)
    if signature != b"PK\x03\x04":
        raise _bad("bad local header signature")
    if extra_len > ZIP_MAX_EXTRA or name_len != len(name_bytes):
        raise _bad("local header lengths disagree with the central directory")
    rest = handle.read(name_len + extra_len)
    if len(rest) != name_len + extra_len or rest[:name_len] != name_bytes:
        raise _bad("local header name disagrees with the central directory")
    if (flags, method, crc, csize, usize) != (member.flags, member.method, member.crc, member.csize, member.usize):
        raise _bad(f"local header of {member.name!r} disagrees with the central directory (or uses a data descriptor)")
    _zip_extra_ok(rest[name_len:], "a local header")
    member.data_offset = member.offset + ZIP_LOCAL.size + name_len + extra_len
    return member.data_offset + member.csize


def _hash_zip_member(handle, member: _ZipMember) -> str:
    """SHA-256 of a member's content, verified against its recorded size, CRC and extent."""
    if member.usize > MAX_ARCHIVE_MEMBER_BYTES:
        raise InventoryError(f"archive member {member.name!r} declares an unreasonable size", "bound-exceeded")
    digest = hashlib.sha256()
    crc = 0
    handle.seek(member.data_offset)
    if member.method == ZIP_METHOD_STORED:
        remaining = member.csize
        while remaining > 0:
            chunk = handle.read(min(CHUNK, remaining))
            if not chunk:
                raise _bad(f"archive member {member.name!r} is shorter than declared")
            digest.update(chunk)
            crc = zlib.crc32(chunk, crc)
            remaining -= len(chunk)
    else:
        inflater = zlib.decompressobj(-15)
        remaining_in, produced = member.csize, 0
        while True:
            pending = inflater.unconsumed_tail
            if not pending and remaining_in:
                pending = handle.read(min(CHUNK, remaining_in))
                if not pending:
                    raise _bad(f"archive member {member.name!r} is shorter than declared")
                remaining_in -= len(pending)
            # Output budget: never more than one byte past the declared size, so surplus is
            # detected without ever materialising it (max_length=0 would mean "unlimited").
            out = inflater.decompress(pending, min(CHUNK, member.usize - produced + 1))
            produced += len(out)
            if produced > member.usize:
                raise _bad(f"archive member {member.name!r} expands past its declared size {member.usize}")
            digest.update(out)
            crc = zlib.crc32(out, crc)
            if inflater.eof:
                break
            if not pending and not out and not inflater.unconsumed_tail:
                raise _bad(f"archive member {member.name!r}: deflate stream ends before its end marker")
        if inflater.unused_data or inflater.unconsumed_tail or remaining_in:
            raise _bad(f"archive member {member.name!r}: bytes follow the end of its deflate stream")
        if produced != member.usize:
            raise _bad(f"archive member {member.name!r} expands to {produced} bytes, not the declared {member.usize}")
    if crc != member.crc:
        raise _bad(f"archive member {member.name!r} does not match its recorded CRC-32")
    return digest.hexdigest()


def _scan_zip(path: Path, size: int, admit) -> None:
    with open(path, "rb") as handle:
        eocd_offset, record = _zip_locate_end(handle, size)
        (_sig, disk, cd_disk, on_disk, count, cd_size, cd_offset, _comment) = record
        if disk or cd_disk or on_disk != count or 0xFFFF in (count, on_disk) or 0xFFFFFFFF in (cd_size, cd_offset):
            raise _bad("multi-disk or zip64 archives are not supported")
        # Everything below is decided from the 22-byte end record alone, before the
        # central directory is read, so a forged count or size cannot drive an allocation.
        if count > MAX_ENTRIES * 2:
            raise InventoryError("too many archive members", "bound-exceeded")
        if cd_size < count * ZIP_CENTRAL.size or cd_size > count * (ZIP_CENTRAL.size + MAX_PATH_BYTES + ZIP_MAX_EXTRA):
            raise InventoryError("central directory size is implausible for its member count", "bound-exceeded")
        if cd_offset + cd_size != eocd_offset:
            raise InventoryError("bytes before the zip members or between the members and the end record", "unauthenticated-data")
        handle.seek(cd_offset)
        directory = handle.read(cd_size)
        if len(directory) != cd_size:
            raise _bad("central directory is truncated")
        members = _zip_parse_central(directory, count)
        declared_total = sum(member.usize for member in members)
        if declared_total > MAX_TOTAL_BYTES or any(member.usize > MAX_ARCHIVE_MEMBER_BYTES for member in members):
            raise InventoryError(f"archive larger than {MAX_TOTAL_BYTES} bytes", "bound-exceeded")
        # Extent map: members are contiguous from offset 0 to the central directory.
        cursor = 0
        for member in sorted(members, key=lambda item: item.offset):
            if member.offset > cursor:
                raise InventoryError("bytes before or between zip members that no record explains", "unauthenticated-data")
            if member.offset < cursor:
                raise _bad(f"zip member {member.name!r} overlaps the previous member")
            cursor = _zip_check_local(handle, member, size)
        if cursor != cd_offset:
            raise InventoryError("bytes between the last zip member and the central directory", "unauthenticated-data")
        for member in members:
            is_directory = member.name.endswith("/")
            admit(
                member.name,
                member.usize,
                lambda m=member: _hash_zip_member(handle, m),
                bool(member.mode & 0o111) and not is_directory,
            )


# ------------------------------------------------------------------------------- TAR

TAR_BLOCK = 512
TAR_PAX_KEYS = ("path", "linkpath", "mtime", "atime", "ctime", "uid", "gid", "uname", "gname")
TAR_EXTENSION_TYPES = (tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)
TAR_REFUSED_TYPES = (tarfile.XGLTYPE, tarfile.GNUTYPE_SPARSE, tarfile.SOLARIS_XHDTYPE)


def _tar_block(size: int) -> int:
    return (size + TAR_BLOCK - 1) // TAR_BLOCK * TAR_BLOCK


def _tar_check_padding(handle, data_offset: int, size: int, physical_size: int) -> None:
    """The bytes between a record's data and the next 512-byte boundary must exist and be zero.

    ``tarfile`` skips them unread, so anything stored there would be carried by the archive without
    entering the inventory (review R10). Fewer than 512 bytes, so reading them is always bounded.
    """
    start = data_offset + size
    count = _tar_block(size) - size
    if not count:
        return
    if start + count > physical_size:
        raise _bad("tar record padding is truncated")
    handle.seek(start)
    padding = handle.read(count)
    if len(padding) != count:
        raise _bad("tar record padding is truncated")
    if padding.strip(b"\x00"):
        raise InventoryError("non-zero tar record padding", "unauthenticated-data")


def _check_pax_records(body: bytes) -> None:
    """Strict ``"<len> <key>=<value>\\n"`` records limited to keys that cannot move a boundary."""
    cursor = 0
    while cursor < len(body):
        space = body.find(b" ", cursor, cursor + 8)
        if space <= cursor or not body[cursor:space].isdigit():
            raise _bad("malformed PAX record length")
        length = int(body[cursor:space])
        end = cursor + length
        if length <= space - cursor + 1 or end > len(body) or body[end - 1 : end] != b"\n":
            raise _bad("malformed PAX record")
        key, equals, _value = body[space + 1 : end - 1].partition(b"=")
        if not equals or key.decode("utf-8", "replace") not in TAR_PAX_KEYS:
            raise _bad(f"PAX key {key[:32]!r} is not accepted (only path/link/time/owner fields)")
        cursor = end


def _tar_preflight(path: Path, physical_size: int) -> tuple[list[tuple[int, int, int]], int]:
    """Walk the raw 512-byte headers before ``tarfile`` allocates for any of them.

    Returns ``[(first_header_offset, data_offset, size), ...]`` for every payload header
    and the offset of the end marker. Extension records are size-checked before being read.
    """
    records: list[tuple[int, int, int]] = []
    metadata_total = 0
    blocks = 0
    offset = 0
    start: int | None = None
    with open(path, "rb") as handle:
        while True:
            blocks += 1
            if blocks > 4 * MAX_ENTRIES + 8 or len(records) > MAX_ENTRIES * 2:
                raise InventoryError("too many archive members", "bound-exceeded")
            handle.seek(offset)
            block = handle.read(TAR_BLOCK)
            if not block:
                if offset == 0:
                    raise _bad("empty tar file")
                return records, offset
            if block.count(b"\x00") == TAR_BLOCK:
                return records, offset
            info = tarfile.TarInfo.frombuf(block, "utf-8", "surrogateescape")
            if info.size < 0:  # checked before any accounting, boundary arithmetic or read can use it
                raise _bad(f"tar header declares a negative size ({info.size})")
            if info.type in TAR_REFUSED_TYPES:
                raise _bad(f"tar header type {info.type!r} (global, sparse or Solaris extension) is not accepted")
            if start is None:
                start = offset
            if info.type in TAR_EXTENSION_TYPES:
                if info.size > MAX_TAR_METADATA_BYTES:
                    raise InventoryError(f"tar extension record of {info.size} bytes", "bound-exceeded")
                metadata_total += info.size
                if metadata_total > MAX_TAR_METADATA_TOTAL:
                    raise InventoryError("tar extension records exceed the metadata budget", "bound-exceeded")
                if offset + TAR_BLOCK + info.size > physical_size:
                    raise _bad("tar extension record is truncated")
                if info.type == tarfile.XHDTYPE:
                    body = handle.read(info.size)
                    if len(body) != info.size:
                        raise _bad("tar extension record is truncated")
                    _check_pax_records(body)
                _tar_check_padding(handle, offset + TAR_BLOCK, info.size, physical_size)
                offset += TAR_BLOCK + _tar_block(info.size)
                continue
            data_offset = offset + TAR_BLOCK
            # Mirror tarfile: data follows regular (and unknown) types only.
            skips_data = info.isreg() or info.type not in tarfile.SUPPORTED_TYPES
            if skips_data and data_offset + info.size > physical_size:
                raise _bad("tar member data is truncated")
            if skips_data:
                _tar_check_padding(handle, data_offset, info.size, physical_size)
            records.append((start, data_offset, info.size))
            start = None
            offset = data_offset + (_tar_block(info.size) if skips_data else 0)


def _scan_tar(path: Path, physical_size: int, admit) -> None:
    expected, end_offset = _tar_preflight(path, physical_size)
    with tarfile.open(path, "r:") as archive:
        members = []
        for member in archive:
            members.append(member)
            if len(members) > len(expected):
                raise _bad("tarfile sees more members than the header walk")
        seen = [(member.offset, member.offset_data, member.size) for member in members]
        if seen != expected or archive.offset != end_offset:
            raise _bad("tarfile's view of the archive disagrees with the raw header walk")
        for member in members:
            if member.isdir():
                if member.size:
                    raise _bad(f"directory member {member.name!r} carries a payload")
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
    _require_zero_tail(path, end_offset)


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
    # The physical size is bounded before a single byte is read: payload cap plus a
    # per-node allowance for headers and padding.
    if info.st_size > MAX_TOTAL_BYTES + 2 * MAX_ENTRIES * ARCHIVE_ENTRY_OVERHEAD:
        raise InventoryError(f"archive larger than {MAX_TOTAL_BYTES} bytes plus header allowance", "bound-exceeded")
    entries: list[dict] = []
    empty_directories: list[str] = []
    directories: list[str] = []
    seen: set[str] = set()  # explicit members, for duplicate detection
    nodes: set[str] = set()  # every distinct path: explicit members *and* the parents they imply
    total = 0

    def admit(name: str, size: int, digest_reader, executable: bool) -> None:
        nonlocal total
        directory = name.endswith("/")
        normalized = normalize_relative_path(name.rstrip("/"))
        check_depth(normalized, directory=directory)
        if normalized in seen:
            raise InventoryError(f"duplicate archive member: {normalized!r}", "duplicate-archive-member")
        # The same node bound a directory walk enforces: an archive that omits its directory records
        # describes the same tree, so its implied parents (counted once, never again when also explicit) count.
        # Decided here, before the alias/prefix structures of ``_finish`` are built (depth is bounded).
        nodes.update((*_parents(normalized), normalized))
        if len(nodes) > MAX_ENTRIES * 2:
            raise InventoryError(f"more than {MAX_ENTRIES * 2} files and directories", "bound-exceeded")
        seen.add(normalized)
        if directory:
            directories.append(normalized + "/")
            return
        if len(entries) >= MAX_ENTRIES:
            raise InventoryError(f"more than {MAX_ENTRIES} archive members", "bound-exceeded")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise InventoryError(f"archive larger than {MAX_TOTAL_BYTES} bytes", "bound-exceeded")
        entries.append({"path": normalized, "size": size, "sha256": digest_reader(), "executable": executable})

    try:
        if suffix == ".zip":
            _scan_zip(path, info.st_size, admit)
        else:
            _scan_tar(path, info.st_size, admit)
    except (tarfile.TarError, OSError, EOFError, RuntimeError, zlib.error, struct.error) as error:
        raise InventoryError(f"malformed archive {path.name}: {error}", "malformed-archive") from error
    # A directory is "empty" only when nothing lies below it, whether the descendant is a
    # file or another directory member: ``a/`` + ``a/b/`` describes the same tree as ``a/b/``.
    populated = {parent for entry in entries for parent in _parents(entry["path"])}
    populated |= {parent for directory in directories for parent in _parents(directory.rstrip("/"))}
    empty_directories = [d for d in directories if d.rstrip("/") not in populated]
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
