#!/usr/bin/env python3
"""Tests for the externally computed, pinned source anchor (accdrv/anchor.py) and its driver wiring.

Everything here uses temporary directories, a throw-away git repository for the *generating* side, and fakes. No Chrome,
registry, keychain, signing, device or project git metadata is touched.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

from accdrv import anchor as anc, chrome as achrome, registration as reg, runner  # noqa: E402
from accdrv.errors import Blocked, Refused  # noqa: E402

REAL_HOST = HERE.parent
MAC_EXE = "/Users/tester/acc/VelesOtp23PoC.app/Contents/MacOS/veles-native-host"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


VALIDATOR = load("validator_for_anchor_tests", "validate-evidence.py")
CLI = load("lifecycle_driver_anchor_cli", "lifecycle-driver.py")
CASES = {c["id"]: c for c in json.loads((REAL_HOST / "acceptance" / "cases.json").read_text())["cases"]}


def git(repo, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
                          cwd=repo, env=env, check=True, capture_output=True).stdout


class AnchorFixture(unittest.TestCase):
    """A synthetic original checkout (git, with an untracked file), its pinned anchor, and a non-git copy of it."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(os.path.realpath(self._td.name))
        self.orig = self.tmp / "orig"
        host = self.orig / "native-bridge" / "host"
        (host / "scripts").mkdir(parents=True)
        (host / "acceptance").mkdir()
        for rel in anc.EVALUATOR_FILES:  # the real evaluator bytes, so the verified validator is the real one
            shutil.copyfile(REAL_HOST / rel, host / rel)
        (host / "src").mkdir()
        (host / "src" / "main.rs").write_text("fn main() {}\n")
        (self.orig / ".gitignore").write_text("__pycache__/\ntarget/\n")
        git(self.orig, "init", "-q")
        git(self.orig, "add", "-A")
        git(self.orig, "commit", "-q", "-m", "fixture")
        (host / "src" / "untracked.rs").write_text("// untracked but part of the acceptance source\n")
        (self.orig / "target").mkdir()
        (self.orig / "target" / "ignored.bin").write_bytes(b"ignored build output")
        (self.orig / ".superpowers").mkdir()
        (self.orig / ".superpowers" / "notes.md").write_text("excluded evidence/state\n")
        self.revision = git(self.orig, "rev-parse", "HEAD").decode().strip()
        self.exe = self.tmp / "mac" / "VelesOtp23PoC.app" / "Contents" / "MacOS" / "veles-native-host"
        self.exe.parent.mkdir(parents=True)
        for d in self.exe.parents:  # whatever the umask: the host's directory chain must not be group-writable
            if d == self.tmp.parent:
                break
            d.chmod(0o755)
        self.exe.write_bytes(b"\xcf\xfa\xed\xfe fake mach-o")
        self.exe.chmod(0o755)
        self.exe_sha = hashlib.sha256(self.exe.read_bytes()).hexdigest()
        self.anchor_doc = self.emit()
        self.copy = self.tmp / "copy"  # the Mac copy: bytes only, no git metadata
        shutil.copytree(self.orig, self.copy, ignore=shutil.ignore_patterns(".git", "target", ".superpowers"))
        (self.copy / ".git").write_text("gitdir: /linux/only/path/.git/worktrees/x\n")  # dangling worktree pointer
        self.anchor_path = self.tmp / "anchor.json"
        self.write_anchor(self.anchor_doc)

    def emit(self, **kw):
        args = dict(binary_path=str(self.exe), binary_sha256=self.exe_sha, binary_size=self.exe.stat().st_size, attest_built_from_tree=True)
        args.update(kw)
        return anc.emit_anchor(self.orig, VALIDATOR, **args)

    def write_anchor(self, doc):
        data = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()
        self.anchor_path.write_bytes(data)
        self.pin = hashlib.sha256(data).hexdigest()

    def verify(self, **kw):
        args = dict(anchor_path=self.anchor_path, pinned_sha256=self.pin, checkout_root=self.copy, host=self.exe, family="posix")
        args.update(kw)
        return anc.verify_anchor(**args)


class EmitTests(AnchorFixture):
    def test_emit_matches_the_validators_own_computation_and_includes_untracked(self):
        self.assertEqual(self.anchor_doc["source_revision"], self.revision)
        self.assertEqual(self.anchor_doc["source_tree_sha256"], VALIDATOR.source_tree_sha256(str(self.orig)))
        files = self.anchor_doc["source_files"]
        self.assertIn("native-bridge/host/src/untracked.rs", files)
        self.assertNotIn("target/ignored.bin", files)
        self.assertFalse([f for f in files if f.startswith(".superpowers/") or f.startswith(".git/")])
        self.assertEqual(self.anchor_doc["evaluator_files"], VALIDATOR_EVALUATOR_HASHES(self.orig))

    def test_emit_is_compatible_with_the_validators_anchor_shape(self):
        reference = VALIDATOR.compute_anchor(str(self.orig), str(self.exe))
        for key, value in reference.items():
            self.assertEqual(self.anchor_doc[key], value, key)

    def test_emit_requires_real_git_and_never_invents_a_revision(self):
        with self.assertRaises(Blocked):
            anc.emit_anchor(self.copy, VALIDATOR, binary_path=str(self.exe), binary_sha256=self.exe_sha, binary_size=1)

    def test_attestation_is_absent_unless_explicitly_requested(self):
        doc = self.emit(attest_built_from_tree=False)
        self.assertIsNone(doc["build_attestation"])
        att = self.anchor_doc["build_attestation"]
        self.assertEqual((att["built_from_source_revision"], att["built_from_tree_sha256"], att["binary_sha256"]),
                         (self.revision, self.anchor_doc["source_tree_sha256"], self.exe_sha))

    def test_constants_match_the_validator(self):
        self.assertEqual(anc.EVALUATOR_FILES, VALIDATOR.EVALUATOR_FILES)
        self.assertEqual(anc.TREE_EXCLUDED_PREFIXES, VALIDATOR.TREE_EXCLUDED_PREFIXES)


