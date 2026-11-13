import { beforeEach, describe, expect, it } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';

import { ActivationEngine } from '../../src/pairing/activation';
import type { ActivationTrustRepository } from '../../src/pairing/activation';
import { ProofRegistry } from '../../src/pairing/proofs';
import { IndexedDbIdentityStore } from '../../src/trust/identity';
import { ChromeTrustRepository } from '../../src/trust/repository';
import type { TrustSnapshot } from '../../src/trust/model';
import { concat, hmacSha256, type RecoveryKeys } from '../../src/session/schedule';
import type { SessionEffect } from '../../src/session/engine';
import { RecordType, SessionClass } from '../../src/session/wire';
import {
    PEER_STATE_ACTIVE,
    PEER_STATE_BELOW_ACTIVE,
    PhoneHarness,
    terminatedReason,
    type PhoneRecord,
} from '../session/client-harness';

// OTP-10 plan 9e: connector-scoped expiry, interruption at every activation message,
// one-sided pending records, incomplete records that authorize no OTP data, the durable
// mirror across a restart, and two desktops against one phone.
//
// The same production pieces as `activation.test.ts` run here: the real client engine,
// the real `ActivationEngine` over a session-bound `ProofRegistry`, and the
// OTP2-STA-010d connector context rules of `ChromeTrustRepository`.

const ET = new Uint8Array(32).fill(0x42);
const STAGE_PREPARE = 0;
const STAGE_COMPLETE = 2;

type MirrorState = 'pending' | 'active_activation_pending' | 'active';
type PhoneState = 'pending' | 'committed_waiting_ack' | 'active';
type Cut =
    | 'before_prepare'
    | 'prepare_lost'
    | 'commit_lost'
    | 'complete_lost'
    | 'ack_lost'
    | 'ack_delivered';

/** The client's durable mirror for one phone, driven by the activation handler. */
class MirrorTrust implements ActivationTrustRepository {
    peerId = '';
    state: MirrorState | 'deleted' = 'pending';
    readonly confirms: { stage: string; proof: Uint8Array }[] = [];
    revoked = false;

    peers(): Promise<readonly { installationId: string; state: string }[]> {
        return Promise.resolve(
            this.peerId === '' || this.state === 'deleted'
                ? []
                : [{ installationId: this.peerId, state: this.state }],
        );
    }

    confirm(peerId: string, stage: 'complete' | 'phone_active', proof: Uint8Array): Promise<void> {
        this.confirms.push({ stage, proof: proof.slice() });
        this.peerId = peerId;
        this.state = 'active_activation_pending';
        return Promise.resolve();
    }

    revoke(peerId: string): Promise<void> {
        this.peerId = peerId;
        this.revoked = true;
        this.state = 'deleted';
        return Promise.resolve();
    }
}

function sendOf(effects: { kind: string }[]): Uint8Array {
    const send = effects.find(
        (effect): effect is { kind: 'send'; bytes: Uint8Array } => effect.kind === 'send',
    );
    if (send === undefined) throw new Error('expected a send effect');
    return send.bytes;
}

function mac(key: Uint8Array, stage: number): Promise<Uint8Array> {
    return hmacSha256(key, concat(ET, Uint8Array.of(stage)));
}

class Exchange {
    readonly trust = new MirrorTrust();
    readonly proofs = new ProofRegistry();
    readonly activation = new ActivationEngine({ trust: this.trust, proofs: this.proofs });
    harness: PhoneHarness | null = null;
    commitType: number | null = null;
    ackType: number | null = null;
    mirror: MirrorState;
    /** The phone's scripted record state; it decides the confirm byte the phone sends. */
    phone: PhoneState;
    private sequence = 1n;
    private clientTx = 1n;
    private owed: { type: number; body: Uint8Array }[] = [];

    constructor(mirror: MirrorState, phone: PhoneState = 'pending') {
        this.mirror = mirror;
        this.phone = phone;
    }

