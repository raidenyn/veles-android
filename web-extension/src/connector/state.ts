// Connector-side scan, device, connection, and send-queue state.
//
// Scans, discovered devices, and connections are separate stores: one scan
// can discover many phones, devices outlive a stopped scan in the bounded
// discovery cache, and connections are keyed only by their connection handle.
// Handles that have been stopped/closed are moved into bounded tombstones so a
// late host event is classified as stale and can never recreate the handle.

import { DiagnosticRing, type DiagnosticEntry } from './diagnostics';
import {
    BRIDGE_OPEN_CONNECTIONS_MAX,
    BRIDGE_OPEN_SCANS_MAX,
    BRIDGE_SCAN_MAX_MS,
    CONNECTOR_CONNECTION_TOMBSTONE_MAX,
    CONNECTOR_DIAGNOSTICS_MAX,
    CONNECTOR_DISCOVERED_DEVICES_MAX,
    CONNECTOR_SCAN_TOMBSTONE_MAX,
    CONNECTOR_SEND_BYTES_GLOBAL,
    CONNECTOR_SEND_BYTES_PER_CONNECTION,
    CONNECTOR_SEND_RECORDS_GLOBAL,
    CONNECTOR_SEND_RECORDS_PER_CONNECTION,
} from './limits';
import type { ConnectorResult } from './port';
import type { ValidationContext, WireEvent } from './protocol';

export type EventApplication = 'applied' | 'stale' | 'protocol_violation';
export type ScanStatus = 'starting' | 'started' | 'stopping' | 'stopped';
export type ConnectionStatus =
    | 'connecting'
    | 'connected'
    | 'disconnecting'
    | 'ambiguous'
    | 'closed';
export type ConnectionCloseReason = 'user' | 'loss' | 'timeout' | 'protocol';
export type SendCancelReason =
    | 'user'
    | 'loss'
    | 'timeout'
    | 'protocol'
    | 'reset'
    | 'stale'
    | 'ambiguous';

export interface ScanRecord {
    readonly handle: string;
    status: ScanStatus;
    readonly devices: Set<string>;
    readonly generation: number;
    readonly startedAt: number;
    readonly expiresAt: number;
    expiredAt?: number;
}

export interface DeviceRecord {
    readonly handle: string;
    label: string;
    platformHint?: string;
    advertisementBytes: number;
    scanHandle: string;
    readonly generation: number;
}

export type SentEvent = Extract<WireEvent, { type: 'sent' }>;

/**
 * One send waiting for its independent FIFO turn. The record copy is held
 * only until the operation is posted or cancelled; `release()` zeroes it.
 */
export interface QueuedOperation {
    readonly byteLength: number;
    readonly connectionGeneration: number;
    readonly portEpoch: number;
    posted: boolean;
    bytes: Uint8Array | null;
    release(): void;
    cancel(reason: SendCancelReason): void;
    resolve(result: ConnectorResult<SentEvent>): void;
}

export interface ConnectionRecord {
    readonly handle: string;
    deviceHandle?: string;
    platformHint?: string;
    status: ConnectionStatus;
    readonly generation: number;
    readonly queue: BoundedQueue<QueuedOperation>;
}

export type ConnectTarget = { deviceHandle: string } | { platformHint: string };

export interface ConnectorSnapshot {
    scans: {
        handle: string;
        status: ScanStatus;
        generation: number;
        deviceCount: number;
        startedAt: number;
        expiresAt: number;
        locallyExpired: boolean;
    }[];
    devices: {
        handle: string;
        label: string;
        platformHint?: string;
        advertisementBytes: number;
        scanHandle: string;
        generation: number;
    }[];
    connections: {
        handle: string;
        deviceHandle?: string;
        platformHint?: string;
        status: ConnectionStatus;
        generation: number;
        queuedOperations: number;
        queuedBytes: number;
        sendInFlight: boolean;
    }[];
    diagnostics: DiagnosticEntry[];
}

export interface ConnectorStateDeps {
    now?: () => number;
    diagnosticsCapacity?: number;
}

interface PendingScan {
    readonly generation: number;
}

interface PendingConnection {
    readonly generation: number;
    readonly target: ConnectTarget;
}

interface QueueLimits {
    maxItems: number;
    maxBytes: number;
}

export class BoundedQueue<T> {
    private readonly values: T[] = [];
    private byteCount = 0;

