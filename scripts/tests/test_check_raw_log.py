"""Behavioural tests for android/scripts/check-raw-log.sh.

The script holds today's inline `android.util.Log` CI guard: the same two greps, the same `::error::`
lines and the same exit status. Each test copies the script into a temporary fixture tree, creates the
sources the grep scans and runs it with the fixture root as the working directory, exactly as CI runs
it from the repository root.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(SCRIPTS)
SCRIPT = os.path.join(REPO, "android", "scripts", "check-raw-log.sh")

RAW_LOG_MESSAGE = (
    "Raw android.util.Log usage or wildcard import found outside LogSink.kt. Use VelesLog instead."
)
BARE_LOG_MESSAGE = "Bare Log.d/w/e/i/v call found outside LogSink.kt. Use VelesLog instead."


def source(relative_path, text):
    return relative_path, text


class CheckRawLogTest(unittest.TestCase):
    def run_guard(self, files):
        fixture = tempfile.mkdtemp(prefix="veles-raw-log-")
        self.addCleanup(shutil.rmtree, fixture, True)
        # Both scanned roots always exist in a real checkout; the fixture keeps them present so a
        # failure is a guard hit, never a missing-directory error.
        for directory in ("android/app/src/main", "android/app/src/debug"):
            os.makedirs(os.path.join(fixture, directory), exist_ok=True)
        for relative_path, text in files:
            path = os.path.join(fixture, relative_path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        copied = os.path.join(fixture, "check-raw-log.sh")
        shutil.copy2(SCRIPT, copied)
        return subprocess.run(
            ["sh", copied],
            cwd=fixture,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )

    def test_clean_tree_exits_0(self):
        result = self.run_guard(
            [
                source(
                    "android/app/src/main/java/me/nagaev/veles/LogSink.kt",
                    "package me.nagaev.veles\n\nobject LogSink\n",
                ),
                source(
                    "android/app/src/main/java/me/nagaev/veles/Clean.kt",
                    "package me.nagaev.veles\n\nobject Clean {\n    val message = \"Log-free\"\n}\n",
                ),
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_fully_qualified_log_exits_1(self):
        result = self.run_guard(
            [
                source(
                    "android/app/src/main/java/me/nagaev/veles/Leak.kt",
                    "package me.nagaev.veles\n\nval logger = android.util.Log\n",
                )
            ]
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("android/app/src/main/java/me/nagaev/veles/Leak.kt", result.stdout)
        self.assertIn(RAW_LOG_MESSAGE, result.stdout)

    def test_wildcard_util_import_exits_1(self):
        result = self.run_guard(
            [
                source(
                    "android/app/src/main/java/me/nagaev/veles/Wildcard.kt",
                    "package me.nagaev.veles\n\nimport android.util.*\n",
                )
            ]
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("android/app/src/main/java/me/nagaev/veles/Wildcard.kt", result.stdout)
        self.assertIn(RAW_LOG_MESSAGE, result.stdout)

    def test_bare_log_call_exits_1(self):
        result = self.run_guard(
            [
                source(
                    "android/app/src/main/java/me/nagaev/veles/Bare.kt",
                    "package me.nagaev.veles\n\nobject Bare {\n"
                    "    fun log() = Log.d(\"tag\", \"message\")\n}\n",
                )
            ]
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("android/app/src/main/java/me/nagaev/veles/Bare.kt", result.stdout)
        self.assertIn(BARE_LOG_MESSAGE, result.stdout)

    def test_logsink_exempt(self):
        result = self.run_guard(
            [
                source(
                    "android/app/src/main/java/me/nagaev/veles/LogSink.kt",
                    "package me.nagaev.veles\n\n"
                    "import android.util.Log\n\n"
                    "object LogSink {\n"
                    "    fun log() = Log.d(\"tag\", \"message\")\n}\n",
                )
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_debug_sources_scanned_by_both_greps(self):
        result = self.run_guard(
            [
                source(
                    "android/app/src/debug/java/me/nagaev/veles/DebugLeak.kt",
                    "package me.nagaev.veles\n\nval logger = android.util.Log\n",
                ),
                source(
                    "android/app/src/debug/java/me/nagaev/veles/DebugBare.kt",
                    "package me.nagaev.veles\n\nobject DebugBare {\n"
                    "    fun log() = Log.e(\"tag\", \"message\")\n}\n",
                ),
            ]
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("android/app/src/debug/java/me/nagaev/veles/DebugLeak.kt", result.stdout)
        self.assertIn("android/app/src/debug/java/me/nagaev/veles/DebugBare.kt", result.stdout)
        self.assertIn(RAW_LOG_MESSAGE, result.stdout)
        self.assertIn(BARE_LOG_MESSAGE, result.stdout)


if __name__ == "__main__":
    unittest.main()
