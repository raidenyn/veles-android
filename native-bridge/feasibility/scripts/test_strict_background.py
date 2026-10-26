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
        "cleanup": {"shutdown_complete": True, "chrome_closed": True, "surviving_hosts": [], "errors": []},
        "diagnostic_short": False,
        "chrome": {"args": ["/chrome", "--user-data-dir=/tmp/p", "--remote-debugging-port=0", "--no-first-run"],
                   "args_source": "proc.args"},
        "history_tokens": {"connector": "tok-c", "ordinary_tab": "tok-o"},
        "history": {
            "connector": {"token": "tok-c", "truncated": False,
                          "initial": {"wall": (closed_wall - 100) * 1000, "visibility": "hidden", "focused": False},
                          "events": [{"seq": 0, "kind": "visibilitychange", "wall": (closed_wall - 50) * 1000,
                                      "visibility": "hidden", "focused": False}]},
            "ordinary_tab": {"token": "tok-o", "truncated": False,
                             "initial": {"wall": (closed_wall - 60) * 1000, "visibility": "hidden", "focused": False},
                             "events": [{"seq": 0, "kind": "visibilitychange", "wall": (closed_wall - 30) * 1000,
                                         "visibility": "visible", "focused": False},
                                        {"seq": 1, "kind": "focus", "wall": (closed_wall - 29) * 1000,
                                         "visibility": "visible", "focused": True}]},
        },
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


def bad_event(wall_offset_s, kind, visibility, focused, seq):
    return {"seq": seq, "kind": kind, "wall": (1_000_000.0 + wall_offset_s) * 1000,
            "visibility": visibility, "focused": focused}


class History(unittest.TestCase):
    def test_interrupted_ordinary_focus_fails(self):
        e = good_evidence()
        e["history"]["ordinary_tab"]["events"] += [bad_event(200, "blur", "visible", False, 2),
                                                   bad_event(201, "focus", "visible", True, 3)]
        v = verdict(e)["checks"]["ST-12"]
        self.assertEqual(v["status"], "FAIL")
        self.assertEqual(len(v["actual"]["interruptions"]), 1, "a blur that is later undone is still an interruption")

    def test_connector_becoming_visible_mid_interval_fails(self):
        e = good_evidence()
        e["history"]["connector"]["events"] += [bad_event(300, "visibilitychange", "visible", False, 1),
                                                bad_event(310, "visibilitychange", "hidden", False, 2)]
        self.assertEqual(status(e, "ST-11"), "FAIL")

    def test_connector_focus_mid_interval_fails(self):
        e = good_evidence()
        e["history"]["connector"]["events"].append(bad_event(100, "focus", "hidden", True, 1))
        self.assertEqual(status(e, "ST-11"), "FAIL")

    def test_missing_absent_or_truncated_history_fails(self):
        for key, check in (("connector", "ST-11"), ("ordinary_tab", "ST-12")):
            e = good_evidence()
            del e["history"][key]
            self.assertEqual(status(e, check), "FAIL", key)
            e = good_evidence()
            e["history"][key]["truncated"] = True
            self.assertEqual(status(e, check), "FAIL", key)
        e = good_evidence()
        del e["history"]
        self.assertEqual((status(e, "ST-11"), status(e, "ST-12")), ("FAIL", "FAIL"))

    def test_sequence_gap_is_truncation(self):
        e = good_evidence()
        e["history"]["ordinary_tab"]["events"][1]["seq"] = 5
        self.assertEqual(status(e, "ST-12"), "FAIL")

    def test_missing_initial_focus_or_visibility_fails(self):
        for field in ("focused", "visibility"):
            e = good_evidence()
            e["history"]["ordinary_tab"]["initial"].pop(field)
            self.assertEqual(status(e, "ST-12"), "FAIL", field)
        e = good_evidence()
        e["history"]["connector"]["initial"]["focused"] = None
        self.assertEqual(status(e, "ST-11"), "FAIL")

    def test_false_ordinary_focus_at_interval_start_fails(self):
        e = good_evidence()
        e["history"]["ordinary_tab"]["events"].pop()
        self.assertEqual(status(e, "ST-12"), "FAIL")

    def test_events_after_the_pre_attach_cut_are_ignored_but_counted(self):
        e = good_evidence()
        cut = e["timeline"]["cdp_reopened_wall"]
        e["history"]["ordinary_tab"]["events"] += [
            {"seq": 2, "kind": "blur", "wall": (cut + 0.5) * 1000, "visibility": "visible", "focused": False}]
        v = verdict(e)["checks"]["ST-12"]
        self.assertEqual(v["status"], "PASS")
        self.assertEqual(v["actual"]["events_after_cut_ignored"], 1)

    def test_event_before_the_cut_is_not_ignored(self):
        e = good_evidence()
        cut = e["timeline"]["cdp_reopened_wall"]
        e["history"]["ordinary_tab"]["events"].append(
            {"seq": 2, "kind": "blur", "wall": (cut - 0.5) * 1000, "visibility": "visible", "focused": False})
        self.assertEqual(status(e, "ST-12"), "FAIL")

    def test_replaced_page_token_or_late_install_fails(self):
        e = good_evidence()
        e["history_tokens"]["ordinary_tab"] = "other"
        self.assertEqual(status(e, "ST-12"), "FAIL")
        e = good_evidence()
        e["history"]["connector"]["initial"]["wall"] = (e["timeline"]["cdp_closed_wall"] + 1) * 1000
        self.assertEqual(status(e, "ST-11"), "FAIL")

    def test_history_interval_must_be_ten_minutes(self):
        e = good_evidence()
        e["timeline"]["cdp_reopened_wall"] = e["timeline"]["cdp_closed_wall"] + 599
        self.assertEqual(status(e, "ST-11"), "FAIL")

    def test_ordinary_install_script_has_no_timer_or_keepalive(self):
        for pattern in ("setInterval", "setTimeout", "requestAnimationFrame", "Audio", "WebSocket", "RTC", "locks"):
            self.assertNotIn(pattern, core.ORDINARY_HISTORY_JS)
        self.assertEqual(core.scan_extension_for_exemptions({"ordinary.js": core.ORDINARY_HISTORY_JS}), [])


