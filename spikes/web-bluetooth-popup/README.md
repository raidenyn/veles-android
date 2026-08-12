# Veles Web Bluetooth Spike — cross-machine tester guide

> **Synthetic-only warning.** This spike uses a public synthetic test key and
> synthetic six-digit OTP values. No real bank OTP, notification content, or
> production key material is ever sent, stored, or displayed by either side of
> the harness. The implementation environment that produced this PR has no
> access to the required physical hardware matrix, so **every physical result
> in the report begins as `Not run`.** Only the tester running the matrix on
> physical hardware can populate the results.

This guide is the handoff for the user performing physical validation on their
own Windows and macOS machines and Android phones. It assumes no
implementation-environment context. Follow it in order, copy the exact commit
SHA you test into `docs/spikes/2026-08-11-popup-web-bluetooth-validation.md`
before every run, and never reuse results after a harness change unless the PR
explicitly says the case is unaffected.

## 1. Check out the pull-request branch

The spike lives on branch `feat/77-popup-web-bluetooth-spike` of
`raidenyn/veles-android` on GitHub.

### GitHub CLI

```bash
git switch master
git pull --ff-only
gh pr checkout "$(gh pr list --repo raidenyn/veles-android --head feat/77-popup-web-bluetooth-spike --json number --jq '.[0].number')"
git rev-parse HEAD
```

### Normal Git

```bash
git fetch origin feat/77-popup-web-bluetooth-spike
git switch --create feat/77-popup-web-bluetooth-spike --track origin/feat/77-popup-web-bluetooth-spike
git rev-parse HEAD
```

### Later PR updates

When the PR is updated, pull the new commit and record the new SHA. Do not keep
testing on an older SHA after a harness change unless the PR explicitly marks a
case as unaffected.

```bash
git switch feat/77-popup-web-bluetooth-spike
git pull --ff-only
git rev-parse HEAD
```

Copy the SHA reported by `git rev-parse HEAD` into the report
(`docs/spikes/2026-08-11-popup-web-bluetooth-validation.md`) before every run,
and never reuse results after a harness change unless the PR explicitly says
the case is unaffected.

## 2. Build, install, launch, and reset the Android app

### Prerequisites

- JDK 17 on PATH (`java -version` shows `17.x`).
- Android SDK with `platform-tools` on PATH, or Android Studio's SDK manager.
  `adb` must be callable from a terminal.
- A USB- or Wi-Fi-debugged Android phone (Android 13 / SDK 33 or higher) with
  developer options enabled.
- Bluetooth enabled on the phone.

### Build and install the debug APK

