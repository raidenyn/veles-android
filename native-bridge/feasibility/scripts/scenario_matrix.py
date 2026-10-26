"""OTP-23 foreground lifecycle scenarios and the M6 two-central driver (stdlib only, Python 3.9+).

Complements physical_validate.py / macos_poc.py (which already cover the two-phone size matrix and
disconnect-one/survivor) with the remaining LIFECYCLE cases and the M6 role driver. Everything is
foreground: no background exemption is measured. Each scenario owns a fresh disposable Chrome
profile/DevTools port and a fresh host process; only processes this run owns are ever terminated.

Run from native-bridge/feasibility after the host is registered (register-host.ps1 /
macos-register.sh) with both OTP-06 phones advertising:

    python scripts/scenario_matrix.py lifecycle --scenario all --out-dir evidence/lifecycle
    python scripts/scenario_matrix.py lifecycle --scenario stdin-eof --out-dir evidence/lifecycle
    python scripts/scenario_matrix.py m6 --role windows --pair-run-id RUN1 --phone-label "OnePlus 6T" \\
        --sole-advertiser --barrier-file C:\\tmp\\release --out evidence/m6-windows.json
    python3 scripts/scenario_matrix.py m6 --role mac ... --out evidence/m6-mac.json
    python scripts/scenario_matrix.py m6-adjudicate --windows evidence/m6-windows.json \\
        --mac evidence/m6-mac.json --out evidence/m6-adjudication.json

Platform helpers are imported lazily (chrome_validate on Windows, macos_background_support on macOS),
so the pure logic and the unit tests run anywhere. Results carry expected / actual / status and a safe
error class; no payload bytes, Bluetooth addresses, platform hints or device labels are recorded.
"""

import argparse
import base64
import json
import os
import pathlib
import platform
import queue
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

import scenario_matrix_core as core

VELES_SERVICE = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51"
ABSENT_HINT = {"windows": "winrt:020000000001:1", "mac": "macos:00000000-0000-0000-0000-00000000dead"}
LIFECYCLE = ("shutdown", "port-disconnect", "tab-close", "stdin-eof", "stdin-eof-truncated",
             "failure-isolation")


class Blocked(Exception):
    """A precondition outside the code under test (phones, permission, platform support)."""

    def __init__(self, error_class, reason):
        super().__init__(reason)
        self.error_class = error_class
        self.reason = reason


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def now_ms():
    return int(time.time() * 1000)


def scrub(text):
    return text.replace(str(pathlib.Path.home()), "%HOME%")


# ---- platform adapters (lazy imports) ---------------------------------------------------------------


class Adapter:
    platform = None
    role = None
    direct_spawn_flags = 0

    def wait_gone(self, pids, timeout=10.0):
        start = time.time()
        for pid in [p for p in pids if p]:
            left = max(0.1, timeout - (time.time() - start))
            if self._wait_one(pid, left) is None:
                return None
        return round(time.time() - start, 3)

    def wrapper_pids(self, pid):
        return []

    def tcc_note(self):
        return None

    def owned_new_hosts(self, before, chrome_pid):
        """Hosts started since `before` whose ancestry includes OUR Chrome: never another browser's."""
        found = []
        for proc in self.host_processes():
            if proc["ProcessId"] in before:
                continue
            if chrome_pid in [link["ProcessId"] for link in self.ancestry(proc["ProcessId"])]:
                found.append(proc)
        return found

    def host_binary(self):
        manifest = pathlib.Path(self.manifest_path())
        document = json.loads(manifest.read_bytes().decode("utf-8"))
        return document["path"]


class WindowsAdapter(Adapter):
    platform, role = "windows", "windows"
    direct_spawn_flags = 0x08000000  # CREATE_NO_WINDOW

    def __init__(self):
        import chrome_validate as cv
        self.cv = cv
        self.exe, self.host_name = cv.EXE_NAME["plain"], cv.HOSTS["plain"]

    def chrome_run(self, env):
        return self.cv.ChromeRun(env, medium=True)

    def host_processes(self):
        return self.cv.host_processes(self.exe)

    def ancestry(self, pid):
        return self.cv.ancestry(pid)

    def _wait_one(self, pid, timeout):
        return self.cv.wait_gone(pid, timeout)

    def wrapper_pids(self, pid):
        wrapper = self.cv.wrapper_of(pid)
        return [wrapper] if wrapper else []

    def hygiene(self, pids):
        windows = {str(p): self.cv.top_level_windows_of(p) for p in pids}
        network = {str(p): self.cv.network_endpoints_of(p) for p in pids}
        return {"visible_windows": sum(1 for v in windows.values() for w in v if w["visible"]),
                "top_level_windows": sum(len(v) for v in windows.values()),
                "network_endpoints": sum(len(v) for v in network.values())}

    def kill(self, pid):
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30)

    def manifest_path(self):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Google\Chrome\NativeMessagingHosts\\" + self.host_name) as key:
            return winreg.QueryValueEx(key, "")[0]


