// Native Messaging port lifecycle for the Veles connector.
//
// One instance owns one Native Messaging port. It performs neither BLE work
// nor state-store mutation: it validates the envelope, correlates terminal
// events, bounds outstanding requests, and synthesizes connector-only
// failures. Record payloads are passed to subscribers and never retained.

import {
    ENVELOPE_VERSION,
    HOST_NAME,
    SUPPORTED_API_VERSIONS,
    VELES_SERVICE_UUID,
    isApiVersion,
    isCanonicalBase64,
    isHandle,
    isPlainObject,
    isPlatformHint,
    isRequestId,
    isServiceUuid,
    isSupportedApiVersion,
    validateInbound,
    type PendingKind,
    type ValidationContext,
    type WireEvent,
} from './protocol';
import {
    synthesizeCapExceeded,
    synthesizeIncompatibleApi,
    synthesizeInternal,
    synthesizeMissingHost,
    synthesizePortClosed,
    synthesizeProtocolViolation,
    synthesizeTimeout,
    type ConnectorFailure,
} from './errors';

export type { ConnectorFailure, ConnectorFailureCode } from './errors';

export interface NativePortLike {
    postMessage(message: unknown): void;
    disconnect(): void;
    onMessage: { addListener(listener: (message: unknown) => void): void };
    onDisconnect: { addListener(listener: () => void): void };
}

export type WireErrorEvent = Extract<WireEvent, { type: 'error' }>;

export type ConnectorResult<T extends WireEvent> =
    | { ok: true; event: T }
    | { ok: false; source: 'wire'; error: WireErrorEvent }
    | { ok: false; source: 'local'; error: ConnectorFailure };

export type ConnectorCloseReason = 'missing_host' | 'port_lost' | 'protocol' | 'user' | 'shutdown';

export interface ConnectorClose {
    generation: number;
    reason: ConnectorCloseReason;
    intentional: boolean;
}

export interface NativeConnectorPortDeps {
    connectNative: (hostName: string) => NativePortLike;
    lastError?: () => { message?: string } | undefined;
    validationContext: () => ValidationContext;
    /**
     * Called synchronously with the monotonic request id chosen by the port.
     * The controller uses this to bind scan/connect state generations without
     * ever supplying an id itself (caller id injection could reuse an old
     * identity and is deliberately impossible).
     */
    onRequestIssued?: (requestId: string, kind: PendingKind) => void;
    /**
     * Test/tooling seam for the monotonic counter start. It cannot inject an
     * id: the port still emits `c<base36 counter>` and refuses at the safe
     * integer ceiling.
     */
    requestIdStart?: number;
    now?: () => number;
    setTimeout?: (fn: () => void, ms: number) => unknown;
    clearTimeout?: (handle: unknown) => void;
    requestTimeoutMs?: number;
}

type CloseIntent = 'user' | 'shutdown' | 'protocol';

interface PendingEntry {
    requestId: string;
    kind: PendingKind;
    handle?: string;
    generation?: number;
    portGeneration: number;
    resolve: (result: ConnectorResult<WireEvent>) => void;
    timer?: unknown;
}

const PENDING_MAX = 64;
const TOMBSTONE_MAX = 256;
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;
const MAX_REQUEST_NUMBER = Number.MAX_SAFE_INTEGER;
const RESERVED_REQUEST_FIELDS: ReadonlySet<string> = new Set([
    'envelope_version',
    'type',
    'request_id',
    'api_version',
]);

class BoundedIdSet {
    private readonly values = new Map<string, true>();

    constructor(private readonly maxItems: number) {}

    add(value: string): void {
        if (this.values.has(value)) this.values.delete(value);
        this.values.set(value, true);
        while (this.values.size > this.maxItems) {
            const oldest = this.values.keys().next().value;
            if (oldest === undefined) return;
            this.values.delete(oldest);
        }
    }

    has(value: string): boolean {
        return this.values.has(value);
    }

    clear(): void {
        this.values.clear();
    }
}

