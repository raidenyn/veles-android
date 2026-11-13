import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { ClientMachine, type ClientTrustRepository } from '../../src/pairing/client-machine';
import { PairingExecution, initPairingCore, openHandleCount } from '../../src/pairing/core';
import { PairingFrameType, encodePairingFrame } from '../../src/pairing/frames';
import {
    fingerprintDisplay,
    isEnrollmentKeysWiped,
    pairFingerprint,
} from '../../src/pairing/enrollment';
import { equalConstantTime } from '../../src/session/engine';
import { RecordType } from '../../src/session/wire';
import { TrustError, bytes } from '../../src/trust/model';
import type { ChromeTrustRepository } from '../../src/trust/repository';
import { SessionClock, createTestTrust } from '../connector/session-harness';
import { PACKAGE_BYTES, PhonePeer, hexOf, openPhoneHandleCount } from './enrollment-harness';

// OTP-10 Task 8: the TypeScript client trace runner. Every client-role
// `enrollment.json` trace the OTP-10 inventory executes is replayed against the
// production `ClientMachine` and the production codecs; the phone half is the
// real WASM core as SPAKE2 party A (`PhonePeer`). Simulated flags in the traces
// are OTP observations (OTP2-FIX-004), never cryptographic evidence: a bad cA
// is one flipped MAC byte, a bad tag is one flipped ciphertext byte, and an
// invalid offer point is the all-zero SEC1 body. The cB, prepare and activate
// MACs and both identity records are real bytes.
//
// Effects are observed from real state: a pending record is a `peers()` read,
// `compute_et`/`persist_pending_record` require a durable record whose stored
// `ET` equals the phone's independently derived `ET`, a verified prepare is one
// whose MAC the phone actually produced over that same `ET`, and an erased key
// holder is the machine's real (wiped) holder.

const PROTOCOL_ROOT = resolve(import.meta.dirname, '../../../protocol');
const CODE = '424242';

type JsonObject = Record<string, unknown>;

interface Step {
    t_ms: number;
    event: string;
    simulate: Record<string, string>;
    expect: Record<string, string>;
}

interface Fixture {
    id: string;
    input: JsonObject;
    expected: JsonObject;
}

interface EngineEffect {
    terminate: boolean;
    erase: boolean;
    deliver: boolean;
}

interface InventoryEntry {
    'engine-effect': EngineEffect;
}

function loadFixtures(): Fixture[] {
    const parsed = JSON.parse(
        readFileSync(resolve(PROTOCOL_ROOT, 'v1/fixtures/enrollment.json'), 'utf8'),
    ) as { fixtures: Fixture[] };
    return parsed.fixtures;
}

function loadInventory(): Record<string, unknown> {
    return JSON.parse(
        readFileSync(resolve(PROTOCOL_ROOT, 'conformance/otp-10-scenarios.json'), 'utf8'),
    ) as Record<string, unknown>;
}

const FIXTURES = loadFixtures();
const INVENTORY = loadInventory();

const stepsOf = (fixture: Fixture): Step[] =>
    (
        fixture.input.steps as (Omit<Step, 'simulate' | 'expect'> & {
            simulate?: Record<string, unknown>;
            expect?: Record<string, string>;
        })[]
    ).map((step) => ({
        ...step,
        simulate: Object.fromEntries(
            Object.entries(step.simulate ?? {}).map(([key, value]) => [key, String(value)]),
        ),
        expect: step.expect ?? {},
    }));

const effectList = (fixture: Fixture, name: string): string[] => {
    const effects = (fixture.expected.effects ?? {}) as Record<string, string[] | undefined>;
    return effects[name] ?? [];
};

const finalStateOf = (fixture: Fixture): Record<string, string> => {
    const value = fixture.expected.final_state;
    if (typeof value !== 'string') return {};
    return Object.fromEntries(
        value
            .split(';')
            .filter((entry) => entry.length > 0)
            .map((entry) => {
                const [key, state] = entry.split('=');
                return [key, state];
            }),
    );
};

function engineEffectOf(entry: unknown): EngineEffect | null {
    if (typeof entry !== 'object' || entry === null) return null;
    const effect = (entry as InventoryEntry)['engine-effect'];
    if (typeof effect !== 'object' || effect === null) return null;
    return {
        terminate: effect.terminate === true,
        erase: effect.erase === true,
        deliver: effect.deliver === true,
    };
}

