"""Targeted tests for scripts/mac_release_attribution.py, A2 (source-proven causal inequalities only).

Hand-computed synthetic cases built from REALISTIC complete phone telemetry (one 20-byte proof notify,
the 9-fragment 4096-byte echo 8×512 + 36, every notify_api bound to a code-0 callback, stable
elapsed_realtime_ms): unique / zero / ambiguous (a hull PASS that must NOT be credited) / swapped order /
global-vs-own / quantization / the invalid 16-byte lower bound that would falsely exclude the true mapping
/ premise negatives (missing later fragment, incomplete echo, wall-clock step) / structural / preconditions /
analyzer pin. Plus the three FROZEN mac-retake-2026-10-29 runs against the independent review's safe table
and the retained ABORTED mac-resume-2026-10-28 replay (never creditable). No broad fuzzing.
Usage:  python3 scripts/test_mac_release_attribution.py
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

# handle A: 16B 1000→1050, 4096 1200→1700; handle B: 16B 1060→1110, 4096 1750→2250.
# global last exchange 2250, exit 2330.
EXIT = 2330


def pair(t_in, t_msg, handle, size):
    return (f"[{t_in:5d}ms pid=7] in  len=115 type=send request_id=x api_version=1 "
            f"connection_handle={handle} record_base64.len={size}\n"
            f"[{t_msg:5d}ms pid=7] out len=137 type=message api_version=1 "
            f"connection_handle={handle} record_base64.len={size}\n")


def trace(extra=""):
    return (pair(1000, 1050, "aaaa", 24) + pair(1060, 1110, "bbbb", 24) + pair(1200, 1700, "aaaa", 5464)
            + pair(1750, 2250, "bbbb", 5464) + extra + f"[{EXIT:5d}ms pid=7] exit code=0 stdout_drained=true\n")


def transport(origin, t16, t_first, t_dis, fragments=None, clock_step_at=None):
    """Complete realistic burst on one phone clock; elapsed_realtime_ms = epoch − constant (optionally a
    wall step of 5 ms after the `clock_step_at`-th event)."""
    fragments = [512] * 8 + [36] if fragments is None else fragments
    rows = [("connected", origin + 500, 0), ("mtu", origin + 520, 0), ("subscribed", origin + 600, 0),
            ("notify_api", origin + t16, 20), ("notify_callback", origin + t16 + 6, 0)]
    for k, size in enumerate(fragments):
        at = origin + t_first + 8 * k
        rows += [("notify_api", at, size), ("notify_callback", at + 4, 0)]
    rows.append(("disconnected", origin + t_dis, 0))
    events, bound = [], 0
    for seq, (kind, at, size) in enumerate(rows, start=1):
        if kind == "notify_api":
            bound = seq
        step = 5 if clock_step_at is not None and seq > clock_step_at else 0
        events.append({"seq": seq, "kind": kind, "epoch_ms": at, "elapsed_realtime_ms": at - origin + 1000 - step,
                       "code": 0 if kind.startswith("notify") else None, "bytes": size,
                       "bound_op": bound if kind.startswith("notify") else None})
    n = len(fragments) + 1
    peer = {"peer_sha256_prefix": "abcdef0123456789", "truncated": False, "events": events,
            "notify_calls": n, "api_success": n, "callback_success": n, "api_nonzero": 0, "callback_nonzero": 0,
            "pending_unresolved": 0, "unbound_callbacks": 0, "connects": 1, "disconnects": 1}
    return {"kind": "otp23-transport-evidence", "run_id": "R", "final": True, "final_after_service_stopped": True,
            "clean": True, "truncated": False, "peers_dropped": 0, "peers": [peer]}


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
        self.tmp = tempfile.mkdtemp(prefix="attr2-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def case(self, x=None, y=None, extra="", status="PASS"):
        # default truth: X on A (e16 OX+1025, e_first OX+1450, DIS OX+2290); Y on B (OY+1085, OY+2000, OY+2300)
        return Case(self.tmp, trace(extra), x or transport(OX, 1025, 1450, 2290),
                    y or transport(OY, 1085, 2000, 2300), status=status)

    def test_unique_mapping_from_safe_bounds_upgrades_hull_fail(self):
        doc = self.case().run()
        # hull (diagnostic) for Y: [OY−25, OY+85] → DIS_lo 2215 < 2250 → hull FAIL
        self.assertEqual(doc["hull_result_diagnostic_only"]["verdict"], "FAIL")
        self.assertEqual(doc["attribution_status"], "unique_mapping")
        self.assertEqual([m["feasible"] for m in doc["mappings"]], [True, False])
        x, y = doc["candidates"]["xxxx0001"], doc["candidates"]["yyyy0002"]
        # O(X,A): lo = e_first − m4096 − 1 = OX−251; hi = min(e16 − i16 + 1, e_first − i4096 + 1) = OX+26
        self.assertEqual((x["aaaa"]["lo"], x["aaaa"]["hi"], x["aaaa"]["width_ms"]), (OX - 251, OX + 26, 277))
        self.assertEqual(x["aaaa"]["bracket16_lower_not_causal_recorded_only"], OX + 1025 - 1050 - 1)
        # O(X,B) is feasible under A2 ([OX−801, OX−299]); uniqueness rests on O(Y,A) = [OY+299, OY+86] (empty)
        self.assertTrue(x["bbbb"]["feasible"])
        self.assertEqual((y["aaaa"]["lo"], y["aaaa"]["hi"]), (OY + 299, OY + 86))
        self.assertFalse(y["aaaa"]["feasible"])
        self.assertEqual((y["bbbb"]["lo"], y["bbbb"]["hi"]), (OY - 251, OY + 26))
        self.assertEqual(doc["assignments"]["yyyy0002"]["dis_trace_interval"], [2274, 2551])
        self.assertEqual(doc["assignments"]["yyyy0002"]["margin_vs_global_last_ms"], 24)
        self.assertEqual(doc["assignments"]["xxxx0001"]["dis_trace_interval"], [2264, 2541])
        self.assertTrue(all(all(a["checks"].values()) and len(a["checks"]) == 4 for a in doc["assignments"].values()))
        self.assertEqual(doc["verdict"], "PASS")
        self.assertTrue(doc["m08_creditable"])
        self.assertFalse(doc["precision"]["all_within_instrument_target"], "277 ms > 250 ms target: not claimed")

    def test_swapped_transport_order_gives_the_same_mapping(self):
        a, b = self.case().run(), self.case().run(reverse=True)
        self.assertEqual(a["mappings"], b["mappings"])
        self.assertEqual({s: v["handle"] for s, v in a["assignments"].items()},
                         {s: v["handle"] for s, v in b["assignments"].items()})
        self.assertEqual(a["verdict"], b["verdict"])

    def test_hull_pass_with_ambiguous_safe_mapping_is_never_credited(self):
        # Y on B with e16 OY+1100, e_first OY+1760: O(Y,A) = [OY+59, OY+101] and O(Y,B) = [OY−491, OY+11] are
        # both feasible, O(X,B) too ⇒ 2 feasible mappings. The non-causal 16-byte lower bound alone would have
        # excluded O(X,B) (A1 unique). The frozen hull PASSes (DIS_lo 2265 / 2260 ≥ 2250) — still NOT credited.
        doc = self.case(y=transport(OY, 1100, 1760, 2360)).run()
        self.assertEqual(doc["hull_result_diagnostic_only"]["verdict"], "PASS")
        self.assertEqual(doc["attribution_status"], "fail_closed_ambiguous_mapping")
        self.assertEqual([m["feasible"] for m in doc["mappings"]], [True, True])
        self.assertEqual(doc["assignments"], {})
        self.assertEqual(doc["verdict"], "FAIL")
        self.assertFalse(doc["m08_creditable"])

    def test_zero_feasible_mappings_fail(self):
        # Y: e16 OY+1040, e_first OY+2260 — O(Y,A) = [OY+559, OY+41], O(Y,B) = [OY+9, OY−19]: no handle fits,
        # while the hull stays non-empty ([OY+10, OY+40]) so this is the zero-feasible path, not structural
        doc = self.case(y=transport(OY, 1040, 2260, 2300)).run()
        self.assertEqual(doc["hull_result_diagnostic_only"]["problems"], [])
        self.assertEqual(doc["attribution_status"], "fail_closed_zero_feasible_mappings")
        self.assertEqual([m["feasible"] for m in doc["mappings"]], [False, False])
        self.assertEqual(doc["verdict"], "FAIL")
        self.assertFalse(doc["m08_creditable"])

    def test_invalid_16b_lower_bound_would_falsely_exclude_the_true_mapping(self):
        # truth X on A, but X's 16-byte notify_api is stamped 25 ms AFTER the host logged that echo (post-return
        # stamp delay) and its first 4096 fragment soon after the send: e16 = OX+1075 > m16 = OX+1050,
        # e_first = OX+1210. A1's bracket16 lower (OX+24) exceeds the 4096 upper (OX+11) — the true pair would be
        # excluded (A1: zero feasible). A2: O(X,A) = [OX−491, OX+11] feasible; unique with Y on B.
        doc = self.case(x=transport(OX, 1075, 1210, 2290)).run()
        x = doc["candidates"]["xxxx0001"]["aaaa"]
        self.assertEqual(x["bracket16_lower_not_causal_recorded_only"], OX + 24)
        self.assertEqual((x["lo"], x["hi"]), (OX - 491, OX + 11))
        self.assertGreater(x["bracket16_lower_not_causal_recorded_only"], x["hi"])
        self.assertEqual(doc["attribution_status"], "unique_mapping")
        self.assertEqual(doc["assignments"]["xxxx0001"]["handle"], "aaaa")
        self.assertEqual(doc["verdict"], "PASS")

    def test_global_last_exchange_clause_is_not_relaxed_to_own_link(self):
        # X released at trace 2100: after its OWN link's last message (1700), before the GLOBAL one (2250)
        doc = self.case(x=transport(OX, 1025, 1450, 2100)).run()
        ax = doc["assignments"]["xxxx0001"]
        self.assertEqual(doc["attribution_status"], "unique_mapping")
        self.assertEqual(ax["dis_trace_interval"], [2074, 2351])
        self.assertTrue(ax["release_after_own_link_last_recorded_only"])
        self.assertFalse(ax["checks"]["release_after_last_exchange"])
        self.assertEqual(doc["verdict"], "FAIL")
        self.assertFalse(doc["m08_creditable"])

    def test_quantization_pad_is_applied_before_feasibility(self):
        # X: e16 OX+1025, e_first OX+1726: unpadded lo OX+26 > hi OX+25 (1 ms miss); padded [OX+25, OX+26]
        doc = self.case(x=transport(OX, 1025, 1726, 2290)).run()
        x = doc["candidates"]["xxxx0001"]["aaaa"]
        self.assertTrue(x["feasible"])
        self.assertEqual((x["lo"], x["hi"], x["width_ms"]), (OX + 25, OX + 26, 1))
        self.assertEqual(doc["attribution_status"], "unique_mapping")
        self.assertEqual(doc["assignments"]["xxxx0001"]["handle"], "aaaa")

    def test_premise_single_fragment_is_not_a_safe_lower_anchor(self):
        doc = self.case(x=transport(OX, 1025, 1450, 2290, fragments=[512])).run()
        self.assertEqual(doc["attribution_status"], "fail_closed_causal_premises")
        self.assertFalse(doc["causal_premises"]["xxxx0001"]["met"])
        self.assertEqual(doc["causal_premises"]["xxxx0001"]["later_fragments_after_first"], 0)
        self.assertEqual(doc["verdict"], "FAIL")
        self.assertFalse(doc["m08_creditable"])

    def test_premise_incomplete_echo_fails_closed(self):
        doc = self.case(x=transport(OX, 1025, 1450, 2290, fragments=[512] * 8)).run()
        self.assertEqual(doc["attribution_status"], "fail_closed_causal_premises")
        self.assertEqual(doc["verdict"], "FAIL")

    def test_premise_wall_clock_step_fails_closed(self):
        doc = self.case(y=transport(OY, 1085, 2000, 2300, clock_step_at=8)).run()
        self.assertEqual(doc["attribution_status"], "fail_closed_causal_premises")
        self.assertEqual(doc["causal_premises"]["yyyy0002"]["epoch_minus_elapsed_spread_ms"], 5)
        self.assertEqual(doc["verdict"], "FAIL")
        self.assertFalse(doc["m08_creditable"])

    def test_real_premises_hold_on_the_valid_fixture(self):
        doc = self.case().run()
        for serial in ("xxxx0001", "yyyy0002"):
            premise = doc["causal_premises"][serial]
            self.assertTrue(premise["met"], premise["problems"])
            self.assertEqual(premise["notify_bytes"], [20] + [512] * 8 + [36])
            self.assertEqual(premise["later_fragments_after_first"], 8)
            self.assertEqual(premise["epoch_minus_elapsed_spread_ms"], 0)

    def test_structural_hull_problem_fails_closed(self):
        y = transport(OY, 1085, 2000, 2300)
        y["clean"] = False
        doc = self.case(y=y).run()
        self.assertTrue(doc["hull_result_diagnostic_only"]["problems"])
        self.assertEqual(doc["attribution_status"], "fail_closed_structural_hull_problems")
        self.assertEqual(doc["verdict"], "FAIL")
        self.assertFalse(doc["m08_creditable"])

    def test_preconditions_failure_with_hull_pass_is_not_credited(self):
        # third handle; Y released at OY+2360 so the hull (diagnostic) PASSes — A2 still fails closed
        doc = self.case(y=transport(OY, 1085, 2000, 2360), extra=pair(1120, 1150, "cccc", 24)).run()
        self.assertEqual(doc["hull_result_diagnostic_only"]["verdict"], "PASS")
        self.assertEqual(doc["attribution_status"], "fail_closed_preconditions")
        self.assertEqual(doc["verdict"], "FAIL")
        self.assertFalse(doc["m08_creditable"])

    def test_failed_scenario_is_never_creditable(self):
        doc = self.case(status="FAIL").run()
        self.assertEqual(doc["verdict"], "PASS")
        self.assertFalse(doc["target_teardown_scenario"])
        self.assertFalse(doc["m08_creditable"])

    def test_wrong_imported_analyzer_bytes_fail_closed(self):
        with mock.patch.object(mra, "REVIEWED_ANALYZER_SHA256", "0" * 64):
            doc = self.case().run()
        self.assertEqual(doc["verdict"], "FAIL")
        self.assertFalse(doc["m08_creditable"])


class FrozenReplay(unittest.TestCase):
    """The three frozen captures against the independent review's SAFE table
    (/tmp/opencode/otp23-mac-final-data-review-opus.md §2): same mappings, same DIS_lo / global margins,
    wider widths and DIS_hi."""
    FROZEN = HERE.parent / "evidence" / "mac-retake-2026-10-29"
    EXPECTED = {
        "shutdown": {"2be02e41": ("e7b2832a8828839e", 256, [4143, 4399], 84),
                     "864a3db0": ("053544add3f3ad4d", 258, [4152, 4410], 93),
                     "widths": {"2be02e41": {"053544add3f3ad4d": -230, "e7b2832a8828839e": 256},
                                "864a3db0": {"053544add3f3ad4d": 258, "e7b2832a8828839e": 504}},
                     "feasible": [False, True], "hull": "PASS"},
        "port-disconnect": {"2be02e41": ("56433e089280829e", 273, [6036, 6309], 85),
                            "864a3db0": ("c710e81543b792ef", 282, [6005, 6287], 54),
                            "widths": {"2be02e41": {"56433e089280829e": 273, "c710e81543b792ef": 533},
                                       "864a3db0": {"56433e089280829e": -224, "c710e81543b792ef": 282}},
                            "feasible": [True, False], "hull": "FAIL"},
        "tab-close": {"2be02e41": ("71857b85775992a2", 270, [5167, 5437], 54),
                      "864a3db0": ("093954e54c2fa8d8", 235, [5173, 5408], 60),
                      "widths": {"2be02e41": {"093954e54c2fa8d8": -242, "71857b85775992a2": 270},
                                 "864a3db0": {"093954e54c2fa8d8": 235, "71857b85775992a2": 550}},
                      "feasible": [False, True], "hull": "FAIL"},
    }

    def test_frozen_three_reproduce_the_safe_table(self):
        if not (self.FROZEN / "SHA256SUMS").is_file():
            self.skipTest("frozen mac-retake-2026-10-29 evidence not present")
        for scenario, expected in self.EXPECTED.items():
            with self.subTest(scenario):
                base = self.FROZEN / f"{scenario}-1"
                transports = [(base / "phones" / f"transport-{s}.json",
                               json.loads((base / "phones" / f"transport-{s}.json").read_text()))
                              for s in ("2be02e41", "864a3db0")]
                doc = mra.attribute(base / "mac", scenario, transports, json.loads((base / "stops.json").read_text()))
                self.assertEqual(doc["hull_result_diagnostic_only"]["verdict"], expected["hull"])
                for serial in ("2be02e41", "864a3db0"):
                    premise = doc["causal_premises"][serial]
                    self.assertTrue(premise["met"], premise["problems"])
                    self.assertEqual(premise["notify_bytes"], [20] + [512] * 8 + [36])
                    self.assertLessEqual(premise["epoch_minus_elapsed_spread_ms"], 1)
                self.assertEqual(doc["attribution_status"], "unique_mapping")
                self.assertEqual([m["feasible"] for m in doc["mappings"]], expected["feasible"])
                self.assertEqual({s: {h: c["width_ms"] for h, c in v.items()} for s, v in doc["candidates"].items()},
                                 expected["widths"])
                for serial in ("2be02e41", "864a3db0"):
                    handle, width, dis, margin = expected[serial]
                    got = doc["assignments"][serial]
                    self.assertEqual(got["handle"], handle)
                    self.assertEqual(got["origin_interval"]["width_ms"], width)
                    self.assertEqual(got["dis_trace_interval"], dis)
                    self.assertEqual(got["margin_vs_global_last_ms"], margin)
                    self.assertTrue(all(got["checks"].values()) and len(got["checks"]) == 4)
                self.assertEqual(doc["verdict"], "PASS")
                self.assertTrue(doc["m08_creditable"])
                self.assertFalse(doc["precision"]["all_within_instrument_target"])

    def test_retained_aborted_runs_stay_not_creditable(self):
        retained = HERE.parent / "evidence" / "mac-resume-2026-10-28"
        if not (retained / "lifecycle-1" / "shutdown.json").is_file():
            self.skipTest("retained mac-resume-2026-10-28 evidence not present")
        raw = [(n, json.loads((retained / "phones" / n).read_text()))
               for n in ("transport-2be02e41.json", "transport-864a3db0.json")]
        for k, scenario in enumerate(["shutdown", "port-disconnect", "tab-close", "failure-isolation"]):
            with self.subTest(scenario):
                tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"attr2-retained-{scenario}-"))
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
