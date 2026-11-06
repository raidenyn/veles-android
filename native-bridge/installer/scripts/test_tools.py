#!/usr/bin/env python3
"""Tool-identity contract tests for the OTP-25 standalone installers (Task 1).

Standard library only. Run from the repository root:

    python3 -m unittest discover -s native-bridge/installer/scripts -p test_tools.py

These are static contract tests. They prove that the checked-in tool lock pins
the reviewed NSIS archive, Rust and Xcode/SDK identities exactly, refuses any
Tauri/WiX payload, and that ``release_config.validate_tools`` fails closed on a
missing, mismatched or unresolved compiler/linker/SDK fingerprint. They do not
claim that a Windows/macOS build, an NSIS compilation or a CI inventory run
happened: those native steps are recorded separately in the task report.
"""

import copy
import re
import sys
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALLER = HERE.parent
sys.path.insert(0, str(HERE))

import release_config  # noqa: E402  (sibling module, imported after sys.path setup)

LOCK_PATH = INSTALLER / "config" / "tools.lock.json"
TOOLCHAIN_PATH = INSTALLER / "rust-toolchain.toml"
PROVISIONER_PATH = HERE / "provision-nsis.ps1"

# The already-reviewed archive pin the design and plan both quote verbatim.
NSIS_URL = (
    "https://github.com/tauri-apps/binary-releases/releases/download/nsis-3/nsis-3.zip"
)
NSIS_SHA256 = "1bb9fc85ee5b220d3869325dbb9d191dfe6537070f641c30fbb275c97051fd0c"

# Values that would make a pin meaningless. "*" is also rejected as a substring.
WILDCARD_VALUES = {"*", "latest", "current", "stable", "nightly", "any", "unspecified", "tbd", "todo", ""}


def load_lock():
    return release_config.load_lock(LOCK_PATH)


def complete_observation(lock, target):
    """Build a complete observed inventory for one target from the lock itself.

    Only concretely pinned tools can be observed: an entry with
    ``required_unresolved`` has no approved identity to report yet.
    """
    tools = {}
    for name, entry in lock["tools"].items():
        if target not in entry["targets"] or "identity" not in entry:
            continue
        tools[name] = dict(entry["identity"])
    return {
        "target": target,
        "tools": tools,
        "archives": {
            name: {"sha256": entry["sha256"], "version": entry["version"]}
            for name, entry in lock["archives"].items()
        },
    }


def fill_unresolved(lock):
    """Return a copy of the lock with test-only identities for unresolved tools.

    The synthetic values exist only to prove the validator's comparison and
    blocking behaviour; they are never written to the checked-in lock.
    """
    filled = copy.deepcopy(lock)
    for entry in filled["tools"].values():
        unresolved = entry.pop("required_unresolved", None)
        if unresolved is not None:
            entry["identity"] = {field: "fixture-" + field for field in unresolved["must_supply"]}
    return filled


