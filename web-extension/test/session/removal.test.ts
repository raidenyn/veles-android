import { describe, expect, it } from 'vitest';

import {
    REMOVAL_RESPONSE_TIMEOUT_MS,
    SessionManager,
    type SessionTimers,
} from '../../src/connector/sessions';
import { composeHandlers } from '../../src/pairing/activation';
import { IDLE_TIMEOUT_MS } from '../../src/session/limits';
import { SESSION_QUEUE_LIMIT } from '../../src/session/queue';
import { createRemovalHandler, type RemovalOperations } from '../../src/session/removal';
import { AppErrorCode, RecordType, SessionClass } from '../../src/session/wire';
import { TrustStoreError, bytes, type TrustState } from '../../src/trust/model';

import { HANDLE_CONNECTION, HANDLE_DEVICE, disconnectedEvent } from '../connector/fixtures';
import type { MockNativePort } from '../connector/mock-port';
import {
    ackSend,
    answerHandshake,
    connectForSession,
    createTestController,
    createTestTrust,
    decodeRecordHeader,
    deferred,
    emitRecord,
    negotiateHost,
    postedSends,
    sessionEngineDeps,
    type PhoneSide,
    type PostedSend,
    type TestController,
    type TestTrust,
} from '../connector/session-harness';
import {
    PEER_STATE_BELOW_ACTIVE,
    PhoneHarness,
    TestPhone,
    appErrorBody,
    sendBytes,
    terminatedReason,
} from '../session/client-harness';

// OTP2-REC-013 mutual removal on the Chrome endpoint (design D4, mutual-peer-removal):
//
// - the responder deletes its record inside the handler, replies `removal_response` as the last
//   record in that direction and asks for the reply-then-close outcome; it never tears the
//   session down itself;
// - a responder whose delete fails sends no response and ends through the engine's failure path,
//   which is what lets the store-failure callback escalate;
// - the requester deletes only after a `removal_response` the engine delivered while the session
//   was awaiting one, and keeps its record when the response is lost, the session ends or the
//   deadline expires;
// - the requester's 10 000 ms deadline is an owner-owned timer on the injected `SessionTimers`,
//   because the connector's 15 000 ms tick is longer than it. No test sleeps or reads a clock.

const EMPTY = new Uint8Array(0);

/**
 * A fake `RemovalOperations` (a later task implements the coordinator's real contract). A
 * configured failure reports the store-failure episode once and then raises `TrustStoreError`,
 * exactly as the repository's callback and its caller do.
 */
class RecordingOperations implements RemovalOperations {
    readonly deleted: string[] = [];
    readonly storeFailures: string[] = [];
    readonly #failure: boolean;

    constructor(options: { fail?: boolean } = {}) {
        this.#failure = options.fail ?? false;
    }

    async deleteFenced(peerId: string): Promise<void> {
        if (this.#failure) {
            this.storeFailures.push('re-pair required');
            throw new TrustStoreError();
        }
        this.deleted.push(peerId);
    }
}

/** A deletion that suspends until `allow`, so a test can race a teardown into the handler. */
class SuspendedOperations implements RemovalOperations {
    readonly deleted: string[] = [];
    readonly entered = deferred();
    readonly #gate = deferred();

    allow(): void {
        this.#gate.resolve();
    }

    async deleteFenced(peerId: string): Promise<void> {
        this.entered.resolve();
        await this.#gate.promise;
        this.deleted.push(peerId);
    }
}

/**
 * Fills every remaining queue slot behind the suspended handler (the handler step holds the only
 * other one), so the next submission — the teardown's or the deadline's settlement — would be
 * refused by the bounded queue. The accepted jobs only run when the suspended handler returns.
 * Nothing here refuses a submission: a refused `sendApplication`/record would itself end the
 * session through the queue's overflow path, which is not the race under test.
 */
function saturateQueue(fixture: RemovalFixture): void {
    for (let index = 0; index < SESSION_QUEUE_LIMIT - 1; index += 1) {
        void fixture.manager
            .sendApplication(HANDLE_CONNECTION, RecordType.EVENT_ACK, new Uint8Array(32).fill(0x33))
            .catch(() => undefined);
    }
}

