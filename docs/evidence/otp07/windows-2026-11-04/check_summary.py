#!/usr/bin/env python3
"""Validate the published OTP-07 Windows whitelist summary (stdlib only)."""
import hashlib, json, re
from pathlib import Path
HERE = Path(__file__).resolve().parent
SUMMARY = json.loads((HERE / "summary.json").read_text(encoding="utf-8"))
VEL1 = bytes([0x56, 0x45, 0x4C, 0x01])

def check(condition, message):
    assert condition, message

def record(length, seed):
    data = bytearray(length)
    data[:4] = VEL1
    for index in range(4, length):
        data[index] = (index + seed) & 0xFF
    return bytes(data)
def reply(length, seed):
    data = bytearray(record(length, seed))
    data[3] = 2
    return bytes(data)
def digest(data):
    return hashlib.sha256(data).hexdigest()

check(SUMMARY["schema"] == "veles.otp07.windows-public-evidence.v1", "schema")
source = SUMMARY["source"]
check(source["commit"] == "58b243d4a55ed18d6b3e3bd0dee05b2f505d7c24" and source["tree"] ==
      "aa5331e1b4f8a0874b5b877ca0838b1ac7d96c90" and source["original_commit_immutable"] is True, "source")
status = SUMMARY["status"]
check(status["overall"] == "APPROVED_SCOPED_NARROW_FOREGROUND_SMOKE_WITH_LIMITS", "overall status")
check(status["physical_evidence_acceptance"] == "REVIEWED_SCOPED_PASS_WITH_LIMITS", "reviewed scope")
check(status["external_android_signer_chain"] == "RESOLVED_PASS", "signer chain status")
check(status["environment_metadata"] == "POST_RUN_CORROBORATED_NOT_CONTEMPORANEOUS" and
      status["contemporaneous_environment_capture"] is False, "environment status")
check(status["macos"] == "NOT_RUN", "macOS status")
for flag in ("full_otp07_closed", "installer_acceptance", "strict_lifecycle_acceptance",
             "authenticated_or_background_transport", "literal_toolbar_invocation", "no_gatt_writes_proven"):
    check(status[flag] is False, f"{flag} must be false")
extension = SUMMARY["extension"]
check(extension["zip_sha256"] == "4b317ec8a5aa3c54035f35bfc2cc8215f02e141102790ad9527a86d0d42f029f", "zip")
check(extension["catalog_sha256"] ==
      "440b1c6dff6156f06c7993658726d440c093bba36b97acb9ad49c73fe084ec21", "catalog hash")
catalog = sorted(extension["catalog"], key=lambda entry: entry["path"])
canonical = json.dumps([{"path": entry["path"], "sha256": entry["sha256"]} for entry in catalog],
                       separators=(",", ":")).encode("utf-8")
check(len(catalog) == 13 and extension["catalog_entry_count"] == 13, "catalog size")
check(digest(canonical) == extension["catalog_sha256"], "catalog recompute")
for name, expected in {
    "connectorScript": "5596b03cf4c67868ec1e610664b566a967a82e510032b38a593b9fd32b784661",
    "connectorHtml": "ec4478422db840af4038d73e8c217f88a86d553c660da30c7725864dd73df88b",
    "backgroundScript": "8e3258a970d5726470625bd9bf8fa11f9d5139df92e0ca709cbcf1756a7b5b1c",
}.items():
    check(extension["served"][name]["sha256"] == expected, f"served {name}")
    check(extension["served"][name]["matches_staged_catalog"] is True, f"served match {name}")
host = SUMMARY["host"]
check(host["sha256"] == "36a9fcda38d63292b720b7f3c84fa29e5dc6bcc0ef45b1e0cc13a5a55a5d91af", "host hash")
check(host["unsigned_development_binary"] is True, "host unsigned flag")
check(host["authenticode_status"] == "NotSigned" and
      host["documented_build_command_without_test_driver_features"] is True, "host signing/build metadata")
check("production_binary_without_test_driver_feature" not in host, "no unqualified feature claim")
check(SUMMARY["evaluator"]["windows_smoke_sha256"] ==
      "7e03aa8867b051be0ffe01416b155ad03c71deeceae2d90dbd001ddc2fe9676d", "evaluator hash")
