# OTP-12 Gate B runbook

Physical run procedure for the Gate B evidence matrix (`docs/gate-b/matrix.json`). Every row in
that matrix is `NOT RUN`: this document describes how a run is performed and recorded, it does
not contain a run. The matrix stays `decision: OPEN` and `gate_b_accepted: false` until a
separate adjudication records a verdict, which is outside this tooling.

## Prerequisites and conventions

- **OTP-24 physical acceptance is not complete.** No Gate B row can be filled in until the
  prerequisite physical acceptance has a recorded verdict; until then a row that could not run
  stays `NOT RUN` with its blocker.
- **The extension artifact that observes the run is recorded as an identity.** A physical row
  records the observing artifact — channel, `version_name`, extension id, `dist/manifest.json`
  hash and the extension directory hash — never just "the extension". A development-channel
  build is not a production artifact, and the row says so.
- **The non-evidentiary smoke run is never Gate B evidence.** A smoke run (seed, pull, a short
  scheduled push, the development-channel `pullAll()` path) exercises the fixture and the
  connector; its result is reported as a smoke run and can never fill a matrix row.
- **Minute 0 is the controller-clock time** at which the phone's `scheduled` line for a push
  scheduled with a 1200-second delay is read from `adb logcat`, and it records the offset method:
  the line's phone `elapsed=` value, the controller clock reading taken when the line appears,
  and the transport latency estimate. Every later timestamp in the row is a controller-clock
  reading, and the timing rule is evaluated on the offset-corrected values, never by sleeping.
- **Reseed before the post-minute-15 pull.** The five records seeded at minute 0 carry at most
  600-second lifetimes, so they are expired long before minute 15. Reseed immediately before the
  post-minute-15 pull, after the screen is unlocked; the re-pull then shows the reseeded
  unexpired records present and the earlier records absent.
- **A push after five pulled records evicts one on the client.** A client keeps at most five
  events, so after five records have been pulled a further push makes the client drop one
  record. Record the eviction so a smaller re-pull is not misread as a fixture or transport
  failure.
- **Never record secrets.** Pairing codes, installation ids, public keys, platform hints and
  transcripts are read on screen and not written to a row; rows carry bounded prefixes and
  hashes only.

## Common commands

`<applicationId>` is `me.nagaev.veles.debug` for a plain debug build (`me.nagaev.veles.otp23`
for the OTP-23 fixture build). The sharing service is not exported: enable and stop it from the
Home screen's "Device sharing transport" card (`Enable transport` / `Stop`) or the foreground
notification's `Stop` action, never with `am startservice`.

```bash
APP=me.nagaev.veles.debug
R=$APP/me.nagaev.veles.debugprobe.ProbeReceiver

# Ordered-outcome lines: seeded, scheduled, fired, skipped, cancelled, expired-omitted.
adb logcat -v time -s VelesProbe > controller.log
adb shell pidof "$APP"

# Seed exactly five records; every lifetime is 1..600 seconds.
adb shell am broadcast -a me.nagaev.veles.debug.GATE_B_SEED -n "$R" --es lifetimes 60,120,300,600,600

# Schedule the push; the timing rule accepts only a 1200-second delay.
adb shell am broadcast -a me.nagaev.veles.debug.GATE_B_SCHEDULE_PUSH -n "$R" --es delay 1200

# Cancel a pending push, or disarm the fixture (which returns it to probe mode).
adb shell am broadcast -a me.nagaev.veles.debug.GATE_B_CANCEL_PUSH -n "$R"
adb shell am broadcast -a me.nagaev.veles.debug.GATE_B_DISARM -n "$R"

# Probe push: sends nothing while the fixture is armed, one fresh event otherwise.
adb shell am broadcast -a me.nagaev.veles.debug.PUSH -n "$R"

# Phone identity and a local revoke. The identity value never goes into a row.
adb shell am broadcast -a me.nagaev.veles.debug.EXPORT_IDENTITY -n "$R"
adb shell am broadcast -a me.nagaev.veles.debug.REVOKE_PEER -n "$R" --es id <peer installation id>

# Debug pairing screen (trust state, pairing window, peer list).
adb shell am start -n "$APP"/me.nagaev.veles.debugpairing.PairingDebugScreen
```

