"""OTP-23 Mac M-08 release attribution (Option A of the 2026-10-29 physical review): a rigorous
per-phone × per-handle causal bijection over the FROZEN analyzer's own inputs.

The frozen analyzer (mac_release_bound.py, imported and byte-pinned, never edited) maps each phone with
the conservative hull over BOTH links' in/out pairs, because it does not know which host connection
handle served which phone. This stage adds exactly one fact the run already guarantees — each phone
held exactly one link, so its 16-byte proof echo and its first 4096-byte probe fragment belong to the
SAME handle — and deduces the mapping instead of assuming it:

  O(p, h) = [e16 − m16_h − q, e16 − i16_h + q] ∩ [e4096 − m4096_h − q, e4096 − i4096_h + q]

for every phone p and handle h (e = the phone's own notify_api epoch, i/m = the host trace's `in send`
/ `out message` ms of that handle's record; q = the ±1 ms quantization of both stamp sources, applied
OUTWARD before any feasibility test). Every bijection phones→handles is enumerated; a mapping is
feasible iff all its O(p, map(p)) are non-empty. The true mapping is necessarily feasible, so ONLY a
unique feasible mapping is used (by deduction — never a choice among feasible ones, and no phone epoch
is ever compared with another phone's epoch). 0 feasible ⇒ FAIL (the premises are violated); ≥ 2
feasible ⇒ ambiguous, the frozen hull verdict stands.

Under the unique mapping the four UNCHANGED checks are re-run on the attributed interval:
`release_after_last_exchange` against the trace's GLOBAL last exchange message (the own-link value is
recorded, never used to pass), `within_bound_of_own_exit` (exit + declared bound), and both fixture-stop
guards with the attributed `hi` (the hull-`hi` values are recorded alongside). A frozen hull FAIL may be
upgraded ONLY by a unique mapping with all four checks PASS; any structural hull problem (missing or
ambiguous events, unclean telemetry, empty anchor, run/peer mismatch) is never bypassed. Preconditions
(exactly 2 phones, each one burst of one peer, same run; exactly 2 handles, each with exactly one 16-byte
and one 4096-byte pair, and no other record size) not met ⇒ no attribution, the hull verdict stands.
`m08_creditable = verdict == PASS and target_teardown_scenario` (the hull's own flag, unchanged).

Usage:  python3 scripts/mac_release_attribution.py --lifecycle-dir <dir> --scenario tab-close \
          --transport a.json --transport b.json --stops stops.json \
          [--hull-release-bound release-bound.json] --out release-bound-attributed.json
Python 3.9+ stdlib only; no device/Mac access.
"""

import argparse
import hashlib
import itertools
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mac_release_bound as mrb  # FROZEN reviewed analyzer: imported read-only, byte-pinned below

SELF_SHA = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
REVIEWED_ANALYZER_SHA256 = "a49b3450d6d984c24e87ae60b4ffd00f4ca661beb92ec56b9b5410fc6bc26560"
QUANTUM_MS = 1
CHECKS = ("release_after_last_exchange", "within_bound_of_own_exit",
          "stop_after_disconnect", "stop_after_bound_plus_uncertainty")


def per_handle_pairs(host):
    """{handle: {"p16": [(i, m)], "p4096": [(i, m)]}} from the frozen parser's in/out pairs."""
    proof_key, probe_key = mrb.base64_len(mrb.driver.LINK_PROOF_BYTES), mrb.base64_len(mrb.core.SIZE)
    handles = {}
    for key, name in ((proof_key, "p16"), (probe_key, "p4096")):
        for t_in, t_msg, handle in host["sizes"].get(key, []):
            handles.setdefault(handle, {"p16": [], "p4096": []})[name].append((t_in, t_msg))
    return handles, {proof_key, probe_key}


def preconditions(hull, host):
    """Problems that make attribution NOT APPLICABLE (the hull verdict then stands)."""
    problems = []
    phones = hull.get("phones") or []
    if len(phones) != 2 or len({p["serial_hint"] for p in phones}) != 2:
        problems.append(f"expected exactly 2 distinct phones, saw {len(phones)}")
    if not all(p.get("clean_telemetry") is True for p in phones):
        problems.append("telemetry not complete/clean on every phone")
    if len({p.get("run_id") for p in phones}) != 1 or len({p.get("peer_sha256_prefix") for p in phones}) != 1:
        problems.append("phones do not share one run id and one peer")
    handles, allowed = per_handle_pairs(host)
    if set(host["sizes"]) - allowed:
        problems.append(f"unexpected record sizes in the trace: {sorted(set(host['sizes']) - allowed)}")
    if len(handles) != 2:
        problems.append(f"expected exactly 2 handles, saw {len(handles)}")
    for handle, pairs in handles.items():
        if len(pairs["p16"]) != 1 or len(pairs["p4096"]) != 1:
            problems.append(f"handle {handle[:8]}: expected exactly one 16-byte and one 4096-byte pair, "
                            f"saw {len(pairs['p16'])}/{len(pairs['p4096'])}")
    return problems, handles


