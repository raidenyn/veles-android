# Test Notification Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Test screen where the user types notification text, posts it as a real system notification, and sees whether the regex handlers matched it.

**Architecture:** A singleton `TestResultFlow` (MutableStateFlow) bridges `NotificationListener` (service) to `TestViewModel` (UI). The service detects self-posted notifications by comparing `message.source` to an injectable package name. Navigation uses the existing `navigation-compose` `NavHost` already wired in `VelesPermissionsApp`.

**Tech Stack:** Kotlin, Jetpack Compose, navigation-compose, Room (unchanged), MockK, kotlinx-coroutines-test, SharedPreferences.

## Global Constraints

- minSdk 33, compileSdk 35
- Single-module app: `app/src/main/java/me/nagaev/veles/`
- JVM unit tests in `app/src/test/java/me/nagaev/veles/`
- Instrumented tests in `app/src/androidTest/java/me/nagaev/veles/`
- Use MockK for all mocking (already in dependencies)
- Follow existing SharedPreferences pattern (`NotificationStatePreferences`) for persistence wrappers
- Follow existing ViewModel/Factory pattern (`PermissionsViewModel` / `PermissionsViewModelFactory`)
- `TestTags` constants are the stable test selectors — add any new ones there

---

## File Map

**Create:**
- `common/TestResultFlow.kt` — singleton `MutableStateFlow<TestResult?>` + `TestResult` data class
- `common/TestInputPreferences.kt` — SharedPreferences wrapper for last-typed text
- `testing/TestNotificationSender.kt` — posts a system notification on `VelesTestChannel`
- `testing/viewmodel/TestState.kt` — UI state: `inputText` + `lastResult`
- `testing/viewmodel/TestViewModel.kt` — collects `TestResultFlow`, exposes `onTextChanged`/`send`
- `testing/viewmodel/TestViewModelFactory.kt` — `ViewModelProvider.Factory`
- `testing/ui/TestScreen.kt` — Compose screen with text field, send button, result badge
- `src/test/.../common/TestResultFlowTest.kt`
- `src/test/.../testing/viewmodel/TestViewModelTest.kt`

**Modify:**
- `otp/NotificationListenerService.kt` — add `ownPackageName` parameter; write to `TestResultFlow` for self-notifications
- `src/androidTest/.../otp/NotificationListenerTest.kt` — add two tests for self-notification detection
- `common/ui/TestTags.kt` — add `TEST_INPUT`, `TEST_SEND_BUTTON`, `TEST_RESULT`
- `permissions/ui/PermissionsScreen.kt` — add `onNavigateToTest` parameter + "Test" `TextButton`
- `permissions/ui/VelesPermissionsApp.kt` — become `NavHost` with `"permissions"` and `"test"` routes
- `app/build.gradle.kts` — add `testImplementation(libs.kotlinx.coroutines.test)`

---

### Task 1: TestResultFlow singleton

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/common/TestResultFlow.kt`
- Create: `app/src/test/java/me/nagaev/veles/common/TestResultFlowTest.kt`

**Interfaces:**
- Produces: `TestResult(result: MessageHandlingResult, timestamp: Long)`, `TestResultFlow.current: MutableStateFlow<TestResult?>`
- Consumed by: Task 2 (NotificationListener), Task 4 (TestViewModel)

- [ ] **Step 1: Write the failing test**

```kotlin
// app/src/test/java/me/nagaev/veles/common/TestResultFlowTest.kt
package me.nagaev.veles.common