class ChromeFlags(unittest.TestCase):
    def test_clean_command_line_passes(self):
        self.assertEqual(status(good_evidence(), "ST-13"), "PASS")

    def test_exemption_flags_fail(self):
        for flag in ("--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                     "--disable-backgrounding-occluded-windows", "--disable-features=IntensiveWakeUpThrottling",
                     "--disable-features=CalculateNativeWinOcclusion,TimerThrottlingForBackgroundTabs",
                     "--enable-features=AllowFreezingBackgroundTabs", "--disable-ipc-flooding-protection"):
            e = good_evidence()
            e["chrome"]["args"].append(flag)
            self.assertEqual(status(e, "ST-13"), "FAIL", flag)

    def test_unrecorded_command_line_fails(self):
        for chrome in ({}, {"args": []}, {"args": None}):
            e = good_evidence()
            e["chrome"] = chrome
            self.assertEqual(status(e, "ST-13"), "FAIL")

    def test_unrelated_feature_flags_do_not_trip(self):
        e = good_evidence()
        e["chrome"]["args"].append("--disable-features=Translate")
        self.assertEqual(status(e, "ST-13"), "PASS")

    def test_native_messaging_exemption_is_disclosed_not_claimed_excluded(self):
        v = verdict(good_evidence())
        self.assertTrue(v["checks"]["ST-13"]["actual"]["native_messaging_exemption_not_excluded"])
        self.assertFalse(v["formal_otp23_acceptance"])


class ShortRunGuard(unittest.TestCase):
    def test_short_minimum_never_passes(self):
        e = good_evidence()
        e["min_background_s"] = 300
        self.assertEqual(status(e, "ST-14"), "FAIL")
        self.assertNotEqual(verdict(e)["strict_result"], "PASS")

    def test_labelled_diagnostic_is_reported_as_diagnostic_only(self):
        e = good_evidence()
        e["min_background_s"] = 300
        e["diagnostic_short"] = True
        v = verdict(e)
        self.assertEqual(v["strict_result"], "DIAGNOSTIC_ONLY")
        self.assertEqual(status(e, "ST-14"), "FAIL")

    def test_diagnostic_label_with_real_length_still_cannot_be_formal(self):
        e = good_evidence()
        e["diagnostic_short"] = True
        self.assertNotEqual(verdict(e)["strict_result"], "PASS")

    def test_cleanup_requires_chrome_closed_and_no_orphans(self):
        e = good_evidence()
        e["cleanup"]["chrome_closed"] = False
        self.assertEqual(status(e, "ST-10"), "FAIL")


