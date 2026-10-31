"""Pure logic for scenario_matrix.py (OTP-23 lifecycle scenarios and M6 two-central overlap).

No Chrome, no Bluetooth, no platform imports: everything here is unit-testable on any OS with the
standard library (Python 3.9 compatible). It never declares matrix acceptance; the adjudicator only
merges two per-central result files into a verdict the parent records.

Safe by construction: results carry sizes, SHA-256 digests, stages, timestamps and opaque short
handle prefixes only. No record bytes, no Bluetooth addresses, no platform hints, no device labels
(labels are hashed).
"""

import base64
import hashlib
import json
import math
import pathlib
import re
import struct
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

STATUSES = ("PASS", "FAIL", "BLOCKED", "PARTIAL")
# Safe error classes a scenario may report; never free text from the platform.
ERROR_CLASSES = (
    "none", "insufficient_phones", "ambiguous_target", "target_not_found", "permission_denied",
    "unsupported", "environment", "host_error", "timeout", "assertion", "requires_operator",
    "criterion_conflict",
)
DENIAL_HINTS = ("denied", "consent", "permission", "unauthor", "not_authorized")
SIZE = 4096
MIN_EXCHANGES = 20
MAX_FRAME_BYTES = 65536
SEED_BASE = {"windows": 41, "mac": 173}  # per-platform default; any value is overridable
SEED_STEP = 13  # odd, so seeds are distinct modulo 256 for 256 consecutive exchanges
NOTIFY_CODES_REASON = (
    "phone transport evidence (otp23-transport-evidence.json) is collected separately and is not adjudicated "
    "here; this requirement stays BLOCKED in this adjudicator"
)


# ---- result records --------------------------------------------------------------------------


def record(case_id, title, expected, actual, status, error_class="none", **extra):
    """One result: always expected / actual / status, plus a safe error class."""
    if status not in STATUSES:
        raise ValueError(f"bad status {status!r}")
    if error_class not in ERROR_CLASSES:
        raise ValueError(f"bad error class {error_class!r}")
    return dict({"id": case_id, "title": title, "expected": expected, "actual": actual,
                 "status": status, "error_class": error_class}, **extra)


def classify_failure(reply):
    """Safe class of a failed host reply: denial, otherwise host_error. Never copies free text."""
    blob = json.dumps(reply or {}).lower()
    if any(hint in blob for hint in DENIAL_HINTS):
        return "permission_denied"
    if isinstance(reply, dict) and reply.get("code") == "timeout":
        return "timeout"
    return "host_error"


def safe_reply(reply):
    """Only the structured, non-identifying fields of a host reply."""
    if not isinstance(reply, dict):
        return {"type": None}
    return {k: reply.get(k) for k in ("type", "code", "recoverability", "stage", "status",
                                      "synthesized") if k in reply}


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def label_hash(label):
    """Device labels are hashed, never recorded; None stays None."""
    return None if label is None else sha256_hex(label.encode("utf-8"))[:16]


def short_handle(handle):
    return None if not isinstance(handle, str) else sha256_hex(handle.encode("utf-8"))[:12]


def file_sha256(path):
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 16), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def source_revision(repo=None):
    """Git provenance or explicit `available: false` with stage and bounded reason (archives have no
    .git). Never a guessed commit."""
    repo = pathlib.Path(repo) if repo else ROOT.parent.parent

    def unavailable(stage, reason):
        return {"available": False, "stage": stage, "reason": " ".join(str(reason).split())[:200]}

    def git(stage, *args):
        try:
            done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                  text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
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


# The lazily imported platform observer/helper modules an acceptance run must pin per platform.
# The names are what scenario_matrix.py imports; the hashes are read from each module's actual
# `__file__` (the producer bytes on the measuring machine), never from a guessed checkout.
PLATFORM_OBSERVER_MODULE_NAMES = {
    "windows": ("chrome_validate",),
    "mac": ("macos_poc", "macos_background_support", "macos_cdp", "macos_install"),
}


def source_hashes(platform=None, also_loaded=()):
    """SHA-256 of the exact files this driver and the connector run from.

    Every lazily imported platform observer/helper an acceptance run on `platform` is expected to
    load is pinned from its actual `__file__` (the producer bytes the run really executes — B-4); a
    required name that is not in sys.modules is reported in `observer_modules_missing` — recorded,
    never silently dropped."""
    here = pathlib.Path(__file__).resolve().parent
    files = [here / "scenario_matrix.py", here / "scenario_matrix_core.py",
             ROOT / "extension" / "connector.js", ROOT / "extension" / "manifest.json",
             ROOT / "src" / "identity.rs"]
    hashes = {path.name: file_sha256(path) for path in files}
    pinned, missing = {}, []
    for name in tuple(PLATFORM_OBSERVER_MODULE_NAMES.get(platform, ())) + tuple(also_loaded):
        module = sys.modules.get(name)
        path = getattr(module, "__file__", None)
        digest = file_sha256(path) if module is not None and path else None
        if digest is None:
            missing.append(name)  # not loaded, unreadable or hash failed: missing, NEVER a None digest
        else:
            pinned[name + ".py"] = digest
    hashes["platform_observer_sha256"] = pinned
    hashes["observer_modules_missing"] = missing
    return hashes


_REQUIRED_DIGEST_HEX = frozenset("0123456789abcdef")


def observer_pin_problems(platform, source_sha256):
    """B-4 fail-closed validation of run-metadata observer pins (portable/pure).

    `source_sha256` is the run metadata `source_sha256` document. A run may only measure when
    `platform_observer_sha256` is a dict whose key set is EXACTLY the platform's required
    `<module>.py` entries, each carrying a valid 64-hex SHA-256 digest, AND
    `observer_modules_missing` is an empty list. Anything else (omitted/empty map, None/malformed
    digests, missing or unexpected pinned declarations, a non-list or non-empty missing list,
    unknown platform) is a problem that refuses measurement."""
    names = PLATFORM_OBSERVER_MODULE_NAMES.get(platform)
    if names is None:
        return [f"unknown platform {platform!r}: no declared platform observer modules"]
    required = [name + ".py" for name in names]
    if not isinstance(source_sha256, dict):
        return [f"run metadata source_sha256 is not a document ({type(source_sha256).__name__})"]
    pinned = source_sha256.get("platform_observer_sha256")
    if not isinstance(pinned, dict):
        return ["run metadata carries no platform_observer_sha256 map: observer bytes unpinned (B-4)"]
    problems = []
    for key in pinned:
        if key not in required:
            problems.append(f"unexpected pinned module declaration {key!r}; required exactly "
                            f"{sorted(required)}")
    for key in required:
        digest = pinned.get(key)
        if not isinstance(digest, str) or len(digest) != 64 \
                or any(char not in _REQUIRED_DIGEST_HEX for char in digest.lower()):
            problems.append(f"platform_observer_sha256[{key!r}] missing or malformed "
                            f"({digest!r}): must be a 64-hex sha256 string")
    missing = source_sha256.get("observer_modules_missing")
    if not isinstance(missing, list) or missing:
        problems.append(f"observer_modules_missing is {missing!r}: a required observer was not "
                        "loaded/pinned; measurement refuses (trust the map, not the missing list)")
    return problems


# ---- native messaging framing (u32le length || UTF-8 JSON) ------------------------------------


def encode_frame(message):
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if not 1 <= len(body) <= MAX_FRAME_BYTES:
        raise ValueError("frame length out of range")
    return struct.pack("<I", len(body)) + body


class FrameDecoder:
    """Incremental decoder for the host's stdout. `eof()` reports how the stream ended:
    'clean' (frame boundary), 'truncated_header', 'truncated_payload', or 'bad_length'."""

    def __init__(self):
        self.buffer = b""
        self.broken = None

    def feed(self, data):
        self.buffer += data
        frames = []
        while self.broken is None:
            if len(self.buffer) < 4:
                break
            (length,) = struct.unpack("<I", self.buffer[:4])
            if length == 0 or length > MAX_FRAME_BYTES:
                self.broken = "bad_length"
                break
            if len(self.buffer) < 4 + length:
                break
            body, self.buffer = self.buffer[4:4 + length], self.buffer[4 + length:]
            try:
                frames.append(json.loads(body.decode("utf-8")))
            except (UnicodeDecodeError, ValueError):
                self.broken = "bad_json"
        return frames

    def eof(self):
        if self.broken:
            return self.broken
        if not self.buffer:
            return "clean"
        return "truncated_header" if len(self.buffer) < 4 else "truncated_payload"


def request_frame(kind, request_id, api_version=None, **fields):
    body = {"envelope_version": 1, "type": kind, "request_id": request_id}
    body.update(fields)
    if kind == "hello":
        body["api_versions"] = [1]
    elif api_version is not None:
        body["api_version"] = api_version
    return encode_frame(body)


