import { describe, it, expect, vi } from 'vitest';
import { gateNativePort } from '../../src/connector/lifecycle';
import type { NativePortLike } from '../../src/connector/port';
import { NativeConnectorPort } from '../../src/connector/port';
import { MockNativePort } from './mock-port';
import { HANDLE_CONNECTION, readyEvent } from './fixtures';

function rig() {
    let receive!: (message: unknown) => void;
    let disconnected!: () => void;
    let closes = 0;
    const posted: unknown[] = [];
    const raw: NativePortLike = {
        postMessage(value) {
            posted.push(value);
        },
        disconnect() {
            closes++;
        },
        onMessage: {
            addListener(listener) {
                receive = listener;
            },
        },
        onDisconnect: {
            addListener(listener) {
                disconnected = listener;
            },
        },
    };
    const emitted: unknown[] = [];
    const pending: ((valid: boolean) => void)[] = [];
    const gated = gateNativePort(
        raw,
        () =>
            new Promise((resolve) => {
                pending.push(resolve);
            }),
    );
    gated.onMessage.addListener((value) => emitted.push(value));
    let losses = 0;
    gated.onDisconnect.addListener(() => {
        losses++;
    });
    return {
        gated,
        posted,
        receive: (v: unknown) => receive(v),
        lost: () => disconnected(),
        emitted,
        pending,
        closes: () => closes,
        losses: () => losses,
    };
}
const tick = async () => {
    for (let n = 0; n < 8; n++) await Promise.resolve();
};

describe('native callback generation boundary', () => {
    async function integrated() {
        const raw = new MockNativePort();
        let waiting = false;
        let release: (allowed: boolean) => void = () => undefined;
        const gate = gateNativePort(
            raw,
            () =>
                waiting
                    ? new Promise((resolve) => {
                          release = resolve;
                      })
                    : Promise.resolve(true),
            () => raw.lastError,
        );
        const port = new NativeConnectorPort({
            connectNative: () => gate,
            validationContext: () => ({
                phase: 'pre_ready',
                apiVersion: null,
                pending: new Map(),
                scans: new Map(),
                connections: new Map([[HANDLE_CONNECTION, { status: 'connected', generation: 1 }]]),
            }),
        });
        port.open();
        const hello = port.hello();
        const request = await raw.nextPosted();
        await raw.emit(readyEvent({ request_id: request.request_id as string }));
        expect((await hello).ok).toBe(true);
        return {
            port,
            raw,
            hold: () => {
                waiting = true;
            },
            release: () => {
                waiting = false;
                release(true);
            },
        };
    }
    it('does not transmit a real pending request after its timeout', async () => {
        vi.useFakeTimers();
        try {
            const r = await integrated();
            r.hold();
            const sending = r.port.request('send', {
                connection_handle: HANDLE_CONNECTION,
                record_base64: 'AAAA',
            });
            await tick();
            vi.advanceTimersByTime(30000);
            expect(await sending).toMatchObject({ ok: false, error: { code: 'timeout' } });
            r.release();
            await tick();
            expect(
                r.raw.posted.filter((value) => (value as { type: string }).type === 'send'),
            ).toEqual([]);
            await r.port.close('user');
        } finally {
            vi.useRealTimers();
        }
    });
    it('preserves successful shutdown when the actual request port receives terminal then EOF', async () => {
        const r = await integrated();
        const shutdown = r.port.request('shutdown', {});
        const posted = await r.raw.nextPosted();
        r.hold();
        await r.raw.emit({
            envelope_version: 1,
            api_version: 1,
            type: 'shutdown_complete',
            request_id: posted.request_id,
        });
        await tick();
        await r.raw.disconnect();
        r.release();
        await tick();
        expect(await shutdown).toMatchObject({ ok: true, event: { type: 'shutdown_complete' } });
    });
    it('drains a terminal message received before native EOF', async () => {
        const r = rig();
        r.receive({ type: 'shutdown_complete' });
        await tick();
        r.lost();
        expect(r.losses()).toBe(0);
        r.pending.shift()?.(true);
        await tick();
        expect(r.emitted).toEqual([{ type: 'shutdown_complete' }]);
        expect(r.losses()).toBe(1);
    });
    it('cancels envelopes that have not yet reached the native port', async () => {
        const r = rig();
        r.gated.postMessage({ type: 'send', request_id: 'expired' });
        await tick();
        r.gated.cancelRequest?.('expired');
        r.pending.shift()?.(true);
        await tick();
        expect(r.posted).toEqual([]);
    });
    it('never posts queued outgoing work after worker revocation', async () => {
        const r = rig();
        r.gated.postMessage({ type: 'send', record_base64: 'AAAA' });
        await tick();
        expect(r.posted).toEqual([]);
        r.pending.shift()?.(false);
        await tick();
        expect(r.posted).toEqual([]);
        expect(r.closes()).toBe(1);
    });
    it('orders authorized callbacks and drops all work on synchronous invalidation', async () => {
        const r = rig();
        r.receive({ type: 'first' });
        r.receive({ type: 'second' });
        await tick();
        expect(r.emitted).toEqual([]);
        r.pending.shift()?.(true);
        await tick();
        expect(r.emitted).toEqual([{ type: 'first' }]);
        r.gated.disconnect();
        r.pending.shift()?.(true);
        await tick();
        r.receive({ type: 'late' });
        expect(r.emitted).toEqual([{ type: 'first' }]);
        expect(r.closes()).toBe(1);
    });
    it('rejects a stale worker generation before dispatch and closes once', async () => {
        const r = rig();
        r.receive({ type: 'old' });
        await tick();
        r.pending.shift()?.(false);
        await tick();
        expect(r.emitted).toEqual([]);
        expect(r.closes()).toBe(1);
        expect(r.losses()).toBe(1);
        r.lost();
        expect(r.losses()).toBe(1);
    });
    it('fails closed at 129 queued callbacks without retaining their payloads', async () => {
        const r = rig();
        for (let i = 0; i < 129; i++) r.receive({ type: 'message', data: i });
        expect(r.closes()).toBe(1);
        expect(r.losses()).toBe(1);
        expect(r.emitted).toEqual([]);
    });
    it('fails closed above the encoded byte cap', () => {
        const r = rig();
        r.receive('x'.repeat(262145));
        expect(r.closes()).toBe(1);
        expect(r.emitted).toEqual([]);
    });
});
