#!/usr/bin/env python3
"""Signed-content correspondence tests for the OTP-25 standalone installers (Task 9).

Standard library only. Run from the repository root:

    python3 -m unittest discover -s native-bridge/installer/scripts -p test_correspondence.py

Fixture honesty
---------------
Two kinds of input are used and they are never conflated:

* **Real toolchain output** - ``fixtures/correspondence/veles-fixture-x64.exe`` (PE32+,
  linked by the pinned Rust 1.98.0 toolchain with ``rust-lld``) and
  ``veles-fixture-arm64.macho`` (arm64 Mach-O linked with ``ld64.lld``, carrying the
  linker's own ad-hoc signature). They were cross-linked on a Linux x86_64 host - not on
  Windows/macOS runners - and are pinned by SHA-256 below.
* **Locally constructed images** - everything else, assembled deterministically from the
  published on-disk formats by the builders in this file (hand-rolled PE32/PE32+, thin and
  fat Mach-O, ``.app`` trees) or derived from the real images by the independent builders in
  ``correspondence_fixtures.py``. Their WIN_CERTIFICATE tables, CodeDirectories and CMS
  wrappers are structurally valid **placeholders**: no Authenticode or Apple signature, no
  notarization and no native signing run exists anywhere in this suite.

Authenticode and Apple code signing cannot run on the Linux host that develops this task
(no ``signtool``, no ``xcrun``/``codesign``). What is therefore unproven is whether the real
tools mutate images exactly as the allowlist assumes; ``fixtures/PROVENANCE.md`` lists the
blocked native steps and the commands that would produce real signed fixtures.
"""

import hashlib
import io
import json
import lzma
import plistlib
import struct
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
import warnings
import zipfile
import zlib
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import correspondence  # noqa: E402  (sibling module, imported after sys.path setup)
import correspondence_fixtures as fx  # noqa: E402
import package_inputs  # noqa: E402
import test_package_inputs as raw_zip  # noqa: E402  (shared raw-ZIP header helpers, no TestCase imported)

SCRIPT = HERE / "correspondence.py"


# ---------------------------------------------------------------------------
# PE builder (PE32 / PE32+), deterministic.
# ---------------------------------------------------------------------------

FILE_ALIGN = 0x200
SECTION_ALIGN = 0x1000
SIZE_OF_HEADERS = 0x400


def align(value, boundary):
    return (value + boundary - 1) // boundary * boundary


def build_resource_section(rva):
    """A real three-level resource tree: RT_VERSION / id 1 / lang 0x0409 -> data."""
    payload = b"VS_VERSION_INFO-fixture\x00" * 3
    root = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 16, 0x80000000 | 0x18)
    name = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 1, 0x80000000 | 0x30)
    lang = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", 0x0409, 0x48)
    assert len(root) == 0x18 and len(name) == 0x18 and len(lang) == 0x18
    data_rva = rva + 0x58
    entry = struct.pack("<IIII", data_rva, len(payload), 1252, 0)
    blob = root + name + lang + entry
    assert len(blob) == 0x58
    return blob + payload


def build_pe(
    *,
    pe32plus=True,
    machine=0x8664,
    text=None,
    overlay=b"",
    timestamp=0x5F000000,
    subsystem=3,
    checksum=0,
    cert_offset=0,
    cert_size=0,
    section_overrides=None,
    extra_section_header=None,
    gap_before_rdata=0,
):
    text = text if text is not None else bytes(range(256)) * 3 + b"\xc3"
    rdata = b"VELES-RDATA-FIXTURE\x00" * 20
    text_rva, rdata_rva, rsrc_rva = 0x1000, 0x2000, 0x3000
    rsrc = build_resource_section(rsrc_rva)
    raws = [(b".text\0\0\0", text, 0x60000020), (b".rdata\0\0", rdata, 0x40000040), (b".rsrc\0\0\0", rsrc, 0x40000040)]
    rvas = [text_rva, rdata_rva, rsrc_rva]

    opt_size = 240 if pe32plus else 224
    nsections = len(raws) + (1 if extra_section_header else 0)
    headers_end = 0x80 + 4 + 20 + opt_size + 40 * nsections
    assert headers_end <= SIZE_OF_HEADERS

    sections = []
    rawptr = SIZE_OF_HEADERS
    body = bytearray()
    raw_offsets = []
    for (name, data, chars), rva in zip(raws, rvas):
        if name.startswith(b".rdata") and gap_before_rdata:
            body += b"\0" * gap_before_rdata  # unreferenced bytes between sections
            rawptr += gap_before_rdata
        raw_offsets.append(rawptr)
        rawsize = align(len(data), FILE_ALIGN)
        header = struct.pack("<8sIIIIIIHHI", name, len(data), rva, rawsize, rawptr, 0, 0, 0, 0, chars)
        sections.append(header)
        body += data.ljust(rawsize, b"\0")
        rawptr += rawsize
    if extra_section_header:
        sections.append(extra_section_header)
    if section_overrides:
        for index, patch in section_overrides.items():
            header = bytearray(sections[index])
            for offset, fmt, value in patch:
                struct.pack_into(fmt, header, offset, value)
            sections[index] = bytes(header)

    size_of_image = rsrc_rva + SECTION_ALIGN
    magic = 0x20B if pe32plus else 0x10B
    if pe32plus:
        opt = struct.pack(
            "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
            magic, 14, 0, len(text), len(rdata) + len(rsrc), 0, text_rva, text_rva,
            0x140000000, SECTION_ALIGN, FILE_ALIGN, 6, 0, 0, 0, 6, 0, 0,
            size_of_image, SIZE_OF_HEADERS, checksum, subsystem, 0x8160,
            0x100000, 0x1000, 0x100000, 0x1000, 0, 16,
        )
    else:
        opt = struct.pack(
            "<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII",
            magic, 14, 0, len(text), len(rdata) + len(rsrc), 0, text_rva, text_rva, rdata_rva,
            0x400000, SECTION_ALIGN, FILE_ALIGN, 6, 0, 0, 0, 6, 0, 0,
            size_of_image, SIZE_OF_HEADERS, checksum, subsystem, 0x8140,
            0x100000, 0x1000, 0x100000, 0x1000, 0, 16,
        )
    dirs = bytearray(16 * 8)
    struct.pack_into("<II", dirs, 2 * 8, rsrc_rva, len(rsrc))
    struct.pack_into("<II", dirs, 4 * 8, cert_offset, cert_size)
    opt += bytes(dirs)
    assert len(opt) == opt_size, (len(opt), opt_size)

    coff = struct.pack("<HHIIIHH", machine, nsections, timestamp, 0, 0, opt_size, 0x0022)
    dos = b"MZ" + b"\0" * 0x3A + struct.pack("<I", 0x80)
    dos += b"This program cannot be run in DOS mode.\r\r\n$".ljust(0x40, b"\0")
    header = dos + b"PE\0\0" + coff + opt + b"".join(sections)
    header = header.ljust(SIZE_OF_HEADERS, b"\0")
    meta = {
        "checksum_offset": 0x80 + 4 + 20 + 64,
        "secdir_offset": 0x80 + 4 + 20 + (112 if pe32plus else 96) + 4 * 8,
        "text_offset": SIZE_OF_HEADERS,
        "rdata_offset": raw_offsets[1],
        "rsrc_offset": raw_offsets[2],
        "gap_offset": SIZE_OF_HEADERS + align(len(text), FILE_ALIGN),
        "timestamp_offset": 0x80 + 4 + 4,
        "section_table_offset": 0x80 + 4 + 20 + opt_size,
        "file_end": len(header) + len(body),
    }
    return bytes(header) + bytes(body) + overlay, meta


def win_certificate(blob, *, revision=0x0200, cert_type=0x0002):
    entry = struct.pack("<IHH", 8 + len(blob), revision, cert_type) + blob
    return entry.ljust(align(len(entry), 8), b"\0")


def der_sequence(body):
    """A DER SEQUENCE (the outer shape of a PKCS#7 ContentInfo) around opaque ``body``."""
    assert 0x100 <= len(body) <= 0xFFFF
    return b"\x30\x82" + struct.pack(">H", len(body)) + body


PKCS7_FIXTURE = der_sequence(bytes((i * 7 + 3) & 0xFF for i in range(0xFFB)))
PKCS7_FIXTURE_TWO = der_sequence(bytes((i * 11 + 5) & 0xFF for i in range(0x400)))


def authenticode_like(unsigned, meta, *, checksum=0x00ABCDEF, certs=None, pad=None):
    """Apply the documented Authenticode file mutation to a built PE.

    Mutates exactly: the optional-header checksum, the security data-directory entry,
    zero padding up to 8-byte alignment, and a terminal WIN_CERTIFICATE table. This is
    a documented-format model, not signtool output.
    """
    certs = certs if certs is not None else [win_certificate(PKCS7_FIXTURE)]
    table = b"".join(certs)
    offset = align(len(unsigned), 8)
    padding = (b"\0" * (offset - len(unsigned))) if pad is None else pad
    image = bytearray(unsigned) + padding
    struct.pack_into("<I", image, meta["checksum_offset"], checksum)
    struct.pack_into("<II", image, meta["secdir_offset"], offset, len(table))
    return bytes(image) + table


# ---------------------------------------------------------------------------
# Mach-O builder (thin 64-bit little-endian + fat), deterministic.
# ---------------------------------------------------------------------------

CPU_ARM64 = 0x0100000C
CPU_X86_64 = 0x01000007
LC_SEGMENT_64, LC_SYMTAB, LC_UUID, LC_BUILD_VERSION, LC_MAIN = 0x19, 0x2, 0x1B, 0x32, 0x80000028
LC_FUNCTION_STARTS, LC_CODE_SIGNATURE = 0x26, 0x1D

CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_CODEDIRECTORY = 0xFADE0C02
CSMAGIC_REQUIREMENTS = 0xFADE0C01
CSMAGIC_ENTITLEMENTS = 0xFADE7171
CSMAGIC_DER_ENTITLEMENTS = 0xFADE7172
CSMAGIC_BLOBWRAPPER = 0xFADE0B01
SIG_DATASIZE = 0x800


def page_size(cputype):
    return 0x4000 if cputype == CPU_ARM64 else 0x1000


def segment(name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, sections=()):
    body = struct.pack(
        "<II16sQQQQiiII",
        LC_SEGMENT_64, 72 + 80 * len(sections), name.encode().ljust(16, b"\0"),
        vmaddr, vmsize, fileoff, filesize, maxprot, initprot, len(sections), 0,
    )
    return body + b"".join(sections)


def section(sectname, segname, addr, size, offset, flags=0):
    return struct.pack(
        "<16s16sQQIIIIIIII",
        sectname.encode().ljust(16, b"\0"), segname.encode().ljust(16, b"\0"),
        addr, size, offset, 2, 0, 0, flags, 0, 0, 0,
    )


def build_macho(
    *,
    cputype=CPU_ARM64,
    cpusubtype=None,
    text=b"\x1f\x20\x03\xd5" * 64,
    uuid=bytes(range(0x10, 0x20)),
    symbols=b"\0_main\0\0",
    signature=None,
    extra_load_command=None,
    trailing=b"",
    linkedit_vmsize=None,
    cmd_order_swap=False,
    ncmds_delta=0,
    header_flags=0x00200085,
    slack=None,
    low_slack=None,
    pad_fill=None,
    linkedit_sections=(),
    pre_symtab_command=None,
    symtab_ident=LC_SYMTAB,
):
    """Build one thin Mach-O. ``signature`` is the exact bytes of the signature region
    (already padded to its allocated size) or ``None`` for an image without
    LC_CODE_SIGNATURE."""
    page = page_size(cputype)
    cpusubtype = (0 if cputype == CPU_ARM64 else 3) if cpusubtype is None else cpusubtype
    text_off = 0x800
    cstring = b"veles-fixture\0"
    cstring_off = text_off + align(len(text), 16)
    assert cstring_off + len(cstring) < page

    funcstarts = b"\x01\x02\x03\x04" * 4  # 16 bytes
    nlist = struct.pack("<IBBHQ", 1, 0x0F, 1, 0, 0x100000000 + text_off) + struct.pack("<IBBHQ", 0, 0, 0, 0, 0)
    link_base = page
    fs_off = link_base
    sym_off = fs_off + len(funcstarts)
    str_off = sym_off + len(nlist)
    link_end = str_off + len(symbols)
    sig_off = align(link_end, 16)

    content_end = link_end if signature is None else sig_off + len(signature)
    linkedit_filesize = content_end - link_base
    vmsize = linkedit_vmsize if linkedit_vmsize is not None else align(linkedit_filesize, page)

    text_sections = [
        section("__text", "__TEXT", 0x100000000 + text_off, len(text), text_off, 0x80000400),
        section("__cstring", "__TEXT", 0x100000000 + cstring_off, len(cstring), cstring_off, 0x2),
    ]
    cmds = [
        segment("__PAGEZERO", 0, 0x100000000, 0, 0, 0, 0),
        segment("__TEXT", 0x100000000, page, 0, page, 5, 5, text_sections),
        segment("__LINKEDIT", 0x100000000 + page, vmsize, link_base, linkedit_filesize, 1, 1, tuple(linkedit_sections)),
        struct.pack("<IIIIII", symtab_ident, 24, sym_off, 2, str_off, len(symbols)),
        struct.pack("<II", LC_UUID, 24) + uuid,
        struct.pack("<IIIIII", LC_BUILD_VERSION, 24, 1, 0x00100000, 0x00100000, 0),
        struct.pack("<IIQQ", LC_MAIN, 24, text_off, 0),
        struct.pack("<IIII", LC_FUNCTION_STARTS, 16, fs_off, len(funcstarts)),
    ]
    if pre_symtab_command is not None:
        cmds.insert(3, pre_symtab_command)  # ahead of LC_SYMTAB
    if extra_load_command is not None:
        cmds.append(extra_load_command)
    if signature is not None:
        cmds.append(struct.pack("<IIII", LC_CODE_SIGNATURE, 16, sig_off, len(signature)))
    if cmd_order_swap:
        cmds[3], cmds[4] = cmds[4], cmds[3]
    sizeofcmds = sum(len(c) for c in cmds)
    header = struct.pack("<IiiIIIII", 0xFEEDFACF, cputype, cpusubtype, 2, len(cmds) + ncmds_delta, sizeofcmds, header_flags, 0)
    image = bytearray(page + linkedit_filesize)
    image[: len(header)] = header
    cursor = len(header)
    for c in cmds:
        image[cursor : cursor + len(c)] = c
        cursor += len(c)
    image[text_off : text_off + len(text)] = text
    image[cstring_off : cstring_off + len(cstring)] = cstring
    image[fs_off : fs_off + len(funcstarts)] = funcstarts
    image[sym_off : sym_off + len(nlist)] = nlist
    image[str_off : str_off + len(symbols)] = symbols
    if slack is not None:
        image[0xC00 : 0xC00 + len(slack)] = slack  # inside __TEXT, outside every section
    if low_slack is not None:
        image[0x700 : 0x700 + len(low_slack)] = low_slack  # between the load commands and section data
    if pad_fill is not None:
        image[link_end:sig_off] = pad_fill * (sig_off - link_end)  # alignment padding before the signature
    if signature is not None:
        image[sig_off : sig_off + len(signature)] = signature
    return bytes(image) + trailing, {"sig_off": sig_off, "page": page, "text_off": text_off, "link_base": link_base,
                                     "sym_off": sym_off, "uuid_cmd": None}


