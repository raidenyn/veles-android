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
USER_NAME = {"windows": "FIXTURE-PC\\tester", "macos": "tester"}
# Shapes follow the documented `whoami /groups` + `whoami /user` and `id` outputs.
WINDOWS_GROUPS = (
    "GROUP INFORMATION\n-----------------\n\n"
    "Group Name                             Type             SID          Attributes\n"
    "====================================== ================ ============ ==================================================\n"
    "Everyone                               Well-known group S-1-1-0      Mandatory group, Enabled by default, Enabled group\n"
    "BUILTIN\\Users                          Alias            S-1-5-32-545 Mandatory group, Enabled by default, Enabled group\n"
    "{extra}"
    "Mandatory Label\\{level} Mandatory Level Label            {sid}\n\n"
)
WINDOWS_USER = (
    "USER INFORMATION\n----------------\n\n"
    "User Name           SID\n=================== =============================================\n"
    "{name} S-1-5-21-1111111111-2222222222-3333333333-1001\n"
)


def windows_identity(name: str = USER_NAME["windows"], level: str = "Medium", sid: str = "S-1-16-8192",
                     extra: str = "") -> str:
    return WINDOWS_GROUPS.format(level=level, sid=sid, extra=extra) + WINDOWS_USER.format(name=name)


STANDARD_CAPTURE = {
    "windows": windows_identity(),
    "macos": "uid=501(tester) gid=20(staff) groups=20(staff),12(everyone),61(localaccounts)\n",
}
ADMIN_CAPTURE = {
    "windows": windows_identity(extra="BUILTIN\\Administrators                resolved-deny   S-1-5-32-544 Group used for deny only\n"),
    "macos": "uid=501(tester) gid=20(staff) groups=20(staff),80(admin),12(everyone)\n",
}


def report(operation: str, state: str, *, failure: str | None = None, warnings: list[str] | None = None) -> str:
    """The engine's one stdout document, in the shape cli::render_report writes."""
    return json.dumps({"operation": operation, "state": state, "prior_usable": failure is None,
                       "active_payload": None, "failure": failure, "warnings": warnings or []},
                      sort_keys=True, separators=(",", ":")) + "\n"


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
        self.accounts = [{"id": "std-1", "kind": "standard", "fresh": False, "identity_capture": "",
                          "user_name": USER_NAME[platform]}]
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
        retention = case["requires"].get("retention_action")
        retention_obs = {"grant-before", "grant-after"}

        def observe(obs: str) -> None:
            path = self.add_capture(f"{attempt_id}-{obs}.txt", f"{obs} observed {n}\n")
            observations.append({"id": obs, "at": self.stamp(), "capture": path})

        for action in case["actions"]:
            if action == drop_action:
                continue
            if action == retention and "grant-before" in case["observations"] and drop_observation != "grant-before":
                observe("grant-before")
            disposition = case.get("dispositions", {}).get(action)
            if disposition is None:
                text, returns = f"{action} output {n}\n", "0"
            elif disposition["result"] == "clean":
                text = report(disposition["operation"], disposition["state"])
                returns = "0"
            else:
                text = report(disposition["operations"][0], "failed", failure=disposition["failures"][0])
                returns = "1"
            path = self.add_capture(f"{attempt_id}-{action}.txt", text)
            actions.append({"id": action, "at": self.stamp(), "step": f"controlled step {action}",
                            "capture": path, "returns": returns, "elevated": False})
            if action == retention and "grant-after" in case["observations"] and drop_observation != "grant-after":
                observe("grant-after")
        for obs in case["observations"]:
            if obs == drop_observation or (retention and obs in retention_obs):
                continue
            observe(obs)
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


