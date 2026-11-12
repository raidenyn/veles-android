import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import {
    DeviceType,
    EnrollmentReject,
    IdentityRole,
    decodeIdentityRecord,
    decodeIdentityRecordForStorage,
    decodeFingerprintVerifiedIdentityRecord,
    deriveEnrollmentKeys,
    encodeIdentityRecord,
    enrollmentNonce,
    enrollmentSessionPrefix,
    fingerprintDisplay,
    isEnrollmentKeysWiped,
    pairFingerprint,
    recordFingerprint,
    transcript,
    wipeEnrollmentKeys,
    withEnrollmentKeys,
    createIdentityRecord,
    type EnrollmentTranscript,
    type IdentityRecord,
} from '../../src/pairing/enrollment';
import { authorized } from '../../src/session/policy';
import { Direction, RecordType, SessionClass } from '../../src/session/wire';
import { bytes, hex, hexOf, input, obj, outputs, str } from '../session/fixtures';

const ENROLLMENT = 'veles-enrollment-schedule';
const fixtureInput = input(ENROLLMENT);
const fixtureOutputs = outputs(ENROLLMENT);

const context = bytes(fixtureInput, 'ctx');
const ke = bytes(fixtureInput, 'ke');
const phoneBody = bytes(fixtureOutputs, 'phone_identity_body');
const clientBody = bytes(fixtureOutputs, 'client_identity_body');
const et = bytes(fixtureOutputs, 'et');

function validPoint(): Uint8Array {
    return bytes(fixtureOutputs, 'phone_identity_pub');
}

function recordFor(
    role: IdentityRole,
    deviceType: DeviceType,
    label = 'phone-label',
): Promise<IdentityRecord> {
    return createIdentityRecord(
        {
            installationId: Uint8Array.from({ length: 16 }, (_, index) => index),
            label,
            deviceType,
            identityPubkey: validPoint(),
            nonce: Uint8Array.from({ length: 16 }, (_, index) => 0x40 + index),
        },
        role,
    );
}

function encodeUnchecked(record: IdentityRecord): Uint8Array {
    const label = new TextEncoder().encode(record.label);
    const body = new Uint8Array(16 + 2 + label.length + 1 + 65 + 32 + 16);
    body.set(record.installationId, 0);
    body[16] = label.length >>> 8;
    body[17] = label.length & 0xff;
    body.set(label, 18);
    body[18 + label.length] = record.deviceType;
    body.set(record.identityPubkey, 19 + label.length);
    body.set(record.fingerprint, 19 + label.length + 65);
    body.set(record.nonce, 19 + label.length + 65 + 32);
    return body;
}

function transcriptOf(
    contextOverride: Uint8Array = context,
    phoneBodyOverride: Uint8Array = phoneBody,
): Promise<Uint8Array> {
    const value: EnrollmentTranscript = {
        context: contextOverride,
        phonePoint: bytes(fixtureInput, 'p_a'),
        clientPoint: bytes(fixtureInput, 'p_b'),
        phoneConfirm: bytes(fixtureInput, 'c_a'),
        clientConfirm: bytes(fixtureInput, 'c_b'),
        phoneIdentityBody: phoneBodyOverride,
        clientIdentityBody: clientBody,
    };
    return transcript(value);
}

async function hmacSha256(key: Uint8Array, data: Uint8Array): Promise<Uint8Array> {
    const imported = await crypto.subtle.importKey(
        'raw',
        key,
        { name: 'HMAC', hash: 'SHA-256' },
        false,
        ['sign'],
    );
    return new Uint8Array(await crypto.subtle.sign('HMAC', imported, data));
}

function activationMac(key: Uint8Array, value: Uint8Array, stage: number): Promise<Uint8Array> {
    return hmacSha256(key, new Uint8Array([...value, stage]));
}