signer = SUMMARY["android_fixture"]["signer_evidence"]
check(signer["sha256"] == "c06806c8d32cdd4f617614a94e36079d5618672349c97d14ccb815860ceb2ef8", "signer hash")
check(signer["available"] is True and signer["windows_executable_signing"] is False, "signer scope")
check(signer["semantic_external_signer_acceptance"] == "PASS" and signer["chain_resolved"] is True, "signer chain")
check(signer["pre_install_copies_sha256"] == "5013c6b04f6d0ad041cb0b1d2e645c4ac81e9fc9ae08c1d6c1360f96835a6313" and
      signer["adb_install_results_sha256"] == "c8f87f03473de0c40321fa219386a51f737642ccc6a56c76c09775d0315a239e", "signer anchors")
check(SUMMARY["android_fixture"]["app_apk_sha256"] ==
      "3e2a6c41bb0eb6d3ef9b07f478d29b0fe417dcf7df1915c0b6482a754c059f08", "app apk")
check(SUMMARY["android_fixture"]["test_apk_sha256"] ==
      "afadd554b475013f62102c72b62c0badc46706d6f9f3e26dfe52ede4687fac84", "test apk")
check(SUMMARY["android_fixture"]["signing_certificate_sha256"] ==
      "9ef3104b934a37b111726ff91c7cb5ec5affc0e8b6a90742ed0bc55501969df9", "cert")
env = SUMMARY["environment_postrun"]
check(env["artifact_sha256"] == "d318faeeca58725cc8293c8c429f4bc976cd4177d8213e247fb459b9b0959582" and
      env["observed_at"] == "2026-11-04T13:50:30.2396875+07:00" and env["supplemental"] is True and
      env["contemporaneous_with_run"] is False, "environment provenance")
check(env["os"]["caption"] == "Microsoft Windows 11 Pro" and env["os"]["version"] == "10.0.26300" and
      env["os"]["architecture"] == "64-bit", "environment OS")
check(env["chrome_current"]["product_version"] == "156.0.8078.80" and
      env["chrome_current"]["matches_original_cdp_browser_version"] is True and
      env["chrome_current"]["live_process_capture_available"] is False, "Chrome linkage")
check(env["host_authenticode_status"] == "NotSigned", "host Authenticode")
check(env["original_capture_available"]["os_version"] is False and
      env["original_capture_available"]["chrome_process_and_flags"] is False, "no contemporaneous capture")
exchanges = SUMMARY["transfers"]["exchanges"]
check(len(exchanges) == 13, "exchange count")
check(sum(1 for item in exchanges if item["phase"] == "main") == 9, "main exchanges")
check(sum(1 for item in exchanges if item["phase"] == "reopen") == 4, "reopen exchanges")
long_hashes = set()
for item in exchanges:
    check(item["request_sha256_reconstructed"] == digest(record(item["length"], item["seed"])), f"request {item['ref']}")
    check(item["reply_sha256"] == digest(reply(item["length"], item["seed"])), f"reply {item['ref']}")
    check(item["reply_matched"] is True, f"matched {item['ref']}")
    check(item["send_terminal"]["source"] == "wire" and item["send_terminal"]["settled"] is True, f"send {item['ref']}")
    if item["length"] == 4:
        check(item["four_byte_boundary_only"] is True, f"boundary {item['ref']}")
        check(item["request_sha256_reconstructed"] ==
              "70848327e00df6dd9b45a88b6219523cd35c7379070d89bfa978407fa07229bd", "4-byte request")
    else:
        long_hashes.add(item["request_sha256_reconstructed"])
