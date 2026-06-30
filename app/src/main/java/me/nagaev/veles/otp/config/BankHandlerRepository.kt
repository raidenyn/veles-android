package me.nagaev.veles.otp.config

import android.content.Context

class BankHandlerRepository(context: Context) {
    private val dao = BankHandlerDatabase.getInstance(context).bankHandlerConfigDao()

    fun getAll(): List<BankHandlerConfig> = dao.getAll()

    suspend fun getAllSuspend(): List<BankHandlerConfig> = dao.getAllSuspend()

    suspend fun insert(config: BankHandlerConfig): Long = dao.insert(config)

    suspend fun update(config: BankHandlerConfig) = dao.update(config)

    suspend fun delete(config: BankHandlerConfig) = dao.delete(config)
}
