# Veles connector-tab Web Bluetooth spike

> **Synthetic data only.** This harness uses a public test key and synthetic
> six-digit values. Never use it with a real OTP or notification.

## Current status

PR #78 established two binding platform findings:

- Desktop Chrome cannot present the Web Bluetooth chooser from an MV3 action
  popup. The extension action therefore opens or focuses a connector tab.
- Windows Chrome cannot reliably use MITM-gated GATT attributes. The harness
  therefore uses plain GATT and remains synthetic-only.

Plain-GATT pull, push, direct clipboard copy, connector closure, and one-tab/
two-phone flows were spot-checked. The evidence is incomplete and Issue #77 is
not a go decision.

The current harness has two known limitations:

- Its five-second JavaScript heartbeat can be throttled when the connector tab
  is backgrounded, causing the Android 15-second session timeout.
- Its direct `navigator.clipboard.writeText()` call requires the connector tab
  to be focused.

The stable product requires the connector tab to remain useful while open but
backgrounded. Before the remaining physical matrix runs, the synthetic harness
must replace the short-timer dependency and route background automatic copy
through the supported offscreen clipboard helper. Bluetooth must remain owned
by the connector tab.

The current roadmap is
`docs/superpowers/specs/2026-08-11-android-chrome-bluetooth-otp-sharing-design.md`.
Recorded evidence is
`docs/spikes/2026-08-11-popup-web-bluetooth-validation.md`.

## Environment boundary

The implementation environment can build Android, run JVM and Node tests, and
inspect extension files. It has no Chrome browser, Android device, Bluetooth
adapter, Windows host, or macOS host. Automated checks cannot establish physical
Bluetooth behavior.

Physical results require:

- Stable Chrome on Windows and macOS.
- Android 13 or newer.
- Two physical desktop computers for fan-out.
- Two Android phones for multi-phone testing.
- Exact commit, device, OS, Chrome, and Bluetooth-adapter records.

## Check out the branch

GitHub CLI:

```bash
git switch master
git pull --ff-only
gh pr checkout 78
git rev-parse HEAD
```

Normal Git:

```bash
git fetch origin feat/77-popup-web-bluetooth-spike
git switch --create feat/77-popup-web-bluetooth-spike \
  --track origin/feat/77-popup-web-bluetooth-spike
git rev-parse HEAD
```

After a PR update:

```bash
git switch feat/77-popup-web-bluetooth-spike
git pull --ff-only
git rev-parse HEAD
```

Record the exact SHA for every physical run. Do not reuse a result after a
harness change unless the PR explicitly says that case is unaffected.

## Build and install Android

Prerequisites:

- JDK 17.
- Android SDK and platform-tools.
- An Android 13+ phone with USB or Wi-Fi debugging.

Build and install:

```bash
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Open the second debug launcher named **Veles BLE Spike**. Grant Nearby Devices
and notification permissions, enable Bluetooth, and select **Start service**.
Confirm that the foreground indication appears and the activity reports
Advertising.

The harness supports:

- **Push now** for an immediate synthetic event.
- **Schedule in 10 seconds** for a smoke event.
- **Schedule in 20 minutes** for the foreground-lifecycle case.
- **Stop service** to close GATT, advertising, clients, and scheduled events.
- Copying the diagnostic event log for a physical report.

Reset only between cases that require a clean application state:

```bash
adb shell am force-stop me.nagaev.veles.debug
adb shell pm clear me.nagaev.veles.debug
```

Do not force-stop or clear the application during the foreground-lifecycle
case.

## OS Bluetooth pairing

Do not pair the desktop and phone through OS Bluetooth settings as normal
setup. Chrome chooser selection plus Veles application pairing is the intended
flow. The synthetic harness does not yet implement production OPAQUE pairing.

If Windows cannot connect and a bond already exists, forget the phone/desktop
relationship on both systems and repeat from an unpaired baseline. Record a
stale-bond run separately; do not treat the bond as a prerequisite or trust
signal.

## Load the extension

1. Open `chrome://extensions` in stable Chrome.
2. Enable Developer mode.
3. Select Load unpacked.
4. Select `spikes/web-bluetooth-popup/` from this checkout.
5. Pin **Veles Web Bluetooth Spike**.
6. Select the extension action.
7. Confirm Chrome opens a connector tab and the page reports that its protocol
   self-check passed.

Selecting the action again should focus the existing connector tab rather than
open a duplicate. Selecting **Connect phone** invokes Chrome's chooser. A new
connector-tab lifetime requires explicit chooser selection again.

After updating the branch, use Reload on `chrome://extensions` and open a fresh
connector tab.

## Automated checks

```bash
node --test \
  spikes/web-bluetooth-popup/protocol.test.mjs \
  spikes/web-bluetooth-popup/launcher.test.mjs
node --check spikes/web-bluetooth-popup/protocol.mjs
node --check spikes/web-bluetooth-popup/popup.mjs
node --check spikes/web-bluetooth-popup/launcher.mjs
node --check spikes/web-bluetooth-popup/service-worker.mjs
```

Android construction checks:

```bash
./gradlew testDebugUnitTest
./gradlew assembleDebug
./gradlew assembleRelease
```

These commands do not count as physical validation.

## Remaining physical gates

Do not mark Issue #77 complete until a revised harness and exact physical report
cover all of the following.

### Single-session baseline

- Windows and macOS each show the chooser from the connector tab without an OS
  pairing prerequisite.
- Synthetic authentication, pull, push, and manual copy work over plain GATT.
- Closing the connector tab ends or expires the session.
- A new tab requires explicit chooser selection and can pull again.

### Background connector

- Connect while the connector tab is active.
- Enable background automatic copy.
- Switch to a normal browsing tab for long enough to trigger Chrome's intensive
  timer throttling.
- Trigger synthetic pushes while the connector is backgrounded.
- Confirm the protected-session placeholder remains live, events are retained,
  the badge updates, and copy occurs through the offscreen clipboard helper.
- Select the extension action and confirm the existing connector tab is
  focused with the received events intact.

This case cannot pass with the current short heartbeat and direct clipboard
implementation. Revise the harness before running it.

### Multiple peers

- One phone concurrently serves Windows and macOS. Independent pulls succeed
  and one push reaches both clients. Run twice.
- One Windows connector tab serves two phones. Source-specific pull and push
  work without cross-client failure. Run twice.
- One macOS connector tab serves two phones. Run twice.

### Android foreground lifecycle

1. Start the service and schedule the 20-minute push.
2. Background the app, remove its task, and lock the phone for at least 15
   minutes.
3. Confirm the foreground indication remains.
4. Use a connector tab to select the unpaired phone through Chrome's chooser.
5. Confirm synthetic authentication and pull.
6. Remain connected until the scheduled push arrives.

### Evidence required

For every run record:

- Exact PR commit SHA.
- Phone model and Android build.
- Desktop model and OS build.
- Bluetooth adapter.
- Stable Chrome version.
- Exact steps and prompts.
- Result, timing, logs, and limitations.
- Individual repetitions rather than an aggregate summary.

## Decision rule

The spike reaches **go** only when every required Windows and macOS case passes.
A harness defect is fixed and rerun. Missing hardware or incomplete evidence is
inconclusive. A repeatable failure of background delivery, automatic copy,
multi-peer isolation, explicit reselection, or Android foreground lifecycle
returns the transport design for review.

No outcome from this synthetic harness permits real OTP transport. Real OTPs
remain blocked until the application security design, protected records,
physical security matrix, and release-candidate security review are complete.
