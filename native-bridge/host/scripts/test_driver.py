#!/usr/bin/env python3
"""Tests for the OTP-24 production lifecycle driver (scripts/accdrv, scripts/lifecycle-driver.py).

Everything runs with fakes on any OS: no Chrome, no phone, no registry, no signing. These tests prove safety
properties of the driver code; they are NOT physical evidence and say nothing about real Windows/macOS behaviour.
"""
import hashlib
import importlib.util
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from accdrv import chrome as achrome, ids, lifecycle, macid, observers, registration as reg, runner  # noqa: E402
from accdrv.errors import Blocked, Refused  # noqa: E402
from accdrv.recorder import ProductFailure, Recorder  # noqa: E402

CASES = {c["id"]: c for c in json.loads((HERE.parent / "acceptance" / "cases.json").read_text())["cases"]}
EXT_ID, ORIGIN = ids.read_extension_identity(runner.EXT_DIR)
RUN_ID = "0123456789ab"
HOST_NAME = reg.host_name_for(RUN_ID)


def load_validator():
    spec = importlib.util.spec_from_file_location("ve", HERE / "validate-evidence.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(os.path.realpath(self._td.name))

    def exe(self, name="veles-native-host", embed=True, where=None):
        d = where or (self.tmp / "bin")
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o755)  # the host's directory chain must not be group-writable, whatever the umask
        p = d / name
        p.write_bytes(b"\x7fELF-dummy " + (ORIGIN.encode() if embed else b"") + b" end")
        p.chmod(0o755)
        return p


# ---- identity ----------------------------------------------------------------------------------------------------------
class IdentityTests(Tmp):
    def test_id_derivation_matches_independent_computation(self):
        key = json.loads((runner.EXT_DIR / "manifest.json").read_text())["key"]
        import base64
        digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
        expected = digest.translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))
        self.assertEqual(EXT_ID, expected)
        self.assertEqual(ORIGIN, f"chrome-extension://{expected}/")

    def test_known_chrome_vector(self):
        # Widely published example: key of the Chrome "hello world" sample derives a known id shape (a-p only).
        self.assertRegex(EXT_ID, r"^[a-p]{32}$")

    def test_bad_keys_refused(self):
        for bad in ("!!!notbase64", "", "QUJD", "AAAA" * 40):
            with self.assertRaises(Refused, msg=bad):
                ids.extension_id_from_key(bad)

    def _ext(self, **over):
        m = json.loads((runner.EXT_DIR / "manifest.json").read_text())
        for k, v in over.items():
            if v is None:
                m.pop(k, None)
            else:
                m[k] = v
        d = self.tmp / f"ext{len(list(self.tmp.iterdir()))}"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps(m))
        return d

    def test_extension_must_be_narrow(self):
        for over in ({"key": None}, {"permissions": ["nativeMessaging", "tabs"]}, {"permissions": []},
                     {"host_permissions": ["<all_urls>"]}, {"externally_connectable": {"matches": ["*://*/*"]}},
                     {"content_scripts": [{"matches": ["*://*/*"], "js": ["x.js"]}]}, {"web_accessible_resources": []}):
            with self.assertRaises(Refused, msg=str(over)):
                ids.read_extension_identity(self._ext(**over))

    def test_extension_files_have_no_trace_trust_or_network_dependency(self):
        text = "".join(p.read_text() for p in runner.EXT_DIR.iterdir() if p.suffix in (".js", ".html", ".json"))
        for token in ("VELES_OTP23_TRACE", "otp23", "spake", "localStorage", "fetch(", "XMLHttpRequest", "new WebSocket", "indexedDB"):
            self.assertNotIn(token, text, token)
        self.assertNotIn("record_base64.", text.replace("record_base64.length", ""))  # contents never read back

    def test_connector_redacts_record_contents(self):
        js = (runner.EXT_DIR / "connector.js").read_text()
        self.assertIn("delete view.record_base64", js)

    def test_origin_embedded_check(self):
        self.assertTrue(ids.binary_embeds_origin(self.exe(), ORIGIN))
        with self.assertRaises(Blocked):
            ids.binary_embeds_origin(self.exe("veles-native-host", embed=False, where=self.tmp / "other"), ORIGIN)
        for bad in ("chrome-extension://*/", ORIGIN + "x", ORIGIN[:-1], "https://example.com/", ""):
            with self.assertRaises(Refused, msg=bad):
                ids.binary_embeds_origin(self.exe(), bad)


# ---- registration ----------------------------------------------------------------------------------------------------
class ManifestTests(Tmp):
    def test_good_manifest_is_exact(self):
        for family, exe in (("posix", "/opt/x/veles-native-host"), ("windows", "C:\\x\\veles-native-host.exe"), ("windows", "C:/x/veles-native-host.exe")):
            m = reg.build_manifest(HOST_NAME, exe, ORIGIN, family)
            self.assertEqual(m["allowed_origins"], [ORIGIN])
            self.assertEqual(m["type"], "stdio")
            self.assertEqual(reg.check_manifest(m, exe, ORIGIN, family), [])

    def test_refusals(self):
        good = dict(name=HOST_NAME, exe="/x/veles-native-host", origin=ORIGIN, family="posix")
        cases = [dict(origin="chrome-extension://*/"), dict(origin="chrome-extension://abc/"), dict(origin=ORIGIN + ",x"),
                 dict(exe="relative/veles-native-host"), dict(exe="/x/otp23-host"), dict(exe="/x/host-test-driver"),
                 dict(exe="/x/../veles-native-host"), dict(exe="/x/./veles-native-host"), dict(name="me.nagaev.veles.otp23.plain"),
                 dict(name="com.google.chrome.example"), dict(name=reg.NAME_PREFIX + "UPPER"), dict(family="windows"),
                 dict(exe="C:\\x\\veles-native-host", family="windows"), dict(exe="\\\\server\\share\\veles-native-host.exe", family="windows"),
                 dict(exe="/x/veles-native-host\x00", family="posix"), dict(family="plan9")]
        for over in cases:
            with self.assertRaises(Refused, msg=str(over)):
                reg.build_manifest(**{**good, **over})

    def test_readback_check_detects_tampering(self):
        exe = "/x/veles-native-host"
        m = reg.build_manifest(HOST_NAME, exe, ORIGIN, "posix")
        for mutate in (lambda d: d.update(allowed_origins=[ORIGIN, "chrome-extension://" + "a" * 32 + "/"]),
                       lambda d: d.update(allowed_origins=["chrome-extension://*/"]), lambda d: d.update(path="/x/other"),
                       lambda d: d.update(type="http"), lambda d: d.update(extra=1), lambda d: d.update(name="x"),
                       lambda d: d.pop("allowed_origins")):
            d = json.loads(json.dumps(m))
            mutate(d)
            self.assertTrue(reg.check_manifest(d, exe, ORIGIN, "posix"), d)
        self.assertTrue(reg.check_manifest([], exe, ORIGIN, "posix"))

    def test_executable_check(self):
        good = self.exe()
        self.assertEqual(reg.check_executable(good, "posix"), good)
        for name in ("otp23-host", "host-test-driver", "veles-native-host-copy"):
            with self.assertRaises(Refused):
                reg.check_executable(self.exe(name), "posix")
        link = self.tmp / "link" / "veles-native-host"
        link.parent.mkdir()
        link.symlink_to(good)
        with self.assertRaises(Refused):
            reg.check_executable(link, "posix")
        with self.assertRaises(Refused):
            reg.check_executable(self.tmp / "missing" / "veles-native-host", "posix")

    def test_write_exclusive_never_overwrites_or_follows(self):
        p = self.tmp / "m.json"
        reg.write_exclusive(p, b"one")
        with self.assertRaises(FileExistsError):
            reg.write_exclusive(p, b"two")
        self.assertEqual(p.read_bytes(), b"one")
        outside = self.tmp / "outside"
        outside.write_text("secret")
        link = self.tmp / "l.json"
        link.symlink_to(outside)
        with self.assertRaises(OSError):
            reg.write_exclusive(link, b"x")
        self.assertEqual(outside.read_text(), "secret")


class ProfileSafetyTests(Tmp):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.root = self.tmp / "run"
        self.root.mkdir()
        self.profile = self.root / "p"
        self.profile.mkdir()

    def test_fresh_profile_accepted(self):
        self.assertEqual(reg.ensure_disposable_profile(self.profile, self.root, self.home), self.profile)

    def test_refusals(self):
        (self.profile / "Default").mkdir()
        with self.assertRaises(Refused):  # not empty: could be a used profile
            reg.ensure_disposable_profile(self.profile, self.root, self.home)
        with self.assertRaises(Refused):  # the run root itself is not "inside" it
            reg.ensure_disposable_profile(self.root, self.root, self.home)
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        with self.assertRaises(Refused):
            reg.ensure_disposable_profile(outside, self.root, self.home)
        link = self.root / "lnk"
        link.symlink_to(outside)
        with self.assertRaises(Refused):
            reg.ensure_disposable_profile(link, self.root, self.home)
        with self.assertRaises(Refused):
            reg.ensure_disposable_profile(self.root / "nope", self.root, self.home)

    def test_personal_chrome_dirs_refused_even_as_run_root(self):
        for d in [reg.default_chrome_dirs(self.home)[0], reg.default_chrome_dirs(self.home)[2]]:
            personal = Path(d)
            personal.mkdir(parents=True)
            profile = personal / "prof"
            profile.mkdir()
            with self.assertRaises(Refused, msg=d):
                reg.ensure_disposable_profile(profile, personal, self.home)

    def test_symlink_into_personal_dir_refused(self):
        personal = Path(reg.default_chrome_dirs(self.home)[0])
        (personal / "x").mkdir(parents=True)
        # a run-root-relative symlink pointing at the personal profile dir
        (self.root / "viaLink").symlink_to(personal / "x")
        with self.assertRaises(Refused):
            reg.ensure_disposable_profile(self.root / "viaLink", self.root, self.home)


class MacRegistrationTests(Tmp):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.sysroot = self.tmp / "sys"
        self.dirs = reg.native_messaging_dirs(self.home, self.sysroot)
        for d in self.dirs[:2]:
            Path(d).mkdir(parents=True)
        self.personal = Path(self.dirs[0])
        (self.personal / "personal.host.json").write_text('{"x":1}')
        (self.personal / "nested").mkdir()
        (self.personal / "nested" / "deep.json").write_text("1")
        self.chrome_root = self.personal.parent
        (self.chrome_root / "Local State").write_text("personal browser state")
        self.manifest = reg.build_manifest(HOST_NAME, "/x/veles-native-host", ORIGIN, "posix")
        self.snap = lambda: reg.snapshot_native_hosts(self.dirs)

    def test_snapshot_targets_only_the_real_manifest_dirs(self):
        self.assertTrue(all(d.endswith(("NativeMessagingHosts", "native-messaging-hosts")) for d in self.dirs))
        snap = self.snap()
        self.assertEqual(set(snap), set(self.dirs))
        self.assertIn("nested/deep.json", snap[str(self.personal)])  # recursive
        flat = json.dumps(snap)
        self.assertNotIn("Local State", flat)
        before = self.snap()
        (self.chrome_root / "Local State").write_text("changed by the user's own Chrome")
        (self.chrome_root / "Default").mkdir()
        self.assertEqual(self.snap(), before, "unrelated personal Chrome files must not influence or enter the snapshot")

    def test_evidence_description_has_no_personal_listing(self):
        d = reg.describe_snapshot(self.snap())
        self.assertNotIn("personal.host.json", json.dumps(d))
        self.assertEqual(d["entry_counts"][str(self.personal)], 3)  # personal.host.json, nested/, nested/deep.json
        self.assertRegex(d["snapshot_sha256"], r"^[0-9a-f]{64}$")

    def test_manifest_lands_only_in_profile_and_globals_untouched(self):
        profile = self.tmp / "prof"
        profile.mkdir()
        r = reg.ProfileManifestRegistration(HOST_NAME, self.manifest, self.snap)
        info = r.register()
        self.assertNotIn("personal.host.json", json.dumps(info))
        placed = Path(r.place(profile))
        self.assertEqual(placed.parent.parent, profile)
        self.assertEqual(json.loads(placed.read_bytes()), self.manifest)
        self.assertTrue(r.unregister()["globals_unchanged"])

    def test_each_kind_of_global_change_is_detected(self):
        changes = {
            "added top-level": lambda: (self.personal / f"{HOST_NAME}.json").write_text("{}"),
            "changed content": lambda: (self.personal / "personal.host.json").write_text('{"x":2}'),
            "removed": lambda: (self.personal / "personal.host.json").unlink(),
            "nested change": lambda: (self.personal / "nested" / "deep.json").write_text("2"),
            "nested add": lambda: (self.personal / "nested" / "new.json").write_text("2"),
            "system dir created": lambda: Path(self.dirs[2]).mkdir(parents=True),
            "system manifest": lambda: (Path(self.dirs[1]) / "sys.json").write_text("{}"),
            "symlink planted": lambda: (self.personal / "lnk").symlink_to("/etc/passwd"),
        }
        for label, mutate in changes.items():
            r = reg.ProfileManifestRegistration(HOST_NAME, self.manifest, self.snap)
            r.register()
            mutate()
            with self.assertRaises(reg.RestoreFailed, msg=label):
                r.unregister()
            # reset for the next case
            for p in list(self.personal.iterdir()):
                if p.name in (f"{HOST_NAME}.json", "lnk"):
                    p.unlink()
            (self.personal / "personal.host.json").write_text('{"x":1}')
            (self.personal / "nested" / "deep.json").write_text("1")
            nn = self.personal / "nested" / "new.json"
            if nn.exists():
                nn.unlink()
            (Path(self.dirs[1]) / "sys.json").unlink(missing_ok=True)
            if Path(self.dirs[2]).exists():
                Path(self.dirs[2]).rmdir()

    def test_failure_message_names_entries_but_never_suggests_windows_recovery(self):
        r = reg.ProfileManifestRegistration(HOST_NAME, self.manifest, self.snap)
        r.register()
        (self.personal / f"{HOST_NAME}.json").write_text("{}")
        with self.assertRaises(reg.RestoreFailed) as cm:
            r.unregister()
        self.assertIn(f"{HOST_NAME}.json: added", str(cm.exception))
        self.assertEqual(r.recovery_hint, "")

    def test_place_refuses_existing_directory(self):
        profile = self.tmp / "prof"
        (profile / "NativeMessagingHosts").mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            reg.ProfileManifestRegistration(HOST_NAME, self.manifest, self.snap).place(profile)


