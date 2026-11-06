#!/usr/bin/env python3
"""Deterministic-input inventory tests for the OTP-25 standalone installers (Task 9).

Python 3.12+ standard library only; no network. Run from the repository root:

    python3 -m unittest discover -s native-bridge/installer/scripts -p 'test_*inputs.py'

``two_clean_input_trees_equal_despite_checkout_path`` is the determinism proof the
plan names: two byte-identical trees created under unrelated checkout paths, in
different creation order, with different mtimes/permission bits/umask-dependent
modes, must yield the *identical* inventory, and the rendered inventory must carry
no trace of either checkout path. The neighbouring tests prove the inventory is not
trivially constant: every property that must change the result does.
"""

import contextlib
import io
import json
import lzma
import os
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import warnings
import zipfile
import zlib
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import package_inputs  # noqa: E402  (sibling module, imported after sys.path setup)

SCRIPT = HERE / "package_inputs.py"

FILES = {
    "bin/veles-native-host": (b"\x7fELF-host-bytes" * 40, 0o755),
    "bin/veles-installer-engine": (b"engine" * 100, 0o755),
    "manifests/chrome.json.template": (b'{"allowed_origins":["chrome-extension://prod/"]}\n', 0o644),
    "docs/README.txt": (b"read me\n", 0o644),
    "docs/nested/deep/notes.txt": (b"", 0o644),
    "zeta.txt": (b"z", 0o644),
    "Alpha.txt": (b"a", 0o644),
    "éclair.txt": (b"unicode name", 0o644),
}


def materialize(root: Path, files=None, *, order=None, mtime=None, perms=None, empty_dirs=("empty/sub",)):
    files = dict(FILES if files is None else files)
    names = list(files) if order is None else order
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        data, mode = files[name]
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(perms(mode) if perms else mode)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
    for directory in empty_dirs:
        (root / directory).mkdir(parents=True, exist_ok=True)
    if mtime is not None:
        for directory in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
            os.utime(directory, (mtime, mtime))
        os.utime(root, (mtime, mtime))
    return root


class Scratch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def run_cli(self, *args, env=None, cwd=None):
        environment = {"PATH": os.environ.get("PATH", ""), **(env or {})}
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), *map(str, args)],
            capture_output=True,
            timeout=120,
            env=environment,
            cwd=cwd,
        )


class InventoryDeterminismTests(Scratch):
    def test_two_clean_input_trees_equal_despite_checkout_path(self):
        first_root = materialize(
            self.base / "checkout-one" / "work" / "repo",
            mtime=1_000_000_000,
        )
        second_root = materialize(
            self.base / "totally" / "different" / "deeper" / "clone-2",
            order=list(reversed(list(FILES))),
            mtime=1_900_000_000,
            perms=lambda mode: mode | 0o060 if mode == 0o644 else mode,  # group-writable: umask-like noise
        )
        first = package_inputs.build_inventory(first_root)
        second = package_inputs.build_inventory(second_root)
        self.assertEqual(first, second)
        self.assertEqual(package_inputs.canonical_json(first), package_inputs.canonical_json(second))
        self.assertEqual(first["inventory_sha256"], second["inventory_sha256"])
        self.assertEqual(package_inputs.compare_inventories(first, second), [])

        # Not trivially constant: it really lists every file with its hash and mode...
        by_path = {entry["path"]: entry for entry in first["entries"]}
        self.assertEqual(set(by_path), set(FILES))
        self.assertTrue(by_path["bin/veles-native-host"]["executable"])
        self.assertFalse(by_path["docs/README.txt"]["executable"])
        self.assertEqual(by_path["docs/nested/deep/notes.txt"]["size"], 0)
        self.assertEqual(first["empty_directories"], ["empty/sub/"])
        # ...sorted by the UTF-8 bytes of the path, not by locale or listing order.
        raw_paths = [entry["path"].encode("utf-8") for entry in first["entries"]]
        self.assertEqual(raw_paths, sorted(raw_paths))
        self.assertLess(
            [entry["path"] for entry in first["entries"]].index("Alpha.txt"),
            [entry["path"] for entry in first["entries"]].index("zeta.txt"),
        )
        # ...and nothing host specific appears anywhere in the rendered form.
        rendered = package_inputs.canonical_json(first).decode("utf-8")
        for needle in (str(first_root), str(second_root), "checkout-one", "clone-2", "different", str(self.base)):
            self.assertNotIn(needle, rendered)
        self.assertNotIn("1000000000", rendered)
        self.assertNotIn("1900000000", rendered)

        # The CLI is equally path/locale/timezone independent.
        outputs = []
        for cwd_name, root, env in (
            ("cwd-a", first_root, {"LC_ALL": "C", "TZ": "UTC"}),
            ("cwd-b", second_root, {"LC_ALL": "C.UTF-8", "TZ": "Asia/Bangkok"}),
        ):
            (self.base / cwd_name).mkdir()
            done = self.run_cli("inventory", root, env=env, cwd=self.base / cwd_name)
            self.assertEqual(done.returncode, 0, done.stderr)
            outputs.append(done.stdout)
        self.assertEqual(outputs[0], outputs[1])

    def test_inventory_changes_with_every_property_that_must_matter(self):
        reference = package_inputs.build_inventory(materialize(self.base / "ref"))
        mutations = {}

        changed = materialize(self.base / "content")
        (changed / "docs/README.txt").write_bytes(b"read me!\n")
        mutations["content"] = changed

        same_size = materialize(self.base / "same-size")
        (same_size / "zeta.txt").write_bytes(b"y")
        mutations["same-size-content"] = same_size

        mode = materialize(self.base / "mode")
        (mode / "docs/README.txt").chmod(0o755)
        mutations["executable-bit"] = mode

        added = materialize(self.base / "added")
        (added / "extra.bin").write_bytes(b"x")
        mutations["added-file"] = added

        removed = materialize(self.base / "removed")
        (removed / "zeta.txt").unlink()
        mutations["removed-file"] = removed

        renamed = materialize(self.base / "renamed")
        (renamed / "zeta.txt").rename(renamed / "zeta2.txt")
        mutations["renamed-file"] = renamed

        no_empty = materialize(self.base / "no-empty", empty_dirs=())
        mutations["empty-directory-dropped"] = no_empty

        for name, root in mutations.items():
            with self.subTest(name=name):
                other = package_inputs.build_inventory(root)
                self.assertNotEqual(other["inventory_sha256"], reference["inventory_sha256"])
                self.assertNotEqual(package_inputs.compare_inventories(reference, other), [])

        # Permission bits other than "any execute bit" do not matter; mtimes do not matter.
        quiet = materialize(self.base / "quiet", perms=lambda m: 0o600 if m == 0o644 else 0o700, mtime=42)
        self.assertEqual(package_inputs.build_inventory(quiet), reference)

    def test_inventory_does_not_read_the_clock_or_environment(self):
        root = materialize(self.base / "tree")
        first = package_inputs.build_inventory(root)
        os.utime(root / "zeta.txt", (7, 7))
        with mock.patch("time.time", side_effect=AssertionError("clock read")):
            second = package_inputs.build_inventory(root)
        self.assertEqual(first, second)

    def test_compare_inventories_reports_exact_differences(self):
        reference = package_inputs.build_inventory(materialize(self.base / "ref"))
        other_root = materialize(self.base / "other")
        (other_root / "zeta.txt").write_bytes(b"changed")
        (other_root / "docs/README.txt").chmod(0o755)
        (other_root / "extra.bin").write_bytes(b"x")
        (other_root / "Alpha.txt").unlink()
        (other_root / "empty/sub").rmdir()
        other = package_inputs.build_inventory(other_root)
        differences = package_inputs.compare_inventories(reference, other)
        summary = sorted((item["code"], item["path"]) for item in differences)
        self.assertEqual(
            summary,
            [
                ("content", "zeta.txt"),
                ("executable", "docs/README.txt"),
                ("only_in_first", "Alpha.txt"),
                ("only_in_first", "empty/sub/"),  # the removed leaf directory...
                ("only_in_second", "empty/"),  # ...leaves its parent as the new empty directory
                ("only_in_second", "extra.bin"),
            ],
        )


