"""Offline finalization of a strict background run whose phones were not attached to the driver's machine.

The driver (strict_background.py) on a Mac ends BLOCKED on ST-08 because it cannot read the phones'
hash-only evidence. After the idle interval the phone files are collected on the PC they are USB-attached to
(``adb -s SERIAL shell run-as me.nagaev.veles.otp23 cat files/otp23-unsolicited.json > phoneN.json``) and passed
here, together with the immutable RAW strict result, to produce a NEW adjudication JSON. The raw artifact is
only read (its sha256 is recorded); the output path must not exist and is created exclusively, so a raw
artifact or an earlier adjudication can never be overwritten. Verdicts come from the pure
``strict_background_core.evaluate``, called with the raw's trusted top-level ``platform`` (Opus F-1: the
trusted platform is authoritative; a contradiction with the foreground's self-recorded platform is BLOCKED). ``formal_otp23_acceptance`` stays false: the requirements matrix decides.

Usage: python scripts/strict_background_finalize.py --raw evidence/strict-cold.json \\
           --phone-evidence phone-a.json --phone-evidence phone-b.json --out evidence/strict-cold.adjudicated.json
Exit: 0 = PASS, 1 = adjudicated but not PASS, 2 = usage / unreadable input / output exists.
"""

import argparse
import copy
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

import strict_background_core as core
from strict_phone_validation import (KIND, parse_phone_arg, read_input, sha256_bytes,  # noqa: F401
                                     validate_phone_files)

ADJUDICATION_KIND = "otp23-strict-adjudication"
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
EVALUATOR_FILES = ("strict_background_core.py", "strict_phone_validation.py", "strict_background_finalize.py")


def _git(directory, *args):
    """Stdout of a git command run in `directory`, or None when git or the checkout is unavailable."""
    try:
        done = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def evaluator_provenance(directory=SCRIPT_DIR):
    """Which evaluator code produced a verdict: git revision, whether the evaluator files differ from it, and
    their SHA256. The dirty flag covers ONLY the evaluator files (other uncommitted work is not reported as
    evaluator change). Outside a git checkout the revision and dirty flag are null, never guessed; the hashes
    are always computed from the files actually loaded."""
    directory = pathlib.Path(directory)
    revision = (_git(directory, "rev-parse", "HEAD") or "").strip() or None
    status = _git(directory, "status", "--porcelain", "--untracked-files=all", "--", *EVALUATOR_FILES) \
        if revision else None
    dirty_files = None if status is None else sorted(line[3:].strip() for line in status.splitlines() if line)
    hashes = {}
    for name in EVALUATOR_FILES:
        try:
            hashes[name] = sha256_bytes((directory / name).read_bytes())
        except OSError:
            hashes[name] = None
    return {"git_revision": revision, "git_available": revision is not None,
            "evaluator_files_dirty": None if dirty_files is None else bool(dirty_files),
            "evaluator_files_modified": dirty_files, "sha256": hashes}


def adjudicate(raw, raw_record, phone_records, now=None, clock=None, provenance=None):
    """Pure given its inputs. Returns the new adjudication document (never mutates `raw`)."""
    validation = validate_phone_files(raw, phone_records, clock)
    evidence = copy.deepcopy(raw.get("evidence") or {})
    raw_had_phone = evidence.get("phone_evidence") is not None
    parsed = [r["json"] for r in phone_records if isinstance(r.get("json"), dict)]
    evidence["phone_evidence"] = parsed or None
    evidence["phone_validation"] = validation
    evidence["phone_clock"] = validation.get("clock")
    recomputed_raw = core.evaluate(raw.get("evidence") or {}, platform=raw.get("platform"))["checks"]
    mismatches = [k for k, c in (raw.get("checks") or {}).items()
                  if k in recomputed_raw and recomputed_raw[k]["status"] != c.get("status")]
    verdict = core.evaluate(evidence, platform=raw.get("platform"))
    result = verdict["strict_result"]
    if mismatches:
        result = "FAIL"
    consistency = {"checks_in_raw_that_disagree_with_recomputation": mismatches}
    return {
        "kind": ADJUDICATION_KIND,
        "formal_otp23_acceptance": False,
        "note": "Pure re-evaluation of an immutable raw strict result plus offline phone evidence; the "
                "requirements matrix, not this file, adjudicates OTP-23 acceptance.",
        "evaluator_provenance": provenance if provenance is not None else evaluator_provenance(),
        "adjudicated_utc": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strict_result": result,
        "scenario": raw.get("scenario"),
        "platform": raw.get("platform"),
        "checks": verdict["checks"],
        "raw_consistency": consistency,
        "inputs": {
            "raw": {k: raw_record.get(k) for k in ("path", "sha256", "size")} | {
                "strict_result_in_raw": raw.get("strict_result"), "raw_had_phone_evidence": raw_had_phone},
            "phone_files": [{"path": r.get("path"), "source_hash": r.get("source_hash"), "sha256": r.get("sha256"),
                             "size": r.get("size"), "mtime_epoch": r.get("mtime_epoch")} for r in phone_records],
        },
        "phone_validation": validation,
        "evidence": evidence,
    }


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", required=True, help="immutable raw strict_background.py result JSON")
    parser.add_argument("--phone-evidence", action="append", default=[], required=True,
                        help="SERIAL=PATH: one phone's otp23-unsolicited.json with its adb serial (published only "
                             "as a hash); repeat per physical phone. A bare PATH cannot PASS")
    parser.add_argument("--phone-clock-offset-ms", type=float, help="desktop clock minus phone clock, measured")
    parser.add_argument("--phone-clock-uncertainty-ms", type=float, help="uncertainty of that offset (>= 0)")
    parser.add_argument("--phone-clock-source", help="how the offset was measured (free text, recorded)")
    parser.add_argument("--out", required=True, help="NEW adjudication JSON (must not exist)")
    args = parser.parse_args(argv)
    out = pathlib.Path(args.out)
    parsed_inputs = [parse_phone_arg(p) for p in args.phone_evidence]
    inputs = [pathlib.Path(p).resolve() for p in [args.raw] + [path for _, path in parsed_inputs]]
    if out.exists() or out.resolve() in inputs:
        print(f"error: refusing to overwrite existing {out}", file=sys.stderr)
        return 2
    raw_record = read_input(args.raw)
    raw = raw_record.get("json")
    if raw_record.get("error") or not isinstance(raw, dict) or "evidence" not in raw or "checks" not in raw:
        print("error: --raw is not a strict_background result", file=sys.stderr)
        return 2
    clock = None
    if args.phone_clock_offset_ms is not None and args.phone_clock_uncertainty_ms is not None \
            and args.phone_clock_source:
        clock = {"offset_ms": args.phone_clock_offset_ms, "uncertainty_ms": args.phone_clock_uncertainty_ms,
                 "source": args.phone_clock_source}
    document = adjudicate(raw, raw_record, [read_input(path, source) for source, path in parsed_inputs], clock=clock)
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
