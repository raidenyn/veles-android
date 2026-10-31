"""Chrome-launched rapid-relaunch control (Windows interactive session, synthetic bytes only).

One Chrome, one connector tab. FIRST host: connect the phones selected by --first, then ``otp23.closePort()`` (the connector's
literal port.disconnect) and wait for the host to exit. After --gap-s a SECOND host is started through the same connector and
connects the phones selected by --second. Phones are selected by the first 8 hex characters of the SHA-256 of their advertised
label (labels are never recorded); use relaunch_control.py --discover to list them.

    python scripts/chrome_relaunch_control.py --first a1b2c3d4 --second a1b2c3d4,e5f6a7b8 --gap-s 1 --out-dir OUT

Not an acceptance driver: it records what each connect returned and the host trace. Run it from an interactive logon.
"""

import argparse
import json
import pathlib
import time
from datetime import datetime, timezone

import scenario_matrix as sm
import scenario_matrix_core as core


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def connect_labels(session, wanted, window_s):
    """Discover, then connect exactly the wanted label hashes; returns per-label safe replies (never raises on a failed connect)."""
    # Tokens: "any1" = the first discovered device, "all" = every device found (up to two); otherwise label hash prefixes.
    want = 1 if wanted == ["any1"] else 2 if wanted == ["all"] else len(wanted)
    found = session.discover(window_s, want=want, keep_scan=False)
    by_label = {core.label_hash(d.get("label"))[:8]: d for d in found}
    if wanted == ["any1"]:
        wanted = sorted(by_label)[:1]
    elif wanted == ["all"]:
        wanted = sorted(by_label)
    chosen = [(label, by_label[label]) for label in wanted if label in by_label]
    record = {"discovered": sorted(by_label), "missing_wanted": [w for w in wanted if w not in by_label], "connects": [],
              "connect_started_utc": utc()}
    targets = json.dumps([{"device_handle": d["device_handle"]} for _, d in chosen])
    replies = session.js(f"Promise.all({targets}.map(t => otp23.connectDevice(t)))", timeout=80) if chosen else []
    for (label, _), reply in zip(chosen, replies):
        record["connects"].append({"label_hash8": label, "reply": core.safe_reply(reply), "at_utc": utc()})
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--first", required=True)
    ap.add_argument("--second", required=True)
    ap.add_argument("--gap-s", type=float, default=1.0)
    ap.add_argument("--discover-s", type=float, default=30.0)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    from macos_install import read_identity
    identity = read_identity()
    out_dir = pathlib.Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=False)
    adapter = sm.make_adapter()
    session = sm.Session(adapter, out_dir / "host-trace.log", identity)
    result = {"args": vars(args), "chrome_version": None, "phases": []}
    try:
        result["ready_first"] = session.start()
        result["chrome_version"] = session.chrome.version.get("product")
        first = connect_labels(session, [h for h in args.first.split(",") if h], args.discover_s)
        first["phase"] = "first"
        result["phases"].append(first)
        session.js("otp23.closePort(); 1")
        gone = adapter.wait_gone(session.owned_pids(), 15.0)
        result["first_host_gone_after_s"] = gone
        result["first_host_gone_utc"] = utc()
        time.sleep(args.gap_s)
        session.js(f"otp23.connect({json.dumps(adapter.host_name)}); 1")
        ready = session.js("otp23.hello()")
        result["ready_second"] = core.safe_reply(ready)
        result["second_hello_utc"] = utc()
        second = connect_labels(session, [h for h in args.second.split(",") if h], args.discover_s)
        second["phase"] = "second"
        result["phases"].append(second)
    finally:
        result["finished_utc"] = utc()
        try:
            session.close()
        except Exception as error:  # noqa: BLE001 - recorded, not raised over the result
            result["close_error"] = type(error).__name__
        (out_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({p["phase"]: [(c["label_hash8"], c["reply"].get("type"), c["reply"].get("stage")) for c in p["connects"]]
                          for p in result["phases"]}))


if __name__ == "__main__":
    main()
