# Stop logging OTPs and notification content — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route all app logging through an injectable `VelesLog` class that redacts OTP values and notification bodies (length-only by default) and no-ops in release builds; add a detekt rule preventing raw `android.util.Log` calls; add a Test-screen toggle for developer opt-in to raw content.

**Architecture:** `VelesLog` is a plain class taking `LogSink` (write seam), `LogConfig` (read-only toggle), and `debugEnabled: Boolean`. Production wires `AndroidLogSink` + `SharedPreferencesLogConfig` + `BuildConfig.DEBUG` from `Context` (manual DI, container-ready). A detekt `ForbiddenMethodCall` rule bans `android.util.Log` outside `AndroidLogSink.kt`.

**Tech Stack:** Kotlin, AndroidX (Compose, Room, Lifecycle), kotlinx.coroutines, JUnit4 + MockK (unit tests), Robolectric (already a test dep), detekt 1.23.7.

## Global Constraints

- minSdk 33; single-module Android app.
- `BuildConfig.DEBUG` is the production build gate. Requires `buildFeatures { buildConfig = true }` in `app/build.gradle.kts` (currently absent — added in Task 1).
- `VelesLog`, `LogSink`, `LogConfig`, `SharedPreferencesLogConfig`, `MutableLogConfig`, `RecordingLogSink` must be pure Kotlin / Android-free OR take `Context` only where noted — no `android.util.Log` imports outside `AndroidLogSink.kt`.
- detekt version pinned at 1.23.7 (`libs.detekt`); do not bump.
- Unit tests run on JVM (`./gradlew testDebugUnitTest`); `BuildConfig.DEBUG` is `true` in the test classpath, so the release no-op path is tested via the `debugEnabled` constructor param, not by flipping `BuildConfig`.
- Commit style: lowercase prefix (`feat:`, `refactor:`, `test:`, `chore:`, `style:`) matching `git log --oneline` history.
- The only file allowed to call `android.util.Log` is `AndroidLogSink.kt` (enforced by detekt in Task 6).
- `HandlerChainReloader`'s `java.util.logging.Logger` is out of scope — leave it unchanged.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `app/build.gradle.kts` | Modify | Enable `buildConfig = true`. |
| `app/src/main/java/me/nagaev/veles/common/LogSink.kt` | Create | Write seam interface + `AndroidLogSink` (only `android.util.Log` caller) + `RecordingLogSink` (test). |
| `app/src/main/java/me/nagaev/veles/common/LogConfig.kt` | Create | Read-only toggle interface + `SharedPreferencesLogConfig` (production) + `MutableLogConfig` (test). |
| `app/src/main/java/me/nagaev/veles/common/VelesLog.kt` | Create | Injectable logging class with redaction + build gate. |
| `app/src/test/java/me/nagaev/veles/common/VelesLogTest.kt` | Create | JVM unit test for `VelesLog` (8 cases). |
| `app/src/main/java/me/nagaev/veles/otp/NotificationListener.kt` | Modify | Inject `VelesLog`, replace 5 `Log.d` calls. |
| `app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt` | Modify | Construct `VelesLog` in `onReceive`, replace 2 `Log.d` calls. |
| `app/src/main/java/me/nagaev/veles/VelesApplication.kt` | Modify | Construct `VelesLog` in `onCreate`, replace 1 `Log.d` call. |
| `app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt` | Modify | Inject `VelesLog` into tests; drop `mockkStatic(Log)`. |
| `app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt` | Modify | Add `SharedPreferences` mock for `SharedPreferencesLogConfig`. |
| `app/src/main/java/me/nagaev/veles/testing/viewmodel/TestState.kt` | Modify | Add `logRawContent: Boolean` field. |
| `app/src/main/java/me/nagaev/veles/testing/viewmodel/TestViewModel.kt` | Modify | Add `onLogRawContentToggled`, load persisted value. |
| `app/src/main/java/me/nagaev/veles/testing/ui/TestScreen.kt` | Modify | Add `Switch` for raw-content toggle. |
| `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt` | Modify | Add `TEST_LOG_RAW_CONTENT_SWITCH`. |
| `app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt` | Modify | Wire `onLogRawContentToggled` callback. |
| `app/src/test/java/me/nagaev/veles/testing/viewmodel/TestViewModelTest.kt` | Modify | Add toggle-persists test. |
| `config/detekt/detekt.yml` | Modify | Add `ForbiddenMethodCall` rule. |