class MacAdapter(Adapter):
    platform, role = "mac", "mac"

    def __init__(self):
        import macos_background_support as support
        import macos_poc as mp
        self.support, self.mp = support, mp
        self.exe, self.host_name = support.EXE_NAME["plain"], support.HOSTS["plain"]

    def chrome_run(self, env):
        return self.support.ChromeRun(extra_env=env)

    def host_processes(self):
        return self.support.host_processes(self.exe)

    def ancestry(self, pid):
        return self.support.ancestry(pid)

    def _wait_one(self, pid, timeout):
        return self.support.wait_gone(pid, timeout)

    def hygiene(self, pids):
        windows = [self.mp.windows_owned_by(p) for p in pids]
        usable = [w for w in windows if w is not None]
        out = {"visible_windows": sum(len(w) for w in usable) if usable else "unavailable",
               "top_level_windows": "unavailable", "network_endpoints": "unavailable"}
        try:
            total = 0
            for pid in pids:
                text = subprocess.run(["lsof", "-nP", "-a", "-p", str(pid), "-i"], capture_output=True,
                                      text=True, timeout=30).stdout
                total += max(0, len([line for line in text.splitlines() if line.strip()]) - 1)
            out["network_endpoints"] = total
        except (OSError, subprocess.SubprocessError):
            pass
        return out

    def kill(self, pid):
        try:
            os.kill(pid, 15)
        except OSError:
            pass

    def manifest_path(self):
        return str(self.support.registered_manifest())

    def tcc_note(self):
        return ("direct launch: Bluetooth TCC is attributed to the launching process (terminal/driver), "
                "not to Google Chrome; this context is NOT Chrome-provenance evidence")


def make_adapter():
    if sys.platform == "win32":
        return WindowsAdapter()
    if sys.platform == "darwin":
        return MacAdapter()
    raise Blocked("unsupported", f"no adapter for platform {sys.platform}")


# ---- Chrome session -------------------------------------------------------------------------------


