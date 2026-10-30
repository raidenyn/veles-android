"""OTP-23 macOS Chrome driver (disposable profile, CDP Extensions.loadUnpacked, two real phones).

Launches stable Chrome with a throwaway --user-data-dir (never the real profile), loads the
unpacked connector extension over the DevTools protocol, then drives window.otp23 in the
connector tab against the registered me.nagaev.veles.otp23.plain host packaged inside
VelesOtp23PoC.app. `chrome_validate.py` is Windows-only and is deliberately NOT imported;
the stdlib equivalents live in the dedicated helper scripts/macos_cdp.py.

Usage from native-bridge/feasibility (after ./scripts/macos-package.sh + ./scripts/macos-register.sh):
    python3 scripts/macos_poc.py --out evidence/macos-poc.json
    python3 scripts/macos_poc.py --diag-scan-only --out evidence/macos-poc-diag.json
    python3 scripts/macos_poc.py register --app target/macos-poc/VelesOtp23PoC.app
    python3 scripts/macos_poc.py unregister

Python 3.9, standard library only. Target CDP is always loopback. No payload is ever logged:
records exist as sizes and SHA-256 digests only. Missing phones, Bluetooth permission denials,
record mismatches and any surviving owned host process fail the run (exit 1). Permission
denials print the System Settings fix; nothing here bypasses or resets TCC.
"""

import argparse
import base64
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone

from macos_cdp import Cdp, WebSocket
from macos_install import APP_DEFAULT, MANIFEST_DIR_DEFAULT, PACKAGED_BINARY, read_identity, \
    register as install_register, unregister as install_unregister

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXT_DIR = ROOT / "extension"
DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HOST_NAME = "me.nagaev.veles.otp23.plain"
VELES_SERVICE = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51"
SIZES = [4, 177, 178, 179, 180] + list(range(505, 512)) + [1015, 1016, 1017] + [4095, 4096]
SEED_BASE = [17, 137]  # per-handle distinct seed bases: handle 0 uses 17+i, handle 1 uses 137+i
DENIAL_HINTS = ("denied", "consent", "permission", "unauthor", "not_authorized")
MTU_NEGOTIATED = "negotiated"
MTU_INFERRED_MAX_WRITE = "inferred_from_max_write"  # legacy host traces (<= fb224e8): MTU derived from max_write
BUDGET_MEASURED_MAX_WRITE = "measured_max_write_without_response"  # macOS: attribute budget = min(write, 512)
TCC_MESSAGE = (
    "Bluetooth access was denied. Open System Settings -> Privacy & Security -> Bluetooth and "
    "allow 'Google Chrome' (and/or the VelesOtp23PoC app), then re-run this command. This "
    "driver never bypasses or resets TCC."
)


class PermissionDenied(RuntimeError):
    """A scan/connect/send reply classified as a Bluetooth permission denial."""


# ---- synthetic OTP-06 fixture (mirrors scripts/physical_validate.py and the JS builder) ----------


def fixture(size, seed):
    """Deterministic record accepted by the OTP-06 SyntheticTransportFixture: header VEL\\x01,
    remainder (i + seed) & 255 (physical_validate's version is identical after the overwrite)."""
    data = bytearray((i + seed) & 255 for i in range(size))
    data[:4] = b"VEL\x01"
    return bytes(data)


def echo(record):
    """Expected OTP-06 reply: the same record with the version byte 3 changed to 0x02."""
    return record[:3] + b"\x02" + record[4:]


def js_send_expression(handle, size, seed):
    """Full `otp23.send(...)` expression. The record is built in the page itself; the mapper
    MUST be the second argument of Uint8Array.from (never base64/atob decoding)."""
    mapper = f"(_, i) => i < 4 ? [86, 69, 76, 1][i] : (i + {seed}) & 255"
    return f"otp23.send({json.dumps(handle)}, Uint8Array.from({{length: {size}}}, {mapper}))"


def chrome_arguments(profile, chrome_path):
    """Stable Chrome with a throwaway profile; CDP is loopback-only via DevToolsActivePort."""
    return [
        chrome_path, f"--user-data-dir={profile}", "--remote-debugging-port=0",
        "--enable-unsafe-extension-debugging", "--no-first-run", "--no-default-browser-check",
        "--disable-sync", "--window-size=1000,700", "about:blank",
    ]


def install_profile_manifest(profile, manifest_source):
    """Chrome resolves NativeMessagingHosts inside --user-data-dir, so the validated manifest
    (byte-identical copy of the registered one) goes into the disposable profile only."""
    target_dir = pathlib.Path(profile) / "NativeMessagingHosts"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{HOST_NAME}.json"
    target.write_bytes(pathlib.Path(manifest_source).read_bytes())
    target.chmod(0o644)
    return target


