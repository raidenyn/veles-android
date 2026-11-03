"""Synthetic two-phone rig adapter for the OTP-24 physical acceptance driver.

Everything with an external effect is INJECTED; this module owns no device, radio, registry or process access:

* ``adb``       - a ``SafeAdb`` around an injected runner. It accepts only a closed allowlist of READ-ONLY commands
                  (getprop, date, pm path, dumpsys package, ``run-as <pkg> cat|ls`` of the fixture's own files, a
                  pull of the installed ``base.apk``). Install/uninstall/clear/rm/svc/am/push/reboot and anything
                  else are Refused before the runner is called.
* ``cert_reader`` - signing-certificate SHA-256 of APK bytes (``ApksignerCertReader`` wraps ``apksigner`` through an
                  injected runner).
* ``capacity_probe``, ``link_failer``, ``conditions``, ``gatt_write_probe``, ``native_code_source``,
  ``unrelated_advertiser`` - measurements/actions the controller owns. Absent => Blocked with the exact reason.
* the Chrome side is ``ctx.js`` (the disposable acceptance extension), never a second browser.

Binding rules (each has a test):
* phone slot <-> adb serial is configured explicitly; the published ``source_hash`` is a salted hash (serial never
  recorded); two slots may not share a serial or a source hash;
* the installed APK is pulled byte-for-byte, hashed, and its signing certificate must equal the expected
  fingerprint (computed by the controller from the NEW apk with the same reader) - no replacement is ever done here;
* a connector connection is bound to a slot ONLY by bytes: a unique challenge goes down the connection and exactly
  one configured phone's fixture telemetry must show that digest (the other must not). Scan results and labels are
  never trusted for binding;
* a transfer is proven only when the destination phone logged exactly one NEW record of the right size and digest
  and no other phone logged a new record;
* anything not measurable (negotiated ATT capacity, zero GATT writes, native error codes, OS-level release without a
  fixture state, pipe tap) raises Blocked - never a default, never a fabricated "ok".
"""
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from .errors import Blocked, Refused
from .lifecycle import UnattachedRig
from .recorder import ProductFailure

SOURCE_CONTEXT = "veles-otp24-phone-source:"  # HMAC message prefix; the KEY is private to the controller/run
DEFAULT_PACKAGE = "me.nagaev.veles.otp23"  # docs/windows-ble-poc.md isolated-device fixture application id
GETPROPS = {"ro.product.model", "ro.build.version.release", "ro.build.version.sdk"}
SERIAL_RE = re.compile(r"^[A-Za-z0-9._-]{4,64}$")  # no ":" -> no host:port (adb over TCP)
TCP_SERIAL_RE = re.compile(r"(\d{1,3}\.){3}\d{1,3}|_tcp|_adb-tls|^adb-")  # IPv4 literals and mDNS/wireless-debugging names
LAYOUT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")  # same shape the fixture enforces on otp23_run_id
TOKEN_RE = re.compile(r"^[A-Za-z0-9._/:=+%~-]+$")
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
APK_PATH_RE = re.compile(r"^/data/app/[A-Za-z0-9._=/~-]+/base\.apk$")
# Which terminal error proves HOST cancellation is data, not code: cases.json `pending_request_handling.terminal_error` for the case
# (explicit shutdown: internal/shutdown, before shutdown_complete; per-phone failure: send_failed/link_lost). Abrupt-EOF triggers have no
# such rule - option 2: their per-request outcome is recorded as unknown (`record_pending_disposition`). The connector-synthesized
# internal/port_closed is local only: the connector never logs it in `completionLog`, and it is never accepted as host proof.
GLOBAL_TRIGGERS = {"literal_port_disconnect", "close_connector_tab", "chrome_owned_pipe_closure", "shutdown_request"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9._:~-]{1,128}$")
RELEASE_BOUND_S = 30.0  # OTP-23 pre-declared link-release bound (package1_phone_bound.py); a parameter, not a new claim
CLOCK_SAMPLES = 7
BIND_CHALLENGE_BYTES = 64
PROOF_BYTES = 128


class CmdResult(NamedTuple):
    rc: int
    out: bytes
    err: bytes


class FixtureLayout:
    """File names of the OTP-23 synthetic fixture (SharingInteropPhysicalTest.holdObservedPeripheral)."""

    def __init__(self, server_file="otp23-a01-server.json", ready_file="otp23-a01-ready"):
        for name in (server_file, ready_file):
            if not isinstance(name, str) or not LAYOUT_NAME_RE.match(name) or ".." in name:
                raise Refused("fixture layout file names must be plain single path components")
        if server_file == ready_file:
            raise Refused("fixture layout file names must be distinct")
        self.server_file, self.ready_file = server_file, ready_file

    @property
    def readable(self):
        return {self.server_file, self.ready_file}


class RigConfig:
    def __init__(self, serials, expected_cert_sha256, package=DEFAULT_PACKAGE, layout=None, source_key=None,
                 expected_apk_sha256=None, run_id=None, io_timeout_s=15.0, release_bound_s=RELEASE_BOUND_S):
        if not isinstance(serials, dict) or not serials or set(serials) - {"A", "B"}:
            raise Refused("serials must map slot A (and optionally B) to adb serials")
        for slot, serial in serials.items():
            if not isinstance(serial, str) or not SERIAL_RE.match(serial) or TCP_SERIAL_RE.search(serial):
                raise Refused(f"slot {slot}: malformed adb serial or a network (TCP/wireless) transport; only USB serials are accepted")
        if len(set(serials.values())) != len(serials):
            raise Refused("two slots share one adb serial: distinct phones are required")
        if not PACKAGE_RE.match(package):
            raise Refused("malformed package name")
        if not HEX64.match(expected_cert_sha256 or ""):
            raise Refused("expected_cert_sha256 must be the SHA-256 fingerprint read from the new APK's real apksigner output")
        if expected_apk_sha256 is not None and not HEX64.match(expected_apk_sha256):
            raise Refused("expected_apk_sha256 malformed")
        if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
            raise Refused("run_id is required: it must equal the id the controller gave the running fixture (stale telemetry is refused)")
        if source_key is not None and (not isinstance(source_key, (bytes, bytearray)) or len(source_key) < 16):
            raise Refused("source_key must be at least 16 secret bytes from the controller (never stored in evidence)")
        self.serials, self.package, self.layout = dict(serials), package, layout or FixtureLayout()
        self.expected_cert_sha256, self.expected_apk_sha256 = expected_cert_sha256, expected_apk_sha256
        self.run_id = run_id
        # Private identifier key: controller-supplied (stable across runs) or ephemeral (distinct within this run only).
        self._source_key = bytes(source_key) if source_key is not None else os.urandom(32)
        self.io_timeout_s, self.release_bound_s = io_timeout_s, release_bound_s

    def __repr__(self):
        return f"RigConfig(slots={sorted(self.serials)}, package={self.package!r}, run_id={self.run_id!r})"

    def source_hash(self, slot):
        """HMAC-SHA256(private key, context + serial). Not reversible from a short serial without the key; never published with it."""
        return hmac.new(self._source_key, (SOURCE_CONTEXT + self.serials[slot]).encode(), hashlib.sha256).hexdigest()


