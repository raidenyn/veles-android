import { describe, expect, it, vi, type Mock } from 'vitest';
import {
    CONNECTOR_BOOTSTRAP_GRACE_MS,
    CONNECTOR_FOCUS_RECOVERY_ATTEMPTS,
    CONNECTOR_PING_TYPE,
    CONNECTOR_PONG_TYPE,
    CONNECTOR_TAB_KEY,
    openOrFocusConnector,
    setupLauncher,
    type ConnectorTabRef,
    type LauncherChrome,
} from '../src/background';
import {
    CONNECTOR_PING_TYPE as PAGE_PING_TYPE,
    CONNECTOR_PONG_TYPE as PAGE_PONG_TYPE,
    makeConnectorPingListener,
    registerConnectorPing,
} from '../src/connector';

interface FakeTab {
    id: number;
    windowId: number;
    status: 'loading' | 'complete';
}

interface FakeOptions {
    session?: boolean;
    storedTab?: ConnectorTabRef;
    storedTabClosed?: boolean;
    ping?: () => Promise<unknown>;
    createTab?: () => Promise<{ id?: number; windowId?: number }>;
    windowUpdate?: (windowId: number, info: { focused: boolean }) => Promise<unknown>;
    tabUpdate?: (tabId: number, info: { active: boolean }) => Promise<unknown>;
    now?: () => number;
}

function makeFakeApi(options: FakeOptions = {}) {
    const store: Record<string, unknown> = {};
    const liveTabs = new Map<number, FakeTab>();
    let nextTabId = 20;
    if (options.storedTab) {
        store[CONNECTOR_TAB_KEY] = { ...options.storedTab };
        if (options.storedTabClosed !== true) {
            liveTabs.set(options.storedTab.tabId, {
                id: options.storedTab.tabId,
                windowId: options.storedTab.windowId,
                status: 'complete',
            });
        }
    }

    const session =
        options.session === false
            ? undefined
            : {
                  get: vi.fn(async (key: string) => ({ [key]: store[key] })),
                  set: vi.fn(async (items: Record<string, unknown>) => {
                      Object.assign(store, items);
                  }),
                  remove: vi.fn(async (key: string) => {
                      Reflect.deleteProperty(store, key);
                  }),
              };

    const sendMessage = vi.fn(
        options.ping ??
            (() =>
                Promise.reject(
                    new Error('Could not establish connection. Receiving end does not exist.'),
                )),
    );
    const create = vi.fn(
        options.createTab ??
            (async () => {
                const tab = { id: nextTabId, windowId: 2 };
                nextTabId += 1;
                liveTabs.set(tab.id, { ...tab, status: 'loading' });
                return tab;
            }),
    );
    const get = vi.fn(async (tabId: number) => {
        const tab = liveTabs.get(tabId);
        if (!tab) throw new Error(`No tab with id: ${tabId}`);
        return { ...tab };
    });
    const update = vi.fn(options.tabUpdate ?? (async () => ({})));
    const windowsUpdate = vi.fn(options.windowUpdate ?? (async () => ({})));
    const addListener = vi.fn();

    const api = {
        runtime: {
            getURL: (path: string) => `chrome-extension://test/${path}`,
            sendMessage,
        },
        tabs: { create, get, update },
        windows: { update: windowsUpdate },
        storage: session ? { session } : {},
        now: options.now ?? (() => 1000),
        action: { onClicked: { addListener } },
    } as unknown as LauncherChrome;

    return { api, store, liveTabs, session };
}

function mockOf(fn: unknown): Mock {
    return fn as Mock;
}

