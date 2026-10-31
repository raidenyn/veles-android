"""OTP-23 package1 console-session preflight (run INSIDE the logged-in console session via /IT task).

Validates, on the machine where the measured runs will happen and in the same session context:
1. session identity: the observer session id equals the active console session id (EnumWindows context);
2. window enumeration sees real windows (total counts + visible class sample; no titles recorded);
3. message-only enumeration works (FindWindowEx walk from HWND_MESSAGE);
4. endpoint table crosscheck: ctypes GetExtendedTcp/UdpTable totals vs PowerShell Get-Net*Connection totals.

Everything is recorded with counts and totals only. Nothing here passes an OTP-23 row; it validates the
observation context so a later absence claim is auditable.

    python scripts/package1_console_preflight.py --out evidence/.../preflight.json
"""

import argparse
import ctypes
import collections
import json
import os
import pathlib
import subprocess
import sys
from ctypes import wintypes
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import chrome_validate as cv  # noqa: E402 - same machine, same session


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    document = {"started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "platform": sys.platform, "python": sys.version.split()[0]}
    observer_session = cv.session_of(os.getpid())
    active_console = cv.active_console_session()
    document["session_identity"] = {"observer_pid": os.getpid(), "observer_session": observer_session,
                                    "active_console_session": active_console,
                                    "observer_on_active_console": observer_session == active_console}

    total_windows = {"visible": 0, "hidden": 0}
    visible_classes = collections.Counter()
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        visible = bool(user32.IsWindowVisible(hwnd))
        total_windows["visible" if visible else "hidden"] += 1
        if visible and total_windows["visible"] <= 64:
            name = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, name, 128)
            visible_classes[name.value] += 1
        return True

    user32.EnumWindows(visit, 0)
    document["window_enumeration"] = {"total_visible": total_windows["visible"],
                                      "total_hidden": total_windows["hidden"],
                                      "visible_classes_top": dict(visible_classes.most_common(8))}
    document["visible_enumeration_nonempty"] = total_windows["visible"] > 0

    message_only_total = 0
    walker = wintypes.HWND(0)
    hwnd_message = wintypes.HWND(-3)
    user32.FindWindowExW.restype = wintypes.HWND
    for _ in range(4096):
        walker = user32.FindWindowExW(hwnd_message, walker, None, None)
        if not walker:
            break
        message_only_total += 1
    document["message_only_listing"] = {"total": message_only_total}

    tables = cv.endpoint_table()
    ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                         "@(Get-NetTCPConnection -ErrorAction SilentlyContinue).Count;"
                         "@(Get-NetUDPEndpoint -ErrorAction SilentlyContinue).Count"],
                        capture_output=True, text=True, timeout=60)
    lines = [x for x in ps.stdout.strip().splitlines() if x.strip().isdigit()]
    document["endpoint_crosscheck"] = {
        "ctypes": {"tcp4": len(tables["tcp4"]), "tcp6": len(tables["tcp6"]),
                   "udp4": len(tables["udp4"]), "udp6": len(tables["udp6"])},
        "powershell": {"tcp_total": int(lines[0]) if lines else None,
                       "udp_total": int(lines[1]) if len(lines) > 1 else None},
        "method": "totals recorded for audit; per-pid truth in measured runs comes from the ctypes table "
                  "with the run's own CDP listener as the positive control"}

    document["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    target = pathlib.Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(json.dumps(document, indent=2))
    return 0 if document["session_identity"]["observer_on_active_console"] and \
        document["visible_enumeration_nonempty"] else 1


if __name__ == "__main__":
    sys.exit(main())