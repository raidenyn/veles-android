import { describe, expect, it } from 'vitest';

import type { WireEvent } from '../../src/connector/protocol';
import {
    ConnectorState,
    type QueuedOperation,
    type SendCancelReason,
} from '../../src/connector/state';
import {
    HANDLE_CONNECTION,
    HANDLE_CONNECTION_2,
    HANDLE_DEVICE,
    HANDLE_DEVICE_2,
    HANDLE_SCAN,
    deviceFoundEvent,
    disconnectedEvent,
    messageEvent,
    scanStartedEvent,
    scanStoppedEvent,
} from './fixtures';

function event(value: Record<string, unknown>): WireEvent {
    return value as unknown as WireEvent;
}

function must<T>(value: T | undefined): T {
    if (value === undefined) throw new Error('missing test value');
    return value;
}

function handle(index: number): string {
    return index.toString(16).padStart(16, '0');
}

interface FakeOperation extends QueuedOperation {
    readonly cancelled: SendCancelReason[];
    readonly resolved: unknown[];
}

function operation(byteLength: number, generation = 1, portEpoch = 1): FakeOperation {
    const cancelled: SendCancelReason[] = [];
    const resolved: unknown[] = [];
    let released = false;
    const op: FakeOperation = {
        byteLength,
        connectionGeneration: generation,
        portEpoch,
        posted: false,
        bytes: new Uint8Array(byteLength),
        cancelled,
        resolved,
        release() {
            if (released) return;
            released = true;
            if (op.bytes !== null) op.bytes.fill(0);
            op.bytes = null;
        },
        cancel(reason) {
            op.release();
            cancelled.push(reason);
        },
        resolve(value) {
            op.release();
            resolved.push(value);
        },
    };
    return op;
}

function startScan(state: ConnectorState, scanHandle = HANDLE_SCAN) {
    const begun = state.beginScan();
    expect(begun).not.toBeNull();
    if (begun === null) throw new Error('scan cap unexpectedly reached');
    expect(state.acceptScan(scanHandle, begun.generation)).toBe(true);
    state.apply(event(scanStartedEvent({ scan_handle: scanHandle })));
    return begun.generation;
}

function connectDevice(state: ConnectorState, deviceHandle: string, connectionHandle: string) {
    const begun = state.beginConnect({ deviceHandle });
    expect(begun).not.toBeNull();
    if (begun === null) throw new Error('connection cap unexpectedly reached');
    expect(state.acceptConnection(connectionHandle, begun.generation)).toBe(true);
    return begun.generation;
}

