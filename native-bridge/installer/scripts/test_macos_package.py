#!/usr/bin/env python3
"""Contract tests for the native macOS installer frontend and its bundle inputs (OTP-25 Task 11).

This host is Linux x86_64: ``swift``/Xcode are absent. These tests therefore do NOT compile
or run any Swift, launch an app, sign, notarize or ask Gatekeeper anything. What they DO
execute, for real, is everything that is plain text or plain shell:

* the three bundle ``Info.plist`` files and the two ``.entitlements`` files are parsed with
  ``plistlib`` and their security-relevant content is asserted (host bundle ID, Bluetooth
  usage text on the host only, no launch agents / privileged helper / system paths, and
  only the hardened-runtime entitlements the host needs - never a blanket
  ``disable-library-validation``);
* ``Package.swift`` and ``App.swift`` are scanned (comments stripped) for the closed process
  surface: one structured ``Process`` start, no shell, no host launch, no TCC tooling;
* the Swift report/enum vocabulary is compared with the Rust ``InstallReport`` model so the
  two frontends cannot drift from the engine unnoticed;
* ``scripts/package-macos.sh`` is RUN on fabricated inputs (it needs only bash, coreutils
  and ``touch``) and the resulting unsigned ``.app`` tree / DMG layout is inspected:
  layout, modes, fixed mtimes, no symlinks, no checkout-path leakage, byte-identical
  output from two differently-placed input trees.

Python 3.12+ standard library only. Run from the repository root:

    python3 -m unittest discover -s native-bridge/installer/scripts -p test_macos_package.py
"""

import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALLER = HERE.parent
MACOS = INSTALLER / "macos"
SCRIPT = HERE / "package-macos.sh"
MODEL_RS = INSTALLER / "src" / "model.rs"
PATHS_RS = INSTALLER / "src" / "paths.rs"
PACKAGE_SWIFT = MACOS / "Package.swift"
APP_SWIFT = MACOS / "Sources" / "VelesBridgeInstaller" / "App.swift"
TESTS_SWIFT = MACOS / "Tests" / "VelesBridgeInstallerTests" / "FrontendTests.swift"

HOST_BUNDLE_ID = "me.nagaev.veles.bridge"
INSTALLER_BUNDLE_ID = "me.nagaev.veles.bridge.installer"
HOST_EXECUTABLE = "veles-native-host"
ENGINE_EXECUTABLE = "veles-bridge-maintenance"
INSTALLER_EXECUTABLE = "VelesBridgeInstaller"
APP_NAME = "Veles Bridge Installer.app"
HOST_APP_NAME = "Veles Native Bridge.app"
MINIMUM_OS = "26.0"
FIXED_MTIME = 946684800  # 2000-01-01T00:00:00Z

# Hardened-runtime exceptions and sandbox/file entitlements that must never be requested.
FORBIDDEN_ENTITLEMENTS = (
    "com.apple.security.cs.disable-library-validation",
    "com.apple.security.cs.allow-unsigned-executable-memory",
    "com.apple.security.cs.allow-jit",
    "com.apple.security.cs.disable-executable-page-protection",
    "com.apple.security.cs.allow-dyld-environment-variables",
    "com.apple.security.cs.debugger",
    "com.apple.security.get-task-allow",
    "com.apple.security.app-sandbox",
    "com.apple.security.inherit",
    "com.apple.security.automation.apple-events",
    "com.apple.security.files.all",
    "com.apple.security.temporary-exception.files.absolute-path.read-write",
    "com.apple.developer.system-extension.install",
    "com.apple.private.tcc.allow",
)


def strip_swift_comments(text: str) -> str:
    """Remove ``//`` and (nested) ``/* */`` comments outside string literals."""
    out: list[str] = []
    i, n, depth = 0, len(text), 0
    in_string = False
    while i < n:
        two = text[i : i + 2]
        char = text[i]
        if depth:
            if two == "/*":
                depth += 1
                i += 2
            elif two == "*/":
                depth -= 1
                i += 2
            else:
                i += 1
            continue
        if in_string:
            out.append(char)
            if char == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            i += 1
        elif two == "//":
            while i < n and text[i] != "\n":
                i += 1
        elif two == "/*":
            depth = 1
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)


def load_plist(path: Path) -> dict:
    with path.open("rb") as handle:
        return plistlib.load(handle)


def walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_strings(item)


