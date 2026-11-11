// Cohesive connector controller: one Native Messaging port, one state store.
//
// The controller owns request scheduling so a connection/port generation is
// captured before work is queued. Sends are FIFO per connection, counted
// against both the per-link and global send-storage bounds including the
// operation currently in flight, and are never replayed after an ambiguous
// terminal. Port close invalidates every timer/context and clears all stores.

import {
    synthesizeCapExceeded,
    synthesizeProtocolViolation,
    synthesizeSendFailed,
    synthesizePortClosed,
    synthesizeUnknownHandle,
    type ConnectorFailure,
} from './errors';
import { BRIDGE_RECORD_MAX_BYTES, BRIDGE_SCAN_MAX_MS } from './limits';
import {
    NativeConnectorPort,
    type ConnectorClose,
    type ConnectorFailure as PortConnectorFailure,
    type ConnectorResult,
    type NativePortLike,
} from './port';
import { VELES_SERVICE_UUID, type WireEvent } from './protocol';
import {
    ConnectorState,
    type ConnectTarget,
    type QueuedOperation,
    type SendCancelReason,
} from './state';

type ReadyEvent = Extract<WireEvent, { type: 'ready' }>;
type ScanStateEvent = Extract<WireEvent, { type: 'scan_state' }>;
type ConnectedEvent = Extract<WireEvent, { type: 'connected' }>;
type DisconnectedEvent = Extract<WireEvent, { type: 'disconnected' }>;
type SentEvent = Extract<WireEvent, { type: 'sent' }>;
type ShutdownCompleteEvent = Extract<WireEvent, { type: 'shutdown_complete' }>;

export interface ConnectorControllerDeps {
    connectNative: (hostName: string) => NativePortLike;
    lastError?: () => { message?: string } | undefined;
    state?: ConnectorState;
    requestIdStart?: number;
    now?: () => number;
    setTimeout?: (fn: () => void, ms: number) => unknown;
    clearTimeout?: (handle: unknown) => void;
    requestTimeoutMs?: number;
}

export interface DecodedRecord {
    connectionHandle: string;
    bytes: Uint8Array;
}

export type RecordListener = (record: DecodedRecord) => void;

interface RequestContext {
    kind: 'scan' | 'connect';
    generation: number;
    portEpoch: number;
    requestId?: string;
}

interface ScanTimer {
    generation: number;
    portEpoch: number;
    handle: unknown;
}

const BASE64_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
const BASE64_REVERSE = new Map([...BASE64_ALPHABET].map((character, index) => [character, index]));

function encodeBase64(bytes: Uint8Array): string {
    let output = '';
    for (let index = 0; index < bytes.length; index += 3) {
        const first = bytes[index] ?? 0;
        const second = bytes[index + 1];
        const third = bytes[index + 2];
        output += BASE64_ALPHABET[first >> 2];
        output += BASE64_ALPHABET[((first & 0x03) << 4) | ((second ?? 0) >> 4)];
        output +=
            second === undefined
                ? '='
                : BASE64_ALPHABET[((second & 0x0f) << 2) | ((third ?? 0) >> 6)];
        output += third === undefined ? '=' : BASE64_ALPHABET[third & 0x3f];
    }
    return output;
}

function decodeBase64(value: string): Uint8Array | null {
    const padding = value.endsWith('==') ? 2 : value.endsWith('=') ? 1 : 0;
    const dataLength = value.length - padding;
    if (value.length % 4 !== 0 || dataLength < 0) return null;
    const output = new Uint8Array((value.length / 4) * 3 - padding);
    let byte = 0;
    let accumulator = 0;
    let bits = 0;
    for (let index = 0; index < dataLength; index += 1) {
        const digit = BASE64_REVERSE.get(value[index] ?? '');
        if (digit === undefined) return null;
        accumulator = (accumulator << 6) | digit;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            output[byte] = (accumulator >> bits) & 0xff;
            byte += 1;
        }
    }
    return output;
}

function cancelledFailure(reason: SendCancelReason): ConnectorFailure {
    switch (reason) {
        case 'user':
            return synthesizeSendFailed('disconnected');
        case 'loss':
            return synthesizeSendFailed('link_lost');
        case 'timeout':
            return synthesizeSendFailed('timeout');
        case 'protocol':
            return synthesizeSendFailed('protocol');
        case 'stale':
            return synthesizeSendFailed('stale');
        case 'ambiguous':
            return synthesizeSendFailed('ambiguous_send');
        case 'reset':
            return synthesizePortClosed('send');
    }
}

