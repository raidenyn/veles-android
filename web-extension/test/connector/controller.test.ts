import { describe, expect, it, vi } from 'vitest';

import { ConnectorController } from '../../src/connector/controller';
import type { ConnectorResult } from '../../src/connector/port';
import type { WireEvent } from '../../src/connector/protocol';
import { MockNativePort } from './mock-port';
import {
    HANDLE_CONNECTION,
    HANDLE_CONNECTION_2,
    HANDLE_DEVICE,
    HANDLE_DEVICE_2,
    HANDLE_SCAN,
    HANDLE_SCAN_2,
    connectedEvent,
    deviceFoundEvent,
    disconnectedEvent,
    messageEvent,
    readyEvent,
    scanStartedEvent,
    scanStoppedEvent,
    sentEvent,
    wireErrorEvent,
} from './fixtures';

class FakeClock {
    now = 0;
    private nextId = 1;
    private readonly tasks = new Map<number, { due: number; run: () => void }>();

    readonly setTimeout = (fn: () => void, ms: number): number => {
        const id = this.nextId;
        this.nextId += 1;
        this.tasks.set(id, { due: this.now + ms, run: fn });
        return id;
    };

    readonly clearTimeout = (handle: unknown): void => {
        if (typeof handle === 'number') this.tasks.delete(handle);
    };

    advance(ms: number): void {
        this.now += ms;
        for (const [id, task] of [...this.tasks]) {
            if (task.due <= this.now) {
                this.tasks.delete(id);
                task.run();
            }
        }
    }

    get pendingTimers(): number {
        return this.tasks.size;
    }
}

function setup() {
    const mock = new MockNativePort();
    const clock = new FakeClock();
    const controller = new ConnectorController({
        connectNative: vi.fn(() => mock),
        lastError: () => mock.lastError,
        now: () => clock.now,
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
        requestTimeoutMs: 30_000,
    });
    return { controller, mock, clock };
}

async function ready(context = setup()) {
    context.controller.open();
    const hello = context.controller.hello();
    const posted = await context.mock.nextPosted();
    await context.mock.emit(readyEvent({ request_id: posted.request_id as string }));
    const result = await hello;
    expect(result.ok).toBe(true);
    return context;
}

async function startScan(context: Awaited<ReturnType<typeof ready>>, scanHandle = HANDLE_SCAN) {
    const request = context.controller.startScan();
    const posted = await context.mock.nextPosted();
    expect(posted).toMatchObject({ type: 'start_scan' });
    await context.mock.emit(
        scanStartedEvent({
            request_id: posted.request_id as string,
            scan_handle: scanHandle,
        }),
    );
    const result = await request;
    expect(result.ok).toBe(true);
    return posted;
}

async function connect(
    context: Awaited<ReturnType<typeof ready>>,
    deviceHandle: string,
    connectionHandle: string,
) {
    const request = context.controller.connect({ deviceHandle });
    const posted = await context.mock.nextPosted();
    expect(posted).toMatchObject({ type: 'connect', device_handle: deviceHandle });
    await context.mock.emit(
        connectedEvent({
            request_id: posted.request_id as string,
            connection_handle: connectionHandle,
        }),
    );
    const result = await request;
    expect(result.ok).toBe(true);
    return posted;
}

function bytes(value: number[]): Uint8Array {
    return Uint8Array.from(value);
}

function must<T>(value: T | undefined): T {
    if (value === undefined) throw new Error('missing test value');
    return value;
}

function hexHandle(index: number): string {
    return index.toString(16).padStart(16, '0');
}

async function connectHint(context: Awaited<ReturnType<typeof ready>>, index: number) {
    const request = context.controller.connect({ platformHint: `phone-${index}` });
    const posted = await context.mock.nextPosted();
    expect(posted).toMatchObject({ type: 'connect', platform_hint: `phone-${index}` });
    await context.mock.emit(
        connectedEvent({
            request_id: posted.request_id as string,
            connection_handle: hexHandle(index),
        }),
    );
    const result = await request;
    expect(result.ok).toBe(true);
}

