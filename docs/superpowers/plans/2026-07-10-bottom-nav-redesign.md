# Bottom-Nav Shell + Icon Palette Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle Veles' three screens into a persistent bottom-navigation shell with the emerald/gold brand palette from the launcher icon, per issue #39 and `docs/superpowers/specs/2026-07-10-bottom-nav-redesign-design.md`, with zero behavior changes.

**Architecture:** Single flat Navigation-Compose `NavHost` (routes unchanged) wrapped in a `Scaffold` whose `bottomBar` renders a Material 3 `NavigationBar` only on the three top-level routes. Static `lightColorScheme`/`darkColorScheme` replace Material You dynamic color; the gold "success" accent maps onto the `tertiary` role. One data-shape addition: `MessageHandlingResult.otpMessage` so the Test screen can display the extracted OTP/amount/merchant.

**Tech Stack:** Kotlin, Jetpack Compose (Material 3), Navigation-Compose, Hilt, JUnit4 + MockK (unit), Compose UI test (instrumented).

## Global Constraints

- Branch: all work on `redesign/39-bottom-nav-shell` (already created; spec + this plan are committed there).
- Spec is authoritative for every hex value, dp/sp size, and copy string: `docs/superpowers/specs/2026-07-10-bottom-nav-redesign-design.md`. Issue #39 body is the upstream source.
- Wherever the issue says `success`/`successContainer`/`onSuccessContainer`, use `tertiary`/`tertiaryContainer`/`onTertiaryContainer`.
- No behavior changes: reuse `PermissionsState`, `BankConfigsState`, `BankConfigEditState`, `TestState`, all ViewModel actions, all dialogs' data flows. Never reimplement regex matching in the UI.
- Existing `TestTags` must keep working except `TEST_RESULT_TEMPLATE`, which Task 7 deletes (only used in `TestScreenComposeTest`, updated in the same task).
- Verification commands (run from repo root; use `./gradlew` in Git Bash or `.\gradlew` in PowerShell):
  - `./gradlew spotlessCheck detekt` — style gates, must pass before every commit.
  - `./gradlew testDebugUnitTest` — JVM unit tests.
  - `./gradlew assembleDebug` — compile gate for Compose UI work.
  - `./gradlew connectedDebugAndroidTest` — instrumented tests; **only if an emulator/device is attached**. Always write/update the instrumented tests regardless; if no device is available, state that they were not run.
- Keep existing code style: trailing commas, explicit imports (no wildcards), `@Suppress("LongParameterList")` where already present.
- Commit after every task (frequent small commits).

---

### Task 1: Expose extracted `OtpMessage` on `MessageHandlingResult`

The Test screen's matched card (Task 7) needs the extracted OTP code, amount, and merchant. Today `RegexMessageHandler` sends the `OtpMessage` only to the notifier; the result carries just `status` + `matchedTemplateName`.

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/MessageHandler.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt:38`
- Test: `app/src/test/java/me/nagaev/veles/otp/handlers/RegexMessageHandlerTest.kt`

**Interfaces:**
- Consumes: existing `OtpMessage`/`Otp`/`Money` data classes in `OtpMessageHandler.kt`.
- Produces: `MessageHandlingResult(status: Status, matchedTemplateName: String?, otpMessage: OtpMessage? = null)`. Task 7 reads `result.handlingResult.otpMessage`.

- [ ] **Step 1: Write two failing tests** — append to `RegexMessageHandlerTest.kt` (inside the class, after the last test):

```kotlin
    @Test
    fun `matched result carries the extracted otp message`() {
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(defaultMessage)

        assert(
            result.otpMessage ==
                OtpMessage(
                    otp = Otp(value = "079853", id = "HStX"),
                    pay = Money(amount = BigDecimal("319.93"), currencyCode = "THB"),
                    merchant = "AMP*AIS SERVICES",
                ),
        )
    }

    @Test
    fun `filtered result carries no otp message`() {
        val message = defaultMessage.copy(text = "")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result.otpMessage == null)
    }
```

- [ ] **Step 2: Run tests to verify failure**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"`
Expected: FAIL — `Unresolved reference: otpMessage` (compile error counts as the failing state).

- [ ] **Step 3: Add the field** — in `MessageHandler.kt`, replace the `MessageHandlingResult` data class with:

```kotlin
data class MessageHandlingResult(
    val status: Status,
    val matchedTemplateName: String?,
    val otpMessage: OtpMessage? = null,
) {
    enum class Status { ACCEPTED, FILTERED }

    companion object {
        val ACCEPTED = MessageHandlingResult(Status.ACCEPTED, null)
        val FILTERED = MessageHandlingResult(Status.FILTERED, null)
    }
}
```

In `RegexMessageHandler.kt`, the ACCEPTED branch currently builds the `OtpMessage` inline inside the `notifier` call. Extract it to a local and pass it to the result — replace the `return if (...)` block body:

```kotlin
        return if (otp != null && money != null && merchant != null) {
            val otpMessage =
                OtpMessage(
                    otp = otp,
                    pay = money,
                    merchant = merchant,
                )
            notifier.onOtpMessageReceived(otpMessage)
            MessageHandlingResult(MessageHandlingResult.Status.ACCEPTED, name, otpMessage)
        } else {
            MessageHandlingResult.FILTERED
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"`
Expected: PASS (all 17 tests, including the 2 new ones).

- [ ] **Step 5: Full unit suite + style, then commit**

Run: `./gradlew spotlessCheck detekt testDebugUnitTest`
Expected: all PASS.

```bash
git add app/src/main/java/me/nagaev/veles/otp/handlers/MessageHandler.kt app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt app/src/test/java/me/nagaev/veles/otp/handlers/RegexMessageHandlerTest.kt
git commit -m "feat: expose extracted OtpMessage on MessageHandlingResult"
```

---

### Task 2: Replace dynamic color with the static emerald/gold brand palette

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/theme/Colors.kt` (full replace)
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/theme/Theme.kt` (full replace)

**Interfaces:**
- Consumes: nothing new.
- Produces: `VelesTheme` now always applies `LightColors`/`DarkColors`; `colorScheme.tertiary*` is the gold success accent; `surfaceContainerLow/…/Highest` and `outlineVariant` are set. All later tasks rely on these roles.

No unit test exists for color tokens (pure declarative constants); the test cycle is the compile + style gate, plus visual check on device if available.

- [ ] **Step 1: Replace `Colors.kt` entirely with:**