describe('ConnectorState — separate scan, device, and connection stores', () => {
    it('keeps one scan with many phones and independent per-connection queues', () => {
        const state = new ConnectorState({ now: () => 1_000 });
        const scanGeneration = startScan(state);

        state.apply(event(deviceFoundEvent()));
        state.apply(
            event(
                deviceFoundEvent({
                    device_handle: HANDLE_DEVICE_2,
                    label: 'Second phone',
                    platform_hint: 'watch',
                }),
            ),
        );

        expect(state.scans.size).toBe(1);
        expect(state.scans.get(HANDLE_SCAN)).toMatchObject({
            generation: scanGeneration,
            status: 'started',
        });
        expect([...must(state.scans.get(HANDLE_SCAN)).devices]).toEqual([
            HANDLE_DEVICE,
            HANDLE_DEVICE_2,
        ]);
        expect(state.devices.size).toBe(2);
        expect(state.devices.get(HANDLE_DEVICE_2)).toMatchObject({
            label: 'Second phone',
            platformHint: 'watch',
            scanHandle: HANDLE_SCAN,
            advertisementBytes: 0,
        });

        const generationA = connectDevice(state, HANDLE_DEVICE, HANDLE_CONNECTION);
        const generationB = connectDevice(state, HANDLE_DEVICE_2, HANDLE_CONNECTION_2);
        expect(state.connections.size).toBe(2);
        expect(must(state.connections.get(HANDLE_CONNECTION)).generation).toBe(generationA);
        expect(must(state.connections.get(HANDLE_CONNECTION_2)).generation).toBe(generationB);

        expect(state.enqueueSend(HANDLE_CONNECTION, generationA, operation(1))).toBe('ok');
        expect(state.enqueueSend(HANDLE_CONNECTION, generationA, operation(2))).toBe('ok');
        expect(
            state.enqueueSend(HANDLE_CONNECTION_2, generationB, operation(3, generationB, 1)),
        ).toBe('ok');
        expect(must(state.connections.get(HANDLE_CONNECTION)).queue.size).toBe(2);
        expect(must(state.connections.get(HANDLE_CONNECTION)).queue.bytes).toBe(3);
        expect(must(state.connections.get(HANDLE_CONNECTION_2)).queue.size).toBe(1);
    });

    it('rejects a third open scan and a 33rd connection locally', () => {
        const state = new ConnectorState({ now: () => 0 });
        expect(state.beginScan()).not.toBeNull();
        expect(state.beginScan()).not.toBeNull();
        expect(state.beginScan()).toBeNull();

        for (let index = 1; index <= 32; index += 1) {
            const begun = state.beginConnect({ platformHint: `phone-${index}` });
            expect(begun, `connection ${index}`).not.toBeNull();
            if (begun === null) throw new Error('connection cap reached too early');
            expect(state.acceptConnection(handle(index), begun.generation)).toBe(true);
        }
        expect(state.beginConnect({ platformHint: 'phone-33' })).toBeNull();
        expect(state.connections.size).toBe(32);
    });

    it('FIFO-evicts the oldest non-connected discovered device and keeps connected devices', () => {
        const state = new ConnectorState({ now: () => 0 });
        startScan(state);
        const first = handle(1);
        state.apply(event(deviceFoundEvent({ device_handle: first, label: 'first' })));
        connectDevice(state, first, HANDLE_CONNECTION);

        for (let index = 2; index <= 1024; index += 1) {
            state.apply(
                event(deviceFoundEvent({ device_handle: handle(index), label: `d${index}` })),
            );
        }
        expect(state.devices.size).toBe(1024);
        expect(state.devices.has(first)).toBe(true);

        const last = handle(1025);
        state.apply(
            event(deviceFoundEvent({ device_handle: last, label: 'last', advertisement: 'AQ==' })),
        );

        expect(state.devices.size).toBe(1024);
        expect(state.devices.has(first)).toBe(true);
        expect(state.devices.has(handle(2))).toBe(false);
        expect(state.devices.has(last)).toBe(true);
        expect(must(state.devices.get(last)).advertisementBytes).toBe(1);
        expect(must(state.scans.get(HANDLE_SCAN)).devices.size).toBe(1024);
    });

    it('bounds per-connection and global send queues without mutating on rejection', () => {
        const state = new ConnectorState({ now: () => 0 });
        const generation = connectDevice(state, HANDLE_DEVICE, HANDLE_CONNECTION);
        for (let index = 0; index < 16; index += 1) {
            expect(state.enqueueSend(HANDLE_CONNECTION, generation, operation(1))).toBe('ok');
        }
        expect(state.enqueueSend(HANDLE_CONNECTION, generation, operation(1))).toBe('cap_exceeded');
        expect(must(state.connections.get(HANDLE_CONNECTION)).queue.size).toBe(16);
        expect(must(state.connections.get(HANDLE_CONNECTION)).queue.bytes).toBe(16);

        // Fill the global count (8 links x 16 records = 128), then check the
        // ninth link is refused globally even though its own queue is empty.
        const connections: { handle: string; generation: number }[] = [
            { handle: HANDLE_CONNECTION, generation },
        ];
        for (let index = 2; index <= 8; index += 1) {
            const connectionHandle = handle(index);
            const connectionGeneration = connectDevice(state, HANDLE_DEVICE, connectionHandle);
            connections.push({ handle: connectionHandle, generation: connectionGeneration });
            for (let slot = 0; slot < 16; slot += 1) {
                expect(
                    state.enqueueSend(
                        connectionHandle,
                        connectionGeneration,
                        operation(1, connectionGeneration, 1),
                    ),
                ).toBe('ok');
            }
        }
        expect(connections).toHaveLength(8);
        const ninthHandle = handle(9);
        const ninthGeneration = connectDevice(state, HANDLE_DEVICE, ninthHandle);
        expect(
            state.enqueueSend(ninthHandle, ninthGeneration, operation(1, ninthGeneration, 1)),
        ).toBe('cap_exceeded');
        expect(must(state.connections.get(ninthHandle)).queue.size).toBe(0);
    });

    it('enforces per-link and global byte ceilings including queued operations', () => {
        const state = new ConnectorState({ now: () => 0 });
        const generation = connectDevice(state, HANDLE_DEVICE, HANDLE_CONNECTION);
        expect(state.enqueueSend(HANDLE_CONNECTION, generation, operation(8_000))).toBe('ok');
        expect(state.enqueueSend(HANDLE_CONNECTION, generation, operation(8_000))).toBe('ok');
        expect(state.enqueueSend(HANDLE_CONNECTION, generation, operation(400))).toBe(
            'cap_exceeded',
        );
        expect(must(state.connections.get(HANDLE_CONNECTION)).queue.bytes).toBe(16_000);

        const globalState = new ConnectorState({ now: () => 0 });
        for (let index = 1; index <= 8; index += 1) {
            const connectionHandle = handle(index);
            const connectionGeneration = connectDevice(
                globalState,
                HANDLE_DEVICE,
                connectionHandle,
            );
            for (let slot = 0; slot < 16; slot += 1) {
                expect(
                    globalState.enqueueSend(
                        connectionHandle,
                        connectionGeneration,
                        operation(1_024, connectionGeneration, 1),
                    ),
                ).toBe('ok');
            }
        }
        const ninthHandle = handle(9);
        const ninthGeneration = connectDevice(globalState, HANDLE_DEVICE, ninthHandle);
        expect(
            globalState.enqueueSend(ninthHandle, ninthGeneration, operation(1, ninthGeneration, 1)),
        ).toBe('cap_exceeded');
        expect(
            [...globalState.connections.values()].reduce(
                (total, connection) => total + connection.queue.bytes,
                0,
            ),
        ).toBe(131_072);
    });
});

