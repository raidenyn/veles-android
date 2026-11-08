#!/usr/bin/env python3
"""Behavioural tests for the unsigned-input assembler (OTP-25 Task 14).

``assemble_unsigned.py`` turns already-built executables plus an approved release config into the
unsigned input tree Task 13's gate consumes (``tree.tar`` + ``provenance.json``). Everything here
runs on the Linux development host with fabricated executables: no compiler, signer, NSIS or Swift
is involved, and no claim is made about real Windows/macOS binaries.

Run from the repository root:

    python3 -m unittest discover -s native-bridge/installer/scripts -p test_assemble_unsigned.py
"""

import hashlib
import io
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import assemble_unsigned as au  # noqa: E402
import package_inputs  # noqa: E402
import release_config  # noqa: E402
import release_gate  # noqa: E402

COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER_COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
EXTENSION_ALIAS = "production"
EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"


def config(platform="windows", channel="production", **overrides):
    identity = "me.nagaev.veles.bridge" + (".fixture" if channel == "fixture" else "")
    value = {
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
    value.update(overrides)
    return value


RESOLVED_LOCK = json.loads(json.dumps(release_config.load_lock()))
RESOLVED_LOCK["tools"]["msvc"] = {"targets": ["windows-x86_64"], "identity": {
    "cl_version": "19.44.35211", "linker_version": "14.44.35211.0", "msvc_toolset_version": "14.44.35207"}}
RESOLVED_LOCK["tools"]["windows_sdk"] = {"targets": ["windows-x86_64"], "identity": {
    "windows_sdk_version": "10.0.26100.0", "ucrt_version": "10.0.26100.0"}}
OBSERVED = {"target": "windows-x86_64", "tools": {name: dict(entry["identity"]) for name, entry in RESOLVED_LOCK["tools"].items()
                                                  if "windows-x86_64" in entry["targets"]}}


class Workspace(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)

    def write_config(self, value, name="config.json"):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def binaries(self, tag="x", platform="windows"):
        directory = self.root / f"bin-{tag}"
        directory.mkdir()
        names = {
            "windows": ("veles-bridge-maintenance.exe", "veles-bridge-cleanup.exe", "veles-native-host.exe", "uninstall.exe"),
            "macos": ("veles-bridge-maintenance", "veles-native-host"),
        }[platform]
        for name in names:
            (directory / name).write_bytes(f"{platform}:{name}\n".encode() * 8)
        return directory


class ConfigChecks(Workspace):
    def problems(self, value, **kwargs):
        kwargs.setdefault("channel", "production")
        kwargs.setdefault("platform", "windows")
        return au.check_config(value, **kwargs)

    def test_accepts_the_matching_channel_and_platform(self):
        self.assertEqual([], self.problems(config()))
        self.assertEqual([], self.problems(config("macos"), platform="macos"))
        self.assertEqual([], self.problems(config(channel="fixture"), channel="fixture"))

    def test_a_fixture_config_can_never_make_a_production_artifact(self):
        self.assertTrue(self.problems(config(channel="fixture"), channel="production"))
        self.assertTrue(self.problems(config(channel="production"), channel="fixture"))

    def test_the_development_channel_is_never_packageable(self):
        for requested in ("production", "fixture"):
            self.assertTrue(self.problems(config(channel="development", host_name="me.nagaev.veles.bridge.dev", bundle_id="me.nagaev.veles.bridge.dev"), channel=requested))
        with self.assertRaises(ValueError):
            au.check_config(config(), channel="development", platform="windows")

    def test_identity_must_match_the_channel(self):
        self.assertTrue(self.problems(config(host_name="me.nagaev.veles.bridge.fixture")))
        self.assertTrue(self.problems(config(bundle_id="me.nagaev.veles.bridge.dev")))

    def test_the_channel_field_itself_is_compared_not_only_the_identity(self):
        production_identity = {"host_name": "me.nagaev.veles.bridge", "bundle_id": "me.nagaev.veles.bridge"}
        fixture_identity = {"host_name": "me.nagaev.veles.bridge.fixture", "bundle_id": "me.nagaev.veles.bridge.fixture"}
        # A fixture-labelled config wearing the production identity, and the reverse, are both refused.
        self.assertTrue(any("channel" in p for p in self.problems(config(channel="fixture", **production_identity), channel="production")))
        self.assertTrue(any("channel" in p for p in self.problems(config(channel="production", **fixture_identity), channel="fixture")))

    def test_target_and_minimum_os_must_match_the_platform(self):
        self.assertTrue(self.problems(config("macos"), platform="windows"))
        self.assertTrue(self.problems(config(minimum_os="9")))
        self.assertTrue(self.problems(config("macos", minimum_os="15.0"), platform="macos"))

    def test_extension_id_must_be_real_and_bare(self):
        for bad in ("unknown-alias", "short", EXTENSION_ID.upper(), "z" * 32, EXTENSION_ID + "a"):
            self.assertTrue(self.problems(config(extension_id=bad)), bad)

    def test_unknown_missing_and_computed_fields_are_refused(self):
        self.assertTrue(self.problems(config(extra=1)))
        missing = config()
        del missing["policy"]
        self.assertTrue(self.problems(missing))
        # The commit and payload id are computed by the build, never accepted from the input.
        self.assertTrue(self.problems(config(source_commit=COMMIT)))
        self.assertTrue(self.problems(config(payload_id="1" * 64)))

    def test_version_api_and_policy_shapes(self):
        for bad in ("1.2", "01.2.3", "1.2.3.4", "v1.2.3", "1.2.-3"):
            self.assertTrue(self.problems(config(version=bad)), bad)
        self.assertTrue(self.problems(config(api_min=2, api_max=1)))
        self.assertTrue(self.problems(config(api_min=0)))
        self.assertTrue(self.problems(config(policy={"publishers": [], "denied_payloads": [], "rollback_payloads": [], "epoch": 1})))
        self.assertTrue(self.problems(config(policy={"publishers": [" padded"], "denied_payloads": [], "rollback_payloads": [], "epoch": 1})))
        self.assertTrue(self.problems(config(policy={"publishers": ["a", "a"], "denied_payloads": [], "rollback_payloads": [], "epoch": 1})))

    def test_load_rejects_duplicate_keys_and_non_objects(self):
        path = self.root / "dup.json"
        path.write_text('{"schema":1,"schema":1}', encoding="utf-8")
        with self.assertRaises(ValueError):
            au.load_config(path)
        path.write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError):
            au.load_config(path)