describe('enrollment schedule', () => {
    it('matches the fixture key schedule', async () => {
        await withEnrollmentKeys(await deriveEnrollmentKeys(ke, context), async (keys) => {
            expect(keys.aToBAesKey).toEqual(bytes(fixtureOutputs, 'k_ab_aes'));
            expect(keys.aToBNoncePrefix).toEqual(bytes(fixtureOutputs, 'k_ab_nonce_prefix'));
            expect(keys.phoneActivationKey).toEqual(bytes(fixtureOutputs, 'k_act_a'));
            expect(keys.bToAAesKey).toEqual(bytes(fixtureOutputs, 'k_ba_aes'));
            expect(keys.bToANoncePrefix).toEqual(bytes(fixtureOutputs, 'k_ba_nonce_prefix'));
            expect(keys.clientActivationKey).toEqual(bytes(fixtureOutputs, 'k_act_b'));
        });
    });

    it('matches the fixture session prefix and nonces', async () => {
        expect(await enrollmentSessionPrefix(context)).toEqual(
            bytes(fixtureOutputs, 'session_id_prefix'),
        );
        expect(enrollmentNonce(bytes(fixtureOutputs, 'k_ab_nonce_prefix'), 0n)).toEqual(
            bytes(fixtureOutputs, 'enrollment_nonce_ab_seq0'),
        );
        expect(enrollmentNonce(bytes(fixtureOutputs, 'k_ba_nonce_prefix'), 7n)).toEqual(
            bytes(fixtureOutputs, 'enrollment_nonce_ba_seq7'),
        );
    });

    it('rejects a wrong Ke size and an oversized context', async () => {
        await expect(deriveEnrollmentKeys(new Uint8Array(15), context)).rejects.toBeInstanceOf(
            EnrollmentReject,
        );
        await expect(deriveEnrollmentKeys(ke, new Uint8Array(4097))).rejects.toBeInstanceOf(
            EnrollmentReject,
        );
        const keys = await deriveEnrollmentKeys(ke, new Uint8Array(4096));
        wipeEnrollmentKeys(keys);
    });

    it('zeroes every derived field after use, including the failure path', async () => {
        const keys = await deriveEnrollmentKeys(ke, context);
        expect(isEnrollmentKeysWiped(keys)).toBe(false);
        const observed = await withEnrollmentKeys(keys, (holder) => holder.aToBAesKey.slice());
        expect(observed).toHaveLength(32);
        expect(isEnrollmentKeysWiped(keys)).toBe(true);
        for (const field of Object.values(keys) as Uint8Array[]) {
            expect(field.every((byte) => byte === 0)).toBe(true);
        }

        let reuseRan = false;
        await expect(
            withEnrollmentKeys(keys, () => {
                reuseRan = true;
            }),
        ).rejects.toBeInstanceOf(EnrollmentReject);
        expect(reuseRan).toBe(false);

        const failing = await deriveEnrollmentKeys(ke, context);
        await expect(
            withEnrollmentKeys(failing, () => {
                throw new Error('caller failure');
            }),
        ).rejects.toThrow('caller failure');
        expect(isEnrollmentKeysWiped(failing)).toBe(true);
        for (const field of Object.values(failing) as Uint8Array[]) {
            expect(field.every((byte) => byte === 0)).toBe(true);
        }
    });
});

