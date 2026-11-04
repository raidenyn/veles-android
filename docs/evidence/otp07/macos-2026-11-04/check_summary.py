#!/usr/bin/env python3
"""Validate the published OTP-07 macOS whitelist summary (stdlib only)."""
import hashlib
import json
import re
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


check(SUMMARY["schema"] == "veles.otp07.macos-public-evidence.v1", "schema")
status = SUMMARY["status"]
check(status["overall"] == "APPROVED_SCOPED_NARROW_FOREGROUND_CDP_SMOKE_WITH_LIMITS", "overall")
check(status["physical_evidence_acceptance"] == "REVIEWED_SCOPED_PASS_WITH_LIMITS", "review scope")
check(status["macos_bounded_records"] == "RECONCILED", "records")
check(status["review_findings"] == {"critical": 0, "minor": 1,
                                    "minor_ids": ["catalog_self_entry_stale"]}, "findings")
check(status["mac_test_vehicle_signing"] == "PASS_AT_RETAINED_READBACK_BOUNDARY", "signing scope")
check(status["environment_metadata"] == "CONTEMPORANEOUS_BEFORE_AND_PHASE_OBSERVATIONS" and
      status["contemporaneous_environment_capture"] is True, "environment status")
check(status["external_android_signer_chain"] == "REFERENCED_PASS_NOT_REEXECUTED_ON_MAC", "android signer")
check(status["bt_permission_denial_recovery"] == "NOT_RUN", "denial recovery")
for flag in ("full_otp07_closed", "installer_acceptance", "notarization",
             "production_identity_migration", "strict_lifecycle_acceptance",
             "authenticated_or_background_transport", "literal_toolbar_invocation",
             "no_gatt_writes_proven"):
    check(status[flag] is False, f"{flag} must be false")
check(status["windows_prior_result_unchanged"] is True, "windows prior result")

source = SUMMARY["source"]
check(source == {"commit": "58b243d4a55ed18d6b3e3bd0dee05b2f505d7c24",
                 "tree": "aa5331e1b4f8a0874b5b877ca0838b1ac7d96c90",
                 "original_commit_immutable": True,
                 "followup_changes": "evidence/docs publication only; no source recomposition"}, "source")

extension = SUMMARY["extension"]
catalog = sorted(extension["catalog"], key=lambda entry: entry["path"])
canonical = json.dumps([{"path": entry["path"], "sha256": entry["sha256"]} for entry in catalog],
                       separators=(",", ":")).encode("utf-8")
check(len(catalog) == 13 and extension["catalog_entry_count"] == 13, "catalog size")
check(extension["zip_sha256"] ==
      "4b317ec8a5aa3c54035f35bfc2cc8215f02e141102790ad9527a86d0d42f029f", "zip")
check(digest(canonical) == extension["catalog_sha256"] ==
      "440b1c6dff6156f06c7993658726d440c093bba36b97acb9ad49c73fe084ec21", "catalog hash")
check(extension["catalog_identical_to_windows"] is True, "catalog windows flag")
windows_summary = HERE.parent / "windows-2026-11-04" / "summary.json"
if windows_summary.exists():
    windows = json.loads(windows_summary.read_text(encoding="utf-8"))
    check(windows["extension"]["catalog_sha256"] == extension["catalog_sha256"], "windows catalog hash")
    check(sorted(({"path": item["path"], "sha256": item["sha256"]} for item in windows["extension"]["catalog"]),
                 key=lambda item: item["path"]) ==
          [{"path": item["path"], "sha256": item["sha256"]} for item in catalog], "windows catalog entries")
for name, expected in {
    "connectorScript": "5596b03cf4c67868ec1e610664b566a967a82e510032b38a593b9fd32b784661",
    "connectorHtml": "ec4478422db840af4038d73e8c217f88a86d553c660da30c7725864dd73df88b",
    "backgroundScript": "8e3258a970d5726470625bd9bf8fa11f9d5139df92e0ca709cbcf1756a7b5b1c",
}.items():
    check(extension["served"][name]["sha256"] == expected, f"served {name}")
    check(extension["served"][name]["matches_staged_catalog"] is True, f"served match {name}")

host = SUMMARY["host"]
check(host["signed_executable_sha256"] ==
      "05655ed8d4e962df808191c1e7bfb9906c5bdb6bb4b4355a146a4915baeca3e3", "signed exe")