class FakeRegistry:
    """In-memory HKCU. Strict: a key path must be exactly <WIN_PARENT>\\<single name>; anything else raises, so a
    tampered/nested name cannot hide behind path flattening. `events` records calls so ordering can be asserted."""

    def __init__(self, existing=None, journal=None):
        self.keys = dict(existing or {})
        self.events = []
        self.journal = journal
        self.fail = {}  # method -> exception to raise
        self.readback = None  # value get_default reports for newly created keys (None = truthful)

    def _k(self, key):
        parent, _, name = key.rpartition("\\")
        if parent != reg.WIN_PARENT or not name or "\\" in name:
            raise AssertionError(f"registry path outside the Chrome NativeMessagingHosts parent: {key!r}")
        return name

    def _maybe_fail(self, method):
        if method in self.fail:
            raise self.fail[method]

    def subkeys(self, parent):
        assert parent == reg.WIN_PARENT
        self.events.append(("subkeys", parent))
        self._maybe_fail("subkeys")
        return sorted(self.keys)

    def exists(self, key):
        self._maybe_fail("exists")
        return self._k(key) in self.keys

    def create(self, key, default):
        self.events.append(("create", key, self.journal.exists() if self.journal else None))
        self._maybe_fail("create")
        self.keys[self._k(key)] = default

    def get_default(self, key):
        self._maybe_fail("get_default")
        if self.readback is not None and self._k(key) not in ("Other.Host",):
            return self.readback
        return self.keys[self._k(key)]

    def delete(self, key):
        self.events.append(("delete", key))
        self._maybe_fail("delete")
        del self.keys[self._k(key)]


class WindowsRegistrationTests(Tmp):
    def make(self, existing=None, name=HOST_NAME):
        self.state = self.tmp / "state"
        self.state.mkdir(exist_ok=True)
        self.journal = self.state / reg.JOURNAL_NAME
        self.backend = FakeRegistry({"Other.Host": "C:\\other.json", **(existing or {})}, self.journal)
        return reg.WindowsRegistration(self.backend, self.journal, name, "C:\\run\\m.json", b"{}", work_dir=self.tmp / "w")

    def test_register_then_restore_leaves_registry_identical(self):
        before = dict(self.make().b.keys)
        r = self.make()
        r.register()
        self.assertIn(HOST_NAME, self.backend.keys)
        self.assertEqual(self.backend.keys["Other.Host"], "C:\\other.json")
        r.unregister()
        self.assertEqual(self.backend.keys, before)
        self.assertFalse(self.journal.exists())

    def test_journal_is_durable_before_the_first_mutation(self):
        r = self.make()
        r.register()
        create = next(e for e in self.backend.events if e[0] == "create")
        self.assertTrue(create[2], "journal must exist when the key is created")
        self.assertEqual(json.loads(self.journal.read_text())["state"], "registered")

    def test_existing_key_is_never_overwritten_or_deleted(self):
        r = self.make({HOST_NAME: "C:\\someone-elses.json"})
        with self.assertRaises(reg.Refused):
            r.register()
        self.assertEqual(self.backend.keys[HOST_NAME], "C:\\someone-elses.json")
        self.assertFalse([e for e in self.backend.events if e[0] in ("create", "delete")])
        self.assertFalse(r.registered)
        self.assertFalse(self.journal.exists())

    def test_leftover_journal_blocks_a_new_registration(self):
        r = self.make()
        self.journal.write_text("{}")
        with self.assertRaises(Refused):
            r.register()
        self.assertNotIn(HOST_NAME, self.backend.keys)
        self.assertFalse(r.registered)

    def test_strict_acceptance_names_only(self):
        for bad in ("me.nagaev.veles.otp23.plain", "com.google.chrome.example", "Other.Host", "",
                    reg.NAME_PREFIX + "UPPERCASE00", reg.NAME_PREFIX + "0123456789a", reg.NAME_PREFIX + "0123456789abc",
                    reg.NAME_PREFIX + "0123456789ab\\child", reg.NAME_PREFIX + "0123456789ab.x", reg.NAME_PREFIX + "../x", reg.NAME_PREFIX):
            with self.assertRaises(Refused, msg=bad):
                reg.WindowsRegistration(FakeRegistry(), self.tmp / "j", bad, "C:\\m.json")

    def test_value_changed_by_someone_else_is_not_deleted(self):
        r = self.make()
        r.register()
        self.backend.keys[HOST_NAME] = "C:\\evil.json"
        with self.assertRaises(reg.RestoreFailed):
            r.unregister()
        self.assertIn(HOST_NAME, self.backend.keys)
        self.assertTrue(self.journal.exists(), "journal retained so recovery stays possible")

    def test_unrelated_parent_changes_do_not_wedge_recovery_and_are_reported(self):
        r = self.make()
        r.register()
        self.backend.keys["Appeared.During"] = "x"
        del self.backend.keys["Other.Host"]
        out = r.unregister()
        self.assertTrue(out["restored"])
        self.assertNotIn(HOST_NAME, self.backend.keys)
        self.assertIn("Appeared.During", self.backend.keys)  # never touched
        self.assertEqual(out["other_parent_changes_reported_not_touched"], {"added": ["Appeared.During"], "removed": ["Other.Host"]})
        self.assertFalse(self.journal.exists())

    def test_recovery_after_crash_restores(self):
        r = self.make()
        r.register()  # simulated crash: no unregister
        reg.restore(self.backend, self.journal)
        self.assertEqual(self.backend.keys, {"Other.Host": "C:\\other.json"})

    def test_recovery_after_crash_before_create(self):
        r = self.make()
        r.before = self.backend.subkeys(reg.WIN_PARENT)
        r._write_journal("pending")
        out = reg.restore(self.backend, self.journal)  # key never created: nothing to delete
        self.assertTrue(out["restored"])
        self.assertFalse([e for e in self.backend.events if e[0] == "delete"])

    def test_readback_mismatch_marks_registered_and_is_cleaned_up(self):
        r = self.make()
        self.backend.readback = "C:\\something-else.json"
        with self.assertRaises(Refused):
            r.register()
        self.assertTrue(r.registered, "something was created: cleanup must run")
        self.assertEqual(json.loads(self.journal.read_text())["state"], "mismatch")
        self.backend.readback = None
        self.backend.keys[HOST_NAME] = "C:\\something-else.json"  # what the (misbehaving) store actually holds
        out = r.unregister()
        self.assertTrue(out["restored"])
        self.assertNotIn(HOST_NAME, self.backend.keys)

    def test_registry_errors_are_wrapped_not_raw(self):
        for method, exc in (("create", PermissionError("denied")), ("get_default", OSError("io"))):
            r = self.make()
            self.backend.fail[method] = exc
            with self.assertRaises(Refused, msg=method):
                r.register()
            self.assertTrue(r.registered, method)  # journal was written first
            self.backend.fail.clear()
            self.backend.keys.pop(HOST_NAME, None)
            reg.restore(self.backend, self.journal)
        r = self.make()
        self.backend.fail["subkeys"] = PermissionError("denied")
        with self.assertRaises(Refused):
            r.register()
        self.assertFalse(r.registered, "nothing mutated when the pre-run read fails")
        r = self.make()
        r.register()
        self.backend.fail["delete"] = PermissionError("denied")
        with self.assertRaises(reg.RestoreFailed):
            r.unregister()
        self.assertTrue(self.journal.exists())

    def test_half_created_key_without_value_is_ours_and_deleted(self):
        r = self.make()
        r.register()
        self.backend.keys[HOST_NAME] = None  # created but value missing
        j = json.loads(self.journal.read_text())
        j["state"] = "pending"
        self.journal.write_text(json.dumps(j))
        self.assertTrue(reg.restore(self.backend, self.journal)["restored"])
        self.assertNotIn(HOST_NAME, self.backend.keys)

    def test_tampered_journals_are_refused_and_delete_nothing(self):
        r = self.make()
        r.register()
        good = json.loads(self.journal.read_text())
        variants = [
            {**good, "key": reg.WIN_PARENT + "\\Other.Host", "name": "Other.Host"},
            {**good, "key": "Software\\Microsoft\\Windows\\CurrentVersion\\Run\\x"},
            {**good, "key": reg.WIN_PARENT + "\\..\\..\\Run\\" + HOST_NAME},
            {**good, "name": HOST_NAME + "\\child", "key": reg.WIN_PARENT + "\\" + HOST_NAME + "\\child"},
            {**good, "subkeys_before": good["subkeys_before"] + [HOST_NAME]},
            {**good, "subkeys_before": None}, {**good, "schema": "other"}, [1, 2], "str",
        ]
        for v in variants:
            self.journal.write_text(json.dumps(v))
            with self.assertRaises(reg.RestoreFailed, msg=str(v)[:80]):
                reg.restore(self.backend, self.journal)
            self.assertIn("Other.Host", self.backend.keys)
            self.assertIn(HOST_NAME, self.backend.keys)
        self.journal.write_text("not json")
        with self.assertRaises(reg.RestoreFailed):
            reg.restore(self.backend, self.journal)

    def test_real_backend_blocks_off_windows_and_is_hkcu_only(self):
        if sys.platform != "win32":
            with self.assertRaises(Blocked):
                reg.WinregBackend()
        src = (HERE / "accdrv" / "registration.py").read_text()
        self.assertNotIn("HKEY_LOCAL_MACHINE", src)
        self.assertIsNone(re.search(r"\bHKLM\b", src))


