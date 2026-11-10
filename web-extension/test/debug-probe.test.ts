import { afterEach, describe, expect, it, vi } from 'vitest';

import { ConnectorApp } from '../src/connector';
import { createDevelopmentProbe, type DevelopmentProbe } from '../src/debug/probe';
import { injectActivePeer } from '../src/debug/inject';
import { DEBUG_READOUT_TEST_ID } from '../src/debug/readout';
import { RecordType } from '../src/session/wire';
import { bytes, fingerprint, hex } from '../src/trust/model';
import {
    HANDLE_CONNECTION,
    HANDLE_CONNECTION_2,
    HANDLE_DEVICE,
    connectedEvent,
    disconnectedEvent,
    readyEvent,
} from './connector/fixtures';
import {
    TestView,
    ackSend,
    answerHandshake,
    createTestController,
    createTestTrust,
    decodeRecordHeader,
    emitRecord,
    flush,
    nextSend,
    sessionEngineDeps,
    type TestController,
    type TestTrust,
} from './connector/session-harness';
import { PEER_STATE_BELOW_ACTIVE, TestPhone } from './session/client-harness';

// ---------------------------------------------------------------------------
// Minimal document surface: the readout is the only DOM the probe touches.
// ---------------------------------------------------------------------------

class FakeElement {
    readonly dataset: Record<string, string> = {};
    readonly children: FakeElement[] = [];
    textContent = '';
    hidden = false;

    constructor(readonly tagName: string) {}

    append(...nodes: FakeElement[]): void {
        this.children.push(...nodes);
    }

    setAttribute(name: string, value: string): void {
        this.dataset[name] = value;
    }
}

class FakeDocument {
    readonly body = new FakeElement('body');
    createElement(tagName: string): FakeElement {
        return new FakeElement(tagName);
    }
    querySelector(): FakeElement | null {
        return null;
    }
    findReadout(): FakeElement | undefined {
        return this.body.children.find((child) => child.dataset.testid === DEBUG_READOUT_TEST_ID);
    }
}

interface ProbeFixture extends TestController {
    app: ConnectorApp;
    probe: DevelopmentProbe;
    document: FakeDocument;
    trust: TestTrust;
    phone: TestPhone;
}

async function createProbeFixture(phoneOverride?: TestPhone): Promise<ProbeFixture> {
    const context = createTestController();
    const trust = await createTestTrust(context.clock);
    const phone = phoneOverride ?? (await TestPhone.create());
    const document = new FakeDocument();
    const probe = createDevelopmentProbe(document as unknown as Document, {
        createDebugRepository: () => {
            const clone = trust.clone();
            clone.bindVerifier(async () => true);
            return clone.repository;
        },
    });
    const app = new ConnectorApp({
        controller: context.controller,
        view: new TestView(),
        setTimeout: context.clock.setTimeout,
        clearTimeout: context.clock.clearTimeout,
        session: probe.sessionOptions({
            createRepository(verifier) {
                trust.bindVerifier(verifier);
                return trust.repository;
            },
            createEngineDeps(handle, peerId, localInstallationId) {
                return sessionEngineDeps(trust, context.clock, localInstallationId)(handle, peerId);
            },
        }),
    });
    if (app.sessions === null) throw new Error('session manager unavailable');
    probe.install(app, app.sessions, context.controller);
    const started = app.start();
    const posted = await context.currentPort().nextPosted();
    await context.currentPort().emit(readyEvent({ request_id: posted.request_id as string }));
    await started;
    await flush();
    return { ...context, app, probe, document, trust, phone };
}

function debugHook() {
    const hook = globalThis.velesDebug;
    if (hook === undefined) throw new Error('velesDebug is not installed');
    return hook;
}

async function connectAndAuthenticate(
    fixture: ProbeFixture,
    peerId: string,
    connectionHandle: string = HANDLE_CONNECTION,
    phone: TestPhone = fixture.phone,
    peerState?: number,
) {
    const port = fixture.currentPort();
    const connecting = debugHook().connect(HANDLE_DEVICE, peerId);
    const posted = await port.nextPosted();
    if (posted.type !== 'connect') throw new Error('expected a connect request');
    await port.emit(
        connectedEvent({
            request_id: posted.request_id as string,
            connection_handle: connectionHandle,
        }),
    );
    const side = await answerHandshake(fixture, {
        phone,
        connectionHandle,
        ...(peerState === undefined ? {} : { peerState }),
    });
    await connecting;
    return side;
}

