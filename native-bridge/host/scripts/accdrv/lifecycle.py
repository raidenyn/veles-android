"""Case runner: Chrome-owned lifecycle triggers and direct-launch diagnostics against the PRODUCTION host.

Design rules (each has a test):
* Fail fast: the first Blocked/ProductFailure stops the case (a trigger fired without proven preconditions
  would be meaningless); cleanup (Chrome, profile, registration) ALWAYS runs afterwards.
* Phone / adapter-condition / OS link-release steps go through an injected `rig`. The default rig raises
  Blocked for every call: this module contains no phone-bridge or radio control.
* The Chrome-owned stdout pipe cannot be tapped by the connector once the port or tab is gone, so
  observe_last_json / observe_stdout_close are Blocked unless an independent `pipe_tap` is injected - except
  after an explicit `shutdown`, where the connector itself receives shutdown_complete and then the disconnect.
* Hygiene observations are recorded one by one; an unmeasurable one stays absent (and blocks the case).
"""
import hashlib
import json
import struct
import subprocess
import threading
import time

from .errors import Blocked, Refused
from .recorder import ProductFailure
from .registration import RestoreFailed

SERVICE_UUID = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51"
EXIT_WAIT_S = 10.0  # the host's own bound is 5 s (README); double it so only a genuine hang fails


class UnattachedRig:
    """Default rig: nothing is attached, so every phone/condition/link measurement is Blocked."""

    def __getattr__(self, name):
        if name.startswith("_") or name in ("cleanup", "before_trigger"):
            raise AttributeError(name)

        def blocked(ctx, st):
            raise Blocked(f"{name}: no phone/adapter rig attached (the controller owns the hardware)")
        return blocked


class Ctx:
    def __init__(self, case, rec, chrome=None, observer=None, host_exe=None, host_name=None, ext_dir=None, ext_id=None,
                 origin=None, rig=None, pipe_tap=None, clock=time.time, sleep=time.sleep, popen=subprocess.Popen, env=None, guard=None):
        self.guard = guard  # callable(where): raises Blocked unless the host executable still has the anchored bytes
        self.case, self.rec, self.chrome, self.observer = case, rec, chrome, observer
        self.host_exe, self.host_name, self.ext_dir, self.ext_id, self.origin = host_exe, host_name, ext_dir, ext_id, origin
        self.rig = rig or UnattachedRig()
        self.pipe_tap, self.clock, self.sleep, self.popen, self.env = pipe_tap, clock, sleep, popen, env or {}

    def js(self, st, expression, timeout=60):
        if self.guard is not None and "velesAcceptance.connect(" in expression:
            self.guard("immediately before connect_native/reconnect")  # Chrome execs the host on this call, every time
        return self.chrome.js(st["session"], expression, timeout)


def _need(st, key, why):
    if key not in st:
        raise Blocked(f"{why} (earlier step did not complete)")
    return st[key]


# ---- Chrome-owned steps ----------------------------------------------------------------------------------------------
def launch_chrome_disposable(ctx, st):
    before = ctx.observer.host_processes(ctx.host_exe)
    if before:
        raise Refused(f"{len(before)} production host process(es) already running; a baseline must be empty")
    ctx.rec.capture("baseline-processes", "process", {"host_processes_before_launch": before})
    ctx.rec.control("baseline_observation_before_launch", "pass", "baseline-processes")
    st["before"] = before
    version = ctx.chrome.start()
    prov = ctx.chrome.provenance()
    prov.update(channel=(version.get("product") or "").split("/")[0] or None, extension_id=ctx.ext_id,
                extension_origin=ctx.origin)
    ctx.rec.capture("chrome-launch", "chrome_cdp", {"version": version, "provenance": prov})
    prov["provenance"] = {"source": "Browser.getVersion over DevTools", "recorded_at": ctx.rec.clock(), "capture_ref": "chrome-launch"}
    ctx.rec.fields["chrome"] = prov


