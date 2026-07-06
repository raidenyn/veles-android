package me.nagaev.veles.otp.handlers

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import me.nagaev.veles.otp.config.BankHandlerConfig
import java.util.logging.Level
import java.util.logging.Logger
import java.util.regex.PatternSyntaxException

class HandlerChainReloader(
    private val configs: Flow<List<BankHandlerConfig>>,
    private val notifier: OtpMessageHandler,
    private val restartBackoffMs: Long = 1000L,
) {
    @Volatile
    var messageHandler: MessageHandler = CompositeMessageHandler(emptyList())
        private set

    internal var job: Job? = null
        private set

    @Suppress("TooGenericExceptionCaught") // resilience boundary: must restart on any flow-level failure
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
                        } catch (e: PatternSyntaxException) {
                            // A malformed regex in one config must not freeze hot-reload;
                            // keep the previous chain and let the next edit take effect.
                            logger().log(Level.WARNING, "Failed to rebuild handler chain", e)
                        }
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    // Restart on any flow-level failure (e.g. a cursor/DB error during the
                    // invalidation-tracker re-query). CancellationException is rethrown above.
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
