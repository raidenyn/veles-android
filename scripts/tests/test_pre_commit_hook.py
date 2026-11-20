"""The pre-commit hook must scan staged changes and never print a secret.

Each case builds a throwaway git repository whose path contains a space, isolates
HOME, the system config and PATH, and runs the project's hook by absolute path.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
HOOK = PROJECT / ".githooks" / "pre-commit"
GITLEAKS = os.environ.get("VELES_GITLEAKS", "")
REAL_GITLEAKS = bool(GITLEAKS) and Path(GITLEAKS).is_file() and os.access(GITLEAKS, os.X_OK)
WINDOWS = sys.platform == "win32"

# Assembled from pieces at runtime so no credential-shaped literal is committed.
# The body uses the AWS key alphabet [A-Z2-7] and clears the rule's entropy floor.
FAKE_KEY = "AKIA" + "".join(("T7QM2", "XZP4", "NRD6", "WLY"))


@unittest.skipUnless(shutil.which("sh") and shutil.which("git"), "needs sh and git")
class PreCommitHookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="veles pre-commit env "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "git").symlink_to(shutil.which("git"))
        self.env = {
            "HOME": str(self.tmp),
            "GIT_CONFIG_NOSYSTEM": "1",
            "PATH": str(bin_dir),
        }

    def make_repo(self):
        repo = Path(tempfile.mkdtemp(prefix="veles pre-commit repo "))
        self.addCleanup(shutil.rmtree, repo, True)
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.name", "Veles Test")
        self.git(repo, "config", "user.email", "test@example.invalid")
        shutil.copyfile(PROJECT / ".gitleaks.toml", repo / ".gitleaks.toml")
        return repo

    def git(self, repo, *args, cwd=None):
        return subprocess.run(
            [shutil.which("git"), *args],
            cwd=str(cwd or repo), env=self.env, capture_output=True, text=True, check=True,
        )

    def run_hook(self, repo, gitleaks=None, cwd=None):
        env = dict(self.env)
        if gitleaks is not None:
            env["VELES_GITLEAKS"] = str(gitleaks)
        return subprocess.run(
            [shutil.which("sh"), str(HOOK)],
            cwd=str(cwd or repo), env=env, capture_output=True, text=True,
        )

    @unittest.skipUnless(REAL_GITLEAKS, "needs VELES_GITLEAKS naming an executable gitleaks")
    def test_refused(self):
        repo = self.make_repo()
        sub = repo / "nested"
        sub.mkdir()
        (sub / "config.ini").write_text("aws_access_key_id = %s\n" % FAKE_KEY)
        self.git(repo, "add", "config.ini", cwd=sub)

        result = self.run_hook(repo, GITLEAKS, cwd=sub)

        self.assertEqual(result.returncode, 1, "the hook must block with exit code 1")
        output = result.stdout + result.stderr
        self.assertTrue("aws-access-token" in output, "the report must name the aws-access-token rule")
        self.assertTrue("config.ini" in output, "the report must name the staged file")
        self.assertTrue("--no-verify" in output, "the block message must name the bypass")
        self.assertFalse(FAKE_KEY in result.stdout, "the fake key must not appear in stdout")
        self.assertFalse(FAKE_KEY in result.stderr, "the fake key must not appear in stderr")

    @unittest.skipUnless(REAL_GITLEAKS, "needs VELES_GITLEAKS naming an executable gitleaks")
    def test_clean_passes(self):
        repo = self.make_repo()
        (repo / "hello.txt").write_text("hello\n")
        self.git(repo, "add", "hello.txt")

        result = self.run_hook(repo, GITLEAKS)

        self.assertEqual(result.returncode, 0, "a clean staged change must pass")

    @unittest.skipUnless(REAL_GITLEAKS, "needs VELES_GITLEAKS naming an executable gitleaks")
    def test_staged_deletion_passes(self):
        repo = self.make_repo()
        (repo / "gone.txt").write_text("goodbye\n")
        self.git(repo, "add", "gone.txt")
        self.git(repo, "commit", "-q", "-m", "test: add a file", "--no-verify")
        self.git(repo, "rm", "-q", "gone.txt")

        result = self.run_hook(repo, GITLEAKS)

        self.assertEqual(result.returncode, 0, "a staged deletion must pass")

    def test_missing_binary_warns(self):
        repo = self.make_repo()
        (repo / "hello.txt").write_text("hello\n")
        self.git(repo, "add", "hello.txt")
        stub = repo / "build" / "release-tools" / "gitleaks" / "gitleaks"
        stub.parent.mkdir(parents=True)
        stub.write_text("#!/bin/sh\nexit 3\n")
        stub.chmod(0o755)
        self.assertTrue(os.access(stub, os.X_OK))

        result = self.run_hook(repo, self.tmp / "nope")

        self.assertEqual(result.returncode, 0, "a missing binary must warn and allow the commit")
        self.assertIn("./gradlew installGitleaks", result.stderr)

        if WINDOWS:
            return
        noexec = self.tmp / "gitleaks-not-executable"
        noexec.write_text("#!/bin/sh\nexit 3\n")
        noexec.chmod(0o644)
        self.assertFalse(os.access(noexec, os.X_OK))

        result = self.run_hook(repo, noexec)

        self.assertEqual(result.returncode, 0, "a missing binary must warn and allow the commit")
        self.assertIn("./gradlew installGitleaks", result.stderr)

    @unittest.skipIf(WINDOWS, "needs a POSIX shell stub")
    def test_tool_error_warns(self):
        repo = self.make_repo()
        (repo / "hello.txt").write_text("hello\n")
        self.git(repo, "add", "hello.txt")
        stub = self.tmp / "gitleaks-error"
        stub.write_text("#!/bin/sh\nexit 2\n")
        stub.chmod(0o755)

        result = self.run_hook(repo, stub)

        self.assertEqual(result.returncode, 0, "a tool error must warn and allow the commit")
        self.assertIn("scan did not run", result.stderr, "the warning must say the scan did not run")
        self.assertIn("gitleaks exited 2", result.stderr, "the warning must name the exit code")


if __name__ == "__main__":
    unittest.main()
