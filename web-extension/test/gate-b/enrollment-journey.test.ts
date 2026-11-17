import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

import { beforeAll, describe, expect, it } from 'vitest';

import { ActivationEngine } from '../../src/pairing/activation';
import { ClientMachine } from '../../src/pairing/client-machine';
import { initPairingCore, PairingExecution } from '../../src/pairing/core';
import { ProofRegistry } from '../../src/pairing/proofs';
import { SessionClass } from '../../src/session/wire';

import { MemoryTrust } from './trust';
import {
    EVENT_PAIRING_STATE,
    EVENT_TRUST,
    EVENT_WINDOW,
    command,
    deriveIdentity,
    eventText,
    hexOf,
    isEvent,
    loadJourney,
    newClientEngine,
    openSession,
    runJourney,
    startPhone,
    type JourneyStep,
    type StepResult,
} from './transport';

// OTP-12: the `enrollment` journey runs the production ClientMachine and pairing
// frames directly against the existing pairing loopback (PhoneLoopbackMainKt
// pairing). It never imports the production PairingLink: the transport here is the
// child JVM pipe, so the test proves the pairing core and trust surface alone.
//
// Skipped only when the loopback classpath is unset; `VELES_LOOPBACK_REQUIRED=1`
// (set by sessionLoopbackCheck) turns that skip into a failure so CI cannot lose it.

const CLASSPATH = process.env.VELES_LOOPBACK_CLASSPATH ?? '';
const REQUIRED = process.env.VELES_LOOPBACK_REQUIRED === '1';
const TIMEOUT_MS = 60_000;
const PAIRING_CODE = '000042';
const PHONE_LABEL = 'Veles loopback phone identity v1';
const PHONE_INSTALLATION_LABEL = 'Veles loopback phone installation v1';
const CLIENT_LABEL = 'Veles loopback client identity v1';
const CLIENT_INSTALLATION_LABEL = 'Veles loopback client installation v1';

function fixedNonce(label: string): Uint8Array {
    return new Uint8Array(createHash('sha256').update(label).digest()).subarray(0, 16);
}

interface PhoneTrustPeer {
    installationId: string;
    publicKey: string;
    state: string;
}

interface PhoneTrust {
    identity: { installationId: string; publicKey: string };
    peers: PhoneTrustPeer[];
}

interface ReconnectSession {
    keys: Awaited<ReturnType<typeof openSession>>['keys'];
    prefix: Uint8Array;
    sessionClass: SessionClass;
}

function equalBytes(left: Uint8Array, right: Uint8Array): boolean {
    if (left.length !== right.length) return false;
    let difference = 0;
    for (let index = 0; index < left.length; index += 1) difference |= left[index] ^ right[index];
    return difference === 0;
}

function keysDiffer(left: ReconnectSession['keys'], right: ReconnectSession['keys']): boolean {
    return (
        !equalBytes(left.sessionId, right.sessionId) &&
        !equalBytes(left.c2pPrefix, right.c2pPrefix) &&
        !equalBytes(left.p2cPrefix, right.p2cPrefix) &&
        !equalBytes(left.clientConfirmKey, right.clientConfirmKey) &&
        !equalBytes(left.phoneConfirmKey, right.phoneConfirmKey)
    );
}

class EnrollmentDriver {
    phoneTrust: PhoneTrust | null = null;
    phoneState = '';

    constructor(
        private readonly phone: ReturnType<typeof startPhone>,
        private readonly trust: MemoryTrust,
        private readonly proofs: ProofRegistry,
        private readonly phoneIdentity: Awaited<ReturnType<typeof deriveIdentity>>,
    ) {}

    async run(step: JourneyStep): Promise<StepResult> {
        switch (step.action) {
            case 'enroll':
                return this.#enroll();
            case 'reconnect':
                return this.#reconnect();
            default:
                throw new Error(`the enrollment driver has no action ${step.action}`);
        }
    }

    async #enroll(): Promise<{ sessionClass: string }> {
        command(this.phone, 'pair');
        await this.phone.next((message) => isEvent(message, EVENT_WINDOW));
        const machine = new ClientMachine({
            core: PairingExecution,
            repo: this.trust,
            proofs: this.proofs,
            clock: () => 0,
            code: PAIRING_CODE,
            label: 'Chrome',
            nonce: () => fixedNonce('gate-b client pairing nonce'),
            identityNonce: () => fixedNonce('gate-b client identity nonce'),
        });
        let outbound: Uint8Array[] = [machine.start()];
        let commitSent = false;
        for (;;) {
            for (const frame of outbound) this.phone.send(0, frame);
            outbound = [];
            if (machine.closed) break;
            const message = await this.phone.next((candidate) => candidate.kind === 0);
            outbound = await machine.onFrame(message.payload);
            if (!machine.closed && machine.state === 'verified_prepare_ready' && !commitSent) {
                commitSent = true;
                outbound.push(await machine.commit());
            }
        }
        if (machine.failed) throw new Error('the enrollment machine failed');
        expect(machine.state).toBe('active');
        expect(machine.fingerprint()).toMatch(/^[0-9A-F]{4} [0-9A-F]{4} [0-9A-F]{4}$/);