    constructor(
        private readonly limits: QueueLimits,
        private readonly sizeOf: (value: T) => number,
    ) {}

    get size(): number {
        return this.values.length;
    }

    get bytes(): number {
        return this.byteCount;
    }

    wouldAccept(value: T): boolean {
        const bytes = this.sizeOf(value);
        return (
            this.values.length < this.limits.maxItems &&
            this.byteCount + bytes <= this.limits.maxBytes
        );
    }

    push(value: T): boolean {
        if (!this.wouldAccept(value)) return false;
        this.values.push(value);
        this.byteCount += this.sizeOf(value);
        return true;
    }

    peek(): T | undefined {
        return this.values[0];
    }

    shift(): T | undefined {
        if (this.values.length === 0) return undefined;
        const value = this.values.shift() as T;
        this.byteCount -= this.sizeOf(value);
        return value;
    }

    remove(value: T): boolean {
        const index = this.values.indexOf(value);
        if (index < 0) return false;
        this.values.splice(index, 1);
        this.byteCount -= this.sizeOf(value);
        return true;
    }

    clear(): void {
        this.values.length = 0;
        this.byteCount = 0;
    }

    list(): T[] {
        return [...this.values];
    }
}

function decodedBase64Length(value: string): number {
    const padding = value.endsWith('==') ? 2 : value.endsWith('=') ? 1 : 0;
    return (value.length / 4) * 3 - padding;
}

export class ConnectorState {
    readonly scans = new Map<string, ScanRecord>();
    readonly devices = new Map<string, DeviceRecord>();
    readonly connections = new Map<string, ConnectionRecord>();
    readonly diagnostics: DiagnosticRing;

    private readonly now: () => number;
    private readonly pendingScans = new Map<number, PendingScan>();
    private readonly pendingConnections = new Map<number, PendingConnection>();
    private readonly scanTombstones = new Map<string, number>();
    private readonly connectionTombstones = new Map<string, number>();
    private readonly disconnectPreviousStatus = new Map<string, ConnectionStatus>();
    private nextScanGeneration = 0;
    private nextConnectionGeneration = 0;
    private nextDeviceGeneration = 0;
    private globalSendRecords = 0;
    private globalSendBytes = 0;

    constructor(deps: ConnectorStateDeps = {}) {
        this.now = deps.now ?? Date.now;
        this.diagnostics = new DiagnosticRing(
            deps.diagnosticsCapacity ?? CONNECTOR_DIAGNOSTICS_MAX,
            this.now,
        );
    }

    get openScanCount(): number {
        let count = this.pendingScans.size;
        for (const scan of this.scans.values()) {
            if (scan.status !== 'stopped') count += 1;
        }
        return count;
    }

    get openConnectionCount(): number {
        let count = this.pendingConnections.size;
        for (const connection of this.connections.values()) {
            if (connection.status !== 'closed') count += 1;
        }
        return count;
    }

    beginScan(): { generation: number } | null {
        if (this.openScanCount >= BRIDGE_OPEN_SCANS_MAX) return null;
        this.nextScanGeneration += 1;
        const generation = this.nextScanGeneration;
        this.pendingScans.set(generation, { generation });
        this.diagnostics.push({ direction: 'outbound', type: 'start_scan' });
        return { generation };
    }

    abortScan(generation: number): boolean {
        return this.pendingScans.delete(generation);
    }

    acceptScan(handle: string, generation: number): boolean {
        const pending = this.pendingScans.get(generation);
        if (pending === undefined) return false;
        this.pendingScans.delete(generation);
        if (this.scans.has(handle) || this.scanTombstones.has(handle)) return false;
        const startedAt = this.now();
        this.scans.set(handle, {
            handle,
            status: 'started',
            devices: new Set(),
            generation,
            startedAt,
            expiresAt: startedAt + BRIDGE_SCAN_MAX_MS,
        });
        this.diagnostics.push({
            direction: 'local',
            type: 'scan_started',
            scan_handle: handle,
        });
        return true;
    }

    beginStopScan(handle: string): { generation: number } | null {
        const scan = this.scans.get(handle);
        if (scan === undefined || scan.status !== 'started') return null;
        scan.status = 'stopping';
        this.diagnostics.push({
            direction: 'outbound',
            type: 'stop_scan',
            scan_handle: handle,
        });
        return { generation: scan.generation };
    }