def walk_strings(value, path=()):
    """Yield (path, string) for every string in a nested JSON value."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk_strings(key, path + ("<key>",))
            yield from walk_strings(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_strings(item, path + (str(index),))
    elif isinstance(value, str):
        yield path, value


class NsisPinTests(unittest.TestCase):
    def test_nsis_pin_matches_reviewed_archive(self):
        lock = load_lock()
        self.assertEqual(set(lock["archives"]), {"nsis"}, "the lock must pin exactly the one reviewed archive")
        nsis = lock["archives"]["nsis"]
        self.assertEqual(nsis["url"], NSIS_URL)
        self.assertEqual(nsis["sha256"], NSIS_SHA256)
        self.assertEqual(nsis["version"], "3.08")
        self.assertEqual(nsis["extract_root"], "nsis-3.08")
        self.assertEqual(nsis["makensis"], "nsis-3.08/makensis.exe")
        self.assertEqual(release_config.validate_lock(lock), [], "the checked-in lock must be structurally valid")

        # A different URL or digest for the same field must be rejected, so the
        # test above cannot be satisfied by an unreviewed archive.
        for field, value in (
            ("url", "https://example.invalid/nsis-3.zip"),
            ("sha256", "0" * 64),
            ("version", "3.09"),
        ):
            broken = copy.deepcopy(lock)
            broken["archives"]["nsis"][field] = value
            errors = release_config.validate_lock(broken)
            self.assertTrue(errors, f"a changed NSIS {field} must be rejected")
            self.assertTrue(
                any("nsis" in error.lower() for error in errors),
                f"the NSIS {field} rejection must name the archive: {errors}",
            )

    def test_no_tauri_or_wix_payload(self):
        lock = load_lock()
        text = LOCK_PATH.read_text(encoding="utf-8").lower()
        self.assertNotIn("wix", text, "the standalone installer lock must not reference WiX")
        self.assertNotIn(
            "nsis_tauri_utils",
            text,
            "the standalone installer lock must not reference the Tauri NSIS plugin",
        )
        self.assertNotIn("windows-tools.json", text, "the lock must not import the Tauri archive manifest")

        wix_archive = copy.deepcopy(lock)
        wix_archive["archives"]["wix"] = {
            "version": "3.14.1",
            "url": "https://example.invalid/wix314-binaries.zip",
            "sha256": "1" * 64,
            "archive_file": "wix314-binaries.zip",
            "extract_root": "wix314",
            "makensis": "wix314/candle.exe",
            "version_argument": "/?" ,
        }
        errors = release_config.validate_lock(wix_archive)
        self.assertTrue(errors, "a WiX archive entry must be rejected")
        self.assertTrue(any("wix" in error.lower() for error in errors), errors)

        plugin_path = copy.deepcopy(lock)
        plugin_path["archives"]["nsis"]["makensis"] = "nsis-3.08/Plugins/x86-unicode/nsis_tauri_utils.dll"
        errors = release_config.validate_lock(plugin_path)
        self.assertTrue(errors, "a Tauri NSIS plugin path must be rejected")
        self.assertTrue(any("nsis_tauri_utils" in error for error in errors), errors)

        plugin_tool = copy.deepcopy(lock)
        plugin_tool["tools"]["wix"] = {
            "targets": ["windows-x86_64"],
            "identity": {"version": "3.14.1"},
        }
        errors = release_config.validate_lock(plugin_tool)
        self.assertTrue(errors, "a WiX tool entry must be rejected")
        self.assertTrue(any("wix" in error.lower() for error in errors), errors)

        # The provisioner may only download and expand the single locked NSIS
        # archive: no WiX toolset, no nsis_tauri_utils.dll, no Tauri manifest.
        source = PROVISIONER_PATH.read_text(encoding="utf-8")
        self.assertIn("tools.lock.json", source, "the provisioner must read the reviewed tool lock")
        self.assertNotIn("windows-tools.json", source, "the provisioner must not reuse the Tauri WiX/NSIS manifest")
        self.assertEqual(source.count("Expand-Archive"), 1, "the provisioner must expand exactly one archive")
        self.assertEqual(source.count("Invoke-WebRequest"), 1, "the provisioner must download exactly one archive")
        deny_list = re.search(r"\$ForbiddenPayload\s*=\s*@\(([^)]*)\)", source)
        self.assertIsNotNone(deny_list, "the provisioner must declare a forbidden-payload deny list")
        for token in ("wix", "nsis_tauri_utils"):
            self.assertIn(token, deny_list.group(1).lower(), f"the provisioner deny list must include {token}")


class ValidateToolsTests(unittest.TestCase):
    def test_tool_mismatch_and_missing_identity_block(self):
        lock = load_lock()

        # macOS identities are pinned exactly, so any drift blocks.
        macos = complete_observation(lock, "macos-arm64")
        self.assertEqual(release_config.validate_tools(lock, macos), [])

        missing_tool = copy.deepcopy(macos)
        del missing_tool["tools"]["xcode"]
        errors = release_config.validate_tools(lock, missing_tool)
        self.assertTrue(errors, "a missing Xcode identity must block")
        self.assertTrue(any("xcode" in error for error in errors), errors)

        missing_build = copy.deepcopy(macos)
        del missing_build["tools"]["xcode"]["build"]
        errors = release_config.validate_tools(lock, missing_build)
        self.assertTrue(errors, "a missing Xcode build fingerprint must block")
        self.assertTrue(any("xcode.build" in error for error in errors), errors)

        wrong_sdk = copy.deepcopy(macos)
        wrong_sdk["tools"]["macos_sdk"]["version"] = "macosx26.4"
        errors = release_config.validate_tools(lock, wrong_sdk)
        self.assertTrue(errors, "a mismatched macOS SDK version must block")
        self.assertTrue(any("macos_sdk.version" in error for error in errors), errors)

        missing_channel = copy.deepcopy(macos)
        del missing_channel["tools"]["rust"]["channel"]
        errors = release_config.validate_tools(lock, missing_channel)
        self.assertTrue(errors, "a missing Rust channel must block")
        self.assertTrue(any("rust.channel" in error for error in errors), errors)

        # The real lock has no approved Windows compiler/linker/SDK identity, so
        # every Windows build must block even with a complete observable inventory.
        windows = complete_observation(lock, "windows-x86_64")
        errors = release_config.validate_tools(lock, windows)
        self.assertTrue(errors, "the unresolved Windows toolchain must block")
        self.assertTrue(any("msvc" in error and "unresolved" in error for error in errors), errors)
        self.assertTrue(any("windows_sdk" in error and "unresolved" in error for error in errors), errors)

        # Once the exact fingerprints are supplied (fixture values here), the same
        # mechanism compares them field by field and still blocks on any omission.
        filled = fill_unresolved(lock)
        filled_windows = complete_observation(filled, "windows-x86_64")
        self.assertEqual(release_config.validate_tools(filled, filled_windows), [])
        for tool, field in (
            ("msvc", "cl_version"),
            ("msvc", "linker_version"),
            ("windows_sdk", "windows_sdk_version"),
            ("windows_sdk", "ucrt_version"),
        ):
            broken = copy.deepcopy(filled_windows)
            del broken["tools"][tool][field]
            errors = release_config.validate_tools(filled, broken)
            self.assertTrue(errors, f"a missing {tool}.{field} fingerprint must block")
            self.assertTrue(any(f"{tool}.{field}" in error for error in errors), errors)

        mismatch = copy.deepcopy(filled_windows)
        mismatch["tools"]["msvc"]["linker_version"] = "fixture-other-linker"
        errors = release_config.validate_tools(filled, mismatch)
        self.assertTrue(errors, "a mismatched linker fingerprint must block")
        self.assertTrue(
            any("linker_version" in error and "fixture-other-linker" in error for error in errors), errors
        )

        unset_archive = copy.deepcopy(macos)
        del unset_archive["archives"]["nsis"]["sha256"]
        errors = release_config.validate_tools(lock, unset_archive)
        self.assertTrue(errors, "a build must report the verified NSIS archive digest")
        self.assertTrue(any("nsis" in error and "sha256" in error for error in errors), errors)

    def test_windows_compiler_and_sdk_identity_is_unresolved_not_guessed(self):
        lock = load_lock()
        for tool, required in (
            ("msvc", {"cl_version", "linker_version"}),
            ("windows_sdk", {"windows_sdk_version", "ucrt_version"}),
        ):
            entry = lock["tools"][tool]
            self.assertEqual(entry["targets"], ["windows-x86_64"], f"{tool} is a Windows-target tool")
            self.assertIn(
                "required_unresolved", entry, f"{tool} must be recorded as required but unresolved"
            )
            self.assertNotIn("identity", entry, f"{tool} must not carry an invented identity")
            unresolved = entry["required_unresolved"]
            self.assertTrue(
                unresolved["reason"].strip(), f"{tool} must explain why the identity is unresolved"
            )
            self.assertIn(
                "windows-2025",
                unresolved["reason"],
                f"{tool} must name the runner whose inventory is missing",
            )
            supplied = set(unresolved["must_supply"])
            self.assertTrue(
                required <= supplied, f"{tool} must list {sorted(required)}, found {sorted(supplied)}"
            )
            self.assertTrue(
                any(field.endswith("_sha256") for field in supplied),
                f"{tool} must require a file digest, not only a version",
            )
            # No invented hash may stand in for the missing identity.
            self.assertFalse(
                [
                    value
                    for _, value in walk_strings(entry)
                    if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                ],
                f"{tool} must not contain a fabricated digest",
            )

    def test_lock_pins_reviewed_rust_xcode_and_sdk_identities(self):
        lock = load_lock()
        self.assertEqual(lock["schema"], 1)
        self.assertEqual(lock["reviewed"], "2026-11-06")
        self.assertEqual(set(lock["targets"]), {"windows-x86_64", "macos-arm64"})
        self.assertEqual(lock["targets"]["windows-x86_64"]["runner"], "windows-2025")
        self.assertEqual(lock["targets"]["windows-x86_64"]["build_target"], "x86_64-pc-windows-msvc")
        self.assertEqual(lock["targets"]["macos-arm64"]["runner"], "macos-26")
        self.assertEqual(lock["targets"]["macos-arm64"]["build_target"], "aarch64-apple-darwin")
        self.assertEqual(lock["targets"]["macos-arm64"]["developer_dir"], "/Applications/Xcode_26.6.app")

        self.assertEqual(lock["tools"]["xcode"]["identity"], {"version": "26.6", "build": "17F113"})
        self.assertEqual(lock["tools"]["macos_sdk"]["identity"], {"version": "macosx26.5"})
        self.assertEqual(
            lock["tools"]["xcode"]["identity"]["build"],
            "17F113",
            "the Xcode build fingerprint must match the value the macOS workflow already enforces",
        )

        toolchain = tomllib.loads(TOOLCHAIN_PATH.read_text(encoding="utf-8"))["toolchain"]
        rust = lock["tools"]["rust"]["identity"]
        self.assertEqual(rust["channel"], "1.98.0")
        self.assertEqual(toolchain["channel"], rust["channel"], "rust-toolchain.toml must pin the locked Rust channel")
        self.assertEqual(toolchain["profile"], rust["profile"])
        self.assertEqual(sorted(toolchain["components"]), sorted(rust["components"].split(",")))
        self.assertEqual(sorted(toolchain["targets"]), sorted(rust["targets"].split(",")))
        self.assertIn("x86_64-pc-windows-msvc", rust["targets"])
        self.assertIn("aarch64-apple-darwin", rust["targets"])
        self.assertEqual(lock["tools"]["rust"]["targets"], ["windows-x86_64", "macos-arm64"])

    def test_lock_rejects_wildcards_placeholders_and_unknown_fields(self):
        lock = load_lock()
        for mutate in (
            lambda broken: broken["archives"]["nsis"].__setitem__("version", "latest"),
            lambda broken: broken["archives"]["nsis"].__setitem__("sha256", "1bb9fc*"),
            lambda broken: broken["tools"]["rust"]["identity"].__setitem__("channel", "1.98.*"),
            lambda broken: broken["targets"]["windows-x86_64"].__setitem__("runner", "windows-latest"),
            lambda broken: broken["tools"]["rust"].__setitem__("download", "https://example.invalid/rustup"),
            lambda broken: broken["tools"]["xcode"]["identity"].__setitem__("build", ""),
        ):
            broken = copy.deepcopy(lock)
            mutate(broken)
            self.assertTrue(release_config.validate_lock(broken), "an inexact or unknown pin must be rejected")

        self.assertTrue(release_config.validate_lock({"schema": 2}), "an unknown schema version must be rejected")
        self.assertTrue(
            release_config.validate_tools(lock, {"target": "linux-x86_64"}),
            "an unlocked target must be rejected",
        )
        self.assertTrue(
            release_config.validate_tools(lock, {}), "an observation without a target must be rejected"
        )

    def test_lock_parser_rejects_duplicate_keys(self):
        text = LOCK_PATH.read_text(encoding="utf-8")
        duplicated = text.replace('"schema": 1,', '"schema": 1,\n  "schema": 1,', 1)
        self.assertNotEqual(text, duplicated)
        with self.assertRaises(ValueError):
            release_config.parse_lock(duplicated)


class ProvisionerContractTests(unittest.TestCase):
    def test_provisioner_is_windows_only_and_checks_destination(self):
        source = PROVISIONER_PATH.read_text(encoding="utf-8")
        # Parameters before every executable statement, like the other repo scripts.
        self.assertRegex(source, r"\A(?:\s*(?:#.*|<#[\s\S]*?#>)\s*\n)*\s*(?:\[CmdletBinding\(\)\]\s*\n\s*)?param\(")
        self.assertIn("$Destination", source)
        self.assertIn("must not already exist", source, "a pre-existing destination must fail")
        self.assertIn("IsPathRooted", source, "the destination must be an absolute path")
        self.assertIn("-contains '..'", source, "a '..' destination or archive entry must fail")
        self.assertIn("ReparsePoint", source, "a reparse-point destination ancestor must fail")
        self.assertIn("Get-FileHash", source, "the downloaded archive must be hashed")
        self.assertIn("SHA256 mismatch", source, "a hash mismatch must fail")
        self.assertIn("makensis", source.lower(), "the provisioner must verify the extracted compiler")
        # Windows-only: enforced at runtime, not just documented.
        self.assertIn("$IsWindows", source, "the provisioner must refuse to run off Windows")
        self.assertRegex(source, r"(?i)windows-only")
        # The pin has exactly one source of truth: the reviewed lock.
        self.assertIn("Get-RequiredProperty $archives 'nsis'", source)
        self.assertNotIn(NSIS_SHA256, source, "the provisioner must read the digest from the lock, not duplicate it")
        self.assertNotIn(NSIS_URL, source, "the provisioner must read the URL from the lock, not duplicate it")


if __name__ == "__main__":
    unittest.main()
