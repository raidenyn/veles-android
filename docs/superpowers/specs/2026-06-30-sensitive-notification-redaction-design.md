# Sensitive Notification Redaction — Design Spec

**Date:** 2026-06-30

## Overview

Android 15 (and OxygenOS 15 on OnePlus) redacts sensitive notification content
for `NotificationListenerService` instances that lack sensitive-notification
access. When redaction is active, `extras.getCharSequence(EXTRA_TEXT)` returns a
localized placeholder instead of the real OTP text, so Veles' regex handlers
can never match.

This spec adds a redaction-awareness subsystem to Veles that:

1. Detects, locale- and OEM-agnostically, when notification content is redacted.
2. Exposes the redaction state to the existing `PermissionsScreen` as a new
   section (collapsed by default, expanded when redaction is detected).
3. Deep-links the user to the correct per-OEM settings screen to fix the
   toggle.
4. Provides a "test sensitive reading" probe so the user can verify the toggle
   is on without needing a real bank SMS.

### Non-goals (explicitly out of scope)

- **No attempt to acquire `ROLE_COMPANION_DEVICE_WATCH`.** The role is gated
  behind a real `CompanionDeviceManager.associate()` pairing handshake and
  cannot be self-granted or spoofed. Apps that tried this on the Play Store
  have been pulled. Veles does not attempt it.
- **No reflection or accessibility-based content recovery.** The redaction
  happens in `NotificationManagerService` *before* the `StatusBarNotification`
  is dispatched, so the real `CharSequence` is never present in the bundle
  the listener receives. Reflection on the listener side cannot recover it.
  Using an accessibility service to scrape the notification shade's sensitive
  content is gated on Android 15 and is a Play-policy violation for
  non-assistive apps.
- **No per-OEM settings customization beyond Stock and OxygenOS.** Samsung /
  Xiaomi / etc. fall through to `StockAndroid` for now; the
  `NotificationRedactionPath` abstraction leaves room to add them later.

---

## Architecture

Five new/changed components:

```
NotificationListener.onNotificationPosted
  → RedactionDetector.isRedacted(sbn)
  → RedactionStateFlow.value = Hidden | Readable
  → PermissionsViewModel observes
  → PermissionsScreen renders redaction section (collapsed/expanded)
  → User taps "Open settings" → NotificationRedactionPath.settingsIntent → system settings
  → User returns, taps "Test sensitive reading"
  → TestNotificationSender.postSecretProbe()
  → NotificationListener sees real text → RedactionStateFlow = Readable
  → Section collapses to "✓ readable"
```

---

## Component details

### 1. `RedactionDetector` (new, `otp/RedactionDetector.kt`)

Pure detection logic. Locale- and OEM-agnostic — does **not** match on the
redaction placeholder string (which is a framework-localized resource that
varies by locale and OEM).

Signals used:

- `Notification.visibility == Notification.VISIBILITY_SECRET` — the sender
  declared the notification secret. This AOSP field is preserved on the
  `Notification` object the listener receives, across all OEMs. The system
  only redacts *secret-visibility* notifications for listeners lacking
  sensitive-notification access.
- Content heuristic: no 4+ digit run in `EXTRA_TEXT`. A real bank OTP always
  contains a digit group; the redacted placeholder contains none. This
  works on Thai, Chinese, German, Japanese, etc. — no string matching.

```kotlin
object RedactionDetector {
    private val DIGIT_RUN = Regex("\\d{4,}")
    fun isRedacted(sbn: StatusBarNotification): Boolean {
        val n = sbn.notification ?: return false
        val text = n.extras?.getCharSequence(NotificationCompat.EXTRA_TEXT)?.toString().orEmpty()
        return n.visibility == Notification.VISIBILITY_SECRET
            && text.isNotBlank()
            && DIGIT_RUN.find(text) == null
    }
}
```

**False-positive trade-off:** a non-OTP `VISIBILITY_SECRET` notification from a
bank (e.g., "Card blocked, call us") with no digit run would be flagged
redacted and wrongly expand the section. This is acceptable because (a) it
self-corrects on the next real OTP, which has a digit run, flipping the state
to `Readable`, and (b) the user seeing "sensitive access hidden" when they
have a genuine sensitive notification is slightly over-cautious, not a wrong
OTP read.

The signature takes the full `StatusBarNotification` (not just the
`CharSequence`) so it can inspect `notification.visibility`. This is a change
from the original placeholder-matching design.

