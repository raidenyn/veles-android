"""Tests for the OTP-23 requirements matrix rules (validation, decision, rendering)."""

import copy
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import matrix as m  # noqa: E402

BASE = m.load()


def with_row(row_id, **changes):
    matrix = copy.deepcopy(BASE)
    for row in matrix["rows"]:
        if row["id"] == row_id:
            row.update(changes)
    return matrix


class CommittedMatrix(unittest.TestCase):
    def test_is_valid_and_its_document_is_current(self):
        self.assertEqual(m.validate(BASE), [])
        self.assertEqual(m.DOC.read_text(encoding="utf-8"), m.render(BASE))

    def test_unrun_hardware_rows_default_to_not_run_and_the_decision_is_no_go(self):
        verdict, open_rows = m.decision(BASE)
        self.assertEqual(verdict == "GO", all(r["status"] == "PASS" for r in BASE["rows"]))
        by_id = {r["id"]: r for r in BASE["rows"]}
        # no hardware row is PASS from fresh scan-only or historical evidence
        for row in BASE["rows"]:
            if row["group"] in ("windows", "macos", "android") and row["id"] not in ("W-13", "W-03"):
                self.assertNotEqual(row["status"], "PASS", row["id"])
        # phone-dependent rows are BLOCKED on the permission prerequisite, with logs as evidence
        for rid in ("W-04", "W-05", "W-06", "W-11", "W-12", "M-07", "M-09", "M-10", "A-03"):
            self.assertEqual(by_id[rid]["status"], "BLOCKED", rid)
            self.assertTrue(by_id[rid]["blocker"], rid)
        # scan-only fresh Windows evidence does not promote link-active cleanup rows
        for rid in ("W-02", "W-07", "W-08", "W-09"):
            self.assertEqual(by_id[rid]["status"], "BLOCKED", rid)
        self.assertIn("HIDDEN", by_id["W-02"]["actual"])
        self.assertIn("W-11", open_rows)

    def test_existing_account_windows_launch_is_a_narrow_non_waived_pass(self):
        row = {r["id"]: r for r in BASE["rows"]}["W-13"]
        self.assertEqual((row["status"], row["account"], row["waiver"]), ("PASS", "admin", ""))
        self.assertIn("fresh-windows-chrome.json", " ".join(row["evidence"]))
        self.assertIn("Proves nothing about phones, transfer or link cleanup", row["actual"])
        self.assertIn("W-01", m.waived(BASE))

    def test_w03_is_a_narrow_pass_and_single_phone_data_never_promotes_two_phone_rows(self):
        by_id = {r["id"]: r for r in BASE["rows"]}
        self.assertEqual(by_id["W-03"]["status"], "PASS")
        self.assertIn("one device", by_id["W-03"]["actual"])
        for rid in ("W-04", "W-05", "W-06"):
            self.assertEqual(by_id[rid]["status"], "BLOCKED", rid)
            self.assertIn("SUPPORTING single-phone evidence only", by_id[rid]["actual"], rid)
            self.assertIn("UNPROVEN", by_id[rid]["actual"], rid)
        self.assertIn("10 Pro", by_id["A-03"]["actual"])
        self.assertNotIn("both phones", by_id["A-03"]["blocker"])
        self.assertEqual(by_id["R-01"]["status"], "NOT RUN")
        self.assertIn("PENDING", by_id["R-01"]["expected"])

    def test_g01_cites_the_audited_verbatim_logs_not_the_header_only_ones(self):
        g = {r["id"]: r for r in BASE["rows"]}["G-01"]
        joined = " ".join(g["evidence"])
        for needle in ("linux-audited-gates.log", "ci-log-guards-verbatim.log", "ci-log-guards.sh", "gradle-version.txt"):
            self.assertIn(needle, joined)
        self.assertNotIn("post-review/log-guards.log", joined)
        self.assertNotIn("post-review/jdk.txt", joined)

    def test_prerequisite_rows_are_blocked_not_bare_not_run(self):
        by_id = {r["id"]: r for r in BASE["rows"]}
        for rid in ("A-01", "A-02", "M-11"):
            self.assertEqual(by_id[rid]["status"], "BLOCKED", rid)
            self.assertTrue(by_id[rid]["blocker"], rid)

    def test_existing_account_unsigned_host_row_is_blocking_and_was_not_run(self):
        row = {r["id"]: r for r in BASE["rows"]}["M-11"]
        self.assertEqual((row["status"], row["waiver"], row["actual"], row["evidence"]), ("BLOCKED", "", "", []))
        self.assertTrue(row["blocker"])
        self.assertIn("M-11", m.decision(BASE)[1])

    def test_tauri_packaging_stays_unjustified_and_review_is_a_placeholder(self):
        by_id = {r["id"]: r for r in BASE["rows"]}
        d = by_id["D-01"]
        self.assertEqual(d["status"], "PASS")
        self.assertIn("NOT APPROVED", d["actual"])
        self.assertIn("NOT JUSTIFIED", d["actual"])
        self.assertIn("not approval of Tauri", d["actual"])
        # the evaluation is not circular: it never requires OTP-25 installer work inside OTP-23
        self.assertNotIn("installer proof", d["blocker"] + d["expected"].replace("no bundler/installer is implemented or required", ""))
        self.assertIn("OTP-24 GO", d["actual"])
        self.assertEqual(by_id["R-01"]["status"], "NOT RUN")
        self.assertIn("claude-opus-5-5", by_id["R-01"]["expected"])
        self.assertIn("R-01", m.decision(BASE)[1])

    def test_account_waiver_covers_exactly_the_new_account_rows_and_is_never_a_pass(self):
        self.assertEqual(m.waived(BASE), ["W-01", "M-01", "M-02"])
        for row in BASE["rows"]:
            if row["waiver"]:
                self.assertEqual(row["status"], "NOT RUN", row["id"])
                self.assertTrue(row["waiver"].startswith("User-approved acceptance scope revision"))
        open_ids = m.decision(BASE)[1]
        for rid in ("W-01", "M-01", "M-02"):
            self.assertNotIn(rid, open_ids)
        # nothing else is relaxed: signing/identity, allow/deny/recovery and strict background stay open
        for rid in ("M-03", "M-04", "M-05", "M-06", "W-11", "M-09"):
            self.assertIn(rid, open_ids)

    def test_waiver_rules(self):
        waiver = "User-approved acceptance scope revision: x"
        self.assertTrue(m.validate(with_row("D-02", waiver=waiver, status="BLOCKED", blocker="b")))
        self.assertTrue(m.validate(with_row("D-02", waiver="skip it")))
        self.assertEqual(m.validate(with_row("D-02", waiver=waiver)), [])
        all_done = copy.deepcopy(BASE)
        for row in all_done["rows"]:
            if not row["waiver"]:
                row.update(status="PASS", actual="ok", evidence=["e"], blocker="", operator="op", account="admin")
        self.assertEqual(m.validate(all_done), [])
        self.assertEqual(m.decision(all_done), ("GO", []))

    def test_pass_must_identify_the_account_truthfully(self):
        changes = dict(status="PASS", actual="ok", evidence=["e"])
        self.assertTrue(m.validate(with_row("W-02", account="", **changes)))
        self.assertTrue(m.validate(with_row("W-02", account="root", **changes)))
        self.assertEqual(m.validate(with_row("W-02", account="standard", **changes)), [])

    def test_every_macos_account_and_tcc_row_stays_blocked_without_human_access(self):
        rows = {r["id"]: r for r in BASE["rows"]}
        for rid in ("M-03", "M-04", "M-05", "M-06"):
            self.assertEqual(rows[rid]["status"], "BLOCKED", rid)
            self.assertTrue(rows[rid]["human_gated"], rid)
            self.assertTrue(rows[rid]["blocker"], rid)
        text = " ".join(rows[r]["requirement"] + rows[r]["expected"] for r in ("M-01", "M-02", "M-03", "M-04", "M-05", "M-06"))
        for needle in ("FRESH STANDARD", "prompt owner", "signed", "final identifier", "DENY", "ALLOW", "SAME extension state"):
            self.assertIn(needle.lower(), text.lower())

    def test_covers_the_rfc_acceptance_criteria_and_the_added_requirements(self):
        text = json.dumps(BASE).lower()
        for needle in ("standard non-admin", "no window", "bounded device events", "independently exchange",
                       "disconnect affects only its handle", "port closure", "stdin eof", "shutdown",
                       "denied", "ten", "cold", "prewarm", "m6", "tauri", "507/508/509", "1015/1016/1017",
                       "4095/4096", "no devtools client"):
            self.assertIn(needle if needle != "ten" else "10 min", text, needle)

    def test_cold_and_prewarm_background_are_separate_rows_on_each_platform(self):
        ids = {r["id"] for r in BASE["rows"]}
        self.assertTrue({"W-11", "W-12", "M-09", "M-10"} <= ids)


