// Extension-wide connector ownership.
//
// Only one connector document may open the Native Messaging host. Ownership is
// established before any `connectNative` call and is released on page
// lifecycle. When the browser supports Web Locks (Chrome 102 does), the lock is
// the authoritative mutual exclusion: it is released automatically if the page
// crashes or is killed, so a hidden/dead owner cannot block the extension
// forever. The service worker records the owner tab/window for the launcher and
// focus policy, and serializes claims when Web Locks are unavailable.

export const CONNECTOR_CLAIM_TYPE = 'veles.connector.claim';
export const CONNECTOR_CLAIM_RESULT_TYPE = 'veles.connector.claim-result';
export const CONNECTOR_RELEASE_TYPE = 'veles.connector.release';
export const CONNECTOR_FOCUS_TYPE = 'veles.connector.focus';
export const CONNECTOR_FOCUS_RESULT_TYPE = 'veles.connector.focus-result';
export const CONNECTOR_OWNER_PROBE_TYPE = 'veles.connector.owner-probe';
export const CONNECTOR_OWNER_HERE_TYPE = 'veles.connector.owner-here';
export const CONNECTOR_OWNER_LOCK = 'veles.connector.owner';

const DOCUMENT_ID_PATTERN = /^[A-Za-z0-9._-]{1,64}$/;
const DEFAULT_CLAIM_TIMEOUT_MS = 2_000;
const DEFAULT_PROBE_TIMEOUT_MS = 500;

export interface ConnectorOwnerTabRef {
    tabId: number;
    windowId: number;
}

export interface ConnectorOwner extends ConnectorOwnerTabRef {
    documentId: string;
}

export interface OwnershipMessageSender {
    id?: string;
    tab?: {
        id?: number;
        windowId?: number;
    };
}

export interface OwnershipClaimResponse {
    type: typeof CONNECTOR_CLAIM_RESULT_TYPE;
    granted: boolean;
    owner?: ConnectorOwnerTabRef;
}

export interface OwnershipFocusResponse {
    type: typeof CONNECTOR_FOCUS_RESULT_TYPE;
    focused: boolean;
    owner?: ConnectorOwnerTabRef;
}

export type OwnershipClaimOutcome =
    | { owner: true }
    | {
          owner: false;
          reason: 'another-document' | 'claim-failed' | 'unauthorized' | 'storage';
          ownerTab?: ConnectorOwnerTabRef;
      };

export interface OwnershipRuntimeApi {
    runtime: {
        id?: string;
        sendMessage(message: unknown): Promise<unknown>;
        onMessage: {
            addListener(
                listener: (
                    message: unknown,
                    sender: OwnershipMessageSender,
                    sendResponse: (response?: unknown) => void,
                ) => boolean | undefined,
            ): void;
        };
    };
    tabs: {
        get(tabId: number): Promise<{ id?: number; windowId?: number }>;
        update(tabId: number, updateProperties: { active: boolean }): Promise<unknown>;
        onRemoved?: {
            addListener(listener: (tabId: number) => void): void;
        };
    };
    windows: {
        update(windowId: number, updateInfo: { focused: boolean }): Promise<unknown>;
    };
    setTimeout?: (fn: () => void, ms: number) => unknown;
    clearTimeout?: (handle: unknown) => void;
    now?: () => number;
    probeTimeoutMs?: number;
}

export interface OwnershipLockManager {
    request(
        name: string,
        options: { ifAvailable: true },
        callback: (lock: unknown | null) => Promise<void> | void,
    ): Promise<unknown>;
}

