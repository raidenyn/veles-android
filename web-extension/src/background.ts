import { setupOwnership, type OwnershipRuntimeApi } from './connector/ownership';
import { ChromeLocalStore } from './trust/storage';

// Restrict on every worker load, not only installation/update. Repository
// operations independently await restriction before reading or writing trust.
// A denied restriction never exposes a repository; callers fail closed.
export const trustStorageReady =
    typeof chrome !== 'undefined' && chrome.storage?.local
        ? new ChromeLocalStore().restrict().then(
              () => true,
              () => false,
          )
        : Promise.resolve(false);

// ---------------------------------------------------------------------------
// Connector launcher
// ---------------------------------------------------------------------------

export const CONNECTOR_TAB_KEY = 'veles.connector.tabId';
export const CONNECTOR_PING_TYPE = 'veles.connector.ping';
export const CONNECTOR_PONG_TYPE = 'veles.connector.pong';
// A just-created tab is trusted without a pong only while it is still
// bootstrapping. After this bound it counts as an established reference and
// must answer the ping again.
export const CONNECTOR_BOOTSTRAP_GRACE_MS = 5_000;
export const CONNECTOR_FOCUS_RECOVERY_ATTEMPTS = 2;
const CONNECTOR_PAGE = 'connector.html';

export interface ConnectorTabRef {
    tabId: number;
    windowId: number;
}

interface StoredConnectorTab extends ConnectorTabRef {
    // Present only for a freshly created tab that has not answered yet.
    freshUntil?: number;
}

interface SessionArea {
    get(keys: string | string[]): Promise<Record<string, unknown>>;
    set(items: Record<string, unknown>): Promise<void>;
    remove?(keys: string | string[]): Promise<void>;
}

// Minimal injected surface so the launcher is testable without a browser.
// It deliberately has no `tabs.query` and never exposes `tab.url`.
export interface LauncherChrome {
    runtime: {
        getURL(path: string): string;
        sendMessage(message: unknown): Promise<unknown>;
    };
    tabs: {
        create(createProperties: { url: string; active: boolean }): Promise<{
            id?: number;
            windowId?: number;
        }>;
        get(tabId: number): Promise<{ id?: number; windowId?: number; status?: string }>;
        update(tabId: number, updateProperties: { active: boolean }): Promise<unknown>;
    };
    windows: {
        update(windowId: number, updateInfo: { focused: boolean }): Promise<unknown>;
    };
    storage?: {
        session?: SessionArea;
    };
    now?: () => number;
    action?: {
        onClicked: {
            addListener(listener: () => void): void;
        };
    };
}

// storage.session is not guaranteed on every supported Chrome build. The
// per-API fallback keeps the tab id for the service worker lifetime; if the
// worker restarts, a broadcast ping rediscovers the live connector.
const sessionFallback = new WeakMap<LauncherChrome, StoredConnectorTab>();

let launcherQueue: Promise<void> = Promise.resolve();

function currentTime(api: LauncherChrome): number {
    return api.now?.() ?? Date.now();
}

function isTabRef(value: unknown): value is ConnectorTabRef {
    if (typeof value !== 'object' || value === null) return false;
    const candidate = value as { tabId?: unknown; windowId?: unknown };
    return (
        typeof candidate.tabId === 'number' &&
        Number.isInteger(candidate.tabId) &&
        candidate.tabId >= 0 &&
        typeof candidate.windowId === 'number' &&
        Number.isInteger(candidate.windowId) &&
        candidate.windowId >= 0
    );
}

function isStoredTab(value: unknown): value is StoredConnectorTab {
    if (!isTabRef(value)) return false;
    const freshUntil = (value as { freshUntil?: unknown }).freshUntil;
    return (
        freshUntil === undefined || (typeof freshUntil === 'number' && Number.isFinite(freshUntil))
    );
}

function sessionArea(api: LauncherChrome): SessionArea | undefined {
    const session = api.storage?.session;
    if (session === undefined) return undefined;
    if (typeof session.get !== 'function' || typeof session.set !== 'function') return undefined;
    return session;
}

async function readStoredTab(api: LauncherChrome): Promise<StoredConnectorTab | undefined> {
    const session = sessionArea(api);
    if (session !== undefined) {
        try {
            const items = await session.get(CONNECTOR_TAB_KEY);
            const stored = items?.[CONNECTOR_TAB_KEY];
            if (isStoredTab(stored)) return stored;
        } catch {
            // Fall through to the in-memory fallback.
        }
    }
    return sessionFallback.get(api);
}

