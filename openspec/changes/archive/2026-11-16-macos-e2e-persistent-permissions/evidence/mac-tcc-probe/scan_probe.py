#!/usr/bin/env python3
"""Drives the real Native Messaging host over stdin/stdout: hello -> start_scan -> stop -> shutdown.

Prints the host's framed JSON responses, so the CoreBluetooth authorization path is exercised by
the real production adapter rather than a synthetic probe.
"""
import json
import struct
import subprocess
import sys

HOST = sys.argv[1]
ORIGIN = "chrome-extension://ppfjeblflhibkohilcnholalpgbcacng/"
SERVICE = "0000fff0-0000-1000-8000-00805f9b34fb"

proc = subprocess.Popen(
    [HOST, ORIGIN, "--parent-window=0"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)


def send(obj):
    data = json.dumps(obj, separators=(",", ":")).encode()
    proc.stdin.write(struct.pack("<I", len(data)) + data)
    proc.stdin.flush()


def recv():
    head = proc.stdout.read(4)
    if len(head) < 4:
        return None
    (size,) = struct.unpack("<I", head)
    return json.loads(proc.stdout.read(size))


def until(*types):
    while True:
        message = recv()
        if message is None:
            return None
        print(json.dumps(message, sort_keys=True))
        if message.get("type") in types:
            return message


send({"envelope_version": 1, "type": "hello", "request_id": "tcc-hello", "api_versions": [1]})
ready = until("ready", "error")
if ready is None or ready.get("type") != "ready":
    print("--- stderr:", proc.stderr.read().decode(errors="replace"))
    sys.exit(1)

send(
    {
        "envelope_version": 1,
        "api_version": 1,
        "type": "start_scan",
        "request_id": "tcc-scan",
        "service_uuid": SERVICE,
    }
)
scan = until("scan_state", "error")
if scan and scan.get("scan_handle"):
    send(
        {
            "envelope_version": 1,
            "api_version": 1,
            "type": "stop_scan",
            "request_id": "tcc-stop",
            "scan_handle": scan["scan_handle"],
        }
    )
    until("scan_state", "error")

send({"envelope_version": 1, "api_version": 1, "type": "shutdown", "request_id": "tcc-shutdown"})
until("shutdown_complete", "error")
proc.stdin.close()
print("exit", proc.wait(timeout=10))
print("--- stderr:", proc.stderr.read().decode(errors="replace"))
