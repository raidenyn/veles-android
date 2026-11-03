#!/usr/bin/env python3
"""Validate one OTP-24 physical-acceptance run directory (Task 10). Standard library only.

    python3 -I native-bridge/host/scripts/validate-evidence.py <run-dir> --binary <exe> [--repo ROOT] [--cases FILE]
    python3 -I native-bridge/host/scripts/validate-evidence.py <run-dir> --anchor anchor.json

Exit codes: 0 PASS, 1 FAIL, 2 BLOCKED, 4 NOT RUN, 3 usage.

WHAT A "PASS" FROM THIS TOOL MEANS - AND DOES NOT MEAN
------------------------------------------------------
PASS is a *structural and consistency* verdict on a run directory against (a) the case definitions and (b) an
EXTERNAL PROVENANCE ANCHOR. It is not physical acceptance. In particular:

* The anchor is computed OUTSIDE the run from trusted inputs: the actual checkout (HEAD revision plus a hash over
  every tracked AND untracked non-ignored file's bytes), the evaluator files as they exist in that checkout, and
  the actual executable passed with --binary. Everything the run claims about those must equal the anchor.
  Without an anchor the verdict can never be PASS (`provenance_unanchored`). The anchor is only as trustworthy
  as the checkout/binary it was computed from; a party who controls both the checkout and the evaluator can
  forge a consistent record. The binary-to-source link (`built_from_*`) is attested by whoever supplies the
  anchor, not rebuilt here.
* The adjudication record is SELF-ATTESTED: two different strings, a boolean and an opaque `review_ref`. Nothing
  here authenticates the adjudicator or proves independence. Physical acceptance additionally requires a
  separate, authenticated review (reviewer-controlled system, signed review) that this validator does not and
  cannot perform. The CLI prints this on every run; matrix rows may not go to PASS on this tool alone.
* Pending-request fate (`pending_request_handling`): for abrupt EOF triggers (port.disconnect, tab close, Chrome-owned pipe EOF)
  the per-request outcome is NOT observable (user decision, option 2) - the record must say "unknown" and may not claim host
  cancellation; a record asserting a per-request outcome there FAILS. Only explicit shutdown and per-phone failure require a
  proven-pending armed request with a correlated, non-synthesized host error specific to that trigger. A PASS therefore never
  means "the host cancelled the request" for an EOF case.
* Phone identity (`source_hash`) and clocks are recorded claims; they are cross-checked for internal consistency
  (receipts vs transfers vs phones) but not against the phones' own telemetry, which only a reviewer can compare.

Every path reference (run.json, SHA256SUMS, captures, test manifest, adjudication) goes through ONE routine:
relative, normalized, no traversal, no symlink at any component beneath the run directory, final target a
regular file, and the run directory listing must contain only regular files and directories.
Malformed types never crash the tool: each check is isolated and a crash/type error becomes a structured
`malformed_evidence` FAIL.
"""

import argparse
import errno
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = HOST_DIR.parents[1]
DEFAULT_CASES = HOST_DIR / "acceptance" / "cases.json"
SCHEMA = "veles-otp24-physical-evidence/1"
SCOPE = "windows_chrome_never_visible_wrapper_only"
CLOCK_SLACK_MS = 30_000  # BLE delivery + page scheduling between a phone event and the host receipt
EXIT = {"PASS": 0, "FAIL": 1, "BLOCKED": 2, "NOT RUN": 4}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
ORIGIN = re.compile(r"^chrome-extension://[a-p]{32}/$")
PRODUCTION_NAMES = {"veles-native-host", "veles-native-host.exe"}
CATALOG_EXCLUDED = {"run.json", "SHA256SUMS", "adjudication.json"}
EVALUATOR_FILES = ("scripts/physical-matrix.py", "scripts/validate-evidence.py", "acceptance/cases.json")
TREE_EXCLUDED_PREFIXES = (".superpowers/", "native-bridge/host/evidence/")
ATT_OVERHEAD, ATTRIBUTE_MAX, FRAG_HEADER, MIN_MTU, MAX_MTU = 3, 512, 4, 185, 517
MAX_JSON = 32 * 1024 * 1024
NOTICE = ("Structural/consistency verdict only. Adjudication is self-attested; physical acceptance additionally "
          "requires a separate authenticated review that this tool does not perform.")
# Contradictions and violations are FAIL; everything else is incomplete evidence (BLOCKED).
FAIL_CODES = {
    "binary_path_mismatch",
    "control_failed", "observation_violation", "transfer_mismatch", "rejection_wrote", "capture_hash_mismatch",
    "catalog_mismatch", "adjudication_mismatch", "adjudication_not_pass", "unexpected_exit_code", "action_failed",
    "production_identity_claimed", "binary_not_production", "binary_source_mismatch", "test_manifest_mismatch",
    "chrome_profile_not_disposable", "receipt_phone_mismatch", "phone_clock_outside_allowance", "duplicate_phone_source",
    "wrapper_exception_overbroad", "source_mismatch", "evaluator_mismatch", "binary_hash_mismatch", "unsafe_path",
    "receipt_digest_mismatch", "malformed_evidence",
    "pending_claim_forbidden", "pending_cancellation_unproven", "pending_handling_mode_mismatch",
}

Result = namedtuple("Result", "verdict defects")


# ---- one secure path routine ---------------------------------------------------------------------------------
class PathError(Exception):
    pass


# Race-resistant traversal needs openat-style directory fds with O_NOFOLLOW|O_DIRECTORY. Where the platform lacks
# them the validator refuses to accept anything (structured BLOCKED `unsupported_platform`) instead of falling back
# to a path-based walk that a concurrent writer could redirect.
SECURE_FS_SUPPORTED = (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY") and os.open in os.supports_dir_fd
                       and os.scandir in os.supports_fd)
_RACE_HOOK = None  # test seam only: called as hook(event, detail) between traversal steps


