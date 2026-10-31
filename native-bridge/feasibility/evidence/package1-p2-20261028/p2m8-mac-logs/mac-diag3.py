import sys, pathlib, tempfile, time
sys.path.insert(0, "/Users/raiden/homepc/work/otp23-p2/scripts")
import macos_background_support as support, macos_poc as mp
# minimal trivial extension: manifest.json only (version 3, no permissions)
tmp = pathlib.Path(tempfile.mkdtemp())
(tmp / "manifest.json").write_text('{"manifest_version":3,"name":"probe-min","version":"1.0"}')
run = support.ChromeRun(extra_env={"VELES_OTP23_TRACE": "/tmp/otp23-diag3-trace.log"})
print("port:", run.port, "version:", run.cdp.call("Browser.getVersion", {}).get("product"))
for label, path in (("minimal-extension", tmp), ("real-extension", mp.EXT_DIR)):
    try:
        out = run.cdp.call("Extensions.loadUnpacked", {"path": str(path)})
        print(label, "ok:", out)
    except Exception as e:
        print(label, "failed:", type(e).__name__, str(e)[:100])
        alive = run.cdp.call("Browser.getVersion", {}) if False else None
        try:
            print(label, "getVersion after:", run.cdp.call("Browser.getVersion", {}).get("product"))
        except Exception as e2:
            print(label, "browser died:", type(e2).__name__)
            break
try:
    run.close()
except Exception as e:
    print("close:", type(e).__name__)
