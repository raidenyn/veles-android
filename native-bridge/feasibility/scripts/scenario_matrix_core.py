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
import pathlib
import re
import struct
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent

STATUSES = ("PASS", "FAIL", "BLOCKED", "PARTIAL")
# Safe error classes a scenario may report; never free text from the platform.
ERROR_CLASSES = (
    "none", "insufficient_phones", "ambiguous_target", "target_not_found", "permission_denied",
    "unsupported", "environment", "host_error", "timeout", "assertion", "requires_operator",
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


def source_hashes():
    """SHA-256 of the exact files this driver and the connector run from."""
    here = pathlib.Path(__file__).resolve().parent
    files = [here / "scenario_matrix.py", here / "scenario_matrix_core.py",
             ROOT / "extension" / "connector.js", ROOT / "extension" / "manifest.json",
             ROOT / "src" / "identity.rs"]
    return {path.name: file_sha256(path) for path in files}


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
        done = re.match(r"exit code=(-?\d+) stdout_drained=(\w+)", body)
        if done:
            exit_info = {"at_ms": at_ms, "code": int(done.group(1)),
                         "stdout_drained": done.group(2) == "true"}
    return {"steps": steps, "exit": exit_info}


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


def check_teardown(parsed, expected, require_drained=None):
    """Problems (empty = ok) for one lifecycle path; the paths stay distinguishable so a Chrome
    port.disconnect / tab close / stdin EOF is never credited as an explicit shutdown, or the reverse.

    expected:
      'shutdown'      shutdown request answered by shutdown_complete (last host output), exit 0, stdout drained
      'stdin_closed'  host saw stdin EOF itself: exit line present, code 0, no shutdown handshake
      'disconnect'    Chrome closed the port / tab: host gone without a shutdown handshake, either by its own
                      stdin-EOF exit (exit line, code 0) or killed by Chrome (no exit line); the mechanism is
                      recorded, never assumed
    `stdout_drained` is required where a reader keeps consuming stdout (shutdown, direct stdin EOF); when
    Chrome tears both pipes down it is recorded but cannot be demanded (`require_drained` overrides)."""
    if require_drained is None:
        require_drained = expected == "shutdown"
    problems = []
    mechanism = trace_mechanism(parsed)
    allowed = ("stdin_closed", "no_exit") if expected == "disconnect" else (expected,)
    if mechanism not in allowed:
        problems.append(f"trace mechanism {mechanism!r}, expected {' or '.join(allowed)!r}")
    info = parsed["exit"]
    if info is None:
        if expected != "disconnect":
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
    return problems


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
