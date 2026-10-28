"""OTP-23 package1 B5 v2/v3/v4 derivation (source-only repair of the B5 defects; no raw rewrite).

Derives the three corrected artifacts plus the owner W-02 adjudication from the UNCHANGED raw inputs,
and records the full provenance (the orchestration script's own measured sha, code bytes, clock
contract, every input, outputs):

  python3 scripts/package1_b5_v2_derive.py --out evidence/package1-b5-v4-20261028 \\
      --connected evidence/package1-connected-2026-10-28 --p2 evidence/package1-p2-20261028

The script measures its OWN sha256 at runtime (`sha256(__file__)`) and pins it with the derivation/
orchestration role into the derivation record and EVERY output's provenance code list — never a
hardcoded self-hash (which could not survive its own edits).

Fails (exit 2/3) when any input fails its pinned SHA256SUMS manifest check, when a conservative
assertion does not hold, or when a derived value differs from the independently hand-computed
values. Old derived JSONs are never rewritten: each artifact carries its `supersedes` pointer chain.
"""

import argparse
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scenario_matrix_core as core  # noqa: E402
import package1_phone_bound as pb  # noqa: E402
import package1_phone_burst_telemetry as pbt  # noqa: E402

THIS_SCRIPT = pathlib.Path(__file__).resolve()
THIS_SCRIPT_SHA256 = hashlib.sha256(THIS_SCRIPT.read_bytes()).hexdigest()
THIS_SCRIPT_RELATIVE = "native-bridge/feasibility/scripts/" + THIS_SCRIPT.name

EXPECTED_SERIALS = ("2be02e41", "864a3db0")
PHONE_LABELS = {"2be02e41": "6T", "864a3db0": "10Pro"}
FIXTURE_PACKAGE = "me.nagaev.veles.otp23"
P2_RUN_ID = "p2pd1-portdisconnect-20261028T0651Z"

STOP_MARKERS = {"2be02e41", "864a3db0"}

# The parent-review record this v4 derivation answers (independently measured citation, like the
# owner-record constants below)
FIX_VERIFICATION_SHA256 = "217e6f2f2bcd896a7cda0bb8b6cf484ae3da7151f98f29ca6fb399bf2d40d4d2"
FIX_VERIFICATION_PATH = "/tmp/opencode/otp23-package1-p2-gpt-v3-final.md"

V2_FOLDER = "package1-b5-v2-20261028"
V3_FOLDER = "package1-b5-v3-20261028"


def superseded_chain(out, v3_name, v2_name):
    """Pointer chain to the two preserved candidate derivations (v3 immediate predecessor, v2) that
    this v4 artifact supersedes; both folders keep their own checksummed manifests."""
    entries = []
    for folder, name in ((V3_FOLDER, v3_name), (V2_FOLDER, v2_name)):
        previous = out.parent / folder / name
        entries.append({"path": f"native-bridge/feasibility/evidence/{folder}/{name}",
                        "sha256": sha256(previous) if previous.exists() else None,
                        "note": "preserved checksummed candidate kept immutable; superseded by this "
                                "v4 artifact from the same unchanged raws with identical derived "
                                "values (exact-branch finite-number predicate + controlled "
                                "numeric_range boundary)"})
    return entries

REVIEWED_ANALYZERS = (
    (THIS_SCRIPT_RELATIVE, THIS_SCRIPT_SHA256,
     "derivation program (orchestration): parses stop markers, assembles phone documents, applies the "
     "conservative/review-value assertions, computes the W-02 census, copies the owner records and "
     "serializes these outputs — pinned by its own measured sha"),
    ("scenario_matrix_core.py", None, "core helper: phone-to-PC conversion (_telemetry_bursts subtracts "
                                "offset_phone_minus_pc_ms_median), phone_burst_association guards "
                                "(finite-number predicate), "
                                "trace_origin_calibration, parse_trace"),
    ("package1_phone_burst_telemetry.py", None, "analyzer: trace exit/anchor derivation + document assembly "
                                           "(phone-burst-association artifacts)"),
    ("package1_phone_bound.py", None, "analyzer: old dumpsys-history bound (row_epoch_ms subtracts the "
                                "phone-minus-PC offset) for tab-close-phone-bound artifacts"),
    ("scenario_matrix.py", None, "driver: connected-phase sample recording (wall_ms) for "
                           "connected-sample-brackets artifacts"),
    ("pc_phone_clock_probe.py", None, "clock producer: measures and defines offset_phone_minus_pc_ms_median "
                                "= phone_epoch - pc_epoch"),
    ("chrome_validate.py", None, "Windows platform observer used by the sampled census (windows/endpoints)"),
)


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def manifest_map(path):
    entries = {}
    for line in pathlib.Path(path).read_text(encoding="utf-8", errors="strict").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        entries[name.removeprefix("./").strip()] = digest.strip()
    return entries


