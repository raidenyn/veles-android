# Clipboard Hygiene for Copied OTPs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a Copy tap, mark the OTP clip sensitive, auto-clear it from the clipboard after 2 minutes unless overwritten, and dismiss the originating notification.

**Architecture:** Extend `CopyDataReceiver.onReceive` to set `ClipDescription.EXTRA_IS_SENSITIVE`, cancel the notification via a new `EXTRA_NOTIFICATION_ID` intent extra, and schedule a `Handler.postDelayed` clear guarded by a pure `shouldClearClip` check. `UserNotifierOtpMessageHandler` passes the notification id into the copy intent.

**Tech Stack:** Kotlin, Android (minSdk 33), Hilt, MockK (JVM unit tests), Robolectric (`UserNotifierOtpMessageHandlerTest`), JUnit4.

## Global Constraints

- minSdk 33 — `ClipDescription.EXTRA_IS_SENSITIVE` and `PersistableBundle` are available unconditionally; no version gating.
- No new Gradle dependencies (no WorkManager/AlarmManager). Use `Handler(Looper.getMainLooper()).postDelayed`.
- TTL is a hardcoded constant (2 minutes), no settings UI.
- Do not log raw OTP content beyond the existing `logger.dCopiedOtp(otp)` call, which already respects `LogConfig.rawContentEnabled`.
- Clip label is the string `"OTP"` (unchanged from today). Auto-clear only if the current primary clip still matches that label AND the exact OTP text.
- Only two production files change: `otp/CopyDataReceiver.kt` and `otp/handlers/UserNotifierOtpMessageHandler.kt`.
- Run unit tests with `./gradlew testDebugUnitTest`.

---

### Task 1: Pass the notification id into the copy intent

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt` (add the `EXTRA_NOTIFICATION_ID` companion constant only)
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandler.kt:24-64`
- Test: `app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt`

**Interfaces:**
- Produces: `CopyDataReceiver.EXTRA_NOTIFICATION_ID: String = "NotificationId"` (companion constant). `UserNotifierOtpMessageHandler` puts the notification id (`message.hashCode()`) into the copy intent under this key as an `Int` extra.

- [ ] **Step 1: Add the `EXTRA_NOTIFICATION_ID` constant to `CopyDataReceiver`**

In `CopyDataReceiver.kt`, extend the companion object:

```kotlin
companion object {
    const val EXTRA_COPY_TEXT = "CopyText"
    const val EXTRA_NOTIFICATION_ID = "NotificationId"
}
```

- [ ] **Step 2: Write the failing test**

Add to `UserNotifierOtpMessageHandlerTest.kt` (Robolectric, follows the existing `savedIntent` inspection pattern used by `Copy PendingIntent is distinct per notification`):

```kotlin
@Test
fun `Copy intent carries the notification id`() {
    val message = defaultMessage.copy()
    val context = ApplicationProvider.getApplicationContext<Context>()
    val handler = UserNotifierOtpMessageHandler(context)
    handler.onOtpMessageReceived(message)

    val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    val notification = shadowOf(notificationManager).getNotification(message.hashCode())
        ?: error("Expected a notification posted for message")

    val pendingIntent = notification.actions.first().actionIntent
    val savedIntent = shadowOf(pendingIntent).savedIntent

    assertEquals(
        "Copy intent must carry the notification id used to post it",
        message.hashCode(),
        savedIntent.getIntExtra(CopyDataReceiver.EXTRA_NOTIFICATION_ID, -1),
    )
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandlerTest"`
Expected: FAIL — `getIntExtra` returns `-1` (the extra is not yet set), `assertEquals` fails.

- [ ] **Step 4: Add the extra in `UserNotifierOtpMessageHandler`**

In `onOtpMessageReceived`, hoist the id to a local val and add the extra. Replace lines 24-64 region so it reads:

