#!/usr/bin/env python3
"""Orchestration-contract tests for the protected signing / release gate (OTP-25 Task 13).

These tests assert the CONTRACT around signing, never a physical signature:

* which commands the orchestrator would run (through a fake runner that records them), in which
  order, and what it does when one exits non-zero;
* that nothing credential-bearing is touched before authorization, input provenance and credential
  presence all pass, and that no unsigned fallback output survives any failure;
* that cleanup runs on every failure point;
* that ``release_gate.validate`` accepts only a complete, mutually consistent evidence bundle and
  names every defect otherwise. The accepted bundles are produced end to end by the gate's own
  stages (verify-inputs -> sign-nested -> assemble -> sign-outer -> bundle) over the fake toolchain,
  so a green bundle proves the stages agree with the validator, not that anything is signed.

The fake runner "signs" by appending bytes and "reports" by writing the JSON the real tools would
write. NOTHING here proves that ``signtool``, ``codesign``, ``notarytool``, ``stapler``, ``spctl``,
``certutil``, ``7z``, ``hdiutil`` or ``makensis`` accept those command lines or print that text, and
nothing proves a signature, chain, timestamp, notarization ticket or Gatekeeper verdict: no such tool
exists on this host. Those steps are BLOCKED / NOT RUN and are listed in the task report. The sealed
carrier tests are different in kind: they check bytes against the carrier layout of ``policy.rs``.

Python 3.12+ standard library only. Run from the repository root:

    python3 -m unittest discover -s native-bridge/installer/scripts -p test_release_gate.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))

import release_config  # noqa: E402
import release_gate as gate  # noqa: E402
from release_gate import EnvRef, RunResult  # noqa: E402

COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER_COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
# The Windows leaf certificate is identified exactly as the engine's trust layer identifies it:
# sha256:<SHA-256 of the leaf certificate DER>. The display name is NOT an authorization identifier.
FAKE_DER = b"0\x82fixture leaf certificate DER"
THUMB = hashlib.sha1(FAKE_DER).hexdigest().upper()
WIN_PUBLISHER = "sha256:" + hashlib.sha256(FAKE_DER).hexdigest()
WIN_DISPLAY_NAME = "Veles Test Publisher"
TEAM = "TEAMID1234"
MAC_IDENTITY = f"Developer ID Application: Veles Test ({TEAM})"
SECRET_VALUES = ("hunter2-pfx-password", "p12-secret-password", "notary-key-secret-bytes")
WORKFLOW = REPO / ".github" / "workflows" / "release-native-installers.yml"
TARGET = {"windows": "windows-x86_64", "macos": "macos-arm64"}
PACKAGE = {"windows": gate.WIN_PACKAGE, "macos": gate.MAC_PACKAGE}
APP = gate.APP
HOST_APP = gate.HOST_APP
CARRIER_APP = gate.CARRIER_APP


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- build-tool identity (F8) ----------------------------------------------------------------------
# The repository lock keeps the Windows MSVC/SDK identities `required_unresolved` on purpose, so a Windows
# candidate can never be eligible from it. The orchestration tests below need a Windows pipeline that GETS
# to the signing stages, so they run against a synthetic lock that resolves those two tools. The tests in
# ToolIdentityBindingTests run against the REAL lock and prove the hard blocker.
REAL_LOCK = release_config.load_lock()
RESOLVED_LOCK = json.loads(json.dumps(REAL_LOCK))
RESOLVED_LOCK["tools"]["msvc"] = {
    "targets": ["windows-x86_64"],
    "identity": {"cl_version": "19.44.35211", "linker_version": "14.44.35211.0", "msvc_toolset_version": "14.44.35207",
                 "msvc_toolset_sha256": "a" * 64},
}
RESOLVED_LOCK["tools"]["windows_sdk"] = {
    "targets": ["windows-x86_64"],
    "identity": {"windows_sdk_version": "10.0.26100.0", "ucrt_version": "10.0.26100.0", "windows_sdk_sha256": "b" * 64},
}
assert release_config.validate_lock(RESOLVED_LOCK) == [], release_config.validate_lock(RESOLVED_LOCK)


def tool_observation(platform: str, lock: dict | None = None) -> dict:
    """An inventory that matches ``lock`` exactly (the synthetic one by default)."""
    lock = lock or RESOLVED_LOCK
    target = "windows-x86_64" if platform == "windows" else "macos-arm64"
    return {"target": target, "tools": {name: dict(entry["identity"]) for name, entry in lock["tools"].items()
                                         if target in entry["targets"] and "identity" in entry}}


def setUpModule() -> None:
    patcher = mock.patch.object(gate, "tools_lock", lambda: RESOLVED_LOCK, create=True)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


def descriptor_bytes(platform: str, *, commit: str = COMMIT, target: str | None = None, publishers=None) -> bytes:
    if publishers is None:
        publishers = [WIN_PUBLISHER] if platform == "windows" else [f"team:{TEAM}"]
    document = {
        "schema": 1,
        "source_commit": commit,
        "target": target or TARGET[platform],
        "version": "1.2.3",
        "policy": {"publishers": publishers, "denied_payloads": [], "rollback_payloads": [], "epoch": 1},
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def carrier(descriptor: bytes, *, sealed: bool) -> bytes:
    """A carrier document with the layout of ``policy.rs`` (magic, version, flags, header_len, offset, length)."""
    header = b"VELESPC1" + struct.pack("<HHIQQ", 1, 1 if sealed else 0, 32, 32, len(descriptor))
    return header + descriptor + (bytes(32) if sealed else b"")


def good_env(platform: str, descriptor: bytes | None = None) -> dict[str, str]:
    digest = sha(descriptor if descriptor is not None else descriptor_bytes(platform))
    env = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/tags/1.2.3",
        "GITHUB_SHA": COMMIT,
        "VELES_SIGNING_ENVIRONMENT": "native-release",
        "VELES_DESCRIPTOR_SHA256": digest,
        "VELES_APPROVED_DESCRIPTOR_SHA256": digest,
    }
    if platform == "windows":
        env.update(
            VELES_WIN_PFX_BASE64=base64.b64encode(b"fake-pfx-bytes").decode(),
            VELES_WIN_PFX_PASSWORD=SECRET_VALUES[0],
            VELES_WIN_CERT_SHA1=THUMB,
            VELES_WIN_PUBLISHER=WIN_PUBLISHER,
            VELES_WIN_TIMESTAMP_URL="https://timestamp.example.invalid/rfc3161",
        )
    else:
        env.update(
            VELES_MAC_P12_BASE64=base64.b64encode(b"fake-p12-bytes").decode(),
            VELES_MAC_P12_PASSWORD=SECRET_VALUES[1],
            VELES_MAC_IDENTITY=MAC_IDENTITY,
            VELES_MAC_TEAM_ID=TEAM,
            VELES_NOTARY_KEY_BASE64=base64.b64encode(SECRET_VALUES[2].encode()).decode(),
            VELES_NOTARY_KEY_ID="KEYID12345",
            VELES_NOTARY_ISSUER="11111111-2222-3333-4444-555555555555",
        )
    return env


def ci_env(platform: str) -> dict[str, str]:
    """The environment of a credential-free job: trusted context and descriptor digest only."""
    env = good_env(platform)
    return {k: v for k, v in env.items() if k.startswith("GITHUB_") or k == "VELES_DESCRIPTOR_SHA256"}


# ---------------------------------------------------------------------------
# Fake toolchain
# ---------------------------------------------------------------------------


class FakeRunner:
    """Records every command (secrets stay as ``$NAME``) and plays the native tools' role.

    ``fail_at`` is a 0-based index into the call sequence (or a predicate on the flattened argv
    string) whose command exits 1 without side effects.
    """

    def __init__(self, env, *, fail_at=None, corr=None, notary_status="Accepted", staple_ok=True, app_source=None, spctl_text=None,
                 leaf_der=FAKE_DER, signed_leaf_sha1=THUMB):
        self.env = dict(env)
        self.leaf_der = leaf_der
        self.signed_leaf_sha1 = signed_leaf_sha1
        self.fail_at = fail_at
        self.corr = corr or {}
        self.notary_status = notary_status
        self.staple_ok = staple_ok
        self.app_source = app_source
        self.spctl_text = spctl_text or "accepted\nsource=Notarized Developer ID\n"
        self.calls: list[tuple] = []
        self.views: dict[str, dict] = {}

    def identify(self, name: str) -> dict:
        return {"path": f"/fake/bin/{name}", "sha256": sha(name.encode())}

    def flat(self, argv) -> list[str]:
        return [self.env[item.name] if isinstance(item, EnvRef) else str(item) for item in argv]

    def __call__(self, argv, *, capture=False) -> RunResult:
        index = len(self.calls)
        self.calls.append(tuple(f"${a.name}" if isinstance(a, EnvRef) else str(a) for a in argv))
        flat = self.flat(argv)
        if self.fail_at is not None:
            hit = self.fail_at(" ".join(flat)) if callable(self.fail_at) else index == self.fail_at
            if hit:
                return RunResult(1, "")
        return self.play(flat)

    def play(self, flat: list[str]) -> RunResult:
        if any(item.endswith("correspondence.py") for item in flat):
            return self.play_correspondence(flat[1:])
        name = Path(flat[0]).stem.lower()
        handler = getattr(self, f"tool_{name.replace('-', '_')}", None)
        if handler is None:
            raise AssertionError(f"unexpected tool in command: {flat!r}")
        return handler(flat[1:])

    def play_correspondence(self, args):
        def opt(flag):
            return args[args.index(flag) + 1]

        unsigned, signed, kind, report = Path(opt("--unsigned")), Path(opt("--signed")), opt("--kind"), Path(opt("--report"))
        if kind == "tree":
            # What the comparator would be shown, captured before the scratch view is removed.
            self.views[signed.name] = {
                "unsigned": sorted(p.relative_to(unsigned).as_posix() for p in unsigned.rglob("*") if p.is_file()),
                "signed": sorted(p.relative_to(signed).as_posix() for p in signed.rglob("*") if p.is_file()),
            }
        verdict, codes = self.corr.get(unsigned.name, ("PASS", []))
        if kind in ("pe", "macho"):
            hashes = {"unsigned_sha256": sha(unsigned.read_bytes()), "signed_sha256": sha(signed.read_bytes())}
        else:
            hashes = {"unsigned_inventory_sha256": gate.inventory_digest(unsigned), "signed_inventory_sha256": gate.inventory_digest(signed)}
        document = {"schema": 1, "verdict": verdict, "kind": kind, "differences": [{"code": c} for c in codes], "allowed_differences": [], "input_hashes": hashes}
        if kind != "pe":
            explicit = "--entitlements" in args
            document["entitlement_policy"] = {"source": "explicit" if explicit else "inherited", "file_sha256": sha(Path(opt("--entitlements")).read_bytes()) if explicit else None}
        report.write_text(json.dumps(document))
        return RunResult(0 if verdict == "PASS" else 1, "")

    # -- Windows -------------------------------------------------------------
    def tool_certutil(self, args):
        if "-store" in args and args[-1].endswith(".cer"):
            Path(args[-1]).write_bytes(self.leaf_der)  # certutil -store My <id> <OutputFile> exports the certificate
            return RunResult(0, "")
        if "-store" in args:
            return RunResult(0, f"================ Certificate 0 ================\nCert Hash(sha1): {self.env.get('VELES_WIN_CERT_SHA1', THUMB)}\n")
        return RunResult(0, "")

    def tool_signtool(self, args):
        if args[0] == "sign":
            target = Path(args[-1])
            target.write_bytes(target.read_bytes() + b"AUTHENTICODE")
            return RunResult(0, "")
        return RunResult(
            0,
            "Signature Index: 0 (Primary Signature)\n\nSigning Certificate Chain:\n    Issued to: Fake Root\n"
            "    SHA1 hash: 1111111111111111111111111111111111111111\n\n"
            f"        Issued to: {WIN_DISPLAY_NAME}\n        SHA1 hash: {self.signed_leaf_sha1}\n\n"
            "The signature is timestamped: 2026-11-06\nSuccessfully verified\n",
        )

    def tool_7z(self, args):
        out = next(a for a in args if a.startswith("-o"))[2:]
        setup = Path(args[-1])
        dest = Path(out) / "$PLUGINSDIR" / "package"
        shutil.copytree(setup.parent / "package", dest)
        (dest / "payload" / "maintenance").mkdir(parents=True, exist_ok=True)
        shutil.copy2(setup.parent / "uninstall.exe", dest / "payload" / "maintenance" / "uninstall.exe")
        return RunResult(0, "")

    def tool_makensis(self, args):
        if args == ["/VERSION"]:
            return RunResult(0, "v3.08\n")
        outfile = next(a for a in args if a.startswith("/DOUTFILE="))[len("/DOUTFILE=") :]
        Path(outfile).write_bytes(b"MZ-setup")
        return RunResult(0, "")

    # -- macOS ---------------------------------------------------------------
    def tool_security(self, args):
        return RunResult(0, "")

    def tool_codesign(self, args):
        if "--sign" in args:
            target = Path(args[-1])
            if target.is_dir():
                seal = target / "Contents" / "_CodeSignature" / "CodeResources"
                seal.parent.mkdir(parents=True, exist_ok=True)
                seal.write_bytes(b"seal")
            else:
                target.write_bytes(target.read_bytes() + b"MACHOSIG")
            return RunResult(0, "")
        if args[0] == "-d" and "--entitlements" in args:
            return RunResult(0, gate.HOST_ENTITLEMENTS.read_text() if "veles-native-host" in args[-1] else "")
        if args[0] == "-dvv":
            runtime = "(runtime)" if not args[-1].endswith(".dmg") else ""
            return RunResult(
                0,
                f"Identifier=me.nagaev.veles\nCodeDirectory v=20500 flags=0x10000{runtime} hashes=1\n"
                f"Authority={MAC_IDENTITY}\nAuthority=Apple Root CA\nTimestamp=Nov 6, 2026\nTeamIdentifier={TEAM}\n",
            )
        return RunResult(0, "")

    def tool_xcrun(self, args):
        if args[0] == "notarytool" and args[1] == "submit":
            return RunResult(0, json.dumps({"id": "sub-1", "status": self.notary_status}))
        if args[0] == "notarytool":
            return RunResult(0, json.dumps({"status": self.notary_status, "issues": None}))
        if args[0] == "stapler" and args[1] == "staple":
            target = Path(args[-1])
            target.write_bytes(target.read_bytes() + b"TICKET")
            return RunResult(0 if self.staple_ok else 65, "")
        return RunResult(0 if self.staple_ok else 65, "")

    def tool_spctl(self, args):
        return RunResult(0, self.spctl_text)

    def tool_hdiutil(self, args):
        if args[0] == "attach":
            mountpoint = Path(args[args.index("-mountpoint") + 1])
            shutil.copytree(self.app_source, mountpoint / gate.APP, dirs_exist_ok=True, symlinks=True)
        elif args[0] == "create":
            Path(args[-1]).write_bytes(b"dmg-bytes")
        return RunResult(0, "")


# ---------------------------------------------------------------------------
# Input trees and requests
# ---------------------------------------------------------------------------


def write(root: Path, rel: str, data: bytes, mode: int = 0o644) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def write_inventory(root: Path, platform: str) -> None:
    entries = gate._entries(gate.payload_view(platform, root))
    (root / PACKAGE[platform] / "inventory.json").write_bytes(gate.canonical_json({"schema": 1, "entries": entries}))


def windows_unsigned_tree(root: Path, descriptor: bytes | None = None) -> None:
    descriptor = descriptor if descriptor is not None else descriptor_bytes("windows")
    write(root, "package/descriptor.json", descriptor)
    # The engine image embeds a sealed-flag carrier whose digest slot the producer left zeroed.
    write(root, "package/carrier.exe", b"MZ-engine\0" + carrier(descriptor, sealed=True), 0o755)
    write(root, "package/payload/veles-native-host.exe", b"MZ-host", 0o755)
    write(root, "package/payload/veles-bridge-cleanup.exe", b"MZ-cleanup", 0o755)
    write(root, "uninstall.exe", b"MZ-uninstaller", 0o755)
    write_inventory(root, "windows")


def macos_unsigned_tree(root: Path, descriptor: bytes | None = None) -> None:
    descriptor = descriptor if descriptor is not None else descriptor_bytes("macos")
    write(root, f"{gate.MAC_PACKAGE}/descriptor.json", descriptor)
    write(root, f"{HOST_APP}/Contents/MacOS/veles-native-host", b"host-macho", 0o755)
    write(root, f"{HOST_APP}/Contents/Info.plist", b"host-plist")
    write(root, f"{CARRIER_APP}/Contents/MacOS/veles-bridge-maintenance", b"engine-macho", 0o755)
    write(root, f"{CARRIER_APP}/Contents/Info.plist", b"carrier-plist")
    write(root, gate.CARRIER_PATH["macos"], carrier(descriptor, sealed=False))
    write(root, f"{APP}/Contents/MacOS/VelesBridgeInstaller", b"installer-macho", 0o755)
    write(root, f"{APP}/Contents/Info.plist", b"installer-plist")
    write_inventory(root, "macos")


UNSIGNED = {"windows": windows_unsigned_tree, "macos": macos_unsigned_tree}
CONTAINER = {"windows": gate.SETUP_NAME, "macos": gate.DMG_NAME}


class Case:
    """A scratch directory holding one stage's input tree, request and credentials."""

    def __init__(self, test: unittest.TestCase, platform: str, stage: str, **env_overrides):
        self.root = Path(tempfile.mkdtemp(prefix="gate-test-"))
        test.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.platform, self.stage = platform, stage
        self.inputs = self.root / "inputs"
        self.env = good_env(platform)
        self.env.update(env_overrides)
        if stage == "nested":
            UNSIGNED[platform](self.inputs)
            previous = "verify-inputs"
        else:
            # The outer stage consumes the real output of the nested stage plus the assembled container.
            nested = self.root / "nested-in"
            UNSIGNED[platform](nested)
            done = gate.run_stage(self._request(nested, self.root / "nested-out", "nested", "verify-inputs"), good_env(platform), FakeRunner(good_env(platform)))
            assert done.ok, done.detail
            shutil.copytree(self.root / "nested-out", self.inputs, symlinks=True)
            write(self.inputs, CONTAINER[platform], b"MZ-setup" if platform == "windows" else b"dmg-bytes", 0o755 if platform == "windows" else 0o644)
            previous = "assemble"
        self.boundary = {
            "stage": previous,
            "commit": COMMIT,
            "descriptor_digest": sha(self.descriptor.read_bytes()),
            "recipe_digest": gate.recipe_digest(gate.INSTALLER_DIR, self.platform),
            "input_inventory_sha256": "00" * 32,
            "output_inventory_sha256": gate.inventory_digest(self.inputs),
            "credentials": False,
        }

    @property
    def descriptor(self) -> Path:
        return self.inputs / PACKAGE[self.platform] / "descriptor.json"

    def _request(self, inputs: Path, output: Path, stage: str, previous: str) -> "gate.SignRequest":
        boundary = {
            "stage": previous,
            "commit": COMMIT,
            "descriptor_digest": sha((inputs / PACKAGE[self.platform] / "descriptor.json").read_bytes()),
            "recipe_digest": gate.recipe_digest(gate.INSTALLER_DIR, self.platform),
            "input_inventory_sha256": "00" * 32,
            "output_inventory_sha256": gate.inventory_digest(inputs),
            "credentials": False,
        }
        return gate.SignRequest(self.platform, stage, inputs, output, self.root / f"scratch-{stage}-x", self.root / f"reports-{stage}-x", COMMIT, inputs / PACKAGE[self.platform] / "descriptor.json", boundary, gate.recipe_digest(gate.INSTALLER_DIR, self.platform))

    def request(self, **changes) -> "gate.SignRequest":
        values = dict(
            platform=self.platform,
            stage=self.stage,
            inputs=self.inputs,
            output=self.root / "out",
            scratch=self.root / "scratch",
            reports=self.root / "reports",
            expected_commit=COMMIT,
            descriptor_path=self.descriptor,
            boundary=self.boundary,
            recipe_digest=gate.recipe_digest(gate.INSTALLER_DIR, self.platform),
        )
        values.update(changes)
        return gate.SignRequest(**values)

    def runner(self, **kwargs) -> FakeRunner:
        return FakeRunner(self.env, app_source=self.root / "out" / APP, **kwargs)

    def run(self, runner=None, **changes):
        runner = runner or self.runner()
        return runner, gate.run_stage(self.request(**changes), self.env, runner)