def _hook(event, detail):
    if _RACE_HOOK is not None:
        _RACE_HOOK(event, detail)


def split_rel(rel):
    """Validate a run-relative reference; returns its components. Rejects everything not plainly relative."""
    if not isinstance(rel, str) or not rel or "\0" in rel or "\\" in rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise PathError(f"not a plain relative path: {rel!r}")
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise PathError(f"non-normalized or traversing path: {rel!r}")
    return parts


_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)


def _open_root(root):
    if not SECURE_FS_SUPPORTED:
        raise PathError("platform lacks openat/O_NOFOLLOW/O_DIRECTORY")
    try:
        return os.open(str(root), _DIR_FLAGS)  # O_NOFOLLOW: the run directory itself may not be a symlink
    except OSError as e:
        raise PathError(f"run directory: {_why(e)}")


def _why(e):
    if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
        return "symlink or non-directory component"
    return type(e).__name__


def _open_child(dirfd, name, flags, what):
    try:
        return os.open(name, flags, dir_fd=dirfd)
    except OSError as e:
        raise PathError(f"{what}: {_why(e)} at {name!r}")


def secure_read(root, rel, limit=None):
    """Read bytes of root/rel by walking held directory fds: every step is openat(O_NOFOLLOW[|O_DIRECTORY]) relative to
    the previous verified fd, so replacing an already-checked (or not yet visited) ancestor with a symlink cannot
    redirect the read. The final fd must be a regular file."""
    parts = split_rel(rel)
    fd = _open_root(root)
    try:
        for i, part in enumerate(parts):
            _hook("before_open", (i, part))
            last = i == len(parts) - 1
            nxt = _open_child(fd, part, _FILE_FLAGS if last else _DIR_FLAGS, rel)
            os.close(fd)
            fd = nxt
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PathError(f"{rel}: not a regular file")
        if limit is not None and st.st_size > limit:
            raise PathError(f"{rel}: larger than {limit} bytes")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if limit is not None and total > limit:
                raise PathError(f"{rel}: larger than {limit} bytes")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def list_regular(root):
    """({rel: None}, problems): regular files under root found via directory fds (no path-based walk)."""
    files, problems = {}, []

    def walk(dirfd, prefix):
        with os.scandir(dirfd) as it:
            entries = sorted(it, key=lambda e: e.name)
        for e in entries:
            rel = prefix + e.name
            _hook("enumerate", rel)
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                problems.append(rel + " (vanished or unreadable)")
                continue
            if stat.S_ISLNK(st.st_mode):
                problems.append(rel + " (symlink)")
            elif stat.S_ISDIR(st.st_mode):
                try:
                    sub = os.open(e.name, _DIR_FLAGS, dir_fd=dirfd)
                except OSError:
                    problems.append(rel + " (directory replaced during enumeration)")
                    continue
                try:
                    walk(sub, rel + "/")
                finally:
                    os.close(sub)
            elif stat.S_ISREG(st.st_mode):
                files[rel] = None
            else:
                problems.append(rel + " (not a regular file)")

    fd = _open_root(root)
    try:
        walk(fd, "")
    finally:
        os.close(fd)
    return files, problems


def parse_json_strict(data):
    def no_dupes(pairs):
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate key")
        return dict(pairs)

    def no_const(name):
        raise ValueError("non-finite number " + name)

    return json.loads(data.decode("utf-8"), object_pairs_hook=no_dupes, parse_constant=no_const)


# ---- external provenance anchor ----------------------------------------------------------------------------------
def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


class AnchorError(Exception):
    pass


def norm_path(path, family):
    """Canonical absolute path for `family` ('posix' | 'windows') or None. Fail closed: relative, UNC/device, `.`/`..`/empty
    components, NUL, backslashes in POSIX paths and reserved Windows characters are all unverifiable."""
    if not isinstance(path, str) or "\0" in path or not path:
        return None
    if family == "posix":
        parts = path.split("/")
        if not path.startswith("/") or "\\" in path or len(parts) < 2 or any(p in ("", ".", "..") for p in parts[1:]):
            return None
        return path
    if family == "windows":
        text = path.replace("/", "\\")
        m = re.match(r"^([A-Za-z]):\\(.+)$", text)
        if not m:
            return None
        parts = m.group(2).split("\\")
        if any(p in ("", ".", "..") or re.search(r'[<>:"|?*]', p) or p.endswith((".", " ")) for p in parts):
            return None
        return m.group(1).upper() + ":\\" + "\\".join(parts)
    return None


def same_path(a, b, family):
    na, nb = norm_path(a, family), norm_path(b, family)
    return na is not None and nb is not None and (na.casefold() == nb.casefold() if family == "windows" else na == nb)


def source_tree_sha256(repo):
    """Hash over every tracked and untracked non-ignored file's BYTES, excluding evidence/state dirs. Symlinks and special
    files in the tree are REJECTED (AnchorError): their referents are outside what this hash can bind, and there are none
    in the repository, so no provenance is faked by hashing only the link text."""
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=repo,
                         capture_output=True, timeout=120, check=True).stdout
    lines = []
    for raw in sorted(set(p for p in out.split(b"\0") if p)):
        rel = raw.decode("utf-8", "surrogateescape")
        if rel.startswith(TREE_EXCLUDED_PREFIXES):
            continue
        full = os.path.join(repo, rel)
        try:
            st = os.lstat(full)
        except OSError:
            continue  # tracked file deleted in the working tree
        if stat.S_ISLNK(st.st_mode):
            raise AnchorError(f"source tree contains a symlink ({rel}); referent bytes are not bound - refusing to anchor")
        if not stat.S_ISREG(st.st_mode):
            raise AnchorError(f"source tree contains a non-regular file ({rel}) - refusing to anchor")
        lines.append(f"{sha256_bytes(Path(full).read_bytes())}  {rel}\n")
    return sha256_bytes("".join(lines).encode("utf-8", "surrogateescape"))


