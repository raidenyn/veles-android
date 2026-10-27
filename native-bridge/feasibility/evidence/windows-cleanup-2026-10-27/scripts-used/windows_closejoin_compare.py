"""OTP-23 Windows close-begin-matched comparison driver (direct launch, synthetic bytes only).

Runs 3+3 controlled relaunch runs against ONE host binary: the FIRST process connects both phones
and exits through stdin EOF; the SECOND process connects both after a gap matched from a COMMON
absolute anchor that exists in both arms. The awaited/legacy behaviour arms are selected per run
by the `VELES_OTP23_DETACHED_CLOSE` environment variable of the spawned host processes, so one
binary hash covers both arms.

Anchors and gaps (all on the PC's one clock, epoch milliseconds, traced by the host itself):
  anchor    = max `conn N disconnect requested epoch_ms=` of the FIRST host (traced before the
              queue/detach decision, so the legacy arm has it; `close begin` usually does not
              exist there)
  connect   = min `conn N connect worker started epoch_ms=` of the SECOND host
  gap_ms    = connect - anchor (the recorded actual gap; no configured sleep is claimed as one)
  close_ms  = per-conn FIRST-host close duration (`close begin` -> last `close step` rel ms) -
              the only added delay of the awaited arm; recorded so delay cannot explain a
              difference. After the SECOND host's discovery completes the driver waits until
              anchor+target before issuing the connect, so a fast discovery cannot widen the gap.

Not an acceptance driver: it records what each connect returned, which trace lines the hosts
wrote, and the validity flags of the OTP-23 fix preflight (released-before-exit + shutdown
joined N/N for the awaited arm; `closing worker detached` for the legacy arm).

    python scripts/windows_closejoin_compare.py --host-binary HB --out-dir OUT --discover
    python scripts/windows_closejoin_compare.py --host-binary HB --out-dir OUT
"""

import argparse
import json
import os
import pathlib
import re
import subprocess
import time
from datetime import datetime, timezone

import scenario_matrix as sm
import scenario_matrix_core as core

LINE = re.compile(r"\[ *(\d+)ms pid=\d+\] (.*)")
EPOCH_LINE = re.compile(r"conn (\S+) disconnect requested epoch_ms=(\d+)")
CONNECT_LINE = re.compile(r"conn (\S+) connect worker started epoch_ms=(\d+)")
CLOSE_BEGIN_LINE = re.compile(r"conn (\S+) close begin")
CLOSE_STEP_LINE = re.compile(r"conn (\S+) close step (\S+) at (\d+)ms (ok|err hresult=0x[0-9a-f]+)")
RELEASED_LINE = re.compile(r"conn (\S+) released ")
DETACHED_LINE = re.compile(r"conn (\S+) closing worker detached")
SHUTDOWN_JOINED_LINE = re.compile(r"(budget exceeded: )?shutdown joined (\d+)/(\d+) within (\d+)ms")
SETUP_LINE = re.compile(r"conn (\S+) setup failed: stage=(\S+) retryable=(\S+)")
EXIT_LINE = re.compile(r"exit code=(\S+)")


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def epoch_ms():
    return int(time.time() * 1000)


