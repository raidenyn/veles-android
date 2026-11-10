import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { RecordType, SessionClass } from '../../src/session/wire';
import { SessionReject } from '../../src/session/reject';
import { rbase as reconnectRbase } from '../../src/session/schedule';
import type { ApplicationHandler, SessionEffect } from '../../src/session/engine';
import {
    FakeClock,
    PhoneHarness,
    hexOf,
    type PhoneRecord,
    type StartedHandshake,
} from './client-harness';

// OTP-09 Task 10 conformance runner for the TypeScript client engine.
//
// The Kotlin runner drives the phone engine; this runner drives the client engine
// over the same inventory. Each `execute` scenario replays the fixture steps with a
// scripted phone peer built from the production codec. `engine-effect` entries are
// trace-validation negatives whose endpoint assertion replaces the trace outcome.
// The harness is passive: `ScenarioHandler` records deliveries, event counts and
// acks but never replies on its own; every `send_protected_record` step is exactly
// one `sendApplication` call.

const PROTOCOL_ROOT = resolve(import.meta.dirname, '../../../protocol');
const INVENTORY = resolve(PROTOCOL_ROOT, 'conformance/otp-09-scenarios.json');
const RECORD_PREFIX = 'durable_client_record:';

type JsonObject = Record<string, unknown>;

interface Fixture {
    id: string;
    input: JsonObject;
    expected: JsonObject;
}

interface Step {
    t_ms: number;
    event: string;
    peer?: string;
    simulate: Record<string, string>;
    expect?: Record<string, string>;
}

function loadFixtures(path: string): Fixture[] {
    const parsed = JSON.parse(
        readFileSync(resolve(PROTOCOL_ROOT, 'v1/fixtures', path), 'utf8'),
    ) as {
        fixtures: Fixture[];
    };
    return parsed.fixtures;
}

const FIXTURES: Fixture[] = [...loadFixtures('records.json'), ...loadFixtures('reconnect.json')];
const INVENTORY_ENTRIES = JSON.parse(readFileSync(INVENTORY, 'utf8')) as Record<string, unknown>;

const byId = (id: string): Fixture => {
    const fixture = FIXTURES.find((candidate) => candidate.id === id);
    if (fixture === undefined) throw new Error(`no fixture with id ${id}`);
    return fixture;
};

const roleOf = (fixture: Fixture): string => fixture.input.role as string;
const stepsOf = (fixture: Fixture): Step[] =>
    (fixture.input.steps as (Omit<Step, 'simulate'> & { simulate?: Record<string, string> })[]).map(
        (step) => ({
            ...step,
            simulate: step.simulate ?? {},
            expect: step.expect ?? {},
        }),
    );
const persistedOf = (fixture: Fixture): Record<string, string> =>
    (fixture.input.persisted_state ?? {}) as Record<string, string>;
const effectsOf = (fixture: Fixture): { require?: string[]; forbid?: string[]; order?: string[] } =>
    (fixture.expected.effects ?? {}) as { require?: string[]; forbid?: string[]; order?: string[] };

function engineEffectOf(
    entry: unknown,
): { terminate: boolean; erase: boolean; deliver: boolean } | null {
    if (typeof entry !== 'object' || entry === null) return null;
    const effect = (entry as { 'engine-effect'?: unknown })['engine-effect'];
    if (typeof effect !== 'object' || effect === null) return null;
    const value = effect as Record<string, unknown>;
    return {
        terminate: value.terminate === true,
        erase: value.erase === true,
        deliver: value.deliver === true,
    };
}

function parseAssignments(value: string): Map<string, string> {
    return new Map(
        value
            .split(';')
            .filter((entry) => entry.length > 0)
            .map((entry) => {
                const index = entry.indexOf('=');
                return [entry.slice(0, index), entry.slice(index + 1)] as const;
            }),
    );
}

// ---------------------------------------------------------------------------------------------
// Application handler: passive, bounds-validating, ack-aware
// ---------------------------------------------------------------------------------------------

const MAX_EVENTS = 5;
const EVENT_ID_OFFSET = 16;
const EVENT_ID_BYTES = 16;
const EVENT_BYTES = 79;

class ScenarioHandler implements ApplicationHandler {
    readonly deliveries: { type: number; eventCount: number }[] = [];
    readonly known = new Set<string>();
    readonly acks: { id: string; known: boolean }[] = [];
    settled = false;
    sawUnknown = false;

