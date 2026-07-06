# Tech: Hot-reload bank handlers when configs change

**Type:** Technical improvement (correctness/architecture)
**Priority:** High — config edits currently don't take effect
**Effort:** Medium (~0.5–1 day)

## Problem

`NotificationListener.onCreate` builds the handler chain **once**:

```kotlin
val handlers = repository.getAll().map { config -> RegexMessageHandler(...) }
messageHandler = CompositeMessageHandler(handlers)
```

The `NotificationListenerService` is a long-lived process that the system keeps alive for as long
as notification access is granted — often days. Any config added, edited, or deleted through the
new Bank Config CRUD UI (#2) is invisible to the listener until the service is recreated (reboot,
toggle notification access). Users edit a regex, test it, and see stale behaviour with no
indication why. This also makes the Test Screen lie: it exercises the *old* chain.

## Bonus problem

`repository.getAll()` is a synchronous Room query on the main thread, enabled by
`allowMainThreadQueries()` on the database builder. It works today but blocks the main thread of
the listener process and normalizes a pattern that will hurt as the table grows.

## Proposal

1. Add a `Flow`-returning query to `BankHandlerConfigDao`:
   ```kotlin
   @Query("SELECT * FROM bank_handler_configs")
   fun observeAll(): Flow<List<BankHandlerConfig>>
   ```
2. In `NotificationListener`, hold a `@Volatile var messageHandler` and collect the flow on a
   service-scoped `CoroutineScope` (created in `onCreate`, cancelled in `onDestroy`), rebuilding
   `CompositeMessageHandler` on each emission. Room delivers flow emissions off the main thread.
3. Keep the injected-handler constructor path unchanged so existing tests still work.
4. Remove `allowMainThreadQueries()` once no synchronous callers remain (delete
   `getAll()`/keep only `getAllSuspend()` and `observeAll()`).

## Testing

- Unit test: fake repository backed by a `MutableStateFlow`; emit a new config list; assert the
  next message is matched by the new handler and not the old one.
- Instrumented: existing `NotificationListenerTest` should pass unchanged (injected handler path).