def VALIDATOR_EVALUATOR_HASHES(repo):
    return {rel: hashlib.sha256((Path(repo) / "native-bridge" / "host" / rel).read_bytes()).hexdigest() for rel in VALIDATOR.EVALUATOR_FILES}


class VerifyTests(AnchorFixture):
    def test_valid_anchored_copy_without_git_is_accepted(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("verification must not run git")):
            v = self.verify()
        self.assertEqual(v["source_revision"], self.revision)
        self.assertEqual(v["source_tree_sha256"], self.anchor_doc["source_tree_sha256"])
        self.assertEqual(v["binary_path"], str(self.exe))
        self.assertTrue(v["externally_attested"])
        self.assertEqual(v["anchor_sha256"], self.pin)

    def test_verified_anchor_drives_the_run_record_fields(self):
        v = self.verify()
        fields = runner.anchor_fields(v, self.exe, attest=False)  # a caller flag cannot change an externally anchored attestation
        self.assertEqual(fields["source"], {"revision": self.revision, "tree_sha256": self.anchor_doc["source_tree_sha256"]})
        self.assertEqual(fields["binary"]["built_from_source_revision"], self.revision)
        self.assertEqual(fields["evaluator"]["files"], self.anchor_doc["evaluator_files"])

    def test_no_attestation_in_anchor_means_no_provenance_claim(self):
        self.write_anchor(self.emit(attest_built_from_tree=False))
        v = self.verify()
        self.assertFalse(v["externally_attested"])
        fields = runner.anchor_fields(v, self.exe, attest=True)
        self.assertIsNone(fields["binary"]["built_from_source_revision"])
        self.assertIsNone(fields["binary"]["built_from_tree_sha256"])

    def test_wrong_pin_or_edited_anchor_is_blocked(self):
        with self.assertRaises(Blocked):
            self.verify(pinned_sha256="0" * 64)
        with self.assertRaises(Blocked):
            self.verify(pinned_sha256="not-hex")
        doc = dict(self.anchor_doc, source_revision="1" * 40)
        data = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()
        self.anchor_path.write_bytes(data)  # edited after the pin was taken
        with self.assertRaises(Blocked):
            self.verify()

    def test_mutated_source_byte_is_blocked(self):
        target = self.copy / "native-bridge" / "host" / "src" / "main.rs"
        target.write_text("fn main() { evil(); }\n")
        with self.assertRaisesRegex(Blocked, "main.rs"):
            self.verify()

    def test_mutated_untracked_and_evaluator_files_are_blocked(self):
        (self.copy / "native-bridge/host/src/untracked.rs").write_text("// changed\n")
        with self.assertRaises(Blocked):
            self.verify()
        shutil.copyfile(self.orig / "native-bridge/host/src/untracked.rs", self.copy / "native-bridge/host/src/untracked.rs")
        self.verify()
        with open(self.copy / "native-bridge/host/scripts/validate-evidence.py", "ab") as f:
            f.write(b"\n# tampered\n")
        with self.assertRaisesRegex(Blocked, "validate-evidence.py"):
            self.verify()

    def test_added_or_removed_files_are_blocked(self):
        (self.copy / "native-bridge/host/src/extra.rs").write_text("x")
        with self.assertRaisesRegex(Blocked, "extra.rs"):
            self.verify()
        (self.copy / "native-bridge/host/src/extra.rs").unlink()
        (self.copy / "native-bridge/host/src/main.rs").unlink()
        with self.assertRaisesRegex(Blocked, "main.rs"):
            self.verify()

    def test_symlinks_in_the_copy_are_blocked(self):
        os.symlink("main.rs", self.copy / "native-bridge/host/src/link.rs")
        with self.assertRaisesRegex(Blocked, "symlink"):
            self.verify()

    def test_planted_bytecode_is_blocked_not_tolerated(self):
        cache = self.copy / "native-bridge/host/scripts/__pycache__"
        cache.mkdir()
        (cache / "x.cpython-313.pyc").write_bytes(b"pyc")
        with self.assertRaisesRegex(Blocked, "__pycache__"):
            self.verify()
        shutil.rmtree(cache)
        self.verify()
        (self.copy / "native-bridge/host/scripts/stray.pyc").write_bytes(b"pyc")
        with self.assertRaisesRegex(Blocked, "bytecode"):
            self.verify()

    def test_emit_refuses_a_tree_that_contains_bytecode(self):
        cache = self.orig / "native-bridge/host/scripts/__pycache__"
        cache.mkdir()
        (cache / "x.pyc").write_bytes(b"pyc")
        (self.orig / ".gitignore").write_text("target/\n")  # not ignored: it would be part of the validator's tree hash
        with self.assertRaisesRegex(Blocked, "bytecode"):
            self.emit()

    def test_non_string_binary_path_is_blocked_not_a_crash(self):
        self.write_anchor(dict(self.anchor_doc, binary_path=["x"]))
        with self.assertRaises(Blocked):
            self.verify()

    def test_host_directory_chain_must_be_operator_owned_and_not_writable_by_others(self):
        self.verify()
        bundle = self.exe.parent
        bundle.chmod(0o775)
        with self.assertRaisesRegex(Blocked, "group/other-writable"):
            self.verify()
        bundle.chmod(0o777 | 0o1000)  # sticky, like /tmp: tolerated
        self.verify()
        bundle.chmod(0o755)
        self.exe.parent.parent.chmod(0o757)
        with self.assertRaisesRegex(Blocked, "writable"):
            self.verify()
        self.exe.parent.parent.chmod(0o755)

    @unittest.skipIf(os.getuid() == 0, "root-owned directories are always accepted")
    def test_directory_owned_by_someone_else_is_blocked(self):
        with self.assertRaisesRegex(Blocked, "owned by uid"):
            anc.check_trusted_chain(self.exe, uid=os.getuid() + 1)

    def test_missing_or_dangling_git_metadata_is_irrelevant_but_present_git_is_not_trusted(self):
        shutil.rmtree(self.copy / ".git", ignore_errors=True)
        (self.copy / ".git").unlink(missing_ok=True)
        self.verify()
        # a real-looking .git directory whose HEAD says something else must not change the identity
        (self.copy / ".git").mkdir()
        (self.copy / ".git" / "HEAD").write_text("ref: refs/heads/other\n")
        self.assertEqual(self.verify()["source_revision"], self.revision)

    def test_binary_hash_or_size_mismatch_is_blocked(self):
        self.exe.write_bytes(b"\xcf\xfa\xed\xfe different build")
        with self.assertRaisesRegex(Blocked, "sha256"):
            self.verify()

    def test_binary_path_substitution_is_blocked(self):
        other = self.tmp / "other" / "VelesOtp23PoC.app" / "Contents" / "MacOS" / "veles-native-host"
        other.parent.mkdir(parents=True)
        shutil.copyfile(self.exe, other)  # identical bytes, wrong place
        with self.assertRaisesRegex(Blocked, "path"):
            self.verify(host=other)

    def test_anchor_for_another_platform_or_foreign_path_style_is_blocked(self):
        for doc in (dict(self.anchor_doc, binary_path_platform="windows", binary_path="C:\\acc\\veles-native-host.exe"),
                    dict(self.anchor_doc, binary_path="/home/linux/veles-native-host"),
                    dict(self.anchor_doc, binary_path="relative/veles-native-host"),
                    dict(self.anchor_doc, binary_path=str(self.exe) + "/../veles-native-host")):
            self.write_anchor(doc)
            with self.assertRaises(Blocked, msg=doc["binary_path"]):
                self.verify()
        self.write_anchor(self.anchor_doc)
        with self.assertRaises(Blocked):
            self.verify(family="windows")

    def test_symlinked_host_is_blocked(self):
        link = self.tmp / "link-host"
        os.symlink(self.exe, link)
        self.write_anchor(dict(self.anchor_doc, binary_path=str(link)))
        with self.assertRaises(Blocked):
            self.verify(host=link)

    def test_checkout_root_bound_by_the_anchor_cannot_be_substituted(self):
        self.write_anchor(self.emit(checkout_root=str(self.copy)))
        self.verify()
        elsewhere = self.tmp / "elsewhere"
        shutil.copytree(self.copy, elsewhere)
        with self.assertRaisesRegex(Blocked, "checkout"):
            self.verify(checkout_root=elsewhere)

    def test_evaluator_set_and_hash_must_be_exact(self):
        bad = dict(self.anchor_doc["evaluator_files"])
        bad.pop("acceptance/cases.json")
        self.write_anchor(dict(self.anchor_doc, evaluator_files=bad))
        with self.assertRaises(Blocked):
            self.verify()
        bad = dict(self.anchor_doc["evaluator_files"], **{"acceptance/cases.json": "0" * 64})
        self.write_anchor(dict(self.anchor_doc, evaluator_files=bad))
        with self.assertRaises(Blocked):
            self.verify()

    def test_malformed_anchor_documents_are_blocked(self):
        for doc in (dict(self.anchor_doc, schema="other/1"), dict(self.anchor_doc, source_revision="abc"),
                    dict(self.anchor_doc, source_tree_sha256="z" * 64), dict(self.anchor_doc, source_files=[]),
                    dict(self.anchor_doc, build_attestation={"built_from_source_revision": "2" * 40,
                                                             "built_from_tree_sha256": self.anchor_doc["source_tree_sha256"],
                                                             "binary_sha256": self.exe_sha}),
                    dict(self.anchor_doc, source_tree_sha256="0" * 64)):
            self.write_anchor(doc)
            with self.assertRaises(Blocked, msg=str(doc.get("schema"))):
                self.verify()
        self.anchor_path.write_text("not json")
        self.pin = hashlib.sha256(b"not json").hexdigest()
        with self.assertRaises(Blocked):
            self.verify()

    def test_checkout_missing_or_not_a_directory_is_blocked(self):
        with self.assertRaises(Blocked):
            self.verify(checkout_root=self.tmp / "nope")