---

### Task 1: Enable BuildConfig and create the logging seams (`LogSink`, `LogConfig`)

**Files:**
- Modify: `app/build.gradle.kts`
- Create: `app/src/main/java/me/nagaev/veles/common/LogSink.kt`
- Create: `app/src/main/java/me/nagaev/veles/common/LogConfig.kt`

**Interfaces:**
- Produces: `LogSink` (interface, `fun d(tag: String, msg: String)`), `AndroidLogSink` (class implementing `LogSink`), `LogConfig` (interface, `val rawContentEnabled: Boolean`), `SharedPreferencesLogConfig(context: Context): LogConfig` with `fun saveRawContentEnabled(value: Boolean)`.

- [ ] **Step 1: Enable `buildConfig` in `app/build.gradle.kts`**

Edit `app/build.gradle.kts`. In the `android { }` block, the `buildFeatures { }` currently only enables `compose`. Add `buildConfig = true`:

```kotlin
    buildFeatures {
        compose = true
        buildConfig = true
    }
```

- [ ] **Step 2: Create `LogSink.kt`**

Create `app/src/main/java/me/nagaev/veles/common/LogSink.kt`:

```kotlin
package me.nagaev.veles.common

import android.util.Log

interface LogSink {
    fun d(tag: String, msg: String)
}

class AndroidLogSink : LogSink {
    override fun d(tag: String, msg: String) {
        Log.d(tag, msg)
    }
}
```

This is the only file in production code that imports `android.util.Log`. (The detekt rule in Task 6 will enforce this.)

- [ ] **Step 3: Create `LogConfig.kt`**

Create `app/src/main/java/me/nagaev/veles/common/LogConfig.kt`:

```kotlin
package me.nagaev.veles.common

import android.content.Context

interface LogConfig {
    val rawContentEnabled: Boolean
}

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

- [ ] **Step 4: Verify the build compiles**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL (`BuildConfig` is now generated; the new classes compile).

- [ ] **Step 5: Verify existing tests still pass**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew testDebugUnitTest`
Expected: BUILD SUCCESSFUL, all existing tests green.

- [ ] **Step 6: Commit**

```bash
git add app/build.gradle.kts app/src/main/java/me/nagaev/veles/common/LogSink.kt app/src/main/java/me/nagaev/veles/common/LogConfig.kt
git commit -m "feat: add LogSink and LogConfig seams for redacted logging"
```

---

### Task 2: Create `VelesLog` (TDD)

**Files:**
- Create: `app/src/test/java/me/nagaev/veles/common/VelesLogTest.kt`
- Create: `app/src/main/java/me/nagaev/veles/common/VelesLog.kt`

