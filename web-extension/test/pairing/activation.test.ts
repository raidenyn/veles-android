import { describe, expect, it } from 'vitest';

import { ActivationEngine, composeHandlers } from '../../src/pairing/activation';
import { ProofRegistry } from '../../src/pairing/proofs';
import { RecordType, SessionClass } from '../../src/session/wire';
import { concat, hmacSha256 } from '../../src/session/schedule';
import { SessionManager } from '../../src/connector/sessions';
import type { TrustedPeer } from '../../src/trust/model';
import { PEER_STATE_BELOW_ACTIVE, PhoneHarness, terminatedReason } from '../session/client-harness';

// OTP-10 plan 9d: the Chrome activation exchange over the session-bound proof registry.
// The pending client waits for the recovery prepare before it re-commits; an active-family
// client that sees peer_state 0x02 sends the step-5 ack on `authenticated`; a recovery session
// never upgrades to full.

const ET = new Uint8Array(32).fill(0x42);

class FakeTrust {
    readonly confirms: { peerId: string; stage: string; proof: Uint8Array }[] = [];
    peerId = '';
    state = 'pending';

    peers(): Promise<readonly { installationId: string; state: string }[]> {
        return Promise.resolve(
            this.peerId === '' ? [] : [{ installationId: this.peerId, state: this.state }],
        );
    }

    confirm(peerId: string, stage: 'complete' | 'phone_active', proof: Uint8Array): Promise<void> {
        this.confirms.push({ peerId, stage, proof: proof.slice() });
        this.peerId = peerId;
        this.state = stage === 'complete' ? 'active_activation_pending' : 'active';
        return Promise.resolve();
    }
}

function recordType(bytes: Uint8Array): number {
    return (bytes[3] << 8) | bytes[4];
}

function sendOf(effects: { kind: string }[]): Uint8Array {
    const send = effects.find(
        (effect): effect is { kind: 'send'; bytes: Uint8Array } => effect.kind === 'send',
    );
    if (send === undefined) throw new Error('expected a send effect');
    return send.bytes;
}

function activationMac(key: Uint8Array, stage: number): Promise<Uint8Array> {
    return hmacSha256(key, concat(ET, Uint8Array.of(stage)));
}

