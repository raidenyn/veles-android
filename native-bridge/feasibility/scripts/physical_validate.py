"""Physical two-phone synthetic matrix. Requires both OTP-06 peripherals advertising.

Run from an interactive Windows user session, after register-host.ps1:
python scripts/physical_validate.py --flavour plain --out evidence/physical-plain.json
No simulated fallback: missing phones, failed setup or byte mismatch fail the run.
"""

import argparse
import base64
import hashlib
import json
import pathlib
import time
import traceback

from chrome_validate import ChromeRun, EXE_NAME, HOSTS, VELES_SERVICE, host_processes, scrub, wait_gone


def fixture(size, seed):
    data = bytearray((i + seed) & 255 for i in range(size))
    data[:4] = b"VEL\x01"
    return bytes(data)


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def run(args):
    out = pathlib.Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    trace = out.with_suffix(".trace.log")
    trace.unlink(missing_ok=True)
    result = {"flavour": args.flavour, "cases": [], "result": "FAIL"}
    chrome = None
    try:
        chrome = ChromeRun({"VELES_OTP23_TRACE": str(trace)}, medium=True)
        result["chrome_version"] = chrome.version["product"]
        ext = chrome.load_extension()["id"]
        _, session = chrome.open_page(f"chrome-extension://{ext}/connector.html")
        for _ in range(50):
            if chrome.js(session, "typeof otp23 === 'object'"):
                break
            time.sleep(0.1)
        js = lambda expression: chrome.js(session, expression, timeout=70)
        js(f"otp23.connect({json.dumps(HOSTS[args.flavour])}); 1")
        ready = js("otp23.hello()")
        require(ready.get("type") == "ready", ready)
        processes = host_processes(EXE_NAME[args.flavour])
        require(len(processes) == 1, "expected one host process")
        pid = processes[0]["ProcessId"]
        result["host_pid"] = pid
        started = js(f"otp23.startScan({json.dumps(VELES_SERVICE)})")
        require(started.get("status") == "started", started)
        devices = {}
        deadline = time.monotonic() + 30
        while len(devices) < 2 and time.monotonic() < deadline:
            for event in js("otp23.drainEvents()"):
                message = event["message"]
                if message["type"] == "device_found":
                    devices[message["device_handle"]] = message
            time.sleep(0.25)
        require(len(devices) >= 2, f"only {len(devices)} physical peripherals found")
        targets = [{"device_handle": h} for h in list(devices)[:2]]
        connected = js(f"Promise.all({json.dumps(targets)}.map(t => otp23.connectDevice(t)))")
        result["setup_replies"] = connected
        require(all(c.get("type") == "connected" for c in connected), connected)
        handles = [c["connection_handle"] for c in connected]
        require(len(set(handles)) == 2, "connection handles crossed")
        result["cases"].append({"case": "two-independent-connects", "result": "PASS"})
        stopped = js(f"otp23.stopScan({json.dumps(started['scan_handle'])})")
        require(stopped.get("status") == "stopped", stopped)

        def exchange(active_handles, size, seeds):
            js("otp23.drainEvents()")
            records = [fixture(size, seed) for seed in seeds]
            expected = {h: base64.b64encode(r[:3] + b"\x02" + r[4:]).decode()
                        for h, r in zip(active_handles, records)}
            requests = [{"h": h, "b": base64.b64encode(r).decode()}
                        for h, r in zip(active_handles, records)]
            replies = js(f"Promise.all({json.dumps(requests)}.map(x => otp23.request('send', "
                         "{connection_handle:x.h,record_base64:x.b})))")
            require(all(r.get("type") == "sent" for r in replies), replies)
            ids = [r["request_id"] for r in replies]
            require(len(set(ids)) == len(ids), "request ids reused")
            received = {}
            deadline = time.monotonic() + 12
            while len(received) < len(expected) and time.monotonic() < deadline:
                for event in js("otp23.drainEvents()"):
                    m = event["message"]
                    require(m["type"] != "disconnected", m)
                    if m["type"] == "message":
                        h = m["connection_handle"]
                        require(h in expected and m["record_base64"] == expected[h], "foreign handle or byte mismatch")
                        require(h not in received, "duplicate response")
                        received[h] = hashlib.sha256(base64.b64decode(m["record_base64"])).hexdigest()
                time.sleep(0.05)
            require(len(received) == len(expected), f"only {len(received)}/{len(expected)} responses")
            return {"bytes_per_phone": size, "response_sha256": list(received.values()), "request_ids": ids}

        # Floor-MTU edges, Android attribute ceiling (508/509 payload bytes),
        # MTU-517 fragment edge (510/511), then maximum records. Actual MTU is in the trace.
        sizes = [4, 178, 179, 180, 505, 506, 507, 508, 509, 510, 511, 1020, 1021, 4095, 4096]
        for index, size in enumerate(sizes + [4096] * 5):
            evidence = exchange(handles, size, [17 + index, 137 + index])
            result["cases"].append({"case": "concurrent-whole-records", "result": "PASS", **evidence})
        disconnected = js(f"otp23.disconnectDevice({json.dumps(handles[0])})")
        require(disconnected.get("type") == "disconnected", disconnected)
        result["cases"].append({"case": "disconnect-one-other-still-exchanges", "result": "PASS",
                                **exchange(handles[1:], 4096, [219])})
        done = js("otp23.shutdown()")
        require(done.get("type") == "shutdown_complete", done)
        gone = wait_gone(pid, 8)
        require(gone is not None, "host survived shutdown with real link")
        result["cases"].append({"case": "shutdown-real-link", "result": "PASS", "process_gone_seconds": gone})
        result["result"] = "PASS"
    except Exception:
        result["failure"] = traceback.format_exc()
    finally:
        if chrome:
            chrome.close()
        out.write_text(scrub(json.dumps(result, indent=2)), encoding="utf-8")
        print(json.dumps(result, indent=2))
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--flavour", choices=["plain", "tauri"], default="plain")
    parser.add_argument("--out", required=True)
    raise SystemExit(run(parser.parse_args()))