### 2. `RedactionState` + `RedactionStateFlow` (new, `common/RedactionStateFlow.kt`)

Mirrors the existing `TestResultFlow` singleton pattern.

```kotlin
enum class RedactionState { Unknown, Readable, Hidden }

object RedactionStateFlow {
    val current = MutableStateFlow(RedactionState.Unknown)
}
```

- `Unknown` — no secret notification seen yet.
- `Readable` — a secret notification was seen with real content (digit run
  present), i.e. sensitive access is on.
- `Hidden` — a secret notification was seen redacted, i.e. sensitive access
  is off.

Set by `NotificationListener`; observed by `PermissionsViewModel`. Separate
from `TestResultFlow` so the existing test screen's state is untouched.

### 3. `NotificationRedactionPath` (new, `otp/NotificationRedactionPath.kt`)

Sealed interface, two known implementations, one factory. Each impl provides
the settings deep-link intent, a human-readable settings location string, and
explainer copy.

```kotlin
sealed interface NotificationRedactionPath {
    val settingsIntent: Intent
    val settingsLocation: String
    val explainerCopy: String

    object StockAndroid : NotificationRedactionPath {
        override val settingsIntent: Intent =
            if (Build.VERSION.SDK_INT >= 34)
                Intent(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS)
                    .putExtra(Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME, componentName)
            else
                Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
        override val settingsLocation =
            "Settings → Notifications → Notification access → Veles → Sensitive notifications"
        override val explainerCopy =
            "Your device hides sensitive notification content from Veles. " +
            "In Settings, open Notifications → Notification access → Veles, " +
            "and turn on 'Sensitive notifications.'"
    }

    object OxygenOS : NotificationRedactionPath {
        override val settingsIntent: Intent = StockAndroid.settingsIntent
        // OnePlus "Enhanced Notifications" / "Intelligent notifications" toggle
        // appears inside the same listener-detail screen on OxygenOS 15.
        override val settingsLocation =
            "Settings → Notifications → Notification access → Veles → Enhanced Notifications"
        override val explainerCopy =
            "Your OnePlus device hides sensitive notification content. " +
            "In Settings, open Notifications → Notification access → Veles, " +
            "and turn off 'Enhanced Notifications.'"
    }

    companion object {
        fun from(manufacturer: String?): NotificationRedactionPath =
            when (manufacturer?.lowercase()) {
                "oneplus" -> OxygenOS
                else -> StockAndroid
            }
    }
}
```

**API-level fallback:** `Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS`
and `Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME` require API 34+.
Veles' `minSdk` is 33, so on API 33 we fall back to
`Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS` (the list screen; the user then
taps Veles to reach the detail page).

**`componentName`** is the listener's `ComponentName`, resolved at construction
time and passed in (or resolved via `packageManager` in the factory). The exact
construction point is deferred to the implementation plan; the spec only
requires that the intent opens the correct per-listener detail page on API
34+.

**Extensibility:** Samsung (`"samsung"`), Xiaomi (`"xiaomi"`/`"redmi"`), etc.
can be added as new `object`s in the `when` block without touching the rest of
the codebase. Until added, they fall through to `StockAndroid`.

### 4. `NotificationListenerService` change (`otp/NotificationListenerService.kt`)

In `onNotificationPosted`, after extracting `text` (current lines 70-71), insert
the redaction check. The existing handler chain is unchanged.

```kotlin
// Existing:
val title = extras?.getCharSequence(NotificationCompat.EXTRA_TITLE).toString()
val text = extras?.getCharSequence(NotificationCompat.EXTRA_TEXT).toString()

// New (after the existing Log.d line):
if (RedactionDetector.isRedacted(it)) {
    RedactionStateFlow.current.value = RedactionState.Hidden
} else if (it.notification?.visibility == Notification.VISIBILITY_SECRET
           && RedactionStateFlow.current.value == RedactionState.Hidden) {
    RedactionStateFlow.current.value = RedactionState.Readable
}
```

- `isRedacted` is called with the full `sbn` (not just `text`) so it can read
  `notification.visibility`.
- The `else if` branch handles the "user fixed it" transition. It fires only
  when the notification is `VISIBILITY_SECRET` *and* `isRedacted` returned
  false (which, combined, means a secret notification whose real content came
  through — i.e. sensitive access is on). A `VISIBILITY_PUBLIC` notification
  is never redacted regardless of the toggle, so it is not evidence the
  toggle is on and must not trigger the `Hidden → Readable` transition. The
  explicit `visibility == VISIBILITY_SECRET` guard enforces this. Once
  `Hidden`, the first non-redacted secret notification flips the state to
  `Readable`, collapsing the onboarding section automatically.
