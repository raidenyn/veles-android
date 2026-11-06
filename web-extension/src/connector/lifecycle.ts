import type { NativePortLike } from './port';

// Additional bounded queue at the asynchronous worker-generation boundary.
// No callback can enter the controller before the worker accepts its generation.
export function gateNativePort(
    raw: NativePortLike,
    authorize: () => Promise<boolean>,
    lastError: () => { message?: string } | undefined = () => undefined,
): NativePortLike {
    const listeners: ((message: unknown) => void)[] = [];
    const losses: ((error?: { message?: string }) => void)[] = [];
    const queue: { value: unknown; bytes: number; outgoing: boolean }[] = [];
    let bytes = 0;
    let closed = false;
    let pumping = false;
    let transportEnded = false;
    let capturedError: { message?: string } | undefined;
    const close = (notify: boolean, disconnect: boolean): void => {
        if (closed) return;
        closed = true;
        queue.length = 0;
        bytes = 0;
        if (disconnect && !transportEnded) raw.disconnect();
        if (notify) for (const listener of losses) listener(capturedError);
    };
    const pump = async (): Promise<void> => {
        if (pumping || closed) return;
        pumping = true;
        try {
            while (queue.length && !closed) {
                if (!(await authorize())) {
                    close(true, true);
                    break;
                }
                if (closed) break;
                const item = queue.shift();
                if (item === undefined) break;
                bytes -= item.bytes;
                if (item.outgoing) {
                    if (!transportEnded) raw.postMessage(item.value);
                } else for (const listener of listeners) listener(item.value);
            }
            if (transportEnded && !closed) close(true, false);
        } catch {
            close(true, true);
        } finally {
            pumping = false;
        }
    };
    const enqueue = (value: unknown, outgoing: boolean): void => {
        if (closed) return;
        let size: number;
        try {
            size = new TextEncoder().encode(JSON.stringify(value)).length;
        } catch {
            close(true, true);
            return;
        }
        if (queue.length >= 128 || bytes + size > 262144) {
            close(true, true);
            return;
        }
        queue.push({ value, bytes: size, outgoing });
        bytes += size;
        void pump();
    };
    raw.onMessage.addListener((value) => enqueue(value, false));
    // Snapshot lastError during the raw callback; deliver EOF after preceding
    // native messages. Explicit generation invalidation still drops immediately.
    raw.onDisconnect.addListener(() => {
        const error = lastError();
        capturedError = error === undefined ? undefined : { message: error.message };
        transportEnded = true;
        for (let index = queue.length - 1; index >= 0; index--) {
            if (queue[index].outgoing) {
                bytes -= queue[index].bytes;
                queue.splice(index, 1);
            }
        }
        if (!queue.length) close(true, false);
        else void pump();
    });
    return {
        postMessage(message) {
            if (!closed) enqueue(message, true);
            else throw new Error('closed generation');
        },
        disconnect() {
            close(false, true);
        },
        cancelRequest(requestId) {
            for (let index = queue.length - 1; index >= 0; index--) {
                const item = queue[index];
                if (
                    item.outgoing &&
                    typeof item.value === 'object' &&
                    item.value !== null &&
                    (item.value as { request_id?: unknown }).request_id === requestId
                ) {
                    bytes -= item.bytes;
                    queue.splice(index, 1);
                }
            }
        },
        onMessage: {
            addListener(listener) {
                listeners.push(listener);
            },
        },
        onDisconnect: {
            addListener(listener) {
                losses.push(listener);
            },
        },
    };
}
