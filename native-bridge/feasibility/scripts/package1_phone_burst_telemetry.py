"""OTP-23 package1 B-5 transport-telemetry burst analysis (evidence-only).

Correlates, for ONE teardown run:
  - the host's own trace (`core.parse_trace`): the last `disconnect requested epoch_ms` anchor and the
    interpolated own-exit epoch (PC clock),
  - each phone's pulled `otp23-transport-evidence.json` (`-e otp23_transport_evidence true`, one
    pre-declared `otp23_run_id` per run),
  - the fresh pre-run clock offsets (`clock-offset-<batch>.json`),
  - the per-phone fixture-stop markers (phone-local epoch ms recorded at stop creation, plus the PC
    epoch at the same moment),
through the reviewed pure validator `scenario_matrix_core.phone_burst_association` — including its
fixture-stop confound guard (disconnect strictly before the stop marker; stop at least bound + clock
uncertainty after own exit; same central prefix across phones; same run id and fixture source).

    python scripts/package1_phone_burst_telemetry.py <dir> --run-id RUN --trace trace.log \\
        --clock clock.json --phone 2be02e41=transport-2be02e41.json --phone 864a3db0=... \\
        --stop 2be02e41=phone-ms,pc-ms --stop 864a3db0=phone-ms,pc-ms --out out.json
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scenario_matrix_core as core  # noqa: E402 - same directory

EXPECTED_SERIALS = ("2be02e41", "864a3db0")
PHONE_LABELS = {"2be02e41": "6T", "864a3db0": "10Pro"}
FIXTURE_PACKAGE = "me.nagaev.veles.otp23"


def host_exit_epoch_ms(trace_path):
    """(own_exit_epoch_estimate, anchor_disconnect_epoch) from the host trace, or (None, None)."""
    parsed = core.parse_trace(pathlib.Path(trace_path).read_text(encoding="utf-8", errors="replace"))
    requested = parsed["disconnect_requested"]
    if parsed["exit"] is None or not requested:
        return None, None
    anchor = requested[-1]["epoch_ms"]
    anchor_at = requested[-1]["at_ms"]
    return anchor + (parsed["exit"]["at_ms"] - anchor_at), anchor


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dir", help="run directory holding the trace and the pulled transport docs")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--trace", required=True, help="host trace filename inside <dir>")
    parser.add_argument("--clock", required=True, help="clock-offset json (fresh pre-run probe)")
    parser.add_argument("--phone", action="append", required=True, metavar="SERIAL=FILE",
                        help="serial=transport evidence file (inside <dir> or absolute)")
    parser.add_argument("--stop", action="append", metavar="SERIAL=PHONE_MS,PC_MS",
                        help="fixture stop marker per serial (epoch ms phone-local, then PC at stop)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    run_dir = pathlib.Path(args.dir)
    exit_ms, anchor_ms = host_exit_epoch_ms(run_dir / args.trace)
    probes = {p["serial"]: p for p in json.load(open(args.clock))["probes"]}
    phone_docs, stop_map = [], {}
    for binding in args.phone:
        serial, _, file_name = binding.partition("=")
        telemetry = json.loads((run_dir / file_name).read_text(encoding="utf-8"))
        document = {"serial": serial, "telemetry": telemetry}
        clock = probes.get(serial)
        if clock and clock.get("available"):
            document["clock"] = clock
        phone_docs.append(document)
    for binding in args.stop or ():
        serial, _, marker = binding.partition("=")
        phone_ms, _, pc_ms = marker.partition(",")
        stop_map[serial] = {"epoch_phone_ms": int(phone_ms), "epoch_pc_ms": int(pc_ms)}
    for document in phone_docs:
        document["fixture_stop"] = stop_map.get(document["serial"])

    verdict = core.phone_burst_association(phone_docs, exit_ms, anchor_ms, EXPECTED_SERIALS,
                                           run_id=args.run_id, source_package=FIXTURE_PACKAGE)
    document = {
        "what": "OTP-23 package1 B-5 phone burst association from fixture transport telemetry",
        "run_id": args.run_id,
        "bound_s_predeclared": core.PACKAGE1_BOUND_S,
        "fixture_stop_required": "disconnect strictly before the stop marker; stop >= bound + clock "
                                 "uncertainty after the host's own exit (fixture-stop confound guard)",
        "host_exit_epoch_ms_estimate": exit_ms,
        "anchor_disconnect_epoch_ms": anchor_ms,
        "stop_markers": stop_map,
        "verdict": verdict,
    }
    pathlib.Path(args.out).write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(f"B-5 {verdict['status']} problems={verdict['problems'] if verdict['problems'] else 'none'}")
    for serial, detail in verdict["phones"].items():
        burst = detail.get("burst")
        print(f"  {PHONE_LABELS.get(serial, serial)} {serial}: associated={detail.get('associated')} "
              f"gap_vs_exit={burst['gap_vs_host_exit_ms'] if burst else None} ms "
              f"within_bound={(burst or {}).get('within_bound')}; stop={detail.get('fixture_stop')}")
    print(f"-> {args.out}")
    return 0 if verdict["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())