# ---- the only adb surface ----------------------------------------------------------------------------------------------
class SafeAdb:
    """Allowlisted READ-ONLY adb. `runner(argv) -> CmdResult` is injected; the allowlist is enforced before it is called."""

    def __init__(self, runner, adb_path, config):
        if not str(adb_path).startswith(("/", "\\")) and not re.match(r"^[A-Za-z]:[\\/]", str(adb_path)):
            raise Refused("adb must be an absolute path")
        self.runner, self.adb_path, self.config = runner, str(adb_path), config
        self._apk = {}  # slot -> the exact base.apk path `pm path` reported for the fixture package on that phone

    def apk_path(self, slot):
        """`pm path` for the fixture package; the ONLY path a later `exec-out cat` for this slot may name."""
        path = parse_pm_path(self(slot, "shell", "pm", "path", self.config.package), self.config.package)
        self._apk[slot] = path
        return path

    def check(self, args, slot=None):
        """Raise Refused unless `args` (after `-s SERIAL`) is exactly one of the read-only forms."""
        args = tuple(args)
        if not args or any(not isinstance(a, str) or not TOKEN_RE.match(a) for a in args):
            raise Refused("adb arguments must be plain tokens")
        pkg, lay = self.config.package, self.config.layout
        if args[:2] == ("shell", "getprop") and len(args) == 3 and args[2] in GETPROPS:
            return
        if args == ("shell", "date", "+%s%3N"):
            return
        if args == ("shell", "pm", "path", pkg) or args == ("shell", "dumpsys", "package", pkg):
            return
        if len(args) == 5 and args[:4] == ("shell", "run-as", pkg, "cat") and args[4] in {"files/" + n for n in lay.readable}:
            return
        if args == ("shell", "run-as", pkg, "ls", "files"):
            return
        if len(args) == 3 and args[:2] == ("exec-out", "cat") and slot is not None and self._apk.get(slot) == args[2] \
                and APK_PATH_RE.match(args[2]) and ".." not in args[2]:
            return
        raise Refused(f"adb command not on the read-only allowlist: {' '.join(args)[:80]}")

    def __call__(self, slot, *args):
        self.check(args, slot)
        serial = self.config.serials[slot]
        result = self.runner([self.adb_path, "-s", serial, *args])
        if not isinstance(result, CmdResult):
            result = CmdResult(*result)
        return result


def subprocess_runner(timeout_s=60):
    """Real runner for the controller: no shell, bounded time. Launch failures/timeouts are Blocked."""
    def run(argv):
        try:
            p = subprocess.run(argv, capture_output=True, timeout=timeout_s, shell=False)
        except (OSError, subprocess.SubprocessError) as e:
            raise Blocked(f"{Path(argv[0]).name} could not be run: {type(e).__name__}")
        return CmdResult(p.returncode, p.stdout, p.stderr)
    return run


class ApksignerCertReader:
    """SHA-256 of the (single) signing certificate of APK bytes, from real `apksigner verify --print-certs` output."""

    def __init__(self, runner, apksigner, scratch_dir):
        self.runner, self.apksigner, self.scratch = runner, str(apksigner), Path(scratch_dir)

    def __call__(self, apk_bytes):
        name = self.scratch / ("apk-" + hashlib.sha256(apk_bytes).hexdigest()[:16] + ".apk")
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as e:
            raise Blocked(f"apksigner scratch file could not be created exclusively ({type(e).__name__}); remove a leftover {name.name}")
        try:
            os.write(fd, apk_bytes)
        finally:
            os.close(fd)
        try:
            res = self.runner([self.apksigner, "verify", "--print-certs", str(name)])
        finally:
            try:
                name.unlink()
            except OSError:
                pass
        return parse_apksigner(res)


def parse_apksigner(res):
    if res.rc != 0:
        raise Blocked(f"apksigner verify failed (rc {res.rc})")
    digests = re.findall(r"^Signer #\d+ certificate SHA-256 digest: ([0-9a-f]{64})\s*$", res.out.decode("utf-8", "replace"), re.M)
    if len(digests) != 1:
        raise Blocked(f"expected exactly one signer certificate digest, found {len(digests)}")
    return digests[0]


# ---- pure parsers ------------------------------------------------------------------------------------------------------
def parse_pm_path(res, package=None):
    if res.rc != 0:
        raise Blocked(f"pm path failed (rc {res.rc})")
    lines = [l.strip() for l in res.out.decode("utf-8", "replace").splitlines() if l.strip()]
    paths = [l[len("package:"):] for l in lines if l.startswith("package:")]
    if len(paths) != len(lines) or len(paths) != 1 or not APK_PATH_RE.match(paths[0]):
        raise Blocked("pm path must name exactly one base.apk (split or multiple APKs are not bound)")
    if package is not None and not re.match(rf"^/data/app/(?:[^/]+/)?{re.escape(package)}-[^/]+/base\.apk$", paths[0]):
        raise Blocked("pm path does not name a package directory of the fixture package")
    return paths[0]


def parse_version(res):
    if res.rc != 0:
        raise Blocked(f"dumpsys package failed (rc {res.rc})")
    text = res.out.decode("utf-8", "replace")
    names = set(re.findall(r"^\s*versionName=(\S+)\s*$", text, re.M))
    codes = set(re.findall(r"^\s*versionCode=(\d+)\b", text, re.M))
    if len(names) != 1 or len(codes) != 1:
        raise Blocked("fixture versionName/versionCode not unambiguous in dumpsys package output")
    return f"{names.pop()}+{codes.pop()}"


def parse_epoch_ms(res):
    text = res.out.decode("ascii", "replace").strip()
    if res.rc != 0 or not re.fullmatch(r"\d{13}", text):
        raise Blocked("phone `date +%s%3N` did not return a 13-digit epoch in milliseconds")
    return int(text)


