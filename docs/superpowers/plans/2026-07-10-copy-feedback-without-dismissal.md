# Copy Feedback Without Dismissal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the OTP notification visible after tapping Copy and show "Copied ✓" feedback in the button label instead of dismissing the notification.

**Architecture:** Extract a shared `OtpNotificationBuilder` that builds the OTP notification (including the Copy PendingIntent with all fields as extras). Both `UserNotifierOtpMessageHandler` (initial post, `copied=false`) and `CopyDataReceiver` (re-post on copy, `copied=true`) call it. The receiver replaces `cancel(notificationId)` with `notify(notificationId, builder.build(..., copied=true))`.

**Tech Stack:** Kotlin, Android NotificationCompat, Robolectric, MockK, JUnit4

## Global Constraints

- minSdk 33; target/test with `@Config(sdk = [33])` in Robolectric tests.
- The Copy PendingIntent identity scheme must be preserved: request code = `notificationId`, data URI `veles://otp/<notificationId>`, `FLAG_UPDATE_CURRENT or FLAG_IMMUTABLE`. This is the fix for the #10 PendingIntent-collision bug.
- The `EXTRA_COPY_TEXT` and `EXTRA_NOTIFICATION_ID` constant names in `CopyDataReceiver` are already established and referenced by tests — do not rename them.
- The clipboard-clear timer (`Handler.postDelayed` + `shouldClearClip`, 2 min) is unchanged.
- `OtpNotificationBuilder` is a plain class taking `Context` — no Hilt annotations. Both callers construct it directly: `OtpNotificationBuilder(context)`.
- `CopyDataReceiver` gains a `notificationBuilderOverride` constructor parameter (nullable, defaults to `null`) for test injection, following the existing `loggerOverride` pattern.
- `CHANNEL_ID` moves from `UserNotifierOtpMessageHandler` to `OtpNotificationBuilder`. One reference in `NotificationListenerTest.kt:265` must be updated.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `app/src/main/java/me/nagaev/veles/otp/handlers/OtpNotificationBuilder.kt` | New | Single source of truth for building the OTP notification: content, Copy action (with PendingIntent + extras), channel creation. Supports `copied` flag for label. |
| `app/src/main/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandler.kt` | Modify | Delegates notification building to `OtpNotificationBuilder`; calls `notify()` behind `areNotificationsEnabled()` guard. |
| `app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt` | Modify | Re-posts notification with `copied=true` instead of cancelling. Adds `EXTRA_MERCHANT`, `EXTRA_AMOUNT_TEXT`, `EXTRA_CURRENCY_CODE` constants and `notificationBuilderOverride` param. |
| `app/src/test/java/me/nagaev/veles/otp/handlers/OtpNotificationBuilderTest.kt` | New | Tests action labels, PendingIntent identity, extras, channel idempotency. |
| `app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt` | Modify | Replaces `cancel` assertions with `notify`; adds missing-extras test. |
| `app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt` | Modify | Adds notification content-correctness test. |
| `app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt` | Modify | Updates `CHANNEL_ID` reference from `UserNotifierOtpMessageHandler` to `OtpNotificationBuilder`. |

---

### Task 1: Create OtpNotificationBuilder

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/handlers/OtpNotificationBuilder.kt`
- Test: `app/src/test/java/me/nagaev/veles/otp/handlers/OtpNotificationBuilderTest.kt`

**Interfaces:**
- Consumes: `CopyDataReceiver.EXTRA_COPY_TEXT`, `CopyDataReceiver.EXTRA_NOTIFICATION_ID` (existing constants in `me.nagaev.veles.otp.CopyDataReceiver`)
- Produces: `OtpNotificationBuilder(context: Context)` constructor; `OtpNotificationBuilder.build(notificationId: Int, merchant: String, otp: String, amountText: String, currencyCode: String, copied: Boolean): Notification`; `OtpNotificationBuilder.CHANNEL_ID: String`

- [ ] **Step 1: Write the failing tests**

Create `app/src/test/java/me/nagaev/veles/otp/handlers/OtpNotificationBuilderTest.kt`:

```kotlin
package me.nagaev.veles.otp.handlers