    async openSession(): Promise<{ sessionClass: SessionClass; unprompted: { type: number }[] }> {
        this.harness = await PhoneHarness.create({
            state: this.mirror,
            et: ET,
            handler: this.activation,
            upgrade: async (peerId) => {
                // A phone-reported active proof promotes the durable mirror.
                this.trust.peerId = peerId;
                this.trust.state = 'active';
            },
        });
        this.trust.peerId = this.harness.peerId;
        this.trust.state = this.mirror;
        const opened = await this.harness.openSession({
            peerState: this.phone === 'active' ? PEER_STATE_ACTIVE : PEER_STATE_BELOW_ACTIVE,
        });
        if (opened.authenticated.kind !== 'authenticated') {
            throw new Error('the session did not authenticate');
        }
        this.sequence = 1n;
        this.clientTx = 1n;
        this.owed = opened.authenticated.unprompted ?? [];
        return {
            sessionClass: opened.authenticated.sessionClass,
            unprompted: this.owed,
        };
    }

    private requireHarness(): PhoneHarness {
        if (this.harness === null) throw new Error('no live harness');
        return this.harness;
    }

    private recovery(): { kRecClient: Uint8Array; kRecPhone: Uint8Array } {
        const recovery = this.requireHarness().keys?.recovery;
        if (recovery === null || recovery === undefined) throw new Error('no recovery keys');
        return recovery;
    }

    async phoneRecord(type: number, sequence: bigint, body: Uint8Array): Promise<Uint8Array> {
        return this.requireHarness().phoneRecord(type, sequence, body);
    }

    /** The phone's step-0 prepare; returns the client's reply (a commit for a pending mirror). */
    async deliverPrepare(): Promise<SessionEffect[]> {
        const prepare = await mac(this.recovery().kRecPhone, STAGE_PREPARE);
        const record = await this.phoneRecord(
            RecordType.ENROLLMENT_PREPARE,
            this.sequence,
            prepare,
        );
        this.sequence += 1n;
        return this.requireHarness().engine.onInbound(record);
    }

    /** The phone's activation_complete; returns the client's ack on an activation-pending mirror. */
    async deliverComplete(): Promise<SessionEffect[]> {
        const complete = await mac(this.recovery().kRecPhone, STAGE_COMPLETE);
        const record = await this.phoneRecord(
            RecordType.ACTIVATION_COMPLETE,
            this.sequence,
            complete,
        );
        this.sequence += 1n;
        return this.requireHarness().engine.onInbound(record);
    }

    /** Applies the handshake up to [cut]; commit/ack are the records actually on the wire. */
    async forward(cut: Cut): Promise<void> {
        if (cut === 'before_prepare') return;
        const commitEffects = await this.deliverPrepare();
        if (cut === 'prepare_lost') return;
        const commit = await this.requireHarness().openClientApplication(
            sendOf(commitEffects),
            this.clientTx,
        );
        this.clientTx += 1n;
        this.commitType = commit.type;
        expect(commit.type).toBe(RecordType.ACTIVATION_COMMIT);
        this.phone = 'committed_waiting_ack';
        if (cut === 'commit_lost') return;
        const ackEffects = await this.deliverComplete();
        if (cut === 'complete_lost') return;
        const ack = await this.requireHarness().openClientApplication(
            sendOf(ackEffects),
            this.clientTx,
        );
        this.clientTx += 1n;
        this.ackType = ack.type;
        expect(ack.type).toBe(RecordType.ACTIVATION_ACK);
        this.mirror = 'active_activation_pending';
        if (cut === 'ack_lost') return;
        this.phone = 'active';
        // The phone is active; the mirror is promoted only by a FULL reconnect on that proof.
        await this.promote();
    }

    /** Resumes a new reconnect generation until the phone record is active. */
    async resume(): Promise<void> {
        if (this.mirror === 'pending') {
            const commitEffects = await this.deliverPrepare();
            const commit = await this.requireHarness().openClientApplication(
                sendOf(commitEffects),
                this.clientTx,
            );
            this.clientTx += 1n;
            this.commitType = commit.type;
            expect(commit.type).toBe(RecordType.ACTIVATION_COMMIT);
            const ackEffects = await this.deliverComplete();
            const ack = await this.requireHarness().openClientApplication(
                sendOf(ackEffects),
                this.clientTx,
            );
            this.clientTx += 1n;
            this.ackType = ack.type;
            expect(ack.type).toBe(RecordType.ACTIVATION_ACK);
            this.mirror = 'active_activation_pending';
            this.phone = 'active';
            return;
        }
        if (this.mirror === 'active_activation_pending') {
            // An active-family mirror ignores the phone's prepare and owes step 5.
            await this.deliverPrepare();
            for (const owed of this.owed) {
                const effects = await this.requireHarness().engine.sendApplication(
                    owed.type,
                    owed.body,
                );
                const ack = await this.requireHarness().openClientApplication(
                    sendOf(effects),
                    this.clientTx,
                );
                this.clientTx += 1n;
                this.ackType = ack.type;
                expect(ack.type).toBe(RecordType.ACTIVATION_ACK);
            }
            this.phone = 'active';
        }
    }

