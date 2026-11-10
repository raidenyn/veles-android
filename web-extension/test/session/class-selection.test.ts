import { describe, expect, it } from 'vitest';
import { RecordType } from '../../src/session/wire';
import type { TrustState } from '../../src/trust/model';
import {
    PEER_STATE_ACTIVE,
    PEER_STATE_BELOW_ACTIVE,
    PhoneHarness,
    terminatedReason,
} from './client-harness';

type LocalRecordState = TrustState;

interface Row {
    local: LocalRecordState;
    peerState: number;
    expectedClass: string;
    upgrades: boolean;
    outboundPeerState: number;
}

// Every §11.3 client (C) row of the class-selection table.
const ROWS: Row[] = [
    {
        local: 'active',
        peerState: PEER_STATE_ACTIVE,
        expectedClass: 'FULL',
        upgrades: false,
        outboundPeerState: 0x01,
    },
    {
        local: 'active',
        peerState: PEER_STATE_BELOW_ACTIVE,
        expectedClass: 'FULL_PENDING_PEER',
        upgrades: false,
        outboundPeerState: 0x01,
    },
    {
        local: 'active_activation_pending',
        peerState: PEER_STATE_ACTIVE,
        expectedClass: 'FULL',
        upgrades: true,
        outboundPeerState: 0x01,
    },
    {
        local: 'active_activation_pending',
        peerState: PEER_STATE_BELOW_ACTIVE,
        expectedClass: 'ACTIVATION_RECOVERY',
        upgrades: false,
        outboundPeerState: 0x01,
    },
    {
        local: 'pending',
        peerState: PEER_STATE_ACTIVE,
        expectedClass: 'ACTIVATION_RECOVERY',
        upgrades: false,
        outboundPeerState: 0x02,
    },
    {
        local: 'pending',
        peerState: PEER_STATE_BELOW_ACTIVE,
        expectedClass: 'ACTIVATION_RECOVERY',
        upgrades: false,
        outboundPeerState: 0x02,
    },
];

describe('client class selection', () => {
    for (const row of ROWS) {
        it(`selects ${row.expectedClass} for ${row.local} with peer_state ${row.peerState}`, async () => {
            const harness = await PhoneHarness.create({ state: row.local });
            const { drive, authenticated } = await harness.openSession({
                peerState: row.peerState,
            });

            expect(drive.clientConfirm.body[32]).toBe(row.outboundPeerState);
            expect(authenticated).toMatchObject({
                kind: 'authenticated',
                sessionClass: row.expectedClass,
                peerId: harness.peerId,
            });
            expect(harness.store.get(harness.peerId)?.state).toBe(
                row.upgrades ? 'active' : row.local,
            );
            expect(harness.calls.upgrade).toHaveLength(row.upgrades ? 1 : 0);
            expect(harness.engine.isOpen).toBe(true);
        });
    }

    it('never upgrades an activation_recovery session mid-session', async () => {
        const harness = await PhoneHarness.create({ state: 'pending' });
        await harness.openSession({ peerState: PEER_STATE_ACTIVE });

        // The local record becoming active after session_open cannot change the
        // fixed class: a push (full-only) is still refused as unauthorized, not
        // routed to the fail-closed unhandled path.
        const record = harness.store.get(harness.peerId);
        if (record !== undefined) record.state = 'active';

        expect(harness.calls.upgrade).toHaveLength(0);
        expect(await harness.engine.onTick()).toEqual([]);
        const effects = await harness.engine.onInbound(
            await harness.phoneRecord(RecordType.PUSH_EVENT, 1n),
        );
        expect(terminatedReason(effects)).toBe('authorization');
        expect(effects.some((effect) => effect.kind === 'send')).toBe(false);
    });
});
