"""OTP-23 Mac M-08 release-bound analyzer (parent P0-A decision + Opus preflight Task 3, 2026-10-28).

In-band causal anchor: binds the phones' actually observed link releases to the host's own exit
line WITHOUT any absolute Mac wall anchor and WITHOUT reusing any stale PC/Windows clock offset.
The anchor is the host trace's own ms timeline (an origin interval):
  - every 16-byte link-proof exchange the host received is logged `in … type=send …
    record_base64.len=24` before the phone's 20-byte notify arrives, and the exact echo is logged
    `out … type=message … record_base64.len=24` after it;
  - the same holds for the 4096-byte probe exchanges (the phone's first >20-byte notify fragment);
  - so by pure causality the phone event epoch `e` lies strictly between `origin + t_in` and
    `origin + t_msg`, giving `origin ∈ [e − t_msg_max, e − t_in_min]` (the conservative hull over
    a record's in/out pairs — valid for either phone without knowing which handle served it);
  - the per-phone origin interval is the INTERSECTION of the 16-byte and the 4096-byte brackets
    (empty ⇒ FAIL); `offset_phone_minus_trace = interval midpoint`, `uncertainty = half-width + 1 ms`.

Release interval `DIS_trace = DIS_phone − origin ± u` must satisfy BOTH: `DIS_trace_lo ≥
t_last_exchange_message` (released after the last proven-alive exchange) and `DIS_trace_hi ≤
t_exit + declared bound` (within the pre-declared 30 s bound of the host's own exit line). The
whole interval is recorded. The fixture-stop guard runs on the phone clock: the stop bracket's
earliest stamp must be later than `DIS_phone` and ≥ `origin_max + (t_exit + bound)` — a
stop-induced release therefore cannot satisfy the bound.

What this output is for: P0-A makes the host-trace release-count gate Windows-only; every macOS
acceptance scenario MUST additionally carry this external phone-side release proof. The analyzer
adjudicates nothing in acceptance/otp23-matrix.json (the parent records rows from result files).
Replays of ABORTED runs (e.g. mac-resume-2026-10-28/lifecycle-1 cleanup exits) are diagnostic only
and are never creditable target-teardown evidence: `target_teardown_scenario` is only true for a
run whose own scenario JSON recorded trace teardown checks.
ASSUMPTION (documented, by the parent's retake scope): the two release checks assume the teardown
happens after every exchange, which holds for shutdown / port-disconnect / tab-close (the M-08
retake set). failure-isolation's deliberate first-link disconnect happens BEFORE the survivor
exchange by design, so `release_after_last_exchange` must not be expected to hold for that phone
in a real failure-isolation run; the parent's retake does not include that scenario.

Usage:  python3 scripts/mac_release_bound.py --lifecycle-dir <dir> --scenario shutdown \
          --transport a.json --transport b.json --stops stops.json \
          [--declared-bound-ms 30000] [--corroborate clock-offset-mac.json] --out result.json
Python 3.9+ stdlib only; no device/Mac access needed to run the analyzer itself.
"""

import argparse
import hashlib
import json
import pathlib
import re
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import scenario_matrix_core as core  # frozen: reused read-only, byte-pinned in every output
import scenario_matrix as driver  # LINK_PROOF_BYTES: the scenario driver's own proof shape

TRACE_STAMP = re.compile(r"\[\s*(\d+)ms pid=(\d+)\] (.*)$")
TRACE_LINK = re.compile(r"(in|out)\s+len=\d+ type=(send|message)\b.*?"
                        r"connection_handle=([0-9a-f]+).*?record_base64\.len=(\d+)")
TRACE_EXIT = re.compile(r"^exit code=(-?\d+) stdout_drained=(\w+)$")
SELF_SHA = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()


def fail(problems, message):
    problems.append(message)


