import { describe, it, expect } from 'vitest';
import { setupOffscreen } from '../src/offscreen';

describe('offscreen worker provenance and execution admission', () => {
    function rig(admitted: boolean) {
        let listener!: (
            message: unknown,
            sender: chrome.runtime.MessageSender,
            respond: (result: unknown) => void,
        ) => boolean;
        let copies = 0;
        const input = {
            value: '',
            select() {
                return;
            },
            remove() {
                return;
            },
        };
        const runtime = {
            id: 'extension',
            getURL: (path: string) => `chrome-extension://extension/${path}`,
            onMessage: {
                addListener(fn: typeof listener) {
                    listener = fn;
                },
            },
            sendMessage: async () => ({ ok: admitted }),
        };
        setupOffscreen(
            runtime as unknown as typeof chrome.runtime,
            {
                createElement: () => input,
                body: {
                    appendChild() {
                        return;
                    },
                },
                execCommand() {
                    copies++;
                    return true;
                },
            } as unknown as Document,
        );
        const message = {
            type: 'veles.clipboard.write',
            target: 'offscreen',
            code: '012345',
            operationId: '12345678-1234-1234-1234-123456789abc',
        };
        const sender = { id: 'extension', url: 'chrome-extension://extension/background.js' };
        const send = (from = sender, value: unknown = message): Promise<unknown> =>
            new Promise((resolve) => {
                if (!listener(value, from, resolve)) resolve(undefined);
            });
        return { send, message, sender, copies: () => copies, input };
    }
    it('does not copy when the worker revokes execution admission', async () => {
        const r = rig(false);
        expect(await r.send()).toEqual({ ok: false, operationId: r.message.operationId });
        expect(r.copies()).toBe(0);
    });
    it('copies once after admission and erases its temporary text', async () => {
        const r = rig(true);
        expect(await r.send()).toEqual({ ok: true, operationId: r.message.operationId });
        expect(r.copies()).toBe(1);
        expect(r.input.value).toBe('');
    });
    it('rejects wrong source, tab senders, and malformed requests', async () => {
        const r = rig(true);
        expect(
            await r.send({ ...r.sender, url: 'chrome-extension://extension/connector.html' }),
        ).toBe(undefined);
        expect(await r.send({ ...r.sender, id: 'other' })).toBe(undefined);
        expect(await r.send({ ...r.sender, tab: { id: 1 } } as typeof r.sender)).toBe(undefined);
        expect(await r.send(r.sender, { ...r.message, extra: true })).toBe(undefined);
        expect(r.copies()).toBe(0);
    });
});
