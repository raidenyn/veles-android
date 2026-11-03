"""Isolated, exact-origin Native Messaging registration for acceptance runs.

Rules enforced here (each is unit-tested with fakes):

* The manifest has exactly one `allowed_origins` entry (the disposable extension's exact origin), `type: stdio`
  and an absolute `path` that is the REAL production executable (regular file, not a symlink, exact name).
* Windows: the only global effect is one uniquely named per-user (HKCU) key. It is created ONLY if absent
  (an existing key is never overwritten), a journal is made durable BEFORE the mutation, only that exact key
  is deleted afterwards (and only if its value is still ours), and the surrounding key list must equal the
  pre-run snapshot or the restore is reported as failed with the journal retained for `recover`.
* macOS: NO global registration at all. Chrome reads `<user-data-dir>/NativeMessagingHosts/` first, so the
  manifest lives inside the disposable profile. (That Chrome honours this on the target Mac is an assumption
  proven only by a real run; if it does not, the case BLOCKs at connect_native - never fall back to the
  personal NativeMessagingHosts directory.)
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from .errors import Blocked, Refused
from .ids import ORIGIN_RE

NAME_PREFIX = "me.nagaev.veles.otp24.acceptance."
HOST_NAME_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
# The ONLY registry key names this driver may create, read back, or delete: prefix + 12 hex run id, nothing else.
ACCEPT_NAME_RE = re.compile(r"^me\.nagaev\.veles\.otp24\.acceptance\.[0-9a-f]{12}$")
JOURNAL_SCHEMA = "veles-otp24-registration-journal/1"
JOURNAL_NAME = "registration-journal.json"
LOCK_NAME = "run.lock"
WORK_PREFIX = "veles-otp24-acc-"
RUN_ID_RE = re.compile(r"^[0-9a-f]{12}$")
PRODUCTION_NAMES = {"veles-native-host", "veles-native-host.exe"}
WIN_PARENT = r"Software\Google\Chrome\NativeMessagingHosts"
MANIFEST_KEYS = {"name", "description", "path", "type", "allowed_origins"}


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def host_name_for(run_id):
    if not RUN_ID_RE.match(run_id or ""):
        raise Refused("run id must be 12 lowercase hex characters")
    return NAME_PREFIX + run_id


def _abs_ok(path, family):
    if family == "windows":
        return bool(re.match(r"^[A-Za-z]:[\\/]", path)) and "\x00" not in path and not path.startswith("\\\\")
    return path.startswith("/") and "\x00" not in path


def build_manifest(name, exe, origin, family):
    """The exact host manifest for `exe`. Refuses anything but the real production executable."""
    exe = str(exe)
    if not name.startswith(NAME_PREFIX) or not HOST_NAME_RE.match(name):
        raise Refused(f"host name must be {NAME_PREFIX}<run id>")
    if not ORIGIN_RE.match(origin):
        raise Refused("exactly one concrete chrome-extension origin is required (no wildcard)")
    if family not in ("windows", "posix") or not _abs_ok(exe, family):
        raise Refused(f"{exe!r} is not an absolute {family} path")
    base = re.split(r"[\\/]", exe)[-1]
    if base.casefold() != ("veles-native-host.exe" if family == "windows" else "veles-native-host"):
        raise Refused(f"{base!r} is not the production host name for {family}")
    if re.search(r"(^|[\\/])\.\.?([\\/]|$)", exe):
        raise Refused("executable path must be normalized")
    return {"name": name, "description": "Veles OTP-24 acceptance test registration (disposable, not a production registration)",
            "path": exe, "type": "stdio", "allowed_origins": [origin]}


def check_manifest(doc, exe, origin, family):
    """Problems (empty = exactly the manifest this driver would write). Used on the manifest as read BACK."""
    problems = []
    if not isinstance(doc, dict):
        return ["manifest is not an object"]
    if set(doc) - MANIFEST_KEYS:
        problems.append(f"unexpected keys {sorted(set(doc) - MANIFEST_KEYS)}")
    if doc.get("type") != "stdio":
        problems.append("type must be stdio")
    if doc.get("allowed_origins") != [origin]:
        problems.append("allowed_origins must be exactly [the extension origin]")
    if doc.get("path") != str(exe):
        problems.append("path is not the exact production executable")
    if not isinstance(doc.get("name"), str) or not doc["name"].startswith(NAME_PREFIX) or not HOST_NAME_RE.match(doc["name"]):
        problems.append("name is not an acceptance host name")
    return problems


def check_executable(exe, family):
    exe = Path(exe)
    if exe.is_symlink() or not exe.is_file():
        raise Refused(f"{exe} is not a regular, non-symlink file")
    expected = "veles-native-host.exe" if family == "windows" else "veles-native-host"
    if exe.name.casefold() != expected.casefold():
        raise Refused(f"{exe.name!r} is not {expected} (the production host for {family})")
    return exe


def write_exclusive(path, data):
    """Create `path` with `data`; never overwrites; durable before returning."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