/** Independent §8.1 event encoder: the test never reuses the probe's writer. */
function encodeEvent(fields: {
    phoneInstallationId: string;
    eventId: Uint8Array;
    receiptEpochSeconds: bigint;
    remainingLifetime: number;
    code: string;
    merchant: string;
    amountMagnitude: bigint;
    amountScale: number;
    currency: string;
}): Uint8Array {
    const encoder = new TextEncoder();
    const code = encoder.encode(fields.code);
    const merchant = encoder.encode(fields.merchant);
    const currency = encoder.encode(fields.currency);
    const body = new Uint8Array(
        16 + 16 + 8 + 4 + 2 + code.length + 2 + merchant.length + 8 + 1 + currency.length,
    );
    let offset = 0;
    body.set(
        Uint8Array.from(fields.phoneInstallationId.match(/../g) ?? [], (b) => parseInt(b, 16)),
        offset,
    );
    offset += 16;
    body.set(fields.eventId, offset);
    offset += 16;
    const view = new DataView(body.buffer, body.byteOffset, body.byteLength);
    view.setBigUint64(offset, fields.receiptEpochSeconds);
    offset += 8;
    view.setUint32(offset, fields.remainingLifetime);
    offset += 4;
    view.setUint16(offset, code.length);
    offset += 2;
    body.set(code, offset);
    offset += code.length;
    view.setUint16(offset, merchant.length);
    offset += 2;
    body.set(merchant, offset);
    offset += merchant.length;
    view.setBigUint64(offset, fields.amountMagnitude);
    offset += 8;
    view.setUint8(offset, fields.amountScale);
    offset += 1;
    body.set(currency, offset);
    return body;
}

function readoutText(fixture: ProbeFixture): string {
    return fixture.document.findReadout()?.textContent ?? '';
}

const EVENT_ID = new Uint8Array(16).fill(0x5a);

afterEach(() => {
    delete (globalThis as { velesDebug?: unknown }).velesDebug;
});

