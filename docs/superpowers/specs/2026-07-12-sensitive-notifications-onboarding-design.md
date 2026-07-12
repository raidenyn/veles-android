# Sensitive-Notifications Onboarding — Design

**Date:** 2026-07-12
**Issue:** [#43](https://github.com/raidenyn/veles-android/issues/43)

## Problem

On Android 15+ (API 35) the OS redacts OTP content from notifications before handing them
to untrusted `NotificationListenerService`s — Veles sees only *"Sensitive notification
content hidden"*, which defeats the app's purpose. The current workaround (adb AppOp grant
plus OEM-specific settings) requires a computer and developer options. We need an in-app
onboarding flow: detect the state proactively, grant the permission in one tap where
possible, verify the grant actually works, and guide the user through fallbacks when it
doesn't.

## Decisions

- **One spec, one implementation plan** covering all phases (detection, CDM association,
  verification, UI, tests/docs). The phases are tightly coupled.
- **Card-only UI.** The sensitive-notifications state renders as a single tiered card on
  `PermissionsScreen` (replacing `RedactionSection`). It is *not* shown as a switch row in
  the permissions list — the state is too rich for a binary toggle. The card is hidden when
  everything is fine.
- **Primary grant path:** CompanionDeviceManager watch-profile association (protection
  level of `REQUEST_COMPANION_PROFILE_WATCH` is *normal*; the granted `COMPANION_DEVICE_WATCH`
  role carries `RECEIVE_SENSITIVE_NOTIFICATIONS` for manifest-declared permissions only).
  No INTERNET permission — the no-network guarantee holds.
- **Honest UX:** the pre-dialog explainer says plainly that Android only shares sensitive
  notifications with companion-device apps, the system dialog will say "watch", and any
  nearby Bluetooth device works.

## Architecture

```
SensitiveNotificationsStatus (static check: permission + AppOp)   ┐
RedactionStateFlow (reactive signal from real notifications)      ├─→ PermissionsViewModel
CompanionAssociationService (CDM associate/disassociate)          │     merged state
TestNotificationSender.postProbe → NotificationListener           │        ↓
  → TestResultFlow (verification round-trip)                      ┘  SensitiveNotificationsCard
```

### 1. `SensitiveNotificationsStatus` (new, `permissions/services/`)

Static, proactive check. Result:

```kotlin
sealed interface SensitiveNotificationsGrant {
    data class Granted(val via: Via) : SensitiveNotificationsGrant { enum class Via { Role, AppOp } }
    object NotGranted : SensitiveNotificationsGrant
    object NotApplicable : SensitiveNotificationsGrant   // API < 35
}
```

- API < 35 ⇒ `NotApplicable` (content readable by default).
- API ≥ 35:
  1. `checkSelfPermission(android.permission.RECEIVE_SENSITIVE_NOTIFICATIONS)` granted
     ⇒ `Granted(Role)` (covers the CDM role grant).
  2. Else `AppOpsManager.unsafeCheckOpNoThrow("android:receive_sensitive_notifications",
     myUid, packageName)`: `MODE_ALLOWED` ⇒ `Granted(AppOp)` (covers the adb grant);
     `MODE_DEFAULT` ⇒ defer to the permission result (i.e. `NotGranted` at this point);
     other modes ⇒ `NotGranted`.
  3. The AppOps call is wrapped in try/catch — unknown-op behavior varies by build; on
     exception fall back to the permission check alone.

### 2. `CompanionAssociationService` (new, `permissions/services/`)

Manifest additions:

```xml
<uses-feature android:name="android.software.companion_device_setup" android:required="false" />
<uses-permission android:name="android.permission.REQUEST_COMPANION_PROFILE_WATCH" />
<uses-permission android:name="android.permission.RECEIVE_SENSITIVE_NOTIFICATIONS" />
```

(Declaring `RECEIVE_SENSITIVE_NOTIFICATIONS` is safe: protection `signature|role` means it
stays ungranted unless the role/AppOp grants it, and the role grant applies only to
manifest-declared permissions — so the watch role does not silently give Veles SMS/contacts
access.)

