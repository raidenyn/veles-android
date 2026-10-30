"""macOS adapter giving poc_background.py the small `chrome_validate` surface it uses on Windows.

poc_background.py only touches ChromeRun, EXE_NAME, HOSTS, VELES_SERVICE, host_processes(exe),
ancestry(pid), expect, wait_gone and scrub. This module provides exactly those on top of macos_poc
(CoreBluetooth backend, `ps -axo` process rows, Python 3.9 compatible, stdlib only). Process dicts use
the Windows keys (ProcessId / ParentProcessId / Name / CommandLine) so the shared driver is unchanged.
The registered native-messaging manifest is validated (name, allowed_origins, packaged binary) and its
bytes are copied into the disposable Chrome profile only; nothing in the user's registration is touched.
"""

import pathlib

import macos_poc as mp
from macos_install import APP_DEFAULT, MANIFEST_DIR_DEFAULT, PACKAGED_BINARY, read_identity

PLATFORM = "macos"
EXE_NAME = {"plain": "otp23-host"}
HOSTS = {"plain": mp.HOST_NAME}
VELES_SERVICE = mp.VELES_SERVICE
expect = mp.require
wait_gone = mp.wait_gone
scrub = mp.scrub


def registered_manifest(app=None, manifest_dir=None):
    """Validate the registered manifest against identity.rs and the packaged binary; return its path."""
    identity = read_identity()
    expect(identity["host_name"] == mp.HOST_NAME, "identity.rs plain host name drifted")
    binary = pathlib.Path(app or APP_DEFAULT) / PACKAGED_BINARY
    expect(binary.is_file(), f"{binary} missing; run cargo build --release then ./scripts/macos-package.sh")
    try:
        return mp.check_registration(manifest_dir or MANIFEST_DIR_DEFAULT, binary)
    except SystemExit as error:  # check_registration exits with a message; keep the driver's step handling
        raise AssertionError(str(error.code))


def to_windows_shape(row):
    return {"ProcessId": row["pid"], "ParentProcessId": row["ppid"], "Name": row["comm"],
            "CommandLine": row["args"]}


def host_processes(exe, snapshot=None):
    """Plain-host processes as ProcessId/Name/CommandLine dicts; `snapshot` (ps text) is injectable."""
    expect(exe == EXE_NAME["plain"], f"unsupported host executable {exe!r}")
    rows = mp.parse_ps(mp.ps_snapshot() if snapshot is None else snapshot)
    return [to_windows_shape(row) for row in mp.host_processes(rows)]


def ancestry(pid, limit=6, snapshot=None):
    """Parent chain, closest parent first, with the Windows keys (ProcessId / Name / CommandLine)."""
    rows = mp.parse_ps(mp.ps_snapshot() if snapshot is None else snapshot)
    return [{"ProcessId": link["pid"], "Name": link["comm"], "CommandLine": link["args"]}
            for link in mp.ancestry(rows, pid, limit)]


class ChromeRun(mp.ChromeRun):
    def __init__(self, extra_env=None, medium=False, manifest_source=None, chrome_path=mp.DEFAULT_CHROME):
        self.medium = medium  # Windows integrity-level flag; no macOS equivalent, accepted for parity
        super().__init__(chrome_path=chrome_path, extra_env=extra_env,
                         manifest_source=manifest_source or registered_manifest())

    def load_extension(self):
        return self.cdp.call("Extensions.loadUnpacked", {"path": str(mp.EXT_DIR)})
