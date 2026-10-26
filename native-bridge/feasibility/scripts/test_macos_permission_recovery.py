"""Mock-only tests for the OTP-23 permission-recovery core and driver orchestration.

No Mac, Chrome, devices or System Settings. Usage:
    python3 -m unittest discover -s scripts -p "test_macos_permission_recovery.py" -v
"""

import copy
import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import macos_permission_recovery as driver
import macos_permission_recovery_core as core

SIG = {"identifier": "me.nagaev.veles.bridge", "team": "TEAMX", "available": True}


def state(**extra):
    base = {"sentinel_sha256": "aa", "extension_id": "ext", "chrome_pid": 10, "profile": "/p",
            "binary_sha256": "e7c716", "signature": SIG, "load_unpacked_calls": 1}
    base.update(extra)
    return base


def good_run():
    return {
        "baseline": state(hello_type="ready", scan_class="started", devices=2, host_gone=True),
        "denied": state(hello_type="ready", scan_class="access_denied", host_gone=True,
                        ack={"toggled": "off", "entry_label": "VelesOtp23PoC", "prompt": "shown"}),
        "recovered": state(hello_type="ready", scan_class="started", devices=1, stop_status="stopped",
                           shutdown_type="shutdown_complete", host_gone=True,
                           ack={"toggled": "on", "entry_label": "VelesOtp23PoC", "prompt": "unknown"}),
        "profile_removed": True, "surviving_hosts": [],
    }


class EvaluateTests(unittest.TestCase):
    def failures(self, mutate):
        run = good_run()
        mutate(run)
        return core.evaluate(run)["failures"]

    def test_pass(self):
        verdict = core.evaluate(good_run())
        self.assertEqual(verdict["result"], "PASS", verdict["failures"])
        self.assertTrue(verdict["same_state_recovery"])
        self.assertEqual(verdict["prompt_observation"]["value"], "shown")

    def test_prompt_unknown_does_not_change_verdict(self):
        run = good_run()
        run["denied"]["ack"]["prompt"] = "unknown"
        verdict = core.evaluate(run)
        self.assertEqual(verdict["result"], "PASS")
        self.assertEqual(verdict["prompt_observation"]["value"], "unknown")

    def test_wrong_sentinel(self):
        f = self.failures(lambda r: r["recovered"].update(sentinel_sha256="bb"))
        self.assertIn("recovered:sentinel_changed", f)

    def test_wrong_binary_hash(self):
        f = self.failures(lambda r: r["denied"].update(binary_sha256="other"))
        self.assertIn("denied:binary_changed", f)

    def test_signature_extension_chrome_profile_changes(self):
        f = self.failures(lambda r: r["recovered"].update(
            signature={"identifier": "x"}, extension_id="e2", chrome_pid=11, profile="/q"))
        for code in ("signature_changed", "extension_changed", "chrome_changed", "profile_changed"):
            self.assertIn("recovered:" + code, f)
        self.assertFalse(core.evaluate(self._mut(lambda r: r["recovered"].update(chrome_pid=11)))
                         ["same_state_recovery"])

    def _mut(self, mutate):
        run = good_run()
        mutate(run)
        return run

    def test_second_load_unpacked(self):
        f = self.failures(lambda r: r["denied"].update(load_unpacked_calls=2))
        self.assertIn("denied:reload_detected", f)

    def test_scan_started_while_expected_denied(self):
        f = self.failures(lambda r: r["denied"].update(scan_class="started"))
        self.assertIn("denied:deny_not_observed", f)

    def test_deny_wrong_code(self):
        f = self.failures(lambda r: r["denied"].update(scan_class="denied_wrong_code"))
        self.assertIn("denied:deny_wrong_code", f)

    def test_other_error_is_not_a_denial(self):
        f = self.failures(lambda r: r["denied"].update(scan_class="other_error"))
        self.assertIn("denied:deny_not_observed", f)

    def test_timeouts(self):
        f = self.failures(lambda r: (r["denied"].update(scan_class="timeout"),
                                     r["recovered"].update(scan_class="timeout")))
        self.assertIn("denied:timeout", f)
        self.assertIn("recovered:timeout", f)

    def test_missing_ack(self):
        f = self.failures(lambda r: (r["denied"].pop("ack"), r["recovered"].update(ack_error="ack_missing")))
        self.assertIn("denied:ack_missing", f)
        self.assertIn("recovered:ack_missing", f)

    def test_recovery_without_phone(self):
        f = self.failures(lambda r: r["recovered"].update(devices=0))
        self.assertIn("recovered:no_phone", f)
        f = self.failures(lambda r: r["baseline"].update(devices=0))
        self.assertIn("baseline:no_phone", f)

    def test_recovery_still_denied(self):
        f = self.failures(lambda r: r["recovered"].update(scan_class="access_denied"))
        self.assertIn("recovered:recover_not_permitted", f)

    def test_baseline_not_granted(self):
        f = self.failures(lambda r: r["baseline"].update(scan_class="access_denied"))
        self.assertIn("baseline:not_granted", f)

    def test_missing_phase_and_abort_fail_closed(self):
        run = good_run()
        del run["recovered"]
        run["abort"] = "interrupted"
        f = core.evaluate(run)["failures"]
        self.assertIn("recovered:phase_missing", f)
        self.assertTrue(any(x.startswith("abort:") for x in f))
        self.assertEqual(core.evaluate({})["result"], "FAIL")

    def test_host_survivor_and_dirty_profile(self):
        f = self.failures(lambda r: (r["denied"].update(host_gone=False),
                                     r.update(profile_removed=False, surviving_hosts=[5])))
        self.assertIn("denied:host_survived", f)
        self.assertIn("final:profile_not_clean", f)
        self.assertIn("final:host_survived", f)

    def test_fresh_prompt_recorded_as_waived(self):
        self.assertIn("waived", core.evaluate(good_run())["fresh_first_prompt"])


