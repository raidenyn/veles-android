#!/usr/bin/env python3
"""OTP-24 production physical-acceptance case runner (Task 10). Standard library only.

    physical-matrix.py --list
    physical-matrix.py --check-matrix
    physical-matrix.py --case <id> --host <absolute-executable> --output <new-dir>

What this does, and deliberately does not do:

* It validates the production executable and the source/evaluator identities BEFORE any execution:
  the host must be an absolute path to a regular file named `veles-native-host[.exe]` (the Tauri
  scaffold `otp23-host*`, the `host-test-driver` and anything else are refused, exit 3). The output
  directory must not exist (evidence is never overwritten).
* It then asks the per-platform driver whether it can run. No executable driver exists yet (see
  `DRIVERS[...].gaps`: the reviewed OTP-23 Chrome/phone utilities are Windows-/macOS-only, hard-wired to the
  OTP-23 scaffold host and trace sink, and have never been exercised against the production binary).
  Therefore the result is a BLOCKED run record (exit 2) with the exact blockers, the executable hash
  and the source/evaluator identities, **no captures and no verdict beyond BLOCKED**. Nothing here
  fabricates a capture, a phone binding, a timestamp or a result.
* A future driver must set `executable = True`, produce raw captures + a checksum catalog, and leave
  adjudication to a separate party; `validate-evidence.py` then decides PASS. This script never writes PASS.

Pending requests (cases.json `pending_request_handling`): abrupt-EOF lifecycle cases (port.disconnect, tab close, Chrome-owned
pipe EOF) record the in-flight request's outcome as "unknown" and never claim host cancellation (owner option 2); explicit shutdown
and per-phone failure require a correlated, non-synthesized host error specific to the trigger. Matrix rows stay BLOCKED either way.

Exit codes: 2 BLOCKED record written, 3 usage/refusal, 0 for --list / --check-matrix success.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOST_DIR = HERE.parent
REPO = HOST_DIR.parents[1]
CASES = HOST_DIR / "acceptance" / "cases.json"
MATRIX = HOST_DIR / "acceptance" / "matrix.json"
SCHEMA = "veles-otp24-physical-evidence/1"
PRODUCTION_NAMES = {"veles-native-host", "veles-native-host.exe"}

Driver = namedtuple("Driver", "executable gaps")

_WIN_GAPS = [
    "No production Chrome/phone driver exists for Windows. The reviewed OTP-23 utilities "
    "(native-bridge/feasibility/scripts/chrome_validate.py, physical_validate.py, scenario_matrix_core.py) were "
    "NOT adapted: they use Windows ctypes/PowerShell inspection, HKCU registration of the OTP-23 scaffold hosts "
    "(me.nagaev.veles.otp23.*, otp23-host.exe) and the VELES_OTP23_TRACE sink for MTU, which the production host "
    "deliberately lacks. They have never been executed against veles-native-host.",
    "Needs an authorized, leased physical Windows PC with a Bluetooth adapter, stable Chrome, two phones with the "
    "synthetic fixture installed under the approved Android debug key, and independent link-release measurement.",
]
_MAC_GAPS = [
    "No production Chrome/phone driver exists for macOS. The reviewed OTP-23 utilities "
    "(native-bridge/feasibility/scripts/macos_poc.py, macos_cdp.py, macos_install.py) drive the otp23.* connector "
    "API and VELES_OTP23_TRACE of the scaffold host and were NOT adapted or run against veles-native-host.",
    "Needs an authorized, awake Mac in the Aqua context with the approved signed test-vehicle bundle (production "
    "identity and TCC remain deferred), stable Chrome, two phones and independent link-release measurement.",
]
DRIVERS = {"windows": Driver(False, _WIN_GAPS), "macos": Driver(False, _MAC_GAPS)}


def host_platform():
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def load_cases(path=CASES):
    return {c["id"]: c for c in json.loads(Path(path).read_text())["cases"]}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_matrix(cases_path=CASES, matrix_path=MATRIX):
    """Problems (empty = consistent): a row per case, none PASS, none with evidence, acceptance open."""
    problems = []
    cases = load_cases(cases_path)
    matrix = json.loads(Path(matrix_path).read_text())
    rows = {r.get("id"): r for r in matrix.get("rows", [])}
    if set(rows) != set(cases):
        problems.append(f"matrix rows differ from cases: {sorted(set(rows) ^ set(cases))}")
    for rid, r in rows.items():
        if r.get("status") not in ("NOT RUN", "BLOCKED"):
            problems.append(f"{rid}: status {r.get('status')!r} (only NOT RUN/BLOCKED are allowed without validated fresh evidence)")
        if r.get("evidence"):
            problems.append(f"{rid}: evidence listed without a validated run")
        if not r.get("blocker"):
            problems.append(f"{rid}: no blocker recorded")
    if matrix.get("decision") != "OPEN" or matrix.get("physical_acceptance_complete") is not False:
        problems.append("matrix must keep decision OPEN and physical_acceptance_complete false")
    return problems


def git(*args):
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, timeout=60, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _validator():
    """Load the sibling validator by path (isolated mode `-I` drops the script directory from sys.path)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("validate_evidence_for_matrix", HERE / "validate-evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_identity():
    """Revision plus a hash over tracked AND untracked bytes (same function the validator anchors against)."""
    rev = git("rev-parse", "HEAD")
    revision = rev.decode().strip() if rev and re.match(rb"^[0-9a-f]{40}\s*$", rev) else None
    try:
        tree = _validator().source_tree_sha256(REPO)
    except (OSError, subprocess.SubprocessError, _validator().AnchorError):
        tree = None
    return {"revision": revision, "tree_sha256": tree}


def evaluator_identity():
    rev = source_identity()["revision"]
    files = {rel: sha256_bytes((HOST_DIR / rel).read_bytes()) for rel in _validator().EVALUATOR_FILES}
    return {"revision": rev, "files": files}


def preflight_driver(case):
    """(status, reasons). BLOCKED unless an executable driver exists for this host platform AND case."""
    reasons = []
    plat = host_platform()
    if case["platform"] != plat:
        reasons.append(f"case targets {case['platform']} but this machine is {plat}; physical cases run only on their own platform")
    driver = DRIVERS.get(case["platform"])
    if driver is None or not driver.executable:
        reasons.extend(driver.gaps if driver else [f"no driver for platform {case['platform']}"])
    return ("BLOCKED", reasons) if reasons else ("READY", [])


def refuse(message):
    print(f"physical-matrix: refused: {message}", file=sys.stderr)
    return 3


def write_blocked_record(case, host, out, reasons):
    exe = Path(host)
    data = exe.read_bytes()
    out.mkdir(parents=True)
    manifest = {
        "schema": SCHEMA,
        "run_id": f"{case['id']}@{now()}",
        "case_id": case["id"],
        "declared_status": "BLOCKED",
        "blocker": " | ".join(reasons),
        "operator": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
        "recorded_at": now(),
        "source": source_identity(),
        "evaluator": evaluator_identity(),
        "binary": {"kind": "production", "name": exe.name, "path": str(exe), "sha256": sha256_bytes(data), "size": len(data),
                   "build_command": "./gradlew bridgeHostBuild", "built_from_source_revision": None},
        "captures": [],
        "failed_setups": [],
        "note": "Identity preflight only. No device, Chrome, phone or signing identity was used; nothing was executed.",
    }
    (out / "SHA256SUMS").write_text("")
    manifest["checksum_catalog"] = {"path": "SHA256SUMS", "sha256": sha256_bytes(b"")}
    (out / "run.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check-matrix", action="store_true")
    ap.add_argument("--case")
    ap.add_argument("--host")
    ap.add_argument("--output")
    ap.add_argument("--cases", default=str(CASES))
    args = ap.parse_args(argv)
    if args.list:
        for c in load_cases(args.cases).values():
            print(f"{c['id']}\t{c['kind']}\t{c['description']}")
        return 0
    if args.check_matrix:
        problems = check_matrix(args.cases, MATRIX)
        for p in problems:
            print(p, file=sys.stderr)
        print("matrix consistent" if not problems else "matrix INCONSISTENT")
        return 0 if not problems else 1
    if not (args.case and args.host and args.output):
        return refuse("--case, --host and --output are required")
    cases = load_cases(args.cases)
    case = cases.get(args.case)
    if case is None:
        return refuse(f"unknown case {args.case!r}; see --list")
    host = Path(args.host)
    if not host.is_absolute():
        return refuse("--host must be an absolute path to the production executable")
    if host.name not in PRODUCTION_NAMES:
        return refuse(f"{host.name!r} is not the production host (veles-native-host[.exe]); the scaffold and test driver are not OTP-24 acceptance targets")
    if host.is_symlink() or not host.is_file():
        return refuse(f"{host} is not a regular file")
    expected = "veles-native-host.exe" if case["platform"] == "windows" else "veles-native-host"
    if host.name != expected:
        return refuse(f"{host.name!r} does not match platform {case['platform']} (expected {expected})")
    out = Path(args.output).absolute()
    if out.exists():
        return refuse(f"{out} already exists; evidence is never overwritten")
    status, reasons = preflight_driver(case)
    if status != "READY":
        write_blocked_record(case, host, out, reasons)
        print(json.dumps({"case": case["id"], "status": "BLOCKED", "output": str(out), "blockers": reasons}, indent=2))
        return 2
    return refuse("driver reported READY but no executable driver is implemented in this tool")  # unreachable today


if __name__ == "__main__":
    sys.exit(main())