def _hash_pages(data, code_limit, log2):
    size = 1 << log2
    return b"".join(hashlib.sha256(data[i : i + size]).digest() for i in range(0, code_limit, size))


def build_signature_blob(
    prefix, *, entitlements=None, tamper_slot=False, bad_magic=False, omit_cms=False, cd_flags=0x10000, der_entitlements=None
):
    """A SuperBlob with a real CodeDirectory over ``prefix`` (the bytes before the
    signature), an empty requirements set, optional entitlements and a CMS wrapper
    stand-in. Padded to SIG_DATASIZE."""
    ident = b"me.nagaev.veles.native-host\0"
    code_limit = len(prefix)
    log2 = 12
    n_code = (code_limit + (1 << log2) - 1) >> log2
    requirements = struct.pack(">III", CSMAGIC_REQUIREMENTS, 12, 0)
    ent_blob = None
    if entitlements is not None:
        payload = plistlib.dumps(entitlements, fmt=plistlib.FMT_XML)
        ent_blob = struct.pack(">II", CSMAGIC_ENTITLEMENTS, 8 + len(payload)) + payload
    special = {2: hashlib.sha256(requirements).digest()}
    if ent_blob is not None:
        special[5] = hashlib.sha256(ent_blob).digest()
    der_blob = None
    if der_entitlements is not None:  # correctly hashed slot 7, arbitrary payload
        der_blob = struct.pack(">II", CSMAGIC_DER_ENTITLEMENTS, 8 + len(der_entitlements)) + der_entitlements
        special[7] = hashlib.sha256(der_blob).digest()
    n_special = max(special)
    hash_size = 32
    fixed = 48  # CodeDirectory v0x20100: through scatterOffset
    ident_off = fixed
    hash_off = ident_off + len(ident) + n_special * hash_size
    code_hashes = _hash_pages(prefix, code_limit, log2)
    if tamper_slot:
        code_hashes = bytes([code_hashes[0] ^ 1]) + code_hashes[1:]
    specials = b"".join(special.get(slot, b"\0" * hash_size) for slot in range(n_special, 0, -1))
    length = hash_off + n_code * hash_size
    directory = struct.pack(
        ">IIIIIIIIIBBBBII",
        CSMAGIC_CODEDIRECTORY, length, 0x20100, cd_flags, hash_off, ident_off, n_special, n_code,
        code_limit, hash_size, 2, 0, log2, 0, 0,
    )
    assert len(directory) == fixed, len(directory)
    directory += ident + specials + code_hashes
    assert len(directory) == length
    cms = struct.pack(">II", CSMAGIC_BLOBWRAPPER, 8 + 24) + b"CMS-STAND-IN-FIXTURE-\x00\x00\x00"
    blobs = [(0, directory), (2, requirements)]
    if ent_blob is not None:
        blobs.append((5, ent_blob))
    if der_blob is not None:
        blobs.append((7, der_blob))
    if not omit_cms:
        blobs.append((0x10000, cms))
    index_len = 12 + 8 * len(blobs)
    offset = index_len
    index, body = b"", b""
    for slot, blob in blobs:
        index += struct.pack(">II", slot, offset)
        body += blob
        offset += len(blob)
    total = index_len + len(body)
    magic = 0xFADE0CC1 if bad_magic else CSMAGIC_EMBEDDED_SIGNATURE
    superblob = struct.pack(">III", magic, total, len(blobs)) + index + body
    assert total <= SIG_DATASIZE
    return superblob.ljust(SIG_DATASIZE, b"\0")


def build_signed_macho(
    *, entitlements=None, tamper_slot=False, bad_magic=False, omit_cms=False, cd_flags=0x10000, der_entitlements=None, **kwargs
):
    kwargs = dict(kwargs)
    placeholder, meta = build_macho(signature=b"\0" * SIG_DATASIZE, **kwargs)
    prefix = placeholder[: meta["sig_off"]]
    # The prefix includes the load commands, which already carry dataoff/datasize.
    blob = build_signature_blob(
        prefix,
        entitlements=entitlements,
        tamper_slot=tamper_slot,
        bad_magic=bad_magic,
        omit_cms=omit_cms,
        cd_flags=cd_flags,
        der_entitlements=der_entitlements,
    )
    return build_macho(signature=blob, **kwargs)


def build_linker_signed_macho(**kwargs):
    """Unsigned input as emitted by an arm64 linker: carries an ad-hoc signature."""
    placeholder, meta = build_macho(signature=b"\0" * 0x400, **kwargs)
    prefix = placeholder[: meta["sig_off"]]
    blob = build_signature_blob(prefix)[:0x400]  # the blob itself is far smaller than 0x400
    assert not blob[0x300:].strip(b"\0")
    return build_macho(signature=blob, **kwargs)


def build_fat(slices):
    """slices: list of (cputype, cpusubtype, bytes, alignment_log2) -> 32-bit fat image."""
    table_end = 8 + 20 * len(slices)
    cursor = table_end
    placed = []
    for cputype, subtype, data, log2 in slices:
        cursor = align(cursor, 1 << log2)
        placed.append((cputype, subtype, cursor, data, log2))
        cursor += len(data)
    out = bytearray(struct.pack(">II", 0xCAFEBABE, len(slices)))
    for cputype, subtype, offset, data, log2 in placed:
        out += struct.pack(">iiIII", cputype, subtype, offset, len(data), log2)
    for _cputype, _subtype, offset, data, _log2 in placed:
        out = out.ljust(offset, b"\0")
        out += data
    return bytes(out)


def sha(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

class WorkDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def put(self, name, data):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def compare_bytes(self, kind, unsigned, signed, **kwargs):
        u = self.put("unsigned.bin", unsigned)
        s = self.put("signed.bin", signed)
        return correspondence.compare(u, s, kind, **kwargs)

    def codes(self, report, key="differences"):
        return [item["code"] for item in report[key]]


class PeCorrespondenceTests(WorkDir):
    def fixture(self, **kwargs):
        unsigned, meta = build_pe(**kwargs)
        signed = authenticode_like(unsigned, meta)
        return unsigned, signed, meta

    def test_pe_only_certificate_table_checksum_and_signature_overlay_change(self):
        for pe32plus, machine in ((True, 0x8664), (False, 0x14C)):
            with self.subTest(pe32plus=pe32plus):
                # 0x13 bytes of real overlay make the file length non-8-aligned, so the
                # signature alignment padding is non-empty.
                unsigned, signed, _ = self.fixture(pe32plus=pe32plus, machine=machine, overlay=b"OVERLAY-DATA-0123456")
                self.assertNotEqual(len(unsigned) % 8, 0)
                report = self.compare_bytes("pe", unsigned, signed)
                self.assertEqual(report["schema"], 1)
                self.assertEqual(report["verdict"], "PASS", report["differences"])
                self.assertEqual(report["differences"], [])
                self.assertEqual(
                    self.codes(report, "allowed_differences"),
                    [
                        "pe-checksum-field",
                        "pe-security-directory",
                        "pe-signature-alignment-padding",
                        "pe-certificate-table",
                    ],
                )
                table = report["allowed_differences"][-1]
                self.assertEqual(table["offset"], align(len(unsigned), 8))
                self.assertEqual(table["offset"] + table["size"], len(signed))
                self.assertEqual(table["entries"], 1)
                self.assertEqual(report["input_hashes"]["unsigned_sha256"], sha(unsigned))
                self.assertEqual(report["input_hashes"]["signed_sha256"], sha(signed))
                self.assertTrue(report["input_hashes"]["signed_matches_invariant"])

    def test_pe_without_overlay_or_padding_still_passes(self):
        unsigned, signed, _ = self.fixture()
        self.assertEqual(len(unsigned) % 8, 0)
        report = self.compare_bytes("pe", unsigned, signed)
        self.assertEqual(report["verdict"], "PASS")
        self.assertNotIn("pe-signature-alignment-padding", self.codes(report, "allowed_differences"))

    def test_pe_changed_code_or_extra_overlay_rejected(self):
        unsigned, meta = build_pe(overlay=b"OVERLAY")
        good = authenticode_like(unsigned, meta)

        def sign_modified(modified):
            return authenticode_like(modified, meta)

        cases = {}
        flipped = bytearray(unsigned)
        flipped[meta["text_offset"] + 3] ^= 0xFF
        cases["code"] = (sign_modified(bytes(flipped)), "pe-section-changed", "section:.text")

        flipped = bytearray(unsigned)
        flipped[meta["rsrc_offset"] + 0x60] ^= 0x01
        cases["resource"] = (sign_modified(bytes(flipped)), "pe-section-changed", "section:.rsrc")

        flipped = bytearray(unsigned)
        flipped[meta["timestamp_offset"]] ^= 0x01
        cases["header-timestamp"] = (sign_modified(bytes(flipped)), "pe-header-changed", "headers")

        cases["extra-overlay"] = (sign_modified(unsigned + b"EXTRA-PAYLOAD"), "pe-overlay-changed", "overlay")
        cases["lost-overlay"] = (sign_modified(unsigned[:-4]), "pe-overlay-changed", "overlay")

        nonzero_pad = authenticode_like(unsigned, meta, pad=b"\xAA" * (align(len(unsigned), 8) - len(unsigned)))
        cases["nonzero-alignment-padding"] = (nonzero_pad, "pe-signature-alignment-padding", None)

        trailing = good + b"UNAUTHENTICATED"
        cases["trailing-after-certificate"] = (trailing, "pe-unauthenticated-trailing-data", None)

        for name, (signed, code, region) in cases.items():
            with self.subTest(name=name):
                report = self.compare_bytes("pe", unsigned, signed)
                self.assertEqual(report["verdict"], "FAIL")
                self.assertIn(code, self.codes(report))
                if region is not None:
                    self.assertIn(region, [d.get("region") for d in report["differences"]])

    def test_pe_bytes_between_sections_are_compared_not_ignored(self):
        unsigned, meta = build_pe(gap_before_rdata=0x200)
        good = authenticode_like(unsigned, meta)
        self.assertEqual(self.compare_bytes("pe", unsigned, good)["verdict"], "PASS")
        smuggled = bytearray(unsigned)
        smuggled[meta["gap_offset"] + 5] = 0x41  # not inside any section
        report = self.compare_bytes("pe", unsigned, authenticode_like(bytes(smuggled), meta))
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual([(d["code"], d["region"]) for d in report["differences"]], [("pe-section-changed", f"gap:{meta['gap_offset']:#x}")])

    def test_pe_certificate_table_must_bound_real_win_certificates_and_be_terminal(self):
        unsigned, meta = build_pe(overlay=b"OVERLAY")
        good = authenticode_like(unsigned, meta)
        offset = align(len(unsigned), 8)

        def patch_secdir(size=None, off=None):
            image = bytearray(good)
            cur_off, cur_size = struct.unpack_from("<II", image, meta["secdir_offset"])
            struct.pack_into("<II", image, meta["secdir_offset"], cur_off if off is None else off, cur_size if size is None else size)
            return bytes(image)

        bad = "pe-invalid-certificate-table"
        cases = {
            "size-too-short": (patch_secdir(size=len(good) - offset - 8), "overruns"),
            "size-too-long": (patch_secdir(size=len(good) - offset + 8), "past the end of the file"),
            "offset-misaligned": (patch_secdir(off=offset + 4), "not 8-byte aligned"),
            "offset-before-image-end": (patch_secdir(off=meta["file_end"] - 8), "overlaps the image content"),
            "wrong-revision": (
                authenticode_like(unsigned, meta, certs=[win_certificate(PKCS7_FIXTURE, revision=0x0100)]),
                "revision/type",
            ),
            "wrong-type": (
                authenticode_like(unsigned, meta, certs=[win_certificate(PKCS7_FIXTURE, cert_type=0x0001)]),
                "revision/type",
            ),
            "empty-certificate": (authenticode_like(unsigned, meta, certs=[win_certificate(b"")]), "empty or overruns"),
            "length-overruns-table": (
                authenticode_like(
                    unsigned, meta, certs=[struct.pack("<IHH", 0x7FFFFFF0, 0x0200, 2) + PKCS7_FIXTURE.ljust(0x1008, b"\0")]
                ),
                "empty or overruns",
            ),
            "not-der-sequence": (authenticode_like(unsigned, meta, certs=[win_certificate(b"\x00" * 64)]), "not a DER SEQUENCE"),
            "der-length-lies": (  # declares a 0x100-byte SEQUENCE but carries ~4 KiB of non-zero body
                authenticode_like(
                    unsigned, meta, certs=[win_certificate(PKCS7_FIXTURE[:2] + struct.pack(">H", 0x100) + PKCS7_FIXTURE[4:])]
                ),
                "does not match",
            ),
        }
        for name, (signed, reason) in cases.items():
            with self.subTest(name=name):
                report = self.compare_bytes("pe", unsigned, signed)
                self.assertEqual(report["verdict"], "FAIL")
                self.assertEqual(report["allowed_differences"], [])
                reasons = [d["message"] for d in report["differences"] if d["code"] == bad]
                self.assertTrue(any(reason in message for message in reasons), (reason, report["differences"]))

        two = authenticode_like(unsigned, meta, certs=[win_certificate(PKCS7_FIXTURE), win_certificate(PKCS7_FIXTURE_TWO)])
        report = self.compare_bytes("pe", unsigned, two)
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["allowed_differences"][-1]["entries"], 2)

    def test_pe_signed_without_certificate_or_unsigned_already_signed_fail(self):
        unsigned, meta = build_pe()
        report = self.compare_bytes("pe", unsigned, unsigned)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("pe-signed-input-has-no-certificate", self.codes(report))

        signed = authenticode_like(unsigned, meta)
        report = self.compare_bytes("pe", signed, signed)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("pe-unsigned-input-has-certificate", self.codes(report))

    def test_pe_overlapping_or_out_of_range_sections_are_rejected(self):
        # Overlapping raw data: section 1 claims section 0's file range.
        text_ptr = SIZE_OF_HEADERS
        good_unsigned, good_meta = build_pe()
        good_signed = authenticode_like(good_unsigned, good_meta)
        malformed = {
            "raw-overlap": (build_pe(section_overrides={1: [(20, "<I", text_ptr)]})[0], "pe-invalid-section-overlap"),
            "raw-out-of-range": (build_pe(section_overrides={2: [(20, "<I", 0x7FFFFE00)]})[0], "pe-invalid-section-range"),
            "virtual-overlap": (build_pe(section_overrides={1: [(12, "<I", 0x1000)]})[0], "pe-invalid-section-virtual-overlap"),
            "raw-inside-headers": (build_pe(section_overrides={0: [(20, "<I", 0x200)]})[0], "pe-invalid-section-range"),
        }
        for name, (image, code) in malformed.items():
            with self.subTest(name=name):
                # Malformed on the unsigned side...
                report = self.compare_bytes("pe", image, good_signed)
                self.assertEqual(report["verdict"], "FAIL")
                self.assertEqual([(d["code"], d["side"]) for d in report["differences"]], [(code, "unsigned")])
                # ...and the same defect on the signed side (sections are validated before any byte comparison).
                report = self.compare_bytes("pe", good_unsigned, authenticode_like(image, good_meta))
                self.assertEqual(report["verdict"], "FAIL")
                self.assertEqual([(d["code"], d["side"]) for d in report["differences"]], [(code, "signed")])

    def test_pe_unsupported_machine_optional_magic_and_symbols_fail_closed(self):
        good, meta = build_pe()
        signed_good = authenticode_like(good, meta)
        for name, (patch, code) in {
            "machine": ((0x80 + 4, "<H", 0x01C0), "pe-unsupported-machine"),
            "optional-magic": ((0x80 + 4 + 20, "<H", 0x0107), "pe-unsupported-optional-header"),
            "symbol-table": ((0x80 + 4 + 8, "<I", 0x1234), "pe-unsupported-symbol-table"),
            "data-directory-count": ((0x80 + 4 + 20 + 108, "<I", 17), "pe-unsupported-optional-header"),
        }.items():
            with self.subTest(name=name):
                mutated = bytearray(good)
                struct.pack_into(patch[1], mutated, patch[0], patch[2])
                report = self.compare_bytes("pe", bytes(mutated), signed_good)
                self.assertEqual(report["verdict"], "FAIL")
                self.assertEqual([(d["code"], d["side"]) for d in report["differences"]], [(code, "unsigned")])

    def test_non_pe_input_is_invalid_input_not_a_verdict(self):
        unsigned, meta = build_pe()
        signed = authenticode_like(unsigned, meta)
        for name, junk in {
            "empty": b"",
            "text": b"just some text, definitely not a portable executable",
            "mz-only": b"MZ" + b"\0" * 30,
            "no-pe-signature": b"MZ" + b"\0" * 0x3A + struct.pack("<I", 0x80) + b"\0" * 0x100,
        }.items():
            with self.subTest(name=name):
                with self.assertRaises(correspondence.InputError):
                    self.compare_bytes("pe", junk, signed)
                with self.assertRaises(correspondence.InputError):
                    self.compare_bytes("pe", unsigned, junk)


