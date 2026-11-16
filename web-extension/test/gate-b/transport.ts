import assert from 'node:assert/strict';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

import type { ClientEngineDeps, SessionEffect } from '../../src/session/engine';
import { ClientSessionEngine } from '../../src/session/engine';
import { decodeFrame } from '../../src/session/frames';
import { decodeHello } from '../../src/session/hello';
import { requireValidPoint } from '../../src/session/p256';
import { deriveKeyMaterial, importKeys, type ReconnectKeys } from '../../src/session/schedule';
import { FrameType, Role } from '../../src/session/wire';

import type { DerivedIdentity } from './trust';

// OTP-12 design D8: the scripted client's transport.
//
// The phone side is a child JVM speaking the existing binary loopback protocol
// (`kind u8 || u32be length || payload`; kind 0 frame, 1 command, 2 event), so the
// TypeScript client drives the real Kotlin engine with no connector or native
// messaging in the loop. `runJourney` walks the client-agnostic journey file and
// checks each step's expectations; drivers supply the protocol actions.

const WATCHDOG_MS = 50_000;
const SESSION_PREFIX_BYTES = 8;
const CLASS_NAME_SESSION = 'me.nagaev.veles.session.loopback.PhoneLoopbackMainKt';
const CLASS_NAME_GATE_B = 'me.nagaev.veles.session.GateBLoopbackMainKt';

export const EVENT_AUTHENTICATED = 0x01;
export const EVENT_CLOSED = 0x02;
export const EVENT_WINDOW = 0x03;
export const EVENT_TRUST = 0x04;
export const EVENT_PAIRING_STATE = 0x05;

const U16_BYTES = 2;
const INSTALLATION_BYTES = 16;
const EVENT_ID_BYTES = 16;
const EPOCH_BYTES = 8;
const LIFETIME_BYTES = 4;
const AMOUNT_BYTES = 8;
const SCALE_BYTES = 1;
const CURRENCY_BYTES = 3;
const RECORD_HEADER_BYTES = 23;
const RECORD_TAG_BYTES = 16;

const encoder = new TextEncoder();

// ---------------------------------------------------------------------------------------------
// Seed-derived P-256 identities, matching PhoneLoopbackMain.kt
// ---------------------------------------------------------------------------------------------

const FIELD_PRIME = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffffn;
const CURVE_A = FIELD_PRIME - 3n;
const ORDER = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551n;
const GENERATOR = {
    x: 0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296n,
    y: 0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5n,
};
const COORDINATE_BYTES = 32;

function toBigInt(value: Uint8Array): bigint {
    let result = 0n;
    for (const byte of value) result = (result << 8n) | BigInt(byte);
    return result;
}

function fromBigInt(value: bigint): Uint8Array {
    const out = new Uint8Array(COORDINATE_BYTES);
    let remaining = value;
    for (let index = COORDINATE_BYTES - 1; index >= 0; index -= 1) {
        out[index] = Number(remaining & 0xffn);
        remaining >>= 8n;
    }
    return out;
}

function mod(value: bigint, modulus: bigint): bigint {
    return ((value % modulus) + modulus) % modulus;
}

function invert(value: bigint): bigint {
    let low = 1n;
    let high = 0n;
    let a = mod(value, FIELD_PRIME);
    let b = FIELD_PRIME;
    while (a > 1n) {
        const quotient = b / a;
        [low, high] = [high - quotient * low, low];
        [a, b] = [b - quotient * a, a];
    }
    return mod(low, FIELD_PRIME);
}

type Point = { x: bigint; y: bigint } | null;

function add(left: Point, right: Point): Point {
    if (left === null) return right;
    if (right === null) return left;
    if (left.x === right.x && mod(left.y + right.y, FIELD_PRIME) === 0n) return null;
    const slope =
        left.x === right.x && left.y === right.y
            ? mod((3n * left.x * left.x + CURVE_A) * invert(2n * left.y), FIELD_PRIME)
            : mod((right.y - left.y) * invert(right.x - left.x), FIELD_PRIME);
    const x = mod(slope * slope - left.x - right.x, FIELD_PRIME);
    const y = mod(slope * (left.x - x) - left.y, FIELD_PRIME);
    return { x, y };
}

