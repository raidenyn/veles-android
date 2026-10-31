"""OTP-23 Mac M-08 retake 2026-10-29: per-attempt acceptance read-out (reads the driver's own JSON; adjudicates
nothing in the matrix). Writes <attempt>/acceptance-check.json and <attempt>/stops.json (fixture-stop brackets
parsed verbatim from phones/stops.raw.txt: stop_min = in-shell phone date right BEFORE the touch, stop_max = right
AFTER it). Usage: python3 retake_check.py <attempt-dir> <scenario> <n>
"""
import hashlib
import json
import pathlib
import re
import sys

attempt, scen, n = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
mac = attempt / "mac" / f"{scen}-{n}"
doc = json.loads((mac / f"{scen}.json").read_text())
life = json.loads((mac / "lifecycle.json").read_text())
det = doc["detail"]
samples = det.get("probe_during") or []
host_pid = str(det.get("integrity", {}).get("host") or "")
rows = []
for s in samples:
    per = s["probe"]["per_pid"]
    ctl = s["summary"]["controls"]
    host_rows = {pid: r for pid, r in per.items() if r.get("top_visible") == 0 and r.get("all_layers") == 0}
    rows.append({"label": s["label"], "usable": s["summary"]["usable"], "problems": s["summary"]["problems"],
                 "chrome_visible_top_level": ctl["chrome_visible_top_level"],
                 "chrome_cdp_listener_present": ctl["chrome_cdp_listener_present"],
                 "host_top_visible": s["summary"]["host_windows"]["top_visible"],
                 "per_pid": {pid: {"all_layers": r["all_layers"], "tcp4": r["tcp4"], "pid_alive": r["pid_alive"]}
                             for pid, r in per.items()},
                 "context_audit_usable": s["summary"]["context_audit"]["usable"],
                 "launchctl_managername": s["summary"]["context_audit"].get("launchctl_managername")})
diag_lines = [json.loads(l) for l in (mac / "lsof-diagnostics.jsonl").read_text().splitlines() if l.strip()] \
    if (mac / "lsof-diagnostics.jsonl").is_file() else []
diag_classes = sorted({("clean_stderr" if d["classification"] == "clean_stderr" else
                        ("benign" if isinstance(d["classification"], dict) and d["classification"].get("benign")
                         else "OTHER")) for d in diag_lines})
tcp4_available = sum(1 for s in samples for r in s["probe"]["per_pid"].values() if r["tcp4"] != "unavailable")
td = det["teardown"]
chrome_scen = scen in ("shutdown", "port-disconnect", "tab-close")
mechanism_ok = (td.get("trace_mechanism") == "shutdown" and td.get("host_output_order", [None])[-1] == "shutdown_complete"
                ) if scen == "shutdown" else (td.get("trace_mechanism") == "stdin_closed"
                                              and "shutdown_complete" not in td.get("host_output_order", []))
host_pids = set()
for s in samples:
    for pid, r in s["probe"]["per_pid"].items():
        if r["all_layers"] == 0:
            host_pids.add(pid)
checks = {
    "status_pass": doc["status"] == "PASS",
    "six_samples": len(samples) == 6,
    "all_samples_usable": all(r["usable"] for r in rows) and len(rows) == 6,
    "positive_controls_every_sample": all(r["chrome_visible_top_level"] >= 1 and r["chrome_cdp_listener_present"] is True
                                          for r in rows),
    "host_all_layers_zero_every_sample": all(any(v["all_layers"] == 0 for v in r["per_pid"].values()) for r in rows),
    "host_tcp4_empty_every_sample": all(any(v["all_layers"] == 0 and v["tcp4"] == [] for v in r["per_pid"].values())
                                        for r in rows),
    "host_alive_every_sample": all(all(v["pid_alive"] is True for v in r["per_pid"].values()) for r in rows),
    "context_audit_usable_every_sample": all(r["context_audit_usable"] for r in rows),
    "two_distinct_16B_link_proofs": (len(det.get("link_proof") or []) == 2
                                     and all(p["status"] == "PASS" and p["size"] == 16 for p in det["link_proof"])
                                     and len({p["request_sha256"] for p in det["link_proof"]}) == 2),
    "diag_lines_all_benign_or_clean": bool(diag_lines) and diag_classes and set(diag_classes) <= {"benign", "clean_stderr"},
    "diag_lines_ge_available_lsof_results": len(diag_lines) >= tcp4_available,
    "teardown_mechanism_ok": mechanism_ok,
    "trace_exit_code_0_drained": td.get("trace_exit", {}).get("code") == 0 and td.get("trace_exit", {}).get("stdout_drained") is True,
    "gone_within_10s": td.get("owned_gone_after_s") is not None and td["owned_gone_after_s"] <= 10,
    "teardown_problems_none": td.get("problems") == [],
    "cleanup_problems_none": det.get("cleanup_problems") == [],
    "no_survivors": life.get("surviving_owned_hosts_after_run") == [],
    "wrappers_none": det.get("integrity", {}).get("wrapper") == [],
    "mac_released_count_recorded_0": td.get("trace_released_count") == 0,
}
stops = []
for line in (attempt / "phones" / "stops.raw.txt").read_text().splitlines():
    m = re.match(r"(\S+) (\S+) mac_before_ms=(\d+) phone_adb_before_ms=(\d+) in_shell_before_after_touch_ms=\[(\d+) (\d+) \] "
                 r"phone_adb_after_ms=(\d+) mac_after_ms=(\d+)", line)
    if not m:
        raise SystemExit(f"unparsable stop line: {line!r}")
    stops.append({"serial": m.group(2), "phone": m.group(1),
                  "stop_min_phone_ms": int(m.group(5)), "stop_max_phone_ms": int(m.group(6)),
                  "phone_adb_before_ms": int(m.group(4)), "phone_adb_after_ms": int(m.group(7)),
                  "mac_ms_bracket": [int(m.group(3)), int(m.group(8))],
                  "method": "phone `date +%s%3N` inside the same run-as shell immediately before and after "
                            "`touch files/otp23-stop`; outer adb phone dates and Mac ms (ssh python3) recorded around it"})
(attempt / "stops.json").write_text(json.dumps(stops, indent=2))
out = {"what": "OTP-23 Mac M-08 retake per-attempt acceptance read-out (from the driver's own JSON)",
       "scenario": scen, "attempt": n, "scenario_json_sha256": hashlib.sha256((mac / f"{scen}.json").read_bytes()).hexdigest(),
       "status": doc["status"], "error_class": doc["error_class"], "actual": doc["actual"][:400],
       "checks": checks, "all_checks_pass": all(checks.values()),
       "samples": rows, "diag": {"lines": len(diag_lines), "classes": diag_classes,
                                 "available_lsof_results_in_samples": tcp4_available},
       "teardown": td, "link_proof": det.get("link_proof"), "chrome_version_actual": det.get("chrome_version_actual"),
       "self_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()}
(attempt / "acceptance-check.json").write_text(json.dumps(out, indent=2))
print(json.dumps({"scenario": scen, "status": doc["status"], "all_checks_pass": out["all_checks_pass"],
                  "failed": [k for k, v in checks.items() if not v], "diag": out["diag"],
                  "teardown": {k: td.get(k) for k in ("trace_mechanism", "trace_exit", "owned_gone_after_s",
                                                       "trace_released_count")},
                  "order_tail": td.get("host_output_order", [])[-3:], "chrome": det.get("chrome_version_actual")}))