def leftovers(case: Case) -> list[str]:
    return [p for p in ("out", "scratch") if (case.root / p).exists()]


def reports_left(case: Case) -> list[str]:
    reports = case.root / "reports"
    return sorted(p.name for p in reports.rglob("*") if p.is_file()) if reports.exists() else []


FLOWS = (("windows", "nested"), ("windows", "outer"), ("macos", "nested"), ("macos", "outer"))


# ---------------------------------------------------------------------------
# Credentials and authorization
# ---------------------------------------------------------------------------


class CredentialAndAuthorizationTests(unittest.TestCase):
    def test_missing_credential_has_no_unsigned_fallback(self) -> None:
        for platform, stage, dropped in (
            ("windows", "nested", "VELES_WIN_PFX_BASE64"),
            ("windows", "nested", "VELES_WIN_PFX_PASSWORD"),
            ("windows", "outer", "VELES_WIN_CERT_SHA1"),
            ("windows", "outer", "VELES_WIN_TIMESTAMP_URL"),
            ("macos", "nested", "VELES_MAC_P12_BASE64"),
            ("macos", "nested", "VELES_MAC_IDENTITY"),
            ("macos", "outer", "VELES_NOTARY_KEY_ID"),
            ("macos", "outer", "VELES_NOTARY_KEY_BASE64"),
            ("macos", "outer", "VELES_NOTARY_ISSUER"),
        ):
            for how in ("absent", "empty", "blank"):
                with self.subTest(platform=platform, stage=stage, dropped=dropped, how=how):
                    case = Case(self, platform, stage)
                    if how == "absent":
                        del case.env[dropped]
                    else:
                        case.env[dropped] = "" if how == "empty" else "   "
                    runner, outcome = case.run()
                    self.assertFalse(outcome.ok)
                    self.assertEqual(outcome.code, "credential-missing")
                    self.assertIn(dropped, outcome.detail)
                    # No tool was started, so no key material was ever decoded or imported ...
                    self.assertEqual(runner.calls, [])
                    # ... and no output (signed or unsigned), scratch directory, report or checksum file exists.
                    self.assertEqual(leftovers(case), [])
                    self.assertFalse((case.root / "reports").exists())
        # The missing set comes from the platform contract, not from whatever the environment holds.
        self.assertEqual(gate.missing_credentials("windows", "nested", {}), sorted(gate.CREDENTIALS[("windows", "nested")]))
        self.assertIn("VELES_NOTARY_KEY_BASE64", gate.CREDENTIALS[("macos", "outer")])
        self.assertTrue(set(gate.CREDENTIALS[("macos", "outer")]) >= set(gate.CREDENTIALS[("macos", "nested")]))

    def test_malformed_credentials_are_refused_before_any_tool_starts(self) -> None:
        for platform, stage, key, value in (
            ("windows", "nested", "VELES_WIN_CERT_SHA1", "not-a-thumbprint"),
            ("windows", "nested", "VELES_WIN_TIMESTAMP_URL", "http://insecure.example.invalid/"),
            ("macos", "nested", "VELES_MAC_IDENTITY", "Apple Development: someone"),
        ):
            with self.subTest(key=key):
                case = Case(self, platform, stage, **{key: value})
                runner, outcome = case.run()
                self.assertEqual((outcome.ok, outcome.code, runner.calls), (False, "credential-invalid", []))
                self.assertEqual(leftovers(case), [])

    def test_pr_and_unapproved_descriptor_cannot_sign(self) -> None:
        bad_environments = {
            "pull_request": {"GITHUB_EVENT_NAME": "pull_request"},
            "pull_request_target": {"GITHUB_EVENT_NAME": "pull_request_target"},
            "push": {"GITHUB_EVENT_NAME": "push"},
            "schedule": {"GITHUB_EVENT_NAME": "schedule"},
            "workflow_run": {"GITHUB_EVENT_NAME": "workflow_run"},
            "dispatch carrying a PR head ref": {"GITHUB_HEAD_REF": "feature"},
            "dispatch carrying a PR base ref": {"GITHUB_BASE_REF": "main"},
            "branch ref": {"GITHUB_REF": "refs/heads/main"},
            "pr merge ref": {"GITHUB_REF": "refs/pull/7/merge"},
            "mutable tag shape": {"GITHUB_REF": "refs/tags/latest"},
            "leading-zero tag": {"GITHUB_REF": "refs/tags/01.2.3"},
            "version-shaped ref without refs/tags": {"GITHUB_REF": "refs/1.2.3"},
            "version-shaped branch": {"GITHUB_REF": "refs/heads/1.2.3"},
            "prefixed tag": {"GITHUB_REF": "refs/tags/v1.2.3"},
            "other commit": {"GITHUB_SHA": OTHER_COMMIT},
            "no protected environment": {"VELES_SIGNING_ENVIRONMENT": ""},
            "wrong environment": {"VELES_SIGNING_ENVIRONMENT": "production"},
            "claimed digest is not the file's": {"VELES_DESCRIPTOR_SHA256": "9" * 64},
            "approval missing": {"VELES_APPROVED_DESCRIPTOR_SHA256": ""},
            "approval malformed": {"VELES_APPROVED_DESCRIPTOR_SHA256": "not-a-digest"},
            "approval is another descriptor": {"VELES_APPROVED_DESCRIPTOR_SHA256": "9" * 64},
        }
        for platform, stage in FLOWS:
            for name, override in bad_environments.items():
                with self.subTest(platform=platform, stage=stage, case=name):
                    case = Case(self, platform, stage, **override)
                    runner, outcome = case.run()
                    self.assertFalse(outcome.ok)
                    self.assertEqual(outcome.code, "not-authorized")
                    self.assertEqual(runner.calls, [], "no tool may start before authorization")
                    self.assertEqual(leftovers(case), [])
                    self.assertFalse((case.root / "reports").exists())
        # The descriptor FILE is hashed, not trusted: a tampered descriptor whose env digests were not updated fails.
        case = Case(self, "windows", "nested")
        case.descriptor.write_bytes(case.descriptor.read_bytes() + b" ")
        runner, outcome = case.run()
        self.assertEqual((outcome.ok, outcome.code, runner.calls), (False, "not-authorized", []))
        # The authorized baseline really is authorized, so every failure above is attributable to its override.
        self.assertEqual(gate.authorize(good_env("windows"), sha(descriptor_bytes("windows")), COMMIT), [])
        # Credential-free jobs need the trusted context but not the protected environment or the approval.
        self.assertEqual(gate.authorize(ci_env("windows"), sha(descriptor_bytes("windows")), COMMIT, credentialed=False), [])
        self.assertTrue(gate.authorize({**ci_env("windows"), "GITHUB_EVENT_NAME": "pull_request"}, sha(descriptor_bytes("windows")), COMMIT, credentialed=False))
        self.assertTrue(gate.authorize(ci_env("windows"), sha(descriptor_bytes("windows")), COMMIT))

    def test_publisher_must_be_in_the_approved_descriptor_policy(self) -> None:
        # The installer's trust layer denies a valid signature by a publisher the descriptor does not list.
        for platform, stage, key, value in (
            ("windows", "nested", "VELES_WIN_PUBLISHER", "Some Other Publisher"),
            ("macos", "nested", "VELES_MAC_TEAM_ID", "OTHERTEAM1"),
        ):
            with self.subTest(platform=platform):
                case = Case(self, platform, stage, **{key: value})
                runner, outcome = case.run()
                self.assertEqual((outcome.ok, outcome.code, runner.calls), (False, "not-authorized", []))
                self.assertEqual(leftovers(case), [])

    def test_secrets_never_recorded_in_commands_reports_or_outcome(self) -> None:
        for platform, stage in FLOWS:
            with self.subTest(platform=platform, stage=stage):
                case = Case(self, platform, stage)
                runner, outcome = case.run()
                self.assertTrue(outcome.ok, outcome.detail)
                blob = json.dumps(runner.calls) + outcome.detail + json.dumps(outcome.fragment)
                for path in (case.root / "reports").rglob("*"):
                    if path.is_file():
                        blob += path.read_text(errors="replace")
                for secret in SECRET_VALUES + (case.env.get("VELES_WIN_PFX_BASE64", "~"), case.env.get("VELES_MAC_P12_BASE64", "~")):
                    self.assertNotIn(secret, blob)
                self.assertTrue(any("$VELES_" in " ".join(call) for call in runner.calls))

    def test_failure_details_and_blocked_record_do_not_leak_the_keychain_password(self) -> None:
        seen = {}

        class Spy(FakeRunner):
            def __call__(self, argv, *, capture=False):
                words = [str(a) for a in argv]
                if "create-keychain" in words:
                    seen["password"] = words[words.index("-p") + 1]
                return super().__call__(argv, capture=capture)

        case = Case(self, "macos", "nested")
        spy = Spy(case.env, fail_at=lambda text: "set-key-partition-list" in text)
        _, outcome = case.run(spy)
        self.assertEqual((outcome.ok, outcome.code), (False, "credential-import-failed"))
        self.assertGreaterEqual(len(seen.get("password", "")), 16, "the throwaway keychain has a generated password")
        blob = outcome.detail + "".join(p.read_text() for p in (case.root / "reports").rglob("*") if p.is_file())
        self.assertNotIn(seen["password"], blob)

    def test_inputs_are_verified_before_any_key_use(self) -> None:
        for platform, stage in FLOWS:
            with self.subTest(platform=platform, stage=stage, what="payload byte flipped"):
                case = Case(self, platform, stage)
                victim = case.inputs / ("package/payload/veles-native-host.exe" if platform == "windows" else f"{HOST_APP}/Contents/Info.plist")
                victim.write_bytes(victim.read_bytes() + b"tampered")
                case.boundary["output_inventory_sha256"] = gate.inventory_digest(case.inputs)  # the transfer record is honest; the payload is not
                runner, outcome = case.run()
                self.assertEqual((outcome.ok, outcome.code), (False, "input-mismatch"))
                self.assertEqual(runner.calls, [])
                self.assertEqual(leftovers(case), [])
            with self.subTest(platform=platform, stage=stage, what="tree changed after the previous job"):
                case = Case(self, platform, stage)
                write(case.inputs, f"{PACKAGE[platform]}/extra.txt", b"x")
                runner, outcome = case.run()
                self.assertEqual((outcome.ok, outcome.code, runner.calls), (False, "input-mismatch", []))
        for field, value in (("commit", OTHER_COMMIT), ("descriptor_digest", "9" * 64), ("recipe_digest", "9" * 64), ("stage", "sign-outer"), ("credentials", True)):
            with self.subTest(boundary=field):
                case = Case(self, "windows", "nested")
                case.boundary[field] = value
                runner, outcome = case.run()
                self.assertEqual((outcome.ok, outcome.code, runner.calls), (False, "input-mismatch", []))
        case = Case(self, "windows", "nested")
        case.boundary.pop("recipe_digest")
        runner, outcome = case.run()
        self.assertEqual((outcome.ok, outcome.code, runner.calls), (False, "input-mismatch", []))
        # An executable that is not part of the signing plan is refused even when the transfer record covers it.
        case = Case(self, "windows", "nested")
        write(case.inputs, "package/rogue.exe", b"MZ-unlisted", 0o755)
        case.boundary["output_inventory_sha256"] = gate.inventory_digest(case.inputs)
        runner, outcome = case.run()
        self.assertEqual((outcome.ok, outcome.code, runner.calls), (False, "input-mismatch", []))
        self.assertIn("rogue.exe", outcome.detail)
        # The descriptor inside the package must be the approved one.
        case = Case(self, "windows", "nested")
        other = descriptor_bytes("windows", publishers=[WIN_PUBLISHER, "Another"])
        approved_file = case.root / "approved-descriptor.json"
        approved_file.write_bytes(other)
        case.env.update(VELES_DESCRIPTOR_SHA256=sha(other), VELES_APPROVED_DESCRIPTOR_SHA256=sha(other))
        case.boundary["descriptor_digest"] = sha(other)
        runner, outcome = case.run(descriptor_path=approved_file)
        self.assertEqual((outcome.ok, outcome.code, runner.calls), (False, "input-mismatch", []))
        self.assertIn("descriptor inside the package", outcome.detail)

    def test_signing_runs_over_existing_outputs_and_refuses_them(self) -> None:
        case = Case(self, "windows", "nested")
        (case.root / "out").mkdir()
        runner, outcome = case.run()
        self.assertEqual((outcome.ok, outcome.code, runner.calls), (False, "bad-request", []))
        self.assertTrue((case.root / "out").exists(), "a pre-existing directory is not ours to delete")


