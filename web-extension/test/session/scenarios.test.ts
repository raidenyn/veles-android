import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { Direction, RecordType, SessionClass, recordTypeFromWire } from '../../src/session/wire';
import { SessionReject } from '../../src/session/reject';
import {
    confirmation,
    concat,
    hmacSha256,
    rbase as reconnectRbase,
} from '../../src/session/schedule';
import { RECORD_HEADER_BYTES, RECORD_MAX_BYTES } from '../../src/session/record';
import type { ApplicationHandler, SessionEffect } from '../../src/session/engine';
import { ActivationEngine, composeHandlers } from '../../src/pairing/activation';
import { ProofRegistry } from '../../src/pairing/proofs';
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
// scripted phone peer built from the production codec. `engine-effect` entries carry
// an explicit endpoint assertion instead of the trace outcome.
//
// The harness is passive: `ScenarioHandler` records deliveries, event counts and
// acks but never replies on its own; every `send_protected_record` step is exactly
// one `sendApplication` call. Every simulated flag the inventory allows is applied
// to real bytes (hello caps, signature, confirm HMAC, tag, 4097-byte record, session
// prefix, policy flag) and the engine's bounded rejection reasons are observed.

const PROTOCOL_ROOT = resolve(import.meta.dirname, '../../../protocol');
const INVENTORY = resolve(PROTOCOL_ROOT, 'conformance/otp-09-scenarios.json');
const OTP10_INVENTORY = resolve(PROTOCOL_ROOT, 'conformance/otp-10-scenarios.json');
const RECORD_PREFIX = 'durable_client_record:';
const EVENT_BYTES = 79;
const RECORD_TAG_BYTES = 16;

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
const ENROLLMENT_FIXTURES: Fixture[] = loadFixtures('enrollment.json');
const INVENTORY_ENTRIES = readInventory(INVENTORY);
const OTP10_INVENTORY_ENTRIES = readInventory(OTP10_INVENTORY);
/** An OTP-10 entry of an id overrides the OTP-09 entry of the same id. */
const EFFECTIVE_INVENTORY: Record<string, unknown> = {
    ...INVENTORY_ENTRIES,
    ...OTP10_INVENTORY_ENTRIES,
};
const SESSION_IDS = new Set(FIXTURES.map((fixture) => fixture.id));
const ENROLLMENT_IDS = new Set(ENROLLMENT_FIXTURES.map((fixture) => fixture.id));
/** The OTP-09 runner executes `records.json`/`reconnect.json` ids only. */
const EXECUTION_INVENTORY: Record<string, unknown> = Object.fromEntries(
    Object.entries(EFFECTIVE_INVENTORY).filter(([id]) => SESSION_IDS.has(id)),
);
/** The five trace-validation self-checks stay with the OTP-02 fixture runner. */
const FIXTURE_SELF_CHECK_IDS = [
    'st-neg-unknown-event',
    'st-neg-nonmonotonic-time',
    'st-neg-timeout-early',
    'st-neg-unknown-machine',
    'st-neg-step-expect-wrong',
];
/** The OTP-09 inventory defers implementation work to a later change. */
const OTP09_DEFERRAL = /^deferred:OTP-[0-9]+(?:\/[0-9]+)?$/;
/** The OTP-10 inventory defers a scenario to the plan task that will execute it. */
const OTP10_DEFERRAL = /^deferred:Task [0-9]+$/;

function readInventory(path: string): Record<string, unknown> {
    return JSON.parse(readFileSync(path, 'utf8')) as Record<string, unknown>;
}

function classificationOf(entry: unknown, deferral: RegExp): boolean {
    if (typeof entry === 'string') return entry === 'execute' || deferral.test(entry);
    return engineEffectOf(entry) !== null;
}

const byId = (id: string): Fixture => {
    const fixture = FIXTURES.find((candidate) => candidate.id === id);
    if (fixture === undefined) throw new Error(`no fixture with id ${id}`);
    return fixture;
};

const roleOf = (fixture: Fixture): string => fixture.input.role as string;
const stepsOf = (fixture: Fixture): Step[] =>
    (
        fixture.input.steps as (Omit<Step, 'simulate'> & { simulate?: Record<string, unknown> })[]
    ).map((step) => ({
        ...step,
        // The fixtures carry JSON booleans and numbers; normalise every simulated value to
        // its literal text once so a `false` flag can never be mistaken for a truthy value.
        simulate: Object.fromEntries(
            Object.entries(step.simulate ?? {}).map(([key, value]) => [key, String(value)]),
        ),
        expect: step.expect ?? {},
    }));
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
// Event, ack and policy bodies (OTP2-REC-030 / -031 / -032a)
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

/** `policy` u8 (`0` unlocked-only) then `flags` u8 with bit3 `response_to_pull`. */
function policyBody(responseToPull: boolean): Uint8Array {
    return Uint8Array.of(0, responseToPull ? 0x08 : 0x00);
}

// ---------------------------------------------------------------------------------------------
// Raw phone→client record builder with the simulated byte mutations
// ---------------------------------------------------------------------------------------------

function writeU64(value: Uint8Array, offset: number, sequence: bigint): void {
    let remaining = sequence;
    for (let index = offset + 7; index >= offset; index -= 1) {
        value[index] = Number(remaining & 0xffn);
        remaining >>= 8n;
    }
}

function concatBytes(...parts: Uint8Array[]): Uint8Array {
    let length = 0;
    for (const part of parts) length += part.length;
    const joined = new Uint8Array(length);
    let offset = 0;
    for (const part of parts) {
        joined.set(part, offset);
        offset += part.length;
    }
    return joined;
}

function foreignPrefix(prefix: Uint8Array): Uint8Array {
    const copy = new Uint8Array(prefix);
    copy[0] ^= 0xff;
    return copy;
}

interface RecordMutations {
    tagOk?: boolean;
    lengthOk?: boolean;
    sessionIdOk?: boolean;
}