def host_record_bundles(trace_text):
    """Causal in/out pairs per record size from the host trace: {size: [(t_in, t_msg, handle)]},
    plus the exit at_ms and the last exchange-message at_ms. Only structured fields; no bytes."""
    sizes = {}
    opens = []          # pending (size, handle, t) keyed per handle: only one record in flight per handle
    last_exchange_message = None
    exit_at = None
    for line in trace_text.splitlines():
        stamp = TRACE_STAMP.match(line)
        if not stamp:
            continue
        at_ms, _, body = int(stamp.group(1)), stamp.group(2), stamp.group(3).strip()
        done = TRACE_EXIT.match(body)
        if done:
            exit_at = at_ms
            continue
        link = TRACE_LINK.match(body)
        if not link:
            continue
        direction, kind, handle, size = link.group(1), link.group(2), link.group(3), int(link.group(4))
        if direction == "in" and kind == "send":
            opens.append((size, handle, at_ms))
        elif direction == "out" and kind == "message" and opens:
            # pair with the earliest open record of the same size (per-handle order is FIFO)
            for index, (o_size, o_handle, t_in) in enumerate(opens):
                if o_size == size and o_handle == handle:
                    sizes.setdefault(size, []).append((t_in, at_ms, handle))
                    opens.pop(index)
                    last_exchange_message = max(last_exchange_message or 0, at_ms)
                    break
    return {"sizes": sizes, "exit_at_ms": exit_at, "last_exchange_message_at_ms": last_exchange_message}


def phone_burst(transport):
    """The single connected→disconnected burst of the ONE peer in `transport`, with notify events
    in order. Ambiguity (not exactly one connect/disc pair or not exactly one peer) FAILs."""
    problems = []
    peers = transport.get("peers", [])
    if len(peers) != 1:
        fail(problems, f"expected exactly one peer, saw {len(peers)}")
        return None, problems
    peer = peers[0]
    connects = [e for e in peer.get("events", []) if e.get("kind") == "connected"]
    disc = [e for e in peer.get("events", []) if e.get("kind") == "disconnected"]
    if len(connects) != 1 or len(disc) != 1:
        fail(problems, f"expected exactly one connected and one disconnected event, "
                       f"saw {len(connects)}/{len(disc)} (bursts would be ambiguous)")
        return peer, problems
    return peer, problems


def interval_brackets(origin_records, phone_epoch):
    """origin = wall epoch (phone clock) of trace time 0. For every (t_in, t_msg) in/out pair of one
    record size, the phone's event epoch `e` lies causally in `(origin + t_in, origin + t_msg)`, so
    `origin ∈ [e − t_msg, e − t_in]` — for the phone's OWN record's pair. The handle-to-phone
    mapping is unknown, so the conservative envelope over a size's pairs is
    `[e − max(t_msg), e − min(t_in)]` (every pair's interval is contained in it; never narrower than
    any per-pair truth). The per-phone origin interval is then the INTERSECTION of the 16-byte and
    the 4096-byte envelopes (empty ⇒ FAIL) — a stricter requirement, as a wrong burst pairing would
    produce an empty intersection."""
    los = [phone_epoch - t_msg for pairs in origin_records for t_in, t_msg, _ in pairs]
    his = [phone_epoch - t_in for pairs in origin_records for t_in, t_msg, _ in pairs]
    if not los:
        return 0, -1
    return min(los), max(his)