    expireScan(handle: string, generation: number): boolean {
        const scan = this.scans.get(handle);
        if (scan === undefined || scan.generation !== generation) return false;
        if (scan.status !== 'started' && scan.status !== 'stopping') return false;
        if (scan.expiredAt !== undefined) return false;
        if (this.now() < scan.expiresAt) return false;
        // A local deadline is intent, not proof that the host has drained the
        // scan output. Keep the scan wire-live as `stopping` so a queued
        // trailing `device_found` remains valid until the observed stop event
        // (OTP2-BRD-022.1), and the stopped tombstone is created only then.
        scan.expiredAt = this.now();
        scan.status = 'stopping';
        this.diagnostics.push({
            direction: 'local',
            type: 'scan_expired',
            scan_handle: handle,
        });
        return true;
    }

    restoreStopScan(handle: string, generation: number): boolean {
        const scan = this.scans.get(handle);
        if (scan === undefined || scan.generation !== generation) return false;
        if (scan.status !== 'stopping' || scan.expiredAt !== undefined) return false;
        scan.status = 'started';
        this.diagnostics.push({
            direction: 'local',
            type: 'stop_scan_refused',
            scan_handle: handle,
        });
        return true;
    }

    abandonScan(handle: string, generation: number): boolean {
        const scan = this.scans.get(handle);
        if (scan === undefined || scan.generation !== generation) return false;
        this.stopScanRecord(scan);
        this.diagnostics.push({
            direction: 'local',
            type: 'scan_abandoned',
            scan_handle: handle,
        });
        return true;
    }

    beginConnect(target: ConnectTarget): { generation: number } | null {
        if (this.openConnectionCount >= BRIDGE_OPEN_CONNECTIONS_MAX) return null;
        this.nextConnectionGeneration += 1;
        const generation = this.nextConnectionGeneration;
        this.pendingConnections.set(generation, { generation, target });
        return { generation };
    }

    abortConnect(generation: number): boolean {
        return this.pendingConnections.delete(generation);
    }

    acceptConnection(handle: string, generation: number, hint?: string): boolean {
        const pending = this.pendingConnections.get(generation);
        if (pending === undefined) return false;
        this.pendingConnections.delete(generation);
        if (this.connections.has(handle) || this.connectionTombstones.has(handle)) return false;
        const deviceHandle =
            'deviceHandle' in pending.target ? pending.target.deviceHandle : undefined;
        const connection: ConnectionRecord = {
            handle,
            status: 'connected',
            generation,
            queue: new BoundedQueue<QueuedOperation>(
                {
                    maxItems: CONNECTOR_SEND_RECORDS_PER_CONNECTION,
                    maxBytes: CONNECTOR_SEND_BYTES_PER_CONNECTION,
                },
                (operation) => operation.byteLength,
            ),
        };
        if (deviceHandle !== undefined) connection.deviceHandle = deviceHandle;
        if (hint !== undefined) connection.platformHint = hint;
        this.connections.set(handle, connection);
        return true;
    }

    beginDisconnect(handle: string): { generation: number } | null {
        const connection = this.connections.get(handle);
        if (connection === undefined || connection.status === 'disconnecting') return null;
        this.disconnectPreviousStatus.set(handle, connection.status);
        connection.status = 'disconnecting';
        this.cancelUnposted(connection, 'user');
        return { generation: connection.generation };
    }

    refuseDisconnect(handle: string, generation: number): boolean {
        const connection = this.connections.get(handle);
        if (
            connection === undefined ||
            connection.generation !== generation ||
            connection.status !== 'disconnecting'
        ) {
            return false;
        }
        const previous = this.disconnectPreviousStatus.get(handle) ?? 'connected';
        this.disconnectPreviousStatus.delete(handle);
        connection.status = previous;
        this.diagnostics.push({
            direction: 'local',
            type: 'disconnect_refused',
            connection_handle: handle,
        });
        return true;
    }

    closeConnection(handle: string, reason: ConnectionCloseReason): boolean {
        const connection = this.connections.get(handle);
        if (connection === undefined) return false;
        this.connections.delete(handle);
        this.disconnectPreviousStatus.delete(handle);
        this.rememberConnectionTombstone(handle, connection.generation);
        this.releaseQueue(connection, reason);
        this.diagnostics.push({
            direction: 'local',
            type: 'connection_closed',
            code: reason,
            connection_handle: handle,
        });
        return true;
    }

