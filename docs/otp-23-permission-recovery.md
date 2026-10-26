# OTP-23 macOS permission recovery (allow → deny → recover, same extension state)

Driver: `native-bridge/feasibility/scripts/macos_permission_recovery.py` (pure verdict logic in
`macos_permission_recovery_core.py`, tests in `test_macos_permission_recovery.py`).
Run it from an interactive **Aqua** session on the Mac with the existing registered, signed
`VelesOtp23PoC.app` (bundle ID `me.nagaev.veles.bridge`). It never signs, registers, edits System
Settings, resets TCC, reinstalls or clears state.

## Command (stays alive ≤ 10 minutes, every wait bounded)

```bash
cd native-bridge/feasibility
python3 scripts/macos_permission_recovery.py \
  --out evidence/macos-permission-recovery.json \
  --state-dir /tmp/otp23-permission-state \
  --expect-binary-sha-prefix e7c716      # optional guard against a swapped binary
```

Defaults: `--max-seconds 600`, `--wait-seconds 270` per controller wait. `--app` selects another
bundle only when the parent supplies one explicitly.

## Phases and parent-controlled phase files (outside the browser profile)

One disposable Chrome process/profile, one `Extensions.loadUnpacked`. A random synthetic sentinel is
stored in the extension origin's `localStorage`; only its SHA-256 is recorded.

| Phase | Driver does | Then writes |
|---|---|---|
| baseline | hello, startScan (granted), ≥1 phone, stopScan, close port, host gone | `ready.json` `human_disable_needed` |
| denied | (after ack) new connectNative, hello, startScan must be `access_denied`; close port, host gone | `ready.json` `human_enable_needed` |
| recovered | (after ack) new host, hello, startScan started, ≥1 phone, stopScan, shutdown, host gone; Chrome closed, profile removed | final JSON |

Parent (e.g. Linux over SSH): read `<state-dir>/ready.json` (`phase`, `nonce`), have the human toggle
the Settings entry, then write `<state-dir>/ack.json` atomically:

```json
{"nonce": "<ready.nonce>", "ready": "human_disable_needed", "toggled": "off",
 "entry_label": "<literal entry text the human saw>", "prompt": "shown|not_shown|unknown"}
```

Use `ready: human_enable_needed` / `toggled: on` for the second ack. The driver removes any stale
`ack.json` before publishing `ready.json`, and rejects a wrong nonce, wrong phase, wrong direction,
empty label or unknown prompt value.

## Verdict (fail closed)

PASS requires: baseline granted with ≥1 phone; denied phase hello ok and `access_denied` (exact
`stage`/`code`); recovered phase started + ≥1 phone + stop + shutdown; identical sentinel hash,
extension ID, Chrome PID, profile path, host binary SHA-256 and codesign summary across phases; exactly
one `loadUnpacked`; valid acks for both toggles; no surviving owned host; profile removed.
Separate failure codes: `deny_not_observed`, `deny_wrong_code`, `timeout`, `ack_missing`,
`ack_mismatch`, `ack_wrong_toggle`, `ack_invalid`, `sentinel_changed`, `binary_changed`,
`signature_changed`, `extension_changed`, `chrome_changed`, `profile_changed`, `reload_detected`,
`recover_not_permitted`, `no_phone`, `phase_missing`.

The popup prompt is reported only from the human's `prompt` field (`prompt_observation`); it never
changes PASS/FAIL, and the popup owner (Chrome vs the app) is not inferred.

## Evidence notes and limitations

- Human Settings observation, recorded as reported and not as TCC database attribution: "I enabled
  VelesOtp23PoC Bluetooth permission. Chrome permission was enabled before that."
- An already-granted account cannot prove a fresh first prompt: recorded as not observed, waived
  non-blocking for new accounts; this existing-account test remains required.
- Output records timestamps, command line, source revision, binary SHA-256, `codesign -dvv` summary
  when readable (else `available: false`) and a payload-free native trace summary.
- The unsigned DEV matrix is still unmet unless the parent supplies a separate explicit unsigned app
  via `--app`; nothing is created or ad-hoc signed here. Apple Development signing is not CI or
  distribution provenance (separate blocker).
- Foreground controller waits only; no background measurement or keepalive is implied.
- Tests are mock-only (`python3 -I -m unittest discover -s scripts -p test_macos_permission_recovery.py`);
  the real Aqua/device run has not been executed.