function isRefusedControl(result: ConnectorResult<WireEvent>): boolean {
    if (result.ok) return false;
    if (result.source === 'local') {
        return result.error.code === 'cap_exceeded' || result.error.code === 'protocol_violation';
    }
    return (
        result.error.code === 'cap_exceeded' ||
        result.error.code === 'busy' ||
        result.error.code === 'invalid_request'
    );
}

function localFailure<T extends WireEvent>(error: ConnectorFailure): ConnectorResult<T> {
    return { ok: false, source: 'local', error };
}

export class ConnectorController {
    readonly state: ConnectorState;

    private readonly port: NativeConnectorPort;
    private readonly deps: ConnectorControllerDeps;
    private readonly requestContexts = new Map<string, RequestContext>();
    private readonly recordListeners = new Set<RecordListener>();
    private readonly changeListeners = new Set<
        (snapshot: ReturnType<ConnectorState['snapshot']>) => void
    >();
    private readonly failureListeners = new Set<(failure: PortConnectorFailure) => void>();
    private readonly wireErrorListeners = new Set<
        (event: Extract<WireEvent, { type: 'error' }>) => void
    >();
    private readonly closeListeners = new Set<(close: ConnectorClose) => void>();
    private readonly scanTimers = new Map<string, ScanTimer>();

    private portEpoch = 0;
    private selectedApiVersion: number | null = null;
    private pendingContext: RequestContext | null = null;

    constructor(deps: ConnectorControllerDeps) {
        this.deps = deps;
        this.state = deps.state ?? new ConnectorState({ now: deps.now });
        this.port = new NativeConnectorPort({
            connectNative: deps.connectNative,
            lastError: deps.lastError,
            validationContext: () => this.state.validationContext(this.selectedApiVersion),
            onRequestIssued: (requestId) => this.bindRequestContext(requestId),
            requestIdStart: deps.requestIdStart,
            now: deps.now,
            setTimeout: deps.setTimeout,
            clearTimeout: deps.clearTimeout,
            requestTimeoutMs: deps.requestTimeoutMs,
        });
        this.port.onEvent((event) => this.handleEvent(event));
        this.port.onFailure((failure) => this.emitFailure(failure));
        this.port.onWireError((event) => this.emitWireError(event));
        this.port.onClose((close) => this.handleClose(close));
    }

    open(): void {
        if (this.port.isOpen()) return;
        this.selectedApiVersion = null;
        this.portEpoch += 1;
        this.port.open();
    }

    async hello(): Promise<ConnectorResult<ReadyEvent>> {
        const result = await this.port.hello();
        if (result.ok) this.selectedApiVersion = result.event.api_version;
        return result;
    }

    startScan(): Promise<ConnectorResult<ScanStateEvent>> {
        const begun = this.state.beginScan();
        if (begun === null) {
            return Promise.resolve(localFailure(synthesizeCapExceeded()));
        }
        const epoch = this.portEpoch;
        this.emitChange();
        return this.callWithContext(
            {
                kind: 'scan',
                generation: begun.generation,
                portEpoch: epoch,
            },
            () =>
                this.port.request<ScanStateEvent>('start_scan', {
                    service_uuid: VELES_SERVICE_UUID,
                }),
        ).then((result) => {
            if (!result.ok) this.state.abortScan(begun.generation);
            this.emitChange();
            return result;
        });
    }

    stopScan(scanHandle: string): Promise<ConnectorResult<ScanStateEvent>> {
        const begun = this.state.beginStopScan(scanHandle);
        if (begun === null) {
            return Promise.resolve(localFailure(synthesizeUnknownHandle('scan_handle')));
        }
        this.emitChange();
        return this.port
            .request<ScanStateEvent>('stop_scan', { scan_handle: scanHandle })
            .then((result) => {
                if (!result.ok) {
                    if (isRefusedControl(result)) {
                        // The stop was never executed (local admission or a
                        // retryable host refusal). Restore the wire-live scan
                        // so trailing discovery stays valid and stop is
                        // retryable.
                        this.state.restoreStopScan(scanHandle, begun.generation);
                    } else if (result.source === 'wire' && result.error.code === 'unknown_handle') {
                        // The host says the scan no longer exists; do not
                        // resurrect it, but observe the closure locally.
                        this.state.abandonScan(scanHandle, begun.generation);
                    }
                }
                this.emitChange();
                return result;
            });
    }