**Interfaces:**
- Consumes: `LogSink` (interface, `fun d(tag: String, msg: String)`), `LogConfig` (interface, `val rawContentEnabled: Boolean`) from Task 1.
- Produces: `VelesLog(sink: LogSink, logConfig: LogConfig, debugEnabled: Boolean)` with methods `d(tag: String, msg: String)`, `dNotificationPosted(pkg: String, title: String, text: String, key: String, postTime: Long)`, `dCopiedOtp(value: String)`. Also produces `RecordingLogSink` (test helper, lives in the test file) and `MutableLogConfig` (test helper, lives in the test file).

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/me/nagaev/veles/common/VelesLogTest.kt`:

```kotlin
package me.nagaev.veles.common

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VelesLogTest {

    private class RecordingLogSink : LogSink {
        val calls = mutableListOf<Pair<String, String>>()
        override fun d(tag: String, msg: String) { calls.add(tag to msg) }
    }

    private class MutableLogConfig(
        override var rawContentEnabled: Boolean = false,
    ) : LogConfig

    private val sink = RecordingLogSink()
    private val config = MutableLogConfig()
    private val title = "UOB"
    private val text = "Your OTP is 123456"
    private val otpValue = "123456"

    @Test
    fun `dNotificationPosted redacts to lengths when rawContentEnabled is false`() {
        val log = VelesLog(sink, config, debugEnabled = true)
        log.dNotificationLogged(pkg = "com.bank", title = title, text = text, key = "key1", postTime = 1000L)

        assertEquals(1, sink.calls.size)
        val (_, msg) = sink.calls[0]
        assertTrue("should contain titleLen", msg.contains("titleLen=${title.length}"))
        assertTrue("should contain textLen", msg.contains("textLen=${text.length}"))
        assertFalse("must not contain raw title", msg.contains(title))
        assertFalse("must not contain raw text", msg.contains(text))
    }

    @Test
    fun `dNotificationPosted logs raw content when rawContentEnabled is true`() {
        config.rawContentEnabled = true
        val log = VelesLog(sink, config, debugEnabled = true)
        log.dNotificationLogged(pkg = "com.bank", title = title, text = text, key = "key1", postTime = 1000L)

        assertEquals(1, sink.calls.size)
        val (_, msg) = sink.calls[0]
        assertTrue("should contain raw title", msg.contains(title))
        assertTrue("should contain raw text", msg.contains(text))
    }

    @Test
    fun `dCopiedOtp redacts to length when rawContentEnabled is false`() {
        val log = VelesLog(sink, config, debugEnabled = true)
        log.dCopiedOtp(otpValue)

        assertEquals(1, sink.calls.size)
        val (_, msg) = sink.calls[0]
        assertTrue("should contain len", msg.contains("len=${otpValue.length}"))
        assertFalse("must not contain the OTP value", msg.contains(otpValue))
    }

    @Test
    fun `dCopiedOtp logs raw value when rawContentEnabled is true`() {
        config.rawContentEnabled = true
        val log = VelesLog(sink, config, debugEnabled = true)
        log.dCopiedOtp(otpValue)

        assertEquals(1, sink.calls.size)
        val (_, msg) = sink.calls[0]
        assertTrue("should contain the OTP value", msg.contains(otpValue))
    }

    @Test
    fun `dNotificationPosted is a no-op when debugEnabled is false`() {
        val log = VelesLog(sink, config, debugEnabled = false)
        log.dNotificationLogged(pkg = "com.bank", title = title, text = text, key = "key1", postTime = 1000L)

        assertEquals(0, sink.calls.size)
    }

    @Test
    fun `dCopiedOtp is a no-op when debugEnabled is false`() {
        val log = VelesLog(sink, config, debugEnabled = false)
        log.dCopiedOtp(otpValue)

        assertEquals(0, sink.calls.size)
    }

    @Test
    fun `d forwards to sink when debugEnabled is true`() {
        val log = VelesLog(sink, config, debugEnabled = true)
        log.d("Tag", "non-sensitive message")

        assertEquals(1, sink.calls.size)
        assertEquals("Tag" to "non-sensitive message", sink.calls[0])
    }

    @Test
    fun `d is a no-op when debugEnabled is false`() {
        val log = VelesLog(sink, config, debugEnabled = false)
        log.d("Tag", "non-sensitive message")

        assertEquals(0, sink.calls.size)
    }
}
```

Note: the test calls a method named `dNotificationLogged` (not `dNotificationPosted`) — this is the `VelesLog` method name. The "Logged" suffix avoids redundancy with "Posted" from the call site and reads as what the method does (logs a notification event). The implementer must use this exact name.

- [ ] **Step 2: Run the test to verify it fails**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew testDebugUnitTest --tests "me.nagaev.veles.common.VelesLogTest"`
Expected: FAIL with a compile error — `VelesLog` is not defined (unresolved reference).

- [ ] **Step 3: Write the implementation**

Create `app/src/main/java/me/nagaev/veles/common/VelesLog.kt`:

```kotlin
package me.nagaev.veles.common

class VelesLog(
    private val sink: LogSink,
    private val logConfig: LogConfig,
    private val debugEnabled: Boolean,
) {
    fun d(tag: String, msg: String) {
        if (debugEnabled) sink.d(tag, msg)
    }

    fun dNotificationLogged(pkg: String, title: String, text: String, key: String, postTime: Long) {
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

- [ ] **Step 4: Run the test to verify it passes**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew testDebugUnitTest --tests "me.nagaev.veles.common.VelesLogTest"`
Expected: PASS (8 tests).

