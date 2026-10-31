"""Targeted tests for scripts/mac_release_attribution.py (Option A, 2026-10-29 physical review §3).

Hand-computed synthetic cases (unique / ambiguous / zero-feasible / swapped order / global-vs-own /
quantization / structural / preconditions / analyzer pin), a replay of the three FROZEN
mac-retake-2026-10-29 runs against the reviewer's independently computed mapping table (with the
±1 ms quantization pad), and the retained ABORTED mac-resume-2026-10-28 replay (never creditable).
No broad fuzzing. Usage:  python3 scripts/test_mac_release_attribution.py
"""

import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mac_release_attribution as mra
import mac_release_bound as mrb

# handle A: 16B 1000→1050, 4096 1200→1700; handle B: 16B 1060→1110, 4096 1750→2250; exit 2330.
EXIT = 2330


def pair(t_in, t_msg, handle, size):
    return (f"[{t_in:5d}ms pid=7] in  len=115 type=send request_id=x api_version=1 "
            f"connection_handle={handle} record_base64.len={size}\n"
            f"[{t_msg:5d}ms pid=7] out len=137 type=message api_version=1 "
            f"connection_handle={handle} record_base64.len={size}\n")


def trace(a16=(1000, 1050), b16=(1060, 1110), a4096=(1200, 1700), b4096=(1750, 2250), extra=""):
    return (pair(*a16, "aaaa", 24) + pair(*b16, "bbbb", 24) + pair(*a4096, "aaaa", 5464)
            + pair(*b4096, "bbbb", 5464) + extra + f"[{EXIT:5d}ms pid=7] exit code=0 stdout_drained=true\n")


def transport(origin, t16, t4096, t_dis, clean=True):
    events = [{"seq": 1, "kind": "connected", "epoch_ms": origin + 500},
              {"seq": 3, "kind": "notify_api", "epoch_ms": origin + t16, "bytes": 20, "code": 0},
              {"seq": 9, "kind": "notify_api", "epoch_ms": origin + t4096, "bytes": 512, "code": 0}]
    events += [{"seq": 10 + j, "kind": "notify_api", "epoch_ms": origin + t4096 + 4 * (j + 1), "bytes": 512,
                "code": 0} for j in range(8)]
    events.append({"seq": 40, "kind": "disconnected", "epoch_ms": origin + t_dis})
    return {"kind": "otp23-transport-evidence", "run_id": "R", "final": True, "final_after_service_stopped": True,
            "clean": clean, "truncated": False, "peers_dropped": 0,
            "peers": [{"peer_sha256_prefix": "abcdef0123456789", "truncated": False, "events": events}]}


OX, OY = 1793200000000, 1793300000000   # two phone clocks; never compared with each other


