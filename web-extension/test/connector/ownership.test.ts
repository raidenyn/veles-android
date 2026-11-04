import { describe, expect, it, vi } from 'vitest';

import {
    CONNECTOR_CLAIM_RESULT_TYPE,
    CONNECTOR_CLAIM_TYPE,
    CONNECTOR_FOCUS_RESULT_TYPE,
    CONNECTOR_FOCUS_TYPE,
    CONNECTOR_OWNER_HERE_TYPE,
    CONNECTOR_OWNER_PROBE_TYPE,
    CONNECTOR_RELEASE_TYPE,
    ConnectorOwnership,
    ConnectorOwnershipCoordinator,
    type ConnectorOwnershipApi,
    type OwnershipLockManager,
    type OwnershipMessageSender,
    type OwnershipRuntimeApi,
} from '../../src/connector/ownership';

const EXTENSION_ID = 'extension-id';

interface ProbeReply {
    type: string;
    documentId: string;
}

function backgroundApi(options: { alive?: boolean } = {}) {
    const probes: unknown[] = [];
    const focusCalls: string[] = [];
    let responder: ((message: ProbeReply) => unknown) | undefined;
    let alive = options.alive ?? true;
    const api: OwnershipRuntimeApi = {
        runtime: {
            id: EXTENSION_ID,
            sendMessage: vi.fn(async (message: unknown) => {
                probes.push(message);
                if (
                    typeof message === 'object' &&
                    message !== null &&
                    (message as { type?: string }).type === CONNECTOR_OWNER_PROBE_TYPE &&
                    responder !== undefined
                ) {
                    return responder(message as ProbeReply);
                }
                return undefined;
            }),
            onMessage: { addListener: vi.fn() },
        },
        tabs: {
            get: vi.fn(async () => {
                if (!alive) throw new Error('No tab');
                return {};
            }),
            update: vi.fn(async (tabId: number) => {
                focusCalls.push(`tab:${tabId}`);
                return {};
            }),
        },
        windows: {
            update: vi.fn(async (windowId: number) => {
                focusCalls.push(`window:${windowId}`);
                return {};
            }),
        },
        probeTimeoutMs: 25,
    };
    return {
        api,
        probes,
        focusCalls,
        setAlive(value: boolean) {
            alive = value;
        },
        setProbeResponder(value: ((message: ProbeReply) => unknown) | undefined) {
            responder = value;
        },
    };
}

function sender(tabId: number, windowId: number): OwnershipMessageSender {
    return { id: EXTENSION_ID, tab: { id: tabId, windowId } };
}

function claim(documentId: string, tabId: number, holdsLock = false) {
    return { type: CONNECTOR_CLAIM_TYPE, documentId, holdsLock };
}