import me.nagaev.veles.otp.handlers.MessageHandlingResult
import org.junit.Before
import org.junit.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class TestResultFlowTest {

    @Before
    fun setUp() {
        TestResultFlow.current.value = null
    }

    @Test
    fun `initial value is null`() {
        assertNull(TestResultFlow.current.value)
    }

    @Test
    fun `emitting ACCEPTED result updates current value`() {
        val result = TestResult(MessageHandlingResult.ACCEPTED, 1000L)
        TestResultFlow.current.value = result
        assertEquals(result, TestResultFlow.current.value)
    }

    @Test
    fun `emitting FILTERED result updates current value`() {
        val result = TestResult(MessageHandlingResult.FILTERED, 2000L)
        TestResultFlow.current.value = result
        assertEquals(result, TestResultFlow.current.value)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

```
./gradlew testDebugUnitTest --tests "me.nagaev.veles.common.TestResultFlowTest"
```

Expected: FAIL — `TestResultFlow` not found.

- [ ] **Step 3: Implement TestResultFlow**

```kotlin
// app/src/main/java/me/nagaev/veles/common/TestResultFlow.kt
package me.nagaev.veles.common

import kotlinx.coroutines.flow.MutableStateFlow
import me.nagaev.veles.otp.handlers.MessageHandlingResult

data class TestResult(val result: MessageHandlingResult, val timestamp: Long)

object TestResultFlow {
    val current: MutableStateFlow<TestResult?> = MutableStateFlow(null)
}
```

- [ ] **Step 4: Run test to verify it passes**

```
./gradlew testDebugUnitTest --tests "me.nagaev.veles.common.TestResultFlowTest"
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```
git add app/src/main/java/me/nagaev/veles/common/TestResultFlow.kt
git add app/src/test/java/me/nagaev/veles/common/TestResultFlowTest.kt
git commit -m "feat: add TestResultFlow singleton for service-to-UI communication"
```

---

### Task 2: NotificationListener → TestResultFlow integration

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/NotificationListenerService.kt`
- Modify: `app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt`

**Interfaces:**
- Consumes: `TestResultFlow.current` from Task 1, `TestResult` from Task 1
- The constructor gains a third optional parameter: `ownPackageName: String? = null`

- [ ] **Step 1: Write the failing tests** (add to the existing `NotificationListenerTest.kt` after the last test)

```kotlin
// Add these two tests inside class NotificationListenerTest { ... }
// Also add these imports at the top of the file:
// import me.nagaev.veles.common.TestResult
// import me.nagaev.veles.common.TestResultFlow
// import org.junit.After
// import kotlin.test.assertEquals
// import kotlin.test.assertNull

@Before
fun resetTestResultFlow() {
    TestResultFlow.current.value = null
}

@Test
fun `onNotificationPosted writes ACCEPTED to TestResultFlow for self-notifications`() {
    val ownPkg = "me.nagaev.veles"
    val messageHandler = mockk<MessageHandler>(relaxed = true)
    val state = mockk<NotificationStatePreferences>(relaxed = true)
    val notification = mockk<Notification>(relaxed = true)
    val sbn = mockk<StatusBarNotification>(relaxed = true)
    val bundle = mockk<Bundle>(relaxed = true)

    every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "title"
    every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns "text"
    every { sbn.key } returns "key"
    every { sbn.packageName } returns ownPkg
    every { sbn.notification } returns notification
    notification.extras = bundle
    every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.ACCEPTED

    val service = NotificationListener(state, messageHandler, ownPackageName = ownPkg)
    service.onNotificationPosted(sbn)

    assertEquals(MessageHandlingResult.ACCEPTED, TestResultFlow.current.value?.result)
}

@Test
fun `onNotificationPosted does not write to TestResultFlow for external notifications`() {
    val messageHandler = mockk<MessageHandler>(relaxed = true)
    val state = mockk<NotificationStatePreferences>(relaxed = true)
    val notification = mockk<Notification>(relaxed = true)
    val sbn = mockk<StatusBarNotification>(relaxed = true)
    val bundle = mockk<Bundle>(relaxed = true)

    every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "title"
    every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns "text"
    every { sbn.key } returns "key"
    every { sbn.packageName } returns "com.some.bank"
    every { sbn.notification } returns notification
    notification.extras = bundle
    every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.ACCEPTED

    val service = NotificationListener(state, messageHandler, ownPackageName = "me.nagaev.veles")
    service.onNotificationPosted(sbn)

    assertNull(TestResultFlow.current.value)
}
```

Note: `@Before fun resetTestResultFlow()` is a second `@Before` method — rename the existing `beforeTest()` setup if it conflicts, or merge them. The existing file already has `@Before fun beforeTest()` which mocks `Log.d`. Add `resetTestResultFlow()` as a separate `@Before` method (JUnit 4 allows multiple `@Before` methods).

- [ ] **Step 2: Run tests to verify they fail**

```
./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.otp.NotificationListenerTest
```

Expected: the two new tests fail — `NotificationListener` constructor has no `ownPackageName` parameter yet.

- [ ] **Step 3: Modify NotificationListenerService.kt**

Replace the full file content:

```kotlin
package me.nagaev.veles.otp

import android.content.Intent
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.core.app.NotificationCompat
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.TestResult
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.otp.config.BankHandlerRepository
import me.nagaev.veles.otp.handlers.CompositeMessageHandler
import me.nagaev.veles.otp.handlers.Message
import me.nagaev.veles.otp.handlers.MessageHandler
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.otp.handlers.RegexMessageHandler
import me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandler

class NotificationListener(
    state: NotificationStatePreferences? = null,
    messageHandler: MessageHandler? = null,
    private val ownPackageName: String? = null
) : NotificationListenerService() {

    private val state = state ?: NotificationStatePreferences(this)
    private val injectedHandler: MessageHandler? = messageHandler
    private lateinit var messageHandler: MessageHandler

    override fun onCreate() {
        super.onCreate()
        Log.d("NotificationListener", "Created")
        messageHandler = injectedHandler ?: run {
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
        super.onListenerDisconnected()
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

            val effectiveOwnPackage = ownPackageName ?: getPackageName()
            if (message.source == effectiveOwnPackage) {
                TestResultFlow.current.value = TestResult(handlingResult, System.currentTimeMillis())
            }

            if (handlingResult == MessageHandlingResult.ACCEPTED) {
                cancelNotification(it.key)
            }
        }
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```
./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.otp.NotificationListenerTest
```

Expected: all tests PASS (including the two new ones).

- [ ] **Step 5: Commit**

```
git add app/src/main/java/me/nagaev/veles/otp/NotificationListenerService.kt
git add app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt
git commit -m "feat: write to TestResultFlow when NotificationListener processes self-notifications"
```

---

### Task 3: TestInputPreferences + TestNotificationSender

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/common/TestInputPreferences.kt`
- Create: `app/src/main/java/me/nagaev/veles/testing/TestNotificationSender.kt`

**Interfaces:**
- `TestInputPreferences.save(text: String)`, `TestInputPreferences.load(): String`
- `TestNotificationSender.post(text: String)`
- Consumed by Task 4 (TestViewModel)

No unit tests for these — both are thin wrappers over Android system APIs, matching the pattern of `NotificationStatePreferences` and `UserNotifierOtpMessageHandler`.

- [ ] **Step 1: Create TestInputPreferences**

```kotlin
// app/src/main/java/me/nagaev/veles/common/TestInputPreferences.kt
package me.nagaev.veles.common

import android.content.Context

class TestInputPreferences(private val context: Context) {
    private val prefs by lazy {
        context.getSharedPreferences("test_input_preferences", Context.MODE_PRIVATE)
    }

    fun save(text: String) {
        prefs.edit().putString("test_input_text", text).apply()
    }

    fun load(): String = prefs.getString("test_input_text", "") ?: ""
}
```

- [ ] **Step 2: Create TestNotificationSender**

```kotlin
// app/src/main/java/me/nagaev/veles/testing/TestNotificationSender.kt
package me.nagaev.veles.testing

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import me.nagaev.veles.R

class TestNotificationSender(private val context: Context) {
    companion object {
        const val CHANNEL_ID = "VelesTestChannel"
        private const val NOTIFICATION_ID = 99999
    }

    fun post(text: String) {
        val builder = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_otp_message)
            .setContentTitle("Veles Test")
            .setContentText(text)
            .setPriority(NotificationCompat.PRIORITY_HIGH)

        with(NotificationManagerCompat.from(context)) {
            if (areNotificationsEnabled()) {
                tryCreateChannel()
                notify(NOTIFICATION_ID, builder.build())
            }
        }
    }

    private fun tryCreateChannel() {
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Veles Test",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "Test notifications for verifying handler configs"
        }
        manager.createNotificationChannel(channel)
    }
}
```

- [ ] **Step 3: Verify build compiles**

```
./gradlew assembleDebug
```

Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**

```
git add app/src/main/java/me/nagaev/veles/common/TestInputPreferences.kt
git add app/src/main/java/me/nagaev/veles/testing/TestNotificationSender.kt
git commit -m "feat: add TestInputPreferences and TestNotificationSender"
```

---

### Task 4: TestViewModel + TestState + TestViewModelFactory

**Files:**
- Modify: `app/build.gradle.kts` (add `testImplementation(libs.kotlinx.coroutines.test)`)
- Create: `app/src/main/java/me/nagaev/veles/testing/viewmodel/TestState.kt`
- Create: `app/src/main/java/me/nagaev/veles/testing/viewmodel/TestViewModel.kt`
- Create: `app/src/main/java/me/nagaev/veles/testing/viewmodel/TestViewModelFactory.kt`
- Create: `app/src/test/java/me/nagaev/veles/testing/viewmodel/TestViewModelTest.kt`

**Interfaces:**
- Consumes: `TestResultFlow` (Task 1), `TestInputPreferences` (Task 3), `TestNotificationSender` (Task 3)
- Produces: `TestState(inputText: String, lastResult: TestResult?)`, `TestViewModel.uiState: StateFlow<TestState>`, `TestViewModel.onTextChanged(String)`, `TestViewModel.send()`
- Consumed by: Task 5 (TestScreen), Task 6 (navigation wiring)

- [ ] **Step 1: Add coroutines-test to JVM test dependencies**

In `app/build.gradle.kts`, add after the existing `testImplementation(libs.junit)` line:

```kotlin
testImplementation(libs.kotlinx.coroutines.test)
```

- [ ] **Step 2: Write the failing tests**

```kotlin
// app/src/test/java/me/nagaev/veles/testing/viewmodel/TestViewModelTest.kt
package me.nagaev.veles.testing.viewmodel

import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import me.nagaev.veles.common.TestInputPreferences
import me.nagaev.veles.common.TestResult
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.testing.TestNotificationSender
import org.junit.After
import org.junit.Before
import org.junit.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

@OptIn(ExperimentalCoroutinesApi::class)
class TestViewModelTest {
    private val testDispatcher = UnconfinedTestDispatcher()
    private lateinit var preferences: TestInputPreferences
    private lateinit var sender: TestNotificationSender
    private lateinit var viewModel: TestViewModel

    @Before
    fun setUp() {
        Dispatchers.setMain(testDispatcher)
        TestResultFlow.current.value = null
        preferences = mockk(relaxed = true)
        sender = mockk(relaxed = true)
        viewModel = TestViewModel(preferences, sender)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
        TestResultFlow.current.value = null
    }

    @Test
    fun `initial state has empty text when preferences returns empty string`() {
        assertEquals("", viewModel.uiState.value.inputText)
        assertNull(viewModel.uiState.value.lastResult)
    }

    @Test
    fun `initial state loads saved text from preferences`() {
        every { preferences.load() } returns "saved message"
        val vm = TestViewModel(preferences, sender)
        assertEquals("saved message", vm.uiState.value.inputText)
    }

    @Test
    fun `onTextChanged updates inputText in state`() {
        viewModel.onTextChanged("hello")
        assertEquals("hello", viewModel.uiState.value.inputText)
    }

    @Test
    fun `onTextChanged saves text to preferences`() {
        viewModel.onTextChanged("hello")
        verify { preferences.save("hello") }
    }

    @Test
    fun `send posts notification with current inputText`() {
        viewModel.onTextChanged("test message")
        viewModel.send()
        verify { sender.post("test message") }
    }

    @Test
    fun `TestResultFlow update sets lastResult in state`() {
        assertNull(viewModel.uiState.value.lastResult)
        val result = TestResult(MessageHandlingResult.ACCEPTED, 1000L)
        TestResultFlow.current.value = result
        assertEquals(result, viewModel.uiState.value.lastResult)
    }

    @Test
    fun `TestResultFlow FILTERED result sets lastResult in state`() {
        val result = TestResult(MessageHandlingResult.FILTERED, 2000L)
        TestResultFlow.current.value = result
        assertEquals(result, viewModel.uiState.value.lastResult)
    }
}
```

- [ ] **Step 3: Run tests to verify they fail**

```
./gradlew testDebugUnitTest --tests "me.nagaev.veles.testing.viewmodel.TestViewModelTest"
```

Expected: FAIL — `TestViewModel`, `TestState` not found.

- [ ] **Step 4: Create TestState**

```kotlin
// app/src/main/java/me/nagaev/veles/testing/viewmodel/TestState.kt
package me.nagaev.veles.testing.viewmodel

import me.nagaev.veles.common.TestResult

data class TestState(
    val inputText: String = "",
    val lastResult: TestResult? = null
)
```

- [ ] **Step 5: Create TestViewModel**

```kotlin
// app/src/main/java/me/nagaev/veles/testing/viewmodel/TestViewModel.kt
package me.nagaev.veles.testing.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import me.nagaev.veles.common.TestInputPreferences
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.testing.TestNotificationSender

class TestViewModel(
    private val preferences: TestInputPreferences,
    private val sender: TestNotificationSender,
) : ViewModel() {
    private val _uiState = MutableStateFlow(TestState(inputText = preferences.load()))
    val uiState: StateFlow<TestState> = _uiState

    init {
        viewModelScope.launch {
            TestResultFlow.current.collect { result ->
                result?.let {
                    _uiState.value = _uiState.value.copy(lastResult = it)
                }
            }
        }
    }

    fun onTextChanged(text: String) {
        _uiState.value = _uiState.value.copy(inputText = text)
        preferences.save(text)
    }

    fun send() {
        sender.post(_uiState.value.inputText)
    }
}
```

- [ ] **Step 6: Create TestViewModelFactory**

```kotlin
// app/src/main/java/me/nagaev/veles/testing/viewmodel/TestViewModelFactory.kt
package me.nagaev.veles.testing.viewmodel

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import me.nagaev.veles.common.TestInputPreferences
import me.nagaev.veles.testing.TestNotificationSender

class TestViewModelFactory(private val context: Context) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        return if (modelClass.isAssignableFrom(TestViewModel::class.java)) {
            TestViewModel(
                preferences = TestInputPreferences(context),
                sender = TestNotificationSender(context)
            ) as T
        } else {
            throw IllegalArgumentException("ViewModel Not Found")
        }
    }
}
```

- [ ] **Step 7: Run tests to verify they pass**

```
./gradlew testDebugUnitTest --tests "me.nagaev.veles.testing.viewmodel.TestViewModelTest"
```

Expected: 7 tests PASS.

- [ ] **Step 8: Commit**

```
git add app/build.gradle.kts
git add app/src/main/java/me/nagaev/veles/testing/viewmodel/TestState.kt
git add app/src/main/java/me/nagaev/veles/testing/viewmodel/TestViewModel.kt
git add app/src/main/java/me/nagaev/veles/testing/viewmodel/TestViewModelFactory.kt
git add app/src/test/java/me/nagaev/veles/testing/viewmodel/TestViewModelTest.kt
git commit -m "feat: add TestViewModel with state persistence and TestResultFlow collection"
```

---

### Task 5: TestScreen UI + TestTags

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt`
- Create: `app/src/main/java/me/nagaev/veles/testing/ui/TestScreen.kt`

**Interfaces:**
- Consumes: `TestState` from Task 4
- Produces: `TestScreen(state, onTextChanged, onSend, modifier)` composable
- Consumed by: Task 6 (navigation wiring)

- [ ] **Step 1: Add test tags to TestTags.kt**

Replace the full file:

```kotlin
package me.nagaev.veles.common.ui

import me.nagaev.veles.permissions.services.PermissionType

class TestTags {
    companion object {
        const val NOTIFICATION_LISTENER_STATUS = "notification_listener_status"
        const val TEST_INPUT = "test_input"
        const val TEST_SEND_BUTTON = "test_send_button"
        const val TEST_RESULT = "test_result"
        val PERMISSION_STATUS = { state: PermissionType -> "permission_status_${state}" }
    }
}
```

- [ ] **Step 2: Create TestScreen**

```kotlin
// app/src/main/java/me/nagaev/veles/testing/ui/TestScreen.kt
package me.nagaev.veles.testing.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import me.nagaev.veles.common.TestResult
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.testing.viewmodel.TestState

@Composable
fun TestScreen(
    state: TestState,
    onTextChanged: (String) -> Unit,
    onSend: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp)
            .statusBarsPadding()
    ) {
        Text(
            text = "Test",
            style = MaterialTheme.typography.titleLarge,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(vertical = 10.dp)
        )
        OutlinedTextField(
            value = state.inputText,
            onValueChange = onTextChanged,
            label = { Text("Notification text") },
            minLines = 3,
            modifier = Modifier
                .fillMaxWidth()
                .testTag(TestTags.TEST_INPUT)
        )
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = onSend,
            enabled = state.inputText.isNotBlank(),
            modifier = Modifier.testTag(TestTags.TEST_SEND_BUTTON)
        ) {
            Text("Send")
        }
        state.lastResult?.let { result ->
            Spacer(Modifier.height(16.dp))
            ResultBadge(result)
        }
    }
}

