"""Tests for macos_background_support.py -- mocked ps rows and a real temp manifest; no Chrome, no device."""

import ast
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import macos_background_support as support
import macos_poc as mp
from macos_install import PACKAGED_BINARY, read_identity

HERE = pathlib.Path(__file__).resolve().parent
HOST = "/Applications/VelesOtp23PoC.app/Contents/MacOS/otp23-host chrome-extension://abc/"
PS = "\n".join([
    "    1     0 Ss   /sbin/launchd",
    "  500     1 S    /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=0",
    "  610   500 S    " + HOST,
    "  611   500 S    /x/otp23-host-tauri bait",
    "  700     1 S    /usr/bin/otp23-host-other",
])


class ProcessRows(unittest.TestCase):
    def test_host_processes_use_windows_keys_and_exclude_bait(self):
        hosts = support.host_processes("otp23-host", snapshot=PS)
        self.assertEqual([h["ProcessId"] for h in hosts], [610])
        self.assertEqual(hosts[0]["Name"], "otp23-host")
        self.assertEqual(hosts[0]["ParentProcessId"], 500)
        self.assertIn("otp23-host", hosts[0]["CommandLine"])

    def test_unknown_exe_rejected(self):
        with self.assertRaises(AssertionError):
            support.host_processes("other.exe", snapshot=PS)

    def test_ancestry_reaches_chrome_without_cmd_wrapper(self):
        chain = support.ancestry(610, snapshot=PS)
        self.assertEqual([c["ProcessId"] for c in chain], [500, 1])
        self.assertTrue(all({"ProcessId", "Name", "CommandLine"} <= set(c) for c in chain))
        self.assertNotIn("cmd.exe", [c["Name"].lower() for c in chain])


class RegisteredManifest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.binary = self.tmp / "App.app" / PACKAGED_BINARY
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b"")
        self.manifests = self.tmp / "nmh"
        self.manifests.mkdir()

    def write(self, **override):
        doc = {"name": mp.HOST_NAME, "path": str(self.binary), "type": "stdio",
               "allowed_origins": [read_identity()["origin"]]}
        doc.update(override)
        path = self.manifests / f"{mp.HOST_NAME}.json"
        path.write_text(json.dumps(doc))
        return path

    def test_valid_registration(self):
        path = self.write()
        self.assertEqual(support.registered_manifest(self.tmp / "App.app", self.manifests), path)

    def test_wrong_origin_missing_binary_and_unregistered_fail(self):
        with self.assertRaises(AssertionError):  # not registered -> SystemExit converted
            support.registered_manifest(self.tmp / "App.app", self.manifests)
        self.write(allowed_origins=["chrome-extension://zzz/"])
        with self.assertRaises(AssertionError):
            support.registered_manifest(self.tmp / "App.app", self.manifests)
        with self.assertRaises(AssertionError):
            support.registered_manifest(self.tmp / "Missing.app", self.manifests)


class ChromeRunAdapter(unittest.TestCase):
    def test_manifest_forwarded_to_private_profile_install(self):
        with mock.patch.object(mp.ChromeRun, "__init__", return_value=None) as base:
            run = support.ChromeRun({"VELES_OTP23_TRACE": "t"}, medium=True, manifest_source="/m.json")
        base.assert_called_once_with(chrome_path=mp.DEFAULT_CHROME, extra_env={"VELES_OTP23_TRACE": "t"},
                                     manifest_source="/m.json")
        self.assertTrue(run.medium)

    def test_default_manifest_comes_from_registration(self):
        with mock.patch.object(support, "registered_manifest", return_value=pathlib.Path("/reg.json")), \
                mock.patch.object(mp.ChromeRun, "__init__", return_value=None) as base:
            support.ChromeRun({})
        self.assertEqual(base.call_args.kwargs["manifest_source"], pathlib.Path("/reg.json"))

    def test_load_extension_uses_extension_dir(self):
        run = support.ChromeRun.__new__(support.ChromeRun)
        run.cdp = mock.Mock()
        run.cdp.call.return_value = {"id": "x"}
        self.assertEqual(run.load_extension(), {"id": "x"})
        run.cdp.call.assert_called_once_with("Extensions.loadUnpacked", {"path": str(mp.EXT_DIR)})


class DriverSelection(unittest.TestCase):
    def test_poc_background_selects_adapter_by_platform_and_skips_wrapper_on_macos(self):
        src = (HERE / "poc_background.py").read_text(encoding="utf-8")
        ast.parse(src)
        self.assertIn('sys.platform == "darwin"', src)
        self.assertIn("import macos_background_support as cv", src)
        self.assertIn("PLATFORM, WRAPPER = cv.PLATFORM, None", src)
        self.assertIn('PLATFORM, WRAPPER = "windows", "cmd.exe"', src)
        self.assertIn('"platform": PLATFORM', src)
        self.assertIn('"formal_otp23_acceptance": False', src)
        self.assertEqual(support.PLATFORM, "macos")


if __name__ == "__main__":
    unittest.main()
