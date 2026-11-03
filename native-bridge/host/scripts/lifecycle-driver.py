#!/usr/bin/env python3
"""OTP-24 production Chrome-owned lifecycle / direct-launch driver (Task 10 driver). Standard library only.

    lifecycle-driver.py --print-origin
    lifecycle-driver.py --list-gaps
    lifecycle-driver.py --recover-registration --confirm-no-active-run   (Windows only; restores a crashed run)
    lifecycle-driver.py --case ID --host ABS_EXE --chrome ABS_CHROME --output NEW_DIR --anchor ANCHOR.json --anchor-sha256 PIN
    lifecycle-driver.py --emit-anchor NEW_ANCHOR.json --repo GIT_WORKTREE --binary-path P --binary-sha256 H --binary-size N
                        [--attest-built-from-tree] [--checkout-root PATH]        (ORIGINAL git worktree only)

Run ONLY by the controller on the real platform. This driver never signs, never creates keys, never uses a personal
Chrome profile and never writes PASS: it records measurements and exits 2 (BLOCKED) or 1 (FAIL); a complete run is
still recorded BLOCKED "awaiting adjudication". Exit codes: 1 FAIL (measured product failure), 2 BLOCKED, 3 refusal/usage,
5 driver error (a BLOCKED record was still written when possible; this is NOT a product verdict). Acceptance = validate-evidence.py + separate authenticated review.
TRUST BOUNDARY: this driver and accdrv/* are loaded from the checkout they verify. The anchor therefore detects drift/accidents; it does NOT
authenticate a hostile copy (which could skip verification). Hash the driver files with separately obtained tooling before starting it if
that matters. Not an adversarial-authentication claim.
Source identity: run.json identity comes ONLY from an anchor emitted on the original git worktree (which must itself be the repository top
level; GIT_* scrubbed) and pinned by its out-of-band sha256 (accdrv/anchor.py), for EVERY physical case, git checkout or not; the checked-out bytes, evaluator files and
the actual --host path/sha256/size must match it exactly or the run is BLOCKED. No revision is ever synthesized, and build provenance is
claimed only when the pinned anchor itself attests it (the old --attest-built-from-this-checkout flag is refused).
Not wired into physical-matrix.py (DRIVERS[...].executable stays False) until a real run has been adjudicated.
"""
import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Never read bytecode that arrived with the checkout: send the cache to a fresh empty directory BEFORE any checkout module is imported
# (python -I -B does not stop a planted __pycache__/*.pyc from being read). Interpreter start-up itself is outside this protection.
sys.dont_write_bytecode = True
sys.pycache_prefix = tempfile.mkdtemp(prefix="veles-accdrv-pyc-")
sys.path.insert(0, str(HERE))  # -I drops the script directory; the accdrv package lives beside this file

from accdrv import anchor as anchor_mod, ids, registration, runner  # noqa: E402
from accdrv.errors import Blocked, Refused  # noqa: E402

CHECKOUT_ROOT = runner.HOST_DIR.parents[1]

