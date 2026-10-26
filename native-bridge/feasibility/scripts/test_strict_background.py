"""Regression tests for the strict background validation: verdict logic and structural driver guards.

No browser, host or phone is involved; the verdict is only as good as the evidence a physical run feeds
it, and every check here proves one way a weaker run would be refused.
"""

import ast
import copy
import hashlib
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strict_background_core as core  # noqa: E402

SRC = (HERE / "strict_background.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)
WANT = core.expected_sha256()


def good_evidence(scenario="cold"):
    closed_wall, reopened_wall = 1_000_000.0, 1_000_000.0 + 760.0
    return {
        "scenario": scenario,
        "min_background_s": 600,
        "handles": ["aaaa1111", "bbbb2222"],
        "pre_close_probe": {"connector_visibility": "hidden", "ordinary_tab_visibility": "visible",
                            "ordinary_tab_focused": True},
        "timeline": {"cdp_closed_mono": 100.0, "cdp_reopened_mono": 860.0,
                     "cdp_closed_wall": closed_wall, "cdp_reopened_wall": reopened_wall},
        "cdp_calls_during_idle": 0,
        "browser_level_connection_closed": True,
        "connector_session_detached_by_close": True,
        "exemption_scan": {"files": ["connector.js"], "findings": []},
        "events": [{"handle": h, "wall": (closed_wall + 655 + i) * 1000, "visibility": "hidden", "focused": False,
                    "record_sha256": WANT, "len": 4096} for i, h in enumerate(["aaaa1111", "bbbb2222"])],
        "host_trace": {"message_count": 2, "sends_in_trace": 0 if scenario == "cold" else 2,
                       "message_wall_estimates": [closed_wall + 655.5, closed_wall + 656.5]},
        "title_probe": {"title_before_attach": "otp23 messages=2 last=1"},
        "warmup": None if scenario == "cold" else {"aaaa1111": {}, "bbbb2222": {}},
        "phone_evidence": [{"pushes": [{"queued": True, "sha256": WANT, "bytes": 4096,
                                        "subscribed_at_epoch_ms": 1, "fired_at_epoch_ms": 690_100,
                                        "configured_delay_ms": 690_000}]},
                           {"pushes": [{"queued": True, "sha256": WANT, "bytes": 4096,
                                        "subscribed_at_epoch_ms": 5, "fired_at_epoch_ms": 690_300,
                                        "configured_delay_ms": 690_000}]}],
        "cleanup": {"shutdown_complete": True, "surviving_hosts": []},
    }


def verdict(evidence):
    return core.evaluate(evidence)


def status(evidence, check):
    return verdict(evidence)["checks"][check]["status"]


class FixtureAgreement(unittest.TestCase):
    def test_expected_record_matches_the_android_fixture_definition(self):
        record = core.expected_unsolicited()
        self.assertEqual(len(record), 4096)
        self.assertEqual(record[:4], b"VEL\x03")
        self.assertTrue(all(record[i] == (i + 61) & 0xFF for i in range(4, 4096)))
        self.assertEqual(WANT, hashlib.sha256(record).hexdigest())
        kotlin = (HERE.parent.parent.parent / "app/src/main/java/me/nagaev/veles/sharing/SyntheticTransportFixture.kt").read_text()
        self.assertIn("UNSOLICITED_SEED = 61", kotlin)
        self.assertIn("0x56, 0x45, 0x4c, 3", kotlin)


class Verdict(unittest.TestCase):
    def test_complete_good_evidence_passes_every_check_in_both_scenarios(self):
        for scenario in ("cold", "prewarm"):
            v = verdict(good_evidence(scenario))
            self.assertEqual(v["strict_result"], "PASS", (scenario, v["checks"]))
            self.assertFalse(v["formal_otp23_acceptance"], "the driver never declares acceptance")

    def test_a_connected_browser_level_client_fails_the_idle_check(self):
        e = good_evidence()
        e["browser_level_connection_closed"] = False
        self.assertEqual(status(e, "ST-02"), "FAIL")

    def test_any_cdp_call_during_idle_fails(self):
        e = good_evidence()
        e["cdp_calls_during_idle"] = 1
        self.assertEqual(status(e, "ST-02"), "FAIL")

    def test_short_idle_fails(self):
        e = good_evidence()
        e["timeline"]["cdp_reopened_mono"] = 100.0 + 599.0
        self.assertEqual(status(e, "ST-02"), "FAIL")

    def test_receipt_before_ten_minutes_fails(self):
        e = good_evidence()
        e["events"][0]["wall"] = (1_000_000.0 + 599.0) * 1000
        self.assertEqual(status(e, "ST-05"), "FAIL")

    def test_receipt_only_after_reattach_fails_even_if_late_enough(self):
        e = good_evidence()
        e["events"][1]["wall"] = (e["timeline"]["cdp_reopened_wall"] + 1) * 1000
        self.assertEqual(status(e, "ST-05"), "FAIL")

    def test_visible_or_focused_page_at_receipt_fails(self):
        for field, value in (("visibility", "visible"), ("focused", True)):
            e = good_evidence()
            e["events"][0][field] = value
            self.assertEqual(status(e, "ST-04"), "FAIL", field)

    def test_missing_wrong_or_duplicate_messages_fail(self):
        e = good_evidence()
        e["events"].pop()
        self.assertEqual(status(e, "ST-04"), "FAIL")
        e = good_evidence()
        e["events"][0]["record_sha256"] = "00" * 32
        self.assertEqual(status(e, "ST-04"), "FAIL")
        e = good_evidence()
        e["events"].append(copy.deepcopy(e["events"][0]))
        self.assertEqual(status(e, "ST-04"), "FAIL")

    def test_one_phone_is_not_enough(self):
        e = good_evidence()
        e["handles"] = e["handles"][:1]
        e["events"] = e["events"][:1]
        self.assertEqual(status(e, "ST-04"), "FAIL")

    def test_connector_must_be_hidden_with_an_ordinary_tab_focused(self):
        for field, value in (("connector_visibility", "visible"), ("ordinary_tab_visibility", "hidden"),
                             ("ordinary_tab_focused", False)):
            e = good_evidence()
            e["pre_close_probe"][field] = value
            self.assertEqual(status(e, "ST-01"), "FAIL", field)

    def test_exemption_findings_or_an_unscanned_extension_fail(self):
        e = good_evidence()
        e["exemption_scan"]["findings"] = [{"file": "connector.js", "kind": "audio", "match": "AudioContext"}]
        self.assertEqual(status(e, "ST-03"), "FAIL")
        e = good_evidence()
        e["exemption_scan"]["files"] = []
        self.assertEqual(status(e, "ST-03"), "FAIL")

    def test_host_trace_must_precede_reattach_independently(self):
        e = good_evidence()
        e["host_trace"]["message_wall_estimates"] = [e["timeline"]["cdp_reopened_wall"] - 1, 0]
        self.assertEqual(status(e, "ST-06"), "FAIL")
        e = good_evidence()
        e["host_trace"] = {}
        self.assertEqual(status(e, "ST-06"), "BLOCKED")

    def test_target_list_title_must_show_both_receipts_before_attach(self):
        e = good_evidence()
        e["title_probe"]["title_before_attach"] = "otp23 messages=1 last=1"
        self.assertEqual(status(e, "ST-07"), "FAIL")

    def test_missing_phone_evidence_is_blocked_never_pass(self):
        e = good_evidence()
        e["phone_evidence"] = None
        v = verdict(e)
        self.assertEqual(v["checks"]["ST-08"]["status"], "BLOCKED")
        self.assertEqual(v["strict_result"], "BLOCKED")

    def test_phone_evidence_with_wrong_hash_or_early_fire_fails(self):
        e = good_evidence()
        e["phone_evidence"][0]["pushes"][0]["sha256"] = "00" * 32
        self.assertEqual(status(e, "ST-08"), "FAIL")
        e = good_evidence()
        e["phone_evidence"][1]["pushes"][0]["fired_at_epoch_ms"] = 100
        self.assertEqual(status(e, "ST-08"), "FAIL")

    def test_cold_and_prewarm_are_kept_apart(self):
        cold = good_evidence("cold")
        cold["host_trace"]["sends_in_trace"] = 2
        self.assertEqual(status(cold, "ST-09"), "FAIL", "a cold run may not contain a desktop->phone send")
        warm = good_evidence("prewarm")
        warm["warmup"] = None
        self.assertEqual(status(warm, "ST-09"), "FAIL")
        unknown = good_evidence()
        unknown["scenario"] = "mixed"
        self.assertEqual(status(unknown, "ST-09"), "FAIL")

    def test_cleanup_failures_fail(self):
        e = good_evidence()
        e["cleanup"]["surviving_hosts"] = [123]
        self.assertEqual(status(e, "ST-10"), "FAIL")


class Helpers(unittest.TestCase):
    def test_idle_deadline_waits_for_the_last_phone_plus_grace(self):
        self.assertEqual(core.idle_deadline_monotonic({"a": 10.0, "b": 25.0}, 690, 30), 745.0)
        with self.assertRaises(ValueError):
            core.idle_deadline_monotonic({})

    def test_exemption_scan_flags_each_family_and_ignores_comments(self):
        samples = {
            "audio": "const a = new AudioContext();", "webrtc": "new RTCPeerConnection({})",
            "socket": "const s = new WebSocket('ws://x');", "interval_timer": "setInterval(f, 1000)",
            "web_lock": "navigator.locks.request('x', f)", "offscreen_or_alarm": "chrome.alarms.create('k', {})",
            "keepalive_ping": "wakeLock.request()",
        }
        for kind, code in samples.items():
            found = core.scan_extension_for_exemptions({"x.js": code})
            self.assertEqual([f["kind"] for f in found][:1], [kind], code)
        self.assertEqual(core.scan_extension_for_exemptions({"x.js": "// setInterval(f, 1)\n/* new AudioContext() */"}), [])

    def test_the_real_extension_files_are_clean(self):
        ext = HERE.parent / "extension"
        files = {p.name: p.read_text(encoding="utf-8") for p in ext.iterdir() if p.suffix in (".js", ".html", ".json")}
        self.assertEqual(core.scan_extension_for_exemptions(files), [])

    def test_trace_parser_keeps_only_safe_fields(self):
        trace = core.parse_host_trace(
            "[       0ms pid=1] start pid=1\n"
            "[       1ms pid=1] in  len=81 type=hello request_id=r1\n"
            "[     900ms pid=1] in  len=5599 type=send request_id=r6 record_base64.len=5464\n"
            "[  700000ms pid=1] out len=5599 type=message api_version=1 connection_handle=ab record_base64.len=5464\n")
        self.assertEqual(trace["hello_ms"], 1)
        self.assertEqual(trace["messages"], [{"at_ms": 700000, "record_base64_len": 5464}])
        self.assertEqual(trace["sends"], [{"at_ms": 900}])


class DriverStructure(unittest.TestCase):
    def fn(self, name):
        for node in ast.walk(TREE):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(SRC, node)
        self.fail(f"missing {name}")

    def test_idle_phase_touches_no_browser_object(self):
        idle = ast.parse("if True:\n" + "\n".join("    " + l for l in self.fn("ph_idle").splitlines()))
        names = {n.id for n in ast.walk(idle) if isinstance(n, ast.Name)}
        for forbidden in ("chrome", "urllib", "Cdp", "WebSocket", "cv"):
            self.assertNotIn(forbidden, names, f"{forbidden!r} would break the no-DevTools-client interval")
        self.assertIn("sleep", {n.attr for n in ast.walk(idle) if isinstance(n, ast.Attribute)})

    def test_background_phase_closes_the_browser_connection_not_just_the_target_session(self):
        bg = self.fn("ph_background")
        self.assertIn("chrome.cdp.ws.close()", bg)
        self.assertIn("chrome.cdp = None", bg)
        self.assertLess(bg.index("chrome.cdp.ws.close()"), bg.index("chrome.cdp = None"))
        self.assertNotIn("Target.detachFromTarget", bg, "a detached target session with the browser connection open is the old, weaker test")
        self.assertNotIn("setTimeout", bg, "no desktop-side timer may produce the event")

    def test_no_desktop_send_in_the_cold_path(self):
        run = self.fn("run")
        self.assertIn('args.scenario == "prewarm"', run)
        self.assertIn("ph_warmup", run)
        for name in ("ph_background", "ph_idle", "ph_reattach"):
            self.assertNotIn("otp23.send", self.fn(name))

    def test_reads_title_through_http_before_attaching(self):
        reattach = self.fn("ph_reattach")
        self.assertLess(reattach.index("/json/list"), reattach.index("Cdp(WebSocket"))

    def test_driver_never_claims_acceptance(self):
        self.assertIn('"formal_otp23_acceptance": False', self.fn("run"))
        self.assertNotIn('"formal_otp23_acceptance": True', SRC)


if __name__ == "__main__":
    unittest.main()