def compute_anchor(repo=REPO_ROOT, binary=None):
    """Trusted identity facts taken from the real checkout and the real executable, never from a run."""
    repo = str(repo)
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, timeout=60, check=True).stdout.decode().strip()
    host = Path(repo) / "native-bridge" / "host"
    anchor = {
        "source_revision": rev,
        "source_tree_sha256": source_tree_sha256(repo),
        "evaluator_files": {rel: sha256_bytes((host / rel).read_bytes()) for rel in EVALUATOR_FILES},
        "binary_sha256": None, "binary_path": None, "binary_path_platform": None, "binary_size": None,
    }
    if binary:
        family = "windows" if os.name == "nt" else "posix"
        canonical = norm_path(str(binary), family)
        if canonical is None:
            raise AnchorError("--binary must be a normalized absolute path (no '.', '..', UNC or relative components)")
        if os.path.realpath(canonical) != canonical and family == "posix":
            raise AnchorError("--binary path contains a symlink; give the real path of the executable")
        st = os.lstat(canonical)
        if not stat.S_ISREG(st.st_mode):
            raise AnchorError("--binary is not a regular file")
        data = Path(canonical).read_bytes()
        anchor.update(binary_sha256=sha256_bytes(data), binary_path=canonical, binary_path_platform=family, binary_size=len(data))
    return anchor


# ---- small type-safe helpers ----------------------------------------------------------------------------------------
def D(x):
    return x if isinstance(x, dict) else {}


def L(x):
    return x if isinstance(x, list) else []


