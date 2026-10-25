"""Physical two-phone synthetic size matrix on real links. Requires both OTP-06 peripherals
advertising the Veles service. Run from an interactive Windows user session, after
register-host.ps1:

    python scripts/physical_validate.py --flavour plain --out evidence/physical-plain.json

No simulated fallback: missing phones, failed setup, byte or digest mismatch, any case
failure, or a surviving owned host process fails the run (exit 1). Pre-existing
otp23-host processes are recorded and never touched; only the process this run owns must
be gone when the run ends (fail closed). Connection MTUs are read from the host trace
file (VELES_OTP23_TRACE) and reported keyed by the host connection token only; no
per-handle association is inferred, because connected events interleave across
concurrent connections. The wire protocol carries no MTU field. Additionally the plain
host must own no top-level windows and no network endpoints (measured, not asserted on
trust).

Sizes: 4, 178, 179, 180, 505..511, 1015..1017, 4095/4096, plus repeated 4096-byte records
sent concurrently on two distinct handles with per-handle expected digests, then
disconnect-one-survivor and graceful shutdown.
"""

import argparse
import base64
import hashlib
import json
import pathlib
import re
import subprocess
import time
import traceback
from datetime import datetime, timezone

from chrome_validate import (
    EXE_NAME,
    HOSTS,
    VELES_SERVICE,
    ChromeRun,
    host_processes,
    network_endpoints_of,
    scrub,
    top_level_windows_of,
    wait_all_gone,
    wrapper_of,
)

SIZES = [4, 178, 179, 180] + list(range(505, 512)) + [1015, 1016, 1017] + [4095, 4096]


def fixture(size, seed):
    """Deterministic synthetic record accepted by the OTP-06 SyntheticTransportFixture."""
    data = bytearray((i + seed) & 255 for i in range(size))
    data[:4] = b"VEL\x01"
    return bytes(data)


def echo(record):
    """Expected OTP-06 reply: the same record with the version byte 3 changed to 0x02."""
    return record[:3] + b"\x02" + record[4:]


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def source_revision():
    """Source revision of this checkout, for per-run metadata (best effort)."""
    repo = pathlib.Path(__file__).resolve().parents[3]
    try:
        revision = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip() != ""
        return {"commit": revision, "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return None


def file_sha256(path):
    """Hash identity of the running host executable (best effort; None if unreadable)."""
    if not path:
        return None
    literal = str(path).replace("'", "''")
    try:
        return (
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f"(Get-FileHash -LiteralPath '{literal}' -Algorithm SHA256).Hash"],
                capture_output=True, text=True, timeout=60, check=True,
            ).stdout.strip().lower() or None
        )
    except (OSError, subprocess.SubprocessError):
        return None


def parse_trace(path):
    """MTUs keyed by the host connection token, plus the send/reply timeline, from the
    host's own trace lines. Connected/subscribe lines are deliberately NOT associated
    with a conn token: under async operation they interleave across concurrent
    connections, so any handle mapping would be guesswork. Trace never contains record
    bytes. Line shapes:

    [       0ms pid=87052] start pid=87052
    [    1242ms pid=87052] conn 2 mtu=517 at 1242ms
    [    2956ms pid=87052] out len=160 type=connected ... connection_handle=600e...
    [    3543ms pid=87052] in  len=... type=send ... record_base64.len=12
    """
    mtu_by_conn = {}
    sends, replies = [], []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stamp = re.match(r"\[\s*(\d+)ms pid=\d+\]", line)
            at_ms = int(stamp.group(1)) if stamp else None
            mtu = re.search(r"\] conn (\d+) mtu=(\d+) ", line)
            if mtu:
                mtu_by_conn[int(mtu.group(1))] = int(mtu.group(2))
            if at_ms is None:
                continue
            send = re.search(r"type=send .*record_base64\.len=(\d+)", line)
            if send:
                sends.append({"at_ms": at_ms, "record_base64_len": int(send.group(1))})
            reply = re.search(r"type=message .*record_base64\.len=(\d+)", line)
            if reply:
                replies.append({"at_ms": at_ms, "record_base64_len": int(reply.group(1))})
    return {"mtu_by_conn": mtu_by_conn, "sends": sends, "replies": replies}