def kebab(camel: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", camel).lower()


def rust_enum_variants(source: str, name: str) -> list[str]:
    match = re.search(rf"pub enum {name} \{{(?P<body>.*?)\n\}}", source, re.DOTALL)
    assert match, f"{name} not found in model.rs"
    body = re.sub(r"///.*", "", match.group("body"))
    return [kebab(variant) for variant in re.findall(r"^\s*([A-Z][A-Za-z]*),\s*$", body, re.MULTILINE)]


def rust_struct_fields(source: str, name: str) -> list[str]:
    match = re.search(rf"pub struct {name} \{{(?P<body>.*?)\n\}}", source, re.DOTALL)
    assert match, f"{name} not found in model.rs"
    body = re.sub(r"///.*", "", match.group("body"))
    return re.findall(r"^\s*pub ([a-z_]+):", body, re.MULTILINE)


def swift_enum_raw_values(source: str, name: str) -> list[str]:
    match = re.search(rf"enum {name}\b[^{{]*\{{(?P<body>.*?)\n\}}", source, re.DOTALL)
    assert match, f"enum {name} not found in App.swift"
    return re.findall(r'case\s+\w+\s*=\s*"([a-z-]+)"', match.group("body"))


class BundleInputContract(unittest.TestCase):
    """Static contract of the plists, entitlements and Swift sources."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.installer_plist = load_plist(MACOS / "Info.plist")
        cls.host_plist = load_plist(MACOS / "HostInfo.plist")
        cls.carrier_plist = load_plist(MACOS / "CarrierInfo.plist")
        cls.installer_ent = load_plist(MACOS / "Installer.entitlements")
        cls.host_ent = load_plist(MACOS / "Host.entitlements")
        cls.app_source = strip_swift_comments(APP_SWIFT.read_text(encoding="utf-8"))
        cls.test_source = strip_swift_comments(TESTS_SWIFT.read_text(encoding="utf-8"))
        cls.package_swift = strip_swift_comments(PACKAGE_SWIFT.read_text(encoding="utf-8"))

    # -- identity ---------------------------------------------------------------

    def test_host_bundle_id_is_a_channel_placeholder_filled_from_the_config(self) -> None:
        # The identity is the channel's, never a literal that a fixture build would inherit (F2).
        self.assertEqual(self.host_plist["CFBundleIdentifier"], "@BUNDLE_ID@")
        self.assertEqual(self.host_plist["CFBundleExecutable"], HOST_EXECUTABLE)
        self.assertEqual(self.host_plist["CFBundlePackageType"], "APPL")
        self.assertIs(self.host_plist["LSUIElement"], True, "the host is a background app")
        for version_key in ("CFBundleShortVersionString", "CFBundleVersion"):
            self.assertEqual(self.host_plist[version_key], "@VERSION@")

    def test_installer_bundle_id_is_distinct_from_the_bluetooth_host(self) -> None:
        installer_id = self.installer_plist["CFBundleIdentifier"]
        carrier_id = self.carrier_plist["CFBundleIdentifier"]
        self.assertEqual(installer_id, INSTALLER_BUNDLE_ID)
        self.assertNotEqual(installer_id, HOST_BUNDLE_ID)
        self.assertNotEqual(carrier_id, HOST_BUNDLE_ID)
        self.assertNotEqual(carrier_id, installer_id)
        self.assertTrue(
            carrier_id.startswith(INSTALLER_BUNDLE_ID + "."),
            "the engine bundle is namespaced under the installer, never under the host",
        )
        for plist in (self.installer_plist, self.carrier_plist):
            self.assertFalse(plist["CFBundleIdentifier"].endswith(".dev"))
        self.assertEqual(self.installer_plist["CFBundleExecutable"], INSTALLER_EXECUTABLE)
        self.assertEqual(self.carrier_plist["CFBundleExecutable"], ENGINE_EXECUTABLE)

    def test_bluetooth_usage_text_belongs_to_the_host_only(self) -> None:
        description = self.host_plist["NSBluetoothAlwaysUsageDescription"]
        self.assertIsInstance(description, str)
        self.assertGreaterEqual(len(description.strip()), 30, "a real, user-readable purpose string")
        self.assertIn("Veles", description)
        for name, plist in (("Info.plist", self.installer_plist), ("CarrierInfo.plist", self.carrier_plist)):
            keys = [key for key in plist if key.startswith("NSBluetooth")]
            self.assertEqual(keys, [], f"{name}: the installer must not pretend to own BLE")
        self.assertNotRegex(self.app_source, r"CoreBluetooth|CBCentralManager|CBPeripheral")

    def test_installer_is_a_visible_app_and_the_engine_is_headless(self) -> None:
        self.assertEqual(self.installer_plist["CFBundlePackageType"], "APPL")
        self.assertNotEqual(self.installer_plist.get("LSUIElement"), True)
        self.assertNotEqual(self.installer_plist.get("LSBackgroundOnly"), True)
        self.assertEqual(self.installer_plist["NSPrincipalClass"], "NSApplication")
        self.assertIs(self.carrier_plist["LSUIElement"], True)
        self.assertEqual(self.carrier_plist["CFBundlePackageType"], "APPL")

    def test_deployment_target_is_pinned_consistently(self) -> None:
        for plist in (self.installer_plist, self.host_plist, self.carrier_plist):
            self.assertEqual(plist["LSMinimumSystemVersion"], MINIMUM_OS)
        self.assertIn(f'.macOS("{MINIMUM_OS}")', self.package_swift)
        self.assertIn("VelesBridgeInstaller", self.package_swift)
        first_line = PACKAGE_SWIFT.read_text(encoding="utf-8").splitlines()[0]
        self.assertRegex(first_line, r"^// swift-tools-version:\d+\.\d+$")
        self.assertIn("executableTarget", self.package_swift)

    # -- no agents, helpers or system paths ---------------------------------------

    def test_no_launch_agents_privileged_helper_or_system_paths_in_plists(self) -> None:
        forbidden_keys = {
            "SMPrivilegedExecutables",
            "SMAuthorizedClients",
            "SMLoginItemSetAutoLaunch",
            "LSEnvironment",  # would inject DYLD_*/PATH into the process
            "NSAppleEventsUsageDescription",
            "NSSystemAdministrationUsageDescription",
            "NSAppTransportSecurity",
            "LSApplicationQueriesSchemes",
            "CFBundleURLTypes",
            "CFBundleDocumentTypes",
        }
        forbidden_text = re.compile(
            r"LaunchAgents|LaunchDaemons|PrivilegedHelperTools|/Library/|/System/|/usr/|/bin/|/Applications|/private/|/var/",
            re.IGNORECASE,
        )
        for name, plist in (
            ("Info.plist", self.installer_plist),
            ("HostInfo.plist", self.host_plist),
            ("CarrierInfo.plist", self.carrier_plist),
        ):
            self.assertEqual(forbidden_keys & set(plist), set(), name)
            for text in walk_strings(plist):
                self.assertIsNone(forbidden_text.search(text), f"{name} mentions a system/agent path: {text!r}")

    def test_swift_sources_name_no_system_or_agent_paths_and_no_privilege_apis(self) -> None:
        forbidden = [
            r"LaunchAgents",
            r"LaunchDaemons",
            r"PrivilegedHelperTools",
            r"SMAppService",
            r"SMJobBless",
            r"SMLoginItem",
            r"AuthorizationExecuteWithPrivileges",
            r"AuthorizationCreate",
            r"\bsudo\b",
            r"osascript",
            r"NSAppleScript",
            r"/Library/",
            r"/Applications",
            r"/usr/local",
            r"/System/",
        ]
        for pattern in forbidden:
            self.assertIsNone(re.search(pattern, self.app_source), f"App.swift must not contain {pattern!r}")

    # -- hardened runtime / entitlements ---------------------------------------------

    def test_installer_has_no_entitlements(self) -> None:
        self.assertEqual(self.installer_ent, {}, "the installer needs no hardened-runtime exception")

    def test_host_requests_only_the_required_hardened_runtime_entitlement(self) -> None:
        self.assertEqual(self.host_ent, {"com.apple.security.device.bluetooth": True})

    def test_no_blanket_library_validation_or_other_exception(self) -> None:
        for name, entitlements in (("Installer", self.installer_ent), ("Host", self.host_ent)):
            for key in FORBIDDEN_ENTITLEMENTS:
                self.assertNotIn(key, entitlements, f"{name}.entitlements must not request {key}")
            for key, value in entitlements.items():
                self.assertIs(value, True, f"{name}.entitlements {key} must be a plain boolean grant")
        for raw in ("Installer.entitlements", "Host.entitlements"):
            text = (MACOS / raw).read_text(encoding="utf-8")
            self.assertNotIn("disable-library-validation", text)
            self.assertNotIn("allow-unsigned-executable-memory", text)

    def test_carrier_bundle_has_no_entitlements_file(self) -> None:
        self.assertFalse((MACOS / "Carrier.entitlements").exists())

    # -- Swift process surface ----------------------------------------------------------

    def test_process_use_is_one_structured_start_with_no_shell(self) -> None:
        source = self.app_source
        self.assertEqual(len(re.findall(r"\bProcess\(\)", source)), 1)
        self.assertRegex(source, r"\.executableURL\s*=")
        self.assertRegex(source, r"\.arguments\s*=")
        for pattern in (
            r"/bin/(ba|z|c|k|da)?sh\b",
            r"/usr/bin/env\b",
            r'"-c"',
            r"\bsystem\s*\(",
            r"\bpopen\s*\(",
            r"\bposix_spawn",
            r"\bexecv",
            r"launchPath",
            r"\bNSTask\b",
            r"/usr/bin/open\b",
        ):
            self.assertIsNone(re.search(pattern, source), f"App.swift must not contain {pattern!r}")
        # The environment handed to the engine is explicit, never inherited wholesale.
        self.assertRegex(source, r"\.environment\s*=")

    def test_installer_never_launches_the_host_or_touches_tcc_or_other_processes(self) -> None:
        for pattern in (
            r"NSWorkspace",
            r"LSOpen",
            r"openApplication",
            r"launchApplication",
            r"NSRunningApplication",
            r"\bkill\s*\(",
            r"\bpkill\b",
            r"\bkillall\b",
            r"\.terminate\(\)\s*$",
            r"tccutil",
            r"TCC\.db",
            r"veles-native-host",
            r"NativeMessagingHosts",
            r"com\.google\.Chrome",
        ):
            self.assertIsNone(
                re.search(pattern, self.app_source, re.MULTILINE), f"App.swift must not contain {pattern!r}"
            )
        # The only executable the client may start is the engine inside the signed bundle.
        self.assertIn("veles-bridge-maintenance", self.app_source)
        self.assertIn("carrier.app", self.app_source)

    def test_swift_declares_the_required_tests_by_name(self) -> None:
        names = set(re.findall(r"func (test\w+)\s*\(", self.test_source))
        for required in (
            "testUnicodePathsUseProcessArgumentsNotShell",
            "testActiveHostShowsCloseTabsAndRetry",
            "testTrustUnavailableNeverShowsDone",
            "testMaintenanceDoesNotLaunchHost",
            # F11: Update and damaged-install Repair transitions, and the bounded child wait.
            "testAnInstalledReleaseOffersRepairOnlyForItsOwnPackageAndUpdateForAnother",
            "testDamagedStateStillOffersRepairForTheSameVersionPackage",
            "testADamagedInspectOffersRepairWithoutAnyPresentationGuard",
            "testAWedgedEngineIsReportedAfterTheDeadline",
            # F6: the persistent maintenance engine the Rust engine publishes.
            "testThePersistentEnginePathMatchesTheEnginesPublishedLayout",
            "testThePersistentEngineIsPreferredOnlyWhenItIsExecutable",
        ):
            self.assertIn(required, names)

    def test_operation_availability_is_derived_from_installation_and_package_state(self) -> None:
        """F11: Update exists, Repair survives a damaged result, and neither depends on the presentation kind."""
        self.assertRegex(self.app_source, r"enum OperationPlanner")
        self.assertRegex(
            self.app_source,
            r"static func offered\(for report: MaintenanceReport, packagePayload: String\?\)"
            r" -> \[OfferedOperation\]")
        self.assertRegex(self.app_source, r"case update")
        self.assertRegex(self.app_source, r"case \.install, \.update: return \.install")
        # The package this installer carries is read from its own descriptor, bounded, and used to tell
        # "the installed release" (Repair) from "another release" (Update).
        self.assertRegex(self.app_source, r"enum PackageIdentity")
        self.assertRegex(self.app_source, r"descriptor\.json")
        # ... and read under a bound, never loaded whole.
        self.assertRegex(self.app_source, r"read\(upToCount: maxDescriptorBytes \+ 1\)")
        self.assertIsNone(re.search(r"Data\(contentsOf:", self.app_source))
        # The old guard that hid Repair behind an informational/done presentation must not come back.
        for pattern in (r"presentation\.kind\s*==\s*\.info", r"installed:\s*Bool", r"damaged:\s*Bool"):
            self.assertIsNone(re.search(pattern, self.app_source), f"App.swift must not contain {pattern!r}")

    def test_the_frontend_resolves_the_persistent_maintenance_engine(self) -> None:
        """F6: the Swift frontend and the Rust engine agree on one maintenance path set."""
        self.assertRegex(
            self.app_source,
            r'static let maintenanceEngineRelativePath =\s*\n\s*"Library/Application Support/'
            r'Veles/NativeBridge/maintenance/veles-bridge-maintenance"')
        self.assertRegex(self.app_source, r"static func persistentEngine\(homeDirectory: URL\) -> URL")
        self.assertRegex(
            self.app_source,
            r"isExecutableFile\(atPath: persistent\.path\) \? persistent : bundled")
        # The persistent path is exactly the Rust engine's `maintenance_engine_relative`
        # for macOS: `<root>/maintenance/veles-bridge-maintenance`.
        paths = PATHS_RS.read_text(encoding="utf-8")
        self.assertRegex(paths, r'pub const MAINTENANCE_DIR: &str = "maintenance"')
        self.assertRegex(paths, r'pub const MAINTENANCE_ENGINE_FILE_MACOS: &str = "veles-bridge-maintenance"')
        self.assertIn('root.join(steps)'.replace("steps", "maintenance_engine_relative(self.target)"), paths)

    def test_the_child_wait_is_bounded_and_never_signals_the_engine(self) -> None:
        """The Swift frontend must not wait for the engine without a deadline, and must not kill it."""
        self.assertRegex(self.app_source, r"static let defaultDeadline: TimeInterval = (\d+)")
        self.assertRegex(self.app_source, r"terminationHandler")
        self.assertRegex(self.app_source, r"finished\.wait\(timeout: \.now\(\) \+ deadline\)")
        self.assertRegex(self.app_source, r"case timedOut")
        self.assertRegex(self.app_source, r"case \.timedOut:")
        self.assertIsNone(re.search(r"\bprocess\.(terminate|kill)\s*\(", self.app_source))

    # -- cross-language vocabulary ----------------------------------------------------------

    def test_swift_report_vocabulary_matches_the_rust_install_report(self) -> None:
        rust = MODEL_RS.read_text(encoding="utf-8")
        self.assertEqual(
            sorted(swift_enum_raw_values(self.app_source, "FailureCode")),
            sorted(rust_enum_variants(rust, "FailureCode")),
        )
        self.assertEqual(
            sorted(swift_enum_raw_values(self.app_source, "WarningCode")),
            sorted(rust_enum_variants(rust, "WarningCode")),
        )
        self.assertEqual(
            sorted(swift_enum_raw_values(self.app_source, "MaintenanceOperation")),
            sorted(rust_enum_variants(rust, "Operation")),
        )
        fields = rust_struct_fields(rust, "InstallReport")
        self.assertEqual(
            fields, ["operation", "state", "prior_usable", "active_payload", "failure", "warnings"]
        )
        match = re.search(r"allowedKeys[^=]*=\s*\[(?P<keys>[^\]]*)\]", self.app_source)
        self.assertIsNotNone(match, "the decoder pins its closed key set in `allowedKeys`")
        self.assertEqual(sorted(re.findall(r'"([a-z_]+)"', match.group("keys"))), sorted(fields))

    def test_swift_report_bounds_match_the_rust_cli(self) -> None:
        cli = (INSTALLER / "src" / "cli.rs").read_text(encoding="utf-8")
        report_bytes = re.search(r"MAX_REPORT_BYTES: usize = (\d+) \* 1024", cli)
        self.assertIsNotNone(report_bytes)
        self.assertRegex(self.app_source, rf"maxBytes\s*=\s*{report_bytes.group(1)}\s*\*\s*1024")
        model = MODEL_RS.read_text(encoding="utf-8")
        warnings = re.search(r"MAX_REPORT_WARNINGS: usize = (\d+)", model)
        self.assertRegex(self.app_source, rf"maxWarnings\s*=\s*{warnings.group(1)}\b")


# ---------------------------------------------------------------------------------------------
# package-macos.sh, executed for real on fabricated inputs
# ---------------------------------------------------------------------------------------------

INPUT_FILES = {
    "VERSION": b"1.2.3\n",
    "BUNDLE_ID": b"me.nagaev.veles.bridge\n",
    INSTALLER_EXECUTABLE: b"\xcf\xfa\xed\xfe fake installer frontend\n",
    ENGINE_EXECUTABLE: b"\xcf\xfa\xed\xfe fake engine\n",
    HOST_EXECUTABLE: b"\xcf\xfa\xed\xfe fake host\n",
    "veles-policy-carrier": b"VELESPC1 fake carrier document\n",
    "descriptor.json": b'{"schema":1}\n',
    "inventory.json": b'{"schema":1,"entries":[]}\n',
}
EXECUTABLE_RELATIVE = {
    f"Contents/MacOS/{INSTALLER_EXECUTABLE}",
    f"Contents/Resources/package/carrier.app/Contents/MacOS/{ENGINE_EXECUTABLE}",
    f"Contents/Resources/package/payload/{HOST_APP_NAME}/Contents/MacOS/{HOST_EXECUTABLE}",
}
EXPECTED_APP_FILES = {
    "Contents/Info.plist",
    f"Contents/MacOS/{INSTALLER_EXECUTABLE}",
    "Contents/Resources/package/descriptor.json",
    "Contents/Resources/package/inventory.json",
    "Contents/Resources/package/carrier.app/Contents/Info.plist",
    f"Contents/Resources/package/carrier.app/Contents/MacOS/{ENGINE_EXECUTABLE}",
    "Contents/Resources/package/carrier.app/Contents/Resources/veles-policy-carrier",
    f"Contents/Resources/package/payload/{HOST_APP_NAME}/Contents/Info.plist",
    f"Contents/Resources/package/payload/{HOST_APP_NAME}/Contents/MacOS/{HOST_EXECUTABLE}",
}


def make_inputs(root: Path, *, mode: int = 0o644, mtime: int = 1_700_000_000, extra=None) -> Path:
    root.mkdir(parents=True)
    files = dict(INPUT_FILES)
    files.update(extra or {})
    for name, data in files.items():
        path = root / name
        path.write_bytes(data)
        os.chmod(path, mode)
        os.utime(path, (mtime, mtime))
    os.chmod(root, 0o755)
    return root


def run_package(inputs: Path, output: Path, *, umask: int = 0o022, env: dict | None = None, extra_args=()):
    environment = dict(os.environ)
    environment.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT), "--inputs", str(inputs), "--output", str(output), *extra_args],
        capture_output=True,
        text=True,
        env=environment,
        preexec_fn=lambda: os.umask(umask),
        timeout=120,
    )


def snapshot(root: Path) -> dict[str, tuple]:
    """Relative path -> (kind, mode, mtime, sha256|target) for the whole tree."""
    result = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            result[relative] = ("link", mode, int(info.st_mtime), os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            result[relative] = ("dir", mode, int(info.st_mtime), None)
        else:
            result[relative] = ("file", mode, int(info.st_mtime), hashlib.sha256(path.read_bytes()).hexdigest())
    return result


class PackageScriptContract(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="macos-package-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def build(self, name: str = "a", **kwargs):
        inputs = make_inputs(self.tmp / f"in-{name}", **kwargs)
        output = self.tmp / f"out-{name}"
        completed = run_package(inputs, output)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return inputs, output

    def app(self, output: Path) -> Path:
        return output / "dmg-root" / APP_NAME

    # -- static properties of the script ------------------------------------------------------

    def test_script_is_strict_unsigned_and_credential_free(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        self.assertIn("set -euo pipefail", code)
        for token in (
            "codesign",
            "notarytool",
            "stapler",
            "spctl",
            "productsign",
            "hdiutil",
            "security ",
            "curl",
            "wget",
            "sudo",
            "launchctl",
            "tccutil",
            "xattr",
            "eval ",
            "KEYCHAIN",
            "APPLE_",
            "/Library/",
            "/Applications",
            "LaunchAgents",
            "LaunchDaemons",
        ):
            self.assertNotIn(token, code, f"package-macos.sh must not contain {token!r}")

    def test_usage_errors_exit_2_and_create_nothing(self) -> None:
        for args in ([], ["--inputs", str(self.tmp)], ["--output", str(self.tmp / "o")], ["--bogus"]):
            completed = subprocess.run(
                ["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=60
            )
            self.assertEqual(completed.returncode, 2, args)
        self.assertFalse((self.tmp / "o").exists())

    # -- layout -------------------------------------------------------------------------------

    def test_unsigned_app_tree_and_dmg_layout(self) -> None:
        _, output = self.build()
        dmg_root = output / "dmg-root"
        self.assertEqual([child.name for child in sorted(dmg_root.iterdir())], [APP_NAME])
        app = self.app(output)
        files = {
            path.relative_to(app).as_posix() for path in app.rglob("*") if path.is_file()
        }
        self.assertEqual(files, EXPECTED_APP_FILES)
        self.assertTrue((output / "SHA256SUMS").is_file())
        self.assertTrue((output / "manifest.tsv").is_file())
        self.assertEqual(
            sorted(child.name for child in output.iterdir()), ["SHA256SUMS", "dmg-root", "manifest.tsv"]
        )

    def test_no_privileged_helper_launch_agent_symlink_or_system_path(self) -> None:
        _, output = self.build()
        for path in output.rglob("*"):
            relative = path.relative_to(output).as_posix()
            self.assertFalse(path.is_symlink(), f"{relative}: no symlink (and so no /Applications alias)")
            self.assertNotRegex(
                relative,
                r"LaunchAgents|LaunchDaemons|PrivilegedHelperTools|Library|_CodeSignature|\.DS_Store|CodeResources",
            )
        text_files = [
            path for path in output.rglob("*") if path.is_file() and path.suffix in {".plist", ".json", ".tsv", ""}
        ]
        for path in text_files:
            data = path.read_bytes()
            for needle in (b"LaunchAgents", b"LaunchDaemons", b"PrivilegedHelperTools", b"/Applications", b"/Library/"):
                self.assertNotIn(needle, data, f"{path.name} mentions {needle!r}")

    def test_bundle_plists_in_the_output_keep_the_exact_identities(self) -> None:
        _, output = self.build()
        app = self.app(output)
        installer = load_plist(app / "Contents/Info.plist")
        carrier = load_plist(app / "Contents/Resources/package/carrier.app/Contents/Info.plist")
        host = load_plist(app / f"Contents/Resources/package/payload/{HOST_APP_NAME}/Contents/Info.plist")
        self.assertEqual(host["CFBundleIdentifier"], HOST_BUNDLE_ID)
        self.assertIn("NSBluetoothAlwaysUsageDescription", host)
        self.assertEqual(installer["CFBundleIdentifier"], INSTALLER_BUNDLE_ID)
        self.assertNotIn("NSBluetoothAlwaysUsageDescription", installer)
        self.assertNotIn("NSBluetoothAlwaysUsageDescription", carrier)
        for plist in (installer, carrier, host):
            self.assertEqual(plist["CFBundleShortVersionString"], "1.2.3")
            self.assertEqual(plist["CFBundleVersion"], "1.2.3")
        # Byte-for-byte the source otherwise: only the version placeholder is substituted.
        for source, produced in (
            ("Info.plist", app / "Contents/Info.plist"),
            ("HostInfo.plist", app / f"Contents/Resources/package/payload/{HOST_APP_NAME}/Contents/Info.plist"),
            ("CarrierInfo.plist", app / "Contents/Resources/package/carrier.app/Contents/Info.plist"),
        ):
            expected = (MACOS / source).read_bytes().replace(b"@VERSION@", b"1.2.3").replace(b"@BUNDLE_ID@", HOST_BUNDLE_ID.encode())
            self.assertEqual(produced.read_bytes(), expected)

    def test_inputs_are_copied_byte_for_byte(self) -> None:
        inputs, output = self.build()
        app = self.app(output)
        pairs = {
            INSTALLER_EXECUTABLE: f"Contents/MacOS/{INSTALLER_EXECUTABLE}",
            ENGINE_EXECUTABLE: f"Contents/Resources/package/carrier.app/Contents/MacOS/{ENGINE_EXECUTABLE}",
            HOST_EXECUTABLE: f"Contents/Resources/package/payload/{HOST_APP_NAME}/Contents/MacOS/{HOST_EXECUTABLE}",
            "veles-policy-carrier": "Contents/Resources/package/carrier.app/Contents/Resources/veles-policy-carrier",
            "descriptor.json": "Contents/Resources/package/descriptor.json",
            "inventory.json": "Contents/Resources/package/inventory.json",
        }
        for source, target in pairs.items():
            self.assertEqual((inputs / source).read_bytes(), (app / target).read_bytes(), source)

    def test_sealed_inventory_is_included_only_when_the_input_has_one(self) -> None:
        inputs = make_inputs(self.tmp / "in-sealed", extra={"sealed-inventory.json": b'{"schema":1}\n'})
        output = self.tmp / "out-sealed"
        self.assertEqual(run_package(inputs, output).returncode, 0)
        sealed = self.app(output) / "Contents/Resources/package/sealed-inventory.json"
        self.assertEqual(sealed.read_bytes(), b'{"schema":1}\n')

    def test_exactly_the_host_executable_is_executable_in_the_payload(self) -> None:
        _, output = self.build()
        app = self.app(output)
        executable_files = {
            path.relative_to(app).as_posix()
            for path in app.rglob("*")
            if path.is_file() and path.stat().st_mode & 0o111
        }
        self.assertEqual(executable_files, EXECUTABLE_RELATIVE)
        payload_executables = [
            relative for relative in executable_files if relative.startswith("Contents/Resources/package/payload/")
        ]
        self.assertEqual(len(payload_executables), 1, "the engine requires exactly one executable payload file")

    # -- deterministic modes, timestamps, content -----------------------------------------------

    def test_modes_are_normalised_whatever_the_input_modes_or_umask(self) -> None:
        for name, mode, umask in (("p1", 0o600, 0o077), ("p2", 0o777, 0o000)):
            inputs = make_inputs(self.tmp / f"in-{name}", mode=mode)
            output = self.tmp / f"out-{name}"
            completed = run_package(inputs, output, umask=umask)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            app = self.app(output)
            for path in [output, *output.rglob("*")]:
                info = path.lstat()
                relative = path.relative_to(app).as_posix() if path.is_relative_to(app) else None
                if stat.S_ISDIR(info.st_mode):
                    self.assertEqual(stat.S_IMODE(info.st_mode), 0o755, path)
                elif relative in EXECUTABLE_RELATIVE:
                    self.assertEqual(stat.S_IMODE(info.st_mode), 0o755, path)
                else:
                    self.assertEqual(stat.S_IMODE(info.st_mode), 0o644, path)

    def test_every_timestamp_is_fixed(self) -> None:
        for tz in ("UTC", "Pacific/Auckland"):
            inputs = make_inputs(self.tmp / f"in-{tz.replace('/', '_')}")
            output = self.tmp / f"out-{tz.replace('/', '_')}"
            completed = run_package(inputs, output, env={"TZ": tz})
            self.assertEqual(completed.returncode, 0, completed.stderr)
            mtimes = {int(path.lstat().st_mtime) for path in [output, *output.rglob("*")]}
            self.assertEqual(mtimes, {FIXED_MTIME}, tz)

    def test_two_builds_from_different_places_are_identical(self) -> None:
        # Different checkout path, unicode and spaces, different input modes/mtimes/umask/TZ.
        first_inputs = make_inputs(self.tmp / "checkout one" / "inputs", mode=0o644, mtime=1_600_000_000)
        second_inputs = make_inputs(self.tmp / "Jörg Ünal ☃" / "inp uts", mode=0o700, mtime=1_800_000_000)
        first = self.tmp / "o u t" / "first"
        second = self.tmp / "ÖÜ ☃" / "second"
        first.parent.mkdir()
        second.parent.mkdir()
        one = run_package(first_inputs, first, umask=0o022, env={"TZ": "UTC"})
        two = run_package(second_inputs, second, umask=0o077, env={"TZ": "Asia/Tokyo"})
        self.assertEqual(one.returncode, 0, one.stderr)
        self.assertEqual(two.returncode, 0, two.stderr)
        self.assertEqual(snapshot(first), snapshot(second))
        self.assertEqual((first / "SHA256SUMS").read_bytes(), (second / "SHA256SUMS").read_bytes())
        self.assertEqual((first / "manifest.tsv").read_bytes(), (second / "manifest.tsv").read_bytes())

    def test_output_carries_no_checkout_path(self) -> None:
        inputs, output = self.build("leak")
        needles = [str(inputs).encode(), str(output).encode(), str(self.tmp).encode(), str(HERE).encode()]
        for path in output.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                for needle in needles:
                    self.assertNotIn(needle, data, f"{path.name} leaks {needle!r}")

    def test_checksums_and_manifest_describe_the_tree(self) -> None:
        _, output = self.build()
        lines = (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        listed = {}
        for line in lines:
            digest, _, relative = line.partition("  ")
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            listed[relative] = digest
        on_disk = {
            path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in output.rglob("*")
            if path.is_file() and path.name not in {"SHA256SUMS", "manifest.tsv"}
        }
        self.assertEqual(listed, on_disk)
        self.assertEqual(list(listed), sorted(listed), "byte-order sorted")
        manifest = (output / "manifest.tsv").read_text(encoding="utf-8").splitlines()
        rows = [line.split("\t") for line in manifest]
        self.assertTrue(all(len(row) == 4 for row in rows))
        self.assertEqual([row[0] for row in rows], sorted(row[0] for row in rows))
        modes = {row[0]: row[1] for row in rows}
        self.assertEqual(modes[f"dmg-root/{APP_NAME}/Contents/MacOS/{INSTALLER_EXECUTABLE}"], "755")

    # -- refusals ---------------------------------------------------------------------------------

    def test_missing_input_fails_and_leaves_no_output(self) -> None:
        for missing in INPUT_FILES:
            inputs = make_inputs(self.tmp / f"in-miss-{missing}")
            (inputs / missing).unlink()
            output = self.tmp / f"out-miss-{missing}"
            completed = run_package(inputs, output)
            self.assertEqual(completed.returncode, 1, missing)
            self.assertFalse(output.exists(), f"no partial output when {missing} is missing")

    def test_a_fixture_build_carries_the_fixture_host_bundle_id_and_only_there(self) -> None:
        inputs = make_inputs(self.tmp / "in-fixture", extra={"BUNDLE_ID": b"me.nagaev.veles.bridge.fixture\n"})
        output = self.tmp / "out-fixture"
        completed = run_package(inputs, output)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        app = self.app(output)
        host = load_plist(app / f"Contents/Resources/package/payload/{HOST_APP_NAME}/Contents/Info.plist")
        self.assertEqual(host["CFBundleIdentifier"], "me.nagaev.veles.bridge.fixture")
        self.assertNotEqual(host["CFBundleIdentifier"], HOST_BUNDLE_ID)
        self.assertEqual(load_plist(app / "Contents/Info.plist")["CFBundleIdentifier"], INSTALLER_BUNDLE_ID)
        # No production host identity survives anywhere in the fixture host bundle.
        for path in (app / f"Contents/Resources/package/payload/{HOST_APP_NAME}").rglob("Info.plist"):
            self.assertNotIn(b"<string>" + HOST_BUNDLE_ID.encode() + b"</string>", path.read_bytes())

    def test_only_the_two_packageable_host_identities_are_accepted(self) -> None:
        for bad in (b"me.nagaev.veles.bridge.dev\n", b"com.example.host\n", b"me.nagaev.veles.bridge.installer\n",
                    b"me.nagaev.veles.bridge.fixture\nme.nagaev.veles.bridge\n", b"\n", b"me.nagaev.veles.bridge\n\n",
                    b"me.nagaev.veles.bridge ", b"me.nagaev.veles.bridge.FIXTURE\n", b"@BUNDLE_ID@\n"):
            inputs = make_inputs(self.tmp / "in-id", extra={"BUNDLE_ID": bad})
            output = self.tmp / "out-id"
            completed = run_package(inputs, output)
            self.assertEqual(completed.returncode, 1, bad)
            self.assertFalse(output.exists(), bad)
            shutil.rmtree(inputs)

    def test_invalid_version_is_refused(self) -> None:
        for bad in (b"1.2\n", b"v1.2.3\n", b"1.2.3-rc1\n", b"1.2.3\n4\n", b"1.2.3 \n", b"01.2.3\n", b"\n", b"1.2.3;rm\n"):
            inputs = make_inputs(self.tmp / "in-ver", extra={"VERSION": bad})
            output = self.tmp / "out-ver"
            completed = run_package(inputs, output)
            self.assertEqual(completed.returncode, 1, bad)
            self.assertFalse(output.exists(), bad)
            shutil.rmtree(inputs)

    def test_unexpected_symlink_signature_or_extra_input_is_refused(self) -> None:
        cases = {
            "symlink": lambda root: os.symlink("/etc/passwd", root / "extra-link"),
            "extra-file": lambda root: (root / "surprise.bin").write_bytes(b"x"),
            "code-signature": lambda root: (root / "_CodeSignature").mkdir(),
            "subdir": lambda root: (root / "sub").mkdir(),
        }
        for name, mutate in cases.items():
            inputs = make_inputs(self.tmp / f"in-{name}")
            mutate(inputs)
            output = self.tmp / f"out-{name}"
            completed = run_package(inputs, output)
            self.assertEqual(completed.returncode, 1, name)
            self.assertFalse(output.exists(), name)

    def test_input_symlinked_member_is_refused(self) -> None:
        inputs = make_inputs(self.tmp / "in-linked")
        target = self.tmp / "elsewhere"
        target.write_bytes(b"fake")
        (inputs / HOST_EXECUTABLE).unlink()
        os.symlink(target, inputs / HOST_EXECUTABLE)
        output = self.tmp / "out-linked"
        self.assertEqual(run_package(inputs, output).returncode, 1)
        self.assertFalse(output.exists())

    def test_existing_output_and_overlapping_directories_are_refused(self) -> None:
        inputs = make_inputs(self.tmp / "in-over")
        existing = self.tmp / "occupied"
        existing.mkdir()
        (existing / "keep").write_text("mine")
        self.assertEqual(run_package(inputs, existing).returncode, 1)
        self.assertEqual((existing / "keep").read_text(), "mine", "never overwrites or deletes")
        self.assertEqual(run_package(inputs, inputs / "out").returncode, 1)
        self.assertFalse((inputs / "out").exists())
        self.assertEqual(run_package(inputs, self.tmp).returncode, 1, "output may not contain the inputs")

    def test_inputs_are_not_modified(self) -> None:
        inputs = make_inputs(self.tmp / "in-keep")
        before = snapshot(inputs)
        self.assertEqual(run_package(inputs, self.tmp / "out-keep").returncode, 0)
        self.assertEqual(snapshot(inputs), before)

    def test_json_inputs_are_not_reinterpreted(self) -> None:
        inputs, output = self.build("json")
        descriptor = self.app(output) / "Contents/Resources/package/descriptor.json"
        self.assertEqual(json.loads(descriptor.read_text()), {"schema": 1})


if __name__ == "__main__":
    unittest.main()
