"""Behavioural tests of the NSIS frontend's result handling (F12).

``nsis_model.py`` runs the REAL ``Function`` bodies of ``windows/installer.nsi`` over a small interpreted subset of
NSIS (there is no makensis here), with engine reports as fixtures. This replaces text-search checks as the
evidence for the informational-versus-complete distinction. It is not NSIS: NSIS compilation and execution remain
NOT RUN, and the interpreter's string semantics are a reading of the documentation.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import nsis_model  # noqa: E402

NSI = (HERE.parent / "windows" / "installer.nsi").read_text(encoding="utf-8")


def report(operation: str, state: str, *, failure: str | None = None, warnings: list[str] | None = None,
           usable: bool = True) -> str:
    """The engine's canonical one-line report (sorted keys, no whitespace), as ``cli::render_report`` writes it."""
    return json.dumps({"operation": operation, "state": state, "prior_usable": usable, "active_payload": None,
                       "failure": failure, "warnings": warnings or []}, sort_keys=True, separators=(",", ":")) + "\n"


class Outcome:
    def __init__(self, machine: nsis_model.Machine) -> None:
        self.guidance = machine.variables["Guidance"]
        self.succeeded = machine.variables["Succeeded"]
        self.level = machine.error_level
        self.shown = machine.boxes


def handle(operation: str, exit_code: int, text: str, *, uninstaller: bool = False) -> Outcome:
    machine = nsis_model.load(NSI)
    machine.variables["Operation"] = operation
    machine.stack = [text, str(exit_code)]  # ExecToStack: the exit code is on top, the output below it
    machine.call("un.HandleEngineResult" if uninstaller else "HandleEngineResult")
    return Outcome(machine)


MUTATIONS = (
    ("install", "install", "installed"), ("update", "install", "installed"), ("repair", "repair", "repaired"),
    ("rollback", "rollback", "rolled-back"), ("uninstall", "uninstall", "uninstalled"),
)


class CompleteOperations(unittest.TestCase):
    def test_a_clean_mutation_is_done_and_publishes_the_entry(self) -> None:
        for requested, operation, state in MUTATIONS:
            with self.subTest(requested):
                out = handle(requested, 0, report(operation, state))
                self.assertEqual((out.succeeded, out.level), ("1", 0), out.guidance)
                self.assertEqual(len(out.shown), 1)

    def test_a_same_version_install_rerun_reports_the_repair_it_performed(self) -> None:
        out = handle("install", 0, report("repair", "repaired"))
        self.assertEqual((out.succeeded, out.level), ("1", 0), out.guidance)

    def test_leftover_files_and_compatibility_warnings_are_never_dropped(self) -> None:
        plain = handle("uninstall", 0, report("uninstall", "uninstalled")).guidance
        left = handle("uninstall", 0, report("uninstall", "uninstalled", warnings=["leftover-files"]))
        self.assertEqual((left.succeeded, left.level), ("1", 0), "preserved foreign files do not fail the removal")
        self.assertIn("does not own", left.guidance)
        self.assertNotIn("does not own", plain)
        compat = handle("install", 0, report("install", "installed", warnings=["compatibility-window"]))
        self.assertEqual((compat.succeeded, compat.level), ("1", 0))
        self.assertIn("compatible", compat.guidance)
        both = handle("install", 0, report("install", "installed", warnings=["compatibility-window", "leftover-files"]))
        self.assertIn("compatible", both.guidance)
        self.assertIn("does not own", both.guidance)

    def test_the_uninstaller_applies_the_same_semantics(self) -> None:
        clean = handle("uninstall", 0, report("uninstall", "uninstalled"), uninstaller=True)
        self.assertEqual((clean.succeeded, clean.level), ("1", 0), clean.guidance)
        left = handle("uninstall", 0, report("uninstall", "uninstalled", warnings=["leftover-files"]), uninstaller=True)
        self.assertEqual((left.succeeded, left.level), ("1", 0))
        self.assertIn("does not own", left.guidance)
        for text in (report("uninstall", "damaged"), report("uninstall", "uninstalled", warnings=["cleanup-pending"]),
                     report("install", "installed"), "", "not json"):
            with self.subTest(report=text[:30]):
                out = handle("uninstall", 0, text, uninstaller=True)
                self.assertEqual((out.succeeded, out.level), ("", 1), out.guidance)


