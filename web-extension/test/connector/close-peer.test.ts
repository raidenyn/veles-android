import { readFileSync } from 'node:fs';

import { beforeAll, describe, expect, it } from 'vitest';

import { SessionManager } from '../../src/connector/sessions';
import { composeHandlers } from '../../src/pairing/activation';
import { initPairingCore } from '../../src/pairing/core';
import { encodePairingFrame, PairingFrameType } from '../../src/pairing/frames';
import { PairingLink, type PairingTransport } from '../../src/pairing/link';
import type { ApplicationHandler } from '../../src/session/engine';
import { RecordType } from '../../src/session/wire';
import { bytes } from '../../src/trust/model';
import { HANDLE_CONNECTION, HANDLE_CONNECTION_2, HANDLE_DEVICE, HANDLE_DEVICE_2 } from './fixtures';
import type { MockNativePort } from './mock-port';
import {
    PhoneSide,
    type PostedSend,
    type TestController,
    type TestTrust,
    SessionClock,
    ackSend,
    answerHandshake,
    connectForSession,
    createTestController,
    createTestTrust,
    deferred,
    emitRecord,
    negotiateHost,
    nextSend,
    sessionEngineDeps,
} from './session-harness';
import { PACKAGE_BYTES, PhonePeer, hexOf } from '../pairing/enrollment-harness';
import { TestPhone } from '../session/client-harness';

// Task 8: terminate sessions by authenticated peer, terminate an enrollment link
// non-destructively, and honour a handler's reply-then-close outcome. Selection is
// only the peer installation id; labels and handles never select.

const HANDSHAKE_HANDLE = 'c000000000000003';
const HANDSHAKE_DEVICE = 'b000000000000003';

beforeAll(async () => {
    await initPairingCore(readFileSync(PACKAGE_BYTES));
});

interface Fixture {
    context: TestController;
    trust: TestTrust;
    manager: SessionManager;
    phone: TestPhone;
    peerId: string;
}

/** A production `SessionManager` over the shared test trust, with optional link registry. */
async function createManager(
    context: TestController,
    trust: TestTrust,
    options: { handler?: ApplicationHandler; links?: () => readonly PairingLink[] } = {},
): Promise<SessionManager> {
    const holder: { current?: SessionManager } = {};
    trust.bindVerifier(async (peer, stage, proof) =>
        holder.current === undefined ? false : holder.current.verifier(peer, stage, proof),
    );
    const deps = sessionEngineDeps(
        trust,
        context.clock,
        bytes(trust.identity.installationId, 16),
        options.handler === undefined ? {} : { handler: options.handler },
    );
    const manager = new SessionManager(
        context.controller,
        trust.repository,
        (handle, peer) => deps(handle, peer),
        () => context.controller.portGeneration,
        {
            setTimeout: context.clock.setTimeout,
            clearTimeout: context.clock.clearTimeout,
        },
        undefined,
        options.links,
    );
    holder.current = manager;
    await negotiateHost(context);
    return manager;
}

async function createFixture(
    options: { handler?: ApplicationHandler; links?: () => readonly PairingLink[] } = {},
): Promise<Fixture> {
    const context = createTestController();
    const trust = await createTestTrust(context.clock);
    const phone = await TestPhone.create();
    const peerId = await trust.addPeer(phone, { state: 'active' });
    const manager = await createManager(context, trust, options);
    return { context, trust, manager, phone, peerId };
}

async function openSession(
    fixture: Fixture,
    handle: string = HANDLE_CONNECTION,
    deviceHandle: string = HANDLE_DEVICE,
): Promise<PhoneSide> {
    await connectForSession(fixture.context, handle, deviceHandle);
    const opened = fixture.manager.open(handle, fixture.peerId);
    const side = await answerHandshake(fixture.context, {
        phone: fixture.phone,
        connectionHandle: handle,
    });
    await expect(opened).resolves.toEqual({ result: 'authenticated', peerId: fixture.peerId });
    return side;
}