// Builds the round-2 bootstrap interleaving: a fresh connector exists, the
// second click's initial ping misses, tabs.get is held while the page becomes
// complete, and a later ping identifies the existing owner. The supplied
// window-focus implementation applies only to that second-click phase.
async function latePongInterleaving(
    windowUpdate: (windowId: number, info: { focused: boolean }) => Promise<unknown>,
) {
    const { api, store, liveTabs } = makeFakeApi();
    await openOrFocusConnector(api);
    const created = liveTabs.get(20);
    if (created === undefined) throw new Error('fresh connector was not created');

    const send = mockOf(api.runtime.sendMessage);
    send.mockReset();
    send.mockRejectedValueOnce(new Error('no receiver')).mockResolvedValue({
        type: CONNECTOR_PONG_TYPE,
        tabId: 20,
        windowId: 2,
    });

    let releaseGet!: () => void;
    const getGate = new Promise<void>((resolve) => {
        releaseGet = resolve;
    });
    mockOf(api.tabs.get).mockImplementation(async (tabId: number) => {
        await getGate;
        const tab = liveTabs.get(tabId);
        if (tab === undefined) throw new Error(`No tab with id: ${tabId}`);
        return { ...tab };
    });

    mockOf(api.windows.update).mockReset();
    mockOf(api.tabs.update).mockReset();
    mockOf(api.windows.update).mockImplementation(windowUpdate);
    mockOf(api.tabs.update).mockImplementation(async () => ({}));

    const second = openOrFocusConnector(api);
    await vi.waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    created.status = 'complete';
    releaseGet();
    await second;

    return { api, store };
}

