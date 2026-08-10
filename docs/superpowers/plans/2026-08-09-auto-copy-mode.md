# Auto-copy Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in global setting that immediately copies intercepted OTPs using the existing sensitive, two-minute clipboard hygiene behavior.

**Architecture:** Persist the global Boolean in a Proto DataStore through an application-scoped `SettingsRepository`. Keep the synchronous OTP matching chain unchanged; `UserNotifierOtpMessageHandler` launches its work in an injected application scope, reads the setting, copies through a shared `OtpClipboard`, then posts a notification if permitted. The receiver and automatic path both use `OtpClipboard` so sensitive metadata and TTL behavior have one implementation.

**Tech Stack:** Kotlin, Android Proto DataStore, protobuf-javalite, Hilt, coroutines, Compose Material 3, MockK, Robolectric, Compose UI tests.

## Global Constraints

- `minSdk = 33`, `targetSdk = 35`, and `compileSdk = 35` remain unchanged.
- Auto-copy defaults to `false`; any unreadable or corrupted settings data must behave as disabled.
- Auto-copy is global only; do not change `BankHandlerConfig` or run a Room migration.
- Clipboard content must set `ClipDescription.EXTRA_IS_SENSITIVE` and retain the existing two-minute ownership-checked clear.
- Clipboard work occurs before the notification-permission check, so a disabled notification permission never suppresses enabled auto-copy.
- The replacement notification remains available and keeps its Copy action.
- Use Android string resources for every user-visible label and supporting text.
- Do not log raw OTP values beyond the existing `VelesLog.dCopiedOtp` policy.

---

## File Structure

