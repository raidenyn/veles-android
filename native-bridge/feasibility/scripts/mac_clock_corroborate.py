"""OTP-23 Mac clock CORROBORATION probe (parent code-phase decision, 2026-10-28).

Fixes the known defects of the retained mac-resume-2026-10-28 helper (mac_phone_clock_probe.py,
whose raw output stays pinned as a BIASED raw diagnostic — NOT acceptance proof):
  - macOS `date +%s` truncation left a 500 ms bias uncovered → the Mac clock is now read in
    MILLISECONDS (ssh python3 `time.time()`), so the stamp needs no assumed centring;
  - only rounded midpoints/latencies were stored → raw `started`/`finished` (seconds and ms) are
    recorded for every sample;
  - no self-provenance → the helper embeds its own runtime sha256 in the output.

ROLE: CORROBORATION ONLY. Its uncertainty is still measured in whole seconds (one ssh/adb process
per sample over WireGuard) — the ±16-17 s numbers cannot carry a 30 s release bound. The
ACCEPTANCE proof for the M-08 release bound is the in-band causal anchor in
scripts/mac_release_bound.py (origin interval from the host's own trace + phone telemetry, no
absolute Mac anchor). This probe's composed phone-minus-Mac offset is recorded only to show
consistency with that anchor and for the fixture-stop bracket readbacks; a stale PC clock is never
used.

Usage:  python3 scripts/mac_clock_corroborate.py --serials 2be02e41,864a3db0 --out out.json
Python 3.9+ stdlib only; requires adb and the given ssh key/host (no device work was performed in
the code phase — this helper ships for the planned retake).
"""

import argparse
import datetime
import hashlib
import json
import pathlib
import statistics
import subprocess
import sys
import time
from datetime import timezone

SELF_SHA = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
SAMPLES = 9
SSH_CMD = ["ssh", "-i", "/home/dev/.ssh/id_sirius_ed25519",
           "raiden@192.168.11.116", "python3", "-c", "import time; print(int(time.time()*1000))"]


def phone_epoch_ms(serial):
    started = time.time()
    out = subprocess.run(["adb", "-s", serial, "shell", "date", "+%s%3N"],
                         capture_output=True, text=True, timeout=30)
    finished = time.time()
    return started, finished, out.stdout.strip().splitlines()[-1] if out.stdout.strip() else "", \
        out.returncode


def mac_epoch_ms():
    started = time.time()
    out = subprocess.run(SSH_CMD, capture_output=True, text=True, timeout=30)
    finished = time.time()
    return started, finished, out.stdout.strip().splitlines()[-1] if out.stdout.strip() else "", \
        out.returncode


def samples(fn, arg=None, count=SAMPLES):
    raw = []
    for _ in range(count):
        started, finished, text, code = fn(arg) if arg is not None else fn()
        stamp, origin = None, "unusable"
        if code == 0 and text.isdigit():
            value = int(text)
            if value > 10**12:
                stamp = value                      # mac python3 ms / phone ms stamps: ms resolution
                origin = "ms_epoch"
            elif value > 10**9:
                stamp = value * 1000 + 500         # whole-seconds epoch: centred bracket [s, s+1000)
                origin = "whole_seconds_centred"
        raw.append({"started": started, "finished": finished,
                    "started_ms": int(round(started * 1000)), "finished_ms": int(round(finished * 1000)),
                    "latency_ms": int(round((finished - started) * 1000)), "stamp_ms": stamp,
                    "raw_text": text,
                    "stamp_origin": origin})
    usable = [row for row in raw if row["stamp_ms"] is not None]
    return raw, usable


def summarise(raw, usable, label):
    offsets = [row["stamp_ms"] - row["started_ms"] for row in usable]
    offsets_mid = [row["stamp_ms"] - (row["started_ms"] + row["finished_ms"]) / 2 for row in usable]
    max_latency = max(row["latency_ms"] for row in usable)
    spread_half = (max(offsets_mid) - min(offsets_mid)) / 2
    # Reviewer-reported understatement fix (Kimi §9 nit): a whole-seconds fallback stamp is centred
    # at s*1000+500 while the true time lies in [s, s+1000) — ±500 ms MUST be added to this
    # component's uncertainty, otherwise the recorded bound under-states by 500 ms.
    fallback = any(row.get("stamp_origin") == "whole_seconds_centred" for row in usable)
    resolution = ("ms stamps plus ±500 ms whole-seconds centring term" if fallback
                  else "ms stamps (no assumed centring)")
    centring = 500 if fallback else 0
    return {"component": label, "samples": len(usable), "usable_stamps": len(usable),
            "offset_stamp_minus_midpoint_ms_median": statistics.median(offsets_mid),
            "offset_stamp_minus_started_ms_median": statistics.median(offsets),
            "offset_min_ms": min(offsets_mid), "offset_max_ms": max(offsets_mid),
            "max_latency_ms": max_latency,
            "whole_seconds_fallback_used": fallback,
            "resolution": resolution,
            "uncertainty_ms": int(round(spread_half + max_latency / 2 + centring)),
            "method": "stamp at roundtrip midpoint; uncertainty = spread/2 + max latency/2 "
                      "+ (500 ms centring term when whole-seconds stamps occur)"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serials", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    mac_raw, mac_usable = samples(mac_epoch_ms)
    mac_summary = summarise(mac_raw, mac_usable, "mac-minus-local") if len(mac_usable) >= 3 else \
        {"component": "mac-minus-local", "available": False,
         "reason": f"only {len(mac_usable)} of {SAMPLES} readable"}
    probes = []
    for serial in [s for s in args.serials.split(",") if s]:
        phone_raw, phone_usable = samples(phone_epoch_ms, serial)
        doc = {"serial": serial, "phone_raw": phone_raw, "mac_raw": mac_raw}
        if len(phone_usable) < 3 or "available" in mac_summary:
            doc.update({"available": False, "reason": f"phone {len(phone_usable)} or mac component short"})
        else:
            phone_summary = summarise(phone_raw, phone_usable, f"phone-{serial}-minus-local")
            doc.update({"available": True, "phone_component": phone_summary,
                        "mac_component": mac_summary,
                        "offset_phone_minus_mac_ms_median": int(round(
                            phone_summary["offset_stamp_minus_midpoint_ms_median"] -
                            mac_summary["offset_stamp_minus_midpoint_ms_median"])),
                        "uncertainty_ms": phone_summary["uncertainty_ms"] + mac_summary["uncertainty_ms"],
                        "method": "phone-minus-local minus mac-minus-local (same midpoint clock); "
                                  "conservative uncertainty = sum of both components"})
        probes.append(doc)
    document = {
        "what": "OTP-23 Mac clock CORROBORATION probe (fixed ms variant)",
        "role": "corroboration_only_not_acceptance_proof",
        "acceptance_proof": "scripts/mac_release_bound.py in-band causal anchor; this probe must "
                            "never carry a release bound (its uncertainty is whole seconds)",
        "self_sha256": SELF_SHA,
        "started_utc": datetime.datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "probes": probes,
        "finished_utc": datetime.datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    target = pathlib.Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(json.dumps(document, indent=2))


if __name__ == "__main__":
    main()