"""OTP-23 package1 pre-run phone clock probe (stdlib only, run on the PC before measured runs).

Measures the phone-minus-PC wall-clock offset for each serial by sampling `adb shell date +%s%3N`
(UTC epoch ms) repeatedly. Each sample treats the phone stamp as taken at the MIDPOINT of the adb
roundtrip; the per-sample latency/2 is folded into the stated uncertainty, so the numbers stay honest.
Only the median offset, min/max envelope and the stated uncertainty are used downstream; raw pairs are
kept for audit. Run again at the end of a long batch if drift matters.

    python scripts/pc_phone_clock_probe.py --serials 2be02e41,864a3db0 --out evidence/.../clock-offset.json
"""

import argparse
import json
import pathlib
import statistics
import subprocess
import time
from datetime import datetime, timezone

SAMPLES = 9


def phone_epoch_ms(serial):
    """(pc_midpoint_s, latency_s, phone_ms|None) for one adb date roundtrip."""
    started = time.time()
    out = subprocess.run(["adb", "-s", serial, "shell", "date", "+%s%3N"],
                         capture_output=True, text=True, timeout=15)
    finished = time.time()
    text = out.stdout.strip()
    if out.returncode != 0 or not text.isdigit():
        return (started + finished) / 2, finished - started, None
    value = int(text)
    if value > 10**12:  # epoch ms (2026 ~= 1.79e12) - %3N supported on the phone
        return (started + finished) / 2, finished - started, value
    if value > 10**9:  # seconds epoch only (%3N unsupported): whole seconds, honest 1 s resolution
        return (started + finished) / 2, finished - started, value * 1000
    return (started + finished) / 2, finished - started, None


def probe(serial, samples=SAMPLES):
    raw = []
    for _ in range(samples):
        midpoint, latency, phone = phone_epoch_ms(serial)
        raw.append({"pc_mid_ms": int(round(midpoint * 1000)), "latency_ms": int(round(latency * 1000)),
                    "phone_ms": phone})
        time.sleep(0.2)
    usable = [r for r in raw if r["phone_ms"] is not None]
    if len(usable) < 3:
        return {"serial": serial, "available": False,
                "reason": f"only {len(usable)} of {samples} samples readable",
                "raw": raw}
    offsets = [r["phone_ms"] - r["pc_mid_ms"] for r in usable]
    latency = max(r["latency_ms"] for r in usable)
    spread_half = (max(offsets) - min(offsets)) / 2
    resolution = 1000 if any(r["phone_ms"] % 1000 == 0 for r in usable) else 100
    return {"serial": serial, "available": True, "samples": len(usable),
            "offset_phone_minus_pc_ms_median": statistics.median(offsets),
            "offset_min_ms": min(offsets), "offset_max_ms": max(offsets),
            "uncertainty_ms": int(round(spread_half + latency / 2 + resolution / 2)),
            "method": "phone stamp at adb roundtrip midpoint; uncertainty = spread/2 + max latency/2 "
                      "+ phone resolution/2",
            "raw": raw}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serials", required=True, help="comma-separated phone serials")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    serials = [s for s in args.serials.split(",") if s]
    document = {"started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "probes": [probe(serial) for serial in serials]}
    document["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    target = pathlib.Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(json.dumps(document, indent=2))


if __name__ == "__main__":
    main()