```kotlin
package me.nagaev.veles.common.ui.theme

import androidx.compose.ui.graphics.Color

// Palette derived from the launcher icon (deep emerald shield + gold bull/runes), issue #39.
// The gold "success" accent is mapped onto the tertiary role — M3 has no success role.

val md_theme_light_primary = Color(0xFF1E6B47)
val md_theme_light_onPrimary = Color(0xFFFFFFFF)
val md_theme_light_primaryContainer = Color(0xFFC9F2DC)
val md_theme_light_onPrimaryContainer = Color(0xFF00210F)
val md_theme_light_secondary = Color(0xFF6B5514)
val md_theme_light_onSecondary = Color(0xFFFFFFFF)
val md_theme_light_secondaryContainer = Color(0xFFF6E3AE)
val md_theme_light_onSecondaryContainer = Color(0xFF221A00)
val md_theme_light_tertiary = Color(0xFF8A6D14)
val md_theme_light_onTertiary = Color(0xFFFFFFFF)
val md_theme_light_tertiaryContainer = Color(0xFFF7E4A8)
val md_theme_light_onTertiaryContainer = Color(0xFF241A00)
val md_theme_light_error = Color(0xFFBA1A1A)
val md_theme_light_errorContainer = Color(0xFFFFDAD6)
val md_theme_light_onError = Color(0xFFFFFFFF)
val md_theme_light_onErrorContainer = Color(0xFF410002)
val md_theme_light_background = Color(0xFFFBFAF3)
val md_theme_light_onBackground = Color(0xFF1A1C16)
val md_theme_light_surface = Color(0xFFFBFAF3)
val md_theme_light_onSurface = Color(0xFF1A1C16)
val md_theme_light_surfaceVariant = Color(0xFFDDE5DA)
val md_theme_light_onSurfaceVariant = Color(0xFF43483F)
val md_theme_light_outline = Color(0xFF73796C)
val md_theme_light_outlineVariant = Color(0xFFDDE5DA)
val md_theme_light_surfaceContainerLow = Color(0xFFF4F3EA)
val md_theme_light_surfaceContainer = Color(0xFFEEEDE1)
val md_theme_light_surfaceContainerHigh = Color(0xFFE8E7D9)
val md_theme_light_surfaceContainerHighest = Color(0xFFE2E1D1)
val md_theme_light_inverseSurface = Color(0xFF2F312A)
val md_theme_light_inverseOnSurface = Color(0xFFF1F1E7)
val md_theme_light_inversePrimary = Color(0xFF8FDBB0)
val md_theme_light_surfaceTint = md_theme_light_primary

val md_theme_dark_primary = Color(0xFF8FDBB0)
val md_theme_dark_onPrimary = Color(0xFF003920)
val md_theme_dark_primaryContainer = Color(0xFF00522F)
val md_theme_dark_onPrimaryContainer = Color(0xFFABF3C6)
val md_theme_dark_secondary = Color(0xFFE4C567)
val md_theme_dark_onSecondary = Color(0xFF3B2E00)
val md_theme_dark_secondaryContainer = Color(0xFF544000)
val md_theme_dark_onSecondaryContainer = Color(0xFFFFE18C)
val md_theme_dark_tertiary = Color(0xFFD8B84A)
val md_theme_dark_onTertiary = Color(0xFF3B2E00)
val md_theme_dark_tertiaryContainer = Color(0xFF544000)
val md_theme_dark_onTertiaryContainer = Color(0xFFFFE18C)
val md_theme_dark_error = Color(0xFFFFB4AB)
val md_theme_dark_errorContainer = Color(0xFF93000A)
val md_theme_dark_onError = Color(0xFF690005)
val md_theme_dark_onErrorContainer = Color(0xFFFFDAD6)
val md_theme_dark_background = Color(0xFF12140F)
val md_theme_dark_onBackground = Color(0xFFE3E3D9)
val md_theme_dark_surface = Color(0xFF12140F)
val md_theme_dark_onSurface = Color(0xFFE3E3D9)
val md_theme_dark_surfaceVariant = Color(0xFF414941)
val md_theme_dark_onSurfaceVariant = Color(0xFFC1C9BE)
val md_theme_dark_outline = Color(0xFF8B938A)
val md_theme_dark_outlineVariant = Color(0xFF414941)
val md_theme_dark_surfaceContainerLow = Color(0xFF1A1C16)
val md_theme_dark_surfaceContainer = Color(0xFF1E211A)
val md_theme_dark_surfaceContainerHigh = Color(0xFF282B23)
val md_theme_dark_surfaceContainerHighest = Color(0xFF33362D)
val md_theme_dark_inverseSurface = Color(0xFFE3E3D9)
val md_theme_dark_inverseOnSurface = Color(0xFF2F312A)
val md_theme_dark_inversePrimary = Color(0xFF1E6B47)
val md_theme_dark_surfaceTint = md_theme_dark_primary
```

