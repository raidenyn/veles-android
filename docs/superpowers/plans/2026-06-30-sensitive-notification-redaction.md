# Sensitive Notification Redaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when Android 15 / OxygenOS 15 redacts sensitive notification content for Veles' `NotificationListenerService`, expose that state to `PermissionsScreen`, deep-link the user to the OEM-correct settings toggle, and let them verify the fix with a self-posted `VISIBILITY_SECRET` probe.

**Architecture:** A `RedactionDetector` (pure, locale-agnostic — checks `Notification.visibility == VISIBILITY_SECRET` plus absence of a 4+ digit run in `EXTRA_TEXT`) is called from `NotificationListener.onNotificationPosted` and writes to a `RedactionStateFlow` singleton (mirroring `TestResultFlow`). `PermissionsViewModel` observes it and renders a new section in `PermissionsScreen` (collapsed when readable/unknown, expanded when hidden) with an OEM-specific settings deep-link (`NotificationRedactionPath`, sealed interface with `StockAndroid` and `OxygenOS` impls) and a "Test sensitive reading" button that posts a `VISIBILITY_SECRET` probe via an extended `TestNotificationSender`.

**Tech Stack:** Kotlin, Jetpack Compose, MockK (unit + instrumented tests), Robolectric (available in `src/test/`), StateFlow MVVM, Room (unchanged), Android 15 (targetSdk 35, minSdk 33).

## Global Constraints