def is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def parse_ts(value):
    if not isinstance(value, str) or not TS.match(value):
        return None
    try:
        return datetime.strptime(value.rstrip("Z").split(".")[0], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def nonempty(value):
    return isinstance(value, str) and value.strip() != ""


def hex64(value):
    return isinstance(value, str) and bool(HEX64.match(value))


def payload_budget(capacity):
    """(payload, problem). Mirrors fragment.rs LinkBudget: min(M-3,512)-4; write limit W only determines it when W>=512."""
    cap = D(capacity)
    kind, value = cap.get("kind"), cap.get("value")
    if not is_int(value):
        return None, "capacity value missing or not an integer"
    if kind == "att_mtu":
        if not MIN_MTU <= value <= MAX_MTU:
            return None, f"ATT MTU {value} outside {MIN_MTU}..{MAX_MTU} (link must be refused)"
        return min(value - ATT_OVERHEAD, ATTRIBUTE_MAX) - FRAG_HEADER, None
    if kind == "max_write_without_response":
        if value < ATTRIBUTE_MAX:
            return None, f"write limit {value} < {ATTRIBUTE_MAX}: common budget undetermined (link must be refused)"
        return ATTRIBUTE_MAX - FRAG_HEADER, None
    return None, f"unknown capacity kind {kind!r}"


def resolve_size(entry, payload):
    """Numeric record size for a case `sizes` entry (int or chunk label) on a link with this payload budget."""
    if is_int(entry):
        return entry
    return {"chunk-1": payload - 1, "chunk": payload, "chunk+1": payload + 1, "multiple-of-chunk": 2 * payload}[entry]


def size_label(entry):
    return entry if isinstance(entry, str) else str(entry)


class Checker:
    def __init__(self, run_dir, manifest, case, anchor, cases_path):
        self.run = run_dir
        self.m = manifest
        self.case = case
        self.anchor = anchor
        self.cases_path = cases_path
        self.defects = []

    def bad(self, code, message):
        self.defects.append((code, message))

    def get(self, *path):
        node = self.m
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        return node

    def capture_names(self):
        return {c.get("name") for c in L(self.m.get("captures")) if isinstance(c, dict)}

    def read(self, rel, what):
        try:
            return secure_read(self.run, rel, MAX_JSON * 4)
        except PathError as e:
            self.bad("unsafe_path", f"{what}: {e}")
            return None

    # ---- external provenance ------------------------------------------------------------------------------------
    def check_provenance(self):
        a = self.anchor
        need = ("source_revision", "source_tree_sha256", "evaluator_files", "binary_sha256", "binary_path", "binary_path_platform", "binary_size")
        self.anchor_path = None
        self.path_unverifiable = False
        if not isinstance(a, dict) or not (HEX40.match(str(a.get("source_revision"))) and hex64(a.get("source_tree_sha256"))
                                           and isinstance(a.get("evaluator_files"), dict) and hex64(a.get("binary_sha256"))
                                           and a.get("binary_path_platform") in ("posix", "windows") and is_int(a.get("binary_size"))
                                           and norm_path(a.get("binary_path"), a.get("binary_path_platform")) is not None):
            self.bad("provenance_unanchored", "no external anchor (checkout revision + tree hash, evaluator hashes, actual binary hash): "
                                              f"{need} are required and come from outside the run")
            return
        source, ev, b = D(self.get("source")), D(self.get("evaluator")), D(self.get("binary"))
        if not isinstance(source.get("revision"), str) or not HEX40.match(source["revision"]):
            self.bad("missing_source_revision", "source.revision must be a full 40-hex commit")
        elif source["revision"] != a["source_revision"]:
            self.bad("source_mismatch", "source.revision differs from the anchored checkout revision")
        if not hex64(source.get("tree_sha256")):
            self.bad("source_tree_unbound", "source.tree_sha256 (tracked + untracked bytes) required")
        elif source["tree_sha256"] != a["source_tree_sha256"]:
            self.bad("source_mismatch", "source tree hash differs from the anchored tree (includes untracked bytes)")
        files = ev.get("files")
        if not isinstance(ev.get("revision"), str) or not HEX40.match(ev["revision"]) or not (
                isinstance(files, dict) and files and all(hex64(v) for v in files.values())):
            self.bad("missing_evaluator_revision", "evaluator.revision (40-hex) and per-file sha256 values are required")
        elif ev["revision"] != a["source_revision"] or files != a["evaluator_files"]:
            self.bad("evaluator_mismatch", "evaluator revision/file hashes differ from the anchored evaluator files")
        if set(a["evaluator_files"]) != set(EVALUATOR_FILES):
            self.bad("evaluator_mismatch", f"anchor must cover exactly {EVALUATOR_FILES}")
        try:
            cases_hash = sha256_bytes(secure_read(self.cases_path.parent, self.cases_path.name))
        except PathError:
            cases_hash = None
        if a["evaluator_files"].get("acceptance/cases.json") != cases_hash:
            self.bad("evaluator_mismatch", "case definitions used for validation are not the anchored acceptance/cases.json")
        if not hex64(b.get("sha256")):
            self.bad("missing_binary_hash", "binary.sha256 missing or malformed")
        elif b["sha256"] != a["binary_sha256"]:
            self.bad("binary_hash_mismatch", "binary.sha256 differs from the hash of the actual executable")
        family = "windows" if self.case["platform"] == "windows" else "posix"
        if a["binary_path_platform"] != family:
            self.path_unverifiable = True
            self.bad("binary_path_unverifiable", f"anchor path is {a['binary_path_platform']}-style but the case runs on {self.case['platform']}: "
                                                 "the executable path cannot be verified from here (fail closed)")
        else:
            self.anchor_path = norm_path(a["binary_path"], family)
            if not same_path(b.get("path"), a["binary_path"], family):
                self.bad("binary_path_mismatch", "binary.path is not the normalized absolute path of the anchored --binary executable")
            if b.get("size") != a["binary_size"]:
                self.bad("binary_hash_mismatch", "binary.size differs from the actual executable")
        if b.get("built_from_source_revision") != a["source_revision"] or b.get("built_from_tree_sha256") != a["source_tree_sha256"]:
            self.bad("binary_source_mismatch", "binary must be attested as built from the anchored revision AND tree")

    def check_binary(self):
        b = D(self.get("binary"))
        name = b.get("name")
        if b.get("kind") != "production" or name not in PRODUCTION_NAMES:
            self.bad("binary_not_production", f"binary must be the production veles-native-host, not {name!r}/{b.get('kind')!r}")
        elif (self.case["platform"] == "windows") != (name == "veles-native-host.exe"):
            self.bad("binary_not_production", f"binary name {name!r} does not match platform {self.case['platform']}")
        if self.case["requires_test_manifest"]:
            self.check_test_manifest(b)

    def check_test_manifest(self, b):
        tm = self.get("test_manifest")
        if not isinstance(tm, dict):
            self.bad("test_manifest_mismatch", "Chrome case needs a test_manifest block")
            return
        if tm.get("host_sha256") != b.get("sha256") or not hex64(tm.get("host_sha256")):
            self.bad("test_manifest_mismatch", "test_manifest.host_sha256 must equal the production executable hash")
        if tm.get("scope") != "test_only_not_production_registration":
            self.bad("test_manifest_mismatch", "test manifest must be labelled test-only, not a production registration")
        chrome_origin = self.get("chrome", "extension_origin")
        if not isinstance(tm.get("allowed_origin"), str) or not ORIGIN.match(tm["allowed_origin"]):
            self.bad("test_manifest_mismatch", "run.json test_manifest.allowed_origin must be one exact chrome-extension origin")
        elif tm["allowed_origin"] != chrome_origin:
            self.bad("test_manifest_mismatch", "test manifest origin differs from chrome.extension_origin")
        # The ACTUAL manifest file: parsed and bound, not merely hashed.
        data = self.read(tm.get("path"), "test manifest")
        if data is None:
            return
        if sha256_bytes(data) != tm.get("sha256"):
            self.bad("capture_hash_mismatch", "test manifest file hash does not match run.json")
        try:
            doc = parse_json_strict(data)
        except ValueError:
            self.bad("test_manifest_mismatch", "actual test manifest is not valid strict JSON")
            return
        doc = D(doc)
        origins = doc.get("allowed_origins")
        path = doc.get("path")
        problems = []
        if doc.get("type") != "stdio":
            problems.append("type must be 'stdio'")
        if not (isinstance(origins, list) and len(origins) == 1 and isinstance(origins[0], str) and ORIGIN.match(origins[0])):
            problems.append("allowed_origins must be exactly one exact chrome-extension origin (no wildcard/extra)")
        elif origins[0] != chrome_origin or origins[0] != tm.get("allowed_origin"):
            problems.append("allowed_origins[0] differs from the Chrome extension origin recorded for the run")
        family = "windows" if self.case["platform"] == "windows" else "posix"
        if not same_path(path, b.get("path"), family) or norm_path(path, family) is None or (
                (lambda base, name: base.casefold() != str(name).casefold() if family == "windows" else base != name)(
                    norm_path(path, family).replace("\\", "/").rsplit("/", 1)[-1], b.get("name"))):
            problems.append("path must be a normalized absolute path equal to the production executable path recorded for the run")
        elif self.anchor_path is not None and not same_path(path, self.anchor_path, family):
            problems.append("path is not bound to the actual anchored --binary executable path")
        if not isinstance(doc.get("name"), str) or not re.match(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$", doc["name"]):
            problems.append("name must be a valid native messaging host name")
        extra = set(doc) - {"name", "description", "path", "type", "allowed_origins"}
        if extra:
            problems.append(f"unexpected keys {sorted(extra)}")
        for p in problems:
            self.bad("test_manifest_mismatch", "actual manifest: " + p)

    def check_context(self):
        c = self.case
        signing = self.get("signing")
        if not isinstance(signing, dict):
            self.bad("missing_signing", "signing block required")
        elif c["platform"] == "macos":
            ok = all(nonempty(signing.get(k)) for k in ("identity", "team_id", "bundle_id")) and hex64(signing.get("cert_sha256"))
            if not ok or signing.get("identity") in ("-", "adhoc", "ad-hoc") or signing.get("required") is not True:
                self.bad("missing_signing", "macOS needs the approved signing identity, team, bundle id and certificate hash (no ad-hoc)")
            if signing.get("production_identity_claimed") is not False or signing.get("bundle_kind") != "test_vehicle":
                self.bad("production_identity_claimed", "bundle must be a labelled test vehicle; final production identity/TCC is deferred")
        elif not nonempty(signing.get("authenticode")):
            self.bad("missing_signing", "Windows must state the Authenticode status of the exact executable")
        os_ = D(self.get("os"))
        if not all(nonempty(os_.get(k)) for k in ("name", "version", "build", "arch")) or os_.get("name") != c["platform"]:
            self.bad("missing_os_context", "os.name (matching the case), version, build and arch are required")
        for key in ("adapter", "account"):
            if not hex64(D(self.get(key)).get("id_sha256")):
                self.bad("missing_os_context", f"{key} context with id_sha256 required")
        if c["requires_chrome"]:
            ch = self.get("chrome")
            prov = D(ch).get("provenance")
            chd = D(ch)
            if not all(nonempty(chd.get(k)) for k in ("version", "channel", "path", "extension_id")) or not (
                    hex64(chd.get("sha256")) and hex64(chd.get("launch_args_sha256"))):
                self.bad("missing_chrome_provenance", "chrome version/channel/path/hash/launch-args/extension id required")
            elif not isinstance(prov, dict) or not nonempty(prov.get("source")) or parse_ts(prov.get("recorded_at")) is None or (
                    prov.get("capture_ref") not in self.capture_names()):
                self.bad("missing_chrome_provenance", "chrome.provenance needs source, recorded_at and an existing capture_ref")
            if chd:
                profile = D(chd.get("profile"))
                if profile.get("disposable") is not True or profile.get("is_default_profile") is not False:
                    self.bad("chrome_profile_not_disposable", "Chrome must run on a disposable profile, never a personal one")

    # ---- phones, links, transfers ---------------------------------------------------------------------------------
    def check_phones(self):
        c = self.case
        need = [] if c["phones"] == "none" else list(c["phones"])
        by_slot = {p.get("slot"): p for p in L(self.get("phones")) if isinstance(p, dict)}
        for slot in need:
            p = D(by_slot.get(slot))
            if not (hex64(p.get("source_hash")) and hex64(p.get("apk_sha256")) and hex64(p.get("apk_cert_sha256"))
                    and nonempty(p.get("clock_source")) and nonempty(p.get("fixture_version"))
                    and is_num(p.get("clock_offset_ms")) and is_num(p.get("clock_uncertainty_ms")) and p["clock_uncertainty_ms"] >= 0):
                self.bad("missing_phone_binding", f"phone slot {slot}: source_hash, apk and certificate hashes, fixture version and numeric clock offset/uncertainty/source required")
        sources = [D(by_slot.get(s)).get("source_hash") for s in need]
        if len(set(map(str, sources))) != len(sources):
            self.bad("duplicate_phone_source", "distinct slots must have distinct phone sources")
        if need and c.get("sizes"):
            self.check_transfers(by_slot, need)

    def link_payload(self, slot):
        link = next((l for l in L(self.get("links")) if isinstance(l, dict) and l.get("slot") == slot), None)
        if link is None or link.get("capture_ref") not in self.capture_names():
            self.bad("link_capacity_unresolved", f"slot {slot}: negotiated link data with a capture_ref is required to resolve chunk boundaries")
            return None
        payload, problem = payload_budget(link.get("capacity"))
        if problem:
            self.bad("link_capacity_unresolved", f"slot {slot}: {problem}")
            return None
        if link.get("payload_budget") != payload:
            self.bad("transfer_mismatch", f"slot {slot}: recorded payload_budget {link.get('payload_budget')!r} != derived {payload}")
        return payload

    def check_transfers(self, by_slot, need):
        sizes = D(self.case["sizes"])
        transfers = {}
        for t in L(self.get("transfers")):
            if isinstance(t, dict):
                transfers[(t.get("slot"), t.get("label"))] = t
        receipts = [r for r in L(self.get("phone_receipts")) if isinstance(r, dict)]
        for slot in need:
            payload = self.link_payload(slot)
            expected_digest = {}
            for entry in sizes["accept"]:
                label = size_label(entry)
                t = D(transfers.get((slot, label)))
                if payload is None:
                    continue
                want = resolve_size(entry, payload)
                if not (is_int(t.get("size")) and t["size"] == want and 1 <= want <= 4096 and t.get("chunk") == payload
                        and hex64(t.get("expected_sha256")) and t.get("expected_sha256") == t.get("received_sha256")
                        and t.get("outcome") == "ok"):
                    self.bad("transfer_mismatch", f"phone {slot} {label}: numeric size {want} (chunk {payload}) with byte-exact digest match required")
                else:
                    expected_digest[want] = t["expected_sha256"]
            p = D(by_slot.get(slot))
            mine = [r for r in receipts if r.get("slot") == slot]
            if not mine:
                self.bad("missing_phone_receipts", f"phone {slot}: no receipts")
                continue
            seen = set()
            for r in mine:
                if r.get("source_hash") != p.get("source_hash"):
                    self.bad("receipt_phone_mismatch", f"receipt for slot {slot} is not bound to that phone's source")
                size = r.get("size")
                if payload is not None and expected_digest.get(size) != r.get("sha256"):
                    self.bad("receipt_digest_mismatch", f"slot {slot} size {size!r}: receipt digest is not the transfer's expected digest")
                seen.add(size)
                pt, ht = parse_ts(r.get("phone_time")), parse_ts(r.get("host_time"))
                if pt is None or ht is None:
                    self.bad("bad_timestamp", f"receipt for slot {slot} lacks phone/host time")
                elif is_num(p.get("clock_offset_ms")) and is_num(p.get("clock_uncertainty_ms")):
                    skew = abs((pt - ht).total_seconds() * 1000 - p["clock_offset_ms"])
                    if skew > p["clock_uncertainty_ms"] + CLOCK_SLACK_MS:
                        self.bad("phone_clock_outside_allowance", f"slot {slot}: phone/host skew {skew:.0f} ms outside the stated allowance")
            if payload is not None and not set(expected_digest) <= seen:
                self.bad("missing_phone_receipts", f"phone {slot}: receipts missing for sizes {sorted(set(expected_digest) - seen)}")
        rej = {r.get("size"): r for r in L(self.get("rejections")) if isinstance(r, dict)}
        for size in sizes["reject_without_writes"]:
            r = D(rej.get(size))
            if not (r.get("gatt_writes") == 0 and is_int(r.get("gatt_writes")) and nonempty(r.get("error_code"))):
                self.bad("rejection_wrote", f"size {size}: rejection with an error code and zero GATT writes required")

    # ---- actions, callbacks, controls -----------------------------------------------------------------------------
    def check_actions(self):
        actions = [a for a in L(self.get("actions")) if isinstance(a, dict)]
        by_name = {}
        for a in actions:
            by_name.setdefault(a.get("name"), a)
        prev_start = None
        ordered = sorted(actions, key=lambda a: a["seq"] if is_int(a.get("seq")) else 10**9)
        for a in ordered:
            if not is_int(a.get("seq")):
                self.bad("bad_timestamp", f"action {a.get('name')!r}: integer seq required")
            s, e = parse_ts(a.get("started_at")), parse_ts(a.get("ended_at"))
            if s is None or e is None or e < s:
                self.bad("bad_timestamp", f"action {a.get('name')!r}: valid UTC started_at <= ended_at required")
                continue
            if prev_start is not None and s < prev_start:
                self.bad("bad_timestamp", f"action {a.get('name')!r} starts before the previous sequenced action")
            prev_start = s
            if a.get("result") != "ok":
                self.bad("action_failed", f"action {a.get('name')!r} result {a.get('result')!r}")
        seqs = []
        for name in self.case["required_actions"]:
            if name not in by_name:
                self.bad("missing_action", f"required action {name!r} not recorded")
            elif is_int(by_name[name].get("seq")):
                seqs.append(by_name[name]["seq"])
        if seqs != sorted(seqs):
            self.bad("bad_timestamp", "required actions are not recorded in the case's controlled order")

    def check_callbacks(self):
        callbacks = [c for c in L(self.get("callbacks")) if isinstance(c, dict)]
        for c in callbacks:
            if parse_ts(c.get("t")) is None:
                self.bad("bad_timestamp", f"callback {c.get('code')!r} lacks a UTC timestamp")
        rc = self.case.get("required_callbacks")
        if rc:
            good = [c for c in callbacks if c.get("code") in rc["any_of_codes"] and nonempty(c.get("native_code"))
                    and nonempty(c.get("classification")) and c.get("recoverability") in ("retryable", "fatal")]
            if not good:
                self.bad("missing_callback_code", f"need a host error event with code in {rc['any_of_codes']}, native_code, classification and recoverability")

    # ---- pending-request handling (option 2 for EOF triggers; strict host correlation otherwise) ------------------------------
    def check_pending_handling(self):
        """cases.json `pending_request_handling` says how the fate of an in-flight request is evidenced for a trigger:

        * mode `eof_unobservable` (abrupt port.disconnect / tab close / Chrome-owned pipe EOF): the individual request's outcome is NOT
          observable. The record must say so (`per_request_outcome: "unknown"`, `host_correlated_cancellation_claimed: false`) and must
          carry no terminal error, request id or armed-pending claim: a record that asserts a per-request outcome here is fabricated
          (FAIL). The connector's synthesized `port_closed` is local only and can never be offered as host proof.
        * mode `host_correlated_cancellation` (explicit shutdown, per-phone failure): the record must prove an armed request was pending at the
          trigger and ended with a non-synthesized, correlated host error whose (code, stage) is the one the case allows for exactly this
          trigger; for shutdown the error precedes `shutdown_complete`. No other trigger's signal is accepted.
        Cases without a requirement must not carry a block at all (no claim without a rule)."""
        spec = self.case.get("pending_request_handling")
        block = self.get("pending_request_handling")
        if spec is None:
            if block is not None:
                self.bad("pending_claim_forbidden", "this case defines no pending-request handling, so the record may not assert one")
            return
        if not isinstance(block, dict):
            self.bad("pending_cancellation_unproven", "pending_request_handling block missing: the pending-request disposition must be recorded")
            return
        if block.get("mode") != spec["mode"] or block.get("trigger") != spec["trigger"]:
            self.bad("pending_handling_mode_mismatch", f"mode/trigger must be {spec['mode']!r}/{spec['trigger']!r} for this case")
            return
        if block.get("capture_ref") not in self.capture_names():
            self.bad("pending_cancellation_unproven", "pending_request_handling needs a capture_ref naming an existing capture")
        if spec["mode"] == "eof_unobservable":
            claims = sorted(k for k in ("request_id", "armed_pending_proven", "terminal", "terminal_before_shutdown_complete", "outcome")
                            if k in block)
            if block.get("per_request_outcome") != "unknown" or block.get("host_correlated_cancellation_claimed") is not False or claims:
                self.bad("pending_claim_forbidden", "an EOF trigger cannot show the request's fate: per_request_outcome must be 'unknown', "
                         f"host_correlated_cancellation_claimed false, and no per-request claim ({claims}) may be present")
            if not nonempty(block.get("reason")):
                self.bad("pending_cancellation_unproven", "the record must state why the outcome is not observable")
            return
        # strict host-correlated cancellation
        term = D(block.get("terminal"))
        allowed = {tuple(x) for x in spec["terminal_error"]}
        if not (nonempty(block.get("request_id")) and block.get("armed_pending_proven") is True):
            self.bad("pending_cancellation_unproven", "an armed request id and armed_pending_proven=true (pending at the trigger) are required")
        if not (term.get("type") == "error" and term.get("request_id") == block.get("request_id") and term.get("synthesized") is False
                and (term.get("code"), term.get("stage")) in allowed):
            self.bad("pending_cancellation_unproven", f"terminal must be a non-synthesized correlated error with (code, stage) in {sorted(allowed)}")
        if spec.get("ordering") == "terminal_error_before_shutdown_complete" and block.get("terminal_before_shutdown_complete") is not True:
            self.bad("pending_cancellation_unproven", "the terminal error must be shown to precede shutdown_complete")

    def check_controls_observations(self):
        refs = self.capture_names()
        controls = {c.get("name"): c for c in L(self.get("controls")) if isinstance(c, dict)}
        for name in self.case["required_controls"]:
            c = D(controls.get(name))
            if c.get("evidence_ref") not in refs:
                self.bad("missing_control", f"control {name!r} missing or without evidence capture")
            elif c.get("result") != "pass":
                self.bad("control_failed", f"control {name!r} did not pass")
        obs = {o.get("name"): o for o in L(self.get("observations")) if isinstance(o, dict)}
        for name in self.case["observations"]:
            o = D(obs.get(name))
            if o.get("evidence_ref") not in refs:
                self.bad("missing_observation", f"observation {name!r} missing or without evidence capture")
            elif o.get("result") != "none_observed":
                self.bad("observation_violation", f"observation {name!r}: {o.get('result')!r}")
        expected = self.case.get("expected_exit_codes")
        if expected:
            code = D(self.get("process")).get("exit_code")
            if not is_int(code) or code not in expected:
                self.bad("unexpected_exit_code", f"process.exit_code must be an integer in {expected}")

    def check_wrapper(self):
        w = self.m.get("wrapper_exception")
        if self.case["platform"] == "windows" and self.case["kind"] == "chrome":
            if not isinstance(w, dict) or not isinstance(w.get("used"), bool) or w.get("scope") != SCOPE:
                if not isinstance(w, dict) or w.get("scope") in (None, ""):
                    self.bad("wrapper_exception_missing", "Windows Chrome case must record whether the narrow wrapper exception was used")
                else:
                    self.bad("wrapper_exception_overbroad", "wrapper exception must be exactly the never-visible Chrome wrapper")
                return
            if w["used"] and (w.get("visible") is not False or w.get("capture_ref") not in self.capture_names()):
                self.bad("wrapper_exception_overbroad", "a used wrapper exception needs visible=false and a capture of the observation")
        elif w is not None and (w.get("used") if isinstance(w, dict) else True):
            self.bad("wrapper_exception_overbroad", "the wrapper exception exists only for Windows Chrome-launched cases")

    # ---- captures, catalog, adjudication ----------------------------------------------------------------------------
    def check_captures(self):
        kinds_needed = {"host_events", "process"}
        if self.case["requires_chrome"]:
            kinds_needed |= {"chrome_cdp", "hygiene"}
        if self.case["phones"] != "none":
            kinds_needed.add("phone_telemetry")
        have_kinds = set()
        for c in (c for c in L(self.get("captures")) if isinstance(c, dict)):
            data = None
            try:
                data = secure_read(self.run, c.get("path"))
            except PathError as e:
                self.bad("unsafe_path" if "symlink" in str(e) or "plain relative" in str(e) or "traversing" in str(e) else "capture_missing",
                         f"capture {c.get('name')!r}: {e}")
            if data is not None:
                if not hex64(c.get("sha256")) or sha256_bytes(data) != c["sha256"] or c.get("size") != len(data):
                    self.bad("capture_hash_mismatch", f"capture {c.get('name')!r}: manifest hash/size does not match the file")
                have_kinds.add(c.get("kind"))
        for k in sorted(kinds_needed - have_kinds):
            self.bad("capture_missing", f"required raw capture kind {k!r} absent")
        fs = self.get("failed_setups")
        if not isinstance(fs, list):
            self.bad("failed_setups_missing", "failed_setups must be a list (empty if none) - failed setups are preserved, not dropped")
        for s in L(fs):
            if not isinstance(s, dict) or s.get("capture_ref") not in self.capture_names() or not nonempty(s.get("reason")):
                self.bad("failed_setup_unpreserved", "each failed setup needs a reason and a preserved capture_ref")

    def check_catalog(self):
        cat = D(self.get("checksum_catalog"))
        if not hex64(cat.get("sha256")):
            self.bad("catalog_missing", "SHA256SUMS bound from run.json (checksum_catalog.sha256) is required")
            return
        try:
            raw = secure_read(self.run, "SHA256SUMS", MAX_JSON)
        except PathError as e:
            self.bad("catalog_missing" if "FileNotFoundError" in str(e) else "unsafe_path", f"SHA256SUMS: {e}")
            return
        if sha256_bytes(raw) != cat["sha256"]:
            self.bad("catalog_mismatch", "SHA256SUMS differs from the hash bound in run.json")
            return
        listed = {}
        for line in raw.decode("utf-8", "replace").splitlines():
            m = re.match(r"^([0-9a-f]{64})  (\S.*)$", line)
            if not m:
                self.bad("catalog_mismatch", f"malformed catalog line {line!r}")
                continue
            try:
                split_rel(m.group(2))
            except PathError as e:
                self.bad("unsafe_path", f"catalog entry: {e}")
                continue
            listed[m.group(2)] = m.group(1)
        files, problems = list_regular(self.run)
        for p in problems:
            self.bad("unsafe_path", f"run directory entry {p}")
        actual = {rel: f for rel, f in files.items() if rel not in CATALOG_EXCLUDED}
        for rel in actual:
            try:
                ok = listed.get(rel) == sha256_bytes(secure_read(self.run, rel))
            except PathError:
                ok = False
            if not ok:
                self.bad("catalog_mismatch", f"{rel}: not listed in the catalog or hash differs")
        for rel in listed:
            if rel not in actual:
                self.bad("catalog_mismatch", f"{rel}: listed but absent")

    def check_adjudication(self):
        ref = D(self.get("adjudication"))
        data = None
        try:
            data = secure_read(self.run, ref.get("path") if isinstance(ref.get("path"), str) else "adjudication.json", MAX_JSON)
        except PathError as e:
            self.bad("unsafe_path" if "symlink" in str(e) or "relative" in str(e) or "traversing" in str(e) else "adjudication_missing",
                     f"adjudication: {e}")
        if data is None:
            return
        try:
            adj = D(parse_json_strict(data))
        except ValueError:
            self.bad("adjudication_missing", "adjudication record unparseable")
            return
        if parse_ts(adj.get("at")) is None:
            self.bad("bad_timestamp", "adjudication.at must be a UTC timestamp")
        if adj.get("verdict") != "PASS":
            self.bad("adjudication_not_pass", f"adjudication verdict {adj.get('verdict')!r}")
        try:
            bound = sha256_bytes(secure_read(self.run, "run.json"))
        except PathError:
            bound = None
        if adj.get("run_manifest_sha256") != bound:
            self.bad("adjudication_mismatch", "adjudication is not bound to this exact run.json")
        if not nonempty(adj.get("adjudicator")) or adj.get("adjudicator") == self.m.get("operator") or adj.get("separate_from_operator") is not True:
            self.bad("adjudication_not_separate", "adjudicator must be a different party from the operator (self-attested)")
        if not nonempty(adj.get("review_ref")):
            self.bad("authenticated_review_missing", "adjudication.review_ref must point at the separate authenticated review (not verified here)")

    def run_all(self):
        for check in (self.check_provenance, self.check_binary, self.check_context, self.check_phones, self.check_actions,
                      self.check_callbacks, self.check_pending_handling, self.check_controls_observations, self.check_wrapper, self.check_captures,
                      self.check_catalog, self.check_adjudication):
            try:
                check()
            except Exception as e:  # malformed evidence must fail closed with a structured verdict, never crash
                self.bad("malformed_evidence", f"{check.__name__}: {type(e).__name__}")
        return self.defects


def validate_run(run_dir, cases_path=None, anchor=None):
    run_dir = Path(run_dir)
    cases_path = Path(cases_path) if cases_path else DEFAULT_CASES
    if not SECURE_FS_SUPPORTED:
        return Result("BLOCKED", [("unsupported_platform", "no race-resistant (openat/O_NOFOLLOW/O_DIRECTORY) filesystem traversal on "
                                                             "this platform: validator acceptance is disabled, not downgraded")])
    try:
        manifest = parse_json_strict(secure_read(run_dir, "run.json", MAX_JSON))
        if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
            raise ValueError("schema")
    except PathError as e:
        unsafe = "symlink" in str(e) or "relative" in str(e) or "traversing" in str(e)
        return Result("FAIL" if unsafe else "BLOCKED", [("unsafe_path" if unsafe else "bad_manifest", f"run.json: {e}")])
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return Result("BLOCKED", [("bad_manifest", f"{run_dir}/run.json unparseable, duplicate-keyed or wrong schema (want {SCHEMA})")])
    try:
        cases = {c["id"]: c for c in parse_json_strict(secure_read(cases_path.parent, cases_path.name, MAX_JSON))["cases"]}
    except (OSError, ValueError, KeyError, TypeError, PathError):
        return Result("BLOCKED", [("bad_manifest", f"cannot read case definitions {cases_path}")])
    case = cases.get(manifest.get("case_id")) if isinstance(manifest.get("case_id"), str) else None
    if case is None:
        return Result("BLOCKED", [("unknown_case", f"case_id {manifest.get('case_id')!r} is not in {cases_path.name}")])
    declared = manifest.get("declared_status")
    if declared in ("NOT RUN", "BLOCKED"):
        if declared == "BLOCKED" and not nonempty(manifest.get("blocker")):
            return Result("BLOCKED", [("blocker_missing", "BLOCKED requires a stated blocker")])
        return Result(declared, [])
    defects = Checker(run_dir, manifest, case, anchor, cases_path).run_all()
    if declared not in ("PASS", "FAIL"):
        defects.append(("bad_manifest", f"declared_status {declared!r} is not PASS/FAIL/BLOCKED/NOT RUN"))
    if declared == "FAIL" and not defects:
        return Result("FAIL", [])
    if not defects:
        return Result("PASS", [])
    return Result("FAIL" if any(code in FAIL_CODES for code, _ in defects) else "BLOCKED", defects)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_dir")
    ap.add_argument("--cases", default=None)
    ap.add_argument("--repo", default=str(REPO_ROOT), help="checkout the anchor is computed from")
    ap.add_argument("--binary", help="the actual production executable (absolute path)")
    ap.add_argument("--anchor", help="precomputed anchor JSON (from a trusted party) instead of --repo/--binary")
    args = ap.parse_args(argv)
    try:
        if args.anchor:
            anchor = json.loads(Path(args.anchor).read_text())
        elif args.binary:
            anchor = compute_anchor(args.repo, args.binary)
        else:
            anchor = None
    except (OSError, ValueError, subprocess.SubprocessError, AnchorError) as e:
        print(f"validate-evidence: cannot compute anchor: {type(e).__name__}: {e if isinstance(e, AnchorError) else ''}", file=sys.stderr)
        return 3
    result = validate_run(args.run_dir, args.cases, anchor)
    print(json.dumps({"verdict": result.verdict, "notice": NOTICE, "acceptance": "NOT ACCEPTED without a separate authenticated review",
                      "defects": [{"code": c, "message": m} for c, m in result.defects]}, indent=2))
    return EXIT[result.verdict]


if __name__ == "__main__":
    sys.exit(main())
