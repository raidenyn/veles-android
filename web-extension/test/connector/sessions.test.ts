import { describe, expect, it } from 'vitest';

import { SESSION_QUEUE_LIMIT, QueueOverflow, SerialQueue } from '../../src/session/queue';
import { SessionManager } from '../../src/connector/sessions';
import { decodeFrame } from '../../src/session/frames';
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
import { PEER_STATE_ACTIVE, TestPhone, patchSubtle, takeSubtle } from '../session/client-harness';

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
            Uint8Array.of(9),
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
                .sendApplication(HANDLE_CONNECTION, RecordType.EVENT_ACK, Uint8Array.of(1))
                .catch(() => undefined),
        );
        await nextSend(port);
        for (let index = 0; index < SESSION_QUEUE_LIMIT; index += 1) {
            pending.push(
                fixture.manager
                    .sendApplication(HANDLE_CONNECTION, RecordType.EVENT_ACK, Uint8Array.of(2))
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
                Uint8Array.of(3),
            ),
        ).rejects.toBeTruthy();
        expect(side.keys.sessionId.length).toBe(32);
    });

    it('one phone failing does not affect another', async () => {
        const secondPhone = await TestPhone.create();
        const fixture = await createManagerFixture();
        await fixture.trust.addPeer(secondPhone, { state: 'active' });
        const port = fixture.currentPort();

        await connectForSession(fixture, HANDLE_CONNECTION, HANDLE_DEVICE);
        const failed = fixture.manager.open(HANDLE_CONNECTION, fixture.peerId);
        const failedHello = await nextSend(port);
        await ackSend(port, failedHello);
        // A phone hello naming another installation id is the generic failure.
        const impostor = new PhoneSide(secondPhone);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await impostor.acceptClientHello(decodeFrame(failedHello.bytes).payload),
        );
        await flush();
        const reject = await nextSend(port);
        expect(reject.bytes[0]).toBe(FrameType.PREAUTH_REJECT);
        await ackSend(port, reject);
        await expect(failed).resolves.toEqual({ result: 'failed' });
        await ackDisconnect(port, HANDLE_CONNECTION);
        expect((await fixture.trust.peers()).length).toBe(2);

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

        const send = fixture.manager.sendApplication(
            HANDLE_CONNECTION_2,
            RecordType.EVENT_ACK,
            Uint8Array.of(7),
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
        const reject = await nextSend(failing.currentPort());
        await ackSend(failing.currentPort(), reject);
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
            Uint8Array.of(1, 2, 3),
        );
        const postedAck = await nextSend(fixture.currentPort());
        await ackSend(fixture.currentPort(), postedAck);
        await ack;

        const sends = postedSends(fixture.currentPort());
        expect(sends.length).toBeGreaterThanOrEqual(3);
        let sealed = 0;
        for (const send of sends) {
            const first = send.bytes[0];
            if (
                first === FrameType.RECONNECT_HELLO ||
                first === FrameType.RECONNECT_SIGNATURE ||
                first === FrameType.PREAUTH_REJECT
            ) {
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