- [ ] **Step 5: Run the full unit test suite**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew testDebugUnitTest`
Expected: BUILD SUCCESSFUL, all tests green (no regressions).

- [ ] **Step 6: Run spotless + detekt**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew spotlessApply detekt`
Expected: BUILD SUCCESSFUL (spotless auto-formats; detekt passes — `VelesLog` has no `android.util.Log` import).

- [ ] **Step 7: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/common/VelesLog.kt app/src/test/java/me/nagaev/veles/common/VelesLogTest.kt
git commit -m "feat: add VelesLog with redaction and build-type gating"
```

---

### Task 3: Wire `VelesLog` into `NotificationListener`, `CopyDataReceiver`, `VelesApplication`

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/NotificationListener.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt`
- Modify: `app/src/main/java/me/nagaev/veles/VelesApplication.kt`
- Modify: `app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt`
- Modify: `app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt`

**Interfaces:**
- Consumes: `VelesLog(sink, logConfig, debugEnabled)`, `AndroidLogSink`, `SharedPreferencesLogConfig(context)`, `VelesLog.d(tag, msg)`, `VelesLog.dNotificationLogged(pkg, title, text, key, postTime)`, `VelesLog.dCopiedOtp(value)` from Tasks 1-2.
- Produces: `NotificationListener` with an additional constructor param `velesLog: VelesLog? = null` (used by tests; production constructs it in `onCreate`).

- [ ] **Step 1: Update `NotificationListener`**

Edit `app/src/main/java/me/nagaev/veles/otp/NotificationListener.kt`:

1. Remove `import android.util.Log`.
2. Add imports:
   ```kotlin
   import me.nagaev.veles.common.AndroidLogSink
   import me.nagaev.veles.common.SharedPreferencesLogConfig
   import me.nagaev.veles.common.VelesLog
   ```
3. Add a `velesLog: VelesLog? = null` constructor param (after `ownPackageName`):
   ```kotlin
   class NotificationListener(
       state: NotificationStatePreferences? = null,
       messageHandler: MessageHandler? = null,
       private val ownPackageName: String? = null,
       velesLog: VelesLog? = null,
   ) : NotificationListenerService() {
   ```
4. Add a field `private var logger: VelesLog? = velesLog`.
5. In `onCreate`, after the `if (injectedHandler == null) { ... }` block (or inside it, before `reloader = r`), add construction when `velesLog` was null:
   ```kotlin
   if (logger == null) {
       logger = VelesLog(AndroidLogSink(), SharedPreferencesLogConfig(this), BuildConfig.DEBUG)
   }
   ```
   (Place this at the top of `onCreate`, before the `if (injectedHandler == null)` block, so `logger` is available for the `Log.d("NotificationListener", "Created")` replacement.)
6. Replace the 5 `Log.d` calls:
   - `Log.d("NotificationListener", "Created")` → `logger?.d("NotificationListener", "Created")`
   - `Log.d("NotificationListener", "Started: $startId")` → `logger?.d("NotificationListener", "Started: $startId")`
   - `Log.d("NotificationListener", "ListenerConnected")` → `logger?.d("NotificationListener", "ListenerConnected")`
   - `Log.d("NotificationListener", "ListenerDisconnected")` → `logger?.d("NotificationListener", "ListenerDisconnected")`
   - The sensitive `onNotificationPosted` log:
     ```kotlin
     logger?.dNotificationLogged(pkg = packageName, title = title, text = text, key = it.key, postTime = it.postTime)
     ```

The full updated `onCreate` start:
```kotlin
    override fun onCreate() {
        super.onCreate()
        if (logger == null) {
            logger = VelesLog(AndroidLogSink(), SharedPreferencesLogConfig(this), BuildConfig.DEBUG)
        }
        logger?.d("NotificationListener", "Created")
        if (injectedHandler == null) {
            // ... existing scope/reloader construction unchanged ...
        }
    }
```

- [ ] **Step 2: Update `CopyDataReceiver`**

Edit `app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt`. The full replacement:

```kotlin
package me.nagaev.veles.otp

import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import me.nagaev.veles.common.AndroidLogSink
import me.nagaev.veles.common.SharedPreferencesLogConfig
import me.nagaev.veles.common.VelesLog

class CopyDataReceiver : BroadcastReceiver() {
    companion object {
        const val EXTRA_COPY_TEXT = "CopyText"
    }

    override fun onReceive(
        context: Context?,
        intent: Intent?,
    ) {
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
}
```