class InventoryRejectionTests(Scratch):
    def assert_rejected(self, root, code=None):
        with self.assertRaises(package_inputs.InventoryError) as caught:
            package_inputs.build_inventory(root)
        if code is not None:
            self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_symlinks_special_files_and_aliases_are_rejected_not_skipped(self):
        file_link = materialize(self.base / "file-link")
        os.symlink("zeta.txt", file_link / "link")
        self.assert_rejected(file_link, "symlink")

        dir_link = materialize(self.base / "dir-link")
        os.symlink("docs", dir_link / "dlink")
        self.assert_rejected(dir_link, "symlink")

        dangling = materialize(self.base / "dangling")
        os.symlink("/nonexistent/nowhere", dangling / "dangling")
        self.assert_rejected(dangling, "symlink")

        fifo = materialize(self.base / "fifo")
        os.mkfifo(fifo / "pipe")
        self.assert_rejected(fifo, "special-file")

        aliased = materialize(self.base / "alias")
        (aliased / "ZETA.TXT").write_bytes(b"z")
        self.assert_rejected(aliased, "case-folded-alias")

        dir_alias = materialize(self.base / "dir-alias")
        (dir_alias / "Docs").mkdir()
        (dir_alias / "Docs" / "x").write_bytes(b"x")
        self.assert_rejected(dir_alias, "case-folded-alias")

    def test_unsafe_names_are_rejected(self):
        for index, name in enumerate(("trailing.", "trailing ", "colon:name", "back\\slash", "ctl\x01char", "dot.")):
            with self.subTest(name=name):
                root = materialize(self.base / f"unsafe-{index}", files={"ok.txt": (b"x", 0o644)}, empty_dirs=())
                (root / name).write_bytes(b"x")
                self.assert_rejected(root, "unsafe-path")
        bad_utf8 = materialize(self.base / "bad-utf8", files={"ok.txt": (b"x", 0o644)}, empty_dirs=())
        with open(os.path.join(os.fsencode(bad_utf8), b"bad-\xff-name"), "wb") as handle:
            handle.write(b"x")
        self.assert_rejected(bad_utf8, "unsafe-path")
        long_name = materialize(self.base / "long", files={"ok.txt": (b"x", 0o644)}, empty_dirs=())
        (long_name / ("n" * 200)).mkdir()
        deep = long_name / ("n" * 200)
        for _ in range(5):
            deep = deep / ("m" * 200)
            deep.mkdir()
        (deep / "f").write_bytes(b"x")
        self.assert_rejected(long_name, "unsafe-path")

    def test_bounds_are_enforced(self):
        root = materialize(self.base / "many", files={f"f{index}.txt": (b"x", 0o644) for index in range(6)}, empty_dirs=())
        with mock.patch.object(package_inputs, "MAX_ENTRIES", 5):
            self.assert_rejected(root, "bound-exceeded")
        with mock.patch.object(package_inputs, "MAX_TOTAL_BYTES", 5):
            self.assert_rejected(root, "bound-exceeded")
        nested = materialize(self.base / "nested", files={"a.txt": (b"x", 0o644)}, empty_dirs=())
        deep = nested
        for index in range(4):
            deep = deep / f"d{index}"
        deep.mkdir(parents=True)
        (deep / "f").write_bytes(b"x")
        with mock.patch.object(package_inputs, "MAX_DEPTH", 3):
            self.assert_rejected(nested, "bound-exceeded")
        # Exactly at the limit is fine.
        with mock.patch.object(package_inputs, "MAX_ENTRIES", 6):
            package_inputs.build_inventory(root)

    def test_unusable_roots_are_input_errors_not_inventory_rejections(self):
        with self.assertRaises(package_inputs.InputError):
            package_inputs.build_inventory(self.base / "missing")
        file_root = self.base / "plain-file"
        file_root.write_bytes(b"x")
        with self.assertRaises(package_inputs.InputError):
            package_inputs.build_inventory(file_root)
        real = materialize(self.base / "real")
        os.symlink(real, self.base / "root-link")
        with self.assertRaises(package_inputs.InputError):
            package_inputs.build_inventory(self.base / "root-link")
        unreadable = materialize(self.base / "unreadable", files={"a/b.txt": (b"x", 0o644)}, empty_dirs=())
        (unreadable / "a").chmod(0)
        self.addCleanup((unreadable / "a").chmod, 0o755)
        if os.access(unreadable / "a", os.R_OK):
            self.skipTest("running with privileges that ignore directory modes")
        with self.assertRaises(package_inputs.InputError):
            package_inputs.build_inventory(unreadable)


def make_zip(members, *, unix_modes=None, stamp=(2020, 1, 1, 0, 0, 0)):
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # zipfile warns when a test deliberately writes a duplicate name
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in members:
                info = zipfile.ZipInfo(name, date_time=stamp)
                mode = (unix_modes or {}).get(name, 0o100644)
                info.external_attr = mode << 16
                archive.writestr(info, data)
    return buffer.getvalue()


def make_tar(members, *, mtime=0):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:") as archive:
        for kind, name, data, mode in members:
            info = tarfile.TarInfo(name)
            info.mtime = mtime
            info.mode = mode
            if kind == "file":
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = data.decode()
                archive.addfile(info)
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = data.decode()
                archive.addfile(info)
    return buffer.getvalue()


