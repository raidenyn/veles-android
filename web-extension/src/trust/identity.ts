import {
    bytes,
    exactFields,
    hex,
    importPublicKey,
    requireTrust,
    TrustUnavailableError,
    type InstallationIdentity,
} from './model';

export interface StoredIdentity extends InstallationIdentity {
    privateKey: CryptoKey;
}

export interface IdentityStore {
    read(): Promise<StoredIdentity | undefined>;
    create(): Promise<StoredIdentity>;
    clear(): Promise<void>;
}

export async function validateIdentity(value: unknown): Promise<StoredIdentity> {
    exactFields(value, ['installationId', 'publicKey', 'privateKey']);
    bytes(value.installationId as string, 16);
    const key = value.privateKey as CryptoKey;
    requireTrust(key instanceof CryptoKey && key.type === 'private' && !key.extractable);
    requireTrust(
        key.algorithm.name === 'ECDSA' && (key.algorithm as EcKeyAlgorithm).namedCurve === 'P-256',
    );
    requireTrust(key.usages.length === 1 && key.usages[0] === 'sign');
    const publicKey = await importPublicKey(value.publicKey as string);
    const challenge = crypto.getRandomValues(new Uint8Array(32));
    const signature = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, key, challenge);
    requireTrust(
        await crypto.subtle.verify(
            { name: 'ECDSA', hash: 'SHA-256' },
            publicKey,
            signature,
            challenge,
        ),
    );
    return value as unknown as StoredIdentity;
}

/** CryptoKeys use IndexedDB structured cloning; no private key serialization/export. */
export class IndexedDbIdentityStore implements IdentityStore {
    constructor(
        private readonly factory: IDBFactory = indexedDB,
        private readonly name = 'veles-installation-v1',
    ) {}

    private open(): Promise<IDBDatabase> {
        return new Promise((resolve, reject) => {
            const request = this.factory.open(this.name, 1);
            request.onupgradeneeded = () => request.result.createObjectStore('identity');
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(new TrustUnavailableError());
            request.onblocked = () => reject(new TrustUnavailableError());
        });
    }

    private async transaction<T>(
        mode: IDBTransactionMode,
        action: (store: IDBObjectStore) => IDBRequest<T>,
    ): Promise<T> {
        const db = await this.open();
        try {
            return await new Promise<T>((resolve, reject) => {
                const tx = db.transaction('identity', mode);
                const request = action(tx.objectStore('identity'));
                tx.oncomplete = () => resolve(request.result);
                tx.onabort = tx.onerror = () => reject(new TrustUnavailableError());
            });
        } finally {
            db.close();
        }
    }

    async read(): Promise<StoredIdentity | undefined> {
        const value: unknown = await this.transaction('readonly', (store) =>
            store.get('installation'),
        );
        return value === undefined ? undefined : validateIdentity(value);
    }

    async create(): Promise<StoredIdentity> {
        const pair = await crypto.subtle.generateKey(
            { name: 'ECDSA', namedCurve: 'P-256' },
            false,
            ['sign', 'verify'],
        );
        const value: StoredIdentity = {
            installationId: hex(crypto.getRandomValues(new Uint8Array(16))),
            publicKey: hex(new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey))),
            privateKey: pair.privateKey,
        };
        // add (not put) makes concurrent initialization fail rather than replace identity.
        await this.transaction('readwrite', (store) => store.add(value, 'installation'));
        return value;
    }

    async clear(): Promise<void> {
        await this.transaction('readwrite', (store) => store.clear());
    }
}
