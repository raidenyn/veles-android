# OTP-09 physical round-trip evidence — 2026-11-11

Authenticated reconnect and protected-record round trip over BLE, exercised from a real
Android phone against Chrome on Windows 11 and macOS. This is a development-channel run:
the extension and the Native Messaging host are local builds, not production artifacts.

## What was under test

| Item | Value |
| --- | --- |
| Branch | `feat/implement-otp-09` |
| Commit under test | `6a3720bc8985bcfc09da241e848dff993fd51682` |
| Android APK | `app-debug.apk`, package `me.nagaev.veles.otp23` (OTP-23 fixture build) |
| APK SHA-256 | `c80f4723aef4404aab820b58239230f03184933ffad9806fed7d3858152ea25d` |
| APK signing certificate SHA-256 | `9ef3104b934a37b111726ff91c7cb5ec5affc0e8b6a90742ed0bc55501969df9` |
| Installed APK certificate SHA-256 (before reinstall) | `9ef3104b934a37b111726ff91c7cb5ec5affc0e8b6a90742ed0bc55501969df9` |
| Reinstall performed | Yes — `adb install -r -d` (fixture build `versionCode=1` replaced `3`); authorized 2026-11-10 |
| Extension | development channel, `web-extension/dist` |
| Extension `manifest.json` SHA-256 | `0a7a91aef289a720a6207e0dfbc148426a87f328b3cdbea4ad2e9e28cba01839` |
| Windows extension id | `icoiadkjakfgpipmocdbheockdcbleni` |
| macOS extension id | `mhpehgcgnfejmeaocnpaihfaklopeabm` |
| Native host | `veles-native-host/0.1.0`, built locally from `native-bridge/host` on each machine with that machine's development extension id in `VELES_ALLOWED_ORIGINS`; unsigned, not the production host |

The Android package is the repository's OTP-23 fixture build (`applicationIdSuffix
= ".otp23"` via `config/android_keys/fixture-signing.gradle`), not `me.nagaev.veles.debug`;
the probe component is
`me.nagaev.veles.otp23/me.nagaev.veles.debugprobe.ProbeReceiver`.

## Environment

| | Phone | Windows | macOS |
| --- | --- | --- | --- |
| Model / build | OnePlus NE2215, Android 15 | Windows 11 Pro, 10.0.26300 | MacBookPro18,2, macOS 26.5.2 (25F84) |
| Browser | — | Chrome 157.0.8103.39 | Chrome 157.0.8103.39 |
| Transport | BLE GATT server | BLE central via native host (WinRT) | BLE central via native host (CoreBluetooth) |

The macOS host's first BLE attempt was refused by macOS privacy until Bluetooth access was
granted for "Veles Native Bridge" in System Settings; the run below is after that grant.

## Steps and observed results

Commands used the explicit probe component with the actions
`me.nagaev.veles.debug.{EXPORT_IDENTITY,INJECT_PEER,PUSH,REVOKE_PEER}`.

### Windows 11 + Chrome

| Step | Result | Observation |
| --- | --- | --- |
| 1. Inject trust both ways | PASS | `INJECT_PEER` logged `injected peer=1598d2a7…`; `velesDebug.injectPeer(phone)` resolved |
| 2. Authenticate | PASS | connection row `connected`; class `full` implied by the probe working |
| 3. Pull and ack | PASS | marker `VCR4JS`, event `a8b2e40d6c6a28b69acfe482145a56a1`, sid `5650dbfec4f12396`; logcat `VelesProbe marker=` matched the readout |
| 4. adb push and ack | PASS | marker `V1V0DA`, event `d29a286c4aa396cbec360a6592be2b12`, same sid; shown in the readout |
| 5. Idle heartbeat | PASS | 185 s with no application traffic, then a pull succeeded; an idle timeout would have closed the session at 180 s |
| 6. Disconnect and reconnect | PASS | new sid prefix `af49aa16833b60c9`, distinct from step 3 |
| 7. Revoke and reconnect | PASS | `revoked peer=1598d2a7…`; reconnect showed "Could not authenticate — re-pair required." and delivered no event |

### macOS 26.5.2 + Chrome

| Step | Result | Observation |
| --- | --- | --- |
| 1. Inject trust both ways | PASS | `INJECT_PEER` logged `injected peer=0e3fb6ac…`; `velesDebug.injectPeer(phone)` resolved |
| 2. Authenticate | PASS | connection row `connected`; probe worked |
| 3. Pull and ack | PASS | marker `V29UEG`, event `d9a7be186e6d7513f2876aa366387b4a`, sid `957db55cbc7ab46f`; logcat matched |
| 4. adb push and ack | PASS | marker `VHO4M9`, event `11c36eb6708b11f6d03a989a3ce01ba4`, same sid; shown in the readout |
| 5. Idle heartbeat | PASS | 185 s idle, then a pull succeeded |
| 6. Disconnect and reconnect | PASS | new sid prefix `ab7735f95ac878c1`, distinct from step 3 |
| 7. Revoke and reconnect | PASS | `revoked peer=0e3fb6ac…`; reconnect showed "Could not authenticate — re-pair required." and delivered no event |

Note on step 7: the development readout keeps the last event it displayed, so after the
failed reconnect it still showed the marker from the preceding authenticated session. No new
event was delivered and the failure banner was shown; "empty readout" in the plan was
observed as "no new data", not as a blank panel.

## Limits

- Development builds only: the extension, the native host and the Android fixture key are not
  production artifacts, and the host is unsigned.
- One phone model, one Chrome version, one Windows build and one macOS build.
- Step 7 is a trust-record removal while disconnected, not the OTP-11 live revocation of a
  connected peer.
- No keys, nonces or full installation identifiers are recorded here beyond the probe's own
  marker/session-prefix output; installation-id prefixes appear only in command output
  transcribed above as short prefixes.
