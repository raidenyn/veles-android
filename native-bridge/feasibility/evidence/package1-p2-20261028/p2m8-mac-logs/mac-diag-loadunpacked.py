import sys, pathlib, traceback
sys.path.insert(0, "/Users/raiden/homepc/work/otp23-p2/scripts")
import macos_background_support as support, macos_poc as mp
try:
    run = support.ChromeRun(extra_env={"VELES_OTP23_TRACE": "/tmp/otp23-diag-trace.log"})
    print("chrome launched, cdp port:", getattr(run, "port", None))
    try:
        version = run.cdp.call("Browser.getVersion", {})
        print("version:", (version or {}).get("product"))
    except Exception as e:
        print("getVersion failed:", type(e).__name__, e)
    try:
        out = run.load_extension()
        print("extension:", out)
    except Exception as e:
        print("loadUnpacked failed:", type(e).__name__, e)
    try:
        run.close()
    except Exception as e:
        print("close failed:", type(e).__name__, e)
except Exception:
    traceback.print_exc()
