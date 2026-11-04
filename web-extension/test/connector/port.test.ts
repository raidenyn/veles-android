import { describe, expect, it, vi } from 'vitest';
import {
    HOST_NAME,
    VELES_SERVICE_UUID,
    type ValidationContext,
} from '../../src/connector/protocol';
import {
    NativeConnectorPort,
    type ConnectorClose,
    type ConnectorResult,
} from '../../src/connector/port';
import type { WireEvent } from '../../src/connector/protocol';
import { MockNativePort } from './mock-port';
import {
    HANDLE_CONNECTION,
    HANDLE_CONNECTION_2,
    HANDLE_SCAN,
    connectedEvent,
    disconnectedEvent,
    messageEvent,
    readyEvent,
    scanStartedEvent,
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

function setup(
    options: {
        connections?: Map<string, { status: string; generation: number }>;
        requestIdStart?: number;
    } = {},
) {
    const mock = new MockNativePort();
    const clock = new FakeClock();
    const connectNative = vi.fn(() => mock);
    let scans = new Map<string, { status: 'starting' | 'started' | 'stopping' | 'stopped' }>();
    let connections =
        options.connections ??
        new Map([[HANDLE_CONNECTION, { status: 'connected', generation: 1 }]]);
    const port = new NativeConnectorPort({
        connectNative,
        lastError: () => mock.lastError,
        validationContext: (): ValidationContext => ({
            phase: 'pre_ready',
            apiVersion: null,
            pending: new Map(),
            scans,
            connections,
        }),
        requestIdStart: options.requestIdStart,
        now: () => clock.now,
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
        requestTimeoutMs: 30_000,
    });
    return {
        port,
        mock,
        clock,
        connectNative,
        setScans(value: typeof scans) {
            scans = value;
        },
        setConnections(value: typeof connections) {
            connections = value;
        },
    };
}

async function openReady(context = setup()) {
    const { port, mock } = context;
    port.open();
    const hello = port.hello();
    const posted = await mock.nextPosted();
    await mock.emit(readyEvent({ request_id: posted.request_id as string }));
    const result = await hello;
    expect(result).toMatchObject({ ok: true });
    if (!result.ok) throw new Error('hello did not become ready');
    return { ...context, helloPosted: posted };
}

function expectFailure(result: ConnectorResult<WireEvent>) {
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error('expected a local failure');
    if (result.source === 'wire') throw new Error('expected a local failure, got a wire error');
    return result.error;
}

// Typed consumer regression: no casts are needed to distinguish success, a
// validated wire error, and a connector-local failure.
function classifyHello(result: Awaited<ReturnType<NativeConnectorPort['hello']>>): string {
    if (result.ok) return `ready:${result.event.api_version}`;
    if (result.source === 'wire') return `wire:${result.error.code}`;
    return `local:${result.error.stage}`;
}

describe('NativeConnectorPort — handshake and request envelopes', () => {
    it('posts exactly hello pre-ready and resolves only on a correlated ready', async () => {
        const context = setup();
        const { port, mock, connectNative } = context;
        port.open();
        expect(connectNative).toHaveBeenCalledWith(HOST_NAME);

        const hello = port.hello();
        const posted = await mock.nextPosted();
        expect(posted).toEqual({
            envelope_version: 1,
            type: 'hello',
            request_id: 'c1',
            api_versions: [1],
        });
        expect(Object.hasOwn(posted, 'api_version')).toBe(false);

        await mock.emit(readyEvent({ request_id: posted.request_id as string }));
        const result = await hello;
        expect(result).toMatchObject({
            ok: true,
            event: {
                type: 'ready',
                api_version: 1,
                request_id: posted.request_id,
                bridge_version: 'veles-bridge-1.0.0',
                platform: 'windows',
                capabilities: ['veles_record_v1'],
            },
        });
        expect(classifyHello(result)).toBe('ready:1');
        expect(port.isOpen()).toBe(true);
        expect(port.isIntentional()).toBe(false);
    });

    it('adds the negotiated api_version to post-ready requests and correlates terminals', async () => {
        const context = await openReady();
        const { port, mock } = context;
        const request = port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        const posted = await mock.nextPosted();
        expect(posted).toMatchObject({
            envelope_version: 1,
            api_version: 1,
            type: 'send',
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        expect(posted.request_id).toMatch(/^c[0-9a-z]+$/);

        await mock.emit(sentEvent({ request_id: posted.request_id as string }));
        const result = await request;
        expect(result).toMatchObject({ ok: true, event: { type: 'sent' } });
    });

    it('never posts locally synthesized or caller-invalid requests', async () => {
        const { port, mock } = setup();
        const notOpen = await port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        expect(expectFailure(notOpen)).toMatchObject({
            code: 'internal',
            stage: 'port_closed',
            recoverability: 'fatal',
            synthesized: true,
        });

        port.open();
        const preReady = await port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        expect(expectFailure(preReady).code).toBe('protocol_violation');
        expect(mock.posted).toHaveLength(0);

        const reserved = await port.request('send', {
            envelope_version: 9,
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        expect(expectFailure(reserved).code).toBe('protocol_violation');
        expect(mock.posted).toHaveLength(0);

        const invalidBase64 = await port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AR==',
        });
        expect(expectFailure(invalidBase64).code).toBe('protocol_violation');
        expect(mock.posted).toHaveLength(0);

        await port.close('user');
        const afterClose = await port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        expect(expectFailure(afterClose).code).toBe('internal');
        expect(expectFailure(afterClose).stage).toBe('port_closed');
        expect(mock.posted).toHaveLength(0);
    });

    it('uses only the production host name', () => {
        const { port, connectNative } = setup();
        port.open();
        expect(connectNative).toHaveBeenCalledTimes(1);
        expect(connectNative).toHaveBeenCalledWith('me.nagaev.veles.bridge');
        expect(HOST_NAME).toBe('me.nagaev.veles.bridge');
    });

    it('classifies a synchronous connectNative throw locally at port_create_failed', async () => {
        const port = new NativeConnectorPort({
            connectNative: () => {
                throw new Error('host launch failed');
            },
            validationContext: () => ({
                phase: 'pre_ready',
                apiVersion: null,
                pending: new Map(),
                scans: new Map(),
                connections: new Map(),
            }),
        });
        expect(() => port.open()).not.toThrow();
        const result = await port.hello();
        expect(expectFailure(result)).toMatchObject({
            code: 'internal',
            stage: 'port_create_failed',
            recoverability: 'fatal',
            synthesized: true,
        });
        expect(port.isOpen()).toBe(false);
    });

    it('fails closed after a correlated pre-ready incompatible_api host error', async () => {
        const { port, mock } = setup();
        port.open();
        const hello = port.hello();
        const posted = await mock.nextPosted();
        await mock.emit(
            wireErrorEvent({
                api_version: undefined,
                request_id: posted.request_id as string,
                code: 'incompatible_api',
                stage: 'negotiation',
            }),
        );
        const result = await hello;
        expect(result).toMatchObject({
            ok: false,
            source: 'wire',
            error: {
                type: 'error',
                code: 'incompatible_api',
                request_id: posted.request_id,
            },
        });
        expect(classifyHello(result)).toBe('wire:incompatible_api');
        expect(port.isOpen()).toBe(false);
        expect(port.isIntentional()).toBe(true);
        expect(mock.posted).toHaveLength(1);
    });

    it('rejects an unsupported ready api_version as incompatible_api before any BLE request', async () => {
        const { port, mock } = setup();
        port.open();
        const hello = port.hello();
        const posted = await mock.nextPosted();
        await mock.emit(readyEvent({ request_id: posted.request_id as string, api_version: 2 }));
        const failure = expectFailure(await hello);
        expect(failure).toMatchObject({
            code: 'incompatible_api',
            recoverability: 'fatal',
            stage: 'negotiation',
        });
        expect(port.isOpen()).toBe(false);
        expect(mock.posted).toHaveLength(1);
    });
});

describe('NativeConnectorPort — disconnect, missing host, and idempotent close', () => {
    it('classifies async missing host from runtime.lastError before ready', async () => {
        const { port, mock } = setup();
        port.open();
        const hello = port.hello();
        await mock.nextPosted();
        await mock.disconnect('Specified native messaging host not found.');
        const failure = expectFailure(await hello);
        expect(failure).toMatchObject({
            code: 'missing_host',
            recoverability: 'fatal',
            stage: 'missing_host',
        });
        expect(port.isOpen()).toBe(false);
        expect(mock.lastError).toBeUndefined();
    });

    it('notifies once when a pre-hello missing-host disconnect arrives', async () => {
        const { port, mock } = setup();
        const closes: ConnectorClose[] = [];
        port.onClose((close) => closes.push(close));
        port.open();
        await mock.disconnect('Specified native messaging host not found.');
        expect(closes).toHaveLength(1);
        expect(closes[0]).toMatchObject({ reason: 'missing_host', intentional: false });
        expect(mock.lastError).toBeUndefined();
        await mock.disconnect('Specified native messaging host not found.');
        expect(closes).toHaveLength(1);
    });

    it('notifies once on idle post-ready loss even when no request is pending', async () => {
        const context = await openReady();
        context.setScans(new Map([[HANDLE_SCAN, { status: 'started' }]]));
        const closes: ConnectorClose[] = [];
        context.port.onClose((close) => closes.push(close));
        await context.mock.disconnect();
        expect(closes).toHaveLength(1);
        expect(closes[0]).toMatchObject({ reason: 'port_lost', intentional: false });
        await context.mock.disconnect();
        expect(closes).toHaveLength(1);
    });

    it('notifies once on intentional close with the intent reason', async () => {
        const context = await openReady();
        const closes: ConnectorClose[] = [];
        context.port.onClose((close) => closes.push(close));
        await context.port.close('user');
        await context.port.close('user');
        expect(closes).toEqual([expect.objectContaining({ reason: 'user', intentional: true })]);
    });

    it('does not classify missing host after ready', async () => {
        const context = await openReady();
        const send = context.port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        await context.mock.nextPosted();
        await context.mock.disconnect('Specified native messaging host not found.');
        const failure = expectFailure(await send);
        expect(failure.code).toBe('internal');
        expect(failure.stage).toBe('port_closed');
    });

    it('settles a handle-less unknown_handle send when no connection is live', async () => {
        const context = await openReady();
        context.setConnections(new Map());
        const closes: ConnectorClose[] = [];
        const failures: string[] = [];
        context.port.onClose((close) => closes.push(close));
        context.port.onFailure((failure) => failures.push(failure.code));

        const send = context.port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        const sendPosted = await context.mock.nextPosted();
        const connect = context.port.request('connect', { platform_hint: 'Veles Watch' });
        const connectPosted = await context.mock.nextPosted();

        await context.mock.emit(
            wireErrorEvent({
                request_id: sendPosted.request_id as string,
                code: 'unknown_handle',
                stage: 'connection_handle',
                connection_handle: undefined,
            }),
        );
        const result = await send;
        expect(result).toMatchObject({
            ok: false,
            source: 'wire',
            error: { code: 'unknown_handle' },
        });
        expect(failures).toEqual([]);
        expect(closes).toEqual([]);
        expect(context.port.isOpen()).toBe(true);

        await context.mock.emit(connectedEvent({ request_id: connectPosted.request_id as string }));
        expect((await connect).ok).toBe(true);
        expect(context.mock.posted).toHaveLength(3);
        expect(closes).toEqual([]);
    });

    it('settles a handle-less unknown_handle disconnect when no connection is live', async () => {
        const context = await openReady();
        context.setConnections(new Map());
        const closes: ConnectorClose[] = [];
        const failures: string[] = [];
        context.port.onClose((close) => closes.push(close));
        context.port.onFailure((failure) => failures.push(failure.code));

        const disconnect = context.port.request('disconnect', {
            connection_handle: HANDLE_CONNECTION,
        });
        const posted = await context.mock.nextPosted();
        await context.mock.emit(
            wireErrorEvent({
                request_id: posted.request_id as string,
                code: 'unknown_handle',
                stage: 'connection_handle',
                connection_handle: undefined,
            }),
        );
        const result = await disconnect;
        expect(result).toMatchObject({
            ok: false,
            source: 'wire',
            error: { code: 'unknown_handle' },
        });
        expect(failures).toEqual([]);
        expect(closes).toEqual([]);
        expect(context.port.isOpen()).toBe(true);
        expect(context.mock.posted).toHaveLength(2);
    });

    it('settles a handle-less unknown_handle send after a captured live handle disappears', async () => {
        const context = await openReady();
        const closes: ConnectorClose[] = [];
        const failures: string[] = [];
        context.port.onClose((close) => closes.push(close));
        context.port.onFailure((failure) => failures.push(failure.code));

        const survivor = context.port.request('connect', { platform_hint: 'Veles Watch' });
        const survivorPosted = await context.mock.nextPosted();
        const send = context.port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        const sendPosted = await context.mock.nextPosted();

        // Consume the unsolicited release while the connection record is
        // still live, then remove it as the state consumer would.
        await context.mock.emit(
            disconnectedEvent({
                request_id: undefined,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        context.setConnections(new Map());

        await context.mock.emit(
            wireErrorEvent({
                request_id: sendPosted.request_id as string,
                code: 'unknown_handle',
                stage: 'connection_handle',
                connection_handle: undefined,
            }),
        );
        const result = await send;
        expect(result).toMatchObject({
            ok: false,
            source: 'wire',
            error: { code: 'unknown_handle' },
        });
        expect(failures).toEqual([]);
        expect(closes).toEqual([]);
        expect(context.port.isOpen()).toBe(true);

        await context.mock.emit(
            connectedEvent({ request_id: survivorPosted.request_id as string }),
        );
        expect((await survivor).ok).toBe(true);
        expect(context.mock.posted).toHaveLength(3);
        expect(closes).toEqual([]);
    });

    it('settles a handle-less unknown_handle disconnect after a captured live handle disappears', async () => {
        const context = await openReady();
        const closes: ConnectorClose[] = [];
        const failures: string[] = [];
        context.port.onClose((close) => closes.push(close));
        context.port.onFailure((failure) => failures.push(failure.code));

        const survivor = context.port.request('connect', { platform_hint: 'Veles Watch' });
        const survivorPosted = await context.mock.nextPosted();
        const disconnect = context.port.request('disconnect', {
            connection_handle: HANDLE_CONNECTION,
        });
        const posted = await context.mock.nextPosted();

        await context.mock.emit(
            disconnectedEvent({
                request_id: undefined,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        context.setConnections(new Map());

        await context.mock.emit(
            wireErrorEvent({
                request_id: posted.request_id as string,
                code: 'unknown_handle',
                stage: 'connection_handle',
                connection_handle: undefined,
            }),
        );
        const result = await disconnect;
        expect(result).toMatchObject({
            ok: false,
            source: 'wire',
            error: { code: 'unknown_handle' },
        });
        expect(failures).toEqual([]);
        expect(closes).toEqual([]);
        expect(context.port.isOpen()).toBe(true);

        await context.mock.emit(
            connectedEvent({ request_id: survivorPosted.request_id as string }),
        );
        expect((await survivor).ok).toBe(true);
        expect(context.mock.posted).toHaveLength(3);
        expect(closes).toEqual([]);
    });

    it('synthesizes exactly one port_closed per pending request and ignores repeat disconnects', async () => {
        const context = await openReady();
        const failures: string[] = [];
        context.port.onFailure((failure) => failures.push(failure.code));
        const first = context.port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        await context.mock.nextPosted();
        const second = context.port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'Ag==',
        });
        await context.mock.nextPosted();

        await context.mock.disconnect();
        const results = await Promise.all([first, second]);
        expect(results.map((result) => expectFailure(result).code)).toEqual([
            'internal',
            'internal',
        ]);
        expect(failures.filter((code) => code === 'internal')).toHaveLength(2);

        await context.mock.disconnect();
        expect(failures.filter((code) => code === 'internal')).toHaveLength(2);
    });

    it('close terminalizes each pending request exactly once and stays idempotent', async () => {
        const context = await openReady();
        const failures: string[] = [];
        context.port.onFailure((failure) => failures.push(failure.code));
        const send = context.port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        await context.mock.nextPosted();

        await context.port.close('user');
        const failure = expectFailure(await send);
        expect(failure).toMatchObject({ code: 'internal', stage: 'port_closed' });
        await context.port.close('user');
        expect(failures.filter((code) => code === 'internal')).toHaveLength(1);
        expect(context.mock.posted).toHaveLength(2);
    });

    it('close is idempotent, intentional, and does not synthesize for terminal work', async () => {
        const context = await openReady();
        const failures: string[] = [];
        context.port.onFailure((failure) => failures.push(failure.code));
        const send = context.port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        const posted = await context.mock.nextPosted();
        await context.mock.emit(sentEvent({ request_id: posted.request_id as string }));
        expect((await send).ok).toBe(true);

        await context.port.close('user');
        await context.port.close('user');
        expect(context.port.isOpen()).toBe(false);
        expect(context.port.isIntentional()).toBe(true);
        expect(failures).toHaveLength(0);
        expect(context.mock.posted).toHaveLength(2);
    });
});

describe('NativeConnectorPort — timeout ambiguity and fail-closed correlation', () => {
    it('times out a non-scan request exactly once, never replays, and rejects a late terminal', async () => {
        const context = await openReady();
        const failures: { code: string; stage: string }[] = [];
        context.port.onFailure((failure) =>
            failures.push({ code: failure.code, stage: failure.stage }),
        );
        const send = context.port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        const posted = await context.mock.nextPosted();
        context.clock.advance(30_000);
        const failure = expectFailure(await send);
        expect(failure).toMatchObject({ code: 'timeout', recoverability: 'retryable' });
        expect(failures.filter((entry) => entry.code === 'timeout')).toHaveLength(1);
        expect(context.mock.posted).toHaveLength(2);

        await context.mock.emit(sentEvent({ request_id: posted.request_id as string }));
        expect(failures.filter((entry) => entry.code === 'protocol_violation')).toHaveLength(1);
        expect(context.mock.posted).toHaveLength(2);
        expect(context.port.isOpen()).toBe(false);
    });

    it('exempts both scan request kinds from the 30 s request timer', async () => {
        const context = await openReady();
        context.setScans(new Map());
        const failures: string[] = [];
        context.port.onFailure((failure) => failures.push(failure.code));

        const start = context.port.request('start_scan', {
            service_uuid: VELES_SERVICE_UUID,
        });
        const startedPosted = await context.mock.nextPosted();
        context.clock.advance(30_000);
        expect(failures).toEqual([]);
        expect(context.port.isOpen()).toBe(true);
        await context.mock.emit(
            scanStartedEvent({ request_id: startedPosted.request_id as string }),
        );
        expect(await start).toMatchObject({
            ok: true,
            event: { type: 'scan_state', status: 'started' },
        });

        const stop = context.port.request('stop_scan', {
            scan_handle: HANDLE_SCAN,
        });
        await context.mock.nextPosted();
        context.clock.advance(30_000);
        expect(failures).toEqual([]);
        expect(context.port.isOpen()).toBe(true);
        await context.port.close('user');
        expect(expectFailure(await stop)).toMatchObject({
            code: 'internal',
            stage: 'port_closed',
        });
    });

    it('fails closed on a duplicate terminal after success with exactly one local violation', async () => {
        const context = await openReady();
        const send = context.port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        const posted = await context.mock.nextPosted();
        await context.mock.emit(sentEvent({ request_id: posted.request_id as string }));
        expect((await send).ok).toBe(true);

        const failures: { code: string; stage: string }[] = [];
        context.port.onFailure((failure) =>
            failures.push({ code: failure.code, stage: failure.stage }),
        );
        await context.mock.emit(sentEvent({ request_id: posted.request_id as string }));
        expect(failures).toHaveLength(1);
        expect(failures[0]).toMatchObject({
            code: 'protocol_violation',
            stage: 'duplicate_terminal',
        });
        expect(context.mock.posted).toHaveLength(2);
        expect(context.port.isOpen()).toBe(false);
    });

    it('ignores a queued old-generation disconnect and a captured stale timer after reopen', async () => {
        const first = new MockNativePort();
        const second = new MockNativePort();
        let current = first;
        let staleTimer: (() => void) | undefined;
        const port = new NativeConnectorPort({
            connectNative: () => current,
            lastError: () => current.lastError,
            validationContext: () => ({
                phase: 'pre_ready',
                apiVersion: null,
                pending: new Map(),
                scans: new Map(),
                connections: new Map([[HANDLE_CONNECTION, { status: 'connected', generation: 1 }]]),
            }),
            now: () => 0,
            setTimeout: (fn) => {
                staleTimer ??= fn;
                return 1;
            },
            clearTimeout: () => {
                // Keep the captured callback callable, as a stale timer would be.
            },
        });
        const failures: string[] = [];
        const closes: ConnectorClose[] = [];
        port.onFailure((failure) => failures.push(failure.code));
        port.onClose((close) => closes.push(close));

        port.open();
        const oldHello = port.hello();
        const firstHello = await first.nextPosted();
        const oldDisconnect = first.disconnect('Specified native messaging host not found.');
        const closePromise = port.close('user');
        current = second;
        port.open();
        const hello = port.hello();
        const posted = await second.nextPosted();
        // A new explicit generation may reuse the counter safely; old
        // generation callbacks are rejected by generation identity.
        expect(posted.request_id).toBe(firstHello.request_id);
        await oldDisconnect;
        await first.drain();
        staleTimer?.();
        await second.emit(readyEvent({ request_id: posted.request_id as string }));
        expect((await hello).ok).toBe(true);
        expect(expectFailure(await oldHello).code).toBe('internal');
        expect(failures).not.toContain('timeout');
        expect(port.isOpen()).toBe(true);
        expect(closes).toHaveLength(1);
        expect(closes[0]).toMatchObject({ reason: 'user', intentional: true });
        await closePromise;
    });

    it('issues monotonic unique ids and stays healthy past 4096 successful requests', async () => {
        const context = await openReady();
        const seen: string[] = [];
        const total = 4_105;
        for (let index = 0; index < total; index += 1) {
            const request = context.port.request('send', {
                connection_handle: HANDLE_CONNECTION,
                record_base64: 'AQ==',
            });
            const posted = await context.mock.nextPosted();
            seen.push(posted.request_id as string);
            await context.mock.emit(sentEvent({ request_id: posted.request_id as string }));
            expect((await request).ok).toBe(true);
        }

        // Monotonic issuance needs no unbounded "already issued" set.
        expect(new Set(seen).size).toBe(total);
        const numbers = seen.map((id) => Number.parseInt(id.slice(1), 36));
        expect(numbers).toEqual([...numbers].sort((left, right) => left - right));

        // The old 4096-request cutoff killed all live links and suppressed
        // recovery. A healthy session must simply keep working.
        expect(context.port.isOpen()).toBe(true);
        const after = context.port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        const posted = await context.mock.nextPosted();
        await context.mock.emit(sentEvent({ request_id: posted.request_id as string }));
        expect((await after).ok).toBe(true);
        expect(context.mock.posted).toHaveLength(1 + total + 1);
    });

    it('refuses at the safe-integer id ceiling without closing a healthy port', async () => {
        const context = setup({ requestIdStart: Number.MAX_SAFE_INTEGER - 1 });
        await openReady(context);
        const over = context.port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        expect(expectFailure(await over)).toMatchObject({
            code: 'internal',
            stage: 'request_id_overflow',
        });
        expect(context.mock.posted).toHaveLength(1);
        expect(context.port.isOpen()).toBe(true);
    });

    it('bounds outstanding requests at 64 without posting the 65th', async () => {
        const context = await openReady();
        const requests: Promise<ConnectorResult<WireEvent>>[] = [];
        for (let index = 0; index < 64; index += 1) {
            requests.push(
                context.port.request('send', {
                    connection_handle: HANDLE_CONNECTION,
                    record_base64: 'AQ==',
                }),
            );
        }
        const over = await context.port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        expect(expectFailure(over)).toMatchObject({
            code: 'cap_exceeded',
            recoverability: 'retryable',
        });
        expect(context.mock.posted).toHaveLength(1 + 64);

        await context.mock.disconnect();
        const results = await Promise.all(requests);
        expect(results.every((result) => !result.ok)).toBe(true);
        expect(context.mock.posted).toHaveLength(1 + 64);
    });

    it('delivers validated unsolicited messages through onEvent subscriptions', async () => {
        const context = await openReady();
        const seen: WireEvent[] = [];
        const unsubscribe = context.port.onEvent((event) => seen.push(event));
        await context.mock.emit(messageEvent({ connection_handle: HANDLE_CONNECTION }));
        expect(seen).toHaveLength(1);
        expect(seen[0]).toMatchObject({ type: 'message', connection_handle: HANDLE_CONNECTION });

        unsubscribe();
        await context.mock.emit(messageEvent({ connection_handle: HANDLE_CONNECTION }));
        expect(seen).toHaveLength(1);
    });

    it('rejects a cross-handle correlated sent terminal without resolving or creating a handle', async () => {
        const context = await openReady();
        context.setConnections(
            new Map([
                [HANDLE_CONNECTION, { status: 'connected', generation: 1 }],
                [HANDLE_CONNECTION_2, { status: 'connected', generation: 2 }],
            ]),
        );
        const send = context.port.request('send', {
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        });
        const posted = await context.mock.nextPosted();
        const failures: string[] = [];
        context.port.onFailure((failure) => failures.push(failure.code));
        await context.mock.emit(
            sentEvent({
                request_id: posted.request_id as string,
                connection_handle: HANDLE_CONNECTION_2,
            }),
        );
        expect(expectFailure(await send).code).toBe('protocol_violation');
        expect(failures).toEqual(['protocol_violation']);
        expect(context.port.isOpen()).toBe(false);
    });
});
