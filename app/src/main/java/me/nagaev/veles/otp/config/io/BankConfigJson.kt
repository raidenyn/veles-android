package me.nagaev.veles.otp.config.io

import kotlinx.serialization.Serializable

@Serializable
data class BankConfigJson(
    val name: String,
    val regex: RegexJson,
)

@Serializable
data class RegexJson(
    val otp: String,
    val amount: String,
    val merchant: String,
)