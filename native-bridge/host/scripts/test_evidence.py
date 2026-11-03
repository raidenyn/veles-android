#!/usr/bin/env python3
"""Negative-first tests for the OTP-24 physical-acceptance evidence tooling (Task 10).

Standard library only. Run from the repository root:

    python3 -m unittest discover -s native-bridge/host/scripts -p 'test_evidence.py'

IMPORTANT: every run directory built here is a **synthetic validator fixture** generated
inside a temporary directory purely to exercise `validate-evidence.py`. It is never copied
under `native-bridge/host/evidence/` and is not physical evidence of anything. The only
way these tests produce `PASS` is by constructing a *complete* record, which proves the
validator can accept a well-formed run (so the negative tests are meaningful: each one
removes or corrupts exactly one thing from an otherwise accepted record and must not PASS).
"""

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOST = HERE.parent
ROOT = HOST.parents[1]
CASES_PATH = HOST / "acceptance" / "cases.json"
MATRIX_PATH = HOST / "acceptance" / "matrix.json"


def load_module(filename, name):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sha(data):
    return hashlib.sha256(data).hexdigest()


H = lambda tag: sha(tag.encode())  # noqa: E731 - deterministic synthetic 64-hex digests
REV = "a" * 40
TREE = H("tree-of-the-anchored-checkout")
T0 = "2026-11-01T10:00:00Z"
BIN_PATH = {"windows": "C:\\abs\\veles-native-host.exe", "macos": "/abs/veles-native-host"}
BIN_FAMILY = {"windows": "windows", "macos": "posix"}


def anchor_for(platform):
    return {
        "source_revision": REV,
        "source_tree_sha256": TREE,
        "evaluator_files": {"scripts/physical-matrix.py": H("p"), "scripts/validate-evidence.py": H("v"),
                            "acceptance/cases.json": hashlib.sha256((Path(__file__).resolve().parent.parent / "acceptance" / "cases.json").read_bytes()).hexdigest()},
        "binary_sha256": H("bin"),
        "binary_path": BIN_PATH[platform],
        "binary_path_platform": BIN_FAMILY[platform],
        "binary_size": 1234,
    }


ANCHOR = anchor_for("windows")
MAC_ANCHOR = anchor_for("macos")


def independent_resolve(entry, payload):
    """Test-side chunk arithmetic, written separately from the validator."""
    if isinstance(entry, int):
        return entry
    return {"chunk-1": payload - 1, "chunk": payload, "chunk+1": payload + 1, "multiple-of-chunk": 2 * payload}[entry]


# Per-slot negotiated link data. Different capacities per slot prove numeric resolution is per link.
LINKS = {"A": ({"kind": "att_mtu", "value": 517}, 508), "B": ({"kind": "att_mtu", "value": 247}, 240)}
MAC_LINKS = {"A": ({"kind": "max_write_without_response", "value": 512}, 508), "B": ({"kind": "att_mtu", "value": 185}, 178)}


def ts(n):
    return f"2026-11-01T10:{n // 60:02d}:{n % 60:02d}Z"


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())


def capture(run, name, kind, content):
    rel = f"captures/{name}.bin"
    write(run / rel, content)
    return {"name": name, "kind": kind, "path": rel, "sha256": sha(content if isinstance(content, bytes) else content.encode()),
            "size": len(content)}


def seal(run):
    """Rebuild catalog, manifest->catalog binding and adjudication->manifest binding."""
    manifest = json.loads((run / "run.json").read_text())
    lines = []
    for p in sorted(run.rglob("*")):
        if p.is_file() and p.name not in ("run.json", "SHA256SUMS", "adjudication.json"):
            lines.append(f"{sha(p.read_bytes())}  {p.relative_to(run).as_posix()}")
    write(run / "SHA256SUMS", "\n".join(lines) + "\n")
    manifest["checksum_catalog"] = {"path": "SHA256SUMS", "sha256": sha((run / "SHA256SUMS").read_bytes())}
    write(run / "run.json", json.dumps(manifest, indent=2))
    if (run / "adjudication.json").exists():
        adj = json.loads((run / "adjudication.json").read_text())
        adj["run_manifest_sha256"] = sha((run / "run.json").read_bytes())
        write(run / "adjudication.json", json.dumps(adj, indent=2))


def build_valid_run(run, case):
    """A complete synthetic run for `case`; the shape the validator is specified to accept."""
    plat = case["platform"]
    chrome_case = case["requires_chrome"]
    caps = []
    for kind in ("host_events", "process"):
        caps.append(capture(run, f"{kind}", kind, f"synthetic {kind} capture\n"))
    if chrome_case:
        caps.append(capture(run, "chrome_cdp", "chrome_cdp", "synthetic cdp capture\n"))
        caps.append(capture(run, "hygiene", "hygiene", "synthetic hygiene capture\n"))
    if case["phones"] != "none":
        caps.append(capture(run, "phone_telemetry", "phone_telemetry", "synthetic phone telemetry\n"))
    cap_names = {c["name"]: c for c in caps}
    ref = "hygiene" if chrome_case else "process"

    actions = []
    for i, name in enumerate(case["required_actions"]):
        actions.append({"seq": i + 1, "name": name, "started_at": ts(10 + i * 2), "ended_at": ts(11 + i * 2), "result": "ok"})
    last = ts(10 + len(actions) * 2)

    slots = [] if case["phones"] == "none" else list(case["phones"])
    phones = []
    for s in slots:
        phones.append({"slot": s, "source_hash": H("phone-" + s), "apk_sha256": H("apk"), "apk_cert_sha256": H("cert"),
                       "fixture_version": "synthetic-1", "clock_offset_ms": 12.0, "clock_uncertainty_ms": 40.0,
                       "clock_source": "synthetic-validator-fixture"})

    controls = [{"name": n, "result": "pass", "evidence_ref": ref} for n in case["required_controls"]]
    observations = [{"name": n, "result": "none_observed", "evidence_ref": "hygiene"} for n in case["observations"]]

    callbacks = [{"seq": 1, "source": "host", "code": "ready", "t": ts(12)}]
    rc = case.get("required_callbacks")
    if rc:
        callbacks.append({"seq": 2, "source": "host", "code": rc["any_of_codes"][0], "native_code": "synthetic-0x1",
                          "classification": "radio_unavailable", "recoverability": "retryable", "t": ts(14)})

    transfers, rejections, receipts, links = [], [], [], []
    link_table = MAC_LINKS if plat == "macos" else LINKS
    if case.get("sizes"):
        for s in slots:
            capacity, payload = link_table[s]
            links.append({"slot": s, "capacity": capacity, "payload_budget": payload, "capture_ref": "host_events"})
            for entry in case["sizes"]["accept"]:
                size = independent_resolve(entry, payload)
                d = H(f"{s}-{entry}-{size}")
                transfers.append({"slot": s, "label": str(entry), "size": size, "chunk": payload, "expected_sha256": d,
                                  "received_sha256": d, "outcome": "ok"})
                receipts.append({"slot": s, "source_hash": H("phone-" + s), "size": size, "sha256": d,
                                 "phone_time": ts(20), "host_time": ts(20)})
        for size in case["sizes"]["reject_without_writes"]:
            rejections.append({"size": size, "error_code": "oversize_record" if size else "invalid_request", "gatt_writes": 0})

    process = {"exit_code": (case.get("expected_exit_codes") or [0])[0], "exited_at": last, "wrapper_exit_code": None}
    pending = None
    spec = case.get("pending_request_handling")
    if spec and spec["mode"] == "eof_unobservable":
        pending = {"mode": spec["mode"], "trigger": spec["trigger"], "per_request_outcome": "unknown",
                   "host_correlated_cancellation_claimed": False, "capture_ref": "host_events",
                   "reason": "abrupt EOF: the connector is detached and Chrome owns the pipe; no per-request terminal event is observable"}
    elif spec:
        code, stage = spec["terminal_error"][0]
        pending = {"mode": spec["mode"], "trigger": spec["trigger"], "request_id": "r-armed", "armed_pending_proven": True,
                   "terminal": {"type": "error", "request_id": "r-armed", "code": code, "stage": stage, "synthesized": False},
                   "capture_ref": "host_events"}
        if spec["ordering"] == "terminal_error_before_shutdown_complete":
            pending["terminal_before_shutdown_complete"] = True

    manifest = {
        "schema": "veles-otp24-physical-evidence/1",
        "run_id": "synthetic-validator-fixture",
        "case_id": case["id"],
        "declared_status": "PASS",
        "operator": "operator-synthetic",
        "synthetic_validator_fixture": True,
        "source": {"revision": REV, "tree_sha256": TREE},
        "evaluator": {"revision": REV, "files": dict(ANCHOR["evaluator_files"])},
        "binary": {"kind": "production", "name": "veles-native-host" + (".exe" if plat == "windows" else ""),
                   "path": BIN_PATH[plat], "sha256": H("bin"), "size": 1234,
                   "build_command": "./gradlew bridgeHostBuild", "built_from_source_revision": REV, "built_from_tree_sha256": TREE},
        "signing": ({"required": True, "identity": "approved-test-identity", "team_id": "TEAMID1234", "bundle_id": "me.nagaev.veles.bridge.poc",
                     "cert_sha256": H("sign"), "bundle_kind": "test_vehicle", "production_identity_claimed": False}
                    if plat == "macos" else {"required": False, "authenticode": "unsigned"}),
        "os": {"name": plat, "version": "synthetic-1", "build": "synthetic-build", "arch": "x86_64"},
        "adapter": {"kind": "bluetooth", "name": "synthetic-adapter", "id_sha256": H("adapter")},
        "account": {"kind": "standard", "id_sha256": H("account")},
        "phones": phones,
        "actions": actions,
        "callbacks": callbacks,
        "controls": controls,
        "observations": observations,
        "links": links,
        "transfers": transfers,
        "rejections": rejections,
        "phone_receipts": receipts,
        "captures": caps,
        "process": process,
        "failed_setups": [],
        "adjudication": {"path": "adjudication.json"},
    }
    if pending is not None:
        manifest["pending_request_handling"] = pending
    if chrome_case:
        tm = {"kind": "test_manifest", "name": "test-manifest.json"}
        content = json.dumps({"name": "me.nagaev.veles.bridge.test", "path": manifest["binary"]["path"], "type": "stdio",
                              "allowed_origins": ["chrome-extension://abcdefghijklmnopabcdefghijklmnop/"]})
        write(run / "test-manifest.json", content)
        manifest["test_manifest"] = {"path": "test-manifest.json", "sha256": sha(content.encode()),
                                     "host_sha256": manifest["binary"]["sha256"], "allowed_origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop/",
                                     "scope": "test_only_not_production_registration"}
        manifest["chrome"] = {"version": "synthetic-1", "channel": "stable", "path": "/abs/chrome", "sha256": H("chrome"),
                              "profile": {"disposable": True, "is_default_profile": False},
                              "launch_args_sha256": H("args"), "extension_id": "abcdefghijklmnopabcdefghijklmnop",
                              "extension_origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop/",
                              "provenance": {"source": "synthetic-validator-fixture", "recorded_at": ts(5), "capture_ref": "chrome_cdp"}}
        if plat == "windows":
            manifest["wrapper_exception"] = {"used": False, "scope": "windows_chrome_never_visible_wrapper_only"}
    write(run / "run.json", json.dumps(manifest, indent=2))
    adj = {"adjudicator": "reviewer-synthetic", "at": ts(40), "verdict": "PASS", "reasons": ["synthetic fixture"],
           "run_manifest_sha256": "", "separate_from_operator": True,
           "review_ref": "synthetic-review-ref-not-authenticated"}
    write(run / "adjudication.json", json.dumps(adj, indent=2))
    seal(run)
    return cap_names


class EvidenceTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v = load_module("validate-evidence.py", "validate_evidence")
        cls.cases = json.loads(CASES_PATH.read_text())["cases"]
        cls.by_id = {c["id"]: c for c in cls.cases}

    def run_dir(self, case_id):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        run = Path(d.name) / "run"
        run.mkdir()
        build_valid_run(run, self.by_id[case_id])
        return run

    def edit(self, run, fn, reseal=True):
        m = json.loads((run / "run.json").read_text())
        fn(m)
        write(run / "run.json", json.dumps(m, indent=2))
        if reseal:
            seal(run)

    def validate(self, run):
        return self.v.validate_run(run, cases_path=CASES_PATH, anchor=self.anchor_of(run))

    def anchor_of(self, run):
        try:
            plat = json.loads((run / "run.json").read_text())["case_id"].split(".")[0]
        except Exception:
            plat = "windows"
        return anchor_for(plat if plat in BIN_PATH else "windows")

    def assertRejected(self, run, code):
        result = self.validate(run)
        self.assertNotEqual(result.verdict, "PASS", result.defects)
        self.assertIn(code, [d[0] for d in result.defects], result.defects)
        return result


WIN_AB = "windows.AB.transfer-boundaries"
MAC_AB = "macos.AB.transfer-boundaries"
WIN_LIFE = "windows.AB.lifecycle-port-disconnect"
MAC_ERR = "macos.A.error-radio-off"
WIN_DIRECT = "windows.none.direct-eof-truncated"