- The existing `NotificationListenerTest` mocks remain valid: they return
  non-redacted strings and (implicitly) `VISIBILITY_PUBLIC`, so the detector
  returns `false` and the state is unchanged from `Unknown`. No existing test
  breaks.

### 5. `TestNotificationSender` extension (`testing/TestNotificationSender.kt`)

Add `postSecretProbe()` that posts on a new channel
`VelesSecretProbeChannel` (registered alongside the existing
`VelesTestChannel`) with:

```kotlin
Notification.Builder(context, VelesSecretProbeChannel.CHANNEL_ID)
    .setVisibility(Notification.VISIBILITY_SECRET)
    .setContentTitle("Veles probe")
    .setContentText("PROBE_OTP_123456")
    .setSmallIcon(R.drawable.ic_stat_name)
    .build()
```

- `VISIBILITY_SECRET` makes the system apply the same redaction path it
  applies to bank OTPs.
- The text contains a 6-digit run (`123456`) so that when sensitive access is
  on, the listener reads real text → `RedactionDetector.isRedacted` returns
  `false` → `RedactionStateFlow` flips to `Readable`. When sensitive access
  is off, the listener reads the redacted placeholder (no digit run) →
  `isRedacted` returns `true` → state flips to `Hidden`.
- The probe is **not** wired into `TestResultFlow`. It flows exclusively
  through `RedactionStateFlow` to keep the redaction and test-harness
  subsystems distinct, per the decision in brainstorming.

**Known limitation:** Android's exact redaction rules for self-posted
`VISIBILITY_SECRET` notifications are not well-documented and may differ from
third-party SMS redaction. The spec acknowledges this; the probe is a
best-effort signal. The existing `TestScreen` (which posts non-secret
notifications for regex validation) remains as the place to sanity-check
the regex chain with real bank SMS text.

---

## UX on `PermissionsScreen`

The redaction section renders below the existing permission list. It is a
section, not a permission row — it does not extend `PermissionProvider` or
`PermissionsActions`, because the redaction toggle is a per-listener settings
switch, not a grantable Android permission. Forcing it into the
`PermissionProvider` model would require `PermissionProvider.check()` to
query a non-existent permission, which is a leaky abstraction.

### State → rendering

| `RedactionState` | Rendering |
|---|---|
| `Unknown` | Collapsed row: "Sensitive notification access: Not yet checked". A small "Run a test" button posts the secret probe. |
| `Readable` | Collapsed row: "Sensitive notification access: ✓ Readable". Tap to expand shows brief explainer ("Veles can read your bank OTPs."). |
| `Hidden` | Expanded. Header "⚠ Sensitive notification access is off". Explainer copy from `NotificationRedactionPath.explainerCopy` (OEM-specific). Two buttons: "Open settings" (deep-link) and "Test sensitive reading" (probe). |

### Probe interaction

- User taps "Test sensitive reading" (or "Run a test" in `Unknown` state).
- `TestNotificationSender.postSecretProbe()` fires.
- `NotificationListener.onNotificationPosted` receives the probe, runs
  `RedactionDetector`, updates `RedactionStateFlow`.
- The UI reacts to the `RedactionStateFlow` state change (Collected in
  `PermissionsViewModel`).
- If the probe comes back `Readable` within ~3 seconds, the section
  auto-collapses to "✓ Readable".
- If `Hidden` after ~3 seconds, show "Still hidden — try the steps above
  again." and stay expanded.
- The 3-second window is a ViewModel-side timeout on the `StateFlow`
  collection, not a service-side timeout.

### `PermissionsViewModel` / `PermissionsState` changes

- Add `redactionState: StateFlow<RedactionState>` to `PermissionsViewModel`,
  backed by `RedactionStateFlow.current`. (Not added to `PermissionsState`
  data class directly to keep the existing permission-list state shape
  stable; the screen composes `permissionsState` and `redactionState`
  independently. The implementation plan may merge them if cleaner.)
- Add an action for "post secret probe" → calls `TestNotificationSender`.
- Add an action for "open settings" → launches the
  `NotificationRedactionPath.settingsIntent` via the activity.

### OEM detection

`NotificationRedactionPath.from(Build.MANUFACTURER)` is resolved once in the
ViewModel (or the screen), not per-render. The result is held in the
ViewModel state so the intent and copy are stable across recompositions.