function terminations(context: TestController, handle: string): string[] {
    return context.controller.state.diagnostics
        .entries()
        .filter(
            (entry) => entry.type === 'session_terminated' && entry.connection_handle === handle,
        )
        .map((entry) => entry.code ?? '');
}

function disconnects(port: MockNativePort, handle?: string): Record<string, unknown>[] {
    return port.posted.filter(
        (message): message is Record<string, unknown> =>
            typeof message === 'object' &&
            message !== null &&
            (message as { type?: unknown }).type === 'disconnect' &&
            (handle === undefined ||
                (message as { connection_handle?: unknown }).connection_handle === handle),
    );
}

function postedIndex(
    port: MockNativePort,
    predicate: (message: Record<string, unknown>) => boolean,
) {
    return port.posted.findIndex(
        (message) =>
            typeof message === 'object' &&
            message !== null &&
            predicate(message as Record<string, unknown>),
    );
}

/** The non-null assertion form the tests need without the eslint-forbidden operator. */
function defined<T>(value: T | undefined): T {
    if (value === undefined) throw new Error('expected a value');
    return value;
}

function noopHandler(): ApplicationHandler {
    return {
        handles: () => false,
        handle: async () => undefined,
    };
}

/** A removal-handler stand-in: replies `removal_response`, optionally closing after it. */
function removalHandler(options: {
    close: boolean;
    gate?: Promise<void>;
    onHandle?: () => void;
}): ApplicationHandler {
    return {
        handles: (type) => type === RecordType.REMOVAL_REQUEST,
        async handle(_type, _body, reply) {
            options.onHandle?.();
            if (options.gate !== undefined) await options.gate;
            await reply(RecordType.REMOVAL_RESPONSE, new Uint8Array(0));
        },
        closeAfterReply: (type) => options.close && type === RecordType.REMOVAL_REQUEST,
    };
}

class FakeTransport implements PairingTransport {
    readonly sent: Uint8Array[] = [];
    disconnected = false;
    disconnectCount = 0;

    async connect(target: { deviceHandle: string }) {
        return {
            ok: true as const,
            event: {
                type: 'connected' as const,
                envelope_version: 1 as const,
                api_version: 1,
                request_id: `connect-${target.deviceHandle}`,
                connection_handle: `link-${target.deviceHandle}`,
            },
        };
    }

    async send(_handle: string, record: Uint8Array) {
        this.sent.push(record.slice());
        return {
            ok: true as const,
            event: {
                type: 'sent' as const,
                envelope_version: 1 as const,
                api_version: 1,
                request_id: `send-${this.sent.length}`,
                connection_handle: `link-${this.sent.length}`,
            },
        };
    }

    async disconnect() {
        this.disconnected = true;
        this.disconnectCount += 1;
        return {
            ok: true as const,
            event: {
                type: 'disconnected' as const,
                envelope_version: 1 as const,
                api_version: 1,
                connection_handle: `link-${this.sent.length}`,
            },
        };
    }

    onRecord(): () => void {
        return () => undefined;
    }

    private readonly closeListeners = new Map<string, Set<() => void>>();
    private open = true;

    onConnectionClosed(handle: string, listener: () => void): () => void {
        if (!this.open) {
            listener();
            return () => undefined;
        }
        let listeners = this.closeListeners.get(handle);
        if (listeners === undefined) {
            listeners = new Set<() => void>();
            this.closeListeners.set(handle, listeners);
        }
        listeners.add(listener);
        return () => {
            listeners?.delete(listener);
        };
    }

    closeHandle(handle: string): void {
        this.open = false;
        const listeners = this.closeListeners.get(handle);
        this.closeListeners.delete(handle);
        for (const listener of [...(listeners ?? [])]) listener();
    }
}

/**
 * Waits for the next posted send of one handle without a timer budget. Messages
 * posted in between (for example the disconnects a close queued) are skipped.
 */
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

/** Waits for the next posted disconnect request of one handle, without a timer budget. */
async function nextDisconnectFor(
    port: MockNativePort,
    handle: string,
): Promise<Record<string, unknown>> {
    for (;;) {
        const message = await port.nextPosted();
        if (message.type === 'disconnect' && message.connection_handle === handle) return message;
    }
}