class StateLockRecoveryTests(Tmp):
    def setUp(self):
        super().setUp()
        self.state = reg.ensure_state_dir(self.tmp / "state")
        self.backend = FakeRegistry({"Other.Host": "C:\\other.json"})
        self.temp_root = self.tmp / "tmproot"
        self.temp_root.mkdir()

    def reg_obj(self, work=None):
        self.backend.journal = self.state / reg.JOURNAL_NAME
        return reg.WindowsRegistration(self.backend, self.state / reg.JOURNAL_NAME, HOST_NAME, "C:\\m.json", b"{}", work_dir=work)

    def test_state_dir_is_fixed_per_user_and_validated(self):
        self.assertEqual(reg.state_dir_for({"LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local"}).name, "Otp24Acceptance")
        for env in ({}, {"LOCALAPPDATA": ""}, {"LOCALAPPDATA": "relative\\x"}, {"LOCALAPPDATA": "\\\\server\\share"}):
            with self.assertRaises(Blocked, msg=str(env)):
                reg.state_dir_for(env)
        link = self.tmp / "lnk"
        link.symlink_to(self.state)
        with self.assertRaises(Refused):
            reg.ensure_state_dir(link)
        loose = self.tmp / "loose"
        loose.mkdir(mode=0o777)
        loose.chmod(0o777)
        with self.assertRaises(Refused):
            reg.ensure_state_dir(loose)

    def test_lock_is_exclusive_and_released_only_if_ours(self):
        a, b = reg.RunLock(self.state, token="aaaa"), reg.RunLock(self.state, token="bbbb")
        a.acquire()
        with self.assertRaises(Refused) as cm:
            b.acquire()
        self.assertIn("aaaa", str(cm.exception))
        b.release()  # never held: must not remove a's lock
        self.assertTrue((self.state / reg.LOCK_NAME).exists())
        (self.state / reg.LOCK_NAME).write_text(json.dumps({"token": "someone-else"}))
        a.release()  # token no longer ours: leave it
        self.assertTrue((self.state / reg.LOCK_NAME).exists())

    def test_lock_race_exactly_one_winner(self):
        wins, barrier = [], threading.Barrier(8)

        def go(i):
            lock = reg.RunLock(self.state, token=f"t{i}")
            barrier.wait()
            try:
                lock.acquire()
                wins.append(i)
            except Refused:
                pass
        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(wins), 1)

    def test_crash_leftovers_block_the_next_run_until_recovered(self):
        lock = reg.RunLock(self.state)
        lock.acquire()
        r = self.reg_obj()
        r.register()  # ... hard kill here: lock, journal and key remain
        with self.assertRaises(Refused):
            reg.RunLock(self.state).acquire()
        with self.assertRaises(Refused):
            reg.startup_check(self.backend, self.state)  # journal
        (self.state / reg.JOURNAL_NAME).rename(self.tmp / "moved-journal")
        with self.assertRaises(Refused) as cm:
            reg.startup_check(self.backend, self.state)  # leftover key alone is also refused
        self.assertIn(HOST_NAME, str(cm.exception))
        (self.tmp / "moved-journal").rename(self.state / reg.JOURNAL_NAME)
        with self.assertRaises(Refused):  # explicit confirmation is mandatory
            reg.recover(self.backend, self.state, self.temp_root)
        self.assertIn(HOST_NAME, self.backend.keys)
        out = reg.recover(self.backend, self.state, self.temp_root, confirm_no_active_run=True)
        self.assertTrue(out["restored"] and out["lock_cleared"])
        self.assertEqual(self.backend.keys, {"Other.Host": "C:\\other.json"})
        reg.startup_check(self.backend, self.state)  # clean again
        reg.RunLock(self.state).acquire()

    def test_recovery_is_scoped_to_the_journalled_key_only(self):
        other = reg.NAME_PREFIX + "ffffffffffff"
        self.backend.keys[other] = "C:\\a-different-run.json"
        r = self.reg_obj()
        r.register()
        reg.recover(self.backend, self.state, self.temp_root, confirm_no_active_run=True)
        self.assertNotIn(HOST_NAME, self.backend.keys)
        self.assertEqual(self.backend.keys[other], "C:\\a-different-run.json")
        self.assertIn("Other.Host", self.backend.keys)

    def test_stale_key_without_journal_is_reported_not_deleted(self):
        self.backend.keys[HOST_NAME] = "C:\\x.json"
        with self.assertRaises(reg.RestoreFailed):
            reg.recover(self.backend, self.state, self.temp_root, confirm_no_active_run=True)
        self.assertIn(HOST_NAME, self.backend.keys)

    def test_lock_only_leftover_is_cleared_when_nothing_of_ours_exists(self):
        reg.RunLock(self.state).acquire()
        out = reg.recover(self.backend, self.state, self.temp_root, confirm_no_active_run=True)
        self.assertTrue(out["lock_cleared"])
        self.assertFalse((self.state / reg.LOCK_NAME).exists())

    def test_recovery_removes_only_a_proper_leftover_work_dir(self):
        good = self.temp_root / (reg.WORK_PREFIX + "abc")
        good.mkdir()
        (good / "m.json").write_text("{}")
        r = self.reg_obj(work=good)
        r.register()
        out = reg.recover(self.backend, self.state, self.temp_root, confirm_no_active_run=True)
        self.assertTrue(out["work_dir_removed"])
        self.assertFalse(good.exists())
        precious = self.tmp / "precious"
        precious.mkdir()
        for bad in (precious, self.temp_root / "not-ours", self.temp_root, None, "", self.tmp / "nonexistent"):
            self.assertFalse(reg.remove_work_dir(bad, self.temp_root), bad)
        sneaky = self.temp_root / (reg.WORK_PREFIX + "link")
        sneaky.symlink_to(precious)
        self.assertFalse(reg.remove_work_dir(sneaky, self.temp_root))
        wrong_parent = self.tmp / (reg.WORK_PREFIX + "x")
        wrong_parent.mkdir()
        self.assertFalse(reg.remove_work_dir(wrong_parent, self.temp_root))
        self.assertTrue(precious.exists() and wrong_parent.exists())

    def test_backend_errors_in_recovery_are_structured(self):
        r = self.reg_obj()
        r.register()
        self.backend.fail["subkeys"] = PermissionError("denied")
        with self.assertRaises(reg.RestoreFailed):
            reg.recover(self.backend, self.state, self.temp_root, confirm_no_active_run=True)
        self.assertTrue((self.state / reg.JOURNAL_NAME).exists())

    def test_journal_dir_fsync_is_attempted_on_posix(self):
        from unittest import mock
        if not hasattr(os, "O_DIRECTORY") or sys.platform == "win32":
            self.skipTest("POSIX only")
        with mock.patch.object(os, "fsync", wraps=os.fsync) as f:
            self.reg_obj().register()
        self.assertGreaterEqual(f.call_count, 3)  # tmp file(s) and the directory


# ---- chrome ----------------------------------------------------------------------------------------------------------
class FakeProc:
    def __init__(self, exits_at=None):
        self.killed, self.waited, self.rc = False, 0, None
        self.exits_at = exits_at

    def poll(self):
        return self.rc

    def kill(self):
        self.killed, self.rc = True, -9

    def wait(self, timeout=None):
        self.waited += 1
        self.rc = self.rc if self.rc is not None else 0
        return self.rc


class FakeCdp:
    def __init__(self, fail_close=False):
        self.calls, self.fail_close = [], fail_close

    def call(self, method, params=None, session=None, timeout=60):
        self.calls.append((method, params, session))
        if method == "Browser.getVersion":
            return {"product": "Chrome/130.0.1"}
        if method == "Browser.close" and self.fail_close:
            raise ConnectionError("socket dropped")
        if method == "Extensions.loadUnpacked":
            return {"id": EXT_ID}
        if method == "Target.createTarget":
            return {"targetId": "T1"}
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        return {}


class ChromeTests(Tmp):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "run"
        self.root.mkdir()
        self.home = self.tmp / "home"
        self.chrome_exe = self.tmp / "chrome"
        self.chrome_exe.write_bytes(b"chrome")
        self.popen_calls = []

    def session(self, publish=True, proc=None, **kw):
        proc = proc or FakeProc()
        profile_holder = {}

        def popen(args, env=None):
            self.popen_calls.append((args, env))
            prof = Path(args[1].split("=", 1)[1])
            profile_holder["p"] = prof
            if publish:
                (prof / "DevToolsActivePort").write_text("9222\n/devtools/browser/x\n")
            return proc

        self.cdp = FakeCdp(kw.pop("fail_close", False))
        s = achrome.ChromeSession(self.chrome_exe, self.root, self.home, kw.pop("env", {"PATH": "/bin"}), popen=popen,
                                  make_cdp=lambda port, path: self.cdp, sleep=lambda s: None, polls=3, **kw)
        s.proc_fake = proc
        return s

    def test_launch_uses_only_the_fresh_profile_and_clean_env(self):
        s = self.session(env={"PATH": "/bin", "VELES_OTP23_TRACE": "/tmp/t", "VELES_X": "1", "HOME": "/h"})
        s.start()
        args, env = self.popen_calls[0]
        self.assertEqual(args[1], f"--user-data-dir={self.root / 'chrome-profile'}")
        self.assertEqual(sum(a.startswith("--user-data-dir") for a in args), 1)
        self.assertEqual(env, {"PATH": "/bin", "HOME": "/h"})
        for d in reg.default_chrome_dirs(self.home):
            self.assertFalse(any(d in a for a in args))
        prov = s.provenance()
        self.assertTrue(prov["profile"]["disposable"])
        self.assertFalse(prov["profile"]["is_default_profile"])
        self.assertEqual(prov["sha256"], hashlib.sha256(b"chrome").hexdigest())

    def test_before_launch_runs_after_profile_exists_and_before_chrome(self):
        order = []
        s = self.session(before_launch=lambda p: order.append(("hook", Path(p).is_dir(), len(self.popen_calls))))
        s.start()
        self.assertEqual(order, [("hook", True, 0)])

    def test_refuses_relative_missing_or_symlinked_chrome(self):
        link = self.tmp / "chromelink"
        link.symlink_to(self.chrome_exe)
        for bad in ("chrome", self.tmp / "nochrome", link):
            with self.assertRaises(Refused, msg=str(bad)):
                achrome.ChromeSession(bad, self.root, self.home, {})

    def test_existing_profile_dir_blocks_reuse(self):
        (self.root / "chrome-profile").mkdir()
        with self.assertRaises(FileExistsError):
            self.session().start()

    def test_no_port_file_blocks_and_kills(self):
        s = self.session(publish=False)
        with self.assertRaises(Blocked):
            s.start()
        self.assertTrue(s.proc_fake.killed)

    def test_chrome_dying_early_blocks(self):
        p = FakeProc()
        p.rc = 1
        s = self.session(publish=False, proc=p)
        with self.assertRaises(Blocked):
            s.start()

    def test_dispose_removes_only_its_own_profile_even_if_close_fails(self):
        s = self.session(fail_close=True)
        s.start()
        (s.profile / "x").write_text("1")
        other = self.root / "keep"
        other.mkdir()
        s.dispose()
        self.assertFalse(s.profile.exists())
        self.assertTrue(other.exists())

    def test_dispose_refuses_to_delete_a_foreign_path(self):
        s = self.session()
        s.start()
        precious = self.tmp / "precious"
        precious.mkdir()
        s.profile = precious
        s.dispose(rmtree=lambda p: self.fail("must not delete a path that is not this run's chrome-profile"))
        self.assertTrue(precious.exists())

    def test_dispose_kills_a_chrome_that_will_not_exit(self):
        class Stubborn(FakeProc):
            def wait(self, timeout=None):
                self.waited += 1
                if not self.killed:
                    raise subprocess.TimeoutExpired("chrome", timeout)
                return -9
        s = self.session(proc=Stubborn())
        s.start()
        s.dispose()
        self.assertTrue(s.proc_fake.killed)

    def test_js_exception_surfaces(self):
        s = self.session()
        s.start()
        s.cdp = type("C", (), {"call": lambda self, *a, **k: {"exceptionDetails": {"text": "boom"}}})()
        with self.assertRaises(RuntimeError):
            s.js("S", "1")

    def test_trigger_primitives(self):
        s = self.session()
        s.start()
        s.close_tab("T1")
        s.browser_close()
        methods = [c[0] for c in self.cdp.calls]
        self.assertIn("Target.closeTarget", methods)
        self.assertIn("Browser.close", methods)

    def test_clean_env_and_args_are_loopback_debug_only(self):
        self.assertEqual(achrome.clean_env({"A": "1", "VELES_Y": "2"}), {"A": "1"})
        args = achrome.launch_args("/c", "/p")
        self.assertIn("--remote-debugging-port=0", args)
        self.assertNotIn("--remote-debugging-address=0.0.0.0", args)
        self.assertFalse([a for a in args if "profile-directory" in a or "load-extension" in a])