# ---- safe host trace ----------------------------------------------------------------------------

_STAMP = re.compile(r"\[\s*(\d+)ms pid=(\d+)\]\s+(.*)$")


def parse_trace(text):
    """Lifecycle view of the host's safe trace: ordered in/out message kinds and the exit line.
    Only structured fields are read; the trace never carries record bytes."""
    steps, exit_info = [], None
    released, disconnect_requested, joined = [], [], []
    for line in text.splitlines():
        stamp = _STAMP.match(line)
        if not stamp:
            continue
        at_ms, body = int(stamp.group(1)), stamp.group(3)
        direction = "in" if body.startswith("in ") else "out" if body.startswith("out ") else None
        if direction:
            kind = re.search(r"type=(\w+)", body)
            status = re.search(r"status=(\w+)", body)
            steps.append({"at_ms": at_ms, "dir": direction, "type": kind.group(1) if kind else None,
                          "status": status.group(1) if status else None,
                          "framing_violation": "framing violation" in body})
            if "framing violation" in body:
                steps[-1]["type"] = "framing_violation"
            continue
        if "framing violation" in body:
            steps.append({"at_ms": at_ms, "dir": "in", "type": "framing_violation", "status": None,
                          "framing_violation": True})
            continue
        released_conn = re.match(r"conn (\d+) released\b", body)
        if released_conn:
            released.append({"at_ms": at_ms, "conn": int(released_conn.group(1))})
            continue
        requested_conn = re.match(r"conn (\d+) disconnect requested epoch_ms=(\d+)", body)
        if requested_conn:
            disconnect_requested.append({"at_ms": at_ms, "epoch_ms": int(requested_conn.group(2))})
            continue
        joined_match = re.match(r"shutdown joined (\d+)/(\d+) within (\d+)ms", body)
        if joined_match:
            joined.append({"at_ms": at_ms, "joined": int(joined_match.group(1)),
                           "total": int(joined_match.group(2)), "within_ms": int(joined_match.group(3))})
            continue
        done = re.match(r"exit code=(-?\d+) stdout_drained=(\w+)", body)
        if done:
            exit_info = {"at_ms": at_ms, "code": int(done.group(1)),
                         "stdout_drained": done.group(2) == "true"}
    return {"steps": steps, "exit": exit_info, "released": released,
            "disconnect_requested": disconnect_requested, "joined": joined}


def trace_mechanism(parsed):
    """Which teardown path the trace shows: 'shutdown' (a shutdown request was received and
    answered), 'stdin_closed' (an exit with no shutdown request), or 'no_exit' (host never logged
    one)."""
    steps = parsed["steps"]
    got_shutdown = any(s["dir"] == "in" and s["type"] == "shutdown" for s in steps)
    sent_complete = any(s["dir"] == "out" and s["type"] == "shutdown_complete" for s in steps)
    if parsed["exit"] is None:
        return "no_exit"
    return "shutdown" if got_shutdown and sent_complete else "stdin_closed"


def shutdown_complete_is_last_out(parsed):
    outs = [s for s in parsed["steps"] if s["dir"] == "out"]
    return bool(outs) and outs[-1]["type"] == "shutdown_complete" \
        and sum(1 for s in outs if s["type"] == "shutdown_complete") == 1


def check_teardown(parsed, expected, require_drained=None, min_released=None, require_join=False):
    """Problems (empty = ok) for one lifecycle path; the paths stay distinguishable so a Chrome
    port.disconnect / tab close / stdin EOF is never credited as an explicit shutdown, or the reverse.

    expected:
      'shutdown'      shutdown request answered by shutdown_complete (last host output), exit 0, stdout drained
      'stdin_closed'  host saw stdin EOF itself: exit line present, code 0, no shutdown handshake
      'disconnect'    Chrome closed the port / tab: the host itself completed the teardown (its own exit line,
                      code 0) — a host that vanished without an exit line was "terminated, possibly by Chrome"
                      (the ~2 s lingering-host kill), which fails rather than passes
    `stdout_drained` is required where a reader keeps consuming stdout (shutdown, direct stdin EOF); when
    Chrome tears both pipes down it is recorded but cannot be demanded (`require_drained` overrides).
    `min_released`: require that at least N traced `conn N released` lines exist, all before the exit line,
    and that every connection was joined by the bounded shutdown join (joined M/M, M == M) before exit."""
    if require_drained is None:
        require_drained = expected == "shutdown"
    problems = []
    mechanism = trace_mechanism(parsed)
    allowed = ("stdin_closed",) if expected == "disconnect" else (expected,)
    if mechanism not in allowed:
        problems.append(f"trace mechanism {mechanism!r}, expected {' or '.join(allowed)!r}")
    info = parsed["exit"]
    if info is None:
        problems.append("host trace has no exit line")
    else:
        if info["code"] != 0:
            problems.append(f"exit code {info['code']} != 0")
        if require_drained and not info["stdout_drained"]:
            problems.append("stdout not drained before exit")
    if expected == "shutdown" and not shutdown_complete_is_last_out(parsed):
        problems.append("shutdown_complete was not the last (single) host output")
    if expected != "shutdown" and any(s["type"] == "shutdown_complete" for s in parsed["steps"]):
        problems.append("shutdown_complete observed on a non-shutdown path")
    if min_released is not None:
        exit_at = info["at_ms"] if info else None
        late = [r for r in parsed["released"] if exit_at is None or r["at_ms"] > exit_at]
        if late:
            problems.append(f"{len(late)} connection release(s) at or after the exit line")
        if len(parsed["released"]) < min_released:
            problems.append(f"only {len(parsed['released'])} connection release line(s), expected >= {min_released}")
        elif len({r["conn"] for r in parsed["released"]}) < min_released:
            problems.append(
                f"release lines cover only {sorted({r['conn'] for r in parsed['released']})} distinct "
                f"connection id(s), expected >= {min_released}: duplicate release lines for one "
                "connection cannot satisfy the gate — every connection of the run must release (I-3)")
        if require_join and not parsed["joined"]:
            # Only the Windows backend (winrt.rs) traces the bounded shutdown join; the macOS backend has
            # no such line, so demanding one there would add an arbitrary platform gate, not an RFC criterion.
            problems.append("trace has no shutdown joined N/N line")
        elif require_join and parsed["joined"]:
            last_join = parsed["joined"][-1]
            if last_join["joined"] != last_join["total"]:
                problems.append(f"join {last_join['joined']}/{last_join['total']}: a close worker did not finish")
            if (exit_at is not None and last_join["at_ms"] > exit_at) \
                    or (parsed["released"] and last_join["at_ms"] < max(r["at_ms"] for r in parsed["released"])):
                problems.append("shutdown join did not cover every released connection before exit")
    return problems


# ---- connected-phase window/endpoint probe summaries (pure; unit-tested on any platform) ----------


def ntohs(port_netbo):
    return ((port_netbo & 0x00FF) << 8) | ((port_netbo & 0xFF00) >> 8)


def parse_tcp4_table(buf):
    """MIB_TCPTABLE_OWNER_PID bytes -> rows; raises ValueError on short/misaligned input."""
    if len(buf) < 4:
        raise ValueError("tcp4 buffer too short")
    count = struct.unpack_from("<I", buf, 0)[0]
    if len(buf) < 4 + 24 * count:
        raise ValueError("tcp4 buffer truncated")
    return [{"local_port": ntohs(local_port), "remote_port": ntohs(remote_port), "owner": owner,
             "state": state}
            for state, _la, local_port, _ra, remote_port, owner
            in (struct.unpack_from("<6I", buf, 4 + 24 * i) for i in range(count))]


def parse_tcp6_table(buf):
    """MIB_TCP6ROW_OWNER_PID bytes (56-byte rows: local addr(16)+scope+port, remote addr(16)+scope+port,
    state, pid) -> rows."""
    if len(buf) < 4:
        raise ValueError("tcp6 buffer too short")
    count = struct.unpack_from("<I", buf, 0)[0]
    if len(buf) < 4 + 56 * count:
        raise ValueError("tcp6 buffer truncated")
    rows = []
    for i in range(count):
        _la, local_scope, local_port, _ra, remote_scope, remote_port, state, owner = \
            struct.unpack_from("<16sII16sIIII", buf, 4 + 56 * i)
        rows.append({"local_port": ntohs(local_port), "remote_port": ntohs(remote_port),
                     "owner": owner, "state": state})
    return rows


def parse_udp4_table(buf):
    """MIB_UDPROW_OWNER_PID bytes (local addr+port, pid) -> rows."""
    if len(buf) < 4:
        raise ValueError("udp4 buffer too short")
    count = struct.unpack_from("<I", buf, 0)[0]
    if len(buf) < 4 + 12 * count:
        raise ValueError("udp4 buffer truncated")
    return [{"local_port": ntohs(local_port), "owner": owner}
            for _la, local_port, owner in (struct.unpack_from("<3I", buf, 4 + 12 * i) for i in range(count))]


def parse_udp6_table(buf):
    """MIB_UDP6ROW_OWNER_PID bytes (28-byte rows: local addr(16)+scope+port, pid) -> rows."""
    if len(buf) < 4:
        raise ValueError("udp6 buffer too short")
    count = struct.unpack_from("<I", buf, 0)[0]
    if len(buf) < 4 + 28 * count:
        raise ValueError("udp6 buffer truncated")
    return [{"local_port": ntohs(local_port), "owner": owner}
            for _la, _scope, local_port, owner
            in (struct.unpack_from("<16sIII", buf, 4 + 28 * i) for i in range(count))]


TCP_TABLE_OWNER_PID_ALL, UDP_TABLE_OWNER_PID = 5, 1
AF_INET, AF_INET6 = 2, 23
ERROR_INSUFFICIENT_BUFFER = 122


def read_family_with_retry(read_once, attempts=3):
    """Turn one API family into (parsed_rows | "unavailable") without ever inventing a zero.

    `read_once(size)` returns (buffer_bytes_or_None, size_hint, need_retry). A final unreadable family
    stays the string "unavailable" so the summariser can refuse the sample instead of calling it empty."""
    size = 0
    for _ in range(attempts):
        buf, size, need_retry = read_once(size)
        if buf is not None:
            return buf
        if not need_retry:
            break
        size = max(size * 2, 65536)
    return "unavailable"


UNAVAILABLE = "unavailable"


def endpoints_from_table(tables, pid):
    """Endpoints owned by `pid`, sliced from ONE immutable endpoint-table snapshot.

    B-1: the caller reads the table once and passes the SAME object for the Chrome control, the host
    and every wrapper pid — no hidden re-read and no shared global cache that can drift. A family the
    platform could not read stays "unavailable" here and in every slice, never a fabricated empty list."""
    out = {}
    for family, fields in (("tcp4", ("local_port", "remote_port", "state")),
                           ("tcp6", ("local_port", "remote_port", "state")),
                           ("udp4", ("local_port",)), ("udp6", ("local_port",))):
        rows = tables.get(family) if isinstance(tables, dict) else None
        if not isinstance(rows, list):
            out[family] = UNAVAILABLE
        else:
            out[family] = sorted(tuple(row[name] for name in fields)
                                 for row in rows if row["owner"] == pid)
    return out


def assemble_hygiene_probe(host_pids, chrome_pid, cdp_port, read_endpoint_table, census_of,
                           session_of, alive_of, observer_pid, observer_session,
                           active_console_session):
    """Assemble one connected-phase probe from injected platform observers (portable/pure).

    The platform drivers pass their real read functions as seams; this function owns the SEMANTICS:
    `read_endpoint_table` is called EXACTLY ONCE and the returned snapshot object is sliced for the
    Chrome CDP-listener control, the host and every wrapper pid (B-1), so availability, the positive
    controls and the absence observations all come from the same census, and per-sample API reads are
    provable from the read count. The output shape is byte-identical to the pre-B-1 probes so existing
    evidence keeps parsing."""
    tables = read_endpoint_table()  # exactly ONE read backs the whole sample
    chrome_windows = census_of(chrome_pid)
    chrome_eps = endpoints_from_table(tables, chrome_pid)
    listener = None
    for family in ("tcp4", "tcp6"):
        rows = chrome_eps[family]
        if rows == UNAVAILABLE:
            continue  # the control honestly cannot be asserted from an unread table
        for local_port, remote_port, state in rows:
            if cdp_port is not None and local_port == cdp_port and remote_port == 0:
                listener = {"family": family, "local_port": local_port}
    per_pid = {}
    for pid in list(host_pids) + [chrome_pid]:
        windows = chrome_windows if pid == chrome_pid else census_of(pid)
        endpoints = chrome_eps if pid == chrome_pid else endpoints_from_table(tables, pid)
        if pid == chrome_pid:  # chrome keeps its raw message-only count (no truncation gating, as before)
            entry = {"top_visible": len(windows["top_visible"]),
                     "top_hidden": len(windows["top_hidden"]),
                     "message_only": len(windows["message_only"]),
                     "children": windows["children"],
                     "tcp4": endpoints["tcp4"], "tcp6": endpoints["tcp6"],
                     "udp4": endpoints["udp4"], "udp6": endpoints["udp6"],
                     "session": session_of(pid), "pid_alive": alive_of(pid)}
        else:
            entry = {"top_visible": len(windows["top_visible"]),
                     "top_hidden": len(windows["top_hidden"]),
                     "message_only": (UNAVAILABLE if windows["message_only_truncated"]
                                      else len(windows["message_only"])),
                     "message_only_truncated": windows["message_only_truncated"],
                     "children": windows["children"],
                     "top_hidden_classes": sorted({w["class"] for w in windows["top_hidden"]}),
                     "message_only_classes": sorted({w["class"] for w in windows["message_only"]}),
                     "tcp4": endpoints["tcp4"], "tcp6": endpoints["tcp6"],
                     "udp4": endpoints["udp4"], "udp6": endpoints["udp6"],
                     "session": session_of(pid), "pid_alive": alive_of(pid)}
        per_pid[str(pid)] = entry
    return {"mode": "sampled", "per_pid": per_pid,
            "observer": {"pid": observer_pid, "session": observer_session},
            "active_console_session": active_console_session,
            "endpoint_families_available": {family: (isinstance(tables.get(family), list))
                                            for family in ("tcp4", "tcp6", "udp4", "udp6")},
            "controls": {"chrome_visible_top_level": len(chrome_windows["top_visible"]),
                         "chrome_cdp_listener_present": listener is not None,
                         "chrome_cdp_listener": listener}}


def summarise_probe(probe, host_pid, wrapper_pids=(), required_host_fields=None):
    """Pure summary of one connected-phase probe (driver supplies platform data; testable anywhere).

    `probe` carries per-pid window/endpoint observations plus mandatory positive controls; counts may be
    integers or an explicit "unavailable" marker (never a fabricated zero). Problems:
      - the HOST pid owns any window of any kind (visible, hidden, message-only, children), or any endpoint;
      - a WRAPPER pid shows a visible window (a never-visible wrapper console window is a documented
        criterion conflict, surfaced via wrapper_hidden_window_max — see criterion_conflict());
      - a positive control is missing in that sample (the absence is then unverifiable, never a zero);
      - any REQUIRED host field is "unavailable": the sample cannot support an absence claim at all.
    Windows requires all four window kinds and all four endpoint families; macOS requires the any-layer
    census, TCP endpoints and proof the host pid was alive at sample time (required_host_fields overrides)."""
    problems, host_windows, wrapper_windows, host_endpoints, controls = [], {}, {}, {}, {}
    per_pid = probe.get("per_pid", {})
    host_entry = per_pid.get(str(host_pid), {})
    if required_host_fields is None:
        required_host_fields = DEFAULT_WINDOWS_HOST_FIELDS
    for kind in ("top_visible", "top_hidden", "message_only", "children"):
        host_windows[kind] = host_entry.get(kind, "unavailable")
        if isinstance(host_windows[kind], int) and host_windows[kind] > 0:
            problems.append(f"host owns {host_windows[kind]} {kind} window(s)")
    # B-2: the macOS any-layer census is an ownership count, not just availability data — a host
    # window at ANY layer (e.g. an off-screen window) fails exactly like a counted Windows window.
    all_layers = host_entry.get("all_layers")
    if isinstance(all_layers, int) and not isinstance(all_layers, bool) and all_layers > 0:
        problems.append(f"host owns {all_layers} window(s) at any layer (all_layers)")
    for family in ("tcp4", "tcp6", "udp4", "udp6"):
        endpoints = host_entry.get(family, "unavailable")
        host_endpoints[family] = endpoints
        if isinstance(endpoints, int) and endpoints > 0:
            problems.append(f"host owns {endpoints} {family.upper()} endpoint(s)")
        if isinstance(endpoints, list) and endpoints:
            problems.append(f"host owns {len(endpoints)} {family.upper()} endpoint(s)")
    for pid in wrapper_pids:
        entry = per_pid.get(str(pid), {})
        wrapper_windows[str(pid)] = {k: entry.get(k, "unavailable")
                                     for k in ("top_visible", "top_hidden", "message_only", "children",
                                               "tcp4", "tcp6", "udp4", "udp6")}
        visible = entry.get("top_visible", "unavailable")
        if isinstance(visible, int) and visible > 0:
            problems.append(f"wrapper {pid} shows {visible} visible window(s)")
    for name, control in probe.get("controls", {}).items():
        controls[name] = control
    usable = True
    window_control = controls.get("chrome_visible_top_level")
    if not isinstance(window_control, int) or window_control < 1:
        usable = False
        problems.append("positive control missing: owned Chrome visible top-level window not seen by the "
                        "same enumeration (absence unverifiable, not zero)")
    endpoint_control = controls.get("chrome_cdp_listener_present")
    if endpoint_control is not True:
        usable = False
        problems.append("positive control missing: owned Chrome CDP listener not seen in the same "
                        "endpoint sample (absence unverifiable, not zero)")
    for field in required_host_fields:
        value = host_entry.get(field, "unavailable")
        is_data = (value is True) or (isinstance(value, (int, list)) and not isinstance(value, bool))
        if is_data:
            # counts and endpoint lists pass as data (ownership checks judge them above); pid_alive
            # passes only on True; a marker string ("unavailable") or False fails the requirement
            continue
        usable = False
        problems.append(f"required host field {field!r} is {value!r}: absence claim unsupported for this sample")
    return {"problems": problems, "host_windows": host_windows, "wrapper_windows": wrapper_windows,
            "host_endpoints": host_endpoints, "controls": controls, "usable": usable}


