package me.nagaev.veles.otp.config

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query

@Dao
interface BankHandlerConfigDao {
    @Query("SELECT * FROM bank_handler_configs ORDER BY id ASC")
    fun getAll(): List<BankHandlerConfig>

    @Insert
    fun insertAll(vararg configs: BankHandlerConfig)
}