class ChromeRun:
    def __init__(self, chrome_path=DEFAULT_CHROME, extra_env=None, manifest_source=None):
        self.profile = pathlib.Path(tempfile.mkdtemp(prefix="veles-otp23-macos-profile-"))
        self.proc = None
        try:
            if manifest_source is not None:
                install_profile_manifest(self.profile, manifest_source)
        except OSError:
            self.close()
            raise
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        self.env = env  # kept for evidence: the environment Chrome (and thus the host) actually received
        self.cdp = None
        try:
            self.proc = subprocess.Popen(chrome_arguments(self.profile, chrome_path), env=env)
        except OSError:
            self.close()
            raise
        port_file = self.profile / "DevToolsActivePort"
        for _ in range(300):
            if port_file.exists() and len(port_file.read_text().splitlines()) >= 2:
                break
            time.sleep(0.1)
        else:
            self.close()
            raise RuntimeError("Chrome did not publish DevToolsActivePort")
        port, path = port_file.read_text().splitlines()[:2]
        self.port = int(port)
        self.cdp = Cdp(WebSocket("127.0.0.1", self.port, path))
        self.version = self.cdp.call("Browser.getVersion")

    def open_page(self, url):
        target = self.cdp.call("Target.createTarget", {"url": url})["targetId"]
        session = self.cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        self.cdp.call("Runtime.enable", session=session)
        return target, session

    def js(self, session, expression, timeout=70):
        reply = self.cdp.call("Runtime.evaluate",
                              {"expression": expression, "awaitPromise": True, "returnByValue": True},
                              session=session, timeout=timeout)
        if "exceptionDetails" in reply:
            raise RuntimeError(json.dumps(reply["exceptionDetails"])[:500])
        return reply["result"].get("value")

    def close(self):
        if self.proc is not None:
            try:
                self.cdp.call("Browser.close", timeout=10)
            except Exception:
                pass
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
        try:
            shutil.rmtree(self.profile)
        except OSError:
            time.sleep(0.5)
            shutil.rmtree(self.profile, ignore_errors=True)


# ---- process lifecycle (ps -axo based; snapshot injectable for tests) ----------------------------


def ps_snapshot():
    try:
        return subprocess.run(["ps", "-axo", "pid=,ppid=,stat=,args="], capture_output=True,
                              text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


HOST_EXEC = re.compile(r"(?:^|/)otp23-host(?![A-Za-z0-9_-])")


def parse_ps(text):
    rows = []
    for line in text.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)\s+(.*)$", line)
        if not match:
            continue
        pid, ppid, stat, args = int(match.group(1)), int(match.group(2)), match.group(3), match.group(4)
        rows.append({"pid": pid, "ppid": ppid, "stat": stat, "args": args,
                     "comm": pathlib.PurePosixPath(args.split()[0]).name,
                     "is_host": bool(HOST_EXEC.search(args))})
    return rows


def host_processes(rows):
    """Rows whose executable is exactly the plain host binary (paths may contain spaces, so
    recognition is by '/otp23-host' followed by a non-word character, never '*-tauri' or bait)."""
    return [row for row in rows if row["is_host"]]


def ancestry(rows, pid, limit=6):
    """Parent chain (pid, comm, args prefix), closest parent first."""
    by_pid = {row["pid"]: row for row in rows}
    chain, current = [], pid
    for _ in range(limit):
        row = by_pid.get(current)
        if row is None or row["ppid"] not in by_pid:
            break
        current = row["ppid"]
        chain.append({"pid": current, "comm": by_pid[current]["comm"],
                      "args": by_pid[current]["args"][:400]})
    return chain


def process_alive(pid):
    """Alive and neither exited nor an unreaped zombie ('Z' state) on Linux or macOS."""
    try:
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(out) and not out.startswith("Z")


def wait_gone(pid, timeout=10.0):
    start = time.time()
    while time.time() - start < timeout:
        if not process_alive(pid):
            return round(time.time() - start, 3)
        time.sleep(0.1)
    return None


def windows_owned_by(pid):
    """On-screen windows owned by `pid` via CoreGraphics (macOS only); None when unavailable.

    Keys are public CFStringRef constants: kCGWindowOwnerPID. Any failure means 'unavailable',
    never a wrong assertion."""
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        import ctypes.util
        cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
        cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFNumberGetValue.restype = ctypes.c_int
        cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        pid_key = ctypes.c_void_p(ctypes.c_void_p.in_dll(cg, "kCGWindowOwnerPID").value)
        array = cg.CGWindowListCopyWindowInfo(3, None)  # on-screen | exclude desktop elements
        if not array:
            return []
        found = []
        try:
            for index in range(cf.CFArrayGetCount(array)):
                window = cf.CFArrayGetValueAtIndex(array, index)
                number = cf.CFDictionaryGetValue(window, pid_key)
                if not number:
                    continue
                value = ctypes.c_longlong(0)
                if cf.CFNumberGetValue(number, 4, ctypes.byref(value)) \
                        and value.value == pid:  # 4 = kCFNumberSInt64Type
                    found.append({"layer_index": index})
            return found
        finally:
            cf.CFRelease(array)
    except Exception:
        return None


def census_windows_from_entries(entries):
    """Pure census over CGWindowList entries: any layer owned by `pid`, with the layer-0 subset kept.

    `entries` is [{"pid": int, "layer": int}, ...] in CGWindowList order. This is the parsing half of
    census_windows, split out so the ordering/filter behaviour is unit-tested without touching CoreGraphics."""
    all_windows, layer_zero = [], []
    for entry in entries:
        all_windows.append({"layer": entry["layer"]})
        if entry["layer"] == 0:
            layer_zero.append({"layer": 0})
    return {"all": all_windows, "layer_zero": layer_zero}


CGWINDOW_LIST_OPTION_ALL = 0  # kCGWindowListOptionAll: any layer, on-screen or not
CGNULL_WINDOW_ID = 0  # kCGNullWindowID


