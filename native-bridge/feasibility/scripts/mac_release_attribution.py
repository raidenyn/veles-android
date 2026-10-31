"""OTP-23 Mac M-08 release attribution, A2 (source-proven causal inequalities only), 2026-10-29.

A per-phone × per-handle causal bijection over the FROZEN analyzer's own inputs. The frozen analyzer
(mac_release_bound.py a49b3450, imported and byte-pinned, never edited) is used ONLY for its parsers
and as a recorded diagnostic; its hull verdict is never a fallback proof.

PREMISE CORRECTION (supersedes A1 69546ee5 and the 16-byte sentence in the frozen analyzer's
docstring; independent review /tmp/opencode/otp23-mac-final-data-review-opus.md §1): A1 used
`origin ≥ e16 − m16`, i.e. "the phone's 20-byte proof notify_api is stamped before the host logs
`out … type=message`". That is NOT causal: SharingGattServer.kt calls notifyCharacteristicChanged()
and only AFTER it returns does telemetry.notifyResult() take epochMillis() (behind a monitor and a
hash), while the binder call may already have queued the PDU to the Bluetooth stack — the host can
receive and log the single-fragment echo before e16 is stamped. A2 therefore NEVER uses the 16-byte
lower bound; it is recorded as `bracket16_lower_not_causal_recorded_only` and nothing more.

Causal premises A2 does use (each verified on the data, fail-closed):
  * LOWER: the FIRST fragment of the multi-fragment 4096-byte echo. On the phone's main looper,
    nextFragment() sets `awaiting` and fragment 1's notify_api is stamped in that same message;
    fragment k+1 can only be produced after notificationComplete() clears `awaiting` in a LATER
    main-looper message (onNotificationSent). So e_first < the final fragment's call < the final
    fragment on air < host reassembly < the host `out … type=message` stamp m4096_h:
    origin ≥ e_first − m4096_h − q. This needs LATER fragments: a single-fragment response is not a
    safe lower anchor. Verified per phone: exactly one 20-byte proof notify, then exactly the 9-event
    4096 echo 8×512 + 36, every notify_api code 0 with its own bound notify_callback code 0, the
    analyzer's first-fragment event IS the first of those 9.
  * UPPER: the host stamps `in … type=send` before forwarding the frame, and the phone must receive the
    record before echoing, so e16 > i16_h and e_first > i4096_h:
    origin ≤ min(e16 − i16_h, e_first − i4096_h) + q.
  * CLOCK: no wall step inside the burst: max − min of (epoch_ms − elapsed_realtime_ms) over the
    burst's events ≤ 1 ms.
  q = 1 ms (floor quantization of both stamps), applied outward BEFORE any feasibility test.

  O(p, h) = [e_first − m4096_h − q,  min(e16 − i16_h, e_first − i4096_h) + q]

Every bijection phones→handles is enumerated; feasible iff all O(p, map(p)) are non-empty. The true
mapping is necessarily feasible, so ONLY a unique feasible mapping is used, by deduction (never a
choice; phone epochs are never compared across phones). Under it the UNCHANGED acceptance checks run:
release after the trace's GLOBAL last exchange message (own-link value recorded only), within the host's
own exit + declared bound (30 s), disconnect before the fixture stop, stop ≥ attributed hi + exit + bound
(hull-hi value recorded alongside).

FAIL-CLOSED (parent decision 2026-10-29, consequence of the invalid premise): a frozen hull PASS is NOT
an independent proof. Structural hull problems, unmet preconditions, unmet causal premises, zero or ≥ 2
feasible mappings ⇒ verdict FAIL, m08_creditable false; the hull result stays recorded as diagnostics.
`m08_creditable = verdict == PASS and target_teardown_scenario`.

Precision: the conservative origin widths are recorded; the earlier ≤ 250 ms anchor-width figure was a
test-instrument precision target, not an RFC acceptance clause, and is NOT claimed. Acceptance rests on
the full-interval checks above, none of which is relaxed.

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

import mac_release_bound as mrb  # FROZEN reviewed analyzer: parsers + diagnostics only, byte-pinned

SELF_SHA = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
REVIEWED_ANALYZER_SHA256 = "a49b3450d6d984c24e87ae60b4ffd00f4ca661beb92ec56b9b5410fc6bc26560"
QUANTUM_MS = 1
PROOF_NOTIFY_BYTES = mrb.driver.LINK_PROOF_BYTES + 4          # 20: one 16-byte proof + 4-byte header
PROBE_FRAGMENTS = [512] * 8 + [36]                             # the 4096-byte echo as notified
CLOCK_STEP_TOLERANCE_MS = 1
INSTRUMENT_PRECISION_TARGET_MS = 250                           # test-instrument target, NOT an RFC gate
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
    """Structural shape required for an attribution (unmet ⇒ fail closed)."""
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


def causal_premises(phone, transport):
    """Verify, on this phone's own burst, the facts the causal argument needs (fail-closed)."""
    problems = []
    peer = (transport.get("peers") or [{}])[0]
    events = peer.get("events", [])
    notifies = [e for e in events if e.get("kind") == "notify_api"]
    callbacks = {e.get("bound_op"): e for e in events if e.get("kind") == "notify_callback"}
    sizes = [e.get("bytes") for e in notifies]
    if sizes != [PROOF_NOTIFY_BYTES] + PROBE_FRAGMENTS:
        problems.append(f"notify sequence is not one {PROOF_NOTIFY_BYTES}-byte proof + the complete 9-fragment "
                        f"4096-byte echo {PROBE_FRAGMENTS}: saw {sizes}")
    if not all(e.get("code") == 0 for e in notifies):
        problems.append("a notify_api returned non-zero")
    if not all(callbacks.get(e.get("bound_op"), {}).get("code") == 0 for e in notifies):
        problems.append("a notify_api has no bound notify_callback with code 0")
    counters = {k: peer.get(k) for k in ("notify_calls", "api_success", "callback_success", "api_nonzero",
                                         "callback_nonzero", "pending_unresolved", "unbound_callbacks")}
    if not (counters["notify_calls"] == counters["api_success"] == counters["callback_success"] == len(notifies)
            and counters["api_nonzero"] == 0 and counters["callback_nonzero"] == 0
            and counters["pending_unresolved"] == 0 and counters["unbound_callbacks"] == 0):
        problems.append(f"API/callback counters are not all-success and complete: {counters}")
    first = phone["first_fragment_notify"]
    if len(notifies) < 3 or first.get("seq") != notifies[1].get("seq"):
        problems.append("the analyzer's first-fragment event is not the first fragment of a multi-fragment echo")
    later = [e for e in notifies if e.get("seq", 0) > first.get("seq", 0) and e.get("bytes", 0) > PROOF_NOTIFY_BYTES]
    if not later:
        problems.append("no LATER fragment after the first 4096 fragment: not a safe lower anchor")
    offsets = [e["epoch_ms"] - e["elapsed_realtime_ms"] for e in events
               if isinstance(e.get("epoch_ms"), int) and isinstance(e.get("elapsed_realtime_ms"), int)]
    step = (max(offsets) - min(offsets)) if len(offsets) == len(events) and offsets else None
    if step is None or step > CLOCK_STEP_TOLERANCE_MS:
        problems.append(f"phone wall clock not stable within {CLOCK_STEP_TOLERANCE_MS} ms over the burst "
                        f"(epoch − elapsed spread {step})")
    return problems, {"notify_bytes": sizes, "later_fragments_after_first": len(later),
                      "epoch_minus_elapsed_spread_ms": step, "counters": counters}