export interface ConnectorOwnershipApi {
    runtime: {
        sendMessage(message: unknown): Promise<unknown>;
        onMessage: {
            addListener(
                listener: (
                    message: unknown,
                    sender: unknown,
                    sendResponse: (response?: unknown) => void,
                ) => boolean | undefined,
            ): void;
        };
    };
    locks?: OwnershipLockManager | null;
    documentId?: string;
    setTimeout?: (fn: () => void, ms: number) => unknown;
    clearTimeout?: (handle: unknown) => void;
    requestTimeoutMs?: number;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isDocumentId(value: unknown): value is string {
    return typeof value === 'string' && DOCUMENT_ID_PATTERN.test(value);
}

function isTabRef(value: unknown): value is ConnectorOwnerTabRef {
    if (!isPlainObject(value)) return false;
    const { tabId, windowId } = value;
    return (
        typeof tabId === 'number' &&
        Number.isInteger(tabId) &&
        tabId >= 0 &&
        typeof windowId === 'number' &&
        Number.isInteger(windowId) &&
        windowId >= 0
    );
}

function ownerTab(owner: ConnectorOwner | undefined): ConnectorOwnerTabRef | undefined {
    return owner === undefined ? undefined : { tabId: owner.tabId, windowId: owner.windowId };
}

function senderRef(sender: OwnershipMessageSender): ConnectorOwnerTabRef | undefined {
    const tab = sender.tab;
    if (tab === undefined) return undefined;
    const { id, windowId } = tab;
    if (
        typeof id !== 'number' ||
        !Number.isInteger(id) ||
        id < 0 ||
        typeof windowId !== 'number' ||
        !Number.isInteger(windowId) ||
        windowId < 0
    ) {
        return undefined;
    }
    return { tabId: id, windowId };
}

function claimResponse(granted: boolean, owner?: ConnectorOwnerTabRef): OwnershipClaimResponse {
    return owner === undefined
        ? { type: CONNECTOR_CLAIM_RESULT_TYPE, granted }
        : { type: CONNECTOR_CLAIM_RESULT_TYPE, granted, owner };
}

function focusResponse(focused: boolean, owner?: ConnectorOwnerTabRef): OwnershipFocusResponse {
    return owner === undefined
        ? { type: CONNECTOR_FOCUS_RESULT_TYPE, focused }
        : { type: CONNECTOR_FOCUS_RESULT_TYPE, focused, owner };
}

function parseClaim(value: unknown): OwnershipClaimResponse | undefined {
    if (!isPlainObject(value) || value.type !== CONNECTOR_CLAIM_RESULT_TYPE) return undefined;
    if (typeof value.granted !== 'boolean') return undefined;
    const owner = isTabRef(value.owner) ? value.owner : undefined;
    return claimResponse(value.granted, owner);
}

function parseFocus(value: unknown): OwnershipFocusResponse | undefined {
    if (!isPlainObject(value) || value.type !== CONNECTOR_FOCUS_RESULT_TYPE) return undefined;
    if (typeof value.focused !== 'boolean') return undefined;
    const owner = isTabRef(value.owner) ? value.owner : undefined;
    return focusResponse(value.focused, owner);
}

/**
 * Service-worker side of ownership. All mutating messages are serialized so two
 * simultaneous claims cannot both observe "no owner". A claim that says it
 * holds the Web Lock is authoritative: the lock cannot be held twice, so any
 * recorded owner is necessarily stale. Without a lock, a recorded owner is
 * probed before a competing claim is allowed to replace it.
 */
export class ConnectorOwnershipCoordinator {
    private owner: ConnectorOwner | null = null;
    private queue: Promise<unknown> = Promise.resolve();

    constructor(private readonly api: OwnershipRuntimeApi) {}

    handle(message: unknown, sender: OwnershipMessageSender): Promise<unknown> {
        const run = this.queue.then(() => this.dispatch(message, sender));
        this.queue = run.then(
            () => undefined,
            () => undefined,
        );
        return run;
    }

    /** Test/launcher inspection of the currently recorded owner. */
    currentOwner(): ConnectorOwner | null {
        return this.owner === null ? null : { ...this.owner };
    }

    /** The tab lifecycle ended: drop the record so a later claim can take over. */
    releaseTab(tabId: number): void {
        if (this.owner?.tabId === tabId) this.owner = null;
    }

    private async dispatch(message: unknown, sender: OwnershipMessageSender): Promise<unknown> {
        if (!isPlainObject(message)) return undefined;
        if (
            typeof sender.id !== 'string' ||
            typeof this.api.runtime.id !== 'string' ||
            sender.id !== this.api.runtime.id
        ) {
            return undefined;
        }
        switch (message.type) {
            case CONNECTOR_CLAIM_TYPE:
                return this.claim(message, sender);
            case CONNECTOR_RELEASE_TYPE:
                return this.release(message);
            case CONNECTOR_FOCUS_TYPE:
                return this.focus();
            default:
                return undefined;
        }
    }

    private async claim(
        message: Record<string, unknown>,
        sender: OwnershipMessageSender,
    ): Promise<OwnershipClaimResponse> {
        const ref = senderRef(sender);
        if (ref === undefined || !isDocumentId(message.documentId)) {
            return claimResponse(false, ownerTab(this.owner ?? undefined));
        }
        const documentId = message.documentId;
        const holdsLock = message.holdsLock === true;
        const current = this.owner;
        if (current !== null) {
            if (current.documentId === documentId && current.tabId === ref.tabId) {
                return claimResponse(true, ownerTab(current));
            }
            // A reload creates a new document in the same tab; the old
            // document cannot still be alive in that tab.
            const sameTab = current.tabId === ref.tabId;
            if (!sameTab && holdsLock !== true && (await this.ownerIsAlive(current))) {
                return claimResponse(false, ownerTab(current));
            }
        }
        this.owner = { documentId, tabId: ref.tabId, windowId: ref.windowId };
        return claimResponse(true, ownerTab(this.owner));
    }

