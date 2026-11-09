import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from release_assets import (  # noqa: E402
    AssetError,
    expected_assets,
    main,
    stage,
    verify_carry,
    verify_draft,
)

SEVEN = {
    "veles-android-0.2.0.apk",
    "veles-android-0.2.0-mapping.txt",
    "veles-web-extension-0.1.0.zip",
    "veles-installer-windows-0.1.0.exe",
    "veles-installer-macos-0.1.0.dmg",
    "release-manifest.json",
    "SHA256SUMS",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


class AssetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="veles-assets-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def write(self, directory, name, data):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(data)
        return sha(data)

    def carried_installer(self):
        """A previous manifest whose installer 0.1.0 assets sit in carried/."""
        carried = self.tmp / "carried"
        assets = {
            "veles-installer-windows-0.1.0.exe": self.write(carried, "veles-installer-windows-0.1.0.exe", b"exe"),
            "veles-installer-macos-0.1.0.dmg": self.write(carried, "veles-installer-macos-0.1.0.dmg", b"dmg"),
        }
        previous = {
            "schema": 1, "release": "2026.11.09", "commit": "a" * 40,
            "components": {"installer": {"version": "0.1.0", "assets": assets}},
        }
        return previous, carried

    def test_expected_assets(self):
        self.assertEqual(
            expected_assets("installer", "0.2.0"),
            ["veles-installer-windows-0.2.0.exe", "veles-installer-macos-0.2.0.dmg"],
        )
        self.assertEqual(
            expected_assets("android", "0.2.0"),
            ["veles-android-0.2.0.apk", "veles-android-0.2.0-mapping.txt"],
        )
        self.assertEqual(expected_assets("web-extension", "0.2.0"), ["veles-web-extension-0.2.0.zip"])
        with self.assertRaises(AssetError):
            expected_assets("watch", "0.1.0")

    def test_verify_carry_accepts_exact_files(self):
        previous, carried = self.carried_installer()
        verify_carry(previous, "installer", carried)

    def test_verify_carry_rejects_missing_macos_dmg(self):
        previous, carried = self.carried_installer()
        (carried / "veles-installer-macos-0.1.0.dmg").unlink()
        with self.assertRaisesRegex(AssetError, "missing"):
            verify_carry(previous, "installer", carried)

    def test_verify_carry_rejects_missing_mapping(self):
        carried = self.tmp / "android"
        assets = {
            "veles-android-0.1.0.apk": self.write(carried, "veles-android-0.1.0.apk", b"apk"),
            "veles-android-0.1.0-mapping.txt": sha(b"map"),
        }
        previous = {"components": {"android": {"version": "0.1.0", "assets": assets}}}
        with self.assertRaisesRegex(AssetError, "missing"):
            verify_carry(previous, "android", carried)

    def test_verify_carry_rejects_extra_file(self):
        previous, carried = self.carried_installer()
        self.write(carried, "veles-native-host.exe", b"host")
        with self.assertRaisesRegex(AssetError, "unexpected"):
            verify_carry(previous, "installer", carried)

    def test_verify_carry_rejects_digest_mismatch(self):
        previous, carried = self.carried_installer()
        (carried / "veles-installer-windows-0.1.0.exe").write_bytes(b"tampered")
        with self.assertRaisesRegex(AssetError, "digest"):
            verify_carry(previous, "installer", carried)

    def test_verify_carry_rejects_manifest_names_not_matching_expected(self):
        carried = self.tmp / "renamed"
        assets = {
            "veles-installer-windows-0.1.0.exe": self.write(carried, "veles-installer-windows-0.1.0.exe", b"exe"),
            "veles-installer-mac-0.1.0.dmg": self.write(carried, "veles-installer-mac-0.1.0.dmg", b"dmg"),
        }
        previous = {"components": {"installer": {"version": "0.1.0", "assets": assets}}}
        with self.assertRaisesRegex(AssetError, "expected"):
            verify_carry(previous, "installer", carried)

    def test_verify_carry_rejects_component_absent_from_manifest(self):
        previous, carried = self.carried_installer()
        with self.assertRaises(AssetError):
            verify_carry(previous, "android", carried)

    def test_verify_carry_cross_checks_previous_sha256sums(self):
        previous, carried = self.carried_installer()
        sums = self.tmp / "SHA256SUMS"
        sums.write_text(
            "%s  veles-installer-windows-0.1.0.exe\n%s  veles-installer-macos-0.1.0.dmg\n" % (sha(b"exe"), sha(b"other")),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(AssetError, "SHA256SUMS"):
            verify_carry(previous, "installer", carried, sums=sums)

    def plan(self):
        return {
            "release_tag": "2026.11.20",
            "previous_release": "2026.11.09",
            "commit": "b" * 40,
            "any_release": True,
            "components": {
                "android": {"current": "0.1.0", "next": "0.2.0", "action": "RELEASE", "tag": "android-v0.2.0"},
                "web-extension": {"current": None, "next": "0.1.0", "action": "RELEASE", "tag": "web-extension-v0.1.0"},
                "installer": {"current": "0.1.0", "next": "0.1.0", "action": "CARRY", "tag": "installer-v0.1.0"},
            },
        }

    def staged(self):
        previous, carried = self.carried_installer()
        android = self.tmp / "in-android"
        self.write(android, "app-release.apk", b"signed apk")
        self.write(android, "mapping.txt", b"mapping")
        self.write(android, "SHA256SUMS", b"ignored\n")
        web = self.tmp / "in-web"
        self.write(web, "veles-extension-0.1.0.zip", b"zip")
        out = self.tmp / "out"
        digests = stage(self.plan(), {"android": android, "web-extension": web}, carried, out, previous_manifest=previous)
        return digests, out

    def test_stage_produces_exactly_the_seven_assets(self):
        digests, out = self.staged()
        self.assertEqual({p.name for p in out.iterdir()}, SEVEN)
        self.assertEqual(set(digests), SEVEN)
        for name, digest in digests.items():
            self.assertEqual(sha((out / name).read_bytes()), digest)
        self.assertEqual((out / "veles-android-0.2.0.apk").read_bytes(), b"signed apk")
        self.assertEqual((out / "veles-installer-macos-0.1.0.dmg").read_bytes(), b"dmg")

    def test_stage_sha256sums_matches_files(self):
        _, out = self.staged()
        lines = (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        listed = {}
        for line in lines:
            digest, name = line.split("  ", 1)
            listed[name] = digest
        self.assertEqual(set(listed), SEVEN - {"SHA256SUMS"})
        for name, digest in listed.items():
            self.assertEqual(sha((out / name).read_bytes()), digest)

    def test_stage_writes_release_manifest(self):
        _, out = self.staged()
        data = json.loads((out / "release-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["release"], "2026.11.20")
        self.assertEqual(data["commit"], "b" * 40)
        self.assertEqual(data["components"]["android"]["version"], "0.2.0")
        self.assertEqual(
            data["components"]["android"]["assets"]["veles-android-0.2.0.apk"], sha(b"signed apk")
        )
        self.assertEqual(data["components"]["installer"]["version"], "0.1.0")
        self.assertEqual(
            data["components"]["installer"]["assets"],
            {"veles-installer-windows-0.1.0.exe": sha(b"exe"), "veles-installer-macos-0.1.0.dmg": sha(b"dmg")},
        )

    def test_stage_rejects_ambiguous_or_missing_inputs(self):
        previous, carried = self.carried_installer()
        android = self.tmp / "in-android"
        self.write(android, "a.apk", b"1")
        self.write(android, "b.apk", b"2")
        self.write(android, "mapping.txt", b"m")
        web = self.tmp / "in-web"
        self.write(web, "x.zip", b"z")
        with self.assertRaises(AssetError):
            stage(self.plan(), {"android": android, "web-extension": web}, carried, self.tmp / "o1", previous_manifest=previous)
        with self.assertRaises(AssetError):
            stage(self.plan(), {"web-extension": web}, carried, self.tmp / "o2", previous_manifest=previous)

    def test_stage_rejects_tampered_carried_asset(self):
        previous, carried = self.carried_installer()
        (carried / "veles-installer-macos-0.1.0.dmg").write_bytes(b"evil")
        android = self.tmp / "in-android"
        self.write(android, "app.apk", b"1")
        self.write(android, "mapping.txt", b"m")
        web = self.tmp / "in-web"
        self.write(web, "x.zip", b"z")
        with self.assertRaises(AssetError):
            stage(self.plan(), {"android": android, "web-extension": web}, carried, self.tmp / "o", previous_manifest=previous)

    def test_verify_draft_detects_one_changed_byte(self):
        digests, out = self.staged()
        verify_draft(digests, out)
        apk = out / "veles-android-0.2.0.apk"
        data = bytearray(apk.read_bytes())
        data[0] ^= 1
        apk.write_bytes(bytes(data))
        with self.assertRaises(AssetError):
            verify_draft(digests, out)

    def test_verify_draft_detects_missing_and_extra(self):
        digests, out = self.staged()
        (out / "veles-native-host.exe").write_bytes(b"x")
        with self.assertRaises(AssetError):
            verify_draft(digests, out)
        (out / "veles-native-host.exe").unlink()
        (out / "SHA256SUMS").unlink()
        with self.assertRaises(AssetError):
            verify_draft(digests, out)

    def test_cli_round_trip(self):
        previous, carried = self.carried_installer()
        (self.tmp / "prev.json").write_text(json.dumps(previous), encoding="utf-8")
        self.assertEqual(main(["verify-carry", "--manifest-json", str(self.tmp / "prev.json"),
                               "--component", "installer", "--dir", str(carried)]), 0)
        (carried / "extra.bin").write_bytes(b"x")
        self.assertEqual(main(["verify-carry", "--manifest-json", str(self.tmp / "prev.json"),
                               "--component", "installer", "--dir", str(carried)]), 1)
        (carried / "extra.bin").unlink()
        (self.tmp / "plan.json").write_text(json.dumps(self.plan()), encoding="utf-8")
        android = self.tmp / "in-android"
        self.write(android, "app.apk", b"1")
        self.write(android, "mapping.txt", b"m")
        web = self.tmp / "in-web"
        self.write(web, "x.zip", b"z")
        out = self.tmp / "out"
        digests = self.tmp / "digests.json"
        self.assertEqual(main(["stage", "--plan-json", str(self.tmp / "plan.json"),
                               "--input", "android=%s" % android, "--input", "web-extension=%s" % web,
                               "--carried", str(carried), "--manifest-json", str(self.tmp / "prev.json"),
                               "--out", str(out), "--digests-out", str(digests)]), 0)
        self.assertEqual(set(json.loads(digests.read_text(encoding="utf-8"))), SEVEN)
        self.assertEqual(main(["verify-draft", "--expected-json", str(digests), "--dir", str(out)]), 0)
        (out / "SHA256SUMS").write_text("x", encoding="utf-8")
        self.assertEqual(main(["verify-draft", "--expected-json", str(digests), "--dir", str(out)]), 1)


if __name__ == "__main__":
    unittest.main()
