"""OTP-23 Windows Chrome validation driver (disposable profile, CDP Extensions.loadUnpacked).

Launches stable Chrome with a throwaway --user-data-dir (never the real profile), loads the
unpacked connector extension over the DevTools protocol, then drives window.otp23 in the
connector tab to exercise the registered Native Messaging host: on-demand launch, framing,
scan, error classification and every cleanup path (shutdown, port close, tab close, browser exit).

Usage (host already registered with scripts/register-host.ps1):
    python scripts/chrome_validate.py --flavour plain --out evidence/chrome-plain.json
    python scripts/chrome_validate.py --flavour plain --diag-scan --out evidence/chrome-plain-diag.json
    python scripts/chrome_validate.py --flavour plain --phone --out evidence/chrome-plain-phone.json

Only Python's standard library is used.
"""

import argparse
import base64
import ctypes
import json
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import traceback
from ctypes import wintypes

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
ROOT = pathlib.Path(__file__).resolve().parent.parent
EXT_DIR = ROOT / "extension"
HOSTS = {"plain": "me.nagaev.veles.otp23.plain", "tauri": "me.nagaev.veles.otp23.tauri"}
EXE_NAME = {"plain": "otp23-host.exe", "tauri": "otp23-host-tauri.exe"}
VELES_SERVICE = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51"
USER_DATA_REAL = pathlib.Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"


# ---- minimal WebSocket client (RFC 6455, text frames) -----------------------------------------


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


# ---- Windows process helpers ----------------------------------------------------------------------


def powershell_json(command):
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command + " | ConvertTo-Json -Compress -Depth 4"],
        capture_output=True, text=True, timeout=60,
    ).stdout.strip()
    if not out:
        return []
    value = json.loads(out)
    return value if isinstance(value, list) else [value]


def host_processes(exe_name):
    return powershell_json(
        f"Get-CimInstance Win32_Process -Filter \"Name='{exe_name}'\" | "
        "Select-Object ProcessId,ParentProcessId,CommandLine,ExecutablePath,CreationDate"
    )


def children_of(pid):
    return powershell_json(
        f"Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}' | Select-Object ProcessId,Name"
    )


def ancestry(pid, limit=6):
    """Parent chain (name, command-line prefix) from `pid` upwards, closest parent first."""
    chain = []
    current = pid
    for _ in range(limit):
        rows = powershell_json(
            f"Get-CimInstance Win32_Process -Filter 'ProcessId={current}' | "
            "Select-Object ProcessId,ParentProcessId,Name,CommandLine"
        )
        if not rows:
            break
        row = rows[0]
        if current != pid:
            chain.append({"ProcessId": row["ProcessId"], "Name": row["Name"],
                          "CommandLine": (row["CommandLine"] or "")[:600]})
        current = row["ParentProcessId"]
    return chain


def wrapper_of(pid):
    """Chrome starts the host via `cmd.exe /d /s /c "<host>" <origin> ... < pipe > pipe`."""
    chain = ancestry(pid, limit=2)
    if chain and chain[0]["Name"].lower() == "cmd.exe":
        return chain[0]["ProcessId"]
    return None


def wait_all_gone(pids, timeout=10.0):
    start = time.time()
    for pid in [p for p in pids if p]:
        remaining = max(0.1, timeout - (time.time() - start))
        if wait_gone(pid, remaining) is None:
            return None
    return round(time.time() - start, 3)


