"""Independent OS observations (process, endpoints, windows) with injectable command runners.

Everything returns measured data or raises Blocked. A measurement that cannot be taken is NEVER reported as
`none_observed`: a failed probe, an unreadable result, or a host that exited during the probe is Blocked. Windows,
file-write, registry-write and LaunchServices monitors have no stdlib implementation here, so they are Blocked
unless the controller injects a reviewed one.
"""
import json
import re

from .errors import Blocked

PS_LINE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\w{3}\s+\w{3}\s+\d+\s+\d\d:\d\d:\d\d\s+\d{4})\s+(.*\S)\s*$")


def parse_ps(text):
    """`ps -axo pid=,ppid=,lstart=,command=` -> [{pid, ppid, start, command}] (malformed lines skipped, never guessed)."""
    rows = []
    for line in text.splitlines():
        m = PS_LINE.match(line)
        if m:
            rows.append({"pid": int(m.group(1)), "ppid": int(m.group(2)), "start": " ".join(m.group(3).split()), "command": m.group(4)})
    return rows


def parse_cim(text):
    """PowerShell `ConvertTo-Json` of Win32_Process rows (one object or a list) -> list of dicts."""
    text = text.strip()
    if not text:
        return []
    value = json.loads(text)
    rows = value if isinstance(value, list) else [value]
    return [{"pid": int(r["ProcessId"]), "ppid": int(r["ParentProcessId"]), "command": r.get("CommandLine") or "",
             "path": r.get("ExecutablePath") or "", "start": str(r.get("CreationDate") or "")}
            for r in rows if isinstance(r, dict) and "ProcessId" in r]


def parse_lsof(rc, out, err):
    """`lsof -nP -a -p PID -i`: rc 1 with no output and no error means 'no endpoint'; anything odd is a failure."""
    lines = [l for l in out.splitlines() if l.strip()]
    if rc == 0 and len(lines) >= 2:
        return lines[1:]
    if rc == 1 and not lines and not err.strip():
        return []
    raise Blocked(f"lsof result not interpretable (rc {rc}, stderr {err.strip()[:80]!r})")


WIN_ENDPOINT_SCRIPT = (
    "$e=@(); "
    "$t=@(Get-NetTCPConnection -OwningProcess {pid} -ErrorAction SilentlyContinue -ErrorVariable +e); "
    "$u=@(Get-NetUDPEndpoint -OwningProcess {pid} -ErrorAction SilentlyContinue -ErrorVariable +e); "
    "$bad=@($e | Where-Object {{ $_.CategoryInfo.Category -ne 'ObjectNotFound' }}); "
    "[pscustomobject]@{{ endpoints = @(($t + $u) | ForEach-Object {{ $_.LocalPort }}); errors = $bad.Count }} "
    "| ConvertTo-Json -Compress -Depth 3"
)


def parse_win_endpoints(rc, out):
    """Fail closed: a non-zero exit, unparsable output or ANY non-'not found' error is Blocked, never an empty list."""
    if rc != 0:
        raise Blocked(f"endpoint query failed rc {rc}")
    try:
        doc = json.loads(out)
        errors, ports = doc["errors"], doc["endpoints"]
    except (ValueError, KeyError, TypeError):
        raise Blocked("endpoint query output not interpretable")
    if errors != 0:
        raise Blocked(f"endpoint query reported {errors} error(s); cannot conclude 'no endpoint'")
    if ports is None:
        ports = []
    return ports if isinstance(ports, list) else [ports]


class Observer:
    """Interface. `run(argv) -> (rc, stdout, stderr)` is injected; `family` is 'windows' or 'macos'."""

    def __init__(self, family, run, windows_census=None):
        self.family, self.run, self.windows_census = family, run, windows_census

    def _ps(self):
        if self.family == "macos":
            rc, out, err = self.run(["ps", "-axo", "pid=,ppid=,lstart=,command="])
            if rc != 0:
                raise Blocked(f"ps failed rc {rc}")
            return parse_ps(out)
        rc, out, err = self.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                 "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine,ExecutablePath,CreationDate"
                                 " | ConvertTo-Json -Compress -Depth 3"])
        if rc != 0:
            raise Blocked(f"process listing failed rc {rc}")
        try:
            return parse_cim(out)
        except (ValueError, KeyError, TypeError):
            raise Blocked("process listing not interpretable")

    def host_processes(self, exe):
        """Processes whose executable is EXACTLY `exe` (path equality; spaces in the path are fine on macOS because
        the command must equal the path or start with it followed by a space - never a name or prefix match)."""
        exe = str(exe)
        if self.family == "windows":
            norm = lambda s: s.replace("/", "\\").casefold()
            return [r for r in self._ps() if r.get("path") and norm(r["path"]) == norm(exe)]
        # Chrome starts the host with exactly its origin as the argument, so after the exact path only nothing or
        # " chrome-extension://..." may follow: "/Users/u/My" can therefore never match "/Users/u/My Out/.../host".
        return [r for r in self._ps() if r["command"] == exe or r["command"].startswith(exe + " chrome-extension://")]

    def alive(self, who):
        """`who` is a host process row (pid + start time + path/command must all still match: a recycled PID is a
        different process) or, for legacy callers, a bare pid."""
        rows = self._ps()
        if isinstance(who, dict):
            return any(r["pid"] == who["pid"] and r.get("start") == who.get("start") and
                       (r.get("path") or r["command"]) == (who.get("path") or who["command"]) for r in rows)
        return any(r["pid"] == who for r in rows)

    def endpoints(self, who):
        pid = who["pid"] if isinstance(who, dict) else who
        if self.family == "macos":
            found = parse_lsof(*self.run(["lsof", "-nP", "-a", "-p", str(int(pid)), "-i"]))
        else:
            rc, out, err = self.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", WIN_ENDPOINT_SCRIPT.format(pid=int(pid))])
            found = parse_win_endpoints(rc, out)
        if not found and not self.alive(who):
            raise Blocked("host exited during the endpoint probe; 'no endpoint' cannot be concluded")
        return found

    def windows(self, pid):
        if self.windows_census is None:
            raise Blocked("no reviewed top-level/message-only window census is wired in for this platform")
        return self.windows_census(pid)

    def no_write_monitor(self, what):
        raise Blocked(f"no independent monitor for {what} (needs ETW/fs_usage/registry auditing); not inferred from a snapshot")
