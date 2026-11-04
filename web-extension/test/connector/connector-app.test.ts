import { describe, expect, it, vi } from 'vitest';

import { ConnectorApp, type ConnectorAction, type ConnectorView } from '../../src/connector';
import { ConnectorController } from '../../src/connector/controller';
import type { PeerHint } from '../../src/connector/hints';
import type { ConnectorViewModel } from '../../src/connector/view-model';
import { MockNativePort } from './mock-port';
import {
    HANDLE_CONNECTION,
    HANDLE_CONNECTION_2,
    HANDLE_DEVICE,
    HANDLE_SCAN,
    HANDLE_SCAN_2,
    connectedEvent,
    deviceFoundEvent,
    disconnectedEvent,
    readyEvent,
    scanStartedEvent,
    scanStoppedEvent,
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

    get pendingTimers(): number {
        return this.tasks.size;
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

async function flush(): Promise<void> {
    for (let index = 0; index < 20; index += 1) await Promise.resolve();
}

function setup() {
    const clock = new FakeClock();
    const ports: MockNativePort[] = [];
    const controller = new ConnectorController({
        connectNative: vi.fn(() => {
            const port = new MockNativePort();
            ports.push(port);
            return port;
        }),
        lastError: () => ports.at(-1)?.lastError,
        now: () => clock.now,
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
        requestTimeoutMs: 30_000,
    });
    const view = new RecordingView();
    const activePeers = vi.fn<() => Promise<PeerHint[]>>(async () => []);
    const app = new ConnectorApp({
        controller,
        view,
        trustSource: { activePeers },
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
    });
    const currentPort = (): MockNativePort => {
        const port = ports.at(-1);
        if (port === undefined) throw new Error('no port created');
        return port;
    };
    return { app, view, clock, ports, activePeers, currentPort, controller };
}

async function startReady(
    context = setup(),
    overrides: Record<string, unknown> = {},
): Promise<ReturnType<typeof setup>> {
    const started = context.app.start();
    const posted = await context.currentPort().nextPosted();
    await context.currentPort().emit(readyEvent({ request_id: posted.request_id, ...overrides }));
    await started;
    await flush();
    expect(context.view.last.host.state).toBe('ready');
    return context;
}

describe('connector app startup and recovery', () => {
    it('opens the host and negotiates hello on startup', async () => {
        const context = setup();
        const started = context.app.start();
        const posted = await context.currentPort().nextPosted();
        expect(posted).toMatchObject({
            type: 'hello',
            envelope_version: 1,
            api_versions: [1],
        });
        await context.currentPort().emit(readyEvent({ request_id: posted.request_id }));
        await started;
        await flush();

        expect(context.view.last).toMatchObject({
            phase: 'ready',
            hostStatusText: expect.stringContaining('veles-bridge-1.0.0'),
        });
        expect(context.view.last.controls.search.enabled).toBe(true);
    });

    it('shows install guidance for an asynchronous missing host and Retry reopens the bridge', async () => {
        const context = setup();
        const started = context.app.start();
        await context.currentPort().nextPosted();
        await context.currentPort().disconnect('Specified native messaging host not found.');
        await started;
        await flush();

        expect(context.view.last.guidance?.kind).toBe('missing_host');
        expect(context.view.last.controls.ble).toBe(false);
        expect(context.view.last.controls.reconnect).toMatchObject({
            label: 'Retry',
            enabled: true,
        });

        const retry = context.app.handleAction({ type: 'retry' });
        const retried = await context.currentPort().nextPosted();
        expect(retried).toMatchObject({ type: 'hello' });
        expect(context.ports).toHaveLength(2);
        await context.currentPort().emit(readyEvent({ request_id: retried.request_id }));
        await retry;
        await flush();
        expect(context.view.last.guidance).toBeNull();
        expect(context.view.last.phase).toBe('ready');
    });

    it('fails closed with update guidance for an incompatible API', async () => {
        const context = setup();
        const started = context.app.start();
        const posted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            wireErrorEvent({
                api_version: undefined,
                request_id: posted.request_id,
                code: 'incompatible_api',
                recoverability: 'fatal',
                stage: 'negotiation',
            }),
        );
        await started;
        await flush();

        expect(context.view.last.guidance?.kind).toBe('incompatible_api');
        expect(context.view.last.controls.search.enabled).toBe(false);
        expect(context.view.last.controls.ble).toBe(false);
        expect(
            context
                .currentPort()
                .posted.filter((m) => (m as { type: string }).type === 'start_scan'),
        ).toEqual([]);
    });
});

describe('connector app unsolicited host failures', () => {
    it('shows macOS permission guidance for an unsolicited scan failure and keeps a healthy link', async () => {
        const context = await startReady(setup(), { platform: 'macos' });

        const scan = context.app.handleAction({ type: 'search' });
        const scanPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(scanStartedEvent({ request_id: scanPosted.request_id as string }));
        await scan;
        await context.currentPort().emit(deviceFoundEvent());
        const connect = context.app.handleAction({
            type: 'connect',
            deviceHandle: HANDLE_DEVICE,
        });
        const connectPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            connectedEvent({
                request_id: connectPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        await connect;
        await flush();
        const before = context.currentPort().posted.length;

        // The host ends an already-started scan with an unsolicited error,
        // then emits the lifecycle stop. There is no request to correlate.
        await context.currentPort().emit(
            wireErrorEvent({
                request_id: undefined,
                code: 'scan_failed',
                recoverability: 'retryable',
                stage: 'access_denied',
                scan_handle: HANDLE_SCAN,
            }),
        );
        await flush();
        expect(context.view.last.error).toMatchObject({
            code: 'scan_failed',
            stage: 'access_denied',
        });
        expect(context.view.last.guidance?.kind).toBe('macos_permission');
        expect(context.view.last.controls.reconnect).toMatchObject({
            enabled: true,
            label: 'Retry',
        });
        expect(context.controller.isOpen()).toBe(true);
        expect(context.controller.snapshot().connections.map((row) => row.handle)).toEqual([
            HANDLE_CONNECTION,
        ]);

        await context.currentPort().emit({
            envelope_version: 1,
            api_version: 1,
            type: 'scan_state',
            scan_handle: HANDLE_SCAN,
            status: 'stopped',
        });
        await flush();
        // The stop is idempotent for the displayed failure: guidance remains,
        // the survivor connection is untouched, and nothing is replayed.
        expect(context.view.last.guidance?.kind).toBe('macos_permission');
        expect(context.controller.snapshot().connections.map((row) => row.handle)).toEqual([
            HANDLE_CONNECTION,
        ]);
        expect(context.currentPort().posted).toHaveLength(before);
        expect(context.view.last.controls.search.enabled).toBe(true);

        // Search remains an explicit permission probe even while the sticky
        // guidance is shown.
        const probe = context.app.handleAction({ type: 'search' });
        const probePosted = await context.currentPort().nextPosted();
        expect(probePosted).toMatchObject({ type: 'start_scan' });
        await context.currentPort().emit(
            scanStartedEvent({
                request_id: probePosted.request_id as string,
                scan_handle: HANDLE_SCAN_2,
            }),
        );
        await probe;
        await flush();
        expect(context.view.last.guidance).toBeNull();
    });
});

describe('connector app search and connection actions', () => {
    it('searches, lists discovered phones, connects and disconnects without replaying work', async () => {
        const context = await startReady();

        const search = context.app.handleAction({ type: 'search' });
        const scanPosted = await context.currentPort().nextPosted();
        expect(scanPosted).toMatchObject({ type: 'start_scan' });
        await context
            .currentPort()
            .emit(scanStartedEvent({ request_id: scanPosted.request_id as string }));
        await search;
        await context.currentPort().emit(deviceFoundEvent());
        await flush();

        expect(context.view.last.devices[0]).toMatchObject({
            handle: HANDLE_DEVICE,
            label: 'Pixel test phone',
            canConnect: true,
        });
        expect(context.view.last.scanStatusText).toContain('Searching');

        const connect = context.app.handleAction({ type: 'connect', deviceHandle: HANDLE_DEVICE });
        const connectPosted = await context.currentPort().nextPosted();
        expect(connectPosted).toMatchObject({ type: 'connect', device_handle: HANDLE_DEVICE });
        await context.currentPort().emit(
            connectedEvent({
                request_id: connectPosted.request_id as string,
                platform_hint: 'winrt:provisional',
            }),
        );
        await connect;
        await flush();
        expect(context.view.last.connections[0]).toMatchObject({
            label: 'Pixel test phone',
            status: 'connected',
            hintLabel: 'Unverified hint: winrt:provisional',
        });
        // A connected hint is provisional display data; it never triggers a
        // trust read, authentication or durable write.
        expect(context.activePeers).not.toHaveBeenCalled();

        const beforeDisconnect = context.currentPort().posted.length;
        const disconnect = context.app.handleAction({
            type: 'disconnect',
            connectionHandle: context.view.last.connections[0]?.handle ?? '',
        });
        const disconnectPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            disconnectedEvent({
                request_id: disconnectPosted.request_id as string,
                connection_handle: context.view.last.connections[0]?.handle,
            }),
        );
        await disconnect;
        await flush();
        expect(context.view.last.connections).toEqual([]);

        context.clock.advance(60_000);
        await flush();
        expect(context.currentPort().posted).toHaveLength(beforeDisconnect + 1);
    });

    it('stops an active scan through the explicit stop control', async () => {
        const context = await startReady();
        const search = context.app.handleAction({ type: 'search' });
        const scanPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(scanStartedEvent({ request_id: scanPosted.request_id as string }));
        await search;
        await flush();
        expect(context.view.last.controls.stopScan.enabled).toBe(true);

        const stop = context.app.handleAction({ type: 'stop_scan', handle: HANDLE_SCAN });
        const stopPosted = await context.currentPort().nextPosted();
        expect(stopPosted).toMatchObject({ type: 'stop_scan', scan_handle: HANDLE_SCAN });
        await context
            .currentPort()
            .emit(scanStoppedEvent({ request_id: stopPosted.request_id as string }));
        await stop;
        await flush();
        expect(context.view.last.controls.stopScan.visible).toBe(false);
    });
});

describe('connector app shutdown and stale work', () => {
    it('shuts down explicitly and does not auto-reconnect afterwards', async () => {
        const context = await startReady();
        const shut = context.app.handleAction({ type: 'shutdown' });
        const posted = await context.currentPort().nextPosted();
        expect(posted).toMatchObject({ type: 'shutdown' });
        await context
            .currentPort()
            .emit(shutdownCompleteEvent({ request_id: posted.request_id as string }));
        await shut;
        await flush();
        expect(context.view.last.phase).toBe('shut_down');

        const postedBefore = context.currentPort().posted.length;
        context.clock.advance(60_000);
        await flush();
        expect(context.currentPort().posted).toHaveLength(postedBefore);
    });

    it('cancels a scheduled reconnect on tab close and ignores a stale hello', async () => {
        const context = await startReady();
        await context.currentPort().disconnect();
        await flush();
        expect(context.clock.pendingTimers).toBeGreaterThan(0);
        const renders = context.view.models.length;

        context.app.dispose();
        context.clock.advance(60_000);
        await flush();
        expect(context.clock.pendingTimers).toBe(0);
        expect(context.view.models).toHaveLength(renders);

        const stale = setup();
        const started = stale.app.start();
        await stale.currentPort().nextPosted();
        stale.app.dispose();
        const posted = stale.currentPort().posted[0] as { request_id: string };
        await stale.currentPort().emit(readyEvent({ request_id: posted.request_id }));
        await started;
        await flush();
        expect(stale.view.models.every((model) => model.phase !== 'ready')).toBe(true);
    });

    it('does not create a replacement port after app close even if a stale disconnect arrives', async () => {
        const context = await startReady();
        const before = context.currentPort().posted.length;

        context.app.dispose();
        await context.currentPort().disconnect();
        await flush();
        context.clock.advance(60_000);
        await flush();

        expect(context.ports).toHaveLength(1);
        expect(context.currentPort().posted).toHaveLength(before);
    });

    it('uses Retry as a real same-Mac access probe, then clears permission guidance on success', async () => {
        const context = await startReady(setup(), { platform: 'macos' });

        const scan = context.app.handleAction({ type: 'search' });
        const scanPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            wireErrorEvent({
                request_id: scanPosted.request_id,
                code: 'scan_failed',
                recoverability: 'retryable',
                stage: 'access_denied',
            }),
        );
        await scan;
        await flush();
        expect(context.view.last.guidance?.kind).toBe('macos_permission');
        // Permission guidance stays sticky, but its probe controls are usable.
        expect(context.view.last.controls.search.enabled).toBe(true);
        expect(context.view.last.controls.reconnect).toMatchObject({
            enabled: true,
            label: 'Retry',
        });

        // Retry restarts and negotiates, then performs a real scan/access probe
        // rather than stopping at hello.
        const retry = context.app.handleAction({ type: 'retry' });
        await flush();
        const retried = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(readyEvent({ request_id: retried.request_id, platform: 'macos' }));
        const probe = await context.currentPort().nextPosted();
        expect(probe).toMatchObject({ type: 'start_scan' });
        await context
            .currentPort()
            .emit(scanStartedEvent({ request_id: probe.request_id as string }));
        await retry;
        await flush();
        expect(context.view.last.guidance).toBeNull();
        expect(context.view.last.phase).toBe('ready');
        expect(context.view.last.controls.stopScan.enabled).toBe(true);

        // Once the probe succeeded, a later generic failure is not misreported
        // as a permission problem.
        const stop = context.app.handleAction({ type: 'stop_scan', handle: HANDLE_SCAN });
        const stopPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(scanStoppedEvent({ request_id: stopPosted.request_id as string }));
        await stop;
        await flush();

        const generic = context.app.handleAction({ type: 'search' });
        const genericPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            wireErrorEvent({
                request_id: genericPosted.request_id,
                code: 'scan_failed',
                recoverability: 'retryable',
                stage: 'phone_unavailable',
            }),
        );
        await generic;
        await flush();
        expect(context.view.last.guidance?.kind).not.toBe('macos_permission');
    });
});

