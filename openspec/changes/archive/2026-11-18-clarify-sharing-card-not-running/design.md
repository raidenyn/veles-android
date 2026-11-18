## Context

`SharingCard` (`android/app/src/main/java/me/nagaev/veles/sharing/SharingCard.kt`) collects two flows from `SharingState`: `enabled` (the persisted `sharing` preference, loaded when the card enters composition) and `availability` (in memory, `Disabled` in a fresh process). It shows the title; then, when `enabled && !active` (`active` means `Starting` or `Advertising`), the string `sharing_enabled_unavailable`; then one availability string chosen by a `when`; then an Enable/Stop button, and a "Stop sharing" text button while `enabled && !active`.

After a package replacement, a reboot, a force-stop or a system stop of the app, the preference is true and nothing restarts the service, so the card shows `sharing_enabled_unavailable` followed by `sharing_disabled` ("Disabled. This transport does not share OTPs yet."). The same state appears when `SharingService.onDestroy` resets `Starting`/`Advertising` to `Disabled` without a failure. Only `START_STICKY` restarts the service after ordinary process death; `docs/otp-06-transport.md` promises no restart after force-stop, reboot or an explicit failure.

The owner chose copy only (issue raidenyn/veles-android#169, scope point 1; point 2, restoration, is out of scope). The permission set and the debug/release boundary stay unchanged.

## Goals / Non-Goals

**Goals:**
- In the one state "enabled, not running, availability `Disabled`", show a single line that says the transport was on and stopped and how to start it again; do not show the `Disabled` line there.
- Render the other 13 (enabled × availability) combinations exactly as today.
- Make the mapping testable on the JVM.

**Non-Goals:**
- Restarting sharing after a package replacement or reboot (no receiver, no `RECEIVE_BOOT_COMPLETED`).
- Changing `SharingState`, `SharingService`, the manifest, the buttons or the other availability strings.
- Rewording the plain disabled card (`enabled == false`), which still reads "Disabled. This transport does not share OTPs yet."
- An explicit unavailable state for a user stop through Task Manager (the RFC's wish); that case gets the same generic line.

## Decisions

- **A pure mapping function.** A top-level `internal fun sharingCardLines(enabled: Boolean, availability: SharingAvailability): List<Int>` in a new file `SharingCardText.kt` in the `sharing` package returns the string resource ids the card shows below its title, in order. `SharingCard` renders `sharingCardLines(enabled, state)` with one `Text` per id. Alternative: edit the `if`/`when` inline in the composable. Rejected: it can only be tested through Compose, and the "13 unchanged" claim would go unproved. The existing line order is kept: the `enabled && !active` line, then the availability line.
- **The rule.** `enabled && availability == Disabled` returns `[R.string.sharing_stopped]`. `active` states return only their availability line (`enabled` is not shown with them, as today, because `!active` is false). Every other combination returns what the card shows today: `[sharing_enabled_unavailable, <availability line>]` when `enabled && !active`, otherwise `[<availability line>]`. The check is on `availability == Disabled`, not on a derived "not running" flag, so a failed or refused start keeps its explanatory line.
- **A new string, not an edit.** `sharing_stopped` is new; `sharing_enabled_unavailable` stays for `Unsupported`, `PermissionRequired`, `BluetoothOff` and `Failed`, where it is still correct. Text: "Sharing is on, but the Bluetooth service is not running. This can happen after an app update, a restart or the system stopping the app. Tap Enable transport to start it again, or Stop sharing to turn it off." It does not claim an update only (the state has several causes) and names the two buttons the card already shows in this state. The text is exact copy, fixed here so the plan and test do not invent it.
- **Spec as a new capability (a deliberate departure from the approved brainstorm).** The brainstorm put the card requirement into `ble-sharing-availability`. The card's text is instead a new capability `sharing-card-copy`, because that capability's Purpose (prerequisites and advertising results) does not cover the card and a delta cannot widen a Purpose, so a new capability avoids a stale description. The brainstorm's decision is superseded by this one.
- **Docs.** `docs/otp-06-transport.md` gains this exact sentence after "Force-stop, reboot, or an explicit failure does not promise an automatic restart: reopen Home and retry.": "When the process later starts again with the preference still on and no service running, the card says that the Bluetooth service is not running and how to start it again; the restart stays manual." An explicit failure in the same process still shows its own availability line (`Failed`, `Unsupported`, …), not this one.
- **Verification.** JVM unit tests assert all 14 combinations against today's expected lines by comparing resource ids (no Android runtime needed for that), and, in house style (`@RunWith(RobolectricTestRunner::class)`, `@Config(sdk = [33])`, as in `SharingStateTest`), assert that `sharing_stopped` resolves to the exact copy above. A real-phone check first installs the build, enables sharing and reaches `Advertising`, then reinstalls over it with `adb install -r`, reads the card, confirms with `dumpsys activity services` that `SharingService` is absent, presses Enable transport and confirms `Advertising` with the service in the foreground, then stops sharing and confirms the plain `Disabled` card.

## Risks / Trade-offs

- [The new line is shown for any `Disabled`-while-enabled state, including two brief windows: after a tap, until `SharingService.onStartCommand` publishes `Starting` (it passes the system service start, the prerequisite check and `startForeground` first, so more than one frame), and on a fresh enable between `setEnabled(true)` and `update(Starting)`] → the wording ("not running", "tap Enable transport") is still true in both windows and is no worse than today's two lines.
- [The line says "can happen after an app update, a restart or the system stopping the app", which is a partial list of causes] → it is phrased as "can happen", not as the cause; the Task Manager Stop case is accepted as the same generic line.
- [Sharing stays off after every update until the user taps Enable transport] → accepted by the owner for now; restoration is a separate decision and no follow-up is filed.
- [A JVM test that compares resource ids can drift from what is rendered] → the card is a thin loop over the function's result, and the real-phone check reads the rendered card.

## Migration Plan

No data, preference or manifest migration. The change ships with the next build; rollback is reverting the commit.

## Open Questions

None.
