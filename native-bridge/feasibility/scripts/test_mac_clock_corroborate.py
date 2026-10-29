"""Targeted unit tests for the two minimum-fix-phase issues (2026-10-28), no broad fuzzing:

1. mac_clock_corroborate: the whole-seconds fallback stamp centring term (±500 ms) MUST be added to
   the component's uncertainty (reviewer-reported understatement); millisecond stamps carry no
   centring term.
2. macos_poc foreign-mount parsing: the `mounted by` owner token is bounded at the NEXT COMMA — a
   self-owned mount whose options carry a trailing element after the owner must NOT be classified
   foreign (F-7), while a foreign-owned mount keeps working (with or without trailing options).
"""

import pathlib
import sys
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mac_clock_corroborate as mcc
import macos_poc


def make_component(kind, count=None):
    """A fake mac component sample set of the given stamp class without touching ssh/adb."""
    rows = []
    started, finished = 1793204539.0, 1793204539.4  # 400 ms latency
    for _ in range(3):
        if kind == "ms":
            stamp, origin = 1793202259100, "ms_epoch"
        else:
            stamp, origin = 1793202259000 + 500, "whole_seconds_centred"
        rows.append({"started_ms": int(started * 1000), "finished_ms": int(finished * 1000),
                     "latency_ms": 400, "stamp_ms": stamp, "raw_text": str(stamp),
                     "stamp_origin": origin})
    return rows, rows


class CorroborateCentring(unittest.TestCase):
    def test_ms_stamps_carry_no_centring_term(self):
        summary = mcc.summarise(*make_component("ms"), "mac-minus-local")
        self.assertFalse(summary["whole_seconds_fallback_used"])
        self.assertEqual(summary["uncertainty_ms"], 200, "spread 0 + 400 ms latency/2, no centring")

    def test_whole_seconds_fallback_adds_the_500ms_centring_term(self):
        summary = mcc.summarise(*make_component("fallback"), "mac-minus-local")
        self.assertTrue(summary["whole_seconds_fallback_used"])
        self.assertEqual(summary["uncertainty_ms"], 700,
                         "spread 0 + 400/2 latency + 500 ms centring — not understated at 200")
        self.assertIn("±500 ms", summary["resolution"])


class MountedByParser(unittest.TestCase):
    def parse(self, line):
        with mock.patch.object(macos_poc.subprocess, "run",
                               lambda *a, **k: __import__("subprocess").CompletedProcess(
                                   ["/sbin/mount"], 0, stdout=line, stderr="")):
            macos_poc._FOREIGN_NULLFS_CACHE = None
            try:
                return macos_poc.foreign_nullfs_mounts()
            finally:
                macos_poc._FOREIGN_NULLFS_CACHE = None

    def current_user(self):
        import pwd, os
        return pwd.getpwuid(os.getuid()).pw_name

    def test_self_owned_with_trailing_option_is_not_foreign(self):
        line = (f"/Users/me/Downloads/App.app on /tmp/t1 "
                f"(nullfs, local, read-only, mounted by {self.current_user()}, someflag)\n")
        self.assertEqual(self.parse(line), set(),
                         "trailing option after the owner must not flip self-owned to foreign")

    def test_foreign_with_trailing_option_is_still_foreign(self):
        line = ("/Users/other/Downloads/App.app on /tmp/t2 "
                "(nullfs, local, read-only, mounted by someoneelse, someflag)\n")
        self.assertEqual(self.parse(line), {"/tmp/t2"})

    def test_foreign_without_trailing_option_parse_unchanged(self):
        line = ("/Users/other/Downloads/App.app on /tmp/t3 "
                "(nullfs, local, nodev, nosuid, read-only, nobrowse, mounted by someoneelse)\n")
        self.assertEqual(self.parse(line), {"/tmp/t3"})

    def test_sanitized_snapshot_preserves_trailing_options(self):
        line = ("/Users/other/Downloads/App.app on /tmp/t4 "
                "(nullfs, local, mounted by someoneelse, someflag)\n")
        lines = macos_poc._sanitized_nullfs_mount_lines(line, self.current_user())
        self.assertEqual(lines, ["on /tmp/t4 (nullfs, local, mounted by uid≠self, someflag)"])
        self.assertNotIn("someoneelse", lines[0])


if __name__ == "__main__":
    unittest.main()