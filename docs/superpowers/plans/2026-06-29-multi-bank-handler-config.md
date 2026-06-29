# Multi-Bank Handler Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single hardcoded `UobOtpMessageHandler` with a config-driven system that reads bank handler configurations (each with three regex patterns + metadata) from a local Room database, seeded with the existing UOB Thailand pattern on first install.

**Architecture:** A new `otp/config/` package holds a Room entity (`BankHandlerConfig`), DAO, database singleton with a seed callback, and a thin repository. At `NotificationListener` startup the repository loads all configs, maps each to a `RegexMessageHandler` (pure Kotlin, no Android), and wraps them in a `CompositeMessageHandler` that returns `ACCEPTED` on the first match.

**Tech Stack:** Room 2.6.1, KSP 2.1.10-1.0.31, MockK (existing)

## Global Constraints

- minSdk 33, compileSdk 35, Kotlin 2.1.10, AGP 8.9.0
- All dependencies declared in `gradle/libs.versions.toml` version catalog
- Unit tests (`src/test/`) use MockK; no Android runtime
- `./gradlew testDebugUnitTest` must stay green at every commit

---

### Task 1: Add Room and KSP to the build

**Files:**
- Modify: `gradle/libs.versions.toml`
- Modify: `build.gradle.kts` (root)
- Modify: `app/build.gradle.kts`

**Interfaces:**
- Produces: `libs.plugins.ksp`, `libs.androidx.room.runtime`, `libs.androidx.room.ktx`, `libs.androidx.room.compiler` — usable in app module

- [ ] **Step 1: Add versions and library entries to the version catalog**

In `gradle/libs.versions.toml`, add to the `[versions]` block:
```toml
ksp = "2.1.10-1.0.31"
room = "2.6.1"
```

Add to the `[libraries]` block:
```toml
androidx-room-runtime = { module = "androidx.room:room-runtime", version.ref = "room" }
androidx-room-ktx = { module = "androidx.room:room-ktx", version.ref = "room" }
androidx-room-compiler = { module = "androidx.room:room-compiler", version.ref = "room" }
```

Add to the `[plugins]` block:
```toml
ksp = { id = "com.google.devtools.ksp", version.ref = "ksp" }
```

- [ ] **Step 2: Declare KSP plugin in root build file**

In `build.gradle.kts` (root), add one line inside the `plugins {}` block:
```kotlin
alias(libs.plugins.ksp) apply false
```

- [ ] **Step 3: Apply KSP plugin and add Room dependencies to app module**

In `app/build.gradle.kts`, add `alias(libs.plugins.ksp)` inside the `plugins {}` block:
```kotlin
plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.compose)
    alias(libs.plugins.ksp)
}
```

In `app/build.gradle.kts`, add inside the `dependencies {}` block:
```kotlin
implementation(libs.androidx.room.runtime)
implementation(libs.androidx.room.ktx)
ksp(libs.androidx.room.compiler)
```

- [ ] **Step 4: Verify build compiles**

```
./gradlew assembleDebug
```

Expected: `BUILD SUCCESSFUL`

- [ ] **Step 5: Commit**

```bash
git add gradle/libs.versions.toml build.gradle.kts app/build.gradle.kts
git commit -m "build: add Room 2.6.1 and KSP 2.1.10-1.0.31 dependencies"
```

---

### Task 2: Create the persistence layer

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerConfig.kt`
- Create: `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerConfigDao.kt`
- Create: `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerDatabase.kt`
- Create: `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerRepository.kt`

**Interfaces:**
- Consumes: Room runtime (Task 1)
- Produces:
  - `BankHandlerConfig(id, name, otpRegex, moneyRegex, merchantRegex, createdAt, updatedAt)`
  - `BankHandlerRepository(context: Context).getAll(): List<BankHandlerConfig>`

- [ ] **Step 1: Create the Room entity**

Create `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerConfig.kt`:
```kotlin
package me.nagaev.veles.otp.config

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "bank_handler_configs")
data class BankHandlerConfig(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val name: String,
    val otpRegex: String,
    val moneyRegex: String,
    val merchantRegex: String,
    val createdAt: Long,
    val updatedAt: Long
)
```

- [ ] **Step 2: Create the DAO**

Create `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerConfigDao.kt`:
```kotlin
package me.nagaev.veles.otp.config

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query

@Dao
interface BankHandlerConfigDao {
    @Query("SELECT * FROM bank_handler_configs ORDER BY id ASC")
    fun getAll(): List<BankHandlerConfig>

    @Insert
    fun insertAll(vararg configs: BankHandlerConfig)
}
```

- [ ] **Step 3: Create the database with seed data**

Create `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerDatabase.kt`:
```kotlin
package me.nagaev.veles.otp.config

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.sqlite.db.SupportSQLiteDatabase