function multiply(scalar: bigint): Uint8Array {
    let result: Point = null;
    let addend: Point = GENERATOR;
    let remaining = scalar;
    while (remaining > 0n) {
        if (remaining & 1n) result = add(result, addend);
        addend = add(addend, addend);
        remaining >>= 1n;
    }
    if (result === null) throw new Error('identity point at infinity');
    return Uint8Array.of(0x04, ...fromBigInt(result.x), ...fromBigInt(result.y));
}

function base64url(value: Uint8Array): string {
    return Buffer.from(value).toString('base64url');
}

export function hexOf(value: Uint8Array): string {
    return Array.from(value, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export async function deriveIdentity(
    identityLabel: string,
    installationLabel: string,
    seed: string,
): Promise<DerivedIdentity> {
    let counter = 0;
    for (;;) {
        const digest = createHash('sha256')
            .update(
                Buffer.concat([
                    Buffer.from(identityLabel),
                    Buffer.from([counter]),
                    Buffer.from(seed),
                ]),
            )
            .digest();
        const scalar = toBigInt(new Uint8Array(digest));
        if (scalar > 0n && scalar < ORDER) {
            const point = multiply(scalar);
            const key = await crypto.subtle.importKey(
                'jwk',
                {
                    kty: 'EC',
                    crv: 'P-256',
                    x: base64url(point.subarray(1, 33)),
                    y: base64url(point.subarray(33, 65)),
                    d: base64url(new Uint8Array(digest)),
                },
                { name: 'ECDSA', namedCurve: 'P-256' },
                false,
                ['sign'],
            );
            const installationId = createHash('sha256')
                .update(Buffer.concat([Buffer.from(installationLabel), Buffer.from(seed)]))
                .digest()
                .subarray(0, INSTALLATION_BYTES);
            return { installationId, point, key };
        }
        counter += 1;
        if (counter > 256) throw new Error('cannot derive a P-256 scalar from the seed');
    }
}

// ---------------------------------------------------------------------------------------------
// The child process protocol
// ---------------------------------------------------------------------------------------------

export interface PhoneMessage {
    kind: number;
    payload: Uint8Array;
}

export interface LoopbackProcess {
    child: ChildProcessWithoutNullStreams;
    send(kind: number, payload: Uint8Array): void;
    next(match: (message: PhoneMessage) => boolean): Promise<PhoneMessage>;
    waitForStderr(fragment: string): Promise<void>;
    stderr(): string;
    stop(): Promise<void>;
}

export interface StartPhoneOptions {
    seed: string;
    main: 'session' | 'gate-b';
    mode?: 'pairing';
}

interface MessageWaiter {
    match: (message: PhoneMessage) => boolean;
    resolve: (message: PhoneMessage) => void;
    reject: (error: Error) => void;
    timer: ReturnType<typeof setTimeout>;
}

interface StderrWaiter {
    fragment: string;
    resolve: () => void;
    reject: (error: Error) => void;
    timer: ReturnType<typeof setTimeout>;
}

function watchdog(callback: () => void): ReturnType<typeof setTimeout> {
    const timer = setTimeout(callback, WATCHDOG_MS);
    // A pending protocol step must never keep the test process alive on its own.
    timer.unref();
    return timer;
}

/**
 * Spawns the phone child JVM. Frame and stderr waits are event-driven (stdout/stderr data plus
 * waiter queues), never polled, and every wait carries one cancellable watchdog that is cleared
 * on completion. Child `error`/`exit` rejects every pending wait with the child's stderr.
 */
export function startPhone(options: StartPhoneOptions): LoopbackProcess {
    const classpath = process.env.VELES_LOOPBACK_CLASSPATH ?? '';
    const java = process.env.VELES_LOOPBACK_JAVA ?? 'java';
    const hostJniDir = process.env.VELES_HOST_JNI_DIR ?? '';
    const jvmArguments = ['-cp', classpath];
    if (hostJniDir !== '') jvmArguments.push(`-Djava.library.path=${hostJniDir}`);
    const className = options.main === 'gate-b' ? CLASS_NAME_GATE_B : CLASS_NAME_SESSION;
    const programArguments =
        options.mode === 'pairing' ? [options.seed, 'pairing'] : [options.seed];
    const child = spawn(java, [...jvmArguments, className, ...programArguments], {
        stdio: ['pipe', 'pipe', 'pipe'],
    }) as ChildProcessWithoutNullStreams;
    let buffered = Buffer.alloc(0);
    const messages: PhoneMessage[] = [];
    const messageWaiters: MessageWaiter[] = [];
    const stderrWaiters: StderrWaiter[] = [];
    let stderr = '';
    let exitDescription: string | null = null;
    let finished = false;

    const failAll = (error: Error): void => {
        for (const waiter of messageWaiters.splice(0)) {
            clearTimeout(waiter.timer);
            waiter.reject(error);
        }
        for (const waiter of stderrWaiters.splice(0)) {
            clearTimeout(waiter.timer);
            waiter.reject(error);
        }
    };

    const flushMessages = (): void => {
        for (let index = 0; index < messageWaiters.length; ) {
            const waiter = messageWaiters[index];
            if (waiter === undefined) break;
            const found = messages.findIndex(waiter.match);
            if (found < 0) {
                index += 1;
                continue;
            }
            const message = messages.splice(found, 1)[0];
            if (message === undefined) break;
            messageWaiters.splice(index, 1);
            clearTimeout(waiter.timer);
            waiter.resolve(message);
        }
    };

    const flushStderr = (): void => {
        for (let index = 0; index < stderrWaiters.length; ) {
            const waiter = stderrWaiters[index];
            if (waiter === undefined) break;
            if (!stderr.includes(waiter.fragment)) {
                index += 1;
                continue;
            }
            stderrWaiters.splice(index, 1);
            clearTimeout(waiter.timer);
            waiter.resolve();
        }
    };

    child.stdin.on('error', () => undefined);
    child.stderr.on('data', (chunk: Buffer) => {
        stderr += chunk.toString();
        flushStderr();
    });
    child.stdout.on('data', (chunk: Buffer) => {
        buffered = Buffer.concat([buffered, chunk]);
        for (;;) {
            if (buffered.length < 5) break;
            const kind = buffered[0] ?? 0;
            const length = buffered.readUInt32BE(1);
            if (buffered.length < 5 + length) break;
            messages.push({ kind, payload: new Uint8Array(buffered.subarray(5, 5 + length)) });
            buffered = buffered.subarray(5 + length);
        }
        flushMessages();
    });
    child.on('error', (error: Error) => {
        finished = true;
        exitDescription = `loopback process error: ${error.message}; stderr: ${stderr}`;
        failAll(new Error(exitDescription));
    });
    child.on('exit', (code, signal) => {
        finished = true;
        exitDescription = `loopback exit code=${String(code)} signal=${String(signal)}; stderr: ${stderr}`;
        failAll(new Error(exitDescription));
    });

    return {
        child,
        send(kind: number, payload: Uint8Array): void {
            const frame = Buffer.alloc(5 + payload.length);
            frame[0] = kind;
            frame.writeUInt32BE(payload.length, 1);
            Buffer.from(payload).copy(frame, 5);
            child.stdin.write(frame);
        },
        next(match: (message: PhoneMessage) => boolean): Promise<PhoneMessage> {
            const existing = messages.findIndex(match);
            if (existing >= 0) {
                return Promise.resolve(messages.splice(existing, 1)[0] as PhoneMessage);
            }
            if (exitDescription !== null) return Promise.reject(new Error(exitDescription));
            return new Promise<PhoneMessage>((resolve, reject) => {
                const waiter: MessageWaiter = {
                    match,
                    resolve,
                    reject,
                    timer: watchdog(() => {
                        const index = messageWaiters.indexOf(waiter);
                        if (index >= 0) messageWaiters.splice(index, 1);
                        reject(
                            new Error(
                                `loopback timed out waiting for a message; stderr: ${stderr}`,
                            ),
                        );
                    }),
                };
                messageWaiters.push(waiter);
                flushMessages();
            });
        },
        waitForStderr(fragment: string): Promise<void> {
            if (stderr.includes(fragment)) return Promise.resolve();
            if (exitDescription !== null) {
                return Promise.reject(
                    new Error(`loopback exited before stderr '${fragment}'; ${exitDescription}`),
                );
            }
            return new Promise<void>((resolve, reject) => {
                const waiter: StderrWaiter = {
                    fragment,
                    resolve,
                    reject,
                    timer: watchdog(() => {
                        const index = stderrWaiters.indexOf(waiter);
                        if (index >= 0) stderrWaiters.splice(index, 1);
                        reject(
                            new Error(
                                `loopback timed out waiting for stderr '${fragment}'; stderr: ${stderr}`,
                            ),
                        );
                    }),
                };
                stderrWaiters.push(waiter);
                flushStderr();
            });
        },
        stderr(): string {
            return stderr;
        },
        async stop(): Promise<void> {
            if (finished || child.exitCode !== null || child.signalCode !== null) return;
            const exited = new Promise<void>((resolve) => child.once('exit', () => resolve()));
            child.stdin.end();
            child.kill('SIGKILL');
            await exited;
        },
    };
}

export function command(phone: LoopbackProcess, text: string): void {
    phone.send(1, encoder.encode(text));
}

export function isFrame(message: PhoneMessage, type: number): boolean {
    return message.kind === 0 && message.payload.length > 0 && message.payload[0] === type;
}

export function isEvent(message: PhoneMessage, code: number): boolean {
    return message.kind === 2 && message.payload.length > 0 && message.payload[0] === code;
}

export function eventText(message: PhoneMessage): string {
    return new TextDecoder().decode(message.payload.subarray(1));
}

export function isProtectedRecord(message: PhoneMessage): boolean {
    return (
        message.kind === 0 &&
        message.payload.length >= RECORD_HEADER_BYTES + RECORD_TAG_BYTES &&
        message.payload[0] === 1 &&
        message.payload[1] === 1
    );
}

export function recordTypeOf(message: PhoneMessage): number | null {
    if (!isProtectedRecord(message)) return null;
    return ((message.payload[3] ?? 0) << 8) | (message.payload[4] ?? 0);
}

export function sendEffects(phone: LoopbackProcess, effects: SessionEffect[]): void {
    for (const effect of effects) {
        if (effect.kind === 'send') phone.send(0, effect.bytes);
    }
}

// ---------------------------------------------------------------------------------------------
// Pulled events
// ---------------------------------------------------------------------------------------------

export interface PulledEvent {
    /** Lowercase hex of the 16-byte event id. */
    id: string;
    idBytes: Uint8Array;
    body: Uint8Array;
    remainingLifetime: number;
}

function readU16(value: Uint8Array, offset: number): number {
    return ((value[offset] ?? 0) << 8) | (value[offset + 1] ?? 0);
}

function readU32(value: Uint8Array, offset: number): number {
    return (
        (((value[offset] ?? 0) << 24) |
            ((value[offset + 1] ?? 0) << 16) |
            ((value[offset + 2] ?? 0) << 8) |
            (value[offset + 3] ?? 0)) >>>
        0
    );
}

function parseEventAt(body: Uint8Array, start: number): { event: PulledEvent; next: number } {
    let offset = start;
    if (body.length < offset + INSTALLATION_BYTES + EVENT_ID_BYTES + EPOCH_BYTES + LIFETIME_BYTES) {
        throw new Error('a pulled event is truncated');
    }
    offset += INSTALLATION_BYTES;
    const idBytes = body.slice(offset, offset + EVENT_ID_BYTES);
    offset += EVENT_ID_BYTES;
    offset += EPOCH_BYTES;
    const remainingLifetime = readU32(body, offset);
    offset += LIFETIME_BYTES;
    const codeLength = readU16(body, offset);
    offset += U16_BYTES + codeLength;
    const merchantLength = readU16(body, offset);
    offset += U16_BYTES + merchantLength;
    offset += AMOUNT_BYTES + SCALE_BYTES + CURRENCY_BYTES;
    if (offset > body.length) throw new Error('a pulled event is truncated');
    return {
        event: {
            id: hexOf(idBytes),
            idBytes,
            body: body.slice(start, offset),
            remainingLifetime,
        },
        next: offset,
    };
}

export function parseEvent(body: Uint8Array): PulledEvent {
    const parsed = parseEventAt(body, 0);
    if (parsed.next !== body.length) throw new Error('a pushed event has trailing bytes');
    return parsed.event;
}

export function parsePullEvents(body: Uint8Array): PulledEvent[] {
    const count = body[0] ?? 0;
    const events: PulledEvent[] = [];
    let offset = 1;
    for (let index = 0; index < count; index += 1) {
        const parsed = parseEventAt(body, offset);
        events.push(parsed.event);
        offset = parsed.next;
    }
    if (offset !== body.length) throw new Error('the pull_events body has trailing bytes');
    return events;
}

// ---------------------------------------------------------------------------------------------
// The client session engine
// ---------------------------------------------------------------------------------------------

export interface EngineBundle {
    engine: ClientSessionEngine;
    ephemeral: CryptoKeyPair;
    nonce: Uint8Array;
}

export async function newClientEngine(
    deps: Omit<ClientEngineDeps, 'ephemeral' | 'nonce'>,
): Promise<EngineBundle> {
    const ephemeral = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, [
        'deriveBits',
    ]);
    const nonce = crypto.getRandomValues(new Uint8Array(16));
    const engine = new ClientSessionEngine({
        ...deps,
        ephemeral: async () => ephemeral,
        nonce: () => nonce,
    });
    return { engine, ephemeral, nonce };
}

export interface OpenSession {
    engine: ClientSessionEngine;
    keys: ReconnectKeys;
    prefix: Uint8Array;
}

async function deriveSessionKeys(
    ephemeral: CryptoKeyPair,
    nonce: Uint8Array,
    phoneHelloPayload: Uint8Array,
    et: Uint8Array | null,
): Promise<ReconnectKeys> {
    const hello = decodeHello(phoneHelloPayload, Role.PHONE);
    const point = await requireValidPoint(hello.ephemeralPublicKey);
    const shared = new Uint8Array(
        await crypto.subtle.deriveBits({ name: 'ECDH', public: point }, ephemeral.privateKey, 256),
    );
    const material = await deriveKeyMaterial(shared, hello.nonce, nonce, et);
    try {
        return await importKeys(material);
    } finally {
        material.c2pKey.fill(0);
        material.p2cKey.fill(0);
        shared.fill(0);
    }
}

/** Drives one §11.3 reconnect over the loopback and returns its derived session keys. */
export async function openSession(
    phone: LoopbackProcess,
    bundle: EngineBundle,
    et: Uint8Array | null = null,
): Promise<OpenSession> {
    const { engine, ephemeral, nonce } = bundle;
    sendEffects(phone, await engine.start());
    const helloMessage = await phone.next((message) => isFrame(message, FrameType.RECONNECT_HELLO));
    const phoneHelloPayload = decodeFrame(helloMessage.payload).payload;
    sendEffects(phone, await engine.onInbound(helloMessage.payload));
    const signatureMessage = await phone.next((message) =>
        isFrame(message, FrameType.RECONNECT_SIGNATURE),
    );
    const signatureEffects = await engine.onInbound(signatureMessage.payload);
    sendEffects(phone, signatureEffects);
    const confirmSend = signatureEffects.find(
        (effect) => effect.kind === 'send' && effect.completionToken !== undefined,
    );
    if (confirmSend?.kind === 'send' && confirmSend.completionToken !== undefined) {
        sendEffects(phone, await engine.onSendComplete(confirmSend.completionToken, true));
    }
    const confirmMessage = await phone.next(isProtectedRecord);
    const authenticated = await engine.onInbound(confirmMessage.payload);
    if (!authenticated.some((effect) => effect.kind === 'authenticated')) {
        throw new Error('the session did not authenticate');
    }
    sendEffects(phone, authenticated);
    const prefix = engine.sessionIdPrefix;
    if (prefix === null || prefix.length !== SESSION_PREFIX_BYTES) {
        throw new Error('the session has no id prefix');
    }
    return {
        engine,
        keys: await deriveSessionKeys(ephemeral, nonce, phoneHelloPayload, et),
        prefix,
    };
}

// ---------------------------------------------------------------------------------------------
// Journey file and runner
// ---------------------------------------------------------------------------------------------

export interface JourneyExpectation {
    session_class?: string;
    event_count?: number;
    remaining_lifetime_range?: [number, number];
    key_fresh?: boolean;
    rejected?: boolean;
}

export interface JourneyStep {
    action: string;
    params: Record<string, unknown>;
    expect: JourneyExpectation;
}

export interface StepResult {
    sessionClass?: string;
    eventCount?: number;
    eventIds?: string[];
    remainingLifetimes?: number[];
    keyFresh?: boolean;
    rejected?: boolean;
}

export interface JourneyDriver {
    run(step: JourneyStep): Promise<StepResult>;
}

export type JourneyName = 'enrollment' | 'protected-records';

const JOURNEY_FILE = new URL('../../../protocol/conformance/gate-b-journeys.json', import.meta.url);

export function loadJourney(name: JourneyName): JourneyStep[] {
    const document = JSON.parse(readFileSync(JOURNEY_FILE, 'utf8')) as {
        journeys: Record<string, JourneyStep[]>;
    };
    const steps = document.journeys[name];
    if (steps === undefined) throw new Error(`the journey file has no ${name} journey`);
    return steps;
}

export async function runJourney(steps: JourneyStep[], driver: JourneyDriver): Promise<void> {
    let previousPullIds: string[] | null = null;
    for (const step of steps) {
        const result = await driver.run(step);
        const expected = step.expect;
        if (expected.session_class !== undefined) {
            assert.equal(result.sessionClass, expected.session_class, step.action);
        }
        if (expected.event_count !== undefined) {
            assert.equal(result.eventCount, expected.event_count, step.action);
        }
        if (expected.remaining_lifetime_range !== undefined) {
            const [minimum, maximum] = expected.remaining_lifetime_range;
            const lifetimes = result.remainingLifetimes ?? [];
            assert.ok(lifetimes.length > 0, `${step.action} returned no remaining lifetimes`);
            for (const lifetime of lifetimes) {
                assert.ok(
                    lifetime >= minimum && lifetime <= maximum,
                    `${step.action} lifetime ${lifetime} is outside ${minimum}..${maximum}`,
                );
            }
        }
        if (expected.key_fresh !== undefined) {
            assert.equal(result.keyFresh, expected.key_fresh, step.action);
        }
        if (expected.rejected !== undefined) {
            assert.equal(result.rejected, expected.rejected, step.action);
        }
        if (step.params['same_ids_as_previous_pull'] === true) {
            assert.ok(previousPullIds !== null, 'there is no previous pull to compare');
            const ids = result.eventIds ?? [];
            assert.equal(new Set(ids).size, ids.length, 'the second pull repeated an event id');
            for (const id of ids) {
                assert.ok(previousPullIds.includes(id), `${id} was not in the previous pull`);
            }
            assert.ok(ids.length < previousPullIds.length, 'the second pull did not shrink');
        }
        if (step.action === 'pull') previousPullIds = result.eventIds ?? [];
    }
}
