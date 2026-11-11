import { describe, expect, it } from 'vitest';
import { decodeFrame } from '../../src/session/frames';
import { decodeHello, LOCAL_CAPS } from '../../src/session/hello';
import { rbase as reconnectRbase } from '../../src/session/schedule';
import { RecordType, Role } from '../../src/session/wire';
import {
    PEER_STATE_ACTIVE,
    PhoneHarness,
    TestPhone,
    hexOf,
    patchSubtle,
    sendBytes,
    takeSubtle,
    terminatedReason,
    type PhoneRecord,
} from './client-harness';

describe('client reconnect handshake', () => {
    it('sends hello first with a fresh ephemeral key', async () => {
        const ephemeral = await crypto.subtle.generateKey(
            { name: 'ECDH', namedCurve: 'P-256' },
            true,
            ['deriveBits'],
        );
        const ephemeralPublic = new Uint8Array(
            await crypto.subtle.exportKey('raw', ephemeral.publicKey),
        );
        const nonce = Uint8Array.from({ length: 16 }, (_, index) => 0x20 + index);
        const harness = await PhoneHarness.create({
            clientEphemeral: async () => ephemeral,
            clientNonce: () => nonce,
        });

        const started = await harness.start();

        expect(started.effects).toHaveLength(1);
        expect(started.effects[0]).toMatchObject({ kind: 'send' });
        const frame = decodeFrame(started.helloFrame);
        expect(frame.type).toBe(0x06);
        const hello = decodeHello(frame.payload, Role.CLIENT);
        expect(hello.installationId).toEqual(harness.localInstallationId);
        expect(hello.ephemeralPublicKey).toEqual(ephemeralPublic);
        expect(hello.ephemeralPublicKey).not.toEqual(harness.clientPublicKey);
        expect(hello.nonce).toEqual(nonce);
        expect(hello.caps).toEqual(LOCAL_CAPS);
        expect(harness.engine.isOpen).toBe(false);

        // Every connection generates its own key material.
        const first = await PhoneHarness.create();
        const second = await PhoneHarness.create();
        const firstPoint = hexOf((await first.start()).clientHello.ephemeralPublicKey);
        const secondPoint = hexOf((await second.start()).clientHello.ephemeralPublicKey);
        expect(firstPoint).not.toBe(secondPoint);
    });

    it('aborts on phone id mismatch without signing', async () => {
        const harness = await PhoneHarness.create();
        const foreign = await TestPhone.create();
        await harness.start();

        const effects = await harness.deliverPhoneHello(foreign.helloPayload());

        // `preauth_reject` is phone→client only (OTP2-ENC-010): the client aborts without
        // sending a frame and closes with the same generic failure.
        expect(effects).toEqual([{ kind: 'close' }]);
        expect(harness.calls.sign).toBe(0);
        expect(harness.engine.isOpen).toBe(false);
    });

    it('generic failure state is identical for reject and bad signature', async () => {
        const rejected = await PhoneHarness.create();
        await rejected.start();
        const rejectEffects = await rejected.deliverPhoneHello(
            (await TestPhone.create()).helloPayload(),
        );

        const bad = await PhoneHarness.create();
        const started = await bad.start();
        const phoneHello = bad.phone.helloPayload();
        const accept = await bad.deliverPhoneHello(phoneHello);
        expect(accept).toEqual([]);
        const wrongSigner = await TestPhone.create();
        const badSignature = await wrongSigner.sign(
            reconnectRbase(phoneHello, started.clientHelloPayload),
        );
        const badEffects = await bad.deliverPhoneSignature(badSignature);

        expect(badEffects).toEqual(rejectEffects);
        expect(rejectEffects).toEqual([{ kind: 'close' }]);
        expect(badEffects.some((effect) => effect.kind === 'terminated')).toBe(false);
        expect(bad.calls.sign).toBe(0);
        expect(rejected.engine.isOpen).toBe(false);
        expect(bad.engine.isOpen).toBe(false);
    });

    it('peer confirm alone does not open the gate', async () => {
        const harness = await PhoneHarness.create({ state: 'active' });
        const drive = await harness.drive();

        const early = await harness.engine.onInbound(
            await harness.confirmRecord(PEER_STATE_ACTIVE),
        );
        expect(early).toEqual([]);
        expect(harness.engine.isOpen).toBe(false);

        const authenticated = await harness.engine.onSendComplete(drive.token, true);
        expect(authenticated).toEqual([
            {
                kind: 'authenticated',
                sessionClass: 'FULL',
                peerId: harness.peerId,
                sessionIdPrefix: harness.prefix,
            },
        ]);

        const failed = await PhoneHarness.create({ state: 'active' });
        const failedDrive = await failed.drive();
        await failed.engine.onInbound(await failed.confirmRecord(PEER_STATE_ACTIVE));
        const failedEffects = await failed.engine.onSendComplete(failedDrive.token, false);
        expect(terminatedReason(failedEffects)).toBe('send_failed');
        expect(failedEffects.some((effect) => effect.kind === 'authenticated')).toBe(false);
        expect(failed.engine.isOpen).toBe(false);
    });

    it('selected trusted A, valid trusted B answers: rejected before signing and before any metadata write', async () => {
        const selected = await TestPhone.create();
        const answering = await TestPhone.create();
        const selectedId = hexOf(selected.installationId);
        const answeringId = hexOf(answering.installationId);
        const store = new Map([
            [selectedId, { publicKey: selected.publicKey, state: 'active' as const }],
            [answeringId, { publicKey: answering.publicKey, state: 'active' as const }],
        ]);
        const harness = await PhoneHarness.create({
            store,
            phoneIdentity: answering.identity,
            phoneId: answeringId,
            expectedPeerId: selectedId,
        });
        await harness.start();

        const effects = await harness.deliverPhoneHello(answering.helloPayload());

        expect(effects).toEqual([{ kind: 'close' }]);
        expect(harness.calls.sign).toBe(0);
        expect(harness.calls.lookup).toBe(0);
        expect(harness.calls.upgrade).toHaveLength(0);
        expect(store.get(selectedId)?.state).toBe('active');
        expect(store.get(answeringId)?.state).toBe('active');
        expect(harness.engine.isOpen).toBe(false);
    });

    it('confirm decryption crossing the handshake deadline does not upgrade or authenticate', async () => {
        const harness = await PhoneHarness.create({ state: 'active_activation_pending' });
        const drive = await harness.drive();
        expect(await harness.engine.onSendComplete(drive.token, true)).toEqual([]);
        const confirm = await harness.confirmRecord(PEER_STATE_ACTIVE);

        let entered = (): void => undefined;
        const decrypting = new Promise<void>((resolve) => {
            entered = resolve;
        });
        let release = (): void => undefined;
        const gate = new Promise<void>((resolve) => {
            release = resolve;
        });
        const original = takeSubtle('decrypt');
        const restore = patchSubtle('decrypt', async (...args) => {
            const algorithm = args[0] as { additionalData?: Uint8Array };
            const aad = algorithm.additionalData;
            if (aad !== undefined && aad[3] === 0x00 && aad[4] === 0x01) {
                entered();
                await gate;
            }
            return original(...args);
        });
        try {
            const pending = harness.engine.onInbound(confirm);
            await decrypting;
            harness.clock.advance(30_000);
            release();

            const effects = await pending;

            expect(terminatedReason(effects)).toBe('handshake_timeout');
            expect(harness.calls.upgrade).toHaveLength(0);
            expect(harness.store.get(harness.peerId)?.state).toBe('active_activation_pending');
            expect(effects.some((effect) => effect.kind === 'authenticated')).toBe(false);
            expect(harness.engine.isOpen).toBe(false);
        } finally {
            restore();
        }
    });

    it('AES import failure closes the session and wipes the raw key material', async () => {
        const harness = await PhoneHarness.create();
        const started = await harness.start();
        const phoneHello = harness.phone.helloPayload();
        expect(await harness.deliverPhoneHello(phoneHello)).toEqual([]);
        const transcript = reconnectRbase(phoneHello, started.clientHelloPayload);
        const signature = await harness.phone.sign(transcript);

        const captured: Uint8Array[] = [];
        const original = takeSubtle('importKey');
        const restore = patchSubtle('importKey', async (...args) => {
            const algorithm = args[2] as { name?: string } | undefined;
            if (args[0] === 'raw' && algorithm?.name === 'AES-GCM') {
                captured.push(args[1] as Uint8Array);
                throw new Error('AES import failed');
            }
            return original(...args);
        });
        try {
            const effects = await harness.deliverPhoneSignature(transcript, signature);

            expect(effects).toEqual([{ kind: 'close' }]);
            expect(harness.engine.isOpen).toBe(false);
            expect(captured).toHaveLength(2);
            for (const buffer of captured) {
                expect(buffer.every((byte) => byte === 0)).toBe(true);
            }
        } finally {
            restore();
        }
    });

    it('heartbeat sent only while processing inbound after 60 s outbound silence', async () => {
        const harness = await PhoneHarness.create({ state: 'active' });
        await harness.openSession();

        harness.clock.advance(59_000);
        expect(
            await harness.engine.onInbound(await harness.phoneRecord(RecordType.APP_HEARTBEAT, 1n)),
        ).toEqual([]);

        harness.clock.advance(1_000);
        expect(await harness.engine.onTick()).toEqual([]);

        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.APP_HEARTBEAT, 2n),
        );
        expect(effects).toHaveLength(1);
        const heartbeat = await harness.openClientApplication(sendBytes(effects), 1n);
        expect(heartbeat.type).toBe(RecordType.APP_HEARTBEAT);
        expect(heartbeat.body).toEqual(new Uint8Array(0));

        // The heartbeat resets the outbound silence, so an immediate inbound sends none.
        expect(
            await harness.engine.onInbound(await harness.phoneRecord(RecordType.APP_HEARTBEAT, 3n)),
        ).toEqual([]);
        expect(harness.engine.isOpen).toBe(true);
    });

    it('upgrade not attempted when the generation is stale', async () => {
        const harness = await PhoneHarness.create({
            state: 'active_activation_pending',
            isCurrent: () => false,
        });
        const drive = await harness.drive();
        expect(await harness.engine.onSendComplete(drive.token, true)).toEqual([]);

        const effects = await harness.engine.onInbound(
            await harness.confirmRecord(PEER_STATE_ACTIVE),
        );

        expect(harness.calls.upgrade).toHaveLength(0);
        expect(harness.store.get(harness.peerId)?.state).toBe('active_activation_pending');
        expect(effects[0]).toMatchObject({
            kind: 'authenticated',
            sessionClass: 'ACTIVATION_RECOVERY',
        });
    });

    it('wrong HMAC confirm terminates', async () => {
        const harness = await PhoneHarness.create({ state: 'active' });
        await harness.drive();

        const effects = await harness.engine.onInbound(
            await harness.confirmRecord(PEER_STATE_ACTIVE, {
                confirmation: new Uint8Array(32),
            }),
        );

        expect(terminatedReason(effects)).toBe('confirm_invalid');
        expect(harness.engine.isOpen).toBe(false);
        expect(
            await harness.engine.onInbound(await harness.confirmRecord(PEER_STATE_ACTIVE)),
        ).toEqual([]);
        await expect(
            harness.engine.sendApplication(RecordType.PULL_REQUEST, new Uint8Array(0)),
        ).rejects.toThrow();
    });

    it('mirror upgrades before class selection and survives a recreated repository', async () => {
        const store = new Map<string, PhoneRecord>();
        const first = await PhoneHarness.create({ store, state: 'active_activation_pending' });
        const { authenticated } = await first.openSession({ peerState: PEER_STATE_ACTIVE });

        expect(first.calls.upgrade).toHaveLength(1);
        const call = first.calls.upgrade[0];
        expect(call?.peerId).toBe(first.peerId);
        expect(call?.proof).toEqual(await first.confirmRecord(PEER_STATE_ACTIVE));
        expect(store.get(first.peerId)?.state).toBe('active');
        expect(authenticated).toMatchObject({ sessionClass: 'FULL' });

        // A recreated repository/engine reads the upgraded durable mirror.
        const recreated = await PhoneHarness.create({
            store,
            phoneIdentity: first.phone.identity,
            phoneId: first.peerId,
        });
        const second = await recreated.openSession({ peerState: PEER_STATE_ACTIVE });
        expect(recreated.calls.upgrade).toHaveLength(0);
        expect(store.get(first.peerId)?.state).toBe('active');
        expect(second.authenticated).toMatchObject({ sessionClass: 'FULL' });
    });

    it('no upgrade on peer_state 0x02', async () => {
        const harness = await PhoneHarness.create({ state: 'active_activation_pending' });
        const { authenticated } = await harness.openSession({ peerState: 0x02 });

        expect(harness.calls.upgrade).toHaveLength(0);
        expect(harness.store.get(harness.peerId)?.state).toBe('active_activation_pending');
        expect(authenticated).toMatchObject({ sessionClass: 'ACTIVATION_RECOVERY' });
    });
});
