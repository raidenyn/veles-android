"""Observe actual native connect results before/during/after exclusive GATT-service access."""
import argparse
import base64
import hashlib
import json
import pathlib
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))
import chrome_validate as cv

parser = argparse.ArgumentParser()
parser.add_argument('--phase', choices=['baseline', 'baseline-v2', 'held', 'recovered'], required=True)
phase = parser.parse_args().phase
OUT = ROOT / 'capture'
record = {'phase': phase, 'started_utc': datetime.now(timezone.utc).isoformat()}
chrome = None
try:
    binary = ROOT / 'host-registration/plain/otp23-host.exe'
    record['host_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert record['host_sha256'] == '8521de4d58bdba0858e717e3dca3bcc0850b272ba3c1544bda158afb9e60ae33'
    chrome = cv.ChromeRun({'VELES_OTP23_TRACE': str(OUT / ('access-' + phase + '.trace.log'))}, medium=True)
    record['chrome'] = {'pid': chrome.proc.pid, 'version': chrome.version, 'integrity': cv.integrity_of(chrome.proc.pid)}
    ext = chrome.load_extension()['id']
    assert ext == 'gmekhgkipjkikmaadaajljinkgfielei'
    _, session = chrome.open_page(f'chrome-extension://{ext}/connector.html')
    for _ in range(100):
        if chrome.js(session, "typeof window.otp23 === 'object'"):
            break
        time.sleep(.1)
    chrome.js(session, 'otp23.connect("me.nagaev.veles.otp23.plain"); 1')
    record['hello'] = chrome.js(session, 'otp23.hello()')
    assert record['hello'].get('type') == 'ready'
    scan = None
    if phase.startswith('baseline'):
        scan = chrome.js(session, 'otp23.startScan(' + json.dumps(cv.VELES_SERVICE) + ')')
        assert scan.get('status') == 'started'
        devices = []
        deadline = time.monotonic() + 30
        while not devices and time.monotonic() < deadline:
            devices += [e['message'] for e in chrome.js(session, 'otp23.drainEvents()') if e.get('message', {}).get('type') == 'device_found']
            time.sleep(.1)
        assert len(devices) == 1
        target = {'device_handle': devices[0]['device_handle']}
    else:
        target = json.loads((OUT / 'w10-access-target.private.json').read_text())
    connected = chrome.js(session, 'otp23.connectDevice(' + json.dumps(target) + ')')
    record['connect_reply'] = {k: v for k, v in connected.items() if k != 'platform_hint'}
    if connected.get('type') == 'connected':
        hint = connected['platform_hint']
        record['target_hint_sha256'] = hashlib.sha256(hint.encode()).hexdigest()
        if phase.startswith('baseline'):
            (OUT / 'w10-access-target.private.json').write_text(json.dumps({'platform_hint': hint}) + '\n')
        handle = connected['connection_handle']
        data = b'VEL\x01'
        sent = chrome.js(session, 'otp23.request("send",' + json.dumps({'connection_handle': handle, 'record_base64': base64.b64encode(data).decode()}) + ')')
        record['sent'] = sent
        assert sent.get('type') == 'sent'
        deadline = time.monotonic() + 10
        matched = False
        while not matched and time.monotonic() < deadline:
            for event in chrome.js(session, 'otp23.drainEvents()'):
                message = event.get('message', {})
                if message.get('type') == 'message':
                    received = base64.b64decode(message['record_base64'], validate=True)
                    assert message['connection_handle'] == handle and received == b'VEL\x02'
                    record['echo'] = {'bytes': len(received), 'sha256': hashlib.sha256(received).hexdigest(), 'byte_exact': True}
                    matched = True
            time.sleep(.05)
        assert matched
        record['disconnect'] = chrome.js(session, 'otp23.disconnectDevice(' + json.dumps(handle) + ')')
    if scan:
        record['stop'] = chrome.js(session, 'otp23.stopScan(' + json.dumps(scan['scan_handle']) + ')')
    record['shutdown'] = chrome.js(session, 'otp23.shutdown()')
    record['capture_status'] = 'CAPTURED_REQUIRES_ADJUDICATION'
except Exception as error:
    record['capture_status'] = 'ERROR'
    record['error'] = {'type': type(error).__name__, 'message': str(error)}
finally:
    if chrome:
        chrome.close()
    record['finished_utc'] = datetime.now(timezone.utc).isoformat()
    (OUT / ('access-' + phase + '.json')).write_text(json.dumps(record, indent=2) + '\n')