async function writeStoredTab(api: LauncherChrome, tab: StoredConnectorTab): Promise<void> {
    sessionFallback.set(api, tab);
    const session = sessionArea(api);
    if (session === undefined) return;
    try {
        await session.set({ [CONNECTOR_TAB_KEY]: tab });
    } catch {
        // The in-memory copy remains authoritative for this worker.
    }
}

async function storeEstablishedTab(api: LauncherChrome, ref: ConnectorTabRef): Promise<void> {
    await writeStoredTab(api, { tabId: ref.tabId, windowId: ref.windowId });
}

async function storeFreshTab(
    api: LauncherChrome,
    ref: ConnectorTabRef,
    freshUntil: number,
): Promise<void> {
    await writeStoredTab(api, { tabId: ref.tabId, windowId: ref.windowId, freshUntil });
}

async function clearStoredTab(api: LauncherChrome): Promise<void> {
    sessionFallback.delete(api);
    const session = sessionArea(api);
    if (session === undefined) return;
    try {
        if (typeof session.remove === 'function') {
            await session.remove(CONNECTOR_TAB_KEY);
        } else {
            await session.set({ [CONNECTOR_TAB_KEY]: null });
        }
    } catch {
        // The fallback is already cleared; a failed remove is harmless.
    }
}

function parsePong(value: unknown): ConnectorTabRef | undefined {
    if (typeof value !== 'object' || value === null) return undefined;
    const candidate = value as { type?: unknown; tabId?: unknown; windowId?: number };
    if (candidate.type !== CONNECTOR_PONG_TYPE) return undefined;
    return isTabRef(candidate)
        ? { tabId: candidate.tabId, windowId: candidate.windowId }
        : undefined;
}

async function requestPong(api: LauncherChrome): Promise<ConnectorTabRef | undefined> {
    try {
        return parsePong(await api.runtime.sendMessage({ type: CONNECTOR_PING_TYPE }));
    } catch {
        // No connector page is listening yet.
        return undefined;
    }
}

async function liveTab(api: LauncherChrome, tabId: number) {
    try {
        return await api.tabs.get(tabId);
    } catch {
        return undefined;
    }
}

function tabRefFrom(
    tab: { id?: number; windowId?: number },
    fallback?: ConnectorTabRef,
): ConnectorTabRef | undefined {
    const tabId = tab.id;
    const windowId = tab.windowId ?? fallback?.windowId;
    if (
        typeof tabId !== 'number' ||
        !Number.isInteger(tabId) ||
        tabId < 0 ||
        typeof windowId !== 'number' ||
        !Number.isInteger(windowId) ||
        windowId < 0
    ) {
        return undefined;
    }
    return { tabId, windowId };
}

// Focus the owning window before activating its tab. Both calls are
// permission-free; a failure means the window/tab went away.
async function focusTab(api: LauncherChrome, ref: ConnectorTabRef): Promise<boolean> {
    try {
        await api.windows.update(ref.windowId, { focused: true });
    } catch {
        return false;
    }
    try {
        await api.tabs.update(ref.tabId, { active: true });
    } catch {
        return false;
    }
    return true;
}

async function focusConfirmed(api: LauncherChrome, ref: ConnectorTabRef): Promise<boolean> {
    if (!(await focusTab(api, ref))) return false;
    // A pong is document identity. Once confirmed, drop any bootstrap grace.
    await storeEstablishedTab(api, ref);
    return true;
}

async function focusFreshStoredTab(
    api: LauncherChrome,
    stored: StoredConnectorTab,
): Promise<boolean> {
    if (stored.freshUntil === undefined || currentTime(api) >= stored.freshUntil) return false;

    // A just-created tab may still be loading and unable to answer the ping.
    // Only within the bounded bootstrap window, and only while that document
    // is still loading, may a tab be focused without a pong. A completed tab
    // that no longer answers is not accepted as the connector.
    const tab = await liveTab(api, stored.tabId);
    if (tab === undefined) return false;
    if (tab.status !== undefined && tab.status !== 'loading') return false;

    const ref = tabRefFrom(tab, stored);
    if (ref === undefined) return false;
    if (!(await focusTab(api, ref))) return false;
    await storeFreshTab(api, ref, stored.freshUntil);
    return true;
}

