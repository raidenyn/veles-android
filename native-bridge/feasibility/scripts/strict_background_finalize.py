"""Offline finalization of a strict background run whose phones were not attached to the driver's machine.

The driver (strict_background.py) on a Mac ends BLOCKED on ST-08 because it cannot read the phones'
hash-only evidence. After the idle interval the phone files are collected on the PC they are USB-attached to
(``adb -s SERIAL shell run-as me.nagaev.veles.otp23 cat files/otp23-unsolicited.json > phoneN.json``) and passed
here, together with the immutable RAW strict result, to produce a NEW adjudication JSON. The raw artifact is
only read (its sha256 is recorded); the output path must not exist and is created exclusively, so a raw
artifact or an earlier adjudication can never be overwritten. Verdicts come from the pure
``strict_background_core.evaluate``. ``formal_otp23_acceptance`` stays false: the requirements matrix decides.

Usage: python scripts/strict_background_finalize.py --raw evidence/strict-cold.json \\
           --phone-evidence phone-a.json --phone-evidence phone-b.json --out evidence/strict-cold.adjudicated.json
Exit: 0 = PASS, 1 = adjudicated but not PASS, 2 = usage / unreadable input / output exists.
"""

import argparse
import copy
import hashlib
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

import strict_background_core as core

KIND = "otp23-unsolicited-evidence"
ADJUDICATION_KIND = "otp23-strict-adjudication"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_input(path):
    """Return a record for one input file; never raises on content problems (recorded as `error`)."""
    target = pathlib.Path(path)
    record = {"path": str(path), "resolved": None, "sha256": None, "size": None, "mtime_epoch": None,
              "json": None, "error": None}
    try:
        resolved = target.resolve()
        data = resolved.read_bytes()
        record.update({"resolved": str(resolved), "sha256": sha256_bytes(data), "size": len(data),
                       "mtime_epoch": resolved.stat().st_mtime})
        record["json"] = json.loads(data.decode("utf-8"))
    except Exception as error:
        record["error"] = f"{type(error).__name__}"
    return record


