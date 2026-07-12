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
