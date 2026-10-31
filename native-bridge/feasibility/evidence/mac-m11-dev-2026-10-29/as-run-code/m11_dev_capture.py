"""OTP-23 M-11 dev capture (PRIVATE stage script; parent executes ONLY after independent review).

Measures the M-11 canonical criterion on the existing admin account WITHOUT touching the approved
signed fixture or its Bluetooth grant: launch the UNSIGNED dev counterpart
(VelesOtp23Dev.app, bundle id me.nagaev.veles.bridge.dev, automatic linker ad-hoc signature
recorded truthfully) from Chrome on a DISPOSABLE profile with a DISTINCT Native Messaging name
(me.nagaev.veles.otp23.dev); record the actual Bluetooth authorization status, the scan result, and
— if and only if the OS shows a permission dialog — a FRESH-NONCE human step: the owner reads the
EXACT name the dialog shows (before answering) and chooses Allow or Deny THEMSELVES. This script
never clicks, never auto-answers, never resets or touches TCC, never writes outside its own
--out-dir, the disposable profile and the DISTINCT user-scope dev manifest, and never modifies any
shared repo file. M-11 runs ONLY after the three fresh signed M-08 retake scenarios have passed
(their live authorized scans are the real baseline; codesign identity alone is NOT grant proof).

A DENIED outcome is a valid classified observation (recorded as such), never a fabricated unsigned
scan PASS; the recorded result goes to the reviewer for actual adjudication.

Usage (from the M-11 stage dir, on the Mac, after ./package-dev.sh):
    python3 m11_dev_capture.py --out-dir capture/<unique-attempt-name> --user-scope-manifest
Registration (reviewer-required, F-M11-1 — the ONLY relied-upon layer):
  * USER-SCOPE via --user-scope-manifest is now REQUIRED; the script REJECTS profile-only runs
    before any launch. Chrome discovers native-messaging manifests on macOS ONLY in the system
    (/Library/Google/Chrome/NativeMessagingHosts) and the user
    (~/Library/Application Support/Google/Chrome/NativeMessagingHosts) locations — there is no
    --user-data-dir manifest location, and Chrome profile-local discovery was never measured
    working on this project (every working M-08 macOS run had the USER-SCOPE plain manifest
    present; the frozen driver's check_registration requires it). The manifest uses the DISTINCT
    filename me.nagaev.veles.otp23.dev.json — it can never collide with (and never touches)
    me.nagaev.veles.otp23.plain.json; only this harness's own dev file is created and removed
    (byte-verified before unlink).
  * the disposable Chrome profile ALSO gets a copy of the same manifest under
    <profile>/NativeMessagingHosts/, but it is explicitly NOT the relied-upon layer: recorded,
    harmless if inert, dies with the profile.
"""

import argparse
import json
import os
import pathlib
import platform
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone

APPROVED_APP = "/Users/raiden/homepc/work/otp23-cd971f0/native-bridge/feasibility/target/macos-poc/VelesOtp23PoC.app"
APPROVED_SHA = "e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c"
DEV_HOST_NAME = "me.nagaev.veles.otp23.dev"
DEV_BUNDLE = None  # resolved from --stage
EXTENSION_ID = "gmekhgkipjkikmaadaajljinkgfielei"
ORIGIN = f"chrome-extension://{EXTENSION_ID}/"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def codesign_info(path):
    import subprocess
    done = subprocess.run(["codesign", "-dvv", path], capture_output=True, text=True, timeout=30)
    return {"rc": done.returncode, "stderr": done.stderr[:900], "stdout": done.stdout[:400]}


def verify_strict(path):
    import subprocess
    done = subprocess.run(["codesign", "--verify", "--strict", path],
                          capture_output=True, text=True, timeout=30)
    return done.returncode == 0


def registration_state():
    """Exactly which veles manifests exist in the user scope (read-only fact, never modified here
    except the harness's own dev file, written by the required --user-scope-manifest)."""
    base = pathlib.Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts"
    return {"dir": str(base),
            "dev_manifest_exists": (base / f"{DEV_HOST_NAME}.json").exists(),
            "plain_manifest_exists": (base / "me.nagaev.veles.otp23.plain.json").exists(),
            "all_veles_named": sorted(p.name for p in base.glob("*veles*")) if base.is_dir() else []}


