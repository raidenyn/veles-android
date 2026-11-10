import { describe, expect, it } from 'vitest';
import { normalizeLowS } from '../../src/session/ecdsa';
import { SessionReject } from '../../src/session/reject';
import { rbase as reconnectRbase } from '../../src/session/schedule';
import type { ApplicationHandler } from '../../src/session/engine';
import { AppErrorCode, Direction, RecordType } from '../../src/session/wire';
import {
    PEER_STATE_ACTIVE,
    PhoneHarness,
    appErrorBody,
    sendBytes,
    terminatedReason,
} from './client-harness';

const EMPTY = new Uint8Array(0);

function emptyHandler(): ApplicationHandler {
    return {
        handles: () => false,
        handle: async () => Promise.resolve(),
    };
}

class ThrowingHandler implements ApplicationHandler {
    handles(): boolean {
        return true;
    }

    async handle(): Promise<void> {
        throw new Error('handler down');
    }
}

class ErrorThenHeartbeatHandler implements ApplicationHandler {
    handles(type: number): boolean {
        return type === RecordType.PULL_EVENTS;
    }

    async handle(
        _type: number,
        _body: Uint8Array,
        reply: (type: number, body: Uint8Array) => Promise<void>,
    ): Promise<void> {
        await reply(RecordType.APP_ERROR, appErrorBody(AppErrorCode.PROTOCOL_VIOLATION));
        await reply(RecordType.APP_HEARTBEAT, EMPTY);
        await reply(RecordType.PULL_REQUEST, EMPTY);
    }
}