- [ ] **Step 3: Update `VelesApplication`**

Edit `app/src/main/java/me/nagaev/veles/VelesApplication.kt`. The full replacement:

```kotlin
package me.nagaev.veles

import android.app.Application
import me.nagaev.veles.common.AndroidLogSink
import me.nagaev.veles.common.SharedPreferencesLogConfig
import me.nagaev.veles.common.VelesLog

class VelesApplication : Application() {
    override fun onCreate() {
        super.onCreate()

        // This method fires once as well as constructor
        // & here we have application context

        val logger = VelesLog(AndroidLogSink(), SharedPreferencesLogConfig(this), BuildConfig.DEBUG)
        logger.d("VelesApplication", "Veles started")
    }
}
```

- [ ] **Step 4: Update `NotificationListenerTest`**

Edit `app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt`:

1. Remove `import android.util.Log` and the `mockkStatic(Log::class)` + `every { Log.d(any(), any()) } returns 0` lines from `beforeTest()`.
2. Add a test helper at the top of the class:
   ```kotlin
   private class RecordingLogSink : me.nagaev.veles.common.LogSink {
       val calls = mutableListOf<Pair<String, String>>()
       override fun d(tag: String, msg: String) { calls.add(tag to msg) }
   }
   ```
3. Where each test constructs `NotificationListener(state, messageHandler, ownPackageName = ...)`, add a `velesLog` arg:
   ```kotlin
   val log = VelesLog(RecordingLogSink(), object : me.nagaev.veles.common.LogConfig {
       override val rawContentEnabled get() = false
   }, debugEnabled = true)
   val service = NotificationListener(state, messageHandler, ownPackageName = "...", velesLog = log)
   ```
   Add the `velesLog = log` arg to every `NotificationListener(...)` construction in the file. Add imports for `VelesLog`.
4. The `unmockkAll()` in `afterTest()` stays — it unmocks `RedactionDetector` and any other statics. `Log` is no longer mocked, so removing `mockkStatic(Log::class)` is safe.

Note: there are ~10 `NotificationListener(...)` constructions in this file. Each needs the `velesLog = log` arg. To avoid repetition, define `log` once as a class field:
```kotlin
private val testLog = VelesLog(RecordingLogSink(), object : LogConfig {
    override val rawContentEnabled get() = false
}, debugEnabled = true)
```
and pass `velesLog = testLog` to each construction.

- [ ] **Step 5: Update `CopyDataReceiverTest`**

Edit `app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt`:

1. **Keep** `import android.util.Log`, `import io.mockk.mockkStatic`, and the `mockkStatic(Log::class)` + `every { Log.d(any(), any()) } returns 0` lines in `beforeTest()`. They are still needed because `AndroidLogSink` (used in production wiring inside `onReceive`) delegates to `android.util.Log.d`, which is not available on the JVM. `VelesLog`'s own logic is tested in `VelesLogTest` with a fake sink; here we test the real `CopyDataReceiver` wiring, so `Log` must stay mocked.
2. Add a mocked `SharedPreferences` so `SharedPreferencesLogConfig(context)` can be constructed. In `beforeTest()`, after the existing `context` setup:
   ```kotlin
   val prefs = mockk<android.content.SharedPreferences>(relaxed = true)
   every { prefs.getBoolean(any(), any()) } returns false
   every { context.getSharedPreferences("veles_log_config", Context.MODE_PRIVATE) } returns prefs
   ```
   (`relaxed = true` handles `edit()` and the `Editor` chain.)

- [ ] **Step 6: Verify the build compiles**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 7: Verify unit tests pass**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew testDebugUnitTest`
Expected: BUILD SUCCESSFUL, `CopyDataReceiverTest` green, `VelesLogTest` green, all others green.

- [ ] **Step 8: Verify instrumented tests compile**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew compileDebugAndroidTestKotlin`
Expected: BUILD SUCCESSFUL. (No device needed for compile; `connectedDebugAndroidTest` needs a device — run if available, otherwise confirm compile.)

- [ ] **Step 9: Run spotless + detekt**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew spotlessApply detekt`
Expected: BUILD SUCCESSFUL. (detekt may warn about `android.util.Log` in `AndroidLogSink.kt` — the `ForbiddenMethodCall` rule is not added until Task 6, so no failure yet. If detekt fails for other reasons, fix them.)

- [ ] **Step 10: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/NotificationListener.kt app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt app/src/main/java/me/nagaev/veles/VelesApplication.kt app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt
git commit -m "refactor: route all logging through VelesLog"
```

