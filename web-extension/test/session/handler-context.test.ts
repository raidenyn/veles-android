import { describe, expect, it } from 'vitest';
import type { ApplicationHandler, SessionContext, SessionEffect } from '../../src/session/engine';
import { AppErrorCode, RecordType, SessionClass } from '../../src/session/wire';
import {
    PEER_STATE_BELOW_ACTIVE,
    PhoneHarness,
    appErrorBody,
    hexOf,
    sendBytes,
} from './client-harness';
import { RECONNECT, bytes, input } from './fixtures';

// OTP-10 plan 9b: the client engine hands an application or activation handler the fixed
// session context at the `Authenticated` gate, and a registered handler can send an
// unprompted record without a prior inbound record. An activation record with no handler
// keeps the OTP-09 fail-closed rule (`app_error` protocol_violation).

const EMPTY = new Uint8Array(0);

function capturingHandler(capture: (context: SessionContext) => void): ApplicationHandler {
    const handler: ApplicationHandler = {
        handles: () => false,
        handle: async () => undefined,
        forSession: (context) => {
            capture(context);
            return handler;
        },
    };
    return handler;
}

describe('handler session context', () => {
    it('handler receives the session context on authenticated', async () => {
        const et = bytes(input(RECONNECT), 'et');
        let captured: SessionContext | undefined;
        const harness = await PhoneHarness.create({
            state: 'pending',
            et,
            handler: capturingHandler((context) => {
                captured = context;
            }),
        });

        const { authenticated } = await harness.openSession({
            peerState: PEER_STATE_BELOW_ACTIVE,
        });
        expect(authenticated).toMatchObject({
            kind: 'authenticated',
            sessionClass: SessionClass.ACTIVATION_RECOVERY,
        });

        expect(captured).toBeDefined();
        expect(captured?.sessionClass).toBe(SessionClass.ACTIVATION_RECOVERY);
        expect(captured?.peerId).toBe(harness.peerId);
        expect(hexOf(captured?.sessionIdPrefix ?? EMPTY)).toBe(hexOf(harness.prefix));
        expect(captured?.et).toEqual(et);
        expect(captured?.peerState).toBe(PEER_STATE_BELOW_ACTIVE);

        // OTP2-CRY-019f: a non-full class keeps the live recovery holder derived from ET.
        expect(captured?.recovery).not.toBeNull();
        expect(captured?.recovery?.kRecClient.some((byte) => byte !== 0)).toBe(true);
        expect(captured?.recovery?.kRecPhone.some((byte) => byte !== 0)).toBe(true);
    });

    it('handler can send an unprompted record on authenticated', async () => {
        const et = bytes(input(RECONNECT), 'et');
        let captured: SessionContext | undefined;
        const harness = await PhoneHarness.create({
            state: 'active',
            et,
            handler: capturingHandler((context) => {
                captured = context;
            }),
        });

        const { authenticated } = await harness.openSession({
            peerState: PEER_STATE_BELOW_ACTIVE,
        });
        expect(authenticated).toMatchObject({
            kind: 'authenticated',
            sessionClass: SessionClass.FULL_PENDING_PEER,
        });
        expect(captured?.peerState).toBe(PEER_STATE_BELOW_ACTIVE);

        // OTP2-STA-010b step 5: the active-family client saw peer_state 0x02, so it owes the
        // ack. No inbound application record was processed; the handler sends it from its
        // context.
        const body = new Uint8Array(32).fill(0x22);
        const effects = await harness.engine.sendApplication(RecordType.ACTIVATION_ACK, body);

        expect(effects).toHaveLength(1);
        const opened = await harness.openClientApplication(sendBytes(effects), 1n);
        expect(opened.type).toBe(RecordType.ACTIVATION_ACK);
        expect(opened.body).toEqual(body);
        expect(harness.engine.isOpen).toBe(true);
    });

    it('activation record without a handler ends with protocol violation', async () => {
        const harness = await PhoneHarness.create({
            state: 'pending',
            et: bytes(input(RECONNECT), 'et'),
        });
        await harness.openSession({ peerState: PEER_STATE_BELOW_ACTIVE });

        // No activation handler is registered: the authorized activation record must keep the
        // OTP-09 fail-closed rule and change no trust state.
        const effects: SessionEffect[] = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.ENROLLMENT_PREPARE, 1n, new Uint8Array(32)),
        );

        expect(effects).toHaveLength(3);
        const error = await harness.openClientApplication(sendBytes(effects), 1n);
        expect(error.type).toBe(RecordType.APP_ERROR);
        expect(error.body).toEqual(appErrorBody(AppErrorCode.PROTOCOL_VIOLATION));
        expect(effects[1]).toMatchObject({ kind: 'terminated', reason: 'unhandled' });
        expect(effects[2]).toEqual({ kind: 'close' });
        expect(effects.some((effect) => effect.kind === 'deliver')).toBe(false);
        expect(harness.engine.isOpen).toBe(false);
    });
});