class Documents(Workspace):
    def test_inventory_document_uses_the_engine_shape_and_byte_order(self):
        entries = [
            {"path": "b", "size": 1, "sha256": "2" * 64, "executable": False},
            {"path": "B", "size": 2, "sha256": "3" * 64, "executable": True},
        ]
        document = au.inventory_document(entries)
        self.assertEqual(release_gate.canonical_json({"entries": [entries[1], entries[0]], "schema": 1}), document)

    def test_carrier_documents_follow_the_policy_rs_layout(self):
        descriptor = b'{"a":1}\n'
        plain = au.carrier_document(descriptor, sealed_placeholder=False)
        sealed = au.carrier_document(descriptor, sealed_placeholder=True)
        self.assertEqual(b"VELESPC1", plain[:8])
        self.assertEqual(32 + len(descriptor), len(plain))
        self.assertEqual((1).to_bytes(2, "little"), plain[8:10])
        self.assertEqual((0).to_bytes(2, "little"), plain[10:12])
        self.assertEqual((1).to_bytes(2, "little"), sealed[10:12])
        self.assertEqual(plain[:10] + sealed[10:12] + plain[12:], sealed[: len(plain)])
        self.assertEqual(b"\x00" * 32, sealed[-32:])
        self.assertEqual(len(plain) + 32, len(sealed))
        # The gate can seal exactly what this writer produces (the Windows blocker Task 13 recorded).
        digest = hashlib.sha256(b"sealed").digest()
        self.assertEqual(digest, release_gate.seal_carrier(sealed, b"sealed", descriptor, embedded=False)[-32:])
        self.assertEqual(digest, release_gate.seal_carrier(b"MZ" + b"\x00" * 64 + sealed + b"\xff" * 16, b"sealed", descriptor, embedded=True)[66 + len(plain) : 66 + len(plain) + 32])

    def test_documents_are_byte_identical_to_the_rust_writers(self):
        # Golden vectors printed by the engine's own write_carrier / write_sealed_carrier / write_inventory
        # (a throwaway Rust test run on this host during Task 14; not committed).
        descriptor = b'{"a":1}\n'
        self.assertEqual(
            "56454c45535043310100000020000000200000000000000008000000000000007b2261223a317d0a",
            au.carrier_document(descriptor, sealed_placeholder=False).hex(),
        )
        self.assertEqual(
            "56454c45535043310100010020000000200000000000000008000000000000007b2261223a317d0a" + "00" * 32,
            au.carrier_document(descriptor, sealed_placeholder=True).hex(),
        )
        entries = [
            {"path": "veles-native-host.exe", "size": 5, "sha256": hashlib.sha256(b"hello").hexdigest(), "executable": False},
            {"path": "maintenance/uninstall.exe", "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest(), "executable": True},
        ]
        self.assertEqual(
            "7b22656e7472696573223a5b7b2265786563757461626c65223a747275652c2270617468223a226d61696e74656e616e63652f756e696e7374616c6c2e657865222c22736861323536223a2262613738313662663866303163666561343134313430646535646165323232336230303336316133393631373761396362343130666636316632303031356164222c2273697a65223a337d2c7b2265786563757461626c65223a66616c73652c2270617468223a2276656c65732d6e61746976652d686f73742e657865222c22736861323536223a2232636632346462613566623061333065323665383362326163356239653239653162313631653563316661373432356537333034333336323933386239383234222c2273697a65223a357d5d2c22736368656d61223a317d0a",
            au.inventory_document(entries).hex(),
        )

    def test_descriptor_is_canonical_and_binds_commit_and_payload(self):
        descriptor = au.descriptor_document(config(), COMMIT, "a" * 64)
        value = json.loads(descriptor)
        self.assertEqual(COMMIT, value["source_commit"])
        self.assertEqual("a" * 64, value["payload_id"])
        self.assertEqual(release_gate.canonical_json(value), descriptor)
        with self.assertRaises(ValueError):
            au.descriptor_document(config(), "abc", "a" * 64)
        with self.assertRaises(ValueError):
            au.descriptor_document(config(), COMMIT, "xyz")


class WindowsAssembly(Workspace):
    def assemble(self, tag="a", **overrides):
        cfg = self.write_config(config())
        bins = self.binaries(tag)
        out = self.root / f"out-{tag}"
        arguments = dict(
            platform="windows", channel="production", config=cfg, commit=COMMIT, binaries=bins, output=out,
            frontend=None, packager=None,
        )
        arguments.update(overrides)
        au.assemble(**arguments)
        return out

    def test_layout_descriptor_inventory_and_carrier_agree(self):
        out = self.assemble()
        package = out / "tree" / "package"
        self.assertEqual(
            ["carrier.exe", "descriptor.json", "inventory.json", "payload"],
            sorted(p.name for p in package.iterdir()),
        )
        self.assertEqual(["veles-bridge-cleanup.exe", "veles-native-host.exe"], sorted(p.name for p in (package / "payload").iterdir()))
        descriptor = (package / "descriptor.json").read_bytes()
        inventory = (package / "inventory.json").read_bytes()
        self.assertEqual(hashlib.sha256(inventory).hexdigest(), json.loads(descriptor)["payload_id"])
        listed = json.loads(inventory)["entries"]
        self.assertEqual(["maintenance/uninstall.exe", "veles-bridge-cleanup.exe", "veles-native-host.exe"], [e["path"] for e in listed])
        self.assertEqual((out / "tree" / "uninstall.exe").read_bytes(), (self.root / "bin-a" / "uninstall.exe").read_bytes())
        for entry in listed:
            where = out / "tree" / "uninstall.exe" if entry["path"] == "maintenance/uninstall.exe" else package / "payload" / entry["path"]
            data = where.read_bytes()
            self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (entry["size"], entry["sha256"]))
        self.assertEqual((self.root / "bin-a" / "veles-bridge-maintenance.exe").read_bytes(), (package / "carrier.exe").read_bytes())
        carrier = (out / "carrier.bin").read_bytes()
        self.assertIn(descriptor, carrier)
        self.assertEqual(b"\x00" * 32, carrier[-32:])
        self.assertEqual(1, int.from_bytes(carrier[10:12], "little"))

    def test_output_is_independent_of_the_input_location_and_ordering(self):
        first = self.assemble("a")
        second = self.assemble("b")
        self.assertEqual(
            package_inputs.build_inventory(first / "tree")["inventory_sha256"],
            package_inputs.build_inventory(second / "tree")["inventory_sha256"],
        )
        self.assertEqual((first / "carrier.bin").read_bytes(), (second / "carrier.bin").read_bytes())

    def test_a_different_commit_changes_the_descriptor_but_not_the_payload_id(self):
        first = self.assemble("a")
        second = self.assemble("b", commit=OTHER_COMMIT)
        a = json.loads((first / "tree/package/descriptor.json").read_bytes())
        b = json.loads((second / "tree/package/descriptor.json").read_bytes())
        self.assertNotEqual(a["source_commit"], b["source_commit"])
        self.assertEqual(a["payload_id"], b["payload_id"])

    def test_production_assembly_refuses_a_fixture_config_and_leaves_nothing(self):
        cfg = self.write_config(config(channel="fixture"))
        out = self.root / "out-refused"
        with self.assertRaises(au.AssemblyError):
            au.assemble(platform="windows", channel="production", config=cfg, commit=COMMIT, binaries=self.binaries("r"), output=out, frontend=None, packager=None)
        self.assertFalse(out.exists())

    def test_fixture_assembly_refuses_a_production_config_and_leaves_nothing(self):
        cfg = self.write_config(config(channel="production"))
        out = self.root / "out-refused"
        with self.assertRaises(au.AssemblyError):
            au.assemble(platform="windows", channel="fixture", config=cfg, commit=COMMIT, binaries=self.binaries("r"), output=out, frontend=None, packager=None)
        self.assertFalse(out.exists())

    def test_inputs_are_closed_and_regular(self):
        cfg = self.write_config(config())
        for mutate in ("missing", "empty", "symlink", "extra", "no-uninstaller"):
            bins = self.binaries(mutate)
            target = bins / "veles-native-host.exe"
            if mutate == "no-uninstaller":
                (bins / "uninstall.exe").unlink()
            elif mutate == "missing":
                target.unlink()
            elif mutate == "empty":
                target.write_bytes(b"")
            elif mutate == "symlink":
                target.unlink()
                target.symlink_to(bins / "veles-bridge-cleanup.exe")
            else:
                (bins / "veles-bridge-testdriver.exe").write_bytes(b"x")
            out = self.root / f"out-{mutate}"
            with self.assertRaises(au.AssemblyError, msg=mutate):
                au.assemble(platform="windows", channel="production", config=cfg, commit=COMMIT, binaries=bins, output=out, frontend=None, packager=None)
            self.assertFalse(out.exists(), mutate)

    def test_existing_output_and_bad_commit_are_refused(self):
        cfg = self.write_config(config())
        existing = self.root / "exists"
        existing.mkdir()
        with self.assertRaises(au.AssemblyError):
            au.assemble(platform="windows", channel="production", config=cfg, commit=COMMIT, binaries=self.binaries("e"), output=existing, frontend=None, packager=None)
        with self.assertRaises(au.AssemblyError):
            au.assemble(platform="windows", channel="production", config=cfg, commit="HEAD", binaries=self.binaries("f"), output=self.root / "o2", frontend=None, packager=None)


