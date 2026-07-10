# Design: Bottom-Nav Shell + Icon-Based Palette Redesign

**Issue:** [#39](https://github.com/raidenyn/veles-android/issues/39) — Redesign: Permissions/Templates/Test — bottom-nav shell + icon-based palette
**Date:** 2026-07-10
**Branch:** `redesign/39-bottom-nav-shell`

## Overview

Redesign Veles' three screens (Permissions/Home, Bank Message Templates, Test) into a single
persistent bottom-navigation shell, recolored off the app's real launcher icon (deep emerald
shield + gold bull/runes) instead of the leftover default Material red seed. Functionality is
unchanged — this is a navigation, layout, and visual-polish pass, not a feature change.

**Fidelity is high:** issue #39 pins exact hex values, dp/sp measurements, and copy. This spec
records the implementation decisions layered on top of the issue; the issue body is the
authoritative source for every color, size, and string it specifies. The interactive HTML/React
prototype (`Veles Redesign.dc.html`, attached to the issue:
<https://github.com/user-attachments/files/29888975/Veles.Redesign.dc.html>) is a design
reference only — it must be recreated natively in Jetpack Compose, never embedded or shipped.

All screens are wired to the **existing** production logic: `PermissionsViewModel`,
`BankConfigsViewModel`, `BankConfigEditViewModel`, `TestViewModel`/`TestResultFlow`,
`NotificationListener` → `CompositeMessageHandler` → `RegexMessageHandler`, and
`RedactionDetector`/`RedactionStateFlow`. No regex matching is reimplemented in the UI layer.

## Decisions Made (resolving what the issue left open)

1. **Success/gold color role:** repurpose `colorScheme.tertiary`/`tertiaryContainer`/
   `onTertiaryContainer` for the matched-indicator gold. No custom `CompositionLocal`; nothing
   in the codebase extends the theme today, so this is the smallest footprint. Everywhere the
   issue says `success`/`successContainer`/`onSuccessContainer`, read `tertiary`/
   `tertiaryContainer`/`onTertiaryContainer`.
2. **Navigation shell structure:** keep the single flat `NavHost` in `VelesPermissionsApp` with
   the existing routes (`permissions`, `test`, `bank-configs`, `bank-config-edit?id={id}`) and
   arguments untouched. Wrap it in a `Scaffold` whose `bottomBar` slot observes
   `navController.currentBackStackEntryAsState()` and renders the bottom bar only when the
   current route is one of the three top-level destinations. No nested NavHost, no second
   NavController.

## Architecture

### Navigation shell (`VelesPermissionsApp.kt`)

```
Scaffold(
  bottomBar = {
    if (currentRoute in setOf("permissions", "bank-configs", "test"))
      VelesBottomBar(currentRoute, navController)
  }
) { padding ->
  NavHost(navController, startDestination = "permissions", Modifier.padding(padding)) {
    composable("permissions") { ... }        // Home tab
    composable("bank-configs") { ... }       // Templates tab
    composable("test") { ... }               // Test tab
    composable("bank-config-edit?id={id}") { ... }  // full-screen, bar hidden
  }
}
```

- `VelesBottomBar` is a new composable (suggested location: a new `common/ui/` or
  `permissions/ui/components/` file): Material 3 `NavigationBar`, 72dp tall, container color
  `surfaceContainerHigh`, a 1dp top hairline in `outlineVariant`. Three items — **Home**
  (route `permissions`), **Templates** (route `bank-configs`), **Test** (route `test`) — with
  Material icons. Selected item color `primary`, unselected `onSurfaceVariant`.
- Tab taps navigate with `popUpTo(navController.graph.startDestinationId) { saveState = true }`,
  `launchSingleTop = true`, `restoreState = true` so switching tabs does not grow the back stack.
- Selected destination is derived from the current route — no new ViewModel state anywhere.
- `bank-config-edit` remains a sibling full-screen route; because it is not in the top-level
  route set, the bottom bar disappears there automatically and the screen's own back-arrow top
  bar takes over.
- The two `TextButton`s ("Test", "Bank Configs") on `PermissionsScreen` are deleted, along with
  the `onNavigateToTest`/`onNavigateToBankConfigs` parameters.
- No transition animation between tabs (or a plain crossfade at most).

### Theme

- **`Colors.kt`:** replace all `md_theme_*` constants with the emerald/gold palette from issue
  #39 (light + dark, verbatim hex values), including the `surfaceContainerLow`/
  `surfaceContainer`/`surfaceContainerHigh`/`surfaceContainerHighest` roles the current scheme
  never set. The gold success palette becomes the tertiary group
  (light: tertiary `#8A6D14`, tertiaryContainer `#F7E4A8`, onTertiaryContainer `#241A00`;
  dark: tertiary `#D8B84A`, tertiaryContainer `#544000`, onTertiaryContainer `#FFE18C`).
- **`Theme.kt`:** delete the `dynamicLightColorScheme`/`dynamicDarkColorScheme` branch entirely;
  `VelesTheme` selects the static `LightColors`/`DarkColors` based on `isSystemInDarkTheme()`.
  Material You dynamic color would fight the brand palette.
- **`Shapes.kt`:** unchanged. Cards/buttons/inputs 4dp, dialogs/FAB 8–16dp.
- **`Typography.kt`:** unchanged globally. Two local additions near their call sites (not in
  `VelesTypography`): a 28sp bold "display" style for the Home title, and monospace
  `TextStyle(fontFamily = FontFamily.Monospace)` styles for regex fields, the OTP display, and
  received-text lines.

## Screens

All measurements, colors, and copy per issue #39; behavior is reused verbatim from existing
state/actions.

### 1. Home (`PermissionsScreen.kt`, was "Permissions")

- Large title "Veles", 28sp bold, `primary`, 22dp top / 16dp side padding. May optionally show
  the existing launcher icon at small size next to the title (asset already in repo).
- **Status card:** `surfaceContainerHigh`, 4dp radius, 16dp padding. Row: 10dp dot
  (`tertiary` gold when `notificationListenerEnabled`, `error` when not) + two lines:
  "Notification listener enabled/disabled" (15sp medium, `onSurface`) and "Grant notification
  access below to turn it on" (12sp, `onSurfaceVariant`). Keeps the
  `TestTags.NOTIFICATION_LISTENER_STATUS` tag and the enabled/disabled copy so existing
  assertions hold.
- **Redaction warning** (only when `RedactionState.Hidden`, in `RedactionSection.kt`):
  `errorContainer` card, 4dp radius, warning glyph + "Sensitive notification content is hidden
  on this device. Grant Veles access to see real bank messages." (13sp, `onErrorContainer`) +
  filled "Open settings" button (`error` container / `onError` text) calling the existing
  `openRedactionSettings` action. Keep showing `settingsLocation` when non-blank, and keep
  `TestTags.REDACTION_OPEN_SETTINGS`. Banner disappears when `RedactionState` is
  `Visible`/`Unknown` (existing behavior).
- **Permissions section header:** "PERMISSIONS", 12sp, `onSurfaceVariant`, uppercase, 0.08em
  tracking.
- **Permission rows** (`PermissionsList.kt`/`AccessNotificationPermission.kt`):
  `surfaceContainerHigh` card, 4dp radius, 14×16dp padding; title 14sp medium `onSurface` +
  existing description 12sp `onSurfaceVariant`; trailing `SwitchWithLoader` (same
  loader-when-`granted == null` behavior, restyled row only). Keep
  `TestTags.PERMISSION_STATUS(type)` on the row.

### 2. Bank Templates (`BankConfigsScreen.kt`, was "Bank Configs")

- Top row: "Bank Templates" title (22sp bold, `primary`) + two `IconButton`s — Import (download
  icon) and Export (upload icon), `onSurfaceVariant`, keeping
  `TestTags.BANK_CONFIG_IMPORT_BUTTON`/`BANK_CONFIG_EXPORT_BUTTON`. 20/16/8dp padding.
- **List rows:** `surfaceContainerHigh` card, 4dp radius. Leading 36dp circular avatar
  (`primaryContainer` bg, `onPrimaryContainer` text, first letter of name). Name 15sp medium.
  Below it a **monospace** regex preview (the `otpRegex`, 11sp, `onSurfaceVariant`, single line,
  truncated ~30 chars with ellipsis). Tapping the row body opens Edit. Trailing: pencil
  (`onSurfaceVariant`) and trash (`error`) `IconButton`s, 34dp tap targets.
- **FAB:** 56×56dp, 16dp corner radius, `primary`/`onPrimary`, bottom-right, floating ~88dp from
  the bottom so it clears the bottom nav bar. Replaces the "Add" `TextButton`; navigates to
  `bank-config-edit` with no id.
- **Dialogs:** delete-confirm, export selection (`ExportSelection` checkbox list), import review
  (`ImportReview`), and the message dialog keep their exact data/flow and test tags; restyle as
  8dp-corner `surfaceContainerHighest` dialogs instead of default `AlertDialog` chrome.
- Export/Import document launchers (`CreateDocument`/`OpenDocument`) in `VelesPermissionsApp`
  are unchanged, as is the ON_RESUME refresh observer.

### 3. Template Edit (`BankConfigEditScreen.kt`, was "Bank Config Edit")

- Back-arrow top bar (36dp circular icon button, calls `onNavigateBack`) + title "New Template" /
  "Edit Template" (18sp semibold) driven by the existing `isNew` flag.
- Fields in order: Name (regular 14sp), OTP regex, Money regex, Merchant regex. The three regex
  `OutlinedTextField`s use monospace 13sp text. Above each regex field, a new `labelSmall`-style
  helper caption stating the capture groups (new copy):
  - "OTP regex — group 1: id, group 2: code"
  - "Money regex — group 1: currency, group 2: amount"
  - "Merchant regex — group 1: merchant name"
- Validation unchanged: existing `nameError`/`otpRegexError`/`moneyRegexError`/
  `merchantRegexError` render as `supportingText` in `error` color, 12sp.
- Full-width filled Save button, `primary`/`onPrimary`, 4dp radius; existing
  `savedSuccessfully` → `onNavigateBack` effect unchanged.

### 4. Test (`TestScreen.kt`)

- "Test" title (22sp bold, `primary`) + a one-line explainer, multiline input field (not
  monospace — arbitrary SMS body; keeps `TestTags.TEST_INPUT`), full-width filled Send button
  (keeps `TestTags.TEST_SEND_BUTTON`).
- Debug-only "Show raw notification content in logs" switch unchanged (`BuildConfig.DEBUG` gate,
  keeps its tag).
- **Result card** replaces the current plain `Text` stack (keeps `TestTags.TEST_RESULT`,
  `TEST_RESULT_TEMPLATE`, `TEST_RESULT_RECEIVED_TEXT` on the corresponding elements):
  - **Matched** (`Status.ACCEPTED`): `tertiaryContainer` bg, 4dp radius.
    Row 1: small `tertiary` dot + "Matched · {matchedTemplateName}" (14sp semibold,
    `onTertiaryContainer`). Row 2: the extracted OTP code alone in a `surface` inset, 24sp
    monospace, 2px letter spacing — **no Copy button** (Test verifies matching; copying is the
    notification's job). Row 3: "{currency} {amount} · {merchant}" (13sp, `onTertiaryContainer`).
    Row 4: thin-rule-separated "Received: {receivedText}" line, 12sp monospace at 75% opacity.
  - **Not matched** (`Status.FILTERED`): `surfaceContainerHigh` bg, 4dp radius, `outline` dot +
    "No match — no bank template recognized this text" (14sp semibold, `onSurfaceVariant`), then
    the same "Received:" line. **When `receivedText` differs from the typed input**, one more
    12sp `onSurfaceVariant` line: "This is what the listener actually received — the OS redacted
    the real content before Veles could read it (see the banner on Home)."
  - All values come from the real `TestResult` (`handlingResult`, `receivedText`) produced by
    `NotificationListener` — nothing is computed client-side.
  - The matched-card rows 2–3 need OTP/amount/merchant values, which `MessageHandlingResult`
    does not currently carry (verified: it has only `status` + `matchedTemplateName`; the
    extracted `OtpMessage` goes only to the notifier). Extend `MessageHandlingResult` with an
    optional `otpMessage: OtpMessage?` (null for `FILTERED` and for the companion constants),
    populated by `RegexMessageHandler` on `ACCEPTED`. This is a data-shape addition only — no
    behavior change to handling or notification logic — and it flows to the UI through the
    existing `TestResult.handlingResult`/`TestResultFlow` path.

## Data Flow & State

No new state shapes. `PermissionsState`, `BankConfigsState`, `BankConfigEditState`, `TestState`
are reused as-is. The only "new state" is UI-derived: selected bottom-nav destination and
list-vs-edit, both read from the NavHost route.

## Error Handling

Unchanged from today: field validation errors via `BankConfigEditState`, import/export messages
via `BankConfigsState.message`, redaction detection via `RedactionStateFlow`. The redesign only
restyles their presentation.

## Testing

- **Unit tests:** largely unaffected (no handler/ViewModel logic changes); extend
  `RegexMessageHandlerTest` to assert the new `otpMessage` field on `ACCEPTED`/`FILTERED`
  results.
- **Instrumented/Compose tests:** all existing `TestTags` are preserved.
  `VelesPermissionsAppTests` stays on the `permissions` route (its Hilt limitation is
  documented in the file) — its assertions (permission toggles, listener status copy, redaction
  button) must keep passing. `TestScreenComposeTest` and `ExportImportFlowTest` updated only
  where structure changed (e.g. result-card layout, Import/Export now `IconButton`s — same
  tags). Add new tags in `TestTags.kt` for: bottom-nav bar + each nav item, the Templates FAB,
  the Home status card dot/redaction card as needed. Add a small instrumented test that the
  bottom bar shows the three destinations and that the `permissions` route renders it.
- **Verification:** `./gradlew testDebugUnitTest` and detekt/lint must pass;
  `connectedDebugAndroidTest` where a device is available.

## Out of Scope

- No new features, no Room/DAO/repository changes, no handler behavior changes
  (except the data-shape addition of `otpMessage` to `MessageHandlingResult` above).
- No new image assets.
- No transition animations beyond an optional plain crossfade.
- The HTML prototype is not committed to the repo; it stays linked from issue #39.

## Files Touched (expected)

`VelesPermissionsApp.kt`, `PermissionsScreen.kt`, `PermissionsList.kt`,
`AccessNotificationPermission.kt`, `RedactionSection.kt`, `SwitchWithLoader.kt` (restyle only),
`BankConfigsScreen.kt`, `BankConfigEditScreen.kt`, `TestScreen.kt`, `Colors.kt`, `Theme.kt`,
`Typography.kt` (no-op or comment only), `TestTags.kt`, new `VelesBottomBar` composable,
`MessageHandler.kt` (`MessageHandlingResult.otpMessage`) + `RegexMessageHandler.kt`,
plus affected tests.
