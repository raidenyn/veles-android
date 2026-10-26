"""Pure tests for the offline strict-background finalizer: phone-file validation and non-overwrite guarantees."""

import copy
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strict_background_core as core  # noqa: E402
import strict_background_finalize as fin  # noqa: E402
import strict_phone_validation as spv  # noqa: E402
from test_strict_background import good_evidence  # noqa: E402

WANT = core.expected_sha256()
STARTED = 1_000_000.0 - 400.0
CLOSED = 1_000_000.0


def raw_result():
    """A raw driver result as a Mac run leaves it: everything but the phone evidence is present."""
    evidence = good_evidence("cold")
    evidence["phone_evidence"] = None
    evidence.pop("phone_validation")
    evidence["timeline"]["background_started_wall"] = CLOSED - 1
    verdict = core.evaluate(evidence)
    started = fin.datetime.fromtimestamp(STARTED, fin.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"platform": "macos", "scenario": "cold", "strict_result": verdict["strict_result"],
            "meta": {"started_utc": started, "phone_delay_s": 690, "min_background_s": 600},
            "checks": verdict["checks"], "evidence": evidence}


def phone_json(peer, sub_s=CLOSED - 60, **over):
    push = {"peer_sha256_prefix": peer, "subscribed_at_epoch_ms": int(sub_s * 1000),
            "fired_at_epoch_ms": int((sub_s + 690.2) * 1000), "configured_delay_ms": 690_000,
            "queued": True, "bytes": 4096, "sha256": WANT}
    push.update(over)
    return {"kind": fin.KIND, "pushes": [push]}


CLOCK = {"offset_ms": 0, "uncertainty_ms": 500, "source": "unit-test midpoint"}