class MachoCorrespondenceTests(WorkDir):
    def assert_pass(self, report):
        self.assertEqual(report["verdict"], "PASS", report["differences"])
        self.assertEqual(report["differences"], [])

    def test_macho_only_code_signature_command_blob_and_validated_linkedit_extents_change(self):
        unsigned, umeta = build_macho()
        signed, smeta = build_signed_macho()
        report = self.compare_bytes("macho", unsigned, signed)
        self.assert_pass(report)
        self.assertEqual(
            self.codes(report, "allowed_differences"),
            [
                "macho-load-command-count",
                "macho-code-signature-command",
                "macho-linkedit-extent",
                "macho-signature-alignment-padding",
                "macho-code-signature-blob",
            ],
        )
        items = {item["code"]: item for item in report["allowed_differences"]}
        self.assertEqual(items["macho-code-signature-command"]["action"], "added")
        self.assertEqual(items["macho-code-signature-command"]["dataoff"], smeta["sig_off"])
        self.assertEqual(items["macho-code-signature-command"]["datasize"], SIG_DATASIZE)
        self.assertEqual(items["macho-load-command-count"]["sizeofcmds_delta"], 16)
        self.assertEqual(items["macho-signature-alignment-padding"]["size"], smeta["sig_off"] - len(unsigned))
        self.assertEqual(items["macho-code-signature-blob"]["code_directory"]["hash_type"], "sha256")
        self.assertEqual(report["input_hashes"]["unsigned_sha256"], sha(unsigned))
        self.assertEqual(report["input_hashes"]["signed_sha256"], sha(signed))
        self.assertTrue(report["input_hashes"]["signed_matches_invariant"])

    def test_macho_linker_signed_input_is_replaced_not_added(self):
        unsigned = build_linker_signed_macho()[0]
        signed, _ = build_signed_macho()
        report = self.compare_bytes("macho", unsigned, signed)
        self.assert_pass(report)
        items = {item["code"]: item for item in report["allowed_differences"]}
        self.assertEqual(items["macho-code-signature-command"]["action"], "replaced")
        self.assertNotIn("macho-load-command-count", items)

    def test_macho_fat_signed_per_slice_and_entitlements_policy(self):
        entitlements = {"com.apple.security.device.bluetooth": True}
        arm_u, _ = build_macho()
        x86_u, _ = build_macho(cputype=CPU_X86_64, text=b"\x90" * 200)
        arm_s, _ = build_signed_macho(entitlements=entitlements)
        x86_s, _ = build_signed_macho(entitlements=entitlements, cputype=CPU_X86_64, text=b"\x90" * 200)
        fat_u = build_fat([(CPU_ARM64, 0, arm_u, 14), (CPU_X86_64, 3, x86_u, 12)])
        fat_s = build_fat([(CPU_ARM64, 0, arm_s, 14), (CPU_X86_64, 3, x86_s, 12)])
        expected = self.put("entitlements.plist", plistlib.dumps(entitlements))
        report = self.compare_bytes("macho", fat_u, fat_s, entitlements=expected)
        self.assert_pass(report)
        slices = {item["slice"] for item in report["allowed_differences"] if "slice" in item}
        self.assertEqual(slices, {0, 1})

        wrong = self.put("wrong.plist", plistlib.dumps({"com.apple.security.device.camera": True}))
        report = self.compare_bytes("macho", fat_u, fat_s, entitlements=wrong)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("macho-entitlements-mismatch", self.codes(report))

    def test_macho_changed_text_uuid_or_extra_slice_rejected(self):
        unsigned, _ = build_macho()
        cases = {}
        # Validly signed, but over different executable code / UUID.
        modified_text, _ = build_signed_macho(text=b"\x1f\x20\x03\xd5" * 63 + b"\xc0\x03\x5f\xd6")
        cases["text"] = (modified_text, "macho-content-changed")
        other_uuid, _ = build_signed_macho(uuid=bytes(range(0x30, 0x40)))
        cases["uuid"] = (other_uuid, "macho-content-changed")
        other_symbols, _ = build_signed_macho(symbols=b"\0_evil\0\0")
        cases["linkedit-data"] = (other_symbols, "macho-content-changed")
        swapped, _ = build_signed_macho(cmd_order_swap=True)
        cases["command-order"] = (swapped, "macho-content-changed")
        for name, (signed, code) in cases.items():
            with self.subTest(name=name):
                report = self.compare_bytes("macho", unsigned, signed)
                self.assertEqual(report["verdict"], "FAIL")
                self.assertIn(code, self.codes(report))

        # Region naming: the executable section and the UUID command are named.
        report = self.compare_bytes("macho", unsigned, modified_text)
        self.assertIn("__TEXT.__text", [d.get("region") for d in report["differences"]])
        report = self.compare_bytes("macho", unsigned, other_uuid)
        self.assertIn("load-command:LC_UUID", [d.get("region") for d in report["differences"]])

        # Extra slice, missing slice, and an unsigned slice inside a signed fat image.
        arm_u, _ = build_macho()
        x86_u, _ = build_macho(cputype=CPU_X86_64)
        arm_s, _ = build_signed_macho()
        x86_s, _ = build_signed_macho(cputype=CPU_X86_64)
        fat_u = build_fat([(CPU_ARM64, 0, arm_u, 14)])
        fat_s_extra = build_fat([(CPU_ARM64, 0, arm_s, 14), (CPU_X86_64, 3, x86_s, 12)])
        report = self.compare_bytes("macho", fat_u, fat_s_extra)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("macho-slice-set-changed", self.codes(report))

        fat_u2 = build_fat([(CPU_ARM64, 0, arm_u, 14), (CPU_X86_64, 3, x86_u, 12)])
        fat_s_one_unsigned = build_fat([(CPU_ARM64, 0, arm_s, 14), (CPU_X86_64, 3, x86_u, 12)])
        report = self.compare_bytes("macho", fat_u2, fat_s_one_unsigned)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("macho-slice-unsigned", self.codes(report))

        fat_s_missing = build_fat([(CPU_ARM64, 0, arm_s, 14)])
        report = self.compare_bytes("macho", fat_u2, fat_s_missing)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("macho-slice-set-changed", self.codes(report))

        report = self.compare_bytes("macho", fat_u, build_signed_macho()[0])
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("macho-container-kind-changed", self.codes(report))

    def test_macho_unknown_transforms_and_malformed_structure_fail_closed(self):
        unsigned, _ = build_macho()
        signed, smeta = build_signed_macho()

        unknown_cmd = struct.pack("<II", 0x7F000001, 16) + b"\0" * 8
        bad_unsigned, _ = build_macho(extra_load_command=unknown_cmd)
        bad_signed, _ = build_signed_macho(extra_load_command=unknown_cmd)
        report = self.compare_bytes("macho", bad_unsigned, bad_signed)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("macho-unknown-load-command", self.codes(report))

        trailing_signed = signed + b"UNSIGNED-TRAILER"
        wrong_vmsize, _ = build_signed_macho(linkedit_vmsize=0x4000 * 7)
        bad_count, _ = build_signed_macho(ncmds_delta=1)
        truncated = signed[: smeta["sig_off"] + 100]
        bad_blob_magic, _ = build_signed_macho(bad_magic=True)
        tampered_hash, _ = build_signed_macho(tamper_slot=True)
        for name, candidate, code in (
            ("trailing-data", trailing_signed, "macho-trailing-data"),
            ("linkedit-vmsize", wrong_vmsize, "macho-content-changed"),  # region asserted below
            ("ncmds-mismatch", bad_count, "macho-invalid-load-commands"),
            ("truncated-signature", truncated, "macho-invalid-linkedit-extent"),
            ("blob-magic", bad_blob_magic, "macho-invalid-signature-blob"),
            ("code-directory-hash", tampered_hash, "macho-code-directory-hash-mismatch"),
        ):
            with self.subTest(name=name):
                report = self.compare_bytes("macho", unsigned, candidate)
                self.assertEqual(report["verdict"], "FAIL")
                self.assertEqual(report["allowed_differences"], [])
                self.assertIn(code, self.codes(report))
                if name == "linkedit-vmsize":
                    self.assertIn("load-command:LC_SEGMENT_64:__LINKEDIT.vmsize", [d.get("region") for d in report["differences"]])

        report = self.compare_bytes("macho", unsigned, unsigned)  # never signed
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("macho-slice-unsigned", self.codes(report))

        # 32-bit and big-endian Mach-O are recognised but unsupported: FAIL, not PASS.
        thirty_two = struct.pack("<IiiIIIII", 0xFEEDFACE, 12, 0, 2, 0, 0, 0, 0) + b"\0" * 64
        report = self.compare_bytes("macho", thirty_two, signed)
        self.assertEqual(report["verdict"], "FAIL")

    def test_macho_linkedit_must_be_the_last_segment(self):
        # A zero-file segment mapped after __LINKEDIT: the signed-extent arithmetic would be unsound.
        after = segment("__AFTER", 0x100000000 + 0x40000, 0x4000, 0, 0, 3, 3)
        unsigned, _ = build_macho(extra_load_command=after)
        signed, _ = build_signed_macho(extra_load_command=after)
        report = self.compare_bytes("macho", unsigned, signed)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual([d["code"] for d in report["differences"]], ["macho-invalid-linkedit-extent"] * 2)

    def test_macho_every_byte_outside_the_signature_is_compared(self):
        base_kwargs = {}
        unsigned, _ = build_macho(**base_kwargs)
        cases = {
            "header-flags": (dict(header_flags=0x00200086), "header"),
            "segment-slack": (dict(slack=b"SMUGGLED"), "segment-data"),
            "load-command-padding": (dict(low_slack=b"SMUGGLED"), "load-command-padding"),
        }
        for name, (kwargs, region) in cases.items():
            with self.subTest(name=name):
                signed, _ = build_signed_macho(**kwargs)
                report = self.compare_bytes("macho", unsigned, signed)
                self.assertEqual(report["verdict"], "FAIL")
                self.assertIn(region, [d.get("region") for d in report["differences"]])
        # Unsigned side carries the same slack => identical, so it is not the slack itself that fails.
        slack_unsigned, _ = build_macho(slack=b"SAME")
        slack_signed, _ = build_signed_macho(slack=b"SAME")
        self.assertEqual(self.compare_bytes("macho", slack_unsigned, slack_signed)["verdict"], "PASS")

    def test_macho_signature_placement_and_adhoc_rules(self):
        unsigned, _ = build_macho()
        pad_signed, _ = build_signed_macho(pad_fill=b"\xAA")
        report = self.compare_bytes("macho", unsigned, pad_signed)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("macho-invalid-linkedit-extent", self.codes(report))

        no_cms, _ = build_signed_macho(omit_cms=True)
        report = self.compare_bytes("macho", unsigned, no_cms)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(
            [(d["code"], "no CMS" in d["message"]) for d in report["differences"]], [("macho-adhoc-signature", True)]
        )

        flagged, _ = build_signed_macho(cd_flags=0x10002)  # CMS present but CS_ADHOC set
        report = self.compare_bytes("macho", unsigned, flagged)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(
            [(d["code"], "ad-hoc" in d["message"]) for d in report["differences"]], [("macho-adhoc-signature", True)]
        )

    def test_macho_fat_container_strictness(self):
        arm_u, _ = build_macho()
        x86_u, _ = build_macho(cputype=CPU_X86_64)
        arm_s, _ = build_signed_macho()
        x86_s, _ = build_signed_macho(cputype=CPU_X86_64)
        fat_u = build_fat([(CPU_ARM64, 0, arm_u, 14), (CPU_X86_64, 3, x86_u, 12)])
        good = build_fat([(CPU_ARM64, 0, arm_s, 14), (CPU_X86_64, 3, x86_s, 12)])
        self.assertEqual(self.compare_bytes("macho", fat_u, good)["verdict"], "PASS")

        padded = bytearray(good)
        padded[100] = 0x41  # between the arch table and the first slice
        slice_size_lie = bytearray(good)
        struct.pack_into(">I", slice_size_lie, 8 + 12, 99)  # first arch claims a 99-byte slice
        cases = {
            "trailing-bytes": (good + b"X", "macho-trailing-data"),
            "nonzero-padding": (bytes(padded), "macho-invalid-fat"),
            "duplicate-architecture": (build_fat([(CPU_ARM64, 0, arm_s, 14), (CPU_ARM64, 0, arm_s, 14)]), "macho-invalid-fat"),
            "arch-disagrees-with-slice": (build_fat([(CPU_X86_64, 3, arm_s, 12)]), "macho-invalid-fat"),
            "slice-size-lie": (bytes(slice_size_lie), None),
        }
        for name, (candidate, code) in cases.items():
            with self.subTest(name=name):
                report = self.compare_bytes("macho", fat_u, candidate)
                self.assertEqual(report["verdict"], "FAIL")
                self.assertEqual(report["allowed_differences"], [])
                if code:
                    self.assertIn(code, self.codes(report))
        # Fat64 / byte-swapped headers are recognised but unsupported: FAIL, never a pass.
        for magic in (b"\xca\xfe\xba\xbf", b"\xbe\xba\xfe\xca"):
            with self.subTest(magic=magic):
                report = self.compare_bytes("macho", fat_u, magic + good[4:])
                self.assertEqual(report["verdict"], "FAIL")
                self.assertIn("macho-unsupported-format", self.codes(report))

    def test_macho_non_macho_input_is_invalid_input(self):
        signed, _ = build_signed_macho()
        for junk in (b"", b"MZ\x90\x00" + b"\0" * 64, b"\xca\xfe\xba\xbe\x00\x00\x00\x00"):
            with self.subTest(junk=junk[:6]):
                with self.assertRaises(correspondence.InputError):
                    self.compare_bytes("macho", junk, signed)


