package me.nagaev.veles.debugprobe

import me.nagaev.veles.session.ApplicationHandler
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.SessionContext
import me.nagaev.veles.session.SessionReject

/**
 * Debug-only Gate B pull router (OTP-12 design D5, spec "One responder per pull"): while the
 * fixture is armed it answers `pull_request` with the fixture's history and the synthetic probe
 * stays silent; otherwise the probe answers exactly as before. The mode is read once per pull,
 * so a change after the engine's `handles` check can never leave a pull unanswered or answered
 * twice.
 *
 * `event_ack` always goes to the component that owns the id. The shape and installation id are
 * checked with the probe's own [validateAck] first, so the router cannot weaken the probe's
 * fail-closed behaviour: a foreign or malformed ack still throws [SessionReject]. A fixture-owned
 * id is acknowledged (and logged) by the fixture and never reaches the probe; any other id is the
 * probe's OTP2-REC-032a no-op. Each pull logs the component that answered it.
 */
internal class GateBPullRouter(
    private val probe: ApplicationHandler,
    private val fixture: GateBFixture,
    private val mode: () -> Boolean = { fixture.armed },
    private val log: (String) -> Unit = {},
) : ApplicationHandler {
    private val localInstallationId: ByteArray = fixture.installationId()

    override fun handles(type: Int): Boolean = type == RecordType.PULL_REQUEST.wire || type == RecordType.EVENT_ACK.wire

    override suspend fun handle(
        type: Int,
        body: ByteArray,
        reply: suspend (type: Int, body: ByteArray) -> Unit,
    ) {
        when (type) {
            RecordType.PULL_REQUEST.wire -> pull(body, reply)
            RecordType.EVENT_ACK.wire -> acknowledge(body)
            else -> throw SessionReject()
        }
    }

    override fun forSession(context: SessionContext): ApplicationHandler = GateBPullRouter(
        probe = probe.forSession(context),
        fixture = fixture,
        mode = mode,
        log = log,
    )

    private suspend fun pull(body: ByteArray, reply: suspend (type: Int, body: ByteArray) -> Unit) {
        // The single mode read: the component chosen here is the only one that answers.
        if (mode()) {
            // OTP2-REC-031: `pull_request` has an empty body; the fixture refuses anything else
            // exactly as the probe does.
            if (body.isNotEmpty()) throw SessionReject()
            log(PULL_ANSWERED_FIXTURE)
            reply(RecordType.PULL_EVENTS.wire, fixture.pullBody())
        } else {
            log(PULL_ANSWERED_PROBE)
            probe.handle(RecordType.PULL_REQUEST.wire, body, reply)
        }
    }

    private suspend fun acknowledge(body: ByteArray) {
        val eventId = validateAck(body, localInstallationId)
        // The fixture logs an owned acknowledgement itself; an unowned id is the probe's
        // OTP2-REC-032a no-op, so the ack is never answered twice either.
        if (!fixture.acknowledge(eventId)) {
            probe.handle(RecordType.EVENT_ACK.wire, body) { _, _ -> }
        }
    }

    private companion object {
        private const val PULL_ANSWERED_FIXTURE = "pull answered=fixture"
        private const val PULL_ANSWERED_PROBE = "pull answered=probe"
    }
}
