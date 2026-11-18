## Why

`SharingState.prerequisite` refuses to start the Device sharing transport when
`BluetoothAdapter.isMultipleAdvertisementSupported` is false. That check is a leftover from Android 5,
and today it can refuse phones that advertise fine:

- **Origin.** It arrived with the first transport (OTP-06) as the proxy for the RFC's "unsupported
  peripheral advertising is an explicit unavailable state", with no other recorded reason. On
  Android 5.0 `getBluetoothLeAdvertiser()` returned `null` unless the flag was true, and on 5.1–7.x
  unless it or a hidden peripheral-mode flag was. Since Android 8.0 the advertiser no longer depends
  on it, and the app's `minSdk` is 33.
- **What it means now.** The Android 15 Bluetooth service reports it as true only when the controller
  declares at least 5 hardware advertising instances in a vendor-specific capability. A controller
  with 1–4 instances, or one that does not report that capability, reads false yet can advertise.
- **What the transport needs.** One connectable advertisement carrying one service UUID. Serving
  several clients uses several GATT connections, not several advertisements.

The tracked issue blamed this check for the OnePlus 6T showing the transport as unavailable. The
device run disproved that: the 6T reports the flag as true, and unchanged master advertises there.
The text the issue quoted was the `Disabled` state left after a reinstall, which follow-up
`raidenyn/veles-android#169` covers. This change is therefore a hardening, not the fix for an observed
failure: it removes a stale proxy and lets the real advertising attempt decide capability, while a
phone that truly cannot advertise still gets a clear `Unsupported` state.

## What Changes

**Sharing prerequisite**
- From: `SharingState.prerequisite` returns `Unsupported` when
  `BluetoothAdapter.isMultipleAdvertisementSupported` is false.
- To: that condition is removed. The BLE feature, permission, adapter-present and Bluetooth-on checks
  stay unchanged and in the same order.
- Reason: the flag answers a different question (at least 5 hardware advertising instances) than
  the one the transport needs answered (one advertisement).
- Impact: non-breaking. Phones that report the flag as false can now start the service, and phones
  that report it as true see no difference at this check.

**Advertising failure mapping**
- From: every `AdvertiseCallback.onStartFailure` code becomes `Failed`.
- To: `ADVERTISE_FAILED_FEATURE_UNSUPPORTED` becomes `Unsupported`, and every other code stays
  `Failed`. `onStartSuccess` → `Advertising` and a `null` advertiser → `Unsupported` are unchanged.
- Reason: the real advertising attempt becomes the capability signal, so a controller that cannot
  advertise still ends in a clear `Unsupported` state.
- Impact: non-breaking. The service's existing teardown and the card's Start retry path are reused.

There are no protocol, permission, manifest, string or UI changes, and no change to the
debug/release boundary.

## Capabilities

### New Capabilities
- `ble-sharing-availability`: when the Android sharing transport may start, and how its
  availability state (`Unsupported`, `Failed`, `Advertising`, …) is derived from the device
  prerequisites and the real BLE advertising result.

### Modified Capabilities
- None. No existing spec covers sharing-transport availability.

## Impact

- **Code**: `android/app/src/main/java/me/nagaev/veles/sharing/SharingState.kt` (the prerequisite)
  and `SharingGattServer.kt` (the advertising callback). `SharingService` and `SharingCard` are
  unchanged callers.
- **Tests**: JVM/Robolectric tests in `android/app/src/test/java/me/nagaev/veles/sharing/` for the
  relaxed prerequisite, the failure-code mapping and the service start on a single-advertisement
  adapter.
- **Real devices**: the tracked issue's locked-phone Gate B scenario on the OnePlus 6T with the
  fixture build, a Stop/Start retry there, and a start-and-advertise regression on the OnePlus 10
  Pro. Both phones report the flag as true, so no available phone exercises the relaxed branch on
  hardware; the JVM tests cover it.
- **Dependencies**: none added. The protocol and the web extension are unaffected.
