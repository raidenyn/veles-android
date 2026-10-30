"""Owner-approved M-03 only: same signed host, one app-scoped Bluetooth reset.

No signing, key access, new account, global reset, or production code changes.
Manual observation is supplied separately, never inferred from Settings/logs.
"""
import hashlib
import json
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
import macos_poc as mp
import macos_install as mi

APP = pathlib.Path("/Users/raiden/homepc/work/otp23-cd971f0/native-bridge/feasibility/target/macos-poc/VelesOtp23PoC.app")
EXPECTED = "e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c"
OUT = ROOT / "capture"
OUT.mkdir(exist_ok=True)
result = {"operator": "OpenCode parent gpt-6.1-sol", "scope": "M-03 fresh signed-host prompt on existing account; not historical prompt recovery", "steps": []}

def now():
    return datetime.now(timezone.utc).isoformat()

def save():
    tmp = OUT / "result.tmp"
    tmp.write_text(json.dumps(result, indent=2) + "\n")
    tmp.replace(OUT / "result.json")

def note(kind, value):
    result["steps"].append({"utc": now(), "kind": kind, "value": value})
    save()
    print(kind, json.dumps(value), flush=True)

def run(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=40)
    record = {"command": args, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    note("command", record)
    if p.returncode:
        raise RuntimeError("Command failed; no broader fallback: " + repr(args))
    return p.stdout + p.stderr

chrome = None
registered = False
manifest = mi.MANIFEST_DIR_DEFAULT / (mp.HOST_NAME + ".json")
backup = manifest.with_name(manifest.name + mi.BACKUP_SUFFIX)
previous = None
session = None
try:
    result["started_utc"] = now()
    result["owner_approval"] = "User answered Yes to app-specific Bluetooth reset and prompt attendance; 2026-10-30 conversation."
    result["binary_sha256_before"] = hashlib.sha256((APP / mi.PACKAGED_BINARY).read_bytes()).hexdigest()
    assert result["binary_sha256_before"] == EXPECTED
    run(["/usr/bin/codesign", "--verify", "--strict", str(APP)])
    signature = run(["/usr/bin/codesign", "-dvv", str(APP)])
    assert "Identifier=me.nagaev.veles.bridge\n" in signature
    assert "TeamIdentifier=7T39M26Z99\n" in signature
    assert not backup.exists(), "Pre-existing registration backup; stop rather than overwrite"
    if manifest.exists():
        previous = manifest.read_bytes()
        assert mi.is_owned(json.loads(previous), mp.HOST_NAME), "Unowned registration"
    note("registration_preflight", {"target": str(manifest), "existed": previous is not None,
                                    "previous_sha256": hashlib.sha256(previous).hexdigest() if previous else None})
    mi.register(APP, mi.MANIFEST_DIR_DEFAULT)
    registered = True
    note("registered_manifest", json.loads(manifest.read_text()))
    run(["/usr/bin/tccutil", "reset", "BluetoothAlways", "me.nagaev.veles.bridge"])
    chrome = mp.ChromeRun(extra_env={"VELES_OTP23_TRACE": str(OUT / "host.trace.log")}, manifest_source=manifest)
    note("chrome", {"pid": chrome.proc.pid, "version": chrome.version, "profile": str(chrome.profile)})
    ext_id = chrome.cdp.call("Extensions.loadUnpacked", {"path": str(ROOT / "extension")})["id"]
    assert ext_id == mi.read_identity()["extension_id"]
    _, session = chrome.open_page(f"chrome-extension://{ext_id}/connector.html")
    for _ in range(100):
        if chrome.js(session, "typeof window.otp23 === 'object'"):
            break
        time.sleep(.1)
    chrome.js(session, f"otp23.connect({json.dumps(mp.HOST_NAME)}); 1")
    ready = chrome.js(session, "otp23.hello()")
    note("hello", ready)
    assert ready.get("type") == "ready"
    rows = mp.parse_ps(mp.ps_snapshot())
    hosts = [r for r in mp.host_processes(rows) if str(APP / mi.PACKAGED_BINARY) in r["args"]]
    note("signed_host_processes", [{"pid": r["pid"], "ppid": r["ppid"], "ancestry": mp.ancestry(rows, r["pid"])} for r in hosts])
    initial_scan = chrome.js(session, f"otp23.startScan({json.dumps(mp.VELES_SERVICE)})")
    note("initial_scan", initial_scan)
    note("awaiting_owner", {"answer_file": str(OUT / "owner-answer.json"), "deadline_seconds": 1800,
                            "instruction": "Observe exact prompt app name, click Allow, then report both in chat."})
    deadline = time.monotonic() + 1800
    while not (OUT / "owner-answer.json").exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Owner observation deadline expired; no observation inferred")
        time.sleep(1)
    answer = json.loads((OUT / "owner-answer.json").read_text())
    note("owner_answer", answer)
    if initial_scan.get("type") == "scan_state" and initial_scan.get("status") == "started":
        note("stop_initial_scan", chrome.js(session, f"otp23.stopScan({json.dumps(initial_scan['scan_handle'])})"))
    scan = chrome.js(session, f"otp23.startScan({json.dumps(mp.VELES_SERVICE)})")
    note("post_allow_scan", scan)
    if scan.get("type") == "scan_state" and scan.get("status") == "started":
        time.sleep(3)
        note("stop_scan", chrome.js(session, f"otp23.stopScan({json.dumps(scan['scan_handle'])})"))
    note("events", chrome.js(session, "otp23.drainEvents()"))
    note("shutdown", chrome.js(session, "otp23.shutdown()"))
    result["capture_status"] = "COMPLETED_REQUIRES_INDEPENDENT_ADJUDICATION"
except Exception as exc:
    result["capture_status"] = "ERROR"
    note("exception", {"type": type(exc).__name__, "message": str(exc)})
finally:
    if chrome is not None:
        chrome.close()
        note("owned_chrome_closed", True)
    if registered:
        mi.unregister(mi.MANIFEST_DIR_DEFAULT)
        restored = manifest.read_bytes() if manifest.exists() else None
        note("registration_restored", restored == previous)
    result["binary_sha256_after"] = hashlib.sha256((APP / mi.PACKAGED_BINARY).read_bytes()).hexdigest()
    result["ended_utc"] = now()
    save()
