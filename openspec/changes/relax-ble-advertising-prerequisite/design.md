## Context

The Android sharing transport (`android/app/src/main/java/me/nagaev/veles/sharing/`) is a plain-GATT
peripheral. A foreground `SharingService` opens a `SharingGattServer`, adds the Veles service, and on
`onServiceAdded` success starts **one** connectable, low-latency advertisement whose data is only
the service UUID.

Availability is published through `SharingState.availability` (`SharingAvailability`) and rendered
by `SharingCard`.

`SharingState.prerequisite(context)` decides whether the transport may start. It is called in three
places:

1. `SharingCard`'s Start action, before `startService`;
2. `SharingService.onStartCommand`, before foreground promotion;
3. `SharingService`'s `ACTION_STATE_CHANGED` receiver, which fails the service on any non-null result.

Its last check, `!adapter.isMultipleAdvertisementSupported → Unsupported`, rejects the OnePlus 6T
(Android 15). As a result, that phone can neither share nor run the OTP-12 locked-phone Gate B
scenario.

In `SharingGattServer`, the `AdvertiseCallback` maps:
- `onStartSuccess` → `Advertising`;
- **every** `onStartFailure(errorCode)` → `Failed`.

`startAdvertising()` maps a `null` `bluetoothLeAdvertiser` to `Unsupported`. The service's state sink
treats any state other than `Advertising` as fatal: `fail(state)` closes the server, shuts the
sessions down, publishes the state and stops the service. The card's Start button is the retry path.

Constraints:
- the protocol, permission set, manifest and debug/release boundary must not change;
- unit tests must not use the wall clock;
- Android logic must be verified on a real device;
- fixture builds reuse the existing approved fixture signing identity.

## Goals / Non-Goals

**Goals:**
- A phone that can start one connectable BLE advertisement runs the transport, whatever
  `isMultipleAdvertisementSupported` reports.
- A phone that cannot advertise ends in a clear `Unsupported` state (or `Failed` for
  non-capability errors), and the existing Start retry path still works.
- Multi-advertisement phones behave as today: same checks, same successful start, same failure
  handling. The single exception is the `FEATURE_UNSUPPORTED` mapping below.
- The tracked issue's locked-phone Gate B scenario passes on the OnePlus 6T.

**Non-Goals:**
- Changing the advertisement data or settings, or using extended advertising sets.
- Changing the GATT contract, the protocol or the web extension.
- Adding new availability states, strings or UI.
- Fixing OEM recents behaviour on the OnePlus 10 Pro (non-blocking per the issue).

## Decisions

### D1: Remove the multi-advertisement condition from the prerequisite

Delete the `!adapter.isMultipleAdvertisementSupported -> SharingAvailability.Unsupported` branch.
These checks stay unchanged and in order:
1. `FEATURE_BLUETOOTH_LE` → `Unsupported`;
2. `BLUETOOTH_CONNECT`/`BLUETOOTH_ADVERTISE` → `PermissionRequired`;
3. missing adapter → `Unsupported`;
4. adapter off → `BluetoothOff`;
5. `SecurityException` → `PermissionRequired`.

All three callers benefit with no change of their own. The receiver keeps failing the service on
Bluetooth-off and permission loss exactly as before.

*Alternatives:*
- **A per-model allowlist.** It still trusts the wrong flag and needs a release per phone.
- **A soft flag with a fallback attempt.** Two sources of truth for one question, with no
  difference in outcome.

Both were rejected in the brainstorm.

### D2: The real advertising result is the capability signal

In `SharingGattServer`'s `AdvertiseCallback.onStartFailure(errorCode)`:
- `AdvertiseCallback.ADVERTISE_FAILED_FEATURE_UNSUPPORTED` → `Unsupported`;
- any other code (`DATA_TOO_LARGE`, `TOO_MANY_ADVERTISERS`, `ALREADY_STARTED`, `INTERNAL_ERROR`,
  unknown) → `Failed`.

The mapping is a small pure function, for example `advertiseFailureState(errorCode)`, so it can be
unit-tested without a controller. These stay unchanged:
- the `post { … }` wrapper (its closed-server guard included);
- `onStartSuccess` → `Advertising`;
- the `null`-advertiser → `Unsupported` branch.

*Why `FEATURE_UNSUPPORTED` only:* it is the one code that means "this controller cannot do it". The
others are transient (too many advertisers, internal error) or local faults (data too large, already
started) that keep today's `Failed` outcome.

### D3: Reuse the existing teardown and retry path

No change to `SharingService`'s state sink or `fail()`. An `Unsupported` or `Failed` advertising
result tears the service down and publishes the state, and Start retries. Because D1 no longer
blocks Start, a phone that truly cannot advertise briefly starts the foreground service before the
failure arrives. This is accepted (see Risks).

### D4: Tests

