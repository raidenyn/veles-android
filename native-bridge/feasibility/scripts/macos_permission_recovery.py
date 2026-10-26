"""OTP-23 macOS persistent-profile Bluetooth permission recovery driver (Aqua, foreground).

ONE disposable Chrome process/profile and ONE Extensions.loadUnpacked; a random synthetic sentinel
(no real trust material, no OTP) is persisted in the extension origin's localStorage and only its
SHA-256 is recorded. Phases against the already registered, already signed fixture (this driver
never signs, re-signs, registers, unregisters, edits System Settings or touches TCC):

  baseline   granted: hello + startScan + >=1 phone, stop, close the native port, wait host gone.
             Writes ready.json {phase: human_disable_needed} and waits for the controller ack.
  denied     ack says the Settings entry was toggled OFF: new connectNative (same host), hello,
             startScan must give access_denied. Prompt shown/not_shown/unknown comes from the ack
             only. Close port, wait gone. Writes ready.json {phase: human_enable_needed}, waits ack.
  recovered  ack says toggled ON: new host (same binary), hello, startScan started + >=1 phone,
             stopScan, shutdown, host gone; sentinel/Chrome PID/profile/extension ID/binary hash
             unchanged; profile removed at the end.

Parent (Linux, over SSH): read <state-dir>/ready.json, then write <state-dir>/ack.json:
  {"nonce": <ready.nonce>, "ready": <ready.phase>, "toggled": "off"|"on",
   "entry_label": "<literal Settings entry text the human saw>", "prompt": "shown"|"not_shown"|"unknown"}

Total lifetime is bounded (--max-seconds, default 600) and every wait is bounded. The only waiting
here is this foreground test controller loop: no keepalive, no background measurement.
Python 3.9, standard library only. Exit 0 PASS, 1 FAIL, 2 usage/environment.
"""

import argparse
import json
import os
import pathlib
import platform
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import macos_permission_recovery_core as core
import macos_poc
from macos_install import MANIFEST_DIR_DEFAULT, PACKAGED_BINARY, read_identity

SENTINEL_KEY = "otp23_permission_recovery_sentinel"
DEFAULT_MAX_SECONDS = 600
DEFAULT_WAIT_SECONDS = 270
SCAN_WINDOW_SECONDS = 30


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json_atomic(path, document):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(macos_poc.scrub(json.dumps(document, indent=2)), encoding="utf-8")
    os.replace(str(tmp), str(path))


