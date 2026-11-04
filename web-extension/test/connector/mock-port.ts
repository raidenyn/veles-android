import type { NativePortLike } from '../../src/connector/port';

/**
 * Asynchronous Native Messaging port double.
 *
 * `nextPosted()` resolves only after the connector has actually posted a
 * message, so tests never read a request id before the post occurred. Inbound
 * host events and disconnect callbacks are delivered through a promise chain;
 * `drain()` waits for everything queued so far. `lastError` exists only while
 * the disconnect callback runs, matching Chrome's callback-scoped runtime
 * error.
 */
export class MockNativePort implements NativePortLike {
    readonly posted: unknown[] = [];
    lastError?: { message?: string };

    private readonly messageListeners: ((message: unknown) => void)[] = [];
    private readonly disconnectListeners: (() => void)[] = [];
    private readonly postedWaiters: ((message: Record<string, unknown>) => void)[] = [];
    private delivered = 0;
    private delivery: Promise<void> = Promise.resolve();

    readonly onMessage = {
        addListener: (listener: (message: unknown) => void): void => {
            this.messageListeners.push(listener);
        },
    };

    readonly onDisconnect = {
        addListener: (listener: () => void): void => {
            this.disconnectListeners.push(listener);
        },
    };

    postMessage(message: unknown): void {
        this.posted.push(message);
        this.flushPostedWaiters();
    }

    nextPosted(): Promise<Record<string, unknown>> {
        if (this.delivered < this.posted.length) {
            const message = this.posted[this.delivered];
            this.delivered += 1;
            return Promise.resolve(message as Record<string, unknown>);
        }
        return new Promise((resolve) => {
            this.postedWaiters.push(resolve);
        });
    }

    emit(value: unknown): Promise<void> {
        this.delivery = this.delivery.then(() => {
            for (const listener of [...this.messageListeners]) listener(value);
        });
        return this.delivery;
    }

    disconnect(lastErrorMessage?: string): Promise<void> {
        this.delivery = this.delivery.then(() => {
            this.lastError =
                lastErrorMessage === undefined ? undefined : { message: lastErrorMessage };
            try {
                for (const listener of [...this.disconnectListeners]) listener();
            } finally {
                this.lastError = undefined;
            }
        });
        return this.delivery;
    }

    drain(): Promise<void> {
        return this.delivery;
    }

    private flushPostedWaiters(): void {
        while (this.postedWaiters.length > 0 && this.delivered < this.posted.length) {
            const waiter = this.postedWaiters.shift();
            if (waiter === undefined) return;
            const message = this.posted[this.delivered];
            this.delivered += 1;
            waiter(message as Record<string, unknown>);
        }
    }
}
