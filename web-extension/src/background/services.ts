import { LifecycleCoordinator } from './lifecycle';
import { ClipboardCoordinator } from './clipboard';

export function setupBackgroundServices(api: typeof chrome): void {
    const lifecycle = new LifecycleCoordinator({
        id: api.runtime.id,
        getURL: (path) => api.runtime.getURL(path),
        storage: api.storage.session,
        getTab: (id) => api.tabs.get(id),
        badge: (tabId, text) => api.action.setBadgeText({ tabId, text }),
        token: () => crypto.randomUUID(),
    });
    const admissions = new Map<string, () => Promise<boolean>>();
    const clipboard = new ClipboardCoordinator({
        authorize: (token, sender) => lifecycle.valid(token, sender),
        create: async () => {
            // Recover a helper left by a terminated worker; no stale payload is reused.
            if (await api.offscreen.hasDocument()) await api.offscreen.closeDocument();
            await api.offscreen.createDocument({
                url: 'offscreen.html',
                reasons: [api.offscreen.Reason.CLIPBOARD],
                justification: 'Copy one explicitly requested synthetic test code',
            });
        },
        write: async (code, admit) => {
            const operationId = crypto.randomUUID();
            admissions.set(operationId, admit);
            try {
                const result: unknown = await api.runtime.sendMessage({
                    type: 'veles.clipboard.write',
                    target: 'offscreen',
                    code,
                    operationId,
                });
                return (
                    typeof result === 'object' &&
                    result !== null &&
                    Object.keys(result).length === 2 &&
                    (result as { ok?: unknown }).ok === true &&
                    (result as { operationId?: unknown }).operationId === operationId
                );
            } finally {
                admissions.delete(operationId);
            }
        },
        close: () => api.offscreen.closeDocument(),
    });
    api.tabs.onUpdated.addListener((id, change) => {
        if (change.frozen === true || change.discarded === true)
            void lifecycle.invalidate(id).catch(() => undefined);
    });
    api.tabs.onRemoved.addListener((id) => {
        void lifecycle.invalidate(id).catch(() => undefined);
    });
    api.tabs.onReplaced.addListener((_addedTabId, removedTabId) => {
        void lifecycle.invalidate(removedTabId).catch(() => undefined);
    });
    api.runtime.onMessage.addListener((message: unknown, sender, respond) => {
        if (typeof message !== 'object' || message === null || Array.isArray(message)) return false;
        const value = message as Record<string, unknown>;
        const fields: Record<string, string> = {
            'veles.lifecycle.activate': 'type',
            'veles.lifecycle.check': 'token,type',
            'veles.lifecycle.status': 'connected,token,type',
            'veles.lifecycle.end': 'token,type',
            'veles.clipboard.copy': 'code,token,type',
            'veles.clipboard.commit': 'operationId,type',
        };
        if (typeof value.type !== 'string' || !Object.hasOwn(fields, value.type)) return false;
        if (Object.keys(value).sort().join(',') !== fields[value.type]) {
            respond({ ok: false });
            return false;
        }
        const handle = async (): Promise<unknown> => {
            switch (value.type) {
                case 'veles.clipboard.commit': {
                    if (
                        sender.id !== api.runtime.id ||
                        sender.tab !== undefined ||
                        sender.url !== api.runtime.getURL('offscreen.html') ||
                        typeof value.operationId !== 'string'
                    )
                        return { ok: false };
                    const admit = admissions.get(value.operationId);
                    admissions.delete(value.operationId);
                    return { ok: admit !== undefined && (await admit()) };
                }
                case 'veles.lifecycle.activate':
                    return { token: await lifecycle.activate(sender) };
                case 'veles.lifecycle.check':
                    return { ok: await lifecycle.valid(value.token, sender) };
                case 'veles.lifecycle.status':
                    return {
                        ok:
                            typeof value.connected === 'boolean' &&
                            (await lifecycle.status(value.token, sender, value.connected)),
                    };
                case 'veles.lifecycle.end': {
                    return { ok: await lifecycle.end(value.token, sender) };
                }
                case 'veles.clipboard.copy':
                    return clipboard.copy(value.token, value.code, sender);
            }
        };
        void handle().then(respond, () => respond({ ok: false }));
        return true;
    });
}