def parse_fixture(res, run_id, allow_captured=False):
    """Strict parse of the fixture's server json. RUNNING only (CAPTURED only for a slot the rig itself failed); a missing or
    different run id, or malformed content => Blocked."""
    if res.rc != 0:
        raise Blocked(f"fixture telemetry unreadable (rc {res.rc})")
    try:
        doc = json.loads(res.out.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Blocked("fixture telemetry is not valid JSON")
    ok_status = ("RUNNING", "CAPTURED") if allow_captured else ("RUNNING",)
    if not isinstance(doc, dict) or doc.get("status") not in ok_status or not isinstance(doc.get("records"), list) \
            or not isinstance(doc.get("states"), list):
        raise Blocked(f"fixture telemetry is not a {'/'.join(ok_status)} server document with records and states")
    if not run_id or doc.get("run_id") != run_id:
        raise Blocked("fixture telemetry belongs to a different (stale) run id")
    records = []
    for r in doc["records"]:
        if not (isinstance(r, dict) and isinstance(r.get("bytes"), int) and isinstance(r.get("epoch_ms"), int)
                and isinstance(r.get("sha256"), str) and HEX64.match(r["sha256"])):
            raise Blocked("fixture record entry malformed")
        records.append({"bytes": r["bytes"], "sha256": r["sha256"], "epoch_ms": r["epoch_ms"]})
    states = []
    for s in doc["states"]:
        if not (isinstance(s, dict) and isinstance(s.get("epoch_ms"), int) and isinstance(s.get("peers"), list)):
            raise Blocked("fixture state entry malformed")
        states.append({"epoch_ms": s["epoch_ms"], "peers": list(s["peers"])})
    return {"run_id": doc.get("run_id"), "status": doc["status"], "records": records, "states": states}


def budget_from(cap):
    """Payload budget per fragment.rs LinkBudget (min(M-3,512)-4; a write limit only counts when >=512)."""
    kind, value = (cap or {}).get("kind"), (cap or {}).get("value")
    if kind == "att_mtu" and isinstance(value, int) and 23 <= value <= 517:
        return min(value - 3, 512) - 4
    if kind == "max_write_without_response" and isinstance(value, int) and value >= 512:
        return 512 - 4
    raise Blocked("capacity probe returned no usable att_mtu/max_write_without_response value")


def fixture_record(size, seed):
    """Deterministic synthetic bytes. >=4: `VEL\\x01` + (index+seed)&0xff (valid for the fixture's echo)."""
    if size < 4:
        return bytes((seed + i) & 0xFF for i in range(size))
    body = bytearray(((i + seed) & 0xFF) for i in range(size))
    body[:4] = b"VEL\x01"
    if size > 4:
        body[4] = (seed + 4) & 0xFF  # the fixture derives its seed from byte 4
    return bytes(body)


def seed_for(nonce, *parts, slot=None):
    """Pattern seed. The slot only shifts it by one, so A and B can never share a seed for the same (nonce, parts)."""
    base = hashlib.sha256("|".join([nonce, *map(str, parts)]).encode()).digest()[0]
    return (base + (1 if slot == "B" else 0)) & 0xFF


def b64len(n):
    return 4 * ((n + 2) // 3)


def iso(ms):
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso_ms(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp() * 1000


def js_handle(value):
    if not isinstance(value, str) or not HANDLE_RE.match(value):
        raise Blocked("host handle is not a plain token")
    return json.dumps(value)


def js_bytes(data):
    return f'Uint8Array.from(atob("{base64.b64encode(data).decode()}"), c => c.charCodeAt(0))'


# ---- the rig -----------------------------------------------------------------------------------------------------------
class PhoneRig(UnattachedRig):
    """Rig actions for cases.json. Method names are the action names the case runner dispatches."""

    def __init__(self, adb, config, cert_reader, *, capacity_probe=None, link_failer=None, conditions=None, gatt_write_probe=None,
                 native_code_source=None, unrelated_advertiser=None, wall_ms=lambda: time.time() * 1000):
        self.adb, self.cfg, self.cert_reader = adb, config, cert_reader
        self.capacity_probe, self.link_failer, self.conditions = capacity_probe, link_failer, conditions
        self.gatt_write_probe, self.native_code_source, self.unrelated_advertiser = gatt_write_probe, native_code_source, unrelated_advertiser
        self.wall_ms = wall_ms

    # -- shared state ------------------------------------------------------------------------------------------------
    @staticmethod
    def _cap(ctx, name, kind, data):
        """Capture under `name`, or `name-N` when a retry/second observation reuses it (captures are exclusive-create)."""
        taken, candidate, n = {c["name"] for c in ctx.rec.captures}, name, 1
        while candidate in taken:
            n += 1
            candidate = f"{name}-{n}"
        return ctx.rec.capture(candidate, kind, data)

    def _rs(self, ctx, st):
        if "rig" not in st:
            st["rig"] = {"bound": False, "links": {}, "events": [], "phones": {}, "nonce": hashlib.sha256(
                (ctx.rec.clock() + str(id(st))).encode()).hexdigest()[:16], "transfers": [], "receipts": [], "rejections": [],
                "proofs": [], "sent": [], "stopped": set(), "own_scan": False, "claimed_devices": set(), "seq": 0}
        return st["rig"]

    def _needed(self, ctx):
        return [] if ctx.case["phones"] == "none" else list(ctx.case["phones"])

    def _drain(self, ctx, st):
        rs = self._rs(ctx, st)
        events = ctx.js(st, "velesAcceptance.drainEvents()") or []
        for e in events:
            if isinstance(e, dict):
                rs["events"].append(e.get("message", e))
        return rs["events"]

    def _read(self, ctx, st, slot):
        """Fixture telemetry. A slot whose link the rig itself failed may legitimately read CAPTURED (stop-file trigger)."""
        res = self.adb(slot, "shell", "run-as", self.cfg.package, "cat", "files/" + self.cfg.layout.server_file)
        return parse_fixture(res, self.cfg.run_id, allow_captured=slot in self._rs(ctx, st)["stopped"])

    def _read_all(self, ctx, st):
        return {slot: self._read(ctx, st, slot) for slot in self.cfg.serials}

    # -- phone binding (no install, no clear) ----------------------------------------------------------------------------
    def bind_phones(self, ctx, st):
        rs = self._rs(ctx, st)
        if rs["bound"]:
            return
        records = []
        for slot in self.cfg.serials:
            ready = self.adb(slot, "shell", "run-as", self.cfg.package, "ls", "files")
            names = ready.out.decode("utf-8", "replace").split()
            if ready.rc != 0 or self.cfg.layout.ready_file not in names:
                if slot in self._needed(ctx):
                    raise Blocked(f"slot {slot}: fixture ready file absent (the controller starts the fixture; this driver does not)")
                continue
            ready_doc = self.adb(slot, "shell", "run-as", self.cfg.package, "cat", "files/" + self.cfg.layout.ready_file)
            if ready_doc.rc != 0 or ready_doc.out.decode("utf-8", "replace").strip() != self.cfg.run_id:
                raise Blocked(f"slot {slot}: fixture ready file does not carry run id {self.cfg.run_id!r} (stale or foreign fixture)")
            path = self.adb.apk_path(slot)
            pulled = self.adb(slot, "exec-out", "cat", path)
            if pulled.rc != 0 or not pulled.out:
                raise Blocked(f"slot {slot}: could not read the installed APK")
            apk_sha = hashlib.sha256(pulled.out).hexdigest()
            cert = self.cert_reader(pulled.out)
            if cert != self.cfg.expected_cert_sha256:
                raise Refused(f"slot {slot}: installed APK signing certificate differs from the expected fingerprint; "
                              "no replacement or data clear is ever done by this driver")
            if self.cfg.expected_apk_sha256 is not None and apk_sha != self.cfg.expected_apk_sha256:
                raise Refused(f"slot {slot}: installed APK bytes differ from the expected build")
            version = parse_version(self.adb(slot, "shell", "dumpsys", "package", self.cfg.package))
            offset, unc, samples = self.measure_clock(slot)
            view = self._read(ctx, st, slot)
            rs["phones"][slot] = {"slot": slot, "source_hash": self.cfg.source_hash(slot), "apk_sha256": apk_sha, "apk_cert_sha256": cert,
                                  "fixture_version": version, "clock_offset_ms": offset, "clock_uncertainty_ms": unc,
                                  "clock_source": f"adb shell date +%s%3N, {len(samples)} samples, minimum-RTT midpoint (offset = phone - host)",
                                  "baseline_records": len(view["records"])}
            records.append({"slot": slot, "clock_samples": samples})  # no pm-path hash: that path is a stable device identifier
        missing = [s for s in self._needed(ctx) if s not in rs["phones"]]
        if missing:
            raise Blocked(f"phones not bound: {missing}")
        if len({p["source_hash"] for p in rs["phones"].values()}) != len(rs["phones"]):
            raise Refused("two bound phones share one source hash")
        ref = self._cap(ctx, "phone-binding", "phone_telemetry", {"phones": [{k: v for k, v in p.items() if k != "baseline_records"}
                                                                          for p in rs["phones"].values()], "clock": records})
        ctx.rec.fields["phones"] = [{k: v for k, v in rs["phones"][s].items() if k != "baseline_records"} for s in self.cfg.serials if s in rs["phones"]]
        rs["bound"], rs["binding_ref"] = True, ref

    def measure_clock(self, slot):
        samples = []
        for _ in range(CLOCK_SAMPLES):
            t0 = self.wall_ms()
            phone = parse_epoch_ms(self.adb(slot, "shell", "date", "+%s%3N"))
            t1 = self.wall_ms()
            samples.append({"rtt_ms": round(t1 - t0, 3), "offset_ms": round(phone - (t0 + t1) / 2, 3)})
        best = min(samples, key=lambda s: s["rtt_ms"])
        unc = best["rtt_ms"] / 2 + 1.0
        for s in samples:
            if abs(s["offset_ms"] - best["offset_ms"]) > unc + s["rtt_ms"] / 2 + 1.0:
                raise Blocked(f"slot {slot}: phone clock offset unstable across samples; no allowance can be stated")
        return best["offset_ms"], round(unc, 3), samples

    # -- connect + byte-proven binding -------------------------------------------------------------------------------------
    def _ensure_scan(self, ctx, st):
        rs = self._rs(ctx, st)
        if "scan_handle" in st:
            return
        reply = ctx.js(st, "velesAcceptance.startScan(%s)" % json.dumps("8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51"))
        if reply.get("type") != "scan_state" or reply.get("status") != "started":
            raise ProductFailure(f"rig scan: {reply.get('type')!r} code={reply.get('code')!r}")
        st["scan_handle"], rs["own_scan"] = reply["scan_handle"], True

    def _candidates(self, ctx, st):
        rs = self._rs(ctx, st)
        deadline = ctx.clock() + self.cfg.io_timeout_s
        while True:
            found = [e["device_handle"] for e in self._drain(ctx, st) if e.get("type") == "device_found" and e.get("device_handle")]
            fresh = [h for h in dict.fromkeys(found) if h not in rs["claimed_devices"]]
            if fresh:
                return fresh
            if ctx.clock() >= deadline:
                raise Blocked("no unclaimed device_found within the deadline")
            ctx.sleep(0.2)

    def _poll_new_record(self, ctx, st, want_sha, baseline):
        """Return {slot: record-or-None} once the digest shows on some phone (or the deadline passes)."""
        deadline = ctx.clock() + self.cfg.io_timeout_s
        while True:
            views = self._read_all(ctx, st)
            hits = {s: [r for r in v["records"][baseline[s]:] if r["sha256"] == want_sha] for s, v in views.items()}
            if any(hits.values()) or ctx.clock() >= deadline:
                ctx.sleep(0.3)  # let a wrongly routed duplicate become visible
                views = self._read_all(ctx, st)
                return views, {s: [r for r in v["records"][baseline[s]:] if r["sha256"] == want_sha] for s, v in views.items()}
            ctx.sleep(0.2)

    def _baseline(self, ctx, st):
        return {s: len(v["records"]) for s, v in self._read_all(ctx, st).items()}

    def _send(self, ctx, st, conn, data):
        """The one place a record leaves the rig. Every record the host accepted is remembered per connection (echo pairing)."""
        reply = ctx.js(st, f"velesAcceptance.send({js_handle(conn)}, {js_bytes(data)})")
        if reply.get("type") == "sent" and len(data) >= 4:
            self._rs(ctx, st)["sent"].append({"conn": conn, "size": len(data)})
        return reply

    def _peer_sets(self, ctx, st):
        out = {}
        for slot in self.cfg.serials:
            if slot in self._rs(ctx, st)["stopped"]:
                continue
            states = self._read(ctx, st, slot)["states"]
            out[slot] = set(states[-1]["peers"]) if states else set()
        return out

    def _host_peer(self, ctx, st, slot, before):
        """The central peer hash that appeared on `slot` because of THIS connection. Exactly one new peer or the mapping is unproven."""
        deadline = ctx.clock() + self.cfg.io_timeout_s
        while True:
            states = self._read(ctx, st, slot)["states"]
            new = (set(states[-1]["peers"]) if states else set()) - before.get(slot, set())
            if new or ctx.clock() >= deadline:
                break
            ctx.sleep(0.2)
        if len(new) != 1:
            raise Blocked(f"phone {slot}: {len(new)} new central peer(s) appeared with this connection; the host's own peer cannot be "
                          "identified from the fixture telemetry, so release/survivor tracking would be a guess")
        return next(iter(new))

    def _disconnect_quietly(self, ctx, st, conn):
        try:
            ctx.js(st, f"velesAcceptance.disconnectDevice({js_handle(conn)})")
            self._cap(ctx, "unbound-connection-closed", "chrome_cdp", {"connection": conn, "note": "closed after binding failed; the challenge bytes had already been sent"})
        except Exception as e:  # noqa: BLE001 - reported, never hidden
            ctx.rec.block(f"cleanup: an unbound connection could not be closed ({type(e).__name__})")

    def _bind_connection(self, ctx, st, conn):
        rs = self._rs(ctx, st)
        rs["seq"] += 1
        # the size grows with the sequence: the fixture's pattern has only 256 seeds, so size guarantees distinct challenges
        data = fixture_record(BIND_CHALLENGE_BYTES + rs["seq"], seed_for(rs["nonce"], "bind", rs["seq"]))
        sha = hashlib.sha256(data).hexdigest()
        baseline = self._baseline(ctx, st)
        reply = self._send(ctx, st, conn, data)
        if reply.get("type") != "sent":
            raise ProductFailure(f"bind challenge send: {reply.get('type')!r} code={reply.get('code')!r} stage={reply.get('stage')!r}")
        _, hits = self._poll_new_record(ctx, st, sha, baseline)
        owners = [s for s, h in hits.items() if h]
        ref = self._cap(ctx, f"bind-{rs['seq']}", "phone_telemetry", {"challenge_sha256": sha, "bytes": len(data), "phones_that_logged_it": owners,
                                                                       "hits": {s: len(h) for s, h in hits.items()}})
        rs["proofs"].append({"capture_ref": ref, "challenge_sha256": sha, "slot": owners[0] if len(owners) == 1 else None})
        if len(owners) != 1 or len(hits[owners[0]]) != 1:
            raise Blocked("connection could not be bound to exactly one phone by bytes "
                          f"(logged on {owners or 'no phone'}); no scan label or order is trusted")
        return owners[0]

    def _connect(self, ctx, st, want):
        rs = self._rs(ctx, st)
        self.bind_phones(ctx, st)
        if want in rs["links"]:
            self._maybe_stop_scan(ctx, st)
            return
        self._ensure_scan(ctx, st)
        for _ in range(len(self.cfg.serials) + 1):
            handle = self._candidates(ctx, st)[0]
            rs["claimed_devices"].add(handle)
            before = self._peer_sets(ctx, st)
            reply = ctx.js(st, f"velesAcceptance.connectDevice({{device_handle: {js_handle(handle)}}})")
            if reply.get("type") != "connected":
                raise ProductFailure(f"connect: {reply.get('type')!r} code={reply.get('code')!r} stage={reply.get('stage')!r}")
            conn = reply["connection_handle"]
            try:
                slot = self._bind_connection(ctx, st, conn)
                if slot in rs["links"]:
                    raise Refused(f"slot {slot} already has a link; a second connection to the same phone is not expected")
                peer = self._host_peer(ctx, st, slot, before)
            except (Blocked, ProductFailure, Refused):
                self._disconnect_quietly(ctx, st, conn)
                raise
            rs["links"][slot] = {"connection": conn, "device": handle, "connected_at": ctx.rec.clock(), "peer": peer,
                                 "capture_ref": rs["proofs"][-1]["capture_ref"]}
            if slot == want:
                break
        else:
            raise Blocked(f"no connection reached phone {want}")
        self._maybe_stop_scan(ctx, st)
        self._finish_links(ctx, st)

    def _maybe_stop_scan(self, ctx, st):
        rs = self._rs(ctx, st)
        if rs["own_scan"] and all(s in rs["links"] for s in self._needed(ctx)):
            ctx.js(st, f"velesAcceptance.stopScan({js_handle(st['scan_handle'])})")
            rs["own_scan"] = False
            st.pop("scan_handle", None)

    def _finish_links(self, ctx, st):
        rs = self._rs(ctx, st)
        links = []
        for slot, l in rs["links"].items():
            entry = {"slot": slot, "capture_ref": l["capture_ref"], "connected_at": l["connected_at"]}
            if self.capacity_probe is not None:
                cap = self.capacity_probe(slot, l["connection"])
                if isinstance(cap, dict) and cap.get("kind") and isinstance(cap.get("value"), int):
                    entry["capacity"] = cap
                    try:
                        entry["payload_budget"] = budget_from(cap)
                    except Blocked:
                        pass
            links.append(entry)
        ctx.rec.fields["links"] = links

    def connect_phone_A(self, ctx, st):
        self._connect(ctx, st, "A")

    def connect_phone_B(self, ctx, st):
        self._connect(ctx, st, "B")

    # -- scanning case -------------------------------------------------------------------------------------------------
    def observe_device_found(self, ctx, st):
        rs = self._rs(ctx, st)
        self.bind_phones(ctx, st)
        found = self._candidates(ctx, st)
        devices = [e for e in rs["events"] if e.get("type") == "device_found"]
        ref = self._cap(ctx, "device-found", "phone_telemetry", {
            "device_handles": len(set(e.get("device_handle") for e in devices)), "labels_sha256": sorted(
                hashlib.sha256(str(e.get("label")).encode()).hexdigest() for e in devices),
            "binding": "single_fixture_inference" if len(self._needed(ctx)) == 1 and len(self.cfg.serials) == 1 else "not_bound",
            "note": "a scan result is not byte-proven to be the adb-bound phone; no connection is made in this case"})
        if len(self.cfg.serials) != 1 or len(found) != 1:
            raise Blocked("device_found cannot be attributed to the adb-bound phone without a connection "
                          f"(configured phones={len(self.cfg.serials)}, advertisers seen={len(found)}); exactly one fixture must advertise")
        if self.unrelated_advertiser is None:
            raise Blocked("control unrelated_advertiser_not_reported: no unrelated advertiser source was injected")
        verdict = self.unrelated_advertiser(devices)
        ref = self._cap(ctx, "unrelated-advertiser-source", "phone_telemetry", {"verdict": verdict is True, "devices_examined": len(devices),
                                                                                  "source": type(self.unrelated_advertiser).__name__})
        ctx.rec.control("unrelated_advertiser_not_reported", "pass" if verdict is True else "fail", ref)
        if verdict is not True:
            raise ProductFailure("an unrelated advertiser was reported as a device")

    # -- transfers -----------------------------------------------------------------------------------------------------------
    def _prove_send(self, ctx, st, slot, data, label, expect_reply=True):
        rs = self._rs(ctx, st)
        link = rs["links"].get(slot)
        if link is None:
            raise Blocked(f"phone {slot} has no byte-bound link")
        sha = hashlib.sha256(data).hexdigest()
        baseline = self._baseline(ctx, st)
        host_time = ctx.rec.clock()
        reply = self._send(ctx, st, link["connection"], data)
        if reply.get("type") != "sent":
            return reply, None, sha, host_time
        views, hits = self._poll_new_record(ctx, st, sha, baseline)
        wrong = [s for s, h in hits.items() if h and s != slot]
        if wrong:
            raise ProductFailure(f"bytes for phone {slot} ({label}) were logged by phone {wrong[0]}")
        got = hits.get(slot) or []
        if not got:
            raise ProductFailure(f"host reported sent for phone {slot} ({label}) but the running fixture logged no such record")
        new = views[slot]["records"][baseline[slot]:]
        others_new = sum(len(v["records"]) - baseline[s] for s, v in views.items() if s != slot)
        if len(new) != 1 or others_new:
            raise ProductFailure(f"phone {slot} ({label}): {len(new)} new record(s) here and {others_new} elsewhere; exactly one expected")
        return reply, new[0], sha, host_time

    def prove_bytes_both_links(self, ctx, st):
        rs = self._rs(ctx, st)
        proofs = []
        rs["seq"] += 1
        for slot in self._needed(ctx):
            data = fixture_record(PROOF_BYTES, seed_for(rs["nonce"], "proof", rs["seq"], slot=slot))
            reply, rec, sha, _ = self._prove_send(ctx, st, slot, data, "proof")
            if rec is None:
                raise ProductFailure(f"proof send to {slot}: {reply.get('type')!r} code={reply.get('code')!r}")
            if rec["sha256"] != sha or rec["bytes"] != len(data):
                raise ProductFailure(f"phone {slot}: record differs from the bytes sent")
            proofs.append({"slot": slot, "sha256": sha, "bytes": len(data), "phone_epoch_ms": rec["epoch_ms"]})
        digests = [p["sha256"] for p in proofs]
        ref = self._cap(ctx, "bytes-proven", "phone_telemetry", {"proofs": proofs})
        ctx.rec.control("bytes_proven_before_trigger", "pass" if len(set(digests)) == len(digests) else "fail", ref)

    def _payload_budget(self, ctx, st, slot):
        link = self._rs(ctx, st)["links"][slot]
        if self.capacity_probe is None:
            raise Blocked("negotiated ATT capacity is not on the connector wire, the host events, or the fixture's server json "
                          "(peer MTU exists in TransportTelemetry but is not serialized); a capacity_probe must be injected")
        return budget_from(self.capacity_probe(slot, link["connection"]))

    def send_boundary_sizes(self, ctx, st):
        rs = self._rs(ctx, st)
        sizes = ctx.case["sizes"]["accept"]
        budgets = {slot: self._payload_budget(ctx, st, slot) for slot in self._needed(ctx)}  # BEFORE any send
        for slot in self._needed(ctx):
            payload = budgets[slot]
            for entry in sizes:
                size = entry if isinstance(entry, int) else {"chunk-1": payload - 1, "chunk": payload, "chunk+1": payload + 1,
                                                              "multiple-of-chunk": 2 * payload}[entry]
                if not 1 <= size <= 4096:
                    raise Blocked(f"phone {slot} {entry}: resolved size {size} is outside 1..4096")
                data = fixture_record(size, seed_for(rs["nonce"], "xfer", entry, size, slot=slot))
                sha_now = hashlib.sha256(data).hexdigest()
                if any(t["size"] == size and t["slot"] != slot and t["expected_sha256"] == sha_now for t in rs["transfers"]):
                    raise Blocked(f"size {size}: both phones would receive identical bytes, so receipts could not tell them apart")
                reply, rec, sha, host_time = self._prove_send(ctx, st, slot, data, str(entry))
                if rec is None:
                    raise ProductFailure(f"phone {slot} {entry}: send answered {reply.get('type')!r} code={reply.get('code')!r}")
                rs["transfers"].append({"slot": slot, "label": str(entry), "size": size, "chunk": payload, "expected_sha256": sha,
                                        "received_sha256": rec["sha256"], "outcome": "ok" if rec["sha256"] == sha and rec["bytes"] == size else "mismatch"})
                rs["receipts"].append({"slot": slot, "source_hash": self.cfg.source_hash(slot), "size": size, "sha256": rec["sha256"],
                                       "phone_time": iso(rec["epoch_ms"]), "host_time": host_time})
                if rs["transfers"][-1]["outcome"] != "ok":
                    raise ProductFailure(f"phone {slot} {entry}: received digest differs from the sent bytes")

    def send_rejected_sizes(self, ctx, st):
        rs = self._rs(ctx, st)
        slot = self._needed(ctx)[0]
        link = rs["links"].get(slot)
        if link is None:
            raise Blocked(f"phone {slot} has no byte-bound link")
        for size in ctx.case["sizes"]["reject_without_writes"]:
            baseline = self._baseline(ctx, st)
            data = fixture_record(size, seed_for(rs["nonce"], "reject", size))
            writes_before = self.gatt_write_probe(slot) if self.gatt_write_probe else None
            reply = ctx.js(st, f"velesAcceptance.send({js_handle(link['connection'])}, {js_bytes(data)})")
            views = self._read_all(ctx, st)
            logged = sum(len(v["records"]) - baseline[s] for s, v in views.items())
            if reply.get("type") != "error" or not reply.get("code"):
                raise ProductFailure(f"size {size}: expected an error rejection, got {reply.get('type')!r}")
            if logged:
                raise ProductFailure(f"size {size}: rejected request still produced {logged} phone record(s)")
            writes = None
            if self.gatt_write_probe is not None:
                writes = self.gatt_write_probe(slot) - writes_before
            rs["rejections"].append({"size": size, "error_code": reply["code"], "gatt_writes": writes, "phone_records_delta": logged})
            if writes:
                raise ProductFailure(f"size {size}: {writes} GATT write(s) were observed for a request that must be rejected without writes")
            if writes is None:
                self._cap(ctx, f"rejected-{size}", "phone_telemetry", {"size": size, "error_code": reply["code"], "phone_records_delta": 0,
                                                                          "gatt_writes": "not measurable"})
                raise Blocked("zero GATT writes for rejected sizes is not observable: phones only log reassembled records and no "
                              "gatt_write_probe was injected (phone record delta 0 is necessary, not sufficient)")

    def observe_phone_receipts(self, ctx, st):
        rs = self._rs(ctx, st)
        self.bind_phones(ctx, st)
        views = self._read_all(ctx, st)
        for r in rs["receipts"]:  # only records of THIS case (since binding), so a reused digest from an earlier case cannot back a receipt
            since = rs["phones"][r["slot"]]["baseline_records"]
            logged = [x for x in views[r["slot"]]["records"][since:] if x["sha256"] == r["sha256"] and x["bytes"] == r["size"]]
            if len(logged) != 1:
                raise ProductFailure(f"receipt for phone {r['slot']} size {r['size']} is not backed by exactly one record of this case")
        ref = self._cap(ctx, "phone-receipts", "phone_telemetry", {"receipts": rs["receipts"], "transfers": rs["transfers"]})
        ctx.rec.fields.update(transfers=rs["transfers"], phone_receipts=rs["receipts"], rejections=rs["rejections"])
        by_size = {}
        for t in rs["transfers"]:
            by_size.setdefault((t["label"], t["size"]), {})[t["slot"]] = t["expected_sha256"]
        differs = all(len(set(v.values())) == len(v) for v in by_size.values()) and any(len(v) > 1 for v in by_size.values())
        ctx.rec.control("per_phone_expected_digest_differs", "pass" if differs else "fail", ref)
        self._fragment_control(ctx, st)
        if rs["rejections"] and any(r["gatt_writes"] is None for r in rs["rejections"]):
            raise Blocked("rejected_sizes_produce_no_gatt_writes not measured")
        if not rs["rejections"]:
            raise Blocked("rejected_sizes_produce_no_gatt_writes: no rejected size was exercised in this run")
        ctx.rec.control("rejected_sizes_produce_no_gatt_writes", "pass" if all(r["gatt_writes"] == 0 for r in rs["rejections"]) else "fail", ref)

    def _fragment_control(self, ctx, st):
        """Pair connector `message` events with the records that were sent: per connection, exactly one echo of exactly the sent
        record's encoded length for every accepted record >= 4 bytes. A surplus or differently sized message is a fragment (FAIL);
        a missing echo means the claim is unproven (Blocked)."""
        rs = self._rs(ctx, st)
        expected = Counter((x["conn"], b64len(x["size"])) for x in rs["sent"])
        deadline = ctx.clock() + self.cfg.io_timeout_s
        while True:
            self._drain(ctx, st)
            got = Counter((e.get("connection_handle"), e.get("record_length")) for e in rs["events"] if e.get("type") == "message")
            surplus = got - expected
            missing = expected - got
            if surplus or not missing or ctx.clock() >= deadline:
                break
            ctx.sleep(0.2)
        view = {"expected_echoes": sorted([c, n, k] for (c, n), k in expected.items()), "observed_messages": sorted(
            [str(c), str(n), k] for (c, n), k in got.items())}
        ref = self._cap(ctx, "connector-messages", "host_events", {**view, "surplus": sum(surplus.values()), "missing": sum(missing.values())})
        if surplus:
            raise ProductFailure(f"connector saw {sum(surplus.values())} message(s) beyond one echo per sent record or of a different length: possible fragments")
        if missing or not expected:
            raise Blocked("connector_sees_no_fragments: not every sent record produced its echo at the connector, so absence of fragments is unproven")
        ctx.rec.control("connector_sees_no_fragments", "pass", ref)

    # -- per-phone failure and survivor ----------------------------------------------------------------------------------------
    def fail_phone_A_link(self, ctx, st):
        rs = self._rs(ctx, st)
        if self.link_failer is None:
            raise Blocked("no link_failer injected: failing one phone's link is a controller-owned action "
                          "(the OTP-23 fixture's own stop file is the only known sanctioned trigger)")
        link = rs["links"].get("A")
        if link is None:
            raise Blocked("phone A has no byte-bound link")
        self._arm(ctx, st, "A")
        st["trigger"], st["trigger_wall"] = "fail_phone_A_link", ctx.rec.clock()
        rs["stopped"].add("A")  # a stop-file trigger leaves A's telemetry CAPTURED; that is read as such, never as a fixture fault
        self.link_failer("A")
        self._cap(ctx, "trigger-fail-phone-A", "chrome_cdp", {"trigger": "fail_phone_A_link", "at": st["trigger_wall"], "armed_in_flight_send": True})

    def _arm(self, ctx, st, slot):
        """Start one real `send` on the slot's link WITHOUT awaiting it and prove it is still pending: exactly one new request id in the
        connector's pending map and its settle flag still false, read immediately before the trigger. Never raises Blocked: the result
        (proven or the reason it is not) is stored for observe_pending_cancellation, so a failed proof cannot silently pass."""
        rs = self._rs(ctx, st)
        rs["armed"] = {"proven": False, "slot": slot, "reason": "not armed"}
        link = rs["links"].get(slot)
        if link is None:
            rs["armed"]["reason"] = f"phone {slot} has no byte-bound link to arm a request on"
            return
        try:
            before = set(ctx.js(st, "velesAcceptance.snapshot()").get("pending") or [])
            ctx.js(st, f"(window.__armedA = velesAcceptance.send({js_handle(link['connection'])}, {js_bytes(fixture_record(64, 7))}), "
                       "window.__armedS = {settled: false}, window.__armedA.then(() => { window.__armedS.settled = true; }), true)")
            now = set(ctx.js(st, "velesAcceptance.snapshot()").get("pending") or [])
            settled = ctx.js(st, "window.__armedS.settled")
        except (RuntimeError, ConnectionError, TimeoutError, OSError) as e:
            rs["armed"]["reason"] = f"arming failed ({type(e).__name__})"
            return
        new = sorted(now - before)
        if len(new) != 1 or settled is not False:
            rs["armed"]["reason"] = (f"the armed request was not provably pending before the trigger (new pending ids={len(new)}, "
                                     f"settled={settled!r}): it completed too fast to exercise cancellation")
            return
        rs["armed"] = {"proven": True, "slot": slot, "connection": link["connection"], "request_id": new[0], "pending_before_trigger": sorted(now), "checked_at": ctx.rec.clock()}
        self._cap(ctx, "armed-pending-proof", "chrome_cdp", {k: v for k, v in rs["armed"].items()})

    def before_trigger(self, ctx, st, how):
        """Case-runner hook: immediately before a global lifecycle trigger fires, arm and prove a pending request on a bound link."""
        # arm only where the case itself observes pending cancellation: e.g. teardown-hygiene must not gain an extra send
        if how in GLOBAL_TRIGGERS and self._needed(ctx) and "observe_pending_cancellation" in ctx.case["required_actions"]:
            rs = self._rs(ctx, st)
            if rs["links"]:
                self._arm(ctx, st, sorted(rs["links"])[0])
            else:
                rs["armed"] = {"proven": False, "reason": "no byte-bound link existed to arm a request on"}

    @staticmethod
    def _allowed_cancellation(spec, entry, armed):
        """True only for a HOST-delivered terminal error for exactly the armed request id (and link, when disclosed) whose (code, stage)
        is one the case allows for its trigger. Synthesized errors and anything else are not proof."""
        if not isinstance(entry, dict) or entry.get("type") != "error" or entry.get("synthesized") is True:
            return False
        if entry.get("request_id") != armed["request_id"]:
            return False
        if entry.get("connection_handle") not in (None, armed.get("connection")):
            return False
        return [entry.get("code"), entry.get("stage")] in [list(x) for x in spec["terminal_error"]]

    def record_pending_disposition(self, ctx, st):
        """Option 2 for abrupt EOF triggers: record that the individual pending request's outcome is NOT observable. Nothing is armed,
        nothing is claimed; the connector's synthesized port_closed is mentioned only as local. The case's cleanup proof (process exit and
        BLE link release for both phones) is separate and unchanged."""
        spec = ctx.case.get("pending_request_handling") or {}
        if spec.get("mode") != "eof_unobservable":
            raise Blocked("this case does not define an unobservable-EOF pending disposition")
        if st.get("trigger") != spec["trigger"]:
            raise Blocked(f"the recorded trigger is {st.get('trigger')!r}, not {spec['trigger']!r}: the disposition would describe another case")
        reason = ("abrupt EOF on the Native Messaging pipe: the connector is detached and Chrome owns the pipe, so the host's per-request "
                  "terminal events are not observable; any connector-synthesized port_closed is local only and is not host evidence")
        ref = self._cap(ctx, "pending-disposition", "host_events", {
            "mode": spec["mode"], "trigger": spec["trigger"], "per_request_outcome": "unknown", "host_correlated_cancellation_claimed": False,
            "reason": reason, "decision": "option 2 (user, 2026-11-02)"})
        ctx.rec.fields["pending_request_handling"] = {
            "mode": spec["mode"], "trigger": spec["trigger"], "per_request_outcome": "unknown",
            "host_correlated_cancellation_claimed": False, "reason": reason, "capture_ref": ref}

    def observe_pending_cancellation(self, ctx, st):
        """Strict host-correlated cancellation (explicit shutdown, per-phone failure). The proof comes from the connector's `completionLog`,
        which contains host-delivered events only, never the connector's own synthesized errors."""
        rs = self._rs(ctx, st)
        spec = ctx.case.get("pending_request_handling") or {}
        if spec.get("mode") != "host_correlated_cancellation":
            raise Blocked("this case does not require host-correlated pending cancellation (abrupt EOF cases use record_pending_disposition)")
        if st.get("trigger") != spec["trigger"]:
            raise Blocked(f"the recorded trigger is {st.get('trigger')!r}, not {spec['trigger']!r}")
        armed = rs.get("armed")
        if not armed or not armed.get("proven"):
            raise Blocked("pending-request cancellation was not exercised: " + (armed or {}).get("reason", "no request was armed before the trigger"))
        rid = armed["request_id"]
        if not self._page_reachable(ctx, st):
            raise Blocked("the connector page is unreachable, so the host's correlated terminal event for the armed request cannot be read")
        deadline = ctx.clock() + self.cfg.io_timeout_s
        while True:
            log = ctx.js(st, "velesAcceptance.completionLog()") or []
            mine = [e for e in log if isinstance(e, dict) and e.get("request_id") == rid]
            if mine or ctx.clock() >= deadline:
                break
            ctx.sleep(0.2)
        snap = ctx.js(st, "velesAcceptance.snapshot()") or {}
        self._cap(ctx, "pending-cancellation", "host_events", {"armed": armed, "pending_after": snap.get("pending"), "host_log_for_armed": mine,
                                                               "shutdown_complete": [e for e in log if isinstance(e, dict) and e.get("type") == "shutdown_complete"]})
        completes = [e for e in log if isinstance(e, dict) and e.get("type") == "shutdown_complete"]
        if not mine:
            if rid in (snap.get("pending") or []):
                raise ProductFailure(f"the armed request was still pending and never received a host terminal event after {spec['trigger']}"
                                     + (" although the host logged shutdown_complete" if completes else ""))
            if spec.get("ordering") == "terminal_error_before_shutdown_complete" and completes:
                # observed violation, not missing observability: the host completed shutdown while a request proven pending had no
                # host terminal error (the request only ended through the connector's own synthesized outcome, which is local)
                raise ProductFailure("shutdown_complete was logged by the host but the request proven pending at the trigger received no "
                                     "correlated host terminal error (any port_closed outcome is connector-synthesized and local)")
            raise Blocked("the armed request ended without any host-delivered terminal event (only a connector-synthesized outcome) and no "
                          "host shutdown_complete was logged, so the host's behaviour is not observed: not proof")
        if len(mine) != 1:
            raise ProductFailure(f"the armed request received {len(mine)} terminal events; exactly one is required")
        entry = mine[0]
        if not self._allowed_cancellation(spec, entry, armed):
            raise Blocked(f"the armed request's host event was {entry.get('type')!r} {entry.get('code')!r}/{entry.get('stage')!r}, not the "
                          f"documented cancellation signal {spec['terminal_error']} for {spec['trigger']!r}")
        before_complete = None
        if spec.get("ordering") == "terminal_error_before_shutdown_complete":
            if len(completes) != 1:
                raise Blocked("shutdown_complete is not in the host-event log, so the ordering of the cancellation cannot be shown")
            if not entry["seq"] < completes[0]["seq"]:
                raise ProductFailure("shutdown_complete preceded the armed request's terminal error")
            before_complete = True
        ref = "pending-cancellation"
        block = {"mode": spec["mode"], "trigger": spec["trigger"], "request_id": rid, "armed_pending_proven": True,
                 "terminal": {"type": "error", "request_id": rid, "code": entry["code"], "stage": entry["stage"], "synthesized": False,
                              "connection_handle": entry.get("connection_handle"), "source": "connector completionLog (host-delivered)"},
                 "capture_ref": next(c["name"] for c in reversed(ctx.rec.captures) if c["name"].startswith(ref))}
        if before_complete:
            block["terminal_before_shutdown_complete"] = True
        ctx.rec.fields["pending_request_handling"] = block

    def _page_reachable(self, ctx, st):
        try:
            return ctx.js(st, "typeof window.velesAcceptance === 'object'") is True
        except (RuntimeError, ConnectionError, TimeoutError, OSError):  # the page/CDP session is gone; that IS the observation
            return False

    def survivor_transfer_phone_B(self, ctx, st):
        rs = self._rs(ctx, st)
        rs["seq"] += 1
        data = fixture_record(PROOF_BYTES, seed_for(rs["nonce"], "survivor", rs["seq"]))
        sha = hashlib.sha256(data).hexdigest()
        try:
            reply, rec, sha, _ = self._prove_send(ctx, st, "B", data, "survivor")
        except ProductFailure as e:
            ref = self._cap(ctx, "survivor-transfer", "phone_telemetry", {"slot": "B", "sha256": sha, "logged": False, "failure": str(e)[:200]})
            ctx.rec.control("survivor_progress_after_failure", "fail", ref)
            raise
        ref = self._cap(ctx, "survivor-transfer", "phone_telemetry", {"slot": "B", "sha256": sha, "reply_type": reply.get("type"),
                                                                        "logged": rec is not None})
        if rec is None or rec["sha256"] != sha:
            ctx.rec.control("survivor_progress_after_failure", "fail", ref)
            raise ProductFailure(f"survivor phone B did not receive fresh bytes after A failed: {reply.get('type')!r}")
        ctx.rec.control("survivor_progress_after_failure", "pass", ref)

    # -- link release (phone-side, independent of the host process) ----------------------------------------------------------------
    def observe_link_release(self, ctx, st):
        rs = self._rs(ctx, st)
        trigger = st.get("trigger")
        trig_ms = parse_iso_ms(st["trigger_wall"]) if st.get("trigger_wall") else None
        if trigger is None or trig_ms is None:
            raise Blocked("no recorded trigger to attribute a link release to")
        expect_released = ["A"] if trigger == "fail_phone_A_link" else list(self._needed(ctx))
        expect_held = [s for s in self._needed(ctx) if s not in expect_released]
        results = {}
        deadline = ctx.clock() + self.cfg.release_bound_s + 5
        pending = set(expect_released)
        while pending:
            for slot in sorted(pending):
                view = self._read(ctx, st, slot)
                results[slot] = self._release_of(rs, slot, view["states"], trig_ms, view["status"] == "CAPTURED")
                if results[slot].get("released_at_host_ms") is not None or results[slot].get("stopped_fixture_no_release_state"):
                    pending.discard(slot)
            if pending and ctx.clock() >= deadline:
                break
            if pending:
                ctx.sleep(0.5)
        for slot in expect_held:
            states = self._read(ctx, st, slot)["states"]
            results[slot] = {"held": bool(states) and rs["links"][slot]["peer"] in states[-1]["peers"]}
        ref = self._cap(ctx, "link-release", "phone_telemetry", {"trigger": trigger, "results": results, "bound_s": self.cfg.release_bound_s,
                                                                   "tracked_peer": "the host's own central peer hash recorded when the link was bound",
                                                                   "source": "fixture peers list (phone-side), not host process exit"})
        if any(results[s].get("not_connected_before_trigger") for s in expect_released):
            raise Blocked("the phone state did not show the host's own peer connected before the trigger; release cannot be attributed")
        if any(results[s].get("stopped_fixture_no_release_state") for s in expect_released):
            raise Blocked("the failed phone's fixture stopped (CAPTURED) without recording the peer's departure: release of the link is not "
                          "observable from this telemetry and is not guessed")
        unreleased = [s for s in expect_released if results[s].get("released_at_host_ms") is None]
        late = [s for s in expect_released if results[s].get("gap_ms") is not None and results[s]["gap_ms"] > self.cfg.release_bound_s * 1000]
        lost = [s for s in expect_held if not results[s]["held"]]
        if "release_measured_independently_of_process_exit" in ctx.case["required_controls"]:
            ctx.rec.control("release_measured_independently_of_process_exit", "pass" if not unreleased and not late else "fail", ref)
        if trigger in GLOBAL_TRIGGERS and not unreleased and not late and \
                "survivor_progress_not_applicable_for_global_trigger" in ctx.case["required_controls"]:
            # evidence: the trigger is process/port/Chrome-wide and the measured result shows EVERY link released, i.e. no survivor exists
            ctx.rec.control("survivor_progress_not_applicable_for_global_trigger", "pass", ref)
        if unreleased or late or lost:
            raise ProductFailure(f"link release: unreleased={unreleased} late={late} survivor_lost={lost}")

    def _release_of(self, rs, slot, states, trig_ms, captured=False):
        p = rs["phones"][slot]
        peer = rs["links"][slot]["peer"]
        off, unc = p["clock_offset_ms"], p["clock_uncertainty_ms"]
        before = [s for s in states if s["epoch_ms"] - off <= trig_ms + unc]
        if not before or peer not in before[-1]["peers"]:
            return {"not_connected_before_trigger": True, "uncertainty_ms": unc}
        after = [s for s in states if s["epoch_ms"] - off > trig_ms - unc and peer not in s["peers"]]
        if not after:
            if captured:
                return {"stopped_fixture_no_release_state": True, "uncertainty_ms": unc}
            return {"released_at_host_ms": None, "uncertainty_ms": unc}
        at = after[0]["epoch_ms"] - off
        return {"released_at_host_ms": at, "gap_ms": at - trig_ms, "uncertainty_ms": unc, "released_at": iso(at)}

    def cleanup(self, ctx, st):
        """Always called by the case runner (even after Blocked/FAIL): a condition that was applied must be restored, and the
        restoration verified; an unverified restore is retried here and, if it still cannot be proven, reported as possibly applied."""
        if st.get("condition") and not st.get("condition_restored") and self.conditions is not None:
            try:
                self.conditions.restore(st["condition"])
                verified = self.conditions.verify_restored(st["condition"])
            except Exception as e:  # noqa: BLE001 - reported, never hidden
                ctx.rec.block(f"cleanup: CONDITION {st['condition']} NOT RESTORED (may still be applied): {type(e).__name__}")
                return
            st["condition_restored"] = verified is True
            self._cap(ctx, "condition-cleanup-restore", "chrome_cdp", {"condition": st["condition"], "restored": True, "verified": verified is True})
            if verified is not True:
                ctx.rec.block(f"cleanup: CONDITION {st['condition']} restore could not be verified (may still be applied)")

    # -- injected deliberate conditions --------------------------------------------------------------------------------------------
    def _condition(self, ctx, st, name):
        if self.conditions is None:
            raise Blocked(f"{name}: no condition controller injected (radio/adapter/service control is owned by the controller)")
        base = self._baseline_request(ctx, st)
        ref = self._cap(ctx, "baseline-works", "chrome_cdp", base)
        ctx.rec.control("baseline_works_before_condition", "pass" if base["ok"] else "fail", ref)
        if not base["ok"]:
            raise Blocked("baseline request failed before the condition; the condition would prove nothing")
        st["condition"] = name
        self.conditions.apply(name)
        st["condition_at"] = ctx.rec.clock()
        st["error_reply"] = self._elicit(ctx, st, name)

    def _elicit(self, ctx, st, name):
        if name == "synthetic_service_denial":
            raise Blocked("no sanctioned fixture command produces a synthetic service denial (the fixture only implements "
                          "inject_duplicate_connected); a controller-injected elicitation is required")
        reply = ctx.js(st, 'velesAcceptance.startScan("8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51")')
        if reply.get("type") == "scan_state" and reply.get("status") == "started":
            # the condition did not take effect: do not leave a scan running behind the case
            ctx.js(st, f"velesAcceptance.stopScan({js_handle(reply['scan_handle'])})")
        return reply

    def _baseline_request(self, ctx, st):
        reply = ctx.js(st, 'velesAcceptance.startScan("8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51")')
        ok = reply.get("type") == "scan_state" and reply.get("status") == "started"
        if ok:
            ctx.js(st, f"velesAcceptance.stopScan({js_handle(reply['scan_handle'])})")
        return {"ok": ok, "type": reply.get("type"), "code": reply.get("code")}

    def radio_off(self, ctx, st):
        self._condition(ctx, st, "radio_off")

    def adapter_absent(self, ctx, st):
        self._condition(ctx, st, "adapter_absent")

    def synthetic_service_denial(self, ctx, st):
        self._condition(ctx, st, "synthetic_service_denial")

    def observe_error_event(self, ctx, st):
        reply = st.get("error_reply") or {}
        ref = self._cap(ctx, "error-event", "host_events", {k: reply.get(k) for k in ("type", "code", "recoverability", "stage")})
        if reply.get("type") != "error":
            raise ProductFailure(f"condition {st.get('condition')!r} produced {reply.get('type')!r}, not an error event")
        native = self.native_code_source(ctx, reply) if self.native_code_source else None
        if native:
            self._cap(ctx, "native-code-source", "host_events", {"native_code": str(native), "source": type(self.native_code_source).__name__,
                                                                  "for_error": {k: reply.get(k) for k in ("code", "stage")}})
        if not native:
            raise Blocked("the host error event carries code/recoverability/stage only (protocol.rs Event::Error); the required "
                          "native_code is not on the connector wire and no native_code_source was injected")
        ctx.rec.callbacks.append({"code": reply.get("code"), "t": ctx.rec.clock(), "native_code": native, "classification": reply.get("stage"),
                                  "recoverability": reply.get("recoverability"), "capture_ref": ref})

    def restore_condition(self, ctx, st):
        if self.conditions is None:
            raise Blocked("no condition controller injected")
        self.conditions.restore(st.get("condition"))
        verified = self.conditions.verify_restored(st.get("condition"))
        st["condition_restored"] = verified is True  # an unverified restore stays "not restored" so cleanup retries it
        ref = self._cap(ctx, "condition-restored", "chrome_cdp", {"condition": st.get("condition"), "verified": verified is True})
        ctx.rec.control("condition_restored_verified", "pass" if verified is True else "fail", ref)
        if verified is not True:
            raise Blocked("condition restore could not be verified (the condition may still be applied)")
        # NOTE: `not_reinterpreting_historical_accessdenied` is deliberately NOT recorded: it is a statement about how the evidence
        # is interpreted, not something this rig can measure. The case stays BLOCKED on it until the adjudicator records it.

    def fresh_request_after_restore(self, ctx, st):
        st["fresh_reply"] = ctx.js(st, 'velesAcceptance.startScan("8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51")')
        if st["fresh_reply"].get("type") == "scan_state" and st["fresh_reply"].get("status") == "started":
            ctx.js(st, f"velesAcceptance.stopScan({js_handle(st['fresh_reply']['scan_handle'])})")  # leave nothing running

    def observe_recovery(self, ctx, st):
        reply = st.get("fresh_reply") or {}
        self._cap(ctx, "recovery", "host_events", {k: reply.get(k) for k in ("type", "status", "code", "stage")})
        if reply.get("type") != "scan_state" or reply.get("status") != "started":
            raise ProductFailure(f"fresh request after restore: {reply.get('type')!r} code={reply.get('code')!r}")