```bash
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

The debug APK installs under application ID `me.nagaev.veles.debug`. The debug
build exposes a **second** launcher icon named **Veles BLE Spike** alongside the
normal Veles launcher. Open that launcher to reach the spike activity; it does
not modify the production Home screen or Compose navigation.

### Launch and drive the spike activity

1. Open the app launcher labeled **Veles BLE Spike**.
2. When prompted, grant the **Nearby Devices** runtime permissions
   (`BLUETOOTH_ADVERTISE`, `BLUETOOTH_CONNECT`, `POST_NOTIFICATIONS`).
3. Enable Bluetooth on the phone if it is off.
4. Tap **Start service**. Confirm the foreground service notification appears
   (it reports that the BLE spike is active and re-opens the activity).
5. Confirm the activity shows **Advertising** state and an empty connected
   client list.
6. Use the service controls as needed for each case:
   - **Start service** — begin advertising and the GATT server.
   - **Stop service** — close the GATT server, cancel advertising, disconnect
     clients, clear all session state, and cancel scheduled synthetic events.
   - **Push now** — generate a uniquely numbered synthetic push and fan it out
     to every subscribed authenticated client.
   - **Schedule in 10 seconds** — schedule a synthetic push 10 s out (smoke).
   - **Schedule in 20 minutes** — schedule a synthetic push 20 minutes out
     (used only by the foreground-lifecycle case).
7. Tap **Stop service** between cases that require a clean service state.

The foreground service type is `connectedDevice`. The ongoing notification is
expected to remain while the service is running, including across backgrounding
and screen lock in the lifecycle case.

### Reset the app to a clean state

```bash
adb shell am force-stop me.nagaev.veles.debug
adb shell pm clear me.nagaev.veles.debug
```

`force-stop`/`pm clear` is a **reset** operation. It is **not** part of the
foreground lifecycle pass; the lifecycle case requires the service to keep
running through backgrounding and screen lock. Use these commands only to
return to a clean baseline before a fresh-pairing case or after a case ends.

### Remove a desktop from Android Bluetooth settings

Before a fresh-pairing case:

1. Open **Settings → Connected devices (or Bluetooth)** on the phone.
2. Find the previously paired desktop (Windows or macOS host name).
3. Open its entry and select **Forget** / **Unpair**.
4. Repeat for every desktop that was previously paired to that phone.

The phone must have no remembered Bluetooth pairing for the desktop under test
before a fresh-pairing case begins.

## 3. Install the unpacked Chrome extension and handle clean state

These steps apply identically to stable Chrome on Windows and macOS. Use stable
Chrome, not Beta, Dev, or Canary.

1. Open `chrome://extensions`.
2. Enable **Developer mode** (top-right toggle).
3. Select **Load unpacked**.
4. Select the checked-out `spikes/web-bluetooth-popup/` directory.
5. Pin **Veles Web Bluetooth Spike** from the extensions toolbar menu so the
   action icon is always visible.
6. Select the pinned action. Chrome opens a connector tab; selecting the action
   again focuses the same tab. Confirm the status line reads **Protocol
   self-check passed** before selecting **Connect phone**. If the self-check
   fails, reload the extension and re-check; if it still fails, report an
   environment failure.

   Keep the connector tab visible and focused for the duration of a case. A
   backgrounded tab is subject to Chrome's timer throttling (`setInterval` can
   drop to about once a minute after roughly 5 minutes hidden), which can
   silently break the 5-second heartbeat against the server's 15-second
   timeout, and `navigator.clipboard.writeText()` requires the document to be
   focused, so an async copy from a backgrounded tab can reject with a
   focus-related error. If a heartbeat disconnect or a clipboard failure
   happens while the connector tab was backgrounded, treat it as a
   harness/platform artifact, not a real result, and rerun the case with the
   tab focused.

### After pulling a newer PR commit

1. `git pull --ff-only` on the branch (see §1).
2. Open `chrome://extensions`.
3. Select the **Reload** button on the Veles Web Bluetooth Spike card.
4. Select the pinned action to open a fresh connector tab and confirm
   **Protocol self-check passed** again.

### For a fresh extension state

When a case requires a fresh extension state:

1. In `chrome://extensions`, remove the Veles Web Bluetooth Spike extension.
2. Remove the OS-level Bluetooth pairing for the relevant desktop from the
   phone (see §2) and from the desktop's Bluetooth settings.
3. Clear any Chrome Bluetooth grant for the extension by removing and
   reloading the extension.
4. Re-load the same unpacked `spikes/web-bluetooth-popup/` directory.
5. Record the prompts that appear (permission wording, pairing flow, etc.) in
   the report for the case that requires it.

### Connector-tab DevTools (troubleshooting only)

To inspect connector errors, open DevTools for the connector tab (right-click
inside the page and select **Inspect**, or use the platform DevTools shortcut).
Read the console and the on-page event log. Close DevTools before timing a
physical case so debugging overhead does not affect observations. Closing
DevTools does not close the connector; close the connector tab itself when a
case requires a fresh tab lifetime.

### Running the automated checks

The JS unit test suite (protocol framing/HMAC logic and the launcher's
tab-focus-or-create behavior) can be run with Node directly, no Chrome
required:

```bash
node --test spikes/web-bluetooth-popup/protocol.test.mjs spikes/web-bluetooth-popup/launcher.test.mjs
```

## 4. Eight physical validation cases

For every case, record: exact steps, expected result, actual result,
pass/fail, relevant timing, and limitations, in
`docs/spikes/2026-08-11-popup-web-bluetooth-validation.md`.

Fresh pairing runs **once per OS** (cases 1 and 2). Connector-tab closure
(cases 3 and 4), the one-phone-two-computers topology (case 5), and both
two-phone topologies (cases 6 and 7) each **run twice** from a clean
connector-tab lifetime to expose instability. All other cases run once.

### Case 1 — Windows fresh chooser / pair / auth / pull / push / async copy

**Setup.**

- Windows computer, stable Chrome. Remove any OS-level Bluetooth pairing for
  the Windows host from the phone (see §2).
- Reset the app state and reload the extension as a fresh extension state
  (see §3). Stop and restart the spike service.

**Steps.**

1. On the phone, open the **Veles BLE Spike** activity, grant Nearby Devices
   and POST_NOTIFICATIONS, enable Bluetooth, and tap **Start service**.
2. Confirm the activity shows **Advertising** and an empty client list.
3. On Windows, select the pinned action to open the Veles Web Bluetooth Spike
   connector tab and confirm **Protocol self-check passed**.
4. Select **Connect phone** in the connector tab. The Web Bluetooth device
   chooser appears. Select the phone and confirm.
5. Complete the OS-level Bluetooth pairing sequence when prompted (PIN
   confirmation, system pairing dialog, etc.). Record the exact prompts and
   order.
6. Wait for the connector tab to report per-phone connection, subscription,
   and **authenticated** status.
7. Select **Pull current**. Confirm the connector tab displays a synthetic
   current OTP envelope with event number, six-digit code, merchant label,
   amount, and currency.
8. On the phone, tap **Push now**. Confirm the connector tab receives a new
   synthetic push event with a fresh unique event number and code.
9. Clipboard: place known text (e.g. `CLIPBOARD-MARKER`) on the Windows
   clipboard. In the connector tab, select **Copy next push**. On the phone,
   tap **Push now**. Paste the clipboard into a local text editor and confirm
   the synthetic six-digit code replaced the marker.
10. Record exact timing, prompt wording, and any limitations.

**Expected.** Fresh chooser appears, required pairing completes, the
connector tab stays usable through the pairing interactions, pull returns the
synthetic envelope, the push reaches the connector tab, and the asynchronous
clipboard write replaces the known marker text with the synthetic code.

**Reset.** Close the connector tab. On the phone, tap **Stop service**, then
reset the app state as needed. **Retain the OS Bluetooth pairing** — Cases 3
and 4 and Case 5 reuse it. Remove the Windows host from the phone's Bluetooth
pairings only before intentionally re-running a fresh-pairing case or after
all dependent cases (3, 4, and 5) are complete.

### Case 2 — macOS fresh chooser / pair / auth / pull / push / async copy

**Setup.**

- macOS computer, stable Chrome. Remove any OS-level Bluetooth pairing for
  the Mac from the phone.
- Fresh app and extension state on the phone and Mac as in Case 1.

**Steps.** Identical to Case 1, substituting macOS for Windows and the Mac for
the Windows host. Record the exact macOS pairing prompts, dialog wording, and
order.

**Expected.** Same as Case 1 on macOS.

**Reset.** As in Case 1: close the connector tab, stop the service, and reset
the app state as needed. **Retain the OS Bluetooth pairing** — Cases 3 and 4
and Case 5 reuse it. Remove the Mac from the phone's Bluetooth pairings only
before intentionally re-running a fresh-pairing case or after all dependent
cases (3, 4, and 5) are complete.

### Case 3 — Windows connector-tab closure (run twice)

**Setup.**

- Use the already-paired Windows host and phone from Case 1 (do not re-pair
  fresh for this case). Uses the pairing retained from Case 1. Do not re-pair.