def run(args):
    out = pathlib.Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    trace = out.with_suffix(".trace.log")
    trace.unlink(missing_ok=True)
    exe = EXE_NAME[args.flavour]
    started = datetime.now(timezone.utc)
    result = {
        "flavour": args.flavour,
        "cases": [],
        "result": "FAIL",
        "started_utc": started.isoformat(timespec="seconds"),
        "source_revision": source_revision(),
        "negotiation_source": "host trace file (VELES_OTP23_TRACE); no MTU exists on the wire",
        "sizes": SIZES,
        "repeat_4096": args.repeat_4096,
    }
    chrome = None
    pid = None
    wrapper = None
    # Startup cleanup contract: never touch processes or profiles we do not own. Processes
    # already present are recorded and left alone; our own disposable profile is created by
    # ChromeRun and removed by chrome.close().
    pre_existing = {p["ProcessId"] for p in host_processes(exe)}
    result["pre_existing_host_processes_untouched"] = sorted(pre_existing)
    try:
        chrome = ChromeRun({"VELES_OTP23_TRACE": str(trace)}, medium=True)
        result["chrome_version"] = chrome.version["product"]
        ext = chrome.load_extension()["id"]
        _, session = chrome.open_page(f"chrome-extension://{ext}/connector.html")
        for _ in range(50):
            if chrome.js(session, "typeof otp23 === 'object'"):
                break
            time.sleep(0.1)
        js = lambda expression: chrome.js(session, expression, timeout=70)  # noqa: E731
        js(f"otp23.connect({json.dumps(HOSTS[args.flavour])}); 1")
        ready = js("otp23.hello()")
        require(ready.get("type") == "ready", ready)
        result["ready"] = {key: ready.get(key) for key in ("bridge_version", "platform", "api_version")}
        fresh = [p for p in host_processes(exe) if p["ProcessId"] not in pre_existing]
        require(len(fresh) == 1, f"expected exactly one new host process, saw {fresh}")
        proc = fresh[0]
        pid = proc["ProcessId"]
        wrapper = wrapper_of(pid)
        result["host_pid"] = pid
        result["host_wrapper_pid"] = wrapper
        result["host_executable"] = proc.get("ExecutablePath")
        result["host_exe_sha256"] = file_sha256(proc.get("ExecutablePath"))
        # Measured host hygiene (chrome_validate helpers): no visible windows for any flavour,
        # and a plain host owns exactly zero top-level windows and zero network endpoints.
        windows = top_level_windows_of(pid)
        require(not [w for w in windows if w["visible"]], f"host shows a window: {windows}")
        endpoints = network_endpoints_of(pid)
        require(not endpoints, f"host owns network endpoints: {endpoints}")
        if args.flavour == "plain":
            require(not windows, f"plain host owns top-level windows: {windows}")
        result["cases"].append({"case": "host-no-windows-no-listeners", "result": "PASS",
                                "top_level_windows": windows, "network_endpoints": endpoints})
        started_scan = js(f"otp23.startScan({json.dumps(VELES_SERVICE)})")
        require(started_scan.get("status") == "started", started_scan)
        devices = {}
        deadline = time.monotonic() + 30
        while len(devices) < 2 and time.monotonic() < deadline:
            for event in js("otp23.drainEvents()"):
                message = event["message"]
                if message["type"] == "device_found":
                    devices[message["device_handle"]] = message
            time.sleep(0.25)
        require(len(devices) >= 2, f"only {len(devices)} physical peripherals found")
        connected = js(f"Promise.all({json.dumps([{'device_handle': h} for h in list(devices)[:2]])}"
                       ".map(t => otp23.connectDevice(t)))")
        result["setup_replies"] = connected
        require(all(c.get("type") == "connected" for c in connected), connected)
        handles = [c["connection_handle"] for c in connected]
        require(len(set(handles)) == 2, "connection handles crossed")
        result["cases"].append({"case": "two-independent-connects", "result": "PASS"})
        stopped = js(f"otp23.stopScan({json.dumps(started_scan['scan_handle'])})")
        require(stopped.get("status") == "stopped", stopped)

        def exchange(active_handles, size, seeds):
            """Concurrent sends on the given handles; every per-handle expected digest must match."""
            js("otp23.drainEvents()")
            records = [fixture(size, seed) for seed in seeds]
            expected_b64 = {h: base64.b64encode(echo(r)).decode()
                            for h, r in zip(active_handles, records)}
            expected_digest = {h: hashlib.sha256(echo(r)).hexdigest()
                               for h, r in zip(active_handles, records)}
            requests = [{"h": h, "b": base64.b64encode(r).decode()}
                        for h, r in zip(active_handles, records)]
            replies = js(f"Promise.all({json.dumps(requests)}.map(x => otp23.request('send', "
                         "{connection_handle:x.h,record_base64:x.b})))")
            require(all(r.get("type") == "sent" for r in replies), replies)
            request_ids = [r["request_id"] for r in replies]
            require(len(set(request_ids)) == len(request_ids), "request ids reused")
            received = {}
            deadline = time.monotonic() + 12
            while len(received) < len(expected_digest) and time.monotonic() < deadline:
                for event in js("otp23.drainEvents()"):
                    message = event["message"]
                    require(message["type"] != "disconnected", message)
                    if message["type"] == "message":
                        handle = message["connection_handle"]
                        require(handle in expected_b64
                                and message["record_base64"] == expected_b64[handle],
                                f"foreign handle or byte mismatch on {handle}")
                        require(handle not in received, "duplicate response")
                        received[handle] = hashlib.sha256(
                            base64.b64decode(message["record_base64"])).hexdigest()
                time.sleep(0.05)
            require(len(received) == len(expected_digest),
                    f"only {len(received)}/{len(expected_digest)} responses")
            mismatched = [h for h in expected_digest if received.get(h) != expected_digest[h]]
            require(not mismatched, f"per-handle digest mismatch: {mismatched}")
            return {"bytes_per_phone": size,
                    "per_handle": [{"connection_handle": h,
                                    "expected_sha256": expected_digest[h],
                                    "received_sha256": received[h]} for h in active_handles],
                    "request_ids": request_ids}

        # The full record-edge sweep plus repeated 4096-byte records, always concurrent on
        # the two distinct handles (seed 17+index for handle 0, 137+index for handle 1).
        for index, size in enumerate(SIZES + [4096] * args.repeat_4096):
            evidence = exchange(handles, size, [17 + index, 137 + index])
            result["cases"].append({"case": "concurrent-whole-records", "result": "PASS", **evidence})
        disconnected = js(f"otp23.disconnectDevice({json.dumps(handles[0])})")
        require(disconnected.get("type") == "disconnected", disconnected)
        rejected = js(f"otp23.request('send', {{connection_handle: {json.dumps(handles[0])}, "
                      f"record_base64: {json.dumps(base64.b64encode(fixture(4, 5)).decode())}}})")
        require(rejected.get("code") == "unknown_handle", rejected)
        result["cases"].append({"case": "disconnected-handle-rejects-send", "result": "PASS",
                                "code": rejected.get("code")})
        result["cases"].append({"case": "disconnect-one-other-still-exchanges", "result": "PASS",
                                **exchange(handles[1:], 4096, [219])})
        done = js("otp23.shutdown()")
        require(done.get("type") == "shutdown_complete", done)
        gone = wait_all_gone([pid, wrapper], 8)
        require(gone is not None, "owned host survived shutdown with a real link")
        result["cases"].append({"case": "shutdown-real-link", "result": "PASS",
                                "process_gone_seconds": gone})
        # Negotiation recorded from the host's own trace, keyed by connection token only.
        # Exactly two MTU observations are required (one per expected connection); an empty
        # mapping must fail, never vacuously pass. No per-handle mapping is inferred.
        parsed = parse_trace(trace)
        require(len(parsed["mtu_by_conn"]) == 2,
                f"expected exactly 2 connection-MTU observations, saw {parsed['mtu_by_conn']}")
        for token, mtu in parsed["mtu_by_conn"].items():
            require(185 <= mtu <= 517, f"implausible MTU for conn {token}: {mtu}")
        result["negotiation"] = {
            "source": "host trace (VELES_OTP23_TRACE); keyed by host connection token; "
                      "no MTU field exists on the wire; per-handle association not inferred",
            "per_conn": {
                str(token): {"mtu": mtu,
                             "poc_fragment_payload_bytes": min(mtu - 3, 512) - 4,
                             "frozen_fragment_payload_bytes": mtu - 7}
                for token, mtu in parsed["mtu_by_conn"].items()
            },
            "poc_fragment_payload_rule": "min(mtu - 3, 512) - 4",
            "frozen_fragment_payload_rule": "mtu - 7",
            "trace_sends": len(parsed["sends"]),
            "trace_replies": len(parsed["replies"]),
        }
        survivors = [p["ProcessId"] for p in host_processes(exe) if p["ProcessId"] not in pre_existing]
        require(not survivors, f"owned host process(es) survived: {survivors}")
        result["result"] = "PASS"
    except Exception:
        result["failure"] = traceback.format_exc()
    finally:
        if chrome:
            chrome.close()
        if pid and any(p["ProcessId"] == pid for p in host_processes(exe)):
            result["result"] = "FAIL"
            result["owned_process_survived"] = pid
        finished = datetime.now(timezone.utc)
        result["finished_utc"] = finished.isoformat(timespec="seconds")
        result["duration_s"] = round((finished - started).total_seconds(), 1)
        out.write_text(scrub(json.dumps(result, indent=2)), encoding="utf-8")
        print(json.dumps(result, indent=2))
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--flavour", choices=["plain", "tauri"], default="plain")
    parser.add_argument("--out", required=True)
    parser.add_argument("--repeat-4096", type=int, default=3)
    raise SystemExit(run(parser.parse_args()))