describe('service-worker ownership arbitration', () => {
    it('grants one owner and denies a competing live document', async () => {
        const context = backgroundApi();
        context.setProbeResponder(() => ({
            type: CONNECTOR_OWNER_HERE_TYPE,
            documentId: 'doc-a',
        }));
        const coordinator = new ConnectorOwnershipCoordinator(context.api);

        await expect(coordinator.handle(claim('doc-a', 10), sender(10, 2))).resolves.toEqual({
            type: CONNECTOR_CLAIM_RESULT_TYPE,
            granted: true,
            owner: { tabId: 10, windowId: 2 },
        });
        await expect(coordinator.handle(claim('doc-b', 11), sender(11, 3))).resolves.toEqual({
            type: CONNECTOR_CLAIM_RESULT_TYPE,
            granted: false,
            owner: { tabId: 10, windowId: 2 },
        });
        expect(coordinator.currentOwner()).toEqual({
            documentId: 'doc-a',
            tabId: 10,
            windowId: 2,
        });
    });

    it('serializes simultaneous claims without a lock so only one is granted', async () => {
        const context = backgroundApi();
        context.setProbeResponder(() => ({
            type: CONNECTOR_OWNER_HERE_TYPE,
            documentId: 'doc-a',
        }));
        const coordinator = new ConnectorOwnershipCoordinator(context.api);

        const [first, second] = await Promise.all([
            coordinator.handle(claim('doc-a', 10), sender(10, 2)),
            coordinator.handle(claim('doc-b', 11), sender(11, 3)),
        ]);
        expect(
            [first, second].filter((value) => (value as { granted: boolean }).granted),
        ).toHaveLength(1);
    });

    it('replaces a dead hidden owner instead of blocking forever', async () => {
        const context = backgroundApi();
        context.setProbeResponder(() => ({
            type: CONNECTOR_OWNER_HERE_TYPE,
            documentId: 'doc-a',
        }));
        const coordinator = new ConnectorOwnershipCoordinator(context.api);
        await coordinator.handle(claim('doc-a', 10), sender(10, 2));

        // The old document is gone; its tab may even be gone too.
        context.setAlive(false);
        context.setProbeResponder(undefined);
        await expect(coordinator.handle(claim('doc-b', 11), sender(11, 3))).resolves.toEqual({
            type: CONNECTOR_CLAIM_RESULT_TYPE,
            granted: true,
            owner: { tabId: 11, windowId: 3 },
        });
    });

    it('treats the Web Lock holder as authoritative over a stale record', async () => {
        const context = backgroundApi();
        context.setProbeResponder(() => ({
            type: CONNECTOR_OWNER_HERE_TYPE,
            documentId: 'doc-a',
        }));
        const coordinator = new ConnectorOwnershipCoordinator(context.api);
        await coordinator.handle(claim('doc-a', 10), sender(10, 2));

        await expect(coordinator.handle(claim('doc-b', 11, true), sender(11, 3))).resolves.toEqual({
            type: CONNECTOR_CLAIM_RESULT_TYPE,
            granted: true,
            owner: { tabId: 11, windowId: 3 },
        });
        expect(coordinator.currentOwner()?.documentId).toBe('doc-b');
    });

    it('releases only the matching document and focuses the owner window before its tab', async () => {
        const context = backgroundApi();
        context.setProbeResponder(() => ({
            type: CONNECTOR_OWNER_HERE_TYPE,
            documentId: 'doc-a',
        }));
        const coordinator = new ConnectorOwnershipCoordinator(context.api);
        await coordinator.handle(claim('doc-a', 10), sender(10, 2));

        await coordinator.handle(
            { type: CONNECTOR_RELEASE_TYPE, documentId: 'doc-b' },
            sender(11, 3),
        );
        expect(coordinator.currentOwner()?.documentId).toBe('doc-a');
        await coordinator.handle(
            { type: CONNECTOR_RELEASE_TYPE, documentId: 'doc-a' },
            sender(10, 2),
        );
        expect(coordinator.currentOwner()).toBeNull();

        await coordinator.handle(claim('doc-a', 10), sender(10, 2));
        await expect(
            coordinator.handle({ type: CONNECTOR_FOCUS_TYPE }, sender(11, 3)),
        ).resolves.toEqual({
            type: CONNECTOR_FOCUS_RESULT_TYPE,
            focused: true,
            owner: { tabId: 10, windowId: 2 },
        });
        expect(context.focusCalls).toEqual(['window:2', 'tab:10']);
    });

    it('ignores claims from outside the extension', async () => {
        const context = backgroundApi();
        const coordinator = new ConnectorOwnershipCoordinator(context.api);
        await expect(
            coordinator.handle(claim('doc-a', 10), {
                id: 'someone-else',
                tab: { id: 10, windowId: 2 },
            }),
        ).resolves.toBeUndefined();
        expect(coordinator.currentOwner()).toBeNull();
    });
});

class FakeLocks implements OwnershipLockManager {
    held = false;
    requests: string[] = [];

    constructor(private readonly available = true) {}

    request(
        name: string,
        _options: { ifAvailable: true },
        callback: (lock: unknown | null) => Promise<void> | void,
    ): Promise<unknown> {
        this.requests.push(name);
        if (!this.available) {
            return Promise.resolve(callback(null));
        }
        this.held = true;
        const result = callback({ name });
        if (result === undefined) {
            this.held = false;
            return Promise.resolve();
        }
        return Promise.resolve(result).then(() => {
            this.held = false;
        });
    }
}