class PositiveControlTests(EvidenceTestBase):
    """A complete record must be accepted; otherwise the negative tests prove nothing."""

    def test_every_case_accepts_a_complete_synthetic_record(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                run = self.run_dir(case["id"])
                result = self.validate(run)
                self.assertEqual(result.verdict, "PASS", result.defects)
                self.assertEqual(result.defects, [])


class IdentityRejectionTests(EvidenceTestBase):
    def test_missing_source_revision(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["source"].pop("revision"))
        self.assertRejected(run, "missing_source_revision")

    def test_malformed_source_revision(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["source"].__setitem__("revision", "HEAD"))
        self.assertRejected(run, "missing_source_revision")

    def test_source_tree_hash_missing(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["source"].pop("tree_sha256"))
        self.assertRejected(run, "source_tree_unbound")

    def test_missing_evaluator_revision(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["evaluator"].pop("revision"))
        self.assertRejected(run, "missing_evaluator_revision")

    def test_missing_evaluator_file_hashes(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["evaluator"].__setitem__("files", {}))
        self.assertRejected(run, "missing_evaluator_revision")

    def test_missing_binary_hash(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["binary"].pop("sha256"))
        self.assertRejected(run, "missing_binary_hash")

    def test_scaffold_binary_is_not_production(self):
        for name in ("otp23-host.exe", "otp23-host-tauri.exe", "host-test-driver", "veles-bridge.exe"):
            with self.subTest(name=name):
                run = self.run_dir(WIN_AB)
                self.edit(run, lambda m, n=name: m["binary"].__setitem__("name", n))
                self.assertRejected(run, "binary_not_production")

    def test_binary_kind_must_be_production(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["binary"].__setitem__("kind", "test_driver"))
        self.assertRejected(run, "binary_not_production")

    def test_binary_built_from_other_source(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["binary"].__setitem__("built_from_source_revision", "c" * 40))
        self.assertRejected(run, "binary_source_mismatch")

    def test_test_manifest_must_point_at_exact_binary(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["test_manifest"].__setitem__("host_sha256", H("other")))
        self.assertRejected(run, "test_manifest_mismatch")

    def test_test_manifest_missing_for_chrome_case(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.pop("test_manifest"))
        self.assertRejected(run, "test_manifest_mismatch")

    def test_test_manifest_with_wildcard_origin(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["test_manifest"].__setitem__("allowed_origin", "chrome-extension://*/"))
        self.assertRejected(run, "test_manifest_mismatch")

    def test_test_manifest_file_hash_mismatch(self):
        run = self.run_dir(WIN_AB)
        write(run / "test-manifest.json", "{}")
        seal(run)
        self.assertRejected(run, "capture_hash_mismatch")

    def test_test_manifest_labelled_as_production_registration(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["test_manifest"].__setitem__("scope", "production_registration"))
        self.assertRejected(run, "test_manifest_mismatch")


class ContextRejectionTests(EvidenceTestBase):
    def test_macos_missing_signing_identity(self):
        run = self.run_dir(MAC_AB)
        self.edit(run, lambda m: m["signing"].pop("identity"))
        self.assertRejected(run, "missing_signing")

    def test_macos_missing_signing_block(self):
        run = self.run_dir(MAC_AB)
        self.edit(run, lambda m: m.pop("signing"))
        self.assertRejected(run, "missing_signing")

    def test_macos_adhoc_substitution_rejected(self):
        run = self.run_dir(MAC_AB)
        self.edit(run, lambda m: m["signing"].__setitem__("identity", "-"))
        self.assertRejected(run, "missing_signing")

    def test_macos_cannot_claim_production_identity(self):
        run = self.run_dir(MAC_AB)
        self.edit(run, lambda m: m["signing"].__setitem__("production_identity_claimed", True))
        self.assertRejected(run, "production_identity_claimed")

    def test_macos_bundle_must_be_labelled_test_vehicle(self):
        run = self.run_dir(MAC_AB)
        self.edit(run, lambda m: m["signing"].__setitem__("bundle_kind", "production"))
        self.assertRejected(run, "production_identity_claimed")

    def test_windows_missing_signing_statement(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.pop("signing"))
        self.assertRejected(run, "missing_signing")

    def test_missing_os_context(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["os"].pop("build"))
        self.assertRejected(run, "missing_os_context")

    def test_os_platform_must_match_case(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["os"].__setitem__("name", "macos"))
        self.assertRejected(run, "missing_os_context")

    def test_missing_adapter_context(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.pop("adapter"))
        self.assertRejected(run, "missing_os_context")

    def test_missing_chrome_block(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.pop("chrome"))
        self.assertRejected(run, "missing_chrome_provenance")

    def test_chrome_provenance_missing(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["chrome"].pop("provenance"))
        self.assertRejected(run, "missing_chrome_provenance")

    def test_chrome_provenance_capture_ref_must_exist(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["chrome"]["provenance"].__setitem__("capture_ref", "nope"))
        self.assertRejected(run, "missing_chrome_provenance")

    def test_chrome_must_use_disposable_profile(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["chrome"]["profile"].__setitem__("disposable", False))
        self.assertRejected(run, "chrome_profile_not_disposable")

    def test_chrome_default_profile_forbidden(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["chrome"]["profile"].__setitem__("is_default_profile", True))
        self.assertRejected(run, "chrome_profile_not_disposable")

    def test_chrome_origin_must_match_manifest(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["chrome"].__setitem__("extension_origin", "chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/"))
        self.assertRejected(run, "test_manifest_mismatch")

    def test_direct_case_does_not_require_chrome_but_may_not_claim_lifecycle(self):
        run = self.run_dir(WIN_DIRECT)
        self.edit(run, lambda m: m["controls"].clear())
        self.assertRejected(run, "missing_control")


class PhoneBindingTests(EvidenceTestBase):
    def test_missing_phones(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("phones", []))
        self.assertRejected(run, "missing_phone_binding")

    def test_only_one_phone_for_two_phone_case(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["phones"].pop())
        self.assertRejected(run, "missing_phone_binding")

    def test_phone_source_hash_missing(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["phones"][0].pop("source_hash"))
        self.assertRejected(run, "missing_phone_binding")

    def test_phone_source_hash_must_not_be_raw_serial(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["phones"][0].__setitem__("source_hash", "R58M12345AB"))
        self.assertRejected(run, "missing_phone_binding")

    def test_two_slots_cannot_share_one_source(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["phones"][1].__setitem__("source_hash", m["phones"][0]["source_hash"]))
        self.assertRejected(run, "duplicate_phone_source")

    def test_phone_clock_allowance_required(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["phones"][0].pop("clock_uncertainty_ms"))
        self.assertRejected(run, "missing_phone_binding")

    def test_phone_clock_source_required(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["phones"][1].__setitem__("clock_source", ""))
        self.assertRejected(run, "missing_phone_binding")

    def test_phone_apk_certificate_required(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["phones"][0].pop("apk_cert_sha256"))
        self.assertRejected(run, "missing_phone_binding")

    def test_receipt_bound_to_wrong_phone(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["phone_receipts"][0].__setitem__("source_hash", H("someone-else")))
        self.assertRejected(run, "receipt_phone_mismatch")

    def test_receipt_clock_outside_allowance(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["phone_receipts"][0].__setitem__("phone_time", "2026-11-01T11:00:00Z"))
        self.assertRejected(run, "phone_clock_outside_allowance")

    def test_missing_receipts_for_a_phone(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("phone_receipts", [r for r in m["phone_receipts"] if r["slot"] != "B"]))
        self.assertRejected(run, "missing_phone_receipts")

    def test_transfer_digest_mismatch(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["transfers"][0].__setitem__("received_sha256", H("corrupt")))
        self.assertRejected(run, "transfer_mismatch")

    def test_missing_boundary_size(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("transfers", [t for t in m["transfers"] if t["size"] != 4096]))
        self.assertRejected(run, "transfer_mismatch")

    def test_rejected_size_with_gatt_writes(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["rejections"][-1].__setitem__("gatt_writes", 1))
        self.assertRejected(run, "rejection_wrote")

    def test_missing_rejection_size(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("rejections", [r for r in m["rejections"] if r["size"] != 5120]))
        self.assertRejected(run, "rejection_wrote")


class TimestampActionTests(EvidenceTestBase):
    def test_missing_required_action(self):
        run = self.run_dir(WIN_LIFE)
        self.edit(run, lambda m: m.__setitem__("actions", [a for a in m["actions"] if a["name"] != "literal_port_disconnect"]))
        self.assertRejected(run, "missing_action")

    def test_action_without_timestamps(self):
        run = self.run_dir(WIN_LIFE)
        self.edit(run, lambda m: m["actions"][3].pop("started_at"))
        self.assertRejected(run, "bad_timestamp")

    def test_action_with_garbage_timestamp(self):
        run = self.run_dir(WIN_LIFE)
        self.edit(run, lambda m: m["actions"][3].__setitem__("ended_at", "yesterday"))
        self.assertRejected(run, "bad_timestamp")

    def test_timestamp_without_utc_marker(self):
        run = self.run_dir(WIN_LIFE)
        self.edit(run, lambda m: m["actions"][0].__setitem__("started_at", "2026-11-01T10:00:00"))
        self.assertRejected(run, "bad_timestamp")

    def test_action_ends_before_it_starts(self):
        run = self.run_dir(WIN_LIFE)
        self.edit(run, lambda m: m["actions"][2].__setitem__("ended_at", "2026-11-01T09:00:00Z"))
        self.assertRejected(run, "bad_timestamp")

    def test_actions_out_of_order(self):
        run = self.run_dir(WIN_LIFE)

        def swap(m):
            a = m["actions"]
            a[5]["started_at"], a[5]["ended_at"] = "2026-11-01T09:00:00Z", "2026-11-01T09:00:01Z"

        self.edit(run, swap)
        self.assertRejected(run, "bad_timestamp")

    def test_action_failed_result(self):
        run = self.run_dir(WIN_LIFE)
        self.edit(run, lambda m: m["actions"][4].__setitem__("result", "error"))
        self.assertRejected(run, "action_failed")

    def test_trigger_before_bytes_proven(self):
        run = self.run_dir(WIN_LIFE)

        def reorder(m):
            by = {a["name"]: a for a in m["actions"]}
            by["literal_port_disconnect"]["seq"], by["prove_bytes_both_links"]["seq"] = (
                by["prove_bytes_both_links"]["seq"], by["literal_port_disconnect"]["seq"])
            m["actions"].sort(key=lambda a: a["seq"])

        self.edit(run, reorder)
        self.assertRejected(run, "bad_timestamp")


class CallbackControlTests(EvidenceTestBase):
    def test_error_case_without_callbacks(self):
        run = self.run_dir(MAC_ERR)
        self.edit(run, lambda m: m.__setitem__("callbacks", [m["callbacks"][0]]))
        self.assertRejected(run, "missing_callback_code")

    def test_error_case_with_wrong_code(self):
        run = self.run_dir(MAC_ERR)
        self.edit(run, lambda m: m["callbacks"][1].__setitem__("code", "internal"))
        self.assertRejected(run, "missing_callback_code")

    def test_error_case_without_native_code(self):
        run = self.run_dir(MAC_ERR)
        self.edit(run, lambda m: m["callbacks"][1].pop("native_code"))
        self.assertRejected(run, "missing_callback_code")

    def test_error_case_without_classification(self):
        run = self.run_dir(MAC_ERR)
        self.edit(run, lambda m: m["callbacks"][1].pop("classification"))
        self.assertRejected(run, "missing_callback_code")

    def test_error_case_without_recoverability(self):
        run = self.run_dir(MAC_ERR)
        self.edit(run, lambda m: m["callbacks"][1].pop("recoverability"))
        self.assertRejected(run, "missing_callback_code")

    def test_callback_without_timestamp(self):
        run = self.run_dir(MAC_ERR)
        self.edit(run, lambda m: m["callbacks"][1].pop("t"))
        self.assertRejected(run, "bad_timestamp")

    def test_missing_required_control(self):
        run = self.run_dir(WIN_LIFE)
        self.edit(run, lambda m: m["controls"].pop(0))
        self.assertRejected(run, "missing_control")

    def test_failed_control(self):
        run = self.run_dir(WIN_LIFE)
        self.edit(run, lambda m: m["controls"][0].__setitem__("result", "fail"))
        result = self.assertRejected(run, "control_failed")
        self.assertEqual(result.verdict, "FAIL")

    def test_control_without_evidence_ref(self):
        run = self.run_dir(WIN_LIFE)
        self.edit(run, lambda m: m["controls"][0].pop("evidence_ref"))
        self.assertRejected(run, "missing_control")

    def test_missing_hygiene_observation(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("observations", m["observations"][1:]))
        self.assertRejected(run, "missing_observation")

    def test_observed_window_fails(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["observations"][0].__setitem__("result", "observed"))
        result = self.assertRejected(run, "observation_violation")
        self.assertEqual(result.verdict, "FAIL")

    def test_process_exit_code_must_match_direct_diagnostic(self):
        run = self.run_dir(WIN_DIRECT)
        self.edit(run, lambda m: m["process"].__setitem__("exit_code", 0))
        self.assertRejected(run, "unexpected_exit_code")

    def test_process_exit_missing(self):
        run = self.run_dir(WIN_DIRECT)
        self.edit(run, lambda m: m.pop("process"))
        self.assertRejected(run, "unexpected_exit_code")


class CaptureCatalogAdjudicationTests(EvidenceTestBase):
    def test_no_captures(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("captures", []))
        self.assertRejected(run, "capture_missing")

    def test_required_capture_kind_missing(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("captures", [c for c in m["captures"] if c["kind"] != "phone_telemetry"]))
        self.assertRejected(run, "capture_missing")

    def test_capture_file_missing(self):
        run = self.run_dir(WIN_AB)
        (run / "captures" / "host_events.bin").unlink()
        seal(run)
        self.assertRejected(run, "capture_missing")

    def test_capture_content_tampered(self):
        run = self.run_dir(WIN_AB)
        write(run / "captures" / "host_events.bin", "tampered")
        seal(run)  # catalog re-sealed; manifest hash still the old one
        self.assertRejected(run, "capture_hash_mismatch")

    def test_capture_path_escape(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["captures"][0].__setitem__("path", "../outside.bin"))
        self.assertRejected(run, "unsafe_path")

    def test_missing_catalog(self):
        run = self.run_dir(WIN_AB)
        (run / "SHA256SUMS").unlink()
        self.assertRejected(run, "catalog_missing")

    def test_catalog_hash_unbound(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.pop("checksum_catalog"), reseal=False)
        self.assertRejected(run, "catalog_missing")

    def test_catalog_tampered_after_seal(self):
        run = self.run_dir(WIN_AB)
        text = (run / "SHA256SUMS").read_text().replace("captures/host_events.bin", "captures/elsewhere.bin")
        write(run / "SHA256SUMS", text)
        self.assertRejected(run, "catalog_mismatch")

    def test_catalog_omits_a_file(self):
        run = self.run_dir(WIN_AB)
        lines = [l for l in (run / "SHA256SUMS").read_text().splitlines() if "hygiene" not in l]
        write(run / "SHA256SUMS", "\n".join(lines) + "\n")
        m = json.loads((run / "run.json").read_text())
        m["checksum_catalog"]["sha256"] = sha((run / "SHA256SUMS").read_bytes())
        write(run / "run.json", json.dumps(m))
        adj = json.loads((run / "adjudication.json").read_text())
        adj["run_manifest_sha256"] = sha((run / "run.json").read_bytes())
        write(run / "adjudication.json", json.dumps(adj))
        self.assertRejected(run, "catalog_mismatch")

    def test_unlisted_extra_file(self):
        run = self.run_dir(WIN_AB)
        write(run / "captures" / "stray.bin", "extra")
        # extra file present, catalog not updated
        self.assertRejected(run, "catalog_mismatch")

    def test_missing_adjudication(self):
        run = self.run_dir(WIN_AB)
        (run / "adjudication.json").unlink()
        self.assertRejected(run, "adjudication_missing")

    def test_adjudication_verdict_not_pass(self):
        run = self.run_dir(WIN_AB)
        adj = json.loads((run / "adjudication.json").read_text())
        adj["verdict"] = "FAIL"
        write(run / "adjudication.json", json.dumps(adj))
        self.assertRejected(run, "adjudication_not_pass")

    def test_adjudication_not_bound_to_manifest(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("operator", "someone"), reseal=False)
        self.assertRejected(run, "adjudication_mismatch")

    def test_adjudicator_must_differ_from_operator(self):
        run = self.run_dir(WIN_AB)
        adj = json.loads((run / "adjudication.json").read_text())
        adj["adjudicator"] = "operator-synthetic"
        write(run / "adjudication.json", json.dumps(adj))
        seal(run)
        self.assertRejected(run, "adjudication_not_separate")

    def test_adjudication_without_time(self):
        run = self.run_dir(WIN_AB)
        adj = json.loads((run / "adjudication.json").read_text())
        adj.pop("at")
        write(run / "adjudication.json", json.dumps(adj))
        seal(run)
        self.assertRejected(run, "bad_timestamp")

    def test_declared_not_run_never_passes(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("declared_status", "NOT RUN"))
        result = self.validate(run)
        self.assertEqual(result.verdict, "NOT RUN")

    def test_declared_blocked_requires_blocker(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("declared_status", "BLOCKED"))
        result = self.assertRejected(run, "blocker_missing")
        self.assertEqual(result.verdict, "BLOCKED")

    def test_declared_blocked_with_blocker_stays_blocked(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.update({"declared_status": "BLOCKED", "blocker": "no physical Windows host leased"}))
        self.assertEqual(self.validate(run).verdict, "BLOCKED")

    def test_unknown_case(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("case_id", "windows.AB.invented"))
        self.assertRejected(run, "unknown_case")

    def test_wrong_schema(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("schema", "other/1"))
        self.assertRejected(run, "bad_manifest")

    def test_unparseable_manifest(self):
        run = self.run_dir(WIN_AB)
        write(run / "run.json", "{not json")
        self.assertRejected(run, "bad_manifest")

    def test_missing_run_dir(self):
        result = self.v.validate_run(Path("/nonexistent/veles/run"), cases_path=CASES_PATH)
        self.assertNotEqual(result.verdict, "PASS")


class WrapperAndFailedSetupTests(EvidenceTestBase):
    def test_windows_chrome_case_needs_wrapper_exception_record(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.pop("wrapper_exception"))
        self.assertRejected(run, "wrapper_exception_missing")

    def test_wrapper_exception_must_be_narrow(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["wrapper_exception"].update({"used": True, "scope": "all_windows"}))
        self.assertRejected(run, "wrapper_exception_overbroad")

    def test_wrapper_exception_used_requires_capture(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["wrapper_exception"].update({"used": True}))
        self.assertRejected(run, "wrapper_exception_overbroad")

    def test_wrapper_exception_valid_when_narrow_and_captured(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["wrapper_exception"].update({"used": True, "capture_ref": "hygiene", "visible": False}))
        self.assertEqual(self.validate(run).verdict, "PASS")

    def test_wrapper_exception_not_applicable_on_macos(self):
        run = self.run_dir(MAC_AB)
        self.edit(run, lambda m: m.__setitem__("wrapper_exception", {"used": True, "scope": "windows_chrome_never_visible_wrapper_only",
                                                                    "capture_ref": "hygiene", "visible": False}))
        self.assertRejected(run, "wrapper_exception_overbroad")

    def test_failed_setups_key_required(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.pop("failed_setups"))
        self.assertRejected(run, "failed_setups_missing")

    def test_failed_setup_must_reference_preserved_capture(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("failed_setups", [{"id": "s1", "reason": "phone B did not advertise", "capture_ref": "gone"}]))
        self.assertRejected(run, "failed_setup_unpreserved")

    def test_preserved_failed_setup_is_accepted(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("failed_setups", [{"id": "s1", "reason": "phone B did not advertise", "capture_ref": "host_events"}]))
        self.assertEqual(self.validate(run).verdict, "PASS")


class StaticDefinitionTests(unittest.TestCase):
    """The case and matrix definitions themselves must keep every physical case unproven."""

    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(CASES_PATH.read_text())["cases"]
        cls.matrix = json.loads(MATRIX_PATH.read_text())

    def test_case_ids_encode_platform_phones_action_and_are_unique(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        for c in self.cases:
            self.assertEqual(c["id"], f"{c['platform']}.{c['phones']}.{c['action']}")
            self.assertIn(c["platform"], ("windows", "macos"))
            self.assertIn(c["phones"], ("none", "A", "AB"))

    def test_both_platforms_cover_the_same_actions(self):
        by = {p: {c["action"] for c in self.cases if c["platform"] == p} for p in ("windows", "macos")}
        self.assertEqual(by["windows"], by["macos"])
        for needed in ("ready-hygiene", "scanning-hygiene", "transfer-boundaries", "teardown-hygiene",
                       "lifecycle-port-disconnect", "lifecycle-tab-close", "lifecycle-pipe-closure",
                       "lifecycle-explicit-shutdown", "direct-eof-clean", "direct-eof-truncated",
                       "error-radio-off", "error-no-adapter", "error-service-denial", "per-phone-failure"):
            self.assertIn(needed, by["windows"])

    def test_direct_launch_diagnostics_are_not_chrome_lifecycle_cases(self):
        for c in self.cases:
            if c["action"].startswith("direct-eof"):
                self.assertEqual(c["kind"], "direct")
                self.assertFalse(c["requires_chrome"])
            if c["action"].startswith("lifecycle-"):
                self.assertEqual(c["kind"], "chrome")
                self.assertTrue(c["requires_chrome"])
                self.assertEqual(c["phones"], "AB")
                self.assertTrue(c["active_scans"])
                self.assertEqual(c["min_links"], 2)

    def test_matrix_has_a_row_per_case_and_none_is_pass(self):
        rows = {r["id"]: r for r in self.matrix["rows"]}
        self.assertEqual(set(rows), {c["id"] for c in self.cases})
        for r in rows.values():
            self.assertIn(r["status"], ("NOT RUN", "BLOCKED"))
            self.assertEqual(r["evidence"], [])
            self.assertTrue(r["blocker"])
        self.assertEqual(self.matrix["decision"], "OPEN")
        self.assertFalse(self.matrix["physical_acceptance_complete"])

    def test_matrix_defers_production_identity_and_distinguishes_binaries(self):
        text = json.dumps(self.matrix)
        self.assertIn("production identity", text)
        self.assertIn("TCC", text)
        self.assertIn("test_manifest", text)
        self.assertIn("wrapper_exception", text)

    def test_failed_setups_are_indexed_to_existing_historical_evidence(self):
        self.assertTrue(self.matrix["failed_setups"])
        for f in self.matrix["failed_setups"]:
            self.assertTrue((ROOT / f["path"]).exists(), f["path"])
            self.assertTrue(f["historical"])
            self.assertFalse(f["reinterpret"])

    def test_matrix_check_cross_validates_against_cases(self):
        pm = load_module("physical-matrix.py", "physical_matrix")
        self.assertEqual(pm.check_matrix(CASES_PATH, MATRIX_PATH), [])
        broken = json.loads(MATRIX_PATH.read_text())
        broken["rows"][0]["status"] = "PASS"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "matrix.json"
            p.write_text(json.dumps(broken))
            self.assertNotEqual(pm.check_matrix(CASES_PATH, p), [])


class PhysicalMatrixDriverTests(unittest.TestCase):
    """physical-matrix.py must validate identities before executing and never invent a result."""

    @classmethod
    def setUpClass(cls):
        cls.pm = load_module("physical-matrix.py", "physical_matrix_driver")

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(HERE / "physical-matrix.py"), *args],
                              capture_output=True, text=True, timeout=60)

    def test_relative_host_path_refused(self):
        r = self.run_cli("--case", "windows.AB.transfer-boundaries", "--host", "veles-native-host", "--output", str(self.tmp / "o"))
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertFalse((self.tmp / "o" / "run.json").exists())

    def test_missing_host_refused_before_any_output_claims_pass(self):
        r = self.run_cli("--case", "windows.AB.transfer-boundaries", "--host", str(self.tmp / "nope.exe"), "--output", str(self.tmp / "o"))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('"PASS"', r.stdout)

    def test_scaffold_binary_refused(self):
        exe = self.tmp / "otp23-host.exe"
        exe.write_bytes(b"x")
        r = self.run_cli("--case", "windows.AB.transfer-boundaries", "--host", str(exe), "--output", str(self.tmp / "o"))
        self.assertEqual(r.returncode, 3, r.stderr)

    def test_unknown_case_refused(self):
        exe = self.tmp / "veles-native-host.exe"
        exe.write_bytes(b"x")
        r = self.run_cli("--case", "nonsense", "--host", str(exe), "--output", str(self.tmp / "o"))
        self.assertEqual(r.returncode, 3, r.stderr)

    def test_unsupported_host_platform_writes_blocked_record_that_validates_as_blocked(self):
        case = "windows.AB.transfer-boundaries" if sys.platform != "win32" else "macos.AB.transfer-boundaries"
        exe = self.tmp / ("veles-native-host.exe" if case.startswith("windows") else "veles-native-host")
        exe.write_bytes(b"not a real binary")
        out = self.tmp / "o"
        r = self.run_cli("--case", case, "--host", str(exe), "--output", str(out))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        manifest = json.loads((out / "run.json").read_text())
        self.assertEqual(manifest["declared_status"], "BLOCKED")
        self.assertTrue(manifest["blocker"])
        self.assertEqual(manifest["binary"]["sha256"], sha(b"not a real binary"))
        self.assertEqual(manifest["captures"], [])
        v = load_module("validate-evidence.py", "validate_evidence_for_driver")
        result = v.validate_run(out, cases_path=CASES_PATH)
        self.assertEqual(result.verdict, "BLOCKED")
        self.assertNotEqual(result.verdict, "PASS")

    def test_existing_output_directory_not_overwritten(self):
        exe = self.tmp / "veles-native-host.exe"
        exe.write_bytes(b"x")
        out = self.tmp / "o"
        out.mkdir()
        (out / "keep.txt").write_text("evidence")
        r = self.run_cli("--case", "windows.AB.transfer-boundaries", "--host", str(exe), "--output", str(out))
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual((out / "keep.txt").read_text(), "evidence")

    def test_list_and_check_commands(self):
        r = self.run_cli("--list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("windows.AB.lifecycle-port-disconnect", r.stdout)
        r = self.run_cli("--check-matrix")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_no_driver_claims_pass(self):
        for plat, driver in self.pm.DRIVERS.items():
            self.assertFalse(driver.executable, plat)
            self.assertTrue(driver.gaps, plat)

    def test_never_runs_without_a_driver_even_on_matching_platform(self):
        # Even if the host platform matched, an unimplemented driver must block, not pass.
        case = {"id": "x", "platform": self.pm.host_platform(), "kind": "chrome"}
        status, reasons = self.pm.preflight_driver(case)
        self.assertEqual(status, "BLOCKED")
        self.assertTrue(reasons)


def rebind_manifest(run, doc):
    """Replace the REAL test manifest file with `doc` and re-hash/re-seal everything (a rehashing attacker)."""
    raw = doc if isinstance(doc, str) else json.dumps(doc)
    write(run / "test-manifest.json", raw)
    m = json.loads((run / "run.json").read_text())
    m["test_manifest"]["sha256"] = sha(raw.encode())
    write(run / "run.json", json.dumps(m, indent=2))
    seal(run)


def good_manifest(run):
    m = json.loads((run / "run.json").read_text())
    return json.loads((run / "test-manifest.json").read_text()), m


class RehashedManifestTests(EvidenceTestBase):
    """The actual manifest bytes are parsed and bound; hashes/catalog/adjudication regenerated around a bad file must still fail."""

    def bad_manifest(self, mutate):
        run = self.run_dir(WIN_AB)
        doc, _ = good_manifest(run)
        mutate(doc)
        rebind_manifest(run, doc)
        return run

    def test_control_rehash_of_unchanged_manifest_passes(self):
        run = self.bad_manifest(lambda d: None)
        self.assertEqual(self.validate(run).verdict, "PASS")

    def test_wildcard_origin_rehashed(self):
        run = self.bad_manifest(lambda d: d.__setitem__("allowed_origins", ["chrome-extension://*/"]))
        self.assertRejected(run, "test_manifest_mismatch")

    def test_two_origins_rehashed(self):
        run = self.bad_manifest(lambda d: d["allowed_origins"].append("chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/"))
        self.assertRejected(run, "test_manifest_mismatch")

    def test_other_exact_origin_rehashed(self):
        run = self.bad_manifest(lambda d: d.__setitem__("allowed_origins", ["chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/"]))
        self.assertRejected(run, "test_manifest_mismatch")

    def test_manifest_points_at_other_executable_rehashed(self):
        run = self.bad_manifest(lambda d: d.__setitem__("path", "/abs/otp23-host.exe"))
        self.assertRejected(run, "test_manifest_mismatch")

    def test_manifest_points_at_same_name_different_directory(self):
        run = self.bad_manifest(lambda d: d.__setitem__("path", "/elsewhere/veles-native-host.exe"))
        self.assertRejected(run, "test_manifest_mismatch")

    def test_manifest_type_not_stdio_rehashed(self):
        run = self.bad_manifest(lambda d: d.__setitem__("type", "pipe"))
        self.assertRejected(run, "test_manifest_mismatch")

    def test_manifest_extra_key_rehashed(self):
        run = self.bad_manifest(lambda d: d.__setitem__("args", ["--sim"]))
        self.assertRejected(run, "test_manifest_mismatch")

    def test_manifest_not_json_rehashed(self):
        run = self.run_dir(WIN_AB)
        rebind_manifest(run, "{not json")
        self.assertRejected(run, "test_manifest_mismatch")

    def test_manifest_duplicate_keys_rehashed(self):
        run = self.run_dir(WIN_AB)
        text = (run / "test-manifest.json").read_text()
        rebind_manifest(run, text[:-1] + ', "type": "pipe"}')
        self.assertRejected(run, "test_manifest_mismatch")

    def test_manifest_not_an_object_rehashed(self):
        run = self.run_dir(WIN_AB)
        rebind_manifest(run, "[]")
        self.assertRejected(run, "test_manifest_mismatch")


class FabricatedIdentityTests(EvidenceTestBase):
    """A run may be perfectly self-consistent and still lie about the checkout, evaluator or executable."""

    def test_no_anchor_never_passes(self):
        run = self.run_dir(WIN_AB)
        result = self.v.validate_run(run, cases_path=CASES_PATH)
        self.assertNotEqual(result.verdict, "PASS")
        self.assertIn("provenance_unanchored", [d[0] for d in result.defects])

    def test_incomplete_anchor_never_passes(self):
        run = self.run_dir(WIN_AB)
        for key in ANCHOR:
            partial = {k: v for k, v in ANCHOR.items() if k != key}
            result = self.v.validate_run(run, cases_path=CASES_PATH, anchor=partial)
            self.assertNotEqual(result.verdict, "PASS", key)

    def test_consistent_fake_revision(self):
        run = self.run_dir(WIN_AB)

        def fake(m):
            for blk, key in (("source", "revision"), ("evaluator", "revision"), ("binary", "built_from_source_revision")):
                m[blk][key] = "c" * 40

        self.edit(run, fake)
        self.assertRejected(run, "source_mismatch")

    def test_fake_tree_hash_consistently_claimed(self):
        run = self.run_dir(WIN_AB)
        fake = H("a tree that never existed")

        def f(m):
            m["source"]["tree_sha256"] = fake
            m["binary"]["built_from_tree_sha256"] = fake

        self.edit(run, f)
        self.assertRejected(run, "source_mismatch")

    def test_binary_attested_from_other_tree(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["binary"].__setitem__("built_from_tree_sha256", H("other tree")))
        self.assertRejected(run, "binary_source_mismatch")

    def test_fake_evaluator_hashes(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["evaluator"]["files"].__setitem__("scripts/validate-evidence.py", H("a weaker validator")))
        self.assertRejected(run, "evaluator_mismatch")

    def test_evaluator_files_with_extra_or_missing_names(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["evaluator"]["files"].__setitem__("scripts/extra.py", H("x")))
        self.assertRejected(run, "evaluator_mismatch")
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["evaluator"]["files"].pop("scripts/physical-matrix.py"))
        self.assertRejected(run, "evaluator_mismatch")

    def test_fake_binary_hash_consistent_everywhere_in_the_run(self):
        run = self.run_dir(WIN_AB)
        fake = H("not the executable that ran")

        def f(m):
            m["binary"]["sha256"] = fake
            m["test_manifest"]["host_sha256"] = fake

        self.edit(run, f)
        self.assertRejected(run, "binary_hash_mismatch")

    def test_cases_file_not_the_anchored_one(self):
        run = self.run_dir(WIN_AB)
        with tempfile.TemporaryDirectory() as d:
            doc = json.loads(CASES_PATH.read_text())
            doc["cases"] = [c for c in doc["cases"] if c["id"] == WIN_AB]
            doc["cases"][0]["required_actions"] = []
            weak = Path(d) / "cases.json"
            weak.write_text(json.dumps(doc))
            result = self.v.validate_run(run, cases_path=weak, anchor=ANCHOR)
        self.assertNotEqual(result.verdict, "PASS")
        self.assertIn("evaluator_mismatch", [x[0] for x in result.defects])

    def test_review_reference_required(self):
        run = self.run_dir(WIN_AB)
        adj = json.loads((run / "adjudication.json").read_text())
        adj.pop("review_ref")
        write(run / "adjudication.json", json.dumps(adj))
        seal(run)
        self.assertRejected(run, "authenticated_review_missing")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                               "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(cwd), "PATH": "/usr/bin:/bin:/usr/local/bin"})


