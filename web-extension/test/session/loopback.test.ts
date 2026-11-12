import { beforeAll, describe, it, expect } from 'vitest';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

import { normalizeLowS, signRbase, verifyP1363 } from '../../src/session/ecdsa';
import { decodeFrame, decodeSignatureFrame } from '../../src/session/frames';
import { ClientSessionEngine, type SessionEffect } from '../../src/session/engine';
import { openRecord, sealRecord } from '../../src/session/record';
import {
    confirmInput as confirmInputBytes,
    deriveKeyMaterial,
    importKeys,
    rbase as reconnectRbase,
    type ReconnectKeys,
} from '../../src/session/schedule';
import { Direction, FrameType, RecordType, Role, SessionClass } from '../../src/session/wire';
import { requireValidPoint } from '../../src/session/p256';
import { PEER_STATE_ACTIVE, hexOf } from './client-harness';
import { initPairingCore, PairingExecution } from '../../src/pairing/core';
import { PairingLink, type PairingTransport } from '../../src/pairing/link';
import { ActivationEngine } from '../../src/pairing/activation';
import { ProofRegistry } from '../../src/pairing/proofs';
import { bytes as trustBytes, fingerprint } from '../../src/trust/model';
import { SessionClock, createTestTrust } from '../connector/session-harness';
import { API_VERSION } from '../connector/fixtures';
import type { ConnectorResult } from '../../src/connector/port';
import type { WireEvent } from '../../src/connector/protocol';

// OTP-09 Task 10 cross-endpoint loopback: the production TypeScript client engine against
// the real Kotlin phone engine in a child JVM (`PhoneLoopbackMainKt <seed>`). Skipped when
// `VELES_LOOPBACK_CLASSPATH` is unset; `VELES_LOOPBACK_REQUIRED=1` turns that skip into a
// failure so CI cannot silently lose the check.

const CLASSPATH = process.env.VELES_LOOPBACK_CLASSPATH ?? '';
const REQUIRED = process.env.VELES_LOOPBACK_REQUIRED === '1';
const JAVA = process.env.VELES_LOOPBACK_JAVA ?? 'java';
// OTP-10: the Gradle `rustHostJni` output directory, exported by `sessionLoopbackCheck`.
// The child JVM loads the real Rust core with System.loadLibrary("veles_crypto"), so the
// directory must be on java.library.path. Left unset in a bare local run (the OTP-09
// scenarios do not touch the native core); CI always sets it.
const HOST_JNI_DIR = process.env.VELES_HOST_JNI_DIR ?? '';
const CLASS_NAME = 'me.nagaev.veles.session.loopback.PhoneLoopbackMainKt';
const TIMEOUT_MS = 30_000;

// OTP-10 Task 11: the enrollment/recovery loopback runs the same child JVM in `pairing`
// mode, where the phone hosts the production pairing machine, trust repository and
// activation engine on the real host core. The window draws a fixed code `000042`.
const PAIRING_MODE = 'pairing';
const PAIRING_CODE = '000042';
const EVENT_WINDOW = 0x03;
const EVENT_TRUST = 0x04;
const EVENT_PAIRING_STATE = 0x05;
const EVENT_AUTHENTICATED = 0x01;
const EVENT_CLOSED = 0x02;

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
const INSTALLATION_BYTES = 16;

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

interface DerivedIdentity {
    installationId: Uint8Array;
    point: Uint8Array;
    key: CryptoKey;
}