- Create: `app/src/main/proto/settings.proto` - Proto message for the persisted global setting.
- Create: `app/src/main/java/me/nagaev/veles/settings/SettingsSerializer.kt` - Maps the generated Proto message to and from DataStore bytes.
- Create: `app/src/main/java/me/nagaev/veles/settings/SettingsRepository.kt` - Owns reading and updating the auto-copy setting.
- Create: `app/src/main/java/me/nagaev/veles/settings/di/SettingsModule.kt` - Provides the singleton Proto DataStore.
- Create: `app/src/main/java/me/nagaev/veles/common/di/ApplicationScopeModule.kt` - Qualifies and provides the application-lifetime coroutine scope.
- Create: `app/src/main/java/me/nagaev/veles/otp/OtpClipboard.kt` - Owns sensitive clip creation and delayed ownership-checked clearing.
- Create: `app/src/main/java/me/nagaev/veles/permissions/ui/components/AutoCopyOtpCard.kt` - Global setting card and switch for the Permissions screen.
- Create: `app/src/test/java/me/nagaev/veles/settings/SettingsRepositoryTest.kt` - Repository defaults, persistence, and failure fallback tests.
- Create: `app/src/test/java/me/nagaev/veles/otp/OtpClipboardTest.kt` - Clipboard write behavior tests moved out of the receiver.
- Modify: `gradle/libs.versions.toml` - DataStore, protobuf runtime, and protobuf Gradle plugin catalog entries.
- Modify: `build.gradle.kts` - Makes the protobuf plugin alias available to the app module.
- Modify: `app/build.gradle.kts` - Applies/configures protobuf code generation and adds runtime dependencies.
- Modify: `app/src/main/java/me/nagaev/veles/otp/NotificationListenerEntryPoint.kt` - Exposes `OtpClipboard` to the non-Hilt broadcast receiver.
- Modify: `app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt` - Delegates clipboard writing to `OtpClipboard` and retains copied-notification refresh.
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandler.kt` - Reads settings asynchronously and uses the shared helper.
- Modify: `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsState.kt` - Adds the persisted Boolean to UI state and an update action.
- Modify: `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModel.kt` - Observes and updates `SettingsRepository`.
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt` - Places the global setting card on Home.
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt` - Adds a stable selector for the auto-copy switch.
- Modify: `app/src/main/res/values/strings.xml` - Adds the setting title and security/TTL supporting text.
- Modify: `app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt` - Verifies receiver delegation instead of clipboard internals.
- Modify: `app/src/test/java/me/nagaev/veles/otp/ShouldClearClipTest.kt` - Imports the ownership predicate from `OtpClipboard`.
- Modify: `app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt` - Covers enabled, disabled, and notification-disabled flows with a test scope.
- Modify: `app/src/test/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModelSensitiveTest.kt` - Supplies the repository and verifies state/update wiring.
- Modify: `app/src/androidTest/java/me/nagaev/permissions/ui/VelesPermissionsAppTests.kt` - Covers the switch's rendered state and callback.

### Task 1: Add Proto DataStore Settings

**Files:**
- Create: `app/src/main/proto/settings.proto`
- Create: `app/src/main/java/me/nagaev/veles/settings/SettingsSerializer.kt`
- Create: `app/src/main/java/me/nagaev/veles/settings/SettingsRepository.kt`
- Create: `app/src/main/java/me/nagaev/veles/settings/di/SettingsModule.kt`
- Create: `app/src/test/java/me/nagaev/veles/settings/SettingsRepositoryTest.kt`
- Modify: `gradle/libs.versions.toml`
- Modify: `build.gradle.kts`
- Modify: `app/build.gradle.kts`

**Interfaces:**
- Produces: `class SettingsRepository` with `val autoCopyEnabled: Flow<Boolean>`, `suspend fun isAutoCopyEnabled(): Boolean`, and `suspend fun setAutoCopyEnabled(enabled: Boolean)`.
- Produces: generated `me.nagaev.veles.settings.proto.VelesSettings`, containing `bool auto_copy_otp = 1`.
- Produces: singleton `DataStore<VelesSettings>` supplied by Hilt.

- [ ] **Step 1: Add the protobuf build prerequisites and schema**

Add these catalog entries, using `protobuf = "0.9.4"` and `protobuf-java = "4.29.3"` in `[versions]`:

```toml
androidx-datastore = { module = "androidx.datastore:datastore", version = "1.1.3" }
protobuf-javalite = { module = "com.google.protobuf:protobuf-javalite", version.ref = "protobuf-java" }
protobuf = { id = "com.google.protobuf", version.ref = "protobuf" }
```

Apply `alias(libs.plugins.protobuf)` in `app/build.gradle.kts`; declare `implementation(libs.androidx.datastore)` and `implementation(libs.protobuf.javalite)`; configure lite Java generation:

```kotlin
protobuf {
    protoc { artifact = "com.google.protobuf:protoc:4.29.3" }
    generateProtoTasks {
        all().configureEach {
            builtins {
                create("java") { option("lite") }
            }
        }
    }
}
```

Add `alias(libs.plugins.protobuf) apply false` to the root `build.gradle.kts`. Create the schema:

```proto
syntax = "proto3";

option java_package = "me.nagaev.veles.settings.proto";
option java_multiple_files = true;

message VelesSettings {
  bool auto_copy_otp = 1;
}
```

- [ ] **Step 2: Write the failing repository tests**

Use a `DataStoreFactory.create` test store backed by a fresh temporary file and the production `SettingsSerializer`. Test default, persisted update, and unreadable/corrupted data fallback:

```kotlin
@Test
fun `auto-copy defaults to disabled`() = runTest {
    val repository = SettingsRepository(testStore())

    assertFalse(repository.isAutoCopyEnabled())
}

@Test
fun `setting auto-copy persists and is observable`() = runTest {
    val repository = SettingsRepository(testStore())

    repository.setAutoCopyEnabled(true)

    assertTrue(repository.isAutoCopyEnabled())
    assertTrue(repository.autoCopyEnabled.first())
}

@Test
fun `unreadable settings return disabled`() = runTest {
    val repository = SettingsRepository(unreadableStore())

    assertFalse(repository.isAutoCopyEnabled())
}
```

- [ ] **Step 3: Run the repository test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.settings.SettingsRepositoryTest"`

Expected: FAIL because `SettingsSerializer` and `SettingsRepository` do not exist.

- [ ] **Step 4: Implement serializer, repository, and Hilt DataStore provider**

Make the serializer convert malformed protobuf bytes to DataStore corruption:

```kotlin
object SettingsSerializer : Serializer<VelesSettings> {
    override val defaultValue: VelesSettings = VelesSettings.getDefaultInstance()

    override suspend fun readFrom(input: InputStream): VelesSettings =
        try {
            VelesSettings.parseFrom(input)
        } catch (exception: InvalidProtocolBufferException) {
            throw CorruptionException("Cannot read Veles settings.", exception)
        }

    override suspend fun writeTo(t: VelesSettings, output: OutputStream) = t.writeTo(output)
}
```

Make all read failures safe-off and update immutable generated data:

```kotlin
@Singleton
class SettingsRepository @Inject constructor(
    private val dataStore: DataStore<VelesSettings>,
) {
    val autoCopyEnabled: Flow<Boolean> = dataStore.data
        .catch { exception ->
            if (exception is IOException) {
                emit(VelesSettings.getDefaultInstance())
            } else {
                throw exception
            }
        }
        .map { it.autoCopyOtp }

    suspend fun isAutoCopyEnabled(): Boolean = autoCopyEnabled.first()

    suspend fun setAutoCopyEnabled(enabled: Boolean) {
        dataStore.updateData { settings ->
            settings.toBuilder().setAutoCopyOtp(enabled).build()
        }
    }
}
```

Provide a single `DataStoreFactory.create` instance in `SettingsModule` using AndroidX's `dataStoreFile`, which supplies the correct files-directory location:

```kotlin
@Provides
@Singleton
fun provideSettingsDataStore(
    @ApplicationContext context: Context,
): DataStore<VelesSettings> = DataStoreFactory.create(
    serializer = SettingsSerializer,
    produceFile = { context.dataStoreFile("settings.pb") },
)
```

- [ ] **Step 5: Run the repository test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.settings.SettingsRepositoryTest"`

Expected: PASS with default false, update persistence, and safe fallback covered.

- [ ] **Step 6: Commit the settings deliverable**

```bash
git add gradle/libs.versions.toml build.gradle.kts app/build.gradle.kts app/src/main/proto/settings.proto app/src/main/java/me/nagaev/veles/settings app/src/test/java/me/nagaev/veles/settings/SettingsRepositoryTest.kt
git commit -m "feat: add auto-copy settings repository"
```

### Task 2: Centralize Sensitive OTP Clipboard Writes

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/OtpClipboard.kt`
- Create: `app/src/test/java/me/nagaev/veles/otp/OtpClipboardTest.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/NotificationListenerEntryPoint.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt`
- Modify: `app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt`
- Modify: `app/src/test/java/me/nagaev/veles/otp/ShouldClearClipTest.kt`

**Interfaces:**
- Consumes: `VelesLog`, application `Context`, and `R.string.otp_clipboard_label`.
- Produces: `class OtpClipboard` with `fun copy(otp: String): Boolean` and `internal fun shouldClearClip(clip: ClipData?, expectedLabel: String, expectedText: String): Boolean`.
- Produces: `NotificationListenerEntryPoint.otpClipboard(): OtpClipboard` for `CopyDataReceiver`.

- [ ] **Step 1: Write failing helper and receiver-delegation tests**

Move the current sensitive-clip assertions from `CopyDataReceiverTest` to `OtpClipboardTest`. Retain the pure ownership-predicate cases in `ShouldClearClipTest`, but change its static import to `OtpClipboard.shouldClearClip`. Add receiver tests with an injected clipboard mock:

```kotlin
@Test
fun `copy creates a sensitive clip`() {
    val copied = clipboard.copy("123456")

    assertTrue(copied)
    verify { clipData.description.extras = any() }
    assertTrue(extrasSlot.captured.getBoolean(ClipDescription.EXTRA_IS_SENSITIVE))
}

@Test
fun `receiver delegates OTP copy to shared helper`() {
    CopyDataReceiver(logger, notificationBuilder, otpClipboard).onReceive(context, intent)

    verify { otpClipboard.copy(testText) }
}

@Test
fun `missing clipboard service returns false`() {
    every { context.getSystemService(Context.CLIPBOARD_SERVICE) } returns null

    assertFalse(clipboard.copy("123456"))
}
```

- [ ] **Step 2: Run the clipboard tests to verify they fail**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.OtpClipboardTest" --tests "me.nagaev.veles.otp.CopyDataReceiverTest"`