# ---------------------------------------------------------------------------
# Successful orchestration shape
# ---------------------------------------------------------------------------


def tool_calls(runner, tool):
    return [c for c in runner.calls if Path(c[0]).stem == tool]


def correspondence_calls(runner):
    return [c for c in runner.calls if any(x.endswith("correspondence.py") for x in c)]


class OrchestrationShapeTests(unittest.TestCase):
    def test_windows_nested_signs_four_components_seals_the_engine_and_cleans(self) -> None:
        case = Case(self, "windows", "nested")
        runner, outcome = case.run()
        self.assertTrue(outcome.ok, outcome.detail)
        signed = [Path(c[-1]).name for c in tool_calls(runner, "signtool") if c[1] == "sign"]
        # The carrier-bearing engine image is signed LAST, after the sealed-inventory digest was written into it.
        self.assertEqual(signed, ["veles-native-host.exe", "veles-bridge-cleanup.exe", "uninstall.exe", "carrier.exe"])
        sign = next(c for c in tool_calls(runner, "signtool") if c[1] == "sign")
        for flag in ("/fd", "/td", "/tr", "/sha1"):
            self.assertIn(flag, sign)
        self.assertIn("$VELES_WIN_TIMESTAMP_URL", sign)
        self.assertIn("$VELES_WIN_CERT_SHA1", sign)
        # Inputs are untouched; signed copies live in the output; nothing was rebuilt (no compiler ran).
        self.assertEqual((case.inputs / "package/payload/veles-native-host.exe").read_bytes(), b"MZ-host")
        self.assertTrue((case.root / "out/package/payload/veles-native-host.exe").read_bytes().endswith(b"AUTHENTICODE"))
        self.assertFalse({Path(c[0]).stem for c in runner.calls} & {"cargo", "makensis", "gradlew", "npm"})
        # The sealed-inventory document is bound into the engine image: the digest slot holds sha256(document).
        sealed = (case.root / "out/package/sealed-inventory.json").read_bytes()
        engine = (case.root / "out/package/carrier.exe").read_bytes()
        self.assertEqual(gate.read_carrier_seal(engine.removesuffix(b"AUTHENTICODE"), embedded=True), sha(sealed))
        self.assertEqual(outcome.fragment["seal"]["sealed_inventory_sha256"], sha(sealed))
        self.assertEqual(outcome.fragment["seal"]["status"], "sealed")
        # The Task 9 comparator ran once per PE through the runner, and its report was kept.
        corr = correspondence_calls(runner)
        self.assertEqual(len(corr), 4)
        self.assertTrue(all(c[c.index("--kind") + 1] == "pe" for c in corr))
        self.assertEqual(len(outcome.fragment["components"]), 4)
        self.assertTrue((case.root / "reports" / "SHA256SUMS").is_file())
        self.assertFalse((case.root / "scratch").exists())
        self.assertIn("-delstore", [a for c in runner.calls for a in c])
        self.assertEqual(sorted(outcome.fragment["tools"]), ["certutil", "signtool"])

    def test_windows_outer_signs_only_setup_and_checks_pe_and_nested_hashes(self) -> None:
        case = Case(self, "windows", "outer")
        runner, outcome = case.run()
        self.assertTrue(outcome.ok, outcome.detail)
        self.assertEqual([Path(c[-1]).name for c in tool_calls(runner, "signtool") if c[1] == "sign"], ["setup.exe"])
        container = outcome.fragment["containers"][0]
        self.assertEqual(container["kind"], "nsis-setup")
        self.assertEqual(container["pre_sign_sha256"], sha(b"MZ-setup"))
        self.assertEqual(container["post_sign_sha256"], sha(b"MZ-setup" + b"AUTHENTICODE"))
        self.assertTrue(container["nested_hashes_match"])
        self.assertIn("7z", [Path(c[0]).stem for c in runner.calls])
        # setup.exe is compared as a PE immediately around ITS OWN signature; its payload is never compared with earlier bytes.
        corr = correspondence_calls(runner)
        self.assertEqual(len(corr), 1)
        self.assertEqual(corr[0][corr[0].index("--kind") + 1], "pe")
        self.assertTrue(Path(corr[0][corr[0].index("--unsigned") + 1]).parts[-2] == "pre")

    def test_windows_outer_fails_when_extracted_nested_hashes_differ(self) -> None:
        case = Case(self, "windows", "outer")

        class Tampering(FakeRunner):
            def tool_7z(self, args):
                result = super().tool_7z(args)
                victim = Path(next(a for a in args if a.startswith("-o"))[2:]) / "$PLUGINSDIR/package/carrier.exe"
                victim.write_bytes(b"swapped")
                return result

        runner, outcome = case.run(Tampering(case.env))
        self.assertEqual((outcome.ok, outcome.code), (False, "nested-hash-mismatch"))
        self.assertEqual(leftovers(case), [])

    def test_macos_nested_signs_inside_out_seals_the_carrier_and_uses_host_entitlements(self) -> None:
        case = Case(self, "macos", "nested")
        runner, outcome = case.run()
        self.assertTrue(outcome.ok, outcome.detail)
        signs = [c for c in tool_calls(runner, "codesign") if "--sign" in c]
        self.assertEqual(
            [Path(c[-1]).name for c in signs],
            ["veles-native-host", "Veles Native Bridge.app", "veles-bridge-maintenance", "carrier.app", "VelesBridgeInstaller", "Veles Bridge Installer.app"],
        )
        for call in signs:
            self.assertIn("--timestamp", call)
            self.assertEqual(call[call.index("--options") + 1], "runtime")
            self.assertEqual(call[call.index("--sign") + 1], "$VELES_MAC_IDENTITY")
        entitled = [Path(c[-1]).name for c in signs if "--entitlements" in c]
        self.assertEqual(entitled, ["veles-native-host", "Veles Native Bridge.app"])
        self.assertTrue(all(c[c.index("--entitlements") + 1].endswith("Host.entitlements") for c in signs if "--entitlements" in c))
        self.assertFalse(any(c[c.index("--sign") + 1] == "-" for c in signs), "no ad-hoc identity")
        flat = [a for c in runner.calls for a in c]
        self.assertIn("create-keychain", flat)
        self.assertIn("delete-keychain", flat)
        # The sealed digest sits in the carrier BEFORE the carrier app is signed, and the seal covers the signed host app.
        out = case.root / "out"
        sealed = (out / gate.MAC_PACKAGE / "sealed-inventory.json").read_bytes()
        self.assertEqual(gate.read_carrier_seal((out / gate.CARRIER_PATH["macos"]).read_bytes(), embedded=False), sha(sealed))
        paths = {e["path"] for e in json.loads(sealed)["entries"]}
        self.assertIn("Veles Native Bridge.app/Contents/_CodeSignature/CodeResources", paths)
        self.assertTrue(all(Path(c[0]).stem != "xcrun" for c in runner.calls), "nested-stage apps are notarized by the outer DMG, not here")
        # Each executable is compared as Mach-O, each bundle as a tree; the sealed unsigned baseline is the comparison input.
        kinds = sorted(c[c.index("--kind") + 1] for c in correspondence_calls(runner))
        self.assertEqual(kinds, ["macho"] * 3 + ["tree"] * 3)
        policy_runs = [Path(c[c.index("--signed") + 1]).name for c in correspondence_calls(runner) if "--entitlements" in c]
        self.assertEqual(sorted(policy_runs), ["Veles Native Bridge.app", "veles-native-host"])
        # Task 9 applies ONE entitlement policy per tree: the installer app is compared without the nested apps.
        installer = next(c for c in correspondence_calls(runner) if Path(c[c.index("--signed") + 1]).name == APP)
        self.assertNotIn("--entitlements", installer)
        for side in ("unsigned", "signed"):
            seen = runner.views[APP][side]
            self.assertTrue(seen, f"the {side} view of the installer app is empty")
            self.assertFalse([p for p in seen if "Veles Native Bridge.app" in p or "carrier.app" in p], f"nested apps leaked into the {side} view")
            self.assertTrue([p for p in seen if p.endswith("MacOS/VelesBridgeInstaller")], "the installer's own code is compared")
        self.assertTrue(runner.views["Veles Native Bridge.app"]["signed"], "the host app is compared on its own")
        self.assertEqual(sorted(outcome.fragment["tools"]), ["codesign", "security"])

    def test_macos_nested_refuses_a_component_order_that_is_not_inside_out(self) -> None:
        bad = list(gate.COMPONENTS["macos"])
        bad[0], bad[1] = bad[1], bad[0]  # bundle before the executable it contains
        with self.assertRaises(gate.PlanError):
            gate.check_inside_out(bad)
        gate.check_inside_out(gate.COMPONENTS["macos"])
        gate.check_inside_out(gate.COMPONENTS["windows"])

    def test_macos_outer_signs_submits_waits_staples_assesses_and_compares_contents(self) -> None:
        case = Case(self, "macos", "outer")
        runner, outcome = case.run()
        self.assertTrue(outcome.ok, outcome.detail)
        flat = [" ".join(c) for c in runner.calls]
        ordered = [
            next(i for i, c in enumerate(flat) if needle in c)
            for needle in ("--sign $VELES_MAC_IDENTITY", "notarytool submit", "notarytool log", "stapler staple", "stapler validate", "spctl")
        ]
        self.assertEqual(ordered, sorted(ordered))
        submit = next(c for c in runner.calls if "notarytool" in c and "submit" in c)
        self.assertIn("--wait", submit)
        for ref in ("$VELES_NOTARY_KEY_ID", "$VELES_NOTARY_ISSUER"):
            self.assertIn(ref, submit)
        dmg = outcome.fragment["containers"][0]
        self.assertEqual(dmg["kind"], "dmg")
        self.assertEqual(dmg["notarization"]["status"], "Accepted")
        self.assertTrue(dmg["staple"]["validated"] and dmg["gatekeeper"]["accepted"] and dmg["inner_app_gatekeeper"]["accepted"])
        self.assertTrue(dmg["contents_match"])
        # Both mounts are detached again; the pre-sign image is mounted BEFORE signing.
        attaches = [i for i, c in enumerate(flat) if "hdiutil attach" in c]
        detaches = [i for i, c in enumerate(flat) if "hdiutil detach" in c]
        self.assertEqual(len(attaches), 2)
        self.assertEqual(len(detaches), 2)
        self.assertLess(attaches[0], ordered[0])
        self.assertEqual([c["kind"] for c in outcome.fragment["containers"]], ["dmg"])
        self.assertIn("-readonly", runner.calls[attaches[0]])

    def test_macos_outer_stops_on_a_rejected_notarization_a_failed_staple_or_a_gatekeeper_rejection(self) -> None:
        class DirtyLog(FakeRunner):
            def tool_xcrun(self, args):
                if args[:2] == ["notarytool", "log"]:
                    return RunResult(0, json.dumps({"status": "Accepted", "issues": [{"severity": "error"}]}))
                return super().tool_xcrun(args)

        class NoStatus(FakeRunner):
            def tool_xcrun(self, args):
                return RunResult(0, "not json") if args[:2] == ["notarytool", "submit"] else super().tool_xcrun(args)

        cases = (
            ("rejected", dict(notary_status="Invalid"), "notarization-failed", "notarytool log"),
            ("unstapled", dict(staple_ok=False), "staple-failed", "spctl"),
            ("gatekeeper rejects", dict(spctl_text="rejected\nsource=no usable signature\n"), "gatekeeper-rejected", None),
            ("gatekeeper accepts but not notarized", dict(spctl_text="accepted\nsource=Developer ID\n"), "gatekeeper-rejected", None),
        )
        for name, kwargs, code, never in cases:
            with self.subTest(name):
                case = Case(self, "macos", "outer")
                runner = case.runner(**kwargs)
                _, outcome = case.run(runner)
                self.assertEqual((outcome.ok, outcome.code), (False, code))
                self.assertEqual(leftovers(case), [])
                self.assertTrue([c for c in runner.calls if "delete-keychain" in c])
                if never:
                    self.assertFalse([c for c in runner.calls if never in " ".join(c)], f"{never} must not run after {name}")
        for fake in (DirtyLog, NoStatus):
            with self.subTest(fake.__name__):
                case = Case(self, "macos", "outer")
                _, outcome = case.run(fake(case.env, app_source=case.root / "out" / APP))
                self.assertEqual((outcome.ok, outcome.code), (False, "notarization-failed"))
                self.assertEqual(leftovers(case), [])

    def test_macos_outer_refuses_a_dmg_that_does_not_hold_exactly_the_signed_app(self) -> None:
        case = Case(self, "macos", "outer")

        class Extra(FakeRunner):
            def tool_hdiutil(self, args):
                result = super().tool_hdiutil(args)
                if args[0] == "attach":
                    (Path(args[args.index("-mountpoint") + 1]) / "unexpected.txt").write_text("x")
                return result

        runner, outcome = case.run(Extra(case.env, app_source=case.root / "out" / APP))
        self.assertEqual((outcome.ok, outcome.code), (False, "dmg-contents-mismatch"))
        self.assertEqual(leftovers(case), [])
        # The mount that was made is detached on the failure path.
        self.assertTrue([c for c in runner.calls if "detach" in c])
        self.assertFalse([c for c in runner.calls if "notarytool" in c], "nothing is submitted for a DMG that holds other content")

    def test_wrong_signer_and_weak_signatures_fail_closed(self) -> None:
        class WrongPublisher(FakeRunner):
            def tool_signtool(self, args):
                result = super().tool_signtool(args)
                return RunResult(0, result.output.replace(THUMB, "2" * 40)) if args[0] == "verify" else result

        class NoTimestamp(FakeRunner):
            def tool_signtool(self, args):
                result = super().tool_signtool(args)
                return RunResult(0, result.output.replace("The signature is timestamped", "no stamp")) if args[0] == "verify" else result

        for fake, code in ((WrongPublisher, "signature-unverified"), (NoTimestamp, "signature-unverified")):
            with self.subTest(fake=fake.__name__):
                case = Case(self, "windows", "nested")
                _, outcome = case.run(fake(case.env))
                self.assertEqual((outcome.ok, outcome.code), (False, code))
                self.assertEqual(leftovers(case), [])

        class WrongEntitlements(FakeRunner):
            def tool_codesign(self, args):
                result = super().tool_codesign(args)
                if args[0] == "-d" and "--entitlements" in args and "veles-native-host" in args[-1]:
                    return RunResult(0, result.output.replace("</dict>", "<key>com.apple.security.cs.disable-library-validation</key><true/></dict>"))
                return result

        class MissingEntitlements(FakeRunner):
            def tool_codesign(self, args):
                if args[0] == "-d" and "--entitlements" in args:
                    return RunResult(0, "")
                return super().tool_codesign(args)

        class WrongTeam(FakeRunner):
            def tool_codesign(self, args):
                result = super().tool_codesign(args)
                return RunResult(0, result.output.replace(f"TeamIdentifier={TEAM}", "TeamIdentifier=EVIL000000")) if args[0] == "-dvv" else result

        class NoRuntime(FakeRunner):
            def tool_codesign(self, args):
                result = super().tool_codesign(args)
                return RunResult(0, result.output.replace("(runtime)", "")) if args[0] == "-dvv" else result

        for fake, code in ((WrongEntitlements, "entitlements-mismatch"), (MissingEntitlements, "entitlements-mismatch"), (WrongTeam, "signature-unverified"), (NoRuntime, "signature-unverified")):
            with self.subTest(fake=fake.__name__):
                case = Case(self, "macos", "nested")
                _, outcome = case.run(fake(case.env))
                self.assertEqual((outcome.ok, outcome.code), (False, code))
                self.assertEqual(leftovers(case), [])

    def test_wrong_certificate_in_the_store_is_refused(self) -> None:
        case = Case(self, "windows", "nested")

        class Other(FakeRunner):
            def tool_certutil(self, args):
                return RunResult(0, "Cert Hash(sha1): 0000000000000000000000000000000000000000\n") if "-store" in args else RunResult(0, "")

        _, outcome = case.run(Other(case.env))
        self.assertEqual((outcome.ok, outcome.code), (False, "credential-import-failed"))
        self.assertEqual(leftovers(case), [])


