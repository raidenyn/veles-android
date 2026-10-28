"""Pure phone-evidence validation shared by the strict driver and the offline finalizer (no platform imports).

Every acquisition path (driver ``--adb``, driver/finalizer ``--phone-evidence SERIAL=PATH``) is validated by
the same ``validate_phone_files`` BEFORE ``strict_background_core.evaluate`` sees it. Rules:

* a phone is identified by an explicit per-file SOURCE identity (a salted hash of the adb serial); the
  serial itself is never published. ``peer_sha256_prefix`` is the hash of the CENTRAL's address, so two real
  phones can legitimately carry the same value: it is recorded as information and never used to claim (or
  deny) distinct phones;
* distinctness = distinct source hashes AND distinct file bytes AND distinct paths;
* each phone's ``fired_at`` must match one page receipt (a distinct receipt per phone) within an EXPLICIT
  clock allowance: the phone->desktop clock offset, its uncertainty and its measurement source must be
  supplied. Without them the verdict is BLOCKED, never an assumed-perfect-clock PASS;
* a bare file path (no source) can never PASS (BLOCKED); extra, stale, failed, wrong-hash or wrong-delay
  pushes FAIL.
"""

import hashlib
import itertools
import json
import pathlib
from datetime import datetime, timezone

import strict_background_core as core

KIND = "otp23-unsolicited-evidence"
SOURCE_SALT = "veles-otp23-phone-source:"
DELIVERY_ALLOWANCE_MS = 30_000  # BLE delivery + page scheduling between the phone firing and the page receipt


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def source_hash(serial):
    """Published stand-in for an adb serial."""
    return sha256_bytes((SOURCE_SALT + serial).encode("utf-8"))


def parse_phone_arg(text):
    """'SERIAL=PATH' -> (source_hash, path); a bare path -> (None, path) which can never PASS."""
    head, sep, tail = text.partition("=")
    if sep and head and tail and "/" not in head and "\\" not in head:
        return source_hash(head), tail
    return None, text


def read_input(path, source=None):
    """Record for one input file; never raises on content problems (recorded as `error`)."""
    record = {"path": str(path), "resolved": None, "sha256": None, "size": None, "mtime_epoch": None,
              "json": None, "error": None, "source_hash": source}
    try:
        resolved = pathlib.Path(path).resolve()
        data = resolved.read_bytes()
        record.update({"resolved": str(resolved), "sha256": sha256_bytes(data), "size": len(data),
                       "mtime_epoch": resolved.stat().st_mtime})
        record["json"] = json.loads(data.decode("utf-8"))
    except Exception as error:
        record["error"] = type(error).__name__
    return record


def record_from_text(text, source, collected_epoch, label):
    """Record for evidence pulled directly (adb); hashes the exact bytes received."""
    data = text.encode("utf-8")
    record = {"path": label, "resolved": label, "sha256": sha256_bytes(data), "size": len(data),
              "mtime_epoch": collected_epoch, "json": None, "error": None, "source_hash": source}
    try:
        record["json"] = json.loads(text)
    except ValueError:
        record["error"] = "JSONDecodeError"
    return record


