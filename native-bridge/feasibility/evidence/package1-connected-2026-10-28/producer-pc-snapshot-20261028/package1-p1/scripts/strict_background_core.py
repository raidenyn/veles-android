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
* the extension carries no audio / WebRTC / socket / interval / lock / keepalive API;
* visibility/focus HISTORIES of both the connector and the ordinary tab are preconfigured (listeners
  installed before the last DevTools client closes, no timers) and collected only after the measurement:
  initial state plus timestamped visibilitychange/focus/blur events must show the connector hidden and the
  ordinary tab visible AND focused for the whole interval, with an explicit cut time taken BEFORE the
  re-attach (events caused by attaching are never counted);
* the Chrome command line actually launched is recorded and must carry no throttling/backgrounding/freezing
  exemption flag. LIMITATION (not hidden, not relaxed): this scan only proves those *flags* are absent; it
  does not measure Chrome-internal behaviour. The RFC wording ("no ... other timer-throttling exemption
  active") was clarified by the owner-approved 2026-10-27 amendment (OTP-23 only): event delivery through the
  architecture's Native Messaging port is NOT a timer-throttling exemption; ordinary timers and the port
  itself are permitted, and the criterion keeps forbidding opt-outs, artificial keepalive, audio, WebRTC,
  exemption flags and DevTools clients during idle. Its unsatisfied-report condition (a same-build comparison
  in which a port tab escapes intensive timer throttling while a port-less control tab does not) belongs to
  separate same-build probe evidence, not to this per-run evaluation; the 156.0.8078.48 probes on Windows and
  macOS found port tab and port-less control both intensively throttled. This is recorded in the ST-13
  detail; ``formal_otp23_acceptance`` is always false and the requirements matrix adjudicates.

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
CLOCK_SKEW_S = 120
WARMUP_SEEDS = (41, 53)
HISTORY_KINDS = ("visibilitychange", "focus", "blur", "freeze", "resume")
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


# Flag names (lower case, without value) that disable or weaken background throttling / freezing /
# occlusion handling. ``--disable-features`` / ``--enable-features`` values are scanned separately.
BANNED_CHROME_FLAGS = (
    "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows", "--disable-background-media-suspend",
    "--disable-ipc-flooding-protection", "--disable-throttle-non-visible-cross-origin-iframes",
    "--run-all-compositor-stages-before-draw",
)
BANNED_FEATURE_WORDS = ("throttl", "freez", "backgrounding", "occlusion", "intensivewakeup", "wakeup", "suspend",
                        "autodiscard", "discard", "backgroundtab", "backgroundrenderer")

# Installed into the ordinary tab BEFORE the last DevTools client closes. Listeners only: no timers,
# intervals, requestAnimationFrame, audio or sockets. Returns the page token + install time.
ORDINARY_HISTORY_JS = """(() => {
  const state = () => ({wall: Date.now(), visibility: document.visibilityState, focused: document.hasFocus()});
  const h = {token: Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10),
             installed_wall: Date.now(), initial: state(), events: [], truncated: false, limit: 2000};
  const rec = (kind) => {
    if (h.events.length >= h.limit) { h.truncated = true; return; }
    h.events.push(Object.assign({seq: h.events.length, kind: kind}, state()));
  };
  document.addEventListener('visibilitychange', () => rec('visibilitychange'));
  window.addEventListener('focus', () => rec('focus'));
  window.addEventListener('blur', () => rec('blur'));
  document.addEventListener('freeze', () => rec('freeze'));
  document.addEventListener('resume', () => rec('resume'));
  window.__otp23History = h;
  return {token: h.token, installed_wall: h.installed_wall};
})()"""
ORDINARY_READ_JS = "(() => { const h = window.__otp23History; return h ? JSON.parse(JSON.stringify(h)) : null; })()"


def scan_chrome_args(args):
    """args: list of the launched Chrome argv. Returns findings for exemption flags ([] = none found).
    None/empty args are reported as unrecorded, which the caller treats as a failure."""
    if not args:
        return [{"flag": None, "kind": "command_line_not_recorded"}]
    findings = []
    if len(args) == 1:
        # A single string is a whole command line (OS query fallback), not just the executable: tokenise
        # every flag in it rather than skipping "argv[0]".
        tokens = re.findall(r'--[^\s"=]+(?:=(?:"[^"]*"|\S+))?', str(args[0]))
    else:
        tokens = [str(a) for a in args[1:]]
    for text in tokens:
        text = text.replace('"', "")
        if not text.startswith("--"):
            continue
        name, _, value = text.partition("=")
        name = name.lower()
        if name in BANNED_CHROME_FLAGS:
            findings.append({"flag": text, "kind": "throttling_exemption_flag"})
        elif name in ("--disable-features", "--enable-features", "--force-fieldtrials", "--js-flags"):
            hit = [w for w in BANNED_FEATURE_WORDS if w in value.lower()]
            if hit:
                findings.append({"flag": text, "kind": "feature_override_touches_throttling", "words": hit})
    return findings


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def evaluate_history(history, closed_wall_s, cut_wall_s, min_s, want_visibility, want_focused,
                     expected_token=None):
    """Replay one page's preconfigured history over [closed_wall_s, cut_wall_s] (cut taken BEFORE attach).

    Returns (status, detail). Missing/truncated/inconsistent history, a missing initial visibility or focus,
    any state change that leaves the required state inside the interval, or a short interval FAILS."""
    if not isinstance(history, dict):
        return "FAIL", {"problems": ["history absent"]}
    problems = []
    initial, events = history.get("initial"), history.get("events")
    if history.get("truncated"):
        problems.append("history truncated")
    if expected_token is not None and history.get("token") != expected_token:
        problems.append("history token differs from the one installed before DevTools closed (page replaced)")
    if not isinstance(initial, dict) or not _num(initial.get("wall")) \
            or initial.get("visibility") not in ("visible", "hidden") or not isinstance(initial.get("focused"), bool):
        problems.append("initial state missing visibility/focus/wall")
        initial = None
    if not isinstance(events, list):
        problems.append("events missing")
        events = []
    if closed_wall_s is None or cut_wall_s is None:
        problems.append("interval boundaries missing")
        return "FAIL", {"problems": problems}
    closed_ms, cut_ms = closed_wall_s * 1000.0, cut_wall_s * 1000.0
    last_wall = initial["wall"] if initial else None
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("seq") != index:
            problems.append(f"event {index}: sequence gap or malformed")
            break
        if event.get("kind") not in HISTORY_KINDS or not _num(event.get("wall")):
            problems.append(f"event {index}: unknown kind or missing wall")
            break
        if event["kind"] not in ("freeze", "resume") and (
                event.get("visibility") not in ("visible", "hidden") or not isinstance(event.get("focused"), bool)):
            problems.append(f"event {index}: missing visibility/focus")
            break
        if event["kind"] == "focus" and event.get("focused") is not True \
                or event["kind"] == "blur" and event.get("focused") is not False:
            problems.append(f"event {index}: {event['kind']} contradicts its own focus state")
            break
        if last_wall is not None and event["wall"] < last_wall:
            problems.append(f"event {index}: wall clock went backwards")
            break
        last_wall = event["wall"]
    if initial and initial["wall"] > closed_ms:
        problems.append("history installed after the last DevTools client closed")
    if problems:
        return "FAIL", {"problems": problems}

    def ok(visibility, focused):
        return visibility == want_visibility and focused is want_focused

    state = (initial["visibility"], initial["focused"])
    for event in events:
        if event["wall"] > closed_ms:
            break
        if event["kind"] not in ("freeze", "resume"):
            state = (event["visibility"], event["focused"])
    in_window, interruptions, post_cut, notes = [], [], 0, []
    if not ok(*state):
        interruptions.append({"at": "interval start", "visibility": state[0], "focused": state[1]})
    for event in events:
        if event["wall"] <= closed_ms:
            continue
        if event["wall"] > cut_ms:
            post_cut += 1  # caused by (or racing) the re-attach: explicitly outside the measured interval
            continue
        in_window.append(event["kind"])
        if event["kind"] in ("freeze", "resume"):
            notes.append(event["kind"])
            continue
        state = (event["visibility"], event["focused"])
        if not ok(*state):
            interruptions.append({"at_wall_ms": event["wall"], "kind": event["kind"],
                                  "visibility": state[0], "focused": state[1]})
    seconds = round((cut_ms - closed_ms) / 1000.0, 3)
    if seconds < min_s:
        problems.append(f"interval {seconds} s < {min_s} s")
    if interruptions:
        problems.append("interrupted interval")
    detail = {"problems": problems, "interval_seconds": seconds, "interruptions": interruptions,
              "events_in_interval": in_window, "events_after_cut_ignored": post_cut,
              "lifecycle_notes": notes, "initial": initial, "token": history.get("token")}
    return ("FAIL" if problems else "PASS"), detail


def expected_unsolicited(length=RECORD_LEN, seed=UNSOLICITED_SEED):
    """The phone fixture's type-3 record, recomputed independently of the Android code."""
    record = bytearray((i + seed) & 0xFF for i in range(length))
    record[:4] = b"VEL" + bytes([RECORD_TYPE_UNSOLICITED])
    return bytes(record)


def warmup_reply_sha256(seed):
    """sha256 of the host/phone echo of the warm-up record of this seed (type byte 1 -> 2)."""
    data = bytearray((i + seed) & 0xFF for i in range(RECORD_LEN))
    data[:4] = b"VEL\x02"
    return hashlib.sha256(bytes(data)).hexdigest()


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


def sample_foreground(read_pid, owns, session_ok=None, ps_args=None, page_ok=None):
    """One OS foreground sample. verified=False when the foreground owner cannot be read (locked desktop, no
    foreground window, API error) or, when `session_ok` is supplied (macOS), the session's lock/console state
    is not provably on-console AND unlocked: a locked screen or off-console session, or an unreadable state,
    leaves the sample unverifiable (fail closed - the frontmost app cannot be trusted then). `owned` only
    counts when verified. `read_pid` returns an int pid or a {'pid', 'name', 'bundle'} detail dict (macOS):
    name/bundle are audit-only. `ps_args`, when supplied, records the foreground pid's command line; its
    failure is recorded as None and never changes verified/owned. `page_ok`, when supplied, is the locked-
    screen counter-evidence read at sample time (macOS preflight P-2: the frontmost reader keeps reporting
    the previous app while the screen is locked, so the unlocked consistency comes from the page itself); the
    payload is recorded as `page_state` and, when it is present, it must say ok=True for a sample to confirm
    the foreground gate - hidden/locked/unreadable page state never confirms. macOS runs treat the page-state
    guard as REQUIRED evidence (ST-15); other platforms record it, and their gate keeps confirming on the
    pid/session result alone. Calibration raw: evidence/post-grant-2026-10-26/strict-mac-20261027/
    calibration-cal-ssh-20261027T155808Z.jsonl (preflight-2 reviewed snapshot sha256 a517e5e6…; it shows
    (a)-(d) click distinguishing and the locked interval). It is lock counter-evidence, not a foreground
    proof by itself."""
    session = None
    if session_ok is not None:
        try:
            session = session_ok()
        except Exception:
            session = {"verified": False}
        if not isinstance(session, dict):
            session = {"verified": False}
        if not session.get("verified") or session.get("on_console") is not True \
                or session.get("screen_locked") is not False:
            return {"verified": False, "pid": None, "owned": False, "session": session}
    try:
        read = read_pid()
    except Exception:
        read = None
    detail = {}
    pid = read
    if isinstance(read, dict):
        pid = read.get("pid")
        detail = {"name": read.get("name"), "bundle": read.get("bundle")}
    extra = {}
    if session_ok is not None:
        extra["session"] = session
    if ps_args is not None:
        try:
            extra["ps_args"] = ps_args(pid) if pid else None
        except Exception:
            extra["ps_args"] = None
    if page_ok is not None:
        try:
            page = page_ok()
        except Exception as exc:
            page = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        if not isinstance(page, dict):
            page = {"ok": False, "error": "junk page state"}
        extra["page_state"] = page
    if not pid:
        return dict({"verified": False, "pid": None, "owned": False}, **detail, **extra)
    try:
        owned = bool(owns(pid))
    except Exception:
        return dict({"verified": False, "pid": pid, "owned": False}, **detail, **extra)
    return dict({"verified": True, "pid": pid, "owned": owned}, **detail, **extra)


def wait_for_foreground(sample, timeout_s, poll_s, clock, sleep, on_poll=None):
    """Bounded pre-test controller poll (no page-side timer): wait for a human to put the owned browser in the
    OS foreground. state: confirmed | timeout (readable but never owned) | unverifiable (never readable).
    When a sample carries a page_state payload, confirmation additionally requires it to be consistent with
    an unlocked console (ok True): samples without that key (e.g. Windows) confirm on the pid/session result
    alone."""
    started = clock()
    deadline = started + timeout_s
    polls, seen_verified, last = 0, False, None
    while True:
        last = sample()
        polls += 1
        seen_verified = seen_verified or last["verified"]
        page_ok = (last.get("page_state") or {}).get("ok", True)
        if last["verified"] and last["owned"] and page_ok:
            return {"state": "confirmed", "polls": polls, "waited_s": round(clock() - started, 3), "last": last}
        if on_poll:
            on_poll(last, polls)
        if clock() >= deadline:
            return {"state": "timeout" if seen_verified else "unverifiable", "polls": polls,
                    "waited_s": round(clock() - started, 3), "last": last}
        sleep(min(poll_s, max(0.0, deadline - clock())))


def _foreground_check(fg, platform=None):
    """ST-15 over evidence['os_foreground']. Every strict run (Windows and macOS) must show the real OS
    foreground owned by this run's Chrome before any BLE work and again after the tab switch, before every
    DevTools client closes. Legacy mac raws (which recorded {"required": False} and no gate) and any run
    that recorded no gate at all stay BLOCKED; the macOS gate is not waived. The platform comes from a
    TRUSTED source - the caller passes the result's top-level platform; a new-schema os_foreground
    (self-recorded platform or page_state_guard key) whose trustworthy platform is missing or unknown (e.g.
    "darwin") is BLOCKED, never adjudicated by a guessed platform. Explicit legacy detection keeps the
    pre-page-guard Windows raws (gate, no platform key, no page_state_guard) audit-only as they always
    were. macOS additionally requires the recorded session dictionaries (verified, on-console, unlocked)
    and the page-state guard at both sample points (preflight P-2 / preflight-2 Q-1)."""
    expected = ("OS foreground owned by the run's own Chrome PID (directly or through its ancestry) before "
                "any BLE work and again after the ordinary-tab switch, before every DevTools client closes")
    if not isinstance(fg, dict) or fg.get("required") is not True:
        return _check("BLOCKED", expected, "no foreground evidence recorded")
    legacy = "platform" not in fg and "page_state_guard" not in fg
    trusted = platform or fg.get("platform")
    if not legacy and trusted not in ("macos", "windows"):
        return _check("BLOCKED", expected, "%s platform is missing or unknown" % (trusted,))
    self_recorded = fg.get("platform")
    if trusted and self_recorded and trusted != self_recorded:
        return _check("BLOCKED", expected, "trusted result platform %s contradicts the foreground's "
                      "self-recorded platform %s; the sample cannot be adjudicated" % (trusted, self_recorded))
    page_required = trusted == "macos"
    gate, after = fg.get("gate") or {}, fg.get("after_switch") or {}
    detail = {"gate_state": gate.get("state"), "gate_pid": (gate.get("last") or {}).get("pid"),
              "chrome_pid": fg.get("chrome_pid"), "after_switch": after, "reader": fg.get("reader"),
              "platform": trusted, "page_required": page_required,
              "session_guard": fg.get("session_guard"),
              "gate_confirmed_mono": fg.get("gate_confirmed_mono"), "ble_start_mono": fg.get("ble_start_mono")}
    if gate.get("state") != "confirmed":
        return _check("BLOCKED", expected, detail)  # nobody confirmed the foreground: nothing was measured
    if page_required:
        gate_page = (gate.get("last") or {}).get("page_state")
        if not isinstance(gate_page, dict) or not gate_page.get("ok"):
            return _check("BLOCKED", expected, detail)  # gate confirmed without unlocked-consistent page state
        gate_session = (gate.get("last") or {}).get("session")
        if gate_session != {"verified": True, "on_console": True, "screen_locked": False}:
            return _check("BLOCKED", expected, detail)  # gate sample not provably on-console + unlocked
    ordered = core_num(fg.get("gate_confirmed_mono")) and core_num(fg.get("ble_start_mono")) \
        and fg["gate_confirmed_mono"] < fg["ble_start_mono"]
    if not ordered:
        return _check("FAIL", expected, detail)  # BLE work not provably after the confirmed foreground
    if not after.get("verified"):
        return _check("BLOCKED", expected, detail)
    if page_required:
        after_page = after.get("page_state")
        if not isinstance(after_page, dict):
            return _check("BLOCKED", expected, detail)  # page-state guard not recorded at the after-switch sample
        if not after_page.get("ok"):
            return _check("FAIL", expected, detail)  # screen locked or Chrome not visibly front at that point
        after_session = after.get("session")
        if after_session != {"verified": True, "on_console": True, "screen_locked": False}:
            return _check("BLOCKED", expected, detail)  # after-switch sample not provably on-console + unlocked
    return _check("PASS" if after.get("owned") is True else "FAIL", expected, detail)


def core_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check(status, expected, actual):
    return {"status": status, "expected": expected, "actual": actual}


def _all_pass(checks):
    values = [c["status"] for c in checks.values()]
    if "FAIL" in values:
        return "FAIL"
    return "PASS" if all(v == "PASS" for v in values) else "BLOCKED"


def evaluate(evidence, platform=None):  # noqa: C901 - one flat list of independent requirement checks
    """Independent checks over a run's evidence dict; see the module docstring for the contract. `platform`
    is the TRUSTED top-level platform of the result the evidence came from (callers pass it; ST-15 also
    accepts the os_foreground's self-recorded platform, and a new-schema foreground with neither is
    BLOCKED)."""
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
    validation = evidence.get("phone_validation")
    if not validation:
        checks["ST-08"] = _check("BLOCKED", "phone evidence validated by strict_phone_validation (sources, "
                                 "freshness, clock-matched receipts)", "no validation recorded")
    elif validation.get("status") != "PASS":
        checks["ST-08"] = _check(validation.get("status", "FAIL"),
                                 "two distinct current-run phone evidence files, validated after the idle interval",
                                 {"problems": validation.get("problems", [])})
    else:
        pushes = [p for item in (phones or []) for p in item.get("pushes", [])]
        good = len(pushes) == len(handles) >= 2 and all(
            p.get("queued") is True and p.get("sha256") == want and p.get("bytes") == RECORD_LEN for p in pushes)
        checks["ST-08"] = _check("PASS" if good else "FAIL",
                                 "each phone pushed the expected hash once, queued, after its configured delay",
                                 {"pushes": len(pushes), "validation": "PASS"})

    scenario = evidence.get("scenario")
    trace_sends = trace.get("sends_in_trace")
    if scenario == "cold":
        ok = trace_sends == 0 and not evidence.get("warmup")
        checks["ST-09"] = _check("PASS" if ok else "FAIL",
                                 "cold idle: no desktop->phone record was ever sent in this run",
                                 {"sends_in_host_trace": trace_sends, "warmup": bool(evidence.get("warmup"))})
    elif scenario == "prewarm":
        warm = evidence.get("warmup") or {}
        valid = {warmup_reply_sha256(seed) for seed in WARMUP_SEEDS}
        hashes = [(v or {}).get("sha256") for v in warm.values()]
        proof = bool(handles) and set(warm) == set(handles) and all(h in valid for h in hashes) \
            and len(set(hashes)) == len(hashes)
        ok = proof and trace_sends == len(handles)
        checks["ST-09"] = _check("PASS" if ok else "FAIL",
                                 "prewarm: exactly one byte-verified exchange per phone (exact warm-up reply hashes, sends == phones)",
                                 {"sends_in_host_trace": trace_sends, "warmup": evidence.get("warmup")})
    else:
        checks["ST-09"] = _check("FAIL", "scenario must be 'cold' or 'prewarm'", scenario)

    cleanup = evidence.get("cleanup") or {}
    checks["ST-10"] = _check(
        "PASS" if cleanup.get("shutdown_complete") is True and cleanup.get("surviving_hosts") == []
        and cleanup.get("chrome_closed") is True else "FAIL",
        "shutdown_complete, Chrome closed, and no surviving host process counted AFTER Chrome closed", cleanup)

    cut = timeline.get("cdp_reopened_wall")
    history = evidence.get("history") or {}
    tokens = evidence.get("history_tokens") or {}
    for check_id, key, visibility, focused, label in (
            ("ST-11", "connector", "hidden", False, "connector page hidden and unfocused"),
            ("ST-12", "ordinary_tab", "visible", True, "ordinary tab visible and focused")):
        status, detail = evaluate_history(history.get(key), closed_wall, cut, min_background, visibility, focused,
                                          tokens.get(key))
        checks[check_id] = _check(
            status, f"preconfigured history shows the {label} for >= {min_background} s with no interruption "
            "(events after the pre-attach cut ignored)", detail)

    chrome = evidence.get("chrome") or {}
    flag_findings = scan_chrome_args(chrome.get("args"))
    checks["ST-13"] = _check(
        "PASS" if not flag_findings else "FAIL",
        "recorded Chrome command line carries no throttling/backgrounding/freezing exemption flag",
        {"findings": flag_findings, "arg_count": len(chrome.get("args") or []),
         "args_source": chrome.get("args_source"),
         "native_messaging_port": "not a timer-throttling exemption (owner-approved RFC clarification, "
                                  "2026-10-27): event delivery through the Native Messaging port is not an "
                                  "exemption; ordinary timers and the port itself are permitted",
         "scope": "flags only; this check does not measure Chrome-internal behaviour, and the same-build "
                  "port-escape comparison lives in separate probe evidence"})

    checks["ST-15"] = _foreground_check(evidence.get("os_foreground"), platform)

    diagnostic = bool(evidence.get("diagnostic_short"))
    checks["ST-14"] = _check(
        "PASS" if not diagnostic and min_background >= MIN_BACKGROUND_S else "FAIL",
        f"formal strict run: min_background_s >= {MIN_BACKGROUND_S} and not a diagnostic short run",
        {"min_background_s": min_background, "diagnostic_short": diagnostic})

    result = _all_pass(checks)
    failing = [k for k, c in checks.items() if c["status"] == "FAIL"]
    if diagnostic and failing == ["ST-14"]:
        result = "DIAGNOSTIC_ONLY"  # everything else held, but a short run can never be a formal strict PASS
    return {"strict_result": result, "formal_otp23_acceptance": False, "checks": checks,
            "scenario": scenario, "diagnostic_short": diagnostic}
