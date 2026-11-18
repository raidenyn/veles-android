## 1. Prerequisite (TDD, Robolectric, no wall clock)

- [x] 1.1 Write failing `SharingState.prerequisite` tests:
  - an LE-capable, permitted, enabled adapter with `setIsMultipleAdvertisementSupported(false)` returns `null`;
  - one failure at a time with the earlier checks passing: no LE feature → `Unsupported`, missing permission → `PermissionRequired`, adapter off → `BluetoothOff`;
  - precedence: missing permission plus adapter off → `PermissionRequired`;
  - cover the missing-adapter and `SecurityException` cases where Robolectric can reach them; otherwise record why in `apply.md`.
- [x] 1.2 Remove the `isMultipleAdvertisementSupported` branch from `SharingState.prerequisite`, leaving every other check in order; make 1.1 pass.

## 2. Advertising result mapping (TDD)

- [x] 2.1 Write failing tests for a pure `advertiseFailureState(errorCode)`: `ADVERTISE_FAILED_FEATURE_UNSUPPORTED` → `Unsupported`; `DATA_TOO_LARGE`, `TOO_MANY_ADVERTISERS`, `ALREADY_STARTED`, `INTERNAL_ERROR` and an unknown code → `Failed`.
- [x] 2.2 In `SharingGattServerTest` (mocked `BluetoothLeAdvertiser`), after `onServiceAdded(GATT_SUCCESS)`, capture the `AdvertiseCallback` and assert:
  - `onStartFailure(FEATURE_UNSUPPORTED)` reports `Unsupported`;
  - another code reports `Failed`;
  - `onStartSuccess` reports `Advertising`;
  - an `onStartSuccess` and an `onStartFailure` delivered after `close()` each report nothing; drain the main looper (`shadowOf(Looper.getMainLooper()).idle()`) before asserting;
  - a `null` advertiser reports `Unsupported`.
- [x] 2.3 Add `advertiseFailureState` and use it in `onStartFailure`, keeping the `post { … }` closed-server guard and `onStartSuccess` unchanged; make 2.1 and 2.2 pass.

## 3. Service start and failure/retry (TDD)

- [x] 3.1 Extract the `SharingGattServer` state lambda in `SharingService` into an `internal fun onGattState(state)` with unchanged behaviour: `Advertising` → `SharingState.update`, anything else → `fail(state)`.
- [x] 3.2 Add a `SharingServiceTest` single-advertisement start.

  Make GATT startup deterministic first. Check whether Robolectric 4.14.1's shadow `BluetoothManager.openGattServer` returns a server whose `addService` returns `true`.
  - If it does, use it.
  - If not, add a minimal `internal` GATT factory seam on `SharingService`. Its default builds the real `SharingGattServer`; the test supplies a fake that only records the state sink.
  - Record which path was taken in `apply.md`.
  - In `@After`, destroy every service controller and reset `SharingState` (`update(Disabled)`, `setEnabled(false)`).

  Setup:
  - availability `Disabled`, LE feature present;
  - both permissions granted through the shadow application;
  - adapter `STATE_ON` with multi-advertisement false;
  - `enabled` preference set.

  Assert `onStartCommand(null, …)` returns `START_STICKY`, publishes `Starting`, has a foreground notification and did not stop itself.
- [x] 3.3 Add a `SharingServiceTest` failure-then-retry:
  - `onGattState(Unsupported)` on the started service publishes `Unsupported`, removes the foreground notification and stops the service;
  - a new service instance's `onStartCommand` publishes `Starting` in the foreground;
  - `onGattState(Advertising)` publishes `Advertising`.
- [x] 3.4 Run from `android/`:
  - `./gradlew testDebugUnitTest`;
  - the release boundary guard `./gradlew :app:releaseHookGuard`;
  - the lint gate `./gradlew spotlessCheck detekt lintDebug`.

  Run the raw `android.util.Log` guard from `.github/workflows/check-android.yml` from the repository root, since its paths start with `android/app/...`.

  Fix every failure.

## 4. Real-device verification (mandatory acceptance gate)

- [x] 4.1 Build the fixture APK and test APK from `android/` with the approved fixture signing init script (`android/AGENTS.md`).
  - Compare the new APK's signing certificate with the installed app's before `adb install -r`.
  - Never regenerate a key, uninstall or clear data. If the key is missing, stop and ask.
- [x] 4.2 OnePlus 6T: the issue's acceptance scenario. Follow `docs/otp-12-gate-b-runbook.md`'s "Common commands" and `locked-phone-20min` sections, with `APP=me.nagaev.veles.otp23` (the fixture build) and `R=$APP/me.nagaev.veles.debugprobe.ProbeReceiver`. Capture `adb logcat -v time -s VelesProbe` for the whole run.

  This run is stricter than the runbook: the phone stays locked and asleep, with the task removed, until the push has arrived.

  Steps:
  1. Enable the transport from the Home card. Confirm the card reaches `Advertising` (whatever it showed before Start).
  2. Chrome (development-channel connector) discovers and authenticates a `full` session; keep it open.
  3. Seed: `adb shell am broadcast -a me.nagaev.veles.debug.GATE_B_SEED -n "$R"`. With no `lifetimes` extra, the five default 600 s lifetimes are seeded.
  4. Schedule: `adb shell am broadcast -a me.nagaev.veles.debug.GATE_B_SCHEDULE_PUSH -n "$R" --es delay 1200`. Confirm the `scheduled` line with the process id; its controller-clock reading is minute 0, using the runbook's offset method.
  5. Lock and confirm sleep: `adb shell input keyevent 223`, then `dumpsys power` reports `mWakefulness=Asleep`.
  6. With the phone still locked, remove the task (`am stack list` / `am stack remove <id>`, or the Recents gesture) without force-stopping. Confirm it is still `Asleep` and the same `pidof` value.
  7. Keep the phone locked and asleep, with the task removed, through the `fired` line and the connector's receipt. Sample `dumpsys power`, `dumpsys activity services "$APP" | grep -i foreground` and `pidof` during the wait and at arrival. Arrival must be within minute 19 to 21 inclusive on the controller clock.
  8. After the arrival (which is after minute 15), unlock, reseed, and run `await velesDebug.pullAll()`. Record the fixture state.
  9. Record in `apply.md`:
     - the raw `scheduled`/`fired` lines;
     - minute 0, the offset method, and the lock, task-removal, arrival, unlock and pull times;
     - the wakefulness, foreground and process samples;
     - the exact build (version, commit, APK sha256, signing certificate) and the extension identity per the runbook.

  If the 6T reports `ADVERTISE_FAILED_FEATURE_UNSUPPORTED` (the card shows `Unsupported` after Start), stop and report it to the user as a hardware finding; do not mark this task done. A missed window or an unlock before arrival is a `FAIL`, recorded as is.
- [x] 4.3 OnePlus 6T restart: Stop sharing, then Start again, and confirm it advertises again. Record that a real `onStartFailure` → teardown → retry was not observed on hardware; task 3.3 covers it.
- [x] 4.4 OnePlus 10 Pro (multi-advertisement) regression: the same build starts sharing, reaches `Advertising`, and a Chrome session authenticates.
- [ ] 4.5 State what ran on which device in `apply.md` and the PR note. If a device or Chrome is unavailable, stop and ask; never report the change as tested.