class RealAnchorTests(unittest.TestCase):
    """compute_anchor on a real throwaway git checkout: untracked bytes, evaluator files and the real executable are bound."""

    @classmethod
    def setUpClass(cls):
        cls.v = load_module("validate-evidence.py", "validate_evidence_anchor")

    def make_repo(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        repo = Path(d.name)
        host = repo / "native-bridge" / "host"
        for rel in self.v.EVALUATOR_FILES:
            write(host / rel, f"synthetic evaluator {rel}\n")
        write(repo / "src.txt", "tracked\n")
        git(repo, "init", "-q")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "synthetic")
        exe = repo / "exe-outside-tree"
        return repo, exe

    def test_untracked_file_bytes_change_the_tree_hash(self):
        repo, exe = self.make_repo()
        base = self.v.source_tree_sha256(repo)
        write(repo / "untracked.txt", "one")
        with_one = self.v.source_tree_sha256(repo)
        write(repo / "untracked.txt", "two")
        with_two = self.v.source_tree_sha256(repo)
        self.assertEqual(len({base, with_one, with_two}), 3)

    def test_tracked_modification_changes_tree_hash_but_revision_stays(self):
        repo, exe = self.make_repo()
        write(exe, b"binary")
        a1 = self.v.compute_anchor(repo, exe)
        write(repo / "src.txt", "edited\n")
        a2 = self.v.compute_anchor(repo, exe)
        self.assertEqual(a1["source_revision"], a2["source_revision"])
        self.assertNotEqual(a1["source_tree_sha256"], a2["source_tree_sha256"])

    def test_evidence_directory_is_excluded_from_the_tree_hash(self):
        repo, _ = self.make_repo()
        base = self.v.source_tree_sha256(repo)
        write(repo / "native-bridge" / "host" / "evidence" / "run1" / "run.json", "{}")
        self.assertEqual(base, self.v.source_tree_sha256(repo))

    def test_anchor_binds_actual_binary_and_evaluator_bytes(self):
        repo, exe = self.make_repo()
        write(exe, b"binary-1")
        a1 = self.v.compute_anchor(repo, exe)
        self.assertEqual(a1["binary_sha256"], sha(b"binary-1"))
        self.assertEqual(set(a1["evaluator_files"]), set(self.v.EVALUATOR_FILES))
        write(exe, b"binary-2")
        write(repo / "native-bridge" / "host" / "scripts" / "validate-evidence.py", "weakened\n")
        a2 = self.v.compute_anchor(repo, exe)
        self.assertNotEqual(a1["binary_sha256"], a2["binary_sha256"])
        self.assertNotEqual(a1["evaluator_files"]["scripts/validate-evidence.py"], a2["evaluator_files"]["scripts/validate-evidence.py"])

    def test_real_repo_anchor_is_computable_and_sees_untracked_evaluator_files(self):
        anchor = self.v.compute_anchor(ROOT, None)
        self.assertRegex(anchor["source_revision"], r"^[0-9a-f]{40}$")
        self.assertRegex(anchor["source_tree_sha256"], r"^[0-9a-f]{64}$")
        self.assertIsNone(anchor["binary_sha256"])
        for rel, digest in anchor["evaluator_files"].items():
            self.assertEqual(digest, sha((HOST / rel).read_bytes()))