def parse_utc(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def validate_phone_files(raw, phone_records, skew_s=core.CLOCK_SKEW_S):
    """Pure. raw = parsed raw strict result; phone_records = list of read_input() dicts.
    Returns {status: PASS|FAIL|BLOCKED, problems: [...], files: [...]}; missing is BLOCKED, everything
    else wrong (duplicate, stale, failed, wrong hash/delay/kind) is FAIL. Never PASS on weak evidence."""
    evidence, meta = raw.get("evidence") or {}, raw.get("meta") or {}
    timeline = evidence.get("timeline") or {}
    expected_count = len(evidence.get("handles") or [])
    problems, fails, files = [], False, []
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

    if len(phone_records) < max(expected_count, 2):
        problems.append(f"missing phone evidence: {len(phone_records)} file(s) for {max(expected_count, 2)} phones")
    elif len(phone_records) > expected_count:
        problems.append(f"extra phone evidence: {len(phone_records)} files for {expected_count} phones")
        fails = True

    for key, label in (("resolved", "path"), ("sha256", "content hash")):
        seen = [r[key] for r in phone_records if r.get(key)]
        if len(seen) != len(set(seen)):
            problems.append(f"duplicate phone evidence {label}: the same source was supplied twice")
            fails = True
    peers = []
    for index, record in enumerate(phone_records, 1):
        item = {"index": index, "sha256": record.get("sha256"), "size": record.get("size"),
                "mtime_utc": None if record.get("mtime_epoch") is None else
                datetime.fromtimestamp(record["mtime_epoch"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "problems": []}
        files.append(item)
        local = item["problems"]
        data = record.get("json")
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
                    if window_ok:
                        if sub / 1000 < started - skew_s or sub / 1000 > bg_start + skew_s:
                            local.append("subscription time is outside this run (stale push from another run)")
                        if fired / 1000 > reopened + skew_s:
                            local.append("fired after the driver re-attached (not unsolicited during idle)")
                        if fired / 1000 < closed + min_bg - skew_s:
                            local.append("fired before the idle interval could have lasted the minimum")
                peer = push.get("peer_sha256_prefix")
                if not peer:
                    local.append("peer_sha256_prefix missing (cannot prove distinct phones)")
                peers.append(peer)
        mtime = record.get("mtime_epoch")
        if window_ok and mtime is not None and mtime < closed + min_bg - skew_s:
            local.append("file predates the end of the idle interval (collected before idle ended)")
        if local:
            fails = True
            problems.extend(f"file #{index}: {text}" for text in local)
    named = [p for p in peers if p]
    if len(named) != len(set(named)):
        problems.append("two files describe the same phone (identical peer prefix)")
        fails = True
    status = "FAIL" if fails else ("BLOCKED" if problems else "PASS")
    return {"status": status, "problems": problems, "files": files}


def adjudicate(raw, raw_record, phone_records, now=None):
    """Pure given its inputs. Returns the new adjudication document (never mutates `raw`)."""
    validation = validate_phone_files(raw, phone_records)
    evidence = copy.deepcopy(raw.get("evidence") or {})
    raw_had_phone = evidence.get("phone_evidence") is not None
    parsed = [r["json"] for r in phone_records if isinstance(r.get("json"), dict)]
    evidence["phone_evidence"] = parsed or None
    evidence["phone_validation"] = validation
    recomputed_raw = core.evaluate(raw.get("evidence") or {})["checks"]
    mismatches = [k for k, c in (raw.get("checks") or {}).items()
                  if k in recomputed_raw and recomputed_raw[k]["status"] != c.get("status")]
    verdict = core.evaluate(evidence)
    result = verdict["strict_result"]
    if mismatches:
        result = "FAIL"
    consistency = {"checks_in_raw_that_disagree_with_recomputation": mismatches}
    return {
        "kind": ADJUDICATION_KIND,
        "formal_otp23_acceptance": False,
        "note": "Pure re-evaluation of an immutable raw strict result plus offline phone evidence; the "
                "requirements matrix, not this file, adjudicates OTP-23 acceptance.",
        "adjudicated_utc": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strict_result": result,
        "scenario": raw.get("scenario"),
        "platform": raw.get("platform"),
        "checks": verdict["checks"],
        "raw_consistency": consistency,
        "inputs": {
            "raw": {k: raw_record.get(k) for k in ("path", "sha256", "size")} | {
                "strict_result_in_raw": raw.get("strict_result"), "raw_had_phone_evidence": raw_had_phone},
            "phone_files": [{"path": r.get("path"), "sha256": r.get("sha256"), "size": r.get("size"),
                             "mtime_epoch": r.get("mtime_epoch")} for r in phone_records],
        },
        "phone_validation": validation,
        "evidence": evidence,
    }


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", required=True, help="immutable raw strict_background.py result JSON")
    parser.add_argument("--phone-evidence", action="append", default=[], required=True,
                        help="one phone's otp23-unsolicited.json, repeat once per physical phone")
    parser.add_argument("--out", required=True, help="NEW adjudication JSON (must not exist)")
    args = parser.parse_args(argv)
    out = pathlib.Path(args.out)
    inputs = [pathlib.Path(p).resolve() for p in [args.raw] + args.phone_evidence]
    if out.exists() or out.resolve() in inputs:
        print(f"error: refusing to overwrite existing {out}", file=sys.stderr)
        return 2
    raw_record = read_input(args.raw)
    raw = raw_record.get("json")
    if raw_record.get("error") or not isinstance(raw, dict) or "evidence" not in raw or "checks" not in raw:
        print("error: --raw is not a strict_background result", file=sys.stderr)
        return 2
    document = adjudicate(raw, raw_record, [read_input(p) for p in args.phone_evidence])
    text = json.dumps(document, indent=2, sort_keys=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(out), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        print(f"error: refusing to overwrite existing {out}", file=sys.stderr)
        return 2
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"strict_result={document['strict_result']} scenario={document['scenario']} "
          f"phone_validation={document['phone_validation']['status']} formal_otp23_acceptance=false -> {out}")
    return 0 if document["strict_result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(run())
