# Hot-reload bank handlers when configs change — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `NotificationListener` rebuild its handler chain whenever `bank_handler_configs` rows change, without restarting the service, and remove the synchronous main-thread Room query.

**Architecture:** Add a `Flow`-returning Room query. Introduce a pure-Kotlin `HandlerChainReloader` that collects the flow on a service-scoped coroutine and atomically swaps a rebuilt `CompositeMessageHandler` into a `@Volatile` field. `NotificationListener` reads that field on each notification. Remove the now-dead synchronous `getAll()` and `allowMainThreadQueries()`.

**Tech Stack:** Kotlin, AndroidX Room 2.6.1 (`room-ktx` already provides `Flow` support), kotlinx.coroutines, JUnit4 + MockK (unit tests), `kotlinx-coroutines-test`.

## Global Constraints

- minSdk 33; single-module Android app.
- Room version is pinned at `2.6.1` (`libs.androidx.room`); do not bump it. `Flow`-returning `@Query` is supported in this version via `room-ktx` (already a dependency).
- Unit tests run on JVM with no Android runtime (`./gradlew testDebugUnitTest`). `HandlerChainReloader` and its test must not import any `android.*` class. Use `java.util.logging.Logger` instead of `android.util.Log`.
- Existing instrumented tests (`./gradlew connectedDebugAndroidTest`) use the injected-handler constructor of `NotificationListener` and must remain green without modification.
- Commit style: lowercase prefix (`feat:`, `refactor:`, `test:`, `chore:`) matching `git log --oneline` history.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerConfigDao.kt` | Modify | Add `observeAll(): Flow<...>`; later remove `getAll()`. |
| `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerRepository.kt` | Modify | Add `observeAll()`; later remove `getAll()`. |
| `app/src/main/java/me/nagaev/veles/otp/handlers/HandlerChainReloader.kt` | Create | Pure-Kotlin reactive chain rebuild. |
| `app/src/test/java/me/nagaev/veles/otp/handlers/HandlerChainReloaderTest.kt` | Create | JVM unit test for the reloader. |
| `app/src/main/java/me/nagaev/veles/otp/NotificationListener.kt` | Modify | Use reloader; remove `lateinit`; add `onDestroy`. |
| `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerDatabase.kt` | Modify | Remove `.allowMainThreadQueries()`. |

---

### Task 1: Add `observeAll()` Flow query to DAO and repository

This task only *adds* capability; nothing consumes it yet, so the build and all existing tests stay green. `getAll()` is kept until Task 4 to avoid breaking `NotificationListener` mid-plan.

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerConfigDao.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerRepository.kt`

**Interfaces:**
- Produces: `BankHandlerConfigDao.observeAll(): Flow<List<BankHandlerConfig>>` and `BankHandlerRepository.observeAll(): Flow<List<BankHandlerConfig>>` (consumed in Task 3).

- [ ] **Step 1: Add the DAO query**

Edit `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerConfigDao.kt`. Add the `kotlinx.coroutines.flow.Flow` import and a new `observeAll` method. Keep `getAll()` for now.

The file should become:

```kotlin
package me.nagaev.veles.otp.config

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

@Dao
interface BankHandlerConfigDao {
    @Query("SELECT * FROM bank_handler_configs ORDER BY id ASC")
    fun getAll(): List<BankHandlerConfig>

    @Insert
    fun insertAll(vararg configs: BankHandlerConfig)

    @Query("SELECT * FROM bank_handler_configs ORDER BY id ASC")
    suspend fun getAllSuspend(): List<BankHandlerConfig>

    @Query("SELECT * FROM bank_handler_configs ORDER BY id ASC")
    fun observeAll(): Flow<List<BankHandlerConfig>>

    @Insert
    suspend fun insert(config: BankHandlerConfig): Long

    @Update
    suspend fun update(config: BankHandlerConfig)

    @Delete
    suspend fun delete(config: BankHandlerConfig)
}
```

- [ ] **Step 2: Add the repository passthrough**

Edit `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerRepository.kt`. Add the `Flow` import and an `observeAll()` method. Keep `getAll()` for now.

```kotlin
package me.nagaev.veles.otp.config

import android.content.Context
import kotlinx.coroutines.flow.Flow

class BankHandlerRepository(
    context: Context,
) {
    private val dao = BankHandlerDatabase.getInstance(context).bankHandlerConfigDao()

    fun getAll(): List<BankHandlerConfig> = dao.getAll()

    suspend fun getAllSuspend(): List<BankHandlerConfig> = dao.getAllSuspend()

    fun observeAll(): Flow<List<BankHandlerConfig>> = dao.observeAll()

    suspend fun insert(config: BankHandlerConfig): Long = dao.insert(config)

    suspend fun update(config: BankHandlerConfig) = dao.update(config)

    suspend fun delete(config: BankHandlerConfig) = dao.delete(config)
}
```

