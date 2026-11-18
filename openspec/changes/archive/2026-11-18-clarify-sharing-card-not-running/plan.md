# Sharing card: stopped-transport line Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When sharing is enabled but the transport is not running and the availability is `Disabled`, the Device sharing card shows one plain "stopped" line instead of the misleading "Disabled. This transport does not share OTPs yet." line.

**Architecture:** The card's text lines become a pure function `sharingCardLines(enabled, availability)` that returns string resource ids; `SharingCard` renders one `Text` per id. Only the (enabled, `Disabled`) combination changes; the other 13 keep today's output. No service, manifest, permission or debug/release change.

**Tech Stack:** Kotlin, Jetpack Compose, Android string resources, JUnit4 + Robolectric (`@Config(sdk = [33])`), Gradle from `android/`.

**Spec:** `openspec/changes/clarify-sharing-card-not-running/specs/sharing-card-copy/spec.md`, `design.md`, `tasks.md` in the same directory.

## Global Constraints

- Copy of the new string `sharing_stopped`, verbatim: `Sharing is on, but the Bluetooth service is not running. This can happen after an app update, a restart or the system stopping the app. Tap Enable transport to start it again, or Stop sharing to turn it off.`
- `sharing_enabled_unavailable` and `sharing_disabled` keep their current text; the card's buttons, the `start` lambda and the `active` logic do not change.
- Only the (enabled = true, `Disabled`) combination yields `[R.string.sharing_stopped]`; for the other 13 the order is the `enabled && !active` line first, then the availability line.
- Do not touch `SharingState`, `SharingService`, the manifest, the permission set or the debug/release boundary. Never uninstall the app or clear its data, and never regenerate or commit signing keys.
- Commit subjects are Conventional Commits: `type(scope): description`.
- No test sleeps or depends on the wall clock.

## Review Focus

- Enabled with `Starting` or `Advertising` still shows only the availability line (not the stopped line): pinned by the 14-combination test in Task 1.
- Enabled with `Failed` or `Unsupported` still shows the "enabled, but not running" line plus its own line: pinned by the same test.
- Not enabled with `Disabled` still reads "Disabled. This transport does not share OTPs yet.": pinned by the same test.
- The string resolves to the exact copy (a typo would pass an id-only test): Task 1 resolves it with a Robolectric context.
- The card still renders what the function returns, and the buttons are unchanged: the real-phone check in Task 3.

---

## Task 1: Line mapping, string and tests

**Files:**
- Modify: `android/app/src/main/res/values/sharing.xml`
- Create: `android/app/src/main/java/me/nagaev/veles/sharing/SharingCardText.kt`
- Test: `android/app/src/test/java/me/nagaev/veles/sharing/SharingCardTextTest.kt`

**Interfaces:**
- Produces: `internal fun sharingCardLines(enabled: Boolean, availability: SharingAvailability): List<Int>` in package `me.nagaev.veles.sharing`, returning the string resource ids shown below the card's title, in order (a plain `List<Int>`: `@StringRes` has no type-use target and cannot annotate a type argument; say in the KDoc that the ids are string resources); and the string resource `R.string.sharing_stopped`.

- [ ] **Step 1: Write the failing test.** `SharingCardTextTest` (`@RunWith(RobolectricTestRunner::class)`, `@Config(sdk = [33])`, house style of `SharingStateTest`) with these tests, using `me.nagaev.veles.R`:
  - `enabledWithDisabledShowsOnlyTheStoppedLine`: `sharingCardLines(true, SharingAvailability.Disabled) == listOf(R.string.sharing_stopped)`.
  - `notEnabledAndDisabledShowsOnlyTheDisabledLine`: `sharingCardLines(false, Disabled) == listOf(R.string.sharing_disabled)`.
  - `enabledAndFailingStatesShowTheNotRunningLineThenTheirOwn`: for `Unsupported`, `PermissionRequired`, `BluetoothOff`, `Failed` with `enabled = true`, the result is `listOf(R.string.sharing_enabled_unavailable, own)` where `own` is `sharing_unsupported`, `sharing_permission`, `sharing_bluetooth_off`, `sharing_failed` respectively.
  - `enabledAndRunningShowOnlyTheirOwnLine`: `Starting` → `[sharing_starting]`, `Advertising` → `[sharing_advertising]`, with `enabled = true`.
  - `notEnabledShowsOnlyTheOwnLineForEveryAvailability`: for all 7 values with `enabled = false`, the result is the single own line from the table above (`Disabled` → `sharing_disabled`).
  - `availabilityLinesResolveToTheirExactText`: with the same context, `sharing_disabled`, `sharing_starting`, `sharing_advertising`, `sharing_unsupported`, `sharing_permission`, `sharing_bluetooth_off`, `sharing_failed` and `sharing_enabled_unavailable` resolve to the texts fixed by the spec scenario `Availability lines` and by the "Sharing is enabled, but the transport is not running. Enable transport to retry, or stop sharing." line.
  - `stoppedLineResolvesToTheExactCopy`: `ApplicationProvider.getApplicationContext<Application>().getString(R.string.sharing_stopped)` equals the verbatim copy in Global Constraints.