describe('identity records', () => {
    it('decodes the fixture bodies and reproduces both fingerprints', async () => {
        const phone = decodeIdentityRecord(phoneBody, IdentityRole.PHONE);
        expect(phone.installationId).toEqual(
            bytes(obj(fixtureInput, 'phone_identity'), 'installation_id'),
        );
        expect(phone.label).toBe(str(obj(fixtureInput, 'phone_identity'), 'label'));
        expect(phone.deviceType).toBe(DeviceType.PHONE);
        expect(phone.identityPubkey).toEqual(bytes(fixtureOutputs, 'phone_identity_pub'));
        expect(phone.fingerprint).toEqual(bytes(fixtureOutputs, 'fp_self_phone'));
        expect(await recordFingerprint(phone, IdentityRole.PHONE)).toEqual(phone.fingerprint);
        expect(encodeIdentityRecord(phone, IdentityRole.PHONE)).toEqual(phoneBody);

        const client = decodeIdentityRecord(clientBody, IdentityRole.CLIENT);
        expect(client.label).toBe('');
        expect(client.deviceType).toBe(DeviceType.DESKTOP);
        expect(client.identityPubkey).toEqual(bytes(fixtureOutputs, 'client_identity_pub'));
        expect(client.fingerprint).toEqual(bytes(fixtureOutputs, 'fp_self_client'));
        expect(await recordFingerprint(client, IdentityRole.CLIENT)).toEqual(client.fingerprint);
        expect(encodeIdentityRecord(client, IdentityRole.CLIENT)).toEqual(clientBody);

        const pair = await pairFingerprint(phone.identityPubkey, client.identityPubkey);
        expect(pair).toEqual(bytes(fixtureOutputs, 'fp_pair'));
        expect(fingerprintDisplay(pair)).toBe(str(fixtureOutputs, 'fp_pair_display'));
    });

    it('creates the fixture body from its fields', async () => {
        const phone = await createIdentityRecord(
            {
                installationId: bytes(obj(fixtureInput, 'phone_identity'), 'installation_id'),
                label: str(obj(fixtureInput, 'phone_identity'), 'label'),
                deviceType: DeviceType.PHONE,
                identityPubkey: bytes(fixtureOutputs, 'phone_identity_pub'),
                nonce: bytes(obj(fixtureInput, 'phone_identity'), 'nonce'),
            },
            IdentityRole.PHONE,
        );
        expect(phone.fingerprint).toEqual(bytes(fixtureOutputs, 'fp_self_phone'));
        expect(encodeIdentityRecord(phone, IdentityRole.PHONE)).toEqual(phoneBody);
    });

    it('enforces the role-bound device_type', async () => {
        for (const deviceType of [DeviceType.DESKTOP, DeviceType.WATCH]) {
            await expect(recordFor(IdentityRole.PHONE, deviceType)).rejects.toBeInstanceOf(
                EnrollmentReject,
            );
            const record = await recordFor(IdentityRole.CLIENT, deviceType);
            expect(() => encodeIdentityRecord(record, IdentityRole.PHONE)).toThrow(
                EnrollmentReject,
            );
            expect(() => decodeIdentityRecord(encodeUnchecked(record), IdentityRole.PHONE)).toThrow(
                EnrollmentReject,
            );
        }

        const phone = await recordFor(IdentityRole.PHONE, DeviceType.PHONE);
        expect(() => encodeIdentityRecord(phone, IdentityRole.CLIENT)).toThrow(EnrollmentReject);
        expect(() => decodeIdentityRecord(encodeUnchecked(phone), IdentityRole.CLIENT)).toThrow(
            EnrollmentReject,
        );

        const unknown = encodeUnchecked(phone);
        const labelLength = (unknown[16] << 8) | unknown[17];
        unknown[18 + labelLength] = 0x03;
        expect(() => decodeIdentityRecord(unknown, IdentityRole.PHONE)).toThrow(EnrollmentReject);
        expect(() => decodeIdentityRecord(unknown, IdentityRole.CLIENT)).toThrow(EnrollmentReject);
    });

    it('enforces the record length bounds and safe text', async () => {
        const empty = await recordFor(IdentityRole.PHONE, DeviceType.PHONE, '');
        const full = await recordFor(IdentityRole.CLIENT, DeviceType.DESKTOP, 'a'.repeat(32));
        expect(encodeIdentityRecord(empty, IdentityRole.PHONE)).toHaveLength(132);
        expect(encodeIdentityRecord(full, IdentityRole.CLIENT)).toHaveLength(164);

        await expect(
            recordFor(IdentityRole.PHONE, DeviceType.PHONE, 'a'.repeat(33)),
        ).rejects.toBeInstanceOf(EnrollmentReject);
        expect(() => decodeIdentityRecord(new Uint8Array(131), IdentityRole.PHONE)).toThrow(
            EnrollmentReject,
        );
        expect(() => decodeIdentityRecord(new Uint8Array(165), IdentityRole.PHONE)).toThrow(
            EnrollmentReject,
        );
        expect(() =>
            decodeIdentityRecord(
                new Uint8Array([...encodeIdentityRecord(empty, IdentityRole.PHONE), 0x00]),
                IdentityRole.PHONE,
            ),
        ).toThrow(EnrollmentReject);

        await expect(
            recordFor(IdentityRole.PHONE, DeviceType.PHONE, 'a\u0001b'),
        ).rejects.toBeInstanceOf(EnrollmentReject);
        await expect(
            recordFor(IdentityRole.PHONE, DeviceType.PHONE, '\uD800'),
        ).rejects.toBeInstanceOf(EnrollmentReject);

        const badTag = {
            installationId: new Uint8Array(16),
            label: 'x',
            deviceType: DeviceType.PHONE,
            identityPubkey: new Uint8Array(65).fill(0x03),
            nonce: new Uint8Array(16),
        };
        await expect(createIdentityRecord(badTag, IdentityRole.PHONE)).rejects.toBeInstanceOf(
            EnrollmentReject,
        );
        await expect(
            createIdentityRecord(
                { ...badTag, identityPubkey: new Uint8Array(64) },
                IdentityRole.PHONE,
            ),
        ).rejects.toBeInstanceOf(EnrollmentReject);
    });

    it('stores nothing until the fingerprint and the point validator both pass', async () => {
        const good = encodeIdentityRecord(
            await recordFor(IdentityRole.PHONE, DeviceType.PHONE),
            IdentityRole.PHONE,
        );
        const validated: Uint8Array[] = [];
        const stored: IdentityRecord[] = [];
        await decodeIdentityRecordForStorage(
            good,
            IdentityRole.PHONE,
            (point) => {
                validated.push(point);
            },
            (record) => stored.push(record),
        );
        expect(validated).toHaveLength(1);
        expect(stored).toHaveLength(1);

        const bad = good.slice();
        bad[bad.length - 17] = (bad[bad.length - 17] + 1) & 0xff;
        const validatedBad: Uint8Array[] = [];
        const afterBad: IdentityRecord[] = [];
        await expect(
            decodeIdentityRecordForStorage(
                bad,
                IdentityRole.PHONE,
                (point) => {
                    validatedBad.push(point);
                },
                (record) => afterBad.push(record),
            ),
        ).rejects.toBeInstanceOf(EnrollmentReject);
        expect(validatedBad).toHaveLength(0);
        expect(afterBad).toHaveLength(0);
        expect(stored).toHaveLength(1);

        const afterInvalidPoint: IdentityRecord[] = [];
        await expect(
            decodeIdentityRecordForStorage(
                good,
                IdentityRole.PHONE,
                () => {
                    throw new Error('invalid point');
                },
                (record) => afterInvalidPoint.push(record),
            ),
        ).rejects.toThrow('invalid point');
        expect(afterInvalidPoint).toHaveLength(0);

        await expect(
            decodeFingerprintVerifiedIdentityRecord(good, IdentityRole.PHONE),
        ).resolves.toBeDefined();
    });

    it('rejects enroll_identity in every reconnect session class', () => {
        for (const sessionClass of Object.values(SessionClass)) {
            for (const direction of Object.values(Direction)) {
                expect(authorized(RecordType.ENROLL_IDENTITY, direction, sessionClass)).toBe(false);
            }
        }
        expect(
            authorized(RecordType.APP_HEARTBEAT, Direction.CLIENT_TO_PHONE, SessionClass.FULL),
        ).toBe(true);
    });
});