const CLIENT_FIXTURES = FIXTURES.filter((fixture) => fixture.input.role === 'client');

/**
 * Every client-role enrollment trace: the seven positives/negatives Task 8
 * executes plus the `st-neg-commit-without-verified-prepare` engine-effect
 * negative. The Task 9 activation-recovery rows are not client-role traces.
 */
const EXECUTED_CLIENT_TRACES = new Set([
    'st-en-client-confirms-in-order',
    'st-en-enrollment-no-otp-client-receives',
    'st-en-client-prepare-gate-full-flow',
    'st-neg-commit-without-verified-prepare',
    'st-neg-early-prepare-rejected',
    'st-neg-prepare-mismatch-erases',
    'st-en-client-one-sided-pending-cancelled-then-fresh-pairing',
    'st-neg-client-persist-failure-publishes-nothing',
]);

class RunRepository implements ClientTrustRepository {
    addPendingCalls = 0;
    failPersist = false;
    readonly revoked: string[] = [];

    constructor(private readonly delegate: ChromeTrustRepository) {}

    identity() {
        return this.delegate.identity();
    }
    peers() {
        return this.delegate.peers();
    }
    async addPending(peer: Parameters<ChromeTrustRepository['addPending']>[0]): Promise<void> {
        this.addPendingCalls += 1;
        if (this.failPersist) throw new TrustError();
        await this.delegate.addPending(peer);
    }
    confirm(
        peerId: string,
        stage: Parameters<ChromeTrustRepository['confirm']>[1],
        proof: Uint8Array,
    ) {
        return this.delegate.confirm(peerId, stage, proof);
    }
    revoke(peerId: string): Promise<void> {
        this.revoked.push(peerId);
        return this.delegate.revoke(peerId);
    }
}

function recordTypeOf(record: Uint8Array): number {
    return ((record[3] & 0xff) << 8) | (record[4] & 0xff);
}

function recordDirection(record: Uint8Array): number {
    return record[2] & 0xff;
}

function frameType(frame: Uint8Array): number {
    return frame[0] & 0xff;
}

function assertOrder(id: string, expected: string[], actual: string[]): void {
    let from = 0;
    for (const name of expected) {
        const index = actual.indexOf(name, from);
        expect(index, `${id} order name ${name}: ${JSON.stringify(actual)}`).toBeGreaterThanOrEqual(
            0,
        );
        from = index + 1;
    }
}

class TraceRun {
    private readonly clock = new SessionClock();
    private readonly trust = createTestTrust(this.clock);
    private repository!: RunRepository;
    private machine!: ClientMachine;
    private phone!: PhonePeer;
    private lastRequest: Uint8Array | null = null;
    private lastPb: Uint8Array | null = null;
    private expectedCompleteMac: Uint8Array | null = null;
    private recordEverPersisted = false;
    private persistAttempted = false;
    private identityPublicKey: Uint8Array | null = null;

    readonly observations: string[] = [];
    readonly sequence: string[] = [];
    readonly stateAt = new Map<number, Record<string, string>>();
    accepted = true;
    stage: string | null = null;
    noStateMutation = false;
    private observedEffect: EngineEffect | null = null;

    constructor(readonly fixture: Fixture) {}

    private observe(name: string): void {
        if (!this.observations.includes(name)) this.observations.push(name);
        this.sequence.push(name);
    }

    private keysErased(): boolean {
        const keys = this.machine.enrollmentKeysForTest();
        return keys === null || isEnrollmentKeysWiped(keys);
    }

    private observeErasePairingState(): void {
        if (this.keysErased()) this.observe('erase_pairing_state');
    }

    private observeCloseConnection(): void {
        if (this.machine.closed) this.observe('close_enrollment_connection');
    }

    private observePairingFailed(): void {
        if (this.machine.failed) this.observe('pairing_failed_generic');
    }

    private async prepare(): Promise<void> {
        const trust = await this.trust;
        this.identityPublicKey = bytes(trust.identity.publicKey, 65);
        this.repository = new RunRepository(trust.repository);
        trust.bindVerifier(async (_peer, stage, proof) => {
            if (stage !== 'complete' || this.expectedCompleteMac === null) return false;
            return equalConstantTime(proof, this.expectedCompleteMac);
        });
    }

