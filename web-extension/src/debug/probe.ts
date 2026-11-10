import type { ConnectorApp, ConnectorSessionOptions } from '../connector';
import type { SessionController, SessionManager } from '../connector/sessions';
import type { ApplicationHandler } from '../session/engine';
import { bytes, hex, type InstallationIdentity } from '../trust/model';
import type { ChromeTrustRepository } from '../trust/repository';
import { injectActivePeer } from './inject';
import { createDebugReadout, type DebugReadout } from './readout';

/** One strictly decoded §8.1 synthetic event. */
export interface ProbeEvent {
    phoneInstallationId: string;
    eventId: string;
    receiptEpochSeconds: number;
    remainingLifetime: number;
    code: string;
    merchant: string;
    amountMagnitude: number;
    amountScale: number;
    currency: string;
    sessionIdPrefix: string;
}

/** The development-only connector hook. It is absent from production bundles. */
export interface VelesDebug {
    exportIdentity(): Promise<InstallationIdentity>;
    connect(deviceHandle: string, peerId: string): Promise<void>;
    injectPeer(id: string, pub: string): Promise<void>;
    pull(handle?: string): Promise<ProbeEvent>;
}

declare global {
    var velesDebug: VelesDebug | undefined;
}

export interface DevelopmentProbe {
    /** Wraps the connector's session wiring with the probe's application handler. */
    sessionOptions(options: ConnectorSessionOptions): ConnectorSessionOptions;
    /** Installs `globalThis.velesDebug` and the debug readout after the app is constructed. */
    install(app: ConnectorApp, sessions: SessionManager, controller: SessionController): void;
    dispose(): void;
}

export interface DevelopmentProbeOptions {
    /**
     * Builds a repository over the SAME durable identity store and lock with an accept-all
     * verifier. The connector's own repository must never receive a permissive verifier.
     */
    createDebugRepository?: () => ChromeTrustRepository;
}

// §7/§8.1 wire numbers are frozen. The probe repeats only the four constants it needs, so
// the development chunk never imports the connector's entry module (which would force the
// production entry chunk into a hashed shared chunk).
const PROBE_TYPE = {
    PULL_REQUEST: 0x0200,
    PULL_EVENTS: 0x0202,
    PUSH_EVENT: 0x0203,
    EVENT_ACK: 0x0204,
} as const;

const SESSION_PREFIX_START = 15;
const SESSION_PREFIX_END = 23;
const EVENT_ID_BYTES = 16;
const INSTALLATION_ID_BYTES = 16;
const MAX_LIFETIME_SECONDS = 600;
const MAX_CODE_BYTES = 16;
const MAX_MERCHANT_BYTES = 64;
const MAX_AMOUNT_SCALE = 8;
const CURRENCY_BYTES = 3;
const utf8 = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });

interface PendingPull {
    resolve(event: ProbeEvent): void;
    reject(error: Error): void;
}

/** A malformed synthetic record ends the session (the engine fails closed on handler errors). */
function reject(): never {
    throw new Error('debug probe: malformed synthetic event');
}

class Cursor {
    private offset = 0;

    constructor(private readonly value: Uint8Array) {}

    u8(): number {
        if (this.remaining() < 1) reject();
        return this.value[this.offset++] ?? 0;
    }

    u16(): number {
        return (this.u8() << 8) | this.u8();
    }

    u32(): number {
        let result = 0;
        for (let index = 0; index < 4; index += 1) result = result * 256 + this.u8();
        return result;
    }

    u64(): number {
        let result = 0n;
        for (let index = 0; index < 8; index += 1) result = (result << 8n) | BigInt(this.u8());
        return Number(result);
    }

    take(size: number): Uint8Array {
        if (this.remaining() < size) reject();
        return this.value.slice(this.offset, (this.offset += size));
    }

    remaining(): number {
        return this.value.length - this.offset;
    }

    end(): void {
        if (this.remaining() !== 0) reject();
    }
}

function decodeText(value: Uint8Array, max: number, allowEmpty: boolean): string {
    if (value.length > max || (!allowEmpty && value.length === 0)) reject();
    let text: string;
    try {
        text = utf8.decode(value);
    } catch {
        reject();
    }
    if (Array.from(text).some((char) => char.charCodeAt(0) < 0x20 || char.charCodeAt(0) === 0x7f)) {
        reject();
    }
    return text;
}

