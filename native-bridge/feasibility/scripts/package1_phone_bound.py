"""OTP-23 package1 link-release bound analysis (evidence-only; the bound was pre-declared in PLAN.md).

For each per-scenario run directory, correlates:
  - the host's own trace: last `disconnect requested epoch_ms` (PC UTC ms) and `exit code=0` (offset ms),
  - the phone ConnectionState history rows (device-local wall clock) captured immediately after the run,
  - the clock offset (clock-offset.json of this evidence dir),
and prints the phone DISCONNECTED-minus-host-exit gap against the pre-declared 30 s bound.

    python scripts/package1_phone_bound.py <evidence_dir> [bound_s]
"""

import json
import pathlib
import re
import sys

DEVICE_TZ_OFFSET_MS = 7 * 3600 * 1000  # measured: device 07:39 == 00:39Z on this phone pair


def trace_exit_utc_ms(trace_text):
    """(exit_epoch_ms_estimate, last_disconnect_requested_epoch_ms) for the host trace.

    The trace uses trace-relative ms offsets; the only PC-absolute anchor is the
    `disconnect requested epoch_ms`. The exit epoch is interpolated from the exit offset minus the
    anchor's own offset."""
    stamps = re.findall(r"\[\s*(\d+)ms pid=\d+\] conn \d+ disconnect requested epoch_ms=(\d+)", trace_text)
    dis_offset = int(stamps[-1][0]) if stamps else None
    dis_epoch = int(stamps[-1][1]) if stamps else None
    m = re.search(r"\[\s*(\d+)ms pid=\d+\] exit code=(-?\d+)", trace_text)
    exit_offset = int(m.group(1)) if m else None
    exit_at = (dis_epoch + (exit_offset - dis_offset)) if (dis_offset is not None and exit_offset is not None) else None
    return exit_at, dis_epoch


def phone_disconnect_rows(dumpsys_text):
    rows = re.findall(r"^\s*(\d\d-\d\d \d\d:\d\d:\d\d\.\d\d\d) (CONNECTED|DISCONNECTED)\s+(\S+)(.*)$",
                      dumpsys_text, re.M)
    parsed = []
    for stamp, state, addr, rest in rows:
        parsed.append({"datestamp": stamp, "state": state, "addr": addr, "reason": rest.strip()})
    return parsed


def row_epoch_ms(row, offset_ms):
    """Phone wall clock -> PC-comparable UTC epoch estimate. The measured offset field is
    `offset_phone_minus_pc_ms_median = phone_epoch - pc_epoch` (pc_phone_clock_probe.py contract),
    so the conversion SUBTRACTS it: `pc_epoch = phone_epoch - offset_ms`, never an addition."""
    stamp = row["datestamp"]  # MM-DD HH:MM:SS.mmm in the phone's LOCAL wall clock
    month, day = int(stamp[:2]), int(stamp[3:5])
    hh, mm = int(stamp[6:8]), int(stamp[9:11])
    ss, ms = int(stamp[12:14]), int(stamp[15:18])
    from datetime import datetime, timezone
    as_if_utc = datetime(2026, month, day, hh, mm, ss, ms * 1000, tzinfo=timezone.utc)
    return as_if_utc.timestamp() * 1000 - DEVICE_TZ_OFFSET_MS - offset_ms