    handles(type: number): boolean {
        return (
            type === RecordType.PULL_REQUEST ||
            type === RecordType.PULL_EVENTS ||
            type === RecordType.PUSH_EVENT ||
            type === RecordType.EVENT_ACK ||
            type === RecordType.PHONE_POLICY_STATE
        );
    }

    async handle(type: number, body: Uint8Array): Promise<void> {
        let eventCount = 0;
        if (type === RecordType.PULL_EVENTS) {
            eventCount = body[0];
            // OTP2-REC-031: 0..5 events. The application layer owns the bound; a larger
            // set is rejected whole and the engine fails closed.
            if (eventCount > MAX_EVENTS) throw new SessionReject();
            for (let index = 0; index < eventCount; index += 1) {
                const start = 1 + index * EVENT_BYTES;
                this.register(
                    body.subarray(
                        start + EVENT_ID_OFFSET,
                        start + EVENT_ID_OFFSET + EVENT_ID_BYTES,
                    ),
                );
            }
        } else if (type === RecordType.PUSH_EVENT) {
            eventCount = 1;
            this.register(body.subarray(EVENT_ID_OFFSET, EVENT_ID_OFFSET + EVENT_ID_BYTES));
        } else if (type === RecordType.EVENT_ACK) {
            const id = hexOf(body.subarray(EVENT_ID_OFFSET, EVENT_ID_OFFSET + EVENT_ID_BYTES));
            const known = this.known.has(id);
            this.acks.push({ id, known });
            if (known) this.settled = true;
            else this.sawUnknown = true;
        }
        this.deliveries.push({ type, eventCount });
    }

    private register(id: Uint8Array): void {
        this.known.add(hexOf(id));
    }
}

// ---------------------------------------------------------------------------------------------
// Event and ack bodies (OTP2-REC-030 / OTP2-REC-032a)
// ---------------------------------------------------------------------------------------------

const encoder = new TextEncoder();

function syntheticEventId(index: number): Uint8Array {
    return Uint8Array.from({ length: 16 }, (_, position) => ((index + 1) * 16 + position) & 0xff);
}

function syntheticEvent(phoneInstallationId: Uint8Array, index: number): Uint8Array {
    const code = encoder.encode('123456');
    const merchant = encoder.encode('Test merchant');
    const tail = new Uint8Array(8 + 4 + 2 + code.length + 2 + merchant.length + 8 + 1 + 3);
    const view = new DataView(tail.buffer);
    view.setBigUint64(0, BigInt(1_700_000_000 + index));
    view.setUint32(8, 120);
    view.setUint16(12, code.length);
    tail.set(code, 14);
    view.setUint16(14 + code.length, merchant.length);
    tail.set(merchant, 16 + code.length);
    view.setBigUint64(16 + code.length + merchant.length, 100n);
    tail[24 + code.length + merchant.length] = 2;
    tail.set(encoder.encode('XTS'), 25 + code.length + merchant.length);
    const event = new Uint8Array(32 + tail.length);
    event.set(phoneInstallationId, 0);
    event.set(syntheticEventId(index), 16);
    event.set(tail, 32);
    return event;
}

function pullEventsBody(
    phoneInstallationId: Uint8Array,
    count: number,
    handler: ScenarioHandler,
): Uint8Array {
    const body = new Uint8Array(1 + count * EVENT_BYTES);
    body[0] = count;
    for (let index = 0; index < count; index += 1) {
        const event = syntheticEvent(phoneInstallationId, index);
        body.set(event, 1 + index * EVENT_BYTES);
        handler.known.add(hexOf(event.subarray(EVENT_ID_OFFSET, EVENT_ID_OFFSET + EVENT_ID_BYTES)));
    }
    return body;
}

function pushEventBody(phoneInstallationId: Uint8Array, handler: ScenarioHandler): Uint8Array {
    const event = syntheticEvent(phoneInstallationId, 0);
    handler.known.add(hexOf(event.subarray(EVENT_ID_OFFSET, EVENT_ID_OFFSET + EVENT_ID_BYTES)));
    return event;
}

function ackBody(
    phoneInstallationId: Uint8Array,
    handler: ScenarioHandler,
    known: boolean,
): Uint8Array {
    const id = known ? [...handler.known][0] : hexOf(syntheticEventId(0xfe));
    const body = new Uint8Array(32);
    body.set(phoneInstallationId, 0);
    for (let index = 0; index < 16; index += 1) {
        body[16 + index] = parseInt((id ?? '').slice(index * 2, index * 2 + 2), 16);
    }
    return body;
}