function hasOnlyFields(
    fields: Record<string, unknown>,
    required: readonly string[],
    optional: readonly string[] = [],
): boolean {
    const allowed = new Set([...required, ...optional]);
    const keys = Object.keys(fields);
    if (keys.length < required.length || keys.length > allowed.size) return false;
    if (!required.every((key) => Object.hasOwn(fields, key))) return false;
    for (const key of keys) {
        if (!allowed.has(key)) return false;
        const value = fields[key];
        if (value === null || value === undefined) return false;
    }
    return true;
}

function validApiOffer(fields: Record<string, unknown>): boolean {
    const hasVersions = Object.hasOwn(fields, 'api_versions');
    const hasRange = Object.hasOwn(fields, 'api_version_range');
    if (hasVersions === hasRange) return false;

    if (hasVersions) {
        const versions = fields.api_versions;
        if (!Array.isArray(versions) || versions.length < 1 || versions.length > 16) return false;
        const seen = new Set<number>();
        let previous = 0;
        for (const version of versions) {
            if (!isApiVersion(version) || !isSupportedApiVersion(version)) return false;
            if (version <= previous) return false;
            if (seen.has(version)) return false;
            seen.add(version);
            previous = version;
        }
        return true;
    }

    const range = fields.api_version_range;
    if (!isPlainObject(range) || !hasOnlyFields(range, ['min', 'max'])) return false;
    const min = range.min;
    const max = range.max;
    if (!isApiVersion(min) || !isApiVersion(max)) return false;
    if (!isSupportedApiVersion(min) || !isSupportedApiVersion(max)) return false;
    return min <= max;
}

function validateRequestFields(
    kind: PendingKind,
    fields: Record<string, unknown>,
): { ok: true } | { ok: false } {
    if (!isPlainObject(fields)) return { ok: false };
    for (const key of Object.keys(fields)) {
        if (RESERVED_REQUEST_FIELDS.has(key)) return { ok: false };
        const value = fields[key];
        if (value === null || value === undefined) return { ok: false };
    }

    switch (kind) {
        case 'hello':
            if (
                !hasOnlyFields(fields, [], ['api_versions', 'api_version_range']) ||
                !validApiOffer(fields)
            ) {
                return { ok: false };
            }
            return { ok: true };
        case 'start_scan':
            if (
                !hasOnlyFields(fields, ['service_uuid']) ||
                !isServiceUuid(fields.service_uuid) ||
                fields.service_uuid !== VELES_SERVICE_UUID
            ) {
                return { ok: false };
            }
            return { ok: true };
        case 'stop_scan':
            if (!hasOnlyFields(fields, ['scan_handle']) || !isHandle(fields.scan_handle)) {
                return { ok: false };
            }
            return { ok: true };
        case 'connect': {
            const hasDevice = Object.hasOwn(fields, 'device_handle');
            const hasHint = Object.hasOwn(fields, 'platform_hint');
            if (hasDevice === hasHint) return { ok: false };
            if (!hasOnlyFields(fields, [], ['device_handle', 'platform_hint']))
                return { ok: false };
            if (hasDevice) return isHandle(fields.device_handle) ? { ok: true } : { ok: false };
            return isPlatformHint(fields.platform_hint) ? { ok: true } : { ok: false };
        }
        case 'disconnect':
            if (
                !hasOnlyFields(fields, ['connection_handle']) ||
                !isHandle(fields.connection_handle)
            ) {
                return { ok: false };
            }
            return { ok: true };
        case 'send':
            if (
                !hasOnlyFields(fields, ['connection_handle', 'record_base64']) ||
                !isHandle(fields.connection_handle) ||
                !isCanonicalBase64(fields.record_base64, 1, 5120)
            ) {
                return { ok: false };
            }
            return { ok: true };
        case 'shutdown':
            return hasOnlyFields(fields, []) ? { ok: true } : { ok: false };
    }
}