class SecurePathTests(EvidenceTestBase):
    """One routine for every reference: relative, normalized, no symlink anywhere beneath the run, regular files only."""

    def outside(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_symlinked_capture_ancestor(self):
        run = self.run_dir(WIN_AB)
        out = self.outside()
        (run / "captures").rename(out / "captures")
        (run / "captures").symlink_to(out / "captures")
        result = self.assertRejected(run, "unsafe_path")
        self.assertEqual(result.verdict, "FAIL")

    def test_symlinked_capture_file(self):
        run = self.run_dir(WIN_AB)
        out = self.outside()
        target = run / "captures" / "host_events.bin"
        (out / "x.bin").write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(out / "x.bin")
        self.assertRejected(run, "unsafe_path")

    def test_nested_symlinked_ancestor_inside_run(self):
        run = self.run_dir(WIN_AB)
        (run / "real").mkdir()
        (run / "link").symlink_to(run / "real")
        def move(m):
            c = m["captures"][0]
            write(run / "real" / "c.bin", (run / c["path"]).read_bytes())
            (run / c["path"]).unlink()
            c["path"] = "link/c.bin"
        self.edit(run, move)
        self.assertRejected(run, "unsafe_path")

    def test_traversal_and_absolute_and_odd_capture_paths(self):
        for bad in ("../x.bin", "captures/../captures/host_events.bin", "/etc/hostname", "C:/Windows/x", "captures\\host_events.bin",
                    "captures//host_events.bin", "./captures/host_events.bin", "", "captures/host_events.bin\0"):
            with self.subTest(path=bad):
                run = self.run_dir(WIN_AB)
                self.edit(run, lambda m, b=bad: m["captures"][0].__setitem__("path", b))
                self.assertRejected(run, "unsafe_path")

    def test_absolute_path_to_a_valid_external_file_is_not_followed(self):
        run = self.run_dir(WIN_AB)
        out = self.outside()
        data = (run / "captures" / "host_events.bin").read_bytes()
        (out / "ext.bin").write_bytes(data)
        self.edit(run, lambda m: m["captures"][0].__setitem__("path", str(out / "ext.bin")))
        self.assertRejected(run, "unsafe_path")

    def test_test_manifest_path_traversal_and_symlink(self):
        run = self.run_dir(WIN_AB)
        out = self.outside()
        (out / "m.json").write_bytes((run / "test-manifest.json").read_bytes())
        self.edit(run, lambda m: m["test_manifest"].__setitem__("path", str(out / "m.json")))
        self.assertRejected(run, "unsafe_path")
        run = self.run_dir(WIN_AB)
        data = (run / "test-manifest.json").read_bytes()
        (out / "m2.json").write_bytes(data)
        (run / "test-manifest.json").unlink()
        (run / "test-manifest.json").symlink_to(out / "m2.json")
        seal(run)
        self.assertRejected(run, "unsafe_path")

    def test_adjudication_outside_run_or_symlinked(self):
        run = self.run_dir(WIN_AB)
        out = self.outside()
        adj = (run / "adjudication.json").read_bytes()
        (out / "a.json").write_bytes(adj)
        self.edit(run, lambda m: m["adjudication"].__setitem__("path", str(out / "a.json")))
        self.assertRejected(run, "unsafe_path")
        run = self.run_dir(WIN_AB)
        (out / "b.json").write_bytes((run / "adjudication.json").read_bytes())
        (run / "adjudication.json").unlink()
        (run / "adjudication.json").symlink_to(out / "b.json")
        self.assertRejected(run, "unsafe_path")

    def test_symlinked_run_json_catalog_and_run_dir(self):
        out = self.outside()
        run = self.run_dir(WIN_AB)
        (out / "run.json").write_bytes((run / "run.json").read_bytes())
        (run / "run.json").unlink()
        (run / "run.json").symlink_to(out / "run.json")
        result = self.validate(run)
        self.assertNotEqual(result.verdict, "PASS")
        self.assertIn("unsafe_path", [d[0] for d in result.defects])
        run = self.run_dir(WIN_AB)
        (out / "SHA256SUMS").write_bytes((run / "SHA256SUMS").read_bytes())
        (run / "SHA256SUMS").unlink()
        (run / "SHA256SUMS").symlink_to(out / "SHA256SUMS")
        self.assertRejected(run, "unsafe_path")
        run = self.run_dir(WIN_AB)
        link = out / "runlink"
        link.symlink_to(run)
        result = self.validate(link)
        self.assertNotEqual(result.verdict, "PASS")

    def test_non_regular_file_in_run_dir(self):
        import os
        run = self.run_dir(WIN_AB)
        os.mkfifo(run / "captures" / "pipe.bin")
        self.assertRejected(run, "unsafe_path")

    def test_stray_symlink_in_run_dir(self):
        run = self.run_dir(WIN_AB)
        (run / "captures" / "stray").symlink_to("/etc/hostname")
        self.assertRejected(run, "unsafe_path")

    def test_catalog_entry_with_traversal(self):
        run = self.run_dir(WIN_AB)
        text = (run / "SHA256SUMS").read_text() + f"{H('x')}  ../escape.bin\n"
        write(run / "SHA256SUMS", text)
        m = json.loads((run / "run.json").read_text())
        m["checksum_catalog"]["sha256"] = sha(text.encode())
        write(run / "run.json", json.dumps(m))
        adj = json.loads((run / "adjudication.json").read_text())
        adj["run_manifest_sha256"] = sha((run / "run.json").read_bytes())
        write(run / "adjudication.json", json.dumps(adj))
        self.assertRejected(run, "unsafe_path")


class ReceiptBindingTests(EvidenceTestBase):
    def test_receipt_digest_contradicts_transfer(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["phone_receipts"][0].__setitem__("sha256", H("something else")))
        self.assertRejected(run, "receipt_digest_mismatch")

    def test_receipt_digest_missing(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["phone_receipts"][3].pop("sha256"))
        self.assertRejected(run, "receipt_digest_mismatch")

    def test_receipt_digest_of_the_other_phones_transfer(self):
        run = self.run_dir(WIN_AB)

        def swap(m):
            a = next(r for r in m["phone_receipts"] if r["slot"] == "A" and r["size"] == 508)
            b = next(r for r in m["phone_receipts"] if r["slot"] == "B" and r["size"] == 240)
            a["sha256"], b["sha256"] = b["sha256"], a["sha256"]

        self.edit(run, swap)
        self.assertRejected(run, "receipt_digest_mismatch")

    def test_receipt_for_a_size_never_transferred(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["phone_receipts"].append(dict(m["phone_receipts"][0], size=777)))
        self.assertRejected(run, "receipt_digest_mismatch")

    def test_receipt_only_proves_the_declared_size(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["phone_receipts"][0].__setitem__("size", 178))
        self.assertRejected(run, "receipt_digest_mismatch")


class NumericBoundaryTests(EvidenceTestBase):
    def test_fixture_resolves_different_numbers_per_link(self):
        run = self.run_dir(WIN_AB)
        m = json.loads((run / "run.json").read_text())
        by = {(t["slot"], t["label"]): t["size"] for t in m["transfers"]}
        self.assertEqual((by[("A", "chunk-1")], by[("A", "chunk")], by[("A", "chunk+1")], by[("A", "multiple-of-chunk")]), (507, 508, 509, 1016))
        self.assertEqual((by[("B", "chunk-1")], by[("B", "chunk")], by[("B", "chunk+1")], by[("B", "multiple-of-chunk")]), (239, 240, 241, 480))
        self.assertEqual(self.validate(run).verdict, "PASS")

    def test_macos_write_limit_and_floor_mtu_links_resolve(self):
        run = self.run_dir(MAC_AB)
        m = json.loads((run / "run.json").read_text())
        by = {(t["slot"], t["label"]): t["size"] for t in m["transfers"]}
        self.assertEqual((by[("A", "chunk")], by[("B", "chunk")], by[("B", "multiple-of-chunk")]), (508, 178, 356))
        self.assertEqual(self.validate(run).verdict, "PASS")

    def test_mtu_517_clamps_to_512_attribute_budget(self):
        self.assertEqual(self.v.payload_budget({"kind": "att_mtu", "value": 517}), (508, None))
        self.assertEqual(self.v.payload_budget({"kind": "att_mtu", "value": 515}), (508, None))
        self.assertEqual(self.v.payload_budget({"kind": "att_mtu", "value": 514}), (507, None))
        self.assertEqual(self.v.payload_budget({"kind": "att_mtu", "value": 185}), (178, None))

    def test_unresolvable_capacities(self):
        for cap in ({"kind": "att_mtu", "value": 184}, {"kind": "att_mtu", "value": 518}, {"kind": "max_write_without_response", "value": 511},
                    {"kind": "att_mtu", "value": "517"}, {"kind": "att_mtu", "value": True}, {"kind": "weird", "value": 300}, {}, None):
            with self.subTest(cap=cap):
                payload, problem = self.v.payload_budget(cap)
                self.assertIsNone(payload)
                self.assertTrue(problem)

    def test_boundary_sizes_from_the_wrong_mtu_are_rejected(self):
        run = self.run_dir(WIN_AB)

        def wrong(m):  # pretend slot A negotiated 185 but record sizes for 517
            next(l for l in m["links"] if l["slot"] == "A")["capacity"] = {"kind": "att_mtu", "value": 185}

        self.edit(run, wrong)
        self.assertRejected(run, "transfer_mismatch")

    def test_recorded_size_differs_from_derived(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: next(t for t in m["transfers"] if t["slot"] == "A" and t["label"] == "chunk").__setitem__("size", 507))
        self.assertRejected(run, "transfer_mismatch")

    def test_recorded_chunk_differs_from_derived(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: next(t for t in m["transfers"] if t["slot"] == "B" and t["label"] == "chunk+1").__setitem__("chunk", 508))
        self.assertRejected(run, "transfer_mismatch")

    def test_recorded_payload_budget_wrong(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["links"][0].__setitem__("payload_budget", 512))
        self.assertRejected(run, "transfer_mismatch")

    def test_missing_link_data(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("links", [l for l in m["links"] if l["slot"] != "B"]))
        self.assertRejected(run, "link_capacity_unresolved")

    def test_link_data_without_capture(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["links"][0].__setitem__("capture_ref", "nope"))
        self.assertRejected(run, "link_capacity_unresolved")

    def test_refused_link_capacity_cannot_pass(self):
        for cap in ({"kind": "max_write_without_response", "value": 400}, {"kind": "att_mtu", "value": 100}):
            run = self.run_dir(WIN_AB)
            self.edit(run, lambda m, c=cap: m["links"][1].__setitem__("capacity", c))
            self.assertRejected(run, "link_capacity_unresolved")

    def test_label_missing_from_transfers(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["transfers"][0].pop("label"))
        self.assertRejected(run, "transfer_mismatch")

    def test_literal_symbolic_size_is_not_a_number(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m["transfers"][0].__setitem__("size", "chunk-1"))
        self.assertRejected(run, "transfer_mismatch")

    def test_boolean_is_not_a_size(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: next(t for t in m["transfers"] if t["label"] == "1").__setitem__("size", True))
        self.assertRejected(run, "transfer_mismatch")


class MalformedTypeTests(EvidenceTestBase):
    """Wrong types never crash the validator and never PASS: a structured verdict with (code, message) defects."""

    WEIRD = (None, 5, -1, 1.5, True, "x", "", [], {}, [1], [None], ["x"], [[]], {"a": 1}, float("inf"))

    def assertStructured(self, result):
        self.assertIn(result.verdict, ("PASS", "FAIL", "BLOCKED", "NOT RUN"))
        for d in result.defects:
            self.assertEqual(len(d), 2)
            self.assertTrue(all(isinstance(x, str) for x in d))

    def test_specific_type_confusions(self):
        cases = {
            "source_scalar": lambda m: m.__setitem__("source", 5),
            "source_string": lambda m: m.__setitem__("source", "abc"),
            "source_list": lambda m: m.__setitem__("source", [REV]),
            "seq_string": lambda m: m["actions"][2].__setitem__("seq", "three"),
            "seq_none": lambda m: m["actions"][2].__setitem__("seq", None),
            "seq_missing": lambda m: m["actions"][2].pop("seq"),
            "action_scalar": lambda m: m["actions"].__setitem__(1, 7),
            "clock_offset_string": lambda m: m["phones"][0].__setitem__("clock_offset_ms", "12"),
            "clock_uncertainty_bool": lambda m: m["phones"][1].__setitem__("clock_uncertainty_ms", True),
            "phones_string": lambda m: m.__setitem__("phones", "AB"),
            "phone_scalar": lambda m: m["phones"].__setitem__(0, 3),
            "receipts_scalar_items": lambda m: m.__setitem__("phone_receipts", [1, None, "x"]),
            "receipt_size_list": lambda m: m["phone_receipts"][0].__setitem__("size", [508]),
            "receipt_time_number": lambda m: m["phone_receipts"][0].__setitem__("phone_time", 12345),
            "transfers_dict": lambda m: m.__setitem__("transfers", {"a": 1}),
            "transfer_label_list": lambda m: m["transfers"][0].__setitem__("label", ["1"]),
            "links_scalar": lambda m: m.__setitem__("links", [1, 2]),
            "link_capacity_string": lambda m: m["links"][0].__setitem__("capacity", "517"),
            "controls_dict": lambda m: m.__setitem__("controls", {}),
            "control_name_list": lambda m: m["controls"].append({"name": ["x"]}),
            "captures_string": lambda m: m.__setitem__("captures", "x"),
            "capture_path_number": lambda m: m["captures"][0].__setitem__("path", 5),
            "capture_size_string": lambda m: m["captures"][0].__setitem__("size", "10"),
            "chrome_list": lambda m: m.__setitem__("chrome", []),
            "chrome_profile_string": lambda m: m["chrome"].__setitem__("profile", "disposable"),
            "binary_none": lambda m: m.__setitem__("binary", None),
            "os_list": lambda m: m.__setitem__("os", []),
            "signing_string": lambda m: m.__setitem__("signing", "unsigned"),
            "failed_setups_string": lambda m: m.__setitem__("failed_setups", "none"),
            "failed_setup_scalar": lambda m: m.__setitem__("failed_setups", [1]),
            "wrapper_string": lambda m: m.__setitem__("wrapper_exception", "narrow"),
            "test_manifest_string": lambda m: m.__setitem__("test_manifest", "x"),
            "case_id_list": lambda m: m.__setitem__("case_id", [WIN_AB]),
            "declared_status_list": lambda m: m.__setitem__("declared_status", ["PASS"]),
            "declared_blocked_blocker_number": lambda m: m.update({"declared_status": "BLOCKED", "blocker": 5}),
        }
        for name, fn in cases.items():
            with self.subTest(name):
                run = self.run_dir(WIN_AB)
                self.edit(run, fn)
                result = self.validate(run)
                self.assertStructured(result)
                self.assertNotEqual(result.verdict, "PASS", name)

    def test_type_confusions_on_case_specific_fields(self):
        cases = (
            (WIN_DIRECT, "process_list", lambda m: m.__setitem__("process", [0]), True),
            (WIN_DIRECT, "exit_code_bool", lambda m: m.__setitem__("process", {"exit_code": False}), True),
            (WIN_DIRECT, "exit_code_string", lambda m: m.__setitem__("process", {"exit_code": "2"}), True),
            (MAC_ERR, "callbacks_number", lambda m: m.__setitem__("callbacks", 5), True),
            (MAC_ERR, "callback_code_list", lambda m: m["callbacks"][1].__setitem__("code", ["scan_failed"]), True),
            (WIN_AB, "catalog_string", lambda m: m.__setitem__("checksum_catalog", "SHA256SUMS"), False),
            (WIN_AB, "catalog_missing_hash", lambda m: m.__setitem__("checksum_catalog", {"sha256": 5}), False),
        )
        for case_id, name, fn, reseal in cases:
            with self.subTest(name):
                run = self.run_dir(case_id)
                self.edit(run, fn, reseal=reseal)
                result = self.validate(run)
                self.assertStructured(result)
                self.assertNotEqual(result.verdict, "PASS", name)

    def test_adjudication_reference_of_wrong_type_is_structured(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("adjudication", "adjudication.json"))
        self.assertStructured(self.validate(run))
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("adjudication", {"path": ["adjudication.json"]}))
        self.assertStructured(self.validate(run))

    def test_every_top_level_key_survives_every_weird_value(self):
        required = {"source", "evaluator", "binary", "signing", "os", "adapter", "account", "chrome", "test_manifest", "phones", "links",
                    "actions", "controls", "observations", "transfers", "rejections", "phone_receipts", "captures", "process",
                    "failed_setups", "adjudication", "checksum_catalog", "case_id", "schema", "declared_status", "wrapper_exception"}
        base = self.run_dir(WIN_AB)
        keys = list(json.loads((base / "run.json").read_text()))
        for key in keys:
            for weird in self.WEIRD:
                with self.subTest(key=key, weird=repr(weird)):
                    run = self.run_dir(WIN_AB)
                    m = json.loads((run / "run.json").read_text())
                    m[key] = weird
                    write(run / "run.json", json.dumps(m, allow_nan=True))
                    result = self.validate(run)  # must not raise
                    self.assertStructured(result)
                    if key in required and weird != m.get(key, object()):
                        self.assertNotEqual(result.verdict, "PASS")

    def test_nested_item_types(self):
        base = self.run_dir(WIN_AB)
        m0 = json.loads((base / "run.json").read_text())
        for key in ("actions", "phones", "links", "transfers", "phone_receipts", "captures", "controls", "observations", "rejections"):
            for i in range(len(m0[key])):
                for field in list(m0[key][i]):
                    for weird in (None, 5, "x", [], {}, True):
                        run = self.run_dir(WIN_AB)
                        m = json.loads((run / "run.json").read_text())
                        m[key][i][field] = weird
                        write(run / "run.json", json.dumps(m))
                        self.assertStructured(self.validate(run))
                if i >= 2:
                    break

    def test_manifest_top_level_not_an_object(self):
        for text in ("[]", "5", "null", '"x"', "{", "", "\xff", '{"schema": "veles-otp24-physical-evidence/1", "schema": "x"}',
                     '{"schema": "veles-otp24-physical-evidence/1", "case_id": NaN}'):
            run = self.run_dir(WIN_AB)
            (run / "run.json").write_bytes(text.encode("latin-1"))
            result = self.validate(run)
            self.assertStructured(result)
            self.assertNotEqual(result.verdict, "PASS", text)

    def test_cli_reports_structured_json_and_never_a_traceback_on_garbage(self):
        run = self.run_dir(WIN_AB)
        self.edit(run, lambda m: m.__setitem__("actions", "garbage"))
        anchor = run.parent / "anchor.json"
        anchor.write_text(json.dumps(ANCHOR))
        r = subprocess.run([sys.executable, "-I", str(HERE / "validate-evidence.py"), str(run), "--anchor", str(anchor), "--cases", str(CASES_PATH)],
                           capture_output=True, text=True, timeout=60)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(json.loads(r.stdout)["verdict"], "BLOCKED" if r.returncode == 2 else "FAIL")
        self.assertNotEqual(r.returncode, 0)