async function sealPhoneRecord(
    harness: PhoneHarness,
    type: number,
    sequence: bigint,
    body: Uint8Array,
    mutations: RecordMutations = {},
): Promise<Uint8Array> {
    const keys = harness.keys;
    if (keys === null) throw new Error('no session keys');
    const recordBody =
        mutations.lengthOk === false
            ? new Uint8Array(RECORD_MAX_BYTES + 1 - RECORD_HEADER_BYTES - RECORD_TAG_BYTES)
            : body;
    const header = new Uint8Array(RECORD_HEADER_BYTES);
    header[0] = 1;
    header[1] = 1;
    header[2] = Direction.PHONE_TO_CLIENT;
    header[3] = (type >>> 8) & 0xff;
    header[4] = type & 0xff;
    writeU64(header, 5, sequence);
    header[13] = (recordBody.length >>> 8) & 0xff;
    header[14] = recordBody.length & 0xff;
    header.set(
        mutations.sessionIdOk === false ? foreignPrefix(harness.prefix) : harness.prefix,
        15,
    );
    const nonce = new Uint8Array(12);
    nonce.set(keys.p2cPrefix, 0);
    writeU64(nonce, 4, sequence);
    const sealed = new Uint8Array(
        await crypto.subtle.encrypt(
            {
                name: 'AES-GCM',
                iv: nonce,
                additionalData: header,
                tagLength: 128,
            },
            keys.p2cKey,
            recordBody,
        ),
    );
    const record = concatBytes(header, sealed);
    if (mutations.tagOk === false) record[record.length - 1] ^= 0x01;
    return record;
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
    'continuity_token_matches',
    'act_mac_ok',
]);

interface StepOutcome {
    index: number;
    event: string;
    simulate: Record<string, string>;
    terminated: string | null;
    closed: boolean;
    sentTypes: number[];
    deliveredTypes: number[];
    /** Closed with the generic client failure and sent no frame (OTP2-REV-001). */
    genericAbort: boolean;
    unauthorized: boolean;
    unknownType: boolean;
    oversize: boolean;
    recordOpened: boolean;
    sequenceMismatch: boolean;
    heartbeatCrossed: boolean;
    eventCount: number | null;
    authenticatedBefore: boolean;
    expectedRxBefore: bigint;
    inboundBefore: bigint;
    inboundAfter: bigint;
    outboundBefore: bigint;
    inboundAtTeardown: bigint;
    outboundAtTeardown: bigint;
    snapshot: Map<string, string>;
    accepted: boolean;
}

interface RunObservations {
    steps: StepOutcome[];
    authenticated: boolean;
    sessionClass: SessionClass | null;
    /** Every class fixed at a gate in this fixture, across reconnects. */
    sessionClasses: SessionClass[];
    terminations: string[];
    /** Peer byte decoded from the client's own emitted reconnect_confirm records. */
    clientConfirmPeerStates: number[];
    delivered: boolean;
    keysErased: boolean;
    sessionOpen: boolean;
    generations: number;
    /** Repository instances opened from durable storage (initial load + reloads). */
    loads: number;
    reloads: number;
    continuityMatched: boolean;
    continuityLost: boolean;
    deletedIncomplete: boolean;
    upgrades: number;
    storeState: string;
    /** State the repository loaded from durable storage before any session upgrade. */
    reloadedState: string;
    durableState: string;
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
    genericAbort: boolean;
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
    // `preauth_reject` is phone→client only: the client's abort is a bare close with no send.
    const genericAbort = closed && terminated === null && sentTypes.length === 0;
    return { terminated, closed, sentTypes, deliveredTypes, genericAbort };
}

type PersistedState = 'active' | 'active_activation_pending' | 'pending' | 'deleted';

type DurableState = 'pending' | 'active_activation_pending' | 'active';

interface DurableRecord {
    publicKey: Uint8Array;
    state: DurableState;
    /** OTP2-CRY-019f public enrollment transcript, when the fixture carries one. */
    et?: Uint8Array;
    /** Boot continuity token recorded when the record was written. */
    continuityToken: string;
}

/** Persistent browser-profile storage that survives a process restart. */
class DurableClientStore {
    readonly records = new Map<string, DurableRecord>();

    seed(key: string, record: DurableRecord): void {
        this.records.set(key, {
            publicKey: new Uint8Array(record.publicKey),
            state: record.state,
            ...(record.et === undefined ? {} : { et: new Uint8Array(record.et) }),
            continuityToken: record.continuityToken,
        });
    }

    read(key: string): DurableRecord | undefined {
        const record = this.records.get(key);
        return record === undefined
            ? undefined
            : {
                  publicKey: new Uint8Array(record.publicKey),
                  state: record.state,
                  ...(record.et === undefined ? {} : { et: new Uint8Array(record.et) }),
                  continuityToken: record.continuityToken,
              };
    }

    write(key: string, record: DurableRecord): void {
        this.seed(key, record);
    }

    delete(key: string): void {
        this.records.delete(key);
    }
}

/**
 * One in-memory repository instance loaded from the durable store by a process with its own
 * boot continuity token. Opening it applies OTP2-STA-010d: a token mismatch deletes incomplete
 * `pending` records and leaves durable active mirrors untouched. A restart always constructs a
 * new instance, so a regression that reuses the previous repository cannot pass the scenario.
 */
class ClientRepository {
    readonly store = new Map<string, PhoneRecord>();
    readonly continuityMatched: boolean;
    readonly deletedIncomplete: boolean;

    private constructor(
        private readonly durable: DurableClientStore,
        private readonly peerId: string,
        processToken: string,
    ) {
        const record = durable.read(peerId);
        this.continuityMatched = record === undefined || record.continuityToken === processToken;
        this.deletedIncomplete =
            record !== undefined && !this.continuityMatched && record.state === 'pending';
        if (this.deletedIncomplete) {
            durable.delete(peerId);
        } else if (record !== undefined) {
            this.store.set(peerId, {
                publicKey: record.publicKey,
                state: record.state,
                ...(record.et === undefined ? {} : { et: new Uint8Array(record.et) }),
            });
        }
    }

    static open(
        durable: DurableClientStore,
        peerId: string,
        processToken: string,
    ): ClientRepository {
        return new ClientRepository(durable, peerId, processToken);
    }