function decodeCurrency(value: Uint8Array): string {
    if (value.length !== CURRENCY_BYTES) reject();
    const text = String.fromCharCode(...value);
    if (!/^[A-Z]{3}$/.test(text)) reject();
    return text;
}

function decodeEvent(cursor: Cursor, expectedPeerId: string, sessionIdPrefix: string): ProbeEvent {
    const phoneInstallationId = hex(cursor.take(INSTALLATION_ID_BYTES));
    if (phoneInstallationId !== expectedPeerId) reject();
    const eventId = hex(cursor.take(EVENT_ID_BYTES));
    const receiptEpochSeconds = cursor.u64();
    const remainingLifetime = cursor.u32();
    if (remainingLifetime > MAX_LIFETIME_SECONDS) reject();
    const code = decodeText(cursor.take(cursor.u16()), MAX_CODE_BYTES, false);
    const merchant = decodeText(cursor.take(cursor.u16()), MAX_MERCHANT_BYTES, true);
    const amountMagnitude = cursor.u64();
    const amountScale = cursor.u8();
    if (amountScale > MAX_AMOUNT_SCALE) reject();
    const currency = decodeCurrency(cursor.take(CURRENCY_BYTES));
    return {
        phoneInstallationId,
        eventId,
        receiptEpochSeconds,
        remainingLifetime,
        code,
        merchant,
        amountMagnitude,
        amountScale,
        currency,
        sessionIdPrefix,
    };
}

/** Strict §8.1 decode of exactly one synthetic event from `pull_events` or `push_event`. */
export function decodeProbeEvent(
    type: number,
    body: Uint8Array,
    expectedPeerId: string,
    sessionIdPrefix: string,
): ProbeEvent {
    const cursor = new Cursor(body);
    if (type === PROBE_TYPE.PULL_EVENTS) {
        if (cursor.u8() !== 1) reject();
    } else if (type !== PROBE_TYPE.PUSH_EVENT) {
        reject();
    }
    const event = decodeEvent(cursor, expectedPeerId, sessionIdPrefix);
    cursor.end();
    return event;
}

function ackBody(event: ProbeEvent): Uint8Array {
    const body = new Uint8Array(INSTALLATION_ID_BYTES + EVENT_ID_BYTES);
    body.set(bytes(event.phoneInstallationId, INSTALLATION_ID_BYTES));
    body.set(bytes(event.eventId, EVENT_ID_BYTES), INSTALLATION_ID_BYTES);
    return body;
}

function requireValue<T>(value: T | null, label: string): T {
    if (value === null) throw new Error(`debug probe: ${label} is not installed`);
    return value;
}

/**
 * Development-channel probe (design D5). Installed only when `__VELES_CHANNEL__` is
 * `development`; the production build folds the guard and never bundles this module.
 */
