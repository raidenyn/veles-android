"""Unit tests for scripts/mac_release_bound.py (parent P0-A decision + Opus preflight Task 3).

Hand-computed interval cases (no numeric-schema fuzzing, per the task), plus the four named
edge cases: conservative intersection with the FULL interval recorded, ambiguous burst, missing
events, stop confound — and a replay of the RETAINED ABORTED mac-resume-2026-10-28/lifecycle-1
data that must come out `diagnostic_only_not_creditable`, never target teardown PASS evidence.

Usage:  python3 scripts/test_mac_release_bound.py
"""

import json
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mac_release_bound as mrb

# Hand-computed synthetic case: the same causal structure as the real retained data.
# trace: proof pairs (2199→2244), (2256→2318); 4096 pairs (2372→2815), (2851→3252); exit 3667.
TRACE = """\
[    2000ms pid=7] out len=135 type=scan_state request_id=r2 api_version=1 status=stopped
[    2199ms pid=7] in  len=115 type=send request_id=r6 api_version=1 connection_handle=aaaa record_base64.len=24
[    2244ms pid=7] out len=137 type=message api_version=1 connection_handle=aaaa record_base64.len=24
[    2256ms pid=7] in  len=115 type=send request_id=r7 api_version=1 connection_handle=bbbb record_base64.len=24
[    2318ms pid=7] out len=137 type=message api_version=1 connection_handle=bbbb record_base64.len=24
[    2372ms pid=7] in  len=5599 type=send request_id=r8 api_version=1 connection_handle=aaaa record_base64.len=5464
[    2815ms pid=7] out len=5577 type=message api_version=1 connection_handle=aaaa record_base64.len=5464
[    2851ms pid=7] in  len=5599 type=send request_id=r9 api_version=1 connection_handle=bbbb record_base64.len=5464
[    3252ms pid=7] out len=5577 type=message api_version=1 connection_handle=bbbb record_base64.len=5464
[    3667ms pid=7] exit code=0 stdout_drained=true
"""

B = 1793202259264  # hand-picked phone-epoch base (same magnitude as the retained data, phone clock)


def proof_pair(t_in, t_msg, handle="aaaa", size=24):
    return f"[{t_in}ms pid=7] in  len=115 type=send request_id=x api_version=1 connection_handle={handle} record_base64.len={size}\n" \
           f"[{t_msg}ms pid=7] out len=137 type=message api_version=1 connection_handle={handle} record_base64.len={size}\n"


def transport_doc(events, run_id="R", prefix="abcdef0123456789"):
    return {"kind": "otp23-transport-evidence", "run_id": run_id, "final": True,
            "final_after_service_stopped": True, "clean": True, "truncated": False,
            "peers_dropped": 0, "peers": [
                {"peer_sha256_prefix": prefix, "truncated": False,
                 "notify_calls": sum(1 for e in events if e["kind"] == "notify_api"),
                 "api_success": sum(1 for e in events if e["kind"] == "notify_api"),
                 "callbacks": sum(1 for e in events if e["kind"] == "notify_callback"),
                 "connects": sum(1 for e in events if e["kind"] == "connected"),
                 "disconnects": sum(1 for e in events if e["kind"] == "disconnected"),
                 "events": events}]}


def burst_events(e16, e_frag, dis, connect, notify_fragment=512):
    events = [{"seq": 1, "kind": "connected", "epoch_ms": connect}, {"seq": 2, "kind": "mtu", "epoch_ms": connect + 35, "code": 517},
              {"seq": 3, "kind": "notify_api", "epoch_ms": e16, "bytes": 20},
              {"seq": 9, "kind": "notify_api", "epoch_ms": e_frag, "bytes": notify_fragment, "code": 0}]
    for i, extra in enumerate([e_frag + j * 4 for j in range(1, 9)]):
        events.append({"seq": 10 + i, "kind": "notify_api", "epoch_ms": extra, "bytes": 512, "code": 0})
    events.append({"seq": 40, "kind": "disconnected", "epoch_ms": dis})
    return events


