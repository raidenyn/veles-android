import { describe, expect, it } from 'vitest';

import { SESSION_QUEUE_LIMIT, QueueOverflow, SerialQueue } from '../../src/session/queue';
import { SESSION_TICK_MS, SessionManager } from '../../src/connector/sessions';
import { decodeFrame } from '../../src/session/frames';
import type { ApplicationHandler } from '../../src/session/engine';
import { Direction, FrameType, RecordType } from '../../src/session/wire';
import type { TrustedPeer } from '../../src/trust/model';
import { HANDLE_CONNECTION, HANDLE_CONNECTION_2, HANDLE_DEVICE, HANDLE_DEVICE_2 } from './fixtures';
import {
    PhoneSide,
    ackDisconnect,
    ackSend,
    answerHandshake,
    connectForSession,
    createManagerFixture,
    decodeRecordHeader,
    emitRecord,
    flush,
    nextSend,
    postedSends,
} from './session-harness';
import {
    PEER_STATE_ACTIVE,
    PEER_STATE_BELOW_ACTIVE,
    TestPhone,
    patchSubtle,
    takeSubtle,
} from '../session/client-harness';

function deferred<T = void>(): { promise: Promise<T>; resolve: (value: T) => void } {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((settle) => {
        resolve = settle;
    });
    return { promise, resolve };
}

describe('SerialQueue', () => {
    it('uses the named per-session limit of 16', () => {
        expect(SESSION_QUEUE_LIMIT).toBe(16);
    });

    it('runs jobs strictly in submission order', async () => {
        const queue = new SerialQueue(4);
        const order: number[] = [];
        await Promise.all([
            queue.run(async () => {
                order.push(1);
            }),
            queue.run(async () => {
                order.push(2);
            }),
            queue.run(async () => {
                order.push(3);
            }),
        ]);
        expect(order).toEqual([1, 2, 3]);
        expect(queue.pending).toBe(0);
    });

    it('rejects past the limit with QueueOverflow without breaking the chain', async () => {
        const queue = new SerialQueue(2);
        const gate = deferred();
        const first = queue.run(async () => {
            await gate.promise;
        });
        const second = queue.run(async () => undefined);
        await expect(queue.run(async () => undefined)).rejects.toBeInstanceOf(QueueOverflow);
        gate.resolve();
        await Promise.all([first, second]);
        expect(queue.pending).toBe(0);
        await expect(queue.run(async () => undefined)).resolves.toBeUndefined();
    });
});