# ---- macOS: manifest inside the disposable profile only -----------------------------------------------------------
def default_chrome_dirs(home):
    """Personal/global Chrome user-data locations that must never be used as a profile."""
    home = str(home).rstrip("/\\")
    return [f"{home}/Library/Application Support/Google/Chrome", f"{home}/Library/Application Support/Google/Chrome Beta",
            f"{home}/Library/Application Support/Google/Chrome Canary", f"{home}/Library/Application Support/Chromium",
            "/Library/Google/Chrome",
            f"{home}/.config/google-chrome", f"{home}/.config/chromium", f"{home}/AppData/Local/Google/Chrome/User Data"]


def native_messaging_dirs(home, system_root="/"):
    """EXACTLY the Native Messaging manifest directories Chrome reads outside a profile (macOS/Linux). Only these
    are snapshotted - never the user-data roots, so unrelated personal files (Local State, caches...) are not read."""
    home = str(home).rstrip("/")
    root = str(system_root).rstrip("/")
    return [f"{home}/Library/Application Support/Google/Chrome/NativeMessagingHosts",
            f"{root}/Library/Google/Chrome/NativeMessagingHosts",
            f"{home}/.config/google-chrome/NativeMessagingHosts", f"{home}/.config/chromium/NativeMessagingHosts",
            f"{root}/etc/opt/chrome/native-messaging-hosts"]


def ensure_disposable_profile(profile, run_root, home):
    """The profile must be an existing EMPTY directory strictly inside this run's own root, not a symlink, and
    not (inside) any personal/global Chrome directory."""
    profile, run_root = Path(os.path.abspath(profile)), Path(os.path.abspath(run_root))
    if profile.is_symlink() or not profile.is_dir():
        raise Refused(f"{profile} is not a real directory")
    if run_root not in profile.parents:
        raise Refused(f"{profile} is not inside this run's root {run_root}")
    if any(profile.iterdir()):
        raise Refused(f"{profile} is not empty: only a fresh profile is allowed")
    real = os.path.realpath(profile)
    for d in default_chrome_dirs(home):
        d = os.path.realpath(d)
        if real == d or real.startswith(d + os.sep) or d.startswith(real + os.sep):
            raise Refused(f"{profile} overlaps the personal/global Chrome directory {d}")
    return profile


class ProfileManifestRegistration:
    """macOS/Linux: <profile>/NativeMessagingHosts/<name>.json, removed with the disposable profile.

    `register()` only snapshots the GLOBAL locations; the manifest is written by `place(profile)`, which
    ChromeSession calls right after creating the fresh profile and before launching Chrome.
    """

    def __init__(self, name, manifest, snapshot_globals):
        self.name = name
        self.manifest = manifest
        self.manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode()
        self.snapshot_globals = snapshot_globals  # callable -> JSON-able description of the GLOBAL locations
        self.before = None
        self.registered = False
        self.path = None

    def register(self):
        self.before = self.snapshot_globals()
        self.registered = True
        return {"scope": "disposable_profile_only", **describe_snapshot(self.before)}

    def place(self, profile):
        directory = Path(profile) / "NativeMessagingHosts"
        directory.mkdir(mode=0o700)
        self.path = directory / f"{self.name}.json"
        write_exclusive(self.path, self.manifest_bytes)
        return str(self.path)

    recovery_hint = ""  # nothing global exists to recover on macOS

    def unregister(self):
        """Nothing global was touched; prove it by comparing the global snapshot (names only on difference)."""
        after = self.snapshot_globals()
        if after != self.before:
            raise RestoreFailed("a global Native Messaging location changed during the run: " + "; ".join(diff_snapshots(self.before, after)))
        return {"globals_unchanged": True, **describe_snapshot(after)}