- [ ] **Step 3: Verify the build compiles**

Run: `./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL (Room KSP generates the `Flow`-returning impl at compile time; no runtime test needed for a one-line `@Query`).

- [ ] **Step 4: Verify existing tests still pass**

Run: `./gradlew testDebugUnitTest`
Expected: BUILD SUCCESSFUL, all existing tests green.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/BankHandlerConfigDao.kt app/src/main/java/me/nagaev/veles/otp/config/BankHandlerRepository.kt
git commit -m "feat: add observeAll() Flow query to bank handler config dao/repository"
```

---

### Task 2: Create `HandlerChainReloader` (TDD)

The core unit. Pure-Kotlin, Android-free, JVM-testable. It owns a `@Volatile var messageHandler` initialized to an empty `CompositeMessageHandler`, collects a `Flow<List<BankHandlerConfig>>` on a provided `CoroutineScope`, and rebuilds the chain on each emission. Rebuild errors are caught so a malformed config doesn't kill the collector.

**Files:**
- Create: `app/src/test/java/me/nagaev/veles/otp/handlers/HandlerChainReloaderTest.kt`
- Create: `app/src/main/java/me/nagaev/veles/otp/handlers/HandlerChainReloader.kt`

**Interfaces:**
- Consumes:
  - `me.nagaev.veles.otp.config.BankHandlerConfig` (data class, fields: `id: Long`, `name: String`, `otpRegex: String`, `moneyRegex: String`, `merchantRegex: String`, `createdAt: Long`, `updatedAt: Long`)
  - `me.nagaev.veles.otp.handlers.OtpMessageHandler` (interface, method `fun onOtpMessageReceived(message: OtpMessage)`)
  - `me.nagaev.veles.otp.handlers.RegexMessageHandler(name: String, otpRegex: String, moneyRegex: String, merchantRegex: String, notifier: OtpMessageHandler): MessageHandler` — **eagerly compiles `Regex(otpRegex)` in its constructor**, so an invalid regex throws `java.util.regex.PatternSyntaxException` at construction time.
  - `me.nagaev.veles.otp.handlers.CompositeMessageHandler(handlers: List<MessageHandler>): MessageHandler` — returns `MessageHandlingResult.FILTERED` for an empty list.
  - `me.nagaev.veles.otp.handlers.Message` (data class: `key: String`, `source: String`, `title: String`, `text: String`)
  - `me.nagaev.veles.otp.handlers.MessageHandlingResult` (data class with `Status.ACCEPTED`/`FILTERED` and `matchedTemplateName: String?`; companion `ACCEPTED`/`FILTERED`)
