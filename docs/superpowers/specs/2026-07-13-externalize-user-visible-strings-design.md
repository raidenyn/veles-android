# Externalize User-Visible Strings - Design

**Date:** 2026-07-13
**Issue:** [#54](https://github.com/raidenyn/veles-android/issues/54)

## Goal

Move every user-visible Android string into resources so a future translation can be added
without changing Kotlin code. This change establishes English as the only packaged and
selectable locale; it does not add translations or an in-app language picker.

The extraction covers Compose screens, accessibility descriptions, notifications, notification
channels, clipboard metadata that can appear in system UI, system-facing labels, validation and
operation messages emitted by ViewModels, and the default Test-screen sample. Clearly awkward
English is corrected while preserving existing meaning and behavior.

## Decisions

- Use feature- or screen-prefixed resource names in `res/values/strings.xml`.
- Keep complete phrases in resources and use positional format arguments so translators can
  reorder dynamic values.
- Use `<plurals>` for count-dependent text.
- Represent ViewModel-owned copy with an immutable resource-backed `UiText` type and resolve it
  at the Compose boundary.
- Resolve strings directly from `Context` in non-Compose notification and clipboard code.
- Extract all user-visible surfaces, including the Test notification channel, default sample,
  clipboard label, redaction settings path, and notification-listener service label.
- Mark technical or behavior-sensitive resource values non-translatable when translating them
  would be incorrect.
- Delete dead Android Studio template strings and unused user-facing properties rather than
  creating dead replacement resources.
- Use Android's native per-app language support. The only initial locale is English.

## Resource Organization

All natural-language product copy lives in `app/src/main/res/values/strings.xml`. Resource names
are grouped by their owning feature, for example `bank_configs_*`, `bank_config_edit_*`,
`test_screen_*`, `permissions_*`, `sensitive_card_*`, and `otp_notification_*`.

Formatted resources contain whole phrases with positional placeholders such as `%1$s` and
`%2$d`. Kotlin must not assemble translated sentences from independently translated fragments.
Count-sensitive messages include export success, import success, and import-confirmation text,
and use `<plurals>` with the count supplied as both the quantity selector and a format argument
when the number appears in the output.

The following remain stable technical values rather than translatable copy:

- Navigation routes, URI schemes, MIME types, package names, intent actions and extras.
- Notification channel IDs, database names, preference file names and keys.
- URLs, adb commands, AppOp and permission names, regexes, and `TestTags`.
- Dynamic domain data such as template names, merchant names, OTPs, currency codes, imported
  JSON, and incoming notification text.

Existing preference identifiers in `strings.xml` must not become translatable because changing
them would make persisted state appear lost after a locale change. They may remain resources
with `translatable="false"` or move to Kotlin constants while retaining their exact values.

Under the strict extraction scope, the clipboard label and default Test-screen sample become
resources. They are marked `translatable="false"`: the clipboard label is technical metadata,
and an independently translated sample could stop matching the seeded bank regex. A future
locale may deliberately provide a localized sample together with a compatible template.

## UiText

Add a sealed `UiText` value type under `me.nagaev.veles.common` with two variants:

- `Res(@StringRes id, args: List<Any>)` for plain and formatted strings.
- `Plural(@PluralsRes id, quantity: Int, args: List<Any>)` for quantity strings.

Arguments use immutable lists rather than vararg-backed arrays so value equality is reliable in
state and JVM tests. Convenience constructors may accept varargs, but stored state must retain
value-based equality. Plural resolution supports format arguments beyond the quantity; callers
explicitly include the quantity when the selected string displays it.

A Composable resolver next to `UiText` uses the current Android resources. Resolution happens
while rendering, so existing state is displayed in the active app locale after a configuration
change. The type does not retain a `Context`.

Apply `UiText` to:

- `BankConfigsState.message` and all import/export outcome and failure assignments.
- `BankConfigEditState` field validation errors.
- `PermissionsState.redactionSettingsLocation` and its producer.

`NotificationRedactionPath` exposes a string resource ID for each settings path. Its unused
`explainerCopy` property is deleted instead of externalized.

## Compose UI

Compose code resolves static copy with `stringResource(...)` and state-backed copy with
`UiText.asString()`. The extraction includes:

- `VelesBottomBar` labels, with destinations storing resource IDs rather than resolved strings.
- `PermissionsScreen` headings, status text, guidance, and accessibility content.
- `SensitiveNotificationsCard` instructions, controls, links, status text, and fallback copy.
- `BankConfigsScreen` titles, actions, dialogs, accessibility descriptions, and pluralized
  import/export text.
- `BankConfigEditScreen` navigation descriptions, labels, captions, errors, and actions.
- `TestScreen` headings, input labels, statuses, redaction guidance, result labels, and formatted
  extracted values.

Stable `TestTags` remain unchanged. Dynamic user or domain content remains raw data and is
inserted into complete formatted resources where surrounding copy is shown.

## Notifications and System Surfaces

`OtpNotificationBuilder` resolves notification body text, Copy action labels, channel name, and
channel description through its existing `Context`. `TestNotificationSender` does the same for
the probe text, notification title, test channel name, and channel description.

Channel IDs remain hardcoded constants. Channel creation must not return solely because a channel
already exists: it resubmits the channel with the localized name and description so a locale
change updates system UI. Android preserves user-controlled channel behavior when an existing
channel is recreated with the same ID.

`CopyDataReceiver` resolves the clipboard label from resources when creating and identifying the
sensitive clip. The notification-listener service label is corrected from `OTP_Listener` to clear
English suitable for Android settings. Existing permission descriptions are corrected only where
their current wording is objectively awkward; this is not a broad product-copy rewrite.

## Locale Configuration

Add `res/xml/locales_config.xml` with English (`en`) as its sole locale and reference it from the
application with `android:localeConfig`. Add `en` to `androidResources.localeFilters` in
`app/build.gradle.kts` so transitive library locales are not packaged accidentally.

The project uses this explicit locale-config strategy and does not enable AGP automatic locale
configuration. Adding a translation later requires synchronized changes:

1. Add `res/values-xx/strings.xml`.
2. Add its BCP-47 locale tag to `locales_config.xml`.
3. Add the locale to `androidResources.localeFilters`.

## Error Handling

Localization does not change operation control flow. Import/export failures and validation
errors continue through their existing state paths but carry resource identity, quantity, and
format arguments instead of resolved English.

Android's default `values/` resources provide fallback text if a future locale omits a resource.
Resource placeholder and formatting mismatches are implementation errors to catch through build,
lint, and tests rather than new runtime fallback logic. Stable preference identifiers prevent
locale changes from affecting persisted state.

## Testing and Validation

Unit tests assert `UiText` resource IDs, quantities, and arguments instead of English output.
Notification tests supply or mock resource results and continue to verify complete formatted
notification text and action labels. Redaction-path tests assert resource identity. Clipboard
tests account for the resource-backed label.

Compose tests continue selecting nodes through `TestTags`. Any assertions that intentionally
verify visible text resolve the expected resource through the target context rather than embedding
English literals. Tests must cover plain, formatted, and plural `UiText` resolution, including
plural arguments beyond the quantity where supported.

Verification includes:

- `./gradlew testDebugUnitTest`
- `./gradlew assembleDebug`
- Android lint/resource validation.
- Relevant instrumented tests when a device or emulator is available.
- A final production-source audit for remaining user-visible string literals, with every
  intentional exception classified as technical, dynamic domain data, or test/preview fixture.

## Out of Scope

- Shipping any non-English translation.
- Adding an in-app language picker; Android's per-app language settings are sufficient for the
  app's minimum SDK.
- Localizing the landing-page site.
- Localizing persistent template names or seeded bank regex data.
- Changing amount parsing or introducing locale-aware currency/number formatting beyond placing
  existing dynamic values into reorderable string resources.
- Broadly rewriting product copy or changing permission, notification, import/export, or Test
  screen behavior.

## Acceptance Criteria

- Production Kotlin contains no hardcoded user-visible English except explicitly classified
  technical or dynamic values.
- Compose, notification, channel, clipboard, service-label, validation, and operation copy is
  resource-backed.
- ViewModels do not resolve Android resources or retain a `Context` for message localization.
- `UiText` supports deterministic equality, formatted strings, and plurals with arbitrary format
  arguments required by this feature.
- Existing notification channels receive localized names and descriptions without changing their
  IDs or user-controlled settings.
- Preference names and keys retain their exact values across the change.
- Android declares English as the sole selectable and packaged locale.
- Dead template resources and the unused redaction explainer are removed.
- JVM tests and the debug build pass; relevant instrumented tests pass when an Android target is
  available.
