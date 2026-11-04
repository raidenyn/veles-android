import { describe, expect, it } from 'vitest';

import { ConnectorApp, type ConnectorAction, type ConnectorView } from '../../src/connector';
import { ConnectorController } from '../../src/connector/controller';
import type { PeerHint } from '../../src/connector/hints';
import type { ConnectorViewModel } from '../../src/connector/view-model';
import { assertOutgoingEnvelope } from './contract-oracle';
import { MockNativePort } from './mock-port';
import {
    HANDLE_CONNECTION,
    HANDLE_CONNECTION_2,
    HANDLE_DEVICE,
    HANDLE_DEVICE_2,
    HANDLE_SCAN,
    connectedEvent,
    deviceFoundEvent,
    disconnectedEvent,
    messageEvent,
    readyEvent,
    scanStartedEvent,
    scanStoppedEvent,
    sentEvent,
    shutdownCompleteEvent,
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
}

class RecordingView implements ConnectorView {
    readonly models: ConnectorViewModel[] = [];
    private listener: ((action: ConnectorAction) => void) | null = null;

    render(viewModel: ConnectorViewModel): void {
        this.models.push(viewModel);
    }

    onAction(listener: (action: ConnectorAction) => void): () => void {
        this.listener = listener;
        return () => {
            if (this.listener === listener) this.listener = null;
        };
    }

    emit(action: ConnectorAction): void {
        this.listener?.(action);
    }

    get last(): ConnectorViewModel {
        const model = this.models.at(-1);
        if (model === undefined) throw new Error('no view model rendered');
        return model;
    }
}

function setup() {
    const clock = new FakeClock();
    const ports: MockNativePort[] = [];
    const controller = new ConnectorController({
        connectNative: () => {
            const port = new MockNativePort();
            ports.push(port);
            return port;
        },
        lastError: () => ports.at(-1)?.lastError,
        now: () => clock.now,
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
        requestTimeoutMs: 30_000,
    });
    const currentPort = (): MockNativePort => {
        const port = ports.at(-1);
        if (port === undefined) throw new Error('no port created');
        return port;
    };
    return { controller, clock, ports, currentPort };
}

type Context = ReturnType<typeof setup>;

async function openReady(context: Context = setup()): Promise<Context> {
    context.controller.open();
    const hello = context.controller.hello();
    const posted = await context.currentPort().nextPosted();
    expect(posted).toMatchObject({
        envelope_version: 1,
        type: 'hello',
        api_versions: [1],
    });
    await context.currentPort().emit(readyEvent({ request_id: posted.request_id as string }));
    const result = await hello;
    expect(result.ok).toBe(true);
    return context;
}

async function startScan(context: Context, scanHandle = HANDLE_SCAN) {
    const request = context.controller.startScan();
    const posted = await context.currentPort().nextPosted();
    expect(posted).toMatchObject({ type: 'start_scan', api_version: 1 });
    await context.currentPort().emit(
        scanStartedEvent({
            request_id: posted.request_id as string,
            scan_handle: scanHandle,
        }),
    );
    const result = await request;
    expect(result.ok).toBe(true);
    return posted;
}

async function connectDevice(context: Context, deviceHandle: string, connectionHandle: string) {
    const request = context.controller.connect({ deviceHandle });
    const posted = await context.currentPort().nextPosted();
    expect(posted).toMatchObject({
        type: 'connect',
        api_version: 1,
        device_handle: deviceHandle,
    });
    await context.currentPort().emit(
        connectedEvent({
            request_id: posted.request_id as string,
            connection_handle: connectionHandle,
        }),
    );
    const result = await request;
    expect(result.ok).toBe(true);
    return posted;
}

async function connectHint(context: Context, index: number) {
    const request = context.controller.connect({ platformHint: `phone-${index}` });
    const posted = await context.currentPort().nextPosted();
    expect(posted).toMatchObject({ type: 'connect', platform_hint: `phone-${index}` });
    await context.currentPort().emit(
        connectedEvent({
            request_id: posted.request_id as string,
            connection_handle: index.toString(16).padStart(16, '0'),
        }),
    );
    const result = await request;
    expect(result.ok).toBe(true);
}

function bytes(length: number, value = 0x5a): Uint8Array {
    return new Uint8Array(length).fill(value);
}