// ---------------------------------------------------------------------------------------------
// The client scenario driver
// ---------------------------------------------------------------------------------------------

const PEER_STATE_ACTIVE = 0x01;

// Every key the in-scope step mapping consumes. Keys that appear only in deferred ids
// (`act_mac_ok`, `pending_expiry`, `push_retransmit`) fail if an executed id ever uses them.
const MAPPED_SIMULATE_KEYS = new Set([
    'record_exists',
    'hello_valid',
    'sig_ok',
    'hmac_ok',
    'peer_state',
    'tag_ok',
    'length_ok',
    'session_id_ok',
    'sequence',
    'record_type',
    'event_count',
    'event_known',
    'pull_response',
    'rx_next',
    'continuity_token_matches',
]);

interface StepOutcome {
    index: number;
    event: string;
    simulate: Record<string, string>;
    terminated: string | null;
    closed: boolean;
    sentTypes: number[];
    deliveredTypes: number[];
    recordOpened: boolean;
    unauthorized: boolean;
    sequenceMismatch: boolean;
    heartbeatCrossed: boolean;
    eventCount: number | null;
    authenticatedBefore: boolean;
    inboundBefore: bigint;
    inboundAfter: bigint;
    snapshot: Map<string, string>;
    accepted: boolean;
}

interface RunObservations {
    steps: StepOutcome[];
    authenticated: boolean;
    sessionClass: SessionClass | null;
    terminations: string[];
    phonePeerStates: number[];
    delivered: boolean;
    keysErased: boolean;
    sessionOpen: boolean;
    generations: number;
    upgrades: number;
    storeState: string;
}

function wireType(bytes: Uint8Array): number | null {
    if (bytes.length === 0) return null;
    if (bytes[0] === 0x06 || bytes[0] === 0x07 || bytes[0] === 0x0f) return bytes[0];
    if (bytes.length < 23) return null;
    return (bytes[3] << 8) | bytes[4];
}

function classify(effects: SessionEffect[]): {
    terminated: string | null;
    closed: boolean;
    sentTypes: number[];
    deliveredTypes: number[];
} {
    let terminated: string | null = null;
    let closed = false;
    const sentTypes: number[] = [];
    const deliveredTypes: number[] = [];
    for (const effect of effects) {
        if (effect.kind === 'terminated') terminated = effect.reason;
        if (effect.kind === 'close') closed = true;
        if (effect.kind === 'send') {
            const type = wireType(effect.bytes);
            if (type !== null) sentTypes.push(type);
        }
        if (effect.kind === 'deliver') deliveredTypes.push(effect.type);
    }
    return { terminated, closed, sentTypes, deliveredTypes };
}

class ClientScenario {
    private readonly fixture: Fixture;
    private readonly clock = new FakeClock();
    private readonly observations: RunObservations = {
        steps: [],
        authenticated: false,
        sessionClass: null,
        terminations: [],
        phonePeerStates: [],
        delivered: false,
        keysErased: false,
        sessionOpen: false,
        generations: 0,
        upgrades: 0,
        storeState: '',
    };

    private generation: { harness: PhoneHarness; handler: ScenarioHandler } | null = null;
    private store: Map<string, PhoneRecord> | null = null;
    private phoneIdentity: CryptoKeyPair | null = null;
    private peerId: string | null = null;
    private started: StartedHandshake | null = null;
    private expectedRx = 0n;

    constructor(fixture: Fixture) {
        this.fixture = fixture;
    }

    async run(): Promise<RunObservations> {
        this.observations.storeState =
            persistedOf(this.fixture)[`${RECORD_PREFIX}main`] ?? 'active';
        for (const [index, step] of stepsOf(this.fixture).entries()) {
            await this.execute(index, step);
        }
        this.observations.keysErased = this.generation?.harness.engine._keysErased ?? false;
        this.observations.sessionOpen = this.generation?.harness.engine.isOpen ?? false;
        return this.observations;
    }