- `compileSdk = 35`, `targetSdk = 35`, `minSdk = 33` (from `app/build.gradle.kts`).
- `Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS` and `Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME` require API 34+. On API 33 fall back to `Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS` (the list page; user taps Veles).
- No string-matching of the redaction placeholder — it is a framework-localized resource. Detection uses `Notification.visibility` + digit-run heuristic only.
- The redaction toggle is a per-listener settings switch, NOT a grantable Android permission. Do not extend `PermissionProvider` / `PermissionType` / `PermissionsActions`. The redaction section is a separate Compose section in `PermissionsScreen` backed by a separate `RedactionStateFlow`.
- `src/test/` has Robolectric + MockK (`mockk.android`, `mockk.agent`, `robolectric`, `androidx.test.core`). Robolectric lets `src/test/` construct real `Notification`, `Bundle`, and `StatusBarNotification` instances. Prefer Robolectric `RuntimeEnvironment` + real `Notification.Builder` over MockK mocks for `RedactionDetectorTest` (matches `RedactionDetector`'s pure-logic nature and avoids brittle mock setup).
- Unit test command: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.RedactionDetectorTest"` (etc). Instrumented: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=...` (requires device; CI runs unit tests only).
- `NotificationListener` constructor signature (`state, messageHandler, ownPackageName`) must not change — instrumented tests depend on it.
- Follow existing code style: no code comments unless asked, 4-space indent, `package me.nagaev.veles.*`.
- Commit message style: `feat:`, `test:`, `refactor:`, `docs:` prefixes (from `git log --oneline`).

---

## File Structure

| File | Responsibility | Status |
|---|---|---|
| `app/src/main/java/me/nagaev/veles/otp/RedactionDetector.kt` | Pure function: given a `StatusBarNotification`, return whether it's redacted. | Create |
| `app/src/test/java/me/nagaev/veles/otp/RedactionDetectorTest.kt` | Robolectric unit tests for the detector. | Create |
| `app/src/main/java/me/nagaev/veles/common/RedactionStateFlow.kt` | `RedactionState` enum + `RedactionStateFlow` singleton `MutableStateFlow`. | Create |
| `app/src/test/java/me/nagaev/veles/common/RedactionStateFlowTest.kt` | Minimal test that the flow holds state. | Create |
| `app/src/main/java/me/nagaev/veles/otp/NotificationRedactionPath.kt` | Sealed interface with `StockAndroid`/`OxygenOS` impls + `from(manufacturer)` factory. | Create |
| `app/src/test/java/me/nagaev/veles/otp/NotificationRedactionPathTest.kt` | Factory dispatch tests (Robolectric for `Intent`). | Create |
| `app/src/main/java/me/nagaev/veles/otp/NotificationListenerService.kt` | Insert redaction check in `onNotificationPosted` (after line 73). | Modify |
| `app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt` | Add redaction-state assertion cases. | Modify |
| `app/src/main/java/me/nagaev/veles/testing/TestNotificationSender.kt` | Add `postSecretProbe()` + `SECRET_CHANNEL_ID`. | Modify |
| `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt` | Add redaction-section test tags. | Modify |
| `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsState.kt` | Add `redactionState: RedactionState` field. | Modify |
| `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModel.kt` | Observe `RedactionStateFlow`; add `openRedactionSettings` / `testSensitiveReading` actions. | Modify |
| `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModelFactory.kt` | Wire `TestNotificationSender` + redaction-path into the VM. | Modify |
| `app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt` | Add redaction section composable. | Modify |
| `app/src/main/java/me/nagaev/veles/permissions/ui/components/RedactionSection.kt` | The collapsed/expanded redaction section UI. | Create |
| `app/src/androidTest/java/me/nagaev/permissions/ui/VelesPermissionsAppTests.kt` | Add redaction-section UI tests. | Modify |

---

## Task 1: `RedactionDetector` + unit tests

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/RedactionDetector.kt`
- Create: `app/src/test/java/me/nagaev/veles/otp/RedactionDetectorTest.kt`

**Interfaces:**
- Consumes: `android.service.notification.StatusBarNotification`, `android.app.Notification`, `androidx.core.app.NotificationCompat` (already in deps).
- Produces: `object RedactionDetector { fun isRedacted(sbn: StatusBarNotification): Boolean }` — consumed by Task 4 (`NotificationListenerService`).

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/me/nagaev/veles/otp/RedactionDetectorTest.kt`:

```kotlin
package me.nagaev.veles.otp

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import android.service.notification.StatusBarNotification
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class RedactionDetectorTest {

    private lateinit var context: Context
    private val notificationManager: NotificationManager
        get() = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        if (notificationManager.getNotificationChannel("test") == null) {
            notificationManager.createNotificationChannel(
                NotificationChannel("test", "test", NotificationManager.IMPORTANCE_HIGH)
            )
        }
    }

    private fun sbn(visibility: Int, text: CharSequence?): StatusBarNotification {
        val notification = Notification.Builder(context, "test")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("title")
            .setContentText(text)
            .setVisibility(visibility)
            .build()
        return StatusBarNotification(
            "pkg", "pkg", 1, "tag", 0, 0, 0, notification, android.os.UserHandle.of(0), 0L
        )
    }

    @Test
    fun `secret notification with no digits is redacted`() {
        assertTrue(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "Sensitive notification content hidden")))
    }

    @Test
    fun `secret notification with digit run is not redacted`() {
        assertFalse(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "Your OTP is 123456")))
    }

    @Test
    fun `public notification with no digits is not redacted`() {
        assertFalse(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_PUBLIC, "Sensitive notification content hidden")))
    }

    @Test
    fun `private notification with no digits is not redacted`() {
        assertFalse(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_PRIVATE, "no digits here")))
    }

    @Test
    fun `secret notification with blank text is not redacted`() {
        assertFalse(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "")))
    }

    @Test
    fun `secret notification with null text is not redacted`() {
        assertFalse(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, null)))
    }

    @Test
    fun `non-ASCII OTP text with digit run is not redacted`() {
        assertFalse(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "รหัส OTP ของคุณคือ 079853")))
    }

    @Test
    fun `German redaction placeholder with no digits is redacted`() {
        assertTrue(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "Vertrauliche Benachrichtigungsinhalte ausgeblendet")))
    }

    @Test
    fun `Japanese redaction placeholder with no digits is redacted`() {
        assertTrue(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "通知の機密内容は非表示です")))
    }

    @Test
    fun `notification with exactly 4 digits is not redacted`() {
        assertFalse(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "code 1234")))
    }

    @Test
    fun `notification with 3 digits is redacted`() {
        assertTrue(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "code 123")))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.RedactionDetectorTest"`
Expected: FAIL with "Unresolved reference: RedactionDetector"

- [ ] **Step 3: Write minimal implementation**

Create `app/src/main/java/me/nagaev/veles/otp/RedactionDetector.kt`:

```kotlin
package me.nagaev.veles.otp

import android.app.Notification
import android.service.notification.StatusBarNotification
import androidx.core.app.NotificationCompat

object RedactionDetector {
    private val DIGIT_RUN = Regex("\\d{4,}")

    fun isRedacted(sbn: StatusBarNotification): Boolean {
        val notification = sbn.notification ?: return false
        val text = notification.extras
            ?.getCharSequence(NotificationCompat.EXTRA_TEXT)
            ?.toString()
            .orEmpty()
        return notification.visibility == Notification.VISIBILITY_SECRET
            && text.isNotBlank()
            && DIGIT_RUN.find(text) == null
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.RedactionDetectorTest"`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/RedactionDetector.kt app/src/test/java/me/nagaev/veles/otp/RedactionDetectorTest.kt
git commit -m "feat: add RedactionDetector for locale-agnostic sensitive-notification redaction detection"
```

---

## Task 2: `RedactionStateFlow` + unit test

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/common/RedactionStateFlow.kt`
- Create: `app/src/test/java/me/nagaev/veles/common/RedactionStateFlowTest.kt`

**Interfaces:**
- Consumes: `kotlinx.coroutines.flow.MutableStateFlow` (already in deps).
- Produces: `enum class RedactionState { Unknown, Readable, Hidden }` and `object RedactionStateFlow { val current: MutableStateFlow<RedactionState> }` — consumed by Task 4 (writer) and Task 6 (reader).

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/me/nagaev/veles/common/RedactionStateFlowTest.kt`:

```kotlin
package me.nagaev.veles.common

import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

class RedactionStateFlowTest {

    @Before
    fun reset() {
        RedactionStateFlow.current.value = RedactionState.Unknown
    }

    @Test
    fun `initial state is Unknown`() {
        assertEquals(RedactionState.Unknown, RedactionStateFlow.current.value)
    }

    @Test
    fun `transitions to Hidden`() {
        RedactionStateFlow.current.value = RedactionState.Hidden
        assertEquals(RedactionState.Hidden, RedactionStateFlow.current.value)
    }

    @Test
    fun `transitions to Readable`() {
        RedactionStateFlow.current.value = RedactionState.Readable
        assertEquals(RedactionState.Readable, RedactionStateFlow.current.value)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.common.RedactionStateFlowTest"`
Expected: FAIL with "Unresolved reference: RedactionStateFlow"

- [ ] **Step 3: Write minimal implementation**

Create `app/src/main/java/me/nagaev/veles/common/RedactionStateFlow.kt`:

```kotlin
package me.nagaev.veles.common

import kotlinx.coroutines.flow.MutableStateFlow

enum class RedactionState { Unknown, Readable, Hidden }

object RedactionStateFlow {
    val current: MutableStateFlow<RedactionState> = MutableStateFlow(RedactionState.Unknown)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.common.RedactionStateFlowTest"`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/common/RedactionStateFlow.kt app/src/test/java/me/nagaev/veles/common/RedactionStateFlowTest.kt
git commit -m "feat: add RedactionStateFlow singleton for redaction state"
```

---

## Task 3: `NotificationRedactionPath` + unit tests

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/NotificationRedactionPath.kt`
- Create: `app/src/test/java/me/nagaev/veles/otp/NotificationRedactionPathTest.kt`

**Interfaces:**
- Consumes: `android.content.Intent`, `android.provider.Settings`, `android.os.Build`, `android.content.ComponentName`.
- Produces:
  - `sealed interface NotificationRedactionPath` with `settingsIntent: Intent`, `settingsLocation: String`, `explainerCopy: String`
  - `object StockAndroid : NotificationRedactionPath`
  - `object OxygenOS : NotificationRedactionPath`
  - `companion object { fun from(manufacturer: String?, componentName: ComponentName): NotificationRedactionPath }`
  - Consumed by Task 6 (`PermissionsViewModel`).

Note on `componentName`: the listener's `ComponentName` is passed in from the VM/factory (which has access to the package name + the fixed class name `me.nagaev.veles.otp.NotificationListener`). This keeps `NotificationRedactionPath` pure-testable without resolving the component at construction time.

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/me/nagaev/veles/otp/NotificationRedactionPathTest.kt`:

```kotlin
package me.nagaev.veles.otp

import android.content.ComponentName
import android.os.Build
import android.provider.Settings
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class NotificationRedactionPathTest {

    private val componentName = ComponentName("me.nagaev.veles", "me.nagaev.veles.otp.NotificationListener")

    @Test
    fun `from oneplus lowercase returns OxygenOS`() {
        assertTrue(NotificationRedactionPath.from("oneplus", componentName) is NotificationRedactionPath.OxygenOS)
    }

    @Test
    fun `from OnePlus mixed case returns OxygenOS`() {
        assertTrue(NotificationRedactionPath.from("OnePlus", componentName) is NotificationRedactionPath.OxygenOS)
    }

    @Test
    fun `from ONEPLUS uppercase returns OxygenOS`() {
        assertTrue(NotificationRedactionPath.from("ONEPLUS", componentName) is NotificationRedactionPath.OxygenOS)
    }

    @Test
    fun `from Google returns StockAndroid`() {
        assertTrue(NotificationRedactionPath.from("Google", componentName) is NotificationRedactionPath.StockAndroid)
    }

    @Test
    fun `from samsung returns StockAndroid`() {
        assertTrue(NotificationRedactionPath.from("samsung", componentName) is NotificationRedactionPath.StockAndroid)
    }

    @Test
    fun `from null returns StockAndroid`() {
        assertTrue(NotificationRedactionPath.from(null, componentName) is NotificationRedactionPath.StockAndroid)
    }

    @Test
    fun `StockAndroid settings intent uses detail action on API 34+`() {
        val path = NotificationRedactionPath.StockAndroid
        assertEquals(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS, path.settingsIntent.action)
    }

    @Test
    fun `StockAndroid settingsLocation mentions Sensitive notifications`() {
        assertTrue(NotificationRedactionPath.StockAndroid.settingsLocation.contains("Sensitive notifications"))
    }

    @Test
    fun `OxygenOS explainerCopy mentions Enhanced Notifications`() {
        assertTrue(NotificationRedactionPath.OxygenOS.explainerCopy.contains("Enhanced Notifications"))
    }

    @Test
    fun `OxygenOS settings intent matches StockAndroid intent action`() {
        assertEquals(
            NotificationRedactionPath.StockAndroid.settingsIntent.action,
            NotificationRedactionPath.OxygenOS.settingsIntent.action
        )
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.NotificationRedactionPathTest"`
Expected: FAIL with "Unresolved reference: NotificationRedactionPath"

- [ ] **Step 3: Write minimal implementation**

Create `app/src/main/java/me/nagaev/veles/otp/NotificationRedactionPath.kt`:

```kotlin
package me.nagaev.veles.otp

import android.content.ComponentName
import android.content.Intent
import android.os.Build
import android.provider.Settings

sealed interface NotificationRedactionPath {
    val settingsIntent: Intent
    val settingsLocation: String
    val explainerCopy: String

    object StockAndroid : NotificationRedactionPath {
        override val settingsLocation: String =
            "Settings > Notifications > Notification access > Veles > Sensitive notifications"
        override val explainerCopy: String =
            "Your device hides sensitive notification content from Veles. " +
            "In Settings, open Notifications > Notification access > Veles, " +
            "and turn on 'Sensitive notifications'."

        private fun buildIntent(componentName: ComponentName): Intent =
            if (Build.VERSION.SDK_INT >= 34) {
                Intent(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS)
                    .putExtra(Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME, componentName)
            } else {
                Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
            }

        override fun settingsIntent(componentName: ComponentName): Intent = buildIntent(componentName)
    }

    object OxygenOS : NotificationRedactionPath {
        override val settingsLocation: String =
            "Settings > Notifications > Notification access > Veles > Enhanced Notifications"
        override val explainerCopy: String =
            "Your OnePlus device hides sensitive notification content. " +
            "In Settings, open Notifications > Notification access > Veles, " +
            "and turn off 'Enhanced Notifications'."
        override fun settingsIntent(componentName: ComponentName): Intent =
            StockAndroid.settingsIntent(componentName)
    }

    fun settingsIntent(componentName: ComponentName): Intent

    companion object {
        fun from(manufacturer: String?, componentName: ComponentName): NotificationRedactionPath =
            when (manufacturer?.lowercase()) {
                "oneplus" -> OxygenOS
                else -> StockAndroid
            }
    }
}
```

Note: the `settingsIntent` is a function taking `componentName` rather than a property, because `ComponentName` is needed at call time but the sealed impls are singletons. Update the test in Step 1 if you changed this — tests call `path.settingsIntent` as a property. **Fix the test to use `path.settingsIntent(componentName)`.**

Re-edit the test file `app/src/test/java/me/nagaev/veles/otp/NotificationRedactionPathTest.kt`: replace every `path.settingsIntent` / `StockAndroid.settingsIntent` / `OxygenOS.settingsIntent` property access with `.settingsIntent(componentName)`. Specifically:

- In `StockAndroid settings intent uses detail action on API 34+`: change `path.settingsIntent.action` → `path.settingsIntent(componentName).action`
- In `OxygenOS settings intent matches StockAndroid intent action`: change both sides to call `.settingsIntent(componentName)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.NotificationRedactionPathTest"`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/NotificationRedactionPath.kt app/src/test/java/me/nagaev/veles/otp/NotificationRedactionPathTest.kt
git commit -m "feat: add NotificationRedactionPath sealed interface with Stock and OxygenOS impls"
```

---

## Task 4: Wire `RedactionDetector` into `NotificationListenerService`

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/NotificationListenerService.kt` (insert after line 73)
- Modify: `app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt` (add redaction cases)

**Interfaces:**
- Consumes: Task 1 `RedactionDetector.isRedacted(sbn)`, Task 2 `RedactionStateFlow`.
- Produces: `NotificationListener.onNotificationPosted` now writes to `RedactionStateFlow` as a side effect. No signature change.

- [ ] **Step 1: Write the failing test**

Append to `app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt`, inside `class NotificationListenerTest`. Add imports at the top:

```kotlin
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.RedactionStateFlow
```

Add a `@Before` to reset the flow (alongside the existing `resetTestResultFlow`):

```kotlin
@Before
fun resetRedactionStateFlow() {
    RedactionStateFlow.current.value = RedactionState.Unknown
}
```

Add these tests at the end of the class:

```kotlin
@Test
fun `onNotificationPosted sets RedactionStateFlow to Hidden when secret notification is redacted`() {
    val messageHandler = mockk<MessageHandler>(relaxed = true)
    val state = mockk<NotificationStatePreferences>(relaxed = true)
    val notification = mockk<Notification>(relaxed = true)
    val sbn = mockk<StatusBarNotification>(relaxed = true)
    val bundle = mockk<Bundle>(relaxed = true)

    every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "title"
    every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns "Sensitive notification content hidden"
    every { sbn.key } returns "key"
    every { sbn.packageName } returns "com.some.bank"
    every { sbn.notification } returns notification
    every { notification.visibility } returns Notification.VISIBILITY_SECRET
    notification.extras = bundle
    every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.FILTERED

    val service = NotificationListener(state, messageHandler, ownPackageName = "me.nagaev.veles")
    service.onCreate()
    service.onNotificationPosted(sbn)

    assertEquals(RedactionState.Hidden, RedactionStateFlow.current.value)
}

@Test
fun `onNotificationPosted sets RedactionStateFlow to Readable when previously Hidden and secret notification has digits`() {
    val messageHandler = mockk<MessageHandler>(relaxed = true)
    val state = mockk<NotificationStatePreferences>(relaxed = true)
    val notification = mockk<Notification>(relaxed = true)
    val sbn = mockk<StatusBarNotification>(relaxed = true)
    val bundle = mockk<Bundle>(relaxed = true)

    RedactionStateFlow.current.value = RedactionState.Hidden

    every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "title"
    every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns "Your OTP is 123456"
    every { sbn.key } returns "key"
    every { sbn.packageName } returns "com.some.bank"
    every { sbn.notification } returns notification
    every { notification.visibility } returns Notification.VISIBILITY_SECRET
    notification.extras = bundle
    every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.ACCEPTED

    val service = NotificationListener(state, messageHandler, ownPackageName = "me.nagaev.veles")
    service.onCreate()
    service.onNotificationPosted(sbn)

    assertEquals(RedactionState.Readable, RedactionStateFlow.current.value)
}

@Test
fun `onNotificationPosted does not set Readable from a public notification when Hidden`() {
    val messageHandler = mockk<MessageHandler>(relaxed = true)
    val state = mockk<NotificationStatePreferences>(relaxed = true)
    val notification = mockk<Notification>(relaxed = true)
    val sbn = mockk<StatusBarNotification>(relaxed = true)
    val bundle = mockk<Bundle>(relaxed = true)

    RedactionStateFlow.current.value = RedactionState.Hidden

    every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "title"
    every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns "some non-secret text"
    every { sbn.key } returns "key"
    every { sbn.packageName } returns "com.some.bank"
    every { sbn.notification } returns notification
    every { notification.visibility } returns Notification.VISIBILITY_PUBLIC
    notification.extras = bundle
    every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.FILTERED

    val service = NotificationListener(state, messageHandler, ownPackageName = "me.nagaev.veles")
    service.onCreate()
    service.onNotificationPosted(sbn)

    assertEquals(RedactionState.Hidden, RedactionStateFlow.current.value)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.otp.NotificationListenerTest`
Expected: FAIL — `RedactionStateFlow.current.value` is `Unknown`, not `Hidden`/`Readable`. (Requires a connected device/emulator. If no device, skip to Step 3 and run in CI later.)

- [ ] **Step 3: Write minimal implementation**

Edit `app/src/main/java/me/nagaev/veles/otp/NotificationListenerService.kt`. After line 73 (the `Log.d` line) and before line 75 (`val message = Message(`), insert:

```kotlin
            val notification = it.notification
            if (RedactionDetector.isRedacted(it)) {
                RedactionStateFlow.current.value = RedactionState.Hidden
            } else if (notification?.visibility == Notification.VISIBILITY_SECRET
                && RedactionStateFlow.current.value == RedactionState.Hidden
            ) {
                RedactionStateFlow.current.value = RedactionState.Readable
            }
```

Add the imports at the top of the file:

```kotlin
import android.app.Notification
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.RedactionStateFlow
```

The `androidx.core.app.NotificationCompat` import is already present (used for `EXTRA_TITLE`/`EXTRA_TEXT`). The `Notification` import is new (used for `VISIBILITY_SECRET`).

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.otp.NotificationListenerTest`
Expected: PASS (all 8 tests — 5 existing + 3 new). If no device, run unit tests that don't need the listener: `./gradlew testDebugUnitTest` and confirm no compilation errors.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/NotificationListenerService.kt app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt
git commit -m "feat: wire RedactionDetector into NotificationListener onNotificationPosted"
```

---

## Task 5: Extend `TestNotificationSender` with `postSecretProbe()`

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/testing/TestNotificationSender.kt`

**Interfaces:**
- Consumes: `androidx.core.app.NotificationCompat`, `android.app.NotificationChannel`, `android.app.NotificationManager` (already in deps).
- Produces: `TestNotificationSender.postSecretProbe()` and companion `SECRET_CHANNEL_ID`. Consumed by Task 6 (`PermissionsViewModel`).

- [ ] **Step 1: Write the failing test**

This task has no new unit test — `TestNotificationSender` posts real system notifications and can't be meaningfully unit-tested without an Android runtime. The instrumented UI tests in Task 7 verify the probe is triggered. Add a TODO note: the existing `TestNotificationSender` has no unit tests either (it's an Android-runtime class). Skip to Step 3.

- [ ] **Step 2: Run test to verify it fails**

N/A (no test in this task).

- [ ] **Step 3: Write minimal implementation**

Edit `app/src/main/java/me/nagaev/veles/testing/TestNotificationSender.kt`. Replace the file contents with:

```kotlin
package me.nagaev.veles.testing

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import me.nagaev.veles.R

class TestNotificationSender(private val context: Context) {
    companion object {
        const val CHANNEL_ID = "VelesTestChannel"
        const val SECRET_CHANNEL_ID = "VelesSecretProbeChannel"
        private const val NOTIFICATION_ID = 99999
        private const val SECRET_NOTIFICATION_ID = 99998
        private const val SECRET_PROBE_TEXT = "PROBE_OTP_123456"
    }

    fun post(text: String) {
        val builder = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_otp_message)
            .setContentTitle("Veles Test")
            .setContentText(text)
            .setPriority(NotificationCompat.PRIORITY_HIGH)

        with(NotificationManagerCompat.from(context)) {
            if (areNotificationsEnabled()) {
                tryCreateChannel()
                notify(NOTIFICATION_ID, builder.build())
            }
        }
    }

    fun postSecretProbe() {
        val builder = NotificationCompat.Builder(context, SECRET_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_otp_message)
            .setContentTitle("Veles probe")
            .setContentText(SECRET_PROBE_TEXT)
            .setVisibility(NotificationCompat.VISIBILITY_SECRET)
            .setPriority(NotificationCompat.PRIORITY_HIGH)

        with(NotificationManagerCompat.from(context)) {
            if (areNotificationsEnabled()) {
                tryCreateSecretChannel()
                notify(SECRET_NOTIFICATION_ID, builder.build())
            }
        }
    }

    private fun tryCreateChannel() {
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Veles Test",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "Test notifications for verifying handler configs"
        }
        manager.createNotificationChannel(channel)
    }

    private fun tryCreateSecretChannel() {
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(SECRET_CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            SECRET_CHANNEL_ID,
            "Veles Secret Probe",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "Sensitive-access probes for verifying redaction toggle state"
        }
        manager.createNotificationChannel(channel)
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL (compilation check; no new test to run).

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/testing/TestNotificationSender.kt
git commit -m "feat: add postSecretProbe to TestNotificationSender for redaction verification"
```

---

## Task 6: Wire redaction state into `PermissionsViewModel` + `PermissionsState`

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsState.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModel.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModelFactory.kt`

**Interfaces:**
- Consumes: Task 2 `RedactionStateFlow`/`RedactionState`, Task 3 `NotificationRedactionPath`, Task 5 `TestNotificationSender.postSecretProbe()`.
- Produces: `PermissionsViewModel` exposes `redactionState` via `uiState`, and `openRedactionSettings`/`testSensitiveReading` actions (added to `PermissionsActions`). Consumed by Task 7.

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModelRedactionTest.kt`:

```kotlin
package me.nagaev.veles.permissions.viewmodal

import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.otp.NotificationRedactionPath
import me.nagaev.veles.permissions.services.PermissionsProvider
import me.nagaev.veles.testing.TestNotificationSender
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class PermissionsViewModelRedactionTest {

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        RedactionStateFlow.current.value = RedactionState.Unknown
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun viewModel(
        provider: PermissionsProvider = mockk(relaxed = true),
        prefs: NotificationStatePreferences = mockk(relaxed = true),
        sender: TestNotificationSender = mockk(relaxed = true),
        path: NotificationRedactionPath = NotificationRedactionPath.StockAndroid,
    ): PermissionsViewModel = PermissionsViewModel(provider, prefs, sender, path)

    @Test
    fun `uiState reflects Unknown redaction state initially`() {
        val vm = viewModel()
        assertEquals(RedactionState.Unknown, vm.uiState.value.redactionState)
    }

    @Test
    fun `uiState reflects Hidden redaction state when flow updates`() {
        val vm = viewModel()
        RedactionStateFlow.current.value = RedactionState.Hidden
        assertEquals(RedactionState.Hidden, vm.uiState.value.redactionState)
    }

    @Test
    fun `testSensitiveReading calls postSecretProbe`() {
        val sender = mockk<TestNotificationSender>(relaxed = true)
        val vm = viewModel(sender = sender)
        vm.testSensitiveReading()
        verify { sender.postSecretProbe() }
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.permissions.viewmodal.PermissionsViewModelRedactionTest"`
Expected: FAIL with "Unresolved reference: redactionState" / "Cannot find parameter `sender`" / "Unresolved reference: testSensitiveReading".

- [ ] **Step 3: Write minimal implementation**

Edit `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsState.kt`. Replace the file:

```kotlin
package me.nagaev.veles.permissions.viewmodal

import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.permissions.services.PermissionType

data class PermissionsState(
    val permissions: Map<PermissionType, Permission>,
    val notificationListenerEnabled: Boolean,
    val redactionState: RedactionState = RedactionState.Unknown,
    val redactionSettingsLocation: String = "",
) {
    companion object {
        val Init = PermissionsState(emptyMap(), notificationListenerEnabled = false)
        val Mocked = PermissionsState(
            mapOf(
                PermissionType.ACCESS_NOTIFICATIONS to Permission(
                    PermissionType.ACCESS_NOTIFICATIONS,
                    true
                ),
                PermissionType.SEND_NOTIFICATIONS to Permission(
                    PermissionType.SEND_NOTIFICATIONS,
                    false
                )
            ),
            notificationListenerEnabled = false
        )
    }
}

data class Permission(
    val type: PermissionType,
    val granted: Boolean?
)
```

Edit `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModel.kt`. Replace the file:

```kotlin
package me.nagaev.veles.permissions.viewmodal

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.otp.NotificationRedactionPath
import me.nagaev.veles.permissions.services.PermissionProvider
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.services.PermissionsProvider
import me.nagaev.veles.testing.TestNotificationSender

interface PermissionsActions {
    val requestPermission: RequestPermission
    val revokePermission: RevokePermission
    val openRedactionSettings: () -> Unit
    val testSensitiveReading: () -> Unit

    companion object {
        val Mocked: PermissionsActions = object : PermissionsActions {
            override val requestPermission: RequestPermission = {}
            override val revokePermission: RevokePermission = {}
            override val openRedactionSettings: () -> Unit = {}
            override val testSensitiveReading: () -> Unit = {}
        }
    }
}

typealias RequestPermission = (type: PermissionType) -> Unit
typealias RevokePermission = (type: PermissionType) -> Unit

class PermissionsViewModel(
    private val permissionsProvider: PermissionsProvider,
    private val notificationStatePreferences: NotificationStatePreferences,
    private val testNotificationSender: TestNotificationSender,
    private val redactionPath: NotificationRedactionPath,
    private val openSettings: (android.content.Intent) -> Unit,
) : ViewModel(), PermissionsActions {

    private val _uiState = MutableStateFlow(PermissionsState.Init)
    val uiState: StateFlow<PermissionsState> = _uiState

    init {
        updatePermissionsState()
        viewModelScope.launch {
            RedactionStateFlow.current.collect { state ->
                _uiState.value = _uiState.value.copy(
                    redactionState = state,
                    redactionSettingsLocation = redactionPath.settingsLocation,
                )
            }
        }
    }

    fun updatePermissionsState() {
        _uiState.value =
            uiState.value.copy(
                permissions = permissionsProvider.providers.entries.associate {
                    it.key to Permission(it.key, it.value.isGranted())
                },
                notificationListenerEnabled = notificationStatePreferences.getConnectionState(),
                redactionSettingsLocation = redactionPath.settingsLocation,
            )
    }

    private fun unsetPermissionState(type: PermissionType) {
        _uiState.value =
            uiState.value.let { it ->
                it.copy(
                    permissions = it.permissions.toMutableMap().also {
                        it[type] = Permission(type, null)
                    }
                )
            }
    }

    private fun execute(type: PermissionType, method: suspend (PermissionProvider) -> Unit) {
        permissionsProvider.providers[type]?.let {
            unsetPermissionState(type)
            viewModelScope.launch {
                method(it)
                updatePermissionsState()
            }
        }
    }

    override val requestPermission: RequestPermission = { type ->
        execute(type) { provider -> provider.request() }
    }

    override val revokePermission: RevokePermission = { type ->
        execute(type) { provider -> provider.revoke() }
    }

    override val openRedactionSettings: () -> Unit = {
        openSettings(redactionPath.settingsIntent(componentNameForListener()))
    }

    override val testSensitiveReading: () -> Unit = {
        testNotificationSender.postSecretProbe()
    }

    private fun componentNameForListener(): android.content.ComponentName =
        android.content.ComponentName(
            getApplication<android.app.Application>().packageName,
            "me.nagaev.veles.otp.NotificationListener",
        )
}
```

Wait — the existing `PermissionsViewModel` does not extend `AndroidViewModel`, so `getApplication()` isn't available. The `componentName` must be passed in. Revise: add a `componentName: ComponentName` constructor parameter instead of resolving via `getApplication()`.

Re-edit the `PermissionsViewModel.kt` implementation. Replace the `componentNameForListener()` function and its call with a stored `componentName`:

- Change the constructor to:
```kotlin
class PermissionsViewModel(
    private val permissionsProvider: PermissionsProvider,
    private val notificationStatePreferences: NotificationStatePreferences,
    private val testNotificationSender: TestNotificationSender,
    private val redactionPath: NotificationRedactionPath,
    private val componentName: android.content.ComponentName,
    private val openSettings: (android.content.Intent) -> Unit,
) : ViewModel(), PermissionsActions {
```
- Remove the `componentNameForListener()` private function.
- Change `openRedactionSettings` to:
```kotlin
    override val openRedactionSettings: () -> Unit = {
        openSettings(redactionPath.settingsIntent(componentName))
    }
```

Now update the test in Step 1 to pass `componentName` and `openSettings`. Edit `app/src/test/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModelRedactionTest.kt` — replace the `viewModel()` factory function:

```kotlin
    private fun viewModel(
        provider: PermissionsProvider = mockk(relaxed = true),
        prefs: NotificationStatePreferences = mockk(relaxed = true),
        sender: TestNotificationSender = mockk(relaxed = true),
        path: NotificationRedactionPath = NotificationRedactionPath.StockAndroid,
        componentName: android.content.ComponentName =
            android.content.ComponentName("me.nagaev.veles", "me.nagaev.veles.otp.NotificationListener"),
        openSettings: (android.content.Intent) -> Unit = {},
    ): PermissionsViewModel = PermissionsViewModel(provider, prefs, sender, path, componentName, openSettings)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.permissions.viewmodal.PermissionsViewModelRedactionTest"`
Expected: PASS (3 tests).

- [ ] **Step 5: Update `PermissionsViewModelFactory` to wire new dependencies**

Edit `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModelFactory.kt`. Replace the `create()` function body (lines 29-50):

```kotlin
    private fun create(): PermissionsViewModel {
        val activityProvider: ActivityProvider = ActivityProviderImpl(activity)
        val accessNotificationPermissionProvider =
            AccessNotificationPermissionProvider(activityProvider)
        val sendNotificationPermissionProvider =
            SendNotificationPermissionProvider(activityProvider, requestPermissionLauncher)

        val permissionsProvider: PermissionsProvider = PermissionsProviderImpl(
            providers = mapOf(
                PermissionType.ACCESS_NOTIFICATIONS to accessNotificationPermissionProvider,
                PermissionType.SEND_NOTIFICATIONS to sendNotificationPermissionProvider
            )
        )

        val notificationStatePreferences = NotificationStatePreferences(activity)
        val testNotificationSender = TestNotificationSender(activity)
        val componentName = android.content.ComponentName(
            activity.packageName,
            "me.nagaev.veles.otp.NotificationListener",
        )
        val redactionPath = NotificationRedactionPath.from(Build.MANUFACTURER, componentName)
        val openSettings: (android.content.Intent) -> Unit = { intent -> activity.startActivity(intent) }

        return PermissionsViewModel(
            permissionsProvider,
            notificationStatePreferences,
            testNotificationSender,
            redactionPath,
            componentName,
            openSettings,
        )
    }
```

Add the imports at the top:

```kotlin
import android.os.Build
import me.nagaev.veles.otp.NotificationRedactionPath
import me.nagaev.veles.testing.TestNotificationSender
```

- [ ] **Step 6: Run build to verify it compiles**

Run: `./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 7: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/permissions/viewmodal/ app/src/test/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModelRedactionTest.kt
git commit -m "feat: expose redaction state and actions in PermissionsViewModel"
```

---

## Task 7: Redaction section UI in `PermissionsScreen` + TestTags + UI tests

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt`
- Create: `app/src/main/java/me/nagaev/veles/permissions/ui/components/RedactionSection.kt`
- Modify: `app/src/androidTest/java/me/nagaev/permissions/ui/VelesPermissionsAppTests.kt`

**Interfaces:**
- Consumes: Task 6 `PermissionsState.redactionState`/`redactionSettingsLocation`, `PermissionsActions.openRedactionSettings`/`testSensitiveReading`.
- Produces: Compose section rendering collapsed/expanded redaction UI with test tags `REDACTION_STATUS`, `REDACTION_OPEN_SETTINGS`, `REDACTION_TEST_BUTTON`.

- [ ] **Step 1: Add TestTags**

Edit `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt`. Replace the file:

```kotlin
package me.nagaev.veles.common.ui

import me.nagaev.veles.permissions.services.PermissionType

class TestTags {
    companion object {
        const val NOTIFICATION_LISTENER_STATUS = "notification_listener_status"
        const val TEST_INPUT = "test_input"
        const val TEST_SEND_BUTTON = "test_send_button"
        const val TEST_RESULT = "test_result"
        const val REDACTION_STATUS = "redaction_status"
        const val REDACTION_OPEN_SETTINGS = "redaction_open_settings"
        const val REDACTION_TEST_BUTTON = "redaction_test_button"
        val PERMISSION_STATUS = { state: PermissionType -> "permission_status_${state}" }
    }
}
```

- [ ] **Step 2: Write the failing UI test**

Append to `app/src/androidTest/java/me/nagaev/permissions/ui/VelesPermissionsAppTests.kt`. Add imports at the top:

```kotlin
import me.nagaev.veles.common.RedactionState
```

Add these tests at the end of `class VelesPermissionsAppTests`:

```kotlin
@Test
fun `redaction section shows collapsed readable status`() {
    composeTestRule.setContent {
        VelesPermissionsApp(
            permissionsState = permissionsState.copy(
                redactionState = RedactionState.Readable,
                redactionSettingsLocation = "Settings > Sensitive notifications",
            ),
            permissionsActions = permissionsActions
        )
    }
    composeTestRule
        .onNodeWithTag(TestTags.REDACTION_STATUS)
        .assertTextContains("Readable", substring = true)
}

@Test
fun `redaction section shows hidden status when Hidden`() {
    composeTestRule.setContent {
        VelesPermissionsApp(
            permissionsState = permissionsState.copy(
                redactionState = RedactionState.Hidden,
                redactionSettingsLocation = "Settings > Enhanced Notifications",
            ),
            permissionsActions = permissionsActions
        )
    }
    composeTestRule
        .onNodeWithTag(TestTags.REDACTION_STATUS)
        .assertTextContains("off", substring = true)
    composeTestRule.onNodeWithTag(TestTags.REDACTION_OPEN_SETTINGS).assertExists()
    composeTestRule.onNodeWithTag(TestTags.REDACTION_TEST_BUTTON).assertExists()
}

@Test
fun `redaction section shows unknown status initially`() {
    composeTestRule.setContent {
        VelesPermissionsApp(
            permissionsState = permissionsState.copy(
                redactionState = RedactionState.Unknown,
                redactionSettingsLocation = "",
            ),
            permissionsActions = permissionsActions
        )
    }
    composeTestRule
        .onNodeWithTag(TestTags.REDACTION_STATUS)
        .assertTextContains("Not yet checked", substring = true)
    composeTestRule.onNodeWithTag(TestTags.REDACTION_TEST_BUTTON).assertExists()
}

@Test
fun `clicking test button calls testSensitiveReading`() {
    composeTestRule.setContent {
        VelesPermissionsApp(
            permissionsState = permissionsState.copy(
                redactionState = RedactionState.Unknown,
            ),
            permissionsActions = permissionsActions
        )
    }
    composeTestRule.onNodeWithTag(TestTags.REDACTION_TEST_BUTTON).performClick()
    verify { permissionsActions.testSensitiveReading() }
}

@Test
fun `clicking open settings calls openRedactionSettings when Hidden`() {
    composeTestRule.setContent {
        VelesPermissionsApp(
            permissionsState = permissionsState.copy(
                redactionState = RedactionState.Hidden,
                redactionSettingsLocation = "Settings > Enhanced Notifications",
            ),
            permissionsActions = permissionsActions
        )
    }
    composeTestRule.onNodeWithTag(TestTags.REDACTION_OPEN_SETTINGS).performClick()
    verify { permissionsActions.openRedactionSettings() }
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.permissions.ui.VelesPermissionsAppTests`
Expected: FAIL — `onNodeWithTag(REDACTION_STATUS)` not found. (Requires device; if no device, skip to Step 4 and verify compilation.)

- [ ] **Step 4: Write minimal implementation — redaction section composable**

Create `app/src/main/java/me/nagaev/veles/permissions/ui/components/RedactionSection.kt`:

```kotlin
package me.nagaev.veles.permissions.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.ui.TestTags

@Composable
fun RedactionSection(
    state: RedactionState,
    settingsLocation: String,
    onOpenSettings: () -> Unit,
    onTestSensitiveReading: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier.fillMaxWidth().padding(horizontal = 10.dp)) {
        when (state) {
            RedactionState.Unknown -> {
                Text(
                    modifier = Modifier.testTag(TestTags.REDACTION_STATUS),
                    text = "Sensitive notification access: Not yet checked",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.secondary,
                )
                Spacer(Modifier.height(4.dp))
                TextButton(
                    onClick = onTestSensitiveReading,
                    modifier = Modifier.testTag(TestTags.REDACTION_TEST_BUTTON),
                ) { Text("Run a test") }
            }
            RedactionState.Readable -> {
                Text(
                    modifier = Modifier.testTag(TestTags.REDACTION_STATUS),
                    text = "Sensitive notification access: \u2713 Readable",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.secondary,
                )
            }
            RedactionState.Hidden -> {
                Text(
                    modifier = Modifier.testTag(TestTags.REDACTION_STATUS),
                    text = "\u26A0 Sensitive notification access is off",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.error,
                )
                Spacer(Modifier.height(4.dp))
                if (settingsLocation.isNotBlank()) {
                    Text(
                        text = "Open: $settingsLocation",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(8.dp))
                }
                Row {
                    Button(
                        onClick = onOpenSettings,
                        modifier = Modifier.testTag(TestTags.REDACTION_OPEN_SETTINGS),
                    ) { Text("Open settings") }
                    Spacer(Modifier.padding(horizontal = 4.dp))
                    TextButton(
                        onClick = onTestSensitiveReading,
                        modifier = Modifier.testTag(TestTags.REDACTION_TEST_BUTTON),
                    ) { Text("Test sensitive reading") }
                }
            }
        }
    }
}
```

- [ ] **Step 5: Render the section in `PermissionsScreen`**

Edit `app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt`. Replace the file:

```kotlin
package me.nagaev.veles.permissions.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import me.nagaev.veles.R
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.ui.components.PermissionsList
import me.nagaev.veles.permissions.ui.components.RedactionSection
import me.nagaev.veles.permissions.viewmodal.PermissionsActions
import me.nagaev.veles.permissions.viewmodal.PermissionsState

@Composable
fun PermissionsScreen(
    state: PermissionsState,
    actions: PermissionsActions,
    onNavigateToTest: () -> Unit,
    modifier: Modifier = Modifier
) {
    Box(modifier = modifier.fillMaxSize()) {
        Column {
            Text(
                modifier = Modifier.padding(10.dp).statusBarsPadding(),
                text = stringResource(id = R.string.permissions),
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.primary
            )
            Text(
                modifier = Modifier.padding(10.dp).testTag(TestTags.NOTIFICATION_LISTENER_STATUS),
                text =
                    if (state.notificationListenerEnabled)
                        "Notification listener enabled"
                    else
                        "Notification listener disabled",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.secondary
            )
            RedactionSection(
                state = state.redactionState,
                settingsLocation = state.redactionSettingsLocation,
                onOpenSettings = actions.openRedactionSettings,
                onTestSensitiveReading = actions.testSensitiveReading,
            )
            Spacer(Modifier.height(4.dp))
            TextButton(
                onClick = onNavigateToTest,
                modifier = Modifier.padding(horizontal = 10.dp)
            ) {
                Text("Test")
            }
            PermissionsList(
                permissions = state.permissions,
                actions = actions,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

@Preview(showBackground = true)
@Composable
fun PermissionsScreenPreview() {
    PermissionsScreen(
        state = PermissionsState.Mocked,
        actions = PermissionsActions.Mocked,
        onNavigateToTest = {},
    )
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.permissions.ui.VelesPermissionsAppTests`
Expected: PASS (all 10 tests — 5 existing + 5 new). If no device, run `./gradlew assembleDebug` to verify compilation.

- [ ] **Step 7: Run full unit test suite to verify no regressions**

Run: `./gradlew testDebugUnitTest`
Expected: PASS (all existing unit tests + new ones).

- [ ] **Step 8: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt app/src/main/java/me/nagaev/veles/permissions/ui/ app/src/androidTest/java/me/nagaev/permissions/ui/VelesPermissionsAppTests.kt
git commit -m "feat: add redaction section to PermissionsScreen with collapsed/expanded states"
```

---

## Verification (after all tasks)

- [ ] Run `./gradlew testDebugUnitTest` — all unit tests pass (RedactionDetector, RedactionStateFlow, NotificationRedactionPath, PermissionsViewModelRedaction, all existing).
- [ ] Run `./gradlew assembleDebug` — debug APK builds.
- [ ] (If device available) Run `./gradlew connectedDebugAndroidTest` — all instrumented tests pass (NotificationListener redaction cases, VelesPermissionsAppTests redaction-section UI).
- [ ] (Manual, on a OnePlus device with OxygenOS 15) Install the debug APK, grant notification access, post a `VISIBILITY_SECRET` test notification (or a real bank OTP), observe the redaction section expand, tap "Open settings", toggle "Enhanced Notifications" off, return, tap "Test sensitive reading", observe the section collapse to "✓ Readable".