- Produces:
  - `HandlerChainReloader(configs: Flow<List<BankHandlerConfig>>, notifier: OtpMessageHandler)` — constructor.
  - `@Volatile var messageHandler: MessageHandler` — public read field, initialized to `CompositeMessageHandler(emptyList())`.
  - `fun start(scope: CoroutineScope): Unit` — launches collection on `scope`; uses `scope`'s coroutine context (so tests pass a `TestScope` with a test dispatcher).
  - `fun stop(): Unit` — cancels the collector job.

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/me/nagaev/veles/otp/handlers/HandlerChainReloaderTest.kt`:

```kotlin
package me.nagaev.veles.otp.handlers

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import me.nagaev.veles.otp.config.BankHandlerConfig
import org.junit.Assert.assertEquals
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class HandlerChainReloaderTest {

    private val notifier = object : OtpMessageHandler {
        override fun onOtpMessageReceived(message: OtpMessage) = Unit
    }

    private fun config(name: String, otp: String, money: String, merchant: String) =
        BankHandlerConfig(
            id = 0L,
            name = name,
            otpRegex = otp,
            moneyRegex = money,
            merchantRegex = merchant,
            createdAt = 0L,
            updatedAt = 0L,
        )

    private val bankA = config("BankA", """ SMS-(\d{6}) """, """THB(\d+\.\d{2})""", """at (\w+)""")
    private val bankB = config("BankB", """ CODE-(\d{4}) """, """USD(\d+\.\d{2})""", """at (\w+)""")

    private val msgA = Message("k", "src", "t", "Use SMS-123456 pay THB100.00 at SHOP")
    private val msgB = Message("k", "src", "t", "Use CODE-4321 pay USD50.00 at STORE")

    private fun reloader(flow: kotlinx.coroutines.flow.Flow<List<BankHandlerConfig>>) =
        HandlerChainReloader(flow, notifier)

    @Test
    fun `messageHandler defaults to an empty composite that filters everything before start`() {
        val r = reloader(MutableSharedFlow()) // never emits
        val result = r.messageHandler.onMessageReceived(msgA)
        assertEquals(MessageHandlingResult.FILTERED, result)
    }

    @Test
    fun `no emission yet - started collector keeps the empty default that filters everything`() = runTest(UnconfinedTestDispatcher()) {
        val neverEmits = MutableSharedFlow<List<BankHandlerConfig>>() // no replay, no initial value
        val r = reloader(neverEmits)
        r.start(this)
        try {
            assertEquals(MessageHandlingResult.FILTERED, r.messageHandler.onMessageReceived(msgA))
        } finally {
            r.stop()
        }
    }

    @Test
    fun `initial emission builds a chain that accepts a matching message`() = runTest(UnconfinedTestDispatcher()) {
        val flow = MutableStateFlow(listOf(bankA))
        val r = reloader(flow)
        r.start(this)
        try {
            val result = r.messageHandler.onMessageReceived(msgA)
            assertEquals(MessageHandlingResult.Status.ACCEPTED, result.status)
            assertEquals("BankA", result.matchedTemplateName)
        } finally {
            r.stop()
        }
    }

    @Test
    fun `changed config list is picked up - old message no longer matches, new one does`() = runTest(UnconfinedTestDispatcher()) {
        val flow = MutableStateFlow(listOf(bankA))
        val r = reloader(flow)
        r.start(this)
        try {
            // Before change: A matches, B does not.
            assertEquals(MessageHandlingResult.Status.ACCEPTED, r.messageHandler.onMessageReceived(msgA).status)
            assertEquals(MessageHandlingResult.Status.FILTERED, r.messageHandler.onMessageReceived(msgB).status)

            // Swap to BankB.
            flow.value = listOf(bankB)

            // After change: A no longer matches, B does.
            assertEquals(MessageHandlingResult.Status.FILTERED, r.messageHandler.onMessageReceived(msgA).status)
            assertEquals(MessageHandlingResult.Status.ACCEPTED, r.messageHandler.onMessageReceived(msgB).status)
            assertEquals("BankB", r.messageHandler.onMessageReceived(msgB).matchedTemplateName)
        } finally {
            r.stop()
        }
    }

    @Test
    fun `empty emission produces a chain that filters everything`() = runTest(UnconfinedTestDispatcher()) {
        val flow = MutableStateFlow(listOf(bankA))
        val r = reloader(flow)
        r.start(this)
        try {
            flow.value = emptyList()
            assertEquals(MessageHandlingResult.FILTERED, r.messageHandler.onMessageReceived(msgA))
        } finally {
            r.stop()
        }
    }

    @Test
    fun `malformed config does not kill the collector and a later fix takes effect`() = runTest(UnconfinedTestDispatcher()) {
        val flow = MutableStateFlow(listOf(bankA))
        val r = reloader(flow)
        r.start(this)
        try {
            assertEquals(MessageHandlingResult.Status.ACCEPTED, r.messageHandler.onMessageReceived(msgA).status)

            // Emit a config with an invalid regex; RegexMessageHandler construction throws, the
            // collector catches it and keeps the previous (BankA) chain.
            flow.value = listOf(config("Bad", "[", "x", "y"))
            assertEquals(
                "previous chain should still be active after a failed rebuild",
                MessageHandlingResult.Status.ACCEPTED,
                r.messageHandler.onMessageReceived(msgA).status,
            )

            // Emit a valid config; the collector recovers and the new chain takes effect.
            flow.value = listOf(bankB)
            assertEquals(MessageHandlingResult.Status.ACCEPTED, r.messageHandler.onMessageReceived(msgB).status)
            assertEquals("BankB", r.messageHandler.onMessageReceived(msgB).matchedTemplateName)
        } finally {
            r.stop()
        }
    }
}
```

Notes:
- `runTest(UnconfinedTestDispatcher())` makes the test dispatcher unconfined, so `scope.launch` inside `start()` runs its collector eagerly — each `MutableStateFlow` emission is processed before the next statement in the test body executes. Without this, the default `StandardTestDispatcher` would queue the collector and `r.messageHandler` would still read the empty default, causing false failures. This matches the codebase's existing pattern (`BankConfigsViewModelTest` uses `UnconfinedTestDispatcher` + `Dispatchers.setMain`).
- Each test wraps its assertions in `try { ... } finally { r.stop() }` to cancel the collector before `runTest` finishes, so no suspended `collect` coroutine is left dangling. The `MutableSharedFlow` test (no emission) exercises the spec's "call start, emit nothing" case: the collector suspends waiting for an emission that never comes, and `messageHandler` stays at its empty-composite default.

- [ ] **Step 2: Run the test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.HandlerChainReloaderTest"`
Expected: FAIL with a compile error — `HandlerChainReloader` is not defined (unresolved reference).

