package me.nagaev.veles.otp.config

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject

class BankHandlerRepository @Inject constructor(
    @ApplicationContext context: Context,
) {
    private val dao = BankHandlerDatabase.getInstance(context).bankHandlerConfigDao()

    suspend fun getAllSuspend(): List<BankHandlerConfig> = dao.getAllSuspend()

    fun observeAll(): Flow<List<BankHandlerConfig>> = dao.observeAll()

    suspend fun insert(config: BankHandlerConfig): Long = dao.insert(config)

    suspend fun update(config: BankHandlerConfig) = dao.update(config)

    suspend fun delete(config: BankHandlerConfig) = dao.delete(config)
}
