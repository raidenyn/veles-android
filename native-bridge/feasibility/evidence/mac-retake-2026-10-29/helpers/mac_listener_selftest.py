"""OTP-23 Mac M-08 retake: listener self-test for the reviewed lsof classifier (read-only, no Chrome).

Opens ONE own loopback listener (127.0.0.1:0) in this process, then calls the STAGED
macos_poc.lsof_endpoints(os.getpid()) exactly as the driver does. Records alongside it the raw lsof
rc/stderr for the same pid (so whether the foreign-nullfs warning is present is OBSERVED, not assumed),
the env state, and the module's own sha256. Run twice by the operator: once with
VELES_OTP23_LSOF_DIAG=<dir>/lsof-diagnostics.jsonl, once with it unset. No -w, no mount action.

Usage: python3 mac_listener_selftest.py <scripts-dir> <out.json>
"""
import hashlib
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

scripts = pathlib.Path(sys.argv[1]).resolve()
out = pathlib.Path(sys.argv[2])
if out.exists():
    sys.exit(f"{out} exists; unique output only")
sys.path.insert(0, str(scripts))
import macos_poc  # noqa: E402  (the staged, reviewed bytes)

listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.bind(("127.0.0.1", 0))
listener.listen(1)
port = listener.getsockname()[1]
raw = subprocess.run(["lsof", "-nP", "-a", "-p", str(os.getpid()), "-i"], capture_output=True, text=True,
                     timeout=30)
result = macos_poc.lsof_endpoints(os.getpid())
listener.close()
doc = {
    "what": "OTP-23 Mac M-08 retake listener self-test (reviewed lsof classifier, own loopback listener)",
    "wall_ms": int(time.time() * 1000),
    "pid": os.getpid(),
    "listener_port": port,
    "env_VELES_OTP23_LSOF_DIAG": os.environ.get("VELES_OTP23_LSOF_DIAG"),
    "macos_poc_sha256": hashlib.sha256((scripts / "macos_poc.py").read_bytes()).hexdigest(),
    "self_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
    "raw_lsof": {"argv": ["lsof", "-nP", "-a", "-p", str(os.getpid()), "-i"], "rc": raw.returncode,
                 "stdout_lines": len(raw.stdout.splitlines()), "stderr_verbatim": raw.stderr,
                 "foreign_nullfs_warning_present": "can't stat() nullfs file system" in raw.stderr},
    "lsof_endpoints_result": result,
    "listener_port_returned": (result is not None and any(r.get("local_port") == port for r in result)),
}
out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
print(json.dumps({k: doc[k] for k in ("listener_port", "env_VELES_OTP23_LSOF_DIAG", "lsof_endpoints_result",
                                      "listener_port_returned")} |
                 {"warning_present": doc["raw_lsof"]["foreign_nullfs_warning_present"],
                  "raw_rc": raw.returncode}))