describe('ConnectorController — operational routing', () => {
    it('reports validated unsolicited wire errors without closing healthy routing', async () => {
        const context = await ready();
        await startScan(context);
        await context.mock.emit(deviceFoundEvent());
        await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);

        const wireErrors: { code: string; stage: string; requestId?: string }[] = [];
        context.controller.onWireError((event) =>
            wireErrors.push({
                code: event.code,
                stage: event.stage,
                ...(event.request_id === undefined ? {} : { requestId: event.request_id }),
            }),
        );

        await context.mock.emit(
            wireErrorEvent({
                request_id: undefined,
                code: 'scan_failed',
                recoverability: 'retryable',
                stage: 'access_denied',
                scan_handle: HANDLE_SCAN,
            }),
        );
        expect(wireErrors).toEqual([{ code: 'scan_failed', stage: 'access_denied' }]);
        expect(context.controller.isOpen()).toBe(true);
        expect(context.controller.snapshot().connections.map((row) => row.handle)).toEqual([
            HANDLE_CONNECTION,
        ]);

        // A correlated failure settles its own request and is not also
        // re-reported as an unsolicited wire error.
        const send = context.controller.send(HANDLE_CONNECTION, bytes([1]));
        const posted = await context.mock.nextPosted();
        await context.mock.emit(
            wireErrorEvent({
                request_id: posted.request_id,
                code: 'send_failed',
                recoverability: 'retryable',
                stage: 'link_lost',
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        expect((await send).ok).toBe(false);
        expect(wireErrors).toHaveLength(1);
        expect(context.controller.isOpen()).toBe(true);
    });

    it('runs one scan, discovers two phones, connects both, and routes records', async () => {
        const context = await ready();
        await startScan(context);
        await context.mock.emit(deviceFoundEvent());
        await context.mock.emit(
            deviceFoundEvent({
                device_handle: HANDLE_DEVICE_2,
                label: 'Second phone',
                platform_hint: 'watch',
            }),
        );

        expect(context.controller.snapshot().devices).toHaveLength(2);
        await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);
        await connect(context, HANDLE_DEVICE_2, HANDLE_CONNECTION_2);
        expect(context.controller.snapshot().connections).toHaveLength(2);

        const records: { connectionHandle: string; bytes: Uint8Array }[] = [];
        context.controller.onRecord((record) => records.push(record));
        await context.mock.emit(
            messageEvent({ connection_handle: HANDLE_CONNECTION_2, record_base64: 'AQID' }),
        );
        expect(records).toHaveLength(1);
        expect(must(records[0]).connectionHandle).toBe(HANDLE_CONNECTION_2);
        expect([...must(records[0]).bytes]).toEqual([1, 2, 3]);
    });

    it('serializes each connection FIFO while sends on different connections are independent', async () => {
        const context = await ready();
        await startScan(context);
        await context.mock.emit(deviceFoundEvent());
        await context.mock.emit(deviceFoundEvent({ device_handle: HANDLE_DEVICE_2 }));
        await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);
        await connect(context, HANDLE_DEVICE_2, HANDLE_CONNECTION_2);

        const first = context.controller.send(HANDLE_CONNECTION, bytes([1, 2, 3]));
        const firstPosted = await context.mock.nextPosted();
        const second = context.controller.send(HANDLE_CONNECTION, bytes([4, 5]));
        const other = context.controller.send(HANDLE_CONNECTION_2, bytes([6]));
        const otherPosted = await context.mock.nextPosted();

        expect(firstPosted).toMatchObject({
            type: 'send',
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQID',
        });
        expect(otherPosted).toMatchObject({
            type: 'send',
            connection_handle: HANDLE_CONNECTION_2,
            record_base64: 'Bg==',
        });
        expect(
            context.mock.posted.filter((message) => (message as { type: string }).type === 'send'),
        ).toHaveLength(2);

        let secondSettled = false;
        void second.then(() => {
            secondSettled = true;
        });
        await context.mock.emit(
            sentEvent({
                request_id: firstPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        expect((await first).ok).toBe(true);
        await Promise.resolve();
        expect(secondSettled).toBe(false);

        const secondPosted = await context.mock.nextPosted();
        expect(secondPosted).toMatchObject({ record_base64: 'BAU=' });
        await context.mock.emit(
            sentEvent({
                request_id: secondPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        await context.mock.emit(
            sentEvent({
                request_id: otherPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        expect((await second).ok).toBe(true);
        expect((await other).ok).toBe(true);
    });

    it('does not let a sent for one handle settle another connection', async () => {
        const context = await ready();
        await startScan(context);
        await context.mock.emit(deviceFoundEvent());
        await context.mock.emit(deviceFoundEvent({ device_handle: HANDLE_DEVICE_2 }));
        await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);
        await connect(context, HANDLE_DEVICE_2, HANDLE_CONNECTION_2);

        const first = context.controller.send(HANDLE_CONNECTION, bytes([1]));
        const firstPosted = await context.mock.nextPosted();
        const second = context.controller.send(HANDLE_CONNECTION_2, bytes([2]));
        const secondPosted = await context.mock.nextPosted();

        let secondSettled = false;
        void second.then(() => {
            secondSettled = true;
        });
        await context.mock.emit(
            sentEvent({
                request_id: firstPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        expect((await first).ok).toBe(true);
        await Promise.resolve();
        expect(secondSettled).toBe(false);

        await context.mock.emit(
            sentEvent({
                request_id: secondPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        expect((await second).ok).toBe(true);
    });

    it('rejects unsent queued sends on user disconnect and preserves the in-flight terminal', async () => {
        const context = await ready();
        await startScan(context);
        await context.mock.emit(deviceFoundEvent());
        await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);

        const inFlight = context.controller.send(HANDLE_CONNECTION, bytes([1]));
        const inFlightPosted = await context.mock.nextPosted();
        const queued = context.controller.send(HANDLE_CONNECTION, bytes([2]));
        const disconnect = context.controller.disconnect(HANDLE_CONNECTION);
        const disconnectPosted = await context.mock.nextPosted();
        expect(disconnectPosted).toMatchObject({
            type: 'disconnect',
            connection_handle: HANDLE_CONNECTION,
        });

        const queuedResult = await queued;
        expect(queuedResult).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'send_failed', stage: 'disconnected' },
        });
        expect(
            context.mock.posted.filter((message) => (message as { type: string }).type === 'send'),
        ).toHaveLength(1);

        await context.mock.emit(
            wireErrorEvent({
                request_id: inFlightPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
                code: 'send_failed',
                stage: 'disconnected',
            }),
        );
        await context.mock.emit(
            disconnectedEvent({
                request_id: disconnectPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );

        expect(await inFlight).toMatchObject({
            ok: false,
            source: 'wire',
            error: { code: 'send_failed' },
        });
        expect((await disconnect).ok).toBe(true);
        expect(context.controller.snapshot().connections).toHaveLength(0);
    });

    it('keeps a locally expired scan wire-live until the host stop, accepting queued discovery', async () => {
        const context = await ready();
        await startScan(context);
        await context.mock.emit(deviceFoundEvent());
        await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);
        expect(context.clock.pendingTimers).toBe(1);

        context.clock.advance(30_000);
        expect(context.controller.snapshot().scans).toHaveLength(1);

        context.clock.advance(270_000);
        expect(context.controller.snapshot().scans).toHaveLength(1);
        expect(context.controller.snapshot().scans[0]).toMatchObject({
            status: 'stopping',
            locallyExpired: true,
        });
        expect(
            context.mock.posted.some(
                (message) => (message as { type: string }).type === 'stop_scan',
            ),
        ).toBe(false);

        // This discovery was already queued by the host before its stop
        // event; local expiry must not turn it into a protocol violation.
        await context.mock.emit(deviceFoundEvent({ device_handle: HANDLE_DEVICE_2 }));
        expect(context.controller.snapshot().devices).toHaveLength(2);
        expect(context.controller.snapshot().connections).toHaveLength(1);

        await context.mock.emit(
            scanStoppedEvent({ request_id: undefined, scan_handle: HANDLE_SCAN }),
        );
        expect(context.controller.isOpen()).toBe(true);
        expect(context.controller.snapshot().scans).toHaveLength(0);
        expect(context.controller.snapshot().connections).toHaveLength(1);
        expect(context.clock.pendingTimers).toBe(0);
    });

    it('clears every store and pending queue when an idle port is lost', async () => {
        const context = await ready();
        await startScan(context);
        await context.mock.emit(deviceFoundEvent());
        await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);
        const queued = context.controller.send(HANDLE_CONNECTION, bytes([1]));
        await context.mock.nextPosted();
        const unsent = context.controller.send(HANDLE_CONNECTION, bytes([2]));

        await context.mock.disconnect();

        expect(context.controller.snapshot()).toMatchObject({
            scans: [],
            devices: [],
            connections: [],
        });
        expect(await queued).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'internal', stage: 'port_closed' },
        });
        expect(await unsent).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'internal', stage: 'port_closed' },
        });
        expect(context.clock.pendingTimers).toBe(0);
    });

    it('never replays an ambiguous send and keeps the connection unusable until disconnect', async () => {
        const context = await ready();
        await startScan(context);
        await context.mock.emit(deviceFoundEvent());
        await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);

        const first = context.controller.send(HANDLE_CONNECTION, bytes([1]));
        await context.mock.nextPosted();
        const second = context.controller.send(HANDLE_CONNECTION, bytes([2]));

        context.clock.advance(30_000);
        expect(await first).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'timeout', stage: 'timeout' },
        });
        expect(await second).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'send_failed', stage: 'ambiguous_send' },
        });
        expect(
            context.mock.posted.filter((message) => (message as { type: string }).type === 'send'),
        ).toHaveLength(1);
        expect(must(context.controller.snapshot().connections[0]).status).toBe('ambiguous');

        const third = await context.controller.send(HANDLE_CONNECTION, bytes([3]));
        expect(third).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'send_failed', stage: 'ambiguous_send' },
        });
        expect(
            context.mock.posted.filter((message) => (message as { type: string }).type === 'send'),
        ).toHaveLength(1);
    });

    it('preserves the release/response race: a handle-less unknown_handle is a wire terminal', async () => {
        const context = await ready();
        await startScan(context);
        await context.mock.emit(deviceFoundEvent());
        await context.mock.emit(deviceFoundEvent({ device_handle: HANDLE_DEVICE_2 }));
        await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);

        const send = context.controller.send(HANDLE_CONNECTION, bytes([1]));
        const sendPosted = await context.mock.nextPosted();
        const survivor = context.controller.connect({ deviceHandle: HANDLE_DEVICE_2 });
        const survivorPosted = await context.mock.nextPosted();

        await context.mock.emit(
            disconnectedEvent({
                request_id: undefined,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        await context.mock.emit(
            wireErrorEvent({
                request_id: sendPosted.request_id as string,
                connection_handle: undefined,
                code: 'unknown_handle',
                stage: 'connection_handle',
            }),
        );
        expect(await send).toMatchObject({
            ok: false,
            source: 'wire',
            error: { code: 'unknown_handle' },
        });
        expect(context.controller.isOpen()).toBe(true);
        expect(context.controller.snapshot().connections.map((c) => c.handle)).not.toContain(
            HANDLE_CONNECTION,
        );

        await context.mock.emit(
            connectedEvent({
                request_id: survivorPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        expect((await survivor).ok).toBe(true);
    });

    it('restores a started scan after a wire cap refusal and retries stop', async () => {
        const context = await ready();
        await startScan(context);
        await context.mock.emit(deviceFoundEvent());

        const stop = context.controller.stopScan(HANDLE_SCAN);
        const posted = await context.mock.nextPosted();
        expect(posted).toMatchObject({ type: 'stop_scan' });
        await context.mock.emit(
            wireErrorEvent({
                request_id: posted.request_id as string,
                code: 'cap_exceeded',
                recoverability: 'retryable',
                stage: 'cap_exceeded',
                scan_handle: HANDLE_SCAN,
            }),
        );
        expect(await stop).toMatchObject({
            ok: false,
            source: 'wire',
            error: { code: 'cap_exceeded' },
        });
        expect(context.controller.snapshot().scans[0]).toMatchObject({ status: 'started' });

        // The scan was never stopped on the host, so queued discovery is
        // still valid and a later stop can actually be posted.
        await context.mock.emit(deviceFoundEvent({ device_handle: HANDLE_DEVICE_2 }));
        expect(context.controller.snapshot().devices).toHaveLength(2);

        const retry = context.controller.stopScan(HANDLE_SCAN);
        const retryPosted = await context.mock.nextPosted();
        expect(retryPosted).toMatchObject({ type: 'stop_scan' });
        await context.mock.emit(
            scanStoppedEvent({
                request_id: retryPosted.request_id as string,
                scan_handle: HANDLE_SCAN,
            }),
        );
        expect((await retry).ok).toBe(true);
        expect(context.controller.snapshot().scans).toHaveLength(0);
        expect(
            context.mock.posted.filter(
                (message) => (message as { type: string }).type === 'stop_scan',
            ),
        ).toHaveLength(2);
    });

    it('restores a started scan after a local admission refusal and retries stop', async () => {
        const context = await ready();
        await startScan(context, HANDLE_SCAN);

        // Hold a second start_scan response, 32 sends, and 31 disconnects to
        // fill all 64 local request slots before the stop is attempted.
        const heldSecondScan = context.controller.startScan();
        await context.mock.nextPosted();
        for (let index = 1; index <= 32; index += 1) {
            await connectHint(context, index);
        }
        const sendIds: string[] = [];
        const sends: Promise<unknown>[] = [];
        for (let index = 1; index <= 32; index += 1) {
            sends.push(context.controller.send(hexHandle(index), bytes([index])));
            sendIds.push((await context.mock.nextPosted()).request_id as string);
        }
        const disconnects: Promise<unknown>[] = [];
        for (let index = 1; index <= 31; index += 1) {
            disconnects.push(context.controller.disconnect(hexHandle(index)));
            await context.mock.nextPosted();
        }

        const stop = await context.controller.stopScan(HANDLE_SCAN);
        expect(stop).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'cap_exceeded' },
        });
        expect(context.controller.snapshot().scans[0]).toMatchObject({ status: 'started' });
        expect(
            context.mock.posted.filter(
                (message) => (message as { type: string }).type === 'stop_scan',
            ),
        ).toHaveLength(0);

        await context.mock.emit(deviceFoundEvent({ device_handle: HANDLE_DEVICE_2 }));
        expect(context.controller.snapshot().devices).toHaveLength(1);

        await context.mock.emit(
            sentEvent({
                request_id: sendIds[0] as string,
                connection_handle: hexHandle(1),
            }),
        );
        await sends[0];

        const retry = context.controller.stopScan(HANDLE_SCAN);
        const retryPosted = await context.mock.nextPosted();
        expect(retryPosted).toMatchObject({ type: 'stop_scan' });
        await context.mock.emit(
            scanStoppedEvent({
                request_id: retryPosted.request_id as string,
                scan_handle: HANDLE_SCAN,
            }),
        );
        expect((await retry).ok).toBe(true);

        // Keep pending promises from becoming unhandled test noise.
        void heldSecondScan;
        void disconnects;
    });

    it('preserves a live connection after a wire cap refusal and retries disconnect', async () => {
        const context = await ready();
        await startScan(context);
        await context.mock.emit(deviceFoundEvent());
        await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);

        const send = context.controller.send(HANDLE_CONNECTION, bytes([1]));
        const sendPosted = await context.mock.nextPosted();
        const disconnect = context.controller.disconnect(HANDLE_CONNECTION);
        const disconnectPosted = await context.mock.nextPosted();

        await context.mock.emit(
            wireErrorEvent({
                request_id: disconnectPosted.request_id as string,
                code: 'cap_exceeded',
                recoverability: 'retryable',
                stage: 'cap_exceeded',
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        expect(await disconnect).toMatchObject({
            ok: false,
            source: 'wire',
            error: { code: 'cap_exceeded' },
        });
        expect(context.controller.snapshot().connections[0]).toMatchObject({
            status: 'connected',
            queuedOperations: 1,
            sendInFlight: true,
        });

        const records: number[] = [];
        context.controller.onRecord((record) => {
            records.push(record.bytes.length);
        });
        await context.mock.emit(
            messageEvent({ connection_handle: HANDLE_CONNECTION, record_base64: 'AQID' }),
        );
        expect(records).toEqual([3]);

        await context.mock.emit(
            sentEvent({
                request_id: sendPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        const sendResult = await send;
        expect(sendResult.ok).toBe(true);

        const retry = context.controller.disconnect(HANDLE_CONNECTION);
        const retryPosted = await context.mock.nextPosted();
        await context.mock.emit(
            disconnectedEvent({
                request_id: retryPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        expect((await retry).ok).toBe(true);
        expect(context.controller.snapshot().connections).toHaveLength(0);
    });

    it('preserves a live connection after a local admission refusal and retries disconnect', async () => {
        const context = await ready();
        await startScan(context);
        for (let index = 1; index <= 32; index += 1) {
            await connectHint(context, index);
        }
        const sendIds: string[] = [];
        const sends: Promise<unknown>[] = [];
        for (let index = 1; index <= 32; index += 1) {
            sends.push(context.controller.send(hexHandle(index), bytes([index])));
            sendIds.push((await context.mock.nextPosted()).request_id as string);
        }

        const heldStop = context.controller.stopScan(HANDLE_SCAN);
        await context.mock.nextPosted();
        const disconnects: Promise<unknown>[] = [];
        for (let index = 1; index <= 31; index += 1) {
            disconnects.push(context.controller.disconnect(hexHandle(index)));
            await context.mock.nextPosted();
        }

        const target = hexHandle(32);
        const before = context.mock.posted.length;
        const refused = await context.controller.disconnect(target);
        expect(refused).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'cap_exceeded' },
        });
        expect(context.mock.posted).toHaveLength(before);
        expect(context.controller.snapshot().connections).toHaveLength(32);
        expect(
            must(
                context.controller
                    .snapshot()
                    .connections.find((connection) => connection.handle === target),
            ),
        ).toMatchObject({
            status: 'connected',
            queuedOperations: 1,
            sendInFlight: true,
        });

        // Free exactly one request slot and retry the same handle.
        await context.mock.emit(
            sentEvent({
                request_id: sendIds[31] as string,
                connection_handle: target,
            }),
        );
        await sends[31];

        const retry = context.controller.disconnect(target);
        const retryPosted = await context.mock.nextPosted();
        await context.mock.emit(
            disconnectedEvent({
                request_id: retryPosted.request_id as string,
                connection_handle: target,
            }),
        );
        expect((await retry).ok).toBe(true);
        expect(context.controller.snapshot().connections).toHaveLength(31);

        void heldStop;
        void disconnects;
    });

    it('notifies onChange for queued sends, stop intent, and disconnect intent', async () => {
        const context = await ready();
        await startScan(context);
        await context.mock.emit(deviceFoundEvent());
        await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);

        const changes: ReturnType<typeof context.controller.snapshot>[] = [];
        context.controller.onChange((snapshot) => changes.push(snapshot));
        const first = context.controller.send(HANDLE_CONNECTION, bytes([1]));
        await context.mock.nextPosted();
        changes.length = 0;

        const second = context.controller.send(HANDLE_CONNECTION, bytes([2]));
        expect(must(changes.at(-1)).connections[0]).toMatchObject({
            queuedOperations: 2,
            sendInFlight: true,
        });

        const stop = context.controller.stopScan(HANDLE_SCAN);
        expect(must(changes.at(-1)).scans[0]).toMatchObject({ status: 'stopping' });

        const disconnect = context.controller.disconnect(HANDLE_CONNECTION);
        expect(must(changes.at(-1)).connections[0]).toMatchObject({
            status: 'disconnecting',
            queuedOperations: 1,
        });

        void first;
        void second;
        void stop;
        void disconnect;
    });

    it('owns the caller copy and releases it on cancellation', async () => {
        const context = await ready();
        await startScan(context);
        await context.mock.emit(deviceFoundEvent());
        await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);

        const first = context.controller.send(HANDLE_CONNECTION, bytes([1]));
        const firstPosted = await context.mock.nextPosted();

        const caller = Uint8Array.from([7, 8, 9]);
        const second = context.controller.send(HANDLE_CONNECTION, caller);
        caller.fill(0x55);
        const connection = must(context.controller.state.connections.get(HANDLE_CONNECTION));
        const queuedSecond = must(connection.queue.list()[1]);
        expect([...must(queuedSecond.bytes ?? undefined)]).toEqual([7, 8, 9]);

        await context.mock.emit(
            sentEvent({
                request_id: firstPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        void first;
        const secondPosted = await context.mock.nextPosted();
        expect(secondPosted).toMatchObject({
            type: 'send',
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'BwgJ',
        });

        const thirdCaller = Uint8Array.from([4, 5]);
        const third = context.controller.send(HANDLE_CONNECTION, thirdCaller);
        const queuedThird = must(
            must(context.controller.state.connections.get(HANDLE_CONNECTION)).queue.list()[1],
        );
        expect([...must(queuedThird.bytes ?? undefined)]).toEqual([4, 5]);

        const disconnect = context.controller.disconnect(HANDLE_CONNECTION);
        expect(await third).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'send_failed', stage: 'disconnected' },
        });
        expect(queuedThird.bytes).toBeNull();

        await context.mock.emit(
            sentEvent({
                request_id: secondPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        expect((await second).ok).toBe(true);
        void disconnect;
    });

    it('accounts the posted in-flight send against per-connection count and byte caps', async () => {
        const context = await ready();
        await startScan(context);
        await context.mock.emit(deviceFoundEvent());
        await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);

        const sends: Promise<unknown>[] = [];
        for (let index = 0; index < 16; index += 1) {
            sends.push(context.controller.send(HANDLE_CONNECTION, bytes(new Array(1024).fill(1))));
        }
        const firstPosted = await context.mock.nextPosted();
        expect(context.controller.snapshot().connections[0]).toMatchObject({
            queuedOperations: 16,
            queuedBytes: 16_384,
            sendInFlight: true,
        });
        expect(await context.controller.send(HANDLE_CONNECTION, bytes([1]))).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'cap_exceeded' },
        });

        await context.mock.emit(
            sentEvent({
                request_id: firstPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        await sends[0];
        const secondPosted = await context.mock.nextPosted();
        expect(secondPosted).toMatchObject({ type: 'send' });
        const accepted = context.controller.send(HANDLE_CONNECTION, bytes(new Array(1024).fill(2)));
        expect(context.controller.snapshot().connections[0]).toMatchObject({
            queuedOperations: 16,
            queuedBytes: 16_384,
            sendInFlight: true,
        });
        void accepted;
    });

    it('accounts in-flight sends against the global cap and restores capacity on cancellation', async () => {
        const context = await ready();
        await startScan(context);
        for (let index = 1; index <= 9; index += 1) {
            await connectHint(context, index);
        }
        const sends: Promise<unknown>[] = [];
        for (let index = 1; index <= 8; index += 1) {
            for (let slot = 0; slot < 16; slot += 1) {
                sends.push(
                    context.controller.send(hexHandle(index), bytes(new Array(1024).fill(index))),
                );
            }
        }
        expect(await context.controller.send(hexHandle(9), bytes([1]))).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'cap_exceeded' },
        });

        const disconnect = context.controller.disconnect(hexHandle(1));
        const accepted = context.controller.send(hexHandle(9), bytes([1]));
        expect(must(context.controller.state.connections.get(hexHandle(9))).queue.size).toBe(1);

        void sends;
        void disconnect;
        void accepted;
    });

    it('rejects caps locally before posting', async () => {
        const context = await ready();
        await startScan(context, HANDLE_SCAN);
        await startScan(context, HANDLE_SCAN_2);
        const before = context.mock.posted.length;
        const third = await context.controller.startScan();
        expect(third).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'cap_exceeded' },
        });
        expect(context.mock.posted).toHaveLength(before);

        const results: Promise<ConnectorResult<WireEvent>>[] = [];
        for (let index = 0; index < 32; index += 1) {
            const request = context.controller.connect({ platformHint: `phone-${index}` });
            const posted = await context.mock.nextPosted();
            await context.mock.emit(
                connectedEvent({
                    request_id: posted.request_id as string,
                    connection_handle: index.toString(16).padStart(16, '0'),
                }),
            );
            results.push(request as Promise<ConnectorResult<WireEvent>>);
        }
        await Promise.all(results);
        const beforeConnect = context.mock.posted.length;
        const extra = await context.controller.connect({ platformHint: 'phone-33' });
        expect(extra).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'cap_exceeded' },
        });
        expect(context.mock.posted).toHaveLength(beforeConnect);
    });
});
