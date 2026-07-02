package me.nagaev.veles.otp.config.io

import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import me.nagaev.veles.otp.config.BankHandlerConfig

object ConfigSerializer {
    private val json = Json {
        prettyPrint = true
        ignoreUnknownKeys = false
        encodeDefaults = true
    }
    private val listSerializer = ListSerializer(BankConfigJson.serializer())

    fun toJson(configs: List<BankHandlerConfig>): String {
        val payload = configs.map {
            BankConfigJson(
                name = it.name,
                regex = RegexJson(
                    otp = it.otpRegex,
                    amount = it.moneyRegex,
                    merchant = it.merchantRegex,
                ),
            )
        }
        return json.encodeToString(listSerializer, payload)
    }

    fun fromJson(text: String): List<BankConfigJson> = json.decodeFromString(listSerializer, text)
}