def process_alive(pid):
    return bool(powershell_json(f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | Select-Object ProcessId"))


def top_level_windows_of(pid):
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, name, 256)
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            found.append({"hwnd": hwnd, "visible": bool(user32.IsWindowVisible(hwnd)),
                          "class": name.value, "title_len": len(title.value)})
        return True

    user32.EnumWindows(visit, 0)
    return found


def network_endpoints_of(pid):
    return powershell_json(
        f"@(Get-NetTCPConnection -OwningProcess {pid} -ErrorAction SilentlyContinue | Select-Object LocalPort,State) + "
        f"@(Get-NetUDPEndpoint -OwningProcess {pid} -ErrorAction SilentlyContinue | Select-Object LocalPort)"
    )


# ---- OTP-23 package1: connected-phase window/endpoint probe (one machine read per sample) -----------

_IPHLPAPI = None


def _iphlpapi():
    global _IPHLPAPI
    if _IPHLPAPI is None:
        _IPHLPAPI = ctypes.WinDLL("iphlpapi", use_last_error=True)
    return _IPHLPAPI


_ENDPOINTS_CACHE = None


def endpoint_table():
    """One snapshot of every TCP (IPv4+IPv6) and UDP (IPv4+IPv6) endpoint with its owning pid.

    Only ports, owner pids and raw states are recorded (no addresses). A family whose API read finally
    fails stays the string "unavailable" — never an invented empty list (a false zero); per-family
    availability travels with the probe. The bytes are parsed by the pure functions in
    scenario_matrix_core so the parse is unit-tested on every platform."""
    iphlp = _iphlpapi()
    core = sys.modules.get("scenario_matrix_core") or _import_core()
    tables = {"tcp4": "unavailable", "tcp6": "unavailable", "udp4": "unavailable", "udp6": "unavailable"}
    family_api = (("tcp4", 2, core.TCP_TABLE_OWNER_PID_ALL, iphlp.GetExtendedTcpTable),
                  ("tcp6", 23, core.TCP_TABLE_OWNER_PID_ALL, iphlp.GetExtendedTcpTable),
                  ("udp4", 2, core.UDP_TABLE_OWNER_PID, iphlp.GetExtendedUdpTable),
                  ("udp6", 23, core.UDP_TABLE_OWNER_PID, iphlp.GetExtendedUdpTable))
    for family, af, table_class, api in family_api:
        def read_once(size, af=af, table_class=table_class, api=api):
            size_arg = wintypes.ULONG(size)
            err = api(None, ctypes.byref(size_arg), False, af, table_class, 0)
            if err != core.ERROR_INSUFFICIENT_BUFFER:
                return None, size_arg.value, False
            buf = ctypes.create_string_buffer(max(size_arg.value, size))
            err = api(buf, ctypes.byref(size_arg), False, af, table_class, 0)
            if err == 0:
                return buf.raw[:size_arg.value], size_arg.value, False
            if err == core.ERROR_INSUFFICIENT_BUFFER:  # the table grew between the two calls: retry
                return None, size_arg.value, True
            return None, size_arg.value, False

        raw = core.read_family_with_retry(read_once)
        if raw == "unavailable":
            tables[family] = "unavailable"
            continue
        parser = {"tcp4": core.parse_tcp4_table, "tcp6": core.parse_tcp6_table,
                  "udp4": core.parse_udp4_table, "udp6": core.parse_udp6_table}[family]
        try:
            tables[family] = parser(raw)
        except ValueError:
            tables[family] = "unavailable"  # a misparsed table must not look like absence
    return tables


def endpoints_of_pid(pid):
    """Endpoints owned by `pid` from the latest endpoint_table() read: port tuples per family.

    Call endpoint_table() once per sample, then slice per pid — keeps one consistent snapshot and makes
    the Chrome listener control and the host's absence come from the SAME read. A family that failed to
    read stays "unavailable" (never empty)."""
    tables = _ENDPOINTS_CACHE if _ENDPOINTS_CACHE is not None else endpoint_table()
    out = {}
    for family, fields in (("tcp4", ("local_port", "remote_port", "state")),
                           ("tcp6", ("local_port", "remote_port", "state")),
                           ("udp4", ("local_port",)), ("udp6", ("local_port",))):
        rows = tables.get(family)
        if rows == "unavailable":
            out[family] = "unavailable"
        else:
            out[family] = sorted(tuple(row[name] for name in fields)
                                 for row in rows if row["owner"] == pid)
    return out


def invalidate_endpoint_cache():
    global _ENDPOINTS_CACHE
    _ENDPOINTS_CACHE = None


def _import_core():
    import scenario_matrix_core as core_module
    return core_module


def window_inventory(pid):
    """Window census owned by `pid`: top-level (visible/hidden), message-only and child counts.

    EnumWindows skips message-only windows, so they are enumerated separately via FindWindowEx from
    HWND_MESSAGE: the walk covers the full message-only list (cap 65536) and sets `message_only_truncated`
    if the cap ends the walk early — a truncated census is unavailable for that kind, never under-reported.
    Only counts, visibility and class names are recorded (no titles)."""
    user32 = ctypes.windll.user32
    user32.FindWindowExW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    census = {"top_visible": [], "top_hidden": [], "children": 0, "message_only_truncated": False}
    class_of = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def count_children(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            census["children"] += 1
        return True

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            name = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, name, 128)
            class_of[hwnd] = name.value
            (census["top_visible"] if user32.IsWindowVisible(hwnd) else census["top_hidden"]).append(
                {"class": name.value, "hwnd_index": len(class_of)})
        return True

    user32.EnumWindows(visit, 0)
    for hwnd in list(class_of):
        user32.EnumChildWindows(hwnd, count_children, 0)
    message_only = []
    walker = user32.FindWindowExW(wintypes.HWND(-3), wintypes.HWND(0), None, None)
    while walker is not None and len(message_only) < 65536:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(walker, ctypes.byref(owner))
        if owner.value == pid:
            name = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(walker, name, 128)
            message_only.append({"class": name.value})
        walker = user32.FindWindowExW(wintypes.HWND(-3), walker, None, None)
    census["message_only_truncated"] = len(message_only) >= 65536
    return {"top_visible": census["top_visible"], "top_hidden": census["top_hidden"],
            "children": census["children"], "message_only": message_only,
            "message_only_truncated": census["message_only_truncated"]}


_KERNEL32 = None


def session_of(pid):
    session = wintypes.DWORD(0)
    ok = _kernel32().ProcessIdToSessionId(pid, ctypes.byref(session))
    return session.value if ok else None


def _kernel32():
    global _KERNEL32
    if _KERNEL32 is None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
        kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel.ProcessIdToSessionId.restype = wintypes.BOOL
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        _KERNEL32 = kernel
    return _KERNEL32


def pid_alive(pid):
    """True when the process exists (cheap PROCESS_QUERY_LIMITED_INFORMATION open succeeds, or the
    failure is an access-denied for a live process — dead pids fail with ERROR_INVALID_PARAMETER or
    a zero-error path is treated as dead). Fail-closed: only a positive open counts as alive."""
    kernel = _kernel32()
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if handle:
        kernel.CloseHandle(handle)
        return True
    return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: exists, cannot query


def active_console_session():
    return _kernel32().WTSGetActiveConsoleSessionId()


def hygiene_probe(pids, chrome_pid, cdp_port):
    """Connected-phase probe: windows + endpoints + session identity + mandatory positive controls.

    One read per family, so the sample is internally consistent: the same enumeration that will judge the
    host's absence must ALSO see Chrome's visible window and hear Chrome's CDP listener, or the sample is
    explicitly marked unusable by the caller (unavailable is never reported as zero)."""
    kernel = _kernel32()
    chrome_win = window_inventory(chrome_pid)
    tables = endpoint_table()
    chrome_eps = endpoints_of_pid(chrome_pid)
    listener = None
    for family in ("tcp4", "tcp6"):
        rows = chrome_eps[family]
        if rows == "unavailable":
            continue  # the control honestly cannot be asserted from an unread table
        for local_port, remote_port, state in rows:
            if cdp_port is not None and local_port == cdp_port and remote_port == 0:
                listener = {"family": family, "local_port": local_port}
    per_pid = {}
    for pid in pids:
        windows = window_inventory(pid)
        endpoints = endpoints_of_pid(pid)
        per_pid[str(pid)] = {
            "top_visible": len(windows["top_visible"]), "top_hidden": len(windows["top_hidden"]),
            "message_only": ("unavailable" if windows["message_only_truncated"]
                             else len(windows["message_only"])),
            "message_only_truncated": windows["message_only_truncated"],
            "children": windows["children"],
            "top_hidden_classes": sorted({w["class"] for w in windows["top_hidden"]}),
            "message_only_classes": sorted({w["class"] for w in windows["message_only"]}),
            "tcp4": endpoints["tcp4"], "tcp6": endpoints["tcp6"],
            "udp4": endpoints["udp4"], "udp6": endpoints["udp6"],
            "session": session_of(pid), "pid_alive": pid_alive(pid)}
    per_pid[str(chrome_pid)] = {
        "top_visible": len(chrome_win["top_visible"]), "top_hidden": len(chrome_win["top_hidden"]),
        "message_only": len(chrome_win["message_only"]), "children": chrome_win["children"],
        "tcp4": chrome_eps["tcp4"], "tcp6": chrome_eps["tcp6"],
        "udp4": chrome_eps["udp4"], "udp6": chrome_eps["udp6"],
        "session": session_of(chrome_pid), "pid_alive": pid_alive(chrome_pid)}
    host_session = session_of(pids[0]) if pids else None
    return {"mode": "sampled", "per_pid": per_pid,
            "observer": {"pid": os.getpid(), "session": session_of(os.getpid())},
            "active_console_session": kernel.WTSGetActiveConsoleSessionId(),
            "endpoint_families_available": {k: tables[k] != "unavailable" for k in tables},
            "controls": {"chrome_visible_top_level": len(chrome_win["top_visible"]),
                         "chrome_cdp_listener_present": listener is not None,
                         "chrome_cdp_listener": listener}}


def wait_gone(pid, timeout=10.0):
    start = time.time()
    while time.time() - start < timeout:
        if not process_alive(pid):
            return round(time.time() - start, 3)
        time.sleep(0.1)
    return None


# ---- integrity levels and unelevated (medium-integrity) launch ----------------------------------------------

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_adv = ctypes.WinDLL("advapi32", use_last_error=True)
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_u32.GetShellWindow.restype = wintypes.HWND
_adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_adv.DuplicateTokenEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, ctypes.c_int, ctypes.c_int,
                                  ctypes.POINTER(wintypes.HANDLE)]
_adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD)]
_adv.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
_adv.GetSidSubAuthorityCount.argtypes = [wintypes.LPVOID]
_adv.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
_adv.GetSidSubAuthority.argtypes = [wintypes.LPVOID, wintypes.DWORD]
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_adv.CreateRestrictedToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                       wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                                       ctypes.POINTER(wintypes.HANDLE)]
_adv.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID)]
_adv.GetLengthSid.argtypes = [wintypes.LPVOID]
_adv.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
_adv.CreateProcessWithTokenW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPWSTR,
                                         wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR, wintypes.LPVOID,
                                         wintypes.LPVOID]


def integrity_of(pid):
    """Integrity level and elevation of a process token; None if it cannot be queried."""
    handle = _k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    token = wintypes.HANDLE()
    try:
        if not _adv.OpenProcessToken(handle, 0x8, ctypes.byref(token)):  # TOKEN_QUERY
            return None
        size = wintypes.DWORD()
        _adv.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))  # TokenIntegrityLevel
        buffer = ctypes.create_string_buffer(size.value)
        if not _adv.GetTokenInformation(token, 25, buffer, size, ctypes.byref(size)):
            return None
        # TOKEN_MANDATORY_LABEL { SID_AND_ATTRIBUTES { PSID Sid; DWORD Attributes } }
        sid = ctypes.c_void_p.from_buffer(buffer).value
        count = _adv.GetSidSubAuthorityCount(sid)[0]
        rid = _adv.GetSidSubAuthority(sid, count - 1)[0]
        elevation = wintypes.DWORD()
        _adv.GetTokenInformation(token, 20, ctypes.byref(elevation), 4, ctypes.byref(size))  # TokenElevation
        level = {0x1000: "low", 0x2000: "medium", 0x3000: "high", 0x4000: "system"}.get(rid, hex(rid))
        return {"integrity": level, "rid": hex(rid), "token_is_elevated": bool(elevation.value)}
    finally:
        if token:
            _k32.CloseHandle(token)
        _k32.CloseHandle(handle)


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", wintypes.LPVOID),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class MediumProcess:
    """A process of the SAME user started with a UAC-style limited token derived from this
    (elevated) process: LUA restricted token (administrator group disabled, privileges removed) with
    the medium mandatory label, carrying this script's environment and staying in this session. It is
    an unelevated process of that account; it is NOT a separate standard (non-administrator) account."""

    def __init__(self, args, env):
        self.args = list(args)  # the argv actually handed to CreateProcessWithTokenW (strict flag scan)
        own, dup, restricted = wintypes.HANDLE(), wintypes.HANDLE(), wintypes.HANDLE()
        if not _adv.OpenProcessToken(_k32.GetCurrentProcess(), 0x2 | 0x8, ctypes.byref(own)):
            raise OSError(ctypes.get_last_error(), "OpenProcessToken(self)")
        if not _adv.DuplicateTokenEx(own, 0x02000000, None, 2, 1, ctypes.byref(dup)):  # primary copy
            raise OSError(ctypes.get_last_error(), "DuplicateTokenEx")
        sid = wintypes.LPVOID()
        if not _adv.ConvertStringSidToSidW("S-1-16-8192", ctypes.byref(sid)):
            raise OSError(ctypes.get_last_error(), "ConvertStringSidToSid")
        label = _SidAndAttributes(sid, 0x20)  # SE_GROUP_INTEGRITY
        size = ctypes.sizeof(label) + _adv.GetLengthSid(sid)
        if not _adv.SetTokenInformation(dup, 25, ctypes.byref(label), size):
            raise OSError(ctypes.get_last_error(), "SetTokenInformation(IntegrityLevel)")
        if not _adv.CreateRestrictedToken(dup, 0x4, 0, None, 0, None, 0, None, ctypes.byref(restricted)):  # LUA_TOKEN
            raise OSError(ctypes.get_last_error(), "CreateRestrictedToken")
        block = "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0"
        env_buffer = ctypes.create_unicode_buffer(block, len(block))
        startup = _StartupInfo()
        startup.cb = ctypes.sizeof(startup)
        info = _ProcessInfo()
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
        ok = _adv.CreateProcessWithTokenW(restricted, 1, None, command, 0x400, env_buffer, None,
                                          ctypes.byref(startup), ctypes.byref(info))
        if not ok:
            raise OSError(ctypes.get_last_error(), "CreateProcessWithTokenW")
        self.pid = info.dwProcessId
        self.handle = info.hProcess
        _k32.CloseHandle(info.hThread)

    def wait(self, timeout=None):
        millis = 0xFFFFFFFF if timeout is None else int(timeout * 1000)
        if _k32.WaitForSingleObject(self.handle, millis) != 0:
            raise subprocess.TimeoutExpired("chrome", timeout)

    def kill(self):
        _k32.TerminateProcess(self.handle, 1)


