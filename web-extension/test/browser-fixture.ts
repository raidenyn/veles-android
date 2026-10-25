// Test-only extension module, bundled into an ephemeral directory by test-browser.mjs.
// Never an input of the production Vite build or package.
import { ChromeTrustRepository } from '../src/trust/repository';
import { IndexedDbIdentityStore } from '../src/trust/identity';
import { hex, type TrustSnapshot } from '../src/trust/model';
import { TRUST_STORAGE_KEY } from '../src/trust/storage';

const repo = new ChromeTrustRepository(
    undefined,
    undefined,
    undefined,
    async (_peer, _stage, proof) => proof[0] === 1,
);
export const identity = () => repo.identity();
export const peers = () => repo.peers();
export const reset = () => repo.reset();
export async function addPending() {
    const pair = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, false, [
        'sign',
        'verify',
    ]);
    const peer = {
        installationId: hex(crypto.getRandomValues(new Uint8Array(16))),
        publicKey: hex(new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey))),
        label: 'Browser fixture phone',
        deviceType: 0,
        enrollmentTranscript: 'ab'.repeat(32),
    };
    await repo.addPending(peer);
    return peer.installationId;
}
export async function complete(id: string) {
    await repo.confirm(id, 'complete', new Uint8Array([1]));
}
export async function activate(id: string) {
    await repo.confirm(id, 'phone_active', new Uint8Array([1]));
}
export async function exportPrivate() {
    const key = await new IndexedDbIdentityStore().read();
    try {
        if (!key) throw new Error('Missing fixture identity');
        await crypto.subtle.exportKey('pkcs8', key.privateKey);
        return true;
    } catch {
        return false;
    }
}
export async function loseKey() {
    await new IndexedDbIdentityStore().clear();
}
export async function corrupt() {
    const saved = (await chrome.storage.local.get(TRUST_STORAGE_KEY))[
        TRUST_STORAGE_KEY
    ] as TrustSnapshot;
    saved.peers[0].fingerprint = '00'.repeat(32);
    await chrome.storage.local.set({ [TRUST_STORAGE_KEY]: saved });
}
