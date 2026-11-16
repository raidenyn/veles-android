"""Tests for the Gate B evidence matrix validator and the Gate B timing rules.

The fixtures below repeat the required row table, status vocabulary and evidence field list
from the OTP-12 spec literally, so a wrong constant in the validator cannot satisfy its own
test. Nothing here sleeps or reads a clock: the timing rules take integer seconds and are
exercised with supplied values only.

Stdlib only; Python 3.8 compatible; discovered by
``python3 -m unittest discover -s docs/checks``.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

CHECKS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CHECKS)

from check_gate_b_matrix import (  # noqa: E402
    AFFIRMATIVE_CASE_EVIDENCE,
    AFFIRMATIVE_EVIDENCE_FIELDS,
    CONTENT_CASE_EVIDENCE,
    CONTENT_EVIDENCE_FIELDS,
    NOT_ACCEPTANCE_STATEMENT,
    TEXT_EVIDENCE_FIELDS,
    TEXT_OR_LIST_EVIDENCE_FIELDS,
    main,
    report,
    validate,
)
from gate_b_timing import background_ok, twenty_minute_ok  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(CHECKS))
MATRIX_PATH = os.path.join(ROOT, "docs", "gate-b", "matrix.json")
JOURNEYS_PATH = os.path.join(ROOT, "protocol", "conformance", "gate-b-journeys.json")
RUNBOOK_PATH = os.path.join(ROOT, "docs", "otp-12-gate-b-runbook.md")

NONE_CASES = ("host-missing-recovery", "host-incompatible-recovery")
A_CASES = (
    "extension-search",
    "spake2-pairing",
    "hint-reconnect-fresh-keys",
    "protected-pull",
    "push",
    "background-delivery",
    "offscreen-copy",
    "forced-freeze",
    "forced-discard",
    "connector-closure",
    "connector-restart",
    "host-closure",
    "host-restart",
    "revocation",
    "bluetooth-interruption",
)
AB_CASES = ("two-phone-lifecycle",)
ANDROID_A_CASES = ("locked-phone-20min", "fixture-retention-expiry")
ALL_CASES = NONE_CASES + A_CASES + AB_CASES + ANDROID_A_CASES
EXPECTED_ROW_IDS = frozenset(
    [
        "%s.%s.%s" % (platform, phones, case)
        for platform in ("windows", "macos")
        for phones, cases in (("none", NONE_CASES), ("A", A_CASES), ("AB", AB_CASES))
        for case in cases
    ]
    + ["android.A.%s" % case for case in ANDROID_A_CASES]
)

JOURNEY_IDS = frozenset(("enrollment", "protected-records"))
JOURNEYS_BY_CASE = {
    "extension-search": ("enrollment",),
    "spake2-pairing": ("enrollment",),
    "hint-reconnect-fresh-keys": ("enrollment",),
    "connector-restart": ("enrollment",),
    "host-restart": ("enrollment",),
    "bluetooth-interruption": ("enrollment",),
    "two-phone-lifecycle": ("enrollment",),
    "protected-pull": ("protected-records",),
    "push": ("protected-records",),
    "background-delivery": ("protected-records",),
    "offscreen-copy": ("protected-records",),
    "forced-freeze": ("protected-records",),
    "forced-discard": ("protected-records",),
    "revocation": ("protected-records",),
    "locked-phone-20min": ("protected-records",),
    "fixture-retention-expiry": ("protected-records",),
}

EVIDENCE_FIELDS = (
    "source_revision",
    "evaluator_revision",
    "hashes",
    "identities",
    "models",
    "os_builds",
    "chrome_versions",
    "adapters",
    "focused_tab",
    "exemption_absent",
    "actions",
    "bindings",
    "return_codes",
    "expected",
    "actual",
    "classification",
    "cleanup",
    "raw_captures",
    "checksum_catalog",
    "repetitions",
    "reproducibility",
    "no_plaintext_fallback",
    "host_process_queue",
    "redacted_logs",
    "provenance_anchor",
    "adjudication",
)

FREEZE_EXTRAS = (
    "forced_trace",
    "tabs_observations",
    "badge_clear",
    "port_generation_invalidated",
    "old_port_closed_no_host",
    "repull_retained_expired",
)
CASE_EXTRAS = {
    "forced-freeze": FREEZE_EXTRAS,
    "forced-discard": FREEZE_EXTRAS,
    "connector-restart": ("fresh_handles", "fresh_keys"),
    "host-restart": ("fresh_handles", "fresh_keys"),
    "hint-reconnect-fresh-keys": ("fresh_keys",),
    "bluetooth-interruption": ("fresh_keys",),
    "offscreen-copy": ("clipboard_transition",),
    "connector-closure": ("no_host_process",),
    "host-closure": ("no_host_process",),
    "background-delivery": ("other_tab_focused",),
    "locked-phone-20min": ("foreground_present",),
}


def base_row(platform, phones, case):
    return {
        "id": "%s.%s.%s" % (platform, phones, case),
        "platform": platform,
        "phones": phones,
        "case": case,
        "journeys": list(JOURNEYS_BY_CASE.get(case, ())),
        "status": "NOT RUN",
        "blocker": "no Gate B physical run has been performed for this row",
        "no_bond_handle_or_hint_reliance": None,
    }


def evidence(case):
    record = {field: "recorded" for field in EVIDENCE_FIELDS}
    record["exemption_absent"] = True
    record["no_plaintext_fallback"] = True
    record["reproducibility"] = "reproducible from the raw captures"
    record["classification"] = "physical run"
    for field in CASE_EXTRAS.get(case, ()):
        record[field] = True if field in ("foreground_present", "other_tab_focused") else "recorded"
    return record


def rows_for(status):
    rows = []
    for row_id in sorted(EXPECTED_ROW_IDS):
        platform, phones, case = row_id.split(".")
        row = base_row(platform, phones, case)
        row["status"] = status
        if status == "PASS":
            row["no_bond_handle_or_hint_reliance"] = True
        if status != "NOT RUN":
            row["evidence"] = evidence(case)
            row["blocker"] = ""
        rows.append(row)
    return rows


def initial_matrix():
    return {
        "decision": "OPEN",
        "gate_b_accepted": False,
        "initial_state": True,
        "rows": rows_for("NOT RUN"),
    }


def verdict_matrix(status="PASS", row_id="windows.A.push"):
    matrix = initial_matrix()
    matrix["initial_state"] = False
    for row in matrix["rows"]:
        if row["id"] == row_id:
            row["status"] = status
            if status == "PASS":
                row["no_bond_handle_or_hint_reliance"] = True
            row["evidence"] = evidence(row["case"])
            row["blocker"] = ""
    return matrix


def accepted_matrix():
    matrix = {
        "decision": "ACCEPTED",
        "gate_b_accepted": True,
        "initial_state": False,
        "decision_reference": "adjudication record outside this repository",
        "rows": rows_for("PASS"),
    }
    return matrix


def row_of(matrix, row_id):
    for row in matrix["rows"]:
        if row["id"] == row_id:
            return row
    raise AssertionError("fixture has no row %s" % row_id)


class GateBMatrixTest(unittest.TestCase):
    def findings(self, matrix, journey_ids=JOURNEY_IDS):
        return validate(matrix, set(journey_ids))

    def assert_finding(self, findings, fragment):
        self.assertTrue(
            any(fragment in finding for finding in findings),
            "expected a finding containing %r, got %r" % (fragment, findings),
        )

    def test_committed_matrix_validates(self):
        with open(MATRIX_PATH, encoding="utf-8") as handle:
            matrix = json.load(handle)
        with open(JOURNEYS_PATH, encoding="utf-8") as handle:
            journeys = json.load(handle)
        journey_ids = set(journeys["journeys"])
        before = json.loads(json.dumps(matrix))

        self.assertEqual(self.findings(matrix, journey_ids), [])
        self.assertEqual(matrix, before, "the validator must not modify the matrix")
        self.assertEqual({row["id"] for row in matrix["rows"]}, EXPECTED_ROW_IDS)
        self.assertEqual(len(matrix["rows"]), len(EXPECTED_ROW_IDS))
        for row in matrix["rows"]:
            self.assertIn(row["status"], ("NOT RUN", "FAIL", "PASS"), row["id"])
            for journey in row["journeys"]:
                self.assertIn(journey, journey_ids, row["id"])

        cited = {journey for row in matrix["rows"] for journey in row["journeys"]}
        self.assertEqual(cited, journey_ids)

        if matrix["initial_state"] is True:
            # The initial-state pin: the committed matrix must stay visibly not accepted.
            self.assertEqual(matrix["decision"], "OPEN")
            self.assertIs(matrix["gate_b_accepted"], False)
            for row in matrix["rows"]:
                self.assertEqual(row["status"], "NOT RUN", row["id"])
                self.assertTrue(row["blocker"].strip(), row["id"])
                self.assertIsNone(row["no_bond_handle_or_hint_reliance"], row["id"])
                self.assertNotIn("evidence", row, row["id"])
        else:
            # A later evidence commit released the pin; only the invariants apply.
            if matrix["gate_b_accepted"] is True:
                self.assertEqual(matrix["decision"], "ACCEPTED")
                self.assertTrue(str(matrix.get("decision_reference", "")).strip())
            for row in matrix["rows"]:
                if row["status"] == "PASS":
                    self.assertIs(row["no_bond_handle_or_hint_reliance"], True, row["id"])
                    self.assertIn("evidence", row, row["id"])

    def test_later_evidence_state_is_not_pinned(self):
        # The committed matrix stays pinned; a later commit releases the pin in place.
        with open(MATRIX_PATH, encoding="utf-8") as handle:
            committed = json.load(handle)
        self.assertIs(committed["initial_state"], True)
        released = copy.deepcopy(committed)
        released["initial_state"] = False
        self.assertEqual(self.findings(released), [])

        # Releasing the pin does not by itself make a matrix valid: the invariants still apply.
        later = initial_matrix()
        later["initial_state"] = False
        self.assertEqual(self.findings(later), [])

        # A valid later-state matrix with all rows PASS and full evidence validates.
        accepted = accepted_matrix()
        self.assertEqual(self.findings(accepted), [])

        # The same evidence under initial_state true is rejected by the pin.
        pinned = copy.deepcopy(accepted)
        pinned["initial_state"] = True
        self.assert_finding(self.findings(pinned), "initial_state")

        # Releasing the pin does not relax the invariants.
        without_reference = copy.deepcopy(accepted)
        del without_reference["decision_reference"]
        self.assert_finding(self.findings(without_reference), "decision_reference")

    def test_missing_row_names_the_id(self):
        matrix = initial_matrix()
        matrix["rows"] = [row for row in matrix["rows"] if row["id"] != "windows.A.push"]
        self.assert_finding(self.findings(matrix), "windows.A.push")

        extra = base_row("windows", "A", "teleport")
        matrix["rows"].append(extra)
        self.assert_finding(self.findings(matrix), "windows.A.teleport")

    def test_unknown_status_rejected(self):
        matrix = initial_matrix()
        row_of(matrix, "macos.A.revocation")["status"] = "BLOCKED"
        findings = self.findings(matrix)
        self.assert_finding(findings, "macos.A.revocation")
        self.assert_finding(findings, "BLOCKED")

    def test_initial_state_pin_requires_all_not_run(self):
        matrix = initial_matrix()
        self.assertEqual(self.findings(matrix), [])

        mutated = copy.deepcopy(matrix)
        mutated["decision"] = "ACCEPTED"
        self.assert_finding(self.findings(mutated), "OPEN")

        mutated = copy.deepcopy(matrix)
        mutated["gate_b_accepted"] = True
        self.assertTrue(self.findings(mutated))

        mutated = copy.deepcopy(matrix)
        row = row_of(mutated, "android.A.locked-phone-20min")
        row["status"] = "FAIL"
        row["evidence"] = evidence(row["case"])
        self.assert_finding(self.findings(mutated), "NOT RUN")

        mutated = copy.deepcopy(matrix)
        row_of(mutated, "windows.A.push")["blocker"] = "   "
        self.assert_finding(self.findings(mutated), "blocker")

    def test_accepted_without_every_pass_rejected(self):
        matrix = accepted_matrix()
        row = row_of(matrix, "windows.AB.two-phone-lifecycle")
        row["status"] = "FAIL"
        row["no_bond_handle_or_hint_reliance"] = None
        row["evidence"] = evidence(row["case"])
        findings = self.findings(matrix)
        self.assert_finding(findings, "windows.AB.two-phone-lifecycle")
        self.assert_finding(findings, "PASS")

    def test_nonrun_status_without_captures_rejected(self):
        for status in ("PASS", "FAIL"):
            matrix = verdict_matrix(status)
            record = row_of(matrix, "windows.A.push")["evidence"]
            del record["raw_captures"]
            del record["checksum_catalog"]
            findings = self.findings(matrix)
            self.assert_finding(findings, "raw_captures")
            self.assert_finding(findings, "checksum_catalog")
        matrix = verdict_matrix("FAIL")
        self.assertEqual(self.findings(matrix), [])

    def test_pass_requires_no_bond_field_true(self):
        matrix = verdict_matrix("PASS")
        self.assertEqual(self.findings(matrix), [])
        row_of(matrix, "windows.A.push")["no_bond_handle_or_hint_reliance"] = None
        self.assert_finding(self.findings(matrix), "no_bond_handle_or_hint_reliance")

        matrix = verdict_matrix("FAIL")
        row_of(matrix, "windows.A.push")["no_bond_handle_or_hint_reliance"] = True
        self.assert_finding(self.findings(matrix), "no_bond_handle_or_hint_reliance")

    def test_accepted_requires_decision_reference(self):
        matrix = accepted_matrix()
        self.assertEqual(self.findings(matrix), [])
        del matrix["decision_reference"]
        self.assert_finding(self.findings(matrix), "decision_reference")

    def test_restart_row_requires_fresh_handles_and_keys(self):
        matrix = verdict_matrix("PASS", "windows.A.connector-restart")
        self.assertEqual(self.findings(matrix), [])
        del row_of(matrix, "windows.A.connector-restart")["evidence"]["fresh_handles"]
        self.assert_finding(self.findings(matrix), "fresh_handles")

        matrix = verdict_matrix("PASS", "macos.A.host-restart")
        self.assertEqual(self.findings(matrix), [])
        del row_of(matrix, "macos.A.host-restart")["evidence"]["fresh_keys"]
        self.assert_finding(self.findings(matrix), "fresh_keys")

    def test_reconnect_row_requires_fresh_keys(self):
        matrix = verdict_matrix("PASS", "windows.A.hint-reconnect-fresh-keys")
        self.assertEqual(self.findings(matrix), [])
        del row_of(matrix, "windows.A.hint-reconnect-fresh-keys")["evidence"]["fresh_keys"]
        self.assert_finding(self.findings(matrix), "fresh_keys")

    def test_offscreen_row_requires_clipboard_transition(self):
        matrix = verdict_matrix("PASS", "windows.A.offscreen-copy")
        self.assertEqual(self.findings(matrix), [])
        del row_of(matrix, "windows.A.offscreen-copy")["evidence"]["clipboard_transition"]
        self.assert_finding(self.findings(matrix), "clipboard_transition")

    def test_freeze_row_requires_retained_expired_result(self):
        matrix = verdict_matrix("PASS", "windows.A.forced-freeze")
        self.assertEqual(self.findings(matrix), [])
        del row_of(matrix, "windows.A.forced-freeze")["evidence"]["repull_retained_expired"]
        self.assert_finding(self.findings(matrix), "repull_retained_expired")

    def test_twenty_minute_requires_foreground_present(self):
        matrix = verdict_matrix("PASS", "android.A.locked-phone-20min")
        self.assertEqual(self.findings(matrix), [])
        del row_of(matrix, "android.A.locked-phone-20min")["evidence"]["foreground_present"]
        self.assert_finding(self.findings(matrix), "foreground_present")

    def test_background_requires_other_tab_focused(self):
        matrix = verdict_matrix("PASS", "windows.A.background-delivery")
        self.assertEqual(self.findings(matrix), [])
        record = row_of(matrix, "windows.A.background-delivery")["evidence"]
        del record["other_tab_focused"]
        self.assert_finding(self.findings(matrix), "other_tab_focused")

        matrix = verdict_matrix("PASS", "windows.A.background-delivery")
        record = row_of(matrix, "windows.A.background-delivery")["evidence"]
        record["exemption_absent"] = False
        self.assert_finding(self.findings(matrix), "exemption_absent")

    def test_pass_without_adjudication_or_anchor_rejected(self):
        matrix = verdict_matrix("PASS")
        del row_of(matrix, "windows.A.push")["evidence"]["adjudication"]
        self.assert_finding(self.findings(matrix), "adjudication")

        matrix = verdict_matrix("PASS")
        del row_of(matrix, "windows.A.push")["evidence"]["provenance_anchor"]
        self.assert_finding(self.findings(matrix), "provenance_anchor")

    def test_not_run_with_evidence_rejected(self):
        matrix = initial_matrix()
        row = row_of(matrix, "macos.A.extension-search")
        row["evidence"] = evidence(row["case"])
        findings = self.findings(matrix)
        self.assert_finding(findings, "macos.A.extension-search")
        self.assert_finding(findings, "evidence")

    def test_freeze_row_without_badge_clear_rejected(self):
        matrix = verdict_matrix("PASS", "macos.A.forced-discard")
        self.assertEqual(self.findings(matrix), [])
        del row_of(matrix, "macos.A.forced-discard")["evidence"]["badge_clear"]
        self.assert_finding(self.findings(matrix), "badge_clear")

    def test_closure_row_without_no_host_process_rejected(self):
        matrix = verdict_matrix("PASS", "macos.A.host-closure")
        self.assertEqual(self.findings(matrix), [])
        del row_of(matrix, "macos.A.host-closure")["evidence"]["no_host_process"]
        self.assert_finding(self.findings(matrix), "no_host_process")

    def test_arrival_18_54_fails_19_00_passes_21_00_passes_21_01_fails(self):
        minute0 = 1_000_000
        arrival = minute0 + 20 * 60

        def accepts(arrival_time):
            return twenty_minute_ok(
                minute0,
                arrival_time,
                minute0 + 60,
                minute0 + 60 + 16 * 60,
                minute0 + 970,
                1200,
                True,
            )

        self.assertTrue(accepts(arrival))
        self.assertFalse(accepts(minute0 + 18 * 60 + 54))
        self.assertTrue(accepts(minute0 + 19 * 60))
        self.assertTrue(accepts(minute0 + 21 * 60))
        self.assertFalse(accepts(minute0 + 21 * 60 + 1))

    def test_unlock_at_14_minutes_fails(self):
        minute0 = 1_000_000
        task_removal = minute0 + 60

        def accepts(unlock_time):
            return twenty_minute_ok(
                minute0, minute0 + 20 * 60, task_removal, unlock_time, minute0 + 970, 1200, True
            )

        self.assertFalse(accepts(task_removal + 14 * 60))
        self.assertTrue(accepts(task_removal + 15 * 60))

    def test_pull_at_minute_15_fails(self):
        minute0 = 1_000_000
        task_removal = minute0 + 60

        def accepts(pull_time):
            return twenty_minute_ok(
                minute0,
                minute0 + 20 * 60,
                task_removal,
                task_removal + 16 * 60,
                pull_time,
                1200,
                True,
            )

        self.assertFalse(accepts(minute0 + 15 * 60))
        self.assertTrue(accepts(minute0 + 15 * 60 + 1))

    def test_delay_not_1200_fails(self):
        minute0 = 1_000_000
        task_removal = minute0 + 60
        for delay in (0, 600, 1199, 1201, 7200):
            with self.subTest(delay=delay):
                self.assertFalse(
                    twenty_minute_ok(
                        minute0,
                        minute0 + 20 * 60,
                        task_removal,
                        task_removal + 16 * 60,
                        minute0 + 970,
                        delay,
                        True,
                    )
                )

    def test_background_needs_minute_seven_event_and_no_exemption(self):
        start = 50_000
        end = start + 10 * 60
        seven = start + 7 * 60

        # Both the event and its copy must be strictly after minute seven.
        self.assertTrue(background_ok(start, end, seven + 1, seven + 1, True, False))
        self.assertTrue(background_ok(start, end, seven + 5, seven + 6, True, False))
        self.assertFalse(background_ok(start, end, seven, seven, True, False))
        self.assertFalse(background_ok(start, end, seven, seven + 1, True, False))
        self.assertFalse(background_ok(start, end, seven + 1, seven, True, False))
        self.assertFalse(background_ok(start, end, seven - 1, seven, True, False))
        self.assertFalse(background_ok(start, end, seven + 5, seven - 1, True, False))
        self.assertFalse(background_ok(start, end - 1, seven + 5, seven + 6, True, False))
        self.assertFalse(background_ok(start, end, seven + 5, seven + 6, False, False))
        self.assertFalse(background_ok(start, end, seven + 5, seven + 6, True, True))
        self.assertFalse(background_ok(start, end, end + 1, end + 2, True, False))

    def test_placeholder_boolean_evidence_rejected(self):
        # An accepted matrix whose evidence is only booleans is not evidence: references and
        # content fields need real values, and a boolean decision reference is not a reference.
        matrix = accepted_matrix()
        matrix["decision_reference"] = True
        for row in matrix["rows"]:
            row["evidence"] = {field: True for field in EVIDENCE_FIELDS}
            for field in CASE_EXTRAS.get(row["case"], ()):
                row["evidence"][field] = True
        findings = self.findings(matrix)
        self.assert_finding(findings, "decision_reference")
        self.assert_finding(findings, "source_revision")
        self.assert_finding(findings, "provenance_anchor")
        self.assert_finding(findings, "raw_captures")
        self.assert_finding(findings, "actions")

        # Case extras need content too; the affirmative flags are the only booleans.
        for row_id, fields in (
            ("windows.A.forced-freeze", ("badge_clear", "forced_trace")),
            ("windows.A.offscreen-copy", ("clipboard_transition",)),
            ("macos.A.connector-restart", ("fresh_keys", "fresh_handles")),
        ):
            matrix = verdict_matrix("PASS", row_id)
            record = row_of(matrix, row_id)["evidence"]
            for field in fields:
                record[field] = False
            findings = self.findings(matrix)
            for field in fields:
                self.assert_finding(findings, field)

        matrix = verdict_matrix("PASS", "android.A.locked-phone-20min")
        row_of(matrix, "android.A.locked-phone-20min")["evidence"]["foreground_present"] = False
        self.assert_finding(self.findings(matrix), "foreground_present")

        # A single affirmative flag turned into content is rejected as well.
        matrix = verdict_matrix("PASS")
        row_of(matrix, "windows.A.push")["evidence"]["exemption_absent"] = "yes"
        self.assert_finding(self.findings(matrix), "exemption_absent")

    def test_evidence_type_categories_cover_every_field(self):
        categorised = set(TEXT_EVIDENCE_FIELDS) | set(TEXT_OR_LIST_EVIDENCE_FIELDS)
        categorised |= set(CONTENT_EVIDENCE_FIELDS) | set(AFFIRMATIVE_EVIDENCE_FIELDS)
        self.assertEqual(categorised, set(EVIDENCE_FIELDS))
        self.assertEqual(
            len(TEXT_EVIDENCE_FIELDS)
            + len(TEXT_OR_LIST_EVIDENCE_FIELDS)
            + len(CONTENT_EVIDENCE_FIELDS)
            + len(AFFIRMATIVE_EVIDENCE_FIELDS),
            len(EVIDENCE_FIELDS),
            "an evidence field must belong to exactly one type category",
        )
        case_extras = set()
        for fields in CASE_EXTRAS.values():
            case_extras |= set(fields)
        self.assertEqual(
            case_extras, set(AFFIRMATIVE_CASE_EVIDENCE) | set(CONTENT_CASE_EVIDENCE)
        )

    def test_malformed_case_type_does_not_raise(self):
        matrix = verdict_matrix("PASS", "windows.A.push")
        row = row_of(matrix, "windows.A.push")
        row["case"] = {"unexpected": "object"}
        findings = self.findings(matrix)
        self.assert_finding(findings, "case must be a string")
        self.assert_finding(findings, "windows.A.push")

    def test_output_states_tool_does_not_accept_gate_b(self):
        matrix = verdict_matrix("PASS")
        before = copy.deepcopy(matrix)
        journey_ids = set(JOURNEY_IDS)
        direct = validate(matrix, journey_ids)
        self.assertIsInstance(direct, list)
        self.assertEqual(direct, [])
        self.assertTrue(direct.statement)
        self.assertIn(NOT_ACCEPTANCE_STATEMENT, direct.statement)
        self.assertIn(NOT_ACCEPTANCE_STATEMENT, str(direct))
        self.assertIn(NOT_ACCEPTANCE_STATEMENT, report(matrix, journey_ids))
        self.assertEqual(matrix, before)
        self.assertEqual(row_of(matrix, "windows.A.push")["status"], "PASS")

        temporary = tempfile.mkdtemp(prefix="veles-gate-b-matrix-")
        self.addCleanup(shutil.rmtree, temporary, True)
        path = os.path.join(temporary, "matrix.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(matrix, handle)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--matrix", path, "--journeys", JOURNEYS_PATH])
        printed = output.getvalue()
        self.assertEqual(code, 0, printed)
        self.assertIn(NOT_ACCEPTANCE_STATEMENT, printed)
        self.assertIn("not Gate B acceptance", printed)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), before)

        with open(MATRIX_PATH, encoding="utf-8") as handle:
            committed = json.load(handle)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--matrix", MATRIX_PATH, "--journeys", JOURNEYS_PATH]), 0)
        self.assertIn(NOT_ACCEPTANCE_STATEMENT, output.getvalue())
        self.assertEqual(committed["decision"], "OPEN")
        self.assertIs(committed["gate_b_accepted"], False)

    def test_runbook_has_section_per_case_and_required_statements(self):
        with open(RUNBOOK_PATH, encoding="utf-8") as handle:
            text = handle.read()

        sections = {}
        current = None
        for line in text.splitlines():
            if line.startswith("## "):
                current = line[3:].strip()
                sections[current] = []
            elif current is not None:
                sections[current].append(line)

        for case in ALL_CASES:
            self.assertIn(case, sections, "runbook has no section for %s" % case)
            body = "\n".join(sections[case])
            for marker in ("Steps", "Commands", "Record"):
                self.assertIn(marker, body, "%s section has no %s block" % (case, marker))

        lowered = text.lower()
        for statement in (
            "minute 0 is the controller-clock time",
            "reseed",
            "evicts one on the client",
            "OTP-24 physical acceptance is not complete".lower(),
            "recorded as an identity",
            "never Gate B evidence",
        ):
            self.assertIn(statement.lower(), lowered, "runbook lacks %r" % statement)

        # The locked-phone rule requires the phone to be locked at task removal, so the
        # procedure must lock first, remove the task while locked, and pull after minute 15.
        locked = "\n".join(sections["locked-phone-20min"]).lower()
        self.assertIn("lock the screen first", locked)
        self.assertLess(
            locked.index("lock the screen first"),
            locked.index("remove the app task"),
            "the locked-phone procedure must lock before it removes the task",
        )
        self.assertLess(
            locked.index("remove the app task"),
            locked.index("reseed"),
            "the locked-phone procedure must remove the task before the post-minute-15 pull",
        )


if __name__ == "__main__":
    unittest.main()