class ArchiveInventoryTests(Scratch):
    def put(self, name, data):
        path = self.base / name
        path.write_bytes(data)
        return path

    def test_archive_inventory_matches_directory_inventory_regardless_of_timestamps(self):
        reference = package_inputs.build_inventory(materialize(self.base / "tree", empty_dirs=()))
        members = [(name, data) for name, (data, _mode) in FILES.items()]
        modes = {name: 0o100755 if mode & 0o111 else 0o100644 for name, (_d, mode) in FILES.items()}
        zip_a = self.put("a.zip", make_zip(members, unix_modes=modes, stamp=(2001, 2, 3, 4, 5, 6)))
        zip_b = self.put("b.zip", make_zip(list(reversed(members)), unix_modes=modes, stamp=(2030, 12, 30, 23, 59, 58)))
        tar_members = [("file", name, data, mode) for name, (data, mode) in FILES.items()]
        tar_a = self.put("a.tar", make_tar(tar_members, mtime=0))
        tar_b = self.put("b.tar", make_tar(list(reversed(tar_members)), mtime=1_700_000_000))
        for path in (zip_a, zip_b, tar_a, tar_b):
            with self.subTest(path=path.name):
                self.assertEqual(package_inputs.build_archive_inventory(path), reference)

    def test_zip_members_without_file_type_bits_are_regular_files(self):
        # ZipFile.writestr(str, ...) stores only permission bits (0o600) with no S_IFREG, the
        # same shape many Windows/Java zippers produce; it must not be mistaken for a device.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("plain.txt", b"hello")
            info = zipfile.ZipInfo("tool.sh")
            info.external_attr = 0o755 << 16
            archive.writestr(info, b"#!/bin/sh\n")
        inventory = package_inputs.build_archive_inventory(self.put("plain.zip", buffer.getvalue()))
        self.assertEqual(
            {entry["path"]: entry["executable"] for entry in inventory["entries"]},
            {"plain.txt": False, "tool.sh": True},
        )

    def test_duplicate_traversal_link_and_special_members_are_rejected(self):
        cases = {
            "dup.zip": (make_zip([("a/x", b"1"), ("a/x", b"2")]), "duplicate-archive-member"),
            "casedup.zip": (make_zip([("a/x", b"1"), ("a/X", b"2")]), "case-folded-alias"),
            "dir-file.zip": (make_zip([("a", b"1"), ("a/", b"")], unix_modes={"a/": 0o040755}), "duplicate-archive-member"),
            "traversal.zip": (make_zip([("../evil", b"x")]), "unsafe-path"),
            "absolute.zip": (make_zip([("/etc/passwd", b"x")]), "unsafe-path"),
            "dotslash.zip": (make_zip([("./x", b"x")]), "unsafe-path"),
            "backslash.zip": (make_zip([("a\\b", b"x")]), "unsafe-path"),
            "symlink.zip": (make_zip([("l", b"target")], unix_modes={"l": 0o120777}), "symlink"),
            "device.zip": (make_zip([("d", b"")], unix_modes={"d": 0o020644}), "special-file"),
            "dup.tar": (make_tar([("file", "a", b"1", 0o644), ("file", "a", b"2", 0o644)]), "duplicate-archive-member"),
            "traversal.tar": (make_tar([("file", "../evil", b"x", 0o644)]), "unsafe-path"),
            "symlink.tar": (make_tar([("symlink", "l", b"target", 0o777)]), "symlink"),
            "hardlink.tar": (make_tar([("file", "a", b"1", 0o644), ("hardlink", "h", b"a", 0o644)]), "symlink"),
        }
        for name, (blob, code) in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(package_inputs.InventoryError) as caught:
                    package_inputs.build_archive_inventory(self.put(name, blob))
                self.assertEqual(caught.exception.code, code)

    def test_malformed_encrypted_and_oversized_archives_are_rejected_before_unbounded_reads(self):
        with self.assertRaises(package_inputs.InventoryError):
            package_inputs.build_archive_inventory(self.put("garbage.zip", b"PK\x03\x04" + b"\x00" * 40))
        with self.assertRaises(package_inputs.InventoryError):
            package_inputs.build_archive_inventory(self.put("garbage.tar", b"not a tar" * 100))

        blob = bytearray(make_zip([("a", b"payload")]))
        local = blob.index(b"PK\x03\x04")
        central = blob.index(b"PK\x01\x02")
        blob[local + 6] |= 1  # encrypted flag, local header
        blob[central + 8] |= 1  # encrypted flag, central directory
        with self.assertRaises(package_inputs.InventoryError):
            package_inputs.build_archive_inventory(self.put("encrypted.zip", bytes(blob)))

        # A declared size above the cap is refused without decompressing or hashing anything.
        bomb = self.put("bomb.zip", make_zip([("big", b"\x00" * 100_000)]))
        with mock.patch.object(package_inputs, "MAX_TOTAL_BYTES", 10_000), mock.patch.object(
            package_inputs, "MAX_ARCHIVE_MEMBER_BYTES", 10_000
        ):
            with mock.patch.object(package_inputs, "_hash_zip_member", side_effect=AssertionError("decompressed a member")):
                with self.assertRaises(package_inputs.InventoryError) as caught:
                    package_inputs.build_archive_inventory(bomb)
        self.assertEqual(caught.exception.code, "bound-exceeded")

        # Many small members hit the entry bound.
        many = self.put("many.tar", make_tar([("file", f"f{index}", b"x", 0o644) for index in range(8)]))
        with mock.patch.object(package_inputs, "MAX_ENTRIES", 5):
            with self.assertRaises(package_inputs.InventoryError) as caught:
                package_inputs.build_archive_inventory(many)
        self.assertEqual(caught.exception.code, "bound-exceeded")

    def test_corrupt_compressed_member_is_an_inventory_rejection_not_a_crash(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo("data.bin", date_time=(2020, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            # ``writestr(ZipInfo, ...)`` keeps the ZipInfo's own (stored) type; ask for deflate
            # explicitly or this test silently exercises a stored member (review F14).
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, bytes(range(256)) * 64)
        with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as check:
            self.assertEqual(check.getinfo("data.bin").compress_type, zipfile.ZIP_DEFLATED)
            self.assertLess(check.getinfo("data.bin").compress_size, check.getinfo("data.bin").file_size)
        blob = bytearray(buffer.getvalue())
        data_start = 30 + len("data.bin")  # local header + name; the deflate stream follows
        blob[data_start : data_start + 16] = b"\xff" * 16  # invalid deflate block
        with self.assertRaises(package_inputs.InventoryError) as caught:
            package_inputs.build_archive_inventory(self.put("corrupt.zip", bytes(blob)))
        self.assertEqual(caught.exception.code, "malformed-archive")

    def test_decompressor_exceptions_are_mapped_to_malformed_archive(self):
        # The archive parser is ours, so a decompressor failure is simulated at the real
        # codec boundary (zlib) rather than by crafting bytes zlib happens to classify.
        path = self.put("ok.zip", make_codec_zip([("a", b"payload" * 20)], zipfile.ZIP_DEFLATED))
        with mock.patch.object(zlib, "decompressobj", side_effect=zlib.error("invalid block type")):
            with self.assertRaises(package_inputs.InventoryError) as caught:
                package_inputs.build_archive_inventory(path)
        self.assertEqual(caught.exception.code, "malformed-archive")

    def test_unauthenticated_data_around_archives_is_rejected(self):
        # Bytes in front of the first zip member (self-extractor style prefix).
        prefixed = self.put("prefixed.zip", b"MZ-PREFIX-PAYLOAD" + make_zip([("a", b"x")]))
        with self.assertRaises(package_inputs.InventoryError) as caught:
            package_inputs.build_archive_inventory(prefixed)
        self.assertEqual(caught.exception.code, "unauthenticated-data")
        # Non-zero bytes after the tar end-of-archive marker.
        base = make_tar([("file", "a", b"x", 0o644)])
        smuggled = self.put("smuggled.tar", base + b"SMUGGLED-SECOND-ARCHIVE-PAYLOAD")
        with self.assertRaises(package_inputs.InventoryError) as caught:
            package_inputs.build_archive_inventory(smuggled)
        self.assertEqual(caught.exception.code, "unauthenticated-data")
        # Zero padding (blocking factor) is normal and accepted.
        padded = self.put("padded.tar", base + b"\x00" * 4096)
        self.assertEqual(package_inputs.build_archive_inventory(padded)["entries"][0]["path"], "a")

    def test_unsupported_and_missing_archives_are_input_errors(self):
        with self.assertRaises(package_inputs.InputError):
            package_inputs.build_archive_inventory(self.put("x.rar", b"x"))
        with self.assertRaises(package_inputs.InputError):
            package_inputs.build_archive_inventory(self.base / "missing.zip")
        (self.base / "dir.zip").mkdir()
        with self.assertRaises(package_inputs.InputError):
            package_inputs.build_archive_inventory(self.base / "dir.zip")


# --------------------------------------------------------------------------------------
# Raw ZIP helpers: the review's reproductions edit declared sizes/offsets by hand, so the
# tests build real archives with ``zipfile`` and then patch the headers.


def _eocd(blob):
    eocd = blob.rindex(b"PK\x05\x06")
    cd_size, cd_offset = struct.unpack_from("<II", blob, eocd + 12)
    return eocd, cd_offset, cd_size


def _central_offsets(blob):
    _eocd_offset, cd_offset, cd_size = _eocd(blob)
    offsets, cursor = [], cd_offset
    while cursor < cd_offset + cd_size:
        assert blob[cursor : cursor + 4] == b"PK\x01\x02"
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", blob, cursor + 28)
        offsets.append(cursor)
        cursor += 46 + name_len + extra_len + comment_len
    return offsets


def patch_member(blob, index, *, crc=None, csize=None, usize=None):
    """Rewrite a member's CRC/sizes in BOTH its local and central records."""
    blob = bytearray(blob)
    central = _central_offsets(blob)[index]
    local = struct.unpack_from("<I", blob, central + 42)[0]
    for record, crc_at in ((local, 14), (central, 16)):
        if crc is not None:
            struct.pack_into("<I", blob, record + crc_at, crc)
        if csize is not None:
            struct.pack_into("<I", blob, record + crc_at + 4, csize)
        if usize is not None:
            struct.pack_into("<I", blob, record + crc_at + 8, usize)
    return bytes(blob)


def make_codec_zip(members, method, *, unix_mode=0o100644):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.external_attr = unix_mode << 16
            info.compress_type = method
            archive.writestr(info, data)
    return buffer.getvalue()


def insert_gap(blob, at, junk):
    """Insert ``junk`` at ``at`` and repair every offset so only the *gap* is unusual."""
    blob = bytearray(blob)
    for central in _central_offsets(blob):
        local = struct.unpack_from("<I", blob, central + 42)[0]
        if local >= at:
            struct.pack_into("<I", blob, central + 42, local + len(junk))
    eocd, cd_offset, _cd_size = _eocd(blob)
    if cd_offset >= at:
        struct.pack_into("<I", blob, eocd + 16, cd_offset + len(junk))
    blob[at:at] = junk
    return bytes(blob)


class ReadRecorder:
    """Records the size of every read() the code under test makes on a file it opens."""

    def __init__(self):
        self.reads = []

    def wrap(self, real_open):
        recorder = self

        class Proxy:
            def __init__(self, handle):
                self._handle = handle

            def read(self, size=-1, *args):
                recorder.reads.append(size)
                return self._handle.read(size, *args)

            def readinto(self, buffer):
                recorder.reads.append(len(buffer))
                return self._handle.readinto(buffer)

            def __getattr__(self, name):
                return getattr(self._handle, name)

            def __enter__(self):
                self._handle.__enter__()
                return self

            def __exit__(self, *exc):
                return self._handle.__exit__(*exc)

            def __iter__(self):
                return iter(self._handle)

        def opener(*args, **kwargs):
            handle = real_open(*args, **kwargs)
            mode = args[1] if len(args) > 1 else kwargs.get("mode", "r")
            return Proxy(handle) if "b" in mode else handle

        return opener

    def patches(self):
        opener = self.wrap(io.open)
        return [
            mock.patch("builtins.open", opener),
            mock.patch.object(io, "open", opener),
            mock.patch.object(tarfile, "bltn_open", opener),
        ]

    @property
    def largest(self):
        return max([size for size in self.reads if isinstance(size, int) and size >= 0] or [0])


class ArchiveAccountingTests(Scratch):
    """Review F7-F9, F13, F14: every byte an archive carries is accounted for or refused."""

    def put(self, name, data):
        path = self.base / name
        path.write_bytes(data)
        return path

    def assert_refused(self, name, blob, code):
        with self.assertRaises(package_inputs.InventoryError) as caught:
            package_inputs.build_archive_inventory(self.put(name, blob))
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        return caught.exception

    # ---- F7: a size declaration is a claim to verify, not a bound that truncates ----------

    def lzma_bomb(self):
        # 1 MiB of 'x' whose headers claim ONE byte (the review's 333-byte reproduction).
        blob = make_codec_zip([("a", b"x" * (1 << 20))], zipfile.ZIP_LZMA)
        return patch_member(blob, 0, crc=zlib.crc32(b"x"), usize=1)

    def deflate_bomb(self):
        blob = make_codec_zip([("a", b"x" * (1 << 20))], zipfile.ZIP_DEFLATED)
        return patch_member(blob, 0, crc=zlib.crc32(b"x"), usize=1)

    def test_f7_lzma_member_declaring_one_byte_but_carrying_a_megabyte_is_refused(self):
        bomb = self.lzma_bomb()
        self.assertLess(len(bomb), 1000)  # the attack archive is tiny
        # Sanity: this is exactly the archive stock zipfile happily reports as one byte.
        with zipfile.ZipFile(io.BytesIO(bomb)) as archive:
            self.assertEqual(archive.read("a"), b"x")
        self.assert_refused("bomb-lzma.zip", bomb, "unsupported-compression")

    def test_f7_deflate_member_expanding_past_its_declaration_is_refused_with_bounded_output(self):
        bomb = self.deflate_bomb()
        produced = []
        real = zlib.decompressobj

        def counting(*args, **kwargs):
            inner = real(*args, **kwargs)

            class Counting:
                def decompress(self, data, max_length=0):
                    out = inner.decompress(data, max_length)
                    produced.append(len(out))
                    return out

                def __getattr__(self, name):
                    return getattr(inner, name)

            return Counting()

        with mock.patch.object(zlib, "decompressobj", counting):
            self.assert_refused("bomb-deflate.zip", bomb, "malformed-archive")
        # The decompressor was never allowed to materialise the hidden megabyte.
        self.assertGreater(len(produced), 0)  # the real decompressor really ran
        self.assertLessEqual(sum(produced), 2, produced)

    def test_f7_archives_with_different_real_content_never_compare_equal(self):
        genuine = self.put("genuine.zip", make_codec_zip([("a", b"x")], zipfile.ZIP_DEFLATED))
        for name, bomb in (("lzma", self.lzma_bomb()), ("deflate", self.deflate_bomb())):
            with self.subTest(codec=name):
                hidden = self.put(f"hidden-{name}.zip", bomb)
                report = package_inputs._compare_report(genuine, hidden)
                self.assertEqual(report["verdict"], "FAIL")
                self.assertEqual([item["side"] for item in report["differences"]], ["second"])
                self.assertEqual(report["input_hashes"], {})
        other = self.put("other.zip", make_codec_zip([("a", b"y")], zipfile.ZIP_DEFLATED))
        self.assertEqual(package_inputs._compare_report(genuine, other)["verdict"], "FAIL")

    def test_f7_stored_member_whose_compressed_size_exceeds_its_declared_size_is_refused(self):
        blob = make_codec_zip([("a", b"surplus")], zipfile.ZIP_STORED)
        # Audit correction: the inherited version forged the CRC of the *declared* prefix, so the CRC over the
        # seven stored bytes failed first and the size rule was never what refused it (mutation: deleting the
        # csize == usize check survived). Forge the CRC of the bytes actually stored so only the size disagrees.
        crc = zlib.crc32(b"surplus")
        self.assert_refused("stored-surplus.zip", patch_member(blob, 0, usize=0, crc=crc), "malformed-archive")
        self.assert_refused("stored-short.zip", patch_member(blob, 0, usize=3, crc=crc), "malformed-archive")
        with self.assertRaises(package_inputs.InventoryError) as caught:
            package_inputs.build_archive_inventory(self.base / "stored-surplus.zip")
        self.assertIn("compressed size", str(caught.exception))

    def test_f7_deflate_member_shorter_than_declared_is_refused_even_when_the_crc_matches(self):
        # Declared 2 bytes, stream yields 1, CRC forged to the 1 real byte: the recorded size would be a lie.
        blob = make_codec_zip([("a", b"x")], zipfile.ZIP_DEFLATED)
        self.assert_refused("deflate-short.zip", patch_member(blob, 0, usize=2, crc=zlib.crc32(b"x")), "malformed-archive")
        with self.assertRaises(package_inputs.InventoryError) as caught:
            package_inputs.build_archive_inventory(self.base / "deflate-short.zip")
        self.assertIn("expands to 1 bytes", str(caught.exception))

    def test_f7_crc_and_codec_inconsistencies_are_refused(self):
        stored = make_codec_zip([("a", b"payload")], zipfile.ZIP_STORED)
        self.assert_refused("bad-crc.zip", patch_member(stored, 0, crc=1), "malformed-archive")
        self.assert_refused("codec-bzip2.zip", make_codec_zip([("a", b"payload")], zipfile.ZIP_BZIP2), "unsupported-compression")
        extent = make_codec_zip([("a", b"y" * 4000)], zipfile.ZIP_DEFLATED)
        central = _central_offsets(extent)[0]
        csize = struct.unpack_from("<I", extent, central + 20)[0]
        local = struct.unpack_from("<I", extent, central + 42)[0]
        name_len, extra_len = struct.unpack_from("<HH", extent, local + 26)
        data_end = local + 30 + name_len + extra_len + csize
        # Junk appended INSIDE the member's declared compressed extent, after the deflate stream ends.
        padded = patch_member(insert_gap(extent, data_end, b"JUNKJUNK"), 0, csize=csize + 8)
        self.assert_refused("deflate-trailing.zip", padded, "malformed-archive")
        # A truncated stream is refused too (remove the last two bytes of the extent and repair
        # the declared sizes and the central-directory offset so only the truncation is wrong).
        cut = bytearray(extent)
        del cut[data_end - 2 : data_end]
        end_record = bytes(cut).rindex(b"PK\x05\x06")
        struct.pack_into("<I", cut, end_record + 16, struct.unpack_from("<I", cut, end_record + 16)[0] - 2)
        self.assert_refused("deflate-cut.zip", patch_member(bytes(cut), 0, csize=csize - 2), "malformed-archive")

    def test_valid_deflate_and_stored_zip_archives_still_inventory_like_a_directory(self):
        reference = package_inputs.build_inventory(materialize(self.base / "tree", empty_dirs=()))
        members = [(name, data) for name, (data, _mode) in FILES.items()]
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with self.subTest(method=method):
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as archive:
                    for name, data in members:
                        info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                        info.external_attr = (0o100755 if FILES[name][1] & 0o111 else 0o100644) << 16
                        info.compress_type = method
                        archive.writestr(info, data)
                inventory = package_inputs.build_archive_inventory(self.put(f"ok-{method}.zip", buffer.getvalue()))
                self.assertEqual(inventory, reference)

    # ---- F8: nothing in the archive may be left unexplained -------------------------------

    def test_f8_empty_zip_with_a_prefix_or_trailer_is_refused(self):
        empty = make_zip([])
        self.assertEqual(len(empty), 22)
        self.assertEqual(package_inputs.build_archive_inventory(self.put("empty.zip", empty))["entries"], [])
        self.assert_refused("empty-prefix.zip", b"PREFIX" + empty, "unauthenticated-data")
        self.assert_refused("empty-trailer.zip", empty + b"TRAILER", "unauthenticated-data")

    def test_f8_data_after_the_end_record_and_archive_comments_are_refused(self):
        blob = make_zip([("a", b"x")])
        self.assert_refused("trailer.zip", blob + b"TRAILER", "unauthenticated-data")
        self.assert_refused("trailer-zero.zip", blob + b"\x00\x00\x00\x00", "unauthenticated-data")
        commented = bytearray(blob)
        eocd = bytes(commented).rindex(b"PK\x05\x06")
        struct.pack_into("<H", commented, eocd + 20, 5)
        commented += b"hello"
        self.assert_refused("comment.zip", bytes(commented), "unauthenticated-data")

    def test_f8_gaps_between_members_and_before_the_central_directory_are_refused(self):
        blob = make_zip([("a", b"first"), ("b", b"second")])
        second_local = struct.unpack_from("<I", blob, _central_offsets(blob)[1] + 42)[0]
        self.assert_refused("gap-between.zip", insert_gap(blob, second_local, b"HIDDEN-GAP"), "unauthenticated-data")
        cd_offset = _eocd(blob)[1]
        self.assert_refused("gap-before-cd.zip", insert_gap(blob, cd_offset, b"HIDDEN-GAP"), "unauthenticated-data")

    def test_f8_directory_members_carry_no_payload_and_consistent_type_metadata(self):
        carrying = make_zip([("d/", b"EXTRA-CONTENT")], unix_modes={"d/": 0o040755})
        self.assert_refused("dir-payload.zip", carrying, "malformed-archive")
        # A directory name whose Unix type says "regular file", and a regular name typed as a directory.
        self.assert_refused("dir-as-file.zip", make_zip([("d/", b"")], unix_modes={"d/": 0o100644}), "malformed-archive")
        self.assert_refused("file-as-dir.zip", make_zip([("f", b"x")], unix_modes={"f": 0o040755}), "malformed-archive")
        honest = make_zip([("d/", b""), ("e/f", b"x")], unix_modes={"d/": 0o040755})
        inventory = package_inputs.build_archive_inventory(self.put("dirs.zip", honest))
        self.assertEqual(inventory["empty_directories"], ["d/"])

    def test_f8_local_and_central_records_must_agree_and_carry_no_unexplained_fields(self):
        blob = make_zip([("a", b"payload")])
        renamed = bytearray(blob)
        renamed[30] = ord("b")  # local name differs from the central name
        self.assert_refused("name-mismatch.zip", bytes(renamed), "malformed-archive")
        # A member comment is data that no inventory field would ever show.
        central = _central_offsets(blob)[0]
        commented = bytearray(blob)
        struct.pack_into("<H", commented, central + 32, 4)
        eocd, cd_offset, cd_size = _eocd(bytes(commented))
        commented[eocd:eocd] = b"NOTE"
        struct.pack_into("<I", commented, eocd + 4 + 12, cd_size + 4)
        self.assert_refused("member-comment.zip", bytes(commented), "unauthenticated-data")
        # An unrecognised extra field in the local header (after the 1-byte name); everything
        # behind it shifts, so the end record's central-directory offset moves too.
        extra = struct.pack("<HH", 0xCAFE, 4) + b"DATA"
        fixed = bytearray(blob)
        struct.pack_into("<H", fixed, 28, len(extra))
        fixed[31:31] = extra
        eocd2, cd2, _ = _eocd(bytes(fixed))
        struct.pack_into("<I", fixed, eocd2 + 16, cd2 + len(extra))
        self.assert_refused("unknown-extra.zip", bytes(fixed), "malformed-archive")

    def test_f8_local_record_must_repeat_the_central_record_exactly(self):
        # Only the local CRC differs; the central record (which the CRC check uses) is intact.
        blob = bytearray(make_zip([("a", b"payload")]))
        struct.pack_into("<I", blob, 14, struct.unpack_from("<I", blob, 14)[0] ^ 1)
        self.assert_refused("local-crc.zip", bytes(blob), "malformed-archive")
        with self.assertRaises(package_inputs.InventoryError) as caught:
            package_inputs.build_archive_inventory(self.base / "local-crc.zip")
        self.assertIn("disagrees with the central directory", str(caught.exception))

    def test_f8_end_record_comment_length_must_be_zero_even_without_comment_bytes(self):
        blob = bytearray(make_zip([("a", b"x")]))
        struct.pack_into("<H", blob, _eocd(bytes(blob))[0] + 20, 5)  # claims a comment that is not there
        self.assert_refused("lying-comment.zip", bytes(blob), "unauthenticated-data")

    # ---- F9: metadata is bounded before any library allocates for it ---------------------

    def test_f9_central_directory_larger_than_its_member_count_can_explain_is_not_read(self):
        # One member, but the end record claims a ~200 KB central directory (junk precedes the end record).
        blob = bytearray(make_zip([("a", b"x")]))
        eocd, _cd_offset, cd_size = _eocd(bytes(blob))
        junk = b"\x00" * 200_000
        blob[eocd:eocd] = junk
        struct.pack_into("<I", blob, eocd + len(junk) + 12, cd_size + len(junk))
        path = self.put("padded-cd.zip", bytes(blob))
        recorder = ReadRecorder()
        with contextlib.ExitStack() as stack:
            for patch in recorder.patches():
                stack.enter_context(patch)
            with self.assertRaises(package_inputs.InventoryError) as caught:
                package_inputs.build_archive_inventory(path)
        self.assertEqual(caught.exception.code, "bound-exceeded")
        self.assertLessEqual(recorder.largest, 64, recorder.reads)

    def test_f9_zip_central_directory_is_not_read_when_the_member_count_is_already_refused(self):
        blob = make_zip([(f"m{index}", b"x") for index in range(10)])
        path = self.put("ten.zip", blob)
        recorder = ReadRecorder()
        with contextlib.ExitStack() as stack:
            for patch in recorder.patches():
                stack.enter_context(patch)
            stack.enter_context(mock.patch.object(package_inputs, "MAX_ENTRIES", 1))
            with self.assertRaises(package_inputs.InventoryError) as caught:
                package_inputs.build_archive_inventory(path)
        self.assertEqual(caught.exception.code, "bound-exceeded")
        self.assertLessEqual(recorder.largest, 64, recorder.reads)  # the 22-byte end record, nothing more

    def test_f9_forged_central_directory_size_is_refused_without_reading_it(self):
        blob = bytearray(make_zip([("a", b"x")]))
        eocd, _cd_offset, _cd_size = _eocd(bytes(blob))
        struct.pack_into("<I", blob, eocd + 12, 0x7FFF0000)
        path = self.put("forged.zip", bytes(blob))
        recorder = ReadRecorder()
        with contextlib.ExitStack() as stack:
            for patch in recorder.patches():
                stack.enter_context(patch)
            with self.assertRaises(package_inputs.InventoryError):
                package_inputs.build_archive_inventory(path)
        self.assertLessEqual(recorder.largest, 64, recorder.reads)

    def test_f9_physical_archive_size_is_bounded(self):
        # Physical bound = payload cap + a per-node header allowance, decided before any read.
        path = self.put("big.zip", make_zip([("a", b"x" * 40_000)]))
        recorder = ReadRecorder()
        with contextlib.ExitStack() as stack:
            for patch in recorder.patches():
                stack.enter_context(patch)
            stack.enter_context(mock.patch.object(package_inputs, "MAX_TOTAL_BYTES", 64))
            stack.enter_context(mock.patch.object(package_inputs, "MAX_ARCHIVE_MEMBER_BYTES", 64))
            stack.enter_context(mock.patch.object(package_inputs, "MAX_ENTRIES", 1))
            with self.assertRaises(package_inputs.InventoryError) as caught:
                package_inputs.build_archive_inventory(path)
        self.assertEqual(caught.exception.code, "bound-exceeded")
        self.assertEqual(recorder.reads, [])
        tar_path = self.put("big.tar", make_tar([("file", "a", b"x" * 40_000, 0o644)]))
        recorder = ReadRecorder()
        with contextlib.ExitStack() as stack:
            for patch in recorder.patches():
                stack.enter_context(patch)
            stack.enter_context(mock.patch.object(package_inputs, "MAX_TOTAL_BYTES", 64))
            stack.enter_context(mock.patch.object(package_inputs, "MAX_ENTRIES", 1))
            with self.assertRaises(package_inputs.InventoryError) as caught:
                package_inputs.build_archive_inventory(tar_path)
        self.assertEqual(caught.exception.code, "bound-exceeded")
        self.assertEqual(recorder.reads, [])

    def pax_tar(self, headers, name="a", data=b"x"):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:", format=tarfile.PAX_FORMAT) as archive:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.pax_headers = dict(headers)
            archive.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    def test_f9_oversized_tar_extension_records_are_refused_before_they_are_read(self):
        gnu = io.BytesIO()
        with tarfile.open(fileobj=gnu, mode="w:", format=tarfile.GNU_FORMAT) as archive:
            info = tarfile.TarInfo("n" * 6000)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        cases = {"pax-8192.tar": self.pax_tar({"uname": "u" * 8192}), "gnu-longname.tar": gnu.getvalue()}
        for name, blob in cases.items():
            with self.subTest(name=name):
                path = self.put(name, blob)
                recorder = ReadRecorder()
                with contextlib.ExitStack() as stack:
                    for patch in recorder.patches():
                        stack.enter_context(patch)
                    with self.assertRaises(package_inputs.InventoryError) as caught:
                        package_inputs.build_archive_inventory(path)
                self.assertEqual(caught.exception.code, "bound-exceeded", str(caught.exception))
                self.assertLessEqual(recorder.largest, 1024, recorder.reads)

    def test_f9_cumulative_tar_metadata_is_bounded_and_unrecognised_pax_keys_are_refused(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:", format=tarfile.PAX_FORMAT) as archive:
            for index in range(6):
                info = tarfile.TarInfo(f"f{index}")
                info.size = 1
                info.pax_headers = {"uname": "u" * 2000}
                archive.addfile(info, io.BytesIO(b"x"))
        path = self.put("many-pax.tar", buffer.getvalue())
        with mock.patch.object(package_inputs, "MAX_TAR_METADATA_TOTAL", 4096, create=True):
            with self.assertRaises(package_inputs.InventoryError) as caught:
                package_inputs.build_archive_inventory(path)
        self.assertEqual(caught.exception.code, "bound-exceeded")
        for key in ("comment", "size", "GNU.sparse.name", "hdrcharset"):
            with self.subTest(key=key):
                self.assert_refused(f"pax-{key}.tar", self.pax_tar({key: "1"}), "malformed-archive")
        honest = self.pax_tar({"path": "d/" + "n" * 150, "mtime": "1700000000.5", "uname": "u", "gname": "g"})
        self.assertEqual(
            [entry["path"] for entry in package_inputs.build_archive_inventory(self.put("honest.tar", honest))["entries"]],
            ["d/" + "n" * 150],
        )

    def test_f9_global_sparse_and_exotic_tar_headers_are_refused(self):
        # A global PAX header (applies to every later member) with an otherwise honest key.
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:", format=tarfile.PAX_FORMAT, pax_headers={"uname": "root"}) as archive:
            info = tarfile.TarInfo("a")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        self.assertEqual(buffer.getvalue()[156:157], tarfile.XGLTYPE)  # the first header IS a global header
        self.assert_refused("global.tar", buffer.getvalue(), "malformed-archive")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:", format=tarfile.GNU_FORMAT) as archive:
            info = tarfile.TarInfo("a")
            info.type = tarfile.GNUTYPE_SPARSE
            info.size = 0
            archive.addfile(info)
        self.assert_refused("sparse.tar", buffer.getvalue(), "malformed-archive")

    # ---- F13: the same extracted tree must describe the same way ---------------------------

    def test_f13_explicit_and_implicit_parent_directories_describe_the_same_tree(self):
        modes = {"empty/": 0o040755, "empty/sub/": 0o040755}
        explicit = self.put("explicit.zip", make_zip([("empty/", b""), ("empty/sub/", b"")], unix_modes=modes))
        implicit = self.put("implicit.zip", make_zip([("empty/sub/", b"")], unix_modes=modes))
        tar = self.put("explicit.tar", make_tar([("dir", "empty", b"", 0o755), ("dir", "empty/sub", b"", 0o755)]))
        tree = materialize(self.base / "tree", files={}, empty_dirs=("empty/sub",))
        first = package_inputs.build_archive_inventory(explicit)
        self.assertEqual(first["empty_directories"], ["empty/sub/"])
        self.assertEqual(package_inputs.build_archive_inventory(implicit)["empty_directories"], ["empty/sub/"])
        self.assertEqual(package_inputs.build_archive_inventory(tar)["empty_directories"], ["empty/sub/"])
        self.assertEqual(package_inputs.compare_inventories(first, package_inputs.build_archive_inventory(implicit)), [])
        self.assertEqual(package_inputs.build_inventory(tree), first)


class UnicodeAliasAndBoundTests(Scratch):
    """Review F11 (normalisation aliases) and F12 (count/depth bounds are uniform)."""

    COMPOSED, DECOMPOSED = "caf\u00e9", "cafe\u0301"

    def put(self, name, data):
        path = self.base / name
        path.write_bytes(data)
        return path

    def test_f11_composed_and_decomposed_names_are_aliases_in_trees_and_archives(self):
        self.assertNotEqual(self.COMPOSED, self.DECOMPOSED)
        tree = materialize(
            self.base / "tree",
            files={f"{self.COMPOSED}.txt": (b"a", 0o644), f"{self.DECOMPOSED}.txt": (b"b", 0o644)},
            empty_dirs=(),
        )
        self.assertEqual(len(list(tree.iterdir())), 2)  # this filesystem really keeps both spellings
        with self.assertRaises(package_inputs.InventoryError) as caught:
            package_inputs.build_inventory(tree)
        self.assertEqual(caught.exception.code, "case-folded-alias")
        members = [(f"{self.COMPOSED}.txt", b"a"), (f"{self.DECOMPOSED}.txt", b"b")]
        for name, blob in (
            ("alias.zip", make_zip(members)),
            ("alias.tar", make_tar([("file", n, d, 0o644) for n, d in members])),
        ):
            with self.subTest(archive=name):
                with self.assertRaises(package_inputs.InventoryError) as caught:
                    package_inputs.build_archive_inventory(self.put(name, blob))
                self.assertEqual(caught.exception.code, "case-folded-alias")

    def test_f11_directory_components_and_case_plus_normalisation_aliases_are_rejected(self):
        dirs = materialize(
            self.base / "dirs",
            files={f"D{self.COMPOSED}/a.txt": (b"a", 0o644), f"d{self.DECOMPOSED}/b.txt": (b"b", 0o644)},
            empty_dirs=(),
        )
        with self.assertRaises(package_inputs.InventoryError) as caught:
            package_inputs.build_inventory(dirs)
        self.assertEqual(caught.exception.code, "case-folded-alias")
        # Upper-case composed vs lower-case decomposed: needs casefold AND normalisation.
        with self.assertRaises(package_inputs.InventoryError):
            package_inputs.build_archive_inventory(self.put("mixed.zip", make_zip([("\u00c9.txt", b"a"), ("e\u0301.txt", b"b")])))

    def test_f11_distinct_names_are_kept_with_their_original_spelling(self):
        inventory = package_inputs.build_archive_inventory(
            self.put("fine.zip", make_zip([(f"{self.DECOMPOSED}.txt", b"a"), ("cafe.txt", b"b"), ("other.txt", b"c")]))
        )
        self.assertEqual([entry["path"] for entry in inventory["entries"]], ["cafe.txt", f"{self.DECOMPOSED}.txt", "other.txt"])

    def test_f12_every_directory_node_counts_toward_the_bound(self):
        root = materialize(self.base / "dirs", files={"a.txt": (b"x", 0o644)}, empty_dirs=tuple(f"empty{index}" for index in range(10)))
        with mock.patch.object(package_inputs, "MAX_ENTRIES", 1):
            with self.assertRaises(package_inputs.InventoryError) as caught:
                package_inputs.build_inventory(root)
        self.assertEqual(caught.exception.code, "bound-exceeded")
        # Within the limit (files <= MAX_ENTRIES, nodes <= 2 * MAX_ENTRIES) the tree is accepted.
        with mock.patch.object(package_inputs, "MAX_ENTRIES", 11):
            self.assertEqual(len(package_inputs.build_inventory(root)["empty_directories"]), 10)
        archive = make_zip([(f"e{index}/", b"") for index in range(10)], unix_modes={f"e{index}/": 0o040755 for index in range(10)})
        with mock.patch.object(package_inputs, "MAX_ENTRIES", 1):
            with self.assertRaises(package_inputs.InventoryError) as caught:
                package_inputs.build_archive_inventory(self.put("dirs.zip", archive))
        self.assertEqual(caught.exception.code, "bound-exceeded")

    def test_f12_directory_listing_is_bounded_before_it_is_materialised(self):
        consumed = []

        class Entry:
            path = "/nonexistent"

            def __init__(self, index):
                self.name = f"n{index}"
                consumed.append(index)

            def stat(self, follow_symlinks=True):
                return os.stat_result((0o120777, 0, 0, 1, 0, 0, 0, 0, 0, 0))  # a symlink: never inventoried

        class Scanner:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def __iter__(self):
                return (Entry(index) for index in range(100_000))

        root = materialize(self.base / "fake", files={"a.txt": (b"x", 0o644)}, empty_dirs=())
        with mock.patch.object(package_inputs, "MAX_ENTRIES", 10), mock.patch.object(package_inputs.os, "scandir", return_value=Scanner()):
            with self.assertRaises(package_inputs.InventoryError) as caught:
                package_inputs.build_inventory(root)
        self.assertEqual(caught.exception.code, "bound-exceeded")
        self.assertLessEqual(len(consumed), 25, len(consumed))  # stopped near 2 * MAX_ENTRIES, not 100000

    def test_f12_archive_paths_obey_the_same_depth_bound_as_directory_trees(self):
        limit = package_inputs.MAX_DEPTH
        ok_name = "/".join(["d"] * limit + ["f"])  # exactly `limit` directory components
        too_deep = "/".join(["d"] * (limit + 2) + ["f"])
        just_over = "/".join(["d"] * (limit + 1) + ["f"])
        self.assertEqual(
            [e["path"] for e in package_inputs.build_archive_inventory(self.put("ok.zip", make_zip([(ok_name, b"x")])))["entries"]],
            [ok_name],
        )
        for label, blob in (
            ("zip34", make_zip([(too_deep, b"x")])),
            ("zip33", make_zip([(just_over, b"x")])),
            ("tar34", make_tar([("file", too_deep, b"x", 0o644)])),
            ("zipdir", make_zip([("/".join(["d"] * (limit + 1)) + "/", b"")], unix_modes={"/".join(["d"] * (limit + 1)) + "/": 0o040755})),
        ):
            with self.subTest(case=label):
                with self.assertRaises(package_inputs.InventoryError) as caught:
                    package_inputs.build_archive_inventory(self.put(f"{label}.zip" if label.startswith("zip") else f"{label}.tar", blob))
                self.assertEqual(caught.exception.code, "bound-exceeded")
        # The directory walk agrees at the boundary: `limit` nested directories pass, one more fails.
        deep = materialize(self.base / "deep-ok", files={}, empty_dirs=())
        cursor = deep
        for _ in range(limit):
            cursor = cursor / "d"
        cursor.mkdir(parents=True)
        (cursor / "f").write_bytes(b"x")
        self.assertEqual(len(package_inputs.build_inventory(deep)["entries"]), 1)
        (cursor / "d").mkdir()
        (cursor / "d" / "f").write_bytes(b"x")
        with self.assertRaises(package_inputs.InventoryError) as caught:
            package_inputs.build_inventory(deep)
        self.assertEqual(caught.exception.code, "bound-exceeded")


class CommandLineTests(Scratch):
    def test_inventory_and_compare_exit_codes_and_reports(self):
        left = materialize(self.base / "left")
        right = materialize(self.base / "right")
        out = self.base / "inventory.json"
        done = self.run_cli("inventory", left, "--output", out)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(out.read_text())["inventory_sha256"], package_inputs.build_inventory(left)["inventory_sha256"])

        report = self.base / "report.json"
        done = self.run_cli("compare", left, right, "--report", report)
        self.assertEqual(done.returncode, 0, done.stderr)
        document = json.loads(report.read_text())
        self.assertEqual((document["schema"], document["verdict"], document["differences"]), (1, "PASS", []))

        (right / "zeta.txt").write_bytes(b"different")
        done = self.run_cli("compare", left, right, "--report", report)
        self.assertEqual(done.returncode, 1)
        document = json.loads(report.read_text())
        self.assertEqual(document["verdict"], "FAIL")
        self.assertEqual([item["code"] for item in document["differences"]], ["content"])

        # A readable-but-unacceptable tree is a FAIL report (exit 1), replacing any stale PASS.
        report.write_text(json.dumps({"schema": 1, "verdict": "PASS"}))
        os.symlink("zeta.txt", right / "link")
        done = self.run_cli("compare", left, right, "--report", report)
        self.assertEqual(done.returncode, 1)
        document = json.loads(report.read_text())
        self.assertEqual(document["verdict"], "FAIL")
        self.assertEqual(document["differences"][0]["code"], "symlink")

        # Missing input is exit 2 with an explicit ERROR document, never a stale PASS.
        report.write_text(json.dumps({"schema": 1, "verdict": "PASS"}))
        done = self.run_cli("compare", left, self.base / "missing", "--report", report)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(json.loads(report.read_text())["verdict"], "ERROR")
        self.assertEqual(self.run_cli("inventory", self.base / "missing").returncode, 2)
        self.assertEqual(self.run_cli("inventory").returncode, 2)
        self.assertEqual(self.run_cli("bogus").returncode, 2)
        unwritable = self.base / "no-such-dir" / "inv.json"
        self.assertEqual(self.run_cli("inventory", left, "--output", unwritable).returncode, 2)


if __name__ == "__main__":
    unittest.main()