# ---- Chrome session -----------------------------------------------------------------------------------


class ChromeRun:
    def __init__(self, extra_env, medium=False):
        self.profile = pathlib.Path(tempfile.mkdtemp(prefix="veles-otp23-profile-"))
        env = os.environ.copy()
        env.update(extra_env)
        self.env = env  # kept for evidence: the environment Chrome (and thus the host) actually received
        args = [
            CHROME, f"--user-data-dir={self.profile}", "--remote-debugging-port=0",
            "--enable-unsafe-extension-debugging", "--no-first-run", "--no-default-browser-check",
            "--disable-sync", "--window-size=1000,700", "about:blank",
        ]
        elevated = bool(integrity_of(os.getpid()).get("token_is_elevated"))
        self.proc = MediumProcess(args, env) if medium and elevated else subprocess.Popen(args, env=env)
        port_file = self.profile / "DevToolsActivePort"
        for _ in range(300):
            if port_file.exists() and len(port_file.read_text().splitlines()) >= 2:
                break
            time.sleep(0.1)
        else:
            self.proc.kill()
            self.proc.wait(timeout=10)
            shutil.rmtree(self.profile, ignore_errors=True)
            raise RuntimeError("Chrome did not publish DevToolsActivePort")
        port, path = port_file.read_text().splitlines()[:2]
        self.port = int(port)
        self.cdp = Cdp(WebSocket("127.0.0.1", self.port, path))
        self.version = self.cdp.call("Browser.getVersion")

    def load_extension(self):
        return self.cdp.call("Extensions.loadUnpacked", {"path": str(EXT_DIR)})

    def open_page(self, url):
        target = self.cdp.call("Target.createTarget", {"url": url})["targetId"]
        session = self.cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        self.cdp.call("Runtime.enable", session=session)
        return target, session

    def js(self, session, expression, timeout=60):
        reply = self.cdp.call(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
            session=session, timeout=timeout,
        )
        if "exceptionDetails" in reply:
            raise RuntimeError(json.dumps(reply["exceptionDetails"])[:500])
        return reply["result"].get("value")

    def close(self):
        try:
            self.cdp.call("Browser.close", timeout=10)
        except Exception:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        for _ in range(20):
            try:
                shutil.rmtree(self.profile)
                break
            except OSError:
                time.sleep(0.5)


# ---- case framework ---------------------------------------------------------------------------------------


class Report:
    def __init__(self):
        self.cases = []

    def case(self, case_id, title, expected, fn):
        record = {"id": case_id, "title": title, "expected": expected}
        started = time.time()
        try:
            actual = fn()
            record["actual"] = actual
            record["result"] = "PASS"
        except AssertionError as error:
            record["actual"] = str(error)
            record["result"] = "FAIL"
        except Exception as error:  # noqa: BLE001 - the harness must keep going and record it
            record["actual"] = f"{type(error).__name__}: {error}"
            record["trace"] = traceback.format_exc(limit=3)
            record["result"] = "ERROR"
        record["seconds"] = round(time.time() - started, 2)
        self.cases.append(record)
        print(f"[{record['result']:5}] {case_id} {title}")
        if record["result"] != "PASS":
            print("        ", str(record["actual"])[:400])
        return record