    private async newAttempt(): Promise<void> {
        this.phone = await PhonePeer.create({ code: CODE });
        this.machine = new ClientMachine({
            core: PairingExecution,
            repo: this.repository,
            clock: this.clock.now,
            code: CODE,
        });
        this.lastRequest = this.machine.start();
        this.lastPb = null;
    }

    async execute(): Promise<void> {
        await this.prepare();
        for (const [index, step] of stepsOf(this.fixture).entries()) {
            this.clock.value = step.t_ms;
            await this.runStep(index, step);
            this.stateAt.set(index, {
                'client_pairing:main': this.machine.state,
                'durable_client_record:main': await this.durableState(),
            });
        }
    }

    private async runStep(index: number, step: Step): Promise<void> {
        switch (step.event) {
            case 'send_pairing_request':
                await this.newAttempt();
                return;
            case 'pairing_offer':
                await this.onOffer(step);
                return;
            case 'pairing_confirm_a':
                await this.onConfirmA(step);
                return;
            case 'enroll_identity':
                await this.onEnrollIdentity(index, step);
                return;
            case 'protected_record':
                await this.onProtectedRecord(step);
                return;
            case 'send_activation_commit':
                await this.onCommit();
                return;
            case 'activation_complete':
                await this.onComplete(step);
                return;
            case 'connection_lost':
                await this.onConnectionLost();
                return;
            case 'cancel_pairing':
                await this.onCancel();
                return;
            default:
                throw new Error(`${this.fixture.id} has no client mapping for ${step.event}`);
        }
    }

    private async onOffer(step: Step): Promise<void> {
        if (this.lastRequest === null) throw new Error('no pairing request');
        const offer = this.phone.offerFor(this.lastRequest, step.simulate.offer_valid !== 'false');
        const out = await this.machine.onFrame(offer);
        if (
            out.length === 1 &&
            frameType(out[0]) === PairingFrameType.PB &&
            this.machine.state === 'awaiting_ca'
        ) {
            this.lastPb = out[0];
            this.observe('compute_pB');
            return;
        }
        this.observe('pairing_failed_generic');
    }

    private async onConfirmA(step: Step): Promise<void> {
        if (this.lastPb === null) throw new Error('no client point');
        const confirmation = this.phone.acceptPoint(this.lastPb);
        if (step.simulate.cA_mac_ok === 'false') confirmation[0] ^= 1;
        const out = await this.machine.onFrame(
            encodePairingFrame(PairingFrameType.CA, confirmation),
        );
        if (out.length === 1 && frameType(out[0]) === PairingFrameType.CB) {
            this.observe('verify_confirm_a');
            this.observe('send_confirm_b');
            await this.phone.acceptConfirmB(out[0]);
            return;
        }
        this.observePairingFailed();
    }

    private async onEnrollIdentity(index: number, step: Step): Promise<void> {
        this.repository.failPersist = step.simulate.persist_ok === 'false';
        const frame = await this.phone.identityFrame({
            fingerprintOk: step.simulate.fingerprint_ok !== 'false',
            deviceTypeOk: step.simulate.device_type_ok !== 'false',
            aeadOk: step.simulate.aead_ok !== 'false',
        });
        const out = await this.machine.onFrame(frame);
        const ownRecord = out.find(
            (candidate) =>
                recordDirection(candidate) === 1 &&
                recordTypeOf(candidate) === RecordType.ENROLL_IDENTITY,
        );
        if (ownRecord !== undefined) {
            await this.phone.acceptClientIdentity(ownRecord);
            const et = await this.phone.expectedTranscript();
            const peer = (await this.repository.peers()).find(
                (candidate) => candidate.installationId === hexOf(this.phone.installationId),
            );
            if (peer?.state === 'pending' && peer.enrollmentTranscript === hexOf(et)) {
                this.observe('compute_et');
                this.observe('persist_pending_record');
                this.recordEverPersisted = true;
            }
            this.observe('send_enroll_identity');
            if (peer !== undefined) this.observe('record_persisted_pending');
            return;
        }
        if (step.simulate.persist_ok === 'false') {
            expect(
                this.repository.addPendingCalls,
                `${this.fixture.id} persist reached the store`,
            ).toBe(1);
            expect(await this.repository.peers()).toEqual([]);
            expect(this.machine.state, `${this.fixture.id}: persist failure returns idle`).toBe(
                'idle',
            );
            expect(this.keysErased(), `${this.fixture.id}: persist failure erases keys`).toBe(true);
            expect(this.machine.closed, `${this.fixture.id}: persist failure closes`).toBe(true);
            this.observe('persist_failed_no_usable_record_published');
            this.observeErasePairingState();
            this.observeCloseConnection();
            this.persistAttempted = true;
            return;
        }
        expect(this.machine.failed, `${this.fixture.id}: rejected identity is a failure`).toBe(
            true,
        );
        expect(this.keysErased(), `${this.fixture.id}: rejected identity erases keys`).toBe(true);
        expect(this.machine.closed, `${this.fixture.id}: rejected identity closes`).toBe(true);
        this.observePairingFailed();
        this.observeErasePairingState();
        this.observeCloseConnection();
    }