interface ScheduledTask {
    readonly delay: number;
    readonly run: () => void;
    cancelled: boolean;
}

/**
 * The injected `SessionTimers` surface of the production `SessionManager`. Every scheduled task is
 * recorded, so a test can fire the removal deadline and observe its cancellation without a wall
 * clock, a real timer or a sleep.
 */
class RemovalTimers implements SessionTimers {
    readonly tasks: ScheduledTask[] = [];

    readonly setTimeout = (fn: () => void, ms: number): unknown => {
        const task = { delay: ms, run: fn, cancelled: false };
        this.tasks.push(task);
        return task;
    };

    readonly clearTimeout = (handle: unknown): void => {
        (handle as ScheduledTask).cancelled = true;
    };

    /** The tasks armed with the removal response deadline. */
    deadlines(): ScheduledTask[] {
        return this.tasks.filter((task) => task.delay === REMOVAL_RESPONSE_TIMEOUT_MS);
    }
}

function noopHandler() {
    return { handles: () => false, handle: async () => undefined };
}

interface RemovalFixture extends TestController {
    trust: TestTrust;
    manager: SessionManager;
    phone: TestPhone;
    peerId: string;
    timers: RemovalTimers;
}

/**
 * A production `SessionManager` over the shared test trust, with the removal handler inside the
 * production handler composition and the owner's deadline timers recorded.
 */
async function createFixture(
    options: { state?: TrustState; operations?: RemovalOperations } = {},
): Promise<RemovalFixture> {
    const context = createTestController();
    const trust = await createTestTrust(context.clock);
    const phone = await TestPhone.create();
    const peerId = await trust.addPeer(phone, { state: options.state ?? 'active' });
    const timers = new RemovalTimers();
    const holder: { current?: SessionManager } = {};
    trust.bindVerifier(async (peer, stage, proof) =>
        holder.current === undefined ? false : holder.current.verifier(peer, stage, proof),
    );
    const deps = sessionEngineDeps(trust, context.clock, bytes(trust.identity.installationId, 16), {
        handler: composeHandlers(
            createRemovalHandler(options.operations ?? new RecordingOperations()),
            noopHandler(),
        ),
    });
    const manager = new SessionManager(
        context.controller,
        trust.repository,
        (handle, peer) => deps(handle, peer),
        () => context.controller.portGeneration,
        { setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout },
    );
    holder.current = manager;
    await negotiateHost(context);
    return { ...context, trust, manager, phone, peerId, timers };
}

async function openSession(
    fixture: RemovalFixture,
    options: { peerState?: number } = {},
): Promise<PhoneSide> {
    await connectForSession(fixture, HANDLE_CONNECTION, HANDLE_DEVICE);
    const opened = fixture.manager.open(HANDLE_CONNECTION, fixture.peerId);
    const side = await answerHandshake(fixture, {
        phone: fixture.phone,
        connectionHandle: HANDLE_CONNECTION,
        ...(options.peerState === undefined ? {} : { peerState: options.peerState }),
    });
    await expect(opened).resolves.toEqual({ result: 'authenticated', peerId: fixture.peerId });
    return side;
}

/** Runs `requestRemoval` up to the posted request and acknowledges that send. */
async function sendRequest(
    fixture: RemovalFixture,
): Promise<{ removal: Promise<string>; request: PostedSend }> {
    const removal = fixture.manager.requestRemoval(fixture.peerId);
    const request = await nextSendFor(fixture.currentPort(), HANDLE_CONNECTION);
    await ackSend(fixture.currentPort(), request);
    return { removal, request };
}

function terminations(context: TestController, handle: string): string[] {
    return context.controller.state.diagnostics
        .entries()
        .filter(
            (entry) => entry.type === 'session_terminated' && entry.connection_handle === handle,
        )
        .map((entry) => entry.code ?? '');
}

/** Waits for the next posted send of one handle without a timer budget. */
async function nextSendFor(port: MockNativePort, handle: string): Promise<PostedSend> {
    for (;;) {
        const message = await port.nextPosted();
        if (message.type !== 'send' || message.connection_handle !== handle) continue;
        return {
            requestId: message.request_id as string,
            handle,
            bytes: Uint8Array.from(Buffer.from(message.record_base64 as string, 'base64')),
        };
    }
}