def analyse(run_dir, bound_s, offsets):
    run = pathlib.Path(run_dir)
    trace_file = next(iter(sorted(run.glob("*.host-trace.log"))), None)
    problems = []
    if trace_file is None:
        return {"run": run.name, "available": False, "reason": "no host trace"}
    text = trace_file.read_text(errors="replace")
    exit_at, dis_req = trace_exit_utc_ms(text)
    if exit_at is None or dis_req is None:
        return {"run": run.name, "available": False,
                "reason": f"trace lacks exit/disconnect epoch anchor (dis={dis_req})"}
    phones = {}
    dump_files = {}
    for serial, phone in (("2be02e41", "6T"), ("864a3db0", "10Pro")):
        candidates = list(run.glob(f"dumpsys-{serial}-*.txt"))
        usable = [f for f in candidates if "not recognized" not in f.read_text(errors="replace")[:200]]
        dump_files[phone] = usable
        if not usable:
            phones[phone] = "no readable capture"
            continue
        # latest capture that still shows the run's event window wins
        best = None
        for f in sorted(usable, key=lambda p: p.stat().st_mtime, reverse=True):
            rows = phone_disconnect_rows(f.read_text(errors="replace"))
            if rows:
                best = (f.name, rows)
                break
        rows = best[1] if best else []
        offset = offsets[phone]["offset_phone_minus_pc_ms_median"]
        events = [(r, row_epoch_ms(r, offset)) for r in rows]
        # Pair the run's OWN burst: a CONNECTED whose link closes with a DISCONNECTED, where the
        # CONNECTED lands inside the run's own window (the anchor epoch identifies the run; connects
        # precede the anchor by the scenario's own seconds) and its DISCONNECTED follows the anchor.
        connects = [(r, e) for r, e in events if r["state"] == "CONNECTED"]
        chosen = None
        for crep, cepoch in connects:
            # the run's own CONNECT happens inside the scenario: a few seconds BEFORE the disconnect
            # request (trace: connect replies at ~8 s, anchor at the release, ~15 s after start)
            if not (1500 <= dis_req - cepoch <= 45000):
                continue
            following = [(r, e) for r, e in events
                         if r["state"] == "DISCONNECTED" and 0 <= e - dis_req <= 35000
                         and e >= cepoch]
            if following:
                best_dis = min(following, key=lambda pair: pair[1])
                chosen = (crep, best_dis)
                break
        last, chosen_epoch = chosen[1] if chosen else (None, None)
        gap_ms = (chosen_epoch - exit_at) if chosen_epoch is not None else None
        phones[phone] = {"capture_file": best[0] if best else None,
                         "last_disconnect_device": last["datestamp"].split()[1] if last else None,
                         "gap_vs_host_exit_ms": gap_ms,
                         "bound_epoch_anchor_ms": dis_req,
                         "within_bound": gap_ms is not None and -offsets[phone]["uncertainty_ms"] <= gap_ms \
                             <= bound_s * 1000,
                         "burst_own_run_found": chosen is not None,
                         "rows_in_history": len(rows)}
        if last is None:
            problems.append(f"{phone}: the run's own CONNECTED->DISCONNECTED burst is not in the retained "
                            f"phone history (rotated out); a rerun with a fresh capture is required")
        elif gap_ms < 0:
            problems.append(f"{phone}: DISCONNECTED before host exit ({gap_ms / 1000:.1f}s)")
        elif gap_ms > bound_s * 1000:
            problems.append(f"{phone}: DISCONNECTED {gap_ms / 1000:.1f}s after host exit exceeds the "
                            f"{bound_s:.0f}s pre-declared bound")
    return {"run": run.name, "host_exit_utc_ms_estimate": exit_at, "anchor_disconnect_epoch_ms": dis_req,
            "phones": phones, "bound_s": bound_s, "problems": problems}


def main():
    evidence_dir = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    bound_s = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    probes = {p["serial"]: p for p in json.load(open(evidence_dir / "clock-offset.json"))["probes"]}
    offsets = {"6T": probes["2be02e41"], "10Pro": probes["864a3db0"]}
    runs = [d for d in sorted(evidence_dir.glob("run-*")) if d.is_dir() and not d.name.endswith("-stdout")]
    documents = [analyse(d, bound_s, offsets) for d in runs]
    out = evidence_dir / "phone-link-release-bound.json"
    out.write_text(json.dumps({"bound_s_predeclared": bound_s, "results": documents}, indent=2))
    for d in documents:
        print(f"== {d['run']}: " + (f"UNAVAILABLE ({d.get('reason')})" if not d.get("available", True)
                                    else json.dumps(d["phones"], default=str)[:220] +
                                    (f" PROBLEMS: {d['problems']}" if d["problems"] else " OK")))
    print(f"-> {out}")


if __name__ == "__main__":
    main()