    markConnectionAmbiguous(handle: string, generation: number): boolean {
        const connection = this.connections.get(handle);
        if (connection === undefined || connection.generation !== generation) return false;
        this.disconnectPreviousStatus.delete(handle);
        connection.status = 'ambiguous';
        this.cancelUnposted(connection, 'ambiguous');
        this.diagnostics.push({
            direction: 'local',
            type: 'connection_ambiguous',
            connection_handle: handle,
        });
        return true;
    }

    enqueueSend(
        handle: string,
        generation: number,
        operation: QueuedOperation,
    ): 'ok' | 'cap_exceeded' | 'stale' | 'unknown_handle' {
        const connection = this.connections.get(handle);
        if (connection === undefined) return 'unknown_handle';
        if (
            connection.generation !== generation ||
            connection.status !== 'connected' ||
            operation.connectionGeneration !== generation
        ) {
            return 'stale';
        }
        if (
            this.globalSendRecords + 1 > CONNECTOR_SEND_RECORDS_GLOBAL ||
            this.globalSendBytes + operation.byteLength > CONNECTOR_SEND_BYTES_GLOBAL ||
            !connection.queue.wouldAccept(operation)
        ) {
            return 'cap_exceeded';
        }
        if (!connection.queue.push(operation)) return 'cap_exceeded';
        this.globalSendRecords += 1;
        this.globalSendBytes += operation.byteLength;
        this.diagnostics.push({
            direction: 'outbound',
            type: 'send_queued',
            connection_handle: handle,
            bytes: operation.byteLength,
        });
        return 'ok';
    }

    peekSend(handle: string): QueuedOperation | undefined {
        return this.connections.get(handle)?.queue.peek();
    }

    markSendPosted(handle: string, operation: QueuedOperation): boolean {
        const connection = this.connections.get(handle);
        if (connection === undefined || connection.queue.peek() !== operation) return false;
        operation.posted = true;
        return true;
    }

    completeSend(handle: string, operation: QueuedOperation): boolean {
        const connection = this.connections.get(handle);
        if (connection === undefined) return false;
        if (!connection.queue.remove(operation)) return false;
        this.globalSendRecords -= 1;
        this.globalSendBytes -= operation.byteLength;
        return true;
    }

    discardSend(handle: string, operation: QueuedOperation, reason: SendCancelReason): boolean {
        const connection = this.connections.get(handle);
        if (connection === undefined) return false;
        if (!connection.queue.remove(operation)) return false;
        this.globalSendRecords -= 1;
        this.globalSendBytes -= operation.byteLength;
        operation.release();
        operation.cancel(reason);
        return true;
    }

    apply(event: WireEvent): EventApplication {
        switch (event.type) {
            case 'ready':
                this.diagnostics.push({ direction: 'inbound', type: 'ready' });
                return 'applied';
            case 'device_found':
                return this.applyDeviceFound(event);
            case 'scan_state':
                return this.applyScanState(event);
            case 'connected':
                this.diagnostics.push({
                    direction: 'inbound',
                    type: 'connected',
                    connection_handle: event.connection_handle,
                });
                if (this.connections.has(event.connection_handle)) return 'applied';
                return this.connectionTombstones.has(event.connection_handle)
                    ? 'stale'
                    : 'protocol_violation';
            case 'disconnected': {
                this.diagnostics.push({
                    direction: 'inbound',
                    type: 'disconnected',
                    connection_handle: event.connection_handle,
                });
                const connection = this.connections.get(event.connection_handle);
                if (connection !== undefined) {
                    this.closeConnection(
                        event.connection_handle,
                        connection.status === 'disconnecting' ? 'user' : 'loss',
                    );
                    return 'applied';
                }
                return this.connectionTombstones.has(event.connection_handle)
                    ? 'stale'
                    : 'protocol_violation';
            }
            case 'sent':
                this.diagnostics.push({
                    direction: 'inbound',
                    type: 'sent',
                    connection_handle: event.connection_handle,
                });
                if (this.connections.has(event.connection_handle)) return 'applied';
                return this.connectionTombstones.has(event.connection_handle)
                    ? 'stale'
                    : 'protocol_violation';
            case 'message': {
                const bytes = decodedBase64Length(event.record_base64);
                this.diagnostics.push({
                    direction: 'inbound',
                    type: 'message',
                    connection_handle: event.connection_handle,
                    bytes,
                });
                if (this.connections.has(event.connection_handle)) return 'applied';
                return this.connectionTombstones.has(event.connection_handle)
                    ? 'stale'
                    : 'protocol_violation';
            }
            case 'shutdown_complete':
                this.diagnostics.push({
                    direction: 'inbound',
                    type: 'shutdown_complete',
                });
                return 'applied';
            case 'error':
                this.diagnostics.push({
                    direction: 'inbound',
                    type: 'error',
                    ...(event.code === undefined ? {} : { code: event.code }),
                    ...(event.stage === undefined ? {} : { stage: event.stage }),
                    ...(event.scan_handle === undefined ? {} : { scan_handle: event.scan_handle }),
                    ...(event.connection_handle === undefined
                        ? {}
                        : { connection_handle: event.connection_handle }),
                });
                if (
                    event.connection_handle !== undefined &&
                    !this.connections.has(event.connection_handle) &&
                    !this.connectionTombstones.has(event.connection_handle)
                ) {
                    return 'protocol_violation';
                }
                if (
                    event.scan_handle !== undefined &&
                    !this.scans.has(event.scan_handle) &&
                    !this.scanTombstones.has(event.scan_handle)
                ) {
                    return 'protocol_violation';
                }
                return 'applied';
        }
    }