Expected: FAIL because `OtpClipboard` and the receiver override constructor parameter do not exist.

- [ ] **Step 3: Implement `OtpClipboard` and delegate from the receiver**

Move these exact responsibilities from `CopyDataReceiver` into `OtpClipboard.copy`: service lookup, `ClipData.newPlainText`, setting `EXTRA_IS_SENSITIVE`, `setPrimaryClip`, `logger.dCopiedOtp`, and the existing two-minute delayed `shouldClearClip` check. Return `false` only when `ClipboardManager` is unavailable; otherwise return `true` after scheduling the clear.

Add `fun otpClipboard(): OtpClipboard` to `NotificationListenerEntryPoint`. Make `CopyDataReceiver` accept an optional third `otpClipboardOverride` for JVM tests, resolve the production dependency from the entry point when absent, and replace its inline clip construction with:

```kotlin
otpClipboard.copy(otp)
```

Keep the existing notification rebuild and all intent-extra fallbacks unchanged.

- [ ] **Step 4: Run the clipboard tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.OtpClipboardTest" --tests "me.nagaev.veles.otp.CopyDataReceiverTest" --tests "me.nagaev.veles.otp.ShouldClearClipTest"`

Expected: PASS; the receiver delegates, clips are sensitive, and existing ownership behavior remains true.

- [ ] **Step 5: Commit the clipboard deliverable**

```bash
git add app/src/main/java/me/nagaev/veles/otp/OtpClipboard.kt app/src/main/java/me/nagaev/veles/otp/NotificationListenerEntryPoint.kt app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt app/src/test/java/me/nagaev/veles/otp/OtpClipboardTest.kt app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt app/src/test/java/me/nagaev/veles/otp/ShouldClearClipTest.kt
git commit -m "refactor: share OTP clipboard handling"
```

### Task 3: Apply the Setting in OTP Handling

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/common/di/ApplicationScopeModule.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandler.kt`
- Modify: `app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt`
- Modify: `app/src/androidTest/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt`

**Interfaces:**
- Consumes: `SettingsRepository.isAutoCopyEnabled()`, `OtpClipboard.copy(otp)`, and `@ApplicationScope CoroutineScope`.
- Produces: automatic clipboard copying before notification posting; `copied` reflects whether the auto-copy write succeeded.
- Produces: `@ApplicationScope` qualifier and one singleton `CoroutineScope(SupervisorJob() + Dispatchers.Default)`.

- [ ] **Step 1: Write failing notifier tests with a deterministic scope**

Update the JVM test fixture to inject `SettingsRepository`, `OtpClipboard`, and `this` from `runTest` as the handler scope. Add these cases:

```kotlin
@Test
fun `enabled auto-copy writes OTP before copied notification`() = runTest {
    coEvery { settingsRepository.isAutoCopyEnabled() } returns true
    every { otpClipboard.copy("123456") } returns true

    handler(this).onOtpMessageReceived(defaultMessage)
    advanceUntilIdle()

    verifyOrder {
        otpClipboard.copy("123456")
        notificationManager.notify(defaultMessage.hashCode(), any())
    }
    assertEquals("Copy 123456 Copied ✓", postedActionTitle())
}

@Test
fun `disabled auto-copy leaves clipboard untouched and posts normal notification`() = runTest {
    coEvery { settingsRepository.isAutoCopyEnabled() } returns false

    handler(this).onOtpMessageReceived(defaultMessage)
    advanceUntilIdle()

    verify(exactly = 0) { otpClipboard.copy(any()) }
    assertEquals("Copy 123456", postedActionTitle())
}

@Test
fun `enabled auto-copy writes even when notifications are disabled`() = runTest {
    coEvery { settingsRepository.isAutoCopyEnabled() } returns true
    every { otpClipboard.copy("123456") } returns true
    every { notificationManager.areNotificationsEnabled() } returns false

    handler(this).onOtpMessageReceived(defaultMessage)
    advanceUntilIdle()

    verify { otpClipboard.copy("123456") }
    verify(exactly = 0) { notificationManager.notify(any(), any()) }
}
```

- [ ] **Step 2: Run the notifier test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandlerTest"`