class Case:
    def __init__(self, tmp, trace_text, x, y, status="PASS"):
        self.dir = pathlib.Path(tmp)
        (self.dir / "tab-close.host-trace.log").write_text(trace_text, encoding="utf-8")
        (self.dir / "tab-close.json").write_text(json.dumps(
            {"status": status, "detail": {"teardown": {"trace_exit": {"at_ms": EXIT, "code": 0}}}}), encoding="utf-8")
        self.transports = []
        for serial, doc in (("xxxx0001", x), ("yyyy0002", y)):
            path = self.dir / f"transport-{serial}.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            self.transports.append((path, doc))
        self.stops = [{"serial": "xxxx0001", "stop_min_phone_ms": OX + 100000, "stop_max_phone_ms": OX + 100040},
                      {"serial": "yyyy0002", "stop_min_phone_ms": OY + 100000, "stop_max_phone_ms": OY + 100040}]

    def run(self, reverse=False):
        transports = list(reversed(self.transports)) if reverse else self.transports
        return mra.attribute(self.dir, "tab-close", transports, self.stops)


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="attr-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def unique_case(self, y_dis=2300, **kw):
        # X on A: e16 = OX+1025, e4096 = OX+1450; Y on B: e16 = OY+1085, e4096 = OY+2000.
        return Case(self.tmp, trace(**kw), transport(OX, 1025, 1450, 2290), transport(OY, 1085, 2000, y_dis))

    def test_unique_mapping_narrows_and_upgrades_hull_fail(self):
        doc = self.unique_case().run()
        # hull for Y: 16B [e16−1110, e16−1000] ∩ 4096 [e4096−2250, e4096−1200] = [OY−25, OY+85] → DIS_lo 2215 < 2250
        self.assertEqual(doc["hull_result"]["verdict"], "FAIL")
        self.assertEqual(doc["attribution_status"], "unique_mapping")
        self.assertEqual([m["feasible"] for m in doc["mappings"]], [True, False])
        self.assertEqual(doc["mappings"][0]["mapping"], {"xxxx0001": "aaaa", "yyyy0002": "bbbb"})
        x, y = doc["candidates"]["xxxx0001"], doc["candidates"]["yyyy0002"]
        # padded brackets: [e − m − 1, e − i + 1]
        self.assertEqual(x["aaaa"]["bracket16"], [OX + 1025 - 1050 - 1, OX + 1025 - 1000 + 1])
        self.assertEqual(x["aaaa"]["bracket4096"], [OX + 1450 - 1700 - 1, OX + 1450 - 1200 + 1])
        self.assertEqual((x["aaaa"]["lo"], x["aaaa"]["hi"], x["aaaa"]["width_ms"]), (OX - 26, OX + 26, 52))
        self.assertFalse(x["bbbb"]["feasible"])   # 16B [OX−86, OX−34] ∩ 4096 [OX−801, OX−299]
        self.assertEqual((y["bbbb"]["lo"], y["bbbb"]["hi"]), (OY - 26, OY + 26))
        self.assertFalse(y["aaaa"]["feasible"])
        ay = doc["assignments"]["yyyy0002"]
        self.assertEqual(ay["dis_trace_interval"], [2274, 2326])
        self.assertEqual(ay["margin_vs_global_last_ms"], 24)
        self.assertEqual(doc["assignments"]["xxxx0001"]["dis_trace_interval"], [2264, 2316])
        self.assertEqual(doc["assignments"]["xxxx0001"]["own_last_exchange_message_at_ms"], 1700)
        self.assertTrue(all(ay["checks"].values()) and len(ay["checks"]) == 4)
        self.assertEqual(doc["verdict"], "PASS")
        self.assertTrue(doc["upgraded_from_hull_fail"])
        self.assertTrue(doc["m08_creditable"])
        self.assertEqual(doc["imported_analyzer_sha256"], mra.REVIEWED_ANALYZER_SHA256)

    def test_swapped_transport_order_gives_the_same_mapping(self):
        a, b = self.unique_case().run(), self.unique_case().run(reverse=True)
        self.assertEqual(a["mappings"], b["mappings"])
        self.assertEqual({s: v["handle"] for s, v in a["assignments"].items()},
                         {s: v["handle"] for s, v in b["assignments"].items()})
        self.assertEqual(a["verdict"], b["verdict"])

    def test_ambiguous_both_feasible_keeps_hull_verdict(self):
        # B nearly identical to A → both bijections feasible; never choose
        case = Case(self.tmp, trace(b16=(1002, 1052), b4096=(1202, 2250)),
                    transport(OX, 1025, 1450, 2290), transport(OY, 1027, 1452, 2262))
        doc = case.run()
        self.assertEqual(doc["attribution_status"], "ambiguous_multiple_feasible_mappings")
        self.assertEqual([m["feasible"] for m in doc["mappings"]], [True, True])
        self.assertEqual(doc["assignments"], {})
        self.assertEqual(doc["verdict"], doc["hull_result"]["verdict"])
        self.assertEqual(doc["verdict"], "FAIL")
        self.assertFalse(doc["upgraded_from_hull_fail"])
        self.assertFalse(doc["m08_creditable"])

    def test_zero_feasible_mappings_fail(self):
        # both phones are consistent ONLY with handle A → no bijection feasible (premises violated)
        case = Case(self.tmp, trace(), transport(OX, 1025, 1450, 2290), transport(OY, 1025, 1450, 2290))
        doc = case.run()
        self.assertEqual(doc["hull_result"]["problems"], [])
        self.assertEqual(doc["attribution_status"], "zero_feasible_mappings")
        self.assertEqual([m["feasible"] for m in doc["mappings"]], [False, False])
        self.assertEqual(doc["verdict"], "FAIL")
        self.assertFalse(doc["m08_creditable"])

    def test_global_last_exchange_clause_is_not_relaxed_to_own_link(self):
        # X released at trace 2100: after its OWN link's last message (1700) but before the GLOBAL (2250)
        case = Case(self.tmp, trace(), transport(OX, 1025, 1450, 2100), transport(OY, 1085, 2000, 2300))
        doc = case.run()
        ax = doc["assignments"]["xxxx0001"]
        self.assertEqual(doc["attribution_status"], "unique_mapping")
        self.assertEqual(ax["dis_trace_interval"], [2074, 2126])
        self.assertTrue(ax["release_after_own_link_last_recorded_only"])
        self.assertFalse(ax["checks"]["release_after_last_exchange"])
        self.assertEqual(doc["verdict"], "FAIL")
        self.assertFalse(doc["m08_creditable"])

    def test_quantization_pad_is_applied_before_feasibility(self):
        # X's unpadded 16B [OX−25, OX+25] and 4096 [OX+26, OX+526] miss by 1 ms; padded they meet
        case = Case(self.tmp, trace(), transport(OX, 1025, 1726, 2290), transport(OY, 1085, 2000, 2300))
        doc = case.run()
        x = doc["candidates"]["xxxx0001"]["aaaa"]
        self.assertTrue(x["feasible"])
        self.assertEqual((x["lo"], x["hi"], x["width_ms"]), (OX + 25, OX + 26, 1))
        self.assertEqual(doc["attribution_status"], "unique_mapping")
        self.assertEqual(doc["assignments"]["xxxx0001"]["handle"], "aaaa")

    def test_structural_hull_problem_is_never_bypassed(self):
        case = Case(self.tmp, trace(), transport(OX, 1025, 1450, 2290),
                    transport(OY, 1085, 2000, 2300, clean=False))
        doc = case.run()
        self.assertTrue(doc["hull_result"]["problems"])
        self.assertEqual(doc["attribution_status"], "not_applicable_structural_hull_problems")
        self.assertEqual(doc["verdict"], "FAIL")
        self.assertFalse(doc["m08_creditable"])

    def test_preconditions_third_handle_keeps_hull_verdict(self):
        case = Case(self.tmp, trace(extra=pair(1120, 1150, "cccc", 24)),
                    transport(OX, 1025, 1450, 2290), transport(OY, 1085, 2000, 2300))
        doc = case.run()
        self.assertEqual(doc["attribution_status"], "not_applicable_preconditions")
        self.assertFalse(doc["preconditions"]["met"])
        self.assertEqual(doc["verdict"], doc["hull_result"]["verdict"])
        self.assertFalse(doc["upgraded_from_hull_fail"])

    def test_failed_scenario_is_never_creditable(self):
        case = Case(self.tmp, trace(), transport(OX, 1025, 1450, 2290), transport(OY, 1085, 2000, 2300),
                    status="FAIL")
        doc = case.run()
        self.assertEqual(doc["verdict"], "PASS")
        self.assertFalse(doc["target_teardown_scenario"])
        self.assertFalse(doc["m08_creditable"])

    def test_wrong_imported_analyzer_bytes_fail_closed(self):
        with mock.patch.object(mra, "REVIEWED_ANALYZER_SHA256", "0" * 64):
            doc = self.unique_case().run()
        self.assertEqual(doc["verdict"], "FAIL")
        self.assertFalse(doc["m08_creditable"])


