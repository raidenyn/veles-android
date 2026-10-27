"""Tests for the OTP-23 requirements matrix rules (validation, decision, rendering)."""

import copy
import hashlib
import json
import pathlib
import re
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

    def test_hardware_rows_are_pass_only_with_actual_post_grant_or_narrow_fresh_evidence(self):
        verdict, open_rows = m.decision(BASE)
        self.assertEqual(verdict == "GO", all(r["status"] == "PASS" for r in BASE["rows"] if not r["waiver"]))
        by_id = {r["id"]: r for r in BASE["rows"]}
        # every hardware PASS is either one of the narrow pre-grant passes or backed by the post-grant raw files
        for row in BASE["rows"]:
            if row["group"] in ("windows", "macos", "android") and row["status"] == "PASS" and row["id"] not in ("W-13", "W-03"):
                self.assertIn("post-grant-2026-10-26/", " ".join(row["evidence"]), row["id"])
                self.assertTrue(row["account"] or row["group"] == "android", row["id"])
        # promotions that the permission-granted physical runs support
        for rid in ("W-04", "W-05", "W-06", "W-09", "M-05", "M-06", "M-07", "A-02", "A-03"):
            self.assertEqual(by_id[rid]["status"], "PASS", rid)
        # rows whose literal requirement the new evidence does not meet stay blocked, each with a reason
        for rid in ("W-02", "W-07", "W-08", "W-10", "W-11", "W-12", "M-03", "M-04", "M-08", "A-01", "M-11"):
            self.assertEqual(by_id[rid]["status"], "BLOCKED", rid)
            self.assertTrue(by_id[rid]["blocker"], rid)
            self.assertIn(rid, open_rows)
        self.assertIn("W-11", open_rows)

    def test_post_grant_pass_rows_are_backed_by_the_actual_raw_files(self):
        base = REPO / "native-bridge/feasibility/evidence/post-grant-2026-10-26"
        by_id = {r["id"]: r for r in BASE["rows"]}
        for rid in ("W-04", "W-05", "W-06", "W-09", "M-05", "M-06", "M-07", "A-02", "A-03"):
            for rel in by_id[rid]["evidence"]:
                self.assertTrue((REPO / rel).exists(), (rid, rel))
        win = json.loads((base / "windows-initial/windows-physical-complete.json").read_text())
        self.assertEqual((win["result"], len(win["cases"]), {c["result"] for c in win["cases"]}), ("PASS", 25, {"PASS"}))
        self.assertEqual(win["host_exe_sha256"], "6cf72ed0d2e9140420cca8167e8b8d7322c6afc74ab73a5a68a5189c7819fcda")
        for size in (177, 178, 179, 507, 508, 509, 1015, 1016, 1017, 4095, 4096):
            self.assertIn(size, win["sizes"])
        mac = json.loads((base / "macos-initial/macos-physical-complete.json").read_text())
        self.assertEqual((mac["result"], len(mac["results"])), ("PASS", 26))
        self.assertEqual(mac["host_binary_sha256"], "e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c")
        # macOS reports a measured max write-without-response, never a made-up MTU
        self.assertIn("NOT an MTU of 515", by_id["M-07"]["actual"])
        # W-09: only the rerun that actually passed shutdown is cited; the earlier BLOCKED run stays recorded
        lc = json.loads((base / "windows-final/post-grant-2026-10-27/windows-lifecycle-scan-active/lifecycle.json").read_text())
        res = {r["id"]: r["status"] for r in lc["results"]}
        self.assertEqual(res["LC-shutdown"], "PASS")
        for blocked in ("LC-port-disconnect", "LC-stdin-eof"):
            self.assertEqual(res[blocked], "BLOCKED")
        self.assertIn("UNPROVEN", by_id["W-07"]["actual"])
        self.assertEqual(by_id["W-07"]["status"], by_id["W-08"]["status"], "BLOCKED")
        first = json.loads((base / "windows-initial/windows-lifecycle/lifecycle.json").read_text())
        self.assertEqual(sum(r["status"] == "BLOCKED" for r in first["results"]), 4)

    def test_macos_permission_recovery_rows_match_the_controlled_retry_and_keep_the_failed_attempt(self):
        base = REPO / "native-bridge/feasibility/evidence/post-grant-2026-10-26"
        by_id = {r["id"]: r for r in BASE["rows"]}
        res = json.loads((base / "macos-final/recovery2-20261027/result.json").read_text())
        self.assertEqual((res["result"], res["same_state_recovery"], res["failures"]), ("PASS", True, []))
        run = res["run"]
        self.assertEqual(run["denied"]["scan_class"], "access_denied")
        self.assertEqual(run["recovered"]["scan_class"], "started")
        self.assertEqual({run[k]["chrome_pid"] for k in ("baseline", "denied", "recovered")}, {60558})
        self.assertEqual({run[k]["load_unpacked_calls"] for k in ("baseline", "denied", "recovered")}, {1})
        self.assertEqual(run["denied"]["ack"]["prompt"], "unknown")
        # the earlier timed-out attempt is preserved as a FAIL and nothing claims a prompt owner
        old = json.loads((base / "macos-initial/macos-permission-recovery.json").read_text())
        self.assertEqual(old["result"], "FAIL")
        for rid in ("M-05", "M-06"):
            self.assertTrue(by_id[rid]["operator"], rid)
            self.assertIn("not", by_id[rid]["actual"].lower())
            self.assertNotIn("TCC reset was used", by_id[rid]["actual"])
        self.assertIn("TIMED OUT", by_id["M-04"]["actual"])
        self.assertEqual(by_id["M-04"]["status"], "BLOCKED")

    def test_strict_background_w_windows_stay_blocked_and_m_mac_rows_pass_under_the_clarification(self):
        base = REPO / "native-bridge/feasibility/evidence/post-grant-2026-10-26"
        by_id = {r["id"]: r for r in BASE["rows"]}
        adjudications = [base / "phone-evidence/mac/otp23-macos-strict-cold-adjudicated.json",
                         base / "phone-evidence/mac/otp23-macos-strict-prewarm-adjudicated.json",
                         base / "windows-final/post-grant-2026-10-27/windows-strict-cold-foreground-2.json",
                         base / "windows-final/post-grant-2026-10-27/windows-strict-prewarm-foreground-2.json",
                         base / "strict-mac-20261027/strict-mac-20261027-cold.adjudicated-v2.json",
                         base / "strict-mac-20261027/strict-mac-20261027-prewarm.adjudicated-v2.json"]
        for path in adjudications:
            data = json.loads(path.read_text())
            self.assertFalse(data["formal_otp23_acceptance"], path.name)
            self.assertEqual(data["strict_result"], "PASS", path.name)
            self.assertTrue(all(c["status"] == "PASS" for c in data["checks"].values()), path.name)
        for path in adjudications[:4]:  # legacy-era files record the exclusion flag; the v2 files carry the clarified note
            data = json.loads(path.read_text())
            self.assertTrue(data["checks"]["ST-13"]["actual"]["native_messaging_exemption_not_excluded"], path.name)
        # the v2 adjudications carry the trusted-platform finalizer provenance and the clarified-criterion ST-13 note
        for kind in ("cold", "prewarm"):
            v2 = json.loads((base / f"strict-mac-20261027/strict-mac-20261027-{kind}.adjudicated-v2.json").read_text())
            self.assertIn("timer-throttling exemption", v2["checks"]["ST-13"]["actual"]["native_messaging_port"])
            self.assertTrue(v2["checks"]["ST-15"]["actual"]["after_switch"]["session"]["verified"])
            self.assertIn("dc5d127", json.dumps(v2["evaluator_provenance"]))
        # the failed earlier Windows attempts are retained next to the passing retries
        history = base / "windows-strict-history"
        for name in ("cold-first", "cold-second", "cold-foreground", "prewarm"):
            self.assertEqual(json.loads((history / f"otp23-windows-strict-{name}.json").read_text())["strict_result"], "FAIL", name)
        # raw Mac results stay BLOCKED; only the separate adjudications pass
        for kind in ("cold", "prewarm"):
            raw = json.loads((base / f"macos-final/post-grant-2026-10-26/macos-strict-{kind}-raw.json").read_text())
            self.assertEqual(raw["strict_result"], "BLOCKED", kind)
        # 2026-10-27: the criterion was clarified (owner-approved); W-11/W-12 were NOT converted by it
        for rid in ("W-11", "W-12"):
            self.assertEqual(by_id[rid]["status"], "BLOCKED", rid)
            blocker = by_id[rid]["blocker"]
            self.assertIn("clarified on 2026-10-27", blocker, rid)
            self.assertIn("not promoted", blocker, rid)
            self.assertIn("formal_otp23_acceptance=false", blocker, rid)
        # the promotion adjudicated the fresh macOS runs (v2) after an independent verification of the fixes
        for rid in ("M-09", "M-10"):
            self.assertEqual(by_id[rid]["status"], "PASS", rid)
            actual = by_id[rid]["actual"]
            self.assertIn("ST-01..ST-15 ALL PASS", actual, rid)
            self.assertIn("adjudicated-v2", actual, rid)
            self.assertIn("formal_otp23_acceptance=false", actual, rid)
            self.assertIn("timer-throttling exemption", actual, rid)
            self.assertIn("156.0.8078.48", actual, rid)
            self.assertIn("operator-bound", actual, rid)
            self.assertIn("linear drift", actual, rid)
            self.assertIn("otp23-mac-final-fix-verification-opus.md", actual, rid)
            for rel in by_id[rid]["evidence"]:
                self.assertTrue((REPO / rel).exists(), (rid, rel))
            for rel in by_id[rid]["evidence"]:
                if "strict-mac-20261027" in rel:
                    self.assertIn("post-grant-2026-10-26/", rel, rid)
        # the cold row does not claim the partial JUnit log and stays separate from prewarm
        self.assertIn("NO PASS is claimed from it", by_id["M-09"]["actual"])
        self.assertIn("never combined", by_id["M-10"]["actual"])
        self.assertEqual(m.decision(BASE)[0], "NO-GO")

    def test_m6_machine_fail_is_retained_beside_the_manual_adjudication(self):
        base = REPO / "native-bridge/feasibility/evidence/post-grant-2026-10-26/m6"
        row = {r["id"]: r for r in BASE["rows"]}["A-02"]
        machine = json.loads((base / "otp23-m6-desktop-adjudication.json").read_text())
        self.assertEqual((machine["verdict"], machine["m6_discharged"]), ("FAIL", False))
        phone = json.loads((base / "otp23-m6-phone-transport.json").read_text())
        self.assertTrue(phone["clean"] and phone["final"] and phone["final_after_service_stopped"] and not phone["truncated"])
        self.assertEqual(len(phone["peers"]), 2)
        opus = json.loads((base / "otp23-resume-opus-m6-adjudication.json").read_text())
        self.assertEqual(list(opus["modelUsage"]), ["claude-opus-5-5"])
        self.assertEqual(opus["session_id"], "97410d6b-c523-444a-86e8-f934f3da6b5c")
        self.assertEqual(opus["subagent_stats"]["spawned"], 0)
        self.assertEqual(row["status"], "PASS")
        for needle in ("MANUAL ADJUDICATION", "machine verdict is RETAINED", "RESIDUALS", "NOT an OTP-24 GO", "97410d6b-c523-444a-86e8-f934f3da6b5c"):
            self.assertIn(needle, row["actual"])

    def test_post_grant_identity_and_gate_counts_come_from_real_outputs(self):
        base = REPO / "native-bridge/feasibility/evidence/post-grant-2026-10-26"
        ident = json.loads((base / "post-grant-identity.json").read_text())
        sig = (base / "signing-and-gates/otp23-postgrant-android-signature.txt").read_text()
        self.assertIn(ident["android"]["signing_certificate_sha256"], sig)
        mac = (base / "signing-and-gates/otp23-postgrant-mac-signature.txt").read_text()
        for needle in ("Identifier=me.nagaev.veles.bridge", "TeamIdentifier=7T39M26Z99", ident["macos"]["host_sha256"]):
            self.assertIn(needle, mac)
        gates = (base / "signing-and-gates/otp23-3dbe3db-gates.log").read_text(errors="replace")
        self.assertIn("gradle_exit_code=0", gates)
        self.assertIn("BUILD SUCCESSFUL", gates)
        installed = (base / "signing-and-gates/otp23-3dbe3db-installed.txt").read_text()
        self.assertIn(ident["android"]["debug_apk_sha256"], installed)
        g = {r["id"]: r for r in BASE["rows"]}["G-01"]
        self.assertIn("334 JVM tests", g["actual"])
        self.assertIn("321 JVM tests", g["actual"])  # the old phase stays recorded
        self.assertEqual(ident["android"]["jvm_tests"]["count"], 334)
        junit = (base / "signing-and-gates" / ident["android"]["jvm_tests"]["summary_file"].split("/")[-1]).read_text()
        totals = re.search(r"TOTAL files=(\d+) tests=(\d+) failures=(\d+) errors=(\d+) skipped=(\d+)", junit)
        self.assertEqual(tuple(map(int, totals.groups())), (ident["android"]["jvm_tests"]["classes"], 334, 0, 0, 0))
        per_class = re.findall(r"^\S+ tests=(\d+) failures=0 errors=0 skipped=0$", junit, re.M)
        self.assertEqual((len(per_class), sum(map(int, per_class))), (42, 334))
        dex = (base / "signing-and-gates/otp23-3dbe3db-release-dex-inspection.txt").read_text()
        self.assertIn("apk_sha256 " + ident["android"]["release_apk_sha256_unsigned"] + " MATCHES_EXPECTED", dex)
        self.assertIn("total_hits 0", dex)
        self.assertIn("needle TransportTelemetry hits 0", dex)
        for name in ("final-gradle-gates", "final-log-guards", "final-python-tests"):
            self.assertTrue((base / f"signing-and-gates/otp23-postgrant-{name}.log").stat().st_size > 0)
        self.assertIn("BUILD SUCCESSFUL", (base / "signing-and-gates/otp23-postgrant-final-gradle-gates.log").read_text())

    def test_existing_account_windows_launch_is_a_narrow_non_waived_pass(self):
        row = {r["id"]: r for r in BASE["rows"]}["W-13"]
        self.assertEqual((row["status"], row["account"], row["waiver"]), ("PASS", "admin", ""))
        self.assertIn("fresh-windows-chrome.json", " ".join(row["evidence"]))
        self.assertIn("Proves nothing about phones, transfer or link cleanup", row["actual"])
        self.assertIn("W-01", m.waived(BASE))

    def test_w03_is_a_narrow_pass_and_the_earlier_single_phone_text_is_preserved(self):
        by_id = {r["id"]: r for r in BASE["rows"]}
        self.assertEqual(by_id["W-03"]["status"], "PASS")
        self.assertIn("one device", by_id["W-03"]["actual"])
        # the pre-grant single-phone record and the unproven PH-01 failure stay in the promoted rows
        for rid in ("W-04", "W-05", "W-06"):
            self.assertIn("single-phone", by_id[rid]["actual"], rid)
        self.assertIn("UNPROVEN", by_id["W-04"]["actual"])
        self.assertIn("grant", by_id["A-03"]["actual"].lower())

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
        passed = dict(status="PASS", account="admin", blocker="")
        self.assertTrue(m.validate(with_row("R-01", actual="", evidence=[], **passed)))
        self.assertTrue(m.validate(with_row("R-01", evidence=[], **passed)))
        if row["status"] != "PASS":
            # post-grant phase: the e19c8ca-scoped PASS is preserved but the NEW phase review is PENDING
            self.assertEqual(row["status"], "BLOCKED")
            self.assertIn("PENDING", row["actual"])
            self.assertIn("PENDING", row["blocker"])
            self.assertIn("e19c8ca", row["actual"])
            self.assertIn("f3746c7a-184d-4343-933a-12093f203b6a", row["actual"])
            self.assertIn("NOT the final review", row["actual"])
            self.assertIn("R-01", m.decision(BASE)[1])
            post = json.loads((REPO / "native-bridge/feasibility/evidence/post-grant-2026-10-26/post-grant-model-provenance.json").read_text())
            self.assertEqual(post["final_review"]["status"], "PENDING")
            # model names come from the raw CLI results, never from self-report
            for session in post["implementation_sessions_claude_code_cli"]:
                raw = json.loads((REPO / "native-bridge/feasibility/evidence/post-grant-2026-10-26" / session["raw"]).read_text())
                self.assertEqual((raw["session_id"], list(raw["modelUsage"])), (session["session_id"], ["claude-sonnet-5-5"]))
            for interim in post["opus_interim_reviews"]["raws"]:
                raw = json.loads((REPO / "native-bridge/feasibility/evidence/post-grant-2026-10-26" / interim["file"]).read_text())
                self.assertEqual((raw["session_id"], list(raw["modelUsage"])), (post["opus_interim_reviews"]["session_id"], ["claude-opus-5-5"]))
            # the older review evidence is still present
            self.assertTrue((REPO / "native-bridge/feasibility/evidence/fresh-2026-10-26/opus-final-ingress-verification.json").exists())
            return
        base = REPO / "native-bridge/feasibility/evidence/post-grant-2026-10-26"
        post = json.loads((base / "post-grant-model-provenance.json").read_text(encoding="utf-8"))["final_review"]
        if post["status"].startswith("PASS"):
            # CURRENT attestation: the PASS must rest on the NEW post-grant review, not on the preserved e19c8ca one
            commit = post["verified_commit"]
            self.assertRegex(commit, r"^[0-9a-f]{40}$")
            self.assertRegex(post["reviewed_snapshot_commit"], r"^[0-9a-f]{40}$")
            self.assertIn(commit, row["actual"])
            self.assertIn(commit, (base / "reviews-and-provenance/otp23-postgrant-r01-attestation.md").read_text())
            self.assertEqual((row["status"], row["blocker"], row["waiver"]), ("PASS", "", ""))
            self.assertEqual(post["provider"], "firstParty")
            self.assertEqual(post["observed_canonicalModel"], "claude-opus-5-5")
            self.assertEqual(post["acceptance_decision"], "NO-GO for OTP-24")
            self.assertEqual(sorted(post["remaining_minor_disposition"]), ["MINOR-A", "MINOR-B", "MINOR-C", "MINOR-D"])
            for key, name in (("raw_result", None), ("fix_verification", "reviews-and-provenance/otp23-postgrant-opus-fix-verification-result.json")):
                rel = post["raw_result"] if name is None else name
                data = (base / rel).read_bytes()
                raw = json.loads(data)
                digest = post["raw_result_sha256"] if name is None else post["fix_verification_raw_sha256"]
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest, key)
                self.assertEqual(list(raw["modelUsage"]), ["claude-opus-5-5"], key)
                self.assertEqual(raw["modelUsage"]["claude-opus-5-5"]["canonicalModel"], "claude-opus-5-5", key)
                self.assertEqual(raw["modelUsage"]["claude-opus-5-5"]["provider"], "firstParty", key)
                self.assertEqual(raw["session_id"], post["session_id"], key)
                self.assertEqual(raw["subagent_stats"]["spawned"], 0, key)
                self.assertFalse(raw["is_error"], key)
                self.assertIn("native-bridge/feasibility/evidence/post-grant-2026-10-26/" + rel, row["evidence"], key)
                self.assertIn(digest, row["actual"], key)
            self.assertIn(post["session_id"], row["actual"])
            attestation = json.loads((base / post["attestation"]).read_text())
            self.assertEqual((attestation["byte_preserved_commit"], attestation["decision"]), (commit, "NO-GO for OTP-24"))
            self.assertEqual(attestation["reviewed_snapshot_commit"], post["reviewed_snapshot_commit"])
            self.assertIn("did NOT itself re-open", attestation["binding"])  # Opus never reviewed the commit object itself
            self.assertIn("native-bridge/feasibility/evidence/post-grant-2026-10-26/" + post["attestation"], row["evidence"])
            # the e19c8ca review stays as preserved history and is not the sole proof for the new source
            self.assertIn("f3746c7a-184d-4343-933a-12093f203b6a", row["actual"])
            self.assertIn("preserved history", row["actual"])
            self.assertNotEqual(post["session_id"], "f3746c7a-184d-4343-933a-12093f203b6a")
            self.assertTrue((REPO / "native-bridge/feasibility/evidence/fresh-2026-10-26/opus-final-ingress-verification.json").exists())
            verdict, open_rows = m.decision(BASE)
            self.assertNotIn("R-01", open_rows)
            self.assertEqual(verdict, "NO-GO")
            self.assertTrue(open_rows)  # a review PASS never makes the decision GO
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
        self.assertTrue({"M-03", "W-11", "W-12", "M-11"} <= set(open_rows))
        # R-01 blocks exactly while its row is not PASS (the generic pending/false-PASS rules are tested separately)
        self.assertEqual("R-01" in open_rows, by_id["R-01"]["status"] != "PASS")

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
        for rid in ("A-01", "M-11"):
            self.assertEqual(by_id[rid]["status"], "BLOCKED", rid)
            self.assertTrue(by_id[rid]["blocker"], rid)

    def test_existing_account_unsigned_host_row_is_blocking_and_was_not_run(self):
        row = {r["id"]: r for r in BASE["rows"]}["M-11"]
        self.assertEqual((row["status"], row["waiver"], row["evidence"]), ("BLOCKED", "", []))
        self.assertTrue(row["blocker"])
        self.assertIn("M-11", m.decision(BASE)[1])
        for field in ("actual", "blocker"):
            self.assertIn("no unsigned dev-registered host was built or run", row[field])
            self.assertIn("signed me.nagaev.veles.bridge", row[field])
        self.assertIn("NOT waived", row["actual"])
        self.assertIn("no result exists", row["blocker"])

    def test_no_row_claims_the_owner_cannot_approve_bluetooth_after_the_grant(self):
        for row in BASE["rows"]:
            self.assertNotIn("cannot approve", row["blocker"], row["id"])
            self.assertNotIn("cannot be granted now", row["blocker"], row["id"])
            if row["id"] == "M-11":
                self.assertNotIn("cannot approve", row["actual"])
            # the only surviving mention is explicitly labelled as pre-grant history
            if "cannot approve" in row["actual"]:
                self.assertIn("PRE-GRANT", row["actual"], row["id"])

    def test_historical_mac_adjudication_provenance_is_stated_and_head_blocks_st15(self):
        by_id = {r["id"]: r for r in BASE["rows"]}
        base = REPO / "native-bridge/feasibility/evidence/post-grant-2026-10-26/phone-evidence/mac"
        for rid, name in (("M-09", "cold"), ("M-10", "prewarm")):
            row = by_id[rid]
            # the rows are PASS on the 2026-10-27 v2 adjudications, but the historical evaluator story stays in the text
            self.assertEqual(row["status"], "PASS")
            for needle in ("HISTORICAL EVALUATOR PROVENANCE", "OPERATOR-INFERRED", "BLOCKED", "ST-15",
                           "NOT reproducible at HEAD", "no legacy bypass"):
                self.assertIn(needle, row["actual"], (rid, needle))
            new = json.loads((base / f"reevaluation-at-head/otp23-macos-strict-{name}-reevaluated-at-head.json").read_text())
            old = json.loads((base / f"otp23-macos-strict-{name}-adjudicated.json").read_text())
            self.assertEqual((old["strict_result"], new["strict_result"]), ("PASS", "BLOCKED"))
            self.assertEqual(new["checks"]["ST-15"]["status"], "BLOCKED")
            self.assertEqual(new["inputs"]["raw"]["sha256"], old["inputs"]["raw"]["sha256"])
            self.assertEqual(len(new["evaluator_provenance"]["sha256"]), 3)
            self.assertNotIn("evaluator_provenance", old)  # the originals are untouched and carry none

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
        # nothing else is relaxed: signing/identity, allow/deny/recovery and the strict background windows stay open
        for rid in ("M-03", "M-04", "W-11", "W-12"):
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

    def test_macos_rows_pass_only_where_the_human_observation_and_signature_exist(self):
        rows = {r["id"]: r for r in BASE["rows"]}
        for rid in ("M-03", "M-04"):
            self.assertEqual(rows[rid]["status"], "BLOCKED", rid)
            self.assertTrue(rows[rid]["human_gated"], rid)
            self.assertTrue(rows[rid]["blocker"], rid)
        for rid in ("M-05", "M-06"):
            self.assertEqual((rows[rid]["status"], rows[rid]["human_gated"]), ("PASS", True), rid)
            self.assertTrue(rows[rid]["operator"].strip(), rid)
        self.assertIn("NOT Developer ID", rows["M-03"]["actual"])
        text = " ".join(rows[r]["requirement"] + rows[r]["expected"] for r in ("M-01", "M-02", "M-03", "M-04", "M-05", "M-06"))
        for needle in ("FRESH STANDARD", "prompt owner", "signed", "final identifier", "DENY", "ALLOW", "SAME extension state"):
            self.assertIn(needle.lower(), text.lower())

    def test_signing_provenance_amendment_is_narrow_and_not_a_waiver_or_a_go(self):
        rows = {r["id"]: r for r in BASE["rows"]}
        m03 = rows["M-03"]
        self.assertEqual(m03["status"], "BLOCKED")
        self.assertEqual(m03["waiver"], "")
        self.assertNotIn("M-03", m.waived(BASE))
        self.assertIn("2026-10-27", m03["source"])
        self.assertIn("operator-captured", m03["source"])
        # the blocker is the retained prompt-owner requirement; CI/Developer ID/notarization are OTP-25, not blockers
        self.assertIn("prompt owner", m03["blocker"])
        self.assertIn("OTP-25", m03["blocker"])
        self.assertNotIn("no Developer ID", m03["blocker"])
        self.assertIn("Apple Development", m03["expected"])
        for rid in ("M-04", "M-11", "W-11", "W-12"):
            self.assertEqual(rows[rid]["status"], "BLOCKED", rid)
        # the later strict-mac-20261027 adjudication promoted only M-09/M-10, under the clarified criterion
        for rid in ("M-09", "M-10"):
            self.assertEqual(rows[rid]["status"], "PASS", rid)
        self.assertEqual(m.decision(BASE)[0], "NO-GO")
        self.assertTrue(any("owner approval 2026-10-27" in key for key in BASE["sources"]))
        self.assertIn("Signing-provenance amendment", m.render(BASE))
        rfc = (m.ROOT.parent.parent / "docs" / "rfcs" / "2026-08-21-otp-external-devices.md").read_text(encoding="utf-8")
        self.assertIn("Amended on 2026-10-27 (owner-approved for OTP-23 only; **operator-captured**", rfc)
        self.assertIn("not a GitHub approval", rfc)
        flat = " ".join(rfc.split())  # robust against line reflow
        self.assertIn("does not approve OTP-24", flat)
        self.assertIn("e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c", flat)
        self.assertIn("25bc7fc25e48c4470d4c3765b6a5f9b719ff381d", flat)
        # background clarification: the EARLIER "require more evidence first" record is preserved, the LATER approval carries the exact text
        APPROVED = ("Event delivery through the architecture\u2019s Native Messaging port is not a timer-throttling exemption. "
                    "The criterion continues to forbid timer-throttling opt-outs, artificial keepalive, audio, WebRTC, exemption flags and DevTools clients during idle; "
                    "ordinary timers and the port itself are permitted. "
                    "If a same-build comparison shows the port tab escapes intensive timer throttling while the port-less control does not, report the criterion unsatisfied.")
        self.assertIn(APPROVED, flat)
        sources = BASE["sources"]
        earlier = [k for k in sources if k.startswith("owner decision 2026-10-27")]
        later = [k for k in sources if k.startswith("owner approval 2026-10-27") and "background clarification" in k]
        self.assertEqual((len(earlier), len(later)), (1, 1))
        self.assertIn("require more evidence first", sources[earlier[0]].lower())
        self.assertIn("EARLIER", sources[earlier[0]])
        self.assertIn(APPROVED, sources[later[0]])
        self.assertIn("source only", sources[later[0]])
        self.assertIn("ST-15 not waived", sources[later[0]])
        self.assertIn("OTP-24 is not approved", sources[later[0]])
        for needle in ("source inspection of Chromium 155.0.8059.85 and 156.0.8078.48", "Chrome 155 was inspected in source only", "no fresh 155 probe",
                       "macOS ST-15 is not waived", "freeze/discard requirements", "cold-idle and prewarmed runs", "does not promote any matrix row"):
            self.assertIn(needle, flat)
        self.assertIn("as clarified by the 2026-10-27 background-criterion revision below", flat)
        self.assertIn("Background-criterion clarification", m.render(BASE))

    def test_windows_port_closure_rows_state_the_non_adopted_identity_and_stay_blocked(self):
        rows = {r["id"]: r for r in BASE["rows"]}
        for rid in ("W-07", "W-08"):
            self.assertEqual(rows[rid]["status"], "BLOCKED", rid)
            blocker = rows[rid]["blocker"]
            self.assertIn("e36328d2", blocker, rid)
            self.assertIn("non-adopted", blocker, rid)
            self.assertIn("no automatic promotion", blocker, rid)
            self.assertIn("6cf72ed0", blocker, rid)
            # the stale "no Chrome-provenance proof" wording must not come back
            self.assertNotIn("no Chrome-provenance stdin EOF", blocker, rid)

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

    def test_m09_m10_promotion_scope_is_narrow_and_not_a_go_or_otp24_change(self):
        """The M-09/M-10 promotion rests on the fresh 2026-10-27 v2 evidence + the independent Opus
        verification, records the residuals in the rows, changes no other row and keeps NO-GO."""
        rows = {r["id"]: r for r in BASE["rows"]}
        # exactly the two rows moved; every previously BLOCKED row is still BLOCKED
        self.assertEqual(rows["M-09"]["status"], rows["M-10"]["status"], "PASS")
        self.assertIn("2026-10-27", rows["M-09"]["source"])
        self.assertIn("verification 2026-10-27", rows["M-09"]["source"])
        for rid in ("W-02", "W-07", "W-08", "W-10", "W-11", "W-12", "M-03", "M-04", "M-08", "A-01", "M-11"):
            self.assertEqual(rows[rid]["status"], "BLOCKED", rid)
        # the accounts are still identified truthfully and no waiver appeared
        self.assertEqual((rows["M-09"]["account"], rows["M-10"]["account"]), ("admin", "admin"))
        self.assertEqual(m.waived(BASE), ["W-01", "M-01", "M-02"])
        # the promotion source is recorded in the matrix sources and keeps the chronology intact
        key = [k for k in BASE["sources"] if k.startswith("verification 2026-10-27")]
        self.assertEqual(len(key), 1)
        self.assertIn("strict-mac-20261027", BASE["sources"][key[0]])
        self.assertIn("stays NO-GO", BASE["sources"][key[0]])
        self.assertEqual(m.decision(BASE)[0], "NO-GO")


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