def parse_trace(path):
    """Host-trace summary used for anchors, close durations and the preflight validity flags."""
    parsed = {
        "disconnects": {},  # conn -> [epoch_ms, ...]
        "connects": {},  # conn -> [epoch_ms, ...]
        "close_begin_rel": {},  # conn -> first `close begin` rel ms
        "close_steps": {},  # conn -> [(rel_ms, name, outcome)]
        "released": {},  # conn -> first `released` line index
        "detached": [],  # conns with the legacy detached trace
        "shutdown_joined": None,  # (joined, total, within_ms)
        "shutdown_budget_exceeded": False,
        "setups": [],  # conn/stage/retryable of setup failures
        "exit_rel": None,
        "exit_index": None,
        "exit_code": None,
        "status_lines": [],
    }
    for index, line in enumerate(pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines()):
        m = LINE.match(line)
        rest = m.group(2) if m else line
        rel = int(m.group(1)) if m else None
        if m := EPOCH_LINE.search(rest):
            parsed["disconnects"].setdefault(m.group(1), []).append(int(m.group(2)))
        if m := CONNECT_LINE.search(rest):
            parsed["connects"].setdefault(m.group(1), []).append(int(m.group(2)))
        if m := CLOSE_BEGIN_LINE.search(rest):
            parsed["close_begin_rel"].setdefault(m.group(1), rel)
        if m := CLOSE_STEP_LINE.search(rest):
            parsed["close_steps"].setdefault(m.group(1), []).append((rel, m.group(2), m.group(4)))
        if m := RELEASED_LINE.search(rest):
            parsed["released"].setdefault(m.group(1), index)
        if m := DETACHED_LINE.search(rest):
            parsed["detached"].append(m.group(1))
        if m := SHUTDOWN_JOINED_LINE.search(rest):
            parsed["shutdown_budget_exceeded"] = parsed["shutdown_budget_exceeded"] or bool(m.group(1))
            parsed["shutdown_joined"] = (int(m.group(2)), int(m.group(3)), int(m.group(4)))
        if m := SETUP_LINE.search(rest):
            parsed["setups"].append({"conn": m.group(1), "stage": m.group(2), "retryable": m.group(3)})
        if m := EXIT_LINE.search(rest):
            parsed["exit_rel"], parsed["exit_index"], parsed["exit_code"] = rel, index, m.group(1)
        if "GattCommunicationStatus=" in rest:
            parsed["status_lines"].append(rest)
    return parsed


def close_durations(parsed):
    """Per-conn FIRST-host close duration: `close begin` rel ms to the LAST `close step` rel ms."""
    durations = {}
    for conn, begin in parsed["close_begin_rel"].items():
        steps = parsed["close_steps"].get(conn, [])
        if begin is not None and steps:
            durations[conn] = max(step[0] for step in steps) - begin
    return durations


def validity_for_arm(arm, p):
    """Preflight validity of the FIRST (close-bearing) host phase.

    Awaited: every disconnected conn reached `released` BEFORE the exit line and shutdown joined
    every expected handle within the budget. Legacy: every disconnected conn shows the detached
    trace (the close was truncated by design of that arm).
    """
    conns = sorted(p["disconnects"])
    shutdown = p["shutdown_joined"]
    if arm == "awaited":
        released_ok = bool(conns) and all(conn in p["released"] for conn in conns) and \
            all(p["released"][conn] < p["exit_index"] for conn in conns if p["exit_index"] is not None)
        valid = released_ok and shutdown is not None and shutdown[0] == shutdown[1] == len(conns) \
            and not p["shutdown_budget_exceeded"]
        reason = None if valid else (
            f"conns={conns} released={sorted(p['released'])} exit_code={p['exit_code']} "
            f"shutdown={shutdown} exceeded={p['shutdown_budget_exceeded']}"
        )
        return valid, reason
    detached = sorted(set(p["detached"]))
    valid = bool(conns) and detached == conns
    reason = None if valid else f"conns={conns} detached={detached}"
    return valid, reason


def host_procs_alive():
    try:
        listing = subprocess.run(["tasklist", "/FI", "IMAGENAME eq otp23-host.exe"],
                                 capture_output=True, timeout=20).stdout.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - recorded, treated as unknown
        return None
    return listing.count("otp23-host.exe")


def quiescent(previous_exit_epoch_ms, min_quiescent_s):
    """Declared precondition before a run: no host process alive and at least min_quiescent_s
    since the previous run's last host exit, so a prior pair cannot contaminate the next FIRST
    process. No blind retry: the actual wait is recorded."""
    started = time.monotonic()
    while True:
        alive = host_procs_alive()
        waited = time.monotonic() - started
        since_previous = None if previous_exit_epoch_ms is None else (epoch_ms() - previous_exit_epoch_ms) / 1000.0
        ready = alive == 0 and (since_previous is None or since_previous >= min_quiescent_s)
        if ready or waited > 120:
            record = {"host_procs": alive, "waited_s": round(waited, 3), "satisfied": ready, "at_utc": utc()}
            if since_previous is not None:
                record["since_previous_exit_s"] = round(since_previous, 3)
            return record
        time.sleep(0.5)


def arm_env(arm, base):
    env = dict(base)
    env.pop("VELES_OTP23_DETACHED_CLOSE", None)
    if arm == "legacy":
        env["VELES_OTP23_DETACHED_CLOSE"] = "1"
    return env