class MacosAssembly(Workspace):
    def test_two_pass_packaging_binds_the_real_payload(self):
        cfg = self.write_config(config("macos"))
        bins = self.binaries("m", "macos")
        frontend = self.root / "VelesBridgeInstaller"
        frontend.write_bytes(b"frontend\n" * 8)
        out = self.root / "out-mac"
        au.assemble(platform="macos", channel="production", config=cfg, commit=COMMIT, binaries=bins, output=out, frontend=frontend, packager=HERE / "package-macos.sh")
        app = out / "tree" / "Veles Bridge Installer.app"
        package = app / "Contents" / "Resources" / "package"
        descriptor = (package / "descriptor.json").read_bytes()
        inventory = (package / "inventory.json").read_bytes()
        self.assertEqual(hashlib.sha256(inventory).hexdigest(), json.loads(descriptor)["payload_id"])
        listed = {e["path"] for e in json.loads(inventory)["entries"]}
        self.assertTrue(any(path.endswith("Contents/MacOS/veles-native-host") for path in listed), listed)
        # The inventory names exactly the payload directory's files.
        actual = {e["path"] for e in package_inputs.build_inventory(package / "payload")["entries"]}
        self.assertEqual(actual, listed)
        carrier = (package / "carrier.app" / "Contents" / "Resources" / "veles-policy-carrier").read_bytes()
        self.assertEqual(b"VELESPC1", carrier[:8])
        self.assertIn(descriptor, carrier)
        self.assertEqual(0, int.from_bytes(carrier[10:12], "little"))
        self.assertFalse((out / "tree" / "Applications").exists())


    def test_the_host_bundle_identity_follows_the_channel(self):
        # F2: the fixture tree must not carry the production host bundle ID.
        for channel, expected in (("production", "me.nagaev.veles.bridge"), ("fixture", "me.nagaev.veles.bridge.fixture")):
            with self.subTest(channel=channel):
                cfg = self.write_config(config("macos", channel=channel))
                frontend = self.root / f"VelesBridgeInstaller-{channel}"
                frontend.write_bytes(b"frontend\n" * 8)
                out = self.root / f"out-mac-{channel}"
                au.assemble(platform="macos", channel=channel, config=cfg, commit=COMMIT, binaries=self.binaries(f"m-{channel}", "macos"),
                            output=out, frontend=frontend, packager=HERE / "package-macos.sh")
                host_plist = out / "tree" / "Veles Bridge Installer.app" / "Contents" / "Resources" / "package" / "payload" / "Veles Native Bridge.app" / "Contents" / "Info.plist"
                self.assertEqual(plistlib.loads(host_plist.read_bytes())["CFBundleIdentifier"], expected)