function base64(value: Uint8Array): string {
    return Buffer.from(value).toString('base64');
}

function must<T>(value: T | undefined): T {
    if (value === undefined) throw new Error('missing test value');
    return value;
}

describe('OTP-07 integration — host negotiation and visible failures', () => {
    it('negotiates only the supported API overlap and fails closed before BLE work', async () => {
        const context = setup();
        context.controller.open();
        const hello = context.controller.hello();
        const posted = await context.currentPort().nextPosted();
        expect(posted).toEqual({
            envelope_version: 1,
            type: 'hello',
            request_id: expect.any(String),
            api_versions: [1],
        });

        const beforeReady = await context.controller.startScan();
        expect(beforeReady).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'protocol_violation', stage: 'pre_ready' },
        });
        expect(context.currentPort().posted).toHaveLength(1);

        await context.currentPort().emit(
            readyEvent({
                request_id: posted.request_id as string,
                api_version: 2,
            }),
        );
        const result = await hello;
        expect(result).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'incompatible_api' },
        });

        const afterIncompatible = await context.controller.startScan();
        expect(afterIncompatible.ok).toBe(false);
        expect(
            context
                .currentPort()
                .posted.filter((message) => (message as { type: string }).type === 'start_scan'),
        ).toEqual([]);
    });

    it('fails closed on a pre-ready wire incompatible_api without issuing BLE requests', async () => {
        const context = setup();
        context.controller.open();
        const hello = context.controller.hello();
        const posted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            wireErrorEvent({
                request_id: posted.request_id as string,
                api_version: undefined,
                code: 'incompatible_api',
                recoverability: 'fatal',
                stage: 'negotiation',
            }),
        );
        const result = await hello;
        expect(result).toMatchObject({
            ok: false,
            source: 'wire',
            error: { code: 'incompatible_api' },
        });
        expect(
            context
                .currentPort()
                .posted.filter((message) => (message as { type: string }).type === 'start_scan'),
        ).toEqual([]);
    });

    it('shows install guidance for an asynchronous missing host and disables BLE controls', async () => {
        const context = setup();
        const view = new RecordingView();
        const activePeers = async (): Promise<PeerHint[]> => [];
        const app = new ConnectorApp({
            controller: context.controller,
            view,
            trustSource: { activePeers },
            setTimeout: context.clock.setTimeout,
            clearTimeout: context.clock.clearTimeout,
        });

        const started = app.start();
        await context.currentPort().nextPosted();
        await context.currentPort().disconnect('Specified native messaging host not found.');
        await started;

        expect(view.last.guidance?.kind).toBe('missing_host');
        expect(view.last.guidance?.message).toContain('Install the compatible Veles');
        expect(view.last.controls.ble).toBe(false);
        expect(view.last.controls.search.enabled).toBe(false);
        expect(view.last.controls.reconnect).toMatchObject({ label: 'Retry', enabled: true });
    });

    it('shows update guidance and disables BLE controls for an incompatible host', async () => {
        const context = setup();
        const view = new RecordingView();
        const app = new ConnectorApp({
            controller: context.controller,
            view,
            trustSource: { activePeers: async () => [] },
            setTimeout: context.clock.setTimeout,
            clearTimeout: context.clock.clearTimeout,
        });

        const started = app.start();
        const posted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(readyEvent({ request_id: posted.request_id as string, api_version: 2 }));
        await started;

        expect(view.last.guidance?.kind).toBe('incompatible_api');
        expect(view.last.guidance?.message).toContain('Update the Veles Native Bridge');
        expect(view.last.controls.ble).toBe(false);
        expect(
            context
                .currentPort()
                .posted.filter((message) => (message as { type: string }).type === 'start_scan'),
        ).toEqual([]);
    });
});

