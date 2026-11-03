"""Deterministic tests for accdrv/rig.py. No adb, phone, Chrome, registry, signing or network: a simulated World stands in for
the two phones (adb runner) and for the acceptance extension (Chrome `js`). Time is a single simulated clock.

The World is deliberately adversarial where it matters: it can mis-route, drop, duplicate, corrupt or broadcast bytes, hide
the fixture, and fail a link. A passing test therefore means the rig detected (or correctly accepted) that behaviour.
"""
import base64
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from accdrv import lifecycle, rig as R  # noqa: E402
from accdrv.errors import Blocked, Refused  # noqa: E402
from accdrv.recorder import ProductFailure, Recorder  # noqa: E402
from test_driver import (AutoPhaseChrome, CASES, EXT_ID, HOST_NAME, ORIGIN, RecordingRegistration, ScriptedObserver,  # noqa: E402
                         load_validator)

PKG = R.DEFAULT_PACKAGE
SERIALS = {"A": "SERIALAAAA1", "B": "SERIALBBBB2"}
SKEW = 5000


def cert_of(apk):
    return hashlib.sha256(b"CERT:" + apk.split(b"|")[0]).hexdigest()


EXPECTED_CERT = cert_of(b"SIGNER1|x")


class World:
    """Simulated phones + connector. `order` is the order scan results are reported (first candidate gets connected first)."""

    def __init__(self, order=("B", "A")):
        self.t = 1_800_000_000_000.0
        self.order = list(order)
        self.argv_log = []
        self.js_log = []
        self.phones = {s: {"serial": SERIALS[s], "records": [], "states": [{"epoch_ms": int(self.t + SKEW), "peers": []}], "ready": True,
                           "running": True, "apk": b"SIGNER1|" + s.encode(), "versions": "    versionCode=1 minSdk=33\n    versionName=0.0.1\n"}
                       for s in SERIALS}
        self.events, self.conn_slot, self.route, self.pending = [], {}, {}, []
        self.port_open, self.armed, self.scan_error, self.condition = True, None, None, None
        self.log, self.page_alive = [], True  # `log` = host-delivered completions only (the connector's completionLog)
        self.drop = self.dup = self.corrupt = self.broadcast = False
        self.clock_jitter = 0
        self.ready_content = "r1\n"
        self.foreign = {}
        self.fixed_shell_output = {}

    # time ------------------------------------------------------------------------------------------------------------------
    def now(self):
        self.t += 1
        return self.t

    def sleep(self, seconds):
        self.t += seconds * 1000

    def clock_s(self):
        return self.now() / 1000

    def rec_clock(self):
        return R.iso(self.now())

    # adb --------------------------------------------------------------------------------------------------------------------
    def runner(self, argv):
        self.argv_log.append(list(argv))
        assert argv[0] == "/opt/adb" and argv[1] == "-s", argv
        slot = next(s for s, p in self.phones.items() if p["serial"] == argv[2])
        ph, args = self.phones[slot], tuple(argv[3:])
        if args in self.fixed_shell_output:
            return R.CmdResult(*self.fixed_shell_output[args])
        if args == ("shell", "run-as", PKG, "cat", "files/otp23-a01-ready"):
            return R.CmdResult(0, self.ready_content.encode(), b"")
        if args == ("shell", "run-as", PKG, "ls", "files"):
            return R.CmdResult(0, (b"otp23-a01-ready\n" if ph["ready"] else b""), b"")
        if args == ("shell", "run-as", PKG, "cat", "files/otp23-a01-server.json"):
            doc = {"run_id": "r1", "status": "RUNNING" if ph["running"] else "CAPTURED", "states": ph["states"], "records": ph["records"]}
            return R.CmdResult(0, json.dumps(doc).encode(), b"")
        if args == ("shell", "pm", "path", PKG):
            return R.CmdResult(0, b"package:/data/app/~~abc==/me.nagaev.veles.otp23-xyz==/base.apk\n", b"")
        if args[:2] == ("exec-out", "cat"):
            return R.CmdResult(0, ph["apk"], b"")
        if args == ("shell", "dumpsys", "package", PKG):
            return R.CmdResult(0, ph["versions"].encode(), b"")
        if args == ("shell", "date", "+%s%3N"):
            return R.CmdResult(0, str(int(self.now() + SKEW + self.clock_jitter * (len(self.argv_log) % 3))).encode() + b"\n", b"")
        raise AssertionError(f"unmodelled adb command {args}")

    # connector --------------------------------------------------------------------------------------------------------------
    def phone_record(self, slot, data):
        self.phones[slot]["records"].append({"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "epoch_ms": int(self.now() + SKEW)})

    @staticmethod
    def peer_of(slot):
        return hashlib.sha256(slot.encode()).hexdigest()[:16]

    def host_event(self, **kw):
        """Append one host-delivered completion (this is what the connector logs; synthesized errors are never logged)."""
        self.log.append({"seq": len(self.log), "at": "t", "type": "error", "request_id": "r-armed", "connection_handle": "conn-A", "code": None,
                         "stage": None, **kw})

    def resolve_armed_by_host(self, code, stage, **kw):
        self.host_event(code=code, stage=stage, **kw)
        self.armed, self.pending = {"type": "error", "request_id": "r-armed", "code": code, "stage": stage}, []

    def stop(self, slot):
        """Like the fixture's stop file: the server closes and the document becomes CAPTURED WITHOUT a final state."""
        self.phones[slot]["running"] = False
        if slot == "A" and self.armed == "pending":
            self.resolve_armed_by_host("send_failed", "link_lost")

    def release(self, slot):
        self.sleep(0.5)  # a real release is never simultaneous with its trigger
        self.phones[slot]["states"].append({"epoch_ms": int(self.now() + SKEW), "peers": list(self.foreign.get(slot, []))})
        if slot == "A" and self.armed == "pending":
            self.resolve_armed_by_host("send_failed", "link_lost")

    def js(self, expr):
        self.js_log.append(expr)
        if expr == "velesAcceptance.closePort()":
            self.port_open = False
            if self.armed == "pending":  # the connector synthesizes this itself: it is NOT in the host log
                self.armed = {"type": "error", "request_id": "r-armed", "code": "internal", "stage": "port_closed", "synthesized": True}
                self.pending = []
            return None
        if expr == "typeof window.velesAcceptance === 'object'":
            return self.page_alive
        if expr == "velesAcceptance.completionLog()":
            return list(self.log)
        if expr == "window.__armedS.settled":
            return self.armed != "pending"
        if "window.__armedA = " in expr:
            self.armed, self.pending = "pending", ["r-armed"]
            return True
        if "window.__armedA ?" in expr:
            return self.armed if isinstance(self.armed, dict) else {"type": "still_pending"}
        if expr.endswith("snapshot().portOpen"):
            return self.port_open
        if expr.endswith("snapshot()"):
            return {"portOpen": self.port_open, "pending": list(self.pending), "disconnect": None}
        if "startScan(" in expr:
            if self.scan_error or self.condition:
                return {"type": "error", "code": "scan_failed", "stage": "radio_off", "recoverability": "retryable"}
            for s in self.order:
                if self.phones[s]["ready"]:
                    self.events.append({"at": "t", "message": {"type": "device_found", "device_handle": f"dev-{s}", "label": "Veles", "scan_handle": "scan1"},
                                        "orphan_correlated": False})
            return {"type": "scan_state", "status": "started", "scan_handle": "scan1"}
        if "disconnectDevice(" in expr:
            self.disconnected = getattr(self, "disconnected", []) + [expr]
            return {"type": "disconnected"}
        if "stopScan(" in expr:
            return {"type": "scan_state", "status": "stopped", "scan_handle": "scan1"}
        if "drainEvents()" in expr:
            out, self.events = self.events, []
            return out
        m = re.search(r'connectDevice\(\{device_handle: "([^"]+)"\}\)', expr)
        if m:
            slot = m.group(1).split("-")[1]
            conn = f"conn-{slot}"
            self.conn_slot[conn] = slot
            self.phones[slot]["states"].append({"epoch_ms": int(self.now() + SKEW), "peers": [self.peer_of(slot)] + list(self.foreign.get(slot, []))})
            return {"type": "connected", "connection_handle": conn}
        m = re.search(r'send\("([^"]+)", Uint8Array\.from\(atob\("([^"]*)"\)', expr)
        if m:
            conn, data = m.group(1), base64.b64decode(m.group(2))
            if len(data) == 0 or len(data) > 4096:
                return {"type": "error", "code": "invalid_request", "stage": "schema", "recoverability": "retryable"}
            slot = self.route.get(conn, self.conn_slot[conn])
            if self.corrupt:
                data = bytes([data[0] ^ 1]) + data[1:]
            if not self.drop:
                self.phone_record(slot, data)
                if self.dup:
                    self.phone_record(slot, data)
                if self.broadcast:
                    self.phone_record("A" if slot == "B" else "B", data)
            if len(data) >= 4:
                self.events.append({"at": "t", "message": {"type": "message", "connection_handle": conn, "record_length": R.b64len(len(data))},
                                    "orphan_correlated": False})
            return {"type": "sent", "connection_handle": conn}
        raise AssertionError(f"unmodelled js {expr[:80]}")


class WorldChrome(AutoPhaseChrome):
    """Lifecycle-level Chrome: rig expressions go to the World, hello/connect/shutdown stay with the shared FakeChrome."""

    def __init__(self, observer, world, **kw):
        super().__init__(observer, **kw)
        self.world = world

    def js(self, session, expr, timeout=60):
        if "hello()" in expr or ("connect(" in expr and "connectDevice" not in expr):
            return super().js(session, expr, timeout)
        if expr == "velesAcceptance.closePort()":
            for s_ in ("A", "B"):  # the host sees EOF and exits, which releases both BLE links
                if self.world.phones[s_]["states"][-1]["peers"]:
                    self.world.release(s_)
            return self.world.js(expr)
        if "shutdown()" in expr:
            if self.world.armed == "pending" and getattr(self.world, "shutdown_without_error", False):
                # product violation simulated: the host completes shutdown without any error for the pending request; the page's
                # connector then ends the request itself (synthesized, local, never logged)
                self.world.armed = {"type": "error", "request_id": "r-armed", "code": "internal", "stage": "port_closed", "synthesized": True}
                self.world.pending = []
            elif self.world.armed == "pending":
                self.world.resolve_armed_by_host("internal", "shutdown", connection_handle=None)  # host-emitted, BEFORE shutdown_complete
            self.world.host_event(type="shutdown_complete", request_id="r-shutdown", connection_handle=None)
            self.world.port_open = False
            for s in ("A", "B"):
                if self.world.phones[s]["states"][-1]["peers"]:
                    self.world.release(s)
            return {"type": "shutdown_complete"}
        if expr == "velesAcceptance.snapshot()" and not self.world.port_open:
            return {"portOpen": False, "pending": [], "disconnect": {"at": "t", "message": None}}
        return self.world.js(expr)


class Conditions:
    def __init__(self, world, verify=True):
        self.world, self.verify, self.calls = world, verify, []

    def apply(self, name):
        self.calls.append(("apply", name))
        self.world.condition = name

    def restore(self, name):
        self.calls.append(("restore", name))
        self.world.condition = None

    def verify_restored(self, name):
        self.calls.append(("verify", name))
        return self.verify


class RigBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.world = World()
        self.n = 0

    def config(self, serials=SERIALS, **kw):
        return R.RigConfig(serials, EXPECTED_CERT, run_id="r1", **kw)

    def make_rig(self, cfg=None, **kw):
        cfg = cfg or self.config()
        adb = R.SafeAdb(self.world.runner, "/opt/adb", cfg)
        return R.PhoneRig(adb, cfg, cert_of_apk, wall_ms=self.world.now, **kw)

    def ctx(self, case_id, rig, **kw):
        self.n += 1
        case = CASES[case_id]
        rec = Recorder(self.tmp / f"out{self.n}", case, clock=self.world.rec_clock)
        rec.fields["binary"] = {"sha256": "c" * 64}
        obs = kw.pop("observer", None) or ScriptedObserver("h")
        chrome = kw.pop("chrome", None) or WorldChrome(obs, self.world)
        ctx = lifecycle.Ctx(case, rec, chrome=chrome, observer=obs, host_exe="/x/veles-native-host", host_name=HOST_NAME, ext_dir="/ext",
                            ext_id=EXT_ID, origin=ORIGIN, rig=rig, clock=self.world.clock_s, sleep=self.world.sleep, **kw)
        return ctx, {"session": "S1"}

    def sent_count(self):
        return sum(1 for e in self.world.js_log if "velesAcceptance.send(" in e)


def cert_of_apk(apk_bytes):
    return cert_of(apk_bytes)


AB_TRANSFER = "windows.AB.transfer-boundaries"
AB_FAIL = "windows.AB.per-phone-failure"
AB_SHUTDOWN = "windows.AB.lifecycle-explicit-shutdown"
AB_PORT = "windows.AB.lifecycle-port-disconnect"


# ---- the adb allowlist ---------------------------------------------------------------------------------------------------------
class SafeAdbTests(RigBase):
    def adb(self):
        return R.SafeAdb(self.world.runner, "/opt/adb", self.config())

    def test_every_read_only_form_is_accepted(self):
        adb = self.adb()
        for args in (("shell", "getprop", "ro.product.model"), ("shell", "date", "+%s%3N"), ("shell", "pm", "path", PKG),
                     ("shell", "dumpsys", "package", PKG), ("shell", "run-as", PKG, "cat", "files/otp23-a01-server.json"),
                     ("shell", "run-as", PKG, "cat", "files/otp23-a01-ready"), ("shell", "run-as", PKG, "ls", "files"),
                     ):
            adb.check(args)
        path = adb.apk_path("A")  # pm path first; only that exact path may then be pulled
        adb.check(("exec-out", "cat", path), "A")

    def test_everything_that_changes_a_device_is_refused_before_the_runner_runs(self):
        adb = self.adb()
        bad = [("install", "-r", "x.apk"), ("uninstall", PKG), ("shell", "pm", "clear", PKG), ("shell", "pm", "uninstall", PKG),
               ("shell", "rm", "-rf", "/sdcard"), ("shell", "svc", "bluetooth", "disable"), ("shell", "am", "force-stop", PKG),
               ("shell", "am", "start", "-n", "x/y"), ("push", "a", "/sdcard/a"), ("pull", "/sdcard/a", "."), ("reboot",), ("kill-server",),
               ("root",), ("remount",), ("shell", "settings", "put", "global", "x", "1"), ("shell", "run-as", PKG, "rm", "files/otp23-a01-server.json"),
               ("shell", "run-as", PKG, "touch", "files/otp23-a01-stop"), ("shell", "run-as", "other.pkg", "cat", "files/otp23-a01-server.json"),
               ("shell", "run-as", PKG, "cat", "files/../shared_prefs/x.xml"), ("shell", "run-as", PKG, "cat", "files/otp23-a01-command.json"),
               ("shell", "run-as", PKG, "cat", "databases/bank_handler_configs.db"), ("shell", "dumpsys", "package", "com.other.app"),
               ("shell", "pm", "path", "com.other.app"), ("shell", "getprop", "persist.sys.something"), ("shell", "date", "-s", "2020"),
               ("exec-out", "cat", "/sdcard/secret.txt"), ("exec-out", "cat", "/data/app/../shadow/base.apk"), ("exec-out", "cat", "/data/data/x/files/y"),
               ("exec-out", "screencap"), ("shell", "run-as", PKG, "cat", "files/otp23-a01-server.json;rm"), ("shell", "sh", "-c", "id"), ("shell",), ()]
        for args in bad:
            before = len(self.world.argv_log)
            with self.assertRaises(Refused, msg=str(args)):
                adb("A", *args)
            self.assertEqual(len(self.world.argv_log), before, f"runner was reached for {args}")

    def test_adb_path_must_be_absolute_and_serial_slot_must_exist(self):
        with self.assertRaises(Refused):
            R.SafeAdb(self.world.runner, "adb", self.config())
        with self.assertRaises(KeyError):
            self.adb()("C", "shell", "date", "+%s%3N")

    def test_config_rejects_unsafe_input(self):
        for serials in ({"A": "SAME12345", "B": "SAME12345"}, {"A": "x"}, {"C": "SERIALCCCC1"}, {}, {"A": "bad serial;"}):
            with self.assertRaises(Refused, msg=str(serials)):
                R.RigConfig(serials, EXPECTED_CERT)
        for cert in ("", "abc", "G" * 64, None):
            with self.assertRaises(Refused):
                R.RigConfig(SERIALS, cert)
        with self.assertRaises(Refused):
            R.RigConfig(SERIALS, EXPECTED_CERT, package="not a package")

    def test_apksigner_parser_requires_exactly_one_signer_and_success(self):
        ok = R.CmdResult(0, f"Signer #1 certificate DN: CN=x\nSigner #1 certificate SHA-256 digest: {'a' * 64}\n".encode(), b"")
        self.assertEqual(R.parse_apksigner(ok), "a" * 64)
        two = R.CmdResult(0, ok.out + f"Signer #2 certificate SHA-256 digest: {'b' * 64}\n".encode(), b"")
        for bad in (two, R.CmdResult(1, ok.out, b""), R.CmdResult(0, b"", b""), R.CmdResult(0, b"Signer #1 certificate SHA-256 digest: ABC\n", b"")):
            with self.assertRaises(Blocked):
                R.parse_apksigner(bad)

    def test_apksigner_reader_deletes_its_scratch_file_and_never_overwrites(self):
        seen = []

        def runner(argv):
            seen.append(argv)
            self.assertTrue(Path(argv[-1]).read_bytes() == b"APK")
            return R.CmdResult(0, f"Signer #1 certificate SHA-256 digest: {'c' * 64}\n".encode(), b"")
        reader = R.ApksignerCertReader(runner, "/opt/apksigner", self.tmp)
        self.assertEqual(reader(b"APK"), "c" * 64)
        self.assertEqual(list(self.tmp.glob("apk-*")), [])
        self.assertEqual(seen[0][:3], ["/opt/apksigner", "verify", "--print-certs"])


# ---- phone binding ---------------------------------------------------------------------------------------------------------------
class BindingTests(RigBase):
    def bind(self, rig=None):
        rig = rig or self.make_rig()
        ctx, st = self.ctx(AB_TRANSFER, rig)
        rig.bind_phones(ctx, st)
        return rig, ctx, st

    def test_binding_records_exact_source_apk_cert_version_and_measured_clock(self):
        rig, ctx, st = self.bind()
        phones = {p["slot"]: p for p in ctx.rec.fields["phones"]}
        self.assertEqual(set(phones), {"A", "B"})
        for slot, p in phones.items():
            self.assertRegex(p["source_hash"], r"^[0-9a-f]{64}$")
            self.assertNotEqual(p["source_hash"], hashlib.sha256((R.SOURCE_CONTEXT + SERIALS[slot]).encode()).hexdigest())  # not a public-salt hash
            self.assertEqual(p["apk_sha256"], hashlib.sha256(self.world.phones[slot]["apk"]).hexdigest())
            self.assertEqual(p["apk_cert_sha256"], EXPECTED_CERT)
            self.assertEqual(p["fixture_version"], "0.0.1+1")
            self.assertAlmostEqual(p["clock_offset_ms"], SKEW, delta=1)
            self.assertLess(p["clock_uncertainty_ms"], 5)
        self.assertNotEqual(phones["A"]["source_hash"], phones["B"]["source_hash"])
        self.assertNotEqual(phones["A"]["apk_sha256"], phones["B"]["apk_sha256"])

    def test_serials_never_reach_the_evidence_directory(self):
        rig, ctx, st = self.bind()
        ctx.rec.finalize()
        blob = b"".join(p.read_bytes() for p in ctx.rec.out.rglob("*") if p.is_file())
        for serial in SERIALS.values():
            self.assertNotIn(serial.encode(), blob)

    def test_binding_satisfies_the_real_validators_phone_checks(self):
        rig, ctx, st = self.bind()
        ve = load_validator()
        checker = ve.Checker(None, {"phones": ctx.rec.fields["phones"]}, CASES["windows.AB.lifecycle-explicit-shutdown"], None, None)
        checker.check_phones()
        self.assertEqual(checker.defects, [])

    def test_certificate_mismatch_is_refused_and_nothing_was_changed(self):
        self.world.phones["B"]["apk"] = b"OTHERSIGNER|B"
        rig = self.make_rig()
        ctx, st = self.ctx(AB_TRANSFER, rig)
        with self.assertRaises(Refused) as e:
            rig.bind_phones(ctx, st)
        self.assertIn("no replacement", str(e.exception))
        self.assertNotIn("phones", ctx.rec.fields)
        self.assertTrue(self.world.argv_log)

    def test_expected_apk_hash_mismatch_is_refused(self):
        rig = self.make_rig(self.config(expected_apk_sha256="0" * 64))
        ctx, st = self.ctx(AB_TRANSFER, rig)
        with self.assertRaises(Refused):
            rig.bind_phones(ctx, st)

    def test_missing_fixture_is_blocked_not_started_by_the_driver(self):
        self.world.phones["B"]["ready"] = False
        rig = self.make_rig()
        ctx, st = self.ctx(AB_TRANSFER, rig)
        with self.assertRaises(Blocked) as e:
            rig.bind_phones(ctx, st)
        self.assertIn("ready file absent", str(e.exception))

    def test_unneeded_phone_without_fixture_is_tolerated(self):
        self.world.phones["B"]["ready"] = False
        rig = self.make_rig()
        ctx, st = self.ctx("windows.A.scanning-hygiene", rig)
        rig.bind_phones(ctx, st)
        self.assertEqual([p["slot"] for p in ctx.rec.fields["phones"]], ["A"])

    def test_split_or_missing_apk_listings_are_blocked(self):
        for out in (b"package:/data/app/x/base.apk\npackage:/data/app/x/split_a.apk\n", b"", b"package:/sdcard/evil.apk\n"):
            self.world.fixed_shell_output[("shell", "pm", "path", PKG)] = (0, out, b"")
            rig = self.make_rig()
            ctx, st = self.ctx(AB_TRANSFER, rig)
            with self.assertRaises(Blocked):
                rig.bind_phones(ctx, st)

    def test_ambiguous_fixture_version_is_blocked(self):
        self.world.fixed_shell_output[("shell", "dumpsys", "package", PKG)] = (0, b"versionName=1\nversionName=2\nversionCode=1\n", b"")
        rig = self.make_rig()
        ctx, st = self.ctx(AB_TRANSFER, rig)
        with self.assertRaises(Blocked):
            rig.bind_phones(ctx, st)

    def test_unstable_or_malformed_phone_clock_is_blocked(self):
        self.world.clock_jitter = 400
        rig = self.make_rig()
        ctx, st = self.ctx(AB_TRANSFER, rig)
        with self.assertRaises(Blocked) as e:
            rig.bind_phones(ctx, st)
        self.assertIn("unstable", str(e.exception))
        self.world.clock_jitter = 0
        self.world.fixed_shell_output[("shell", "date", "+%s%3N")] = (0, b"1700000000\n", b"")
        rig = self.make_rig()
        ctx, st = self.ctx(AB_TRANSFER, rig)
        with self.assertRaises(Blocked):
            rig.bind_phones(ctx, st)

    def test_fixture_run_id_mismatch_or_stopped_fixture_is_blocked(self):
        self.world.phones["A"]["running"] = False
        rig = self.make_rig()
        ctx, st = self.ctx(AB_TRANSFER, rig)
        with self.assertRaises(Blocked):
            rig.bind_phones(ctx, st)
        self.world.phones["A"]["running"] = True
        rig = self.make_rig(R.RigConfig(SERIALS, EXPECTED_CERT, run_id="other"))
        ctx, st = self.ctx(AB_TRANSFER, rig)
        with self.assertRaises(Blocked):
            rig.bind_phones(ctx, st)

    def test_fixture_parser_rejects_malformed_documents(self):
        def res(doc):
            return R.CmdResult(0, json.dumps(doc).encode(), b"")
        good = {"run_id": "r", "status": "RUNNING", "records": [], "states": []}
        R.parse_fixture(res(good), "r")
        for bad in ({**good, "status": "ERROR"}, {**good, "records": [{"bytes": 1}]}, {**good, "states": [{"epoch_ms": 1}]}, [1],
                    {**good, "records": [{"bytes": "1", "epoch_ms": 1, "sha256": "a" * 64}]}):
            with self.assertRaises(Blocked):
                R.parse_fixture(res(bad), "r")
        with self.assertRaises(Blocked):
            R.parse_fixture(R.CmdResult(0, b"not json", b""), "r")
        with self.assertRaises(Blocked):
            R.parse_fixture(R.CmdResult(1, b"", b"x"), "r")


# ---- connections are bound only by bytes ---------------------------------------------------------------------------------------
class ConnectTests(RigBase):
    def connected(self, order=("B", "A"), case=AB_TRANSFER, **kw):
        self.world = World(order)
        rig = self.make_rig(**kw)
        ctx, st = self.ctx(case, rig)
        return rig, ctx, st

    def test_first_candidate_belonging_to_the_other_phone_is_discovered_not_assumed(self):
        rig, ctx, st = self.connected(order=("B", "A"))
        rig.connect_phone_A(ctx, st)
        links = st["rig"]["links"]
        self.assertEqual({s: l["connection"] for s, l in links.items()}, {"A": "conn-A", "B": "conn-B"})  # B was connected first, then A
        rig.connect_phone_B(ctx, st)  # already proven: no further connection
        self.assertEqual(len([e for e in self.world.js_log if "connectDevice" in e]), 2)

    def test_binding_ignores_scan_order_and_labels(self):
        for order in (("A", "B"), ("B", "A")):
            rig, ctx, st = self.connected(order=order)
            rig.connect_phone_B(ctx, st)
            rig.connect_phone_A(ctx, st)
            self.assertEqual({s: l["connection"] for s, l in st["rig"]["links"].items()}, {"A": "conn-A", "B": "conn-B"})

    def test_every_challenge_is_unique_and_logged_with_hashes_only(self):
        rig, ctx, st = self.connected()
        rig.connect_phone_A(ctx, st)
        shas = [p["challenge_sha256"] for p in st["rig"]["proofs"]]
        self.assertEqual(len(shas), len(set(shas)))
        self.assertEqual(len(shas), 2)
        for name in ("bind-1", "bind-2"):
            text = (ctx.rec.out / "captures" / name).read_text()
            self.assertIn("challenge_sha256", text)
            self.assertNotIn("record_base64", text)

    def test_bytes_that_no_phone_logged_leave_the_connection_unbound(self):
        rig, ctx, st = self.connected()
        self.world.drop = True
        with self.assertRaises(Blocked) as e:
            rig.connect_phone_A(ctx, st)
        self.assertIn("bound to exactly one phone by bytes", str(e.exception))
        self.assertEqual(st["rig"]["links"], {})

    def test_bytes_that_two_phones_logged_leave_the_connection_unbound(self):
        rig, ctx, st = self.connected()
        self.world.broadcast = True
        with self.assertRaises(Blocked):
            rig.connect_phone_A(ctx, st)
        self.assertEqual(st["rig"]["links"], {})

    def test_duplicate_challenge_delivery_is_not_accepted_as_a_binding(self):
        rig, ctx, st = self.connected()
        self.world.dup = True
        with self.assertRaises(Blocked):
            rig.connect_phone_A(ctx, st)

    def test_rig_started_scan_is_stopped_once_every_needed_link_exists(self):
        rig, ctx, st = self.connected()
        rig.connect_phone_A(ctx, st)
        self.assertNotIn("scan_handle", st)
        self.assertEqual(sum("stopScan" in e for e in self.world.js_log), 1)

    def test_a_scan_started_by_the_case_is_left_to_the_case(self):
        rig, ctx, st = self.connected(case="windows.AB.teardown-hygiene")
        st["scan_handle"] = "scan1"
        self.world.js('velesAcceptance.startScan("u")')  # the case's own start_scan produced these device_found events
        rig.connect_phone_A(ctx, st)
        rig.connect_phone_B(ctx, st)
        self.assertEqual(st["scan_handle"], "scan1")
        self.assertFalse(any("stopScan" in e for e in self.world.js_log))

    def test_connect_error_from_the_host_is_a_product_failure(self):
        rig, ctx, st = self.connected()
        orig = self.world.js

        def js(expr):
            if "connectDevice" in expr:
                return {"type": "error", "code": "connect_failed", "stage": "gatt_connect", "recoverability": "retryable"}
            return orig(expr)
        self.world.js = js
        with self.assertRaises(ProductFailure):
            rig.connect_phone_A(ctx, st)

    def test_no_device_found_before_the_deadline_is_blocked(self):
        rig, ctx, st = self.connected()
        for p in self.world.phones.values():
            p["ready"] = True
        self.world.order = []
        with self.assertRaises(Blocked) as e:
            rig.connect_phone_A(ctx, st)
        self.assertIn("no unclaimed device_found", str(e.exception))

    def test_unsafe_host_handles_are_not_interpolated_into_javascript(self):
        for bad in ('x"; alert(1); "', "a b", "", "x" * 200, 5, None):
            with self.assertRaises(Blocked):
                R.js_handle(bad)

    def test_only_read_only_adb_commands_were_ever_run(self):
        rig, ctx, st = self.connected()
        rig.connect_phone_A(ctx, st)
        self.assertTrue(self.world.argv_log)
        slot_of = {v: k for k, v in SERIALS.items()}
        rig.adb._apk = {s: rig.adb._apk[s] for s in rig.adb._apk}
        for argv in self.world.argv_log:
            if argv[3] == "exec-out":
                self.assertEqual(argv[5], rig.adb._apk[slot_of[argv[2]]])  # only the pm-path-reported APK was ever pulled
            else:
                rig.adb.check(argv[3:], slot_of[argv[2]])


# ---- transfers -----------------------------------------------------------------------------------------------------------------
class TransferTests(RigBase):
    def linked(self, capacity=247, gatt=None):
        self.world = World()
        cap = (lambda slot, conn: {"kind": "att_mtu", "value": capacity}) if capacity else None
        rig = self.make_rig(capacity_probe=cap, gatt_write_probe=gatt)
        ctx, st = self.ctx(AB_TRANSFER, rig)
        rig.connect_phone_A(ctx, st)
        rig.connect_phone_B(ctx, st)
        return rig, ctx, st

    def test_without_a_capacity_probe_nothing_is_sent_and_the_gap_is_named(self):
        rig, ctx, st = self.linked(capacity=None)
        before = self.sent_count()
        with self.assertRaises(Blocked) as e:
            rig.send_boundary_sizes(ctx, st)
        self.assertIn("capacity", str(e.exception))
        self.assertEqual(self.sent_count(), before)

    def test_unusable_capacity_values_are_blocked(self):
        for cap in ({"kind": "att_mtu", "value": 20}, {"kind": "att_mtu", "value": 600}, {"kind": "max_write_without_response", "value": 100},
                    {"kind": "mystery", "value": 1}, None, {"kind": "att_mtu", "value": "247"}):
            with self.assertRaises(Blocked):
                R.budget_from(cap)
        self.assertEqual(R.budget_from({"kind": "att_mtu", "value": 247}), 240)
        self.assertEqual(R.budget_from({"kind": "att_mtu", "value": 517}), 508)
        self.assertEqual(R.budget_from({"kind": "max_write_without_response", "value": 512}), 508)

    def test_boundary_transfers_are_byte_proven_per_phone_and_digests_differ(self):
        rig, ctx, st = self.linked()
        rig.send_boundary_sizes(ctx, st)
        rs = st["rig"]
        self.assertEqual(len(rs["transfers"]), 14)  # 7 sizes x 2 phones
        for t in rs["transfers"]:
            self.assertEqual(t["expected_sha256"], t["received_sha256"])
            self.assertEqual(t["outcome"], "ok")
            self.assertEqual(t["chunk"], 240)
        sizes = {t["label"]: t["size"] for t in rs["transfers"]}
        self.assertEqual((sizes["chunk-1"], sizes["chunk"], sizes["chunk+1"], sizes["multiple-of-chunk"]), (239, 240, 241, 480))
        for label in sizes:
            a = next(t for t in rs["transfers"] if t["slot"] == "A" and t["label"] == label)
            b = next(t for t in rs["transfers"] if t["slot"] == "B" and t["label"] == label)
            self.assertNotEqual(a["expected_sha256"], b["expected_sha256"])
        for r in rs["receipts"]:  # each receipt is backed by exactly one record on that phone and none on the other
            mine = [x for x in self.world.phones[r["slot"]]["records"] if x["sha256"] == r["sha256"]]
            other = [x for s, p in self.world.phones.items() if s != r["slot"] for x in p["records"] if x["sha256"] == r["sha256"]]
            self.assertEqual((len(mine), len(other)), (1, 0))

    def test_recorded_fields_satisfy_the_real_validator_transfer_checks(self):
        rig, ctx, st = self.linked(gatt=lambda slot: 0)
        rig.send_boundary_sizes(ctx, st)
        rig.send_rejected_sizes(ctx, st)
        rig.observe_phone_receipts(ctx, st)
        ve = load_validator()
        manifest = {**{k: ctx.rec.fields[k] for k in ("phones", "links", "transfers", "phone_receipts", "rejections")},
                    "captures": [{"name": c["name"]} for c in ctx.rec.captures]}
        checker = ve.Checker(None, manifest, CASES[AB_TRANSFER], None, None)
        checker.check_phones()
        self.assertEqual(checker.defects, [])

    def test_a_corrupted_delivery_is_a_product_failure(self):
        rig, ctx, st = self.linked()
        self.world.corrupt = True
        with self.assertRaises(ProductFailure):
            rig.send_boundary_sizes(ctx, st)

    def test_a_dropped_delivery_that_the_host_called_sent_is_a_product_failure(self):
        rig, ctx, st = self.linked()
        self.world.drop = True
        with self.assertRaises(ProductFailure) as e:
            rig.send_boundary_sizes(ctx, st)
        self.assertIn("logged no such record", str(e.exception))

    def test_a_misrouted_delivery_is_a_product_failure(self):
        rig, ctx, st = self.linked()
        self.world.route["conn-A"] = "B"
        with self.assertRaises(ProductFailure) as e:
            rig.send_boundary_sizes(ctx, st)
        self.assertIn("logged by phone B", str(e.exception))

    def test_an_unrelated_record_appearing_on_the_other_phone_is_a_product_failure(self):
        rig, ctx, st = self.linked()
        orig = self.world.js

        def js(expr):
            out = orig(expr)
            if "send(" in expr and out.get("type") == "sent":
                self.world.phone_record("B" if "conn-A" in expr else "A", b"stray-bytes")
            return out
        self.world.js = js
        with self.assertRaises(ProductFailure) as e:
            rig.send_boundary_sizes(ctx, st)
        self.assertIn("elsewhere", str(e.exception))

    def test_a_duplicated_delivery_is_a_product_failure(self):
        rig, ctx, st = self.linked()
        self.world.dup = True
        with self.assertRaises(ProductFailure):
            rig.send_boundary_sizes(ctx, st)

    def test_rejected_sizes_without_a_gatt_write_probe_are_blocked_never_asserted_zero(self):
        rig, ctx, st = self.linked()
        with self.assertRaises(Blocked) as e:
            rig.send_rejected_sizes(ctx, st)
        self.assertIn("not observable", str(e.exception))
        self.assertEqual(st["rig"]["rejections"][0]["gatt_writes"], None)

    def test_rejected_sizes_with_a_probe_record_zero_writes_and_error_codes(self):
        rig, ctx, st = self.linked(gatt=lambda slot: 7)
        rig.send_rejected_sizes(ctx, st)
        self.assertEqual([(r["size"], r["gatt_writes"]) for r in st["rig"]["rejections"]], [(0, 0), (4097, 0), (5120, 0)])
        self.assertTrue(all(r["error_code"] == "invalid_request" for r in st["rig"]["rejections"]))

    def test_a_probe_that_sees_writes_for_a_rejected_size_is_a_product_failure(self):
        counter = {"n": 0}

        def probe(slot):
            counter["n"] += 1
            return counter["n"] // 2 * 3  # grows between the before/after reads
        rig, ctx, st = self.linked(gatt=probe)
        with self.assertRaises(ProductFailure) as e:
            rig.send_rejected_sizes(ctx, st)
        self.assertIn("GATT write(s) were observed", str(e.exception))

    def test_a_rejected_size_that_the_host_accepts_is_a_product_failure(self):
        rig, ctx, st = self.linked(gatt=lambda slot: 0)
        orig = self.world.js

        def js(expr):
            if "send(" in expr and "armed" not in expr:
                return {"type": "sent", "connection_handle": "conn-A"}
            return orig(expr)
        self.world.js = js
        with self.assertRaises(ProductFailure):
            rig.send_rejected_sizes(ctx, st)

    def test_a_rejected_size_that_still_reaches_a_phone_is_a_product_failure(self):
        rig, ctx, st = self.linked(gatt=lambda slot: 0)
        orig = self.world.js

        def js(expr):
            m = re.search(r'send\("([^"]+)", Uint8Array\.from\(atob\("([^"]*)"\)', expr)
            if m and len(base64.b64decode(m.group(2))) in (0, 4097, 5120):
                self.world.phone_record("A", b"leak")
                return {"type": "error", "code": "invalid_request", "stage": "schema"}
            return orig(expr)
        self.world.js = js
        with self.assertRaises(ProductFailure):
            rig.send_rejected_sizes(ctx, st)

    def test_receipts_controls_and_fragment_check_pass_only_with_evidence(self):
        rig, ctx, st = self.linked(gatt=lambda slot: 0)
        rig.send_boundary_sizes(ctx, st)
        rig.send_rejected_sizes(ctx, st)
        rig.observe_phone_receipts(ctx, st)
        self.assertEqual({c["name"]: c["result"] for c in ctx.rec.controls},
                         {"per_phone_expected_digest_differs": "pass", "connector_sees_no_fragments": "pass",
                          "rejected_sizes_produce_no_gatt_writes": "pass"})
        names = {c["name"] for c in ctx.rec.captures}
        for c in ctx.rec.controls:
            self.assertIn(c["evidence_ref"], names)

    def test_fragment_sized_connector_messages_are_a_product_failure(self):
        rig, ctx, st = self.linked(gatt=lambda slot: 0)
        rig.send_boundary_sizes(ctx, st)
        self.world.events.append({"at": "t", "message": {"type": "message", "connection_handle": "conn-A", "record_length": 28}})
        with self.assertRaises(ProductFailure) as e:
            rig.observe_phone_receipts(ctx, st)
        self.assertIn("fragments", str(e.exception))

    def test_no_connector_echo_means_the_fragment_claim_is_unproven(self):
        rig, ctx, st = self.linked(gatt=lambda slot: 0)
        rig.send_boundary_sizes(ctx, st)
        st["rig"]["events"] = [e for e in st["rig"]["events"] if e.get("type") != "message"]
        self.world.events = []
        with self.assertRaises(Blocked):
            rig.observe_phone_receipts(ctx, st)

    def test_a_receipt_without_a_phone_record_is_a_product_failure(self):
        rig, ctx, st = self.linked(gatt=lambda slot: 0)
        rig.send_boundary_sizes(ctx, st)
        st["rig"]["receipts"][0]["sha256"] = "f" * 64
        with self.assertRaises(ProductFailure):
            rig.observe_phone_receipts(ctx, st)

    def test_identical_per_phone_digests_fail_the_distinctness_control(self):
        rig, ctx, st = self.linked(gatt=lambda slot: 0)
        rig.send_boundary_sizes(ctx, st)
        for t in st["rig"]["transfers"]:
            t["expected_sha256"] = "d" * 64
        with self.assertRaises(Blocked):  # no rejected size exercised; the failed distinctness control is still recorded first
            rig.observe_phone_receipts(ctx, st)
        self.assertEqual({c["name"]: c["result"] for c in ctx.rec.controls}["per_phone_expected_digest_differs"], "fail")

    def test_slot_seeds_never_collide_and_bind_challenges_are_always_distinct(self):
        for n in range(400):
            nonce = hashlib.sha256(str(n).encode()).hexdigest()[:16]
            a, b = R.seed_for(nonce, "xfer", "chunk", 240, slot="A"), R.seed_for(nonce, "xfer", "chunk", 240, slot="B")
            self.assertNotEqual(R.fixture_record(240, a), R.fixture_record(240, b))
            c1 = R.fixture_record(R.BIND_CHALLENGE_BYTES + 1, R.seed_for(nonce, "bind", 1))
            c2 = R.fixture_record(R.BIND_CHALLENGE_BYTES + 2, R.seed_for(nonce, "bind", 2))
            self.assertNotEqual(hashlib.sha256(c1).digest(), hashlib.sha256(c2).digest())

    def test_header_only_records_cannot_distinguish_phones_and_are_blocked(self):
        rig, ctx, st = self.linked()
        ctx.case = dict(ctx.case, sizes={"accept": [4], "reject_without_writes": []})
        with self.assertRaises(Blocked) as e:
            rig.send_boundary_sizes(ctx, st)
        self.assertIn("identical bytes", str(e.exception))

    def test_fixture_records_are_deterministic_valid_echo_records(self):
        for size in (1, 3, 4, 5, 240, 4096):
            r = R.fixture_record(size, 9)
            self.assertEqual(len(r), size)
            self.assertEqual(r, R.fixture_record(size, 9))
            if size > 4:
                self.assertNotEqual(r, R.fixture_record(size, 10))
        r = R.fixture_record(100, 33)
        self.assertEqual(r[:4], b"VEL\x01")
        seed = (r[4] - 4) & 0xFF
        self.assertTrue(all(r[i] == ((i + seed) & 0xFF) for i in range(4, 100)))  # the fixture's own acceptance rule


# ---- per-phone failure, survivor, link release -------------------------------------------------------------------------------------
class FailureTests(RigBase):
    def linked(self, case=AB_FAIL, failer="auto"):
        self.world = World()
        if failer == "auto":
            failer = lambda slot: self.world.release(slot)  # noqa: E731
        rig = self.make_rig(link_failer=failer)
        ctx, st = self.ctx(case, rig)
        rig.connect_phone_A(ctx, st)
        rig.connect_phone_B(ctx, st)
        rig.prove_bytes_both_links(ctx, st)
        return rig, ctx, st

    def test_prove_bytes_records_a_control_with_per_phone_digests(self):
        rig, ctx, st = self.linked()
        c = {c["name"]: c for c in ctx.rec.controls}["bytes_proven_before_trigger"]
        self.assertEqual(c["result"], "pass")
        proof = json.loads((ctx.rec.out / "captures" / "bytes-proven").read_text())["proofs"]
        self.assertEqual([p["slot"] for p in proof], ["A", "B"])
        self.assertNotEqual(proof[0]["sha256"], proof[1]["sha256"])

    def test_failing_a_link_needs_an_injected_failer_and_sends_nothing_without_it(self):
        rig, ctx, st = self.linked(failer=None)
        before = len(self.world.js_log)
        with self.assertRaises(Blocked):
            rig.fail_phone_A_link(ctx, st)
        self.assertEqual(len(self.world.js_log), before)

    def test_full_per_phone_failure_sequence_observes_cancellation_survivor_and_release(self):
        rig, ctx, st = self.linked()
        rig.fail_phone_A_link(ctx, st)
        rig.observe_pending_cancellation(ctx, st)
        rig.survivor_transfer_phone_B(ctx, st)
        rig.observe_link_release(ctx, st)
        pending = json.loads((ctx.rec.out / "captures" / "pending-cancellation").read_text())
        self.assertEqual(pending["pending_after"], [])
        self.assertEqual(ctx.rec.fields["pending_request_handling"]["terminal"]["stage"], "link_lost")
        rel = json.loads((ctx.rec.out / "captures" / "link-release").read_text())
        self.assertIsNotNone(rel["results"]["A"]["released_at_host_ms"])
        self.assertTrue(rel["results"]["B"]["held"])
        self.assertLess(rel["results"]["A"]["gap_ms"], 1000)
        self.assertEqual({c["name"]: c["result"] for c in ctx.rec.controls}["survivor_progress_after_failure"], "pass")

    def test_survivor_that_receives_nothing_fails_and_records_a_failed_control(self):
        rig, ctx, st = self.linked()
        rig.fail_phone_A_link(ctx, st)
        self.world.drop = True
        with self.assertRaises(ProductFailure):
            rig.survivor_transfer_phone_B(ctx, st)
        self.assertEqual({c["name"]: c["result"] for c in ctx.rec.controls}["survivor_progress_after_failure"], "fail")

    def test_a_host_error_for_the_survivor_send_is_a_product_failure_with_a_failed_control(self):
        rig, ctx, st = self.linked()
        orig = self.world.js

        def js(expr):
            if "send(" in expr and "conn-B" in expr and "__armedA" not in expr:
                return {"type": "error", "code": "send_failed", "stage": "gatt_write"}
            return orig(expr)
        self.world.js = js
        with self.assertRaises(ProductFailure):
            rig.survivor_transfer_phone_B(ctx, st)
        self.assertEqual({c["name"]: c["result"] for c in ctx.rec.controls}["survivor_progress_after_failure"], "fail")

    def test_failed_link_that_is_never_released_is_a_product_failure(self):
        rig, ctx, st = self.linked(failer=lambda slot: None)
        rig.fail_phone_A_link(ctx, st)
        with self.assertRaises(ProductFailure) as e:
            rig.observe_link_release(ctx, st)
        self.assertIn("unreleased=['A']", str(e.exception))

    def test_release_slower_than_the_bound_is_a_product_failure(self):
        rig, ctx, st = self.linked(failer=lambda slot: None)
        rig.fail_phone_A_link(ctx, st)
        self.world.sleep(60)
        self.world.release("A")
        with self.assertRaises(ProductFailure) as e:
            rig.observe_link_release(ctx, st)
        self.assertIn("late=['A']", str(e.exception))

    def test_losing_the_survivor_is_a_product_failure(self):
        rig, ctx, st = self.linked()
        rig.fail_phone_A_link(ctx, st)
        self.world.release("B")
        with self.assertRaises(ProductFailure) as e:
            rig.observe_link_release(ctx, st)
        self.assertIn("survivor_lost=['B']", str(e.exception))

    def test_a_release_that_cannot_be_attributed_is_blocked(self):
        rig, ctx, st = self.linked()
        self.world.release("A")  # released BEFORE the trigger: not caused by it
        self.world.sleep(30)
        rig.fail_phone_A_link(ctx, st)
        with self.assertRaises(Blocked):
            rig.observe_link_release(ctx, st)

    def test_link_release_without_a_trigger_is_blocked(self):
        rig, ctx, st = self.linked()
        with self.assertRaises(Blocked):
            rig.observe_link_release(ctx, st)

    def test_clock_offset_is_applied_to_phone_times(self):
        rig, ctx, st = self.linked()
        trig = self.world.t
        st["trigger"], st["trigger_wall"] = "shutdown_request", R.iso(trig)
        self.world.sleep(1.4)
        self.world.release("A")
        out = rig._release_of(st["rig"], "A", self.world.phones["A"]["states"], trig)
        self.assertGreater(out["gap_ms"], 1800)
        self.assertLess(out["gap_ms"], 2200)  # the 5 s phone skew is removed

    def test_global_trigger_requires_both_links_released(self):
        rig, ctx, st = self.linked(case=AB_SHUTDOWN)
        st["trigger"], st["trigger_wall"] = "shutdown_request", ctx.rec.clock()
        self.world.release("A")  # B is never released
        with self.assertRaises(ProductFailure) as e:
            rig.observe_link_release(ctx, st)
        self.assertIn("unreleased=['B']", str(e.exception))

    def test_global_trigger_with_both_links_released_passes_the_independence_control(self):
        rig, ctx, st = self.linked(case=AB_SHUTDOWN)
        st["trigger"], st["trigger_wall"] = "shutdown_request", ctx.rec.clock()
        self.world.release("A")
        self.world.release("B")
        rig.observe_link_release(ctx, st)
        self.assertEqual({c["name"]: c["result"] for c in ctx.rec.controls}["release_measured_independently_of_process_exit"], "pass")

    def armed_global(self, how="literal_port_disconnect", fast=False):
        """A linked AB case with a request armed (and proven pending) by the case-runner hook, then the port gone."""
        rig, ctx, st = self.linked(case=AB_PORT)
        st["trigger"] = how
        if fast:
            orig = self.world.js

            def js(expr):
                if expr == "window.__armedS.settled":
                    return True  # the request had already completed when we looked
                return orig(expr)
            self.world.js = js
        rig.before_trigger(ctx, st, how)
        self.world.port_open = False
        return rig, ctx, st

    class Tap:
        def __init__(self, requests):
            self.requests = requests

        def pending_cancellation(self):
            return {"requests": self.requests, "source": "test tap"}

    # R1/R2 ----------------------------------------------------------------------------------------------------------------------


# ---- scanning case ----------------------------------------------------------------------------------------------------------------------
class ScanningTests(RigBase):
    def setUp(self):
        super().setUp()
        self.cfg = R.RigConfig({"A": SERIALS["A"]}, EXPECTED_CERT, run_id="r1")

    def run_scan(self, **kw):
        rig = self.make_rig(self.cfg, **kw)
        ctx, st = self.ctx("windows.A.scanning-hygiene", rig)
        st["scan_handle"] = "scan1"
        self.world.js('velesAcceptance.startScan("u")')
        return rig, ctx, st

    def test_scan_result_is_attributed_only_in_the_single_fixture_case_and_needs_the_control(self):
        self.world.order = ["A"]
        rig, ctx, st = self.run_scan()
        with self.assertRaises(Blocked) as e:
            rig.observe_device_found(ctx, st)
        self.assertIn("unrelated_advertiser_not_reported", str(e.exception))
        text = (ctx.rec.out / "captures" / "device-found").read_text()
        self.assertIn("single_fixture_inference", text)

    def test_unrelated_advertiser_control_pass_and_fail(self):
        self.world.order = ["A"]
        rig, ctx, st = self.run_scan(unrelated_advertiser=lambda devices: True)
        rig.observe_device_found(ctx, st)
        self.assertEqual({c["name"]: c["result"] for c in ctx.rec.controls}["unrelated_advertiser_not_reported"], "pass")
        self.world.order = ["A"]
        rig, ctx, st = self.run_scan(unrelated_advertiser=lambda devices: False)
        with self.assertRaises(ProductFailure):
            rig.observe_device_found(ctx, st)

    def test_two_advertisers_cannot_be_attributed_without_a_connection(self):
        cfg = self.config()
        rig = self.make_rig(cfg, unrelated_advertiser=lambda d: True)
        ctx, st = self.ctx("windows.A.scanning-hygiene", rig)
        st["scan_handle"] = "scan1"
        self.world.js('velesAcceptance.startScan("u")')
        with self.assertRaises(Blocked):
            rig.observe_device_found(ctx, st)


# ---- deliberate conditions ---------------------------------------------------------------------------------------------------------------
class ConditionTests(RigBase):
    CASE = "windows.A.error-radio-off"

    def setUp(self):
        super().setUp()
        self.cfg = R.RigConfig({"A": SERIALS["A"]}, EXPECTED_CERT, run_id="r1")

    def start(self, conditions="auto", **kw):
        self.cond = Conditions(self.world) if conditions == "auto" else conditions
        rig = self.make_rig(self.cfg, conditions=self.cond, **kw)
        ctx, st = self.ctx(self.CASE, rig)
        return rig, ctx, st

    def test_no_condition_controller_blocks_before_any_request(self):
        rig, ctx, st = self.start(conditions=None)
        with self.assertRaises(Blocked):
            rig.radio_off(ctx, st)
        self.assertEqual(self.world.js_log, [])

    def test_native_code_is_not_on_the_wire_so_the_error_event_blocks_without_a_source(self):
        rig, ctx, st = self.start()
        rig.radio_off(ctx, st)
        with self.assertRaises(Blocked) as e:
            rig.observe_error_event(ctx, st)
        self.assertIn("native_code", str(e.exception))
        self.assertEqual(ctx.rec.callbacks, [])

    def test_full_recovery_sequence_with_an_injected_native_code_source(self):
        rig, ctx, st = self.start(native_code_source=lambda c, reply: "0x8000000E")
        rig.radio_off(ctx, st)
        self.assertEqual({c["name"]: c["result"] for c in ctx.rec.controls}["baseline_works_before_condition"], "pass")
        rig.observe_error_event(ctx, st)
        cb = ctx.rec.callbacks[0]
        self.assertEqual((cb["code"], cb["native_code"], cb["classification"], cb["recoverability"]), ("scan_failed", "0x8000000E", "radio_off", "retryable"))
        rig.restore_condition(ctx, st)
        rig.fresh_request_after_restore(ctx, st)
        rig.observe_recovery(ctx, st)
        controls = {c["name"]: c["result"] for c in ctx.rec.controls}
        self.assertEqual(controls["condition_restored_verified"], "pass")
        self.assertEqual(self.cond.calls, [("apply", "radio_off"), ("restore", "radio_off"), ("verify", "radio_off")])
        ve = load_validator()
        checker = ve.Checker(None, {"callbacks": ctx.rec.callbacks}, CASES[self.CASE], None, None)
        checker.check_callbacks()
        self.assertEqual(checker.defects, [])

    def test_unverified_restore_is_blocked(self):
        rig, ctx, st = self.start(Conditions(self.world, verify=False))
        rig.radio_off(ctx, st)
        with self.assertRaises(Blocked):
            rig.restore_condition(ctx, st)
        self.assertEqual({c["name"]: c["result"] for c in ctx.rec.controls}["condition_restored_verified"], "fail")

    def test_baseline_that_fails_never_applies_the_condition(self):
        self.world.scan_error = True
        rig, ctx, st = self.start()
        with self.assertRaises(Blocked):
            rig.radio_off(ctx, st)
        self.assertEqual(self.cond.calls, [])

    def test_a_condition_that_produces_no_error_is_a_product_failure(self):
        class NoOpConditions(Conditions):
            def apply(self, name):
                self.calls.append(("apply", name))  # does not change the world
        rig, ctx, st = self.start(NoOpConditions(self.world))
        rig.radio_off(ctx, st)
        with self.assertRaises(ProductFailure):
            rig.observe_error_event(ctx, st)

    def test_recovery_failure_is_a_product_failure(self):
        rig, ctx, st = self.start()
        rig.radio_off(ctx, st)
        self.world.scan_error = True
        rig.fresh_request_after_restore(ctx, st)
        with self.assertRaises(ProductFailure):
            rig.observe_recovery(ctx, st)

    def test_service_denial_has_no_sanctioned_trigger_and_the_condition_is_still_restored(self):
        rig, ctx, st = self.start()
        with self.assertRaises(Blocked) as e:
            rig.synthetic_service_denial(ctx, st)
        self.assertIn("inject_duplicate_connected", str(e.exception))
        rig.cleanup(ctx, st)
        self.assertIn(("restore", "synthetic_service_denial"), self.cond.calls)

    def test_cleanup_restores_an_applied_condition_exactly_once_and_reports_failure(self):
        rig, ctx, st = self.start()
        rig.radio_off(ctx, st)
        rig.cleanup(ctx, st)
        rig.cleanup(ctx, st)
        self.assertEqual(self.cond.calls.count(("restore", "radio_off")), 1)

        class Broken(Conditions):
            def restore(self, name):
                raise OSError("boom")
        rig, ctx, st = self.start(Broken(self.world))
        rig.radio_off(ctx, st)
        rig.cleanup(ctx, st)
        self.assertTrue(any("NOT RESTORED" in b for b in ctx.rec.blockers))

    def test_run_case_always_restores_the_condition_even_when_the_case_blocks(self):
        for case_id, action in (("windows.A.error-radio-off", "radio_off"), ("windows.A.error-no-adapter", "adapter_absent")):
            self.world = World()
            self.cond = Conditions(self.world)
            rig = self.make_rig(self.cfg, conditions=self.cond)  # no native_code_source: observe_error_event blocks
            ctx, st = self.ctx(case_id, rig)
            obs = ctx.observer
            ctx.chrome = WorldChrome(obs, self.world)
            doc = lifecycle.run_case(ctx, RecordingRegistration())
            self.assertEqual(doc["declared_status"], "BLOCKED")
            self.assertIn("native_code", doc["blocker"])
            self.assertEqual(self.cond.calls.count(("restore", action)), 1, case_id)


# ---- whole-case behaviour through the real case runner ----------------------------------------------------------------------------------------
class IntegrationTests(RigBase):
    def run_case(self, case_id, **rig_kw):
        self.world = World()
        rig = self.make_rig(capacity_probe=lambda s, c: {"kind": "att_mtu", "value": 247}, **rig_kw)
        ctx, _ = self.ctx(case_id, rig)
        ctx.chrome = WorldChrome(ctx.observer, self.world)
        return lifecycle.run_case(ctx, RecordingRegistration()), ctx

    def test_explicit_shutdown_runs_end_to_end_but_a_driver_run_is_never_pass(self):
        doc, ctx = self.run_case(AB_SHUTDOWN)
        done = [a["name"] for a in doc["actions"] if a["result"] == "ok"]
        for a in ("connect_phone_A", "connect_phone_B", "prove_bytes_both_links", "shutdown_request", "observe_link_release"):
            self.assertIn(a, done, doc.get("blocker"))
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertNotIn("PASS", json.dumps(doc["declared_status"]))
        self.assertEqual({p["slot"] for p in doc["phones"]}, {"A", "B"})
        self.assertEqual([c["name"] for c in doc["controls"] if c["result"] != "pass"], [])

    def test_a_global_trigger_without_a_pipe_tap_blocks_at_last_json_with_evidence_kept(self):
        doc, ctx = self.run_case(AB_PORT)
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("observe_last_json", doc["blocker"])
        self.assertIn("bytes_proven_before_trigger", [c["name"] for c in doc["controls"]])

    def test_the_transfer_case_without_a_gatt_probe_is_blocked_at_rejections(self):
        doc, ctx = self.run_case(AB_TRANSFER)
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("send_rejected_sizes", doc["blocker"])
        self.assertIn("not observable", doc["blocker"])

    def test_default_rig_still_blocks_every_phone_step(self):
        for name in ("connect_phone_A", "send_boundary_sizes", "fail_phone_A_link", "radio_off"):
            ctx, st = self.ctx(AB_TRANSFER, lifecycle.UnattachedRig())
            with self.assertRaises(Blocked):
                getattr(ctx.rig, name)(ctx, st)

    def test_every_rig_action_named_in_cases_json_is_handled_by_the_phone_rig(self):
        handled = {n for n in dir(R.PhoneRig) if not n.startswith("_")}
        chrome_or_direct = set(lifecycle.CHROME) | set(lifecycle.DIRECT)
        needed = {a for c in CASES.values() if c["kind"] == "chrome" for a in c["required_actions"]} - chrome_or_direct
        self.assertEqual(sorted(needed - handled), [])

    def test_rig_source_has_no_device_mutating_tokens_outside_the_refusal_tests(self):
        src = (HERE / "accdrv" / "rig.py").read_text()
        code = re.sub(r'"""[\s\S]*?"""', "", src)
        code = "\n".join(l for l in code.splitlines() if not l.lstrip().startswith("#"))
        for token in (r"\binstall\b", r"\buninstall\b", r"pm clear", r"\bsvc\b", r"\bfastboot\b", r"\breboot\b", r"\bkeychain\b", r"\bcodesign\b",
                      r"\bHKLM\b", r"\bwinreg\b", r"\bsecurity\s+import\b", r"\btccutil\b"):
            self.assertIsNone(re.search(token, code.replace("not on the read-only allowlist", "")), token)


# ---- Opus review round 1: I1-I8 and the SafeAdb / layout / run-id tightening ----------------------------------------------------------
class ReviewFixTests(RigBase):
    def linked(self, case=AB_TRANSFER, **kw):
        self.world = World()
        kw.setdefault("capacity_probe", lambda slot, conn: {"kind": "att_mtu", "value": 247})
        rig = self.make_rig(**kw)
        ctx, st = self.ctx(case, rig)
        rig.connect_phone_A(ctx, st)
        rig.connect_phone_B(ctx, st)
        return rig, ctx, st

    def full_transfer(self, **kw):
        rig, ctx, st = self.linked(gatt_write_probe=lambda slot: 0, **kw)
        rig.send_boundary_sizes(ctx, st)
        rig.send_rejected_sizes(ctx, st)
        return rig, ctx, st

    def controls(self, ctx):
        return {c["name"]: c["result"] for c in ctx.rec.controls}

    # I1 -------------------------------------------------------------------------------------------------------------------------
    def test_I1_all_echoes_paired_per_connection_passes(self):
        rig, ctx, st = self.full_transfer()
        rig.observe_phone_receipts(ctx, st)
        self.assertEqual(self.controls(ctx)["connector_sees_no_fragments"], "pass")

    def test_I1_a_surplus_message_with_an_allowed_length_is_still_a_fragment(self):
        # 240 is the `chunk` echo length: a record split into two full fragments used to hide inside the allowed-length set
        rig, ctx, st = self.full_transfer()
        self.world.events.append({"at": "t", "message": {"type": "message", "connection_handle": "conn-A", "record_length": R.b64len(240)}})
        with self.assertRaises(ProductFailure) as e:
            rig.observe_phone_receipts(ctx, st)
        self.assertIn("beyond one echo per sent record", str(e.exception))
        self.assertNotIn("connector_sees_no_fragments", self.controls(ctx))

    def test_I1_an_echo_on_the_wrong_connection_is_surplus_and_missing(self):
        rig, ctx, st = self.full_transfer()
        self.world.drain_swap = True
        for e in self.world.events:
            if e["message"]["type"] == "message" and e["message"]["connection_handle"] == "conn-A" and e["message"]["record_length"] == R.b64len(480):
                e["message"]["connection_handle"] = "conn-B"
        for e in st["rig"]["events"]:
            if e.get("type") == "message" and e["connection_handle"] == "conn-A" and e["record_length"] == R.b64len(480):
                e["connection_handle"] = "conn-B"
        with self.assertRaises(ProductFailure):
            rig.observe_phone_receipts(ctx, st)

    def test_I1_one_missing_echo_leaves_the_claim_unproven(self):
        rig, ctx, st = self.full_transfer()
        for bucket in (self.world.events, st["rig"]["events"]):
            for i, e in enumerate(list(bucket)):
                m = e.get("message", e)
                if m.get("type") == "message" and m["connection_handle"] == "conn-B" and m["record_length"] == R.b64len(4096):
                    bucket.remove(e)
                    break
        with self.assertRaises(Blocked) as e:
            rig.observe_phone_receipts(ctx, st)
        self.assertIn("not every sent record produced its echo", str(e.exception))

    def test_I1_observing_receipts_without_any_rejection_run_is_blocked(self):
        rig, ctx, st = self.linked(gatt_write_probe=lambda s: 0)
        rig.send_boundary_sizes(ctx, st)
        with self.assertRaises(Blocked) as e:
            rig.observe_phone_receipts(ctx, st)
        self.assertIn("no rejected size was exercised", str(e.exception))
        self.assertNotIn("rejected_sizes_produce_no_gatt_writes", self.controls(ctx))

    # I2 / required control -----------------------------------------------------------------------------------------------------
    def test_I2_the_historical_accessdenied_control_is_never_self_asserted(self):
        self.world = World()
        cfg = R.RigConfig({"A": SERIALS["A"]}, EXPECTED_CERT, run_id="r1")
        cond = Conditions(self.world)
        rig = self.make_rig(cfg, conditions=cond, native_code_source=lambda c, r: "0x1")
        ctx, st = self.ctx("windows.A.error-radio-off", rig)
        rig.radio_off(ctx, st)
        rig.observe_error_event(ctx, st)
        rig.restore_condition(ctx, st)
        rig.fresh_request_after_restore(ctx, st)
        rig.observe_recovery(ctx, st)
        self.assertNotIn("not_reinterpreting_historical_accessdenied", self.controls(ctx))
        doc = ctx.rec.finalize()
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("not_reinterpreting_historical_accessdenied", doc["blocker"])

    def test_I2_native_code_source_value_is_captured_as_evidence(self):
        self.world = World()
        cfg = R.RigConfig({"A": SERIALS["A"]}, EXPECTED_CERT, run_id="r1")
        rig = self.make_rig(cfg, conditions=Conditions(self.world), native_code_source=lambda c, r: "0x8000000E")
        ctx, st = self.ctx("windows.A.error-radio-off", rig)
        rig.radio_off(ctx, st)
        rig.observe_error_event(ctx, st)
        self.assertIn("0x8000000E", (ctx.rec.out / "captures" / "native-code-source").read_text())

    def test_I8_global_trigger_records_the_required_survivor_not_applicable_control(self):
        self.world = World()
        rig = self.make_rig()
        ctx, st = self.ctx(AB_SHUTDOWN, rig)
        rig.connect_phone_A(ctx, st)
        rig.connect_phone_B(ctx, st)
        rig.prove_bytes_both_links(ctx, st)
        st["trigger"], st["trigger_wall"] = "shutdown_request", ctx.rec.clock()
        self.world.release("A")
        self.world.release("B")
        rig.observe_link_release(ctx, st)
        self.assertEqual(self.controls(ctx)["survivor_progress_not_applicable_for_global_trigger"], "pass")

    def test_I8_the_control_is_not_recorded_when_a_link_was_not_released(self):
        self.world = World()
        rig = self.make_rig()
        ctx, st = self.ctx(AB_SHUTDOWN, rig)
        rig.connect_phone_A(ctx, st)
        rig.connect_phone_B(ctx, st)
        st["trigger"], st["trigger_wall"] = "shutdown_request", ctx.rec.clock()
        self.world.release("A")
        with self.assertRaises(ProductFailure):
            rig.observe_link_release(ctx, st)
        self.assertNotIn("survivor_progress_not_applicable_for_global_trigger", self.controls(ctx))

    def test_I8_the_control_is_not_recorded_for_a_per_phone_trigger(self):
        rig, ctx, st = self.linked(case=AB_FAIL, link_failer=lambda slot: self.world.release(slot))
        rig.prove_bytes_both_links(ctx, st)
        rig.fail_phone_A_link(ctx, st)
        rig.observe_link_release(ctx, st)
        self.assertNotIn("survivor_progress_not_applicable_for_global_trigger", self.controls(ctx))

    def test_I8_every_required_control_of_the_shutdown_case_is_recorded_by_the_end_of_the_link_release_step(self):
        self.world = World()
        rig = self.make_rig(capacity_probe=lambda s, c: {"kind": "att_mtu", "value": 247})
        ctx, _ = self.ctx(AB_SHUTDOWN, rig)
        ctx.chrome = WorldChrome(ctx.observer, self.world)
        doc = lifecycle.run_case(ctx, RecordingRegistration())
        required = set(CASES[AB_SHUTDOWN]["required_controls"])
        recorded = {c["name"] for c in doc["controls"]}
        # the only remaining gap is the unobservable pending cancellation, which blocks the case honestly
        self.assertEqual(sorted(required - recorded), [], doc.get("blocker"))

    # I3 -------------------------------------------------------------------------------------------------------------------------
    # I4 -------------------------------------------------------------------------------------------------------------------------
    def stop_failer(self):
        return lambda slot: self.world.stop(slot)

    def test_I4_stop_file_trigger_survivor_still_proves_bytes_but_release_stays_blocked(self):
        rig, ctx, st = self.linked(case=AB_FAIL, link_failer=self.stop_failer())
        rig.prove_bytes_both_links(ctx, st)
        rig.fail_phone_A_link(ctx, st)
        rig.observe_pending_cancellation(ctx, st)  # the armed request is cancelled when the server closes
        rig.survivor_transfer_phone_B(ctx, st)  # reads A as CAPTURED without treating it as a fixture fault
        self.assertEqual(self.controls(ctx)["survivor_progress_after_failure"], "pass")
        with self.assertRaises(Blocked) as e:
            rig.observe_link_release(ctx, st)
        self.assertIn("CAPTURED", str(e.exception))
        self.assertIn("not guessed", str(e.exception))

    def test_I4_a_phone_the_rig_did_not_fail_that_becomes_captured_is_blocked(self):
        rig, ctx, st = self.linked(case=AB_FAIL, link_failer=self.stop_failer())
        rig.prove_bytes_both_links(ctx, st)
        rig.fail_phone_A_link(ctx, st)
        self.world.phones["B"]["running"] = False  # the survivor's fixture died by itself
        with self.assertRaises(Blocked):
            rig.survivor_transfer_phone_B(ctx, st)

    def test_I4_captured_telemetry_is_rejected_unless_explicitly_allowed(self):
        res = R.CmdResult(0, json.dumps({"run_id": "r", "status": "CAPTURED", "records": [], "states": []}).encode(), b"")
        with self.assertRaises(Blocked):
            R.parse_fixture(res, "r")
        self.assertEqual(R.parse_fixture(res, "r", allow_captured=True)["status"], "CAPTURED")

    def test_I4_captured_telemetry_that_does_show_the_departure_gives_a_release(self):
        rig, ctx, st = self.linked(case=AB_FAIL, link_failer=self.stop_failer())
        rig.prove_bytes_both_links(ctx, st)
        rig.fail_phone_A_link(ctx, st)
        self.world.sleep(0.5)
        self.world.phones["A"]["states"].append({"epoch_ms": int(self.world.now() + SKEW), "peers": []})  # a fixture that records it
        rig.survivor_transfer_phone_B(ctx, st)
        rig.observe_link_release(ctx, st)

    # I5 -------------------------------------------------------------------------------------------------------------------------
    def add_foreign(self, slot, peer="f" * 16):
        self.world.foreign.setdefault(slot, []).append(peer)
        st_ = self.world.phones[slot]["states"]
        st_.append({"epoch_ms": int(self.world.now() + SKEW), "peers": list(st_[-1]["peers"]) + [peer]})

    def test_I5_the_hosts_peer_is_the_new_one_even_with_a_foreign_central_present(self):
        self.world = World()
        self.add_foreign("B")
        rig = self.make_rig()
        ctx, st = self.ctx(AB_FAIL, rig)
        rig.connect_phone_A(ctx, st)
        rig.connect_phone_B(ctx, st)
        self.assertEqual(st["rig"]["links"]["B"]["peer"], World.peer_of("B"))
        self.assertNotIn("f" * 16, (ctx.rec.out / "captures" / "phone-binding").read_text())

    def test_I5_a_lost_survivor_is_not_hidden_by_a_foreign_central(self):
        self.world = World()
        self.add_foreign("B")
        rig = self.make_rig(link_failer=lambda slot: self.world.release(slot))
        ctx, st = self.ctx(AB_FAIL, rig)
        rig.connect_phone_A(ctx, st)
        rig.connect_phone_B(ctx, st)
        rig.fail_phone_A_link(ctx, st)
        self.world.release("B")  # the host's link to B is lost; the foreign central stays
        with self.assertRaises(ProductFailure) as e:
            rig.observe_link_release(ctx, st)
        self.assertIn("survivor_lost=['B']", str(e.exception))

    def test_I5_release_is_detected_although_a_foreign_central_remains(self):
        self.world = World()
        self.add_foreign("A")
        rig = self.make_rig(link_failer=lambda slot: self.world.release(slot))
        ctx, st = self.ctx(AB_FAIL, rig)
        rig.connect_phone_A(ctx, st)
        rig.connect_phone_B(ctx, st)
        rig.fail_phone_A_link(ctx, st)
        rig.observe_link_release(ctx, st)

    def test_I5_two_new_centrals_at_once_keep_the_case_blocked_and_close_the_connection(self):
        self.world = World(order=("A", "B"))
        self.world.foreign["A"] = ["e" * 16]  # a foreign central connects at the same moment as the host
        rig = self.make_rig()
        ctx, st = self.ctx(AB_FAIL, rig)
        with self.assertRaises(Blocked) as e:
            rig.connect_phone_A(ctx, st)
        self.assertIn("cannot be identified", str(e.exception))
        self.assertEqual(st["rig"]["links"], {})
        self.assertEqual(len(self.world.disconnected), 1)

    def test_I5_no_new_peer_visible_keeps_the_case_blocked(self):
        self.world = World(order=("A", "B"))
        orig = self.world.js

        def js(expr):
            out = orig(expr)
            if "connectDevice" in expr:
                self.world.phones["A"]["states"].pop()  # telemetry never published the new peer
            return out
        self.world.js = js
        rig = self.make_rig()
        ctx, st = self.ctx(AB_FAIL, rig)
        with self.assertRaises(Blocked):
            rig.connect_phone_A(ctx, st)

    def test_unbound_connection_is_closed_best_effort(self):
        self.world = World()
        self.world.drop = True
        rig = self.make_rig()
        ctx, st = self.ctx(AB_TRANSFER, rig)
        with self.assertRaises(Blocked):
            rig.connect_phone_A(ctx, st)
        self.assertTrue(self.world.disconnected)

    # I6 -------------------------------------------------------------------------------------------------------------------------
    def test_I6_a_stale_record_from_an_earlier_case_cannot_break_a_receipt(self):
        rig, ctx, st = self.full_transfer()
        r = st["rig"]["receipts"][0]
        slot = r["slot"]
        self.world.phones[slot]["records"].insert(0, {"bytes": r["size"], "sha256": r["sha256"], "epoch_ms": 1})  # an earlier case's identical digest
        st["rig"]["phones"][slot]["baseline_records"] += 1  # ... which already existed when this case bound the phone
        rig.observe_phone_receipts(ctx, st)

    def test_I6_a_second_copy_inside_this_case_is_still_a_failure(self):
        rig, ctx, st = self.full_transfer()
        r = st["rig"]["receipts"][0]
        self.world.phones[r["slot"]]["records"].append({"bytes": r["size"], "sha256": r["sha256"], "epoch_ms": 1})
        with self.assertRaises(ProductFailure):
            rig.observe_phone_receipts(ctx, st)

    # I7 -------------------------------------------------------------------------------------------------------------------------
    def test_I7_source_hash_is_a_keyed_hmac_not_a_public_salt_hash(self):
        import hmac as _hmac
        key = b"k" * 32
        cfg = R.RigConfig(SERIALS, EXPECTED_CERT, run_id="r1", source_key=key)
        want = _hmac.new(key, (R.SOURCE_CONTEXT + SERIALS["A"]).encode(), hashlib.sha256).hexdigest()
        self.assertEqual(cfg.source_hash("A"), want)
        self.assertNotEqual(cfg.source_hash("A"), hashlib.sha256((R.SOURCE_CONTEXT + SERIALS["A"]).encode()).hexdigest())
        other = R.RigConfig(SERIALS, EXPECTED_CERT, run_id="r1", source_key=b"z" * 32)
        self.assertNotEqual(cfg.source_hash("A"), other.source_hash("A"))
        self.assertEqual(cfg.source_hash("A"), R.RigConfig(SERIALS, EXPECTED_CERT, run_id="r1", source_key=key).source_hash("A"))  # stable with the controller key

    def test_I7_without_a_controller_key_the_identifier_is_ephemeral_and_distinct_per_run(self):
        a = R.RigConfig(SERIALS, EXPECTED_CERT, run_id="r1")
        b = R.RigConfig(SERIALS, EXPECTED_CERT, run_id="r1")
        self.assertNotEqual(a.source_hash("A"), b.source_hash("A"))
        self.assertNotEqual(a.source_hash("A"), a.source_hash("B"))

    def test_I7_the_key_never_reaches_evidence_or_repr_and_short_keys_are_refused(self):
        key = b"SECRET-KEY-BYTES-0123456789abcdef"
        cfg = R.RigConfig(SERIALS, EXPECTED_CERT, run_id="r1", source_key=key)
        self.assertNotIn("SECRET", repr(cfg))
        rig = self.make_rig(cfg)
        ctx, st = self.ctx(AB_TRANSFER, rig)
        rig.bind_phones(ctx, st)
        ctx.rec.finalize()
        blob = b"".join(p.read_bytes() for p in ctx.rec.out.rglob("*") if p.is_file())
        self.assertNotIn(key, blob)
        self.assertNotIn(base64.b64encode(key), blob)
        self.assertNotIn(key.hex().encode(), blob)
        for bad in (b"short", "not-bytes", 12):
            with self.assertRaises(Refused):
                R.RigConfig(SERIALS, EXPECTED_CERT, run_id="r1", source_key=bad)

    # SafeAdb / layout / run id --------------------------------------------------------------------------------------------------
    def test_tcp_and_wireless_serials_are_refused(self):
        for serial in ("192.168.1.5:5555", "10.0.0.2", "192.168.1.5", "adb-ABC123-xyz._adb-tls-connect._tcp", "host:5555", "emulator-5554:x", "wifi_tcp1"):
            with self.assertRaises(Refused, msg=serial):
                R.RigConfig({"A": serial}, EXPECTED_CERT, run_id="r1")
        R.RigConfig({"A": "R58M1234XYZ"}, EXPECTED_CERT, run_id="r1")  # a USB serial is fine

    def test_exec_out_is_tied_to_the_pm_path_result_of_the_same_slot(self):
        adb = R.SafeAdb(self.world.runner, "/opt/adb", self.config())
        other_pkg = "/data/app/~~zzz==/com.other.app-abc==/base.apk"
        own = "/data/app/~~abc==/me.nagaev.veles.otp23-xyz==/base.apk"
        with self.assertRaises(Refused):
            adb.check(("exec-out", "cat", own), "A")  # before pm path
        adb.apk_path("A")
        adb.check(("exec-out", "cat", own), "A")
        with self.assertRaises(Refused):
            adb.check(("exec-out", "cat", other_pkg), "A")  # another package's APK, even though it matches the generic shape
        with self.assertRaises(Refused):
            adb.check(("exec-out", "cat", own), "B")  # slot B has no pm path result yet
        with self.assertRaises(Refused):
            adb.check(("exec-out", "cat", own))  # no slot at all

    def test_pm_path_for_another_package_directory_is_blocked(self):
        res = R.CmdResult(0, b"package:/data/app/~~zzz==/com.other.app-abc==/base.apk\n", b"")
        with self.assertRaises(Blocked):
            R.parse_pm_path(res, PKG)
        self.world.fixed_shell_output[("shell", "pm", "path", PKG)] = (0, res.out, b"")
        rig = self.make_rig()
        ctx, st = self.ctx(AB_TRANSFER, rig)
        with self.assertRaises(Blocked):
            rig.bind_phones(ctx, st)
        self.assertFalse(any(a[3] == "exec-out" for a in self.world.argv_log))  # nothing was pulled

    def test_fixture_layout_names_are_validated(self):
        for bad in ("../x", "a..b", "a/b", "", ".hidden", "x" * 80, "a b", "a;b", None, 5):
            with self.assertRaises(Refused, msg=repr(bad)):
                R.FixtureLayout(server_file=bad)
            with self.assertRaises(Refused, msg=repr(bad)):
                R.FixtureLayout(ready_file=bad)
        with self.assertRaises(Refused):
            R.FixtureLayout("same.json", "same.json")
        lay = R.FixtureLayout("srv.json", "rdy")
        cfg = R.RigConfig(SERIALS, EXPECTED_CERT, layout=lay, run_id="r1")
        adb = R.SafeAdb(self.world.runner, "/opt/adb", cfg)
        adb.check(("shell", "run-as", PKG, "cat", "files/srv.json"))
        with self.assertRaises(Refused):
            adb.check(("shell", "run-as", PKG, "cat", "files/otp23-a01-server.json"))

    def test_run_id_is_mandatory_and_stale_documents_are_refused(self):
        for bad in (None, "", "has space", "x" * 80, 5):
            with self.assertRaises(Refused, msg=repr(bad)):
                R.RigConfig(SERIALS, EXPECTED_CERT, run_id=bad)
        doc = R.CmdResult(0, json.dumps({"run_id": "old", "status": "RUNNING", "records": [], "states": []}).encode(), b"")
        for run_id in ("new", "", None):
            with self.assertRaises(Blocked):
                R.parse_fixture(doc, run_id)

    def test_ready_file_content_must_equal_the_run_id(self):
        for content in ("old-run\n", "", "r1x\n"):
            self.world.ready_content = content
            rig = self.make_rig()
            ctx, st = self.ctx(AB_TRANSFER, rig)
            with self.assertRaises(Blocked) as e:
                rig.bind_phones(ctx, st)
            self.assertIn("stale or foreign", str(e.exception))

    # cleanup / restore semantics -------------------------------------------------------------------------------------------------
    def cond_rig(self, conditions):
        self.world = World()
        cfg = R.RigConfig({"A": SERIALS["A"]}, EXPECTED_CERT, run_id="r1")
        rig = self.make_rig(cfg, conditions=conditions)
        ctx, st = self.ctx("windows.A.error-radio-off", rig)
        return rig, ctx, st

    def test_unverified_restore_is_retried_by_cleanup_and_reported_as_possibly_applied(self):
        self.world = World()
        cond = Conditions(self.world, verify=False)
        rig, ctx, st = self.cond_rig(cond)
        rig.radio_off(ctx, st)
        with self.assertRaises(Blocked):
            rig.restore_condition(ctx, st)
        self.assertFalse(st.get("condition_restored"))
        n = cond.calls.count(("restore", "radio_off"))
        rig.cleanup(ctx, st)
        self.assertEqual(cond.calls.count(("restore", "radio_off")), n + 1)  # retried, not skipped
        self.assertTrue(any("may still be applied" in b for b in ctx.rec.blockers))

    def test_verified_restore_is_not_repeated_by_cleanup(self):
        cond = Conditions(World())
        rig, ctx, st = self.cond_rig(cond)
        rig.radio_off(ctx, st)
        rig.restore_condition(ctx, st)
        before = list(cond.calls)
        rig.cleanup(ctx, st)
        self.assertEqual(cond.calls, before)

    def test_restore_that_raises_is_reported_by_cleanup_and_never_swallowed(self):
        class Broken(Conditions):
            def restore(self, name):
                self.calls.append(("restore", name))
                raise OSError("device gone")
        rig, ctx, st = self.cond_rig(Broken(World()))
        rig.radio_off(ctx, st)
        rig.cleanup(ctx, st)
        self.assertTrue(any("NOT RESTORED (may still be applied)" in b for b in ctx.rec.blockers))

    def test_applied_condition_that_has_no_effect_leaves_no_scan_running(self):
        class NoOp(Conditions):
            def apply(self, name):
                self.calls.append(("apply", name))
        rig, ctx, st = self.cond_rig(NoOp(World()))
        rig.radio_off(ctx, st)
        self.assertEqual(sum("stopScan" in e for e in self.world.js_log), 2)  # baseline scan + the scan the condition failed to stop

    def test_fresh_request_after_restore_leaves_no_scan_running(self):
        rig, ctx, st = self.cond_rig(Conditions(World()))
        rig.fresh_request_after_restore(ctx, st)
        self.assertEqual(sum("stopScan" in e for e in self.world.js_log), 1)

    def test_a_second_interrupt_in_rig_cleanup_still_disposes_chrome_and_restores_registration_then_reraises(self):
        self.world = World()

        class Rig(R.PhoneRig):
            def cleanup(self, ctx, st):
                raise KeyboardInterrupt()
        cfg = self.config()
        rig = Rig(R.SafeAdb(self.world.runner, "/opt/adb", cfg), cfg, cert_of_apk, wall_ms=self.world.now)
        ctx, _ = self.ctx("windows.none.ready-hygiene", rig)
        ctx.chrome = WorldChrome(ctx.observer, self.world)
        reg_ = RecordingRegistration()
        with self.assertRaises(KeyboardInterrupt):
            lifecycle.run_case(ctx, reg_)
        self.assertTrue(ctx.chrome.disposed)
        self.assertEqual(reg_.events, ["register", "unregister"])
        self.assertTrue((ctx.rec.out / "run.json").exists())  # the record was finalized before the re-raise
        self.assertIn("rig cleanup interrupted", json.loads((ctx.rec.out / "run.json").read_text())["blocker"])

    def test_cleanup_after_a_case_blocked_mid_condition_restores_through_run_case(self):
        self.world = World()
        cond = Conditions(self.world)
        cfg = R.RigConfig({"A": SERIALS["A"]}, EXPECTED_CERT, run_id="r1")
        rig = self.make_rig(cfg, conditions=cond, native_code_source=lambda c, r: None)
        ctx, _ = self.ctx("windows.A.error-radio-off", rig)
        ctx.chrome = WorldChrome(ctx.observer, self.world)
        doc = lifecycle.run_case(ctx, RecordingRegistration())
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertEqual(cond.calls.count(("restore", "radio_off")), 1)
        self.assertIn(("verify", "radio_off"), cond.calls)


# ---- N2: an interrupt in ANY cleanup stage must not skip a later stage or the finalization ---------------------------------------------
class InterruptingCleanupTests(RigBase):
    class Direct:
        def __init__(self, exc=None):
            self.exc, self.closed = exc, False

        def close(self):
            self.closed = True
            if self.exc:
                raise self.exc

    class IChrome:
        def __init__(self, exc=None):
            self.exc, self.disposed = exc, False

        def dispose(self):
            self.disposed = True
            if self.exc:
                raise self.exc

    class IReg(RecordingRegistration):
        def __init__(self, exc=None):
            super().__init__()
            self.exc = exc

        def unregister(self):
            self.events.append("unregister")
            if self.exc:
                raise self.exc
            return {"restored": True}

    class IRig:
        def __init__(self, exc=None):
            self.exc, self.cleaned = exc, False

        def cleanup(self, ctx, st):
            self.cleaned = True
            if self.exc:
                raise self.exc

    def run_stage(self, rig_exc=None, direct_exc=None, chrome_exc=None, reg_exc=None):
        case = CASES["windows.none.direct-eof-clean"]
        rec = Recorder(self.tmp / f"o{self.n}", case, clock=self.world.rec_clock)
        self.n += 1
        parts = types_ns(rig=self.IRig(rig_exc), direct=self.Direct(direct_exc), chrome=self.IChrome(chrome_exc), reg=self.IReg(reg_exc))
        ctx = lifecycle.Ctx(case, rec, chrome=parts.chrome, observer=None, rig=parts.rig, clock=self.world.clock_s, sleep=self.world.sleep)
        parts.reg.registered = True  # as after a successful register()
        st = {"direct": parts.direct}
        result = lifecycle._cleanup(ctx, st, parts.reg)
        return parts, rec, result

    def assert_all_stages_ran(self, parts):
        self.assertTrue(parts.rig.cleaned)
        self.assertTrue(parts.direct.closed)
        self.assertTrue(parts.chrome.disposed)
        self.assertEqual(parts.reg.events, ["unregister"])

    def test_interrupt_in_each_stage_runs_every_other_stage_and_is_returned(self):
        for stage in ("rig_exc", "direct_exc", "chrome_exc", "reg_exc"):
            for exc in (KeyboardInterrupt(), SystemExit(3)):
                parts, rec, result = self.run_stage(**{stage: exc})
                self.assert_all_stages_ran(parts)
                self.assertIs(result, exc, stage)
                self.assertTrue(any("interrupted" in b for b in rec.blockers), (stage, rec.blockers))

    def test_every_stage_interrupted_still_attempts_everything_and_returns_the_first(self):
        first = KeyboardInterrupt("first")
        parts, rec, result = self.run_stage(rig_exc=first, direct_exc=KeyboardInterrupt("2"), chrome_exc=SystemExit(1), reg_exc=KeyboardInterrupt("4"))
        self.assert_all_stages_ran(parts)
        self.assertIs(result, first)
        self.assertEqual(len([b for b in rec.blockers if "interrupted" in b]), 4)

    def test_ordinary_failures_in_any_stage_are_recorded_and_do_not_skip_later_stages(self):
        for stage in ("rig_exc", "direct_exc", "chrome_exc", "reg_exc"):
            parts, rec, result = self.run_stage(**{stage: RuntimeError("boom")})
            self.assert_all_stages_ran(parts)
            self.assertIsNone(result)
            self.assertTrue(rec.blockers, stage)

    def test_an_unexpected_registration_exception_is_reported_as_not_restored(self):
        parts, rec, result = self.run_stage(reg_exc=ValueError("odd"))
        self.assertTrue(any("REGISTRATION NOT RESTORED" in b and "ValueError" in b for b in rec.blockers), rec.blockers)

    def run_case_with(self, **kw):
        case = CASES["windows.none.ready-hygiene"]
        obs = ScriptedObserver("h")
        chrome = WorldChrome(obs, self.world)
        orig_dispose = chrome.dispose
        if kw.get("chrome_exc"):
            def dispose():
                orig_dispose()
                raise kw["chrome_exc"]
            chrome.dispose = dispose
        reg_ = self.IReg(kw.get("reg_exc"))
        rec = Recorder(self.tmp / f"rc{self.n}", case, clock=self.world.rec_clock)
        self.n += 1
        rec.fields["binary"] = {"sha256": "c" * 64}
        rig = kw.get("rig") or self.IRig()
        ctx = lifecycle.Ctx(case, rec, chrome=chrome, observer=obs, host_exe="/x/veles-native-host", host_name=HOST_NAME, ext_dir="/ext",
                            ext_id=EXT_ID, origin=ORIGIN, rig=rig, clock=self.world.clock_s, sleep=self.world.sleep)
        return ctx, chrome, reg_, rec

    def test_run_case_finalizes_the_record_then_reraises_for_an_interrupt_in_chrome_disposal_or_registration_restore(self):
        for stage, exc in (("chrome_exc", KeyboardInterrupt()), ("reg_exc", SystemExit(2))):
            self.world = World()
            ctx, chrome, reg_, rec = self.run_case_with(**{stage: exc})
            with self.assertRaises(type(exc)):
                lifecycle.run_case(ctx, reg_)
            self.assertTrue(chrome.disposed)
            self.assertEqual(reg_.events, ["register", "unregister"])  # registration restore is ALWAYS attempted
            doc = json.loads((rec.out / "run.json").read_text())  # finalized before the re-raise
            self.assertEqual(doc["declared_status"], "BLOCKED")
            self.assertIn("interrupted", doc["blocker"])

    def test_run_case_interrupted_in_a_post_check_still_finalizes_then_reraises(self):
        self.world = World()
        ctx, chrome, reg_, rec = self.run_case_with()

        def check(r):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            lifecycle.run_case(ctx, reg_, post_checks=(check,))
        self.assertEqual(reg_.events, ["register", "unregister"])
        self.assertTrue((rec.out / "run.json").exists())
        self.assertIn("driver_errors", json.loads((rec.out / "run.json").read_text()))

    def test_an_interrupt_during_the_actions_is_still_finalized_and_cleaned_up(self):
        self.world = World()
        ctx, chrome, reg_, rec = self.run_case_with()

        class Boom(WorldChrome):
            pass
        orig = chrome.js

        def js(session, expr, timeout=60):
            if "hello()" in expr:
                raise KeyboardInterrupt()
            return orig(session, expr, timeout)
        chrome.js = js
        with self.assertRaises(KeyboardInterrupt):
            lifecycle.run_case(ctx, reg_)
        self.assertTrue(chrome.disposed)
        self.assertEqual(reg_.events, ["register", "unregister"])
        self.assertTrue((rec.out / "run.json").exists())


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


# ---- Option 2 (user decision 2026-11-02): abrupt EOF => per-request outcome unknown; shutdown / per-phone failure stay strict -----------------
EOF_TRIGGERS = {"windows.AB.lifecycle-port-disconnect": "literal_port_disconnect", "windows.AB.lifecycle-tab-close": "close_connector_tab",
                "windows.AB.lifecycle-pipe-closure": "chrome_owned_pipe_closure",
                "macos.AB.lifecycle-port-disconnect": "literal_port_disconnect", "macos.AB.lifecycle-tab-close": "close_connector_tab",
                "macos.AB.lifecycle-pipe-closure": "chrome_owned_pipe_closure"}


class Option2Tests(RigBase):
    def linked(self, case, **kw):
        self.world = World()
        rig = self.make_rig(**kw)
        ctx, st = self.ctx(case, rig)
        rig.connect_phone_A(ctx, st)
        rig.connect_phone_B(ctx, st)
        return rig, ctx, st

    def check_block(self, ctx, case_id):
        """Run the REAL validator's pending-handling check over the record the rig produced."""
        ve = load_validator()
        manifest = {"pending_request_handling": ctx.rec.fields.get("pending_request_handling"),
                    "captures": [{"name": c["name"]} for c in ctx.rec.captures]}
        checker = ve.Checker(None, manifest, CASES[case_id], None, None)
        checker.check_pending_handling()
        return checker.defects

    def strict(self, case=AB_SHUTDOWN, arm=True, **kw):
        """Linked shutdown case. arm=True arms+proves a pending request as the case-runner hook would; arm=False leaves that to the real
        lifecycle._trigger hook when the test calls lifecycle.shutdown_request."""
        rig, ctx, st = self.linked(case, **kw)
        rig.prove_bytes_both_links(ctx, st)
        st["host_pid"] = 4242
        if arm:
            rig.before_trigger(ctx, st, "shutdown_request")
            self.assertTrue(st["rig"]["armed"]["proven"], st["rig"]["armed"])
        return rig, ctx, st

    # EOF: unknown ------------------------------------------------------------------------------------------------------------------
    def test_every_eof_case_records_unknown_and_claims_nothing_the_validator_accepts(self):
        for case_id, trigger in EOF_TRIGGERS.items():
            rig, ctx, st = self.linked(case_id)
            st["trigger"] = trigger
            rig.record_pending_disposition(ctx, st)
            block = ctx.rec.fields["pending_request_handling"]
            self.assertEqual((block["per_request_outcome"], block["host_correlated_cancellation_claimed"]), ("unknown", False), case_id)
            for forbidden in ("request_id", "terminal", "armed_pending_proven", "outcome"):
                self.assertNotIn(forbidden, block)
            self.assertEqual(self.check_block(ctx, case_id), [], case_id)
            self.assertIn("pending-disposition", {c["name"] for c in ctx.rec.captures})
            text = (ctx.rec.out / "captures" / "pending-disposition").read_text()
            self.assertIn('"per_request_outcome": "unknown"', text)
            self.assertIn("local only", text)

    def test_eof_cases_never_arm_an_extra_request(self):
        for case_id, trigger in EOF_TRIGGERS.items():
            rig, ctx, st = self.linked(case_id)
            before = self.sent_count()
            rig.before_trigger(ctx, st, trigger)
            self.assertEqual(self.sent_count(), before, case_id)
            self.assertNotIn("armed", st["rig"])
            self.assertNotIn("armed-pending-proof", {c["name"] for c in ctx.rec.captures})

    def test_disposition_is_refused_for_another_trigger_or_a_strict_case(self):
        rig, ctx, st = self.linked("windows.AB.lifecycle-tab-close")
        for trig in (None, "literal_port_disconnect", "shutdown_request", "fail_phone_A_link"):
            st["trigger"] = trig
            with self.assertRaises(Blocked, msg=str(trig)):
                rig.record_pending_disposition(ctx, st)
        self.assertNotIn("pending_request_handling", ctx.rec.fields)
        for case_id, trig in ((AB_SHUTDOWN, "shutdown_request"), (AB_FAIL, "fail_phone_A_link"), (AB_TRANSFER, "shutdown_request")):
            rig, ctx, st = self.linked(case_id)
            st["trigger"] = trig
            with self.assertRaises(Blocked, msg=case_id):
                rig.record_pending_disposition(ctx, st)

    def test_strict_observation_is_refused_for_eof_cases_so_no_host_claim_can_appear(self):
        for case_id, trigger in EOF_TRIGGERS.items():
            rig, ctx, st = self.linked(case_id)
            st["trigger"] = trigger
            st["rig"]["armed"] = {"proven": True, "slot": "A", "connection": "conn-A", "request_id": "r-armed"}  # even a perfect arming
            self.world.resolve_armed_by_host("send_failed", "link_lost")  # ... and a host-looking error cannot create a claim here
            with self.assertRaises(Blocked) as e:
                rig.observe_pending_cancellation(ctx, st)
            self.assertIn("does not require host-correlated", str(e.exception))
            self.assertNotIn("pending_request_handling", ctx.rec.fields)

    def test_a_synthesized_port_closed_never_becomes_a_host_claim_in_an_eof_case(self):
        rig, ctx, st = self.linked(AB_PORT)
        self.world.armed, self.world.pending = "pending", ["r-armed"]  # something the page happens to hold
        rig.before_trigger(ctx, st, "literal_port_disconnect")
        st["trigger"] = "literal_port_disconnect"
        self.world.js("velesAcceptance.closePort()")  # only the page closes its port: the connector synthesizes port_closed, locally
        self.assertEqual(self.world.log, [])  # the host log has nothing
        rig.record_pending_disposition(ctx, st)
        block = ctx.rec.fields["pending_request_handling"]
        self.assertEqual(block["per_request_outcome"], "unknown")
        self.assertNotIn("terminal", block)

    def test_whole_case_with_an_injected_tap_records_unknown_cleanup_proof_and_stays_blocked(self):
        class Tap:
            def last_json(self):
                return {"frames": []}

            def stdout_closed(self):
                return {"stdout_eof": True}
        self.world = World()
        rig = self.make_rig()
        ctx, _ = self.ctx(AB_PORT, rig)
        ctx.chrome = WorldChrome(ctx.observer, self.world)
        ctx.pipe_tap = Tap()
        doc = lifecycle.run_case(ctx, RecordingRegistration())
        done = [a["name"] for a in doc["actions"] if a["result"] == "ok"]
        for a in ("prove_bytes_both_links", "literal_port_disconnect", "observe_process_exit", "observe_link_release", "record_pending_disposition"):
            self.assertIn(a, done, doc.get("blocker"))
        self.assertEqual(doc["pending_request_handling"]["per_request_outcome"], "unknown")
        self.assertEqual(doc["declared_status"], "BLOCKED")  # awaiting adjudication / hygiene, never PASS
        controls = {c["name"]: c["result"] for c in doc["controls"]}
        self.assertEqual(controls["release_measured_independently_of_process_exit"], "pass")  # two-phone cleanup proof preserved
        self.assertEqual(controls["survivor_progress_not_applicable_for_global_trigger"], "pass")
        release = json.loads((ctx.rec.out / "captures" / "link-release").read_text())["results"]
        self.assertEqual(sorted(release), ["A", "B"])
        self.assertTrue(all(r["released_at_host_ms"] is not None for r in release.values()))

    # strict: explicit shutdown ---------------------------------------------------------------------------------------------------------
    def shutdown(self, rig, ctx, st):
        lifecycle.shutdown_request(ctx, st)

    def test_shutdown_requires_and_records_the_correlated_host_error_before_shutdown_complete(self):
        rig, ctx, st = self.strict(arm=False)
        self.shutdown(rig, ctx, st)
        rig.observe_pending_cancellation(ctx, st)
        block = ctx.rec.fields["pending_request_handling"]
        self.assertEqual((block["mode"], block["trigger"], block["armed_pending_proven"]), ("host_correlated_cancellation", "shutdown_request", True))
        self.assertEqual((block["terminal"]["code"], block["terminal"]["stage"], block["terminal"]["synthesized"]), ("internal", "shutdown", False))
        self.assertIs(block["terminal_before_shutdown_complete"], True)
        self.assertEqual(self.check_block(ctx, AB_SHUTDOWN), [])
        self.assertIn(block["capture_ref"], {c["name"] for c in ctx.rec.captures})

    def test_shutdown_complete_before_the_terminal_error_is_a_product_failure(self):
        rig, ctx, st = self.strict(arm=False)
        self.shutdown(rig, ctx, st)
        log = self.world.log
        err = next(e for e in log if e["request_id"] == "r-armed")
        done = next(e for e in log if e["type"] == "shutdown_complete")
        err["seq"], done["seq"] = done["seq"], err["seq"]
        with self.assertRaises(ProductFailure) as e:
            rig.observe_pending_cancellation(ctx, st)
        self.assertIn("preceded", str(e.exception))
        self.assertNotIn("pending_request_handling", ctx.rec.fields)

    def test_shutdown_complete_without_a_correlated_error_for_a_pending_request_is_a_product_failure(self):
        rig, ctx, st = self.strict(arm=False)
        self.world.shutdown_without_error = True
        self.shutdown(rig, ctx, st)
        self.assertTrue(st["rig"]["armed"]["proven"])  # it WAS pending at the trigger
        self.assertTrue(any(e["type"] == "shutdown_complete" for e in self.world.log))
        self.assertFalse(any(e["request_id"] == "r-armed" for e in self.world.log))  # and the host never answered it
        self.assertEqual(self.world.armed["stage"], "port_closed")  # only the connector's own local outcome exists
        with self.assertRaises(ProductFailure) as e:
            rig.observe_pending_cancellation(ctx, st)
        self.assertIn("shutdown_complete was logged", str(e.exception))
        self.assertIn("synthesized", str(e.exception))
        self.assertNotIn("pending_request_handling", ctx.rec.fields)  # no claim is recorded for a failed observation

    def test_shutdown_complete_with_the_request_still_pending_is_a_product_failure_with_an_accurate_message(self):
        rig, ctx, st = self.strict()
        st["trigger"], st["trigger_wall"] = "shutdown_request", ctx.rec.clock()
        self.world.host_event(type="shutdown_complete", request_id="r-shutdown", connection_handle=None)  # nothing for r-armed, still pending
        with self.assertRaises(ProductFailure) as e:
            rig.observe_pending_cancellation(ctx, st)
        self.assertIn("after shutdown_request", str(e.exception))
        self.assertIn("host logged shutdown_complete", str(e.exception))
        self.assertNotIn("failed link", str(e.exception))

    def test_no_error_and_no_shutdown_complete_is_missing_observability_not_a_failure(self):
        rig, ctx, st = self.strict()
        st["trigger"], st["trigger_wall"] = "shutdown_request", ctx.rec.clock()
        self.world.armed = {"type": "error", "request_id": "r-armed", "code": "internal", "stage": "port_closed", "synthesized": True}
        self.world.pending = []  # the connector ended it locally; the host log shows neither an error nor shutdown_complete
        with self.assertRaises(Blocked) as e:
            rig.observe_pending_cancellation(ctx, st)
        self.assertIn("no host shutdown_complete was logged", str(e.exception))

    def test_a_valid_ordered_error_still_passes_after_the_failure_split(self):
        rig, ctx, st = self.strict(arm=False)
        self.shutdown(rig, ctx, st)
        rig.observe_pending_cancellation(ctx, st)
        self.assertIs(ctx.rec.fields["pending_request_handling"]["terminal_before_shutdown_complete"], True)

    def test_whole_shutdown_case_without_the_error_ends_fail_not_blocked(self):
        self.world = World()
        self.world.shutdown_without_error = True
        rig = self.make_rig()
        ctx, _ = self.ctx(AB_SHUTDOWN, rig)
        ctx.chrome = WorldChrome(ctx.observer, self.world)
        doc = lifecycle.run_case(ctx, RecordingRegistration())
        self.assertEqual(doc["declared_status"], "FAIL")
        self.assertTrue(any("shutdown_complete was logged" in f for f in doc["failures"]), doc.get("failures"))
        self.assertNotIn("pending_request_handling", doc)

    def test_the_split_applies_only_to_explicit_shutdown(self):
        # per-phone failure has no shutdown_complete marker: a synthesized-only outcome stays missing observability (Blocked)
        rig, ctx, st = self.per_phone(failer=lambda slot: None)
        self.world.armed = {"type": "error", "request_id": "r-armed", "code": "send_failed", "stage": "link_lost", "synthesized": True}
        self.world.pending = []
        with self.assertRaises(Blocked):
            rig.observe_pending_cancellation(ctx, st)
        # ... and a request still pending there is a failure whose message names its own trigger
        rig, ctx, st = self.per_phone(failer=lambda slot: None)
        with self.assertRaises(ProductFailure) as e:
            rig.observe_pending_cancellation(ctx, st)
        self.assertIn("after fail_phone_A_link", str(e.exception))
        self.assertNotIn("shutdown_complete", str(e.exception))

    def test_shutdown_without_a_logged_shutdown_complete_cannot_show_ordering(self):
        rig, ctx, st = self.strict(arm=False)
        self.shutdown(rig, ctx, st)
        self.world.log[:] = [e for e in self.world.log if e["type"] != "shutdown_complete"]
        with self.assertRaises(Blocked):
            rig.observe_pending_cancellation(ctx, st)

    def test_shutdown_rejects_every_other_signal(self):
        for code, stage in (("send_failed", "link_lost"), ("send_failed", "disconnected"), ("busy", "queue"), ("timeout", "timeout"),
                            ("send_failed", "gatt_write"), ("internal", "port_closed")):
            rig, ctx, st = self.strict()
            st["trigger"], st["trigger_wall"] = "shutdown_request", ctx.rec.clock()
            self.world.resolve_armed_by_host(code, stage)
            self.world.host_event(type="shutdown_complete", request_id="r-shutdown", connection_handle=None)
            with self.assertRaises(Blocked, msg=f"{code}/{stage}"):
                rig.observe_pending_cancellation(ctx, st)
            self.assertNotIn("pending_request_handling", ctx.rec.fields)

    def test_a_connector_synthesized_outcome_is_not_host_proof_even_with_the_allowed_signal(self):
        for strict_case, trigger in ((AB_SHUTDOWN, "shutdown_request"), (AB_FAIL, "fail_phone_A_link")):
            rig, ctx, st = self.linked(strict_case)
            st["host_pid"] = 4242
            if trigger == "shutdown_request":
                rig.before_trigger(ctx, st, trigger)
            else:
                rig.link_failer = lambda slot: None
                rig.fail_phone_A_link(ctx, st)
            st["trigger"] = trigger
            code, stage = ("internal", "shutdown") if trigger == "shutdown_request" else ("send_failed", "link_lost")
            # the promise resolved with a synthesized error (never logged by the connector): the host log is empty
            self.world.armed, self.world.pending = {"type": "error", "request_id": "r-armed", "code": code, "stage": stage, "synthesized": True}, []
            with self.assertRaises(Blocked) as e:
                rig.observe_pending_cancellation(ctx, st)
            self.assertIn("connector-synthesized", str(e.exception))
        # ... and a log entry that itself claims to be synthesized is refused by the predicate
        spec = CASES[AB_SHUTDOWN]["pending_request_handling"]
        armed = {"request_id": "r-armed", "connection": "conn-A"}
        entry = {"type": "error", "request_id": "r-armed", "code": "internal", "stage": "shutdown", "synthesized": True}
        self.assertFalse(R.PhoneRig._allowed_cancellation(spec, entry, armed))
        self.assertTrue(R.PhoneRig._allowed_cancellation(spec, {**entry, "synthesized": None}, armed))

    def test_shutdown_needs_a_request_proven_pending_at_the_trigger(self):
        rig, ctx, st = self.linked(AB_SHUTDOWN)
        st["host_pid"] = 4242
        orig = self.world.js
        self.world.js = lambda expr: True if expr == "window.__armedS.settled" else orig(expr)  # already completed when checked
        rig.before_trigger(ctx, st, "shutdown_request")
        self.assertFalse(st["rig"]["armed"]["proven"])
        st["trigger"] = "shutdown_request"
        with self.assertRaises(Blocked) as e:
            rig.observe_pending_cancellation(ctx, st)
        self.assertIn("not provably pending", str(e.exception))
        rig2, ctx2, st2 = self.linked(AB_SHUTDOWN)
        st2["trigger"] = "shutdown_request"  # nothing armed at all
        with self.assertRaises(Blocked) as e2:
            rig2.observe_pending_cancellation(ctx2, st2)
        self.assertIn("no request was armed", str(e2.exception))

    def test_other_request_other_link_duplicate_or_still_pending_are_not_a_pass(self):
        def prep():
            rig, ctx, st = self.strict()
            st["trigger"], st["trigger_wall"] = "shutdown_request", ctx.rec.clock()
            return rig, ctx, st
        rig, ctx, st = prep()
        self.world.host_event(request_id="r-other", code="internal", stage="shutdown", connection_handle=None)
        self.world.pending = []
        with self.assertRaises(Blocked):  # nothing for the armed id and it is no longer pending
            rig.observe_pending_cancellation(ctx, st)
        rig, ctx, st = prep()
        self.world.resolve_armed_by_host("internal", "shutdown", connection_handle="conn-B")
        self.world.host_event(type="shutdown_complete", request_id="r-s", connection_handle=None)
        with self.assertRaises(Blocked):  # another link
            rig.observe_pending_cancellation(ctx, st)
        rig, ctx, st = prep()
        self.world.resolve_armed_by_host("internal", "shutdown", connection_handle=None)
        self.world.host_event(code="internal", stage="shutdown", connection_handle=None)
        with self.assertRaises(ProductFailure):  # two terminal events for one request
            rig.observe_pending_cancellation(ctx, st)
        rig, ctx, st = prep()
        self.world.log.clear()  # never completed, still pending
        with self.assertRaises(ProductFailure):
            rig.observe_pending_cancellation(ctx, st)

    def test_an_unreachable_page_is_blocked_not_guessed(self):
        rig, ctx, st = self.strict()
        st["trigger"] = "shutdown_request"
        self.world.page_alive = False
        with self.assertRaises(Blocked) as e:
            rig.observe_pending_cancellation(ctx, st)
        self.assertIn("unreachable", str(e.exception))

    def test_strict_observation_refuses_the_wrong_trigger(self):
        rig, ctx, st = self.strict()
        st["trigger"] = "literal_port_disconnect"
        with self.assertRaises(Blocked):
            rig.observe_pending_cancellation(ctx, st)

    def test_teardown_hygiene_arms_nothing(self):
        self.world = World()
        rig = self.make_rig()
        ctx, st = self.ctx("windows.AB.teardown-hygiene", rig)
        rig.connect_phone_A(ctx, st)
        rig.connect_phone_B(ctx, st)
        before = self.sent_count()
        rig.before_trigger(ctx, st, "shutdown_request")
        self.assertEqual(self.sent_count(), before)
        self.assertNotIn("armed", st["rig"])
        self.assertNotIn("window.__armedA", "".join(self.world.js_log))

    def test_only_explicit_shutdown_arms_among_the_global_triggers(self):
        armed_cases = []
        for case_id in list(EOF_TRIGGERS) + [AB_SHUTDOWN, "macos.AB.lifecycle-explicit-shutdown", "windows.AB.teardown-hygiene"]:
            if "observe_pending_cancellation" in CASES[case_id]["required_actions"]:
                armed_cases.append(case_id)
        self.assertEqual(sorted(armed_cases), sorted([AB_SHUTDOWN, "macos.AB.lifecycle-explicit-shutdown"]))

    # strict: per-phone failure -----------------------------------------------------------------------------------------------------------
    def per_phone(self, failer="auto"):
        self.world = World()
        if failer == "auto":
            failer = lambda slot: self.world.release(slot)  # noqa: E731
        rig = self.make_rig(link_failer=failer)
        ctx, st = self.ctx(AB_FAIL, rig)
        rig.connect_phone_A(ctx, st)
        rig.connect_phone_B(ctx, st)
        rig.prove_bytes_both_links(ctx, st)
        rig.fail_phone_A_link(ctx, st)
        return rig, ctx, st

    def test_per_phone_failure_accepts_only_send_failed_link_lost(self):
        rig, ctx, st = self.per_phone()
        rig.observe_pending_cancellation(ctx, st)
        block = ctx.rec.fields["pending_request_handling"]
        self.assertEqual((block["terminal"]["code"], block["terminal"]["stage"]), ("send_failed", "link_lost"))
        self.assertNotIn("terminal_before_shutdown_complete", block)
        self.assertEqual(self.check_block(ctx, AB_FAIL), [])
        for code, stage in (("internal", "shutdown"), ("send_failed", "disconnected"), ("internal", "port_closed"), ("busy", "queue")):
            rig, ctx, st = self.per_phone(failer=lambda slot: None)
            self.world.resolve_armed_by_host(code, stage)
            with self.assertRaises(Blocked, msg=f"{code}/{stage}"):
                rig.observe_pending_cancellation(ctx, st)
            self.assertNotIn("pending_request_handling", ctx.rec.fields)

    def test_per_phone_failure_without_the_armed_proof_is_blocked(self):
        rig, ctx, st = self.per_phone(failer=lambda slot: None)
        st["rig"]["armed"] = {"proven": False, "reason": "completed too fast"}
        with self.assertRaises(Blocked) as e:
            rig.observe_pending_cancellation(ctx, st)
        self.assertIn("completed too fast", str(e.exception))

    def test_no_cross_trigger_weakening_between_strict_cases(self):
        specs = {c: CASES[c]["pending_request_handling"] for c in (AB_SHUTDOWN, AB_FAIL)}
        armed = {"request_id": "r-armed", "connection": "conn-A"}
        shut = {"type": "error", "request_id": "r-armed", "code": "internal", "stage": "shutdown"}
        loss = {"type": "error", "request_id": "r-armed", "code": "send_failed", "stage": "link_lost"}
        self.assertTrue(R.PhoneRig._allowed_cancellation(specs[AB_SHUTDOWN], shut, armed))
        self.assertFalse(R.PhoneRig._allowed_cancellation(specs[AB_SHUTDOWN], loss, armed))
        self.assertTrue(R.PhoneRig._allowed_cancellation(specs[AB_FAIL], loss, armed))
        self.assertFalse(R.PhoneRig._allowed_cancellation(specs[AB_FAIL], shut, armed))
        self.assertFalse(R.PhoneRig._allowed_cancellation(specs[AB_FAIL], {**loss, "request_id": "x"}, armed))
        self.assertFalse(R.PhoneRig._allowed_cancellation(specs[AB_FAIL], {**loss, "connection_handle": "conn-B"}, armed))
        self.assertFalse(R.PhoneRig._allowed_cancellation(specs[AB_FAIL], {**loss, "type": "sent"}, armed))
        self.assertFalse(R.PhoneRig._allowed_cancellation(specs[AB_FAIL], "nope", armed))

    def test_the_connector_logs_only_host_events(self):
        src = (HERE.parent / "acceptance" / "driver-extension" / "connector.js").read_text()
        self.assertIn("completionLog", src)
        synth_fn = src[src.index("function synthesizePortClosed"):src.index("function redact")]
        self.assertIn("synthesized: true", synth_fn)
        self.assertNotIn("completions.push", synth_fn)
        timeout_and_post = src[src.index("function request("):src.index("async function hello")]
        self.assertNotIn("completions.push", timeout_and_post)  # synthesized timeout / post_failed errors are never logged either


if __name__ == "__main__":
    unittest.main()
