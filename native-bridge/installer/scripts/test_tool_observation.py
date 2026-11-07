"""Tests for tool_observation.py: observed native build tools versus config/tools.lock.json (F8).

The parsers are exercised on text written from each tool's documented output. None of it is real tool
output (this host has neither Xcode nor MSVC), so these tests prove the comparison logic and the fail-closed
behaviour, not that a real runner prints these bytes.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import release_config  # noqa: E402
import tool_observation as obs  # noqa: E402

LOCK = release_config.load_lock()

RUSTC = "rustc 1.98.0 (abcdef012 2026-10-01)\n"
COMPONENTS = ("cargo-aarch64-apple-darwin\nclippy-aarch64-apple-darwin\nrust-std-aarch64-apple-darwin\n"
              "rust-std-x86_64-pc-windows-msvc\nrustc-aarch64-apple-darwin\nrustfmt-aarch64-apple-darwin\n")
TARGETS = "aarch64-apple-darwin\nx86_64-pc-windows-msvc\n"
XCODE = "Xcode 26.6\nBuild version 17F113\n"
SDK = "MacOSX26.5.sdk - macOS 26.5 (macosx26.5)\nSDKVersion: 26.5\nPath: /Applications/Xcode_26.6.app/x\n"
CL = "Microsoft (R) C/C++ Optimizing Compiler Version 19.44.35211 for x64\nusage: cl [ option... ]\n"
LINK = "Microsoft (R) Incremental Linker Version 14.44.35211.0\n"
WIN_ENV = {"VCToolsVersion": "14.44.35207", "WindowsSDKVersion": "10.0.26100.0\\", "UCRTVersion": "10.0.26100.0"}


class Script:
    """A scripted tool runner: argv prefix -> (exit code, output); unknown commands raise."""

    def __init__(self, table: dict[str, tuple[int, str]]) -> None:
        self.table, self.calls = table, []

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv)
        key = " ".join(argv)
        if key not in self.table:
            raise obs.ObservationError(f"cannot run {argv[0]}: not found")
        return self.table[key]


def mac_script(**overrides: tuple[int, str]) -> Script:
    table = {
        "rustc --version": (0, RUSTC),
        "rustup show profile": (0, "minimal\n"),
        "rustup component list --installed": (0, COMPONENTS),
        "rustup target list --installed": (0, TARGETS),
        "xcodebuild -version": (0, XCODE),
        "xcodebuild -version -sdk macosx": (0, SDK),
    }
    table.update(overrides)
    return Script(table)


def win_script(**overrides: tuple[int, str]) -> Script:
    table = mac_script().table
    for key in ("xcodebuild -version", "xcodebuild -version -sdk macosx"):
        del table[key]
    table.update({"cl": (2, CL), "link": (1100, LINK)})
    table.update(overrides)
    return Script(table)


class Parsing(unittest.TestCase):
    def test_a_macos_runner_matching_the_lock_is_accepted(self) -> None:
        observed = obs.observe("macos-arm64", mac_script())
        self.assertEqual(obs.check(observed, "macos-arm64", LOCK), [])
        self.assertEqual(observed["tools"]["xcode"], {"version": "26.6", "build": "17F113"})
        self.assertEqual(observed["tools"]["rust"]["components"], "clippy,rustfmt")

    def test_every_observed_field_is_compared_exactly(self) -> None:
        for label, script, needle in (
            ("rust version", mac_script(**{"rustc --version": (0, "rustc 1.99.0 (x 2026-01-01)\n")}), "rust.channel"),
            ("rust profile", mac_script(**{"rustup show profile": (0, "default\n")}), "rust.profile"),
            ("extra component", mac_script(**{"rustup component list --installed": (0, COMPONENTS + "rust-docs-aarch64-apple-darwin\n")}), "rust.components"),
            ("extra target", mac_script(**{"rustup target list --installed": (0, TARGETS + "wasm32-unknown-unknown\n")}), "rust.targets"),
            ("xcode build", mac_script(**{"xcodebuild -version": (0, "Xcode 26.6\nBuild version 17F999\n")}), "xcode.build"),
            ("xcode version", mac_script(**{"xcodebuild -version": (0, "Xcode 26.7\nBuild version 17F113\n")}), "xcode.version"),
            ("sdk", mac_script(**{"xcodebuild -version -sdk macosx": (0, "MacOSX26.4.sdk - macOS 26.4 (macosx26.4)\n")}), "macos_sdk.version"),
        ):
            with self.subTest(label):
                errors = obs.check(obs.observe("macos-arm64", script), "macos-arm64", LOCK)
                self.assertTrue(any(needle in e for e in errors), errors)

    def test_unrecognised_output_and_failing_tools_are_never_defaulted(self) -> None:
        for label, script in (
            ("rustc prose", mac_script(**{"rustc --version": (0, "hello\n")})),
            ("rustc exit", mac_script(**{"rustc --version": (1, RUSTC)})),
            ("xcode prose", mac_script(**{"xcodebuild -version": (0, "no xcode\n")})),
            ("two SDKs", mac_script(**{"xcodebuild -version -sdk macosx": (0, SDK + SDK)})),
            ("no SDK", mac_script(**{"xcodebuild -version -sdk macosx": (0, "")})),
            ("no profile", mac_script(**{"rustup show profile": (0, "\n")})),
            ("no targets", mac_script(**{"rustup target list --installed": (0, "")})),
        ):
            with self.subTest(label), self.assertRaises(obs.ObservationError):
                obs.observe("macos-arm64", script)

    def test_a_tool_that_cannot_be_started_blocks(self) -> None:
        table = mac_script().table
        del table["xcodebuild -version"]
        with self.assertRaises(obs.ObservationError):
            obs.observe("macos-arm64", Script(table))

    def test_the_wrong_target_is_refused_even_if_every_identity_matches(self) -> None:
        observed = obs.observe("macos-arm64", mac_script())
        self.assertTrue(obs.check(observed, "windows-x86_64", LOCK))
        self.assertTrue(obs.check({**observed, "target": "windows-x86_64"}, "macos-arm64", LOCK))


class WindowsIsAHardBlocker(unittest.TestCase):
    def test_unresolved_compiler_and_sdk_identities_block_even_a_fully_observed_runner(self) -> None:
        observed = obs.observe("windows-x86_64", win_script(), env=WIN_ENV)
        self.assertEqual(observed["tools"]["msvc"]["cl_version"], "19.44.35211")
        self.assertEqual(observed["tools"]["msvc"]["linker_version"], "14.44.35211.0")
        self.assertEqual(observed["tools"]["windows_sdk"], {"windows_sdk_version": "10.0.26100.0", "ucrt_version": "10.0.26100.0"})
        errors = obs.check(observed, "windows-x86_64", LOCK)
        text = "\n".join(errors)
        self.assertIn("'msvc'", text)
        self.assertIn("'windows_sdk'", text)
        self.assertIn("unresolved", text)

    def test_the_repository_lock_still_records_both_as_unresolved(self) -> None:
        # If this starts failing, the owner resolved the fingerprints and the Windows production
        # path may open - which must be a deliberate, reviewed change to tools.lock.json.
        self.assertIn("required_unresolved", LOCK["tools"]["msvc"])
        self.assertIn("required_unresolved", LOCK["tools"]["windows_sdk"])

    def test_digests_are_not_invented(self) -> None:
        observed = obs.observe("windows-x86_64", win_script(), env=WIN_ENV)
        for name in ("msvc", "windows_sdk"):
            self.assertFalse([k for k in observed["tools"][name] if k.endswith("sha256")])

    def test_missing_developer_environment_blocks(self) -> None:
        for label, env in (("no toolset", {k: v for k, v in WIN_ENV.items() if k != "VCToolsVersion"}),
                           ("no sdk", {k: v for k, v in WIN_ENV.items() if k != "WindowsSDKVersion"}),
                           ("no ucrt", {k: v for k, v in WIN_ENV.items() if k != "UCRTVersion"})):
            with self.subTest(label), self.assertRaises(obs.ObservationError):
                obs.observe("windows-x86_64", win_script(), env=env)
        with self.assertRaises(obs.ObservationError):
            obs.observe("windows-x86_64", win_script(cl=(2, "no banner\n")), env=WIN_ENV)


class ObservationContext(unittest.TestCase):
    """The observation must run in the same crate/toolchain context as the build (N6)."""

    def test_every_observed_tool_runs_in_the_pinned_installer_crate(self) -> None:
        calls: list[tuple[list[str], dict]] = []

        class Done:
            returncode = 0
            stderr = ""

            def __init__(self, stdout: str) -> None:
                self.stdout = stdout

        table = {
            "rustc --version": RUSTC,
            "rustup show profile": "minimal\n",
            "rustup component list --installed": COMPONENTS,
            "rustup target list --installed": TARGETS,
            "xcodebuild -version": XCODE,
            "xcodebuild -version -sdk macosx": SDK,
        }

        def fake_run(argv: list[str], **kwargs):
            calls.append((argv, kwargs))
            return Done(table[" ".join(argv)])

        with mock.patch.object(obs.subprocess, "run", side_effect=fake_run):
            observed = obs.observe("macos-arm64")

        self.assertEqual(obs.check(observed, "macos-arm64", LOCK), [])
        self.assertTrue(calls)
        for argv, kwargs in calls:
            self.assertEqual(
                kwargs.get("cwd"),
                obs.INSTALLER_DIR,
                f"{' '.join(argv)} must run inside the pinned installer crate",
            )


class CommandLine(unittest.TestCase):
    def run_main(self, argv: list[str]) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return obs.main(argv), err.getvalue()

    def test_check_exit_codes_and_no_warning_mode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            good, bad = Path(raw) / "good.json", Path(raw) / "bad.json"
            inventory = obs.observe("macos-arm64", mac_script())
            good.write_text(json.dumps(inventory))
            bad.write_text(json.dumps({**inventory, "tools": {**inventory["tools"], "xcode": {"version": "1", "build": "2"}}}))
            self.assertEqual(self.run_main(["check", "--target", "macos-arm64", "--observed", str(good)])[0], 0)
            code, message = self.run_main(["check", "--target", "macos-arm64", "--observed", str(bad)])
            self.assertEqual(code, 1)
            self.assertIn("BLOCKED", message)
            code, _ = self.run_main(["check", "--target", "windows-x86_64", "--observed", str(good)])
            self.assertEqual(code, 1)
            code, _ = self.run_main(["check", "--target", "macos-arm64", "--observed", str(Path(raw) / "absent.json")])
            self.assertEqual(code, 2)
            (Path(raw) / "junk.json").write_text("{")
            self.assertEqual(self.run_main(["check", "--target", "macos-arm64", "--observed", str(Path(raw) / "junk.json")])[0], 2)

    def test_observe_without_the_tools_exits_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "observed.json"
            def unavailable(argv: list[str]) -> tuple[int, str]:
                raise obs.ObservationError(f"cannot run {argv[0]}: not found")

            with mock.patch.object(obs, "system_runner", unavailable):
                code, message = self.run_main(["observe", "--target", "macos-arm64", "--output", str(out)])
            self.assertEqual(code, 2, message)
            self.assertFalse(out.exists(), "a failed observation must leave no inventory behind")


if __name__ == "__main__":
    unittest.main()