describe('connector launcher', () => {
    it('serializes two concurrent clicks into exactly one created tab', async () => {
        const { api, store } = makeFakeApi();

        await Promise.all([openOrFocusConnector(api), openOrFocusConnector(api)]);

        expect(mockOf(api.tabs.create)).toHaveBeenCalledTimes(1);
        expect(mockOf(api.tabs.create)).toHaveBeenCalledWith({
            url: 'chrome-extension://test/connector.html',
            active: true,
        });
        // The second queued click could not ping the still-loading new tab;
        // the bounded fresh-tab state lets it focus that tab instead of
        // creating a second one.
        expect(mockOf(api.tabs.get)).toHaveBeenCalledWith(20);
        expect(mockOf(api.windows.update)).toHaveBeenCalledWith(2, { focused: true });
        expect(mockOf(api.tabs.update)).toHaveBeenCalledWith(20, { active: true });
        expect(store[CONNECTOR_TAB_KEY]).toEqual({
            tabId: 20,
            windowId: 2,
            freshUntil: 1000 + CONNECTOR_BOOTSTRAP_GRACE_MS,
        });
    });

    it('revalidates a fresh tab that finishes bootstrap before tabs.get', async () => {
        const { api, store, liveTabs } = makeFakeApi();

        // First click creates the sole fresh connector.
        await openOrFocusConnector(api);
        expect(mockOf(api.tabs.create)).toHaveBeenCalledTimes(1);
        const created = liveTabs.get(20);
        expect(created?.status).toBe('loading');

        // Second click: the initial ping still misses the responder, but the
        // page completes bootstrap before tabs.get resolves. Hold tabs.get to
        // make that interleaving deterministic.
        const send = mockOf(api.runtime.sendMessage);
        send.mockReset();
        send.mockRejectedValueOnce(new Error('no receiver')).mockResolvedValueOnce({
            type: CONNECTOR_PONG_TYPE,
            tabId: 20,
            windowId: 2,
        });

        let releaseGet!: () => void;
        const getGate = new Promise<void>((resolve) => {
            releaseGet = resolve;
        });
        const getImplementation = mockOf(api.tabs.get).getMockImplementation();
        mockOf(api.tabs.get).mockImplementation(async (tabId: number) => {
            await getGate;
            if (getImplementation === undefined) throw new Error('missing fake tabs.get');
            return getImplementation(tabId);
        });
        // Ignore the first click's already-completed focus calls.
        mockOf(api.windows.update).mockClear();
        mockOf(api.tabs.update).mockClear();

        const second = openOrFocusConnector(api);
        await vi.waitFor(() => expect(send).toHaveBeenCalledTimes(1));
        if (created !== undefined) created.status = 'complete';
        releaseGet();
        await second;

        // The late pong revalidated the existing owner: no second tab.
        expect(mockOf(api.tabs.create)).toHaveBeenCalledTimes(1);
        expect(mockOf(api.windows.update)).toHaveBeenCalledWith(2, { focused: true });
        expect(mockOf(api.tabs.update)).toHaveBeenCalledWith(20, { active: true });
        const order = (fn: unknown) => mockOf(fn).mock.invocationCallOrder[0];
        expect(order(api.windows.update)).toBeLessThan(order(api.tabs.update));
        expect(store[CONNECTOR_TAB_KEY]).toEqual({ tabId: 20, windowId: 2 });
    });

    it('recovers a transient late-pong focus failure without a duplicate', async () => {
        let attempts = 0;
        const { api, store } = await latePongInterleaving(async () => {
            attempts += 1;
            if (attempts === 1) throw new Error('transient focus failure');
            return {};
        });

        expect(mockOf(api.tabs.create)).toHaveBeenCalledTimes(1);
        expect(mockOf(api.windows.update)).toHaveBeenCalledTimes(2);
        expect(mockOf(api.tabs.update)).toHaveBeenCalledWith(20, { active: true });
        expect(store[CONNECTOR_TAB_KEY]).toEqual({ tabId: 20, windowId: 2 });
    });

    it('keeps the late-pong owner when focus keeps failing', async () => {
        const { api, store } = await latePongInterleaving(async () => {
            throw new Error('persistent focus failure');
        });

        expect(mockOf(api.tabs.create)).toHaveBeenCalledTimes(1);
        expect(mockOf(api.windows.update)).toHaveBeenCalledTimes(
            1 + CONNECTOR_FOCUS_RECOVERY_ATTEMPTS,
        );
        expect(mockOf(api.tabs.update)).not.toHaveBeenCalled();
        expect(store[CONNECTOR_TAB_KEY]).toEqual({ tabId: 20, windowId: 2 });
    });

    it('focuses the owning window then activates an existing connector tab', async () => {
        const { api } = makeFakeApi({
            storedTab: { tabId: 4, windowId: 2 },
            ping: async () => ({ type: CONNECTOR_PONG_TYPE, tabId: 4, windowId: 2 }),
        });

        await openOrFocusConnector(api);

        expect(mockOf(api.runtime.sendMessage)).toHaveBeenCalledWith({ type: CONNECTOR_PING_TYPE });
        expect(mockOf(api.tabs.create)).not.toHaveBeenCalled();
        expect(mockOf(api.windows.update)).toHaveBeenCalledWith(2, { focused: true });
        expect(mockOf(api.tabs.update)).toHaveBeenCalledWith(4, { active: true });
        const order = (fn: unknown) => mockOf(fn).mock.invocationCallOrder[0];
        expect(order(api.windows.update)).toBeLessThan(order(api.tabs.update));
    });

    it('creates a tab and replaces the stored id when the old tab is gone', async () => {
        const { api, store } = makeFakeApi({
            storedTab: { tabId: 3, windowId: 1 },
            storedTabClosed: true,
        });

        await openOrFocusConnector(api);

        expect(mockOf(api.tabs.create)).toHaveBeenCalledTimes(1);
        expect(store[CONNECTOR_TAB_KEY]).toEqual({
            tabId: 20,
            windowId: 2,
            freshUntil: 1000 + CONNECTOR_BOOTSTRAP_GRACE_MS,
        });
        expect(mockOf(api.windows.update)).toHaveBeenCalledWith(2, { focused: true });
        expect(mockOf(api.tabs.update)).toHaveBeenCalledWith(20, { active: true });
    });

    it('replaces a live stored tab that no longer answers the ping', async () => {
        // The tab id still exists and its document is complete, but it has
        // navigated away from the connector. Liveness alone is not identity.
        const { api, store } = makeFakeApi({
            storedTab: { tabId: 3, windowId: 1 },
        });

        await openOrFocusConnector(api);

        expect(mockOf(api.tabs.create)).toHaveBeenCalledTimes(1);
        expect(mockOf(api.tabs.update)).not.toHaveBeenCalledWith(3, { active: true });
        expect(mockOf(api.tabs.update)).toHaveBeenCalledWith(20, { active: true });
        expect(store[CONNECTOR_TAB_KEY]).toEqual({
            tabId: 20,
            windowId: 2,
            freshUntil: 1000 + CONNECTOR_BOOTSTRAP_GRACE_MS,
        });
    });

    it('retries focus for a same-ID pong instead of creating a duplicate', async () => {
        const pong = { type: CONNECTOR_PONG_TYPE, tabId: 4, windowId: 2 };
        let windowUpdates = 0;
        const { api, store } = makeFakeApi({
            storedTab: { tabId: 4, windowId: 2 },
            ping: async () => pong,
            windowUpdate: async () => {
                windowUpdates += 1;
                if (windowUpdates === 1) throw new Error('transient focus failure');
                return {};
            },
        });

        await openOrFocusConnector(api);

        expect(mockOf(api.tabs.create)).not.toHaveBeenCalled();
        expect(mockOf(api.windows.update)).toHaveBeenCalledTimes(2);
        expect(mockOf(api.windows.update)).toHaveBeenLastCalledWith(2, { focused: true });
        expect(mockOf(api.tabs.update)).toHaveBeenCalledWith(4, { active: true });
        expect(store[CONNECTOR_TAB_KEY]).toEqual({ tabId: 4, windowId: 2 });
    });

    it('keeps one owner when a confirmed connector stays unfocusable', async () => {
        const pong = { type: CONNECTOR_PONG_TYPE, tabId: 4, windowId: 2 };
        const { api, store } = makeFakeApi({
            storedTab: { tabId: 4, windowId: 2 },
            ping: async () => pong,
            windowUpdate: async () => {
                throw new Error('persistent focus failure');
            },
        });

        await openOrFocusConnector(api);

        expect(mockOf(api.tabs.create)).not.toHaveBeenCalled();
        expect(mockOf(api.windows.update)).toHaveBeenCalledTimes(
            1 + CONNECTOR_FOCUS_RECOVERY_ATTEMPTS,
        );
        expect(mockOf(api.tabs.update)).not.toHaveBeenCalled();
        expect(store[CONNECTOR_TAB_KEY]).toEqual({ tabId: 4, windowId: 2 });
    });

    it('expires the fresh-tab bootstrap state instead of trusting it forever', async () => {
        let now = 1000;
        const { api, store } = makeFakeApi({
            now: () => now,
        });

        await openOrFocusConnector(api);
        expect(mockOf(api.tabs.create)).toHaveBeenCalledTimes(1);
        expect(store[CONNECTOR_TAB_KEY]).toEqual({
            tabId: 20,
            windowId: 2,
            freshUntil: 1000 + CONNECTOR_BOOTSTRAP_GRACE_MS,
        });

        now += CONNECTOR_BOOTSTRAP_GRACE_MS;
        await openOrFocusConnector(api);

        expect(mockOf(api.tabs.create)).toHaveBeenCalledTimes(2);
        expect(store[CONNECTOR_TAB_KEY]).toEqual({
            tabId: 21,
            windowId: 2,
            freshUntil: 1000 + 2 * CONNECTOR_BOOTSTRAP_GRACE_MS,
        });
    });

    it('rediscovers a live connector after a stale focus failure', async () => {
        let pings = 0;
        let windowUpdates = 0;
        const { api, store } = makeFakeApi({
            storedTab: { tabId: 3, windowId: 1 },
            ping: async () => {
                pings += 1;
                return pings === 1
                    ? { type: CONNECTOR_PONG_TYPE, tabId: 3, windowId: 1 }
                    : { type: CONNECTOR_PONG_TYPE, tabId: 5, windowId: 6 };
            },
            windowUpdate: async () => {
                windowUpdates += 1;
                if (windowUpdates === 1) throw new Error('No window with id: 1');
                return {};
            },
        });

        await openOrFocusConnector(api);

        expect(mockOf(api.tabs.create)).not.toHaveBeenCalled();
        expect(mockOf(api.windows.update)).toHaveBeenNthCalledWith(1, 1, { focused: true });
        expect(mockOf(api.windows.update)).toHaveBeenNthCalledWith(2, 6, { focused: true });
        expect(mockOf(api.tabs.update)).toHaveBeenCalledWith(5, { active: true });
        expect(store[CONNECTOR_TAB_KEY]).toEqual({ tabId: 5, windowId: 6 });
    });

    it('falls back to in-memory tab state when storage.session is unavailable', async () => {
        const { api } = makeFakeApi({ session: false });

        await Promise.all([openOrFocusConnector(api), openOrFocusConnector(api)]);

        expect(mockOf(api.tabs.create)).toHaveBeenCalledTimes(1);
        expect(mockOf(api.tabs.get)).toHaveBeenCalledWith(20);
        expect(mockOf(api.tabs.update)).toHaveBeenCalledWith(20, { active: true });
    });

    it('registers an action click listener that opens the connector', async () => {
        const { api } = makeFakeApi();

        setupLauncher(api);

        const addListener = mockOf(api.action?.onClicked.addListener);
        expect(addListener).toHaveBeenCalledTimes(1);
        const listener = addListener.mock.calls[0][0] as () => void;
        listener();

        await vi.waitFor(() => expect(mockOf(api.tabs.create)).toHaveBeenCalledTimes(1));
    });
});