def load_extension(ctx, st):
    result = ctx.chrome.load_extension(ctx.ext_dir)
    if result.get("id") != ctx.ext_id:
        raise Blocked(f"Chrome loaded the extension as {result.get('id')!r}, not the keyed id {ctx.ext_id!r}")
    ctx.rec.capture("extension-load", "chrome_cdp", result)


def connect_native(ctx, st):
    target, session = ctx.chrome.open_page(f"chrome-extension://{ctx.ext_id}/connector.html")
    st.update(target=target, session=session)
    ctx.js(st, f"velesAcceptance.connect({json.dumps(ctx.host_name)})")


def negotiate_ready(ctx, st):
    reply = ctx.js(st, "velesAcceptance.hello()")
    ctx.rec.capture("hello-reply", "host_events", reply)
    if not isinstance(reply, dict) or reply.get("type") != "ready":
        raise ProductFailure(f"hello did not yield ready: type={reply.get('type') if isinstance(reply, dict) else None!r} "
                             f"code={reply.get('code') if isinstance(reply, dict) else None!r} "
                             f"stage={reply.get('stage') if isinstance(reply, dict) else None!r}")
    deadline = ctx.clock() + 10
    new = []
    while ctx.clock() < deadline:
        new = [r for r in ctx.observer.host_processes(ctx.host_exe) if r not in st["before"]]
        if new:
            break
        ctx.sleep(0.2)
    if len(new) != 1:
        raise Blocked(f"expected exactly one new production host process after ready, saw {len(new)}")
    st["host_pid"] = new[0]["pid"]
    st["host_row"] = new[0]
    ctx.rec.capture("host-process", "process", {"host": new[0], "ready": {k: reply.get(k) for k in ("platform", "bridge_version", "api_version")}})


def start_scan(ctx, st):
    reply = ctx.js(st, f"velesAcceptance.startScan({json.dumps(SERVICE_UUID)})")
    ctx.rec.capture("start-scan-reply", "host_events", reply)
    if reply.get("type") != "scan_state" or reply.get("status") != "started":
        raise ProductFailure(f"start_scan: {reply.get('type')!r} code={reply.get('code')!r} stage={reply.get('stage')!r}")
    st["scan_handle"] = reply.get("scan_handle")


def observe_hygiene(ctx, st):
    pid = _need(st, "host_pid", "no host process to observe")
    blocked = []
    wanted = set(ctx.case["observations"])

    def measure(name, fn):
        if name not in wanted:
            return
        try:
            seen = fn()
        except Blocked as e:
            blocked.append(f"{name}: {e}")
            return
        ref = ctx.rec.capture(f"hygiene-{name}", "hygiene", {"host_pid": pid, "observed": seen})
        ctx.rec.observation(name, "none_observed" if not seen else "observed", ref)

    def windows():
        census = ctx.observer.windows(pid)
        return [w for k in ("top_visible", "top_hidden", "message_only") for w in census.get(k, [])]

    measure("no_top_level_window", windows)
    measure("no_network_endpoint", lambda: ctx.observer.endpoints(st.get("host_row") or pid))
    for name, what in (("no_filesystem_write_outside_test_dirs", "file writes"),
                       ("no_registry_write_outside_test_manifest", "registry writes"),
                       ("no_launchservices_registration_change", "LaunchServices registration")):
        measure(name, lambda what=what: ctx.observer.no_write_monitor(what))
    if blocked:
        raise Blocked("; ".join(blocked))


def _trigger(ctx, st, how, fn):
    st["host_pid"] = _need(st, "host_pid", "no host process")
    st["trigger"] = how
    hook = getattr(ctx.rig, "before_trigger", None)
    if hook is not None:  # lets the rig arm an in-flight request and prove it pending immediately before the trigger fires
        hook(ctx, st, how)
    st["trigger_wall"] = ctx.rec.clock()
    out = fn()
    ctx.rec.capture(f"trigger-{how}", "chrome_cdp", {"trigger": how, "at": st["trigger_wall"], "result": out})


def literal_port_disconnect(ctx, st):
    _trigger(ctx, st, "literal_port_disconnect", lambda: ctx.js(st, "velesAcceptance.closePort()"))


