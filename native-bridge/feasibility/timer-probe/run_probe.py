#!/usr/bin/python3
"""Targeted Native Messaging timer probe (macOS or Windows, Python 3.9+, stdlib). No Bluetooth, no phones.

Launches its OWN Chrome in the GUI session (macOS ``open -na``; Windows a direct child, so run it from an interactive
logon) with an isolated ``--user-data-dir``, loads the probe extension over CDP, registers a non-Bluetooth host under a
unique name (macOS: that profile's NativeMessagingHosts only; Windows: a per-user HKCU key for the unique probe name, removed
again at the end), opens three tabs (port tab, port-less control tab, ordinary tab activated last), then CLOSES EVERY CDP
CLIENT for the idle interval and reads the in-page logs afterwards.

    python3 run_probe.py --out-dir OUT [--hidden-s 540] [--debug-port 9333]
"""
import argparse
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import macos_cdp  # noqa: E402
import timer_probe_analysis as tpa  # noqa: E402

HOST_NAME = "me.nagaev.veles.timerprobe"
CHROME_APP = "/Applications/Google Chrome.app"
WIN_CHROME = [os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
              os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
              os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")]
IS_WIN = sys.platform == "win32"
REG_KEY = "Software\\Google\\Chrome\\NativeMessagingHosts\\" + HOST_NAME


CHROME_PROC = None


def http_json(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as reply:
        return json.load(reply)


def connect(port):
    version = http_json(port, "/json/version")
    path = version["webSocketDebuggerUrl"].split(str(port), 1)[1]
    return macos_cdp.Cdp(macos_cdp.WebSocket("127.0.0.1", port, path)), version


def page_eval(cdp, target_id, expression):
    session = cdp.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})["sessionId"]
    try:
        out = cdp.call("Runtime.evaluate", {"expression": expression, "returnByValue": True}, session=session)
        return out["result"].get("value")
    finally:
        cdp.call("Target.detachFromTarget", {"sessionId": session})