    private async onProtectedRecord(step: Step): Promise<void> {
        const type = Number.parseInt(
            String(step.simulate.record_type ?? '0x0104').replace(/^0x/, ''),
            16,
        );
        const before = this.machine.state;
        if (type === RecordType.ENROLLMENT_PREPARE) {
            const options = {
                macOk: step.simulate.act_mac_ok !== 'false',
                tagOk: step.simulate.tag_ok !== 'false',
                lengthOk: step.simulate.length_ok !== 'false',
                sessionIdOk: step.simulate.session_id_ok !== 'false',
            };
            // An early prepare can carry no MAC over an `ET` (the phone has not seen
            // the client record), so it is a structurally valid 32-byte body that the
            // ordering rule must reject before any MAC verification.
            const frame =
                before === 'awaiting_phone_identity'
                    ? await this.phone.protectedRecord(
                          RecordType.ENROLLMENT_PREPARE,
                          new Uint8Array(32),
                          options,
                      )
                    : await this.phone.prepareFrame(options);
            const out = await this.machine.onFrame(frame);
            expect(out).toEqual([]);
            if (before === 'awaiting_phone_identity') {
                expect(this.machine.state, `${this.fixture.id}: early prepare returns idle`).toBe(
                    'idle',
                );
                expect(this.machine.failed, `${this.fixture.id}: early prepare fails`).toBe(true);
                expect(this.keysErased(), `${this.fixture.id}: early prepare erases keys`).toBe(
                    true,
                );
                expect(this.machine.closed, `${this.fixture.id}: early prepare closes`).toBe(true);
                expect(await this.repository.peers()).toEqual([]);
                if (this.machine.failed) this.observe('reject_out_of_order_prepare');
                this.observeErasePairingState();
                this.observeCloseConnection();
                return;
            }
            if (before === 'awaiting_prepare') {
                if (this.machine.state === 'verified_prepare_ready') {
                    this.observe('verify_enrollment_prepare');
                    return;
                }
                expect(this.machine.failed, `${this.fixture.id}: prepare mismatch fails`).toBe(
                    true,
                );
                expect(
                    await this.repository.peers(),
                    `${this.fixture.id}: prepare mismatch deletes the pending record`,
                ).toEqual([]);
                expect(this.keysErased(), `${this.fixture.id}: prepare mismatch erases keys`).toBe(
                    true,
                );
                expect(this.machine.closed, `${this.fixture.id}: prepare mismatch closes`).toBe(
                    true,
                );
                if (this.machine.failed) this.observe('activation_mac_failure');
                this.observe('erase_pending_record');
                this.observe('erase_incomplete_materials');
                this.observeCloseConnection();
                return;
            }
            throw new Error(`${this.fixture.id} prepare arrived in ${before}`);
        }
        const rxBefore = this.machine.rxSequenceForTest();
        const peersBefore = await this.repository.peers();
        const frame = await this.phone.strayPushFrame();
        const out = await this.machine.onFrame(frame);
        expect(out).toEqual([]);
        expect(this.machine.rxSequenceForTest()).toBe(rxBefore);
        expect(this.machine.state).toBe('idle');
        expect(this.machine.failed, `${this.fixture.id}: the unauthorized record fails`).toBe(true);
        expect(this.machine.closed, `${this.fixture.id}: the unauthorized record closes`).toBe(
            true,
        );
        expect(await this.repository.peers()).toEqual(peersBefore);
        if (this.machine.failed && this.machine.state === 'idle') {
            this.observe('reject_unauthorized_enrollment_record');
        }
        this.observeCloseConnection();
    }