def write_dev_manifest(target_path):
    document = {"name": DEV_HOST_NAME,
                "description": "Veles OTP-23 M-11 dev host; synthetic bytes only",
                "path": str((pathlib.Path(DEV_BUNDLE) / "Contents/MacOS/otp23-host")),
                "type": "stdio",
                "allowed_origins": [ORIGIN]}
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    target_path.chmod(0o644)
    return {"path": str(target_path), "sha256": sha256(target_path), "document": document}


def install_profile_manifest(profile):
    return write_dev_manifest(pathlib.Path(profile) / "NativeMessagingHosts" / f"{DEV_HOST_NAME}.json")


def chrome_arguments(profile):
    """Same disposable-profile CDP argument set the frozen M-08 driver uses (no extra flags)."""
    return [CHROME, f"--user-data-dir={profile}", "--remote-debugging-port=0",
            "--enable-unsafe-extension-debugging", "--no-first-run", "--no-default-browser-check",
            "--disable-sync", "--window-size=1000,700", "about:blank"]


class CdpSession:
    """Deliberately self-contained (private M-11 artifact): minimal stdlib CDP over the loopback
    DevTools port; mirrors the frozen macos_cdp mechanics WITHOUT importing or editing it."""

    def __init__(self, port, path):
        import socket
        import base64
        sock = socket.create_connection(("127.0.0.1", port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            response += chunk
        if b" 101 " not in response.partition(b"\r\n\r\n")[0].split(b"\r\n")[0]:
            raise RuntimeError("devtools ws refused")
        self._sock = sock
        self._buffer = b""
        self.next_id = 0

    def call(self, method, params=None, session=None, timeout=60):
        import json as _json
        import struct
        self.next_id += 1
        payload = _json.dumps({"id": self.next_id, "method": method,
                               "params": params or {},
                               **({"sessionId": session} if session else {})}).encode()
        header = bytearray([0x81])
        if len(payload) < 126:
            header.append(0x80 | len(payload))
        elif len(payload) < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", len(payload))
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", len(payload))
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + mask + masked)
        deadline = time.monotonic() + timeout
        while True:
            remaining = max(0.1, deadline - time.monotonic())
            message = self._recv(remaining)
            import json as _json2
            reply = _json2.loads(message)
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply.get("result", {})

    def _recv(self, timeout):
        import struct
        self._sock.settimeout(timeout)
        while len(self._buffer) < 2:
            self._buffer += self._sock.recv(65536)
        b0, b1 = self._buffer[0], self._buffer[1]
        length = b1 & 0x7F
        offset = 2
        if length == 126:
            while len(self._buffer) < offset + 2:
                self._buffer += self._sock.recv(65536)
            (length,) = struct.unpack(">H", self._buffer[offset:offset + 2])
            offset += 2
        elif length == 127:
            while len(self._buffer) < offset + 8:
                self._buffer += self._sock.recv(65536)
            (length,) = struct.unpack(">Q", self._buffer[offset:offset + 8])
            offset += 8
        while len(self._buffer) < offset + length:
            self._buffer += self._sock.recv(65536)
        payload = self._buffer[offset:offset + length]
        self._buffer = self._buffer[offset + length:]
        if b0 & 0x0F == 8:
            raise ConnectionError("websocket close frame")
        return payload.decode()

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


AUTH_TRACE_MARKERS = ("bluetooth authorization before adapter init:", "adapter init:",
                      "adapter wait timed out:")


def read_answer(path, nonce):
    """The owner's nonce-bound answer, or (None, problem). Valid shapes ONLY:
    {"nonce": <this nonce>, "answered_by": "owner", "dialog_observed": true,
     "prompt_name_shown": "<verbatim>", "choice": "allow"|"deny"}  or
    {"nonce": <this nonce>, "answered_by": "owner", "dialog_observed": false}  (owner saw no prompt).
    Nothing is ever inferred (not from Settings, not from the trace)."""
    path = pathlib.Path(path)
    if not path.exists():
        return None, None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return None, f"answer file unreadable: {type(error).__name__}"
    if doc.get("nonce") != nonce or doc.get("answered_by") != "owner":
        return None, "answer file must carry this nonce and answered_by=owner"
    if doc.get("dialog_observed") is True:
        if not (isinstance(doc.get("prompt_name_shown"), str) and doc["prompt_name_shown"].strip()
                and doc.get("choice") in ("allow", "deny")):
            return None, "dialog_observed=true needs a verbatim prompt_name_shown and choice allow|deny"
    elif doc.get("dialog_observed") is not False:
        return None, "dialog_observed must be true or false (owner's own observation)"
    return doc, None


def reply_stage(reply):
    return (reply or {}).get("stage") if (reply or {}).get("type") == "error" else None


def watch_prompt(take_scan_reply, read_trace, answer_path, nonce, wait_s, restart_scan,
                 clock=time.monotonic, sleep=time.sleep, max_rescans=2):
    """Keep the host/port alive and observe until the owner's nonce answer AND an actual scan
    reply observed after it (or an authorized started scan) exist, or the deadline passes.

    Trace authorization lines are OBSERVATIONS only (the first one, 'before adapter init', is
    emitted before CoreBluetooth can even show a dialog). A pre-answer error reply (the host's
    10 s adapter budget expiring as permission_pending while the dialog is open) is recorded as
    pending, never as a denial; once the owner answered, at most `max_rescans` new scans are
    issued to observe the actual post-answer authorization/scan result."""
    start = clock()
    deadline = start + wait_s
    observations, seen, replies, answer_problems = [], set(), [], []
    answer, answer_t, rescans, awaiting = None, None, 0, False
    ended_by = "deadline"

    def sweep_trace(now):
        for line in read_trace().splitlines():
            if any(marker in line for marker in AUTH_TRACE_MARKERS) and line not in seen:
                seen.add(line)
                observations.append({"t_s": now, "line": line.strip()})

    while clock() < deadline:
        now = round(clock() - start, 1)
        sweep_trace(now)
        reply = take_scan_reply()
        if reply is not None:
            replies.append({"t_s": now, "reply": reply, "after_answer": answer is not None})
            awaiting = False
        if answer is None:
            answer, problem = read_answer(answer_path, nonce)
            if problem and problem not in answer_problems:
                answer_problems.append(problem)
            if answer is not None:
                answer_t = now
        last = replies[-1] if replies else None
        if answer is not None and last is not None:
            started = last["reply"].get("status") == "started"
            pending = reply_stage(last["reply"]) == "permission_pending"
            if started or (last["after_answer"] and (not pending or rescans >= max_rescans)):
                ended_by = "answer_and_scan_reply"
                break
            if not awaiting and rescans < max_rescans:
                restart_scan()
                rescans += 1
                awaiting = True
        sleep(1.0)
    sweep_trace(round(clock() - start, 1))  # lines the host wrote just before its final reply
    return {"ended_by": ended_by, "waited_s": round(clock() - start, 1), "observations": observations,
            "replies": replies, "answer": answer, "answer_t_s": answer_t,
            "answer_problems": answer_problems, "rescans": rescans,
            "final_reply": replies[-1]["reply"] if replies else None}


def classify(watch):
    """From the final scan reply plus the post-init authorization line; never the pre-init line."""
    final = (watch or {}).get("final_reply")
    post_init = [o["line"] for o in (watch or {}).get("observations", [])
                 if "adapter init:" in o["line"] and "before adapter init" not in o["line"]
                 or "adapter wait timed out:" in o["line"]]
    auth = None
    for line in post_init:
        found = re.search(r"authorization=(\w+)", line)
        if found:
            auth = found.group(1)
    if final is None:
        state = "no_scan_reply"
    elif final.get("status") == "started":
        state = "authorized_started"
    elif reply_stage(final) == "permission_pending":
        state = "pending_at_host_budget_not_denial"
    elif reply_stage(final) == "access_denied":
        state = "access_denied"
    elif final.get("type") == "error":
        state = f"scan_error:{final.get('code') or 'unknown'}:{reply_stage(final) or 'unknown'}"
    else:
        state = f"scan_reply:{final.get('status') or final.get('type')}"
    return {"state": state, "post_init_authorization": auth,
            "owner_dialog_observed": ((watch or {}).get("answer") or {}).get("dialog_observed"),
            "ended_by": (watch or {}).get("ended_by")}


def main():
    global DEV_BUNDLE
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, help="M-11 stage dir holding target-dev/VelesOtp23Dev.app and extension/")
    parser.add_argument("--out-dir", required=True, help="unique attempt dir (never overwritten)")
    parser.add_argument("--user-scope-manifest", action="store_true", required=True,
                        help="REQUIRED (F-M11-1): write the distinct user-scope dev manifest - "
                             "Chrome discovers NM manifests only in system/user scope; profile-only "
                             "runs are unsupported and rejected before any launch")
    parser.add_argument("--prompt-wait-s", type=float, default=720.0)
    args = parser.parse_args()
    DEV_BUNDLE = pathlib.Path(args.stage) / "target-dev" / "VelesOtp23Dev.app"
    stage = pathlib.Path(args.stage)
    if not (DEV_BUNDLE / "Contents/MacOS/otp23-host").is_file():
        sys.exit("error: dev bundle missing; run ./package-dev.sh first")
    out_dir = pathlib.Path(args.out_dir)
    if out_dir.exists():
        sys.exit(f"error: {out_dir} exists; unique attempt dirs only")
    out_dir.mkdir(parents=True)

    def stamp():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    record = {"what": "OTP-23 M-11 dev capture (distinct unsigned identity, existing admin account)",
              "started_utc": stamp(), "platform": platform.platform(), "python": sys.version.split()[0]}
    chrome_proc = None
    profile = None
    user_scope = None
    cdp = None
    trace_path = out_dir / "m11-dev.host-trace.log"
    problems = []

    def env_keys():
        return {k: v for k, v in os.environ.items() if k.startswith("VELES")}

    try:
        # ---- preconditions (read-only) --------------------------------------------------------
        fixture = {"path": APPROVED_APP,
                   "binary_sha256": sha256(pathlib.Path(APPROVED_APP) / "Contents/MacOS/otp23-host"),
                   "codesign_verify_strict": verify_strict(APPROVED_APP)}
        if fixture["binary_sha256"] != APPROVED_SHA or not fixture["codesign_verify_strict"]:
            sys.exit("error: approved signed fixture identity drifted — refusing to proceed")
        record["approved_fixture_before"] = fixture
        reg_before = registration_state()
        record["registration_state_before"] = reg_before
        if reg_before["dev_manifest_exists"]:
            sys.exit("error: a user-scope dev manifest already exists — unique M-11 state violated")
        record["extension_files"] = {
            path.name: sha256(path) for path in sorted((stage / "extension").iterdir())}
        dev_binary = DEV_BUNDLE / "Contents/MacOS/otp23-host"
        record["dev_bundle"] = {"path": str(DEV_BUNDLE), "binary_sha256": sha256(dev_binary),
                                "codesign_dvv_recorded": codesign_info(DEV_BUNDLE),
                                "expected": {"bundle_id": "me.nagaev.veles.bridge.dev",
                                             "label": "VelesOtp23Dev",
                                             "origin_allowed": ORIGIN, "host_name": DEV_HOST_NAME}}
        # distinct dev label/identity MUST hold before the first launch (the approved fixture has no
        # CFBundleDisplayName; its Settings label comes from CFBundleName, so the dev delta is the same key)
        import plistlib
        with open(DEV_BUNDLE / "Contents/Info.plist", "rb") as handle:
            plist = plistlib.load(handle)
        identity = {key: plist.get(key) for key in
                    ("CFBundleIdentifier", "CFBundleName", "CFBundleDisplayName", "CFBundleExecutable")}
        record["dev_bundle"]["info_plist"] = identity
        if (identity["CFBundleIdentifier"] != "me.nagaev.veles.bridge.dev"
                or identity["CFBundleName"] != "VelesOtp23Dev"
                or identity["CFBundleDisplayName"] not in (None, "VelesOtp23Dev")):
            sys.exit(f"error: dev bundle identity/label is not the distinct dev one: {identity}")

        # ---- launch ---------------------------------------------------------------------------
        profile = tempfile.mkdtemp(prefix="veles-otp23-m11-dev-profile-")
        profile_manifest = install_profile_manifest(profile)
        record["profile_manifest_profile_local"] = {**profile_manifest,
                                                    "layer": "profile-dir copy: recorded for "
                                                             "completeness, NOT the relied-upon "
                                                             "layer (dies with the profile)"}
        if args.user_scope_manifest:
            user_scope = write_dev_manifest(pathlib.Path.home() /
                                            "Library" / "Application Support" / "Google" / "Chrome" /
                                            "NativeMessagingHosts" / f"{DEV_HOST_NAME}.json")
            user_scope["layer"] = "user-scope distinct-name manifest (own; removed/recorded at end)"
            record["user_scope_manifest"] = user_scope
        env = dict(os.environ, VELES_OTP23_TRACE=str(trace_path))
        env.pop("VELES_OTP23_DETACHED_CLOSE", None)
        import subprocess
        chrome_proc = subprocess.Popen(chrome_arguments(profile), env=env)
        record["chrome"] = {"argv": chrome_arguments(profile).copy(),
                            "env_veles_keys": env_keys(),
                            "pid": chrome_proc.pid,
                            "context": {"uid": os.getuid(), "ssh": bool(os.environ.get("SSH_CONNECTION"))}}
        port_file = pathlib.Path(profile) / "DevToolsActivePort"
        for _ in range(300):
            if port_file.exists() and len(port_file.read_text().splitlines()) >= 2:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Chrome did not publish DevToolsActivePort")
        port, ws_path = port_file.read_text().splitlines()[:2]
        cdp = CdpSession(int(port), ws_path)
        record["chrome"].update({"cdp_port": int(port),
                                 "version": cdp.call("Browser.getVersion").get("product")})
        ext_id = cdp.call("Extensions.loadUnpacked", {"path": str(stage / "extension")})["id"]
        record["extension_loaded"] = {"id": ext_id, "matches_expected": ext_id == EXTENSION_ID}
        target = cdp.call("Target.createTarget", {"url": f"chrome-extension://{ext_id}/connector.html"})["targetId"]
        session_id = cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        cdp.call("Runtime.enable", session=session_id)

        def js(expression, timeout=70):
            reply = cdp.call("Runtime.evaluate",
                             {"expression": expression, "awaitPromise": True, "returnByValue": True},
                             session=session_id, timeout=timeout)
            if "exceptionDetails" in reply:
                raise RuntimeError(str(reply["exceptionDetails"])[:500])
            return reply["result"].get("value")

        for _ in range(50):
            if js("typeof window.otp23 === 'object'"):
                break
            time.sleep(0.1)
        cdp.call("Runtime.evaluate",
                 {"expression": f"otp23.connect({json.dumps(DEV_HOST_NAME)}); 1"}, session=session_id)
        hello = cdp.call("Runtime.evaluate",
                         {"expression": "otp23.hello()", "awaitPromise": True, "returnByValue": True},
                         session=session_id, timeout=40)["result"].get("value")
        record["hello"] = hello
        if not (hello or {}).get("type") == "ready":
            raise RuntimeError(f"dev host hello failed: {hello}")

        # ---- scan: the first Bluetooth-touching event; the OS prompt (if any) appears here ----
        nonce = str(uuid.uuid4())
        request = {"nonce": nonce,
                   "issued_utc": stamp(),
                   "human_step": "Do NOT click yet. Read the EXACT name this Bluetooth permission "
                                 "dialog shows (which app is asking), reply it verbatim to the "
                                 "parent, and only then click your Allow/Deny choice.",
                   "expected_dialog": "the Bluetooth permission prompt naming the app asking for "
                                      "Bluetooth access (the M-11 observation: Chrome vs the dev "
                                      "binary)",
                   "answer_file": str(out_dir / f"m11-prompt-{nonce}.answer.json"),
                   "answer_format": {"nonce": nonce, "answered_by": "owner",
                                     "dialog_observed": "true|false (owner's own observation)",
                                     "prompt_name_shown": "<exact verbatim text, only if a dialog was seen>",
                                     "choice": "allow|deny (only if a dialog was seen)"}}
        record["human_prompt_request"] = request
        (out_dir / f"m11-prompt-{nonce}.request.json").write_text(json.dumps(request, indent=2),
                                                                  encoding="utf-8")
        print(f"PROMPT-WATCH-REQUESTED nonce={nonce} answer_file={request['answer_file']}")
        js_expr = ("window.__scan = null; "
                   f"otp23.startScan('8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51')"
                   f".then(r => {{ window.__scan = r; }}); 1")
        cdp.call("Runtime.evaluate", {"expression": js_expr}, session=session_id)
        watch = watch_prompt(
            take_scan_reply=lambda: js("(() => { const r = window.__scan; window.__scan = null; return r; })()",
                                       timeout=5),
            read_trace=lambda: trace_path.read_text(errors="replace") if trace_path.exists() else "",
            answer_path=out_dir / f"m11-prompt-{nonce}.answer.json", nonce=nonce,
            wait_s=args.prompt_wait_s,
            restart_scan=lambda: cdp.call("Runtime.evaluate", {"expression": js_expr}, session=session_id))
        record["prompt_watch"] = watch
        record["human_prompt_answer"] = watch["answer"]
        record["scan_outcome"] = watch["final_reply"]
        record["authorized_state_classified"] = classify(watch)
        if watch["ended_by"] == "deadline":
            problems.append("prompt watch reached its deadline without the owner's nonce answer "
                            "and a post-answer scan reply (recorded as observed, not adjudicated)")
        record["problems_classified_denial_possible"] = "a denied/pending outcome is a VALID M-11 " \
            "observation (recorded as-is); the reviewer adjudicates; no fabricated unsigned PASS"
        # stop every scan that started, then close cleanly
        for item in watch["replies"]:
            handle = item["reply"].get("scan_handle")
            if item["reply"].get("status") == "started" and handle:
                js(f"otp23.stopScan({json.dumps(handle)}); 1", timeout=20)
                record.setdefault("scans_stopped_after_capture", []).append(handle)
        done = cdp.call("Runtime.evaluate",
                        {"expression": "otp23.shutdown()", "awaitPromise": True, "returnByValue": True},
                        session=session_id, timeout=30)["result"].get("value")
        record["shutdown_reply"] = done
    except SystemExit as refusal:  # N1: a refused run's record must not read as clean
        problems.append(f"refused: {refusal.code}")
        record["refused"] = str(refusal.code)
        raise
    except Exception as error:  # noqa: BLE001 - record, then continue teardown
        problems.append(f"{type(error).__name__}: {error}")
        record["failure"] = __import__("traceback").format_exc()
    finally:
        if chrome_proc is not None:
            try:
                if cdp is not None:
                    cdp.call("Browser.close", timeout=15)
            except Exception:  # noqa: BLE001
                chrome_proc.terminate()
            try:
                chrome_proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                chrome_proc.kill()
        if profile:
            shutil.rmtree(profile, ignore_errors=True)
            record["disposable_profile_removed"] = True
        if user_scope is not None:
            target = pathlib.Path(user_scope["path"])
            if target.exists():
                import hashlib
                if sha256(target) == user_scope["sha256"]:
                    target.unlink()
                    record["user_scope_manifest_removed"] = {"path": user_scope["path"],
                                                             "note": "only this harness's own dev "
                                                                     "manifest; nothing else touched"}
                else:
                    record["user_scope_manifest_remove_refused"] = {
                        "path": user_scope["path"], "note": "bytes drifted from what this harness "
                                                            "wrote; left for the operator to inspect"}
        try:
            fixture_after = {"path": APPROVED_APP,
                             "binary_sha256": sha256(pathlib.Path(APPROVED_APP) / "Contents/MacOS/otp23-host"),
                             "codesign_verify_strict": verify_strict(APPROVED_APP)}
        except OSError as error:  # N3: missing approved app is recorded, the capture is still written
            fixture_after = {"path": APPROVED_APP, "binary_sha256": None, "codesign_verify_strict": False,
                             "unavailable": f"{type(error).__name__}: {error}"}
        if fixture_after["binary_sha256"] != APPROVED_SHA or not fixture_after["codesign_verify_strict"]:
            problems.append("APPROVED FIXTURE IDENTITY CHANGED — STOP and report")
        record["approved_fixture_after"] = fixture_after
        record["registration_state_after"] = registration_state()
        record["settings_read_only_owner_check"] = (
            "re-request the read-only System Settings Bluetooth entry check after the run "
            "(VelesOtp23PoC unchanged; any NEW VelesOtp23Dev entry = recorded fact, never removed)")
        record["problems"] = problems
        record["finished_utc"] = stamp()
        (out_dir / "m11-capture.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(json.dumps({k: record.get(k) for k in ("hello", "scan_outcome",
                                                     "authorized_state_classified", "problems")}, indent=1))
    return 0 if not problems else 1



if __name__ == "__main__":
    raise SystemExit(main())