describe('development probe', () => {
    it('cross-installed peers authenticate as full', async () => {
        // Side B is a separate trust store. Its own identity key signs the phone half of the
        // handshake, so this test cannot pass unless B's exported identity is the one used.
        const otherSide = await createTestTrust();
        const otherPublicKey = await crypto.subtle.importKey(
            'raw',
            bytes(otherSide.identity.publicKey, 65),
            { name: 'ECDSA', namedCurve: 'P-256' },
            true,
            ['verify'],
        );
        const otherPhone = await TestPhone.create({
            identity: { privateKey: otherSide.identity.privateKey, publicKey: otherPublicKey },
            installationId: bytes(otherSide.identity.installationId, 16),
        });
        expect(hex(otherPhone.installationId)).toBe(otherSide.identity.installationId);
        expect(hex(otherPhone.publicKey)).toBe(otherSide.identity.publicKey);

        const fixture = await createProbeFixture(otherPhone);
        const hook = debugHook();

        // Each side exports its installation id and identity key.
        const connector = await hook.exportIdentity();
        expect(connector.installationId).toMatch(/^[0-9a-f]{32}$/);
        expect(connector.publicKey).toMatch(/^04[0-9a-f]{128}$/);
        const phoneIdentity = {
            installationId: hex(otherPhone.installationId),
            publicKey: hex(otherPhone.publicKey),
        };

        // Inject each side's exported identity into the other side's trust store.
        await hook.injectPeer(phoneIdentity.installationId, phoneIdentity.publicKey);
        const otherDebug = otherSide.clone();
        otherDebug.bindVerifier(async () => true);
        await injectActivePeer(
            otherDebug.repository,
            connector.installationId,
            connector.publicKey,
        );

        // A's connector store holds exactly B's exported identity: the same key that signs
        // the phone half above. The A-side lookup reads this store.
        const peers = await fixture.trust.peers();
        const injected = peers.find((peer) => peer.installationId === phoneIdentity.installationId);
        expect(injected).toBeDefined();
        expect(injected?.installationId).toBe(otherSide.identity.installationId);
        expect(injected?.publicKey).toBe(otherSide.identity.publicKey);
        expect(injected?.publicKey).toBe(hex(otherPhone.publicKey));
        expect(injected?.state).toBe('active');
        expect(injected?.fingerprint).toBe(
            await fingerprint(otherSide.identity.publicKey, connector.publicKey),
        );
        // B's store holds exactly A's exported identity.
        const connectorRecord = (await otherSide.peers()).find(
            (peer) => peer.installationId === connector.installationId,
        );
        expect(connectorRecord?.publicKey).toBe(connector.publicKey);
        expect(connectorRecord?.state).toBe('active');

        // The production lookup function reads the peers above; assert it returns the
        // injected record with the signing key.
        const lookedUp = await sessionEngineDeps(
            fixture.trust,
            fixture.clock,
            bytes(connector.installationId, 16),
        )(HANDLE_CONNECTION, phoneIdentity.installationId).lookupPhone(
            phoneIdentity.installationId,
        );
        expect(lookedUp?.publicKey).toEqual(bytes(otherSide.identity.publicKey, 65));
        expect(lookedUp?.state).toBe('active');

        const side = await connectAndAuthenticate(fixture, phoneIdentity.installationId);
        // A `full` class is the only class that authorizes pull_request; an
        // activation-recovery session would reject it.
        const port = fixture.currentPort();
        if (fixture.app.sessions === null) throw new Error('session manager unavailable');
        const pull = fixture.app.sessions.sendApplication(
            HANDLE_CONNECTION,
            RecordType.PULL_REQUEST,
            new Uint8Array(0),
        );
        const request = await nextSend(port);
        expect(decodeRecordHeader(request.bytes)?.type).toBe(RecordType.PULL_REQUEST);
        await ackSend(port, request);
        await expect(pull).resolves.toBeUndefined();
        expect(side.prefix).toHaveLength(8);
    });

    it('pull selects the single full session, not recovery or handshaking', async () => {
        const fixture = await createProbeFixture();
        const pendingPhone = await TestPhone.create();
        const recoveryPeer = await fixture.trust.addPeer(pendingPhone, {
            state: 'active_activation_pending',
        });
        // The recovery session is opened first and stays open while the full one connects.
        await connectAndAuthenticate(
            fixture,
            recoveryPeer,
            HANDLE_CONNECTION_2,
            pendingPhone,
            PEER_STATE_BELOW_ACTIVE,
        );
        await debugHook().injectPeer(
            hex(fixture.phone.installationId),
            hex(fixture.phone.publicKey),
        );
        const fullSide = await connectAndAuthenticate(
            fixture,
            hex(fixture.phone.installationId),
            HANDLE_CONNECTION,
        );
        const port = fixture.currentPort();

        const pulled = debugHook().pull();
        const request = await nextSend(port);
        expect(request.handle).toBe(HANDLE_CONNECTION);
        expect(decodeRecordHeader(request.bytes)?.type).toBe(RecordType.PULL_REQUEST);
        await ackSend(port, request);

        const body = Uint8Array.from([
            1,
            ...encodeEvent({
                phoneInstallationId: hex(fixture.phone.installationId),
                eventId: EVENT_ID,
                receiptEpochSeconds: 1_700_000_000n,
                remainingLifetime: 600,
                code: 'VAB12C',
                merchant: 'Veles synthetic',
                amountMagnitude: 100n,
                amountScale: 2,
                currency: 'XTS',
            }),
        ]);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await fullSide.applicationFrame(RecordType.PULL_EVENTS, 1n, body),
        );
        await expect(pulled).resolves.toMatchObject({ code: 'VAB12C' });
    });

    it('pull throws when only a non-full session is open', async () => {
        const fixture = await createProbeFixture();
        const pendingPhone = await TestPhone.create();
        const recoveryPeer = await fixture.trust.addPeer(pendingPhone, {
            state: 'active_activation_pending',
        });
        await connectAndAuthenticate(
            fixture,
            recoveryPeer,
            HANDLE_CONNECTION_2,
            pendingPhone,
            PEER_STATE_BELOW_ACTIVE,
        );

        await expect(debugHook().pull()).rejects.toThrow(/exactly one open full session/);
    });

    it('runtime message from content script has no handler', async () => {
        const addListener = vi.fn();
        (chrome.runtime as unknown as { onMessage: unknown }).onMessage = { addListener };

        await createProbeFixture();

        expect(addListener).not.toHaveBeenCalled();
        const hook = debugHook();
        expect(Object.keys(hook).sort()).toEqual([
            'connect',
            'exportIdentity',
            'injectPeer',
            'pull',
        ]);
    });

    it('pull round trip shows matching marker in readout', async () => {
        const fixture = await createProbeFixture();
        await debugHook().injectPeer(
            hex(fixture.phone.installationId),
            hex(fixture.phone.publicKey),
        );
        const side = await connectAndAuthenticate(fixture, hex(fixture.phone.installationId));
        const port = fixture.currentPort();

        const pulled = debugHook().pull();
        const request = await nextSend(port);
        expect(decodeRecordHeader(request.bytes)?.type).toBe(RecordType.PULL_REQUEST);
        await ackSend(port, request);

        const body = Uint8Array.from([
            1,
            ...encodeEvent({
                phoneInstallationId: hex(fixture.phone.installationId),
                eventId: EVENT_ID,
                receiptEpochSeconds: 1_700_000_000n,
                remainingLifetime: 600,
                code: 'VAB12C',
                merchant: 'Veles synthetic',
                amountMagnitude: 100n,
                amountScale: 2,
                currency: 'XTS',
            }),
        ]);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(RecordType.PULL_EVENTS, 1n, body),
        );

        const event = await pulled;
        expect(event.code).toBe('VAB12C');
        expect(event.eventId).toBe(hex(EVENT_ID));
        expect(event.sessionIdPrefix).toBe(hex(side.prefix));
        expect(event.remainingLifetime).toBe(600);
        expect(event.merchant).toBe('Veles synthetic');

        expect(readoutText(fixture)).toContain('VAB12C');
        expect(readoutText(fixture)).toContain(hex(EVENT_ID));
        expect(readoutText(fixture)).toContain(hex(side.prefix));

        const ack = await nextSend(port);
        const opened = await side.openClientRecord(ack.bytes, 2n);
        expect(opened.type).toBe(RecordType.EVENT_ACK);
        expect(hex(opened.body.slice(0, 16))).toBe(hex(fixture.phone.installationId));
        expect(hex(opened.body.slice(16))).toBe(hex(EVENT_ID));
        await ackSend(port, ack);
    });

    it('malformed event ends session and leaves readout empty', async () => {
        const fixture = await createProbeFixture();
        await debugHook().injectPeer(
            hex(fixture.phone.installationId),
            hex(fixture.phone.publicKey),
        );
        const side = await connectAndAuthenticate(fixture, hex(fixture.phone.installationId));
        const port = fixture.currentPort();

        const pulled = debugHook().pull();
        const request = await nextSend(port);
        await ackSend(port, request);

        // 601 s exceeds the §8.1 lifetime bound.
        const body = Uint8Array.from([
            1,
            ...encodeEvent({
                phoneInstallationId: hex(fixture.phone.installationId),
                eventId: EVENT_ID,
                receiptEpochSeconds: 1_700_000_000n,
                remainingLifetime: 601,
                code: 'VAB12C',
                merchant: 'Veles synthetic',
                amountMagnitude: 100n,
                amountScale: 2,
                currency: 'XTS',
            }),
        ]);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(RecordType.PULL_EVENTS, 1n, body),
        );

        const disconnect = await port.nextPosted();
        expect(disconnect.type).toBe('disconnect');
        await port.emit(
            disconnectedEvent({
                request_id: disconnect.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        await expect(pulled).rejects.toBeTruthy();
        expect(readoutText(fixture)).toBe('');
        expect(fixture.document.findReadout()?.hidden).toBe(true);
    });
});