def close_connector_tab(ctx, st):
    _trigger(ctx, st, "close_connector_tab", lambda: ctx.chrome.close_tab(_need(st, "target", "no connector tab")))


def chrome_owned_pipe_closure(ctx, st):
    _trigger(ctx, st, "chrome_owned_pipe_closure", lambda: ctx.chrome.browser_close())


def shutdown_request(ctx, st):
    def go():
        reply = ctx.js(st, "velesAcceptance.shutdown()")
        st["shutdown_reply"] = reply
        return reply
    _trigger(ctx, st, "shutdown_request", go)
    if st["shutdown_reply"].get("type") != "shutdown_complete":
        raise ProductFailure(f"shutdown answered {st['shutdown_reply'].get('type')!r}, not shutdown_complete")


def observe_last_json(ctx, st):
    if st.get("trigger") == "shutdown_request":
        ctx.rec.capture("last-json", "host_events", st["shutdown_reply"])
    elif ctx.pipe_tap is not None:
        ctx.rec.capture("last-json", "host_events", ctx.pipe_tap.last_json())
    else:
        raise Blocked("the connector is detached once the port/tab/Chrome is gone and Chrome owns the stdout pipe: "
                      "last JSON needs an independent pipe tap, which is not available")


def observe_stdout_close(ctx, st):
    if st.get("trigger") == "shutdown_request":
        deadline = ctx.clock() + EXIT_WAIT_S
        snap = None
        while ctx.clock() < deadline:
            snap = ctx.js(st, "velesAcceptance.snapshot()")
            if snap.get("disconnect"):
                ctx.rec.capture("stdout-close", "host_events", snap)
                return
            ctx.sleep(0.1)
        raise ProductFailure("no port disconnect (stdout close) after shutdown_complete")
    if ctx.pipe_tap is not None:
        ctx.rec.capture("stdout-close", "host_events", ctx.pipe_tap.stdout_closed())
    else:
        raise Blocked("stdout close is not observable under Chrome-owned launching without an independent pipe tap")


def observe_process_exit(ctx, st):
    pid = _need(st, "host_pid", "no host process")
    start = ctx.clock()
    deadline = start + EXIT_WAIT_S
    while ctx.clock() < deadline:
        if not ctx.observer.alive(st.get("host_row") or pid):
            ctx.rec.capture("process-exit", "process", {"host_pid": pid, "gone_after_s": round(ctx.clock() - start, 3),
                                                        "trigger": st.get("trigger"), "exit_code": "not observable (Chrome-owned)"})
            return
        ctx.sleep(0.1)
    ctx.rec.capture("process-exit", "process", {"host_pid": pid, "survived_s": EXIT_WAIT_S, "trigger": st.get("trigger")})
    raise ProductFailure(f"host pid {pid} survived {EXIT_WAIT_S}s after {st.get('trigger')}")


# ---- direct-launch diagnostics (NOT Chrome-owned) ----------------------------------------------------------------------
def frame(obj):
    data = json.dumps(obj, separators=(",", ":")).encode()
    return struct.pack("<I", len(data)) + data