describe('ConnectorState — cancellation, idempotence, and stale handles', () => {
    it('cancels unsent operations once and treats late closed-handle events as stale', () => {
        const state = new ConnectorState({ now: () => 0 });
        const generation = connectDevice(state, HANDLE_DEVICE, HANDLE_CONNECTION);
        const queued = operation(4, generation, 1);
        expect(state.enqueueSend(HANDLE_CONNECTION, generation, queued)).toBe('ok');

        expect(state.closeConnection(HANDLE_CONNECTION, 'user')).toBe(true);
        expect(state.closeConnection(HANDLE_CONNECTION, 'user')).toBe(false);
        expect(queued.cancelled).toEqual(['user']);
        expect(queued.bytes).toBeNull();
        expect(state.connections.has(HANDLE_CONNECTION)).toBe(false);

        expect(state.apply(event(disconnectedEvent({ request_id: undefined })))).toBe('stale');
        expect(state.connections.has(HANDLE_CONNECTION)).toBe(false);

        const context = state.validationContext(1);
        expect(context.connections.get(HANDLE_CONNECTION)).toEqual({
            status: 'closed',
            generation,
        });
    });

    it('releases an in-flight operation account only when it completes, not when unsent work is cancelled', () => {
        const state = new ConnectorState({ now: () => 0 });
        const generation = connectDevice(state, HANDLE_DEVICE, HANDLE_CONNECTION);
        const inFlight = operation(5, generation, 1);
        const unsent = operation(7, generation, 1);
        expect(state.enqueueSend(HANDLE_CONNECTION, generation, inFlight)).toBe('ok');
        expect(state.enqueueSend(HANDLE_CONNECTION, generation, unsent)).toBe('ok');

        expect(state.markSendPosted(HANDLE_CONNECTION, inFlight)).toBe(true);
        expect(state.beginDisconnect(HANDLE_CONNECTION)).toEqual({ generation });
        expect(unsent.cancelled).toEqual(['user']);
        expect(must(state.connections.get(HANDLE_CONNECTION)).queue.size).toBe(1);
        expect(must(state.connections.get(HANDLE_CONNECTION)).queue.bytes).toBe(5);
        expect(state.snapshot().connections[0]).toMatchObject({
            queuedOperations: 1,
            queuedBytes: 5,
            sendInFlight: true,
        });

        expect(state.completeSend(HANDLE_CONNECTION, inFlight)).toBe(true);
        expect(must(state.connections.get(HANDLE_CONNECTION)).queue.size).toBe(0);
        expect(must(state.connections.get(HANDLE_CONNECTION)).queue.bytes).toBe(0);
    });

    it('marks an ambiguous send connection unusable and cancels everything unsent', () => {
        const state = new ConnectorState({ now: () => 0 });
        const generation = connectDevice(state, HANDLE_DEVICE, HANDLE_CONNECTION);
        const unsent = operation(2, generation, 1);
        expect(state.enqueueSend(HANDLE_CONNECTION, generation, unsent)).toBe('ok');

        expect(state.markConnectionAmbiguous(HANDLE_CONNECTION, generation)).toBe(true);
        expect(must(state.connections.get(HANDLE_CONNECTION)).status).toBe('ambiguous');
        expect(unsent.cancelled).toEqual(['ambiguous']);
        expect(state.enqueueSend(HANDLE_CONNECTION, generation, operation(2, generation, 1))).toBe(
            'stale',
        );
    });
});

