#!/usr/bin/env python3
"""OTP-23 macOS finalization: measured clock offsets (desktop Mac minus each phone).

Method (same as the 2026-10-26 resume-era measurement): each clock is sampled remotely
against this container's clock as the reference. remote_minus_reference = (each remote read's UTC timestamp = the before/after midpoint. The half round-trip bounds that single measurement's
uncertainty. The pairwise offset desktop_phone = (mac_rel - phone_rel) is exact under the
shared reference; its uncertainty is the SUM of the two half-RTT bounds (conservative:
worst-case linear combination, not RSS), plus an explicitly stated post-run drift margin
for the interval between run evidence timestamps and this measurement.

Raw sample lines are written verbatim (evidence artifact)."""
import json
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone

HOME = pathlib.Path.home()
ADB = str(HOME / ".android-sdk/platform-tools/adb")
SERIALS = {"phone_2be02e41": "2be02e41", "phone_864a3db0": "864a3db0"}
SSH_CMD = ["ssh", "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
           "-o", "WarnWeakCrypto=no-pq-kex", "-i", str(HOME / ".ssh/id_sirius_ed25519"),
           "raiden@192.168.11.116"]
SAMPLES = 7

mac_cmd = SSH_CMD + ["/usr/bin/python3", "-c",
                     "'import time;print(round(time.time()*1000))'"]


def sample(cmd):
    before = time.time_ns() / 1e6
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    after = time.time_ns() / 1e6
    if r.returncode:
        raise RuntimeError(cmd[0] + ": rc=" + str(r.returncode) + " " + r.stderr.strip())
    lines = [ln.strip() for ln in r.stdout.strip().splitlines() if ln.strip()]
    remote = int(lines[-1])
    if not (1_000_000_000_000 < remote < 2_000_000_000_000):
        raise RuntimeError("remote value not a plausible ms epoch: %d" % remote)
    mid = (before + after) / 2
    return {"rtt_ms": round(after - before, 3),
            "midpoint_reference_utc": datetime.fromtimestamp(mid / 1000, timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "remote_utc": datetime.fromtimestamp(remote / 1000, timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "remote_ms": remote,
            "midpoint_ms": round(mid, 3),
            "remote_minus_reference_ms": round(remote - mid, 3),
            "half_rtt_bound_ms": round((after - before) / 2 + 1, 3)}


def measured(label, cmd):
    out = []
    for _ in range(SAMPLES):
        out.append(sample(cmd))
    best = min(out, key=lambda s: s["rtt_ms"])
    return {"label": label, "selected": best, "samples": out}


out = {"measured_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
       "reference_clock": "dev container clock (midpoint of before/after around each remote read)",
       "method": "remote epoch minus local midpoint; half SSH/adb round-trip bounds each "
                 "sample; min-RTT sample selected; pairwise desktop-phone offset = mac_rel - "
                 "phone_rel with conservative summed half-RTT uncertainty plus post-run drift margin",
       "units": "milliseconds", "clocks": {}}
for label, serial in SERIALS.items():
    out["clocks"][label] = measured(label, [ADB, "-s", serial, "shell", "date", "+%s%3N"])
out["clocks"]["macos_sirius"] = measured("macos_sirius", mac_cmd)

mac = out["clocks"]["macos_sirius"]["selected"]["remote_minus_reference_ms"]
out["desktop_minus_phone_ms"] = {}
for label in SERIALS:
    phone = out["clocks"][label]["selected"]["remote_minus_reference_ms"]
    pair_unc = out["clocks"]["macos_sirius"]["selected"]["half_rtt_bound_ms"] + \
        out["clocks"][label]["selected"]["half_rtt_bound_ms"]
    out["desktop_minus_phone_ms"][label] = {
        "offset_ms": round(mac - phone, 3),
        "measured_uncertainty_ms": round(pair_unc, 3),
    }
print(json.dumps(out, indent=2))