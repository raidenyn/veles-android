"""Tests for the deterministic M6 phone-clock adjudication and its committed evidence output."""

import copy
import hashlib
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import m6_phone_clock_adjudication as m6  # noqa: E402

M6_DIR = HERE.parent / "evidence/post-grant-2026-10-26/m6"
PHONE = M6_DIR / "otp23-m6-phone-transport.json"


def synthetic(offset_b, disconnect_a):
    """Two peers, 2 records each, fragments 5 ms apart; peer B shifted by offset_b ms."""
    def peer(name, base):
        events, seq, op = [], 0, 0
        for record in range(2):
            for index, size in enumerate(m6.RECORD_FRAGMENT_BYTES):
                t = base + record * 100 + index * 5
                op += 1
                events.append({"seq": seq + 1, "kind": "notify_api", "elapsed_realtime_ms": t, "bytes": size, "bound_op": op})
                events.append({"seq": seq + 2, "kind": "notify_callback", "elapsed_realtime_ms": t + 3, "bytes": 0, "bound_op": op})
                seq += 2
        return {"peer_sha256_prefix": name, "events": events}
    a, b = peer("aa", 0), peer("bb", offset_b)
    if disconnect_a is not None:
        a["events"].append({"seq": 999, "kind": "disconnected", "elapsed_realtime_ms": disconnect_a, "bytes": 0, "bound_op": None})
    b["events"].append({"seq": 998, "kind": "disconnected", "elapsed_realtime_ms": 10_000, "bytes": 0, "bound_op": None})
    return {"run_id": "t", "peers": [a, b]}


class Algorithm(unittest.TestCase):
    def test_committed_run_reproduces_the_manual_adjudication_numbers(self):
        result = m6.adjudicate(json.loads(PHONE.read_text()))
        self.assertEqual(result["records_per_peer"], {"576d2120c83dc839": 100, "c3a3dc8495dd5bc2": 100})
        self.assertEqual((result["overlapping_record_pairs"], result["summed_overlap_ms"]), (16, 1505))
        self.assertEqual((result["survivor_records_after_disconnect"],
                          result["survivor_notifications_after_disconnect"]), (71, 639))
        self.assertEqual(result["earliest_disconnect_peer"], "576d2120c83dc839")

    def test_committed_output_matches_a_fresh_run_and_binds_the_raw_hash(self):
        out = json.loads((M6_DIR / "otp23-m6-phone-clock-adjudication-output.json").read_text())
        fresh = m6.adjudicate(json.loads(PHONE.read_text()))
        fresh["input_sha256"] = hashlib.sha256(PHONE.read_bytes()).hexdigest()
        self.assertEqual(out, fresh)

    def test_disjoint_peers_do_not_overlap_and_interleaved_peers_do(self):
        self.assertEqual(m6.adjudicate(synthetic(500, 5000))["overlapping_record_pairs"], 0)
        near = m6.adjudicate(synthetic(10, 5000))
        self.assertGreater(near["overlapping_record_pairs"], 0)
        self.assertGreater(near["summed_overlap_ms"], 0)

    def test_survivor_counts_only_records_after_the_earliest_disconnect(self):
        result = m6.adjudicate(synthetic(500, 90))
        self.assertEqual(result["survivor_peer"], "bb")
        self.assertEqual(result["survivor_records_after_disconnect"], 2)
        self.assertEqual(result["survivor_notifications_after_disconnect"], 18)
        self.assertEqual(m6.adjudicate(synthetic(500, 650))["survivor_records_after_disconnect"], 0)

    def test_malformed_input_is_rejected_not_guessed(self):
        bad = synthetic(0, 5000)
        bad["peers"][0]["events"] = [e for e in bad["peers"][0]["events"] if e["seq"] != 1]
        with self.assertRaises(ValueError):
            m6.adjudicate(bad)
        wrong = copy.deepcopy(synthetic(0, 5000))
        wrong["peers"][0]["events"][0]["bytes"] = 100
        with self.assertRaises(ValueError):
            m6.adjudicate(wrong)
        with self.assertRaises(ValueError):
            m6.adjudicate({"peers": synthetic(0, 5000)["peers"][:1]})


if __name__ == "__main__":
    unittest.main()