def candidate(phone, pairs, quantum):
    """O(p, h) from source-proven inequalities only (see module docstring)."""
    e16 = int(phone["proof_notify"]["epoch_ms"])
    e_first = int(phone["first_fragment_notify"]["epoch_ms"])
    (i16, m16), = pairs["p16"]
    (i4096, m4096), = pairs["p4096"]
    lo = e_first - m4096 - quantum
    hi = min(e16 - i16 + quantum, e_first - i4096 + quantum)
    return {"lo": lo, "hi": hi, "feasible": lo <= hi, "width_ms": hi - lo,
            "lower_from": "first 4096 fragment (multi-fragment, main-looper ordered)",
            "upper_terms": {"e16_minus_i16_plus_q": e16 - i16 + quantum,
                            "efirst_minus_i4096_plus_q": e_first - i4096 + quantum},
            "bracket16_lower_not_causal_recorded_only": e16 - m16 - quantum,
            "within_instrument_precision_target_250ms": hi - lo <= INSTRUMENT_PRECISION_TARGET_MS,
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
        "what": "OTP-23 Mac M-08 phone-side release proof, A2 per-handle causal attribution "
                "(source-proven inequalities only)",
        "method_version": "A2 (supersedes A1 69546ee5: 16-byte lower bound not causal)",
        "scenario": scenario, "quantum_ms": quantum_ms, "declared_bound_ms": declared_bound_ms,
        "hull_result_diagnostic_only": {k: hull.get(k) for k in ("verdict", "problems", "target_teardown_scenario",
                                                                 "creditability", "run_id")},
        "hull_role": "diagnostics only; a hull PASS is never accepted as a fallback proof",
        "hull_phones_diagnostic_only": [{"serial_hint": p["serial_hint"], "origin_interval": p["origin_interval"],
                                         "dis_trace_interval": p["dis_trace_interval"], "checks": p["checks"]}
                                        for p in hull.get("phones") or []],
        "target_teardown_scenario": hull.get("target_teardown_scenario") is True,
    }
    status, assignments = None, {}
    if hull.get("problems"):
        status = "fail_closed_structural_hull_problems"
        problems.append("structural problems in the frozen analyzer's inputs: "
                        + " | ".join(str(p) for p in hull["problems"]))
    elif not trace_path.is_file():
        status = "fail_closed_no_trace"
        problems.append("host trace missing")
    else:
        host = mrb.host_record_bundles(trace_path.read_text(encoding="utf-8", errors="replace"))
        pre, handles = preconditions(hull, host)
        document["preconditions"] = {"problems": pre, "met": not pre}
        transport_by_serial = {pathlib.Path(p).stem.replace("transport-", ""): doc for p, doc in transports}
        premises = {}
        for phone in hull.get("phones") or []:
            premise_problems, facts = causal_premises(phone, transport_by_serial.get(phone["serial_hint"], {}))
            premises[phone["serial_hint"]] = {"met": not premise_problems, "problems": premise_problems, **facts}
        document["causal_premises"] = premises
        if pre:
            status = "fail_closed_preconditions"
            problems.extend(pre)
        elif not all(v["met"] for v in premises.values()):
            status = "fail_closed_causal_premises"
            problems.extend(f"{s}: {p}" for s, v in premises.items() for p in v["problems"])
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
                status = "fail_closed_zero_feasible_mappings"
                problems.append("no phone→handle mapping is causally feasible (premises violated)")
            elif len(feasible) > 1:
                status = "fail_closed_ambiguous_mapping"
                problems.append(f"{len(feasible)} feasible mappings: attribution ambiguous, never chosen")
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
                failing = [f"{s}: {k}" for s, a in assignments.items() for k in CHECKS if a["checks"].get(k) is not True]
                if failing:
                    problems.append("acceptance checks failed: " + ", ".join(failing))
    verdict = "PASS" if status == "unique_mapping" and not problems else "FAIL"
    widths = [a["origin_interval"]["width_ms"] for a in assignments.values()]
    document.update({
        "attribution_status": status, "assignments": assignments,
        "verdict": verdict, "problems": problems,
        "hull_verdict_differs": hull.get("verdict") != verdict,
        "m08_creditable": verdict == "PASS" and document["target_teardown_scenario"],
        "m08_creditable_rule": "verdict == PASS and target_teardown_scenario; hull verdict never substitutes",
        "precision": {"assigned_origin_widths_ms": widths,
                      "instrument_precision_target_ms": INSTRUMENT_PRECISION_TARGET_MS,
                      "all_within_instrument_target": bool(widths) and all(w <= INSTRUMENT_PRECISION_TARGET_MS for w in widths),
                      "note": "the 250 ms figure was a test-instrument precision target, not an RFC acceptance clause; "
                              "it is NOT claimed here. Acceptance rests on the full-interval checks (global last "
                              "exchange, exit + bound, both stop guards), none of which is relaxed — not a waiver."},
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
    if frozen is not None and frozen["verdict"] != document["hull_result_diagnostic_only"]["verdict"]:
        document["problems"].append("recomputed hull diagnostic differs from the frozen release-bound.json")
        document["verdict"], document["m08_creditable"] = "FAIL", False
    document["source_binding"] = source_binding()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(f"mac-release-attribution(A2): {document['verdict']} ({args.scenario}, {document['attribution_status']}, "
          f"m08_creditable={document['m08_creditable']}) -> {target}")
    return 0 if document["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
