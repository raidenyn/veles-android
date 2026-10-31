"""Pure tests for windows_closejoin_compare.py (no host, no Bluetooth, no Chrome).

Review I-A (windows-final Opus review, 2026-10-27) regression coverage:
  - the selection-token resolution that produced the retained compare-1 zero-link attempt;
  - the strengthened run validity rule: exactly TWO first-phase connections, BOTH second
    phone attempts with nothing unresolved, the MEASURED gap within tolerance of the target;
  - the close-discipline preflight edges: empty/one-connection first phases, `released`
    after exit, shutdown not N/N or budget-exceeded, legacy detach mismatch;
  - the offline recheck of saved compare directories (stored-parse/gap consistency).

    python3 -m unittest test_windows_closejoin_compare       (from native-bridge/feasibility/scripts)
"""

import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import windows_closejoin_compare as wcc  # noqa: E402


def awaited_first_parsed(**over):
    parsed = {
        "disconnects": {"2": [1000], "3": [1000]},
        "released": {"2": 5, "3": 6},
        "detached": [],
        "shutdown_joined": (2, 2, 10),
        "shutdown_budget_exceeded": False,
        "exit_index": 7,
        "exit_code": "0",
    }
    parsed.update(over)
    return parsed


class TokenResolution(unittest.TestCase):
    FOUND = {"h1": "03dcc1bc", "h2": "a25859af"}

    def test_all_token_resolves_to_every_discovered_label(self):
        self.assertEqual(wcc.resolve_wanted(["all"], self.FOUND),
                         ["03dcc1bc", "a25859af"])

    def test_any1_token_resolves_to_exactly_one(self):
        self.assertEqual(wcc.resolve_wanted(["any1"], self.FOUND), ["03dcc1bc"])

    def test_pre_resolved_prefixes_pass_through(self):
        self.assertEqual(wcc.resolve_wanted(["a25859af"], self.FOUND), ["a25859af"])
        self.assertEqual(wcc.resolve_wanted([], self.FOUND), [])

    def test_compare1_defect_shape_literal_token_selects_nothing(self):
        """The compare-1 failure: the rewrite left the literal token unconverted, so the
        label filter (`label in wanted`) matched no discovered label and linked ZERO phones.
        The live resolver must differ from that pre-fix behaviour for the same discovery."""
        labels = sorted(self.FOUND.values())
        pre_fix_conversion = lambda w, f: w  # noqa: E731 - the pre-fix rewrite (bug, kept inline)
        chosen_pre_fix = [label for label in labels if label in pre_fix_conversion(["all"], self.FOUND)]
        self.assertEqual(chosen_pre_fix, [])  # zero links -> compare-1 invalid
        self.assertEqual(wcc.resolve_wanted(["all"], self.FOUND), labels)
        self.assertNotEqual(chosen_pre_fix, wcc.resolve_wanted(["all"], self.FOUND))


class CloseDisciplinePreflight(unittest.TestCase):
    def test_awaited_valid_reference(self):
        valid, reason = wcc.validity_for_arm("awaited", awaited_first_parsed())
        self.assertTrue(valid, reason)

    def test_empty_first_phase_is_invalid_in_both_arms(self):
        for arm in ("awaited", "legacy"):
            parsed = awaited_first_parsed(disconnects={})
            if arm == "legacy":
                parsed = {"disconnects": {}, "detached": [], "shutdown_joined": None, "exit_index": None}
            valid, _ = wcc.validity_for_arm(arm, parsed)
            self.assertFalse(valid, arm)

    def test_awaited_invalid_when_released_follows_exit(self):
        parsed = awaited_first_parsed(released={"2": 5, "3": 9})  # exit_index is 7
        valid, reason = wcc.validity_for_arm("awaited", parsed)
        self.assertFalse(valid)
        self.assertIn("released", reason)

    def test_awaited_invalid_when_shutdown_is_not_n_over_n(self):
        parsed = awaited_first_parsed(shutdown_joined=(1, 2, 10))
        valid, _ = wcc.validity_for_arm("awaited", parsed)
        self.assertFalse(valid)

    def test_awaited_invalid_when_budget_exceeded(self):
        parsed = awaited_first_parsed(shutdown_budget_exceeded=True)
        valid, _ = wcc.validity_for_arm("awaited", parsed)
        self.assertFalse(valid)

    def test_legacy_valid_reference_and_detached_mismatch(self):
        legacy = {"disconnects": {"2": [1000], "3": [1000]}, "detached": ["2", "3"],
                  "released": {}, "shutdown_joined": None, "exit_index": None}
        self.assertTrue(wcc.validity_for_arm("legacy", legacy)[0])
        legacy["detached"] = ["2"]
        self.assertFalse(wcc.validity_for_arm("legacy", legacy)[0])