function operationHandle(kind: PendingKind, fields: Record<string, unknown>): string | undefined {
    if (kind === 'stop_scan') {
        return typeof fields.scan_handle === 'string' ? fields.scan_handle : undefined;
    }
    if (kind === 'send' || kind === 'disconnect') {
        return typeof fields.connection_handle === 'string' ? fields.connection_handle : undefined;
    }
    return undefined;
}

function normalizeRequestNumber(value: number | undefined): number {
    if (value === undefined || !Number.isSafeInteger(value) || value < 0) return 0;
    return Math.min(value, MAX_REQUEST_NUMBER);
}

function isMissingHostMessage(message: string | undefined): boolean {
    return (
        typeof message === 'string' &&
        /(?:native messaging host|native host).*(?:not found|not installed|not registered|does not exist)|no such (?:native messaging )?host/i.test(
            message,
        )
    );
}

export class NativeConnectorPort {
    private readonly deps: NativeConnectorPortDeps;
    private readonly requestTimeoutMs: number;
    private readonly tombstones = new BoundedIdSet(TOMBSTONE_MAX);
    private readonly eventListeners = new Set<(event: WireEvent) => void>();
    private readonly wireErrorListeners = new Set<(event: WireErrorEvent) => void>();
    private readonly failureListeners = new Set<(failure: ConnectorFailure) => void>();
    private readonly closeListeners = new Set<(close: ConnectorClose) => void>();
    private lastNotifiedCloseGeneration = -1;

    private port: NativePortLike | null = null;
    private generation = 0;
    private phase: 'pre_ready' | 'ready' = 'pre_ready';
    private selectedApiVersion: number | null = null;
    private pending = new Map<string, PendingEntry>();
    private closed = false;
    private intent: CloseIntent | null = null;
    private createFailure: ConnectorFailure | null = null;
    private requestCounter = 0;

    constructor(deps: NativeConnectorPortDeps) {
        this.deps = deps;
        this.requestTimeoutMs = deps.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
    }

    open(): void {
        if (this.port !== null) return;
        if (this.closed) {
            this.closed = false;
            this.intent = null;
        }
        this.createFailure = null;
        this.phase = 'pre_ready';
        this.selectedApiVersion = null;
        this.generation += 1;
        this.requestCounter = normalizeRequestNumber(this.deps.requestIdStart);
        this.tombstones.clear();
        const generation = this.generation;

        let port: NativePortLike;
        try {
            port = this.deps.connectNative(HOST_NAME);
        } catch {
            this.createFailure = synthesizeInternal('port_create_failed');
            return;
        }
        this.port = port;
        port.onMessage.addListener((message) => this.handleMessage(generation, message));
        port.onDisconnect.addListener(() => this.handleDisconnect(generation));
    }

    hello(): Promise<ConnectorResult<Extract<WireEvent, { type: 'ready' }>>> {
        return this.startRequest<Extract<WireEvent, { type: 'ready' }>>('hello', {
            api_versions: [...SUPPORTED_API_VERSIONS],
        });
    }

    request<T extends WireEvent>(
        kind: PendingKind,
        fields: Record<string, unknown> = {},
    ): Promise<ConnectorResult<T>> {
        return this.startRequest<T>(kind, fields);
    }

    onEvent(listener: (event: WireEvent) => void): () => void {
        this.eventListeners.add(listener);
        return () => {
            this.eventListeners.delete(listener);
        };
    }

    onFailure(listener: (failure: ConnectorFailure) => void): () => void {
        this.failureListeners.add(listener);
        return () => {
            this.failureListeners.delete(listener);
        };
    }

    /** Validated unsolicited wire errors (never correlated request terminals). */
    onWireError(listener: (event: WireErrorEvent) => void): () => void {
        this.wireErrorListeners.add(listener);
        return () => {
            this.wireErrorListeners.delete(listener);
        };
    }

    onClose(listener: (close: ConnectorClose) => void): () => void {
        this.closeListeners.add(listener);
        return () => {
            this.closeListeners.delete(listener);
        };
    }