class FrozenReplay(unittest.TestCase):
    """The three frozen captures against the reviewer's independently computed table (§2), with the
    ±1 ms pad: widths +2, DIS intervals ±1, margins −1 versus the unpadded review numbers."""
    FROZEN = HERE.parent / "evidence" / "mac-retake-2026-10-29"
    EXPECTED = {
        "shutdown": {"2be02e41": ("e7b2832a8828839e", 63, [4143, 4206], 84, 4059),
                     "864a3db0": ("053544add3f3ad4d", 53, [4152, 4205], 93, 3509),
                     "hull": "PASS", "infeasible_miss": {"2be02e41": -230, "864a3db0": -177}},
        "port-disconnect": {"2be02e41": ("56433e089280829e", 54, [6036, 6090], 85, 5373),
                            "864a3db0": ("c710e81543b792ef", 49, [6005, 6054], 54, 5951),
                            "hull": "FAIL", "infeasible_miss": {"2be02e41": -197, "864a3db0": -224}},
        "tab-close": {"2be02e41": ("71857b85775992a2", 61, [5167, 5228], 54, 5113),
                      "864a3db0": ("093954e54c2fa8d8", 62, [5173, 5235], 60, 4518),
                      "hull": "FAIL", "infeasible_miss": {"2be02e41": -242, "864a3db0": -136}},
    }

    def test_frozen_three_reproduce_the_reviewed_mapping_table(self):
        if not (self.FROZEN / "SHA256SUMS").is_file():
            self.skipTest("frozen mac-retake-2026-10-29 evidence not present")
        for scenario, expected in self.EXPECTED.items():
            with self.subTest(scenario):
                base = self.FROZEN / f"{scenario}-1"
                transports = [(base / "phones" / f"transport-{s}.json",
                               json.loads((base / "phones" / f"transport-{s}.json").read_text()))
                              for s in ("2be02e41", "864a3db0")]
                stops = json.loads((base / "stops.json").read_text())
                doc = mra.attribute(base / "mac", scenario, transports, stops)
                self.assertEqual(doc["hull_result"]["verdict"], expected["hull"])
                self.assertEqual(doc["attribution_status"], "unique_mapping")
                self.assertEqual(sum(m["feasible"] for m in doc["mappings"]), 1)
                for serial in ("2be02e41", "864a3db0"):
                    handle, width, dis, margin, own = expected[serial]
                    got = doc["assignments"][serial]
                    self.assertEqual(got["handle"], handle)
                    self.assertEqual(got["origin_interval"]["width_ms"], width)
                    self.assertEqual(got["dis_trace_interval"], dis)
                    self.assertEqual(got["margin_vs_global_last_ms"], margin)
                    self.assertEqual(got["own_last_exchange_message_at_ms"], own)
                    self.assertTrue(all(got["checks"].values()) and len(got["checks"]) == 4)
                    self.assertTrue(got["stop_guard_with_hull_hi_recorded"]["stop_after_bound_plus_uncertainty"])
                    other = [h for h in doc["candidates"][serial] if h != handle][0]
                    self.assertEqual(doc["candidates"][serial][other]["width_ms"],
                                     expected["infeasible_miss"][serial])
                self.assertEqual(doc["verdict"], "PASS")
                self.assertTrue(doc["m08_creditable"])
                self.assertEqual(doc["upgraded_from_hull_fail"], expected["hull"] == "FAIL")

    def test_retained_aborted_runs_stay_not_creditable(self):
        retained = HERE.parent / "evidence" / "mac-resume-2026-10-28"
        if not (retained / "lifecycle-1" / "shutdown.json").is_file():
            self.skipTest("retained mac-resume-2026-10-28 evidence not present")
        raw = [(n, json.loads((retained / "phones" / n).read_text()))
               for n in ("transport-2be02e41.json", "transport-864a3db0.json")]
        order = ["shutdown", "port-disconnect", "tab-close", "failure-isolation"]
        for k, scenario in enumerate(order):
            with self.subTest(scenario):
                tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"attr-retained-{scenario}-"))
                self.addCleanup(shutil.rmtree, tmp, True)
                sliced, stops = [], []
                for name, document in raw:
                    peer = document["peers"][0]
                    events = peer["events"]
                    lo = [i for i, e in enumerate(events) if e.get("kind") == "connected"][k]
                    hi = [i for i, e in enumerate(events) if e.get("kind") == "disconnected"][k]
                    sub = {**document, "peers": [{**peer, "truncated": peer.get("truncated", False),
                                                  "events": events[lo:hi + 1]}]}
                    path = tmp / name
                    path.write_text(json.dumps(sub), encoding="utf-8")
                    sliced.append((path, sub))
                    stops.append({"serial": name[len("transport-"):-len(".json")],
                                  "stop_min_phone_ms": document["finished_at_epoch_ms"] + 10 ** 9,
                                  "stop_max_phone_ms": document["finished_at_epoch_ms"] + 10 ** 9 + 1})
                doc = mra.attribute(retained / "lifecycle-1", scenario, sliced, stops)
                self.assertFalse(doc["target_teardown_scenario"])
                self.assertFalse(doc["m08_creditable"])


if __name__ == "__main__":
    unittest.main()