class HandComputed(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="release-bound-"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_case(self, trace=TRACE, a_events=None, b_events=None, scenario_status="PASS",
                   teardown=None):
        (self.dir / "shutdown.host-trace.log").write_text(trace, encoding="utf-8")
        # REAL Chrome PASS shape (Kimi F-1): trace_exit lives NESTED under detail.teardown — verify_gone
        # returns that dict; a top-level direct-mirror shape never occurs in driver output.
        detail = teardown if teardown is not None else \
            {"ready": {"type": "ready"},
             "teardown": {"owned_gone_after_s": 0.1, "trace_mechanism": "stdin_closed",
                          "trace_exit": {"at_ms": 3667, "code": 0}}}
        (self.dir / "shutdown.json").write_text(json.dumps(
            {"status": scenario_status, "detail": detail}), encoding="utf-8")
        a_events = a_events or burst_events(B, B + 360, B + 1473, B - 931)
        b_events = b_events or burst_events(B - 400, B - 40, B + 900, B - 1100)
        a = self.dir / "transport-2be02e41.json"
        b = self.dir / "transport-864a3db0.json"
        a.write_text(json.dumps(transport_doc(a_events)), encoding="utf-8")
        b.write_text(json.dumps(transport_doc(b_events), ), encoding="utf-8")
        stops = self.dir / "stops.json"
        stops.write_text(json.dumps([
            {"serial": "2be02e41", "stop_min_phone_ms": B + 200000, "stop_max_phone_ms": B + 200400,
             "method": "bracketed phone date, mac ms readback via ssh"},
            {"serial": "864a3db0", "stop_min_phone_ms": B + 200000, "stop_max_phone_ms": B + 200300,
             "method": "same"}]), encoding="utf-8")
        return a, b, stops

    def run_analyser(self, scenario="shutdown", a_events=None, b_events=None,
                     scenario_status="PASS", teardown=None):
        a, b, stops = self.write_case(a_events=a_events, b_events=b_events,
                                      scenario_status=scenario_status, teardown=teardown)
        return mrb.analyse(self.dir, scenario, [(a, json.loads(a.read_text())),
                                                (b, json.loads(b.read_text()))],
                           json.loads(stops.read_text()), 30000, None)

    def test_hand_computed_intersection_interval_and_release_checks(self):
        document = self.run_analyser()
        # B = 1793202259264; phone A: e16 = B, proof in/out hull (2199..2318) → 16B bracket
        #   [B−2318, B−2199]; e_frag = B−360? phone A e_frag = B+360; 4096 hull (2372..3252)
        #   → [B+360−3252, B+360−2372] = [B−2892, B−2012]; INTERSECTION =
        #   [max(B−2318, B−2892), min(B−2199, B−2012)] = [B−2318, B−2199] (width 119, u 60.5).
        phone_a = document["phones"][0]
        self.assertEqual(phone_a["origin_interval"]["lo"], B - 2318)
        self.assertEqual(phone_a["origin_interval"]["hi"], B - 2199)
        self.assertEqual(phone_a["origin_interval"]["width_ms"], 119)
        self.assertAlmostEqual(phone_a["origin_interval"]["uncertainty_ms"], 60.5)
        # DIS_phone = B+1473 → DIS_trace interval = [B+1473 − (B−2199), B+1473 − (B−2318)] = [3672, 3791]
        self.assertEqual(phone_a["dis_trace_interval"], [3672, 3791])
        self.assertTrue(phone_a["checks"]["release_after_last_exchange"], "3672 ≥ 3252 (last exchange message)")
        self.assertTrue(phone_a["checks"]["within_bound_of_own_exit"], "3791 ≤ 3667+30000")
        self.assertTrue(phone_a["checks"]["stop_after_disconnect"])
        self.assertTrue(phone_a["checks"]["stop_after_bound_plus_uncertainty"],
                        "B+200000 ≥ (B−2199) + 3667 + 30000 = B+31468")
        # phone B: e16 = B−400 → 16B envelope [B−2718, B−2599]; e_frag = B−40 → 4096 envelope
        # [B−40−3252, B−40−2372] = [B−3292, B−2412]; intersection [B−2718, B−2599] (width 119);
        # DIS = B+900 → [B+900−(B−2599), B+900−(B−2718)] = [3499, 3618]
        phone_b = document["phones"][1]
        self.assertEqual(phone_b["origin_interval"]["lo"], B - 2718)
        self.assertEqual(phone_b["origin_interval"]["hi"], B - 2599)
        self.assertEqual(phone_b["dis_trace_interval"], [3499, 3618])
        self.assertTrue(all(phone_b["checks"].values()))
        self.assertEqual(document["verdict"], "PASS")
        self.assertEqual(document["target_teardown_scenario"], True)
        self.assertEqual(document["creditability"], "target_evidence")
        self.assertIn("none —", document["absolute_mac_anchor"])
        self.assertEqual(document["helper_self_sha256"], mrb.SELF_SHA)
        self.assertEqual(document["helper_self_sha256"], mrb.file_sha256(Path(__file__).resolve().parent / "mac_release_bound.py"))

    def test_anchor_brackets_recorded_and_self_pinned(self):
        document = self.run_analyser()
        phone_a = document["phones"][0]
        self.assertEqual(phone_a["origin_interval"]["brackets"]["proof16"], [B - 2318, B - 2199])
        self.assertEqual(phone_a["origin_interval"]["brackets"]["probe4096"], [B - 2892, B - 2012])
        self.assertEqual(document["inputs"]["trace_sha256"], mrb.file_sha256(self.dir / "shutdown.host-trace.log"))

    def test_ambiguous_two_bursts_fail(self):
        a_events = burst_events(B, B + 360, B + 1473, B - 931)
        a_events += [{"seq": 41, "kind": "connected", "epoch_ms": B + 5000},
                     {"seq": 42, "kind": "disconnected", "epoch_ms": B + 6000}]
        document = self.run_analyser(a_events=a_events)
        self.assertEqual(document["verdict"], "FAIL")
        self.assertTrue(any("exactly one connected and one disconnected" in p
                            for p in document["problems"]), document["problems"])

    def test_missing_nofify_events_fail(self):
        events = [e for e in burst_events(B, B + 360, B + 1473, B - 931) if e["kind"] != "notify_api"]
        document = self.run_analyser(a_events=events)
        self.assertEqual(document["verdict"], "FAIL")
        self.assertTrue(any("missing events" in p for p in document["problems"]))

    def test_empty_anchor_intersection_reason_is_retained(self):
        # F-2: when the intersection is empty the SPECIFIC reason must survive in problems (not be
        # swallowed by the generic cannot-map message); brackets stay recorded; verdict FAIL.
        wrong_frag = burst_events(B, B + 15000, B + 16000, B - 931)  # fragment far from the proof
        document = self.run_analyser(a_events=wrong_frag)
        self.assertEqual(document["verdict"], "FAIL")
        phone_a = document["phones"][0]
        self.assertTrue(any("origin intersection empty" in p for p in document["problems"]),
                         document["problems"])
        self.assertGreater(phone_a["origin_interval"]["brackets"]["probe4096"][0],
                           phone_a["origin_interval"]["brackets"]["proof16"][1])
        self.assertEqual(document["phones"][0]["dis_trace_interval"], [None, None])

    def test_pass_never_credited_without_nested_trace_exit(self):
        # F-1: a PASS whose detail carries NO nested detail.teardown.trace_exit (an aborted/driver-
        # regression shape) is never credited as target evidence, even though status says PASS.
        document = self.run_analyser(scenario_status="PASS", teardown={})
        self.assertEqual(document["verdict"], "PASS", "the bound computation itself is unaffected")
        self.assertIs(document["target_teardown_scenario"], False)
        self.assertEqual(document["creditability"], "diagnostic_only_not_creditable")

    def test_failed_run_with_nested_teardown_is_never_credited(self):
        # F-1: the status gate and the nested-shape gate both apply — a FAIL run with teardown data
        # recorded is still not target evidence.
        document = self.run_analyser(scenario_status="FAIL")
        self.assertEqual(document["verdict"], "PASS")
        self.assertIs(document["target_teardown_scenario"], False)
        self.assertEqual(document["creditability"], "diagnostic_only_not_creditable")

    REAL_PASS = (HERE.parent / "evidence" / "post-grant-2026-10-26" / "windows-final" /
                 "post-grant-2026-10-27" / "windows-lifecycle-scan-active" / "shutdown.json")

    def test_real_driver_pass_detail_is_credited(self):
        # F-1: the gate is pinned to a REAL committed Chrome-scenario PASS artifact (driver output,
        # not a mirror of the analyzer's assumption): its detail, verbatim, must read target_evidence.
        real = json.loads(self.REAL_PASS.read_text(encoding="utf-8"))
        self.assertEqual(real["status"], "PASS")
        self.assertNotIn("trace_exit", real["detail"], "real shape: trace_exit is never top-level")
        document = self.run_analyser(scenario_status=real["status"], teardown=real["detail"])
        self.assertEqual(document["verdict"], "PASS")
        self.assertIs(document["target_teardown_scenario"], True)
        self.assertEqual(document["creditability"], "target_evidence")

    def test_stop_confound_early_stop_fails(self):
        # stop marker after the DIS event but BEFORE the mapped bound moment: confound guard holds
        self.write_case()
        a = self.dir / "transport-2be02e41.json"
        stops = self.dir / "stops.json"
        stops.write_text(json.dumps([
            {"serial": "2be02e41", "stop_min_phone_ms": B + 5000, "stop_max_phone_ms": B + 5001,
             "method": "bracketed"},  # after DIS (B+1473) but BEFORE B+31468
            {"serial": "864a3db0", "stop_min_phone_ms": B + 200000, "stop_max_phone_ms": B + 200300,
             "method": "bracketed"}]), encoding="utf-8")
        document = mrb.analyse(self.dir, "shutdown", [(a, json.loads(a.read_text()))],
                               json.loads(stops.read_text()), 30000, None)
        self.assertEqual(document["verdict"], "FAIL")
        phone_a = document["phones"][0]
        self.assertFalse(phone_a["checks"]["stop_after_bound_plus_uncertainty"])
        self.assertTrue(phone_a["checks"]["stop_after_disconnect"])

    def test_stop_before_disconnect_fails(self):
        self.write_case()
        a = self.dir / "transport-2be02e41.json"
        stops = self.dir / "stops.json"
        stops.write_text(json.dumps([
            {"serial": "2be02e41", "stop_min_phone_ms": B + 1000, "stop_max_phone_ms": B + 1001,
             "method": "bracketed"},  # BEFORE the DIS event (B+1473): stop-induced release
            {"serial": "864a3db0", "stop_min_phone_ms": B + 200000, "stop_max_phone_ms": B + 200300,
             "method": "bracketed"}]), encoding="utf-8")
        document = mrb.analyse(self.dir, "shutdown", [(a, json.loads(a.read_text()))],
                               json.loads(stops.read_text()), 30000, None)
        self.assertEqual(document["verdict"], "FAIL")
        self.assertFalse(document["phones"][0]["checks"]["stop_after_disconnect"])

    def test_missing_stop_markers_fail(self):
        self.write_case()
        document = mrb.analyse(self.dir, "shutdown",
                               [(self.dir / "transport-2be02e41.json", json.loads((self.dir / "transport-2be02e41.json").read_text()))],
                               [], 30000, None)
        self.assertEqual(document["verdict"], "FAIL")
        self.assertTrue(any("stop" in p for p in document["problems"]))

    def test_corroboration_raw_is_labelled_not_acceptance_proof(self):
        corroborate = self.dir / "clock.json"
        corroborate.write_text(json.dumps({"probes": []}), encoding="utf-8")
        self.write_case()
        document = mrb.analyse(self.dir, "shutdown",
                               [(self.dir / "transport-2be02e41.json", json.loads((self.dir / "transport-2be02e41.json").read_text())),
                                (self.dir / "transport-864a3db0.json", json.loads((self.dir / "transport-864a3db0.json").read_text()))],
                               json.loads((self.dir / "stops.json").read_text()), 30000,
                               {"path": corroborate.name, "sha256": mrb.file_sha256(corroborate),
                                "document": {}, "note": "CORROBORATION ONLY: known ±16-17 s raw diagnostic; never acceptance proof"})
        self.assertIn("CORROBORATION ONLY", document["inputs"]["corroboration"]["note"])

    RETAINED = HERE.parent / "evidence" / "mac-resume-2026-10-28"

    def test_retained_aborted_run_replays_as_diagnostic_only(self):
        """Replay the RETAINED ABORTED lifecycle-1 run: the causal machinery works and the anchor
        intersections are non-empty and narrow, but NOTHING is creditable as target teardown PASS
        (the scenarios aborted before teardown; their cleanup exits are diagnostic data only)."""
        if not (self.RETAINED / "lifecycle-1" / "shutdown.json").is_file():
            self.skipTest("retained mac-resume-2026-10-28 evidence not present")
        for scenario in ("shutdown", "port-disconnect", "tab-close", "failure-isolation"):
            with self.subTest(scenario):
                scenario_dir = self.RETAINED / "lifecycle-1"
                doc = self.build_retained_analysis(scenario_dir, scenario)
                self.assertEqual(doc["verdict"], "PASS",
                                 f"{scenario}: anchor machinery + bound checks on retained cleanup exits")
                self.assertIs(doc["target_teardown_scenario"], False)
                self.assertEqual(doc["creditability"], "diagnostic_only_not_creditable")
                for phone in doc["phones"]:
                    width = phone["origin_interval"]["width_ms"]
                    self.assertLessEqual(width, 250, f"{scenario} {phone['serial_hint']}: anchor too wide")
                    self.assertGreater(width, 0)
                self.assertEqual(doc["creditability"], "diagnostic_only_not_creditable")

    def build_retained_analysis(self, scenario_dir, scenario):
        """Slice ONE retained burst per phone from the four-burst retained telemetry (per-scenario
        run order), for the analyzer to work against — the retained RAW files stay byte-preserved."""
        raw = [(name, json.loads((self.RETAINED / "phones" / name).read_text()))
               for name in ("transport-2be02e41.json", "transport-864a3db0.json")]
        slice_dir = Path(tempfile.mkdtemp(prefix=f"retained-{scenario}-"))
        self.addCleanup(_rmtree_quiet, slice_dir)
        stops = []
        sliced = []
        # take the k-th connect/disc pair and everything between them (retained run order)
        order = ["shutdown", "port-disconnect", "tab-close", "failure-isolation"]
        k = order.index(scenario)
        for name, document in raw:
            peer = document["peers"][0]
            events = peer["events"]
            connects = [i for i, e in enumerate(events) if e.get("kind") == "connected"]
            discs = [i for i, e in enumerate(events) if e.get("kind") == "disconnected"]
            lo_i, hi_i = connects[k], discs[k]
            sub = dict(document)
            sub = {**document, "peers": [
                {**peer, "truncated": peer.get("truncated", False),
                 "connects": 1, "disconnects": 1,
                 "events": events[lo_i:hi_i + 1]}]}
            path = slice_dir / name
            path.write_text(json.dumps(sub), encoding="utf-8")
            sliced.append((path, sub))
            stops.append({"serial": name.replace("transport-", "").replace(".json", ""),
                          "stop_min_phone_ms": document["finished_at_epoch_ms"] + 10**9,
                          "stop_max_phone_ms": document["finished_at_epoch_ms"] + 10**9 + 1,
                          "method": "retained-replay diagnostic stop is synthetic (real stop is far later)"})
        return mrb.analyse(scenario_dir, scenario, sliced, stops, 30000, None)


def _rmtree_quiet(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