---

## Testing strategy

### Unit tests (`src/test/`)

**`RedactionDetectorTest`** (pure JVM):
- `VISIBILITY_SECRET` + no digit run → `true`
- `VISIBILITY_SECRET` + digit run → `false`
- `VISIBILITY_PUBLIC` (or `PRIVATE`) + no digit run → `false`
- `null` notification → `false`
- Empty/blank text with `VISIBILITY_SECRET` → `false`
- Non-ASCII OTP text (Thai) with digit run → `false`
- Locale-agnostic: placeholder strings in German/Chinese/Japanese (which
  contain no digit runs) with `VISIBILITY_SECRET` → `true`

Pure JVM tests. `Notification` and `StatusBarNotification` are Android types,
so either (a) use small hand-rolled fakes implementing the two fields the
detector reads (`notification.visibility`, `notification.extras`), or (b) use
MockK with `mockk<StatusBarNotification>()`. The implementation plan picks the
approach that matches the existing `src/test/` style (the repo's existing
unit tests use plain data classes where possible).

**`NotificationRedactionPathTest`** (pure JVM):
- `from("oneplus")` → `OxygenOS`
- `from("OnePlus")` → `OxygenOS` (case-insensitive)
- `from("Google")` → `StockAndroid`
- `from(null)` → `StockAndroid`
- `from("samsung")` → `StockAndroid` (until a Samsung impl is added)

Intent action constants are asserted without constructing the full `Intent`
(since `Intent` is an Android type; assert on the `action` string only, or
use Robolectric if the repo already does).

### Instrumented tests (`src/androidTest/`)

- **Extend `NotificationListenerTest`** — add a case where the mocked
  `EXTRA_TEXT` is a redaction placeholder and `notification.visibility` is
  `VISIBILITY_SECRET`, assert `RedactionStateFlow.current.value == Hidden`.
  Existing tests stay green (they use `VISIBILITY_PUBLIC` implicitly).
- **Extend `VelesPermissionsAppTests`** (Compose UI) — add tests asserting:
  - Redaction section renders collapsed at `Unknown`.
  - Redaction section renders expanded at `Hidden` (drive by setting
    `RedactionStateFlow.current.value` before rendering).
  - "Open settings" and "Test sensitive reading" buttons are present in the
    expanded state.
  - Tapping "Test sensitive reading" calls `postSecretProbe` (verify via a
    mock or spy).

### Manual end-to-end check (documented, not automated)

On a OnePlus device with OxygenOS 15 and sensitive access off, post a real
bank OTP SMS (or simulate one). Verify the section expands. Tap "Open
settings", toggle "Enhanced Notifications" off. Tap "Test sensitive reading",
verify the section collapses to "✓ Readable". This checks the path the unit
tests cannot: that self-posted `VISIBILITY_SECRET` redaction matches
third-party SMS redaction.

---

## Files changed / added

| File | Change |
|---|---|
| `app/src/main/java/me/nagaev/veles/otp/RedactionDetector.kt` | **New** |
| `app/src/main/java/me/nagaev/veles/common/RedactionStateFlow.kt` | **New** |
| `app/src/main/java/me/nagaev/veles/otp/NotificationRedactionPath.kt` | **New** |
| `app/src/main/java/me/nagaev/veles/otp/NotificationListenerService.kt` | Modified (insert redaction check in `onNotificationPosted`) |
| `app/src/main/java/me/nagaev/veles/testing/TestNotificationSender.kt` | Modified (add `postSecretProbe()` + `VelesSecretProbeChannel`) |
| `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModel.kt` | Modified (observe `RedactionStateFlow`, add probe + open-settings actions) |
| `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsState.kt` | Modified (add redaction state field, if merged) |
| `app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt` | Modified (add redaction section UI) |
| `app/src/test/java/me/nagaev/veles/otp/RedactionDetectorTest.kt` | **New** |
| `app/src/test/java/me/nagaev/veles/otp/NotificationRedactionPathTest.kt` | **New** |
| `app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt` | Modified (add redaction case) |
| `app/src/androidTest/java/me/nagaev/veles/permissions/ui/VelesPermissionsAppTests.kt` | Modified (add redaction-section UI tests) |

No new Gradle dependencies. No `AndroidManifest.xml` changes (no new
permission or role is declared — the redaction toggle is a settings switch,
not a permission, and the listener service is already declared).