describe('OTP-07 integration — one scan, multiple phones, and whole records', () => {
    it('lists two phones from one scan, connects both, and routes whole records independently', async () => {
        const context = await openReady();
        await startScan(context);
        await context.currentPort().emit(deviceFoundEvent());
        await context.currentPort().emit(
            deviceFoundEvent({
                device_handle: HANDLE_DEVICE_2,
                label: 'Second phone',
                platform_hint: 'winrt:second',
            }),
        );

        expect(context.controller.snapshot().devices.map((device) => device.handle)).toEqual([
            HANDLE_DEVICE,
            HANDLE_DEVICE_2,
        ]);

        await connectDevice(context, HANDLE_DEVICE, HANDLE_CONNECTION);
        await connectDevice(context, HANDLE_DEVICE_2, HANDLE_CONNECTION_2);
        expect(
            context.controller.snapshot().connections.map((connection) => connection.handle),
        ).toEqual([HANDLE_CONNECTION, HANDLE_CONNECTION_2]);

        const records: { connectionHandle: string; bytes: Uint8Array }[] = [];
        context.controller.onRecord((record) => records.push(record));
        await context
            .currentPort()
            .emit(messageEvent({ connection_handle: HANDLE_CONNECTION_2, record_base64: 'AQID' }));
        expect(records).toHaveLength(1);
        expect(must(records[0]).connectionHandle).toBe(HANDLE_CONNECTION_2);
        expect([...must(records[0]).bytes]).toEqual([1, 2, 3]);

        const first = context.controller.send(HANDLE_CONNECTION, bytes(3, 1));
        const firstPosted = await context.currentPort().nextPosted();
        expect(firstPosted).toEqual({
            envelope_version: 1,
            api_version: 1,
            type: 'send',
            request_id: expect.any(String),
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQEB',
        });

        const second = context.controller.send(HANDLE_CONNECTION_2, bytes(4, 2));
        const secondPosted = await context.currentPort().nextPosted();
        expect(secondPosted).toMatchObject({
            type: 'send',
            connection_handle: HANDLE_CONNECTION_2,
            record_base64: 'AgICAg==',
        });

        await context.currentPort().emit(
            sentEvent({
                request_id: firstPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        expect((await first).ok).toBe(true);
        await context.currentPort().emit(
            sentEvent({
                request_id: secondPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        expect((await second).ok).toBe(true);
    });

    it('keeps a 4096-byte inbound record whole and rejects 4097 bytes without delivery', async () => {
        const context = await openReady();
        await startScan(context);
        await context.currentPort().emit(deviceFoundEvent());
        await connectDevice(context, HANDLE_DEVICE, HANDLE_CONNECTION);

        const records: { connectionHandle: string; bytes: Uint8Array }[] = [];
        context.controller.onRecord((record) => records.push(record));
        const exact = bytes(4096, 0x33);
        await context.currentPort().emit(
            messageEvent({
                connection_handle: HANDLE_CONNECTION,
                record_base64: base64(exact),
            }),
        );
        expect(records).toHaveLength(1);
        expect(must(records[0]).bytes).toEqual(exact);

        await context.currentPort().emit(
            messageEvent({
                connection_handle: HANDLE_CONNECTION,
                record_base64: base64(bytes(4097, 0x44)),
            }),
        );
        expect(records).toHaveLength(1);
        expect(context.controller.isOpen()).toBe(false);
        expect(context.controller.snapshot().connections).toEqual([]);
    });

    it('posts a 4096-byte send as one whole base64 record and completes only on correlated sent', async () => {
        const context = await openReady();
        await startScan(context);
        await context.currentPort().emit(deviceFoundEvent());
        await connectDevice(context, HANDLE_DEVICE, HANDLE_CONNECTION);

        const record = bytes(4096, 0xa5);
        const send = context.controller.send(HANDLE_CONNECTION, record);
        const posted = await context.currentPort().nextPosted();
        expect(posted).toEqual({
            envelope_version: 1,
            api_version: 1,
            type: 'send',
            request_id: expect.any(String),
            connection_handle: HANDLE_CONNECTION,
            record_base64: base64(record),
        });
        expect(Buffer.from(posted.record_base64 as string, 'base64')).toEqual(Buffer.from(record));

        // A record arriving on the same connection is not an application
        // acknowledgement of the send; the pending send remains unsettled.
        let settled = false;
        void send.then(() => {
            settled = true;
        });
        await context.currentPort().emit(
            messageEvent({
                connection_handle: HANDLE_CONNECTION,
                record_base64: base64(bytes(64, 0x01)),
            }),
        );
        await Promise.resolve();
        expect(settled).toBe(false);

        // Transport-write semantics: only the correlated `sent` event
        // terminalizes this request, and it does not itself imply delivery or
        // authentication.
        await context.currentPort().emit(
            sentEvent({
                request_id: posted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        expect((await send).ok).toBe(true);
        expect(context.controller.snapshot().connections[0]?.status).toBe('connected');
    });

    it('rejects an empty or oversize local send before posting', async () => {
        const context = await openReady();
        await startScan(context);
        await context.currentPort().emit(deviceFoundEvent());
        await connectDevice(context, HANDLE_DEVICE, HANDLE_CONNECTION);
        const postedBefore = context.currentPort().posted.length;

        const empty = await context.controller.send(HANDLE_CONNECTION, new Uint8Array(0));
        expect(empty).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'protocol_violation', stage: 'record' },
        });
        const oversize = await context.controller.send(HANDLE_CONNECTION, bytes(5121));
        expect(oversize).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'protocol_violation', stage: 'record' },
        });
        expect(context.currentPort().posted).toHaveLength(postedBefore);
    });
});

describe('OTP-07 integration — lifecycle and stale generations', () => {
    it('synthesizes exactly one port_closed for each pending request on port death', async () => {
        const context = await openReady();
        await startScan(context);
        await context.currentPort().emit(deviceFoundEvent());
        await context.currentPort().emit(deviceFoundEvent({ device_handle: HANDLE_DEVICE_2 }));
        await connectDevice(context, HANDLE_DEVICE, HANDLE_CONNECTION);
        await connectDevice(context, HANDLE_DEVICE_2, HANDLE_CONNECTION_2);

        const first = context.controller.send(HANDLE_CONNECTION, bytes(1, 1));
        await context.currentPort().nextPosted();
        const second = context.controller.send(HANDLE_CONNECTION_2, bytes(1, 2));
        await context.currentPort().nextPosted();

        const failures: string[] = [];
        context.controller.onFailure((failure) =>
            failures.push(`${failure.code}:${failure.stage}`),
        );
        await context.currentPort().disconnect();

        expect(await first).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'internal', stage: 'port_closed' },
        });
        expect(await second).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'internal', stage: 'port_closed' },
        });
        expect(failures.filter((value) => value === 'internal:port_closed')).toHaveLength(2);
        expect(context.controller.isOpen()).toBe(false);
    });

    it('does not replay after send timeout and a late sent cannot resurrect the connection', async () => {
        const context = await openReady();
        await startScan(context);
        await context.currentPort().emit(deviceFoundEvent());
        await connectDevice(context, HANDLE_DEVICE, HANDLE_CONNECTION);

        const send = context.controller.send(HANDLE_CONNECTION, bytes(3, 1));
        const posted = await context.currentPort().nextPosted();
        context.clock.advance(30_000);
        expect(await send).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'timeout' },
        });
        expect(
            context
                .currentPort()
                .posted.filter((message) => (message as { type: string }).type === 'send'),
        ).toHaveLength(1);

        await context.currentPort().emit(
            sentEvent({
                request_id: posted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        expect(context.controller.isOpen()).toBe(false);
        expect(context.controller.snapshot().connections).toEqual([]);
    });

    it('fails closed on a duplicate terminal without replaying or mutating the link', async () => {
        const context = await openReady();
        await startScan(context);
        await context.currentPort().emit(deviceFoundEvent());
        await connectDevice(context, HANDLE_DEVICE, HANDLE_CONNECTION);

        const send = context.controller.send(HANDLE_CONNECTION, bytes(2, 7));
        const posted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            sentEvent({
                request_id: posted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        expect((await send).ok).toBe(true);
        const sendsBefore = context
            .currentPort()
            .posted.filter((message) => (message as { type: string }).type === 'send').length;

        const failures: string[] = [];
        context.controller.onFailure((failure) => failures.push(failure.code));
        await context.currentPort().emit(
            sentEvent({
                request_id: posted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        expect(context.controller.isOpen()).toBe(false);
        expect(
            context
                .currentPort()
                .posted.filter((message) => (message as { type: string }).type === 'send'),
        ).toHaveLength(sendsBefore);
        expect(failures).toContain('protocol_violation');
    });

    it('ignores events from an old port generation after replacement negotiation', async () => {
        const context = await openReady();
        await startScan(context);
        await context.currentPort().emit(deviceFoundEvent());
        await connectDevice(context, HANDLE_DEVICE, HANDLE_CONNECTION);
        const oldPort = context.currentPort();
        const oldScanRequest = oldPort.posted.find(
            (message) => (message as { type: string }).type === 'start_scan',
        ) as { request_id: string };

        await oldPort.disconnect();
        expect(context.controller.isOpen()).toBe(false);

        context.controller.open();
        const hello = context.controller.hello();
        const replacementHello = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(readyEvent({ request_id: replacementHello.request_id as string }));
        expect((await hello).ok).toBe(true);
        const replacementPort = context.currentPort();
        expect(replacementPort).not.toBe(oldPort);

        await startScan(context);
        await replacementPort.emit(deviceFoundEvent());
        await connectDevice(context, HANDLE_DEVICE, HANDLE_CONNECTION);
        const before = replacementPort.posted.length;

        await oldPort.emit(
            sentEvent({
                request_id: oldScanRequest.request_id,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        await oldPort.emit(
            disconnectedEvent({
                connection_handle: HANDLE_CONNECTION,
            }),
        );

        expect(replacementPort.posted).toHaveLength(before);
        expect(context.controller.isOpen()).toBe(true);
        expect(context.controller.snapshot().connections).toHaveLength(1);
        expect(context.controller.snapshot().connections[0]?.handle).toBe(HANDLE_CONNECTION);
    });

    it('intentional close suppresses automatic reconnect after the port dies', async () => {
        const context = await openReady();
        await context.controller.close('user');
        expect(context.controller.isOpen()).toBe(false);
        const postedBefore = context.currentPort().posted.length;
        context.clock.advance(60_000);
        await Promise.resolve();
        expect(context.currentPort().posted).toHaveLength(postedBefore);
    });
});

describe('OTP-07 integration — independent count and byte caps (Task 3 residual)', () => {
    function sendSized(context: Context, handle: string, length: number, value = 0x01) {
        return context.controller.send(handle, bytes(length, value));
    }

    it('rejects on the per-link byte ceiling below the record-count ceiling', async () => {
        const context = await openReady();
        await startScan(context);
        await connectHint(context, 1);
        const connectionHandle = '0000000000000001';

        for (let value = 1; value <= 4; value += 1) {
            void sendSized(context, connectionHandle, 4096, value);
        }
        await context.currentPort().nextPosted();

        const connection = must(
            context.controller
                .snapshot()
                .connections.find((row) => row.handle === connectionHandle),
        );
        expect(connection).toMatchObject({
            queuedOperations: 4,
            queuedBytes: 16_384,
            sendInFlight: true,
        });

        const rejected = await sendSized(context, connectionHandle, 1, 5);
        expect(rejected).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'cap_exceeded' },
        });
    });

    it('rejects on the global byte ceiling below the global record-count ceiling', async () => {
        const context = await openReady();
        await startScan(context);
        for (let index = 1; index <= 9; index += 1) await connectHint(context, index);

        for (let index = 1; index <= 8; index += 1) {
            const connectionHandle = index.toString(16).padStart(16, '0');
            for (let slot = 0; slot < 4; slot += 1) {
                void context.controller.send(connectionHandle, bytes(4096, index * 16 + slot));
            }
        }
        await context.currentPort().nextPosted();

        const total = context.controller
            .snapshot()
            .connections.reduce((sum, connection) => sum + connection.queuedBytes, 0);
        expect(total).toBe(131_072);
        const recordCount = context.controller
            .snapshot()
            .connections.reduce((sum, connection) => sum + connection.queuedOperations, 0);
        expect(recordCount).toBe(32);

        const ninthHandle = (9).toString(16).padStart(16, '0');
        const rejected = await context.controller.send(ninthHandle, bytes(1));
        expect(rejected).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'cap_exceeded' },
        });
    });

    it('rejects on the per-link record-count ceiling below the byte ceiling', async () => {
        const context = await openReady();
        await startScan(context);
        await connectHint(context, 1);
        const connectionHandle = '0000000000000001';

        for (let slot = 0; slot < 16; slot += 1) {
            void sendSized(context, connectionHandle, 1);
        }
        await context.currentPort().nextPosted();
        expect(
            must(
                context.controller
                    .snapshot()
                    .connections.find((row) => row.handle === connectionHandle),
            ),
        ).toMatchObject({ queuedOperations: 16, queuedBytes: 16 });

        const rejected = await sendSized(context, connectionHandle, 1);
        expect(rejected).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'cap_exceeded' },
        });
    });

    it('rejects on the global record-count ceiling below the global byte ceiling', async () => {
        const context = await openReady();
        await startScan(context);
        for (let index = 1; index <= 9; index += 1) await connectHint(context, index);

        for (let index = 1; index <= 8; index += 1) {
            const connectionHandle = index.toString(16).padStart(16, '0');
            for (let slot = 0; slot < 16; slot += 1) {
                void context.controller.send(connectionHandle, bytes(1, index));
            }
        }
        await context.currentPort().nextPosted();
        const snapshot = context.controller.snapshot();
        expect(
            snapshot.connections.reduce((sum, connection) => sum + connection.queuedOperations, 0),
        ).toBe(128);
        expect(
            snapshot.connections.reduce((sum, connection) => sum + connection.queuedBytes, 0),
        ).toBe(128);

        const ninthHandle = (9).toString(16).padStart(16, '0');
        const rejected = await context.controller.send(ninthHandle, bytes(1));
        expect(rejected).toMatchObject({
            ok: false,
            source: 'local',
            error: { code: 'cap_exceeded' },
        });
    });
});