class WindowsSignerIdentityTests(unittest.TestCase):
    """F4: the signing gate authorizes the SAME identity the engine's trust layer does.

    Runtime policy is ``sha256:<SHA-256 of the leaf certificate DER>``. A display name is a label on
    the certificate, never an authorization identifier, so the gate must derive and check the
    fingerprint and bind it to the certificate that actually signs.
    """

    def test_a_display_name_is_not_an_authorizable_publisher(self) -> None:
        for label, value in (("display name", WIN_DISPLAY_NAME), ("uppercase digest", "sha256:" + "AB" * 32),
                             ("short digest", "sha256:" + "ab" * 31), ("no scheme", "ab" * 32),
                             ("sha1 thumbprint", "sha1:" + THUMB), ("empty", "")):
            with self.subTest(publisher=label):
                descriptor = descriptor_bytes("windows", publishers=[value] if value else [WIN_PUBLISHER])
                env = {**good_env("windows", descriptor), "VELES_WIN_PUBLISHER": value}
                _, problems = gate.signer_policy("windows", env, descriptor, COMMIT)
                self.assertTrue(problems, f"the gate authorized {value!r}, which the runtime can never match")

    def test_the_runtime_format_fingerprint_is_the_authorized_identity(self) -> None:
        env = good_env("windows")
        policy, problems = gate.signer_policy("windows", env, descriptor_bytes("windows"), COMMIT)
        self.assertEqual(problems, [])
        self.assertEqual(policy["publisher"], WIN_PUBLISHER)
        self.assertRegex(policy["publisher"], r"^sha256:[0-9a-f]{64}$")

    def test_signed_components_record_the_fingerprint_not_a_display_name(self) -> None:
        for stage in ("nested", "outer"):
            with self.subTest(stage=stage):
                case = Case(self, "windows", stage)
                runner, outcome = case.run()
                self.assertTrue(outcome.ok, outcome.detail)
                records = outcome.fragment.get("components") or outcome.fragment.get("containers")
                self.assertTrue(records)
                for record in records:
                    self.assertEqual(record["signature"]["identity"], WIN_PUBLISHER)
                    self.assertNotIn(WIN_DISPLAY_NAME, json.dumps(record["signature"]))
                exports = [c for c in runner.calls if c[0] == "certutil" and c[-1].endswith(".cer")]
                self.assertEqual(len(exports), 1, "the leaf certificate is exported once to derive the fingerprint")

    def test_the_store_certificate_must_hash_to_the_approved_fingerprint(self) -> None:
        other = b"0\x82a different leaf certificate"
        other_thumb = hashlib.sha1(other).hexdigest().upper()
        # A certificate the operator imported that is NOT the approved publisher's: thumbprint and DER agree
        # with each other, but the SHA-256 pin in the approved descriptor names another certificate.
        case = Case(self, "windows", "nested", VELES_WIN_CERT_SHA1=other_thumb)
        runner, outcome = case.run(FakeRunner(case.env, leaf_der=other))
        self.assertEqual((outcome.ok, outcome.code), (False, "credential-import-failed"))
        self.assertIn("approved", outcome.detail)
        self.assertEqual([c for c in runner.calls if c[0] == "signtool"], [], "no signature is made with the wrong certificate")
        self.assertEqual(leftovers(case), [])

    def test_the_exported_certificate_must_be_the_configured_thumbprint(self) -> None:
        case = Case(self, "windows", "nested")
        runner, outcome = case.run(FakeRunner(case.env, leaf_der=b"not the configured certificate"))
        self.assertEqual((outcome.ok, outcome.code), (False, "credential-import-failed"))
        self.assertEqual([c for c in runner.calls if c[0] == "signtool"], [])

    def test_a_missing_or_empty_certificate_export_blocks_before_signing(self) -> None:
        case = Case(self, "windows", "nested")
        runner, outcome = case.run(FakeRunner(case.env, leaf_der=b""))
        self.assertEqual((outcome.ok, outcome.code), (False, "credential-import-failed"))
        self.assertEqual([c for c in runner.calls if c[0] == "signtool"], [])

    def test_a_file_signed_by_another_leaf_is_refused(self) -> None:
        case = Case(self, "windows", "nested")
        _, outcome = case.run(FakeRunner(case.env, signed_leaf_sha1="2" * 40))
        self.assertEqual((outcome.ok, outcome.code), (False, "signature-unverified"))
        self.assertEqual(leftovers(case), [])

    def test_a_signature_with_no_leaf_hash_in_the_output_is_refused(self) -> None:
        class NoHash(FakeRunner):
            def tool_signtool(self, args):
                result = super().tool_signtool(args)
                if args[0] == "sign":
                    return result
                return RunResult(0, "\n".join(l for l in result.output.splitlines() if "SHA1 hash" not in l) + "\n")

        case = Case(self, "windows", "nested")
        _, outcome = case.run(NoHash(case.env))
        self.assertEqual((outcome.ok, outcome.code), (False, "signature-unverified"))

    def test_self_signed_mode_records_the_anchor_and_removes_it(self) -> None:
        root_der = b"0\x82fixture self-signing root"
        case = Case(
            self,
            "windows",
            "nested",
            VELES_WIN_TRUST_SELF_SIGNED="1",
            VELES_WIN_ROOT_CER_BASE64=base64.b64encode(root_der).decode(),
        )
        runner, outcome = case.run()
        self.assertTrue(outcome.ok, outcome.detail)
        for record in outcome.fragment["components"]:
            signature = record["signature"]
            self.assertIs(signature["public_trust"], False)
            self.assertEqual(signature["trust_mode"], "self-signed")
            self.assertEqual(signature["trust_anchor_sha256"], hashlib.sha256(root_der).hexdigest())
        self.assertTrue(any(call[0] == "certutil" and "-addstore" in call for call in runner.calls))
        self.assertTrue(any(call[0] == "certutil" and "-delstore" in call for call in runner.calls))

    def test_self_signed_mode_requires_a_well_formed_root_certificate(self) -> None:
        env = {**good_env("windows"), "VELES_WIN_TRUST_SELF_SIGNED": "1"}
        self.assertIn("VELES_WIN_ROOT_CER_BASE64", gate.missing_credentials("windows", "nested", env))
        env["VELES_WIN_ROOT_CER_BASE64"] = "not base64!"
        self.assertTrue(any("ROOT_CER" in problem for problem in gate.credential_format_errors("windows", env)))

    def test_a_self_signed_signature_must_record_its_anchor(self) -> None:
        policy = {"publisher": WIN_PUBLISHER}
        signature = {
            "verified": True,
            "timestamped": True,
            "identity": WIN_PUBLISHER,
            "public_trust": False,
            "trust_anchor_sha256": "a" * 64,
        }
        self.assertEqual(gate._signature_errors("x", signature, "windows", policy, runtime=True), [])
        del signature["trust_anchor_sha256"]
        self.assertTrue(gate._signature_errors("x", signature, "windows", policy, runtime=True))


# ---------------------------------------------------------------------------
# Assembly (no credentials)
# ---------------------------------------------------------------------------