async function deriveIdentity(
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
        const scalar = toBigInt(digest);
        if (scalar > 0n && scalar < ORDER) {
            const point = multiply(scalar);
            const key = await crypto.subtle.importKey(
                'jwk',
                {
                    kty: 'EC',
                    crv: 'P-256',
                    x: base64url(point.subarray(1, 33)),
                    y: base64url(point.subarray(33, 65)),
                    d: base64url(digest),
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

interface Message {
    kind: number;
    payload: Uint8Array;
}

interface LoopbackProcess {
    child: ChildProcessWithoutNullStreams;
    send(kind: number, payload: Uint8Array): void;
    next(match: (message: Message) => boolean): Promise<Message>;
    waitForStderr(fragment: string): Promise<void>;
    stop(): void;
}

function startPhone(seed: string, mode?: string): LoopbackProcess {
    const jvmArguments = ['-cp', CLASSPATH];
    if (HOST_JNI_DIR !== '') {
        jvmArguments.push(`-Djava.library.path=${HOST_JNI_DIR}`);
    }
    const programArguments = mode === undefined ? [seed] : [seed, mode];
    const child = spawn(JAVA, [...jvmArguments, CLASS_NAME, ...programArguments], {
        stdio: ['pipe', 'pipe', 'pipe'],
    }) as ChildProcessWithoutNullStreams;
    let buffered = Buffer.alloc(0);
    const messages: Message[] = [];
    const waiters: (() => void)[] = [];
    const stderrWaiters: (() => void)[] = [];
    let stderr = '';
    child.stderr.on('data', (chunk: Buffer) => {
        stderr += chunk.toString();
        stderrWaiters.splice(0).forEach((resolve) => resolve());
    });
    child.stdout.on('data', (chunk: Buffer) => {
        buffered = Buffer.concat([buffered, chunk]);
        for (;;) {
            if (buffered.length < 5) break;
            const kind = buffered[0];
            const length = buffered.readUInt32BE(1);
            if (buffered.length < 5 + length) break;
            messages.push({ kind, payload: new Uint8Array(buffered.subarray(5, 5 + length)) });
            buffered = buffered.subarray(5 + length);
            waiters.splice(0).forEach((resolve) => resolve());
        }
    });
    child.on('exit', () => waiters.splice(0).forEach((resolve) => resolve()));
    return {
        child,
        send(kind: number, payload: Uint8Array): void {
            const frame = Buffer.alloc(5 + payload.length);
            frame[0] = kind;
            frame.writeUInt32BE(payload.length, 1);
            Buffer.from(payload).copy(frame, 5);
            child.stdin.write(frame);
        },
        async next(match: (message: Message) => boolean): Promise<Message> {
            const deadline = Date.now() + TIMEOUT_MS;
            for (;;) {
                const index = messages.findIndex(match);
                if (index >= 0) return messages.splice(index, 1)[0];
                if (child.exitCode !== null) {
                    throw new Error(`loopback exited before a matching message; stderr: ${stderr}`);
                }
                if (Date.now() > deadline) {
                    throw new Error(`loopback timed out; stderr: ${stderr}`);
                }
                await new Promise<void>((resolve) => {
                    waiters.push(resolve);
                    setTimeout(resolve, 20);
                });
            }
        },
        async waitForStderr(fragment: string): Promise<void> {
            const deadline = Date.now() + TIMEOUT_MS;
            while (!stderr.includes(fragment)) {
                if (child.exitCode !== null) {
                    throw new Error(
                        `loopback exited before stderr '${fragment}'; stderr: ${stderr}`,
                    );
                }
                if (Date.now() > deadline) {
                    throw new Error(
                        `loopback timed out waiting for stderr '${fragment}'; stderr: ${stderr}`,
                    );
                }
                await new Promise<void>((resolve) => {
                    stderrWaiters.push(resolve);
                    setTimeout(resolve, 20);
                });
            }
        },
        stop(): void {
            child.stdin.end();
            child.kill('SIGKILL');
        },
    };
}

function isFrame(message: Message, type: number): boolean {
    return message.kind === 0 && message.payload.length > 0 && message.payload[0] === type;
}

function isEvent(message: Message, code: number): boolean {
    return message.kind === 2 && message.payload.length > 0 && message.payload[0] === code;
}

function command(phone: LoopbackProcess, text: string): void {
    phone.send(1, encoder.encode(text));
}

function eventText(message: Message): string {
    return new TextDecoder().decode(message.payload.subarray(1));
}

function isProtectedRecord(message: Message): boolean {
    return (
        message.kind === 0 &&
        message.payload.length >= 23 &&
        message.payload[0] === 1 &&
        message.payload[1] === 1
    );
}

// ---------------------------------------------------------------------------------------------
// Client side of the loopback
// ---------------------------------------------------------------------------------------------

const encoder = new TextEncoder();

class LoopbackClient {
    static async create(
        client: DerivedIdentity,
        phone: DerivedIdentity,
        received: number[] = [],
        wired?: { engine: ClientSessionEngine; ephemeral: CryptoKeyPair; nonce: Uint8Array },
    ): Promise<LoopbackClient> {
        if (wired !== undefined) {
            return new LoopbackClient(
                client,
                phone,
                wired.ephemeral,
                wired.nonce,
                received,
                wired.engine,
            );
        }
        const ephemeral = await crypto.subtle.generateKey(
            { name: 'ECDH', namedCurve: 'P-256' },
            true,
            ['deriveBits'],
        );
        const nonce = crypto.getRandomValues(new Uint8Array(16));
        return new LoopbackClient(client, phone, ephemeral, nonce, received);
    }

    readonly engine: ClientSessionEngine;
    readonly ephemeral: CryptoKeyPair;
    readonly nonce: Uint8Array;
    clientHelloPayload = new Uint8Array(0);
    phoneHelloPayload = new Uint8Array(0);
    keys: ReconnectKeys | null = null;
    phoneSignature = new Uint8Array(0);
    transcript = new Uint8Array(0);
    confirmInput = new Uint8Array(0);

    private constructor(
        readonly client: DerivedIdentity,
        readonly phone: DerivedIdentity,
        ephemeral: CryptoKeyPair,
        nonce: Uint8Array,
        received: number[],
        engine?: ClientSessionEngine,
    ) {
        this.ephemeral = ephemeral;
        this.nonce = nonce;
        this.engine =
            engine ??
            new ClientSessionEngine({
                localInstallationId: client.installationId.slice(),
                sign: (message) => signRbase(client.key, message),
                expectedPeerId: hexOf(phone.installationId),
                isCurrent: () => true,
                lookupPhone: async () => ({ publicKey: phone.point, state: 'active' }),
                upgradeMirror: async () => undefined,
                now: () => 0,
                ephemeral: async () => ephemeral,
                nonce: () => nonce,
                handler: {
                    handles: (type) => received.includes(type),
                    handle: async () => undefined,
                },
            });
    }

    async start(phone: LoopbackProcess): Promise<Uint8Array> {
        const effects = await this.engine.start();
        const helloFrame = sendBytes(effects);
        const hello = decodeFrame(helloFrame);
        if (hello.type !== FrameType.RECONNECT_HELLO)
            throw new Error('the client did not start with a hello');
        this.clientHelloPayload = new Uint8Array(hello.payload);
        phone.send(0, helloFrame);
        return helloFrame;
    }

    async acceptPhoneHello(message: Message): Promise<void> {
        const frame = decodeFrame(message.payload);
        if (frame.type !== FrameType.RECONNECT_HELLO) throw new Error('not a phone hello');
        this.phoneHelloPayload = new Uint8Array(frame.payload);
        await this.engine.onInbound(message.payload);
    }

    async acceptPhoneSignature(phone: LoopbackProcess, message: Message): Promise<SessionEffect[]> {
        const frame = decodeFrame(message.payload);
        if (frame.type !== FrameType.RECONNECT_SIGNATURE) throw new Error('not a phone signature');
        const signature = decodeSignatureFrame(frame.payload, Role.PHONE).signature;
        if (signature.length !== 64) throw new Error('the phone signature is not 64 bytes');
        if (!timingSafeEqual(normalizeLowS(signature), signature))
            throw new Error('the phone signature is high-S');
        this.phoneSignature = new Uint8Array(signature);
        this.transcript = new Uint8Array(
            reconnectRbase(this.phoneHelloPayload, this.clientHelloPayload),
        );
        expect(await verifyP1363(this.phone.point, this.transcript, signature)).toBe(true);
        const effects = await this.engine.onInbound(message.payload);
        const signatureEffect = effects.find(
            (effect) =>
                effect.kind === 'send' && wireType(effect.bytes) === FrameType.RECONNECT_SIGNATURE,
        );
        const confirmEffect = effects.find(
            (effect) =>
                effect.kind === 'send' && wireType(effect.bytes) === RecordType.RECONNECT_CONFIRM,
        );
        if (signatureEffect?.kind !== 'send' || confirmEffect?.kind !== 'send') {
            throw new Error('the client did not emit its signature and confirm');
        }
        const clientSignature = decodeSignatureFrame(
            decodeFrame(signatureEffect.bytes).payload,
            Role.CLIENT,
        ).signature;
        if (clientSignature.length !== 64) throw new Error('the client signature is not 64 bytes');
        if (!timingSafeEqual(normalizeLowS(clientSignature), clientSignature)) {
            throw new Error('the client signature is high-S');
        }
        this.keys = await this.deriveKeys(this.phoneHelloPayload);
        this.confirmInput = new Uint8Array(
            await confirmInputBytes(this.transcript, this.phoneSignature, clientSignature),
        );
        phone.send(0, signatureEffect.bytes);
        phone.send(0, confirmEffect.bytes);
        if (confirmEffect.completionToken !== undefined) {
            await this.engine.onSendComplete(confirmEffect.completionToken, true);
        }
        return effects;
    }

    async acceptPhoneConfirm(message: Message): Promise<'authenticated'> {
        const effects = await this.engine.onInbound(message.payload);
        if (effects[0]?.kind !== 'authenticated')
            throw new Error('the session did not authenticate');
        return 'authenticated';
    }

    get prefix(): Uint8Array {
        const prefix = this.engine.sessionIdPrefix;
        if (prefix === null) throw new Error('no session prefix');
        return prefix;
    }

    get sessionKeys(): ReconnectKeys {
        if (this.keys === null) throw new Error('no session keys');
        return this.keys;
    }

    async open(record: Uint8Array, sequence: bigint) {
        const keys = this.sessionKeys;
        return openRecord(
            keys.p2cKey,
            keys.p2cPrefix,
            this.prefix,
            Direction.PHONE_TO_CLIENT,
            sequence,
            record,
        );
    }

    async send(phone: LoopbackProcess, type: number, body: Uint8Array): Promise<void> {
        const effects = await this.engine.sendApplication(type, body);
        for (const effect of effects) {
            if (effect.kind === 'send') phone.send(0, effect.bytes);
        }
    }

    private async deriveKeys(phoneHelloPayload: Uint8Array): Promise<ReconnectKeys> {
        const hello = decodeHelloPayload(phoneHelloPayload);
        const point = await requireValidPoint(hello.ephemeralPublicKey);
        const shared = new Uint8Array(
            await crypto.subtle.deriveBits(
                { name: 'ECDH', public: point },
                this.ephemeral.privateKey,
                256,
            ),
        );
        const material = await deriveKeyMaterial(shared, hello.nonce, this.nonce);
        const keys = await importKeys(material);
        material.c2pKey.fill(0);
        material.p2cKey.fill(0);
        shared.fill(0);
        return keys;
    }
}

// The transcript needs the phone hello payload; the engine holds it internally, so keep a
// tiny copy here when the hello is accepted.
async function acceptAndRemember(client: LoopbackClient, message: Message): Promise<Uint8Array> {
    await client.acceptPhoneHello(message);
    return decodeFrame(message.payload).payload;
}

function decodeHelloPayload(payload: Uint8Array): {
    ephemeralPublicKey: Uint8Array;
    nonce: Uint8Array;
} {
    // installation 16 || point 65 || nonce 16 || role 1 || version 2 || caps_len 2 || caps
    if (payload.length < 102) throw new Error('short phone hello');
    return {
        ephemeralPublicKey: payload.subarray(16, 81),
        nonce: payload.subarray(81, 97),
    };
}

function sendBytes(effects: SessionEffect[], index = 0): Uint8Array {
    const effect = effects[index];
    if (effect?.kind !== 'send') throw new Error(`expected a send effect at ${index}`);
    return effect.bytes;
}

function wireType(bytes: Uint8Array): number | null {
    if (bytes.length === 0) return null;
    if (bytes[0] === FrameType.RECONNECT_HELLO || bytes[0] === FrameType.RECONNECT_SIGNATURE)
        return bytes[0];
    if (bytes.length < 23) return null;
    return (bytes[3] << 8) | bytes[4];
}

function timingSafeEqual(left: Uint8Array, right: Uint8Array): boolean {
    if (left.length !== right.length) return false;
    let difference = 0;
    for (let index = 0; index < left.length; index += 1) difference |= left[index] ^ right[index];
    return difference === 0;
}

function eventAckBody(phoneInstallationId: Uint8Array, event: Uint8Array): Uint8Array {
    const body = new Uint8Array(32);
    body.set(phoneInstallationId, 0);
    body.set(event.subarray(16, 32), 16);
    return body;
}

async function handshake(phone: LoopbackProcess, seed: string): Promise<LoopbackClient> {
    const clientIdentity = await deriveIdentity(
        'Veles loopback client identity v1',
        'Veles loopback client installation v1',
        seed,
    );
    const phoneIdentity = await deriveIdentity(
        'Veles loopback phone identity v1',
        'Veles loopback phone installation v1',
        seed,
    );
    const client = await LoopbackClient.create(clientIdentity, phoneIdentity, [
        RecordType.PULL_EVENTS,
        RecordType.PUSH_EVENT,
    ]);
    await performHandshake(phone, client);
    return client;
}

/**
 * Drives any client engine through the §11.3 handshake against the loopback phone. The
 * OTP-10 recovery scenarios pass their own engine (real trust repository, real activation
 * handler) so the same wire exchange runs against the production client machine.
 */
async function performHandshake(
    phone: LoopbackProcess,
    client: LoopbackClient,
): Promise<SessionEffect[]> {
    await client.start(phone);
    const helloMessage = await phone.next((message) => isFrame(message, FrameType.RECONNECT_HELLO));
    await acceptAndRemember(client, helloMessage);
    const signatureMessage = await phone.next((message) =>
        isFrame(message, FrameType.RECONNECT_SIGNATURE),
    );
    await client.acceptPhoneSignature(phone, signatureMessage);
    const confirmMessage = await phone.next(isProtectedRecord);
    const effects = await client.engine.onInbound(confirmMessage.payload);
    const authenticated = effects.find((effect) => effect.kind === 'authenticated');
    if (authenticated?.kind !== 'authenticated')
        throw new Error('the session did not authenticate');
    const authenticatedEvent = await phone.next((message) => isEvent(message, EVENT_AUTHENTICATED));
    expect(Array.from(authenticatedEvent.payload.subarray(2, 10))).toEqual(
        Array.from(client.prefix),
    );
    // The client's own unprompted records (OTP2-STA-010b step 5) go out after the gate.
    for (const record of authenticated.unprompted ?? []) {
        await client.send(phone, record.type, record.body);
    }
    return effects;
}

// ---------------------------------------------------------------------------------------------
// OTP-10 enrollment and recovery loopback
// ---------------------------------------------------------------------------------------------

interface PhoneTrustPeer {
    installationId: string;
    publicKey: string;
    label: string;
    deviceType: number;
    enrollmentTranscript: string;
    fingerprint: string;
    state: string;
}

interface PhoneTrustSnapshot {
    identity: { installationId: string; publicKey: string };
    peers: PhoneTrustPeer[];
    openHandles: number;
}

async function phoneTrust(phone: LoopbackProcess): Promise<PhoneTrustSnapshot> {
    command(phone, 'trust');
    const message = await phone.next((candidate) => isEvent(candidate, EVENT_TRUST));
    return JSON.parse(eventText(message)) as PhoneTrustSnapshot;
}

async function phonePairingState(phone: LoopbackProcess): Promise<string> {
    command(phone, 'state');
    const message = await phone.next((candidate) => isEvent(candidate, EVENT_PAIRING_STATE));
    return eventText(message);
}

function equalBytes(left: Uint8Array, right: Uint8Array): boolean {
    if (left.length !== right.length) return false;
    let difference = 0;
    for (let index = 0; index < left.length; index += 1) difference |= left[index] ^ right[index];
    return difference === 0;
}

async function waitFor(predicate: () => boolean, what: string): Promise<void> {
    const deadline = Date.now() + TIMEOUT_MS;
    while (!predicate()) {
        if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
        await new Promise<void>((resolve) => setTimeout(resolve, 20));
    }
}

/** Reads one protected record, lets the client engine handle it and forwards every reply. */
async function receiveProtected(
    phone: LoopbackProcess,
    client: LoopbackClient,
): Promise<SessionEffect[]> {
    const message = await phone.next(isProtectedRecord);
    const effects = await client.engine.onInbound(message.payload);
    for (const effect of effects) {
        if (effect.kind === 'send') phone.send(0, effect.bytes);
    }
    return effects;
}

interface ReconnectRun {
    client: LoopbackClient;
    engine: ClientSessionEngine;
}

/**
 * Builds the production client engine for one signed reconnect over the enrolled trust
 * record: the real recovery-key derivation (from the durable `ET`), the real activation
 * handler and the real repository transition.
 */
async function reconnectClient(
    trust: Awaited<ReturnType<typeof createTestTrust>>,
    phoneIdentity: DerivedIdentity,
    clock: SessionClock,
    proofs: ProofRegistry,
): Promise<ReconnectRun> {
    const peers = await trust.repository.peers();
    if (peers.length !== 1) throw new Error(`expected one trusted phone, got ${peers.length}`);
    const peer = peers[0];
    if (peer === undefined) throw new Error('no trusted phone');
    const ephemeral = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, [
        'deriveBits',
    ]);
    const nonce = crypto.getRandomValues(new Uint8Array(16));
    const engine = new ClientSessionEngine({
        localInstallationId: trustBytes(trust.identity.installationId, 16),
        expectedPeerId: peer.installationId,
        isCurrent: () => true,
        lookupPhone: async (id) =>
            id === peer.installationId
                ? {
                      publicKey: trustBytes(peer.publicKey, 65),
                      state: peer.state,
                      et: trustBytes(peer.enrollmentTranscript, 32),
                  }
                : undefined,
        upgradeMirror: (id, proof) => trust.repository.confirm(id, 'phone_active', proof),
        now: clock.now,
        ephemeral: async () => ephemeral,
        nonce: () => nonce,
        handler: new ActivationEngine({ trust: trust.repository, proofs }),
        sign: async (message) => {
            const identity = await trust.keys.read();
            if (identity === undefined) throw new Error('identity unavailable');
            return normalizeLowS(
                new Uint8Array(
                    await crypto.subtle.sign(
                        { name: 'ECDSA', hash: 'SHA-256' },
                        identity.privateKey,
                        message,
                    ),
                ),
            );
        },
    });
    const client = await LoopbackClient.create(phoneIdentity, phoneIdentity, [], {
        engine,
        ephemeral,
        nonce,
    });
    return { client, engine };
}

async function assertMatchingRecords(
    snapshot: PhoneTrustSnapshot,
    trust: Awaited<ReturnType<typeof createTestTrust>>,
    phoneIdentity: DerivedIdentity,
    phoneState: string,
    clientState: string,
): Promise<void> {
    expect(snapshot.identity.installationId).toBe(hexOf(phoneIdentity.installationId));
    expect(snapshot.identity.publicKey).toBe(hexOf(phoneIdentity.point));
    expect(snapshot.peers).toHaveLength(1);
    const peer = snapshot.peers[0];
    expect(peer?.state).toBe(phoneState);
    expect(peer?.installationId).toBe(trust.identity.installationId);
    expect(peer?.publicKey).toBe(trust.identity.publicKey);
    expect(peer?.label).toBe('Chrome');
    expect(peer?.deviceType).toBe(0x01);
    expect(snapshot.openHandles).toBe(0);

    const peers = await trust.repository.peers();
    expect(peers).toHaveLength(1);
    const mirror = peers[0];
    expect(mirror?.state).toBe(clientState);
    expect(mirror?.installationId).toBe(hexOf(phoneIdentity.installationId));
    expect(mirror?.publicKey).toBe(hexOf(phoneIdentity.point));
    // Both stores persisted the same public `ET` and independently derive the same fingerprint.
    expect(mirror?.enrollmentTranscript).toBe(peer?.enrollmentTranscript);
    expect(mirror?.fingerprint).toBe(peer?.fingerprint);
    expect(peer?.fingerprint).toBe(
        await fingerprint(hexOf(phoneIdentity.point), trust.identity.publicKey),
    );
}

/**
 * The enrollment link over the child JVM: `connect` opens the fixed-code window,
 * `send` writes a frame, and the reader dispatches inbound frames to the link.
 */
class LoopbackPairingTransport implements PairingTransport {
    readonly connectionHandle = 'loopback-enrollment';
    /** Drops the next inbound frame (the interrupted-activation cut). */
    dropNext = false;
    private readonly listeners = new Set<
        (record: { connectionHandle: string; bytes: Uint8Array }) => void
    >();
    private readonly buffered: Uint8Array[] = [];
    private running = true;

    constructor(private readonly phone: LoopbackProcess) {
        void this.read();
    }

    async connect(): Promise<ConnectorResult<ConnectedEvent>> {
        command(this.phone, 'pair');
        await this.phone.next((message) => isEvent(message, EVENT_WINDOW));
        return {
            ok: true,
            event: {
                type: 'connected',
                envelope_version: 1,
                api_version: API_VERSION,
                request_id: 'loopback-connect',
                connection_handle: this.connectionHandle,
            },
        };
    }

    async send(_handle: string, record: Uint8Array): Promise<ConnectorResult<SentEvent>> {
        this.phone.send(0, record);
        return {
            ok: true,
            event: {
                type: 'sent',
                envelope_version: 1,
                api_version: API_VERSION,
                request_id: 'loopback-send',
                connection_handle: this.connectionHandle,
            },
        };
    }

    async disconnect(): Promise<ConnectorResult<DisconnectedEvent>> {
        this.running = false;
        return {
            ok: true,
            event: {
                type: 'disconnected',
                envelope_version: 1,
                api_version: API_VERSION,
                connection_handle: this.connectionHandle,
            },
        };
    }

    onRecord(
        listener: (record: { connectionHandle: string; bytes: Uint8Array }) => void,
    ): () => void {
        this.listeners.add(listener);
        for (const bytes of this.buffered.splice(0)) {
            listener({ connectionHandle: this.connectionHandle, bytes });
        }
        return () => {
            this.listeners.delete(listener);
        };
    }

    /** Stops dispatching; a pending read never steals a later inbound frame. */
    close(): void {
        this.running = false;
    }

    private async read(): Promise<void> {
        for (;;) {
            let message: Message;
            try {
                message = await this.phone.next(
                    (candidate) => this.running && candidate.kind === 0,
                );
            } catch {
                return;
            }
            if (!this.running) return;
            if (this.dropNext) {
                this.dropNext = false;
                continue;
            }
            const bytes = message.payload;
            if (this.listeners.size === 0) {
                this.buffered.push(bytes);
                continue;
            }
            for (const listener of [...this.listeners]) {
                listener({ connectionHandle: this.connectionHandle, bytes });
            }
        }
    }
}

type ConnectedEvent = Extract<WireEvent, { type: 'connected' }>;
type SentEvent = Extract<WireEvent, { type: 'sent' }>;
type DisconnectedEvent = Extract<WireEvent, { type: 'disconnected' }>;

// ---------------------------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------------------------

if (CLASSPATH === '' && REQUIRED) {
    describe('session loopback', () => {
        it('fails when VELES_LOOPBACK_REQUIRED is set without a classpath', () => {
            expect(
                CLASSPATH,
                'VELES_LOOPBACK_CLASSPATH is required when VELES_LOOPBACK_REQUIRED=1',
            ).not.toBe('');
        });
    });
} else if (CLASSPATH === '') {
    describe.skip('session loopback (VELES_LOOPBACK_CLASSPATH unset)', () => {
        it('skips without a Kotlin classpath', () => undefined);
    });
} else {
    describe('session loopback', () => {
        beforeAll(async () => {
            await initPairingCore(
                readFileSync(new URL('../../rust-wasm/pkg/veles_crypto_bg.wasm', import.meta.url)),
            );
        });

        it(
            'handshake, heartbeat, pull/ack, push and app_error cross endpoints',
            async () => {
                const seed = 'otp09-loopback-core';
                const phone = startPhone(seed);
                try {
                    const client = await handshake(phone, seed);

                    // Heartbeat: the first client heartbeat is allowed and the phone processes it.
                    await client.send(phone, RecordType.APP_HEARTBEAT, new Uint8Array(0));

                    // Pull/ack: the phone answers one pull with one event; the client acks it.
                    await client.send(phone, RecordType.PULL_REQUEST, new Uint8Array(0));
                    const pullMessage = await phone.next(isProtectedRecord);
                    const pull = await client.open(
                        pullMessage.payload,
                        client.engine._inboundSequence,
                    );
                    expect(pull.type).toBe(RecordType.PULL_EVENTS);
                    expect(pull.body[0]).toBe(1);
                    expect(pull.seq).toBe(client.engine._inboundSequence);
                    await client.engine.onInbound(pullMessage.payload);
                    const pulledEvent = pull.body.subarray(1, 1 + 79);
                    await client.send(
                        phone,
                        RecordType.EVENT_ACK,
                        eventAckBody(client.phone.installationId, pulledEvent),
                    );
                    await phone.waitForStderr('loopback: settled');

                    // Push: the phone sends one push event on the command channel; the client acks it.
                    phone.send(1, encoder.encode('push'));
                    const pushMessage = await phone.next(isProtectedRecord);
                    const push = await client.open(
                        pushMessage.payload,
                        client.engine._inboundSequence,
                    );
                    expect(push.type).toBe(RecordType.PUSH_EVENT);
                    await client.engine.onInbound(pushMessage.payload);
                    await client.send(
                        phone,
                        RecordType.EVENT_ACK,
                        eventAckBody(client.phone.installationId, push.body),
                    );

                    // app_error: the phone closes on a terminal application error.
                    const closingPrefix = client.prefix.slice();
                    await client.send(phone, RecordType.APP_ERROR, Uint8Array.of(1, 0, 0));
                    const closed = await phone.next((message) => isEvent(message, EVENT_CLOSED));
                    expect(Array.from(closed.payload.subarray(1, 9))).toEqual(
                        Array.from(closingPrefix),
                    );
                } finally {
                    phone.stop();
                }
            },
            TIMEOUT_MS,
        );

        it(
            'two reconnects have distinct session ids and keys',
            async () => {
                const seed = 'otp09-loopback-reconnect';
                const phone = startPhone(seed);
                try {
                    const first = await handshake(phone, seed);
                    const firstPrefix = first.prefix.slice();
                    const firstKeys = first.sessionKeys;
                    const firstC2pPrefix = new Uint8Array(firstKeys.c2pPrefix);
                    const firstP2cPrefix = new Uint8Array(firstKeys.p2cPrefix);
                    const firstSessionId = new Uint8Array(firstKeys.sessionId);
                    const firstClientConfirm = new Uint8Array(firstKeys.clientConfirmKey);
                    const firstPhoneConfirm = new Uint8Array(firstKeys.phoneConfirmKey);

                    phone.send(1, encoder.encode('reset'));
                    await phone.next((message) => isEvent(message, EVENT_CLOSED));
                    const second = await handshake(phone, seed);
                    const secondKeys = second.sessionKeys;

                    expect(Array.from(second.prefix)).not.toEqual(Array.from(firstPrefix));
                    expect(Array.from(secondKeys.sessionId)).not.toEqual(
                        Array.from(firstSessionId),
                    );
                    expect(Array.from(secondKeys.c2pPrefix)).not.toEqual(
                        Array.from(firstC2pPrefix),
                    );
                    expect(Array.from(secondKeys.p2cPrefix)).not.toEqual(
                        Array.from(firstP2cPrefix),
                    );
                    expect(Array.from(secondKeys.clientConfirmKey)).not.toEqual(
                        Array.from(firstClientConfirm),
                    );
                    expect(Array.from(secondKeys.phoneConfirmKey)).not.toEqual(
                        Array.from(firstPhoneConfirm),
                    );

                    // The AES keys are non-extractable, so key freshness is proved by swapping
                    // only the key while holding the originating record's own nonce prefix and
                    // session prefix constant: the header/session checks pass and GCM must fail.
                    // A reused key would decrypt and the test would fail.
                    const firstP2cRecord = await sealRecord(
                        firstKeys.p2cKey,
                        firstKeys.p2cPrefix,
                        first.prefix,
                        Direction.PHONE_TO_CLIENT,
                        RecordType.PUSH_EVENT,
                        1n,
                        new Uint8Array(0),
                    );
                    await expect(
                        openRecord(
                            firstKeys.p2cKey,
                            firstKeys.p2cPrefix,
                            first.prefix,
                            Direction.PHONE_TO_CLIENT,
                            1n,
                            firstP2cRecord,
                        ),
                    ).resolves.toMatchObject({ type: RecordType.PUSH_EVENT });
                    await expect(
                        openRecord(
                            secondKeys.p2cKey,
                            firstKeys.p2cPrefix,
                            first.prefix,
                            Direction.PHONE_TO_CLIENT,
                            1n,
                            firstP2cRecord,
                        ),
                    ).rejects.toThrow();
                    const secondP2cRecord = await sealRecord(
                        secondKeys.p2cKey,
                        secondKeys.p2cPrefix,
                        second.prefix,
                        Direction.PHONE_TO_CLIENT,
                        RecordType.PUSH_EVENT,
                        1n,
                        new Uint8Array(0),
                    );
                    await expect(
                        openRecord(
                            secondKeys.p2cKey,
                            secondKeys.p2cPrefix,
                            second.prefix,
                            Direction.PHONE_TO_CLIENT,
                            1n,
                            secondP2cRecord,
                        ),
                    ).resolves.toMatchObject({ type: RecordType.PUSH_EVENT });
                    await expect(
                        openRecord(
                            firstKeys.p2cKey,
                            secondKeys.p2cPrefix,
                            second.prefix,
                            Direction.PHONE_TO_CLIENT,
                            1n,
                            secondP2cRecord,
                        ),
                    ).rejects.toThrow();
                    const firstC2pRecord = await sealRecord(
                        firstKeys.c2pKey,
                        firstKeys.c2pPrefix,
                        first.prefix,
                        Direction.CLIENT_TO_PHONE,
                        RecordType.PULL_REQUEST,
                        1n,
                        new Uint8Array(0),
                    );
                    await expect(
                        openRecord(
                            firstKeys.c2pKey,
                            firstKeys.c2pPrefix,
                            first.prefix,
                            Direction.CLIENT_TO_PHONE,
                            1n,
                            firstC2pRecord,
                        ),
                    ).resolves.toMatchObject({ type: RecordType.PULL_REQUEST });
                    await expect(
                        openRecord(
                            secondKeys.c2pKey,
                            firstKeys.c2pPrefix,
                            first.prefix,
                            Direction.CLIENT_TO_PHONE,
                            1n,
                            firstC2pRecord,
                        ),
                    ).rejects.toThrow();
                    const secondC2pRecord = await sealRecord(
                        secondKeys.c2pKey,
                        secondKeys.c2pPrefix,
                        second.prefix,
                        Direction.CLIENT_TO_PHONE,
                        RecordType.PULL_REQUEST,
                        1n,
                        new Uint8Array(0),
                    );
                    await expect(
                        openRecord(
                            secondKeys.c2pKey,
                            secondKeys.c2pPrefix,
                            second.prefix,
                            Direction.CLIENT_TO_PHONE,
                            1n,
                            secondC2pRecord,
                        ),
                    ).resolves.toMatchObject({ type: RecordType.PULL_REQUEST });
                    await expect(
                        openRecord(
                            firstKeys.c2pKey,
                            secondKeys.c2pPrefix,
                            second.prefix,
                            Direction.CLIENT_TO_PHONE,
                            1n,
                            secondC2pRecord,
                        ),
                    ).rejects.toThrow();
                } finally {
                    phone.stop();
                }
            },
            TIMEOUT_MS,
        );

        it(
            'Kotlin signatures verify in TypeScript and TypeScript signatures are low-S',
            async () => {
                const seed = 'otp09-loopback-signatures';
                const phone = startPhone(seed);
                try {
                    const client = await handshake(phone, seed);
                    expect(client.phoneSignature).toHaveLength(64);
                    expect(Array.from(normalizeLowS(client.phoneSignature))).toEqual(
                        Array.from(client.phoneSignature),
                    );
                    expect(
                        await verifyP1363(
                            client.phone.point,
                            client.transcript,
                            client.phoneSignature,
                        ),
                    ).toBe(true);
                    // The Kotlin phone only emitted its confirm after verifying the client's
                    // TypeScript signature, so `handshake` authenticating is the reverse proof.
                    expect(client.engine.isOpen).toBe(true);
                } finally {
                    phone.stop();
                }
            },
            TIMEOUT_MS,
        );

        it(
            'enrollment_full: the phone-hosted machine and the production client reach active',
            async () => {
                const seed = 'otp10-loopback-enrollment';
                const phone = startPhone(seed, PAIRING_MODE);
                const transport = new LoopbackPairingTransport(phone);
                try {
                    const phoneIdentity = await deriveIdentity(
                        'Veles loopback phone identity v1',
                        'Veles loopback phone installation v1',
                        seed,
                    );
                    const clock = new SessionClock();
                    const trust = await createTestTrust(clock);
                    const proofs = new ProofRegistry();
                    const mirror: { engine?: ClientSessionEngine } = {};
                    trust.bindVerifier(async (peer, stage, proof) => {
                        if (stage === 'complete') {
                            return proofs.consume(peer.installationId, 'complete', proof);
                        }
                        // Production stage binding: only the verified confirm that reported
                        // the phone active may upgrade the durable mirror.
                        const confirm = mirror.engine?.verifiedPhoneConfirm ?? null;
                        return (
                            stage === 'phone_active' &&
                            confirm !== null &&
                            mirror.engine?.verifiedPeerState === PEER_STATE_ACTIVE &&
                            equalBytes(confirm, proof)
                        );
                    });

                    const link = await PairingLink.start({
                        controller: transport,
                        repo: trust.repository,
                        proofs,
                        code: PAIRING_CODE,
                        deviceHandle: 'loopback-phone',
                        clock: clock.now,
                        tickIntervalMs: 0,
                        init: async () => undefined,
                        core: PairingExecution,
                    });

                    // The fixed code in the window must verify the SPAKE2 confirmation.
                    await waitFor(
                        () => link.machine.state === 'verified_prepare_ready',
                        'a verified enrollment prepare',
                    );
                    await transport.send(link.connectionHandle, await link.machine.commit());
                    await link.settled();
                    await waitFor(() => link.machine.closed, 'the enrollment connection to close');
                    expect(link.machine.failed).toBe(false);
                    expect(link.machine.fingerprint()).toMatch(
                        /^[0-9A-F]{4} [0-9A-F]{4} [0-9A-F]{4}$/,
                    );
                    expect(await phonePairingState(phone)).toBe('ACTIVE');
                    await assertMatchingRecords(
                        await phoneTrust(phone),
                        trust,
                        phoneIdentity,
                        'active',
                        'active_activation_pending',
                    );

                    // A signed reconnect without a code upgrades the mirror; both stores end active.
                    const run = await reconnectClient(trust, phoneIdentity, clock, proofs);
                    mirror.engine = run.engine;
                    command(phone, 'reconnect');
                    await performHandshake(phone, run.client);
                    expect(run.engine.sessionClass).toBe(SessionClass.FULL);
                    await assertMatchingRecords(
                        await phoneTrust(phone),
                        trust,
                        phoneIdentity,
                        'active',
                        'active',
                    );
                } finally {
                    transport.close();
                    phone.stop();
                }
            },
            TIMEOUT_MS * 3,
        );

        it(
            'interrupted_activation_resumes: a lost activation_complete resumes through recovery',
            async () => {
                const seed = 'otp10-loopback-interrupted';
                const phone = startPhone(seed, PAIRING_MODE);
                const transport = new LoopbackPairingTransport(phone);
                try {
                    const phoneIdentity = await deriveIdentity(
                        'Veles loopback phone identity v1',
                        'Veles loopback phone installation v1',
                        seed,
                    );
                    const clock = new SessionClock();
                    const trust = await createTestTrust(clock);
                    const proofs = new ProofRegistry();
                    const mirror: { engine?: ClientSessionEngine } = {};
                    trust.bindVerifier(async (peer, stage, proof) => {
                        if (stage === 'complete') {
                            return proofs.consume(peer.installationId, 'complete', proof);
                        }
                        const confirm = mirror.engine?.verifiedPhoneConfirm ?? null;
                        return (
                            stage === 'phone_active' &&
                            confirm !== null &&
                            mirror.engine?.verifiedPeerState === PEER_STATE_ACTIVE &&
                            equalBytes(confirm, proof)
                        );
                    });

                    const link = await PairingLink.start({
                        controller: transport,
                        repo: trust.repository,
                        proofs,
                        code: PAIRING_CODE,
                        deviceHandle: 'loopback-phone',
                        clock: clock.now,
                        tickIntervalMs: 0,
                        init: async () => undefined,
                        core: PairingExecution,
                    });

                    await waitFor(
                        () => link.machine.state === 'verified_prepare_ready',
                        'a verified enrollment prepare',
                    );
                    // The phone verifies the commit and releases activation_complete; the link
                    // drops that record, so the connection dies mid-activation.
                    transport.dropNext = true;
                    await transport.send(link.connectionHandle, await link.machine.commit());
                    await waitFor(
                        () => link.machine.state === 'awaiting_complete',
                        'the client to wait for activation_complete',
                    );
                    expect(await phonePairingState(phone)).toBe('COMMITTED_WAITING_ACK');
                    await link.abandon();
                    command(phone, 'disconnect');
                    expect(await phonePairingState(phone)).toBe('IDLE');

                    // Both sides keep the durable incomplete record for the signed recovery.
                    expect((await trust.repository.peers())[0]?.state).toBe('pending');
                    expect((await phoneTrust(phone)).peers[0]?.state).toBe('committed_waiting_ack');

                    // Recovery: the phone owes enrollment_prepare and the pending client re-commits.
                    const recovery = await reconnectClient(trust, phoneIdentity, clock, proofs);
                    mirror.engine = recovery.engine;
                    command(phone, 'reconnect');
                    await performHandshake(phone, recovery.client);
                    expect(recovery.engine.sessionClass).toBe(SessionClass.ACTIVATION_RECOVERY);
                    const prepareEffects = await receiveProtected(phone, recovery.client);
                    expect(prepareEffects.some((effect) => effect.kind === 'send')).toBe(true);
                    const completeEffects = await receiveProtected(phone, recovery.client);
                    expect(completeEffects.some((effect) => effect.kind === 'send')).toBe(true);
                    await assertMatchingRecords(
                        await phoneTrust(phone),
                        trust,
                        phoneIdentity,
                        'active',
                        'active_activation_pending',
                    );

                    // The first full reconnect after recovery promotes the mirror on the active proof.
                    const full = await reconnectClient(trust, phoneIdentity, clock, proofs);
                    mirror.engine = full.engine;
                    command(phone, 'reconnect');
                    await performHandshake(phone, full.client);
                    expect(full.engine.sessionClass).toBe(SessionClass.FULL);
                    await assertMatchingRecords(
                        await phoneTrust(phone),
                        trust,
                        phoneIdentity,
                        'active',
                        'active',
                    );
                } finally {
                    transport.close();
                    phone.stop();
                }
            },
            TIMEOUT_MS * 3,
        );
    });
}