    private async onCommit(): Promise<void> {
        const entry = INVENTORY[this.fixture.id];
        const effect = engineEffectOf(entry);
        if (effect !== null) {
            const before = await this.snapshot();
            let frame: Uint8Array | null = null;
            try {
                frame = await this.machine.commit();
            } catch {
                frame = null;
            }
            const observed: EngineEffect = {
                terminate: this.machine.closed,
                erase: this.keysErased(),
                deliver: frame !== null,
            };
            this.observedEffect = observed;
            expect(observed, `${this.fixture.id}: engine-effect from real state`).toEqual(effect);
            this.noStateMutation = false;
            expect(await this.snapshot(), `${this.fixture.id}: no state mutation`).toEqual(before);
            this.noStateMutation = true;
            // A commit that actually reached the wire is the positive outcome of
            // this negative trace and must fail the fixture's `accepted:false`.
            this.accepted = frame !== null;
            this.stage = frame === null ? 'transition' : null;
            return;
        }
        const frame = await this.machine.commit();
        if (frame === null) throw new Error('the verified prepare must authorize a commit');
        expect(recordTypeOf(frame)).toBe(RecordType.ACTIVATION_COMMIT);
        this.observe('send_activation_commit');
    }

    private async onComplete(step: Step): Promise<void> {
        this.expectedCompleteMac = await this.phone.activationMac(2);
        const frame = await this.phone.completeFrame({
            macOk: step.simulate.act_mac_ok !== 'false',
        });
        const out = await this.machine.onFrame(frame);
        const peer = (await this.repository.peers())[0];
        if (peer?.state === 'active_activation_pending') {
            this.observe('record_active_client');
            this.observe('activation_pending_flag_set');
        }
        if (out.some((candidate) => recordTypeOf(candidate) === RecordType.ACTIVATION_ACK)) {
            this.observe('send_activation_ack');
        }
        const keys = this.machine.enrollmentKeysForTest();
        if (keys === null || isEnrollmentKeysWiped(keys)) this.observe('erase_enrollment_keys');
        if (this.machine.closed) this.observe('close_enrollment_connection');
        const fingerprint = this.machine.fingerprint();
        if (fingerprint !== undefined) {
            const expected =
                this.identityPublicKey === null
                    ? undefined
                    : fingerprintDisplay(
                          await pairFingerprint(this.phone.identityPubkey, this.identityPublicKey),
                      );
            expect(fingerprint, `${this.fixture.id}: display_fp_pair`).toBe(expected);
            this.observe('display_fp_pair');
        }
    }

    private async onConnectionLost(): Promise<void> {
        await this.machine.onDisconnect();
        expect(this.machine.state, `${this.fixture.id}: link loss returns idle`).toBe('idle');
        expect(this.machine.failed, `${this.fixture.id}: link loss is a failure`).toBe(true);
        expect(this.keysErased(), `${this.fixture.id}: link loss erases keys`).toBe(true);
        expect(this.machine.closed, `${this.fixture.id}: link loss closes`).toBe(true);
        this.observePairingFailed();
        this.observe('connection_lost');
        if (this.keysErased()) this.observe('erase_incomplete_materials');
        this.observeCloseConnection();
    }

    private async onCancel(): Promise<void> {
        const hadRecord = (await this.repository.peers()).length > 0;
        await this.machine.cancel();
        expect(this.machine.state, `${this.fixture.id}: cancel returns idle`).toBe('idle');
        expect(this.keysErased(), `${this.fixture.id}: cancel erases keys`).toBe(true);
        expect(this.machine.closed, `${this.fixture.id}: cancel closes`).toBe(true);
        if (this.machine.state === 'idle' && this.machine.closed) {
            this.observe('pairing_cancelled');
        }
        this.observeErasePairingState();
        if (hadRecord && (await this.repository.peers()).length === 0) {
            this.observe('erase_incomplete_materials');
        }
        this.observeCloseConnection();
    }