class TreeCorrespondenceTests(WorkDir):
    def app(self, name, *, signed, resources=None, staple=False, extra=None):
        root = self.base / name
        files = {
            "Contents/Info.plist": (plistlib.dumps({"CFBundleIdentifier": "me.nagaev.veles"}), 0o644),
            "Contents/Resources/config.json": (b'{"origin":"chrome-extension://prod/"}\n', 0o644),
        }
        binary = build_signed_macho()[0] if signed else build_macho()[0]
        files["Contents/MacOS/veles-native-host"] = (binary, 0o755)
        if signed:
            files["Contents/_CodeSignature/CodeResources"] = (plistlib.dumps({"files2": {}}), 0o644)
        if staple:
            files["Contents/CodeResources"] = (b"TICKET-FIXTURE", 0o644)
        files.update(resources or {})
        files.update(extra or {})
        for rel, (data, mode) in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(mode)
        return root

    def test_signed_app_tree_with_only_seal_and_ticket_additions_passes(self):
        unsigned = self.app("u.app", signed=False)
        signed = self.app("s.app", signed=True)
        report = correspondence.compare(unsigned, signed, "tree")
        self.assertEqual(report["verdict"], "PASS", report["differences"])
        codes = self.codes(report, "allowed_differences")
        self.assertIn("tree-code-seal-added", codes)
        self.assertIn("macho-code-signature-blob", codes)
        self.assertEqual(report["input_hashes"]["unsigned_inventory_sha256"], package_inputs.build_inventory(unsigned)["inventory_sha256"])
        self.assertEqual(report["input_hashes"]["signed_inventory_sha256"], package_inputs.build_inventory(signed)["inventory_sha256"])

        stapled = self.app("t.app", signed=True, staple=True)
        report = correspondence.compare(unsigned, stapled, "tree")
        self.assertEqual(report["verdict"], "PASS", report["differences"])
        ticket = [d for d in report["allowed_differences"] if d["code"] == "tree-stapled-ticket-added"]
        self.assertEqual(len(ticket), 1)
        self.assertEqual(ticket[0]["sha256"], sha(b"TICKET-FIXTURE"))
        self.assertTrue(ticket[0]["verify_separately"])

    def test_tree_rejects_unexpected_resource_and_duplicate_archive_member(self):
        unsigned = self.app("u.app", signed=False)

        cases = {
            "unexpected-resource": (
                self.app("a.app", signed=True, extra={"Contents/Resources/extra.bin": (b"x", 0o644)}),
                "tree-unexpected-in-signed",
                "Contents/Resources/extra.bin",
            ),
            "modified-resource": (
                self.app("b.app", signed=True, resources={"Contents/Resources/config.json": (b'{"origin":"*"}\n', 0o644)}),
                "tree-content-changed",
                "Contents/Resources/config.json",
            ),
            "other-signature-file": (
                self.app("c.app", signed=True, extra={"Contents/_CodeSignature/CodeDirectory": (b"x", 0o644)}),
                "tree-unexpected-in-signed",
                "Contents/_CodeSignature/CodeDirectory",
            ),
            "ticket-in-wrong-place": (
                self.app("d.app", signed=True, extra={"Contents/Resources/CodeResources": (b"x", 0o644)}),
                "tree-unexpected-in-signed",
                "Contents/Resources/CodeResources",
            ),
            "executable-bit-changed": (
                self.app(
                    "e.app",
                    signed=True,
                    resources={"Contents/Resources/config.json": (b'{"origin":"chrome-extension://prod/"}\n', 0o755)},
                ),
                "tree-mode-changed",
                "Contents/Resources/config.json",
            ),
            "broad-ds-store": (
                self.app("f.app", signed=True, extra={"Contents/.DS_Store": (b"x", 0o644)}),
                "tree-unexpected-in-signed",
                "Contents/.DS_Store",
            ),
        }
        for name, (signed, code, path) in cases.items():
            with self.subTest(name=name):
                report = correspondence.compare(unsigned, signed, "tree")
                self.assertEqual(report["verdict"], "FAIL")
                self.assertEqual(report["allowed_differences"], [])
                self.assertIn((code, path), [(d["code"], d.get("path")) for d in report["differences"]])
        ticket_alone = self.app("t0.app", signed=False, staple=True)
        report = correspondence.compare(unsigned, ticket_alone, "tree")
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("tree-ticket-without-seal", self.codes(report))

        missing = self.app("g.app", signed=True)
        (missing / "Contents" / "Resources" / "config.json").unlink()
        report = correspondence.compare(unsigned, missing, "tree")
        self.assertIn("tree-missing-in-signed", self.codes(report))

        unsigned_exec = self.app("h.app", signed=True)
        (unsigned_exec / "Contents" / "MacOS" / "veles-native-host").write_bytes(build_macho()[0])
        report = correspondence.compare(unsigned, unsigned_exec, "tree")
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("tree-executable-not-signed", self.codes(report))

        untouched = self.app("i.app", signed=False)
        (untouched / "Contents" / "_CodeSignature").mkdir()
        (untouched / "Contents" / "_CodeSignature" / "CodeResources").write_bytes(b"seal")
        report = correspondence.compare(unsigned, untouched, "tree")
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("tree-executable-not-signed", self.codes(report))

        # Duplicate archive member: a well-formed zip that lists one path twice.
        members = [("Contents/Info.plist", b"a"), ("Contents/Resources/x", b"b"), ("Contents/Resources/x", b"EVIL")]
        buffer = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(buffer, "w") as archive:
                for name, data in members:
                    archive.writestr(name, data)
        duplicate_zip = self.put("dup.zip", buffer.getvalue())
        report = correspondence.compare(duplicate_zip, duplicate_zip, "tree")
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("duplicate-archive-member", self.codes(report))
        report = correspondence.compare(unsigned, duplicate_zip, "tree")
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("duplicate-archive-member", self.codes(report))

    def test_tree_archives_compare_like_directories(self):
        unsigned = self.app("u.app", signed=False)
        signed = self.app("s.app", signed=True)

        def zipped(root, name, stamp):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        info = zipfile.ZipInfo(path.relative_to(root).as_posix(), date_time=stamp)
                        info.external_attr = (0o100755 if path.stat().st_mode & 0o111 else 0o100644) << 16
                        archive.writestr(info, path.read_bytes())
            return self.put(name, buffer.getvalue())

        report = correspondence.compare(zipped(unsigned, "u.zip", (2020, 1, 1, 0, 0, 0)), zipped(signed, "s.zip", (2031, 5, 5, 5, 5, 4)), "tree")
        self.assertEqual(report["verdict"], "PASS", report["differences"])
        report = correspondence.compare(unsigned, zipped(signed, "s2.zip", (2024, 2, 2, 2, 2, 2)), "tree")
        self.assertEqual(report["verdict"], "PASS", report["differences"])

    def test_tree_archive_with_content_beyond_its_declared_size_never_passes(self):
        """Review F7 end to end: a 1 MiB payload declared as 1 byte must not compare equal to a genuine 1-byte file."""
        import zlib

        genuine = self.put("genuine.zip", raw_zip.make_codec_zip([("payload", b"x")], zipfile.ZIP_DEFLATED))
        for label, method in (("lzma", zipfile.ZIP_LZMA), ("deflate", zipfile.ZIP_DEFLATED)):
            with self.subTest(codec=label):
                hidden = raw_zip.patch_member(
                    raw_zip.make_codec_zip([("payload", b"x" * (1 << 20))], method), 0, crc=zlib.crc32(b"x"), usize=1
                )
                self.assertLess(len(hidden), 4096)
                for first, second in ((genuine, self.put(f"hidden-{label}.zip", hidden)), (self.put(f"hidden2-{label}.zip", hidden), genuine)):
                    report = correspondence.compare(first, second, "tree")
                    self.assertEqual(report["verdict"], "FAIL")
                    self.assertEqual(report["allowed_differences"], [])
                    self.assertEqual(len(report["differences"]), 1, report["differences"])
                    self.assertIn(report["differences"][0]["code"], ("unsupported-compression", "malformed-archive"))
        # Through the CLI it is a non-zero exit with a FAIL document, never exit 0.
        out = self.base / "report.json"
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--unsigned", str(genuine), "--signed", str(self.base / "hidden-lzma.zip"), "--kind", "tree", "--report", str(out)],
            capture_output=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(out.read_text())["verdict"], "FAIL")

    def test_tree_delegates_pe_files_and_keeps_other_files_byte_identical(self):
        unsigned_pe, meta = build_pe()
        signed_pe = authenticode_like(unsigned_pe, meta)
        for name, exe in (("u", unsigned_pe), ("s", signed_pe)):
            root = self.base / name
            (root / "bin").mkdir(parents=True)
            (root / "bin" / "veles-native-host.exe").write_bytes(exe)
            (root / "bin" / "manifest.json").write_bytes(b"{}")
        report = correspondence.compare(self.base / "u", self.base / "s", "tree")
        self.assertEqual(report["verdict"], "PASS", report["differences"])
        self.assertIn("pe-certificate-table", self.codes(report, "allowed_differences"))
        self.assertEqual(
            [d["path"] for d in report["allowed_differences"] if d["code"] == "pe-certificate-table"],
            ["bin/veles-native-host.exe"],
        )
        (self.base / "s" / "bin" / "manifest.json").write_bytes(b'{"x":1}')
        report = correspondence.compare(self.base / "u", self.base / "s", "tree")
        self.assertEqual(report["verdict"], "FAIL")

    def test_tree_empty_directory_missing_from_signed_is_rejected(self):
        unsigned = self.app("u.app", signed=False)
        (unsigned / "Contents" / "Resources" / "placeholder").mkdir()
        signed = self.app("s.app", signed=True)
        report = correspondence.compare(unsigned, signed, "tree")
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn(("tree-missing-in-signed", "Contents/Resources/placeholder/"), [(d["code"], d.get("path")) for d in report["differences"]])

    def test_tree_rejects_symlinks_and_empty_directory_changes(self):
        unsigned = self.app("u.app", signed=False)
        signed = self.app("s.app", signed=True)
        (signed / "Contents" / "Resources" / "hollow").mkdir()
        report = correspondence.compare(unsigned, signed, "tree")
        self.assertEqual(report["verdict"], "FAIL")
        (signed / "Contents" / "Resources" / "hollow").rmdir()
        import os

        os.symlink("../MacOS/veles-native-host", signed / "Contents" / "Resources" / "link")
        report = correspondence.compare(unsigned, signed, "tree")
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("symlink", self.codes(report))


class InputErrorTests(WorkDir):
    def test_missing_unreadable_and_unsupported_inputs_raise_input_error(self):
        unsigned, meta = build_pe()
        good = self.put("good.exe", unsigned)
        with self.assertRaises(correspondence.InputError):
            correspondence.compare(good, self.base / "missing.exe", "pe")
        with self.assertRaises(correspondence.InputError):
            correspondence.compare(self.base / "missing.exe", good, "pe")
        with self.assertRaises(correspondence.InputError):
            correspondence.compare(good, good, "elf")
        with self.assertRaises(correspondence.InputError):
            correspondence.compare(good, self.base, "pe")  # directory where a file is required
        with self.assertRaises(correspondence.InputError):
            correspondence.compare(good, good, "tree")  # plain file that is not an archive
        with self.assertRaises(correspondence.InputError):
            correspondence.compare(self.base / "nope", self.base / "nope2", "tree")


PROVENANCE = HERE / "fixtures" / "PROVENANCE.md"


def provenance_rows():
    rows = {}
    for line in PROVENANCE.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[1].startswith("`") and len(cells[1].strip("`")) == 64:
            rows[cells[0]] = {"sha256": cells[1].strip("`"), "size": int(cells[2]), "kind": cells[3]}
    return rows


class HostileInputTests(WorkDir):
    """Declared sizes are never trusted: oversized claims are refused before any allocation or long loop."""

    def test_hostile_declared_sizes_are_rejected_quickly(self):
        unsigned, meta = build_pe()
        signed = authenticode_like(unsigned, meta)
        hostile_pe = bytearray(unsigned)
        struct.pack_into("<I", hostile_pe, meta["section_table_offset"] + 16, 0xFFFFFFF0)  # .text SizeOfRawData
        report = self.compare_bytes("pe", bytes(hostile_pe), signed)
        self.assertEqual([d["code"] for d in report["differences"]], ["pe-invalid-section-range"])

        many = authenticode_like(unsigned, meta, certs=[win_certificate(PKCS7_FIXTURE_TWO)] * (correspondence.MAX_CERTIFICATES + 1))
        report = self.compare_bytes("pe", unsigned, many)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertTrue(any("more than" in d["message"] for d in report["differences"]), report["differences"])

        mac_unsigned, _ = build_macho()
        mac_signed, mac_meta = build_signed_macho()
        hostile = bytearray(mac_signed)
        struct.pack_into("<I", hostile, 16, 0xFFFFFFFF)  # ncmds
        self.assertEqual(
            [d["code"] for d in self.compare_bytes("macho", mac_unsigned, bytes(hostile))["differences"]], ["macho-invalid-load-commands"]
        )
        hostile = bytearray(mac_signed)
        struct.pack_into(">I", hostile, mac_meta["sig_off"] + 8, 0xFFFFFFFF)  # SuperBlob count
        self.assertEqual(
            [d["code"] for d in self.compare_bytes("macho", mac_unsigned, bytes(hostile))["differences"]], ["macho-invalid-signature-blob"]
        )
        hostile = bytearray(mac_signed)
        struct.pack_into(">I", hostile, mac_meta["sig_off"] + 4, 0xFFFFFFFF)  # SuperBlob length
        self.assertEqual(
            [d["code"] for d in self.compare_bytes("macho", mac_unsigned, bytes(hostile))["differences"]], ["macho-invalid-signature-blob"]
        )

    def test_oversized_files_and_archive_bombs_are_refused_before_reading(self):
        unsigned, meta = build_pe()
        good = self.put("good.exe", unsigned)
        with mock.patch.object(correspondence, "MAX_FILE_BYTES", 100):
            with self.assertRaises(correspondence.InputError):
                correspondence.compare(good, good, "pe")

        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Contents/big.bin", b"\x00" * 200_000)
        path = self.put("bomb.zip", bomb.getvalue())
        with mock.patch.object(package_inputs, "MAX_TOTAL_BYTES", 10_000), mock.patch.object(
            package_inputs, "MAX_ARCHIVE_MEMBER_BYTES", 10_000
        ), mock.patch.object(zipfile.ZipFile, "open", side_effect=AssertionError("decompressed")):
            report = correspondence.compare(path, path, "tree")
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual({d["code"] for d in report["differences"]}, {"bound-exceeded"})

    def test_symlinked_inputs_are_not_followed(self):
        unsigned, meta = build_pe()
        real = self.put("real.exe", unsigned)
        link = self.base / "link.exe"
        link.symlink_to(real)
        with self.assertRaises(correspondence.InputError):
            correspondence.compare(link, real, "pe")
        tree = self.base / "tree"
        tree.mkdir()
        (self.base / "tree-link").symlink_to(tree)
        with self.assertRaises(correspondence.InputError):
            correspondence.compare(self.base / "tree-link", tree, "tree")