    connect(target: ConnectTarget): Promise<ConnectorResult<ConnectedEvent>> {
        const begun = this.state.beginConnect(target);
        if (begun === null) {
            return Promise.resolve(localFailure(synthesizeCapExceeded()));
        }
        const epoch = this.portEpoch;
        const fields: Record<string, unknown> =
            'deviceHandle' in target
                ? { device_handle: target.deviceHandle }
                : { platform_hint: target.platformHint };
        this.emitChange();
        return this.callWithContext(
            {
                kind: 'connect',
                generation: begun.generation,
                portEpoch: epoch,
            },
            () => this.port.request<ConnectedEvent>('connect', fields),
        ).then((result) => {
            if (!result.ok) this.state.abortConnect(begun.generation);
            this.emitChange();
            return result;
        });
    }

    disconnect(connectionHandle: string): Promise<ConnectorResult<DisconnectedEvent>> {
        const begun = this.state.beginDisconnect(connectionHandle);
        if (begun === null) {
            return Promise.resolve(localFailure(synthesizeUnknownHandle()));
        }
        this.emitChange();
        return this.port
            .request<DisconnectedEvent>('disconnect', {
                connection_handle: connectionHandle,
            })
            .then((result) => {
                if (!result.ok) {
                    if (isRefusedControl(result)) {
                        // No disconnect reached the host (or it was refused
                        // before execution). Restore the same generation so
                        // the live native link stays addressable and the user
                        // can retry.
                        this.state.refuseDisconnect(connectionHandle, begun.generation);
                    } else if (result.source === 'wire' && result.error.code === 'unknown_handle') {
                        this.state.closeConnection(connectionHandle, 'loss');
                    } else {
                        // Timeout/loss is ambiguous: keep the handle visible
                        // for an explicit retry, but do not allow sends.
                        this.state.markConnectionAmbiguous(connectionHandle, begun.generation);
                    }
                }
                this.emitChange();
                return result;
            });
    }

    send(connectionHandle: string, record: Uint8Array): Promise<ConnectorResult<SentEvent>> {
        if (record.length < 1 || record.length > BRIDGE_RECORD_MAX_BYTES) {
            return Promise.resolve(localFailure(synthesizeProtocolViolation('record')));
        }
        const connection = this.state.connections.get(connectionHandle);
        if (connection === undefined) {
            return Promise.resolve(localFailure(synthesizeUnknownHandle()));
        }
        if (connection.status !== 'connected') {
            return Promise.resolve(
                localFailure(
                    synthesizeSendFailed(
                        connection.status === 'ambiguous' ? 'ambiguous_send' : 'connection_state',
                    ),
                ),
            );
        }

        const copy = Uint8Array.from(record);
        const generation = connection.generation;
        const epoch = this.portEpoch;
        let settled = false;
        let released = false;
        let resolvePromise!: (result: ConnectorResult<SentEvent>) => void;
        const promise = new Promise<ConnectorResult<SentEvent>>((resolve) => {
            resolvePromise = resolve;
        });
        const release = (): void => {
            if (released) return;
            released = true;
            copy.fill(0);
            operation.bytes = null;
        };
        const settle = (result: ConnectorResult<SentEvent>): void => {
            if (settled) return;
            settled = true;
            release();
            resolvePromise(result);
        };
        const operation: QueuedOperation = {
            byteLength: copy.length,
            connectionGeneration: generation,
            portEpoch: epoch,
            posted: false,
            bytes: copy,
            release,
            cancel: (reason) => settle(localFailure(cancelledFailure(reason))),
            resolve: settle,
        };

        const enqueued = this.state.enqueueSend(connectionHandle, generation, operation);
        if (enqueued !== 'ok') {
            const failure =
                enqueued === 'cap_exceeded'
                    ? synthesizeCapExceeded()
                    : enqueued === 'unknown_handle'
                      ? synthesizeUnknownHandle()
                      : synthesizeSendFailed('stale');
            operation.resolve(localFailure(failure));
            return promise;
        }
        this.emitChange();
        this.pumpSend(connectionHandle);
        return promise;
    }