class IdentityProof(Base):
    """F14: a standard-user claim needs positive, complete, parsable identity evidence."""

    def _validate_with(self, platform: str, name: str, text: str) -> list[str]:
        b = Bundle(self.root / name, platform)
        b.add_capture("whoami.txt", text)
        b.attempt("clean-install")
        b.write()
        return b.validate()

    def test_empty_or_unrecognised_identity_capture_proves_nothing(self) -> None:
        for platform in PLATFORMS:
            for label, text in (("empty", ""), ("whitespace", "\n\n"),
                                ("unrelated", "command completed\n"),
                                ("error-text", "'whoami' is not recognized as an internal or external command\n")):
                with self.subTest(platform=platform, capture=label):
                    self.assertRejected(self._validate_with(platform, f"{platform}-{label}", text), "identity")

    def test_truncated_or_partial_windows_identity_is_refused(self) -> None:
        full = STANDARD_CAPTURE["windows"]
        cases = {
            "groups-only": full[: full.index("USER INFORMATION")],
            "user-only": full[full.index("USER INFORMATION"):],
            "no-integrity-label": "\n".join(line for line in full.splitlines() if "Mandatory Label" not in line) + "\n",
            "cut-mid-table": full[: full.index("BUILTIN")],
        }
        for label, text in cases.items():
            with self.subTest(capture=label):
                self.assertRejected(self._validate_with("windows", f"w-{label}", text), "identity")

    def test_windows_integrity_level_must_be_medium(self) -> None:
        for level, sid in (("High", "S-1-16-12288"), ("System", "S-1-16-16384"), ("Low", "S-1-16-4096")):
            with self.subTest(level=level):
                errors = self._validate_with("windows", f"w-{level}", windows_identity(level=level, sid=sid))
                self.assertRejected(errors, "integrity")

    def test_observed_account_must_be_the_declared_test_account(self) -> None:
        errors = self._validate_with("windows", "w-other", windows_identity(name="FIXTURE-PC\\someone-else"))
        self.assertRejected(errors, "declared")
        b = Bundle(self.root / "w-undeclared")
        del b.accounts[0]["user_name"]
        b.attempt("clean-install")
        b.write()
        self.assertRejected(b.validate(), "user_name")
        for label, text in (("uid-zero", "uid=0(root) gid=0(wheel) groups=0(wheel),1(daemon)\n"),
                            ("other-user", "uid=502(someone) gid=20(staff) groups=20(staff)\n"),
                            ("no-groups", "uid=501(tester) gid=20(staff)\n"),
                            ("group-names-only", "staff everyone localaccounts\ntester\n")):
            with self.subTest(macos=label):
                self.assertRejected(self._validate_with("macos", f"m-{label}", text), "identity")

    def test_a_complete_standard_capture_is_still_accepted(self) -> None:
        for platform in PLATFORMS:
            self.assertEqual(self._validate_with(platform, f"ok-{platform}", STANDARD_CAPTURE[platform]), [])


