#!/usr/bin/env python3
"""The production carrier wiring (OTP-25 branch-review round 3).

Round 2 added the build-time channel (`VELES_EMBEDDED_CARRIER` -> `build.rs` ->
`EMBEDDED_CARRIER`), but nothing in the production path ever set it. These tests pin the
wiring that closes that gap, on the Linux development host:

* the assembler publishes the *unsealed* carrier document that the engine compiles in, and
  the *sealed-placeholder* document that Task 13 seals inside the Windows engine resource;
* the assembler can verify a carrier against the exact descriptor, the channel and (when
  given) a built image, so the engine build consumes an already-verified artifact;
* the real `build.rs` embeds the assembler's exact bytes in a production build, fails closed
  when a production build has no carrier, and refuses a release carrier in a development
  build;
* a fixture build without a carrier still builds and stays honestly unreleased.

No native Windows/macOS tool, compiler target or signer is involved. The only external
program is `rustc`, used to compile the actual build script; it is skipped when absent
(never reported as a pass).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALLER = HERE.parent
sys.path.insert(0, str(HERE))

import assemble_unsigned as au  # noqa: E402

COMMIT = "0123456789abcdef0123456789abcdef01234567"
EXTENSION_ALIAS = "production"
EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"
RUSTC = shutil.which("rustc")


def config(platform="windows", channel="production"):
    identity = "me.nagaev.veles.bridge" + (".fixture" if channel == "fixture" else "")
    return {
        "schema": 1,
        "channel": channel,
        "version": "1.2.3",
        "target": "windows-x86_64" if platform == "windows" else "macos-arm64",
        "minimum_os": "10" if platform == "windows" else "26.0",
        "extension_id": EXTENSION_ALIAS,
        "host_name": identity,
        "bundle_id": identity,
        "api_min": 1,
        "api_max": 1,
        "policy": {
            "publishers": ["Example Publisher"],
            "denied_payloads": [],
            "rollback_payloads": [],
            "epoch": 1,
        },
    }


def assemble_sample(root, platform="windows", channel="production", tag="a", value=None, engine_bytes=None):
    """Assemble one synthetic unsigned tree; the inputs are fabricated, the code is real."""
    root = Path(root)
    value = value if value is not None else config(platform, channel)
    config_path = root / f"config-{tag}.json"
    config_path.write_text(json.dumps(value), encoding="utf-8")
    binaries = root / f"bin-{tag}"
    binaries.mkdir()
    names = {
        "windows": (
            "veles-bridge-maintenance.exe",
            "veles-bridge-cleanup.exe",
            "veles-native-host.exe",
            "uninstall.exe",
        ),
        "macos": ("veles-bridge-maintenance", "veles-native-host"),
    }[platform]
    engine_name = "veles-bridge-maintenance.exe" if platform == "windows" else "veles-bridge-maintenance"
    for name in names:
        content = engine_bytes if name == engine_name and engine_bytes is not None else f"{platform}:{name}\n".encode() * 8
        (binaries / name).write_bytes(content)
    out = root / f"out-{tag}"
    frontend = None
    packager = None
    if platform == "macos":
        frontend = root / f"frontend-{tag}"
        frontend.write_bytes(b"frontend\n" * 8)
        packager = HERE / "package-macos.sh"
    au.assemble(
        platform=platform,
        channel=channel,
        config=config_path,
        commit=COMMIT,
        binaries=binaries,
        output=out,
        frontend=frontend,
        packager=packager,
    )
    return out


class Workspace(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)

    def assemble(self, platform="windows", channel="production", tag="a", value=None):
        return assemble_sample(self.root, platform, channel, tag, value)


class AssemblerCarrier(Workspace):
    """The assembler is the single authority for both carrier documents."""

    def test_windows_publishes_the_unsealed_embedded_carrier_that_matches_the_descriptor(self):
        out = self.assemble()
        descriptor = (out / "tree" / "package" / "descriptor.json").read_bytes()
        embedded = (out / "embedded-carrier.bin").read_bytes()
        resource = (out / "carrier.bin").read_bytes()

        self.assertEqual(b"VELESPC1", embedded[:8])
        self.assertIn(descriptor, embedded)
        # Unsealed: no sealed-inventory digest slot, so the compile-time document never
        # looks like the placeholder Task 13 fills in place.
        self.assertEqual(0, int.from_bytes(embedded[10:12], "little"))
        self.assertEqual(len(embedded), embedded.find(descriptor) + len(descriptor))
        # The resource document still carries the zeroed placeholder Task 13 needs.
        self.assertEqual(1, int.from_bytes(resource[10:12], "little"))
        self.assertEqual(b"\x00" * 32, resource[-32:])
        self.assertEqual(embedded[32 : 32 + len(descriptor)], resource[32 : 32 + len(descriptor)])

    def test_macos_publishes_the_same_bytes_it_packages_in_carrier_app(self):
        out = self.assemble(platform="macos", tag="m")
        package = out / "tree" / "Veles Bridge Installer.app" / "Contents" / "Resources" / "package"
        embedded = (out / "embedded-carrier.bin").read_bytes()
        packaged = (package / "carrier.app" / "Contents" / "Resources" / "veles-policy-carrier").read_bytes()
        self.assertEqual(embedded, packaged)
        self.assertEqual(0, int.from_bytes(embedded[10:12], "little"))
        self.assertIn((package / "descriptor.json").read_bytes(), embedded)

    def test_verify_carrier_accepts_the_assemblers_own_document(self):
        out = self.assemble()
        au.verify_carrier(
            carrier=out / "embedded-carrier.bin",
            descriptor=out / "tree" / "package" / "descriptor.json",
            channel="production",
        )
        # The sealed-placeholder document is the same descriptor and is accepted too.
        au.verify_carrier(
            carrier=out / "carrier.bin",
            descriptor=out / "tree" / "package" / "descriptor.json",
            channel="production",
        )

    def test_verify_carrier_refuses_another_channels_carrier(self):
        out = self.assemble()
        with self.assertRaises(au.AssemblyError):
            au.verify_carrier(
                carrier=out / "embedded-carrier.bin",
                descriptor=out / "tree" / "package" / "descriptor.json",
                channel="fixture",
            )

    def test_verify_carrier_refuses_a_descriptor_that_is_not_the_packages(self):
        out = self.assemble()
        descriptor = self.root / "other-descriptor.json"
        value = json.loads((out / "tree" / "package" / "descriptor.json").read_bytes())
        value["version"] = "9.9.9"
        descriptor.write_bytes(au.canonical_json(value))
        with self.assertRaises(au.AssemblyError):
            au.verify_carrier(
                carrier=out / "embedded-carrier.bin", descriptor=descriptor, channel="production"
            )

    def test_verify_carrier_requires_exactly_one_copy_inside_an_image(self):
        out = self.assemble()
        carrier = (out / "embedded-carrier.bin").read_bytes()
        descriptor = out / "tree" / "package" / "descriptor.json"
        for count, expected_error in ((0, True), (1, False), (2, True)):
            with self.subTest(count=count):
                image = self.root / f"image-{count}.bin"
                image.write_bytes(b"MZ" + carrier * count + b"tail")
                if expected_error:
                    with self.assertRaises(au.AssemblyError):
                        au.verify_carrier(
                            carrier=out / "embedded-carrier.bin", descriptor=descriptor,
                            channel="production", image=image,
                        )
                else:
                    au.verify_carrier(
                        carrier=out / "embedded-carrier.bin", descriptor=descriptor,
                        channel="production", image=image,
                    )

    def test_a_fixture_assembly_publishes_a_fixture_carrier_not_a_production_one(self):
        out = self.assemble(channel="fixture", tag="f", value=config("windows", "fixture"))
        descriptor = json.loads((out / "tree" / "package" / "descriptor.json").read_bytes())
        self.assertEqual("fixture", descriptor["channel"])
        au.verify_carrier(
            carrier=out / "embedded-carrier.bin",
            descriptor=out / "tree" / "package" / "descriptor.json",
            channel="fixture",
        )
        with self.assertRaises(au.AssemblyError):
            au.verify_carrier(
                carrier=out / "embedded-carrier.bin",
                descriptor=out / "tree" / "package" / "descriptor.json",
                channel="production",
            )

    def test_the_embedded_carrier_is_byte_identical_across_independent_assemblies(self):
        first = self.assemble(tag="a")
        second = self.assemble(tag="b")
        self.assertEqual(
            (first / "embedded-carrier.bin").read_bytes(),
            (second / "embedded-carrier.bin").read_bytes(),
        )
        self.assertEqual(
            (first / "carrier.bin").read_bytes(), (second / "carrier.bin").read_bytes()
        )

    def test_the_payload_identity_does_not_depend_on_the_engine_image(self):
        # Rebuild ordering: the carrier is computed from the payload, which never contains the
        # engine. Building the engine again with the carrier compiled in therefore cannot change
        # the descriptor (or the payload id) the engine was built against.
        bootstrap = assemble_sample(self.root, tag="bootstrap")
        embedded = assemble_sample(
            self.root, tag="embedded", engine_bytes=b"carrier-bearing engine\n" * 64
        )
        first = json.loads((bootstrap / "tree" / "package" / "descriptor.json").read_bytes())
        second = json.loads((embedded / "tree" / "package" / "descriptor.json").read_bytes())
        self.assertEqual(first, second)
        self.assertEqual(first["payload_id"], second["payload_id"])
        self.assertTrue(
            (bootstrap / "tree" / "package" / "carrier.exe").read_bytes()
            != (embedded / "tree" / "package" / "carrier.exe").read_bytes()
        )
        self.assertEqual(
            (bootstrap / "embedded-carrier.bin").read_bytes(),
            (embedded / "embedded-carrier.bin").read_bytes(),
        )


@unittest.skipUnless(RUSTC, "rustc is required to exercise the real build script")
class BuildScript(unittest.TestCase):
    """The real `build.rs`, compiled by rustc, not a re-implementation of its rule."""

    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temporary.name)
        cls.binary = cls.root / "build-script"
        built = subprocess.run(
            [RUSTC, "--edition", "2021", str(INSTALLER / "build.rs"), "-o", str(cls.binary)],
            cwd=INSTALLER, capture_output=True, text=True, check=False,
        )
        if built.returncode != 0:
            raise AssertionError(f"build.rs does not compile: {built.stderr}")

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def run_build(self, tag, *, channel=None, carrier=None):
        out = self.root / f"out-{tag}"
        out.mkdir()
        env = dict(os.environ)
        for name in ("VELES_EMBEDDED_CARRIER", "VELES_BUILD_CHANNEL"):
            env.pop(name, None)
        if channel is not None:
            env["VELES_BUILD_CHANNEL"] = channel
        if carrier is not None:
            env["VELES_EMBEDDED_CARRIER"] = str(carrier)
        env["OUT_DIR"] = str(out)
        done = subprocess.run([str(self.binary)], cwd=INSTALLER, env=env, capture_output=True, text=True, check=False)
        return done, out

    def assembled(self, tag):
        return assemble_sample(self.root, tag=tag)

    def test_a_production_build_without_the_carrier_fails_closed(self):
        done, out = self.run_build("production-absent", channel="production")
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("VELES_EMBEDDED_CARRIER", done.stderr)
        self.assertIn("production", done.stderr)
        self.assertFalse((out / "embedded_carrier.rs").exists())

    def test_a_production_build_embeds_the_assemblers_exact_bytes(self):
        out = self.assembled("a")
        carrier = (out / "embedded-carrier.bin").read_bytes()
        done, generated = self.run_build("production-present", channel="production", carrier=out / "embedded-carrier.bin")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(carrier, (generated / "embedded_carrier.bin").read_bytes())
        source = (generated / "embedded_carrier.rs").read_text(encoding="utf-8")
        self.assertIn("Some(include_bytes!", source)

    def test_a_fixture_build_without_a_carrier_is_unreleased_not_a_build_error(self):
        done, generated = self.run_build("fixture-absent", channel="fixture")
        self.assertEqual(done.returncode, 0, done.stderr)
        source = (generated / "embedded_carrier.rs").read_text(encoding="utf-8")
        self.assertIn("EMBEDDED_CARRIER: Option<&[u8]> = None", source)
        self.assertFalse((generated / "embedded_carrier.bin").exists())

    def test_a_fixture_build_embeds_the_fixture_carrier(self):
        fixture = assemble_sample(
            self.root, channel="fixture", tag="fixpos", value=config("windows", "fixture")
        )
        carrier = (fixture / "embedded-carrier.bin").read_bytes()
        done, generated = self.run_build(
            "fixture-present", channel="fixture", carrier=fixture / "embedded-carrier.bin"
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(carrier, (generated / "embedded_carrier.bin").read_bytes())
        self.assertIn(
            "Some(include_bytes!",
            (generated / "embedded_carrier.rs").read_text(encoding="utf-8"),
        )

    def test_a_fixture_build_refuses_a_production_carrier(self):
        production = assemble_sample(self.root, tag="prodneg")
        done, generated = self.run_build(
            "fixture-mismatch", channel="fixture", carrier=production / "embedded-carrier.bin"
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("fixture", done.stderr)
        self.assertFalse((generated / "embedded_carrier.rs").exists())

    def test_a_production_build_refuses_a_fixture_carrier(self):
        fixture = assemble_sample(
            self.root, channel="fixture", tag="fixneg", value=config("windows", "fixture")
        )
        done, generated = self.run_build(
            "production-mismatch", channel="production", carrier=fixture / "embedded-carrier.bin"
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("production", done.stderr)
        self.assertFalse((generated / "embedded_carrier.rs").exists())

    def test_a_development_build_refuses_a_release_carrier(self):
        out = self.assembled("dev")
        done, generated = self.run_build("development", channel="development", carrier=out / "embedded-carrier.bin")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("development", done.stderr)
        self.assertFalse((generated / "embedded_carrier.rs").exists())


if __name__ == "__main__":
    unittest.main()
