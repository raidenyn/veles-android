# Copy PendingIntent Collision Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Copy notification action's `PendingIntent` identity match the identity Android already uses for the OTP notification itself (`notificationId = message.hashCode()`), so tapping Copy on an older OTP notification can never again silently copy a newer OTP.

**Architecture:** `UserNotifierOtpMessageHandler` already computes `notificationId = message.hashCode()` for `notify(notificationId, ...)`. Currently the Copy action's `PendingIntent` request code and `Uri` are instead built from `OtpMessage.id` (a hash of the *source* Android notification's key, not guaranteed unique across messages). Task 1 repoints both to `notificationId`. Task 2 removes the now-fully-unused `OtpMessage.id` field and its population in `RegexMessageHandler`, since nothing else references it.

**Tech Stack:** Kotlin, Android (minSdk 33), Hilt DI, JUnit4, MockK (JVM unit tests), Robolectric (`src/test`), Espresso/Compose test + real Android runtime (`src/androidTest`).

## Global Constraints

- Full spec: `docs/superpowers/specs/2026-07-09-copy-pendingintent-collision-design.md`.
- Unit test command: `./gradlew testDebugUnitTest`. Single class: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandlerTest"`.
- Do not change `TestNotificationSender`'s fixed `NOTIFICATION_ID = 99999` — out of scope per spec's Non-goals.
- Do not touch `CopyDataReceiver`, clipboard clearing, or redaction behavior — unrelated to this bug.

---

### Task 1: Repoint Copy PendingIntent identity to `notificationId`

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandler.kt:28-48`
- Test: `app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt`

**Interfaces:**
- Consumes: `OtpMessage` (unchanged in this task — still has `val id: Int` field, removed in Task 2), `UserNotifierOtpMessageHandler(context: Context)` constructor (unchanged).
- Produces: `UserNotifierOtpMessageHandler.onOtpMessageReceived(message: OtpMessage)` now builds the Copy `PendingIntent` and its `Uri` from the same `notificationId: Int` local (`message.hashCode()`) that is passed to `notify()`. No public signature changes — later tasks only rely on this behavioral guarantee, not any new symbol.

- [ ] **Step 1: Write the two failing regression tests**

Insert these two tests into `app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt`, immediately after the existing `` `Copy PendingIntent is distinct per notification and keeps its own OTP` `` test (i.e. right before the `` `Copy intent carries the notification id` `` test, around line 102):

```kotlin
    @Test
    fun `Copy PendingIntent request code matches the posted notification id`() {
        val message = defaultMessage.copy()
        val context = ApplicationProvider.getApplicationContext<Context>()
        val handler = UserNotifierOtpMessageHandler(context)
        handler.onOtpMessageReceived(message)

        val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val notification = shadowOf(notificationManager).getNotification(message.hashCode())
            ?: error("Expected a notification posted for message")

        val pendingIntent = notification.actions.first().actionIntent
        val shadowPendingIntent = shadowOf(pendingIntent)

        // Regression guard for the #10 collision: the Copy PendingIntent's request code
        // must be tied to the exact id passed to notify(), so two notifications that are
        // distinct in the tray always have distinct Copy actions, and can never fall back
        // to a value derived from the source notification's (possibly-reused) key.
        assertEquals(
            "Copy PendingIntent request code must match the id passed to notify()",
            message.hashCode(),
            shadowPendingIntent.requestCode,
        )
    }

    @Test
    fun `Copy intent data URI encodes the notification id`() {
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
            "veles://otp/${message.hashCode()}",
            savedIntent.data.toString(),
        )
    }
```

No new imports are needed — `assertEquals`, `shadowOf`, `ApplicationProvider`, `Context`, `NotificationManager` are already imported in this file.

- [ ] **Step 2: Run the tests to verify the two new ones fail**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandlerTest"`

Expected: `Copy PendingIntent request code matches the posted notification id` FAILS with an `AssertionError` (actual request code is `defaultMessage.id`, which is `1`, not `message.hashCode()`). `Copy intent data URI encodes the notification id` FAILS with an `AssertionError` comparing `veles://otp/<hash>` against the actual `veles://otp/1`. All other tests in the class still PASS.

- [ ] **Step 3: Implement the fix**