class GrantBracket(Base):
    """F13: grant-before AND grant-after must sit on the two sides of the maintenance action."""

    def _bundle(self, name: str, family: str = "repair") -> tuple[Bundle, dict, dict, dict]:
        b = Bundle(self.root / name)
        entry = b.attempt(family)
        obs = {o["id"]: o for o in entry["observations"]}
        action = next(a for a in entry["actions"]
                      if a["id"] == case_by_id(f"windows.{family}")["requires"]["retention_action"])
        return b, entry, obs, action

    def test_the_positive_fixture_brackets_the_maintenance_action(self) -> None:
        b, entry, obs, action = self._bundle("order")
        self.assertLess(obs["grant-before"]["at"], action["at"])
        self.assertLess(action["at"], obs["grant-after"]["at"])
        b.write()
        self.assertEqual(b.validate(), [])

    def test_every_retention_case_names_one_maintenance_action(self) -> None:
        for case in load_cases()["cases"]:
            if case["requires"]["retention_claim"]:
                self.assertIn(case["requires"].get("retention_action"), case["actions"], case["id"])

    def test_both_observations_after_the_maintenance_action_are_refused(self) -> None:
        b, entry, obs, action = self._bundle("after")
        obs["grant-before"]["at"] = "2026-12-01T12:00:00Z"
        obs["grant-after"]["at"] = "2026-12-01T12:00:01Z"
        entry["ended"] = "2026-12-01T12:00:02Z"
        b.write()
        self.assertRejected(b.validate(), "bracket")

    def test_both_observations_before_the_maintenance_action_are_refused(self) -> None:
        b, entry, obs, action = self._bundle("before")
        obs["grant-before"]["at"] = "2026-12-01T09:30:00Z"
        obs["grant-after"]["at"] = "2026-12-01T09:30:01Z"
        entry["started"] = "2026-12-01T09:29:00Z"
        b.write()
        self.assertRejected(b.validate(), "bracket")

    def test_each_one_sided_violation_is_refused_on_its_own(self) -> None:
        for which in ("grant-before", "grant-after"):
            with self.subTest(late=which):
                b, entry, obs, action = self._bundle(f"one-{which}")
                if which == "grant-before":
                    obs["grant-before"]["at"] = "2026-12-01T12:00:00Z"   # after the action, before grant-after
                    obs["grant-after"]["at"] = "2026-12-01T12:00:01Z"
                else:
                    obs["grant-after"]["at"] = "2026-12-01T09:30:00Z"    # before the action
                    obs["grant-before"]["at"] = "2026-12-01T09:29:59Z"
                entry["started"] = "2026-12-01T09:29:00Z"
                entry["ended"] = "2026-12-01T12:00:02Z"
                b.write()
                self.assertRejected(b.validate(), "bracket")

    def test_an_observation_at_the_same_instant_as_the_action_is_refused(self) -> None:
        b, entry, obs, action = self._bundle("same")
        obs["grant-before"]["at"] = action["at"]
        b.write()
        self.assertRejected(b.validate(), "bracket")

    def test_unrelated_setup_actions_need_not_be_bracketed(self) -> None:
        b, entry, obs, action = self._bundle("setup")
        setup = next(a for a in entry["actions"] if a["id"] == "damage-managed-file")
        setup["at"] = "2026-12-01T09:00:05Z"
        entry["started"] = "2026-12-01T09:00:00Z"
        b.write()
        self.assertEqual(b.validate(), [])


