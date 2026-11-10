"""The commit-msg hook must find a Python 3 interpreter other than `python3`."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
HOOK = PROJECT / ".githooks" / "commit-msg"


@unittest.skipUnless(shutil.which("sh") and shutil.which("git"), "needs sh and git")
class CommitMsgHookTest(unittest.TestCase):
    def run_hook(self, shim_name, message="fix: a thing\n"):
        tmp = Path(tempfile.mkdtemp(prefix="veles-hook-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "git").symlink_to(shutil.which("git"))
        if shim_name:
            shim = bin_dir / shim_name
            shim.write_text('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)
            shim.chmod(0o755)
        msg = tmp / "msg"
        msg.write_text(message)
        env = {"PATH": str(bin_dir), "HOME": str(tmp)}
        return subprocess.run(
            [shutil.which("sh"), str(HOOK), str(msg)],
            cwd=PROJECT, env=env, capture_output=True, text=True,
        )

    def test_falls_back_to_python(self):
        result = self.run_hook("python")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_bad_message_through_fallback(self):
        result = self.run_hook("python", "not conventional\n")
        self.assertNotEqual(result.returncode, 0)

    def test_no_interpreter_gives_clear_message(self):
        result = self.run_hook(None)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Python 3.8+", result.stderr)
        self.assertIn("--no-verify", result.stderr)


if __name__ == "__main__":
    unittest.main()
