import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, it, expect } from 'vitest';

// OTP-02 Plan Task 6 shared-fixture consumer (web extension). The fixtures
// live at the repository root `protocol/v1/` — the same byte-exact files the
// Node contract checks and the Kotlin/Rust/TypeScript consumers read. No
// checked-in copy. This module is test-only and must never be bundle input.
//
// OTP-03 hooks named explicitly (not implemented here): WASM-bound record
// codec and SPAKE2/AEAD/signature conformance over these same committed
// vectors (OTP2-CRY-*, OTP2-CRY-016) run through the rust-wasm pkg in later
// plan tasks; the OTP-02 evidence stops at shared-fixture byte-level
// assertions.

// The consumer resolves the shared protocol/v1 fixture root at the
// repository root from this package's location — never a package-local copy.
const PROTOCOL_ROOT = resolve(import.meta.dirname, '../../protocol/v1');

const U64_MAX_DECIMAL = '18446744073709551615';
const U64_OVERFLOW_DECIMAL = '18446744073709551616';
const BASE64_ROUND_TRIP_STRING = 'AP+AgQ3+';
const BASE64_ROUND_TRIP_BYTES = [0x00, 0xff, 0x80, 0x81, 0x0d, 0xfe];
const BASE64_ROUND_TRIP_HEX = '00ff80810dfe';
const BASE64_SIMPLE_VALID = 'AQ==';
const BASE64_SIMPLE_VALID_BYTES = [0x01];

interface Fixture {
    id: string;
    fixture_version: number;
    category: string;
    input: Record<string, unknown>;
    expected: Record<string, unknown>;
}

interface FixtureEnvelope {
    version: number;
    fixtures: Fixture[];
}

function loadEnvelope(name: string): FixtureEnvelope {
    const path = resolve(PROTOCOL_ROOT, 'fixtures', name);
    let parsed: unknown;
    try {
        parsed = JSON.parse(readFileSync(path, 'utf8'));
    } catch (error) {
        throw new Error(
            `missing or unreadable shared fixture ${name} (${path}): ${String(error)} — ` +
                'the consumer must read the shared protocol/v1 root',
        );
    }
    return parsed as FixtureEnvelope;
}

function fixturesOf(name: string): Fixture[] {
    const envelope = loadEnvelope(name);
    expect(envelope.version).toBe(1);
    expect(envelope.fixtures.length).toBeGreaterThan(0);
    return envelope.fixtures;
}

function fixtureById(id: string, names: string[]): Fixture {
    for (const name of names) {
        const found = fixturesOf(name).find((fixture) => fixture.id === id);
        if (found !== undefined) return found;
    }
    throw new Error(`missing fixture id ${id}`);
}

function strField(value: Record<string, unknown>, key: string): string {
    const field = value[key];
    if (typeof field !== 'string') throw new Error(`missing fixture string field ${key}`);
    return field;
}

// Canonical unsigned decimal string for u64 fields, mirroring the fixture
// contract: no leading zeros, parses exactly into the u64 range.
function parseU64(value: string): bigint {
    if (!/^[0-9]+$/.test(value) || (value.length > 1 && value.startsWith('0'))) {
        throw new Error(`field must be a canonical unsigned decimal string: "${value}"`);
    }
    const number_ = BigInt(value);
    if (number_ > BigInt(U64_MAX_DECIMAL)) {
        throw new Error(`u64 decimal string overflow rejected: ${value}`);
    }
    return number_;
}

// Canonical hex byte strings: lowercase, even length (mirrors
// checks/binary.mjs decodeHex).
function decodeHex(text: string): Uint8Array {
    if (text.length % 2 !== 0) {
        throw new Error(`malformed hex byte string (odd length): "${text}"`);
    }
    if (!/^[0-9a-f]*$/.test(text)) {
        throw new Error(`malformed hex byte string (not lowercase hex): "${text}"`);
    }
    const bytes = new Uint8Array(text.length / 2);
    for (let index = 0; index < bytes.length; index += 1) {
        bytes[index] = Number.parseInt(text.slice(2 * index, 2 * index + 2), 16);
    }
    return bytes;
}