// Every validated pong — initial or late — goes through this single bounded
// focus/recovery policy. A pong is identity; failure to focus it must never be
// treated as permission to create a second owner.
async function handleValidatedPong(api: LauncherChrome, pong: ConnectorTabRef): Promise<void> {
    if (await focusConfirmed(api, pong)) return;
    await recoverConfirmedFocus(api, pong);
}

async function recoverConfirmedFocus(api: LauncherChrome, initial: ConnectorTabRef): Promise<void> {
    let current = initial;
    let revalidated = false;
    for (let attempt = 0; attempt < CONNECTOR_FOCUS_RECOVERY_ATTEMPTS; attempt += 1) {
        const next = await requestPong(api);
        if (next === undefined) break;
        revalidated = true;
        current = next;
        // Same-ID pongs are accepted: a transient window/tab focus failure is
        // not evidence that the connector document disappeared.
        if (await focusConfirmed(api, next)) return;
    }

    if (revalidated) {
        // A connector still answers, so keep this single owner even though it
        // could not be focused. A later click retries rather than duplicating.
        await storeEstablishedTab(api, current);
        return;
    }

    // No connector could be reconfirmed; recover by creating one.
    await clearStoredTab(api);
    await createConnectorTab(api);
}

async function createConnectorTab(api: LauncherChrome): Promise<void> {
    const created = await api.tabs.create({
        url: api.runtime.getURL(CONNECTOR_PAGE),
        active: true,
    });
    let ref = tabRefFrom(created);
    if (ref === undefined && typeof created.id === 'number') {
        const tab = await liveTab(api, created.id);
        if (tab !== undefined) ref = tabRefFrom(tab);
    }
    if (ref === undefined) return;
    await storeFreshTab(api, ref, currentTime(api) + CONNECTOR_BOOTSTRAP_GRACE_MS);
    await focusTab(api, ref);
}

async function openOrFocus(api: LauncherChrome): Promise<void> {
    const stored = await readStoredTab(api);
    const pong = await requestPong(api);

    if (pong !== undefined) {
        await handleValidatedPong(api, pong);
        return;
    }

    if (stored !== undefined) {
        if (stored.freshUntil !== undefined) {
            if (await focusFreshStoredTab(api, stored)) return;

            // The fresh tab may have finished bootstrapping between the first
            // ping and tabs.get: the responder is now live even though the
            // initial ping missed it. Revalidate identity before replacing it,
            // so a newly available pong focuses the existing owner instead of
            // creating a duplicate. This is identity confirmation, not
            // indefinite liveness trust; the fresh window stays bounded.
            const latePong = await requestPong(api);
            if (latePong !== undefined) {
                await handleValidatedPong(api, latePong);
                return;
            }
        }

        // Established references require a pong; a live tab id alone is not
        // document identity. A navigated-away tab is replaced, not focused.
        await clearStoredTab(api);
    }

    await createConnectorTab(api);
}

// Clicks are serialized across the whole worker so two near-simultaneous
// clicks cannot both observe "no connector" and create two tabs.
export function openOrFocusConnector(api: LauncherChrome): Promise<void> {
    const run = launcherQueue.then(() => openOrFocus(api));
    launcherQueue = run.then(
        () => undefined,
        () => undefined,
    );
    return run;
}

export function setupLauncher(api: LauncherChrome): void {
    if (!api.action?.onClicked) {
        throw new Error('chrome.action.onClicked is unavailable');
    }
    api.action.onClicked.addListener(() => {
        void openOrFocusConnector(api).catch(() => undefined);
    });
}

// Extension-wide connector ownership arbitration for restored/duplicated
// connector documents. The page must be granted ownership before it opens a
// Native Messaging port; this runs in the worker so the decision survives any
// one document and can focus the existing owner.
if (typeof chrome !== 'undefined' && chrome.runtime?.onMessage && chrome.tabs && chrome.windows) {
    setupOwnership(chrome as unknown as OwnershipRuntimeApi);
}

// MV3 service worker entry.
if (typeof chrome !== 'undefined' && chrome.action?.onClicked) {
    setupLauncher(chrome as unknown as LauncherChrome);
}

// The chrome-global guard exists so vitest (Node environment) can import this
// module without a browser runtime; the test setup stubs chrome.runtime anyway,
// but defensive coding here prevents a hard ReferenceError if the stub is
// missed in a future test file.
if (typeof chrome !== 'undefined' && chrome.runtime?.onInstalled) {
    chrome.runtime.onInstalled.addListener(() => {
        // no-op placeholder
    });
}

export {};