check(host["unsigned_source_executable_sha256"] ==
      "25f6ab2ef872bff8512a7c1bce496f5151a7ae0fb0f6d13d070c2223bdc82fa9", "unsigned exe")
check(host["signed_executable_bytes"] == 1881264 and host["test_vehicle"] is True, "test vehicle")
check(host["bundle_identifier"] == "me.nagaev.veles.otp23.poc" and
      host["team_identifier"] == "7T39M26Z99", "identity")
check(host["signature_kind"] == "Apple Development" and host["anchor"] == "apple generic" and
      host["valid_on_disk"] is True and host["satisfies_designated_requirement"] is True, "codesign")
check(host["ad_hoc"] is False and host["production_distribution"] is False and
      host["notarized"] is False and host["installer"] is False, "distribution scope")
check(host["designated_requirement_old_and_new_identical"] is True, "designated requirement")

environment = SUMMARY["environment"]
check(environment["contemporaneous_with_run"] is True and
      environment["status"] == "CONTEMPORANEOUS_BEFORE_AND_PHASE_OBSERVATIONS", "contemporaneous")
check(environment["os"] == {"product_name": "macOS", "product_version": "26.5.2",
                            "build_version": "25F84", "architecture": "arm64",
                            "gui_session": "Aqua"}, "macOS environment")
check(environment["chrome"]["product_version"] == "156.0.8078.81" and
      environment["chrome"]["matches_pre_main"] is True, "Chrome linkage")
check(all(environment["original_capture_available"].values()), "original capture available")

evaluator = SUMMARY["evaluator"]
check(evaluator["macos_smoke_sha256"] ==
      "2afe92dc9ac8cad774f565e419c40557b5f72b55c544f224aec490d90936845f", "evaluator")
check(evaluator["windows_baseline_sha256"] ==
      "7e03aa8867b051be0ffe01416b155ad03c71deeceae2d90dbd001ddc2fe9676d", "windows evaluator")
check(evaluator["diff_added_lines"] == 17 and evaluator["diff_removed_lines"] == 15, "driver diff")
check(evaluator["retained_private_not_public_reproducer"] is True, "evaluator privacy")

fixture = SUMMARY["android_fixture"]
check(fixture["app_apk_sha256"] ==
      "3e2a6c41bb0eb6d3ef9b07f478d29b0fe417dcf7df1915c0b6482a754c059f08", "app apk")
check(fixture["test_apk_sha256"] ==
      "afadd554b475013f62102c72b62c0badc46706d6f9f3e26dfe52ede4687fac84", "test apk")
check(fixture["signing_certificate_sha256"] ==
      "9ef3104b934a37b111726ff91c7cb5ec5affc0e8b6a90742ed0bc55501969df9", "cert")
signer = fixture["signer_evidence"]
check(signer["sha256"] ==
      "c06806c8d32cdd4f617614a94e36079d5618672349c97d14ccb815860ceb2ef8" and
      signer["bytes"] == 1977, "signer evidence")
check(signer["reexecuted_on_mac"] is False and signer["install_or_clear_on_mac"] is False, "mac signer scope")

run = SUMMARY["run"]
check(run["run_id"] == "otp07-mac-20261104-a", "run id")
for phase, total, sends in (("main", 9, 10), ("reopen", 4, 6)):
    entry = run[phase]
    check(entry["status"] == "PASS" and entry["complete"] is True and entry["final"] is True, f"{phase} state")
    check(entry["failures"] == 0 and entry["timeouts"] == 0 and entry["unmatched_records"] == 0, f"{phase} clean")
    check(entry["record_audit"] == {"total": total, "exchange_matched": total, "exchange_failed": 0,
                                    "unknown": 0, "digest_issues": 0, "unexpected": 0,
                                    "send_count": sends, "unsettled_sends": 0}, f"{phase} audit")

registration = SUMMARY["registration"]
check(registration["per_profile_only"] is True and registration["global_registration"] is False, "per-profile")
check(registration["manifest_sha256"] ==
      "1eed352fe25edad7c44b9775fa847ba1d065cc26d1b39efc4e351c5ea2c7abb3" and
      registration["manifest_bytes"] == 344, "manifest")
check(registration["cleanup_recorded"] is True and registration["profile_manifest_removed"] is True and
      registration["copy_byte_identical"] is True and
      registration["global_nmh_before_after_identical"] is True, "cleanup")
