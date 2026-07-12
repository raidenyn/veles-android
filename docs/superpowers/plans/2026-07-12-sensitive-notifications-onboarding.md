# Sensitive-Notifications Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In-app onboarding for `RECEIVE_SENSITIVE_NOTIFICATIONS` on Android 15+: proactive status detection, one-tap CompanionDeviceManager (CDM) watch-profile grant, in-app verification probe, and a tiered fallback card on `PermissionsScreen`.

**Spec:** `docs/superpowers/specs/2026-07-12-sensitive-notifications-onboarding-design.md` (read it first).

**Architecture:** Three new services in `permissions/services/` (`SensitiveNotificationsStatus` static check, `CompanionAssociationService` CDM flow, `SensitiveNotificationPermissionProvider` glue), a verification probe through the existing `TestNotificationSender` → `NotificationListener` → `TestResultFlow` pipeline, and a merged UI state rendered by a new `SensitiveNotificationsCard` that replaces `RedactionSection`.

**Tech Stack:** Kotlin, Jetpack Compose (Material 3), Hilt, MockK + JUnit4 (JVM unit tests), Compose UI tests (androidTest).

## Global Constraints

- `minSdk = 33`, `compileSdk = 35`, `targetSdk = 35` — API-35-only APIs must be behind `Build.VERSION.SDK_INT` guards or injected SDK-level params.
- **Never add the INTERNET permission** — the app's privacy story depends on it.
- Unit tests: `./gradlew testDebugUnitTest` (JVM, MockK). Instrumented: `./gradlew connectedDebugAndroidTest` (device required — if no device is attached, note it and rely on unit tests + `assembleDebug`).
- Every new interactive Compose element gets a stable selector in `common/ui/TestTags.kt`.
- Permission string: `android.permission.RECEIVE_SENSITIVE_NOTIFICATIONS`. AppOp string: `android:receive_sensitive_notifications`. adb command: `adb shell appops set me.nagaev.veles RECEIVE_SENSITIVE_NOTIFICATIONS allow`.
- Commit after each task; messages follow the repo's conventional-commit style (`feat:`, `test:`, `docs:`).

---

### Task 1: `SensitiveNotificationsStatus` — static grant detection

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/permissions/services/SensitiveNotificationsStatus.kt`
- Test: `app/src/test/java/me/nagaev/veles/permissions/services/SensitiveNotificationsStatusTest.kt`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SensitiveNotificationsGrant` sealed interface (`Granted(via: Via)` with `enum Via { Role, AppOp }`, `NotGranted`, `NotApplicable`) and `SensitiveNotificationsStatus.check(): SensitiveNotificationsGrant`. Constructor: `SensitiveNotificationsStatus(context: Context, sdkInt: Int = Build.VERSION.SDK_INT, myUid: () -> Int = { Process.myUid() })`.

- [ ] **Step 1: Write the failing test**

```kotlin
package me.nagaev.veles.permissions.services

import android.app.AppOpsManager
import android.content.Context
import android.content.pm.PackageManager
import io.mockk.every
import io.mockk.mockk
import org.junit.Assert.assertEquals
import org.junit.Test

class SensitiveNotificationsStatusTest {
    private val appOps = mockk<AppOpsManager>()
    private val context = mockk<Context> {
        every { packageName } returns "me.nagaev.veles"
        every { getSystemService(Context.APP_OPS_SERVICE) } returns appOps
    }

    private fun status(sdkInt: Int = 35) = SensitiveNotificationsStatus(context, sdkInt = sdkInt, myUid = { 10001 })

    @Test
    fun `below API 35 is NotApplicable regardless of anything else`() {
        assertEquals(SensitiveNotificationsGrant.NotApplicable, status(sdkInt = 34).check())
    }

    @Test
    fun `permission granted means Granted via Role`() {
        every { context.checkSelfPermission(SensitiveNotificationsStatus.PERMISSION) } returns PackageManager.PERMISSION_GRANTED
        assertEquals(
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role),
            status().check(),
        )
    }

    @Test
    fun `appop MODE_ALLOWED means Granted via AppOp`() {
        every { context.checkSelfPermission(SensitiveNotificationsStatus.PERMISSION) } returns PackageManager.PERMISSION_DENIED
        every { appOps.unsafeCheckOpNoThrow(SensitiveNotificationsStatus.APP_OP, 10001, "me.nagaev.veles") } returns AppOpsManager.MODE_ALLOWED
        assertEquals(
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.AppOp),
            status().check(),
        )
    }

    @Test
    fun `appop MODE_DEFAULT and permission denied means NotGranted`() {
        every { context.checkSelfPermission(SensitiveNotificationsStatus.PERMISSION) } returns PackageManager.PERMISSION_DENIED
        every { appOps.unsafeCheckOpNoThrow(SensitiveNotificationsStatus.APP_OP, 10001, "me.nagaev.veles") } returns AppOpsManager.MODE_DEFAULT
        assertEquals(SensitiveNotificationsGrant.NotGranted, status().check())
    }

    @Test
    fun `appop MODE_ERRORED means NotGranted`() {
        every { context.checkSelfPermission(SensitiveNotificationsStatus.PERMISSION) } returns PackageManager.PERMISSION_DENIED
        every { appOps.unsafeCheckOpNoThrow(SensitiveNotificationsStatus.APP_OP, 10001, "me.nagaev.veles") } returns AppOpsManager.MODE_ERRORED
        assertEquals(SensitiveNotificationsGrant.NotGranted, status().check())
    }

    @Test
    fun `appop lookup throwing falls back to permission result`() {
        every { context.checkSelfPermission(SensitiveNotificationsStatus.PERMISSION) } returns PackageManager.PERMISSION_DENIED
        every { appOps.unsafeCheckOpNoThrow(any<String>(), any<Int>(), any<String>()) } throws IllegalArgumentException("unknown op")
        assertEquals(SensitiveNotificationsGrant.NotGranted, status().check())
    }

    @Test
    fun `null AppOpsManager falls back to permission result`() {
        every { context.checkSelfPermission(SensitiveNotificationsStatus.PERMISSION) } returns PackageManager.PERMISSION_DENIED
        every { context.getSystemService(Context.APP_OPS_SERVICE) } returns null
        assertEquals(SensitiveNotificationsGrant.NotGranted, status().check())
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.permissions.services.SensitiveNotificationsStatusTest"`
Expected: FAIL — `SensitiveNotificationsStatus` unresolved.

- [ ] **Step 3: Write the implementation**

