"""Rapid-relaunch control for the Windows access_denied investigation (direct launch, synthetic bytes only).

Runs two host processes back to back: FIRST connects the phones selected by --first, exits through stdin EOF; after --gap-s
SECOND connects the phones selected by --second. Phones are selected by the first characters of the SHA-256 of their
advertised label (labels are never recorded and they rotate between fixture sessions; ``--discover`` prints the current hashes), or by the
tokens ``any1`` (first discovered device) and ``all`` (every device found, up to two).

    python scripts/relaunch_control.py --discover --host-binary HB --out-dir OUT
    python scripts/relaunch_control.py --first a1b2c3d4 --second a1b2c3d4,e5f6a7b8 --gap-s 1 --host-binary HB --out-dir OUT

Not an acceptance driver: it only records what each connect returned and which trace lines the host wrote.
"""

import argparse
import json
import os
import pathlib
import time
from datetime import datetime, timezone

import scenario_matrix as sm
import scenario_matrix_core as core


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def run_phase(binary, origin, out_dir, name, wanted, discover_s, discover_only=False):
    trace = out_dir / f"{name}.host-trace.log"
    trace.unlink(missing_ok=True)
    record = {"phase": name, "started_utc": utc(), "wanted": wanted, "devices": {}, "connects": []}
    host = sm.DirectHost([binary, origin], dict(os.environ, VELES_OTP23_TRACE=str(trace)), 0)
    try:
        ready = host.reply(host.request("hello"), 20)
        host.api_version = (ready or {}).get("api_version")
        started = host.reply(host.request("start_scan", service_uuid=sm.VELES_SERVICE), 20)
        record["scan"] = core.safe_reply(started)
        deadline = time.monotonic() + discover_s
        found = {}
        while time.monotonic() < deadline:
            frames = host.events_of("device_found", 1.0, count=len(found) + 1)
            for frame in frames:
                found[frame["device_handle"]] = core.label_hash(frame.get("label"))[:8]
            if wanted == ["any1"] and len(found) >= 1:
                break
            if wanted == ["all"] and len(found) >= 2:
                break
            if wanted not in (["any1"], ["all"]) and wanted and all(any(h == w for h in found.values()) for w in wanted):
                break
            if discover_only and len(found) >= 2:
                break
        record["devices"] = sorted(found.values())
        if discover_only:
            return record
        host.reply(host.request("stop_scan", scan_handle=started["scan_handle"]), 20)
        if wanted == ["any1"]:  # tokens: first discovered device / every device found; otherwise label hash prefixes
            wanted = sorted(found.values())[:1]
        elif wanted == ["all"]:
            wanted = sorted(found.values())
        chosen = [(handle, label) for handle, label in found.items() if label in wanted]
        record["connect_started_utc"] = utc()
        ids = [(host.request("connect", device_handle=handle), label) for handle, label in chosen]
        for request_id, label in ids:
            reply = host.reply(request_id, 70)
            record["connects"].append({"label_hash8": label, "reply": core.safe_reply(reply), "at_utc": utc()})
        record["missing_wanted"] = sorted(set(wanted) - {label for _, label in chosen})
        host.proc.stdin.close()  # EOF, not shutdown
        try:
            record["exit_code"] = host.proc.wait(timeout=15)
        except Exception:  # noqa: BLE001 - recorded, then reaped in finally
            record["exit_code"] = "timeout"
        record["exited_utc"] = utc()
    finally:
        host.kill()
        record["finished_utc"] = utc()
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host-binary", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--first", default="", help="comma-separated label hash prefixes (8 hex) for the first process")
    ap.add_argument("--second", default="", help="same for the second process")
    ap.add_argument("--gap-s", type=float, default=1.0)
    ap.add_argument("--discover-s", type=float, default=30.0)
    args = ap.parse_args()
    from macos_install import read_identity
    origin = read_identity()["origin"]
    out_dir = pathlib.Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=False)
    result = {"host_binary_sha256": core.file_sha256(args.host_binary), "args": vars(args), "phases": []}
    if args.discover:
        result["phases"].append(run_phase(args.host_binary, origin, out_dir, "discover", [], args.discover_s, True))
    else:
        first = [h for h in args.first.split(",") if h]
        second = [h for h in args.second.split(",") if h]
        result["phases"].append(run_phase(args.host_binary, origin, out_dir, "first", first, args.discover_s))
        result["first_exited_utc"] = result["phases"][0].get("exited_utc")
        time.sleep(args.gap_s)
        result["phases"].append(run_phase(args.host_binary, origin, out_dir, "second", second, args.discover_s))
    (out_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({p["phase"]: [(c["label_hash8"], c["reply"].get("type"), c["reply"].get("stage")) for c in p["connects"]]
                      or p["devices"] for p in result["phases"]}))


if __name__ == "__main__":
    main()
