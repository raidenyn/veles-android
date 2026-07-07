# Stop logging OTPs and notification content

**Issue:** [raidenyn/veles-android#11](https://github.com/raidenyn/veles-android/issues/11)
**Type:** Technical improvement (security/privacy)
**Priority:** High — OTPs are secrets
**Effort:** Small (~2 hours)

## Problem

Sensitive data is written to logcat in production builds:

- `NotificationListener.onNotificationPosted` logs the full title and text of **every** notification on the device (`app/src/main/java/me/nagaev/veles/otp/NotificationListener.kt:90`: `Log.d("NotificationListener", "Title: $title, Text: $text, Package: $packageName, Timestamp: ${it.postTime}, Key: ${it.key}")`).
- `CopyDataReceiver.onReceive` logs the copied OTP verbatim (`app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt:26`: `Log.d("CopyDataReceiver", "Copied '$it'")`).
- `VelesApplication.onCreate` logs a harmless lifecycle message (`app/src/main/java/me/nagaev/veles/VelesApplication.kt:14`).

Logcat is readable by ADB, bug reports, and (pre-redaction) other debugging tooling. For an app whose entire premise is handling bank OTPs, leaking them (plus the content of every other app's notifications) into the system log is the single biggest privacy gap.

There are 8 `Log.d` calls total in production code: 5 in `NotificationListener`, 2 in `CopyDataReceiver`, 1 in `VelesApplication`. (`HandlerChainReloader`, added in #28, already uses `java.util.logging.Logger` and logs no secrets — out of scope.)

## Goal

No OTP value or notification body reaches logcat, in any build configuration, unless a developer has explicitly opted in via the Test screen toggle. A CI detekt rule prevents raw `android.util.Log` calls from returning.

## Non-goals

- Migrating `HandlerChainReloader`'s `java.util.logging.Logger` — it's already Android-free and logs only failure metadata.
- Custom Proguard/R8 rules to strip `Log.d` in release — the `BuildConfig.DEBUG` gate + R8 dead-code elimination handles it.
- A detekt rule for `System.out` / `printStackTrace` — not in the issue, YAGNI.
- Introducing a DI framework (Hilt/Koin) — the classes are structured to be DI-ready, but manual construction from `Context` is used now.

## Architecture

A `VelesLog` class is the only logging API in the app. It's a plain, injectable class (no singleton, no `object`) taking three constructor dependencies: a `LogSink` (the write seam), a `LogConfig` (the shared toggle state, read-only interface), and a `debugEnabled: Boolean` (the build gate). Each component that logs constructs its own `VelesLog` from its `Context` — manual DI, structured so a future IoC container can bind the three interfaces.

- **Redaction form:** length-only by default (`titleLen=3, textLen=42`). Raw content only when `LogConfig.rawContentEnabled` is true.
- **Production gate:** `debugEnabled = BuildConfig.DEBUG`. In release, every `VelesLog` method returns immediately; R8 strips the dead branch.
- **Opt-in:** a `Switch` on the Test screen toggles `rawContentEnabled`, persisted via `SharedPreferencesLogConfig`. The toggle has observable effect only in debug builds; in release `VelesLog` no-ops regardless.
- **Enforcement:** a detekt `ForbiddenMethodCall` rule bans `android.util.Log.*` everywhere except `AndroidLogSink.kt`.

```
Component constructs VelesLog(sink, logConfig, BuildConfig.DEBUG) from Context
  → VelesLog.dXxx(...)
      → if (!debugEnabled) return
      → reads logConfig.rawContentEnabled
      → sink.d(tag, length-only-msg | raw-msg)
```

## Components

### `LogConfig` (new — `common/LogConfig.kt`)

Injectable read-only toggle state:

```kotlin
interface LogConfig {
    val rawContentEnabled: Boolean
}
```

**Production: `SharedPreferencesLogConfig`** (`common/SharedPreferencesLogConfig.kt`):

```kotlin
class SharedPreferencesLogConfig(
    context: Context,
) : LogConfig {
    private val prefs = context.getSharedPreferences("veles_log_config", Context.MODE_PRIVATE)

    override val rawContentEnabled: Boolean
        get() = prefs.getBoolean(KEY_RAW_CONTENT, false)

    fun saveRawContentEnabled(value: Boolean) {
        prefs.edit().putBoolean(KEY_RAW_CONTENT, value).apply()
    }

    companion object {
        private const val KEY_RAW_CONTENT = "raw_content_enabled"
    }
}
```

`SharedPreferences` keeps an in-memory cache after first load, so reads are a HashMap lookup — negligible in a debug-gated log path. The write seam (`saveRawContentEnabled`) is used by the Test screen; `VelesLog` only reads.

**Tests: `MutableLogConfig`** — a simple `var rawContentEnabled: Boolean = false` implementing `LogConfig`, for direct control in `VelesLogTest`.

DI-ready: when a container arrives, `LogConfig` binds as a `@Singleton`; `SharedPreferencesLogConfig` is the production implementation, `MutableLogConfig` the test implementation.

### `LogSink` (new — `common/LogSink.kt`)

The write seam:

```kotlin
interface LogSink {
    fun d(tag: String, msg: String)
}
```

**Production: `AndroidLogSink`** (`common/AndroidLogSink.kt`) — the **only** file in the app that calls `android.util.Log`:

```kotlin
class AndroidLogSink : LogSink {
    override fun d(tag: String, msg: String) = android.util.Log.d(tag, msg)
}
```

**Tests: `RecordingLogSink`** — collects `(tag, msg)` pairs into a list for assertion.

### `VelesLog` (new — `common/VelesLog.kt`)

A plain class, no Android imports:

```kotlin
class VelesLog(
    private val sink: LogSink,
    private val logConfig: LogConfig,
    private val debugEnabled: Boolean,
) {
    fun d(tag: String, msg: String) {
        if (debugEnabled) sink.d(tag, msg)
    }

    fun dNotificationPosted(pkg: String, title: String, text: String, key: String, postTime: Long) {
        if (!debugEnabled) return
        if (logConfig.rawContentEnabled) {
            sink.d(
                "NotificationListener",
                "Title: $title, Text: $text, Package: $pkg, Timestamp: $postTime, Key: $key",
            )
        } else {
            sink.d(
                "NotificationListener",
                "Posted: pkg=$pkg, titleLen=${title.length}, textLen=${text.length}, Timestamp: $postTime, Key: $key",
            )
        }
    }

    fun dCopiedOtp(value: String) {
        if (!debugEnabled) return
        if (logConfig.rawContentEnabled) {
            sink.d("CopyDataReceiver", "Copied '$value'")
        } else {
            sink.d("CopyDataReceiver", "Copied OTP (len=${value.length})")
        }
    }
}
```

- `d(tag, msg)` is the generic escape hatch for non-sensitive logs (lifecycle messages: `Created`, `ListenerConnected`, etc.). The caller is responsible for not passing secrets into it — there are no secrets in the lifecycle logs.
- `dNotificationPosted` and `dCopiedOtp` are the purpose-built methods for the two sensitive logs. They receive the raw values but never forward them to `sink.d` unless `logConfig.rawContentEnabled` is true; otherwise they log lengths only.
- `postTime` is a non-sensitive `Long` timestamp — kept in both branches.

### `NotificationListener` (modify — `otp/NotificationListener.kt`)

Add a `velesLog: VelesLog? = null` constructor parameter alongside the existing `state`/`messageHandler`/`ownPackageName` injected params (the constructor already supports injection for testing).

In `onCreate`, the non-injected branch (`injectedHandler == null`) constructs the logger:

```kotlin
val log = velesLog ?: VelesLog(
    AndroidLogSink(),
    SharedPreferencesLogConfig(this),
    BuildConfig.DEBUG,
)
```

Store it in a `private var logger: VelesLog` field. Replace all 5 `Log.d` calls:
- `Log.d("NotificationListener", "Created")` → `logger.d("NotificationListener", "Created")`
- `Log.d("NotificationListener", "Started: $startId")` → `logger.d("NotificationListener", "Started: $startId")`
- `Log.d("NotificationListener", "ListenerConnected")` → `logger.d("NotificationListener", "ListenerConnected")`
- `Log.d("NotificationListener", "ListenerDisconnected")` → `logger.d("NotificationListener", "ListenerDisconnected")`
- The sensitive `onNotificationPosted` log → `logger.dNotificationPosted(packageName, title, text, it.key, it.postTime)`.

Remove `import android.util.Log`.

### `CopyDataReceiver` (modify — `otp/CopyDataReceiver.kt`)

`BroadcastReceiver` is system-instantiated, so construction happens in `onReceive` from `context` (manual DI from Context — the standard pattern, DI-ready for a future `container.inject(this)`):

```kotlin
override fun onReceive(context: Context?, intent: Intent?) {
    val logger = context?.let {
        VelesLog(AndroidLogSink(), SharedPreferencesLogConfig(it), BuildConfig.DEBUG)
    }
    logger?.d("CopyDataReceiver", "Context $context")
    context?.apply {
        intent?.getStringExtra(EXTRA_COPY_TEXT)?.let {
            val systemService = getSystemService(Context.CLIPBOARD_SERVICE)
            (systemService as ClipboardManager?)?.let { clipboardService ->
                val clip = ClipData.newPlainText("OTP", it)
                clipboardService.setPrimaryClip(clip)
                logger?.dCopiedOtp(it)
            }
        }
    }
}
```

Remove `import android.util.Log`.

### `VelesApplication` (modify — `VelesApplication.kt`)

Construct in `onCreate`:

```kotlin
override fun onCreate() {
    super.onCreate()
    val logger = VelesLog(AndroidLogSink(), SharedPreferencesLogConfig(this), BuildConfig.DEBUG)
    logger.d("VelesApplication", "Veles started")
}
```

Remove `import android.util.Log`.

### Test screen toggle (modify — `testing/`)

**`TestState`** — add `val logRawContent: Boolean = false`.

**`TestInputPreferences`** — add `loadLogRawContent(): Boolean` and `saveLogRawContent(value: Boolean)` reading/writing the same `veles_log_config` SharedPreferences file (key `raw_content_enabled`) that `SharedPreferencesLogConfig` reads. (Either reuse `SharedPreferencesLogConfig` directly or duplicate the key — I'll have `TestViewModel` hold a `SharedPreferencesLogConfig` instance and call `saveRawContentEnabled`, avoiding key duplication.)

**`TestViewModel`** — add `onLogRawContentToggled(value: Boolean)`:
```kotlin
fun onLogRawContentToggled(value: Boolean) {
    logConfig.saveRawContentEnabled(value)
    _uiState.update { it.copy(logRawContent = value) }
}
```
In `init`, load the persisted value into `TestState.logRawContent`. The toggle has observable effect only in debug builds; in release `VelesLog` no-ops regardless of the flag.

**`TestScreen`** — add a `Switch` (with a `testTag` from `TestTags`) below the Send button, labeled "Show raw notification content in logs (debug only)". `onLogRawContentToggled` callback passed from the screen's host.

**`TestTags`** — add `const val TEST_LOG_RAW_CONTENT_SWITCH = "test_log_raw_content_switch"`.

### `build.gradle.kts` (modify)

Add `buildFeatures { buildConfig = true }` inside `android { }` so `BuildConfig.DEBUG` is generated. (Currently absent — `buildFeatures` only enables `compose`.)

### `config/detekt/detekt.yml` (modify)

Add a `ForbiddenMethodCall` rule banning `android.util.Log` everywhere except `AndroidLogSink.kt`:

```yaml
potential-bugs:
  ForbiddenMethodCall:
    active: true
    excludes: ['**/AndroidLogSink.kt']
    methods:
      - 'android.util.Log.*'
```

This makes `AndroidLogSink.kt` the single chokepoint. Any new `android.util.Log` call elsewhere fails the `detekt` CI job.

## Data flow

### Notification posted (hot path)
```
NotificationListener.onNotificationPosted(sbn)
  → logger.dNotificationPosted(pkg, title, text, key, postTime)
      → if (!BuildConfig.DEBUG) return
      → SharedPreferencesLogConfig.rawContentEnabled (cached read)
      → if true: AndroidLogSink.d("NotificationListener", "Title: $title, Text: $text, ...")
      → if false: AndroidLogSink.d("NotificationListener", "Posted: pkg=..., titleLen=..., textLen=..., ...")
```

### OTP copied
```
CopyDataReceiver.onReceive(context, intent)
  → constructs VelesLog from context
  → logger.dCopiedOtp(otpValue)
      → if (!BuildConfig.DEBUG) return
      → if rawContentEnabled: AndroidLogSink.d("CopyDataReceiver", "Copied '<otp>'")
      → else: AndroidLogSink.d("CopyDataReceiver", "Copied OTP (len=<n>)")
```

### Toggle flipped
```
TestScreen Switch toggled
  → TestViewModel.onLogRawContentToggled(true)
  → SharedPreferencesLogConfig.saveRawContentEnabled(true)  // persisted
  → TestState.logRawContent = true
  // Next VelesLog read of rawContentEnabled sees true (SharedPreferences in-memory cache)
```

## Error handling

- `VelesLog` never throws: `Log.d` returns an `Int` and does not throw; `SharedPreferences.getBoolean` does not throw on a valid key. No try/catch needed.
- `logConfig.rawContentEnabled` is read on every `dXxx` call. `SharedPreferences` reads are cached in memory after first load — thread-safe via `SharedPreferences`'s internal lock. `@Volatile` is not needed on the interface (it's a read of a persisted value, not a mutable field).
- Null `context` in `CopyDataReceiver.onReceive` → logger is null → no log, no crash (the `?.` guards).

## Testing

### `VelesLogTest` (new, JVM — `src/test/`)

Pure classes, no MockK static mocking, no Robolectric. Uses `RecordingLogSink` and `MutableLogConfig`.

Cases:
1. `dNotificationPosted` with `rawContentEnabled = false` → sink received one call; message contains `titleLen=` and `textLen=`; message does NOT contain the raw title or text.
2. `dNotificationPosted` with `rawContentEnabled = true` → sink message contains the raw title and text.
3. `dCopiedOtp` with `rawContentEnabled = false` → message contains `len=` and does NOT contain the OTP value.
4. `dCopiedOtp` with `rawContentEnabled = true` → message contains the OTP value.
5. `dNotificationPosted` with `debugEnabled = false` → sink received zero calls (release no-op).
6. `dCopiedOtp` with `debugEnabled = false` → sink received zero calls.
7. `d(tag, msg)` with `debugEnabled = true` → sink received one call with the exact msg.
8. `d(tag, msg)` with `debugEnabled = false` → sink received zero calls.

### `NotificationListenerTest` (modify — `src/androidTest/`)

Pass a `VelesLog(RecordingLogSink(), MutableLogConfig(), debugEnabled = true)` via the new `velesLog` constructor param. The existing `mockkStatic(Log::class)` can be dropped (no longer needed for log mocking — `VelesLog` delegates to the injected sink, not `android.util.Log`). Other static mocks in the file (`RedactionDetector`) stay. Existing behavioral assertions (TestResultFlow, cancelNotification, redaction) unchanged — they don't assert on log content.

### `CopyDataReceiverTest` (modify — `src/test/`)

The receiver now constructs `VelesLog(AndroidLogSink(), SharedPreferencesLogConfig(context), BuildConfig.DEBUG)` from `context`. The test currently uses MockK with a mocked `Context`. Add a mocked `SharedPreferences` to the `context` mock so `SharedPreferencesLogConfig` can be constructed:

```kotlin
val prefs = mockk<SharedPreferences>(relaxed = true)
every { prefs.getBoolean(any(), any()) } returns false
every { context.getSharedPreferences("veles_log_config", Context.MODE_PRIVATE) } returns prefs
```

Keep `mockkStatic(Log::class)` since `AndroidLogSink` calls `android.util.Log.d` and the test runs on JVM without the Android runtime. Existing behavioral assertions (clipboard) unchanged. (Alternative: switch to Robolectric for a real `Context`+`SharedPreferences`, but MockK with a `SharedPreferences` mock is simpler and consistent with the existing test style.)

### `TestViewModel` toggle test (new or added to existing — `src/test/`)

Assert `onLogRawContentToggled(true)` calls `logConfig.saveRawContentEnabled(true)` and updates `TestState.logRawContent` to true. Use a MockK-mocked `SharedPreferencesLogConfig` (or a `MutableLogConfig`-style fake with a recorded `saveRawContentEnabled` call).

### Detekt guard

After the `ForbiddenMethodCall` rule is added, verify `./gradlew detekt` fails if a raw `android.util.Log` call is added outside `AndroidLogSink.kt`. (This is a config check, not a unit test — the plan will include a step to temporarily add a `Log.d` call elsewhere, run detekt, confirm failure, then remove it.)

## Out of scope

- `HandlerChainReloader`'s `java.util.logging.Logger` — already Android-free, logs no secrets.
- Proguard/R8 custom rules — `BuildConfig.DEBUG` gate + R8 DCE handles release stripping.
- `System.out` / `printStackTrace` detekt rule — not in the issue.
- DI framework introduction — classes are DI-ready; manual construction is used now.

## Risks

- **`BuildConfig.DEBUG` in tests:** unit tests run against the debug build variant, so `BuildConfig.DEBUG` is `true` in the test classpath. The `debugEnabled` constructor param lets `VelesLogTest` pass `false` directly to test the release no-op path — no Robolectric needed for that case.
- **`SharedPreferences` in `CopyDataReceiverTest`:** the test currently uses a fully-mocked `Context`. Adding a mocked `SharedPreferences` is a small extension and stays consistent with the MockK style. If it proves awkward, the test can switch to Robolectric (already a test dependency).
- **Detekt `ForbiddenMethodCall` config syntax:** the exact YAML key for method patterns (`methods:` vs `methodsToFind:`) varies by detekt version. The plan will verify the rule fires correctly against a throwaway `Log.d` call before relying on it.
