"""Read back the restored grant with a fresh Chrome-launched, unchanged host. No reset."""
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
OUT = ROOT / "capture"
result = {"started_utc": datetime.now(timezone.utc).isoformat(), "scope": "Fresh-process post-Allow authorization verification; no reset"}
manifest = mi.MANIFEST_DIR_DEFAULT / (mp.HOST_NAME + ".json")
chrome = None
registered = False
try:
    assert not manifest.exists(), "Do not overwrite an existing registration"
    result["binary_sha256"] = hashlib.sha256((APP / mi.PACKAGED_BINARY).read_bytes()).hexdigest()
    assert result["binary_sha256"] == "e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c"
    mi.register(APP, mi.MANIFEST_DIR_DEFAULT)
    registered = True
    chrome = mp.ChromeRun(extra_env={"VELES_OTP23_TRACE": str(OUT / "verify-grant.trace.log")}, manifest_source=manifest)
    result["chrome"] = {"pid": chrome.proc.pid, "version": chrome.version}
    ext = chrome.cdp.call("Extensions.loadUnpacked", {"path": str(ROOT / "extension")})["id"]
    assert ext == mi.read_identity()["extension_id"]
    _, session = chrome.open_page(f"chrome-extension://{ext}/connector.html")
    for _ in range(100):
        if chrome.js(session, "typeof window.otp23 === 'object'"):
            break
        time.sleep(.1)
    chrome.js(session, f"otp23.connect({json.dumps(mp.HOST_NAME)}); 1")
    result["hello"] = chrome.js(session, "otp23.hello()")
    scan = chrome.js(session, f"otp23.startScan({json.dumps(mp.VELES_SERVICE)})")
    result["scan"] = scan
    assert scan.get("type") == "scan_state" and scan.get("status") == "started"
    result["stop"] = chrome.js(session, f"otp23.stopScan({json.dumps(scan['scan_handle'])})")
    result["shutdown"] = chrome.js(session, "otp23.shutdown()")
    result["status"] = "CAPTURED"
except Exception as exc:
    result["status"] = "ERROR"
    result["exception"] = {"type": type(exc).__name__, "message": str(exc)}
finally:
    if chrome:
        chrome.close()
    if registered:
        mi.unregister(mi.MANIFEST_DIR_DEFAULT)
    result["registration_absent_after"] = not manifest.exists()
    result["finished_utc"] = datetime.now(timezone.utc).isoformat()
    (OUT / "verify-grant.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
