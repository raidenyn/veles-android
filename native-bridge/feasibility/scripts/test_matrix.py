"""Tests for the OTP-23 requirements matrix rules (validation, decision, rendering)."""

import copy
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import matrix as m  # noqa: E402

REPO = HERE.parent.parent.parent
EVIDENCE = REPO / "native-bridge/feasibility/evidence/fresh-2026-10-26"
BASE = m.load()


def with_row(row_id, **changes):
    matrix = copy.deepcopy(BASE)
    for row in matrix["rows"]:
        if row["id"] == row_id:
            row.update(changes)
    return matrix


def unrun(row_id, **changes):
    """A row reset to a fresh NOT RUN baseline, so rule tests do not depend on the committed row state."""
    base = dict(status="NOT RUN", actual="", evidence=[], blocker="", waiver="", account="", operator="")
    base.update(changes)
    return with_row(row_id, **base)


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

    def test_review_row_is_durable_evidence_not_a_golden_state(self):
        """R-01 is never waived; before a verified review it blocks; once PASS it must be backed by the actual raw
        review metadata (model, provenance record, commit) and still never makes the decision GO."""
        by_id = {r["id"]: r for r in BASE["rows"]}
        row = by_id["R-01"]
        self.assertEqual(row["waiver"], "")
        self.assertNotIn("R-01", m.waived(BASE))
        # pending state: a copy without PASS must remain a blocking open row
        pending = with_row("R-01", status="NOT RUN", actual="", evidence=[], blocker="")
        self.assertIn("R-01", m.decision(pending)[1])
        # a PASS without actual + evidence is invalid
        self.assertTrue(m.validate(with_row("R-01", actual="", evidence=[])))
        self.assertTrue(m.validate(with_row("R-01", evidence=[])))
        if row["status"] != "PASS":
            self.assertIn("R-01", m.decision(BASE)[1])
            return
        provenance = json.loads((EVIDENCE / "model-provenance.json").read_text(encoding="utf-8"))["final_review"]
        raw_names = [e for e in row["evidence"] if pathlib.Path(e).name.startswith("opus-") and e.endswith(".json")]
        self.assertIn("native-bridge/feasibility/evidence/fresh-2026-10-26/" + provenance["raw_result"], row["evidence"])
        raw = json.loads((REPO / "native-bridge/feasibility/evidence/fresh-2026-10-26" / provenance["raw_result"]).read_text(encoding="utf-8"))
        # actual CLI metadata: exact canonical model, first party, no subagents, no error
        self.assertEqual(list(raw["modelUsage"]), ["claude-opus-5-5"])
        self.assertEqual(raw["subagent_stats"]["spawned"], 0)
        self.assertFalse(raw["is_error"])
        self.assertEqual(provenance["observed_canonicalModel"], "claude-opus-5-5")
        self.assertEqual(provenance["provider"], "firstParty")
        self.assertEqual(provenance["session_id"], raw["session_id"])
        self.assertIn(raw["session_id"], row["actual"])
        commit = provenance["verified_commit"]
        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        self.assertIn(commit, row["actual"])
        self.assertRegex(provenance["verified_record_fix_commit"], r"^[0-9a-f]{40}$")
        self.assertTrue(provenance["status"].startswith("PASS"))
        self.assertEqual(provenance["acceptance_decision"], "NO-GO for OTP-24")
        self.assertTrue(raw_names)
        # the earlier, withdrawn reviews stay as preserved history
        for name in provenance["earlier_reviews_preserved_as_history"]:
            self.assertTrue((REPO / "native-bridge/feasibility/evidence/fresh-2026-10-26" / name).exists(), name)
        # a passed review is not an acceptance: the decision stays NO-GO while any blocking row is open
        verdict, open_rows = m.decision(BASE)
        self.assertEqual(verdict == "GO", not open_rows)
        self.assertNotIn("R-01", open_rows)

    def test_decision_row_records_no_go_and_never_implies_eligibility(self):
        by_id = {r["id"]: r for r in BASE["rows"]}
        d = by_id["D-02"]
        self.assertEqual(d["waiver"], "")
        if d["status"] == "PASS":
            self.assertIn("NO-GO", d["actual"])
            self.assertIn("does not claim OTP-23 satisfied", d["actual"])
        verdict, open_rows = m.decision(BASE)
        blocking = [r["id"] for r in BASE["rows"] if r["status"] in ("BLOCKED", "FAIL", "NOT RUN") and not r["waiver"]]
        self.assertEqual(sorted(open_rows), sorted(blocking))
        self.assertEqual(verdict, "NO-GO" if blocking else "GO")
        self.assertTrue({"M-03", "W-11", "M-09", "A-03", "M-11"} <= set(open_rows))

    def test_g01_is_backed_by_verbatim_same_shell_logs_on_a_committed_clean_source(self):
        g = {r["id"]: r for r in BASE["rows"]}["G-01"]
        joined = " ".join(g["evidence"])
        for needle in ("final-e19c8ca/linux-clean-gates.log", "final-e19c8ca/ci-log-guards-verbatim.log",
                       "ingress-final/ci-log-guards.sh", "mutation-silent-postvalue.log"):
            self.assertIn(needle, joined)
        # the header-only guard file and the PATH-java file are history, never proof
        self.assertNotIn("post-review/log-guards.log", joined)
        self.assertNotIn("post-review/jdk.txt", joined)
        self.assertRegex(g["actual"], r"COMMITTED [0-9a-f]{40}")
        for rel in g["evidence"]:
            self.assertTrue((REPO / rel).exists(), rel)
        gates = (REPO / [e for e in g["evidence"] if e.endswith("final-e19c8ca/linux-clean-gates.log")][0]).read_text(errors="replace")
        for needle in ("JAVA_HOME=/home/dev/.jdk/zulu21", "Launcher JVM:", "Daemon JVM:    /home/dev/.jdk/zulu21", "BUILD SUCCESSFUL", "gradle_exit_code=0"):
            self.assertIn(needle, gates)
        guards = (REPO / [e for e in g["evidence"] if e.endswith("final-e19c8ca/ci-log-guards-verbatim.log")][0]).read_text(errors="replace")
        self.assertEqual(guards.count("grep -r"), 2)  # the script text (bash -v) shows both greps
        self.assertIn("guard_exit_code=0", guards)
        # the executed script is the ci.yml block, byte for byte after de-indentation
        ci = (REPO / ".github/workflows/ci.yml").read_text()
        script = (REPO / [e for e in g["evidence"] if e.endswith("ingress-final/ci-log-guards.sh")][0]).read_text()
        for line in script.strip().splitlines():
            self.assertIn(line.strip(), ci)
        self.assertRegex(g["actual"], r"\b321 JVM tests")

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
        self.assertIn("claude-opus-5-5", by_id["R-01"]["expected"])

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
        self.assertTrue(m.validate(unrun("D-02", waiver=waiver, status="BLOCKED", blocker="b")))
        self.assertTrue(m.validate(unrun("D-02", waiver="skip it")))
        self.assertEqual(m.validate(unrun("D-02", waiver=waiver)), [])
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
        self.assertTrue(m.validate(unrun("D-02", status="PASS", account="admin")))
        self.assertTrue(m.validate(unrun("D-02", status="PASS", actual="zero windows", account="admin")))
        self.assertEqual(m.validate(unrun("D-02", status="PASS", actual="zero windows", evidence=["evidence/x.json"], account="admin")), [])

    def test_human_gated_pass_needs_a_named_operator(self):
        changes = dict(status="PASS", actual="ok", evidence=["e"], blocker="", account="admin")
        self.assertTrue(m.validate(with_row("M-04", **changes)))
        self.assertEqual(m.validate(with_row("M-04", operator="a named person", **changes)), [])

    def test_not_run_rows_carry_no_result(self):
        self.assertTrue(m.validate(unrun("D-02", actual="looks fine")))
        self.assertTrue(m.validate(unrun("D-02", evidence=["x"])))

    def test_blocked_and_fail_need_a_reason_and_statuses_are_closed(self):
        self.assertTrue(m.validate(unrun("D-02", status="BLOCKED")))
        self.assertTrue(m.validate(unrun("D-02", status="FAIL")))
        self.assertTrue(m.validate(unrun("D-02", status="PARTIAL")))
        self.assertTrue(m.validate(unrun("D-02", status="pass")))

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