class PackagerDrift(Workspace):
    def test_a_payload_that_depends_on_the_descriptor_is_refused(self):
        # A packager whose payload embeds the descriptor would make payload_id unreproducible: pass 2 differs from pass 1.
        packager = self.root / "packager.sh"
        packager.write_text(
            "#!/usr/bin/env bash\nset -eu\n"
            "while [ $# -gt 0 ]; do case $1 in --inputs) in=$2; shift 2;; --output) out=$2; shift 2;; esac; done\n"
            "dir=\"$out/dmg-root/Veles Bridge Installer.app/Contents/Resources/package/payload\"\n"
            "mkdir -p \"$dir\"; cp \"$in/descriptor.json\" \"$dir/leaked-descriptor\"\n",
            encoding="utf-8",
        )
        frontend = self.root / "VelesBridgeInstaller"
        frontend.write_bytes(b"frontend\n" * 8)
        out = self.root / "out-drift"
        with self.assertRaises(au.AssemblyError) as caught:
            au.assemble(platform="macos", channel="production", config=self.write_config(config("macos")), commit=COMMIT, binaries=self.binaries("d", "macos"), output=out, frontend=frontend, packager=packager)
        self.assertIn("changed between", str(caught.exception))
        self.assertFalse(out.exists())


class Finalize(Workspace):
    def trees(self, differ=False, uninstaller=True):
        paths = []
        for tag in ("a", "b"):
            cfg = self.write_config(config())
            out = self.root / f"slot-{tag}"
            au.assemble(platform="windows", channel="production", config=cfg, commit=COMMIT, binaries=self.binaries(tag), output=out, frontend=None, packager=None)
            if not uninstaller:
                (out / "tree" / "uninstall.exe").unlink()
            paths.append(out)
        if differ:
            (paths[1] / "tree" / "uninstall.exe").write_bytes(b"different\n")
        return paths

    def test_equal_independent_slots_produce_the_gate_artifacts(self):
        a, b = self.trees()
        out = self.root / "published"
        au.finalize(platform="windows", tool_observation=OBSERVED, slot_a=a, slot_b=b, output=out, commit=COMMIT)
        self.assertEqual(["SHA256SUMS", "provenance.json", "report-ab.json", "tree.tar"], sorted(p.name for p in out.iterdir()))
        provenance = json.loads((out / "provenance.json").read_bytes())
        self.assertEqual({"schema", "source_commit", "descriptor_digest", "a_inventory_sha256", "b_inventory_sha256", "tool_observation"}, set(provenance))
        self.assertEqual(OBSERVED, provenance["tool_observation"])
        self.assertEqual(COMMIT, provenance["source_commit"])
        extracted = self.root / "extracted"
        extracted.mkdir()
        with tarfile.open(out / "tree.tar") as archive:
            archive.extractall(extracted, filter="tar")
        inventory = package_inputs.build_inventory(extracted)["inventory_sha256"]
        self.assertEqual(inventory, provenance["a_inventory_sha256"])
        self.assertEqual(inventory, provenance["b_inventory_sha256"])
        self.assertEqual(hashlib.sha256((extracted / "package" / "descriptor.json").read_bytes()).hexdigest(), provenance["descriptor_digest"])
        sums = (out / "SHA256SUMS").read_text()
        for name in ("tree.tar", "provenance.json"):
            self.assertIn(f"{hashlib.sha256((out / name).read_bytes()).hexdigest()}  {name}", sums)
        self.assertEqual("PASS", json.loads((out / "report-ab.json").read_bytes())["verdict"])

    def test_the_observed_tool_inventory_is_bound_into_the_provenance(self):
        a, b = self.trees()
        out = self.root / "published"
        au.finalize(platform="windows", tool_observation=OBSERVED, slot_a=a, slot_b=b, output=out, commit=COMMIT)
        provenance = json.loads((out / "provenance.json").read_bytes())
        self.assertEqual(OBSERVED, provenance["tool_observation"])

    def test_a_missing_or_foreign_tool_observation_publishes_nothing(self):
        a, b = self.trees()
        for label, observation in (("none", None), ("list", []), ("empty", {}), ("other target", {**OBSERVED, "target": "macos-arm64"}),
                                   ("no tools", {"target": "windows-x86_64"}), ("tools not an object", {"target": "windows-x86_64", "tools": []})):
            with self.subTest(label):
                out = self.root / f"published-{abs(hash(label))}"
                with self.assertRaises(au.AssemblyError):
                    au.finalize(platform="windows", tool_observation=observation, slot_a=a, slot_b=b, output=out, commit=COMMIT)
                self.assertFalse(out.exists())

    def test_the_tar_is_deterministic_and_keeps_modes(self):
        a, b = self.trees()
        (a / "tree" / "package" / "payload" / "veles-native-host.exe").chmod(0o755)
        (b / "tree" / "package" / "payload" / "veles-native-host.exe").chmod(0o755)
        out1, out2 = self.root / "p1", self.root / "p2"
        au.finalize(platform="windows", tool_observation=OBSERVED, slot_a=a, slot_b=b, output=out1, commit=COMMIT)
        os.utime(a / "tree" / "uninstall.exe", (12345, 12345))
        au.finalize(platform="windows", tool_observation=OBSERVED, slot_a=a, slot_b=b, output=out2, commit=COMMIT)
        self.assertEqual((out1 / "tree.tar").read_bytes(), (out2 / "tree.tar").read_bytes())
        with tarfile.open(out1 / "tree.tar") as archive:
            members = {m.name.lstrip("./"): m for m in archive.getmembers()}
        host = members["package/payload/veles-native-host.exe"]
        self.assertTrue(host.mode & stat.S_IXUSR)
        self.assertTrue(all(m.mtime == 0 and m.uid == 0 and m.gid == 0 and m.uname == "" for m in members.values()))

    def test_unequal_slots_are_a_blocker_and_publish_nothing(self):
        a, b = self.trees(differ=True)
        out = self.root / "published"
        with self.assertRaises(au.AssemblyError) as caught:
            au.finalize(platform="windows", tool_observation=OBSERVED, slot_a=a, slot_b=b, output=out, commit=COMMIT)
        self.assertIn("differ", str(caught.exception))
        self.assertFalse((out / "tree.tar").exists())
        self.assertFalse((out / "provenance.json").exists())
        report = json.loads((out / "report-ab.json").read_bytes())
        self.assertEqual("FAIL", report["verdict"])

    def test_the_same_directory_is_not_two_builds(self):
        a, _ = self.trees()
        with self.assertRaises(au.AssemblyError):
            au.finalize(platform="windows", tool_observation=OBSERVED, slot_a=a, slot_b=a, output=self.root / "published", commit=COMMIT)
        link = self.root / "alias"
        link.symlink_to(a)
        with self.assertRaises(au.AssemblyError):
            au.finalize(platform="windows", tool_observation=OBSERVED, slot_a=a, slot_b=link, output=self.root / "published2", commit=COMMIT)

    def test_slots_built_for_different_commits_are_refused(self):
        a, b = self.trees()
        shutil.rmtree(b)
        au.assemble(platform="windows", channel="production", config=self.write_config(config(), "c2.json"), commit=OTHER_COMMIT, binaries=self.binaries("zz"), output=b, frontend=None, packager=None)
        with self.assertRaises(au.AssemblyError):
            au.finalize(platform="windows", tool_observation=OBSERVED, slot_a=a, slot_b=b, output=self.root / "published", commit=COMMIT)

    def test_slots_that_agree_with_each_other_but_not_with_the_requested_commit_are_refused(self):
        a, b = self.trees()
        with self.assertRaises(au.AssemblyError) as caught:
            au.finalize(platform="windows", tool_observation=OBSERVED, slot_a=a, slot_b=b, output=self.root / "published", commit=OTHER_COMMIT)
        self.assertIn("was built for commit", str(caught.exception))

    def test_the_windows_uninstaller_is_required(self):
        a, b = self.trees(uninstaller=False)
        with self.assertRaises(au.AssemblyError):
            au.finalize(platform="windows", tool_observation=OBSERVED, slot_a=a, slot_b=b, output=self.root / "published", commit=COMMIT)

    def test_existing_output_is_refused(self):
        a, b = self.trees()
        existing = self.root / "published"
        existing.mkdir()
        with self.assertRaises(au.AssemblyError):
            au.finalize(platform="windows", tool_observation=OBSERVED, slot_a=a, slot_b=b, output=existing, commit=COMMIT)

    def test_the_gate_accepts_what_finalize_publishes(self):
        a, b = self.trees()
        out = self.root / "published"
        au.finalize(platform="windows", tool_observation=OBSERVED, slot_a=a, slot_b=b, output=out, commit=COMMIT)
        trees = {}
        for tag in ("a", "b"):
            trees[tag] = self.root / f"gate-{tag}"
            trees[tag].mkdir()
            with tarfile.open(out / "tree.tar") as archive:
                archive.extractall(trees[tag], filter="tar")
        descriptor = trees["a"] / "package" / "descriptor.json"
        digest = hashlib.sha256(descriptor.read_bytes()).hexdigest()
        env = {
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/tags/1.2.3", "GITHUB_SHA": COMMIT,
            "VELES_DESCRIPTOR_SHA256": digest,
        }
        with mock.patch.object(release_gate, "tools_lock", lambda: RESOLVED_LOCK):
            outcome = release_gate.verify_unsigned_inputs(
                platform="windows", a=trees["a"], b=trees["b"], provenance=out / "provenance.json", descriptor_path=descriptor,
                env=env, expected_commit=COMMIT, recipe="r" * 64, output=self.root / "chosen", reports=self.root / "reports",
            )
        # Task 13's first job accepts exactly what the producer publishes: A/B equality, provenance,
        # descriptor binding and the structural stage check (payload view == inventory.json).
        self.assertTrue(outcome.ok, f"{outcome.code}: {outcome.detail}")