def scrub(text):
    """Evidence must not embed the local profile path."""
    home = str(pathlib.Path.home())
    return text.replace(home, "%USERPROFILE%").replace(home.replace("\\", "\\\\"), "%USERPROFILE%")


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def host_for_pid(exe, pid):
    return next((p for p in host_processes(exe) if p["ProcessId"] == pid), None)


def new_host_process(exe, before_pids, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        fresh = [p for p in host_processes(exe) if p["ProcessId"] not in before_pids]
        if fresh:
            return fresh
        time.sleep(0.1)
    return []


def run(args):
    host_name = HOSTS[args.flavour]
    exe = EXE_NAME[args.flavour]
    evidence_dir = pathlib.Path(args.out).resolve().parent
    trace_path = evidence_dir / f"host-trace-{args.flavour}{'-diag' if args.diag_scan else ''}{'-phone' if args.phone else ''}{'-medium' if args.medium else ''}.log"
    trace_path.unlink(missing_ok=True)
    extra_env = {"VELES_OTP23_TRACE": str(trace_path)}
    if args.diag_scan:
        extra_env["VELES_OTP23_DIAG_SCAN_ANY"] = "1"

    real_state = USER_DATA_REAL / "Local State"
    real_before = real_state.stat().st_mtime_ns if real_state.exists() else None
    report = Report()
    chrome = ChromeRun(extra_env, medium=args.medium)
    meta = {
        "chrome_version": chrome.version.get("product"),
        "chrome_user_agent": chrome.version.get("userAgent"),
        "chrome_path": CHROME,
        "chrome_browser_pid": chrome.proc.pid,
        "disposable_profile": str(chrome.profile),
        "host_name": host_name,
        "flavour": args.flavour,
        "diag_scan_any": args.diag_scan,
        "driver_integrity": integrity_of(os.getpid()),
        "chrome_integrity": integrity_of(chrome.proc.pid),
        "launch_mode": "limited token derived from elevated driver" if args.medium and integrity_of(os.getpid()).get("token_is_elevated")
        else "inherited from driver; see measured integrity (not proof of a standard non-admin account)",
    }
    print("Chrome", meta["chrome_version"], "pid", chrome.proc.pid, "profile", chrome.profile)

    ext_id = None
    try:
        # ---- CH-01 ---------------------------------------------------------------------------
        def load():
            nonlocal ext_id
            result = chrome.load_extension()
            ext_id = result["id"]
            expected = "gmekhgkipjkikmaadaajljinkgfielei"
            expect(ext_id == expected, f"extension id {ext_id} != fixed {expected}")
            return {"loaded_extension_id": ext_id, "via": "Extensions.loadUnpacked over CDP websocket"}

        loaded = report.case("CH-01", "Load unpacked MV3 extension with fixed key/id", "id equals the fixed id", load)
        if loaded["result"] != "PASS":
            return finish(report, meta, args, chrome, real_state, real_before)
        url = f"chrome-extension://{ext_id}/connector.html"

        def fresh_connector():
            target, session = chrome.open_page(url)
            for _ in range(50):
                if chrome.js(session, "typeof window.otp23 === 'object'"):
                    break
                time.sleep(0.1)
            return target, session

        def connect_hello(session, name=host_name):
            before = {p["ProcessId"] for p in host_processes(exe)}
            started = time.time()
            chrome.js(session, f"otp23.connect({json.dumps(name)}); 1")
            ready = chrome.js(session, "otp23.hello()")
            return ready, round((time.time() - started) * 1000), before

        # ---- CH-02/03/04 launch, framing, no window -------------------------------------------------
        target, session = fresh_connector()
        state = {}

        def launch():
            nothing_before = host_processes(exe)
            expect(not nothing_before, f"host already running before connect: {nothing_before}")
            ready, ms, before = connect_hello(session)
            expect(ready.get("type") == "ready", f"hello reply {ready}")
            procs = new_host_process(exe, before)
            expect(len(procs) == 1, f"expected exactly one new host process, saw {procs}")
            proc = procs[0]
            state["pid"] = proc["ProcessId"]
            chain = ancestry(proc["ProcessId"])
            state["ancestry"] = chain
            state["wrapper"] = chain[0]["ProcessId"] if chain and chain[0]["Name"].lower() == "cmd.exe" else None
            expect(
                chrome.proc.pid in [p["ProcessId"] for p in chain],
                f"Chrome browser pid {chrome.proc.pid} is not an ancestor of the host: {chain}",
            )
            cmd = proc["CommandLine"] or ""
            expect("chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/" in cmd, f"origin not in command line: {cmd}")
            return {
                "launched_on_demand_by_runtime.connectNative": True,
                "integrity": {
                    "chrome": integrity_of(chrome.proc.pid),
                    "cmd_wrapper": integrity_of(state["wrapper"]) if state.get("wrapper") else None,
                    "host": integrity_of(proc["ProcessId"]),
                },
                "host_pid": proc["ProcessId"], "parent_pid": proc["ParentProcessId"], "ancestry": chain,
                "command_line": cmd, "executable": proc["ExecutablePath"], "hello_to_ready_ms": ms,
                "ready": ready,
            }

        report.case("CH-02", "Chrome launches the host on demand via runtime.connectNative; hello -> ready",
                    "one new host process, parent = Chrome browser, exact origin argument, ready reply", launch)

        def no_window():
            pid = state["pid"]
            wrapper = state.get("wrapper")
            owners = [pid] + ([wrapper] if wrapper else [])
            windows = {o: top_level_windows_of(o) for o in owners}
            kids = {o: children_of(o) for o in owners}
            net = {o: network_endpoints_of(o) for o in owners}
            host_windows = windows[pid]
            expect(not [w for w in host_windows if w["visible"]], f"host shows a window: {host_windows}")
            if args.flavour == "plain":
                expect(not host_windows, f"host owns top-level windows: {host_windows}")
            visible_wrapper = [w for w in windows.get(wrapper, []) if w["visible"]] if wrapper else []
            expect(not visible_wrapper, f"Chrome's cmd wrapper shows a window: {visible_wrapper}")
            host_kids = kids[pid]
            expect(not host_kids, f"host has child processes: {host_kids}")
            expect(not any(net.values()), f"network endpoints: {net}")
            wrapper_kids = [k["Name"] for k in kids.get(wrapper, [])] if wrapper else []
            expect(wrapper_kids.count(EXE_NAME[args.flavour]) == 1, f"wrapper children {wrapper_kids}")
            return {
                "host_pid": pid, "chrome_cmd_wrapper_pid": wrapper,
                "host_top_level_windows_visible": 0,
                "host_top_level_windows_hidden": host_windows,
                "wrapper_visible_windows": 0,
                "wrapper_hidden_windows": len(windows.get(wrapper, [])) if wrapper else 0,
                "host_child_processes": 0,
                "tcp_udp_endpoints": 0, "wrapper_children": wrapper_kids,
                "note": "Chrome starts the host through a hidden cmd.exe wrapper that redirects "
                        "stdin/stdout to Chrome-owned named pipes",
            }

        report.case("CH-03", "Host creates no window, no console child, no listener socket",
                    "plain host: 0 windows of any kind; Tauri host: 0 visible windows (hidden event-loop helper windows are reported); Chrome's cmd wrapper: 0 visible windows; no host children; 0 TCP/UDP endpoints", no_window)

        def correlated_errors():
            invalid = chrome.js(session, "otp23.request('start_scan', {service_uuid: 'NOT-A-UUID'})")
            unknown = chrome.js(session, "otp23.send('0123456789abcdef', new Uint8Array([86,69,76,1]))")
            reset = chrome.js(session, "otp23.stopScan('0123456789abcdef')")
            expect(invalid.get("code") == "invalid_request", invalid)
            expect(unknown.get("code") == "unknown_handle", unknown)
            expect(reset.get("code") == "unknown_handle", reset)
            snap = chrome.js(session, "otp23.snapshot()")
            expect(snap["portOpen"], "an invalid request must not close the session")
            return {"invalid_uuid": invalid, "unknown_connection_handle": unknown, "unknown_scan_handle": reset}

        report.case("CH-04", "Schema and handle errors are correlated and the session survives",
                    "invalid_request / unknown_handle with request ids; port stays open", correlated_errors)

        def veles_scan():
            started = chrome.js(session, f"otp23.startScan({json.dumps(VELES_SERVICE)})")
            expect(started.get("type") == "scan_state" and started.get("status") == "started", started)
            time.sleep(4)
            events = chrome.js(session, "otp23.drainEvents()")
            stopped = chrome.js(session, f"otp23.stopScan({json.dumps(started['scan_handle'])})")
            expect(stopped.get("type") == "scan_state" and stopped.get("status") == "stopped", stopped)
            expect(stopped["request_id"] != started["request_id"], "ids must differ")
            found = [e for e in events if e["message"].get("type") == "device_found"]
            return {
                "scan_started": {k: started[k] for k in ("request_id", "scan_handle", "status")},
                "veles_service_devices_found_in_4s": len(found),
                "scan_stopped_request_id": stopped["request_id"],
                "note": "0 means no phone is advertising the Veles service right now",
            }

        report.case("CH-05", "Extension-owned scan for the Veles service: started, bounded events, stopped",
                    "scan_state started (scan handle), device events only for that scan, scan_state stopped", veles_scan)

        if args.diag_scan:
            def diag():
                started = chrome.js(session, f"otp23.startScan({json.dumps(VELES_SERVICE)})")
                expect(started.get("status") == "started", started)
                time.sleep(8)
                events = chrome.js(session, "otp23.drainEvents()")
                found = [e["message"] for e in events if e["message"].get("type") == "device_found"]
                hints = [m["platform_hint"] for m in found]
                expect(len(set(hints)) == len(hints), "duplicate device_found for one device in one scan")
                expect(all(m["scan_handle"] == started["scan_handle"] for m in found), "foreign scan handle")
                expect(all(1 <= len(m["label"].encode()) <= 64 for m in found), "label bounds")
                chrome.js(session, f"otp23.stopScan({json.dumps(started['scan_handle'])})")
                return {"devices_delivered_to_extension": len(found), "sample_redacted":
                        [{"label_len": len(m["label"]), "hint_prefix": m["platform_hint"][:6]} for m in found[:3]]}

            report.case("CH-06", "[diagnostic: unfiltered scan] bounded device events reach the extension",
                        "device_found per device once, within the rate bound, correct scan handle", diag)

        # ---- CH-07 absent device classification ---------------------------------------------------------
        def absent():
            reply = chrome.js(session, "otp23.connectDevice({platform_hint: 'winrt:020000000001:1'})", timeout=70)
            expect(reply.get("type") == "error" and reply.get("code") == "connect_failed", reply)
            return {"reply": {k: reply.get(k) for k in ("code", "recoverability", "stage", "request_id")}}

        report.case("CH-07", "Connect to an absent device fails closed with a classified error",
                    "correlated connect_failed with stage and recoverability", absent)

        # ---- CH-08 shutdown while scanning (stdin stays open: Chrome holds the pipe) ------------------------
        def shutdown_path():
            started = chrome.js(session, f"otp23.startScan({json.dumps(VELES_SERVICE)})")
            expect(started.get("status") == "started", started)
            t0 = time.time()
            done = chrome.js(session, "otp23.shutdown()")
            expect(done.get("type") == "shutdown_complete", done)
            events = chrome.js(session, "otp23.drainEvents()")
            kinds = [e["message"].get("type") + ":" + str(e["message"].get("status")) for e in events]
            expect("scan_state:stopped" in kinds, f"no unsolicited scan stop before shutdown_complete: {kinds}")
            gone = wait_all_gone([state["pid"], state.get("wrapper")], 8)
            expect(gone is not None, "host or its cmd wrapper still alive after shutdown_complete")
            snap = chrome.js(session, "otp23.snapshot()")
            return {"shutdown_complete": True, "unsolicited_before_complete": kinds,
                    "process_gone_after_s": gone, "request_to_gone_s": round(time.time() - t0, 3),
                    "port_state": snap["disconnect"]}

        report.case("CH-08", "shutdown: scan stopped, shutdown_complete last, stdout closed, process exits (stdin still open)",
                    "unsolicited scan stop, correlated shutdown_complete, port closes, host process gone", shutdown_path)

        # ---- CH-09 relaunch + port closure ----------------------------------------------------------------
        def port_close():
            chrome.js(session, "otp23.snapshot()")  # old port is closed; connect a fresh one
            ready, ms, before = connect_hello(session)
            expect(ready.get("type") == "ready", ready)
            proc = new_host_process(exe, before)[0]
            expect(proc["ProcessId"] != state["pid"], "relaunch reused a pid?")
            pid = proc["ProcessId"]
            wrapper = wrapper_of(pid)
            started = chrome.js(session, f"otp23.startScan({json.dumps(VELES_SERVICE)})")
            expect(started.get("status") == "started", started)
            t0 = time.time()
            chrome.js(session, "otp23.closePort(); 1")
            gone = wait_all_gone([pid, wrapper], 8)
            expect(gone is not None, "host or cmd wrapper survived Chrome port closure")
            return {"relaunched_pid": pid, "hello_to_ready_ms": ms, "port_closed_to_process_gone_s": round(time.time() - t0, 3)}

        report.case("CH-09", "Chrome port closure (stdin EOF) while scanning: scan released, process exits",
                    "host process gone shortly after port.disconnect(); relaunch on next connect works", port_close)

        # ---- CH-10 tab close ---------------------------------------------------------------------------------
        def tab_close():
            tab, tab_session = fresh_connector()
            ready, _, before = connect_hello(tab_session)
            expect(ready.get("type") == "ready", ready)
            pid = new_host_process(exe, before)[0]["ProcessId"]
            wrapper = wrapper_of(pid)
            started = chrome.js(tab_session, f"otp23.startScan({json.dumps(VELES_SERVICE)})")
            expect(started.get("status") == "started", started)
            t0 = time.time()
            chrome.cdp.call("Target.closeTarget", {"targetId": tab})
            gone = wait_all_gone([pid, wrapper], 8)
            expect(gone is not None, "host or cmd wrapper survived closing the connector tab")
            return {"host_pid": pid, "tab_closed_to_process_gone_s": round(time.time() - t0, 3)}

        report.case("CH-10", "Closing the connector tab while scanning: process exits",
                    "host process gone after the owning page is destroyed", tab_close)

        # ---- CH-11 unregistered host ------------------------------------------------------------------------------
        def missing():
            tab, tab_session = fresh_connector()
            chrome.js(tab_session, "otp23.connect('me.nagaev.veles.otp23.unregistered'); 1")
            time.sleep(1.5)
            snap = chrome.js(tab_session, "otp23.snapshot()")
            reply = chrome.js(tab_session, "otp23.hello()")
            chrome.cdp.call("Target.closeTarget", {"targetId": tab})
            expect(snap["disconnect"] is not None, snap)
            expect(reply.get("code") == "internal" and reply.get("stage") == "port_closed", reply)
            return {"port_disconnect": snap["disconnect"], "synthesized_hello_reply": reply}

        report.case("CH-11", "Missing registration is reported by Chrome before any hello",
                    "onDisconnect with a native-host-not-found message; requests synthesize port_closed", missing)

        # ---- CH-12 two ports => two processes (informational) -------------------------------------------------------
        def two_ports():
            a, sa = fresh_connector()
            b, sb = fresh_connector()
            _, _, before = connect_hello(sa)
            pa = new_host_process(exe, before)[0]["ProcessId"]
            ready_b, _, before_b = connect_hello(sb)
            pb = new_host_process(exe, before_b)[0]["ProcessId"]
            expect(pa != pb, "second port reused the first process")
            chrome.js(sa, "otp23.closePort(); 1")
            gone_a = wait_gone(pa, 8)
            alive_b = process_alive(pb)
            chrome.js(sb, "otp23.closePort(); 1")
            gone_b = wait_gone(pb, 8)
            for t in (a, b):
                chrome.cdp.call("Target.closeTarget", {"targetId": t})
            expect(gone_a is not None and alive_b and gone_b is not None, f"{gone_a} {alive_b} {gone_b}")
            return {"processes": [pa, pb], "closing_one_port_leaves_the_other": True,
                    "note": "each port owns its own host process; the product design uses a single connector tab"}

        report.case("CH-12", "Two ports get two independent host processes; closing one leaves the other",
                    "distinct pids; independent cleanup", two_ports)

        # ---- CH-13 browser exit ----------------------------------------------------------------------------------------
        def browser_exit():
            tab, tab_session = fresh_connector()
            ready, _, before = connect_hello(tab_session)
            pid = new_host_process(exe, before)[0]["ProcessId"]
            started = chrome.js(tab_session, f"otp23.startScan({json.dumps(VELES_SERVICE)})")
            expect(started.get("status") == "started", started)
            state["browser_exit_pid"] = pid
            state["browser_exit_wrapper"] = wrapper_of(pid)
            return {"host_pid": pid, "cmd_wrapper_pid": state["browser_exit_wrapper"], "scan": "running; Chrome will now be closed"}

        report.case("CH-13", "Host running with an active scan when Chrome exits",
                    "host process gone after Browser.close", browser_exit)

        if args.phone:
            phone_cases(chrome, report, session, fresh_connector, connect_hello, exe)

        meta["extension_id"] = ext_id
        return finish(report, meta, args, chrome, real_state, real_before, [state.get("browser_exit_pid"), state.get("browser_exit_wrapper")])
    except Exception:
        meta["fatal"] = traceback.format_exc()
        return finish(report, meta, args, chrome, real_state, real_before)


def phone_cases(chrome, report, session, fresh_connector, connect_hello, exe):
    """Only executed with --phone. Never claims a round trip unless the phone actually answers."""

    def roundtrip():
        tab, tab_session = fresh_connector()
        ready, _, _ = connect_hello(tab_session)
        expect(ready.get("type") == "ready", ready)
        started = chrome.js(tab_session, f"otp23.startScan({json.dumps(VELES_SERVICE)})")
        expect(started.get("status") == "started", started)
        deadline = time.time() + 30
        device = None
        while time.time() < deadline and device is None:
            for event in chrome.js(tab_session, "otp23.drainEvents()"):
                if event["message"].get("type") == "device_found":
                    device = event["message"]
            time.sleep(0.5)
        if device is None:
            raise AssertionError("no phone advertised the Veles service within 30 s")
        connected = chrome.js(tab_session, f"otp23.connectDevice({{device_handle: {json.dumps(device['device_handle'])}}})", timeout=70)
        if connected.get("type") != "connected":
            raise AssertionError({k: connected.get(k) for k in ("code", "stage", "recoverability")})
        handle = connected["connection_handle"]
        sent = chrome.js(tab_session, f"otp23.send({json.dumps(handle)}, new Uint8Array([86,69,76,1]))")
        deadline = time.time() + 10
        message = None
        while time.time() < deadline and message is None:
            for event in chrome.js(tab_session, "otp23.drainEvents()"):
                if event["message"].get("type") == "message":
                    message = event["message"]
            time.sleep(0.2)
        expect(sent.get("type") == "sent", sent)
        expect(message is not None and message["record_base64"] == "VkVMAg==", f"no VEL\\x02 response: {message}")
        return {"status": "ROUND-TRIP", "sent": "VEL\\x01", "received": "VEL\\x02"}

    report.case("PH-01", "Physical phone round trip (OTP-06 synthetic fixture)", "VEL\\x01 -> VEL\\x02", roundtrip)


def finish(report, meta, args, chrome, real_state, real_before, browser_exit_pids=None):
    if browser_exit_pids and browser_exit_pids[0] is not None:
        t0 = time.time()
        try:
            chrome.cdp.call("Browser.close", timeout=10)
        except Exception:
            pass
        try:
            chrome.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            chrome.proc.kill()
        gone = wait_all_gone(browser_exit_pids, 10)
        record = next((c for c in report.cases if c["id"] == "CH-13"), None)
        if record is not None:
            if gone is not None:
                record["actual"] = dict(record["actual"], chrome_exit_to_host_gone_s=round(time.time() - t0, 3))
            else:
                record["result"], record["actual"] = "FAIL", "host survived Chrome exit"
            print(f"[{record['result']:5}] CH-13 (after Browser.close)")
    else:
        chrome.close()
    survivors = host_processes(EXE_NAME[args.flavour])
    real_after = real_state.stat().st_mtime_ns if real_state.exists() else None
    meta["surviving_host_processes_after_run"] = survivors
    meta["real_profile_local_state_mtime_unchanged"] = real_before == real_after
    meta["real_profile_path_never_used"] = str(USER_DATA_REAL)
    shutil.rmtree(chrome.profile, ignore_errors=True)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(scrub(json.dumps({"meta": meta, "cases": report.cases}, indent=2)), encoding="utf-8")
    failed = [c for c in report.cases if c["result"] != "PASS"]
    print(f"{len(report.cases) - len(failed)}/{len(report.cases)} cases passed; survivors={len(survivors)}; evidence -> {out}")
    return 1 if failed or survivors or "fatal" in meta else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--flavour", choices=sorted(HOSTS), default="plain")
    parser.add_argument("--out", required=True)
    parser.add_argument("--diag-scan", action="store_true", help="set VELES_OTP23_DIAG_SCAN_ANY for the host")
    parser.add_argument("--medium", action="store_true",
                        help="launch Chrome with the unelevated medium-integrity token of the same user")
    parser.add_argument("--phone", action="store_true", help="also attempt the physical phone round trip")
    sys.exit(run(parser.parse_args()))