class Session:
    """One fresh Chrome profile + one host launched on demand through the connector page."""

    def __init__(self, adapter, trace_path, identity):
        self.adapter, self.trace_path, self.identity = adapter, pathlib.Path(trace_path), identity
        self.chrome = None
        self.page = None
        self.target = None
        self.before = set()
        self.host_pid = None
        self.wrappers = []
        self.events = []  # every unsolicited event seen (never contains anything we log verbatim)
        self.unexpected = []
        self.request_ids = []

    def js(self, expression, timeout=70):
        return self.chrome.js(self.page, expression, timeout=timeout)

    def start(self):
        self.trace_path.unlink(missing_ok=True)
        self.before = {p["ProcessId"] for p in self.adapter.host_processes()}
        self.chrome = self.adapter.chrome_run({"VELES_OTP23_TRACE": str(self.trace_path)})
        ext = self.chrome.load_extension()["id"]
        require(ext == self.identity["extension_id"], "extension id drifted from identity.rs")
        self.target, self.page = self.chrome.open_page(f"chrome-extension://{ext}/connector.html")
        for _ in range(50):
            if self.js("typeof window.otp23 === 'object'"):
                break
            time.sleep(0.1)
        self.js(f"otp23.connect({json.dumps(self.adapter.host_name)}); 1")
        ready = self.js("otp23.hello()")
        if ready.get("type") != "ready":
            raise Blocked(core.classify_failure(ready), f"hello failed: {core.safe_reply(ready)}")
        owned = []
        for _ in range(100):
            owned = self.adapter.owned_new_hosts(self.before, self.chrome.proc.pid)
            if owned:
                break
            time.sleep(0.1)
        require(len(owned) == 1, f"expected exactly one owned host process, saw {len(owned)}")
        self.host_pid = owned[0]["ProcessId"]
        self.wrappers = self.adapter.wrapper_pids(self.host_pid)
        return core.safe_reply(ready)

    def owned_pids(self):
        return [self.host_pid] + list(self.wrappers)

    def pump(self):
        batch = self.js("otp23.drainEvents()") or []
        self.events.extend(batch)
        return [e["message"] for e in batch]

    def discover(self, window_s, want=2):
        started = self.js(f"otp23.startScan({json.dumps(VELES_SERVICE)})")
        if started.get("status") != "started":
            raise Blocked(core.classify_failure(started), f"startScan: {core.safe_reply(started)}")
        devices, deadline = {}, time.monotonic() + window_s
        while time.monotonic() < deadline and (want is None or len(devices) < want):
            for message in self.pump():
                if message.get("type") == "device_found":
                    devices[message["device_handle"]] = {"label": message.get("label"),
                                                         "device_handle": message["device_handle"]}
            time.sleep(0.25)
        stopped = self.js(f"otp23.stopScan({json.dumps(started['scan_handle'])})")
        require(stopped.get("status") == "stopped", f"stopScan {core.safe_reply(stopped)}")
        return list(devices.values())

    def connect(self, devices):
        targets = json.dumps([{"device_handle": d["device_handle"]} for d in devices])
        replies = self.js(f"Promise.all({targets}.map(t => otp23.connectDevice(t)))", timeout=80)
        bad = [r for r in replies if r.get("type") != "connected"]
        if bad:
            raise Blocked(core.classify_failure(bad), f"connect: {[core.safe_reply(r) for r in bad]}")
        handles = [r["connection_handle"] for r in replies]
        require(len(set(handles)) == len(handles), "connection handles crossed")
        return handles

    def exchange(self, handle, item, timeout=12.0):
        """One send and the exact echo; records start/end wall time, sizes and digests only."""
        record = {"index": item["index"], "seed": item["seed"], "size": item["size"],
                  "request_sha256": item["request_sha256"], "expected_sha256": item["expected_sha256"],
                  "handle": core.short_handle(handle), "status": "FAIL", "start_wall_ms": now_ms()}
        sent = self.js(f"otp23.request('send', {{connection_handle: {json.dumps(handle)}, "
                       f"record_base64: {json.dumps(core.request_b64(item))}}})")
        self.request_ids.append(sent.get("request_id"))
        if sent.get("type") != "sent":
            record.update(end_wall_ms=now_ms(), problem=f"send reply {core.safe_reply(sent)}")
            return record
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for message in self.pump():
                if message.get("type") == "disconnected":
                    self.unexpected.append({"type": "disconnected", "handle": core.short_handle(
                        message.get("connection_handle"))})
                elif message.get("type") == "message":
                    problem = core.verify_reply(item, handle, message)
                    record.update(end_wall_ms=now_ms(),
                                  received_sha256=core.sha256_hex(base64.b64decode(
                                      message.get("record_base64", ""))))
                    record["duration_ms"] = record["end_wall_ms"] - record["start_wall_ms"]
                    if problem:
                        record["problem"] = problem
                    else:
                        record["status"] = "PASS"
                    return record
            time.sleep(0.02)
        record.update(end_wall_ms=now_ms(), problem=f"no reply within {timeout:.0f} s")
        return record

    def start_scan(self):
        started = self.js(f"otp23.startScan({json.dumps(VELES_SERVICE)})")
        require(started.get("status") == "started", f"scan restart {core.safe_reply(started)}")
        return started["scan_handle"]

    def parsed_trace(self, wait_s=3.0):
        deadline = time.time() + wait_s
        while True:
            text = self.trace_path.read_text(encoding="utf-8", errors="replace") \
                if self.trace_path.exists() else ""
            parsed = core.parse_trace(text)
            if parsed["exit"] is not None or time.time() > deadline:
                return parsed
            time.sleep(0.1)

    def close(self):
        """Close Chrome, then verify and (only if needed, only owned) reap leftover hosts."""
        problems = []
        if self.chrome is not None:
            try:
                self.chrome.close()
            except Exception as error:  # noqa: BLE001 - cleanup must continue
                problems.append(f"chrome close {type(error).__name__}")
        if self.host_pid is not None:
            leftovers = [p for p in [self.host_pid] + self.wrappers
                         if self.adapter.wait_gone([p], 5.0) is None]
            for pid in leftovers:
                self.adapter.kill(pid)
            if leftovers:
                problems.append(f"owned process(es) survived Chrome close: {len(leftovers)} (killed)")
        return problems


def link_pair(session, args):
    """Discover two phones, connect both, and PROVE both links carry bytes (never assumed active)."""
    devices = session.discover(args.discover_s, want=2)
    pair, status, error_class, reason = core.pick_two(devices)
    if pair is None:
        raise Blocked(error_class, reason)
    handles = session.connect(pair)
    proofs = []
    for handle, item in zip(handles, core.plan_exchanges(session.adapter.role, 2, size=4)):
        proofs.append(session.exchange(handle, item))
    require(all(p["status"] == "PASS" for p in proofs), f"link proof failed: {[p.get('problem') for p in proofs]}")
    session.start_scan()  # active scan alongside the two active links
    return handles, [{k: p[k] for k in ("size", "request_sha256", "received_sha256", "status")} for p in proofs]


def verify_gone(session, expected, args, extra=None):
    gone = session.adapter.wait_gone(session.owned_pids(), 10.0)
    parsed = session.parsed_trace()
    problems = core.check_teardown(parsed, expected)
    if gone is None:
        problems.append("owned host or wrapper still alive 10 s after teardown")
    survivors = [p["ProcessId"] for p in session.adapter.owned_new_hosts(session.before, session.chrome.proc.pid)]
    if survivors:
        problems.append(f"{len(survivors)} owned host(s) still listed")
    outs = [f"{s['type']}:{s['status']}" if s["status"] else s["type"] for s in parsed["steps"] if s["dir"] == "out"]
    detail = {"owned_gone_after_s": gone, "trace_mechanism": core.trace_mechanism(parsed), "trace_exit": parsed["exit"],
              "host_output_order": outs[-8:], "problems": problems}
    detail.update(extra or {})
    require(not problems, json.dumps(detail))
    return detail


# ---- chrome-driven lifecycle scenarios --------------------------------------------------------------


def scenario_chrome(adapter, name, args, identity, out_dir):
    title = {"shutdown": "Shutdown with an active scan and two active links",
             "port-disconnect": "Chrome port.disconnect with an active scan and two active links",
             "tab-close": "Connector tab close with an active scan and two active links",
             "failure-isolation": "Disconnect one link; the survivor keeps exchanging; then clean shutdown"}[name]
    expected = {
        "shutdown": "pending request resolved, scan stopped, shutdown_complete is the last host output, stdout closed, "
                    "host and wrapper gone, zero windows/endpoints",
        "port-disconnect": "owned host and wrapper gone with NO shutdown handshake (stdin EOF path), zero windows/endpoints",
        "tab-close": "owned host and wrapper gone with NO shutdown handshake after the owning tab is destroyed",
        "failure-isolation": "stale handle rejected (unknown_handle), survivor exchanges 4096 bytes exactly, "
                             "shutdown then removes the host"}[name]
    session = Session(adapter, out_dir / f"{name}.host-trace.log", identity)
    detail, status, error_class, actual = {}, "FAIL", "assertion", ""
    try:
        detail["ready"] = session.start()
        detail["hygiene_before"] = adapter.hygiene(session.owned_pids())
        hygiene = detail["hygiene_before"]
        if isinstance(hygiene["visible_windows"], int):
            require(hygiene["visible_windows"] == 0, f"host shows windows: {hygiene}")
        if isinstance(hygiene["network_endpoints"], int):
            require(hygiene["network_endpoints"] == 0, f"host owns network endpoints: {hygiene}")
        handles, detail["link_proof"] = link_pair(session, args)
        if name == "shutdown":
            session.js("window.__pending = null; otp23.connectDevice({platform_hint: "
                       + json.dumps(ABSENT_HINT[adapter.role]) + "}).then(r => { window.__pending = r; }); 1")
            reply = session.js("otp23.shutdown()")
            require(reply.get("type") == "shutdown_complete", f"shutdown reply {core.safe_reply(reply)}")
            pending = session.js("window.__pending")
            snapshot = session.js("otp23.snapshot()")
            require(isinstance(pending, dict) and pending.get("type") == "error",
                    f"pending request not terminated: {core.safe_reply(pending)}")
            require(not snapshot["pending"], "connector still has pending requests")
            detail["pending_request_result"] = core.safe_reply(pending)
            detail["port_state"] = snapshot["disconnect"] is not None
            detail["teardown"] = verify_gone(session, "shutdown", args)
        elif name == "port-disconnect":
            session.js("otp23.closePort(); 1")
            detail["teardown"] = verify_gone(session, "disconnect", args)
        elif name == "tab-close":
            session.chrome.cdp.call("Target.closeTarget", {"targetId": session.target})
            detail["teardown"] = verify_gone(session, "disconnect", args)
        else:
            gone = session.js(f"otp23.disconnectDevice({json.dumps(handles[0])})")
            require(gone.get("type") == "disconnected", f"disconnect {core.safe_reply(gone)}")
            stale = session.js(f"otp23.request('send', {{connection_handle: {json.dumps(handles[0])}, "
                               f"record_base64: {json.dumps(core.request_b64(core.plan_exchanges(adapter.role, 1, size=4)[0]))}}})")
            require(stale.get("code") == "unknown_handle", f"stale send {core.safe_reply(stale)}")
            survivor = session.exchange(handles[1], core.plan_exchanges(adapter.role, 1, size=core.SIZE)[0])
            require(survivor["status"] == "PASS", f"survivor {survivor.get('problem')}")
            detail["stale_send"] = core.safe_reply(stale)
            detail["survivor"] = {k: survivor[k] for k in ("size", "request_sha256", "received_sha256", "status")}
            done = session.js("otp23.shutdown()")
            require(done.get("type") == "shutdown_complete", f"shutdown {core.safe_reply(done)}")
            detail["teardown"] = verify_gone(session, "shutdown", args)
        require(not session.unexpected, f"unexpected disconnect events {session.unexpected}")
        status, error_class, actual = "PASS", "none", "all checks held; see detail"
    except Blocked as blocked:
        status, error_class, actual = "BLOCKED", blocked.error_class, blocked.reason
    except AssertionError as failure:
        actual = str(failure)[:900]
    except Exception:  # noqa: BLE001 - recorded, scenario ends
        actual, error_class = traceback.format_exc(limit=3)[-900:], "environment"
    detail["cleanup_problems"] = session.close()
    if detail["cleanup_problems"] and status == "PASS":
        status, error_class, actual = "FAIL", "assertion", f"cleanup problems {detail['cleanup_problems']}"
    return core.record(f"LC-{name}", title, expected, actual, status, error_class, detail=detail)