def _scan(root, base, out, limit=5000):
    for e in sorted(os.scandir(root), key=lambda e: e.name):
        rel = f"{base}/{e.name}" if base else e.name
        if len(out) > limit:
            out["<truncated>"] = "too many entries"
            return
        if e.is_symlink():
            out[rel] = "symlink->" + os.readlink(e.path)
        elif e.is_dir(follow_symlinks=False):
            out[rel + "/"] = "dir"
            _scan(e.path, rel, out, limit)
        elif e.is_file(follow_symlinks=False):
            try:
                out[rel] = sha256_bytes(Path(e.path).read_bytes())
            except OSError as err:
                out[rel] = f"unreadable:{type(err).__name__}"
        else:
            out[rel] = "special"


def snapshot_native_hosts(dirs):
    """dir -> {relative path: sha256 | 'dir' | 'symlink->..' | 'special' | 'unreadable:..'}, RECURSIVE; a missing
    directory is None. Read only. An unreadable directory is recorded, so it can only compare equal to itself."""
    out = {}
    for d in dirs:
        p = Path(d)
        if p.is_symlink():
            out[str(d)] = {"<dir-is-symlink>": os.readlink(p)}
        elif not p.is_dir():
            out[str(d)] = None
        else:
            entries = {}
            try:
                _scan(p, "", entries)
            except OSError as err:
                entries = {"<unreadable>": type(err).__name__}
            out[str(d)] = entries
    return out


def diff_snapshots(before, after):
    """Short human description of which entries differ (names only; never contents)."""
    notes = []
    for d in sorted(set(before) | set(after)):
        b, a = before.get(d), after.get(d)
        if b == a:
            continue
        if b is None or a is None:
            notes.append(f"{d}: directory {'appeared' if b is None else 'disappeared'}")
            continue
        for name in sorted(set(b) | set(a)):
            if b.get(name) != a.get(name):
                notes.append(f"{d}/{name}: {'added' if name not in b else 'removed' if name not in a else 'changed'}")
    return notes[:20] or ["differs"]


def describe_snapshot(snapshot):
    """What goes into evidence: directories checked, entry counts and one digest - not the personal listing."""
    digest = sha256_bytes(json.dumps(snapshot, sort_keys=True).encode())
    return {"dirs_checked": sorted(snapshot), "entry_counts": {d: (None if v is None else len(v)) for d, v in sorted(snapshot.items())},
            "snapshot_sha256": digest}


# ---- Windows: one unique per-user key, journalled, verified restore --------------------------------------------------
class RestoreFailed(Exception):
    pass


class WinregBackend:
    """HKCU-only registry access (no machine-wide hive is ever opened; never writes outside WIN_PARENT\\<name>). Windows only.

    Only "not found" is mapped to absence; any other OSError (access denied, ...) propagates to the callers, which
    turn it into a Refused/RestoreFailed - an unreadable registry is never read as an empty one.
    """

    def __init__(self):
        if sys.platform != "win32":
            raise Blocked("the Windows registry backend runs only on Windows")
        import winreg  # noqa: PLC0415
        self.w = winreg
        self.access = winreg.KEY_WOW64_64KEY

    def _open(self, sub, rights):
        return self.w.OpenKey(self.w.HKEY_CURRENT_USER, sub, 0, rights | self.access)

    def subkeys(self, parent):
        try:
            with self._open(parent, self.w.KEY_READ) as k:
                return sorted(self.w.EnumKey(k, i) for i in range(self.w.QueryInfoKey(k)[0]))
        except FileNotFoundError:
            return []

    def exists(self, key):
        try:
            self._open(key, self.w.KEY_READ).Close()
            return True
        except FileNotFoundError:
            return False

    def create(self, key, default):
        with self.w.CreateKeyEx(self.w.HKEY_CURRENT_USER, key, 0, self.w.KEY_SET_VALUE | self.access) as k:
            self.w.SetValueEx(k, "", 0, self.w.REG_SZ, default)

    def get_default(self, key):
        """The default value, or None when the key exists without one."""
        with self._open(key, self.w.KEY_READ) as k:
            try:
                return self.w.QueryValueEx(k, "")[0]
            except FileNotFoundError:
                return None

    def delete(self, key):
        self.w.DeleteKeyEx(self.w.HKEY_CURRENT_USER, key, self.access, 0)


# ---- fixed per-user state: journal + single-run lock ---------------------------------------------------------------------
def state_dir_for(env):
    """Fixed per-user directory (Windows: %LOCALAPPDATA%\\Veles\\Otp24Acceptance). The journal and the lock live here,
    never in a per-run temp dir, so a crashed run can always be found and recovered."""
    base = (env or {}).get("LOCALAPPDATA", "")
    if not base or not _abs_ok(base, "windows"):
        raise Blocked("%LOCALAPPDATA% is not set to an absolute drive path; no fixed per-user state location")
    return Path(base) / "Veles" / "Otp24Acceptance"


