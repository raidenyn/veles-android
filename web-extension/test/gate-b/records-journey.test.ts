import { readFileSync } from 'node:fs';

import { beforeAll, describe, expect, it } from 'vitest';

import { initPairingCore } from '../../src/pairing/core';
import { RecordType, SessionClass } from '../../src/session/wire';

import { MemoryTrust } from './trust';
import {
    command,
    deriveIdentity,
    hexOf,
    isProtectedRecord,
    loadJourney,
    newClientEngine,
    openSession,
    parseEvent,
    parsePullEvents,
    recordTypeOf,
    runJourney,
    sendEffects,
    startPhone,
    type JourneyStep,
    type PulledEvent,
    type StepResult,
} from './transport';

// OTP-12: the `protected-records` journey runs against the testDebug-only
// GateBLoopbackMainKt. The fixture and scheduler there live on a manual clock, so
// `advance_clock` expires records and fires the scheduled push with no real delay;
// the client engine and the phone engine's session clock are separate.
//
// Skipped only when the loopback classpath is unset; `VELES_LOOPBACK_REQUIRED=1`
// (set by sessionLoopbackCheck) turns that skip into a failure.

const CLASSPATH = process.env.VELES_LOOPBACK_CLASSPATH ?? '';
const REQUIRED = process.env.VELES_LOOPBACK_REQUIRED === '1';
const TIMEOUT_MS = 60_000;
const PHONE_LABEL = 'Veles loopback phone identity v1';
const PHONE_INSTALLATION_LABEL = 'Veles loopback phone installation v1';
const CLIENT_LABEL = 'Veles loopback client identity v1';
const CLIENT_INSTALLATION_LABEL = 'Veles loopback client installation v1';

class ProtectedRecordsDriver {
    #lastPushed: PulledEvent | null = null;

    constructor(
        private readonly phone: ReturnType<typeof startPhone>,
        private readonly engine: Awaited<ReturnType<typeof newClientEngine>>['engine'],
        private readonly phoneInstallationId: Uint8Array,
        private readonly buildEngine: () => Promise<Awaited<ReturnType<typeof newClientEngine>>>,
    ) {}

    async run(step: JourneyStep): Promise<StepResult> {
        switch (step.action) {
            case 'seed':
                return this.#seed(step);
            case 'pull':
                return this.#pull();
            case 'advance_clock':
                return this.#advance(step);
            case 'schedule_push':
                return this.#schedule(step);
            case 'push':
                return this.#push();
            case 'ack':
                return this.#ack();
            case 'revoke':
                return this.#revoke();
            case 'reconnect_expect_rejected':
                return this.#rejected();
            default:
                throw new Error(`the protected-records driver has no action ${step.action}`);
        }
    }

