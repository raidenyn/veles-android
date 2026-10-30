"""OTP-23 DIAGNOSTIC: an unsolicited host event arrives in a hidden connector tab
after >= 10 continuous minutes while another ordinary tab is focused.

No phone is used: the host's 300 s scan cap is a genuine, unsolicited
host->extension event. The scan is started at ~5.3 min of backgrounding, so the cap event is
delivered at ~10.3 min. CDP remains attached and polls the page; absence of timer-throttling
exemptions is NOT established. This does not satisfy the phone-background-delivery gate.

Usage:  python scripts/background_10min.py --flavour plain --out evidence/background-10min-plain.json
"""

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import chrome_validate as cv  # noqa: E402

VELES_SERVICE = cv.VELES_SERVICE
START_SCAN_AT = 320  # seconds of backgrounding (after Chrome's 5-minute intensive-throttling threshold)
RUN_UNTIL = 640  # seconds of backgrounding: the cap event must land before this
SNAPSHOT_EVERY = 60

PROBE_JS = """(() => ({
  visibility: document.visibilityState,
  hasFocus: document.hasFocus(),
  audioVideoElements: document.querySelectorAll('audio,video').length,
  webrtcApiAvailable: typeof RTCPeerConnection === 'function',
}))()"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flavour", choices=sorted(cv.HOSTS), default="plain")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    exe = cv.EXE_NAME[args.flavour]
    host_name = cv.HOSTS[args.flavour]
    trace = pathlib.Path(args.out).resolve().with_name(f"host-trace-background-{args.flavour}.log")
    trace.unlink(missing_ok=True)
    chrome = cv.ChromeRun({"VELES_OTP23_TRACE": str(trace)})
    out = {"chrome_version": chrome.version.get("product"), "flavour": args.flavour, "snapshots": [],
           "debugger_attached": True, "event_source": "scan_cap", "acceptance_pass": False, "result": "FAIL"}
    try:
        ext_id = chrome.load_extension()["id"]
        out["extension_id"] = ext_id
        a_target, a_session = chrome.open_page(f"chrome-extension://{ext_id}/connector.html")
        for _ in range(50):
            if chrome.js(a_session, "typeof window.otp23 === 'object'"):
                break
            time.sleep(0.1)
        before = {p["ProcessId"] for p in cv.host_processes(exe)}
        chrome.js(a_session, f"otp23.connect({json.dumps(host_name)}); 1")
        ready = chrome.js(a_session, "otp23.hello()")
        assert ready["type"] == "ready", ready
        host_pid = cv.new_host_process(exe, before)[0]["ProcessId"]
        out["host_pid"] = host_pid

        # Another ordinary tab becomes the focused tab; the connector tab is now hidden.
        b_target, b_session = chrome.open_page("about:blank")
        chrome.cdp.call("Target.activateTarget", {"targetId": b_target})
        time.sleep(1.5)
        probe = chrome.js(a_session, PROBE_JS)
        assert probe["visibility"] == "hidden", f"connector tab is not backgrounded: {probe}"
        b_probe = chrome.js(b_session, "({visibility: document.visibilityState, focus: document.hasFocus()})")
        targets = chrome.cdp.call("Target.getTargets")["targetInfos"]
        out["focused_tab"] = {"targetId": b_target, "url": "about:blank", "probe": b_probe}
        out["connector_tab"] = {"targetId": a_target, "probe_at_hide": probe}
        out["tabs_at_hide"] = [{"type": t["type"], "url": t["url"][:80], "attached": t.get("attached")}
                               for t in targets if t["type"] == "page"]
        hidden_at = time.time()
        print("connector hidden; host pid", host_pid, flush=True)

        started = False
        deadline = hidden_at + RUN_UNTIL
        next_snapshot = hidden_at + SNAPSHOT_EVERY
        cap_event = None
        while time.time() < deadline:
            elapsed = time.time() - hidden_at
            if not started and elapsed >= START_SCAN_AT:
                reply = chrome.js(a_session, f"otp23.startScan({json.dumps(VELES_SERVICE)})")
                assert reply["status"] == "started", reply
                scan_started_elapsed = elapsed
                out["scan_started_at_s"] = round(scan_started_elapsed, 1)
                out["scan_handle"] = reply["scan_handle"]
                started = True
                print(f"scan started at {elapsed:.0f}s", flush=True)
            if time.time() >= next_snapshot:
                snap = chrome.js(a_session, "otp23.snapshot()")
                snap["elapsed_s"] = round(time.time() - hidden_at)
                snap["host_alive"] = cv.process_alive(host_pid)
                out["snapshots"].append(snap)
                print("snapshot", snap["elapsed_s"], snap["visibility"], "port", snap["portOpen"],
                      "host", snap["host_alive"], flush=True)
                next_snapshot += SNAPSHOT_EVERY
            timeline = chrome.js(a_session, "otp23.timeline()")
            stopped = [e for e in timeline if e["type"] == "scan_state" and e["status"] == "stopped"]
            if stopped and cap_event is None:
                cap_event = stopped[0]
                cap_event["received_after_hidden_s"] = round(time.time() - hidden_at, 1)
                cap_event["page_visibility_at_receipt"] = chrome.js(a_session, "document.visibilityState")
                print("cap event received", cap_event, flush=True)
                break
            time.sleep(2)

        out["unsolicited_event"] = cap_event
        out["page_probe_at_end"] = chrome.js(a_session, PROBE_JS)
        out["host_alive_at_end"] = cv.process_alive(host_pid)
        out["host_pid_unchanged"] = [p["ProcessId"] for p in cv.host_processes(exe)] == [host_pid]
        ok = bool(cap_event) and cap_event["received_after_hidden_s"] >= 600 and \
            out["page_probe_at_end"]["visibility"] == "hidden" and out["host_alive_at_end"]
        out["result"] = "DIAGNOSTIC" if ok else "FAIL"
        # Clean shutdown at the end (also proves the port still works after 10 minutes).
        done = chrome.js(a_session, "otp23.shutdown()")
        out["shutdown_after_10min"] = done.get("type")
        out["host_gone_after_shutdown_s"] = cv.wait_gone(host_pid, 8)
    finally:
        chrome.close()
        pathlib.Path(args.out).write_text(cv.scrub(json.dumps(out, indent=2)), encoding="utf-8")
    print(out["result"], "->", args.out)
    return 0 if out["result"] == "DIAGNOSTIC" else 1


if __name__ == "__main__":
    sys.exit(main())
