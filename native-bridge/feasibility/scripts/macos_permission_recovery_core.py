"""Pure verdict logic for the OTP-23 macOS persistent-profile permission-recovery validation.

No platform imports: the driver (macos_permission_recovery.py) gathers one record per phase and this
module decides. Requirement: the SAME extension state (same Chrome process, profile, extension ID and
a random synthetic sentinel persisted in the extension origin) must observe grant -> actual deny ->
recovery of the Bluetooth permission with the SAME signed host binary, without reinstall, TCC reset or
a second Extensions.loadUnpacked.

Fail closed: any missing phase/field, changed identity, unobserved denial, missing/invalid controller
ack or missing phone is a FAIL. The prompt observation (human supplied via the ack) is reported
separately and never turns a same-state recovery into a pass or a fail. A fresh first-prompt (new
account) cannot be proven by an already-granted account and is recorded as waived/not observed.
"""

import hashlib
import json

PHASES = ("baseline", "denied", "recovered")
READY_DISABLE = "human_disable_needed"
READY_ENABLE = "human_enable_needed"
PROMPT_VALUES = ("shown", "not_shown", "unknown")
DENIAL_HINTS = ("denied", "consent", "permission", "unauthor", "not_authorized")
STATE_KEYS = ("sentinel_sha256", "extension_id", "chrome_pid", "profile", "binary_sha256", "signature")
LABEL_MAX = 200
DEFAULT_HUMAN_NOTE = ("I enabled VelesOtp23PoC Bluetooth permission. "
                      "Chrome permission was enabled before that.")


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def classify_scan(reply):
    """started | access_denied | denied_wrong_code | other_error | timeout | missing."""
    if reply is None:
        return "missing"
    if not isinstance(reply, dict):
        return "other_error"
    if reply.get("timeout") is True:
        return "timeout"
    if reply.get("status") == "started":
        return "started"
    if reply.get("type") == "error" or "code" in reply or "stage" in reply:
        if "access_denied" in (reply.get("stage"), reply.get("code")):
            return "access_denied"
        blob = json.dumps(reply, sort_keys=True).lower()
        if any(hint in blob for hint in DENIAL_HINTS):
            return "denied_wrong_code"
    return "other_error"


def parse_ack(raw, nonce, expected_ready):
    """Validate a controller ack against the ready file it answers.

    Returns (ack, error). The ack must echo the nonce AND the ready phase name, state the toggle
    direction, carry the literal Settings entry label the human saw (never guessed) and a
    shown/not_shown/unknown prompt observation."""
    if not isinstance(raw, dict):
        return None, "ack_invalid"
    if raw.get("nonce") != nonce or raw.get("ready") != expected_ready:
        return None, "ack_mismatch"
    wanted = "off" if expected_ready == READY_DISABLE else "on"
    if raw.get("toggled") != wanted:
        return None, "ack_wrong_toggle"
    label = raw.get("entry_label")
    if not isinstance(label, str) or not label.strip() or len(label) > LABEL_MAX:
        return None, "ack_invalid"
    if raw.get("prompt") not in PROMPT_VALUES:
        return None, "ack_invalid"
    return {"toggled": wanted, "entry_label": label.strip(), "prompt": raw["prompt"],
            "note": str(raw.get("note", ""))[:LABEL_MAX]}, None


def _need(record, failures, phase, *keys):
    for key in keys:
        if record.get(key) is None:
            failures.append(f"{phase}:missing_{key}")


def _same_state(base, other, phase, failures):
    codes = {"sentinel_sha256": "sentinel_changed", "extension_id": "extension_changed",
             "chrome_pid": "chrome_changed", "profile": "profile_changed",
             "binary_sha256": "binary_changed", "signature": "signature_changed"}
    for key in STATE_KEYS:
        if other.get(key) is None:
            failures.append(f"{phase}:missing_{key}")
        elif other[key] != base.get(key):
            failures.append(f"{phase}:{codes[key]}")
    if other.get("load_unpacked_calls") != 1:
        failures.append(f"{phase}:reload_detected")