API:

- `hasAssociation(): Boolean` — `CompanionDeviceManager.myAssociations` filtered by
  `DEVICE_PROFILE_WATCH`.
- `suspend associate(): AssociationOutcome` — builds
  `AssociationRequest.Builder().setDeviceProfile(DEVICE_PROFILE_WATCH).build()`, calls
  `CompanionDeviceManager.associate(request, executor, callback)`, launches the callback's
  `IntentSender`, suspends until resolution. Outcomes: `Associated`, `Cancelled`, `Failed`,
  `Unsupported` (feature `companion_device_setup` missing or CDM null — UI skips straight
  to fallbacks).
- `suspend disassociate()` — removes the watch association (used by `revoke()`; no UI
  affordance).

**IntentSender launcher (codebase delta):** the existing `RequestPermissionLauncher` only
wraps `ActivityResultContracts.RequestPermission`. Add a sibling `IntentSenderLauncher`
built on `ActivityResultContracts.StartIntentSenderForResult`, pre-registered in
`PermissionsActivity` (same pattern: field-initialized at activity construction, passed
into the provider via `buildPermissionsProvider()`).

### 3. `SensitiveNotificationPermissionProvider` (new, `permissions/services/`)

Implements the existing `PermissionProvider` interface; new enum value
`PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS`.

- `isGranted()` = static status is `Granted` or `NotApplicable`.
- `request()` = CDM `associate()` flow.
- `revoke()` = `disassociate()`.

It participates in the `PermissionsProvider.providers` map so request/revoke flow through
the existing `PermissionsViewModel.execute()` plumbing, but **`PermissionsList` skips this
type when rendering rows** (card-only decision).

### 4. Verification probe

- `TestNotificationSender.postProbe(): String` — posts
  `"Veles check: code NNNNNN"` (NNNNNN random per probe) with
  `setVisibility(VISIBILITY_SECRET)` on the existing `VelesTestChannel`; returns the sent
  body for comparison.
- `NotificationListener` already routes own-package + test-channel notifications into
  `TestResultFlow` with the received text — no listener change needed for the round-trip.
- `PermissionsViewModel.verifySensitiveAccess()`:
  1. Post probe, remember sent body.
  2. Await a new `TestResultFlow` value with a 5 s timeout.
  3. Exact compare sent vs. `receivedText`: match ⇒ `Readable`; mismatch ⇒ `Hidden`;
     timeout ⇒ `Unknown` + hint to check notification-listener connection.
  4. Write the result into `RedactionStateFlow` so all consumers agree.
  5. **Cleanup (codebase delta):** cancel the probe notification (the test notification id)
     and reset `TestResultFlow.current = null` — otherwise the Test screen shows a phantom
     result badge the next time it is opened.
- Runs automatically after a successful CDM association; also exposed as a manual
  "Check now" button on the card.
- **Probe caveat (codebase delta):** some builds may not redact an app's *own*
  notifications, so a probe `Readable` can be a false positive. The reactive signal from
  real notifications stays authoritative: a later real-world `Hidden` observation
  downgrades the merged state to `GrantedButRedacted` even after a passing probe.

### 5. Merged UI state

`PermissionsViewModel` merges the static grant status and `RedactionStateFlow` into a
single field on `PermissionsState`:

```kotlin
enum class SensitiveNotificationsUiState {
    NotApplicable,      // API < 35 — card hidden, zero behavior change
    NotGranted,         // static check says no grant
    Verifying,          // probe in flight
    Granted,            // grant present, last signal Readable/Unknown-after-probe-match
    GrantedButRedacted, // grant present but redaction observed (OEM variance)
    Unknown,            // verification timed out / listener not connected
}
```

Static-granted **but** `RedactionStateFlow` last observed `Hidden` ⇒ `GrantedButRedacted`
(explicit OEM-variance state routing to fallbacks). The existing
`redactionState`/`redactionSettingsLocation` fields on `PermissionsState` are absorbed by
the new state + card.

### 6. `SensitiveNotificationsCard` (replaces `RedactionSection`)