function encodeHex(bytes: Uint8Array): string {
    let text = '';
    for (const byte of bytes) text += byte.toString(16).padStart(2, '0');
    return text;
}

// Canonical RFC 4648 base64 with at most two trailing pad characters
// (Buffer.from alone accepts unpadded input, so rules are enforced first).
function decodeBase64(value: string): Uint8Array {
    if (value.length % 4 !== 0) {
        throw new Error(`malformed base64 record (length not a multiple of 4): "${value}"`);
    }
    const padCount = value.endsWith('==') ? 2 : value.endsWith('=') ? 1 : 0;
    if (padCount > 0) {
        const payload = value.slice(0, value.length - padCount);
        if (payload.includes('=')) {
            throw new Error(
                `malformed base64 record (padding must only be at the end): "${value}"`,
            );
        }
    } else if (value.includes('=')) {
        throw new Error(`malformed base64 record (padding must only trail): "${value}"`);
    }
    if (!/^[A-Za-z0-9+/]*={0,2}$/.test(value)) {
        throw new Error(`malformed base64 record (non-canonical alphabet): "${value}"`);
    }
    const decoded = new Uint8Array(Buffer.from(value, 'base64'));
    if (encodeBase64(decoded) !== value) {
        throw new Error(`malformed base64 record (non-canonical pad bits): "${value}"`);
    }
    return decoded;
}

function encodeBase64(bytes: Uint8Array): string {
    return Buffer.from(bytes).toString('base64');
}

function envelopeText(fixture: Fixture): { record_base64: string } {
    return JSON.parse(strField(fixture.input, 'text')) as { record_base64: string };
}

function expectedAccepted(fixture: Fixture): boolean {
    return fixture.expected.accepted === true;
}

function expectedRejected(fixture: Fixture): boolean {
    return fixture.expected.rejected === true;
}

// ---------- tests ----------