class RunValidity(unittest.TestCase):
    SECOND_ATTEMPTS = ["03dcc1bc", "a25859af"]

    def valid_case(self, **over):
        args = dict(arm="awaited", first_parsed=awaited_first_parsed(),
                    attempts=self.SECOND_ATTEMPTS, missing_wanted=[],
                    gap_ms=1500, target_gap_ms=1500, tolerance_ms=500)
        args.update(over)
        return wcc.run_validity(**args)

    def test_full_run_is_valid(self):
        self.assertEqual(self.valid_case(), (True, None))

    def test_zero_links_first_phase_is_invalid(self):
        valid, reason = self.valid_case(first_parsed=awaited_first_parsed(disconnects={}))
        self.assertFalse(valid)
        self.assertIn("exactly 2", reason)

    def test_one_connection_first_phase_is_invalid(self):
        parsed = awaited_first_parsed(disconnects={"2": [1000]}, released={"2": 5},
                                      shutdown_joined=(1, 1, 10))
        valid, reason = self.valid_case(first_parsed=parsed)
        self.assertFalse(valid)
        self.assertIn("exactly 2", reason)

    def test_three_first_phase_connections_are_invalid(self):
        parsed = awaited_first_parsed(disconnects={"2": [1000], "3": [1000], "4": [1000]},
                                      released={"2": 5, "3": 6, "4": 7},
                                      shutdown_joined=(3, 3, 10))
        valid, reason = self.valid_case(first_parsed=parsed)
        self.assertFalse(valid)
        self.assertIn("exactly 2", reason)

    def test_second_host_must_attempt_every_wanted_phone(self):
        valid, reason = self.valid_case(attempts=["03dcc1bc"])
        self.assertFalse(valid)
        self.assertIn("second-host attempts", reason)

    def test_unresolved_wanted_labels_are_invalid(self):
        valid, reason = self.valid_case(missing_wanted=["all"])
        self.assertFalse(valid)
        self.assertIn("unresolved", reason)

    def test_missing_measured_gap_is_invalid(self):
        valid, reason = self.valid_case(gap_ms=None)
        self.assertFalse(valid)
        self.assertIn("no measured gap", reason)

    def test_measured_gap_outside_tolerance_is_invalid(self):
        valid, reason = self.valid_case(gap_ms=1600, target_gap_ms=1500, tolerance_ms=50)
        self.assertFalse(valid)
        self.assertIn("outside", reason)
        self.assertIn("1500", reason)

    def test_measured_gap_inside_tolerance_is_valid(self):
        self.assertEqual(self.valid_case(gap_ms=1501, target_gap_ms=1500, tolerance_ms=500)[0], True)

    def test_arm_close_discipline_still_applies(self):
        parsed = awaited_first_parsed(released={"2": 5, "3": 9})
        valid, reason = self.valid_case(first_parsed=parsed)
        self.assertFalse(valid)
        self.assertIn("close-discipline preflight", reason)


