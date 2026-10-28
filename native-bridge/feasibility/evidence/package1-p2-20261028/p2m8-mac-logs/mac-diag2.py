import sys, subprocess, json, urllib.request, time
sys.path.insert(0, "/Users/raiden/homepc/work/otp23-p2/scripts")
import macos_background_support as support
run = support.ChromeRun(extra_env={"VELES_OTP23_TRACE": "/tmp/otp23-diag2-trace.log"})
print("port:", run.port)
def http_list():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{run.port}/json/list", timeout=5) as r:
            return [(t.get("type"), t.get("title")) for t in json.load(r)]
    except Exception as e:
        return f"http list failed: {type(e).__name__} {e}"
print("targets before:", http_list())
try:
    out = run.cdp.call("Extensions.loadUnpacked", {"path": str(support.mp.EXT_DIR)})
    print("loadUnpacked ok:", out)
except Exception as e:
    print("loadUnpacked failed:", type(e).__name__, str(e)[:120])
time.sleep(1)
print("targets after:", http_list())
try:
    print("getVersion after:", run.cdp.call("Browser.getVersion", {}).get("product"))
except Exception as e:
    print("getVersion after failed:", type(e).__name__, str(e)[:120])
alive = subprocess.run(["pgrep", "-f", f"--user-data-dir=.*veles-otp23.*"], capture_output=True, text=True)
print("chrome processes for our profile:", alive.stdout.strip().count("\n") + (1 if alive.stdout.strip() else 0))
try:
    run.close()
except Exception as e:
    print("close failed:", type(e).__name__, e)