- [ ] **Step 2: Run it to see it fail.** From `android/`: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.sharing.SharingCardTextTest"`. Expected: FAIL at compile time (`sharingCardLines` and `R.string.sharing_stopped` do not exist).

- [ ] **Step 3: Add the string.** In `sharing.xml` add `<string name="sharing_stopped">` with the verbatim copy next to `sharing_enabled_unavailable`; change no other string.

- [ ] **Step 4: Implement `sharingCardLines`** in `SharingCardText.kt` with the signature above. Compute the availability's own line with the same mapping the card uses today (`Disabled` → `sharing_disabled`, `Starting` → `sharing_starting`, `Advertising` → `sharing_advertising`, `Unsupported` → `sharing_unsupported`, `PermissionRequired` → `sharing_permission`, `BluetoothOff` → `sharing_bluetooth_off`, `Failed` → `sharing_failed`). Rule: `enabled && availability == Disabled` → `listOf(sharing_stopped)`; else `enabled && availability` not `Starting`/`Advertising` → `listOf(sharing_enabled_unavailable, own)`; else `listOf(own)`.

- [ ] **Step 5: Run the test to see it pass.** Same command. Expected: PASS, 7 tests.

- [ ] **Step 6: Commit.**

```bash
git add android/app/src/main/res/values/sharing.xml android/app/src/main/java/me/nagaev/veles/sharing/SharingCardText.kt android/app/src/test/java/me/nagaev/veles/sharing/SharingCardTextTest.kt
git commit -m "fix(android): add the sharing card stopped-transport line mapping"
```

## Task 2: Render the mapping and document it

**Files:**
- Modify: `android/app/src/main/java/me/nagaev/veles/sharing/SharingCard.kt`
- Modify: `docs/otp-06-transport.md`

**Interfaces:**
- Consumes: `sharingCardLines(enabled: Boolean, availability: SharingAvailability): List<Int>` from Task 1.

- [ ] **Step 1: Wire the card.** In `SharingCard.kt` replace the inline `if (enabled && !active) Text(...)` and the `Text(stringResource(when (state) {...}))` with `sharingCardLines(enabled, state).forEach { Text(stringResource(it)) }` in the same position (after the title, before the first `Button`). Leave the `active` value, the `start` lambda, the permission launcher and both buttons unchanged. No import becomes unused (`Text`, `stringResource`, `R` and `SharingAvailability` stay in use), so change none.

- [ ] **Step 2: Run the unit tests.** From `android/`: `./gradlew testDebugUnitTest`. Expected: PASS.

- [ ] **Step 3: Add the documentation sentence.** In `docs/otp-06-transport.md`, after "Force-stop, reboot, or an explicit failure does not promise an automatic restart: reopen Home and retry." add: "When the process later starts again with the preference still on and no service running, the card says that the Bluetooth service is not running and how to start it again; the restart stays manual." Keep the paragraph's line wrapping style.

- [ ] **Step 4: Commit.**

```bash
git add android/app/src/main/java/me/nagaev/veles/sharing/SharingCard.kt docs/otp-06-transport.md
git commit -m "fix(android): show a plain stopped line on the sharing card"
```

## Task 3: Verification

**Files:**
- Create: `openspec/changes/clarify-sharing-card-not-running/apply.md` (the record of what ran and on what)

- [ ] **Step 1: Lint gate.** From `android/`: `./gradlew spotlessCheck detekt lintDebug`, then the raw `android.util.Log` guard exactly as in the `android / lint` job of `.github/workflows/check-android.yml`. On a spotless failure run `./gradlew spotlessApply`, inspect the diff and rerun. Expected: all pass.

- [ ] **Step 2: Real-phone check.** Needs a real phone; with none available, stop and ask, and do not report the change as tested. The init script `android/config/android_keys/fixture-signing.gradle` is git-ignored and may be absent from this checkout: if it is, stop and ask the owner, never build with another key and never fall back to uninstalling. Build from `android/` with `./gradlew --init-script config/android_keys/fixture-signing.gradle assembleDebug`; compare the new APK's signing-certificate fingerprint with the installed app's before `adb install -r`. Then: install, enable sharing, reach `Advertising`; `adb install -r` over it; read the card (one status line equal to the verbatim copy, no "does not share OTPs yet", buttons Enable transport and Stop sharing present); `dumpsys activity services` shows no `SharingService`; press Enable transport and confirm `Advertising` and the service in the foreground; press the card's Stop sharing button and confirm "Disabled. This transport does not share OTPs yet." Then enable sharing again, `adb install -r` over it once more, and in the stopped card press its Stop sharing text button; confirm the plain "Disabled. This transport does not share OTPs yet." card and, after force-stopping and reopening the app, that the stopped line does not return. Record device, commands and outputs in `apply.md`.

- [ ] **Step 3: Repository checks.** `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)`; run the checks in the `AGENTS.md` of every node it selects; then `openspec validate --all`. Expected: all pass.

- [ ] **Step 4: Commit the record.**

```bash
git add openspec/changes/clarify-sharing-card-not-running/apply.md
git commit -m "docs(openspec): record the sharing card verification"
```