export function createDevelopmentProbe(
    document: Document,
    options: DevelopmentProbeOptions = {},
): DevelopmentProbe {
    const readout: DebugReadout = createDebugReadout(document);
    const handles = new Map<string, string>();
    const prefixes = new Map<string, string>();
    const pending = new Map<string, PendingPull>();
    const unsubscribers: (() => void)[] = [];
    let makeRepository: ConnectorSessionOptions['createRepository'] | null = null;
    let debugRepository: ChromeTrustRepository | null = null;
    let app: ConnectorApp | null = null;
    let sessions: SessionManager | null = null;
    let installed = false;

    function rejectPull(handle: string, reason: string): void {
        const waiter = pending.get(handle);
        if (waiter === undefined) return;
        pending.delete(handle);
        waiter.reject(new Error(`debug probe: ${reason}`));
    }

    function forget(handle: string): void {
        handles.delete(handle);
        prefixes.delete(handle);
        rejectPull(handle, 'session ended');
    }

    function probeHandler(handle: string, peerId: string): ApplicationHandler {
        return {
            handles: (type) => type === PROBE_TYPE.PULL_EVENTS || type === PROBE_TYPE.PUSH_EVENT,
            handle: async (type, body, reply) => {
                let event: ProbeEvent;
                try {
                    event = decodeProbeEvent(type, body, peerId, prefixes.get(handle) ?? '');
                } catch (error) {
                    readout.clear();
                    throw error;
                }
                await reply(PROBE_TYPE.EVENT_ACK, ackBody(event));
                readout.show({
                    marker: event.code,
                    eventId: event.eventId,
                    sessionIdPrefix: event.sessionIdPrefix,
                });
                if (type === PROBE_TYPE.PULL_EVENTS) {
                    const waiter = pending.get(handle);
                    pending.delete(handle);
                    waiter?.resolve(event);
                }
            },
        };
    }

    function requireRepository(): ChromeTrustRepository {
        if (debugRepository !== null) return debugRepository;
        if (options.createDebugRepository !== undefined) {
            debugRepository = options.createDebugRepository();
        } else {
            const factory = requireValue(makeRepository, 'repository factory');
            debugRepository = factory(async () => true);
        }
        return debugRepository;
    }

    function requireSessions(): SessionManager {
        return requireValue(sessions, 'session manager');
    }

    function targetHandle(handle: string | undefined): string {
        if (handle !== undefined) {
            if (!handles.has(handle)) throw new Error(`debug probe: unknown session ${handle}`);
            return handle;
        }
        const open = [...handles.keys()];
        if (open.length !== 1) {
            throw new Error(
                `debug probe: expected exactly one open full session, found ${open.length}`,
            );
        }
        return open[0];
    }

    const hook: VelesDebug = {
        async exportIdentity() {
            return requireSessions().repository.identity();
        },
        async connect(deviceHandle, peerId) {
            if (app === null) throw new Error('debug probe: connector is not installed');
            await app.handleAction({ type: 'connect', deviceHandle, peerId });
        },
        async injectPeer(id, pub) {
            await injectActivePeer(requireRepository(), id, pub);
        },
        async pull(handle) {
            const manager = requireSessions();
            const target = targetHandle(handle);
            if (pending.has(target)) throw new Error('debug probe: a pull is already outstanding');
            let resolve!: (event: ProbeEvent) => void;
            let reject!: (error: Error) => void;
            const promise = new Promise<ProbeEvent>((settle, fail) => {
                resolve = settle;
                reject = fail;
            });
            pending.set(target, { resolve, reject });
            try {
                await manager.sendApplication(target, PROBE_TYPE.PULL_REQUEST, new Uint8Array(0));
            } catch (error) {
                pending.delete(target);
                throw error;
            }
            return promise;
        },
    };

    return {
        sessionOptions(base) {
            makeRepository = base.createRepository;
            return {
                createRepository(verifier) {
                    return base.createRepository(verifier);
                },
                createEngineDeps(handle, peerId, localInstallationId, repository) {
                    handles.set(handle, peerId);
                    return {
                        ...base.createEngineDeps(handle, peerId, localInstallationId, repository),
                        handler: probeHandler(handle, peerId),
                    };
                },
            };
        },

        install(application, manager, source) {
            if (installed) return;
            installed = true;
            app = application;
            sessions = manager;
            unsubscribers.push(
                source.onRecord((record) => {
                    if (record.bytes.length < SESSION_PREFIX_END) return;
                    prefixes.set(
                        record.connectionHandle,
                        hex(record.bytes.slice(SESSION_PREFIX_START, SESSION_PREFIX_END)),
                    );
                }),
            );
            unsubscribers.push(
                source.onChange((snapshot) => {
                    for (const handle of [...handles.keys()]) {
                        const connection = snapshot.connections.find(
                            (candidate) => candidate.handle === handle,
                        );
                        if (connection === undefined || connection.status !== 'connected') {
                            forget(handle);
                        }
                    }
                }),
            );
            unsubscribers.push(
                source.onClose(() => {
                    for (const handle of [...pending.keys()]) rejectPull(handle, 'port closed');
                    handles.clear();
                    prefixes.clear();
                }),
            );
            globalThis.velesDebug = hook;
        },

        dispose() {
            for (const unsubscribe of unsubscribers) unsubscribe();
            unsubscribers.length = 0;
            for (const handle of [...pending.keys()]) rejectPull(handle, 'probe disposed');
            handles.clear();
            prefixes.clear();
            app = null;
            sessions = null;
            if (globalThis.velesDebug === hook) delete globalThis.velesDebug;
        },
    };
}