In `app/src/main/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandler.kt`, replace the `onOtpMessageReceived` body (currently lines 23-48) with:

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
                // Tied to notificationId (not the source notification's key) because the
                // source key is not guaranteed unique across messages -- see #10.
                data = Uri.parse("veles://otp/$notificationId")
                putExtra(CopyDataReceiver.EXTRA_COPY_TEXT, message.otp.value)
                putExtra(CopyDataReceiver.EXTRA_NOTIFICATION_ID, notificationId)
            }
        val copyPendingIntent: PendingIntent =
            PendingIntent.getBroadcast(
                context,
                // Same identity used for notify() below, so two notifications that are
                // distinct in the tray always have distinct Copy PendingIntents, and vice
                // versa. Do not derive this from the source notification's key/id/tag --
                // those can repeat across distinct OTP messages (see #10).
                notificationId,
                copyIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
```

The rest of the method (the `NotificationCompat.Builder` block through `notify(notificationId, builder.build())`) is unchanged.

- [ ] **Step 4: Run the tests to verify everything passes**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandlerTest"`

Expected: PASS, all tests in the class including the two new ones and the pre-existing `` `Copy PendingIntent is distinct per notification and keeps its own OTP` `` and `` `Copy intent carries the notification id` `` tests.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandler.kt app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt
git commit -m "fix: tie Copy PendingIntent identity to the posted notification id

PR #27 keyed the Copy action's PendingIntent/Uri off a hash of the
source notification's key, which repeats across distinct OTP messages
(e.g. TestNotificationSender's fixed id, or banks that update-in-place),
silently reintroducing the #10 bug where tapping Copy on an older
notification copies the newest OTP. Use notificationId (already used
for notify()) instead, so the two identities can never disagree."
```

---

### Task 2: Remove the now-unused `OtpMessage.id` field

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/OtpMessageHandler.kt:9-14`
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt:31-38`
- Modify: `app/src/test/java/me/nagaev/veles/otp/handlers/RegexMessageHandlerTest.kt:37-45,166-176`
- Modify: `app/src/test/java/me/nagaev/veles/otp/handlers/UobThaiRegexMessageHandlerTest.kt:37-45`
- Modify: `app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt:19-25,47-60`
- Modify: `app/src/androidTest/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt:22-28`

**Interfaces:**
- Consumes: The `notificationId`-based fix from Task 1 (this task does not touch `UserNotifierOtpMessageHandler`'s Copy-intent logic again, only removes an unrelated dead field it no longer reads).
- Produces: `OtpMessage(otp: Otp, pay: Money, merchant: String)` — three-argument constructor, `id` removed. Any code outside this task's file list that still constructs `OtpMessage(id = ..., ...)` will fail to compile, which is the intended safety net (grep confirmed no other call sites exist before this task starts).

This task is a mechanical, compiler-verified removal of dead code (confirmed via `grep -rn "message\.id\|OtpMessage.id"` across `app/src/{main,test,androidTest}` — the only production references are the two lines already changed in Task 1's *comments*, not code, and this task's six files are the only construction sites). There is no new behavior to drive with a RED test; the existing test suite is the safety net.

- [ ] **Step 1: Run the full unit test suite as a baseline**

Run: `./gradlew testDebugUnitTest`

Expected: PASS (this confirms Task 1 is in a clean, all-green state before starting the removal).

- [ ] **Step 2: Remove the `id` field and every call site**

In `app/src/main/java/me/nagaev/veles/otp/handlers/OtpMessageHandler.kt`, change:

```kotlin
data class OtpMessage(
    val id: Int,
    val otp: Otp,
    val pay: Money,
    val merchant: String,
)
```

to:

```kotlin
data class OtpMessage(
    val otp: Otp,
    val pay: Money,
    val merchant: String,
)
```

In `app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt`, change:

```kotlin
                OtpMessage(
                    id = message.key.hashCode(),
                    otp = otp,
                    pay = money,
                    merchant = merchant,
                ),
```

to:

```kotlin
                OtpMessage(
                    otp = otp,
                    pay = money,
                    merchant = merchant,
                ),
```

In `app/src/test/java/me/nagaev/veles/otp/handlers/RegexMessageHandlerTest.kt`, remove the line `id = defaultMessage.key.hashCode(),` from both `OtpMessage(...)` fixtures (in `` `Valid OTP message processing` `` around line 39, and in `` `Money with extreme amount values` `` around line 169), e.g. the first becomes:

```kotlin
                OtpMessage(
                    otp = Otp(value = "079853", id = "HStX"),
                    pay = Money(amount = BigDecimal("319.93"), currencyCode = "THB"),
                    merchant = "AMP*AIS SERVICES",
                ),
```

In `app/src/test/java/me/nagaev/veles/otp/handlers/UobThaiRegexMessageHandlerTest.kt`, remove the same `id = defaultMessage.key.hashCode(),` line from the one `OtpMessage(...)` fixture (around line 39):

```kotlin
                OtpMessage(
                    otp = Otp(value = "511066", id = "OTP="),
                    pay = Money(amount = BigDecimal("600.00"), currencyCode = "THB"),
                    merchant = "WWWSFCINEMACITYCOMCORP",
                ),
```

In `app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt` (the `src/test` one), remove `id = 1,` from `defaultMessage` (around line 21) and `id = 1,` / `id = 2,` from `message1` / `message2` in `` `Copy PendingIntent is distinct per notification and keeps its own OTP` `` (around lines 49 and 56). For example `defaultMessage` becomes:

```kotlin
    private val defaultMessage =
        OtpMessage(
            otp = Otp(value = "123456", id = "123"),
            pay = Money(amount = BigDecimal(100), currencyCode = "USD"),
            merchant = "Test Merchant",
        )
```

and `message1`/`message2` become:

```kotlin
        val message1 =
            OtpMessage(
                otp = Otp(value = "111111", id = "1"),
                pay = Money(amount = BigDecimal(100), currencyCode = "USD"),
                merchant = "Merchant One",
            )
        val message2 =
            OtpMessage(
                otp = Otp(value = "222222", id = "2"),
                pay = Money(amount = BigDecimal(200), currencyCode = "USD"),
                merchant = "Merchant Two",
            )
```

In `app/src/androidTest/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt`, remove `id = 1,` from `defaultMessage` (around line 24):

```kotlin
    private val defaultMessage =
        OtpMessage(
            otp = Otp(value = "123456", id = "123"),
            pay = Money(amount = BigDecimal(100), currencyCode = "USD"),
            merchant = "Test Merchant",
        )
```

- [ ] **Step 3: Run the full unit test suite again to verify the removal compiles and passes**

Run: `./gradlew testDebugUnitTest`

Expected: PASS. (A leftover `id = ...` argument anywhere would fail to compile, so a green build here proves every call site was updated.)

- [ ] **Step 4: Verify the instrumented test source compiles**

Run: `./gradlew compileDebugAndroidTestKotlin`

Expected: BUILD SUCCESSFUL. (This project's `androidTest` suite needs a connected device/emulator to actually *run* via `./gradlew connectedDebugAndroidTest` — if one is available, run that instead for full confidence; otherwise this compile-only check confirms the `id` removal didn't break `UserNotifierOtpMessageHandlerTest.kt` in `src/androidTest`.)

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/handlers/OtpMessageHandler.kt app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt app/src/test/java/me/nagaev/veles/otp/handlers/RegexMessageHandlerTest.kt app/src/test/java/me/nagaev/veles/otp/handlers/UobThaiRegexMessageHandlerTest.kt app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt app/src/androidTest/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt
git commit -m "refactor: remove unused OtpMessage.id field

Now that UserNotifierOtpMessageHandler no longer reads it (previous
commit switched the Copy PendingIntent's identity to notificationId),
OtpMessage.id -- a hash of the source notification's key -- has no
remaining readers. Drop it and the RegexMessageHandler code that
populated it."
```

---

### Manual verification (optional, requires a connected device/emulator)

Not a substitute for the automated tests above, but recommended before closing out issue #10 for good, since it's the exact scenario that motivated reopening it:

1. Install the debug build and grant notification-listener + POST_NOTIFICATIONS permissions.
2. Open the Test Screen (`permissions` → `test`).
3. Send a first test message that matches a configured bank handler (e.g. the seeded UOB Thailand config) with one OTP value.
4. Immediately send a second, different matching test message with a different OTP value, before dismissing the first OTP notification.
5. Confirm two separate OTP notifications are visible in the shade.
6. Tap "Copy" on the **first (older)** notification.
7. Paste into any text field and confirm it holds the **first** notification's OTP, not the second's.

## Self-Review Notes

- **Spec coverage:** Every spec section maps to a task — Problem/Goal → Task 1's fix; "`OtpMessage.id` not referenced anywhere outside these two lines" claim → Task 2's removal; Testing section's four bullet groups → Task 1 Step 1 (two new tests) + Task 2 Steps 1/3 (existing suite as regression net) + Manual section (the issue's own manual scenario). Non-goals (`TestNotificationSender`, clipboard) are called out in Global Constraints so no task touches them.
- **Placeholder scan:** No TBD/TODO; every step shows exact code and exact commands.
- **Type consistency:** `OtpMessage(otp, pay, merchant)` (Task 2's end state) matches every call site shown in both tasks; `notificationId: Int` naming matches its existing use for `notify()` in the untouched remainder of `onOtpMessageReceived`.
