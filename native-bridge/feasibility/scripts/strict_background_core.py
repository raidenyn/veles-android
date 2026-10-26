"""Pure verdict logic for the strict OTP-23 background-delivery validation (no platform imports).

Strict means, for the RFC OTP-23 acceptance sentence "an event arrives after the connector has been
backgrounded for ten continuous minutes with another ordinary tab focused and no audio, WebRTC, or any
other timer-throttling exemption active":

* the driver holds NO DevTools client of any kind (neither the connector target session nor the
  browser-level connection) between putting the connector in the background and the end of the idle
  interval, and sends nothing to either phone or the host in that interval;
* the delivery is an UNSOLICITED phone notification fired by preconfigured instrumentation a fixed time
  after the central subscribed, so no desktop-side timer, request or keepalive produces the event;
* the page itself timestamps the receipt (wall clock, visibility, focus), the host trace independently
  places the emission before the driver re-attached, and the phone records hashes of what it pushed;
* the extension carries no audio / WebRTC / socket / interval / lock / keepalive API.

The existing ``poc_background.py`` evidence (browser-level CDP attached, desktop-scheduled timers, warmed
links) is preserved untouched and is NOT this validation. ``evaluate`` never reports acceptance itself:
``formal_otp23_acceptance`` is always false here; the requirements matrix adjudicates.
"""

import hashlib
import re

MIN_BACKGROUND_S = 600
PHONE_DELAY_S = 690
UNSOLICITED_SEED = 61
RECORD_LEN = 4096
RECORD_TYPE_UNSOLICITED = 3
GRACE_S = 30
HOST_BEFORE_REATTACH_MARGIN_S = 5

# APIs that are, or can act as, background-throttling exemptions or keepalives. The scan is a belt and
# braces guard over the exact extension files the run loaded; it supports, never replaces, the
# observation that the receipt happened while hidden.
EXEMPTION_PATTERNS = {
    "audio": r"\b(AudioContext|webkitAudioContext|HTMLAudioElement|new\s+Audio\s*\(|<audio\b|<video\b|MediaSession)",
    "webrtc": r"\b(RTCPeerConnection|RTCDataChannel|getUserMedia|getDisplayMedia)\b",
    "socket": r"\b(WebSocket|EventSource|WebTransport)\s*\(|\bnew\s+(WebSocket|EventSource|WebTransport)\b",
    "interval_timer": r"\bsetInterval\s*\(",
    "web_lock": r"navigator\s*\.\s*locks",
    "offscreen_or_alarm": r"chrome\s*\.\s*(offscreen|alarms)\b",
    "keepalive_ping": r"\bkeepalive\b|\bkeep-alive\b|\bwakeLock\b",
    "notifications_permission": r"\bNotification\s*\.\s*requestPermission\b|chrome\s*\.\s*notifications",
}


def expected_unsolicited(length=RECORD_LEN, seed=UNSOLICITED_SEED):
    """The phone fixture's type-3 record, recomputed independently of the Android code."""
    record = bytearray((i + seed) & 0xFF for i in range(length))
    record[:4] = b"VEL" + bytes([RECORD_TYPE_UNSOLICITED])
    return bytes(record)


def expected_sha256():
    return hashlib.sha256(expected_unsolicited()).hexdigest()


def scan_extension_for_exemptions(files):
    """files: {name: text}. Returns a list of {file, kind, match} findings (empty = clean)."""
    findings = []
    for name, text in sorted(files.items()):
        code = re.sub(r"//[^\n]*|/\*.*?\*/|<!--.*?-->", "", text, flags=re.S)
        for kind, pattern in EXEMPTION_PATTERNS.items():
            match = re.search(pattern, code)
            if match:
                findings.append({"file": name, "kind": kind, "match": match.group(0)})
    return findings


def idle_deadline_monotonic(connected_mono, phone_delay_s=PHONE_DELAY_S, grace_s=GRACE_S):
    """Monotonic time to keep every DevTools client closed until: the LAST phone's unsolicited push is due
    (its subscription time + the preconfigured delay) plus a grace for BLE delivery."""
    if not connected_mono:
        raise ValueError("no connection timestamps")
    return max(connected_mono.values()) + phone_delay_s + grace_s


def parse_host_trace(text):
    """Safe fields only (no payloads): message/send lines with the host's own ms clock."""
    messages, sends, hello_ms = [], [], None
    for line in text.splitlines():
        stamp = re.match(r"\[\s*(\d+)ms pid=\d+\]", line)
        if not stamp:
            continue
        at_ms = int(stamp.group(1))
        if " in  " in line and "type=hello" in line and hello_ms is None:
            hello_ms = at_ms
        message = re.search(r"out .*type=message .*record_base64\.len=(\d+)", line)
        if message:
            messages.append({"at_ms": at_ms, "record_base64_len": int(message.group(1))})
        if re.search(r"in  .*type=send ", line):
            sends.append({"at_ms": at_ms})
    return {"messages": messages, "sends": sends, "hello_ms": hello_ms}


def _check(status, expected, actual):
    return {"status": status, "expected": expected, "actual": actual}


def _all_pass(checks):
    values = [c["status"] for c in checks.values()]
    if "FAIL" in values:
        return "FAIL"
    return "PASS" if all(v == "PASS" for v in values) else "BLOCKED"


def evaluate(evidence):  # noqa: C901 - one flat list of independent requirement checks
    """Independent checks over a run's evidence dict; see the module docstring for the contract."""
    checks = {}
    handles = list(evidence.get("handles", []))
    events = evidence.get("events", [])
    timeline = evidence.get("timeline", {})
    min_background = evidence.get("min_background_s", MIN_BACKGROUND_S)

    probe = evidence.get("pre_close_probe") or {}
    checks["ST-01"] = _check(
        "PASS" if probe.get("connector_visibility") == "hidden"
        and probe.get("ordinary_tab_visibility") == "visible" and probe.get("ordinary_tab_focused") is True
        else "FAIL",
        "connector hidden and another ordinary tab visible and focused before the last DevTools client closes",
        probe)

    closed, reopened = timeline.get("cdp_closed_mono"), timeline.get("cdp_reopened_mono")
    idle = None if closed is None or reopened is None else round(reopened - closed, 3)
    calls = evidence.get("cdp_calls_during_idle")
    ok = idle is not None and idle >= min_background and calls == 0 \
        and evidence.get("browser_level_connection_closed") is True \
        and evidence.get("connector_session_detached_by_close") is True
    checks["ST-02"] = _check(
        "PASS" if ok else "FAIL",
        f"no DevTools client (target session or browser-level) for >= {min_background} s, 0 CDP calls",
        {"idle_seconds": idle, "cdp_calls_during_idle": calls,
         "browser_level_connection_closed": evidence.get("browser_level_connection_closed"),
         "connector_session_detached_by_close": evidence.get("connector_session_detached_by_close")})

    findings = (evidence.get("exemption_scan") or {}).get("findings")
    checks["ST-03"] = _check(
        "PASS" if findings == [] and (evidence.get("exemption_scan") or {}).get("files") else "FAIL",
        "no audio / WebRTC / socket / interval / lock / keepalive API in the loaded extension files",
        evidence.get("exemption_scan"))

    want = expected_sha256()
    per_handle, problems = {}, []
    for name in handles:
        mine = [e for e in events if e.get("handle") == name]
        per_handle[name] = {"events": len(mine)}
        if len(mine) != 1:
            problems.append(f"{name}: expected exactly 1 unsolicited message, saw {len(mine)}")
            continue
        event = mine[0]
        per_handle[name].update({"sha256": event.get("record_sha256"), "visibility": event.get("visibility"),
                                 "focused": event.get("focused")})
        if event.get("record_sha256") != want:
            problems.append(f"{name}: record is not the byte-exact type-3 fixture")
        if event.get("visibility") != "hidden":
            problems.append(f"{name}: page visibility at receipt was {event.get('visibility')!r}")
        if event.get("focused") is True:
            problems.append(f"{name}: connector page was focused at receipt")
    checks["ST-04"] = _check(
        "PASS" if handles and len(handles) >= 2 and not problems else "FAIL",
        "each of two phones delivers exactly one byte-exact unsolicited 4096-byte record, page hidden",
        {"per_handle": per_handle, "problems": problems})

    closed_wall = timeline.get("cdp_closed_wall")
    reopened_wall = timeline.get("cdp_reopened_wall")
    durations = {}
    for name in handles:
        mine = [e for e in events if e.get("handle") == name]
        if len(mine) == 1 and closed_wall is not None and mine[0].get("wall") is not None:
            durations[name] = round(mine[0]["wall"] / 1000 - closed_wall, 3)
    during = bool(durations) and reopened_wall is not None and all(
        (e.get("wall") or 0) / 1000 < reopened_wall for e in events)
    checks["ST-05"] = _check(
        "PASS" if len(durations) == len(handles) and handles
        and all(d >= min_background for d in durations.values()) and during else "FAIL",
        f"page receipt >= {min_background} s after the last DevTools client closed and before it re-attached",
        {"seconds_in_background_at_receipt": durations, "receipts_before_reattach": during})

    trace = evidence.get("host_trace") or {}
    estimates = trace.get("message_wall_estimates") or []
    before = reopened_wall is not None and len(estimates) >= len(handles) and all(
        t < reopened_wall - HOST_BEFORE_REATTACH_MARGIN_S for t in estimates)
    checks["ST-06"] = _check(
        "PASS" if before else ("BLOCKED" if not trace else "FAIL"),
        "host trace independently places both message emissions before the driver re-attached",
        {"message_count": trace.get("message_count"), "wall_estimates": estimates,
         "reattach_wall": reopened_wall})

    title = (evidence.get("title_probe") or {}).get("title_before_attach") or ""
    counted = re.search(r"messages=(\d+)", title)
    checks["ST-07"] = _check(
        "PASS" if counted and int(counted.group(1)) >= len(handles) and handles else "FAIL",
        "DevTools HTTP target list (no CDP session) shows the connector received both messages before attach",
        {"title": title})

    phones = evidence.get("phone_evidence")
    if not phones:
        checks["ST-08"] = _check("BLOCKED", "phone-side hash evidence for every push", "not collected")
    else:
        pushes = [p for item in phones for p in item.get("pushes", [])]
        good = len(pushes) >= len(handles) and all(
            p.get("queued") is True and p.get("sha256") == want and p.get("bytes") == RECORD_LEN
            and p.get("fired_at_epoch_ms", 0) - p.get("subscribed_at_epoch_ms", 0)
            >= p.get("configured_delay_ms", 1 << 62) for p in pushes)
        checks["ST-08"] = _check("PASS" if good else "FAIL",
                                 "each phone pushed the expected hash once, queued, after its configured delay",
                                 {"pushes": len(pushes)})

    scenario = evidence.get("scenario")
    trace_sends = trace.get("sends_in_trace")
    if scenario == "cold":
        ok = trace_sends == 0 and not evidence.get("warmup")
        checks["ST-09"] = _check("PASS" if ok else "FAIL",
                                 "cold idle: no desktop->phone record was ever sent in this run",
                                 {"sends_in_host_trace": trace_sends, "warmup": bool(evidence.get("warmup"))})
    elif scenario == "prewarm":
        ok = bool(evidence.get("warmup")) and (trace_sends or 0) >= len(handles)
        checks["ST-09"] = _check("PASS" if ok else "FAIL",
                                 "prewarm: a byte-verified exchange per phone happened before the idle interval",
                                 {"sends_in_host_trace": trace_sends, "warmup": evidence.get("warmup")})
    else:
        checks["ST-09"] = _check("FAIL", "scenario must be 'cold' or 'prewarm'", scenario)

    cleanup = evidence.get("cleanup") or {}
    checks["ST-10"] = _check(
        "PASS" if cleanup.get("shutdown_complete") is True and cleanup.get("surviving_hosts") == [] else "FAIL",
        "shutdown_complete and no surviving host process", cleanup)

    return {"strict_result": _all_pass(checks), "formal_otp23_acceptance": False, "checks": checks,
            "scenario": scenario}