class ClassifyTests(unittest.TestCase):
    def test_classes(self):
        c = core.classify_scan
        self.assertEqual(c({"status": "started"}), "started")
        self.assertEqual(c({"type": "error", "code": "scan_failed", "stage": "access_denied"}), "access_denied")
        self.assertEqual(c({"type": "error", "code": "permission_denied"}), "denied_wrong_code")
        self.assertEqual(c({"type": "error", "code": "bluetooth_off"}), "other_error")
        self.assertEqual(c({"timeout": True}), "timeout")
        self.assertEqual(c(None), "missing")


class AckTests(unittest.TestCase):
    def raw(self, **kw):
        base = {"nonce": "n1", "ready": core.READY_DISABLE, "toggled": "off",
                "entry_label": "VelesOtp23PoC", "prompt": "not_shown"}
        base.update(kw)
        return base

    def test_valid(self):
        ack, err = core.parse_ack(self.raw(), "n1", core.READY_DISABLE)
        self.assertIsNone(err)
        self.assertEqual(ack["prompt"], "not_shown")

    def test_rejects(self):
        for kw, code in (({"nonce": "old"}, "ack_mismatch"), ({"ready": core.READY_ENABLE}, "ack_mismatch"),
                         ({"toggled": "on"}, "ack_wrong_toggle"), ({"entry_label": " "}, "ack_invalid"),
                         ({"prompt": "maybe"}, "ack_invalid")):
            self.assertEqual(core.parse_ack(self.raw(**kw), "n1", core.READY_DISABLE)[1], code)
        self.assertEqual(core.parse_ack(None, "n1", core.READY_DISABLE)[1], "ack_invalid")


class FakeChrome:
    """Stands in for macos_poc.ChromeRun: one process, one extension load, persistent sentinel."""

    def __init__(self, scan_replies):
        self.proc = type("P", (), {"pid": 42})()
        self.profile = pathlib.Path(tempfile.mkdtemp())
        self.store = {}
        self.loads = 0
        self.scans = list(scan_replies)
        self.closed = False

    def js(self, session, expr, timeout=70):
        if expr.startswith("localStorage.setItem"):
            self.store["v"] = expr.split("'")[3]
            return 1
        if expr.startswith("localStorage.getItem"):
            return self.store.get("v")
        if expr.startswith("otp23.hello"):
            return {"type": "ready"}
        if expr.startswith("otp23.startScan"):
            return self.scans.pop(0)
        if expr.startswith("otp23.drainEvents"):
            return [{"message": {"type": "device_found", "device_handle": "d1"}}]
        if expr.startswith("otp23.stopScan"):
            return {"status": "stopped"}
        if expr.startswith("otp23.shutdown"):
            return {"type": "shutdown_complete"}
        return 1


class WaitTests(unittest.TestCase):
    def test_wait_for_ack_times_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctl = driver.Controller(pathlib.Path(tmp), sleep=lambda s: None, clock=iter(range(0, 1000, 100)).__next__)
            ctl.publish(core.READY_DISABLE)
            ack, err = ctl.wait_ack(core.READY_DISABLE, timeout=250)
            self.assertIsNone(ack)
            self.assertEqual(err, "ack_missing")
            ready = json.loads((pathlib.Path(tmp) / "ready.json").read_text())
            self.assertEqual(ready["phase"], core.READY_DISABLE)

    def test_stale_ack_removed_and_valid_ack_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            (tmp / "ack.json").write_text("{}")
            ctl = driver.Controller(tmp, sleep=lambda s: None)
            ctl.publish(core.READY_ENABLE)
            self.assertFalse((tmp / "ack.json").exists())
            nonce = json.loads((tmp / "ready.json").read_text())["nonce"]
            (tmp / "ack.json").write_text(json.dumps({
                "nonce": nonce, "ready": core.READY_ENABLE, "toggled": "on",
                "entry_label": "VelesOtp23PoC", "prompt": "unknown"}))
            ack, err = ctl.wait_ack(core.READY_ENABLE, timeout=5)
            self.assertIsNone(err)
            self.assertEqual(ack["toggled"], "on")


