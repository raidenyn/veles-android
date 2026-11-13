package me.nagaev.veles.otp.config

import kotlinx.coroutines.flow.Flow
import javax.inject.Inject

class BankHandlerRepository @Inject constructor(
    private val dao: BankHandlerConfigDao,
) {
    suspend fun getAllSuspend(): List<BankHandlerConfig> = dao.getAllSuspend()

    fun observeAll(): Flow<List<BankHandlerConfig>> = dao.observeAll()

    suspend fun insert(config: BankHandlerConfig): Long = dao.insert(config)

    suspend fun update(config: BankHandlerConfig) = dao.update(config)

    suspend fun delete(config: BankHandlerConfig) = dao.delete(config)
}