Expected: FAIL because the handler has no settings, clipboard, or coroutine-scope dependencies.

- [ ] **Step 3: Implement the application scope and ordered notifier work**

Define the qualifier and provider:

```kotlin
@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class ApplicationScope

@Provides
@Singleton
@ApplicationScope
fun provideApplicationScope(): CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
```

Inject the scope, `SettingsRepository`, and `OtpClipboard` into `UserNotifierOtpMessageHandler`. Replace direct notification work with a scope launch that performs this exact order:

```kotlin
applicationScope.launch {
    val copied = if (settingsRepository.isAutoCopyEnabled()) {
        otpClipboard.copy(message.otp.value)
    } else {
        false
    }
    postNotification(message, copied)
}
```

Keep `postNotification` responsible only for the existing `NotificationManagerCompat.areNotificationsEnabled()` guard and `OtpNotificationBuilder.build(..., copied = copied)`. Do not make `OtpMessageHandler` or the regex handler suspending.

- [ ] **Step 4: Run notifier tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandlerTest"`

Expected: PASS with enabled, disabled, notification-disabled, and existing notification intent coverage.

- [ ] **Step 5: Run the instrumented notifier test**

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandlerTest`

Expected: PASS on a connected emulator or device; it verifies the production notification path still posts on Android.

- [ ] **Step 6: Commit the automatic copy deliverable**

```bash
git add app/src/main/java/me/nagaev/veles/common/di/ApplicationScopeModule.kt app/src/main/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandler.kt app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt app/src/androidTest/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt
git commit -m "feat: auto-copy intercepted OTPs"
```

### Task 4: Expose the Global Auto-copy Switch

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/permissions/ui/components/AutoCopyOtpCard.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsState.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModel.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt`
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt`
- Modify: `app/src/main/res/values/strings.xml`
- Modify: `app/src/test/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModelSensitiveTest.kt`
- Modify: `app/src/androidTest/java/me/nagaev/permissions/ui/VelesPermissionsAppTests.kt`

**Interfaces:**
- Consumes: `SettingsRepository.autoCopyEnabled` and `SettingsRepository.setAutoCopyEnabled(enabled)`.
- Produces: `PermissionsState.autoCopyEnabled: Boolean`, `PermissionsActions.setAutoCopyEnabled: (Boolean) -> Unit`, and `TestTags.AUTO_COPY_OTP_SWITCH`.
- Produces: `AutoCopyOtpCard(enabled: Boolean, onEnabledChange: (Boolean) -> Unit, modifier: Modifier = Modifier)`.

- [ ] **Step 1: Add failing ViewModel and Compose UI tests**

In the ViewModel test, inject a mocked repository exposing a `MutableStateFlow(false)`. Verify its flow appears in `uiState` and changing the action delegates to the repository:

```kotlin
@Test
fun `auto-copy setting follows repository state`() = runTest {
    val enabled = MutableStateFlow(false)
    every { settingsRepository.autoCopyEnabled } returns enabled

    val vm = viewModel(settingsRepository = settingsRepository)
    enabled.value = true

    assertEquals(true, vm.uiState.value.autoCopyEnabled)
}

@Test
fun `set auto-copy delegates to settings repository`() = runTest {
    val vm = viewModel(settingsRepository = settingsRepository)

    vm.setAutoCopyEnabled(true)
    runCurrent()

    coVerify { settingsRepository.setAutoCopyEnabled(true) }
}
```

In the Compose test, render `VelesPermissionsApp` twice with `autoCopyEnabled` false and true, assert `TestTags.AUTO_COPY_OTP_SWITCH` is respectively off and on, then click it and verify `permissionsActions.setAutoCopyEnabled(true)`.

- [ ] **Step 2: Run the ViewModel and Compose tests to verify they fail**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.permissions.viewmodal.PermissionsViewModelSensitiveTest"`