def census_windows(pid):
    """OTP-23 M-08 connected-phase census: windows of ANY layer owned by `pid` via
    CGWindowListCopyWindowInfo(kCGWindowListOptionAll(0), kCGNullWindowID(0)).

    Returns the census dict, or None when the API itself failed (a NULL listing is "unavailable", never
    zero windows; an EMPTY listing is real data — the Chrome layer-0 control decides usability)."""
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        import ctypes.util
        cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
        cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFNumberGetValue.restype = ctypes.c_int
        cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        pid_key = ctypes.c_void_p(ctypes.c_void_p.in_dll(cg, "kCGWindowOwnerPID").value)
        layer_key = ctypes.c_void_p(ctypes.c_void_p.in_dll(cg, "kCGWindowLayer").value)
        array = cg.CGWindowListCopyWindowInfo(CGWINDOW_LIST_OPTION_ALL, CGNULL_WINDOW_ID)
        if not array:
            return None  # NULL listing is 'unavailable', never zero windows
        owned = []
        try:
            for index in range(cf.CFArrayGetCount(array)):
                window = cf.CFArrayGetValueAtIndex(array, index)
                number = cf.CFDictionaryGetValue(window, pid_key)
                if not number:
                    continue
                value = ctypes.c_longlong(0)
                if not (cf.CFNumberGetValue(number, 4, ctypes.byref(value)) and value.value == pid):
                    continue
                layer_value = ctypes.c_longlong(0)
                layer = -1
                layer_number = cf.CFDictionaryGetValue(window, layer_key)
                if layer_number and cf.CFNumberGetValue(layer_number, 4, ctypes.byref(layer_value)):
                    layer = layer_value.value
                owned.append({"layer": layer})
        finally:
            cf.CFRelease(array)
        return census_windows_from_entries(owned)
    except Exception:
        return None


def pid_alive(pid):
    """True when `pid` exists right now (kill(pid, 0)); False when it is gone; None if undecidable."""
    if sys.platform != "darwin":
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, but belongs to another uid
    except OSError:
        return None


def context_probe():
    """The actual observation context this driver runs in (macOS): window manager, uid, SSH marker.
    Recorded as data for every measured run — never a placeholder string."""
    if sys.platform != "darwin":
        return None
    try:
        managername = subprocess.run(["launchctl", "managername"], capture_output=True, text=True,
                                     timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        managername = "unavailable"
    return {"launchctl_managername": managername or "unavailable",
            "uid": os.getuid(),
            "ssh_connection_present": bool(os.environ.get("SSH_CONNECTION"))}


def port_after_final_colon(token):
    """Port after the FINAL colon of an lsof address token (I-1): '[::1]:9222' -> 9222 (never 1 from the
    interior '::1'), 'fe80::1%en0:52001' -> 52001, '*:5353' -> 5353. Raises ValueError on garbage."""
    if not isinstance(token, str) or token == "":
        raise ValueError(f"empty address token: {token!r}")
    colon = token.rfind(":")
    if colon == -1 or colon == len(token) - 1:
        raise ValueError(f"no port in address token: {token!r}")
    port = token[colon + 1:]
    if not port.isdigit():
        raise ValueError(f"bad port in address token: {token!r}")
    return int(port)


def parse_lsof_endpoints(lines):
    """Pure half of lsof_endpoints (I-1): data lines -> [{local_port, remote_port, state}].

    Raises ValueError on any line that does not parse — the caller must record that as unavailable,
    never as a fabricated zero."""
    endpoints = []
    for line in lines:
        if not line.strip():
            continue
        match = re.search(r"\b(TCP|UDP)\s+(.*)$", line)
        if not match:
            raise ValueError(f"unparsable lsof line: {line!r}")
        proto, rest = match.group(1), match.group(2).strip()
        state = None
        paren = re.search(r"\((\w+)\)\s*$", rest)
        if paren:
            state = paren.group(1)
            rest = rest[:paren.start()].strip()
        if "->" in rest:
            local_token, remote_token = (part.strip() for part in rest.split("->", 1))
        else:
            local_token, remote_token = rest, None
        endpoints.append({"local_port": port_after_final_colon(local_token),
                          "remote_port": port_after_final_colon(remote_token) if remote_token else None,
                          "state": state or proto})
    return endpoints


# ---- lsof stderr classification (Opus preflight Task 1, parent P0-A decision 2026-10-28) ----------

_LSOF_WARN_PATH = re.compile(r"^lsof: WARNING: can't stat\(\) nullfs file system (/\S+)$")
_LSOF_WARN_MID = re.compile(r"^\s+Output information may be incomplete\.$")
_LSOF_WARN_DEV = re.compile(r'^\s+assuming "dev=([0-9a-f]+)" from mount table$')
_LSOF_DIAG_ENV = "VELES_OTP23_LSOF_DIAG"
_FOREIGN_NULLFS_CACHE = None
_LSOF_SNAPSHOT_CACHE = {}


def classify_lsof_stderr(stderr, foreign_nullfs_mounts):
    """Strict classifier: acceptable stderr consists ENTIRELY of one or more exact 3-line
    foreign-nullfs mount-stat warning blocks, each block's path a member of `foreign_nullfs_mounts`
    (recovered from the measured mount table ONLY — never an assumption):

        lsof: WARNING: can't stat() nullfs file system <mount-point>
              Output information may be incomplete.
              assuming "dev=<hex>" from mount table

    Returns (benign, blocks): benign True with the parsed [{"path", "dev"}] blocks (empty stderr →
    (True, [])), or benign False with None for ANY line that is not one of these exact blocks —
    unknown/incomplete/error stderr is never classified as benign. A benign verdict alone is never
    enough to accept a warning: the caller must also record the mandatory diagnostics line."""
    if not stderr or not stderr.strip():
        return True, []
    lines = stderr.splitlines()
    if len(lines) % 3 != 0:
        return False, None
    blocks = []
    for index in range(0, len(lines), 3):
        head, mid, dev = _LSOF_WARN_PATH.match(lines[index]), \
            _LSOF_WARN_MID.match(lines[index + 1]), _LSOF_WARN_DEV.match(lines[index + 2])
        if not (head and mid and dev):
            return False, None
        if head.group(1) not in foreign_nullfs_mounts:
            return False, None
        blocks.append({"path": head.group(1), "dev": dev.group(1)})
    return True, blocks


def foreign_nullfs_mounts():
    """Foreign nullfs mount points: lines of the measured `mount` table that are (nullfs, …) AND
    'mounted by <name>' with <name> != the current user's passwd name. Cached once per process;
    any failure to run or parse `mount` returns the EMPTY set, so the corresponding warning stays
    unavailable — never an assumption. The mount POINT is the exact string lsof warns about;
    foreign status is decided only by the mount table's own owner attribution."""
    global _FOREIGN_NULLFS_CACHE
    if _FOREIGN_NULLFS_CACHE is not None:
        return _FOREIGN_NULLFS_CACHE
    import pwd
    try:
        self_name = pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, ImportError):
        self_name = None
    try:
        text = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        text = ""
    mounts = set()
    for line in text.splitlines():
        if " (nullfs," not in line:
            continue
        head, sep, tail = line.rpartition(" (")
        if not sep or " on " not in head:
            continue
        _, _, point = head.partition(" on ")
        body = tail[:-1] if tail.endswith(")") else tail
        # F-7: the owner name is bounded at the NEXT COMMA (it is one comma-separated option inside
        # the mount line), never the whole option tail — a self-owned mount with a further trailing
        # option must not be misclassified as foreign.
        owner = re.search(r"mounted by ([^,]+)", body)
        if point and owner and self_name is not None and owner.group(1).strip() != self_name:
            mounts.add(point)
    _FOREIGN_NULLFS_CACHE = mounts
    return _FOREIGN_NULLFS_CACHE