/** Waits for the next posted disconnect request of one handle without a timer budget. */
async function nextDisconnectFor(
    port: MockNativePort,
    handle: string,
): Promise<Record<string, unknown>> {
    for (;;) {
        const message = await port.nextPosted();
        if (message.type === 'disconnect' && message.connection_handle === handle) return message;
    }
}

/** The record types this endpoint actually sent on one handle, in order. */
function sentTypes(port: MockNativePort, handle: string): number[] {
    return postedSends(port, handle).flatMap((send) => {
        const header = decodeRecordHeader(send.bytes);
        return header === null ? [] : [header.type];
    });
}

// The engine half: the handler contract and the requester's fencing.

describe('removal handler', () => {
    it('deletes, replies removal_response last and asks the engine to close', async () => {
        const ops = new RecordingOperations();
        const harness = await PhoneHarness.create({ handler: createRemovalHandler(ops) });
        const { authenticated } = await harness.openSession();
        expect(authenticated).toMatchObject({
            kind: 'authenticated',
            sessionClass: SessionClass.FULL,
        });

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.REMOVAL_REQUEST, 1n),
        );

        expect(ops.deleted).toEqual([harness.peerId]);
        expect(effects).toHaveLength(3);
        const response = await harness.openClientApplication(sendBytes(effects), 1n);
        expect(response.type).toBe(RecordType.REMOVAL_RESPONSE);
        expect(response.body).toEqual(EMPTY);
        expect(effects[1]).toEqual({
            kind: 'terminated',
            reason: 'removal',
            holdForDelivery: true,
        });
        expect(effects[2]).toEqual({ kind: 'close' });
        expect(effects.filter((effect) => effect.kind === 'send')).toHaveLength(1);
        expect(effects.some((effect) => effect.kind === 'deliver')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
        // Nothing is accepted after the reply-then-close transition.
        expect(
            await harness.engine.onInbound(await harness.phoneRecord(RecordType.APP_HEARTBEAT, 2n)),
        ).toEqual([]);
    });

    it('reports the removal stage without deferring delivery for the requester', async () => {
        const ops = new RecordingOperations();
        const harness = await PhoneHarness.create({ handler: createRemovalHandler(ops) });
        await harness.openSession();
        harness.engine.setRemovalPending(true);

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.REMOVAL_RESPONSE, 1n),
        );

        expect(ops.deleted).toEqual([harness.peerId]);
        expect(effects).toHaveLength(2);
        expect(effects[0]).toEqual({ kind: 'terminated', reason: 'removal' });
        expect(effects[1]).toEqual({ kind: 'close' });
        expect(harness.engine.isOpen).toBe(false);
    });

    it('keeps the app_error terminal effect unflagged when the handler replies with it', async () => {
        const handler = {
            handles: (type: number) => type === RecordType.REMOVAL_REQUEST,
            async handle(
                _type: number,
                _body: Uint8Array,
                reply: (type: number, body: Uint8Array) => Promise<void>,
            ): Promise<void> {
                await reply(RecordType.APP_ERROR, appErrorBody(AppErrorCode.PROTOCOL_VIOLATION));
            },
        };
        const harness = await PhoneHarness.create({ handler });
        await harness.openSession();

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.REMOVAL_REQUEST, 1n),
        );

        expect(effects[1]).toEqual({ kind: 'terminated', reason: 'app_error' });
        expect(effects[2]).toEqual({ kind: 'close' });
        expect(harness.engine.isOpen).toBe(false);
    });

    it('keeps the authorization terminal effect unflagged on a request while awaiting', async () => {
        const ops = new RecordingOperations();
        const harness = await PhoneHarness.create({ handler: createRemovalHandler(ops) });
        await harness.openSession();
        harness.engine.setRemovalPending(true);

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.REMOVAL_REQUEST, 1n),
        );

        expect(effects[0]).toEqual({ kind: 'terminated', reason: 'authorization' });
        expect(effects.some((effect) => effect.kind === 'deliver')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
        expect(ops.deleted).toEqual([]);
    });

    it('ends the session without a reply when the deletion fails', async () => {
        const ops = new RecordingOperations({ fail: true });
        const harness = await PhoneHarness.create({ handler: createRemovalHandler(ops) });
        await harness.openSession();

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.REMOVAL_REQUEST, 1n),
        );

        expect(ops.storeFailures).toEqual(['re-pair required']);
        expect(ops.deleted).toEqual([]);
        expect(terminatedReason(effects)).toBe('dependency');
        expect(effects[0]).toEqual({ kind: 'terminated', reason: 'dependency' });
        expect(effects.some((effect) => effect.kind === 'send')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
    });

    it('fails an application send while the response is awaited', async () => {
        const ops = new RecordingOperations();
        const harness = await PhoneHarness.create({ handler: createRemovalHandler(ops) });
        await harness.openSession();
        harness.engine.setRemovalPending(true);

        const effects = await harness.engine.sendApplication(
            RecordType.EVENT_ACK,
            new Uint8Array(32).fill(0x11),
        );

        expect(terminatedReason(effects)).toBe('authorization');
        expect(effects.some((effect) => effect.kind === 'send')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
        expect(ops.deleted).toEqual([]);
    });

    it('fails an inbound record other than the response, heartbeat or app_error', async () => {
        const ops = new RecordingOperations();
        const harness = await PhoneHarness.create({ handler: createRemovalHandler(ops) });
        await harness.openSession();
        harness.engine.setRemovalPending(true);

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.PUSH_EVENT, 1n, new Uint8Array(8).fill(1)),
        );

        expect(terminatedReason(effects)).toBe('authorization');
        expect(effects.some((effect) => effect.kind === 'deliver')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
        expect(ops.deleted).toEqual([]);
    });

    it('still allows heartbeats and app_error while awaiting the response', async () => {
        const ops = new RecordingOperations();
        const harness = await PhoneHarness.create({ handler: createRemovalHandler(ops) });
        await harness.openSession();
        harness.engine.setRemovalPending(true);

        expect(
            await harness.engine.onInbound(await harness.phoneRecord(RecordType.APP_HEARTBEAT, 1n)),
        ).toEqual([]);
        expect(harness.engine.isOpen).toBe(true);

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(
                RecordType.APP_ERROR,
                2n,
                appErrorBody(AppErrorCode.SESSION_EXPIRED),
            ),
        );

        expect(terminatedReason(effects)).toBe('app_error');
        expect(harness.engine.isOpen).toBe(false);
        expect(ops.deleted).toEqual([]);
    });

    it('treats an unsolicited response as a protocol violation and deletes nothing', async () => {
        const ops = new RecordingOperations();
        const harness = await PhoneHarness.create({ handler: createRemovalHandler(ops) });
        await harness.openSession();

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.REMOVAL_RESPONSE, 1n),
        );

        expect(terminatedReason(effects)).toBe('authorization');
        expect(effects.some((effect) => effect.kind === 'deliver')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
        expect(ops.deleted).toEqual([]);
    });

    it('rejects a removal request on a non-full session before any trust change', async () => {
        const ops = new RecordingOperations();
        const harness = await PhoneHarness.create({ handler: createRemovalHandler(ops) });
        const { authenticated } = await harness.openSession({
            peerState: PEER_STATE_BELOW_ACTIVE,
        });
        expect(authenticated).toMatchObject({
            kind: 'authenticated',
            sessionClass: SessionClass.FULL_PENDING_PEER,
        });

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.REMOVAL_REQUEST, 1n),
        );

        expect(terminatedReason(effects)).toBe('authorization');
        expect(effects.some((effect) => effect.kind === 'deliver')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
        expect(ops.deleted).toEqual([]);
    });

    it('keeps the generic close-after-reply stage for a non-removal record', async () => {
        const handler = {
            handles: (type: number) => type === RecordType.PUSH_EVENT,
            async handle(
                _type: number,
                _body: Uint8Array,
                reply: (type: number, body: Uint8Array) => Promise<void>,
            ): Promise<void> {
                await reply(RecordType.EVENT_ACK, new Uint8Array(32).fill(0x22));
            },
            closeAfterReply: (type: number) => type === RecordType.PUSH_EVENT,
        };
        const harness = await PhoneHarness.create({ handler });
        await harness.openSession();

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.PUSH_EVENT, 1n, new Uint8Array(4)),
        );

        expect(effects).toHaveLength(3);
        expect(effects[1]).toEqual({ kind: 'terminated', reason: 'close_after_reply' });
        expect(effects[2]).toEqual({ kind: 'close' });
        expect(harness.engine.isOpen).toBe(false);
    });

    it('keeps the record when the response never arrives', async () => {
        const ops = new RecordingOperations();
        const harness = await PhoneHarness.create({ handler: createRemovalHandler(ops) });
        await harness.openSession();
        harness.engine.setRemovalPending(true);

        // The peer never answers: the session ends on its own idle deadline, not a removal.
        harness.clock.advance(IDLE_TIMEOUT_MS);
        const effects = await harness.engine.onTick();

        expect(terminatedReason(effects)).toBe('idle');
        expect(harness.engine.isOpen).toBe(false);
        expect(ops.deleted).toEqual([]);
        expect(await harness.engine.onTick()).toEqual([]);
    });
});

