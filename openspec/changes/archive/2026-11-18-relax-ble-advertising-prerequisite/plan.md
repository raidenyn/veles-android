# Relax the BLE Multi-Advertisement Prerequisite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a phone that can start one BLE advertisement run the Android sharing transport, and report `Unsupported` only when the real advertising attempt says the controller cannot advertise.

**Architecture:**
- Remove the `isMultipleAdvertisementSupported` branch from `SharingState.prerequisite`.
- Map `AdvertiseCallback.onStartFailure` through a pure `advertiseFailureState(errorCode)`.
- Extract `SharingService`'s GATT state lambda into `internal fun onGattState(state)`, so the existing failure teardown and the Start retry are testable.

There are no protocol, permission, manifest, string or UI changes.

**Tech Stack:** Kotlin, Android (minSdk 33), JUnit4, Robolectric 4.14.1 (`@Config(sdk = [33])`), MockK, Gradle from `android/`.

**Spec:** `openspec/changes/relax-ble-advertising-prerequisite/specs/ble-sharing-availability/spec.md`; design: `design.md`; tasks: `tasks.md`; the same change directory.

## Global Constraints

- The prerequisite order stays: BLE feature → `Unsupported`; `BLUETOOTH_CONNECT`/`BLUETOOTH_ADVERTISE` → `PermissionRequired`; no adapter → `Unsupported`; adapter off → `BluetoothOff`; `SecurityException` → `PermissionRequired`.
- `ADVERTISE_FAILED_FEATURE_UNSUPPORTED` → `Unsupported`. `DATA_TOO_LARGE`, `TOO_MANY_ADVERTISERS`, `ALREADY_STARTED`, `INTERNAL_ERROR` and any unknown code → `Failed`.
- Unchanged: `onStartSuccess` → `Advertising`; a `null` advertiser → `Unsupported`; callbacks after `close()` are ignored (the `post { if (!closed) … }` guard).
- No protocol, permission, manifest, string, UI or debug/release-boundary change.
- Unit tests never sleep or read the wall clock (`.agents/rules/repo-no-time-based-tests.md`).
- No raw `android.util.Log`: use the existing logging, or none.
- Commit subjects are Conventional Commits with scope `android`.
- Fixture builds reuse the approved fixture signing identity. Never regenerate a key, uninstall or clear data (`android/AGENTS.md`).

## Review Focus

1. **Bluetooth turned off while sharing.** The `ACTION_STATE_CHANGED` receiver must still fail the service with `BluetoothOff`. Task 1's `adapterOffIsBluetoothOff` pins the receiver's input. The receiver and `fail()` are unchanged code, and Task 3's failure test pins the `fail()` teardown it calls. No new broadcast-driven test is added.
2. **A permission revoked while the adapter is off.** Precedence must give `PermissionRequired`. Pinned by the Task 1 precedence test.
3. **A late advertising callback after Stop.** It must not resurrect `Advertising` or overwrite `Disabled`. Pinned by the Task 2 post-close tests, for both success and failure.
4. **An unknown vendor failure code** (for example `99`). It must not crash or read as a capability verdict. Pinned by the Task 2 unknown-code case → `Failed`.
5. **A retry after `Unsupported`.** It must not inherit the failed service's state. Pinned by Task 3's fresh-instance retry test.

---

### Task 0: Branch

- [ ] **Step 1:** From the repository root, run `git fetch origin`, then create the feature branch from `origin/master`, carrying the uncommitted change directory onto it:
  `git switch -c fix/relax-ble-advertising-prerequisite origin/master`.
  If the switch refuses because of local changes, stop and ask.
  Commit only this change's files (`openspec/changes/relax-ble-advertising-prerequisite/`). Never add `openspec/changes/ble-transport-hardening/`, which belongs to another task.

### Task 1: Relax the prerequisite

**Files:**
- Modify: `android/app/src/main/java/me/nagaev/veles/sharing/SharingState.kt` (the `prerequisite` `when`)
- Create test: `android/app/src/test/java/me/nagaev/veles/sharing/SharingStateTest.kt`

**Interfaces:**
- Produces: `SharingState.prerequisite(context: Context): SharingAvailability?`, with the same signature and the multi-advertisement branch removed.