class Rules(unittest.TestCase):
    def test_pass_requires_actual_and_evidence(self):
        self.assertTrue(m.validate(with_row("D-02", status="PASS", account="admin")))
        self.assertTrue(m.validate(with_row("D-02", status="PASS", actual="zero windows", account="admin")))
        self.assertEqual(m.validate(with_row("D-02", status="PASS", actual="zero windows", evidence=["evidence/x.json"], account="admin")), [])

    def test_human_gated_pass_needs_a_named_operator(self):
        changes = dict(status="PASS", actual="ok", evidence=["e"], blocker="", account="admin")
        self.assertTrue(m.validate(with_row("M-04", **changes)))
        self.assertEqual(m.validate(with_row("M-04", operator="a named person", **changes)), [])

    def test_not_run_rows_carry_no_result(self):
        self.assertTrue(m.validate(with_row("D-02", actual="looks fine")))
        self.assertTrue(m.validate(with_row("D-02", evidence=["x"])))

    def test_blocked_and_fail_need_a_reason_and_statuses_are_closed(self):
        self.assertTrue(m.validate(with_row("D-02", status="BLOCKED")))
        self.assertTrue(m.validate(with_row("D-02", status="FAIL")))
        self.assertTrue(m.validate(with_row("D-02", status="PARTIAL")))
        self.assertTrue(m.validate(with_row("D-02", status="pass")))

    def test_default_status_and_duplicates_are_enforced(self):
        changed = copy.deepcopy(BASE)
        changed["default_status"] = "PASS"
        self.assertTrue(m.validate(changed))
        dup = copy.deepcopy(BASE)
        dup["rows"].append(copy.deepcopy(dup["rows"][0]))
        self.assertTrue(any("duplicate" in p for p in m.validate(dup)))

    def test_one_open_row_keeps_the_decision_no_go(self):
        all_pass = copy.deepcopy(BASE)
        for row in all_pass["rows"]:
            row.update(status="PASS", actual="ok", evidence=["e"], blocker="", operator="op", waiver="", account="admin")
        self.assertEqual(m.validate(all_pass), [])
        self.assertEqual(m.decision(all_pass), ("GO", []))
        all_pass["rows"][3]["status"] = "FAIL"
        all_pass["rows"][3]["blocker"] = "x"
        self.assertEqual(m.decision(all_pass)[0], "NO-GO")


if __name__ == "__main__":
    unittest.main()
