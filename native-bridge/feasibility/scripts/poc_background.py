"""OTP-23 background-liveness PoC: one 610 s hidden-tab setTimeout in the connector page driving otp23.send on BOTH phone handles over real native messaging. After scheduling, an ordinary about:blank tab is opened and activated, the connector asserted hidden, and its CDP session detached (Target.detachFromTarget) leaving only the browser-level CDP connection; Python sleeps 630 s with zero connector CDP traffic, then re-attaches and verifies two "sent" replies, byte-exact VEL2 4096-byte synthetic records per handle, visibility "hidden" at send, and elapsed >= 600000 ms from scheduling. Late evidence FAILS; a bounded 60000 ms clamping allowance is recorded clearly because the connector CDP session is re-attached before the stored state is read. TWO physical phones required -- real scan, no simulation; FAIL if missing.
If window.pocBackground is absent at the first read (the hidden timer may fire anywhere in 610..670 s) it is polled boundedly -- page clock, up to schedule+TIMER_MS+CLAMP_EXTRA_MS+10000 ms -- and reattachment_may_have_woken_page is recorded explicitly; absence is never fabricated and no formal background acceptance is claimed.
 Stdlib only; never terminates processes; never writes record payloads (SHA-256 hashes only). formal_otp23_acceptance stays false whatever the poc_result: the browser-level CDP connection remains and the native-messaging liveness exemption is not comprehensively excluded -- working PoC evidence, not formal acceptance.
Forensics: VELES_OTP23_TRACE is set to <out>.trace.log (initialized, unlinked before launch, recorded in meta.host_trace_log), the stored send replies keep the safe fields type/code/stage/recoverability (never payload bytes) and are dumped into meta on failure, and a pre-idle warmup sends a 4096-byte synthetic record to BOTH handles with distinct seeds, then polls otp23.drainEvents() for up to 12 s for the asynchronous response (empty drains are NOT failures -- the host notifies after the GATT write completes; timeout/disconnect/error/duplicates/wrong bytes are) and verifies the actual message bytes per handle before anything is scheduled.
Optional --diagnostic-delay-seconds N (positive integer, default None) shrinks the timer to N*1000 ms, the detached wait to N+5 s and the minimum elapsed to N*1000 ms, recording diagnostic_short_run=true -- a diagnostic run NEVER implies ten-minute acceptance. Defaults stay 610000/630/600000.
Usage: python scripts/poc_background.py --flavour plain --out evidence/poc-background.json [--diagnostic-delay-seconds N]
macOS (sys.platform == darwin) uses macos_background_support.py; the cmd.exe wrapper requirement is Windows-only.
"""

import argparse
import base64
import hashlib
import json
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

if sys.platform == "darwin":
    import macos_background_support as cv
    PLATFORM, WRAPPER = cv.PLATFORM, None
else:
    import chrome_validate as cv
    PLATFORM, WRAPPER = "windows", "cmd.exe"

FIXED_ID = "gmekhgkipjkikmaadaajljinkgfielei"
TIMER_MS, WAIT_S, MIN_MS, CLAMP_EXTRA_MS = 610_000, 630, 600_000, 60_000
WARMUP_SEEDS = (41, 53)
WARMUP_NOTIFY_TIMEOUT_S = 12
SCHEDULE = """(() => {
  const record = Uint8Array.from(atob("%s"), c => c.charCodeAt(0)), handles = %s; window.pocBackground = null;
  setTimeout(async () => {
    const info = { sent_at: {}, replies: {}, visibility_at_send: {} };
    for (const [name, handle] of Object.entries(handles)) {
      const sent_at = performance.now(); const visibility_at_send = document.visibilityState;
      const reply = await otp23.send(handle, record);
      info.replies[name] = { type: reply && reply.type, code: (reply && reply.code) || null,
        stage: (reply && reply.stage) || null, recoverability: (reply && reply.recoverability) || null };
      info.sent_at[name] = sent_at;
      info.visibility_at_send[name] = visibility_at_send;
    }
    info.visibility_after = document.visibilityState; info.done_at = performance.now(); window.pocBackground = info;
  }, %d);
  return performance.now();
})()"""

def utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_bg_until(read, sched_ms, bound_ms, sleep=time.sleep, wall=time.time):
    """Poll a reattached connector page for window.pocBackground until schedule+bound_ms on the page clock.
    read() must return (result_or_None, page_now_ms). The hidden-tab timer may fire anywhere in
    [TIMER_MS, TIMER_MS+CLAMP_EXTRA_MS], so a first read at 630 s can legitimately miss it; the poll is bounded
    by the page-clock allowance plus a wall-clock guard, never fabricates a result, and never claims formal
    background acceptance. sleep/wall are injectable so the bound is regression-testable with a fake clock."""
    info = {"poll_reads": 0, "result_at_first_read": False, "reattachment_may_have_woken_page": False}
    wall_deadline = wall() + bound_ms / 1000 + 15
    while True:
        result, page_now = read()
        info["poll_reads"] += 1
        info["page_elapsed_ms_at_last_read"] = round(page_now - sched_ms)
        if isinstance(result, dict):
            info["result_at_first_read"] = info["poll_reads"] == 1
            info["reattachment_may_have_woken_page"] = info["poll_reads"] != 1
            return result, info
        if page_now - sched_ms > bound_ms:
            raise AssertionError(f"window.pocBackground absent through the bounded hidden-timer allowance "
                                 f"(schedule+{bound_ms} ms page clock); not fabricating evidence")
        if wall() >= wall_deadline:
            raise AssertionError(f"window.pocBackground absent and wall-clock guard hit before the page clock "
                                 f"passed schedule+{bound_ms} ms; not fabricating evidence")
        sleep(0.25)


def warmup_send_expression(handle, record):
    """Single source of truth for the warmup record expression the connector must evaluate.
    The decoder map MUST be the second argument of Uint8Array.from -- `atob(b64, fn)` silently ignores fn, so
    Uint8Array.from would iterate the b64-decoded STRING by code point and mangle every byte >= 0x80."""
    return (f"otp23.send({json.dumps(handle)}, "
            f"Uint8Array.from(atob({json.dumps(base64.b64encode(record).decode())}), c => c.charCodeAt(0)))")


def collect_warmup_response(read_poll, expected_b64, handle, timeout_s, sleep=time.sleep, clock=time.time):
    """Poll the connector for the asynchronous warmup response of one connection handle.
    read_poll() must return {"snapshot": otp23.snapshot(), "events": otp23.drainEvents() messages}. Empty drains
    are normal -- the host notifies asynchronously after the GATT write completes, so a single immediate drain is
    NOT a failure. Fail fast on: port disconnect (snapshot.portOpen false), async error events, duplicate responses
    for the handle, or wrong bytes. A missing response is a timeout failure only after timeout_s of polling.
    Returns (message, info) with poll count, notify_wait_s (fake-clock-friendly) and sha256 of the verified bytes.
    sleep/clock are injectable so the failure paths are regression-testable without real waiting."""
    started, polls = clock(), 0
    while True:
        polls += 1
        read = read_poll()
        snapshot, drained = read["snapshot"], read["events"]
        if not (isinstance(snapshot, dict) and snapshot.get("portOpen")):
            raise AssertionError(f"warmup: native messaging port disconnected while waiting for the async response "
                                 f"(handle {handle[:8]}): {snapshot.get('disconnect') if isinstance(snapshot, dict) else snapshot}")
        if error_events := [m for m in drained if m.get("type") == "error"]:
            raise AssertionError(f"warmup: async error event(s) for handle {handle[:8]}: {error_events}")
        matches = [m for m in drained if m.get("type") == "message" and m.get("connection_handle") == handle]
        digest = hashlib.sha256(base64.b64decode(matches[0]["record_base64"])).hexdigest() if matches else None
        if matches and matches[0]["record_base64"] != expected_b64:
            raise AssertionError(f"warmup record for handle {handle[:8]} wrong bytes (sha256 {digest}); expected the exact VEL2 fixture")
        if len(matches) > 1:
            raise AssertionError(f"warmup: duplicate response(s) for handle {handle[:8]}: {len(matches)} message events")
        if matches:
            return matches[0], {"polls": polls, "notify_wait_s": round(clock() - started, 3),
                                "sha256": digest, "byte_exact": True}
        if clock() - started >= timeout_s:
            raise AssertionError(f"warmup response for handle {handle[:8]} not delivered within {timeout_s} s of "
                                 f"drainEvents polling (empty first drains are not failures; this is a genuine timeout)")
        sleep(0.5)