class NeverDoneUnlessComplete(unittest.TestCase):
    def test_pending_cleanup_is_a_failure_whatever_the_exit_code_says(self) -> None:
        for exit_code, text in (
            (0, report("uninstall", "uninstalled", warnings=["cleanup-pending"])),
            (1, report("uninstall", "uninstalled", warnings=["cleanup-pending"])),
            (1, report("uninstall", "failed", failure="cleanup-pending")),
            (0, report("uninstall", "failed", failure="cleanup-pending")),
        ):
            with self.subTest(exit=exit_code, report=text[:40]):
                out = handle("uninstall", exit_code, text)
                self.assertEqual((out.succeeded, out.level), ("", 1), out.guidance)
                self.assertIn("could not be removed yet", out.guidance)
                self.assertNotIn("Done", out.guidance)

    def test_an_unsettled_state_with_exit_zero_is_not_a_success(self) -> None:
        for state in ("damaged", "foreign", "recovery-required", "failed", "absent"):
            with self.subTest(state=state):
                out = handle("repair", 0, report("repair", state))
                self.assertEqual((out.succeeded, out.level), ("", 1), out.guidance)
                self.assertNotIn("Done", out.guidance)

    def test_a_report_for_another_operation_or_state_is_not_a_success(self) -> None:
        for requested, text in (
            ("install", report("uninstall", "uninstalled")),
            ("repair", report("install", "installed")),
            ("rollback", report("rollback", "installed")),
            ("uninstall", report("uninstall", "installed")),
            ("install", report("inspect", "installed")),
        ):
            with self.subTest(requested=requested, report=text[:40]):
                out = handle(requested, 0, text)
                self.assertEqual((out.succeeded, out.level), ("", 1), out.guidance)

    def test_an_unreadable_report_is_not_a_success(self) -> None:
        for text in ("", "Done.", "{}", "{\"state\":\"installed\"}", "garbage\n"):
            with self.subTest(report=text):
                out = handle("install", 0, text)
                self.assertEqual((out.succeeded, out.level), ("", 1), out.guidance)

    def test_failure_codes_select_their_guidance_and_unsupported_state_stays_distinct(self) -> None:
        cases = {
            "host-active": "Close all Veles connector tabs",
            "unsupported-state": "newer Veles installer",
            "trust-unavailable": "could not check",
            "trust-denied": "did not accept",
            "recovery-required": "interrupted",
            "foreign-state": "Another program",
            "no-rollback": "no earlier verified version",
            "repair-package-required": "exact version",
            "cleanup-pending": "could not be removed yet",
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                out = handle("install", 1, report("install", "failed", failure=code))
                self.assertEqual((out.succeeded, out.level), ("", 1))
                self.assertIn(expected, out.guidance)
        pending = handle("install", 1, report("install", "failed", failure="cleanup-pending")).guidance
        unsupported = handle("install", 1, report("install", "failed", failure="unsupported-state")).guidance
        self.assertNotEqual(pending, unsupported)

    def test_exit_codes_two_and_other_mean_the_engine_did_not_run(self) -> None:
        self.assertEqual(handle("install", 2, "").level, 2)
        self.assertEqual(handle("install", 5, "").level, 3)


class InformationalInspect(unittest.TestCase):
    """Inspect exits 0 for every report it produces: the state is the answer, never "installation succeeded"."""

    def test_no_inspect_outcome_is_done_or_publishes_an_entry(self) -> None:
        for state, failure in (("absent", None), ("installed", None), ("damaged", None), ("damaged", "recovery-required"),
                               ("foreign", "foreign-state"), ("recovery-required", "recovery-required")):
            with self.subTest(state=state, failure=failure):
                out = handle("inspect", 0, report("inspect", state, failure=failure, usable=state == "installed"))
                self.assertEqual(out.succeeded, "", "inspect never claims a completed operation")
                self.assertEqual(out.level, 0, "an inspection that produced a report is complete")
                self.assertNotIn("Done", out.guidance)
                self.assertNotIn("Open the Veles connector", out.guidance)

    def test_each_inspected_state_is_described(self) -> None:
        expect = {
            "absent": "not installed",
            "installed": "installed and complete",
            "damaged": "damaged",
            "foreign": "Another program",
            "recovery-required": "interrupted",
        }
        for state, text in expect.items():
            with self.subTest(state=state):
                out = handle("inspect", 0, report("inspect", state))
                self.assertIn(text, out.guidance)

    def test_a_damaged_inspect_points_at_repair(self) -> None:
        self.assertIn("Repair", handle("inspect", 0, report("inspect", "damaged")).guidance)

    def test_inspect_still_surfaces_pending_cleanup_and_leftovers(self) -> None:
        pending = handle("inspect", 1, report("inspect", "damaged", failure="cleanup-pending", usable=False))
        self.assertEqual((pending.succeeded, pending.level), ("", 1))
        self.assertIn("could not be removed yet", pending.guidance)
        left = handle("inspect", 0, report("inspect", "installed", warnings=["leftover-files"]))
        self.assertIn("does not own", left.guidance)
        compat = handle("inspect", 0, report("inspect", "installed", warnings=["compatibility-window"]))
        self.assertIn("compatible", compat.guidance)

    def test_an_inspect_request_answered_by_another_operation_is_refused(self) -> None:
        out = handle("inspect", 0, report("install", "installed"))
        self.assertEqual((out.succeeded, out.level), ("", 1))


class InterpreterIsTheRealScript(unittest.TestCase):
    def test_the_interpreter_runs_the_shipped_functions_and_nothing_else(self) -> None:
        machine = nsis_model.load(NSI)
        for name in ("HandleEngineResult", "un.HandleEngineResult", "SelectGuidance", "un.SelectGuidance", "Contains"):
            self.assertIn(name, machine.functions)

    def test_a_script_that_leaves_the_supported_subset_fails_loudly(self) -> None:
        machine = nsis_model.load("Function F\n  ExecWait 'cmd /c calc'\nFunctionEnd\n")
        with self.assertRaises(nsis_model.NsisError):
            machine.call("F")


if __name__ == "__main__":
    unittest.main()
