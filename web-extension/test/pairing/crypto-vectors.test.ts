import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeAll, describe, expect, it } from 'vitest';

import {
    DeviceType,
    IdentityRole,
    createIdentityRecord,
    deriveEnrollmentKeys,
    encodeIdentityRecord,
    fingerprintDisplay,
    pairFingerprint,
    transcript,
    withEnrollmentKeys,
    type EnrollmentKeys,
} from '../../src/pairing/enrollment';
import { initPairingCore } from '../../src/pairing/core';
import { pairing_command } from '../../rust-wasm/pkg/veles_crypto.js';

// OTP-10 plan 2.5: the TypeScript consumer for every enrollment crypto vector in
// `protocol/v1/fixtures/crypto.json`. SPAKE2 runs on the real WASM core (both roles through the
// same `pairing_command` API the connector's wrapper uses); the enrollment schedule, identity
// records, `ET` and both fingerprints use the production `pairing/enrollment` module.

const PACKAGE_BYTES = new URL('../../rust-wasm/pkg/veles_crypto_bg.wasm', import.meta.url);
const CRYPTO = resolve(import.meta.dirname, '../../../protocol/v1/fixtures/crypto.json');

const ROLE_PHONE = 1;
const ROLE_CLIENT = 2;
const OP_START = 1;
const OP_EXCHANGE = 2;
const OP_CONFIRM = 3;
const OP_FINISH = 4;
const OP_CLOSE = 5;
const STATUS_OK = 0;
const POINT_BYTES = 65;
const CONFIRMATION_BYTES = 32;
const KEY_BYTES = 16;
const HANDLE_BYTES = 8;
const CONTEXT_PREFIX_BYTES = 30;

type JsonObject = Record<string, unknown>;

interface Fixture {
    id: string;
    input: JsonObject & {
        code?: string;
        deviation?: { type?: string; negates?: string; value?: string; value2?: string };
    };
    expected: JsonObject & {
        stage?: string;
        outputs?: JsonObject;
    };
}

const FIXTURES = (JSON.parse(readFileSync(CRYPTO, 'utf8')) as { fixtures: Fixture[] }).fixtures;

function fixture(id: string): Fixture {
    const found = FIXTURES.find((entry) => entry.id === id);
    if (found === undefined) throw new Error(`missing crypto fixture ${id}`);
    return found;
}

function str(object: JsonObject, key: string): string {
    const value = object[key];
    if (typeof value !== 'string') throw new Error(`missing fixture string ${key}`);
    return value;
}

function obj(object: JsonObject, key: string): JsonObject {
    const value = object[key];
    if (typeof value !== 'object' || value === null)
        throw new Error(`missing fixture object ${key}`);
    return value as JsonObject;
}

function hexOf(value: string): Uint8Array {
    const text = value.replace(/^0x/, '');
    if (text.length % 2 !== 0) throw new Error('malformed fixture hex');
    return Uint8Array.from(text.match(/../g) ?? [], (byte) => Number.parseInt(byte, 16));
}

function ascii(value: string): Uint8Array {
    return Uint8Array.from(value, (character) => character.charCodeAt(0));
}

function concat(...parts: Uint8Array[]): Uint8Array {
    const joined = new Uint8Array(parts.reduce((total, part) => total + part.length, 0));
    let offset = 0;
    for (const part of parts) {
        joined.set(part, offset);
        offset += part.length;
    }
    return joined;
}

/** One real WASM core handle; the core destroys it on any rejected command. */
class CoreHandle {
    private closed = false;
    private readonly handle: Uint8Array;

    constructor(handle: Uint8Array) {
        this.handle = handle;
    }

    get isClosed(): boolean {
        return this.closed;
    }

    exchange(peerPoint: Uint8Array): Uint8Array {
        return this.call(OP_EXCHANGE, peerPoint);
    }

    confirm(peerConfirmation: Uint8Array): Uint8Array {
        return this.call(OP_CONFIRM, peerConfirmation);
    }

    finish(): Uint8Array {
        const key = this.call(OP_FINISH, new Uint8Array(0));
        this.closed = true;
        return key;
    }