describe('connector bootstrap ping', () => {
    const tab = { id: 7, windowId: 2 };

    function makePingApi() {
        const addListener = vi.fn();
        const getCurrent = vi.fn(async () => tab);
        const api = {
            runtime: { id: 'extension-id', onMessage: { addListener } },
            tabs: { getCurrent },
        };
        return { api, addListener, getCurrent };
    }

    it('answers a ping from this extension with the current tab and window', async () => {
        const { api } = makePingApi();
        const listener = makeConnectorPingListener(api);
        const sendResponse = vi.fn();

        expect(listener({ type: PAGE_PING_TYPE }, { id: 'extension-id' }, sendResponse)).toBe(true);

        await vi.waitFor(() =>
            expect(sendResponse).toHaveBeenCalledWith({
                type: PAGE_PONG_TYPE,
                tabId: 7,
                windowId: 2,
            }),
        );
    });

    it('ignores pings from another extension or without an extension sender', () => {
        const { api, getCurrent } = makePingApi();
        const listener = makeConnectorPingListener(api);
        const sendResponse = vi.fn();

        expect(listener({ type: PAGE_PING_TYPE }, { id: 'other-extension' }, sendResponse)).toBe(
            false,
        );
        expect(listener({ type: PAGE_PING_TYPE }, {}, sendResponse)).toBe(false);
        expect(getCurrent).not.toHaveBeenCalled();
        expect(sendResponse).not.toHaveBeenCalled();
    });

    it('ignores unrelated messages', () => {
        const { api, getCurrent } = makePingApi();
        const listener = makeConnectorPingListener(api);
        const sendResponse = vi.fn();

        expect(listener({ type: 'something.else' }, { id: 'extension-id' }, sendResponse)).toBe(
            false,
        );
        expect(getCurrent).not.toHaveBeenCalled();
        expect(sendResponse).not.toHaveBeenCalled();
    });

    it('registers the ping responder on the connector runtime', () => {
        const { api, addListener } = makePingApi();

        registerConnectorPing(api);

        expect(addListener).toHaveBeenCalledTimes(1);
    });
});