/** Drives a link until the phone identity is verified and its pending record persisted. */
async function driveToPending(link: PairingLink, transport: FakeTransport, phone: PhonePeer) {
    await link.receive(phone.offerFor(transport.sent[0]));
    const cA = phone.acceptPoint(transport.sent[1]);
    await link.receive(encodePairingFrame(PairingFrameType.CA, cA));
    await phone.acceptConfirmB(transport.sent[2]);
    await link.receive(await phone.identityFrame());
    await phone.acceptClientIdentity(transport.sent[3]);
}

describe('PairingLink.terminate', () => {
    it('shares one teardown with every concurrent caller', async () => {
        const clock = new SessionClock();
        const trust = await createTestTrust(clock);
        const transport = new FakeTransport();
        const phone = await PhonePeer.create({ code: '000042' });
        const frameStarted = deferred();
        const frameGate = deferred();
        const link = await PairingLink.start({
            controller: transport,
            repo: trust.repository,
            code: '000042',
            clock: clock.now,
            deviceHandle: 'device-concurrent',
            tickIntervalMs: 0,
            validatePoint: async () => {
                frameStarted.resolve();
                await frameGate.promise;
            },
        });
        try {
            const receiving = link.receive(phone.offerFor(transport.sent[0]));
            // The frame is now genuinely in flight and blocked inside the machine.
            await frameStarted.promise;

            let firstSettled = false;
            let secondSettled = false;
            const first = link.terminate().then(() => {
                firstSettled = true;
            });
            const second = link.terminate().then(() => {
                secondSettled = true;
            });
            // A microtask turn proves neither call resolved early; both wait behind
            // the in-flight frame for the single shared teardown.
            await Promise.resolve();
            expect(firstSettled).toBe(false);
            expect(secondSettled).toBe(false);
            expect(transport.disconnectCount).toBe(0);

            frameGate.resolve();
            await receiving;
            await Promise.all([first, second]);
            expect(firstSettled).toBe(true);
            expect(secondSettled).toBe(true);
            // One teardown, not two: both callers awaited the same cached promise.
            expect(transport.disconnectCount).toBe(1);
            expect(link.machine.state).toBe('idle');
        } finally {
            phone.close();
        }
    });
});

