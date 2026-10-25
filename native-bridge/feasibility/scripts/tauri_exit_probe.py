"""Reproduces the Tauri windowless-exit finding without any host logic.

Runs `otp23-host-tauri` with OTP23_TAURI_EXIT_PROBE=1: setup -> worker thread sleeps 200 ms ->
AppHandle::exit(0). Exit code 0 means Tauri reached RunEvent::Exit and ended the process itself;
11 means the event loop never got there and the probe had to force the exit after 1.5 s.

Usage: python scripts/tauri_exit_probe.py <path-to-otp23-host-tauri.exe> [runs]
"""

import os
import subprocess
import sys
import tempfile
import time

EXE = os.path.abspath(sys.argv[1])
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
ORIGIN = "chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/"

results = []
for run in range(RUNS):
    trace = os.path.join(tempfile.gettempdir(), f"tauri-probe-{os.getpid()}-{run}.log")
    open(trace, "w").close()
    env = dict(os.environ, OTP23_TAURI_EXIT_PROBE="1", VELES_OTP23_TRACE=trace)
    started = time.time()
    proc = subprocess.Popen([EXE, ORIGIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
    try:
        code = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = "HUNG>30s"
    lines = [line.split("] ", 1)[1] for line in open(trace).read().splitlines() if "] " in line]
    os.unlink(trace)
    results.append((code, round(time.time() - started, 2), lines))
    print(f"run {run}: exit={code} after {results[-1][1]} s")
    for line in lines:
        print("    ", line)

forced = sum(1 for code, *_ in results if code == 11)
print(f"{forced}/{RUNS} runs needed the forced exit (Tauri never reached RunEvent::Exit)")