GAPS = [
    "phones: connect_phone_*, prove_bytes_*, transfers, receipts, fail_phone_*, link-release and pending-cancellation need a controller-attached phone rig (default rig blocks every call)",
    "conditions: radio_off / adapter_absent / synthetic_service_denial and their restore need a controller-attached rig",
    "pipe tap: last JSON and stdout close are unobservable after port/tab/Chrome closure under Chrome-owned launching (shutdown_request excepted)",
    "hygiene monitors: file-write, registry-write and LaunchServices monitors are not implemented; window census must be injected",
    "adapter identity (adapter.id_sha256), phone bindings, wrapper_exception observation and adjudication are not produced by this driver",
    "process exit code is not observable for Chrome-owned launches (only for direct-launch cases)",
    "journal durability on Windows: os.replace is not write-through, so a power loss can lose the journal while the key persists (startup_check then refuses on the leftover key)",
    "trust boundary: the verifier runs from the checkout it verifies, so a hostile copy can skip it; the anchor detects drift, it does not authenticate the copy (pin the driver files with separately obtained tooling)",
    "driver-source trust vs binary trust are separate: the DRIVER CODE is trusted only as far as the operator hashed it externally (not enforced); the HOST BINARY is bound by the pinned sha256/size/path, must itself be operator/root-owned, not group/other-writable and singly linked (st_nlink==1), its directory chain likewise, and is re-hashed at verify, record, after codesign / Windows Authenticode, immediately before every launch, before native connect and every reconnect, and after the run; the check-to-exec window (Chrome's own exec of the host, swap-and-restore, a privileged writer) and Windows ACLs/hard links are NOT closed",
    "bytecode: sys.pycache_prefix is redirected at start-up and any __pycache__/.pyc in an anchored tree is Blocked; code run by an interpreter started before that point is outside the protection",
    "external anchor: proves the checked-out bytes equal what the generator saw and the host path/hash equal the pinned values; it cannot prove the generator's checkout was honest, that the pin travelled securely, or how the host was built (attestation is the generator-side operator's assertion)",
    "WinregBackend, ChromeSession and the macOS codesign path have only been exercised through fakes on Linux",
]


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_evidence_for_driver", HERE / "validate-evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_anchor(args, host, case):
    """(anchor, attest). Raises Refused for unusable flag combinations and Blocked when source identity cannot be established."""
    if args.attest_built_from_this_checkout:
        raise Refused("--attest-built-from-this-checkout is no longer accepted: build attestation must be inside the pinned anchor")
    pinned = bool(args.anchor) + bool(args.anchor_sha256)
    default_cases = HERE.parent / "acceptance" / "cases.json"
    if pinned and os.path.realpath(args.cases) != os.path.realpath(default_cases):
        raise Refused("--cases may not be overridden with --anchor: the verified acceptance/cases.json is the only case source")
    if pinned == 1:
        raise Refused("--anchor and --anchor-sha256 (the pin, from a separate channel) must be given together")
    if pinned:
        family = "windows" if case["platform"] == "windows" else "posix"
        verified = anchor_mod.verify_anchor(args.anchor, args.anchor_sha256, CHECKOUT_ROOT, host, family)
        verified_case = {c["id"]: c for c in json.loads(verified["cases_json_bytes"])["cases"]}.get(case["id"])
        if verified_case != case:
            raise Blocked("the case definition in use differs from the anchored acceptance/cases.json")
        return verified, False
    raise Blocked("physical cases require an externally generated, out-of-band PINNED anchor (--anchor FILE --anchor-sha256 PIN, made with "
                  "--emit-anchor on the original git worktree); no unpinned revision is ever recorded, with or without git")


