"""Minimal stdlib WebSocket + DevTools-protocol client, shared by the macOS OTP-23 PoC driver.

Verbatim stdlib equivalents of chrome_validate.py's WebSocket/Cdp classes (that module imports
Windows-only machinery, so the macOS side carries its own copy). Loopback-only usage; text
frames, masked client->server writes, ping frames ignored as Chrome tolerates.
"""

import base64
import json
import os
import socket
import struct
import time


class WebSocket:
    def __init__(self, host, port, path):
        self.sock = socket.create_connection((host, port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            response += chunk
        head, _, self.buffer = response.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError(head.decode(errors="replace"))

    def _read(self, n):
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def send(self, text):
        payload = text.encode()
        header = bytearray([0x81])
        if len(payload) < 126:
            header.append(0x80 | len(payload))
        elif len(payload) < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", len(payload))
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", len(payload))
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def recv(self, timeout):
        self.sock.settimeout(timeout)
        message = b""
        while True:
            b0, b1 = self._read(2)
            length = b1 & 0x7F
            if length == 126:
                (length,) = struct.unpack(">H", self._read(2))
            elif length == 127:
                (length,) = struct.unpack(">Q", self._read(8))
            payload = self._read(length)
            opcode = b0 & 0x0F
            if opcode == 8:
                raise ConnectionError("websocket close frame")
            if opcode == 9:
                continue  # ping: Chrome does not require a pong for DevTools
            message += payload
            if b0 & 0x80:
                return message.decode()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Cdp:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0
        self.events = []

    def call(self, method, params=None, session=None, timeout=60):
        self.next_id += 1
        message = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self.ws.send(json.dumps(message))
        deadline = time.time() + timeout
        while True:
            remaining = max(0.1, deadline - time.time())
            reply = json.loads(self.ws.recv(remaining))
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply.get("result", {})
            self.events.append(reply)
            if time.time() > deadline:
                raise TimeoutError(method)