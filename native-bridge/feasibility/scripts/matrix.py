"""OTP-23 requirements matrix: validation, decision and rendering (stdlib only).

The matrix is the single place that adjudicates OTP-23 acceptance. Drivers (strict_background.py,
physical_validate.py, macos_poc.py, ...) produce evidence; they never declare acceptance. A row is
PASS only with an `actual` observation, at least one `evidence` reference and, for human-gated rows,
a named `operator`. Fresh rows start NOT RUN. The decision is NO-GO unless every row is PASS or is a `NOT RUN` row carrying the user-approved
account waiver (tests that need NEW accounts are non-blocking for OTP-23; never a PASS, never a claim of
fresh/standard-account evidence, and the waiver covers nothing else).

    python3 scripts/matrix.py check                 # validate the JSON and the rendered document
    python3 scripts/matrix.py render --write        # regenerate docs/otp-23-requirements-matrix.md
    python3 scripts/matrix.py decision              # prints GO / NO-GO and the non-PASS ids (exit 1 on NO-GO)
"""

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MATRIX = ROOT / "acceptance" / "otp23-matrix.json"
DOC = ROOT.parent.parent / "docs" / "otp-23-requirements-matrix.md"
STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT RUN")
REQUIRED = ("id", "group", "requirement", "source", "expected", "actual", "evidence", "status", "blocker", "human_gated", "operator", "waiver", "account")
ACCOUNTS = ("admin", "standard")
WAIVER_PREFIX = "User-approved acceptance scope revision"


def load(path=MATRIX):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def validate(matrix):
    problems, seen = [], set()
    if matrix.get("default_status") != "NOT RUN":
        problems.append("default_status must be 'NOT RUN'")
    for row in matrix.get("rows", []):
        rid = row.get("id", "<missing id>")
        missing = [k for k in REQUIRED if k not in row]
        if missing:
            problems.append(f"{rid}: missing fields {missing}")
            continue
        if rid in seen:
            problems.append(f"{rid}: duplicate id")
        seen.add(rid)
        status = row["status"]
        if status not in STATUSES:
            problems.append(f"{rid}: invalid status {status!r}")
        if status == "PASS":
            if not row["actual"].strip():
                problems.append(f"{rid}: PASS without an actual observation")
            if not row["evidence"]:
                problems.append(f"{rid}: PASS without evidence references")
            if row["human_gated"] and not row["operator"].strip():
                problems.append(f"{rid}: human-gated PASS needs a named operator")
        if status == "NOT RUN" and (row["actual"].strip() or row["evidence"]):
            problems.append(f"{rid}: NOT RUN rows carry no actual result or evidence")
        if status in ("BLOCKED", "FAIL") and not row["blocker"].strip():
            problems.append(f"{rid}: {status} needs a blocker/failure description")
        waiver = row["waiver"].strip()
        if waiver:
            if status != "NOT RUN":
                problems.append(f"{rid}: only a NOT RUN row may carry the account waiver")
            if not waiver.startswith(WAIVER_PREFIX):
                problems.append(f"{rid}: waiver must record the user-approved scope revision")
            if row["account"]:
                problems.append(f"{rid}: a waived row has no account (nothing was executed)")
        if status == "PASS" and row["group"] in ("windows", "macos") and row["account"] not in ACCOUNTS:
            problems.append(f"{rid}: PASS must truthfully identify the account as admin or standard")
        if row["account"] and row["account"] not in ACCOUNTS:
            problems.append(f"{rid}: account must be 'admin' or 'standard'")
        if status == "BLOCKED" and row["human_gated"] and row["operator"].strip():
            problems.append(f"{rid}: a human-gated row with a named operator is not blocked on access")
    return problems


def decision(matrix):
    open_rows = [r["id"] for r in matrix["rows"]
                 if r["status"] != "PASS" and not (r["status"] == "NOT RUN" and r["waiver"].strip())]
    return ("GO" if not open_rows else "NO-GO"), open_rows


def waived(matrix):
    return [r["id"] for r in matrix["rows"] if r["waiver"].strip() and r["status"] == "NOT RUN"]