describe('enrollment transcript and fingerprints', () => {
    it('matches the fixture ET and binds both identities and the context', async () => {
        expect(await transcriptOf()).toEqual(et);

        const alteredIdentity = phoneBody.slice();
        alteredIdentity[alteredIdentity.length - 1] ^= 0x01;
        const alteredContext = context.slice();
        alteredContext[alteredContext.length - 1] ^= 0x01;
        const alteredIdentityEt = await transcriptOf(context, alteredIdentity);
        const alteredContextEt = await transcriptOf(alteredContext);
        expect(hexOf(alteredIdentityEt)).not.toBe(hexOf(et));
        expect(hexOf(alteredContextEt)).not.toBe(hexOf(et));

        // OTP2-CRY-019a: every activation proof is HMAC(K_act, ET || stage).
        const keys = await deriveEnrollmentKeys(ke, context);
        try {
            const prepare = await activationMac(keys.phoneActivationKey, et, 0x00);
            expect(prepare).toEqual(bytes(fixtureOutputs, 'act_prepare_mac'));
            expect(
                hexOf(await activationMac(keys.phoneActivationKey, alteredIdentityEt, 0x00)),
            ).not.toBe(hexOf(prepare));
            expect(
                hexOf(await activationMac(keys.phoneActivationKey, alteredContextEt, 0x00)),
            ).not.toBe(hexOf(prepare));
            expect(await activationMac(keys.clientActivationKey, et, 0x01)).toEqual(
                bytes(fixtureOutputs, 'act_commit_mac'),
            );
            expect(await activationMac(keys.phoneActivationKey, et, 0x02)).toEqual(
                bytes(fixtureOutputs, 'act_complete_mac'),
            );
            expect(await activationMac(keys.clientActivationKey, et, 0x03)).toEqual(
                bytes(fixtureOutputs, 'act_ack_mac'),
            );
        } finally {
            wipeEnrollmentKeys(keys);
        }
    });

    it('context mismatch changes the schedule and the transcript', async () => {
        const encoding = JSON.parse(
            readFileSync(
                new URL('../../../protocol/v1/fixtures/crypto.json', import.meta.url),
                'utf8',
            ),
        ) as {
            fixtures: {
                id: string;
                expected: { stage?: string; proof?: string };
                input: { deviation: { value: string } };
            }[];
        };
        const negative = encoding.fixtures.find(
            (entry) => entry.id === 'veles-neg-context-mismatch',
        );
        if (negative === undefined) throw new Error('missing veles-neg-context-mismatch');
        expect(negative.expected.stage).toBe('context');
        expect(negative.expected.proof).toBe('confirmation_mismatch');
        const mismatched = hex(negative.input.deviation.value);

        const base = await deriveEnrollmentKeys(ke, context);
        const other = await deriveEnrollmentKeys(ke, mismatched);
        try {
            expect(base.aToBAesKey).not.toEqual(other.aToBAesKey);
            expect(base.bToAAesKey).not.toEqual(other.bToAAesKey);
            expect(base.phoneActivationKey).not.toEqual(other.phoneActivationKey);
            expect(hexOf(await transcriptOf(mismatched))).not.toBe(hexOf(et));
        } finally {
            wipeEnrollmentKeys(base);
            wipeEnrollmentKeys(other);
        }
    });
});