        command(this.phone, 'trust');
        this.phoneTrust = JSON.parse(
            eventText(await this.phone.next((message) => isEvent(message, EVENT_TRUST))),
        ) as PhoneTrust;
        command(this.phone, 'state');
        this.phoneState = eventText(
            await this.phone.next((message) => isEvent(message, EVENT_PAIRING_STATE)),
        );
        expect(this.phoneState).toBe('ACTIVE');
        expect(this.phoneTrust.identity.installationId).toBe(
            hexOf(this.phoneIdentity.installationId),
        );
        expect(this.phoneTrust.peers).toHaveLength(1);
        expect(this.phoneTrust.peers[0]?.state).toBe('active');
        expect(this.phoneTrust.peers[0]?.publicKey).toBe(hexOf(this.trust.localPoint()));
        return { sessionClass: 'enrollment' };
    }

    async #reconnect(): Promise<{ sessionClass: string; keyFresh: boolean }> {
        const first = await this.#oneReconnect();
        const second = await this.#oneReconnect();
        expect(first.sessionClass).toBe(SessionClass.FULL);
        expect(second.sessionClass).toBe(SessionClass.FULL);
        expect(keysDiffer(first.keys, second.keys)).toBe(true);
        expect(equalBytes(first.prefix, second.prefix)).toBe(false);
        return { sessionClass: 'full', keyFresh: true };
    }

    async #oneReconnect(): Promise<ReconnectSession> {
        command(this.phone, 'reconnect');
        const peerId = hexOf(this.phoneIdentity.installationId);
        const peer = await this.trust.lookup(peerId);
        if (peer === undefined) throw new Error('the enrolled phone record is missing');
        const bundle = await newClientEngine({
            localInstallationId: this.trust.localInstallationId(),
            sign: (message) => this.trust.sign(message),
            expectedPeerId: peerId,
            isCurrent: () => true,
            lookupPhone: async (id) => (id === peerId ? peer : undefined),
            upgradeMirror: (id, proof) => this.trust.confirm(id, 'phone_active', proof),
            now: () => 1_000,
            // The reconnect runs through the production recovery activation handler, exactly
            // like the existing loopback recovery scenario, not a bare no-op handler.
            handler: new ActivationEngine({ trust: this.trust, proofs: this.proofs }),
        });
        const session = await openSession(this.phone, bundle, peer.et ?? null);
        const sessionClass = bundle.engine.sessionClass;
        if (sessionClass === null) throw new Error('the reconnect did not authenticate');
        return { keys: session.keys, prefix: session.prefix, sessionClass };
    }
}

if (CLASSPATH === '' && REQUIRED) {
    describe('gate-b enrollment journey', () => {
        it('fails when VELES_LOOPBACK_REQUIRED is set without a classpath', () => {
            expect(
                CLASSPATH,
                'VELES_LOOPBACK_CLASSPATH is required when VELES_LOOPBACK_REQUIRED=1',
            ).not.toBe('');
        });
    });
} else if (CLASSPATH === '') {
    describe.skip('gate-b enrollment journey (VELES_LOOPBACK_CLASSPATH unset)', () => {
        it('skips without a Kotlin classpath', () => undefined);
    });
} else {
    describe('gate-b enrollment journey', () => {
        beforeAll(async () => {
            await initPairingCore(
                readFileSync(new URL('../../rust-wasm/pkg/veles_crypto_bg.wasm', import.meta.url)),
            );
        });

        it(
            'enrolls, activates and reconnects with fresh keys',
            async () => {
                const seed = 'otp12-gate-b-enrollment';
                const phoneIdentity = await deriveIdentity(
                    PHONE_LABEL,
                    PHONE_INSTALLATION_LABEL,
                    seed,
                );
                const clientIdentity = await deriveIdentity(
                    CLIENT_LABEL,
                    CLIENT_INSTALLATION_LABEL,
                    seed,
                );
                const trust = new MemoryTrust(clientIdentity);
                const proofs = new ProofRegistry();
                const phone = startPhone({ seed, main: 'session', mode: 'pairing' });
                try {
                    const driver = new EnrollmentDriver(phone, trust, proofs, phoneIdentity);
                    await runJourney(loadJourney('enrollment'), driver);
                    expect(driver.phoneState).toBe('ACTIVE');
                    expect(driver.phoneTrust?.peers[0]?.state).toBe('active');
                } finally {
                    await phone.stop();
                }
            },
            TIMEOUT_MS,
        );
    });
}