class FixtureProvenanceTests(unittest.TestCase):
    """The committed fixtures are exactly what PROVENANCE.md says, and it says what is blocked."""

    def test_base_images_are_real_toolchain_output_of_the_claimed_shape(self):
        pe = fx.PE_BASE.read_bytes()
        self.assertEqual(pe[:2], b"MZ")
        lfanew = struct.unpack_from("<I", pe, 0x3C)[0]
        self.assertEqual(pe[lfanew : lfanew + 4], b"PE\0\0")
        machine, nsections = struct.unpack_from("<HH", pe, lfanew + 4)
        magic = struct.unpack_from("<H", pe, lfanew + 24)[0]
        self.assertEqual((machine, magic, nsections), (0x8664, 0x20B, 2))
        self.assertEqual(struct.unpack_from("<II", pe, fx.pe_offsets(pe)["security"]), (0, 0))
        self.assertEqual(len(pe), 2048)

        macho = fx.MACHO_BASE.read_bytes()
        magic, cputype, _sub, filetype, ncmds = struct.unpack_from("<IiiII", macho, 0)
        self.assertEqual((magic, cputype, filetype, ncmds), (0xFEEDFACF, fx.CPU_TYPE_ARM64, 2, 14))
        commands = fx._macho_commands(macho)
        self.assertEqual(commands[-1][0], fx.LC_CODE_SIGNATURE)  # the linker's own ad-hoc signature
        dataoff, datasize = struct.unpack_from("<II", macho, commands[-1][1] + 8)
        self.assertEqual(struct.unpack_from(">I", macho, dataoff)[0], 0xFADE0CC0)  # SuperBlob
        # ad-hoc: the CodeDirectory flags carry CS_ADHOC (0x2) and there is no CMS wrapper.
        blobs = struct.unpack_from(">I", macho, dataoff + 8)[0]
        slots = [struct.unpack_from(">II", macho, dataoff + 12 + 8 * i)[0] for i in range(blobs)]
        self.assertNotIn(0x10000, slots)
        cd_offset = struct.unpack_from(">I", macho, dataoff + 16)[0]
        self.assertTrue(struct.unpack_from(">I", macho, dataoff + cd_offset + 12)[0] & 0x2)
        self.assertEqual(dataoff + datasize, len(macho))

    def test_base_images_are_sanitized(self):
        for path in (fx.PE_BASE, fx.MACHO_BASE):
            data = path.read_bytes()
            for needle in (b"/home", b"/Users", b"C:\\", b"/tmp", b".worktrees", b"veles-android", b"rustc"):
                self.assertNotIn(needle, data, (path.name, needle))

    def test_fixture_digests_match_provenance(self):
        rows = provenance_rows()
        actual = fx.derived_fixtures()
        self.assertEqual(set(rows), set(actual))
        for name, blob in actual.items():
            with self.subTest(name=name):
                self.assertEqual(rows[name]["sha256"], sha(blob))
                self.assertEqual(rows[name]["size"], len(blob))
        real = {"pe-x64-unsigned.exe": fx.PE_BASE, "macho-arm64-linker-signed.bin": fx.MACHO_BASE}
        for name, row in rows.items():
            with self.subTest(name=name, kind=row["kind"]):
                if name in real:
                    self.assertEqual(row["kind"], "real toolchain output")
                    self.assertEqual(sha(real[name].read_bytes()), row["sha256"])
                else:
                    self.assertEqual(row["kind"], "locally constructed")
        # Deterministic: a second derivation is byte-identical.
        self.assertEqual(fx.derived_fixtures(), actual)

    def test_provenance_records_blocked_native_steps_and_makes_no_native_claims(self):
        text = PROVENANCE.read_text(encoding="utf-8")
        for step in (
            "Authenticode-signed PE",
            "Developer ID-signed Mach-O",
            "Notarization + ticket stapling",
            "Real-tool allowlist validation",
        ):
            row = [line for line in text.splitlines() if step in line]
            self.assertEqual(len(row), 1, step)
            self.assertIn("BLOCKED / NOT RUN", row[0])
        self.assertIn("Nothing here is a real Authenticode or Apple signature", text)
        self.assertIn("placeholder", text)
        for needle in ("/home/", "/Users/", "C:\\"):
            self.assertNotIn(needle, text)


