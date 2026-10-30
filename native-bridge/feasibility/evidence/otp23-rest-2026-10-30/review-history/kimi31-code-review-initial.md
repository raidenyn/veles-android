# Focused independent code review — Kimi K3.1 Pro

## Verdict: CHANGES REQUIRED

### Important — stale macOS guidance violates the platform gate after a platform change

**Files/lines:**
- `native-bridge/feasibility/extension/connector.js:71-81`
- `native-bridge/feasibility/extension/connector.js:138-145`
- Missing regression coverage in `native-bridge/feasibility/scripts/connector-api.test.mjs:71-82`

`updatePermissionGuidance` hides the panel only after a successful scan. `connect()` resets `state.platform`, but neither it nor a non-macOS `hello` clears an already-visible macOS panel. Consequently, this sequence leaves the exact macOS Settings instructions visible while reporting a Windows denial:

1. macOS `access_denied` displays the guidance.
2. Close and reconnect without a successful scan.
3. Complete `hello` with `platform = "windows"` and receive Windows `access_denied`.

The new connector test does not cover this sequence: its successful macOS scan at lines 71-74 clears the panel before the Windows case begins. I reproduced the uncovered sequence with an in-memory VM probe; it returned:

```json
{"macVisible":true,"windowsDeniedPanelHidden":false,"windowsDeniedText":"Bluetooth access was denied. Open System Settings -> Privacy & Security -> Bluetooth, enable VelesOtp23PoC, return to this connector, and retry the scan. Keep this extension loaded; no reinstall or permission reset is needed."}
```

This is a real violation of the stated platform gate, not a request for a new physical criterion. Clear/hide the guidance when the active platform becomes non-macOS (or otherwise guarantee that a Windows denial cannot inherit stale macOS guidance), and add a regression test for the sequence above with no intervening successful scan. Assert both that the panel is hidden and that the macOS text is cleared. Retain the existing exact-text, successful-scan-clearing, and same-platform recovery checks.

**Required checks after the fix:** rerun `node --test native-bridge/feasibility/scripts/connector-api.test.mjs` and `python3 -m unittest discover -s native-bridge/feasibility/scripts -p "test_macos_permission_recovery.py" -v`, plus the parent's affected required checks. The connector change requires focused re-review before approval.

**Physical-evidence impact:** this finding does **not** invalidate the prior same-macOS denial/recovery evidence or the Android physical evidence; none of those evaluations claims or exercises a macOS-to-Windows transition. It does block approval of the current connector code and therefore blocks the requested mechanical-integration permission until corrected and re-reviewed.

## Other scoped code

No additional meaningful code defect was found in the reviewed recovery driver/core/tests or Android physical fixture:

- The recovery driver reads the rendered DOM, requires exact denial guidance and cleared recovery guidance, aborts an invalid granted baseline or invalid/missing acknowledgement, and always enables `guidance_required` for new runs. The core remains opt-in for that requirement, preserving legacy frozen evaluations without granting them new M-04 credit.
- The Android fixture uses the real admitted `BluetoothDevice` for the explicitly labelled callback injection, checks peer/setup/admission-timestamp stability, labels the injection as non-OS-originated, and implements the requested negative-before-MTU followed by MTU/CCCD/echo sequence. The reviewed fixture inputs keep `BLUETOOTH_SCAN` in the explicit opt-in fixture manifest only.

These observations are limited to code suitability for the feasibility fixtures; they are not physical-evidence adjudication, production approval, OTP-24 acceptance, or a waiver of any criterion.

## Checks actually run by this reviewer

- `node --test native-bridge/feasibility/scripts/connector-api.test.mjs` — **3/3 passed**.
- `python3 -m unittest discover -s native-bridge/feasibility/scripts -p "test_macos_permission_recovery.py" -v` — **29/29 passed**.
- The separate in-memory platform-transition probe above — **failed the intended platform gate**, confirming the finding.

No hardware, credentials, repository edits, commits, pushes, or parent-reported verification results were used or claimed as my own.