@Database(entities = [BankHandlerConfig::class], version = 1)
abstract class BankHandlerDatabase : RoomDatabase() {
    abstract fun bankHandlerConfigDao(): BankHandlerConfigDao

    companion object {
        @Volatile
        private var INSTANCE: BankHandlerDatabase? = null

        fun getInstance(context: Context): BankHandlerDatabase =
            INSTANCE ?: synchronized(this) {
                Room.databaseBuilder(
                    context.applicationContext,
                    BankHandlerDatabase::class.java,
                    "bank_handler_configs.db"
                )
                    .addCallback(SeedCallback())
                    .build()
                    .also { INSTANCE = it }
            }
    }

    private class SeedCallback : Callback() {
        override fun onCreate(db: SupportSQLiteDatabase) {
            super.onCreate(db)
            val now = System.currentTimeMillis()
            db.execSQL(
                "INSERT INTO bank_handler_configs (name, otpRegex, moneyRegex, merchantRegex, createdAt, updatedAt) VALUES (?, ?, ?, ?, ?, ?)",
                arrayOf(
                    "UOB Thailand",
                    """ (\w{4})-(\d{6}) """,
                    """of ([A-Z]{3})(\d{1,15}\.\d{1,4}) at""",
                    """at (.{1,64}) expiring""",
                    now,
                    now
                )
            )
        }
    }
}
```

- [ ] **Step 4: Create the repository**

Create `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerRepository.kt`:
```kotlin
package me.nagaev.veles.otp.config

import android.content.Context

class BankHandlerRepository(context: Context) {
    private val dao = BankHandlerDatabase.getInstance(context).bankHandlerConfigDao()

    fun getAll(): List<BankHandlerConfig> = dao.getAll()
}
```

- [ ] **Step 5: Verify build compiles**

```
./gradlew assembleDebug
```

Expected: `BUILD SUCCESSFUL`

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/
git commit -m "feat: add Room persistence layer for bank handler configs"
```

---

### Task 3: Create `RegexMessageHandler` and replace `UobOtpMessageHandler`

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt`
- Create: `app/src/test/java/me/nagaev/veles/otp/handlers/RegexMessageHandlerTest.kt`
- Delete: `app/src/main/java/me/nagaev/veles/otp/handlers/UobOtpMessageHandler.kt`
- Delete: `app/src/test/java/me/nagaev/veles/otp/handlers/UobOtpMessageHandlerTest.kt`

**Interfaces:**
- Consumes: `MessageHandler`, `OtpMessageHandler`, `Message`, `MessageHandlingResult`, `OtpMessage`, `Otp`, `Money` (all existing in `otp/handlers/`)
- Produces: `RegexMessageHandler(otpRegex: String, moneyRegex: String, merchantRegex: String, notifier: OtpMessageHandler) : MessageHandler`

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/me/nagaev/veles/otp/handlers/RegexMessageHandlerTest.kt`:
```kotlin
package me.nagaev.veles.otp.handlers

import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.Test
import java.math.BigDecimal

class RegexMessageHandlerTest {
    private companion object {
        const val OTP_REGEX = """ (\w{4})-(\d{6}) """
        const val MONEY_REGEX = """of ([A-Z]{3})(\d{1,15}\.\d{1,4}) at"""
        const val MERCHANT_REGEX = """at (.{1,64}) expiring"""
    }

    private val defaultMessage = Message(
        key = "123456",
        source = "line",
        title = "OTP Code",
        text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time."
    )

    private fun handler(notifier: OtpMessageHandler) =
        RegexMessageHandler(OTP_REGEX, MONEY_REGEX, MERCHANT_REGEX, notifier)

    @Test
    fun `Valid OTP message processing`() {
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(defaultMessage)

        assert(result == MessageHandlingResult.ACCEPTED)
        verify {
            otpMessageHandler.onOtpMessageReceived(
                OtpMessage(
                    id = defaultMessage.key.hashCode(),
                    otp = Otp(value = "079853", id = "HStX"),
                    pay = Money(amount = BigDecimal("319.93"), currencyCode = "THB"),
                    merchant = "AMP*AIS SERVICES"
                )
            )
        }
    }

    @Test
    fun `Message with missing OTP`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Message with missing money`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Message with missing merchant`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93 expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Message with empty text`() {
        val message = defaultMessage.copy(text = "")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `OTP with incorrect format`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX079853 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Money with incorrect format`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93. at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Merchant name exceeding max length`() {
        val longMerchantName = "A".repeat(65)
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93 at $longMerchantName expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Merchant name with special characters`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93 at AMP*AIS@SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.ACCEPTED)
        verify(exactly = 1) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Money amount with no digits before decimal`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB.1234 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Money with extreme amount values`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB999999999999999.9999 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.ACCEPTED)
        verify {
            otpMessageHandler.onOtpMessageReceived(
                OtpMessage(
                    id = defaultMessage.key.hashCode(),
                    otp = Otp(value = "079853", id = "HStX"),
                    pay = Money(amount = BigDecimal("999999999999999.9999"), currencyCode = "THB"),
                    merchant = "AMP*AIS SERVICES"
                )
            )
        }
    }

    @Test
    fun `OTP id with edge length`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HSt-079853 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `OTP value with edge length`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-07985 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Money with edge currency length`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of TH319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `OTP format variations`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX:079853 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }
}
```

- [ ] **Step 2: Run test to confirm it fails**

```
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"
```

Expected: FAIL — `RegexMessageHandler` does not exist yet.

- [ ] **Step 3: Create `RegexMessageHandler`**

Create `app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt`:
```kotlin
package me.nagaev.veles.otp.handlers