class DirectHost:
    """The production host started directly with the allowed origin as its only argument, over real pipes."""

    def __init__(self, popen, exe, origin, env, cwd):
        self.proc = popen([str(exe), origin], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd)
        self.out, self.err, self.eof = bytearray(), bytearray(), threading.Event()
        threading.Thread(target=self._drain, args=(self.proc.stdout, self.out, self.eof), daemon=True).start()
        threading.Thread(target=self._drain, args=(self.proc.stderr, self.err, None), daemon=True).start()

    @staticmethod
    def _drain(stream, sink, done):
        try:
            while True:
                chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
                if not chunk:
                    break
                sink += chunk
        finally:
            if done:
                done.set()

    def send(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def frames(self):
        buf, out = bytes(self.out), []
        while len(buf) >= 4:
            (n,) = struct.unpack("<I", buf[:4])
            if len(buf) < 4 + n:
                break
            out.append(json.loads(buf[4:4 + n]))
            buf = buf[4 + n:]
        return out, len(buf)

    def wait_frame(self, clock, sleep, timeout=15):
        deadline = clock() + timeout
        while clock() < deadline:
            fr, _ = self.frames()
            if fr:
                return fr[0]
            sleep(0.05)
        return None

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


def direct_launch(ctx, st):
    ctx.rec.control("not_chrome_owned_lifecycle", "pass", ctx.rec.capture("direct-launch", "process", {
        "launch": "direct", "argv": ["<exe>", ctx.origin], "chrome": False}))
    st["direct"] = DirectHost(ctx.popen, ctx.host_exe, ctx.origin, ctx.env, ctx.rec.out)


def direct_negotiate_ready(ctx, st):
    d = _need(st, "direct", "no direct host")
    d.send(frame({"envelope_version": 1, "type": "hello", "request_id": "d-hello", "api_versions": [1]}))
    reply = d.wait_frame(ctx.clock, ctx.sleep)
    ctx.rec.capture("hello-reply", "host_events", reply if reply is not None else {"no_reply": True})
    if not reply or reply.get("type") != "ready":
        raise ProductFailure(f"direct hello did not yield ready: {reply and reply.get('type')!r}")


def close_stdin_clean(ctx, st):
    st["direct"].proc.stdin.close()


def send_truncated_frame_then_eof(ctx, st):
    d = st["direct"]
    d.send(struct.pack("<I", 100) + b'{"envelope_version":1')  # declares 100 bytes, delivers 21, then EOF
    d.proc.stdin.close()


def direct_observe_last_json(ctx, st):
    d = st["direct"]
    d.eof.wait(EXIT_WAIT_S)
    frames, leftover = d.frames()
    ctx.rec.capture("last-json", "host_events", {"frames": frames, "trailing_partial_bytes": leftover})


def direct_observe_stdout_close(ctx, st):
    if not st["direct"].eof.wait(EXIT_WAIT_S):
        raise ProductFailure("stdout did not close after stdin EOF")
    ctx.rec.capture("stdout-close", "host_events", {"stdout_eof": True, "stderr_bytes": len(st["direct"].err)})


def direct_observe_process_exit(ctx, st):
    d = st["direct"]
    try:
        code = d.proc.wait(timeout=EXIT_WAIT_S)
    except subprocess.TimeoutExpired:
        raise ProductFailure("host did not exit after stdin EOF")
    ctx.rec.fields["process"] = {"exit_code": code}
    ctx.rec.capture("process-exit", "process", {"exit_code": code, "stderr": bytes(d.err[:512]).decode("ascii", "replace")})


CHROME = {"launch_chrome_disposable": launch_chrome_disposable, "load_extension": load_extension, "connect_native": connect_native,
          "negotiate_ready": negotiate_ready, "start_scan": start_scan, "observe_hygiene": observe_hygiene,
          "literal_port_disconnect": literal_port_disconnect, "close_connector_tab": close_connector_tab,
          "chrome_owned_pipe_closure": chrome_owned_pipe_closure, "shutdown_request": shutdown_request,
          "observe_last_json": observe_last_json, "observe_stdout_close": observe_stdout_close,
          "observe_process_exit": observe_process_exit}
DIRECT = {"direct_launch": direct_launch, "negotiate_ready": direct_negotiate_ready, "close_stdin_clean": close_stdin_clean,
          "send_truncated_frame_then_eof": send_truncated_frame_then_eof, "observe_last_json": direct_observe_last_json,
          "observe_stdout_close": direct_observe_stdout_close, "observe_process_exit": direct_observe_process_exit}


def handler_for(ctx, name):
    table = DIRECT if ctx.case["kind"] == "direct" else CHROME
    if name in table:
        return table[name]
    if ctx.case["kind"] == "direct":
        return lambda c, s: (_ for _ in ()).throw(Blocked(f"no direct handler for {name}"))
    return getattr(ctx.rig, name)  # phone/condition/link steps: rig or Blocked


def run_case(ctx, registration=None, post_checks=()):
    """Run every required action in order; stop at the first Blocked/ProductFailure/driver error; ALWAYS clean up
    and ALWAYS finalize a record. An unexpected exception is recorded as a driver error (BLOCKED, never FAIL)."""
    st = {}
    rec = ctx.rec
    interrupted = None
    try:
        if registration is not None:
            try:
                rec.capture("registration-setup", "registration", registration.register())
                rec.capture("test-manifest", "test_manifest", registration.manifest_bytes)
                rec.fields["test_manifest"] = {
                    "path": "captures/test-manifest", "sha256": hashlib.sha256(registration.manifest_bytes).hexdigest(),
                    "host_sha256": rec.fields.get("binary", {}).get("sha256"), "allowed_origin": ctx.origin,
                    "scope": "test_only_not_production_registration", "host_name": ctx.host_name}
            except (Refused, Blocked) as e:
                rec.block(f"registration refused: {e}")
                # Cleanup is decided by what was actually mutated (journal written), NOT by the exception type.
                if not getattr(registration, "registered", False):
                    registration = None
                st["skip_actions"] = True
        if not st.get("skip_actions"):
            for name in ctx.case["required_actions"]:
                fn = handler_for(ctx, name)
                try:
                    rec.run_action(name, lambda fn=fn: fn(ctx, st))
                except (Blocked, Refused, ProductFailure):
                    break  # recorded by run_action; later steps would rest on a missing precondition
                except Exception:
                    break  # recorded as a driver error by run_action: BLOCKED, record still written
    except BaseException as e:  # noqa: BLE001 - e.g. KeyboardInterrupt: finalize first, then re-raise
        interrupted = e
        rec.driver_error("run_case", e)
    finally:
        cleanup_interrupt = _cleanup(ctx, st, registration)
    post_interrupt = None
    for check in post_checks:
        try:
            check(rec)
        except Exception as e:  # noqa: BLE001
            rec.driver_error("post_check", e)
        except BaseException as e:  # noqa: BLE001 - finalize first, re-raise afterwards
            rec.driver_error("post_check", e)
            post_interrupt = post_interrupt or e
    doc = rec.finalize()
    if interrupted is not None:
        raise interrupted
    if cleanup_interrupt is not None:
        raise cleanup_interrupt  # the record is already finalized
    if post_interrupt is not None:
        raise post_interrupt
    return doc


def _stage(rec, label, fn, deferred, on_error=None):
    """Run one cleanup stage. An ordinary failure is recorded as a blocker; ANY BaseException (KeyboardInterrupt/SystemExit) is
    recorded, remembered in `deferred`, and swallowed for now so that every later stage still runs. It is re-raised by run_case
    only after the record is finalized."""
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - reported, never hidden
        rec.block(on_error(e) if on_error else f"cleanup: {label} failed: {type(e).__name__}")
    except BaseException as e:  # noqa: BLE001
        rec.block(f"cleanup: {label} interrupted ({type(e).__name__}); remaining cleanup still runs")
        deferred.append(e)


def _cleanup(ctx, st, registration):
    """Every stage is attempted whatever happened in the earlier ones, in this order: rig, direct host, Chrome/profile, registration.
    Returns the first deferred interrupt (or None)."""
    rec = ctx.rec
    deferred = []
    cleanup = getattr(ctx.rig, "cleanup", None)
    if cleanup is not None:
        _stage(rec, "rig cleanup", lambda: cleanup(ctx, st), deferred)
    if "direct" in st:
        _stage(rec, "direct host close", lambda: st["direct"].close(), deferred)
    if ctx.chrome is not None:
        _stage(rec, "Chrome/profile disposal", lambda: ctx.chrome.dispose(), deferred)
    if registration is not None and getattr(registration, "registered", True):
        hint = getattr(registration, "recovery_hint", "")

        def restore():
            rec.capture("registration-restore", "registration", registration.unregister())

        def not_restored(e):
            return f"cleanup: REGISTRATION NOT RESTORED{' - ' + hint if hint else ''}: {type(e).__name__}: {e}"
        _stage(rec, "registration restore", restore, deferred, on_error=not_restored)
    return deferred[0] if deferred else None
