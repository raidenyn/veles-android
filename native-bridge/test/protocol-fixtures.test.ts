import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

// Test-only OTP-02 consumer. It deliberately resolves the repository fixture
// root rather than accepting a package-local copy.
//
// OTP-03 hooks named explicitly (not implemented here): the Tauri host's
// record transport (opaque base64 only, never parsing) gains protocol-level
// conformance testing in OTP-03 over these same vectors (OTP2-BRD-*, GATT
// framing OTP2-GAT-010/011); OTP-02 evidence stops at shared-fixture
// byte-level assertions.
const PROTOCOL_ROOT = resolve(import.meta.dirname, '../../protocol/v1');
const U64_MAX = '18446744073709551615';
const U64_OVERFLOW = '18446744073709551616';
const BINARY_RECORD = 'AP+AgQ3+';
const BINARY_BYTES = [0x00, 0xff, 0x80, 0x81, 0x0d, 0xfe];

interface Fixture {
    id: string;
    input: Record<string, unknown>;
    expected: Record<string, unknown>;
}

function fixturesOf(name: string): Fixture[] {
    const path = resolve(PROTOCOL_ROOT, 'fixtures', name);
    try {
        const envelope = JSON.parse(readFileSync(path, 'utf8')) as {
            version: number;
            fixtures: Fixture[];
        };
        expect(envelope.version).toBe(1);
        expect(envelope.fixtures.length).toBeGreaterThan(0);
        return envelope.fixtures;
    } catch (error) {
        throw new Error(`missing or unreadable shared fixture ${name}: ${String(error)}`);
    }
}

function fixture(id: string, name: string): Fixture {
    const found = fixturesOf(name).find((entry) => entry.id === id);
    if (found === undefined) throw new Error(`missing fixture id ${id}`);
    return found;
}

function decodeHex(value: string): Uint8Array {
    if (value.length % 2 !== 0 || !/^[0-9a-f]*$/.test(value)) {
        throw new Error(`malformed hex byte string: "${value}"`);
    }
    return Uint8Array.from({ length: value.length / 2 }, (_, index) =>
        Number.parseInt(value.slice(index * 2, index * 2 + 2), 16),
    );
}

function encodeHex(bytes: Uint8Array): string {
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function decodeBase64(value: string): Uint8Array {
    if (
        value.length % 4 !== 0 ||
        !/^[A-Za-z0-9+/]*={0,2}$/.test(value) ||
        value.slice(0, value.indexOf('=') === -1 ? value.length : value.indexOf('=')).includes('=')
    ) {
        throw new Error(`malformed base64 record: "${value}"`);
    }
    const bytes = new Uint8Array(Buffer.from(value, 'base64'));
    if (Buffer.from(bytes).toString('base64') !== value) {
        throw new Error(`malformed base64 record (non-canonical pad bits): "${value}"`);
    }
    return bytes;
}

function parseU64(value: string): bigint {
    if (!/^(0|[1-9][0-9]*)$/.test(value) || BigInt(value) > BigInt(U64_MAX)) {
        throw new Error(`invalid u64 decimal string: ${value}`);
    }
    return BigInt(value);
}

describe('OTP-02 shared protocol fixtures (native bridge consumer)', () => {
    it('loads shared encoding and crypto fixture IDs', () => {
        expect(fixture('enc-record-push-max-seq-u64', 'encoding.json').id).toBe(
            'enc-record-push-max-seq-u64',
        );
        expect(fixture('veles-record-push-event', 'crypto.json').id).toBe(
            'veles-record-push-event',
        );
    });

    it('decodes accepted fixture bytes and preserves the u64 maximum', () => {
        for (const entry of fixturesOf('encoding.json')) {
            if (entry.expected.accepted === true) {
                const bytes = entry.input.bytes;
                expect(typeof bytes).toBe('string');
                expect(encodeHex(decodeHex(bytes as string))).toBe(bytes);
            }
        }
        const expected = fixture('enc-record-push-max-seq-u64', 'encoding.json').expected as {
            fields: Record<string, string>;
        };
        expect(expected.fields.sequence_number).toBe(U64_MAX);
        expect(parseU64(expected.fields.sequence_number)).toBe((1n << 64n) - 1n);
        expect(() => parseU64(U64_OVERFLOW)).toThrow(/invalid u64/);
    });

    it('preserves the fixture binary record byte-for-byte and rejects malformed forms', () => {
        const text = fixture('brd-ev-message-binary-round-trip-preserved', 'bridge.json').input
            .text;
        expect(typeof text).toBe('string');
        expect((JSON.parse(text as string) as { record_base64: string }).record_base64).toBe(
            BINARY_RECORD,
        );
        expect(Array.from(decodeBase64(BINARY_RECORD))).toEqual(BINARY_BYTES);
        for (const value of ['AA=A', 'A', 'AB==']) {
            expect(() => decodeBase64(value)).toThrow(/malformed base64 record/);
        }
        for (const value of ['AB', 'a', '00g0'])
            expect(() => decodeHex(value)).toThrow(/malformed hex/);
    });

    it('fails loudly for a missing fixture ID', () => {
        expect(() => fixture('this-fixture-must-not-exist', 'crypto.json')).toThrow(
            /missing fixture id/,
        );
    });
});

describe('OTP-24 shared record boundary shapes (native bridge consumer)', () => {
    const limits = JSON.parse(readFileSync(resolve(PROTOCOL_ROOT, 'limits.json'), 'utf8')) as {
        limits: { name: string; value: number }[];
    };
    const named = (name: string): number => {
        const entry = limits.limits.find((limit) => limit.name === name);
        if (entry === undefined) throw new Error(`missing shared limit ${name}`);
        return Number(entry.value);
    };
    // Envelope admission ceiling vs the GATT send ceiling the host enforces after admission
    // (OTP2-BRD-031 vs OTP2-CRY-016c/OTP2-GAT-011). Frame byte-limit shapes (1..65536, zero/
    // oversize/truncated-vs-clean-EOF) are executable Rust framing tests only
    // (native-bridge/host/tests/protocol.rs); byte-limits beyond the record ceilings belong to
    // that implementation, not to a helper mirrored in test code.
    const RECORD_MAX = named('bridge_record_max_bytes');
    const GATT_RECORD_MAX = named('record_max_bytes');

    it('keeps the two record ceilings distinct in the shared limits inventory', () => {
        expect(RECORD_MAX).toBe(5120);
        expect(GATT_RECORD_MAX).toBe(4096);
    });

    it('size-boundary send fixtures keep the shared envelope/GATT verdicts', () => {
        const sizes = [
            ['brd-req-send-record-5119-below-limit', true, 5119],
            ['brd-req-send-record-5120-at-limit', true, 5120],
            ['brd-req-send-record-5121-over-limit', false, 5121],
            ['brd-req-send-record-empty', false, 0],
        ] as const;
        for (const [id, accepted, decodedLength] of sizes) {
            const entry = fixture(id, 'bridge.json');
            expect(entry.expected.accepted).toBe(accepted);
            const parsed = JSON.parse(entry.input.text as string) as { record_base64: string };
            const decoded = decodeBase64(parsed.record_base64);
            expect(Array.from(decoded).length).toBe(decodedLength);
            if (accepted) {
                expect(decodedLength).toBeGreaterThanOrEqual(GATT_RECORD_MAX);
                expect(decodedLength).toBeLessThanOrEqual(RECORD_MAX);
            } else {
                expect(decodedLength === 0 || decodedLength > RECORD_MAX).toBe(true);
            }
        }
    });
});