class CdpTests(unittest.TestCase):
    class Ws:
        def __init__(self, replies):
            self.replies, self.sent = list(replies), []

        def send(self, t):
            self.sent.append(json.loads(t))

        def recv(self, timeout):
            return json.dumps(self.replies.pop(0))

    def test_interleaved_events_are_kept_and_reply_matched(self):
        c = achrome.Cdp(self.Ws([{"method": "x"}, {"id": 1, "result": {"ok": 1}}]))
        self.assertEqual(c.call("A"), {"ok": 1})
        self.assertEqual(c.events, [{"method": "x"}])

    def test_error_raises(self):
        c = achrome.Cdp(self.Ws([{"id": 1, "error": {"message": "no"}}]))
        with self.assertRaises(RuntimeError):
            c.call("A")

    def test_timeout(self):
        t = iter([0, 0, 100, 100, 100])
        c = achrome.Cdp(self.Ws([{"method": "e"}] * 5), clock=lambda: next(t))
        with self.assertRaises(TimeoutError):
            c.call("A", timeout=5)

    def test_non_loopback_refused(self):
        for host in ("example.com", "0.0.0.0", "192.168.1.5", ""):
            with self.assertRaises(Refused):
                achrome.WebSocket(host, 1, "/x", connect=lambda *a, **k: self.fail("must not connect"))

    def test_real_loopback_handshake_and_framing(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        got = {}

        def serve():
            conn, _ = srv.accept()
            head = b""
            while b"\r\n\r\n" not in head:
                head += conn.recv(4096)
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\n\r\n")
            hdr = conn.recv(2)
            length = hdr[1] & 0x7F
            data = b""
            if length == 126:
                length = struct.unpack(">H", conn.recv(2))[0]
            mask = conn.recv(4)
            while len(data) < length:
                data += conn.recv(length - len(data))
            got["text"] = bytes(b ^ mask[i % 4] for i, b in enumerate(data)).decode()
            payload = b'{"id": 1, "result": {"v": "' + b"y" * 200 + b'"}}'
            conn.sendall(b"\x89\x00" + b"\x81" + bytes([126]) + struct.pack(">H", len(payload)) + payload)  # ping then big text
            conn.close()

        threading.Thread(target=serve, daemon=True).start()
        ws = achrome.WebSocket("127.0.0.1", port, "/devtools/browser/x")
        cdp = achrome.Cdp(ws)
        self.assertEqual(cdp.call("Big.method", {"a": "b" * 300})["v"], "y" * 200)
        self.assertIn("Big.method", got["text"])
        ws.close()
        srv.close()


# ---- signing identity ------------------------------------------------------------------------------------------------
GOOD_CODESIGN = "\n".join(["Executable=/x", f"Identifier={macid.APPROVED_BUNDLE_ID}", "Format=bundle with Mach-O thin (arm64)",
                           "Signature size=4800", f"Authority={macid.APPROVED_AUTHORITY}",
                           "Authority=Apple Worldwide Developer Relations Certification Authority", "Authority=Apple Root CA",
                           f"TeamIdentifier={macid.APPROVED_TEAM_ID}"])
BUNDLE = "/Users/u/out/VelesOtp23PoC.app/Contents/MacOS/veles-native-host"


class MacIdentityTests(Tmp):
    def runner_for(self, dvv=GOOD_CODESIGN, verify_rc=0, extract=True, dvv_rc=0):
        calls = []

        def run(argv):
            calls.append(argv)
            if argv[:2] == ["codesign", "-dvv"]:
                return dvv_rc, "", dvv
            if "--verify" in argv:
                return verify_rc, "", ""
            if argv[:2] == ["codesign", "-d"]:
                if extract:
                    base = argv[2].split("=", 1)[1]
                    Path(base + "0").write_bytes(b"LEAF-CERT-DER")
                return 0, "", ""
            self.fail(f"unexpected command {argv}")
        run.calls = calls
        return run

    def test_good_identity_is_read_not_assumed(self):
        run = self.runner_for()
        s = macid.read_signing(BUNDLE, run, self.tmp / "certs")
        self.assertEqual(s["cert_sha256"], hashlib.sha256(b"LEAF-CERT-DER").hexdigest())
        self.assertIs(s["production_identity_claimed"], False)
        self.assertEqual(s["bundle_kind"], "test_vehicle")
        for argv in run.calls:  # read-only tools only
            self.assertEqual(argv[0], "codesign")
            self.assertNotIn("-s", argv)
            self.assertNotIn("--sign", argv)

    def test_every_deviation_is_refused(self):
        bad = {
            "adhoc": GOOD_CODESIGN.replace("Signature size=4800", "Signature=adhoc"),
            "other bundle": GOOD_CODESIGN.replace(macid.APPROVED_BUNDLE_ID, "me.nagaev.veles.bridge"),
            "other team": GOOD_CODESIGN.replace(macid.APPROVED_TEAM_ID, "AAAAAAAAAA"),
            "other leaf": GOOD_CODESIGN.replace(macid.APPROVED_AUTHORITY, "Apple Development: someone@else (XXXX)"),
            "no root": GOOD_CODESIGN.replace("Authority=Apple Root CA", "Authority=Self"),
            "lookalike line": GOOD_CODESIGN.replace(f"Identifier={macid.APPROVED_BUNDLE_ID}", f"Identifier={macid.APPROVED_BUNDLE_ID}.x"),
            "leaf second": GOOD_CODESIGN.replace(f"Authority={macid.APPROVED_AUTHORITY}\n", "Authority=Evil\n" + f"Authority={macid.APPROVED_AUTHORITY}\n", 1),
        }
        for label, text in bad.items():
            with self.assertRaises(Refused, msg=label):
                macid.read_signing(BUNDLE, self.runner_for(dvv=text), self.tmp / f"c-{label[:3]}")

    def test_anchor_failure_unreadable_and_missing_cert(self):
        with self.assertRaises(Refused):
            macid.read_signing(BUNDLE, self.runner_for(verify_rc=3), self.tmp / "a")
        with self.assertRaises(Blocked):
            macid.read_signing(BUNDLE, self.runner_for(dvv_rc=1), self.tmp / "b")
        with self.assertRaises(Blocked):
            macid.read_signing(BUNDLE, self.runner_for(extract=False), self.tmp / "c")

    def test_executable_must_be_in_the_approved_bundle(self):
        for bad in ("/tmp/veles-native-host", "/x/Other.app/Contents/MacOS/veles-native-host", "/x/VelesOtp23PoC.app/Contents/Resources/veles-native-host",
                    "/x/VelesOtp23PoC.app/Contents/MacOS/otp23-host"):
            with self.assertRaises(Refused, msg=bad):
                macid.read_signing(bad, self.runner_for(), self.tmp / "z")

    def test_no_signing_or_keychain_operations_in_sources(self):
        src = "".join(p.read_text() for p in (HERE / "accdrv").glob("*.py") if p.name != "rig.py") + (HERE / "lifecycle-driver.py").read_text()
        for token in ("--sign", "codesign\", \"-s", "security import", "security delete", "tccutil", "find-identity", "delete-identity", "export-key",
                      "shell=True", "VELES_OTP23_TRACE", "otp23-host", "rm -rf", "HKEY_LOCAL_MACHINE"):
            self.assertNotIn(token, src, token)
        # rig.py is the ONLY module that may speak of adb: a read-only allowlist enforced and tested in test_rig.py.
        self.assertIsNone(re.search(r"\badb\b", src), "no adb/phone control belongs outside accdrv/rig.py")
        rig_src = (HERE / "accdrv" / "rig.py").read_text()
        for token in ("--sign", "security import", "tccutil", "shell=True", "HKEY_LOCAL_MACHINE"):
            self.assertNotIn(token, rig_src, token)
        self.assertIsNone(re.search(r"\bHKLM\b", src), "machine-wide registry hive must never be referenced")

    def test_authenticode_quoting_refused(self):
        with self.assertRaises(Refused):
            macid.windows_authenticode("C:\\x'; calc; '\\veles-native-host.exe", lambda a: (0, "Valid", ""))
        self.assertIn("Valid", macid.windows_authenticode("C:\\x\\veles-native-host.exe", lambda a: (0, "Valid\n", "")))
        with self.assertRaises(Blocked):
            macid.windows_authenticode("C:\\x\\h.exe", lambda a: (1, "", "err"))
        with self.assertRaises(Blocked):
            macid.windows_authenticode("C:\\x\\h.exe", lambda a: (0, "Valid; rm", ""))


# ---- observers -------------------------------------------------------------------------------------------------------
class ObserverTests(unittest.TestCase):
    PS = ("  100     1 Mon Nov  1 10:00:00 2026 /sbin/launchd\n"
          "  200   100 Mon Nov  1 10:05:01 2026 /opt/run/veles-native-host chrome-extension://abc/\n"
          "  300   100 Mon Nov  1 10:06:00 2026 /tmp/evil/veles-native-host x\n"
          "  400   100 Mon Nov  1 10:07:00 2026 /Users/u/My Out/VelesOtp23PoC.app/Contents/MacOS/veles-native-host chrome-extension://abc/\n"
          " garbage line\n")

    def test_parse_ps_skips_garbage_and_keeps_start_time(self):
        rows = observers.parse_ps(self.PS)
        self.assertEqual([r["pid"] for r in rows], [100, 200, 300, 400])
        self.assertEqual(rows[1]["start"], "Mon Nov 1 10:05:01 2026")

    def test_host_processes_match_exact_path_not_name_even_with_spaces(self):
        o = observers.Observer("macos", lambda a: (0, self.PS, ""))
        self.assertEqual([r["pid"] for r in o.host_processes("/opt/run/veles-native-host")], [200])
        self.assertEqual(o.host_processes("/opt/run/veles-native-hos"), [])
        self.assertEqual(o.host_processes("/opt/run"), [])
        spaced = "/Users/u/My Out/VelesOtp23PoC.app/Contents/MacOS/veles-native-host"
        self.assertEqual([r["pid"] for r in o.host_processes(spaced)], [400])
        self.assertEqual(o.host_processes("/Users/u/My"), [])

    def test_alive_requires_the_same_process_not_just_the_pid(self):
        o = observers.Observer("macos", lambda a: (0, self.PS, ""))
        row = o.host_processes("/opt/run/veles-native-host")[0]
        self.assertTrue(o.alive(row))
        self.assertTrue(o.alive(200))
        self.assertFalse(o.alive({**row, "start": "Mon Nov 1 09:00:00 2026"}), "a recycled pid has a different start time")
        self.assertFalse(o.alive({**row, "command": "/bin/other"}))
        self.assertFalse(o.alive({**row, "pid": 999}))

    def test_windows_matching_is_case_and_slash_insensitive_but_exact(self):
        rows = json.dumps([{"ProcessId": 5, "ParentProcessId": 4, "CommandLine": "x", "ExecutablePath": "C:\\Run\\VELES-native-host.exe", "CreationDate": "d1"},
                           {"ProcessId": 6, "ParentProcessId": 4, "CommandLine": "x", "ExecutablePath": "C:\\Evil\\veles-native-host.exe", "CreationDate": "d2"}])
        o = observers.Observer("windows", lambda a: (0, rows, ""))
        found = o.host_processes("c:/run/veles-native-host.exe")
        self.assertEqual([r["pid"] for r in found], [5])
        self.assertTrue(o.alive(found[0]))
        self.assertFalse(o.alive({**found[0], "start": "other"}))

    def test_cim_single_object_empty_and_bad(self):
        self.assertEqual(len(observers.parse_cim(json.dumps({"ProcessId": 1, "ParentProcessId": 0}))), 1)
        self.assertEqual(observers.parse_cim("  "), [])
        self.assertEqual(observers.parse_cim(json.dumps([1, "x", {"nope": 1}])), [])
        with self.assertRaises(ValueError):
            observers.parse_cim("{broken")
        with self.assertRaises(Blocked):  # surfaced as Blocked by the observer, not a raw ValueError
            observers.Observer("windows", lambda a: (0, "{broken", "")).host_processes("C:\\x\\veles-native-host.exe")

    def test_lsof_interpretation_is_strict(self):
        self.assertEqual(observers.parse_lsof(1, "", ""), [])
        self.assertEqual(len(observers.parse_lsof(0, "COMMAND PID\nhost 1 TCP *:80\n", "")), 1)
        for rc, out, err in ((1, "", "permission denied"), (2, "", ""), (127, "", "not found"), (0, "", ""), (1, "COMMAND\n", "")):
            with self.assertRaises(Blocked, msg=(rc, out, err)):
                observers.parse_lsof(rc, out, err)

    def test_windows_endpoint_probe_fails_closed(self):
        ok = json.dumps({"endpoints": [], "errors": 0})
        self.assertEqual(observers.parse_win_endpoints(0, ok), [])
        self.assertEqual(observers.parse_win_endpoints(0, json.dumps({"endpoints": 443, "errors": 0})), [443])
        self.assertEqual(observers.parse_win_endpoints(0, json.dumps({"endpoints": None, "errors": 0})), [])
        for rc, out in ((1, ok), (0, ""), (0, "[]"), (0, "{}"), (0, json.dumps({"endpoints": [], "errors": 2})), (0, "not json"),
                        (0, json.dumps({"endpoints": [], "errors": "0"}))):
            with self.assertRaises(Blocked, msg=(rc, out)):
                observers.parse_win_endpoints(rc, out)
        self.assertIn("-ErrorVariable", observers.WIN_ENDPOINT_SCRIPT)
        self.assertNotIn("| Select-Object LocalPort | ConvertTo-Json", observers.WIN_ENDPOINT_SCRIPT)

    def test_empty_endpoint_result_for_a_host_that_exited_is_blocked_not_none(self):
        row = {"pid": 200, "start": "Mon Nov 1 10:05:01 2026", "command": "/opt/run/veles-native-host chrome-extension://abc/"}
        gone = "  100     1 Mon Nov  1 10:00:00 2026 /sbin/launchd\n"
        alive_run = lambda argv: (1, "", "") if argv[0] == "lsof" else (0, self.PS, "")
        gone_run = lambda argv: (1, "", "") if argv[0] == "lsof" else (0, gone, "")
        self.assertEqual(observers.Observer("macos", alive_run).endpoints(row), [])
        with self.assertRaises(Blocked):
            observers.Observer("macos", gone_run).endpoints(row)
        # windows: empty list + host gone
        def wrun(argv):
            if "Get-NetTCPConnection" in argv[-1]:
                return 0, json.dumps({"endpoints": [], "errors": 0}), ""
            return 0, "", ""
        with self.assertRaises(Blocked):
            observers.Observer("windows", wrun).endpoints({"pid": 7, "start": "d", "command": "x", "path": "C:\\h.exe"})

    def test_unmeasurable_things_are_blocked_never_none_observed(self):
        o = observers.Observer("macos", lambda a: (0, "", ""))
        with self.assertRaises(Blocked):
            o.windows(1)
        with self.assertRaises(Blocked):
            o.no_write_monitor("file writes")
        self.assertEqual(observers.Observer("macos", lambda a: (0, "", ""), windows_census=lambda p: {"top_visible": []}).windows(1), {"top_visible": []})

    def test_command_failure_is_blocked(self):
        with self.assertRaises(Blocked):
            observers.Observer("macos", lambda a: (1, "", "x")).host_processes("/x")
        with self.assertRaises(Blocked):
            observers.Observer("windows", lambda a: (1, "", "x")).alive(1)

    def test_default_run_turns_launch_failures_into_blocked(self):
        with self.assertRaises(Blocked):
            runner.default_run(["/definitely/not/a/command"])


# ---- recorder --------------------------------------------------------------------------------------------------------
class RecorderTests(Tmp):
    case = CASES["macos.none.ready-hygiene"]

    def rec(self, case=None, sub="r"):
        return Recorder(self.tmp / sub, case or self.case)

    def test_capture_validation_and_exclusive_creation(self):
        r = self.rec()
        r.capture("a.json", "process", {"x": 1})
        for bad in ("a.json", "../x", "a/b", "", "x" * 100, "a b"):
            with self.assertRaises((ValueError, OSError), msg=bad):
                r.capture(bad, "process", "d")
        self.assertEqual(r.captures[0]["size"], len((self.tmp / "r" / "captures" / "a.json").read_bytes()))

    def test_existing_output_dir_is_never_reused(self):
        (self.tmp / "exists").mkdir()
        with self.assertRaises(FileExistsError):
            Recorder(self.tmp / "exists", self.case)

    def test_action_outcomes_map_to_blocked_or_fail_never_pass(self):
        r = self.rec()
        self.assertEqual(r.run_action("ok", lambda: 5), 5)
        for exc, label in ((Blocked("b"), "blocked"), (Refused("r"), "blocked"), (ProductFailure("p"), "failed"), (KeyError("k"), "error")):
            with self.assertRaises(type(exc)):
                r.run_action(f"x-{label}-{type(exc).__name__}", lambda exc=exc: (_ for _ in ()).throw(exc))
        self.assertEqual([a["result"] for a in r.actions], ["ok", "blocked", "blocked", "failed", "error"])
        doc = r.finalize()
        self.assertEqual(doc["declared_status"], "FAIL")  # a ProductFailure was measured
        self.assertNotEqual(doc["declared_status"], "PASS")

    def test_driver_error_is_a_blocker_not_a_product_verdict(self):
        r = self.rec()
        with self.assertRaises(KeyError):
            r.run_action("x", lambda: {}["k"])
        doc = r.finalize()
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("driver error KeyError", doc["blocker"])

    def test_complete_run_still_is_not_pass(self):
        case = CASES["macos.none.ready-hygiene"]
        r = Recorder(self.tmp / "full", case)
        for a in case["required_actions"]:
            r.run_action(a, lambda: None)
        ref = r.capture("c", "hygiene", "x")
        for o in case["observations"]:
            r.observation(o, "none_observed", ref)
        for c in case["required_controls"]:
            r.control(c, "pass", ref)
        doc = r.finalize()
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("awaiting separate adjudication", doc["blocker"])

    def test_missing_measurements_are_listed_as_blockers(self):
        r = self.rec()
        doc = r.finalize()
        self.assertEqual(doc["declared_status"], "BLOCKED")
        for needle in ("required actions not completed", "observations not measured", "controls not measured"):
            self.assertIn(needle, doc["blocker"])

    def test_catalog_matches_files_and_validator_never_passes_it(self):
        r = self.rec()
        r.capture("one", "process", "1")
        r.capture("two", "hygiene", {"a": 2})
        r.finalize()
        out = self.tmp / "r"
        lines = (out / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(sorted(l.split("  ")[1] for l in lines), ["captures/one", "captures/two"])
        for l in lines:
            h, p = l.split("  ")
            self.assertEqual(hashlib.sha256((out / p).read_bytes()).hexdigest(), h)
        v = load_validator()
        res = v.validate_run(out)
        self.assertEqual(res.verdict, "BLOCKED")
        # Even if someone flips the declared status to PASS, the validator still refuses (no anchor/adjudication).
        doc = json.loads((out / "run.json").read_text())
        doc["declared_status"] = "PASS"
        doc.pop("blocker", None)
        (out / "run.json").write_text(json.dumps(doc))
        self.assertNotEqual(v.validate_run(out).verdict, "PASS")


# ---- lifecycle -------------------------------------------------------------------------------------------------------
class FakeChrome:
    """Scripted ChromeSession stand-in. `js` answers by recognising the call."""

    def __init__(self, hello_type="ready", scan_ok=True, ext_id=EXT_ID, shutdown_ok=True):
        self.calls, self.started, self.disposed = [], False, False
        self.hello_type, self.scan_ok, self.ext_id, self.shutdown_ok = hello_type, scan_ok, ext_id, shutdown_ok
        self.snapshot_calls = 0

    def start(self):
        self.started = True
        self.calls.append("start")
        return {"product": "Chrome/130.0"}

    def provenance(self):
        return {"path": "/chrome", "sha256": "a" * 64, "launch_args_sha256": "b" * 64, "version": "Chrome/130.0",
                "profile": {"path": "/p", "disposable": True, "is_default_profile": False}}

    def load_extension(self, d):
        self.calls.append("load")
        return {"id": self.ext_id}

    def open_page(self, url):
        self.calls.append(("open", url))
        return "T1", "S1"

    def js(self, session, expr, timeout=60):
        self.calls.append(("js", expr))
        if expr == "typeof window.velesAcceptance === 'object'":
            return True
        if "connect(" in expr:
            return {"opened_at": "x"}
        if "hello()" in expr:
            return {"type": self.hello_type, "api_version": 1, "platform": "macos", "bridge_version": "1"} if self.hello_type == "ready" else \
                {"type": "error", "code": "internal", "stage": "negotiation"}
        if "startScan" in expr:
            return {"type": "scan_state", "status": "started", "scan_handle": "h1"} if self.scan_ok else {"type": "error", "code": "scan_failed", "stage": "x"}
        if "shutdown()" in expr:
            return {"type": "shutdown_complete"} if self.shutdown_ok else {"type": "error", "code": "internal"}
        if "snapshot()" in expr:
            self.snapshot_calls += 1
            return {"portOpen": False, "disconnect": {"at": "t", "message": None}}
        if "closePort" in expr:
            return None
        self.fail_unknown = expr
        return None

    def close_tab(self, target):
        self.calls.append(("close_tab", target))
        return {"success": True}

    def browser_close(self):
        self.calls.append("browser_close")

    def dispose(self):
        self.disposed = True
        self.calls.append("dispose")


class ScriptedObserver:
    """Host process appears once Chrome connects; disappears after any trigger unless `survives`."""

    def __init__(self, host, survives=False, windows=None, endpoints=()):
        self.host, self.survives, self._windows, self._endpoints = host, survives, windows, list(endpoints)
        self.alive_calls = 0
        self.phase = "before"

    def host_processes(self, exe):
        return [] if self.phase == "before" else [{"pid": 4242, "ppid": 1, "command": str(exe)}]

    def alive(self, pid):
        self.alive_calls += 1
        return self.survives

    def endpoints(self, pid):
        return self._endpoints

    def windows(self, pid):
        if self._windows is None:
            raise Blocked("no census")
        return self._windows

    def no_write_monitor(self, what):
        raise Blocked(f"no monitor for {what}")


class AutoPhaseChrome(FakeChrome):
    def __init__(self, observer, **kw):
        super().__init__(**kw)
        self.observer = observer

    def js(self, session, expr, timeout=60):
        if "connect(" in expr:
            self.observer.phase = "after"
        return super().js(session, expr, timeout)


class RecordingRegistration:
    recovery_hint = reg.WindowsRegistration.recovery_hint

    def __init__(self, fail_restore=False, fail_register=None, mutate_before_fail=False):
        self.events, self.fail_restore, self.fail_register = [], fail_restore, fail_register
        self.mutate_before_fail = mutate_before_fail
        self.manifest_bytes = json.dumps(reg.build_manifest(HOST_NAME, "/x/veles-native-host", ORIGIN, "posix")).encode()
        self.registered = False

    def register(self):
        if self.fail_register:
            if self.mutate_before_fail:
                self.registered = True  # like WindowsRegistration after its journal write
                self.events.append("mutated")
            raise self.fail_register
        self.registered = True
        self.events.append("register")
        return {"scope": "fake"}

    def unregister(self):
        self.events.append("unregister")
        if self.fail_restore:
            raise reg.RestoreFailed("registry mismatch")
        return {"restored": True}


class AttachedRig:
    """TEST-ONLY rig whose every call succeeds. It exists to prove the driver's own sequencing; it is not a phone."""

    def __getattr__(self, name):
        if name.startswith("_") or name in ("cleanup", "before_trigger"):
            raise AttributeError(name)
        return lambda ctx, st: None


class LifecycleTests(Tmp):
    def ctx(self, case_id, observer=None, chrome=None, rig=None, pipe_tap=None, hello="ready", **kw):
        case = CASES[case_id]
        self.observer = observer or ScriptedObserver("h")
        self.chrome = chrome or AutoPhaseChrome(self.observer, hello_type=hello)
        self.rec = Recorder(self.tmp / f"out{len(list(self.tmp.iterdir()))}", case)
        self.rec.fields["binary"] = {"sha256": "c" * 64}
        t = {"n": 0}

        def clock():
            t["n"] += 1
            return t["n"] * 0.01
        return lifecycle.Ctx(case, self.rec, chrome=self.chrome, observer=self.observer, host_exe="/x/veles-native-host", host_name=HOST_NAME,
                             ext_dir="/ext", ext_id=EXT_ID, origin=ORIGIN, rig=rig, pipe_tap=pipe_tap, clock=clock, sleep=lambda s: None, **kw)

    def test_phone_cases_block_before_any_trigger_without_a_rig(self):
        for cid in ("macos.AB.lifecycle-port-disconnect", "macos.AB.lifecycle-tab-close", "macos.AB.lifecycle-pipe-closure",
                    "macos.AB.lifecycle-explicit-shutdown"):
            reg_ = RecordingRegistration()
            ctx = self.ctx(cid)
            doc = lifecycle.run_case(ctx, reg_)
            self.assertEqual(doc["declared_status"], "BLOCKED", cid)
            self.assertIn("connect_phone_A", doc["blocker"])
            self.assertNotIn("PASS", doc["declared_status"])
            fired = [c for c in self.chrome.calls if c in ("browser_close",) or (isinstance(c, tuple) and (c[0] == "close_tab" or "closePort" in c[1] or "shutdown()" in c[1]))]
            self.assertEqual(fired, [], f"{cid}: a lifecycle trigger fired without proven preconditions")
            self.assertTrue(self.chrome.disposed)
            self.assertEqual(reg_.events, ["register", "unregister"])

    def test_cleanup_runs_even_when_chrome_fails_to_start(self):
        class Boom(FakeChrome):
            def start(self):
                raise Blocked("no chrome")
        reg_ = RecordingRegistration()
        ctx = self.ctx("macos.none.ready-hygiene", chrome=Boom())
        doc = lifecycle.run_case(ctx, reg_)
        self.assertIn("no chrome", doc["blocker"])
        self.assertTrue(ctx.chrome.disposed if hasattr(ctx.chrome, "disposed") else True)
        self.assertEqual(reg_.events, ["register", "unregister"])

    def test_unexpected_exceptions_finalize_blocked_with_a_record_on_disk(self):
        for label, exc in (("MemoryError", MemoryError("x")), ("RuntimeError (CDP)", RuntimeError("Extensions.loadUnpacked: {'code': -32601}")),
                           ("ValueError (parse)", ValueError("bad json")), ("TimeoutExpired", subprocess.TimeoutExpired("ps", 1)),
                           ("PermissionError", PermissionError("winreg"))):
            class Crash(FakeChrome):
                def load_extension(self, d, exc=exc):
                    raise exc
            reg_ = RecordingRegistration()
            ctx = self.ctx("macos.none.ready-hygiene", chrome=Crash())
            doc = lifecycle.run_case(ctx, reg_)
            self.assertEqual(doc["declared_status"], "BLOCKED", label)
            self.assertEqual(doc["driver_errors"][0]["type"], type(exc).__name__, label)
            self.assertIn("driver error", doc["blocker"], label)
            self.assertNotIn("failures", doc, label)  # a driver defect is never a product FAIL
            on_disk = json.loads((ctx.rec.out / "run.json").read_text())
            self.assertEqual(on_disk["declared_status"], "BLOCKED", label)
            self.assertTrue((ctx.rec.out / "SHA256SUMS").exists(), label)
            self.assertTrue(ctx.chrome.disposed, label)
            self.assertEqual(reg_.events, ["register", "unregister"], label)

    def test_interrupt_still_cleans_up_finalizes_then_propagates(self):
        class Boom(FakeChrome):
            def load_extension(self, d):
                raise KeyboardInterrupt()
        reg_ = RecordingRegistration()
        ctx = self.ctx("macos.none.ready-hygiene", chrome=Boom())
        with self.assertRaises(KeyboardInterrupt):
            lifecycle.run_case(ctx, reg_)
        doc = json.loads((ctx.rec.out / "run.json").read_text())
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertTrue(doc["driver_errors"])
        self.assertEqual(reg_.events, ["register", "unregister"])
        self.assertTrue(ctx.chrome.disposed)

    def test_cleanup_failures_never_prevent_the_record(self):
        class BadDispose(FakeChrome):
            def dispose(self):
                raise OSError("rmtree failed")
        reg_ = RecordingRegistration(fail_restore=True)
        ctx = self.ctx("macos.none.ready-hygiene", chrome=BadDispose())
        doc = lifecycle.run_case(ctx, reg_)
        self.assertIn("disposal failed", doc["blocker"])
        self.assertIn("REGISTRATION NOT RESTORED", doc["blocker"])
        self.assertTrue((ctx.rec.out / "run.json").exists())

    def test_registration_refusal_runs_nothing_and_cleans_nothing(self):
        reg_ = RecordingRegistration(fail_register=Refused("key exists"))
        ctx = self.ctx("macos.none.ready-hygiene")
        doc = lifecycle.run_case(ctx, reg_)
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("registration refused: key exists", doc["blocker"])
        self.assertNotIn("start", self.chrome.calls)
        self.assertEqual(reg_.events, [])  # never "restore" something we did not create

    def test_registration_that_mutated_then_failed_is_still_cleaned_up(self):
        reg_ = RecordingRegistration(fail_register=Refused("read-back differs"), mutate_before_fail=True)
        ctx = self.ctx("macos.none.ready-hygiene")
        doc = lifecycle.run_case(ctx, reg_)
        self.assertIn("registration refused: read-back differs", doc["blocker"])
        self.assertEqual(reg_.events, ["mutated", "unregister"], "cleanup must follow the actual mutation, not the exception type")
        self.assertNotIn("start", self.chrome.calls)  # no action ran on a doubtful registration

    def test_restore_failure_is_loud(self):
        reg_ = RecordingRegistration(fail_restore=True)
        doc = lifecycle.run_case(self.ctx("macos.none.ready-hygiene"), reg_)
        self.assertIn("REGISTRATION NOT RESTORED", doc["blocker"])
        self.assertIn("--recover-registration --confirm-no-active-run", doc["blocker"])

    def test_test_manifest_evidence_is_bound_to_binary_and_origin(self):
        reg_ = RecordingRegistration()
        ctx = self.ctx("macos.none.ready-hygiene")
        doc = lifecycle.run_case(ctx, reg_)
        tm = doc["test_manifest"]
        self.assertEqual(tm["allowed_origin"], ORIGIN)
        self.assertEqual(tm["host_sha256"], "c" * 64)
        self.assertEqual(tm["scope"], "test_only_not_production_registration")
        data = (ctx.rec.out / tm["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), tm["sha256"])
        self.assertEqual(json.loads(data)["allowed_origins"], [ORIGIN])

    def test_extension_id_mismatch_blocks(self):
        obs = ScriptedObserver("h")
        ctx = self.ctx("macos.none.ready-hygiene", observer=obs, chrome=AutoPhaseChrome(obs, ext_id="z" * 32))
        doc = lifecycle.run_case(ctx, RecordingRegistration())
        self.assertIn("not the keyed id", doc["blocker"])
        self.assertNotIn("connect_native", [a["name"] for a in doc["actions"]])

    def test_connect_native_waits_for_the_connector_page_api_before_connecting(self):
        class LatePage(FakeChrome):
            def __init__(self):
                super().__init__()
                self.ready_polls = 0

            def js(self, session, expr, timeout=60):
                if expr == "typeof window.velesAcceptance === 'object'":
                    self.ready_polls += 1
                    if self.ready_polls < 3:
                        self.calls.append(("js", expr))
                        return False
                return super().js(session, expr, timeout)

        chrome = LatePage()
        lifecycle.connect_native(self.ctx("macos.none.ready-hygiene", chrome=chrome), {})
        connect_calls = [i for i, c in enumerate(chrome.calls)
                         if isinstance(c, tuple) and "velesAcceptance.connect(" in c[1]]
        self.assertEqual(len(connect_calls), 1)
        self.assertEqual(chrome.ready_polls, 3)
        self.assertGreater(connect_calls[0], 2, "connect must not run before the connector page defines its API")

    def test_connect_native_blocks_without_connecting_when_the_connector_api_never_appears(self):
        class NeverReady(FakeChrome):
            def js(self, session, expr, timeout=60):
                if expr == "typeof window.velesAcceptance === 'object'":
                    self.calls.append(("js", expr))
                    return False
                return super().js(session, expr, timeout)

        chrome = NeverReady()
        with self.assertRaisesRegex(Blocked, "native connect was not attempted"):
            lifecycle.connect_native(self.ctx("macos.none.ready-hygiene", chrome=chrome), {})
        self.assertFalse([c for c in chrome.calls if isinstance(c, tuple) and "velesAcceptance.connect(" in c[1]])

    def test_preexisting_host_process_refuses_ambiguous_baseline(self):
        obs = ScriptedObserver("h")
        obs.phase = "after"  # a host is already running before launch
        ctx = self.ctx("macos.none.ready-hygiene", observer=obs, chrome=FakeChrome())
        doc = lifecycle.run_case(ctx, RecordingRegistration())
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("already running", doc["blocker"])
        self.assertFalse(ctx.chrome.started)

    def test_hello_error_is_a_product_failure_without_payload_leak(self):
        doc = lifecycle.run_case(self.ctx("macos.none.ready-hygiene", hello="error"), RecordingRegistration())
        self.assertEqual(doc["declared_status"], "FAIL")
        self.assertIn("negotiation", doc["failures"][0])
        self.assertNotIn("record_base64", json.dumps(doc))

    def test_hygiene_records_only_what_was_measured(self):
        obs = ScriptedObserver("h", windows=None, endpoints=[])
        doc = lifecycle.run_case(self.ctx("macos.none.ready-hygiene", observer=obs, chrome=AutoPhaseChrome(obs)), RecordingRegistration())
        names = {o["name"]: o["result"] for o in doc["observations"]}
        self.assertEqual(names, {"no_network_endpoint": "none_observed"})
        self.assertEqual(doc["declared_status"], "BLOCKED")
        for missing in ("no_top_level_window", "no_filesystem_write_outside_test_dirs", "no_launchservices_registration_change"):
            self.assertIn(missing, doc["blocker"])

    def test_a_window_or_endpoint_is_recorded_as_observed(self):
        obs = ScriptedObserver("h", windows={"top_visible": [{"class": "X"}], "top_hidden": [], "message_only": []}, endpoints=["tcp *:80"])
        doc = lifecycle.run_case(self.ctx("macos.none.ready-hygiene", observer=obs, chrome=AutoPhaseChrome(obs)), RecordingRegistration())
        names = {o["name"]: o["result"] for o in doc["observations"]}
        self.assertEqual(names["no_top_level_window"], "observed")
        self.assertEqual(names["no_network_endpoint"], "observed")
        v = load_validator()
        res = v.validate_run(self.rec.out)
        self.assertNotEqual(res.verdict, "PASS")

    def test_windows_only_message_only_window_counts(self):
        obs = ScriptedObserver("h", windows={"top_visible": [], "top_hidden": [], "message_only": [{"class": "Hidden"}]})
        doc = lifecycle.run_case(self.ctx("macos.none.ready-hygiene", observer=obs, chrome=AutoPhaseChrome(obs)), RecordingRegistration())
        self.assertEqual({o["name"]: o["result"] for o in doc["observations"]}["no_top_level_window"], "observed")

    def run_with_rig(self, case_id, **kw):
        obs = ScriptedObserver("h", survives=kw.pop("survives", False))
        chrome = AutoPhaseChrome(obs)
        ctx = self.ctx(case_id, observer=obs, chrome=chrome, rig=AttachedRig(), **kw)
        return lifecycle.run_case(ctx, RecordingRegistration()), chrome

    def test_explicit_shutdown_observes_last_json_and_stdout_close(self):
        doc, chrome = self.run_with_rig("macos.AB.lifecycle-explicit-shutdown")
        done = [a["name"] for a in doc["actions"] if a["result"] == "ok"]
        for a in ("shutdown_request", "observe_last_json", "observe_stdout_close", "observe_process_exit"):
            self.assertIn(a, done)
        # rig steps recorded no controls, so the run cannot look complete
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("controls not measured", doc["blocker"])

    def test_other_triggers_cannot_observe_last_json_without_a_pipe_tap(self):
        for cid, trig in (("macos.AB.lifecycle-port-disconnect", "closePort"), ("macos.AB.lifecycle-tab-close", "close_tab"),
                          ("macos.AB.lifecycle-pipe-closure", "browser_close")):
            doc, chrome = self.run_with_rig(cid)
            acts = {a["name"]: a for a in doc["actions"]}
            self.assertEqual(acts["observe_last_json"]["result"], "blocked", cid)
            self.assertIn("pipe tap", doc["blocker"], cid)
            self.assertNotIn("observe_stdout_close", acts, cid)  # fail fast
            self.assertEqual(doc["declared_status"], "BLOCKED")

    def test_pipe_tap_unblocks_but_is_recorded_as_a_capture(self):
        class Tap:
            def last_json(self):
                return {"frames": []}

            def stdout_closed(self):
                return {"closed": True}
        doc, _ = self.run_with_rig("macos.AB.lifecycle-tab-close", pipe_tap=Tap())
        done = [a["name"] for a in doc["actions"] if a["result"] == "ok"]
        self.assertIn("observe_stdout_close", done)

    def test_host_surviving_the_trigger_is_a_product_failure(self):
        doc, _ = self.run_with_rig("macos.AB.lifecycle-explicit-shutdown", survives=True)
        self.assertEqual(doc["declared_status"], "FAIL")
        self.assertIn("survived", doc["failures"][0])

    def test_shutdown_not_answered_with_shutdown_complete_fails(self):
        obs = ScriptedObserver("h")
        ctx = self.ctx("macos.AB.lifecycle-explicit-shutdown", observer=obs, chrome=AutoPhaseChrome(obs, shutdown_ok=False), rig=AttachedRig())
        doc = lifecycle.run_case(ctx, RecordingRegistration())
        self.assertEqual(doc["declared_status"], "FAIL")

    def test_ambiguous_host_processes_block(self):
        class Two(ScriptedObserver):
            def host_processes(self, exe):
                return [] if self.phase == "before" else [{"pid": 1}, {"pid": 2}]
        obs = Two("h")
        doc = lifecycle.run_case(self.ctx("macos.none.ready-hygiene", observer=obs, chrome=AutoPhaseChrome(obs)), RecordingRegistration())
        self.assertIn("exactly one new production host process", doc["blocker"])

    def test_every_case_action_has_a_handler_or_blocks(self):
        for cid, case in CASES.items():
            ctx = lifecycle.Ctx(case, None)
            for a in case["required_actions"]:
                fn = lifecycle.handler_for(ctx, a)
                self.assertTrue(callable(fn), (cid, a))
                if a not in (lifecycle.DIRECT if case["kind"] == "direct" else lifecycle.CHROME):
                    with self.assertRaises(Blocked, msg=(cid, a)):
                        fn(ctx, {})

    def test_unknown_direct_action_blocks(self):
        ctx = lifecycle.Ctx({"kind": "direct"}, None)
        with self.assertRaises(Blocked):
            lifecycle.handler_for(ctx, "radio_off")(ctx, {})


# ---- direct launch against a REAL child process (a scripted stand-in host) ----------------------------------------------
FAKE_HOST = r'''
import struct, sys, json, time
mode = sys.argv[2] if len(sys.argv) > 2 else "good"
sys.stderr.write("argv=" + json.dumps(sys.argv[1:2]) + "\n"); sys.stderr.flush()
def read(n):
    b = b""
    while len(b) < n:
        c = sys.stdin.buffer.read(n - len(b))
        if not c: return None
        b += c
    return b
def send(o):
    d = json.dumps(o).encode(); sys.stdout.buffer.write(struct.pack("<I", len(d)) + d); sys.stdout.buffer.flush()
h = read(4)
req = json.loads(read(struct.unpack("<I", h)[0]))
if mode == "silent":
    time.sleep(60)
send({"envelope_version": 1, "type": "ready" if mode != "noready" else "error", "request_id": req["request_id"], "api_version": 1, "platform": "x"})
h = read(4)
if h is None:
    if mode == "hang": time.sleep(60)
    sys.exit(0)
n = struct.unpack("<I", h)[0]
body = read(n)
sys.exit(2 if body is None else 0)
'''


class DirectLaunchTests(Tmp):
    def setUp(self):
        super().setUp()
        self.script = self.tmp / "fake_host.py"
        self.script.write_text(FAKE_HOST)
        self.patcher_wait = lifecycle.EXIT_WAIT_S

    def tearDown(self):
        lifecycle.EXIT_WAIT_S = self.patcher_wait

    def run_direct(self, case_id, mode="good", wait=5.0):
        lifecycle.EXIT_WAIT_S = wait
        case = CASES[case_id]
        rec = Recorder(self.tmp / f"d{len(list(self.tmp.iterdir()))}", case)

        def popen(argv, **kw):
            self.argv = argv
            self.env_seen = kw.get("env")
            return subprocess.Popen([sys.executable, "-I", str(self.script), argv[1], mode], **kw)
        ctx = lifecycle.Ctx(case, rec, host_exe="/x/veles-native-host", origin=ORIGIN, popen=popen, env={"PATH": "/bin"})
        return lifecycle.run_case(ctx), rec

    def test_clean_eof_exit_zero_is_measured(self):
        doc, rec = self.run_direct("macos.none.direct-eof-clean")
        self.assertEqual(doc["process"]["exit_code"], 0)
        self.assertEqual(self.argv[1:], [ORIGIN])
        done = [a["name"] for a in doc["actions"] if a["result"] == "ok"]
        self.assertEqual(done, CASES["macos.none.direct-eof-clean"]["required_actions"])
        self.assertEqual(doc["declared_status"], "BLOCKED")  # context/adjudication still missing; never PASS

    def test_truncated_frame_exit_two_is_measured_and_partial_bytes_reported(self):
        doc, rec = self.run_direct("macos.none.direct-eof-truncated")
        self.assertEqual(doc["process"]["exit_code"], 2)
        last = json.loads((rec.out / "captures" / "last-json").read_text())
        self.assertEqual(len(last["frames"]), 1)

    def test_no_ready_is_a_product_failure(self):
        doc, _ = self.run_direct("macos.none.direct-eof-clean", mode="noready")
        self.assertEqual(doc["declared_status"], "FAIL")

    def test_host_that_never_exits_after_eof_fails_and_is_killed(self):
        doc, rec = self.run_direct("macos.none.direct-eof-clean", mode="hang", wait=0.5)
        self.assertEqual(doc["declared_status"], "FAIL")
        self.assertTrue(any("exit" in f or "stdout" in f for f in doc["failures"]))

    def test_validator_accepts_the_measured_exit_code_only_via_full_evidence(self):
        doc, rec = self.run_direct("macos.none.direct-eof-clean")
        res = load_validator().validate_run(rec.out)
        self.assertNotEqual(res.verdict, "PASS")


# ---- runner / CLI ----------------------------------------------------------------------------------------------------
FAKE_ANCHOR = {"source_revision": "1" * 40, "source_tree_sha256": "2" * 64, "evaluator_files": {"scripts/physical-matrix.py": "3" * 64,
               "scripts/validate-evidence.py": "4" * 64, "acceptance/cases.json": "5" * 64}, "binary_path": "/x", "binary_path_platform": "posix"}


def fake_clock(step=1.0):
    """A clock that advances `step` seconds per reading, so deadline loops end instantly and deterministically."""
    t = {"n": 0.0}

    def clock():
        t["n"] += step
        return t["n"]
    return clock


MAC_ADAPTER_OUTPUT = json.dumps({"SPBluetoothDataType": [{"controller_properties": {
    "controller_address": "10:20:30:40:50:60", "controller_chipset": "BCM_4387", "controller_transport": "PCIe"}}]})
WINDOWS_ADAPTER_OUTPUT = json.dumps([{
    "Status": "OK", "FriendlyName": "Intel(R) Wireless Bluetooth(R)",
    "InstanceId": "USB\\VID_8087&PID_0026\\5&2A1B3C4D&0&10"}])


class RunnerTests(Tmp):
    def anchor(self, host):
        return {**FAKE_ANCHOR, "binary_path": str(host)}

    def test_wrong_platform_is_a_blocked_record_with_binary_hash(self):
        host = self.exe("veles-native-host.exe")
        doc = runner.execute_case(CASES["windows.none.ready-hygiene"], host, "/chrome", self.tmp / "o", self.anchor(host), family="linux")
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("targets windows but this machine is linux", doc["blocker"])
        self.assertEqual(doc["binary"]["sha256"], hashlib.sha256(host.read_bytes()).hexdigest())
        self.assertIsNone(doc["binary"]["built_from_source_revision"], "builder attestation must be explicit")

    def test_attestation_is_explicit_only(self):
        host = self.exe()
        doc = runner.execute_case(CASES["macos.none.ready-hygiene"], host, "/chrome", self.tmp / "o", self.anchor(host), family="linux", attest=True)
        self.assertEqual(doc["binary"]["built_from_source_revision"], "1" * 40)

    def test_binary_without_the_acceptance_origin_is_blocked_before_anything_starts(self):
        host = self.exe(embed=False)
        started = []
        doc = runner.execute_case(CASES["macos.none.ready-hygiene"], host, "/chrome", self.tmp / "o", self.anchor(host), family="macos",
                                  session_factory=lambda *a, **k: started.append(1))
        self.assertIn("VELES_ALLOWED_ORIGINS", doc["blocker"])
        self.assertEqual(started, [])

    def test_wrong_executables_and_existing_output_are_refused(self):
        for name in ("otp23-host", "host-test-driver", "veles-native-host.bak"):
            host = self.exe(name, where=self.tmp / name.replace(".", "_"))
            with self.assertRaises(Refused, msg=name):
                runner.execute_case(CASES["macos.none.ready-hygiene"], host, "/c", self.tmp / "o2", self.anchor(host), family="macos")
        host = self.exe()
        (self.tmp / "taken").mkdir()
        with self.assertRaises(Refused):
            runner.execute_case(CASES["macos.none.ready-hygiene"], host, "/c", self.tmp / "taken", self.anchor(host), family="macos")
        with self.assertRaises(Refused):  # macOS binary name for a Windows case
            runner.execute_case(CASES["windows.none.ready-hygiene"], host, "/c", self.tmp / "o3", self.anchor(host), family="windows")

    # --- real runner wiring ------------------------------------------------------------------------------------------
    def mac_env(self, personal_write=None, crash=None, run_extra=None):
        """Drive execute_case on 'macos' with REAL wiring (registration, native_messaging_dirs, observers, recorder) and
        only Chrome and the OS commands faked."""
        self.home = self.tmp / "home"
        self.sysroot = self.tmp / "sys"
        self.personal = Path(reg.native_messaging_dirs(self.home, self.sysroot)[0])
        self.personal.mkdir(parents=True, exist_ok=True)
        (self.personal / "personal.json").write_text("{}")
        (self.personal.parent / "Local State").write_text("personal browser state")
        self.host = self.exe(where=self.tmp / "VelesOtp23PoC.app" / "Contents" / "MacOS")
        self.sessions = []
        test = self

        class Sess(FakeChrome):
            def __init__(s, chrome, run_root, home_, env, before_launch=None, **kw):
                super().__init__()
                s.env, s.profile, s.before_launch = env, Path(run_root) / "chrome-profile", before_launch
                test.sessions.append(s)

            def start(s):
                s.profile.mkdir()
                s.before_launch(s.profile)
                s.manifest_at_launch = next((s.profile / "NativeMessagingHosts").iterdir()).read_text()
                if personal_write:
                    personal_write(test)
                if crash:
                    raise crash
                return super().start()

        def fake_run(argv):
            if argv[:2] == ["codesign", "-dvv"]:
                return 0, "", GOOD_CODESIGN
            if "--verify" in argv:
                return 0, "", ""
            if argv[:2] == ["codesign", "-d"]:
                Path(argv[2].split("=", 1)[1] + "0").write_bytes(b"cert")
            if argv[:2] == ["system_profiler", "SPBluetoothDataType"]:
                return 0, MAC_ADAPTER_OUTPUT, ""
            return 0, "", ""
        self.fake_run = run_extra or fake_run
        self.Sess = Sess

    def run_mac(self, **kw):
        out = self.tmp / f"o{len(list(self.tmp.iterdir()))}"
        doc = runner.execute_case(CASES["macos.none.ready-hygiene"], self.host, str(self.host), out, self.anchor(self.host), family="macos",
                                  run=self.fake_run, session_factory=self.Sess, home=str(self.home), system_root=str(self.sysroot),
                                  env=kw.pop("env", {"PATH": "/bin"}), sleep=lambda s: None, temp_root=str(self.tmp / "tmp"),
                                  clock=fake_clock(), **kw)
        return doc, out

    def run_direct_mac(self, case):
        script = self.tmp / "direct-host.py"
        script.write_text(FAKE_HOST)

        def popen(argv, **kw):
            return subprocess.Popen([sys.executable, "-I", str(script), argv[1], "good"], **kw)
        out = self.tmp / f"d{len(list(self.tmp.iterdir()))}"
        doc = runner.execute_case(case, self.host, "", out, self.anchor(self.host), family="macos", run=self.fake_run, popen=popen,
                                  home=str(self.home), system_root=str(self.sysroot), env={"PATH": "/bin"},
                                  temp_root=str(self.tmp / "tmp"), sleep=lambda s: None, clock=fake_clock(0.001))
        return doc, out

    def test_macos_direct_launch_records_a_hashed_adapter_identity(self):
        self.mac_env()
        doc, _ = self.run_direct_mac(CASES["macos.none.direct-eof-clean"])
        self.assertEqual(doc["adapter"]["kind"], "bluetooth")
        self.assertRegex(doc["adapter"]["id_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("10:20:30:40:50:60", json.dumps(doc))

    def test_macos_direct_launch_reads_the_approved_signing_identity_regardless_of_the_case_flag(self):
        self.mac_env()
        doc, _ = self.run_direct_mac(dict(CASES["macos.none.direct-eof-clean"], requires_signing=False))
        self.assertEqual(doc["signing"]["identity"], macid.APPROVED_AUTHORITY)
        self.assertEqual(doc["signing"]["bundle_kind"], "test_vehicle")

    def setUp(self):
        super().setUp()
        (self.tmp / "tmp").mkdir()

    def test_macos_real_wiring_clean_run_touches_nothing_global(self):
        self.mac_env()
        before = reg.snapshot_native_hosts(reg.native_messaging_dirs(self.home, self.sysroot))
        doc, out = self.run_mac()
        self.assertNotEqual(doc["declared_status"], "PASS")
        self.assertEqual(reg.snapshot_native_hosts(reg.native_messaging_dirs(self.home, self.sysroot)), before)
        self.assertEqual(json.loads(self.sessions[0].manifest_at_launch)["allowed_origins"], [ORIGIN])
        self.assertEqual(doc["signing"]["bundle_kind"], "test_vehicle")
        self.assertNotIn("registration refused", doc.get("blocker", ""))
        self.assertNotIn("NOT RESTORED", doc.get("blocker", ""))
        self.assertEqual(list((self.tmp / "tmp").iterdir()), [], "work dir must be removed")
        evidence = "".join(p.read_text() for p in out.rglob("*") if p.is_file())
        self.assertNotIn("personal.json", evidence)
        self.assertNotIn("Local State", evidence)

    def test_macos_real_wiring_detects_a_manifest_written_to_the_personal_native_messaging_dir(self):
        def write(test):
            (test.personal / f"{HOST_NAME}.json").write_text("{}")
        self.mac_env(personal_write=write)
        doc, out = self.run_mac()
        self.assertIn("REGISTRATION NOT RESTORED", doc["blocker"])
        self.assertIn(": added", doc["blocker"])
        self.assertNotIn("--recover-registration", doc["blocker"], "nothing to recover on macOS")
        self.assertEqual(doc["declared_status"], "BLOCKED")

    def test_macos_real_wiring_detects_system_dir_and_ignores_unrelated_personal_files(self):
        def write(test):
            (test.personal.parent / "Local State").write_text("the user's own Chrome rewrote this")
            (test.personal.parent / "Default").mkdir(exist_ok=True)
        self.mac_env(personal_write=write)
        doc, _ = self.run_mac()
        self.assertNotIn("NOT RESTORED", doc.get("blocker", ""))
        self.mac_env(personal_write=lambda t: (Path(reg.native_messaging_dirs(t.home, t.sysroot)[1])).mkdir(parents=True))
        doc, _ = self.run_mac()
        self.assertIn("NOT RESTORED", doc["blocker"])

    def test_macos_driver_crash_mid_run_still_gives_a_blocked_record_and_clean_state(self):
        self.mac_env(crash=RuntimeError("DevTools exploded"))
        before = reg.snapshot_native_hosts(reg.native_messaging_dirs(self.home, self.sysroot))
        doc, out = self.run_mac()
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertTrue(doc["driver_errors"])
        self.assertTrue((out / "run.json").exists())
        self.assertEqual(reg.snapshot_native_hosts(reg.native_messaging_dirs(self.home, self.sysroot)), before)
        self.assertEqual(list((self.tmp / "tmp").iterdir()), [])

    def test_assembly_failures_leave_a_blocked_record_not_an_empty_dir(self):
        self.mac_env()
        def boom(*a, **k):
            raise RuntimeError("factory failed")
        out = self.tmp / "asm"
        doc = runner.execute_case(CASES["macos.none.ready-hygiene"], self.host, str(self.host), out, self.anchor(self.host), family="macos",
                                  run=self.fake_run, session_factory=boom, home=str(self.home), system_root=str(self.sysroot), env={},
                                  temp_root=str(self.tmp / "tmp"))
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertEqual(doc["driver_errors"][0]["stage"], "assembly")
        self.assertTrue((out / "run.json").exists() and (out / "SHA256SUMS").exists())
        self.assertEqual(list((self.tmp / "tmp").iterdir()), [])

    def test_signing_command_timeout_is_a_blocked_record(self):
        def timeout_run(argv):
            raise Blocked("codesign could not be run: TimeoutExpired")
        self.mac_env(run_extra=timeout_run)
        doc, out = self.run_mac()
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("signing identity", doc["blocker"])
        self.assertTrue((out / "run.json").exists())
        self.assertEqual(list((self.tmp / "tmp").iterdir()), [])

    def test_env_is_scrubbed_for_chrome_and_for_direct_launch(self):
        self.mac_env()
        env = {"PATH": "/bin", "RUST_BACKTRACE": "full", "VELES_OTP23_TRACE": "/t", "RUST_LOG": "debug", "HOME": "/h"}
        self.run_mac(env=env)
        self.assertEqual(self.sessions[0].env, {"PATH": "/bin", "HOME": "/h"})
        seen = {}

        script = self.tmp / "fake_host.py"
        script.write_text(FAKE_HOST)

        def popen(argv, **kw):
            seen.update(kw["env"])
            return subprocess.Popen([sys.executable, "-I", str(script), argv[1], "good"], **kw)
        self.host = self.exe(where=self.tmp / "VelesOtp23PoC.app" / "Contents" / "MacOS")
        runner.execute_case(CASES["macos.none.direct-eof-clean"], self.host, "", self.tmp / "direct", self.anchor(self.host), family="macos",
                            run=self.fake_run, popen=popen, home=str(self.home), system_root=str(self.sysroot), env=env,
                            temp_root=str(self.tmp / "tmp"), sleep=lambda s: None, clock=fake_clock(0.001))
        self.assertEqual(seen, {"PATH": "/bin", "HOME": "/h"})

    def test_binary_changing_during_the_run_is_flagged(self):
        self.mac_env()
        test = self

        def tamper(t):
            Path(t.host).write_bytes(Path(t.host).read_bytes() + b"X")
        self.mac_env(personal_write=tamper)
        doc, _ = self.run_mac()
        self.assertIn("executable changed during the run", doc["blocker"])
        self.assertNotEqual(doc["binary"]["sha256_after_run"], doc["binary"]["sha256"])
        self.assertEqual(doc["binary"]["role"], "production_source_acceptance_build")

    # --- Windows wiring with a fake registry ---------------------------------------------------------------------------
    def win_env(self):
        from unittest import mock
        self.host = self.exe("veles-native-host.exe", where=self.tmp / "w")
        self.state = self.tmp / "localappdata" / "Veles" / "Otp24Acceptance"
        self.env = {"PATH": "x", "LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local"}
        self.registry = FakeRegistry({"Pre.Existing": "C:\\a.json"})
        self.sessions = []
        test = self
        # POSIX test machine: let the Windows-style absolute-path rule accept the temp path (only this predicate is patched)
        p = mock.patch.object(reg, "_abs_ok", lambda path, family: True)
        p.start()
        self.addCleanup(p.stop)

        class Sess(FakeChrome):
            def __init__(s, *a, **k):
                super().__init__()
                test.sessions.append(s)
                s.env = a[3]
                test.during_run_keys = None
        self.Sess = Sess

    def run_win(self, session_factory=None, case="windows.none.ready-hygiene", **kw):
        def fake_run(argv):
            if argv[0] == "powershell" and "Authenticode" in argv[-1]:
                return 0, "NotSigned\n", ""
            if argv[0] == "powershell" and "Get-PnpDevice" in argv[-1]:
                return 0, WINDOWS_ADAPTER_OUTPUT, ""
            return 0, "", ""
        out = self.tmp / f"w{len(list(self.tmp.iterdir()))}"
        self.registry.journal = self.state / reg.JOURNAL_NAME
        doc = runner.execute_case(CASES[case], self.host, "C:\\chrome\\chrome.exe", out, self.anchor(self.host), family="windows", run=fake_run,
                                  backend_factory=lambda: self.registry, session_factory=session_factory or self.Sess, sleep=lambda s: None,
                                  clock=fake_clock(), state_dir=self.state, env=self.env, temp_root=str(self.tmp / "tmp"), **kw)
        return doc, out

    def test_windows_wiring_registers_in_the_fixed_state_dir_and_restores_everything(self):
        self.win_env()
        before = dict(self.registry.keys)
        doc, out = self.run_win()
        self.assertEqual(self.registry.keys, before)
        self.assertEqual([e[0] for e in self.registry.events if e[0] in ("create", "delete")], ["create", "delete"])
        self.assertFalse((self.state / reg.JOURNAL_NAME).exists())
        self.assertFalse((self.state / reg.LOCK_NAME).exists())
        self.assertEqual(list((self.tmp / "tmp").iterdir()), [])
        self.assertEqual(doc["test_manifest"]["allowed_origin"], ORIGIN)
        self.assertNotEqual(doc["declared_status"], "PASS")
        self.assertEqual(doc["signing"]["authenticode"], "Get-AuthenticodeSignature status: NotSigned")
        self.assertNotIn("registration_journal", doc)

    def test_windows_leftover_key_journal_or_lock_blocks_before_any_mutation(self):
        for label, setup in (("key", lambda: self.registry.keys.update({reg.NAME_PREFIX + "aaaaaaaaaaaa": "C:\\x.json"})),
                             ("journal", lambda: (reg.ensure_state_dir(self.state), (self.state / reg.JOURNAL_NAME).write_text("{}"))),
                             ("lock", lambda: (reg.ensure_state_dir(self.state), (self.state / reg.LOCK_NAME).write_text("{}")))):
            self.win_env()
            setup()
            keys = dict(self.registry.keys)
            doc, out = self.run_win()
            self.assertEqual(doc["declared_status"], "BLOCKED", label)
            self.assertIn("startup refused", doc["blocker"], label)
            self.assertEqual(self.registry.keys, keys, label)
            self.assertFalse([e for e in self.registry.events if e[0] in ("create", "delete")], label)
            self.assertEqual(self.sessions, [], label)  # Chrome never even constructed
            for f in ("journal", "lock"):
                if label == f:
                    self.assertTrue((self.state / (reg.JOURNAL_NAME if f == "journal" else reg.LOCK_NAME)).exists(), "leftovers are never silently removed")
            self.tearDown_win()

    def tearDown_win(self):
        import shutil
        shutil.rmtree(self.state.parent.parent, ignore_errors=True)
        for p in (self.tmp / "tmp").iterdir():
            shutil.rmtree(p, ignore_errors=True)

    def test_windows_driver_crash_after_registration_restores_and_records(self):
        self.win_env()
        before = dict(self.registry.keys)

        class Crashing(FakeChrome):
            def __init__(s, *a, **k):
                super().__init__()

            def start(s):
                raise RuntimeError("DevTools")
        doc, out = self.run_win(session_factory=Crashing)
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertTrue(doc["driver_errors"])
        self.assertEqual(self.registry.keys, before)
        self.assertFalse((self.state / reg.JOURNAL_NAME).exists())
        self.assertFalse((self.state / reg.LOCK_NAME).exists())

    def test_windows_readback_mismatch_is_cleaned_up_through_real_wiring(self):
        self.win_env()
        before = dict(self.registry.keys)
        self.registry.readback = "C:\\other.json"
        doc, out = self.run_win()
        self.assertIn("registration refused", doc["blocker"])
        self.registry.readback = None
        self.assertNotIn(reg.NAME_PREFIX, "".join(self.registry.keys))
        self.assertEqual(self.registry.keys, before)
        self.assertFalse((self.state / reg.JOURNAL_NAME).exists())
        self.assertFalse((self.state / reg.LOCK_NAME).exists())
        self.assertEqual(self.sessions[0].started, False)  # no action ran on a doubtful registration

    def test_windows_unrestorable_registry_keeps_journal_lock_and_workdir_for_recovery(self):
        self.win_env()
        self.registry.fail["delete"] = PermissionError("denied")
        doc, out = self.run_win()
        self.assertIn("REGISTRATION NOT RESTORED", doc["blocker"])
        self.assertTrue((self.state / reg.JOURNAL_NAME).exists())
        self.assertTrue((self.state / reg.LOCK_NAME).exists(), "the lock stays until recovery: no second run may start")
        leftover = list((self.tmp / "tmp").iterdir())
        self.assertEqual(len(leftover), 1)
        self.registry.fail.clear()
        out2 = reg.recover(self.registry, self.state, self.tmp / "tmp", confirm_no_active_run=True)
        self.assertTrue(out2["restored"] and out2["lock_cleared"] and out2["work_dir_removed"])
        self.assertEqual(list((self.tmp / "tmp").iterdir()), [])

    def test_windows_crash_then_blocked_then_recovered_then_clean_run(self):
        self.win_env()
        before = dict(self.registry.keys)
        self.registry.fail["delete"] = PermissionError("denied")  # run 1 ends like a crash: cleanup cannot remove the key
        doc1, _ = self.run_win()
        self.assertIn("REGISTRATION NOT RESTORED", doc1["blocker"])
        self.registry.fail.clear()
        stale = [k for k in self.registry.keys if k.startswith(reg.NAME_PREFIX)]
        self.assertEqual(len(stale), 1)
        doc2, _ = self.run_win()  # run 2: must refuse and must not touch run 1's key, journal or lock
        self.assertIn("startup refused", doc2["blocker"])
        self.assertEqual([k for k in self.registry.keys if k.startswith(reg.NAME_PREFIX)], stale)
        self.assertTrue((self.state / reg.JOURNAL_NAME).exists() and (self.state / reg.LOCK_NAME).exists())
        reg.recover(self.registry, self.state, self.tmp / "tmp", confirm_no_active_run=True)
        self.assertEqual(self.registry.keys, before)
        doc3, _ = self.run_win()  # run 3: clean
        self.assertNotIn("startup refused", doc3.get("blocker", ""))
        self.assertEqual(self.registry.keys, before)
        self.assertFalse((self.state / reg.LOCK_NAME).exists())

    def test_windows_second_run_cannot_start_while_the_first_holds_the_lock(self):
        self.win_env()
        reg.ensure_state_dir(self.state)
        first = reg.RunLock(self.state)
        first.acquire()
        doc, _ = self.run_win()
        self.assertIn("startup refused", doc["blocker"])
        self.assertTrue((self.state / reg.LOCK_NAME).exists(), "the second run must not remove the first run's lock")
        first.release()
        self.assertFalse((self.state / reg.LOCK_NAME).exists())

    def test_missing_localappdata_is_a_clean_blocked_record(self):
        self.win_env()
        self.env = {"PATH": "x"}
        out = self.tmp / "nolocal"
        def fake_run(argv):
            if argv[0] == "powershell" and "Get-PnpDevice" in argv[-1]:
                return 0, WINDOWS_ADAPTER_OUTPUT, ""
            return 0, "NotSigned", ""
        doc = runner.execute_case(CASES["windows.none.ready-hygiene"], self.host, "C:\\c.exe", out, self.anchor(self.host), family="windows",
                                  run=fake_run, backend_factory=lambda: self.registry, session_factory=self.Sess, env=self.env,
                                  temp_root=str(self.tmp / "tmp"))
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("LOCALAPPDATA", doc["blocker"])
        self.assertEqual(self.registry.keys, {"Pre.Existing": "C:\\a.json"})


class CliTests(Tmp):
    def cli(self, *args):
        return subprocess.run([sys.executable, "-I", "-B", str(HERE / "lifecycle-driver.py"), *args], capture_output=True, text=True, timeout=300)

    def test_print_origin_matches_extension(self):
        p = self.cli("--print-origin")
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout)
        self.assertEqual(out["origin"], ORIGIN)
        self.assertIn(f"VELES_ALLOWED_ORIGINS={ORIGIN}", out["build"])

    def test_gaps_are_listed(self):
        p = self.cli("--list-gaps")
        self.assertEqual(p.returncode, 0)
        for needle in ("phones", "pipe tap", "hygiene monitors", "only been exercised through fakes"):
            self.assertIn(needle, p.stdout)

    def test_usage_and_refusals(self):
        self.assertEqual(self.cli().returncode, 3)
        self.assertEqual(self.cli("--case", "nope", "--host", "/x", "--chrome", "", "--output", str(self.tmp / "o")).returncode, 3)
        self.assertEqual(self.cli("--case", "macos.none.ready-hygiene", "--host", "rel/veles-native-host", "--chrome", "", "--output", str(self.tmp / "o")).returncode, 3)
        self.assertFalse((self.tmp / "o").exists())

    @unittest.skipIf(sys.platform in ("win32", "darwin"), "this assertion is about a machine that is neither target platform")
    def test_without_a_pinned_anchor_a_blocked_record_is_written(self):
        host = self.exe()
        p = self.cli("--case", "macos.none.ready-hygiene", "--host", str(host), "--chrome", "/nonexistent", "--output", str(self.tmp / "o"))
        self.assertEqual(p.returncode, 2, p.stderr)
        doc = json.loads((self.tmp / "o" / "run.json").read_text())
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("PINNED anchor", doc["blocker"])
        self.assertEqual(doc["captures"], [])

    def test_recover_registration_blocks_off_windows_and_needs_confirmation(self):
        if sys.platform != "win32":
            for args in (("--recover-registration",), ("--recover-registration", "--confirm-no-active-run")):
                self.assertEqual(self.cli(*args).returncode, 2, args)

    def load_cli(self):
        spec = importlib.util.spec_from_file_location("lifecycle_driver_cli", HERE / "lifecycle-driver.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_exit_codes_separate_driver_errors_from_product_failures(self):
        from unittest import mock
        cli = self.load_cli()
        host = self.exe()
        base = ["--case", "macos.none.ready-hygiene", "--host", str(host), "--chrome", "", "--output", str(self.tmp / "o")]
        anchor = {"anything": 1}
        import contextlib
        import io
        quiet = contextlib.ExitStack()
        quiet.enter_context(contextlib.redirect_stdout(io.StringIO()))
        quiet.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.addCleanup(quiet.close)
        with mock.patch.object(cli, "resolve_anchor", return_value=(anchor, False)):
            for doc, code in (({"declared_status": "FAIL"}, 1), ({"declared_status": "BLOCKED"}, 2),
                              ({"declared_status": "BLOCKED", "driver_errors": [{"stage": "x"}]}, 5)):
                with mock.patch.object(cli.runner, "execute_case", return_value=doc):
                    self.assertEqual(cli.main(base), code, doc)
            for exc in (RuntimeError("cdp"), subprocess.TimeoutExpired("ps", 1), PermissionError("winreg"), ValueError.__mro__[1]("x")):
                with mock.patch.object(cli.runner, "execute_case", side_effect=exc):
                    self.assertEqual(cli.main(base), 5 if not isinstance(exc, ValueError) else 3, repr(exc))
            with mock.patch.object(cli.runner, "execute_case", side_effect=Refused("no")):
                self.assertEqual(cli.main(base), 3)

    def test_gitignore_keeps_bytecode_out_and_no_pycache_is_shipped(self):
        ignore = (HERE.parent / ".gitignore").read_text().split()
        self.assertIn("__pycache__/", ignore)
        self.assertIn("*.pyc", ignore)

    def test_physical_matrix_driver_stays_non_executable(self):
        spec = importlib.util.spec_from_file_location("pm", HERE / "physical-matrix.py")
        pm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pm)
        self.assertFalse(any(d.executable for d in pm.DRIVERS.values()))
        matrix = json.loads((HERE.parent / "acceptance" / "matrix.json").read_text())
        self.assertEqual(matrix["decision"], "OPEN")
        self.assertTrue(all(r["status"] in ("BLOCKED", "NOT RUN") for r in matrix["rows"]))


if __name__ == "__main__":
    unittest.main()