import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.test.core.app.ApplicationProvider
import me.nagaev.veles.otp.CopyDataReceiver
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.math.BigDecimal

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class OtpNotificationBuilderTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val notificationManager =
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    private val testMerchant = "Test Merchant"
    private val testOtp = "123456"
    private val testAmount = BigDecimal(100).toPlainString()
    private val testCurrency = "USD"
    private val testNotificationId = 42

    private fun buildNotification(copied: Boolean) =
        OtpNotificationBuilder(context).build(
            notificationId = testNotificationId,
            merchant = testMerchant,
            otp = testOtp,
            amountText = testAmount,
            currencyCode = testCurrency,
            copied = copied,
        )

    @Test
    fun `Action label is Copy OTP when copied is false`() {
        val notification = buildNotification(copied = false)

        val action = notification.actions.first()
        assertEquals("Copy $testOtp", action.title)
    }

    @Test
    fun `Action label includes Copied checkmark when copied is true`() {
        val notification = buildNotification(copied = true)

        val action = notification.actions.first()
        assertEquals("Copy $testOtp Copied ✓", action.title)
    }

    @Test
    fun `PendingIntent request code matches notification id`() {
        val notification = buildNotification(copied = false)

        val pendingIntent = notification.actions.first().actionIntent
        val shadowPendingIntent = shadowOf(pendingIntent)

        assertEquals(
            "Copy PendingIntent request code must match the notification id",
            testNotificationId,
            shadowPendingIntent.requestCode,
        )
    }

    @Test
    fun `PendingIntent data URI encodes the notification id`() {
        val notification = buildNotification(copied = false)

        val savedIntent = shadowOf(notification.actions.first().actionIntent).savedIntent

        assertEquals(
            "veles://otp/$testNotificationId",
            savedIntent.data.toString(),
        )
    }

    @Test
    fun `Intent extras carry all fields for rebuilding`() {
        val notification = buildNotification(copied = false)

        val savedIntent = shadowOf(notification.actions.first().actionIntent).savedIntent

        assertEquals(testOtp, savedIntent.getStringExtra(CopyDataReceiver.EXTRA_COPY_TEXT))
        assertEquals(
            testNotificationId,
            savedIntent.getIntExtra(CopyDataReceiver.EXTRA_NOTIFICATION_ID, -1),
        )
        assertEquals(testMerchant, savedIntent.getStringExtra(CopyDataReceiver.EXTRA_MERCHANT))
        assertEquals(testAmount, savedIntent.getStringExtra(CopyDataReceiver.EXTRA_AMOUNT_TEXT))
        assertEquals(testCurrency, savedIntent.getStringExtra(CopyDataReceiver.EXTRA_CURRENCY_CODE))
    }

    @Test
    fun `Notification content text and title are correct`() {
        val notification = buildNotification(copied = false)

        assertEquals(testMerchant, notification.extras.get(NotificationCompat.EXTRA_TITLE))
        assertEquals(
            "OTP: $testOtp, Pay: $testAmount $testCurrency",
            notification.extras.get(NotificationCompat.EXTRA_TEXT),
        )
    }

    @Test
    fun `Channel is created on first build`() {
        buildNotification(copied = false)

        val channel = notificationManager.getNotificationChannel(OtpNotificationBuilder.CHANNEL_ID)
        assertNotNull("Notification channel must be created", channel)
    }

    @Test
    fun `Channel is not recreated on second build`() {
        buildNotification(copied = false)
        val channelAfterFirst =
            notificationManager.getNotificationChannel(OtpNotificationBuilder.CHANNEL_ID)

        buildNotification(copied = false)
        val channelAfterSecond =
            notificationManager.getNotificationChannel(OtpNotificationBuilder.CHANNEL_ID)

        assertEquals(
            "Channel id should be unchanged after second build",
            channelAfterFirst.id,
            channelAfterSecond.id,
        )
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.OtpNotificationBuilderTest"`
Expected: FAIL with "Unresolved reference: OtpNotificationBuilder"

- [ ] **Step 3: Create OtpNotificationBuilder**

Create `app/src/main/java/me/nagaev/veles/otp/handlers/OtpNotificationBuilder.kt`:

```kotlin
package me.nagaev.veles.otp.handlers

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.app.NotificationCompat
import me.nagaev.veles.R
import me.nagaev.veles.otp.CopyDataReceiver

class OtpNotificationBuilder(
    private val context: Context,
) {
    companion object {
        const val CHANNEL_ID = "HandyOTPMessageChannel"
    }

    fun build(
        notificationId: Int,
        merchant: String,
        otp: String,
        amountText: String,
        currencyCode: String,
        copied: Boolean,
    ): Notification {
        tryCreateNotificationChannel()

        val copyIntent =
            Intent(context, CopyDataReceiver::class.java).apply {
                action = "Copy"
                data = Uri.parse("veles://otp/$notificationId")
                putExtra(CopyDataReceiver.EXTRA_COPY_TEXT, otp)
                putExtra(CopyDataReceiver.EXTRA_NOTIFICATION_ID, notificationId)
                putExtra(CopyDataReceiver.EXTRA_MERCHANT, merchant)
                putExtra(CopyDataReceiver.EXTRA_AMOUNT_TEXT, amountText)
                putExtra(CopyDataReceiver.EXTRA_CURRENCY_CODE, currencyCode)
            }
        val copyPendingIntent: PendingIntent =
            PendingIntent.getBroadcast(
                context,
                notificationId,
                copyIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )

        val text = "OTP: $otp, Pay: $amountText $currencyCode"
        val actionLabel = if (copied) "Copy $otp Copied ✓" else "Copy $otp"

        return NotificationCompat
            .Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_otp_message)
            .setContentTitle(merchant)
            .setContentText(text)
            .addAction(
                R.drawable.ic_otp_message,
                actionLabel,
                copyPendingIntent,
            ).setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
    }

    private fun tryCreateNotificationChannel() {
        val notificationManager =
            context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        if (notificationManager.getNotificationChannel(CHANNEL_ID) != null) {
            return
        }

        val importance = NotificationManager.IMPORTANCE_HIGH
        val channel =
            NotificationChannel(CHANNEL_ID, "Handy OTP", importance).apply {
                description = "Show handy OTP passwords from banks"
            }

        notificationManager.createNotificationChannel(channel)
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.OtpNotificationBuilderTest"`
Expected: PASS — all 8 tests green

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/handlers/OtpNotificationBuilder.kt \
        app/src/test/java/me/nagaev/veles/otp/handlers/OtpNotificationBuilderTest.kt
git commit -m "feat: extract OtpNotificationBuilder for shared notification building"
```

---

### Task 2: Refactor UserNotifierOtpMessageHandler to use OtpNotificationBuilder

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandler.kt` (full rewrite of class body)
- Modify: `app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt` (add one test)
- Modify: `app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt:25,265` (update import and CHANNEL_ID reference)

**Interfaces:**
- Consumes: `OtpNotificationBuilder(context).build(...)` from Task 1
- Produces: `UserNotifierOtpMessageHandler` still implements `OtpMessageHandler.onOtpMessageReceived(message: OtpMessage)` — no signature change. `CHANNEL_ID` is removed from this class (moved to `OtpNotificationBuilder`).

- [ ] **Step 1: Rewrite UserNotifierOtpMessageHandler**

Replace the entire contents of `app/src/main/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandler.kt` with:

```kotlin
package me.nagaev.veles.otp.handlers

import android.content.Context
import androidx.core.app.NotificationManagerCompat
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject

class UserNotifierOtpMessageHandler @Inject constructor(
    @ApplicationContext private val context: Context,
) : OtpMessageHandler {

    override fun onOtpMessageReceived(message: OtpMessage) {
        val notificationId = message.hashCode()

        with(NotificationManagerCompat.from(context)) {
            if (!areNotificationsEnabled()) return

            val notification =
                OtpNotificationBuilder(context).build(
                    notificationId = notificationId,
                    merchant = message.merchant,
                    otp = message.otp.value,
                    amountText = message.pay.amount.toPlainString(),
                    currencyCode = message.pay.currencyCode,
                    copied = false,
                )
            notify(notificationId, notification)
        }
    }
}
```

Note: `CHANNEL_ID`, `tryCreateNotificationChannel()`, and all PendingIntent/Intent/Uri/NotificationCompat imports are removed — they now live in `OtpNotificationBuilder`. The `areNotificationsEnabled()` guard now wraps the `build()` call too, preserving the original behavior where the channel is only created when notifications are enabled.

- [ ] **Step 2: Update NotificationListenerTest CHANNEL_ID reference**

In `app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt`:

Replace the import on line 25:
```kotlin
import me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandler
```
with:
```kotlin
import me.nagaev.veles.otp.handlers.OtpNotificationBuilder
```

Replace line 265:
```kotlin
        every { notification.channelId } returns UserNotifierOtpMessageHandler.CHANNEL_ID
```
with:
```kotlin
        every { notification.channelId } returns OtpNotificationBuilder.CHANNEL_ID
```

- [ ] **Step 3: Run existing tests to verify they still pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandlerTest"`
Expected: PASS — all existing tests green (PendingIntent identity, request code, data URI, notification id extras all preserved by the builder)

Also run the instrumented test to verify the CHANNEL_ID reference compiles (requires connected device/emulator, or at minimum verify compilation):
Run: `./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL

- [ ] **Step 4: Add notification content-correctness test**

In `app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt`, add this import alongside the existing imports:

```kotlin
import androidx.core.app.NotificationCompat
```

Then add this test after the existing `Valid OTP message handling` test (after line 39):

```kotlin
    @Test
    fun `Notification content text and title reflect the OtpMessage`() {
        val message = defaultMessage.copy()
        val context = ApplicationProvider.getApplicationContext<Context>()
        val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        val handler = UserNotifierOtpMessageHandler(context)
        handler.onOtpMessageReceived(message)

        val notification = shadowOf(notificationManager).getNotification(message.hashCode())
            ?: error("Expected a notification posted for message")

        assertEquals(
            "Notification title must be the merchant",
            "Test Merchant",
            notification.extras.get(NotificationCompat.EXTRA_TITLE),
        )
        assertEquals(
            "Notification text must contain OTP, amount, and currency",
            "OTP: 123456, Pay: 100 USD",
            notification.extras.get(NotificationCompat.EXTRA_TEXT),
        )
    }
```

- [ ] **Step 5: Run tests to verify the new test passes**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandlerTest"`
Expected: PASS — all tests green including the new content-correctness test

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandler.kt \
        app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt \
        app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt
git commit -m "refactor: delegate UserNotifierOtpMessageHandler to OtpNotificationBuilder"
```

---

### Task 3: Modify CopyDataReceiver to re-post instead of cancel

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt` (add constants, add override param, replace cancel with notify)
- Modify: `app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt` (add builder mock, replace cancel assertions, add missing-extras test)

**Interfaces:**
- Consumes: `OtpNotificationBuilder(context).build(...)` from Task 1
- Produces: `CopyDataReceiver.EXTRA_MERCHANT: String`, `CopyDataReceiver.EXTRA_AMOUNT_TEXT: String`, `CopyDataReceiver.EXTRA_CURRENCY_CODE: String` (new companion constants)

- [ ] **Step 1: Update CopyDataReceiverTest to expect notify instead of cancel**

Replace the entire contents of `app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt` with:

```kotlin
package me.nagaev.veles.otp

import android.app.Notification
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
import me.nagaev.veles.otp.handlers.OtpNotificationBuilder
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class CopyDataReceiverTest {
    private val context = mockk<Context>(relaxed = true)
    private val clipboardManager = mockk<ClipboardManager>(relaxed = true)
    private val intent = mockk<Intent>(relaxed = true)
    private val clipData = mockk<ClipData>(relaxed = true)
    private val clipDescription = mockk<ClipDescription>(relaxed = true)
    private val notificationManager = mockk<NotificationManagerCompat>(relaxed = true)
    private val logger = mockk<VelesLog>(relaxed = true)
    private val notificationBuilder = mockk<OtpNotificationBuilder>(relaxed = true)
    private val mockNotification = mockk<Notification>(relaxed = true)

    private val testText = "Test text"
    private val extrasSlot = slot<PersistableBundle>()

    @Before
    fun beforeTest() {
        every { context.getSystemService(Context.CLIPBOARD_SERVICE) } returns clipboardManager
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns testText
        every { intent.getIntExtra(EXTRA_NOTIFICATION_ID, -1) } returns 42
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_MERCHANT) } returns "Test Merchant"
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_AMOUNT_TEXT) } returns "100"
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_CURRENCY_CODE) } returns "USD"

        mockkStatic(ClipData::class)
        every { ClipData.newPlainText(any<String>(), any<String>()) } returns clipData
        every { clipData.description } returns clipDescription
        every { clipDescription.extras = capture(extrasSlot) } just Runs

        mockkStatic(NotificationManagerCompat::class)
        every { NotificationManagerCompat.from(context) } returns notificationManager

        every {
            notificationBuilder.build(any(), any(), any(), any(), any(), any())
        } returns mockNotification
    }

    @Test
    fun `Valid Context and Intent with text`() {
        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        verify { ClipData.newPlainText("OTP", testText) }
        verify { clipboardManager.setPrimaryClip(clipData) }
    }

    @Test
    fun `Clip is marked sensitive`() {
        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        assertTrue(
            "Copied OTP clip must be flagged EXTRA_IS_SENSITIVE",
            extrasSlot.captured.getBoolean(ClipDescription.EXTRA_IS_SENSITIVE),
        )
    }

    @Test
    fun `Notification is re-posted with copied state instead of cancelled`() {
        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        verify { notificationManager.notify(42, mockNotification) }
        verify(exactly = 0) { notificationManager.cancel(any<Int>()) }
    }

    @Test
    fun `Missing notification id skips re-post`() {
        every { intent.getIntExtra(EXTRA_NOTIFICATION_ID, -1) } returns -1

        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        verify(exactly = 0) { notificationManager.notify(any(), any()) }
    }

    @Test
    fun `Null Context`() {
        CopyDataReceiver(logger, notificationBuilder).onReceive(null, intent)
    }

    @Test
    fun `Null Intent`() {
        CopyDataReceiver(logger, notificationBuilder).onReceive(mockk(relaxed = true), null)
    }

    @Test
    fun `Missing EXTRA COPY TEXT`() {
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns null

        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        verify(exactly = 0) { clipboardManager.setPrimaryClip(any()) }
    }

    @Test
    fun `Empty EXTRA COPY TEXT`() {
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns ""

        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        verify(exactly = 1) { clipboardManager.setPrimaryClip(any()) }
    }

    @Test
    fun `Clipboard Service unavailable`() {
        every { context.getSystemService(Context.CLIPBOARD_SERVICE) } returns null

        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        verify(exactly = 0) { clipboardManager.setPrimaryClip(any()) }
    }

    @Test
    fun `Missing merchant amount and currency extras fall back to empty strings`() {
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_MERCHANT) } returns null
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_AMOUNT_TEXT) } returns null
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_CURRENCY_CODE) } returns null

        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        verify { notificationManager.notify(42, mockNotification) }
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.CopyDataReceiverTest"`
Expected: FAIL — "Notification is re-posted" test fails because the receiver still calls `cancel` instead of `notify`. Also, `CopyDataReceiver` doesn't accept a second constructor parameter yet.

- [ ] **Step 3: Modify CopyDataReceiver to re-post instead of cancel**

Replace the entire contents of `app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt` with:

```kotlin
package me.nagaev.veles.otp

import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.os.PersistableBundle
import androidx.core.app.NotificationManagerCompat
import dagger.hilt.android.EntryPointAccessors
import me.nagaev.veles.common.VelesLog
import me.nagaev.veles.otp.handlers.OtpNotificationBuilder

class CopyDataReceiver(
    private val loggerOverride: VelesLog? = null,
    private val notificationBuilderOverride: OtpNotificationBuilder? = null,
) : BroadcastReceiver() {
    companion object {
        const val EXTRA_COPY_TEXT = "CopyText"
        const val EXTRA_NOTIFICATION_ID = "NotificationId"
        const val EXTRA_MERCHANT = "Merchant"
        const val EXTRA_AMOUNT_TEXT = "AmountText"
        const val EXTRA_CURRENCY_CODE = "CurrencyCode"
        internal const val CLIP_LABEL = "OTP"
        private const val CLEAR_DELAY_MILLIS = 2 * 60 * 1000L

        internal fun shouldClearClip(clip: ClipData?, expectedText: String): Boolean {
            if (clip == null || clip.itemCount == 0) return false
            if (clip.description.label != CLIP_LABEL) return false
            return clip.getItemAt(0).text?.toString() == expectedText
        }
    }

    override fun onReceive(
        context: Context?,
        intent: Intent?,
    ) {
        if (context == null) return
        val logger = loggerOverride ?: resolveLogger(context)
        logger.d("CopyDataReceiver", "Context $context")

        val otp = intent?.getStringExtra(EXTRA_COPY_TEXT) ?: return
        val notificationId = intent.getIntExtra(EXTRA_NOTIFICATION_ID, -1)
        val merchant = intent.getStringExtra(EXTRA_MERCHANT) ?: ""
        val amountText = intent.getStringExtra(EXTRA_AMOUNT_TEXT) ?: ""
        val currencyCode = intent.getStringExtra(EXTRA_CURRENCY_CODE) ?: ""
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
            val notificationBuilder =
                notificationBuilderOverride ?: OtpNotificationBuilder(context)
            val notification = notificationBuilder.build(
                notificationId = notificationId,
                merchant = merchant,
                otp = otp,
                amountText = amountText,
                currencyCode = currencyCode,
                copied = true,
            )
            NotificationManagerCompat.from(context).notify(notificationId, notification)
        }

        Handler(Looper.getMainLooper()).postDelayed({
            if (shouldClearClip(clipboardManager.primaryClip, otp)) {
                clipboardManager.clearPrimaryClip()
            }
        }, CLEAR_DELAY_MILLIS)
    }

    private fun resolveLogger(context: Context): VelesLog = EntryPointAccessors.fromApplication(
        context.applicationContext,
        NotificationListenerEntryPoint::class.java,
    ).velesLog()
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.CopyDataReceiverTest"`
Expected: PASS — all 10 tests green

- [ ] **Step 5: Run the ShouldClearClipTest to verify it still passes**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.ShouldClearClipTest"`
Expected: PASS — `shouldClearClip` is unchanged

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt \
        app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt
git commit -m "feat: re-post notification with Copied feedback instead of dismissing"
```

---

### Task 4: Full verification

- [ ] **Step 1: Run all unit tests**

Run: `./gradlew testDebugUnitTest`
Expected: BUILD SUCCESSFUL — all tests green

- [ ] **Step 2: Run lint and detekt**

Run: `./gradlew lint detekt`
Expected: BUILD SUCCESSFUL — no new warnings or errors

- [ ] **Step 3: Build debug APK**

Run: `./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL
