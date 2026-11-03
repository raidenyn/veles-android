"""Externally computed, operator-pinned source anchor for runs on a checkout that has no usable git metadata.

Problem: `validate-evidence.py compute_anchor` needs `git`. A copied (non-git) checkout, e.g. on the Mac, has none, and
this driver must never invent a revision or claim that a binary was built from source it cannot identify.

Solution, in two halves that share nothing but a JSON document and its sha256:

* `emit_anchor` runs ONLY on the original git worktree (tracked + untracked, non-ignored files, exactly the file set
  `source_tree_sha256` hashes) and writes a document with the real revision, a per-file sha256 manifest, the evaluator
  file hashes, the host executable's identity (path, sha256, size) and an optional builder attestation.
* `verify_anchor` runs where the acceptance happens. The operator passes the document AND its sha256 obtained over a
  separate channel (the "pin"). The document is accepted only if its bytes hash to the pin, the actual checkout's bytes
  (walked from disk, no git) equal the manifest exactly, the evaluator files match, and the actual host executable has
  the anchored path, sha256 and size. Anything else raises `Blocked`: the run is recorded BLOCKED, never accepted.

Trust limits (stated, not hidden):
* This is DRIFT/ACCIDENT detection, NOT adversarial authentication of the copy. The driver (lifecycle-driver.py, accdrv/*)
  is itself loaded from the checkout it verifies, so a hostile copy can simply skip verification. Adversarial copies would
  need the driver files hashed by separately obtained tooling (e.g. `shasum -a 256 -c` against a sums file delivered over the
  same out-of-band channel as the pin) BEFORE the driver is started; nothing in this repository does that for the operator.
* Python bytecode: the CLI points `sys.pycache_prefix` at a fresh empty directory before importing any checkout module, so a
  planted `__pycache__/*.pyc` is never read by the driver; and any `__pycache__` or `.pyc` in the walked tree is Blocked.
  Modules imported by an interpreter started before that point are out of reach.
* The pin is only as trustworthy as the channel that carried it; the verification proves the checked-out bytes equal the bytes
  the generator saw, not that the generator ran on an honest checkout; a build attestation is the generator-side operator's
  assertion and is only echoed, never produced, here.
* The executable is hash-checked at verification, when recorded, after codesign / Authenticode, immediately before the launch, before
  every native connect/reconnect and after the run. The file itself and its directory chain must be owned by the operator/root and
  not group/other-writable (sticky dirs excepted) and the file must have exactly one hard link (POSIX). This narrows
  but cannot close the check-to-exec window (no fd-based exec, no file-system snapshot): a swap-and-restore between a check and
  Chrome's own exec of the host is not detectable by this driver. Windows ACLs are not inspected.
"""
import hashlib
import json
import os
import contextlib
import re
import stat
import subprocess
import types
from pathlib import Path

from .errors import Blocked

SCHEMA = "veles-otp24-source-anchor/1"
# Copies of the validator constants, so nothing from the checkout has to be executed before it is verified.
# test_anchor.py asserts they equal the validator's.
EVALUATOR_FILES = ("scripts/physical-matrix.py", "scripts/validate-evidence.py", "acceptance/cases.json")
TREE_EXCLUDED_PREFIXES = (".superpowers/", "native-bridge/host/evidence/")
HOST_REL = "native-bridge/host"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_ANCHOR = 64 * 1024 * 1024
SHOWN = 8


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def tree_sha256(files):
    """The validator's tree hash, recomputed from a {relpath: sha256} map (no filesystem, no git)."""
    lines = [f"{files[rel]}  {rel}\n" for rel in sorted(files, key=lambda r: r.encode("utf-8", "surrogateescape"))]
    return sha256_bytes("".join(lines).encode("utf-8", "surrogateescape"))


def _excluded(rel):
    return rel.startswith(TREE_EXCLUDED_PREFIXES)


def _bytecode(rel):
    return "__pycache__" in rel.split("/") or rel.endswith((".pyc", ".pyo"))