describe('ConnectorState — scan lifetime, tombstones, and diagnostics', () => {
    it('keeps an expired scan wire-live until the observed stop, then tombstones it', () => {
        let now = 10_000;
        const state = new ConnectorState({ now: () => now });
        const generation = startScan(state);
        state.apply(event(deviceFoundEvent()));

        now += 299_999;
        expect(state.expireScan(HANDLE_SCAN, generation)).toBe(false);
        expect(state.scans.has(HANDLE_SCAN)).toBe(true);

        now += 1;
        expect(state.expireScan(HANDLE_SCAN, generation)).toBe(true);
        expect(state.scans.has(HANDLE_SCAN)).toBe(true);
        expect(state.scans.get(HANDLE_SCAN)).toMatchObject({ status: 'stopping' });
        expect(state.snapshot().scans[0]).toMatchObject({ locallyExpired: true });
        expect(state.devices.has(HANDLE_DEVICE)).toBe(true);
        expect(state.validationContext(1).scans.get(HANDLE_SCAN)).toEqual({
            status: 'stopping',
        });

        // A discovery already queued by the host before its stop event is
        // still valid after local expiry.
        expect(state.apply(event(deviceFoundEvent({ device_handle: HANDLE_DEVICE_2 })))).toBe(
            'applied',
        );
        expect(state.devices.has(HANDLE_DEVICE_2)).toBe(true);

        expect(state.apply(event(scanStoppedEvent({ request_id: undefined })))).toBe('applied');
        expect(state.scans.has(HANDLE_SCAN)).toBe(false);
        expect(state.validationContext(1).scans.get(HANDLE_SCAN)).toEqual({
            status: 'stopped',
        });
        expect(state.apply(event(scanStoppedEvent({ request_id: undefined })))).toBe('stale');
    });

    it('bounds diagnostics and never stores labels, hints, records, or advertisements', () => {
        const state = new ConnectorState({ now: () => 5, diagnosticsCapacity: 2 });
        startScan(state);
        state.apply(
            event(
                deviceFoundEvent({
                    label: 'Private phone label',
                    platform_hint: 'private hint',
                    advertisement: 'AQID',
                }),
            ),
        );
        connectDevice(state, HANDLE_DEVICE, HANDLE_CONNECTION);
        state.apply(event(messageEvent({ record_base64: 'AQID' })));

        const entries = state.diagnostics.entries();
        expect(entries).toHaveLength(2);
        for (const entry of entries) {
            expect(Object.keys(entry).sort()).not.toContain('label');
            expect(Object.keys(entry).sort()).not.toContain('platform_hint');
            expect(Object.keys(entry).sort()).not.toContain('record_base64');
            expect(Object.keys(entry).sort()).not.toContain('advertisement');
            expect(JSON.stringify(entry)).not.toContain('Private phone label');
            expect(JSON.stringify(entry)).not.toContain('private hint');
            expect(JSON.stringify(entry)).not.toContain('AQID');
        }
        expect(entries.at(-1)).toMatchObject({
            at: 5,
            direction: 'inbound',
            type: 'message',
            connection_handle: HANDLE_CONNECTION,
            bytes: 3,
        });
    });

    it('reset clears scans, devices, connections, and every pending queue', () => {
        const state = new ConnectorState({ now: () => 0 });
        startScan(state);
        state.apply(event(deviceFoundEvent()));
        const generation = connectDevice(state, HANDLE_DEVICE, HANDLE_CONNECTION);
        const queued = operation(3, generation, 1);
        expect(state.enqueueSend(HANDLE_CONNECTION, generation, queued)).toBe('ok');

        state.reset('reset');

        expect(state.scans.size).toBe(0);
        expect(state.devices.size).toBe(0);
        expect(state.connections.size).toBe(0);
        expect(queued.cancelled).toEqual(['reset']);
        expect(state.diagnostics.size).toBe(0);
        expect(state.validationContext(null).scans.size).toBe(0);
        expect(state.validationContext(null).connections.size).toBe(0);
    });
});

describe('BoundedQueue — accounting and non-mutating rejection', () => {
    it('enforces both count and byte ceilings and releases bytes on shift/clear', async () => {
        const { BoundedQueue } = await import('../../src/connector/state');
        const queue = new BoundedQueue<string>(
            { maxItems: 2, maxBytes: 5 },
            (value) => value.length,
        );
        expect(queue.wouldAccept('abc')).toBe(true);
        expect(queue.push('abc')).toBe(true);
        expect(queue.push('de')).toBe(true);
        expect(queue.push('f')).toBe(false);
        expect(queue.size).toBe(2);
        expect(queue.bytes).toBe(5);
        expect(queue.peek()).toBe('abc');
        expect(queue.shift()).toBe('abc');
        expect(queue.bytes).toBe(2);
        expect(queue.push('fgh')).toBe(true);
        queue.clear();
        expect(queue.size).toBe(0);
        expect(queue.bytes).toBe(0);
    });
});
