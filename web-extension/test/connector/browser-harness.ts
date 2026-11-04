// Test-only packaged-browser harness.
//
// The built extension page is loaded in Chromium. This module runs before the
// production connector bundle in the ephemeral test copy and replaces only
// `chrome.runtime.connectNative` with an in-page simulated Native Port. The
// production bundle still performs the real bootstrap, controller, protocol,
// state and DOM rendering; nothing here is part of `dist/` or the package.
import type { NativePortLike } from '../../src/connector/port';

type MessageListener = (message: unknown) => void;
type DisconnectListener = () => void;

class SimulatedNativePort implements NativePortLike {
    readonly posted: unknown[] = [];

    private readonly messageListeners: MessageListener[] = [];
    private readonly disconnectListeners: DisconnectListener[] = [];
    private readonly postedWaiters: ((message: Record<string, unknown>) => void)[] = [];
    private delivered = 0;
    private delivery: Promise<void> = Promise.resolve();

    readonly onMessage = {
        addListener: (listener: MessageListener): void => {
            this.messageListeners.push(listener);
        },
    };

    readonly onDisconnect = {
        addListener: (listener: DisconnectListener): void => {
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

    disconnect(): Promise<void> {
        this.delivery = this.delivery.then(() => {
            for (const listener of [...this.disconnectListeners]) listener();
        });
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

const ports: SimulatedNativePort[] = [];
let active: SimulatedNativePort | null = null;

function connectSimulated(): NativePortLike {
    active = new SimulatedNativePort();
    ports.push(active);
    return active;
}

const chromeApi = (
    globalThis as unknown as {
        chrome?: { runtime?: { connectNative?: unknown } };
    }
).chrome;
if (chromeApi?.runtime === undefined || typeof chromeApi.runtime.connectNative !== 'function') {
    throw new Error('browser harness requires a Chrome extension runtime');
}
Object.defineProperty(chromeApi.runtime, 'connectNative', {
    configurable: true,
    writable: true,
    value: connectSimulated,
});

function requirePort(): SimulatedNativePort {
    if (active === null) throw new Error('the connector has not opened a simulated port');
    return active;
}

Object.defineProperty(globalThis, '__velesConnectorHarness', {
    configurable: true,
    value: {
        portCount: (): number => ports.length,
        posted: (): unknown[] => requirePort().posted.slice(),
        nextPosted: (): Promise<Record<string, unknown>> => requirePort().nextPosted(),
        emit: (value: unknown): Promise<void> => requirePort().emit(value),
        disconnect: (): Promise<void> => requirePort().disconnect(),
    },
});
