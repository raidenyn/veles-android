# Design: Hilt Dependency Injection

**Issue:** https://github.com/raidenyn/veles-android/issues/15
**Date:** 2026-07-07
**Status:** Approved

## Goal

Replace all manual dependency wiring with Hilt:

- 4 hand-rolled `ViewModelProvider.Factory` classes
- The double-checked-locking singleton in `BankHandlerDatabase.getInstance()`
- Global `object` singletons `TestResultFlow` and `RedactionStateFlow` (reset by convention in tests)
- `NotificationListener`'s nullable constructor parameters with hand-built production fallbacks

## Constraints and decisions

- **Full migration in one branch** — all five targets from the issue, no half-migrated state.
- **Tests keep plain constructor injection.** No Hilt test infrastructure (`HiltAndroidRule`, `@BindValue`, test modules). Constructors stay public; Hilt replaces only production wiring. Existing unit and instrumented tests continue to pass mockk fakes directly.
- **KSP, not kapt** — the project already uses KSP for Room (Kotlin 2.1.10, AGP 8.9.0, Gradle 8.11.1; all Hilt-compatible).
- **Modules grouped by responsibility**, placed in `di/` sub-packages mirroring the existing package boundaries.

## 1. Hilt setup

- Add to `gradle/libs.versions.toml`: Hilt version, `hilt-android`, `hilt-compiler` libraries, `hilt` Gradle plugin.
- `app/build.gradle.kts`: apply Hilt plugin, add `implementation(libs.hilt.android)` and `ksp(libs.hilt.compiler)`.
- Root `build.gradle.kts`: `alias(libs.plugins.hilt) apply false`.
- `VelesApplication` (`app/src/main/java/me/nagaev/veles/VelesApplication.kt`): annotate `@HiltAndroidApp`.
- `PermissionsActivity`: annotate `@AndroidEntryPoint` so `by viewModels()` and `hiltViewModel()` inside its Compose content resolve Hilt ViewModels.

## 2. Modules

All in `SingletonComponent`.

