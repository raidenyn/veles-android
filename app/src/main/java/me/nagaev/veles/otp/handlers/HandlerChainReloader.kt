package me.nagaev.veles.otp.handlers

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import me.nagaev.veles.otp.config.BankHandlerConfig
import java.util.logging.Level
import java.util.logging.Logger

class HandlerChainReloader(
    private val configs: Flow<List<BankHandlerConfig>>,
    private val notifier: OtpMessageHandler,
    private val restartBackoffMs: Long = 1000L,
) {
    @Volatile
    var messageHandler: MessageHandler = CompositeMessageHandler(emptyList())
        private set

    private var job: Job? = null

    fun start(scope: CoroutineScope) {
        job?.cancel()
        job = scope.launch {
            var failed = true
            while (isActive && failed) {
                failed = false
                try {
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
                        } catch (e: CancellationException) {
                            throw e
                        } catch (e: Throwable) {
                            logger().log(Level.WARNING, "Failed to rebuild handler chain", e)
                        }
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Throwable) {
                    failed = true
                    logger().log(Level.WARNING, "Config flow failed, restarting collector", e)
                    delay(restartBackoffMs)
                }
            }
        }
    }

    fun stop() {
        job?.cancel()
    }

    private fun logger(): Logger = Logger.getLogger("HandlerChainReloader")
}
