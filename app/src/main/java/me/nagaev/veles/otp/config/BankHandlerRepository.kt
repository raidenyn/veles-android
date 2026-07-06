package me.nagaev.veles.otp.config

import android.content.Context
import kotlinx.coroutines.flow.Flow

class BankHandlerRepository(
    context: Context,
) {
    private val dao = BankHandlerDatabase.getInstance(context).bankHandlerConfigDao()

    fun getAll(): List<BankHandlerConfig> = dao.getAll()

    suspend fun getAllSuspend(): List<BankHandlerConfig> = dao.getAllSuspend()

    fun observeAll(): Flow<List<BankHandlerConfig>> = dao.observeAll()

    suspend fun insert(config: BankHandlerConfig): Long = dao.insert(config)

    suspend fun update(config: BankHandlerConfig) = dao.update(config)

    suspend fun delete(config: BankHandlerConfig) = dao.delete(config)
}