    close(): void {
        if (this.closed) return;
        this.closed = true;
        pairing_command(concat(Uint8Array.of(1, OP_CLOSE), this.handle));
    }

    private call(operation: number, body: Uint8Array): Uint8Array {
        if (this.closed) throw new Error('pairing execution is closed');
        const response = pairing_command(concat(Uint8Array.of(1, operation), this.handle, body));
        if (response[0] !== STATUS_OK) {
            this.closed = true;
            throw new Error(`pairing core refused operation ${operation}`);
        }
        return response.slice(1);
    }
}

function start(
    role: number,
    code: string,
    context: Uint8Array,
): { handle: CoreHandle; point: Uint8Array } {
    const response = pairing_command(
        concat(Uint8Array.of(1, OP_START, role), ascii(code), context),
    );
    if (response[0] !== STATUS_OK) throw new Error('pairing core refused start');
    if (response.length !== 1 + HANDLE_BYTES + POINT_BYTES) {
        throw new Error(`pairing core start returned ${response.length} bytes`);
    }
    return {
        handle: new CoreHandle(response.slice(1, 1 + HANDLE_BYTES)),
        point: response.slice(1 + HANDLE_BYTES),
    };
}

function contextOf(id: string): Uint8Array {
    const outputs = fixture(id).expected.outputs;
    if (outputs === undefined) throw new Error(`no outputs for ${id}`);
    return hexOf(str(outputs, 'ctx'));
}

function baseCode(): string {
    return str(fixture('veles-pairing-code-000000').input, 'code');
}

beforeAll(async () => {
    await initPairingCore(readFileSync(PACKAGE_BYTES));
});