class RealImageCorrespondenceTests(WorkDir):
    """The comparator on the real base images and their locally constructed signed variants."""

    def verdict(self, kind, unsigned, signed, **kwargs):
        return self.compare_bytes(kind, unsigned, signed, **kwargs)

    def test_real_pe_with_constructed_certificate_table_passes_with_exact_allowlist(self):
        base = fx.read_pe_base()
        report = self.verdict("pe", base, fx.pe_sign(base))
        self.assertEqual(report["verdict"], "PASS", report["differences"])
        self.assertEqual(
            self.codes(report, "allowed_differences"), ["pe-checksum-field", "pe-security-directory", "pe-certificate-table"]
        )  # 2048 % 8 == 0: no alignment padding
        table = report["allowed_differences"][-1]
        self.assertEqual((table["offset"], table["size"], table["entries"]), (len(base), 136, 1))
        self.assertTrue(table["verify_separately"])
        two = self.verdict("pe", base, fx.pe_sign(base, certificates=2))
        self.assertEqual(two["verdict"], "PASS")
        self.assertEqual(two["allowed_differences"][-1]["entries"], 2)

    def test_real_pe_rejections(self):
        base = fx.read_pe_base()
        signed = fx.pe_sign(base)
        text = 0x400  # .text raw offset of the committed image
        flipped = bytearray(base)
        flipped[text] ^= 0xFF
        for name, candidate, code in (
            ("changed-code", fx.pe_sign(bytes(flipped)), "pe-section-changed"),
            ("extra-overlay", fx.pe_sign(base + b"EVIL-OVERLAY"), "pe-overlay-changed"),
            ("trailing-after-certificates", signed + b"TRAILING", "pe-unauthenticated-trailing-data"),
            ("unsigned-as-signed", base, "pe-signed-input-has-no-certificate"),
        ):
            with self.subTest(name=name):
                report = self.verdict("pe", base, candidate)
                self.assertEqual(report["verdict"], "FAIL")
                self.assertIn(code, self.codes(report))
                self.assertEqual(report["allowed_differences"], [])

    def test_real_macho_added_and_replaced_signatures_pass(self):
        linker_signed = fx.read_macho_base()
        stripped = fx.macho_strip_signature(linker_signed)
        added = self.verdict("macho", stripped, fx.macho_sign(stripped, entitlements=None))
        self.assertEqual(added["verdict"], "PASS", added["differences"])
        items = {item["code"]: item for item in added["allowed_differences"]}
        self.assertEqual(items["macho-code-signature-command"]["action"], "added")
        self.assertEqual(items["macho-load-command-count"]["sizeofcmds_delta"], 16)
        self.assertEqual(items["macho-code-signature-blob"]["code_directory"]["page_hashes_verified"], True)
        self.assertFalse(items["macho-code-signature-blob"]["cryptographic_signature_verified"])

        replaced = self.verdict("macho", linker_signed, fx.macho_sign(linker_signed, entitlements=None))
        self.assertEqual(replaced["verdict"], "PASS", replaced["differences"])
        items = {item["code"]: item for item in replaced["allowed_differences"]}
        self.assertEqual(items["macho-code-signature-command"]["action"], "replaced")
        self.assertNotIn("macho-load-command-count", items)
        # The real linker image has vmsize == filesize (unaligned); signing rounds it to the 16 KiB page.
        self.assertEqual(items["macho-linkedit-extent"]["vmsize"]["unsigned"], items["macho-linkedit-extent"]["filesize"]["unsigned"])
        self.assertEqual(items["macho-linkedit-extent"]["vmsize"]["signed"] % 0x4000, 0)

    def test_real_macho_entitlements_must_match_policy(self):
        stripped = fx.macho_strip_signature(fx.read_macho_base())
        with_entitlements = fx.macho_sign(stripped)  # embeds fx.ENTITLEMENTS_PLIST
        report = self.verdict("macho", stripped, with_entitlements)  # nothing declared => added entitlements are a mismatch
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("macho-entitlements-mismatch", self.codes(report))
        declared = self.put("declared.plist", fx.ENTITLEMENTS_PLIST)
        self.assertEqual(self.verdict("macho", stripped, with_entitlements, entitlements=declared)["verdict"], "PASS")
        other = self.put("other.plist", plistlib.dumps({"com.apple.security.cs.allow-jit": True}))
        report = self.verdict("macho", stripped, with_entitlements, entitlements=other)
        self.assertIn("macho-entitlements-mismatch", self.codes(report))
        # Dropping declared entitlements is a mismatch too.
        report = self.verdict("macho", stripped, fx.macho_sign(stripped, entitlements=None), entitlements=declared)
        self.assertIn("macho-entitlements-mismatch", self.codes(report))

    def test_real_macho_rejections(self):
        linker_signed = fx.read_macho_base()
        stripped = fx.macho_strip_signature(linker_signed)
        commands = fx._macho_commands(stripped)
        text_section = next(
            offset + 72 + 48
            for cmd, offset, _size in commands
            if cmd == fx.LC_SEGMENT_64 and struct.unpack_from("<I", stripped, offset + 64)[0] > 0  # nsects
        )
        code_offset = struct.unpack_from("<I", stripped, text_section)[0]
        uuid_offset = next(offset + 8 for cmd, offset, _size in commands if cmd == fx.LC_UUID)

        def mutated(position):
            image = bytearray(stripped)
            image[position] ^= 0xFF
            return fx.macho_sign(bytes(image), entitlements=None)

        good = fx.macho_sign(stripped, entitlements=None)
        trailing = good + b"UNSIGNED-TRAILER"
        for name, candidate, code in (
            ("changed-code", mutated(code_offset), "macho-content-changed"),
            ("changed-uuid", mutated(uuid_offset), "macho-content-changed"),
            ("trailing-data", trailing, "macho-trailing-data"),
            ("still-unsigned", stripped, "macho-slice-unsigned"),
        ):
            with self.subTest(name=name):
                report = self.verdict("macho", stripped, candidate)
                self.assertEqual(report["verdict"], "FAIL")
                self.assertIn(code, self.codes(report))
                self.assertEqual(report["allowed_differences"], [])
        # The linker's ad-hoc signature is not an acceptable *signed* output.
        report = self.verdict("macho", stripped, linker_signed)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("macho-adhoc-signature", self.codes(report))
        # A signed page that does not match the signed bytes is caught by recomputing the hashes.
        tampered = bytearray(good)
        tampered[text_section + 4] ^= 0x01  # inside the code covered by the CodeDirectory, after signing
        report = self.verdict("macho", stripped, bytes(tampered))
        self.assertEqual(report["verdict"], "FAIL")

    def test_real_fat_image_signed_per_slice(self):
        arm_stripped = fx.macho_strip_signature(fx.read_macho_base())
        x86_stripped = fx.macho_retarget(arm_stripped, fx.CPU_TYPE_X86_64, 3)
        arm_signed = fx.macho_sign(arm_stripped, entitlements=None)
        x86_signed = fx.macho_sign(x86_stripped, entitlements=None)
        fat_unsigned = fx.macho_fat([(fx.CPU_TYPE_ARM64, 0, 14, arm_stripped), (fx.CPU_TYPE_X86_64, 3, 12, x86_stripped)])
        fat_signed = fx.macho_fat([(fx.CPU_TYPE_ARM64, 0, 14, arm_signed), (fx.CPU_TYPE_X86_64, 3, 12, x86_signed)])
        report = self.verdict("macho", fat_unsigned, fat_signed)
        self.assertEqual(report["verdict"], "PASS", report["differences"])
        self.assertEqual({item["slice"] for item in report["allowed_differences"]}, {0, 1})

        one_unsigned = fx.macho_fat([(fx.CPU_TYPE_ARM64, 0, 14, arm_signed), (fx.CPU_TYPE_X86_64, 3, 12, x86_stripped)])
        report = self.verdict("macho", fat_unsigned, one_unsigned)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("macho-slice-unsigned", self.codes(report))
        lying_size = fx.macho_fat(
            [(fx.CPU_TYPE_ARM64, 0, 14, arm_signed), (fx.CPU_TYPE_X86_64, 3, 12, x86_signed)], sizes_override={1: 99}
        )
        report = self.verdict("macho", fat_unsigned, lying_size)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertTrue(report["differences"])

    def test_real_app_tree_with_real_executable(self):
        stripped = fx.macho_strip_signature(fx.read_macho_base())
        signed = fx.macho_sign(stripped, entitlements=None)
        trees = {}
        for name, executable, extra in (("u", stripped, {}), ("s", signed, {"Contents/_CodeSignature/CodeResources": b"seal"})):
            root = self.base / f"{name}.app"
            files = {"Contents/MacOS/veles-native-host": executable, "Contents/Info.plist": plistlib.dumps({"CFBundleName": "Veles"})}
            files.update(extra)
            for relative, data in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o755 if "MacOS" in relative else 0o644)
            trees[name] = root
        report = correspondence.compare(trees["u"], trees["s"], "tree")
        self.assertEqual(report["verdict"], "PASS", report["differences"])
        by_code = {}
        for item in report["allowed_differences"]:
            by_code.setdefault(item["code"], []).append(item.get("path"))
        self.assertEqual(by_code["tree-code-seal-added"], ["Contents/_CodeSignature/CodeResources"])
        for code in ("macho-code-signature-command", "macho-code-signature-blob", "macho-linkedit-extent", "macho-load-command-count"):
            self.assertEqual(by_code[code], ["Contents/MacOS/veles-native-host"], code)
        # Nothing else was excused: every allowed entry is one of the above or documented padding.
        self.assertLessEqual(set(by_code), {"tree-code-seal-added", "macho-signature-alignment-padding", "macho-code-signature-command",
                                           "macho-code-signature-blob", "macho-linkedit-extent", "macho-load-command-count"})

        # The same tree with a modified resource is not excused by the presence of a seal.
        (trees["s"] / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleName": "Evil"}))
        report = correspondence.compare(trees["u"], trees["s"], "tree")
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn(("tree-content-changed", "Contents/Info.plist"), [(d["code"], d.get("path")) for d in report["differences"]])


def superblob_blobs(image, sig_off):
    """(slot, absolute offset, magic, length, index entry offset) of every blob in a SuperBlob."""
    _magic, _length, count = struct.unpack_from(">III", image, sig_off)
    out = []
    for position in range(count):
        entry = sig_off + 12 + 8 * position
        slot, offset = struct.unpack_from(">II", image, entry)
        blob_magic, blob_length = struct.unpack_from(">II", image, sig_off + offset)
        out.append({"slot": slot, "offset": sig_off + offset, "magic": blob_magic, "length": blob_length, "entry": entry})
    return out


def linker_signed(**blob_kwargs):
    """An unsigned input carrying a (possibly unusual) linker-style signature without a CMS wrapper."""
    placeholder, meta = build_macho(signature=b"\0" * SIG_DATASIZE)
    blob = build_signature_blob(placeholder[: meta["sig_off"]], omit_cms=True, **blob_kwargs)
    return build_macho(signature=blob)


def raw_linkedit_section(**override):
    fields = dict(
        sectname="__original", segname="__LINKEDIT", addr=0x100000000 + 0x4000, size=16, offset=0x4000,
        align=2, reloff=0, nreloc=0, flags=0, r1=0, r2=0, r3=0,
    )
    fields.update(override)
    return struct.pack(
        "<16s16sQQIIIIIIII",
        fields["sectname"].encode().ljust(16, b"\0"), fields["segname"].encode().ljust(16, b"\0"),
        fields["addr"], fields["size"], fields["offset"], fields["align"], fields["reloff"], fields["nreloc"],
        fields["flags"], fields["r1"], fields["r2"], fields["r3"],
    )


class MachoReviewFindingTests(WorkDir):
    """Regression tests for review findings F1-F6 and F16 (Mach-O)."""

    def expect_fail(self, unsigned, signed, code=None, **kwargs):
        report = self.compare_bytes("macho", unsigned, signed, **kwargs)
        self.assertEqual(report["verdict"], "FAIL", report["allowed_differences"])
        self.assertEqual(report["allowed_differences"], [])
        if code is not None:
            self.assertIn(code, self.codes(report), report["differences"])
        return report

    def passes(self, unsigned, signed, **kwargs):
        report = self.compare_bytes("macho", unsigned, signed, **kwargs)
        self.assertEqual(report["verdict"], "PASS", report["differences"])
        return report

    # ---- F1: the __LINKEDIT section table is part of the compared command ------------------

    def test_f1_linkedit_section_table_is_compared_field_by_field(self):
        unsigned, _ = build_macho(linkedit_sections=[raw_linkedit_section()])
        same, _ = build_signed_macho(linkedit_sections=[raw_linkedit_section()])
        self.passes(unsigned, same)
        variants = {
            "sectname": dict(sectname="__CHANGED"),
            "segname": dict(segname="__OTHER"),
            "addr+offset": dict(addr=0x100000000 + 0x4004, offset=0x4004),
            "size": dict(size=8),
            "align": dict(align=3),
            "reloff": dict(reloff=0x4008),
            "nreloc": dict(nreloc=1),
            "flags": dict(flags=0x2),
            "reserved1": dict(r1=1),
            "reserved2": dict(r2=2),
            "reserved3": dict(r3=3),
        }
        for name, change in variants.items():
            with self.subTest(field=name):
                signed, _ = build_signed_macho(linkedit_sections=[raw_linkedit_section(**change)])
                # Audit correction: the inherited loop asserted only FAIL, which would also pass for an
                # unrelated parse failure. Require the reason and the region the review asked to be compared.
                report = self.expect_fail(unsigned, signed, "macho-content-changed")
                self.assertIn("load-command:LC_SEGMENT_64:__LINKEDIT", [d.get("region") for d in report["differences"]])
        signed, _ = build_signed_macho(linkedit_sections=[raw_linkedit_section(sectname="__CHANGED")])
        report = self.expect_fail(unsigned, signed, "macho-content-changed")
        self.assertIn("load-command:LC_SEGMENT_64:__LINKEDIT", [d.get("region") for d in report["differences"]])
        # A section added on one side only is also a change.
        self.expect_fail(build_macho()[0], build_signed_macho(linkedit_sections=[raw_linkedit_section()])[0], "macho-content-changed")

    # ---- F2: fat container order, CPU/subtype/alignment and packing are compared -----------

    def fat_images(self):
        arm_u, _ = build_macho()
        x86_u, _ = build_macho(cputype=CPU_X86_64)
        arm_s, _ = build_signed_macho()
        x86_s, _ = build_signed_macho(cputype=CPU_X86_64)
        return arm_u, x86_u, arm_s, x86_s

    def test_f2_fat_table_order_alignment_capabilities_and_packing_are_compared(self):
        arm_u, x86_u, arm_s, x86_s = self.fat_images()
        fat_u = build_fat([(CPU_ARM64, 0, arm_u, 14), (CPU_X86_64, 3, x86_u, 12)])
        good = build_fat([(CPU_ARM64, 0, arm_s, 14), (CPU_X86_64, 3, x86_s, 12)])
        self.passes(fat_u, good)
        second_offset_at = 8 + 20 + 8
        second = struct.unpack_from(">I", good, second_offset_at)[0]
        repacked = bytearray(good[:second] + b"\0" * 0x1000 + good[second:])
        struct.pack_into(">I", repacked, second_offset_at, second + 0x1000)
        cases = {
            "reversed-order": build_fat([(CPU_X86_64, 3, x86_s, 12), (CPU_ARM64, 0, arm_s, 14)]),
            "alignment-14-to-15": build_fat([(CPU_ARM64, 0, arm_s, 15), (CPU_X86_64, 3, x86_s, 12)]),
            "capability-bits": build_fat([(CPU_ARM64, -0x80000000, arm_s, 14), (CPU_X86_64, 3, x86_s, 12)]),
        }
        for name, candidate in cases.items():
            with self.subTest(name=name):
                self.expect_fail(fat_u, candidate, "macho-fat-metadata-changed")
        with self.subTest(name="extra-zero-padding-between-slices"):
            self.expect_fail(fat_u, bytes(repacked), "macho-invalid-fat")
        # The same applies when the *unsigned* side is the odd one.
        reversed_unsigned = build_fat([(CPU_X86_64, 3, x86_u, 12), (CPU_ARM64, 0, arm_u, 14)])
        self.expect_fail(reversed_unsigned, good, "macho-fat-metadata-changed")

    # ---- F3: entitlements semantics -------------------------------------------------------

    def test_f3_der_entitlements_slot_fails_closed_until_it_is_decoded(self):
        unsigned, _ = build_macho()
        signed, _ = build_signed_macho(der_entitlements=b"NOT-A-DER-ENTITLEMENTS-VALUE")
        report = self.expect_fail(unsigned, signed, "macho-der-entitlements-unsupported")
        self.assertEqual([d["code"] for d in report["differences"]], ["macho-der-entitlements-unsupported"])

    def test_f3_entitlement_value_types_are_compared_exactly(self):
        key = "com.apple.security.cs.allow-jit"
        unsigned, _ = build_macho()
        cases = [
            (1, True, False), (True, 1, False), (1, 1.0, False), (0, False, False), ("1", 1, False),
            (True, True, True), (1, 1, True), (1.5, 1.5, True), ("x", "x", True),
        ]
        for policy_value, embedded_value, expected in cases:
            with self.subTest(policy=repr(policy_value), embedded=repr(embedded_value)):
                signed, _ = build_signed_macho(entitlements={key: embedded_value})
                policy = self.put("policy.plist", plistlib.dumps({key: policy_value}))
                if expected:
                    self.passes(unsigned, signed, entitlements=policy)
                else:
                    self.expect_fail(unsigned, signed, "macho-entitlements-mismatch", entitlements=policy)

    def test_f3_baseline_entitlement_types_are_inherited_exactly_and_shapes_are_validated(self):
        key = "com.apple.security.cs.allow-jit"
        stripped = fx.macho_strip_signature(fx.read_macho_base())
        as_int = fx.macho_sign(stripped, entitlements=plistlib.dumps({key: 1}))
        as_bool = fx.macho_sign(stripped, entitlements=plistlib.dumps({key: True}))
        self.passes(as_int, fx.macho_sign(stripped, entitlements=plistlib.dumps({key: 1})))
        self.expect_fail(as_int, as_bool, "macho-entitlements-mismatch")
        self.expect_fail(as_bool, as_int, "macho-entitlements-mismatch")
        # An entitlements plist whose top level is not a dictionary is not an entitlement set.
        not_a_dict, _ = build_signed_macho(entitlements=["com.apple.security.cs.allow-jit"])
        self.expect_fail(build_macho()[0], not_a_dict, "macho-invalid-entitlements")
        array_policy = self.put("array.plist", plistlib.dumps(["x"]))
        with self.assertRaises(correspondence.InputError):
            self.compare_bytes("macho", build_macho()[0], build_signed_macho()[0], entitlements=array_policy)

    # ---- F4: an unreadable unsigned signature is a rejection, not "no entitlements" --------

    def test_f4_unreadable_or_ambiguous_unsigned_signature_is_rejected(self):
        signed, _ = build_signed_macho()
        unsigned, meta = linker_signed()
        self.passes(unsigned, signed)  # control: a well-formed linker signature is fine
        sig = meta["sig_off"]
        blobs = {b["slot"]: b for b in superblob_blobs(unsigned, sig)}

        def mutated(edit):
            image = bytearray(unsigned)
            edit(image)
            return bytes(image)

        cases = {
            "bad-magic": mutated(lambda i: struct.pack_into(">I", i, sig, 0xFADE0CC1)),
            "declared-length-too-short": mutated(lambda i: struct.pack_into(">I", i, sig + 4, 40)),
            "duplicate-slot": mutated(lambda i: struct.pack_into(">I", i, blobs[2]["entry"], 0)),
            "overlapping-blobs": mutated(lambda i: struct.pack_into(">I", i, blobs[2]["entry"] + 4, blobs[0]["offset"] - sig + 8)),
            "blob-past-the-region": mutated(lambda i: struct.pack_into(">I", i, blobs[2]["offset"] + 4, 0xFFFF)),
            "implausible-count": mutated(lambda i: struct.pack_into(">I", i, sig + 8, 0)),
        }
        for name, candidate in cases.items():
            with self.subTest(name=name):
                report = self.expect_fail(candidate, signed, "macho-invalid-signature-blob")
                self.assertEqual({d.get("side") for d in report["differences"]}, {"unsigned"})
        # Duplicate entitlements entries in the unsigned input are ambiguous.
        with_ent, ent_meta = linker_signed(entitlements={"k": True})
        ent_blobs = {b["slot"]: b for b in superblob_blobs(with_ent, ent_meta["sig_off"])}
        duplicate = bytearray(with_ent)
        struct.pack_into(">I", duplicate, ent_blobs[2]["entry"], 5)  # two slot-5 entries
        self.expect_fail(bytes(duplicate), signed, "macho-invalid-signature-blob")
        # A DER entitlements slot cannot be interpreted, on the unsigned side either.
        with_der, _ = linker_signed(der_entitlements=b"DER")
        self.expect_fail(with_der, signed, "macho-der-entitlements-unsupported")

    # ---- F5: only understood CodeDirectory layouts are accepted ---------------------------

    def test_f5_unsupported_codedirectory_layouts_are_rejected(self):
        unsigned, _ = build_macho()
        signed, meta = build_signed_macho()
        self.passes(unsigned, signed)
        cd = {b["slot"]: b for b in superblob_blobs(signed, meta["sig_off"])}[0]["offset"]

        def patched(offset, value, fmt=">I"):
            image = bytearray(signed)
            struct.pack_into(fmt, image, cd + offset, value)
            return bytes(image)

        cases = {
            "scatterOffset": patched(44, 0xFFFFFFFF),
            "version-newer-than-implemented": patched(8, 0x20700),
            "version-between-layouts": patched(8, 0x20150),
            "version-future-major": patched(8, 0x30000),
            "version-too-old": patched(8, 0x20000),
        }
        for name, candidate in cases.items():
            with self.subTest(name=name):
                self.expect_fail(unsigned, candidate, "macho-unsupported-signature-layout")
        # v0x20400 declares an 88-byte header; this CodeDirectory's identifier starts at byte 48.
        self.expect_fail(unsigned, patched(8, 0x20400), "macho-invalid-signature-blob")

    def test_f5_newer_layout_fields_are_validated_on_the_real_image(self):
        # Added during the audit: the inherited F5 test patched only the v0x20100 builder, leaving the
        # 0x20200+ selectors (team identifier, spare fields, executable segment) unexercised.
        stripped = fx.macho_strip_signature(fx.read_macho_base())
        signed = fx.macho_sign(stripped, entitlements=None)
        self.passes(stripped, signed)
        slice_ = correspondence.MachSlice(signed)
        cd = {b["slot"]: b for b in superblob_blobs(signed, slice_.dataoff)}[0]["offset"]
        self.assertEqual(struct.unpack_from(">I", signed, cd + 8)[0], 0x20400)

        def patched(offset, value, fmt=">I"):
            image = bytearray(signed)
            struct.pack_into(fmt, image, cd + offset, value)
            return bytes(image)

        cases = {
            "spare2": (patched(40, 1), "macho-unsupported-signature-layout"),
            "spare3": (patched(52, 1), "macho-unsupported-signature-layout"),
            "team-offset-outside-the-blob": (patched(48, 0xFFFF), "macho-invalid-signature-blob"),
            "team-offset-inside-the-header": (patched(48, 8), "macho-invalid-signature-blob"),
            "exec-segment-past-the-signed-range": (patched(72, slice_.dataoff + 1, ">Q"), "macho-invalid-signature-blob"),
        }
        for name, (candidate, code) in cases.items():
            with self.subTest(name=name):
                self.expect_fail(stripped, candidate, code)

    # ---- F6: every supported command is validated before it is decoded ---------------------

    def test_f6_malformed_known_load_commands_fail_instead_of_passing(self):
        bad = {
            "lc-note-8-bytes": struct.pack("<II", 0x31, 8),
            "lc-load-dylib-8-bytes": struct.pack("<II", 0x0C, 8),
            "lc-segment-64-8-bytes": struct.pack("<II", 0x19, 8),
            "lc-rpath-8-bytes": struct.pack("<II", 0x8000001C, 8),
            "dylib-name-offset-out-of-range": struct.pack("<IIIIII", 0x0C, 32, 0x400, 2, 0x10000, 0x10000) + b"lib\0\0\0\0\0",
            "dylib-name-unterminated": struct.pack("<IIIIII", 0x0C, 32, 24, 2, 0x10000, 0x10000) + b"AAAAAAAA",
            "build-version-tool-count": struct.pack("<IIIIII", 0x32, 24, 1, 0, 0, 3),
            "data-in-code-outside-linkedit": struct.pack("<IIII", 0x29, 16, 0x10, 0x20),
            "dyld-info-outside-linkedit": struct.pack("<II", 0x80000022, 48) + struct.pack("<10I", 0x10, 8, 0, 0, 0, 0, 0, 0, 0, 0),
            "note-range-past-the-file": struct.pack("<II16sQQ", 0x31, 40, b"owner", 1 << 40, 8),
        }
        for name, command in bad.items():
            with self.subTest(name=name):
                unsigned, _ = build_macho(extra_load_command=command)
                signed, _ = build_signed_macho(extra_load_command=command)
                self.expect_fail(unsigned, signed, "macho-invalid-load-commands")

    def test_f6_virtual_memory_and_section_geometry_is_validated(self):
        overlapping = segment("__ODD", 0x100000000, 0x1000, 0, 0, 3, 3)  # zero file size, overlaps __TEXT in memory
        unsigned, _ = build_macho(extra_load_command=overlapping)
        signed, _ = build_signed_macho(extra_load_command=overlapping)
        self.expect_fail(unsigned, signed, "macho-invalid-load-commands")
        # A section whose address is outside its segment (real image, re-signed after the edit).
        stripped = fx.macho_strip_signature(fx.read_macho_base())
        text = correspondence.MachSlice(stripped).segments[1]
        mutated = bytearray(stripped)
        struct.pack_into("<Q", mutated, text.command.offset + 72 + 32, text.vmaddr + text.vmsize + 0x1000)
        self.expect_fail(bytes(mutated), fx.macho_sign(bytes(mutated)), "macho-invalid-load-commands")

    def test_f6_a_format_error_becomes_a_fail_report_not_a_traceback_with_a_stale_report(self):
        command = struct.pack("<II", 0x19, 8)
        unsigned = self.put("u.macho", build_macho(extra_load_command=command)[0])
        signed = self.put("s.macho", build_signed_macho(extra_load_command=command)[0])
        report = self.base / "report.json"
        report.write_text(json.dumps({"schema": 1, "verdict": "PASS"}))
        done = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--unsigned", str(unsigned), "--signed", str(signed), "--kind", "macho", "--report", str(report)],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertEqual(json.loads(report.read_text())["verdict"], "FAIL")

    def test_f6_an_unexpected_internal_error_is_exit_2_with_an_error_report(self):
        unsigned = self.put("u.macho", build_macho()[0])
        signed = self.put("s.macho", build_signed_macho()[0])
        report = self.base / "report.json"
        report.write_text(json.dumps({"schema": 1, "verdict": "PASS"}))
        with mock.patch.object(correspondence, "compare", side_effect=RuntimeError("internal fault")):
            code = correspondence.main(["--unsigned", str(unsigned), "--signed", str(signed), "--kind", "macho", "--report", str(report)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(report.read_text())["verdict"], "ERROR")

    # ---- F16: the report identifies the entitlement policy and the architectures ------------

    def test_f16_report_identifies_policy_source_policy_digest_and_architectures(self):
        entitlements = {"com.apple.security.device.bluetooth": True}
        arm_u, x86_u, _, _ = self.fat_images()
        arm_s, _ = build_signed_macho(entitlements=entitlements)
        x86_s, _ = build_signed_macho(entitlements=entitlements, cputype=CPU_X86_64)
        fat_u = build_fat([(CPU_ARM64, 0, arm_u, 14), (CPU_X86_64, 3, x86_u, 12)])
        fat_s = build_fat([(CPU_ARM64, 0, arm_s, 14), (CPU_X86_64, 3, x86_s, 12)])
        policy_bytes = plistlib.dumps(entitlements)
        report = self.passes(fat_u, fat_s, entitlements=self.put("policy.plist", policy_bytes))
        policy = report["entitlement_policy"]
        self.assertEqual(policy["source"], "explicit")
        self.assertEqual(policy["file_sha256"], sha(policy_bytes))
        self.assertRegex(policy["canonical_sha256"], r"^[0-9a-f]{64}$")
        # Same semantics, different serialisation: file digest differs, canonical digest does not.
        reserialised = plistlib.dumps(entitlements, fmt=plistlib.FMT_BINARY)
        other = self.passes(fat_u, fat_s, entitlements=self.put("policy2.plist", reserialised))["entitlement_policy"]
        self.assertNotEqual(other["file_sha256"], policy["file_sha256"])
        self.assertEqual(other["canonical_sha256"], policy["canonical_sha256"])
        # A different policy value type is a different canonical policy.
        typed = self.put("typed.plist", plistlib.dumps({"com.apple.security.device.bluetooth": 1}))
        arm_int, _ = build_signed_macho(entitlements={"com.apple.security.device.bluetooth": 1})
        x86_int, _ = build_signed_macho(entitlements={"com.apple.security.device.bluetooth": 1}, cputype=CPU_X86_64)
        fat_int = build_fat([(CPU_ARM64, 0, arm_int, 14), (CPU_X86_64, 3, x86_int, 12)])
        different = self.passes(fat_u, fat_int, entitlements=typed)["entitlement_policy"]
        self.assertNotEqual(different["canonical_sha256"], policy["canonical_sha256"])
        # Inherited policy says so (no policy file digest).
        inherited = self.passes(build_macho()[0], build_signed_macho()[0])["entitlement_policy"]
        self.assertEqual(inherited, {"source": "inherited-from-unsigned", "file_sha256": None, "canonical_sha256": None})
        # Fat slices carry their architecture, not only a position.
        slices = report["slices"]
        self.assertEqual([item["architecture"]["cputype"] for item in slices], ["0x0100000c", "0x01000007"])
        self.assertEqual([item["architecture"]["align"] for item in slices], [14, 12])
        for item in report["allowed_differences"]:
            if "slice" in item:
                self.assertEqual(item["architecture"], slices[item["slice"]]["architecture"])
        # PE reports do not pretend to have an entitlement policy.
        pe_u, meta = build_pe()
        self.assertNotIn("entitlement_policy", self.compare_bytes("pe", pe_u, authenticode_like(pe_u, meta)))

    def test_f16_hash_fields_per_kind_are_the_documented_ones(self):
        pe_u, meta = build_pe()
        pe = self.compare_bytes("pe", pe_u, authenticode_like(pe_u, meta))["input_hashes"]
        self.assertEqual(
            sorted(pe),
            ["signed_invariant_sha256", "signed_matches_invariant", "signed_sha256", "unsigned_invariant_sha256", "unsigned_sha256"],
        )
        macho = self.compare_bytes("macho", build_macho()[0], build_signed_macho()[0])["input_hashes"]
        self.assertEqual(sorted(macho), ["signed_matches_invariant", "signed_sha256", "unsigned_sha256"])
        # Audit correction: the inherited assertion grepped five phrases only. Every key a report actually
        # carries must be named in the documentation, so a new or renamed key cannot go undocumented.
        tree_dir = self.base / "doc-tree"
        tree_dir.mkdir()
        (tree_dir / "file").write_bytes(b"x")
        tree = correspondence.compare(tree_dir, tree_dir, "tree")
        self.assertEqual(tree["entitlement_policy"]["source"], "inherited-from-unsigned")
        for key in [*pe, *macho, *tree["input_hashes"]]:
            self.assertIn(key, correspondence.__doc__)
        for key in ("slices", "entitlement_policy", "canonical_sha256", "file_sha256"):
            self.assertIn(key, correspondence.__doc__)
        for name in ("Report fields", "verify_separately", "signtool verify", "codesign --verify", "stapler validate", "spctl"):
            self.assertIn(name, correspondence.__doc__)


class PortableFindingTests(WorkDir):
    """Review F10 (tree executables) and F15 (report freshness on argument errors)."""

    def tree(self, name, files):
        root = self.base / name
        for relative, (data, mode) in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(mode)
        return root

    def test_f10_truncated_or_invalid_pe_candidates_in_a_tree_are_rejected(self):
        valid_header = b"MZ" + b"\0" * 0x3A
        candidates = {
            "mz-only": b"MZ" + b"\0" * 30,
            "oversized-lfanew": valid_header + struct.pack("<I", 0x7FFFFFFF) + b"\0" * 64,
            "lfanew-inside-the-dos-header": valid_header + struct.pack("<I", 0x10) + b"\0" * 64,
            "no-pe-signature": valid_header + struct.pack("<I", 0x40) + b"NOPE" + b"\0" * 64,
        }
        for name, data in candidates.items():
            with self.subTest(name=name):
                files = {"bin/tool.exe": (data, 0o755), "docs/readme.txt": (b"hello", 0o644)}
                report = correspondence.compare(self.tree(f"u-{name}", files), self.tree(f"s-{name}", files), "tree")
                self.assertEqual(report["verdict"], "FAIL")
                self.assertEqual(report["allowed_differences"], [])
                self.assertIn(("tree-unparseable-executable", "bin/tool.exe"), [(d["code"], d.get("path")) for d in report["differences"]])
        # A PE extension makes a non-executable file a candidate too; plain data that merely starts "MZ" is a resource.
        dll = {"lib/x.dll": (b"MZ" + b"\0" * 30, 0o644)}
        self.assertEqual(correspondence.compare(self.tree("u-dll", dll), self.tree("s-dll", dll), "tree")["verdict"], "FAIL")
        # Audit addition: the execute bit alone makes an MZ file an executable candidate, whatever its name.
        bare = {"bin/tool": (b"MZ" + b"\0" * 30, 0o755)}
        self.assertEqual(correspondence.compare(self.tree("u-bare", bare), self.tree("s-bare", bare), "tree")["verdict"], "FAIL")
        plain = {"notes/mz.dat": (b"MZ these are my notes", 0o644)}
        self.assertEqual(correspondence.compare(self.tree("u-plain", plain), self.tree("s-plain", plain), "tree")["verdict"], "PASS")

    def test_f15_report_is_replaced_for_every_report_spelling_on_argument_errors(self):
        unsigned = self.put("u.bin", b"x")
        report = self.base / "report.json"
        for spelling in (["--report", str(report)], [f"--report={report}"]):
            with self.subTest(spelling=spelling[0]):
                report.write_text(json.dumps({"schema": 1, "verdict": "PASS"}))
                code = correspondence.main(["--unsigned", str(unsigned), "--signed", str(unsigned), "--kind", "pe", *spelling, "--unknown"])
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(report.read_text())["verdict"], "ERROR")
        # Abbreviated option names are refused rather than guessed at. Audit correction: the inherited
        # version used a pair that exits 2 for an unrelated reason (not a PE), so it could not tell whether
        # `--rep` was refused. Identical trees PASS (exit 0) if an abbreviation were accepted.
        tree = self.tree("abbrev", {"file.txt": (b"same", 0o644)})
        report.write_text(json.dumps({"schema": 1, "verdict": "PASS"}))
        self.assertEqual(correspondence.main(["--unsigned", str(tree), "--signed", str(tree), "--kind", "tree", "--report", str(report)]), 0)
        self.assertEqual(correspondence.main(["--unsigned", str(tree), "--signed", str(tree), "--kind", "tree", "--rep", str(report)]), 2)


class Round2FatOrderTests(WorkDir):
    """Re-review N1: the physical order of fat slices is compared, not only the table order."""

    def images(self):
        arm_u, _ = build_macho()
        x86_u, _ = build_macho(cputype=CPU_X86_64)
        arm_s, _ = build_signed_macho()
        x86_s, _ = build_signed_macho(cputype=CPU_X86_64)
        return arm_u, x86_u, arm_s, x86_s

    @staticmethod
    def with_table_swapped(blob):
        """Exchange the two 20-byte table entries (offsets travel with their entry)."""
        swapped = bytearray(blob)
        swapped[8:28], swapped[28:48] = blob[28:48], blob[8:28]
        return bytes(swapped)

    def test_n1_reversing_the_physical_slice_order_while_restoring_the_table_order_fails(self):
        arm_u, x86_u, arm_s, x86_s = self.images()
        fat_u = build_fat([(CPU_ARM64, 0, arm_u, 14), (CPU_X86_64, 3, x86_u, 12)])
        self.assertEqual(self.compare_bytes("macho", fat_u, build_fat([(CPU_ARM64, 0, arm_s, 14), (CPU_X86_64, 3, x86_s, 12)]))["verdict"], "PASS")
        # Signed: x86_64 then arm64 in the file, then the table entries exchanged so the table reads arm64, x86_64.
        relocated = self.with_table_swapped(build_fat([(CPU_X86_64, 3, x86_s, 12), (CPU_ARM64, 0, arm_s, 14)]))
        self.assertEqual(struct.unpack_from(">iiIII", relocated, 8)[0] & 0xFFFFFFFF, CPU_ARM64)  # the table order is restored
        self.assertGreater(struct.unpack_from(">I", relocated, 8 + 8)[0], struct.unpack_from(">I", relocated, 28 + 8)[0])
        report = self.compare_bytes("macho", fat_u, relocated)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(report["allowed_differences"], [])
        self.assertEqual(self.codes(report), ["macho-fat-physical-order-changed"], report["differences"])
        # The unsigned side being the physically reordered one is the same change.
        reordered_unsigned = self.with_table_swapped(build_fat([(CPU_X86_64, 3, x86_u, 12), (CPU_ARM64, 0, arm_u, 14)]))
        report = self.compare_bytes("macho", reordered_unsigned, build_fat([(CPU_ARM64, 0, arm_s, 14), (CPU_X86_64, 3, x86_s, 12)]))
        self.assertEqual(self.codes(report), ["macho-fat-physical-order-changed"], report["differences"])

    def test_n1_a_consistent_physical_order_that_differs_from_the_table_order_is_still_supported(self):
        arm_u, x86_u, arm_s, x86_s = self.images()
        unsigned = self.with_table_swapped(build_fat([(CPU_X86_64, 3, x86_u, 12), (CPU_ARM64, 0, arm_u, 14)]))
        signed = self.with_table_swapped(build_fat([(CPU_X86_64, 3, x86_s, 12), (CPU_ARM64, 0, arm_s, 14)]))
        report = self.compare_bytes("macho", unsigned, signed)
        self.assertEqual(report["verdict"], "PASS", report["differences"])


class Round2PolicyPlistTests(WorkDir):
    """Re-review N3 (expansion budget) and N4 (string keys) for binary entitlement policies."""

    KEY = "com.apple.security.cs.allow-jit"

    def policy_file(self, blob, name="policy.plist"):
        return self.put(name, blob)

    def assert_policy_refused(self, blob, text):
        with self.assertRaises(correspondence.InputError) as caught:
            correspondence.load_policy(self.policy_file(blob))
        self.assertIn(text, str(caught.exception))
        return caught.exception

    @staticmethod
    def integer_key(blob, text="1"):
        """Swap the ASCII-string key object (0x51 '1') for the integer 1 (0x10 0x01): same length, same offsets."""
        marker = bytes([0x50 + len(text)]) + text.encode()
        assert blob.count(marker) == 1, blob
        return blob.replace(marker, b"\x10" + bytes([int(text)]))

    def test_n4_integer_dictionary_keys_in_a_binary_policy_are_refused_not_coerced(self):
        flat = self.integer_key(plistlib.dumps({"1": True}, fmt=plistlib.FMT_BINARY))
        self.assertEqual(plistlib.loads(flat), {1: True})  # the premise: plistlib really hands back an int key
        self.assert_policy_refused(flat, "string key")
        nested = self.integer_key(plistlib.dumps({"outer": {"1": True}}, fmt=plistlib.FMT_BINARY))
        self.assertEqual(plistlib.loads(nested), {"outer": {1: True}})
        self.assert_policy_refused(nested, "string key")
        inside_array = self.integer_key(plistlib.dumps({"outer": [{"1": 2}]}, fmt=plistlib.FMT_BINARY))
        self.assert_policy_refused(inside_array, "string key")
        # canonical_plist itself never stringifies a key.
        with self.assertRaises(ValueError):
            correspondence.canonical_plist({1: True})

    def test_n4_a_signed_xml_entitlement_never_matches_an_integer_key_policy(self):
        unsigned, _ = build_macho()
        signed, _ = build_signed_macho(entitlements={"1": True})
        control = self.put("string-key.plist", plistlib.dumps({"1": True}, fmt=plistlib.FMT_BINARY))
        self.assertEqual(self.compare_bytes("macho", unsigned, signed, entitlements=control)["verdict"], "PASS")
        forged = self.put("int-key.plist", self.integer_key(plistlib.dumps({"1": True}, fmt=plistlib.FMT_BINARY)))
        with self.assertRaises(correspondence.InputError):
            self.compare_bytes("macho", unsigned, signed, entitlements=forged)

    def hostile_tower(self, levels=16):
        node = True
        for _ in range(levels):
            node = [node, node]
        return plistlib.dumps({"k": node}, fmt=plistlib.FMT_BINARY)

    def test_n3_shared_reference_amplification_is_refused_within_a_bounded_visit_and_memory_budget(self):
        blob = self.hostile_tower()
        self.assertLess(len(blob), 200)  # the review's 113-byte policy
        visits = []
        real = correspondence.canonical_plist

        def counting(value):
            visits.append(1)
            return real(value)

        tracemalloc.start()
        try:
            with mock.patch.object(correspondence, "canonical_plist", counting):
                self.assert_policy_refused(blob, "budget")
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLessEqual(len(visits), correspondence.MAX_PLIST_NODES, len(visits))
        self.assertLess(peak, 4 * 1024 * 1024, peak)

    def test_n3_scalar_bytes_nesting_and_node_count_are_each_bounded(self):
        shared = "s" * 400_000
        wide = plistlib.dumps({"k": [shared] * 64}, fmt=plistlib.FMT_BINARY)  # 25.6 M characters once expanded
        self.assertLess(len(wide), 500_000)
        self.assert_policy_refused(wide, "budget")
        deep = 40
        value = "leaf"
        for _ in range(deep):
            value = [value]
        self.assert_policy_refused(plistlib.dumps({"k": value}, fmt=plistlib.FMT_BINARY), "budget")
        many = plistlib.dumps({"k": [True] * (correspondence.MAX_PLIST_NODES + 1)}, fmt=plistlib.FMT_XML)
        self.assertLess(len(many), correspondence.MAX_ENTITLEMENTS_BYTES)
        self.assert_policy_refused(many, "budget")

    def test_n3_an_ordinary_binary_policy_and_its_xml_twin_have_the_same_canonical_digest(self):
        value = {self.KEY: True, "list": [1, 2.5, "x", b"d", {"nested": False}], "shared": [[True, True], [True, True]]}
        digests = set()
        for fmt in (plistlib.FMT_BINARY, plistlib.FMT_XML):
            policy = correspondence.load_policy(self.policy_file(plistlib.dumps(value, fmt=fmt)))
            digests.add(policy.report()["canonical_sha256"])
        self.assertEqual(len(digests), 1)
        # A shared sub-structure that stays inside the budget is accepted (6 levels = 127 nodes).
        tower = self.hostile_tower(6)
        self.assertEqual(correspondence.load_policy(self.policy_file(tower)).report()["source"], "explicit")


class Round2LoadCommandTests(WorkDir):
    """Re-review N5: cross-command checks are order independent and string padding is zero."""

    LC_DYSYMTAB = 0x0B

    def dysymtab(self, **partitions):
        values = dict(ilocal=0, nlocal=0, iext=0, next=0, iundef=0, nundef=0)
        values.update(partitions)
        fields = [values["ilocal"], values["nlocal"], values["iext"], values["next"], values["iundef"], values["nundef"]]
        return struct.pack("<II18I", self.LC_DYSYMTAB, 80, *fields, *([0] * 12))

    def pair(self, **kwargs):
        return build_macho(**kwargs)[0], build_signed_macho(**kwargs)[0]

    def verdict(self, **kwargs):
        unsigned, signed = self.pair(**kwargs)
        return self.compare_bytes("macho", unsigned, signed)

    def test_n5_a_dysymtab_partition_past_the_symbol_table_fails_in_either_command_order(self):
        reviewer = self.dysymtab(iext=0xFFFFFFFF, next=2)  # the symbol table has two symbols
        for placement in ("pre_symtab_command", "extra_load_command"):
            with self.subTest(placement=placement, case="out-of-range"):
                report = self.verdict(**{placement: reviewer})
                self.assertEqual(report["verdict"], "FAIL")
                self.assertIn("macho-invalid-load-commands", self.codes(report), report["differences"])
            with self.subTest(placement=placement, case="well-formed"):
                good = self.dysymtab(iext=0, next=2, iundef=2, nundef=0)
                self.assertEqual(self.verdict(**{placement: good})["verdict"], "PASS")
        # No symbol table at all: any partition is out of range, an all-zero one is not.
        for placement in ("pre_symtab_command", "extra_load_command"):
            with self.subTest(placement=placement, case="no-symtab"):
                report = self.verdict(symtab_ident=0x2C, **{placement: self.dysymtab(next=1)})
                self.assertIn("macho-invalid-load-commands", self.codes(report), report["differences"])
                self.assertEqual(self.verdict(symtab_ident=0x2C, **{placement: self.dysymtab()})["verdict"], "PASS")

    def test_n5_duplicate_symbol_table_declarations_are_refused(self):
        symtab = struct.pack("<IIIIII", LC_SYMTAB, 24, 0x4000, 2, 0x4020, 8)
        report = self.verdict(extra_load_command=symtab)
        self.assertIn("macho-invalid-load-commands", self.codes(report), report["differences"])
        two = self.dysymtab()
        report = self.verdict(pre_symtab_command=two, extra_load_command=two)
        self.assertIn("macho-invalid-load-commands", self.codes(report), report["differences"])

    def test_n5_string_areas_must_be_zero_after_the_terminator(self):
        def dylib(area, offset=24):
            size = offset + len(area)
            return struct.pack("<IIIIII", 0x0C, size, offset, 2, 0x10000, 0x10000) + b"\0" * (offset - 24) + area

        def path_command(ident, area, header=12):
            return struct.pack("<III", ident, header + len(area), header) + area

        junk = {
            "dylib-junk-after-the-terminator": dylib(b"lib\0JUNK"),
            "dylib-junk-in-the-last-byte": dylib(b"lib\0\0\0\0X"),
            "dylib-gap-before-the-string": struct.pack("<IIIIII", 0x0C, 40, 32, 2, 0x10000, 0x10000) + b"JUNK" + b"\0" * 4 + b"lib\0\0\0\0\0",
            "rpath-junk-after-the-terminator": path_command(0x8000001C, b"/p\0X"),
            "dylinker-junk-after-the-terminator": path_command(0x0E, b"/usr/lib/dyld\0JUNK\0\0"),
        }
        for name, command in junk.items():
            with self.subTest(name=name):
                self.assertEqual(len(command) % 8, 0, (name, len(command)))
                report = self.verdict(extra_load_command=command)
                self.assertEqual(report["verdict"], "FAIL")
                self.assertIn("macho-invalid-load-commands", self.codes(report), report["differences"])
                self.assertTrue(any("padding" in d["message"] for d in report["differences"]), report["differences"])
        clean = {
            "dylib": dylib(b"lib\0\0\0\0\0"),
            "rpath": path_command(0x8000001C, b"/p\0\0"),
            "dylinker": path_command(0x0E, b"/usr/lib/dyld\0" + b"\0" * 6),
        }
        for name, command in clean.items():
            with self.subTest(name=name, control=True):
                self.assertEqual(len(command) % 8, 0, (name, len(command)))
                self.assertEqual(self.verdict(extra_load_command=command)["verdict"], "PASS")


class Round2CodeLimitTests(WorkDir):
    """Re-review N7: the CodeDirectory's effective code limit follows Apple's reader (codeLimit64 when non-zero)."""

    def setUp(self):
        super().setUp()
        self.stripped = fx.macho_strip_signature(fx.read_macho_base())
        self.signed = fx.macho_sign(self.stripped, entitlements=None)
        self.slice = correspondence.MachSlice(self.signed)
        self.cd = {b["slot"]: b for b in superblob_blobs(self.signed, self.slice.dataoff)}[0]["offset"]
        self.dataoff = self.slice.dataoff
        self.assertEqual(struct.unpack_from(">I", self.signed, self.cd + 8)[0], 0x20400)
        self.assertEqual(struct.unpack_from(">I", self.signed, self.cd + 32)[0], self.dataoff)
        self.assertEqual(struct.unpack_from(">Q", self.signed, self.cd + 56)[0], 0)

    def limits(self, limit32, limit64):
        image = bytearray(self.signed)
        struct.pack_into(">I", image, self.cd + 32, limit32)
        struct.pack_into(">Q", image, self.cd + 56, limit64)
        return bytes(image)

    def report(self, limit32, limit64):
        return self.compare_bytes("macho", self.stripped, self.limits(limit32, limit64))

    def test_n7_a_dual_populated_code_directory_with_the_correct_effective_limit_passes(self):
        self.assertEqual(self.report(self.dataoff, 0)["verdict"], "PASS")  # control: the 32-bit-only form
        for label, limit32 in (("32-bit field kept", self.dataoff), ("32-bit sentinel", 0xFFFFFFFF)):
            with self.subTest(form=label):
                report = self.report(limit32, self.dataoff)
                self.assertEqual(report["verdict"], "PASS", report["differences"])
                blob = [item for item in report["allowed_differences"] if item["code"] == "macho-code-signature-blob"][0]
                self.assertEqual(blob["code_directory"]["code_limit"], self.dataoff)

    def test_n7_a_wrong_effective_limit_or_an_inconsistent_32_bit_field_still_fails(self):
        cases = {
            "64-bit limit one byte long": (0xFFFFFFFF, self.dataoff + 1, "does not cover exactly"),
            "64-bit limit short": (0xFFFFFFFF, self.dataoff - 16, "does not cover exactly"),
            "64-bit limit long beside the old 32-bit limit": (self.dataoff, self.dataoff + 1, "32-bit code limit"),
            "32-bit limit wrong, no 64-bit limit": (self.dataoff + 16, 0, "does not cover exactly"),
            "32-bit field contradicts the 64-bit limit": (self.dataoff + 16, self.dataoff, "32-bit code limit"),
            "32-bit field zero beside a 64-bit limit": (0, self.dataoff, "32-bit code limit"),
        }
        for name, (limit32, limit64, text) in cases.items():
            with self.subTest(name=name):
                report = self.report(limit32, limit64)
                self.assertEqual(report["verdict"], "FAIL")
                self.assertEqual(report["allowed_differences"], [])
                self.assertEqual(self.codes(report), ["macho-invalid-signature-blob"], report["differences"])
                self.assertIn(text, report["differences"][0]["message"])


class Round2ArchiveSnapshotTests(WorkDir):
    """Re-review N8: inventory, archive hash and member reads share one immutable snapshot."""

    def lzma_bomb(self):
        blob = raw_zip.make_codec_zip([("a", b"x" * (1 << 20))], zipfile.ZIP_LZMA)
        return raw_zip.patch_member(blob, 0, crc=zlib.crc32(b"x"), usize=1)

    def test_n8_a_zip_replaced_after_inventory_is_never_reread_through_an_unbounded_codec(self):
        genuine = raw_zip.make_zip([("a", b"x")])
        path = self.put("payload.zip", genuine)
        source = correspondence.TreeSource(path)
        expected_inventory = source.inventory
        path.write_bytes(self.lzma_bomb())  # replaced between the inventory and the content read
        decoded = []
        real = lzma.LZMADecompressor

        class Counting:  # LZMADecompressor is an immutable type: wrap it to account for every decoded byte
            def __init__(self, *args, **kwargs):
                self._decompressor = real(*args, **kwargs)

            def decompress(self, data, max_length=-1):
                out = self._decompressor.decompress(data, max_length)
                decoded.append(len(out))
                return out

            def __getattr__(self, name):
                return getattr(self._decompressor, name)

        with mock.patch.object(lzma, "LZMADecompressor", Counting):
            data = source.read("a")
        self.assertEqual(data, b"x")
        self.assertEqual(decoded, [])  # no codec ran at all on the replacement
        self.assertEqual(source.archive_sha256, correspondence.sha256(genuine))
        self.assertEqual(source.inventory, expected_inventory)

    def test_n8_replacing_an_archive_with_different_valid_content_does_not_change_what_is_read(self):
        for suffix, genuine, other in (
            (".zip", raw_zip.make_zip([("a", b"x")]), raw_zip.make_zip([("a", b"y")])),
            (".tar", raw_zip.make_tar([("file", "a", b"x", 0o644)]), raw_zip.make_tar([("file", "a", b"y", 0o644)])),
        ):
            with self.subTest(suffix=suffix):
                path = self.put(f"payload{suffix}", genuine)
                source = correspondence.TreeSource(path)
                path.write_bytes(other)
                self.assertEqual(source.read("a"), b"x")
                self.assertEqual(source.archive_sha256, correspondence.sha256(genuine))

    def test_n8_the_archive_is_read_from_disk_exactly_once(self):
        path = self.put("payload.tar", raw_zip.make_tar([("file", "a", b"x", 0o644), ("file", "b", b"yy", 0o644)]))
        reads = []
        real = correspondence.read_regular_file

        def recording(target):
            reads.append(Path(target))
            return real(target)

        with mock.patch.object(correspondence, "read_regular_file", recording):
            source = correspondence.TreeSource(path)
            self.assertEqual([source.read("a"), source.read("b")], [b"x", b"yy"])
        self.assertEqual(reads, [path])

    def test_n8_a_tree_comparison_uses_the_snapshot_for_every_member(self):
        unsigned = self.put("u.zip", raw_zip.make_zip([("keep.txt", b"same"), ("tool.exe", b"MZ" + b"\0" * 30)]))
        signed = self.put("s.zip", raw_zip.make_zip([("keep.txt", b"same"), ("tool.exe", b"MZ" + b"\0" * 30)]))
        with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("zipfile must not be used for reads")):
            report = correspondence.compare(unsigned, signed, "tree")
        self.assertEqual(report["verdict"], "FAIL")  # the unreadable MZ candidate is rejected, not a crash
        self.assertIn(("tree-unparseable-executable", "tool.exe"), [(d["code"], d.get("path")) for d in report["differences"]])
        plain_u = self.put("pu.tar", raw_zip.make_tar([("file", "a.txt", b"same", 0o644)]))
        plain_s = self.put("ps.tar", raw_zip.make_tar([("file", "a.txt", b"same", 0o644)]))
        self.assertEqual(correspondence.compare(plain_u, plain_s, "tree")["verdict"], "PASS")