def _sanitized_nullfs_mount_lines(text, self_name):
    """Nullfs-only lines of a whole `mount` output, redacted for publication: mount POINTS kept
    verbatim (they carry no account names), source paths dropped entirely (they contain other
    users' home dirs and app names), owner names replaced by 'uid=self' / 'uid≠self'."""
    lines = []
    for line in text.splitlines():
        if " (nullfs," not in line:
            continue
        head, sep, tail = line.rpartition(" (")
        if not sep or " on " not in head:
            continue
        _, _, point = head.partition(" on ")
        body = tail[:-1] if tail.endswith(")") else tail
        owner = re.search(r"mounted by ([^,]+)", body)   # F-7: comma-bounded owner token
        who = "uid=self" if (owner and self_name is not None and owner.group(1).strip() == self_name) \
            else "uid≠self"
        opts = re.sub(r"mounted by [^,]+", f"mounted by {who}", body)
        lines.append(f"on {point} ({opts})")
    return lines


def _lsof_mount_snapshot_sha256(directory):
    """Write the sanitized nullfs mount snapshot ONCE per process to <directory>/mount-nullfs.txt
    and return its sha256; None (fail-closed) on any write failure or when the mount table itself
    cannot be read. The hash is what the mandatory diagnostics lines reference."""
    key = str(directory)
    if key in _LSOF_SNAPSHOT_CACHE:
        return _LSOF_SNAPSHOT_CACHE[key]
    try:
        import pwd
        self_name = pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, ImportError):
        self_name = f"uid={os.getuid()}"
    try:
        text = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        _LSOF_SNAPSHOT_CACHE[key] = None
        return None
    content = "".join([
        "# sanitized nullfs mount-table snapshot (mount points only; source paths dropped;",
        " owner names redacted as uid=self / uid≠self)\n"]) + \
        "".join(line + "\n" for line in _sanitized_nullfs_mount_lines(text, self_name))
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "mount-nullfs.txt"
        target.write_text(content, encoding="utf-8")
        _LSOF_SNAPSHOT_CACHE[key] = file_sha256(target)
    except OSError:
        _LSOF_SNAPSHOT_CACHE[key] = None
    return _LSOF_SNAPSHOT_CACHE[key]


def _lsof_diag_record(pid, rc, stdout_rows, stderr_verbatim, classification):
    """Append the mandatory JSONL diagnostics line; False when the env var is unset (or the write
    fails) so a benign warning can then never be accepted unaudited."""
    diag_path = os.environ.get(_LSOF_DIAG_ENV)
    if not diag_path:
        return False
    mount_snapshot = _lsof_mount_snapshot_sha256(pathlib.Path(diag_path).resolve().parent)
    if mount_snapshot is None:
        return False
    record = {"wall_ms": int(time.time() * 1000),
              "pid": pid, "argv": ["lsof", "-nP", "-a", "-p", str(pid), "-i"], "rc": rc,
              "stdout_rows": stdout_rows, "stderr_verbatim": stderr_verbatim,
              "classification": classification, "mount_snapshot_sha256": mount_snapshot,
              "note": "snapshot redaction drops source paths and replaces owner names "
                      "(uid=self/uid≠self); the warning's mount point is kept verbatim"}
    try:
        pathlib.Path(diag_path).open("a", encoding="utf-8").write(json.dumps(record) + "\n")
    except OSError:
        return False
    return True