class EmitGitIdentityTests(AnchorFixture):
    """Generation must itself be bound to the original worktree: no parent repository, no GIT_* redirect, a real commit."""

    def stripped_copy_under_parent_repo(self):
        parent = self.tmp / "parent"
        parent.mkdir()
        git(parent, "init", "-q")
        (parent / "f").write_text("x")
        git(parent, "add", "-A")
        git(parent, "commit", "-q", "-m", "unrelated")
        child = parent / "checkout-copy"
        shutil.copytree(self.copy, child, ignore=shutil.ignore_patterns(".git"))
        return child

    def emit_from(self, repo, validator=VALIDATOR):
        return anc.emit_anchor(repo, validator, binary_path=str(self.exe), binary_sha256=self.exe_sha, binary_size=self.exe.stat().st_size)

    def test_emit_under_an_unrelated_parent_repository_is_blocked(self):
        child = self.stripped_copy_under_parent_repo()
        with self.assertRaisesRegex(Blocked, "top level"):
            self.emit_from(child)
        out = self.tmp / "never.json"
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            code = CLI.main(["--emit-anchor", str(out), "--repo", str(child), "--binary-path", str(self.exe), "--binary-sha256", self.exe_sha,
                             "--binary-size", "1"])
        self.assertEqual(code, 3)
        self.assertFalse(out.exists())

    def test_emit_ignores_git_dir_redirects_and_cannot_be_pointed_at_another_repo(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.orig / ".git"), "GIT_WORK_TREE": str(self.orig)}):
            with self.assertRaises(Blocked):  # the copy has only a dangling .git pointer
                self.emit_from(self.copy)
        with mock.patch.dict(os.environ, {"GIT_DIR": "/elsewhere/.git", "GIT_WORK_TREE": "/elsewhere", "GIT_CEILING_DIRECTORIES": "/"}):
            doc = self.emit_from(self.orig)  # the real worktree still works: GIT_* is scrubbed
            self.assertEqual(os.environ["GIT_DIR"], "/elsewhere/.git", "restored afterwards")
        self.assertEqual(doc["source_revision"], self.revision)

    def test_validator_reference_hash_runs_without_git_variables(self):
        seen = {}

        class Proxy:
            norm_path = staticmethod(VALIDATOR.norm_path)

            @staticmethod
            def source_tree_sha256(repo):
                seen.update({k: v for k, v in os.environ.items() if k.startswith("GIT_")})
                return VALIDATOR.source_tree_sha256(repo)

        with mock.patch.dict(os.environ, {"GIT_DIR": "/elsewhere", "GIT_INDEX_FILE": "/elsewhere/index"}):
            self.emit_from(self.orig, Proxy)
        self.assertEqual(seen, {})
        self.assertEqual(anc.scrubbed_git_env({"GIT_DIR": "x", "git_work_tree": "y", "PATH": "/bin"}), {"PATH": "/bin"})

    def test_repository_without_a_commit_is_blocked(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        git(empty, "init", "-q")
        with self.assertRaises(Blocked):
            self.emit_from(empty)


class HostFileTests(AnchorFixture):
    """The executable ITSELF (not only its directories) must be operator/root-owned, unwritable by others, and singly linked."""

    def test_group_or_other_writable_host_is_blocked_at_verify(self):
        for mode in (0o775, 0o757, 0o777):
            self.exe.chmod(mode)
            with self.assertRaisesRegex(Blocked, "group/other-writable"):
                self.verify()
        self.exe.chmod(0o755)
        self.verify()

    def test_hard_linked_host_is_blocked(self):
        alias = self.tmp / "alias-of-host"
        os.link(self.exe, alias)
        with self.assertRaisesRegex(Blocked, "hard links"):
            self.verify()
        alias.unlink()
        self.verify()

    @unittest.skipIf(os.getuid() == 0, "root-owned files are always accepted")
    def test_host_owned_by_someone_else_is_blocked(self):
        with self.assertRaisesRegex(Blocked, "owned by uid"):
            anc.check_host_file(self.exe, uid=os.getuid() + 1)

    def test_symlink_or_directory_is_not_a_host_file(self):
        link = self.tmp / "ln"
        os.symlink(self.exe, link)
        with self.assertRaises(Blocked):
            anc.check_host_file(link)
        with self.assertRaises(Blocked):
            anc.check_host_file(self.exe.parent)

    def test_guard_notices_mode_or_link_changes_after_verification(self):
        guard = anc.host_guard(self.exe, self.exe_sha, self.exe.stat().st_size)
        guard("now")
        self.exe.chmod(0o777)
        with self.assertRaisesRegex(Blocked, "writable"):
            guard("after chmod")
        self.exe.chmod(0o755)
        alias = self.tmp / "late-alias"
        os.link(self.exe, alias)
        with self.assertRaisesRegex(Blocked, "hard links"):
            guard("after link")


class UnanchoredPhysicalTests(AnchorFixture):
    def argv(self, *extra):
        return ["--case", "macos.none.ready-hygiene", "--host", str(self.exe), "--chrome", "/x", "--output", str(self.tmp / "o"), *extra]

    def test_no_anchor_is_blocked_even_in_a_perfect_git_checkout(self):
        sink = io.StringIO()
        with mock.patch.object(CLI, "CHECKOUT_ROOT", self.orig), \
                mock.patch.object(CLI, "load_validator", side_effect=AssertionError("no unpinned anchor may be computed")), \
                mock.patch.object(CLI.runner, "execute_case", side_effect=AssertionError("must not run")), \
                contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            code = CLI.main(self.argv())
        self.assertEqual(code, 2, sink.getvalue())
        doc = json.loads((self.tmp / "o" / "run.json").read_text())
        self.assertIn("PINNED anchor", doc["blocker"])
        self.assertNotIn("source", doc)
        self.assertNotIn("revision", json.dumps(doc.get("evaluator", {})))

    def test_no_anchor_is_blocked_in_a_copy_with_a_parent_repo_or_git_dir(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.orig / ".git"), "GIT_WORK_TREE": str(self.orig)}), \
                mock.patch.object(CLI, "CHECKOUT_ROOT", self.copy), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(CLI.main(self.argv()), 2)
        self.assertNotIn("source", json.loads((self.tmp / "o" / "run.json").read_text()))

    def test_the_retired_self_attestation_flag_is_refused(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(CLI.main(self.argv("--attest-built-from-this-checkout")), 3)
        self.assertFalse((self.tmp / "o").exists())

    def test_the_cli_has_no_unpinned_git_source_path(self):
        src = (HERE / "lifecycle-driver.py").read_text()
        self.assertNotIn("compute_anchor", src.split("def main")[0].split("def resolve_anchor")[1])


class PlantedBytecodeTests(AnchorFixture):
    def test_a_planted_unchecked_pyc_is_not_executed_by_the_driver(self):
        import py_compile
        import importlib.util as iu
        host_copy = self.tmp / "hostcopy"
        shutil.copytree(REAL_HOST, host_copy, ignore=shutil.ignore_patterns("__pycache__", "evidence", "target"))
        victim = host_copy / "scripts" / "accdrv" / "errors.py"
        evil = self.tmp / "evil_errors.py"
        evil.write_text("print('PLANTED-BYTECODE-RAN')\n" + victim.read_text())
        with mock.patch.object(sys, "pycache_prefix", None):  # this process redirected its own cache; the copy's lives beside the source
            pyc = iu.cache_from_source(str(victim))
        Path(pyc).parent.mkdir(parents=True, exist_ok=True)
        py_compile.compile(str(evil), cfile=pyc, doraise=True, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        script = host_copy / "scripts" / "lifecycle-driver.py"
        # sanity: without the driver's redirect the planted file really is picked up by an ordinary interpreter
        probe = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); from accdrv import errors",
                                str(host_copy / "scripts")], capture_output=True, text=True)
        self.assertIn("PLANTED-BYTECODE-RAN", probe.stdout, "test setup: the planted pyc must be effective without the redirect")
        run = subprocess.run([sys.executable, "-I", "-B", str(script), "--print-origin"], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn("PLANTED-BYTECODE-RAN", run.stdout + run.stderr)
        self.assertIn("origin", run.stdout)

    def test_driver_sets_a_fresh_pycache_prefix_and_no_bytecode_writing(self):
        self.assertTrue(CLI.sys.dont_write_bytecode)
        self.assertTrue(sys.pycache_prefix and "veles-accdrv-pyc-" in sys.pycache_prefix)
        self.assertTrue(os.path.isdir(sys.pycache_prefix))


class BinaryBindingTests(AnchorFixture):
    """The executable that was hashed is the one that is recorded, codesigned, launched and re-checked."""

    def setUp(self):
        super().setUp()
        import test_driver as td
        self.td = td
        self.mac = self.tmp / "bundle" / "VelesOtp23PoC.app" / "Contents" / "MacOS" / "veles-native-host"
        self.mac.parent.mkdir(parents=True)
        for d in self.mac.parents:
            if d == self.tmp.parent:
                break
            d.chmod(0o755)
        self.mac.write_bytes(b"\xcf\xfa\xed\xfe " + td.ORIGIN.encode() + b" original")
        self.mac.chmod(0o755)
        data = self.mac.read_bytes()
        self.good = hashlib.sha256(data).hexdigest()
        self.anchor = {**td.FAKE_ANCHOR, "binary_path": str(self.mac), "binary_path_platform": "posix", "binary_sha256": self.good,
                       "binary_size": len(data)}

    def swap(self):
        self.mac.write_bytes(b"\xcf\xfa\xed\xfe " + self.td.ORIGIN.encode() + b" SWAPPED!")

    def execute(self, out="o", **kw):
        return runner.execute_case(CASES["macos.none.ready-hygiene"], self.mac, "/chrome", self.tmp / out, self.anchor, family="macos",
                                   temp_root=str(self.tmp), **kw)

    def test_swap_between_verification_and_recording_is_blocked(self):
        self.swap()
        doc = self.execute(session_factory=lambda *a, **k: self.fail("must not launch"), run=self.td.GOOD_RUN if hasattr(self.td, "GOOD_RUN") else None)
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("swapped after verification", doc["blocker"])
        self.assertNotIn("source", doc, "no identity is recorded for bytes that were not the anchored ones")

    def test_swap_during_codesign_is_blocked_before_any_launch(self):
        base = self.td.RunnerLike.runner_for if hasattr(self.td, "RunnerLike") else None
        calls = []

        def run(argv):
            calls.append(argv[:2])
            if argv[:2] == ["codesign", "-dvv"]:
                self.swap()  # the file codesign is describing is no longer the anchored one
                return 0, "", self.td.GOOD_CODESIGN
            if "--verify" in argv:
                return 0, "", ""
            Path(argv[2].split("=", 1)[1] + "0").write_bytes(b"LEAF")
            return 0, "", ""

        doc = self.execute(run=run, session_factory=lambda *a, **k: self.fail("must not launch"))
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("after codesign", doc["blocker"])
        self.assertIn("differs from the anchored sha256", doc["blocker"])

    def test_swap_immediately_before_launch_is_blocked_by_the_session_hook(self):
        captured = {}

        def factory(chrome, root, home, env, before_launch=None, **kw):
            captured["hook"] = before_launch
            raise Blocked("stop here; only the hook is under test")

        def run(argv):
            if argv[:2] == ["codesign", "-dvv"]:
                return 0, "", self.td.GOOD_CODESIGN
            if "--verify" in argv:
                return 0, "", ""
            Path(argv[2].split("=", 1)[1] + "0").write_bytes(b"LEAF")
            return 0, "", ""

        self.execute(run=run, session_factory=factory)
        self.assertIsNotNone(captured.get("hook"))
        self.swap()
        with self.assertRaisesRegex(Blocked, "immediately before launch"):
            captured["hook"](self.tmp / "profile")

    def test_swap_during_authenticode_check_is_blocked(self):
        win = self.tmp / "winbin" / "veles-native-host.exe"
        win.parent.mkdir()
        win.parent.chmod(0o755)
        win.write_bytes(b"MZ " + self.td.ORIGIN.encode() + b" original")
        win.chmod(0o755)
        data = win.read_bytes()
        anchor = {**self.anchor, "binary_path": str(win), "binary_sha256": hashlib.sha256(data).hexdigest(), "binary_size": len(data)}

        def run(argv):
            win.write_bytes(b"MZ " + self.td.ORIGIN.encode() + b" SWAPPED!!")
            return 0, "Valid\n", ""

        doc = runner.execute_case(CASES["windows.none.ready-hygiene"], win, "/chrome", self.tmp / "ow", anchor, family="windows", run=run,
                                  session_factory=lambda *a, **k: self.fail("must not launch"), temp_root=str(self.tmp))
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("after Authenticode", doc["blocker"])

    def test_connect_native_and_every_reconnect_are_guarded(self):
        from accdrv.lifecycle import Ctx
        calls = []

        class Chrome:
            def js(self, session, expression, timeout=60):
                calls.append(expression)
                return None

        guard = anc.host_guard(self.mac, self.good, self.anchor["binary_size"])
        ctx = Ctx(CASES["macos.none.ready-hygiene"], mock.Mock(), chrome=Chrome(), guard=guard)
        st = {"session": "S"}
        ctx.js(st, "velesAcceptance.connect('h')")
        ctx.js(st, "velesAcceptance.hello()")
        self.assertEqual(len(calls), 2)
        self.swap()
        ctx.js(st, "velesAcceptance.hello()")  # not a connect: not guarded
        ctx.js(st, "velesAcceptance.connectDevice({device_handle: 'p'})")  # phone link, not the native port
        self.assertEqual(len(calls), 4)
        for _ in range(2):  # the first connect and any later reconnect both refuse
            with self.assertRaisesRegex(Blocked, "connect_native/reconnect"):
                ctx.js(st, "velesAcceptance.connect('h')")
        self.assertEqual(len(calls), 4, "Chrome must never be asked to connect (and so exec the host) after a swap")

    def test_the_real_connect_step_and_runner_wire_the_guard(self):
        from accdrv import lifecycle
        calls = []

        class Chrome:
            def open_page(self, url):
                return "T", "S"

            def js(self, session, expression, timeout=60):
                calls.append(expression)
                return True if expression == "typeof window.velesAcceptance === 'object'" else None

        ctx = lifecycle.Ctx(CASES["macos.none.ready-hygiene"], mock.Mock(), chrome=Chrome(), ext_id="e", host_name="h",
                            guard=anc.host_guard(self.mac, self.good))
        self.swap()
        with self.assertRaises(Blocked):
            lifecycle.connect_native(ctx, {})
        self.assertFalse([c for c in calls if "velesAcceptance.connect(" in c],
                         "Chrome must never be asked to connect (and so exec the host) after a swap")
        self.assertIn("guard=guard", (HERE / "accdrv" / "runner.py").read_text())

    def test_guarded_popen_refuses_to_start_a_swapped_executable(self):
        launched = []
        guard = anc.host_guard(self.mac, self.good, self.anchor["binary_size"])
        popen = runner.guarded_popen(lambda *a, **k: launched.append(a) or "proc", lambda where: guard(where))
        self.assertEqual(popen(["x"]), "proc")
        self.swap()
        with self.assertRaises(Blocked):
            popen(["y"])
        self.assertEqual(len(launched), 1)

    def test_post_run_recheck_is_against_the_anchor_not_a_second_read(self):
        class Rec:
            fields = {"binary": {"sha256": "ignored"}}
            blocked = []

            def block(self, m):
                self.blocked.append(m)
        rec = Rec()
        runner._rehash_check(self.mac, self.good)(rec)
        self.assertEqual(rec.blocked, [])
        self.swap()
        runner._rehash_check(self.mac, self.good)(rec)
        self.assertEqual(len(rec.blocked), 1)

    def test_host_guard_fails_closed_when_the_file_disappears(self):
        guard = anc.host_guard(self.mac, self.good)
        self.mac.unlink()
        with self.assertRaises(Blocked):
            guard("x")


class CasesBindingTests(AnchorFixture):
    def argv(self, *extra):
        return ["--case", "macos.none.ready-hygiene", "--host", str(self.exe), "--chrome", "/x", "--output", str(self.tmp / "o"), *extra]

    def test_alternate_cases_file_is_refused_with_an_anchor_even_if_byte_identical(self):
        alt = self.tmp / "alt-cases.json"
        shutil.copyfile(REAL_HOST / "acceptance" / "cases.json", alt)
        with mock.patch.object(CLI, "CHECKOUT_ROOT", self.copy), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(CLI.main(self.argv("--cases", str(alt), "--anchor", str(self.anchor_path), "--anchor-sha256", self.pin)), 3)
        self.assertFalse((self.tmp / "o").exists())

    def test_the_case_in_use_must_equal_the_one_in_the_verified_bytes(self):
        cases = json.loads((REAL_HOST / "acceptance" / "cases.json").read_text())
        for c in cases["cases"]:
            if c["id"] == "macos.none.ready-hygiene":
                c["requires_signing"] = not c["requires_signing"]
        verified = {"cases_json_bytes": json.dumps(cases).encode()}
        args = mock.Mock(anchor="a", anchor_sha256="b", attest_built_from_this_checkout=False, cases=str(REAL_HOST / "acceptance" / "cases.json"))
        with mock.patch.object(CLI.anchor_mod, "verify_anchor", return_value=verified):
            with self.assertRaisesRegex(Blocked, "case definition"):
                CLI.resolve_anchor(args, self.exe, CASES["macos.none.ready-hygiene"])

    def test_default_cases_with_a_valid_anchor_is_accepted(self):
        args = mock.Mock(anchor=str(self.anchor_path), anchor_sha256=self.pin, attest_built_from_this_checkout=False,
                         cases=str(REAL_HOST / "acceptance" / "cases.json"))
        with mock.patch.object(CLI, "CHECKOUT_ROOT", self.copy):
            anchor, attest = CLI.resolve_anchor(args, self.exe, CASES["macos.none.ready-hygiene"])
        self.assertEqual(anchor["source_revision"], self.revision)
        self.assertFalse(attest)


class DocumentedTrustBoundaryTests(unittest.TestCase):
    def test_no_adversarial_authentication_is_claimed(self):
        doc = (HERE / "lifecycle-driver.py").read_text() + (HERE / "accdrv" / "anchor.py").read_text()
        for needle in ("NOT adversarial authentication", "does NOT\nauthenticate a hostile copy", "check-to-exec window"):
            self.assertIn(needle, doc)
        self.assertTrue(any("trust boundary" in g for g in CLI.GAPS))
        self.assertTrue(any("driver-source trust vs binary trust" in g and "st_nlink==1" in g and "every reconnect" in g for g in CLI.GAPS))
        self.assertTrue(any("bytecode" in g for g in CLI.GAPS))

    def test_physical_driver_stays_non_executable_and_rows_blocked(self):
        pm = load("pm_for_anchor_tests", "physical-matrix.py")
        self.assertFalse(any(d.executable for d in pm.DRIVERS.values()))
        matrix = json.loads((REAL_HOST / "acceptance" / "matrix.json").read_text())
        self.assertEqual(matrix["decision"], "OPEN")
        self.assertTrue(all(r["status"] in ("BLOCKED", "NOT RUN") for r in matrix["rows"]))


class DriverWiringTests(AnchorFixture):
    def run_main(self, *args, case="macos.none.ready-hygiene"):
        out = self.tmp / "out"
        base = ["--case", case, "--host", str(self.exe), "--chrome", "/nonexistent", "--output", str(out)]
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            code = CLI.main([*base, *args])
        return code, out, sink.getvalue()

    def patch_root(self, root):
        return mock.patch.object(CLI, "CHECKOUT_ROOT", Path(root))

    def test_no_git_and_no_anchor_writes_a_blocked_record_without_source_claims(self):
        with self.patch_root(self.copy):
            code, out, text = self.run_main()
        self.assertEqual(code, 2, text)
        doc = json.loads((out / "run.json").read_text())
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("--anchor", doc["blocker"])
        self.assertNotIn("source", doc)
        self.assertNotIn("revision", json.dumps(doc.get("evaluator", {})))
        self.assertEqual(doc["captures"], [])

    def test_anchor_flags_must_come_as_a_pair_and_exclude_self_attestation(self):
        with self.patch_root(self.copy):
            self.assertEqual(self.run_main("--anchor", str(self.anchor_path))[0], 3)
            self.assertEqual(self.run_main("--anchor-sha256", self.pin)[0], 3)
            code, out, _ = self.run_main("--anchor", str(self.anchor_path), "--anchor-sha256", self.pin, "--attest-built-from-this-checkout")
            self.assertEqual(code, 3)
            self.assertFalse(out.exists())

    def test_mutated_source_under_a_valid_pin_blocks_before_anything_runs(self):
        (self.copy / "native-bridge/host/src/main.rs").write_text("tampered")
        with self.patch_root(self.copy), mock.patch.object(CLI.runner, "execute_case", side_effect=AssertionError("must not run")):
            code, out, _ = self.run_main("--anchor", str(self.anchor_path), "--anchor-sha256", self.pin)
        self.assertEqual(code, 2)
        doc = json.loads((out / "run.json").read_text())
        self.assertEqual(doc["declared_status"], "BLOCKED")
        self.assertIn("main.rs", doc["blocker"])
        self.assertNotIn("source", doc)

    def test_binary_path_mismatch_blocks_with_a_record(self):
        other = self.tmp / "m2" / "VelesOtp23PoC.app" / "Contents" / "MacOS" / "veles-native-host"
        other.parent.mkdir(parents=True)
        shutil.copyfile(self.exe, other)
        out = self.tmp / "out"
        sink = io.StringIO()
        with self.patch_root(self.copy), contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            code = CLI.main(["--case", "macos.none.ready-hygiene", "--host", str(other), "--chrome", "/x", "--output", str(out),
                             "--anchor", str(self.anchor_path), "--anchor-sha256", self.pin])
        self.assertEqual(code, 2)
        self.assertIn("path", json.loads((out / "run.json").read_text())["blocker"])

    def test_valid_anchor_reaches_execute_case_with_the_verified_anchor_and_attestation(self):
        seen = {}

        def fake_execute(case, host, chrome, out, anchor, **kw):
            seen.update(anchor=anchor, kw=kw)
            return {"declared_status": "BLOCKED", "blocker": "x"}

        with self.patch_root(self.copy), mock.patch.object(CLI.runner, "execute_case", fake_execute):
            code, _, _ = self.run_main("--anchor", str(self.anchor_path), "--anchor-sha256", self.pin)
        self.assertEqual(code, 2)
        self.assertEqual(seen["anchor"]["source_revision"], self.revision)
        self.assertTrue(seen["anchor"]["externally_attested"])


class EmitCliTests(AnchorFixture):
    def test_emit_subcommand_prints_the_pin_and_writes_the_anchor(self):
        out = self.tmp / "emitted.json"
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            code = CLI.main(["--emit-anchor", str(out), "--repo", str(self.orig), "--binary-path", str(self.exe), "--binary-sha256", self.exe_sha,
                             "--binary-size", str(self.exe.stat().st_size)])
        self.assertEqual(code, 0, sink.getvalue())
        data = out.read_bytes()
        self.assertEqual(json.loads(sink.getvalue())["anchor_sha256"], hashlib.sha256(data).hexdigest())
        self.assertIsNone(json.loads(data)["build_attestation"])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            again = CLI.main(["--emit-anchor", str(out), "--repo", str(self.orig), "--binary-path", str(self.exe), "--binary-sha256", self.exe_sha,
                              "--binary-size", "1"])
        self.assertEqual(again, 3, "never overwritten")
        self.assertEqual(out.read_bytes(), data)


class ChromeFlagTests(unittest.TestCase):
    def test_launch_args_are_exactly_the_reviewed_set(self):
        args = achrome.launch_args("/Applications/Chrome.app/Contents/MacOS/Chrome", "/run/chrome-profile")
        self.assertEqual(args, ["/Applications/Chrome.app/Contents/MacOS/Chrome", "--user-data-dir=/run/chrome-profile", "--remote-debugging-port=0",
                                "--enable-unsafe-extension-debugging", "--use-mock-keychain", "--no-first-run", "--no-default-browser-check",
                                "--disable-sync", "--window-size=1000,700", "about:blank"])

    def test_mock_keychain_and_isolation_flags(self):
        args = achrome.launch_args("/c", "/p")
        self.assertEqual(args.count("--use-mock-keychain"), 1)
        self.assertEqual([a for a in args if a.startswith("--user-data-dir")], ["--user-data-dir=/p"])
        self.assertEqual(args[1], "--user-data-dir=/p", "the disposable profile flag stays directly after the executable")
        for forbidden in ("--profile-directory", "--load-extension", "--remote-debugging-address", "--no-sandbox", "--disable-web-security",
                          "--password-store", "--headless", "--remote-allow-origins", "--disable-extensions", "--incognito"):
            self.assertFalse([a for a in args if a.startswith(forbidden)], forbidden)
        self.assertTrue(args[2:] and all(a.startswith("--") or a == "about:blank" for a in args[1:]))
        self.assertTrue(all(a.startswith("--remote-debugging-port=0") for a in args if "remote-debugging" in a))

    def test_session_launches_with_mock_keychain_on_the_disposable_profile_only(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(os.path.realpath(td))
            root, home = td / "run", td / "home"
            root.mkdir()
            chrome = td / "chrome"
            chrome.write_bytes(b"c")
            calls = []

            class Proc:
                def poll(self):
                    return None

            def popen(args, env=None):
                calls.append(args)
                (Path(args[1].split("=", 1)[1]) / "DevToolsActivePort").write_text("1\n/devtools/browser/x\n")
                return Proc()

            cdp = mock.Mock()
            cdp.call.return_value = {"product": "Chrome/1"}
            s = achrome.ChromeSession(chrome, root, home, {"PATH": "/bin"}, popen=popen, make_cdp=lambda p, path: cdp, sleep=lambda s: None, polls=3)
            s.start()
            self.assertIn("--use-mock-keychain", calls[0])
            self.assertEqual(calls[0][1], f"--user-data-dir={root / 'chrome-profile'}")
            self.assertNotIn(str(home), " ".join(calls[0]))
            self.assertIn("--use-mock-keychain", [a for a in s.args])
            expected = hashlib.sha256("\0".join(s.args).encode()).hexdigest()
            self.assertEqual(s.provenance()["launch_args_sha256"], expected)


class PersonalProfileTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(os.path.realpath(self._td.name))
        self.home = self.tmp / "Users" / "me"

    def test_every_personal_mac_chrome_variant_is_listed(self):
        dirs = reg.default_chrome_dirs(str(self.home))
        for variant in ("Chrome", "Chrome Beta", "Chrome Canary", "Chromium"):
            base = "Google" if variant != "Chromium" else None
            suffix = f"Library/Application Support/{base}/{variant}" if base else "Library/Application Support/Chromium"
            self.assertIn(f"{self.home}/{suffix}", dirs, variant)

    def test_profile_inside_or_equal_to_or_above_personal_chrome_is_refused(self):
        personal = self.home / "Library" / "Application Support" / "Google" / "Chrome"
        for run_root, profile in ((personal / "run", personal / "run" / "chrome-profile"), (personal.parent.parent, personal.parent)):
            profile.mkdir(parents=True, exist_ok=True)
            with self.assertRaises(Refused, msg=str(profile)):
                reg.ensure_disposable_profile(profile, run_root, str(self.home))
        canary = self.home / "Library" / "Application Support" / "Google" / "Chrome Canary" / "r"
        (canary / "chrome-profile").mkdir(parents=True)
        with self.assertRaises(Refused):
            reg.ensure_disposable_profile(canary / "chrome-profile", canary, str(self.home))

    def test_symlink_into_personal_profile_and_nonempty_profiles_are_refused(self):
        personal = self.home / "Library" / "Application Support" / "Google" / "Chrome"
        personal.mkdir(parents=True)
        root = self.tmp / "run"
        root.mkdir()
        os.symlink(personal, root / "chrome-profile")
        with self.assertRaises(Refused):
            reg.ensure_disposable_profile(root / "chrome-profile", root, str(self.home))
        (root / "chrome-profile").unlink()
        (root / "chrome-profile").mkdir()
        (root / "chrome-profile" / "Default").mkdir()
        with self.assertRaises(Refused):
            reg.ensure_disposable_profile(root / "chrome-profile", root, str(self.home))

    def test_session_refuses_to_launch_when_the_run_root_is_inside_a_personal_profile(self):
        personal = self.home / "Library" / "Application Support" / "Google" / "Chrome" / "run"
        personal.mkdir(parents=True)
        chrome = self.tmp / "chrome"
        chrome.write_bytes(b"c")
        popen = mock.Mock()
        s = achrome.ChromeSession(chrome, personal, str(self.home), {}, popen=popen, make_cdp=mock.Mock(), sleep=lambda s: None, polls=1)
        with self.assertRaises(Refused):
            s.start()
        popen.assert_not_called()

    def test_chrome_executable_must_be_explicit_absolute_real_file(self):
        for bad in ("chrome", "", str(self.tmp / "missing")):
            with self.assertRaises(Refused, msg=bad):
                achrome.ChromeSession(bad, self.tmp, str(self.home), {})


if __name__ == "__main__":
    unittest.main()
