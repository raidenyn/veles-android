import { describe, expect, it } from 'vitest';
import {
    RECORD_HEADER_BYTES,
    RECORD_MAX_CIPHERTEXT_BYTES,
    RECORD_TAG_BYTES,
    openRecord,
    sealRecord,
} from '../../src/session/record';
import { SessionReject } from '../../src/session/reject';
import {
    AppErrorCode,
    Direction,
    RecordType,
    directionFromWire,
    recordTypeFromWire,
} from '../../src/session/wire';
import {
    RECORD_CONFIRM,
    RECORD_PUSH,
    bytes,
    deviation,
    ids,
    input,
    outputs,
    str,
    wire,
} from './fixtures';

const DIRECTION_OFFSET = 2;
const TYPE_OFFSET = 3;
const SEQUENCE_OFFSET = 5;
const LENGTH_OFFSET = 13;
const SESSION_OFFSET = 15;

interface Vector {
    key: CryptoKey;
    prefix: Uint8Array;
    sid: Uint8Array;
    direction: Direction;
    type: number;
    sequence: bigint;
    body: Uint8Array;
    record: Uint8Array;
}

function u64be(value: bigint): Uint8Array {
    const encoded = new Uint8Array(8);
    let remaining = value;
    for (let index = 7; index >= 0; index -= 1) {
        encoded[index] = Number(remaining & 0xffn);
        remaining >>= 8n;
    }
    return encoded;
}

function directionOf(value: number): Direction {
    const direction = directionFromWire(value);
    if (direction === null) throw new Error(`unknown fixture direction ${value}`);
    return direction;
}

function recordTypeOf(value: number): number {
    const type = recordTypeFromWire(value);
    if (type === null) throw new Error(`unknown fixture record type ${value}`);
    return type;
}

async function vector(id: string): Promise<Vector> {
    const fixtureInput = input(id);
    const rawKey = bytes(fixtureInput, 'key');
    return {
        key: await crypto.subtle.importKey('raw', rawKey, { name: 'AES-GCM' }, false, [
            'encrypt',
            'decrypt',
        ]),
        prefix: bytes(fixtureInput, 'nonce_prefix'),
        sid: bytes(fixtureInput, 'session_id_prefix'),
        direction: directionOf(wire(str(fixtureInput, 'direction'))),
        type: recordTypeOf(wire(str(fixtureInput, 'record_type'))),
        sequence: BigInt(str(fixtureInput, 'sequence')),
        body: bytes(fixtureInput, 'body'),
        record: bytes(outputs(id), 'record'),
    };
}

function seal(
    v: Vector,
    body: Uint8Array = v.body,
    sequence: bigint = v.sequence,
): Promise<Uint8Array> {
    return sealRecord(v.key, v.prefix, v.sid, v.direction, v.type, sequence, body);
}

function open(v: Vector, record: Uint8Array, expected: bigint = v.sequence) {
    return openRecord(v.key, v.prefix, v.sid, v.direction, expected, record);
}

/** A valid AEAD record whose plaintext body bypasses the §7.3 body rules. */
async function sealRaw(
    v: Vector,
    type: number,
    body: Uint8Array,
    sequence = 0n,
): Promise<Uint8Array> {
    const aad = new Uint8Array(RECORD_HEADER_BYTES);
    aad[0] = 1;
    aad[1] = 1;
    aad[DIRECTION_OFFSET] = v.direction;
    aad[TYPE_OFFSET] = (type >>> 8) & 0xff;
    aad[TYPE_OFFSET + 1] = type & 0xff;
    aad.set(u64be(sequence), SEQUENCE_OFFSET);
    aad[LENGTH_OFFSET] = (body.length >>> 8) & 0xff;
    aad[LENGTH_OFFSET + 1] = body.length & 0xff;
    aad.set(v.sid, SESSION_OFFSET);
    const iv = new Uint8Array(12);
    iv.set(v.prefix, 0);
    iv.set(u64be(sequence), 4);
    const ciphertext = new Uint8Array(
        await crypto.subtle.encrypt(
            { name: 'AES-GCM', iv, additionalData: aad, tagLength: 128 },
            v.key,
            body,
        ),
    );
    const record = new Uint8Array(aad.length + ciphertext.length);
    record.set(aad, 0);
    record.set(ciphertext, aad.length);
    return record;
}

