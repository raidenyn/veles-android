"""Chrome-owned lifecycle plumbing: minimal CDP client and a launcher that only ever uses a disposable profile.

The WebSocket/CDP client is a stdlib port of the reviewed OTP-23 client (loopback only). The launcher differs in
what it REFUSES: any profile that is not a fresh empty directory inside this run's root, any Chrome path that is
not an explicit existing file, and any inherited trace/diagnostic environment (the production host has none).
"""
import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import time
from pathlib import Path

from .errors import Blocked, Refused
from .registration import ensure_disposable_profile

FORBIDDEN_ENV_PREFIXES = ("VELES_", "RUST_")  # no trace sink, diagnostic or backtrace switch may reach the host


class WebSocket:
    def __init__(self, host, port, path, connect=socket.create_connection):
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise Refused("DevTools is loopback only")
        self.sock = connect((host, port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
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
            header += bytes([0x80 | 126]) + struct.pack(">H", len(payload))
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", len(payload))
        mask = os.urandom(4)
        self.sock.sendall(bytes(header) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

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
                continue
            message += payload
            if b0 & 0x80:
                return message.decode()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Cdp:
    def __init__(self, ws, clock=time.time):
        self.ws, self.next_id, self.events, self.clock = ws, 0, [], clock

    def call(self, method, params=None, session=None, timeout=60):
        self.next_id += 1
        message = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self.ws.send(json.dumps(message))
        deadline = self.clock() + timeout
        while True:
            reply = json.loads(self.ws.recv(max(0.1, deadline - self.clock())))
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply.get("result", {})
            self.events.append(reply)
            if self.clock() > deadline:
                raise TimeoutError(method)


def clean_env(base):
    """Environment for Chrome: the caller's, minus anything that could switch on a host diagnostic."""
    return {k: v for k, v in base.items() if not k.startswith(FORBIDDEN_ENV_PREFIXES)}


def launch_args(chrome, profile):
    return [str(chrome), f"--user-data-dir={profile}", "--remote-debugging-port=0", "--enable-unsafe-extension-debugging",
            "--use-mock-keychain", "--no-first-run", "--no-default-browser-check", "--disable-sync", "--window-size=1000,700", "about:blank"]


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ChromeSession:
    """One Chrome process on one disposable profile. `popen`, `connect` and `sleep` are injectable for tests."""

    def __init__(self, chrome, run_root, home, env, popen=subprocess.Popen, make_cdp=None, sleep=time.sleep, polls=300, before_launch=None):
        chrome = Path(chrome)
        if not chrome.is_absolute() or chrome.is_symlink() or not chrome.is_file():
            raise Refused(f"{chrome}: an explicit absolute path to a real Chrome executable is required")
        self.chrome, self.run_root, self.home = chrome, Path(run_root), home
        self.env, self.popen, self.sleep, self.polls = clean_env(env), popen, sleep, polls
        self.make_cdp = make_cdp or (lambda port, path: Cdp(WebSocket("127.0.0.1", port, path)))
        self.before_launch = before_launch
        self.profile = None
        self.proc = None
        self.cdp = None
        self.args = None

    def start(self):
        self.profile = self.run_root / "chrome-profile"
        self.profile.mkdir(mode=0o700)
        ensure_disposable_profile(self.profile, self.run_root, self.home)
        if self.before_launch is not None:
            self.before_launch(self.profile)  # e.g. place the exact-origin manifest inside the fresh profile
        self.args = launch_args(self.chrome, self.profile)
        self.proc = self.popen(self.args, env=self.env)
        port_file = self.profile / "DevToolsActivePort"
        for _ in range(self.polls):
            if port_file.exists() and len(port_file.read_text().splitlines()) >= 2:
                break
            if self.proc.poll() is not None:
                raise Blocked(f"Chrome exited ({self.proc.poll()}) before publishing DevToolsActivePort")
            self.sleep(0.1)
        else:
            self.kill()
            raise Blocked("Chrome did not publish DevToolsActivePort")
        port, path = port_file.read_text().splitlines()[:2]
        self.cdp = self.make_cdp(int(port), path)
        self.version = self.cdp.call("Browser.getVersion")
        return self.version

    def provenance(self):
        return {"path": str(self.chrome), "sha256": sha256_file(self.chrome),
                "launch_args_sha256": hashlib.sha256("\0".join(self.args).encode()).hexdigest(),
                "version": self.version.get("product"), "profile": {"path": str(self.profile), "disposable": True,
                                                                     "is_default_profile": False}}

    def load_extension(self, ext_dir):
        return self.cdp.call("Extensions.loadUnpacked", {"path": str(ext_dir)})

    def open_page(self, url):
        target = self.cdp.call("Target.createTarget", {"url": url})["targetId"]
        session = self.cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        self.cdp.call("Runtime.enable", session=session)
        return target, session

    def js(self, session, expression, timeout=60):
        reply = self.cdp.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True},
                              session=session, timeout=timeout)
        if "exceptionDetails" in reply:
            raise RuntimeError(json.dumps(reply["exceptionDetails"])[:500])
        return reply["result"].get("value")

    def close_tab(self, target):
        return self.cdp.call("Target.closeTarget", {"targetId": target})

    def browser_close(self):
        """Chrome-owned pipe closure: Chrome itself exits and closes every Native Messaging pipe."""
        try:
            self.cdp.call("Browser.close", timeout=10)
        except (ConnectionError, TimeoutError, OSError):
            pass  # Chrome may drop the socket before answering; the process exit is what matters

    def kill(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()

    def dispose(self, rmtree=shutil.rmtree):
        """Always: close, reap, delete ONLY the disposable profile this session created."""
        if self.cdp is not None:
            self.browser_close()
        if self.proc is not None:
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=15)
        if self.profile is not None and self.run_root in self.profile.parents and self.profile.name == "chrome-profile":
            for _ in range(20):
                try:
                    rmtree(self.profile)
                    return
                except FileNotFoundError:
                    return
                except OSError:
                    self.sleep(0.5)
            raise OSError(f"could not remove the disposable profile {self.profile}")