check(registration["owned_chrome_closed_via_browser_close"] is True and
      registration["owned_host_processes_after_close"] == 0 and
      registration["remaining_hosts_in_test_profile"] == 0, "no remaining hosts")

exchanges = SUMMARY["transfers"]["exchanges"]
check(len(exchanges) == 13, "exchange count")
check(sum(1 for item in exchanges if item["phase"] == "main") == 9, "main exchanges")
check(sum(1 for item in exchanges if item["phase"] == "reopen") == 4, "reopen exchanges")
check(len({(item["phase"], item["ref"]) for item in exchanges}) == 13, "unique exchange refs")
long_hashes = set()
for item in exchanges:
    check(item["request_sha256_reconstructed"] == digest(record(item["length"], item["seed"])),
          f"request {item['ref']}")
    check(item["reply_sha256"] == digest(reply(item["length"], item["seed"])), f"reply {item['ref']}")
    check(item["reply_matched"] is True, f"matched {item['ref']}")
    check(item["send_terminal"]["source"] == "wire" and item["send_terminal"]["settled"] is True,
          f"send {item['ref']}")
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
check(len({(item["phase"], item["exchange_ref"]) for item in receipts}) == 13, "unique receipt refs")
expected_connection = {"A": "conn-A", "B": "conn-B"}
by_key = {(item["phase"], item["seq"]): item for item in receipts}
for item in exchanges:
    receipt = by_key[(item["phase"], item["receipt"]["seq"])]
    check(receipt["exchange_ref"] == item["ref"] and receipt["sha256"] == item["reply_sha256"] and
          receipt["at"] == item["receipt"]["at"], f"receipt {item['ref']}")
    check(receipt["phone_ref"] == item["phone_ref"], f"receipt phone {item['ref']}")
    check(receipt["connection_ref"] == expected_connection[item["phone_ref"]],
          f"receipt connection {item['ref']}")
    check(receipt["length"] == item["length"], f"receipt length {item['ref']}")

telemetry = SUMMARY["phone_telemetry"]
check({ref: (telemetry[ref]["api_success"], telemetry[ref]["notify_callback_seen"],
             telemetry[ref]["notify_api_nonzero"], telemetry[ref]["notify_callback_nonzero"],
             telemetry[ref]["record_complete"], telemetry[ref]["mtu"]) for ref in ("A", "B")} == {
    "A": (15, 15, 0, 0, 6, 517), "B": (24, 24, 0, 0, 7, 517)}, "telemetry counters")
for ref in ("A", "B"):
    check(telemetry[ref]["clean"] is True and telemetry[ref]["final"] is True, f"clean {ref}")
    check(telemetry[ref]["final_after_service_stopped"] is True, f"stopped {ref}")
    check(telemetry[ref]["content_digests_present"] is False, f"no content digests {ref}")
    check(telemetry[ref]["wall_clock_calibrated_with_mac"] is False, f"no clock calibration {ref}")
runner = SUMMARY["fixture_runner_witness"]
check(runner["A"]["instrument_stdout_has_final_junit"] is False and
      runner["A"]["separate_postrun_testrunner_log_available"] is True and
      runner["A"]["tests"] == 1 and runner["A"]["failed"] == 0 and
      runner["A"]["synthesized_ok_into_stdout"] is False, "A runner witness")
check(runner["B"]["instrument_stdout_has_final_junit"] is True and
      runner["B"]["instrument_stdout_value"] == "OK (1 test)", "B runner witness")

check(SUMMARY["transfers"]["protocol_rejection_4097"]["gatt_write_absence_proven"] is False and
      SUMMARY["transfers"]["protocol_rejection_4097"]["code"] == "oversize_record" and
      SUMMARY["transfers"]["protocol_rejection_4097"]["connection_still_connected"] is True, "4097 scope")
bounded = SUMMARY["bounded_observations"]
check(bounded["explicit_disconnect"]["observation_window_ms"] == 5000 and
      bounded["explicit_disconnect"]["reconnect_observed"] is False and
      bounded["explicit_disconnect"]["bounded_attached_foreground_only"] is True, "disconnect scope")
check(len(bounded["stale_local_refusals"]) == 2 and
      all(item["source"] == "local" and item["code"] == "unknown_handle" and
          item["stage"] == "connection_handle" for item in bounded["stale_local_refusals"]), "stale local")
check(bounded["reopen_integrity"]["injected_stale_event"] is False and
      bounded["reopen_integrity"]["records_match_fresh_handles"] is True, "reopen handles")