- [ ] **Step 1: Write the failing tests** in `SharingStateTest` (`@RunWith(RobolectricTestRunner::class) @Config(sdk = [33])`).

  Helper `ready()`:
  - `shadowOf(context.packageManager).setSystemFeature(FEATURE_BLUETOOTH_LE, true)`;
  - `shadowOf(context as Application).grantPermissions(BLUETOOTH_CONNECT, BLUETOOTH_ADVERTISE)`;
  - `shadowOf(adapter).setState(BluetoothAdapter.STATE_ON)`;
  - `shadowOf(adapter).setIsMultipleAdvertisementSupported(false)`;
  - `adapter` is `context.getSystemService(BluetoothManager::class.java).adapter`.

  Tests:
  - `singleAdvertisementAdapterPasses`: `ready()` → `assertNull(SharingState.prerequisite(context))`.
  - `missingLeFeatureIsUnsupported`: `ready()`, then the feature is false → `Unsupported`.
  - `missingPermissionIsPermissionRequired`: `ready()`, then `denyPermissions(BLUETOOTH_ADVERTISE)` → `PermissionRequired`.
  - `adapterOffIsBluetoothOff`: `ready()`, then `setState(STATE_OFF)` → `BluetoothOff`.
  - `permissionPrecedesBluetoothOff`: `ready()`, deny `BLUETOOTH_CONNECT`, `STATE_OFF` → `PermissionRequired`.
  - Missing adapter and `SecurityException`: attempt them with a MockK `Context` returning `getSystemService(BluetoothManager::class.java)`:
    - `null` → expect `Unsupported`;
    - an adapter whose `isEnabled` throws `SecurityException` → expect `PermissionRequired`.

    `ContextCompat.checkSelfPermission` needs `checkPermission` stubbed to `PERMISSION_GRANTED`, and `packageManager.hasSystemFeature` stubbed `true`. If MockK cannot stub them, record why in `apply.md`; this covers task 1.1's last bullet.

- [ ] **Step 2: Run and confirm the failure.**
  `cd android && ./gradlew testDebugUnitTest --tests "me.nagaev.veles.sharing.SharingStateTest"`.
  Expected: `singleAdvertisementAdapterPasses` FAILS (it gets `Unsupported`), and the others pass.

- [ ] **Step 3: Implement.** Delete the line `!adapter.isMultipleAdvertisementSupported -> SharingAvailability.Unsupported` from the `when`. Nothing else in the function changes.

- [ ] **Step 4: Run Step 2's command again.** Expected: all PASS. Then run `./gradlew testDebugUnitTest --tests "me.nagaev.veles.sharing.SharingServiceTest"`. Expected: PASS, since `enabledIntentSurvivesUnavailableRestorationWithoutClaimingAdvertising` uses the missing LE feature and stays valid.

- [ ] **Step 5: Commit.**
  `git add android/app/src/main/java/me/nagaev/veles/sharing/SharingState.kt android/app/src/test/java/me/nagaev/veles/sharing/SharingStateTest.kt`
  `git commit -m "fix(android): stop requiring multiple BLE advertisements for sharing"`

### Task 2: Map advertising failures by code

**Files:**
- Modify: `android/app/src/main/java/me/nagaev/veles/sharing/SharingGattServer.kt` (the `advertising` `AdvertiseCallback`, and a new top-level `internal` function in the same file)
- Modify test: `android/app/src/test/java/me/nagaev/veles/sharing/SharingGattServerTest.kt`

**Interfaces:**
- Produces: `internal fun advertiseFailureState(errorCode: Int): SharingAvailability`.

