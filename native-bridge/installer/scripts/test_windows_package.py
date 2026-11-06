#!/usr/bin/env python3
"""Static contract tests for the Windows NSIS frontend (OTP-25 Task 10).

``windows/installer.nsi`` is text, so its security-relevant contract is asserted on
Linux without ``makensis``. These tests do NOT compile the script, run NSIS or
touch a registry: compilation with the pinned NSIS 3.08 and every runtime
behaviour are NOT RUN here (no makensis, no Windows runner). They pin the shape
the compiled installer must have:

* ``RequestExecutionLevel user`` and nothing that elevates or touches machine scope;
* the engine is only ever started through ``nsExec::ExecToStack`` with a quoted
  Unicode-safe command line made of fixed words (no shell, no caller selected
  root, origin, command or URL);
* the unsigned descriptor and inventory are embedded, the signed uninstaller is
  embedded from packaging (never generated at end-user runtime);
* Uninstall metadata lives only under ``HKCU``; Chrome registration is never
  written by NSIS (the engine owns it);
* repair, update, rollback and uninstall are all present, and the host is never
  launched.

Python 3.12+ standard library only. Run from the repository root:

    python3 -m unittest discover -s native-bridge/installer/scripts -p test_windows_package.py
"""

import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
NSI = HERE.parent / "windows" / "installer.nsi"

ENGINE_COMMAND = re.compile(r"""nsExec::ExecToStack\s+'(?P<command>[^']*)'""")