interface EncodingFixture {
    id: string;
    input: { layout: string; bytes: string };
    expected: {
        accepted: boolean;
        fields?: { installation_id: string; label: string; device_type: string };
    };
}

describe('shared enroll_identity fixtures', () => {
    const document = JSON.parse(
        readFileSync(
            new URL('../../../protocol/v1/fixtures/encoding.json', import.meta.url),
            'utf8',
        ),
    ) as { fixtures: EncodingFixture[] };
    const fixtures = document.fixtures.filter(
        (fixture) => fixture.input.layout === 'enroll_identity',
    );

    it('has the shared fixture set', () => {
        expect(fixtures.length).toBeGreaterThan(0);
    });

    for (const fixture of fixtures) {
        it(`${fixture.id}_${fixture.expected.accepted ? 'accepts' : 'rejects'}`, () => {
            const body = hex(fixture.input.bytes);
            if (fixture.expected.accepted) {
                const fields = fixture.expected.fields;
                if (fields === undefined) throw new Error('accepted fixture must carry fields');
                const role =
                    fields.device_type === '0x00' ? IdentityRole.PHONE : IdentityRole.CLIENT;
                const decoded = decodeIdentityRecord(body, role);
                expect(encodeIdentityRecord(decoded, role)).toEqual(body);
                expect(decoded.label).toBe(fields.label);
                expect('0x' + hexOf(decoded.installationId)).toBe(fields.installation_id);
            } else {
                for (const role of Object.values(IdentityRole)) {
                    expect(() => decodeIdentityRecord(body, role)).toThrow(EnrollmentReject);
                }
            }
        });
    }
});