describe('OTP-02 shared protocol fixtures (web extension consumer)', () => {
    let encodingIds: string[] = [];
    let cryptoIds: string[] = [];

    beforeEach(() => {
        encodingIds = fixturesOf('encoding.json').map((fixture) => fixture.id);
        cryptoIds = fixturesOf('crypto.json').map((fixture) => fixture.id);
    });

    it('loads encoding and crypto fixture ids from the shared root', () => {
        expect(new Set(encodingIds).size).toBe(encodingIds.length);
        expect(encodingIds).toContain('enc-record-push-max-seq-u64');
        expect(new Set(cryptoIds).size).toBe(cryptoIds.length);
        expect(cryptoIds).toContain('rfc9382-p256-sha256-hkdf-hmac-server-client');
        expect(cryptoIds).toContain('veles-record-push-event');
    });

    it('decodes accepted hex fixtures byte-for-byte and re-encodes canonically', () => {
        for (const fixture of fixturesOf('encoding.json')) {
            if (!expectedAccepted(fixture)) continue;
            const bytesHex = strField(fixture.input, 'bytes');
            const decoded = decodeHex(bytesHex);
            expect(encodeHex(decoded)).toBe(bytesHex);
        }
    });

    it('preserves u64 decimal strings exactly', () => {
        const fields = (
            fixtureById('enc-record-push-max-seq-u64', ['encoding.json']).expected as {
                fields: Record<string, string>;
            }
        ).fields;
        expect(fields.sequence_number).toBe(U64_MAX_DECIMAL);
        expect(parseU64(fields.sequence_number)).toBe(18446744073709551615n);
        const maxReceipt = (
            fixtureById('enc-body-otp-event-max-bounds', ['encoding.json']).expected as {
                fields: Record<string, string>;
            }
        ).fields;
        expect(maxReceipt.receipt_epoch_seconds).toBe(U64_MAX_DECIMAL);
        expect(parseU64(U64_MAX_DECIMAL)).toBe((1n << 64n) - 1n);
        expect(() => parseU64(U64_OVERFLOW_DECIMAL)).toThrow(/overflow/);
        const limits = JSON.parse(readFileSync(resolve(PROTOCOL_ROOT, 'limits.json'), 'utf8')) as {
            limits: { name: string; value: string }[];
        };
        expect(limits.limits.find((limit) => limit.name === 'sequence_max')?.value).toBe(
            U64_MAX_DECIMAL,
        );
        const stateFixtures = JSON.parse(
            readFileSync(resolve(PROTOCOL_ROOT, 'fixtures', 'records.json'), 'utf8'),
        ) as { fixtures: { id: string }[] };
        expect(stateFixtures.fixtures.map((fixture) => fixture.id)).toContain(
            'st-rec-max-u64-sequence-then-terminated',
        );
    });

    it('rejects malformed hex byte strings', () => {
        for (const malformed of ['AB', 'abc', '0g00', 'dead bee', '0xzz']) {
            expect(() => decodeHex(malformed)).toThrow(/malformed hex byte string/);
        }
    });

    it('decodes canonical base64 records byte-for-byte and re-encodes', () => {
        expect(Array.from(decodeBase64(BASE64_ROUND_TRIP_STRING))).toEqual(BASE64_ROUND_TRIP_BYTES);
        expect(encodeHex(decodeBase64(BASE64_ROUND_TRIP_STRING))).toBe(BASE64_ROUND_TRIP_HEX);
        expect(encodeBase64(decodeBase64(BASE64_ROUND_TRIP_STRING))).toBe(BASE64_ROUND_TRIP_STRING);
        expect(Array.from(decodeBase64(BASE64_SIMPLE_VALID))).toEqual(BASE64_SIMPLE_VALID_BYTES);
        expect(
            envelopeText(fixtureById('brd-ev-message-binary-round-trip-preserved', ['bridge.json']))
                .record_base64,
        ).toBe(BASE64_ROUND_TRIP_STRING);
    });

    it('rejects malformed base64 records taken from the shared fixtures', () => {
        for (const id of [
            'brd-req-send-base64-odd-length',
            'brd-req-send-base64-whitespace',
            'brd-req-send-base64-url-alphabet',
        ]) {
            const recordBase64 = envelopeText(fixtureById(id, ['bridge.json'])).record_base64;
            expect(() => decodeBase64(recordBase64)).toThrow(/malformed base64 record/);
        }
    });

    it('crypto outputs decode byte-for-byte and negatives bind to existing positives', () => {
        const crypto = fixturesOf('crypto.json');
        const positiveIds = crypto
            .filter((fixture) => !expectedRejected(fixture))
            .map((fixture) => fixture.id);
        expect(positiveIds.some((id) => id.startsWith('rfc9382-p256-sha256-hkdf-hmac-'))).toBe(
            true,
        );
        let hexByteOutputs = 0;
        for (const fixture of crypto) {
            if (expectedRejected(fixture)) {
                const deviation = (
                    fixture.input as {
                        deviation: { negates: string };
                    }
                ).deviation;
                expect(positiveIds).toContain(deviation.negates);
            } else {
                const outputs = fixture.expected.outputs as Record<string, string> | undefined;
                for (const [, value] of Object.entries(outputs ?? {})) {
                    expect(typeof value).toBe('string');
                    if (value.startsWith('0x')) {
                        expect(encodeHex(decodeHex(value.slice(2)))).toBe(value.slice(2));
                        hexByteOutputs += 1;
                    }
                }
            }
        }
        expect(hexByteOutputs).toBeGreaterThan(0);
    });

    it('missing fixture ids fail for every consumer file', () => {
        for (const name of ['encoding.json', 'crypto.json', 'bridge.json']) {
            expect(
                fixturesOf(name).some((fixture) => fixture.id === 'this-fixture-must-not-exist'),
            ).toBe(false);
            expect(() => fixtureById('this-fixture-must-not-exist', [name])).toThrow(
                /missing fixture id/,
            );
        }
    });
});