    /** Persists the session's record state under the current process boot token. */
    persist(bootToken: string): void {
        const record = this.store.get(this.peerId);
        if (record === undefined) return;
        this.durable.write(this.peerId, {
            publicKey: record.publicKey,
            state: record.state as DurableState,
            ...(record.et === undefined ? {} : { et: new Uint8Array(record.et) }),
            continuityToken: bootToken,
        });
    }

    delete(): void {
        this.store.delete(this.peerId);
        this.durable.delete(this.peerId);
    }
}

class ClientScenario {
    private readonly fixture: Fixture;
    private readonly clock = new FakeClock();
    private readonly observations: RunObservations = {
        steps: [],
        authenticated: false,
        sessionClass: null,
        sessionClasses: [],
        terminations: [],
        clientConfirmPeerStates: [],
        delivered: false,
        keysErased: false,
        sessionOpen: false,
        generations: 0,
        loads: 0,
        reloads: 0,
        continuityMatched: true,
        continuityLost: false,
        deletedIncomplete: false,
        upgrades: 0,
        storeState: '',
        reloadedState: '',
        durableState: '',
    };

    private generation: {
        harness: PhoneHarness;
        handler: ScenarioHandler;
        activation: ActivationEngine;
    } | null = null;
    private readonly proofs = new ProofRegistry();
    private activationSends = new Set<number>();
    private activationConfirms = 0;
    private durable: DurableClientStore | null = null;
    private repository: ClientRepository | null = null;
    private phoneIdentityPair: CryptoKeyPair | null = null;
    private peerId: string | null = null;
    private started: StartedHandshake | null = null;
    private expectedRx = 0n;
    private persistedState: PersistedState = 'active';
    private persistedToken = 'boot1';
    private processToken = 'boot1';
    private repositoryStale = false;
    private oversizeBuilt = false;

    constructor(fixture: Fixture) {
        this.fixture = fixture;
    }

    async run(): Promise<RunObservations> {
        const persistedState = persistedOf(this.fixture);
        this.persistedState = (persistedState[`${RECORD_PREFIX}main`] ??
            'active') as PersistedState;
        this.persistedToken = persistedState.continuity_token ?? 'boot1';
        this.processToken = this.persistedToken;
        // The running process already has the repository loaded before the fixture's first
        // event; a `process_load` step must drop it and load a *new* instance from durable
        // storage. A regression that keeps the loaded instance fails the reload assertions.
        await this.ensureDurable();
        this.loadRepository();
        for (const [index, step] of stepsOf(this.fixture).entries()) {
            await this.execute(index, step);
        }
        this.observations.keysErased = this.generation?.harness.engine._keysErased ?? false;
        this.observations.sessionOpen = this.generation?.harness.engine.isOpen ?? false;
        return this.observations;
    }

    private async phoneIdentity(): Promise<CryptoKeyPair> {
        if (this.phoneIdentityPair === null) {
            this.phoneIdentityPair = await crypto.subtle.generateKey(
                { name: 'ECDSA', namedCurve: 'P-256' },
                true,
                ['sign', 'verify'],
            );
        }
        return this.phoneIdentityPair;
    }

    /** Creates the durable store and the software phone identity on first use. */
    private async ensureDurable(): Promise<void> {
        if (this.durable !== null) return;
        const identity = await this.phoneIdentity();
        const rawPublic = new Uint8Array(await crypto.subtle.exportKey('raw', identity.publicKey));
        this.peerId ??= hexOf(rawPublic.subarray(0, 16));
        this.durable = new DurableClientStore();
        this.durable.seed(this.peerId, {
            publicKey: rawPublic,
            state: this.persistedState as DurableState,
            ...(persistedOf(this.fixture).et === undefined ? {} : { et: new Uint8Array(32) }),
            continuityToken: this.persistedToken,
        });
    }

    /** Loads a new repository from the durable store and records what it observed. */
    private loadRepository(): ClientRepository {
        if (this.durable === null || this.peerId === null) {
            throw new Error(`${this.fixture.id}: no durable store to load`);
        }
        const repository = ClientRepository.open(this.durable, this.peerId, this.processToken);
        this.repository = repository;
        this.repositoryStale = false;
        this.observations.loads += 1;
        this.observations.reloads = this.observations.loads - 1;
        this.observations.continuityMatched = repository.continuityMatched;
        this.observations.deletedIncomplete = repository.deletedIncomplete;
        this.observations.reloadedState = repository.store.get(this.peerId)?.state ?? 'deleted';
        this.observations.storeState = this.observations.reloadedState;
        this.observations.durableState = this.durable.read(this.peerId)?.state ?? 'deleted';
        return repository;
    }

