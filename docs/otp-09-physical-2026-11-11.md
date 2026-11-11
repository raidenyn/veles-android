# OTP-09 physical round-trip evidence — 2026-11-11

Authenticated reconnect and protected-record round trip over BLE, exercised from a real
Android phone against Chrome on Windows 11 and macOS. This is a development-channel run:
the extension and the Native Messaging host are local builds, not production artifacts.
The run was repeated after the final-review fix wave; the values below are from that repeat.

## What was under test

| Item | Value |
| --- | --- |
| Branch | `feat/implement-otp-09` |
| Commit under test (Android APK) | `6a1faa1` (includes the final-review fix `a1d3d6c`) |
| Commit under test (extension) | `a1d3d6c` |
| Android APK | `app-debug.apk`, package `me.nagaev.veles.otp23` (OTP-23 fixture build) |
| APK SHA-256 | `681312321984c3472a1c661ca40a636a621730fcb09fab9bcd0c8bd8cfda301b` |
| APK signing certificate SHA-256 | `9ef3104b934a37b111726ff91c7cb5ec5affc0e8b6a90742ed0bc55501969df9` |
| Installed APK certificate SHA-256 (before reinstall) | `9ef3104b934a37b111726ff91c7cb5ec5affc0e8b6a90742ed0bc55501969df9` |
| Reinstall performed | Yes — `adb install -r` (same fixture `versionCode=1`); authorized 2026-11-10 |
| Extension | development channel, `web-extension/dist` |
| Extension `manifest.json` SHA-256 | `e5be1e3fdb6415b3cb2de8d469ad15f5d0ee7cf075df679c9c6ce0423feb72a8` |
| Windows extension id | `icoiadkjakfgpipmocdbheockdcbleni` |
| macOS extension id | `mhpehgcgnfejmeaocnpaihfaklopeabm` |
| Native host | `veles-native-host/0.1.0`, built locally from `native-bridge/host` on each machine with that machine's development extension id in `VELES_ALLOWED_ORIGINS`; unsigned, not the production host |

The Android package is the repository's OTP-23 fixture build (`applicationIdSuffix
= ".otp23"` via `config/android_keys/fixture-signing.gradle`), not `me.nagaev.veles.debug`;
the probe component is
`me.nagaev.veles.otp23/me.nagaev.veles.debugprobe.ProbeReceiver`.

`EXPORT_IDENTITY` returns the full installation id and public key only through the
ordered-broadcast result (`Broadcast completed: result=0, data="identity=… pub=…"`); logcat
carries eight-character prefixes only.

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
| 1. Inject trust both ways | PASS | `INJECT_PEER` for the Windows client; `velesDebug.injectPeer(phone)` resolved |
| 2. Authenticate | PASS | connection row `connected`; the probe worked, so the class was `full` |
| 3. Pull and ack | PASS | marker `V36RON`, event `afd9b5437f6d344586f0a4a56327e847`, sid `f611c8bfb1c973f5`; logcat `VelesProbe marker=` matched the readout |
| 4. adb push and ack | PASS | marker `VY06L5`, event `823fba81a36d68abdee806c679ac42bd`, same sid; shown in the readout |
| 5. Idle heartbeat | PASS (indirect) | 185 s with no application traffic, then a pull returned `VFDHB6` on the same sid; the session outlived the 180 s idle timeout, which is consistent with the heartbeat keeping it open, but the heartbeat frame itself was not observed directly |
| 6. Disconnect and reconnect | PASS | new sid prefix `4d751c738110e866`, distinct from step 3 |
| 7. Revoke and reconnect | PASS | revoked the Windows client; reconnect showed "Could not authenticate — re-pair required." and delivered no event |

### macOS 26.5.2 + Chrome

| Step | Result | Observation |
| --- | --- | --- |
| 1. Inject trust both ways | PASS | `INJECT_PEER` for the macOS client; `velesDebug.injectPeer(phone)` resolved |
| 2. Authenticate | PASS | connection row `connected`; the probe worked |
| 3. Pull and ack | PASS | marker `VRKLH8`, event `5b506aa5c14f96335942fdcf2a9a637a`, sid `eb77ff99869fa69f`; logcat matched |
| 4. adb push and ack | PASS | marker `VS62WZ`, event `874ece06dc0155d7916b9054b0fc3cce`, same sid; shown in the readout |
| 5. Idle heartbeat | PASS (indirect) | 185 s idle, then a pull returned `V5MDIG` on the same sid; see the Windows note on direct observation |
| 6. Disconnect and reconnect | PASS | new sid prefix `d40bdde82a03f62f`, distinct from step 3 |
| 7. Revoke and reconnect | PASS | revoked the macOS client; reconnect showed "Could not authenticate — re-pair required." and delivered no event |

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
- The heartbeat observation is indirect: survival past the 180 s idle timeout plus a
  successful pull afterwards, not a captured heartbeat record.
- No keys, nonces or full installation identifiers are recorded here beyond the probe's own
  marker/session-prefix output; installation ids appear only as the short prefixes from the
  logcat lines quoted above.