import java.math.BigDecimal

class RegexMessageHandler(
    otpRegex: String,
    moneyRegex: String,
    merchantRegex: String,
    private val notifier: OtpMessageHandler
) : MessageHandler {
    private val otpPattern = Regex(otpRegex)
    private val moneyPattern = Regex(moneyRegex)
    private val merchantPattern = Regex(merchantRegex)

    override fun onMessageReceived(message: Message): MessageHandlingResult {
        val otp = otpPattern.find(message.text)?.let {
            Otp(value = it.groupValues[2], id = it.groupValues[1])
        }
        val money = moneyPattern.find(message.text)?.let {
            Money(amount = BigDecimal(it.groupValues[2]), currencyCode = it.groupValues[1])
        }
        val merchant = merchantPattern.find(message.text)?.let {
            it.groupValues[1]
        }

        return if (otp != null && money != null && merchant != null) {
            notifier.onOtpMessageReceived(
                OtpMessage(
                    id = message.key.hashCode(),
                    otp = otp,
                    pay = money,
                    merchant = merchant
                )
            )
            MessageHandlingResult.ACCEPTED
        } else {
            MessageHandlingResult.FILTERED
        }
    }
}
```

- [ ] **Step 4: Delete the old handler and its test**

Delete `app/src/main/java/me/nagaev/veles/otp/handlers/UobOtpMessageHandler.kt`.

Delete `app/src/test/java/me/nagaev/veles/otp/handlers/UobOtpMessageHandlerTest.kt`.

- [ ] **Step 5: Run tests to confirm they pass**

```
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"
```

Expected: all 14 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt
git add app/src/test/java/me/nagaev/veles/otp/handlers/RegexMessageHandlerTest.kt
git rm app/src/main/java/me/nagaev/veles/otp/handlers/UobOtpMessageHandler.kt
git rm app/src/test/java/me/nagaev/veles/otp/handlers/UobOtpMessageHandlerTest.kt
git commit -m "feat: replace UobOtpMessageHandler with generic RegexMessageHandler"
```

---

### Task 4: Create `CompositeMessageHandler` with tests

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/handlers/CompositeMessageHandler.kt`
- Create: `app/src/test/java/me/nagaev/veles/otp/handlers/CompositeMessageHandlerTest.kt`

**Interfaces:**
- Consumes: `MessageHandler`, `Message`, `MessageHandlingResult` (existing)
- Produces: `CompositeMessageHandler(handlers: List<MessageHandler>) : MessageHandler`

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/me/nagaev/veles/otp/handlers/CompositeMessageHandlerTest.kt`:
```kotlin
package me.nagaev.veles.otp.handlers

import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.Test

class CompositeMessageHandlerTest {
    private val message = Message(key = "key", source = "source", title = "title", text = "text")

    @Test
    fun `first handler matches returns ACCEPTED and does not call second`() {
        val first = mockk<MessageHandler> {
            every { onMessageReceived(message) } returns MessageHandlingResult.ACCEPTED
        }
        val second = mockk<MessageHandler>()

        val result = CompositeMessageHandler(listOf(first, second)).onMessageReceived(message)

        assert(result == MessageHandlingResult.ACCEPTED)
        verify(exactly = 0) { second.onMessageReceived(any()) }
    }

    @Test
    fun `first filtered second matches returns ACCEPTED`() {
        val first = mockk<MessageHandler> {
            every { onMessageReceived(message) } returns MessageHandlingResult.FILTERED
        }
        val second = mockk<MessageHandler> {
            every { onMessageReceived(message) } returns MessageHandlingResult.ACCEPTED
        }

        val result = CompositeMessageHandler(listOf(first, second)).onMessageReceived(message)

        assert(result == MessageHandlingResult.ACCEPTED)
    }

    @Test
    fun `all handlers filtered returns FILTERED`() {
        val first = mockk<MessageHandler> {
            every { onMessageReceived(message) } returns MessageHandlingResult.FILTERED
        }
        val second = mockk<MessageHandler> {
            every { onMessageReceived(message) } returns MessageHandlingResult.FILTERED
        }

        val result = CompositeMessageHandler(listOf(first, second)).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
    }

    @Test
    fun `empty handler list returns FILTERED`() {
        val result = CompositeMessageHandler(emptyList()).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
    }
}
```

