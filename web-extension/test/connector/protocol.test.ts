import { describe, expect, it } from 'vitest';
import {
    CONNECT_REQUEST_ID,
    DISCONNECT_REQUEST_ID,
    ERROR_REQUEST_ID,
    HANDLE_CONNECTION,
    HANDLE_CONNECTION_2,
    HANDLE_SCAN,
    HANDLE_SCAN_2,
    HELLO_REQUEST_ID,
    SEND_REQUEST_ID,
    SHUTDOWN_REQUEST_ID,
    START_SCAN_REQUEST_ID,
    STOP_SCAN_REQUEST_ID,
    connectedEvent,
    deviceFoundEvent,
    disconnectedEvent,
    messageEvent,
    pendingOf,
    preReadyContext,
    readyContext,
    readyEvent,
    scanStartedEvent,
    scanStoppedEvent,
    sentEvent,
    shutdownCompleteEvent,
    wireErrorEvent,
} from './fixtures';
import { validateInbound, type ValidationContext } from '../../src/connector/protocol';

function accepted(value: unknown, context: ValidationContext) {
    const result = validateInbound(value, context);
    expect(result).toMatchObject({ ok: true });
    if (!result.ok) throw new Error(`expected acceptance, got ${result.code}/${result.stage}`);
    return result.event;
}

function rejected(value: unknown, context: ValidationContext) {
    const result = validateInbound(value, context);
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error('expected rejection');
    return result;
}

function readyWithPending(overrides: Partial<ValidationContext> = {}): ValidationContext {
    return readyContext(overrides);
}