class CommandLine(Workspace):
    def run_cli(self, *arguments):
        return subprocess.run([sys.executable, "-I", str(HERE / "assemble_unsigned.py"), *arguments], capture_output=True, text=True)

    def test_check_config_exit_codes(self):
        production = self.write_config(config(), "p.json")
        fixture = self.write_config(config(channel="fixture"), "f.json")
        ok = self.run_cli("check-config", "--config", str(production), "--channel", "production", "--platform", "windows")
        self.assertEqual(0, ok.returncode, ok.stderr)
        wrong = self.run_cli("check-config", "--config", str(fixture), "--channel", "production", "--platform", "windows")
        self.assertEqual(1, wrong.returncode)
        self.assertEqual(2, self.run_cli("check-config", "--config", str(production), "--channel", "development", "--platform", "windows").returncode)
        self.assertEqual(2, self.run_cli("check-config", "--config", str(self.root / "missing.json"), "--channel", "production", "--platform", "windows").returncode)

    def test_require_tracked_refuses_untracked_and_modified_configs(self):
        repository = self.root / "repo"
        repository.mkdir()
        git = lambda *a: subprocess.run(["git", "-C", str(repository), "-c", "user.name=t", "-c", "user.email=t@t", *a], capture_output=True, text=True, check=True)  # noqa: E731
        git("init", "-q")
        path = repository / "release.json"
        path.write_text(json.dumps(config()), encoding="utf-8")
        arguments = ("check-config", "--config", str(path), "--channel", "production", "--platform", "windows", "--require-tracked")
        self.assertEqual(1, self.run_cli(*arguments).returncode, "an untracked config is not a tracked build input")
        git("add", "release.json")
        git("commit", "-q", "-m", "x")
        self.assertEqual(0, self.run_cli(*arguments).returncode)
        path.write_text(json.dumps(config(version="9.9.9")), encoding="utf-8")
        result = self.run_cli(*arguments)
        self.assertEqual(1, result.returncode)
        self.assertIn("differs from HEAD", result.stderr)

    def test_finalize_requires_the_tool_observation_file_and_binds_it(self):
        a, b = Finalize.trees(self)
        out = self.root / "published-cli"
        base = ["finalize", "--platform", "windows", "--slot-a", str(a), "--slot-b", str(b), "--output", str(out), "--commit", COMMIT]
        missing = self.run_cli(*base)
        self.assertEqual(missing.returncode, 2, missing.stderr)
        self.assertFalse(out.exists())
        observed = self.root / "observed.json"
        observed.write_text(json.dumps(OBSERVED))
        done = self.run_cli(*base, "--tool-observation", str(observed))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(OBSERVED, json.loads((out / "provenance.json").read_bytes())["tool_observation"])
        junk = self.root / "junk.json"
        junk.write_text("{")
        again = self.run_cli(*(base[:-1] + [str(self.root / "published-junk")]), "--tool-observation", str(junk))
        self.assertEqual(again.returncode, 2, again.stderr)

    def test_origin_command_prints_exactly_the_one_allowed_origin(self):
        production = self.write_config(config(), "p.json")
        result = self.run_cli("origin", "--config", str(production))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(f"chrome-extension://{EXTENSION_ID}/\n", result.stdout)


if __name__ == "__main__":
    unittest.main()

