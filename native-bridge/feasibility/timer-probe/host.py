#!/usr/bin/python3
"""Disposable non-Bluetooth Native Messaging host for the timer probe (Python 3.9+, stdlib only).

Emits {"seq": n, "t": epoch_ms} every INTERVAL_S seconds with Native Messaging framing (u32 LE length +
UTF-8 JSON) and exits when stdin reaches EOF. Appends its own send log to $TIMER_PROBE_HOST_LOG, default
host-send.log beside this script.
"""
import json
import os
import struct
import sys
import threading
import time

INTERVAL_S = 10.0


def main():
    # Chrome launched via `open` does not inherit our environment, so the default sits next to this script.
    log_path = os.environ.get("TIMER_PROBE_HOST_LOG") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "host-send.log")
    log = open(log_path, "a", buffering=1) if log_path else None
    stop = threading.Event()

    def watch_stdin():
        while sys.stdin.buffer.read(4096):
            pass
        stop.set()  # EOF: Chrome closed the port

    threading.Thread(target=watch_stdin, daemon=True).start()
    seq = 0
    while not stop.wait(INTERVAL_S if seq else 1.0):
        seq += 1
        body = json.dumps({"seq": seq, "t": int(time.time() * 1000)}).encode()
        sys.stdout.buffer.write(struct.pack("<I", len(body)) + body)
        sys.stdout.buffer.flush()
        if log:
            log.write(body.decode() + "\n")
    if log:
        log.write(json.dumps({"event": "stdin_eof", "t": int(time.time() * 1000)}) + "\n")


if __name__ == "__main__":
    main()