    /** A real FULL reconnect on the phone's active proof promotes the durable mirror. */
    private async promote(): Promise<void> {
        this.requireHarness().engine.teardown();
        this.harness = null;
        this.owed = [];
        this.mirror = 'active_activation_pending';
        this.harness = await PhoneHarness.create({
            state: 'active_activation_pending',
            et: ET,
            handler: this.activation,
            upgrade: async (peerId) => {
                this.trust.peerId = peerId;
                this.trust.state = 'active';
            },
        });
        this.trust.peerId = this.harness.peerId;
        this.trust.state = 'active_activation_pending';
        const opened = await this.harness.openSession({ peerState: PEER_STATE_ACTIVE });
        if (opened.authenticated.kind !== 'authenticated') {
            throw new Error('the promoted session did not authenticate');
        }
        expect(opened.authenticated.sessionClass).toBe(SessionClass.FULL);
        expect(this.trust.state).toBe('active');
        this.mirror = 'active';
        this.sequence = 1n;
        this.clientTx = 1n;
    }

    /** Sends a real OTP pull; an incomplete record must refuse it without delivering data. */
    async assertNoOtp(): Promise<void> {
        const classOf = this.requireHarness().engine.sessionClass;
        expect(classOf, 'an incomplete record must not be a full session').not.toBe(
            SessionClass.FULL,
        );
        await expect(
            this.requireHarness().engine.sendApplication(
                RecordType.PULL_REQUEST,
                new Uint8Array(0),
            ),
        ).rejects.toThrow();
    }

    /** Sends a real OTP pull; a full session must deliver it. */
    async assertOtpDelivered(): Promise<void> {
        const effects = await this.requireHarness().engine.sendApplication(
            RecordType.PULL_REQUEST,
            new Uint8Array(0),
        );
        expect(effects.length).toBe(1);
    }

    sessionClassOf(): SessionClass | null {
        return this.requireHarness().engine.sessionClass;
    }

    phoneActivationKey(): Uint8Array {
        return this.recovery().kRecPhone;
    }

    liveHarness(): PhoneHarness {
        return this.requireHarness();
    }

    async teardown(): Promise<void> {
        this.requireHarness().engine.teardown();
        this.harness = null;
    }
}

function profileStore(phoneId: string, raw: Uint8Array, et: Uint8Array): Map<string, PhoneRecord> {
    return new Map([[phoneId, { publicKey: raw, state: 'active_activation_pending', et }]]);
}

async function phoneIdentity(): Promise<{ pair: CryptoKeyPair; id: string; raw: Uint8Array }> {
    const pair = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, [
        'sign',
        'verify',
    ]);
    const raw = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
    const id = Array.from(raw.subarray(0, 16), (byte) => byte.toString(16).padStart(2, '0')).join(
        '',
    );
    return { pair, id, raw };
}

interface RepositoryHarness {
    keys: IndexedDbIdentityStore;
    make: () => ChromeTrustRepository;
    closeConnectors: () => void;
}

function repositoryHarness(db: IDBFactory, verifier = async () => true): RepositoryHarness {
    const keys = new IndexedDbIdentityStore(db);
    let value: unknown;
    const live = new Set<string>();
    let chain = Promise.resolve();
    const local = {
        restrict: async () => undefined,
        read: async () => structuredClone(value),
        write: async (next: TrustSnapshot) => {
            value = structuredClone(next);
        },
        clear: async () => {
            value = undefined;
        },
    };
    const lock = async <T>(fn: () => Promise<T>): Promise<T> => {
        const result = chain.then(fn);
        chain = result.then(
            () => undefined,
            () => undefined,
        );
        return result;
    };
    const make = () => {
        const id = crypto.randomUUID();
        live.add(id);
        return new ChromeTrustRepository(
            keys,
            local,
            lock,
            verifier,
            () => 0,
            () => 1000,
            { id, isAlive: async (context) => live.has(context) },
        );
    };
    return { keys, make, closeConnectors: () => live.clear() };
}

