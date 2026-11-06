#!/usr/bin/env python3
"""Signed-content correspondence for the OTP-25 standalone installers (Task 9).

Python 3.12+ standard library only; no network, no subprocess, no platform tools.

``compare(unsigned, signed, kind)`` decides whether ``signed`` is the ``unsigned``
input plus *only* the mutations a code-signing step is documented to make::

    {"schema": 1, "verdict": "PASS" | "FAIL", "kind": ...,
     "differences": [...],            # anything not allowed; empty exactly on PASS
     "allowed_differences": [...],    # the signature-only mutations that were accounted for
     "input_hashes": {...}}

It is *not* a blind "strip the signature, then compare everything" comparison and
it ignores no directory, extension or "loadable only" subset: every byte of each
input is either proven identical, or is part of one named, structurally validated
signature mutation. Anything else (including parser doubt) is a ``differences``
entry and a FAIL.

Allowlists (each exclusion is a signature container, never payload):

* ``pe`` - the optional-header ``CheckSum`` field; the security data-directory
  entry; zero bytes padding the unsigned image up to the next 8-byte boundary;
  one terminal table of WIN_CERTIFICATE records that the security directory exactly
  bounds (revision 2.0, PKCS#7 type, DER ``SEQUENCE`` body). Headers, the section
  table, every section's raw data (resources live in ``.rsrc``), inter-section
  gaps and the overlay must be byte-identical.
* ``macho`` - the ``LC_CODE_SIGNATURE`` load command (added, or replaced when the
  linker already ad-hoc signed), the header ``ncmds``/``sizeofcmds`` that command
  implies, the ``__LINKEDIT`` ``vmsize``/``filesize`` growth that exactly covers
  the signature, zero alignment padding in front of the blob and the embedded
  signature SuperBlob itself (validated: CodeDirectory page hashes recomputed over
  the signed bytes, special-slot hashes, entitlements). The architecture/slice
  set, every other load command, every section, all other ``__LINKEDIT`` data and
  the UUID must be byte-identical.
* ``tree`` - ``Contents/_CodeSignature/CodeResources`` (the seal) and
  ``Contents/CodeResources`` (a stapled ticket, only beside a seal); every other
  path must exist on both sides with identical content and execute bit, and every
  PE/Mach-O image found by content is delegated to the matching reader above.
  Inputs are directories or ``.zip``/``.tar`` payloads, inventoried by
  ``package_inputs`` (symlinks, duplicate members, aliases and bound violations
  FAIL). DMG/NSIS containers are verified as a signed container plus an extracted
  payload inventory: extract the payload and pass it here as a tree.

What this tool does **not** do: verify any cryptographic signature, certificate
chain, timestamp, notarization ticket or seal contents. Those are verified
separately by the native tools (``signtool verify``, ``codesign --verify``,
``stapler validate``, ``spctl``) on the native runners; every allowed entry
therefore records ``"verify_separately": true`` where a trust decision remains.

Report fields (``schema`` 1). Every report has ``verdict``, ``kind``, ``differences``,
``allowed_differences`` (empty unless PASS) and ``input_hashes``; the keys of
``input_hashes`` depend on the kind:

* ``pe`` - ``unsigned_sha256``, ``signed_sha256`` (whole files), ``unsigned_invariant_sha256``,
  ``signed_invariant_sha256`` (the file with the checksum field, security directory and certificate
  table removed) and ``signed_matches_invariant``.
* ``macho`` - ``unsigned_sha256``, ``signed_sha256`` and ``signed_matches_invariant``. The report also
  carries ``slices`` (index plus full architecture identity: cputype, cpusubtype, fat alignment; entries
  of ``allowed_differences`` for a fat slice repeat that ``architecture``).
* ``tree`` - ``unsigned_inventory_sha256``, ``signed_inventory_sha256`` and, for archive inputs, the
  archive file's own ``unsigned_sha256`` / ``signed_sha256``.

``macho`` and ``tree`` reports also carry ``entitlement_policy``: ``source`` (``explicit`` or
``inherited-from-unsigned``), ``file_sha256`` of the policy file and ``canonical_sha256`` of its
type-preserving canonical form (both ``null`` when inherited), so a release gate can see which policy
applied. ``signed_matches_invariant`` is structural, never a cryptographic trust statement.

Native trust obligations that this tool leaves open (``"verify_separately": true`` / blob entries with
``"cryptographic_signature_verified": false``): ``signtool verify`` of the Authenticode chain and
timestamp, ``codesign --verify`` of the signature and seal, ``stapler validate`` of tickets and
``spctl`` assessment. A PASS here plus those checks is the release evidence; either alone is not.
A DER entitlements slot is refused outright (``macho-der-entitlements-unsupported``) until it can be decoded.

Exit codes of the CLI: 0 verdict PASS, 1 verdict FAIL (readable but unexpected
content), 2 invalid input or environment (missing/unreadable file, unsupported
``--kind``, not a PE/Mach-O/archive at all, bad arguments, I/O error). Exit 2
replaces any stale report with ``{"verdict": "ERROR"}``; success is never reported
when the comparison could not be performed.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import plistlib
import stat
import struct
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

try:  # normal import (tests, `python -m`)
    import package_inputs
except ImportError:  # `python3 -I correspondence.py` puts no script directory on sys.path
    _spec = importlib.util.spec_from_file_location("package_inputs", Path(__file__).resolve().with_name("package_inputs.py"))
    package_inputs = importlib.util.module_from_spec(_spec)
    sys.modules["package_inputs"] = package_inputs
    _spec.loader.exec_module(package_inputs)

SCHEMA = 1
KINDS = ("pe", "macho", "tree")

# One class on purpose: a tree input that package_inputs refuses to read is the same
# "invalid input" a missing PE is, so callers catch a single type.
InputError = package_inputs.InputError

MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_SECTIONS = 96  # PE format limit
MAX_CERTIFICATE_TABLE = 16 * 1024 * 1024
MAX_CERTIFICATES = 64  # a table of 8-byte records must not buy a million hash iterations
MAX_LOAD_COMMANDS = 4096
MAX_LOAD_COMMAND_BYTES = 1 << 20
MAX_FAT_ARCHES = 8
MAX_SUPERBLOB_BLOBS = 32
MAX_ENTITLEMENTS_BYTES = 1 << 20


class Reject(Exception):
    """Readable input whose structure cannot be proven acceptable (a FAIL, not exit 2)."""

    def __init__(self, code: str, message: str, region: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.region = region

    def difference(self, side: str | None = None, **extra: Any) -> dict:
        item: dict[str, Any] = {"code": self.code, "message": str(self)}
        if side is not None:
            item["side"] = side
        if self.region is not None:
            item["region"] = self.region
        item.update(extra)
        return item


def difference(code: str, message: str, **extra: Any) -> dict:
    return {"code": code, "message": message, **extra}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def align_up(value: int, boundary: int) -> int:
    return (value + boundary - 1) // boundary * boundary


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def read_regular_file(path: Path) -> bytes:
    """Bounded whole-file read of a regular, non-symlink file."""
    try:
        info = os.lstat(path)
    except OSError as error:
        raise InputError(f"cannot read {path}: {error.strerror}") from error
    if not stat.S_ISREG(info.st_mode):
        raise InputError(f"not a regular file: {path}")
    if info.st_size > MAX_FILE_BYTES:
        raise InputError(f"{path} is larger than {MAX_FILE_BYTES} bytes")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError as error:
        raise InputError(f"cannot read {path}: {error.strerror}") from error
    if len(data) > MAX_FILE_BYTES:
        raise InputError(f"{path} is larger than {MAX_FILE_BYTES} bytes")
    return data


class Reader:
    """Bounds-checked big/little endian field access; every overrun is a Reject."""

    def __init__(self, data: bytes, code: str) -> None:
        self.data = data
        self.code = code

    def unpack(self, fmt: str, offset: int):
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(self.data):
            raise Reject(self.code, f"truncated: need {size} bytes at offset {offset:#x} of {len(self.data):#x}")
        return struct.unpack_from(fmt, self.data, offset)

    def u16(self, offset: int) -> int:
        return self.unpack("<H", offset)[0]

    def u32(self, offset: int) -> int:
        return self.unpack("<I", offset)[0]


# ============================================================================ PE

PE_MACHINES = {0x014C: False, 0x8664: True, 0xAA64: True}  # machine -> PE32+?
WIN_CERT_REVISION_2 = 0x0200
WIN_CERT_TYPE_PKCS_SIGNED_DATA = 0x0002


class PeSection:
    def __init__(self, index, name, vsize, vaddr, rawsize, rawptr, characteristics):
        self.index, self.name = index, name
        self.vsize, self.vaddr, self.rawsize, self.rawptr = vsize, vaddr, rawsize, rawptr
        self.characteristics = characteristics

    @property
    def region(self) -> str:
        return f"section:{self.name}"

    @property
    def raw_end(self) -> int:
        return self.rawptr + self.rawsize


class PeImage:
    def __init__(self, data: bytes) -> None:
        self.data = data
        r = Reader(data, "pe-invalid-header")
        # Not a PE at all => the comparison cannot be performed (InputError, exit 2).
        if len(data) < 0x40 or data[:2] != b"MZ":
            raise InputError("not a PE image: no MZ header")
        lfanew = r.u32(0x3C)
        if lfanew < 0x40 or lfanew + 24 > len(data) or data[lfanew : lfanew + 4] != b"PE\x00\x00":
            raise InputError("not a PE image: no PE signature")
        self.lfanew = lfanew
        (self.machine, nsections, self.timestamp, symbols_ptr, symbols_count, opt_size, _chars) = r.unpack(
            "<HHIIIHH", lfanew + 4
        )
        self.opt = opt = lfanew + 24
        magic = r.u16(opt)
        if magic not in (0x10B, 0x20B):
            raise Reject("pe-unsupported-optional-header", f"unsupported optional header magic {magic:#x}")
        self.pe32plus = magic == 0x20B
        if self.machine not in PE_MACHINES or PE_MACHINES[self.machine] != self.pe32plus:
            raise Reject("pe-unsupported-machine", f"unsupported machine {self.machine:#x} for optional header {magic:#x}")
        base = 112 if self.pe32plus else 96
        if opt_size != base + 16 * 8 or r.u32(opt + (108 if self.pe32plus else 92)) != 16:
            raise Reject("pe-unsupported-optional-header", "optional header must carry exactly 16 data directories")
        if opt + opt_size > len(data):
            raise Reject("pe-invalid-header", "optional header extends past the file")
        if symbols_ptr or symbols_count:
            raise Reject("pe-unsupported-symbol-table", "COFF symbol table present (would be unauthenticated trailing data)")
        self.section_alignment = r.u32(opt + 32)
        self.file_alignment = r.u32(opt + 36)
        self.size_of_image = r.u32(opt + 56)
        self.size_of_headers = r.u32(opt + 60)
        self.checksum_offset = opt + 64
        self.directories_offset = opt + base
        self.security_offset = opt + base + 4 * 8
        self.security = r.unpack("<II", self.security_offset)
        self.checksum = r.u32(self.checksum_offset)
        for name, value in (("FileAlignment", self.file_alignment), ("SectionAlignment", self.section_alignment)):
            if value == 0 or value & (value - 1):
                raise Reject("pe-invalid-header", f"{name} {value:#x} is not a power of two")
        if not 0x200 <= self.file_alignment <= 0x10000 or self.section_alignment < self.file_alignment:
            raise Reject("pe-invalid-header", "implausible FileAlignment/SectionAlignment")
        if not 1 <= nsections <= MAX_SECTIONS:
            raise Reject("pe-invalid-header", f"section count {nsections} outside 1..{MAX_SECTIONS}")
        self.table_offset = opt + opt_size
        table_end = self.table_offset + 40 * nsections
        if table_end > self.size_of_headers or self.size_of_headers > len(data):
            raise Reject("pe-invalid-header", "section table does not fit in SizeOfHeaders / the file")
        self.sections = [self._section(r, index) for index in range(nsections)]
        self._validate_geometry()
        self.raw_end = max([self.size_of_headers] + [s.raw_end for s in self.sections if s.rawsize])
        self._validate_directories(r)

    def _section(self, r: Reader, index: int) -> PeSection:
        offset = self.table_offset + 40 * index
        name, vsize, vaddr, rawsize, rawptr, relocs, lines, nrelocs, nlines, chars = r.unpack("<8sIIIIIIHHI", offset)
        if relocs or lines or nrelocs or nlines:
            raise Reject("pe-invalid-section", f"section {index} carries COFF relocations/line numbers")
        label = name.rstrip(b"\x00").decode("latin-1")
        return PeSection(index, label, vsize, vaddr, rawsize, rawptr, chars)

    def virtual_span(self, section: PeSection) -> int:
        return align_up(section.vsize or section.rawsize, self.section_alignment)

    def _validate_geometry(self) -> None:
        names: dict[str, int] = {}
        for s in self.sections:
            names[s.name] = names.get(s.name, 0) + 1
        for s in self.sections:
            if names[s.name] > 1:
                s.name = f"{s.name}#{s.index}"
        for s in self.sections:
            if s.rawsize:
                if s.rawptr < self.size_of_headers:
                    raise Reject("pe-invalid-section-range", f"section {s.name} raw data starts inside the headers", s.region)
                if s.raw_end > len(self.data):
                    raise Reject("pe-invalid-section-range", f"section {s.name} raw data extends past the file", s.region)
                if s.rawptr % self.file_alignment:
                    raise Reject("pe-invalid-section-range", f"section {s.name} raw pointer is not file aligned", s.region)
            if s.vaddr % self.section_alignment or s.vaddr < align_up(self.size_of_headers, self.section_alignment):
                raise Reject("pe-invalid-section-range", f"section {s.name} virtual address invalid", s.region)
            if s.vaddr + self.virtual_span(s) > self.size_of_image:
                raise Reject("pe-invalid-section-range", f"section {s.name} extends past SizeOfImage", s.region)
        for left, right in _adjacent(sorted((s for s in self.sections if s.rawsize), key=lambda s: s.rawptr)):
            if left.raw_end > right.rawptr:
                raise Reject("pe-invalid-section-overlap", f"sections {left.name} and {right.name} overlap in the file")
        for left, right in _adjacent(sorted(self.sections, key=lambda s: s.vaddr)):
            if left.vaddr + self.virtual_span(left) > right.vaddr:
                raise Reject(
                    "pe-invalid-section-virtual-overlap", f"sections {left.name} and {right.name} overlap in memory"
                )

    def _validate_directories(self, r: Reader) -> None:
        for index in range(16):
            if index == 4:  # security: a FILE offset, validated against the file in compare_pe
                continue
            rva, size = r.unpack("<II", self.directories_offset + 8 * index)
            if not (rva or size):
                continue
            inside_headers = rva + size <= self.size_of_headers
            inside_section = any(
                s.vaddr <= rva and rva + size <= s.vaddr + self.virtual_span(s) for s in self.sections
            )
            if not (inside_headers or inside_section):
                raise Reject("pe-invalid-data-directory", f"data directory {index} lies outside every section")


def _adjacent(items):
    return zip(items, items[1:])


def parse_win_certificates(table: bytes) -> list[dict]:
    """Strictly parse a WIN_CERTIFICATE table that must be consumed exactly."""
    if not table or len(table) % 8:
        raise Reject("pe-invalid-certificate-table", "certificate table is empty or not a multiple of 8 bytes")
    certificates, offset = [], 0
    while offset < len(table):
        if len(certificates) >= MAX_CERTIFICATES:
            raise Reject("pe-invalid-certificate-table", f"more than {MAX_CERTIFICATES} certificates")
        if offset + 8 > len(table):
            raise Reject("pe-invalid-certificate-table", "truncated WIN_CERTIFICATE header")
        length, revision, cert_type = struct.unpack_from("<IHH", table, offset)
        if revision != WIN_CERT_REVISION_2 or cert_type != WIN_CERT_TYPE_PKCS_SIGNED_DATA:
            raise Reject(
                "pe-invalid-certificate-table",
                f"WIN_CERTIFICATE revision/type {revision:#x}/{cert_type:#x} is not 0x0200/0x0002",
            )
        if length <= 8 or offset + length > len(table):
            raise Reject("pe-invalid-certificate-table", "WIN_CERTIFICATE length is empty or overruns the table")
        blob = table[offset + 8 : offset + length]
        _check_der_sequence(blob)
        certificates.append({"length": length, "sha256": sha256(blob)})
        advance = align_up(length, 8)
        if offset + advance > len(table) or table[offset + length : offset + advance].strip(b"\x00"):
            raise Reject("pe-invalid-certificate-table", "WIN_CERTIFICATE padding is missing or non-zero")
        offset += advance
    return certificates


def _check_der_sequence(blob: bytes) -> None:
    """The body must be one DER ``SEQUENCE`` (PKCS#7 ContentInfo), spanning the blob up to zero padding."""
    if len(blob) < 2 or blob[0] != 0x30:
        raise Reject("pe-invalid-certificate-table", "certificate body is not a DER SEQUENCE")
    first = blob[1]
    if first < 0x80:
        header, body = 2, first
    else:
        count = first & 0x7F
        if not 1 <= count <= 4 or 2 + count > len(blob):
            raise Reject("pe-invalid-certificate-table", "unsupported DER length encoding")
        header, body = 2 + count, int.from_bytes(blob[2 : 2 + count], "big")
    total = header + body
    if total > len(blob) or blob[total:].strip(b"\x00"):
        raise Reject("pe-invalid-certificate-table", "DER SEQUENCE length does not match the certificate body")


def pe_masked_headers(image: PeImage) -> bytes:
    headers = bytearray(image.data[: image.size_of_headers])
    headers[image.checksum_offset : image.checksum_offset + 4] = b"\x00" * 4
    headers[image.security_offset : image.security_offset + 8] = b"\x00" * 8
    return bytes(headers)


def compare_pe(unsigned: bytes, signed: bytes) -> dict:
    differences: list[dict] = []
    allowed: list[dict] = []
    hashes = {"unsigned_sha256": sha256(unsigned), "signed_sha256": sha256(signed)}

    def finish(invariant_unsigned=None, invariant_signed=None):
        hashes["unsigned_invariant_sha256"] = invariant_unsigned
        hashes["signed_invariant_sha256"] = invariant_signed
        hashes["signed_matches_invariant"] = invariant_unsigned is not None and invariant_unsigned == invariant_signed
        return differences, allowed, hashes

    images: dict[str, PeImage] = {}
    for side, data in (("unsigned", unsigned), ("signed", signed)):
        try:
            images[side] = PeImage(data)
        except Reject as reject:
            differences.append(reject.difference(side))
    if len(images) != 2:
        return finish()
    u, s = images["unsigned"], images["signed"]

    if u.security != (0, 0):
        differences.append(difference("pe-unsigned-input-has-certificate", "the unsigned input already carries a security directory", side="unsigned"))
    cert_offset, cert_size = s.security
    if (cert_offset, cert_size) == (0, 0):
        differences.append(difference("pe-signed-input-has-no-certificate", "the signed input carries no certificate table", side="signed"))
    if differences:
        return finish()

    # --- the certificate table must be the exact terminal region, after the image content
    table_ok = True
    if cert_offset % 8 or cert_size > MAX_CERTIFICATE_TABLE:
        differences.append(difference("pe-invalid-certificate-table", "certificate table offset is not 8-byte aligned or is oversized", side="signed"))
        table_ok = False
    elif cert_offset < s.raw_end:
        differences.append(difference("pe-invalid-certificate-table", "certificate table overlaps the image content", side="signed", offset=cert_offset))
        table_ok = False
    elif cert_offset + cert_size > len(signed):
        differences.append(difference("pe-invalid-certificate-table", "certificate table extends past the end of the file", side="signed"))
        table_ok = False
    certificates: list[dict] = []
    if table_ok:
        try:
            certificates = parse_win_certificates(signed[cert_offset : cert_offset + cert_size])
        except Reject as reject:
            differences.append(reject.difference("signed"))
            table_ok = False
        if cert_offset + cert_size < len(signed):
            differences.append(
                difference(
                    "pe-unauthenticated-trailing-data",
                    "bytes follow the certificate table that no signature covers",
                    offset=cert_offset + cert_size,
                    size=len(signed) - (cert_offset + cert_size),
                )
            )

    # --- everything that is not signature material must be identical
    if pe_masked_headers(u) != pe_masked_headers(s):
        differences.append(difference("pe-header-changed", "headers differ outside the checksum and security directory", region="headers"))
    geometry_u = [(x.name, x.vsize, x.vaddr, x.rawsize, x.rawptr, x.characteristics) for x in u.sections]
    geometry_s = [(x.name, x.vsize, x.vaddr, x.rawsize, x.rawptr, x.characteristics) for x in s.sections]
    if geometry_u != geometry_s or u.raw_end != s.raw_end:
        differences.append(difference("pe-section-changed", "section table differs", region="section-table"))
        return finish()
    for section in u.sections:
        if unsigned[section.rawptr : section.raw_end] != signed[section.rawptr : section.raw_end]:
            differences.append(difference("pe-section-changed", f"raw data of {section.name} differs", region=section.region))
    cursor = u.size_of_headers
    for section in sorted((x for x in u.sections if x.rawsize), key=lambda x: x.rawptr):
        if unsigned[cursor : section.rawptr] != signed[cursor : section.rawptr]:
            differences.append(difference("pe-section-changed", "bytes between sections differ", region=f"gap:{cursor:#x}"))
        cursor = max(cursor, section.raw_end)

    # overlay (+ the alignment padding that signing appends), then the table's position
    end = u.raw_end
    overlay_u = unsigned[end:]
    padding = align_up(len(unsigned), 8) - len(unsigned)
    expected_offset = len(unsigned) + padding
    overlay_s = signed[end:cert_offset] if cert_offset >= end else b""
    if cert_offset != expected_offset or overlay_s[: len(overlay_u)] != overlay_u or len(overlay_s) != len(overlay_u) + padding:
        differences.append(
            difference("pe-overlay-changed", "overlay differs beyond signature alignment padding", region="overlay", expected_certificate_offset=expected_offset)
        )
    elif overlay_s[len(overlay_u) :].strip(b"\x00"):
        differences.append(difference("pe-signature-alignment-padding", "alignment padding before the certificate table is not zero", offset=len(unsigned), size=padding))

    if differences:
        return finish()

    if u.checksum != s.checksum:
        allowed.append({"code": "pe-checksum-field", "offset": s.checksum_offset, "unsigned": u.checksum, "signed": s.checksum})
    allowed.append(
        {
            "code": "pe-security-directory",
            "offset": s.security_offset,
            "unsigned": {"offset": 0, "size": 0},
            "signed": {"offset": cert_offset, "size": cert_size},
        }
    )
    if padding:
        allowed.append({"code": "pe-signature-alignment-padding", "offset": len(unsigned), "size": padding})
    allowed.append(
        {
            "code": "pe-certificate-table",
            "offset": cert_offset,
            "size": cert_size,
            "entries": len(certificates),
            "certificates": certificates,
            "verify_separately": True,
        }
    )
    masked_u = pe_masked_headers(u) + unsigned[u.size_of_headers :]
    masked_s = pe_masked_headers(s) + signed[s.size_of_headers : len(unsigned)]
    return finish(sha256(masked_u), sha256(masked_s))


# ======================================================================== Mach-O

MH_MAGIC_64 = b"\xcf\xfa\xed\xfe"  # 0xFEEDFACF little endian
OTHER_THIN_MAGICS = (b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf")
FAT_MAGIC = b"\xca\xfe\xba\xbe"
FAT_MAGIC_64 = b"\xca\xfe\xba\xbf"
FAT_MAGIC_SWAPPED = (b"\xbe\xba\xfe\xca", b"\xbf\xba\xfe\xca")

LC_SEGMENT_64 = 0x19
LC_CODE_SIGNATURE = 0x1D
LC_UUID = 0x1B
KNOWN_LOAD_COMMANDS = {
    0x02: "LC_SYMTAB",
    0x0B: "LC_DYSYMTAB",
    0x0C: "LC_LOAD_DYLIB",
    0x0D: "LC_ID_DYLIB",
    0x0E: "LC_LOAD_DYLINKER",
    0x19: "LC_SEGMENT_64",
    0x1B: "LC_UUID",
    0x1D: "LC_CODE_SIGNATURE",
    0x1E: "LC_SEGMENT_SPLIT_INFO",
    0x20: "LC_LAZY_LOAD_DYLIB",
    0x22: "LC_DYLD_INFO",
    0x24: "LC_VERSION_MIN_MACOSX",
    0x26: "LC_FUNCTION_STARTS",
    0x27: "LC_DYLD_ENVIRONMENT",
    0x29: "LC_DATA_IN_CODE",
    0x2A: "LC_SOURCE_VERSION",
    0x2B: "LC_DYLIB_CODE_SIGN_DRS",
    0x2C: "LC_ENCRYPTION_INFO_64",
    0x2E: "LC_LINKER_OPTIMIZATION_HINT",
    0x31: "LC_NOTE",
    0x32: "LC_BUILD_VERSION",
    0x80000018: "LC_LOAD_WEAK_DYLIB",
    0x8000001C: "LC_RPATH",
    0x8000001F: "LC_REEXPORT_DYLIB",
    0x80000022: "LC_DYLD_INFO_ONLY",
    0x80000023: "LC_LOAD_UPWARD_DYLIB",
    0x80000028: "LC_MAIN",
    0x80000033: "LC_DYLD_EXPORTS_TRIE",
    0x80000034: "LC_DYLD_CHAINED_FIXUPS",
}
# Commands whose size is fixed by their on-disk struct (mach-o/loader.h).
FIXED_COMMAND_SIZES = {
    0x02: 24,  # LC_SYMTAB
    0x0B: 80,  # LC_DYSYMTAB
    0x1B: 24,  # LC_UUID
    0x1D: 16,  # LC_CODE_SIGNATURE
    0x1E: 16,  # LC_SEGMENT_SPLIT_INFO
    0x22: 48,  # LC_DYLD_INFO
    0x24: 16,  # LC_VERSION_MIN_MACOSX
    0x26: 16,  # LC_FUNCTION_STARTS
    0x29: 16,  # LC_DATA_IN_CODE
    0x2A: 16,  # LC_SOURCE_VERSION
    0x2B: 16,  # LC_DYLIB_CODE_SIGN_DRS
    0x2C: 24,  # LC_ENCRYPTION_INFO_64
    0x2E: 16,  # LC_LINKER_OPTIMIZATION_HINT
    0x31: 40,  # LC_NOTE
    0x80000022: 48,  # LC_DYLD_INFO_ONLY
    0x80000028: 24,  # LC_MAIN
    0x80000033: 16,  # LC_DYLD_EXPORTS_TRIE
    0x80000034: 16,  # LC_DYLD_CHAINED_FIXUPS
}
DYLIB_COMMANDS = (0x0C, 0x0D, 0x20, 0x80000018, 0x8000001F, 0x80000023)  # dylib_command: name at +8, 24 byte header
STRING_COMMANDS = (0x0E, 0x27, 0x8000001C)  # dylinker / rpath: lc_str at +8, 12 byte header
LINKEDIT_DATA_COMMANDS = (0x1D, 0x1E, 0x26, 0x29, 0x2B, 0x2E, 0x80000033, 0x80000034)  # (dataoff, datasize) at +8
LC_SYMTAB, LC_DYSYMTAB, LC_BUILD_VERSION, LC_MAIN = 0x02, 0x0B, 0x32, 0x80000028
LC_ENCRYPTION_INFO_64, LC_NOTE = 0x2C, 0x31
LC_DYLD_INFO, LC_DYLD_INFO_ONLY = 0x22, 0x80000022
CPU_TYPE_ARM64, CPU_TYPE_X86_64 = 0x0100000C, 0x01000007
FILE_TYPES = (2, 6, 8)  # MH_EXECUTE, MH_DYLIB, MH_BUNDLE
ZEROFILL_SECTION_TYPES = (0x01, 0x0C, 0x12)

SLOT_CODE_DIRECTORY, SLOT_REQUIREMENTS, SLOT_ENTITLEMENTS, SLOT_DER_ENTITLEMENTS = 0, 2, 5, 7
SLOT_ALTERNATE_FIRST, SLOT_ALTERNATE_LAST, SLOT_CMS = 0x1000, 0x1004, 0x10000
CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_CODEDIRECTORY = 0xFADE0C02
CSMAGIC_REQUIREMENTS = 0xFADE0C01
CSMAGIC_ENTITLEMENTS = 0xFADE7171
CSMAGIC_DER_ENTITLEMENTS = 0xFADE7172
CSMAGIC_BLOBWRAPPER = 0xFADE0B01
CS_ADHOC = 0x2
HASH_TYPES = {1: ("sha1", 20), 2: ("sha256", 32), 3: ("sha256-truncated", 20), 4: ("sha384", 48)}


class Command:
    def __init__(self, ident: int, offset: int, raw: bytes) -> None:
        self.ident, self.offset, self.raw = ident, offset, raw
        self.name = KNOWN_LOAD_COMMANDS[ident]


class Segment:
    def __init__(self, command: Command) -> None:
        raw = command.raw
        if len(raw) < 72:
            raise Reject("macho-invalid-load-commands", f"LC_SEGMENT_64 has size {len(raw)}, below its 72 byte header")
        (self.name_raw, self.vmaddr, self.vmsize, self.fileoff, self.filesize, self.maxprot, self.initprot, nsects, self.flags) = struct.unpack_from(
            "<16sQQQQiiII", raw, 8
        )
        self.name = self.name_raw.rstrip(b"\x00").decode("latin-1")
        if len(raw) != 72 + 80 * nsects:
            raise Reject("macho-invalid-load-commands", f"segment {self.name} command size does not match its section count")
        self.sections = []
        for index in range(nsects):
            sectname, segname, addr, size, offset, _align, _reloff, _nreloc, flags = struct.unpack_from(
                "<16s16sQQIIIII", raw, 72 + 80 * index
            )
            self.sections.append(
                {
                    "name": f"{segname.rstrip(b'\x00').decode('latin-1')}.{sectname.rstrip(b'\x00').decode('latin-1')}",
                    "addr": addr,
                    "size": size,
                    "offset": offset,
                    "flags": flags,
                    "zerofill": (flags & 0xFF) in ZEROFILL_SECTION_TYPES,
                }
            )
        self.command = command


def check_command_shape(command: Command) -> None:
    """Minimum size and internal string offsets of a known command, before anything decodes it."""
    ident, raw, name = command.ident, command.raw, command.name
    size = len(raw)

    def bad(message: str) -> Reject:
        return Reject("macho-invalid-load-commands", f"{name}: {message}")

    if ident == LC_SEGMENT_64:
        if size < 72:
            raise bad(f"size {size} is below its 72 byte header")
    elif ident in DYLIB_COMMANDS or ident in STRING_COMMANDS:
        header = 24 if ident in DYLIB_COMMANDS else 12
        if size < header:
            raise bad(f"size {size} is below its {header} byte header")
        offset = struct.unpack_from("<I", raw, 8)[0]
        if not header <= offset < size or raw.find(b"\x00", offset) < 0:
            raise bad("name/path offset is outside the command or its string is not terminated")
    elif ident == LC_BUILD_VERSION:
        if size < 24 or size != 24 + 8 * struct.unpack_from("<I", raw, 20)[0]:
            raise bad("size does not match its tool count")
    elif ident not in FIXED_COMMAND_SIZES:
        raise bad("no shape rule for this command")  # KNOWN_LOAD_COMMANDS and the rules must stay in step


class MachSlice:
    """One strictly parsed thin 64-bit little-endian Mach-O image."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        r = Reader(data, "macho-invalid-header")
        if data[:4] != MH_MAGIC_64:
            if data[:4] in OTHER_THIN_MAGICS:
                raise Reject("macho-unsupported-format", "only 64-bit little-endian Mach-O images are supported")
            raise Reject("macho-invalid-header", "slice is not a 64-bit Mach-O image")
        self.cputype, self.cpusubtype, self.filetype, ncmds, sizeofcmds, self.flags, self.reserved = r.unpack("<iiIIIII", 4)
        if self.filetype not in FILE_TYPES:
            raise Reject("macho-unsupported-format", f"unsupported Mach-O file type {self.filetype}")
        if ncmds > MAX_LOAD_COMMANDS or sizeofcmds > MAX_LOAD_COMMAND_BYTES or 32 + sizeofcmds > len(data):
            raise Reject("macho-invalid-load-commands", "load command area is implausible or past the slice")
        self.ncmds, self.sizeofcmds = ncmds, sizeofcmds
        self.commands: list[Command] = []
        cursor = 32
        for _ in range(ncmds):
            if cursor + 8 > 32 + sizeofcmds:
                raise Reject("macho-invalid-load-commands", "ncmds runs past sizeofcmds")
            ident, size = struct.unpack_from("<II", data, cursor)
            if size < 8 or size % 8 or cursor + size > 32 + sizeofcmds:
                raise Reject("macho-invalid-load-commands", f"load command at {cursor:#x} has an invalid size")
            if ident not in KNOWN_LOAD_COMMANDS:
                raise Reject("macho-unknown-load-command", f"unknown load command {ident:#x}")
            expected = FIXED_COMMAND_SIZES.get(ident)
            if expected is not None and size != expected:
                raise Reject("macho-invalid-load-commands", f"{KNOWN_LOAD_COMMANDS[ident]} has size {size}, expected {expected}")
            command = Command(ident, cursor, data[cursor : cursor + size])
            check_command_shape(command)
            self.commands.append(command)
            cursor += size
        if cursor != 32 + sizeofcmds:
            raise Reject("macho-invalid-load-commands", "sizeofcmds is larger than the commands it describes")
        self.segments = [Segment(c) for c in self.commands if c.ident == LC_SEGMENT_64]
        signatures = [c for c in self.commands if c.ident == LC_CODE_SIGNATURE]
        if len(signatures) > 1 or len([c for c in self.commands if c.ident == LC_UUID]) > 1:
            raise Reject("macho-invalid-load-commands", "duplicate LC_CODE_SIGNATURE or LC_UUID")
        self.signature_command = signatures[0] if signatures else None
        self.dataoff = self.datasize = None
        if self.signature_command:
            self.dataoff, self.datasize = struct.unpack_from("<II", self.signature_command.raw, 8)
        self._validate_layout()

    def _validate_layout(self) -> None:
        linkedits = [s for s in self.segments if s.name == "__LINKEDIT"]
        if len(linkedits) != 1:
            raise Reject("macho-invalid-linkedit-extent", "exactly one __LINKEDIT segment is required")
        self.linkedit = linkedit = linkedits[0]
        ranges = []
        for segment in self.segments:
            if segment.fileoff + segment.filesize > len(self.data):
                raise Reject("macho-invalid-linkedit-extent", f"segment {segment.name} extends past the slice")
            if segment.filesize:
                ranges.append((segment.fileoff, segment.fileoff + segment.filesize, segment.name))
            for section in segment.sections:
                if section["zerofill"] or not section["size"]:
                    continue
                if not (segment.fileoff <= section["offset"] and section["offset"] + section["size"] <= segment.fileoff + segment.filesize):
                    raise Reject("macho-invalid-load-commands", f"section {section['name']} lies outside its segment")
        for (_, left_end, left), (right_start, _, right) in _adjacent(sorted(ranges)):
            if left_end > right_start:
                raise Reject("macho-invalid-load-commands", f"segments {left} and {right} overlap in the file")
        if any(s.fileoff > linkedit.fileoff or s.vmaddr > linkedit.vmaddr for s in self.segments if s is not linkedit):
            raise Reject("macho-invalid-linkedit-extent", "__LINKEDIT is not the last segment")
        linkedit_end = linkedit.fileoff + linkedit.filesize
        if linkedit_end < len(self.data):
            raise Reject("macho-trailing-data", "bytes follow __LINKEDIT and are covered by no load command")
        if self.dataoff is not None and not (
            linkedit.fileoff <= self.dataoff and self.dataoff + self.datasize == linkedit_end == len(self.data)
        ):
            raise Reject("macho-invalid-linkedit-extent", "the code signature does not end __LINKEDIT and the slice")
        if 32 + self.sizeofcmds > self.first_data_offset():
            raise Reject("macho-invalid-load-commands", "load commands overlap section data")
        self._validate_virtual_geometry()
        self._validate_command_ranges()

    def _validate_virtual_geometry(self) -> None:
        """Segments and sections must be coherent in memory as well as in the file."""
        bad = "macho-invalid-load-commands"
        spans = []
        for segment in self.segments:
            if segment.vmaddr + segment.vmsize > 1 << 64:
                raise Reject(bad, f"segment {segment.name} wraps the address space")
            if segment.vmsize < segment.filesize:
                raise Reject(bad, f"segment {segment.name} maps fewer bytes in memory than it has in the file")
            if segment.vmsize:
                spans.append((segment.vmaddr, segment.vmaddr + segment.vmsize, segment.name))
            section_spans, file_spans = [], []
            for section in segment.sections:
                end = section["addr"] + section["size"]
                if section["size"] and not (segment.vmaddr <= section["addr"] and end <= segment.vmaddr + segment.vmsize):
                    raise Reject(bad, f"section {section['name']} lies outside its segment in memory")
                if section["size"]:
                    section_spans.append((section["addr"], end, section["name"]))
                if not section["zerofill"] and section["size"]:
                    if section["offset"] - segment.fileoff != section["addr"] - segment.vmaddr:
                        raise Reject(bad, f"section {section['name']} file offset and address disagree within its segment")
                    file_spans.append((section["offset"], section["offset"] + section["size"], section["name"]))
            for group in (section_spans, file_spans):
                for (_, left_end, left), (right_start, _, right) in _adjacent(sorted(group)):
                    if left_end > right_start:
                        raise Reject(bad, f"sections {left} and {right} overlap")
        for (_, left_end, left), (right_start, _, right) in _adjacent(sorted(spans)):
            if left_end > right_start:
                raise Reject(bad, f"segments {left} and {right} overlap in memory")

    def _validate_command_ranges(self) -> None:
        """Offsets inside linkedit-resident commands must stay inside __LINKEDIT and off the signature."""
        bad = "macho-invalid-load-commands"
        low, high = self.linkedit.fileoff, self.linkedit.fileoff + self.linkedit.filesize
        signature = None if self.dataoff is None else (self.dataoff, self.dataoff + self.datasize)
        symbols = None
        for command in self.commands:
            raw, ident = command.raw, command.ident
            ranges: list[tuple[str, int, int]] = []
            if ident == LC_SYMTAB:
                symoff, nsyms, stroff, strsize = struct.unpack_from("<IIII", raw, 8)
                symbols = nsyms
                ranges = [("symbols", symoff, nsyms * 16), ("strings", stroff, strsize)]
            elif ident == LC_DYSYMTAB:
                values = struct.unpack_from("<18I", raw, 8)
                partitions = [(values[0], values[1]), (values[2], values[3]), (values[4], values[5])]
                if symbols is not None and any(index + count > symbols for index, count in partitions):
                    raise Reject(bad, f"{command.name}: a symbol partition runs past the symbol table")
                ranges = [
                    ("toc", values[6], values[7] * 8), ("modules", values[8], values[9] * 56),
                    ("extrefs", values[10], values[11] * 4), ("indirect", values[12], values[13] * 4),
                    ("external relocations", values[14], values[15] * 8), ("local relocations", values[16], values[17] * 8),
                ]
            elif ident in (LC_DYLD_INFO, LC_DYLD_INFO_ONLY):
                values = struct.unpack_from("<10I", raw, 8)
                ranges = [(label, values[2 * i], values[2 * i + 1]) for i, label in enumerate(("rebase", "bind", "weak bind", "lazy bind", "exports"))]
            elif ident in LINKEDIT_DATA_COMMANDS and ident != LC_CODE_SIGNATURE:
                ranges = [("data", *struct.unpack_from("<II", raw, 8))]
            elif ident == LC_ENCRYPTION_INFO_64:
                cryptoff, cryptsize = struct.unpack_from("<II", raw, 8)
                if cryptsize and cryptoff + cryptsize > len(self.data):
                    raise Reject(bad, f"{command.name}: encrypted range lies outside the slice")
            elif ident == LC_NOTE:
                offset, size = struct.unpack_from("<QQ", raw, 24)
                if size and offset + size > len(self.data):
                    raise Reject(bad, f"{command.name}: note data lies outside the slice")
            elif ident == LC_MAIN:
                if struct.unpack_from("<Q", raw, 8)[0] >= len(self.data):
                    raise Reject(bad, f"{command.name}: entry offset lies outside the slice")
            for label, offset, size in ranges:
                if not size:
                    continue
                if offset < low or offset + size > high:
                    raise Reject(bad, f"{command.name}: {label} range lies outside __LINKEDIT")
                if signature and offset < signature[1] and offset + size > signature[0]:
                    raise Reject(bad, f"{command.name}: {label} range overlaps the code signature")

    def first_data_offset(self) -> int:
        offsets = [s["offset"] for g in self.segments for s in g.sections if s["size"] and not s["zerofill"] and s["offset"]]
        offsets += [g.fileoff for g in self.segments if g.filesize and g.fileoff]
        return min(offsets) if offsets else len(self.data)

    @property
    def page_size(self) -> int:
        return 0x4000 if self.cputype == CPU_TYPE_ARM64 else 0x1000

    @property
    def non_signature_commands(self) -> list[Command]:
        return [c for c in self.commands if c.ident != LC_CODE_SIGNATURE]

    @property
    def linkedit_data_end(self) -> int:
        """End of linkedit content that is not the signature."""
        return self.dataoff if self.dataoff is not None else self.linkedit.fileoff + self.linkedit.filesize


def masked_linkedit_command(segment: Segment) -> bytes:
    """The raw ``__LINKEDIT`` command with vmsize (bytes 32-40) and filesize (bytes 48-56) zeroed."""
    raw = bytearray(segment.command.raw)
    raw[32:40] = b"\x00" * 8
    raw[48:56] = b"\x00" * 8
    return bytes(raw)


def command_region(command: Command) -> str:
    if command.ident == LC_SEGMENT_64:
        return f"load-command:{command.name}:{command.raw[8:24].rstrip(b'\x00').decode('latin-1')}"
    return f"load-command:{command.name}"


# ---- embedded signature --------------------------------------------------------


def canonical_plist(value: Any) -> Any:
    """A JSON-able, type-preserving form of a decoded plist (``True`` is not ``1`` is not ``1.0``)."""
    if value is None:  # "no entitlements slot at all", distinct from an empty dictionary
        return ["absent"]
    if isinstance(value, bool):  # before int: bool is an int subclass
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        return ["real", "nan" if value != value else value.hex()]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, (bytes, bytearray)):
        return ["data", bytes(value).hex()]
    if isinstance(value, datetime.datetime):
        return ["date", value.isoformat()]
    if isinstance(value, list):
        return ["array", [canonical_plist(item) for item in value]]
    if isinstance(value, dict):
        return ["dict", {str(key): canonical_plist(item) for key, item in value.items()}]
    if isinstance(value, plistlib.UID):
        return ["uid", value.data]
    raise ValueError(f"unsupported plist value type {type(value).__name__}")


def canonical_plist_sha256(value: Any) -> str:
    return sha256(canonical_json(canonical_plist(value)))


def decode_entitlements_dict(payload: bytes, fmt, code: str, label: str) -> dict:
    """Decode an entitlements plist; it must be a dictionary of plist values."""
    if len(payload) > MAX_ENTITLEMENTS_BYTES:
        raise Reject(code, f"{label} is oversized")
    try:
        value = plistlib.loads(payload, fmt=fmt)
        canonical_plist(value)  # every nested value must be a type this tool can compare exactly
    except Exception as error:  # plistlib raises several unrelated types
        raise Reject(code, f"{label} does not parse: {type(error).__name__}") from error
    if not isinstance(value, dict):
        raise Reject(code, f"{label} is not a dictionary")
    return value


class SignatureInfo:
    def __init__(self) -> None:
        self.report: dict[str, Any] = {}
        self.entitlements: Any = None  # decoded plist from the XML entitlements blob, or None


def parse_blob_header(region: bytes, offset: int, code: str) -> tuple[int, int]:
    if offset < 0 or offset + 8 > len(region):
        raise Reject(code, "blob header outside the signature")
    magic, length = struct.unpack_from(">II", region, offset)
    if length < 8 or offset + length > len(region):
        raise Reject(code, f"blob {magic:#x} length overruns the signature")
    return magic, length


def parse_signature(slice_: MachSlice, *, signed: bool = True) -> SignatureInfo:
    """Validate the SuperBlob at the slice's LC_CODE_SIGNATURE (structure, not trust).

    ``signed=False`` is for an *unsigned input* that already carries a linker (ad-hoc) signature: it gets
    the identical bounded structural checks, relaxing only the CMS-wrapper and ad-hoc requirements.
    """
    code = "macho-invalid-signature-blob"
    region = slice_.data[slice_.dataoff : slice_.dataoff + slice_.datasize]
    if len(region) < 12:
        raise Reject(code, "signature region is smaller than a SuperBlob header")
    magic, length, count = struct.unpack_from(">III", region, 0)
    if magic != CSMAGIC_EMBEDDED_SIGNATURE:
        raise Reject(code, f"SuperBlob magic {magic:#x} is not an embedded signature")
    if not 12 <= length <= len(region) or region[length:].strip(b"\x00"):
        raise Reject(code, "SuperBlob length is inconsistent with LC_CODE_SIGNATURE or its padding is not zero")
    region = region[:length]
    if not 1 <= count <= MAX_SUPERBLOB_BLOBS or 12 + 8 * count > length:
        raise Reject(code, "SuperBlob blob count is implausible")
    blobs: dict[int, bytes] = {}
    spans = []
    for index in range(count):
        slot, offset = struct.unpack_from(">II", region, 12 + 8 * index)
        blob_magic, blob_length = parse_blob_header(region, offset, code)
        if offset < 12 + 8 * count:
            raise Reject(code, "blob overlaps the SuperBlob index")
        if slot in blobs:
            raise Reject(code, f"duplicate slot {slot:#x}")
        blobs[slot] = region[offset : offset + blob_length]
        spans.append((offset, offset + blob_length))
        expected = {
            SLOT_REQUIREMENTS: CSMAGIC_REQUIREMENTS,
            SLOT_ENTITLEMENTS: CSMAGIC_ENTITLEMENTS,
            SLOT_DER_ENTITLEMENTS: CSMAGIC_DER_ENTITLEMENTS,
            SLOT_CMS: CSMAGIC_BLOBWRAPPER,
        }.get(slot)
        is_directory = slot == SLOT_CODE_DIRECTORY or SLOT_ALTERNATE_FIRST <= slot <= SLOT_ALTERNATE_LAST
        if is_directory:
            expected = CSMAGIC_CODEDIRECTORY
        if expected is None:
            raise Reject("macho-unsupported-signature-slot", f"signature slot {slot:#x} is not understood")
        if blob_magic != expected:
            raise Reject(code, f"slot {slot:#x} has magic {blob_magic:#x}, expected {expected:#x}")
    for (_, left_end), (right_start, _) in _adjacent(sorted(spans)):
        if left_end > right_start:
            raise Reject(code, "signature blobs overlap")
    if SLOT_CODE_DIRECTORY not in blobs:
        raise Reject(code, "no primary CodeDirectory")
    if SLOT_DER_ENTITLEMENTS in blobs:
        # The DER form is the one the kernel enforces. Until it is decoded and cross-checked against the
        # XML form and the policy, its semantics are unverified: fail closed rather than disclaim.
        raise Reject("macho-der-entitlements-unsupported", "a DER entitlements slot is present but this tool cannot decode it")
    cms = blobs.get(SLOT_CMS)
    if signed and (cms is None or len(cms) <= 8):
        raise Reject("macho-adhoc-signature", "no CMS signature wrapper: ad-hoc signatures are not an acceptable signed output")

    info = SignatureInfo()
    directories = []
    for slot in sorted(blobs):
        if slot == SLOT_CODE_DIRECTORY or SLOT_ALTERNATE_FIRST <= slot <= SLOT_ALTERNATE_LAST:
            directories.append(validate_code_directory(slot, blobs, slice_, allow_adhoc=not signed))
    if len({d["identifier"] for d in directories}) != 1 or len({d["code_limit"] for d in directories}) != 1:
        raise Reject(code, "CodeDirectories disagree on identifier or code limit")
    primary = directories[0]
    entitlements = blobs.get(SLOT_ENTITLEMENTS)
    if entitlements is not None:
        info.entitlements = decode_entitlements_dict(entitlements[8:], plistlib.FMT_XML, "macho-invalid-entitlements", "entitlements plist")
    info.report = {
        "dataoff": slice_.dataoff,
        "datasize": slice_.datasize,
        "superblob_length": length,
        "code_directory": primary,
        "alternate_code_directories": len(directories) - 1,
        "entitlements_sha256": sha256(entitlements) if entitlements is not None else None,
        "cms_sha256": sha256(cms) if cms is not None else None,
        "cryptographic_signature_verified": False,
        "verify_separately": True,
    }
    return info


# CodeDirectory versions this tool implements, with their full fixed header sizes (cs_blobs.h):
#   0x20001 base (through spare2) / 0x20100 + scatterOffset / 0x20200 + teamOffset /
#   0x20300 + spare3, codeLimit64 / 0x20400 + execSegBase, execSegLimit, execSegFlags /
#   0x20500 + runtime, preEncryptOffset.
# Anything else (older, between layouts, or newer) is refused, never read as "an 88-byte header".
CODE_DIRECTORY_HEADER_SIZES = {0x20001: 44, 0x20100: 48, 0x20200: 52, 0x20300: 64, 0x20400: 88, 0x20500: 96}


def validate_code_directory(slot: int, blobs: dict[int, bytes], slice_: MachSlice, *, allow_adhoc: bool = False) -> dict:
    code = "macho-invalid-signature-blob"
    unsupported = "macho-unsupported-signature-layout"
    blob = blobs[slot]
    if len(blob) < 44:
        raise Reject(code, "CodeDirectory is truncated")
    (_magic, length, version, flags, hash_offset, ident_offset, n_special, n_code, code_limit) = struct.unpack_from(">IIIIIIIII", blob, 0)
    hash_size, hash_type, _platform, page_log2 = struct.unpack_from(">BBBB", blob, 36)
    if length != len(blob):
        raise Reject(code, "CodeDirectory length disagrees with its blob")
    header_size = CODE_DIRECTORY_HEADER_SIZES.get(version)
    if header_size is None:
        raise Reject(unsupported, f"CodeDirectory version {version:#x} is not an implemented layout")
    if len(blob) < header_size:
        raise Reject(code, "CodeDirectory header is truncated")
    if ident_offset < header_size or ident_offset >= length:  # structure before layout selectors
        raise Reject(code, "CodeDirectory identifier offset is invalid")
    if struct.unpack_from(">I", blob, 40)[0]:
        raise Reject(unsupported, "CodeDirectory spare2 field is not zero")
    if version >= 0x20100 and struct.unpack_from(">I", blob, 44)[0]:
        raise Reject(unsupported, "CodeDirectory declares scatter vector pages, which this tool does not implement")
    team_offset = struct.unpack_from(">I", blob, 48)[0] if version >= 0x20200 else 0
    if version >= 0x20300:
        if struct.unpack_from(">I", blob, 52)[0]:
            raise Reject(unsupported, "CodeDirectory spare3 field is not zero")
        code_limit64 = struct.unpack_from(">Q", blob, 56)[0]
        if code_limit and code_limit64:
            raise Reject(code, "CodeDirectory declares both a 32-bit and a 64-bit code limit")
        code_limit = code_limit or code_limit64
    if version >= 0x20400:
        exec_base, exec_limit = struct.unpack_from(">QQ", blob, 64)
        if exec_base + exec_limit > code_limit:
            raise Reject(code, "CodeDirectory executable segment lies outside the signed range")
    if version >= 0x20500 and struct.unpack_from(">I", blob, 92)[0]:
        raise Reject(unsupported, "CodeDirectory declares pre-encryption hashes, which this tool does not implement")
    if hash_type not in HASH_TYPES or HASH_TYPES[hash_type][1] != hash_size:
        raise Reject(code, f"unsupported CodeDirectory hash type {hash_type} / size {hash_size}")
    algorithm = HASH_TYPES[hash_type][0]
    hash_name = {"sha1": "sha1", "sha256": "sha256", "sha256-truncated": "sha256", "sha384": "sha384"}[algorithm]
    if flags & CS_ADHOC and not allow_adhoc:
        raise Reject("macho-adhoc-signature", "CodeDirectory is flagged ad-hoc")
    if code_limit != slice_.dataoff:
        raise Reject(code, "CodeDirectory does not cover exactly the bytes before the signature")
    if page_log2 and not 12 <= page_log2 <= 16:
        raise Reject(code, f"implausible CodeDirectory page size 2^{page_log2}")
    page = (1 << page_log2) if page_log2 else code_limit or 1
    expected_slots = -(-code_limit // page) if code_limit else 0
    if n_code != expected_slots or n_special > 255:
        raise Reject(code, "CodeDirectory slot counts do not match the code limit")
    end = blob.find(b"\x00", ident_offset)
    if end < 0:
        raise Reject(code, "CodeDirectory identifier is not terminated")
    if hash_offset < max(header_size, end + 1) + n_special * hash_size or hash_offset + n_code * hash_size > length:
        raise Reject(code, "CodeDirectory hash arrays are out of range")
    team = None
    if team_offset:
        team_end = blob.find(b"\x00", team_offset) if header_size <= team_offset < length else -1
        hashes_start = hash_offset - n_special * hash_size
        if team_end < 0 or (team_offset < hash_offset + n_code * hash_size and team_end >= hashes_start):
            raise Reject(code, "CodeDirectory team identifier offset is invalid")
        team = blob[team_offset:team_end].decode("utf-8", "replace")

    def digest(chunk: bytes) -> bytes:
        return hashlib.new(hash_name, chunk).digest()[:hash_size]

    for index in range(n_code):
        start = index * page
        actual = digest(slice_.data[start : min(start + page, code_limit)])
        if actual != blob[hash_offset + index * hash_size : hash_offset + (index + 1) * hash_size]:
            raise Reject("macho-code-directory-hash-mismatch", f"code page {index} hash does not match the signed bytes")
    special_hashes: dict[int, str] = {}
    for special in range(1, n_special + 1):
        recorded = blob[hash_offset - special * hash_size : hash_offset - (special - 1) * hash_size]
        blob_for_slot = blobs.get(special) if special in (SLOT_REQUIREMENTS, SLOT_ENTITLEMENTS, SLOT_DER_ENTITLEMENTS) else None
        if blob_for_slot is not None:
            if digest(blob_for_slot) != recorded:
                raise Reject("macho-code-directory-hash-mismatch", f"special slot {special} hash does not match its blob")
        elif special in (SLOT_REQUIREMENTS, SLOT_ENTITLEMENTS, SLOT_DER_ENTITLEMENTS):
            if recorded.strip(b"\x00"):
                raise Reject("macho-code-directory-hash-mismatch", f"special slot {special} is hashed but its blob is absent")
        elif special not in (1, 3, 4, 6) and recorded.strip(b"\x00"):
            raise Reject(code, f"special slot {special} is populated but unknown")
        if recorded.strip(b"\x00"):
            special_hashes[special] = recorded.hex()
    for special in (SLOT_REQUIREMENTS, SLOT_ENTITLEMENTS, SLOT_DER_ENTITLEMENTS):
        if special in blobs and special > n_special:
            raise Reject("macho-code-directory-hash-mismatch", f"blob for special slot {special} is not hashed by the CodeDirectory")
    return {
        "slot": slot,
        "version": f"{version:#x}",
        "flags": f"{flags:#x}",
        "hash_type": algorithm,
        "identifier": blob[ident_offset:end].decode("utf-8", "replace"),
        "team_identifier": team,
        "code_limit": code_limit,
        "page_size": page,
        "n_code_slots": n_code,
        "page_hashes_verified": True,
        "special_slot_hashes": {str(key): special_hashes[key] for key in sorted(special_hashes)},
    }


# ---- container + slice comparison -----------------------------------------------


class FatArch:
    def __init__(self, cputype, cpusubtype, offset, size, align):
        self.cputype, self.cpusubtype, self.offset, self.size, self.align = cputype, cpusubtype, offset, size, align

    @property
    def key(self) -> tuple[int, int]:
        return (self.cputype & 0xFFFFFFFF, self.cpusubtype & 0x00FFFFFF)


def fat_table_entry(arch: FatArch) -> dict:
    """The fat-table fields a signing step has no reason to change (everything except offset/size)."""
    return {"cputype": f"{arch.cputype & 0xFFFFFFFF:#010x}", "cpusubtype": f"{arch.cpusubtype & 0xFFFFFFFF:#010x}", "align": arch.align}


def parse_macho_container(data: bytes) -> tuple[str, list[tuple[FatArch | None, MachSlice]], list[int]]:
    """``(kind, slices in table order, table indices in physical (file) order)``."""
    magic = data[:4]
    if magic == MH_MAGIC_64 or magic in OTHER_THIN_MAGICS:
        return "thin", [(None, MachSlice(data))], [0]
    if magic in (FAT_MAGIC_64,) + FAT_MAGIC_SWAPPED:
        raise Reject("macho-unsupported-format", "only 32-bit-offset big-endian fat headers are supported")
    if magic != FAT_MAGIC:
        raise InputError("not a Mach-O image: unrecognised magic")
    if len(data) < 8:
        raise InputError("not a Mach-O image: truncated fat header")
    count = struct.unpack_from(">I", data, 4)[0]
    if not 1 <= count <= MAX_FAT_ARCHES or 8 + 20 * count > len(data):
        raise InputError("not a Mach-O image: implausible fat architecture count")
    arches = [FatArch(*struct.unpack_from(">iiIII", data, 8 + 20 * index)) for index in range(count)]
    keys = [a.key for a in arches]
    if len(set(keys)) != len(keys):
        raise Reject("macho-invalid-fat", "duplicate architecture in the fat header")
    table_end = 8 + 20 * count
    cursor = table_end
    slices: dict[int, MachSlice] = {}
    physical: list[int] = []
    # Physical layout is judged in file order. A signing step may grow slices, so offsets legitimately
    # move, but only to the one deterministic place: each slice at its own alignment boundary directly
    # after the previous slice (the first after the table). Any other offset is relocation or extra padding.
    for index in sorted(range(count), key=lambda i: arches[i].offset):
        arch = arches[index]
        if arch.align > 15:
            raise Reject("macho-invalid-fat", "fat slice alignment is implausible")
        if arch.offset + arch.size > len(data) or arch.offset < cursor:
            raise Reject("macho-invalid-fat", "fat slice lies outside the file or overlaps another slice")
        if arch.offset != align_up(cursor, 1 << arch.align):
            raise Reject("macho-invalid-fat", "fat slice is not packed at the next alignment boundary (relocation or extra padding)")
        if data[cursor : arch.offset].strip(b"\x00"):
            raise Reject("macho-invalid-fat", "non-zero bytes between fat slices")
        slice_ = MachSlice(data[arch.offset : arch.offset + arch.size])
        if (slice_.cputype & 0xFFFFFFFF, slice_.cpusubtype & 0x00FFFFFF) != arch.key:
            raise Reject("macho-invalid-fat", "fat header architecture disagrees with the slice header")
        slices[index] = slice_
        physical.append(index)
        cursor = arch.offset + arch.size
    if cursor != len(data):
        raise Reject("macho-trailing-data", "bytes follow the last fat slice")
    return "fat", [(arch, slices[index]) for index, arch in enumerate(arches)], physical  # table order, as written


def compare_slice(u: MachSlice, s: MachSlice, policy: "Policy", index: int | None):
    """Differences/allowed for one thin slice pair (both already parsed)."""
    differences: list[dict] = []
    allowed: list[dict] = []
    tag = {} if index is None else {"slice": index}

    def bad(code: str, message: str, **extra):
        differences.append(difference(code, message, **tag, **extra))

    if s.signature_command is None:
        bad("macho-slice-unsigned", "the signed image carries no LC_CODE_SIGNATURE for this slice")
        return differences, allowed
    try:
        signature = parse_signature(s)
    except Reject as reject:
        differences.append(reject.difference("signed", **tag))
        return differences, allowed

    if u.data[:4] != s.data[:4] or u.data[4:16] != s.data[4:16] or u.data[24:32] != s.data[24:32]:
        bad("macho-content-changed", "Mach-O header fields other than ncmds/sizeofcmds differ", region="header")

    cu, cs = u.non_signature_commands, s.non_signature_commands
    if [c.ident for c in cu] != [c.ident for c in cs]:
        bad("macho-content-changed", "the set or order of load commands differs", region="load-commands")
    else:
        for left, right in zip(cu, cs):
            if left.raw == right.raw:
                continue
            if left.ident == LC_SEGMENT_64 and left.raw[8:24] == b"__LINKEDIT".ljust(16, b"\x00") == right.raw[8:24]:
                continue  # compared below with only its two extent fields masked
            bad("macho-content-changed", f"{left.name} differs", region=command_region(left))

    lu, ls = u.linkedit, s.linkedit
    replaced = u.signature_command is not None
    # The whole raw segment command (address, file offset, protections, flags, section count and every
    # section header field) must match; only vmsize and filesize may change, judged by the extent check.
    if masked_linkedit_command(lu) != masked_linkedit_command(ls):
        bad("macho-content-changed", "__LINKEDIT differs beyond its extent", region="load-command:LC_SEGMENT_64:__LINKEDIT")

    # --- the linkedit extent must be exactly "old data, zero padding, signature"
    data_end = u.linkedit_data_end
    # Re-signing keeps the old blob's offset (the bytes before it are untouched); a first
    # signature goes at the next 16-byte boundary after the old data, behind zero padding.
    expected_dataoff = u.dataoff if replaced else align_up(data_end, 16)
    padding = 0 if replaced else s.dataoff - data_end
    if s.dataoff != expected_dataoff or (padding and s.data[data_end : s.dataoff].strip(b"\x00")):
        bad("macho-invalid-linkedit-extent", "signature is not placed at the aligned end of the original __LINKEDIT data")
    elif ls.filesize != s.dataoff + s.datasize - ls.fileoff:
        bad("macho-invalid-linkedit-extent", "__LINKEDIT filesize does not end at the signature")
    else:
        vm_ok = ls.vmsize == lu.vmsize and ls.vmsize >= ls.filesize or ls.vmsize == align_up(ls.filesize, s.page_size)
        if not vm_ok:
            bad(
                "macho-content-changed",
                "__LINKEDIT vmsize is neither unchanged nor the page-rounded signed size",
                region="load-command:LC_SEGMENT_64:__LINKEDIT.vmsize",
            )

    # --- everything else, byte for byte
    first = u.first_data_offset()
    if first != s.first_data_offset():
        bad("macho-content-changed", "first section data offset differs", region="sections")
    else:
        for side, image in (("unsigned", u), ("signed", s)):
            if image.data[32 + image.sizeofcmds : first].strip(b"\x00"):
                bad("macho-content-changed", "non-zero bytes between the load commands and section data", region="load-command-padding", side=side)
        named_section_difference = False
        for segment in u.segments:
            for section in segment.sections:
                if section["zerofill"] or not section["size"]:
                    continue
                span = slice(section["offset"], section["offset"] + section["size"])
                if u.data[span] != s.data[span]:
                    named_section_difference = True
                    bad("macho-content-changed", f"section {section['name']} differs", region=section["name"])
        body = slice(first, lu.fileoff)
        if u.data[body] != s.data[body] and not named_section_difference:
            bad("macho-content-changed", "segment data outside every section differs", region="segment-data")
        if u.data[lu.fileoff : data_end] != s.data[lu.fileoff : data_end]:
            bad("macho-content-changed", "__LINKEDIT data other than the signature differs", region="__LINKEDIT")

    # --- entitlements semantics: type-preserving comparison against the declared or inherited policy
    if replaced:
        try:
            before = parse_signature(u, signed=False).entitlements
        except Reject as reject:  # an unreadable or ambiguous baseline is not evidence of "no entitlements"
            differences.append(reject.difference("unsigned", **tag))
            return differences, allowed
    else:
        before = None
    wanted = policy.value if policy.value is not None else before
    if canonical_plist(signature.entitlements) != canonical_plist(wanted):
        bad("macho-entitlements-mismatch", "entitlements embedded in the signature differ from the expected policy")

    if differences:
        return differences, allowed

    if replaced:
        allowed.append({"code": "macho-code-signature-command", "action": "replaced", **tag, "dataoff": s.dataoff, "datasize": s.datasize, "previous": {"dataoff": u.dataoff, "datasize": u.datasize}})
    else:
        allowed.append({"code": "macho-load-command-count", **tag, "ncmds_delta": s.ncmds - u.ncmds, "sizeofcmds_delta": s.sizeofcmds - u.sizeofcmds})
        allowed.append({"code": "macho-code-signature-command", "action": "added", **tag, "dataoff": s.dataoff, "datasize": s.datasize})
    if (lu.vmsize, lu.filesize) != (ls.vmsize, ls.filesize):
        allowed.append(
            {
                "code": "macho-linkedit-extent",
                **tag,
                "vmsize": {"unsigned": lu.vmsize, "signed": ls.vmsize},
                "filesize": {"unsigned": lu.filesize, "signed": ls.filesize},
            }
        )
    if padding:
        allowed.append({"code": "macho-signature-alignment-padding", **tag, "offset": data_end, "size": padding})
    allowed.append({"code": "macho-code-signature-blob", **tag, **signature.report})
    return differences, allowed


class Policy:
    """The entitlement policy a comparison was run under: explicit (a plist file) or inherited."""

    def __init__(self, value: dict | None = None, file_sha256: str | None = None) -> None:
        self.value = value
        self.file_sha256 = file_sha256

    def report(self) -> dict:
        if self.value is None:
            return {"source": "inherited-from-unsigned", "file_sha256": None, "canonical_sha256": None}
        return {"source": "explicit", "file_sha256": self.file_sha256, "canonical_sha256": canonical_plist_sha256(self.value)}


def load_policy(path: Path | None) -> Policy:
    if path is None:
        return Policy()
    raw = read_regular_file(path)
    try:
        value = decode_entitlements_dict(raw, None, "macho-invalid-entitlements", "entitlements policy")
    except Reject as reject:
        raise InputError(f"cannot use entitlements policy {path}: {reject}") from reject
    return Policy(value, sha256(raw))


def compare_macho(unsigned: bytes, signed: bytes, policy: Policy) -> tuple[list, list, dict]:
    differences: list[dict] = []
    allowed: list[dict] = []
    hashes = {"unsigned_sha256": sha256(unsigned), "signed_sha256": sha256(signed)}
    containers: dict[str, tuple[str, list, list[int]]] = {}
    for side, data in (("unsigned", unsigned), ("signed", signed)):
        try:
            containers[side] = parse_macho_container(data)
        except Reject as reject:
            differences.append(reject.difference(side))
    hashes["signed_matches_invariant"] = False
    if len(containers) != 2:
        return differences, allowed, hashes, []
    (kind_u, slices_u, order_u), (kind_s, slices_s, order_s) = containers["unsigned"], containers["signed"]
    if kind_u != kind_s:
        differences.append(difference("macho-container-kind-changed", f"unsigned is {kind_u}, signed is {kind_s}"))
        return differences, allowed, hashes, []
    keys_u = sorted((a.key if a else (s.cputype & 0xFFFFFFFF, s.cpusubtype & 0xFFFFFF)) for a, s in slices_u)
    keys_s = sorted((a.key if a else (s.cputype & 0xFFFFFFFF, s.cpusubtype & 0xFFFFFF)) for a, s in slices_s)
    if keys_u != keys_s:
        differences.append(difference("macho-slice-set-changed", "architecture set differs", unsigned=keys_u, signed=keys_s))
        return differences, allowed, hashes, []
    table_u = [fat_table_entry(a) for a, _ in slices_u if a]
    table_s = [fat_table_entry(a) for a, _ in slices_s if a]
    if table_u != table_s:
        differences.append(
            difference(
                "macho-fat-metadata-changed",
                "the fat table differs in order, CPU type/subtype (including capability bits) or alignment",
                region="fat-table",
                unsigned=table_u,
                signed=table_s,
            )
        )
        return differences, allowed, hashes, []
    # The table entries now agree, so the same table index names the same architecture on both sides: comparing
    # the file-order permutation of indices compares which architecture sits where. A signing step grows slices
    # in place; it never moves one architecture past another. (Offsets then follow from the packing rule.)
    if order_u != order_s:
        differences.append(
            difference(
                "macho-fat-physical-order-changed",
                "the slices are stored in a different physical order than in the unsigned input",
                region="fat-layout",
                unsigned=order_u,
                signed=order_s,
            )
        )
        return differences, allowed, hashes, []
    identities = []
    for position, ((arch, slice_u), (_, slice_s)) in enumerate(zip(slices_u, slices_s)):
        # Full architecture identity (not only a position) so a consumer can tell which slice an entry is about.
        architecture = (
            fat_table_entry(arch)
            if arch
            else {"cputype": f"{slice_u.cputype & 0xFFFFFFFF:#010x}", "cpusubtype": f"{slice_u.cpusubtype & 0xFFFFFFFF:#010x}", "align": None}
        )
        identities.append({"index": position, "architecture": architecture})
        d, a = compare_slice(slice_u, slice_s, policy, None if arch is None else position)
        differences.extend({**item, "architecture": architecture} if "slice" in item else item for item in d)
        allowed.extend({**item, "architecture": architecture} if "slice" in item else item for item in a)
    hashes["signed_matches_invariant"] = not differences
    return differences, allowed, hashes, identities


# ================================================================ tree + driver


def _tree_roles(path: str) -> tuple[str, str] | None:
    """('seal'|'ticket', bundle) when ``path`` is a documented code-signing addition."""
    parts = path.split("/")
    for kind, tail in (("seal", ["Contents", "_CodeSignature", "CodeResources"]), ("ticket", ["Contents", "CodeResources"])):
        if parts[-len(tail) :] == tail:
            bundle = parts[: -len(tail)]
            if not bundle or bundle[-1].endswith(".app"):
                return kind, "/".join(bundle)
    return None


PE_SUFFIXES = (".exe", ".dll", ".sys", ".ocx", ".scr", ".cpl", ".efi", ".drv")


def _image_kind(data: bytes, path: str, executable: bool) -> str | None:
    """'macho' / 'pe' for anything that claims to be an executable image, else ``None`` (an ordinary resource).

    A *candidate* is classified by what it claims to be, not by whether it parses: an ``MZ`` file that
    is named like a PE or carries the execute bit is delegated to the strict reader, which rejects it
    if unreadable. Downgrading it to an ordinary resource would let a truncated executable skip the
    signing check entirely.
    """
    if data[:4] in (MH_MAGIC_64, FAT_MAGIC) + OTHER_THIN_MAGICS + (FAT_MAGIC_64,) + FAT_MAGIC_SWAPPED:
        return "macho"
    if data[:2] == b"MZ":
        if len(data) >= 0x40:
            lfanew = struct.unpack_from("<I", data, 0x3C)[0]
            if 0x40 <= lfanew <= len(data) - 4 and data[lfanew : lfanew + 4] == b"PE\x00\x00":
                return "pe"
        if executable or path.lower().endswith(PE_SUFFIXES):
            return "pe"
    return None


class TreeSource:
    """A directory or .zip/.tar payload with its deterministic inventory and bounded file reads."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        try:
            info = os.lstat(self.path)
        except OSError as error:
            raise InputError(f"cannot read {self.path}: {error.strerror}") from error
        self.archive_sha256 = None
        if stat.S_ISDIR(info.st_mode):
            self.kind = "directory"
            self.inventory = package_inputs.build_inventory(self.path)
        elif stat.S_ISREG(info.st_mode):
            self.kind = "archive"
            self.inventory = package_inputs.build_archive_inventory(self.path)
            self.archive_sha256 = sha256(read_regular_file(self.path))
        else:
            raise InputError(f"not a directory or archive: {self.path}")
        self.entries = {entry["path"]: entry for entry in self.inventory["entries"]}

    def read(self, relative: str) -> bytes:
        entry = self.entries[relative]
        if self.kind == "directory":
            data = read_regular_file(self.path.joinpath(*relative.split("/")))
        elif self.path.suffix.lower() == ".zip":
            with zipfile.ZipFile(self.path) as archive, archive.open(relative) as handle:
                data = handle.read(entry["size"] + 1)
        else:
            with tarfile.open(self.path, "r:") as archive:
                member = archive.extractfile(relative)
                data = member.read(entry["size"] + 1) if member else b""
        if len(data) != entry["size"] or sha256(data) != entry["sha256"]:
            raise InputError(f"{relative} changed while being compared")
        return data


def compare_tree(unsigned_path: Path, signed_path: Path, policy: Policy) -> tuple[list, list, dict]:
    differences: list[dict] = []
    allowed: list[dict] = []
    sources: dict[str, TreeSource] = {}
    for side, path in (("unsigned", unsigned_path), ("signed", signed_path)):
        try:
            sources[side] = TreeSource(path)
        except package_inputs.InventoryError as error:
            differences.append(difference(error.code, str(error), side=side))
    if len(sources) != 2:
        return differences, allowed, {}
    u, s = sources["unsigned"], sources["signed"]
    hashes = {
        "unsigned_inventory_sha256": u.inventory["inventory_sha256"],
        "signed_inventory_sha256": s.inventory["inventory_sha256"],
    }
    for side, source in (("unsigned", u), ("signed", s)):
        if source.archive_sha256:
            hashes[f"{side}_sha256"] = source.archive_sha256

    seals: set[str] = set()
    for path in sorted(set(u.entries) | set(s.entries)):
        left, right = u.entries.get(path), s.entries.get(path)
        if left is None:
            role = _tree_roles(path)
            if role is None:
                differences.append(difference("tree-unexpected-in-signed", "path exists only in the signed tree", path=path))
            elif role[0] == "seal":
                seals.add(role[1])
                allowed.append({"code": "tree-code-seal-added", "path": path, "size": right["size"], "sha256": right["sha256"], "verify_separately": True})
            continue
        if right is None:
            differences.append(difference("tree-missing-in-signed", "path exists only in the unsigned tree", path=path))
            continue
        data = u.read(path)
        kind = _image_kind(data, path, left["executable"])
        if kind is None:
            if (left["sha256"], left["size"]) != (right["sha256"], right["size"]):
                differences.append(difference("tree-content-changed", "file content differs", path=path))
        else:
            _delegate(kind, path, data, s, policy, differences, allowed)
        if left["executable"] != right["executable"]:
            differences.append(difference("tree-mode-changed", "execute bit differs", path=path))
    for path in sorted(set(s.entries) - set(u.entries)):
        role = _tree_roles(path)
        if role and role[0] == "ticket":
            if role[1] in seals:
                entry = s.entries[path]
                allowed.append({"code": "tree-stapled-ticket-added", "path": path, "size": entry["size"], "sha256": entry["sha256"], "verify_separately": True})
            else:
                differences.append(difference("tree-ticket-without-seal", "stapled ticket without a code-signature seal", path=path))
    for path in sorted(set(u.inventory["empty_directories"]) - set(s.inventory["empty_directories"])):
        differences.append(difference("tree-missing-in-signed", "empty directory exists only in the unsigned tree", path=path))
    for path in sorted(set(s.inventory["empty_directories"]) - set(u.inventory["empty_directories"])):
        differences.append(difference("tree-unexpected-in-signed", "empty directory exists only in the signed tree", path=path))
    allowed.sort(key=lambda item: (item.get("path", ""), item["code"]))
    return differences, allowed, hashes


def _delegate(kind, path, unsigned_data, signed_source, policy, differences, allowed):
    signed_data = signed_source.read(path)
    try:
        if kind == "pe":
            d, a, _ = compare_pe(unsigned_data, signed_data)
        else:
            d, a, _, _ = compare_macho(unsigned_data, signed_data, policy)
    except InputError as error:
        differences.append(difference("tree-unparseable-executable", str(error), path=path))
        return
    unsigned_codes = {"macho-slice-unsigned", "pe-signed-input-has-no-certificate"}
    if any(item["code"] in unsigned_codes for item in d):
        differences.append(difference("tree-executable-not-signed", f"{kind} image was not signed", path=path))
    differences.extend({**item, "path": path} for item in d)
    allowed.extend({**item, "path": path} for item in a)


def compare(unsigned: Path, signed: Path, kind: str, *, entitlements: Path | None = None) -> dict:
    """Compare ``signed`` against ``unsigned``; raises ``InputError`` when no comparison is possible."""
    if kind not in KINDS:
        raise InputError(f"unsupported --kind {kind!r}; expected one of {', '.join(KINDS)}")
    unsigned, signed = Path(unsigned), Path(signed)
    policy = load_policy(entitlements) if kind != "pe" else Policy()
    extra: dict[str, Any] = {}
    if kind == "tree":
        differences, allowed, hashes = compare_tree(unsigned, signed, policy)
    else:
        data = (read_regular_file(unsigned), read_regular_file(signed))
        if kind == "pe":
            differences, allowed, hashes = compare_pe(*data)
        else:
            differences, allowed, hashes, extra["slices"] = compare_macho(*data, policy)
    if kind != "pe":
        # Self-describing evidence: which entitlement policy this verdict was produced under.
        extra["entitlement_policy"] = policy.report()
    return {
        "schema": SCHEMA,
        "verdict": "PASS" if not differences else "FAIL",
        "kind": kind,
        "differences": differences,
        # On FAIL nothing is "approved": a partial allowlist must not be mistaken for acceptance.
        "allowed_differences": allowed if not differences else [],
        "input_hashes": hashes,
        **extra,
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse would print usage and exit 2 itself; keep it one code path
        raise InputError(f"usage: {message}")


def _write_report(path: Path, document: dict) -> None:
    if not path.parent.is_dir() or path.is_dir():
        raise InputError(f"cannot write report {path}")
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(canonical_json(document))
        os.replace(temporary, path)
    except OSError as error:
        raise InputError(f"cannot write report {path}: {error.strerror}") from error


def _fail_closed(report_path: Path | None, console: str, recorded: str) -> int:
    """Exit 2 and replace any stale report with an ERROR document (never leave an old PASS behind)."""
    print(f"correspondence: {console}", file=sys.stderr)
    if report_path is not None:
        try:
            _write_report(report_path, {"schema": SCHEMA, "verdict": "ERROR", "error": recorded})
        except InputError:
            pass
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _Parser(
        description="Verify that a signed image/tree differs from its unsigned input only by signature material.",
        allow_abbrev=False,  # an abbreviated --report would otherwise be accepted but invisible to the recovery scan above
    )
    parser.add_argument("--unsigned", type=Path, required=True)
    parser.add_argument("--signed", type=Path, required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--entitlements", type=Path, help="expected entitlements plist (macho/tree); default: unchanged")
    # Argument errors must still replace a stale report, so find --report before parsing, in both spellings
    # argparse accepts (`--report PATH`, `--report=PATH`); the last occurrence wins, as it does in argparse.
    report_path = None
    for index, token in enumerate(argv):
        if token == "--report" and index + 1 < len(argv):
            report_path = Path(argv[index + 1])
        elif token.startswith("--report="):
            report_path = Path(token.partition("=")[2])
    try:
        if argv in (["-h"], ["--help"]):
            parser.print_help()
            return 0
        args = parser.parse_args(argv)
        report_path = args.report
        document = compare(args.unsigned, args.signed, args.kind, entitlements=args.entitlements)
        _write_report(args.report, document)
        return 0 if document["verdict"] == "PASS" else 1
    except InputError as error:
        return _fail_closed(report_path, f"invalid input: {error}", str(error))
    except Exception as error:  # noqa: BLE001 - the tool cannot account for this input; never PASS, never a traceback
        message = f"internal error ({type(error).__name__}); the comparison could not be completed"
        return _fail_closed(report_path, message, message)


if __name__ == "__main__":
    sys.exit(main())