- [ ] **Step 3: Write the minimal implementation**

Create `app/src/main/java/me/nagaev/veles/otp/handlers/HandlerChainReloader.kt`:

```kotlin
package me.nagaev.veles.otp.handlers

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.launch
import me.nagaev.veles.otp.config.BankHandlerConfig
import java.util.logging.Level
import java.util.logging.Logger

class HandlerChainReloader(
    private val configs: Flow<List<BankHandlerConfig>>,
    private val notifier: OtpMessageHandler,
) {
    @Volatile
    var messageHandler: MessageHandler = CompositeMessageHandler(emptyList())
        private set

    private var job: Job? = null

    fun start(scope: CoroutineScope) {
        job = scope.launch {
            configs.collect { list ->
                try {
                    messageHandler = CompositeMessageHandler(
                        list.map { config ->
                            RegexMessageHandler(
                                name = config.name,
                                otpRegex = config.otpRegex,
                                moneyRegex = config.moneyRegex,
                                merchantRegex = config.merchantRegex,
                                notifier = notifier,
                            )
                        },
                    )
                } catch (e: Throwable) {
                    Logger.getLogger("HandlerChainReloader")
                        .log(Level.WARNING, "Failed to rebuild handler chain", e)
                }
            }
        }
    }

    fun stop() {
        job?.cancel()
    }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.HandlerChainReloaderTest"`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the full unit test suite**

Run: `./gradlew testDebugUnitTest`
Expected: BUILD SUCCESSFUL, all tests green (no regressions).

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/handlers/HandlerChainReloader.kt app/src/test/java/me/nagaev/veles/otp/handlers/HandlerChainReloaderTest.kt
git commit -m "feat: add HandlerChainReloader for reactive bank handler chain rebuilds"
```

---

### Task 3: Wire `NotificationListener` to `HandlerChainReloader`

Replace the one-shot `getAll()`-based construction with a service-scoped `CoroutineScope` and a `HandlerChainReloader` started in `onCreate` and stopped in `onDestroy`. The injected-handler constructor path is untouched, so all existing instrumented tests pass as-is.

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/NotificationListener.kt`

**Interfaces:**
- Consumes:
  - `HandlerChainReloader(configs: Flow<List<BankHandlerConfig>>, notifier: OtpMessageHandler)` with `start(scope: CoroutineScope)` and `stop()`, `@Volatile var messageHandler: MessageHandler` (from Task 2).
  - `BankHandlerRepository.observeAll(): Flow<List<BankHandlerConfig>>` (from Task 1).
  - `UserNotifierOtpMessageHandler(context: Context): OtpMessageHandler` (existing).
- Produces: `NotificationListener` whose production (non-injected) path rebuilds its chain on config changes; injected path unchanged.

- [ ] **Step 1: Update `NotificationListener`**

Edit `app/src/main/java/me/nagaev/veles/otp/NotificationListener.kt`. Replace the file body so that:
- `lateinit var messageHandler` is removed.
- New fields: `private var serviceScope: CoroutineScope? = null`, `private var reloader: HandlerChainReloader? = null`.
- `onCreate` non-injected branch builds the scope + repository + notifier + reloader and calls `start`.
- A private `activeHandler()` resolves the handler at notification time.
- `onDestroy` stops the reloader and cancels the scope.

The full file becomes:

```kotlin
package me.nagaev.veles.otp

import android.app.Notification
import android.content.Intent
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.common.TestResult
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.otp.config.BankHandlerRepository
import me.nagaev.veles.otp.handlers.CompositeMessageHandler
import me.nagaev.veles.otp.handlers.HandlerChainReloader
import me.nagaev.veles.otp.handlers.Message
import me.nagaev.veles.otp.handlers.MessageHandler
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandler
import me.nagaev.veles.testing.TestNotificationSender

class NotificationListener(
    state: NotificationStatePreferences? = null,
    messageHandler: MessageHandler? = null,
    private val ownPackageName: String? = null,
) : NotificationListenerService() {
    private val state = state ?: NotificationStatePreferences(this)
    private val injectedHandler: MessageHandler? = messageHandler
    private var serviceScope: CoroutineScope? = null
    private var reloader: HandlerChainReloader? = null

    override fun onCreate() {
        super.onCreate()
        Log.d("NotificationListener", "Created")
        if (injectedHandler == null) {
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
            serviceScope = scope
            val repository = BankHandlerRepository(this)
            val notifier = UserNotifierOtpMessageHandler(this)
            val r = HandlerChainReloader(repository.observeAll(), notifier)
            r.start(scope)
            reloader = r
        }
    }

    override fun onDestroy() {
        reloader?.stop()
        serviceScope?.cancel()
        serviceScope = null
        reloader = null
        super.onDestroy()
    }

    override fun onStartCommand(
        intent: Intent?,
        flags: Int,
        startId: Int,
    ): Int {
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
        super.onListenerDisconnected()
    }

    private fun activeHandler(): MessageHandler =
        injectedHandler ?: reloader?.messageHandler ?: CompositeMessageHandler(emptyList())

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        sbn?.let {
            val packageName = it.packageName ?: ""
            val extras = it.notification?.extras
            val title = extras?.getCharSequence(NotificationCompat.EXTRA_TITLE).toString()
            val text = extras?.getCharSequence(NotificationCompat.EXTRA_TEXT).toString()

            Log.d("NotificationListener", "Title: $title, Text: $text, Package: $packageName, Timestamp: ${it.postTime}, Key: ${it.key}")

            val notification = it.notification
            if (RedactionDetector.isRedacted(it)) {
                RedactionStateFlow.current.value = RedactionState.Hidden
            } else if (notification?.visibility == Notification.VISIBILITY_SECRET &&
                RedactionStateFlow.current.value == RedactionState.Hidden
            ) {
                RedactionStateFlow.current.value = RedactionState.Readable
            }

            val message =
                Message(
                    key = it.key,
                    source = packageName,
                    title = title,
                    text = text,
                )

            val handlingResult = activeHandler().onMessageReceived(message)

            val effectiveOwnPackage = ownPackageName ?: getPackageName()
            val channelId = it.notification?.channelId
            if (message.source == effectiveOwnPackage && channelId == TestNotificationSender.CHANNEL_ID) {
                TestResultFlow.current.value = TestResult(
                    handlingResult = handlingResult,
                    receivedText = message.text,
                    receivedTitle = message.title,
                    sourcePackage = message.source,
                    timestamp = System.currentTimeMillis(),
                )
            }

            if (handlingResult.status == MessageHandlingResult.Status.ACCEPTED) {
                cancelNotification(it.key)
            }
        }
    }
}
```

- [ ] **Step 2: Verify the debug build compiles**

Run: `./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL. (The old `messageHandler` field is gone; `onNotificationPosted` now calls `activeHandler()`.)

- [ ] **Step 3: Verify existing instrumented `NotificationListenerTest` still compiles and passes**

The tests construct `NotificationListener(state, messageHandler, ownPackageName = ...)` and call `onCreate` + `onNotificationPosted`. With an injected handler, `activeHandler()` returns it directly; no scope/reloader is created. No test edits are needed.

Run: `./gradlew connectedDebugAndroidTest --tests "me.nagaev.veles.otp.NotificationListenerTest"`
Expected: BUILD SUCCESSFUL, all `NotificationListenerTest` cases green. (Requires a connected device/emulator. If no device is available in the executing environment, run `./gradlew testDebugUnitTest` to confirm at least the JVM suite is green and skip the instrumented run with a note.)

- [ ] **Step 4: Run the full unit test suite**

Run: `./gradlew testDebugUnitTest`
Expected: BUILD SUCCESSFUL, all tests green.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/NotificationListener.kt
git commit -m "feat: hot-reload bank handlers in NotificationListener via HandlerChainReloader"
```

---

### Task 4: Remove dead synchronous `getAll()` and `allowMainThreadQueries()`