    async #seed(step: JourneyStep): Promise<{ eventCount: number }> {
        const lifetimes = (step.params['lifetimes'] as number[] | undefined) ?? [];
        command(this.phone, `seed ${lifetimes.join(',')}`);
        await this.phone.waitForStderr(`seeded count=${lifetimes.length}`);
        return { eventCount: lifetimes.length };
    }

    async #pull(): Promise<{
        eventCount: number;
        eventIds: string[];
        remainingLifetimes: number[];
    }> {
        await this.#send(RecordType.PULL_REQUEST, new Uint8Array(0));
        const message = await this.phone.next(
            (candidate) =>
                isProtectedRecord(candidate) && recordTypeOf(candidate) === RecordType.PULL_EVENTS,
        );
        const effects = await this.engine.onInbound(message.payload);
        const delivered = effects.find(
            (effect) => effect.kind === 'deliver' && effect.type === RecordType.PULL_EVENTS,
        );
        if (delivered?.kind !== 'deliver') throw new Error('the pull delivered no events record');
        const events = parsePullEvents(delivered.body);
        return {
            eventCount: events.length,
            eventIds: events.map((event) => event.id),
            remainingLifetimes: events.map((event) => event.remainingLifetime),
        };
    }

    async #advance(step: JourneyStep): Promise<Record<string, never>> {
        const seconds = (step.params['seconds'] as number | undefined) ?? 0;
        command(this.phone, `advance ${seconds}`);
        return {};
    }

    async #schedule(step: JourneyStep): Promise<Record<string, never>> {
        const delay = (step.params['delay_seconds'] as number | undefined) ?? 0;
        command(this.phone, `schedule ${delay}`);
        await this.phone.waitForStderr(`scheduled delay=${delay}`);
        return {};
    }

    async #push(): Promise<{ eventCount: number }> {
        const message = await this.phone.next(
            (candidate) =>
                isProtectedRecord(candidate) && recordTypeOf(candidate) === RecordType.PUSH_EVENT,
        );
        const effects = await this.engine.onInbound(message.payload);
        const delivered = effects.find(
            (effect) => effect.kind === 'deliver' && effect.type === RecordType.PUSH_EVENT,
        );
        if (delivered?.kind !== 'deliver') throw new Error('the push delivered no event');
        const event = parseEvent(delivered.body);
        this.#lastPushed = event;
        return { eventCount: 1 };
    }

    async #ack(): Promise<Record<string, never>> {
        const pushed = this.#lastPushed;
        if (pushed === null) throw new Error('there is no pushed event to acknowledge');
        const body = new Uint8Array(32);
        body.set(this.phoneInstallationId, 0);
        body.set(pushed.idBytes, 16);
        await this.#send(RecordType.EVENT_ACK, body);
        return {};
    }

    async #revoke(): Promise<Record<string, never>> {
        command(this.phone, 'revoke');
        await this.phone.waitForStderr('revoked');
        return {};
    }

    async #rejected(): Promise<{ rejected: boolean }> {
        const bundle = await this.buildEngine();
        const startEffects = await bundle.engine.start();
        sendEffects(this.phone, startEffects);
        const message = await this.phone.next((candidate) => candidate.kind === 0);
        const effects = await bundle.engine.onInbound(message.payload);
        const rejected =
            !bundle.engine.isOpen &&
            effects.some((effect) => effect.kind === 'close' || effect.kind === 'terminated');
        return { rejected };
    }

    async #send(type: number, body: Uint8Array): Promise<void> {
        sendEffects(this.phone, await this.engine.sendApplication(type, body));
    }
}

if (CLASSPATH === '' && REQUIRED) {
    describe('gate-b protected-records journey', () => {
        it('fails when VELES_LOOPBACK_REQUIRED is set without a classpath', () => {
            expect(
                CLASSPATH,
                'VELES_LOOPBACK_CLASSPATH is required when VELES_LOOPBACK_REQUIRED=1',
            ).not.toBe('');
        });
    });
} else if (CLASSPATH === '') {
    describe.skip('gate-b protected-records journey (VELES_LOOPBACK_CLASSPATH unset)', () => {
        it('skips without a Kotlin classpath', () => undefined);
    });
} else {
    describe('gate-b protected-records journey', () => {
        beforeAll(async () => {
            await initPairingCore(
                readFileSync(new URL('../../rust-wasm/pkg/veles_crypto_bg.wasm', import.meta.url)),
            );
        });

        it(
            'serves history, expires after advance, pushes, acks and rejects a revoked reconnect',
            async () => {
                const seed = 'otp12-gate-b-records';
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
                const trust = await MemoryTrust.withActivePeer(clientIdentity, phoneIdentity);
                const phone = startPhone({ seed, main: 'gate-b' });
                try {
                    const peerId = hexOf(phoneIdentity.installationId);
                    const buildEngine = () =>
                        newClientEngine(
                            trust.engineDeps(peerId, {
                                handler: {
                                    handles: (type) =>
                                        type === RecordType.PULL_EVENTS ||
                                        type === RecordType.PUSH_EVENT,
                                    handle: async () => undefined,
                                },
                            }),
                        );
                    const bundle = await buildEngine();
                    const session = await openSession(phone, bundle, null);
                    expect(bundle.engine.sessionClass).toBe(SessionClass.FULL);
                    expect(session.prefix).toHaveLength(8);
                    const driver = new ProtectedRecordsDriver(
                        phone,
                        bundle.engine,
                        phoneIdentity.installationId,
                        buildEngine,
                    );
                    await runJourney(loadJourney('protected-records'), driver);
                } finally {
                    phone.stop();
                }
            },
            TIMEOUT_MS,
        );
    });
}