def browser_pid(profile):
    if IS_WIN:
        return CHROME_PROC.pid if CHROME_PROC is not None and CHROME_PROC.poll() is None else None
    out = subprocess.run(["pgrep", "-f", f"--user-data-dir={profile}"], capture_output=True, text=True).stdout.split()
    for pid in out:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", pid], capture_output=True, text=True).stdout
        if "--type=" not in cmd:
            return int(pid)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--hidden-s", type=int, default=540)
    ap.add_argument("--debug-port", type=int, default=9333)
    args = ap.parse_args()
    out = pathlib.Path(args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    profile = out / "profile"
    (profile / "NativeMessagingHosts").mkdir(parents=True)
    host = HERE / "host.py"
    host.chmod(0o755)
    host_log = HERE / "host-send.log"
    host_log.unlink(missing_ok=True)

    # Windows: port 0 and DevToolsActivePort, exactly as the harness's ChromeRun does (a fixed port never answered there).
    flags = [f"--user-data-dir={profile}", f"--remote-debugging-port={0 if IS_WIN else args.debug_port}",
             "--enable-unsafe-extension-debugging", "--no-first-run", "--no-default-browser-check", "about:blank"]
    global CHROME_PROC
    if IS_WIN:
        chrome_exe = next((c for c in WIN_CHROME if os.path.exists(c)), None)
        if chrome_exe is None:
            raise SystemExit("chrome.exe not found")
        CHROME_PROC = subprocess.Popen([chrome_exe] + flags)
    else:
        subprocess.run(["open", "-na", CHROME_APP, "--args"] + flags, check=True)
    if IS_WIN:
        port_file = profile / "DevToolsActivePort"
        for _ in range(300):
            if port_file.exists() and len(port_file.read_text().splitlines()) >= 2:
                args.debug_port = int(port_file.read_text().splitlines()[0])
                break
            time.sleep(0.1)
        else:
            CHROME_PROC.terminate()
            raise SystemExit("Chrome did not publish DevToolsActivePort")
    for _ in range(40):
        try:
            cdp, version = connect(args.debug_port)
            break
        except OSError:
            time.sleep(1)
    else:
        raise SystemExit("dedicated Chrome did not open CDP")
    pid = browser_pid(profile)
    result = {"chrome_version_from_cdp": version.get("Browser"), "user_agent": version.get("User-Agent"),
              "profile": str(profile), "browser_pid": pid, "hidden_s": args.hidden_s, "debug_port": args.debug_port,
              "platform": sys.platform, "launch_flags_as_passed": flags,
              "launch_command_flags": (subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                                                      text=True).stdout.strip() if pid and not IS_WIN else None)}
    try:
        ext = cdp.call("Extensions.loadUnpacked", {"path": str(HERE / "extension")})["id"]
        result["extension_id"] = ext
        host_path = host
        if IS_WIN:  # Chrome cannot run a .py file; a generated .bat launches it with this interpreter
            host_path = out / "timerprobe-host.bat"
            host_path.write_text(f'@echo off\r\n"{sys.executable}" "{host}"\r\n')
        manifest = {"name": HOST_NAME, "description": "OTP-23 timer probe host (no Bluetooth)", "path": str(host_path),
                    "type": "stdio", "allowed_origins": [f"chrome-extension://{ext}/"]}
        manifest_file = profile / "NativeMessagingHosts" / f"{HOST_NAME}.json"
        manifest_file.write_text(json.dumps(manifest, indent=2))
        if IS_WIN:
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_file))
            result["registry_key_created"] = "HKCU\\" + REG_KEY
        base = f"chrome-extension://{ext}/probe.html"
        targets = {}
        for label, url in (("port", f"{base}?mode=port"), ("control", f"{base}?mode=control")):
            # Windows: a plain createTarget left both probe tabs visible (first Windows run, retained); ask for background tabs
            # in the same window. macOS kept the original call so its retained run stays reproducible by this code.
            params = {"url": url, "newWindow": False, "background": True} if IS_WIN else {"url": url}
            targets[label] = cdp.call("Target.createTarget", params)["targetId"]
        targets["ordinary"] = cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        cdp.call("Target.activateTarget", {"targetId": targets["ordinary"]})
        hidden_start_epoch_ms = int(time.time() * 1000)
        time.sleep(25)
        probe = "JSON.stringify({vis: document.visibilityState, timers: window.__log.timers.length, messages: window.__log.messages.length})"
        result["setup_check"] = {k: json.loads(page_eval(cdp, targets[k], probe)) for k in ("port", "control")}
        result["hidden_start_epoch_ms"] = hidden_start_epoch_ms
        if any(v["vis"] != "hidden" for v in result["setup_check"].values()):
            result["aborted"] = "probe tabs are not hidden at setup; the idle interval would not test the hidden case"
            raise RuntimeError(result["aborted"])
        result["targets"] = targets
        cdp.ws.close()  # no DevTools client of any kind during the idle interval
        cdp = None
        time.sleep(args.hidden_s)

        cdp, _ = connect(args.debug_port)
        logs = {k: json.loads(page_eval(cdp, targets[k], "JSON.stringify(window.__log)")) for k in ("port", "control")}
        result["read_back_epoch_ms"] = int(time.time() * 1000)
        result["raw_logs"] = logs
        expected = len([ln for ln in host_log.read_text().splitlines() if '"seq"' in ln]) if host_log.exists() else 0
        analysis = {}
        for label, log in logs.items():
            window_start = (hidden_start_epoch_ms - log["started_epoch_ms"]) + 300_000
            analysis[label] = {"timers": tpa.summarize_timers(log["timers"], window_start)}
        analysis["port"]["messages"] = tpa.summarize_messages(logs["port"]["messages"], expected)
        result["host_send_log_lines"] = host_log.read_text().splitlines() if host_log.exists() else []
        result["analysis"] = analysis
    finally:
        try:
            if cdp:
                cdp.call("Browser.close")
        except Exception:  # noqa: BLE001 - best effort; fall through to the pid-scoped kill
            pass
        time.sleep(3)
        left = browser_pid(profile)
        if left:
            if IS_WIN:
                CHROME_PROC.terminate()  # our own child only
            else:
                os.kill(left, signal.SIGTERM)  # only the process whose command line carries OUR profile
        if IS_WIN:
            import winreg
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_KEY)  # only the unique probe name
                result["registry_key_removed"] = True
            except OSError:
                result["registry_key_removed"] = False
        (out / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result.get("analysis", {}), indent=2))


if __name__ == "__main__":
    main()