- [ ] **Step 2: Run test to confirm it fails**

```
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.CompositeMessageHandlerTest"
```

Expected: FAIL — `CompositeMessageHandler` does not exist yet.

- [ ] **Step 3: Create `CompositeMessageHandler`**

Create `app/src/main/java/me/nagaev/veles/otp/handlers/CompositeMessageHandler.kt`:
```kotlin
package me.nagaev.veles.otp.handlers

class CompositeMessageHandler(private val handlers: List<MessageHandler>) : MessageHandler {
    override fun onMessageReceived(message: Message): MessageHandlingResult {
        for (handler in handlers) {
            if (handler.onMessageReceived(message) == MessageHandlingResult.ACCEPTED) {
                return MessageHandlingResult.ACCEPTED
            }
        }
        return MessageHandlingResult.FILTERED
    }
}
```

- [ ] **Step 4: Run tests to confirm they pass**

```
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.CompositeMessageHandlerTest"
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/handlers/CompositeMessageHandler.kt
git add app/src/test/java/me/nagaev/veles/otp/handlers/CompositeMessageHandlerTest.kt
git commit -m "feat: add CompositeMessageHandler that tries handlers in order"
```

---

### Task 5: Wire `NotificationListener` and run the full test suite

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/NotificationListenerService.kt`

**Interfaces:**
- Consumes: `BankHandlerRepository` (Task 2), `RegexMessageHandler` (Task 3), `CompositeMessageHandler` (Task 4), `UserNotifierOtpMessageHandler` (existing)

- [ ] **Step 1: Update `NotificationListener`**

Replace the default-parameter block in `NotificationListenerService.kt`. The full updated file:
```kotlin
package me.nagaev.veles.otp

import android.content.Intent
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.core.app.NotificationCompat
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.otp.config.BankHandlerRepository
import me.nagaev.veles.otp.handlers.CompositeMessageHandler
import me.nagaev.veles.otp.handlers.Message
import me.nagaev.veles.otp.handlers.MessageHandler
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.otp.handlers.RegexMessageHandler
import me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandler

class NotificationListener(
    state: NotificationStatePreferences? = null,
    messageHandler: MessageHandler? = null
) : NotificationListenerService() {

    private val state = state ?: NotificationStatePreferences(this)
    private val messageHandler: MessageHandler = messageHandler ?: run {
        val notifier = UserNotifierOtpMessageHandler(this)
        val repository = BankHandlerRepository(this)
        val handlers = repository.getAll().map { config ->
            RegexMessageHandler(
                otpRegex = config.otpRegex,
                moneyRegex = config.moneyRegex,
                merchantRegex = config.merchantRegex,
                notifier = notifier
            )
        }
        CompositeMessageHandler(handlers)
    }

    override fun onCreate() {
        Log.d("NotificationListener", "Created")
        super.onCreate()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        Log.d("NotificationListener", "Started: $startId")
        return START_REDELIVER_INTENT
    }

    override fun onListenerConnected() {
        Log.d("NotificationListener", "ListenerConnected")
        state.saveConnectionState(true)
        super.onListenerConnected()
    }

    override fun onListenerDisconnected() {
        Log.d("NotificationListener", "ListenerDisconnected")
        state.saveConnectionState(false)
        super.onListenerConnected()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        sbn?.let {
            val packageName = it.packageName ?: ""
            val extras = it.notification?.extras
            val title = extras?.getCharSequence(NotificationCompat.EXTRA_TITLE).toString()
            val text = extras?.getCharSequence(NotificationCompat.EXTRA_TEXT).toString()

            Log.d("NotificationListener", "Title: $title, Text: $text, Package: $packageName, Timestamp: ${it.postTime}, Key: ${it.key}")

            val message = Message(
                key = it.key,
                source = packageName,
                title = title,
                text = text
            )

            val handlingResult = messageHandler.onMessageReceived(message)

            if (handlingResult == MessageHandlingResult.ACCEPTED) {
                cancelNotification(it.key)
            }
        }
    }
}
```

- [ ] **Step 2: Run the full unit test suite**

```
./gradlew testDebugUnitTest
```

Expected: all tests PASS, `BUILD SUCCESSFUL`.

- [ ] **Step 3: Verify the app builds**

```
./gradlew assembleDebug
```

Expected: `BUILD SUCCESSFUL`

- [ ] **Step 4: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/NotificationListenerService.kt
git commit -m "feat: wire NotificationListener to load handlers from Room database"
```