DEFAULT_WINDOWS_HOST_FIELDS = ("top_visible", "top_hidden", "message_only", "children",
                               "tcp4", "tcp6", "udp4", "udp6", "pid_alive")
DEFAULT_MACOS_HOST_FIELDS = ("all_layers", "tcp4", "pid_alive")
DEFAULT_AFTER_HOST_FIELDS = ()


def wrapper_hidden_conflict(samples):
    """Largest never-visible wrapper window count across connected-phase samples, or 0.

    A non-zero value is the literal-criterion conflict on the affected matrix rows (windows "owned by the
    host AND its wrapper"); it must never turn into a quiet PASS — the row stays owner-decided."""
    worst = 0
    for sample in samples:
        for wrapper in (sample.get("summary", {}).get("wrapper_windows", {}) or {}).values():
            hidden = wrapper.get("top_hidden", 0)
            if isinstance(hidden, int) and hidden > worst:
                worst = hidden
    return worst


def session_identity_problem(observer_session, active_console, chrome_session, host_session,
                             wrapper_sessions=()):
    """Problems for one probe's session identity (Windows): the enumeration that judges the host's absence
    must come from the same interactive console session the observed processes live in — observer, Chrome,
    host AND every wrapper pid (I-4). A session recorded as None (unmeasurable) is unavailable, not equal.
    This is a Windows integer-session comparator: macOS observers must be audited with
    macos_context_audit() instead (B-3); never apply this gate to a macOS probe."""
    problems = []
    observed = [("observer", observer_session), ("chrome", chrome_session), ("host", host_session)]
    observed += [(f"wrapper {pid}", session) for pid, session in wrapper_sessions]
    for label, session in observed:
        if session is None:
            problems.append(f"{label} session unavailable")
        elif active_console is None or session != active_console:
            problems.append(f"{label} session {session} != active console session {active_console}")
    return problems


def macos_context_audit(context):
    """B-3: macOS observation-context audit — recorded data, NOT a Windows integer-session gate.

    Audits that the run's actual mac context was measured and is auditable: `launchctl managername`,
    uid and the SSH marker (shape of macos_poc.context_probe()). Returns {"problems", "usable"};
    the values are never compared against any invented session id."""
    problems = []
    if not isinstance(context, dict):
        problems.append(f"macOS observation context was not measured: {context!r}")
        return {"problems": problems, "usable": False}
    managername = context.get("launchctl_managername")
    if not isinstance(managername, str) or not managername or managername == "unavailable":
        problems.append("macOS launchctl managername unavailable: observation context not audited "
                        "(no Windows integer-session gate is invented)")
    uid = context.get("uid")
    if not isinstance(uid, int) or isinstance(uid, bool):
        problems.append("macOS observing uid not measured")
    if "ssh_connection_present" not in context:
        problems.append("macOS SSH-context marker missing")
    return {"problems": problems, "usable": not problems,
            "launchctl_managername": context.get("launchctl_managername"),
            "uid": context.get("uid"),
            "ssh_connection_present": context.get("ssh_connection_present")}


def trace_origin_calibration(parsed):
    """Derive the host trace's own absolute origin ("trace 0ms" as a PC epoch) from the trace's own
    PC-absolute anchors. A traced `conn N disconnect requested epoch_ms=X` line carries BOTH its
    trace-relative `at_ms` and the PC-absolute `epoch_ms` at the same moment, so the trace timeline
    origin is `epoch_ms - at_ms`. Returns None when the trace carries no absolute anchor (legacy
    batches): those samples keep the recorded launch-based estimate, clearly labelled an estimate.
    The spread over all anchors is recorded so an anchor disagreement is visible, never averaged
    away; the anchor is used as-is (never re-labelled as a startup/startup-reference anchor)."""
    anchors = [(d["at_ms"], d["epoch_ms"]) for d in parsed.get("disconnect_requested", [])
               if _num(d.get("at_ms")) and _num(d.get("epoch_ms"))]
    if not anchors:
        return None
    per_anchor = [{"at_ms": at, "epoch_ms": epoch, "origin_epoch_ms": epoch - at} for at, epoch in anchors]
    candidates = sorted({a["origin_epoch_ms"] for a in per_anchor})
    return {"method": "trace absolute disconnect-requested anchor: trace_origin_epoch_ms = epoch_ms "
                      "- at_ms; corrected sample offset = wall_ms - trace_origin_epoch_ms "
                      "(removes the native-host spawn delay the launch-based estimate includes)",
            "anchor_kind": "disconnect_requested",
            "anchor_count": len(per_anchor),
            "anchors": per_anchor,
            "origin_candidates_ms": candidates,
            "origin_spread_ms": max(c["origin_epoch_ms"] for c in per_anchor)
                                - min(c["origin_epoch_ms"] for c in per_anchor) if len(per_anchor) > 1 else None,
            "trace_origin_epoch_ms": candidates[-1]}


def correlate_samples(samples, parsed):
    """Place each connected-phase sample inside the host's own trace timeline.

    The recorded launch-based estimate (`wall_ms - host_launch_wall_ms`) is LATE by the native-host
    spawn delay: the driver records `host_launch_wall_ms` before `otp23.connect()` while the trace
    only starts when the host process itself begins. When the trace carries a PC-absolute anchor
    (a `disconnect requested epoch_ms` line), `trace_origin_calibration()` derives the trace's own
    origin and the sample offset is corrected to `wall_ms - trace_origin_epoch_ms`; the raw
    launch-based estimate is kept unchanged for provenance and no longer used for bracketing. Legacy
    traces without an absolute anchor (and samples without `wall_ms`) keep the recorded estimate,
    labelled as the launch-based estimate it is."""
    steps = parsed["steps"]
    calibration = trace_origin_calibration(parsed)
    annotated = []
    for sample in samples:
        document = dict(sample)
        offset = sample.get("trace_offset_ms_estimate")
        wall = sample.get("wall_ms")
        if calibration is not None and _num(wall):
            corrected = wall - calibration["trace_origin_epoch_ms"]
            document["trace_offset_corrected_ms"] = corrected
            document["trace_origin_calibration"] = dict(calibration, applied=True)
            bracket_offset, basis = corrected, (
                "sample wall_ms minus the trace's own absolute-origin calibration "
                f"(trace_origin_epoch_ms {calibration['trace_origin_epoch_ms']} = disconnect-requested "
                f"epoch_ms {calibration['anchors'][-1]['epoch_ms']} at trace "
                f"{calibration['anchors'][-1]['at_ms']} ms); the launch-based estimate is kept "
                "separately and corrected here for the native-host spawn delay")
        elif calibration is not None:
            document["trace_origin_calibration"] = dict(calibration, applied=False)
            bracket_offset, basis = offset, (
                "recorded launch-based estimate (host trace offsets count from its own start); the "
                "trace carries an absolute-origin calibration but this sample has no wall_ms to "
                "convert against it — still an estimate, labelled as such")
        else:
            document["trace_origin_calibration"] = None
            bracket_offset, basis = offset, (
                "sample wall_ms minus recorded host-launch wall_ms (host trace offsets count from "
                "its own start); launch-based estimate — the trace carries no absolute anchor to "
                "correct against")
        if bracket_offset is not None:
            before = [s for s in steps if s["at_ms"] <= bracket_offset]
            after = [s for s in steps if s["at_ms"] > bracket_offset]
            last = before[-1] if before else None
            nxt = after[0] if after else None
            document["trace_bracket"] = {
                "after": None if last is None else
                {"at_ms": last["at_ms"], "type": last.get("type"),
                 "dir": last["dir"], "status": last.get("status")},
                "before": None if nxt is None else
                {"at_ms": nxt["at_ms"], "type": nxt.get("type"),
                 "dir": nxt["dir"], "status": nxt.get("status")},
                "estimate_basis": basis}
        annotated.append(document)
    return annotated