After Task 3, nothing calls `getAll()` and no synchronous DAO method remains. Remove both so the main-thread-query footgun can't return.

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerConfigDao.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerRepository.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerDatabase.kt`

**Interfaces:**
- Consumes: confirmation (via grep) that `getAll()` has no remaining callers.
- Produces: no public API change beyond removing `getAll()`.

- [ ] **Step 1: Confirm `getAll()` has no remaining callers**

Run: `rg --no-heading "getAll\(\)" app/src`
Expected: only the definition lines in the DAO and repository remain (no call sites). The instrumented test file `ExportImportFlowTest.kt` uses `getAllSuspend()`, not `getAll()`. If any caller remains, stop and fix before proceeding.

- [ ] **Step 2: Remove `getAll()` from the DAO**

Edit `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerConfigDao.kt`. Remove the `fun getAll(): List<BankHandlerConfig>` method and its `@Query`. The file becomes:

```kotlin
package me.nagaev.veles.otp.config

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

@Dao
interface BankHandlerConfigDao {
    @Insert
    fun insertAll(vararg configs: BankHandlerConfig)

    @Query("SELECT * FROM bank_handler_configs ORDER BY id ASC")
    suspend fun getAllSuspend(): List<BankHandlerConfig>

    @Query("SELECT * FROM bank_handler_configs ORDER BY id ASC")
    fun observeAll(): Flow<List<BankHandlerConfig>>

    @Insert
    suspend fun insert(config: BankHandlerConfig): Long

    @Update
    suspend fun update(config: BankHandlerConfig)

    @Delete
    suspend fun delete(config: BankHandlerConfig)
}
```

- [ ] **Step 3: Remove `getAll()` from the repository**

Edit `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerRepository.kt`. Remove the `fun getAll()` line. The file becomes:

```kotlin
package me.nagaev.veles.otp.config

import android.content.Context
import kotlinx.coroutines.flow.Flow

class BankHandlerRepository(
    context: Context,
) {
    private val dao = BankHandlerDatabase.getInstance(context).bankHandlerConfigDao()

    suspend fun getAllSuspend(): List<BankHandlerConfig> = dao.getAllSuspend()

    fun observeAll(): Flow<List<BankHandlerConfig>> = dao.observeAll()

    suspend fun insert(config: BankHandlerConfig): Long = dao.insert(config)

    suspend fun update(config: BankHandlerConfig) = dao.update(config)

    suspend fun delete(config: BankHandlerConfig) = dao.delete(config)
}
```

- [ ] **Step 4: Remove `allowMainThreadQueries()` from the database builder**

Edit `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerDatabase.kt`. In the `getInstance` builder chain, delete the `.allowMainThreadQueries()` line. The builder call becomes:

```kotlin
        fun getInstance(context: Context): BankHandlerDatabase = INSTANCE ?: synchronized(this) {
            Room
                .databaseBuilder(
                    context.applicationContext,
                    BankHandlerDatabase::class.java,
                    "bank_handler_configs.db",
                )
                .addMigrations(MIGRATION_1_2)
                .addCallback(SeedCallback())
                .build()
                .also { INSTANCE = it }
        }
```

- [ ] **Step 5: Verify the build compiles**

Run: `./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL. If it fails with a Room/main-thread error from a remaining synchronous caller, that caller must be converted to `getAllSuspend()`/`observeAll()` — but per Step 1's grep there are none.

- [ ] **Step 6: Verify all unit tests pass**

Run: `./gradlew testDebugUnitTest`
Expected: BUILD SUCCESSFUL, all tests green.

- [ ] **Step 7: Verify instrumented tests still compile and pass**

Run: `./gradlew connectedDebugAndroidTest`
Expected: BUILD SUCCESSFUL. (If no device is available, run `./gradlew compileDebugAndroidTestKotlin` to at least confirm the instrumented sources compile, and note the runtime run was skipped.)

- [ ] **Step 8: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/BankHandlerConfigDao.kt app/src/main/java/me/nagaev/veles/otp/config/BankHandlerRepository.kt app/src/main/java/me/nagaev/veles/otp/config/BankHandlerDatabase.kt
git commit -m "refactor: remove synchronous getAll() and allowMainThreadQueries"
```

---

## Verification (whole-plan gate)

After all four tasks:

- [ ] `./gradlew testDebugUnitTest` — green, including the new `HandlerChainReloaderTest`.
- [ ] `./gradlew assembleDebug` — builds.
- [ ] (If a device/emulator is available) `./gradlew connectedDebugAndroidTest` — green, `NotificationListenerTest` unchanged.
- [ ] `rg "allowMainThreadQueries" app/src` — no matches.
- [ ] `rg "\bgetAll\(\)" app/src` — no matches (only `getAllSuspend` and `observeAll` remain).