---

### Task 4: Add the Test-screen toggle for raw-content opt-in

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt`
- Modify: `app/src/main/java/me/nagaev/veles/testing/viewmodel/TestState.kt`
- Modify: `app/src/main/java/me/nagaev/veles/testing/viewmodel/TestViewModel.kt`
- Modify: `app/src/main/java/me/nagaev/veles/testing/viewmodel/TestViewModelFactory.kt`
- Modify: `app/src/main/java/me/nagaev/veles/testing/ui/TestScreen.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt`
- Modify: `app/src/test/java/me/nagaev/veles/testing/viewmodel/TestViewModelTest.kt`

**Interfaces:**
- Consumes: `SharedPreferencesLogConfig(context)` from Task 1, `SharedPreferencesLogConfig.saveRawContentEnabled(value)` / `.rawContentEnabled` from Task 1.
- Produces: `TestState.logRawContent: Boolean`, `TestViewModel.onLogRawContentToggled(value: Boolean)`, `TestScreen` accepts `logRawContent: Boolean` and `onLogRawContentToggled: (Boolean) -> Unit` params.

- [ ] **Step 1: Add the test tag**

Edit `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt`. Add after the existing `TEST_*` constants:

```kotlin
    const val TEST_LOG_RAW_CONTENT_SWITCH = "test_log_raw_content_switch"
```

- [ ] **Step 2: Add `logRawContent` to `TestState`**

Edit `app/src/main/java/me/nagaev/veles/testing/viewmodel/TestState.kt`:

```kotlin
package me.nagaev.veles.testing.viewmodel

import me.nagaev.veles.common.TestResult

data class TestState(
    val inputText: String = "",
    val lastResult: TestResult? = null,
    val logRawContent: Boolean = false,
)
```

- [ ] **Step 3: Update `TestViewModel`**

Edit `app/src/main/java/me/nagaev/veles/testing/viewmodel/TestViewModel.kt`. Add a `logConfig: SharedPreferencesLogConfig` constructor param, load the persisted value in `init`, and add `onLogRawContentToggled`:

```kotlin
package me.nagaev.veles.testing.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import me.nagaev.veles.common.SharedPreferencesLogConfig
import me.nagaev.veles.common.TestInputPreferences
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.testing.TestNotificationSender

class TestViewModel(
    private val preferences: TestInputPreferences,
    private val sender: TestNotificationSender,
    private val logConfig: SharedPreferencesLogConfig,
) : ViewModel() {
    private val _uiState = MutableStateFlow(
        TestState(
            inputText = preferences.load(),
            logRawContent = logConfig.rawContentEnabled,
        ),
    )
    val uiState: StateFlow<TestState> = _uiState

    init {
        viewModelScope.launch(Dispatchers.Unconfined) {
            TestResultFlow.current.collect { result ->
                result?.let {
                    _uiState.update { state -> state.copy(lastResult = it) }
                }
            }
        }
    }

    fun onTextChanged(text: String) {
        _uiState.update { it.copy(inputText = text) }
        preferences.save(text)
    }

    fun send() {
        sender.post(_uiState.value.inputText)
    }

    fun onLogRawContentToggled(value: Boolean) {
        logConfig.saveRawContentEnabled(value)
        _uiState.update { it.copy(logRawContent = value) }
    }

    override fun onCleared() {
        super.onCleared()
        TestResultFlow.current.value = null
    }
}
```

- [ ] **Step 4: Update `TestViewModelFactory`**

Edit `app/src/main/java/me/nagaev/veles/testing/viewmodel/TestViewModelFactory.kt` to pass a `SharedPreferencesLogConfig`:

```kotlin
package me.nagaev.veles.testing.viewmodel

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import me.nagaev.veles.common.SharedPreferencesLogConfig
import me.nagaev.veles.common.TestInputPreferences
import me.nagaev.veles.testing.TestNotificationSender