```kotlin
override fun onOtpMessageReceived(message: OtpMessage) {
    val text = "OTP: ${message.otp.value}, Pay: ${message.pay.amount} ${message.pay.currencyCode}"
    val title = message.merchant
    val notificationId = message.hashCode()

    val copyIntent =
        Intent(context, CopyDataReceiver::class.java).apply {
            action = "Copy"
            // A unique data URI makes Intent.filterEquals differ per message, so even
            // if two request codes ever collide the PendingIntents stay distinct and
            // each keeps its own extras (FLAG_UPDATE_CURRENT would otherwise overwrite
            // them, making the older notification's Copy action copy the newest OTP).
            data = Uri.parse("veles://otp/${message.id}")
            putExtra(CopyDataReceiver.EXTRA_COPY_TEXT, message.otp.value)
            putExtra(CopyDataReceiver.EXTRA_NOTIFICATION_ID, notificationId)
        }
    val copyPendingIntent: PendingIntent =
        PendingIntent.getBroadcast(
            context,
            // Unique request code per notification so each notification owns its own
            // PendingIntent; otherwise FLAG_UPDATE_CURRENT would overwrite the older
            // notification's extras and "Copy" would copy the newest OTP.
            message.id,
            copyIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

    val builder =
        NotificationCompat
            .Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_otp_message)
            .setContentTitle(title)
            .setContentText(text)
            .addAction(
                R.drawable.ic_otp_message,
                "Copy ${message.otp.value}",
                copyPendingIntent,
            ).setPriority(NotificationCompat.PRIORITY_HIGH)

    with(NotificationManagerCompat.from(context)) {
        if (areNotificationsEnabled()) {
            tryCreateNotificationChannel()
            notify(notificationId, builder.build())
        }
    }
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandlerTest"`
Expected: PASS (all tests in the class, including the existing distinct-PendingIntent test which still relies on `message.hashCode()` as the id).

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt \
        app/src/main/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandler.kt \
        app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt
git commit -m "feat: pass notification id into OTP copy intent (#14)"
```

---

### Task 2: Add the `shouldClearClip` guard function

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt`
- Test: `app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt`

**Interfaces:**
- Produces: `CopyDataReceiver.shouldClearClip(clip: ClipData?, expectedText: String): Boolean` (companion, `internal`). Returns `true` only when `clip` is non-null, has at least one item, its `description.label == "OTP"`, and item 0's text equals `expectedText`.

- [ ] **Step 1: Write the failing tests**

Add a new test class `app/src/test/java/me/nagaev/veles/otp/ShouldClearClipTest.kt`:

```kotlin
package me.nagaev.veles.otp

import android.content.ClipData
import android.content.ClipDescription
import io.mockk.every
import io.mockk.mockk
import me.nagaev.veles.otp.CopyDataReceiver.Companion.shouldClearClip
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ShouldClearClipTest {
    private fun clip(label: String?, text: String?, itemCount: Int = 1): ClipData {
        val description = mockk<ClipDescription>()
        every { description.label } returns label
        val item = mockk<ClipData.Item>()
        every { item.text } returns text
        return mockk<ClipData>().apply {
            every { this@apply.description } returns description
            every { this@apply.itemCount } returns itemCount
            every { getItemAt(0) } returns item
        }
    }

    @Test
    fun `matching label and text clears`() {
        assertTrue(shouldClearClip(clip("OTP", "123456"), "123456"))
    }

    @Test
    fun `matching label but different text does not clear`() {
        assertFalse(shouldClearClip(clip("OTP", "999999"), "123456"))
    }

    @Test
    fun `different label does not clear`() {
        assertFalse(shouldClearClip(clip("Note", "123456"), "123456"))
    }

    @Test
    fun `null clip does not clear`() {
        assertFalse(shouldClearClip(null, "123456"))
    }

    @Test
    fun `empty clip does not clear`() {
        assertFalse(shouldClearClip(clip("OTP", null, itemCount = 0), "123456"))
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.ShouldClearClipTest"`
Expected: FAIL — `shouldClearClip` is unresolved (compilation error).

- [ ] **Step 3: Add `shouldClearClip` to `CopyDataReceiver`**

In `CopyDataReceiver.kt`, add the private label constant and the function to the companion object. Add the import `import android.content.ClipData`:

```kotlin
companion object {
    const val EXTRA_COPY_TEXT = "CopyText"
    const val EXTRA_NOTIFICATION_ID = "NotificationId"
    internal const val CLIP_LABEL = "OTP"
    private const val CLEAR_DELAY_MILLIS = 2 * 60 * 1000L

    internal fun shouldClearClip(clip: ClipData?, expectedText: String): Boolean {
        if (clip == null || clip.itemCount == 0) return false
        if (clip.description.label != CLIP_LABEL) return false
        return clip.getItemAt(0).text?.toString() == expectedText
    }
}
```

