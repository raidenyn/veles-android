package me.nagaev.veles.otp.config.di

import android.content.Context
import androidx.room.Room
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import me.nagaev.veles.otp.config.BankHandlerConfigDao
import me.nagaev.veles.otp.config.BankHandlerDatabase
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object DatabaseModule {
    @Provides
    @Singleton
    fun provideDatabase(
        @ApplicationContext context: Context,
    ): BankHandlerDatabase = Room
        .databaseBuilder(
            context,
            BankHandlerDatabase::class.java,
            "bank_handler_configs.db",
        )
        .addMigrations(BankHandlerDatabase.MIGRATION_1_2)
        .addCallback(BankHandlerDatabase.SeedCallback())
        .build()

    @Provides
    fun provideDao(db: BankHandlerDatabase): BankHandlerConfigDao = db.bankHandlerConfigDao()
}
