#!/usr/bin/env python3
"""Contract tests for the DEBUG installer packaging path (OTP-25 PR path).

A debug PR installer is a FINISHED ``setup.exe`` / ``.dmg`` built from the isolated fixture
identity, not a renamed input tree. These tests exercise the real code path through a recording
runner that plays the native tools (makensis, codesign, hdiutil):

* the container is built by the same helpers the release assembly uses, and ``authorize`` is
  never consulted (a pull-request environment that ``authorize`` rejects still packages);
* only a fixture-channel tree with the fixture identity, the expected commit and a matching
  inventory can be packaged; production/development descriptors, a changed commit, a tampered
  payload and an unexpected executable are refused;
* any signing credential or release-only environment refuses before a tool runs;
* ``metadata.json`` has exactly the five published fields and ``SHA256SUMS`` covers every
  published file, symbols included.

Python 3.12+ standard library only. Run from the repository root:

    python3 -m unittest discover -s native-bridge/installer/scripts -p test_debug_packaging.py
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
import contextlib
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import assemble_unsigned as au  # noqa: E402
import release_gate as gate  # noqa: E402
from test_release_gate import FakeRunner  # noqa: E402

COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER_COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
FIXTURE = "me.nagaev.veles.bridge.fixture"
FIXTURE_PUBLISHER = "Veles Fixture (not a release signer)"
CONTAINER = {"windows": gate.SETUP_NAME, "macos": gate.DMG_NAME}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(root: Path, rel: str, data: bytes, mode: int = 0o644) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def fixture_config(platform: str) -> dict:
    return au.load_config(gate.INSTALLER_DIR / "config" / f"fixture-{platform}.json")


def fixture_tree(root: Path, platform: str, *, commit: str = COMMIT) -> bytes:
    """A debug input tree in the exact layout ``assemble_unsigned.py assemble`` publishes."""
    config = fixture_config(platform)
    if platform == "windows":
        write(root, f"{gate.WIN_PAYLOAD}/veles-native-host.exe", b"MZ-host", 0o755)
        write(root, f"{gate.WIN_PAYLOAD}/veles-bridge-cleanup.exe", b"MZ-cleanup", 0o755)
        write(root, gate.UNINSTALLER, b"MZ-uninstaller", 0o755)
        entries = gate._entries(gate.payload_view("windows", root))
        inventory = au.inventory_document(entries)
        descriptor = au.descriptor_document(config, commit, sha(inventory))
        write(root, f"{gate.WIN_PACKAGE}/inventory.json", inventory)
        write(root, f"{gate.WIN_PACKAGE}/descriptor.json", descriptor)
        # The carrier resource the Windows producer embeds: a document with the zeroed
        # sealed-digest placeholder (an unsigned tree is never sealed).
        write(root, f"{gate.WIN_PACKAGE}/carrier.exe", b"MZ-engine\0" + au.carrier_document(descriptor, sealed_placeholder=True), 0o755)
    else:
        write(root, f"{gate.HOST_APP}/Contents/MacOS/veles-native-host", b"host-macho", 0o755)
        write(root, f"{gate.HOST_APP}/Contents/Info.plist", b"host-plist")
        write(root, f"{gate.CARRIER_APP}/Contents/MacOS/veles-bridge-maintenance", b"engine-macho", 0o755)
        write(root, f"{gate.CARRIER_APP}/Contents/Info.plist", b"carrier-plist")
        write(root, f"{gate.APP}/Contents/MacOS/VelesBridgeInstaller", b"installer-macho", 0o755)
        write(root, f"{gate.APP}/Contents/Info.plist", b"installer-plist")
        entries = gate._entries(gate.payload_view("macos", root))
        inventory = au.inventory_document(entries)
        descriptor = au.descriptor_document(config, commit, sha(inventory))
        write(root, f"{gate.MAC_PACKAGE}/inventory.json", inventory)
        write(root, f"{gate.MAC_PACKAGE}/descriptor.json", descriptor)
        write(root, gate.CARRIER_PATH["macos"], au.carrier_document(descriptor, sealed_placeholder=False))
    return descriptor


def descriptor_path(tree: Path, platform: str) -> Path:
    return tree / (gate.WIN_PACKAGE if platform == "windows" else gate.MAC_PACKAGE) / "descriptor.json"


def debug_env(platform: str, **overrides) -> dict[str, str]:
    """A PR-like environment: no credential, no release environment, no tag ref."""
    env = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_HEAD_REF": "pr/debug-installer",
        "GITHUB_BASE_REF": "master",
        "GITHUB_REF": "refs/pull/25/merge",
        "GITHUB_SHA": COMMIT,
    }
    if platform == "windows":
        env["NSIS_PATH"] = "/fake/nsis/makensis.exe"
    env.update(overrides)
    return env


class DebugCase:
    """A scratch fixture tree, the tracked config and an output directory."""

    def __init__(self, test: unittest.TestCase, platform: str):
        self.root = Path(tempfile.mkdtemp(prefix="gate-debug-"))
        test.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.platform = platform
        self.tree = self.root / "tree"
        self.config = gate.INSTALLER_DIR / "config" / f"fixture-{platform}.json"
        fixture_tree(self.tree, platform)
        self.output = self.root / "out"
        self.env = debug_env(platform)

    def run(self, runner=None, **changes):
        values = dict(
            platform=self.platform,
            tree=self.tree,
            config=self.config,
            output=self.output,
            commit=COMMIT,
            env=self.env,
            runner=runner or FakeRunner(self.env, app_source=self.tree / gate.APP),
            symbols=None,
        )
        values.update(changes)
        return gate.package_debug(**values)


class CapturingWindowsRunner(FakeRunner):
    """Captures the sealed debug package NSIS is actually handed."""

    def tool_makensis(self, args):
        if args != ["/VERSION"]:
            package = Path(next(a for a in args if a.startswith("/DINPUT_DIR="))[len("/DINPUT_DIR="):])
            self.captured = {
                "canonical": (package / "inventory.json").read_bytes(),
                "sealed": (package / "sealed-inventory.json").read_bytes(),
                "carrier": (package / "carrier.exe").read_bytes(),
                "descriptor": (package / "descriptor.json").read_bytes(),
            }
        return super().tool_makensis(args)


class CapturingMacosRunner(FakeRunner):
    """Captures the DMG root the debug packaging actually hands to hdiutil."""

    def tool_hdiutil(self, args):
        if args[0] == "create":
            root = Path(args[args.index("-srcfolder") + 1])
            package = root / gate.MAC_PACKAGE
            carrier = root / gate.CARRIER_PATH["macos"]
            self.captured = {
                "dmg_root": root,
                "dmg_root_entries": sorted(p.name for p in root.iterdir()),
                "canonical": (package / "inventory.json").read_bytes(),
                "sealed": (package / "sealed-inventory.json").read_bytes(),
                "carrier": carrier.read_bytes(),
                "host_seal": (root / gate.HOST_APP / "Contents" / "_CodeSignature" / "CodeResources").is_file(),
                "host_macho": (root / gate.HOST_APP / "Contents" / "MacOS" / "veles-native-host").read_bytes(),
            }
        return super().tool_hdiutil(args)


class DebugPackagingTests(unittest.TestCase):
    def test_a_windows_fixture_tree_becomes_a_finished_setup_exe(self):
        case = DebugCase(self, "windows")
        runner = FakeRunner(case.env, app_source=case.tree / gate.APP)
        done = case.run(runner)
        self.assertTrue(done.ok, done.detail)
        setup = case.output / gate.SETUP_NAME
        self.assertTrue(setup.is_file(), "the debug artifact must be a finished setup.exe")
        self.assertFalse((case.output / "tree").exists(), "an input tree is not an installer")
        metadata = json.loads((case.output / "metadata.json").read_text())
        self.assertEqual(
            set(metadata),
            {"source_commit", "build_kind", "channel", "platform", "workflow_run_id"},
        )
        self.assertEqual(
            metadata,
            {
                "source_commit": COMMIT,
                "build_kind": "debug",
                "channel": "fixture",
                "platform": "windows",
                "workflow_run_id": "local",
            },
        )
        sums = (case.output / "SHA256SUMS").read_text().splitlines()
        listed = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in sums}
        self.assertEqual(set(listed), {gate.SETUP_NAME, "metadata.json"})
        self.assertEqual(listed[gate.SETUP_NAME], sha(setup.read_bytes()))
        # The container is built by the release assembly's NSIS arguments with the isolated
        # fixture identity, with no signer.
        compile_call = next(c for c in runner.calls if any(a.startswith("/DOUTFILE=") for a in c))
        self.assertIn("/NOCD", compile_call)
        self.assertIn("/DCHANNEL=fixture", compile_call)
        self.assertNotIn("/DCHANNEL=production", compile_call)
        self.assertTrue(Path(compile_call[-1]).is_absolute())
        self.assertEqual(Path(compile_call[-1]), gate.NSIS_RECIPE)
        names = {Path(c[0]).stem for c in runner.calls}
        self.assertFalse(names & {"signtool", "codesign", "notarytool", "stapler"})

    def test_a_macos_fixture_tree_is_ad_hoc_signed_inside_out_and_packaged(self):
        case = DebugCase(self, "macos")
        runner = FakeRunner(case.env, app_source=case.tree / gate.APP)
        done = case.run(runner)
        self.assertTrue(done.ok, done.detail)
        self.assertTrue((case.output / gate.DMG_NAME).is_file())
        self.assertFalse((case.output / "tree").exists())
        metadata = json.loads((case.output / "metadata.json").read_text())
        self.assertEqual(metadata["platform"], "macos")
        self.assertEqual(metadata["build_kind"], "debug")
        self.assertEqual(metadata["channel"], "fixture")
        # Ad-hoc signing is inside-out, exactly the COMPONENTS["macos"] order.
        sign_calls = [c for c in runner.calls if Path(c[0]).stem == "codesign" and "--sign" in c]
        expected = [component.path for component in gate.COMPONENTS["macos"]]
        self.assertEqual(len(sign_calls), len(expected))
        for call, relative in zip(sign_calls, expected):
            self.assertTrue(
                call[-1].endswith("/" + relative) or call[-1].endswith(relative),
                (call[-1], relative),
            )
            self.assertEqual(list(call[1:4]), ["--force", "--sign", "-"])
        self.assertIn("hdiutil", {Path(c[0]).stem for c in runner.calls})

    def test_the_shipped_package_carries_a_sealed_inventory_of_its_signed_bytes(self):
        with self.subTest(platform="windows"):
            case = DebugCase(self, "windows")
            runner = CapturingWindowsRunner(case.env, app_source=case.tree / gate.APP)
            done = case.run(runner)
            self.assertTrue(done.ok, done.detail)
            captured = runner.captured
            # Nothing is signed on Windows: the shipped bytes are the canonical inventory bytes,
            # and the sealed digest must no longer be the assembler's empty placeholder.
            self.assertEqual(captured["sealed"], captured["canonical"])
            self.assertEqual(
                gate.read_carrier_seal(captured["carrier"], embedded=True),
                sha(captured["sealed"]),
            )
        with self.subTest(platform="macos"):
            case = DebugCase(self, "macos")
            runner = CapturingMacosRunner(case.env, app_source=case.tree / gate.APP)
            done = case.run(runner)
            self.assertTrue(done.ok, done.detail)
            captured = runner.captured
            self.assertEqual(
                captured["dmg_root_entries"],
                [gate.APP],
                "the DMG root must contain the installer .app itself, not its contents",
            )
            self.assertTrue(
                captured["host_seal"],
                "the inventoried payload host bundle must be ad-hoc signed in the shipped DMG",
            )
            self.assertNotEqual(
                captured["sealed"],
                captured["canonical"],
                "ad-hoc signing changed the payload, so the signed-byte inventory must differ",
            )
            self.assertEqual(
                gate.read_carrier_seal(captured["carrier"], embedded=False),
                sha(captured["sealed"]),
            )
            descriptor = json.loads(descriptor_path(case.tree, "macos").read_text())
            self.assertEqual(descriptor["payload_id"], sha(captured["canonical"]))

    def test_the_adapter_never_consults_authorize(self):
        case = DebugCase(self, "windows")
        # `authorize` rejects this pull-request environment outright; a call would block.
        original = gate.authorize

        def forbidden(*args, **kwargs):
            raise AssertionError("package_debug must never call authorize()")

        gate.authorize = forbidden
        try:
            done = case.run()
        finally:
            gate.authorize = original
        self.assertTrue(done.ok, done.detail)
        self.assertTrue((case.output / gate.SETUP_NAME).is_file())

    def test_signing_credentials_and_the_release_environment_refuse_before_any_tool(self):
        for variable in (
            "VELES_WIN_PFX_BASE64",
            "VELES_MAC_P12_BASE64",
            "VELES_NOTARY_KEY_BASE64",
            "VELES_SIGNING_ENVIRONMENT",
        ):
            with self.subTest(variable=variable):
                case = DebugCase(self, "windows")
                runner = FakeRunner(case.env)
                done = case.run(runner, env=case.env | {variable: "x"})
                self.assertFalse(done.ok)
                self.assertEqual(done.code, "credentials-present", done.detail)
                self.assertEqual(runner.calls, [])
                self.assertFalse(case.output.exists())

    def test_only_a_fixture_tree_for_the_expected_commit_is_packageable(self):
        with self.subTest("production descriptor"):
            case = DebugCase(self, "windows")
            descriptor = json.loads(descriptor_path(case.tree, "windows").read_text())
            descriptor["channel"] = "production"
            descriptor["host_name"] = descriptor["bundle_id"] = "me.nagaev.veles.bridge"
            descriptor_path(case.tree, "windows").write_text(json.dumps(descriptor))
            runner = FakeRunner(case.env)
            done = case.run(runner)
            self.assertEqual((done.ok, done.code), (False, "fixture-required"), done.detail)
            self.assertEqual(runner.calls, [])
        with self.subTest("another commit"):
            case = DebugCase(self, "windows")
            runner = FakeRunner(case.env)
            done = case.run(runner, commit=OTHER_COMMIT)
            self.assertEqual((done.ok, done.code), (False, "source-commit-mismatch"), done.detail)
            self.assertEqual(runner.calls, [])
        with self.subTest("another fixture identity"):
            case = DebugCase(self, "windows")
            descriptor = json.loads(descriptor_path(case.tree, "windows").read_text())
            descriptor["host_name"] = descriptor["bundle_id"] = "me.nagaev.veles.bridge.other"
            descriptor_path(case.tree, "windows").write_text(json.dumps(descriptor))
            done = case.run()
            self.assertEqual((done.ok, done.code), (False, "fixture-required"))

    def test_a_tampered_inventory_or_payload_refuses(self):
        with self.subTest("payload changed"):
            case = DebugCase(self, "windows")
            (case.tree / gate.WIN_PAYLOAD / "veles-native-host.exe").write_bytes(b"MZ-swapped")
            done = case.run()
            self.assertEqual((done.ok, done.code), (False, "input-mismatch"), done.detail)
        with self.subTest("inventory changed"):
            case = DebugCase(self, "windows")
            inventory = case.tree / gate.WIN_PACKAGE / "inventory.json"
            inventory.write_bytes(inventory.read_bytes() + b" ")
            done = case.run()
            self.assertEqual((done.ok, done.code), (False, "input-mismatch"))
        with self.subTest("unexpected executable"):
            case = DebugCase(self, "windows")
            write(case.tree, "package/payload/extra.exe", b"MZ-extra", 0o755)
            done = case.run()
            self.assertEqual((done.ok, done.code), (False, "input-mismatch"), done.detail)
        with self.subTest("signed tree"):
            case = DebugCase(self, "windows")
            write(case.tree, f"{gate.WIN_PACKAGE}/sealed-inventory.json", b"{}\n")
            done = case.run()
            self.assertEqual((done.ok, done.code), (False, "input-mismatch"))

    def test_a_config_of_another_channel_is_refused(self):
        case = DebugCase(self, "windows")
        config = json.loads(case.config.read_text())
        config["channel"] = "production"
        altered = case.root / "config.json"
        altered.write_text(json.dumps(config))
        done = case.run(config=altered)
        self.assertEqual((done.ok, done.code), (False, "config-refused"), done.detail)

    def test_existing_output_is_never_overwritten(self):
        case = DebugCase(self, "windows")
        case.output.mkdir(parents=True)
        (case.output / "stale").write_text("x")
        done = case.run()
        self.assertEqual((done.ok, done.code), (False, "bad-request"))
        self.assertEqual((case.output / "stale").read_text(), "x")

    def test_symbols_are_published_and_checksummed(self):
        case = DebugCase(self, "windows")
        symbols = case.root / "symbols"
        symbols.mkdir()
        (symbols / "veles-bridge-maintenance.pdb").write_bytes(b"pdb-bytes")
        done = case.run(symbols=symbols)
        self.assertTrue(done.ok, done.detail)
        self.assertEqual(
            (case.output / "symbols" / "veles-bridge-maintenance.pdb").read_bytes(),
            b"pdb-bytes",
        )
        listed = (case.output / "SHA256SUMS").read_text()
        self.assertIn("symbols/veles-bridge-maintenance.pdb", listed)
        self.assertIn(sha(b"pdb-bytes"), listed)

    def test_the_workflow_run_id_comes_from_the_debug_producer_environment(self):
        case = DebugCase(self, "windows")
        done = case.run(env=case.env | {"VELES_WORKFLOW_RUN_ID": "1234567890"})
        self.assertTrue(done.ok, done.detail)
        self.assertEqual(
            json.loads((case.output / "metadata.json").read_text())["workflow_run_id"],
            "1234567890",
        )

    def test_the_release_stages_cannot_reach_the_debug_packaging(self):
        import inspect

        parameters = inspect.signature(gate.package_debug).parameters
        for forbidden in ("boundary", "recipe_digest", "expected_commit"):
            self.assertNotIn(forbidden, parameters)
        self.assertNotIn("package-debug", gate.STAGES)


class DebugIdentityIsolationTests(unittest.TestCase):
    """The debug installer's Windows identity is isolated from production (OTP-25 PR path).

    There is no makensis on this host: this pins the argument selection and the recipe's
    per-channel identity (root directory, display name and Installed Apps key), and checks that
    the generated uninstaller is compiled with the same channel. Native NSIS compilation/execution
    remains NOT RUN.
    """

    def test_the_channel_define_is_explicit_for_both_production_and_fixture(self):
        self.assertEqual(gate.windows_channel_defines("production"), ["/DCHANNEL=production"])
        self.assertEqual(gate.windows_channel_defines("fixture"), ["/DCHANNEL=fixture"])
        with self.assertRaises(gate.StageFailure):
            gate.windows_channel_defines("development")

    def test_production_defaults_are_unchanged_and_fixture_identity_is_distinct(self):
        nsi = (gate.INSTALLER_DIR / "windows" / "installer.nsi").read_text(encoding="utf-8")
        # No /DCHANNEL: the production identity is the default.
        self.assertIn("!ifndef CHANNEL", nsi)
        self.assertIn('!define ROOT_DIR_NAME "NativeBridge"', nsi)
        self.assertIn(
            '!define UNINSTALL_KEY "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\VelesNativeBridge"',
            nsi,
        )
        self.assertIn('!define DISPLAY_NAME "Veles Native Bridge"', nsi)
        # The fixture branch selects a sibling root, a distinct display name and a distinct key.
        self.assertIn('!if "${CHANNEL}" == "fixture"', nsi)
        self.assertIn('!define ROOT_DIR_NAME "NativeBridgeFixture"', nsi)
        self.assertIn(
            '!define UNINSTALL_KEY "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\VelesNativeBridgeFixture"',
            nsi,
        )
        self.assertIn('!define DISPLAY_NAME "Veles Native Bridge (Fixture DEBUG)"', nsi)
        self.assertNotEqual(
            "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\VelesNativeBridge",
            "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\VelesNativeBridgeFixture",
        )
        self.assertNotEqual("NativeBridge", "NativeBridgeFixture")
        self.assertNotEqual("Veles Native Bridge", "Veles Native Bridge (Fixture DEBUG)")

    def test_installer_and_uninstaller_reference_only_the_selected_root(self):
        nsi = (gate.INSTALLER_DIR / "windows" / "installer.nsi").read_text(encoding="utf-8")
        # Both maintenance references are derived from the selected root, so the fixture
        # uninstaller can never stage or execute the production maintenance engine.
        self.assertIn(
            "$LOCALAPPDATA\\Veles\\${ROOT_DIR_NAME}\\maintenance\\veles-bridge-maintenance.exe",
            nsi,
        )
        self.assertIn(
            "$LOCALAPPDATA\\Veles\\${ROOT_DIR_NAME}\\maintenance\\uninstall.exe",
            nsi,
        )
        self.assertNotIn("$LOCALAPPDATA\\Veles\\NativeBridge\\maintenance", nsi)
        self.assertIn('"DisplayName" "${DISPLAY_NAME}"', nsi)
        self.assertIn('Name "${DISPLAY_NAME}"', nsi)

    def test_the_generated_uninstaller_is_compiled_for_the_same_channel(self):
        script = (gate.INSTALLER_DIR / "scripts" / "generate-uninstaller.ps1").read_text(encoding="utf-8")
        self.assertIn("[ValidateSet('production', 'fixture')]", script)
        self.assertIn('"/DCHANNEL=$Channel"', script)
        gradle = (HERE.parents[2] / "build.gradle.kts").read_text(encoding="utf-8")
        self.assertIn('"-Channel", channel', gradle)


class DebugPackagingCommandLineTests(unittest.TestCase):
    def test_the_command_line_dispatches_to_the_debug_path(self):
        case = DebugCase(self, "windows")
        argv = [
            "package-debug",
            "--platform", "windows",
            "--tree", str(case.tree),
            "--config", str(case.config),
            "--output", str(case.output),
            "--commit", COMMIT,
        ]
        with mock.patch.object(gate, "package_debug", return_value=gate.Outcome(True)) as called:
            self.assertEqual(gate.main(argv), 0)
        self.assertEqual(called.call_args.kwargs["platform"], "windows")
        self.assertEqual(called.call_args.kwargs["tree"], case.tree)
        self.assertEqual(called.call_args.kwargs["output"], case.output)

    def test_the_command_line_accepts_no_signing_boundary_or_recipe(self):
        case = DebugCase(self, "windows")
        base = [
            "package-debug",
            "--platform", "windows",
            "--tree", str(case.tree),
            "--config", str(case.config),
            "--output", str(case.output),
            "--commit", COMMIT,
        ]
        with mock.patch.object(gate, "package_debug", return_value=gate.Outcome(True)):
            for forbidden in (["--boundary", "x"], ["--recipe-digest", "x"], ["--stage", "outer"]):
                with self.subTest(forbidden=forbidden):
                    with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
                        gate.main(base + forbidden)
                    self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
