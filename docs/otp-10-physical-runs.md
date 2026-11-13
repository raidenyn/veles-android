# OTP-10 physical runs — 2026-11-13

Phone-to-Chrome SPAKE2 enrollment, durable activation, reconnect and interrupted-activation
recovery over BLE, exercised from a real Android phone against Chrome on Windows 11 and macOS.
This is a development-channel run: the extension and the Native Messaging host are local
builds, not production artifacts.

## What was under test

| Item | Value |
| --- | --- |
| Branch | `feat/otp-10` |
| Commit under test | `1b740a9` (includes the physical-run fix below) |
| Android APK | `app-debug.apk`, package `me.nagaev.veles.otp23` (OTP-23 fixture build) |
| APK signing certificate SHA-256 | `9ef3104b934a37b111726ff91c7cb5ec5affc0e8b6a90742ed0bc55501969df9` |
| Installed APK certificate SHA-256 (before reinstall) | `9ef3104b934a37b111726ff91c7cb5ec5affc0e8b6a90742ed0bc55501969df9` |
| Reinstall | `pm install -r -d -g` on the OnePlus 6T (installed `versionCode` 3 → built `versionCode` 1, debug-only downgrade); app data preserved, no uninstall and no data clear |
| Extension | development channel, `web-extension/dist`, `version_name` `0.1.0-46-g1b740a9` |
| Windows extension id | `icoiadkjakfgpipmocdbheockdcbleni` |
| macOS extension id | `mhpehgcgnfejmeaocnpaihfaklopeabm` |
| Native host | `veles-native-host/0.1.0`, built locally from `native-bridge/host` at `1b740a9`; unsigned on Windows, ad-hoc signed app bundle on macOS; not the production host |

The phone is the repository's OTP-23 fixture build (`applicationIdSuffix = ".otp23"` via
`config/android_keys/fixture-signing.gradle`). The debug pairing controls are
`me.nagaev.veles.debugpairing.{START,CANCEL}` and the screen
`me.nagaev.veles.debugpairing.PairingDebugScreen`; trust was read from the app's
`no_backup/installation-trust.db` and from the extension's `chrome.storage.local`.

## Environment

| | Phone | Windows | macOS |
| --- | --- | --- | --- |
| Model / build | OnePlus 6T (ONEPLUS A6013), Android 15 | Windows 11 Pro 10.0.26300, build 26300 | MacBook Pro, macOS 26.5.2 (25F84) |
| Browser | — | Chrome 157.0.8103.78 | Chrome 157.0.8103.51 |
| Transport | BLE GATT server | BLE central via native host (WinRT) | BLE central via native host (CoreBluetooth) |

## Bug found by the run (fixed before the recorded steps)

The first Windows attempt stalled at `State: verified_prepare_ready · Fingerprint: C758 6584
7F3D`; the host then terminated the BLE link (`GATT_CONN_TERMINATE_PEER_USER` on the phone) and
the client timed out. Cause: the production `PairingLink` never sent `activation_commit`.
`ClientMachine.#onPrepare` returns no frames and expects the driver to call `commit()`, which
the CI loopback did by hand (`web-extension/test/session/loopback.test.ts`), masking the gap.
Fixed in `1b740a9` (the link now sends the commit after a verified prepare; the loopback and a
link unit test drive it through the link). The steps below are after that fix.

## Windows 11 + Chrome

Fresh state: the phone held no peer records and the Chrome profile held none (the debug Clear
control on both sides was used; the phone's Clear is the debug screen's peer-id field plus
"Clear incomplete record", Chrome's is the panel's per-record Clear).

| Step | Result | Observation |
| --- | --- | --- |
| 1. Pair with code `495184` | PASS | Panel reached `State: active · Fingerprint: C758 6584 7F3D`; the phone record became `ACTIVE` and the Chrome record `active_activation_pending`; both carried transcript `a7ff6e79…` and the same fingerprint |
| 2. Reconnect 1 (device-row Connect, user-selected) | PASS | Chrome record upgraded to `active`, `lastSeen` set, platform hint `winrt:6fed207647e4:1` captured |
| 3. Interrupted activation (pairing code `237535`) | PASS (interrupted as intended) | The phone reached `COMMITTED_WAITING_ACK`; the app was force-stopped to cut the link. Chrome kept a `pending` record (transcript `3ef8210e…`), the phone kept the `COMMITTED_WAITING_ACK` record |
| 4. Resume after the cut | PASS | After restarting the app and the sharing transport, a signed recovery session with the explicit phone installation id completed activation without a code: the phone record became `ACTIVE` (`lastSeen` set) and the Chrome record `active_activation_pending` |
| 5. Reconnect after recovery | PASS | A device-row Connect upgraded the Chrome record to `active`, `lastSeen` set, platform hint replaced with `winrt:473403c745d6:1` |

The Windows connector reported `Bridge veles-native-host/0.1.0 · Windows · API 1` throughout.
The extension id used by the host's compiled allowlist and the loaded unpacked extension was
`icoiadkjakfgpipmocdbheockdcbleni` (the unpacked-extension id is SHA-256 of the extension path,
UTF-16LE on Windows).

## macOS 26.5.2 + Chrome

Status: **BLOCKED at the macOS Bluetooth privacy grant; no pairing was attempted.**

| Step | Result | Observation |
| --- | --- | --- |
| 1. Extension and host | PASS | `Extensions.loadUnpacked` returned `mhpehgcgnfejmeaocnpaihfaklopeabm`; the connector reported `Bridge veles-native-host/0.1.0 · macOS · API 1` |
| 2. Phone search | BLOCKED | The connector showed “Allow Bluetooth access on this Mac … macOS blocked Bluetooth access for the Veles Native Bridge. In System Settings > Privacy & Security > Bluetooth, allow Bluetooth access for the Veles Native Bridge on this Mac, then choose Retry.” |
| 3. Privacy grant | NOT GRANTED | System Settings → Privacy & Security → Bluetooth lists `VelesNativeBridge` and two `veles-native-host` entries; enabling them from the GUI session did not clear the denial for the dev-built host |

The macOS host was built as an ad-hoc-signed `VelesNativeBridge.app` bundle with
`NSBluetoothAlwaysUsageDescription` and `LSUIElement`, launched through the Native Messaging
manifest `me.nagaev.veles.bridge` (registered per-user and under the dedicated Chrome profile's
`NativeMessagingHosts`). A grant recorded for a different build identity did not apply to this
one. The run needs a user-approved Bluetooth grant for this host identity before the macOS
steps can be executed; nothing was recorded for a macOS pairing.

## Limits

- Development builds only: the extension and the Android fixture key are not production
  artifacts; the Windows host is unsigned and the macOS host is ad-hoc signed.
- One phone (OnePlus 6T), one Chrome version per platform, one Windows build, one macOS build.
- The interrupted activation was produced by force-stopping the app after the phone reached
  `COMMITTED_WAITING_ACK`; the resume used the connector's debug `connect` hook with an explicit
  phone installation id, which is the documented explicit-id recovery path, not a product UI
  flow.
- Only the Windows run completed the pairing, reconnect and recovery steps; the macOS run is
  recorded as blocked at its first required permission grant.
- No keys, nonces or full installation identifiers are recorded here beyond the fingerprints,
  transcripts, hints and session values quoted above.