Expected: FAIL because `PermissionsState`, `PermissionsActions`, and `PermissionsViewModel` do not expose auto-copy.

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.permissions.ui.VelesPermissionsAppTests`

Expected: FAIL because the setting card and stable test tag do not exist.

- [ ] **Step 3: Implement state collection, update action, resources, and card**

Add `autoCopyEnabled: Boolean = false` to `PermissionsState`. Add this action to the interface and its `Mocked` implementation:

```kotlin
val setAutoCopyEnabled: (Boolean) -> Unit
```

Inject `SettingsRepository` into `PermissionsViewModel`. In `init`, collect `autoCopyEnabled` in `viewModelScope` and update only `PermissionsState.autoCopyEnabled`. Implement:

```kotlin
override val setAutoCopyEnabled: (Boolean) -> Unit = { enabled ->
    viewModelScope.launch { settingsRepository.setAutoCopyEnabled(enabled) }
}
```

Create `AutoCopyOtpCard` with the same `Card`, `Row`, surface color, type scale, and horizontal spacing as `AccessNotificationPermission`. Use a regular `Switch`, apply `TestTags.AUTO_COPY_OTP_SWITCH` to it, and add it below `ListenerStatusCard` in `PermissionsScreen` before the sensitive-notification card.

Add these strings:

```xml
<string name="auto_copy_otp_title">Automatically copy OTP to clipboard</string>
<string name="auto_copy_otp_description">Copies intercepted OTPs automatically. Codes are marked sensitive and cleared from the clipboard after about 2 minutes.</string>
```

- [ ] **Step 4: Run the ViewModel and Compose tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.permissions.viewmodal.PermissionsViewModelSensitiveTest"`

Expected: PASS with repository state and updates covered.

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.permissions.ui.VelesPermissionsAppTests`

Expected: PASS with switch on/off rendering and callback coverage.

- [ ] **Step 5: Commit the settings UI deliverable**

```bash
git add app/src/main/java/me/nagaev/veles/permissions/ui/components/AutoCopyOtpCard.kt app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsState.kt app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModel.kt app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt app/src/main/res/values/strings.xml app/src/test/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModelSensitiveTest.kt app/src/androidTest/java/me/nagaev/permissions/ui/VelesPermissionsAppTests.kt
git commit -m "feat: add auto-copy setting"
```

### Task 5: Run the Feature Regression Suite

**Files:**
- Modify: no production files unless verification exposes a concrete defect.

**Interfaces:**
- Consumes: all tasks above.
- Produces: verified unit, static-analysis, and device test evidence for the feature.

- [ ] **Step 1: Run targeted JVM tests**

Run:

```bash
./gradlew testDebugUnitTest --tests "me.nagaev.veles.settings.SettingsRepositoryTest" --tests "me.nagaev.veles.otp.OtpClipboardTest" --tests "me.nagaev.veles.otp.CopyDataReceiverTest" --tests "me.nagaev.veles.otp.ShouldClearClipTest" --tests "me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandlerTest" --tests "me.nagaev.veles.permissions.viewmodal.PermissionsViewModelSensitiveTest"
```

Expected: PASS.

- [ ] **Step 2: Run formatting, static analysis, and the full JVM suite**

Run:

```bash
./gradlew spotlessCheck detekt testDebugUnitTest
```

Expected: PASS with no formatting or detekt violations.

- [ ] **Step 3: Run the relevant instrumented tests**

Run:

```bash
./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.permissions.ui.VelesPermissionsAppTests
./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandlerTest
```

Expected: PASS on a connected emulator or device. If no device is available, record that only this device-dependent verification could not run.

- [ ] **Step 4: Build the debug APK**

Run: `./gradlew assembleDebug`

Expected: PASS and `app/build/outputs/apk/debug/app-debug.apk` exists.

## Plan Self-Review

- Spec coverage: Task 1 implements the Proto DataStore default/failure behavior; Task 2 preserves sensitive clip/TTL behavior through a shared helper; Task 3 covers automatic copy ordering and disabled notifications; Task 4 provides the global opt-in and explanatory UI; Task 5 verifies all specified paths.
- No placeholders: all paths, commands, generated names, interfaces, and test cases are specified.
- Type consistency: `SettingsRepository.autoCopyEnabled`, `isAutoCopyEnabled`, `setAutoCopyEnabled`, `OtpClipboard.copy`, `PermissionsState.autoCopyEnabled`, `PermissionsActions.setAutoCopyEnabled`, and `TestTags.AUTO_COPY_OTP_SWITCH` use identical names throughout.