def _check_ack(record, phase, failures):
    if record.get("ack_error"):
        failures.append(f"{phase}:{record['ack_error']}")
    elif not isinstance(record.get("ack"), dict):
        failures.append(f"{phase}:ack_missing")


def evaluate(run):
    """Return {result: PASS|FAIL, failures: [...], phases: {...}, prompt_observation, ...}."""
    failures = []
    phases = {}
    for name in PHASES:
        if not isinstance(run.get(name), dict):
            failures.append(f"{name}:phase_missing")
    if run.get("abort"):
        failures.append("abort:" + str(run["abort"])[:120])

    base = run.get("baseline") if isinstance(run.get("baseline"), dict) else {}
    if base:
        before = len(failures)
        _need(base, failures, "baseline", *STATE_KEYS)
        if base.get("load_unpacked_calls") != 1:
            failures.append("baseline:reload_detected")
        if base.get("hello_type") != "ready":
            failures.append("baseline:hello_failed")
        if base.get("scan_class") != "started":
            failures.append("baseline:not_granted")
        elif not base.get("devices"):
            failures.append("baseline:no_phone")
        if base.get("host_gone") is not True:
            failures.append("baseline:host_survived")
        phases["baseline"] = {"ok": len(failures) == before}

    den = run.get("denied") if isinstance(run.get("denied"), dict) else {}
    if den and base:
        before = len(failures)
        _same_state(base, den, "denied", failures)
        _check_ack(den, "denied", failures)
        if den.get("hello_type") != "ready":
            failures.append("denied:hello_failed")
        scan = den.get("scan_class")
        if scan == "started":
            failures.append("denied:deny_not_observed")
        elif scan == "denied_wrong_code":
            failures.append("denied:deny_wrong_code")
        elif scan == "timeout":
            failures.append("denied:timeout")
        elif scan != "access_denied":
            failures.append("denied:deny_not_observed")
        if den.get("host_gone") is not True:
            failures.append("denied:host_survived")
        phases["denied"] = {"ok": len(failures) == before}

    rec = run.get("recovered") if isinstance(run.get("recovered"), dict) else {}
    if rec and base:
        before = len(failures)
        _same_state(base, rec, "recovered", failures)
        _check_ack(rec, "recovered", failures)
        if rec.get("hello_type") != "ready":
            failures.append("recovered:hello_failed")
        scan = rec.get("scan_class")
        if scan == "timeout":
            failures.append("recovered:timeout")
        elif scan != "started":
            failures.append("recovered:recover_not_permitted")
        elif not rec.get("devices"):
            failures.append("recovered:no_phone")
        if rec.get("stop_status") != "stopped":
            failures.append("recovered:scan_not_stopped")
        if rec.get("shutdown_type") != "shutdown_complete":
            failures.append("recovered:shutdown_failed")
        if rec.get("host_gone") is not True:
            failures.append("recovered:host_survived")
        phases["recovered"] = {"ok": len(failures) == before}

    if run.get("profile_removed") is not True:
        failures.append("final:profile_not_clean")
    if run.get("surviving_hosts"):
        failures.append("final:host_survived")

    prompt = (den.get("ack") or {}).get("prompt", "unknown") if isinstance(den.get("ack"), dict) else "unknown"
    return {
        "result": "FAIL" if failures else "PASS",
        "failures": failures,
        "phases": phases,
        "same_state_recovery": not any(":" in f and f.split(":")[1] in (
            "sentinel_changed", "extension_changed", "chrome_changed", "profile_changed",
            "reload_detected") for f in failures),
        "prompt_observation": {"value": prompt, "source": "human via ack file",
                               "popup_owner": "not inferred"},
        "fresh_first_prompt": "not_observed; waived non-blocking for new accounts "
                              "(this account was already granted)",
    }
