"""Platform-neutral regression tests for the macOS OTP-23 PoC scripts -- no Mac, no Chrome, no devices.

Mirrors scripts/test_poc_background.py conventions (unittest, ast/compile checks, fake inputs).
All macOS-only side effects are injected or skipped: the manifest register/unregister logic
(macos_install.py) and the driver's pure helpers run on any platform, the CoreGraphics window
check never runs off-darwin (returns None), and no test touches a real Chrome or Bluetooth
stack. The Windows driver scripts/chrome_validate.py must stay untouched; these tests also
assert macos_poc.py does not import it or any Windows-only machinery.

Usage:  python3 -m unittest discover -s scripts -p "test_macos_poc.py" -v
"""

import ast
import contextlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import macos_install
import macos_poc

DRIVER_SOURCE = (HERE / "macos_poc.py").read_text(encoding="utf-8")
DRIVER_TREE = ast.parse(DRIVER_SOURCE)


def fake_app(home, name="VelesOtp23PoC.app"):
    """A fake packaged bundle under a spaced parent (quoting discipline); the bundle file name
    must stay exactly VelesOtp23PoC.app because ownership is keyed on the bundle layout."""
    app = home / "build out" / name
    binary = app / macos_install.PACKAGED_BINARY
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fake-elf-otp23-host")
    binary.chmod(0o755)
    return app, binary