class ActionDispositions(Base):
    """F15: engine results are decoded and held to the case's expected disposition."""

    def _repair(self, name: str, text: str | None = None, returns: str | None = None) -> list[str]:
        b = Bundle(self.root / name)
        entry = b.attempt("repair")
        action = next(a for a in entry["actions"] if a["id"] == "choose-repair")
        if text is not None:
            b.captures[action["capture"].removeprefix("captures/")] = text.encode()
        if returns is not None:
            action["returns"] = returns
        b.write()
        return b.validate()

    def test_a_clean_repair_with_a_matching_report_is_accepted(self) -> None:
        self.assertEqual(self._repair("ok"), [])

    def test_a_nonzero_return_is_not_a_clean_maintenance_pass(self) -> None:
        self.assertRejected(self._repair("rc1", returns="1"), "return")

    def test_a_return_that_is_not_the_exact_exit_status_is_refused(self) -> None:
        for bad in ("0 ", "00", "success", "-0", " 0"):
            with self.subTest(returns=bad):
                self.assertRejected(self._repair(f"r-{abs(hash(bad))}", returns=bad), "return")

    def test_cleanup_pending_reports_are_never_a_clean_pass(self) -> None:
        for label, text in (
            ("failure", report("repair", "failed", failure="cleanup-pending")),
            ("warning", report("repair", "repaired", warnings=["cleanup-pending"])),
            ("warning-with-others", report("repair", "repaired", warnings=["leftover-files", "cleanup-pending"])),
        ):
            with self.subTest(report=label):
                self.assertRejected(self._repair(f"cp-{label}", text=text), "report")
                self.assertRejected(self._repair(f"cp0-{label}", text=text, returns="1"), "report")

    def test_failure_unsettled_state_and_wrong_operation_are_refused(self) -> None:
        for label, text in (
            ("failure", report("repair", "failed", failure="io")),
            ("damaged", report("repair", "damaged")),
            ("recovery", report("repair", "recovery-required")),
            ("wrong-op", report("install", "installed")),
            ("wrong-state", report("repair", "installed")),
            ("inspect", report("inspect", "installed")),
        ):
            with self.subTest(report=label):
                self.assertRejected(self._repair(f"bad-{label}", text=text), "report")

    def test_a_capture_that_is_not_one_strict_report_is_refused(self) -> None:
        good = report("repair", "repaired")
        for label, text in (
            ("empty", ""),
            ("prose", "repair finished\n"),
            ("two-documents", good + good),
            ("trailing-text", good + "Done.\n"),
            ("unknown-field", good.replace("}\n", ',"extra":1}\n')),
            ("duplicate-key", '{"operation":"repair","operation":"repair","state":"repaired","prior_usable":true,'
                              '"active_payload":null,"failure":null,"warnings":[]}\n'),
            ("bad-warning", report("repair", "repaired", warnings=["unknown-warning"])),
            ("missing-field", '{"operation":"repair","state":"repaired"}\n'),
        ):
            with self.subTest(report=label):
                self.assertRejected(self._repair(f"doc-{label}", text=text), "report")

    def test_leftover_and_compatibility_warnings_do_not_make_a_clean_result_unclean(self) -> None:
        text = report("repair", "repaired", warnings=["leftover-files", "compatibility-window"])
        self.assertEqual(self._repair("warn", text=text), [])

    def test_an_expected_refusal_must_be_a_refusal_with_an_allowed_failure(self) -> None:
        def run(name: str, text: str | None = None, returns: str | None = None) -> list[str]:
            b = Bundle(self.root / name)
            entry = b.attempt("failed-upgrade")
            action = next(a for a in entry["actions"] if a["id"] == "choose-update")
            if text is not None:
                b.captures[action["capture"].removeprefix("captures/")] = text.encode()
            if returns is not None:
                action["returns"] = returns
            b.write()
            return b.validate()
        self.assertEqual(run("ok"), [])
        with self.subTest("a successful update is not the expected refusal"):
            self.assertRejected(run("success", text=report("install", "installed"), returns="0"), "refus")
        with self.subTest("exit 0 contradicts a refusal report"):
            self.assertRejected(run("rc0", returns="0"), "return")
        with self.subTest("a failure outside the allowed set"):
            self.assertRejected(run("wrong", text=report("install", "failed", failure="busy")), "report")
        with self.subTest("cleanup-pending is not a refusal of a tampered package"):
            self.assertRejected(run("pend", text=report("install", "failed", failure="cleanup-pending")), "report")

    def test_negative_actions_in_other_cases_are_explicit(self) -> None:
        by_case = {c["id"]: c.get("dispositions", {}) for c in load_cases()["cases"]}
        for platform in PLATFORMS:
            self.assertEqual(by_case[f"{platform}.multi-profile"]["attempt-update-with-sessions-open"]["result"], "refused")
            for action in ("repair-with-trust-unavailable", "present-os-denied-package",
                           "present-denied-payload", "attempt-stale-policy-rollback"):
                self.assertEqual(by_case[f"{platform}.offline-trust"][action]["result"], "refused", action)
            self.assertEqual(by_case[f"{platform}.offline-trust"]["repair-with-cached-trust"]["result"], "clean")

    def test_every_engine_choice_has_a_declared_disposition(self) -> None:
        for case in load_cases()["cases"]:
            for action in case["actions"]:
                if action.startswith("choose-") or action in ("retry-update",):
                    self.assertIn(action, case.get("dispositions", {}), f"{case['id']}: {action}")


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