class TestViewModelFactory(
    private val context: Context,
) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        require(modelClass.isAssignableFrom(TestViewModel::class.java)) { "ViewModel Not Found" }
        return TestViewModel(
            preferences = TestInputPreferences(context),
            sender = TestNotificationSender(context),
            logConfig = SharedPreferencesLogConfig(context),
        ) as T
    }
}
```

- [ ] **Step 5: Add the `Switch` to `TestScreen`**

Edit `app/src/main/java/me/nagaev/veles/testing/ui/TestScreen.kt`. Add `logRawContent: Boolean` and `onLogRawContentToggled: (Boolean) -> Unit` params, and a `Switch` composable after the Send button. Add the needed imports (`Switch`, `Row`, `Text` already imported; add `androidx.compose.material3.Switch`).

The updated `TestScreen` signature and the added block:

```kotlin
@Composable
fun TestScreen(
    state: TestState,
    onTextChanged: (String) -> Unit,
    onSend: () -> Unit,
    logRawContent: Boolean,
    onLogRawContentToggled: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp)
            .statusBarsPadding(),
    ) {
        // ... existing Text, OutlinedTextField, Spacer, Button unchanged ...

        Spacer(Modifier.height(16.dp))
        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            Switch(
                checked = logRawContent,
                onCheckedChange = onLogRawContentToggled,
                modifier = Modifier.testTag(TestTags.TEST_LOG_RAW_CONTENT_SWITCH),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                text = "Show raw notification content in logs (debug only)",
                style = MaterialTheme.typography.bodyMedium,
            )
        }

        state.lastResult?.let { result ->
            // ... unchanged ...
        }
    }
}
```

Add the `width` import: `import androidx.compose.foundation.layout.width`.

Update the `TestScreenPreview` at the bottom of the file to pass the two new params:
```kotlin
        onTextChanged = {},
        onSend = {},
        logRawContent = false,
        onLogRawContentToggled = {},
```

- [ ] **Step 6: Wire the callback in `VelesPermissionsApp`**

Edit `app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt`. In the `composable("test") { ... }` block, the `TestScreen(...)` call needs the two new params:

```kotlin
                    TestScreen(
                        state = testState,
                        onTextChanged = testViewModel::onTextChanged,
                        onSend = testViewModel::send,
                        logRawContent = testState.logRawContent,
                        onLogRawContentToggled = testViewModel::onLogRawContentToggled,
                    )
```

- [ ] **Step 7: Add the toggle-persists test**

Edit `app/src/test/java/me/nagaev/veles/testing/viewmodel/TestViewModelTest.kt`. Add a test (and the needed imports/mocks) asserting that `onLogRawContentToggled(true)` persists and updates state.

First, read the existing `TestViewModelTest.kt` to match its setup style. The test will need a mock `SharedPreferencesLogConfig`. Add:

```kotlin
    @Test
    fun `onLogRawContentToggled persists the value and updates state`() {
        val logConfig = mockk<me.nagaev.veles.common.SharedPreferencesLogConfig>(relaxed = true)
        every { logConfig.rawContentEnabled } returns false
        val viewModel = TestViewModel(
            preferences = mockk(relaxed = true),
            sender = mockk(relaxed = true),
            logConfig = logConfig,
        )

        viewModel.onLogRawContentToggled(true)

        verify { logConfig.saveRawContentEnabled(true) }
        assertEquals(true, viewModel.uiState.value.logRawContent)
    }
```

(Add `import io.mockk.verify` and `import org.junit.Assert.assertEquals` if not already present. The exact mock setup for `preferences`/`sender` should match the existing tests in the file — read them first and mirror the pattern.)

Also add a test that the initial `logRawContent` is loaded from `logConfig`:
```kotlin
    @Test
    fun `initial logRawContent is loaded from logConfig`() {
        val logConfig = mockk<me.nagaev.veles.common.SharedPreferencesLogConfig>(relaxed = true)
        every { logConfig.rawContentEnabled } returns true
        val viewModel = TestViewModel(
            preferences = mockk(relaxed = true),
            sender = mockk(relaxed = true),
            logConfig = logConfig,
        )

        assertEquals(true, viewModel.uiState.value.logRawContent)
    }
```

If the existing `TestViewModelTest.kt` constructions of `TestViewModel` break because of the new `logConfig` param, update them to pass a mock `SharedPreferencesLogConfig(relaxed = true)` with `every { it.rawContentEnabled } returns false`.

- [ ] **Step 8: Verify the build compiles**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 9: Verify unit tests pass**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew testDebugUnitTest`
Expected: BUILD SUCCESSFUL, all tests green including the new toggle tests.

