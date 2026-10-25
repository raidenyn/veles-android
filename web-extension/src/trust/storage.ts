import type { TrustSnapshot } from './model';

export const TRUST_STORAGE_KEY = 'veles.trust.v1';
export interface LocalStore {
    restrict(): Promise<void>;
    read(): Promise<unknown>;
    write(snapshot: TrustSnapshot): Promise<void>;
    clear(): Promise<void>;
}

/** No sync/session storage and no runtime message bridge to untrusted contexts. */
export class ChromeLocalStore implements LocalStore {
    async restrict(): Promise<void> {
        await chrome.storage.local.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' });
    }
    async read(): Promise<unknown> {
        return (await chrome.storage.local.get(TRUST_STORAGE_KEY))[TRUST_STORAGE_KEY];
    }
    async write(snapshot: TrustSnapshot): Promise<void> {
        await chrome.storage.local.set({ [TRUST_STORAGE_KEY]: snapshot });
    }
    async clear(): Promise<void> {
        await chrome.storage.local.remove(TRUST_STORAGE_KEY);
    }
}

export type ExclusiveLock = <T>(operation: () => Promise<T>) => Promise<T>;
export const trustLock: ExclusiveLock = (operation) =>
    navigator.locks.request('veles.trust.v1', operation);