describe('SessionManager.closePeer', () => {
    it('closes only matching handles, including a handshaking generation', async () => {
        const fixture = await createFixture();
        const otherPhone = await TestPhone.create();
        const otherPeerId = await fixture.trust.addPeer(otherPhone, { state: 'active' });
        const port = fixture.context.currentPort();

        await openSession(fixture);
        // A second generation of the same peer that has only sent its hello.
        await connectForSession(fixture.context, HANDSHAKE_HANDLE, HANDSHAKE_DEVICE);
        fixture.manager.open(HANDSHAKE_HANDLE, fixture.peerId);
        const hello = await nextSend(port);
        expect(hello.handle).toBe(HANDSHAKE_HANDLE);
        await ackSend(port, hello);

        await connectForSession(fixture.context, HANDLE_CONNECTION_2, HANDLE_DEVICE_2);
        const otherOpened = fixture.manager.open(HANDLE_CONNECTION_2, otherPeerId);
        await answerHandshake(fixture.context, {
            phone: otherPhone,
            connectionHandle: HANDLE_CONNECTION_2,
        });
        await expect(otherOpened).resolves.toEqual({
            result: 'authenticated',
            peerId: otherPeerId,
        });

        await fixture.manager.closePeer(fixture.peerId);

        expect(terminations(fixture.context, HANDLE_CONNECTION)).toEqual(['revoked']);
        expect(terminations(fixture.context, HANDSHAKE_HANDLE)).toEqual(['revoked']);
        expect(terminations(fixture.context, HANDLE_CONNECTION_2)).toEqual([]);
        expect(fixture.manager.authenticatedSessions().map((session) => session.handle)).toEqual([
            HANDLE_CONNECTION_2,
        ]);

        // The other phone's session still seals and sends after the close.
        const sending = fixture.manager.sendApplication(
            HANDLE_CONNECTION_2,
            RecordType.EVENT_ACK,
            new Uint8Array(32).fill(7),
        );
        const outbound = await nextSendFor(port, HANDLE_CONNECTION_2);
        await ackSend(port, outbound);
        await sending;
    });

    it('resolves only after the selected session is torn down', async () => {
        const fixture = await createFixture();
        await openSession(fixture);

        await fixture.manager.closePeer(fixture.peerId);

        // The logical termination already ran: no engine is left and every send is refused.
        await expect(
            fixture.manager.sendApplication(
                HANDLE_CONNECTION,
                RecordType.EVENT_ACK,
                new Uint8Array(32).fill(1),
            ),
        ).rejects.toBeTruthy();
        expect(terminations(fixture.context, HANDLE_CONNECTION)).toEqual(['revoked']);
        expect(disconnects(fixture.context.currentPort(), HANDLE_CONNECTION)).toHaveLength(1);
    });

    it('terminates a matching enrollment link without deleting its pending record', async () => {
        const registry: { links: PairingLink[] } = { links: [] };
        const fixture = await createFixture({ links: () => registry.links });
        const transport = new FakeTransport();
        const phone = await PhonePeer.create({ code: '000042' });
        const link = await PairingLink.start({
            controller: transport,
            repo: fixture.trust.repository,
            code: '000042',
            clock: fixture.context.clock.now,
            deviceHandle: 'device-link',
            tickIntervalMs: 0,
        });
        const unboundTransport = new FakeTransport();
        const unbound = await PairingLink.start({
            controller: unboundTransport,
            repo: fixture.trust.repository,
            code: '000043',
            clock: fixture.context.clock.now,
            deviceHandle: 'device-unbound',
            tickIntervalMs: 0,
        });
        registry.links = [link, unbound];
        try {
            await driveToPending(link, transport, phone);
            const bound = link.boundPeerId();
            expect(bound).toBe(hexOf(phone.installationId));
            expect(unbound.boundPeerId()).toBeUndefined();
            const pending = (await fixture.trust.repository.peers()).find(
                (peer) => peer.installationId === bound,
            );
            expect(pending?.state).toBe('pending');

            await fixture.manager.closePeer(defined(bound));

            expect(link.machine.state).toBe('idle');
            expect(transport.disconnected).toBe(true);
            // An enrollment not yet bound to a peer is never selected.
            expect(unbound.machine.state).toBe('awaiting_offer');
            expect(unboundTransport.disconnected).toBe(false);
            // Design D2/R8: the durable pending record survives a non-destructive terminate.
            const after = (await fixture.trust.repository.peers()).find(
                (peer) => peer.installationId === bound,
            );
            expect(after?.state).toBe('pending');
        } finally {
            await unbound.terminate();
            phone.close();
        }
    });

    it('closeAll terminates every session and link but deletes nothing', async () => {
        const registry: { link?: PairingLink } = {};
        const fixture = await createFixture({
            links: () => (registry.link === undefined ? [] : [registry.link]),
        });
        await openSession(fixture);
        const transport = new FakeTransport();
        const phone = await PhonePeer.create({ code: '000042' });
        const link = await PairingLink.start({
            controller: transport,
            repo: fixture.trust.repository,
            code: '000042',
            clock: fixture.context.clock.now,
            deviceHandle: 'device-all',
            tickIntervalMs: 0,
        });
        registry.link = link;
        try {
            await driveToPending(link, transport, phone);
            const bound = link.boundPeerId();

            await fixture.manager.closeAll();

            expect(terminations(fixture.context, HANDLE_CONNECTION)).toEqual(['closed']);
            expect(link.machine.state).toBe('idle');
            expect(transport.disconnected).toBe(true);
            const pending = (await fixture.trust.repository.peers()).find(
                (peer) => peer.installationId === bound,
            );
            expect(pending?.state).toBe('pending');
        } finally {
            phone.close();
        }
    });
});