# ---- B-5: phone transport-telemetry burst association -------------------------------------------


PHONE_TELEMETRY_KIND = "otp23-transport-evidence"
PACKAGE1_BOUND_S = 30.0


def _num(value):
    """Shared finite-number predicate with the EXACT branch contract: a boolean is never a number; a
    (non-boolean) int is always accepted WITHOUT any float conversion — every Python int is finite,
    and converting a huge one (`10**1000`) to float would raise OverflowError, so `math.isfinite` is
    applied ONLY to floats, where it rejects NaN/+Inf/-Inf at the parser boundary (`json.loads`
    accepts those tokens)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def phone_burst_association(phone_documents, host_exit_epoch_ms, anchor_disconnect_epoch_ms,
                            expected_serials, bound_s=PACKAGE1_BOUND_S, run_id=None,
                            source_package=None):
    """`_phone_burst_association_impl` behind a controlled-numeric-range boundary: a value the exact
    `_num` branch contract accepts (e.g. a Python `int` of any magnitude, reachable through
    `json.loads`) can still overflow LATER validator arithmetic (float division/conversion in the
    fixture-stop guard). Such an OverflowError becomes a controlled FAIL with a `numeric_range`
    problem — never an uncaught exception, and NO new acceptance gate: ordinary finite data takes
    exactly the same code path as before."""
    try:
        return _phone_burst_association_impl(phone_documents, host_exit_epoch_ms,
                                             anchor_disconnect_epoch_ms, expected_serials,
                                             bound_s=bound_s, run_id=run_id,
                                             source_package=source_package)
    except OverflowError as error:
        return {"status": "FAIL",
                "problems": ["numeric_range: a numeric epoch/timing value accepted by the finite "
                             f"predicate is out of range for the validator's arithmetic ({error}) "
                             "— no association is claimed"],
                "run_id": None, "bound_s": bound_s,
                "expected_serials": list(expected_serials or ()), "phones": {},
                "central_peer_prefixes": [], "same_central": False}


def _phone_burst_association_impl(phone_documents, host_exit_epoch_ms, anchor_disconnect_epoch_ms,
                                  expected_serials, bound_s=PACKAGE1_BOUND_S, run_id=None,
                                  source_package=None):
    """B-5 (OTP-23 package1): associate BOTH phones' OWN connected→disconnected burst — recorded by the
    existing debug fixture transport telemetry (`otp23-transport-evidence.json`, one instrument run
    sharing one pre-declared run id) — with the host run's own traced exit, inside the pre-declared
    bound. This replaces the old dumpsys-history inference: the telemetry cannot rotate its events out
    (in-app bounded retention with an explicit `truncated` flag instead), and every event carries the
    phone's own epoch ms, so the association binds ACTUAL captured events, never a lone later row.

    Accepted (status PASS) only when, for every phone document:
      * the retrieval serial is one of `expected_serials`, each present exactly once (serial binding:
        each document reaches the validator through that serial's adb retrieval path; the serial itself
        is never inside the telemetry and never published);
      * the telemetry is `final`, not `truncated`, `peers_dropped == 0` and `clean` (a complete and
        honest evidence document; a partial snapshot can never support a complete-evidence claim);
      * every document carries the SAME non-empty `run_id` (the shared run binding; the `run_id`
        argument, when given, must match them all);
      * exactly ONE connected→disconnected burst of the SAME `peer_sha256_prefix` fits the association
        window — the connect at/within clock uncertainty before the run's disconnect anchor (the "pre"
        of the pre/post pair: a later stale connect of the same central can never be paired with this
        run) and the disconnect after (within clock uncertainty) the host's own traced exit and within
        `bound_s` (the "post", the actual release timing). Two or more fitting bursts fail as ambiguous;
    and unpaired lifecycle rows are rejected wherever they sit in time: a same-peer connected event
    never released is a stale connect; a same-peer disconnected event with no prior connect is a
    standalone old disconnect — neither can be claimed as this run's pair;
    and ACROSS the documents:
      * the associated bursts reference the SAME `peer_sha256_prefix` — the same central connected to
        both phones. Like `central_peer_prefixes_informational` in strict_phone_validation.py this is
        a binding, never a device-identity claim: identity stays the serial retrieval path.

    Fixture-stop confound guard (the physical stop itself can make the GATT server drop links): every
    document carries a `fixture_stop` marker {"epoch_phone_ms", "epoch_pc_ms"} recorded when the stop
    file was created. The associated disconnect must sit strictly BEFORE the marker on the phone's own
    clock (a disconnect at/after the stop is stop-induced, never a host-caused release), the stop must
    be at least bound_s + that phone's clock uncertainty after the host's own exit (PC-side epoch, so
    the whole pre-declared window was observed with the fixture still advertising), and only a
    disconnect that precedes the stop can be accepted. `source_package`, when declared, must match
    every telemetry's `source_package` (same fixture source). These are the existing link-release
    criterion conditions made explicit — not a new gate; they prevent an artificial falsification.

    Clock: every document carries a `clock` block from the fresh pre-run probe
    (`offset_phone_minus_pc_ms_median` plus `uncertainty_ms`); phone-side epochs are converted to PC
    estimates by SUBTRACTING the offset (the field is phone-minus-PC, so `pc_epoch = phone_epoch -
    offset`), and every comparison allows ±uncertainty — never an assumed-perfect clock. For the
    associated burst the full conservative uncertainty interval of the release gap is recorded as
    `gap_uncertainty_interval_ms` (the whole `[gap-uncertainty, gap+uncertainty]` span, not just the
    point estimate).

    Returns {"status": "PASS"|"FAIL", "problems": [...], "run_id", "bound_s", "expected_serials",
    "phones": {serial: {..detail..}}, "central_peer_prefixes": [...], "same_central": bool}. A missing
    or unusable input (no telemetry, not final, clock unavailable) becomes a problem and FAILs the
    whole result — never a fabricated association.
    """
    problems = []
    phones = {}
    bound_ms = bound_s * 1000.0

    # --- host-side anchors must exist before anything is associated against them.
    if not (_num(host_exit_epoch_ms) and _num(anchor_disconnect_epoch_ms)):
        problems.append("host exit/anchor epochs missing — nothing to associate against")
        return {"status": "FAIL", "problems": problems, "run_id": None, "bound_s": bound_s,
                "expected_serials": list(expected_serials or ()), "phones": {},
                "central_peer_prefixes": [], "same_central": False}

    # --- serial binding: both expected documents, exactly once each, no extras/aliases.
    docs_by_serial = {}
    for index, document in enumerate(phone_documents or []):
        serial = document.get("serial")
        if not isinstance(serial, str) or not serial:
            problems.append(f"phone document #{index}: retrieval serial missing/unusable")
            continue
        if serial in docs_by_serial:
            problems.append(f"serial {serial}: duplicated phone document")
            continue
        docs_by_serial[serial] = document
    expected = list(expected_serials or ())
    if not expected:
        problems.append("expected phone serials not declared")
    for serial in expected:
        if serial not in docs_by_serial:
            problems.append(f"serial {serial}: no phone document (both phones' own bursts required)")
    extra = [s for s in docs_by_serial if s not in expected]
    if extra:
        problems.append(f"unexpected phone serial(s) {sorted(extra)} (only the declared fixture serials bind)")

    # --- run-id binding: EVERY phone document carries the SAME non-empty run id (fail-closed: a
    # missing or empty id on ANY document is a problem, never a silent skip of that document).
    doc_run_ids = {serial: document.get("telemetry", {}).get("run_id")
                   if isinstance(document.get("telemetry"), dict) else None
                   for serial, document in docs_by_serial.items()}
    for serial in sorted(docs_by_serial):
        value = doc_run_ids[serial]
        if not isinstance(value, str) or not value:
            problems.append(f"{serial}: telemetry run id missing/empty "
                            f"(every expected phone document must carry the shared non-empty run id)")
    named = sorted({value for value in doc_run_ids.values() if isinstance(value, str) and value})
    shared_run_id = named[0] if len(named) == 1 else None
    if len(named) > 1:
        problems.append(f"phone documents carry different run ids {named} (one shared run binding required)")
    if not named:
        problems.append("no readable telemetry carries a run id")
    if run_id is not None and shared_run_id is not None and shared_run_id != run_id:
        problems.append(f"telemetry run id {shared_run_id!r} does not match the declared run binding {run_id!r}")
    if not run_id:
        problems.append("declared run binding empty")

    # --- per-document burst association.
    for serial in sorted(docs_by_serial):
        detail, document = {}, docs_by_serial[serial]
        telemetry, clock = document.get("telemetry"), document.get("clock")
        if not isinstance(telemetry, dict) or not isinstance(telemetry.get("peers"), list):
            problems.append(f"{serial}: telemetry missing/unreadable")
            phones[serial] = {"associated": False}
            continue
        if telemetry.get("kind") != PHONE_TELEMETRY_KIND:
            problems.append(f"{serial}: telemetry kind {telemetry.get('kind')!r} != {PHONE_TELEMETRY_KIND!r}")
        if source_package is not None and telemetry.get("source_package") != source_package:
            problems.append(f"{serial}: telemetry source_package {telemetry.get('source_package')!r} "
                            f"!= declared {source_package!r} (same fixture source required)")
        detail.update(kind=telemetry.get("kind"), final=telemetry.get("final"), clean=telemetry.get("clean"),
                      truncated=telemetry.get("truncated"), peers_dropped=telemetry.get("peers_dropped"))
        if telemetry.get("final") is not True:
            problems.append(f"{serial}: telemetry is not final (captured before the instrument finished)")
        if telemetry.get("final_after_service_stopped") is not True:
            problems.append(f"{serial}: final_after_service_stopped is not true "
                            f"(the document must declare capture after the fixture service stopped)")
        if telemetry.get("truncated") is not False:
            problems.append(f"{serial}: telemetry truncated (no complete-evidence claim)")
        if telemetry.get("peers_dropped") != 0:
            problems.append(f"{serial}: telemetry dropped {telemetry.get('peers_dropped')} peer record(s)")
        if telemetry.get("clean") is not True:
            problems.append(f"{serial}: telemetry is not clean (see docs/otp-23-phone-telemetry.md)")
        if not (isinstance(clock, dict) and clock.get("available") is True
                and _num(clock.get("offset_phone_minus_pc_ms_median"))
                and _num(clock.get("uncertainty_ms"))):
            problems.append(f"{serial}: fresh measured clock offset unavailable (never an assumed clock)")
            phones[serial] = dict(detail, associated=False)
            continue
        offset, uncertainty = clock["offset_phone_minus_pc_ms_median"], clock["uncertainty_ms"]
        detail["clock"] = {"offset_phone_minus_pc_ms_median": offset, "uncertainty_ms": uncertainty}
        view = _telemetry_bursts(telemetry, offset)
        problems.extend(f"{serial}: {text}" for text in view["problems"])
        detail.update(connected_event_count=view["connected_count"],
                      disconnected_event_count=view["disconnected_count"],
                      peers_with_lifecycle=view["peers_with_lifecycle"])
        if view["stale_connects"]:
            problems.append(f"{serial}: {len(view['stale_connects'])} same-peer connected event(s) never "
                            f"released (stale connect rejected — not this run's pair)")
        if view["standalone_disconnects"]:
            problems.append(f"{serial}: {len(view['standalone_disconnects'])} disconnected event(s) with no "
                            f"prior same-peer connect (standalone old disconnect rejected)")
        fitting = [b for b in view["bursts"]
                   if b["connected_pc"] <= anchor_disconnect_epoch_ms + uncertainty
                   and -uncertainty <= b["disconnected_pc"] - host_exit_epoch_ms <= bound_ms]
        if len(fitting) == 1:
            burst = fitting[0]
            gap = burst["disconnected_pc"] - host_exit_epoch_ms
            detail["burst"] = {"peer_sha256_prefix": burst["peer"],
                               "connected_epoch_phone_ms": burst["connected_epoch"],
                               "disconnected_epoch_phone_ms": burst["disconnected_epoch"],
                               "connected_pc_estimate_ms": burst["connected_pc"],
                               "disconnected_pc_estimate_ms": burst["disconnected_pc"],
                               "gap_vs_host_exit_ms": gap, "within_bound": gap <= bound_ms,
                               "gap_uncertainty_interval_ms": [gap - uncertainty, gap + uncertainty]}
            detail["associated"] = True
        elif len(fitting) > 1:
            problems.append(f"{serial}: {len(fitting)} bursts fit the pre/post window (ambiguous association)")
            detail["associated"] = False
        else:
            problems.append(f"{serial}: no own-run connected→disconnected pair fits the pre/post window "
                            f"(anchor {anchor_disconnect_epoch_ms}, host exit {host_exit_epoch_ms}, "
                            f"bound {bound_s:.0f} s; stale/foreign rows are rejected)")
            detail["associated"] = False
        detail["started_at_epoch_ms"] = telemetry.get("started_at_epoch_ms")
        if detail["associated"] and _num(telemetry.get("started_at_epoch_ms")) and fitting \
                and fitting[0]["connected_epoch"] < telemetry["started_at_epoch_ms"]:
            problems.append(f"{serial}: the connected event predates this telemetry's start "
                            f"(foreign-run event cannot bind this run)")
            detail["associated"] = False
        stop = document.get("fixture_stop")
        stop_phone = stop.get("epoch_phone_ms") if isinstance(stop, dict) else None
        stop_pc = stop.get("epoch_pc_ms") if isinstance(stop, dict) else None
        if not (_num(stop_phone) and _num(stop_pc)):
            # fail-closed: BOTH the phone-side and the PC-side stop epochs must be present and finite
            # for every expected document — a missing field never skips the stop-confound guard
            problems.append(f"{serial}: fixture stop marker missing/unusable — both the phone-side and "
                            f"PC-side stop epochs are required (stop-induced release cannot be excluded)")
            detail["associated"] = False
        else:
            detail["fixture_stop"] = dict(stop)
            if _num(telemetry.get("started_at_epoch_ms")) and stop_phone < telemetry["started_at_epoch_ms"]:
                problems.append(f"{serial}: fixture stop marker predates the telemetry start (unusable marker)")
            if detail["associated"] and stop_phone <= detail["burst"]["disconnected_epoch_phone_ms"]:
                problems.append(f"{serial}: the disconnected event is at/after the fixture stop marker "
                                f"(stop-induced release rejected)")
                detail["associated"] = False
            if detail["associated"]:
                margin_ms = stop_pc - host_exit_epoch_ms
                if margin_ms < bound_ms + uncertainty:
                    problems.append(f"{serial}: fixture stopped ~{round(margin_ms / 1000, 1)} s after the host's "
                                    f"own exit — before the full pre-declared window (bound + clock "
                                    f"uncertainty); a stop-induced release inside that window cannot be excluded")
                    detail["associated"] = False
        phones[serial] = detail

    # --- cross-document: the same central prefix on both phones' associated bursts.
    prefixes = sorted({detail["burst"]["peer_sha256_prefix"]
                       for detail in phones.values() if detail.get("burst")})
    every_expected_associated = bool(expected) and all(
        phones.get(serial, {}).get("associated") for serial in expected)
    same_central = len(prefixes) == 1 and every_expected_associated
    if len(prefixes) > 1:
        problems.append("associated bursts reference different central prefixes "
                        f"{prefixes} (same-central binding unproven)")
    elif prefixes and not every_expected_associated:
        problems.append("not every declared phone produced an associated burst")
    if not prefixes:
        problems.append("no phone produced an associated burst")

    status = "PASS" if (problems == [] and same_central) else "FAIL"
    return {"status": status, "problems": problems,
            "run_id": shared_run_id if len(named) == 1 else named, "bound_s": bound_s,
            "expected_serials": expected, "phones": phones,
            "central_peer_prefixes": prefixes, "same_central": same_central}


def _telemetry_bursts(telemetry, offset_ms):
    """Structural view of one telemetry document: every `connected`/`disconnected` event seen per peer,
    each connect paired with the FIRST same-peer disconnect at/after it. Phone epochs are converted to
    PC-comparable estimates by SUBTRACTING `offset_ms`: the measured field is
    `offset_phone_minus_pc_ms_median = phone_epoch - pc_epoch` (pc_phone_clock_probe.py), so
    `pc_epoch = phone_epoch - offset_ms`, never an addition. Other event kinds
    (notify/callback/mtu/subscribed) are not consulted. A connect with
    no following same-peer disconnect is a stale connect; a disconnect with no prior same-peer connect
    is a standalone old disconnect; both are reported for rejection, never silently dropped."""
    connected, disconnected, bursts = [], [], []
    stale_connects, standalone_disconnects = [], []
    peers_with_lifecycle, problems = set(), []
    for peer in telemetry.get("peers", []):
        prefix = peer.get("peer_sha256_prefix") if isinstance(peer, dict) else None
        events = peer.get("events") if isinstance(peer, dict) else None
        if not isinstance(prefix, str) or not isinstance(events, list):
            problems.append("peer record without prefix/events")
            continue
        peer_connects, peer_disconnects = [], []
        for event in events:
            if not isinstance(event, dict):
                problems.append("unshaped event record")
                continue
            kind, epoch = event.get("kind"), event.get("epoch_ms")
            if kind not in ("connected", "disconnected"):
                continue
            if not _num(epoch):
                problems.append(f"{prefix[:8]}… {kind} event without usable epoch_ms")
                continue
            (peer_connects if kind == "connected" else peer_disconnects).append(
                {"peer": prefix, "epoch": epoch, "epoch_pc": epoch - offset_ms})
        if peer_connects or peer_disconnects:
            peers_with_lifecycle.add(prefix)
            connected.extend(peer_connects)
            disconnected.extend(peer_disconnects)
        used = set()
        for connect in peer_connects:
            following = [(i, d) for i, d in enumerate(peer_disconnects)
                         if d["epoch"] >= connect["epoch"] and i not in used]
            if following:
                index_i, release = min(following, key=lambda pair: pair[1]["epoch"])
                used.add(index_i)
                bursts.append({"peer": prefix, "connected_epoch": connect["epoch"],
                               "disconnected_epoch": release["epoch"],
                               "connected_pc": connect["epoch_pc"], "disconnected_pc": release["epoch_pc"]})
            else:
                stale_connects.append(dict(connect))
        standalone_disconnects.extend(
            dict(d) for i, d in enumerate(peer_disconnects) if i not in used)
    return {"bursts": bursts, "connected": connected, "disconnected": disconnected,
            "connected_count": len(connected), "disconnected_count": len(disconnected),
            "peers_with_lifecycle": sorted(peers_with_lifecycle),
            "stale_connects": stale_connects, "standalone_disconnects": standalone_disconnects,
            "problems": problems}


# ---- phone discovery and explicit target selection ----------------------------------------------


def select_target(devices, label=None, sole=False):
    """Pick ONE device explicitly. `devices` is a list of {'label', ...}. Returns
    (index, status, error_class, reason). Never guesses: without a selector, or with an ambiguous
    one, the result is BLOCKED."""
    if bool(label) == bool(sole):
        return None, "BLOCKED", "ambiguous_target", "exactly one of --target-label / --sole-advertiser is required"
    if sole:
        if len(devices) == 1:
            return 0, "PASS", "none", "sole advertiser"
        return None, "BLOCKED", ("target_not_found" if not devices else "ambiguous_target"), \
            f"--sole-advertiser needs exactly one advertising phone, saw {len(devices)}"
    matches = [i for i, d in enumerate(devices) if d.get("label") == label]
    if len(matches) == 1:
        return matches[0], "PASS", "none", "unique label match"
    return None, "BLOCKED", ("target_not_found" if not matches else "ambiguous_target"), \
        f"{len(matches)} devices matched the target label of {len(devices)} discovered"


def pick_two(devices):
    """Lifecycle scenarios need two distinct phones: the first two discovered, or BLOCKED."""
    if len(devices) < 2:
        return None, "BLOCKED", "insufficient_phones", f"{len(devices)} of 2 required phones advertise the service"
    return devices[:2], "PASS", "none", "two phones"


# ---- deterministic seeded 4096-byte exchanges -------------------------------------------------


def fixture(size, seed):
    data = bytearray((i + seed) & 255 for i in range(size))
    data[:4] = b"VEL\x01"
    return bytes(data)


def echo(rec):
    return rec[:3] + b"\x02" + rec[4:]


def seeds_for(role, count, base=None):
    start = SEED_BASE[role] if base is None else base
    return [(start + index * SEED_STEP) & 255 for index in range(count)]


def plan_exchanges(role, count, base=None, size=SIZE):
    """Per-exchange request/expected bytes metadata; digests only leave this function's callers."""
    plan = []
    for index, seed in enumerate(seeds_for(role, count, base)):
        request = fixture(size, seed)
        plan.append({"index": index, "seed": seed, "size": size, "request": request,
                     "expected": echo(request), "request_sha256": sha256_hex(request),
                     "expected_sha256": sha256_hex(echo(request))})
    return plan