    close(intent: CloseIntent): Promise<void> {
        this.closeSync(intent);
        return Promise.resolve();
    }

    isOpen(): boolean {
        return this.port !== null && !this.closed;
    }

    isIntentional(): boolean {
        return this.intent !== null;
    }

    private startRequest<T extends WireEvent>(
        kind: PendingKind,
        fields: Record<string, unknown>,
    ): Promise<ConnectorResult<T>> {
        if (this.closed) {
            return Promise.resolve({
                ok: false,
                source: 'local',
                error: synthesizePortClosed(kind),
            });
        }
        if (this.port === null) {
            if (this.createFailure !== null) {
                return Promise.resolve({ ok: false, source: 'local', error: this.createFailure });
            }
            return Promise.resolve({
                ok: false,
                source: 'local',
                error: synthesizePortClosed(kind),
            });
        }
        if (this.phase === 'pre_ready' && kind !== 'hello') {
            return Promise.resolve({
                ok: false,
                source: 'local',
                error: synthesizeProtocolViolation('pre_ready'),
            });
        }
        if (this.phase === 'ready' && kind === 'hello') {
            return Promise.resolve({
                ok: false,
                source: 'local',
                error: synthesizeProtocolViolation('repeated_hello'),
            });
        }
        if (!validateRequestFields(kind, fields).ok) {
            return Promise.resolve({
                ok: false,
                source: 'local',
                error: synthesizeProtocolViolation('request_fields'),
            });
        }
        if (this.pending.size >= PENDING_MAX) {
            return Promise.resolve({
                ok: false,
                source: 'local',
                error: synthesizeCapExceeded(),
            });
        }

        const requestId = this.nextToken();
        if (requestId === null) {
            return Promise.resolve({
                ok: false,
                source: 'local',
                error: this.closed
                    ? synthesizePortClosed(kind)
                    : synthesizeInternal('request_id_overflow'),
            });
        }
        try {
            this.deps.onRequestIssued?.(requestId, kind);
        } catch {
            return Promise.resolve({
                ok: false,
                source: 'local',
                error: synthesizeInternal('request_id_hook'),
            });
        }

        const handle = operationHandle(kind, fields);
        let generation: number | undefined;
        if (handle !== undefined && (kind === 'send' || kind === 'disconnect')) {
            generation = this.deps.validationContext().connections.get(handle)?.generation;
        }

        const message: Record<string, unknown> = {
            envelope_version: ENVELOPE_VERSION,
            type: kind,
            request_id: requestId,
            ...fields,
        };
        if (this.phase === 'ready' && this.selectedApiVersion !== null) {
            message.api_version = this.selectedApiVersion;
        }

        return new Promise<ConnectorResult<T>>((resolve) => {
            const entry: PendingEntry = {
                requestId,
                kind,
                handle,
                generation,
                portGeneration: this.generation,
                resolve: (result) => resolve(result as ConnectorResult<T>),
            };
            this.pending.set(requestId, entry);

            // OTP2-BRD-020/030 exempt both scan request kinds from the 30 s
            // request timeout. They are terminalized by scan lifecycle events
            // or by port closure only.
            if (kind !== 'start_scan' && kind !== 'stop_scan') {
                entry.timer = this.setTimeout(
                    () => this.handleTimeout(entry),
                    this.requestTimeoutMs,
                );
            }

            try {
                this.port?.postMessage(message);
            } catch {
                this.failPending(entry, synthesizeInternal('post_failed'));
            }
        });
    }

    private nextToken(): string | null {
        // A monotonic per-generation counter makes bounded-memory identity
        // possible: every id is new without remembering every id ever issued.
        // The only terminal outcome is safe-integer overflow, refused before
        // wrap; an explicit reset happens when the port generation is opened.
        if (this.requestCounter >= MAX_REQUEST_NUMBER) return null;
        this.requestCounter += 1;
        const candidate = `c${this.requestCounter.toString(36)}`;
        if (!isRequestId(candidate)) return null;
        if (this.pending.has(candidate) || this.tombstones.has(candidate)) return null;
        return candidate;
    }