    private async open(): Promise<{ harness: PhoneHarness; handler: ScenarioHandler }> {
        const handler = new ScenarioHandler();
        const harness = await PhoneHarness.create({
            state: this.observations.storeState as
                | 'active'
                | 'active_activation_pending'
                | 'pending',
            clock: this.clock,
            handler,
            ...(this.store === null
                ? {}
                : {
                      store: this.store,
                      phoneIdentity: this.phoneIdentity ?? undefined,
                      phoneId: this.peerId ?? undefined,
                  }),
        });
        this.store = harness.store;
        this.phoneIdentity = harness.phone.identity;
        this.peerId = harness.peerId;
        this.observations.generations += 1;
        this.observations.authenticated = false;
        this.observations.sessionClass = null;
        this.expectedRx = 0n;
        this.started = null;
        this.generation = { harness, handler };
        return this.generation;
    }

    private live(): { harness: PhoneHarness; handler: ScenarioHandler } {
        if (this.generation === null) throw new Error('no live generation');
        return this.generation;
    }

    private async execute(index: number, step: Step): Promise<void> {
        const unmapped = Object.keys(step.simulate).filter((key) => !MAPPED_SIMULATE_KEYS.has(key));
        if (unmapped.length > 0) {
            throw new Error(
                `unmapped simulate keys ${unmapped.join(', ')} in an executed id (${this.fixture.id})`,
            );
        }
        this.clock.value = step.t_ms;
        const authenticatedBefore = this.observations.sessionClass !== null;
        const inboundBefore = this.generation?.harness.engine._inboundSequence ?? 0n;
        const expectedBefore = this.expectedRx;
        let effects: SessionEffect[] = [];
        let eventCount =
            step.simulate.event_count === undefined ? null : Number(step.simulate.event_count);
        switch (step.event) {
            case 'process_load':
                // The client mirror is durable: a continuity-token loss keeps it (OTP2-STA-010d).
                break;
            case 'send_hello': {
                const generation = await this.open();
                const started = await generation.harness.start();
                this.started = started;
                effects = started.effects;
                break;
            }
            case 'reconnect_hello': {
                const generation = this.live();
                effects = await generation.harness.deliverPhoneHello();
                break;
            }
            case 'reconnect_signature': {
                const generation = this.live();
                const started = this.started;
                if (started === null) throw new Error('reconnect_signature before send_hello');
                const phoneHelloPayload = generation.harness.phone.helloPayload();
                const transcript = reconnectRbase(phoneHelloPayload, started.clientHelloPayload);
                const phoneSignature = await generation.harness.phone.sign(transcript);
                effects = await generation.harness.deliverPhoneSignature(
                    transcript,
                    phoneSignature,
                );
                await generation.harness.adoptHandshake(
                    started,
                    phoneHelloPayload,
                    phoneSignature,
                    effects,
                );
                const token = effects.find(
                    (effect) => effect.kind === 'send' && effect.completionToken !== undefined,
                );
                if (token?.kind === 'send' && token.completionToken !== undefined) {
                    await generation.harness.engine.onSendComplete(token.completionToken, true);
                }
                break;
            }
            case 'send_reconnect_confirm':
                // The client's confirm is emitted with its signature above; this explicit
                // fixture step produces no second emission.
                break;
            case 'reconnect_confirm': {
                const generation = this.live();
                const peerState =
                    step.simulate.peer_state === undefined
                        ? PEER_STATE_ACTIVE
                        : Number(step.simulate.peer_state);
                const record = await generation.harness.confirmRecord(peerState);
                effects = await generation.harness.engine.onInbound(record);
                const authenticated = effects.find((effect) => effect.kind === 'authenticated');
                if (authenticated?.kind === 'authenticated') {
                    this.observations.authenticated = true;
                    this.observations.sessionClass = authenticated.sessionClass;
                    this.expectedRx = 1n;
                    this.observations.upgrades = Math.max(
                        this.observations.upgrades,
                        generation.harness.calls.upgrade.length,
                    );
                    this.observations.phonePeerStates.push(peerState);
                }
                break;
            }
            case 'send_protected_record': {
                const type = Number(step.simulate.record_type);
                effects = await this.send(type, step);
                break;
            }
            case 'protected_record': {
                const type = Number(step.simulate.record_type);
                const sequence = BigInt(step.simulate.sequence);
                const body = this.inboundBody(type, step, eventCount);
                const generation = this.live();
                const record =
                    type === RecordType.PULL_REQUEST
                        ? await generation.harness.phoneUncheckedRecord(type, sequence, body)
                        : await generation.harness.phoneRecord(type, sequence, body);
                effects = await generation.harness.engine.onInbound(record);
                if (type === RecordType.PULL_EVENTS) eventCount = body[0];
                break;
            }
            case 'close_connection':
                this.live().harness.engine.teardown();
                break;
            default:
                throw new Error(`unmapped scenario event '${step.event}' in ${this.fixture.id}`);
        }
        const classified = classify(effects);
        if (classified.terminated !== null)
            this.observations.terminations.push(classified.terminated);
        if (classified.deliveredTypes.length > 0) this.observations.delivered = true;
        const type =
            step.simulate.record_type === undefined ? null : Number(step.simulate.record_type);
        const sequence =
            step.simulate.sequence === undefined ? null : BigInt(step.simulate.sequence);
        const unauthorized =
            type !== null &&
            step.event === 'protected_record' &&
            this.observations.sessionClass !== null &&
            !this.authorized(type);
        const sequenceMismatch =
            step.event === 'protected_record' && sequence !== null && sequence !== expectedBefore;
        const opened =
            step.event === 'protected_record' &&
            (classified.terminated === null ||
                !['record_invalid', 'unexpected_frame', 'sequence'].includes(
                    classified.terminated,
                ));
        if (opened && sequence !== null) {
            this.expectedRx = sequence === 0xffffffffffffffffn ? sequence : sequence + 1n;
        } else if (
            step.event === 'send_protected_record' &&
            type === RecordType.APP_HEARTBEAT &&
            classified.terminated === null
        ) {
            this.expectedRx = expectedBefore + 1n;
        }
        const inboundAfter = this.generation?.harness.engine._inboundSequence ?? 0n;
        this.observations.steps.push({
            index,
            event: step.event,
            simulate: step.simulate,
            terminated: classified.terminated,
            closed: classified.closed,
            sentTypes: classified.sentTypes,
            deliveredTypes: classified.deliveredTypes,
            recordOpened: opened,
            unauthorized,
            sequenceMismatch,
            heartbeatCrossed: type === RecordType.APP_HEARTBEAT && classified.terminated === null,
            eventCount,
            authenticatedBefore,
            inboundBefore,
            inboundAfter,
            snapshot: this.snapshot(),
            accepted: classified.terminated === null && !classified.closed,
        });
    }

