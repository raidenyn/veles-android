import sys, pathlib, traceback, time
sys.path.insert(0, 'scripts')
import chrome_validate as cv
for attempt in (1, 2):
    try:
        chrome = cv.ChromeRun({"VELES_OTP23_TRACE": r"C:\Users\yuriy\otp23-probe\package1-p1\chrome-debug-trace.log"}, medium=True)
        print(f"attempt {attempt}: OK version={chrome.version.get('product')} port={chrome.port}")
        chrome.close()
        break
    except Exception:
        traceback.print_exc()
        time.sleep(3)
