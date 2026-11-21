"""Host-side tests for the watch-pipeline firmware over a (remote) serial port.

WATCH_PORT is any pyserial URL: on the VPS `$(homepc watch url)`
(rfc2217://<home-pc>:4000?ign_set_control), or a local port such as COM3.
"""

import json
import os
import time

import pytest
import serial

REPLY_PREFIX = "@@"


class Watch:
    def __init__(self, port):
        self.port = port

    def cmd(self, payload, timeout=15):
        """Send one JSON command, return the parsed `@@` reply."""
        self.port.reset_input_buffer()
        self.port.write(json.dumps(payload).encode() + b"\n")
        deadline = time.monotonic() + timeout
        buffer = b""
        while time.monotonic() < deadline:
            buffer += self.port.read(self.port.in_waiting or 1)
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith(REPLY_PREFIX):
                    return json.loads(line[len(REPLY_PREFIX):])
        raise TimeoutError(f"no {REPLY_PREFIX} reply to {payload} within {timeout}s")


@pytest.fixture(scope="session")
def watch():
    url = os.environ.get("WATCH_PORT")
    if not url:
        pytest.skip("set WATCH_PORT (on the VPS: export WATCH_PORT=$(homepc watch url))")
    with serial.serial_for_url(url, baudrate=115200, timeout=0.2) as port:
        yield Watch(port)


@pytest.fixture(scope="session")
def peer_uuid():
    """Service UUID of a phone running the Veles BLE spike service, if any."""
    return os.environ.get("WATCH_PEER_UUID")