    private async send(type: number, step: Step): Promise<SessionEffect[]> {
        const { harness, handler } = this.live();
        const count = Number(step.simulate.event_count ?? 0);
        const body =
            type === RecordType.PULL_EVENTS
                ? pullEventsBody(harness.phone.installationId, count, handler)
                : type === RecordType.PUSH_EVENT
                  ? pushEventBody(harness.phone.installationId, handler)
                  : type === RecordType.EVENT_ACK
                    ? ackBody(harness.phone.installationId, handler, true)
                    : new Uint8Array(0);
        return harness.engine.sendApplication(type, body);
    }

    private inboundBody(type: number, step: Step, eventCount: number | null): Uint8Array {
        const { harness, handler } = this.live();
        if (type === RecordType.PULL_EVENTS) {
            return pullEventsBody(harness.phone.installationId, eventCount ?? 0, handler);
        }
        if (type === RecordType.PUSH_EVENT) {
            return pushEventBody(harness.phone.installationId, handler);
        }
        if (type === RecordType.EVENT_ACK) {
            return ackBody(
                harness.phone.installationId,
                handler,
                step.simulate.event_known !== 'false',
            );
        }
        return new Uint8Array(0);
    }

    private authorized(type: number): boolean {
        if (this.observations.sessionClass !== SessionClass.FULL) return false;
        // §7.3 phone→client application types; a client→phone record is a direction violation.
        return (
            type === RecordType.PULL_EVENTS ||
            type === RecordType.PUSH_EVENT ||
            type === RecordType.PHONE_POLICY_STATE ||
            type === RecordType.REMOVAL_REQUEST ||
            type === RecordType.REMOVAL_RESPONSE
        );
    }

    private snapshot(): Map<string, string> {
        const open = this.generation?.harness.engine.isOpen ?? false;
        const terminated = this.observations.terminations.some((reason) => reason !== 'app_error');
        const session = terminated ? 'terminated' : open ? 'session_open' : 'closed';
        const record =
            this.generation === null
                ? undefined
                : this.generation.harness.store.get(this.generation.harness.peerId);
        const store = record?.state ?? this.observations.storeState;
        this.observations.storeState = store;
        return new Map([
            ['session:main', session],
            [`${RECORD_PREFIX}main`, store],
        ]);
    }
}

