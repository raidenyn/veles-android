import type { ChromeTrustRepository } from '../trust/repository';

const DEBUG_LABEL = 'Veles debug peer';
/** Any 32-byte hex transcript is acceptable for a debug record; nothing verifies it. */
const DEBUG_TRANSCRIPT = 'ab'.repeat(32);

/**
 * Development-only trust injection (design D5). Installs one ordinary trust record in the
 * `active` state, bypassing the activation proofs, through the production repository's own
 * transaction, bounds and fingerprint. The unchanged handshake authenticates against it.
 *
 * Reachable only from the development probe's `globalThis.velesDebug`; the repository passed
 * here must be built with an accept-all verifier AND the same durable identity store, so it is
 * never the connector's own repository.
 */
export async function injectActivePeer(
    repository: ChromeTrustRepository,
    installationId: string,
    publicKey: string,
): Promise<void> {
    const existing = (await repository.peers()).find(
        (peer) => peer.installationId === installationId,
    );
    if (existing !== undefined) await repository.revoke(installationId);
    await repository.addPending({
        installationId,
        publicKey,
        label: DEBUG_LABEL,
        deviceType: 0,
        enrollmentTranscript: DEBUG_TRANSCRIPT,
    });
    await repository.confirm(installationId, 'complete', new Uint8Array(0));
    await repository.confirm(installationId, 'phone_active', new Uint8Array(0));
}