async function peerIdentity(db: IDBFactory) {
    const key = await new IndexedDbIdentityStore(db, crypto.randomUUID()).create();
    return {
        installationId: key.installationId,
        publicKey: key.publicKey,
        label: 'Phone',
        deviceType: 0,
        enrollmentTranscript: 'ab'.repeat(32),
    };
}

describe('chrome activation expiry and interruption', () => {
    let db: IDBFactory;

    beforeEach(() => {
        db = new IDBFactory();
    });

    it('chrome_context_loss_deletes_pending', async () => {
        const s = repositoryHarness(db);
        const peer = async () => {
            // A peer identity, never the repository's own: the repository creates its key
            // on its first operation, so a pre-created identity would look like key loss.
            const key = await new IndexedDbIdentityStore(db, crypto.randomUUID()).create();
            return {
                installationId: key.installationId,
                publicKey: key.publicKey,
                label: 'Phone',
                deviceType: 0,
                enrollmentTranscript: 'ab'.repeat(32),
            };
        };
        const repository = s.make();
        await repository.addPending(await peer());
        expect(await repository.peers()).toHaveLength(1);

        s.closeConnectors();

        expect(
            await s.make().peers(),
            'connector-context loss deletes the connector-scoped pending record',
        ).toEqual([]);

        const restarted = s.make();
        const mirror = await peer();
        await restarted.addPending(mirror);
        await restarted.confirm(mirror.installationId, 'complete', Uint8Array.of(1));
        s.closeConnectors();
        const survivors = await s.make().peers();
        expect(survivors.map((record) => record.state)).toEqual(['active_activation_pending']);
    });

    it('mirror_survives_restart', async () => {
        const s = repositoryHarness(db);
        const first = s.make();
        const mirror = await peerIdentity(db);
        await first.addPending(mirror);
        await first.confirm(mirror.installationId, 'complete', Uint8Array.of(1));

        // A restart opens a new repository instance over the same durable storage; the
        // active-family mirror is not connector-scoped and survives.
        s.closeConnectors();
        const restarted = s.make();
        expect((await restarted.peers()).map((record) => record.state)).toEqual([
            'active_activation_pending',
        ]);

        await restarted.confirm(mirror.installationId, 'phone_active', Uint8Array.of(1));
        s.closeConnectors();
        expect((await s.make().peers()).map((record) => record.state)).toEqual(['active']);
    });

    it('interruption_at_every_activation_message', async () => {
        const cuts: Cut[] = [
            'before_prepare',
            'prepare_lost',
            'commit_lost',
            'complete_lost',
            'ack_lost',
            'ack_delivered',
        ];
        for (const cut of cuts) {
            const exchange = new Exchange('pending');
            const opened = await exchange.openSession();
            expect(opened.sessionClass).toBe(SessionClass.ACTIVATION_RECOVERY);
            await exchange.forward(cut);

            const resumed = new Exchange(exchange.mirror, exchange.phone);
            await resumed.openSession();
            await resumed.resume();
            if (exchange.mirror === 'active') {
                expect(resumed.trust.state, `${cut} keeps the active mirror`).toBe('active');
                expect(resumed.sessionClassOf(), `${cut} reaches FULL`).toBe(SessionClass.FULL);
                await resumed.assertOtpDelivered();
            } else {
                expect(resumed.trust.state, `${cut} leaves a durable mirror`).toBe(
                    'active_activation_pending',
                );
                expect(resumed.ackType, `${cut} sends the activation ack`).toBe(
                    RecordType.ACTIVATION_ACK,
                );
                await resumed.assertNoOtp();
            }
            await resumed.teardown();
            await exchange.teardown();
        }
    });

    it('one_sided_pending', async () => {
        // The phone committed but the client never saw complete: a new session re-commits.
        const pending = new Exchange('pending', 'committed_waiting_ack');
        await pending.openSession();
        await pending.resume();
        expect(pending.commitType).toBe(RecordType.ACTIVATION_COMMIT);
        expect(pending.ackType).toBe(RecordType.ACTIVATION_ACK);
        expect(pending.trust.state).toBe('active_activation_pending');
        await pending.assertNoOtp();

        // The client committed and is active-family, but lost the ack: it owes step 5.
        const active = new Exchange('active_activation_pending', 'committed_waiting_ack');
        const activeOpened = await active.openSession();
        expect(activeOpened.sessionClass).toBe(SessionClass.ACTIVATION_RECOVERY);
        expect(activeOpened.unprompted.map((owed) => owed.type)).toEqual([
            RecordType.ACTIVATION_ACK,
        ]);
        await active.resume();
        expect(active.commitType).toBeNull();
        expect(active.ackType).toBe(RecordType.ACTIVATION_ACK);
        expect(active.trust.confirms).toEqual([]);
        expect(active.trust.state).toBe('active_activation_pending');
        await active.assertNoOtp();

        // A fully active mirror whose phone still reports the client below active is
        // full_pending_peer: the class alone must not authorize OTP data.
        const fullPending = new Exchange('active', 'committed_waiting_ack');
        const fullPendingOpened = await fullPending.openSession();
        expect(fullPendingOpened.sessionClass).toBe(SessionClass.FULL_PENDING_PEER);
        await fullPending.assertNoOtp();

        // The opposite one-sided case: no activation message may recover the client's
        // pending mirror into OTP access.
        const orphan = new Exchange('pending', 'pending');
        const orphanOpened = await orphan.openSession();
        expect(orphanOpened.sessionClass).toBe(SessionClass.ACTIVATION_RECOVERY);
        await orphan.assertNoOtp();
    });

    it('incomplete_records_authorize_no_otp', async () => {
        for (const mirror of ['pending', 'active_activation_pending'] as const) {
            const exchange = new Exchange(
                mirror,
                mirror === 'pending' ? 'pending' : 'committed_waiting_ack',
            );
            const opened = await exchange.openSession();
            expect(opened.sessionClass).not.toBe(SessionClass.FULL);
            await exchange.assertNoOtp();
        }
    });

    it('two_desktops_reconnect_without_a_code', async () => {
        const identity = await phoneIdentity();
        const desktops = [
            profileStore(identity.id, identity.raw, new Uint8Array(32).fill(0x41)),
            profileStore(identity.id, identity.raw, new Uint8Array(32).fill(0x42)),
        ];
        for (const [index, store] of desktops.entries()) {
            const harness = await PhoneHarness.create({
                state: 'active_activation_pending',
                et: store.get(identity.id)?.et,
                store,
                phoneIdentity: identity.pair,
                phoneId: identity.id,
            });
            const opened = await harness.openSession({ peerState: PEER_STATE_ACTIVE });
            if (opened.authenticated.kind !== 'authenticated') {
                throw new Error(`desktop ${index + 1} did not authenticate`);
            }
            expect(opened.authenticated.sessionClass).toBe(SessionClass.FULL);
            expect(store.get(identity.id)?.state).toBe('active');
            const sent = await harness.engine.sendApplication(
                RecordType.PULL_REQUEST,
                new Uint8Array(0),
            );
            expect(sent.length).toBe(1);
            harness.engine.teardown();
        }
    });

    it('wrong_complete_mac_erases_incomplete_materials', async () => {
        const exchange = new Exchange('pending');
        await exchange.openSession();
        const complete = await mac(exchange.phoneActivationKey(), STAGE_COMPLETE);
        complete[complete.length - 1] ^= 0x01;
        const record = await exchange.phoneRecord(RecordType.ACTIVATION_COMPLETE, 1n, complete);
        const effects = await exchange.liveHarness().engine.onInbound(record);
        expect(terminatedReason(effects)).toBe('authorization');
        expect(exchange.trust.revoked, 'a wrong MAC erases the incomplete mirror').toBe(true);
        expect(exchange.trust.state).toBe('deleted');
        expect(await exchange.trust.peers()).toEqual([]);
    });

    it('wrong_length_activation_body_erases_incomplete_materials', async () => {
        const exchange = new Exchange('pending');
        await exchange.openSession();
        const handler = exchange.activation.forSession({
            sessionIdPrefix: new Uint8Array(8).fill(0x55),
            sessionClass: SessionClass.ACTIVATION_RECOVERY,
            peerId: exchange.trust.peerId,
            et: ET.slice(),
            peerState: PEER_STATE_BELOW_ACTIVE,
            recovery: {
                kRecClient: new Uint8Array(32),
                kRecPhone: new Uint8Array(32),
            } satisfies RecoveryKeys,
        });
        await expect(
            handler.handle(RecordType.ACTIVATION_COMMIT, new Uint8Array(31), async () => undefined),
        ).rejects.toThrow();
        expect(exchange.trust.revoked, 'an unverifiable body erases the incomplete mirror').toBe(
            true,
        );
        expect(await exchange.trust.peers()).toEqual([]);
    });
});
