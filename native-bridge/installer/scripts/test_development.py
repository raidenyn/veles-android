#!/usr/bin/env python3
"""Contract tests for the OTP-25 development registration wrappers (Task 12).

These tests drive ``register-dev.sh`` / ``remove-dev.sh`` (and, statically, the
PowerShell twins) against synthetic dev build directories. The "maintenance
binary" is a shell stand-in that records its argument vector; no real engine,
Chrome registration or native installer is exercised here. Passing these tests is
portable wrapper evidence only: it is NOT a native Windows/macOS dev walkthrough
and NOT distribution signature acceptance.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import development  # noqa: E402  (sibling module, imported after sys.path setup)

PLACEHOLDER_ID = "abcdefghijklmnopabcdefghijklmnop"
DEV_ID = "hgfedcbahgfedcbahgfedcbahgfedcba"
OTHER_ID = "ponmlkjihgfedcbaponmlkjihgfedcba"
PRODUCTION_ID = "bbbbbbbbbbbbbbbbcccccccccccccccc"
DEV_HOST_NAME = "me.nagaev.veles.bridge.dev"
PRODUCTION_HOST_NAME = "me.nagaev.veles.bridge"
HOST_FILE = "veles-native-host"
MAINTENANCE_FILE = "veles-bridge-maintenance-dev"


def origin(extension_id: str) -> str:
    return f"chrome-extension://{extension_id}/"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def host_bytes(*origins: str) -> bytes:
    body = b"\x7fELF-fake-host\x00" + b"\x00".join(o.encode() for o in origins) + b"\x00tail"
    return body


def maintenance_script(extension_id: str = DEV_ID, host_name: str = DEV_HOST_NAME) -> bytes:
    # The literals the real dev-compiled binary carries are embedded in a comment so the
    # wrapper's byte scan finds them. The body records argv to a sibling log and prints a
    # one-line report like the engine does.
    return (
        "#!/bin/sh\n"
        f"# {host_name} {origin(extension_id)}\n"
        'log="$0.log"\n'
        'printf "%s\\n" "$*" >> "$log"\n'
        'printf "%s\\n" "{\\"operation\\":\\"$1\\",\\"state\\":\\"fake\\"}"\n'
        "exit ${FAKE_ENGINE_EXIT:-0}\n"
    ).encode()


class DevBuild:
    """A synthetic dev build directory, built under a throw-away temporary root."""

    def __init__(self, root: Path, **overrides):
        self.root = root
        self.dir = root / "devbuild"
        self.dir.mkdir()
        host = overrides.pop("host", host_bytes(origin(DEV_ID)))
        maintenance = overrides.pop("maintenance", maintenance_script())
        self.host_path = self.dir / HOST_FILE
        self.host_path.write_bytes(host)
        self.maintenance_path = self.dir / MAINTENANCE_FILE
        self.maintenance_path.write_bytes(maintenance)
        self.maintenance_path.chmod(0o755)
        package = self.dir / "package"
        (package / "payload").mkdir(parents=True)
        (package / "payload" / HOST_FILE).write_bytes(host)
        (package / "descriptor.json").write_text("{}")
        (package / "inventory.json").write_text("{}")
        self.descriptor = {
            "schema": 1,
            "channel": "development",
            "host_name": DEV_HOST_NAME,
            "extension_id": DEV_ID,
            "allowed_origins": [origin(DEV_ID)],
            "host_file": HOST_FILE,
            "host_sha256": sha256(host),
            "maintenance_file": MAINTENANCE_FILE,
            "maintenance_sha256": sha256(maintenance),
            "package_dir": "package",
        }
        self.descriptor.update(overrides)
        self.write_descriptor()

    def write_descriptor(self) -> None:
        (self.dir / development.BUILD_DESCRIPTOR_NAME).write_text(json.dumps(self.descriptor))

    @property
    def engine_log(self) -> Path:
        return Path(str(self.maintenance_path) + ".log")

    def engine_calls(self) -> list[str]:
        if not self.engine_log.exists():
            return []
        return self.engine_log.read_text().splitlines()


def tree_snapshot(root: Path) -> dict[str, tuple[str, str]]:
    snapshot: dict[str, tuple[str, str]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[rel] = ("symlink", os.readlink(path))
        elif path.is_dir():
            snapshot[rel] = ("dir", oct(stat.S_IMODE(path.stat().st_mode)))
        else:
            snapshot[rel] = ("file", sha256(path.read_bytes()))
    return snapshot


def run_script(name: str, *args: str, env: dict | None = None, cwd: Path | None = None):
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(
        ["sh", str(SCRIPTS / name), *args],
        capture_output=True,
        text=True,
        env=merged,
        cwd=cwd,
        timeout=60,
        check=False,
    )


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def build(self, **overrides) -> DevBuild:
        return DevBuild(self.tmp, **overrides)

    def register(self, build: DevBuild, extension_id: str = DEV_ID, *extra: str, env=None):
        return run_script(
            "register-dev.sh",
            "--extension-id",
            extension_id,
            "--host-binary",
            str(build.host_path),
            *extra,
            env=env,
        )

    def remove(self, build: DevBuild, extension_id: str = DEV_ID, *extra: str, env=None):
        return run_script(
            "remove-dev.sh",
            "--extension-id",
            extension_id,
            "--host-binary",
            str(build.host_path),
            *extra,
            env=env,
        )


class RegistrationHappyPath(Fixture):
    def test_matching_dev_build_installs_through_the_dev_engine_only(self):
        build = self.build()
        result = self.register(build)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            build.engine_calls(), [f"install --package {build.dir / 'package'}"]
        )
        self.assertIn('"operation":"install"', result.stdout)

    def test_remove_uses_the_same_checked_engine_with_no_operands(self):
        build = self.build()
        result = self.remove(build)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(build.engine_calls(), ["uninstall"])

    def test_engine_failure_exit_is_propagated_as_failure(self):
        build = self.build()
        result = self.register(build, DEV_ID, env={"FAKE_ENGINE_EXIT": "1"})
        self.assertEqual(result.returncode, 1)


class ProductionIsolation(Fixture):
    def test_prod_roots_names_and_origins_rejected(self):
        # -- No flag can select a root, a host name, an origin, a channel or a manifest.
        build = self.build()
        for flag in (
            "--root",
            "--install-root",
            "--host-name",
            "--origin",
            "--channel",
            "--manifest",
            "--maintenance-binary",
            "--production",
        ):
            with self.subTest(flag=flag):
                result = self.register(build, DEV_ID, flag, "/anything")
                self.assertEqual(result.returncode, 2, (flag, result.stderr))
        self.assertEqual(build.engine_calls(), [], "invalid invocations must never reach the engine")

        # -- Production and placeholder extension IDs / origins as the explicit ID.
        for label, bad_id in (
            ("placeholder", PLACEHOLDER_ID),
            ("wildcard", "*"),
            ("origin form", origin(DEV_ID)),
            ("comma list", f"{DEV_ID},{OTHER_ID}"),
            ("too short", DEV_ID[:-1]),
            ("uppercase", DEV_ID.upper()),
            ("out of alphabet", "q" * 32),
        ):
            with self.subTest(extension_id=label):
                result = self.register(build, bad_id)
                self.assertEqual(result.returncode, 1, (label, result.stderr))
        self.assertEqual(build.engine_calls(), [])

        # -- The approved production ID is rejected when the approved config is present.
        scripts = self.tmp / "scripts-copy"
        shutil.copytree(SCRIPTS, scripts, ignore=shutil.ignore_patterns("__pycache__", "fixtures"))
        config = self.tmp / "config"
        config.mkdir()
        (config / "release.json").write_text(
            json.dumps({"channel": "production", "extension_id": PRODUCTION_ID})
        )
        prod_build = self.build_in(self.tmp / "prodbuild", PRODUCTION_ID)
        result = subprocess.run(
            [
                "sh",
                str(scripts / "register-dev.sh"),
                "--extension-id",
                PRODUCTION_ID,
                "--host-binary",
                str(prod_build.host_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(prod_build.engine_calls(), [])

        # -- Production names / channels / extra origins in the build descriptor.
        for label, change in (
            ("production host name", {"host_name": PRODUCTION_HOST_NAME}),
            ("production channel", {"channel": "production"}),
            ("fixture host name", {"host_name": "me.nagaev.veles.bridge.fixture"}),
            ("wildcard origin", {"allowed_origins": ["chrome-extension://*/"]}),
            (
                "two origins",
                {"allowed_origins": [origin(DEV_ID), origin(OTHER_ID)]},
            ),
            ("no origins", {"allowed_origins": []}),
            ("placeholder origin", {"allowed_origins": [origin(PLACEHOLDER_ID)]}),
            ("origin of another id", {"allowed_origins": [origin(OTHER_ID)]}),
            ("unknown root field", {"root": "/Library/Application Support/Veles/NativeBridge"}),
            ("path-bearing host file", {"host_file": "../" + HOST_FILE}),
            ("path-bearing maintenance file", {"maintenance_file": "../" + MAINTENANCE_FILE}),
            ("escaping package dir", {"package_dir": "../package"}),
            ("absolute package dir", {"package_dir": "/tmp"}),
            ("schema 2", {"schema": 2}),
        ):
            with self.subTest(descriptor=label):
                build = self.build_in(self.tmp / ("d-" + label.replace(" ", "-")), DEV_ID)
                build.descriptor.update(change)
                build.write_descriptor()
                result = self.register(build)
                self.assertEqual(result.returncode, 1, (label, result.stderr))
                self.assertEqual(build.engine_calls(), [], label)

        # -- A host binary that sits inside a production root is never a dev host.
        for parts in (
            ("Library", "Application Support", "Veles", "NativeBridge", "versions", "x"),
            ("AppData", "Local", "Veles", "NativeBridge", "versions", "x"),
        ):
            with self.subTest(root="/".join(parts)):
                inside = self.tmp / "home" / Path(*parts)
                inside.mkdir(parents=True)
                build = self.build_in(inside / "devbuild", DEV_ID)
                result = self.register(build)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(build.engine_calls(), [])

        # -- A development tree is not a production tree: NativeBridgeDev stays allowed.
        dev_ok = self.tmp / "home2" / "Veles" / "NativeBridgeDev" / "x"
        dev_ok.mkdir(parents=True)
        build = self.build_in(dev_ok / "devbuild", DEV_ID)
        self.assertEqual(self.register(build).returncode, 0)

    def build_in(self, directory: Path, extension_id: str, **overrides) -> DevBuild:
        directory.mkdir(parents=True, exist_ok=True)
        host = host_bytes(origin(extension_id))
        maintenance = maintenance_script(extension_id)
        build = DevBuild(
            directory, host=host, maintenance=maintenance, extension_id=extension_id,
            allowed_origins=[origin(extension_id)], **overrides,
        )
        return build

    def test_remove_dev_leaves_production_bytes_unchanged(self):
        # A fake "home" holding a production registration. The wrapper must neither touch
        # it nor hand the engine anything that could point at it.
        home = self.tmp / "fakehome"
        prod = home / "Library" / "Application Support" / "Veles" / "NativeBridge"
        (prod / "versions" / "1.0.0-aaaa").mkdir(parents=True)
        (prod / "versions" / "1.0.0-aaaa" / "host").write_bytes(b"production host bytes")
        (prod / "state").mkdir()
        (prod / "state" / "receipt.json").write_text('{"active":"1.0.0-aaaa"}')
        manifest = (
            home / "Library" / "Application Support" / "Google" / "Chrome"
            / "NativeMessagingHosts" / f"{PRODUCTION_HOST_NAME}.json"
        )
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": PRODUCTION_HOST_NAME}))
        dev_root = home / "Library" / "Application Support" / "Veles" / "NativeBridgeDev"
        dev_root.mkdir(parents=True)
        (dev_root / "dev-state").write_text("dev")

        before = tree_snapshot(home)
        build = self.build()
        result = self.remove(build, DEV_ID, env={"HOME": str(home)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(build.engine_calls(), ["uninstall"])
        after = tree_snapshot(home)
        self.assertEqual(before, after, "the wrapper itself must not modify any registration or root")
        self.assertEqual(
            {k: v for k, v in after.items() if "NativeBridge/" in k or k.endswith("NativeBridge")},
            {k: v for k, v in before.items() if "NativeBridge/" in k or k.endswith("NativeBridge")},
        )

    def test_remove_dev_hands_the_engine_no_root_name_or_origin(self):
        build = self.build()
        result = self.remove(build)
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = build.engine_calls()
        self.assertEqual(argv, ["uninstall"])
        for forbidden in ("NativeBridge", PRODUCTION_HOST_NAME, "--root", "chrome-extension"):
            self.assertNotIn(forbidden, argv[0])

    def test_remove_dev_still_refuses_a_non_dev_build(self):
        build = self.build()
        build.descriptor["channel"] = "production"
        build.write_descriptor()
        result = self.remove(build)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(build.engine_calls(), [])


class HostAllowlistBinding(Fixture):
    def test_host_allowlist_mismatch_rejected(self):
        # Host compiled for another extension than the one named on the command line.
        build = self.build(host=host_bytes(origin(OTHER_ID)))
        build.descriptor["host_sha256"] = sha256(build.host_path.read_bytes())
        build.write_descriptor()
        result = self.register(build)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(build.engine_calls(), [])

        # Command-line ID differs from a consistent descriptor + host pair.
        build = self.build_unique("cli-mismatch")
        result = self.register(build, OTHER_ID)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(build.engine_calls(), [])

        # Host carries the right origin AND another one (an allowlist that is wider than declared).
        wide = self.build_unique(
            "wide", host=host_bytes(origin(DEV_ID), origin(OTHER_ID))
        )
        self.assertEqual(self.register(wide).returncode, 1)
        self.assertEqual(wide.engine_calls(), [])

        # Host carries the placeholder default alongside the dev origin.
        placeholder = self.build_unique(
            "placeholder", host=host_bytes(origin(DEV_ID), origin(PLACEHOLDER_ID))
        )
        self.assertEqual(self.register(placeholder).returncode, 1)

        # Host carries no origin at all.
        empty = self.build_unique("empty", host=b"\x7fELF-no-origin")
        self.assertEqual(self.register(empty).returncode, 1)

        # Tampered host after the descriptor was written (digest binding).
        tampered = self.build_unique("tampered")
        tampered.host_path.write_bytes(host_bytes(origin(DEV_ID)) + b"x")
        self.assertEqual(self.register(tampered).returncode, 1)

        # Package payload is not the descriptor's host.
        swapped = self.build_unique("swapped")
        (swapped.dir / "package" / "payload" / HOST_FILE).write_bytes(host_bytes(origin(DEV_ID)) + b"y")
        self.assertEqual(self.register(swapped).returncode, 1)

        # Maintenance binary tampered / compiled for a different extension.
        other_engine = self.build_unique("other-engine", maintenance=maintenance_script(OTHER_ID))
        self.assertEqual(self.register(other_engine).returncode, 1)
        self.assertEqual(other_engine.engine_calls(), [])

        # The matching pair is accepted (control).
        good = self.build_unique("good")
        self.assertEqual(self.register(good).returncode, 0)

    def build_unique(self, name: str, **overrides) -> DevBuild:
        directory = self.tmp / name
        directory.mkdir()
        return DevBuild(directory, **overrides)

    def test_build_descriptor_must_be_strict_json(self):
        build = self.build()
        text = (build.dir / development.BUILD_DESCRIPTOR_NAME).read_text()
        for label, payload in (
            ("duplicate key", text[:-1] + ', "channel": "production"}'),
            ("not json", "nope"),
            ("array", "[]"),
            ("oversized", " " * (development.MAX_DESCRIPTOR_BYTES + 1) + text),
        ):
            with self.subTest(label=label):
                (build.dir / development.BUILD_DESCRIPTOR_NAME).write_text(payload)
                self.assertEqual(self.register(build).returncode, 1)
        (build.dir / development.BUILD_DESCRIPTOR_NAME).unlink()
        self.assertEqual(self.register(build).returncode, 1)
        self.assertEqual(build.engine_calls(), [])

    def test_host_and_maintenance_symlinks_and_special_files_are_rejected(self):
        build = self.build()
        real = build.dir / "real-host"
        build.host_path.rename(real)
        build.host_path.symlink_to(real)
        self.assertEqual(self.register(build).returncode, 1)
        self.assertEqual(build.engine_calls(), [])


class ModuleSurface(unittest.TestCase):
    def test_extension_id_policy_is_exact(self):
        self.assertEqual(development.validate_extension_id(DEV_ID), DEV_ID)
        for bad in (PLACEHOLDER_ID, "", "*", DEV_ID + "a", DEV_ID[:-1], DEV_ID.upper()):
            with self.assertRaises(development.Refusal):
                development.validate_extension_id(bad)
        with self.assertRaises(development.Refusal):
            development.validate_extension_id(PRODUCTION_ID, production_ids={PRODUCTION_ID})

    def test_the_development_identity_is_distinct_from_production(self):
        self.assertEqual(development.DEV_HOST_NAME, DEV_HOST_NAME)
        self.assertEqual(development.DEV_ROOT_DIR, "NativeBridgeDev")
        self.assertNotEqual(development.DEV_HOST_NAME, development.PRODUCTION_HOST_NAME)
        self.assertEqual(development.PRODUCTION_HOST_NAME, PRODUCTION_HOST_NAME)
        self.assertNotEqual(development.DEV_ROOT_DIR, development.PRODUCTION_ROOT_DIR)


class PowerShellTwins(unittest.TestCase):
    """The PowerShell wrappers cannot be executed on this host (no pwsh). They are held to the
    same lexical contract as the shell wrappers; this is NOT evidence that they run."""

    def read(self, name: str) -> str:
        return (SCRIPTS / name).read_text(encoding="utf-8")

    def test_ps1_twins_have_the_required_parameters_and_no_root_override(self):
        for name in ("register-dev.ps1", "remove-dev.ps1"):
            text = self.read(name)
            with self.subTest(script=name):
                self.assertRegex(text, r"\[Parameter\(Mandatory\s*=\s*\$true\)\]\s*\[string\]\$ExtensionId")
                self.assertRegex(text, r"\[Parameter\(Mandatory\s*=\s*\$true\)\]\s*\[string\]\$HostBinary")
                for forbidden in ("$Root", "$InstallRoot", "$HostName", "$Origin", "$Channel", "$Manifest"):
                    self.assertNotIn(forbidden, text)
                self.assertIn("development.py", text)
                self.assertNotIn("Invoke-Expression", text)
                self.assertNotRegex(text, r"(?i)\biex\b")
                self.assertNotRegex(text, r"(?i)HKLM|HKCU:|reg\s+add|Remove-Item")

    def test_sh_wrappers_pass_only_the_two_documented_flags(self):
        for name in ("register-dev.sh", "remove-dev.sh"):
            text = self.read(name)
            with self.subTest(script=name):
                self.assertIn("--extension-id", text)
                self.assertIn("--host-binary", text)
                self.assertIn("development.py", text)
                self.assertNotRegex(text, r"\brm\s+-")
                self.assertNotRegex(text, r"\beval\b")


if __name__ == "__main__":
    unittest.main()