class Controller:
    """File-based handshake with the parent controller (outside the browser profile)."""

    def __init__(self, state_dir, sleep=time.sleep, clock=time.monotonic):
        self.dir = pathlib.Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.sleep, self.clock = sleep, clock
        self.nonce = None

    def publish(self, phase):
        (self.dir / "ack.json").unlink(missing_ok=True)  # never accept an older answer
        self.nonce = uuid.uuid4().hex
        write_json_atomic(self.dir / "ready.json", {
            "phase": phase, "nonce": self.nonce, "utc": utc_now(),
            "ack_file": str(self.dir / "ack.json"),
            "instruction": ("toggle the VelesOtp23PoC Bluetooth entry OFF" if phase == core.READY_DISABLE
                            else "toggle the VelesOtp23PoC Bluetooth entry ON"),
        })

    def wait_ack(self, phase, timeout):
        """(ack, error); error is ack_missing on timeout, or the parse_ack rejection."""
        ack_path = self.dir / "ack.json"
        deadline = self.clock() + max(timeout, 0)
        while True:
            try:
                raw = json.loads(ack_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = None  # absent or half-written: keep waiting
            if raw is not None:
                return core.parse_ack(raw, self.nonce, phase)
            if self.clock() >= deadline:
                return None, "ack_missing"
            self.sleep(1.0)


def signature_info(app):
    """Read-only codesign -dvv summary, or an explicit unavailable marker. Never invented."""
    try:
        done = subprocess.run(["codesign", "-dvv", str(app)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        return {"available": False, "reason": type(error).__name__}
    if done.returncode != 0:
        return {"available": False, "reason": " ".join(done.stderr.split())[:160]}
    text, info = done.stderr, {"available": True}
    for key, pattern in (("identifier", r"^Identifier=(.*)$"), ("team", r"^TeamIdentifier=(.*)$"),
                         ("signature", r"^Signature=(.*)$"), ("authority", r"^Authority=(.*)$"),
                         ("cdhash", r"^CDHash=(.*)$")):
        found = re.search(pattern, text, re.M)
        info[key] = found.group(1).strip() if found else None
    return info


def sentinel_js(value):
    return f"localStorage.setItem('{SENTINEL_KEY}', '{value}'); 1"


class Run:
    def __init__(self, args, out, chrome_factory=None, now=time.monotonic):
        self.args, self.out = args, out
        self.chrome_factory = chrome_factory or macos_poc.ChromeRun
        self.deadline = now() + args.max_seconds
        self.now = now
        self.controller = Controller(args.state_dir)
        self.result = {"driver": "macos_permission_recovery", "started_utc": utc_now(),
                       "commands": [" ".join(sys.argv)], "result": "FAIL",
                       "macos_version": platform.mac_ver()[0] or platform.platform(),
                       "python_version": sys.version.split()[0], "abort": None,
                       "human_settings_observation": core.human_observation(args.human_note),
                       "waivers": ["fresh new-account first prompt: not observed (already granted "
                                   "account); non-blocking"],
                       "unsigned_dev_matrix": "unmet unless the parent supplies a separate explicit "
                                              "unsigned app via --app; nothing is created here",
                       "provenance_note": "Apple Development signing is not CI/distribution provenance"}
        self.pre_existing = set()

    def remaining(self):
        return self.deadline - self.now()

    def wait_ack(self, phase):
        return self.controller.wait_ack(phase, min(self.args.wait_seconds, self.remaining()))

    def save(self):
        write_json_atomic(self.out, self.result)

    def host_row(self, pre_existing):
        fresh = [r for r in macos_poc.host_processes(macos_poc.parse_ps(macos_poc.ps_snapshot()))
                 if r["pid"] not in pre_existing]
        if len(fresh) != 1:
            raise AssertionError(f"expected exactly one new own host process, saw {len(fresh)}")
        return fresh[0]["pid"]

    def state(self, chrome, ext_id, sentinel_sha, binary, app, loads):
        value = chrome.js(self.session, f"localStorage.getItem('{SENTINEL_KEY}')")
        return {"sentinel_sha256": core.sha256_text(value) if value else None,
                "extension_id": ext_id, "chrome_pid": chrome.proc.pid, "profile": str(chrome.profile),
                "binary_sha256": macos_poc.file_sha256(binary), "signature": signature_info(app),
                "load_unpacked_calls": loads, "utc": utc_now()}

    def native_session(self, chrome, pre_existing):
        """connectNative -> hello; returns (hello_type, host_pid or None)."""
        js = chrome.js
        js(self.session, f"otp23.connect({json.dumps(macos_poc.HOST_NAME)}); 1")
        hello = js(self.session, "otp23.hello()") or {}
        pid = self.host_row(pre_existing) if hello.get("type") == "ready" else None
        return hello.get("type") or "error", pid

    def scan(self, chrome):
        js = chrome.js
        try:
            reply = js(self.session, f"otp23.startScan({json.dumps(macos_poc.VELES_SERVICE)})")
        except Exception as error:  # timeout of the evaluate is a separate verdict from a denial
            return {"timeout": True, "error": type(error).__name__}, "timeout", None
        return reply, core.classify_scan(reply), reply.get("scan_handle") if isinstance(reply, dict) else None

    def devices(self, chrome, window):
        found, deadline = set(), time.monotonic() + window
        while not found and time.monotonic() < deadline:
            for event in chrome.js(self.session, "otp23.drainEvents()") or []:
                message = event.get("message", {})
                if message.get("type") == "device_found":
                    found.add(message.get("device_handle"))
            time.sleep(0.25)
        return len(found)

    def close_port(self, chrome, pid):
        chrome.js(self.session, "otp23.closePort(); 1")
        return pid is None or macos_poc.wait_gone(pid, 10) is not None

    def execute(self):
        args = self.args
        identity = read_identity()
        app = args.app or macos_poc.ROOT / "target" / "macos-poc" / "VelesOtp23PoC.app"
        binary = app / PACKAGED_BINARY
        macos_poc.require(binary.is_file(), f"{binary} missing")
        manifest = macos_poc.check_registration(args.manifest_dir, binary)  # read-only
        self.result["host_binary"] = str(binary)
        self.result["host_binary_sha256_start"] = macos_poc.file_sha256(binary)
        self.result["source_revision"] = macos_poc.source_revision()
        if args.expect_binary_sha_prefix:
            macos_poc.require(
                (self.result["host_binary_sha256_start"] or "").startswith(args.expect_binary_sha_prefix),
                "binary hash does not match --expect-binary-sha-prefix")
        pre_existing = {r["pid"] for r in macos_poc.host_processes(macos_poc.parse_ps(macos_poc.ps_snapshot()))}
        trace = pathlib.Path(args.state_dir) / "host-trace.log"
        trace.unlink(missing_ok=True)
        self.pre_existing = pre_existing
        run = {}
        self.result["run"] = run
        chrome = self.chrome_factory(chrome_path=args.chrome, extra_env={"VELES_OTP23_TRACE": str(trace)},
                                     manifest_source=manifest)
        profile = chrome.profile
        try:
            loads = 0
            ext_id = chrome.cdp.call("Extensions.loadUnpacked", {"path": str(macos_poc.EXT_DIR)})["id"]
            loads += 1
            macos_poc.require(ext_id == identity["extension_id"], "extension id drifted")
            _, self.session = chrome.open_page(f"chrome-extension://{ext_id}/connector.html")
            for _ in range(50):
                if chrome.js(self.session, "typeof window.otp23 === 'object'"):
                    break
                time.sleep(0.1)
            sentinel = uuid.uuid4().hex + uuid.uuid4().hex  # random, synthetic, never logged
            chrome.js(self.session, sentinel_js(sentinel))
            sentinel_sha = core.sha256_text(sentinel)
            del sentinel

            # phase 1: granted baseline
            hello, pid = self.native_session(chrome, pre_existing)
            reply, klass, handle = self.scan(chrome)
            devices = self.devices(chrome, SCAN_WINDOW_SECONDS) if klass == "started" else 0
            if handle:
                chrome.js(self.session, f"otp23.stopScan({json.dumps(handle)})")
            run["baseline"] = {**self.state(chrome, ext_id, sentinel_sha, binary, app, loads),
                               "hello_type": hello, "scan_class": klass, "devices": devices,
                               "host_gone": self.close_port(chrome, pid)}
            self.save()
            if run["baseline"]["sentinel_sha256"] != sentinel_sha:
                run["baseline"]["sentinel_sha256"] = "unreadable"
            self.controller.publish(core.READY_DISABLE)
            ack, err = self.wait_ack(core.READY_DISABLE)

            # phase 2: denied
            hello, pid = self.native_session(chrome, pre_existing)
            reply, klass, handle = self.scan(chrome)
            if handle:  # unexpected: scan started although denial was expected
                chrome.js(self.session, f"otp23.stopScan({json.dumps(handle)})")
            run["denied"] = {**self.state(chrome, ext_id, sentinel_sha, binary, app, loads),
                             "hello_type": hello, "scan_class": klass,
                             "scan_reply_code": [(reply or {}).get("code"), (reply or {}).get("stage")]
                             if isinstance(reply, dict) else None,
                             "host_gone": self.close_port(chrome, pid), "ack": ack, "ack_error": err}
            self.save()
            self.controller.publish(core.READY_ENABLE)
            ack, err = self.wait_ack(core.READY_ENABLE)

            # phase 3: recovered
            hello, pid = self.native_session(chrome, pre_existing)
            reply, klass, handle = self.scan(chrome)
            devices = self.devices(chrome, SCAN_WINDOW_SECONDS) if klass == "started" else 0
            stop = (chrome.js(self.session, f"otp23.stopScan({json.dumps(handle)})") or {}) if handle else {}
            done = chrome.js(self.session, "otp23.shutdown()") or {} if pid else {}
            run["recovered"] = {**self.state(chrome, ext_id, sentinel_sha, binary, app, loads),
                                "hello_type": hello, "scan_class": klass, "devices": devices,
                                "stop_status": stop.get("status"), "shutdown_type": done.get("type"),
                                "host_gone": pid is None or macos_poc.wait_gone(pid, 10) is not None,
                                "ack": ack, "ack_error": err}
            self.save()
        finally:
            chrome.close()
        run["profile_removed"] = not pathlib.Path(profile).exists()
        parsed = macos_poc.parse_trace(trace)
        self.result["native_trace_safe_summary"] = {
            "conn_observations": len(set(parsed["mtu_by_conn"]) | set(parsed["budget_by_conn"])),
            "sends": len(parsed["sends"]), "replies": len(parsed["replies"]), "payloads_recorded": False}
        self.result["host_binary_sha256_end"] = macos_poc.file_sha256(binary)
        if self.result["host_binary_sha256_end"] != self.result["host_binary_sha256_start"]:
            run["baseline"]["binary_sha256"] = "changed_during_run"

    def run(self):
        try:
            self.execute()
        except SystemExit as error:
            self.result["abort"] = f"environment: {error.code}"
        except Exception as error:
            self.result["abort"] = f"{type(error).__name__}: {str(error)[:200]}"
        run = dict(self.result.get("run", {}))
        survivors = [r["pid"] for r in macos_poc.host_processes(macos_poc.parse_ps(macos_poc.ps_snapshot()))
                     if r["pid"] not in self.pre_existing]
        run["abort"] = self.result["abort"]
        run.setdefault("profile_removed", False)
        verdict = core.evaluate({**run, "surviving_hosts": survivors})
        self.result.update(verdict)
        self.result["finished_utc"] = utc_now()
        self.save()
        print(f"result={self.result['result']} failures={self.result['failures']} -> {self.out}")
        return 0 if self.result["result"] == "PASS" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=pathlib.Path)
    parser.add_argument("--state-dir", required=True, type=pathlib.Path,
                        help="directory outside the browser profile holding ready.json / ack.json")
    parser.add_argument("--app", type=pathlib.Path, default=None)
    parser.add_argument("--manifest-dir", type=pathlib.Path, default=MANIFEST_DIR_DEFAULT)
    parser.add_argument("--chrome", default=macos_poc.DEFAULT_CHROME)
    parser.add_argument("--max-seconds", type=int, default=DEFAULT_MAX_SECONDS)
    parser.add_argument("--wait-seconds", type=int, default=DEFAULT_WAIT_SECONDS,
                        help="bound for each controller wait")
    parser.add_argument("--expect-binary-sha-prefix", default=None)
    parser.add_argument("--human-note", default=None,
                        help="exact human statement about Settings; recorded as a user report, "
                             "absent means not_supplied")
    args = parser.parse_args(argv)
    if args.max_seconds <= 0 or args.wait_seconds <= 0:
        parser.error("bounds must be positive")
    if sys.platform != "darwin":
        print("error: requires macOS Aqua session", file=sys.stderr)
        return 2
    return Run(args, args.out).run()


if __name__ == "__main__":
    sys.exit(main())