    private async open(): Promise<{ harness: PhoneHarness; handler: ScenarioHandler }> {
        if (this.persistedState === 'deleted') {
            throw new Error(`${this.fixture.id}: no durable record to reconnect to`);
        }
        await this.ensureDurable();
        const identity = await this.phoneIdentity();
        if (this.repository === null || this.repositoryStale) {
            // Nothing in memory survives a restart: load a new repository instance and let it
            // apply the boot continuity token against the durable record.
            this.loadRepository();
        }
        if (this.peerId === null || this.repository === null) {
            throw new Error(`${this.fixture.id}: no repository loaded`);
        }
        const durableRecord = this.repository.store.get(this.peerId);
        if (durableRecord === undefined) {
            throw new Error(
                `${this.fixture.id}: the repository has no durable record after the reload`,
            );
        }
        const handler = new ScenarioHandler();
        const activation = new ActivationEngine({
            trust: {
                peers: async () => {
                    const live = this.repository?.store.get(this.peerId ?? '');
                    return live === undefined
                        ? []
                        : [{ installationId: this.peerId ?? '', state: live.state }];
                },
                confirm: async (peerId) => {
                    const live = this.repository?.store.get(peerId);
                    if (live === undefined)
                        throw new Error('activation confirm for an unknown peer');
                    live.state = 'active_activation_pending';
                    this.activationConfirms += 1;
                    this.repository?.persist(this.processToken);
                },
            },
            proofs: this.proofs,
        });
        const harness = await PhoneHarness.create({
            state: durableRecord.state,
            clock: this.clock,
            handler: composeHandlers(activation, handler),
            store: this.repository.store,
            phoneIdentity: identity,
            phoneId: this.peerId,
            ...(durableRecord.et === undefined ? {} : { et: durableRecord.et }),
        });
        this.observations.generations += 1;
        this.observations.authenticated = false;
        this.observations.sessionClass = null;
        this.expectedRx = 0n;
        this.started = null;
        this.activationSends = new Set<number>();
        this.generation = { harness, handler, activation };
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
        this.oversizeBuilt = false;
        const authenticatedBefore = this.observations.sessionClass !== null;
        const inboundBefore = this.generation?.harness.engine._inboundSequence ?? 0n;
        const outboundBefore = this.generation?.harness.engine._outboundSequence ?? 0n;
        const expectedBefore = this.expectedRx;
        let effects: SessionEffect[] = [];
        let eventCount =
            step.simulate.event_count === undefined ? null : Number(step.simulate.event_count);
        const type =
            step.simulate.record_type === undefined ? null : Number(step.simulate.record_type);
        const sequence =
            step.simulate.sequence === undefined ? null : BigInt(step.simulate.sequence);

        switch (step.event) {
            case 'process_load': {
                // OTP2-STA-010d: process death tears the engine down and drops the repository.
                // The next connection loads a new repository over the durable store; the
                // repository itself compares the boot continuity token and deletes incomplete
                // `pending` records, leaving a durable active mirror untouched.
                this.generation?.harness.engine.teardown();
                this.generation = null;
                this.repository = null;
                this.started = null;
                this.repositoryStale = true;
                if (step.simulate.continuity_token_matches === 'false') {
                    this.observations.continuityLost = true;
                    this.processToken = `${this.processToken}-restart`;
                } else {
                    this.processToken = this.persistedToken;
                }
                // The restarted process loads a fresh repository instance now; the next
                // connection reuses it. The continuity comparison happens inside the
                // repository against the durable record, not from a copied fixture flag.
                await this.ensureDurable();
                this.loadRepository();
                break;
            }
            case 'send_hello': {
                const generation = await this.open();
                const started = await generation.harness.start();
                this.started = started;
                effects = started.effects;
                break;
            }
            case 'reconnect_hello': {
                const generation = this.live();
                if (step.simulate.record_exists === 'false') {
                    this.repository?.delete();
                    this.persistedState = 'deleted';
                    this.observations.storeState = 'deleted';
                }
                const payload =
                    step.simulate.hello_valid === 'false'
                        ? invalidCapsHello(generation.harness.phone.helloPayload())
                        : undefined;
                effects =
                    payload === undefined
                        ? await generation.harness.deliverPhoneHello()
                        : await generation.harness.deliverPhoneHello(payload);
                break;
            }
            case 'reconnect_signature': {
                const generation = this.live();
                const started = this.started;
                if (started === null) throw new Error('reconnect_signature before send_hello');
                const phoneHelloPayload = generation.harness.phone.helloPayload();
                const transcript = reconnectRbase(phoneHelloPayload, started.clientHelloPayload);
                const phoneSignature = await generation.harness.phone.sign(transcript);
                const delivered =
                    step.simulate.sig_ok === 'false'
                        ? flipSignature(phoneSignature)
                        : phoneSignature;
                effects = await generation.harness.deliverPhoneSignature(transcript, delivered);
                if (step.simulate.sig_ok === 'false') break;
                const drive = await generation.harness.adoptHandshake(
                    started,
                    phoneHelloPayload,
                    phoneSignature,
                    effects,
                );
                this.observations.clientConfirmPeerStates.push(drive.clientConfirm.body[32]);
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
                const keys = generation.harness.keys;
                if (keys === null) throw new Error('no session keys for the phone confirm');
                let record: Uint8Array;
                if (step.simulate.hmac_ok === 'false') {
                    const corrupted = new Uint8Array(
                        await confirmation(keys.phoneConfirmKey, generation.harness.confirmInput),
                    );
                    corrupted[0] ^= 0x01;
                    record = await generation.harness.confirmRecord(peerState, {
                        confirmation: corrupted,
                    });
                } else {
                    record = await generation.harness.confirmRecord(peerState);
                }
                effects = await generation.harness.engine.onInbound(record);
                const authenticated = effects.find((effect) => effect.kind === 'authenticated');
                if (authenticated?.kind === 'authenticated') {
                    this.observations.authenticated = true;
                    this.observations.sessionClass = authenticated.sessionClass;
                    this.observations.sessionClasses.push(authenticated.sessionClass);
                    this.expectedRx = 1n;
                    this.observations.upgrades = Math.max(
                        this.observations.upgrades,
                        generation.harness.calls.upgrade.length,
                    );
                }
                break;
            }
            case 'send_protected_record': {
                if (type === null) throw new Error('send_protected_record without record_type');
                effects = await this.send(type, step);
                break;
            }
            case 'protected_record': {
                if (type === null || sequence === null) {
                    throw new Error('protected_record without record_type or sequence');
                }
                const body = await this.inboundBody(type, step, eventCount);
                const record = await sealPhoneRecord(this.live().harness, type, sequence, body, {
                    tagOk: step.simulate.tag_ok !== 'false',
                    lengthOk: step.simulate.length_ok !== 'false',
                    sessionIdOk: step.simulate.session_id_ok !== 'false',
                });
                this.oversizeBuilt = record.length > RECORD_MAX_BYTES;
                effects = await this.live().harness.engine.onInbound(record);
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
        for (const sent of classified.sentTypes) {
            if (
                sent === RecordType.ENROLLMENT_PREPARE ||
                sent === RecordType.ACTIVATION_COMMIT ||
                sent === RecordType.ACTIVATION_COMPLETE ||
                sent === RecordType.ACTIVATION_ACK
            ) {
                this.activationSends.add(sent);
            }
        }
        if (classified.terminated !== null) {
            this.observations.terminations.push(classified.terminated);
        }
        if (classified.deliveredTypes.length > 0) this.observations.delivered = true;
        const unauthorized =
            type !== null && step.event === 'protected_record' && !this.authorized(type);
        const unknownType = type !== null && recordTypeFromWire(type) === null;
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
        this.persistRepository();
        this.observations.steps.push({
            index,
            event: step.event,
            simulate: step.simulate,
            terminated: classified.terminated,
            closed: classified.closed,
            sentTypes: classified.sentTypes,
            deliveredTypes: classified.deliveredTypes,
            genericAbort: classified.genericAbort,
            unauthorized,
            unknownType,
            oversize: this.oversizeBuilt,
            recordOpened: opened,
            sequenceMismatch,
            heartbeatCrossed: type === RecordType.APP_HEARTBEAT && classified.terminated === null,
            eventCount,
            authenticatedBefore,
            expectedRxBefore: expectedBefore,
            inboundBefore,
            inboundAfter: this.generation?.harness.engine._inboundSequence ?? 0n,
            outboundBefore,
            inboundAtTeardown: this.generation?.harness.engine._inboundSequenceAtTeardown ?? 0n,
            outboundAtTeardown: this.generation?.harness.engine._outboundSequenceAtTeardown ?? 0n,
            snapshot: this.snapshot(),
            accepted: classified.terminated === null && !classified.closed,
        });
    }

    private persistRepository(): void {
        if (this.repository === null || this.durable === null || this.peerId === null) return;
        // The record is written under the boot token of the process that owns the session, so
        // a later restart with the same token matches and a different token still mismatches.
        this.repository.persist(this.processToken);
        this.observations.durableState = this.durable.read(this.peerId)?.state ?? 'deleted';
    }

    private async send(type: number, step: Step): Promise<SessionEffect[]> {
        const { harness, handler } = this.live();
        // COMMIT/ACK are observations, never fabricated: the activation handler must already
        // have emitted them (OTP2-STA-010b steps 1 and 5).
        if (type === RecordType.ACTIVATION_COMMIT || type === RecordType.ACTIVATION_ACK) {
            if (!this.activationSends.has(type)) {
                throw new Error(
                    `${this.fixture.id}: the fixture observes a ${type.toString(16)} send the activation handler never made`,
                );
            }
            return [];
        }
        const count = Number(step.simulate.event_count ?? 0);
        const body =
            type === RecordType.PULL_EVENTS
                ? pullEventsBody(harness.phone.installationId, count, handler)
                : type === RecordType.PUSH_EVENT
                  ? pushEventBody(harness.phone.installationId, handler)
                  : type === RecordType.EVENT_ACK
                    ? ackBody(harness.phone.installationId, handler, true)
                    : type === RecordType.PHONE_POLICY_STATE
                      ? policyBody(step.simulate.pull_response === 'true')
                      : new Uint8Array(0);
        return harness.engine.sendApplication(type, body);
    }

    private async inboundBody(
        type: number,
        step: Step,
        eventCount: number | null,
    ): Promise<Uint8Array> {
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
        if (type === RecordType.PHONE_POLICY_STATE) {
            return policyBody(step.simulate.pull_response === 'true');
        }
        if (type === RecordType.ENROLLMENT_PREPARE) {
            return this.activationMac(type, 0, step.simulate.act_mac_ok !== 'false');
        }
        if (type === RecordType.ACTIVATION_COMPLETE) {
            return this.activationMac(type, 2, step.simulate.act_mac_ok !== 'false');
        }
        return new Uint8Array(0);
    }

    /** The phone-side activation MAC, recomputed from this session's recovery keys and ET. */
    private async activationMac(type: number, stage: number, macOk = true): Promise<Uint8Array> {
        const { harness } = this.live();
        const recovery = harness.keys?.recovery;
        const et = harness.et;
        if (recovery === null || recovery === undefined || et === null) {
            throw new Error(`${this.fixture.id}: activation record without recovery keys`);
        }
        const key =
            type === RecordType.ACTIVATION_COMMIT || type === RecordType.ACTIVATION_ACK
                ? recovery.kRecClient
                : recovery.kRecPhone;
        const mac = await hmacSha256(key, concat(et, Uint8Array.of(stage)));
        if (!macOk) mac[mac.length - 1] ^= 0x01;
        return mac;
    }

    /** True only for the §7.3 phone→client application records. */
    private authorized(type: number): boolean {
        if (this.observations.sessionClass !== SessionClass.FULL) return false;
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
        const live = this.repository?.store.get(this.peerId ?? '');
        const store =
            live?.state ?? this.durable?.read(this.peerId ?? '')?.state ?? this.persistedState;
        this.persistedState = store as PersistedState;
        this.observations.storeState = store;
        return new Map([
            ['session:main', session],
            [`${RECORD_PREFIX}main`, store],
        ]);
    }
}

function invalidCapsHello(valid: Uint8Array): Uint8Array {
    const payload = new Uint8Array(valid);
    // `caps_len` is 1 for the production hello; replace the single capability with 0x02,
    // which violates the mandatory 0x01 first-capability rule.
    payload[payload.length - 1] = 0x02;
    return payload;
}

function flipSignature(signature: Uint8Array): Uint8Array {
    const copy = new Uint8Array(signature);
    copy[31] ^= 0x01;
    return copy;
}

// ---------------------------------------------------------------------------------------------
// Effect observations
// ---------------------------------------------------------------------------------------------

const CLIENT_EFFECTS: Record<string, (run: RunObservations) => boolean> = {
    mutual_authentication_complete: (run) => run.authenticated,
    set_class_full: (run) =>
        run.sessionClass === SessionClass.FULL || run.sessionClasses.includes(SessionClass.FULL),
    set_class_full_pending_peer: (run) =>
        run.sessionClass === SessionClass.FULL_PENDING_PEER ||
        run.sessionClasses.includes(SessionClass.FULL_PENDING_PEER),
    set_class_activation_recovery: (run) =>
        run.sessionClass === SessionClass.ACTIVATION_RECOVERY ||
        run.sessionClasses.includes(SessionClass.ACTIVATION_RECOVERY),
    terminate_session: (run) => run.terminations.some((reason) => reason !== 'app_error'),
    erase_session_keys: (run) => run.keysErased,
    counter_not_advanced: (run) => counterNotAdvanced(run.steps),
    advance_rx_sequence: (run) =>
        run.steps.some(
            (step) =>
                step.event === 'protected_record' &&
                step.accepted &&
                step.inboundAfter === step.inboundBefore + 1n,
        ),
    // The bounded engine reasons are the observation: authorization refusals are
    // `authorization`, record-open rejections (including a direction/type violation) are
    // `record_invalid`, and counter exhaustion is `sequence`.
    reject_unauthorized_record: (run) =>
        run.steps.some(
            (step) =>
                step.terminated === 'authorization' ||
                (step.terminated === 'record_invalid' && step.unauthorized),
        ),
    reject_sequence_violation: (run) =>
        run.steps.some(
            (step) =>
                step.sequenceMismatch &&
                (step.terminated === 'record_invalid' || step.terminated === 'sequence'),
        ),
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
    enrollment_prepare_processed: (run) =>
        run.steps.some((step) => step.deliveredTypes.includes(RecordType.ENROLLMENT_PREPARE)),
    prepare_verified: (run) =>
        run.steps.some(
            (step) => step.accepted && step.deliveredTypes.includes(RecordType.ENROLLMENT_PREPARE),
        ),
    activation_commit_sent: (run) =>
        run.steps.some((step) => step.sentTypes.includes(RecordType.ACTIVATION_COMMIT)),
    activation_complete_processed: (run) =>
        run.steps.some((step) => step.deliveredTypes.includes(RecordType.ACTIVATION_COMPLETE)),
    activation_ack_sent: (run) =>
        run.steps.some((step) => step.sentTypes.includes(RecordType.ACTIVATION_ACK)),
    record_active_client: (run) =>
        run.steps.some(
            (step) =>
                step.snapshot.get('durable_client_record:main') === 'active_activation_pending',
        ),
    activation_pending_flag_set: (run) =>
        run.steps.some(
            (step) =>
                step.snapshot.get('durable_client_record:main') === 'active_activation_pending',
        ),
    reconnect_confirm_sent_peer_state_below_active: (run) =>
        run.clientConfirmPeerStates.includes(0x02),
    erase_enrollment_remnants: () => false,
    reconnect_confirm_sent_peer_state_active: (run) =>
        run.clientConfirmPeerStates.includes(PEER_STATE_ACTIVE),
    delete_incomplete_materials: (run) => run.deletedIncomplete,
    erase_incomplete_materials: () => false,
};

/**
 * OTP2-STA-015 counter observation against the pre-teardown snapshot:
 * - a record rejected while opening leaves both counters untouched;
 * - a record that opened and was then refused by its authorization class consumes exactly
 *   its own sequence and never advances the outbound counter.
 */
function counterNotAdvanced(steps: StepOutcome[]): boolean {
    const rejected = steps.filter(
        (step) => step.event === 'protected_record' && step.terminated !== null,
    );
    if (rejected.length === 0) return false;
    return rejected.every((step) => {
        const expectedInbound =
            step.terminated === 'authorization'
                ? step.expectedRxBefore + 1n
                : step.expectedRxBefore;
        return (
            step.inboundAtTeardown === expectedInbound &&
            step.outboundAtTeardown === step.outboundBefore
        );
    });
}

function observe(run: RunObservations, name: string): boolean {
    const mapper = CLIENT_EFFECTS[name];
    if (mapper === undefined) throw new Error(`effect '${name}' has no engine observation mapping`);
    return mapper(run);
}

async function runScenario(id: string): Promise<RunObservations> {
    return new ClientScenario(byId(id)).run();
}

function step(t_ms: number, event: string, simulate: Record<string, unknown> = {}): Step {
    return { t_ms, event, simulate: stringValues(simulate), expect: {} };
}

function stringValues(simulate: Record<string, unknown>): Record<string, string> {
    return Object.fromEntries(Object.entries(simulate).map(([key, value]) => [key, String(value)]));
}

function mutate(original: Step, patch: Record<string, unknown>): Step {
    return { ...original, simulate: { ...original.simulate, ...stringValues(patch) } };
}

function fixtureWithSteps(steps: Step[]): Fixture {
    const base = byId('st-rec-client-full-pull-delivers');
    return {
        id: `${base.id}#mutation`,
        input: { ...base.input, steps },
        expected: base.expected,
    };
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
        // Coverage is per fixture file: the OTP-09 inventory owns records.json/reconnect.json,
        // the OTP-10 inventory owns enrollment.json (minus the five OTP-02 fixture self-checks)
        // and the ids OTP-09 deferred to OTP-10.
        expect(Object.keys(INVENTORY_ENTRIES).every((id) => SESSION_IDS.has(id))).toBe(true);
        const coveredSessionIds = new Set([
            ...Object.keys(INVENTORY_ENTRIES),
            ...Object.keys(OTP10_INVENTORY_ENTRIES).filter((id) => SESSION_IDS.has(id)),
        ]);
        expect([...coveredSessionIds].sort()).toEqual([...SESSION_IDS].sort());
        const otp10EnrollmentIds = Object.keys(OTP10_INVENTORY_ENTRIES)
            .filter((id) => !SESSION_IDS.has(id))
            .sort();
        expect(otp10EnrollmentIds).toEqual(
            [...ENROLLMENT_IDS].filter((id) => !FIXTURE_SELF_CHECK_IDS.includes(id)).sort(),
        );
        for (const [id, entry] of Object.entries(INVENTORY_ENTRIES)) {
            expect(classificationOf(entry, OTP09_DEFERRAL), `OTP-09 ${id} classification`).toBe(
                true,
            );
        }
        for (const [id, entry] of Object.entries(OTP10_INVENTORY_ENTRIES)) {
            expect(classificationOf(entry, OTP10_DEFERRAL), `OTP-10 ${id} classification`).toBe(
                true,
            );
        }
        expect(
            Object.values(INVENTORY_ENTRIES).filter((entry) => entry === 'execute'),
        ).toHaveLength(21);
    });