def parse_utc(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def clock_ok(clock):
    return (isinstance(clock, dict) and core._num(clock.get("offset_ms")) and core._num(clock.get("uncertainty_ms"))
            and clock["uncertainty_ms"] >= 0 and bool(str(clock.get("source") or "").strip()))


def _match_receipts(fired_ms, receipts_ms, tolerance_ms):
    """A distinct receipt per phone within tolerance (any pairing)."""
    if len(receipts_ms) < len(fired_ms):
        return False
    for chosen in itertools.permutations(range(len(receipts_ms)), len(fired_ms)):
        if all(abs(fired_ms[i] - receipts_ms[j]) <= tolerance_ms for i, j in enumerate(chosen)):
            return True
    return False


def validate_phone_files(raw, phone_records, clock=None, skew_s=core.CLOCK_SKEW_S):
    """Pure. raw = {evidence, meta}; phone_records = read_input()/record_from_text() dicts; clock =
    {offset_ms (desktop - phone), uncertainty_ms, source}. Returns {status, problems, files, clock, ...}.
    Missing/unprovable inputs are BLOCKED; anything wrong or duplicated is FAIL; never PASS on weak input."""
    evidence, meta = raw.get("evidence") or {}, raw.get("meta") or {}
    timeline = evidence.get("timeline") or {}
    expected_count = len(evidence.get("handles") or [])
    problems, blockers, fails, files = [], [], False, []
    want = core.expected_sha256()
    delay_ms = (meta.get("phone_delay_s") or 0) * 1000
    min_bg = evidence.get("min_background_s", core.MIN_BACKGROUND_S)
    try:
        started = parse_utc(meta["started_utc"])
    except Exception:
        started = None
    bg_start, closed, reopened = (timeline.get("background_started_wall"), timeline.get("cdp_closed_wall"),
                                  timeline.get("cdp_reopened_wall"))
    window_ok = not (None in (started, bg_start, closed, reopened) or expected_count < 2 or not delay_ms)
    if not window_ok:
        problems.append("raw result lacks the run window / handles / phone delay needed to judge freshness")
        fails = True
    have_clock = clock_ok(clock)
    if not have_clock:
        blockers.append("phone->desktop clock offset, uncertainty and measurement source not supplied "
                        "(receipt matching cannot be judged; perfect clocks are never assumed)")
    offset_s = (clock["offset_ms"] / 1000.0) if have_clock else 0.0
    slack_s = skew_s + ((clock["uncertainty_ms"] / 1000.0) if have_clock else 0.0)

    if len(phone_records) < max(expected_count, 2):
        blockers.append(f"missing phone evidence: {len(phone_records)} file(s) for {max(expected_count, 2)} phones")
    elif len(phone_records) > expected_count:
        problems.append(f"extra phone evidence: {len(phone_records)} files for {expected_count} phones")
        fails = True
    for key, label in (("resolved", "path"), ("sha256", "content hash"), ("source_hash", "acquisition source")):
        seen = [r[key] for r in phone_records if r.get(key)]
        if len(seen) != len(set(seen)):
            problems.append(f"duplicate phone evidence {label}: the same source was supplied twice")
            fails = True
    if any(not r.get("source_hash") for r in phone_records):
        blockers.append("a phone evidence file has no source provenance (bare path): cannot prove distinct phones")

    fired_adjusted_ms, peers = [], []
    for index, record in enumerate(phone_records, 1):
        item = {"index": index, "source_hash": record.get("source_hash"), "sha256": record.get("sha256"),
                "size": record.get("size"),
                "mtime_utc": None if record.get("mtime_epoch") is None else
                datetime.fromtimestamp(record["mtime_epoch"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "problems": []}
        files.append(item)
        local, data = item["problems"], record.get("json")
        if record.get("error") or not isinstance(data, dict):
            local.append(f"unreadable or not a JSON object ({record.get('error')})")
        else:
            pushes = data.get("pushes")
            if data.get("kind") != KIND:
                local.append("kind is not " + KIND)
            if not isinstance(pushes, list) or len(pushes) != 1:
                local.append(f"expected exactly 1 push, saw {len(pushes) if isinstance(pushes, list) else 'none'} "
                             "(stale or missing pushes)")
            else:
                push = pushes[0] if isinstance(pushes[0], dict) else {}
                if push.get("queued") is not True:
                    local.append("push was not queued (failed)")
                if push.get("sha256") != want or push.get("bytes") != core.RECORD_LEN:
                    local.append("push hash/length is not the type-3 fixture")
                if delay_ms and push.get("configured_delay_ms") != delay_ms:
                    local.append("configured delay differs from this run's phone_delay_s")
                sub, fired = push.get("subscribed_at_epoch_ms"), push.get("fired_at_epoch_ms")
                if not core._num(sub) or not core._num(fired):
                    local.append("subscription/fire timestamps missing")
                else:
                    if delay_ms and fired - sub < delay_ms:
                        local.append("fired earlier than the configured delay after subscription")
                    if window_ok and have_clock:
                        sub_d, fired_d = sub / 1000 + offset_s, fired / 1000 + offset_s
                        if sub_d < started - slack_s or sub_d > bg_start + slack_s:
                            local.append("subscription time is outside this run (stale push from another run)")
                        if fired_d > reopened + slack_s:
                            local.append("fired after the driver re-attached (not unsolicited during idle)")
                        if fired_d < closed + min_bg - slack_s:
                            local.append("fired before the idle interval could have lasted the minimum")
                        fired_adjusted_ms.append(fired + offset_s * 1000)
                peers.append(push.get("peer_sha256_prefix"))
        mtime = record.get("mtime_epoch")
        if window_ok and mtime is not None and mtime < closed + min_bg - skew_s - (slack_s - skew_s):
            local.append("file predates the end of the idle interval (collected before idle ended)")
        if local:
            fails = True
            problems.extend(f"file #{index}: {text}" for text in local)

    events = [e for e in evidence.get("events", []) if core._num(e.get("wall"))]
    if have_clock and window_ok and not fails and len(fired_adjusted_ms) == len(phone_records) == expected_count:
        tolerance_ms = clock["uncertainty_ms"] + DELIVERY_ALLOWANCE_MS
        if not _match_receipts(fired_adjusted_ms, [e["wall"] for e in events], tolerance_ms):
            problems.append("a phone's fired_at does not match its own distinct page receipt within the clock "
                            f"allowance ({tolerance_ms:.0f} ms)")
            fails = True
    status = "FAIL" if fails else ("BLOCKED" if blockers else "PASS")
    return {"status": status, "problems": problems + blockers, "files": files,
            "clock": clock if have_clock else None,
            "central_peer_prefixes_informational": peers}