# ---- direct native-framed stdin scenarios -----------------------------------------------------------


class DirectHost:
    """Host process launched directly (no Chrome) with pipes and the native framing."""

    def __init__(self, argv, env, creationflags=0):
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, creationflags=creationflags)
        self.decoder = core.FrameDecoder()
        self.frames = queue.Queue()
        self.stdout_closed = threading.Event()
        self.eof_state = None
        self.seen = []
        self.counter = 0
        self.api_version = None
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        fd = self.proc.stdout.fileno()
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            for frame in self.decoder.feed(chunk):
                self.frames.put(frame)
        self.eof_state = self.decoder.eof()
        self.stdout_closed.set()

    def request(self, kind, **fields):
        self.counter += 1
        request_id = f"d{self.counter}"
        self.proc.stdin.write(core.request_frame(kind, request_id, self.api_version, **fields))
        self.proc.stdin.flush()
        return request_id

    def next_frame(self, timeout):
        try:
            frame = self.frames.get(timeout=timeout)
        except queue.Empty:
            return None
        self.seen.append(frame)
        return frame

    def reply(self, request_id, timeout=40.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self.next_frame(0.2)
            if frame and frame.get("request_id") == request_id:
                return frame
        return None

    def events_of(self, kind, timeout, count=1):
        found = [f for f in self.seen if f.get("type") == kind and not f.get("request_id")]
        deadline = time.monotonic() + timeout
        while len(found) < count and time.monotonic() < deadline:
            frame = self.next_frame(0.2)
            if frame and frame.get("type") == kind and not frame.get("request_id"):
                found.append(frame)
        return found

    def kill(self):
        """Reap our own child (kill only if still alive) and release the pipes."""
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except OSError:
                pass


def scenario_stdin(adapter, name, args, identity, out_dir, binary=None):
    truncated = name == "stdin-eof-truncated"
    title = ("Stdin closed inside a frame header (direct native-framed process)" if truncated else
             "Stdin EOF with an active scan and two active links (direct native-framed process)")
    expected = ("host rejects the truncated frame (exit 2), logs the framing stage, stdout ends on a frame boundary, "
                "no shutdown credit" if truncated else
                "stdin closed (NOT shutdown): exit 0, stdout reaches EOF on a frame boundary, process gone, "
                "trace shows the stdin-closed path with no shutdown_complete")
    trace = out_dir / f"{name}.host-trace.log"
    trace.unlink(missing_ok=True)
    provenance = {"launch": "direct process with pipes", "chrome_provenance": False,
                  "context": adapter.tcc_note() or "direct launch; Chrome provenance is covered by the Chrome scenarios"}
    detail, status, error_class, actual, host = {"provenance": provenance}, "FAIL", "assertion", "", None
    try:
        binary = binary or adapter.host_binary()
        detail["host_binary_sha256"] = core.file_sha256(binary)
        env = dict(os.environ, VELES_OTP23_TRACE=str(trace))
        host = DirectHost([binary, identity["origin"]], env, adapter.direct_spawn_flags)
        detail["host_pid_recorded"] = True
        if truncated:
            host.proc.stdin.write(b"\x10\x00")  # two of the four header bytes, then EOF
            host.proc.stdin.close()
        else:
            ready = host.reply(host.request("hello"), 20)
            require(ready and ready.get("type") == "ready", f"hello {core.safe_reply(ready)}")
            host.api_version = ready.get("api_version")
            started = host.reply(host.request("start_scan", service_uuid=VELES_SERVICE), 20)
            if not started or started.get("status") != "started":
                raise Blocked(core.classify_failure(started), f"startScan {core.safe_reply(started)}")
            found = host.events_of("device_found", args.discover_s, count=2)
            devices = {f["device_handle"]: {"label": f.get("label"), "device_handle": f["device_handle"]} for f in found}
            pair, _, error_class, reason = core.pick_two(list(devices.values()))
            if pair is None:
                raise Blocked(error_class, reason)
            host.reply(host.request("stop_scan", scan_handle=started["scan_handle"]), 20)
            ids = [host.request("connect", device_handle=d["device_handle"]) for d in pair]
            connected = [host.reply(i, 70) for i in ids]
            bad = [c for c in connected if not c or c.get("type") != "connected"]
            if bad:
                raise Blocked(core.classify_failure(bad), f"connect {[core.safe_reply(b) for b in bad]}")
            handles = [c["connection_handle"] for c in connected]
            proofs = []
            for handle, item in zip(handles, core.plan_exchanges(adapter.role, 2, size=4)):
                sent = host.reply(host.request("send", connection_handle=handle, record_base64=core.request_b64(item)), 20)
                require(sent and sent.get("type") == "sent", f"send {core.safe_reply(sent)}")
                got = host.events_of("message", 12, count=len(proofs) + 1)
                problem = core.verify_reply(item, handle, got[-1] if got else None)
                require(problem is None, f"link proof: {problem}")
                proofs.append({"size": item["size"], "request_sha256": item["request_sha256"]})
            rescan = host.reply(host.request("start_scan", service_uuid=VELES_SERVICE), 20)
            require(rescan and rescan.get("status") == "started", f"scan restart {core.safe_reply(rescan)}")
            detail["link_proof"] = proofs
            host.proc.stdin.close()  # EOF, deliberately NOT a shutdown request
        try:
            code = host.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            raise AssertionError("host still running 10 s after stdin EOF")
        require(host.stdout_closed.wait(5), "stdout never reached EOF")
        deadline, parsed = time.time() + 3, None
        while True:
            text = trace.read_text(encoding="utf-8", errors="replace") if trace.exists() else ""
            parsed = core.parse_trace(text)
            if parsed["exit"] or time.time() > deadline:
                break
            time.sleep(0.1)
        problems = core.check_truncated_eof(parsed, code, host.eof_state) if truncated else \
            core.check_teardown(parsed, "stdin_closed", require_drained=True) + \
            ([] if code == 0 else [f"process exit code {code}"]) + \
            ([] if host.eof_state == "clean" else [f"stdout ended {host.eof_state}"])
        detail.update(exit_code=code, stdout_eof_state=host.eof_state, trace_exit=parsed["exit"],
                      trace_mechanism=core.trace_mechanism(parsed),
                      frames_after_close=sorted({f.get("type") for f in host.seen} - {None}), problems=problems)
        require(not problems, json.dumps(detail))
        status, error_class, actual = "PASS", "none", "all checks held; see detail"
    except Blocked as blocked:
        status, error_class, actual = "BLOCKED", blocked.error_class, blocked.reason
    except AssertionError as failure:
        actual = str(failure)[:900]
    except Exception:  # noqa: BLE001
        actual, error_class = traceback.format_exc(limit=3)[-900:], "environment"
    finally:
        if host is not None:
            host.kill()  # only our own Popen child, and only if it is somehow still alive
    return core.record(f"LC-{name}", title, expected, actual, status, error_class, detail=detail)


def scenario_adapter_toggle():
    return core.record("LC-adapter-toggle", "Bluetooth adapter toggled off/on during active links",
                       "adapter toggled by the operator, links classified, host recovers or exits cleanly",
                       "not performed: toggling the adapter needs an explicit user action and this driver never does it",
                       "BLOCKED", "requires_operator")


# ---- meta, lifecycle entry ----------------------------------------------------------------------------


def meta_for(adapter, identity):
    info = {"platform": adapter.platform, "python": sys.version.split()[0], "os": platform.platform(),
            "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_revision": core.source_revision(), "source_sha256": core.source_hashes(),
            "extension_id": identity["extension_id"], "foreground_only": True,
            "no_background_exemption_measured": True}
    try:
        binary = adapter.host_binary()
        info["host_binary_sha256"] = core.file_sha256(binary)
    except Exception as error:  # noqa: BLE001
        info["host_binary_sha256"] = None
        info["host_binary_unavailable"] = type(error).__name__
    info["pre_existing_host_pids_untouched"] = sorted(p["ProcessId"] for p in adapter.host_processes())
    return info


def run_lifecycle(args, adapter=None, identity=None):
    adapter = adapter or make_adapter()
    if identity is None:
        from macos_install import read_identity
        identity = read_identity()
    out_dir = pathlib.Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    names = list(LIFECYCLE) if args.scenario == "all" else [args.scenario]
    meta, results = meta_for(adapter, identity), []
    for name in names:
        if name == "adapter-toggle":
            result = scenario_adapter_toggle()
        elif name.startswith("stdin-eof"):
            result = scenario_stdin(adapter, name, args, identity, out_dir, binary=args.host_binary)
        else:
            result = scenario_chrome(adapter, name, args, identity, out_dir)
        results.append(result)
        print(f"[{result['status']:7}] {result['id']} {result['error_class']}")
        (out_dir / f"{name}.json").write_text(scrub(json.dumps(result, indent=2)), encoding="utf-8")
    meta["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    survivors = [p["ProcessId"] for p in adapter.host_processes()
                 if p["ProcessId"] not in meta["pre_existing_host_pids_untouched"]]
    summary = {"meta": meta, "results": results, "surviving_owned_hosts_after_run": survivors}
    (out_dir / "lifecycle.json").write_text(scrub(json.dumps(summary, indent=2)), encoding="utf-8")
    ok = all(r["status"] == "PASS" for r in results) and not survivors
    print(f"lifecycle: {'PASS' if ok else 'NOT PASS'} ({len(results)} scenario(s)) -> {out_dir}")
    return 0 if ok else 1


# ---- M6: one central, ONE explicitly selected phone, shared barrier --------------------------------------


def wait_for_barrier(args, clock=time.time, sleep=time.sleep):
    """Block until the parent's release (file appears or epoch reached); returns release wall ms."""
    deadline = clock() + args.barrier_timeout_s
    if args.barrier_file:
        path = pathlib.Path(args.barrier_file)
        while not path.exists():
            if clock() > deadline:
                raise Blocked("timeout", "barrier file never appeared")
            sleep(0.01)
    else:
        if args.start_at_epoch - clock() > args.barrier_timeout_s:
            raise Blocked("timeout", "start epoch is beyond the barrier timeout")
        while clock() < args.start_at_epoch:
            sleep(min(0.01, max(0.0, args.start_at_epoch - clock())))
    return int(clock() * 1000)


def run_m6(args, adapter=None, identity=None, session_factory=Session):
    adapter = adapter or make_adapter()
    if args.role != adapter.role:
        raise SystemExit(f"--role {args.role} does not match this platform ({adapter.role})")
    if identity is None:
        from macos_install import read_identity
        identity = read_identity()
    out = pathlib.Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    stage_path = pathlib.Path(args.stage_file) if args.stage_file else out.with_suffix(".stage.json")
    plan = core.plan_exchanges(args.role, args.exchanges, args.seed_base)
    result = {"scenario": "m6-multi-central", "role": args.role, "pair_run_id": args.pair_run_id,
              "phone_label": args.phone_label, "links_on_this_central": 0, "exchanges": [],
              "system_errors": [], "unexpected_disconnects": [], "status": "FAIL", "error_class": "assertion",
              "expected": f"one central, one explicitly selected phone, {args.exchanges} exact 4096-byte echoes after "
                          "the shared barrier; needs the peer central and m6-adjudicate",
              "actual": "", "meta": meta_for(adapter, identity), "seeds": [p["seed"] for p in plan]}
    session = session_factory(adapter, out.with_suffix(".host-trace.log"), identity)
    try:
        session.start()
        devices = session.discover(args.discover_s, want=None)
        result["discovered_phones"] = len(devices)
        index, status, error_class, reason = core.select_target(devices, args.target_label, args.sole_advertiser)
        if index is None:
            raise Blocked(error_class, reason)
        result["target_label_sha256"] = core.label_hash(devices[index].get("label"))
        handles = session.connect([devices[index]])
        result["links_on_this_central"] = len(handles)
        handle = handles[0]
        stage = core.stage_document(args.pair_run_id, args.role, args.phone_label, result["seeds"],
                                    result["target_label_sha256"], now_ms())
        stage_path.write_text(json.dumps(stage, indent=2), encoding="utf-8")
        print(f"READY {stage['plan_sha256']}", flush=True)
        result["released_at_wall_ms"] = wait_for_barrier(args)
        index_i = 0
        while index_i < len(plan) or (args.run_until_epoch and time.time() < args.run_until_epoch):
            item = plan[index_i] if index_i < len(plan) else core.plan_exchanges(args.role, index_i + 1, args.seed_base)[index_i]
            done = session.exchange(handle, item)
            result["exchanges"].append(done)
            if done["status"] != "PASS":
                result["system_errors"].append({"index": index_i, "problem": done.get("problem")})
                break
            index_i += 1
        if args.disconnect_at_epoch and not result["system_errors"]:
            while time.time() < args.disconnect_at_epoch:
                time.sleep(0.01)
            at = now_ms()
            reply = session.js(f"otp23.disconnectDevice({json.dumps(handle)})")
            result["deliberate_disconnect"] = {"at_wall_ms": at, "reply": core.safe_reply(reply)}
        result["unexpected_disconnects"] = list(session.unexpected)
        ids = [e for e in session.request_ids if e]
        if len(set(ids)) != len(ids):
            result["system_errors"].append({"problem": "request ids reused"})
        done = session.js("otp23.shutdown()")
        require(done.get("type") == "shutdown_complete", f"shutdown {core.safe_reply(done)}")
        result["teardown"] = verify_gone(session, "shutdown", args)
        clean = (not result["system_errors"] and not result["unexpected_disconnects"]
                 and len(result["exchanges"]) >= args.exchanges
                 and all(e["status"] == "PASS" for e in result["exchanges"]))
        require(clean, "exchanges or isolation failed; see exchanges/system_errors")
        result.update(status="PARTIAL", error_class="none",
                      actual="own central clean (exact replies, no system errors); M6 needs the peer central and the "
                             "adjudicator; Android notify return codes are not observable")
    except Blocked as blocked:
        result.update(status="BLOCKED", error_class=blocked.error_class, actual=blocked.reason)
    except AssertionError as failure:
        result["actual"] = str(failure)[:900]
    except Exception:  # noqa: BLE001
        result.update(error_class="environment", actual=traceback.format_exc(limit=3)[-900:])
    result["cleanup_problems"] = session.close()
    if result["cleanup_problems"] and result["status"] == "PARTIAL":
        result.update(status="FAIL", error_class="assertion", actual=f"cleanup problems {result['cleanup_problems']}")
    result["finished_wall_ms"] = now_ms()
    out.write_text(scrub(json.dumps(result, indent=2)), encoding="utf-8")
    print(f"m6 {args.role}: {result['status']} ({result['error_class']}) -> {out}")
    return 0 if result["status"] == "PARTIAL" else 1


def run_adjudicate(args):
    first = json.loads(pathlib.Path(args.windows).read_text(encoding="utf-8"))
    second = json.loads(pathlib.Path(args.mac).read_text(encoding="utf-8"))
    verdict = core.adjudicate_m6(first, second, min_exchanges=args.min_exchanges, min_overlap_ms=args.min_overlap_ms,
                                 min_overlapping_pairs=args.min_overlapping_pairs, clock_offset_ms=args.clock_offset_ms)
    pathlib.Path(args.out).write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    print(f"m6 adjudication: {verdict['verdict']} -> {args.out}")
    return 0 if verdict["verdict"] in ("PASS", "PARTIAL") else 1


# ---- CLI ------------------------------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    life = sub.add_parser("lifecycle")
    life.add_argument("--scenario", choices=list(LIFECYCLE) + ["adapter-toggle", "all"], default="all")
    life.add_argument("--out-dir", required=True)
    life.add_argument("--discover-s", type=float, default=30.0)
    life.add_argument("--host-binary", help="override the registered host binary for the direct stdin cases")
    m6 = sub.add_parser("m6")
    m6.add_argument("--role", choices=["windows", "mac"], required=True)
    m6.add_argument("--pair-run-id", required=True)
    m6.add_argument("--phone-label", required=True, help="parent-provided physical model label, no addresses")
    m6.add_argument("--target-label", help="advertised label of THE shared phone (hashed in results)")
    m6.add_argument("--sole-advertiser", action="store_true", help="exactly one phone advertises; refuse otherwise")
    m6.add_argument("--barrier-file")
    m6.add_argument("--start-at-epoch", type=float)
    m6.add_argument("--barrier-timeout-s", type=float, default=900.0)
    m6.add_argument("--exchanges", type=int, default=core.MIN_EXCHANGES)
    m6.add_argument("--seed-base", type=int, default=None)
    m6.add_argument("--run-until-epoch", type=float, help="keep exchanging beyond --exchanges until this time")
    m6.add_argument("--disconnect-at-epoch", type=float, help="after the exchanges, disconnect this link at this time")
    m6.add_argument("--discover-s", type=float, default=15.0)
    m6.add_argument("--stage-file")
    m6.add_argument("--out", required=True)
    adj = sub.add_parser("m6-adjudicate")
    adj.add_argument("--windows", required=True)
    adj.add_argument("--mac", required=True)
    adj.add_argument("--out", required=True)
    adj.add_argument("--min-exchanges", type=int, default=core.MIN_EXCHANGES)
    adj.add_argument("--min-overlap-ms", type=int, default=1000)
    adj.add_argument("--min-overlapping-pairs", type=int, default=3)
    adj.add_argument("--clock-offset-ms", type=int, default=0, help="windows wall clock minus mac wall clock")
    return parser


def validate_m6_args(parser, args):
    if args.exchanges < core.MIN_EXCHANGES:
        parser.error(f"--exchanges must be at least {core.MIN_EXCHANGES}")
    if bool(args.barrier_file) == (args.start_at_epoch is not None):
        parser.error("exactly one of --barrier-file / --start-at-epoch is required")
    if bool(args.target_label) == bool(args.sole_advertiser):
        parser.error("exactly one of --target-label / --sole-advertiser is required (the phone is never guessed)")
    if args.seed_base is not None and not 0 <= args.seed_base <= 255:
        parser.error("--seed-base must be 0..255")


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "m6":
        validate_m6_args(parser, args)
        return run_m6(args)
    if args.command == "m6-adjudicate":
        return run_adjudicate(args)
    return run_lifecycle(args)


if __name__ == "__main__":
    sys.exit(main())
