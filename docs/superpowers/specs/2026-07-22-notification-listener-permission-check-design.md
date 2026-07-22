# Harden notification-listener permission detection

**Issue:** [raidenyn/veles-android#59](https://github.com/raidenyn/veles-android/issues/59)
**Type:** Bug fix
**Priority:** High - affected fresh installs crash before onboarding
**Effort:** Small

## Problem

`AccessNotificationPermissionProvider.isGranted()` reads
`Settings.Secure.enabled_notification_listeners` as a Kotlin platform type and immediately calls
`contains` on it. Android may return `null` when no notification listener has ever been enabled,
so the permissions ViewModel can crash during initialization on a fresh install.

The current substring check is also imprecise. The setting is a colon-separated list of flattened
component names, but `contains` can accept an unrelated component whose text merely contains the
expected package and class string.

## Goals

- Return `false` rather than throw when the secure setting is null or empty.
- Report access as granted only when the exact Veles `NotificationListener` component is present.
- Ignore malformed and unrelated setting entries safely.
- Add regression coverage for both the fresh-install crash and false-positive matching.

## Non-goals

- Changing the notification-listener request or revoke flows.
- Changing permissions ViewModel or UI behavior.
- Introducing a settings-reader abstraction or changing dependency injection.
- Detecting access only at package level.

## Design

### Permission check

`AccessNotificationPermissionProvider.isGranted()` remains responsible for reading notification
listener access. It will construct the expected `ComponentName` from the current activity package
and `NotificationListener::class.java`.

The provider will read `enabled_notification_listeners` and return `false` when the result is null
or empty. For a non-empty value, it will:

1. Split the value on `:` into flattened component entries.
2. Parse each entry with `ComponentName.unflattenFromString`.
3. Ignore entries that cannot be parsed.
4. Return `true` only when a parsed component equals the expected component.

Using `ComponentName` equality preserves Android's flattened-component semantics, including
relative class names, without accepting substring lookalikes. The existing `request()` and
`revoke()` methods remain unchanged.

### Data flow

```text
PermissionsViewModel initialization
  -> AccessNotificationPermissionProvider.isGranted()
  -> read enabled_notification_listeners
  -> null/empty: false
  -> split non-empty value into entries
  -> parse each flattened ComponentName
  -> exact Veles listener found: true
  -> no exact match: false
```

### Error handling

The check fails closed. A null, empty, malformed, or unrelated setting does not propagate an error
and is treated as permission not granted. This lets onboarding render and direct the user to the
system notification-listener settings instead of crashing.

No broad exception handling is added. `ComponentName.unflattenFromString` already represents an
invalid entry with `null`, so malformed persisted values can be skipped explicitly.

## Testing

A focused `AccessNotificationPermissionProviderTest` unit test will mock the activity, content
resolver, and secure-setting lookup using the project's existing MockK/Robolectric test setup. It
will cover:

- A null setting returns `false` without throwing.
- An empty setting returns `false`.
- The exact Veles listener component returns `true`.
- A matching component among multiple entries returns `true`.
- Unrelated components and substring lookalikes return `false`.
- Malformed entries are ignored and return `false` unless another entry is an exact match.

Verification will run the focused test class followed by the full debug unit-test suite.

## Risks

- Direct secure-setting reads remain tied to Android's colon-separated flattened-component format.
  Parsing with Android's `ComponentName` API minimizes this risk and matches the setting's defined
  representation.
- Exact service matching is intentionally stricter than package-level detection. If Veles adds a
  second listener service later, that service must not satisfy access for the listener used by the
  current notification-processing flow.
