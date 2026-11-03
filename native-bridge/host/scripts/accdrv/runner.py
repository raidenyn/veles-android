"""Assembles one case run (preflight -> context -> lifecycle) from explicit inputs and injectable dependencies.

Every refusal that happens BEFORE Chrome or the host is started still produces a BLOCKED run record (with the
executable hash and source identity) so a skipped case is visible, never silent. Nothing here signs, registers
globally outside the journalled Windows key, or touches a personal profile.
"""
import getpass
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import anchor as anchor_mod, ids, macid, observers, probes, registration as reg
from . import chrome as chrome_mod
from .chrome import ChromeSession
from .errors import Blocked, Refused
from .lifecycle import Ctx, UnattachedRig, run_case
from .recorder import Recorder

HOST_DIR = Path(__file__).resolve().parents[2]
EXT_DIR = HOST_DIR / "acceptance" / "driver-extension"


def host_family(plat=None):
    return {"win32": "windows", "darwin": "macos"}.get(plat or sys.platform, "linux")


def default_run(argv):
    """Run a read-only OS command. Launch failures and timeouts are Blocked (infrastructure), never an empty result."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise Blocked(f"{argv[0]} could not be run: {type(e).__name__}")
    return p.returncode, p.stdout, p.stderr


def anchor_fields(anchor, host, attest):
    """run.json source/evaluator/binary blocks from the externally computed anchor (validator.compute_anchor)."""
    data = Path(host).read_bytes()
    want = anchor.get("binary_sha256")
    if want and (hashlib.sha256(data).hexdigest() != want or len(data) != anchor.get("binary_size", len(data))):
        raise Blocked("the production executable differs from the anchored sha256/size at record time (swapped after verification)")
    if "externally_attested" in anchor:  # pinned external anchor: only ITS attestation counts, never a caller flag
        attest = anchor["externally_attested"] is True
    return {
        "source": {"revision": anchor["source_revision"], "tree_sha256": anchor["source_tree_sha256"]},
        "evaluator": {"revision": anchor["source_revision"], "files": anchor["evaluator_files"]},
        "binary": {"kind": "production", "name": Path(host).name, "path": anchor["binary_path"], "sha256": hashlib.sha256(data).hexdigest(),
                   "size": len(data), "role": "production_source_acceptance_build", "build_command": "VELES_ALLOWED_ORIGINS=<acceptance origin> ./gradlew bridgeHostBuild",
                   "built_from_source_revision": anchor["source_revision"] if attest else None,
                   "built_from_tree_sha256": anchor["source_tree_sha256"] if attest else None},
    }


def os_fields(family):
    return {"os": {"name": family, "version": platform.release(), "build": platform.version(), "arch": platform.machine()},
            "account": {"id_sha256": hashlib.sha256(getpass.getuser().encode()).hexdigest()}}


def blocked_record(case, host, out, reasons, fields=None):
    rec = Recorder(out, case)
    rec.fields.update(fields or {})
    for r in reasons:
        rec.block(r)
    rec.fields.setdefault("note", "No device, Chrome profile or global registration was used by this record.")
    return rec.finalize()


def _rehash_check(host, expected):
    """Post-run: the executable must still be the bytes that were hashed before launch (TOCTOU guard)."""
    def check(rec):
        now = hashlib.sha256(Path(host).read_bytes()).hexdigest()
        rec.fields.setdefault("binary", {})["sha256_after_run"] = now
        if now != expected:
            rec.block("the production executable changed during the run (sha256 differs from the pre-launch hash)")
    return check


def execute_case(case, host, chrome_path, out, anchor, *, family=None, attest=False, run=None, backend_factory=None,
                 session_factory=ChromeSession, popen=subprocess.Popen, rig=None, pipe_tap=None, windows_census=None,
                 home=None, env=None, extension_dir=EXT_DIR, clock=None, sleep=None, rec_clock=None, state_dir=None,
                 system_root="/", temp_root=None, adapter=None):
    """Returns the finalized run.json dict (never PASS). Anything that goes wrong after the output directory exists
    still yields a BLOCKED record (unexpected exceptions are flagged `driver_errors`).

    `run` is the read-only OS command runner (tests inject a fake; production uses the bounded default_run).
    `adapter(family, run) -> dict` is injectable; when omitted the real, read-only platform probe is used with the
    probe's own bounded runner unless a run was injected."""
    family = family or host_family()
    adapter_run = run
    run = run or default_run
    home = home or str(Path.home())
    env = dict(os.environ if env is None else env)
    out = Path(out)
    if out.exists():
        raise Refused(f"{out} exists; evidence is never overwritten")
    pfamily = "windows" if case["platform"] == "windows" else "posix"
    reg.check_executable(host, pfamily)
    try:
        fields = {"operator": getpass.getuser(), **anchor_fields(anchor, host, attest)}
    except Blocked as e:
        return blocked_record(case, host, out, [str(e)])
    anchored_sha = anchor.get("binary_sha256") or fields["binary"]["sha256"]

    def guard(where):
        return anchor_mod.host_guard(host, anchored_sha)(where)
    if case["platform"] != family:
        return blocked_record(case, host, out, [f"case targets {case['platform']} but this machine is {family}"], fields)
    ext_id, origin = ids.read_extension_identity(extension_dir)
    try:
        ids.binary_embeds_origin(host, origin)
    except Blocked as e:
        return blocked_record(case, host, out, [str(e)], fields)
    fields.update(os_fields(family))
    child_env = chrome_mod.clean_env(env)  # one scrubbed environment for Chrome AND direct launches
    rec = Recorder(out, case, **({"clock": rec_clock} if rec_clock else {}))
    rec.fields.update(fields)
    work = None
    lock = None
    try:
        run_id = hashlib.sha256(os.urandom(16)).hexdigest()[:12]
        host_name = reg.host_name_for(run_id)
        kw = {"sleep": sleep} if sleep else {}
        rec.fields["chrome_extension"] = {"id": ext_id, "origin": origin}
        try:
            work = Path(tempfile.mkdtemp(prefix=reg.WORK_PREFIX, dir=temp_root))
            if case["requires_signing"] or family == "macos":
                rec.fields["signing"] = macid.read_signing(host, run, work / "certs")
                guard("after codesign")  # the signature read must describe the bytes that were anchored
            elif family == "windows":
                rec.fields["signing"] = {"authenticode": macid.windows_authenticode(str(host), run)}
                guard("after Authenticode")
        except (Blocked, Refused) as e:
            rec.block(f"signing identity: {e}")
            return rec.finalize()
        try:
            rec.fields["adapter"] = (adapter or probes.adapter_context)(family, adapter_run)
        except Blocked as e:
            rec.block(f"adapter identity: {e}")
            return rec.finalize()
        observer = observers.Observer("windows" if family == "windows" else "macos", run, windows_census)
        chrome = registration = None
        if case["kind"] == "chrome":
            manifest = reg.build_manifest(host_name, host, origin, pfamily)
            manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode()
            popen_kw = {} if popen is subprocess.Popen else {"popen": popen}
            if family == "macos":
                dirs = reg.native_messaging_dirs(home, system_root)
                registration = reg.ProfileManifestRegistration(host_name, manifest, lambda: reg.snapshot_native_hosts(dirs))

                def before_launch(profile):
                    guard("immediately before launch")
                    registration.place(profile)
                chrome = session_factory(chrome_path, work, home, child_env, before_launch=before_launch, **popen_kw, **kw)
            else:
                backend = (backend_factory or reg.WinregBackend)()
                sdir = reg.ensure_state_dir(state_dir or reg.state_dir_for(env))
                try:
                    lock = reg.RunLock(sdir)
                    lock.acquire()
                    reg.startup_check(backend, sdir)
                except (Refused, Blocked) as e:
                    rec.block(f"startup refused (nothing was changed): {e}")
                    return rec.finalize()
                mdir = work / "manifests"
                mdir.mkdir(mode=0o700)
                mpath = mdir / f"{host_name}.json"
                reg.write_exclusive(mpath, manifest_bytes)
                registration = reg.WindowsRegistration(backend, sdir / reg.JOURNAL_NAME, host_name, mpath, manifest_bytes, work_dir=work)
                chrome = session_factory(chrome_path, work, home, child_env, before_launch=lambda profile: guard("immediately before launch"),
                                         **popen_kw, **kw)
        ctx_kw = dict(kw)
        if clock:
            ctx_kw["clock"] = clock
        ctx = Ctx(case, rec, chrome=chrome, observer=observer, host_exe=str(host), host_name=host_name, ext_dir=str(extension_dir),
                  ext_id=ext_id, origin=origin, rig=rig or UnattachedRig(), pipe_tap=pipe_tap, popen=guarded_popen(popen, guard), guard=guard, env=child_env, **ctx_kw)
        return run_case(ctx, registration, post_checks=(_rehash_check(host, anchored_sha),))
    except (KeyboardInterrupt, SystemExit):
        raise
    except (Blocked, Refused) as e:
        rec.block(f"refused before the run started: {e}")
        return rec.finalize()
    except Exception as e:  # noqa: BLE001 - assembly failure: still leave a BLOCKED record, flagged as a driver error
        rec.driver_error("assembly", e)
        return rec.finalize()
    finally:
        # An unrecovered journal keeps BOTH the lock (no second run may start) and the work dir (recovery reads it).
        if not state_dir_journal_exists(state_dir, env, family):
            if lock is not None:
                lock.release()
            if work is not None:
                _rmtree(work)


def guarded_popen(popen, guard):
    def launch(*a, **k):
        guard("immediately before direct launch")
        return popen(*a, **k)
    return launch


def state_dir_journal_exists(state_dir, env, family):
    if family != "windows":
        return False
    try:
        return (Path(state_dir) if state_dir else reg.state_dir_for(env)).joinpath(reg.JOURNAL_NAME).exists()
    except (Blocked, OSError):
        return False


def _rmtree(path):
    shutil.rmtree(path, ignore_errors=True)
