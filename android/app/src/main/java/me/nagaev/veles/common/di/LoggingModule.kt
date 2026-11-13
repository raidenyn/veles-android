package me.nagaev.veles.common.di

import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import me.nagaev.veles.BuildConfig
import me.nagaev.veles.common.AndroidLogSink
import me.nagaev.veles.common.LogConfig
import me.nagaev.veles.common.LogSink
import me.nagaev.veles.common.SharedPreferencesLogConfig
import javax.inject.Qualifier

@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class DebugEnabled

@Module
@InstallIn(SingletonComponent::class)
abstract class LoggingModule {
    @Binds
    abstract fun bindLogConfig(impl: SharedPreferencesLogConfig): LogConfig

    @Binds
    abstract fun bindLogSink(impl: AndroidLogSink): LogSink

    companion object {
        @Provides
        @DebugEnabled
        fun provideDebugEnabled(): Boolean = BuildConfig.DEBUG
    }
}
