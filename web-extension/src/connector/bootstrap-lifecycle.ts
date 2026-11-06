// Install before the first bootstrap await, even before a controller exists.
export class BootstrapLifecycle {
    ended = false;
    private cleanups: (() => void)[] = [];
    constructor(listen: (name: string, callback: () => void) => void, reload: () => void) {
        for (const name of ['freeze', 'pagehide', 'beforeunload']) listen(name, () => this.stop());
        listen('resume', () => {
            if (this.ended) reload();
        });
    }
    cleanup(callback: () => void): void {
        if (this.ended) callback();
        else this.cleanups.push(callback);
    }
    stop(): void {
        if (this.ended) return;
        this.ended = true;
        for (const callback of this.cleanups.splice(0)) callback();
    }
}

export async function activateGeneration(
    send: () => Promise<unknown>,
    release: () => Promise<void>,
): Promise<string | null> {
    try {
        const response = await send();
        if (
            typeof response === 'object' &&
            response !== null &&
            typeof (response as { token?: unknown }).token === 'string'
        ) {
            return (response as { token: string }).token;
        }
    } catch {
        /* Failed worker messaging is an ordinary startup failure. */
    }
    await release();
    return null;
}
