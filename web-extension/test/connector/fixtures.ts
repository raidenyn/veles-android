import type { PendingKind, ValidationContext } from '../../src/connector/protocol';

export const ENVELOPE_VERSION = 1;
export const API_VERSION = 1;

export const HANDLE_SCAN = 'a000000000000001';
export const HANDLE_SCAN_2 = 'a000000000000002';
export const HANDLE_DEVICE = 'b000000000000001';
export const HANDLE_DEVICE_2 = 'b000000000000002';
export const HANDLE_CONNECTION = 'c000000000000001';
export const HANDLE_CONNECTION_2 = 'c000000000000002';

export const HELLO_REQUEST_ID = 'hello-1';
export const START_SCAN_REQUEST_ID = 'start-scan-1';
export const STOP_SCAN_REQUEST_ID = 'stop-scan-1';
export const CONNECT_REQUEST_ID = 'connect-1';
export const DISCONNECT_REQUEST_ID = 'disconnect-1';
export const SEND_REQUEST_ID = 'send-1';
export const SHUTDOWN_REQUEST_ID = 'shutdown-1';
export const ERROR_REQUEST_ID = 'error-1';

type EventOverrides = Partial<Record<string, unknown>>;

function event(base: Record<string, unknown>, overrides: EventOverrides): Record<string, unknown> {
    const value = { ...base, ...overrides };
    for (const key of Object.keys(value)) {
        if (value[key] === undefined) Reflect.deleteProperty(value, key);
    }
    return value;
}

export function readyEvent(overrides: EventOverrides = {}): Record<string, unknown> {
    return event(
        {
            envelope_version: ENVELOPE_VERSION,
            api_version: API_VERSION,
            type: 'ready',
            request_id: HELLO_REQUEST_ID,
            bridge_version: 'veles-bridge-1.0.0',
            platform: 'windows',
            capabilities: ['veles_record_v1'],
        },
        overrides,
    );
}

export function deviceFoundEvent(overrides: EventOverrides = {}): Record<string, unknown> {
    return event(
        {
            envelope_version: ENVELOPE_VERSION,
            api_version: API_VERSION,
            type: 'device_found',
            scan_handle: HANDLE_SCAN,
            device_handle: HANDLE_DEVICE,
            label: 'Pixel test phone',
        },
        overrides,
    );
}

export function scanStartedEvent(overrides: EventOverrides = {}): Record<string, unknown> {
    return event(
        {
            envelope_version: ENVELOPE_VERSION,
            api_version: API_VERSION,
            type: 'scan_state',
            request_id: START_SCAN_REQUEST_ID,
            scan_handle: HANDLE_SCAN,
            status: 'started',
        },
        overrides,
    );
}

export function scanStoppedEvent(overrides: EventOverrides = {}): Record<string, unknown> {
    return event(
        {
            envelope_version: ENVELOPE_VERSION,
            api_version: API_VERSION,
            type: 'scan_state',
            request_id: STOP_SCAN_REQUEST_ID,
            scan_handle: HANDLE_SCAN,
            status: 'stopped',
        },
        overrides,
    );
}

export function connectedEvent(overrides: EventOverrides = {}): Record<string, unknown> {
    return event(
        {
            envelope_version: ENVELOPE_VERSION,
            api_version: API_VERSION,
            type: 'connected',
            request_id: CONNECT_REQUEST_ID,
            connection_handle: HANDLE_CONNECTION,
        },
        overrides,
    );
}

export function disconnectedEvent(overrides: EventOverrides = {}): Record<string, unknown> {
    return event(
        {
            envelope_version: ENVELOPE_VERSION,
            api_version: API_VERSION,
            type: 'disconnected',
            request_id: DISCONNECT_REQUEST_ID,
            connection_handle: HANDLE_CONNECTION,
        },
        overrides,
    );
}

export function sentEvent(overrides: EventOverrides = {}): Record<string, unknown> {
    return event(
        {
            envelope_version: ENVELOPE_VERSION,
            api_version: API_VERSION,
            type: 'sent',
            request_id: SEND_REQUEST_ID,
            connection_handle: HANDLE_CONNECTION,
        },
        overrides,
    );
}

export function messageEvent(overrides: EventOverrides = {}): Record<string, unknown> {
    return event(
        {
            envelope_version: ENVELOPE_VERSION,
            api_version: API_VERSION,
            type: 'message',
            connection_handle: HANDLE_CONNECTION,
            record_base64: 'AQ==',
        },
        overrides,
    );
}

export function shutdownCompleteEvent(overrides: EventOverrides = {}): Record<string, unknown> {
    return event(
        {
            envelope_version: ENVELOPE_VERSION,
            api_version: API_VERSION,
            type: 'shutdown_complete',
            request_id: SHUTDOWN_REQUEST_ID,
        },
        overrides,
    );
}

export function wireErrorEvent(overrides: EventOverrides = {}): Record<string, unknown> {
    return event(
        {
            envelope_version: ENVELOPE_VERSION,
            api_version: API_VERSION,
            type: 'error',
            request_id: ERROR_REQUEST_ID,
            code: 'send_failed',
            recoverability: 'fatal',
            stage: 'send',
        },
        overrides,
    );
}

export interface PendingFixture {
    kind: PendingKind;
    handle?: string;
    generation?: number;
}

export function pendingOf(
    entries: Record<string, PendingFixture>,
): ReadonlyMap<string, PendingFixture> {
    return new Map(Object.entries(entries));
}

export function preReadyContext(overrides: Partial<ValidationContext> = {}): ValidationContext {
    return {
        phase: 'pre_ready',
        apiVersion: null,
        pending: pendingOf({ [HELLO_REQUEST_ID]: { kind: 'hello' } }),
        scans: new Map(),
        connections: new Map(),
        ...overrides,
    };
}

export function readyContext(overrides: Partial<ValidationContext> = {}): ValidationContext {
    return {
        phase: 'ready',
        apiVersion: API_VERSION,
        pending: new Map(),
        scans: new Map([[HANDLE_SCAN, { status: 'started' }]]),
        connections: new Map([[HANDLE_CONNECTION, { status: 'connected', generation: 1 }]]),
        ...overrides,
    };
}