Rendered on `PermissionsScreen` (same position) whenever the merged state is **not**
`NotApplicable` and **not** `Granted`. Content, top to bottom:

1. **Status row** — Blocked / Granted-but-still-redacted / Checking / Unknown, with a short
   plain-language explanation of what redaction means for the app.
2. **Primary action** — "Enable (pair as companion)" button with pre-dialog explainer copy:
   Android only shares sensitive notifications with companion-device apps, so Veles asks to
   be registered as one; the system dialog will ask to pick a nearby Bluetooth device (any
   device works — headphones, car, a real watch); Bluetooth must be on and a device nearby.
   Triggers `requestPermission(RECEIVE_SENSITIVE_NOTIFICATIONS)` → auto-verification with
   progress and result badge.
3. **Fallbacks (progressive disclosure)** — revealed after CDM cancel/unsupported/failed or
   verification still `Hidden`:
   - Per-OEM settings deep-link + copy from the existing `NotificationRedactionPath`
     (`StockAndroid`, `OxygenOS`; structure allows adding Samsung later).
   - Stock-Android option: disable Enhanced notifications (Android System Intelligence)
     globally — deep-link where possible, with the device-wide smart-replies/actions
     trade-off spelled out honestly.
   - Last resort: the adb command rendered in-app with a copy-to-clipboard button, the
     OnePlus "Disable system optimization" pre-step when the path is `OxygenOS`, and a
     link to the README section.
4. **"Check now"** — re-runs the verification probe on demand.

New stable selectors in `common/ui/TestTags.kt` for every interactive element and state.

## Error handling

| Failure | Behavior |
|---|---|
| User cancels the CDM dialog | Stays `NotGranted`; fallbacks revealed — never a dead end. |
| CDM missing / feature absent | `Unsupported` → fallbacks immediately. |
| `unsafeCheckOpNoThrow` throws (unknown op) | Fall back to permission check + reactive signal. |
| Verification timeout (5 s) | `Unknown` + hint to check notification-listener connection. |
| Grant present but content still redacted (OEM variance) | Explicit `GrantedButRedacted` state; fallbacks reachable. |
| Any association/AppOps exception | Caught; degrades to fallbacks, never crashes the screen. |

## Testing

- **Unit (MockK, `src/test/`):**
  - `SensitiveNotificationsStatus` matrix: API level × permission result × AppOp mode ×
    AppOp exception.
  - Merged-state logic in `PermissionsViewModel`, including granted-but-redacted.
  - Verification: match, mismatch, timeout; `TestResultFlow` reset after completion.
  - `CompanionAssociationService` outcomes: success, cancel, failure, unsupported.
- **Instrumented (`src/androidTest/`):**
  - Compose UI tests for each card state via the new TestTags.
  - `NotificationListener` probe round-trip updating `RedactionStateFlow` (existing
    `mockkStatic` patterns).

## Docs

- README: new "Enable sensitive notifications" section describing the in-app flow first;
  demote the adb guide to "if the in-app methods fail".
- CLAUDE.md: update package/architecture notes.

## Out of scope

- Shizuku / embedded wireless-adb integration (would require INTERNET, breaking the
  no-network guarantee).
- A dedicated multi-step onboarding wizard route — this lands inline on `PermissionsScreen`.
- CDM `requestNotificationAccess()` to streamline the existing notification-access step
  (nice follow-up once the association exists).

## Acceptance criteria (from #43)

- Fresh install on Android 15+: `PermissionsScreen` immediately shows sensitive-notification
  status (no need to wait for a real bank SMS).
- One-tap CDM association grants `RECEIVE_SENSITIVE_NOTIFICATIONS` on stock Android 15/16,
  confirmed by the in-app verification probe.
- An adb-granted AppOp is detected as `Granted` without any CDM association.
- When content remains redacted after a grant, the card says so and surfaces per-OEM
  settings guidance and the adb fallback with a copyable command.
- Android 14 and below: card absent (`NotApplicable`), zero behavior change.
- Unit + instrumented tests for the states above; README/CLAUDE.md updated.
