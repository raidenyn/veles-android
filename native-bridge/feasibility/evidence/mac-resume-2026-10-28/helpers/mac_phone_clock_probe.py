"""OTP-23 Mac-resume clock helper (stdlib only; runs on the Linux side alongside the PC's adb).

Measures the phone-minus-MAC wall-clock offset WITHOUT adb on the Mac and WITHOUT reusing any
stale Windows offset. Existing phone tools name the desktop clock "PC" (they stamp `time.time()`
of the machine that runs them), so this helper measures each component against the LOCAL Linux
clock (the measuring stick cancels in the subtraction):

  A. phone-minus-LOCAL: `adb -s SERIAL shell date +%s%3N` midpoints (same method as
     pc_phone_clock_probe.py: phone stamp = adb roundtrip midpoint).
  B. mac-minus-LOCAL: ssh date midpoints over the exact SSH key/host the run uses (macOS `date`
     has whole-second resolution: stamp_ms = seconds*1000, honest 500 ms resolution term).

Composed offset = median(A) - median(B). Stated uncertainty = spreadHalf + maxLatency/2 +
resolution/2 for A, plus the SAME terms for B (conservative sum; nothing averaged away). Raw
pairs for both components are kept for audit. This binds the MAC clock explicitly; no absolute
Mac trace anchor is invented.
"""

import argparse
import json
import pathlib
import statistics
import subprocess
import time
from datetime import datetime, timezone

SAMPLES = 9
SSH_CMD = ["ssh", "-i", "/home/dev/.ssh/id_sirius_ed25519",
           "raiden@192.168.11.116", "date", "+%s"]


def phone_epoch_ms(serial):
    started = time.time()
    out = subprocess.run(["adb", "-s", serial, "shell", "date", "+%s%3N"],
                         capture_output=True, text=True, timeout=30)
    finished = time.time()
    return started, finished, out.stdout.strip(), out.returncode


def mac_epoch_ms():
    started = time.time()
    out = subprocess.run(SSH_CMD, capture_output=True, text=True, timeout=30)
    finished = time.time()
    return started, finished, out.stdout.strip().splitlines()[-1] if out.stdout.strip() else "", \
        out.returncode


def component(fn, arg, kind, samples=SAMPLES):
    raw = []
    for _ in range(samples):
        started, finished, text, code = fn(arg) if arg is not None else fn()
        stamp = None
        if code == 0 and text.isdigit():
            value = int(text)
            if value > 10**12:
                stamp = value
            elif value > 10**9:
                stamp = value * 1000  # whole seconds (macOS date): 1 s resolution
        raw.append({"loc_mid_ms": int(round((started + finished) / 2 * 1000)),
                    "latency_ms": int(round((finished - started) * 1000)), "stamp_ms": stamp})
    return raw


def summarise(usable, label):
    offsets = [r["stamp_ms"] - r["loc_mid_ms"] for r in usable]
    latency = max(r["latency_ms"] for r in usable)
    spread_half = (max(offsets) - min(offsets)) / 2
    resolution = 1000 if any(r["stamp_ms"] % 1000 == 0 for r in usable) else 100
    return {"component": label, "samples": len(usable),
            "offset_stamp_minus_local_ms_median": statistics.median(offsets),
            "offset_min_ms": min(offsets), "offset_max_ms": max(offsets),
            "max_latency_ms": int(round(latency)),
            "resolution_note": ("macOS date whole seconds -> 1 s resolution term" if
                                any(r["stamp_ms"] % 1000 == 0 for r in usable) else "ms resolution"),
            "uncertainty_ms": int(round(spread_half + latency / 2 + resolution / 2)),
            "method": "stamp at roundtrip midpoint; uncertainty = spread/2 + max latency/2 + resolution/2"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serials", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    mac_raw = component(mac_epoch_ms, None, "mac")
    mac_usable = [r for r in mac_raw if r["stamp_ms"] is not None]
    mac_summary = summarise(mac_usable, "mac-minus-local") if len(mac_usable) >= 3 else \
        {"component": "mac-minus-local", "available": False,
         "reason": f"only {len(mac_usable)} of {SAMPLES} readable"}
    probes = []
    for serial in [s for s in args.serials.split(",") if s]:
        ph_raw = component(phone_epoch_ms, serial, "phone")
        ph_usable = [r for r in ph_raw if r["stamp_ms"] is not None]
        doc = {"serial": serial, "phone_raw": ph_raw, "mac_raw": mac_raw}
        if len(ph_usable) < 3 or "available" in mac_summary:
            doc.update({"available": False,
                        "reason": f"phone {len(ph_usable)} or mac component short"})
        else:
            phone_summary = summarise(ph_usable, f"phone-{serial}-minus-local")
            doc.update({"available": True,
                        "phone_component": phone_summary,
                        "mac_component": mac_summary,
                        "offset_phone_minus_mac_ms_median": int(round(
                            phone_summary["offset_stamp_minus_local_ms_median"] -
                            mac_summary["offset_stamp_minus_local_ms_median"])),
                        "uncertainty_ms": phone_summary["uncertainty_ms"] +
                        mac_summary["uncertainty_ms"],
                        "method": "phone-minus-local minus mac-minus-local (both midpoints on the same "
                                  "local clock); conservative uncertainty = sum of components"})
        probes.append(doc)
    document = {"started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "what": "OTP-23 Mac-resume phone clock probe (MAC clock bound explicitly)",
                "probes": probes,
                "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    target = pathlib.Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(json.dumps(document, indent=2))


if __name__ == "__main__":
    main()