describe('validateInbound — event shapes and byte bounds', () => {
    it('accepts a valid pre-ready ready and every valid post-ready event shape', () => {
        expect(accepted(readyEvent(), preReadyContext()).type).toBe('ready');

        accepted(deviceFoundEvent(), readyWithPending());
        accepted(
            deviceFoundEvent({ platform_hint: 'Veles Watch', advertisement: 'AQ==' }),
            readyWithPending(),
        );

        const readyScan = readyWithPending({
            pending: pendingOf({ [START_SCAN_REQUEST_ID]: { kind: 'start_scan' } }),
            scans: new Map(),
        });
        accepted(scanStartedEvent(), readyScan);

        const stoppedScan = readyWithPending({
            pending: pendingOf({
                [STOP_SCAN_REQUEST_ID]: { kind: 'stop_scan', handle: HANDLE_SCAN },
            }),
        });
        accepted(scanStoppedEvent(), stoppedScan);

        const connected = readyWithPending({
            pending: pendingOf({ [CONNECT_REQUEST_ID]: { kind: 'connect' } }),
            connections: new Map(),
        });
        accepted(connectedEvent({ platform_hint: 'Veles Watch' }), connected);

        const disconnected = readyWithPending({
            pending: pendingOf({
                [DISCONNECT_REQUEST_ID]: {
                    kind: 'disconnect',
                    handle: HANDLE_CONNECTION,
                    generation: 1,
                },
            }),
        });
        accepted(disconnectedEvent(), disconnected);

        const sent = readyWithPending({
            pending: pendingOf({
                [SEND_REQUEST_ID]: { kind: 'send', handle: HANDLE_CONNECTION, generation: 1 },
            }),
        });
        accepted(sentEvent(), sent);

        accepted(messageEvent(), readyWithPending());

        const shutdown = readyWithPending({
            pending: pendingOf({ [SHUTDOWN_REQUEST_ID]: { kind: 'shutdown' } }),
        });
        accepted(shutdownCompleteEvent(), shutdown);

        const error = readyWithPending({
            pending: pendingOf({
                [ERROR_REQUEST_ID]: { kind: 'send', handle: HANDLE_CONNECTION, generation: 1 },
            }),
        });
        accepted(wireErrorEvent({ connection_handle: HANDLE_CONNECTION }), error);

        // Unsolicited errors are legal without a request id.
        accepted(wireErrorEvent({ request_id: undefined }), readyWithPending());
    });

    it('rejects started:true and missing/unknown status', () => {
        rejected(scanStartedEvent({ started: true, status: undefined }), readyWithPending());
        rejected(scanStartedEvent({ status: undefined }), readyWithPending());
        rejected(scanStartedEvent({ status: 'running' }), readyWithPending());
    });

    it('rejects short, uppercase, and non-hex handles', () => {
        for (const handle of ['scan1', 'A000000000000001', 'g000000000000001', 'a00000000000000']) {
            const context = readyWithPending({
                pending: pendingOf({ [CONNECT_REQUEST_ID]: { kind: 'connect' } }),
            });
            rejected(connectedEvent({ connection_handle: handle }), context);
        }
    });

    it('rejects non-canonical base64 and decoded-size violations', () => {
        const tooLarge = Buffer.alloc(4097).toString('base64');
        rejected(messageEvent({ record_base64: 'AQ' }), readyWithPending());
        rejected(messageEvent({ record_base64: 'AR==' }), readyWithPending());
        rejected(messageEvent({ record_base64: 'A===' }), readyWithPending());
        rejected(messageEvent({ record_base64: tooLarge }), readyWithPending());
        rejected(messageEvent({ record_base64: '' }), readyWithPending());
        rejected(
            deviceFoundEvent({ advertisement: Buffer.alloc(257).toString('base64') }),
            readyWithPending(),
        );
        rejected(deviceFoundEvent({ advertisement: 'AR==' }), readyWithPending());
        accepted(
            messageEvent({ record_base64: Buffer.alloc(4096).toString('base64') }),
            readyWithPending(),
        );
        accepted(
            deviceFoundEvent({ advertisement: Buffer.alloc(256).toString('base64') }),
            readyWithPending(),
        );
    });

    it('rejects null, unknown fields, and wrong exact field sets', () => {
        rejected(readyEvent({ capabilities: null }), preReadyContext());
        rejected(readyEvent({ extra: true }), preReadyContext());
        rejected(deviceFoundEvent({ request_id: 'unexpected' }), readyWithPending());
        rejected(messageEvent({ request_id: 'unexpected' }), readyWithPending());
        rejected(
            scanStartedEvent({ started: true }),
            readyWithPending({
                pending: pendingOf({ [START_SCAN_REQUEST_ID]: { kind: 'start_scan' } }),
            }),
        );
        rejected(
            sentEvent({ platform_hint: 'extra' }),
            readyWithPending({
                pending: pendingOf({
                    [SEND_REQUEST_ID]: { kind: 'send', handle: HANDLE_CONNECTION, generation: 1 },
                }),
            }),
        );
    });

    it('enforces UTF-8 byte lengths rather than UTF-16 code units', () => {
        // 32 two-byte characters = 64 bytes: accepted. 33 = 66 bytes: rejected.
        accepted(deviceFoundEvent({ label: 'é'.repeat(32) }), readyWithPending());
        rejected(deviceFoundEvent({ label: 'é'.repeat(33) }), readyWithPending());
        rejected(deviceFoundEvent({ label: 'bad\u0001label' }), readyWithPending());
        rejected(deviceFoundEvent({ label: '' }), readyWithPending());

        accepted(deviceFoundEvent({ platform_hint: 'é'.repeat(128) }), readyWithPending());
        rejected(deviceFoundEvent({ platform_hint: 'é'.repeat(129) }), readyWithPending());

        rejected(readyEvent({ bridge_version: 'é'.repeat(33) }), preReadyContext());
        rejected(readyEvent({ bridge_version: '' }), preReadyContext());

        const context = readyWithPending();
        rejected(wireErrorEvent({ request_id: undefined, stage: 'A'.repeat(1) }), context);
        rejected(wireErrorEvent({ request_id: undefined, stage: 'a'.repeat(33) }), context);
        rejected(wireErrorEvent({ request_id: undefined, stage: 'bad stage' }), context);
        accepted(wireErrorEvent({ request_id: undefined, stage: 'a'.repeat(32) }), context);
    });

    it('rejects invalid platform, capability sets, and API versions', () => {
        rejected(readyEvent({ platform: 'linux' }), preReadyContext());
        rejected(readyEvent({ capabilities: ['future_capability'] }), preReadyContext());
        rejected(
            readyEvent({ capabilities: ['veles_record_v1', 'veles_record_v1'] }),
            preReadyContext(),
        );
        accepted(readyEvent({ capabilities: [] }), preReadyContext());
        rejected(readyEvent({ api_version: 2 }), preReadyContext());
        rejected(readyEvent({ api_version: 0 }), preReadyContext());
        rejected(readyEvent({ api_version: '1' }), preReadyContext());
    });

    it('preserves a leading U+FEFF and rejects lone surrogates/oversized input', () => {
        accepted(deviceFoundEvent({ label: '\uFEFFphone' }), readyWithPending());
        accepted(deviceFoundEvent({ platform_hint: '\uFEFFhint' }), readyWithPending());
        rejected(deviceFoundEvent({ label: '\ud800' }), readyWithPending());
        rejected(deviceFoundEvent({ label: 'x'.repeat(100_000) }), readyWithPending());
        rejected(messageEvent({ record_base64: 'A'.repeat(100_000) }), readyWithPending());
    });
});