describe('client protected records and timing', () => {
    it('replay duplicate terminates', async () => {
        const harness = await PhoneHarness.create();
        await harness.openSession();
        const heartbeat = await harness.phoneRecord(RecordType.APP_HEARTBEAT, 1n);

        expect(await harness.engine.onInbound(heartbeat)).toEqual([]);
        expect(harness.engine.isOpen).toBe(true);
        expect(terminatedReason(await harness.engine.onInbound(heartbeat))).toBe('record_invalid');
        expect(harness.engine.isOpen).toBe(false);
    });

    it('reorder terminates', async () => {
        const harness = await PhoneHarness.create();
        await harness.openSession();

        expect(
            terminatedReason(
                await harness.engine.onInbound(
                    await harness.phoneRecord(RecordType.APP_HEARTBEAT, 2n),
                ),
            ),
        ).toBe('record_invalid');
        expect(harness.engine.isOpen).toBe(false);
    });

    it('skip terminates', async () => {
        const harness = await PhoneHarness.create();
        await harness.openSession();

        expect(
            await harness.engine.onInbound(await harness.phoneRecord(RecordType.APP_HEARTBEAT, 1n)),
        ).toEqual([]);
        expect(
            terminatedReason(
                await harness.engine.onInbound(
                    await harness.phoneRecord(RecordType.APP_HEARTBEAT, 3n),
                ),
            ),
        ).toBe('record_invalid');
    });

    it('prior session record rejected', async () => {
        const first = await PhoneHarness.create();
        await first.openSession();
        const captured = await first.phoneRecord(RecordType.APP_HEARTBEAT, 1n);

        const second = await PhoneHarness.create({
            phoneIdentity: first.phone.identity,
            phoneId: first.peerId,
        });
        await second.openSession();

        expect(terminatedReason(await second.engine.onInbound(captured))).toBe('record_invalid');
        expect(second.engine.isOpen).toBe(false);
    });

    it('sequence max accepted once then terminates', async () => {
        const inbound = await PhoneHarness.create();
        await inbound.openSession();
        inbound.engine._seekSequences(0xffffffffffffffffn, 1n);

        const atMax = await inbound.phoneRecord(RecordType.APP_HEARTBEAT, 0xffffffffffffffffn);
        expect(await inbound.engine.onInbound(atMax)).toEqual([]);
        expect(inbound.engine.isOpen).toBe(true);
        expect(terminatedReason(await inbound.engine.onInbound(atMax))).toBe('sequence');
        expect(inbound.engine.isOpen).toBe(false);

        const outbound = await PhoneHarness.create();
        await outbound.openSession();
        outbound.engine._seekSequences(1n, 0xffffffffffffffffn);

        const effects = await outbound.engine.sendApplication(RecordType.PULL_REQUEST, EMPTY);
        const sent = await outbound.openClientApplication(sendBytes(effects), 0xffffffffffffffffn);
        expect(sent.type).toBe(RecordType.PULL_REQUEST);
        expect(terminatedReason(await outbound.engine.onTick())).toBe('sequence');
        expect(outbound.engine.isOpen).toBe(false);
    });

    it('unknown type 0000 and 0201 terminate', async () => {
        for (const type of [0x0000, 0x0201]) {
            const harness = await PhoneHarness.create();
            await harness.openSession();

            expect(
                terminatedReason(
                    await harness.engine.onInbound(await harness.phoneUncheckedRecord(type, 1n)),
                ),
            ).toBe('record_invalid');
            expect(harness.engine.isOpen).toBe(false);
        }
    });

    it('wrong direction terminates', async () => {
        const harness = await PhoneHarness.create();
        await harness.openSession();

        const wrongDirection = await harness.phoneRecord(
            RecordType.PULL_REQUEST,
            1n,
            EMPTY,
            Direction.CLIENT_TO_PHONE,
        );
        expect(terminatedReason(await harness.engine.onInbound(wrongDirection))).toBe(
            'record_invalid',
        );
        expect(harness.engine.isOpen).toBe(false);
    });

    it('push in activation recovery terminates with no delivery', async () => {
        const harness = await PhoneHarness.create({ state: 'pending' });
        await harness.openSession({ peerState: PEER_STATE_ACTIVE });

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.PUSH_EVENT, 1n),
        );

        expect(terminatedReason(effects)).toBe('authorization');
        expect(effects.some((effect) => effect.kind === 'deliver')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
    });

    it('unhandled authorized record sends protocol violation', async () => {
        const harness = await PhoneHarness.create({ handler: emptyHandler() });
        await harness.openSession();

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.PULL_EVENTS, 1n),
        );

        expect(effects).toHaveLength(3);
        const error = await harness.openClientApplication(sendBytes(effects), 1n);
        expect(error.type).toBe(RecordType.APP_ERROR);
        expect(error.body).toEqual(appErrorBody(AppErrorCode.PROTOCOL_VIOLATION));
        expect(terminatedReason(effects.slice(1))).toBe('unhandled');
        expect(effects.some((effect) => effect.kind === 'deliver')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
    });

    it('unhandled activation record fails closed', async () => {
        const harness = await PhoneHarness.create({ state: 'pending' });
        await harness.openSession({ peerState: PEER_STATE_ACTIVE });

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.ACTIVATION_COMPLETE, 1n, new Uint8Array(32)),
        );

        expect(effects).toHaveLength(3);
        const error = await harness.openClientApplication(sendBytes(effects), 1n);
        expect(error.type).toBe(RecordType.APP_ERROR);
        expect(error.body).toEqual(appErrorBody(AppErrorCode.PROTOCOL_VIOLATION));
        expect(terminatedReason(effects.slice(1))).toBe('unhandled');
        expect(harness.engine.isOpen).toBe(false);
    });

    it('app error received is terminal', async () => {
        const harness = await PhoneHarness.create();
        await harness.openSession();

        const error = await harness.phoneRecord(
            RecordType.APP_ERROR,
            1n,
            appErrorBody(AppErrorCode.SESSION_EXPIRED),
        );
        expect(terminatedReason(await harness.engine.onInbound(error))).toBe('app_error');
        expect(harness.engine.isOpen).toBe(false);
        expect(
            await harness.engine.onInbound(await harness.phoneRecord(RecordType.APP_HEARTBEAT, 2n)),
        ).toEqual([]);
    });

    it('unknown app error code terminates', async () => {
        const harness = await PhoneHarness.create();
        await harness.openSession();

        const unknownCode = Uint8Array.of(0x09, 0x00, 0x00);
        expect(
            terminatedReason(
                await harness.engine.onInbound(
                    await harness.phoneRecord(RecordType.APP_ERROR, 1n, unknownCode),
                ),
            ),
        ).toBe('record_invalid');
        expect(harness.engine.isOpen).toBe(false);
    });

    it('input after teardown ignored', async () => {
        const harness = await PhoneHarness.create();
        await harness.openSession();

        harness.engine.teardown();
        expect(harness.engine.isOpen).toBe(false);
        expect(
            await harness.engine.onInbound(await harness.phoneRecord(RecordType.APP_HEARTBEAT, 1n)),
        ).toEqual([]);
        expect(await harness.engine.onTick()).toEqual([]);
        expect(await harness.engine.onSendComplete(0, true)).toEqual([]);
        await expect(harness.engine.start()).resolves.toEqual([]);
        await expect(
            harness.engine.sendApplication(RecordType.PULL_REQUEST, EMPTY),
        ).rejects.toBeInstanceOf(SessionReject);
    });

    it('repeated hello on same generation rejects', async () => {
        const harness = await PhoneHarness.create();
        await harness.start();
        const phoneHello = harness.phone.helloPayload();
        expect(await harness.deliverPhoneHello(phoneHello)).toEqual([]);

        const repeated = await harness.deliverPhoneHello(phoneHello);
        expect(sendBytes(repeated)).toEqual(Uint8Array.of(0x0f, 0x00, 0x00));
        expect(repeated[1]).toEqual({ kind: 'close' });
        expect(harness.engine.isOpen).toBe(false);

        const open = await PhoneHarness.create();
        await open.openSession();
        const onOpen = await open.deliverPhoneHello(open.phone.helloPayload());
        expect(sendBytes(onOpen)).toEqual(Uint8Array.of(0x0f, 0x00, 0x00));
        expect(onOpen[1]).toEqual({ kind: 'close' });
    });

    it('record deleted mid handshake yields no protected data', async () => {
        const harness = await PhoneHarness.create({ lookup: async () => undefined });
        await harness.start();

        const effects = await harness.deliverPhoneHello(harness.phone.helloPayload());

        expect(effects).toHaveLength(2);
        expect(sendBytes(effects)).toEqual(Uint8Array.of(0x0f, 0x00, 0x00));
        expect(effects[1]).toEqual({ kind: 'close' });
        expect(harness.calls.sign).toBe(0);
        expect(harness.engine.isOpen).toBe(false);
    });

    it('throwing lookup fails closed', async () => {
        const harness = await PhoneHarness.create({
            lookup: async () => Promise.reject(new Error('trust storage unavailable')),
        });
        await harness.start();

        const effects = await harness.deliverPhoneHello(harness.phone.helloPayload());

        expect(terminatedReason(effects)).toBe('dependency');
        expect(effects.some((effect) => effect.kind === 'send')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
    });

    it('throwing signer fails closed', async () => {
        const harness = await PhoneHarness.create({
            sign: async () => Promise.reject(new Error('keystore unavailable')),
        });
        const started = await harness.start();
        const phoneHello = harness.phone.helloPayload();
        expect(await harness.deliverPhoneHello(phoneHello)).toEqual([]);
        const transcript = reconnectRbase(phoneHello, started.clientHelloPayload);

        const effects = await harness.deliverPhoneSignature(transcript);

        expect(terminatedReason(effects)).toBe('dependency');
        expect(effects.some((effect) => effect.kind === 'send')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
    });

    it('throwing handler fails closed', async () => {
        const harness = await PhoneHarness.create({ handler: new ThrowingHandler() });
        await harness.openSession();

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.PULL_EVENTS, 1n),
        );

        expect(terminatedReason(effects)).toBe('dependency');
        expect(effects.some((effect) => effect.kind === 'send')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
    });

    it('application error send is terminal', async () => {
        const harness = await PhoneHarness.create();
        await harness.openSession();

        const effects = await harness.engine.sendApplication(
            RecordType.APP_ERROR,
            appErrorBody(AppErrorCode.SESSION_EXPIRED),
        );

        expect(effects).toHaveLength(3);
        const error = await harness.openClientApplication(sendBytes(effects), 1n);
        expect(error.type).toBe(RecordType.APP_ERROR);
        expect(error.body).toEqual(appErrorBody(AppErrorCode.SESSION_EXPIRED));
        expect(terminatedReason(effects.slice(1))).toBe('app_error');
        expect(harness.engine.isOpen).toBe(false);
        expect(
            await harness.engine.onInbound(await harness.phoneRecord(RecordType.APP_HEARTBEAT, 1n)),
        ).toEqual([]);
    });

    it('handler error reply is terminal and stops further replies', async () => {
        const harness = await PhoneHarness.create({ handler: new ErrorThenHeartbeatHandler() });
        await harness.openSession();

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.PULL_EVENTS, 1n),
        );

        expect(effects).toHaveLength(3);
        const error = await harness.openClientApplication(sendBytes(effects), 1n);
        expect(error.type).toBe(RecordType.APP_ERROR);
        expect(error.body).toEqual(appErrorBody(AppErrorCode.PROTOCOL_VIOLATION));
        expect(terminatedReason(effects.slice(1))).toBe('app_error');
        expect(effects.some((effect) => effect.kind === 'deliver')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
    });

    it('early heartbeat ignored', async () => {
        const harness = await PhoneHarness.create();
        await harness.openSession();

        expect(
            await harness.engine.onInbound(await harness.phoneRecord(RecordType.APP_HEARTBEAT, 1n)),
        ).toEqual([]);
        harness.clock.advance(10_000);
        expect(
            await harness.engine.onInbound(await harness.phoneRecord(RecordType.APP_HEARTBEAT, 2n)),
        ).toEqual([]);
        expect(harness.engine.isOpen).toBe(true);
    });

    it('fifth heartbeat in 60 s terminates', async () => {
        const harness = await PhoneHarness.create();
        await harness.openSession();

        for (let sequence = 1n; sequence <= 4n; sequence += 1n) {
            expect(
                await harness.engine.onInbound(
                    await harness.phoneRecord(RecordType.APP_HEARTBEAT, sequence),
                ),
            ).toEqual([]);
            harness.clock.advance(10_000);
        }

        expect(
            terminatedReason(
                await harness.engine.onInbound(
                    await harness.phoneRecord(RecordType.APP_HEARTBEAT, 5n),
                ),
            ),
        ).toBe('heartbeat');
        expect(harness.engine.isOpen).toBe(false);
    });

    it('client heartbeats do not count toward the receive window', async () => {
        const harness = await PhoneHarness.create();
        await harness.openSession();

        harness.clock.advance(60_000);
        expect(
            await harness.engine.onInbound(await harness.phoneRecord(RecordType.APP_HEARTBEAT, 1n)),
        ).toHaveLength(1);
        for (let sequence = 2n; sequence <= 4n; sequence += 1n) {
            harness.clock.advance(1_000);
            expect(
                await harness.engine.onInbound(
                    await harness.phoneRecord(RecordType.APP_HEARTBEAT, sequence),
                ),
            ).toEqual([]);
        }
        harness.clock.advance(1_000);
        expect(
            terminatedReason(
                await harness.engine.onInbound(
                    await harness.phoneRecord(RecordType.APP_HEARTBEAT, 5n),
                ),
            ),
        ).toBe('heartbeat');
    });

    it('application heartbeat spacing enforced', async () => {
        const harness = await PhoneHarness.create();
        await harness.openSession();

        const first = await harness.engine.sendApplication(RecordType.APP_HEARTBEAT, EMPTY);
        expect((await harness.openClientApplication(sendBytes(first), 1n)).type).toBe(
            RecordType.APP_HEARTBEAT,
        );

        harness.clock.advance(29_000);
        expect(await harness.engine.sendApplication(RecordType.APP_HEARTBEAT, EMPTY)).toEqual([]);

        harness.clock.advance(1_000);
        const second = await harness.engine.sendApplication(RecordType.APP_HEARTBEAT, EMPTY);
        expect((await harness.openClientApplication(sendBytes(second), 2n)).type).toBe(
            RecordType.APP_HEARTBEAT,
        );
    });

    it('outbound only activity prevents idle', async () => {
        const harness = await PhoneHarness.create();
        await harness.openSession();

        for (let index = 0; index < 4; index += 1) {
            harness.clock.advance(170_000);
            expect(
                await harness.engine.sendApplication(RecordType.PULL_REQUEST, EMPTY),
            ).toHaveLength(1);
            expect(harness.engine.isOpen).toBe(true);
        }
    });

    it('idle timeout counts injected sleep time', async () => {
        const ticked = await PhoneHarness.create();
        await ticked.openSession();
        ticked.clock.advance(180_000);
        expect(terminatedReason(await ticked.engine.onTick())).toBe('idle');
        expect(ticked.engine.isOpen).toBe(false);

        const awaited = await PhoneHarness.create();
        await awaited.openSession();
        awaited.clock.advance(180_000);
        expect(
            terminatedReason(
                await awaited.engine.onInbound(
                    await awaited.phoneRecord(RecordType.APP_HEARTBEAT, 1n),
                ),
            ),
        ).toBe('idle');
        expect(awaited.engine.isOpen).toBe(false);
    });

    it('phone signature timeout 30 s', async () => {
        const harness = await PhoneHarness.create();
        const started = await harness.start();
        const phoneHello = harness.phone.helloPayload();
        expect(await harness.deliverPhoneHello(phoneHello)).toEqual([]);
        const transcript = reconnectRbase(phoneHello, started.clientHelloPayload);

        harness.clock.advance(30_000);
        const effects = await harness.deliverPhoneSignature(transcript);

        expect(terminatedReason(effects)).toBe('handshake_timeout');
        expect(effects.some((effect) => effect.kind === 'send')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
    });

    it('late confirm completion after the deadline terminates', async () => {
        const harness = await PhoneHarness.create();
        const drive = await harness.drive();
        expect(
            await harness.engine.onInbound(await harness.confirmRecord(PEER_STATE_ACTIVE)),
        ).toEqual([]);
        expect(harness.engine.isOpen).toBe(false);

        harness.clock.advance(30_000);
        const effects = await harness.engine.onSendComplete(drive.token, true);

        expect(terminatedReason(effects)).toBe('handshake_timeout');
        expect(effects.some((effect) => effect.kind === 'authenticated')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
    });

    it('buffered error reply after teardown is not emitted', async () => {
        let entered = (): void => undefined;
        const replied = new Promise<void>((resolve) => {
            entered = resolve;
        });
        let release = (): void => undefined;
        const gate = new Promise<void>((resolve) => {
            release = resolve;
        });
        const handler: ApplicationHandler = {
            handles: () => true,
            async handle(
                _type: number,
                _body: Uint8Array,
                reply: (type: number, body: Uint8Array) => Promise<void>,
            ): Promise<void> {
                await reply(RecordType.APP_ERROR, appErrorBody(AppErrorCode.PROTOCOL_VIOLATION));
                entered();
                await gate;
                throw new Error('handler failed after teardown');
            },
        };
        const harness = await PhoneHarness.create({ handler });
        await harness.openSession();

        const pending = harness.engine.onInbound(
            await harness.phoneRecord(RecordType.PULL_EVENTS, 1n),
        );
        await replied;
        harness.engine.teardown();
        release();

        expect(await pending).toEqual([]);
        expect(harness.engine.isOpen).toBe(false);
    });

    it('late signer completion discarded', async () => {
        let enter = (): void => undefined;
        const entered = new Promise<void>((resolve) => {
            enter = resolve;
        });
        let release = (): void => undefined;
        const gate = new Promise<void>((resolve) => {
            release = resolve;
        });
        const clientIdentity = await crypto.subtle.generateKey(
            { name: 'ECDSA', namedCurve: 'P-256' },
            true,
            ['sign', 'verify'],
        );
        const harness = await PhoneHarness.create({
            sign: async (message) => {
                enter();
                await gate;
                return normalizeLowS(
                    new Uint8Array(
                        await crypto.subtle.sign(
                            { name: 'ECDSA', hash: 'SHA-256' },
                            clientIdentity.privateKey,
                            message,
                        ),
                    ),
                );
            },
        });
        const started = await harness.start();
        const phoneHello = harness.phone.helloPayload();
        expect(await harness.deliverPhoneHello(phoneHello)).toEqual([]);
        const transcript = reconnectRbase(phoneHello, started.clientHelloPayload);

        const pending = harness.deliverPhoneSignature(transcript);
        await entered;
        harness.clock.advance(30_000);
        release();

        const effects = await pending;
        expect(terminatedReason(effects)).toBe('handshake_timeout');
        expect(effects.some((effect) => effect.kind === 'send')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
    });

    it('trust survives teardown', async () => {
        const harness = await PhoneHarness.create();
        await harness.openSession();
        const record = harness.store.get(harness.peerId);
        const publicKey = record?.publicKey.slice();

        harness.engine.teardown();

        expect(harness.engine.isOpen).toBe(false);
        expect(harness.store.get(harness.peerId)?.state).toBe('active');
        expect(harness.store.get(harness.peerId)?.publicKey).toEqual(publicKey);
    });

    it('phone hello timeout after 30 s shows generic failure', async () => {
        const ticked = await PhoneHarness.create();
        await ticked.start();
        ticked.clock.advance(30_000);

        const tickEffects = await ticked.engine.onTick();
        expect(terminatedReason(tickEffects)).toBe('handshake_timeout');
        expect(ticked.engine.isOpen).toBe(false);

        const late = await PhoneHarness.create();
        await late.start();
        late.clock.advance(30_000);
        const lateEffects = await late.deliverPhoneHello(late.phone.helloPayload());
        expect(terminatedReason(lateEffects)).toBe('handshake_timeout');
        expect(lateEffects.some((effect) => effect.kind === 'send')).toBe(false);
        expect(late.engine.isOpen).toBe(false);
    });
});
