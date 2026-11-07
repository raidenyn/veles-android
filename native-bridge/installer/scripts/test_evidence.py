"""Tests for the OTP-25 acceptance matrix and the evidence validator (Task 15).

Every bundle built here is a SYNTHETIC validator fixture living in a temporary
directory. None of it is, or is ever written as, physical acceptance evidence: the
tests prove what the validator refuses and accepts structurally, nothing about any
installer, Windows or macOS machine.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import evidence

HERE = Path(__file__).resolve().parent
INSTALLER = HERE.parent
REPO = INSTALLER.parent.parent
CASES = INSTALLER / "acceptance" / "cases.json"
MATRIX = INSTALLER / "acceptance" / "matrix.json"
DOC = REPO / "docs" / "otp-25-installers.md"

FAMILIES = (
    "clean-install", "launch", "repair", "upgrade", "failed-upgrade", "rollback",
    "uninstall", "fresh-user", "origin-negative", "dev-coexistence", "multi-profile",
    "compatibility", "permission-recovery", "unicode-path", "hygiene", "offline-trust",
)
PLATFORMS = ("windows", "macos")

COMMIT = "a" * 40
H = {name: hashlib.sha256(name.encode()).hexdigest() for name in
     ("config", "descriptor", "tools", "unsigned", "package")}
SIGNER = {"windows": "CN=Example Publisher Fixture", "macos": "Developer ID Application: Example (ABCDE12345)"}
STANDARD_CAPTURE = {
    "windows": "USER INFORMATION\nGROUP INFORMATION\nEveryone  S-1-1-0\nMandatory Label\\Medium Mandatory Level  S-1-16-8192\n",
    "macos": "uid=501(tester) gid=20(staff) groups=20(staff),12(everyone),61(localaccounts)\n",
}
ADMIN_CAPTURE = {
    "windows": "BUILTIN\\Administrators  S-1-5-32-544  Group used for deny only\nMedium Mandatory Level S-1-16-8192\n",
    "macos": "uid=501(tester) gid=20(staff) groups=20(staff),80(admin),12(everyone)\n",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def line_of(entry: dict) -> bytes:
    return json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()


def load_cases() -> dict:
    return json.loads(CASES.read_text())


def case_by_id(case_id: str) -> dict:
    return next(c for c in load_cases()["cases"] if c["id"] == case_id)


class Bundle:
    """Builds a self-consistent synthetic bundle; tests then break exactly one thing."""

    def __init__(self, root: Path, platform: str = "windows") -> None:
        self.platform = platform
        self.run = root / "run"
        self.ext = root / "external"
        (self.run / "captures").mkdir(parents=True)
        self.ext.mkdir()
        self.captures: dict[str, bytes] = {}
        self.entries: list[dict] = []
        self.accounts = [{"id": "std-1", "kind": "standard", "fresh": False, "identity_capture": ""}]
        self.operator = "operator-1"
        self.adjudicator = "adjudicator-1"
        self.publication = {"raw_captures": "withheld"}
        self.binding_captures: dict[str, str] = {}
        self.add_capture("whoami.txt", STANDARD_CAPTURE[platform])
        self.accounts[0]["identity_capture"] = "captures/whoami.txt"
        self.add_capture("package-digest.txt", f"SHA256 {H['package']}  setup\n")
        self.add_capture("signature.txt", f"signer: {SIGNER[platform]}\n")
        self.binding_captures = {
            "package_digest": "captures/package-digest.txt",
            "signature": "captures/signature.txt",
        }
        if platform == "macos":
            self.add_capture("gatekeeper.txt", "accepted source=Notarized Developer ID\n")
            self.binding_captures["gatekeeper"] = "captures/gatekeeper.txt"
        self.add_capture("clock.txt", "clock offset 0.2s\n")
        self.binding = {
            "source_commit": COMMIT, "config_sha256": H["config"],
            "descriptor_sha256": H["descriptor"], "tools_sha256": H["tools"],
            "unsigned_sha256": H["unsigned"], "package_sha256": H["package"],
            "signer": SIGNER[platform], "captures": self.binding_captures,
        }
        self.clocks = {"source": "ntp", "start_utc": "2026-12-01T09:00:00Z",
                       "end_utc": "2026-12-01T18:00:00Z", "host_offset_seconds": 0.2,
                       "offset_capture": "captures/clock.txt"}
        self.anchor = {"schema": 1, "platforms": {platform: {
            "source_commit": COMMIT, "config_sha256": H["config"], "descriptor_sha256": H["descriptor"],
            "tools_sha256": H["tools"], "unsigned_sha256": H["unsigned"],
            "package_sha256": H["package"], "signer": SIGNER[platform]}}}
        self.clock_tick = 0

    # -- raw material -------------------------------------------------------
    def add_capture(self, name: str, text: str) -> str:
        self.captures[name] = text.encode()
        return f"captures/{name}"

    def stamp(self) -> str:
        self.clock_tick += 1
        return f"2026-12-01T10:{self.clock_tick // 60:02d}:{self.clock_tick % 60:02d}Z"

    def attempt(self, family: str, outcome: str = "PASS", *, account: str = "std-1",
                drop_action: str | None = None, drop_observation: str | None = None,
                reason: str | None = None, resolves: list[str] | None = None) -> dict:
        case = case_by_id(f"{self.platform}.{family}")
        n = len(self.entries) + 1
        attempt_id = f"att-{n}"
        started = self.stamp()
        actions, observations = [], []
        for action in case["actions"]:
            if action == drop_action:
                continue
            path = self.add_capture(f"{attempt_id}-{action}.txt", f"{action} output {n}\n")
            actions.append({"id": action, "at": self.stamp(), "step": f"controlled step {action}",
                            "capture": path, "returns": "0", "elevated": False})
        for obs in case["observations"]:
            if obs == drop_observation:
                continue
            path = self.add_capture(f"{attempt_id}-{obs}.txt", f"{obs} observed {n}\n")
            observations.append({"id": obs, "at": self.stamp(), "capture": path})
        entry = {"seq": n, "attempt_id": attempt_id, "case": case["id"], "account": account,
                 "started": started, "ended": self.stamp(), "outcome": outcome,
                 "actions": actions, "observations": observations}
        if outcome != "PASS":
            entry["reason"] = reason if reason is not None else f"{outcome} fixture reason"
        if resolves:
            entry["resolves"] = resolves
        self.entries.append(entry)
        return entry

    def blocked(self, family: str, reason: str = "no signed candidate or standard account") -> dict:
        case = case_by_id(f"{self.platform}.{family}")
        n = len(self.entries) + 1
        entry = {"seq": n, "attempt_id": f"att-{n}", "case": case["id"], "account": None,
                 "started": self.stamp(), "ended": self.stamp(), "outcome": "BLOCKED",
                 "actions": [], "observations": [], "reason": reason}
        self.entries.append(entry)
        return entry

    # -- writing ------------------------------------------------------------
    def write(self, *, adjudicate: bool = True, verdicts: dict[str, str] | None = None) -> "Bundle":
        for name, data in self.captures.items():
            (self.run / "captures" / name).write_bytes(data)
        lines, prev = [], "0" * 64
        for entry in self.entries:
            record = dict(entry, prev=prev)
            raw = line_of(record)
            lines.append(raw)
            prev = sha(raw)
        (self.run / "ledger.jsonl").write_bytes(b"".join(raw + b"\n" for raw in lines))
        self.head = prev
        run = {
            "schema": 1, "run_id": "synthetic-validator-fixture", "platform": self.platform,
            "operator": {"id": self.operator}, "binding": self.binding,
            "environment": {"os_version": "fixture-os", "arch": "x86_64", "chrome_version": "fixture-chrome"},
            "clocks": self.clocks, "accounts": self.accounts,
            "captures": [{"path": f"captures/{n}", "sha256": sha(d), "bytes": len(d)}
                         for n, d in sorted(self.captures.items())],
            "publication": self.publication,
        }
        (self.run / "run.json").write_text(json.dumps(run, indent=1))
        (self.ext / "anchor.json").write_text(json.dumps(self.anchor))
        if adjudicate:
            self.adjudicate(verdicts)
        return self

    def adjudicate(self, verdicts: dict[str, str] | None = None) -> None:
        verdicts = verdicts or {}
        adj = {
            "schema": 1, "adjudicator": {"id": self.adjudicator, "independent_of": [self.operator]},
            "run_json_sha256": sha((self.run / "run.json").read_bytes()),
            "ledger_head_sha256": self.head, "adjudicated_at": "2026-12-01T19:00:00Z",
            "verdicts": [{"attempt_id": e["attempt_id"], "outcome": verdicts.get(e["attempt_id"], e["outcome"]),
                          "rationale": "fixture"} for e in self.entries],
        }
        (self.run / "adjudication.json").write_text(json.dumps(adj))

    @property
    def anchor_path(self) -> Path:
        return self.ext / "anchor.json"

    def validate(self, **kw) -> list[str]:
        return evidence.validate(self.run, CASES, self.anchor_path, **kw)

    # -- post-write tampering helpers -----------------------------------------
    def edit_run(self, fn) -> None:
        path = self.run / "run.json"
        doc = json.loads(path.read_text())
        fn(doc)
        path.write_text(json.dumps(doc, indent=1))

    def readjudicate(self) -> None:
        self.adjudicate()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def bundle(self, platform: str = "windows") -> Bundle:
        return Bundle(self.root / platform, platform)

    def assertRejected(self, errors: list[str], needle: str) -> None:
        self.assertTrue(errors, "validator accepted evidence that must be rejected")
        self.assertTrue(any(needle.lower() in e.lower() for e in errors),
                        f"no error mentions {needle!r}: {errors}")


class MatrixDefinition(Base):
    def test_matrix_has_all_required_cases(self) -> None:
        cases = load_cases()["cases"]
        ids = [c["id"] for c in cases]
        self.assertEqual(len(ids), len(set(ids)), "case ids must be unique")
        matrix_ids = [row["id"] for row in json.loads(MATRIX.read_text())["rows"]]
        for platform in PLATFORMS:
            for family in FAMILIES:
                case_id = f"{platform}.{family}"
                self.assertIn(case_id, ids, f"{case_id} missing from cases.json")
                self.assertIn(case_id, matrix_ids, f"{case_id} missing from matrix.json")
        self.assertEqual(sorted(ids), sorted(matrix_ids), "matrix and cases must cover the same ids")
        self.assertEqual(len(ids), len(PLATFORMS) * len(FAMILIES), "unexpected extra or missing cases")
        for case in cases:
            platform, _, family = case["id"].partition(".")
            self.assertEqual((case["platform"], case["family"]), (platform, family))
            self.assertTrue(case["actions"] and case["observations"], case["id"])
            self.assertTrue(case["why"].strip(), f"{case['id']} must say why the family is required")
            self.assertTrue(case["requires"]["standard_account"], case["id"])

    def test_initial_matrix_has_no_physical_pass(self) -> None:
        doc = json.loads(MATRIX.read_text())
        outcomes = {row["outcome"] for row in doc["rows"]}
        self.assertTrue(outcomes <= {"BLOCKED", "NOT_RUN"}, outcomes)
        self.assertNotIn("PASS", outcomes)
        self.assertEqual(doc["acceptance"]["decision"], "none")
        for row in doc["rows"]:
            self.assertEqual(row["attempts"], [], f"{row['id']} claims history without evidence")
            self.assertTrue(row["blockers"], f"{row['id']} needs a named blocker")
        self.assertEqual(evidence.check_matrix(CASES, MATRIX), [])

    def test_every_case_carries_an_exact_settling_step(self) -> None:
        for case in load_cases()["cases"]:
            settle = case["settle"]
            self.assertTrue(settle["commands"], f"{case['id']} has no exact command")
            self.assertTrue(settle["manual"], f"{case['id']} has no manual step")
            self.assertTrue(case["obligations"], case["id"])
            self.assertTrue(set(case["blockers"]) <= set(load_cases()["blockers"]), case["id"])

    def test_matrix_check_rejects_a_physical_pass_and_unbacked_rows(self) -> None:
        doc = json.loads(MATRIX.read_text())
        bad = copy.deepcopy(doc)
        bad["rows"][0]["outcome"] = "PASS"
        path = self.root / "bad-matrix.json"
        path.write_text(json.dumps(bad))
        self.assertRejected(evidence.check_matrix(CASES, path), "PASS")
        missing = copy.deepcopy(doc)
        del missing["rows"][3]
        path.write_text(json.dumps(missing))
        self.assertRejected(evidence.check_matrix(CASES, path), "missing")
        unblocked = copy.deepcopy(doc)
        unblocked["rows"][1]["blockers"] = []
        path.write_text(json.dumps(unblocked))
        self.assertRejected(evidence.check_matrix(CASES, path), "blocker")
        accepted = copy.deepcopy(doc)
        accepted["acceptance"]["decision"] = "accepted"
        path.write_text(json.dumps(accepted))
        self.assertRejected(evidence.check_matrix(CASES, path), "acceptance")

    def test_document_states_the_recorded_distribution(self) -> None:
        rows = json.loads(MATRIX.read_text())["rows"]
        text = DOC.read_text()
        for outcome in ("PASS", "FAIL", "BLOCKED", "NOT_RUN"):
            count = sum(1 for r in rows if r["outcome"] == outcome)
            self.assertIn(f"{count} {outcome}", text, f"docs must state `{count} {outcome}`")
        for needle in ("#89", "#125", "#127", "does not close"):
            self.assertIn(needle, text)


class StandardUser(Base):
    def test_valid_synthetic_bundle_is_only_structurally_valid(self) -> None:
        for platform in PLATFORMS:
            b = self.bundle(platform)
            b.attempt("clean-install")
            b.write()
            self.assertEqual(b.validate(), [], platform)

    def test_admin_account_cannot_prove_standard_user(self) -> None:
        for platform in PLATFORMS:
            with self.subTest(declared_admin=platform):
                b = Bundle(self.root / f"a-{platform}", platform)
                b.accounts[0]["kind"] = "admin"
                b.attempt("clean-install")
                b.write()
                self.assertRejected(b.validate(), "standard")
            with self.subTest(identity_capture_shows_admin=platform):
                b = Bundle(self.root / f"b-{platform}", platform)
                b.add_capture("whoami.txt", ADMIN_CAPTURE[platform])
                b.attempt("clean-install")
                b.write()
                self.assertRejected(b.validate(), "administrator")
        with self.subTest("elevated action"):
            b = Bundle(self.root / "elev")
            entry = b.attempt("clean-install")
            entry["actions"][0]["elevated"] = True
            b.write()
            self.assertRejected(b.validate(), "elevat")
        with self.subTest("missing identity capture"):
            b = Bundle(self.root / "noid")
            b.accounts[0]["identity_capture"] = ""
            b.attempt("clean-install")
            b.write()
            self.assertRejected(b.validate(), "identity")
        with self.subTest("unknown account"):
            b = Bundle(self.root / "unk")
            b.attempt("clean-install", account="ghost")
            b.write()
            self.assertRejected(b.validate(), "account")

    def test_fresh_user_case_requires_a_fresh_account_with_evidence(self) -> None:
        b = self.bundle()
        b.attempt("fresh-user")
        b.write()
        self.assertRejected(b.validate(), "fresh")
        b = Bundle(self.root / "fresh-flag")
        b.accounts[0]["fresh"] = False
        b.accounts[0]["fresh_evidence"] = b.add_capture("fresh.txt", "profile created 2026-12-01\n")
        b.attempt("fresh-user")
        b.write()
        self.assertRejected(b.validate(), "fresh")
        b = Bundle(self.root / "fresh-ok")
        b.accounts[0]["fresh"] = True
        b.accounts[0]["fresh_evidence"] = b.add_capture("fresh.txt", "profile created 2026-12-01\n")
        b.attempt("fresh-user")
        b.write()
        self.assertEqual(b.validate(), [])


class Binding(Base):
    def test_wrong_signed_candidate_or_self_attested_digest_rejected(self) -> None:
        for key in ("package_sha256", "source_commit", "config_sha256", "descriptor_sha256",
                    "tools_sha256", "unsigned_sha256", "signer"):
            with self.subTest(wrong=key):
                b = Bundle(self.root / key)
                b.attempt("clean-install")
                b.binding[key] = "b" * 40 if key == "source_commit" else ("Other Signer" if key == "signer" else "c" * 64)
                b.write()
                self.assertRejected(b.validate(), key)
        with self.subTest("digest never observed in a raw capture (self-attested)"):
            b = Bundle(self.root / "selfattest")
            b.add_capture("package-digest.txt", "SHA256 " + "d" * 64 + "  setup\n")
            b.attempt("clean-install")
            b.write()
            self.assertRejected(b.validate(), "self-attested")
        with self.subTest("signer never observed in the signature capture"):
            b = Bundle(self.root / "nosigner")
            b.add_capture("signature.txt", "signer: someone else\n")
            b.attempt("clean-install")
            b.write()
            self.assertRejected(b.validate(), "self-attested")
        with self.subTest("anchor supplied from inside the evidence run"):
            b = Bundle(self.root / "inside")
            b.attempt("clean-install")
            b.write()
            inside = b.run / "anchor.json"
            shutil.copy(b.anchor_path, inside)
            self.assertRejected(evidence.validate(b.run, CASES, inside), "external")
        with self.subTest("anchor lacks the platform"):
            b = Bundle(self.root / "noplat")
            b.attempt("clean-install")
            b.anchor = {"schema": 1, "platforms": {}}
            b.write()
            self.assertRejected(b.validate(), "anchor")
        with self.subTest("placeholder digest"):
            b = Bundle(self.root / "zero")
            b.attempt("clean-install")
            b.binding["package_sha256"] = "0" * 64
            b.anchor["platforms"]["windows"]["package_sha256"] = "0" * 64
            b.write()
            self.assertRejected(b.validate(), "package_sha256")

    def test_platform_mismatch_between_run_and_case_is_rejected(self) -> None:
        b = self.bundle("windows")
        entry = b.attempt("clean-install")
        entry["case"] = "macos.clean-install"
        b.write()
        self.assertRejected(b.validate(), "platform")


class RawCaptures(Base):
    def test_capture_checksum_missing_unlisted_and_escaping_paths(self) -> None:
        b = self.bundle()
        b.attempt("clean-install")
        b.write()
        victim = next((b.run / "captures").glob("att-1-open-*"))
        victim.write_bytes(b"tampered")
        self.assertRejected(b.validate(), "checksum")
        victim.unlink()
        self.assertRejected(b.validate(), "missing")
        b = Bundle(self.root / "unlisted")
        b.attempt("clean-install")
        b.write()
        (b.run / "captures" / "stray.txt").write_text("not in the index")
        self.assertRejected(b.validate(), "unlisted")
        b = Bundle(self.root / "escape")
        entry = b.attempt("clean-install")
        entry["actions"][0]["capture"] = "../external/anchor.json"
        b.write()
        self.assertRejected(b.validate(), "capture")
        with self.subTest("symlink out of captures/"):
            b = Bundle(self.root / "symlink")
            b.attempt("clean-install")
            b.write()
            victim = next((b.run / "captures").glob("att-1-open-*"))
            victim.unlink()
            victim.symlink_to(b.anchor_path)
            self.assertRejected(b.validate(), "symbolic link")
        with self.subTest("capture reached through a symlinked directory"):
            b = Bundle(self.root / "dirlink")
            b.attempt("clean-install")
            b.write()
            outside = self.root / "dirlink-outside"
            outside.mkdir()
            (outside / "f.txt").write_bytes(b"outside")
            (b.run / "captures" / "sub").symlink_to(outside, target_is_directory=True)
            def add(doc):
                doc["captures"].append({"path": "captures/sub/f.txt", "sha256": sha(b"outside"), "bytes": 7})
            b.edit_run(add)
            self.assertRejected(b.validate(), "escapes")
        with self.subTest("symlink to another capture inside captures/"):
            b = Bundle(self.root / "symlink2")
            b.attempt("clean-install")
            b.write()
            victim = next((b.run / "captures").glob("att-1-open-*"))
            data = victim.read_bytes()
            victim.unlink()
            (b.run / "captures" / "real.bin").write_bytes(data)
            victim.symlink_to("real.bin")
            self.assertTrue(b.validate())

    def test_missing_action_or_observation_is_not_a_pass(self) -> None:
        case = case_by_id("windows.clean-install")
        b = self.bundle()
        b.attempt("clean-install", drop_action=case["actions"][0])
        b.write()
        self.assertRejected(b.validate(), case["actions"][0])
        b = Bundle(self.root / "obs")
        b.attempt("clean-install", drop_observation="state-after")
        b.write()
        self.assertRejected(b.validate(), "state-after")

    def test_clocks_must_bound_and_order_the_attempt(self) -> None:
        b = self.bundle()
        b.attempt("clean-install")
        b.clocks["start_utc"], b.clocks["end_utc"] = "2026-12-01T11:00:00Z", "2026-12-01T12:00:00Z"
        b.write()
        self.assertRejected(b.validate(), "clock window")
        b = Bundle(self.root / "ordered")
        b.attempt("clean-install")
        b.clocks["start_utc"], b.clocks["end_utc"] = "2026-12-01T18:00:00Z", "2026-12-01T09:00:00Z"
        b.write()
        self.assertRejected(b.validate(), "ordered")
        b = Bundle(self.root / "actionclock")
        entry = b.attempt("clean-install")
        entry["actions"][0]["at"] = "2026-12-02T10:00:00Z"
        b.write()
        self.assertRejected(b.validate(), "clock")
        b = Bundle(self.root / "skew")
        b.clocks["host_offset_seconds"] = 900
        b.attempt("clean-install")
        b.write()
        self.assertRejected(b.validate(), "clock")
        b = Bundle(self.root / "noclock")
        del b.clocks["offset_capture"]
        b.attempt("clean-install")
        b.write()
        self.assertRejected(b.validate(), "clock")


class Retention(Base):
    FAMILIES = ("repair", "upgrade", "rollback", "permission-recovery")

    def test_missing_grant_observation_is_not_retention_pass(self) -> None:
        for family in self.FAMILIES:
            for platform in PLATFORMS:
                self.assertTrue(case_by_id(f"{platform}.{family}")["requires"]["retention_claim"])
        for missing in ("grant-before", "grant-after"):
            with self.subTest(missing=missing):
                b = Bundle(self.root / missing)
                b.attempt("repair", drop_observation=missing)
                b.write()
                errors = b.validate()
                self.assertRejected(errors, missing)
                self.assertRejected(errors, "retention")
        with self.subTest("one capture cannot be both before and after"):
            b = Bundle(self.root / "same")
            entry = b.attempt("repair")
            by_id = {o["id"]: o for o in entry["observations"]}
            by_id["grant-after"]["capture"] = by_id["grant-before"]["capture"]
            b.write()
            self.assertRejected(b.validate(), "grant")
        with self.subTest("grant-after must be later than grant-before"):
            b = Bundle(self.root / "order")
            entry = b.attempt("repair")
            by_id = {o["id"]: o for o in entry["observations"]}
            by_id["grant-after"]["at"], by_id["grant-before"]["at"] = by_id["grant-before"]["at"], by_id["grant-after"]["at"]
            b.write()
            self.assertRejected(b.validate(), "grant")
        with self.subTest("complete observation is accepted"):
            b = Bundle(self.root / "ok")
            b.attempt("repair")
            b.write()
            self.assertEqual(b.validate(), [])


class AppendOnly(Base):
    def test_prior_failures_are_append_only(self) -> None:
        b = self.bundle()
        b.attempt("clean-install", "FAIL", reason="installer refused: observed on fixture")
        b.attempt("clean-install", "PASS", resolves=["att-1"])
        b.write()
        self.assertEqual(b.validate(), [])
        with self.subTest("a PASS after a FAIL must acknowledge it"):
            c = Bundle(self.root / "ack")
            c.attempt("clean-install", "FAIL")
            c.attempt("clean-install", "PASS")
            c.write()
            self.assertRejected(c.validate(), "prior failure")
        with self.subTest("deleting the failure line breaks the chain"):
            lines = (b.run / "ledger.jsonl").read_bytes().splitlines(keepends=True)
            (b.run / "ledger.jsonl").write_bytes(lines[1])
            self.assertRejected(b.validate(), "ledger")
        with self.subTest("rewriting the chain consistently still fails the adjudicated head"):
            d = Bundle(self.root / "rewrite")
            d.attempt("clean-install", "FAIL")
            d.attempt("clean-install", "PASS", resolves=["att-1"])
            d.write()
            survivor = copy.deepcopy(d.entries[1])
            survivor["seq"] = 1
            survivor.pop("resolves")
            raw = line_of(dict(survivor, prev="0" * 64))
            (d.run / "ledger.jsonl").write_bytes(raw + b"\n")
            self.assertRejected(d.validate(), "adjudicat")
        with self.subTest("editing an earlier entry in place breaks the next link"):
            g = Bundle(self.root / "inplace")
            g.attempt("clean-install", "FAIL", reason="original reason")
            g.attempt("clean-install", "PASS", resolves=["att-1"])
            g.write()
            path = g.run / "ledger.jsonl"
            lines = path.read_bytes().splitlines(keepends=True)
            lines[0] = lines[0].replace(b"original reason", b"softened reason")
            path.write_bytes(b"".join(lines))
            self.assertRejected(g.validate(), "ledger chain")
        with self.subTest("flipping a FAIL to PASS in place is detected"):
            e = Bundle(self.root / "flip")
            e.attempt("clean-install", "FAIL")
            e.write()
            path = e.run / "ledger.jsonl"
            path.write_bytes(path.read_bytes().replace(b'"outcome":"FAIL"', b'"outcome":"PASS"'))
            errors = e.validate()
            self.assertTrue(errors)

    def test_previous_run_must_be_a_byte_prefix_of_the_new_ledger(self) -> None:
        old = Bundle(self.root / "old")
        old.attempt("clean-install", "FAIL")
        old.write()
        new = Bundle(self.root / "new")
        new.captures.update(old.captures)
        new.entries = copy.deepcopy(old.entries)
        new.clock_tick = old.clock_tick
        new.attempt("clean-install", "PASS", resolves=["att-1"])
        new.write()
        self.assertEqual(new.validate(previous=old.run), [])
        rewritten = Bundle(self.root / "rewritten")
        rewritten.captures.update(old.captures)
        rewritten.clock_tick = old.clock_tick
        rewritten.attempt("clean-install", "PASS")
        rewritten.write()
        self.assertRejected(rewritten.validate(previous=old.run), "append-only")

    def test_blocked_attempts_are_recorded_but_need_a_reason(self) -> None:
        b = self.bundle()
        b.blocked("clean-install")
        b.write()
        self.assertEqual(b.validate(), [])
        c = Bundle(self.root / "noreason")
        entry = c.blocked("clean-install")
        entry["reason"] = ""
        c.write()
        self.assertRejected(c.validate(), "reason")
        d = Bundle(self.root / "badoutcome")
        entry = d.blocked("clean-install")
        entry["outcome"] = "MAYBE"
        d.write()
        self.assertRejected(d.validate(), "outcome")


class Adjudication(Base):
    def test_adjudication_is_separate_independent_and_bound(self) -> None:
        b = self.bundle()
        b.attempt("clean-install")
        b.write(adjudicate=False)
        self.assertRejected(b.validate(), "adjudication")
        b.adjudicator = b.operator
        b.adjudicate()
        self.assertRejected(b.validate(), "independent")
        b.adjudicator = "adjudicator-1"
        b.adjudicate()
        self.assertEqual(b.validate(), [])
        with self.subTest("adjudicator may not be the tested account"):
            b.adjudicator = "std-1"
            b.adjudicate()
            self.assertRejected(b.validate(), "independent")
        with self.subTest("run.json edited after adjudication"):
            b.adjudicator = "adjudicator-1"
            b.adjudicate()
            b.edit_run(lambda d: d["environment"].update(chrome_version="changed"))
            self.assertRejected(b.validate(), "adjudication")
        with self.subTest("adjudicator verdict disagrees with the claimed PASS"):
            c = Bundle(self.root / "disagree")
            c.attempt("clean-install")
            c.write(verdicts={"att-1": "FAIL"})
            self.assertRejected(c.validate(), "verdict")
        with self.subTest("verdict missing for an attempt"):
            d = Bundle(self.root / "gap")
            d.attempt("clean-install")
            d.attempt("launch")
            d.write()
            adj = json.loads((d.run / "adjudication.json").read_text())
            adj["verdicts"].pop()
            (d.run / "adjudication.json").write_text(json.dumps(adj))
            self.assertRejected(d.validate(), "verdict")

    def test_raw_capture_publication_requires_privacy_review_by_someone_else(self) -> None:
        b = self.bundle()
        b.publication = {"raw_captures": "published"}
        b.attempt("clean-install")
        b.write()
        self.assertRejected(b.validate(), "privacy")
        b.publication = {"raw_captures": "published",
                         "privacy_review": {"reviewer": b.operator, "status": "approved", "reviewed_utc": "2026-12-01T19:30:00Z"}}
        b.write()
        self.assertRejected(b.validate(), "privacy")
        b.publication["privacy_review"]["reviewer"] = "privacy-reviewer"
        b.write()
        self.assertEqual(b.validate(), [])
        b.publication["privacy_review"]["status"] = "pending"
        b.write()
        self.assertRejected(b.validate(), "privacy")


class Cli(Base):
    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = evidence.main(list(argv))
            except SystemExit as exit_:
                code = int(exit_.code or 0)
        return code, out.getvalue(), err.getvalue()

    def test_exit_codes_and_never_claims_acceptance(self) -> None:
        b = self.bundle()
        b.attempt("clean-install")
        b.write()
        args = ("validate", "--run", str(b.run), "--cases", str(CASES), "--anchor", str(b.anchor_path))
        code, out, _ = self.run_cli(*args)
        self.assertEqual(code, 0)
        self.assertIn("NOT acceptance", out)
        self.assertNotRegex(out, r"(?i)\baccepted\b")
        b.binding["package_sha256"] = "e" * 64
        b.write()
        code, out, _ = self.run_cli(*args)
        self.assertEqual(code, 1)
        self.assertIn("package_sha256", out)
        code, _, _ = self.run_cli("validate", "--run", str(self.root / "absent"), "--cases", str(CASES),
                                  "--anchor", str(b.anchor_path))
        self.assertEqual(code, 2)
        code, _, _ = self.run_cli("validate", "--run", str(b.run), "--cases", str(CASES),
                                  "--anchor", str(self.root / "no-anchor.json"))
        self.assertEqual(code, 2)
        (self.root / "bad-anchor.json").write_text("{not json")
        code, _, _ = self.run_cli("validate", "--run", str(b.run), "--cases", str(CASES),
                                  "--anchor", str(self.root / "bad-anchor.json"))
        self.assertEqual(code, 2)
        code, _, _ = self.run_cli("check-matrix", "--cases", str(CASES), "--matrix", str(MATRIX))
        self.assertEqual(code, 0)

    def test_malformed_run_json_is_invalid_evidence_not_a_crash(self) -> None:
        b = self.bundle()
        b.attempt("clean-install")
        b.write()
        (b.run / "run.json").write_text("{broken")
        code, out, _ = self.run_cli("validate", "--run", str(b.run), "--cases", str(CASES), "--anchor", str(b.anchor_path))
        self.assertEqual(code, 1)

    def test_validate_raises_for_missing_inputs(self) -> None:
        with self.assertRaises(evidence.MissingInput):
            evidence.validate(self.root / "nope", CASES, self.root / "nope.json")


class Hygiene(unittest.TestCase):
    def test_no_credentials_or_binaries_in_acceptance_files(self) -> None:
        for path in (CASES, MATRIX):
            text = path.read_text()
            self.assertIsNone(re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY", text))
            self.assertIsNone(re.search(r"\b[0-9a-f]{64}\b", text), f"{path.name} must not carry invented digests")
            json.loads(text)


if __name__ == "__main__":
    unittest.main()
