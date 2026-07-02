package me.nagaev.veles.otp.config

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update

@Dao
interface BankHandlerConfigDao {
    @Query("SELECT * FROM bank_handler_configs ORDER BY id ASC")
    fun getAll(): List<BankHandlerConfig>

    @Insert
    fun insertAll(vararg configs: BankHandlerConfig)

    @Query("SELECT * FROM bank_handler_configs ORDER BY id ASC")
    suspend fun getAllSuspend(): List<BankHandlerConfig>

    @Insert
    suspend fun insert(config: BankHandlerConfig): Long

    @Update
    suspend fun update(config: BankHandlerConfig)

    @Delete
    suspend fun delete(config: BankHandlerConfig)
}