- [ ] **Step 1: Write the failing tests** in `SharingGattServerTest`, reusing its `setup()` (mock `advertiser`, `states`, `flush()`, `callback`/`service` slots). Helper `advertisingCallback()`:
  - `callback.captured.onServiceAdded(BluetoothGatt.GATT_SUCCESS, service.captured)`;
  - `flush()`;
  - `val cb = slot<AdvertiseCallback>()`; `verify { advertiser.startAdvertising(any(), any(), capture(cb)) }`; return `cb.captured`.

  Tests:
  - `advertiseFailureStateMapsOnlyFeatureUnsupportedToUnsupported`:
    - `assertEquals(Unsupported, advertiseFailureState(AdvertiseCallback.ADVERTISE_FAILED_FEATURE_UNSUPPORTED))`;
    - for each of `ADVERTISE_FAILED_DATA_TOO_LARGE`, `ADVERTISE_FAILED_TOO_MANY_ADVERTISERS`, `ADVERTISE_FAILED_ALREADY_STARTED`, `ADVERTISE_FAILED_INTERNAL_ERROR` and `99`: `assertEquals(Failed, …)`.
  - `featureUnsupportedStartFailureReportsUnsupported`: `onStartFailure(FEATURE_UNSUPPORTED)`, `flush()` → `states.last() == Unsupported`.
  - `otherStartFailureReportsFailed`: `onStartFailure(ADVERTISE_FAILED_INTERNAL_ERROR)`, `flush()` → `states.last() == Failed`.
  - `startSuccessStillReportsAdvertising`: `onStartSuccess(mockk(relaxed = true))`, `flush()` → `states.last() == Advertising`.
  - `advertisingCallbacksAfterCloseAreIgnored`:
    - capture the callback;
    - `subject.close()`; `val before = states.toList()`;
    - `onStartSuccess(…)`; `onStartFailure(FEATURE_UNSUPPORTED)`; `flush()`;
    - `assertEquals(before, states)`.
  - `nullAdvertiserReportsUnsupported`: `every { adapter.bluetoothLeAdvertiser } returns null`, `onServiceAdded(GATT_SUCCESS, …)`, `flush()` → `states.last() == Unsupported`.

- [ ] **Step 2: Run and confirm the failure.**
  `cd android && ./gradlew testDebugUnitTest --tests "me.nagaev.veles.sharing.SharingGattServerTest"`.
  Expected: compile failure on `advertiseFailureState`. After stubbing it, `featureUnsupportedStartFailureReportsUnsupported` fails with `Failed`.

- [ ] **Step 3: Implement.**
  - Add `internal fun advertiseFailureState(errorCode: Int): SharingAvailability`: a `when` on `AdvertiseCallback.ADVERTISE_FAILED_FEATURE_UNSUPPORTED` → `Unsupported`, `else` → `Failed`.
  - Change only the failure callback, to `override fun onStartFailure(errorCode: Int) = post { onState(advertiseFailureState(errorCode)) }`.

- [ ] **Step 4: Run Step 2's command again.** Expected: all `SharingGattServerTest` tests PASS, including the existing ones.

- [ ] **Step 5: Commit.**
  `git add android/app/src/main/java/me/nagaev/veles/sharing/SharingGattServer.kt android/app/src/test/java/me/nagaev/veles/sharing/SharingGattServerTest.kt`
  `git commit -m "fix(android): report Unsupported only when BLE advertising is unsupported"`

### Task 3: Service start and failure/retry

**Files:**
- Modify: `android/app/src/main/java/me/nagaev/veles/sharing/SharingService.kt` (the state lambda passed to `SharingGattServer`, and possibly a GATT factory seam)
- Modify test: `android/app/src/test/java/me/nagaev/veles/sharing/SharingServiceTest.kt`

**Interfaces:**
- Consumes: Task 1's relaxed `prerequisite`.
- Produces: `internal fun onGattState(state: SharingAvailability)` on `SharingService`. `Advertising` → `SharingState.update(state)`, and anything else → `fail(state)`, exactly as the old lambda did.

- [ ] **Step 1: Decide the GATT startup path.**
  - In a scratch test, start `SharingService` under Robolectric with Task 1's `ready()` setup and check two things:
    - whether `onStartCommand(null, 0, 1)` returns `START_STICKY`;
    - whether `SharingState.availability.value` is `Starting`, that is, whether the shadow `openGattServer` returned a server and `addService` returned `true`.
  - If yes, use the shadow.
  - If not, add `internal var gattFactory: (Context, (SharingAvailability) -> Unit, ConnectionListener) -> SharingGattServer` to `SharingService`:
    - its default is `::SharingGattServer`;
    - the test supplies a `SharingGattServer` built over a MockK `Context`/`BluetoothManager`, as `SharingGattServerTest` does, with `addService` returning `true`.
  - Record the chosen path in `apply.md`. Delete the scratch test.