class RecheckSaved(unittest.TestCase):
    def _write_compare(self, root, first_lines, second_lines, arm="awaited",
                       attempts=("03dcc1bc", "a25859af"), missing_wanted=(),
                       wanted_resolved=("03dcc1bc", "a25859af"), target=1500):
        run_dir = root / "run-1-awaited" if arm == "awaited" else root / "run-1-legacy"
        run_dir.mkdir(parents=True)
        (run_dir / "first.host-trace.log").write_text("\n".join(first_lines) + "\n")
        (run_dir / "second.host-trace.log").write_text("\n".join(second_lines) + "\n")
        first_parsed = wcc.parse_trace(run_dir / "first.host-trace.log")
        second_parsed = wcc.parse_trace(run_dir / "second.host-trace.log")
        result = {
            "args": {"target_gap_from_close_begin_ms": target},
            "runs": [{"index": 1, "arm": arm, "dir": run_dir.name,
                      "validity": None, "gap_ms": 1490, "gap_target_ms": target,
                      "first": {"trace": "first.host-trace.log", "trace_parsed": first_parsed},
                      "second": {"trace": "second.host-trace.log", "trace_parsed": second_parsed,
                                 "connects": [{"label_hash8": a} for a in attempts],
                                 "missing_wanted": list(missing_wanted),
                                 "wanted_resolved": list(wanted_resolved)}}],
        }
        (root / "result.json").write_text(json.dumps(result, indent=2))
        return result

    FIRST = [
        "[  10ms pid=11] conn 2 disconnect requested epoch_ms=1000",
        "[  12ms pid=11] conn 3 disconnect requested epoch_ms=1010",
        "[  13ms pid=11] conn 2 close begin",
        "[  14ms pid=11] conn 2 close step remove_a at 15ms ok",
        "[  15ms pid=11] conn 2 released (objects closed; link-down unconfirmed)",
        "[  16ms pid=11] conn 3 released (objects closed; link-down unconfirmed)",
        "[  17ms pid=11] shutdown joined 2/2 within 10ms",
        "[  18ms pid=11] exit code=0",
    ]
    LEGACY = [
        "[  10ms pid=11] conn 2 disconnect requested epoch_ms=1000",
        "[  12ms pid=11] conn 3 disconnect requested epoch_ms=1010",
        "[  13ms pid=11] conn 2 closing worker detached",
        "[  14ms pid=11] conn 3 closing worker detached",
        "[  15ms pid=11] exit code=0",
    ]
    SECOND = [
        "[   5ms pid=22] conn 2 connect worker started epoch_ms=2500",
        "[   6ms pid=22] conn 3 connect worker started epoch_ms=2500",
    ]

    def test_valid_saved_run_rechecks_valid_with_matching_stored_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "compare-x"
            root.mkdir()
            self._write_compare(root, self.FIRST, self.SECOND)
            summary = wcc.recheck_saved(root)
            self.assertEqual(summary["counts"], {"valid": 1, "invalid": 0, "not-completed": 0})
            run = summary["runs"][0]
            self.assertEqual((run["verdict"], run["measured_gap_ms"]), ("valid", 1490))
            self.assertTrue(run["stored_parse_matches"], run)
            self.assertTrue(run["gap_matches_stored"], run)

    def test_zero_link_saved_run_rechecks_invalid(self):
        """compare-1 shape: no first-phase connections; the strengthened rule must reject it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "compare-zero"
            root.mkdir()
            self._write_compare(root, ["[   1ms pid=11] exit code=0"], self.SECOND,
                                attempts=(), wanted_resolved=())
            summary = wcc.recheck_saved(root)
            run = summary["runs"][0]
            self.assertEqual(run["verdict"], "invalid")
            self.assertIn("exactly 2", run["reason"])
            self.assertEqual(summary["counts"]["invalid"], 1)

    def test_stored_parse_mismatch_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "compare-drift"
            root.mkdir()
            result = self._write_compare(root, self.FIRST, self.SECOND)
            result["runs"][0]["first"]["trace_parsed"]["exit_index"] = 999
            (root / "result.json").write_text(json.dumps(result, indent=2))
            run = wcc.recheck_saved(root)["runs"][0]
            self.assertFalse(run["stored_parse_matches"])

    def test_stored_parse_tuple_round_trip_is_not_reported_as_mismatch(self):
        """The stored result.json serialises tuples as lists; that alone must not flag a drift."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "compare-tuples"
            root.mkdir()
            self._write_compare(root, self.FIRST, self.SECOND)
            stored = json.loads((root / "result.json").read_text())
            stored["runs"][0]["first"]["trace_parsed"]["shutdown_joined"] = [2, 2, 10]
            (root / "result.json").write_text(json.dumps(stored, indent=2))
            run = wcc.recheck_saved(root)["runs"][0]
            self.assertTrue(run["stored_parse_matches"])


if __name__ == "__main__":
    unittest.main()