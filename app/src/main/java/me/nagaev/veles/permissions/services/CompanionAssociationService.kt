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

    fun hasAssociation(): Boolean = manager()?.myAssociations?.any { it.deviceProfile == AssociationRequest.DEVICE_PROFILE_WATCH } == true

    @Suppress("TooGenericExceptionCaught") // CDM can throw SecurityException, NPE, etc.
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
        } catch (e: Exception) { // resilience boundary: CDM can throw SecurityException, NPE, etc.
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