def request_b64(item):
    return base64.b64encode(item["request"]).decode()


def verify_reply(item, handle, message):
    """Exact reply bytes, handle and routing for one exchange. Returns a problem string or None."""
    if not isinstance(message, dict) or message.get("type") != "message":
        return "no message reply"
    if message.get("connection_handle") != handle:
        return "reply routed to a different connection handle"
    if message.get("record_base64") != base64.b64encode(item["expected"]).decode():
        return "reply bytes differ from the expected echo"
    return None


def stage_hash(pair_run_id, role, phone_label, seeds):
    material = json.dumps([pair_run_id, role, phone_label, seeds], separators=(",", ":"))
    return sha256_hex(material.encode("utf-8"))


def stage_document(pair_run_id, role, phone_label, seeds, target_label_hash, now_ms):
    """Hash-only READY stage: the parent releases both centrals once both exist."""
    return {"stage": "READY", "pair_run_id": pair_run_id, "role": role, "phone_label": phone_label,
            "links_connected": 1, "target_label_sha256": target_label_hash, "exchanges_planned": len(seeds),
            "plan_sha256": stage_hash(pair_run_id, role, phone_label, seeds), "at_wall_ms": now_ms}


# ---- M6 adjudicator ---------------------------------------------------------------------------


def nonce_hash(nonce):
    return sha256_hex(nonce.encode("utf-8")) if isinstance(nonce, str) and nonce else None