@Composable
private fun ResultBadge(result: TestResult) {
    val (label, color) = when (result.result) {
        MessageHandlingResult.ACCEPTED -> "Matched ✓" to Color(0xFF4CAF50)
        MessageHandlingResult.FILTERED -> "No match" to MaterialTheme.colorScheme.onSurfaceVariant
    }
    Text(
        text = label,
        color = color,
        style = MaterialTheme.typography.bodyLarge,
        modifier = Modifier.testTag(TestTags.TEST_RESULT)
    )
}

@Preview(showBackground = true)
@Composable
fun TestScreenPreview() {
    TestScreen(
        state = TestState(inputText = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time."),
        onTextChanged = {},
        onSend = {}
    )
}
```

- [ ] **Step 3: Verify build compiles**

```
./gradlew assembleDebug
```

Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**

```
git add app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt
git add app/src/main/java/me/nagaev/veles/testing/ui/TestScreen.kt
git commit -m "feat: add TestScreen UI with text field, send button, and result badge"
```

---

### Task 6: Navigation wiring

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt`

**Interfaces:**
- Consumes: `TestScreen` (Task 5), `TestViewModel` / `TestViewModelFactory` (Task 4)
- `PermissionsScreen` gains parameter `onNavigateToTest: () -> Unit`

- [ ] **Step 1: Update PermissionsScreen to add the "Test" button**

Replace the full file:

```kotlin
package me.nagaev.veles.permissions.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import me.nagaev.veles.R
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.ui.components.PermissionsList
import me.nagaev.veles.permissions.viewmodal.PermissionsActions
import me.nagaev.veles.permissions.viewmodal.PermissionsState

@Composable
fun PermissionsScreen(
    state: PermissionsState,
    actions: PermissionsActions,
    onNavigateToTest: () -> Unit,
    modifier: Modifier = Modifier
) {
    Box(modifier = modifier.fillMaxSize()) {
        Column {
            Text(
                modifier = Modifier.padding(10.dp).statusBarsPadding(),
                text = stringResource(id = R.string.permissions),
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.primary
            )
            Text(
                modifier = Modifier.padding(10.dp).testTag(TestTags.NOTIFICATION_LISTENER_STATUS),
                text =
                    if (state.notificationListenerEnabled)
                        "Notification listener enabled"
                    else
                        "Notification listener disabled",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.secondary
            )
            TextButton(
                onClick = onNavigateToTest,
                modifier = Modifier.padding(horizontal = 10.dp)
            ) {
                Text("Test")
            }
            PermissionsList(
                permissions = state.permissions,
                actions = actions,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

@Preview(showBackground = true)
@Composable
fun PermissionsScreenPreview() {
    PermissionsScreen(
        state = PermissionsState.Mocked,
        actions = PermissionsActions.Mocked,
        onNavigateToTest = {},
    )
}
```

- [ ] **Step 2: Update VelesPermissionsApp to become a NavHost**

Replace the full file:

```kotlin
package me.nagaev.veles.permissions.ui

import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.tooling.preview.Preview
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import me.nagaev.veles.common.ui.theme.VelesTheme
import me.nagaev.veles.permissions.viewmodal.PermissionsActions
import me.nagaev.veles.permissions.viewmodal.PermissionsState
import me.nagaev.veles.testing.ui.TestScreen
import me.nagaev.veles.testing.viewmodel.TestViewModel
import me.nagaev.veles.testing.viewmodel.TestViewModelFactory

@Composable
fun VelesPermissionsApp(
    permissionsState: PermissionsState,
    permissionsActions: PermissionsActions,
) {
    VelesTheme {
        Surface {
            val navController = rememberNavController()
            NavHost(navController = navController, startDestination = "permissions") {
                composable("permissions") {
                    PermissionsScreen(
                        state = permissionsState,
                        actions = permissionsActions,
                        onNavigateToTest = { navController.navigate("test") }
                    )
                }
                composable("test") {
                    val context = LocalContext.current
                    val factory = remember { TestViewModelFactory(context) }
                    val testViewModel: TestViewModel = viewModel(factory = factory)
                    val testState by testViewModel.uiState.collectAsStateWithLifecycle()
                    TestScreen(
                        state = testState,
                        onTextChanged = testViewModel::onTextChanged,
                        onSend = testViewModel::send
                    )
                }
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
fun VelesAppPreview() {
    VelesPermissionsApp(
        permissionsState = PermissionsState.Mocked,
        permissionsActions = PermissionsActions.Mocked,
    )
}
```

- [ ] **Step 3: Verify build compiles**

```
./gradlew assembleDebug
```

Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Run all JVM unit tests to confirm no regressions**

```
./gradlew testDebugUnitTest
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```
git add app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt
git add app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt
git commit -m "feat: wire Test screen into NavHost and add Test button to Permissions screen"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| Text field editable by user | Task 5 (TestScreen `OutlinedTextField`) |
| Text persisted locally via SharedPreferences | Task 3 (TestInputPreferences), Task 4 (ViewModel init + onTextChanged) |
| Button posts notification | Task 3 (TestNotificationSender), Task 4 (send()) |
| NotificationListener catches + processes via regex | Task 2 (existing service handles all notifications including self) |
| Result highlighted in Test page UI | Task 1 (TestResultFlow), Task 2 (write to flow), Task 4 (collect), Task 5 (ResultBadge) |
| Dedicated Test screen (not inline) | Task 5 + Task 6 |
| Navigation via button on Permissions screen | Task 6 |

**Placeholder scan:** No TBDs, TODOs, or vague steps.

**Type consistency:**
- `TestResult(result: MessageHandlingResult, timestamp: Long)` defined in Task 1, used in Tasks 2, 4, 5 ✓
- `TestResultFlow.current: MutableStateFlow<TestResult?>` defined in Task 1, used in Tasks 2, 4 ✓
- `TestState(inputText: String, lastResult: TestResult?)` defined in Task 4, used in Task 5 ✓
- `TestViewModel.onTextChanged(String)` / `.send()` / `.uiState` defined in Task 4, used in Task 6 ✓
- `TestScreen(state, onTextChanged, onSend, modifier)` defined in Task 5, used in Task 6 ✓
- `TestNotificationSender.post(text: String)` defined in Task 3, used in Task 4 ✓
- `TestInputPreferences.save(String)` / `.load(): String` defined in Task 3, used in Task 4 ✓
