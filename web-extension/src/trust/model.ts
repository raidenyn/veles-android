/** Public durable metadata only. Never put protocol/session secrets in these records. */
export interface InstallationIdentity {
    installationId: string;
    publicKey: string;
}

export interface PendingPeer extends InstallationIdentity {
    label: string;
    deviceType: number;
    enrollmentTranscript: string;
}

export type TrustState = 'pending' | 'active_activation_pending' | 'active';
export type ProofStage = 'complete' | 'phone_active' | 'authenticated';

export interface TrustedPeer extends PendingPeer {
    fingerprint: string;
    state: TrustState;
    createdAtMillis: number;
    lastSeenMillis: number | null;
    pendingContext: string | null;
    pendingSince: number | null;
    platformHint?: string;
}

export interface TrustSnapshot {
    version: 1;
    identity: InstallationIdentity;
    peers: TrustedPeer[];
}

export class TrustError extends Error {
    constructor() {
        super('Could not authenticate — re-pair required');
        this.name = 'TrustError';
    }
}

/** Infrastructure failures are fail-closed but must never suggest erasing trust. */
export class TrustUnavailableError extends Error {
    constructor() {
        super('Trust storage unavailable — retry later');
        this.name = 'TrustUnavailableError';
    }
}

export function requireTrust(condition: unknown): asserts condition {
    if (!condition) throw new TrustError();
}

export function hex(bytes: Uint8Array): string {
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export function bytes(value: string, size: number): Uint8Array<ArrayBuffer> {
    requireTrust(typeof value === 'string' && new RegExp(`^[0-9a-f]{${size * 2}}$`).test(value));
    return Uint8Array.from(value.match(/../g) ?? [], (byte) => parseInt(byte, 16));
}

export function exactFields(
    value: unknown,
    fields: string[],
    optional: string[] = [],
): asserts value is Record<string, unknown> {
    requireTrust(value !== null && typeof value === 'object' && !Array.isArray(value));
    const keys = Object.keys(value);
    requireTrust(
        fields.every((key) => keys.includes(key)) &&
            keys.every((key) => fields.includes(key) || optional.includes(key)),
    );
}

export async function importPublicKey(value: string): Promise<CryptoKey> {
    const raw = bytes(value, 65);
    requireTrust(raw[0] === 4);
    try {
        return await crypto.subtle.importKey(
            'raw',
            raw,
            { name: 'ECDSA', namedCurve: 'P-256' },
            true,
            ['verify'],
        );
    } catch {
        throw new TrustError();
    }
}

export async function fingerprint(phone: string, client: string): Promise<string> {
    const label = new TextEncoder().encode('Veles identity fingerprint v1');
    const input = new Uint8Array(label.length + 130);
    input.set(label);
    input.set(bytes(phone, 65), label.length);
    input.set(bytes(client, 65), label.length + 65);
    return hex(new Uint8Array(await crypto.subtle.digest('SHA-256', input)));
}