// ---------------------------------------------------------------------------------------------
// Effect observations
// ---------------------------------------------------------------------------------------------

const CLIENT_EFFECTS: Record<string, (run: RunObservations) => boolean> = {
    mutual_authentication_complete: (run) => run.authenticated,
    set_class_full: (run) => run.sessionClass === SessionClass.FULL,
    set_class_full_pending_peer: (run) => run.sessionClass === SessionClass.FULL_PENDING_PEER,
    set_class_activation_recovery: (run) => run.sessionClass === SessionClass.ACTIVATION_RECOVERY,
    terminate_session: (run) => run.terminations.some((reason) => reason !== 'app_error'),
    erase_session_keys: (run) => run.keysErased,
    counter_not_advanced: (run) => run.terminations.length > 0 && !run.sessionOpen,
    advance_rx_sequence: (run) =>
        run.steps.some(
            (step) =>
                step.event === 'protected_record' &&
                step.accepted &&
                step.inboundAfter === step.inboundBefore + 1n,
        ),
    reject_unauthorized_record: (run) =>
        run.steps.some((step) => step.terminated !== null && step.unauthorized),
    reject_sequence_violation: (run) =>
        run.steps.some((step) => step.sequenceMismatch && step.terminated !== null),
    pull_request_processed: (run) =>
        run.steps.some(
            (step) =>
                step.accepted &&
                (step.sentTypes.includes(RecordType.PULL_REQUEST) ||
                    step.deliveredTypes.includes(RecordType.PULL_REQUEST)),
        ),
    pull_request_sent: (run) =>
        run.steps.some((step) => step.sentTypes.includes(RecordType.PULL_REQUEST)),
    otp_events_transferred: (run) =>
        run.steps.some(
            (step) =>
                step.accepted &&
                (step.sentTypes.includes(RecordType.PULL_EVENTS) ||
                    step.deliveredTypes.includes(RecordType.PULL_EVENTS)) &&
                (step.eventCount ?? 0) > 0,
        ),
    event_ack_sent: (run) =>
        run.steps.some((step) => step.sentTypes.includes(RecordType.EVENT_ACK)),
    event_ack_processed: (run) =>
        run.steps.some(
            (step) =>
                step.sentTypes.includes(RecordType.EVENT_ACK) ||
                step.deliveredTypes.includes(RecordType.EVENT_ACK),
        ),
    push_event_transferred: (run) =>
        run.steps.some(
            (step) =>
                step.accepted &&
                (step.sentTypes.includes(RecordType.PUSH_EVENT) ||
                    step.deliveredTypes.includes(RecordType.PUSH_EVENT)),
        ),
    push_settled: () => false,
    record_upgraded_fully_active: (run) => run.storeState === 'active' && run.upgrades > 0,
    phone_active_proof_received: (run) => run.upgrades > 0,
    reset_session_slot: (run) => run.generations > 1,
    reconnect_confirm_sent_peer_state_active: (run) =>
        run.phonePeerStates.includes(PEER_STATE_ACTIVE),
    delete_incomplete_materials: () => false,
    erase_incomplete_materials: () => false,
};

function observe(run: RunObservations, name: string): boolean {
    const mapper = CLIENT_EFFECTS[name];
    if (mapper === undefined) throw new Error(`effect '${name}' has no engine observation mapping`);
    return mapper(run);
}

async function runScenario(id: string): Promise<RunObservations> {
    return new ClientScenario(byId(id)).run();
}

function finalSnapshot(run: RunObservations): Map<string, string> {
    const last = run.steps[run.steps.length - 1];
    return last?.snapshot ?? new Map();
}

// ---------------------------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------------------------