def run_phase(binary, origin, out_dir, name, wanted, discover_s, env, gate=None, gap_s=0.0):
    trace = out_dir / f"{name}.host-trace.log"
    env = dict(env, VELES_OTP23_TRACE=str(trace))
    record = {
        "phase": name,
        "arm": "legacy" if env.get("VELES_OTP23_DETACHED_CLOSE") == "1" else "awaited",
        "started_utc": utc(),
        "host_pid": None,
        "wanted": wanted,
        "devices": {},
        "connects": [],
        "trace": trace.name,
    }
    if gate:
        record["gate"] = gate
    host = sm.DirectHost([binary, origin], env, 0)
    try:
        record["host_pid"] = host.proc.pid
        ready = host.reply(host.request("hello"), 20)
        host.api_version = (ready or {}).get("api_version")
        started = host.reply(host.request("start_scan", service_uuid=sm.VELES_SERVICE), 20)
        record["scan"] = core.safe_reply(started)
        deadline = time.monotonic() + discover_s
        found = {}
        while time.monotonic() < deadline:
            frames = host.events_of("device_found", 1.0, count=len(found) + 1)
            for frame in frames:
                found[frame["device_handle"]] = core.label_hash(frame.get("label"))[:8]
            if wanted == ["any1"] and len(found) >= 1:
                break
            if wanted in ([], ["all"]) and len(found) >= 2:
                break
        record["devices"] = sorted(found.values())
        record["devices_found_utc"] = utc()
        if not wanted:
            return record
        host.reply(host.request("stop_scan", scan_handle=started["scan_handle"]), 20)
        # Tokens (as in relaunch_control): any1 = first discovered device; all = every device
        # found (up to two); otherwise the wanted list already holds label-hash prefixes.
        if wanted == ["any1"]:
            wanted = sorted(found.values())[:1]
        elif wanted == ["all"]:
            wanted = sorted(found.values())
        record["wanted_resolved"] = wanted
        # The gap gate: the connect may only START once anchor+target has arrived (so a fast
        # discovery cannot widen the actual gap); when discovery was SLOWER the actual gap is
        # larger than the target and is what gets recorded.
        target = None
        if gate and gate.get("anchor_epoch_ms"):
            target = gate["anchor_epoch_ms"] + gate["target_gap_ms"]
            gate_wait = max(0.0, (target - epoch_ms()) / 1000.0)
            if gate_wait:
                record["gate_wait_s"] = round(gate_wait, 3)
                time.sleep(gate_wait)
            record["gate_met"] = epoch_ms() >= target
        chosen = [(handle, label) for handle, label in found.items() if label in wanted]
        record["connect_started_utc"] = utc()
        record["connect_started_epoch_ms"] = epoch_ms()
        ids = [(host.request("connect", device_handle=handle), label) for handle, label in chosen]
        for request_id, label in ids:
            reply = host.reply(request_id, 70)
            record["connects"].append({"label_hash8": label, "reply": core.safe_reply(reply), "at_utc": utc()})
        record["missing_wanted"] = sorted(set(wanted) - {label for _, label in chosen})
        if gap_s:
            time.sleep(gap_s)
        host.proc.stdin.close()  # EOF, not shutdown
        try:
            record["exit_code"] = host.proc.wait(timeout=15)
        except Exception:  # noqa: BLE001 - recorded, then reaped in finally
            record["exit_code"] = "timeout"
        record["exited_utc"] = utc()
        record["exited_epoch_ms"] = epoch_ms()
    finally:
        host.kill()
        record["finished_utc"] = utc()
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host-binary", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--runs", type=int, default=6)
    ap.add_argument("--order", default="awaited,legacy,legacy,awaited,awaited,legacy",
                    help="comma-separated arm per run; 6 runs with each arm 3 times")
    ap.add_argument("--target-gap-from-close-begin-ms", type=int, default=1500,
                    help="connect may start only once anchor+target epoch arrived; the RECORDED gap is the actual one")
    ap.add_argument("--gap-s", type=float, default=0.0, help="driver sleep before the FIRST host's EOF (default 0)")
    ap.add_argument("--min-quiescent-s", type=float, default=12.0)
    ap.add_argument("--discover-s", type=float, default=30.0)
    ap.add_argument("--adb-serials", default="", help="comma-separated serials for the raw dumpsys capture")
    args = ap.parse_args()
    from macos_install import read_identity
    origin = read_identity()["origin"]

    scripts_dir = pathlib.Path(__file__).resolve().parent
    source_hashes = {
        path.name: core.file_sha256(str(path)) for path in (scripts_dir.parent / "src").glob("*.rs")
    }
    out_dir = pathlib.Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=False)
    wanted = [] if args.discover else ["all"]

    result = {
        "started_utc": utc(),
        "host_binary_sha256": core.file_sha256(args.host_binary),
        "source_sha256": source_hashes,
        "args": vars(args),
        "runs": [],
        "dumpsys_raw": {},
        "finished_utc": None,
    }
    order = (args.order.split(",") * args.runs)[: args.runs]

    previous_exit = None
    for index, arm in enumerate(order):
        run_dir = out_dir / f"run-{index + 1}-{arm}"
        run_dir.mkdir(parents=True)
        run = {
            "index": index + 1,
            "arm": arm,
            "dir": run_dir.name,
            "quiescent": quiescent(previous_exit, args.min_quiescent_s),
        }
        result["runs"].append(run)
        if not run["quiescent"]["satisfied"]:
            run["validity"] = "aborted"
            run["validity_reason"] = f"quiescent precondition not met: {run['quiescent']}"
            break

        env = arm_env(arm, os.environ)
        first = run_phase(args.host_binary, origin, run_dir, "first", wanted, args.discover_s, env, gap_s=args.gap_s)
        first["trace_parsed"] = parse_trace(run_dir / first["trace"])
        anchors = [max(epochs) for epochs in first["trace_parsed"]["disconnects"].values()]
        anchor = max(anchors, default=None)
        if args.discover:
            run["first"] = first
            break

        # SECOND process: discovered fresh; the connect is gated to anchor+target when the
        # discovery is faster than the target gap.
        second = run_phase(
            args.host_binary, origin, run_dir, "second", wanted, args.discover_s, env,
            gate={"anchor_epoch_ms": anchor, "target_gap_ms": args.target_gap_from_close_begin_ms},
        )
        second["trace_parsed"] = parse_trace(run_dir / second["trace"])
        connect_epochs = [min(epochs) for epochs in second["trace_parsed"]["connects"].values()]
        connect_epoch = min(connect_epochs, default=None)
        run.update({
            "first": first,
            "second": second,
            "anchor_epoch_ms": anchor,
            "connect_epoch_ms": connect_epoch,
            "gap_ms": (connect_epoch - anchor) if connect_epoch is not None and anchor is not None else None,
            "gap_target_ms": args.target_gap_from_close_begin_ms,
            "close_durations_ms": close_durations(first["trace_parsed"]),
            # A connect reply is `{"type": "connected"}` on success; anything else is recorded
            # raw in `connects` and flagged here for the per-arm denial count.
            "denial_replies": [c for c in second["connects"] if (c["reply"] or {}).get("type") != "connected"],
        })
        valid, reason = validity_for_arm(arm, first["trace_parsed"])
        run["validity"], run["validity_reason"] = ("valid" if valid else "invalid"), reason
        previous_exit = second.get("exited_epoch_ms")

    if args.adb_serials and not args.discover:
        for serial in [s for s in args.adb_serials.split(",") if s]:
            target_file = out_dir / f"dumpsys-{serial}-RAW.txt"
            try:
                raw = subprocess.run(["adb", "-s", serial, "shell", "dumpsys", "bluetooth_manager"],
                                     capture_output=True, timeout=120).stdout
                target_file.write_bytes(raw)
                result["dumpsys_raw"][serial] = target_file.name
            except Exception as error:  # noqa: BLE001 - recorded
                result["dumpsys_raw"][serial] = f"failed: {error!r}"

    result["finished_utc"] = utc()
    (out_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    summary = [
        {k: r.get(k) for k in ("index", "arm", "gap_ms", "gap_target_ms", "close_durations_ms",
                               "validity", "validity_reason")}
        for r in result["runs"]
    ]
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()