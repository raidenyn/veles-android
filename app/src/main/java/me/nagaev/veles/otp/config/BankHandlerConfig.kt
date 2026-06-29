package me.nagaev.veles.otp.config

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "bank_handler_configs")
data class BankHandlerConfig(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val name: String,
    val otpRegex: String,
    val moneyRegex: String,
    val merchantRegex: String,
    val createdAt: Long,
    val updatedAt: Long
)