- [ ] **Step 2: Write the failing tests** in `SharingServiceTest`.
  - Add `@After fun reset()`, which destroys every built controller and runs `SharingState.update(Disabled)` and `SharingState.setEnabled(context, false)`.
  - `singleAdvertisementAdapterStartsTheForegroundService`:
    - setup: `ready()`, `SharingState.update(Disabled)`, `enabled` preference set;
    - `assertEquals(START_STICKY, service.onStartCommand(null, 0, 1))`;
    - `assertEquals(Starting, SharingState.availability.value)`;
    - `assertNotNull(shadowOf(service).lastForegroundNotification)`;
    - `assertFalse(shadowOf(service).isStoppedBySelf)`.
  - `advertisingFailureStopsServiceAndAFreshStartRetries`:
    - from the started service, `service.onGattState(Unsupported)`;
    - assert `Unsupported`, `shadowOf(service).isStoppedBySelf`, `shadowOf(service).isForegroundStopped`, and `assertNull(shadowOf(service).lastForegroundNotification)` (removal, not just a stop: `STOP_FOREGROUND_REMOVE`);
    - build a new controller and service: `onStartCommand(null, 0, 1)` → `Starting` with a foreground notification;
    - `onGattState(Advertising)` → `Advertising`.

- [ ] **Step 3: Run and confirm the failure.**
  `cd android && ./gradlew testDebugUnitTest --tests "me.nagaev.veles.sharing.SharingServiceTest"`.
  Expected: compile failure on `onGattState`.

- [ ] **Step 4: Implement.**
  - Add `onGattState` with the old lambda's body.
  - Pass `::onGattState` to `SharingGattServer`, or through `gattFactory` if Step 1 added it.
  - No other behaviour change.

- [ ] **Step 5: Run Step 3's command again.** Expected: PASS.

- [ ] **Step 6: Commit.**
  `git add android/app/src/main/java/me/nagaev/veles/sharing/SharingService.kt android/app/src/test/java/me/nagaev/veles/sharing/SharingServiceTest.kt`
  `git commit -m "test(android): cover single-advertisement start and advertising failure retry"`

### Task 4: Checks before push

- [ ] **Step 1:** From `android/`, run `./gradlew testDebugUnitTest`. Expected: BUILD SUCCESSFUL.
- [ ] **Step 2:** From `android/`, run `./gradlew :app:releaseHookGuard`. Expected: BUILD SUCCESSFUL.
- [ ] **Step 3:** From `android/`, run `./gradlew spotlessCheck detekt lintDebug`. Expected: BUILD SUCCESSFUL. On a format failure, run `./gradlew spotlessApply`, inspect the diff, rerun and commit with `style(android): …`.
- [ ] **Step 4:** From the repository root, run the two raw-Log guards from `.github/workflows/check-android.yml` (the `grep -rn "android.util.Log\|import android.util\.\*"` and `grep -rnE "Log\.[dweiv]\("` lines, each piped through `grep -v LogSink.kt`). Expected: no output.
- [ ] **Step 5:** Run `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)`, then the checks listed in each selected node's `AGENTS.md` (`.agents/rules/repo-pre-push-checks.md`).

### Task 5: Real-device verification (mandatory acceptance gate)

Follow `tasks.md` 4.1–4.5 exactly; they hold the full step list, and this task does not repeat it. Use the `homepc` skill for the phones, Chrome and adb. If a device or Chrome is unavailable, stop and ask.

- [ ] **Step 1:** Build and install the fixture build (`tasks.md` 4.1), comparing certificates before installing.
- [ ] **Step 2:** OnePlus 6T locked-phone Gate B scenario (`tasks.md` 4.2). The phone stays asleep, with the task removed, until the push arrives in minute 19 to 21.
- [ ] **Step 3:** OnePlus 6T Stop/Start restart (`tasks.md` 4.3).
- [ ] **Step 4:** OnePlus 10 Pro regression (`tasks.md` 4.4).
- [ ] **Step 5:** Write the evidence and the device list into `apply.md` and commit it: `git add openspec/changes/relax-ble-advertising-prerequisite/apply.md` then `git commit -m "docs(android): record single-advertisement device verification"`. The same text goes into the PR note (`tasks.md` 4.5).
