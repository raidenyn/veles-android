"""Real Chrome-owned host scan against deliberately controlled Windows radio state."""
import argparse
import hashlib
import json
import pathlib
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))
import chrome_validate as cv

args = argparse.ArgumentParser()
args.add_argument('--phase', required=True)
phase = args.parse_args().phase
assert phase in ('baseline', 'radio-off', 'adapter-absent', 'adapter-absent-v2', 'restored')
OUT = ROOT / 'capture'
OUT.mkdir(exist_ok=True)
record = {'phase': phase, 'started_utc': datetime.now(timezone.utc).isoformat(), 'operator': 'OpenCode parent openai/gpt-6.1-sol'}
chrome = None
try:
    binary = ROOT / 'host-registration/plain/otp23-host.exe'
    record['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert record['binary_sha256'] == '8521de4d58bdba0858e717e3dca3bcc0850b272ba3c1544bda158afb9e60ae33'
    record['operator_integrity'] = cv.integrity_of(__import__('os').getpid())
    chrome = cv.ChromeRun({'VELES_OTP23_TRACE': str(OUT / (phase + '.trace.log'))}, medium=True)
    record['chrome'] = {'pid': chrome.proc.pid, 'version': chrome.version, 'integrity': cv.integrity_of(chrome.proc.pid)}
    extension = chrome.load_extension()['id']
    assert extension == 'gmekhgkipjkikmaadaajljinkgfielei'
    _, session = chrome.open_page('chrome-extension://' + extension + '/connector.html')
    for _ in range(100):
        if chrome.js(session, "typeof window.otp23 === 'object'"):
            break
        time.sleep(.1)
    chrome.js(session, 'otp23.connect("me.nagaev.veles.otp23.plain"); 1')
    record['hello'] = chrome.js(session, 'otp23.hello()')
    record['host_processes'] = cv.host_processes('otp23-host.exe')
    record['scan'] = chrome.js(session, 'otp23.startScan(' + json.dumps(cv.VELES_SERVICE) + ')')
    scan = record['scan']
    if scan.get('type') == 'scan_state' and scan.get('status') == 'started':
        record['stop'] = chrome.js(session, 'otp23.stopScan(' + json.dumps(scan['scan_handle']) + ')')
    record['shutdown'] = chrome.js(session, 'otp23.shutdown()')
    record['capture_status'] = 'CAPTURED_REQUIRES_ADJUDICATION'
except Exception as error:
    record['capture_status'] = 'ERROR'
    record['exception'] = {'type': type(error).__name__, 'message': str(error)}
finally:
    if chrome:
        chrome.close()
    record['finished_utc'] = datetime.now(timezone.utc).isoformat()
    (OUT / (phase + '.json')).write_text(json.dumps(record, indent=2) + '\n')