describe('crypto.json pairing code vectors on the real WASM core', () => {
    it('consumes both positive pairing-code vectors with one shared Ke', () => {
        const ids = FIXTURES.filter((entry) => entry.id.startsWith('veles-pairing-code-')).map(
            (entry) => entry.id,
        );
        expect(ids.sort()).toEqual(['veles-pairing-code-000000', 'veles-pairing-code-123456']);
        for (const id of ids) {
            const code = str(fixture(id).input, 'code');
            const context = contextOf(id);
            const phone = start(ROLE_PHONE, code, context);
            const client = start(ROLE_CLIENT, code, context);
            expect(phone.point).toHaveLength(POINT_BYTES);
            expect(phone.point[0]).toBe(4);
            expect(client.point).toHaveLength(POINT_BYTES);
            expect(client.point[0]).toBe(4);
            const confirmationA = phone.handle.exchange(client.point);
            expect(confirmationA).toHaveLength(CONFIRMATION_BYTES);
            // Party B produces no first confirmation.
            expect(client.handle.exchange(phone.point)).toHaveLength(0);
            const confirmationB = client.handle.confirm(confirmationA);
            expect(confirmationB).toHaveLength(CONFIRMATION_BYTES);
            expect(phone.handle.confirm(confirmationB)).toHaveLength(0);
            const phoneKey = phone.handle.finish();
            const clientKey = client.handle.finish();
            expect(phoneKey).toHaveLength(KEY_BYTES);
            expect(Array.from(phoneKey)).toEqual(Array.from(clientKey));
        }
    });

    it('rejects the wrong-code vector at the confirmation', () => {
        const negative = fixture('veles-neg-wrong-code');
        const deviation = obj(negative.input, 'deviation');
        const context = contextOf(str(deviation, 'negates'));
        const phone = start(ROLE_PHONE, str(negative.input, 'code'), context);
        const client = start(ROLE_CLIENT, str(deviation, 'value'), context);
        const confirmationA = phone.handle.exchange(client.point);
        expect(client.handle.exchange(phone.point)).toHaveLength(0);
        expect(() => client.handle.confirm(confirmationA)).toThrow();
        expect(client.handle.isClosed).toBe(true);
        phone.handle.close();
    });

    it('rejects the context-mismatch vector before SPAKE2 starts', () => {
        const negative = fixture('veles-neg-context-mismatch');
        const deviation = obj(negative.input, 'deviation');
        const mismatched = hexOf(str(deviation, 'value'));
        // The vector's deviation is a truncated public context: the core bounds the context
        // length, so it can never start an execution with it.
        expect(mismatched).toHaveLength(16);
        expect(() => start(ROLE_CLIENT, str(negative.input, 'code'), mismatched)).toThrow();
    });

    it('a mismatched canonical context fails the confirmation', () => {
        const negative = fixture('veles-neg-context-mismatch');
        const deviation = obj(negative.input, 'deviation');
        const base = contextOf(str(deviation, 'negates'));
        const mismatched = base.slice();
        // The pairing_id field of the §3.3 context follows the 30-byte label and three u16s.
        mismatched.set(hexOf(str(deviation, 'value')), CONTEXT_PREFIX_BYTES + 6);
        const phone = start(ROLE_PHONE, str(negative.input, 'code'), base);
        const client = start(ROLE_CLIENT, str(negative.input, 'code'), mismatched);
        const confirmationA = phone.handle.exchange(client.point);
        expect(client.handle.exchange(phone.point)).toHaveLength(0);
        expect(() => client.handle.confirm(confirmationA)).toThrow();
        expect(client.handle.isClosed).toBe(true);
        phone.handle.close();
    });

    it('role mismatch is a real confirmation failure', () => {
        const negative = fixture('veles-neg-role-mismatch');
        const deviation = obj(negative.input, 'deviation');
        const context = contextOf(str(deviation, 'negates'));
        const code = str(fixture(str(deviation, 'negates')).input, 'code');
        const first = start(ROLE_PHONE, code, context);
        const second = start(ROLE_PHONE, code, context);
        const firstConfirmation = first.handle.exchange(second.point);
        const secondConfirmation = second.handle.exchange(first.point);
        expect(firstConfirmation).toHaveLength(CONFIRMATION_BYTES);
        expect(secondConfirmation).toHaveLength(CONFIRMATION_BYTES);
        expect(() => first.handle.confirm(secondConfirmation)).toThrow();
        expect(() => second.handle.confirm(firstConfirmation)).toThrow();
    });

    it('role swap is fixed-role policy above a self-consistent core exchange', () => {
        // The core exposes both SPAKE2 roles and does not pin them to endpoints; the vector's
        // role-stage rejection is the fixed-role policy of the machines (OTP2-CRY-002).
        const negative = fixture('veles-neg-role-swap');
        const deviation = obj(negative.input, 'deviation');
        const context = contextOf(str(deviation, 'negates'));
        const code = str(fixture(str(deviation, 'negates')).input, 'code');
        const phone = start(ROLE_CLIENT, code, context);
        const client = start(ROLE_PHONE, code, context);
        const confirmationA = client.handle.exchange(phone.point);
        expect(confirmationA).toHaveLength(CONFIRMATION_BYTES);
        expect(phone.handle.exchange(client.point)).toHaveLength(0);
        const confirmationB = phone.handle.confirm(confirmationA);
        expect(confirmationB).toHaveLength(CONFIRMATION_BYTES);
        expect(client.handle.confirm(confirmationB)).toHaveLength(0);
        expect(Array.from(phone.handle.finish())).toEqual(Array.from(client.handle.finish()));
    });

    it('every fixed point negative fails closed and destroys the handle', () => {
        const ids = [
            'veles-neg-point-off-curve',
            'veles-neg-point-zero-coordinates',
            'veles-neg-point-infinity',
            'veles-neg-point-compressed',
        ];
        for (const id of ids) {
            const deviation = obj(fixture(id).input, 'deviation');
            const started = start(ROLE_PHONE, baseCode(), contextOf(str(deviation, 'negates')));
            expect(() => started.handle.exchange(hexOf(str(deviation, 'value')))).toThrow();
            expect(started.handle.isClosed).toBe(true);
        }
    });

    it('the shared-secret identity vector fails on both role views', () => {
        const deviation = obj(fixture('veles-neg-shared-secret-identity').input, 'deviation');
        const phone = start(ROLE_PHONE, baseCode(), contextOf(str(deviation, 'negates')));
        expect(() => phone.handle.exchange(hexOf(str(deviation, 'value')))).toThrow();
        expect(phone.handle.isClosed).toBe(true);
        const client = start(ROLE_CLIENT, baseCode(), contextOf(str(deviation, 'negates')));
        expect(() => client.handle.exchange(hexOf(str(deviation, 'value2')))).toThrow();
        expect(client.handle.isClosed).toBe(true);
    });
});

