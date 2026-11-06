import { copySyntheticCode } from './background/clipboard';

export function setupOffscreen(runtime: typeof chrome.runtime, doc: Document): void {
    runtime.onMessage.addListener((message: unknown, sender, respond) => {
        if (
            sender.id !== runtime.id ||
            sender.tab !== undefined ||
            sender.url !== runtime.getURL('background.js')
        )
            return false;
        if (typeof message !== 'object' || message === null || Array.isArray(message)) return false;
        const value = message as Record<string, unknown>;
        if (
            Object.keys(value).sort().join(',') !== 'code,operationId,target,type' ||
            typeof value.operationId !== 'string' ||
            !/^[a-f0-9-]{36}$/.test(value.operationId) ||
            value.target !== 'offscreen' ||
            value.type !== 'veles.clipboard.write'
        )
            return false;
        // The worker linearizes admission here, at execution rather than dispatch.
        // Revoked operations waiting in runtime messaging never reach the DOM copy.
        void runtime
            .sendMessage({ type: 'veles.clipboard.commit', operationId: value.operationId })
            .then(
                (result: unknown) => {
                    const admitted =
                        typeof result === 'object' &&
                        result !== null &&
                        (result as { ok?: unknown }).ok === true;
                    respond({
                        ok: admitted && copySyntheticCode(value.code, doc),
                        operationId: value.operationId,
                    });
                },
                () => respond({ ok: false, operationId: value.operationId }),
            );
        return true;
    });
}

if (typeof chrome !== 'undefined' && typeof document !== 'undefined')
    setupOffscreen(chrome.runtime, document);