def lsof_endpoints(pid):
    """Endpoint list owned by `pid` via `lsof -nP -a -p <pid> -i`; ports/states only, no addresses.

    Honest failure semantics (I-1, unchanged): `[]` is returned ONLY for a genuinely successful
    query with no endpoint records (rc=0 with no rows, or rc=1 with NO stderr — lsof's real "no
    matching records") for a pid that is alive. rc outside (0, 1), an OSError, and any unparsable
    line return None (unavailable) — a caller can never turn a failure into a zero.

    Known foreign-nullfs stat warning (P0-A era, Opus preflight): stderr consisting ENTIRELY of
    exact 3-line 'can't stat() nullfs file system' blocks whose mount points are foreign (mounted
    by another user, per the measured mount table) MAY be accepted — but only when the mandatory
    diagnostics record was written: env `VELES_OTP23_LSOF_DIAG=<run-dir>/lsof-diagnostics.jsonl`
    appends one JSON line per invocation (verbatim stderr, classification, sanitized mount-snapshot
    hash; the snapshot is written once to <run-dir>/mount-nullfs.txt, owner names redacted). The
    argv stays normal — never `-w`, which would silence every warning globally. Env unset, any
    diagnostics write failure, any unknown/extra/non-matching stderr line → None."""
    try:
        done = subprocess.run(["lsof", "-nP", "-a", "-p", str(pid), "-i"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode not in (0, 1):
        return None  # usage/internal error: query failed
    try:
        rows = parse_lsof_endpoints(done.stdout.splitlines()[1:])  # skip the lsof header row
    except ValueError:
        return None
    clean = not done.stderr.strip()
    benign, blocks = classify_lsof_stderr(done.stderr, foreign_nullfs_mounts())
    if not benign:
        return None  # I-1: unknown/incomplete/error stderr stays unavailable, never zero
    recorded = _lsof_diag_record(pid, done.returncode, len(done.stdout.splitlines()),
                                 done.stderr, "clean_stderr" if clean else {"benign": True, "blocks": blocks})
    if not clean and not recorded:
        return None  # a benign warning is accepted ONLY with its recorded diagnostics line
    return rows


# ---- evidence helpers ----------------------------------------------------------------------------


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def scrub(text):
    """Evidence must not embed the local paths of the running user."""
    return text.replace(str(pathlib.Path.home()), "%HOME%")


def source_revision():
    """Git provenance, or explicit `available: false` metadata; never a guessed commit.

    A tar-transferred workdir has no .git, so unavailability is expected and must be recorded
    with the stage and a bounded reason rather than silently dropped. Paths in the reason are
    scrubbed when the evidence JSON is serialized.
    """
    repo = ROOT.parent.parent

    def unavailable(stage, reason):
        return {"available": False, "stage": stage, "reason": " ".join(str(reason).split())[:200]}

    def git(stage, *args):
        try:
            done = subprocess.run(["git", "-C", str(repo), *args],
                                  capture_output=True, text=True, timeout=30)
        except OSError as error:
            return None, unavailable(stage, f"git not runnable: {error}")
        except subprocess.SubprocessError as error:
            return None, unavailable(stage, f"{type(error).__name__}: {error}")
        if done.returncode != 0:
            return None, unavailable(stage, done.stderr.strip() or f"exit {done.returncode}")
        return done.stdout.strip(), None

    commit, failure = git("rev-parse", "rev-parse", "HEAD")
    if failure:
        return failure
    status, failure = git("status", "status", "--porcelain")
    if failure:
        return failure
    return {"available": True, "commit": commit, "dirty": status != ""}


def file_sha256(path):
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 16), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def parse_trace(path):
    """Safe host trace: connection-MTU observations keyed by the host connection token (the
    host's own counter) and send/reply record lengths, never payloads. NO per-handle mapping
    is inferred; connected/subscribe lines interleave across concurrent links. Two accepted
    line shapes:

      WinRT (negotiated measurement):
        [    1242ms pid=87052] conn 2 mtu=517 at 1242ms
      macOS corebluetooth (no ATT MTU observation exists and none is invented; the measured
      maximum write-without-response length W is a backend limit; a budget exists only for W >= 512):
        [    1242ms pid=87052] conn 2 max_write_without_response=512 attribute_budget=512 payload_budget=508
      Historical macOS traces (host <= fb224e8) carried a derived MTU instead; they still parse,
      recorded as inferred, never as a negotiated measurement:
        [    1242ms pid=87052] conn 2 max_write_without_response=512 inferred effective att mtu=515 (max write + 3, capped 517)
    """
    mtu_by_conn, source_by_conn, budget_by_conn, sends, replies = {}, {}, {}, [], []
    path = pathlib.Path(path)
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stamp = re.match(r"\[\s*(\d+)ms pid=\d+\]", line)
            at_ms = int(stamp.group(1)) if stamp else None
            mtu = re.search(r"\] conn (\d+) mtu=(\d+) ", line)
            if mtu:
                mtu_by_conn[int(mtu.group(1))] = int(mtu.group(2))
                source_by_conn[int(mtu.group(1))] = MTU_NEGOTIATED
            measured = re.search(
                r"\] conn (\d+) max_write_without_response=(\d+) attribute_budget=(\d+) payload_budget=(\d+)", line)
            if measured:
                budget_by_conn[int(measured.group(1))] = {
                    "max_write_without_response": int(measured.group(2)),
                    "attribute_budget": int(measured.group(3)),
                    "payload_budget": int(measured.group(4)),
                }
                source_by_conn[int(measured.group(1))] = BUDGET_MEASURED_MAX_WRITE
            inferred = re.search(
                r"\] conn (\d+) max_write_without_response=(\d+) .*inferred effective att mtu=(\d+)", line)
            if inferred:
                mtu_by_conn[int(inferred.group(1))] = int(inferred.group(3))
                source_by_conn[int(inferred.group(1))] = MTU_INFERRED_MAX_WRITE
            if at_ms is None:
                continue
            send = re.search(r"type=send .*record_base64\.len=(\d+)", line)
            if send:
                sends.append({"at_ms": at_ms, "record_base64_len": int(send.group(1))})
            reply = re.search(r"type=message .*record_base64\.len=(\d+)", line)
            if reply:
                replies.append({"at_ms": at_ms, "record_base64_len": int(reply.group(1))})
    return {"mtu_by_conn": mtu_by_conn, "mtu_source_by_conn": source_by_conn,
            "budget_by_conn": budget_by_conn, "sends": sends, "replies": replies}


def deny_or_raise(reply, context):
    """A failed host reply: classify a Bluetooth permission denial, otherwise fail the case."""
    blob = json.dumps(reply or {}).lower()
    hints = sorted({hint for hint in DENIAL_HINTS if hint in blob})
    if hints:
        raise PermissionDenied(
            f"{context}: {json.dumps(reply)[:300]}; stages {hints}; fix: {TCC_MESSAGE}")
    raise AssertionError(f"{context} failed: {json.dumps(reply)[:400]}")


def hello_failure_context(chrome, session):
    """Safe connector snapshot summary (port state + disconnect message, no payloads) so a
    discovery failure such as 'Specified native messaging host not found' is not blamed on the
    host process."""
    try:
        snap = chrome.js(session, "otp23.snapshot()", timeout=10) or {}
    except Exception as error:
        return f"snapshot unavailable ({type(error).__name__})"
    disconnect = snap.get("disconnect") or {}
    return scrub("connector snapshot: portOpen=%s disconnect.message=%r" % (
        snap.get("portOpen"), str(disconnect.get("message", ""))[:200]))


def check_registration(manifest_dir, binary):
    manifest = pathlib.Path(manifest_dir) / f"{HOST_NAME}.json"
    if not manifest.is_file():
        raise SystemExit(
            f"not registered: {manifest} is missing; run\n  ./scripts/macos-register.sh")
    document = json.loads(manifest.read_bytes().decode("utf-8"))
    require(isinstance(document, dict) and document.get("name") == HOST_NAME,
            f"manifest names {document.get('name')!r}")
    origins = document.get("allowed_origins")
    expected_origin = read_identity()["origin"]
    require(origins == [expected_origin],
            f"allowed_origins must be exactly [{expected_origin!r}], saw {origins!r}")
    if pathlib.Path(document.get("path", "")).resolve() != binary.resolve():
        raise SystemExit(
            f"manifest {manifest} points at {document.get('path')!r}, not the packaged binary "
            f"{binary}; re-run ./scripts/macos-register.sh")
    return manifest


# ---- run -------------------------------------------------------------------------------------------


FINISHED = "FINISHED"


def run_driver(args, out):
    try:
        identity = read_identity()
        require(identity["host_name"] == HOST_NAME, "identity.rs plain host name drifted")
        require(identity["origin"] == "chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/",
                "origin drifted")
        binary = (args.app or ROOT / "target" / "macos-poc" / "VelesOtp23PoC.app") / PACKAGED_BINARY
        require(binary.is_file(),
                f"{binary} missing; run  cargo build --release  then  ./scripts/macos-package.sh")
        manifest = check_registration(args.manifest_dir, binary)
    except (AssertionError, SystemExit, OSError) as error:
        message = str(getattr(error, "code", None)) if not str(error) else str(error)
        print(f"error: {message}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    trace = out.with_suffix(".trace.log")
    trace.unlink(missing_ok=True)
    result = {
        "host_name": HOST_NAME, "manifest": str(manifest),
        "host_binary": str(binary), "host_binary_sha256": file_sha256(binary),
        "chrome_path": args.chrome, "sizes": SIZES, "repeat_4096": args.repeat_4096,
        "diag_scan_only": args.diag_scan_only,
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_revision": source_revision(),
        "macos_version": platform.mac_ver()[0] or platform.platform(),
        "python_version": sys.version.split()[0], "results": [], "result": "FAIL",
    }
    pre_existing = {row["pid"] for row in host_processes(parse_ps(ps_snapshot()))}
    result["pre_existing_host_processes_untouched"] = sorted(pre_existing)
    chrome = None

    def note(case, detail):
        result["results"].append({"case": case, **detail})
        print(f"[ ok ] {case}")

    try:
        chrome = ChromeRun(chrome_path=args.chrome, extra_env={"VELES_OTP23_TRACE": str(trace)},
                           manifest_source=manifest)
        result["chrome_version"] = chrome.version.get("product")
        result["chrome_pid"] = chrome.proc.pid
        ext_id = chrome.cdp.call("Extensions.loadUnpacked", {"path": str(EXT_DIR)})["id"]
        require(ext_id == identity["extension_id"],
                f"extension id {ext_id} != fixed {identity['extension_id']}")
        _, session = chrome.open_page(f"chrome-extension://{ext_id}/connector.html")
        for _ in range(50):
            if chrome.js(session, "typeof window.otp23 === 'object'"):
                break
            time.sleep(0.1)
        js = chrome.js
        js(session, f"otp23.connect({json.dumps(HOST_NAME)}); 1")
        ready = js(session, "otp23.hello()")
        if ready.get("type") != "ready":
            deny_or_raise(ready, "hello (%s)" % hello_failure_context(chrome, session))
        result["ready"] = {key: ready.get(key) for key in ("bridge_version", "platform", "api_version")}
        rows = parse_ps(ps_snapshot())
        fresh = [row for row in host_processes(rows) if row["pid"] not in pre_existing]
        require(len(fresh) == 1, f"expected exactly one new host process, saw {fresh}")
        pid = fresh[0]["pid"]
        chain = ancestry(rows, pid)
        require(any(link["pid"] == chrome.proc.pid for link in chain),
                f"Chrome {chrome.proc.pid} is not an ancestor of the host: {chain}")
        windows = windows_owned_by(pid)
        if windows is not None:
            require(not windows, f"host owns on-screen windows: {windows}")
        result["host_pid"] = pid
        note("host-launched-headless", {"pid": pid, "ancestor_chain": chain,
                                        "on_screen_windows": windows})

        def poll_devices(timeout):
            devices, deadline = {}, time.monotonic() + timeout
            while len(devices) < 2 and time.monotonic() < deadline:
                for event in js(session, "otp23.drainEvents()"):
                    message = event["message"]
                    if message["type"] == "device_found":
                        devices[message["device_handle"]] = message
                time.sleep(0.25)
            return devices

        started = js(session, f"otp23.startScan({json.dumps(VELES_SERVICE)})")
        if started.get("status") != "started":
            deny_or_raise(started, "startScan(veles)")
        result["scan_handle_seen"] = started.get("scan_handle")
        if args.diag_scan_only:
            devices = poll_devices(20)
            stopped = js(session, f"otp23.stopScan({json.dumps(started['scan_handle'])})")
            require(stopped.get("status") == "stopped", stopped)
            require(devices, "no phone advertised the Veles service within the scan window")
            note("diagnostic-scan", {"devices_found": len(devices),
                                     "label_lengths": sorted({len(m.get("label", "")) for m in devices.values()})})
            done = js(session, "otp23.shutdown()")
            require(done.get("type") == "shutdown_complete", done)
            require(wait_gone(pid, 8) is not None, "host survived shutdown (diagnostic)")
            note("shutdown", {"mode": "diag"})
        else:
            devices = poll_devices(30)
            require(len(devices) >= 2,
                    f"only {len(devices)} phone(s) advertise the Veles service; BOTH OTP-06 "
                    "peripherals must be advertising (Veles app on two Android phones)")
            stopped = js(session, f"otp23.stopScan({json.dumps(started['scan_handle'])})")
            require(stopped.get("status") == "stopped", stopped)
            connected = js(session, "Promise.all("
                            + json.dumps([{"device_handle": h} for h in list(devices)[:2]])
                            + ".map(t => otp23.connectDevice(t)))")
            if not all(c.get("type") == "connected" for c in connected):
                deny_or_raise(connected, "connectDevice")
            handles = [c["connection_handle"] for c in connected]
            require(len(set(handles)) == 2, "connection handles crossed")
            note("two-independent-connects", {"handles": 2})

            def exchange(active, size, seeds):
                """Concurrent sends on the ACTIVE handles; per-handle digest must match, no payload logged."""
                js(session, "otp23.drainEvents(); 1")
                expected_b64 = {h: base64.b64encode(echo(fixture(size, seed))).decode()
                                for h, seed in zip(active, seeds)}
                expected_digest = {h: hashlib.sha256(echo(fixture(size, seed))).hexdigest()
                                   for h, seed in zip(active, seeds)}
                exprs = ", ".join(js_send_expression(h, size, seed) for h, seed in zip(active, seeds))
                replies = js(session, f"Promise.all([{exprs}])")
                require(all(r.get("type") == "sent" for r in replies), replies)
                require(len({r["request_id"] for r in replies}) == len(replies), "request ids reused")
                received, deadline = {}, time.monotonic() + 12
                while len(received) < len(expected_digest) and time.monotonic() < deadline:
                    for event in js(session, "otp23.drainEvents()"):
                        message = event["message"]
                        require(message["type"] != "disconnected", message)
                        if message["type"] == "message":
                            handle = message["connection_handle"]
                            require(handle in expected_b64
                                    and message["record_base64"] == expected_b64[handle],
                                    f"foreign handle or byte mismatch on {handle}")
                            require(handle not in received, "duplicate response")
                            received[handle] = hashlib.sha256(
                                base64.b64decode(message["record_base64"])).hexdigest()
                    time.sleep(0.05)
                require(len(received) == len(expected_digest),
                        f"only {len(received)}/{len(expected_digest)} replies within 12 s "
                        f"for {size}-byte records")
                require(all(received[h] == expected_digest[h] for h in expected_digest),
                        "per-handle digest mismatch")
                return {"bytes_per_phone": size, "seeds": seeds,
                        "received_sha256": [received[h] for h in active]}

            for index, size in enumerate(SIZES + [4096] * args.repeat_4096):
                note(f"record-{size}-{index}", exchange(handles, size, [SEED_BASE[0] + index, SEED_BASE[1] + index]))
            disconnected = js(session, f"otp23.disconnectDevice({json.dumps(handles[0])})")
            require(disconnected.get("type") == "disconnected", disconnected)
            rejected = js(session, js_send_expression(handles[0], 4, 5))
            if rejected.get("code") != "unknown_handle":
                deny_or_raise(rejected, "send on a disconnected handle")
            note("disconnect-one-rejects-send", {"code": rejected.get("code")})
            note("survivor-exchanges-4096", exchange(handles[1:], 4096, [219]))
            done = js(session, "otp23.shutdown()")
            require(done.get("type") == "shutdown_complete", done)
            require(wait_gone(pid, 8) is not None, "owned host survived shutdown with a real link")
            note("shutdown", {"mode": "matrix"})
            # Port-closure path: fresh port, active scan, disconnect while it runs; process must exit.
            js(session, f"otp23.connect({json.dumps(HOST_NAME)}); 1")
            ready = js(session, "otp23.hello()")
            require(ready.get("type") == "ready", ready)
            fresh = [row for row in host_processes(parse_ps(ps_snapshot()))
                     if row["pid"] not in pre_existing and row["pid"] != pid]
            require(len(fresh) == 1, f"relaunch: {fresh}")
            pid2 = fresh[0]["pid"]
            windows2 = windows_owned_by(pid2)
            if windows2 is not None:
                require(not windows2, f"relaunched host owns on-screen windows: {windows2}")
            started = js(session, f"otp23.startScan({json.dumps(VELES_SERVICE)})")
            require(started.get("status") == "started", started)
            js(session, "otp23.closePort(); 1")
            require(wait_gone(pid2, 8) is not None, "owned host survived Chrome port closure")
            note("port-close", {"pid": pid2})
            parsed = parse_trace(trace)
            observed = set(parsed["mtu_by_conn"]) | set(parsed["budget_by_conn"])
            require(len(observed) == 2,
                    f"expected exactly 2 connection observations, saw {sorted(observed)}")
            require(all(185 <= mtu <= 517 for mtu in parsed["mtu_by_conn"].values()), "implausible MTUs")
            require(all(182 <= b["attribute_budget"] <= 512 and b["payload_budget"] == b["attribute_budget"] - 4
                        for b in parsed["budget_by_conn"].values()), "implausible attribute budgets")
            result["negotiation"] = {
                "source": "host trace (VELES_OTP23_TRACE); keyed by the host connection token only; "
                          "no per-handle association is inferred",
                "per_conn": {str(token): {**({"mtu": parsed["mtu_by_conn"][token]}
                                              if token in parsed["mtu_by_conn"] else {}),
                                          **parsed["budget_by_conn"].get(token, {}),
                                          "source": parsed["mtu_source_by_conn"].get(token, "unknown")}
                             for token in sorted(observed)},
                "mtu_source_legend": {
                    MTU_NEGOTIATED: "WinRT trace line reports the negotiated MTU measurement",
                    BUDGET_MEASURED_MAX_WRITE: "macOS corebluetooth: no ATT MTU is observed or invented; the "
                                               "measured max write-without-response length W is a backend limit; a "
                                               "budget exists only for W >= 512 (smaller W is refused)",
                    MTU_INFERRED_MAX_WRITE: "legacy host trace only: MTU derived as max write + 3, NOT a "
                                            "negotiated measurement",
                },
                "fragment_attribute_rule": "min(ATT MTU - 3, 512)  (macOS: 512 when max write >= 512, else the link is refused)",
                "fragment_payload_rule": "attribute - 4",
                "trace_sends": len(parsed["sends"]),
                "trace_replies": len(parsed["replies"])}
        result["result"] = FINISHED
    except PermissionDenied as error:
        result["permission_denied"] = True
        result["failure"] = str(error)
        print(TCC_MESSAGE, file=sys.stderr)
    except Exception:
        result["failure"] = traceback.format_exc()
    finally:
        if chrome:
            chrome.close()
        survivors = [row["pid"] for row in host_processes(parse_ps(ps_snapshot()))
                     if row["pid"] not in pre_existing]
        if survivors:
            result["result"], result["owned_process_survived"] = "FAIL", survivors
        if result["result"] == FINISHED:
            result["result"] = "PASS"
        result["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out.write_text(scrub(json.dumps(result, indent=2)), encoding="utf-8")
        print(f"result={result['result']} -> {out}")
    return 0 if result["result"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="two-phone physical matrix (the default command)")
    run.add_argument("--out", required=True)
    run.add_argument("--diag-scan-only", action="store_true")
    run.add_argument("--repeat-4096", type=int, default=3)
    run.add_argument("--chrome", default=DEFAULT_CHROME)
    run.add_argument("--app", type=pathlib.Path, default=None,
                     help="VelesOtp23PoC.app root (default target/macos-poc/VelesOtp23PoC.app)")
    run.add_argument("--manifest-dir", type=pathlib.Path, default=MANIFEST_DIR_DEFAULT)
    register = sub.add_parser("register", help="write the user-level native-messaging manifest")
    register.add_argument("--app", type=pathlib.Path, default=APP_DEFAULT)
    register.add_argument("--manifest-dir", type=pathlib.Path, default=MANIFEST_DIR_DEFAULT)
    unregister = sub.add_parser("unregister", help="remove (or restore) only the owned manifest")
    unregister.add_argument("--manifest-dir", type=pathlib.Path, default=MANIFEST_DIR_DEFAULT)
    argv = sys.argv[1:]
    if argv and argv[0] not in ("run", "register", "unregister"):
        argv = ["run"] + argv
    args = parser.parse_args(argv)
    if args.command == "register":
        install_register(args.app, args.manifest_dir)
    elif args.command == "unregister":
        install_unregister(args.manifest_dir)
    else:
        sys.exit(run_driver(args, pathlib.Path(args.out).resolve()))


if __name__ == "__main__":
    main()