def parse_barrier_file(text):
    """Parent barrier file: JSON {"nonce": non-empty str, "epoch_ms": optional number}. Returns
    (document, problem); a half-written file is a problem the caller may retry."""
    try:
        document = json.loads(text)
    except ValueError:
        return None, "barrier file is not valid JSON yet"
    if not isinstance(document, dict) or not isinstance(document.get("nonce"), str) or not document["nonce"]:
        return None, "barrier file carries no nonce"
    return document, None


def _interval(result):
    exchanges = [e for e in result.get("exchanges", []) if e.get("start_wall_ms") is not None]
    if not exchanges:
        return None
    return min(e["start_wall_ms"] for e in exchanges), max(e["end_wall_ms"] for e in exchanges)


def _overlap(a, b):
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def role_problems(result, minimum):
    """Self-consistency of one central's M6 result."""
    problems = []
    if result.get("scenario") != "m6-multi-central":
        problems.append("not an m6-multi-central result")
    if result.get("links_on_this_central") != 1:
        problems.append("M6 needs exactly ONE link per central (two phones on one central is not M6)")
    if result.get("status") not in ("PASS", "PARTIAL"):
        problems.append(f"central status {result.get('status')}")
    exchanges = result.get("exchanges", [])
    if len(exchanges) < minimum:
        problems.append(f"{len(exchanges)} exchanges < {minimum}")
    if any(e.get("status") != "PASS" for e in exchanges):
        problems.append("an exchange failed")
    if len({e.get("seed") for e in exchanges}) != len(exchanges):
        problems.append("exchange seeds are not independent")
    if len({e.get("request_sha256") for e in exchanges}) != len(exchanges):
        problems.append("duplicate request records")
    for e in exchanges:
        if e.get("size") != SIZE:
            problems.append("exchange size is not 4096")
            break
        if e.get("received_sha256") != e.get("expected_sha256"):
            problems.append("exchange reply digest != expected echo digest")
            break
    if result.get("system_errors"):
        problems.append("system errors observed")
    if result.get("unexpected_disconnects"):
        problems.append("unexpected disconnect")
    return problems


def barrier_problems(barrier):
    """One central's barrier record: a real nonce release, staged strictly before it happened."""
    barrier = barrier or {}
    problems = []
    if barrier.get("mode") != "nonce-file":
        problems.append(f"barrier mode {barrier.get('mode')!r} is diagnostic only (needs a parent nonce file)")
    if not barrier.get("nonce_sha256"):
        problems.append("no barrier nonce hash")
    if not barrier.get("plan_sha256"):
        problems.append("no stage plan hash")
    stage, release = barrier.get("stage_wall_ms"), barrier.get("released_at_wall_ms")
    if not isinstance(stage, (int, float)) or not isinstance(release, (int, float)):
        problems.append("stage/release times missing")
    elif stage >= release:
        problems.append("READY stage was not written before the release")
    return problems