    private async snapshot(): Promise<unknown> {
        return {
            state: this.machine.state,
            fingerprint: this.machine.fingerprint(),
            rx: this.machine.rxSequenceForTest().toString(),
            handles: openHandleCount(),
            peers: await this.repository.peers(),
            closed: this.machine.closed,
            failed: this.machine.failed,
            keysWiped: this.keysErased(),
        };
    }

    async durableState(): Promise<string> {
        const peers = await this.repository.peers();
        if (peers.length > 0) {
            const state = peers[0].state;
            return state === 'pending'
                ? 'pending'
                : state === 'active_activation_pending'
                  ? 'active_activation_pending'
                  : 'active';
        }
        // `recordEverPersisted` is set only after a real `peers()` read showed the
        // matching pending record; `persistAttempted` only after asserting that the
        // store call happened. Neither is a flag set by the code path itself.
        if (
            this.recordEverPersisted ||
            (this.persistAttempted && this.repository.addPendingCalls === 1)
        ) {
            return 'deleted';
        }
        return 'absent';
    }

    async finalState(): Promise<Record<string, string>> {
        return {
            'client_pairing:main': this.machine.state,
            'durable_client_record:main': await this.durableState(),
        };
    }

    async teardown(): Promise<void> {
        await this.machine.onDisconnect();
        this.phone.close();
        this.expectedCompleteMac?.fill(0);
    }

    get observedEngineEffect(): EngineEffect | null {
        return this.observedEffect;
    }
}

async function runTrace(id: string): Promise<TraceRun> {
    const fixture = CLIENT_FIXTURES.find((candidate) => candidate.id === id);
    if (fixture === undefined) throw new Error(`no client fixture ${id}`);
    const run = new TraceRun(fixture);
    try {
        await run.execute();
        const final = await run.finalState();
        const expected = finalStateOf(fixture);
        const actual = Object.fromEntries(
            Object.entries(expected).map(([machine]) => [machine, final[machine]]),
        );
        expect(actual, `${id} final_state`).toEqual(expected);
        stepsOf(fixture).forEach((step, index) => {
            for (const [machine, state] of Object.entries(step.expect)) {
                expect(run.stateAt.get(index)?.[machine], `${id} step ${index} ${machine}`).toBe(
                    state,
                );
            }
        });
        for (const required of effectList(fixture, 'require')) {
            expect(run.observations, `${id} requires ${required}`).toContain(required);
        }
        for (const forbidden of effectList(fixture, 'forbid')) {
            expect(run.observations, `${id} forbids ${forbidden}`).not.toContain(forbidden);
        }
        assertOrder(id, effectList(fixture, 'order'), run.sequence);
        expect(run.accepted, `${id} accepted`).toBe(fixture.expected.accepted === true);
        return run;
    } finally {
        await run.teardown();
    }
}

describe('client enrollment traces', () => {
    it('every client trace in enrollment.json is executed exactly once', () => {
        const clientIds = CLIENT_FIXTURES.map((fixture) => fixture.id).sort();
        expect(clientIds).toEqual([...EXECUTED_CLIENT_TRACES].sort());
        for (const id of clientIds) {
            const entry = INVENTORY[id];
            expect(entry, `${id} must be in the OTP-10 inventory`).toBeDefined();
            expect(
                entry === 'execute' || engineEffectOf(entry) !== null,
                `${id} must be executed or an explicit engine-effect entry`,
            ).toBe(true);
        }
    });

    it('every_executed_client_trace_passes_on_the_real_core', async () => {
        await initPairingCore(readFileSync(PACKAGE_BYTES));
        for (const fixture of CLIENT_FIXTURES) {
            if (engineEffectOf(INVENTORY[fixture.id]) !== null) continue;
            await runTrace(fixture.id);
        }
        expect(openHandleCount()).toBe(0);
        expect(openPhoneHandleCount()).toBe(0);
    });

    it('commit_without_verified_prepare_sends_nothing_and_mutates_nothing', async () => {
        await initPairingCore(readFileSync(PACKAGE_BYTES));
        const run = await runTrace('st-neg-commit-without-verified-prepare');
        expect(run.observedEngineEffect).toEqual({
            terminate: false,
            erase: false,
            deliver: false,
        });
        expect(run.noStateMutation).toBe(true);
        expect(run.stage).toBe('transition');
        expect(openHandleCount()).toBe(0);
        expect(openPhoneHandleCount()).toBe(0);
    });
});
