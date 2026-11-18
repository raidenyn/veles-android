## Correction after apply (2026-11-18)

The intent below records the tracked issue's premise, which the device run disproved: the OnePlus
6T reports `isMultipleAdvertisementSupported` as true, and unchanged master advertises there. The
card text the issue quoted was the `Disabled` state a reinstall leaves; follow-up
`raidenyn/veles-android#169` covers it.

The approach still holds, for a different reason. The check came from Android 5, where the flag
gated `getBluetoothLeAdvertiser()`. Since Android 8.0 it no longer does, and the app's `minSdk` is 33.
On Android 15 the flag means at least 5 hardware advertising instances, so it can refuse a phone
that advertises once. The change is a hardening that removes a stale proxy. The 6T Gate B run stays
the tracked issue's acceptance check, not proof that this change unblocked it. The user agreed to
this reframing on 2026-11-18.

## Design Summary

Tracked task: the OTP-12 follow-up "Relax the BLE multi-advertisement prerequisite for
single-advertisement phones". The source is the tracked issue and the OTP-12 real-device notes in
`openspec/changes/archive/2026-11-17-otp-12-gate-b-validation/apply.md`.

**Intent.** On a phone whose controller reports no support for *multiple simultaneous* BLE
advertisements (the OnePlus 6T, Android 15), the Device sharing transport card shows
`SharingAvailability.Unsupported` and `SharingService` never starts. The transport only ever starts
**one** advertisement, so the gate is stricter than what the transport uses. As a result, the OTP-12
locked-phone Gate B scenario cannot run on that phone. The goal is to let a phone that can start one
connectable advertisement run the transport, and to report `Unsupported` only when advertising
really cannot start.

**How it works today** (observed in the code):

- `SharingState.prerequisite(context)` (`android/app/src/main/java/me/nagaev/veles/sharing/SharingState.kt`)
  returns, in order: `Unsupported` without `FEATURE_BLUETOOTH_LE`; `PermissionRequired` without
  `BLUETOOTH_CONNECT`/`BLUETOOTH_ADVERTISE`; `Unsupported` without an adapter; `BluetoothOff` when the
  adapter is off; and **`Unsupported` when `adapter.isMultipleAdvertisementSupported` is false**. A
  `SecurityException` maps to `PermissionRequired`. A `null` result means "may start".
- It has three callers:
  - `SharingCard`'s Start action, before it calls `startService`;
  - `SharingService.onStartCommand`, before it promotes to the foreground;
  - `SharingService`'s Bluetooth state receiver, which fails the service on any non-null result.
- `SharingGattServer.start()` opens the GATT server and adds the service. `onServiceAdded` success
  calls `startAdvertising()`, which:
  - reports `Unsupported` when `adapter.bluetoothLeAdvertiser` is `null`;
  - otherwise starts **one** connectable `ADVERTISE_MODE_LOW_LATENCY` advertisement carrying only the
    service UUID.
- The `AdvertiseCallback` maps `onStartSuccess` to `Advertising` and **every** `onStartFailure` code to
  `Failed`. The service's state sink calls `fail(state)` for any state other than `Advertising`, which
  closes the server, shuts the sessions down, publishes the state and stops the service.
- The user's retry path is the card's Start button. It re-runs the prerequisite and starts the
  service again.

**The change**, in a few lines:

1. Remove the `isMultipleAdvertisementSupported` condition from `SharingState.prerequisite`. Every
   other condition and its order stay as they are.
2. Make the real advertising attempt the capability signal in `SharingGattServer`'s
   `AdvertiseCallback`:
   - `onStartFailure(ADVERTISE_FAILED_FEATURE_UNSUPPORTED)` → `Unsupported`;
   - every other failure code → `Failed`, as today;
   - `onStartSuccess` → `Advertising`, unchanged.
   - The `null`-advertiser case keeps reporting `Unsupported`.
3. Leave the failure handling (`fail()` teardown) and the retry path unchanged. A real
   advertising failure ends in a clear `Unsupported` or `Failed` state, and Start tries again.
4. No change to the protocol, permissions, manifest, user-visible strings or the release/debug
   boundary. On multi-advertisement phones the only difference is one failure case.
   `ADVERTISE_FAILED_FEATURE_UNSUPPORTED`, which such a controller is not expected to return, now
   reports `Unsupported` instead of `Failed`. Successful starts and every other failure behave
   exactly as today.

**Who it serves.** Users with single-advertisement phones, and the Gate B evidence runs that need
such a phone.

**Goals.** The transport starts and advertises on the OnePlus 6T. The OTP-12 locked-phone 20-minute
scenario passes there.

**Non-goals.**
- Changing the advertisement payload or settings, or adding extended advertising.
- Changing the GATT contract.
- Covering the OnePlus 10 Pro recents limitation. The issue marks it non-blocking.

**Anti-goals.**
- Silently claiming `Advertising` when the controller cannot advertise.
- Turning a genuine "cannot advertise" into an endless or hidden retry.
- Widening the permission set.