describe('OTP-07 integration — request identity lifetime', () => {
    it('keeps unrelated live and pending work healthy past 4096 successful requests', async () => {
        const context = await openReady();
        await startScan(context);
        await connectDevice(context, HANDLE_DEVICE, HANDLE_CONNECTION);
        await connectDevice(context, HANDLE_DEVICE_2, HANDLE_CONNECTION_2);

        // Leave one unrelated connection with a posted, unanswered send while
        // the other link performs more than the old 4096-request lifetime.
        const pendingSend = context.controller.send(HANDLE_CONNECTION_2, bytes(2, 0x22));
        const pendingPosted = await context.currentPort().nextPosted();

        for (let index = 0; index < 4_105; index += 1) {
            const send = context.controller.send(HANDLE_CONNECTION, bytes(1, 0x11));
            const posted = await context.currentPort().nextPosted();
            await context.currentPort().emit(
                sentEvent({
                    request_id: posted.request_id as string,
                    connection_handle: HANDLE_CONNECTION,
                }),
            );
            expect((await send).ok).toBe(true);
        }

        expect(context.controller.isOpen()).toBe(true);
        expect(
            context.controller.snapshot().connections.map((connection) => connection.handle),
        ).toEqual([HANDLE_CONNECTION, HANDLE_CONNECTION_2]);

        await context.currentPort().emit(
            sentEvent({
                request_id: pendingPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        expect((await pendingSend).ok).toBe(true);

        const survivor = context.controller.send(HANDLE_CONNECTION_2, bytes(1, 0x33));
        const survivorPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            sentEvent({
                request_id: survivorPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        expect((await survivor).ok).toBe(true);
        expect(context.controller.isOpen()).toBe(true);
    });
});

describe('OTP-07 integration — independent outgoing envelope oracle', () => {
    it('validates every captured post against the frozen contract', async () => {
        const context = await openReady();
        await startScan(context);
        await context.currentPort().emit(deviceFoundEvent());
        await connectDevice(context, HANDLE_DEVICE, HANDLE_CONNECTION);

        const send = context.controller.send(HANDLE_CONNECTION, bytes(3, 0x44));
        const sendPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            sentEvent({
                request_id: sendPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        expect((await send).ok).toBe(true);

        const stop = context.controller.stopScan(HANDLE_SCAN);
        const stopPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(scanStoppedEvent({ request_id: stopPosted.request_id as string }));
        expect((await stop).ok).toBe(true);

        const disconnect = context.controller.disconnect(HANDLE_CONNECTION);
        const disconnectPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(disconnectedEvent({ request_id: disconnectPosted.request_id as string }));
        expect((await disconnect).ok).toBe(true);

        const shutdown = context.controller.shutdown();
        const shutdownPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(shutdownCompleteEvent({ request_id: shutdownPosted.request_id as string }));
        expect((await shutdown).ok).toBe(true);

        const posted = context.currentPort().posted as { type: string }[];
        expect(posted.map((message) => message.type)).toEqual([
            'hello',
            'start_scan',
            'connect',
            'send',
            'stop_scan',
            'disconnect',
            'shutdown',
        ]);
        for (const message of posted) {
            assertOutgoingEnvelope(message, { postReady: message.type !== 'hello' });
        }
    });
});