def ensure_state_dir(path):
    """Create (0700) or validate the state directory: a real directory, not a symlink, ours and private on POSIX."""
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise Refused(f"{path} is not a real directory")
    if hasattr(os, "getuid"):
        st = path.lstat()
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise Refused(f"{path} is not private to this user")
    return path


class RunLock:
    """Single-run lock: O_EXCL create of run.lock. A held (or crash-leftover) lock refuses the run; clearing it is
    an explicit recovery step, never automatic."""

    def __init__(self, state_dir, token=None, pid=None, now=None):
        self.path = Path(state_dir) / LOCK_NAME
        self.token = token or os.urandom(8).hex()
        self.pid = os.getpid() if pid is None else pid
        self.now = now or (lambda: __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()))
        self.held = False

    def acquire(self):
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except FileExistsError:
            try:
                holder = self.path.read_text()[:200]
            except OSError:
                holder = "unreadable"
            raise Refused(f"run lock {self.path} exists (held by {holder!r}): another run is active or crashed; "
                          "use --recover-registration --confirm-no-active-run")
        try:
            os.write(fd, json.dumps({"pid": self.pid, "token": self.token, "at": self.now()}).encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        self.held = True

    def release(self):
        """Remove the lock only if it is still ours."""
        if not self.held:
            return
        try:
            if json.loads(self.path.read_text()).get("token") == self.token:
                self.path.unlink()
        except (OSError, ValueError):
            pass
        self.held = False


def startup_check(backend, state_dir):
    """Before ANY mutation: refuse when a journal exists or any acceptance-prefixed key is present. Nothing is
    deleted here; recovery is a separate, explicit step."""
    journal = Path(state_dir) / JOURNAL_NAME
    if journal.exists():
        raise Refused(f"{journal} exists: a previous run was not recovered (use --recover-registration --confirm-no-active-run)")
    try:
        stale = [k for k in backend.subkeys(WIN_PARENT) if k.startswith(NAME_PREFIX)]
    except OSError as e:
        raise Refused(f"cannot read the Native Messaging registry key: {type(e).__name__}")
    if stale:
        raise Refused(f"leftover acceptance key(s) under HKCU\\{WIN_PARENT}: {stale}; recover or remove them deliberately before a new run")


def fsync_dir(path):
    """Make the journal rename durable on POSIX; on Windows os.replace is MoveFileEx without write-through, so a
    power loss can still lose the journal (documented limitation, M-7)."""
    if sys.platform == "win32" or not hasattr(os, "O_DIRECTORY"):
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class WindowsRegistration:
    """Journalled single-key registration. `registered` becomes True the instant the journal is durable - i.e. as soon
    as ANYTHING of ours may exist - and stays True through every later failure, so cleanup always runs."""

    recovery_hint = "run lifecycle-driver.py --recover-registration --confirm-no-active-run"

    def __init__(self, backend, journal_path, name, manifest_path, manifest_bytes=b"", work_dir=None):
        self.manifest_bytes = manifest_bytes
        if not ACCEPT_NAME_RE.match(name or ""):
            raise Refused("only me.nagaev.veles.otp24.acceptance.<12 hex> host names may be registered")
        self.b, self.journal, self.name = backend, Path(journal_path), name
        self.manifest_path = str(manifest_path)
        self.work_dir = str(work_dir) if work_dir else None
        self.key = f"{WIN_PARENT}\\{name}"
        self.before = None
        self.registered = False
        self.observed = None

    def _write_journal(self, state):
        doc = {"schema": JOURNAL_SCHEMA, "state": state, "key": self.key, "name": self.name, "manifest_path": self.manifest_path,
               "observed_value": self.observed, "work_dir": self.work_dir, "subkeys_before": self.before}
        tmp = self.journal.with_name(self.journal.name + ".tmp")
        if tmp.exists():
            tmp.unlink()
        write_exclusive(tmp, (json.dumps(doc, indent=2) + "\n").encode())
        os.replace(tmp, self.journal)
        fsync_dir(self.journal.parent)

    def register(self):
        try:
            if self.b.exists(self.key):
                raise Refused(f"{self.key} already exists; an existing registration is never overwritten")
            if self.journal.exists():
                raise Refused(f"{self.journal} exists: a previous run was not recovered ({self.recovery_hint})")
            self.before = self.b.subkeys(WIN_PARENT)
        except OSError as e:
            raise Refused(f"registry not readable before registering: {type(e).__name__}")
        self._write_journal("pending")  # durable BEFORE the first mutation
        self.registered = True  # from here on something of ours may exist: cleanup is mandatory
        try:
            self.b.create(self.key, self.manifest_path)
            value = self.b.get_default(self.key)
        except OSError as e:
            raise Refused(f"registry write/read-back failed: {type(e).__name__}")
        if value != self.manifest_path:
            self.observed = value
            self._write_journal("mismatch")  # remember what we created so cleanup may delete exactly it
            raise Refused("registry read-back differs from the written manifest path")
        self._write_journal("registered")
        return {"scope": "hkcu_unique_key", "key": self.key, "manifest_path": self.manifest_path, "subkeys_before": self.before}

    def unregister(self):
        return restore(self.b, self.journal)


def restore(backend, journal_path):
    """Remove exactly the journalled key, verify it is gone, then retire the journal. Any doubt raises and KEEPS the
    journal. Success means OUR key is absent; unrelated changes to the parent since the snapshot are REPORTED, not
    required to be equal (they are never touched), so an unrelated install cannot wedge recovery."""
    journal_path = Path(journal_path)
    try:
        doc = json.loads(journal_path.read_text())
    except (OSError, ValueError) as e:
        raise RestoreFailed(f"unreadable registration journal {journal_path}: {e}")
    if not isinstance(doc, dict) or doc.get("schema") != JOURNAL_SCHEMA:
        raise RestoreFailed("journal schema mismatch")
    key, name = doc.get("key"), doc.get("name")
    if not (isinstance(name, str) and ACCEPT_NAME_RE.match(name) and key == f"{WIN_PARENT}\\{name}"):
        raise RestoreFailed("journal key is not an acceptance key (exact name pattern) under the Chrome NativeMessagingHosts parent")
    before = doc.get("subkeys_before")
    if not isinstance(before, list):
        raise RestoreFailed("journal has no pre-run snapshot")
    if name in before:
        raise RestoreFailed("journal says the key pre-existed; refusing to delete it")
    state = doc.get("state")
    ours = {doc.get("manifest_path")}
    if state in ("pending", "mismatch"):  # we created it moments ago under the lock; a half-written/odd value is ours
        ours |= {doc.get("observed_value"), None}
    try:
        if backend.exists(key):
            if backend.get_default(key) not in ours:
                raise RestoreFailed(f"{key} no longer holds a value we wrote; not deleting a value we do not own")
            backend.delete(key)
        after = backend.subkeys(WIN_PARENT)
        gone = not backend.exists(key)
    except OSError as e:
        raise RestoreFailed(f"registry access failed during restore: {type(e).__name__}")
    if not gone or name in after:
        raise RestoreFailed(f"{key} is still present after deletion")
    journal_path.unlink()
    other = {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after))}
    return {"restored": True, "key_removed": key, "work_dir": doc.get("work_dir"), "other_parent_changes_reported_not_touched": other}