- [ ] **Step 10: Run spotless + detekt**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew spotlessApply detekt`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 11: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt app/src/main/java/me/nagaev/veles/testing/viewmodel/TestState.kt app/src/main/java/me/nagaev/veles/testing/viewmodel/TestViewModel.kt app/src/main/java/me/nagaev/veles/testing/viewmodel/TestViewModelFactory.kt app/src/main/java/me/nagaev/veles/testing/ui/TestScreen.kt app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt app/src/test/java/me/nagaev/veles/testing/viewmodel/TestViewModelTest.kt
git commit -m "feat: add Test-screen toggle for raw log content opt-in"
```

---

### Task 5: Add detekt `ForbiddenMethodCall` rule banning `android.util.Log`

**Files:**
- Modify: `config/detekt/detekt.yml`

**Interfaces:**
- Produces: a detekt rule that fails the build on any `android.util.Log.*` call outside `AndroidLogSink.kt`.

- [ ] **Step 1: Add the rule to `config/detekt/detekt.yml`**

Edit `config/detekt/detekt.yml`. Under the existing `potential-bugs:` block (which currently has `UnsafeCallOnNullableType: active: false`), add a `ForbiddenMethodCall` entry. The full `potential-bugs` section becomes:

```yaml
potential-bugs:
  UnsafeCallOnNullableType:
    active: false
  ForbiddenMethodCall:
    active: true
    excludes: ['**/AndroidLogSink.kt']
    methods:
      - 'android.util.Log.*'
```

Note on detekt 1.23.7 syntax: `ForbiddenMethodCall` is a rule under the `potential-bugs` rule set. The `methods` list takes fully-qualified method signatures; `android.util.Log.*` matches all static methods on `android.util.Log`. The `excludes` list takes glob paths relative to the project root. If `methods` is not the correct key for this detekt version, the implementer should check `./gradlew detekt --help` or the detekt docs and use the correct key — the intent is "ban all `android.util.Log` calls except in `AndroidLogSink.kt`."

- [ ] **Step 2: Verify detekt passes (the rule should not fire on existing code)**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew detekt`
Expected: BUILD SUCCESSFUL. `AndroidLogSink.kt` is excluded; all other `android.util.Log` calls were removed in Task 3. If detekt fails, it means a `Log.d` call was missed — find and fix it.

- [ ] **Step 3: Verify the rule fires on a throwaway violation**

Temporarily add `android.util.Log.d("test", "test")` to any file other than `AndroidLogSink.kt` (e.g. at the top of `VelesApplication.onCreate`). Run:

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew detekt`
Expected: BUILD FAILED with a `ForbiddenMethodCall` finding citing the throwaway line.

Then remove the throwaway line.

- [ ] **Step 4: Verify detekt passes again after removing the throwaway**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew detekt`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 5: Run the full verification suite**

Run: `export JAVA_HOME=/home/oc-shadow/.local/jdk && export PATH="$JAVA_HOME/bin:$PATH" && ./gradlew assembleDebug testDebugUnitTest detekt spotlessCheck`
Expected: BUILD SUCCESSFUL across all tasks.

- [ ] **Step 6: Commit**

```bash
git add config/detekt/detekt.yml
git commit -m "chore: add detekt ForbiddenMethodCall rule banning android.util.Log"
```

---

## Verification (whole-plan gate)

After all five tasks:

- [ ] `./gradlew assembleDebug` — green.
- [ ] `./gradlew testDebugUnitTest` — green, including the 8 `VelesLogTest` cases and the new `TestViewModel` toggle tests.
- [ ] `./gradlew detekt` — green; `ForbiddenMethodCall` rule active; no `android.util.Log` calls outside `AndroidLogSink.kt`.
- [ ] `./gradlew spotlessCheck` — green.
- [ ] `./gradlew compileDebugAndroidTestKotlin` — green (instrumented sources compile).
- [ ] `grep -rn "android.util.Log" app/src/main` — only `AndroidLogSink.kt` matches.
- [ ] (If a device/emulator is available) `./gradlew connectedDebugAndroidTest` — green.