```kotlin
package me.nagaev.veles.permissions.services

import android.app.AppOpsManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Process

sealed interface SensitiveNotificationsGrant {
    data class Granted(val via: Via) : SensitiveNotificationsGrant {
        enum class Via { Role, AppOp }
    }

    data object NotGranted : SensitiveNotificationsGrant

    data object NotApplicable : SensitiveNotificationsGrant
}

class SensitiveNotificationsStatus(
    private val context: Context,
    private val sdkInt: Int = Build.VERSION.SDK_INT,
    private val myUid: () -> Int = { Process.myUid() },
) {
    companion object {
        const val PERMISSION = "android.permission.RECEIVE_SENSITIVE_NOTIFICATIONS"
        const val APP_OP = "android:receive_sensitive_notifications"
        private const val MIN_REDACTION_SDK = 35
    }

    fun check(): SensitiveNotificationsGrant {
        if (sdkInt < MIN_REDACTION_SDK) return SensitiveNotificationsGrant.NotApplicable
        if (context.checkSelfPermission(PERMISSION) == PackageManager.PERMISSION_GRANTED) {
            return SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role)
        }
        val mode =
            try {
                val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as? AppOpsManager
                appOps?.unsafeCheckOpNoThrow(APP_OP, myUid(), context.packageName)
            } catch (_: Exception) {
                // Unknown-op behavior varies by build; fall back to the permission check alone.
                null
            }
        return if (mode == AppOpsManager.MODE_ALLOWED) {
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.AppOp)
        } else {
            SensitiveNotificationsGrant.NotGranted
        }
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.permissions.services.SensitiveNotificationsStatusTest"`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/permissions/services/SensitiveNotificationsStatus.kt app/src/test/java/me/nagaev/veles/permissions/services/SensitiveNotificationsStatusTest.kt
git commit -m "feat: proactive RECEIVE_SENSITIVE_NOTIFICATIONS status detection (#43)"
```

---

### Task 2: Manifest declarations + `IntentSenderLauncher`

**Files:**
- Modify: `app/src/main/AndroidManifest.xml` (add two `uses-permission` + one `uses-feature`)
- Create: `app/src/main/java/me/nagaev/veles/permissions/services/IntentSenderLauncher.kt`

**Interfaces:**
- Consumes: nothing new.
- Produces: `IntentSenderLauncher(launch: (IntentSender, callback: (resultOk: Boolean) -> Unit) -> Unit)` with `IntentSenderLauncher.create(activity: ComponentActivity)` — mirrors the existing `RequestPermissionLauncher` pattern (which has no unit tests either; this is Android glue verified via instrumented flows).

- [ ] **Step 1: Add manifest entries**

In `app/src/main/AndroidManifest.xml`, after the existing `POST_NOTIFICATIONS` line:

```xml
    <uses-permission android:name="android.permission.REQUEST_COMPANION_PROFILE_WATCH" />
    <uses-permission android:name="android.permission.RECEIVE_SENSITIVE_NOTIFICATIONS"
        tools:ignore="ProtectedPermissions" />

    <uses-feature android:name="android.software.companion_device_setup"
        android:required="false" />
```

(`RECEIVE_SENSITIVE_NOTIFICATIONS` is `signature|role` — declaring it is inert until the role/AppOp grants it, and the role grant only applies to manifest-declared permissions.)

- [ ] **Step 2: Create `IntentSenderLauncher`**

```kotlin
package me.nagaev.veles.permissions.services

import android.app.Activity
import android.content.IntentSender
import androidx.activity.ComponentActivity
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts

class IntentSenderLauncher(
    val launch: (intentSender: IntentSender, callback: (resultOk: Boolean) -> Unit) -> Unit,
) {
    companion object {
        fun create(activity: ComponentActivity): IntentSenderLauncher {
            var closerCallback = { _: Boolean -> }
            val resultLauncher =
                activity.registerForActivityResult(
                    ActivityResultContracts.StartIntentSenderForResult(),
                ) { result ->
                    closerCallback(result.resultCode == Activity.RESULT_OK)
                }
            val launch = { intentSender: IntentSender, callback: (Boolean) -> Unit ->
                closerCallback = callback
                resultLauncher.launch(IntentSenderRequest.Builder(intentSender).build())
            }
            return IntentSenderLauncher(launch)
        }
    }
}
```

- [ ] **Step 3: Build**

Run: `./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**

```bash
git add app/src/main/AndroidManifest.xml app/src/main/java/me/nagaev/veles/permissions/services/IntentSenderLauncher.kt
git commit -m "feat: declare companion-watch + sensitive-notifications permissions, IntentSender launcher (#43)"
```

---

### Task 3: `CompanionAssociationService` — CDM watch-profile association

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/permissions/services/CompanionAssociationService.kt`
- Modify: `app/build.gradle.kts` (enable `unitTests.isReturnDefaultValues = true`)
- Test: `app/src/test/java/me/nagaev/veles/permissions/services/CompanionAssociationServiceTest.kt`

**Interfaces:**
- Consumes: `IntentSenderLauncher` (Task 2).
- Produces:
  - `sealed interface AssociationOutcome { Associated; Cancelled; Failed(reason: String?); Unsupported }`
  - `CompanionAssociationService(context: Context, intentSenderLauncher: IntentSenderLauncher, executor: Executor? = null, buildRequest: () -> AssociationRequest = <watch-profile builder>)`
  - `fun isSupported(): Boolean`, `fun hasAssociation(): Boolean`, `suspend fun associate(): AssociationOutcome`, `fun disassociate()`

- [ ] **Step 1: Enable default return values for unit tests**

In `app/build.gradle.kts`, inside the `android { }` block add:

```kotlin
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
```

Why: `associate()` instantiates an anonymous `CompanionDeviceManager.Callback` subclass; without this flag its stub super-constructor throws `RuntimeException("Stub!")` on the JVM. Run `./gradlew testDebugUnitTest` immediately after to confirm the existing suite still passes (this flag only relaxes stub behavior, but verify).

- [ ] **Step 2: Write the failing test**

```kotlin
package me.nagaev.veles.permissions.services

import android.companion.AssociationInfo
import android.companion.AssociationRequest
import android.companion.CompanionDeviceManager
import android.content.Context
import android.content.IntentSender
import android.content.pm.PackageManager
import io.mockk.every
import io.mockk.justRun
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.Executor

class CompanionAssociationServiceTest {
    private val cdm = mockk<CompanionDeviceManager>()
    private val packageManager = mockk<PackageManager> {
        every { hasSystemFeature(PackageManager.FEATURE_COMPANION_DEVICE_SETUP) } returns true
    }
    private val context = mockk<Context> {
        every { getPackageManager() } returns packageManager
        every { getSystemService(Context.COMPANION_DEVICE_SERVICE) } returns cdm
    }
    private val directExecutor = Executor { it.run() }
    private val watchRequest = mockk<AssociationRequest>()

    private fun service(launcher: IntentSenderLauncher = IntentSenderLauncher { _, _ -> }) =
        CompanionAssociationService(context, launcher, executor = directExecutor, buildRequest = { watchRequest })

    private fun watchAssociation(): AssociationInfo = mockk {
        every { deviceProfile } returns AssociationRequest.DEVICE_PROFILE_WATCH
        every { id } returns 7
    }

    @Test
    fun `unsupported when companion feature missing`() = runTest {
        every { packageManager.hasSystemFeature(PackageManager.FEATURE_COMPANION_DEVICE_SETUP) } returns false
        assertFalse(service().isSupported())
        assertEquals(AssociationOutcome.Unsupported, service().associate())
    }

    @Test
    fun `unsupported when CompanionDeviceManager is null`() = runTest {
        every { context.getSystemService(Context.COMPANION_DEVICE_SERVICE) } returns null
        assertEquals(AssociationOutcome.Unsupported, service().associate())
    }

    @Test
    fun `hasAssociation true only for watch-profile associations`() {
        every { cdm.myAssociations } returns listOf(watchAssociation())
        assertTrue(service().hasAssociation())

        every { cdm.myAssociations } returns emptyList()
        assertFalse(service().hasAssociation())
    }

    @Test
    fun `associate resolves Associated when callback reports creation`() = runTest {
        val callbackSlot = slot<CompanionDeviceManager.Callback>()
        every { cdm.associate(watchRequest, any(), capture(callbackSlot)) } answers {
            callbackSlot.captured.onAssociationCreated(watchAssociation())
        }
        assertEquals(AssociationOutcome.Associated, service().associate())
    }

    @Test
    fun `associate resolves via launcher result when dialog finishes`() = runTest {
        val callbackSlot = slot<CompanionDeviceManager.Callback>()
        val sender = mockk<IntentSender>()
        every { cdm.associate(watchRequest, any(), capture(callbackSlot)) } answers {
            callbackSlot.captured.onAssociationPending(sender)
        }
        val cancellingLauncher = IntentSenderLauncher { _, callback -> callback(false) }
        assertEquals(AssociationOutcome.Cancelled, service(cancellingLauncher).associate())

        val okLauncher = IntentSenderLauncher { _, callback -> callback(true) }
        assertEquals(AssociationOutcome.Associated, service(okLauncher).associate())
    }