def scrubbed_git_env(base=None):
    """The environment without any GIT_* variable (GIT_DIR, GIT_WORK_TREE, GIT_CEILING_DIRECTORIES, ... can redirect git)."""
    return {k: v for k, v in (os.environ if base is None else base).items() if not k.upper().startswith("GIT_")}


@contextlib.contextmanager
def git_environ_scrubbed():
    """Remove GIT_* from os.environ for the duration, so code that shells out to git (the validator) cannot be redirected."""
    saved = {k: v for k, v in os.environ.items() if k.upper().startswith("GIT_")}
    for k in saved:
        del os.environ[k]
    try:
        yield
    finally:
        os.environ.update(saved)


def require_own_git_checkout(root):
    """Blocked unless git, run with a scrubbed environment inside `root`, says `root` ITSELF is the top level of a repository
    whose git dir resolves. A copy that merely sits under some other repository (or a dangling worktree pointer) must not
    inherit that repository's HEAD."""
    def run(*args):
        try:
            p = subprocess.run(["git", *args], cwd=str(root), env=scrubbed_git_env(), capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            raise Blocked(f"git could not be run ({type(e).__name__})")
        if p.returncode != 0:
            raise Blocked("no usable git metadata in this checkout")
        return p.stdout.decode("utf-8", "surrogateescape").strip()
    top = run("rev-parse", "--show-toplevel")
    if os.path.realpath(top) != os.path.realpath(str(root)):
        raise Blocked(f"git reports {top} as the repository top level, not the checkout {root}: refusing to inherit another repository's revision")
    git_dir = run("rev-parse", "--absolute-git-dir")
    if not os.path.isdir(git_dir):
        raise Blocked("the git directory of this checkout does not resolve")


def check_trusted_chain(path, uid=None):
    """POSIX: every ancestor directory of `path` is a real directory owned by the operator (or root) and not group/other-writable
    unless sticky. Blocked otherwise. Fail closed; a no-op is NOT offered on POSIX."""
    uid = os.getuid() if uid is None else uid
    parent = Path(path).parent
    for d in (parent, *parent.parents):
        try:
            st = os.lstat(d)
        except OSError as e:
            raise Blocked(f"{d}: cannot inspect the host's directory chain ({type(e).__name__})")
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise Blocked(f"{d}: the host's directory chain contains a symlink or non-directory")
        if st.st_uid not in (uid, 0):
            raise Blocked(f"{d}: owned by uid {st.st_uid}, not the operator or root")
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and not st.st_mode & stat.S_ISVTX:
            raise Blocked(f"{d}: group/other-writable without the sticky bit")


def check_host_file(path, uid=None):
    """POSIX: the executable itself is a regular, non-symlink file owned by the operator/root, not group/other-writable, and has
    exactly one hard link (a second name elsewhere could be rewritten without touching this directory). Windows: not inspected."""
    if os.name == "nt":
        return
    uid = os.getuid() if uid is None else uid
    try:
        st = os.lstat(path)
    except OSError as e:
        raise Blocked(f"{path}: cannot inspect the host executable ({type(e).__name__})")
    if not stat.S_ISREG(st.st_mode):
        raise Blocked(f"{path}: the host executable is not a regular file")
    if st.st_uid not in (uid, 0):
        raise Blocked(f"{path}: the host executable is owned by uid {st.st_uid}, not the operator or root")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise Blocked(f"{path}: the host executable is group/other-writable")
    if st.st_nlink != 1:
        raise Blocked(f"{path}: the host executable has {st.st_nlink} hard links (must be exactly 1)")


def host_guard(host, sha256, size=None):
    """Callable that re-hashes `host` and raises Blocked unless it still has the anchored bytes. Used at every step that
    could act on the executable (record, codesign, Authenticode, launch, connect, post-run)."""
    def guard(where="check"):
        check_host_file(host)
        try:
            data = Path(host).read_bytes()
        except OSError as e:
            raise Blocked(f"the production executable is unreadable at {where} ({type(e).__name__})")
        if sha256_bytes(data) != sha256 or (size is not None and len(data) != size):
            raise Blocked(f"the production executable differs from the anchored sha256 at {where}")
        return sha256
    return guard


def _hex64_map(m):
    return isinstance(m, dict) and all(isinstance(k, str) and isinstance(v, str) and HEX64.match(v) for k, v in m.items())


def _family_of(path):
    return "windows" if re.match(r"^[A-Za-z]:[\\/]", str(path)) else "posix"


# ---- generating side (original git worktree only) ---------------------------------------------------------------------
def _git(repo, *args):
    try:
        return subprocess.run(["git", *args], cwd=str(repo), env=scrubbed_git_env(), capture_output=True, timeout=120, check=True).stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise Blocked(f"anchor can only be generated on the original git worktree ({type(e).__name__})")


def emit_anchor(repo, validator, *, binary_path, binary_sha256, binary_size, attest_built_from_tree=False, checkout_root=None):
    """Anchor document for `repo` (a real git worktree). `validator` is the loaded validate-evidence module; its own
    `source_tree_sha256`/`norm_path` are used as the reference and cross-checked, so the two can never drift."""
    repo = str(repo)
    require_own_git_checkout(repo)  # the generator must be the original worktree itself, never a parent repo or a GIT_* redirect
    revision = _git(repo, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    if not HEX40.match(revision):
        raise Blocked("git did not return a full 40-hex HEAD revision")
    listed = _git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    files = {}
    for raw in sorted(p for p in set(listed.split(b"\0")) if p):
        rel = raw.decode("utf-8", "surrogateescape")
        if _excluded(rel):
            continue
        if _bytecode(rel):
            raise Blocked(f"source tree contains Python bytecode ({rel}); refusing to anchor")
        full = os.path.join(repo, rel)
        try:
            st = os.lstat(full)
        except OSError:
            continue  # tracked file deleted in the working tree
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise Blocked(f"source tree contains a symlink or special file ({rel}); refusing to anchor")
        files[rel] = sha256_bytes(Path(full).read_bytes())
    tree = tree_sha256(files)
    with git_environ_scrubbed():
        reference_tree = validator.source_tree_sha256(repo)
    if tree != reference_tree:
        raise Blocked("internal: the manifest tree hash differs from the validator's source_tree_sha256")
    host = Path(repo) / HOST_REL
    evaluator = {rel: sha256_bytes((host / rel).read_bytes()) for rel in EVALUATOR_FILES}
    family = _family_of(binary_path)
    canonical = validator.norm_path(str(binary_path), family)
    if canonical is None:
        raise Blocked("--binary-path must be a normalized absolute path (no '.', '..', UNC or relative components)")
    if not (isinstance(binary_sha256, str) and HEX64.match(binary_sha256)) or not isinstance(binary_size, int) or binary_size < 0:
        raise Blocked("binary sha256/size must come from the real executable")
    doc = {"schema": SCHEMA, "source_revision": revision, "source_tree_sha256": tree, "source_files": files, "evaluator_files": evaluator,
           "binary_sha256": binary_sha256, "binary_path": canonical, "binary_path_platform": family, "binary_size": binary_size,
           "build_attestation": None, "checkout_root": None}
    if attest_built_from_tree:
        doc["build_attestation"] = {"built_from_source_revision": revision, "built_from_tree_sha256": tree, "binary_sha256": binary_sha256}
    if checkout_root:
        doc["checkout_root"] = str(checkout_root)
    return doc


# ---- verifying side (any machine, no git) -----------------------------------------------------------------------------
def walk_checkout(root):
    """{relpath: sha256} of every regular file under `root`, no git and no symlink following. Skipped: a top-level `.git`
    entry (dangling worktree pointers are meaningless here and never trusted) and the
    validator's evidence/state prefixes. Bytecode (`__pycache__`, `*.pyc`) is Blocked. A symlink or special file anywhere else is Blocked."""
    root = str(root)
    found = {}
    for current, dirs, names in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(current, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in sorted(dirs):
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if rel == ".git" or (rel + "/").startswith(TREE_EXCLUDED_PREFIXES):
                continue
            if d == "__pycache__":
                raise Blocked(f"checkout contains a __pycache__ directory ({rel}); unanchored bytecode is refused")
            if os.path.islink(os.path.join(current, d)):
                raise Blocked(f"checkout contains a symlink ({rel}); referent bytes are not bound")
            keep.append(d)
        dirs[:] = keep
        for n in sorted(names):
            rel = f"{rel_dir}/{n}" if rel_dir else n
            if rel == ".git" or _excluded(rel):
                continue
            if _bytecode(rel):
                raise Blocked(f"checkout contains Python bytecode ({rel}); unanchored bytecode is refused")
            full = os.path.join(current, n)
            st = os.lstat(full)
            if stat.S_ISLNK(st.st_mode):
                raise Blocked(f"checkout contains a symlink ({rel}); referent bytes are not bound")
            if not stat.S_ISREG(st.st_mode):
                raise Blocked(f"checkout contains a non-regular file ({rel})")
            found[rel] = sha256_bytes(Path(full).read_bytes())
    return found


def _names(items):
    items = sorted(items)
    return ", ".join(items[:SHOWN]) + (f" (+{len(items) - SHOWN} more)" if len(items) > SHOWN else "")


def _load_validator(source_bytes, path):
    """Execute validate-evidence.py from bytes that were just hash-checked (not re-read from disk)."""
    module = types.ModuleType("validate_evidence_from_anchored_bytes")
    module.__file__ = str(path)
    exec(compile(source_bytes, str(path), "exec"), module.__dict__)  # noqa: S102 - bytes are anchored by hash
    return module


def _read_anchor(path, pinned):
    if not (isinstance(pinned, str) and HEX64.match(pinned)):
        raise Blocked("--anchor-sha256 must be the 64-hex sha256 of the anchor file, obtained out of band")
    p = Path(path)
    try:
        if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_ANCHOR:
            raise Blocked(f"{p}: the anchor must be a regular file")
        data = p.read_bytes()
    except OSError as e:
        raise Blocked(f"anchor file unreadable: {type(e).__name__}")
    if sha256_bytes(data) != pinned:
        raise Blocked("anchor file sha256 does not match the pinned --anchor-sha256 (edited, truncated or wrong file)")
    try:
        doc = json.loads(data)
    except ValueError:
        raise Blocked("anchor file is not valid JSON")
    return doc


def _check_document(doc):
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise Blocked(f"anchor schema must be {SCHEMA}")
    if not HEX40.match(str(doc.get("source_revision"))):
        raise Blocked("anchor source_revision must be a full 40-hex commit generated on the original git worktree")
    if not HEX64.match(str(doc.get("source_tree_sha256"))):
        raise Blocked("anchor source_tree_sha256 malformed")
    files = doc.get("source_files")
    if not _hex64_map(files) or not files:
        raise Blocked("anchor source_files must be a non-empty {path: sha256} manifest")
    if tree_sha256(files) != doc["source_tree_sha256"]:
        raise Blocked("anchor is internally inconsistent: source_files do not hash to source_tree_sha256")
    ev = doc.get("evaluator_files")
    if not _hex64_map(ev) or set(ev) != set(EVALUATOR_FILES):
        raise Blocked(f"anchor evaluator_files must cover exactly {EVALUATOR_FILES}")
    for rel, digest in ev.items():
        if files.get(f"{HOST_REL}/{rel}") != digest:
            raise Blocked(f"anchor evaluator hash for {rel} is not part of its own source manifest")
    if not HEX64.match(str(doc.get("binary_sha256"))) or not isinstance(doc.get("binary_size"), int) or isinstance(doc.get("binary_size"), bool):
        raise Blocked("anchor binary_sha256/binary_size malformed")
    if not isinstance(doc.get("binary_path"), str):
        raise Blocked("anchor binary_path must be a string")
    if doc.get("binary_path_platform") not in ("posix", "windows"):
        raise Blocked("anchor binary_path_platform must be posix or windows")
    att = doc.get("build_attestation")
    if att is not None:
        want = {"built_from_source_revision": doc["source_revision"], "built_from_tree_sha256": doc["source_tree_sha256"],
                "binary_sha256": doc["binary_sha256"]}
        if att != want:
            raise Blocked("anchor build_attestation does not name exactly the anchored revision, tree and binary")


def verify_anchor(anchor_path, pinned_sha256, checkout_root, host, family):
    """Returns a validator-compatible anchor dict (plus `externally_attested`, `anchor_sha256`, `cases_json_bytes` = the
    hash-checked acceptance/cases.json bytes the run must use) or raises Blocked."""
    doc = _read_anchor(anchor_path, pinned_sha256)
    _check_document(doc)
    root = Path(checkout_root)
    if root.is_symlink() or not root.is_dir():
        raise Blocked(f"{root}: the checkout must be a real directory")
    real_root = os.path.realpath(root)
    if doc.get("checkout_root") not in (None, "") and doc["checkout_root"] != real_root:
        raise Blocked(f"anchor was issued for checkout {doc['checkout_root']} but the actual checkout is {real_root}")
    # 1. evaluator bytes first: they are the only code we are about to execute from the checkout.
    host_dir = root / HOST_REL
    loaded = {}
    for rel in EVALUATOR_FILES:
        try:
            loaded[rel] = (host_dir / rel).read_bytes()
        except OSError:
            raise Blocked(f"evaluator file {rel} is missing from the checkout")
        if sha256_bytes(loaded[rel]) != doc["evaluator_files"][rel]:
            raise Blocked(f"evaluator file {rel} differs from the anchored bytes")
    # 2. the whole source byte tree.
    actual = walk_checkout(root)
    expected = doc["source_files"]
    missing = [r for r in expected if r not in actual]
    extra = [r for r in actual if r not in expected]
    changed = [r for r in expected if r in actual and actual[r] != expected[r]]
    if missing or extra or changed:
        parts = [f"{label}: {_names(items)}" for label, items in (("changed", changed), ("missing", missing), ("unanchored extra", extra)) if items]
        raise Blocked("checked-out source does not match the anchored byte tree (" + "; ".join(parts) + ")")
    if tree_sha256(actual) != doc["source_tree_sha256"]:
        raise Blocked("checked-out source tree hash differs from the anchor")
    # 3. the actual executable: platform, exact path, sha256, size.
    validator = _load_validator(loaded["scripts/validate-evidence.py"], host_dir / "scripts" / "validate-evidence.py")
    if doc["binary_path_platform"] != family:
        raise Blocked(f"anchor was issued for a {doc['binary_path_platform']} executable path but this case needs {family}")
    anchored = validator.norm_path(doc["binary_path"], family)
    actual_path = validator.norm_path(str(host), family)
    if anchored is None or actual_path is None:
        raise Blocked("anchor or --host path is not a normalized absolute path")
    if not validator.same_path(actual_path, anchored, family):
        raise Blocked(f"--host path {actual_path} is not the anchored executable path {anchored}")
    try:
        if family == "posix" and os.path.realpath(actual_path) != actual_path:
            raise Blocked("--host path contains a symlink; give the real path of the executable")
        st = os.lstat(actual_path)
        if not stat.S_ISREG(st.st_mode):
            raise Blocked("--host is not a regular file")
        data = Path(actual_path).read_bytes()
    except OSError as e:
        raise Blocked(f"--host unreadable: {type(e).__name__}")
    if sha256_bytes(data) != doc["binary_sha256"] or len(data) != doc["binary_size"]:
        raise Blocked("the executable's sha256/size differ from the anchored binary")
    if family == "posix":
        check_trusted_chain(actual_path)
    check_host_file(actual_path)
    return {"source_revision": doc["source_revision"], "source_tree_sha256": doc["source_tree_sha256"],
            "evaluator_files": dict(doc["evaluator_files"]), "binary_sha256": doc["binary_sha256"], "binary_path": anchored,
            "binary_path_platform": family, "binary_size": doc["binary_size"], "externally_attested": doc["build_attestation"] is not None,
            "anchor_sha256": pinned_sha256, "cases_json_bytes": loaded["acceptance/cases.json"], "verified_file_count": len(actual), "schema": SCHEMA}
