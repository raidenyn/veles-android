# OTP-06 Android peripheral transport

## Scope and operation

The Home screen's **Device sharing transport** card requests Nearby devices
permissions and starts the service from a visible action. The service immediately
promotes itself to a `connectedDevice` foreground service. Starting with
`startService` from the visible activity avoids an outstanding
`startForegroundService` obligation if prerequisites disappear before promotion.
The foreground notification opens Home and provides **Stop sharing**.

The enabled preference and actual availability are separate: an enabled but
unavailable transport offers retry and disable. Android may restore a killed
sticky service while the preference remains enabled; restoration rechecks all
prerequisites. Force-stop, reboot, or an explicit failure does not promise an
automatic restart: reopen Home and retry. Disable clears intent and closes all
advertising, links, queues and reassembly state. Capability/permission failures
are explicit. There is no scan, location, bonding, or network requirement.

## GATT contract

Use the frozen UUIDs and properties in `docs/protocol/veles-v1.md`, section 9.
Clients must negotiate MTU >=185, then enable notifications through the notify
characteristic's CCCD before sending records. Setup must complete within five
seconds. The ninth client is refused; reads, prepared writes, execute writes,
write-with-response, invalid offsets and invalid CCCD values are rejected.

Records are 1..4096 bytes with uniform four-byte fragment headers. Non-final
payloads are exactly MTU-7 bytes. Reassembly supports two incomplete transactions
and 8 KiB per peer, 16 and 64 KiB globally, matching the frozen fixtures. Senders
serialize one transaction per characteristic. Partial records expire after five
seconds of inactivity; repeated conflicting transaction starts disconnect only
the offending peer. Outbound queues hold at most 16 records/16 KiB per peer and
128 records/128 KiB globally, including in-flight records; overflow drops newest.
Notification stalls disconnect the affected peer after five seconds.

Copied binder ingress is separately bounded to 16 callbacks per admitted peer,
eight peers total. Rejected addresses cannot consume those budgets. Platform
callbacks are serialized on the main looper, and a closed adapter ignores late
callbacks from its own generation. Bluetooth loss and permission failures close
the adapter. Transport completion does not mean authentication or application
acknowledgement.

## Synthetic fixture

Only debug builds respond to complete bytes `56 45 4c 01`, with bytes
`56 45 4c 02`. At MTU 185 a complete request is the write-without-response value
`80 01 00 04 56 45 4c 01`; the response is independently fragmented on notify.
This is a transport fixture, not a protected protocol record or OTP. Release
builds discard completed records until the later protected-session integration.
The sharing package has no production OTP-handler dependency.

## Verification (2026-10-25)

- `JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew testDebugUnitTest` passed, including
  production-receiver consumption of the shared fragmentation fixtures,
  queue/capacity/timeout tests, and Robolectric adapter/service tests.
- `./gradlew spotlessCheck detekt lintDebug` passed with that JDK; Android lint
  reports warnings, not errors. The CI raw `android.util.Log` guard passed.
- Debug APK, instrumentation APK and R8-minified unsigned release APK assembled.
- Inspected the **built release APK** using `apkanalyzer manifest print`:
  sharing adds only `BLUETOOTH_CONNECT`, `BLUETOOTH_ADVERTISE`,
  `FOREGROUND_SERVICE`, and `FOREGROUND_SERVICE_CONNECTED_DEVICE`; optional BLE
  feature; non-exported service with connected-device type (`0x10`). No Bluetooth
  scan or location permissions. Existing dependency-contributed WorkManager
  permissions remain present.
- Physical OnePlus 10 Pro, Android 15: installed debug and test APKs;
  `SharingManifestTest` and `SharingLifecycleTest` passed. The latter launches a
  visible activity, starts the real service, waits for the real advertising
  callback, then stops and waits for disabled state. Raw instrumentation status
  was success (`0`), not an assumption skip. `adb install -r -g` provided test
  runtime grants because this phone blocks `pm grant`.
- Mobile UI hierarchy automation was unavailable on this phone; a screenshot
  confirmed the Home transport card. Full BLE central round trips, two-client
  hardware concurrency, and radio interruption matrices are not claimed by this
  smoke test; native-central integration belongs to OTP-23 and later gates.

## Requested final review

Local Claude Code accepted and used **`claude-opus-5-5`** for the whole-change
review. Its blocking findings were addressed:

- C1: two concurrent reassembly transactions and cap-before-allocation, covered
  by a regression observed failing before the fix and the shared fixtures.
- I1: visible normal-service start followed by immediate promotion, so an early
  failure does not leave an AMS foreground-start obligation.
- I2: expose persisted enabled intent separately from actual availability, with
  unavailable-restore and destruction tests.
- I3: thread-safe admission check before copying/budgeting ingress; rejected-peer
  flood regression.
- I4: subscription as well as MTU setup deadline; MTU-only timeout regression.
- I5: adapter tests for late callbacks, capacity, setup expiry, invalid operations,
  notification-failure isolation and rejected ingress, plus service and fixture
  coverage. A full platform/radio interruption matrix remains later-gate work.

Deferred non-blocking review notes: use a non-exported system broadcast receiver;
inbound callback copies removed during close are left to GC rather than explicitly
zeroed (and the ingress comment rounds 65,792 bytes to 64 KiB); duplicate connected
callbacks could cancel an existing link; CCCD-before-MTU is intentionally rejected;
defensive advertising-stop on a late start callback; hardware two-client
notification concurrency; stronger exact-permission and source-dependency guard
tests. These do not add real OTP exposure at this synthetic-only stage.
