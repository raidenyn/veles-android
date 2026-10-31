"""Tests for the OTP-23 requirements matrix rules (validation, decision, rendering)."""

import copy
import hashlib
import json
import pathlib
import re
import sys
import unittest
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import matrix as m  # noqa: E402

REPO = HERE.parent.parent.parent
EVIDENCE = REPO / "native-bridge/feasibility/evidence/fresh-2026-10-26"
BASE = m.load()
# Physical cases are reviewed; the new code still needs its committed-tree/publication review.
OPEN_ROWS = ()


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
        # every hardware PASS is either one of the narrow pre-grant passes or backed by an actual
        # evidence base: the post-grant raw files, the 2026-10-28 package1 evidence or the frozen
        # 2026-10-29 Mac captures (M-08 retake, M-11 developer-host attempts)
        for row in BASE["rows"]:
            if row["group"] in ("windows", "macos", "android") and row["status"] == "PASS" and row["id"] not in ("W-13", "W-03"):
                bases = " ".join(row["evidence"])
                self.assertTrue(any(b in bases for b in ("post-grant-2026-10-26/", "package1-connected-2026-10-28/",
                                                          "mac-retake-2026-10-29/", "mac-m11-dev-2026-10-29/",
                                                          "mac-m03-prompt-2026-10-30/", "otp23-rest-2026-10-30/")), row["id"])
                self.assertTrue(row["account"] or row["group"] == "android", row["id"])
        # promotions that the permission-granted physical runs support, plus the 2026-10-28
        # integrations: W-11/W-12 (latest-Chrome strict runs + GPT review) and W-02/W-07/W-08
        # (the final integration after the independent chain; see the dedicated tests)
        for rid in ("W-04", "W-05", "W-06", "W-09", "M-05", "M-06", "M-07", "A-02", "A-03",
                     "W-11", "W-12", "W-02", "W-07", "W-08", "M-08", "M-11", "M-03", "M-04", "W-10", "A-01"):
            self.assertEqual(by_id[rid]["status"], "PASS", rid)
        # rows whose literal requirement the new evidence does not meet stay blocked, each with a reason
        for rid in OPEN_ROWS:
            self.assertEqual(by_id[rid]["status"], "BLOCKED", rid)
            self.assertTrue(by_id[rid]["blocker"], rid)
            self.assertIn(rid, open_rows)
        self.assertNotIn("W-11", open_rows)
        self.assertNotIn("W-12", open_rows)

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
        self.assertEqual(by_id["M-04"]["status"], "PASS")

    def test_strict_background_history_and_mac_rows_under_the_clarification(self):
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
        # W-11/W-12 were promoted on the clarified criterion by the latest-Chrome strict runs + the
        # GPT strict review (dedicated test below); W-02/W-07/W-08 were promoted later the same day
        # by the final integration (test_w02_w07_w08_final_integration_2026_10_28 below)

    def test_w11_w12_windows_strict_background_rows_pass_under_the_clarification(self):
        """W-11/W-12 PASS on the owner-approved 2026-10-27 background clarification via the
        latest-Chrome strict runs in the package1 connected evidence, reviewed independently by the
        GPT strict review; NO-GO stands (final integration state)."""
        by_id = {r["id"]: r for r in BASE["rows"]}
        base = REPO / "native-bridge/feasibility/evidence/package1-connected-2026-10-28"
        for rid, scenario in (("W-11", "strict-windows-cold-2.json"),
                              ("W-12", "strict-windows-prewarm.json")):
            row = by_id[rid]
            self.assertEqual(row["status"], "PASS", rid)
            self.assertEqual(row["blocker"], "", rid)
            self.assertEqual(row["account"], "admin", rid)
            self.assertIn(scenario, " ".join(row["evidence"]), rid)
            self.assertIn("LATEST-CHROME (2026-10-28", row["actual"], rid)
            self.assertIn("[account: admin]", row["actual"], rid)
            self.assertIn("gpt-5.6-sol", row["actual"], rid)
            self.assertNotIn("owner present clicking", row["actual"], rid)
        strict = json.loads((base / "strict-windows-cold-2.json").read_text())
        self.assertEqual(strict["strict_result"], "PASS")
        self.assertFalse(strict["formal_otp23_acceptance"])
        ready = json.loads((base / "strict-windows-cold-2.foreground-ready.json").read_text())
        self.assertEqual((ready["state"], ready["polls"]), ("confirmed", 1))
        review = base / "../package1-b5-v2-20261028/reports/otp23-windows-strict-gpt-review.md"
        self.assertTrue(review.is_file(), "the GPT strict review copy must exist")
        # the D-02 decision row records the integration history honestly and no GO/OTP-24 change;
        # the FINAL counts (not any interim/quota state) are the current record
        d02 = by_id["D-02"]
        self.assertIn("INTERIM INTEGRATION 2026-10-28", d02["actual"])
        self.assertIn("usage_limit_reached", d02["actual"])
        self.assertIn("FINAL INTEGRATION 2026-10-28", d02["actual"])
        self.assertIn("23 PASS / 6 BLOCKED / 3 waived", d02["actual"])

    def test_w02_w07_w08_final_integration_2026_10_28(self):
        """The 2026-10-28 final integration promotes W-02/W-07/W-08 on the owner-scoped W-02
        adjudication and the corrected v4 derivations, verified by the independent chain ending in
        the user-authorized Opus 5.5 focused final verification; final counts 23/6/3 with NO-GO
        exactly as recorded in the D-02 row."""
        by_id = {r["id"]: r for r in BASE["rows"]}
        evidence_root = REPO / "native-bridge/feasibility/evidence"
        for rid, artifact in (("W-02", "w02-adjudication.json"),
                              ("W-07", "phone-burst-association-v4.json"),
                              ("W-08", "tab-close-phone-bound-v4.json")):
            row = by_id[rid]
            self.assertEqual(row["status"], "PASS", rid)
            self.assertEqual(row["blocker"], "", rid)
            self.assertEqual(row["account"], "admin", rid)
            self.assertIn(artifact, " ".join(row["evidence"]), rid)
        # W-02's expected wording matches the authoritative RFC amendment (exact owner sentence)
        approved = ("The plain host owns no top-level window, visible or hidden, and no network "
                    "endpoint. Chrome’s default Windows launcher may own one never-visible console "
                    "window; any visible window or network endpoint owned by the host or its wrapper "
                    "fails.")
        self.assertIn(approved, by_id["W-02"]["expected"])
        self.assertIn(approved, (REPO / "docs/rfcs/2026-08-21-otp-external-devices.md").read_text(
            encoding="utf-8").replace("\n", " "))
        adjud = json.loads((evidence_root / "package1-b5-v4-20261028/w02-adjudication.json").read_text())
        self.assertEqual(adjud["adjudication"]["w02_outcome"].split()[0], "PASS")
        census = adjud["census_from_raw"]
        self.assertEqual((census["samples"], census["wrapper_hidden_total"]), (6, 6))
        assoc = json.loads((evidence_root / "package1-b5-v4-20261028/phone-burst-association-v4.json").read_text())
        self.assertEqual(assoc["verdict"]["status"], "PASS")
        self.assertEqual([assoc["verdict"]["phones"][s]["burst"]["gap_vs_host_exit_ms"]
                          for s in ("2be02e41", "864a3db0")], [2322, 2406])
        tabs = json.loads((evidence_root / "package1-b5-v4-20261028/tab-close-phone-bound-v4.json").read_text())
        self.assertEqual([tabs["phones"][ph]["gap_vs_host_exit_ms"] for ph in ("6T", "10Pro")], [2304.0, 2310.0])
        opus = evidence_root / "package1-integration-20261028/reports/otp23-p2-opus-v4-final.md"
        self.assertTrue(opus.is_file(), "the user-authorized Opus 5.5 final report must be published")
        # the D-02 final postscript states the exact counts and the honest remaining rows
        d02 = by_id["D-02"]["actual"]
        self.assertIn("FINAL INTEGRATION 2026-10-28", d02)
        self.assertIn("23 PASS / 6 BLOCKED / 3 waived", d02)
        self.assertIn("W-10, M-03, M-04, M-08 (owner-deferred), M-11, A-01", d02)
        self.assertIn("1b447229-ceaa-4cee-a5be-2181dfdd8838", d02)
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
        self.assertEqual(m.decision(BASE)[0], "GO")

    def test_mac_m08_m11_final_integration_2026_10_29(self):
        """The 2026-10-29 Mac integration promotes exactly M-08 and M-11 on frozen, checksum-pinned
        evidence after the independent Opus micro-review: 25/4/3, open W-10/M-03/M-04/A-01, NO-GO.
        Superseded history (the deferral, the hull and A1 results, the raw M-11 timeouts) is kept,
        and nothing about the original signed prompt (M-03/M-04) is inferred."""
        rows = {r["id"]: r for r in BASE["rows"]}
        evidence = REPO / "native-bridge/feasibility/evidence"
        counts = {s: sum(1 for r in BASE["rows"] if r["status"] == s) for s in m.STATUSES}
        self.assertEqual((counts["PASS"], counts["BLOCKED"], counts["NOT RUN"], counts["FAIL"]), (29, 0, 3, 0))
        self.assertEqual(m.decision(BASE), ("GO", list(OPEN_ROWS)))
        self.assertEqual(m.waived(BASE), ["W-01", "M-01", "M-02"])
        # frozen inputs are pinned; the active integration folder is verified but not pinned
        pinned = {"mac-retake-2026-10-29": "f952c123a1c74a5b1c811f4faa2453ed5f81cae49e39e3f974136798e4ea6455",
                  "mac-release-attributed-safe-2026-10-29": "c05174f458bb151039e0e944f70994f170ba8ed470f4d21317e7256a643b1f96",
                  "mac-release-attributed-2026-10-29": "dd05dac4b63f67336370a8a203d2dff47471e3fbd10eaee73a1692a54b763355",
                  "mac-release-attribution-source-history-2026-10-29": "2a550c1cf0add0ea863b216e78c45c7d3acf18f0ee666d729411690ece7a2854",
                  "mac-m11-dev-2026-10-29": "071d14f114497f76b7f8c60ceaf2a7c5922dd5ff3e17cbefb5e1cfd69098c49a",
                  "mac-tcc-supplement-2026-10-29": "a1e4d80b702753b6e63b7a583d02c01d07f22326b04cb6cf5b042fb8d5d2f42e",
                  "mac-integration-2026-10-29": None}
        for folder, pin in pinned.items():
            sums = (evidence / folder / "SHA256SUMS").read_bytes()
            if pin:
                self.assertEqual(hashlib.sha256(sums).hexdigest(), pin, folder)
            for line in sums.decode().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256((evidence / folder / name).read_bytes()).hexdigest(), digest, (folder, name))
        for rid in ("M-08", "M-11"):
            row = rows[rid]
            self.assertEqual((row["status"], row["blocker"], row["waiver"], row["account"]), ("PASS", "", "", "admin"), rid)
            self.assertIn("verification 2026-10-29", row["source"], rid)
            self.assertIn("2026-10-29 FINAL MAC INTEGRATION", row["actual"], rid)
            self.assertIn("e30e474d-65cc-44c2-99c7-d0195cfc4631", row["actual"], rid)
            for rel in row["evidence"]:
                self.assertTrue((REPO / rel).exists(), (rid, rel))
        # M-08: deferral history kept; only A2 adopted; the precision target is not claimed
        m08 = rows["M-08"]
        self.assertIn("Cannot restart Chrome now", m08["actual"])
        self.assertIn("NOT a waiver", m08["actual"])
        self.assertIn("156.0.8078.74", m08["actual"])
        for needle in ("235–282 ms", "NOT an RFC gate", "NOT claimed as met", "16-byte lower bound is NOT causal",
                       "PASS/FAIL/FAIL", "permission_pending", "neither PASS nor waived"):
            self.assertIn(needle, m08["actual"], needle)
        self.assertFalse(any("/mac-release-attributed-2026-10-29/" in e for e in m08["evidence"]), "A1 is never adopted")
        for case, hull in (("shutdown", "PASS"), ("port-disconnect", "FAIL"), ("tab-close", "FAIL")):
            a2 = json.loads((evidence / f"mac-release-attributed-safe-2026-10-29/{case}-1/release-bound-attributed.json").read_text())
            self.assertEqual((a2["verdict"], a2["m08_creditable"], a2["attribution_status"]), ("PASS", True, "unique_mapping"), case)
            frozen = json.loads((evidence / f"mac-retake-2026-10-29/{case}-1/release-bound.json").read_text())
            self.assertEqual(frozen["verdict"], hull, case)
            checks = json.loads((evidence / f"mac-retake-2026-10-29/{case}-1/acceptance-check.json").read_text())["checks"]
            self.assertEqual((len(checks), all(v is True for v in checks.values())), (19, True), case)
        # M-11: scoped manual PASS; the raw captures keep their timeouts and null answers
        m11 = rows["M-11"]
        self.assertTrue(m11["human_gated"])
        self.assertIn("gpt-6.1-sol", m11["operator"])
        self.assertIn("not an Opus operator", m11["operator"])
        for needle in ("a2bdc40c7d6119e01513ae3c3b9b71a287ad910d114ff172883043303050702d", "linker ad-hoc", "setup FAIL",
                       "720.9 s / 3600.3 s", "never bound", "not a during-run absence proof", "not traced",
                       "no discovery comparison", "controller-attested", "c401a87c", "PERSISTS",
                       "M-03 stays unobserved and M-04 unmet", "no universal log-retention claim"):
            self.assertIn(needle, m11["actual"], needle)
        for attempt, state in ((2, "pending_at_host_budget_not_denial"), (3, "authorized_started")):
            capture = json.loads((evidence / f"mac-m11-dev-2026-10-29/attempts/m11-attempt-{attempt}/m11-capture.json").read_text())
            self.assertIsNone(capture["human_prompt_answer"], attempt)
            self.assertEqual(capture["authorized_state_classified"],
                             {"state": state, "post_init_authorization": None, "owner_dialog_observed": None, "ended_by": "deadline"})
            self.assertTrue(capture["problems"], attempt)
        # the post-run TCC supplement: its only PROMPTING record is the developer subject's
        records = [json.loads(line) for line in
                   (evidence / "mac-tcc-supplement-2026-10-29/raw/scoped-tcc-history.ndjson").read_text().splitlines()]
        prompting = [r["eventMessage"] for r in records if r.get("eventMessage", "").startswith("AUTHREQ_PROMPTING")]
        self.assertEqual(len(prompting), 1)
        self.assertIn("Sub:{me.nagaev.veles.bridge.dev}", prompting[0])
        self.assertIn("msgID=95540.2", prompting[0])
        # Preserve the 2026-10-29 history; M-03 passed separately on the 2026-10-30 observation.
        for rid in ("M-03", "M-04"):
            self.assertEqual(rows[rid]["waiver"], "", rid)
            self.assertIn("2026-10-29 Mac integration (NO status change)", rows[rid]["actual"], rid)
        self.assertEqual(rows["M-04"]["status"], "PASS")
        self.assertIn("UNOBSERVED", rows["M-03"]["actual"])
        self.assertIn("no M-04 OFF→ON toggle was approved or performed", rows["M-04"]["actual"])
        # the decision and review rows record the new state without erasing the 2026-10-28 record
        d02 = rows["D-02"]["actual"]
        self.assertIn("23 PASS / 6 BLOCKED / 3 waived", d02)
        self.assertIn("FINAL MAC INTEGRATION 2026-10-29", d02)
        self.assertIn("25 PASS / 4 BLOCKED / 3 waived", d02)
        self.assertIn("W-10, M-03, M-04, A-01", d02)
        self.assertIn("OTP-24 is NOT authorized", d02)
        r01 = rows["R-01"]["actual"]
        self.assertIn("2026-10-29 MAC INTEGRATION PHASE (current phase; review PENDING)", r01)
        self.assertIn("do NOT cover the new code", r01)
        self.assertIn("faee6a0e-43c5-41d7-9bc4-a2cd3f3de09b", r01)
        self.assertIn("CHANGES REQUIRED \u2192 closed by the bounded F1\u2013F3 closure; APPROVED for commit/publication pending clean export and green CI", r01)
        self.assertIn("closed by option (a)", r01)
        self.assertIn("Mac M-08/M-11 integration", m.render(BASE))
        rfc = " ".join((REPO / "docs/rfcs/2026-08-21-otp-external-devices.md").read_text(encoding="utf-8").split())
        self.assertIn("Cannot restart Chrome now", rfc)
        self.assertIn("M-08 Mac deferral follow-up (2026-10-29", rfc)

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

    def test_m03_fresh_signed_prompt_evidence_and_narrow_disposition(self):
        rows = {r["id"]: r for r in BASE["rows"]}
        row = rows["M-03"]
        root = REPO / "native-bridge/feasibility/evidence/mac-m03-prompt-2026-10-30"
        for line in (root / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest, name)
        provenance = json.loads((root / "provenance.json").read_text())
        for item in provenance["copies"]:
            self.assertEqual(hashlib.sha256((root / item["published"]).read_bytes()).hexdigest(), item["sha256"])
        self.assertEqual(row["expected"], "Directly signed fixture with the final bundle identifier and a recorded stable signing identity "
                         "(OTP-23 minimum, amended 2026-10-27: the existing approved Apple Development-signed fixture; CI-provided and distribution signing are OTP-25); "
                         "prompt owner observed on the existing account. NOT waived. (Historical Apple Development me.nagaev.veles.otp23.poc is not proof of the production identity.)")
        self.assertEqual((row["status"], row["blocker"], row["waiver"], row["account"]), ("PASS", "", "", "admin"))
        self.assertIn("openai/gpt-6.1-sol", row["operator"])
        interrupted = json.loads((root / "interrupted/result.json").read_text())
        old_steps = {s["kind"]: s["value"] for s in interrupted["steps"]}
        self.assertEqual(interrupted["capture_status"], "ERROR")
        self.assertEqual(old_steps["initial_scan"]["stage"], "access_denied")
        self.assertEqual(old_steps["owner_answer"]["action"], "ABORT_INTERRUPTED_ATTEMPT")
        self.assertEqual(old_steps["owner_answer"]["observation"], "NOT_RECORDED")
        capture = json.loads((root / "restart1/result.json").read_text())
        steps = {s["kind"]: s["value"] for s in capture["steps"]}
        times = {s["kind"]: datetime.fromisoformat(s["utc"]) for s in capture["steps"]}
        expected_hash = "e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c"
        self.assertEqual(capture["binary_sha256_before"], expected_hash)
        self.assertEqual(capture["binary_sha256_after"], expected_hash)
        resets = [s["value"]["command"] for s in capture["steps"] if s["kind"] == "command" and "tccutil" in s["value"]["command"][0]]
        self.assertEqual(resets, [["/usr/bin/tccutil", "reset", "BluetoothAlways", "me.nagaev.veles.bridge"]])
        self.assertEqual(steps["initial_scan"]["stage"], "permission_pending")
        self.assertEqual(steps["owner_answer"]["verbatim_owner_report"], "ValesOtp23PoC. Clicked allowed")
        self.assertEqual(steps["owner_answer"]["reported_action"], "Allow")
        self.assertEqual(json.loads((root / "restart1/bundle-info.json").read_text())["CFBundleName"], "VelesOtp23PoC")
        self.assertLess((times["owner_answer"] - times["awaiting_owner"]).total_seconds(), 1800)
        self.assertGreater((times["owner_answer"] - times["awaiting_owner"]).total_seconds(), 0)
        hosts = steps["signed_host_processes"]
        self.assertEqual(len(hosts), 1)
        self.assertEqual((hosts[0]["pid"], hosts[0]["ppid"]), (15918, steps["chrome"]["pid"]))
        records = [json.loads(line) for line in (root / "restart1/signed-prompt-chain.ndjson").read_text().splitlines()]
        messages = [r.get("eventMessage", "") for r in records]
        prompt = [s for s in messages if s.startswith("AUTHREQ_PROMPTING")]
        self.assertEqual(len(prompt), 1)
        for token in ("msgID=15918.2", "me.nagaev.veles.bridge", "pid=15918", "kTCCServiceBluetoothAlways"):
            self.assertIn(token, prompt[0])
        result = [s for s in messages if s.startswith("AUTHREQ_RESULT")]
        self.assertEqual(len(result), 1)
        for token in ("msgID=15918.2", "authValue=2", "authReason=2", "error=(null)"):
            self.assertIn(token, result[0])
        self.assertEqual((steps["post_allow_scan"]["status"], steps["stop_scan"]["status"], steps["shutdown"]["type"]),
                         ("started", "stopped", "shutdown_complete"))
        self.assertEqual(steps["events"], [])
        trace = (root / "restart1/host.trace.log").read_text()
        self.assertNotIn("allowed_always", trace)
        self.assertEqual(set(re.findall(r"pid=(\d+)", trace)), {"15918"})
        verify = json.loads((root / "restart1/verify-grant.json").read_text())
        self.assertEqual((verify["status"], verify["binary_sha256"]), ("CAPTURED", expected_hash))
        self.assertGreater(datetime.fromisoformat(verify["started_utc"]), datetime.fromisoformat(capture["ended_utc"]))
        self.assertIn("authorization before adapter init: allowed_always", (root / "restart1/verify-grant.trace.log").read_text())
        metadata = json.loads((root / "reviewer-metadata.json").read_text())
        self.assertEqual((metadata["provider"], metadata["model"]), ("kimi-code-plan-global", "k3.1-pro"))
        self.assertIn("**APPROVED.**", (root / "kimi31-review.md").read_text())
        for rid in OPEN_ROWS:
            self.assertEqual(rows[rid]["status"], "BLOCKED", rid)
        self.assertIn("no TCC reset", rows["M-04"]["expected"])
        self.assertIn("26 PASS / 3 BLOCKED / 3 waived NOT RUN", rows["D-02"]["actual"])
        self.assertEqual(m.decision(BASE), ("GO", []))
        self.assertEqual(m.waived(BASE), ["W-01", "M-01", "M-02"])

    def test_remaining_physical_cases_have_real_evidence_and_keep_the_publication_hold(self):
        rows = {r["id"]: r for r in BASE["rows"]}
        root = REPO / "native-bridge/feasibility/evidence/otp23-rest-2026-10-30"
        for line in (root / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest, name)
        for rid in ("M-04", "A-01", "W-10"):
            self.assertEqual((rows[rid]["status"], rows[rid]["blocker"], rows[rid]["waiver"]), ("PASS", "", ""), rid)
            self.assertIn("2026-10-30 REMAINING-CASES INTEGRATION", rows[rid]["actual"], rid)
        load = lambda name: json.loads((root / name).read_text())
        mac = load("m04/result.json")
        self.assertEqual((mac["result"], mac["failures"], mac["guidance_required"]), ("PASS", [], True))
        run = mac["run"]
        for phase in ("denied", "recovered"):
            for key in ("chrome_pid", "profile", "sentinel_sha256", "extension_id", "binary_sha256", "signature"):
                self.assertEqual(run[phase][key], run["baseline"][key], (phase, key))
            self.assertEqual(run[phase]["load_unpacked_calls"], 1)
        self.assertEqual(run["denied"]["scan_reply"]["stage"], "access_denied")
        self.assertEqual(run["denied"]["scan_reply"]["recoverability"], "retryable")
        self.assertTrue(run["denied"]["permission_guidance"]["visible"])
        self.assertIn("System Settings -> Privacy & Security -> Bluetooth, enable VelesOtp23PoC", run["denied"]["permission_guidance"]["text"])
        self.assertEqual((run["recovered"]["permission_guidance"]["visible"], run["recovered"]["permission_guidance"]["text"]), (False, ""))
        self.assertTrue(load("m04/owner-on.json")["guidance_observed"])
        self.assertEqual(mac["prompt_observation"]["value"], "unknown")
        self.assertTrue(run["profile_removed"])
        m3 = load("windows-first/capture/a01-m3.json")
        ack = load("controller/a01-m3-phone-ack.json")
        self.assertEqual(m3["capture_status"], "PASS")
        self.assertEqual(m3["nonce"], ack["nonce"])
        self.assertIn("not a naturally repeated OS callback", ack["origin"])
        self.assertEqual(ack["before"]["peers"], ack["after"]["peers"])
        self.assertEqual(ack["before"]["connected_at_elapsed_ms"], ack["after"]["connected_at_elapsed_ms"])
        self.assertEqual([e["bytes"] for e in m3["exchanges"]], [16, 4096, 4096])
        self.assertEqual(len({e["connection_handle"] for e in m3["exchanges"]}), 1)
        for exchange in m3["exchanges"]:
            self.assertEqual(exchange["received_sha256"], exchange["expected_sha256"])
        self.assertNotEqual(m3["exchanges"][1]["input_sha256"], m3["exchanges"][2]["input_sha256"])
        self.assertTrue(m3["connected"]["platform_hint"].startswith("sha256:"))
        server = load("controller/a01-m3-server-final.json")
        self.assertEqual((len(server["records"]), len(server["injections"]), server["server_closed"]), (3, 1, True))
        client = load("controller/a01-m4-client-retake3.json")
        server = load("controller/a01-m4-server-retake3-final.json")
        self.assertEqual((client["status"], client["run_id"]), ("PASS", server["run_id"]))
        events = client["events"]
        cccd = [e for e in events if e["kind"] == "cccd"]
        mtu = next(e for e in events if e["kind"] == "mtu")
        self.assertEqual([e["code"] for e in cccd], [6, 0])
        self.assertEqual((mtu["code"], mtu["extra"]), (0, 517))
        self.assertLess(cccd[0]["elapsed_ms"], mtu["elapsed_ms"])
        self.assertLess(mtu["elapsed_ms"], cccd[1]["elapsed_ms"])
        self.assertEqual(len(server["records"]), 1)
        self.assertTrue(server["server_closed"])
        first_valid = next(i for i, state in enumerate(server["states"]) if state["ready_count"] == state["subscribed_count"] == 1)
        self.assertTrue(all(state["records"] == 0 for state in server["states"][:first_valid + 1]))
        self.assertTrue(any(state["records"] == 1 for state in server["states"][first_valid:]))
        for source in load("mac-phone-order-corroboration.json")["sources"]:
            self.assertEqual(hashlib.sha256((REPO / source["source"]).read_bytes()).hexdigest(), source["sha256"])
            self.assertEqual(source["run_id"], "mac-m08-shutdown-20261029T043432Z")
            for peer in source["peers"]:
                order = peer["events"]
                self.assertEqual([(e["kind"], e["code"]) for e in order], [("connected", None), ("mtu", 517), ("subscribed", None)])
        self.assertEqual(load("windows-first/capture/radio-off.json")["scan"]["stage"], "radio_off")
        self.assertEqual(load("windows-first/capture/radio-off.json")["scan"]["recoverability"], "retryable")
        self.assertEqual(load("windows-first/capture/adapter-absent.json")["scan"]["status"], "started", "failed setup remains uncredited")
        absent = load("windows-reboot/capture/adapter-absent-v2.json")["scan"]
        self.assertEqual((absent["stage"], absent["recoverability"]), ("no_adapter", "fatal"))
        held = load("windows-reboot/capture/access-held.json")
        self.assertEqual((held["connect_reply"]["stage"], held["connect_reply"]["recoverability"]), ("access_denied", "fatal"))
        control = load("windows-reboot/capture/exclusive-direct.json")
        self.assertEqual((control["open_status"], control["sharing_mode"], control["subscription_status"]), ("Success", "Exclusive", "Success"))
        utc = lambda value: datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00")))
        self.assertLess(utc(control["held_utc"]), utc(held["started_utc"]))
        self.assertLess(utc(held["finished_utc"]), utc(control["finished_utc"]))
        recovered = load("windows-reboot/capture/access-recovered.json")
        self.assertEqual(recovered["target_hint_sha256"], load("windows-reboot/capture/access-baseline-v2.json")["target_hint_sha256"])
        self.assertTrue(recovered["echo"]["byte_exact"])
        cleanup = load("windows-reboot/capture/final-reboot-cleanup.json")
        self.assertEqual((cleanup["own_processes_left"], cleanup["holder_process_left"], cleanup["own_tasks_left"]), ([], [], []))
        self.assertTrue(cleanup["registration_absent"])
        self.assertEqual((rows["R-01"]["status"], rows["R-01"]["waiver"]), ("PASS", ""))
        self.assertIn("54386fddc26c510af53e56b78c8ff52ec7161678", rows["R-01"]["actual"])
        self.assertIn("kimi-code-plan-global/k3.1-pro", rows["R-01"]["expected"])
        self.assertEqual(m.decision(BASE), ("GO", []))
        self.assertEqual(m.waived(BASE), ["W-01", "M-01", "M-02"])

    def test_final_committed_tree_review_attestation(self):
        root = REPO / "native-bridge/feasibility/evidence/final-review-2026-10-30"
        for line in (root / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest, name)
        metadata = json.loads((root / "reviewer-metadata.json").read_text())
        self.assertEqual((metadata["provider"], metadata["model"]), ("kimi-code-plan-global", "k3.1-pro"))
        self.assertEqual(metadata["reviewed_commit"], "54386fddc26c510af53e56b78c8ff52ec7161678")
        self.assertEqual(metadata["reviewed_tree"], "4eb098734ab86231e2d40f6d27384d871ea7bbb5")
        report = (root / "kimi31-final-commit-review.md").read_bytes()
        self.assertEqual(hashlib.sha256(report).hexdigest(), metadata["report_sha256"])
        self.assertIn(b"**Verdict: APPROVED**", report)
        self.assertEqual(m.decision(BASE), ("GO", []))

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
        if row["status"] != "PASS" and "2026-10-30" not in row["blocker"]:
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
            self.assertEqual(row["waiver"], "")
            if row["status"] == "PASS":
                self.assertEqual(row["blocker"], "")
            else:
                self.assertEqual(row["status"], "BLOCKED")
                self.assertIn("2026-10-30", row["blocker"])
                self.assertIn("PENDING", row["blocker"])
                self.assertIn("committed", row["blocker"])
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
            self.assertEqual("R-01" in open_rows, row["status"] != "PASS")
            self.assertEqual(verdict, "GO")
            self.assertFalse(open_rows)
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
        self.assertEqual(set(open_rows), set(OPEN_ROWS))
        for rid in ("W-02", "W-07", "W-08", "W-11", "W-12"):
            self.assertNotIn(rid, open_rows)
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
        self.assertEqual((by_id["A-01"]["status"], by_id["A-01"]["blocker"]), ("PASS", ""))
        self.assertIn("2026-10-30 REMAINING-CASES INTEGRATION", by_id["A-01"]["actual"])
        self.assertEqual(by_id["R-01"]["status"], "PASS")

    def test_existing_account_unsigned_host_row_keeps_its_not_run_history_beside_the_scoped_pass(self):
        """M-11 was never waived: it stayed BLOCKED until it was executed on the existing account and
        adjudicated (2026-10-29); the earlier not-executed record is preserved verbatim and labelled."""
        row = {r["id"]: r for r in BASE["rows"]}["M-11"]
        self.assertEqual((row["status"], row["waiver"], row["blocker"], row["account"]), ("PASS", "", "", "admin"))
        self.assertNotIn("M-11", m.decision(BASE)[1])
        history, _, current = row["actual"].partition(" | 2026-10-29 FINAL MAC INTEGRATION")
        self.assertTrue(history.startswith("PRE-INTEGRATION RECORD"))
        self.assertIn("no unsigned dev-registered host was built or run", history)
        self.assertIn("signed me.nagaev.veles.bridge", history)
        self.assertIn("NOT waived", history)
        self.assertTrue(current)

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
        self.assertIn("kimi-code-plan-global/k3.1-pro", by_id["R-01"]["expected"])

    def test_account_waiver_covers_exactly_the_new_account_rows_and_is_never_a_pass(self):
        self.assertEqual(m.waived(BASE), ["W-01", "M-01", "M-02"])
        for row in BASE["rows"]:
            if row["waiver"]:
                self.assertEqual(row["status"], "NOT RUN", row["id"])
                self.assertTrue(row["waiver"].startswith("User-approved acceptance scope revision"))
        open_ids = m.decision(BASE)[1]
        for rid in ("W-01", "M-01", "M-02"):
            self.assertNotIn(rid, open_ids)
        # nothing else is relaxed: signing/identity and the remaining open rows stay open;
        # W-11/W-12 and W-02/W-07/W-08 were promoted 2026-10-28 — never by this waiver
        for rid in OPEN_ROWS:
            self.assertIn(rid, open_ids)
        for rid in ("W-02", "W-07", "W-08", "W-11", "W-12"):
            self.assertNotIn(rid, open_ids)
            self.assertEqual(next(r for r in BASE["rows"] if r["id"] == rid)["waiver"], "")

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
        for rid in ("M-03", "M-04", "M-05", "M-06"):
            self.assertEqual((rows[rid]["status"], rows[rid]["human_gated"]), ("PASS", True), rid)
            self.assertTrue(rows[rid]["operator"].strip(), rid)
        self.assertIn("NOT Developer ID", rows["M-03"]["actual"])
        text = " ".join(rows[r]["requirement"] + rows[r]["expected"] for r in ("M-01", "M-02", "M-03", "M-04", "M-05", "M-06"))
        for needle in ("FRESH STANDARD", "prompt owner", "signed", "final identifier", "DENY", "ALLOW", "SAME extension state"):
            self.assertIn(needle.lower(), text.lower())

    def test_signing_provenance_amendment_is_narrow_and_not_a_waiver_or_a_go(self):
        rows = {r["id"]: r for r in BASE["rows"]}
        m03 = rows["M-03"]
        self.assertEqual(m03["status"], "PASS")
        self.assertEqual(m03["waiver"], "")
        self.assertNotIn("M-03", m.waived(BASE))
        self.assertIn("2026-10-27", m03["source"])
        self.assertIn("operator-captured", m03["source"])
        # The signing amendment alone promoted nothing; the later observed prompt discharged M-03.
        self.assertEqual(m03["blocker"], "")
        self.assertIn("2026-10-30 M-03 INTEGRATION", m03["actual"])
        self.assertIn("OTP-25", m03["actual"])
        self.assertNotIn("no Developer ID", m03["blocker"])
        self.assertIn("Apple Development", m03["expected"])
        self.assertEqual(rows["M-04"]["status"], "PASS")
        # M-11 (the unsigned dev-host comparison the amendment retained) passed only by its own
        # 2026-10-29 execution and manual adjudication, never by this amendment
        self.assertEqual(rows["M-11"]["status"], "PASS")
        self.assertIn("verification 2026-10-29", rows["M-11"]["source"])
        # the later strict-mac-20261027 adjudication promoted only M-09/M-10, under the clarified criterion
        for rid in ("M-09", "M-10"):
            self.assertEqual(rows[rid]["status"], "PASS", rid)
        self.assertEqual(m.decision(BASE)[0], "GO")
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

    def test_windows_port_closure_rows_state_the_non_adopted_identity_in_their_historical_record(self):
        rows = {r["id"]: r for r in BASE["rows"]}
        # the 2026-10-28 final integration promoted W-07/W-08 on the adopted-identity package1
        # evidence; the HISTORICAL non-adopted-identity record (e36328d2-era runs and W-07's
        # 6cf72ed0-era history) stays visible in the row actuals
        for rid in ("W-07", "W-08"):
            self.assertEqual(rows[rid]["status"], "PASS", rid)
            self.assertIn("e36328d2", rows[rid]["actual"], rid)
        self.assertIn("6cf72ed0", rows["W-07"]["actual"], "W-07 history retained")
        self.assertIn("8521de4d", rows["W-07"]["actual"], "W-07 adopted-identity evidence recorded")
        self.assertIn("stdin", rows["W-08"]["actual"], "W-08 EOF semantics recorded")

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
        # exactly the two rows moved; every row blocked at that promotion (2026-10-27) that has not
        # been separately integrated since remains BLOCKED — W-11/W-12 were later promoted by the
        # separate 2026-10-28 interim integration (test_w11_w12_interim_integration_...)
        self.assertEqual(rows["M-09"]["status"], rows["M-10"]["status"], "PASS")
        self.assertIn("2026-10-27", rows["M-09"]["source"])
        self.assertIn("verification 2026-10-27", rows["M-09"]["source"])
        for rid in OPEN_ROWS:
            self.assertEqual(rows[rid]["status"], "BLOCKED", rid)
        # the accounts are still identified truthfully and no waiver appeared
        # (W-02/W-07/W-08 were later promoted by the separate final integration)
        self.assertEqual((rows["M-09"]["account"], rows["M-10"]["account"]), ("admin", "admin"))
        self.assertEqual(m.waived(BASE), ["W-01", "M-01", "M-02"])
        # the promotion source is recorded in the matrix sources and keeps the chronology intact
        key = [k for k in BASE["sources"] if k.startswith("verification 2026-10-27")]
        self.assertEqual(len(key), 1)
        self.assertIn("strict-mac-20261027", BASE["sources"][key[0]])
        self.assertIn("stays NO-GO", BASE["sources"][key[0]])
        self.assertEqual(m.decision(BASE)[0], "GO")


class Rules(unittest.TestCase):
    def test_pass_requires_actual_and_evidence(self):
        self.assertTrue(m.validate(unrun("D-02", status="PASS", account="admin")))
        self.assertTrue(m.validate(unrun("D-02", status="PASS", actual="zero windows", account="admin")))
        self.assertEqual(m.validate(unrun("D-02", status="PASS", actual="zero windows", evidence=["evidence/x.json"], account="admin")), [])

    def test_human_gated_pass_needs_a_named_operator(self):
        changes = dict(status="PASS", actual="ok", evidence=["e"], blocker="", account="admin")
        self.assertTrue(m.validate(unrun("M-04", **changes)))
        self.assertEqual(m.validate(unrun("M-04", operator="a named person", **changes)), [])

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