function pageApi(
    options: {
        locks?: FakeLocks | null;
        claim?: unknown;
        focus?: unknown;
        claimRejects?: boolean;
    } = {},
) {
    const sent: Record<string, unknown>[] = [];
    const listeners: ((
        message: unknown,
        sender: unknown,
        sendResponse: (response?: unknown) => void,
    ) => boolean | undefined)[] = [];
    const api: ConnectorOwnershipApi = {
        runtime: {
            sendMessage: vi.fn(async (message: unknown) => {
                const value = message as Record<string, unknown>;
                sent.push(value);
                if (value.type === CONNECTOR_CLAIM_TYPE) {
                    if (options.claimRejects === true) throw new Error('worker unavailable');
                    return options.claim;
                }
                if (value.type === CONNECTOR_FOCUS_TYPE) return options.focus;
                return { type: CONNECTOR_RELEASE_TYPE, released: true };
            }),
            onMessage: {
                addListener: (listener) => {
                    listeners.push(listener);
                },
            },
        },
        ...(options.locks === undefined ? { locks: null } : { locks: options.locks }),
        documentId: 'doc-page',
        requestTimeoutMs: 100,
    };
    return { api, sent, listeners };
}

describe('connector-page ownership client', () => {
    it('does not claim without the Web Lock and asks the worker to focus the owner', async () => {
        const locks = new FakeLocks(false);
        const context = pageApi({
            locks,
            focus: {
                type: CONNECTOR_FOCUS_RESULT_TYPE,
                focused: true,
                owner: { tabId: 10, windowId: 2 },
            },
        });
        const ownership = new ConnectorOwnership(context.api);
        await expect(ownership.claim()).resolves.toEqual({
            owner: false,
            reason: 'another-document',
            ownerTab: { tabId: 10, windowId: 2 },
        });
        expect(context.sent.map((message) => message.type)).toEqual([CONNECTOR_FOCUS_TYPE]);
    });

    it('announces a held Web Lock and becomes the owner before any native port exists', async () => {
        const locks = new FakeLocks(true);
        const context = pageApi({
            locks,
            claim: { type: CONNECTOR_CLAIM_RESULT_TYPE, granted: true },
        });
        const ownership = new ConnectorOwnership(context.api);
        await expect(ownership.claim()).resolves.toEqual({ owner: true });
        expect(ownership.isOwner()).toBe(true);
        expect(context.sent[0]).toMatchObject({
            type: CONNECTOR_CLAIM_TYPE,
            documentId: 'doc-page',
            holdsLock: true,
        });
        await ownership.release();
        expect(context.sent.at(-1)).toMatchObject({
            type: CONNECTOR_RELEASE_TYPE,
            documentId: 'doc-page',
        });
        expect(locks.held).toBe(false);
    });

    it('keeps a lock-held owner usable even when the worker answer is unavailable', async () => {
        const locks = new FakeLocks(true);
        const context = pageApi({ locks, claimRejects: true });
        const ownership = new ConnectorOwnership(context.api);
        await expect(ownership.claim()).resolves.toEqual({ owner: true });
    });

    it('fails closed without Web Locks when arbitration cannot answer', async () => {
        const context = pageApi({ locks: null, claimRejects: true });
        const ownership = new ConnectorOwnership(context.api);
        await expect(ownership.claim()).resolves.toEqual({ owner: false, reason: 'claim-failed' });
    });

    it('answers only its own owner-liveness probe', async () => {
        const context = pageApi({ locks: null, claim: { granted: false } });
        new ConnectorOwnership(context.api);
        const listener = context.listeners[0];
        const responses: unknown[] = [];
        listener?.({ type: CONNECTOR_OWNER_PROBE_TYPE, documentId: 'doc-page' }, {}, (response) =>
            responses.push(response),
        );
        listener?.({ type: CONNECTOR_OWNER_PROBE_TYPE, documentId: 'other-page' }, {}, (response) =>
            responses.push(response),
        );
        expect(responses).toEqual([{ type: CONNECTOR_OWNER_HERE_TYPE, documentId: 'doc-page' }]);
    });
});
