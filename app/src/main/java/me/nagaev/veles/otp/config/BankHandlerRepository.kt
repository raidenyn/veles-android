package me.nagaev.veles.otp.config

import android.content.Context

class BankHandlerRepository(
    context: Context,
) {
    private val dao = BankHandlerDatabase.getInstance(context).bankHandlerConfigDao()

    fun getAll(): List<BankHandlerConfig> = dao.getAll()
}