describe('serialized connector sessions', () => {
    it('adjacent handshake frames processed in order while verify is pending', async () => {
        const fixture = await createManagerFixture();
        await connectForSession(fixture);
        const port = fixture.currentPort();
        const opened = fixture.manager.open(HANDLE_CONNECTION, fixture.peerId);

        const hello = await nextSend(port);
        const side = new PhoneSide(fixture.phone);
        const phoneHello = await side.acceptClientHello(decodeFrame(hello.bytes).payload);
        const phoneSignature = await side.signatureFrame();
        await ackSend(port, hello);

        const gate = deferred();
        const original = takeSubtle('verify');
        const restore = patchSubtle('verify', async (...args: unknown[]) => {
            await gate.promise;
            return original(...args);
        });
        try {
            await emitRecord(port, HANDLE_CONNECTION, phoneHello);
            await emitRecord(port, HANDLE_CONNECTION, phoneSignature);
            await flush();
            // The signature verification is still pending, so nothing but the
            // hello has crossed the bridge.
            expect(postedSends(port)).toHaveLength(1);

            gate.resolve();
            await flush();
            const clientSignature = await nextSend(port);
            expect(clientSignature.bytes[0]).toBe(FrameType.RECONNECT_SIGNATURE);
            await side.acceptClientSignature(decodeFrame(clientSignature.bytes).payload);
            await ackSend(port, clientSignature);
            const clientConfirm = await nextSend(port);
            expect(clientConfirm.bytes[0]).not.toBe(FrameType.RECONNECT_SIGNATURE);
            await ackSend(port, clientConfirm);
            await emitRecord(port, HANDLE_CONNECTION, await side.confirmFrame());
            await flush();
            await expect(opened).resolves.toEqual({
                result: 'authenticated',
                peerId: fixture.peerId,
            });
        } finally {
            restore();
        }
    });

    it('simultaneous ack and heartbeat get consecutive sequences', async () => {
        const fixture = await createManagerFixture();
        await connectForSession(fixture);
        const opened = fixture.manager.open(HANDLE_CONNECTION, fixture.peerId);
        const side = await answerHandshake(fixture, { phone: fixture.phone });
        await expect(opened).resolves.toMatchObject({ result: 'authenticated' });

        const port = fixture.currentPort();
        const ack = fixture.manager.sendApplication(
            HANDLE_CONNECTION,
            RecordType.EVENT_ACK,
            new Uint8Array(32).fill(9),
        );
        const heartbeat = fixture.manager.sendApplication(
            HANDLE_CONNECTION,
            RecordType.APP_HEARTBEAT,
            new Uint8Array(0),
        );
        const postedAck = await nextSend(port);
        await ackSend(port, postedAck);
        const postedHeartbeat = await nextSend(port);
        await ackSend(port, postedHeartbeat);
        await Promise.all([ack, heartbeat]);

        const openedAck = await side.openClientRecord(postedAck.bytes, 1n);
        const openedHeartbeat = await side.openClientRecord(postedHeartbeat.bytes, 2n);
        expect(openedAck.type).toBe(RecordType.EVENT_ACK);
        expect(openedHeartbeat.type).toBe(RecordType.APP_HEARTBEAT);
        expect(openedAck.seq).toBe(1n);
        expect(openedHeartbeat.seq).toBe(2n);
    });

    it('port replacement while crypto pending discards result', async () => {
        const fixture = await createManagerFixture();
        await connectForSession(fixture);
        const port = fixture.currentPort();
        const opened = fixture.manager.open(HANDLE_CONNECTION, fixture.peerId);

        const hello = await nextSend(port);
        const side = new PhoneSide(fixture.phone);
        const phoneHello = await side.acceptClientHello(decodeFrame(hello.bytes).payload);
        const phoneSignature = await side.signatureFrame();
        await ackSend(port, hello);

        const gate = deferred();
        const original = takeSubtle('verify');
        const restore = patchSubtle('verify', async (...args: unknown[]) => {
            await gate.promise;
            return original(...args);
        });
        try {
            await emitRecord(port, HANDLE_CONNECTION, phoneHello);
            await emitRecord(port, HANDLE_CONNECTION, phoneSignature);
            await flush();
            expect(postedSends(port)).toHaveLength(1);

            // The port is replaced while signature verification is pending.
            await port.disconnect();
            await flush();
            await expect(opened).resolves.toEqual({ result: 'failed' });
            const postedBefore = port.posted.length;

            gate.resolve();
            await flush();
            expect(port.posted).toHaveLength(postedBefore);
            const peer = (await fixture.trust.peers()).find(
                (candidate) => candidate.installationId === fixture.peerId,
            );
            expect(peer?.lastSeenMillis).toBeNull();
            expect(peer?.platformHint).toBeUndefined();
        } finally {
            restore();
        }
    });

    it('queue overflow ends session with resource_exhausted', async () => {
        const fixture = await createManagerFixture();
        await connectForSession(fixture);
        const opened = fixture.manager.open(HANDLE_CONNECTION, fixture.peerId);
        const side = await answerHandshake(fixture, { phone: fixture.phone });
        await expect(opened).resolves.toMatchObject({ result: 'authenticated' });
        const port = fixture.currentPort();

        // The first send stays unacknowledged, so later jobs accumulate behind it.
        const pending: Promise<void>[] = [];
        pending.push(
            fixture.manager
                .sendApplication(
                    HANDLE_CONNECTION,
                    RecordType.EVENT_ACK,
                    new Uint8Array(32).fill(1),
                )
                .catch(() => undefined),
        );
        await nextSend(port);
        for (let index = 0; index < SESSION_QUEUE_LIMIT; index += 1) {
            pending.push(
                fixture.manager
                    .sendApplication(
                        HANDLE_CONNECTION,
                        RecordType.EVENT_ACK,
                        new Uint8Array(32).fill(2),
                    )
                    .catch(() => undefined),
            );
        }
        await flush();

        const entries = fixture.controller.state.diagnostics.entries();
        expect(
            entries.some(
                (entry) =>
                    entry.type === 'session_terminated' &&
                    entry.code === 'resource_exhausted' &&
                    entry.connection_handle === HANDLE_CONNECTION,
            ),
        ).toBe(true);
        expect(
            port.posted.some(
                (message) =>
                    (message as { type?: string }).type === 'disconnect' &&
                    (message as { connection_handle?: string }).connection_handle ===
                        HANDLE_CONNECTION,
            ),
        ).toBe(true);
        await expect(
            fixture.manager.sendApplication(
                HANDLE_CONNECTION,
                RecordType.EVENT_ACK,
                new Uint8Array(32).fill(3),
            ),
        ).rejects.toBeTruthy();
        expect(side.keys.sessionId.length).toBe(32);
    });

    it('one phone failing does not affect another', async () => {
        const impostorPhone = await TestPhone.create();
        const fixture = await createManagerFixture();
        await fixture.trust.addPeer(impostorPhone, { state: 'active' });
        const port = fixture.currentPort();

        // The healthy phone authenticates first and stays open throughout.
        await connectForSession(fixture, HANDLE_CONNECTION_2, HANDLE_DEVICE_2);
        const healthy = fixture.manager.open(HANDLE_CONNECTION_2, fixture.peerId);
        const side = await answerHandshake(fixture, {
            phone: fixture.phone,
            connectionHandle: HANDLE_CONNECTION_2,
        });
        await expect(healthy).resolves.toEqual({
            result: 'authenticated',
            peerId: fixture.peerId,
        });

        // A second link fails with the same generic pre-auth failure while the
        // first session is open and untouched. The client sends no frame: the
        // phone-only `preauth_reject` is not a client record (OTP2-ENC-010).
        await connectForSession(fixture, HANDLE_CONNECTION, HANDLE_DEVICE);
        const failed = fixture.manager.open(HANDLE_CONNECTION, fixture.peerId);
        const failedHello = await nextSend(port);
        await ackSend(port, failedHello);
        const impostor = new PhoneSide(impostorPhone);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await impostor.acceptClientHello(decodeFrame(failedHello.bytes).payload),
        );
        await flush();
        await expect(failed).resolves.toEqual({ result: 'failed' });
        await ackDisconnect(port, HANDLE_CONNECTION);

        const terminations = fixture.controller.state.diagnostics
            .entries()
            .filter((entry) => entry.type === 'session_terminated');
        expect(terminations).toHaveLength(1);
        expect(terminations[0]?.connection_handle).toBe(HANDLE_CONNECTION);

        // The already-open session still seals and sends application records.
        const send = fixture.manager.sendApplication(
            HANDLE_CONNECTION_2,
            RecordType.EVENT_ACK,
            new Uint8Array(32).fill(7),
        );
        const posted = await nextSend(port);
        await ackSend(port, posted);
        await send;
        const record = await side.openClientRecord(posted.bytes, 1n);
        expect(record.type).toBe(RecordType.EVENT_ACK);
    });

    it('metadata unchanged on failure and on stale completion', async () => {
        const failing = await createManagerFixture({ hint: 'winrt:phone-a' });
        await connectForSession(failing);
        const opened = failing.manager.open(HANDLE_CONNECTION, failing.peerId);
        const hello = await nextSend(failing.currentPort());
        await ackSend(failing.currentPort(), hello);
        const other = await TestPhone.create();
        const impostor = new PhoneSide(other);
        await emitRecord(
            failing.currentPort(),
            HANDLE_CONNECTION,
            await impostor.acceptClientHello(decodeFrame(hello.bytes).payload),
        );
        await flush();
        await expect(opened).resolves.toEqual({ result: 'failed' });
        await ackDisconnect(failing.currentPort(), HANDLE_CONNECTION);
        const afterFailure = (await failing.trust.peers())[0];
        expect(afterFailure?.lastSeenMillis).toBeNull();
        expect(afterFailure?.platformHint).toBe('winrt:phone-a');

        const stale = await createManagerFixture({ hint: 'winrt:phone-a' });
        await connectForSession(stale);
        const staleOpened = stale.manager.open(HANDLE_CONNECTION, stale.peerId);
        const staleHello = await nextSend(stale.currentPort());
        const side = new PhoneSide(stale.phone);
        const phoneHello = await side.acceptClientHello(decodeFrame(staleHello.bytes).payload);
        const phoneSignature = await side.signatureFrame();
        await ackSend(stale.currentPort(), staleHello);
        const gate = deferred();
        const original = takeSubtle('verify');
        const restore = patchSubtle('verify', async (...args: unknown[]) => {
            await gate.promise;
            return original(...args);
        });
        try {
            await emitRecord(stale.currentPort(), HANDLE_CONNECTION, phoneHello);
            await emitRecord(stale.currentPort(), HANDLE_CONNECTION, phoneSignature);
            await flush();
            await stale.currentPort().disconnect();
            await flush();
            await expect(staleOpened).resolves.toEqual({ result: 'failed' });
            gate.resolve();
            await flush();
            const afterStale = (await stale.trust.peers())[0];
            expect(afterStale?.lastSeenMillis).toBeNull();
            expect(afterStale?.platformHint).toBe('winrt:phone-a');
        } finally {
            restore();
        }
    });

    it('bridge send payloads are only frames or sealed records', async () => {
        const fixture = await createManagerFixture();
        await connectForSession(fixture);
        const opened = fixture.manager.open(HANDLE_CONNECTION, fixture.peerId);
        const side = await answerHandshake(fixture, {
            phone: fixture.phone,
            peerState: PEER_STATE_ACTIVE,
        });
        await expect(opened).resolves.toMatchObject({ result: 'authenticated' });

        const ack = fixture.manager.sendApplication(
            HANDLE_CONNECTION,
            RecordType.EVENT_ACK,
            new Uint8Array(32).fill(0x0a),
        );
        const postedAck = await nextSend(fixture.currentPort());
        await ackSend(fixture.currentPort(), postedAck);
        await ack;

        const sends = postedSends(fixture.currentPort());
        expect(sends.length).toBeGreaterThanOrEqual(3);
        let sealed = 0;
        for (const send of sends) {
            const first = send.bytes[0];
            if (first === FrameType.RECONNECT_HELLO || first === FrameType.RECONNECT_SIGNATURE) {
                expect(decodeFrame(send.bytes).type).toBe(first);
                continue;
            }
            const header = decodeRecordHeader(send.bytes);
            expect(header).not.toBeNull();
            expect(header?.direction).toBe(Direction.CLIENT_TO_PHONE);
            expect(header?.prefix).toEqual(side.prefix);
            const record = await side.openClientRecord(send.bytes, header?.sequence ?? 0n);
            expect([RecordType.RECONNECT_CONFIRM, RecordType.EVENT_ACK]).toContain(record.type);
            if (record.type === RecordType.EVENT_ACK) sealed += 1;
        }
        // The confirm at sequence 0 and the application ack are the only sealed
        // client records; every other bridge payload was a pre-auth frame.
        expect(sealed).toBe(1);
    });
});