class CommandLineTests(WorkDir):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=120
        )

    def test_cli_exit_codes_and_reports(self):
        pe_u, meta = build_pe(overlay=b"OVERLAY-0123456789")
        pe_s = authenticode_like(pe_u, meta)
        mac_u, _ = build_macho()
        mac_s, _ = build_signed_macho()
        files = {
            "pe_u": self.put("u.exe", pe_u), "pe_s": self.put("s.exe", pe_s),
            "mac_u": self.put("u.macho", mac_u), "mac_s": self.put("s.macho", mac_s),
        }
        report = self.base / "report.json"

        done = self.run_cli("--unsigned", files["pe_u"], "--signed", files["pe_s"], "--kind", "pe", "--report", report)
        self.assertEqual(done.returncode, 0, done.stderr)
        document = json.loads(report.read_text())
        self.assertEqual(document["verdict"], "PASS")
        self.assertEqual(document["schema"], 1)

        done = self.run_cli("--unsigned", files["mac_u"], "--signed", files["mac_s"], "--kind", "macho", "--report", report)
        self.assertEqual(done.returncode, 0, done.stderr)

        tampered = bytearray(pe_s)
        tampered[meta["text_offset"]] ^= 0xFF
        files["pe_bad"] = self.put("bad.exe", bytes(tampered))
        done = self.run_cli("--unsigned", files["pe_u"], "--signed", files["pe_bad"], "--kind", "pe", "--report", report)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(json.loads(report.read_text())["verdict"], "FAIL")

        # Exit 2 replaces any stale PASS report with an explicit ERROR document.
        report.write_text(json.dumps({"schema": 1, "verdict": "PASS"}))
        done = self.run_cli("--unsigned", self.base / "missing", "--signed", files["pe_s"], "--kind", "pe", "--report", report)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(json.loads(report.read_text())["verdict"], "ERROR")

        done = self.run_cli("--unsigned", files["pe_u"], "--signed", files["pe_s"], "--kind", "elf", "--report", report)
        self.assertEqual(done.returncode, 2)
        done = self.run_cli("--unsigned", files["pe_u"])
        self.assertEqual(done.returncode, 2)

        unwritable = self.base / "no-such-dir" / "report.json"
        done = self.run_cli("--unsigned", files["pe_u"], "--signed", files["pe_s"], "--kind", "pe", "--report", unwritable)
        self.assertEqual(done.returncode, 2)

        tree_u = self.base / "tu"
        tree_s = self.base / "ts"
        for root in (tree_u, tree_s):
            root.mkdir()
            (root / "a.txt").write_text("a")
        done = self.run_cli("--unsigned", tree_u, "--signed", tree_s, "--kind", "tree", "--report", report)
        self.assertEqual(done.returncode, 0, done.stderr)
        (tree_s / "extra.txt").write_text("x")
        done = self.run_cli("--unsigned", tree_u, "--signed", tree_s, "--kind", "tree", "--report", report)
        self.assertEqual(done.returncode, 1)


if __name__ == "__main__":
    unittest.main()