def pin_inputs(root, manifest_name, relative_paths, record):
    manifest = manifest_map(root / manifest_name)
    entries = []
    for rel in relative_paths:
        digest = sha256(root / rel)
        if rel in manifest:
            if manifest[rel] != digest:
                raise SystemExit(f"INPUT MANIFEST MISMATCH {root / rel}: manifest {manifest[rel]} "
                                 f"!= measured {digest}")
            verified = "SHA256SUMS"
        else:
            record.setdefault("inputs_missing_from_manifest", []).append(f"{root.name}/{rel}")
            verified = "measured-now (file not listed in the source manifest)"
        entries.append({"path": f"native-bridge/feasibility/evidence/{root.name}/{rel}",
                        "sha256": digest, "verified": verified})
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    here = pathlib.Path(__file__).resolve().parent.parent
    parser.add_argument("--out", default=here / "evidence" / "package1-b5-v4-20261028")
    parser.add_argument("--connected", default=here / "evidence" / "package1-connected-2026-10-28")
    parser.add_argument("--p2", default=here / "evidence" / "package1-p2-20261028")
    args = parser.parse_args()
    out = pathlib.Path(args.out)
    connected, p2 = pathlib.Path(args.connected), pathlib.Path(args.p2)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {"derived_utc": now,
              "package_version": "v4 (supersedes the preserved v3 and v2 candidates from the same "
                                 "unchanged raws with identical values; implements the EXACT "
                                 "finite-number branch contract — non-boolean ints are accepted "
                                 "without float conversion, math.isfinite applies ONLY to floats — "
                                 "and adds the controlled numeric_range FAIL boundary for later "
                                 "arithmetic overflow)",
              "derivation_script": {"path": THIS_SCRIPT_RELATIVE,
                                    "sha256": THIS_SCRIPT_SHA256,
                                    "role": "derivation program (orchestration) — pinned in the "
                                            "`code` list with every output"}}
    out.mkdir(parents=True, exist_ok=True)

    base = pathlib.Path(__file__).resolve().parent
    require(sha256(THIS_SCRIPT) == THIS_SCRIPT_SHA256,
            "the derivation program's own bytes changed during execution")
    code = []
    for name, pinned, role in REVIEWED_ANALYZERS:
        measured = sha256(base / pathlib.Path(name).name)
        if pinned is not None and pinned != measured:
            raise SystemExit(f"SELF-PIN DRIFT: {name} recorded {pinned[:12]}… but measures "
                             f"{measured[:12]}… (the pinned entry IS the running program)")
        entry = {"path": name if name.startswith("native-bridge/") else
                 f"native-bridge/feasibility/scripts/{name}",
                 "sha256": measured, "role": role}
        if pinned is not None:
            entry["pin_source"] = "runtime measured sha256 of this program's own file"
        code.append(entry)
    record["code"] = code
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=here.parent.parent, check=True,
                          capture_output=True, text=True).stdout.strip()
    record["git_head"] = {
        "commit": head,
        "worktree_uncommitted_changes": True,
        "note": "the per-file sha256 list under `code` pins the ACTUAL analyzer/helper/clock bytes "
                "used by this derivation; the p2-stage-manifest.txt of the run pins older staged "
                "bytes (scenario_matrix_core.py=b33b015b…, test_scenario_matrix.py=2183b847…) and "
                "does not list package1_phone_burst_telemetry.py, so that manifest is retained as "
                "HISTORICAL evidence only. The authority for what the run actually executed is the "
                "run's own self-recorded source_sha256 in p2-pd1/lifecycle.json "
                "(scenario_matrix.py=f8284c98…, scenario_matrix_core.py=527de610…, "
                "chrome_validate.py=78d53ca9…), which match these reviewed files."}
    record["stage_manifest_historical"] = {
        "path": f"native-bridge/feasibility/evidence/{p2.name}/p2-stage-manifest.txt",
        "sha256": sha256(p2 / "p2-stage-manifest.txt"),
        "status": "retained immutable as history; NOT the authority for v2 provenance"}
    record["runtime_self_pins"] = json.loads(
        (p2 / "p2-pd1" / "lifecycle.json").read_text(encoding="utf-8"))["meta"]["source_sha256"]
    record["runtime_self_pins_source"] = {
        "path": f"native-bridge/feasibility/evidence/{p2.name}/p2-pd1/lifecycle.json",
        "sha256": sha256(p2 / "p2-pd1" / "lifecycle.json")}

    # ---- artifact #1: phone-burst-association-v3 (B5-1/B5-2: corrected sign + fail-closed guards) ----
    inputs1 = pin_inputs(p2, "SHA256SUMS", [
        "p2-pd1/port-disconnect.host-trace.log", "p2-pd1/transport-2be02e41.json",
        "p2-pd1/transport-864a3db0.json", "clock-offset-p2.json", "phones/stop-markers.csv",
        "p2-pd1/lifecycle.json"], record)
    exit_ms, anchor_ms = pbt.host_exit_epoch_ms(p2 / "p2-pd1" / "port-disconnect.host-trace.log")
    require(exit_ms is not None and anchor_ms is not None,
            "the P2 host trace lacks the exit/disconnect epoch anchors")
    probes = {p["serial"]: p for p in
              json.loads((p2 / "clock-offset-p2.json").read_text(encoding="utf-8"))["probes"]}
    stop_map = {}
    for line in (p2 / "phones" / "stop-markers.csv").read_text(encoding="utf-8").splitlines():
        fields = dict(item.split("=", 1) for item in line.split(",") if "=" in item)
        serial = fields.get("serial")
        if serial in STOP_MARKERS:
            stop_map[serial] = {"epoch_phone_ms": int(fields["marker_before_phone_ms"]),
                                "epoch_pc_ms": int(fields["pc_ms_at_stop"])}
    phone_docs = []
    for serial in EXPECTED_SERIALS:
        telemetry = json.loads((p2 / "p2-pd1" / f"transport-{serial}.json").read_text(encoding="utf-8"))
        phone_docs.append({"serial": serial, "telemetry": telemetry, "clock": probes[serial],
                           "fixture_stop": stop_map[serial]})
    verdict = core.phone_burst_association(phone_docs, exit_ms, anchor_ms, EXPECTED_SERIALS,
                                           run_id=P2_RUN_ID, source_package=FIXTURE_PACKAGE)
    require(verdict["status"] == "PASS", f"v3 association verdict must PASS: {verdict['problems']}")
    expected_gaps = {"2be02e41": 2322, "864a3db0": 2406}
    expected_intervals = {"2be02e41": [629, 4015], "864a3db0": [1482, 3330]}
    conservative = {}
    for serial in EXPECTED_SERIALS:
        burst = verdict["phones"][serial]["burst"]
        require(burst["gap_vs_host_exit_ms"] == expected_gaps[serial],
                f"{serial} gap {burst['gap_vs_host_exit_ms']} != hand-derived {expected_gaps[serial]}")
        require(burst["gap_uncertainty_interval_ms"] == expected_intervals[serial],
                f"{serial} interval != hand-derived {expected_intervals[serial]}")
        low, high = burst["gap_uncertainty_interval_ms"]
        require(0 < low < high < verdict["bound_s"] * 1000,
                f"{serial}: the full interval must lie after the own exit and below the bound")
        conservative[serial] = {"gap_lower_ms": low, "gap_upper_ms": high,
                                "wholly_after_own_exit": True, "upper_below_bound": True}
    association_v4 = {
        "what": "OTP-23 package1 B-5 phone burst association v4 (corrected phone-minus-PC subtraction "
                "+ fail-closed completeness guards under the EXACT finite-number branch contract "
                "with the controlled numeric_range boundary); derives ONLY from the immutable raw "
                "inputs",
        "run_id": P2_RUN_ID,
        "bound_s_predeclared": core.PACKAGE1_BOUND_S,
        "fixture_stop_required": "disconnect strictly before the stop marker; stop >= bound + clock "
                                 "uncertainty after the host's own exit (fixture-stop confound guard)",
        "host_exit_epoch_ms_estimate": exit_ms,
        "anchor_disconnect_epoch_ms": anchor_ms,
        "stop_markers": stop_map,
        "conservative_check": {"rule": "the whole gap uncertainty interval must lie after the host's "
                                       "own exit (lower > 0) and below the pre-declared 30 s bound "
                                       "(upper < bound)", "phones": conservative},
        "verdict": verdict,
        "supersedes": superseded_chain(out, "phone-burst-association-v3.json",
                                       "phone-burst-association-v2.json"),
        "also_supersedes": {"path": f"native-bridge/feasibility/evidence/{p2.name}/p2-pd1/"
                                    f"phone-burst-association.json",
                            "sha256": sha256(p2 / "p2-pd1" / "phone-burst-association.json"),
                            "note": "retained immutable; its phone->PC conversion used the same "
                                    "phone-minus-PC offset field with a + addition, over-estimating "
                                    "the PC release times (gaps +3764 ms / +2790 ms). Historical "
                                    "record; not the published analysis. The v2 candidate corrected "
                                    "the same values; v3 preserves those numbers unchanged."},
        "provenance": provenance_block(inputs1, code, record)}

    # ---- artifact #2: tab-close-phone-bound-v3 (B5-1 old-analyzer sign repair on the old W08 run) ----
    inputs2 = pin_inputs(connected, "SHA256SUMS", [
        "run-tab-close-2/tab-close.host-trace.log", "run-tab-close-2/dumpsys-2be02e41-retry.txt",
        "run-tab-close-2/dumpsys-864a3db0-retry.txt", "clock-offset.json",
        "phone-link-release-bound.json", "run-tab-close-2/tab-close.json"], record)
    offsets = {}
    for probe in json.loads((connected / "clock-offset.json").read_text(encoding="utf-8"))["probes"]:
        offsets[phone_label(probe["serial"])] = probe
    analysis = pb.analyse(connected / "run-tab-close-2", 30.0, offsets)
    require(analysis.get("problems") == [], f"v3 tab-close problems: {analysis.get('problems')}")
    review_gaps = {"6T": 2304, "10Pro": 2310}
    conservative2 = {}
    for phone, gap in review_gaps.items():
        actual = analysis["phones"][phone]["gap_vs_host_exit_ms"]
        require(actual == float(gap), f"tab-close {phone} gap {actual} != hand-derived {gap}")
        uncertainty = offsets[phone]["uncertainty_ms"]
        low, high = actual - uncertainty, actual + uncertainty
        require(0 < low < high < 30_000, f"tab-close {phone}: interval must be after exit, below bound")
        conservative2[phone] = {"gap_lower_ms": low, "gap_upper_ms": high,
                                "wholly_after_own_exit": True, "upper_below_bound": True}
    tab_v4 = {
        "what": "OTP-23 package1 old W-08 run-tab-close-2 phone-link-release bound v4 (same dumpsys "
                "history + pre-run clock probe, corrected phone-minus-PC SUBTRACTION); only "
                "run-tab-close-2 is recomputed — the old batch's 6T port-disconnect capture stays "
                "INVALID/diagnostic (its own CONNECT had rotated out of the 16-row phone history).",
        "run": analysis["run"],
        "host_exit_utc_ms_estimate": analysis["host_exit_utc_ms_estimate"],
        "anchor_disconnect_epoch_ms": analysis["anchor_disconnect_epoch_ms"],
        "bound_s_predeclared": analysis["bound_s"],
        "phones": analysis["phones"],
        "gap_uncertainty_intervals_ms": conservative2,
        "conservative_check": {"rule": "the whole gap uncertainty interval must lie after the host's "
                                       "own exit (lower > 0) and below the pre-declared 30 s bound",
                               "phones": conservative2},
        "retained_unusable": {
            "path": f"native-bridge/feasibility/evidence/{connected.name}/phone-link-release-bound.json",
            "sha256": sha256(connected / "phone-link-release-bound.json"),
            "note": "historical analyzer output with the + sign; its run-port-disconnect-2 6T entry "
                    "already records the rotated-away burst as a PROBLEM (no own-run pair) — that "
                    "standalone DIS row was never a bound proof. Superseded for W-07/W-08 timing by "
                    "the phone-burst-association artifacts on the P2 transport telemetry."},
        "supersedes": superseded_chain(out, "tab-close-phone-bound-v3.json",
                                       "tab-close-phone-bound-v2.json"),
        "also_supersedes": {"note": "applies the corrected subtraction to the OLD batch analyzer; the "
                                    "old phone-link-release-bound.json is retained immutable (6T "
                                    "+3756, 10Pro +3114 over-estimates). Old derived artifacts stay "
                                    "history."},
        "provenance": provenance_block(inputs2, code, record)}

    # ---- artifact #3: connected-sample-brackets-v3 (B5-3: trace absolute origin, no launch-wall zero) ----
    p2_scenario = json.loads((p2 / "p2-pd1" / "port-disconnect.json").read_text(encoding="utf-8"))
    inputs3 = pin_inputs(p2, "SHA256SUMS", ["p2-pd1/port-disconnect.json",
                                            "p2-pd1/port-disconnect.host-trace.log"], record)
    parsed = core.parse_trace((p2 / "p2-pd1" / "port-disconnect.host-trace.log")
                              .read_text(encoding="utf-8", errors="replace"))
    raw_samples = p2_scenario["detail"]["probe_during"]
    samples = [{"label": s["label"], "wall_ms": s["wall_ms"], "exchange": s.get("exchange"),
                "trace_offset_ms_estimate": s.get("trace_offset_ms_estimate"),
                "source": "verbatim fields of detail.probe_during in the pinned raw port-disconnect.json"}
               for s in raw_samples]
    annotated = core.correlate_samples(samples, parsed)
    positions = [document["trace_offset_corrected_ms"] for document in annotated
                 if "trace_offset_corrected_ms" in document]
    require(positions == [9371, 9473, 9584, 9686, 9944, 9951],
            f"corrected positions {positions} != hand-derived [9371,9473,9584,9686,9944,9951]")
    sends = [(document["label"], document["trace_bracket"]) for document in annotated
             if document["label"] == "send-in-flight"]
    for label, bracket in sends:
        require(bracket["after"] and bracket["before"],
                f"{label}: corrected bracket must sit between the sent request and the message reply")
    calibration = annotated[0]["trace_origin_calibration"]
    require(calibration["applied"] and calibration["trace_origin_epoch_ms"] == 1793170753120,
            f"calibration origin {calibration.get('trace_origin_epoch_ms')} != 1793170753120")
    require(len(calibration["anchors"]) == 2 and calibration["origin_spread_ms"] == 0,
            "the two disconnect-requested anchors must agree exactly")
    brackets_v4 = {
        "what": "OTP-23 package1 W-02/W-07 connected-phase sample brackets v4: sample offsets are "
                "corrected against the trace's own PC-absolute disconnect-requested anchor "
                "(epoch_ms - at_ms), removing the 1718 ms native-host spawn delay the raw "
                "launch-based estimate included. Raw sample wall_ms/exchanges are copied verbatim "
                "from the pinned raw port-disconnect.json; the old trace_bracket annotations in "
                "that file (all samples 1718 ms late, wrongly placed after the final host output) "
                "are superseded, not rewritten.",
        "raw_scenario_id": p2_scenario["id"],
        "raw_scenario_status": p2_scenario["status"],
        "trace_origin_calibration": calibration,
        "samples": annotated,
        "supersedes": superseded_chain(out, "connected-sample-brackets-v3.json",
                                       "connected-sample-brackets-v2.json"),
        "also_supersedes": {
            "path": f"native-bridge/feasibility/evidence/{p2.name}/p2-pd1/port-disconnect.json",
            "sha256": sha256(p2 / "p2-pd1" / "port-disconnect.json"),
            "fields": ["detail.probe_during[*].trace_offset_ms_estimate",
                       "detail.probe_during[*].trace_bracket"],
            "note": "the raw scenario document stays byte-identical and keeps its raw "
                    "BLOCKED criterion_conflict status (W-02 wording); only the DERIVED "
                    "bracket labels are corrected here"},
        "provenance": provenance_block(inputs3, code, record)}

    # ---- owner W-02 adjudication (owner-approved clarification; raw BLOCKED stays) ----
    approval_src = pathlib.Path("/tmp/opencode/otp23-package1-owner-approval.json")
    approval_copy = out / "owner-approval-w02-2026-10-28.json"
    approval_copy.write_bytes(approval_src.read_bytes())
    approval = json.loads(approval_copy.read_text(encoding="utf-8"))
    owner_record_sha = "89d255383b21272590f7c3b3cc3370e7b8f1e688b2445a65dba022d529f23fd4"
    require(sha256(approval_copy) == owner_record_sha,
            f"the copied owner approval record does not match the review-cited owner record sha "
            f"{owner_record_sha}")
    deferred_src = pathlib.Path("/tmp/opencode/otp23-package1-mac-deferred.json")
    deferred_copy = out / "mac-deferral-2026-10-28.json"
    deferred_copy.write_bytes(deferred_src.read_bytes())
    deferred_owner_sha = "ace5e31e99c25ee21b4d634208ef006af02624121624952e0c40238505086e99"
    require(sha256(deferred_copy) == deferred_owner_sha,
            f"the copied Mac deferral record does not match the review-cited sha {deferred_owner_sha}")
    census = {"samples": len(raw_samples), "all_usable": True, "problems": []}
    for sample_doc in raw_samples:
        summary, probe = sample_doc["summary"], sample_doc["probe"]
        label = sample_doc["label"]
        if not summary.get("usable") or summary.get("problems") or summary.get("session_problems"):
            census["problems"].append(f"{label}: unusable/problem")
            census["all_usable"] = False
        for kind in ("top_visible", "top_hidden", "message_only", "children"):
            if summary["host_windows"].get(kind) != 0:
                census["problems"].append(f"{label}: host {kind} {summary['host_windows'][kind]}")
        for family in ("tcp4", "tcp6", "udp4", "udp6"):
            if summary["host_endpoints"].get(family):
                census["problems"].append(f"{label}: host endpoints {family} not empty")
        wrappers = summary["wrapper_windows"]
        if not wrappers:
            census["problems"].append(f"{label}: no wrapper census")
        hidden_total = sum(w.get("top_hidden", 0) for w in wrappers.values())
        visible_total = sum(w.get("top_visible", 0) for w in wrappers.values())
        census.setdefault("wrapper_hidden_total", 0)
        census["wrapper_hidden_total"] += hidden_total
        if visible_total != 0 or hidden_total != 1:
            census["problems"].append(f"{label}: wrapper windows visible={visible_total} hidden={hidden_total}")
        for wrapper_windows in wrappers.values():
            for family in ("tcp4", "tcp6", "udp4", "udp6"):
                if wrapper_windows.get(family):
                    census["problems"].append(f"{label}: wrapper endpoints {family} not empty")
        if summary.get("controls", {}).get("chrome_visible_top_level", 0) == 0:
            census["problems"].append(f"{label}: chrome visible positive control missing")
        if not summary.get("controls", {}).get("chrome_cdp_listener_present"):
            census["problems"].append(f"{label}: chrome CDP listener positive control missing")
        sessions = {pid: rec.get("session") for pid, rec in probe["per_pid"].items()}
        active = probe.get("active_console_session")
        observer = (probe.get("observer") or {}).get("session")
        sessions_equal = active == observer and all(value == active for value in sessions.values())
        census.setdefault("per_sample_sessions_equal", [])
        census["per_sample_sessions_equal"].append(sessions_equal)
        if not sessions_equal:
            census["problems"].append(f"{label}: session ids != active console session ({sessions}, "
                                      f"observer {observer}, active {active})")
    require(census["all_usable"] and not census["problems"],
            f"W-02 census summary would not be clean: {census['problems']}")
    require(census["wrapper_hidden_total"] == len(raw_samples) == 6,
            "expected exactly one never-visible wrapper window in each of the six samples")
    classes = sorted({name
                      for sample_doc in raw_samples
                      for pid_rec in sample_doc["probe"]["per_pid"].values()
                      for name in (pid_rec.get("top_hidden_classes") or [])})
    require(classes == ["ConsoleWindowClass"], f"wrapper window classes drifted: {classes}")
    adjudication = {
        "what": "OTP-23 package1 W-02 separate adjudication: the connected-phase census against the "
                "exact owner-approved clarification. The raw scenario record stays BLOCKED "
                "criterion_conflict and is not rewritten; this document is the W-02-only decision "
                "layered on top of it with the owner-approved wording applied to W-02 alone.",
        "scope": approval["scope"],
        "owner_approval": {
            "recorded_utc": approval["recorded_utc"],
            "source": approval["source"],
            "question": approval["question"],
            "approved_text": approval["approved_text"],
            "answer": approval["answer"],
            "copied_byte_identical_from": "/tmp/opencode/otp23-package1-owner-approval.json",
            "copy_sha256": sha256(approval_copy),
            "owner_record_sha256_verified": owner_record_sha,
            "recorded_utc_fallback_note": "byte-identical copy pinned by sha256 above"},
        "raw_scenario": {
            "path": f"native-bridge/feasibility/evidence/{p2.name}/p2-pd1/port-disconnect.json",
            "sha256": sha256(p2 / "p2-pd1" / "port-disconnect.json"),
            "status": p2_scenario["status"], "error_class": p2_scenario["error_class"],
            "actual": p2_scenario["actual"],
            "w02_wrapper_hidden_window_max": p2_scenario["detail"]["w02_wrapper_hidden_window_max"]},
        "census_from_raw": {**census, "wrapper_window_classes": classes,
                            "chrome_version": p2_scenario["detail"]["chrome_version_actual"],
                            "session_summary": "every sample's observer/Chrome/host/wrapper session "
                                               "equals the active console session (sessions recorded "
                                               "per pid; equality computed above, not assumed)"},
        "adjudication": {
            "w02_outcome": "PASS by this separate adjudication (owner-approved narrow clarification "
                           "applied to W-02 ONLY)",
            "basis": "every sample: zero host windows of all four kinds and zero host endpoints in "
                     "all four families; every endpoint family available; positive controls (Chrome "
                     "visible top-level + CDP listener) present; exactly one never-visible "
                     "ConsoleWindowClass on the Chrome-created wrapper, no other window kind, no "
                     "wrapper endpoints, sessions equal the active console session",
            "no_other_rows": "this document neither promotes, waives nor re-scopes any other row and "
                             "creates no OTP-24 eligibility",
            "chrome_flags_note": "the raw scenario record pins ONLY the legacy environment: "
                                 "VELES_OTP23_DETACHED_CLOSE unset, in both the recorded driver "
                                 "environment and `trace_env_passed_to_chrome`). The absence of a "
                                 "direct-native-host feature flag or policy override is NOT claimed "
                                 "from that raw field: it rests on the separate pinned driver/source "
                                 "review of the launch path plus the recorded actual Chrome argv of "
                                 "the strict runs (ST-13, `proc.args`, 9 args, zero override-flag "
                                 "findings); the argv bytes themselves were not retained verbatim. "
                                 "CDP Browser.getVersion records Chrome/156.0.8078.73."},
        "supersedes": superseded_chain(out, "w02-adjudication.json", "w02-adjudication.json"),
        "provenance": provenance_block(inputs3, code, record)}

    # ---- owner decision records (byte-identical preservation; /tmp paths are volatile) ----
    owner_records = {
        "owner_approval_copy": "owner-approval-w02-2026-10-28.json",
        "mac_deferral_copy": "mac-deferral-2026-10-28.json"}

    outputs = {}
    for name, document in (("phone-burst-association-v4.json", association_v4),
                           ("tab-close-phone-bound-v4.json", tab_v4),
                           ("connected-sample-brackets-v4.json", brackets_v4),
                           ("w02-adjudication.json", adjudication)):
        (out / name).write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
        outputs[name] = sha256(out / name)
    record["outputs"] = outputs
    record["owner_decision_records"] = {
        "approval": {"path": owner_records["owner_approval_copy"],
                     "sha256": sha256(out / owner_records["owner_approval_copy"])},
        "mac_deferral": {"path": owner_records["mac_deferral_copy"],
                         "sha256": sha256(out / owner_records["mac_deferral_copy"])},
        "note": "byte-identical copies (cmp-verified shas) of the operator-captured owner records; "
                "the Mac update/restart was DECLINED, so M-08 stays deferred/unmeasured"}
    record["answers_fix_verification"] = {
        "record": {"path": FIX_VERIFICATION_PATH, "sha256": FIX_VERIFICATION_SHA256,
                   "verified_now": sha256(FIX_VERIFICATION_PATH) == FIX_VERIFICATION_SHA256
                   if pathlib.Path(FIX_VERIFICATION_PATH).exists() else "file no longer present "
                                                                        "(sha cited from capture time)"},
        "closures": {
            "B5-2": "scenario_matrix_core._num implements the EXACT branch contract: non-boolean "
                    "ints accepted WITHOUT float conversion (huge ints like 10**1000 no longer "
                    "raise), math.isfinite ONLY for floats (bools/non-finites rejected as before); "
                    "an overflowing later arithmetic step in the validator is a controlled FAIL "
                    "named numeric_range, never an uncaught exception; regressions: "
                    "test_num_accepts_huge_ints_without_overflow, "
                    "test_giant_stop_epochs_never_escape_the_validator",
            "B5-4": "confirmed closed by the focused recheck; retained: this program pins its own "
                    "runtime-measured sha256 with the orchestration role as code[0] in the record "
                    "and in every output provenance (7-entry code list, no hardcoded self-hash)",
            "chrome_flags_note": "closed in v3 and retained verbatim: the raw scenario pins only the "
                                 "legacy env (VELES_OTP23_DETACHED_CLOSE unset); override-flag "
                                 "absence rests on source review + actual argv records"}}
    record["review_hand_values_crosscheck"] = {
        "association_gaps": expected_gaps, "association_intervals": expected_intervals,
        "tab_close_gaps": review_gaps, "sample_positions": positions,
        "all_matched": True}
    (out / "derivation-record.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
    for name, digest in outputs.items():
        print(f"{digest}  {name}")
    print(f"-> {len(outputs) + 1} files in {out}")
    return 0


def phone_label(serial):
    return PHONE_LABELS.get(serial, serial)


def require(condition, message):
    if not condition:
        raise SystemExit(f"V4 DERIVATION ASSERTION FAILED: {message}")


def provenance_block(inputs, code, record):
    contract_producer = next(entry for entry in code
                             if entry["path"].endswith("pc_phone_clock_probe.py"))
    return {"pinned_at": record["derived_utc"],
            "code": code,
            "clock_producer_contract": {
                "producer": contract_producer["path"],
                "producer_sha256": contract_producer["sha256"],
                "field": "offset_phone_minus_pc_ms_median",
                "semantics": "phone_epoch - pc_epoch (measured at the adb roundtrip midpoint); the "
                             "converter derives pc_epoch = phone_epoch - offset — never +",
                "uncertainty": "uncertainty_ms = spread/2 + max latency/2 + phone resolution/2"},
            "inputs": inputs,
            "git_head": record["git_head"],
            "stage_manifest_status": {
                "authority": "runtime self-pins (p2-pd1/lifecycle.json meta.source_sha256) + the "
                             "code entries above are the actual bytes of this derivation",
                "historical_manifest": record["stage_manifest_historical"]}}


if __name__ == "__main__":
    sys.exit(main())