describe('intended peer selection', () => {
    const peer = (installationId: string, state: TrustedPeer['state']): TrustedPeer =>
        ({ installationId, state }) as TrustedPeer;

    it('uses an explicit id without consulting the candidates', () => {
        expect(SessionManager.intendedPeer([], 'explicit-id')).toBe('explicit-id');
        expect(
            SessionManager.intendedPeer([peer('a', 'active'), peer('b', 'active')], 'explicit-id'),
        ).toBe('explicit-id');
    });

    it('picks the single active-family peer by installation id', () => {
        expect(SessionManager.intendedPeer([peer('a', 'active')])).toBe('a');
        expect(SessionManager.intendedPeer([peer('b', 'active_activation_pending')])).toBe('b');
    });

    it('refuses zero candidates', () => {
        expect(SessionManager.intendedPeer([])).toBeUndefined();
        expect(SessionManager.intendedPeer([peer('p', 'pending')])).toBeUndefined();
    });

    it('refuses several candidates', () => {
        expect(
            SessionManager.intendedPeer([peer('a', 'active'), peer('b', 'active')]),
        ).toBeUndefined();
    });
});

describe('session deadlines, pipeline and verifier negatives', () => {
    it('client confirm completion frees adjacent inbound records in order', async () => {
        const delivered: number[] = [];
        const handler = {
            handles: (type: number) => type === RecordType.PULL_EVENTS,
            handle: async (type: number): Promise<void> => {
                delivered.push(type);
            },
        };
        const fixture = await createManagerFixture({ handler });
        await connectForSession(fixture);
        const port = fixture.currentPort();
        const opened = fixture.manager.open(HANDLE_CONNECTION, fixture.peerId);
        const hello = await nextSend(port);
        await ackSend(port, hello);
        const side = new PhoneSide(fixture.phone);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.acceptClientHello(decodeFrame(hello.bytes).payload),
        );
        await emitRecord(port, HANDLE_CONNECTION, await side.signatureFrame());
        await flush();
        const signature = await nextSend(port);
        await side.acceptClientSignature(decodeFrame(signature.bytes).payload);
        await ackSend(port, signature);
        const clientConfirm = await nextSend(port);

        // The phone confirm and its first application record are both buffered
        // behind the still-pending local confirm send.
        await emitRecord(port, HANDLE_CONNECTION, await side.confirmFrame());
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(RecordType.PULL_EVENTS, 1n, Uint8Array.of(1)),
        );
        await flush();
        expect(delivered).toEqual([]);

        await ackSend(port, clientConfirm);
        await flush();
        await expect(opened).resolves.toEqual({
            result: 'authenticated',
            peerId: fixture.peerId,
        });
        expect(delivered).toEqual([RecordType.PULL_EVENTS]);
        expect(
            fixture.controller.state.diagnostics
                .entries()
                .some((entry) => entry.type === 'session_terminated'),
        ).toBe(false);
    });

    it('sends the owed activation ack only after authenticated', async () => {
        const ackBody = new Uint8Array(32).fill(0x33);
        const handler: ApplicationHandler = {
            handles: () => false,
            handle: async () => undefined,
            unprompted: () => [{ type: RecordType.ACTIVATION_ACK, body: ackBody }],
        };
        const fixture = await createManagerFixture({ state: 'active', handler });
        const events: string[] = [];
        const controller = fixture.controller as unknown as {
            send: typeof fixture.controller.send;
        };
        const originalSend = fixture.controller.send.bind(fixture.controller);
        controller.send = (handle, record) => {
            const type = record.length >= 5 ? (record[3] << 8) | record[4] : -1;
            events.push(`send:${type}`);
            return originalSend(handle, record);
        };
        const repository = fixture.trust.repository;
        const originalAuthenticated = repository.authenticated.bind(repository);
        repository.authenticated = async (...args: Parameters<typeof originalAuthenticated>) => {
            events.push('authenticated');
            return originalAuthenticated(...args);
        };

        await connectForSession(fixture);
        const opened = fixture.manager.open(HANDLE_CONNECTION, fixture.peerId);
        await answerHandshake(fixture, {
            phone: fixture.phone,
            peerState: PEER_STATE_BELOW_ACTIVE,
        });
        await expect(opened).resolves.toEqual({
            result: 'authenticated',
            peerId: fixture.peerId,
        });
        await flush(100);

        const mark = events.indexOf('authenticated');
        const ack = events.indexOf(`send:${RecordType.ACTIVATION_ACK}`);
        expect(mark).toBeGreaterThanOrEqual(0);
        expect(ack).toBeGreaterThanOrEqual(0);
        expect(mark, 'the ack is sent only after the metadata write').toBeLessThan(ack);
    });

    it('coarse tick closes a silent handshake and an idle session', async () => {
        const fixture = await createManagerFixture();
        await connectForSession(fixture);
        const port = fixture.currentPort();
        const opened = fixture.manager.open(HANDLE_CONNECTION, fixture.peerId);
        const hello = await nextSend(port);
        await ackSend(port, hello);
        let settled = false;
        void opened.then(() => {
            settled = true;
        });

        // A coarse tick before the 30 s deadline must not expire the handshake
        // early: throttling may close a session late, never early.
        fixture.clock.advance(SESSION_TICK_MS);
        await flush();
        expect(settled).toBe(false);

        fixture.clock.advance(SESSION_TICK_MS);
        await flush();
        await expect(opened).resolves.toEqual({ result: 'failed' });
        expect(
            fixture.controller.state.diagnostics
                .entries()
                .some((entry) => entry.code === 'handshake_timeout'),
        ).toBe(true);
        await ackDisconnect(port, HANDLE_CONNECTION);

        // An open session with no traffic is closed by the idle deadline.
        await connectForSession(fixture, HANDLE_CONNECTION_2, HANDLE_DEVICE_2);
        const idle = fixture.manager.open(HANDLE_CONNECTION_2, fixture.peerId);
        await answerHandshake(fixture, {
            phone: fixture.phone,
            connectionHandle: HANDLE_CONNECTION_2,
        });
        await expect(idle).resolves.toEqual({
            result: 'authenticated',
            peerId: fixture.peerId,
        });
        fixture.clock.advance(180_000);
        await flush();
        expect(
            fixture.controller.state.diagnostics
                .entries()
                .some(
                    (entry) =>
                        entry.code === 'idle' && entry.connection_handle === HANDLE_CONNECTION_2,
                ),
        ).toBe(true);
        await expect(
            fixture.manager.sendApplication(
                HANDLE_CONNECTION_2,
                RecordType.EVENT_ACK,
                new Uint8Array(32).fill(1),
            ),
        ).rejects.toBeTruthy();
    });

    it('phone_active and authenticated refuse unverified, forged and stale proof', async () => {
        const fixture = await createManagerFixture({ state: 'active_activation_pending' });
        const manager = fixture.manager;
        const gate = deferred();
        let finalizing = false;
        fixture.trust.bindVerifier(async (peer, stage, proof) => {
            const accepted = await manager.verifier(peer, stage, proof);
            if (accepted && stage === 'phone_active') {
                // Hold the engine inside its verified finalization window so the
                // negative checks below run exactly there.
                finalizing = true;
                await gate.promise;
            }
            return accepted;
        });
        await connectForSession(fixture);
        const port = fixture.currentPort();
        const repository = fixture.trust.repository;
        const opened = fixture.manager.open(HANDLE_CONNECTION, fixture.peerId);
        const hello = await nextSend(port);
        await ackSend(port, hello);
        const side = new PhoneSide(fixture.phone);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.acceptClientHello(decodeFrame(hello.bytes).payload),
        );
        await emitRecord(port, HANDLE_CONNECTION, await side.signatureFrame());
        await flush();
        const signature = await nextSend(port);
        await side.acceptClientSignature(decodeFrame(signature.bytes).payload);
        await ackSend(port, signature);
        const clientConfirm = await nextSend(port);
        await ackSend(port, clientConfirm);

        // Nothing has verified a phone confirm yet.
        const peer = { installationId: fixture.peerId } as TrustedPeer;
        await expect(
            repository.confirm(fixture.peerId, 'phone_active', Uint8Array.of(1, 2, 3)),
        ).rejects.toBeTruthy();
        expect(await manager.verifier(peer, 'phone_active', Uint8Array.of(1))).toBe(false);
        expect(await manager.verifier(peer, 'authenticated', Uint8Array.of(1))).toBe(false);

        const verifiedConfirm = await side.confirmFrame();
        await emitRecord(port, HANDLE_CONNECTION, verifiedConfirm);
        await flush();
        expect(finalizing).toBe(true);
        // Inside the verified finalization window a forged byte string is not a
        // proof, while the immutable verified confirm is.
        expect(await manager.verifier(peer, 'phone_active', Uint8Array.of(4))).toBe(false);
        expect(await manager.verifier(peer, 'phone_active', verifiedConfirm)).toBe(true);
        // The gate is not open yet, so even the real confirm is not an
        // authenticated-stage proof.
        expect(await manager.verifier(peer, 'authenticated', verifiedConfirm)).toBe(false);

        gate.resolve();
        await flush();
        await expect(opened).resolves.toEqual({
            result: 'authenticated',
            peerId: fixture.peerId,
        });
        expect((await fixture.trust.peers())[0]?.state).toBe('active');
        const stored = (await fixture.trust.peers())[0];
        expect(stored?.lastSeenMillis).toBe(10_000);
        expect(stored?.platformHint).toBeUndefined();

        // Once the class is fixed, phone_active is no longer finalizing.
        expect(await manager.verifier(peer, 'phone_active', verifiedConfirm)).toBe(false);
        // Bytes that never verified this session are never a proof; the same
        // verified confirm is the open session's authenticated proof.
        await expect(
            repository.authenticated(fixture.peerId, 'winrt:leak', Uint8Array.of(5)),
        ).rejects.toBeTruthy();
        expect(await manager.verifier(peer, 'authenticated', verifiedConfirm)).toBe(true);

        // Teardown erases the verified confirm: a closed generation proves nothing.
        await port.disconnect();
        await flush();
        expect(await manager.verifier(peer, 'phone_active', verifiedConfirm)).toBe(false);
        expect(await manager.verifier(peer, 'authenticated', verifiedConfirm)).toBe(false);
        await expect(
            repository.authenticated(fixture.peerId, 'winrt:leak', verifiedConfirm),
        ).rejects.toBeTruthy();
    });

    it('phone_active requires peer_state 0x01 while the local confirm is still pending', async () => {
        const fixture = await createManagerFixture({ state: 'active_activation_pending' });
        const manager = fixture.manager;
        const repository = fixture.trust.repository;
        await connectForSession(fixture);
        const port = fixture.currentPort();
        const opened = manager.open(HANDLE_CONNECTION, fixture.peerId);
        const hello = await nextSend(port);
        await ackSend(port, hello);
        const side = new PhoneSide(fixture.phone);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.acceptClientHello(decodeFrame(hello.bytes).payload),
        );
        await flush();
        await emitRecord(port, HANDLE_CONNECTION, await side.signatureFrame());
        await flush();
        const signature = await nextSend(port);
        await side.acceptClientSignature(decodeFrame(signature.bytes).payload);
        await ackSend(port, signature);
        const clientConfirm = await nextSend(port);
        // Deliberately leave the client confirm send unacknowledged: the engine remains
        // inside its verified-finalization window while the phone confirm arrives.
        const belowActiveConfirm = await side.confirmFrame(PEER_STATE_BELOW_ACTIVE);
        await emitRecord(port, HANDLE_CONNECTION, belowActiveConfirm);
        await flush();

        const peer = { installationId: fixture.peerId } as TrustedPeer;
        // The confirm verified, and the class is not fixed yet, but a 0x02 proof may not
        // upgrade the durable mirror (OTP2-SID-001).
        expect(await manager.verifier(peer, 'phone_active', belowActiveConfirm)).toBe(false);
        await expect(
            repository.confirm(fixture.peerId, 'phone_active', belowActiveConfirm),
        ).rejects.toBeTruthy();
        expect((await fixture.trust.peers())[0]?.state).toBe('active_activation_pending');

        // The local completion settles the handshake as `full_pending_peer`, without an upgrade.
        await ackSend(port, clientConfirm);
        await flush();
        await expect(opened).resolves.toEqual({
            result: 'authenticated',
            peerId: fixture.peerId,
        });
        expect((await fixture.trust.peers())[0]?.state).toBe('active_activation_pending');
        // The same proof now satisfies the authenticated stage of the open session.
        expect(await manager.verifier(peer, 'authenticated', belowActiveConfirm)).toBe(true);
    });

    it('disconnection during post-verifier validation does not persist metadata', async () => {
        const fixture = await createManagerFixture({ hint: 'winrt:phone-a' });
        const gate = deferred();
        const manager = fixture.manager;
        fixture.trust.bindVerifier(async (peer, stage, proof) => {
            const accepted = await manager.verifier(peer, stage, proof);
            // Pause after the production verifier accepted, inside the
            // repository's update, before its final validation and write.
            if (accepted && stage === 'authenticated') await gate.promise;
            return accepted;
        });
        await connectForSession(fixture);
        const port = fixture.currentPort();
        const opened = fixture.manager.open(HANDLE_CONNECTION, fixture.peerId);
        const hello = await nextSend(port);
        await ackSend(port, hello);
        const side = new PhoneSide(fixture.phone);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.acceptClientHello(decodeFrame(hello.bytes).payload),
        );
        await emitRecord(port, HANDLE_CONNECTION, await side.signatureFrame());
        await flush();
        const signature = await nextSend(port);
        await side.acceptClientSignature(decodeFrame(signature.bytes).payload);
        await ackSend(port, signature);
        const clientConfirm = await nextSend(port);
        await ackSend(port, clientConfirm);
        await emitRecord(port, HANDLE_CONNECTION, await side.confirmFrame());
        await flush();

        // The metadata write is paused after the verifier accepted; replacing
        // the port must prevent the durable write at the commit boundary.
        await port.disconnect();
        await flush();
        await expect(opened).resolves.toEqual({ result: 'failed' });
        gate.resolve();
        await flush();
        const peer = (await fixture.trust.peers())[0];
        expect(peer?.lastSeenMillis).toBeNull();
        expect(peer?.platformHint).toBe('winrt:phone-a');
    });
});