def candidate(phone, pairs, quantum):
    """O(p, h): the per-handle causal origin interval, both bounds widened by `quantum` (outward)."""
    e16 = int(phone["proof_notify"]["epoch_ms"])
    e4096 = int(phone["first_fragment_notify"]["epoch_ms"])
    (i16, m16), = pairs["p16"]
    (i4096, m4096), = pairs["p4096"]
    b16 = [e16 - m16 - quantum, e16 - i16 + quantum]
    b4096 = [e4096 - m4096 - quantum, e4096 - i4096 + quantum]
    lo, hi = max(b16[0], b4096[0]), min(b16[1], b4096[1])
    return {"bracket16": b16, "bracket4096": b4096, "lo": lo, "hi": hi,
            "feasible": lo <= hi, "width_ms": hi - lo,
            "own_last_exchange_message_at_ms": max(m16, m4096)}


def attributed_checks(phone, cand, host, stop, declared_bound_ms):
    dis = phone["disconnect_phone_ms"]
    dis_lo, dis_hi = dis - cand["hi"], dis - cand["lo"]
    global_last, exit_at = host["last_exchange_message_at_ms"], host["exit_at_ms"]
    checks = {"release_after_last_exchange": dis_lo >= global_last,
              "within_bound_of_own_exit": dis_hi <= exit_at + declared_bound_ms}
    if stop is not None:
        checks["stop_after_disconnect"] = stop["stop_min_phone_ms"] > dis
        checks["stop_after_bound_plus_uncertainty"] = \
            stop["stop_min_phone_ms"] >= cand["hi"] + exit_at + declared_bound_ms
    hull_hi = phone["origin_interval"]["hi"]
    return {"dis_trace_interval": [dis_lo, dis_hi],
            "global_last_exchange_message_at_ms": global_last,
            "margin_vs_global_last_ms": dis_lo - global_last,
            "own_last_exchange_message_at_ms": cand["own_last_exchange_message_at_ms"],
            "release_after_own_link_last_recorded_only": dis_lo >= cand["own_last_exchange_message_at_ms"],
            "exit_at_ms": exit_at, "exit_plus_bound_ms": exit_at + declared_bound_ms,
            "margin_vs_exit_plus_bound_ms": exit_at + declared_bound_ms - dis_hi,
            "checks": checks,
            "stop_guard_with_hull_hi_recorded": None if stop is None else {
                "hull_hi": hull_hi,
                "stop_after_bound_plus_uncertainty": stop["stop_min_phone_ms"] >= hull_hi + exit_at + declared_bound_ms}}


