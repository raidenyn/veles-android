"""Strict OTP-23 background-delivery validation (fresh evidence; does not replace poc_background.py).

Sequence: Chrome + connector -> hello -> two phones connected -> [prewarm only: one byte-verified 4096-byte
exchange per phone] -> open and focus an ordinary about:blank tab (connector hidden) -> CLOSE EVERY DevTools
client (the connector session dies with the browser-level websocket; this process then holds no CDP object at
all) -> sleep with no browser, host or phone traffic until each phone's preconfigured unsolicited push is due
-> read the DevTools HTTP target list (no session) -> re-attach -> read the page's own receipt timestamps ->
shutdown. The phones must already run SharingFeasibilityTest with ``-e otp23 true -e
otp23_unsolicited_after_s 690`` (see docs/otp-23-requirements-matrix.md for the exact commands). The verdict
logic lives in strict_background_core.py; ``formal_otp23_acceptance`` is always false here.

Scenarios are separate runs: ``--scenario cold`` never sends anything desktop->phone; ``--scenario prewarm``
performs the byte-verified exchange first. Do not merge their evidence.

Phone evidence: ``--adb SERIAL`` (repeatable; the PC the phones are USB-attached to) pulls
files/otp23-unsolicited.json AFTER the idle interval; on a machine without adb pass ``--phone-evidence
PATH`` files fetched later, else the phone check stays BLOCKED rather than guessed.

Usage: python scripts/strict_background.py --scenario cold --out evidence/strict-cold.json [--adb SERIAL ...]
"""

import argparse
import base64
import hashlib
import json
import pathlib
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone

import strict_background_core as core
import strict_phone_validation as spv

if sys.platform == "darwin":
    import macos_background_support as cv
    from macos_cdp import Cdp, WebSocket
    PLATFORM = cv.PLATFORM
else:
    import chrome_validate as cv
    from chrome_validate import Cdp, WebSocket
    PLATFORM = "windows"

FIXED_ID = "gmekhgkipjkikmaadaajljinkgfielei"
EXT_DIR = pathlib.Path(__file__).resolve().parent.parent / "extension"
PACKAGE = "me.nagaev.veles.otp23"
WARMUP_SEEDS = core.WARMUP_SEEDS
WARMUP_TIMEOUT_S = 12
EVIDENCE_FILE = "files/otp23-unsolicited.json"


# Run INSIDE the connector page (an extension page: chrome.tabs.getCurrent/create and chrome.windows.update
# need no manifest permission). The ordinary tab is created in the connector's own window and focused there;
# no visibility override, minimisation or keepalive is involved.
ORDINARY_TAB_JS = """(async (url) => {
  const here = await chrome.tabs.getCurrent();
  const created = await chrome.tabs.create({windowId: here.windowId, url: url, active: true});
  await chrome.windows.update(here.windowId, {focused: true});
  return {connector_tab_id: here.id, connector_window_id: here.windowId,
          ordinary_tab_id: created.id, ordinary_window_id: created.windowId};
})(%s)"""


def utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pull_phone_evidence(serial, adb="adb", runner=subprocess.run):
    """Raw text of the hash-only evidence file the instrumentation wrote; None when it cannot be read."""
    result = runner([adb, "-s", serial, "shell", "run-as", PACKAGE, "cat", EVIDENCE_FILE],
                    capture_output=True, text=True, timeout=60)
    return result.stdout if result.returncode == 0 else None


def chrome_command_line(chrome):
    """The command line Chrome was actually launched with: proc.args (Popen), else an OS query by pid."""
    args = getattr(getattr(chrome, "proc", None), "args", None)
    if args:
        return [str(a) for a in args], "proc.args"
    pid = getattr(getattr(chrome, "proc", None), "pid", None)
    if pid is None:
        return None, "unavailable"
    try:
        if PLATFORM == "windows":
            rows = cv.powershell_json(f"Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' | "
                                      "Select-Object CommandLine")
            line = rows[0].get("CommandLine") if rows else None
            return ([line], "Win32_Process.CommandLine (single string)") if line else (None, "unavailable")
        out = subprocess.run(["ps", "-p", str(int(pid)), "-o", "args="], capture_output=True, text=True,
                             timeout=30).stdout.strip()
        return ([out], "ps args (single string)") if out else (None, "unavailable")
    except Exception:
        return None, "unavailable"


