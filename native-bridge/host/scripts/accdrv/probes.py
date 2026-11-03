"""Read-only platform probes for the physical-run context.

Only the adapter identity is implemented here so far. It hashes the controller instance identifier and never
records the raw Windows PnP instance id or the macOS controller address. An absent, unreadable, malformed or
ambiguous inventory is Blocked; a synthetic id is never produced.
"""
import hashlib
import json
import re
import subprocess

from .errors import Blocked

PROBE_TIMEOUT_S = 30
NAME_MAX = 80
WINDOWS_PNP_COMMAND = [
    "powershell", "-NoProfile", "-NonInteractive", "-Command",
    "Get-PnpDevice -Class Bluetooth -ErrorAction Stop | "
    "Select-Object Status,FriendlyName,InstanceId | ConvertTo-Json -Compress -Depth 3",
]
MACOS_BLUETOOTH_COMMAND = ["system_profiler", "SPBluetoothDataType", "-json"]
_MAC_ADDRESS = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
_NON_DEVICE_PREFIXES = ("BTHLEDEVICE\\", "BTHENUM\\", "BTHLE\\", "BTH\\")


def bounded_run(argv, timeout=PROBE_TIMEOUT_S):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise Blocked(f"{argv[0]} adapter probe could not be run: {type(e).__name__}")
    return p.returncode, p.stdout, p.stderr


def _hash(domain, identity):
    return hashlib.sha256((domain + "\0" + identity).encode("utf-8")).hexdigest()


def _name(value):
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value)).strip()
    return text[:NAME_MAX]


def _json(text):
    text = (text or "").strip()
    if not text:
        raise Blocked("Bluetooth adapter probe returned no data")
    try:
        return json.loads(text)
    except ValueError:
        raise Blocked("Bluetooth adapter probe output is not valid JSON")


def parse_windows_pnp(text):
    doc = _json(text)
    rows = doc if isinstance(doc, list) else [doc]
    if not rows:
        raise Blocked("no enabled Bluetooth adapter in the PnP inventory")
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            raise Blocked("Bluetooth PnP inventory row is not an object")
        instance = row.get("InstanceId")
        if not isinstance(instance, str) or not instance.strip():
            raise Blocked("Bluetooth PnP inventory row lacks InstanceId")
        if row.get("Status") != "OK":
            continue
        instance = instance.strip()
        if instance.upper().startswith(_NON_DEVICE_PREFIXES):
            continue
        candidates.append((row, instance))
    if not candidates:
        raise Blocked("no enabled Bluetooth adapter in the PnP inventory")
    if len(candidates) != 1:
        raise Blocked(f"ambiguous Bluetooth adapter identity ({len(candidates)} candidates)")
    row, instance = candidates[0]
    return {"kind": "bluetooth", "name": _name(row.get("FriendlyName") or "bluetooth-controller"),
            "id_sha256": _hash("windows-pnp-instance", instance)}


def parse_macos_bluetooth(text):
    doc = _json(text)
    entries = doc.get("SPBluetoothDataType") if isinstance(doc, dict) else None
    if entries is None:
        raise Blocked("system_profiler output lacks SPBluetoothDataType")
    entries = entries if isinstance(entries, list) else [entries]
    controllers = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise Blocked("system_profiler Bluetooth entry is not an object")
        props = entry.get("controller_properties")
        if not isinstance(props, dict):
            raise Blocked("system_profiler controller_properties is not an object")
        address = props.get("controller_address")
        if not isinstance(address, str) or not address.strip():
            raise Blocked("system_profiler controller lacks controller_address")
        address = address.strip().lower()
        if not _MAC_ADDRESS.match(address):
            raise Blocked("system_profiler controller_address is not a Bluetooth address")
        controllers.append((props, address))
    if not controllers:
        raise Blocked("no Bluetooth controller in system_profiler output")
    if len(controllers) != 1:
        raise Blocked(f"ambiguous Bluetooth controller identity ({len(controllers)} controllers)")
    props, address = controllers[0]
    chipset = _name(props.get("controller_chipset") or "bluetooth-controller")
    transport = _name(props.get("controller_transport") or "")
    name = f"{chipset} ({transport})" if transport else chipset
    return {"kind": "bluetooth", "name": _name(name),
            "id_sha256": _hash("macos-bluetooth-controller", address)}


def adapter_context(family, run=None):
    run = run or bounded_run
    if family == "windows":
        rc, out, err = run(WINDOWS_PNP_COMMAND)
        if rc != 0:
            raise Blocked(f"Bluetooth PnP probe failed (rc {rc})")
        return parse_windows_pnp(out)
    if family == "macos":
        rc, out, err = run(MACOS_BLUETOOTH_COMMAND)
        if rc != 0:
            raise Blocked(f"system_profiler Bluetooth probe failed (rc {rc})")
        return parse_macos_bluetooth(out)
    raise Blocked(f"no adapter identity probe for platform {family!r}")