### `otp/config/di/DatabaseModule.kt`
- `@Provides @Singleton fun provideDatabase(@ApplicationContext context: Context): BankHandlerDatabase` — same `Room.databaseBuilder` call as today: database name `bank_handler_configs.db`, `MIGRATION_1_2`, `SeedCallback`.
- `@Provides fun provideDao(db: BankHandlerDatabase): BankHandlerConfigDao`.
- Delete the `companion object` singleton (`INSTANCE`, `getInstance()`) from `BankHandlerDatabase`; keep migrations and `SeedCallback` (move them to the module or leave as companion members — implementer's choice, but `getInstance` must be gone).

### `common/di/LoggingModule.kt`
- `@Binds LogConfig` → `SharedPreferencesLogConfig`.
- `@Binds LogSink` → `AndroidLogSink`.
- `@Provides @DebugEnabled Boolean` = `BuildConfig.DEBUG` (small `@Qualifier` annotation `@DebugEnabled` defined alongside).
- `VelesLog` becomes `@Singleton class VelesLog @Inject constructor(sink: LogSink, logConfig: LogConfig, @DebugEnabled debugEnabled: Boolean)`.

### `common/di/DispatchersModule.kt`
- `@Qualifier annotation class IoDispatcher` and `@Provides @IoDispatcher fun provideIoDispatcher(): CoroutineDispatcher = Dispatchers.IO`. Needed because Hilt ignores Kotlin default parameter values: `BankConfigsViewModel`'s `ioDispatcher: CoroutineDispatcher = Dispatchers.IO` parameter requires a binding. The default value stays so unit tests can keep omitting it.

### `permissions/di/PermissionsModule.kt`
- `@Provides fun provideListenerComponentName(@ApplicationContext context: Context): ComponentName` = `ComponentName(context.packageName, "me.nagaev.veles.otp.NotificationListener")`.
- `@Provides fun provideRedactionPath(componentName: ComponentName): NotificationRedactionPath` = `NotificationRedactionPath.from(Build.MANUFACTURER, componentName)`.

No module is needed for the flow singletons (section 4) — `@Singleton class ... @Inject constructor()` is discovered by Hilt without a `@Provides`.

## 3. Context-based classes: annotate constructors

- `BankHandlerRepository`: change constructor from `(context: Context)` to `@Inject constructor(private val dao: BankHandlerConfigDao)` — removes its Context dependency entirely. Update all construction sites (factories being deleted, `NotificationListener` fallback path).
- `NotificationStatePreferences`, `TestInputPreferences`, `TestNotificationSender`, `SharedPreferencesLogConfig`, `UserNotifierOtpMessageHandler`: add `@Inject constructor(@ApplicationContext context: Context)`. No behavior change.

## 4. Flow singletons become injectable classes

`TestResultFlow` and `RedactionStateFlow` change from Kotlin `object` to:

```kotlin
@Singleton
class TestResultFlow @Inject constructor() {
    val current: MutableStateFlow<TestResult?> = MutableStateFlow(null)
}

@Singleton
class RedactionStateFlow @Inject constructor() {
    val current: MutableStateFlow<RedactionState> = MutableStateFlow(RedactionState.Unknown)
}
```

Consumers (`NotificationListener`, `TestViewModel`, `PermissionsViewModel`) receive them via injection instead of referencing the global. Tests construct fresh instances per test instead of resetting a shared global in `@Before`; update the existing `@Before` reset code accordingly where those tests are touched.

## 5. ViewModels

Delete all four factory classes: `BankConfigsViewModelFactory`, `BankConfigEditViewModelFactory`, `TestViewModelFactory`, `PermissionsViewModelFactory`.

- **`BankConfigsViewModel`** → `@HiltViewModel @Inject constructor(repository: BankHandlerRepository, @IoDispatcher ioDispatcher: CoroutineDispatcher = Dispatchers.IO)`. The default value stays for tests; Hilt supplies the binding from `DispatchersModule`. Obtained via `hiltViewModel()` in `VelesPermissionsApp`.
- **`TestViewModel`** → `@HiltViewModel @Inject constructor(preferences: TestInputPreferences, sender: TestNotificationSender, logConfig: SharedPreferencesLogConfig, testResultFlow: TestResultFlow)`. Keeps the concrete `SharedPreferencesLogConfig` (not the `LogConfig` interface) because it calls `saveRawContentEnabled()`, which only exists on the concrete class. Obtained via `hiltViewModel()`.
- **`BankConfigEditViewModel`** → `@HiltViewModel @Inject constructor(savedStateHandle: SavedStateHandle, repository: BankHandlerRepository)`. Reads the existing `id` nav argument (already declared in the `bank-config-edit?id={id}` route with `defaultValue = -1L`) from `savedStateHandle.get<Long>("id")`, mapping `-1L` to `null`. Obtained via `hiltViewModel()`.
- **`PermissionsViewModel`** → assisted injection:

```kotlin
@HiltViewModel(assistedFactory = PermissionsViewModel.Factory::class)
class PermissionsViewModel @AssistedInject constructor(
    private val notificationStatePreferences: NotificationStatePreferences,
    private val redactionPath: NotificationRedactionPath,
    private val componentName: ComponentName,
    private val redactionStateFlow: RedactionStateFlow,
    @Assisted private val permissionsProvider: PermissionsProvider,
    @Assisted private val openSettings: (Intent) -> Unit,
) : ViewModel(), PermissionsActions {
    @AssistedFactory
    interface Factory {
        fun create(
            permissionsProvider: PermissionsProvider,
            openSettings: (Intent) -> Unit,
        ): PermissionsViewModel
    }
}
```

  `PermissionsActivity` builds the Activity-scoped pieces (`ActivityProviderImpl(this)`, `RequestPermissionLauncher.create(this)`, the two `PermissionProvider`s, `PermissionsProviderImpl`) and obtains the ViewModel via `viewModels { ... }` with `HiltViewModelExtensions` / `hiltViewModel(creationCallback = ...)`, passing the assembled `PermissionsProvider` and `openSettings = { startActivity(it) }` through the assisted factory. The permission-provider assembly logic moves from the deleted factory into a private helper in `PermissionsActivity`.

## 6. NotificationListener

Keeps its constructor shape (nullable params, defaults) so instrumented tests are untouched. The production fallback path changes to pull from Hilt instead of hand-building:

```kotlin
@EntryPoint
@InstallIn(SingletonComponent::class)
interface NotificationListenerEntryPoint {
    fun notificationStatePreferences(): NotificationStatePreferences
    fun bankHandlerRepository(): BankHandlerRepository
    fun userNotifierOtpMessageHandler(): UserNotifierOtpMessageHandler
    fun velesLog(): VelesLog
    fun testResultFlow(): TestResultFlow
    fun redactionStateFlow(): RedactionStateFlow
}
```

In `onCreate()` (or lazily on first use), when a constructor parameter is null, resolve it via `EntryPointAccessors.fromApplication(applicationContext, NotificationListenerEntryPoint::class.java)`. The `HandlerChainReloader` construction stays in the listener but consumes the injected repository and notifier. No `@AndroidEntryPoint` on the service. Because flows are no longer globals, the constructor gains nullable `testResultFlow`/`redactionStateFlow` parameters (defaulting to null → resolved from the entry point), which instrumented tests may pass explicitly.

The entry-point interface lives next to the listener in `otp/`.

## 7. Testing

- No new test infrastructure. All existing tests keep direct constructor injection with mockk.
- Tests that reset `TestResultFlow`/`RedactionStateFlow` globals in `@Before` are updated to construct fresh instances and pass them to the class under test.
- Verification: `./gradlew testDebugUnitTest` and `./gradlew connectedDebugAndroidTest` must pass; `./gradlew assembleDebug` must build; manual smoke test of the Test screen flow (send test notification → "Matched" badge) confirms the end-to-end wiring.

## 8. Migration order

Each step keeps the app compiling:

1. Gradle: Hilt plugin + dependencies; `@HiltAndroidApp` on `VelesApplication`; `@AndroidEntryPoint` on `PermissionsActivity`.
2. `DatabaseModule` + `BankHandlerRepository` constructor change; delete `getInstance()`.
3. `LoggingModule` + `VelesLog` injectable; flow singletons → `@Singleton` classes; update consumers and tests.
4. ViewModels: `BankConfigsViewModel`, `TestViewModel`, `BankConfigEditViewModel` (`SavedStateHandle`), `PermissionsViewModel` (assisted); delete all four factories; update `VelesPermissionsApp` and `PermissionsActivity`.
5. `PermissionsModule` (ComponentName + RedactionPath providers).
6. `NotificationListener` entry point + fallback rewiring.
7. Full test run + manual smoke test.

## Out of scope

- Hilt test modules / `HiltAndroidRule` adoption.
- Multi-module Gradle restructuring.
- Any behavior change to OTP handling, notifications, or UI.