    it('otp10InventoryOnlyOverridesTheOtp09Otp10Deferrals', () => {
        const deferredToOtp10 = Object.entries(INVENTORY_ENTRIES)
            .filter(([, entry]) => entry === 'deferred:OTP-10')
            .map(([id]) => id)
            .sort();
        const overlapping = Object.keys(OTP10_INVENTORY_ENTRIES)
            .filter((id) => id in INVENTORY_ENTRIES)
            .sort();
        expect(overlapping).toEqual(deferredToOtp10);
        const executedOtp09 = Object.entries(INVENTORY_ENTRIES)
            .filter(([, entry]) => entry === 'execute')
            .map(([id]) => id);
        expect(
            Object.keys(OTP10_INVENTORY_ENTRIES).filter((id) => executedOtp09.includes(id)),
        ).toEqual([]);
        expect(
            Object.keys(OTP10_INVENTORY_ENTRIES).filter((id) => SESSION_IDS.has(id)),
        ).toHaveLength(10);
    });

    it('enrollmentTracesBelongToTheEnrollmentRunners', () => {
        for (const id of FIXTURE_SELF_CHECK_IDS) {
            expect(ENROLLMENT_IDS.has(id), `self-check ${id} must be an enrollment fixture`).toBe(
                true,
            );
            expect(
                OTP10_INVENTORY_ENTRIES[id],
                `self-check ${id} stays in the OTP-02 runner`,
            ).toBeUndefined();
        }
        expect(Object.keys(INVENTORY_ENTRIES).filter((id) => ENROLLMENT_IDS.has(id))).toEqual([]);
        const owned = new Set([
            ...SESSION_IDS,
            ...[...ENROLLMENT_IDS].filter((id) => !FIXTURE_SELF_CHECK_IDS.includes(id)),
        ]);
        expect(Object.keys(EFFECTIVE_INVENTORY).sort()).toEqual([...owned].sort());
    });

