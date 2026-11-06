import { describe, expect, it, vi } from 'vitest';
import { setupBackgroundServices } from '../src/background/services';

function rig() {
    const data: Record<string, unknown> = {};
    let listener!: (
        value: unknown,
        sender: chrome.runtime.MessageSender,
        respond: (result: unknown) => void,
    ) => boolean;
    let updated!: (id: number, change: chrome.tabs.OnUpdatedInfo) => void;
    let removed!: (id: number) => void;
    let replaced: ((added: number, removed: number) => void) | undefined;
    const writes: unknown[] = [];
    const badges: string[] = [];
    const api = {
        runtime: {
            id: 'extension',
            getURL: (path: string) => `chrome-extension://extension/${path}`,
            onMessage: {
                addListener(fn: typeof listener) {
                    listener = fn;
                },
            },
            sendMessage: async (message: unknown) => {
                writes.push(message);
                const operationId = (message as { operationId: string }).operationId;
                const response = await new Promise<{ ok: boolean }>((resolve) => {
                    listener(
                        { type: 'veles.clipboard.commit', operationId },
                        {
                            id: 'extension',
                            url: 'chrome-extension://extension/offscreen.html',
                        },
                        (result) => resolve(result as { ok: boolean }),
                    );
                });
                return { ok: response.ok, operationId };
            },
        },
        storage: {
            session: {
                get: async (key: string) => ({ [key]: data[key] }),
                set: async (value: Record<string, unknown>) => {
                    Object.assign(data, value);
                },
            },
        },
        action: {
            setBadgeText: async ({ text }: { text: string }) => {
                badges.push(text);
            },
        },
        tabs: {
            get: async () => ({}),
            onUpdated: {
                addListener(fn: typeof updated) {
                    updated = fn;
                },
            },
            onRemoved: {
                addListener(fn: typeof removed) {
                    removed = fn;
                },
            },
            onReplaced: {
                addListener(fn: (added: number, removed: number) => void) {
                    replaced = fn;
                },
            },
        },
        offscreen: {
            Reason: { CLIPBOARD: 'CLIPBOARD' },
            hasDocument: async () => false,
            createDocument: async () => undefined,
            closeDocument: async () => undefined,
        },
    };
    setupBackgroundServices(api as unknown as typeof chrome);
    const sender = {
        id: 'extension',
        url: 'chrome-extension://extension/connector.html',
        documentId: 'doc-a',
        tab: { id: 7 },
    } as chrome.runtime.MessageSender;
    const send = (message: unknown, from = sender): Promise<Record<string, unknown>> =>
        new Promise((resolve) => {
            const handled = listener(message, from, (value) =>
                resolve(value as Record<string, unknown>),
            );
            if (!handled) resolve({});
        });
    return {
        send,
        sender,
        writes,
        badges,
        updated: (change: chrome.tabs.OnUpdatedInfo) => updated(7, change),
        removed: () => removed(7),
        replaced: () => replaced?.(8, 7),
        data,
    };
}

describe('worker service wiring', () => {
    it('revokes the removed tab generation when Chrome replaces a discarded tab', async () => {
        const r = rig();
        const { token } = await r.send({ type: 'veles.lifecycle.activate' });
        r.replaced();
        expect(await r.send({ type: 'veles.lifecycle.check', token })).toEqual({ ok: false });
        await vi.waitFor(() => expect(r.data['veles.lifecycle.7']).toBeNull());
    });
    it.each(['frozen', 'discarded', 'removed'])(
        'revokes generations on %s and rejects old copies/status',
        async (transition) => {
            const r = rig();
            const { token } = await r.send({ type: 'veles.lifecycle.activate' });
            expect(typeof token).toBe('string');
            expect(await r.send({ type: 'veles.clipboard.copy', token, code: '012345' })).toEqual({
                ok: true,
            });
            expect(r.writes).toEqual([
                expect.objectContaining({
                    type: 'veles.clipboard.write',
                    target: 'offscreen',
                    code: '012345',
                }),
            ]);
            if (transition === 'removed') r.removed();
            else r.updated({ [transition]: true });
            expect(await r.send({ type: 'veles.lifecycle.check', token })).toEqual({ ok: false });
            expect(await r.send({ type: 'veles.clipboard.copy', token, code: '654321' })).toEqual({
                ok: false,
            });
            expect(r.writes.length).toBe(1);
            expect(r.badges.at(-1)).toBe('');
        },
    );
    it('rejects wrong-page activation and unexpected payload fields', async () => {
        const r = rig();
        expect(
            await r.send(
                { type: 'veles.lifecycle.activate' },
                { ...r.sender, url: 'https://example.com' },
            ),
        ).toEqual({ token: null });
        expect(await r.send({ type: 'veles.lifecycle.activate', extra: true })).toEqual({
            ok: false,
        });
        expect(r.writes).toEqual([]);
    });
});