describe('connector app automatic recovery regressions', () => {
    it('recovers a lost phone while an unrelated operation failure is displayed', async () => {
        const context = await startReady();

        const search = context.app.handleAction({ type: 'search' });
        const searchPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(scanStartedEvent({ request_id: searchPosted.request_id as string }));
        await search;
        await context.currentPort().emit(deviceFoundEvent());
        const connect = context.app.handleAction({
            type: 'connect',
            deviceHandle: HANDLE_DEVICE,
        });
        const connectPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            connectedEvent({
                request_id: connectPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        await connect;
        await flush();

        const stop = context.app.handleAction({ type: 'stop_scan', handle: HANDLE_SCAN });
        const stopPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(scanStoppedEvent({ request_id: stopPosted.request_id as string }));
        await stop;
        await flush();

        // An unrelated retryable operation failure leaves the host and the
        // phone A link valid but puts the UI in a failed phase.
        const failed = context.app.handleAction({ type: 'search' });
        const failedPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            wireErrorEvent({
                request_id: failedPosted.request_id,
                code: 'cap_exceeded',
                recoverability: 'retryable',
                stage: 'scan_start',
            }),
        );
        await failed;
        await flush();
        expect(context.view.last.phase).toBe('failed');
        expect(context.controller.isOpen()).toBe(true);
        expect(context.controller.snapshot().connections).toHaveLength(1);

        // A now disappears while that error banner is still displayed.
        const beforeLoss = context.currentPort().posted.length;
        await context.currentPort().emit(
            disconnectedEvent({
                request_id: undefined,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        await flush();
        expect(context.controller.snapshot().connections).toHaveLength(0);

        context.clock.advance(1_000);
        await flush();
        const afterLoss = context.currentPort().posted.slice(beforeLoss);
        expect(
            afterLoss.some((message) => (message as { type?: string }).type === 'start_scan'),
        ).toBe(true);
        // A search recovery must not replay A's old connect request.
        expect(afterLoss.some((message) => (message as { type?: string }).type === 'connect')).toBe(
            false,
        );
        expect(context.controller.isOpen()).toBe(true);
    });

    it('recovers the correct associated phone after a failed operation with two live links', async () => {
        const context = setup();
        context.activePeers.mockResolvedValue([
            { peerId: 'peer-a', label: 'Phone A', hint: 'winrt:phone-a' },
        ]);
        await startReady(context);

        // Establish A through the host-recovery hint path so its peer target
        // is recorded, then B through an ordinary search connection.
        await context.currentPort().disconnect();
        await flush();
        context.clock.advance(1_000);
        await flush();
        const hello = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(readyEvent({ request_id: hello.request_id, platform: 'windows' }));
        const hintConnect = await context.currentPort().nextPosted();
        expect(hintConnect).toMatchObject({
            type: 'connect',
            platform_hint: 'winrt:phone-a',
        });
        await context.currentPort().emit(
            connectedEvent({
                request_id: hintConnect.request_id as string,
                connection_handle: HANDLE_CONNECTION,
                platform_hint: 'winrt:phone-a',
            }),
        );
        await flush();

        const search = context.app.handleAction({ type: 'search' });
        const searchPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(scanStartedEvent({ request_id: searchPosted.request_id as string }));
        await search;
        await context.currentPort().emit(deviceFoundEvent());
        const connectB = context.app.handleAction({
            type: 'connect',
            deviceHandle: HANDLE_DEVICE,
        });
        const connectPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            connectedEvent({
                request_id: connectPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        await connectB;
        await flush();

        const stop = context.app.handleAction({ type: 'stop_scan', handle: HANDLE_SCAN });
        const stopPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(scanStoppedEvent({ request_id: stopPosted.request_id as string }));
        await stop;
        await flush();

        const failed = context.app.handleAction({ type: 'search' });
        const failedPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            wireErrorEvent({
                request_id: failedPosted.request_id,
                code: 'cap_exceeded',
                recoverability: 'retryable',
                stage: 'scan_start',
            }),
        );
        await failed;
        await flush();
        expect(context.view.last.phase).toBe('failed');
        expect(context.controller.snapshot().connections).toHaveLength(2);

        const beforeLoss = context.currentPort().posted.length;
        await context.currentPort().emit(
            disconnectedEvent({
                request_id: undefined,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        await flush();
        expect(context.controller.snapshot().connections.map((row) => row.handle)).toEqual([
            HANDLE_CONNECTION_2,
        ]);

        context.clock.advance(1_000);
        await flush();
        const afterLoss = context.currentPort().posted.slice(beforeLoss);
        // A's associated hint is retried; B is neither replayed nor reused.
        expect(
            afterLoss.some(
                (message) =>
                    (message as { type?: string }).type === 'connect' &&
                    (message as { platform_hint?: string }).platform_hint === 'winrt:phone-a',
            ),
        ).toBe(true);
        expect(
            afterLoss.some(
                (message) =>
                    (message as { type?: string }).type === 'connect' &&
                    (message as { device_handle?: string }).device_handle === HANDLE_DEVICE,
            ),
        ).toBe(false);
        expect(context.controller.isOpen()).toBe(true);
    });

    it('treats failed BLE recovery as failure and exhausts three host attempts', async () => {
        const context = await startReady();
        await context.currentPort().disconnect();
        await flush();

        // Attempt 1 opens a replacement host but loses it before ready.
        context.clock.advance(1_000);
        await flush();
        expect(context.ports).toHaveLength(2);
        await context.currentPort().nextPosted();
        await context.currentPort().disconnect();
        await flush();
        expect(context.view.last.reconnectStatusText).toContain('attempt 2');

        // Attempt 2 must be reached (failed recovery is not success).
        context.clock.advance(2_000);
        await flush();
        expect(context.ports).toHaveLength(3);
        await context.currentPort().nextPosted();
        await context.currentPort().disconnect();
        await flush();

        // Attempt 3 must be reached, then the auto cycle is exhausted.
        context.clock.advance(4_000);
        await flush();
        expect(context.ports).toHaveLength(4);
        await context.currentPort().nextPosted();
        await context.currentPort().disconnect();
        await flush();
        expect(context.clock.pendingTimers).toBe(0);
        expect(context.view.last.reconnectStatusText).toContain('stopped after 3');

        // Repeated loss after exhaustion does not silently renew the budget.
        context.clock.advance(60_000);
        await flush();
        expect(context.ports).toHaveLength(4);
    });

    it('resets the host allowance only after a successful recovery', async () => {
        const context = await startReady();
        await context.currentPort().disconnect();
        await flush();

        context.clock.advance(1_000);
        await flush();
        expect(context.ports).toHaveLength(2);
        const hello = await context.currentPort().nextPosted();
        await context.currentPort().emit(readyEvent({ request_id: hello.request_id }));
        const scan = await context.currentPort().nextPosted();
        expect(scan).toMatchObject({ type: 'start_scan' });
        await context
            .currentPort()
            .emit(scanStartedEvent({ request_id: scan.request_id as string }));
        await flush();

        expect(context.view.last.reconnectStatusText).toBeNull();
        // The only remaining timer is the accepted scan's 300 s expiry.
        expect(context.clock.pendingTimers).toBe(1);
    });

    it('never reconnects an intentionally disconnected peer when a different link is lost', async () => {
        const context = setup();
        context.activePeers.mockResolvedValue([
            { peerId: 'peer-a', label: 'Phone A', hint: 'winrt:phone-a' },
        ]);
        await startReady(context);

        // Host loss recovers by the one active hint and establishes A.
        await context.currentPort().disconnect();
        await flush();
        context.clock.advance(1_000);
        await flush();
        const hello = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(readyEvent({ request_id: hello.request_id, platform: 'windows' }));
        const hintConnect = await context.currentPort().nextPosted();
        expect(hintConnect).toMatchObject({
            type: 'connect',
            platform_hint: 'winrt:phone-a',
        });
        await context.currentPort().emit(
            connectedEvent({
                request_id: hintConnect.request_id as string,
                connection_handle: HANDLE_CONNECTION,
                platform_hint: 'winrt:phone-a',
            }),
        );
        await flush();

        // The user explicitly disconnects A. That intent must survive.
        const disconnectA = context.app.handleAction({
            type: 'disconnect',
            connectionHandle: HANDLE_CONNECTION,
        });
        const disconnectPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            disconnectedEvent({
                request_id: disconnectPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        await disconnectA;
        await flush();

        // Connect a different phone B through search, then lose B.
        const search = context.app.handleAction({ type: 'search' });
        const searchPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(scanStartedEvent({ request_id: searchPosted.request_id as string }));
        await search;
        await context.currentPort().emit(deviceFoundEvent());
        const connectB = context.app.handleAction({
            type: 'connect',
            deviceHandle: HANDLE_DEVICE,
        });
        const connectPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            connectedEvent({
                request_id: connectPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        await connectB;
        await flush();

        const stop = context.app.handleAction({ type: 'stop_scan', handle: HANDLE_SCAN });
        const stopPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(scanStoppedEvent({ request_id: stopPosted.request_id as string }));
        await stop;
        await flush();

        const beforeLoss = context.currentPort().posted.length;
        await context.currentPort().emit(
            disconnectedEvent({
                request_id: undefined,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        await flush();
        context.clock.advance(1_000);
        await flush();

        const afterLoss = context.currentPort().posted.slice(beforeLoss);
        expect(
            afterLoss.some(
                (message) =>
                    (message as { type?: string }).type === 'connect' &&
                    (message as { platform_hint?: string }).platform_hint === 'winrt:phone-a',
            ),
        ).toBe(false);
        expect(
            afterLoss.some((message) => (message as { type?: string }).type === 'start_scan'),
        ).toBe(true);
    });

    it('falls back to search for host recovery after an explicit user disconnect', async () => {
        const context = setup();
        context.activePeers.mockResolvedValue([
            { peerId: 'peer-a', label: 'Phone A', hint: 'winrt:phone-a' },
        ]);
        await startReady(context);

        const search = context.app.handleAction({ type: 'search' });
        const searchPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(scanStartedEvent({ request_id: searchPosted.request_id as string }));
        await search;
        await context.currentPort().emit(deviceFoundEvent());
        const connect = context.app.handleAction({
            type: 'connect',
            deviceHandle: HANDLE_DEVICE,
        });
        const connectPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            connectedEvent({
                request_id: connectPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        await connect;
        await flush();

        const disconnect = context.app.handleAction({
            type: 'disconnect',
            connectionHandle: HANDLE_CONNECTION_2,
        });
        const disconnectPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            disconnectedEvent({
                request_id: disconnectPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        await disconnect;
        await flush();

        await context.currentPort().disconnect();
        await flush();
        context.clock.advance(1_000);
        await flush();
        const hello = await context.currentPort().nextPosted();
        await context.currentPort().emit(readyEvent({ request_id: hello.request_id }));
        const recovery = await context.currentPort().nextPosted();

        expect(recovery).toMatchObject({ type: 'start_scan' });
        expect(
            context
                .currentPort()
                .posted.some(
                    (message) =>
                        (message as { type?: string }).type === 'connect' &&
                        (message as { platform_hint?: string }).platform_hint === 'winrt:phone-a',
                ),
        ).toBe(false);
    });

    it('invalidates a deferred link-recovery trust read when the port is replaced', async () => {
        const context = await startReady();

        // Establish B through search.
        const search = context.app.handleAction({ type: 'search' });
        const searchPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(scanStartedEvent({ request_id: searchPosted.request_id as string }));
        await search;
        await context.currentPort().emit(deviceFoundEvent());
        const connect = context.app.handleAction({
            type: 'connect',
            deviceHandle: HANDLE_DEVICE,
        });
        const connectPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            connectedEvent({
                request_id: connectPosted.request_id as string,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        await connect;
        await flush();

        let resolveOldRead:
            | ((peers: { peerId: string; label: string; hint: string }[]) => void)
            | undefined;
        const oldRead = new Promise<{ peerId: string; label: string; hint: string }[]>(
            (resolve) => {
                resolveOldRead = resolve;
            },
        );
        context.activePeers.mockImplementationOnce(async () => oldRead);
        context.activePeers.mockResolvedValue([]);

        // B loss starts a deferred link-recovery read for the old port.
        await context.currentPort().emit(
            disconnectedEvent({
                request_id: undefined,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        await flush();
        expect(resolveOldRead).toBeDefined();

        // The port dies and a replacement host is negotiated while the old
        // link read is still pending.
        await context.currentPort().disconnect();
        await flush();
        context.clock.advance(1_000);
        await flush();
        expect(context.ports).toHaveLength(2);
        const hello = await context.currentPort().nextPosted();
        await context.currentPort().emit(readyEvent({ request_id: hello.request_id }));
        const replacementScan = await context.currentPort().nextPosted();
        expect(replacementScan).toMatchObject({ type: 'start_scan' });
        const beforeOldRead = context.currentPort().posted.length;

        resolveOldRead?.([{ peerId: 'peer-a', label: 'Phone A', hint: 'winrt:phone-a' }]);
        await flush();
        context.clock.advance(60_000);
        await flush();

        const afterOldRead = context.currentPort().posted.slice(beforeOldRead);
        expect(
            afterOldRead.some(
                (message) =>
                    (message as { type?: string }).type === 'connect' &&
                    (message as { platform_hint?: string }).platform_hint === 'winrt:phone-a',
            ),
        ).toBe(false);
        expect(
            afterOldRead.some((message) => (message as { type?: string }).type === 'start_scan'),
        ).toBe(false);
    });

    it('retries a failed hint connect recovery and resets only on connected', async () => {
        const context = setup();
        context.activePeers.mockResolvedValue([
            { peerId: 'peer-a', label: 'Phone A', hint: 'winrt:phone-a' },
        ]);
        await startReady(context);

        await context.currentPort().disconnect();
        await flush();
        context.clock.advance(1_000);
        await flush();
        const hello = await context.currentPort().nextPosted();
        await context.currentPort().emit(readyEvent({ request_id: hello.request_id }));

        const first = await context.currentPort().nextPosted();
        expect(first).toMatchObject({ type: 'connect', platform_hint: 'winrt:phone-a' });
        await context.currentPort().emit(
            wireErrorEvent({
                request_id: first.request_id as string,
                code: 'connect_failed',
                recoverability: 'retryable',
                stage: 'phone_unavailable',
            }),
        );
        await flush();
        expect(context.view.last.reconnectStatusText).toContain('attempt 2');

        context.clock.advance(2_000);
        await flush();
        const second = await context.currentPort().nextPosted();
        expect(second).toMatchObject({ type: 'connect', platform_hint: 'winrt:phone-a' });
        await context.currentPort().emit(
            wireErrorEvent({
                request_id: second.request_id as string,
                code: 'connect_failed',
                recoverability: 'retryable',
                stage: 'phone_unavailable',
            }),
        );
        await flush();
        expect(context.view.last.reconnectStatusText).toContain('attempt 3');

        context.clock.advance(4_000);
        await flush();
        const third = await context.currentPort().nextPosted();
        expect(third).toMatchObject({ type: 'connect', platform_hint: 'winrt:phone-a' });
        await context.currentPort().emit(
            connectedEvent({
                request_id: third.request_id as string,
                connection_handle: HANDLE_CONNECTION,
                platform_hint: 'winrt:phone-a',
            }),
        );
        await flush();
        expect(context.view.last.reconnectStatusText).toBeNull();
        expect(context.view.last.connections).toHaveLength(1);
    });

    it('retries a failed scan recovery and resets only on scan started', async () => {
        const context = await startReady();
        await context.currentPort().disconnect();
        await flush();
        context.clock.advance(1_000);
        await flush();
        const hello = await context.currentPort().nextPosted();
        await context.currentPort().emit(readyEvent({ request_id: hello.request_id }));

        const first = await context.currentPort().nextPosted();
        expect(first).toMatchObject({ type: 'start_scan' });
        await context.currentPort().emit(
            wireErrorEvent({
                request_id: first.request_id as string,
                code: 'scan_failed',
                recoverability: 'retryable',
                stage: 'scan_unavailable',
            }),
        );
        await flush();
        expect(context.view.last.reconnectStatusText).toContain('attempt 2');

        context.clock.advance(2_000);
        await flush();
        const second = await context.currentPort().nextPosted();
        expect(second).toMatchObject({ type: 'start_scan' });
        await context
            .currentPort()
            .emit(scanStartedEvent({ request_id: second.request_id as string }));
        await flush();
        expect(context.view.last.reconnectStatusText).toBeNull();
    });

    it('discards an obsolete peer cycle across port replacement so later loss recovers', async () => {
        const context = setup();
        context.activePeers.mockResolvedValue([
            { peerId: 'peer-a', label: 'Phone A', hint: 'winrt:phone-a' },
        ]);
        await startReady(context);

        // Establish hinted peer A on the first replacement host.
        await context.currentPort().disconnect();
        await flush();
        context.clock.advance(1_000);
        await flush();
        const hello = await context.currentPort().nextPosted();
        await context.currentPort().emit(readyEvent({ request_id: hello.request_id }));
        const firstConnect = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            connectedEvent({
                request_id: firstConnect.request_id as string,
                connection_handle: HANDLE_CONNECTION,
                platform_hint: 'winrt:phone-a',
            }),
        );
        await flush();

        // A loss schedules peer:A but the port dies before its timer fires.
        await context
            .currentPort()
            .emit(
                disconnectedEvent({ request_id: undefined, connection_handle: HANDLE_CONNECTION }),
            );
        await flush();
        await context.currentPort().disconnect();
        await flush();

        // The host cycle reconnects A on the replacement port.
        context.clock.advance(1_000);
        await flush();
        const replacementHello = await context.currentPort().nextPosted();
        await context.currentPort().emit(readyEvent({ request_id: replacementHello.request_id }));
        const replacementConnect = await context.currentPort().nextPosted();
        expect(replacementConnect).toMatchObject({
            type: 'connect',
            platform_hint: 'winrt:phone-a',
        });
        await context.currentPort().emit(
            connectedEvent({
                request_id: replacementConnect.request_id as string,
                connection_handle: HANDLE_CONNECTION_2,
                platform_hint: 'winrt:phone-a',
            }),
        );
        await flush();

        // A later A loss must get a fresh cycle, not be blocked by the
        // obsolete pre-replacement peer cycle.
        await context.currentPort().emit(
            disconnectedEvent({
                request_id: undefined,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        await flush();
        const beforeLoss = context.currentPort().posted.length;
        context.clock.advance(1_000);
        await flush();
        const afterLoss = context.currentPort().posted.slice(beforeLoss);
        expect(
            afterLoss.some(
                (message) =>
                    (message as { type?: string }).type === 'connect' &&
                    (message as { platform_hint?: string }).platform_hint === 'winrt:phone-a',
            ),
        ).toBe(true);
    });

    it('restores ready phase and usable controls after a successful manual retry', async () => {
        const context = await startReady();

        const failed = context.app.handleAction({ type: 'search' });
        const failedPosted = await context.currentPort().nextPosted();
        await context.currentPort().emit(
            wireErrorEvent({
                request_id: failedPosted.request_id,
                code: 'scan_failed',
                recoverability: 'retryable',
                stage: 'phone_unavailable',
            }),
        );
        await failed;
        await flush();
        expect(context.view.last.phase).toBe('failed');
        expect(context.view.last.controls.search.enabled).toBe(true);

        const retry = context.app.handleAction({ type: 'search' });
        const retryPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(scanStartedEvent({ request_id: retryPosted.request_id as string }));
        await retry;
        await flush();
        expect(context.view.last.phase).toBe('ready');
        expect(context.view.last.controls.stopScan.enabled).toBe(true);

        const stop = context.app.handleAction({ type: 'stop_scan', handle: HANDLE_SCAN });
        const stopPosted = await context.currentPort().nextPosted();
        await context
            .currentPort()
            .emit(scanStoppedEvent({ request_id: stopPosted.request_id as string }));
        await stop;
        await flush();
        expect(context.view.last.phase).toBe('ready');
        expect(context.view.last.controls.search.enabled).toBe(true);

        // The next real action is still usable, not rejected by stale failure.
        const next = context.app.handleAction({ type: 'search' });
        const nextPosted = await context.currentPort().nextPosted();
        expect(nextPosted).toMatchObject({ type: 'start_scan' });
        await context.currentPort().emit(
            scanStartedEvent({
                request_id: nextPosted.request_id as string,
                scan_handle: HANDLE_SCAN_2,
            }),
        );
        await next;
        await flush();
        expect(context.view.last.controls.stopScan.enabled).toBe(true);
    });
});
