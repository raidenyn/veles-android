package me.nagaev.veles.otp.handlers

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.launch
import me.nagaev.veles.otp.config.BankHandlerConfig
import java.util.logging.Level
import java.util.logging.Logger

class HandlerChainReloader(
    private val configs: Flow<List<BankHandlerConfig>>,
    private val notifier: OtpMessageHandler,
) {
    @Volatile
    var messageHandler: MessageHandler = CompositeMessageHandler(emptyList())
        private set

    private var job: Job? = null

    fun start(scope: CoroutineScope) {
        job = scope.launch {
            configs.collect { list ->
                try {
                    messageHandler = CompositeMessageHandler(
                        list.map { config ->
                            RegexMessageHandler(
                                name = config.name,
                                otpRegex = config.otpRegex,
                                moneyRegex = config.moneyRegex,
                                merchantRegex = config.merchantRegex,
                                notifier = notifier,
                            )
                        },
                    )
                } catch (e: Throwable) {
                    Logger.getLogger("HandlerChainReloader")
                        .log(Level.WARNING, "Failed to rebuild handler chain", e)
                }
            }
        }
    }

    fun stop() {
        job?.cancel()
    }
}