    it('everyEffectNameInAnExecutedClientScenarioIsMapped', () => {
        const executed = Object.entries(EXECUTION_INVENTORY).filter(
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
        const executed = Object.entries(EXECUTION_INVENTORY).filter(
            ([, entry]) => entry === 'execute',
        );
        const phone = executed.filter(([id]) => roleOf(byId(id)) === 'phone');
        const client = executed.filter(([id]) => roleOf(byId(id)) === 'client');
        expect(phone.length + client.length, 'every execute id belongs to one runner').toBe(
            executed.length,
        );
        // The client-id set is read from the inventory (the fixture role of every execute entry),
        // not a fixed list, so an OTP-10 entry that flips a recovery trace to `execute` reaches
        // this runner without editing the test.
        const inventoryClientIds = executed
            .filter(([id]) => roleOf(byId(id)) === 'client')
            .map(([id]) => id)
            .sort();
        expect(inventoryClientIds.length).toBeGreaterThan(0);
        expect(client.map(([id]) => id).sort()).toEqual(inventoryClientIds);
    });
});

describe('client conformance scenarios', () => {
    for (const [id, entry] of Object.entries(EXECUTION_INVENTORY)) {
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
                if (id === 'st-rec-client-mirror-durable-across-restart') {
                    expect(run.loads, 'initial load plus the restart reload').toBe(2);
                    expect(run.reloads, 'the restart must drop and reload the repository').toBe(1);
                    expect(run.continuityLost, 'the continuity token must change').toBe(true);
                    expect(run.continuityMatched, 'the repository must observe the mismatch').toBe(
                        false,
                    );
                    expect(run.deletedIncomplete, 'an active mirror must survive the restart').toBe(
                        false,
                    );
                    expect(run.reloadedState, 'the mirror must load as activation-pending').toBe(
                        'active_activation_pending',
                    );
                    expect(run.storeState, 'the handshake must upgrade the mirror').toBe('active');
                    expect(run.durableState, 'the upgrade must be persisted durably').toBe(
                        'active',
                    );
                    expect(run.generations, 'the reloaded engine must handshake again').toBe(1);
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
                if (byId(id).expected.stage !== undefined) {
                    expect(fixture.expected.accepted).toBe(false);
                }
            });
        }
    }

    it('deferred scenarios are printed with their owning task', () => {
        const deferred = Object.entries(EFFECTIVE_INVENTORY)
            .filter(([, entry]) => typeof entry === 'string' && entry.startsWith('deferred:'))
            .sort(([left], [right]) => left.localeCompare(right));
        for (const [id, owner] of deferred) {
            console.log(`deferred ${id} -> ${(owner as string).replace('deferred:', '')}`);
        }
        expect(deferred.length).toBeGreaterThan(0);
    });

    it('simulation flags change the bounded engine reason', async () => {
        const hello = step(1000, 'send_hello');
        const phoneHello = step(2000, 'reconnect_hello', { hello_valid: true });
        const phoneSignature = step(3000, 'reconnect_signature', { sig_ok: true });
        const clientConfirm = step(4000, 'send_reconnect_confirm');
        const phoneConfirm = step(5000, 'reconnect_confirm', {
            hmac_ok: true,
            peer_state: '0x01',
        });
        const pull = step(6000, 'send_protected_record', {
            record_type: '0x0200',
            sequence: '1',
        });
        const pullEvents = step(7000, 'protected_record', {
            record_type: '0x0202',
            sequence: '1',
            tag_ok: true,
            length_ok: true,
            session_id_ok: true,
            event_count: 2,
        });
        const handshake = [hello, phoneHello, phoneSignature, clientConfirm, phoneConfirm];

        const invalidHello = await new ClientScenario(
            fixtureWithSteps([hello, mutate(phoneHello, { hello_valid: false })]),
        ).run();
        expect(invalidHello.steps[1].genericAbort).toBe(true);
        expect(invalidHello.steps[1].sentTypes).toEqual([]);

        const invalidSignature = await new ClientScenario(
            fixtureWithSteps([hello, phoneHello, mutate(phoneSignature, { sig_ok: false })]),
        ).run();
        expect(invalidSignature.steps[2].genericAbort).toBe(true);
        expect(invalidSignature.steps[2].sentTypes).toEqual([]);

        const invalidConfirm = await new ClientScenario(
            fixtureWithSteps([...handshake.slice(0, 4), mutate(phoneConfirm, { hmac_ok: false })]),
        ).run();
        expect(invalidConfirm.steps[4].terminated).toBe('confirm_invalid');

        for (const [flag, expectedReason] of [
            ['tag_ok', 'record_invalid'],
            ['session_id_ok', 'record_invalid'],
            ['length_ok', 'record_invalid'],
        ] as const) {
            const run = await new ClientScenario(
                fixtureWithSteps([...handshake, pull, mutate(pullEvents, { [flag]: false })]),
            ).run();
            const recordStep = run.steps[run.steps.length - 1];
            expect(recordStep.terminated, flag).toBe(expectedReason);
            expect(recordStep.oversize, flag).toBe(flag === 'length_ok');
        }
    });

    it('the repository applies a continuity-token change to incomplete records only', () => {
        const pending = new DurableClientStore();
        pending.seed('main', {
            publicKey: Uint8Array.of(1),
            state: 'pending',
            continuityToken: 'boot1',
        });
        const reopenedPending = ClientRepository.open(pending, 'main', 'boot2');
        expect(reopenedPending.continuityMatched).toBe(false);
        expect(reopenedPending.deletedIncomplete).toBe(true);
        expect(reopenedPending.store.size).toBe(0);

        const mirror = new DurableClientStore();
        mirror.seed('main', {
            publicKey: Uint8Array.of(2),
            state: 'active_activation_pending',
            continuityToken: 'boot1',
        });
        const reopenedMirror = ClientRepository.open(mirror, 'main', 'boot2');
        expect(reopenedMirror.continuityMatched).toBe(false);
        expect(reopenedMirror.deletedIncomplete).toBe(false);
        expect(reopenedMirror.store.get('main')?.state).toBe('active_activation_pending');

        const sameBoot = ClientRepository.open(mirror, 'main', 'boot1');
        expect(sameBoot.continuityMatched).toBe(true);
        expect(sameBoot.deletedIncomplete).toBe(false);
        expect(sameBoot.store.get('main')?.state).toBe('active_activation_pending');
    });

    it('restart lifecycle persists through the repository and recreates both', async () => {
        // Build a real durable record and a real first engine, let the mirror upgrade write the
        // record state through the repository API, tear the engine down, then reload a new
        // repository and engine over the same durable store under a changed boot token.
        const identity = await crypto.subtle.generateKey(
            { name: 'ECDSA', namedCurve: 'P-256' },
            true,
            ['sign', 'verify'],
        );
        const rawPublic = new Uint8Array(await crypto.subtle.exportKey('raw', identity.publicKey));
        const peerId = hexOf(rawPublic.subarray(0, 16));
        const durable = new DurableClientStore();
        durable.seed(peerId, {
            publicKey: rawPublic,
            state: 'active_activation_pending',
            continuityToken: 'boot1',
        });

        const repo1 = ClientRepository.open(durable, peerId, 'boot1');
        expect(repo1.continuityMatched).toBe(true);
        const harness1 = await PhoneHarness.create({
            state: 'active_activation_pending',
            store: repo1.store,
            phoneIdentity: identity,
            phoneId: peerId,
        });
        const { authenticated } = await harness1.openSession({ peerState: 1 });
        expect(authenticated.kind).toBe('authenticated');
        // The mirror upgrade is the session's write; persist it through the repository API.
        expect(repo1.store.get(peerId)?.state).toBe('active');
        repo1.persist('boot1');
        expect(durable.read(peerId)?.state).toBe('active');
        expect(durable.read(peerId)?.continuityToken).toBe('boot1');
        const firstPrefix = harness1.engine.sessionIdPrefix?.slice();

        harness1.engine.teardown();
        expect(harness1.engine.isOpen).toBe(false);
        expect(harness1.engine._keysErased).toBe(true);

        const repo2 = ClientRepository.open(durable, peerId, 'boot2');
        expect(repo2.store).not.toBe(repo1.store);
        expect(repo2.continuityMatched).toBe(false);
        expect(repo2.deletedIncomplete).toBe(false);
        expect(repo2.store.get(peerId)?.state).toBe('active');
        const state = repo2.store.get(peerId)?.state;
        if (state === undefined) throw new Error('the reloaded repository lost the mirror');
        const harness2 = await PhoneHarness.create({
            state,
            store: repo2.store,
            phoneIdentity: identity,
            phoneId: peerId,
        });
        const reopened = await harness2.openSession({ peerState: 1 });
        expect(reopened.authenticated.kind).toBe('authenticated');
        if (reopened.authenticated.kind !== 'authenticated') {
            throw new Error('the reloaded engine did not authenticate');
        }
        expect(reopened.authenticated.sessionClass).toBe(SessionClass.FULL);
        expect(harness2.engine).not.toBe(harness1.engine);
        expect(harness2.engine.isOpen).toBe(true);
        expect(harness2.engine.sessionIdPrefix).not.toEqual(firstPrefix);

        // Persisting under the new token makes a later restart match.
        repo2.persist('boot2');
        expect(durable.read(peerId)?.continuityToken).toBe('boot2');
        const repo3 = ClientRepository.open(durable, peerId, 'boot2');
        expect(repo3.continuityMatched).toBe(true);
        expect(repo3.store.get(peerId)?.state).toBe('active');
    });

    it('client signatures are low-S P1363 and the handshake verifies them', async () => {
        // The live cross-endpoint handshake is exercised by `loopback.test.ts`. This runner
        // pins the client half: the client only authenticates after the scripted phone's
        // 64-byte low-S signature verifies, and the confirm it emits carries the client's
        // own peer-state byte.
        const run = await runScenario('st-rec-client-full-pull-delivers');
        expect(run.sessionClass).toBe(SessionClass.FULL);
        const signatureStep = run.steps.find((step) => step.event === 'reconnect_signature');
        expect(signatureStep?.accepted).toBe(true);
        expect(run.clientConfirmPeerStates).toEqual([PEER_STATE_ACTIVE]);
    });
});
