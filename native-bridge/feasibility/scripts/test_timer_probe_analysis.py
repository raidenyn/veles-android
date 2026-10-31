"""Pure tests for timer_probe_analysis.py (no Chrome, no device).

    python3 -m unittest test_timer_probe_analysis      (from scripts/)
"""

import unittest

import timer_probe_analysis as tpa


def chain(start_ms, end_ms, step_ms):
    return [float(t) for t in range(start_ms, end_ms, step_ms)]


class Gaps(unittest.TestCase):
    def test_unthrottled_chain_has_one_second_median(self):
        s = tpa.summarize_timers(chain(0, 600_000, 1000), window_start_ms=300_000)
        self.assertEqual(s["median_gap_ms"], 1000)
        self.assertEqual(s["verdict"], "not_intensively_throttled")

    def test_once_per_minute_chain_is_intensively_throttled(self):
        timers = chain(0, 300_000, 1000) + chain(300_000, 600_000, 60_000)
        s = tpa.summarize_timers(timers, window_start_ms=300_000)
        self.assertEqual(s["median_gap_ms"], 60_000)
        self.assertEqual(s["verdict"], "intensively_throttled")

    def test_too_few_samples_is_inconclusive_not_a_verdict(self):
        s = tpa.summarize_timers([300_000.0, 360_000.0], window_start_ms=300_000)
        self.assertEqual(s["verdict"], "inconclusive")


class Messages(unittest.TestCase):
    def test_lag_is_receive_minus_host_send(self):
        msgs = [{"seq": i, "recv_epoch_ms": 1000 * i + 30, "host_epoch_ms": 1000 * i} for i in range(1, 6)]
        s = tpa.summarize_messages(msgs, expected=5)
        self.assertEqual((s["received"], s["expected"], s["max_lag_ms"], s["missing_seqs"]), (5, 5, 30, []))

    def test_missing_sequence_numbers_are_listed(self):
        msgs = [{"seq": 1, "recv_epoch_ms": 10, "host_epoch_ms": 0}, {"seq": 3, "recv_epoch_ms": 40, "host_epoch_ms": 0}]
        s = tpa.summarize_messages(msgs, expected=3)
        self.assertEqual(s["missing_seqs"], [2])

    def test_no_messages(self):
        self.assertIsNone(tpa.summarize_messages([], expected=3)["max_lag_ms"])


if __name__ == "__main__":
    unittest.main()