describe('chrome activation engine', () => {
    it('activation_full_flow', async () => {
        const trust = new FakeTrust();
        const proofs = new ProofRegistry();
        const activation = new ActivationEngine({ trust, proofs });
        const harness = await PhoneHarness.create({
            state: 'pending',
            et: ET,
            handler: activation,
        });
        trust.peerId = harness.peerId;
        trust.state = 'pending';
        const { authenticated } = await harness.openSession({
            peerState: PEER_STATE_BELOW_ACTIVE,
        });
        expect(authenticated).toMatchObject({
            kind: 'authenticated',
            sessionClass: SessionClass.ACTIVATION_RECOVERY,
        });
        if (authenticated.kind !== 'authenticated') throw new Error('not authenticated');
        expect(authenticated.unprompted ?? []).toEqual([]);

        const recovery = harness.keys?.recovery;
        if (recovery === null || recovery === undefined) throw new Error('no recovery keys');

        // Step 0: a pending client only re-commits after this session's prepare verifies.
        const prepare = await activationMac(recovery.kRecPhone, 0);
        const prepareRecord = await harness.phoneRecord(RecordType.ENROLLMENT_PREPARE, 1n, prepare);
        const commitEffects = await harness.engine.onInbound(prepareRecord);
        const commit = await harness.openClientApplication(sendOf(commitEffects), 1n);
        expect(commit.type).toBe(RecordType.ACTIVATION_COMMIT);
        expect(commit.body).toEqual(await activationMac(recovery.kRecClient, 1));
        expect(recovery.kRecPhone.length).toBe(32);

        // Step 2/3: complete is verified, the mirror becomes activation-pending, the ack follows.
        const complete = await activationMac(recovery.kRecPhone, 2);
        const completeRecord = await harness.phoneRecord(
            RecordType.ACTIVATION_COMPLETE,
            2n,
            complete,
        );
        const ackEffects = await harness.engine.onInbound(completeRecord);
        expect(trust.confirms).toHaveLength(1);
        expect(trust.confirms[0]?.stage).toBe('complete');
        const ack = await harness.openClientApplication(sendOf(ackEffects), 2n);
        expect(ack.type).toBe(RecordType.ACTIVATION_ACK);
        expect(ack.body).toEqual(await activationMac(recovery.kRecClient, 3));

        // Step 6: the class never upgrades; OTP data stays unauthorized through teardown.
        expect(harness.engine.sessionClass).toBe(SessionClass.ACTIVATION_RECOVERY);
        const pull = await harness.phoneRecord(RecordType.PULL_EVENTS, 3n, Uint8Array.of(0));
        expect(terminatedReason(await harness.engine.onInbound(pull))).toBe('authorization');
    });

    it('wrong_mac_changes_nothing', async () => {
        const trust = new FakeTrust();
        const proofs = new ProofRegistry();
        const activation = new ActivationEngine({ trust, proofs });
        const harness = await PhoneHarness.create({
            state: 'pending',
            et: ET,
            handler: activation,
        });
        trust.peerId = harness.peerId;
        trust.state = 'pending';
        await harness.openSession({ peerState: PEER_STATE_BELOW_ACTIVE });
        const recovery = harness.keys?.recovery;
        if (recovery === null || recovery === undefined) throw new Error('no recovery keys');
        const complete = await activationMac(recovery.kRecPhone, 2);
        complete[complete.length - 1] ^= 0x01;
        const record = await harness.phoneRecord(RecordType.ACTIVATION_COMPLETE, 1n, complete);

        const effects = await harness.engine.onInbound(record);

        expect(terminatedReason(effects)).toBe('authorization');
        expect(trust.confirms).toEqual([]);
        expect(harness.store.get(harness.peerId)?.state).toBe('pending');
    });

    it('step5_ack_only_for_active_family', async () => {
        const trust = new FakeTrust();
        const proofs = new ProofRegistry();
        const activation = new ActivationEngine({ trust, proofs });
        const active = await PhoneHarness.create({
            state: 'active_activation_pending',
            et: ET,
            handler: activation,
        });
        trust.peerId = active.peerId;
        trust.state = 'active_activation_pending';
        const opened = await active.openSession({ peerState: PEER_STATE_BELOW_ACTIVE });
        if (opened.authenticated.kind !== 'authenticated') throw new Error('not authenticated');
        // The active mirror is still `activation_recovery`; its local active-family state,
        // not the class alone, is what selects the step-5 ack.
        expect(opened.authenticated.sessionClass).toBe(SessionClass.ACTIVATION_RECOVERY);
        const recovery = active.keys?.recovery;
        if (recovery === null || recovery === undefined) throw new Error('no recovery keys');
        const ack = opened.authenticated.unprompted ?? [];
        expect(ack).toHaveLength(1);
        expect(ack[0]?.type).toBe(RecordType.ACTIVATION_ACK);
        expect(ack[0]?.body).toEqual(await activationMac(recovery.kRecClient, 3));

        // An active-family client verifies the phone's prepare but never re-commits.
        const prepare = await activationMac(recovery.kRecPhone, 0);
        const prepareEffects = await active.engine.onInbound(
            await active.phoneRecord(RecordType.ENROLLMENT_PREPARE, 1n, prepare),
        );
        expect(prepareEffects.filter((effect) => effect.kind === 'send')).toEqual([]);

        const pending = await PhoneHarness.create({
            state: 'pending',
            et: ET,
            handler: activation,
        });
        trust.peerId = pending.peerId;
        trust.state = 'pending';
        const pendingOpened = await pending.openSession({
            peerState: PEER_STATE_BELOW_ACTIVE,
        });
        if (pendingOpened.authenticated.kind !== 'authenticated')
            throw new Error('not authenticated');
        expect(pendingOpened.authenticated.unprompted ?? []).toEqual([]);
    });

    it('pending_client_recommits', async () => {
        const trust = new FakeTrust();
        const activation = new ActivationEngine({ trust, proofs: new ProofRegistry() });
        const harness = await PhoneHarness.create({
            state: 'pending',
            et: ET,
            handler: activation,
        });
        trust.peerId = harness.peerId;
        trust.state = 'pending';
        const opened = await harness.openSession({ peerState: PEER_STATE_BELOW_ACTIVE });
        if (opened.authenticated.kind !== 'authenticated') throw new Error('not authenticated');
        expect(opened.authenticated.unprompted ?? []).toEqual([]);
        const recovery = harness.keys?.recovery;
        if (recovery === null || recovery === undefined) throw new Error('no recovery keys');

        // A prepare from a foreign session key does not authorize a commit, and ends the session.
        const wrong = await activationMac(recovery.kRecPhone, 0);
        wrong[0] ^= 0x01;
        expect(
            terminatedReason(
                await harness.engine.onInbound(
                    await harness.phoneRecord(RecordType.ENROLLMENT_PREPARE, 1n, wrong),
                ),
            ),
        ).toBe('authorization');

        const second = new ActivationEngine({ trust, proofs: new ProofRegistry() });
        const retry = await PhoneHarness.create({ state: 'pending', et: ET, handler: second });
        trust.peerId = retry.peerId;
        trust.state = 'pending';
        await retry.openSession({ peerState: PEER_STATE_BELOW_ACTIVE });
        const retryRecovery = retry.keys?.recovery;
        if (retryRecovery === null || retryRecovery === undefined) throw new Error('no keys');
        const prepare = await activationMac(retryRecovery.kRecPhone, 0);
        const effects = await retry.engine.onInbound(
            await retry.phoneRecord(RecordType.ENROLLMENT_PREPARE, 1n, prepare),
        );
        expect(recordType(sendOf(effects))).toBe(RecordType.ACTIVATION_COMMIT);
    });

    it('recovery_class_never_upgrades', async () => {
        const trust = new FakeTrust();
        const activation = new ActivationEngine({ trust, proofs: new ProofRegistry() });
        const harness = await PhoneHarness.create({
            state: 'pending',
            et: ET,
            handler: activation,
        });
        trust.peerId = harness.peerId;
        trust.state = 'pending';
        await harness.openSession({ peerState: PEER_STATE_BELOW_ACTIVE });
        const recovery = harness.keys?.recovery;
        if (recovery === null || recovery === undefined) throw new Error('no recovery keys');
        await harness.engine.onInbound(
            await harness.phoneRecord(
                RecordType.ENROLLMENT_PREPARE,
                1n,
                await activationMac(recovery.kRecPhone, 0),
            ),
        );
        await harness.engine.onInbound(
            await harness.phoneRecord(
                RecordType.ACTIVATION_COMPLETE,
                2n,
                await activationMac(recovery.kRecPhone, 2),
            ),
        );

        expect(harness.engine.sessionClass).toBe(SessionClass.ACTIVATION_RECOVERY);
        expect(harness.engine.isOpen).toBe(true);
    });

    it('explicit_peer_starts_recovery_for_pending', () => {
        const pending = {
            installationId: 'ab'.repeat(16),
            state: 'pending',
        } as unknown as TrustedPeer;
        expect(SessionManager.intendedPeer([pending], pending.installationId)).toBe(
            pending.installationId,
        );
        expect(SessionManager.intendedPeer([pending])).toBeUndefined();
    });

    it('release_style_null_extension_handler_still_activates', async () => {
        const trust = new FakeTrust();
        const activation = new ActivationEngine({ trust, proofs: new ProofRegistry() });
        expect(composeHandlers(undefined, undefined)).toBeUndefined();
        const composed = composeHandlers(activation, undefined);
        expect(composed).toBe(activation);
        const harness = await PhoneHarness.create({
            state: 'pending',
            et: ET,
            handler: activation,
        });
        trust.peerId = harness.peerId;
        trust.state = 'pending';
        await harness.openSession({ peerState: PEER_STATE_BELOW_ACTIVE });
        const recovery = harness.keys?.recovery;
        if (recovery === null || recovery === undefined) throw new Error('no recovery keys');
        await harness.engine.onInbound(
            await harness.phoneRecord(
                RecordType.ENROLLMENT_PREPARE,
                1n,
                await activationMac(recovery.kRecPhone, 0),
            ),
        );
        await harness.engine.onInbound(
            await harness.phoneRecord(
                RecordType.ACTIVATION_COMPLETE,
                2n,
                await activationMac(recovery.kRecPhone, 2),
            ),
        );
        expect(trust.confirms).toHaveLength(1);
    });

    it('replayed_recovery_record_rejected', async () => {
        const trust = new FakeTrust();
        const activation = new ActivationEngine({ trust, proofs: new ProofRegistry() });
        const first = await PhoneHarness.create({ state: 'pending', et: ET, handler: activation });
        trust.peerId = first.peerId;
        trust.state = 'pending';
        await first.openSession({ peerState: PEER_STATE_BELOW_ACTIVE });
        const firstRecovery = first.keys?.recovery;
        if (firstRecovery === null || firstRecovery === undefined) throw new Error('no keys');
        const replay = await first.phoneRecord(
            RecordType.ENROLLMENT_PREPARE,
            1n,
            await activationMac(firstRecovery.kRecPhone, 0),
        );

        const ephemeral = await crypto.subtle.generateKey(
            { name: 'ECDH', namedCurve: 'P-256' },
            true,
            ['deriveBits'],
        );
        const second = await PhoneHarness.create({
            state: 'pending',
            et: ET,
            handler: activation,
            phoneIdentity: first.phone.identity,
            phoneEphemeral: ephemeral,
            phoneNonce: crypto.getRandomValues(new Uint8Array(16)),
        });
        trust.peerId = second.peerId;
        trust.state = 'pending';
        await second.openSession({ peerState: PEER_STATE_BELOW_ACTIVE });

        const effects = await second.engine.onInbound(replay);

        expect(terminatedReason(effects)).toBe('record_invalid');
    });
});
