// Worker authority for ephemeral connector generations. No payloads are stored.
export interface LifecycleSender {
    id?: string;
    url?: string;
    documentId?: string;
    tab?: { id?: number };
}

export interface LifecycleApi {
    id: string;
    getURL(path: string): string;
    storage: {
        get(key: string): Promise<Record<string, unknown>>;
        set(items: Record<string, unknown>): Promise<void>;
    };
    getTab(id: number): Promise<{ frozen?: boolean; discarded?: boolean }>;
    badge(tabId: number, text: string): Promise<void>;
    token(): string;
}

export class LifecycleCoordinator {
    private queue: Promise<unknown> = Promise.resolve();
    private epochs = new Map<number, number>();
    private revoked = new Set<number>();

    constructor(private readonly api: LifecycleApi) {}

    private tabId(sender: LifecycleSender): number | null {
        const id = sender.tab?.id;
        return sender.id === this.api.id &&
            sender.url === this.api.getURL('connector.html') &&
            typeof sender.documentId === 'string' &&
            sender.documentId.length > 0 &&
            typeof id === 'number' &&
            Number.isInteger(id) &&
            id >= 0
            ? id
            : null;
    }

    private key(id: number): string {
        return `veles.lifecycle.${id}`;
    }
    private epoch(id: number): number {
        return this.epochs.get(id) ?? 0;
    }
    private serialize<T>(operation: () => Promise<T>): Promise<T> {
        const next = this.queue.then(operation);
        this.queue = next.catch(() => undefined);
        return next;
    }

    activate(sender: LifecycleSender): Promise<string | null> {
        const id = this.tabId(sender);
        if (id === null) return Promise.resolve(null);
        const epoch = this.epoch(id) + 1;
        this.epochs.set(id, epoch);
        this.revoked.add(id);
        return this.serialize(async () => {
            try {
                const tab = await this.api.getTab(id);
                if (tab.frozen || tab.discarded || epoch !== this.epoch(id)) return null;
                const token = this.api.token();
                await this.api.storage.set({
                    [this.key(id)]: { token, documentId: sender.documentId },
                });
                if (epoch !== this.epoch(id)) return null;
                this.revoked.delete(id);
                await this.api.badge(id, '');
                return epoch === this.epoch(id) ? token : null;
            } catch {
                return null;
            }
        });
    }

    async valid(token: unknown, sender: LifecycleSender): Promise<boolean> {
        const id = this.tabId(sender);
        if (id === null || typeof token !== 'string' || this.revoked.has(id)) return false;
        const epoch = this.epoch(id);
        try {
            const stored = (await this.api.storage.get(this.key(id)))[this.key(id)];
            const tab = await this.api.getTab(id);
            if (tab.frozen || tab.discarded || epoch !== this.epoch(id)) return false;
            if (typeof stored !== 'object' || stored === null) return false;
            const record = stored as Record<string, unknown>;
            return record.token === token && record.documentId === sender.documentId;
        } catch {
            return false;
        }
    }

    invalidate(id: number): Promise<void> {
        // Revocation is synchronous, before any outstanding browser/storage await.
        this.epochs.set(id, this.epoch(id) + 1);
        this.revoked.add(id);
        // Do not wait for a stale queued status/storage operation to finish.
        void this.api.badge(id, '').catch(() => undefined);
        return this.serialize(async () => {
            await this.api.storage.set({ [this.key(id)]: null });
            await this.api.badge(id, '');
        });
    }

    end(token: unknown, sender: LifecycleSender): Promise<boolean> {
        // End is conditional under the activation queue, never a check followed
        // by unconditional revocation of a possibly replaced document.
        return this.serialize(async () => {
            const id = this.tabId(sender);
            if (id === null || !(await this.valid(token, sender))) return false;
            this.epochs.set(id, this.epoch(id) + 1);
            this.revoked.add(id);
            await this.api.storage.set({ [this.key(id)]: null });
            await this.api.badge(id, '');
            return true;
        });
    }

    status(token: unknown, sender: LifecycleSender, connected: boolean): Promise<boolean> {
        return this.serialize(async () => {
            const id = this.tabId(sender);
            if (id === null) return false;
            if (!(await this.valid(token, sender))) return false;
            await this.api.badge(id, connected ? 'ON' : '');
            return true;
        });
    }
}