def collect_phone_evidence(paths, serials, adb_path, runner=subprocess.run):
    """Source-attributed phone records; never raises (failures are recorded as error strings).
    paths: 'SERIAL=PATH' (a bare PATH has no provenance and can never PASS); serials: adb pulls."""
    records, errors = [], []
    for text in paths:
        source, path = spv.parse_phone_arg(text)
        record = spv.read_input(path, source)
        if record["error"]:
            errors.append(f"phone evidence file #{len(records) + 1}: {record['error']}")
        records.append(record)
    for index, serial in enumerate(serials, 1):
        source = spv.source_hash(serial)
        try:
            text = pull_phone_evidence(serial, adb_path, runner)
        except Exception as error:
            text = None
            errors.append(f"adb pull #{index}: {type(error).__name__}")
        if text is None:
            errors.append(f"adb pull #{index}: no readable evidence file")
            continue
        records.append(spv.record_from_text(text, source, time.time(), f"adb:{source[:16]}"))
    return records, errors


def warmup_record(seed):
    data = bytearray((i + seed) & 255 for i in range(4096))
    data[:4] = b"VEL\x01"
    return bytes(data)


def run(args):
    exe = cv.EXE_NAME["plain"]
    trace_path = pathlib.Path(args.out).resolve().with_suffix(".trace.log")
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.unlink(missing_ok=True)
    result = {"platform": PLATFORM, "scenario": args.scenario, "formal_otp23_acceptance": False,
              "meta": {"started_utc": utc(), "host_trace_log": str(trace_path),
                       "phone_delay_s": args.phone_delay_s, "min_background_s": args.min_background_s,
                       "diagnostic_short": bool(args.diagnostic_short)},
              "cases": []}
    st = {"hosts_before": {p["ProcessId"] for p in cv.host_processes(exe)}, "connected_mono": {}}
    evidence = {"scenario": args.scenario, "min_background_s": args.min_background_s, "timeline": {},
                "handles": [], "events": [], "warmup": None, "diagnostic_short": bool(args.diagnostic_short),
                "history": {}, "history_tokens": {}, "chrome": {}}
    chrome = None

    def step(case_id, title, fn):
        started, record = time.time(), {"id": case_id, "title": title, "result": "FAIL", "detail": None}
        try:
            record.update({"result": "PASS", "detail": fn() or {}})
        except Exception as error:
            record["detail"] = f"{type(error).__name__}: {error}"
        record["seconds"] = round(time.time() - started, 2)
        print(f"[{record['result']:5}] {case_id} {title}")
        result["cases"].append(record)
        return record["result"] == "PASS"

    def ph_env():
        nonlocal chrome
        chrome = cv.ChromeRun({"VELES_OTP23_TRACE": str(trace_path)}, medium=True)
        ext = chrome.load_extension()
        cv.expect(ext["id"] == FIXED_ID, f"extension id {ext['id']} != fixed {FIXED_ID}")
        port, path = (chrome.profile / "DevToolsActivePort").read_text().splitlines()[:2]
        st["port"], st["path"] = int(port), path
        st["target"], st["session"] = chrome.open_page(f"chrome-extension://{FIXED_ID}/connector.html")
        for _ in range(50):
            if chrome.js(st["session"], "typeof window.otp23 === 'object'"):
                break
            time.sleep(0.1)
        result["meta"]["browser_version"] = chrome.version.get("product")
        argv, source = chrome_command_line(chrome)
        evidence["chrome"] = {"args": argv, "args_source": source}
        evidence["history_tokens"]["connector"] = chrome.js(st["session"], "otp23.visibilityHistory().token")
        files = {p.name: p.read_text(encoding="utf-8") for p in sorted(EXT_DIR.iterdir())
                 if p.suffix in (".js", ".html", ".json")}
        evidence["exemption_scan"] = {"files": sorted(files), "findings": core.scan_extension_for_exemptions(files)}
        return {"extension_id": ext["id"], "exemption_findings": evidence["exemption_scan"]["findings"]}

    def ph_hello():
        before = {p["ProcessId"] for p in cv.host_processes(exe)}
        chrome.js(st["session"], f"otp23.connect({json.dumps(cv.HOSTS['plain'])}); 1")
        st["hello_wall"] = time.time()
        cv.expect(chrome.js(st["session"], "otp23.hello()").get("type") == "ready", "hello did not return ready")
        fresh = [p for p in cv.host_processes(exe) if p["ProcessId"] not in before]
        cv.expect(len(fresh) == 1, f"expected one new host process, saw {fresh}")
        st["owned_pid"] = fresh[0]["ProcessId"]
        return {"host_pid": st["owned_pid"]}

    def ph_phones():
        scan = chrome.js(st["session"], f"otp23.startScan({json.dumps(cv.VELES_SERVICE)})")
        cv.expect(scan.get("status") == "started", f"startScan {scan}")
        deadline, devices = time.time() + 40, set()
        while time.time() < deadline and len(devices) < 2:
            devices.update(e["message"].get("device_handle") for e in chrome.js(st["session"], "otp23.drainEvents()")
                           if e["message"].get("type") == "device_found")
            time.sleep(0.5)
        cv.expect(len(devices) >= 2, f"two physical phones required; found {len(devices)}")
        st["handles"] = {}
        for device in sorted(devices)[:2]:
            connected = chrome.js(st["session"], f"otp23.connectDevice({{device_handle: {json.dumps(device)}}})", timeout=70)
            cv.expect(connected.get("type") == "connected", f"connect {device[:8]}: {connected.get('code')}")
            handle = connected["connection_handle"]
            st["handles"][device] = handle
            st["connected_mono"][handle] = time.monotonic()  # the phones' delay starts at their CCCD write, just before
        stopped = chrome.js(st["session"], f"otp23.stopScan({json.dumps(scan['scan_handle'])})")
        cv.expect(stopped.get("type") == "scan_state", f"stopScan {stopped}")
        chrome.js(st["session"], "otp23.drainEvents(); 1")
        evidence["handles"] = [h[:8] for h in st["handles"].values()]
        return {"phones": 2}

    def ph_warmup():
        warmed = {}
        for (device, handle), seed in zip(sorted(st["handles"].items()), WARMUP_SEEDS):
            record = warmup_record(seed)
            expected = base64.b64encode(record[:3] + b"\x02" + record[4:]).decode()
            call = (f"otp23.send({json.dumps(handle)}, Uint8Array.from(atob({json.dumps(base64.b64encode(record).decode())}), "
                    f"c => c.charCodeAt(0)))")
            cv.expect(chrome.js(st["session"], call).get("type") == "sent", f"warmup send to {handle[:8]} failed")
            end, got = time.time() + WARMUP_TIMEOUT_S, None
            while time.time() < end and got is None:
                for event in chrome.js(st["session"], "otp23.drainEvents()"):
                    message = event["message"]
                    if message.get("type") == "message" and message.get("connection_handle") == handle:
                        got = message
                time.sleep(0.5)
            cv.expect(got is not None, f"no warmup reply from {handle[:8]} within {WARMUP_TIMEOUT_S} s")
            cv.expect(got["record_base64"] == expected, f"warmup reply from {handle[:8]} is not byte-exact")
            warmed[handle[:8]] = {"seed": seed, "sha256": hashlib.sha256(base64.b64decode(expected)).hexdigest()}
        evidence["warmup"] = warmed
        return warmed

    def core_js(url):
        return ORDINARY_TAB_JS % json.dumps(url)

    def ph_background():
        """Focus an ordinary tab, prove the connector is hidden, then close EVERY DevTools client."""
        url = "about:blank#otp23-ordinary-" + uuid.uuid4().hex
        binding = chrome.js(st["session"], core_js(url), timeout=30)
        cv.expect(binding.get("ordinary_window_id") == binding.get("connector_window_id"),
                  f"ordinary tab opened in another window: {binding}")
        other = None
        for _ in range(60):
            found = [t for t in chrome.cdp.call("Target.getTargets")["targetInfos"]
                     if t.get("type") == "page" and t.get("url") == url]
            if found:
                other = found[0]["targetId"]
                break
            time.sleep(0.25)
        cv.expect(other is not None, "ordinary tab CDP target not found by its unique URL")
        windows = {name: chrome.cdp.call("Browser.getWindowForTarget", {"targetId": target})["windowId"]
                   for name, target in (("connector", st["target"]), ("ordinary_tab", other))}
        cv.expect(windows["connector"] == windows["ordinary_tab"], f"targets are in different windows: {windows}")
        other_session = chrome.cdp.call("Target.attachToTarget", {"targetId": other, "flatten": True})["sessionId"]
        chrome.cdp.call("Runtime.enable", session=other_session)
        # Preconfigure the ordinary tab's visibility/focus history (listeners only) before any DevTools
        # client closes; it is read back only after the measurement.
        installed = chrome.js(other_session, core.ORDINARY_HISTORY_JS)
        cv.expect(isinstance(installed, dict) and installed.get("token"), "ordinary-tab history not installed")
        evidence["history_tokens"]["ordinary_tab"] = installed["token"]
        st["other_target"] = other
        chrome.js(st["session"], "chrome.tabs.getCurrent().then(t => chrome.windows.update(t.windowId, "
                                 "{focused: true})).then(() => 1)", timeout=30)
        hidden = visible = None
        for _ in range(40):
            hidden = chrome.js(st["session"], "document.visibilityState")
            visible = chrome.js(other_session, "document.visibilityState")
            if hidden == "hidden" and visible == "visible" and chrome.js(other_session, "document.hasFocus()"):
                break
            time.sleep(0.25)
        evidence["same_window_binding"] = {"chrome_tabs": binding, "cdp_window_ids_equal": True,
                                           "created_by": "chrome.tabs.create(windowId=connector window, active)"}
        evidence["pre_close_probe"] = {
            "connector_visibility": hidden, "ordinary_tab_visibility": visible,
            "ordinary_tab_focused": bool(chrome.js(other_session, "document.hasFocus()"))}
        probe = evidence["pre_close_probe"]
        cv.expect(probe["connector_visibility"] == "hidden" and probe["ordinary_tab_visibility"] == "visible"
                  and probe["ordinary_tab_focused"] is True,
                  f"background setup not reached (connector hidden / ordinary visible+focused): {probe}")
        timeline = evidence["timeline"]
        timeline["background_started_wall"] = time.time()
        # From here on this process owns no DevTools object. Any later use would raise AttributeError.
        timeline["cdp_closed_wall"], timeline["cdp_closed_mono"] = time.time(), time.monotonic()
        chrome.cdp.ws.close()
        chrome.cdp = None
        evidence["browser_level_connection_closed"] = True
        evidence["connector_session_detached_by_close"] = True  # sessions are bound to the closed connection
        result["meta"]["cdp_closed_utc"] = utc()
        return evidence["pre_close_probe"]

    def ph_idle():
        deadline = core.idle_deadline_monotonic(st["connected_mono"], args.phone_delay_s, args.grace_s)
        result["meta"]["planned_idle_until_utc_offset_s"] = round(deadline - time.monotonic(), 1)
        while time.monotonic() < deadline:
            time.sleep(min(5.0, max(0.1, deadline - time.monotonic())))
        evidence["cdp_calls_during_idle"] = 0  # structurally: the DevTools object is None for the whole loop above
        return {"slept_s": round(time.monotonic() - evidence["timeline"]["cdp_closed_mono"], 1)}

    def ph_reattach():
        url = f"http://127.0.0.1:{st['port']}/json/list"
        with urllib.request.urlopen(url, timeout=10) as response:  # HTTP target list: no CDP session
            targets = json.loads(response.read().decode())
        mine = [t for t in targets if t.get("id") == st["target"]]
        evidence["title_probe"] = {"title_before_attach": mine[0].get("title") if mine else None}
        timeline = evidence["timeline"]
        timeline["cdp_reopened_wall"], timeline["cdp_reopened_mono"] = time.time(), time.monotonic()
        chrome.cdp = Cdp(WebSocket("127.0.0.1", st["port"], st["path"]))
        session = chrome.cdp.call("Target.attachToTarget", {"targetId": st["target"], "flatten": True})["sessionId"]
        chrome.cdp.call("Runtime.enable", session=session)
        st["reattach_session"] = session
        events = [e for e in chrome.js(session, "otp23.drainEvents()", timeout=120)]
        evidence["history"]["connector"] = chrome.js(session, "otp23.visibilityHistory()")
        other_session = chrome.cdp.call("Target.attachToTarget", {"targetId": st["other_target"],
                                                                  "flatten": True})["sessionId"]
        chrome.cdp.call("Runtime.enable", session=other_session)
        evidence["history"]["ordinary_tab"] = chrome.js(other_session, core.ORDINARY_READ_JS)
        by_handle = {h[:8]: h for h in st["handles"].values()}
        for entry in events:
            message = entry["message"]
            if message.get("type") != "message":
                continue
            raw = base64.b64decode(message["record_base64"])
            evidence["events"].append({
                "handle": message["connection_handle"][:8], "at": entry.get("at"), "wall": entry.get("wall"),
                "visibility": entry.get("visibility"), "focused": entry.get("focused"),
                "record_sha256": hashlib.sha256(raw).hexdigest(), "len": len(raw)})
        cv.expect(set(e["handle"] for e in evidence["events"]) <= set(by_handle), "message from an unknown handle")
        return {"messages": len(evidence["events"]), "title": evidence["title_probe"]["title_before_attach"]}

    def ph_shutdown():
        reply = chrome.js(st["reattach_session"], "otp23.shutdown()", timeout=45)
        cv.expect(reply.get("type") == "shutdown_complete", f"shutdown reply {reply}")
        cv.expect(cv.wait_gone(st["owned_pid"], 15) is not None, "owned host survived shutdown")
        st["shutdown_done"] = True
        return {"shutdown": "complete"}

    phases = [("ST-ENV", "Chrome, fixed-id extension, connector page, exemption scan", ph_env),
              ("ST-HELLO", "hello over the registered plain host", ph_hello),
              ("ST-PHONES", "two physical phones scanned and connected", ph_phones)]
    if args.scenario == "prewarm":
        phases.append(("ST-WARM", "pre-idle byte-verified 4096-byte exchange per phone", ph_warmup))
    phases += [("ST-BG", "ordinary tab focused, connector hidden, every DevTools client closed", ph_background),
               ("ST-IDLE", "idle with no DevTools client until each phone's unsolicited push is due", ph_idle),
               ("ST-REATTACH", "HTTP target list, re-attach, read page receipts", ph_reattach),
               ("ST-SHUTDOWN", "shutdown; host gone", ph_shutdown)]
    try:
        for case_id, title, fn in phases:
            if not step(case_id, title, fn):
                break
    finally:
        cleanup = {"shutdown_complete": bool(st.get("shutdown_done")), "chrome_closed": chrome is None,
                   "surviving_hosts": [], "errors": []}
        try:
            if chrome and chrome.cdp is not None and st.get("reattach_session") and not st.get("shutdown_done"):
                chrome.js(st["reattach_session"], "otp23.shutdown()", timeout=45)
        except Exception as error:
            cleanup["errors"].append(f"late shutdown: {type(error).__name__}")
        if chrome:
            try:
                if chrome.cdp is None and chrome.proc is not None:
                    chrome.cdp = Cdp(WebSocket("127.0.0.1", st["port"], st["path"]))
                chrome.close()  # kills only the Chrome process this run launched, then removes its profile
                cleanup["chrome_closed"] = True
            except Exception as error:
                cleanup["errors"].append(f"chrome close: {type(error).__name__}")
        try:
            # Counted AFTER Chrome closed so a host that was still draining is not reported as an orphan.
            owned = st.get("owned_pid")
            if owned is not None:
                cv.wait_gone(owned, 15)
            cleanup["surviving_hosts"] = sorted({p["ProcessId"] for p in cv.host_processes(exe)} - st["hosts_before"])
        except Exception as error:
            cleanup["errors"].append(f"host scan: {type(error).__name__}")
            cleanup["surviving_hosts"] = ["unknown"]
        evidence["cleanup"] = cleanup

    text = trace_path.read_text(encoding="utf-8", errors="replace") if trace_path.exists() else ""
    trace = core.parse_host_trace(text)
    anchor = st.get("hello_wall")
    evidence["host_trace"] = {
        "message_count": len(trace["messages"]), "sends_in_trace": len(trace["sends"]),
        "message_wall_estimates": [] if anchor is None or trace["hello_ms"] is None else [
            round(anchor + (m["at_ms"] - trace["hello_ms"]) / 1000, 3) for m in trace["messages"]],
        "method": "driver wall time at hello + host trace ms offset since its hello line (approximate)"}
    phone_records, phone_errors = collect_phone_evidence(args.phone_evidence, args.adb, args.adb_path)
    clock = None
    if args.phone_clock_offset_ms is not None and args.phone_clock_uncertainty_ms is not None \
            and args.phone_clock_source:
        clock = {"offset_ms": args.phone_clock_offset_ms, "uncertainty_ms": args.phone_clock_uncertainty_ms,
                 "source": args.phone_clock_source}
    evidence["phone_evidence"] = [r["json"] for r in phone_records if isinstance(r["json"], dict)] or None
    evidence["phone_evidence_errors"] = phone_errors
    try:
        evidence["phone_validation"] = spv.validate_phone_files(
            {"evidence": evidence, "meta": result["meta"]}, phone_records, clock)
    except Exception as error:  # keep the run's evidence; ST-08 stays BLOCKED
        evidence["phone_validation"] = {"status": "BLOCKED", "problems": [f"validation error {type(error).__name__}"]}
    evidence["phone_clock"] = clock
    try:
        verdict = core.evaluate(evidence)
    except Exception as error:  # keep the raw evidence even if the verdict logic itself breaks
        verdict = {"strict_result": "FAIL", "checks": {"EVALUATE": {"status": "FAIL", "expected": "evaluate()",
                                                                      "actual": f"{type(error).__name__}: {error}"}}}
    result.update({"strict_result": verdict["strict_result"], "checks": verdict["checks"], "evidence": evidence})
    result["meta"]["finished_utc"] = utc()
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(cv.scrub(json.dumps(result, indent=2)), encoding="utf-8")
    print(f"strict_result={verdict['strict_result']} scenario={args.scenario} formal_otp23_acceptance=false -> {out}")
    return 0 if verdict["strict_result"] == "PASS" else 1