class RecordAndReceipt(unittest.TestCase):
    def test_record_hash_and_receipt_before_attach_both_required(self):
        e = good_evidence()
        e["events"][0]["record_sha256"] = "11" * 32
        self.assertEqual(status(e, "ST-04"), "FAIL")
        e = good_evidence()
        e["events"][0]["wall"] = (e["timeline"]["cdp_reopened_wall"] + 2) * 1000
        self.assertEqual(status(e, "ST-05"), "FAIL")
        self.assertEqual(status(good_evidence(), "ST-04"), "PASS")
        self.assertEqual(status(good_evidence(), "ST-05"), "PASS")


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

    def test_histories_are_installed_before_close_and_read_after_attach(self):
        bg, reattach = self.fn("ph_background"), self.fn("ph_reattach")
        self.assertLess(bg.index("ORDINARY_HISTORY_JS"), bg.index("chrome.cdp.ws.close()"))
        self.assertLess(bg.index("ORDINARY_HISTORY_JS"), bg.index("Target.activateTarget"))
        self.assertLess(reattach.index("Cdp(WebSocket"), reattach.index("visibilityHistory"))
        self.assertNotIn("visibilityHistory", self.fn("ph_idle"))
        self.assertNotIn("visibilityHistory", bg, "history is collected only after the measurement")

    def test_cut_time_is_taken_before_attach(self):
        reattach = self.fn("ph_reattach")
        self.assertLess(reattach.index('"cdp_reopened_wall"'), reattach.index("Cdp(WebSocket"))

    def test_chrome_command_line_is_recorded_in_env_phase(self):
        self.assertIn("chrome_command_line(chrome)", self.fn("ph_env"))

    def test_cleanup_counts_hosts_after_chrome_close(self):
        run = self.fn("run")
        self.assertLess(run.index("chrome.close()"), run.index('"surviving_hosts"] = sorted'))

    def test_short_runs_require_the_diagnostic_label(self):
        self.assertIn("requires --diagnostic-short", SRC)

    def test_no_process_killing_outside_the_owned_chrome(self):
        for forbidden in ("taskkill", "pkill", "killall", "os.kill", "Stop-Process"):
            self.assertNotIn(forbidden, SRC)

    def test_phone_evidence_collection_never_raises(self):
        def boom(*a, **k):
            raise OSError("adb missing")
        import json, pathlib, subprocess  # noqa: E401 - the driver imports platform CDP modules, so exec the functions alone
        namespace = {"json": json, "pathlib": pathlib, "subprocess": subprocess, "PACKAGE": "p", "EVIDENCE_FILE": "f"}
        for name in ("pull_phone_evidence", "collect_phone_evidence"):
            exec(self.fn(name), namespace)
        phone, errors = namespace["collect_phone_evidence"](["/nonexistent/file.json"], ["SER1"], "adb", boom)
        self.assertEqual(phone, [])
        self.assertEqual(len(errors), 2)
        self.assertNotIn("SER1", " ".join(errors), "serials are not published")

    def test_driver_never_claims_acceptance(self):
        self.assertIn('"formal_otp23_acceptance": False', self.fn("run"))
        self.assertNotIn('"formal_otp23_acceptance": True', SRC)


class InvokeWrapper(unittest.TestCase):
    """Source guards for scripts/invoke-poc.ps1 (PowerShell is not run here)."""

    PS1 = (HERE / "invoke-poc.ps1").read_text(encoding="utf-8")

    def test_strict_is_allowlisted_and_existing_modes_are_kept(self):
        self.assertIn("'physical_validate.py', 'poc_background.py', 'strict_background.py'", self.PS1)
        self.assertIn("'--flavour', 'plain', '--out', $outAbs", self.PS1)
        self.assertIn("--diagnostic-delay-seconds", self.PS1)

    def test_strict_branch_passes_scenario_and_never_flavour(self):
        branch = self.PS1[self.PS1.index("if ($strict) {\n            # strict_background.py has no --flavour"):]
        branch = branch[:branch.index("} else {")]
        self.assertIn("'--scenario', $Scenario", branch)
        self.assertNotIn("--flavour", branch.replace("no --flavour option", ""))
        self.assertIn("'--adb-path', $AdbPath", branch)
        self.assertIn("'--adb', $serial", branch)

    def test_scheduled_mode_keeps_limited_interactive_token_and_owned_cleanup(self):
        self.assertIn("-LogonType Interactive -RunLevel Limited", self.PS1)
        self.assertIn("unregister-host.ps1') -Flavour plain", self.PS1)
        self.assertIn("poc-run-status.json", self.PS1)
        self.assertNotIn("Stop-Process", self.PS1)
        self.assertNotIn("taskkill", self.PS1)

    def test_serials_are_validated_and_not_written_to_the_status_file(self):
        self.assertIn("^[A-Za-z0-9._:-]{1,64}$", self.PS1)
        self.assertIn("phone_serial_count", self.PS1)
        self.assertIn("--adb <redacted>", self.PS1)

    def test_scenario_is_required_for_strict_and_rejected_otherwise(self):
        self.assertIn("-Scenario cold|prewarm is required", self.PS1)
        self.assertIn("only valid with -Script strict_background.py", self.PS1)


if __name__ == "__main__":
    unittest.main()