    private setTimeout(fn: () => void, ms: number): unknown {
        if (this.deps.setTimeout !== undefined) return this.deps.setTimeout(fn, ms);
        return globalThis.setTimeout(fn, ms);
    }

    private clearTimeout(handle: unknown): void {
        if (this.deps.clearTimeout !== undefined) {
            this.deps.clearTimeout(handle);
            return;
        }
        globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>);
    }

    private handleTimeout(entry: PendingEntry): void {
        if (entry.portGeneration !== this.generation) return;
        if (this.pending.get(entry.requestId) !== entry) return;
        this.failPending(entry, synthesizeTimeout(entry.kind));
    }

    private handleMessage(generation: number, message: unknown): void {
        if (generation !== this.generation || this.port === null || this.closed) return;

        if (this.isIncompatibleReady(message)) {
            const requestId =
                isPlainObject(message) && isRequestId(message.request_id)
                    ? message.request_id
                    : undefined;
            const entry = requestId === undefined ? undefined : this.pending.get(requestId);
            if (entry !== undefined) {
                this.failPending(entry, synthesizeIncompatibleApi());
            } else {
                this.emitFailure(synthesizeIncompatibleApi());
            }
            this.closeSync('protocol');
            return;
        }

        const validation = validateInbound(message, this.currentValidationContext());
        if (!validation.ok) {
            const requestId =
                isPlainObject(message) && isRequestId(message.request_id)
                    ? message.request_id
                    : undefined;
            const stage =
                requestId !== undefined && this.tombstones.has(requestId)
                    ? 'duplicate_terminal'
                    : validation.stage;
            const failure = synthesizeProtocolViolation(stage);
            const entry = requestId === undefined ? undefined : this.pending.get(requestId);
            if (entry !== undefined) {
                this.failPending(entry, failure);
            } else {
                this.emitFailure(
                    requestId === undefined ? failure : { ...failure, request_id: requestId },
                );
            }
            this.closeSync('protocol');
            return;
        }

        const event = validation.event;
        if (event.type === 'ready') {
            this.phase = 'ready';
            this.selectedApiVersion = event.api_version;
        }
        if ('request_id' in event && typeof event.request_id === 'string') {
            const entry = this.pending.get(event.request_id);
            if (entry !== undefined) this.completePending(entry, event);
        }
        this.emitEvent(event);
        if (event.type === 'error' && event.request_id === undefined) {
            this.emitWireError(event);
        }

        // A pre-ready incompatible_api is a fatal negotiation outcome: the
        // host will exit. Keep the wire error as the hello terminal, then stop
        // driving the bridge so no later BLE request can be attempted.
        if (
            event.type === 'error' &&
            event.code === 'incompatible_api' &&
            this.phase === 'pre_ready'
        ) {
            this.closeSync('protocol');
        }
    }

    private isIncompatibleReady(message: unknown): boolean {
        if (!isPlainObject(message) || this.phase !== 'pre_ready') return false;
        if (message.type !== 'ready') return false;
        return isApiVersion(message.api_version) && !isSupportedApiVersion(message.api_version);
    }

    private currentValidationContext(): ValidationContext {
        const base = this.deps.validationContext();
        const pending = new Map<
            string,
            { kind: PendingKind; handle?: string; generation?: number }
        >();
        for (const [requestId, entry] of this.pending) {
            pending.set(requestId, {
                kind: entry.kind,
                ...(entry.handle === undefined ? {} : { handle: entry.handle }),
                ...(entry.generation === undefined ? {} : { generation: entry.generation }),
            });
        }
        return {
            phase: this.phase,
            apiVersion: this.phase === 'ready' ? this.selectedApiVersion : null,
            pending,
            scans: base.scans,
            connections: base.connections,
        };
    }

    private completePending(entry: PendingEntry, event: WireEvent): void {
        if (this.pending.get(entry.requestId) !== entry) return;
        this.pending.delete(entry.requestId);
        this.clearEntryTimer(entry);
        this.tombstones.add(entry.requestId);
        if (event.type === 'error') {
            entry.resolve({ ok: false, source: 'wire', error: event });
            return;
        }
        entry.resolve({ ok: true, event });
    }

    private failPending(entry: PendingEntry, failure: ConnectorFailure): void {
        if (this.pending.get(entry.requestId) !== entry) return;
        this.pending.delete(entry.requestId);
        this.clearEntryTimer(entry);
        this.tombstones.add(entry.requestId);
        const local: ConnectorFailure = { ...failure, request_id: entry.requestId };
        entry.resolve({ ok: false, source: 'local', error: local });
        this.emitFailure(local);
    }

    private clearEntryTimer(entry: PendingEntry): void {
        if (entry.timer === undefined) return;
        this.clearTimeout(entry.timer);
        entry.timer = undefined;
    }

    private handleDisconnect(generation: number): void {
        if (generation !== this.generation) return;
        const lastError = this.readLastError();
        const missingHost = this.phase === 'pre_ready' && isMissingHostMessage(lastError?.message);

        this.port = null;
        this.closed = true;
        this.generation += 1;

        const entries = [...this.pending.values()];
        this.pending.clear();
        for (const entry of entries) {
            this.clearEntryTimer(entry);
            this.tombstones.add(entry.requestId);
            const failure = missingHost
                ? synthesizeMissingHost(lastError)
                : synthesizePortClosed(entry.kind);
            const local: ConnectorFailure = { ...failure, request_id: entry.requestId };
            entry.resolve({ ok: false, source: 'local', error: local });
            this.emitFailure(local);
        }

        this.emitClose({
            generation,
            reason: missingHost ? 'missing_host' : 'port_lost',
            intentional: false,
        });
    }

    private readLastError(): { message?: string } | undefined {
        if (this.deps.lastError === undefined) return undefined;
        try {
            return this.deps.lastError();
        } catch {
            return undefined;
        }
    }

    private closeSync(intent: CloseIntent): void {
        if (this.closed) {
            if (this.intent === null) this.intent = intent;
            return;
        }
        this.intent = intent;
        this.closed = true;
        const port = this.port;
        const closingGeneration = this.generation;
        this.port = null;
        this.generation += 1;

        const entries = [...this.pending.values()];
        this.pending.clear();
        for (const entry of entries) {
            this.clearEntryTimer(entry);
            this.tombstones.add(entry.requestId);
            const local: ConnectorFailure = {
                ...synthesizePortClosed(entry.kind),
                request_id: entry.requestId,
            };
            entry.resolve({ ok: false, source: 'local', error: local });
            this.emitFailure(local);
        }

        this.emitClose({
            generation: closingGeneration,
            reason: intent,
            intentional: true,
        });

        if (port !== null) {
            try {
                port.disconnect();
            } catch {
                // The port may already be gone; close is intentionally idempotent.
            }
        }
    }

    private emitClose(close: ConnectorClose): void {
        if (this.lastNotifiedCloseGeneration === close.generation) return;
        this.lastNotifiedCloseGeneration = close.generation;
        for (const listener of [...this.closeListeners]) {
            try {
                listener(close);
            } catch {
                // A subscriber must not break lifecycle notification.
            }
        }
    }

    private emitEvent(event: WireEvent): void {
        for (const listener of [...this.eventListeners]) {
            try {
                listener(event);
            } catch {
                // A subscriber must not break inbound routing.
            }
        }
    }

    private emitWireError(event: WireErrorEvent): void {
        for (const listener of [...this.wireErrorListeners]) {
            try {
                listener(event);
            } catch {
                // A subscriber must not break inbound routing.
            }
        }
    }

    private emitFailure(failure: ConnectorFailure): void {
        for (const listener of [...this.failureListeners]) {
            try {
                listener(failure);
            } catch {
                // A subscriber must not break inbound routing.
            }
        }
    }
}
