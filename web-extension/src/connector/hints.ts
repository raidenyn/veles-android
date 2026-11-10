// Trust-scoped connection hints.
//
// A hint is an opaque OS search candidate, never identity and never proof.
// The only production source is the existing ChromeTrustRepository read path:
// `activePeers()` enforces the storage restriction and validates every peer
// record before returning it. This module deliberately exposes only that read;
// it never calls `authenticated()`, constructs proof bytes, or persists a
// connected hint. The connector's session manager is the only writer, and it
// calls the same repository only after the authentication gate opens. The
// repository's own read path may perform maintenance writes (identity
// initialization or pending pruning) while enforcing its
// restriction/validation; that is not a connector hint write.
//
// The trust model stores one opaque `platformHint` per peer and has no OS
// platform field, so a hint can be scoped to a peer but not to Windows/macOS.
// Scoping by platform would require a contract change and is not invented here.

import { ChromeTrustRepository } from '../trust/repository';

export interface PeerHint {
    peerId: string;
    label: string;
    hint: string;
}

export interface TrustHintSource {
    activePeers(): Promise<readonly PeerHint[]>;
}

export type ReconnectTarget =
    | { kind: 'hint'; peerId: string; hint: string }
    | { kind: 'search'; reason: 'none' | 'ambiguous' | 'invalid' };

/** Structural subset of the trust repository used by the read-only adapter. */
export interface TrustPeerRecord {
    installationId: string;
    label: string;
    platformHint?: string;
}

export interface TrustRepositoryReader {
    activePeers(): Promise<readonly TrustPeerRecord[]>;
}

const HINT_MAX_BYTES = 256;
const PEER_ID_MAX_CHARS = 128;
const utf8 = new TextEncoder();

function isBoundedText(value: unknown, maxBytes: number): value is string {
    if (typeof value !== 'string' || value.length === 0 || value.length > maxBytes) return false;
    const encoded = utf8.encode(value);
    if (encoded.length < 1 || encoded.length > maxBytes) return false;
    for (const character of value) {
        const code = character.codePointAt(0) ?? 0;
        if (code <= 0x1f || code === 0x7f) return false;
    }
    return true;
}

function isPeerId(value: unknown): value is string {
    if (typeof value !== 'string' || value.length === 0 || value.length > PEER_ID_MAX_CHARS) {
        return false;
    }
    for (const character of value) {
        const code = character.codePointAt(0) ?? 0;
        if (code <= 0x1f || code === 0x7f) return false;
    }
    return true;
}

function displayLabel(value: unknown): string {
    if (typeof value !== 'string') return 'Phone';
    const trimmed = value.trim();
    if (trimmed.length === 0) return 'Phone';
    return trimmed.length <= 128 ? trimmed : trimmed.slice(0, 128);
}

/**
 * Keep only well-formed, peer-scoped hints. Validation is repeated here even
 * though the repository already validates persisted records: hints are also
 * accepted from an injected source in tests and must never reach the UI or a
 * `connect` request in an invalid shape.
 */
export function hintsFromPeers(peers: readonly PeerHint[]): PeerHint[] {
    const result: PeerHint[] = [];
    for (const peer of peers) {
        if (!isPeerId(peer.peerId)) continue;
        if (!isBoundedText(peer.hint, HINT_MAX_BYTES)) continue;
        result.push({
            peerId: peer.peerId,
            label: displayLabel(peer.label),
            hint: peer.hint,
        });
    }
    return result;
}

/**
 * Pick a single unambiguous peer hint. More than one candidate is reported as
 * ambiguous even when the hint strings happen to be equal: a hint must never
 * silently select one peer identity over another.
 */
export function chooseReconnectTarget(peers: readonly PeerHint[]): ReconnectTarget {
    const valid = hintsFromPeers(peers);
    if (valid.length === 1) {
        const peer = valid[0] as PeerHint;
        return { kind: 'hint', peerId: peer.peerId, hint: peer.hint };
    }
    if (valid.length > 1) return { kind: 'search', reason: 'ambiguous' };

    const hadHint = peers.some((peer) => typeof peer.hint === 'string' && peer.hint.length > 0);
    return { kind: 'search', reason: hadHint ? 'invalid' : 'none' };
}

/**
 * Adapt the existing trust repository to the hint source. Only `activePeers()`
 * is called; the adapter never authenticates or mutates trust. The
 * repository's own read path awaits `storage.local.setAccessLevel(
 * TRUSTED_CONTEXTS)`, re-validates every peer, and may perform its normal
 * maintenance write before returning the validated peers.
 */
export function repositoryHintSource(repository: TrustRepositoryReader): TrustHintSource {
    return {
        async activePeers(): Promise<readonly PeerHint[]> {
            const peers = await repository.activePeers();
            return hintsFromPeers(
                peers.map((peer) => ({
                    peerId: peer.installationId,
                    label: peer.label,
                    hint: peer.platformHint ?? '',
                })),
            );
        },
    };
}

/**
 * Production source. The connector passes its single `ChromeTrustRepository`
 * (whose verifier is the session manager's stage-bound verifier) so trust reads,
 * mirror upgrades and successful-authentication metadata all share one store.
 * Only `activePeers()` is called on this read path. With no argument the
 * repository's default reject-all verifier stands.
 */
export function createChromeTrustHintSource(
    repository: TrustRepositoryReader = new ChromeTrustRepository(),
): TrustHintSource {
    return repositoryHintSource(repository);
}