def adjudicate_m6(first, second, min_exchanges=MIN_EXCHANGES, min_overlap_ms=1000,
                  min_overlapping_pairs=3, clock_offset_ms=None, clock_uncertainty_ms=None,
                  clock_provenance=None, barrier_tolerance_ms=2000):
    """Merge two central results. Returns the adjudication; never touches the matrix.

    Clock: `clock_offset_ms` (windows wall clock minus mac wall clock), `clock_uncertainty_ms` and a
    non-empty `clock_provenance` (how the parent measured them) are all REQUIRED; without them the
    overlap and barrier checks are BLOCKED, never assumed. Overlap must be >= min + 2*uncertainty and only
    exchange pairs overlapping by more than 2*uncertainty count.

    Barrier: both centrals must have released from the same parent nonce file (equal nonce hashes), staged
    before release, with release times agreeing within uncertainty + tolerance. `--start-at-epoch` runs are
    diagnostic only.

    Phone identity: both centrals must report a non-empty, equal `target_label_sha256` from the actual
    advertisements; the parent-typed `phone_label` is an operator assertion that must agree but never proves
    identity alone.

    The Android notify return codes are not observable with the existing fixture, so M6 is never discharged
    here: verdict is PARTIAL at best (desktop concurrency clean), `m6_discharged` is always False."""
    checks = []

    def check(name, expected, actual, ok, blocked=False):
        checks.append({"name": name, "expected": expected, "actual": actual,
                       "status": "PASS" if ok else ("BLOCKED" if blocked else "FAIL")})
        return ok

    roles = {}
    for result in (first, second):
        role = (result or {}).get("role")
        if role in ("windows", "mac") and role not in roles:
            roles[role] = result
    both = set(roles) == {"windows", "mac"}
    check("both_centrals_present", "one windows and one mac result", sorted(roles), both, blocked=True)
    if not both:
        return _verdict("BLOCKED", checks, ["a central result is missing: one central alone is never M6"],
                        clock_offset_ms, clock_uncertainty_ms, clock_provenance)
    win, mac = roles["windows"], roles["mac"]
    pair = (win.get("pair_run_id"), mac.get("pair_run_id"))
    check("same_pair_run_id", "identical non-empty id", "match" if pair[0] == pair[1] and pair[0] else "mismatch",
          bool(pair[0]) and pair[0] == pair[1])
    labels = (win.get("phone_label"), mac.get("phone_label"))
    hashes = (win.get("target_label_sha256"), mac.get("target_label_sha256"))
    labels_ok = bool(labels[0]) and labels[0] == labels[1]
    both_hashed = bool(hashes[0]) and bool(hashes[1])
    check("same_phone_identity",
          "equal operator phone label AND equal non-empty advertised-label hashes from both centrals "
          "(a label alone is an operator claim, never proof)",
          {"labels_equal": labels[0] == labels[1], "both_hashes_present": both_hashed,
           "hashes_equal": both_hashed and hashes[0] == hashes[1]},
          labels_ok and both_hashed and hashes[0] == hashes[1],
          blocked=labels_ok and not both_hashed)
    for role, result in roles.items():
        problems = role_problems(result, min_exchanges)
        check(f"{role}_central_clean", "20+ exact 4096-byte exchanges, no errors, one link", problems or "ok", not problems,
              blocked=result.get("status") == "BLOCKED")
    clock_ok = (isinstance(clock_offset_ms, (int, float)) and isinstance(clock_uncertainty_ms, (int, float))
                and clock_uncertainty_ms >= 0 and bool(clock_provenance))
    check("clock_measured", "offset, non-negative uncertainty and measurement provenance supplied by the parent",
          {"offset": clock_offset_ms is not None, "uncertainty": clock_uncertainty_ms is not None,
           "provenance": bool(clock_provenance)}, clock_ok, blocked=True)
    barrier_checked = {}
    for role, result in roles.items():
        problems = barrier_problems(result.get("barrier"))
        barrier_checked[role] = problems
        check(f"{role}_barrier_staged_and_released", "nonce-file release after its READY stage", problems or "ok",
              not problems, blocked=any("diagnostic" in p for p in problems))
    wb, mb = win.get("barrier") or {}, mac.get("barrier") or {}
    nonce_equal = bool(wb.get("nonce_sha256")) and wb.get("nonce_sha256") == mb.get("nonce_sha256")
    check("same_barrier_nonce", "identical non-empty nonce hash on both centrals", nonce_equal, nonce_equal,
          blocked=not (wb.get("nonce_sha256") and mb.get("nonce_sha256")))
    if clock_ok and isinstance(wb.get("released_at_wall_ms"), (int, float)) \
            and isinstance(mb.get("released_at_wall_ms"), (int, float)):
        skew = abs((wb["released_at_wall_ms"] - clock_offset_ms) - mb["released_at_wall_ms"])
        bound = clock_uncertainty_ms + barrier_tolerance_ms
        check("barrier_release_simultaneous", f"|win - offset - mac| <= uncertainty + tolerance ({bound} ms)",
              {"skew_ms": round(skew, 1)}, skew <= bound)
    else:
        check("barrier_release_simultaneous", "comparable release times and a measured clock", "unavailable", False,
              blocked=True)
    a, b = _interval(win), _interval(mac)
    if clock_ok and a and b:
        u2 = 2 * clock_uncertainty_ms
        a = (a[0] - clock_offset_ms, a[1] - clock_offset_ms)
        window = _overlap(a, b)
        pairs = sum(1 for x in win.get("exchanges", []) for y in mac.get("exchanges", [])
                    if x.get("start_wall_ms") is not None and y.get("start_wall_ms") is not None
                    and _overlap((x["start_wall_ms"] - clock_offset_ms, x["end_wall_ms"] - clock_offset_ms),
                                 (y["start_wall_ms"], y["end_wall_ms"])) > u2)
        check("real_io_intervals_overlap",
              f">= {min_overlap_ms} + 2x{clock_uncertainty_ms} ms and >= {min_overlapping_pairs} exchange pairs overlapping by > 2x uncertainty",
              {"overlap_ms": window, "overlapping_exchange_pairs": pairs},
              window >= min_overlap_ms + u2 and pairs >= min_overlapping_pairs)
    else:
        check("real_io_intervals_overlap", "both centrals recorded real I/O intervals and the clock is measured",
              "missing", False, blocked=not clock_ok)
    # Isolation: a deliberate disconnect on one central must not disturb the other.
    for role, other in (("windows", "mac"), ("mac", "windows")):
        gone = roles[role].get("deliberate_disconnect")
        if gone is None:
            continue
        at = gone.get("at_wall_ms")
        if not isinstance(at, (int, float)) or not clock_ok:
            check(f"{other}_survives_{role}_disconnect", "recorded disconnect time and a measured clock",
                  "missing", False, blocked=not clock_ok and isinstance(at, (int, float)))
            continue
        # offset = windows clock - mac clock: express the cut in the surviving central's clock
        cut = at + (clock_offset_ms if role == "mac" else -clock_offset_ms)
        survived = [e for e in roles[other].get("exchanges", [])
                    if e.get("start_wall_ms", 0) >= cut + clock_uncertainty_ms and e.get("status") == "PASS"]
        check(f"{other}_survives_{role}_disconnect", "successful exchange after the peer central disconnected",
              len(survived), bool(survived))
    failed = [c for c in checks if c["status"] == "FAIL"]
    blocked = [c for c in checks if c["status"] == "BLOCKED"]
    desktop = "FAIL" if failed else "BLOCKED" if blocked else "PASS"
    return _verdict("PARTIAL" if desktop == "PASS" else desktop, checks, [], clock_offset_ms,
                    clock_uncertainty_ms, clock_provenance, desktop)


def _verdict(verdict, checks, reasons, offset, uncertainty, provenance, desktop=None):
    return {"scenario": "m6-adjudication", "verdict": verdict,
            "desktop_concurrency": desktop or verdict, "checks": checks,
            "requirements": [{"name": "android_notify_return_codes", "status": "BLOCKED",
                              "expected": "per-notification return codes from the phone",
                              "actual": NOTIFY_CODES_REASON}],
            "notes": reasons, "clock": {"offset_ms": offset, "uncertainty_ms": uncertainty, "provenance": provenance},
            "m6_discharged": False,
            "disclaimer": "driver adjudication only; the parent records matrix rows. PARTIAL = desktop concurrency "
                          "clean while the Android notify return codes stay BLOCKED (no override exists)."}


def check_truncated_eof(parsed, returncode, eof_state):
    """Controlled framing case: stdin closed inside a frame header. The host must reject (exit 2),
    log the framing violation, and close stdout on a frame boundary; no shutdown credit."""
    problems = []
    if returncode != 2:
        problems.append(f"exit code {returncode} != 2 (framing rejected)")
    if not any(s["type"] == "framing_violation" for s in parsed["steps"]):
        problems.append("trace has no framing-violation stage")
    if parsed["exit"] is None or parsed["exit"]["code"] != 2:
        problems.append("trace exit line missing or not code 2")
    if eof_state != "clean":
        problems.append(f"stdout ended {eof_state}, expected a clean frame boundary")
    if any(s["type"] == "shutdown_complete" for s in parsed["steps"]):
        problems.append("shutdown_complete observed on a framing-error path")
    return problems