describe('protected records', () => {
    it('seals the fixture records', async () => {
        for (const id of [RECORD_CONFIRM, RECORD_PUSH]) {
            const v = await vector(id);
            const sealed = await seal(v);

            expect(sealed, id).toEqual(v.record);
            expect(sealed.subarray(0, RECORD_HEADER_BYTES), `${id} header`).toEqual(
                bytes(outputs(id), 'header'),
            );
            expect(
                sealed.subarray(RECORD_HEADER_BYTES, sealed.length - RECORD_TAG_BYTES),
                `${id} ciphertext`,
            ).toEqual(bytes(outputs(id), 'ciphertext'));
            expect(sealed.subarray(sealed.length - RECORD_TAG_BYTES), `${id} tag`).toEqual(
                bytes(outputs(id), 'tag'),
            );

            const opened = await open(v, sealed);
            expect(opened.type).toBe(v.type);
            expect(opened.seq).toBe(v.sequence);
            expect(opened.body).toEqual(v.body);
        }
    });

    it('enforces the fixed §7.3 body rules on seal', async () => {
        const clientVector = await vector(RECORD_CONFIRM);
        const phoneVector = await vector(RECORD_PUSH);

        // Empty-body framework and request records.
        for (const type of [
            RecordType.APP_HEARTBEAT,
            RecordType.PULL_REQUEST,
            RecordType.REMOVAL_REQUEST,
            RecordType.REMOVAL_RESPONSE,
        ]) {
            await expect(
                sealRecord(
                    clientVector.key,
                    clientVector.prefix,
                    clientVector.sid,
                    clientVector.direction,
                    type,
                    0n,
                    Uint8Array.of(1),
                ),
            ).rejects.toBeInstanceOf(SessionReject);
        }

        // Fixed widths: the confirm is 33 bytes, each MAC and ack is 32.
        await expect(
            sealRecord(
                clientVector.key,
                clientVector.prefix,
                clientVector.sid,
                clientVector.direction,
                RecordType.RECONNECT_CONFIRM,
                0n,
                new Uint8Array(32),
            ),
        ).rejects.toBeInstanceOf(SessionReject);
        for (const type of [
            RecordType.ACTIVATION_COMMIT,
            RecordType.ACTIVATION_ACK,
            RecordType.EVENT_ACK,
        ]) {
            await expect(sealRaw(clientVector, type, new Uint8Array(31))).resolves.toBeTruthy();
            await expect(
                sealRecord(
                    clientVector.key,
                    clientVector.prefix,
                    clientVector.sid,
                    clientVector.direction,
                    type,
                    0n,
                    new Uint8Array(31),
                ),
            ).rejects.toBeInstanceOf(SessionReject);
        }
        for (const type of [RecordType.ACTIVATION_COMPLETE, RecordType.ENROLLMENT_PREPARE]) {
            await expect(
                sealRecord(
                    phoneVector.key,
                    phoneVector.prefix,
                    phoneVector.sid,
                    phoneVector.direction,
                    type,
                    0n,
                    new Uint8Array(31),
                ),
            ).rejects.toBeInstanceOf(SessionReject);
        }

        // `phone_policy_state` is exactly two bytes with a known policy and no reserved bits.
        for (const body of [Uint8Array.of(0), Uint8Array.of(3, 0), Uint8Array.of(0, 0x10)]) {
            await expect(
                sealRecord(
                    phoneVector.key,
                    phoneVector.prefix,
                    phoneVector.sid,
                    phoneVector.direction,
                    RecordType.PHONE_POLICY_STATE,
                    0n,
                    body,
                ),
            ).rejects.toBeInstanceOf(SessionReject);
        }
    });

    it('enforces the fixed §7.3 body rules on open', async () => {
        const v = await vector(RECORD_CONFIRM);

        // A peer can place any plaintext inside a valid AEAD record.
        for (const [type, body] of [
            [RecordType.APP_HEARTBEAT, Uint8Array.of(1)],
            [RecordType.PULL_REQUEST, Uint8Array.of(1, 2)],
            [RecordType.EVENT_ACK, new Uint8Array(31)],
            [RecordType.APP_ERROR, Uint8Array.of(AppErrorCode.PROTOCOL_VIOLATION, 0, 2, 0x41)],
        ] as const) {
            const record = await sealRaw(v, type, body);
            await expect(
                openRecord(v.key, v.prefix, v.sid, v.direction, 0n, record),
            ).rejects.toBeInstanceOf(SessionReject);
        }

        // Boundary: 64 bytes of printable text is accepted on both seal and open.
        const longest = new Uint8Array(64).fill(0x61);
        const validBody = Uint8Array.of(
            AppErrorCode.PROTOCOL_VIOLATION,
            0,
            longest.length,
            ...longest,
        );
        const sealed = await sealRecord(
            v.key,
            v.prefix,
            v.sid,
            v.direction,
            RecordType.APP_ERROR,
            0n,
            validBody,
        );
        expect((await openRecord(v.key, v.prefix, v.sid, v.direction, 0n, sealed)).body).toEqual(
            validBody,
        );

        // 65 bytes and every control code are prohibited by OTP2-ENC-003.
        for (const reason of [new Uint8Array(65).fill(0x61), Uint8Array.of(0x41, 0x00, 0x42)]) {
            const body = Uint8Array.of(
                AppErrorCode.PROTOCOL_VIOLATION,
                (reason.length >>> 8) & 0xff,
                reason.length & 0xff,
                ...reason,
            );
            await expect(
                sealRecord(v.key, v.prefix, v.sid, v.direction, RecordType.APP_ERROR, 0n, body),
            ).rejects.toBeInstanceOf(SessionReject);
            const record = await sealRaw(v, RecordType.APP_ERROR, body);
            await expect(
                openRecord(v.key, v.prefix, v.sid, v.direction, 0n, record),
            ).rejects.toBeInstanceOf(SessionReject);
        }
    });

    it('rejects the negative record fixtures', async () => {
        const negatives = ids('veles-neg-record-');
        expect(negatives.length).toBe(4);
        for (const id of negatives) {
            const fixtureDeviation = deviation(id);
            const v = await vector(str(fixtureDeviation, 'negates'));
            const tampered = v.record.slice();
            switch (str(fixtureDeviation, 'type')) {
                case 'bad_tag':
                    tampered[tampered.length - 1] ^= 1;
                    break;
                case 'mutated_session':
                    tampered[SESSION_OFFSET] ^= 1;
                    break;
                case 'mutated_direction':
                    tampered[DIRECTION_OFFSET] = Direction.PHONE_TO_CLIENT;
                    break;
                case 'mutated_sequence':
                    tampered.set(u64be(v.sequence + 1n), SEQUENCE_OFFSET);
                    break;
                default:
                    throw new Error(`unexpected deviation for ${id}`);
            }
            await expect(open(v, tampered), id).rejects.toBeInstanceOf(SessionReject);
        }
    });

    it('rejects every tampered header and ciphertext byte', async () => {
        const v = await vector(RECORD_PUSH);
        for (let index = 0; index < v.record.length - RECORD_TAG_BYTES; index += 1) {
            const tampered = v.record.slice();
            tampered[index] ^= 1;
            await expect(open(v, tampered), `byte ${index}`).rejects.toBeInstanceOf(SessionReject);
        }
    });

    it('rejects a ciphertext length mismatch', async () => {
        const v = await vector(RECORD_CONFIRM);
        const tampered = v.record.slice();
        const ciphertextLength = tampered.length - RECORD_HEADER_BYTES - RECORD_TAG_BYTES;
        tampered[LENGTH_OFFSET] = (ciphertextLength - 1) >>> 8;
        tampered[LENGTH_OFFSET + 1] = ciphertextLength - 1;
        await expect(open(v, tampered)).rejects.toBeInstanceOf(SessionReject);
    });

    it('rejects records over 4096 bytes', async () => {
        const v = await vector(RECORD_PUSH);
        const oversized = new Uint8Array(4097);
        oversized.set(v.record);
        await expect(open(v, oversized)).rejects.toBeInstanceOf(SessionReject);
        await expect(
            seal(v, new Uint8Array(RECORD_MAX_CIPHERTEXT_BYTES + 1)),
        ).rejects.toBeInstanceOf(SessionReject);
    });

    it('rejects an unknown app error code', async () => {
        const v = await vector(RECORD_CONFIRM);
        const unknownCode = Uint8Array.of(0x06, 0x00, 0x00);
        // Sealed without the codec's body rules so the open path's own check is exercised.
        const unknownRecord = await sealRaw(v, RecordType.APP_ERROR, unknownCode);
        await expect(
            openRecord(v.key, v.prefix, v.sid, v.direction, 0n, unknownRecord),
        ).rejects.toBeInstanceOf(SessionReject);

        const validBody = Uint8Array.of(AppErrorCode.PROTOCOL_VIOLATION, 0x00, 0x00);
        const validRecord = await sealRecord(
            v.key,
            v.prefix,
            v.sid,
            v.direction,
            RecordType.APP_ERROR,
            0n,
            validBody,
        );
        expect(
            (await openRecord(v.key, v.prefix, v.sid, v.direction, 0n, validRecord)).body,
        ).toEqual(validBody);
    });
});
