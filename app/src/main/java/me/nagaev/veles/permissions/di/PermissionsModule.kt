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

@Module
@InstallIn(SingletonComponent::class)
object PermissionsModule {
    @Provides
    fun provideListenerComponentName(
        @ApplicationContext context: Context,
    ): ComponentName = ComponentName(context.packageName, "me.nagaev.veles.otp.NotificationListener")

    @Provides
    fun provideRedactionPath(componentName: ComponentName): NotificationRedactionPath = NotificationRedactionPath.from(Build.MANUFACTURER, componentName)
}