class AssemblyTests(unittest.TestCase):
    def nested_output(self, platform):
        case = Case(self, platform, "nested")
        runner, outcome = case.run()
        self.assertTrue(outcome.ok, outcome.detail)
        return case, outcome

    def request(self, case, outcome, tmp, **changes):
        values = dict(
            platform=case.platform,
            stage="outer",
            inputs=case.root / "out",
            output=tmp / "assembled",
            scratch=tmp / "scratch",
            reports=tmp / "reports",
            expected_commit=COMMIT,
            descriptor_path=case.descriptor,
            boundary=outcome.fragment["boundary"],
            recipe_digest=gate.recipe_digest(gate.INSTALLER_DIR, case.platform),
        )
        values.update(changes)
        return gate.SignRequest(**values)

    def test_assembly_holds_no_credentials_and_builds_from_the_signed_tree(self) -> None:
        for platform in ("windows", "macos"):
            with self.subTest(platform=platform):
                case, outcome = self.nested_output(platform)
                tmp = Path(tempfile.mkdtemp(prefix="gate-asm-"))
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                env = ci_env(platform)
                if platform == "windows":
                    env["NSIS_PATH"] = "/fake/nsis/makensis.exe"
                runner = FakeRunner(env)
                done = gate.assemble(self.request(case, outcome, tmp), env, runner)
                self.assertTrue(done.ok, done.detail)
                self.assertEqual(done.fragment["boundary"]["stage"], "assemble")
                self.assertIs(done.fragment["boundary"]["credentials"], False)
                self.assertTrue((tmp / "assembled" / CONTAINER[platform]).is_file())
                # The signed nested files are carried over byte for byte; nothing is re-signed here.
                names = {Path(c[0]).stem for c in runner.calls}
                self.assertFalse(names & {"signtool", "codesign", "security", "certutil", "xcrun"})
                if platform == "windows":
                    cmd = next(c for c in runner.calls if "/DOUTFILE=" in " ".join(c))
                    self.assertTrue(any(a.startswith("/DUNINSTALLER_SIGNED=") for a in cmd))
                    self.assertTrue(cmd[-1].endswith("installer.nsi"))
                self.assertFalse((tmp / "scratch").exists())

    def test_assembly_refuses_credentials_wrong_inputs_and_wrong_tool_pin(self) -> None:
        case, outcome = self.nested_output("windows")
        tmp = Path(tempfile.mkdtemp(prefix="gate-asm-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = ci_env("windows") | {"NSIS_PATH": "/fake/makensis.exe"}
        with self.subTest("credential present"):
            runner = FakeRunner(env)
            done = gate.assemble(self.request(case, outcome, tmp), env | {"VELES_WIN_PFX_PASSWORD": "x"}, runner)
            self.assertEqual((done.ok, done.code, runner.calls), (False, "credentials-present", []))
        with self.subTest("untrusted context"):
            done = gate.assemble(self.request(case, outcome, tmp), env | {"GITHUB_EVENT_NAME": "pull_request"}, FakeRunner(env))
            self.assertEqual((done.ok, done.code), (False, "input-mismatch"))
        with self.subTest("boundary is not the credentialed nested output"):
            bad = dict(outcome.fragment["boundary"], stage="verify-inputs")
            done = gate.assemble(self.request(case, outcome, tmp, boundary=bad), env, FakeRunner(env))
            self.assertEqual((done.ok, done.code), (False, "input-mismatch"))
        with self.subTest("recipe changed"):
            done = gate.assemble(self.request(case, outcome, tmp, recipe_digest="9" * 64), env, FakeRunner(env))
            self.assertEqual((done.ok, done.code), (False, "input-mismatch"))
        with self.subTest("tree changed since signing"):
            changed = tmp / "changed"
            shutil.copytree(case.root / "out", changed)
            (changed / "package" / "payload" / "veles-native-host.exe").write_bytes(b"swapped")
            done = gate.assemble(self.request(case, outcome, tmp, inputs=changed), env, FakeRunner(env))
            self.assertEqual((done.ok, done.code), (False, "input-mismatch"))
        with self.subTest("nsis pin"):

            class OldNsis(FakeRunner):
                def tool_makensis(self, args):
                    return RunResult(0, "v3.07\n") if args == ["/VERSION"] else super().tool_makensis(args)

            done = gate.assemble(self.request(case, outcome, tmp), env, OldNsis(env))
            self.assertEqual((done.ok, done.code), (False, "tool-pin-mismatch"))
            self.assertFalse((tmp / "assembled").exists())
            self.assertFalse((tmp / "scratch").exists())
        with self.subTest("tool failure leaves nothing"):
            runner = FakeRunner(env, fail_at=lambda text: "/DOUTFILE=" in text)
            done = gate.assemble(self.request(case, outcome, tmp), env, runner)
            self.assertEqual((done.ok, done.code), (False, "assemble-failed"))
            self.assertFalse((tmp / "assembled").exists())

    def test_makensis_runs_with_the_planned_nocd_and_an_absolute_recipe(self) -> None:
        # Plan: "Unicode MUI2 and zlib, /NOCD, an absolute script path and fixed input/output roots".
        case, outcome = self.nested_output("windows")
        env = ci_env("windows") | {"NSIS_PATH": "/fake/makensis.exe"}
        runner = FakeRunner(env)
        tmp = Path(tempfile.mkdtemp(prefix="gate-asm-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.assertTrue(gate.assemble(self.request(case, outcome, tmp), env, runner).ok)
        compile_call = next(c for c in runner.calls if any(a.startswith("/DOUTFILE=") for a in c))
        self.assertIn("/NOCD", compile_call)
        self.assertEqual(compile_call[-1], str(gate.NSIS_RECIPE))
        self.assertTrue(Path(compile_call[-1]).is_absolute())
        self.assertLess(compile_call.index("/NOCD"), len(compile_call) - 1, "options come before the script")

    def test_cleanup_runs_on_each_assembly_failure(self) -> None:
        for platform in ("windows", "macos"):
            case, outcome = self.nested_output(platform)
            env = ci_env(platform) | ({"NSIS_PATH": "/fake/makensis.exe"} if platform == "windows" else {})
            clean = FakeRunner(env)
            tmp = Path(tempfile.mkdtemp(prefix="gate-asm-"))
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            self.assertTrue(gate.assemble(self.request(case, outcome, tmp), env, clean).ok)
            for index in range(len(clean.calls)):
                with self.subTest(platform=platform, failing_call=index):
                    tmp = Path(tempfile.mkdtemp(prefix="gate-asm-"))
                    self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                    done = gate.assemble(self.request(case, outcome, tmp), env, FakeRunner(env, fail_at=index))
                    self.assertFalse(done.ok)
                    self.assertFalse((tmp / "assembled").exists())
                    self.assertFalse((tmp / "scratch").exists())


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class CleanupTests(unittest.TestCase):
    @staticmethod
    def is_cleanup(call) -> bool:
        flat = " ".join(call)
        return "delete-keychain" in flat or "-delstore" in flat or "hdiutil detach" in flat

    def test_cleanup_runs_on_each_signing_failure(self) -> None:
        for platform, stage in FLOWS:
            case = Case(self, platform, stage)
            clean_runner, outcome = case.run()
            self.assertTrue(outcome.ok, outcome.detail)
            total = len(clean_runner.calls)
            self.assertGreater(total, 5)
            for index in range(total):
                with self.subTest(platform=platform, stage=stage, failing_call=index, command=" ".join(clean_runner.calls[index])[:80]):
                    case = Case(self, platform, stage)
                    runner = case.runner(fail_at=index)
                    _, outcome = case.run(runner)
                    self.assertFalse(outcome.ok, "a failing command must fail the stage, a failing cleanup too")
                    if self.is_cleanup(clean_runner.calls[index]):
                        self.assertTrue(outcome.cleanup_failures or outcome.code == "cleanup-failed")
                    # Every credential store or mount that was created has its undo command issued afterwards.
                    for marker, undo in (("-importPFX", "-delstore"), ("create-keychain", "delete-keychain"), ("attach", "detach")):
                        made = [i for i, c in enumerate(runner.calls) if marker in c]
                        if made:
                            self.assertTrue(any(undo in c for c in runner.calls[made[0] :]), f"{undo} was not issued after {marker}")
                    # Scratch (decoded key material) and any signed or unsigned output are gone.
                    self.assertEqual(leftovers(case), [])
                    self.assertEqual(
                        [n for n in reports_left(case) if n != f"{platform}-{stage}-blocked.json"],
                        [],
                        "only the BLOCKED record survives a failed stage",
                    )

    def test_cleanup_continues_after_a_failing_cleanup_step(self) -> None:
        case = Case(self, "macos", "nested")
        runner = case.runner(fail_at=lambda text: "delete-keychain" in text)
        _, outcome = case.run(runner)
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.cleanup_failures)
        self.assertEqual(leftovers(case), [], "scratch and output are removed even if the keychain removal failed")

    def test_cleanup_continues_after_a_runner_that_raises_during_cleanup(self) -> None:
        case = Case(self, "windows", "nested")

        class Raising(FakeRunner):
            def __call__(self, argv, *, capture=False):
                if "-delstore" in [str(a) for a in argv]:
                    raise OSError("boom")
                return super().__call__(argv, capture=capture)

        _, outcome = case.run(Raising(case.env))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.code, "cleanup-failed")
        self.assertEqual(leftovers(case), [])

    def test_a_tool_that_cannot_start_is_a_stage_failure_that_cleans_up(self) -> None:
        case = Case(self, "windows", "nested")

        class Missing(FakeRunner):
            def __call__(self, argv, *, capture=False):
                if str(argv[0]) == "signtool":
                    raise FileNotFoundError("signtool")
                return super().__call__(argv, capture=capture)

        runner = Missing(case.env)
        _, outcome = case.run(runner)
        self.assertEqual((outcome.ok, outcome.code), (False, "tool-error"))
        self.assertEqual(leftovers(case), [])
        self.assertTrue([c for c in runner.calls if "-delstore" in c])

    def test_key_material_is_written_privately_and_removed(self) -> None:
        seen = {}

        class Spy(FakeRunner):
            def tool_certutil(self, args):
                for arg in args:
                    path = Path(arg)
                    if path.suffix == ".pfx" and path.exists():
                        seen["mode"] = stat.S_IMODE(path.stat().st_mode)
                        seen["dir"] = stat.S_IMODE(path.parent.stat().st_mode)
                        seen["scratch"] = stat.S_IMODE(path.parent.parent.stat().st_mode)
                return super().tool_certutil(args)

        case = Case(self, "windows", "nested")
        _, outcome = case.run(Spy(case.env))
        self.assertTrue(outcome.ok, outcome.detail)
        self.assertEqual((seen.get("mode"), seen.get("dir"), seen.get("scratch")), (0o600, 0o700, 0o700))
        self.assertFalse((case.root / "scratch").exists())

    def test_unchanged_files_and_a_correspondence_failure_leave_no_signed_output(self) -> None:
        case = Case(self, "windows", "nested")
        runner = case.runner(corr={"host.exe": ("FAIL", ["pe-section-changed"])})
        _, outcome = case.run(runner)
        self.assertEqual((outcome.ok, outcome.code), (False, "correspondence-failed"))
        self.assertIn("pe-section-changed", outcome.detail)
        self.assertEqual(leftovers(case), [])
        blocked = json.loads((case.root / "reports" / "windows-nested-blocked.json").read_text())
        self.assertEqual((blocked["status"], blocked["code"]), ("BLOCKED", "correspondence-failed"))
        self.assertFalse((case.root / "reports" / "SHA256SUMS").exists())


# ---------------------------------------------------------------------------
# Real runner behavior (no native tool needed)
# ---------------------------------------------------------------------------


class RunnerTests(unittest.TestCase):
    def test_subprocess_runner_resolves_references_only_at_exec_and_hides_credentials_from_children(self) -> None:
        env = {"VELES_WIN_PFX_PASSWORD": "super-secret-value", "GITHUB_TOKEN_X": "t", "PATH": os.environ["PATH"], "KEEP": "visible"}
        runner = gate.SubprocessRunner(env)
        code = "import os,sys;print(sys.argv[1]);print(sorted(k for k in os.environ if k.startswith('VELES') or 'TOKEN' in k))"
        with mock.patch.dict(os.environ, {}, clear=False):
            result = runner([sys.executable, "-I", "-c", code, EnvRef("VELES_WIN_PFX_PASSWORD")])
        self.assertEqual(result.returncode, 0)
        lines = result.output.splitlines()
        self.assertEqual(lines[0], "super-secret-value", "the reference is resolved into the child's argument")
        self.assertEqual(lines[1], "[]", "no VELES_* or *TOKEN* variable reaches the child's environment")
        self.assertEqual(gate.SubprocessRunner(env)(["definitely-not-a-real-tool-xyz"]).returncode, 127)
        self.assertEqual(runner.identify("definitely-not-a-real-tool-xyz"), {"path": None, "sha256": None})
        self.assertRegex(runner.identify(sys.executable)["sha256"], r"^[0-9a-f]{64}$")


class VerifyInputsTests(unittest.TestCase):
    def setup(self, platform="windows"):
        root = Path(tempfile.mkdtemp(prefix="gate-verify-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        pipeline = Pipeline(root, platform)
        return root, pipeline

    def verify(self, root, pipeline, **changes):
        values = dict(
            platform=pipeline.platform, a=pipeline.a, b=pipeline.b, provenance=pipeline.provenance, descriptor_path=pipeline.descriptor,
            env=ci_env(pipeline.platform), expected_commit=COMMIT, recipe=pipeline.recipe, output=root / "verified", reports=root / "rep",
        )  # fmt: skip
        values.update(changes)
        return gate.verify_unsigned_inputs(**values)

    def test_equal_builds_with_matching_provenance_pass_and_pin_build_a_unchanged(self) -> None:
        for platform in ("windows", "macos"):
            with self.subTest(platform=platform):
                root, pipeline = self.setup(platform)
                done = self.verify(root, pipeline)
                self.assertTrue(done.ok, done.detail)
                self.assertEqual(done.fragment["boundary"]["stage"], "verify-inputs")
                self.assertIs(done.fragment["boundary"]["credentials"], False)
                self.assertEqual(gate.inventory_digest(root / "verified"), gate.inventory_digest(pipeline.a), "build A is carried over byte for byte")
                ab = json.loads((root / "rep" / f"ab-{TARGET[platform]}.json").read_text())
                self.assertEqual((ab["verdict"], ab["differences"]), ("PASS", []))

    def test_unequal_builds_provenance_mismatch_credentials_and_untrusted_context_refuse(self) -> None:
        root, pipeline = self.setup()
        (pipeline.b / "package/payload/veles-native-host.exe").write_bytes(b"MZ-different")
        done = self.verify(root, pipeline)
        self.assertEqual((done.ok, done.code), (False, "ab-mismatch"))
        self.assertFalse((root / "verified").exists(), "an unequal pair produces no signable input")
        # The comparator's FAIL report is kept as evidence.
        self.assertEqual(json.loads((root / "rep" / "ab-windows-x86_64.json").read_text())["verdict"], "FAIL")
        # B has a different inventory, so an inventory-only difference (a mode change) is also refused.
        root, pipeline = self.setup()
        (pipeline.b / "package/payload/veles-native-host.exe").chmod(0o644)
        self.assertEqual(self.verify(root, pipeline).code, "ab-mismatch")
        for name, change in (
            ("credentials present", dict(env=ci_env("windows") | {"VELES_WIN_PFX_PASSWORD": "x"})),
            ("pull request", dict(env=ci_env("windows") | {"GITHUB_EVENT_NAME": "pull_request"})),
            ("branch ref", dict(env=ci_env("windows") | {"GITHUB_REF": "refs/heads/main"})),
            ("other commit", dict(expected_commit=OTHER_COMMIT)),
            ("unapproved descriptor", dict(env=ci_env("windows") | {"VELES_DESCRIPTOR_SHA256": "9" * 64})),
        ):
            with self.subTest(name):
                root, pipeline = self.setup()
                done = self.verify(root, pipeline, **change)
                self.assertFalse(done.ok)
                self.assertIn(done.code, ("credentials-present", "not-authorized"))
                self.assertFalse((root / "verified").exists())
        for name, mutate in (
            ("provenance names another commit", lambda r: r.update(source_commit=OTHER_COMMIT)),
            ("provenance names another descriptor", lambda r: r.update(descriptor_digest="9" * 64)),
            ("provenance names other inventories", lambda r: r.update(a_inventory_sha256="9" * 64, b_inventory_sha256="9" * 64)),
            ("provenance has an extra field", lambda r: r.update(waiver=True)),
        ):
            with self.subTest(name):
                root, pipeline = self.setup()
                record = json.loads(pipeline.provenance.read_text())
                mutate(record)
                pipeline.provenance.write_text(json.dumps(record))
                done = self.verify(root, pipeline)
                self.assertEqual((done.ok, done.code), (False, "provenance-invalid"))
                self.assertFalse((root / "verified").exists())
        root, pipeline = self.setup()
        pipeline.provenance.write_text("not json")
        self.assertEqual(self.verify(root, pipeline).code, "provenance-invalid")
        # Payload hashes are checked here too, before anything can be signed: a drifted payload is refused.
        root, pipeline = self.setup()
        for side in (pipeline.a, pipeline.b):
            (side / "package/payload/veles-native-host.exe").write_bytes(b"MZ-drifted")
        first = gate.package_inputs.build_inventory(pipeline.a)["inventory_sha256"]
        pipeline.provenance.write_bytes(gate.canonical_json({"schema": 1, "source_commit": COMMIT, "descriptor_digest": sha(pipeline.descriptor.read_bytes()), "a_inventory_sha256": first, "b_inventory_sha256": first, "tool_observation": tool_observation(pipeline.platform)}))
        done = self.verify(root, pipeline)
        self.assertEqual((done.ok, done.code), (False, "input-mismatch"))
        self.assertFalse((root / "verified").exists())


# ---------------------------------------------------------------------------
# Sealed carrier bytes (layout of policy.rs)
# ---------------------------------------------------------------------------


class SealedCarrierTests(unittest.TestCase):
    DESC = descriptor_bytes("macos")
    INVENTORY = b'{"entries":[],"schema":1}\n'

    def test_standalone_unsealed_carrier_is_upgraded_with_flag_and_trailing_digest(self) -> None:
        sealed = gate.seal_carrier(carrier(self.DESC, sealed=False), self.INVENTORY, self.DESC, embedded=False)
        self.assertEqual(sealed, carrier(self.DESC, sealed=True)[: -32] + hashlib.sha256(self.INVENTORY).digest())
        self.assertEqual(struct.unpack_from("<H", sealed, 10)[0], 1)
        self.assertEqual(gate.read_carrier_seal(sealed, embedded=False), sha(self.INVENTORY))
        self.assertIsNone(gate.read_carrier_seal(carrier(self.DESC, sealed=False), embedded=False))

    def test_embedded_carrier_gets_its_zeroed_slot_filled_without_resizing(self) -> None:
        image = b"MZ" + bytes(100) + carrier(self.DESC, sealed=True) + b"tail-of-image"
        sealed = gate.seal_carrier(image, self.INVENTORY, self.DESC, embedded=True)
        self.assertEqual(len(sealed), len(image))
        self.assertEqual(gate.read_carrier_seal(sealed, embedded=True), sha(self.INVENTORY))
        self.assertTrue(sealed.endswith(b"tail-of-image"))
        self.assertEqual(sealed[:102], image[:102])

    def test_an_engine_image_that_compiles_the_carrier_in_still_has_one_sealable_placeholder(self) -> None:
        # Round 3: the engine also contains the *unsealed* document it compiled in. The sealed
        # placeholder is the only document the embedded locator may find, and only it is rewritten.
        compiled_in = carrier(self.DESC, sealed=False)
        resource = carrier(self.DESC, sealed=True)
        image = b"MZ" + bytes(64) + compiled_in + bytes(64) + resource + b"tail-of-image"
        sealed = gate.seal_carrier(image, self.INVENTORY, self.DESC, embedded=True)
        self.assertEqual(len(sealed), len(image))
        self.assertEqual(gate.read_carrier_seal(sealed, embedded=True), sha(self.INVENTORY))
        start = 66 + len(compiled_in) + 64
        self.assertEqual(sealed[start : start + len(resource) - 32], resource[:-32])
        self.assertEqual(sealed[66 : 66 + len(compiled_in)], compiled_in)
        self.assertTrue(sealed.endswith(b"tail-of-image"))

    def test_refusals(self) -> None:
        def refused(data, descriptor=None, embedded=False, inventory=None):
            with self.assertRaises(gate.StageFailure) as caught:
                gate.seal_carrier(data, inventory or self.INVENTORY, descriptor or self.DESC, embedded=embedded)
            self.assertEqual(caught.exception.code, "seal-failed")

        good = carrier(self.DESC, sealed=False)
        refused(good, descriptor=descriptor_bytes("macos", commit=OTHER_COMMIT))  # carrier descriptor is not the package descriptor
        refused(b"NOTACARRIER" + good[11:])
        refused(good[:20])
        refused(good + b"trailing")  # bytes after the document
        refused(b"MZ" + good, embedded=True)  # an embedded carrier needs a producer-made placeholder
        already = gate.seal_carrier(good, self.INVENTORY, self.DESC, embedded=False)
        refused(already)  # already sealed
        nonzero = bytearray(carrier(self.DESC, sealed=True))
        nonzero[-1] = 1
        refused(bytes(nonzero))  # slot is not zero
        refused(b"MZ" + carrier(self.DESC, sealed=True) + carrier(self.DESC, sealed=True), embedded=True)  # two documents
        refused(b"MZ" + bytes(8), embedded=True)  # none
        bad_version = bytearray(good)
        bad_version[8] = 9
        refused(bytes(bad_version))
        bad_flags = bytearray(good)
        bad_flags[10] = 2
        refused(bytes(bad_flags))
        short = bytearray(good)
        short[24:32] = struct.pack("<Q", 10**6)
        refused(bytes(short))

    def test_sealed_inventory_has_the_engine_document_shape_and_rejects_surprises(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="gate-seal-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        write(tmp, "a.bin", b"A", 0o755)
        write(tmp, "b.txt", b"B")
        canonical = [
            {"path": "a.bin", "size": 1, "sha256": sha(b"A"), "executable": True},
            {"path": "b.txt", "size": 1, "sha256": sha(b"B"), "executable": False},
        ]
        view = {"a.bin": tmp / "a.bin", "b.txt": tmp / "b.txt"}
        document = json.loads(gate.sealed_inventory_bytes(canonical, view))
        self.assertEqual(set(document), {"schema", "entries"})
        self.assertEqual([e["path"] for e in document["entries"]], ["a.bin", "b.txt"])
        self.assertEqual([e["executable"] for e in document["entries"]], [True, False])
        for entry in document["entries"]:
            self.assertEqual(set(entry), {"path", "size", "sha256", "executable"})
        write(tmp, "extra.bin", b"E")
        with self.assertRaises(gate.StageFailure):
            gate.sealed_inventory_bytes(canonical, {**view, "extra.bin": tmp / "extra.bin"})
        with self.assertRaises(gate.StageFailure):
            gate.sealed_inventory_bytes(canonical, {"a.bin": tmp / "a.bin"})
        # Only the bundle seal file is allowed as a signing by-product.
        write(tmp, "X.app/Contents/_CodeSignature/CodeResources", b"s")
        by_product = {**view, "X.app/Contents/_CodeSignature/CodeResources": tmp / "X.app/Contents/_CodeSignature/CodeResources"}
        entries = json.loads(gate.sealed_inventory_bytes(canonical, by_product))["entries"]
        self.assertEqual(len(entries), 3)

    def test_payload_verification_catches_any_drift(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="gate-payload-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        windows_unsigned_tree(tmp)
        view = gate.payload_view("windows", tmp)
        inventory = gate._parse_engine_inventory((tmp / "package/inventory.json").read_bytes())
        self.assertEqual(gate.verify_payload(view, inventory), [])
        (tmp / "package/payload/veles-native-host.exe").write_bytes(b"MZ-HOST")
        self.assertTrue(gate.verify_payload(view, inventory))
        write(tmp, "package/payload/new.dll", b"x")
        self.assertTrue(any("not in the inventory" in p for p in gate.verify_payload(gate.payload_view("windows", tmp), inventory)))
        with self.assertRaises(ValueError):
            gate._parse_engine_inventory(b'{"schema":1,"entries":[],"extra":1}')


# ---------------------------------------------------------------------------
# Whole pipeline and evidence bundles
# ---------------------------------------------------------------------------


class Pipeline:
    """Runs verify-inputs -> sign-nested -> assemble -> sign-outer for one platform over the fake toolchain."""

    def __init__(self, root: Path, platform: str, **fake):
        self.root, self.platform, self.fake = root, platform, fake
        self.target = TARGET[platform]
        self.a = root / platform / "a"
        self.b = root / platform / "b"
        UNSIGNED[platform](self.a)
        shutil.copytree(self.a, self.b, symlinks=True)
        self.descriptor = self.a / PACKAGE[platform] / "descriptor.json"
        first = gate.package_inputs.build_inventory(self.a)["inventory_sha256"]
        self.provenance = root / platform / "provenance.json"
        self.provenance.write_bytes(
            gate.canonical_json({"schema": 1, "source_commit": COMMIT, "descriptor_digest": sha(self.descriptor.read_bytes()), "a_inventory_sha256": first, "b_inventory_sha256": first,
                                 "tool_observation": tool_observation(platform)})
        )
        self.recipe = gate.recipe_digest(gate.INSTALLER_DIR, platform)
        self.reports_root = root / platform / "reports"
        self.failed: str | None = None

    def runner(self, env, app):
        return FakeRunner(env, app_source=app, **self.fake)

    def run(self) -> "Pipeline":
        p, root = self.platform, self.root / self.platform
        env_ci = ci_env(p) | ({"NSIS_PATH": "/fake/makensis.exe"} if p == "windows" else {})
        verified = gate.verify_unsigned_inputs(
            platform=p, a=self.a, b=self.b, provenance=self.provenance, descriptor_path=self.descriptor, env=ci_env(p),
            expected_commit=COMMIT, recipe=self.recipe, output=root / "verified", reports=root / "rep-verify",
        )  # fmt: skip
        if not verified.ok:
            self.failed = f"verify-inputs: {verified.code} {verified.detail}"
            return self
        env = good_env(p)
        nested_req = gate.SignRequest(p, "nested", root / "verified", root / "nested", root / "scratch-nested", root / "rep-nested", COMMIT, root / "verified" / PACKAGE[p] / "descriptor.json", verified.fragment["boundary"], self.recipe)
        nested = gate.run_stage(nested_req, env, self.runner(env, None))
        if not nested.ok:
            self.failed = f"sign-nested: {nested.code} {nested.detail}"
            return self
        asm_req = gate.SignRequest(p, "outer", root / "nested", root / "assembled", root / "scratch-asm", root / "rep-assemble", COMMIT, root / "nested" / PACKAGE[p] / "descriptor.json", nested.fragment["boundary"], self.recipe)
        assembled = gate.assemble(asm_req, env_ci, self.runner(env_ci, None))
        if not assembled.ok:
            self.failed = f"assemble: {assembled.code} {assembled.detail}"
            return self
        outer_req = gate.SignRequest(p, "outer", root / "assembled", root / "outer", root / "scratch-outer", root / "rep-outer", COMMIT, root / "assembled" / PACKAGE[p] / "descriptor.json", assembled.fragment["boundary"], self.recipe)
        outer = gate.run_stage(outer_req, env, self.runner(env, root / "outer" / APP))
        if not outer.ok:
            self.failed = f"sign-outer: {outer.code} {outer.detail}"
            return self
        self.reports_root.mkdir(parents=True)
        for directory in ("rep-verify", "rep-nested", "rep-assemble", "rep-outer"):
            for path in (root / directory).glob("*.json"):
                shutil.copy2(path, self.reports_root / path.name)
        return self


def build_pipeline_bundle(root: Path, **fake) -> tuple[Path, str]:
    pipelines = [Pipeline(root, platform, **fake).run() for platform in ("windows", "macos")]
    failed = [p.failed for p in pipelines if p.failed]
    if failed:
        raise AssertionError("pipeline did not complete: " + "; ".join(failed))
    merged = root / "reports-merged"
    merged.mkdir()
    for pipeline in pipelines:
        for path in pipeline.reports_root.glob("*.json"):
            shutil.copy2(path, merged / path.name)
    bundle = root / "bundle"
    env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/tags/1.2.3", "GITHUB_SHA": COMMIT, "VELES_SIGNING_ENVIRONMENT": "native-release"}
    with mock.patch.dict(os.environ, env, clear=False):
        os.environ.pop("GITHUB_HEAD_REF", None)
        os.environ.pop("GITHUB_BASE_REF", None)
        code = gate.main(
            ["bundle", "--reports-root", str(merged), "--bundle", str(bundle), "--commit", COMMIT,
             "--descriptor-windows", str(pipelines[0].descriptor), "--descriptor-macos", str(pipelines[1].descriptor)]
        )  # fmt: skip
    assert code == 0
    expected = gate.descriptor_set_digest({"windows-x86_64": sha(pipelines[0].descriptor.read_bytes()), "macos-arm64": sha(pipelines[1].descriptor.read_bytes())})
    return bundle, expected


def edit_report(bundle: Path, rel: str, mutate) -> None:
    """Mutate one indexed report and keep the evidence index consistent, so only the intended defect remains."""
    path = bundle / rel
    value = json.loads(path.read_text())
    mutate(value)
    data = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(data)
    edit_evidence(bundle, lambda e: e["files"].__setitem__(rel, sha(data)))


def edit_evidence(bundle: Path, mutate) -> None:
    path = bundle / "evidence.json"
    value = json.loads(path.read_text())
    mutate(value)
    path.write_bytes(json.dumps(value, sort_keys=True).encode())


def component(report: dict, role: str) -> dict:
    return next(c for c in report["components"] if c["role"] == role)


class ToolIdentityBindingTests(unittest.TestCase):
    """F8: the locked compiler/SDK identities are enforced where production eligibility is decided.

    ``release_config.validate_tools`` used to be called only by its own tests. These tests drive the gate with
    provenance carrying real observations and prove that an unresolved, missing, mismatched or foreign
    inventory blocks before any key is used, and that the bundle validator re-checks what was recorded.
    """

    def setUp(self) -> None:
        self.helper = VerifyInputsTests()
        self.helper.addCleanup = self.addCleanup

    def verify(self, platform: str, mutate=None, *, lock=None):
        root, pipeline = self.helper.setup(platform)
        record = json.loads(pipeline.provenance.read_text())
        if mutate:
            mutate(record)
        pipeline.provenance.write_bytes(gate.canonical_json(record))
        with mock.patch.object(gate, "tools_lock", lambda: lock or RESOLVED_LOCK, create=True):
            return self.helper.verify(root, pipeline), root

    def test_matching_observations_pass_and_are_published_with_the_inputs(self) -> None:
        for platform in ("windows", "macos"):
            with self.subTest(platform=platform):
                done, root = self.verify(platform)
                self.assertTrue(done.ok, done.detail)
                name = done.fragment["tool_observation"]
                self.assertEqual(name, f"tools-{TARGET[platform]}.json")
                self.assertEqual(json.loads((root / "rep" / name).read_text()), tool_observation(platform))

    def test_a_provenance_without_an_observation_is_refused(self) -> None:
        done, _ = self.verify("macos", lambda r: r.pop("tool_observation"))
        self.assertEqual((done.ok, done.code), (False, "provenance-invalid"))

    def test_the_real_windows_lock_blocks_every_candidate_because_its_identities_are_unresolved(self) -> None:
        done, root = self.verify("windows", lambda r: r.update(tool_observation=tool_observation("windows", RESOLVED_LOCK)), lock=REAL_LOCK)
        self.assertEqual((done.ok, done.code), (False, "tools-unverified"))
        self.assertIn("unresolved", done.detail)
        self.assertFalse((root / "verified").exists(), "no build-A copy is released for a blocked candidate")

    def test_the_real_macos_lock_accepts_exactly_its_pinned_identities(self) -> None:
        done, _ = self.verify("macos", lambda r: r.update(tool_observation=tool_observation("macos", REAL_LOCK)), lock=REAL_LOCK)
        self.assertTrue(done.ok, done.detail)

    def test_mismatched_missing_and_foreign_observations_are_refused(self) -> None:
        def edit(change):
            return lambda r: change(r["tool_observation"])

        for label, platform, change in (
            ("rust channel", "macos", edit(lambda o: o["tools"]["rust"].update(channel="1.99.0"))),
            ("xcode build", "macos", edit(lambda o: o["tools"]["xcode"].update(build="17F000"))),
            ("sdk version", "macos", edit(lambda o: o["tools"]["macos_sdk"].update(version="macosx26.4"))),
            ("tool missing", "macos", edit(lambda o: o["tools"].pop("xcode"))),
            ("field missing", "macos", edit(lambda o: o["tools"]["rust"].pop("profile"))),
            ("cl version", "windows", edit(lambda o: o["tools"]["msvc"].update(cl_version="19.00.00000"))),
            ("sdk digest", "windows", edit(lambda o: o["tools"]["windows_sdk"].update(windows_sdk_sha256="c" * 64))),
            ("other target", "macos", edit(lambda o: o.update(target="windows-x86_64"))),
            ("not an object", "macos", lambda r: r.update(tool_observation=[])),
        ):
            with self.subTest(label):
                done, _ = self.verify(platform, change)
                self.assertEqual((done.ok, done.code), (False, "tools-unverified"), done.detail)

    def test_the_bundle_validator_rechecks_the_recorded_observation(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="gate-tools-bundle-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        golden, expected = build_pipeline_bundle(root)

        def fresh():
            work = Path(tempfile.mkdtemp(prefix="gate-tools-copy-"))
            self.addCleanup(shutil.rmtree, work, ignore_errors=True)
            bundle = work / "bundle"
            shutil.copytree(golden, bundle)
            return bundle

        bundle = fresh()
        self.assertEqual(gate.validate(bundle, COMMIT, expected), [])
        rel = json.loads((bundle / "reports/macos-arm64.json").read_text())["unsigned"]["tool_observation"]
        self.assertEqual(rel, "reports/tools-macos-arm64.json")
        with self.subTest("tampered recorded observation"):
            bundle = fresh()
            edit_report(bundle, rel, lambda o: o["tools"]["xcode"].update(build="17F000"))
            self.assertTrue(any("tool" in e and "xcode.build" in e for e in gate.validate(bundle, COMMIT, expected)))
        with self.subTest("the pointer is removed"):
            bundle = fresh()
            edit_report(bundle, "reports/macos-arm64.json", lambda r: r["unsigned"].pop("tool_observation"))
            self.assertTrue(any("tool observation" in e for e in gate.validate(bundle, COMMIT, expected)))
        with self.subTest("the repository lock cannot make a Windows bundle eligible"):
            bundle = fresh()
            with mock.patch.object(gate, "tools_lock", lambda: REAL_LOCK, create=True):
                errors = gate.validate(bundle, COMMIT, expected)
            self.assertTrue(any("windows-x86_64" in e and "unresolved" in e for e in errors), errors)


class ValidateBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="gate-bundle-"))
        cls.golden, cls.expected = build_pipeline_bundle(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="gate-bundle-copy-"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.bundle = self.work / "bundle"
        shutil.copytree(self.golden, self.bundle)

    def validate(self, commit: str = COMMIT, descriptor: str | None = None) -> list[str]:
        return gate.validate(self.bundle, commit, descriptor or self.expected)

    def report(self, target: str) -> dict:
        return json.loads((self.bundle / f"reports/{target}.json").read_text())

    def assertBlocked(self, *needles: str) -> list[str]:
        errors = self.validate()
        self.assertTrue(errors, "the defect was not detected")
        text = "\n".join(errors)
        for needle in needles:
            self.assertIn(needle, text)
        return errors

    def test_complete_pipeline_bundle_is_eligible(self) -> None:
        self.assertEqual(self.validate(), [])
        evidence = json.loads((self.bundle / "evidence.json").read_text())
        self.assertEqual(set(evidence["targets"]), {"windows-x86_64", "macos-arm64"})
        self.assertEqual(evidence["context"]["environment"], "native-release")
        # The CLI computes the same single approved value from the two per-target digests.
        windows, macos = (sha(descriptor_bytes(p)) for p in ("windows", "macos"))
        printed = subprocess.run([sys.executable, "-I", str(HERE / "release_gate.py"), "descriptor-set-digest", "--windows-sha256", windows, "--macos-sha256", macos], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(printed, self.expected)
        # The shipped CLI reads the REAL lock, whose Windows MSVC/SDK identities are unresolved: even a complete,
        # internally consistent bundle is not ELIGIBLE until the owner records those identities (F8).
        done = subprocess.run([sys.executable, "-I", str(HERE / "release_gate.py"), "validate", "--bundle", str(self.bundle), "--commit", COMMIT, "--descriptor-digest", self.expected], capture_output=True, text=True)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertNotIn("ELIGIBLE\n", done.stdout.replace("BLOCKED", ""))
        self.assertTrue(all("windows-x86_64" in line and "tool observation" in line for line in done.stdout.splitlines() if line.startswith("BLOCKED")), done.stdout)
        self.assertIn("unresolved", done.stdout)

    def test_mismatched_commit_target_policy_or_payload_fails(self) -> None:
        # -- commit
        self.assertTrue([e for e in self.validate(OTHER_COMMIT) if "commit" in e])
        edit_evidence(self.bundle, lambda e: e.update(source_commit=OTHER_COMMIT))
        self.assertTrue([e for e in self.validate() if "commit" in e])
        edit_evidence(self.bundle, lambda e: e.update(source_commit=COMMIT))
        edit_report(self.bundle, "reports/macos-arm64.json", lambda r: r.update(source_commit=OTHER_COMMIT))
        self.assertTrue([e for e in self.validate() if e.startswith("macos-arm64") and "source_commit" in e])
        self.setUp()
        # -- descriptor / approval
        self.assertTrue([e for e in self.validate(descriptor="9" * 64) if "descriptor" in e])
        edit_report(self.bundle, "reports/windows-x86_64.json", lambda r: r.update(descriptor_digest="9" * 64))
        self.assertBlocked("windows-x86_64", "descriptor")
        self.setUp()
        # -- target
        edit_report(self.bundle, "reports/windows-x86_64.json", lambda r: r.update(target="macos-arm64"))
        self.assertBlocked("names target")
        self.setUp()
        edit_evidence(self.bundle, lambda e: e["targets"].pop("macos-arm64"))
        self.assertBlocked("exactly")
        self.setUp()
        edit_evidence(self.bundle, lambda e: e["targets"].__setitem__("linux-x86_64", e["targets"]["macos-arm64"]))
        self.assertBlocked("exactly")
        self.setUp()
        # -- publisher policy: the evidence policy and the signatures must agree, and be bound by digest
        edit_report(self.bundle, "reports/windows-x86_64.json", lambda r: r["policy"].update(publisher="Impostor Ltd"))
        self.assertBlocked("report policy digest")
        self.setUp()
        edit_report(self.bundle, "reports/windows-x86_64.json", lambda r: component(r, "host")["signature"].update(identity="Impostor Ltd"))
        self.assertBlocked("component host", "approved publisher")
        self.setUp()
        edit_report(self.bundle, "reports/macos-arm64.json", lambda r: component(r, "engine")["signature"].update(team_id="EVIL000000"))
        self.assertBlocked("component engine", "approved team")
        self.setUp()
        edit_report(self.bundle, "reports/macos-arm64.json", lambda r: component(r, "host")["signature"].update(entitlements_sha256="9" * 64))
        self.assertBlocked("entitlements")
        self.setUp()
        edit_report(self.bundle, "reports/macos-arm64.json", lambda r: component(r, "installer")["signature"].update(hardened_runtime=False))
        self.assertBlocked("hardened runtime")
        self.setUp()
        edit_evidence(self.bundle, lambda e: e.update(policy_digest="9" * 64))
        self.assertBlocked("policy digest")
        self.setUp()
        # -- payload: bytes, index and signing effect
        victim = self.bundle / "reports/corr-windows-host.json"
        victim.write_bytes(victim.read_bytes() + b" ")
        self.assertBlocked("corr-windows-host.json", "sha256")
        self.setUp()
        (self.bundle / "reports/corr-windows-host.json").unlink()
        self.assertBlocked("missing")
        self.setUp()
        (self.bundle / "reports/stray.json").write_text("{}")
        self.assertBlocked("unindexed")
        self.setUp()
        edit_report(self.bundle, "reports/windows-x86_64.json", lambda r: component(r, "host").update(signed_sha256=component(r, "host")["unsigned_sha256"]))
        self.assertBlocked("did not change")
        self.setUp()
        edit_report(self.bundle, "reports/windows-x86_64.json", lambda r: component(r, "host").update(signed_sha256="9" * 64))
        self.assertBlocked("not about this component's bytes")
        self.setUp()
        edit_report(self.bundle, "reports/windows-x86_64.json", lambda r: r["components"].pop(0))
        self.assertBlocked("required role host")
        self.setUp()
        edit_report(self.bundle, "reports/windows-x86_64.json", lambda r: r["containers"][0].update(nested_hashes_match=False))
        self.assertBlocked("nested hashes")
        self.setUp()
        edit_report(self.bundle, "reports/windows-x86_64.json", lambda r: r["containers"][0].update(post_sign_sha256=r["containers"][0]["pre_sign_sha256"]))
        self.assertBlocked("container")
        self.setUp()
        edit_report(self.bundle, "reports/macos-arm64.json", lambda r: r.update(unknown_waiver=True))
        self.assertBlocked("unknown fields")

    def test_ab_equality_boundaries_and_seal_are_required(self) -> None:
        edit_report(self.bundle, "reports/windows-x86_64.json", lambda r: r["unsigned"].update(b_inventory_sha256="9" * 64))
        self.assertBlocked("A/B")
        # Even with a comparison report that agrees with the claimed (unequal) inventories, unequal builds never pass.
        edit_report(self.bundle, "reports/ab-windows-x86_64.json", lambda r: r["input_hashes"].update(second="9" * 64))
        self.assertBlocked("A/B unsigned inventories are not equal")
        self.setUp()
        edit_report(self.bundle, "reports/windows-x86_64.json", lambda r: r["unsigned"].update(chosen="B"))
        self.assertBlocked("build A")
        self.setUp()
        edit_report(self.bundle, "reports/ab-windows-x86_64.json", lambda r: r.update(verdict="FAIL", differences=[{"code": "payload-hash"}]))
        self.assertBlocked("A/B equality report")
        self.setUp()
        edit_report(self.bundle, "reports/macos-arm64.json", lambda r: r["boundaries"][2].update(credentials=True))
        self.assertBlocked("credential state")
        self.setUp()
        edit_report(self.bundle, "reports/macos-arm64.json", lambda r: r["boundaries"][1].update(credentials=False))
        self.assertBlocked("credential state")
        self.setUp()
        edit_report(self.bundle, "reports/macos-arm64.json", lambda r: r["boundaries"][2].update(input_inventory_sha256="9" * 64))
        self.assertBlocked("transfer into assemble")
        self.setUp()
        edit_report(self.bundle, "reports/windows-x86_64.json", lambda r: r["boundaries"][0].update(input_inventory_sha256="9" * 64))
        self.assertBlocked("other than the chosen build A")
        self.setUp()
        edit_report(self.bundle, "reports/windows-x86_64.json", lambda r: r.update(boundaries=r["boundaries"][1:]))
        self.assertBlocked("boundary stages")
        self.setUp()
        edit_report(self.bundle, "reports/windows-x86_64.json", lambda r: r["boundaries"][1].update(recipe_digest="9" * 64))
        self.assertBlocked("another recipe")
        # The sealed-carrier evidence is required and may not be a blocked marker.
        for seal in ({"status": "blocked", "reason": "no carrier"}, {}, {"status": "sealed", "sealed_inventory_sha256": "x", "carrier_sha256": "y"}):
            self.setUp()
            edit_report(self.bundle, "reports/windows-x86_64.json", lambda r, seal=seal: r.update(seal=seal))
            self.assertBlocked("seal")
        # Tool identities are recorded with a real digest.
        for mutate in (lambda t: t.pop("signtool"), lambda t: t["signtool"].update(sha256=None), lambda t: t["signtool"].update(path=None)):
            self.setUp()
            edit_report(self.bundle, "reports/windows-x86_64.json", lambda r, mutate=mutate: mutate(r["tools"]))
            self.assertBlocked("tool identity for signtool")

    def test_context_must_be_a_protected_tag_dispatch(self) -> None:
        for field, value in (("event_name", "pull_request"), ("pull_request", True), ("environment", "production"), ("ref", "refs/heads/main"), ("sha", OTHER_COMMIT)):
            with self.subTest(field=field):
                self.setUp()
                edit_evidence(self.bundle, lambda e, field=field, value=value: e["context"].update({field: value}))
                self.assertBlocked()
        self.setUp()
        edit_evidence(self.bundle, lambda e: e.pop("context"))
        self.assertBlocked("missing")

    def test_unnotarized_unstapled_or_correspondence_failure_blocks(self) -> None:
        macos = "reports/macos-arm64.json"

        def dmg(r):
            return r["containers"][0]

        cases = {
            "notarization rejected": (lambda r: dmg(r)["notarization"].update(status="Invalid"), "not notarized"),
            "notarization absent": (lambda r: dmg(r).pop("notarization"), "not notarized"),
            "notarization has no submission": (lambda r: dmg(r)["notarization"].update(submission_id=""), "not notarized"),
            "unstapled": (lambda r: dmg(r)["staple"].update(validated=False), "not stapled"),
            "staple absent": (lambda r: dmg(r).pop("staple"), "not stapled"),
            "gatekeeper rejected": (lambda r: dmg(r)["gatekeeper"].update(accepted=False), "Gatekeeper"),
            "inner app gatekeeper rejected": (lambda r: dmg(r)["inner_app_gatekeeper"].update(accepted=False), "inner_app_gatekeeper"),
            "contents not verified": (lambda r: dmg(r).update(contents_match=False), "contents"),
            "container dropped": (lambda r: r.update(containers=[]), "container dmg"),
            "container unsigned": (lambda r: dmg(r)["signature"].update(verified=False), "not verified"),
        }
        for name, (mutate, needle) in cases.items():
            with self.subTest(name):
                self.setUp()
                edit_report(self.bundle, macos, mutate)
                self.assertBlocked("macos-arm64", needle)
        # A failing Task 9 correspondence report blocks, whatever the target report claims, and each code is named.
        for rel, label in (
            ("reports/corr-macos-host.json", "component host"),
            ("reports/corr-macos-installer-bundle.json", "component installer-bundle"),
            ("reports/corr-macos-dmg-contents.json", "dmg"),
            ("reports/corr-windows-engine.json", "component engine"),
            ("reports/corr-windows-setup.json", "nsis-setup"),
        ):
            with self.subTest(report=rel):
                self.setUp()
                edit_report(self.bundle, rel, lambda r: r.update(verdict="FAIL", differences=[{"code": "pe-section-changed"}]))
                errors = self.assertBlocked("pe-section-changed")
                self.assertTrue(all(("windows" in e) == ("windows" in rel) for e in errors), errors)
                self.assertTrue(any(label in e for e in errors), errors)
        for rel in ("reports/corr-macos-host.json", "reports/corr-windows-host.json"):
            with self.subTest(report=rel, what="missing verdict"):
                self.setUp()
                edit_report(self.bundle, rel, lambda r: r.pop("verdict"))
                self.assertBlocked("correspondence")
        self.setUp()
        edit_report(self.bundle, "reports/corr-macos-host.json", lambda r: r.update(kind="pe"))
        self.assertBlocked("kind")
        self.setUp()
        edit_report(self.bundle, "reports/corr-macos-host.json", lambda r: r.update(entitlement_policy={"source": "inherited", "file_sha256": None}))
        self.assertBlocked("declared entitlements policy")
        # The comparator's own report outranks the target report's claim.
        self.setUp()
        edit_report(self.bundle, "reports/corr-macos-installer.json", lambda r: r.update(verdict="FAIL", differences=[]))
        report = self.report("macos-arm64")
        self.assertEqual(component(report, "installer")["correspondence"]["verdict"], "PASS")
        self.assertBlocked("component installer")

    def test_macos_host_der_entitlements_block_is_an_ordinary_correspondence_failure(self) -> None:
        # What a real signed entitled host produces today: the comparator refuses the DER slot.
        code = "macho-der-entitlements-unsupported"
        edit_report(self.bundle, "reports/corr-macos-host.json", lambda r: r.update(verdict="FAIL", differences=[{"code": code}]))
        errors = self.validate()
        text = "\n".join(errors)
        self.assertIn("macos-arm64", text)
        self.assertIn("component host", text)
        self.assertIn(code, text)
        self.assertFalse([e for e in errors if e.startswith("windows")], "only the macOS host is blocked")
        # The same blocker at stage level is an ordinary correspondence failure that leaves nothing behind.
        case = Case(self, "macos", "nested")
        runner = case.runner(corr={"veles-native-host": ("FAIL", [code])})
        _, outcome = case.run(runner)
        self.assertEqual((outcome.ok, outcome.code), (False, "correspondence-failed"))
        self.assertIn(code, outcome.detail)
        self.assertEqual(leftovers(case), [])
        # No special case for that code exists in the gate, the signers or the workflow.
        for path in (HERE / "release_gate.py", HERE / "sign-macos.sh", HERE / "sign-windows.ps1", WORKFLOW):
            self.assertNotIn("der-entitlements", path.read_text(), path.name)
            self.assertNotRegex(path.read_text(), r"(?i)\bDER\b.*(skip|ignore|waive|allow)", path.name)

    def test_payload_and_report_integrity_failures(self) -> None:
        (self.bundle / "evidence.json").write_text("not json")
        self.assertEqual(len(self.validate()), 1)
        self.setUp()
        (self.bundle / "evidence.json").write_text("[]")
        self.assertBlocked("not an object")
        self.setUp()
        shutil.rmtree(self.bundle)
        self.assertBlocked("unreadable")
        self.setUp()
        edit_evidence(self.bundle, lambda e: e["files"].__setitem__("../outside.json", "0" * 64))
        self.assertTrue(self.validate())
        self.setUp()
        edit_evidence(self.bundle, lambda e: e.update(waiver=True))
        self.assertBlocked("no waivers")
        self.setUp()
        evidence_path = self.bundle / "evidence.json"
        evidence_path.write_text(evidence_path.read_text().replace('"schema":1', '"schema":1,"schema":1', 1))
        self.assertBlocked("unreadable")  # duplicate keys are rejected, not last-wins

    def test_every_stage_fragment_is_accepted_as_the_next_jobs_boundary_by_the_cli_loader(self) -> None:
        # The workflow hands each job the previous job's fragment FILE; the CLI must unwrap its boundary record.
        import argparse

        reports = self.root / "windows" / "reports"
        chain = (("windows-verify-inputs.json", "verify-inputs"), ("windows-nested.json", "sign-nested"), ("windows-assemble.json", "assemble"))
        for name, stage in chain:
            args = argparse.Namespace(inputs=Path("i"), output=Path("o"), scratch=Path("s"), reports=Path("r"), descriptor=Path("d"), boundary=reports / name, commit=COMMIT, recipe_digest="a" * 64)
            request = gate._request(args, "windows", "nested")
            self.assertEqual(set(request.boundary), gate.BOUNDARY_KEYS, name)
            self.assertEqual(request.boundary["stage"], stage)

    def test_symlinked_evidence_is_refused(self) -> None:
        target = self.bundle / "reports/corr-windows-host.json"
        replacement = self.work / "elsewhere.json"
        replacement.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(replacement)
        self.assertBlocked("corr-windows-host.json")


# ---------------------------------------------------------------------------
# Workflow and signer shims
# ---------------------------------------------------------------------------


def split_jobs(text: str) -> dict[str, str]:
    head, _, body = text.partition("\njobs:\n")
    jobs, current, lines = {}, None, []
    for line in body.splitlines():
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if match:
            if current:
                jobs[current] = "\n".join(lines)
            current, lines = match.group(1), []
        elif current:
            lines.append(line)
    if current:
        jobs[current] = "\n".join(lines)
    jobs["__head__"] = head
    return jobs


def run_blocks(job: str) -> str:
    blocks, capture, indent = [], False, 0
    for line in job.splitlines():
        if capture:
            if line.strip() and len(line) - len(line.lstrip()) <= indent:
                capture = False
            else:
                blocks.append(line)
                continue
        match = re.match(r"^(\s*)(?:-\s+)?run:\s*(.*)$", line)
        if match:
            indent = len(match.group(1)) + (2 if line.lstrip().startswith("-") else 0)
            if match.group(2) in ("|", ">", "|-", ">-"):
                capture = True
            else:
                blocks.append(match.group(2))
    return "\n".join(blocks)


SIGN_JOBS = ("sign-windows-nested", "sign-windows-outer", "sign-macos-nested", "sign-macos-outer")


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text()
        cls.jobs = split_jobs(cls.text)
        cls.head = cls.jobs.pop("__head__")

    def test_triggers_are_dispatch_only_and_permissions_are_read_only(self) -> None:
        on = self.head.split("on:", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("workflow_dispatch", on)
        for forbidden in ("pull_request", "pull_request_target", "push", "schedule", "workflow_run", "issue_comment"):
            self.assertNotRegex(on, rf"(?m)^\s*{forbidden}\s*:", forbidden)
        self.assertRegex(self.head, r"(?m)^permissions:\s*\n\s+contents:\s*read\s*$")
        self.assertNotRegex(self.text, r"(?m)^\s*(contents|id-token|attestations|actions|packages|pull-requests):\s*write")

    def test_credentials_exist_only_in_protected_environment_jobs(self) -> None:
        credentialed = {name for name, body in self.jobs.items() if "secrets." in body}
        self.assertEqual(credentialed, set(SIGN_JOBS))
        for name, body in self.jobs.items():
            has_env = re.search(r"(?m)^    environment:\s*native-release\s*$", body) is not None
            self.assertEqual(has_env, name in SIGN_JOBS, name)
        self.assertNotIn("secrets.", self.head)
        # Secrets are mapped by a step-level env: on the single step that runs the signer, nowhere else.
        for name in SIGN_JOBS:
            steps = re.split(r"(?m)^      - ", self.jobs[name])
            holders = [s for s in steps if "secrets." in s]
            self.assertEqual(len(holders), 1, name)
            self.assertIn("sign-", holders[0])
        # The env-level (job-level) block never carries a secret.
        for name in SIGN_JOBS:
            job_env = self.jobs[name].split("    steps:", 1)[0]
            self.assertNotIn("secrets.", job_env, name)
        # The descriptor approval is a protected-environment variable consumed only by the credentialed jobs and the gate.
        approved = {name for name, body in self.jobs.items() if "vars.VELES_APPROVED_DESCRIPTOR_SHA256_" in body}
        self.assertEqual(approved, set(SIGN_JOBS) | {"gate"})

    def test_no_secret_or_untrusted_expression_reaches_a_shell(self) -> None:
        for name, body in self.jobs.items():
            blocks = run_blocks(body)
            self.assertNotRegex(blocks, r"\$\{\{\s*secrets\.", name)
            self.assertNotRegex(blocks, r"\$\{\{\s*(inputs|github\.event|github\.head_ref|vars)\.", f"{name}: untrusted expression in run:")
        self.assertNotRegex(self.text, r"(?i)toJSON\(\s*secrets")
        self.assertNotRegex(self.text, r"(?i)secrets:\s*inherit")

    def test_signing_jobs_never_compile_or_build_and_assembly_holds_no_credentials(self) -> None:
        for name in (*SIGN_JOBS, "verify-inputs", "gate"):
            body = run_blocks(self.jobs[name])
            for token in ("cargo", "gradlew", "npm ", "swift build", "swift test", "xcodebuild", "makensis", "hdiutil create", "rustup", "tauri", "pip install"):
                self.assertNotIn(token, body, f"{name} must not compile source ({token})")
        for name in ("assemble-windows", "assemble-macos"):
            body = self.jobs[name]
            self.assertNotIn("secrets.", body)
            self.assertNotIn("environment:", body)
            self.assertNotIn("VELES_WIN_", body)
            self.assertNotIn("VELES_MAC_", body)
            self.assertNotIn("VELES_NOTARY_", body)
            self.assertNotIn("VELES_SIGNING_ENVIRONMENT", body)
            self.assertNotIn("VELES_APPROVED", body)
            self.assertTrue(" assemble --platform " in run_blocks(body), name)
        self.assertTrue("NSIS_PATH" in self.jobs["assemble-windows"])
        self.assertTrue("provision-nsis.ps1" in self.jobs["assemble-windows"])

    def test_job_graph_is_verify_sign_assemble_sign_gate(self) -> None:
        def needs(name):
            match = re.search(r"(?m)^    needs:\s*(\[.*?\]|\S+)\s*$", self.jobs[name])
            return set(re.findall(r"[A-Za-z0-9_-]+", match.group(1))) if match else set()

        self.assertEqual(needs("verify-inputs"), set())
        for platform in ("windows", "macos"):
            self.assertEqual(needs(f"sign-{platform}-nested"), {"verify-inputs"})
            self.assertEqual(needs(f"assemble-{platform}"), {f"sign-{platform}-nested"})
            self.assertEqual(needs(f"sign-{platform}-outer"), {f"assemble-{platform}"})
        self.assertEqual(needs("gate"), {"verify-inputs", "sign-windows-outer", "sign-macos-outer"})
        self.assertEqual(set(self.jobs), {"verify-inputs", *SIGN_JOBS, "assemble-windows", "assemble-macos", "gate"})
        self.assertNotIn("environment:", self.jobs["gate"])
        self.assertNotIn("secrets.", self.jobs["gate"])

    def test_gate_decides_with_validate_and_publishes_nothing(self) -> None:
        gate_job = run_blocks(self.jobs["gate"])
        for command in (" validate ", "descriptor-set-digest", " bundle "):
            self.assertTrue(command in gate_job, command)
        # A blocked candidate fails the job: the gate may not swallow a failure of an earlier job.
        self.assertRegex(self.jobs["gate"], r"needs\.[A-Za-z-]+\.result")
        for token in ("gh release", "action-gh-release", "attest-build-provenance", "gh api", "git push", "git tag"):
            self.assertNotIn(token, self.text, token)
        # Best-effort `|| true` exists only in the always-run key-material cleanup steps, never on a signing path.
        for step in re.split(r"(?m)^      - ", self.text):
            if "|| true" in step:
                self.assertTrue("interrupted" in step.splitlines()[0], step.splitlines()[0])

    def test_actions_are_pinned_and_retention_is_bounded(self) -> None:
        for match in re.finditer(r"(?m)^\s*-?\s*uses:\s*(\S+)(.*)$", self.text):
            self.assertRegex(match.group(1), r"@[0-9a-f]{40}$")
            self.assertRegex(match.group(2), r"#\s*v\d+")
        uploads = self.text.count("actions/upload-artifact@")
        self.assertGreaterEqual(uploads, 8)
        self.assertEqual(self.text.count("retention-days:"), uploads)
        self.assertNotRegex(self.text, r"(?m)^\s*retention-days:\s*(?:[3-9]\d|\d{3,})\s*$")

    def test_workflow_paths_match_the_gate_constants(self) -> None:
        # The workflow spells two in-tree paths literally; they must be the ones the gate expects.
        self.assertTrue(f"{gate.WIN_PACKAGE}/descriptor.json" in self.text, "windows descriptor path")
        self.assertTrue(f"{gate.MAC_PACKAGE}/descriptor.json" in self.text, "macos descriptor path")
        self.assertTrue("release_gate.py" in self.text)
        for command in ("verify-inputs", "recipe-digest", "bundle", "validate"):
            self.assertTrue(f" {command} " in self.text, command)


class ShimContractTests(unittest.TestCase):
    def test_shims_are_thin_quiet_and_fail_closed(self) -> None:
        sh = (HERE / "sign-macos.sh").read_text()
        ps = (HERE / "sign-windows.ps1").read_text()
        self.assertTrue(os.access(HERE / "sign-macos.sh", os.X_OK))
        self.assertIn("set -euo pipefail", sh)
        self.assertNotRegex(sh, r"(?m)^\s*set\s+-[a-z]*x")
        self.assertIn("$ErrorActionPreference = 'Stop'", ps)
        for text in (sh, ps):
            self.assertNotRegex(text, r"(?i)\b(eval|Invoke-Expression|iex)\b")
            self.assertNotRegex(text, r"(?i)(echo|printf|Write-Host|Write-Output|Write-Verbose)[^\n]*(\$\{?VELES_|\$env:VELES_)")
            self.assertNotRegex(text, r"(?i)(curl|wget|Invoke-WebRequest|Invoke-RestMethod)")
            self.assertIn("release_gate.py", text)
            self.assertIn(" sign --platform ", text)
            self.assertNotIn("--unsigned-ok", text)
            self.assertNotRegex(text, r"(?i)(\|\|\s*true|-ErrorAction\s+SilentlyContinue)")

    def test_macos_shim_exits_nonzero_and_creates_nothing_without_credentials(self) -> None:
        case = Case(self, "macos", "nested")
        boundary = case.root / "boundary.json"
        boundary.write_text(json.dumps(case.boundary))
        env = {"PATH": os.environ["PATH"], **{k: v for k, v in good_env("macos").items() if k not in gate.CREDENTIALS[("macos", "nested")] or k in ("VELES_MAC_IDENTITY", "VELES_MAC_TEAM_ID")}}
        before = sorted(p.name for p in case.root.iterdir())
        result = subprocess.run(
            ["bash", str(HERE / "sign-macos.sh"), "--stage", "nested", "--inputs", str(case.inputs), "--output", str(case.root / "out"),
             "--scratch", str(case.root / "scratch"), "--reports", str(case.root / "reports"), "--descriptor", str(case.descriptor),
             "--boundary", str(boundary), "--commit", COMMIT, "--recipe-digest", gate.recipe_digest(gate.INSTALLER_DIR, "macos")],
            env=env, capture_output=True, text=True, timeout=60,
        )  # fmt: skip
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("credential-missing", result.stdout + result.stderr)
        self.assertEqual(sorted(p.name for p in case.root.iterdir()), before)

    def test_shim_accepts_a_previous_jobs_fragment_as_the_boundary(self) -> None:
        # The boundary of the previous job is read from its fragment file, so no JSON editing sits between jobs.
        case = Case(self, "macos", "nested")
        fragment = case.root / "fragment.json"
        fragment.write_text(json.dumps({"platform": "macos", "stage": "verify-inputs", "boundary": case.boundary}))
        env = {"PATH": os.environ["PATH"], **{k: v for k, v in good_env("macos").items() if k not in gate.CREDENTIALS[("macos", "nested")] or k in ("VELES_MAC_IDENTITY", "VELES_MAC_TEAM_ID")}}
        result = subprocess.run(
            [sys.executable, "-I", str(HERE / "release_gate.py"), "sign", "--platform", "macos", "--stage", "nested", "--inputs", str(case.inputs),
             "--output", str(case.root / "out"), "--scratch", str(case.root / "scratch"), "--reports", str(case.root / "reports"),
             "--descriptor", str(case.descriptor), "--boundary", str(fragment), "--commit", COMMIT, "--recipe-digest", gate.recipe_digest(gate.INSTALLER_DIR, "macos")],
            env=env, capture_output=True, text=True, timeout=60,
        )  # fmt: skip
        # Provenance was accepted (it got as far as the credential check), and nothing was created.
        self.assertIn("credential-missing", result.stdout + result.stderr)
        self.assertEqual(leftovers(case), [])


if __name__ == "__main__":
    unittest.main()
