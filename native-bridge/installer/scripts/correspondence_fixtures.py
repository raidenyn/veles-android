#!/usr/bin/env python3
"""Deterministic fixture builders for the OTP-25 correspondence tests (Task 9).

Standard library only. This module is test infrastructure: it takes the two REAL
base images committed under ``fixtures/correspondence/`` (a PE32+ image linked by
the pinned Rust 1.98.0 toolchain for ``x86_64-pc-windows-msvc`` and an arm64
Mach-O linked for ``aarch64-apple-darwin``, both with ``rust-lld``) and derives
signature-carrying variants from them **locally and deterministically**.

What this module produces is *locally constructed*, structurally valid
signature containers - a WIN_CERTIFICATE table, an LC_CODE_SIGNATURE command with
an embedded-signature SuperBlob and the ``__LINKEDIT`` growth that goes with it.
It is deliberately independent of ``correspondence.py`` (it re-implements the
byte layouts with ``struct``) so a comparator bug cannot hide behind a matching
builder bug. None of the cryptographic content is a real Authenticode or Apple
signature: the CMS bytes are an opaque placeholder. Native signing on Windows and
macOS is a separate, unrun step recorded in ``fixtures/PROVENANCE.md``.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE_DIR = HERE / "fixtures" / "correspondence"
PE_BASE = FIXTURE_DIR / "veles-fixture-x64.exe"
MACHO_BASE = FIXTURE_DIR / "veles-fixture-arm64.macho"

LC_SEGMENT_64 = 0x19
LC_CODE_SIGNATURE = 0x1D
LC_UUID = 0x1B

CPU_TYPE_X86_64 = 0x01000007
CPU_TYPE_ARM64 = 0x0100000C

def placeholder_cms(index: int = 0) -> bytes:
    """An opaque DER ``SEQUENCE`` wrapper (outer PKCS#7 shape) around a recognisable placeholder.

    The length field is correct so strict readers accept the *shape*; the body is
    NOT a CMS structure and carries no signature. ``index`` makes certificates distinct.
    """
    body = b"LOCALLY-CONSTRUCTED-PLACEHOLDER-NOT-A-REAL-CMS-SIGNATURE" + bytes(range(64)) + bytes([index])
    return b"\x30\x82" + struct.pack(">H", len(body)) + body


PLACEHOLDER_CMS = placeholder_cms()
ENTITLEMENTS_PLIST = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
    b"<key>com.apple.security.cs.allow-jit</key><false/></dict></plist>\n"
)


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


# --------------------------------------------------------------------------- PE


def pe_offsets(data: bytes) -> dict[str, int]:
    """Offsets of the fields Authenticode touches in a PE32+ image."""
    lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    opt = lfanew + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    assert magic == 0x20B, "fixture builder supports PE32+ only"
    return {
        "lfanew": lfanew,
        "optional": opt,
        "checksum": opt + 64,
        "security": opt + 112 + 4 * 8,
    }


def pe_checksum(data: bytes, checksum_offset: int) -> int:
    """The documented ImageHlp checksum (16-bit ones-complement sum + length)."""
    total = 0
    padded = data + (b"\x00" if len(data) % 2 else b"")
    for index in range(0, len(padded), 2):
        if index in (checksum_offset, checksum_offset + 2):
            continue
        total += padded[index] | (padded[index + 1] << 8)
        total = (total & 0xFFFFFFFF) + (total >> 32)
    total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (total + len(data)) & 0xFFFFFFFF


def win_certificate(blob: bytes) -> bytes:
    """One WIN_CERTIFICATE (revision 2.0, PKCS#7) padded to an 8-byte boundary."""
    entry = struct.pack("<IHH", 8 + len(blob), 0x0200, 0x0002) + blob
    return entry + b"\x00" * (-len(entry) % 8)


def pe_sign(base: bytes, *, certificates: int = 1) -> bytes:
    """Return ``base`` with a terminal certificate table and recomputed checksum."""
    offsets = pe_offsets(base)
    assert struct.unpack_from("<II", base, offsets["security"]) == (0, 0)
    body = bytearray(base)
    body.extend(b"\x00" * (-len(body) % 8))
    table_offset = len(body)
    table = b"".join(win_certificate(placeholder_cms(index)) for index in range(certificates))
    body.extend(table)
    struct.pack_into("<II", body, offsets["security"], table_offset, len(table))
    struct.pack_into("<I", body, offsets["checksum"], 0)
    struct.pack_into("<I", body, offsets["checksum"], pe_checksum(bytes(body), offsets["checksum"]))
    return bytes(body)


# ----------------------------------------------------------------------- Mach-O


def _macho_commands(data: bytes, base: int = 0) -> list[tuple[int, int, int]]:
    """(command id, offset, size) for every load command of a thin 64-bit slice."""
    ncmds, sizeofcmds = struct.unpack_from("<II", data, base + 16)
    out = []
    offset = base + 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, offset)
        out.append((cmd, offset, size))
        offset += size
    assert offset - (base + 32) == sizeofcmds
    return out


def macho_strip_signature(data: bytes) -> bytes:
    """Remove a trailing LC_CODE_SIGNATURE and its blob from a thin slice.

    Locally constructed: yields the "no signature yet" shape ``codesign`` starts
    from when the linker did not ad-hoc sign the output.
    """
    commands = _macho_commands(data)
    cmd, offset, size = commands[-1]
    assert cmd == LC_CODE_SIGNATURE and size == 16
    dataoff, _datasize = struct.unpack_from("<II", data, offset + 8)
    body = bytearray(data[:dataoff])
    body[offset : offset + 16] = b"\x00" * 16
    ncmds, sizeofcmds = struct.unpack_from("<II", body, 16)
    struct.pack_into("<II", body, 16, ncmds - 1, sizeofcmds - 16)
    _set_linkedit(body, dataoff, vmsize_policy="delta", original=data)
    return bytes(body)


def _linkedit(body: bytes | bytearray) -> tuple[int, int]:
    """(offset of the __LINKEDIT command, fileoff)."""
    for cmd, offset, _size in _macho_commands(body):
        if cmd == LC_SEGMENT_64 and body[offset + 8 : offset + 24].rstrip(b"\x00") == b"__LINKEDIT":
            return offset, struct.unpack_from("<Q", body, offset + 40)[0]
    raise AssertionError("no __LINKEDIT")


def _set_linkedit(
    body: bytearray, content_end: int, *, vmsize_policy: str, original: bytes | None = None
) -> None:
    offset, fileoff = _linkedit(body)
    filesize = content_end - fileoff
    old_vmsize, old_filesize = struct.unpack_from("<QQ", original if original else body, offset + 32)[0], (
        struct.unpack_from("<Q", original if original else body, offset + 48)[0]
    )
    if vmsize_policy == "page":
        # arm64 uses 16 KiB pages, x86_64 4 KiB; the segment is rounded to the slice's own page.
        cputype = struct.unpack_from("<i", body, 4)[0] & 0xFFFFFFFF
        vmsize = align_up(filesize, 0x4000 if cputype == CPU_TYPE_ARM64 else 0x1000)
    elif vmsize_policy == "delta":
        vmsize = old_vmsize + (filesize - old_filesize)
    else:
        raise AssertionError(vmsize_policy)
    struct.pack_into("<Q", body, offset + 32, vmsize)
    struct.pack_into("<Q", body, offset + 48, filesize)


def code_signature_blob(
    signed_prefix: bytes,
    *,
    identifier: bytes = b"me.nagaev.veles.fixture",
    entitlements: bytes | None = ENTITLEMENTS_PLIST,
    flags: int = 0x10000,
) -> bytes:
    """A structurally valid embedded-signature SuperBlob (big-endian, Apple layout).

    Contains a CodeDirectory (SHA-256, 4 KiB pages, correct page hashes over
    ``signed_prefix``), an optional entitlements blob and the opaque placeholder
    CMS wrapper. Locally constructed; no real signature.
    """
    page = 4096
    n_special = 5
    pages = [signed_prefix[i : i + page] for i in range(0, len(signed_prefix), page)] or [b""]
    header_size = 88
    ident_off = header_size
    hash_off = ident_off + len(identifier) + 1
    hash_off = align_up(hash_off, 4) + n_special * 32
    entitlements_blob = b""
    if entitlements is not None:
        entitlements_blob = struct.pack(">II", 0xFADE7171, 8 + len(entitlements)) + entitlements
    special = [b"\x00" * 32] * n_special
    if entitlements is not None:
        special[0] = hashlib.sha256(entitlements_blob).digest()  # slot 5 is stored first
    page_hashes = b"".join(hashlib.sha256(p).digest() for p in pages)
    cd_length = hash_off + len(page_hashes)
    cd = bytearray(cd_length)
    struct.pack_into(
        ">IIIIIIIII",
        cd,
        0,
        0xFADE0C02,
        cd_length,
        0x20400,
        flags,
        hash_off,
        ident_off,
        n_special,
        len(pages),
        len(signed_prefix),
    )
    cd[36:40] = bytes([32, 2, 0, 12])  # hashSize, hashType=SHA256, platform, pageSize=2^12
    cd[ident_off : ident_off + len(identifier) + 1] = identifier + b"\x00"
    special_start = hash_off - n_special * 32
    cd[special_start:hash_off] = b"".join(special)
    cd[hash_off:] = page_hashes
    wrapper = struct.pack(">II", 0xFADE0B01, 8 + len(PLACEHOLDER_CMS)) + PLACEHOLDER_CMS
    blobs: list[tuple[int, bytes]] = [(0, bytes(cd))]
    if entitlements is not None:
        blobs.append((5, entitlements_blob))
    blobs.append((0x10000, wrapper))
    index_size = 12 + 8 * len(blobs)
    offset = index_size
    index = bytearray()
    body = bytearray()
    for slot, blob in blobs:
        index += struct.pack(">II", slot, offset + len(body))
        body += blob
    total = index_size + len(body)
    return struct.pack(">III", 0xFADE0CC0, total, len(blobs)) + bytes(index) + bytes(body)


def macho_sign(data: bytes, **blob_args: object) -> bytes:
    """Sign (or re-sign) a thin slice the way ``codesign`` lays it out.

    Appends a 16-byte aligned SuperBlob to ``__LINKEDIT``, adds or rewrites the
    LC_CODE_SIGNATURE command, grows ``__LINKEDIT`` filesize and rounds its vmsize
    to the slice's page size (16 KiB arm64, 4 KiB x86_64).
    """
    if _macho_commands(data)[-1][0] == LC_CODE_SIGNATURE:
        data = macho_strip_signature(data)
    body = bytearray(data)
    ncmds, sizeofcmds = struct.unpack_from("<II", body, 16)
    cmd_offset = 32 + sizeofcmds
    assert body[cmd_offset : cmd_offset + 16] == b"\x00" * 16, "no room for LC_CODE_SIGNATURE"
    _offset, fileoff = _linkedit(body)
    linkedit_offset, _ = _linkedit(body)
    content_end = fileoff + struct.unpack_from("<Q", body, linkedit_offset + 48)[0]
    assert content_end == len(body)
    dataoff = align_up(content_end, 16)
    body.extend(b"\x00" * (dataoff - content_end))
    sizing = len(code_signature_blob(bytes(dataoff), **blob_args))  # size is content independent
    struct.pack_into("<II", body, cmd_offset, LC_CODE_SIGNATURE, 16)
    struct.pack_into("<II", body, cmd_offset + 8, dataoff, sizing)
    struct.pack_into("<II", body, 16, ncmds + 1, sizeofcmds + 16)
    _set_linkedit(body, dataoff + sizing, vmsize_policy="page")
    blob = code_signature_blob(bytes(body[:dataoff]), **blob_args)
    assert len(blob) == sizing
    body.extend(blob)
    return bytes(body)


def macho_fat(slices: list[tuple[int, int, int, bytes]], *, sizes_override: dict[int, int] | None = None) -> bytes:
    """Wrap ``(cputype, cpusubtype, align, bytes)`` slices in a 32-bit fat header."""
    header_size = 8 + 20 * len(slices)
    layout = []
    cursor = header_size
    for cputype, cpusubtype, align, blob in slices:
        offset = align_up(cursor, 1 << align)
        layout.append((cputype, cpusubtype, offset, len(blob), align, blob))
        cursor = offset + len(blob)
    out = bytearray(struct.pack(">II", 0xCAFEBABE, len(slices)))
    for index, (cputype, cpusubtype, offset, size, align, _blob) in enumerate(layout):
        if sizes_override and index in sizes_override:
            size = sizes_override[index]
        out += struct.pack(">IIIII", cputype & 0xFFFFFFFF, cpusubtype, offset, size, align)
    for _cputype, _sub, offset, _size, _align, blob in layout:
        out.extend(b"\x00" * (offset - len(out)))
        out += blob
    return bytes(out)


def macho_retarget(data: bytes, cputype: int, cpusubtype: int = 3) -> bytes:
    """Locally constructed second-architecture slice: only the header CPU fields differ."""
    body = bytearray(data)
    struct.pack_into("<ii", body, 4, cputype - (1 << 32) if cputype >= 1 << 31 else cputype, cpusubtype)
    return bytes(body)


def read_pe_base() -> bytes:
    return PE_BASE.read_bytes()


def read_macho_base() -> bytes:
    return MACHO_BASE.read_bytes()


def derived_fixtures() -> dict[str, bytes]:
    """Every locally constructed image the provenance records, by stable name."""
    pe = read_pe_base()
    macho = read_macho_base()
    stripped = macho_strip_signature(macho)
    return {
        "pe-x64-unsigned.exe": pe,
        "pe-x64-signed-constructed.exe": pe_sign(pe),
        "macho-arm64-linker-signed.bin": macho,
        "macho-arm64-no-signature-constructed.bin": stripped,
        "macho-arm64-signed-constructed.bin": macho_sign(stripped),
        "macho-arm64-resigned-constructed.bin": macho_sign(
            macho, entitlements=None, identifier=b"me.nagaev.veles.fixture.resigned"
        ),
    }


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":  # pragma: no cover - convenience for the provenance table
    for name, blob in derived_fixtures().items():
        print(f"{sha256(blob)}  {len(blob):6d}  {name}")