(`CLEAR_DELAY_MILLIS` is added now and consumed in Task 3.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.ShouldClearClipTest"`
Expected: PASS (all 5 cases).

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt \
        app/src/test/java/me/nagaev/veles/otp/ShouldClearClipTest.kt
git commit -m "feat: add shouldClearClip guard for OTP clipboard auto-clear (#14)"
```

---

### Task 3: Mark clip sensitive, cancel notification, schedule auto-clear

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt` (rewrite `onReceive`)
- Test: `app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt`

**Interfaces:**
- Consumes: `EXTRA_COPY_TEXT`, `EXTRA_NOTIFICATION_ID`, `CLIP_LABEL`, `shouldClearClip` (Tasks 1-2).
- Produces: final `onReceive` behavior — sensitive clip set, notification cancelled when id present, delayed clear scheduled.

- [ ] **Step 1: Write the failing tests**

Replace the body of `CopyDataReceiverTest.kt`. Keep the existing 6 tests' intent, add the sensitive-flag and cancel assertions. Note `intent` in the existing setup is a `mockk<Intent>(relaxed = true)`; add a stub for the new int extra and mock the sensitive-flag write with a slot:

```kotlin
package me.nagaev.veles.otp

import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.PersistableBundle
import androidx.core.app.NotificationManagerCompat
import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.mockkStatic
import io.mockk.slot
import io.mockk.verify
import me.nagaev.veles.common.VelesLog
import me.nagaev.veles.otp.CopyDataReceiver.Companion.EXTRA_COPY_TEXT
import me.nagaev.veles.otp.CopyDataReceiver.Companion.EXTRA_NOTIFICATION_ID
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class CopyDataReceiverTest {
    private val context = mockk<Context>(relaxed = true)
    private val clipboardManager = mockk<ClipboardManager>(relaxed = true)
    private val intent = mockk<Intent>(relaxed = true)
    private val clipData = mockk<ClipData>(relaxed = true)
    private val clipDescription = mockk<ClipDescription>(relaxed = true)
    private val notificationManager = mockk<NotificationManagerCompat>(relaxed = true)
    private val logger = mockk<VelesLog>(relaxed = true)

    private val testText = "Test text"
    private val extrasSlot = slot<PersistableBundle>()

    @Before
    fun beforeTest() {
        every { context.getSystemService(Context.CLIPBOARD_SERVICE) } returns clipboardManager
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns testText
        every { intent.getIntExtra(EXTRA_NOTIFICATION_ID, -1) } returns 42

        mockkStatic(ClipData::class)
        every { ClipData.newPlainText(any<String>(), any<String>()) } returns clipData
        every { clipData.description } returns clipDescription
        every { clipDescription.extras = capture(extrasSlot) } just Runs

        mockkStatic(NotificationManagerCompat::class)
        every { NotificationManagerCompat.from(context) } returns notificationManager
    }

    @Test
    fun `Valid Context and Intent with text`() {
        CopyDataReceiver(logger).onReceive(context, intent)

        verify { ClipData.newPlainText("OTP", testText) }
        verify { clipboardManager.setPrimaryClip(clipData) }
    }

    @Test
    fun `Clip is marked sensitive`() {
        CopyDataReceiver(logger).onReceive(context, intent)

        assertTrue(
            "Copied OTP clip must be flagged EXTRA_IS_SENSITIVE",
            extrasSlot.captured.getBoolean(ClipDescription.EXTRA_IS_SENSITIVE),
        )
    }

    @Test
    fun `Notification is cancelled with the provided id`() {
        CopyDataReceiver(logger).onReceive(context, intent)

        verify { notificationManager.cancel(42) }
    }

    @Test
    fun `Missing notification id skips cancel`() {
        every { intent.getIntExtra(EXTRA_NOTIFICATION_ID, -1) } returns -1

        CopyDataReceiver(logger).onReceive(context, intent)

        verify(exactly = 0) { notificationManager.cancel(any<Int>()) }
    }

    @Test
    fun `Null Context`() {
        CopyDataReceiver(logger).onReceive(null, intent)
    }

    @Test
    fun `Null Intent`() {
        CopyDataReceiver(logger).onReceive(mockk(relaxed = true), null)
    }

    @Test
    fun `Missing EXTRA COPY TEXT`() {
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns null

        CopyDataReceiver(logger).onReceive(context, intent)

        verify(exactly = 0) { clipboardManager.setPrimaryClip(any()) }
    }

    @Test
    fun `Empty EXTRA COPY TEXT`() {
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns ""

        CopyDataReceiver(logger).onReceive(context, intent)

        verify(exactly = 1) { clipboardManager.setPrimaryClip(any()) }
    }

    @Test
    fun `Clipboard Service unavailable`() {
        every { context.getSystemService(Context.CLIPBOARD_SERVICE) } returns null

        CopyDataReceiver(logger).onReceive(context, intent)

        verify(exactly = 0) { clipboardManager.setPrimaryClip(any()) }
    }
}
```

Note: the delayed `Handler.postDelayed` clear is not asserted here — a real `Handler(Looper.getMainLooper())` does nothing under plain JVM MockK (no looper runs), so `setPrimaryClip`/cancel assertions are unaffected. The clear logic itself is covered by `ShouldClearClipTest` (Task 2). The `Empty EXTRA COPY TEXT` case sets `setPrimaryClip` once because `""` is non-null.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.CopyDataReceiverTest"`
Expected: FAIL — `Clip is marked sensitive`, `Notification is cancelled with the provided id`, and `Missing notification id skips cancel` fail (extras never set, cancel never called) because `onReceive` doesn't do these yet.

- [ ] **Step 3: Rewrite `onReceive`**

Replace `onReceive` in `CopyDataReceiver.kt` and add the imports `android.content.ClipDescription`, `android.os.Handler`, `android.os.Looper`, `android.os.PersistableBundle`, `androidx.core.app.NotificationManagerCompat`:

```kotlin
override fun onReceive(
    context: Context?,
    intent: Intent?,
) {
    if (context == null) return
    val logger = loggerOverride ?: resolveLogger(context)
    logger.d("CopyDataReceiver", "Context $context")

    val otp = intent?.getStringExtra(EXTRA_COPY_TEXT) ?: return
    val notificationId = intent.getIntExtra(EXTRA_NOTIFICATION_ID, -1)
    val clipboardManager =
        context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager ?: return

    val clip = ClipData.newPlainText(CLIP_LABEL, otp).apply {
        description.extras = PersistableBundle().apply {
            putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, true)
        }
    }
    clipboardManager.setPrimaryClip(clip)
    logger.dCopiedOtp(otp)

    if (notificationId != -1) {
        NotificationManagerCompat.from(context).cancel(notificationId)
    }

    Handler(Looper.getMainLooper()).postDelayed({
        if (shouldClearClip(clipboardManager.primaryClip, otp)) {
            clipboardManager.clearPrimaryClip()
        }
    }, CLEAR_DELAY_MILLIS)
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.CopyDataReceiverTest"`
Expected: PASS (all 9 tests).

- [ ] **Step 5: Run the full unit suite**

Run: `./gradlew testDebugUnitTest`
Expected: PASS — confirms Tasks 1-3 together, no regressions elsewhere.

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt \
        app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt
git commit -m "feat: sensitive clip, auto-clear, and notification dismiss on OTP copy (#14)"
```

---

### Task 4: Manual verification on device/emulator

**Files:** none (manual QA)

- [ ] **Step 1: Build and install the debug APK**

Run: `./gradlew installDebug` (requires a connected Android 13+ device or emulator)

- [ ] **Step 2: Verify sensitive clip**

Trigger an OTP notification (use the in-app Test screen: type a UOB-format message and Send), tap Copy, then open the clipboard preview (long-press a text field / Gboard clipboard). Expected: the system shows a redacted "content copied / sensitive" indicator, not the OTP digits.

- [ ] **Step 3: Verify notification dismissal**

Tap Copy on the Veles notification. Expected: the notification disappears from the shade immediately.

- [ ] **Step 4: Verify auto-clear**

Copy an OTP, wait > 2 minutes without copying anything else, then paste into a text field. Expected: nothing pastes (clipboard cleared). Then repeat, but copy other text before the 2 minutes elapse — expected: the other text survives (not nuked).

- [ ] **Step 5: Record results**

Note pass/fail for each manual check in the PR description. No commit.

---

## Self-Review

**Spec coverage:**
- Mark clip sensitive → Task 3 (`EXTRA_IS_SENSITIVE`) + `Clip is marked sensitive` test.
- Auto-clear after TTL via Handler.postDelayed → Task 2 (`shouldClearClip`) + Task 3 (`postDelayed`, `CLEAR_DELAY_MILLIS`), guarded by label+text match.
- Auto-dismiss notification → Task 1 (`EXTRA_NOTIFICATION_ID` plumbing) + Task 3 (`cancel`) + tests.
- Unit test sensitive flag + cancel + shouldClearClip → Tasks 2 & 3.
- Manual Android 13+ checks → Task 4.
- Non-goals (no WorkManager, hardcoded TTL, two files only) → honored in Global Constraints.

**Placeholder scan:** none — all code and commands are concrete.

**Type consistency:** `EXTRA_NOTIFICATION_ID` (String key, Int value) consistent across Tasks 1 and 3; `shouldClearClip(clip: ClipData?, expectedText: String): Boolean` and `CLIP_LABEL = "OTP"` consistent across Tasks 2 and 3; `CLEAR_DELAY_MILLIS` defined in Task 2, consumed in Task 3.