    @Test
    fun `associate resolves Failed on callback failure`() = runTest {
        val callbackSlot = slot<CompanionDeviceManager.Callback>()
        every { cdm.associate(watchRequest, any(), capture(callbackSlot)) } answers {
            callbackSlot.captured.onFailure("boom")
        }
        assertEquals(AssociationOutcome.Failed("boom"), service().associate())
    }

    @Test
    fun `associate resolves Failed when cdm associate throws`() = runTest {
        every { cdm.associate(watchRequest, any(), any()) } throws SecurityException("nope")
        assertEquals(AssociationOutcome.Failed("nope"), service().associate())
    }

    @Test
    fun `disassociate removes all watch-profile associations`() {
        val association = watchAssociation()
        every { cdm.myAssociations } returns listOf(association)
        justRun { cdm.disassociate(7) }

        service().disassociate()

        verify { cdm.disassociate(7) }
    }
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.permissions.services.CompanionAssociationServiceTest"`
Expected: FAIL — `CompanionAssociationService` unresolved.

- [ ] **Step 4: Write the implementation**

```kotlin
package me.nagaev.veles.permissions.services

import android.companion.AssociationInfo
import android.companion.AssociationRequest
import android.companion.CompanionDeviceManager
import android.content.Context
import android.content.IntentSender
import android.content.pm.PackageManager
import kotlinx.coroutines.suspendCancellableCoroutine
import java.util.concurrent.Executor
import kotlin.coroutines.resume

sealed interface AssociationOutcome {
    data object Associated : AssociationOutcome

    data object Cancelled : AssociationOutcome

    data class Failed(val reason: String?) : AssociationOutcome

    data object Unsupported : AssociationOutcome
}

class CompanionAssociationService(
    private val context: Context,
    private val intentSenderLauncher: IntentSenderLauncher,
    private val executor: Executor? = null,
    private val buildRequest: () -> AssociationRequest = {
        AssociationRequest
            .Builder()
            .setDeviceProfile(AssociationRequest.DEVICE_PROFILE_WATCH)
            .build()
    },
) {
    private fun manager(): CompanionDeviceManager? {
        if (!context.packageManager.hasSystemFeature(PackageManager.FEATURE_COMPANION_DEVICE_SETUP)) return null
        return context.getSystemService(Context.COMPANION_DEVICE_SERVICE) as? CompanionDeviceManager
    }

    fun isSupported(): Boolean = manager() != null

    fun hasAssociation(): Boolean =
        manager()?.myAssociations?.any { it.deviceProfile == AssociationRequest.DEVICE_PROFILE_WATCH } == true

    suspend fun associate(): AssociationOutcome {
        val cdm = manager() ?: return AssociationOutcome.Unsupported
        return try {
            suspendCancellableCoroutine { cont ->
                cdm.associate(
                    buildRequest(),
                    executor ?: context.mainExecutor,
                    object : CompanionDeviceManager.Callback() {
                        override fun onAssociationPending(intentSender: IntentSender) {
                            intentSenderLauncher.launch(intentSender) { resultOk ->
                                if (cont.isActive) {
                                    cont.resume(
                                        if (resultOk) AssociationOutcome.Associated else AssociationOutcome.Cancelled,
                                    )
                                }
                            }
                        }

                        override fun onAssociationCreated(associationInfo: AssociationInfo) {
                            if (cont.isActive) cont.resume(AssociationOutcome.Associated)
                        }

                        override fun onFailure(error: CharSequence?) {
                            if (cont.isActive) cont.resume(AssociationOutcome.Failed(error?.toString()))
                        }
                    },
                )
            }
        } catch (e: Exception) {
            AssociationOutcome.Failed(e.message)
        }
    }

    fun disassociate() {
        manager()?.let { cdm ->
            cdm.myAssociations
                .filter { it.deviceProfile == AssociationRequest.DEVICE_PROFILE_WATCH }
                .forEach { cdm.disassociate(it.id) }
        }
    }
}
```

Note: both `onAssociationCreated` and the launcher result can fire on success — `cont.isActive` guards the double-resume, and resolving `Associated` from a `RESULT_OK` launcher result prevents a hang if `onAssociationCreated` never arrives.

- [ ] **Step 5: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.permissions.services.CompanionAssociationServiceTest"`
Expected: PASS (8 assertions across 8 tests). Then run the full suite: `./gradlew testDebugUnitTest` — expected: PASS (confirms `isReturnDefaultValues` broke nothing).

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/permissions/services/CompanionAssociationService.kt app/src/test/java/me/nagaev/veles/permissions/services/CompanionAssociationServiceTest.kt app/build.gradle.kts
git commit -m "feat: CompanionDeviceManager watch-profile association service (#43)"
```

---

### Task 4: `SensitiveNotificationPermissionProvider` + activity wiring

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/permissions/services/PermissionsProvider.kt` (add enum value)
- Create: `app/src/main/java/me/nagaev/veles/permissions/services/SensitiveNotificationPermissionProvider.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/PermissionsActivity.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/components/PermissionsList.kt` (skip the new type)
- Test: `app/src/test/java/me/nagaev/veles/permissions/services/SensitiveNotificationPermissionProviderTest.kt`

**Interfaces:**
- Consumes: `SensitiveNotificationsStatus.check()` (Task 1), `CompanionAssociationService.{isSupported, associate, disassociate}` (Task 3), `IntentSenderLauncher.create` (Task 2).
- Produces: `PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS`; `SensitiveNotificationPermissionProvider(status, association) : PermissionProvider` with additional `val cdmSupported: Boolean` (Task 6's ViewModel reads it via an `as?` cast from the providers map).

- [ ] **Step 1: Write the failing test**

```kotlin
package me.nagaev.veles.permissions.services

import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.justRun
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SensitiveNotificationPermissionProviderTest {
    private val status = mockk<SensitiveNotificationsStatus>()
    private val association = mockk<CompanionAssociationService>()
    private val provider = SensitiveNotificationPermissionProvider(status, association)

    @Test
    fun `isGranted true for Granted and NotApplicable, false for NotGranted`() {
        every { status.check() } returns SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.AppOp)
        assertTrue(provider.isGranted())

        every { status.check() } returns SensitiveNotificationsGrant.NotApplicable
        assertTrue(provider.isGranted())

        every { status.check() } returns SensitiveNotificationsGrant.NotGranted
        assertFalse(provider.isGranted())
    }

    @Test
    fun `request runs the CDM association flow`() = runTest {
        coEvery { association.associate() } returns AssociationOutcome.Associated
        provider.request()
        coVerify { association.associate() }
    }

    @Test
    fun `revoke disassociates`() = runTest {
        justRun { association.disassociate() }
        provider.revoke()
        coVerify { association.disassociate() }
    }

    @Test
    fun `cdmSupported delegates to the association service`() {
        every { association.isSupported() } returns true
        assertTrue(provider.cdmSupported)
        every { association.isSupported() } returns false
        assertFalse(provider.cdmSupported)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.permissions.services.SensitiveNotificationPermissionProviderTest"`
Expected: FAIL — class unresolved.

- [ ] **Step 3: Implement provider + enum value**

Add to the enum in `PermissionsProvider.kt`:

```kotlin
enum class PermissionType {
    ACCESS_NOTIFICATIONS,
    SEND_NOTIFICATIONS,
    RECEIVE_SENSITIVE_NOTIFICATIONS,
}
```

New file `SensitiveNotificationPermissionProvider.kt`:

```kotlin
package me.nagaev.veles.permissions.services

class SensitiveNotificationPermissionProvider(
    private val status: SensitiveNotificationsStatus,
    private val association: CompanionAssociationService,
) : PermissionProvider {
    val cdmSupported: Boolean
        get() = association.isSupported()

    override fun isGranted(): Boolean = status.check() != SensitiveNotificationsGrant.NotGranted

    override suspend fun request() {
        association.associate()
    }

    override suspend fun revoke() {
        association.disassociate()
    }
}
```

- [ ] **Step 4: Wire into `PermissionsActivity`**

In `PermissionsActivity.kt`, add a launcher field next to `requestPermissionLauncher` and register the provider (the activity is a `Context`):

```kotlin
    private val intentSenderLauncher = IntentSenderLauncher.create(this)
```

and in `buildPermissionsProvider()`:

```kotlin
    private fun buildPermissionsProvider(): PermissionsProvider {
        val activityProvider: ActivityProvider = ActivityProviderImpl(this)
        return PermissionsProviderImpl(
            providers =
            mapOf(
                PermissionType.ACCESS_NOTIFICATIONS to
                    AccessNotificationPermissionProvider(activityProvider),
                PermissionType.SEND_NOTIFICATIONS to
                    SendNotificationPermissionProvider(activityProvider, requestPermissionLauncher),
                PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS to
                    SensitiveNotificationPermissionProvider(
                        SensitiveNotificationsStatus(this),
                        CompanionAssociationService(this, intentSenderLauncher),
                    ),
            ),
        )
    }
```

(Add imports for `IntentSenderLauncher`, `SensitiveNotificationPermissionProvider`, `SensitiveNotificationsStatus`, `CompanionAssociationService`.)

- [ ] **Step 5: Skip the new type in `PermissionsList`**

The sensitive-notifications state renders as a card (Task 7), not a switch row. In `PermissionsList.kt` change the `items(...)` source:

```kotlin
        items(
            items = permissions.values.filter { it.type != PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS },
            key = { it.type },
        ) { provider ->
```

- [ ] **Step 6: Run tests + build**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.permissions.services.SensitiveNotificationPermissionProviderTest"` then `./gradlew assembleDebug`
Expected: PASS, BUILD SUCCESSFUL.

- [ ] **Step 7: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/permissions app/src/test/java/me/nagaev/veles/permissions
git commit -m "feat: RECEIVE_SENSITIVE_NOTIFICATIONS permission provider wired into activity (#43)"
```

---

### Task 5: Verification probe in `TestNotificationSender`

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/testing/TestNotificationSender.kt`

**Interfaces:**
- Consumes: existing `post(text)` internals.
- Produces: `fun postProbe(): String` (returns the sent body, format `"Veles check: code NNNNNN"`, `VISIBILITY_SECRET`, same channel + notification id as `post`) and `fun cancelProbe()`. Round-trip behavior is covered by instrumented tests in Task 8 (thin Android wrapper — same convention as the untested `post`).

- [ ] **Step 1: Implement**

Replace the body of `TestNotificationSender` with a shared builder:

```kotlin
class TestNotificationSender @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    companion object {
        const val CHANNEL_ID = "VelesTestChannel"
        private const val NOTIFICATION_ID = 99999
        private const val PROBE_CODE_RANGE_START = 100000
        private const val PROBE_CODE_RANGE_END = 999999
    }

    fun post(text: String) {
        notify(builder(text))
    }

    fun postProbe(): String {
        val text = "Veles check: code ${(PROBE_CODE_RANGE_START..PROBE_CODE_RANGE_END).random()}"
        notify(builder(text).setVisibility(NotificationCompat.VISIBILITY_SECRET))
        return text
    }

    fun cancelProbe() {
        NotificationManagerCompat.from(context).cancel(NOTIFICATION_ID)
    }

    private fun builder(text: String): NotificationCompat.Builder =
        NotificationCompat
            .Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_otp_message)
            .setContentTitle("Veles Test")
            .setContentText(text)
            .setPriority(NotificationCompat.PRIORITY_HIGH)

    private fun notify(builder: NotificationCompat.Builder) {
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
        val channel =
            NotificationChannel(
                CHANNEL_ID,
                "Veles Test",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = "Test notifications for verifying handler configs"
            }
        manager.createNotificationChannel(channel)
    }
}
```

- [ ] **Step 2: Build + run existing suite**

Run: `./gradlew assembleDebug testDebugUnitTest`
Expected: BUILD SUCCESSFUL, tests PASS.

- [ ] **Step 3: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/testing/TestNotificationSender.kt
git commit -m "feat: secret OTP-like verification probe in TestNotificationSender (#43)"
```

---

### Task 6: Merged state + `verifySensitiveAccess()` in `PermissionsViewModel`

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsState.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModel.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/di/PermissionsModule.kt` (provide `SensitiveNotificationsStatus`)
- Delete: `app/src/test/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModelRedactionTest.kt` (superseded)
- Test: `app/src/test/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModelSensitiveTest.kt`

**Interfaces:**
- Consumes: `SensitiveNotificationsStatus.check()`, `SensitiveNotificationPermissionProvider.cdmSupported`, `TestNotificationSender.{postProbe, cancelProbe}`, `TestResultFlow`, `RedactionStateFlow`, `NotificationRedactionPath`.
- Produces:
  - `enum class SensitiveNotificationsUiState { NotApplicable, NotGranted, Verifying, Granted, GrantedButRedacted, Unknown }` (in `PermissionsState.kt`)
  - `PermissionsState` gains `sensitiveNotifications: SensitiveNotificationsUiState = NotApplicable`, `cdmSupported: Boolean = false`, `showOnePlusAdbPreStep: Boolean = false`; **removes** `redactionState`.
  - `PermissionsActions` gains `verifySensitiveAccess: () -> Unit` and `openEnhancedNotificationsSettings: () -> Unit`.

- [ ] **Step 1: Write the failing tests**

Create `PermissionsViewModelSensitiveTest.kt`:

```kotlin
package me.nagaev.veles.permissions.viewmodal

import android.content.ComponentName
import android.content.Intent
import io.mockk.every
import io.mockk.justRun
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.common.TestResult
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.otp.NotificationRedactionPath
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.permissions.services.PermissionsProvider
import me.nagaev.veles.permissions.services.SensitiveNotificationsGrant
import me.nagaev.veles.permissions.services.SensitiveNotificationsStatus
import me.nagaev.veles.testing.TestNotificationSender
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class PermissionsViewModelSensitiveTest {
    private val status = mockk<SensitiveNotificationsStatus>()
    private val sender = mockk<TestNotificationSender>(relaxed = true)
    private val testResultFlow = TestResultFlow()
    private val redactionStateFlow = RedactionStateFlow()

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun viewModel(): PermissionsViewModel =
        PermissionsViewModel(
            mockk<NotificationStatePreferences>(relaxed = true),
            NotificationRedactionPath.StockAndroid,
            ComponentName("me.nagaev.veles", "me.nagaev.veles.otp.NotificationListener"),
            redactionStateFlow,
            status,
            sender,
            testResultFlow,
            mockk<PermissionsProvider>(relaxed = true),
            { _: Intent -> },
        )

    private fun testResult(text: String) =
        TestResult(
            handlingResult = MessageHandlingResult.FILTERED,
            receivedText = text,
            receivedTitle = "Veles Test",
            sourcePackage = "me.nagaev.veles",
            timestamp = 1L,
        )

    @Test
    fun `NotApplicable below API 35`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns SensitiveNotificationsGrant.NotApplicable
        assertEquals(SensitiveNotificationsUiState.NotApplicable, viewModel().uiState.value.sensitiveNotifications)
    }

    @Test
    fun `NotGranted when static check says no grant`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns SensitiveNotificationsGrant.NotGranted
        assertEquals(SensitiveNotificationsUiState.NotGranted, viewModel().uiState.value.sensitiveNotifications)
    }

    @Test
    fun `Granted when granted and no redaction observed`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.AppOp)
        assertEquals(SensitiveNotificationsUiState.Granted, viewModel().uiState.value.sensitiveNotifications)
    }

    @Test
    fun `GrantedButRedacted when granted but redaction observed`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role)
        val vm = viewModel()
        redactionStateFlow.current.value = RedactionState.Hidden
        assertEquals(SensitiveNotificationsUiState.GrantedButRedacted, vm.uiState.value.sensitiveNotifications)
    }

    @Test
    fun `verification match sets Readable and Granted, cleans up probe`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role)
        every { sender.postProbe() } returns "Veles check: code 835201"
        val vm = viewModel()

        vm.verifySensitiveAccess()
        assertEquals(SensitiveNotificationsUiState.Verifying, vm.uiState.value.sensitiveNotifications)

        testResultFlow.current.value = testResult("Veles check: code 835201")

        assertEquals(RedactionState.Readable, redactionStateFlow.current.value)
        assertEquals(SensitiveNotificationsUiState.Granted, vm.uiState.value.sensitiveNotifications)
        assertNull(testResultFlow.current.value)
        verify { sender.cancelProbe() }
    }

    @Test
    fun `verification mismatch sets Hidden and GrantedButRedacted`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role)
        every { sender.postProbe() } returns "Veles check: code 835201"
        val vm = viewModel()

        vm.verifySensitiveAccess()
        testResultFlow.current.value = testResult("Sensitive notification content hidden")

        assertEquals(RedactionState.Hidden, redactionStateFlow.current.value)
        assertEquals(SensitiveNotificationsUiState.GrantedButRedacted, vm.uiState.value.sensitiveNotifications)
    }

    @Test
    fun `verification timeout sets Unknown`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role)
        every { sender.postProbe() } returns "Veles check: code 835201"
        val vm = viewModel()

        vm.verifySensitiveAccess()
        advanceTimeBy(6_000)

        assertEquals(SensitiveNotificationsUiState.Unknown, vm.uiState.value.sensitiveNotifications)
        verify { sender.cancelProbe() }
    }
}
```

Note: `verifySensitiveAccess` is exposed on `PermissionsActions` as a `() -> Unit` property — invoke it as `vm.verifySensitiveAccess()`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.permissions.viewmodal.PermissionsViewModelSensitiveTest"`
Expected: FAIL — new constructor params / `sensitiveNotifications` unresolved.

- [ ] **Step 3: Update `PermissionsState.kt`**

```kotlin
package me.nagaev.veles.permissions.viewmodal

import me.nagaev.veles.permissions.services.PermissionType

enum class SensitiveNotificationsUiState {
    NotApplicable,
    NotGranted,
    Verifying,
    Granted,
    GrantedButRedacted,
    Unknown,
}

data class PermissionsState(
    val permissions: Map<PermissionType, Permission>,
    val notificationListenerEnabled: Boolean,
    val sensitiveNotifications: SensitiveNotificationsUiState = SensitiveNotificationsUiState.NotApplicable,
    val cdmSupported: Boolean = false,
    val showOnePlusAdbPreStep: Boolean = false,
    val redactionSettingsLocation: String = "",
) {
    companion object {
        val Init = PermissionsState(emptyMap(), notificationListenerEnabled = false)
        val Mocked =
            PermissionsState(
                mapOf(
                    PermissionType.ACCESS_NOTIFICATIONS to
                        Permission(
                            PermissionType.ACCESS_NOTIFICATIONS,
                            true,
                        ),
                    PermissionType.SEND_NOTIFICATIONS to
                        Permission(
                            PermissionType.SEND_NOTIFICATIONS,
                            false,
                        ),
                ),
                notificationListenerEnabled = false,
            )
    }
}

data class Permission(
    val type: PermissionType,
    val granted: Boolean?,
)
```

- [ ] **Step 4: Update `PermissionsViewModel.kt`**

Full replacement:

```kotlin
package me.nagaev.veles.permissions.viewmodal

import android.content.ComponentName
import android.content.Intent
import android.provider.Settings
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.assisted.Assisted
import dagger.assisted.AssistedFactory
import dagger.assisted.AssistedInject
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.otp.NotificationRedactionPath
import me.nagaev.veles.permissions.services.PermissionProvider
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.services.PermissionsProvider
import me.nagaev.veles.permissions.services.SensitiveNotificationPermissionProvider
import me.nagaev.veles.permissions.services.SensitiveNotificationsGrant
import me.nagaev.veles.permissions.services.SensitiveNotificationsStatus
import me.nagaev.veles.testing.TestNotificationSender

interface PermissionsActions {
    val requestPermission: RequestPermission
    val revokePermission: RevokePermission
    val openRedactionSettings: () -> Unit
    val openEnhancedNotificationsSettings: () -> Unit
    val verifySensitiveAccess: () -> Unit

    companion object {
        val Mocked: PermissionsActions =
            object : PermissionsActions {
                override val requestPermission: RequestPermission = {}
                override val revokePermission: RevokePermission = {}
                override val openRedactionSettings: () -> Unit = {}
                override val openEnhancedNotificationsSettings: () -> Unit = {}
                override val verifySensitiveAccess: () -> Unit = {}
            }
    }
}

typealias RequestPermission = (type: PermissionType) -> Unit
typealias RevokePermission = (type: PermissionType) -> Unit

@HiltViewModel(assistedFactory = PermissionsViewModel.Factory::class)
class PermissionsViewModel @AssistedInject constructor(
    private val notificationStatePreferences: NotificationStatePreferences,
    private val redactionPath: NotificationRedactionPath,
    private val componentName: ComponentName,
    private val redactionStateFlow: RedactionStateFlow,
    private val sensitiveStatus: SensitiveNotificationsStatus,
    private val testNotificationSender: TestNotificationSender,
    private val testResultFlow: TestResultFlow,
    @Assisted private val permissionsProvider: PermissionsProvider,
    @Assisted private val openSettings: (Intent) -> Unit,
) : ViewModel(),
    PermissionsActions {
    companion object {
        private const val VERIFY_TIMEOUT_MS = 5_000L
    }

    private val _uiState = MutableStateFlow(PermissionsState.Init)
    val uiState: StateFlow<PermissionsState> = _uiState
    private var isVerifying = false

    @AssistedFactory
    interface Factory {
        fun create(
            permissionsProvider: PermissionsProvider,
            openSettings: (Intent) -> Unit,
        ): PermissionsViewModel
    }

    init {
        updatePermissionsState()
        viewModelScope.launch {
            redactionStateFlow.current.collect { updatePermissionsState() }
        }
    }

    fun updatePermissionsState() {
        _uiState.value =
            uiState.value.copy(
                permissions =
                permissionsProvider.providers.entries.associate {
                    it.key to Permission(it.key, it.value.isGranted())
                },
                notificationListenerEnabled = notificationStatePreferences.getConnectionState(),
                sensitiveNotifications =
                if (isVerifying) {
                    SensitiveNotificationsUiState.Verifying
                } else {
                    mergedSensitiveState(redactionStateFlow.current.value)
                },
                cdmSupported = sensitiveProvider()?.cdmSupported ?: false,
                showOnePlusAdbPreStep = redactionPath is NotificationRedactionPath.OxygenOS,
                redactionSettingsLocation = redactionPath.settingsLocation,
            )
    }

    private fun sensitiveProvider(): SensitiveNotificationPermissionProvider? =
        permissionsProvider.providers[PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS]
            as? SensitiveNotificationPermissionProvider

    private fun mergedSensitiveState(redaction: RedactionState): SensitiveNotificationsUiState =
        when (sensitiveStatus.check()) {
            SensitiveNotificationsGrant.NotApplicable -> SensitiveNotificationsUiState.NotApplicable
            SensitiveNotificationsGrant.NotGranted -> SensitiveNotificationsUiState.NotGranted
            is SensitiveNotificationsGrant.Granted ->
                if (redaction == RedactionState.Hidden) {
                    SensitiveNotificationsUiState.GrantedButRedacted
                } else {
                    SensitiveNotificationsUiState.Granted
                }
        }

    private fun unsetPermissionState(type: PermissionType) {
        _uiState.value =
            uiState.value.copy(
                permissions =
                uiState.value.permissions.toMutableMap().also {
                    it[type] = Permission(type, null)
                },
            )
    }

    private fun execute(
        type: PermissionType,
        method: suspend (PermissionProvider) -> Unit,
    ) {
        permissionsProvider.providers[type]?.let {
            unsetPermissionState(type)
            viewModelScope.launch {
                method(it)
                updatePermissionsState()
            }
        }
    }

    override val requestPermission: RequestPermission = { type ->
        execute(type) { provider ->
            provider.request()
            if (type == PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS && provider.isGranted()) {
                verifySensitiveAccess()
            }
        }
    }

    override val revokePermission: RevokePermission = { type ->
        execute(type) { provider -> provider.revoke() }
    }

    override val openRedactionSettings: () -> Unit = {
        openSettings(redactionPath.settingsIntent(componentName))
    }

    override val openEnhancedNotificationsSettings: () -> Unit = {
        openSettings(Intent(Settings.ACTION_NOTIFICATION_ASSISTANT_SETTINGS))
    }

    override val verifySensitiveAccess: () -> Unit = {
        if (!isVerifying) {
            isVerifying = true
            _uiState.value = uiState.value.copy(sensitiveNotifications = SensitiveNotificationsUiState.Verifying)
            viewModelScope.launch {
                testResultFlow.current.value = null
                val sent = testNotificationSender.postProbe()
                val received =
                    withTimeoutOrNull(VERIFY_TIMEOUT_MS) {
                        testResultFlow.current.filterNotNull().first()
                    }
                testNotificationSender.cancelProbe()
                testResultFlow.current.value = null
                isVerifying = false
                when {
                    received == null ->
                        _uiState.value =
                            uiState.value.copy(sensitiveNotifications = SensitiveNotificationsUiState.Unknown)
                    received.receivedText == sent -> {
                        redactionStateFlow.current.value = RedactionState.Readable
                        updatePermissionsState()
                    }
                    else -> {
                        redactionStateFlow.current.value = RedactionState.Hidden
                        updatePermissionsState()
                    }
                }
            }
        }
    }
}
```

Behavior notes:
- The `Unknown` timeout state survives until the next `updatePermissionsState()` (e.g. window refocus) recomputes the static state — acceptable; the user can re-run "Check now".
- A probe that arrives redacted has `receivedText` = the OS placeholder text, so the exact-compare fails ⇒ `Hidden`. This is deterministic because we control the sent body.
- A probe `Readable` result can be a false positive on builds that don't redact same-package notifications; a later real-world `Hidden` observation flips the merged state to `GrantedButRedacted` via the `RedactionStateFlow` collector.

- [ ] **Step 5: Provide `SensitiveNotificationsStatus` via Hilt**

Add to `PermissionsModule.kt`:

```kotlin
    @Provides
    fun provideSensitiveNotificationsStatus(
        @ApplicationContext context: Context,
    ): SensitiveNotificationsStatus = SensitiveNotificationsStatus(context)
```

(Import `me.nagaev.veles.permissions.services.SensitiveNotificationsStatus`.)

- [ ] **Step 6: Delete the superseded test**

```bash
git rm app/src/test/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModelRedactionTest.kt
```

Its two cases (initial Unknown, flow→Hidden propagation) are superseded by the merged-state tests in Step 1.

- [ ] **Step 7: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest`
Expected: PASS. `PermissionsScreen.kt` still references `state.redactionState` at this point — if compilation of main sources fails, apply the minimal interim change in `PermissionsScreen.kt`: delete the `RedactionSection(...)` call block (lines 57–62) and its import; the full card lands in Task 7.

- [ ] **Step 8: Commit**

```bash
git add -A app/src/main app/src/test
git commit -m "feat: merged sensitive-notifications state + in-app verification loop (#43)"
```

---

### Task 7: `SensitiveNotificationsCard` UI

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/permissions/ui/components/SensitiveNotificationsCard.kt`
- Delete: `app/src/main/java/me/nagaev/veles/permissions/ui/components/RedactionSection.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt`
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt`

**Interfaces:**
- Consumes: `SensitiveNotificationsUiState` (Task 6), `PermissionsActions.{requestPermission, openRedactionSettings, openEnhancedNotificationsSettings, verifySensitiveAccess}`, `PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS`.
- Produces: `@Composable fun SensitiveNotificationsCard(state, cdmSupported, settingsLocation, showOnePlusAdbPreStep, onEnableViaCompanion, onOpenSettings, onOpenEnhancedSettings, onVerify, modifier)` and new TestTags (used by Task 8).

- [ ] **Step 1: Add TestTags**

In `TestTags.kt` replace `REDACTION_OPEN_SETTINGS` with:

```kotlin
    const val SENSITIVE_CARD = "sensitive_card"
    const val SENSITIVE_STATUS = "sensitive_status"
    const val SENSITIVE_ENABLE_BUTTON = "sensitive_enable_button"
    const val SENSITIVE_VERIFY_BUTTON = "sensitive_verify_button"
    const val SENSITIVE_FALLBACKS_TOGGLE = "sensitive_fallbacks_toggle"
    const val SENSITIVE_OPEN_SETTINGS = "sensitive_open_settings"
    const val SENSITIVE_ENHANCED_SETTINGS = "sensitive_enhanced_settings"
    const val SENSITIVE_ADB_COPY = "sensitive_adb_copy"
```

(`REDACTION_OPEN_SETTINGS` is referenced by `VelesPermissionsAppTests` — those tests are rewritten in Task 8; androidTest sources don't block `assembleDebug`.)

- [ ] **Step 2: Create the card**

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
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.viewmodal.SensitiveNotificationsUiState

private const val ADB_COMMAND =
    "adb shell appops set me.nagaev.veles RECEIVE_SENSITIVE_NOTIFICATIONS allow"

@Composable
fun SensitiveNotificationsCard(
    state: SensitiveNotificationsUiState,
    cdmSupported: Boolean,
    settingsLocation: String,
    showOnePlusAdbPreStep: Boolean,
    onEnableViaCompanion: () -> Unit,
    onOpenSettings: () -> Unit,
    onOpenEnhancedSettings: () -> Unit,
    onVerify: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (state == SensitiveNotificationsUiState.NotApplicable ||
        state == SensitiveNotificationsUiState.Granted
    ) {
        return
    }
    var fallbacksExpanded by rememberSaveable { mutableStateOf(false) }
    val showFallbacks =
        fallbacksExpanded ||
            state == SensitiveNotificationsUiState.GrantedButRedacted ||
            !cdmSupported
    Card(
        shape = MaterialTheme.shapes.small,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.errorContainer,
        ),
        modifier = modifier
            .fillMaxWidth()
            .testTag(TestTags.SENSITIVE_CARD),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            StatusRow(state)
            Spacer(Modifier.height(12.dp))
            if (state == SensitiveNotificationsUiState.Verifying) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(8.dp))
                    Text("Checking…", fontSize = 13.sp, color = MaterialTheme.colorScheme.onErrorContainer)
                }
            } else {
                if (cdmSupported && state == SensitiveNotificationsUiState.NotGranted) {
                    Text(
                        text = "Android only shares sensitive notifications with companion-device apps, " +
                            "so Veles asks to be registered as one. The system dialog will ask you to pick " +
                            "a nearby Bluetooth device — any device works (headphones, your car, a watch). " +
                            "Turn Bluetooth on first.",
                        fontSize = 13.sp,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                    )
                    Spacer(Modifier.height(12.dp))
                    Button(
                        onClick = onEnableViaCompanion,
                        shape = MaterialTheme.shapes.small,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = MaterialTheme.colorScheme.error,
                            contentColor = MaterialTheme.colorScheme.onError,
                        ),
                        modifier = Modifier.testTag(TestTags.SENSITIVE_ENABLE_BUTTON),
                    ) { Text("Enable (pair as companion)") }
                    Spacer(Modifier.height(8.dp))
                }
                if (showFallbacks) {
                    FallbackSection(
                        settingsLocation = settingsLocation,
                        showOnePlusAdbPreStep = showOnePlusAdbPreStep,
                        onOpenSettings = onOpenSettings,
                        onOpenEnhancedSettings = onOpenEnhancedSettings,
                    )
                } else {
                    TextButton(
                        onClick = { fallbacksExpanded = true },
                        modifier = Modifier.testTag(TestTags.SENSITIVE_FALLBACKS_TOGGLE),
                    ) { Text("More options") }
                }
                OutlinedButton(
                    onClick = onVerify,
                    shape = MaterialTheme.shapes.small,
                    modifier = Modifier.testTag(TestTags.SENSITIVE_VERIFY_BUTTON),
                ) { Text("Check now") }
            }
        }
    }
}

@Composable
private fun StatusRow(state: SensitiveNotificationsUiState) {
    Row(verticalAlignment = Alignment.Top) {
        Icon(
            imageVector = Icons.Filled.Warning,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onErrorContainer,
            modifier = Modifier.size(20.dp),
        )
        Spacer(Modifier.width(8.dp))
        Text(
            text = when (state) {
                SensitiveNotificationsUiState.NotGranted ->
                    "Android hides OTP content from Veles. Bank codes can't be read until access is granted."
                SensitiveNotificationsUiState.Verifying ->
                    "Checking whether Veles can read sensitive notifications…"
                SensitiveNotificationsUiState.GrantedButRedacted ->
                    "Access is granted, but this device still hides sensitive content. Try the options below."
                SensitiveNotificationsUiState.Unknown ->
                    "Couldn't verify. Check that notification access is enabled, then try again."
                else -> ""
            },
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onErrorContainer,
            modifier = Modifier.testTag(TestTags.SENSITIVE_STATUS),
        )
    }
}

@Composable
private fun FallbackSection(
    settingsLocation: String,
    showOnePlusAdbPreStep: Boolean,
    onOpenSettings: () -> Unit,
    onOpenEnhancedSettings: () -> Unit,
) {
    val clipboard = LocalClipboardManager.current
    Column {
        if (settingsLocation.isNotBlank()) {
            Text(
                text = settingsLocation,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onErrorContainer,
            )
            Spacer(Modifier.height(4.dp))
            OutlinedButton(
                onClick = onOpenSettings,
                shape = MaterialTheme.shapes.small,
                modifier = Modifier.testTag(TestTags.SENSITIVE_OPEN_SETTINGS),
            ) { Text("Open settings") }
            Spacer(Modifier.height(12.dp))
        }
        Text(
            text = "Alternatively, turn off Enhanced notifications. This stops Android from hiding " +
                "sensitive content — but also disables smart replies and actions for all apps.",
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onErrorContainer,
        )
        Spacer(Modifier.height(4.dp))
        OutlinedButton(
            onClick = onOpenEnhancedSettings,
            shape = MaterialTheme.shapes.small,
            modifier = Modifier.testTag(TestTags.SENSITIVE_ENHANCED_SETTINGS),
        ) { Text("Enhanced notifications settings") }
        Spacer(Modifier.height(12.dp))
        if (showOnePlusAdbPreStep) {
            Text(
                text = "On OnePlus: first disable 'System notification optimization' in " +
                    "Developer options, then run the command below.",
                fontSize = 13.sp,
                color = MaterialTheme.colorScheme.onErrorContainer,
            )
            Spacer(Modifier.height(4.dp))
        }
        Text(
            text = "Last resort — grant via adb (see README for full steps):",
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onErrorContainer,
        )
        Text(
            text = ADB_COMMAND,
            fontFamily = FontFamily.Monospace,
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onErrorContainer,
        )
        Spacer(Modifier.height(4.dp))
        OutlinedButton(
            onClick = { clipboard.setText(AnnotatedString(ADB_COMMAND)) },
            shape = MaterialTheme.shapes.small,
            modifier = Modifier.testTag(TestTags.SENSITIVE_ADB_COPY),
        ) { Text("Copy command") }
        Spacer(Modifier.height(8.dp))
    }
}
```

- [ ] **Step 3: Wire into `PermissionsScreen` and delete `RedactionSection`**

In `PermissionsScreen.kt` replace the `RedactionSection(...)` call (and its import) with:

```kotlin
        SensitiveNotificationsCard(
            state = state.sensitiveNotifications,
            cdmSupported = state.cdmSupported,
            settingsLocation = state.redactionSettingsLocation,
            showOnePlusAdbPreStep = state.showOnePlusAdbPreStep,
            onEnableViaCompanion = { actions.requestPermission(PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS) },
            onOpenSettings = actions.openRedactionSettings,
            onOpenEnhancedSettings = actions.openEnhancedNotificationsSettings,
            onVerify = actions.verifySensitiveAccess,
            modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 8.dp),
        )
```

(Add imports for `SensitiveNotificationsCard` and `PermissionType`.) Then:

```bash
git rm app/src/main/java/me/nagaev/veles/permissions/ui/components/RedactionSection.kt
```

- [ ] **Step 4: Build + unit tests**

Run: `./gradlew assembleDebug testDebugUnitTest`
Expected: BUILD SUCCESSFUL, tests PASS.

- [ ] **Step 5: Commit**

```bash
git add -A app/src/main
git commit -m "feat: tiered SensitiveNotificationsCard replaces RedactionSection (#43)"
```

---

### Task 8: Instrumented tests

**Files:**
- Create: `app/src/androidTest/java/me/nagaev/veles/permissions/ui/SensitiveNotificationsCardComposeTest.kt`
- Modify: `app/src/androidTest/java/me/nagaev/permissions/ui/VelesPermissionsAppTests.kt` (replace the two `RedactionSection` tests)
- Modify: `app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt` (add probe round-trip test)

**Interfaces:**
- Consumes: `SensitiveNotificationsCard`, `SensitiveNotificationsUiState`, `PermissionsState` (Tasks 6–7), new TestTags.
- Produces: nothing consumed later.

- [ ] **Step 1: Card Compose tests**

```kotlin
package me.nagaev.veles.permissions.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.ui.components.SensitiveNotificationsCard
import me.nagaev.veles.permissions.viewmodal.SensitiveNotificationsUiState
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class SensitiveNotificationsCardComposeTest {
    @get:Rule
    val composeTestRule = createComposeRule()

    private fun setCard(
        state: SensitiveNotificationsUiState,
        cdmSupported: Boolean = true,
        onEnable: () -> Unit = {},
        onVerify: () -> Unit = {},
    ) {
        composeTestRule.setContent {
            SensitiveNotificationsCard(
                state = state,
                cdmSupported = cdmSupported,
                settingsLocation = "Settings > Notifications",
                showOnePlusAdbPreStep = false,
                onEnableViaCompanion = onEnable,
                onOpenSettings = {},
                onOpenEnhancedSettings = {},
                onVerify = onVerify,
            )
        }
    }

    @Test
    fun cardHiddenWhenNotApplicable() {
        setCard(SensitiveNotificationsUiState.NotApplicable)
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_CARD).assertDoesNotExist()
    }

    @Test
    fun cardHiddenWhenGranted() {
        setCard(SensitiveNotificationsUiState.Granted)
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_CARD).assertDoesNotExist()
    }

    @Test
    fun notGrantedShowsEnableButtonAndTriggersCallback() {
        var enabled = false
        setCard(SensitiveNotificationsUiState.NotGranted, onEnable = { enabled = true })
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_CARD).assertIsDisplayed()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ENABLE_BUTTON).performClick()
        assertTrue(enabled)
    }

    @Test
    fun fallbacksHiddenBehindToggleWhenCdmSupported() {
        setCard(SensitiveNotificationsUiState.NotGranted)
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_COPY).assertDoesNotExist()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_FALLBACKS_TOGGLE).performClick()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_COPY).assertIsDisplayed()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ENHANCED_SETTINGS).assertIsDisplayed()
    }

    @Test
    fun fallbacksShownImmediatelyWhenCdmUnsupported() {
        setCard(SensitiveNotificationsUiState.NotGranted, cdmSupported = false)
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ENABLE_BUTTON).assertDoesNotExist()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_COPY).assertIsDisplayed()
    }

    @Test
    fun grantedButRedactedShowsFallbacksAndVerify() {
        var verified = false
        setCard(SensitiveNotificationsUiState.GrantedButRedacted, onVerify = { verified = true })
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_OPEN_SETTINGS).assertIsDisplayed()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_VERIFY_BUTTON).performClick()
        assertTrue(verified)
    }

    @Test
    fun verifyingShowsProgressWithoutButtons() {
        setCard(SensitiveNotificationsUiState.Verifying)
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_CARD).assertIsDisplayed()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ENABLE_BUTTON).assertDoesNotExist()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_VERIFY_BUTTON).assertDoesNotExist()
    }
}
```

- [ ] **Step 2: Update `VelesPermissionsAppTests`**

Replace the two tests that reference `redactionState`/`TestTags.REDACTION_OPEN_SETTINGS` (around lines 200–235) with equivalents driving the new state, keeping the file's existing state-construction helper style:

```kotlin
    @Test
    fun sensitiveCardVisibleWhenNotGranted() {
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState =
                PermissionsState.Mocked.copy(
                    sensitiveNotifications = SensitiveNotificationsUiState.NotGranted,
                    cdmSupported = true,
                ),
                permissionsActions = PermissionsActions.Mocked,
            )
        }
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_CARD).assertExists()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ENABLE_BUTTON).assertExists()
    }

    @Test
    fun sensitiveCardAbsentWhenNotApplicable() {
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState =
                PermissionsState.Mocked.copy(
                    sensitiveNotifications = SensitiveNotificationsUiState.NotApplicable,
                ),
                permissionsActions = PermissionsActions.Mocked,
            )
        }
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_CARD).assertDoesNotExist()
    }
```

Adapt the exact `setContent` invocation to match how the surrounding tests in that file construct `VelesPermissionsApp` (read the file first; it may pass extra parameters such as bank-config state). Add imports for `SensitiveNotificationsUiState`.

- [ ] **Step 3: Probe round-trip listener test**

Add to `NotificationListenerTest.kt` (androidTest), following the existing self-notification test pattern:

```kotlin
    @Test
    fun `secret probe notification round-trips its text through TestResultFlow`() {
        val ownPkg = "me.nagaev.veles"
        val probeText = "Veles check: code 835201"
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val notification = mockk<Notification>(relaxed = true)
        val sbn = mockk<StatusBarNotification>(relaxed = true)
        val bundle = mockk<Bundle>(relaxed = true)

        every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "Veles Test"
        every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns probeText
        every { sbn.key } returns "key"
        every { sbn.packageName } returns ownPkg
        every { sbn.notification } returns notification
        notification.visibility = Notification.VISIBILITY_SECRET
        notification.extras = bundle
        every { notification.channelId } returns TestNotificationSender.CHANNEL_ID
        every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.FILTERED

        val testResultFlow = TestResultFlow()
        val service = NotificationListener(
            state,
            messageHandler,
            ownPackageName = ownPkg,
            velesLog = testLog,
            testResultFlow = testResultFlow,
            redactionStateFlow = RedactionStateFlow(),
        )
        service.onCreate()
        service.onNotificationPosted(sbn)

        assertEquals(probeText, testResultFlow.current.value?.receivedText)
    }
```

- [ ] **Step 4: Run instrumented tests (device required)**

Run: `./gradlew connectedDebugAndroidTest`
Expected: PASS. If no device/emulator is available, run `./gradlew compileDebugAndroidTestKotlin` instead (must compile) and note in the commit body that on-device execution is pending.

- [ ] **Step 5: Commit**

```bash
git add app/src/androidTest
git commit -m "test: instrumented coverage for sensitive-notifications card and probe round-trip (#43)"
```

---

### Task 9: Docs + final verification

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Update README**

In the existing "Granting access to sensitive notifications" section (around line 99):
1. Rename to "Enable sensitive notifications".
2. Add a new first subsection describing the in-app flow: open Veles → Home screen card → "Enable (pair as companion)" → pick any nearby Bluetooth device in the system dialog → Veles verifies automatically and shows the result. Mention honestly that Android grants this only to companion-device apps, which is why the dialog mentions a watch.
3. Add the Enhanced-notifications fallback with its device-wide trade-off.
4. Keep the existing adb instructions but introduce them with "If the in-app methods fail" (demoted to last resort).

- [ ] **Step 2: Update CLAUDE.md**

- In the package table: add `SensitiveNotificationsStatus`, `CompanionAssociationService`, `SensitiveNotificationPermissionProvider`, `IntentSenderLauncher` to `permissions/services/`; note `SensitiveNotificationsCard` in `permissions/ui/`.
- Add a short "Sensitive notifications (Android 15+)" subsection under Architecture Overview describing: static detection (permission + AppOp), CDM watch-profile grant, verification probe through the test-notification pipeline, merged state (`SensitiveNotificationsUiState`), card-only UI.
- Update the Test Screen section note: `TestNotificationSender` also posts a `VISIBILITY_SECRET` verification probe; verification resets `TestResultFlow` when done.

- [ ] **Step 3: Full verification**

Run: `./gradlew testDebugUnitTest assembleDebug`
Expected: PASS + BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: in-app sensitive-notifications onboarding flow (#43)"
```

---

## Acceptance criteria (verify at the end, from issue #43)

- [ ] Fresh install on Android 15+: `PermissionsScreen` immediately shows sensitive-notification status (no waiting for a real bank SMS).
- [ ] One-tap CDM association grants the permission on stock Android 15/16, confirmed by the verification probe (manual on-device check).
- [ ] An adb-granted AppOp is detected as Granted without any CDM association.
- [ ] Content still redacted after grant ⇒ card says so and surfaces per-OEM guidance + copyable adb command.
- [ ] Android 14 and below: card absent, zero behavior change.
- [ ] Unit + instrumented tests green; README/CLAUDE.md updated.
