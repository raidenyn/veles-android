package me.nagaev.veles.otp

import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.VelesLog
import me.nagaev.veles.otp.config.BankHandlerRepository
import me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandler

@EntryPoint
@InstallIn(SingletonComponent::class)
interface NotificationListenerEntryPoint {
    fun notificationStatePreferences(): NotificationStatePreferences

    fun bankHandlerRepository(): BankHandlerRepository

    fun userNotifierOtpMessageHandler(): UserNotifierOtpMessageHandler

    fun velesLog(): VelesLog
}