    private release(message: Record<string, unknown>): unknown {
        if (this.owner !== null && message.documentId === this.owner.documentId) {
            this.owner = null;
        }
        return { type: CONNECTOR_RELEASE_TYPE, released: this.owner === null };
    }

    private async focus(): Promise<OwnershipFocusResponse> {
        const current = this.owner;
        if (current === null) return focusResponse(false);
        if (!(await this.ownerIsAlive(current))) {
            this.owner = null;
            return focusResponse(false);
        }
        try {
            await this.api.windows.update(current.windowId, { focused: true });
            await this.api.tabs.update(current.tabId, { active: true });
            return focusResponse(true, ownerTab(current));
        } catch {
            return focusResponse(false, ownerTab(current));
        }
    }

    private async ownerIsAlive(owner: ConnectorOwner): Promise<boolean> {
        try {
            await this.api.tabs.get(owner.tabId);
        } catch {
            return false;
        }
        try {
            const response = await this.withTimeout(
                this.api.runtime.sendMessage({
                    type: CONNECTOR_OWNER_PROBE_TYPE,
                    documentId: owner.documentId,
                }),
                this.api.probeTimeoutMs ?? DEFAULT_PROBE_TIMEOUT_MS,
            );
            return (
                isPlainObject(response) &&
                response.type === CONNECTOR_OWNER_HERE_TYPE &&
                response.documentId === owner.documentId
            );
        } catch {
            return false;
        }
    }

    private withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
        return new Promise<T>((resolve, reject) => {
            let settled = false;
            const timer = this.setTimeout(() => {
                if (settled) return;
                settled = true;
                reject(new Error('ownership probe timed out'));
            }, ms);
            promise.then(
                (value) => {
                    if (settled) return;
                    settled = true;
                    this.clearTimeout(timer);
                    resolve(value);
                },
                (error) => {
                    if (settled) return;
                    settled = true;
                    this.clearTimeout(timer);
                    reject(error);
                },
            );
        });
    }

    private setTimeout(fn: () => void, ms: number): unknown {
        if (this.api.setTimeout !== undefined) return this.api.setTimeout(fn, ms);
        return globalThis.setTimeout(fn, ms);
    }

    private clearTimeout(handle: unknown): void {
        if (this.api.clearTimeout !== undefined) {
            this.api.clearTimeout(handle);
            return;
        }
        globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>);
    }
}

export function setupOwnership(api: OwnershipRuntimeApi): ConnectorOwnershipCoordinator {
    const coordinator = new ConnectorOwnershipCoordinator(api);
    api.runtime.onMessage.addListener((message, sender, sendResponse) => {
        if (!isPlainObject(message)) return false;
        const type = message.type;
        if (
            type !== CONNECTOR_CLAIM_TYPE &&
            type !== CONNECTOR_RELEASE_TYPE &&
            type !== CONNECTOR_FOCUS_TYPE
        ) {
            return false;
        }
        void coordinator.handle(message, sender).then(
            (response) => sendResponse(response),
            () => sendResponse(undefined),
        );
        return true;
    });
    api.tabs.onRemoved?.addListener((tabId) => {
        coordinator.releaseTab(tabId);
    });
    return coordinator;
}

let fallbackDocumentCounter = 0;

function defaultDocumentId(): string {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }
    fallbackDocumentCounter += 1;
    return `doc-${Date.now().toString(36)}-${fallbackDocumentCounter.toString(36)}-${Math.random()
        .toString(36)
        .slice(2, 10)}`;
}

function navigatorLocks(): OwnershipLockManager | null {
    if (typeof navigator === 'undefined') return null;
    const locks = (navigator as Navigator & { locks?: OwnershipLockManager }).locks;
    return locks === undefined ? null : locks;
}

/**
 * Connector-page side of ownership. `claim()` must resolve with `owner: true`
 * before the caller constructs a controller or opens a Native Messaging port.
 */
export class ConnectorOwnership {
    private readonly api: ConnectorOwnershipApi;
    private readonly documentId: string;
    private readonly lockManager: OwnershipLockManager | null;
    private owned = false;
    private released = false;
    private releaseLock: (() => void) | null = null;

    constructor(api: ConnectorOwnershipApi) {
        this.api = api;
        this.documentId = api.documentId ?? defaultDocumentId();
        this.lockManager = api.locks === undefined ? navigatorLocks() : api.locks;
        this.installOwnerProbeResponder();
    }