(Inverse/tint roles are not in the issue; they are derived consistently — inversePrimary = the opposite scheme's primary, surfaceTint = primary.)

- [ ] **Step 2: Replace `Theme.kt` entirely with:**

```kotlin
package me.nagaev.veles.common.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

val LightColors = lightColorScheme(
    primary = md_theme_light_primary,
    onPrimary = md_theme_light_onPrimary,
    primaryContainer = md_theme_light_primaryContainer,
    onPrimaryContainer = md_theme_light_onPrimaryContainer,
    secondary = md_theme_light_secondary,
    onSecondary = md_theme_light_onSecondary,
    secondaryContainer = md_theme_light_secondaryContainer,
    onSecondaryContainer = md_theme_light_onSecondaryContainer,
    tertiary = md_theme_light_tertiary,
    onTertiary = md_theme_light_onTertiary,
    tertiaryContainer = md_theme_light_tertiaryContainer,
    onTertiaryContainer = md_theme_light_onTertiaryContainer,
    error = md_theme_light_error,
    errorContainer = md_theme_light_errorContainer,
    onError = md_theme_light_onError,
    onErrorContainer = md_theme_light_onErrorContainer,
    background = md_theme_light_background,
    onBackground = md_theme_light_onBackground,
    surface = md_theme_light_surface,
    onSurface = md_theme_light_onSurface,
    surfaceVariant = md_theme_light_surfaceVariant,
    onSurfaceVariant = md_theme_light_onSurfaceVariant,
    outline = md_theme_light_outline,
    outlineVariant = md_theme_light_outlineVariant,
    surfaceContainerLow = md_theme_light_surfaceContainerLow,
    surfaceContainer = md_theme_light_surfaceContainer,
    surfaceContainerHigh = md_theme_light_surfaceContainerHigh,
    surfaceContainerHighest = md_theme_light_surfaceContainerHighest,
    inverseSurface = md_theme_light_inverseSurface,
    inverseOnSurface = md_theme_light_inverseOnSurface,
    inversePrimary = md_theme_light_inversePrimary,
    surfaceTint = md_theme_light_surfaceTint,
)

val DarkColors = darkColorScheme(
    primary = md_theme_dark_primary,
    onPrimary = md_theme_dark_onPrimary,
    primaryContainer = md_theme_dark_primaryContainer,
    onPrimaryContainer = md_theme_dark_onPrimaryContainer,
    secondary = md_theme_dark_secondary,
    onSecondary = md_theme_dark_onSecondary,
    secondaryContainer = md_theme_dark_secondaryContainer,
    onSecondaryContainer = md_theme_dark_onSecondaryContainer,
    tertiary = md_theme_dark_tertiary,
    onTertiary = md_theme_dark_onTertiary,
    tertiaryContainer = md_theme_dark_tertiaryContainer,
    onTertiaryContainer = md_theme_dark_onTertiaryContainer,
    error = md_theme_dark_error,
    errorContainer = md_theme_dark_errorContainer,
    onError = md_theme_dark_onError,
    onErrorContainer = md_theme_dark_onErrorContainer,
    background = md_theme_dark_background,
    onBackground = md_theme_dark_onBackground,
    surface = md_theme_dark_surface,
    onSurface = md_theme_dark_onSurface,
    surfaceVariant = md_theme_dark_surfaceVariant,
    onSurfaceVariant = md_theme_dark_onSurfaceVariant,
    outline = md_theme_dark_outline,
    outlineVariant = md_theme_dark_outlineVariant,
    surfaceContainerLow = md_theme_dark_surfaceContainerLow,
    surfaceContainer = md_theme_dark_surfaceContainer,
    surfaceContainerHigh = md_theme_dark_surfaceContainerHigh,
    surfaceContainerHighest = md_theme_dark_surfaceContainerHighest,
    inverseSurface = md_theme_dark_inverseSurface,
    inverseOnSurface = md_theme_dark_inverseOnSurface,
    inversePrimary = md_theme_dark_inversePrimary,
    surfaceTint = md_theme_dark_surfaceTint,
)

@Composable
fun VelesTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    // Static brand palette from the launcher icon; Material You dynamic color
    // is intentionally not used because it would override the brand colors.
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        shapes = VelesShapes,
        typography = VelesTypography,
        content = content,
    )
}
```

- [ ] **Step 3: Verify compile + style + tests**

Run: `./gradlew spotlessCheck detekt testDebugUnitTest assembleDebug`
Expected: all PASS. (If `lightColorScheme` rejects the `surfaceContainer*`/`outlineVariant` parameters, the Compose BOM predates M3 1.2 — it doesn't: `surfaceContainerHigh` is already used in `AccessNotificationPermission.kt:38`.)

- [ ] **Step 4: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/common/ui/theme/Colors.kt app/src/main/java/me/nagaev/veles/common/ui/theme/Theme.kt
git commit -m "feat: static emerald/gold brand palette replacing dynamic color (#39)"
```

---

### Task 3: Bottom-navigation shell

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt`
- Create: `app/src/main/java/me/nagaev/veles/common/ui/VelesBottomBar.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt` (remove nav TextButtons + params only — full restyle is Task 4)
- Test: `app/src/androidTest/java/me/nagaev/permissions/ui/VelesPermissionsAppTests.kt`

**Interfaces:**
- Consumes: `VelesTheme` from Task 2.
- Produces: `VelesBottomBar(currentRoute: String?, onNavigate: (String) -> Unit, modifier: Modifier = Modifier)` in package `me.nagaev.veles.common.ui`; `TestTags.BOTTOM_NAV_BAR`, `TestTags.BOTTOM_NAV_ITEM(route: String)`, `TestTags.BANK_CONFIG_ADD_FAB` (FAB tag used by Task 5). `PermissionsScreen(state, actions, modifier)` loses its two navigation lambdas.

- [ ] **Step 1: Write the failing instrumented test** — append to `VelesPermissionsAppTests.kt`:

```kotlin
    @Test
    fun `bottom bar shows three destinations on permissions route`() {
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState = permissionsState,
                permissionsActions = permissionsActions,
            )
        }

        composeTestRule.onNodeWithTag(TestTags.BOTTOM_NAV_BAR).assertExists()
        composeTestRule.onNodeWithTag(TestTags.BOTTOM_NAV_ITEM("permissions")).assertExists()
        composeTestRule.onNodeWithTag(TestTags.BOTTOM_NAV_ITEM("bank-configs")).assertExists()
        composeTestRule.onNodeWithTag(TestTags.BOTTOM_NAV_ITEM("test")).assertExists()
    }
```

Do NOT add tests that navigate to `bank-configs`/`test` here — those routes call `hiltViewModel()` and this test class has no Hilt host (see the comment at the top of the file).

- [ ] **Step 2: Verify it fails to compile**

Run: `./gradlew compileDebugAndroidTestKotlin`
Expected: FAIL — `Unresolved reference: BOTTOM_NAV_BAR`.

- [ ] **Step 3: Add the tags** — in `TestTags.kt`, add inside the object (and remove nothing yet):

```kotlin
    const val BOTTOM_NAV_BAR = "bottom_nav_bar"
    val BOTTOM_NAV_ITEM = { route: String -> "bottom_nav_item_$route" }
    const val BANK_CONFIG_ADD_FAB = "bank_config_add_fab"
```

- [ ] **Step 4: Create `VelesBottomBar.kt`:**

```kotlin
package me.nagaev.veles.common.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ListAlt
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Science
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag

data class BottomNavDestination(
    val route: String,
    val label: String,
    val icon: ImageVector,
)

val bottomNavDestinations = listOf(
    BottomNavDestination(route = "permissions", label = "Home", icon = Icons.Filled.Home),
    BottomNavDestination(route = "bank-configs", label = "Templates", icon = Icons.AutoMirrored.Filled.ListAlt),
    BottomNavDestination(route = "test", label = "Test", icon = Icons.Filled.Science),
)

@Composable
fun VelesBottomBar(
    currentRoute: String?,
    onNavigate: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier) {
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        NavigationBar(
            containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
            modifier = Modifier.testTag(TestTags.BOTTOM_NAV_BAR),
        ) {
            bottomNavDestinations.forEach { destination ->
                NavigationBarItem(
                    selected = currentRoute == destination.route,
                    onClick = { onNavigate(destination.route) },
                    icon = { Icon(destination.icon, contentDescription = destination.label) },
                    label = { Text(destination.label) },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = MaterialTheme.colorScheme.primary,
                        selectedTextColor = MaterialTheme.colorScheme.primary,
                        unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                    ),
                    modifier = Modifier.testTag(TestTags.BOTTOM_NAV_ITEM(destination.route)),
                )
            }
        }
    }
}
```

Note: the prototype's 72dp bar height is intentionally NOT forced via `Modifier.height(72.dp)` — `NavigationBar` is insets-aware and a fixed height would clip on gesture-nav devices. Default M3 bar height (80dp content) + insets is the accepted deviation.

- [ ] **Step 5: Rewrite `VelesPermissionsApp` with the Scaffold shell.** Replace the `VelesTheme { Surface { ... } }` block: keep every `composable(...)` body exactly as it is today, but wrap the NavHost:

```kotlin
    VelesTheme {
        val navController = rememberNavController()
        val backStackEntry by navController.currentBackStackEntryAsState()
        val currentRoute = backStackEntry?.destination?.route
        Scaffold(
            contentWindowInsets = WindowInsets(0.dp),
            bottomBar = {
                if (currentRoute in topLevelRoutes) {
                    VelesBottomBar(
                        currentRoute = currentRoute,
                        onNavigate = { route ->
                            navController.navigate(route) {
                                popUpTo(navController.graph.findStartDestination().id) {
                                    saveState = true
                                }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                    )
                }
            },
        ) { padding ->
            NavHost(
                navController = navController,
                startDestination = "permissions",
                modifier = Modifier.padding(padding),
            ) {
                // ... all four composable(...) blocks unchanged, except the
                // "permissions" one which drops the two navigation lambdas:
                composable("permissions") {
                    PermissionsScreen(
                        state = permissionsState,
                        actions = permissionsActions,
                    )
                }
                // "test", "bank-configs", "bank-config-edit?id={id}" stay byte-identical.
            }
        }
    }
```

Add at file scope (above `VelesPermissionsApp`):

```kotlin
private val topLevelRoutes = setOf("permissions", "bank-configs", "test")
```

New imports needed: `androidx.compose.foundation.layout.WindowInsets`, `androidx.compose.foundation.layout.padding`, `androidx.compose.material3.Scaffold`, `androidx.compose.runtime.getValue`, `androidx.compose.ui.Modifier`, `androidx.compose.ui.unit.dp`, `androidx.navigation.NavGraph.Companion.findStartDestination`, `androidx.navigation.compose.currentBackStackEntryAsState`, `me.nagaev.veles.common.ui.VelesBottomBar`. Remove the now-unused `androidx.compose.material3.Surface` import.

`contentWindowInsets = WindowInsets(0.dp)` is required: screens already apply `statusBarsPadding()` themselves; without zeroing the Scaffold insets the top inset would be applied twice.

- [ ] **Step 6: Trim `PermissionsScreen.kt`** — remove the `onNavigateToTest`/`onNavigateToBankConfigs` parameters, both `TextButton` blocks (and the `TextButton` import), the `Spacer(Modifier.height(4.dp))` between them, and the two lambda arguments in `PermissionsScreenPreview`. Everything else stays (full restyle is Task 4).

- [ ] **Step 7: Compile + style**

Run: `./gradlew spotlessCheck detekt assembleDebug compileDebugAndroidTestKotlin`
Expected: all PASS.

- [ ] **Step 8: Run instrumented tests if a device is attached**

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.permissions.ui.VelesPermissionsAppTests`
Expected: PASS (7 tests). If no device: skip and say so in the task report.

- [ ] **Step 9: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt app/src/main/java/me/nagaev/veles/common/ui/VelesBottomBar.kt app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt app/src/androidTest/java/me/nagaev/permissions/ui/VelesPermissionsAppTests.kt
git commit -m "feat: persistent bottom-nav shell with Home/Templates/Test (#39)"
```

---

### Task 4: Home screen restyle

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt` (full replace)
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/components/RedactionSection.kt` (full replace)
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/components/AccessNotificationPermission.kt` (full replace)
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/components/PermissionsList.kt` (full replace)
- Modify: `app/src/main/res/values/strings.xml` (add 2 strings)
- Test: existing `VelesPermissionsAppTests.kt` must keep passing unchanged (tags + copy preserved)

**Interfaces:**
- Consumes: `tertiary` (gold) from Task 2 for the enabled dot; `PermissionsScreen(state, actions, modifier)` signature from Task 3.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Add strings** — in `strings.xml`, after `send_notification_permission_description`:

```xml
    <string name="access_notification_permission_title">Notification access</string>
    <string name="send_notification_permission_title">Send notifications</string>
```

- [ ] **Step 2: Replace `PermissionsScreen.kt` entirely with:**

```kotlin
package me.nagaev.veles.permissions.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.ui.components.PermissionsList
import me.nagaev.veles.permissions.ui.components.RedactionSection
import me.nagaev.veles.permissions.viewmodal.PermissionsActions
import me.nagaev.veles.permissions.viewmodal.PermissionsState

@Composable
fun PermissionsScreen(
    state: PermissionsState,
    actions: PermissionsActions,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .statusBarsPadding(),
    ) {
        Text(
            text = "Veles",
            fontSize = 28.sp,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 22.dp),
        )
        Spacer(Modifier.height(16.dp))
        ListenerStatusCard(
            enabled = state.notificationListenerEnabled,
            modifier = Modifier.padding(horizontal = 16.dp),
        )
        RedactionSection(
            state = state.redactionState,
            settingsLocation = state.redactionSettingsLocation,
            onOpenSettings = actions.openRedactionSettings,
            modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 8.dp),
        )
        Text(
            text = "PERMISSIONS",
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium,
            letterSpacing = 0.96.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 16.dp, top = 16.dp, bottom = 4.dp),
        )
        PermissionsList(
            permissions = state.permissions,
            actions = actions,
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun ListenerStatusCard(
    enabled: Boolean,
    modifier: Modifier = Modifier,
) {
    Card(
        shape = MaterialTheme.shapes.small,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
        ),
        modifier = modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                Modifier
                    .size(10.dp)
                    .background(
                        color = if (enabled) {
                            MaterialTheme.colorScheme.tertiary
                        } else {
                            MaterialTheme.colorScheme.error
                        },
                        shape = CircleShape,
                    ),
            )
            Spacer(Modifier.width(12.dp))
            Column {
                Text(
                    text = if (enabled) {
                        "Notification listener enabled"
                    } else {
                        "Notification listener disabled"
                    },
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.testTag(TestTags.NOTIFICATION_LISTENER_STATUS),
                )
                if (!enabled) {
                    Text(
                        text = "Grant notification access below to turn it on",
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
fun PermissionsScreenPreview() {
    PermissionsScreen(
        state = PermissionsState.Mocked,
        actions = PermissionsActions.Mocked,
    )
}
```

(The "Grant notification access below…" subtitle is shown only in the disabled state — showing it while enabled would contradict itself; accepted deviation from the issue's two-line description.)

- [ ] **Step 3: Replace `RedactionSection.kt` entirely with:**

```kotlin
package me.nagaev.veles.permissions.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.ui.TestTags

@Composable
fun RedactionSection(
    state: RedactionState,
    settingsLocation: String,
    onOpenSettings: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (state != RedactionState.Hidden) return
    Card(
        shape = MaterialTheme.shapes.small,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.errorContainer,
        ),
        modifier = modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Icon(
                    imageVector = Icons.Filled.Warning,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onErrorContainer,
                    modifier = Modifier.size(20.dp),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = "Sensitive notification content is hidden on this device. " +
                        "Grant Veles access to see real bank messages.",
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.onErrorContainer,
                )
            }
            if (settingsLocation.isNotBlank()) {
                Spacer(Modifier.height(8.dp))
                Text(
                    text = settingsLocation,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onErrorContainer,
                )
            }
            Spacer(Modifier.height(12.dp))
            Button(
                onClick = onOpenSettings,
                shape = MaterialTheme.shapes.small,
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.error,
                    contentColor = MaterialTheme.colorScheme.onError,
                ),
                modifier = Modifier.testTag(TestTags.REDACTION_OPEN_SETTINGS),
            ) { Text("Open settings") }
        }
    }
}
```

- [ ] **Step 4: Replace `AccessNotificationPermission.kt` entirely with:**

```kotlin
package me.nagaev.veles.permissions.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.wrapContentSize
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.R
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.viewmodal.Permission
import me.nagaev.veles.permissions.viewmodal.RequestPermission
import me.nagaev.veles.permissions.viewmodal.RevokePermission

fun getPermissionTitle(type: PermissionType): Int = when (type) {
    PermissionType.ACCESS_NOTIFICATIONS -> R.string.access_notification_permission_title
    PermissionType.SEND_NOTIFICATIONS -> R.string.send_notification_permission_title
}

fun getPermissionDescription(type: PermissionType): Int = when (type) {
    PermissionType.ACCESS_NOTIFICATIONS -> R.string.access_notification_permission_description
    PermissionType.SEND_NOTIFICATIONS -> R.string.send_notification_permission_description
}

@Composable
fun AccessNotificationPermission(
    permission: Permission,
    requestPermission: RequestPermission,
    revokePermission: RevokePermission,
) {
    Card(
        shape = MaterialTheme.shapes.small,
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
        ),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = stringResource(id = getPermissionTitle(permission.type)),
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Text(
                    text = stringResource(id = getPermissionDescription(permission.type)),
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            SwitchWithLoader(
                modifier = Modifier.wrapContentSize().testTag(TestTags.PERMISSION_STATUS(permission.type)),
                checked = permission.granted,
                onCheckedChange = {
                    if (it) {
                        requestPermission(permission.type)
                    } else {
                        revokePermission(permission.type)
                    }
                },
            )
        }
    }
}
```

- [ ] **Step 5: Replace `PermissionsList.kt` entirely with** (drops the stray status-bar/system-bar insets and surface background, which the screen and shell now own):

```kotlin
package me.nagaev.veles.permissions.ui.components

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.viewmodal.Permission
import me.nagaev.veles.permissions.viewmodal.PermissionsActions

@Composable
fun PermissionsList(
    permissions: Map<PermissionType, Permission>,
    actions: PermissionsActions,
    modifier: Modifier = Modifier,
) {
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        state = rememberLazyListState(),
    ) {
        items(items = permissions.values.toList(), key = { it.type }) { provider ->
            AccessNotificationPermission(
                permission = provider,
                requestPermission = actions.requestPermission,
                revokePermission = actions.revokePermission,
            )
        }
    }
}
```

- [ ] **Step 6: Compile + style + unit tests**

Run: `./gradlew spotlessCheck detekt testDebugUnitTest assembleDebug`
Expected: all PASS.

- [ ] **Step 7: Instrumented regression if device attached**

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.permissions.ui.VelesPermissionsAppTests`
Expected: PASS — permission toggle tags, "enabled"/"disabled" status copy, and the redaction Open-settings tag are all preserved by this restyle. If no device: skip and say so.

- [ ] **Step 8: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt app/src/main/java/me/nagaev/veles/permissions/ui/components/RedactionSection.kt app/src/main/java/me/nagaev/veles/permissions/ui/components/AccessNotificationPermission.kt app/src/main/java/me/nagaev/veles/permissions/ui/components/PermissionsList.kt app/src/main/res/values/strings.xml
git commit -m "feat: restyle Home screen with status card and permission cards (#39)"
```

---

### Task 5: Bank Templates screen restyle

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt` (full replace)
- Test: `app/src/androidTest/java/me/nagaev/veles/otp/config/ExportImportFlowTest.kt` must keep passing (it drives everything through `TestTags.BANK_CONFIG_*`, all preserved)

**Interfaces:**
- Consumes: `TestTags.BANK_CONFIG_ADD_FAB` from Task 3; theme roles from Task 2.
- Produces: nothing new for later tasks. All 13 screen parameters keep their exact names and types.

- [ ] **Step 1: Replace `BankConfigsScreen.kt` entirely with:**

```kotlin
package me.nagaev.veles.otp.config.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.FileDownload
import androidx.compose.material.icons.filled.FileUpload
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.viewmodel.BankConfigsState
import me.nagaev.veles.otp.config.viewmodel.ExportSelection
import me.nagaev.veles.otp.config.viewmodel.ImportReview

@Suppress("LongParameterList", "LongMethod")
@Composable
fun BankConfigsScreen(
    state: BankConfigsState,
    onAdd: () -> Unit,
    onEdit: (Long) -> Unit,
    onRequestDelete: (BankHandlerConfig) -> Unit,
    onCancelDelete: () -> Unit,
    onConfirmDelete: () -> Unit,
    onExport: () -> Unit,
    onToggleExportItem: (String) -> Unit,
    onCancelExportSelection: () -> Unit,
    onConfirmExportSelection: () -> Unit,
    onImport: () -> Unit,
    onConfirmImport: () -> Unit,
    onCancelImport: () -> Unit,
    onDismissMessage: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Box(modifier = modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp)
                .statusBarsPadding(),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 20.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Bank Templates",
                    fontSize = 22.sp,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.weight(1f),
                )
                IconButton(
                    onClick = onImport,
                    modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_BUTTON),
                ) {
                    Icon(
                        imageVector = Icons.Filled.FileDownload,
                        contentDescription = "Import templates",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                IconButton(
                    onClick = onExport,
                    modifier = Modifier.testTag(TestTags.BANK_CONFIG_EXPORT_BUTTON),
                ) {
                    Icon(
                        imageVector = Icons.Filled.FileUpload,
                        contentDescription = "Export templates",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            if (state.isLoading) {
                CircularProgressIndicator(
                    modifier = Modifier
                        .align(Alignment.CenterHorizontally)
                        .padding(top = 32.dp),
                )
            } else {
                LazyColumn {
                    items(state.configs, key = { it.id }) { config ->
                        BankConfigRow(
                            config = config,
                            onEdit = { onEdit(config.id) },
                            onDelete = { onRequestDelete(config) },
                        )
                    }
                }
            }
        }

        FloatingActionButton(
            onClick = onAdd,
            shape = RoundedCornerShape(16.dp),
            containerColor = MaterialTheme.colorScheme.primary,
            contentColor = MaterialTheme.colorScheme.onPrimary,
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(16.dp)
                .testTag(TestTags.BANK_CONFIG_ADD_FAB),
        ) {
            Icon(Icons.Filled.Add, contentDescription = "Add template")
        }

        if (state.deleteTarget != null) {
            AlertDialog(
                onDismissRequest = onCancelDelete,
                shape = RoundedCornerShape(8.dp),
                containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
                title = { Text("Delete \"${state.deleteTarget.name}\"?") },
                text = { Text("This template will be permanently removed.") },
                confirmButton = {
                    TextButton(onClick = onConfirmDelete) { Text("Delete") }
                },
                dismissButton = {
                    TextButton(onClick = onCancelDelete) { Text("Cancel") }
                },
            )
        }

        if (state.exportSelection != null) {
            ExportSelectionDialog(
                selection = state.exportSelection,
                onToggle = onToggleExportItem,
                onConfirm = onConfirmExportSelection,
                onDismiss = onCancelExportSelection,
            )
        }

        if (state.importReview != null) {
            ImportReviewDialog(
                review = state.importReview!!,
                onConfirm = onConfirmImport,
                onDismiss = onCancelImport,
            )
        }

        if (state.message != null) {
            AlertDialog(
                onDismissRequest = onDismissMessage,
                shape = RoundedCornerShape(8.dp),
                containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
                confirmButton = {
                    TextButton(onClick = onDismissMessage) { Text("OK") }
                },
                title = { Text("Veles") },
                text = { Text(state.message!!) },
            )
        }
    }
}

@Composable
private fun ExportSelectionDialog(
    selection: ExportSelection,
    onToggle: (String) -> Unit,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        shape = RoundedCornerShape(8.dp),
        containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
        modifier = Modifier.testTag(TestTags.BANK_CONFIG_EXPORT_DIALOG),
        title = { Text("Export templates") },
        text = {
            Column {
                selection.items.forEach { name ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 2.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Checkbox(
                            checked = name in selection.checked,
                            onCheckedChange = { onToggle(name) },
                        )
                        Text(name, style = MaterialTheme.typography.bodyLarge)
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = onConfirm,
                modifier = Modifier.testTag(TestTags.BANK_CONFIG_EXPORT_CONFIRM),
            ) { Text("Export") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}

@Composable
private fun ImportReviewDialog(
    review: ImportReview,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        shape = RoundedCornerShape(8.dp),
        containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
        modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_DIALOG),
        title = { Text("Import ${review.totalConfigs} templates?") },
        text = {
            Column {
                if (review.toInsert.isNotEmpty()) {
                    Text("New:", style = MaterialTheme.typography.titleSmall)
                    review.toInsert.forEach { Text("- ${it.name}") }
                }
                if (review.toOverwrite.isNotEmpty()) {
                    Text("Will replace:", style = MaterialTheme.typography.titleSmall)
                    review.toOverwrite.forEach { (existing, _) ->
                        Text("- ${existing.name}")
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = onConfirm,
                modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_CONFIRM),
            ) { Text("Import") }
        },
        dismissButton = {
            TextButton(
                onClick = onDismiss,
                modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_CANCEL),
            ) { Text("Cancel") }
        },
    )
}

private const val REGEX_PREVIEW_MAX_CHARS = 30

@Composable
private fun BankConfigRow(
    config: BankHandlerConfig,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
) {
    Card(
        shape = MaterialTheme.shapes.small,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
        ),
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp)
            .clickable(onClick = onEdit),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(36.dp)
                    .background(
                        color = MaterialTheme.colorScheme.primaryContainer,
                        shape = CircleShape,
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = config.name.take(1).uppercase(),
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                )
            }
            Spacer(Modifier.width(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = config.name,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Text(
                    text = config.otpRegex.take(REGEX_PREVIEW_MAX_CHARS),
                    fontSize = 11.sp,
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            IconButton(
                onClick = onEdit,
                modifier = Modifier.size(34.dp),
            ) {
                Icon(
                    imageVector = Icons.Filled.Edit,
                    contentDescription = "Edit ${config.name}",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            IconButton(
                onClick = onDelete,
                modifier = Modifier.size(34.dp),
            ) {
                Icon(
                    imageVector = Icons.Filled.Delete,
                    contentDescription = "Delete ${config.name}",
                    tint = MaterialTheme.colorScheme.error,
                )
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
fun BankConfigsScreenPreview() {
    val config = BankHandlerConfig(
        id = 1L,
        name = "UOB Thailand",
        otpRegex = """ (\w{4})-(\d{6}) """,
        moneyRegex = """of ([A-Z]{3})(\d+)""",
        merchantRegex = """at (.+) expiring""",
        createdAt = 0L,
        updatedAt = 0L,
    )
    BankConfigsScreen(
        state = BankConfigsState(configs = listOf(config)),
        onAdd = {},
        onEdit = {},
        onRequestDelete = {},
        onCancelDelete = {},
        onConfirmDelete = {},
        onExport = {},
        onToggleExportItem = {},
        onCancelExportSelection = {},
        onConfirmExportSelection = {},
        onImport = {},
        onConfirmImport = {},
        onCancelImport = {},
        onDismissMessage = {},
    )
}
```

- [ ] **Step 2: Compile + style + unit tests**

Run: `./gradlew spotlessCheck detekt testDebugUnitTest assembleDebug`
Expected: all PASS. (If spotless reorders the `Card`/`CardDefaults` imports, let it — run `./gradlew spotlessApply` and re-check.)

- [ ] **Step 3: Instrumented export/import regression if device attached**

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.otp.config.ExportImportFlowTest`
Expected: PASS — the flow test uses only `BANK_CONFIG_*` tags, all preserved. If no device: skip and say so.

- [ ] **Step 4: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt
git commit -m "feat: restyle Bank Templates list with avatars, FAB and icon actions (#39)"
```

---

### Task 6: Template Edit screen restyle

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigEditScreen.kt` (full replace)

**Interfaces:**
- Consumes: theme roles from Task 2. Signature unchanged: `BankConfigEditScreen(state, isNew, onNameChanged, onOtpRegexChanged, onMoneyRegexChanged, onMerchantRegexChanged, onSave, onNavigateBack, modifier)`.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Replace `BankConfigEditScreen.kt` entirely with:**

```kotlin
package me.nagaev.veles.otp.config.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.otp.config.viewmodel.BankConfigEditState

@Suppress("LongParameterList", "LongMethod")
@Composable
fun BankConfigEditScreen(
    state: BankConfigEditState,
    isNew: Boolean,
    onNameChanged: (String) -> Unit,
    onOtpRegexChanged: (String) -> Unit,
    onMoneyRegexChanged: (String) -> Unit,
    onMerchantRegexChanged: (String) -> Unit,
    onSave: () -> Unit,
    onNavigateBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    LaunchedEffect(state.savedSuccessfully) {
        if (state.savedSuccessfully) onNavigateBack()
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp)
            .statusBarsPadding()
            .verticalScroll(rememberScrollState()),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(vertical = 8.dp),
        ) {
            IconButton(
                onClick = onNavigateBack,
                modifier = Modifier.size(36.dp),
            ) {
                Icon(
                    imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                    contentDescription = "Back",
                )
            }
            Spacer(Modifier.width(8.dp))
            Text(
                text = if (isNew) "New Template" else "Edit Template",
                fontSize = 18.sp,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface,
            )
        }
        OutlinedTextField(
            value = state.name,
            onValueChange = onNameChanged,
            label = { Text("Name") },
            isError = state.nameError != null,
            supportingText = state.nameError?.let { error -> { Text(error) } },
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        RegexField(
            caption = "OTP regex — group 1: id, group 2: code",
            value = state.otpRegex,
            onValueChange = onOtpRegexChanged,
            label = "OTP Regex",
            error = state.otpRegexError,
        )
        Spacer(Modifier.height(8.dp))
        RegexField(
            caption = "Money regex — group 1: currency, group 2: amount",
            value = state.moneyRegex,
            onValueChange = onMoneyRegexChanged,
            label = "Money Regex",
            error = state.moneyRegexError,
        )
        Spacer(Modifier.height(8.dp))
        RegexField(
            caption = "Merchant regex — group 1: merchant name",
            value = state.merchantRegex,
            onValueChange = onMerchantRegexChanged,
            label = "Merchant Regex",
            error = state.merchantRegexError,
        )
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = onSave,
            enabled = !state.isSaving,
            shape = MaterialTheme.shapes.small,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Save")
        }
        Spacer(Modifier.height(16.dp))
    }
}

@Composable
private fun RegexField(
    caption: String,
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    error: String?,
) {
    Column {
        Text(
            text = caption,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 4.dp),
        )
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            label = { Text(label) },
            textStyle = LocalTextStyle.current.copy(
                fontFamily = FontFamily.Monospace,
                fontSize = 13.sp,
            ),
            isError = error != null,
            supportingText = error?.let { message -> { Text(message) } },
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Preview(showBackground = true)
@Composable
fun BankConfigEditScreenPreview() {
    BankConfigEditScreen(
        state = BankConfigEditState(
            name = "UOB Thailand",
            otpRegex = """ (\w{4})-(\d{6}) """,
            moneyRegex = """of ([A-Z]{3})(\d+)""",
            merchantRegex = """at (.+) expiring""",
        ),
        isNew = false,
        onNameChanged = {},
        onOtpRegexChanged = {},
        onMoneyRegexChanged = {},
        onMerchantRegexChanged = {},
        onSave = {},
        onNavigateBack = {},
    )
}
```

- [ ] **Step 2: Compile + style + unit tests**

Run: `./gradlew spotlessCheck detekt testDebugUnitTest assembleDebug`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigEditScreen.kt
git commit -m "feat: restyle Template Edit with back bar and monospace regex fields (#39)"
```

---

### Task 7: Test screen result card

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt` (swap one tag)
- Modify: `app/src/main/java/me/nagaev/veles/testing/ui/TestScreen.kt` (full replace)
- Test: `app/src/androidTest/java/me/nagaev/veles/testing/ui/TestScreenComposeTest.kt` (update)

**Interfaces:**
- Consumes: `MessageHandlingResult.otpMessage` from Task 1; `tertiary*` roles from Task 2.
- Produces: `TestTags.TEST_RESULT_OTP`, `TestTags.TEST_RESULT_REDACTION_HINT`; deletes `TestTags.TEST_RESULT_TEMPLATE` (verified: only `TestScreenComposeTest` references it).

- [ ] **Step 1: Update tests first** — in `TestScreenComposeTest.kt`:

Replace `matchedResult` with (adds the extracted message; needs imports `me.nagaev.veles.otp.handlers.Money`, `me.nagaev.veles.otp.handlers.Otp`, `me.nagaev.veles.otp.handlers.OtpMessage`, `java.math.BigDecimal`):

```kotlin
    private val matchedResult = TestResult(
        handlingResult = MessageHandlingResult(
            MessageHandlingResult.Status.ACCEPTED,
            "UOB Thailand",
            OtpMessage(
                otp = Otp(value = "511066", id = "VjKp"),
                pay = Money(amount = BigDecimal("600.00"), currencyCode = "THB"),
                merchant = "WWWSFCINEMACITYCOMCORP",
            ),
        ),
        receivedText = "For purchase THB600.00 (OTP=511066) at WWWSFCINEMACITYCOMCORP: Ref-VjKp.",
        receivedTitle = "UOB",
        sourcePackage = "me.nagaev.veles",
        timestamp = 1000L,
    )
```

Replace the first test with:

```kotlin
    @Test
    fun `matched result renders status - template name - otp and received text`() {
        composeTestRule.setContent {
            TestScreen(
                state = TestState(inputText = "input", lastResult = matchedResult),
                onTextChanged = {},
                onSend = {},
                logRawContent = false,
                onLogRawContentToggled = {},
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT)
            .assertIsDisplayed()
            .assertTextContains("Matched", substring = true)
            .assertTextContains("UOB Thailand", substring = true)
        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_OTP)
            .assertIsDisplayed()
            .assertTextContains("511066", substring = true)
        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_RECEIVED_TEXT)
            .assertIsDisplayed()
            .assertTextContains("For purchase THB600.00", substring = true)
    }
```

In the second test, replace the `TEST_RESULT_TEMPLATE` assertion block with:

```kotlin
        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_OTP)
            .assertDoesNotExist()
```

Replace the third test (`redacted received text is displayed verbatim`) with:

```kotlin
    @Test
    fun `redacted received text shows the redaction hint`() {
        composeTestRule.setContent {
            TestScreen(
                state = TestState(inputText = "input", lastResult = redactedResult),
                onTextChanged = {},
                onSend = {},
                logRawContent = false,
                onLogRawContentToggled = {},
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_RECEIVED_TEXT)
            .assertIsDisplayed()
            .assertTextContains("Sensitive notification content hidden", substring = true)
        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_REDACTION_HINT)
            .assertIsDisplayed()
    }

    @Test
    fun `no redaction hint when received text matches the typed input`() {
        composeTestRule.setContent {
            TestScreen(
                state = TestState(
                    inputText = "some unrelated text",
                    lastResult = filteredResult,
                ),
                onTextChanged = {},
                onSend = {},
                logRawContent = false,
                onLogRawContentToggled = {},
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_REDACTION_HINT)
            .assertDoesNotExist()
    }
```

- [ ] **Step 2: Verify failure**

Run: `./gradlew compileDebugAndroidTestKotlin`
Expected: FAIL — `Unresolved reference: TEST_RESULT_OTP`.

- [ ] **Step 3: Swap the tags** — in `TestTags.kt`, delete the `TEST_RESULT_TEMPLATE` line and add:

```kotlin
    const val TEST_RESULT_OTP = "test_result_otp"
    const val TEST_RESULT_REDACTION_HINT = "test_result_redaction_hint"
```

- [ ] **Step 4: Replace `TestScreen.kt` entirely with:**

```kotlin
package me.nagaev.veles.testing.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.BuildConfig
import me.nagaev.veles.common.TestResult
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.testing.viewmodel.TestState

private const val RECEIVED_TEXT_ALPHA = 0.75f

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
            .statusBarsPadding()
            .verticalScroll(rememberScrollState()),
    ) {
        Text(
            text = "Test",
            fontSize = 22.sp,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(top = 20.dp, bottom = 4.dp),
        )
        Text(
            text = "Paste a bank message to check which template matches.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 12.dp),
        )
        OutlinedTextField(
            value = state.inputText,
            onValueChange = onTextChanged,
            label = { Text("Notification text") },
            minLines = 3,
            modifier = Modifier
                .fillMaxWidth()
                .testTag(TestTags.TEST_INPUT),
        )
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = onSend,
            enabled = state.inputText.isNotBlank(),
            shape = MaterialTheme.shapes.small,
            modifier = Modifier
                .fillMaxWidth()
                .testTag(TestTags.TEST_SEND_BUTTON),
        ) {
            Text("Send")
        }
        Spacer(Modifier.height(16.dp))
        if (BuildConfig.DEBUG) {
            Row(verticalAlignment = Alignment.CenterVertically) {
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
        }
        state.lastResult?.let { result ->
            Spacer(Modifier.height(16.dp))
            ResultCard(result = result, typedText = state.inputText)
            Spacer(Modifier.height(16.dp))
        }
    }
}

@Composable
private fun ResultCard(
    result: TestResult,
    typedText: String,
) {
    when (result.handlingResult.status) {
        MessageHandlingResult.Status.ACCEPTED -> MatchedCard(result)
        MessageHandlingResult.Status.FILTERED -> NoMatchCard(result, typedText)
    }
}

@Composable
private fun MatchedCard(result: TestResult) {
    Card(
        shape = MaterialTheme.shapes.small,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.tertiaryContainer,
        ),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusDot(color = MaterialTheme.colorScheme.tertiary)
                Spacer(Modifier.width(8.dp))
                Text(
                    text = "Matched · ${result.handlingResult.matchedTemplateName.orEmpty()}",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onTertiaryContainer,
                    modifier = Modifier.testTag(TestTags.TEST_RESULT),
                )
            }
            result.handlingResult.otpMessage?.let { otpMessage ->
                Spacer(Modifier.height(12.dp))
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(
                            color = MaterialTheme.colorScheme.surface,
                            shape = MaterialTheme.shapes.small,
                        ),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text = otpMessage.otp.value,
                        fontSize = 24.sp,
                        fontFamily = FontFamily.Monospace,
                        letterSpacing = 2.sp,
                        color = MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier
                            .padding(vertical = 12.dp)
                            .testTag(TestTags.TEST_RESULT_OTP),
                    )
                }
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "${otpMessage.pay.currencyCode} ${otpMessage.pay.amount} · ${otpMessage.merchant}",
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.onTertiaryContainer,
                )
            }
            Spacer(Modifier.height(12.dp))
            HorizontalDivider(color = MaterialTheme.colorScheme.onTertiaryContainer.copy(alpha = 0.2f))
            Spacer(Modifier.height(8.dp))
            ReceivedTextLine(
                receivedText = result.receivedText,
                color = MaterialTheme.colorScheme.onTertiaryContainer,
            )
        }
    }
}

@Composable
private fun NoMatchCard(
    result: TestResult,
    typedText: String,
) {
    Card(
        shape = MaterialTheme.shapes.small,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
        ),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusDot(color = MaterialTheme.colorScheme.outline)
                Spacer(Modifier.width(8.dp))
                Text(
                    text = "No match — no bank template recognized this text",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag(TestTags.TEST_RESULT),
                )
            }
            Spacer(Modifier.height(8.dp))
            ReceivedTextLine(
                receivedText = result.receivedText,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (result.receivedText != typedText) {
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "This is what the listener actually received — the OS redacted " +
                        "the real content before Veles could read it (see the banner on Home).",
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag(TestTags.TEST_RESULT_REDACTION_HINT),
                )
            }
        }
    }
}

@Composable
private fun ReceivedTextLine(
    receivedText: String,
    color: Color,
) {
    Text(
        text = "Received: $receivedText",
        fontSize = 12.sp,
        fontFamily = FontFamily.Monospace,
        color = color.copy(alpha = RECEIVED_TEXT_ALPHA),
        modifier = Modifier.testTag(TestTags.TEST_RESULT_RECEIVED_TEXT),
    )
}

@Composable
private fun StatusDot(color: Color) {
    Box(
        Modifier
            .size(8.dp)
            .background(color = color, shape = CircleShape),
    )
}

@Preview(showBackground = true)
@Composable
@Suppress("MaxLineLength")
fun TestScreenPreview() {
    TestScreen(
        state = TestState(
            inputText = "For purchase THB600.00 (OTP=511066) at WWWSFCINEMACITYCOMCORP: Ref-VjKp. Never share OTP with anyone.",
            lastResult = TestResult(
                handlingResult = MessageHandlingResult(
                    MessageHandlingResult.Status.ACCEPTED,
                    "UOB Thai",
                ),
                receivedText = "For purchase THB600.00 (OTP=511066) at WWWSFCINEMACITYCOMCORP: Ref-VjKp.",
                receivedTitle = "UOB",
                sourcePackage = "com.uob.th",
                timestamp = 1000L,
            ),
        ),
        onTextChanged = {},
        onSend = {},
        logRawContent = false,
        onLogRawContentToggled = {},
    )
}
```

Notes: the `receivedTitle`/`sourcePackage` lines from the old layout are intentionally dropped (not part of the new card design; the data stays in `TestResult`). The redaction-hint comparison uses the current `inputText` — `TestResult` does not record the sent text, and adding it would be a behavior change out of scope.

- [ ] **Step 5: Compile + style + unit tests**

Run: `./gradlew spotlessCheck detekt testDebugUnitTest assembleDebug compileDebugAndroidTestKotlin`
Expected: all PASS.

- [ ] **Step 6: Run the Compose tests if device attached**

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.testing.ui.TestScreenComposeTest`
Expected: PASS (7 tests). If no device: skip and say so.

- [ ] **Step 7: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt app/src/main/java/me/nagaev/veles/testing/ui/TestScreen.kt app/src/androidTest/java/me/nagaev/veles/testing/ui/TestScreenComposeTest.kt
git commit -m "feat: Test screen result card with OTP display and redaction hint (#39)"
```

---

### Task 8: Documentation + final verification

**Files:**
- Modify: `CLAUDE.md` (Navigation section + Test Screen section)

**Interfaces:** none.

- [ ] **Step 1: Update `CLAUDE.md`** — replace the Navigation section body:

```markdown
### Navigation

`VelesPermissionsApp` wraps a Compose `NavHost` in a `Scaffold` with a persistent bottom
`NavigationBar` (`VelesBottomBar` in `common/ui/`) with three destinations: `permissions`
(Home, start), `bank-configs` (Templates), and `test` (Test). `bank-config-edit?id={id}` is a
full-screen route that hides the bottom bar. The theme is a static emerald/gold brand palette
(`Colors.kt`/`Theme.kt`) derived from the launcher icon; Material You dynamic color is
intentionally not used. The gold "success" accent is mapped onto `colorScheme.tertiary`.
```

Also, in the Core Flow / handler docs, add one line where `MessageHandlingResult` is relevant (e.g. in the Test Screen section): "`MessageHandlingResult` carries the extracted `OtpMessage` (`otpMessage` field) when a template matches, which the Test screen result card displays."

- [ ] **Step 2: Full verification suite**

Run: `./gradlew spotlessCheck detekt testDebugUnitTest assembleDebug compileDebugAndroidTestKotlin`
Expected: all PASS.
If a device/emulator is attached, also run: `./gradlew connectedDebugAndroidTest`
Expected: PASS. If not attached, state so.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for bottom-nav shell and brand palette (#39)"
```

---

## Final Acceptance Checklist

- [ ] All three tabs reachable via bottom nav; bar hidden on Template Edit; back stack doesn't grow when switching tabs.
- [ ] App renders in the emerald/gold palette in both light and dark mode (no Material You colors).
- [ ] Test screen: matched SMS shows gold card + OTP; garbage text shows no-match card; redaction hint appears only when received text differs from typed text.
- [ ] Export/Import/Delete/Add/Edit template flows all work exactly as before.
- [ ] `spotlessCheck`, `detekt`, `testDebugUnitTest`, `assembleDebug` all green; `connectedDebugAndroidTest` green if a device was available.