describe('close after reply', () => {
    it('delivers a handler reply before closePeer terminates the session', async () => {
        const gate = deferred();
        const entered = deferred();
        const fixture = await createFixture({
            handler: composeHandlers(
                noopHandler(),
                removalHandler({
                    close: false,
                    gate: gate.promise,
                    onHandle: () => entered.resolve(),
                }),
            ),
        });
        const side = await openSession(fixture);
        const port = fixture.context.currentPort();

        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(RecordType.REMOVAL_REQUEST, 1n),
        );
        // The handler has the session's in-flight step and is blocked on the gate.
        await entered.promise;

        // closePeer must queue behind that step instead of terminating first.
        let closed = false;
        const closing = fixture.manager.closePeer(fixture.peerId).then(() => {
            closed = true;
        });
        await Promise.resolve();
        expect(closed).toBe(false);
        expect(disconnects(port, HANDLE_CONNECTION)).toHaveLength(0);
        expect(fixture.manager.authenticatedSessions()).toHaveLength(1);

        gate.resolve();
        const reply = await nextSendFor(port, HANDLE_CONNECTION);
        await ackSend(port, reply);
        await closing;

        expect(terminations(fixture.context, HANDLE_CONNECTION)).toEqual(['revoked']);
        // The sealed reply crossed the bridge before the terminal disconnect.
        const replyIndex = postedIndex(port, (message) => message.request_id === reply.requestId);
        const disconnectIndex = postedIndex(
            port,
            (message) =>
                message.type === 'disconnect' && message.connection_handle === HANDLE_CONNECTION,
        );
        expect(replyIndex).toBeGreaterThanOrEqual(0);
        expect(disconnectIndex).toBeGreaterThan(replyIndex);
    });

    it('sends the reply then closes when the handler asks for close after reply', async () => {
        const fixture = await createFixture({
            handler: composeHandlers(noopHandler(), removalHandler({ close: true })),
        });
        const side = await openSession(fixture);
        const port = fixture.context.currentPort();

        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(RecordType.REMOVAL_REQUEST, 1n),
        );
        const reply = await nextSendFor(port, HANDLE_CONNECTION);
        await ackSend(port, reply);
        // The terminal effects run after the acknowledged reply send.
        await nextDisconnectFor(port, HANDLE_CONNECTION);

        const opened = await side.openClientRecord(reply.bytes, 1n);
        expect(opened.type).toBe(RecordType.REMOVAL_RESPONSE);
        // Ruling R6: a removal record type has its own terminal stage, so the requester's owner
        // can tell a mutual removal from any other close-after-reply handler.
        expect(terminations(fixture.context, HANDLE_CONNECTION)).toEqual(['removal']);

        const replyIndex = postedIndex(port, (message) => message.request_id === reply.requestId);
        const disconnectIndex = postedIndex(
            port,
            (message) =>
                message.type === 'disconnect' && message.connection_handle === HANDLE_CONNECTION,
        );
        expect(disconnectIndex).toBeGreaterThan(replyIndex);
        // Nothing is accepted after the reply-then-close transition.
        expect(fixture.manager.authenticatedSessions()).toHaveLength(0);
    });

    it('composeHandlers forwards closeAfterReply from the child that handled the type', () => {
        const closing: ApplicationHandler = {
            handles: (type) => type === RecordType.REMOVAL_REQUEST,
            handle: async () => undefined,
            closeAfterReply: (type) => type === RecordType.REMOVAL_REQUEST,
        };
        const composed = composeHandlers(noopHandler(), closing);
        expect(composed).toBeDefined();
        expect(composed?.closeAfterReply?.(RecordType.REMOVAL_REQUEST)).toBe(true);
        expect(composed?.closeAfterReply?.(RecordType.REMOVAL_RESPONSE)).toBe(false);

        const single = composeHandlers(closing);
        expect(single?.closeAfterReply?.(RecordType.REMOVAL_REQUEST)).toBe(true);
    });
});