describe('crypto.json enrollment schedule consumer', () => {
    it('matches the schedule, identity records, ET and both fingerprints', async () => {
        const enrollment = fixture('veles-enrollment-schedule');
        const context = hexOf(str(enrollment.input, 'ctx'));
        const outputs = enrollment.expected.outputs;
        if (outputs === undefined) throw new Error('no enrollment outputs');
        const keys: EnrollmentKeys = await deriveEnrollmentKeys(
            hexOf(str(enrollment.input, 'ke')),
            context,
        );
        await withEnrollmentKeys(keys, async (live) => {
            expect(live.aToBAesKey).toEqual(hexOf(str(outputs, 'k_ab_aes')));
            expect(live.aToBNoncePrefix).toEqual(hexOf(str(outputs, 'k_ab_nonce_prefix')));
            expect(live.phoneActivationKey).toEqual(hexOf(str(outputs, 'k_act_a')));
            expect(live.bToAAesKey).toEqual(hexOf(str(outputs, 'k_ba_aes')));
            expect(live.bToANoncePrefix).toEqual(hexOf(str(outputs, 'k_ba_nonce_prefix')));
            expect(live.clientActivationKey).toEqual(hexOf(str(outputs, 'k_act_b')));
        });

        const phoneIdentity = obj(enrollment.input, 'phone_identity');
        const clientIdentity = obj(enrollment.input, 'client_identity');
        const phone = await createIdentityRecord(
            {
                installationId: hexOf(str(phoneIdentity, 'installation_id')),
                label: str(phoneIdentity, 'label'),
                deviceType: DeviceType.PHONE,
                identityPubkey: hexOf(str(outputs, 'phone_identity_pub')),
                nonce: hexOf(str(phoneIdentity, 'nonce')),
            },
            IdentityRole.PHONE,
        );
        const client = await createIdentityRecord(
            {
                installationId: hexOf(str(clientIdentity, 'installation_id')),
                label: str(clientIdentity, 'label'),
                deviceType: DeviceType.DESKTOP,
                identityPubkey: hexOf(str(outputs, 'client_identity_pub')),
                nonce: hexOf(str(clientIdentity, 'nonce')),
            },
            IdentityRole.CLIENT,
        );
        expect(encodeIdentityRecord(phone, IdentityRole.PHONE)).toEqual(
            hexOf(str(outputs, 'phone_identity_body')),
        );
        expect(encodeIdentityRecord(client, IdentityRole.CLIENT)).toEqual(
            hexOf(str(outputs, 'client_identity_body')),
        );
        expect(phone.fingerprint).toEqual(hexOf(str(outputs, 'fp_self_phone')));
        expect(client.fingerprint).toEqual(hexOf(str(outputs, 'fp_self_client')));

        const et = await transcript({
            context,
            phonePoint: hexOf(str(enrollment.input, 'p_a')),
            clientPoint: hexOf(str(enrollment.input, 'p_b')),
            phoneConfirm: hexOf(str(enrollment.input, 'c_a')),
            clientConfirm: hexOf(str(enrollment.input, 'c_b')),
            phoneIdentityBody: hexOf(str(outputs, 'phone_identity_body')),
            clientIdentityBody: hexOf(str(outputs, 'client_identity_body')),
        });
        expect(et).toEqual(hexOf(str(outputs, 'et')));
        const pair = await pairFingerprint(phone.identityPubkey, client.identityPubkey);
        expect(pair).toEqual(hexOf(str(outputs, 'fp_pair')));
        expect(fingerprintDisplay(pair)).toBe(str(outputs, 'fp_pair_display'));
    });
});
