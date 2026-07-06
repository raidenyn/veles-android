# Hot-reload bank handlers when configs change

**Issue:** [raidenyn/veles-android#12](https://github.com/raidenyn/veles-android/issues/12)
**Type:** Technical improvement (correctness/architecture)
**Priority:** High
**Effort:** Medium (~0.5–1 day)

## Problem

`NotificationListener.onCreate` builds the handler chain once via a synchronous
`repository.getAll()` call (`app/src/main/java/me/nagaev/veles/otp/NotificationListener.kt:35-49`).
`NotificationListenerService` is a long-lived process that the system keeps alive as long as
notification access is granted — often days. Any config added, edited, or deleted through the Bank
Config CRUD UI is invisible to the listener until the service is recreated (reboot, toggling
notification access). Users edit a regex, test it, and see stale behavior with no indication why.
The Test Screen also lies: it exercises the *old* chain.

A secondary problem: `repository.getAll()` is a synchronous Room query on the main thread, enabled
by `allowMainThreadQueries()` on the database builder
(`BankHandlerDatabase.kt:43`). It works today but blocks the main thread of the listener process
and normalizes a pattern that will hurt as the table grows.

## Goal

Config changes (insert/edit/delete) take effect in the running `NotificationListener` without
requiring a service restart, and the synchronous main-thread Room query is removed.

## Non-goals

- Migrating `BankConfigsViewModel` or `BankConfigEditViewModel` from `getAllSuspend()` to
  `observeAll()`. They work fine and are unrelated to the listener staleness bug.
- UI indication that the chain reloaded.
- Throttling/debouncing of emissions (Room coalesces rapid writes already; not needed for this
  table size).

## Architecture

A new pure-Kotlin `HandlerChainReloader` owns the reactive rebuild of the handler chain.
`NotificationListener` creates one in `onCreate` (when no handler is injected), starts it on a
service-scoped coroutine scope, and reads `reloader.messageHandler` on each notification. The
injected-handler test path is unchanged.

```
Room table change
  → BankHandlerConfigDao.observeAll() emits new List<BankHandlerConfig>
  → HandlerChainReloader collector (Dispatchers.Default) rebuilds CompositeMessageHandler
  → @Volatile swap of messageHandler
  → next onNotificationPosted reads the new chain
```

## Components

### `BankHandlerConfigDao`

Add:

```kotlin
@Query("SELECT * FROM bank_handler_configs ORDER BY id ASC")
fun observeAll(): Flow<List<BankHandlerConfig>>
```

Remove `getAll()` (only caller is `NotificationListener`, removed by this change). Keep
`getAllSuspend()` (ViewModels still use it).

### `BankHandlerRepository`

Add:

```kotlin
fun observeAll(): Flow<List<BankHandlerConfig>> = dao.observeAll()
```

Remove `getAll()`. Keep `getAllSuspend()`, `insert`, `update`, `delete`.

### `BankHandlerDatabase`

Remove `.allowMainThreadQueries()` from the `Room.databaseBuilder(...)` chain
(`BankHandlerDatabase.kt:43`). After this change there are no synchronous DAO callers left.

### `HandlerChainReloader` (new)

Package: `me.nagaev.veles.otp.handlers`.

A pure-Kotlin class (no Android dependencies) that owns Flow collection and chain rebuild, so the
reload behavior gets a fast JVM unit test without the Android runtime.

Responsibilities:
- Hold a `@Volatile var messageHandler: MessageHandler`, initialized to an empty
  `CompositeMessageHandler` that returns `FILTERED` for all messages. This means notifications
  arriving before the first DB emission are safely no-op'd rather than crashing on a `lateinit`
  access — a real improvement over the current code's `lateinit var`.
- `start(scope: CoroutineScope)`: launch a collector on the supplied scope that, for each emission,
  rebuilds a `CompositeMessageHandler` from the emitted configs and swaps it into
  `messageHandler`.
- `stop()`: cancel the collector job.

Constructor parameters:
- `configs: Flow<List<BankHandlerConfig>>` — the reactive config source.
- `notifier: OtpMessageHandler` — the notifier handed to each `RegexMessageHandler`. The
  `OtpMessageHandler` interface already exists (`app/src/main/java/me/nagaev/veles/otp/handlers/OtpMessageHandler.kt`)
  and `UserNotifierOtpMessageHandler` already implements it, so no new abstraction is required.
  The reloader depends only on the interface, keeping it Android-free and unit-testable with a
  fake `OtpMessageHandler`.

Sketch:

```kotlin
class HandlerChainReloader(
    private val configs: Flow<List<BankHandlerConfig>>,
    private val notifier: OtpMessageHandler,
) {
    @Volatile
    var messageHandler: MessageHandler = CompositeMessageHandler(emptyList())

    private val logger = java.util.logging.Logger.getLogger("HandlerChainReloader")

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
                        }
                    )
                } catch (e: Throwable) {
                    // Keep the previous messageHandler; a single malformed config must not
                    // permanently freeze the chain by killing the collector.
                    logger.log(Level.WARNING, "Failed to rebuild handler chain", e)
                }
            }
        }
    }

    fun stop() { job?.cancel() }
}
```

### `NotificationListener`

- Remove the `lateinit var messageHandler: MessageHandler` field.
- Add `private var serviceScope: CoroutineScope? = null` and `private var reloader: HandlerChainReloader? = null`.
- In `onCreate`, the `injectedHandler == null` branch:
  1. Create `serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)`.
  2. Build `BankHandlerRepository(this)`.
  3. Build a `UserNotifierOtpMessageHandler(this)` as the notifier.
  4. Construct `HandlerChainReloader(repository.observeAll(), notifier)`.
  5. Call `reloader.start(serviceScope)`.
  6. Do **not** assign any `messageHandler` field — `onNotificationPosted` resolves the active
     handler dynamically (see below).
- Add `onDestroy`:
  ```kotlin
  override fun onDestroy() {
      reloader?.stop()
      serviceScope?.cancel()
      serviceScope = null
      reloader = null
      super.onDestroy()
  }
  ```
- Replace the `messageHandler.onMessageReceived(message)` call in `onNotificationPosted` with a
  resolution via:
  ```kotlin
  private fun activeHandler(): MessageHandler =
      injectedHandler ?: reloader?.messageHandler ?: CompositeMessageHandler(emptyList())
  ```
  The final `CompositeMessageHandler(emptyList())` fallback covers the edge case where a
  notification is posted before `onCreate` completes — defensive, should not normally happen, but
  avoids any `lateinit`/NPE hazard.
- The injected-handler constructor path (`injectedHandler != null`) is unchanged: `activeHandler()`
  returns it directly, and no scope/reloader is created. Existing instrumented tests pass
  unchanged.

## Data flow

1. A config is inserted/updated/deleted in the `bank_handler_configs` table (via the CRUD UI or
   import flow).
2. Room's `Flow` support detects the table change and `BankHandlerConfigDao.observeAll()` emits a
   new `List<BankHandlerConfig>`.
3. `HandlerChainReloader`'s collector (running on `Dispatchers.Default`) builds a new
   `CompositeMessageHandler` and atomically swaps it into `@Volatile var messageHandler`.
4. The next `onNotificationPosted` call reads `reloader.messageHandler` and dispatches through the
   new chain.

## Error handling

- **Rebuild failure:** the collector wraps each rebuild in a `try`/catch. On failure (e.g. a
  malformed regex compiled inside `RegexMessageHandler`), the previous `messageHandler` is kept
  and the error is logged. The collector survives, so a subsequent edit that fixes the config
  takes effect. Without this guard, a single bad config would kill the collector and permanently
  freeze the chain at its last-good state.
- **Empty config list:** produces an empty `CompositeMessageHandler` that returns `FILTERED` for
  all messages — originals stay visible. Correct behavior (no handler matched, don't cancel).
- **Notification posted before first emission:** `messageHandler` is the empty composite default,
  so the message is `FILTERED` and the original stays visible. No crash.
- **Scope cancellation (`onDestroy`):** `stop()` cancels the collector; `serviceScope.cancel()`
  cancels any in-flight coroutine work. No leaks.

## Testing

### `HandlerChainReloaderTest` (new, JVM — `src/test/`)

Uses `MutableStateFlow<List<BankHandlerConfig>>`, a fake `OtpMessageHandler`, and `TestScope`/`runTest`.

Cases:
1. **Initial emission builds a working chain.** Emit a config list with one bank; assert
   `reloader.messageHandler.onMessageReceived(matchingMessage)` returns `ACCEPTED` with the
   config's name.
2. **Changed config list is picked up.** After case 1, emit a *different* config (different OTP
   regex). Assert a message that matched the *old* regex now returns `FILTERED`, and a message
   matching the *new* regex returns `ACCEPTED` with the new config's name. This is the core
   hot-reload assertion.
3. **Empty emission → `FILTERED` for everything.** Emit `emptyList()`; assert any message returns
   `FILTERED`.
4. **No emission yet → empty composite.** Construct the reloader, call `start`, but emit nothing.
   Assert `messageHandler.onMessageReceived(anyMessage)` returns `FILTERED` (the default empty
   composite).
5. **Malformed config does not kill the collector.** Emit a list containing a config with an
   invalid regex (if `RegexMessageHandler` throws on compile). Assert the previous chain is still
   active, then emit a fixed list and assert the new chain is picked up.

### Existing instrumented `NotificationListenerTest`

Passes unchanged — every test uses the injected-handler constructor path, which this change does
not alter. No new instrumented tests are required.

## Out of scope

- ViewModel migration to `observeAll()` (see Non-goals).
- Reloaded-changed UI affordance.
- Config validation UI (a malformed regex is caught by the reloader's try/catch and logged; a
  separate validation pass is a different feature).

## Risks

- **Room `Flow` relaunch on the listener process.** Room's `Flow` queries re-run on table changes
  via the invalidation tracker. This is the standard, supported pattern and needs no custom
  invalidation handling.
