## Why

The Device sharing transport refuses to start on phones whose BLE controller reports no support for
*multiple simultaneous* advertisements, such as the OnePlus 6T. The transport only ever starts one
advertisement, so this gate is stricter than what it uses. It leaves those phones without sharing
and blocks the OTP-12 locked-phone Gate B scenario on the 6T. Gating on the real advertising result
lets every phone that can advertise once run the transport, while a phone that truly cannot still
gets a clear `Unsupported` state.

## What Changes

**Sharing prerequisite**
- From: `SharingState.prerequisite` returns `Unsupported` when
  `BluetoothAdapter.isMultipleAdvertisementSupported` is false.
- To: that condition is removed. The BLE feature, permission, adapter-present and Bluetooth-on checks
  stay unchanged and in the same order.
- Reason: the flag answers a different question (several concurrent advertisements) than the one
  the transport needs answered (one advertisement).
- Impact: non-breaking. Single-advertisement phones can now start the service, and
  multi-advertisement phones see no difference at this check.

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
  Pro.
- **Dependencies**: none added. The protocol and the web extension are unaffected.