Development-channel connector console (Chrome DevTools on the connector page, or the extension
service worker's console; absent from production bundles):

```js
await velesDebug.exportIdentity();
await velesDebug.pull();        // exactly one event
await velesDebug.pullAll();     // 0..5 events, acks each
await velesDebug.connect(deviceHandle, peerId);
await chrome.tabs.get(tabId);        // frozen, discarded, active, audible
await chrome.action.getBadgeText({ tabId });
chrome.runtime.getContexts({});      // offscreen document / connector document alive?
```

Host process checks: Windows `tasklist | findstr veles-native-host`; macOS
`pgrep -fl veles-native-host`. Host package: `native-bridge/host`; extension:
`web-extension/dist` loaded unpacked in a dedicated Chrome profile.

## host-missing-recovery

**Rows.** `windows.none.host-missing-recovery`, `macos.none.host-missing-recovery`.

**Steps.** 1. With the phone sharing transport enabled and the extension loaded, remove the
Native Messaging host registration (`me.nagaev.veles.bridge`) so the extension has no host.
2. Restart Chrome and open the connector page. 3. Confirm the connector reports the missing-host
state and starts no search, no port and no session. 4. Re-register the host, restart Chrome and
confirm search becomes available again without re-pairing.

**Commands.** Remove and restore the manifest: Windows
`HKCU\Software\Google\Chrome\NativeMessagingHosts\me.nagaev.veles.bridge`, macOS the profile's
`NativeMessagingHosts/me.nagaev.veles.bridge.json`. Then `tasklist | findstr veles-native-host`
or `pgrep -fl veles-native-host` must be empty while missing, and the connector page status text
is the observation.

**Record.** The exact connector text, whether a host process or port existed, the recovery path
(re-registration, Chrome restart) with controller timestamps, and the extension identity. The
matrix row's `adapters`, `host_process_queue`, `expected`, `actual` and `return_codes` fields
carry this.

## host-incompatible-recovery

**Rows.** `windows.none.host-incompatible-recovery`, `macos.none.host-incompatible-recovery`.

**Steps.** 1. Register a host build whose protocol API the extension does not support (an older
`native-bridge/host` build, or a manifest pointing at a different executable). 2. Open the
connector and confirm the refusal is explicit and no session opens. 3. Replace it with the
matching host, restart Chrome, and confirm recovery.

**Commands.** Swap the manifest `path`, keep the same host name; use `tasklist | findstr
veles-native-host` / `pgrep -fl veles-native-host` and the connector status line
(`Bridge veles-native-host/<version> · <OS> · API <n>`). Capture the host binary hash before
and after the swap.

**Record.** Both host versions and API numbers, the refusal text, the swap timestamps, the
recovery result, and the host binary hashes in `hashes` and `identities`.

## extension-search

**Rows.** `windows.A.extension-search`, `macos.A.extension-search`.

**Steps.** 1. Enable the phone sharing transport and open the connector page. 2. Start a search
and confirm the extension owns it: the phone appears in the device list without a pairing
window and without a trust record. 3. Stop the search and confirm the list clears. 4. Confirm
the discovered platform hint is opaque and used only for reachability, never for trust.

**Commands.** The connector's search control, plus `adb shell dumpsys bluetooth_manager` for
the advertisement, and `velesDebug.exportIdentity()` on the connector page. The phone side is
`adb logcat -v time -s VelesProbe`.

**Record.** Device count, search start/stop timestamps, the adapter used, the hint value as an
opaque string, the absence of a trust record, and the explicit statement that no bond, handle or
hint was relied on for trust.

## spake2-pairing

**Rows.** `windows.A.spake2-pairing`, `macos.A.spake2-pairing`.

**Steps.** 1. Start a pairing window on the phone debug screen and enter its code in the Chrome
panel. 2. Confirm both endpoints reach `active` with the same fingerprint and the same
transcript prefix. 3. Confirm the phone record and the client mirror agree, and that the
session was `enrollment` before activation and `full` afterwards.

**Commands.** `adb shell am start -n "$APP"/me.nagaev.veles.debugpairing.PairingDebugScreen`;
`adb logcat -v time -s VelesProbe`; the connector page's pairing debug panel.

**Record.** Fingerprints and transcript prefixes (never the code), both record states, the
enrollment and full session classes, the return codes of each exchange, raw captures of both
surfaces, and the extension identity. The row cites the `enrollment` journey.

## hint-reconnect-fresh-keys

**Rows.** `windows.A.hint-reconnect-fresh-keys`, `macos.A.hint-reconnect-fresh-keys`.

**Steps.** 1. From the paired state, disconnect and reconnect from the device row (a
user-selected reconnect, no pairing code). 2. Confirm the reconnect is `full`, the keys are
fresh (`key_fresh`), and the stored platform hint was used only to find the device. 3. Repeat
after a phone restart so the hint is stale and the reconnect still succeeds through a fresh
search.

**Commands.** The device row's Connect control; `velesDebug.exportIdentity()`; `adb logcat -v
time -s VelesProbe`; `adb shell pidof "$APP"` to record the same phone process across the
reconnect.

**Record.** The hint value (opaque), the fresh-key observation, the session class, the
before/after phone process ids, and the explicit no-bond/no-handle/no-hint trust statement.

## protected-pull

**Rows.** `windows.A.protected-pull`, `macos.A.protected-pull`.

**Steps.** 1. Seed five records with staggered lifetimes. 2. Open one full session from the
device row. 3. Pull and confirm five events decode and each is acknowledged, with the same ids
in the next pull. 4. Confirm a malformed or oversized record ends the session instead of being
partially accepted.

**Commands.** The seed broadcast above, then `await velesDebug.pullAll()` on the connector page;
`adb logcat -v time -s VelesProbe`; the extension readout shows the decoded values.

**Record.** Event count, id prefixes, ack return codes, session prefix, the decoded fields, the
malformed-record rejection, and the raw capture plus checksum catalog entry.

## push

**Rows.** `windows.A.push`, `macos.A.push`.

**Steps.** 1. With a full session open and the fixture armed, schedule a push. 2. Confirm the
fixture logs `scheduled` and, at fire time, `fired` with the destination session prefix. 3.
Confirm the client decodes and acknowledges the pushed event. 4. Note the eviction rule: a push
after five pulled records evicts one on the client.

**Commands.** `adb shell am broadcast -a me.nagaev.veles.debug.GATE_B_SCHEDULE_PUSH -n "$R" --es
delay 30` for the smoke form (never evidence); the evidentiary form is the 1200-second push in
the locked-phone section. `await velesDebug.pullAll()`; `adb logcat -v time -s VelesProbe`.

**Record.** The `scheduled` and `fired` lines with elapsed values, the destination prefixes, the
event id, the controller-clock arrival, the ack codes, and the eviction observation.

## background-delivery

**Rows.** `windows.A.background-delivery`, `macos.A.background-delivery`.

**Steps.** 1. Open a full session in the connector and switch to another ordinary tab. 2. Hold
that focus for at least ten continuous minutes with no audio playing, no WebRTC capture and no
timer-throttling flag or exemption. 3. Confirm a protected event and its offscreen copy both
occur strictly after minute seven of the interval. 4. Sample `chrome.tabs` and the OS clipboard during
and after the interval.

**Commands.** `await chrome.tabs.query({})` for the focused tab id/title and each tab's
`audible`, `frozen` and `discarded`; `chrome.runtime.getContexts({})` for the offscreen
document; Windows `Get-Clipboard`, macOS `pbpaste`; `adb logcat -v time -s VelesProbe`. The
interval start and end are controller-clock readings.

**Record.** Start and end times, focused tab identity, exemption status (audio, WebRTC,
throttling), the event and copy times, the clipboard content, the `background_ok` inputs and its
result, and the raw captures with the checksum catalog.

## offscreen-copy

**Rows.** `windows.A.offscreen-copy`, `macos.A.offscreen-copy`.

**Steps.** 1. Open the connector's Diagnostics disclosure and press `Arm one background copy`
(or `Copy synthetic code`). 2. Confirm the OS clipboard transitions from its previous content
to the fixed synthetic code, correlated with the copy request. 3. Confirm the offscreen helper is
gone afterwards and no native port belongs to it. 4. Optionally remove `clipboardWrite` from a
throwaway copy of the extension to confirm the visible failure and retry path.

**Commands.** `chrome.runtime.getContexts({contextTypes:['OFFSCREEN_DOCUMENT']})` before and
after; Windows `Get-Clipboard`, macOS `pbpaste`; the connector's diagnostic status line; the
extension service worker console.

**Record.** Clipboard before and after, the correlation id, the helper presence and teardown,
the failure and retry results if run, and the correlated transition as the `clipboard_transition`
evidence field.

## forced-freeze

**Rows.** `windows.A.forced-freeze`, `macos.A.forced-freeze`.

**Steps.** 1. With a connected full session, record the connector document id, the port
generation and the per-tab badge. 2. Freeze the connector tab (`chrome://discards` → `Freeze`,
or CDP `Page.setWebLifecycleState` with `frozen`). 3. Confirm the `chrome.tabs` frozen
observation, the immediate badge and status clear, and the old port generation's invalidation
before any queued event is sent. 4. Confirm the old port is closed and no host process remains.
5. Unfreeze, open a fresh connector document, and re-pull: unexpired records are present,
expired records absent.

