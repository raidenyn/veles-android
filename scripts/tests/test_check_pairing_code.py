"""Behavioural tests for the pairing-code logging guards.

`android/scripts/check-pairing-code.sh` and `web-extension/scripts/check-pairing-code.sh` hold today's
inline `android / lint` and `web-extension / lint` pairing-code greps, their `::error::` lines and
their exit status. Each test copies one script into a temporary fixture tree, creates the debug
surface it scans and runs it with the fixture root as the working directory, exactly as CI runs it
from the repository root.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(SCRIPTS)
ANDROID_SCRIPT = os.path.join(REPO, "android", "scripts", "check-pairing-code.sh")

ANDROID_MESSAGE = (
    "A pairing-code value reaches a logger, broadcast result or notification in the Android debug surface."
)


def source(relative_path, text):
    return relative_path, text


def run_guard(script, files):
    fixture = tempfile.mkdtemp(prefix="veles-pairing-code-")
    try:
        for relative_path, text in files:
            path = os.path.join(fixture, relative_path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        copied = os.path.join(fixture, os.path.basename(script))
        shutil.copy2(script, copied)
        return subprocess.run(
            ["sh", copied],
            cwd=fixture,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
    finally:
        shutil.rmtree(fixture, True)


class AndroidPairingCodeTest(unittest.TestCase):
    def test_clean_tree_exits_0(self):
        result = run_guard(
            ANDROID_SCRIPT,
            [
                source(
                    "android/app/src/debug/java/me/nagaev/veles/PairingReadout.kt",
                    "package me.nagaev.veles\n\nobject PairingReadout {\n"
                    "    val label = \"pairing code shown on screen\"\n}\n",
                )
            ],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_sink_with_code_exits_1(self):
        result = run_guard(
            ANDROID_SCRIPT,
            [
                source(
                    "android/app/src/debug/java/me/nagaev/veles/PairingLeak.kt",
                    "package me.nagaev.veles\n\nobject PairingLeak {\n"
                    "    fun log(state: PairingDebugState) = Log.d(\"pair\", state.code)\n}\n",
                )
            ],
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(ANDROID_MESSAGE, result.stdout)

    def test_code_without_sink_exits_0(self):
        result = run_guard(
            ANDROID_SCRIPT,
            [
                source(
                    "android/app/src/debug/java/me/nagaev/veles/PairingState.kt",
                    "package me.nagaev.veles\n\nobject PairingState {\n"
                    "    val pairing = PairingCode.generate()\n}\n",
                )
            ],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