## Alternatives Considered

### Alternative A: Keep the gate, add a per-device allowlist
- **Approach**: Keep `isMultipleAdvertisementSupported`, but skip it for known models such as the
  OnePlus 6T.
- **Pros**: Smallest behavioural change on unknown hardware.
- **Cons**: Doesn't scale. It still trusts a flag that answers a different question. Every new
  single-advertisement phone needs a code change and a release.
- **Why not chosen**: It keeps the wrong signal and adds maintenance. The real advertising result
  answers the actual question for every device.

### Alternative B: Keep the gate as a soft check with a fallback attempt
- **Approach**: When `isMultipleAdvertisementSupported` is false, still try to advertise. Show
  `Unsupported` only if that attempt fails, and keep the flag as a hint elsewhere.
- **Pros**: Preserves the flag's information.
- **Cons**: Two sources of truth for one question. More branches to test. The flag would drive no
  decision once the attempt runs anyway.
- **Why not chosen**: It adds complexity and changes no outcome compared with dropping the flag.

### Alternative C: Drop the flag and let the real advertising result decide (chosen)
- **Approach**: As in the Design Summary.
- **Pros**:
  - Gates only on what the transport uses.
  - One source of truth.
  - Reuses the existing failure and retry path.
  - Small and fully unit-testable with the existing mocked advertiser.
- **Cons**: On a phone that truly cannot advertise, the user sees `Unsupported` only after the
  service has started and the attempt failed, not at the prerequisite check. The foreground service
  runs briefly.
- **Why chosen**: See Agreed Approach.

## Agreed Approach

Alternative C, approved by the user on 2026-11-17. It matches the issue's first suggested option and
the transport's real single-advertisement use. It keeps a clear `Unsupported`/`Failed` outcome and the
existing retry path. On multi-advertisement devices the flag was already true and the advertising
attempt already ran. Their only change is the `ADVERTISE_FAILED_FEATURE_UNSUPPORTED` mapping
described in the Design Summary.

## Key Decisions

- **Signal mapping**:
  - `ADVERTISE_FAILED_FEATURE_UNSUPPORTED` → `Unsupported`.
  - `ADVERTISE_FAILED_DATA_TOO_LARGE`, `ADVERTISE_FAILED_TOO_MANY_ADVERTISERS`,
    `ADVERTISE_FAILED_ALREADY_STARTED`, `ADVERTISE_FAILED_INTERNAL_ERROR` and any unknown code →
    `Failed`. These are not capability verdicts. Some are transient (too many advertisers, internal
    error). `DATA_TOO_LARGE` and `ALREADY_STARTED` are local faults that a plain retry with the same
    payload does not clear. All of them keep today's `Failed` outcome.
- **No new state, string or UI.** The existing `Unsupported` text ("Bluetooth LE peripheral
  advertising is unavailable on this device.") is accurate for the new trigger.
- **The prerequisite keeps all its other checks** in the same order. The Bluetooth state receiver
  therefore still fails the service on Bluetooth-off and permission loss exactly as today.
- **Unit tests** (JVM/Robolectric, no wall clock):
  - `prerequisite` returns `null` on an enabled, permitted adapter that reports no multi-advertisement
    support;
  - the advertising failure codes map to `Unsupported` / `Failed`;
  - success still maps to `Advertising`;
  - the `null` advertiser still maps to `Unsupported`;
  - a service-level Robolectric test: an enabled, permitted adapter that reports no
    multi-advertisement support no longer stops `SharingService` with `Unsupported` at the
    prerequisite.
  - The full sequence — failure, service teardown, a fresh Start, then successful advertising —
    depends on the real controller, so the device run below checks it.
- **Real-device verification**, required by the repository's real-device rule and by the issue's
  acceptance gate:
  - on the OnePlus 6T, the issue's full locked-phone Gate B scenario with the fixture build. The
    sharing service starts and advertises, and Chrome discovers and authenticates a `full` session.
    Then:
    - seed five fixture records with their default lifetimes;
    - run `GATE_B_SCHEDULE_PUSH --es delay 1200` and check that it logs `scheduled` with the process
      id;
    - remove the task from recents, then lock the phone for at least 15 minutes;
    - the push must arrive inside minute 19 to 21 inclusive after the `scheduled` line, measured on
      the controller clock;
    - record a connector pull after minute 15;
    - capture the raw log lines, timings and exact build and identities;
  - on the OnePlus 6T, a Stop followed by a fresh Start that advertises again (the retry path);
  - a start-and-advertise regression check on the OnePlus 10 Pro (multi-advertisement).
- **Fixture build signing.** The fixture APK is built with the existing approved fixture signing
  identity and installed over the existing app, comparing certificates first. No key is regenerated.

## Open Questions

- None blocking. If the OnePlus 6T controller still rejects the single advertisement
  (`ADVERTISE_FAILED_FEATURE_UNSUPPORTED`) once the gate is relaxed, the acceptance gate cannot pass on
  that phone. That outcome would be reported to the user as a finding about the hardware, not hidden.