def emit(args):
    needed = (args.repo, args.binary_path, args.binary_sha256, args.binary_size is not None)
    if not all(needed):
        print("lifecycle-driver: --emit-anchor needs --repo, --binary-path, --binary-sha256 and --binary-size", file=sys.stderr)
        return 3
    try:
        doc = anchor_mod.emit_anchor(args.repo, load_validator(), binary_path=args.binary_path, binary_sha256=args.binary_sha256,
                                     binary_size=args.binary_size, attest_built_from_tree=args.attest_built_from_tree,
                                     checkout_root=args.checkout_root)
        data = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()
        with open(args.emit_anchor, "xb") as f:  # never overwrites
            f.write(data)
    except (Blocked, Refused, OSError) as e:
        print(f"lifecycle-driver: anchor not written: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    print(json.dumps({"output": args.emit_anchor, "anchor_sha256": anchor_mod.sha256_bytes(data), "source_revision": doc["source_revision"],
                      "files": len(doc["source_files"]), "attested": doc["build_attestation"] is not None,
                      "note": "carry anchor_sha256 to the executing machine over a separate channel and pass it as --anchor-sha256"}, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--print-origin", action="store_true")
    ap.add_argument("--list-gaps", action="store_true")
    ap.add_argument("--recover-registration", action="store_true",
                    help="restore the journalled acceptance key in the fixed per-user state dir (Windows only)")
    ap.add_argument("--confirm-no-active-run", action="store_true")
    ap.add_argument("--case")
    ap.add_argument("--host")
    ap.add_argument("--chrome")
    ap.add_argument("--output")
    ap.add_argument("--cases", default=str(HERE.parent / "acceptance" / "cases.json"))
    ap.add_argument("--attest-built-from-this-checkout", action="store_true",
                    help="assert (as the builder) that --host was built from exactly this checkout with the printed origin")
    ap.add_argument("--anchor", help="externally generated anchor JSON (see --emit-anchor); required when the checkout has no usable git")
    ap.add_argument("--anchor-sha256", help="sha256 of the anchor file, obtained out of band; the anchor is rejected unless it matches")
    ap.add_argument("--emit-anchor", metavar="NEW_ANCHOR", help="ORIGINAL git worktree only: write an anchor for --repo")
    ap.add_argument("--repo")
    ap.add_argument("--binary-path")
    ap.add_argument("--binary-sha256")
    ap.add_argument("--binary-size", type=int)
    ap.add_argument("--checkout-root", help="optionally bind the anchor to the absolute path of the checkout it will be verified in")
    ap.add_argument("--attest-built-from-tree", action="store_true",
                    help="(emit) assert, as the builder, that the executable was built from exactly this revision and tree")
    args = ap.parse_args(argv)
    if args.emit_anchor:
        return emit(args)
    if args.print_origin:
        ext_id, origin = ids.read_extension_identity(runner.EXT_DIR)
        print(json.dumps({"extension_id": ext_id, "origin": origin,
                          "build": f"VELES_ALLOWED_ORIGINS={origin} ./gradlew bridgeHostBuild"}, indent=2))
        return 0
    if args.list_gaps:
        print("\n".join(GAPS))
        return 0
    if args.recover_registration:
        try:
            state = registration.state_dir_for(os.environ)
            out = registration.recover(registration.WinregBackend(), state, tempfile.gettempdir(), args.confirm_no_active_run)
            print(json.dumps(out, indent=2))
            return 0
        except (Blocked, registration.RestoreFailed, Refused, OSError) as e:
            print(f"lifecycle-driver: recovery not completed: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
    if not (args.case and args.host and args.chrome is not None and args.output):
        print("lifecycle-driver: --case, --host, --chrome and --output are required", file=sys.stderr)
        return 3
    cases = {c["id"]: c for c in json.loads(Path(args.cases).read_text())["cases"]}
    case = cases.get(args.case)
    if case is None:
        print(f"lifecycle-driver: unknown case {args.case!r}", file=sys.stderr)
        return 3
    host = Path(args.host)
    try:
        if not host.is_absolute():
            raise Refused("--host must be an absolute path")
        try:
            anchor, attest = resolve_anchor(args, host, case)
        except Blocked as e:
            if Path(args.output).exists():
                raise Refused(f"{args.output} exists; evidence is never overwritten")
            doc = runner.blocked_record(case, host, Path(args.output), [str(e)])
            print(json.dumps({"case": case["id"], "declared_status": "BLOCKED", "output": args.output, "blocker": doc.get("blocker")}, indent=2))
            return 2
        doc = runner.execute_case(case, host, args.chrome, args.output, anchor, attest=attest)
    except (Refused, ValueError) as e:
        print(f"lifecycle-driver: refused: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    except Exception as e:  # noqa: BLE001 - a driver defect is not a product FAIL (exit 1 is reserved for that)
        print(f"lifecycle-driver: DRIVER ERROR {type(e).__name__} (not a product verdict; see any partial output)", file=sys.stderr)
        return 5
    print(json.dumps({"case": case["id"], "declared_status": doc["declared_status"], "output": args.output,
                      "blocker": doc.get("blocker"), "failures": doc.get("failures")}, indent=2))
    if doc.get("driver_errors"):
        return 5
    return {"FAIL": 1, "BLOCKED": 2}[doc["declared_status"]]


if __name__ == "__main__":
    sys.exit(main())