class RunTests(unittest.TestCase):
    def run_mock(self, scans, ack_denied="off"):
        from unittest import mock
        import argparse
        tmp = pathlib.Path(tempfile.mkdtemp())
        app = tmp / "App.app"
        binary = app / "Contents/MacOS/otp23-host"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"bin")
        chrome = FakeChrome(scans)
        chrome.cdp = type("C", (), {"call": lambda self, m, p=None, **k: (setattr(chrome, "loads", chrome.loads + 1)
                                                                            or {"id": "gmekhgkipjkikmaadaajljinkgfielei"})})()
        chrome.open_page = lambda url: ("t", "s")

        def close():
            chrome.closed = True
            chrome.profile.rmdir()
        chrome.close = close
        args = argparse.Namespace(app=app, manifest_dir=tmp, chrome="c", max_seconds=600, wait_seconds=5,
                                  state_dir=tmp / "state", expect_binary_sha_prefix=None,
                                  human_note=None)
        run = driver.Run(args, tmp / "out.json", chrome_factory=lambda **kw: chrome)
        phases = iter([(core.READY_DISABLE, ack_denied), (core.READY_ENABLE, "on")])

        def answer(phase, timeout):
            want, toggled = next(phases)
            return {"toggled": toggled, "entry_label": "VelesOtp23PoC", "prompt": "unknown"}, None
        run.controller.publish = lambda phase: None
        run.controller.wait_ack = answer
        denied = {"type": "error", "code": "scan_failed", "stage": "access_denied"}
        with mock.patch.object(driver.macos_poc, "check_registration", return_value=tmp), \
                mock.patch.object(driver.macos_poc, "ps_snapshot", return_value=""), \
                mock.patch.object(driver.macos_poc, "wait_gone", return_value=0.1), \
                mock.patch.object(driver, "signature_info", return_value=SIG), \
                mock.patch.object(driver.Run, "host_row", return_value=7), \
                mock.patch.object(driver.time, "sleep", lambda s: None):
            code = run.run()
        return code, run.result

    def test_missing_note_is_not_supplied(self):
        started = {"status": "started", "scan_handle": "h"}
        denied = {"type": "error", "code": "scan_failed", "stage": "access_denied"}
        _, result = self.run_mock([started, denied, started])
        self.assertEqual(result["human_settings_observation"]["text"], None)
        self.assertEqual(result["human_settings_observation"]["source"], "not_supplied")
        self.assertNotIn("I enabled", json.dumps(result))

    def test_explicit_note_recorded_as_user_report(self):
        note = "I enabled VelesOtp23PoC Bluetooth permission. Chrome permission was enabled before that."
        obs = core.human_observation(note)
        self.assertEqual((obs["text"], obs["source"]), (note, "user report"))
        self.assertEqual(core.human_observation("  ")["source"], "not_supplied")

    def test_cli_default_is_none_and_no_canned_claim(self):
        import io
        from unittest import mock
        captured = {}
        with mock.patch.object(driver, "Run", lambda args, out: captured.update(a=args) or
                               type("R", (), {"run": lambda self: 0})()), \
                mock.patch.object(driver.sys, "platform", "darwin"):
            driver.main(["--out", "o.json", "--state-dir", "s"])
        self.assertIsNone(captured["a"].human_note)
        self.assertNotIn("I enabled", (HERE / "macos_permission_recovery_core.py").read_text())
        self.assertNotIn("I enabled", (HERE / "macos_permission_recovery.py").read_text())

    def test_full_mock_pass(self):
        started = {"status": "started", "scan_handle": "h"}
        denied = {"type": "error", "code": "scan_failed", "stage": "access_denied"}
        code, result = self.run_mock([started, denied, started])
        self.assertEqual(result["failures"], [])
        self.assertEqual(code, 0)
        self.assertNotIn("sentinel_value", json.dumps(result))

    def test_mock_denial_not_observed_fails(self):
        started = {"status": "started", "scan_handle": "h"}
        code, result = self.run_mock([started, started, started])
        self.assertEqual(code, 1)
        self.assertIn("denied:deny_not_observed", result["failures"])


class SourceTests(unittest.TestCase):
    def test_driver_never_touches_tcc_or_settings(self):
        src = (HERE / "macos_permission_recovery.py").read_text()
        for banned in ("tccutil", "sqlite3", "TCC.db", "codesign --force", "codesign -f", "-s -",
                       "x-apple.systempreferences", "open -a", "security "):
            self.assertNotIn(banned, src)
        self.assertEqual(src.count('"Extensions.loadUnpacked"'), 1)


if __name__ == "__main__":
    unittest.main()