def positive_int(text):
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def prepare_trace_path(out):
    """Absolute trace path (native Chrome child has another cwd); parent created, stale own trace removed."""
    trace_path = pathlib.Path(out).resolve().with_suffix(".trace.log")
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.unlink(missing_ok=True)
    return trace_path


def run(args):
    exe = cv.EXE_NAME["plain"]
    diag = getattr(args, "diagnostic_delay_seconds", None)
    trace_path = prepare_trace_path(args.out)
    meta = {"platform": PLATFORM, "flavour": "plain", "started_utc": utc(), "browser_version": None, "chrome_browser_pid": None,
            "owned_host_pid": None, "cmd_wrapper_pid": None, "schedule_utc": None, "timer_due_utc": None,
            "detached_utc": None, "reattached_utc": None, "finished_utc": None,
            "timer_ms": None, "min_elapsed_ms": None, "detach_period_s": None, "timer_clamped": None,
            "hidden_probes": {}, "hashes": {}, "warmup": None, "send_replies": None, "message_events": None,
            "result_at_first_read": None, "reattachment_may_have_woken_page": None, "bound_poll_reads": None,
            "owned_host_pid_gone_s": None, "surviving_host_processes_after_run": None}
    result = {"platform": PLATFORM, "poc_result": "FAIL", "formal_otp23_acceptance": False, "reason": None, "meta": meta, "cases": []}
    chrome, st = None, {"hosts_before": {p["ProcessId"] for p in cv.host_processes(exe)},
                        "timer_ms": diag * 1000 if diag else TIMER_MS, "wait_s": diag + 5 if diag else WAIT_S,
                        "min_ms": diag * 1000 if diag else MIN_MS, "trace_path": str(trace_path)}
    meta.update({"planned_wait_s": st["wait_s"], "timer_ms": st["timer_ms"], "min_elapsed_ms": st["min_ms"],
                 "diagnostic_short_run": bool(diag), "host_trace_log": st["trace_path"]})
    if diag:
        meta["diagnostic_note"] = ("diagnostic short run only: timer/wait/minimum were reduced -- in no way evidence "
                                   "of ten-minute background native-messaging acceptance")

    def guard(fn):
        try: return fn()
        except Exception: return None

    def step(cid, title, fn):
        started, record = time.time(), {"id": cid, "title": title, "result": "FAIL", "detail": None, "seconds": None}
        try:
            record.update({"result": "PASS", "detail": fn() or {}})
        except Exception as error:
            record.update({"detail": f"{type(error).__name__}: {error}"})
        record["seconds"] = round(time.time() - started, 2)
        print(f"[{record['result']:5}] {cid} {title}" + (f"\n          {record['detail'][:300]}" if record["result"] != "PASS" else ""))
        result["cases"].append(record)
        return record["result"] == "PASS"

    def ph_env():
        nonlocal chrome
        chrome = cv.ChromeRun({"VELES_OTP23_TRACE": st["trace_path"]}, medium=True); ext = chrome.load_extension()
        cv.expect(ext["id"] == FIXED_ID, f"extension id {ext['id']} != fixed {FIXED_ID}")
        meta.update({"chrome_browser_pid": chrome.proc.pid,
                     "browser_version": {"product": chrome.version.get("product"), "user_agent": chrome.version.get("userAgent")}})
        st["target"], st["session"] = chrome.open_page(f"chrome-extension://{FIXED_ID}/connector.html")
        for _ in range(50):
            if chrome.js(st["session"], "typeof window.otp23 === 'object'") or time.sleep(0.1):
                break
        return {"extension_id": ext["id"], "connector_target": st["target"]}

    def ph_hello():
        before = {p["ProcessId"] for p in cv.host_processes(exe)}; chrome.js(st["session"], f"otp23.connect({json.dumps(cv.HOSTS['plain'])}); 1")
        cv.expect((ready := chrome.js(st["session"], "otp23.hello()")).get("type") == "ready", f"hello reply {ready}")
        fresh = [p for p in cv.host_processes(exe) if p["ProcessId"] not in before]
        cv.expect(len(fresh) == 1, f"expected exactly one new host process, saw {fresh}")
        chain = cv.ancestry(fresh[0]["ProcessId"])
        wrapper = chain[0] if WRAPPER and chain and chain[0]["Name"].lower() == WRAPPER else None
        cv.expect((wrapper or not WRAPPER) and chrome.proc.pid in [p["ProcessId"] for p in chain], f"host ancestry: {chain}")
        meta.update({"owned_host_pid": fresh[0]["ProcessId"], "cmd_wrapper_pid": wrapper["ProcessId"] if wrapper else None}); st["owned_pid"] = meta["owned_host_pid"]
        return {"host_pid": st["owned_pid"], "wrapper_pid": meta["cmd_wrapper_pid"], "ancestry": chain}

    def ph_phones():
        cv.expect((scan := chrome.js(st["session"], f"otp23.startScan({json.dumps(cv.VELES_SERVICE)})")).get("status") == "started", f"startScan {scan}")
        deadline, devices = time.time() + 40, set()
        while time.time() < deadline and len(devices) < 2:
            if (found := [e["message"].get("device_handle") for e in chrome.js(st["session"], "otp23.drainEvents()")
                          if e["message"].get("type") == "device_found"]):
                devices.update(found)
            time.sleep(0.5)
        cv.expect(len(devices) >= 2, f"two physical phones required; found {len(devices)}"); st["handles"] = {}
        for handle in sorted(devices)[:2]:
            connected = chrome.js(st["session"], f"otp23.connectDevice({{device_handle: {json.dumps(handle)}}})", timeout=70)
            cv.expect(connected.get("type") == "connected", f"connectDevice {handle[:8]}: {connected.get('code')} {connected.get('stage')}")
            st["handles"][handle] = connected["connection_handle"]
        cv.expect((stopped := chrome.js(st["session"], f"otp23.stopScan({json.dumps(scan['scan_handle'])})")).get("type") == "scan_state"
                  and stopped.get("status") == "stopped", f"stopScan reply {stopped}")
        chrome.js(st["session"], "otp23.drainEvents(); 1")
        return {"phones": 2, "scan_stopped": True, "events_cleared": True}

    def ph_warmup():
        warmup, pairs = {}, list(zip(sorted(st["handles"].items()), WARMUP_SEEDS))

        def read_poll():
            return {"snapshot": chrome.js(st["session"], "otp23.snapshot()"),
                    "events": [e["message"] for e in chrome.js(st["session"], "otp23.drainEvents()")]}

        for (name, handle), seed in pairs:
            data = bytearray((i + seed) & 255 for i in range(4096)); data[:4] = b"VEL\x01"
            record = bytes(data)
            expected_b64 = base64.b64encode(record[:3] + b"\x02" + record[4:]).decode()
            reply = chrome.js(st["session"], warmup_send_expression(handle, record))
            cv.expect(reply.get("type") == "sent", f"warmup send failed (setup path broken) to {name[:8]}: {reply}")
            message, info = collect_warmup_response(read_poll, expected_b64, handle, WARMUP_NOTIFY_TIMEOUT_S)
            warmup[name[:8]] = {"seed": seed, **info}
        meta["warmup"] = warmup
        return {"warmup_sends": len(warmup), "seeds": [pair[1] for pair in pairs], "timeout_s": WARMUP_NOTIFY_TIMEOUT_S,
                "note": "foreground pre-idle; async notify collected across bounded polls; scheduled record keeps seed 17"}

    def ph_schedule():
        data = bytearray((i + 17) & 255 for i in range(4096)); data[:4] = b"VEL\x01"
        record, expected = bytes(data), None; expected = record[:3] + b"\x02" + record[4:]
        st["expected_b64"] = base64.b64encode(expected).decode(); meta["hashes"].update({"expected_response_sha256": hashlib.sha256(expected).hexdigest(), "record_bytes": 4096})
        meta["schedule_utc"], meta["timer_due_utc"] = utc(), (datetime.now(timezone.utc) + timedelta(milliseconds=st["timer_ms"])).strftime("%Y-%m-%dT%H:%M:%SZ")
        st["sched_ms"] = chrome.js(st["session"], SCHEDULE % (base64.b64encode(record).decode(), json.dumps(st["handles"]), st["timer_ms"]))
        chrome.cdp.call("Target.activateTarget", {"targetId": chrome.cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]})
        for _ in range(20):
            if (hidden := chrome.js(st["session"], "document.visibilityState")) == "hidden":
                break
            time.sleep(0.25)
        cv.expect(hidden == "hidden", f"connector not hidden after activating about:blank: {hidden}")
        meta["hidden_probes"]["pre_detach"] = hidden
        return {"scheduled_at_ms": st["sched_ms"], "timer_ms": st["timer_ms"], "connector_hidden_pre_detach": hidden}

    def ph_detach_sleep():
        meta["detached_utc"] = utc()
        chrome.cdp.call("Target.detachFromTarget", {"sessionId": st["session"]})
        time.sleep(st["wait_s"])
        meta["reattached_utc"], meta["detach_period_s"] = utc(), st["wait_s"]
        return {"connector_session_detached": True, "browser_connection": "open", "wait_s": st["wait_s"],
                "no_connector_cdp_traffic_during_wait": True}

    def ph_evidence():
        rs = chrome.cdp.call("Target.attachToTarget", {"targetId": st["target"], "flatten": True})["sessionId"]; chrome.cdp.call("Runtime.enable", session=rs)
        st["reattach_session"] = rs

        def read_bg():
            read = chrome.js(rs, "({ bg: window.pocBackground, now: performance.now() })", timeout=60)
            return read.get("bg"), read.get("now")

        poc, poll = read_bg_until(read_bg, st["sched_ms"], st["timer_ms"] + CLAMP_EXTRA_MS + 10000)
        meta.update({"result_at_first_read": poll["result_at_first_read"],
                     "reattachment_may_have_woken_page": poll["reattachment_may_have_woken_page"],
                     "bound_poll_reads": poll["poll_reads"]})
        meta["send_replies"] = poc.get("replies")
        meta["hidden_probes"].update({"at_send_per_handle": poc.get("visibility_at_send"),
                                      "after_reattach": chrome.js(rs, "document.visibilityState")})
        at_send = poc.get("visibility_at_send", {})
        cv.expect(len(at_send) == len(st["handles"]) and all(v == "hidden" for v in at_send.values())
                  and poc.get("visibility_after") == "hidden",
                  f"sends not hidden at send start per handle: {at_send} -> visibility_after {poc.get('visibility_after')}")
        elapsed = {}
        for name in sorted(st["handles"]):
            cv.expect((reply := poc.get("replies", {}).get(name)) and reply.get("type") == "sent",
                      f"two sent replies required; {name[:8]} full safe reply: {reply}")
            elapsed[name] = round(poc["sent_at"][name] - st["sched_ms"])
            cv.expect(st["min_ms"] <= elapsed[name] <= st["timer_ms"] + CLAMP_EXTRA_MS,
                      f"send[{name[:8]}] elapsed {elapsed[name]} ms violates {st['min_ms']}..{st['timer_ms'] + CLAMP_EXTRA_MS} ms")
            if elapsed[name] > st["timer_ms"]:
                meta["timer_clamped"] = {"handle": name[:8], "extra_ms": elapsed[name] - st["timer_ms"], "note": "state read after connector CDP re-attachment"}
        messages = [e["message"] for e in chrome.js(rs, "otp23.drainEvents()", timeout=120) if e["message"].get("type") == "message"]
        meta["message_events"] = [{"type": m.get("type"), "code": m.get("code"), "stage": m.get("stage"), "recoverability": m.get("recoverability")} for m in messages]
        actual = {}
        for name, handle in st["handles"].items():
            got = [m for m in messages if m.get("connection_handle") == handle]
            cv.expect(got, f"no message event for handle {name[:8]}")
            digest = hashlib.sha256(base64.b64decode(got[0]["record_base64"])).hexdigest()
            cv.expect(got[0]["record_base64"] == st["expected_b64"], f"record {name[:8]} not byte-exact (sha256 {digest})")
            actual[name[:8]] = {"sha256": digest, "byte_exact": True}
        meta["hashes"]["actual_response_sha256_per_handle"] = actual
        return {"elapsed_ms_from_schedule": elapsed, "records": actual, "message_events_seen": len(messages)}

    def ph_shutdown():
        cv.expect((reply := chrome.js(st["reattach_session"], "otp23.shutdown()", timeout=45)).get("type") == "shutdown_complete", f"shutdown reply {reply}")
        cv.expect((waited := cv.wait_gone(st["owned_pid"], 15)) is not None, f"owned host pid {st['owned_pid']} survived shutdown")
        meta["owned_host_pid_gone_s"], st["shutdown_done"] = waited, True
        return {"shutdown_reply": reply.get("type"), "owned_pid_gone_s": waited}

    try:
        for cid, title, fn in [("ENV-1", "Stable Chrome + fixed-id unpacked extension + connector page + host trace log", ph_env), ("NMH-1", "hello over registered plain host behind external cmd wrapper", ph_hello),
                               ("PH-2", "Two physical phones: scan, connect, stop scan, clear events", ph_phones), ("PH-3", "Pre-idle warmup: 4096-byte record both handles, distinct seeds, byte-verified", ph_warmup),
                               ("BG-1", "Schedule dual-send timer; about:blank; connector hidden", ph_schedule), ("BG-2", "Detach connector session; detached wait without connector CDP traffic", ph_detach_sleep),
                               ("BG-3", "Reattach: two sent replies, byte-exact records, hidden, >= min elapsed", ph_evidence), ("NMH-2", "shutdown; owned host pid gone", ph_shutdown)]:
            if not step(cid, title, fn):
                break
    finally:
        if chrome:
            if st.get("reattach_session") and st.get("owned_pid") and not st.get("shutdown_done"):
                guard(lambda: chrome.js(st["reattach_session"], "otp23.shutdown()", timeout=45))
            guard(chrome.close)
        meta["surviving_host_processes_after_run"] = guard(lambda: [p["ProcessId"] for p in cv.host_processes(exe)])
        meta["finished_utc"], passed = utc(), bool(result["cases"]) and all(c["result"] == "PASS" for c in result["cases"])
        result["poc_result"] = "PASS" if passed else "FAIL"
        failures = [f"{c['id']}: {c['detail']}" for c in result["cases"] if c["result"] != "PASS"]
        standing = "working PoC evidence only: browser-level CDP connection remains and native-messaging liveness exemption not comprehensively excluded (not formal acceptance)"
        if meta.get("reattachment_may_have_woken_page"):
            standing += ("; result was absent at the first read after reattachment, which may itself have woken "
                         "the hidden page -- no formal background acceptance is claimed")
        if meta.get("diagnostic_short_run"):
            standing += "; diagnostic short run with reduced timer/wait/minimum -- never implies ten-minute acceptance"
        result["reason"] = ("; ".join(failures) + " -- " + standing) if failures else standing
        out = pathlib.Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(cv.scrub(json.dumps(result, indent=2)), encoding="utf-8")
        print(f"poc_result={result['poc_result']} formal_otp23_acceptance=false -> {out}")
    return 0 if result["poc_result"] == "PASS" else 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--flavour", choices=["plain"], default="plain")
    parser.add_argument("--out", required=True)
    parser.add_argument("--diagnostic-delay-seconds", type=positive_int, default=None,
                        help="diagnostic: timer N*1000 ms, detached wait N+5 s, min elapsed N*1000 ms; records "
                             "diagnostic_short_run and never implies ten-minute acceptance")
    sys.exit(run(parser.parse_args()))