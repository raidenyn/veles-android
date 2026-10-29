"""OTP-23 Mac-resume extension-load probe — diagnostics only, not acceptance evidence.

Exercises the UNCHANGED valid real driver flow exactly as scenario_matrix.Session.start does
(macos_background_support.ChromeRun with the validated registered manifest copied byte-for-byte
into a disposable profile, `Extensions.loadUnpacked` via CDP, connector page, `otp23.connect`,
`otp23.hello`), minus BLE discovery. Every input is the staged driver's own code path; this file
adds no alternate loader. If the load step fails, the raw traceback + Chrome/browser state are
printed and preserved as the setup blocker (no causality claim, no retries with other loaders).
"""

import datetime
import json
import pathlib
import platform
import sys
import traceback
import time

WORK = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(WORK / "scripts"))

import macos_poc as mp  # noqa: E402  (unchanged driver helper)
from macos_background_support import ChromeRun, registered_manifest  # noqa: E402
from macos_install import read_identity  # noqa: E402

OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else WORK / "evidence" / "ext-load-diag.json"


def main():
    identity = read_identity()
    record = {
        "what": "OTP-23 Mac-resume extension load + host-ready probe (unchanged driver flow, diagnostics)",
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "identity": identity,
        "manifest": str(registered_manifest()),
        "manifest_sha256": mp.file_sha256(registered_manifest()),
        "chrome_path": mp.DEFAULT_CHROME,
        "python": sys.version.split()[0],
        "os": platform.platform(),
        "context": mp.context_probe(),
    }
    chrome = None
    try:
        chrome = ChromeRun()
        chrome_version = chrome.cdp.call("Browser.getVersion")
        record["chrome_version"] = chrome_version.get("product")
        record["chrome_user_agent"] = chrome_version.get("userAgent")
        record["profile"] = chrome.profile.as_posix().replace(str(pathlib.Path.home()), "%HOME%")
        record["cdp_port"] = chrome.port
        argv_check = mp.chrome_arguments(chrome.profile.as_posix(), mp.DEFAULT_CHROME)
        record["predicted_chrome_argv"] = [a.replace(str(pathlib.Path.home()), "%HOME%")
                                           for a in argv_check]
        t0 = time.monotonic()
        ext = chrome.load_extension()
        record["load_unpacked_seconds"] = round(time.monotonic() - t0, 3)
        record["extension_id"] = ext["id"]
        assert ext["id"] == identity["extension_id"], \
            f"extension id drifted: {ext['id']!r} != {identity['extension_id']!r}"
        target, session = chrome.open_page(f"chrome-extension://{identity['extension_id']}/connector.html")
        record["connector_target"] = target
        otp23_seen = None
        for _ in range(50):
            if chrome.js(session, "typeof window.otp23 === 'object'"):
                otp23_seen = time.monotonic()
                break
            time.sleep(0.1)
        record["otp23_object_seconds"] = round(otp23_seen - t0, 3) if otp23_seen else None
        assert otp23_seen, "connector page never defined otp23"
        chrome.cdp.call("Runtime.evaluate", {"expression": f"otp23.connect({json.dumps(mp.HOST_NAME)}); 1"},
                        session=session)
        ready = chrome.cdp.call("Runtime.evaluate",
                                {"expression": "otp23.hello()", "awaitPromise": True,
                                 "returnByValue": True}, session=session, timeout=40)
        record["hello"] = ready.get("result", {}).get("value")
        assert (record["hello"] or {}).get("type") == "ready", "hello did not return ready"
        record["result"] = "PASS"
    except Exception:
        record["result"] = "FAIL"
        record["failure"] = traceback.format_exc()
    finally:
        if chrome is not None:
            problems = chrome.close()
            record["chrome_close"] = str(problems) if problems else "clean"
        record["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(json.dumps({k: record[k] for k in
                          ("result", "chrome_version", "extension_id", "connection_error") if k in record}))
        if record["result"] != "PASS":
            print(record["failure"])
            sys.exit(1)


if __name__ == "__main__":
    main()