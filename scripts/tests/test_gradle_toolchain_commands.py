"""The root build's host-toolchain probe contract.

The Windows branches cannot run in this container (Linux), so the pieces that differ per host are
pinned here: which file each command is searched for (`npm.cmd`, not `npm.exe`), and the order and
wording of ``protocolNodeCheck``'s checks, which the typed port must keep from the old ``doLast``.
"""

import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUILD = os.path.join(REPO, "build.gradle.kts")


def source():
    with open(BUILD, encoding="utf-8") as handle:
        return handle.read()


def task_block(text, name):
    """The text of one registration, from its `val name` up to the first closing brace at column 0."""
    start = text.index("val " + name + " = tasks.register")
    return text[start:text.index("\n}", start)]


def commands(text):
    """Every `ToolchainCommand(...)` as `(name, windows_name_or_None)`."""
    return [
        (match.group(1), match.group(2))
        for match in re.finditer(r'ToolchainCommand\("([^"]+)"(?:, "([^"]+)")?\)', text)
    ]


class ToolchainCommandTest(unittest.TestCase):
    def test_the_default_windows_name_is_the_bare_name_plus_exe(self):
        self.assertIn('val windowsName: String = "$name.exe",', source())

    def test_the_searched_name_follows_the_windows_flag(self):
        self.assertIn(
            "fun nameOn(windows: Boolean): String = if (windows) windowsName else name",
            source(),
        )
        # The suffix must not be appended anywhere else: that is what made npm probe for npm.exe.
        self.assertNotIn("name + suffix", source())

    def test_the_toolchain_task_passes_the_host_flag_to_every_probe(self):
        text = source()
        self.assertIn("val onWindows = windows.get()", text)
        self.assertIn(
            "RootBuildHelpers.verifyToolchain(execOps, directory, path, onWindows, probe)", text,
        )

    def test_npm_is_probed_as_the_cmd_shim_on_windows_only(self):
        found = commands(source())
        npm = [entry for entry in found if entry[0] == "npm"]
        self.assertEqual([("npm", "npm.cmd")] * 2, npm, npm)
        # Every other command keeps the historical bare name plus ".exe".
        self.assertTrue(all(windows is None for name, windows in found if name != "npm"), found)


class ProtocolNodeCheckOrderTest(unittest.TestCase):
    def test_the_checks_keep_the_former_order(self):
        block = task_block(source(), "protocolNodeCheck")
        markers = [
            'ToolchainCommand("npm", "npm.cmd")',
            'ToolchainCommand("python3")',
            'versionCommand = listOf("node", "--version")',
            'versionCommand = listOf("python3", "--version")',
        ]
        positions = [block.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions), positions)

    def test_the_npm_messages_keep_their_trailing_period(self):
        text = source()
        messages = re.findall(r'missingCommandMessageFormat = "([^"]*)"', text)
        npm_messages = [message for message in messages if "{name}" in message]
        self.assertEqual(['Could not find \'{name}\' on PATH. {installHint}.'] * 2, npm_messages)


if __name__ == "__main__":
    sys.exit(unittest.main())