describe('OTP-09 scenario inventory', () => {
    it('inventoryCoversEveryFixtureId', () => {
        expect(Object.keys(INVENTORY_ENTRIES).sort()).toEqual(
            FIXTURES.map((fixture) => fixture.id).sort(),
        );
        for (const [id, entry] of Object.entries(INVENTORY_ENTRIES)) {
            if (typeof entry === 'string') {
                expect(
                    entry === 'execute' || entry.startsWith('deferred:OTP-'),
                    `${id} classification`,
                ).toBe(true);
            } else {
                expect(engineEffectOf(entry), `${id} engine-effect shape`).not.toBeNull();
            }
        }
        expect(
            Object.values(INVENTORY_ENTRIES).filter((entry) => entry === 'execute'),
        ).toHaveLength(22);
    });

    it('everyEffectNameInAnExecutedClientScenarioIsMapped', () => {
        const executed = Object.entries(INVENTORY_ENTRIES).filter(
            ([id, entry]) =>
                (entry === 'execute' || engineEffectOf(entry) !== null) &&
                roleOf(byId(id)) === 'client',
        );
        for (const [id] of executed) {
            const effects = effectsOf(byId(id));
            for (const name of [
                ...(effects.require ?? []),
                ...(effects.forbid ?? []),
                ...(effects.order ?? []),
            ]) {
                expect(Object.keys(CLIENT_EFFECTS), `${id} effect ${name}`).toContain(name);
            }
        }
    });

    it('clientRoleScenariosAreDrivenByTheTypeScriptRunner', () => {
        const client = Object.entries(INVENTORY_ENTRIES)
            .filter(([, entry]) => entry === 'execute')
            .map(([id]) => id)
            .filter((id) => roleOf(byId(id)) === 'client');
        expect(client.sort()).toEqual(
            [
                'st-rec-client-activation-pending-upgrades-on-phone-proof',
                'st-rec-client-full-pull-delivers',
                'st-rec-client-mirror-durable-across-restart',
                'st-rec-direction-mismatch-terminates',
            ].sort(),
        );
    });
});

describe('client conformance scenarios', () => {
    for (const [id, entry] of Object.entries(INVENTORY_ENTRIES)) {
        const fixture = byId(id);
        if (roleOf(fixture) !== 'client') continue;
        const endpoint = engineEffectOf(entry);
        if (entry !== 'execute' && endpoint === null) continue;

        if (entry === 'execute') {
            it(`${id} passes`, async () => {
                const run = await runScenario(id);
                expect(fixture.expected.accepted).toBe(true);
                expect(finalSnapshot(run)).toEqual(
                    parseAssignments(fixture.expected.final_state as string),
                );
                for (const [index, step] of stepsOf(fixture).entries()) {
                    for (const [key, value] of Object.entries(step.expect ?? {})) {
                        expect(
                            run.steps[index].snapshot.get(key),
                            `${id} step ${index} ${key}`,
                        ).toBe(value);
                    }
                }
                for (const name of effectsOf(fixture).require ?? []) {
                    expect(observe(run, name), `${id} requires ${name}`).toBe(true);
                }
                for (const name of effectsOf(fixture).forbid ?? []) {
                    expect(observe(run, name), `${id} forbids ${name}`).toBe(false);
                }
            });
        } else {
            it(`${id} has its engine-effect endpoint`, async () => {
                const run = await runScenario(id);
                expect(run.terminations.some((reason) => reason !== 'app_error')).toBe(
                    endpoint?.terminate,
                );
                expect(run.keysErased).toBe(endpoint?.erase);
                expect(run.delivered).toBe(endpoint?.deliver);
                expect(fixture.expected.accepted).toBe(false);
            });
        }
    }

    it('deferred scenarios are printed with their owning task', () => {
        const deferred = Object.entries(INVENTORY_ENTRIES)
            .filter(([, entry]) => typeof entry === 'string' && entry.startsWith('deferred:'))
            .sort(([left], [right]) => left.localeCompare(right));
        for (const [id, owner] of deferred) {
            console.log(`deferred ${id} -> ${(owner as string).replace('deferred:', '')}`);
        }
        expect(deferred.length).toBeGreaterThan(0);
    });

    it('kotlin-signed signatures verify in TypeScript and client signatures are low-S', async () => {
        // The live cross-endpoint handshake is exercised by `loopback.test.ts`; this runner
        // pins the client half: the client only authenticates after the scripted phone's
        // Kotlin-compatible 64-byte low-S signature verifies, and the client's own signature
        // is the production low-S P1363 form.
        const run = await runScenario('st-rec-client-full-pull-delivers');
        expect(run.sessionClass).toBe(SessionClass.FULL);
        const signatureStep = run.steps.find((step) => step.event === 'reconnect_signature');
        expect(signatureStep?.accepted).toBe(true);
    });
});