def remove_work_dir(path, temp_root):
    """Delete a leftover run work dir only if it is a direct child of the temp root named veles-otp24-acc-*, not a link."""
    if not path:
        return False
    p = Path(path)
    if p.is_symlink() or not p.is_dir() or not p.name.startswith(WORK_PREFIX) or os.path.realpath(p.parent) != os.path.realpath(temp_root):
        return False
    import shutil  # noqa: PLC0415
    shutil.rmtree(p, ignore_errors=True)
    return True


def recover(backend, state_dir, temp_root, confirm_no_active_run=False):
    """Explicit crash recovery, scoped to the journalled key only. A lock-only leftover (crash before any mutation)
    is cleared when nothing acceptance-prefixed exists. Never deletes a key that has no journal."""
    if not confirm_no_active_run:
        raise Refused("recovery requires --confirm-no-active-run: a live run's key would be deleted")
    state_dir = Path(state_dir)
    journal, lock = state_dir / JOURNAL_NAME, state_dir / LOCK_NAME
    result = {"journal": False, "lock_cleared": False}
    if journal.exists():
        out = restore(backend, journal)
        result.update(journal=True, **out, work_dir_removed=remove_work_dir(out.get("work_dir"), temp_root))
    else:
        try:
            stale = [k for k in backend.subkeys(WIN_PARENT) if k.startswith(NAME_PREFIX)]
        except OSError as e:
            raise RestoreFailed(f"registry not readable: {type(e).__name__}")
        if stale:
            raise RestoreFailed(f"acceptance key(s) {stale} exist without a journal; not deleting keys that cannot be proven ours")
    if lock.exists():
        lock.unlink()
        result["lock_cleared"] = True
    return result