def record(data, name, mtime=CLOSED + 700, path=None, source="auto"):
    raw_bytes = json.dumps(data, sort_keys=True).encode()
    if source == "auto":
        source = spv.source_hash("SERIAL-" + name)
    return {"path": name, "resolved": path or "/" + name, "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "size": len(raw_bytes), "mtime_epoch": mtime, "json": data, "error": None, "source_hash": source}


def two_good():
    return [record(phone_json("aaaa"), "a.json"), record(phone_json("bbbb", CLOSED - 59), "b.json")]


class Validate(unittest.TestCase):
    def status(self, records, raw=None, clock=CLOCK):
        return fin.validate_phone_files(raw or raw_result(), records, clock)

    def test_two_distinct_current_files_pass(self):
        v = self.status(two_good())
        self.assertEqual(v["status"], "PASS", v["problems"])

    def test_missing_files_are_blocked_not_pass(self):
        self.assertEqual(self.status([])["status"], "BLOCKED")
        self.assertEqual(self.status(two_good()[:1])["status"], "BLOCKED")

    def test_duplicate_file_fails_by_path_and_by_content(self):
        a = two_good()[0]
        self.assertEqual(self.status([a, copy.deepcopy(a)])["status"], "FAIL")
        b = record(phone_json("aaaa"), "copy.json", path="/copy.json")  # same content, another path
        self.assertEqual(self.status([a, b])["status"], "FAIL")

    def test_same_acquisition_source_twice_fails_even_with_different_bytes(self):
        src = spv.source_hash("ONE")
        a = record(phone_json("aaaa"), "a.json", source=src)
        b = record(phone_json("bbbb", CLOSED - 59), "b.json", source=src)
        self.assertEqual(self.status([a, b])["status"], "FAIL")

    def test_bare_paths_without_source_provenance_cannot_pass(self):
        good = [record(phone_json("aaaa"), "a.json", source=None), record(phone_json("bbbb", CLOSED - 59), "b.json", source=None)]
        self.assertEqual(self.status(good)["status"], "BLOCKED")

    def test_missing_clock_evidence_is_blocked_not_assumed_perfect(self):
        self.assertEqual(self.status(two_good(), clock=None)["status"], "BLOCKED")
        self.assertEqual(self.status(two_good(), clock={"offset_ms": 0, "uncertainty_ms": 1})["status"], "BLOCKED")

    def test_each_phone_must_match_its_own_distinct_receipt(self):
        raw = raw_result()
        raw["evidence"]["events"][1]["wall"] = raw["evidence"]["events"][0]["wall"] + 3_600_000.0  # far away
        raw["evidence"]["timeline"]["cdp_reopened_wall"] += 4000
        self.assertEqual(self.status(two_good(), raw=raw)["status"], "FAIL")
        raw = raw_result()
        e0 = raw["evidence"]["events"][0]["wall"]
        raw["evidence"]["events"][1]["wall"] = e0 + 100  # both phones fired ~25 s before ONE shared receipt cluster
        self.assertEqual(self.status(two_good(), raw=raw)["status"], "PASS")
        good = two_good()
        good[1] = record(phone_json("bbbb", CLOSED - 59 - 200), "b.json")  # fired 200 s before any receipt
        self.assertEqual(self.status(good)["status"], "FAIL")

    def test_clock_offset_is_applied_to_phone_times(self):
        shifted = {"offset_ms": 3_600_000, "uncertainty_ms": 500, "source": "x"}
        self.assertEqual(self.status(two_good(), clock=shifted)["status"], "FAIL")
        good = [record(phone_json("a", CLOSED - 60 - 3600), "a.json"), record(phone_json("b", CLOSED - 59 - 3600), "b.json")]
        self.assertEqual(self.status(good, clock=shifted)["status"], "PASS")

    def test_same_central_peer_hash_is_not_a_false_fail(self):
        """False FAIL before the fix: peer_sha256_prefix hashes the CENTRAL, so two real phones share it."""
        a = record(phone_json("same-central"), "a.json")
        b = record(phone_json("same-central", CLOSED - 59), "b.json")
        v = self.status([a, b])
        self.assertEqual(v["status"], "PASS", v["problems"])
        self.assertEqual(v["central_peer_prefixes_informational"], ["same-central", "same-central"])

    def test_missing_peer_value_is_not_a_failure_either(self):
        good = two_good()
        good[0] = record(phone_json(None), "a.json")
        self.assertEqual(self.status(good)["status"], "PASS")

    def test_bare_paths_without_source_provenance_cannot_pass(self):
        good = [record(phone_json("aaaa"), "a.json", source=None),
                record(phone_json("bbbb", CLOSED - 59), "b.json", source=None)]
        self.assertEqual(self.status(good)["status"], "BLOCKED")

    def test_missing_clock_evidence_is_blocked_not_assumed_perfect(self):
        self.assertEqual(self.status(two_good(), clock=None)["status"], "BLOCKED")
        self.assertEqual(self.status(two_good(), clock={"offset_ms": 0, "uncertainty_ms": 1})["status"], "BLOCKED")

    def test_each_phone_must_match_its_own_distinct_receipt(self):
        good = two_good()
        good[1] = record(phone_json("bbbb", CLOSED - 59 - 200), "b.json")  # fired 200 s before any receipt
        self.assertEqual(self.status(good)["status"], "FAIL")
        raw = raw_result()
        raw["evidence"]["events"] = raw["evidence"]["events"][:1]  # one receipt cannot serve two phones
        self.assertEqual(self.status(two_good(), raw=raw)["status"], "FAIL")

    def test_clock_offset_is_applied_to_phone_times(self):
        shifted = {"offset_ms": 3_600_000, "uncertainty_ms": 500, "source": "x"}
        self.assertEqual(self.status(two_good(), clock=shifted)["status"], "FAIL")
        good = [record(phone_json("a", CLOSED - 60 - 3600), "a.json"),
                record(phone_json("b", CLOSED - 59 - 3600), "b.json")]
        self.assertEqual(self.status(good, clock=shifted)["status"], "PASS")

    def test_wrong_hash_failed_queue_wrong_delay_wrong_kind_fail(self):
        for over in ({"sha256": "00" * 32}, {"queued": False}, {"configured_delay_ms": 60_000}, {"bytes": 10}):
            good = two_good()
            good[1] = record(phone_json("bbbb", CLOSED - 59, **over), "b.json")
            self.assertEqual(self.status(good)["status"], "FAIL", over)
        good = two_good()
        data = phone_json("bbbb", CLOSED - 59)
        data["kind"] = "something-else"
        good[1] = record(data, "b.json")
        self.assertEqual(self.status(good)["status"], "FAIL")

    def test_stale_pushes_from_another_run_fail(self):
        good = two_good()
        good[1] = record(phone_json("bbbb", CLOSED - 100_000), "b.json")
        self.assertEqual(self.status(good)["status"], "FAIL")
        good = two_good()
        data = phone_json("bbbb", CLOSED - 59)
        data["pushes"].append(copy.deepcopy(data["pushes"][0]))
        good[1] = record(data, "b.json")
        self.assertEqual(self.status(good)["status"], "FAIL", "an extra (stale) push in the file")

    def test_push_after_reattach_or_before_idle_could_complete_fails(self):
        good = two_good()
        good[1] = record(phone_json("bbbb", CLOSED + 5000), "b.json")
        self.assertEqual(self.status(good)["status"], "FAIL")
        good = two_good()
        data = phone_json("bbbb", CLOSED - 59, fired_at_epoch_ms=int((CLOSED - 58) * 1000))
        good[1] = record(data, "b.json")
        self.assertEqual(self.status(good)["status"], "FAIL")

    def test_file_collected_before_idle_ended_fails(self):
        good = two_good()
        good[0] = record(phone_json("aaaa"), "a.json", mtime=CLOSED + 5)
        self.assertEqual(self.status(good)["status"], "FAIL")

    def test_unreadable_file_fails_without_crashing(self):
        bad = {"path": "x", "resolved": "/x", "sha256": None, "size": None, "mtime_epoch": None, "json": None,
               "error": "JSONDecodeError", "source_hash": spv.source_hash("Z")}
        self.assertEqual(self.status([two_good()[0], bad])["status"], "FAIL")


class Adjudicate(unittest.TestCase):
    def test_good_inputs_pass_and_keep_formal_acceptance_false(self):
        doc = fin.adjudicate(raw_result(), {"path": "raw.json", "sha256": "ab", "size": 1}, two_good(), clock=CLOCK)
        self.assertEqual(doc["strict_result"], "PASS", doc["checks"])
        self.assertIs(doc["formal_otp23_acceptance"], False)
        self.assertEqual(doc["inputs"]["raw"]["sha256"], "ab")
        self.assertEqual(len(doc["inputs"]["phone_files"]), 2)

    def test_adjudication_never_mutates_the_raw_object(self):
        raw = raw_result()
        before = json.dumps(raw, sort_keys=True)
        fin.adjudicate(raw, {"path": "raw.json", "sha256": "ab", "size": 1}, two_good(), clock=CLOCK)
        self.assertEqual(json.dumps(raw, sort_keys=True), before)
        self.assertIsNone(raw["evidence"]["phone_evidence"])

    def test_bad_phone_input_blocks_or_fails_the_result(self):
        doc = fin.adjudicate(raw_result(), {"path": "r", "sha256": "ab", "size": 1}, two_good()[:1])
        self.assertEqual(doc["checks"]["ST-08"]["status"], "BLOCKED")
        self.assertEqual(doc["strict_result"], "BLOCKED")
        doc = fin.adjudicate(raw_result(), {"path": "r", "sha256": "ab", "size": 1},
                             [two_good()[0], copy.deepcopy(two_good()[0])], clock=CLOCK)
        self.assertEqual(doc["strict_result"], "FAIL")

    def test_other_failing_checks_survive_good_phone_files(self):
        raw = raw_result()
        raw["evidence"]["chrome"]["args"].append("--disable-renderer-backgrounding")
        raw["checks"] = core.evaluate(raw["evidence"])["checks"]
        doc = fin.adjudicate(raw, {"path": "r", "sha256": "ab", "size": 1}, two_good(), clock=CLOCK)
        self.assertEqual(doc["strict_result"], "FAIL")

    def test_tampered_raw_checks_are_detected(self):
        raw = raw_result()
        raw["checks"]["ST-02"]["status"] = "FAIL"
        doc = fin.adjudicate(raw, {"path": "r", "sha256": "ab", "size": 1}, two_good(), clock=CLOCK)
        self.assertIn("ST-02", doc["raw_consistency"]["checks_in_raw_that_disagree_with_recomputation"])
        self.assertEqual(doc["strict_result"], "FAIL")


class Cli(unittest.TestCase):
    def write(self, directory, name, data):
        path = pathlib.Path(directory) / name
        path.write_text(json.dumps(data))
        return str(path)

    def test_never_overwrites_raw_or_existing_output_and_records_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = self.write(tmp, "raw.json", raw_result())
            a = self.write(tmp, "a.json", phone_json("aaaa"))
            b = self.write(tmp, "b.json", phone_json("bbbb", CLOSED - 59))
            for path in (a, b):  # make mtimes look like a post-idle collection
                os.utime(path, (CLOSED + 700, CLOSED + 700))
            raw_bytes = pathlib.Path(raw_path).read_bytes()
            out = str(pathlib.Path(tmp) / "adjudicated.json")
            clock = ["--phone-clock-offset-ms", "0", "--phone-clock-uncertainty-ms", "500", "--phone-clock-source", "t"]
            a, b = "SER_A=" + a, "SER_B=" + b
            code = fin.run(["--raw", raw_path, "--phone-evidence", a, "--phone-evidence", b, "--out", out] + clock)
            self.assertEqual(code, 0)
            doc = json.loads(pathlib.Path(out).read_text())
            self.assertEqual(doc["inputs"]["raw"]["sha256"], hashlib.sha256(raw_bytes).hexdigest())
            self.assertEqual(doc["inputs"]["phone_files"][0]["source_hash"], spv.source_hash("SER_A"))
            self.assertNotIn("SER_A", json.dumps(doc), "serials are published only as hashes")
            self.assertEqual(fin.run(["--raw", raw_path, "--phone-evidence", a, "--phone-evidence", b, "--out", out]), 2)
            self.assertEqual(fin.run(["--raw", raw_path, "--phone-evidence", a, "--phone-evidence", b,
                                      "--out", raw_path]), 2)
            self.assertEqual(pathlib.Path(raw_path).read_bytes(), raw_bytes)

    def test_non_strict_raw_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = self.write(tmp, "raw.json", {"hello": 1})
            a = self.write(tmp, "a.json", {})
            self.assertEqual(fin.run(["--raw", raw_path, "--phone-evidence", a, "--out", str(pathlib.Path(tmp) / "o.json")]), 2)


if __name__ == "__main__":
    unittest.main()