def positive_int(text):
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["cold", "prewarm"], required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--phone-delay-s", type=positive_int, default=core.PHONE_DELAY_S,
                        help="must equal the phones' -e otp23_unsolicited_after_s")
    parser.add_argument("--min-background-s", type=positive_int, default=core.MIN_BACKGROUND_S,
                        help="below 600 requires --diagnostic-short; such a run can never be a strict PASS")
    parser.add_argument("--diagnostic-short", action="store_true",
                        help="label the run as a short diagnostic (result DIAGNOSTIC_ONLY at best)")
    parser.add_argument("--grace-s", type=positive_int, default=core.GRACE_S)
    parser.add_argument("--adb", action="append", default=[], help="phone serial to pull evidence from (repeatable)")
    parser.add_argument("--adb-path", default="adb")
    parser.add_argument("--phone-evidence", action="append", default=[],
                        help="SERIAL=PATH of a previously pulled evidence file (a bare PATH has no source "
                             "provenance and can never PASS)")
    parser.add_argument("--phone-clock-offset-ms", type=float, help="desktop clock minus phone clock, measured")
    parser.add_argument("--phone-clock-uncertainty-ms", type=float, help="uncertainty of that offset (>= 0)")
    parser.add_argument("--phone-clock-source", help="how the offset was measured (recorded)")
    parsed = parser.parse_args()
    labels = list(parsed.adb) + [p.partition("=")[0] for p in parsed.phone_evidence if "=" in p]
    if len(set(parsed.adb)) != len(parsed.adb) or len(set(labels)) != len(labels):
        parser.error("duplicate phone serial (each physical phone may be supplied once)")
    if parsed.min_background_s < core.MIN_BACKGROUND_S and not parsed.diagnostic_short:
        parser.error(f"--min-background-s below {core.MIN_BACKGROUND_S} requires --diagnostic-short")
    sys.exit(run(parsed))