**Commands.** `chrome://discards`; `await chrome.tabs.get(tabId)`; `await
chrome.action.getBadgeText({tabId})`; `chrome.runtime.getContexts({})`; `tasklist | findstr
veles-native-host` / `pgrep -fl veles-native-host`; `await velesDebug.pullAll()` after the
reconnect; `adb logcat -v time -s VelesProbe`.

**Record.** The forced trace, `chrome.tabs` observations, badge and status before/after, the
port generation number and its invalidation, old-port closure and the absent host process, the
re-pull counts for retained and expired records, and the `repull_retained_expired` evidence
field.

## forced-discard

**Rows.** `windows.A.forced-discard`, `macos.A.forced-discard`.

**Steps.** 1. With a connected full session, record the document id, port generation and badge.
2. Discard the connector tab (`chrome://discards` → `Discard`; `Page.setWebLifecycleState` with
`frozen` then Chrome's discard). 3. Confirm the `chrome.tabs` discarded observation, the badge
and status clear, the old generation invalidation before queued events, and old-port closure with
no host process. 4. Reopen the connector, reconnect, and re-pull: unexpired records present,
expired absent.

**Commands.** The same commands as the freeze section, with `chrome.tabs.get(tabId).discarded`
as the discriminating observation and a reload of the connector URL to obtain the new document.

**Record.** The forced trace, discarded observation, badge/status clear, port generation
invalidation, old-port closure, absent host process, the re-pull retained/expired counts, and
the new document id.

## connector-closure

**Rows.** `windows.A.connector-closure`, `macos.A.connector-closure`.

**Steps.** 1. With a connected full session, note the connector document id, port generation and
badge. 2. Close the connector tab. 3. Confirm the generation is invalidated, the badge is
cleared, the native port is closed and no host process remains. 4. Reopen the connector and
confirm no queued event is replayed.

**Commands.** `await chrome.tabs.remove(tabId)`; `chrome.runtime.getContexts({})`; `tasklist |
findstr veles-native-host` / `pgrep -fl veles-native-host`; the connector's Diagnostics history
after reopening.

**Record.** The tab id, the close timestamp, the invalidation order, badge clear, the absent host
process as the `no_host_process` field, and the no-replay observation.

## connector-restart

**Rows.** `windows.A.connector-restart`, `macos.A.connector-restart`.

**Steps.** 1. With a connected full session, close the connector tab. 2. Open it again and
reconnect from the device row. 3. Confirm a new connector document id, fresh handles, a `full`
session with fresh keys, and no replay of the previous generation's events.

**Commands.** `await chrome.tabs.get(tabId)` for the new `documentId`; the device row's Connect;
`adb logcat -v time -s VelesProbe`; `await velesDebug.exportIdentity()` to prove the identity is
durable across the restart.

**Record.** Old and new document ids, the fresh handle and key observations, the session class,
return codes, and the no-replay observation.

## host-closure

**Rows.** `windows.A.host-closure`, `macos.A.host-closure`.

**Steps.** 1. With a connected full session, terminate the native host process (`taskkill /IM
veles-native-host.exe /F`, macOS `pkill -f veles-native-host`). 2. Confirm the connector shows
the host gone, the port invalidated and the session ended. 3. Confirm no host process remains
and no offscreen helper survives.

**Commands.** The termination command for the platform; `tasklist | findstr veles-native-host` /
`pgrep -fl veles-native-host`; `chrome.runtime.getContexts({})`; the connector status line.

**Record.** The termination time, the connector's observed transition, the absent host process
as `no_host_process`, and the recovery step that was not performed (so the row cannot be
mistaken for a restart).

## host-restart

**Rows.** `windows.A.host-restart`, `macos.A.host-restart`.

**Steps.** 1. Terminate the host as in the closure section. 2. Reopen the connector and
reconnect from the device row so a new host process starts. 3. Confirm fresh handles, a `full`
session with fresh keys, and that no event from the previous host generation is replayed.

**Commands.** The termination command; `tasklist | findstr veles-native-host` / `pgrep -fl
veles-native-host` (new process id); `await velesDebug.pullAll()`; `adb logcat -v time -s
VelesProbe`.

**Record.** Old and new host process ids, fresh handles and keys, the session class, the
no-replay observation, and the connector's bridge version line.

## revocation

**Rows.** `windows.A.revocation`, `macos.A.revocation`.

**Steps.** 1. With a full session open, revoke the desktop peer from the phone debug screen or
with the `REVOKE_PEER` broadcast. 2. Confirm the session ends, the desktop keeps its offline
record, and a reconnect is rejected. 3. Confirm the phone no longer lists the peer, and repeat
with two peers to show the second peer is untouched.

**Commands.** `adb shell am broadcast -a me.nagaev.veles.debug.REVOKE_PEER -n "$R" --es id <peer
installation id>`; `adb logcat -v time -s VelesProbe`; the connector's device row.

**Record.** The revoke outcome line, the session end, the retained offline desktop record, the
rejected reconnect (`reconnect_expect_rejected`), and the untouched second peer. The row cites
the `protected-records` journey.

## bluetooth-interruption

**Rows.** `windows.A.bluetooth-interruption`, `macos.A.bluetooth-interruption`.

**Steps.** 1. With a full session open, turn the phone's Bluetooth off (`adb shell svc bluetooth
disable`, or Settings → Bluetooth off where the shell toggle is restricted). 2. Confirm the
session ends on both sides and the connector shows the disconnected state. 3. Turn Bluetooth
back on and reconnect from the device row. 4. Confirm the reconnect is `full` with fresh keys
and that the app recovered without a process restart.

**Commands.** `adb shell svc bluetooth disable` / `adb shell svc bluetooth enable` (fallback `adb
shell cmd connectivity airplane-mode enable` / `disable`); `adb shell dumpsys bluetooth_manager`;
`adb shell pidof "$APP"`; `adb logcat -v time -s VelesProbe`.

**Record.** Interruption and recovery timestamps, the phone process id before and after, the
session classes, the fresh-key observation, and whether a permission or radio re-grant was
needed.

## two-phone-lifecycle

**Rows.** `windows.AB.two-phone-lifecycle`, `macos.AB.two-phone-lifecycle`.

**Steps.** 1. Pair phone A and phone B with the same desktop, each through its own pairing
window. 2. Confirm both peers have independent records and independent full sessions. 3. Revoke
phone A and confirm phone B's record and session are untouched. 4. Interrupt phone B's radio and
confirm its recovery after re-enabling Bluetooth. 5. Restart the phone process and confirm the
desktop reconnects to the surviving peer.

**Commands.** Per phone: `adb shell am broadcast -a me.nagaev.veles.debug.EXPORT_IDENTITY -n
"$R"` and the `REVOKE_PEER` broadcast; `adb logcat -v time -s VelesProbe` on both phones; the
connector's peer list and device rows.

**Record.** Both phone identities as bounded prefixes, both record states, each session class,
the revocation result per peer, the radio recovery of phone B, the restart recovery, and the
explicit statement that neither trust nor session state was shared between the phones.

## locked-phone-20min

**Rows.** `android.A.locked-phone-20min`.

**Steps.** 1. Enable the sharing transport and pair the desktop; keep one full session open.
2. Seed the fixture. 3. Schedule a 1200-second push and read the fixture's `scheduled` line; the
controller clock reading of that line is minute 0, and the phone `elapsed=` value plus the
latency estimate is the offset method. 4. Lock the screen first and confirm the phone is asleep
(`mWakefulness=Asleep`). 5. With the screen still locked, remove the app task from Recents
without force-stopping it and record the task-removal controller time; the phone must already be
locked at task removal. 6. Keep the phone locked from task removal until at least 15 minutes
after it, sampling the foreground indication; that interval is the required waiting period and
no unlock may interrupt it. 7. Read the `fired` line and the connector's receipt: the arrival
must be within minute 19 to minute 21 inclusive. 8. Confirm the phone was locked at task removal
and was not unlocked before 15 minutes after it. 9. After minute 15, unlock, open the connector,
reseed, and pull; the pull must be strictly after minute 15. 10. Evaluate `twenty_minute_ok` on
the recorded integers.

**Commands.** Schedule the push before locking: `adb shell am broadcast -a
me.nagaev.veles.debug.GATE_B_SCHEDULE_PUSH -n "$R" --es delay 1200`. Lock the screen:
`adb shell input keyevent 223`, then confirm `adb shell dumpsys power | grep -i mWakefulness`
reports `Asleep`. With the screen still locked, remove the task: `adb shell am stack list` then
`adb shell am stack remove <stackId>` (or the Recents gesture), and confirm `mWakefulness` is
still `Asleep`. During the wait read `adb shell dumpsys activity services "$APP" | grep -i
foreground`, `adb shell pidof "$APP"` and `adb logcat -v time -s VelesProbe`. After minute 15,
unlock with `adb shell input keyevent 224` plus the unlock gesture and `await
velesDebug.pullAll()`.

**Record.** Minute 0 and the offset method, the lock time before task removal, the task-removal
time, arrival, unlock and pull times, the foreground samples, the phone process id before and
after task removal, the `scheduled` and `fired` lines, the reseeded re-pull counts, the
`foreground_present` flag, and the rule inputs and result. A missed window is recorded as `FAIL`
with its classification, never rounded.

## fixture-retention-expiry

**Rows.** `android.A.fixture-retention-expiry`.

**Steps.** 1. Seed five records with lifetimes 60, 120, 300, 600 and 600 seconds; pull and
confirm five records with the same ids. 2. Wait 150 seconds (controller clock, no sleeping in a
test) and pull again; exactly three records remain, with the same ids and remaining lifetimes
between 150 and 450 seconds. 3. Push after five pulled records to observe the client's eviction
of one record. 4. Stop and restart the app process; the fixture returns to probe mode, so a seed
or pull through the Gate B commands is refused until it is seeded again.

**Commands.** The seed broadcast with `--es lifetimes 60,120,300,600,600`; `await
velesDebug.pullAll()`; `adb shell am force-stop "$APP"` and the Home card's `Enable transport`
to restart; `adb logcat -v time -s VelesProbe` (`seeded`, `expired-omitted`, `refused` lines).

**Record.** Both pull counts, the ids and remaining lifetimes, the expired-omitted count, the
eviction observation, the post-restart refusal, the controller timestamps, and the raw captures
with the checksum catalog.

## Limits

- This runbook describes runs; it contains none. The committed matrix stays `OPEN` with
  `gate_b_accepted: false` and every row `NOT RUN`.
- Only a debug APK and a development-channel extension are described. A production artifact or
  a signed build changes the recorded identities and must be recorded as such.
- A validation pass by `docs/checks/check_gate_b_matrix.py` is necessary and not sufficient; the
  provenance anchor and the separate adjudication are recorded outside this repository tooling,
  and the decision is never written by the validator.