lifecycle = SUMMARY["lifecycle_observations"]
check(lifecycle["main_process"]["after_main_count"] == 0 and
      lifecycle["reopen_process"]["after_close_count"] == 0, "process observation")
check(lifecycle["sampling"]["ticks"] == 101 and lifecycle["sampling"]["max_gap_approx_ms"] == 2000, "sampling")
check(lifecycle["superseded_observer_v1"]["original_source_retained"] is False and
      lifecycle["superseded_observer_v1"]["count_as_multiple_host_processes"] is False, "observer v1")
check(lifecycle["main_process_id_published"] is False and lifecycle["exact_exit_latency_proven"] is False and
      lifecycle["stdin_eof_proven"] is False and lifecycle["stdout_framing_proven"] is False, "lifecycle limits")
check(SUMMARY["launcher_observation"]["literal_toolbar_click"] is False and
      SUMMARY["launcher_observation"]["native_port_calls_during_probe"] == 0 and
      SUMMARY["launcher_observation"]["worker_module_instance"] is False, "launcher scope")
check(SUMMARY["permissions"]["tcc_reset_or_grant_or_revocation_performed"] is False and
      SUMMARY["permissions"]["denial_recovery_experiment"] == "NOT_RUN", "permission scope")

integrity = SUMMARY["integrity"]
check(integrity["archive_sha256"] ==
      "d19cad4f51d2a6247756425edead48249b131c241c5aa1f314569e5b8458e10f" and
      integrity["archive_bytes"] == 61003 and integrity["archive_regular_file_members"] == 73, "archive")
check(integrity["mirror_files"] == 74 and integrity["uncatalogued_local_note_not_archived"] is True, "mirror")
check(integrity["catalog"]["entries"] == 73 and
      integrity["catalog"]["entries_verified_excluding_self"] == 72 and
      integrity["catalog"]["all_73_archive_members_match_mirror"] is True, "catalog accounting")
check(integrity["catalog"]["self_entry"]["recorded_sha256"] !=
      integrity["catalog"]["self_entry"]["actual_sha256"] and
      integrity["catalog"]["self_entry"]["status"] == "STALE_SELF_ENTRY_PRESERVED", "stale self-entry")
check(integrity["catalog"]["published_checker_self_hash_loop"] is False, "no self-hash loop")
check(SUMMARY["preserved_originals"]["sampler_v1_original_source_retained"] is False and
      SUMMARY["preserved_originals"]["pre_launch_original_archived"] is False, "preserved originals")

for name, value in SUMMARY["original_file_hashes"].items():
    check(re.fullmatch(r"[0-9a-f]{64}", value) is not None, f"hash shape {name}")
check(SUMMARY["original_file_hashes"]["remote/main.json"] ==
      "16aa5727b3a21dfbb50cebdbd59c05ec406176e536cd5296a3fc77773cc9be2d", "main hash")
check(SUMMARY["original_file_hashes"]["remote/reopen.json"] ==
      "22ded06ff27c5788f680dd433271df2016be79863e566e3db00588a9f6f70924", "reopen hash")
check(SUMMARY["original_file_hashes"]["remote/08-codesign-readback.txt"] ==
      "60480be3eb4b11bb4e232a4917760863e305ad3b58497295057ccf9935435230", "codesign hash")
check(SUMMARY["original_file_hashes"]["remote/15-signer-evidence.txt"] ==
      "009378b332ed54a19a6b66075d466d6a57be4a13b2e8978ef91e4d908b1c6140", "signer record hash")

text = (HERE / "summary.json").read_text(encoding="utf-8")
for forbidden in ("AppData", "Program Files", "chrome-extension://"):
    check(forbidden not in text, f"private-path/identifier leak: {forbidden}")
check(re.search(r"/(?:Users|Applications|Library)/", text) is None, "absolute-path-shaped value")
check(re.search(r"\b[a-p]{32}\b", text) is None, "extension-id-shaped value")
check(re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", text) is None,
      "UUID-shaped value")
check(re.search(r"\bpid[=: ]+\d+", text, re.I) is None, "PID-shaped value")
check(re.search(r"[\w.+-]+@[\w.-]+", text) is None, "email-shaped value")
print(f"PASS: 13 exchanges/13 receipts, 9 main + 4 reopen, telemetry A=15 B=24 MTU 517, "
      f"catalog 73 (72 + stale self), no leaks")