    async claim(): Promise<OwnershipClaimOutcome> {
        if (this.released) return { owner: false, reason: 'claim-failed' };
        const lock = await this.acquireLock();
        if (lock === 'unavailable') {
            const ownerTab = await this.focusOwner();
            return { owner: false, reason: 'another-document', ownerTab };
        }

        let response: OwnershipClaimResponse | undefined;
        try {
            response = parseClaim(
                await this.withTimeout(
                    this.api.runtime.sendMessage({
                        type: CONNECTOR_CLAIM_TYPE,
                        documentId: this.documentId,
                        holdsLock: lock === 'held',
                    }),
                    this.api.requestTimeoutMs ?? DEFAULT_CLAIM_TIMEOUT_MS,
                ),
            );
        } catch {
            response = undefined;
        }

        // A held Web Lock is authoritative mutual exclusion. It cannot be taken
        // by another live connector document, so a missing/failed service
        // worker answer must not strand the extension without a host.
        if (lock === 'held') {
            this.owned = true;
            return { owner: true };
        }
        if (response?.granted === true) {
            this.owned = true;
            return { owner: true };
        }
        if (response === undefined) {
            return { owner: false, reason: 'claim-failed' };
        }
        return {
            owner: false,
            reason: response.owner === undefined ? 'unauthorized' : 'another-document',
            ...(response.owner === undefined ? {} : { ownerTab: response.owner }),
        };
    }

    async focusOwner(): Promise<ConnectorOwnerTabRef | undefined> {
        try {
            const response = parseFocus(
                await this.withTimeout(
                    this.api.runtime.sendMessage({ type: CONNECTOR_FOCUS_TYPE }),
                    this.api.requestTimeoutMs ?? DEFAULT_CLAIM_TIMEOUT_MS,
                ),
            );
            return response?.owner;
        } catch {
            return undefined;
        }
    }

    async release(): Promise<void> {
        if (this.released) return;
        this.released = true;
        const wasOwned = this.owned;
        this.owned = false;
        if (wasOwned) {
            try {
                await this.api.runtime.sendMessage({
                    type: CONNECTOR_RELEASE_TYPE,
                    documentId: this.documentId,
                });
            } catch {
                // A terminated page need not complete this message; the Web
                // Lock is released by the browser and a later claim replaces
                // the stale record.
            }
        }
        this.releaseLock?.();
        this.releaseLock = null;
    }

    isOwner(): boolean {
        return this.owned;
    }

    private async acquireLock(): Promise<'held' | 'unavailable' | 'unsupported'> {
        const locks = this.lockManager;
        if (locks === null) return 'unsupported';
        let settle: (state: 'held' | 'unavailable') => void = () => undefined;
        const acquired = new Promise<'held' | 'unavailable'>((resolve) => {
            settle = resolve;
        });
        const hold = new Promise<void>((resolve) => {
            this.releaseLock = resolve;
        });
        try {
            void locks
                .request(CONNECTOR_OWNER_LOCK, { ifAvailable: true }, (lock) => {
                    if (lock === null) {
                        settle('unavailable');
                        return;
                    }
                    settle('held');
                    return hold;
                })
                .catch(() => settle('unavailable'));
        } catch {
            settle('unavailable');
        }
        return acquired;
    }

    private installOwnerProbeResponder(): void {
        this.api.runtime.onMessage.addListener((message, _sender, sendResponse) => {
            if (
                isPlainObject(message) &&
                message.type === CONNECTOR_OWNER_PROBE_TYPE &&
                message.documentId === this.documentId
            ) {
                sendResponse({
                    type: CONNECTOR_OWNER_HERE_TYPE,
                    documentId: this.documentId,
                });
                return false;
            }
            return false;
        });
    }

    private withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
        return new Promise<T>((resolve, reject) => {
            let settled = false;
            const timer = this.setTimeout(() => {
                if (settled) return;
                settled = true;
                reject(new Error('ownership message timed out'));
            }, ms);
            promise.then(
                (value) => {
                    if (settled) return;
                    settled = true;
                    this.clearTimeout(timer);
                    resolve(value);
                },
                (error) => {
                    if (settled) return;
                    settled = true;
                    this.clearTimeout(timer);
                    reject(error);
                },
            );
        });
    }

    private setTimeout(fn: () => void, ms: number): unknown {
        if (this.api.setTimeout !== undefined) return this.api.setTimeout(fn, ms);
        return globalThis.setTimeout(fn, ms);
    }

    private clearTimeout(handle: unknown): void {
        if (this.api.clearTimeout !== undefined) {
            this.api.clearTimeout(handle);
            return;
        }
        globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>);
    }
}
