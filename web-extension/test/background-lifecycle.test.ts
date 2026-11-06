import { describe, expect, it } from 'vitest';
import { LifecycleCoordinator, type LifecycleApi } from '../src/background/lifecycle';

function rig() {
    const data: Record<string, unknown> = {};
    const badges: string[] = [];
    const tab = { frozen: false, discarded: false };
    let count = 0;
    const api: LifecycleApi = {
        id: 'extension',
        getURL: (path) => `chrome-extension://extension/${path}`,
        storage: {
            get: async (key) => ({ [key]: data[key] }),
            set: async (items) => {
                Object.assign(data, items);
            },
        },
        getTab: async () => tab,
        badge: async (_tabId, text) => {
            badges.push(text);
        },
        token: () => `generation-${++count}`,
    };
    const sender = {
        id: 'extension',
        url: 'chrome-extension://extension/connector.html',
        documentId: 'doc-a',
        tab: { id: 7 },
    };
    return { api, sender, tab, badges, coordinator: new LifecycleCoordinator(api) };
}

describe('worker lifecycle authority', () => {
    it('allows replacement activation queued behind the preceding document end', async () => {
        const r = rig();
        const old = await r.coordinator.activate(r.sender);
        const ended = r.coordinator.end(old, r.sender);
        const replacement = { ...r.sender, documentId: 'next-document' };
        const activating = r.coordinator.activate(replacement);
        await ended;
        const fresh = await activating;
        expect(typeof fresh).toBe('string');
        expect(await r.coordinator.valid(fresh, replacement)).toBe(true);
    });
    it('clears diagnostics immediately even with an older status query stalled', async () => {
        const r = rig();
        const token = await r.coordinator.activate(r.sender);
        await r.coordinator.status(token, r.sender, true);
        let release!: (tab: { frozen: boolean }) => void;
        r.api.getTab = () =>
            new Promise((resolve) => {
                release = resolve;
            });
        const oldStatus = r.coordinator.status(token, r.sender, true);
        for (let n = 0; n < 8; n++) await Promise.resolve();
        const invalidated = r.coordinator.invalidate(7);
        expect(r.badges.at(-1)).toBe('');
        release({ frozen: false });
        expect(await oldStatus).toBe(false);
        await invalidated;
        expect(r.badges.at(-1)).toBe('');
    });
    it('fences old validation and end when another document activates', async () => {
        const r = rig();
        const old = await r.coordinator.activate(r.sender);
        let release!: (tab: { frozen: boolean }) => void;
        const getTab = r.api.getTab;
        r.api.getTab = () =>
            new Promise((resolve) => {
                release = resolve;
            });
        const stale = r.coordinator.valid(old, r.sender);
        await Promise.resolve();
        await Promise.resolve();
        r.api.getTab = getTab;
        const sender = { ...r.sender, documentId: 'replacement' };
        const fresh = await r.coordinator.activate(sender);
        release({ frozen: false });
        expect(await stale).toBe(false);
        expect(await r.coordinator.end(old, r.sender)).toBe(false);
        expect(await r.coordinator.valid(fresh, sender)).toBe(true);
    });
    it('binds generation to the exact connector document and extension', async () => {
        const r = rig();
        const token = await r.coordinator.activate(r.sender);
        expect(typeof token).toBe('string');
        expect(await r.coordinator.valid(token, r.sender)).toBe(true);
        for (const sender of [
            { ...r.sender, id: 'other' },
            { ...r.sender, url: 'https://example.com/' },
            { ...r.sender, url: 'chrome-extension://extension/options.html' },
            { ...r.sender, documentId: 'doc-b' },
            { ...r.sender, tab: { id: 8 } },
        ])
            expect(await r.coordinator.valid(token, sender)).toBe(false);
        expect(await r.coordinator.activate({ ...r.sender, url: 'https://example.com/' })).toBe(
            null,
        );
    });

    it('retains revocation across worker restart and rejects frozen/discarded tabs', async () => {
        const r = rig();
        const token = await r.coordinator.activate(r.sender);
        await r.coordinator.invalidate(7);
        const restarted = new LifecycleCoordinator(r.api);
        expect(await restarted.valid(token, r.sender)).toBe(false);
        r.tab.frozen = true;
        expect(await restarted.activate(r.sender)).toBe(null);
        r.tab.frozen = false;
        r.tab.discarded = true;
        expect(await restarted.activate(r.sender)).toBe(null);
        r.tab.discarded = false;
        const fresh = await restarted.activate(r.sender);
        expect(fresh).not.toBe(token);
        expect(await restarted.valid(fresh, r.sender)).toBe(true);
        expect(r.badges.at(-1)).toBe('');
    });

    it('revokes an authorization already waiting on a browser query', async () => {
        const r = rig();
        const token = await r.coordinator.activate(r.sender);
        let release!: (tab: { frozen: boolean }) => void;
        r.api.getTab = () =>
            new Promise((resolve) => {
                release = resolve;
            });
        const pending = r.coordinator.valid(token, r.sender);
        await Promise.resolve();
        await Promise.resolve();
        const invalidation = r.coordinator.invalidate(7);
        release({ frozen: false });
        expect(await pending).toBe(false);
        await invalidation;
    });

    it('cannot restore a diagnostic badge with a revoked token', async () => {
        const r = rig();
        const token = await r.coordinator.activate(r.sender);
        expect(await r.coordinator.status(token, r.sender, true)).toBe(true);
        expect(r.badges.at(-1)).toBe('ON');
        await r.coordinator.invalidate(7);
        expect(await r.coordinator.status(token, r.sender, true)).toBe(false);
        expect(r.badges.at(-1)).toBe('');
    });
});
