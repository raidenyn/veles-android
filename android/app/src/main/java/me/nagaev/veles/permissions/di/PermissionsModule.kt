package me.nagaev.veles.permissions.di

import android.content.ComponentName
import android.content.Context
import android.os.Build
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import me.nagaev.veles.otp.NotificationRedactionPath
import me.nagaev.veles.permissions.services.SensitiveNotificationsStatus

private const val NOTIFICATION_LISTENER_CLASS_NAME =
    "me.nagaev.veles.otp.NotificationListener"

@Module
@InstallIn(SingletonComponent::class)
object PermissionsModule {
    @Provides
    fun provideListenerComponentName(
        @ApplicationContext context: Context,
    ): ComponentName = ComponentName(context.packageName, NOTIFICATION_LISTENER_CLASS_NAME)

    @Provides
    @Suppress("MaxLineLength")
    fun provideRedactionPath(componentName: ComponentName): NotificationRedactionPath = NotificationRedactionPath.from(Build.MANUFACTURER, componentName)

    @Provides
    fun provideSensitiveNotificationsStatus(
        @ApplicationContext context: Context,
    ): SensitiveNotificationsStatus = SensitiveNotificationsStatus(context)
}
