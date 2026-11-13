package me.nagaev.veles.session

/**
 * Composes the production activation handler with an optional build-type extension handler
 * (the debug probe). A null extension never removes the activation handler, so a release build
 * still runs the durable activation exchange (OTP-10 plan 9d).
 *
 * Each child is asked once for its session-scoped instance; the composite then delegates the
 * record surface, the session context and the unprompted sends to those instances.
 */
fun composeApplicationHandlers(vararg handlers: ApplicationHandler?): ApplicationHandler? {
    val present = handlers.filterNotNull()
    return when (present.size) {
        0 -> null
        1 -> present.single()
        else -> CompositeApplicationHandler(present)
    }
}

private class CompositeApplicationHandler(
    private val handlers: List<ApplicationHandler>,
) : ApplicationHandler {
    private val sessionHandlers: List<ApplicationHandler> = handlers

    override fun handles(type: Int): Boolean = sessionHandlers.any { it.handles(type) }

    override suspend fun handle(
        type: Int,
        body: ByteArray,
        reply: suspend (type: Int, body: ByteArray) -> Unit,
    ) {
        var handled = false
        sessionHandlers.forEach { handler ->
            if (handler.handles(type)) {
                handler.handle(type, body, reply)
                handled = true
            }
        }
        if (!handled) throw SessionReject()
    }

    @Suppress("MaxLineLength") // ktlint keeps a single expression at this width.
    override fun forSession(context: SessionContext): ApplicationHandler =
        CompositeApplicationHandler(handlers.map { it.forSession(context) })

    override fun unprompted(): List<UnpromptedApplication> = sessionHandlers.flatMap { it.unprompted() }
}