class SourceGuards(unittest.TestCase):
    def test_compiles_as_standalone_files(self):
        for name in ("macos_poc.py", "macos_cdp.py", "macos_install.py"):
            ast.parse((HERE / name).read_text(encoding="utf-8"))
        for name in ("macos_poc.py", "macos_cdp.py", "macos_install.py"):
            subprocess.run([sys.executable, "-m", "py_compile", str(HERE / name)],
                           check=True, timeout=60)

    def test_no_windows_imports_or_chrome_validate(self):
        for name in ("macos_poc.py", "macos_cdp.py", "macos_install.py"):
            tree = ast.parse((HERE / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for module in names:
                    self.assertFalse(module.split(".")[0] in ("chrome_validate", "ctypes.wintypes"),
                                     f"{name} imports {module}")
        self.assertNotIn("WinDLL", DRIVER_SOURCE)
        self.assertNotIn("LOCALAPPDATA", DRIVER_SOURCE)
        self.assertNotIn("wintypes", DRIVER_SOURCE)

    def test_identity_constants(self):
        identity = macos_install.read_identity()
        self.assertEqual(identity["extension_id"], "gmekhgkipjkikmaadaajljinkgfielei")
        self.assertEqual(identity["origin"], "chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/")
        self.assertEqual(identity["host_name"], "me.nagaev.veles.otp23.plain")

    def test_launch_flags(self):
        args = macos_poc.chrome_arguments(pathlib.Path("/tmp/p"), "/Ch/Ch")
        self.assertIn("--remote-debugging-port=0", args)
        self.assertIn("--enable-unsafe-extension-debugging", args)
        self.assertIn("--user-data-dir=/tmp/p", args)
        self.assertNotIn("--user-data-dir=/Users/raiden/Library/Application Support/Google/Chrome", args)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        self.dirs = self.home / "chrome profiles dir" / "host registry"
        self.app, self.binary = fake_app(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self):
        return json.loads((self.dirs / "me.nagaev.veles.otp23.plain.json").read_bytes().decode("utf-8"))

    def test_fresh_register_writes_exact_manifest(self):
        macos_install.register(self.app, self.dirs)
        document = self.manifest()
        self.assertEqual(document["name"], "me.nagaev.veles.otp23.plain")
        self.assertEqual(document["allowed_origins"],
                         ["chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/"])
        self.assertEqual(document["type"], "stdio")
        self.assertEqual(pathlib.Path(document["path"]).resolve(),
                         self.binary.resolve().expanduser())
        self.assertTrue(pathlib.Path(document["path"]).is_absolute())
        self.assertTrue(document["path"].endswith(macos_install.PACKAGED_BINARY))
        raw = (self.dirs / "me.nagaev.veles.otp23.plain.json").read_bytes()
        self.assertEqual(raw[:1], b"{", "manifest must be UTF-8 without a BOM")

    def test_reregister_owned_saves_backup(self):
        macos_install.register(self.app, self.dirs)
        first = (self.dirs / "me.nagaev.veles.otp23.plain.json").read_bytes()
        macos_install.register(self.app, self.dirs)  # same owned bundle: update allowed
        backup = self.dirs / "me.nagaev.veles.otp23.plain.json.otp23-poc-backup"
        self.assertTrue(backup.is_file() and backup.read_bytes() == first)

    def test_register_refuses_unrelated_manifest(self):
        foreign_path = "/Applications/Unrelated.app/Contents/MacOS/other-host"
        self.dirs.mkdir(parents=True)
        manifest = self.dirs / "me.nagaev.veles.otp23.plain.json"
        payload = json.dumps({"name": "me.nagaev.veles.otp23.plain", "path": foreign_path}).encode()
        manifest.write_bytes(payload)
        with self.assertRaises(SystemExit) as caught:
            macos_install.register(self.app, self.dirs)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(manifest.read_bytes(), payload, "foreign manifest must be left untouched")

    def test_unregister_restores_backup_and_removes_owned(self):
        macos_install.register(self.app, self.dirs)
        first = (self.dirs / "me.nagaev.veles.otp23.plain.json").read_bytes()
        macos_install.register(self.app, self.dirs)
        macos_install.unregister(self.dirs)
        manifest = self.dirs / "me.nagaev.veles.otp23.plain.json"
        self.assertEqual(manifest.read_bytes(), first, "backup must be restored on unregister")
        self.assertFalse((self.dirs / "me.nagaev.veles.otp23.plain.json.otp23-poc-backup").exists())
        macos_install.unregister(self.dirs)
        self.assertFalse(manifest.exists(), "owned manifest must be removed")
        macos_install.unregister(self.dirs)  # absent: idempotent, no error
        self.assertFalse(macos_install.is_owned({"name": "x", "path": "/bin/sh"}, "me.nagaev.veles.otp23.plain"))

    def test_unregister_refuses_unrelated(self):
        self.dirs.mkdir(parents=True)
        manifest = self.dirs / "me.nagaev.veles.otp23.plain.json"
        payload = json.dumps({"name": "me.nagaev.veles.otp23.plain", "path": "/etc/passwd"}).encode()
        manifest.write_bytes(payload)
        with self.assertRaises(SystemExit) as caught:
            macos_install.unregister(self.dirs)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(manifest.read_bytes(), payload, "unregister must not delete foreign files")


class BuilderTests(unittest.TestCase):
    REQUIRED = {4, 178, 179, 180, 505, 506, 507, 508, 509, 510, 511,
                1015, 1016, 1017, 4095, 4096}

    def test_mapper_is_from_argument_not_atob(self):
        expression = macos_poc.js_send_expression("conn-handle", 4096, 137)
        self.assertIn("Uint8Array.from({length: 4096}, (_, i) =>", expression)
        self.assertNotIn("atob", expression)
        self.assertNotIn("atob(", DRIVER_SOURCE)
        self.assertIn('"conn-handle"', expression)

    def decode_mapper(self, expression):
        """Decode the generated JS mapper into Python and evaluate it per index."""
        match = __import__("re").search(r"Uint8Array\.from\(\{length: (\d+)\}, \(_, i\) => (.+)\)\)$", expression)
        self.assertIsNotNone(match, expression)
        length, body = int(match.group(1)), match.group(2)
        condition, rest = body.split(" ? ", 1)
        true_branch, false_branch = rest.rsplit(" : ", 1)
        return length, [eval(true_branch, {"__builtins__": {}}, {"i": i})
                        if eval(condition, {"__builtins__": {}}, {"i": i}) else
                        eval(false_branch, {"__builtins__": {}}, {"i": i})
                        for i in range(length)]

    def test_builder_matches_fixture_for_every_size(self):
        for size in sorted(macos_poc.SIZES) + [4096]:
            for seed in (17, 137, 219):
                length, decoded = self.decode_mapper(macos_poc.js_send_expression("h", size, seed))
                self.assertEqual(length, size)
                self.assertEqual(bytes(decoded), macos_poc.fixture(size, seed))

    def test_fixture_and_echo_properties(self):
        record = macos_poc.fixture(16, 7)
        self.assertEqual(record[:4], b"VEL\x01")
        self.assertEqual(record[4:], bytes((i + 7) & 255 for i in range(4, 16)))
        self.assertEqual(macos_poc.echo(record), record[:3] + b"\x02" + record[4:])
        self.assertEqual(len(set(macos_poc.SIZES)), len(macos_poc.SIZES))  # sizes unique
        self.assertTrue(self.REQUIRED.issubset(set(macos_poc.SIZES)))
        self.assertNotEqual(macos_poc.SEED_BASE[0], macos_poc.SEED_BASE[1])
        self.assertEqual(macos_poc.SEED_BASE, [17, 137])


class ProcessTests(unittest.TestCase):
    CANNED = (
        "    1     0 Ss /sbin/launchd\n"
        "  501     1 S+ /Applications/Google Chrome.app/Contents/MacOS/Google "
        "chrome --user-data-dir=/tmp/veles-otp23-macos-profile-x\n"
        "  700   501 S  /Users/n/build out/VelesOtp23PoC.app/Contents/MacOS/otp23-host "
        "chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/ --user-data-dir=/tmp/veles\n"
        "  701   501 S  /Users/n/x/otp23-host-tauri chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/\n"
        "  702   501 S  /bin/sh /Users/n/x/otp23-host-bait\n"
    )

    def test_parse_ps_and_filter(self):
        rows = macos_poc.parse_ps(self.CANNED)
        self.assertEqual([row["pid"] for row in rows], [1, 501, 700, 701, 702])
        self.assertEqual(rows[2]["ppid"], 501)
        self.assertEqual(rows[2]["stat"], "S")
        hosts = macos_poc.host_processes(rows)
        self.assertEqual([row["pid"] for row in hosts], [700], "only the plain host, not -tauri nor bait")

    def test_lsof_endpoints_ports_only_and_none_on_query_failure(self):
        canned = ("COMMAND  PID USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME\n"
                  "otp23-ho 700  user    6u  IPv4  0xabcdef        0t0  TCP 127.0.0.1:51111->127.0.0.1:52000"
                  " (ESTABLISHED)\n"
                  "otp23-ho 700  user    7u  IPv4  0xbcdef1        0t0  UDP *:5353\n")

        def fake_run(argv, capture_output=False, text=False, timeout=None):
            return subprocess.CompletedProcess(argv, 0, stdout=canned, stderr="")

        with mock.patch.object(macos_poc.subprocess, "run", fake_run):
            endpoints = macos_poc.lsof_endpoints(700)
        self.assertEqual(endpoints, [{"local_port": 51111, "remote_port": 52000, "state": "ESTABLISHED"},
                                     {"local_port": 5353, "remote_port": None, "state": "UDP"}])
        with mock.patch.object(macos_poc.subprocess, "run",
                               lambda *a, **k: (_ for _ in ()).throw(OSError("no lsof"))):
            self.assertIsNone(macos_poc.lsof_endpoints(700), "query failure stays unavailable, never empty")

    def test_lsof_endpoint_port_parses_from_the_final_colon(self):
        # I-1: bracketed IPv6 must not stop at the interior '::1' colon
        self.assertEqual(macos_poc.port_after_final_colon("[::1]:9222"), 9222)
        self.assertEqual(macos_poc.port_after_final_colon("fe80::1%en0:52001"), 52001)
        self.assertEqual(macos_poc.port_after_final_colon("*:5353"), 5353)
        self.assertEqual(macos_poc.port_after_final_colon("127.0.0.1:51111"), 51111)
        for bad in (None, "", "nocolon", "[::1]:", "[::1]:notaport"):
            with self.assertRaises(ValueError):
                macos_poc.port_after_final_colon(bad)

    def test_lsof_endpoints_bracketed_ipv6_and_remote_ipv6(self):
        canned = ("COMMAND  PID USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME\n"
                  "Chrome   501 user    57u IPv6  0xff            0t0  TCP [::1]:9222 (LISTEN)\n"
                  "Chrome   501 user    58u IPv6  0xff            0t0  TCP "
                  "[fe80::1%en0]:51111->fd00::1%en1:52001 (ESTABLISHED)\n"
                  "Chrome   501 user    59u IPv6  0xff            0t0  UDP *:5353\n")

        def fake_run(argv, capture_output=False, text=False, timeout=None):
            return subprocess.CompletedProcess(argv, 0, stdout=canned, stderr="")

        with mock.patch.object(macos_poc.subprocess, "run", fake_run):
            endpoints = macos_poc.lsof_endpoints(501)
        self.assertEqual(endpoints, [
            {"local_port": 9222, "remote_port": None, "state": "LISTEN"},
            {"local_port": 51111, "remote_port": 52001, "state": "ESTABLISHED"},
            {"local_port": 5353, "remote_port": None, "state": "UDP"}],
            "a port of 1 (or any interior-colon artifact) would be a misparse")

    def test_lsof_any_nonempty_stderr_makes_the_census_unavailable_even_rc0(self):
        # I-1 follow-up: rc=0 with a diagnostic stderr must never become a fabricated zero
        rows = ("COMMAND  PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
                "otp23-ho 700  user    6u  IPv4  0xff  0t0      TCP *:51111 (LISTEN)\n")
        cases = {
            "rc0-warning-empty-stdout": (0, "", "warning: cannot stat some fd\n", None),
            "rc0-rows-plus-warning": (0, rows, "lsof: partial output, permission restricted\n", None),
        }
        for name, (returncode, stdout, stderr, expected) in cases.items():
            def fake_run(argv, capture_output=False, text=False, timeout=None, _r=(returncode, stdout, stderr)):
                return subprocess.CompletedProcess(argv, _r[0], stdout=_r[1], stderr=_r[2])
            with mock.patch.object(macos_poc.subprocess, "run", fake_run), self.subTest(name):
                self.assertEqual(macos_poc.lsof_endpoints(700), expected, name)

    def test_lsof_endpoints_returncode_and_stderr_are_honoured(self):
        # I-1: only a successful query may produce []; [] is never fabricated from a failed query
        cases = {
            "rc1-no-stderr": (1, "", "", []),           # genuine alive-pid no-match: lsof's real "no records"
            "rc1-permission": (1, "", "lsof: can't get process information: permission denied", None),
            "rc2-internal": (2, "", "lsof: internal error", None),
            "rc3-weird": (3, "", "boom", None),
            "rc0-unparsable": (0, "COMMAND PID\notp23-ho 700 user 6u TCP garbage\n", "", None),
            "rc0-empty": (0, "", "", []),               # rc0 with no rows: honest empty (query succeeded)
        }
        for name, (returncode, stdout, stderr, expected) in cases.items():
            def fake_run(argv, capture_output=False, text=False, timeout=None, _r=(returncode, stdout, stderr)):
                return subprocess.CompletedProcess(argv, _r[0], stdout=_r[1], stderr=_r[2])
            with mock.patch.object(macos_poc.subprocess, "run", fake_run), self.subTest(name):
                self.assertEqual(macos_poc.lsof_endpoints(700), expected, name)

    # ---- foreign-nullfs warning classifier (P0-A era; Opus preflight Task 1) --------------------

    MOUNT_POINT = "/private/var/folders/yc/someuserfolder/T/AppTranslocation/ABCDEF01-2345-6789-ABCD-EF0123456789"

    def warning_block(self, mount_point=None, dev="2e000004", with_dev=True):
        block = (f"lsof: WARNING: can't stat() nullfs file system "
                 f"{mount_point or self.MOUNT_POINT}\n"
                 "    Output information may be incomplete.\n")
        if with_dev:
            block += f'    assuming "dev={dev}" from mount table\n'
        return block

    ROWS = ("COMMAND  PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
            "otp23-ho 700 user 6u IPv4 0xff 0t0 TCP *:51111 (LISTEN)\n")
    EXPECT = [{"local_port": 51111, "remote_port": None, "state": "LISTEN"}]

    def setUp(self):
        self._old_env = os.environ.pop("VELES_OTP23_LSOF_DIAG", None)
        self.diag_dir = tempfile.mkdtemp(prefix="lsof-diag-")
        self.diag_path = str(pathlib.Path(self.diag_dir) / "lsof-diagnostics.jsonl")

    def tearDown(self):
        if self._old_env is not None:
            os.environ["VELES_OTP23_LSOF_DIAG"] = self._old_env
        import shutil
        shutil.rmtree(self.diag_dir, ignore_errors=True)
        macos_poc._FOREIGN_NULLFS_CACHE = None
        macos_poc._LSOF_SNAPSHOT_CACHE.clear()

    def self_user(self):
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name

    def foreign_mount(self, owner):
        """A fake /sbin/mount table whose only nullfs line is owned by `owner`, plus one non-nullfs line."""
        return (f"/Users/owner/Downloads/App.app on {self.MOUNT_POINT} "
                f"(nullfs, local, nodev, nosuid, read-only, nobrowse, mounted by {owner})\n"
                "/dev/disk1s1 on / (apfs, local, journaled)\n")

    def patch_lsof_and_mount(self, foreign_owner, *, rc=1, stdout=ROWS, stderr=None, blocks=1):
        stderr = stderr if stderr is not None else (self.warning_block() * blocks)
        mount_text = self.foreign_mount(foreign_owner)

        def fake_run(argv, capture_output=False, text=False, timeout=None):
            if argv == ["/sbin/mount"]:
                return subprocess.CompletedProcess(argv, 0, stdout=mount_text, stderr="")
            return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr=stderr)
        return mock.patch.object(macos_poc.subprocess, "run", fake_run)

    def setup_diag(self):
        os.environ["VELES_OTP23_LSOF_DIAG"] = self.diag_path

    def classify(self, stderr, mounts):
        return macos_poc.classify_lsof_stderr(stderr, mounts)

    def test_classifier_exact_block_is_benign_only_with_matching_foreign_mount(self):
        block = self.warning_block()
        self.assertEqual(self.classify(block, {self.MOUNT_POINT}), (True, [{"path": self.MOUNT_POINT, "dev": "2e000004"}]))
        self.assertEqual(self.classify(block, set()), (False, None))
        other = {self.MOUNT_POINT.replace("ABCDEF01", "FFFFFF00")}
        self.assertEqual(self.classify(block, other), (False, None), "path not in the foreign set: unavailable")
        self.assertEqual(self.classify("", {self.MOUNT_POINT}), (True, []), "clean stderr: nothing to classify")

    def test_lsof_accepts_the_exact_foreign_block_with_rows_and_records_it(self):
        # exact 3-line block + rows, diagnostics recorded and holding the VERBATIM stderr
        self.setup_diag()
        with self.patch_lsof_and_mount("someoneelse"):
            endpoints = macos_poc.lsof_endpoints(700)
        self.assertEqual(endpoints, self.EXPECT)
        record = json.loads((pathlib.Path(self.diag_path).read_text().splitlines()[0]))
        self.assertEqual(record["argv"], ["lsof", "-nP", "-a", "-p", "700", "-i"], "argv stays normal (no -w)")
        self.assertEqual(record["rc"], 1)
        self.assertEqual(record["stdout_rows"], 2)
        self.assertEqual(record["stderr_verbatim"], self.warning_block())
        self.assertEqual(record["classification"], {"benign": True, "blocks": [{"path": self.MOUNT_POINT, "dev": "2e000004"}]})
        snapshot = pathlib.Path(self.diag_dir) / "mount-nullfs.txt"
        self.assertTrue(snapshot.is_file())
        self.assertEqual(record["mount_snapshot_sha256"], macos_poc.file_sha256(snapshot))
        text = snapshot.read_text()
        self.assertIn(self.MOUNT_POINT, text, "the mount point is kept verbatim")
        self.assertNotIn("App.app", text, "no foreign app name in the snapshot")
        self.assertNotIn("Users/owner", text, "no foreign source path in the snapshot")
        self.assertNotIn("someoneelse", text, "no other user's account name in the snapshot")
        self.assertIn("mounted by uid≠self", text)
        self.assertNotIn("mounted by " + self.self_user(), text)

    def test_lsof_exact_block_rc1_no_rows_is_honest_empty(self):
        self.setup_diag()
        with self.patch_lsof_and_mount("someoneelse", rc=1, stdout="COMMAND  PID USER\n"):
            self.assertEqual(macos_poc.lsof_endpoints(700), [], "genuine no-match: never a fabricated zero")

    def test_lsof_two_exact_blocks_for_two_foreign_mounts(self):
        point_b = self.MOUNT_POINT.replace("ABCDEF01-2345", "00000009-1111")
        blocks = self.warning_block() + self.warning_block(mount_point=point_b)
        self.setup_diag()
        mount_text = self.foreign_mount("someoneelse") + \
            f"/Users/owner/Downloads/Other.app on {point_b} (nullfs, local, mounted by someoneelse)\n"

        def fake_run(argv, capture_output=False, text=False, timeout=None):
            if argv == ["/sbin/mount"]:
                return subprocess.CompletedProcess(argv, 0, stdout=mount_text, stderr="")
            return subprocess.CompletedProcess(argv, 1, stdout=self.ROWS, stderr=blocks)
        with mock.patch.object(macos_poc.subprocess, "run", fake_run):
            self.assertEqual(macos_poc.lsof_endpoints(700), self.EXPECT)

    def test_lsof_block_without_the_assuming_dev_line_is_unavailable(self):
        self.setup_diag()
        with self.patch_lsof_and_mount("someoneelse", stderr=self.warning_block(with_dev=False)):
            self.assertIsNone(macos_poc.lsof_endpoints(700))

    def test_lsof_block_for_a_mount_owned_by_self_is_unavailable(self):
        self.setup_diag()
        with self.patch_lsof_and_mount(self.self_user()):
            self.assertIsNone(macos_poc.lsof_endpoints(700), "self-owned mount: the warning is not foreign")

    def test_lsof_any_extra_stderr_line_is_unavailable(self):
        self.setup_diag()
        extra = self.warning_block() + "and one more line\n"
        with self.patch_lsof_and_mount("someoneelse", stderr=extra):
            self.assertIsNone(macos_poc.lsof_endpoints(700))

    def test_lsof_non_nullfs_foreign_mount_is_unavailable(self):
        # the mount table has the path but as a NON-nullfs type: it cannot be in the foreign set
        self.setup_diag()
        mount_text = (f"/Users/owner/Downloads/App.app on {self.MOUNT_POINT} "
                      "(apfs, local, journaled)\n")

        def fake_run(argv, capture_output=False, text=False, timeout=None):
            if argv == ["/sbin/mount"]:
                return subprocess.CompletedProcess(argv, 0, stdout=mount_text, stderr="")
            return subprocess.CompletedProcess(argv, 1, stdout=self.ROWS, stderr=self.warning_block())
        with mock.patch.object(macos_poc.subprocess, "run", fake_run):
            self.assertIsNone(macos_poc.lsof_endpoints(700))

    def test_lsof_rc2_with_the_block_is_unavailable(self):
        self.setup_diag()
        with self.patch_lsof_and_mount("someoneelse", rc=2):
            self.assertIsNone(macos_poc.lsof_endpoints(700))

    def test_lsof_benign_block_without_diag_env_is_unavailable(self):
        with self.patch_lsof_and_mount("someoneelse"):
            self.assertIsNone(macos_poc.lsof_endpoints(700), "unaudited benign warning: unavailable")

    def test_lsof_diag_write_failure_fails_closed(self):
        self.setup_diag()
        blocker = pathlib.Path(self.diag_dir) / "not-a-dir"
        blocker.write_text("I am a file")
        os.environ["VELES_OTP23_LSOF_DIAG"] = str(blocker / "sub" / "lsof-diagnostics.jsonl")
        with self.patch_lsof_and_mount("someoneelse"):
            self.assertIsNone(macos_poc.lsof_endpoints(700), "diagnostics write failure: unavailable")
        os.environ["VELES_OTP23_LSOF_DIAG"] = self.diag_path

    def test_lsof_diag_env_unset_census_still_works_when_stderr_clean(self):
        # historical behavior preserved: a genuinely clean lsof never needed diagnostics
        rows = "COMMAND  PID USER\n"  # rc=1 no rows
        def fake_run(argv, capture_output=False, text=False, timeout=None):
            return subprocess.CompletedProcess(argv, 1, stdout=rows, stderr="")
        with mock.patch.object(macos_poc.subprocess, "run", fake_run):
            self.assertEqual(macos_poc.lsof_endpoints(700), [])

    def test_clean_stderr_is_recorded_when_diag_env_is_set(self):
        self.setup_diag()
        rows = "COMMAND  PID USER\n"
        def fake_run(argv, capture_output=False, text=False, timeout=None):
            if argv == ["/sbin/mount"]:
                return subprocess.CompletedProcess(argv, 0, stdout="/dev/disk1s1 on / (apfs, local)\n", stderr="")
            return subprocess.CompletedProcess(argv, 1, stdout=rows, stderr="")
        with mock.patch.object(macos_poc.subprocess, "run", fake_run):
            self.assertEqual(macos_poc.lsof_endpoints(700), [])
        record = json.loads(pathlib.Path(self.diag_path).read_text().splitlines()[0])
        self.assertEqual(record["classification"], "clean_stderr")
        self.assertEqual(record["stderr_verbatim"], "")

    def test_mount_unparseable_keeps_the_warning_unavailable(self):
        self.setup_diag()
        def fake_run(argv, capture_output=False, text=False, timeout=None):
            if argv == ["/sbin/mount"]:
                raise OSError("mount not runnable")
            return subprocess.CompletedProcess(argv, 1, stdout=self.ROWS, stderr=self.warning_block())
        with mock.patch.object(macos_poc.subprocess, "run", fake_run):
            self.assertIsNone(macos_poc.lsof_endpoints(700), "unmeasurable mount table: warning stays unavailable")

    def test_parse_lsof_endpoints_is_pure_and_total(self):
        self.assertEqual(macos_poc.parse_lsof_endpoints(
            ["otp23-ho 700 user 6u IPv4 0xff 0t0 TCP *:51111 (LISTEN)"]),
            [{"local_port": 51111, "remote_port": None, "state": "LISTEN"}])
        with self.assertRaises(ValueError):
            macos_poc.parse_lsof_endpoints(["some unix socket row without tcp/udp"])

    def test_ancestry_chain(self):
        rows = macos_poc.parse_ps(self.CANNED)
        chain = macos_poc.ancestry(rows, 700)
        self.assertEqual([(link["pid"], link["comm"]) for link in chain],
                         [(501, "Google"), (1, "launchd")])

    def test_pid_alive_not_invented_off_darwin(self):
        if sys.platform == "darwin":
            return
        self.assertIsNone(macos_poc.pid_alive(12345), "liveness is measured, never invented off-macOS")

    def test_wait_gone_real_process(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.4)"])
        self.assertTrue(macos_poc.process_alive(child.pid))
        gone = macos_poc.wait_gone(child.pid, 5)
        child.wait(timeout=5)
        self.assertIsNotNone(gone, "an exited child must count as gone even before reaping")
        self.assertFalse(macos_poc.process_alive(child.pid))
        self.assertIsNotNone(macos_poc.wait_gone(child.pid, 0.3), "already-gone pid resolves fast")


class CensusAnyLayer(unittest.TestCase):
    """B-2: the any-layer census keeps off-screen/nonzero-layer host windows as data the summariser
    fails on — they must never silently drop out of the probe."""

    def test_census_windows_from_entries_keeps_all_layers_and_splits_layer_zero(self):
        entries = [{"pid": 700, "layer": 0}, {"pid": 700, "layer": 24}]
        census = macos_poc.census_windows_from_entries(entries)
        self.assertEqual(census["all"], [{"layer": 0}, {"layer": 24}])
        self.assertEqual(census["layer_zero"], [{"layer": 0}])
        empty = macos_poc.census_windows_from_entries([])
        self.assertEqual(empty, {"all": [], "layer_zero": []})


class EvidenceTests(unittest.TestCase):
    def test_scrub_replaces_home(self):
        home = str(pathlib.Path.home())
        self.assertIn("%HOME%", macos_poc.scrub(json.dumps({"path": home})))
        self.assertEqual(macos_poc.scrub(home), "%HOME%")
        self.assertNotIn(home, macos_poc.scrub(json.dumps({"a": home, "b": home})))

    def _git(self, results):
        calls = iter(results)

        def run(cmd, **kwargs):
            result = next(calls)
            if isinstance(result, Exception):
                raise result
            return subprocess.CompletedProcess(cmd, result[0], result[1], result[2])
        return mock.patch.object(macos_poc.subprocess, "run", side_effect=run)

    def test_source_revision_success(self):
        with self._git([(0, "abc123\n", ""), (0, " M file\n", "")]):
            self.assertEqual(macos_poc.source_revision(),
                             {"available": True, "commit": "abc123", "dirty": True})
        with self._git([(0, "abc123\n", ""), (0, "", "")]):
            self.assertFalse(macos_poc.source_revision()["dirty"])

    def test_source_revision_git_missing(self):
        with self._git([FileNotFoundError(2, "No such file", "git")]):
            rev = macos_poc.source_revision()
        self.assertFalse(rev["available"])
        self.assertEqual(rev["stage"], "rev-parse")
        self.assertIn("git not runnable", rev["reason"])
        self.assertNotIn("commit", rev)

    def test_source_revision_not_a_repo_is_bounded_and_scrubbed(self):
        home = str(pathlib.Path.home())
        stderr = f"fatal: not a git repository: {home}/x\n" + "y" * 1000
        with self._git([(128, "", stderr)]):
            rev = macos_poc.source_revision()
        self.assertFalse(rev["available"])
        self.assertEqual(rev["stage"], "rev-parse")
        self.assertLessEqual(len(rev["reason"]), 200)
        self.assertNotIn("commit", rev)
        self.assertNotIn(home, macos_poc.scrub(json.dumps(rev)))

    def test_source_revision_status_failure_reports_stage(self):
        with self._git([(0, "abc123\n", ""),
                        subprocess.TimeoutExpired("git", 30)]):
            rev = macos_poc.source_revision()
        self.assertEqual((rev["available"], rev["stage"]), (False, "status"))
        self.assertIn("TimeoutExpired", rev["reason"])

    def test_parse_trace_safe_fields_only(self):
        trace = pathlib.Path(tempfile.mkdtemp()) / "t.log"
        trace.write_text(
            "[       0ms pid=87052] start pid=87052\n"
            "[    1242ms pid=87052] conn 2 max_write_without_response=512 inferred effective att mtu=515 (max write + 3, capped 517)\n"
            "[    1243ms pid=87052] conn 2 mtu=185 at 1300ms\n"
            "[    2956ms pid=87052] out len=160 type=connected ... connection_handle=600e\n"
            "[    3543ms pid=87052] in  len=12 type=send ... record_base64.len=12\n"
            "[    3600ms pid=87052] in  len=9 type=message ... record_base64.len=8\n")
        parsed = macos_poc.parse_trace(trace)
        self.assertEqual(parsed["mtu_by_conn"], {2: 185})
        self.assertEqual(parsed["mtu_source_by_conn"], {2: macos_poc.MTU_NEGOTIATED})
        self.assertEqual(len(parsed["sends"]), 1)
        self.assertEqual(len(parsed["replies"]), 1)

    def test_parse_trace_accepts_actual_macos_max_write_line(self):
        trace = pathlib.Path(tempfile.mkdtemp()) / "t.log"
        trace.write_text(
            "[       0ms pid=87052] start pid=87052\n"
            "[    1242ms pid=87052] conn 2 max_write_without_response=512 inferred effective att mtu=515 (max write + 3, capped 517)\n"
            "[    1300ms pid=87052] conn 3 max_write_without_response=244 inferred effective att mtu=247 (max write + 3, capped 517)\n"
            "[    2956ms pid=87052] out len=160 type=connected ... connection_handle=600e\n")
        parsed = macos_poc.parse_trace(trace)
        self.assertEqual(parsed["mtu_by_conn"], {2: 515, 3: 247})
        self.assertEqual(parsed["mtu_source_by_conn"],
                         {2: macos_poc.MTU_INFERRED_MAX_WRITE, 3: macos_poc.MTU_INFERRED_MAX_WRITE},
                         "macOS MTU is derived, never reported as a negotiated measurement")
        self.assertEqual(set(parsed), {"mtu_by_conn", "mtu_source_by_conn", "budget_by_conn", "sends", "replies"},
                         "no per-handle or per-address mapping fields exist")
        self.assertEqual(parsed["sends"], [])

    def test_parse_trace_accepts_attribute_budget_line_without_inventing_an_mtu(self):
        trace = pathlib.Path(tempfile.mkdtemp()) / "t.log"
        trace.write_text(
            "[    1242ms pid=87052] conn 2 max_write_without_response=512 attribute_budget=512 payload_budget=508\n"
            "[    1300ms pid=87052] conn 3 max_write_without_response=244 attribute_budget=244 payload_budget=240\n")
        parsed = macos_poc.parse_trace(trace)
        self.assertEqual(parsed["mtu_by_conn"], {}, "CoreBluetooth reports no MTU and none is derived")
        self.assertEqual(parsed["budget_by_conn"], {
            2: {"max_write_without_response": 512, "attribute_budget": 512, "payload_budget": 508},
            3: {"max_write_without_response": 244, "attribute_budget": 244, "payload_budget": 240},
        })
        self.assertEqual(parsed["mtu_source_by_conn"],
                         {2: macos_poc.BUDGET_MEASURED_MAX_WRITE, 3: macos_poc.BUDGET_MEASURED_MAX_WRITE})

    def test_parse_trace_accepts_windows_form(self):
        trace = pathlib.Path(tempfile.mkdtemp()) / "t.log"
        trace.write_text(
            "[    1242ms pid=87052] conn 2 mtu=517 at 1242ms\n"
            "[    1300ms pid=87052] conn 3 mtu=185 at 1300ms\n")
        parsed = macos_poc.parse_trace(trace)
        self.assertEqual(parsed["mtu_by_conn"], {2: 517, 3: 185})
        self.assertEqual(parsed["mtu_source_by_conn"],
                         {2: macos_poc.MTU_NEGOTIATED, 3: macos_poc.MTU_NEGOTIATED})

    def test_parse_trace_mixed_sources_keyed_by_token_only(self):
        trace = pathlib.Path(tempfile.mkdtemp()) / "t.log"
        trace.write_text(
            "[    1242ms pid=87052] conn 2 max_write_without_response=512 inferred effective att mtu=515 (max write + 3, capped 517)\n"
            "[    1300ms pid=87052] conn 3 mtu=185 at 1300ms\n")
        parsed = macos_poc.parse_trace(trace)
        self.assertEqual(parsed["mtu_by_conn"], {2: 515, 3: 185})
        self.assertEqual(parsed["mtu_source_by_conn"],
                         {2: macos_poc.MTU_INFERRED_MAX_WRITE, 3: macos_poc.MTU_NEGOTIATED})
        self.assertNotIn("handle", " ".join(parsed), "MTUs stay keyed by the host connection token")

    def test_deny_or_raise_classification(self):
        with self.assertRaises(macos_poc.PermissionDenied):
            macos_poc.deny_or_raise({"type": "error", "code": "scan_failed", "stage": "access_denied"}, "scan")
        with self.assertRaises(macos_poc.PermissionDenied):
            macos_poc.deny_or_raise({"type": "error", "code": "connect_failed",
                                     "stage": "connect", "detail": "permission denied"}, "connect")
        with self.assertRaises(AssertionError):
            macos_poc.deny_or_raise({"type": "error", "code": "connect_failed", "stage": "connect"}, "connect")

    def test_registration_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            app, binary = fake_app(home)
            dirs = home / "hosts"
            with self.assertRaises(SystemExit):
                macos_poc.check_registration(dirs, binary)  # not registered
            macos_install.register(app, dirs)
            self.assertTrue(macos_poc.check_registration(dirs, binary))
            tampered = dirs / "me.nagaev.veles.otp23.plain.json"
            doc = json.loads(tampered.read_text())
            doc["path"] = "/Applications/Other.app/Contents/MacOS/otp23-host"
            tampered.write_text(json.dumps(doc))
            with self.assertRaises(SystemExit):
                macos_poc.check_registration(dirs, binary)


class ProfileManifestTests(unittest.TestCase):
    """Chrome --user-data-dir=<temp> reads <temp>/NativeMessagingHosts, not the real profile."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name)
        self.app, self.binary = fake_app(self.home)
        self.dirs = self.home / "hosts"
        macos_install.register(self.app, self.dirs)
        self.source = self.dirs / "me.nagaev.veles.otp23.plain.json"

    def test_manifest_copied_before_popen_and_source_untouched(self):
        before = self.source.read_bytes()
        seen = {}

        def fake_popen(argv, env=None):
            profile = pathlib.Path(next(a for a in argv if a.startswith("--user-data-dir="))
                                   .split("=", 1)[1])
            copy = profile / "NativeMessagingHosts" / "me.nagaev.veles.otp23.plain.json"
            seen["profile"] = profile
            seen["exists_at_popen"] = copy.is_file()
            seen["bytes"] = copy.read_bytes() if copy.is_file() else None
            raise OSError("stop after popen")

        with mock.patch.object(macos_poc.subprocess, "Popen", fake_popen):
            with self.assertRaises(OSError):
                macos_poc.ChromeRun("/fake/chrome", manifest_source=self.source)
        self.assertTrue(seen["exists_at_popen"], "manifest must exist before Chrome starts")
        self.assertEqual(seen["bytes"], before)
        document = json.loads(seen["bytes"].decode("utf-8"))
        self.assertEqual(document["allowed_origins"], [macos_install.read_identity()["origin"]])
        self.assertEqual(pathlib.Path(document["path"]), self.binary.resolve())
        self.assertEqual(self.source.read_bytes(), before, "registered source must be untouched")
        self.assertFalse(seen["profile"].exists(), "owned profile removed when launch fails")
        self.assertEqual(sorted(p.name for p in self.dirs.iterdir()), [self.source.name])

    def test_cleanup_removes_only_owned_profile(self):
        bystander = self.home / "veles-otp23-macos-profile-other"
        bystander.mkdir()
        with mock.patch.object(macos_poc.subprocess, "Popen", side_effect=OSError("no")):
            with self.assertRaises(OSError):
                macos_poc.ChromeRun("/fake/chrome", manifest_source=self.source)
        self.assertTrue(bystander.is_dir())
        self.assertTrue(self.source.is_file())

    def test_run_driver_passes_registered_manifest(self):
        ScriptedChrome.instances = []
        ScriptedChrome.drain_queue = []
        clock, fake_ps = FakeClock(), FakePs(chrome_pid=5001)
        args = types.SimpleNamespace(app=self.app, manifest_dir=self.dirs, diag_scan_only=True,
                                     repeat_4096=3, chrome="/fake/chrome")
        with contextlib.ExitStack() as stack:
            for patch in (mock.patch.object(macos_poc, "ChromeRun", ScriptedChrome),
                          mock.patch.object(macos_poc, "ps_snapshot", fake_ps.snapshot),
                          mock.patch.object(macos_poc, "wait_gone", fake_ps.wait_gone),
                          mock.patch.object(macos_poc, "windows_owned_by", lambda pid: None),
                          mock.patch.object(macos_poc, "time", clock)):
                stack.enter_context(patch)
            macos_poc.run_driver(args, self.home / "ev" / "poc.json")
        self.assertEqual(ScriptedChrome.instances[0].manifest_source, self.source)

    def test_origin_must_match_exactly(self):
        document = json.loads(self.source.read_text())
        for origins in (["chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/"],
                        [macos_install.read_identity()["origin"], "chrome-extension://x/"],
                        []):
            document["allowed_origins"] = origins
            self.source.write_text(json.dumps(document))
            with self.assertRaises(AssertionError):
                macos_poc.check_registration(self.dirs, self.binary)

    def test_hello_failure_context_reports_disconnect_message(self):
        chrome = types.SimpleNamespace(js=lambda session, expr, timeout=10: {
            "portOpen": False,
            "disconnect": {"message": "Specified native messaging host not found."}})
        text = macos_poc.hello_failure_context(chrome, "s")
        self.assertIn("Specified native messaging host not found", text)
        self.assertIn("portOpen=False", text)

        def boom(session, expr, timeout=10):
            raise RuntimeError("x")
        self.assertIn("unavailable", macos_poc.hello_failure_context(types.SimpleNamespace(js=boom), "s"))


class WindowsCheckContract(unittest.TestCase):
    def test_window_check_inert_off_darwin(self):
        if sys.platform == "darwin":
            return
        self.assertIsNone(macos_poc.windows_owned_by(12345))


class FakeClock:
    """time.monotonic/sleep stand-in so polls never really wait."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakePs:
    """ps snapshot stand-in: no host during the preflight, host present while alive, gone
    once wait_gone runs (models the real lifecycle)."""

    def __init__(self, chrome_pid):
        self.chrome_pid = chrome_pid
        self.dead = False
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        base = ("     1     0 Ss /sbin/launchd\n"
                "  5001     1 S+ /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                "--user-data-dir=/tmp/fake-profile\n")
        if self.calls == 1 or self.dead:
            return base
        return (base
                + "  4242  %d S  /opt/x dir/VelesOtp23PoC.app/Contents/MacOS/otp23-host "
                  "chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/\n" % self.chrome_pid)

    def wait_gone(self, pid, timeout=10.0):
        self.dead = True
        return 0.05


class ScriptedChrome:
    """Fake ChromeRun: js() dispatcher over the connector expressions with a scripted
    drainEvents queue (consumed FIFO; empty afterwards)."""

    drain_queue = []
    instances = []

    def __init__(self, chrome_path=None, extra_env=None, manifest_source=None):
        self.manifest_source = manifest_source
        ScriptedChrome.instances.append(self)
        self.proc = types.SimpleNamespace(pid=5001)
        self.version = {"product": "fake/1.0"}
        self.closed = False
        self.cdp = types.SimpleNamespace(
            call=lambda method, params=None, timeout=60, session=None:
            {"id": "gmekhgkipjkikmaadaajljinkgfielei"})

    def open_page(self, url):
        return "target-1", "session-1"

    def js(self, session, expression, timeout=70):
        if expression.startswith("typeof window.otp23"):
            return True
        if "otp23.connect(" in expression:
            return 1
        if "otp23.hello()" in expression:
            return {"type": "ready", "bridge_version": "0", "platform": "fake", "api_version": 1}
        if "otp23.startScan(" in expression:
            return {"type": "scan_state", "status": "started", "scan_handle": "sh", "request_id": "r-scan"}
        if "otp23.drainEvents()" in expression:
            return ScriptedChrome.drain_queue.pop(0) if ScriptedChrome.drain_queue else []
        if "otp23.stopScan(" in expression:
            return {"type": "scan_state", "status": "stopped", "request_id": "r-stop"}
        if "otp23.shutdown()" in expression:
            return {"type": "shutdown_complete", "request_id": "r-shutdown"}
        raise AssertionError("unexpected JS evaluation: " + expression[:120])

    def close(self):
        self.closed = True


class DiagScanSemantics(unittest.TestCase):
    """Regression for two diag bugs found in controller review:
    1. the diag path drained device events twice -- poll_devices(20) already consumed them and
       returned the map, but the return was ignored and a SECOND drainEvents() (normally empty)
       required `found`, failing the run although phones had been seen. The diag path must use
       poll_devices' returned map, stop the scan afterwards and report the count safely.
    2. the diag path early-`return`ed inside try (function returned None -> exit 0) while the
       finally wrote FAIL; the process exit code must honor the final fail-closed calculation."""

    def _run_diag(self, drain_sequence):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = pathlib.Path(tmp.name)
        app, _binary = fake_app(home)
        dirs = home / "hosts"
        macos_install.register(app, dirs)
        ScriptedChrome.drain_queue = list(drain_sequence)
        ScriptedChrome.instances = []
        clock, fake_ps = FakeClock(), FakePs(chrome_pid=5001)
        args = types.SimpleNamespace(app=app, manifest_dir=dirs, diag_scan_only=True,
                                     repeat_4096=3, chrome="/fake/chrome")
        out = home / "evidence" / "poc.json"
        patches = [
            mock.patch.object(macos_poc, "ChromeRun", ScriptedChrome),
            mock.patch.object(macos_poc, "ps_snapshot", fake_ps.snapshot),
            mock.patch.object(macos_poc, "wait_gone", fake_ps.wait_gone),
            mock.patch.object(macos_poc, "windows_owned_by", lambda pid: None),
            mock.patch.object(macos_poc, "time", clock),
        ]
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            code = macos_poc.run_driver(args, out)
        self.assertEqual(ScriptedChrome.drain_queue, [], "scripted drains must be consumed by the poll")
        self.assertTrue(ScriptedChrome.instances and ScriptedChrome.instances[0].closed,
                        "the finally block must close the fake browser")
        chrome = ScriptedChrome.instances[0]
        report = json.loads(out.read_text(encoding="utf-8"))
        return code, chrome, report

    def test_one_phone_seen_passes_without_redrain_failure(self):
        device_event = {"at": 0, "message": {"type": "device_found", "label": "xxxx",
                                             "device_handle": "corebluetooth:0123:1",
                                             "platform_hint": "corebluetooth:0123456789ab:1"}}
        code, _chrome, report = self._run_diag([[device_event]])
        self.assertEqual(code, 0, "diag with phones seen must exit PASS: %s" % report.get("failure"))
        self.assertEqual(report["result"], "PASS")
        case = next(c for c in report["results"] if c["case"] == "diagnostic-scan")
        self.assertEqual(case["devices_found"], 1)
        self.assertEqual(case["label_lengths"], [4])
        self.assertEqual(next(c for c in report["results"] if c["case"] == "shutdown")["mode"], "diag")
        serialized = json.dumps(report)
        self.assertNotIn("device_handle", serialized, "no per-device identifiers in the report")
        self.assertNotIn('"label":', serialized, "labels are reduced to lengths only")

    def test_no_phone_fails_closed_with_nonzero_exit(self):
        code, _chrome, report = self._run_diag([])
        self.assertEqual(code, 1, "diag with no phones must honor finally-computed FAIL")
        self.assertEqual(report["result"], "FAIL")
        self.assertIn("no phone advertised the Veles service", report["failure"])

    def test_run_driver_never_early_returns_bare(self):
        tree = ast.parse((HERE / "macos_poc.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "run_driver")

        def own_returns(node):
            found = []
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    continue  # nested poll_devices/exchange returns are not run_driver's
                if isinstance(child, ast.Return):
                    found.append(child)
                found.extend(own_returns(child))
            return found

        statements = own_returns(function)
        self.assertEqual(2, len(statements), "exactly the preflight failure and the final code")
        for statement in statements:
            self.assertIsNotNone(statement.value, "a bare return yields None -> exit 0 lie")


if __name__ == "__main__":
    unittest.main()