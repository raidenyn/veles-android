package me.nagaev.veles.settings.di

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.core.DataStoreFactory
import androidx.datastore.core.handlers.ReplaceFileCorruptionHandler
import androidx.datastore.dataStoreFile
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import me.nagaev.veles.settings.SettingsSerializer
import me.nagaev.veles.settings.proto.VelesSettings
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object SettingsModule {
    @Provides
    @Singleton
    fun provideSettingsDataStore(
        @ApplicationContext context: Context,
    ): DataStore<VelesSettings> = DataStoreFactory.create(
        serializer = SettingsSerializer,
        produceFile = { context.dataStoreFile("settings.pb") },
        corruptionHandler = ReplaceFileCorruptionHandler(
            produceNewData = { VelesSettings.getDefaultInstance() },
        ),
    )
}