def render(matrix):
    verdict, open_rows = decision(matrix)
    counts = {s: sum(1 for r in matrix["rows"] if r["status"] == s) for s in STATUSES}
    lines = [
        "# OTP-23 requirements matrix",
        "",
        "<!-- Generated from native-bridge/feasibility/acceptance/otp23-matrix.json by scripts/matrix.py render --write. -->",
        "",
        f"**Decision: {verdict}** — " + ", ".join(f"{counts[s]} {s}" for s in STATUSES)
        + (". Every row must be PASS (or an approved account-waived NOT RUN) before OTP-24 may start." if open_rows else "."),
        "",
        f"**User-approved account waiver** (2026-10-26): tests that require NEW accounts are skipped and non-blocking for the OTP-23 gate: {', '.join(waived(matrix)) or 'none'}. "
        "They stay `NOT RUN`, are not PASS, and prove nothing about fresh or standard accounts. The waiver changes no other requirement (signing provenance, final identifier, allow/deny/recovery, architecture, strict background). "
        "Rows that do run record the account truthfully as `admin` or `standard`.",
        "",
        "**Signing-provenance amendment** (2026-10-27, owner-approved and operator-captured, OTP-23 only; not a GitHub approval): the existing approved Apple Development-signed "
        "final-identifier fixture is the minimum signing provenance; CI-provided and distribution signing (Developer ID, notarization, stapling) are deferred to OTP-25. "
        "The prompt owner, allow/deny/recovery, the unsigned dev-host comparison (M-11) and every background gate are unchanged; this approves neither OTP-24 nor any waiver.",
        "",
        "**Background-criterion clarification** (2026-10-27, owner-approved and operator-captured, OTP-23 only; later than the owner's \"require more evidence first\" answer): the Native Messaging port itself is not a "
        "timer-throttling exemption, and the criterion still forbids opt-outs, keepalive, audio, WebRTC, exemption flags and DevTools clients during idle. Cold/prewarm runs, foreground evidence (macOS ST-15 is not waived), "
        "freeze/discard requirements and the NO-GO rule remain; the approval promoted nothing by itself - M-09 (cold) and M-10 (prewarm) were separately adjudicated PASS on the fresh 2026-10-27 strict-mac-20261027 evidence "
        "(v2 adjudications with recorded evaluator provenance, after-run provenance capture and the independent Opus fix verification; residuals recorded in the rows); W-11/W-12 were promoted on this "
        "clarification by the 2026-10-28 latest-Chrome (156.0.8078.73) package1 strict runs with the independent GPT strict review, and W-02/W-07/W-08 were promoted the same day by the FINAL integration "
        "after the independent review chain (GPT P2/B5 + fix verification + v3-final recheck, then the user-authorized Opus 5.5 focused final verification) — final counts 23 PASS / 6 BLOCKED / 3 waived as of 2026-10-28 (superseded by the 2026-10-29 Mac integration below), "
        "still NO-GO, and OTP-24 is not approved.",
        "",
        "**Mac M-08/M-11 integration** (2026-10-29, after the independent Opus 5.5 micro-review; owner records operator-captured, not GitHub approvals): M-08 was promoted on the frozen "
        "mac-retake-2026-10-29 evidence and the adopted A2 safe phone-release attribution after the owner completed the deferred Chrome update (that completion resumes, and does not waive, "
        "the 2026-10-28 deferral; the earlier <=250 ms figure was an instrument precision target, not a gate, and is not claimed); M-11 is a scoped manual PASS on the existing admin account "
        "with its recorded caveats (late, never-accepted owner answers; no host trace; no discovery comparison). M-03 (original signed prompt owner unobserved) and M-04 (no OFF/ON toggle "
        "approved) stayed BLOCKED at that integration; no new waiver, no gate change, still NO-GO, and OTP-24 is not approved. The M-03 status is superseded by the follow-up below.",
        "",
        "**Signed-host prompt follow-up** (2026-10-30): M-03 ONLY is now PASS after the owner-approved app-specific Bluetooth reset and fresh signed-host prompt observation on the existing admin account. "
        "The original historical prompt remains unknown; the unchanged signed host is the subject in the new TCC PROMPTING/RESULT chain. The exact owner spelling, first interrupted ERROR and initial permission_pending are preserved. "
        "Explicit allowed_always was captured only on a subsequent fresh launch. Parent openai/gpt-6.1-sol operated; independent kimi-code-plan-global/k3.1-pro approved M-03 and bounded integration. "
        "At that follow-up M-04 guidance/recovery, W-10 and A-01 remained BLOCKED (26 PASS / 3 BLOCKED / 3 waived NOT RUN). This dated state is superseded by the remaining-cases integration below; no expected text or waiver changed.",
        "",
        "**Remaining physical cases** (later 2026-10-30): M-04, A-01 and W-10 are PASS on independently reviewed controlled evidence. M-04 displayed exact guidance and recovered without a reset in the same extension state; "
        "A-01 uses explicitly labelled real-link callback injection, actual CCCD-before-MTU radio traffic and direct phone-side Mac MTU ordering; W-10 deliberately controlled radio-off, no available adapter and exclusive-service OS access denial (not privacy-policy denial). "
        "Kimi K3.1 Pro independently approved evidence and code after the stale-guidance fix. R-01 is reopened BLOCKED: new code is uncommitted/unpublished, and earlier Opus committed-tree reviews do not cover it. "
        "At that integration R-01 remained BLOCKED (28 PASS / 1 BLOCKED / 3 waived NOT RUN); that dated state is superseded by the final review below.",
        "",
        "**Final committed-tree review** (2026-10-30): owner-selected Kimi K3.1 Pro independently APPROVED committed integration 54386fddc26c510af53e56b78c8ff52ec7161678, tree 4eb098734ab86231e2d40f6d27384d871ea7bbb5. R-01 is PASS on that reviewed commit; this subsequent metadata-only attestation is not claimed to have been reviewed as a commit object. Current 29 PASS / 0 BLOCKED / 3 waived NOT RUN; GO for feasibility eligibility, not automatic authorization to implement OTP-24.",
        "",
        "Fresh rows start `NOT RUN`. Historical PoC results are context in the narrative documents, never a PASS here:",
        "they were taken with different binaries, an administrator account and (for background) a connected browser-level",
        "DevTools client. Human-gated rows stay `BLOCKED` until a person with the account/GUI access records the result.",
        "",
        "| ID | Requirement | Expected | Actual | Evidence | Status |",
        "|---|---|---|---|---|---|",
    ]
    for row in matrix["rows"]:
        evidence = "; ".join(row["evidence"]) or "—"
        actual = row["actual"] or "—"
        if row["waiver"].strip():
            actual = "(waived, not run) " + row["waiver"]
        if row["account"]:
            actual += f" [account: {row['account']}]"
        if row["status"] in ("BLOCKED", "FAIL"):
            actual = (actual + " " if row["actual"] else "") + f"({row['blocker']})"
        cells = [row["id"], row["requirement"], row["expected"], actual, evidence, f"**{row['status']}**"]
        lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    lines += ["", "## Sources", ""]
    for source, text in matrix.get("sources", {}).items():
        lines.append(f"- **{source}** — {text}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["check", "render", "decision"])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--matrix", default=str(MATRIX))
    parser.add_argument("--doc", default=str(DOC))
    args = parser.parse_args(argv)
    matrix = load(args.matrix)
    problems = validate(matrix)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2
    if args.command == "decision":
        verdict, open_rows = decision(matrix)
        print(f"{verdict}: {len(open_rows)} row(s) open: {', '.join(open_rows)}" if open_rows else "GO")
        print(f"approved account-waived (NOT RUN, not PASS): {', '.join(waived(matrix)) or 'none'}")
        return 0 if verdict == "GO" else 1
    text = render(matrix)
    doc = pathlib.Path(args.doc)
    if args.command == "render" and args.write:
        doc.write_text(text, encoding="utf-8")
        return 0
    if not doc.exists() or doc.read_text(encoding="utf-8") != text:
        print(f"{doc} is stale; run scripts/matrix.py render --write", file=sys.stderr)
        return 3
    print(f"matrix ok: {decision(matrix)[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