def attribute(lifecycle_dir, scenario, transports, stops, declared_bound_ms=30000.0,
              quantum_ms=QUANTUM_MS, hull_release_bound=None):
    problems = []
    analyzer_sha = mrb.file_sha256(pathlib.Path(mrb.__file__))
    if analyzer_sha != REVIEWED_ANALYZER_SHA256 or mrb.SELF_SHA != REVIEWED_ANALYZER_SHA256:
        problems.append(f"imported analyzer is not the reviewed bytes ({analyzer_sha[:12]})")
    hull = mrb.analyse(lifecycle_dir, scenario, transports, stops, declared_bound_ms)
    out_dir = pathlib.Path(lifecycle_dir)
    trace_path = out_dir / f"{scenario}.host-trace.log"
    document = {
        "what": "OTP-23 Mac M-08 phone-side release proof with per-handle causal attribution (Option A)",
        "scenario": scenario, "quantum_ms": quantum_ms, "declared_bound_ms": declared_bound_ms,
        "hull_result": {k: hull.get(k) for k in ("verdict", "problems", "target_teardown_scenario",
                                                  "creditability", "run_id")},
        "hull_phones": [{"serial_hint": p["serial_hint"], "origin_interval": p["origin_interval"],
                         "dis_trace_interval": p["dis_trace_interval"], "checks": p["checks"]}
                        for p in hull.get("phones") or []],
        "target_teardown_scenario": hull.get("target_teardown_scenario") is True,
    }
    status, verdict, assignments = None, None, {}
    if hull.get("problems"):
        status, verdict = "not_applicable_structural_hull_problems", "FAIL"
    elif not trace_path.is_file():
        status, verdict = "not_applicable_no_trace", hull.get("verdict", "FAIL")
    else:
        host = mrb.host_record_bundles(trace_path.read_text(encoding="utf-8", errors="replace"))
        pre, handles = preconditions(hull, host)
        document["preconditions"] = {"problems": pre, "met": not pre}
        if pre:
            status, verdict = "not_applicable_preconditions", hull.get("verdict", "FAIL")
        else:
            phones = sorted(hull["phones"], key=lambda p: p["serial_hint"])
            handle_ids = sorted(handles)
            candidates = {p["serial_hint"]: {h: candidate(p, handles[h], quantum_ms) for h in handle_ids}
                          for p in phones}
            mappings = []
            for perm in itertools.permutations(handle_ids):
                mapping = {p["serial_hint"]: h for p, h in zip(phones, perm)}
                mappings.append({"mapping": mapping,
                                 "feasible": all(candidates[s][h]["feasible"] for s, h in mapping.items())})
            feasible = [m for m in mappings if m["feasible"]]
            document["handles"] = {h: {"p16": handles[h]["p16"], "p4096": handles[h]["p4096"]} for h in handle_ids}
            document["candidates"] = candidates
            document["mappings"] = mappings
            document["global_last_exchange_message_at_ms"] = host["last_exchange_message_at_ms"]
            document["exit_at_ms"] = host["exit_at_ms"]
            if not feasible:
                status, verdict = "zero_feasible_mappings", "FAIL"
                problems.append("no phone→handle mapping is causally feasible (premises violated)")
            elif len(feasible) > 1:
                status, verdict = "ambiguous_multiple_feasible_mappings", hull.get("verdict", "FAIL")
            else:
                status = "unique_mapping"
                stop_map = {s["serial"]: s for s in stops}
                for phone in phones:
                    serial = phone["serial_hint"]
                    handle = feasible[0]["mapping"][serial]
                    cand = candidates[serial][handle]
                    stop = stop_map.get(serial)
                    if stop is None:
                        problems.append(f"{serial}: fixture-stop markers missing")
                    assignments[serial] = {"handle": handle, "origin_interval": cand,
                                           **attributed_checks(phone, cand, host, stop, declared_bound_ms)}
                all_pass = all(a["checks"].get(k) is True for a in assignments.values() for k in CHECKS)
                verdict = "PASS" if all_pass and not problems else "FAIL"
    if problems:
        verdict = "FAIL"
    document.update({
        "attribution_status": status, "assignments": assignments,
        "verdict": verdict, "problems": problems,
        "upgraded_from_hull_fail": hull.get("verdict") == "FAIL" and verdict == "PASS",
        "m08_creditable": verdict == "PASS" and document["target_teardown_scenario"],
        "m08_creditable_rule": "verdict == PASS and target_teardown_scenario (hull flag, unchanged)",
        "inputs": {"lifecycle_dir": str(out_dir),
                   "trace_sha256": mrb.file_sha256(trace_path) if trace_path.is_file() else None,
                   "scenario_json_sha256": (mrb.file_sha256(out_dir / f"{scenario}.json")
                                            if (out_dir / f"{scenario}.json").is_file() else None),
                   "transports": [{"path": pathlib.Path(p).name, "sha256": mrb.file_sha256(p)} for p, _ in transports],
                   "stops": stops,
                   "hull_release_bound_frozen": hull_release_bound},
        "helper_self_sha256": SELF_SHA,
        "imported_analyzer_sha256": analyzer_sha,
        "imported_core_sha256": mrb.module_sha256(mrb.core),
        "imported_driver_sha256": mrb.module_sha256(mrb.driver),
        "absolute_mac_anchor": "none (host trace's own timeline; per-phone clocks never compared)",
        "engineered_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return document


def source_binding():
    """Git HEAD + tracked dirty-diff sha of the analysis sources (explicit unavailable otherwise)."""
    repo = HERE.parent.parent.parent
    try:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=30, check=True).stdout.strip()
        diff = subprocess.run(["git", "-C", str(repo), "diff", "HEAD", "--binary"], capture_output=True,
                              timeout=60, check=True).stdout
        return {"available": True, "head": head, "tracked_dirty_diff_sha256": hashlib.sha256(diff).hexdigest()}
    except (OSError, subprocess.SubprocessError) as error:
        return {"available": False, "reason": type(error).__name__}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lifecycle-dir", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--transport", action="append", required=True, type=pathlib.Path)
    parser.add_argument("--stops", required=True, type=pathlib.Path)
    parser.add_argument("--declared-bound-ms", type=float, default=30000.0)
    parser.add_argument("--hull-release-bound", type=pathlib.Path,
                        help="the FROZEN release-bound.json of this run (recorded + cross-checked, never written)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    target = pathlib.Path(args.out)
    if target.exists():
        raise SystemExit(f"{target} exists; derived outputs are never overwritten")
    transports = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in args.transport]
    stops = json.loads(args.stops.read_text(encoding="utf-8"))
    frozen = None
    if args.hull_release_bound:
        frozen_doc = json.loads(args.hull_release_bound.read_text(encoding="utf-8"))
        frozen = {"path": args.hull_release_bound.name, "sha256": mrb.file_sha256(args.hull_release_bound),
                  "verdict": frozen_doc.get("verdict"), "helper_self_sha256": frozen_doc.get("helper_self_sha256")}
    document = attribute(args.lifecycle_dir, args.scenario, transports, stops, args.declared_bound_ms,
                         hull_release_bound=frozen)
    if frozen is not None and frozen["verdict"] != document["hull_result"]["verdict"]:
        document["problems"].append("recomputed hull verdict differs from the frozen release-bound.json")
        document["verdict"], document["m08_creditable"] = "FAIL", False
    document["source_binding"] = source_binding()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(f"mac-release-attribution: {document['verdict']} ({args.scenario}, {document['attribution_status']}, "
          f"m08_creditable={document['m08_creditable']}) -> {target}")
    return 0 if document["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