    validationContext(apiVersion: number | null): ValidationContext {
        const scans = new Map<
            string,
            { status: 'starting' | 'started' | 'stopping' | 'stopped' }
        >();
        for (const [handle, scan] of this.scans) {
            scans.set(handle, { status: scan.status });
        }
        for (const [handle, generation] of this.scanTombstones) {
            void generation;
            scans.set(handle, { status: 'stopped' });
        }
        const connections = new Map<string, { status: string; generation: number }>();
        for (const [handle, connection] of this.connections) {
            connections.set(handle, {
                status: connection.status,
                generation: connection.generation,
            });
        }
        for (const [handle, generation] of this.connectionTombstones) {
            connections.set(handle, { status: 'closed', generation });
        }
        return {
            phase: apiVersion === null ? 'pre_ready' : 'ready',
            apiVersion,
            pending: new Map(),
            scans,
            connections,
        };
    }

    snapshot(): ConnectorSnapshot {
        return {
            scans: [...this.scans.values()].map((scan) => ({
                handle: scan.handle,
                status: scan.status,
                generation: scan.generation,
                deviceCount: scan.devices.size,
                startedAt: scan.startedAt,
                expiresAt: scan.expiresAt,
                locallyExpired: scan.expiredAt !== undefined,
            })),
            devices: [...this.devices.values()].map((device) => ({
                handle: device.handle,
                label: device.label,
                ...(device.platformHint === undefined ? {} : { platformHint: device.platformHint }),
                advertisementBytes: device.advertisementBytes,
                scanHandle: device.scanHandle,
                generation: device.generation,
            })),
            connections: [...this.connections.values()].map((connection) => ({
                handle: connection.handle,
                ...(connection.deviceHandle === undefined
                    ? {}
                    : { deviceHandle: connection.deviceHandle }),
                ...(connection.platformHint === undefined
                    ? {}
                    : { platformHint: connection.platformHint }),
                status: connection.status,
                generation: connection.generation,
                queuedOperations: connection.queue.size,
                queuedBytes: connection.queue.bytes,
                sendInFlight: connection.queue.list().some((operation) => operation.posted),
            })),
            diagnostics: this.diagnostics.entries(),
        };
    }

    reset(reason: SendCancelReason = 'reset'): void {
        for (const connection of this.connections.values()) {
            this.releaseQueue(connection, reason);
        }
        this.connections.clear();
        this.scans.clear();
        this.devices.clear();
        this.pendingScans.clear();
        this.pendingConnections.clear();
        this.scanTombstones.clear();
        this.connectionTombstones.clear();
        this.disconnectPreviousStatus.clear();
        this.globalSendRecords = 0;
        this.globalSendBytes = 0;
        this.diagnostics.clear();
    }

    private applyScanState(event: Extract<WireEvent, { type: 'scan_state' }>): EventApplication {
        this.diagnostics.push({
            direction: 'inbound',
            type: 'scan_state',
            scan_handle: event.scan_handle,
        });
        if (event.status === 'started') {
            const scan = this.scans.get(event.scan_handle);
            if (scan !== undefined && scan.status === 'started') return 'applied';
            return this.scanTombstones.has(event.scan_handle) ? 'stale' : 'protocol_violation';
        }
        const scan = this.scans.get(event.scan_handle);
        if (scan !== undefined) {
            this.stopScanRecord(scan);
            return 'applied';
        }
        return this.scanTombstones.has(event.scan_handle) ? 'stale' : 'protocol_violation';
    }