check(len(long_hashes) == 9, "long content distinct")
receipts = SUMMARY["transfers"]["receipts"]
check(len(receipts) == 13, "receipt count")
check(sorted(item["seq"] for item in receipts if item["phase"] == "main") == list(range(1, 10)), "main receipts")
check(sorted(item["seq"] for item in receipts if item["phase"] == "reopen") == list(range(1, 5)), "reopen receipts")
receipt_by_key = {(item["phase"], item["seq"]): item for item in receipts}
for item in exchanges:
    receipt = receipt_by_key[(item["phase"], item["receipt"]["seq"])]
    check(receipt["exchange_ref"] == item["ref"] and receipt["sha256"] == item["reply_sha256"] and
          receipt["at"] == item["receipt"]["at"], f"receipt {item['ref']}")
telemetry = SUMMARY["phone_telemetry"]
check({ref: (telemetry[ref]["api_success"], telemetry[ref]["notify_callback_seen"],
             telemetry[ref]["notify_api_nonzero"], telemetry[ref]["notify_callback_nonzero"],
             telemetry[ref]["record_complete"]) for ref in ("A", "B")} == {
    "A": (15, 15, 0, 0, 6), "B": (24, 24, 0, 0, 7)}, "telemetry counters")
for ref in ("A", "B"):
    check(telemetry[ref]["clean"] is True and telemetry[ref]["final"] is True, f"clean {ref}")
    check(telemetry[ref]["final_after_service_stopped"] is True, f"stopped {ref}")
    check(telemetry[ref]["content_digests_present"] is False, f"no content digests {ref}")
    check(telemetry[ref]["wall_clock_calibrated_with_windows"] is False, f"no clock calibration {ref}")
lifecycle = SUMMARY["lifecycle_observations"]
check(lifecycle["reopen_process_poll"]["creation_minus_seen_ms"] == 29.7376, "chronology")
check(lifecycle["main_process_id_recorded"] is False and lifecycle["strict_exit_timing"] is False, "lifecycle limits")
check(lifecycle["reopen_process_poll"]["original_source_retained"] is False and
      lifecycle["reopen_process_poll"]["transcribed_source_sha256"] == "434f38bb9841026e2e7c79108afafc93964bade244d6cd1971b6c1b1ae3ffeab", "poller transcription")
check(SUMMARY["launcher_observation"]["literal_toolbar_click"] is False, "launcher scope")
check(SUMMARY["transfers"]["protocol_rejection_4097"]["gatt_write_absence_proven"] is False and
      SUMMARY["bounded_observations"]["explicit_disconnect"]["reconnect_observed"] is False, "bounded scope")
check(SUMMARY["attempt_a"]["promoted_to_success"] is False, "attempt-a promotion")
review = SUMMARY["review"]
check(review["final_disposition"] == "APPROVED_SCOPED_NARROW_FOREGROUND_SMOKE_WITH_LIMITS" and
      review["external_signer_acceptance"] == "RESOLVED_PASS", "review disposition")
check(review["environment_metadata"] == "POST_RUN_CORROBORATED_NOT_CONTEMPORANEOUS" and
      review["contemporaneous_environment_capture"] is False, "review environment")
text = (HERE / "summary.json").read_text(encoding="utf-8")
for forbidden in ("AppData", "chrome-extension://", "Program Files"):
    check(forbidden not in text, f"private-path/identifier leak: {forbidden}")
check(re.search(r"[A-Za-z]:[\\/]{1,2}", text) is None, "drive-path-shaped value")
check(re.search(r"\b[a-p]{32}\b", text) is None, "extension-id-shaped value")
check(re.search(r"\bpid[=: ]+\d+", text, re.I) is None, "PID-shaped value")
for name, value in SUMMARY["original_file_hashes"].items():
    check(re.fullmatch(r"[0-9a-f]{64}", value) is not None, f"hash shape {name}")
check(SUMMARY["original_file_hashes"]["attempt_b/main.json"] ==
      "9a2c9b39be1ad6e0f81c5dac8194abac40ffcd1f7711d2b15e69f60e438712da", "main hash")
check(SUMMARY["original_file_hashes"]["attempt_b/reopen.json"] ==
      "45c4d2c219ae965182c52fd8d5acc8cada12cde0f017e20d9eb1e20a4a96dd4a", "reopen hash")
print(f"PASS: 13 exchanges/13 receipts, catalog {len(catalog)}, telemetry A=15 B=24, no leaks")
