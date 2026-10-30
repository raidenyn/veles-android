"""Live-link duplicate callback experiment. The phone injection is explicitly controller driven."""
import base64
import hashlib
import json
import pathlib
import sys
import time
import uuid
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))
import chrome_validate as cv

OUT = ROOT / 'capture'
record = {'kind': 'a01-m3', 'run_id': 'a01-m3-20261030', 'started_utc': datetime.now(timezone.utc).isoformat(),
          'injection_scope': 'Controller invokes the real Android callback with an already-connected real BluetoothDevice; not a naturally duplicated OS event',
          'exchanges': []}
path = OUT / 'a01-m3.json'
chrome = None

def save(stage):
    record['stage'] = stage
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, indent=2) + '\n')
    temporary.replace(path)

def fixture(size, seed):
    data = bytearray((i + seed) & 255 for i in range(size))
    data[:4] = b'VEL\x01'
    return bytes(data)

try:
    binary = ROOT / 'host-registration/plain/otp23-host.exe'
    record['host_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert record['host_sha256'] == '8521de4d58bdba0858e717e3dca3bcc0850b272ba3c1544bda158afb9e60ae33'
    chrome = cv.ChromeRun({'VELES_OTP23_TRACE': str(OUT / 'a01-m3.trace.log')}, medium=True)
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
    scan = chrome.js(session, 'otp23.startScan(' + json.dumps(cv.VELES_SERVICE) + ')')
    record['scan'] = scan
    assert scan.get('status') == 'started'
    devices = []
    deadline = time.monotonic() + 30
    while not devices and time.monotonic() < deadline:
        devices += [e['message'] for e in chrome.js(session, 'otp23.drainEvents()') if e.get('message', {}).get('type') == 'device_found']
        time.sleep(.1)
    assert len(devices) == 1, 'Expected exactly one own-service advertiser'
    connected = chrome.js(session, 'otp23.connectDevice(' + json.dumps({'device_handle': devices[0]['device_handle']}) + ')')
    record['connected'] = connected
    assert connected.get('type') == 'connected'
    handle = connected['connection_handle']

    def exchange(size, seed, phase):
        data = fixture(size, seed)
        expected = data[:3] + b'\x02' + data[4:]
        reply = chrome.js(session, 'otp23.request("send",' + json.dumps({'connection_handle': handle, 'record_base64': base64.b64encode(data).decode()}) + ')')
        assert reply.get('type') == 'sent', reply
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            for event in chrome.js(session, 'otp23.drainEvents()'):
                message = event.get('message', {})
                if message.get('type') == 'disconnected':
                    raise RuntimeError('Live peer disconnected during experiment')
                if message.get('type') == 'message':
                    actual = base64.b64decode(message['record_base64'], validate=True)
                    assert message['connection_handle'] == handle and actual == expected
                    record['exchanges'].append({'phase': phase, 'connection_handle': handle, 'bytes': size,
                        'input_sha256': hashlib.sha256(data).hexdigest(), 'expected_sha256': hashlib.sha256(expected).hexdigest(),
                        'received_sha256': hashlib.sha256(actual).hexdigest(), 'sent_reply': reply})
                    save(phase)
                    return
            time.sleep(.05)
        raise TimeoutError('No complete byte-exact echo')

    exchange(16, 17, 'proof_before')
    exchange(4096, 31, 'large_before')
    nonce = uuid.uuid4().hex
    record['nonce'] = nonce
    save('await_duplicate')
    deadline = time.monotonic() + 180
    while True:
        try:
            ack = json.loads((OUT / 'a01-continue.json').read_text())
        except (OSError, ValueError):
            ack = None
        if isinstance(ack, dict) and ack.get('nonce') == nonce and ack.get('phone_ack_verified') is True:
            record['controller_ack'] = ack
            break
        if time.monotonic() >= deadline:
            raise TimeoutError('Duplicate injection handshake not completed')
        time.sleep(.25)
    exchange(4096, 53, 'large_after_duplicate')
    record['disconnect'] = chrome.js(session, 'otp23.disconnectDevice(' + json.dumps(handle) + ')')
    record['stop'] = chrome.js(session, 'otp23.stopScan(' + json.dumps(scan['scan_handle']) + ')')
    record['shutdown'] = chrome.js(session, 'otp23.shutdown()')
    record['capture_status'] = 'PASS'
except Exception as error:
    record['capture_status'] = 'FAIL'
    record['error'] = {'type': type(error).__name__, 'message': str(error)}
finally:
    if chrome:
        chrome.close()
    record['finished_utc'] = datetime.now(timezone.utc).isoformat()
    save('finished')