    private applyDeviceFound(
        event: Extract<WireEvent, { type: 'device_found' }>,
    ): EventApplication {
        const scan = this.scans.get(event.scan_handle);
        if (scan === undefined) {
            return this.scanTombstones.has(event.scan_handle) ? 'stale' : 'protocol_violation';
        }
        if (scan.status !== 'started' && scan.status !== 'stopping') {
            return 'protocol_violation';
        }

        const advertisementBytes =
            event.advertisement === undefined ? 0 : decodedBase64Length(event.advertisement);
        const existing = this.devices.get(event.device_handle);
        if (existing !== undefined) {
            existing.label = event.label;
            existing.advertisementBytes = advertisementBytes;
            if (event.platform_hint === undefined) {
                delete existing.platformHint;
            } else {
                existing.platformHint = event.platform_hint;
            }
            if (existing.scanHandle !== event.scan_handle) {
                this.scans.get(existing.scanHandle)?.devices.delete(existing.handle);
                existing.scanHandle = event.scan_handle;
            }
            scan.devices.add(existing.handle);
        } else {
            if (this.devices.size >= CONNECTOR_DISCOVERED_DEVICES_MAX) {
                const connected = new Set(
                    [...this.connections.values()]
                        .map((connection) => connection.deviceHandle)
                        .filter((handle): handle is string => handle !== undefined),
                );
                let evicted: string | undefined;
                for (const handle of this.devices.keys()) {
                    if (!connected.has(handle)) {
                        evicted = handle;
                        break;
                    }
                }
                if (evicted === undefined) return 'protocol_violation';
                this.evictDevice(evicted);
            }
            this.nextDeviceGeneration += 1;
            const device: DeviceRecord = {
                handle: event.device_handle,
                label: event.label,
                advertisementBytes,
                scanHandle: event.scan_handle,
                generation: this.nextDeviceGeneration,
            };
            if (event.platform_hint !== undefined) device.platformHint = event.platform_hint;
            this.devices.set(device.handle, device);
            scan.devices.add(device.handle);
        }

        this.diagnostics.push({
            direction: 'inbound',
            type: 'device_found',
            scan_handle: event.scan_handle,
            device_handle: event.device_handle,
            bytes: advertisementBytes,
        });
        return 'applied';
    }

    private stopScanRecord(scan: ScanRecord): void {
        this.scans.delete(scan.handle);
        this.scanTombstones.set(scan.handle, scan.generation);
        while (this.scanTombstones.size > CONNECTOR_SCAN_TOMBSTONE_MAX) {
            const oldest = this.scanTombstones.keys().next().value;
            if (oldest === undefined) break;
            this.scanTombstones.delete(oldest);
        }
    }

    private rememberConnectionTombstone(handle: string, generation: number): void {
        this.connectionTombstones.set(handle, generation);
        while (this.connectionTombstones.size > CONNECTOR_CONNECTION_TOMBSTONE_MAX) {
            const oldest = this.connectionTombstones.keys().next().value;
            if (oldest === undefined) break;
            this.connectionTombstones.delete(oldest);
        }
    }

    private evictDevice(handle: string): void {
        const device = this.devices.get(handle);
        if (device === undefined) return;
        this.devices.delete(handle);
        this.scans.get(device.scanHandle)?.devices.delete(handle);
    }

    private releaseQueue(connection: ConnectionRecord, reason: SendCancelReason): void {
        const operations = connection.queue.list();
        const bytes = connection.queue.bytes;
        connection.queue.clear();
        this.globalSendRecords -= operations.length;
        this.globalSendBytes -= bytes;
        for (const operation of operations) {
            operation.release();
            if (!operation.posted) operation.cancel(reason);
        }
    }

    private cancelUnposted(connection: ConnectionRecord, reason: SendCancelReason): void {
        for (const operation of connection.queue.list()) {
            if (operation.posted) continue;
            if (!connection.queue.remove(operation)) continue;
            this.globalSendRecords -= 1;
            this.globalSendBytes -= operation.byteLength;
            operation.release();
            operation.cancel(reason);
        }
    }
}
