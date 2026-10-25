/** Liveness only, never authentication. A document's Web Lock vanishes on context loss. */
export interface PendingOwner {
    readonly id: string;
    isAlive(id: string): Promise<boolean>;
}

let owner: PendingOwner | undefined;

/** Shared by repository instances in ONE connector document, not storage.session. */
export function browserPendingOwner(): PendingOwner {
    if (owner) return owner;
    const id = crypto.randomUUID();
    const name = `veles.pending-owner.${id}`;
    let acquired: () => void;
    let failed: (reason: unknown) => void;
    const ready = new Promise<void>((resolve, reject) => {
        acquired = resolve;
        failed = reject;
    });
    void navigator.locks
        .request(name, async () => {
            acquired();
            // Intentionally held for the document's lifetime. Browser teardown releases it.
            await new Promise<void>(() => undefined);
        })
        .catch((error: unknown) => failed(error));
    owner = {
        id,
        async isAlive(candidate) {
            await ready;
            const locks = await navigator.locks.query();
            return (
                locks.held?.some((lock) => lock.name === `veles.pending-owner.${candidate}`) ??
                false
            );
        },
    };
    return owner;
}