def strip_comments(text: str) -> str:
    """Remove NSIS ``;``/``#`` line comments (outside quotes) and ``/* */`` blocks."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    lines = []
    for raw in text.splitlines():
        quote = None
        kept = []
        for char in raw:
            if quote:
                kept.append(char)
                if char == quote:
                    quote = None
                continue
            if char in "\"'`":
                quote = char
                kept.append(char)
                continue
            if char in ";#":
                break
            kept.append(char)
        lines.append("".join(kept).rstrip())
    return "\n".join(lines)


def expand_defines(text: str, code: str) -> str:
    """Expand ``${NAME}`` references to simple ``!define NAME "value"`` constants."""
    defines = dict(re.findall(r'(?m)^\s*!define\s+(\w+)\s+"([^"]*)"\s*$', code))
    for _ in range(8):
        expanded = re.sub(r"\$\{(\w+)\}", lambda m: defines.get(m.group(1), m.group(0)), text)
        if expanded == text:
            break
        text = expanded
    return text


def open_conditionals(code: str, upto: int) -> list[str]:
    """The ``!if*`` directives still open at character offset ``upto``."""
    stack: list[str] = []
    for line in code[:upto].splitlines():
        directive = line.strip()
        if re.match(r"!(if|ifdef|ifndef|ifmacrodef|ifmacrondef)\b", directive):
            stack.append(" ".join(directive.split()))
        elif directive.startswith("!else"):
            if stack:
                stack[-1] = "else of " + stack[-1]
        elif directive.startswith("!endif") and stack:
            stack.pop()
    return stack


class WindowsInstallerContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = NSI.read_text(encoding="utf-8")
        cls.code = strip_comments(cls.raw)
        cls.lowered = cls.code.lower()

    # -- privilege and scope ------------------------------------------------

    def test_request_execution_level_user_only(self) -> None:
        levels = re.findall(r"(?im)^\s*RequestExecutionLevel\s+(\S+)", self.code)
        self.assertEqual(levels, ["user"], "exactly one RequestExecutionLevel, and it is user")
        self.assertRegex(self.code, r"(?im)^\s*Unicode\s+true\s*$")

    def test_no_elevation_machine_scope_or_shell_primitives(self) -> None:
        forbidden = [
            r"RequestExecutionLevel\s+(admin|highest)",
            r"SetShellVarContext\s+all",
            r"\bHKLM\b",
            r"\bHKEY_LOCAL_MACHINE\b",
            r"\bHKCR\b",
            r"\bHKEY_CLASSES_ROOT\b",
            r"\bHKU\b",
            r"\bHKEY_USERS\b",
            r"\bHKCC\b",
            r"\bSetRegView\b",
            r"\$PROGRAMFILES",
            r"\$COMMONFILES",
            r"\$WINDIR",
            r"\$SYSDIR",
            r"UserInfo::",
            r"ShellExecAsUser",
            r"\brunas\b",
            r"\bExecShell(Wait)?\b",
            r"\bExecWait\b",
            r"(?m)^\s*Exec\s",
            r"\bExecDos\b",
            r"nsExec::Exec(?!ToStack)",
            r"\bShellExecute",
            r"System::Call",
            r"\bcmd(\.exe)?\b",
            r"\bpowershell\b",
            r"\bwscript\b",
            r"\breg(\.exe)?\s+(add|delete)",
            r"\bregsvr32\b",
            r"\bschtasks\b",
            r"\bsc\.exe\b",
            r"CreateService",
            r"RMDir\s+/r",
            r"\bRebootFlag\b",
            r"\bSetRebootFlag\b",
            r"\bReboot\b",
            r"/REBOOTOK",
        ]
        for pattern in forbidden:
            self.assertIsNone(
                re.search(pattern, self.code, re.IGNORECASE),
                f"installer.nsi must not contain {pattern!r}",
            )

    def test_no_download_webview_wix_or_tauri(self) -> None:
        for token in (
            "http://",
            "https://",
            "inetc::",
            "nsisdl",
            "nsisdl::",
            "webview",
            "tauri",
            "nsis_tauri_utils",
            "msiexec",
        ):
            self.assertNotIn(token, self.lowered, f"installer.nsi must not mention {token!r}")
        self.assertIsNone(re.search(r"\bwix\b", self.lowered), "no WiX")

    # -- engine invocation --------------------------------------------------

    def engine_commands(self) -> list[str]:
        return [match.group("command") for match in ENGINE_COMMAND.finditer(self.code)]

    def test_quoted_unicode_engine_invocation(self) -> None:
        commands = self.engine_commands()
        self.assertTrue(commands, "the engine is started with nsExec::ExecToStack")
        # Every other program start is forbidden above, so these are the only ones.
        self.assertEqual(
            len(commands),
            len(re.findall(r"nsExec::", self.code)),
            "nsExec is only ever used as ExecToStack",
        )
        allowed_operations = {"inspect", "install", "repair", "rollback", "uninstall"}
        seen = set()
        for command in commands:
            match = re.fullmatch(
                r'"\$\{(?P<engine>[A-Z_]+)\}" (?P<operation>[a-z]+)'
                r'(?: --package "\$\{(?P<package>[A-Z_]+)\}")?',
                command,
            )
            self.assertIsNotNone(match, f"unexpected engine command line: {command!r}")
            self.assertIn(match.group("engine"), {"ENGINE_EXE", "MAINTENANCE_ENGINE_EXE"})
            self.assertIn(match.group("operation"), allowed_operations)
            if match.group("package"):
                self.assertEqual(match.group("package"), "PACKAGE_DIR")
                self.assertIn(match.group("operation"), {"install", "repair"})
            else:
                self.assertNotIn(match.group("operation"), {"install", "repair"})
            seen.add(match.group("operation"))
        self.assertEqual(seen, allowed_operations, "all five engine operations are invoked")

        # The engine and the package are private staging below $PLUGINSDIR, never a
        # caller-selected location, and are defined once.
        self.assertRegex(
            self.code, r'(?m)^\s*!define\s+PACKAGE_DIR\s+"\$PLUGINSDIR\\package"\s*$'
        )
        self.assertRegex(
            self.code, r'(?m)^\s*!define\s+ENGINE_EXE\s+"\$\{PACKAGE_DIR\}\\carrier\.exe"\s*$'
        )
        # The uninstaller cannot carry files, so it runs the retained maintenance
        # engine at one fixed path below the fixed per-user root.
        self.assertRegex(
            self.code,
            r'(?m)^\s*!define\s+MAINTENANCE_ENGINE_EXE\s+'
            r'"\$LOCALAPPDATA\\Veles\\NativeBridge\\maintenance\\veles-bridge-maintenance\.exe"\s*$',
        )

    def test_engine_arguments_are_not_built_from_user_input(self) -> None:
        # The operation is chosen from a closed list; no command line is
        # assembled by concatenating a user-controlled string.
        for command in self.engine_commands():
            self.assertNotIn("$R", command)
            self.assertNotIn("$0", command)
            self.assertNotIn("$CMDLINE", command)
            self.assertNotIn("%", command)
            self.assertNotIn("&", command)
            self.assertNotIn("|", command)
        self.assertNotRegex(self.code, r"\$CMDLINE")
        self.assertRegex(self.code, r"\$\{GetOptions\}")
        for operation in ("inspect", "install", "repair", "update", "rollback", "uninstall"):
            self.assertRegex(
                self.code,
                rf'StrCmp\s+\$Operation\s+"{operation}"',
                f"operation {operation!r} is matched against the closed list",
            )

    # -- embedded inputs ----------------------------------------------------

    def test_embedded_descriptor_and_inventory(self) -> None:
        for define in ("VERSION", "INPUT_DIR", "OUTFILE", "UNINSTALLER_SIGNED"):
            self.assertRegex(
                self.code,
                rf"(?m)^\s*!ifndef\s+{define}\s*\n\s*!error\s+",
                f"{define} is a required packaging define",
            )
        self.assertIn("OutFile", self.code)
        self.assertRegex(self.code, r'File\s+"/oname=carrier\.exe"\s+"\$\{INPUT_DIR\}\\carrier\.exe"')
        self.assertRegex(self.code, r'File\s+"\$\{INPUT_DIR\}\\descriptor\.json"')
        self.assertRegex(self.code, r'File\s+"\$\{INPUT_DIR\}\\inventory\.json"')
        self.assertRegex(self.code, r'File\s+/nonfatal\s+"\$\{INPUT_DIR\}\\sealed-inventory\.json"')
        self.assertRegex(self.code, r'File\s+/r\s+"\$\{INPUT_DIR\}\\payload\\\*\.\*"')
        # Everything is extracted to private plugin staging only.
        out_paths = re.findall(r"(?im)^\s*SetOutPath\s+(.+)$", self.code)
        self.assertTrue(out_paths)
        for path in out_paths:
            resolved = expand_defines(path, self.code)
            self.assertIn("$PLUGINSDIR", resolved, f"SetOutPath {path!r} must be private staging")
        self.assertNotIn("$INSTDIR", self.code)
        self.assertNotRegex(self.code, r"(?m)^\s*InstallDir\b")

    def test_signed_uninstaller_is_embedded_not_generated_at_runtime(self) -> None:
        # The uninstaller is generated and signed during packaging; the installer
        # embeds that verified binary as inventoried payload. The one
        # WriteUninstaller exists only in the packaging-time generation mode.
        writes = list(re.finditer(r"(?m)^\s*WriteUninstaller\b", self.code))
        self.assertEqual(len(writes), 1, "one WriteUninstaller, in the packaging mode only")
        self.assertIn(
            "!ifdef GENERATE_UNINSTALLER",
            open_conditionals(self.code, writes[0].start()),
            "WriteUninstaller sits inside !ifdef GENERATE_UNINSTALLER (the true branch)",
        )
        # The embedded signed binary is only demanded outside that mode.
        embed = self.code.index('"${UNINSTALLER_SIGNED}"', self.code.index("!else"))
        self.assertNotIn("!ifdef GENERATE_UNINSTALLER", open_conditionals(self.code, embed))
        self.assertRegex(self.code, r'"\$\{UNINSTALLER_SIGNED\}"')

    # -- HKCU-only uninstall metadata ----------------------------------------

    def test_hkcu_only_uninstall_metadata(self) -> None:
        self.assertRegex(
            self.code,
            r'(?m)^\s*!define\s+UNINSTALL_KEY\s+'
            r'"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\VelesNativeBridge"\s*$',
        )
        registry = re.findall(r"(?im)^\s*(Write|Delete|Enum|Read)Reg\w*\s+(\S+)", self.code)
        self.assertTrue(registry, "uninstall metadata is written")
        for _verb, root in registry:
            self.assertEqual(root.upper(), "HKCU", "registry access is HKCU only")
        for name in (
            "DisplayName",
            "DisplayVersion",
            "Publisher",
            "UninstallString",
            "QuietUninstallString",
            "NoModify",
            "NoRepair",
        ):
            self.assertRegex(self.code, rf'WriteReg\w+\s+HKCU\s+"\$\{{UNINSTALL_KEY\}}"\s+"{name}"')
        # Removed again on a completed uninstall, and only that key.
        self.assertRegex(self.code, r'DeleteRegKey\s+HKCU\s+"\$\{UNINSTALL_KEY\}"')
        self.assertEqual(len(re.findall(r"DeleteRegKey\b", self.code)), 1)
        # Published only after the engine reported a complete operation.
        self.assertIn("Function PublishUninstallEntry", self.code)

    def test_nsis_never_mutates_chrome_registration(self) -> None:
        for token in (
            "google\\chrome",
            "nativemessaginghosts",
            "chrome-extension://",
            "allowed_origins",
            "software\\google",
        ):
            self.assertNotIn(token, self.lowered, f"NSIS must not reference {token!r}")
        self.assertNotRegex(self.code, r"(?im)^\s*FileOpen\b")
        self.assertNotRegex(self.code, r"(?im)^\s*FileWrite\b")
        self.assertNotRegex(self.code, r"(?im)^\s*WriteINIStr\b")

    # -- operations ---------------------------------------------------------

    def test_repair_update_rollback_and_uninstall_paths_present(self) -> None:
        for operation, engine_word in (
            ("install", "install"),
            ("update", "install"),  # an update is the engine's install over a prior release
            ("repair", "repair"),
            ("rollback", "rollback"),
            ("uninstall", "uninstall"),
            ("inspect", "inspect"),
        ):
            self.assertIn(f'"{operation}"', self.code, f"{operation} is a selectable operation")
            self.assertTrue(
                any(f" {engine_word}" in command for command in self.engine_commands()),
                f"{operation} maps to the engine's {engine_word}",
            )
        self.assertRegex(self.code, r'(?m)^\s*Section\s+"?Uninstall"?\s*$')
        self.assertRegex(self.code, r"(?m)^\s*Function\s+un\.onInit\b")

    def test_engine_result_is_surfaced_not_assumed(self) -> None:
        # Exit 0 is complete, 1 a failure, 2 an internal invocation error; the
        # report's failure code selects guidance, and cleanup-pending is never
        # shown as done.
        self.assertRegex(self.code, r'StrCmp\s+\$\w+\s+"0"')
        self.assertRegex(self.code, r'StrCmp\s+\$\w+\s+"1"')
        self.assertRegex(self.code, r'StrCmp\s+\$\w+\s+"2"')
        for code in (
            "host-active",
            "cleanup-pending",
            "unsupported-state",
            "trust-denied",
            "trust-unavailable",
            "recovery-required",
            "foreign-state",
            "no-rollback",
            "repair-package-required",
        ):
            self.assertIn(f'"{code}"', self.code, f"guidance exists for {code}")
        self.assertIn("SetErrorLevel", self.code)
        self.assertIn("MUI_PAGE_INSTFILES", self.code)
        self.assertIn("!include MUI2.nsh", self.code)

    def test_no_auto_launch_of_the_host(self) -> None:
        for token in ("MUI_FINISHPAGE_RUN", "veles-native-host", "NativeMessaging", "StartMenu", "CreateShortCut"):
            self.assertNotIn(token.lower(), self.lowered, f"installer.nsi must not mention {token!r}")
        # The only programs the script can start are the engine commands above.
        self.assertEqual(
            len(self.engine_commands()), len(re.findall(r"nsExec::ExecToStack", self.code))
        )
        # The cleanup helper is staged privately for the engine and never started.
        self.assertNotRegex(self.code, r"veles-bridge-cleanup\.exe[^\n]*nsExec")
        self.assertNotRegex(self.code, r"nsExec[^\n]*veles-bridge-cleanup")


if __name__ == "__main__":
    unittest.main()