describe('validateInbound — API phase and correlation', () => {
    it('requires hello correlation for ready and allows only ready/error before ready', () => {
        rejected(readyEvent(), preReadyContext({ pending: new Map() }));
        rejected(readyEvent({ request_id: 'other' }), preReadyContext());
        rejected(messageEvent(), preReadyContext());
        rejected(messageEvent({ api_version: undefined }), readyWithPending());
        rejected(messageEvent({ api_version: 2 }), readyWithPending());
        rejected(readyEvent(), readyWithPending());
    });

    it('accepts a pre-ready correlated error without api_version and rejects an unsolicited one', () => {
        accepted(
            wireErrorEvent({
                api_version: undefined,
                request_id: HELLO_REQUEST_ID,
                code: 'incompatible_api',
                stage: 'negotiation',
            }),
            preReadyContext(),
        );
        rejected(
            wireErrorEvent({ api_version: undefined, request_id: undefined }),
            preReadyContext(),
        );
        rejected(
            wireErrorEvent({ api_version: 1, request_id: HELLO_REQUEST_ID }),
            preReadyContext(),
        );
    });

    it('rejects unknown, duplicate, and cross-kind correlated terminals', () => {
        rejected(
            sentEvent({ request_id: 'unknown' }),
            readyWithPending({
                pending: pendingOf({
                    [SEND_REQUEST_ID]: { kind: 'send', handle: HANDLE_CONNECTION, generation: 1 },
                }),
            }),
        );
        rejected(
            connectedEvent(),
            readyWithPending({
                pending: pendingOf({ [CONNECT_REQUEST_ID]: { kind: 'connect' } }),
            }),
        );
        // A connected terminal cannot use a send request id.
        rejected(
            connectedEvent({ request_id: SEND_REQUEST_ID }),
            readyWithPending({
                pending: pendingOf({
                    [SEND_REQUEST_ID]: { kind: 'send', handle: HANDLE_CONNECTION, generation: 1 },
                }),
            }),
        );
        // stopped cannot complete start_scan, and started cannot complete stop_scan.
        rejected(
            scanStoppedEvent({ request_id: START_SCAN_REQUEST_ID }),
            readyWithPending({
                pending: pendingOf({ [START_SCAN_REQUEST_ID]: { kind: 'start_scan' } }),
            }),
        );
        rejected(
            scanStartedEvent({ request_id: STOP_SCAN_REQUEST_ID }),
            readyWithPending({
                pending: pendingOf({
                    [STOP_SCAN_REQUEST_ID]: { kind: 'stop_scan', handle: HANDLE_SCAN },
                }),
            }),
        );
    });

    it('accepts a trailing discovery while a scan is stopping until stopped arrives', () => {
        const stopping = readyWithPending({
            scans: new Map([[HANDLE_SCAN, { status: 'stopping' }]]),
        });
        accepted(deviceFoundEvent(), stopping);

        const stopped = readyWithPending({
            pending: pendingOf({
                [STOP_SCAN_REQUEST_ID]: { kind: 'stop_scan', handle: HANDLE_SCAN },
            }),
            scans: new Map([[HANDLE_SCAN, { status: 'stopping' }]]),
        });
        accepted(scanStoppedEvent(), stopped);

        rejected(
            deviceFoundEvent(),
            readyWithPending({ scans: new Map([[HANDLE_SCAN, { status: 'stopped' }]]) }),
        );
    });

    it('binds correlated errors to the operation kind and captured generation', () => {
        const startScan = readyWithPending({
            pending: pendingOf({ [START_SCAN_REQUEST_ID]: { kind: 'start_scan' } }),
            scans: new Map(),
        });
        rejected(
            wireErrorEvent({
                request_id: START_SCAN_REQUEST_ID,
                connection_handle: HANDLE_CONNECTION,
            }),
            startScan,
        );

        const connect = readyWithPending({
            pending: pendingOf({ [CONNECT_REQUEST_ID]: { kind: 'connect' } }),
            connections: new Map(),
        });
        rejected(
            wireErrorEvent({ request_id: CONNECT_REQUEST_ID, scan_handle: HANDLE_SCAN }),
            connect,
        );
        accepted(
            wireErrorEvent({
                request_id: CONNECT_REQUEST_ID,
                connection_handle: undefined,
                code: 'unknown_handle',
                stage: 'connect',
            }),
            connect,
        );

        const staleSent = readyWithPending({
            pending: pendingOf({
                [SEND_REQUEST_ID]: { kind: 'send', handle: HANDLE_CONNECTION, generation: 2 },
            }),
            connections: new Map([[HANDLE_CONNECTION, { status: 'connected', generation: 1 }]]),
        });
        rejected(
            wireErrorEvent({
                request_id: SEND_REQUEST_ID,
                connection_handle: HANDLE_CONNECTION,
            }),
            staleSent,
        );
        rejected(
            wireErrorEvent({ request_id: SEND_REQUEST_ID, connection_handle: undefined }),
            staleSent,
        );

        const staleDisconnect = readyWithPending({
            pending: pendingOf({
                [DISCONNECT_REQUEST_ID]: {
                    kind: 'disconnect',
                    handle: HANDLE_CONNECTION,
                    generation: 2,
                },
            }),
            connections: new Map([[HANDLE_CONNECTION, { status: 'connected', generation: 1 }]]),
        });
        rejected(
            wireErrorEvent({ request_id: DISCONNECT_REQUEST_ID, connection_handle: undefined }),
            staleDisconnect,
        );

        const send = readyWithPending({
            pending: pendingOf({
                [SEND_REQUEST_ID]: { kind: 'send', handle: HANDLE_CONNECTION, generation: 1 },
            }),
        });
        accepted(
            wireErrorEvent({ request_id: SEND_REQUEST_ID, connection_handle: undefined }),
            send,
        );
        accepted(
            wireErrorEvent({ request_id: SEND_REQUEST_ID, connection_handle: HANDLE_CONNECTION }),
            send,
        );
        rejected(wireErrorEvent({ request_id: SEND_REQUEST_ID, scan_handle: HANDLE_SCAN }), send);

        const stop = readyWithPending({
            pending: pendingOf({
                [STOP_SCAN_REQUEST_ID]: { kind: 'stop_scan', handle: HANDLE_SCAN },
            }),
        });
        rejected(
            wireErrorEvent({
                request_id: STOP_SCAN_REQUEST_ID,
                connection_handle: HANDLE_CONNECTION,
            }),
            stop,
        );
        accepted(
            wireErrorEvent({ request_id: STOP_SCAN_REQUEST_ID, scan_handle: HANDLE_SCAN }),
            stop,
        );
    });

    it('requires correlated handles and captured generations to match', () => {
        rejected(
            sentEvent({ connection_handle: HANDLE_CONNECTION_2 }),
            readyWithPending({
                pending: pendingOf({
                    [SEND_REQUEST_ID]: { kind: 'send', handle: HANDLE_CONNECTION, generation: 1 },
                }),
                connections: new Map([
                    [HANDLE_CONNECTION, { status: 'connected', generation: 1 }],
                    [HANDLE_CONNECTION_2, { status: 'connected', generation: 2 }],
                ]),
            }),
        );
        rejected(
            sentEvent({ connection_handle: HANDLE_CONNECTION_2 }),
            readyWithPending({
                pending: pendingOf({
                    [SEND_REQUEST_ID]: { kind: 'send', handle: HANDLE_CONNECTION, generation: 1 },
                }),
                connections: new Map([
                    [HANDLE_CONNECTION_2, { status: 'connected', generation: 2 }],
                ]),
            }),
        );
        rejected(
            sentEvent(),
            readyWithPending({
                pending: pendingOf({
                    [SEND_REQUEST_ID]: { kind: 'send', handle: HANDLE_CONNECTION, generation: 2 },
                }),
                connections: new Map([[HANDLE_CONNECTION, { status: 'connected', generation: 1 }]]),
            }),
        );
        rejected(
            disconnectedEvent({ connection_handle: HANDLE_CONNECTION_2 }),
            readyWithPending({
                pending: pendingOf({
                    [DISCONNECT_REQUEST_ID]: {
                        kind: 'disconnect',
                        handle: HANDLE_CONNECTION,
                        generation: 1,
                    },
                }),
            }),
        );
    });

    it('settles handle-less unknown_handle errors for send/disconnect that never had a live connection', () => {
        for (const [requestId, kind] of [
            [SEND_REQUEST_ID, 'send'],
            [DISCONNECT_REQUEST_ID, 'disconnect'],
        ] as const) {
            const context = readyWithPending({
                pending: pendingOf({
                    [requestId]: { kind, handle: HANDLE_CONNECTION },
                }),
                connections: new Map(),
            });
            accepted(
                wireErrorEvent({
                    request_id: requestId,
                    code: 'unknown_handle',
                    stage: 'connection_handle',
                    connection_handle: undefined,
                }),
                context,
            );
            // Repeating the absent handle is still cross-routed/stale.
            rejected(
                wireErrorEvent({
                    request_id: requestId,
                    code: 'unknown_handle',
                    stage: 'connection_handle',
                    connection_handle: HANDLE_CONNECTION,
                }),
                context,
            );
            // Without a live connection only the explicit unknown_handle
            // terminal is legitimate for a handle-less correlated error.
            rejected(
                wireErrorEvent({
                    request_id: requestId,
                    code: 'send_failed',
                    stage: 'connection_handle',
                    connection_handle: undefined,
                }),
                context,
            );
        }
    });

    it('settles handle-less unknown_handle after a captured live handle disappears', () => {
        for (const [requestId, kind] of [
            [SEND_REQUEST_ID, 'send'],
            [DISCONNECT_REQUEST_ID, 'disconnect'],
        ] as const) {
            const context = readyWithPending({
                pending: pendingOf({
                    [requestId]: { kind, handle: HANDLE_CONNECTION, generation: 1 },
                }),
                connections: new Map(),
            });
            accepted(
                wireErrorEvent({
                    request_id: requestId,
                    code: 'unknown_handle',
                    stage: 'connection_handle',
                    connection_handle: undefined,
                }),
                context,
            );
            rejected(
                wireErrorEvent({
                    request_id: requestId,
                    code: 'unknown_handle',
                    stage: 'connection_handle',
                    connection_handle: HANDLE_CONNECTION,
                }),
                context,
            );
            rejected(
                wireErrorEvent({
                    request_id: requestId,
                    code: 'send_failed',
                    stage: 'connection_handle',
                    connection_handle: undefined,
                }),
                context,
            );
        }

        const send = readyWithPending({
            pending: pendingOf({
                [SEND_REQUEST_ID]: { kind: 'send', handle: HANDLE_CONNECTION, generation: 1 },
            }),
            connections: new Map(),
        });
        rejected(
            sentEvent({ request_id: SEND_REQUEST_ID, connection_handle: HANDLE_CONNECTION }),
            send,
        );
    });

    it('validates device_found scan/status, message/connection, and unsolicited stopped scan', () => {
        rejected(deviceFoundEvent({ scan_handle: HANDLE_SCAN_2 }), readyWithPending());
        rejected(
            deviceFoundEvent(),
            readyWithPending({ scans: new Map([[HANDLE_SCAN, { status: 'stopped' }]]) }),
        );
        rejected(messageEvent({ connection_handle: HANDLE_CONNECTION_2 }), readyWithPending());
        rejected(
            scanStoppedEvent({ request_id: undefined, scan_handle: HANDLE_SCAN_2 }),
            readyWithPending(),
        );
        accepted(
            scanStoppedEvent({ request_id: undefined }),
            readyWithPending({ scans: new Map([[HANDLE_SCAN, { status: 'started' }]]) }),
        );
        rejected(
            scanStartedEvent({ request_id: undefined }),
            readyWithPending({ scans: new Map([[HANDLE_SCAN, { status: 'started' }]]) }),
        );
    });

    it('validates error code, recoverability, handle exclusivity, and known handles', () => {
        rejected(wireErrorEvent({ request_id: undefined, code: 'overflow' }), readyWithPending());
        rejected(
            wireErrorEvent({ request_id: undefined, recoverability: 'maybe' }),
            readyWithPending(),
        );
        rejected(
            wireErrorEvent({
                request_id: undefined,
                scan_handle: HANDLE_SCAN,
                connection_handle: HANDLE_CONNECTION,
            }),
            readyWithPending(),
        );
        rejected(
            wireErrorEvent({ request_id: undefined, connection_handle: HANDLE_CONNECTION_2 }),
            readyWithPending(),
        );
        rejected(
            wireErrorEvent({ request_id: undefined, scan_handle: HANDLE_SCAN_2 }),
            readyWithPending(),
        );
        accepted(
            wireErrorEvent({ request_id: undefined, connection_handle: HANDLE_CONNECTION }),
            readyWithPending(),
        );
    });
});