JVM/Robolectric only, with no sleeps and no wall clock:
- **`prerequisite` returns `null`** for a BLE-capable, permitted, enabled adapter that reports
  `isMultipleAdvertisementSupported == false`. Robolectric's `ShadowBluetoothAdapter` sets the flag;
  permissions are granted through the shadow application.
- **The `prerequisite` regressions keep their results:** no BLE feature → `Unsupported`, missing
  permission → `PermissionRequired`, adapter off → `BluetoothOff`.
- **`advertiseFailureState`:** `FEATURE_UNSUPPORTED` → `Unsupported`; each other documented code
  and an unknown code → `Failed`.
- **`SharingGattServerTest`**, which already mocks the `BluetoothLeAdvertiser`: capture the
  `AdvertiseCallback` after `onServiceAdded(GATT_SUCCESS)`, invoke `onStartFailure` with
  `FEATURE_UNSUPPORTED` and with another code, and check the reported states. Check that
  `onStartSuccess` still reports `Advertising`. Check that a `null` `bluetoothLeAdvertiser` still
  reports `Unsupported`.
- **`SharingServiceTest`, single-advertisement start** (Robolectric 4.14.1 provides the shadow APIs).
  Setup:
  - the availability starts at `Disabled`;
  - `FEATURE_BLUETOOTH_LE` is present;
  - `BLUETOOTH_CONNECT` and `BLUETOOTH_ADVERTISE` are granted through the shadow application's
    `grantPermissions`;
  - the shadow adapter is in `setState(STATE_ON)` with `setIsMultipleAdvertisementSupported(false)`;
  - the `enabled` preference is set.

  Then `onStartCommand(null, …)` must:
  - return `START_STICKY`;
  - publish `Starting`;
  - promote the service to the foreground (the shadow service has a foreground notification);
  - not stop the service itself.

  This is positive evidence that the prerequisite passed and GATT startup began, not merely the
  absence of `Unsupported`.
- **`SharingServiceTest`, failure then retry.** To make the service's GATT state sink testable
  without a real controller, extract the existing lambda into an `internal` method on
  `SharingService` (for example `onGattState(state)`) with unchanged behaviour: `Advertising` is
  published, and anything else calls `fail(state)`. From the started service above:
  - `onGattState(Unsupported)` publishes `Unsupported`, removes the foreground notification and stops
    the service;
  - a fresh `onStartCommand` on a new service instance publishes `Starting` and promotes it again;
  - `onGattState(Advertising)` then publishes `Advertising`.

  This proves the failure → teardown → fresh Start → success sequence at the service level. The
  controller-level trigger (`onStartFailure`) is covered by the `SharingGattServerTest` assertions
  above.

### D5: Real-device verification

Fixture build (`assembleDebug assembleDebugAndroidTest` with the approved fixture signing init
script), installed over the existing app after comparing signing certificates:

1. **OnePlus 6T (single-advertisement)**, the tracked issue's acceptance scenario:
   - the service starts and advertises, and Chrome discovers and authenticates a `full` session;
   - seed five fixture records with their default lifetimes;
   - `GATE_B_SCHEDULE_PUSH --es delay 1200` logs `scheduled` with the process id;
   - remove the task from recents, then lock the phone for at least 15 minutes;
   - the push arrives in minute 19 to 21 inclusive after `scheduled`, on the controller clock;
   - record a connector pull after minute 15;
   - capture the raw log lines, timings and exact build and identities.
2. **OnePlus 6T restart:** Stop, then Start again, advertises again. This checks a normal restart
   only. A real `onStartFailure` cannot be forced on the device without an extra fault hook, so
   failure → teardown → retry is proven by the service-level test in D4. The device report states
   that this sequence was not observed on hardware.
3. **OnePlus 10 Pro (multi-advertisement) regression:** the service starts and advertises, and a
   Chrome session authenticates.

The results are recorded in the change's `apply.md` and the PR note.

## Risks / Trade-offs

- **[Risk] The 6T controller rejects even one advertisement (`FEATURE_UNSUPPORTED`).**
  → The card then shows `Unsupported` and the acceptance gate cannot pass. Report it to the user as
  a hardware finding; never hide it or retry endlessly.
- **[Risk] A phone that cannot advertise briefly shows the foreground notification before
  failing.** → Acceptable: the service tears itself down on the failure callback, as it does today
  for `Failed`, and the state stays visible on the card.
- **[Trade-off] `FEATURE_UNSUPPORTED` changes from `Failed` to `Unsupported` on every device.**
  → This is intended. Multi-advertisement controllers are not expected to return it, and the
  `Unsupported` text is accurate.
- **[Risk] Robolectric's GATT shadows cannot drive a full service start.** → D4 keeps the
  service-level assertion to "not stopped by the prerequisite", and the real-device runs cover the
  full lifecycle.

## Migration Plan

None: there is no stored state, schema or protocol change. Rollback is reverting the commit.

## Open Questions

None blocking. The 6T outcome in Risks is the only unknown, and it is settled by the device run.