    async shutdown(): Promise<ConnectorResult<ShutdownCompleteEvent>> {
        const result = await this.port.request<ShutdownCompleteEvent>('shutdown', {});
        await this.port.close('shutdown');
        return result;
    }

    close(intent: 'user' | 'shutdown' | 'protocol' = 'user'): Promise<void> {
        return this.port.close(intent);
    }

    isOpen(): boolean {
        return this.port.isOpen();
    }

    /**
     * Monotonic identity of the current native port generation. It advances on
     * every open and every close, so async session work captured for an older
     * generation can never deliver into a replacement port.
     */
    get portGeneration(): number {
        return this.portEpoch;
    }

    isIntentional(): boolean {
        return this.port.isIntentional();
    }

    onRecord(listener: RecordListener): () => void {
        this.recordListeners.add(listener);
        return () => {
            this.recordListeners.delete(listener);
        };
    }

    onChange(listener: (snapshot: ReturnType<ConnectorState['snapshot']>) => void): () => void {
        this.changeListeners.add(listener);
        return () => {
            this.changeListeners.delete(listener);
        };
    }

    onFailure(listener: (failure: ConnectorFailure) => void): () => void {
        this.failureListeners.add(listener);
        return () => {
            this.failureListeners.delete(listener);
        };
    }

    /** Validated unsolicited wire errors, distinct from local failures. */
    onWireError(listener: (event: Extract<WireEvent, { type: 'error' }>) => void): () => void {
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

    snapshot(): ReturnType<ConnectorState['snapshot']> {
        return this.state.snapshot();
    }

    private bindRequestContext(requestId: string): void {
        if (this.pendingContext === null) return;
        if (this.pendingContext.requestId !== undefined) {
            this.requestContexts.delete(this.pendingContext.requestId);
        }
        this.pendingContext.requestId = requestId;
        this.requestContexts.set(requestId, this.pendingContext);
    }

    private callWithContext<T extends WireEvent>(
        context: RequestContext,
        call: () => Promise<ConnectorResult<T>>,
    ): Promise<ConnectorResult<T>> {
        const previous = this.pendingContext;
        this.pendingContext = context;
        let promise: Promise<ConnectorResult<T>>;
        try {
            promise = call();
        } finally {
            this.pendingContext = previous;
        }
        return promise.finally(() => {
            if (context.requestId !== undefined) {
                this.requestContexts.delete(context.requestId);
            }
        });
    }

    private handleEvent(event: WireEvent): void {
        if (event.type === 'ready') {
            this.selectedApiVersion = event.api_version;
        }

        if (event.type === 'scan_state' && event.status === 'started') {
            if (event.request_id !== undefined) {
                const context = this.requestContexts.get(event.request_id);
                this.requestContexts.delete(event.request_id);
                if (
                    context !== undefined &&
                    context.kind === 'scan' &&
                    context.portEpoch === this.portEpoch &&
                    this.state.acceptScan(event.scan_handle, context.generation)
                ) {
                    this.scheduleScanTimer(
                        event.scan_handle,
                        context.generation,
                        context.portEpoch,
                    );
                }
            }
        }

        if (event.type === 'connected') {
            const context = this.requestContexts.get(event.request_id);
            this.requestContexts.delete(event.request_id);
            if (
                context !== undefined &&
                context.kind === 'connect' &&
                context.portEpoch === this.portEpoch
            ) {
                this.state.acceptConnection(
                    event.connection_handle,
                    context.generation,
                    event.platform_hint,
                );
            }
        }

        if (event.type === 'scan_state' && event.status === 'stopped') {
            this.clearScanTimer(event.scan_handle);
        }

        const application = this.state.apply(event);
        if (event.type === 'message' && application === 'applied') {
            const bytes = decodeBase64(event.record_base64);
            if (bytes !== null) {
                for (const listener of [...this.recordListeners]) {
                    try {
                        listener({ connectionHandle: event.connection_handle, bytes });
                    } catch {
                        // A record consumer must not break routing.
                    }
                }
            }
        }

        this.emitChange();
        if (application === 'protocol_violation') {
            void this.port.close('protocol');
        }
    }

    private handleClose(close: ConnectorClose): void {
        this.portEpoch += 1;
        this.requestContexts.clear();
        for (const timer of this.scanTimers.values()) {
            this.clearTimer(timer.handle);
        }
        this.scanTimers.clear();
        this.state.reset(close.reason === 'user' ? 'user' : 'reset');
        for (const listener of [...this.closeListeners]) {
            try {
                listener(close);
            } catch {
                // A lifecycle subscriber must not break cleanup.
            }
        }
        this.emitChange();
    }

    private scheduleScanTimer(scanHandle: string, generation: number, portEpoch: number): void {
        this.clearScanTimer(scanHandle);
        const timer = this.setTimer(() => {
            const current = this.scanTimers.get(scanHandle);
            if (current === undefined || current.handle !== timer) return;
            this.scanTimers.delete(scanHandle);
            if (portEpoch !== this.portEpoch) return;
            if (this.state.expireScan(scanHandle, generation)) this.emitChange();
        }, BRIDGE_SCAN_MAX_MS);
        this.scanTimers.set(scanHandle, { generation, portEpoch, handle: timer });
    }

    private clearScanTimer(scanHandle: string): void {
        const timer = this.scanTimers.get(scanHandle);
        if (timer === undefined) return;
        this.scanTimers.delete(scanHandle);
        this.clearTimer(timer.handle);
    }

    private setTimer(fn: () => void, ms: number): unknown {
        if (this.deps.setTimeout !== undefined) return this.deps.setTimeout(fn, ms);
        return globalThis.setTimeout(fn, ms);
    }

    private clearTimer(handle: unknown): void {
        if (this.deps.clearTimeout !== undefined) {
            this.deps.clearTimeout(handle);
            return;
        }
        globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>);
    }