// The owner half: the requester's owner API and the 10 000 ms deadline.

describe('SessionManager.requestRemoval', () => {
    it('settles removed only after a valid removal_response', async () => {
        const ops = new RecordingOperations();
        const fixture = await createFixture({ operations: ops });
        const side = await openSession(fixture);
        const port = fixture.currentPort();

        const { removal, request } = await sendRequest(fixture);
        const openedRequest = await side.openClientRecord(request.bytes, 1n);
        expect(openedRequest.type).toBe(RecordType.REMOVAL_REQUEST);
        expect(openedRequest.body).toEqual(EMPTY);

        // Nothing is deleted before the response; the session stays open and awaiting.
        expect(ops.deleted).toEqual([]);
        expect(fixture.manager.authenticatedSessions()).toHaveLength(1);

        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(RecordType.REMOVAL_RESPONSE, 1n),
        );

        await expect(removal).resolves.toBe('removed');
        expect(ops.deleted).toEqual([fixture.peerId]);
        expect(terminations(fixture, HANDLE_CONNECTION)).toEqual(['removal']);
        expect(fixture.manager.authenticatedSessions()).toHaveLength(0);
        await nextDisconnectFor(port, HANDLE_CONNECTION);
        // The request is the last record the requester sends.
        expect(sentTypes(port, HANDLE_CONNECTION).at(-1)).toBe(RecordType.REMOVAL_REQUEST);
        expect(
            sentTypes(port, HANDLE_CONNECTION).filter(
                (type) => type === RecordType.REMOVAL_REQUEST,
            ),
        ).toHaveLength(1);
    });

    it('deletes and replies on a removal request through the production composition', async () => {
        const ops = new RecordingOperations();
        const fixture = await createFixture({ operations: ops });
        const side = await openSession(fixture);
        const port = fixture.currentPort();

        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(RecordType.REMOVAL_REQUEST, 1n),
        );
        const reply = await nextSendFor(port, HANDLE_CONNECTION);
        const opened = await side.openClientRecord(reply.bytes, 1n);
        expect(opened.type).toBe(RecordType.REMOVAL_RESPONSE);
        expect(opened.body).toEqual(EMPTY);
        await ackSend(port, reply);

        await nextDisconnectFor(port, HANDLE_CONNECTION);
        expect(ops.deleted).toEqual([fixture.peerId]);
        expect(terminations(fixture, HANDLE_CONNECTION)).toEqual(['removal']);
        expect(fixture.manager.authenticatedSessions()).toHaveLength(0);
        expect(sentTypes(port, HANDLE_CONNECTION).at(-1)).toBe(RecordType.REMOVAL_RESPONSE);
    });

    it('is kept-not-connected without a full session of that peer', async () => {
        const ops = new RecordingOperations();
        const fixture = await createFixture({ operations: ops });

        await expect(fixture.manager.requestRemoval(fixture.peerId)).resolves.toBe(
            'kept-not-connected',
        );

        // A full-pending-peer session is not a removal destination.
        await openSession(fixture, { peerState: PEER_STATE_BELOW_ACTIVE });
        await expect(fixture.manager.requestRemoval(fixture.peerId)).resolves.toBe(
            'kept-not-connected',
        );
        expect(sentTypes(fixture.currentPort(), HANDLE_CONNECTION)).not.toContain(
            RecordType.REMOVAL_REQUEST,
        );
        expect(ops.deleted).toEqual([]);
        expect(fixture.manager.authenticatedSessions()).toHaveLength(1);
    });

    it('keeps the record when the session ends before the response', async () => {
        const ops = new RecordingOperations();
        const fixture = await createFixture({ operations: ops });
        await openSession(fixture);
        const port = fixture.currentPort();

        const { removal } = await sendRequest(fixture);
        await port.emit(
            disconnectedEvent({ request_id: undefined, connection_handle: HANDLE_CONNECTION }),
        );

        await expect(removal).resolves.toBe('kept-no-response');
        expect(ops.deleted).toEqual([]);
        expect(fixture.manager.authenticatedSessions()).toHaveLength(0);
        expect(fixture.timers.deadlines()).toHaveLength(1);
        expect(fixture.timers.deadlines()[0]?.cancelled).toBe(true);
    });

    it('expires at exactly REMOVAL_RESPONSE_TIMEOUT_MS and keeps the record', async () => {
        const ops = new RecordingOperations();
        const fixture = await createFixture({ operations: ops });
        await openSession(fixture);
        const port = fixture.currentPort();

        const { removal } = await sendRequest(fixture);

        expect(REMOVAL_RESPONSE_TIMEOUT_MS).toBe(10_000);
        const deadlines = fixture.timers.deadlines();
        expect(deadlines).toHaveLength(1);
        expect(deadlines[0]?.cancelled).toBe(false);
        expect(ops.deleted).toEqual([]);
        expect(fixture.manager.authenticatedSessions()).toHaveLength(1);

        deadlines[0]?.run();

        await expect(removal).resolves.toBe('kept-no-response');
        expect(ops.deleted).toEqual([]);
        expect(terminations(fixture, HANDLE_CONNECTION)).toEqual(['removal_timeout']);
        expect(fixture.manager.authenticatedSessions()).toHaveLength(0);
        await nextDisconnectFor(port, HANDLE_CONNECTION);
    });

    it('cancels the deadline on a response, a disconnect and a teardown', async () => {
        // A response settles the exchange and the deadline is cancelled.
        const answeredOps = new RecordingOperations();
        const answered = await createFixture({ operations: answeredOps });
        const answeredSide = await openSession(answered);
        const answeredRequest = await sendRequest(answered);
        await emitRecord(
            answered.currentPort(),
            HANDLE_CONNECTION,
            await answeredSide.applicationFrame(RecordType.REMOVAL_RESPONSE, 1n),
        );
        await expect(answeredRequest.removal).resolves.toBe('removed');
        expect(answered.timers.deadlines()[0]?.cancelled).toBe(true);
        expect(answeredOps.deleted).toEqual([answered.peerId]);

        // A disconnect settles the exchange and the deadline is cancelled.
        const droppedOps = new RecordingOperations();
        const dropped = await createFixture({ operations: droppedOps });
        await openSession(dropped);
        const droppedRequest = await sendRequest(dropped);
        await dropped
            .currentPort()
            .emit(
                disconnectedEvent({ request_id: undefined, connection_handle: HANDLE_CONNECTION }),
            );
        await expect(droppedRequest.removal).resolves.toBe('kept-no-response');
        expect(dropped.timers.deadlines()[0]?.cancelled).toBe(true);
        expect(droppedOps.deleted).toEqual([]);

        // A teardown settles the exchange and the deadline is cancelled.
        const closedOps = new RecordingOperations();
        const closed = await createFixture({ operations: closedOps });
        await openSession(closed);
        const closedRequest = await sendRequest(closed);
        await closed.manager.closeAll();
        await expect(closedRequest.removal).resolves.toBe('kept-no-response');
        expect(closed.timers.deadlines()[0]?.cancelled).toBe(true);
        expect(closedOps.deleted).toEqual([]);

        // A fresh exchange after all three still works, so no state leaked.
        const nextOps = new RecordingOperations();
        const next = await createFixture({ operations: nextOps });
        const nextSide = await openSession(next);
        const nextRequest = await sendRequest(next);
        await emitRecord(
            next.currentPort(),
            HANDLE_CONNECTION,
            await nextSide.applicationFrame(RecordType.REMOVAL_RESPONSE, 1n),
        );
        await expect(nextRequest.removal).resolves.toBe('removed');
        expect(nextOps.deleted).toEqual([next.peerId]);
    });

    it('settles removed when a teardown races the deletion', async () => {
        const ops = new SuspendedOperations();
        const fixture = await createFixture({ operations: ops });
        const side = await openSession(fixture);
        const port = fixture.currentPort();

        const { removal } = await sendRequest(fixture);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(RecordType.REMOVAL_RESPONSE, 1n),
        );
        await ops.entered.promise;
        expect(ops.deleted).toEqual([]);

        // The session ends while the handler is still deleting.
        fixture.manager.close(HANDLE_CONNECTION);
        ops.allow();

        // The deletion that already happened still settles the exchange as removed.
        await expect(removal).resolves.toBe('removed');
        expect(ops.deleted).toEqual([fixture.peerId]);
        expect(fixture.manager.authenticatedSessions()).toHaveLength(0);
        expect(fixture.timers.deadlines()[0]?.cancelled).toBe(true);
        // Nothing is sent after the removal request.
        expect(sentTypes(port, HANDLE_CONNECTION).at(-1)).toBe(RecordType.REMOVAL_REQUEST);
    });

    it('settles removed when a teardown races the deletion on a saturated queue', async () => {
        const ops = new SuspendedOperations();
        const fixture = await createFixture({ operations: ops });
        const side = await openSession(fixture);
        const port = fixture.currentPort();

        const { removal } = await sendRequest(fixture);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(RecordType.REMOVAL_RESPONSE, 1n),
        );
        await ops.entered.promise;
        expect(ops.deleted).toEqual([]);

        // The queue is at its limit, so the teardown's settlement job would be refused and an
        // unconditional fallback would settle before the deletion below completes.
        saturateQueue(fixture);
        fixture.manager.close(HANDLE_CONNECTION);
        ops.allow();

        await expect(removal).resolves.toBe('removed');
        expect(ops.deleted).toEqual([fixture.peerId]);
        expect(fixture.manager.authenticatedSessions()).toHaveLength(0);
        expect(fixture.timers.deadlines()[0]?.cancelled).toBe(true);
    });

    it('settles removed when the deadline fires on a saturated queue during the deletion', async () => {
        const ops = new SuspendedOperations();
        const fixture = await createFixture({ operations: ops });
        const side = await openSession(fixture);
        const port = fixture.currentPort();

        const { removal } = await sendRequest(fixture);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(RecordType.REMOVAL_RESPONSE, 1n),
        );
        await ops.entered.promise;

        // The deadline expires while the deletion is suspended; its expiry cannot take a queue
        // slot either, so it must wait for the deletion instead of settling first.
        saturateQueue(fixture);
        fixture.timers.deadlines()[0]?.run();
        ops.allow();

        await expect(removal).resolves.toBe('removed');
        expect(ops.deleted).toEqual([fixture.peerId]);
        expect(terminations(fixture, HANDLE_CONNECTION)).toEqual(['removal']);
    });

    it('settles removed when the deletion outlives the idle deadline', async () => {
        const ops = new SuspendedOperations();
        const fixture = await createFixture({ operations: ops });
        const side = await openSession(fixture);
        const port = fixture.currentPort();

        const { removal } = await sendRequest(fixture);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(RecordType.REMOVAL_RESPONSE, 1n),
        );
        await ops.entered.promise;

        // The handler's successful deletion is the outcome that matters, not the idle deadline
        // it happened to outlive: trust is already gone.
        fixture.clock.advance(IDLE_TIMEOUT_MS);
        ops.allow();

        await expect(removal).resolves.toBe('removed');
        expect(ops.deleted).toEqual([fixture.peerId]);
        expect(terminations(fixture, HANDLE_CONNECTION)).toEqual(['removal']);
        expect(fixture.manager.authenticatedSessions()).toHaveLength(0);
    });
});
