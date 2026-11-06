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

import io
import json
import lzma
import os
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
            "dir-file.zip": (make_zip([("a", b"1"), ("a/", b"")]), "duplicate-archive-member"),
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

        # A declared size above the cap is refused without decompressing anything.
        bomb = self.put("bomb.zip", make_zip([("big", b"\x00" * 100_000)]))
        with mock.patch.object(package_inputs, "MAX_TOTAL_BYTES", 10_000), mock.patch.object(
            package_inputs, "MAX_ARCHIVE_MEMBER_BYTES", 10_000
        ):
            with mock.patch.object(zipfile.ZipFile, "open", side_effect=AssertionError("decompressed a member")):
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
            archive.writestr(info, bytes(range(256)) * 64)
        blob = bytearray(buffer.getvalue())
        data_start = 30 + len("data.bin")  # local header + name; the deflate stream follows
        blob[data_start : data_start + 16] = b"\xff" * 16  # invalid deflate block
        with self.assertRaises(package_inputs.InventoryError) as caught:
            package_inputs.build_archive_inventory(self.put("corrupt.zip", bytes(blob)))
        self.assertEqual(caught.exception.code, "malformed-archive")

    def test_decompressor_exceptions_other_than_badzipfile_are_mapped(self):
        # CPython 3.14's zipfile reports every corruption I can provoke as BadZipFile, but other
        # builds/codecs surface the raw decompressor error. The mapping is defensive, so it is
        # tested by simulating those exceptions rather than by crafting an archive.
        path = self.put("ok.zip", make_zip([("a", b"payload")]))
        for error in (zlib.error("invalid block type"), lzma.LZMAError("corrupt input")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(zipfile.ZipFile, "open", side_effect=error):
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