    private pumpSend(connectionHandle: string): void {
        const connection = this.state.connections.get(connectionHandle);
        if (connection === undefined || connection.status !== 'connected') return;
        const operation = this.state.peekSend(connectionHandle);
        if (operation === undefined || operation.posted) return;

        if (
            operation.connectionGeneration !== connection.generation ||
            operation.portEpoch !== this.portEpoch ||
            operation.bytes === null
        ) {
            this.state.discardSend(connectionHandle, operation, 'stale');
            this.emitChange();
            this.pumpSend(connectionHandle);
            return;
        }

        const payload = operation.bytes;
        const recordBase64 = encodeBase64(payload);
        if (!this.state.markSendPosted(connectionHandle, operation)) return;
        operation.release();

        const result = this.port.request<SentEvent>('send', {
            connection_handle: connectionHandle,
            record_base64: recordBase64,
        });
        this.emitChange();
        void result.then((outcome) => this.finishSend(connectionHandle, operation, outcome));
    }

    private finishSend(
        connectionHandle: string,
        operation: QueuedOperation,
        result: ConnectorResult<SentEvent>,
    ): void {
        const connection = this.state.connections.get(connectionHandle);
        this.state.completeSend(connectionHandle, operation);
        operation.resolve(result);

        if (result.ok) {
            this.pumpSend(connectionHandle);
        } else if (result.source === 'wire' && result.error.code === 'unknown_handle') {
            this.state.closeConnection(connectionHandle, 'loss');
        } else if (this.isAmbiguousFailure(result)) {
            this.state.markConnectionAmbiguous(
                connectionHandle,
                connection?.generation ?? operation.connectionGeneration,
            );
        } else {
            this.pumpSend(connectionHandle);
        }
        this.emitChange();
    }

    private isAmbiguousFailure(result: ConnectorResult<SentEvent>): boolean {
        if (result.ok) return false;
        if (result.source === 'local') {
            return (
                result.error.code === 'timeout' ||
                result.error.code === 'internal' ||
                result.error.code === 'protocol_violation'
            );
        }
        return (
            result.error.code === 'timeout' ||
            result.error.code === 'send_failed' ||
            result.error.code === 'internal'
        );
    }

    private emitFailure(failure: ConnectorFailure): void {
        for (const listener of [...this.failureListeners]) {
            try {
                listener(failure);
            } catch {
                // A failure subscriber must not break routing.
            }
        }
    }

    private emitWireError(event: Extract<WireEvent, { type: 'error' }>): void {
        for (const listener of [...this.wireErrorListeners]) {
            try {
                listener(event);
            } catch {
                // A wire-error subscriber must not break routing.
            }
        }
    }

    private emitChange(): void {
        const snapshot = this.state.snapshot();
        for (const listener of [...this.changeListeners]) {
            try {
                listener(snapshot);
            } catch {
                // A view subscriber must not break routing.
            }
        }
    }
}