- Reset only the connector-tab lifetime: close the connector tab, then select
  the action to open a fresh one.

**Steps.**

1. Start the service on the phone if it is not already running.
2. Select the pinned action to open the connector tab and connect/authenticate
   to the phone (explicit selection through **Connect phone** / the device
   chooser, as in Case 1). Pull current data and confirm the authenticated
   session works.
3. **Close the connector tab** (close the browser tab itself; do not use
   connector-tab DevTools during this observation — see §3).
4. Observe the phone activity. Record Android disconnect callback timing or
   the bounded heartbeat timeout after which the session expires. Confirm the
   client disappears from the connected client list.
5. Wait at least 15 seconds past expiry to ensure the session is fully gone.
6. **Select the action to create a fresh connector tab.** Confirm there is
   **no live session** — the phone list is empty and there is no remembered
   connection.
7. **Explicitly reselect the phone** through **Connect phone** and the
   chooser. Confirm the connector tab re-authenticates and reconnects.
8. Select **Pull current** and confirm the pull succeeds.

**Run twice.** Repeat the entire closure → fresh tab → reselection → pull
sequence from a clean connector-tab lifetime a second time. Record both runs.

**Expected.** Closing the connector tab ends the session; Android records
disconnect or expires the session after the bounded timeout; the fresh tab
shows no live session; explicit reselection through the chooser reconnects
and pull succeeds. Both runs pass.

**Reset.** Close the connector tab. Stop the service on the phone.

### Case 4 — macOS connector-tab closure (run twice)

**Setup.** Already-paired Mac and phone from Case 2. Uses the pairing retained
from Case 2. Do not re-pair. Reset only the connector-tab lifetime.

**Steps.** Identical to Case 3, substituting macOS for Windows.

**Run twice.** As in Case 3.

**Expected.** As in Case 3, on macOS.

**Reset.** As in Case 3.

### Case 5 — One phone serving two computers concurrently (run twice)

**Setup.**

- One Android phone, one Windows computer, one macOS computer, both already
  paired to the phone from Cases 1 and 2.
- Start the service on the phone once. Confirm Advertising and an empty
  client list before connecting either connector tab.

**Steps.**

1. On Windows, open the connector tab, connect/authenticate to the phone.
2. On macOS, open a separate connector tab, connect/authenticate to the
   **same** phone.
3. From the Windows connector tab, **Pull current** and confirm a result.
4. From the macOS connector tab, **Pull current** and confirm an independent
   result.
5. On the phone, tap **Push now**.
6. Confirm the push reaches **both** the Windows and macOS connector tabs as
   independent events with the same unique event number, and that a slow or
   failed client on one side does not block the other.

**Expected.** Both connector tabs authenticate to the same phone concurrently;
pulls are independent; one push reaches both clients without cross-client
blocking.

**Run twice.** Repeat the entire two-computer connect/pull/push sequence from a
clean connector-tab lifetime a second time. Record both runs.

**Reset.** Close both connector tabs (one on each computer). Stop the service
on the phone.

### Case 6 — Two phones, one Windows connector tab (run twice)

**Setup.**

- Two Android phones (phone A and phone B). Pair each to the Windows host (or
  use the already-paired phone A from Case 1 and freshly pair phone B).
- Start the service on both phones.

**Steps.**

1. In the Windows connector tab, select **Connect phone** and
   connect/authenticate to phone A.
2. Select **Connect phone** again and connect/authenticate to phone B without
   dropping the first connection. Both sessions are retained in the same
   connector tab.
3. From the connector tab, select phone A's **Pull current** and confirm a
   phone-A-source result.
4. From the connector tab, select phone B's **Pull current** and confirm a
   phone-B-source result.
5. On phone A, tap **Push now**. Confirm the connector tab receives a
   phone-A-source push.
6. On phone B, tap **Push now**. Confirm the connector tab receives a
   phone-B-source push while phone A's connection remains active.

