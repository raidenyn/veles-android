# Multi-Bank Handler Config Design

**Date:** 2026-06-29  
**Status:** Approved

## Problem

`UobOtpMessageHandler` hardcodes three regex patterns for one bank's OTP message format. Adding support for another bank requires a code change and a new release. The goal is to store handler configurations in a local SQLite database so multiple banks can be supported without code changes, and a future UI can add/edit/delete them.

## Scope

- Read bank handler configs from Room (SQLite).
- Seed the database with the existing UOB Thailand configuration on first install.
- Try each loaded handler in sequence; accept the first match.
- No UI for editing configs (deferred to a separate feature).

## Architecture

### New package: `otp/config/`

Holds the Room persistence layer. Three classes:

**`BankHandlerConfig`** — Room `@Entity`, table `bank_handler_configs`.

| Column | Type | Notes |
|---|---|---|
| `id` | `Long` | Auto-generated PK |
| `name` | `String` | Display name, e.g. "UOB Thailand" |
| `otpRegex` | `String` | Regex with two capture groups: id (group 1), value (group 2) |
| `moneyRegex` | `String` | Regex with two capture groups: currency code (group 1), amount (group 2) |
| `merchantRegex` | `String` | Regex with one capture group: merchant name (group 1) |
| `createdAt` | `Long` | Epoch milliseconds, set once at insert |
| `updatedAt` | `Long` | Epoch milliseconds, updated on each write |

**`BankHandlerConfigDao`** — `@Dao` with:
- `getAll(): List<BankHandlerConfig>` — returns all rows, ordered by `id`.
- `insertAll(vararg configs: BankHandlerConfig)` — used by the seed callback only for now.

**`BankHandlerDatabase`** — `@Database(entities = [BankHandlerConfig::class], version = 1)`, singleton via `Room.databaseBuilder`. Seeds one row in `RoomDatabase.Callback.onCreate`:

```
name          = "UOB Thailand"
otpRegex      = " (\w{4})-(\d{6}) "
moneyRegex    = "of ([A-Z]{3})(\d{1,15}\.\d{1,4}) at"
merchantRegex = "at (.{1,64}) expiring"
createdAt     = updatedAt = System.currentTimeMillis()
```

**`BankHandlerRepository`** — thin wrapper around the DAO. Exposes `getAll(): List<BankHandlerConfig>`. Loaded once at service start, not per notification.

### Modified package: `otp/handlers/`

**`RegexMessageHandler`** — replaces `UobOtpMessageHandler`. Pure Kotlin, no Android dependencies. Constructor: `(otpRegex: String, moneyRegex: String, merchantRegex: String, notifier: OtpMessageHandler)`. Compiles the three patterns at construction time. Parsing logic is identical to the deleted `UobOtpMessageHandler`. Implements `MessageHandler`.

**`CompositeMessageHandler`** — new class. Constructor: `(handlers: List<MessageHandler>)`. Implements `MessageHandler`. Iterates `handlers` in order; returns `ACCEPTED` on the first handler that matches, `FILTERED` if none match (including the empty-list case).

**`UobOtpMessageHandler`** — deleted. Its UOB patterns live in the seed row.

### Wiring: `NotificationListener`

The default-parameter block that previously constructed `UobOtpMessageHandler` is replaced:

1. Obtain `BankHandlerDatabase` (singleton, Context = `this`).
2. Call `repository.getAll()` — blocking, acceptable on the service startup path.
3. Map each config to `RegexMessageHandler(config.otpRegex, config.moneyRegex, config.merchantRegex, notifier)`.
4. Wrap the list in `CompositeMessageHandler`.

The injectable `messageHandler: MessageHandler?` constructor parameter is unchanged, so existing instrumented tests continue to bypass the DB.

## Data Flow

```
NotificationListener.onNotificationPosted
  → CompositeMessageHandler.onMessageReceived
      → RegexMessageHandler[0].onMessageReceived  (UOB Thailand)
          match? → UserNotifierOtpMessageHandler → notification + ACCEPTED
          no match? → RegexMessageHandler[1]... → FILTERED
```

## Testing

### Unit tests (no Android runtime)

**`RegexMessageHandlerTest`** — all existing `UobOtpMessageHandlerTest` cases migrated verbatim; class under test changes from `UobOtpMessageHandler` to `RegexMessageHandler` constructed with the UOB seed strings.

**`CompositeMessageHandlerTest`** — covers:
- First handler matches → `ACCEPTED`, second handler not called.
- First filtered, second matches → `ACCEPTED`.
- All handlers filtered → `FILTERED`.
- Empty handler list → `FILTERED`.

### Deleted

**`UobOtpMessageHandlerTest`** — deleted alongside `UobOtpMessageHandler`.

### Unchanged

Existing instrumented tests (`VelesPermissionsAppTests`, `NotificationListener` service tests) inject mocks directly and are unaffected.

## Out of Scope

- UI for creating, editing, or deleting handler configs.
- Enabling/disabling individual handlers.
- Regex validation at config-read time.
- Migration strategy for future schema changes (Room migration will be added when the schema changes).
