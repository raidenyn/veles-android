"""Mac release-gate unit (parent P0-A decision 2026-10-28, after the Opus preflight).

The approved macOS fixture (`e7c716c5…`, source cd971f0) exits before any awaited
`release_device`, so a Mac host trace never contains `conn N released` lines: a host-trace
release-count gate would fail every Mac teardown regardless of reality. The parent chose options
P0-A: `scenario_matrix.release_gate` returns 2 ONLY on Windows (behaviour unchanged there, bounded
join unchanged), None on macOS — where `trace_released_count` stays recorded honestly (typically 0)
and every macOS acceptance scenario must additionally carry independent phone-side release proof
via `scripts/mac_release_bound.py`. Windows cannot be constructed on Linux (chrome_validate needs
Windows env), so the gate is asserted through each adapter class's ACTUAL `platform` attribute,
exactly what the callsites read; a `check_teardown` pairing documents the Mac acceptance shape
(0 releases tolerated by the trace gate; the phone-side proof is the release evidence).

Usage:  python3 scripts/test_mac_release_gate.py
"""

import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import scenario_matrix
from scenario_matrix import MacAdapter, WindowsAdapter, core


class ReleaseGateTests(unittest.TestCase):
    def test_gate_is_windows_only_with_the_real_adapter_attributes(self):
        self.assertEqual(WindowsAdapter.platform, "windows")
        self.assertEqual(scenario_matrix.release_gate(WindowsAdapter.platform), 2,
                         "Windows behaviour unchanged: still min_released=2")
        adapter = MacAdapter()  # constructible off-darwin (helpers do real work only at call time)
        self.assertEqual(adapter.platform, "mac")
        self.assertIsNone(scenario_matrix.release_gate(adapter.platform),
                         "macOS: no host-trace release-count gate; recorded count carries instead")

    def test_gate_rejects_other_platforms(self):
        self.assertIsNone(scenario_matrix.release_gate("linux"), "an unknown platform must not silently demand 2")
        self.assertIsNone(scenario_matrix.release_gate(""))
        self.assertIsNone(scenario_matrix.release_gate(None))

    def test_every_teardown_callsite_uses_release_gate(self):
        text = (HERE / "scenario_matrix.py").read_text(encoding="utf-8")
        callsites = text.count("min_released=release_gate(adapter.platform)")
        self.assertEqual(callsites, 5, "shutdown x2, port-disconnect, tab-close, direct stdin: all gated")
        self.assertNotIn("min_released=2,", text.replace("still min_released=2 +", ""),
                         "no ungated min_released=2 callsite remains (documented prose line allowed)")

    def test_mac_trace_gate_tolerates_zero_release_lines_while_counts_stay_recorded(self):
        # The Mac acceptance shape: trace checks (mechanism, exit, drained) hold with NO release
        # lines at all — the RELEASE evidence is then the phone-side burst (mac_release_bound.py);
        # a Windows-shaped gate still rejects 0 release lines. Fake trace payloads, frozen core.
        parsed = core.parse_trace(
            "[hello pid=1] host start\n"
            "[1ms pid=1] out hello type=ready\n"
            "[2ms pid=1] out send ...\n"
            "[3ms pid=1] in message ...\n"
            "[9ms pid=1] exit code=0 stdout_drained=true\n")
        self.assertEqual(core.check_teardown(parsed, "disconnect", min_released=None), [],
                         "mac gate: 0 traced releases + own exit + stdin_closed mechanism = no trace problem")
        problems = core.check_teardown(parsed, "disconnect", min_released=2)
        self.assertTrue(any("release line" in p for p in problems),
                        "windows gate unchanged: 0 traced releases is a problem")


if __name__ == "__main__":
    unittest.main()