class IndependenceNarrativeTests(EvidenceTestBase):
    def test_cli_pass_is_labelled_not_accepted_and_self_attested(self):
        run = self.run_dir(WIN_AB)
        anchor = run.parent / "anchor.json"
        anchor.write_text(json.dumps(ANCHOR))
        r = subprocess.run([sys.executable, "-I", str(HERE / "validate-evidence.py"), str(run), "--anchor", str(anchor)],
                           capture_output=True, text=True, timeout=60)
        out = json.loads(r.stdout)
        self.assertEqual((r.returncode, out["verdict"]), (0, "PASS"), r.stdout)
        self.assertIn("NOT ACCEPTED", out["acceptance"])
        self.assertIn("self-attested", out["notice"])
        self.assertIn("authenticated review", out["notice"])

    def test_cli_without_anchor_cannot_pass(self):
        run = self.run_dir(WIN_AB)
        r = subprocess.run([sys.executable, "-I", str(HERE / "validate-evidence.py"), str(run)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("provenance_unanchored", r.stdout)

    def test_docstring_does_not_claim_independence_proof(self):
        text = (HERE / "validate-evidence.py").read_text()
        self.assertIn("SELF-ATTESTED", text)
        self.assertNotIn("independently verified", text)

    def test_matrix_requires_authenticated_review_beyond_validator(self):
        matrix = json.loads(MATRIX_PATH.read_text())
        self.assertIn("authenticated", json.dumps(matrix["acceptance_requires"]))
        self.assertFalse(matrix["validator_pass_is_acceptance"])


def rebind_paths(run, path):
    """Coordinated attacker: set run.binary.path AND the real manifest's path to `path`, re-hash and re-seal everything."""
    m = json.loads((run / "run.json").read_text())
    m["binary"]["path"] = path
    doc = json.loads((run / "test-manifest.json").read_text())
    doc["path"] = path
    raw = json.dumps(doc)
    write(run / "test-manifest.json", raw)
    m["test_manifest"]["sha256"] = sha(raw.encode())
    write(run / "run.json", json.dumps(m, indent=2))
    seal(run)


class BinaryPathBindingTests(EvidenceTestBase):
    """The run's and the real manifest's executable path must be THE path of the actual --binary, per platform."""

    def test_control_matching_paths_pass_on_both_platforms(self):
        for case in (WIN_AB, MAC_AB):
            run = self.run_dir(case)
            self.assertEqual(self.validate(run).verdict, "PASS", case)

    def test_resealed_coordinated_wrong_directory(self):
        run = self.run_dir(WIN_AB)
        rebind_paths(run, "C:\\evil\\veles-native-host.exe")
        result = self.assertRejected(run, "binary_path_mismatch")
        self.assertEqual(result.verdict, "FAIL")
        run = self.run_dir(MAC_AB)
        rebind_paths(run, "/evil/veles-native-host")
        self.assertRejected(run, "binary_path_mismatch")

    def test_resealed_coordinated_relative_nonexistent_and_odd_paths(self):
        for case, paths in ((WIN_AB, ("veles-native-host.exe", ".\\veles-native-host.exe", "C:\\abs\\..\\abs\\veles-native-host.exe",
                                      "\\\\server\\share\\veles-native-host.exe", "C:\\abs\\veles-native-host.exe\\", "D:\\abs\\veles-native-host.exe")),
                            (MAC_AB, ("veles-native-host", "./veles-native-host", "/abs/../abs/veles-native-host", "/abs//veles-native-host",
                                      "/abs/veles-native-host/", "/ABS/veles-native-host", "/nonexistent/veles-native-host"))):
            for bad in paths:
                with self.subTest(case=case, path=bad):
                    run = self.run_dir(case)
                    rebind_paths(run, bad)
                    result = self.validate(run)
                    self.assertNotEqual(result.verdict, "PASS")
                    self.assertIn("binary_path_mismatch", [d[0] for d in result.defects])

    def test_windows_paths_compare_case_insensitively_with_either_slash(self):
        run = self.run_dir(WIN_AB)
        rebind_paths(run, "c:/ABS/Veles-Native-Host.EXE")
        self.assertEqual(self.validate(run).verdict, "PASS")

    def test_manifest_alone_pointing_elsewhere_while_run_json_is_right(self):
        run = self.run_dir(MAC_AB)
        doc = json.loads((run / "test-manifest.json").read_text())
        doc["path"] = "/evil/veles-native-host"
        rebind_manifest(run, doc)
        self.assertRejected(run, "test_manifest_mismatch")

    def test_run_json_path_wrong_while_manifest_is_right(self):
        run = self.run_dir(MAC_AB)
        self.edit(run, lambda m: m["binary"].__setitem__("path", "/evil/veles-native-host"))
        self.assertRejected(run, "binary_path_mismatch")

    def test_binary_size_must_match_actual_file(self):
        run = self.run_dir(MAC_AB)
        self.edit(run, lambda m: m["binary"].__setitem__("size", 1))
        self.assertRejected(run, "binary_hash_mismatch")

    def test_cross_platform_path_cannot_be_verified_so_blocked(self):
        run = self.run_dir(WIN_AB)  # a Windows case judged against a POSIX-style anchor path
        result = self.v.validate_run(run, cases_path=CASES_PATH, anchor=anchor_for("macos"))
        self.assertEqual(result.verdict, "BLOCKED")
        self.assertIn("binary_path_unverifiable", [d[0] for d in result.defects])
        run = self.run_dir(MAC_AB)
        result = self.v.validate_run(run, cases_path=CASES_PATH, anchor=anchor_for("windows"))
        self.assertEqual(result.verdict, "BLOCKED")

    def test_anchor_without_path_or_with_unusable_path_is_unanchored(self):
        run = self.run_dir(WIN_AB)
        for patch in ({"binary_path": None}, {"binary_path": "relative.exe"}, {"binary_path_platform": "plan9"}, {"binary_size": "1234"},
                      {"binary_path": "/posix/path", "binary_path_platform": "windows"}):
            with self.subTest(patch=patch):
                result = self.v.validate_run(run, cases_path=CASES_PATH, anchor=dict(anchor_for("windows"), **patch))
                self.assertNotEqual(result.verdict, "PASS")
                self.assertIn("provenance_unanchored", [d[0] for d in result.defects])

    def test_norm_path_table(self):
        n = self.v.norm_path
        for good, fam in (("/a/b", "posix"), ("/a/b c/d", "posix"), ("C:\\a\\b", "windows"), ("c:/a/b", "windows")):
            self.assertIsNotNone(n(good, fam), good)
        for bad, fam in (("", "posix"), ("a/b", "posix"), ("/", "posix"), ("/a/./b", "posix"), ("/a/../b", "posix"), ("/a//b", "posix"),
                         ("/a/b/", "posix"), ("/a\\b", "posix"), ("/a\0b", "posix"), (None, "posix"), (5, "posix"), ("/a", "plan9"),
                         ("a\\b", "windows"), ("\\\\h\\s\\f", "windows"), ("\\\\?\\C:\\a", "windows"), ("C:\\a\\..\\b", "windows"),
                         ("C:\\a\\b:stream", "windows"), ("C:\\a\\b.", "windows"), ("C:", "windows"), ("C:\\", "windows"), ("C:a\\b", "windows")):
            self.assertIsNone(n(bad, fam), repr(bad))


class AnchorBinaryTests(unittest.TestCase):
    """compute_anchor binds the actual executable's normalized absolute path, size and bytes, or refuses."""

    @classmethod
    def setUpClass(cls):
        cls.v = load_module("validate-evidence.py", "validate_evidence_bin")

    def make_repo(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        repo = Path(d.name).resolve()
        for rel in self.v.EVALUATOR_FILES:
            write(repo / "native-bridge" / "host" / rel, f"x {rel}\n")
        write(repo / "a.txt", "a\n")
        git(repo, "init", "-q")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "s")
        return repo

    def test_records_path_family_and_size(self):
        repo = self.make_repo()
        exe = repo.parent / (repo.name + "-exe")
        write(exe, b"abc")
        a = self.v.compute_anchor(repo, exe)
        self.assertEqual((a["binary_path"], a["binary_size"], a["binary_sha256"]), (str(exe), 3, sha(b"abc")))
        self.assertEqual(a["binary_path_platform"], "posix")

    def test_refuses_relative_symlinked_missing_and_non_regular_binaries(self):
        repo = self.make_repo()
        exe = repo.parent / (repo.name + "-exe")
        write(exe, b"abc")
        link = repo.parent / (repo.name + "-link")
        link.symlink_to(exe)
        dirlink = repo.parent / (repo.name + "-dirlink")
        dirlink.symlink_to(repo)
        for bad in ("exe-relative", str(link), str(dirlink / "a.txt"), str(repo.parent / "nope"), str(repo), str(exe) + "/../" + exe.name):
            with self.subTest(binary=bad):
                with self.assertRaises((self.v.AnchorError, OSError)):
                    self.v.compute_anchor(repo, bad)


class SourceSymlinkTests(unittest.TestCase):
    """A symlink's referent bytes are not bound by the tree hash, so anchors are refused rather than faked."""

    @classmethod
    def setUpClass(cls):
        cls.v = load_module("validate-evidence.py", "validate_evidence_sym")

    def make_repo(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        repo = Path(d.name).resolve() / "repo"
        repo.mkdir()
        for rel in self.v.EVALUATOR_FILES:
            write(repo / "native-bridge" / "host" / rel, f"x {rel}\n")
        write(repo / "a.txt", "a\n")
        write(repo / ".gitignore", "generated.rs\n")
        git(repo, "init", "-q")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "s")
        return repo

    def test_untracked_symlink_to_external_file_is_refused(self):
        repo = self.make_repo()
        out = repo.parent / "external.rs"
        out.write_text("fn main() {}")
        (repo / "link.rs").symlink_to(out)
        with self.assertRaises(self.v.AnchorError):
            self.v.source_tree_sha256(repo)

    def test_symlink_referent_mutation_cannot_hide_behind_an_anchor(self):
        repo = self.make_repo()
        out = repo.parent / "external.rs"
        out.write_text("version one")
        (repo / "link.rs").symlink_to(out)
        for content in ("version one", "version two"):  # the referent changes; there is never an accepted anchor
            out.write_text(content)
            with self.assertRaises(self.v.AnchorError):
                self.v.compute_anchor(repo, None)

    def test_tracked_symlink_and_ignored_referent_are_refused(self):
        repo = self.make_repo()
        write(repo / "generated.rs", "ignored generated source")  # ignored, so not in the hash
        (repo / "uses_generated.rs").symlink_to("generated.rs")
        git(repo, "add", "uses_generated.rs")
        with self.assertRaises(self.v.AnchorError):
            self.v.source_tree_sha256(repo)

    def test_dangling_and_directory_symlinks_are_refused(self):
        repo = self.make_repo()
        (repo / "dangling").symlink_to("nowhere")
        with self.assertRaises(self.v.AnchorError):
            self.v.source_tree_sha256(repo)
        (repo / "dangling").unlink()
        (repo / "dirlink").symlink_to(repo.parent)
        with self.assertRaises(self.v.AnchorError):
            self.v.source_tree_sha256(repo)

    def test_cli_exits_with_usage_error_for_a_symlinked_tree(self):
        repo = self.make_repo()
        (repo / "link.rs").symlink_to(repo / "a.txt")
        exe = repo.parent / "exe"
        write(exe, b"x")
        run = repo.parent / "run"
        run.mkdir()
        r = subprocess.run([sys.executable, "-I", str(HERE / "validate-evidence.py"), str(run), "--repo", str(repo), "--binary", str(exe)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("symlink", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class HostileAncestorTests(EvidenceTestBase):
    """Directory-fd traversal: an ancestor swapped at ANY moment cannot redirect a read outside the run directory."""

    SECRET = b"OUTSIDE-SECRET-MUST-NEVER-BE-READ"

    def setUp(self):
        self.addCleanup(setattr, self.v, "_RACE_HOOK", None)

    def outside_copy(self, run):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        out = Path(d.name)
        (out / "captures").mkdir()
        (out / "captures" / "host_events.bin").write_bytes(self.SECRET)
        return out

    def test_naive_path_walk_would_leak_in_this_scenario(self):
        """Sanity: the attack is real against a lstat-then-open-by-path reader (so the tests below prove something)."""
        import os
        run = self.run_dir(WIN_AB)
        out = self.outside_copy(run)
        target = run / "captures" / "host_events.bin"
        st = os.lstat(target)  # 'check' passes ...
        (run / "captures").rename(run / "captures.real")
        (run / "captures").symlink_to(out / "captures")  # ... ancestor swapped ...
        with open(target, "rb") as handle:  # ... path-based open follows it
            self.assertEqual(handle.read(), self.SECRET)

    def test_swap_before_the_ancestor_is_opened(self):
        run = self.run_dir(WIN_AB)
        out = self.outside_copy(run)

        def hook(event, detail):
            if event == "before_open" and detail == (0, "captures"):
                (run / "captures").rename(run / "captures.real")
                (run / "captures").symlink_to(out / "captures")

        self.v._RACE_HOOK = hook
        with self.assertRaises(self.v.PathError) as cm:
            self.v.secure_read(run, "captures/host_events.bin")
        self.assertNotIn("SECRET", str(cm.exception))

    def test_swap_after_the_ancestor_fd_is_held(self):
        run = self.run_dir(WIN_AB)
        out = self.outside_copy(run)
        original = (run / "captures" / "host_events.bin").read_bytes()

        def hook(event, detail):
            if event == "before_open" and detail == (1, "host_events.bin"):  # captures/ already opened and verified
                (run / "captures").rename(run / "captures.real")
                (run / "captures").symlink_to(out / "captures")

        self.v._RACE_HOOK = hook
        data = self.v.secure_read(run, "captures/host_events.bin")
        self.assertEqual(data, original)  # served from the verified fd, never the outside secret
        self.assertNotEqual(data, self.SECRET)

    def test_final_component_swapped_for_symlink(self):
        run = self.run_dir(WIN_AB)
        out = self.outside_copy(run)

        def hook(event, detail):
            if event == "before_open" and detail == (1, "host_events.bin"):
                (run / "captures" / "host_events.bin").unlink()
                (run / "captures" / "host_events.bin").symlink_to(out / "captures" / "host_events.bin")

        self.v._RACE_HOOK = hook
        with self.assertRaises(self.v.PathError):
            self.v.secure_read(run, "captures/host_events.bin")

    def test_swap_inside_validation_cannot_pass_or_read_outside(self):
        run = self.run_dir(WIN_AB)
        out = self.outside_copy(run)
        fired = []

        def hook(event, detail):
            if event == "before_open" and detail == (0, "captures") and not fired:
                fired.append(1)
                (run / "captures").rename(run / "captures.real")
                (run / "captures").symlink_to(out / "captures")

        self.v._RACE_HOOK = hook
        result = self.validate(run)
        self.assertTrue(fired)
        self.assertNotEqual(result.verdict, "PASS")
        self.assertTrue(any(c == "unsafe_path" for c, _ in result.defects), result.defects)

    def test_enumeration_substitution_is_reported(self):
        run = self.run_dir(WIN_AB)
        out = self.outside_copy(run)

        def hook(event, detail):
            if event == "enumerate" and detail == "captures":
                (run / "captures").rename(run / "captures.real")
                (run / "captures").symlink_to(out / "captures")

        self.v._RACE_HOOK = hook
        files, problems = self.v.list_regular(run)
        self.assertTrue(problems or "captures/host_events.bin" not in files, (files, problems))
        self.assertFalse(any("SECRET" in p for p in problems))

    def test_threaded_flipping_ancestor_never_yields_outside_bytes(self):
        import os
        import threading
        run = self.run_dir(WIN_AB)
        out = self.outside_copy(run)
        original = (run / "captures" / "host_events.bin").read_bytes()
        real, name, link_target = run / "captures.real", run / "captures", out / "captures"
        name.rename(real)
        stop = threading.Event()

        def flipper():  # `captures` is, in turn: the genuine directory, absent, a symlink to the outside secret, absent
            while not stop.is_set():
                try:
                    os.rename(real, name)
                    os.rename(name, real)
                    os.symlink(link_target, name)
                    os.unlink(name)
                except OSError:
                    pass

        t = threading.Thread(target=flipper)
        t.start()
        ok = err = 0
        try:
            for _ in range(4000):
                try:
                    data = self.v.secure_read(run, "captures/host_events.bin")
                except self.v.PathError:
                    err += 1
                    continue
                self.assertEqual(data, original)  # never the outside secret, never anything else
                ok += 1
        finally:
            stop.set()
            t.join()
        self.assertEqual(ok + err, 4000)

    def test_unsupported_platform_is_blocked_not_downgraded(self):
        run = self.run_dir(WIN_AB)
        self.assertEqual(self.validate(run).verdict, "PASS")
        self.v.SECURE_FS_SUPPORTED, saved = False, self.v.SECURE_FS_SUPPORTED
        self.addCleanup(setattr, self.v, "SECURE_FS_SUPPORTED", saved)
        result = self.validate(run)
        self.assertEqual(result.verdict, "BLOCKED")
        self.assertEqual([c for c, _ in result.defects], ["unsupported_platform"])
        with self.assertRaises(self.v.PathError):
            self.v.secure_read(run, "run.json")
        with self.assertRaises(self.v.PathError):
            self.v.list_regular(run)

    def test_reads_use_no_path_based_open_of_evidence_files(self):
        """Static guard: evidence reads go through secure_read; no Path.read_bytes/open(path) on run-relative names."""
        text = (HERE / "validate-evidence.py").read_text()
        body = text[text.index("class Checker"):text.index("def validate_run")]
        self.assertNotIn(".read_bytes()", body)
        self.assertNotIn(".read_text()", body)
        self.assertNotIn("rglob(", body)
        self.assertNotIn("os.walk(", text)


class PendingHandlingTests(EvidenceTestBase):
    """Option 2: abrupt-EOF cases record the pending request outcome as unknown; shutdown / per-phone failure stay strict."""
    EOF_IDS = [f"{plat}.AB.lifecycle-{x}" for plat in ("windows", "macos") for x in ("port-disconnect", "tab-close", "pipe-closure")]
    SHUTDOWN_IDS = ["windows.AB.lifecycle-explicit-shutdown", "macos.AB.lifecycle-explicit-shutdown"]
    PHONE_IDS = ["windows.AB.per-phone-failure", "macos.AB.per-phone-failure"]

    def test_definitions_distinguish_option2_from_host_cancellation(self):
        for cid in self.EOF_IDS:
            c = self.by_id[cid]
            self.assertEqual(c["pending_request_handling"]["mode"], "eof_unobservable", cid)
            self.assertEqual(c["pending_request_handling"]["per_request_outcome"], "unknown")
            self.assertEqual(c["pending_request_handling"]["host_correlated_cancellation_claim"], "forbidden")
            self.assertIn("record_pending_disposition", c["required_actions"])
            self.assertNotIn("observe_pending_cancellation", c["required_actions"])
            # the two-phone cleanup proof is preserved
            for kept in ("observe_process_exit", "observe_link_release", "prove_bytes_both_links"):
                self.assertIn(kept, c["required_actions"], cid)
            self.assertIn("release_measured_independently_of_process_exit", c["required_controls"])
        for cid in self.SHUTDOWN_IDS + self.PHONE_IDS:
            c = self.by_id[cid]
            self.assertEqual(c["pending_request_handling"]["mode"], "host_correlated_cancellation", cid)
            self.assertIn("observe_pending_cancellation", c["required_actions"])
            self.assertNotIn("record_pending_disposition", c["required_actions"])
        self.assertEqual(self.by_id[self.SHUTDOWN_IDS[0]]["pending_request_handling"]["terminal_error"], [["internal", "shutdown"]])
        self.assertEqual(self.by_id[self.PHONE_IDS[0]]["pending_request_handling"]["terminal_error"], [["send_failed", "link_lost"]])
        for c in self.cases:  # the rule belongs to exactly these ten cases
            self.assertEqual("pending_request_handling" in c, c["id"] in self.EOF_IDS + self.SHUTDOWN_IDS + self.PHONE_IDS, c["id"])

    def test_valid_records_pass_for_all_three_kinds(self):
        for cid in self.EOF_IDS + self.SHUTDOWN_IDS + self.PHONE_IDS:
            result = self.validate(self.run_dir(cid))
            self.assertEqual(result.verdict, "PASS", (cid, result.defects))

    def test_eof_unknown_is_accepted_and_carries_no_per_request_claim(self):
        run = self.run_dir(self.EOF_IDS[0])
        block = json.loads((run / "run.json").read_text())["pending_request_handling"]
        self.assertEqual(block["per_request_outcome"], "unknown")
        self.assertIs(block["host_correlated_cancellation_claimed"], False)
        self.assertNotIn("terminal", block)

    def test_eof_record_missing_the_disposition_is_not_pass(self):
        for cid in self.EOF_IDS[:3]:
            run = self.run_dir(cid)
            self.edit(run, lambda m: m.pop("pending_request_handling"))
            self.assertRejected(run, "pending_cancellation_unproven")

    def test_eof_record_cannot_claim_a_host_cancellation_outcome(self):
        claims = {
            "claimed true": lambda b: b.__setitem__("host_correlated_cancellation_claimed", True),
            "outcome cancelled": lambda b: b.__setitem__("per_request_outcome", "cancelled"),
            "outcome absent": lambda b: b.pop("per_request_outcome"),
            "terminal error attached": lambda b: b.__setitem__("terminal", {"type": "error", "code": "send_failed", "stage": "link_lost", "synthesized": False}),
            "request id attached": lambda b: b.__setitem__("request_id", "r-armed"),
            "armed proof attached": lambda b: b.__setitem__("armed_pending_proven", True),
            "free-form outcome": lambda b: b.__setitem__("outcome", "cancelled_by_host"),
        }
        for cid in self.EOF_IDS:
            for label, mutate in claims.items():
                run = self.run_dir(cid)
                self.edit(run, lambda m, mutate=mutate: mutate(m["pending_request_handling"]))
                self.assertRejected(run, "pending_claim_forbidden")

    def test_eof_record_using_the_strict_mode_or_the_wrong_trigger_is_rejected(self):
        run = self.run_dir(self.EOF_IDS[0])
        self.edit(run, lambda m: m.__setitem__("pending_request_handling", {
            "mode": "host_correlated_cancellation", "trigger": "literal_port_disconnect", "request_id": "r", "armed_pending_proven": True,
            "terminal": {"type": "error", "request_id": "r", "code": "send_failed", "stage": "link_lost", "synthesized": False},
            "capture_ref": "host_events"}))
        self.assertRejected(run, "pending_handling_mode_mismatch")
        run = self.run_dir("windows.AB.lifecycle-tab-close")
        self.edit(run, lambda m: m["pending_request_handling"].__setitem__("trigger", "literal_port_disconnect"))
        self.assertRejected(run, "pending_handling_mode_mismatch")

    def test_eof_unknown_must_be_explained_and_backed_by_a_capture(self):
        run = self.run_dir(self.EOF_IDS[0])
        self.edit(run, lambda m: m["pending_request_handling"].__setitem__("reason", ""))
        self.assertRejected(run, "pending_cancellation_unproven")
        run = self.run_dir(self.EOF_IDS[0])
        self.edit(run, lambda m: m["pending_request_handling"].__setitem__("capture_ref", "nope"))
        self.assertRejected(run, "pending_cancellation_unproven")

    def test_a_synthesized_port_closed_is_not_host_proof_for_any_trigger(self):
        synth = {"type": "error", "request_id": "r-armed", "code": "internal", "stage": "port_closed", "synthesized": True}
        for cid in self.SHUTDOWN_IDS + self.PHONE_IDS:
            run = self.run_dir(cid)
            self.edit(run, lambda m: m["pending_request_handling"].__setitem__("terminal", dict(synth)))
            self.assertRejected(run, "pending_cancellation_unproven")
        # ... nor does flagging an otherwise valid signal as synthesized, or omitting the flag, make it host proof
        for mutate in (lambda t: t.__setitem__("synthesized", True), lambda t: t.pop("synthesized")):
            run = self.run_dir(self.SHUTDOWN_IDS[0])
            self.edit(run, lambda m, mutate=mutate: mutate(m["pending_request_handling"]["terminal"]))
            self.assertRejected(run, "pending_cancellation_unproven")

    def test_explicit_shutdown_stays_strict(self):
        cases = {
            "unarmed": lambda b: b.__setitem__("armed_pending_proven", False),
            "no armed id": lambda b: b.pop("request_id"),
            "other request id": lambda b: b["terminal"].__setitem__("request_id", "r-other"),
            "success instead of error": lambda b: b["terminal"].__setitem__("type", "sent"),
            "requested-disconnect stage": lambda b: b["terminal"].update(code="send_failed", stage="disconnected"),
            "link-loss signal is not the shutdown signal": lambda b: b["terminal"].update(code="send_failed", stage="link_lost"),
            "ordering unproven": lambda b: b.pop("terminal_before_shutdown_complete"),
            "ordering false": lambda b: b.__setitem__("terminal_before_shutdown_complete", False),
            "unknown outcome is not enough": lambda b: b.update(mode="eof_unobservable"),
        }
        for cid in self.SHUTDOWN_IDS:
            for label, mutate in cases.items():
                run = self.run_dir(cid)
                self.edit(run, lambda m, mutate=mutate: mutate(m["pending_request_handling"]))
                result = self.validate(run)
                self.assertNotEqual(result.verdict, "PASS", (cid, label))
                self.assertTrue({d[0] for d in result.defects} & {"pending_cancellation_unproven", "pending_handling_mode_mismatch"}, (cid, label))

    def test_per_phone_failure_stays_strict_and_does_not_accept_the_shutdown_signal(self):
        for cid in self.PHONE_IDS:
            for code, stage in (("internal", "shutdown"), ("send_failed", "disconnected"), ("busy", "queue"), ("timeout", "timeout")):
                run = self.run_dir(cid)
                self.edit(run, lambda m, code=code, stage=stage: m["pending_request_handling"]["terminal"].update(code=code, stage=stage))
                self.assertRejected(run, "pending_cancellation_unproven")
            run = self.run_dir(cid)
            self.edit(run, lambda m: m.pop("pending_request_handling"))
            self.assertRejected(run, "pending_cancellation_unproven")

    def test_no_cross_trigger_weakening(self):
        # the EOF unknown block does not satisfy a strict case, and a strict block does not satisfy an EOF case
        run = self.run_dir(self.SHUTDOWN_IDS[0])
        eof_block = json.loads((self.run_dir(self.EOF_IDS[0]) / "run.json").read_text())["pending_request_handling"]
        self.edit(run, lambda m: m.__setitem__("pending_request_handling", dict(eof_block)))
        self.assertRejected(run, "pending_handling_mode_mismatch")
        # the shutdown requirement must not leak to the per-phone case and vice versa
        run = self.run_dir(self.PHONE_IDS[0])
        shut = json.loads((self.run_dir(self.SHUTDOWN_IDS[0]) / "run.json").read_text())["pending_request_handling"]
        self.edit(run, lambda m: m.__setitem__("pending_request_handling", dict(shut)))
        self.assertRejected(run, "pending_handling_mode_mismatch")

    def test_cases_without_a_rule_may_not_carry_a_pending_claim(self):
        for cid in (WIN_AB, "windows.AB.teardown-hygiene", WIN_DIRECT, MAC_ERR, "windows.none.ready-hygiene"):
            run = self.run_dir(cid)
            self.edit(run, lambda m: m.__setitem__("pending_request_handling", {"mode": "eof_unobservable", "trigger": "x",
                                                                                 "per_request_outcome": "unknown"}))
            self.assertRejected(run, "pending_claim_forbidden")

    def test_a_malformed_block_fails_closed(self):
        for bad in ("unknown", 5, [], None):
            run = self.run_dir(self.EOF_IDS[0])
            self.edit(run, lambda m, bad=bad: m.__setitem__("pending_request_handling", bad))
            self.assertNotEqual(self.validate(run).verdict, "PASS")

    def test_pending_rule_is_evaluator_input(self):
        # cases.json is an evaluator file: weakening it changes the evaluator anchor, so a run built against the old rule cannot pass
        self.assertIn("acceptance/cases.json", self.v.EVALUATOR_FILES)


if __name__ == "__main__":
    unittest.main()