def analyse(lifecycle_dir, scenario, transports, stops, declared_bound_ms, corroborate=None):
    problems = []
    out_dir = pathlib.Path(lifecycle_dir)
    trace_path = out_dir / f"{scenario}.host-trace.log"
    scenario_path = out_dir / f"{scenario}.json"
    if not trace_path.is_file() or not scenario_path.is_file():
        return {"verdict": "FAIL", "problems": [f"scenario files missing under {out_dir} "
                                                f"(trace {trace_path.name}: {trace_path.is_file()})"]}
    trace_text = trace_path.read_text(encoding="utf-8", errors="replace")
    host = host_record_bundles(trace_text)
    scenario_doc = json.loads(scenario_path.read_text(encoding="utf-8"))
    # a replay of an ABORTED run is diagnostic only: the trace teardown checks must have recorded in
    # the scenario's own result. REAL driver shape (Kimi F-1): verify_gone stores trace_exit nested
    # under detail.teardown, so that nested field is the creditability gate; ABORTED runs abort
    # BEFORE any teardown step and carry no detail.teardown at all — never credited, by shape and by
    # status.
    detail = scenario_doc.get("detail", {}) or {}
    teardown = (detail.get("teardown") or {})
    target_teardown = scenario_doc.get("status") == "PASS" and "trace_exit" in teardown
    sizes = host["sizes"]
    proof_pairs = sizes.get(base64_len(driver.LINK_PROOF_BYTES), [])
    probe_pairs = sizes.get(base64_len(core.SIZE), [])
    unusable = []
    if not proof_pairs or not probe_pairs:
        unusable.append(f"trace has no 16-byte proof and/or 4096-byte probe bundles "
                        f"(sizes seen: {sorted(sizes)}, exit {host['exit_at_ms']})")
    if host["exit_at_ms"] is None:
        unusable.append("host trace has no exit line")
    if host["last_exchange_message_at_ms"] is None:
        unusable.append("host trace has no exchange message (no proven-alive anchor)")
    if unusable:
        return {"verdict": "FAIL", "problems": unusable, "scenario": scenario,
                "lifecycle_dir": str(out_dir), "helper_self_sha256": SELF_SHA,
                "absolute_mac_anchor": "none"}
    phones = []
    stop_map = {document["serial"]: document for document in stops}
    for transport_path, transport in transports:
        serial = transport_path.stem.replace("transport-", "")
        peer, burst_problems = phone_burst(transport)
        problems.extend(f"{serial}: {p}" for p in burst_problems)
        if peer is None or burst_problems:
            continue
        events = peer.get("events", [])
        connects = [e["epoch_ms"] for e in events if e.get("kind") == "connected"]
        disc = [e["epoch_ms"] for e in events if e.get("kind") == "disconnected"]
        notifies = [e for e in events if e.get("kind") == "notify_api"]
        proof_event = notifies[0] if notifies else None
        fragment_event = next((e for e in notifies[1:] if e.get("bytes", 0) > driver.LINK_PROOF_BYTES + 4), None)
        if not notifies or not fragment_event:
            fail(problems, f"{serial}: telemetry has no proof/probe notify events (missing events)")
            continue
        if proof_event.get("bytes", 0) != driver.LINK_PROOF_BYTES + 4:
            fail(problems, f"{serial}: first notify is {proof_event.get('bytes')} bytes, "
                           f"expected the {driver.LINK_PROOF_BYTES}-byte proof (+4 header)")
            continue
        if not (connects[0] <= int(proof_event["epoch_ms"]) <= disc[0]):
            fail(problems, f"{serial}: notify events are not inside their connect→disconnect burst")
            continue
        lo16, hi16 = interval_brackets([proof_pairs], int(proof_event["epoch_ms"])) \
            if proof_pairs else (0, -1)
        lo4096, hi4096 = interval_brackets([probe_pairs], int(fragment_event["epoch_ms"])) \
            if probe_pairs else (0, -1)
        lo, hi = max(lo16, lo4096), min(hi16, hi4096)
        anchor_problems = []
        if not (lo <= hi):
            fail(anchor_problems, f"origin intersection empty: 16B [{lo16:.0f},{hi16:.0f}] ∩ "
                                  f"4096B [{lo4096:.0f},{hi4096:.0f}]")
        mid = (lo + hi) / 2 if lo <= hi else None
        uncertainty = (hi - lo) / 2 + 1 if lo <= hi else None
        width = hi - lo if lo <= hi else None
        dis_phone = disc[0]
        dis_lo, dis_hi = (dis_phone - hi, dis_phone - lo) if lo <= hi else (None, None)
        checks = {}
        if dis_lo is not None and host["last_exchange_message_at_ms"] is not None and host["exit_at_ms"] is not None:
            checks["release_after_last_exchange"] = dis_lo >= host["last_exchange_message_at_ms"]
            checks["within_bound_of_own_exit"] = dis_hi <= host["exit_at_ms"] + declared_bound_ms
            stop = stop_map.get(serial)
            if stop is None:
                fail(anchor_problems, "fixture-stop markers missing for this serial")
            else:
                checks["stop_after_disconnect"] = stop["stop_min_phone_ms"] > dis_phone
                checks["stop_after_bound_plus_uncertainty"] = \
                    stop["stop_min_phone_ms"] >= hi + host["exit_at_ms"] + declared_bound_ms
        else:
            fail(problems, f"{serial}: cannot map the release interval (missing trace/telemetry parts)")
        # F-2: the precise anchor problems propagate UNCONDITIONALLY (an empty intersection keeps its
        # specific reason even when the release interval could not be mapped), verdict unchanged.
        if anchor_problems:
            fail(problems, *(f"{serial}: {p}" for p in anchor_problems))
        phone = {
            "transport_path": transport_path.name,
            "transport_sha256": file_sha256(transport_path),
            "serial_hint": serial,
            "run_id": transport.get("run_id"),
            "peer_sha256_prefix": peer.get("peer_sha256_prefix"),
            "connect_phone_ms": connects[0], "disconnect_phone_ms": dis_phone,
            "proof_notify": proof_event, "first_fragment_notify": fragment_event,
            "origin_interval": {"lo": lo, "hi": hi, "width_ms": width,
                                "midpoint": mid, "uncertainty_ms": uncertainty,
                                "brackets": {"proof16": [lo16, hi16], "probe4096": [lo4096, hi4096]}},
            "dis_trace_interval": [dis_lo, dis_hi],
            "checks": checks,
            "clean_telemetry": all([transport.get("final") is True,
                                    transport.get("final_after_service_stopped") is True,
                                    transport.get("clean") is True,
                                    transport.get("truncated") is False,
                                    transport.get("peers_dropped") == 0,
                                    peer.get("truncated") is False]),
        }
        if not phone["clean_telemetry"]:
            fail(problems, f"{serial}: transport telemetry is not final/clean/untruncated")
        phones.append(phone)
    same_run = len({p.get("run_id") for p in phones}) == 1 and \
        len({p.get("peer_sha256_prefix") for p in phones}) == 1
    if len(phones) == 2 and not same_run:
        fail(problems, "phones do not share one run id / one peer prefix (same central)")
    verdict = "PASS" if not problems and len(phones) == 2 and \
        all(p["checks"].get(k) is True for p in phones
            for k in ("release_after_last_exchange", "within_bound_of_own_exit",
                      "stop_after_disconnect", "stop_after_bound_plus_uncertainty")) else "FAIL"
    document = {
        "what": "OTP-23 Mac M-08 phone-side release proof (in-band causal anchor, P0-A era)",
        "verdict": verdict, "problems": problems,
        "scenario": scenario, "lifecycle_dir": str(out_dir), "run_id": (phones[0] or {}).get("run_id"),
        "declared_bound_ms": declared_bound_ms,
        "release_gate": "windows-only (P0-A): the Mac trace release count (usually 0) stays recorded "
                        "honestly in the scenario JSON; this phone-side proof is the release evidence",
        "target_teardown_scenario": target_teardown,
        "creditability": "target_evidence" if target_teardown else "diagnostic_only_not_creditable",
        "target_teardown_note": "an ABORTED run's cleanup exits (e.g. lifecycle-1) satisfy nothing: "
                                "this flag is true only for a run whose own scenario JSON recorded "
                                "trace teardown checks — replays are diagnostic only, never creditable",
        "absolute_mac_anchor": "none — the anchor is the host trace's own origin interval; no Mac "
                               "wall epoch is asserted, no PC/Windows clock offset is used",
        "phones": phones,
        "inputs": {"trace_path": trace_path.name, "trace_sha256": file_sha256(trace_path),
                   "scenario_json_path": scenario_path.name, "scenario_json_sha256": file_sha256(scenario_path),
                   "scenario_matrix_core_sha256": module_sha256(core, "scenario_matrix_core"),
                   "stops": stops,
                   "corroboration": corroborate},
        "helper_self_sha256": SELF_SHA,
        "engineered_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return document


def base64_len(size):
    """base64 length of `size` bytes without padding assumptions (driver uses standard base64)."""
    return (size + 2) // 3 * 4


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def module_sha256(module, name=None):
    try:
        return file_sha256(pathlib.Path(module.__file__))
    except (OSError, TypeError, AttributeError):
        return "unavailable"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lifecycle-dir", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--transport", action="append", required=True, type=pathlib.Path)
    parser.add_argument("--stops", required=True, type=pathlib.Path,
                        help="JSON list: serial, stop_min_phone_ms, stop_max_phone_ms, method")
    parser.add_argument("--declared-bound-ms", type=float, default=30000.0)
    parser.add_argument("--corroborate", type=pathlib.Path, help="clock-offset-mac.json (corroboration only)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    transports = []
    for path in args.transport:
        transports.append((path, json.loads(path.read_text(encoding="utf-8"))))
    stops = json.loads(args.stops.read_text(encoding="utf-8"))
    corroborate = None
    if args.corroborate:
        corroborate = {"path": args.corroborate.name, "sha256": file_sha256(args.corroborate),
                       "document": json.loads(args.corroborate.read_text(encoding="utf-8")),
                       "note": "CORROBORATION ONLY: known ±16-17 s raw diagnostic (date +N truncation "
                               "bias era); never acceptance proof"}
    document = analyse(args.lifecycle_dir, args.scenario, transports, stops,
                       args.declared_bound_ms, corroborate)
    target = pathlib.Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(f"mac-release-bound: {document['verdict']} ({args.scenario}) -> {target}")
    return 0 if document["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())