**Run twice.** Repeat the entire two-phone connect/pull/push sequence from a
clean connector-tab lifetime a second time. Record both runs.

**Expected.** One connector tab maintains two concurrent authenticated
sessions; pulls and pushes are source-specific; neither phone's push closes
the other phone's connection. Both runs pass.

**Reset.** Close the connector tab. Stop the service on both phones.

### Case 7 — Two phones, one macOS connector tab (run twice)

**Setup.**

- Two Android phones, each paired to the Mac (use the already-paired phone
  from Case 2 plus a second phone).
- Start the service on both phones.

**Steps.** Identical to Case 6, substituting macOS for Windows.

**Run twice.** As in Case 6.

**Expected.** As in Case 6, on macOS.

**Reset.** As in Case 6.

### Case 8 — Android foreground lifecycle (schedule 20 m, reconnect after 15 m)

**Setup.**

- One desktop (Windows or macOS) already paired to the phone.
- Reset the app state, then start fresh.

**Steps.**

1. On the phone, open the **Veles BLE Spike** activity, grant permissions,
   enable Bluetooth, and tap **Start service**.
2. Confirm **Advertising** and empty client list.
3. Tap **Schedule in 20 minutes**. Confirm the activity shows the scheduled
   push.
4. Background the app and **remove its task** from the recents/task list.
5. **Lock the phone with the screen off** for at least 15 minutes.
6. After 15 minutes, confirm the foreground service notification (the BLE
   spike active indication) is still present.
7. From the already-paired desktop, open the connector tab, select
   **Connect phone**, and complete authentication through the existing
   pairing.
8. **Pull current** and confirm the pull succeeds.
9. Remain connected and wait until the scheduled 20-minute push arrives
   (verify it lands in the connector tab).
10. Record timing for: service still alive at 15 m, reconnect succeeds, pull
    succeeds, scheduled push arrives.

**Expected.** The foreground service survives 15 m of backgrounding, task
removal, and screen lock; the foreground indication remains; a new connector
tab from an already-paired desktop reconnects and authenticates; pull
succeeds; the scheduled push arrives on time.

**Reset.** Close the connector tab. Stop the service on the phone. Run
`adb shell am force-stop me.nagaev.veles.debug` and
`adb shell pm clear me.nagaev.veles.debug` only **after** the case completes.

### Clipboard verification (cases that exercise async copy)

For every case that exercises asynchronous copy:

1. Place known text on the desktop clipboard (e.g. `CLIPBOARD-MARKER`).
2. In the connector tab, select **Copy next push**.
3. On the phone, trigger the relevant push (e.g. **Push now**).
4. Paste the clipboard into a local text editor (Notepad, TextEdit, etc.).
5. Confirm the synthetic six-digit code replaced the marker text, not the
   marker text itself.

Record the pasted value in the report.

## 5. Iteration and reporting

After running the matrix, fill in
`docs/spikes/2026-08-11-popup-web-bluetooth-validation.md`:

- The exact commit SHA you tested (`git rev-parse HEAD`).
- Android phone model, Android version/build, and app debug version for each
  phone.
- Windows computer model, Windows version/build, Bluetooth adapter details,
  and stable Chrome version.
- Mac model, macOS version/build, Bluetooth adapter details, and stable
  Chrome version.
- Any OS-level pairing prerequisites, prompts, permissions, or settings.
- The eight cases' actual results, pass/fail, timing, and limitations.
- Repetition results for closure (cases 3, 4), one-phone-two-computers
  (case 5), and multi-device (cases 6, 7).
- Observed limitations and environment failures.
- The go/no-go decision per the rules in the report file.

If the harness changes during testing, the PR records a new commit. Pull that
commit, record the new SHA, and re-run every affected case. Do not carry over
results from the old SHA unless the PR explicitly says a case is unaffected.

The PR remains draft until the report has enough physical evidence for a
go/no-go decision. Automated CI passing is not, by itself, a reason to merge.