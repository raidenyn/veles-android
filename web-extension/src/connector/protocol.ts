// Strict validation for the frozen Veles Native Messaging envelope v1.
//
// Normative source: docs/protocol/veles-v1.md section 10 (OTP2-BRD-*) and
// protocol/v1/envelope.schema.json. This module validates host -> connector
// events. It never interprets record bytes: `record_base64` remains opaque
// transport data and is only checked for canonical encoding and size.

export const ENVELOPE_VERSION = 1;
export const SUPPORTED_API_VERSIONS = [1] as const;
export const HOST_NAME = 'me.nagaev.veles.bridge';
export const VELES_SERVICE_UUID = '8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51';

// Envelope admission ceilings (OTP2-BRD-031): `send` records are admitted up to
// 5120 decoded bytes at the envelope boundary; a complete `message` record is
// still bounded by the Veles record maximum of 4096.
export const RECORD_BASE64_MAX_BYTES = 5120;
export const RECORD_MAX_BYTES = 4096;
export const ADVERTISEMENT_MAX_BYTES = 256;
export const LABEL_MAX_BYTES = 64;
export const PLATFORM_HINT_MAX_BYTES = 256;
export const BRIDGE_VERSION_MAX_BYTES = 64;
export const ERROR_STAGE_MAX_BYTES = 32;
export const CAPABILITIES_MAX = 16;
export const API_VERSION_MAX = 1048576;

export const CAPABILITY_RECORD_V1 = 'veles_record_v1';
const DEFINED_CAPABILITIES: ReadonlySet<string> = new Set([CAPABILITY_RECORD_V1]);

export type WireErrorCode =
    | 'incompatible_api'
    | 'invalid_request'
    | 'unknown_handle'
    | 'unknown_request'
    | 'invalid_base64'
    | 'oversize_record'
    | 'cap_exceeded'
    | 'busy'
    | 'scan_failed'
    | 'connect_failed'
    | 'send_failed'
    | 'timeout'
    | 'internal';

export type PendingKind =
    | 'hello'
    | 'start_scan'
    | 'stop_scan'
    | 'connect'
    | 'disconnect'
    | 'send'
    | 'shutdown';

export type WireEvent =
    | {
          type: 'ready';
          envelope_version: 1;
          api_version: number;
          request_id: string;
          bridge_version: string;
          platform: 'windows' | 'macos';
          capabilities: string[];
      }
    | {
          type: 'device_found';
          envelope_version: 1;
          api_version: number;
          scan_handle: string;
          device_handle: string;
          label: string;
          platform_hint?: string;
          advertisement?: string;
      }
    | {
          type: 'scan_state';
          envelope_version: 1;
          api_version: number;
          scan_handle: string;
          status: 'started' | 'stopped';
          request_id?: string;
      }
    | {
          type: 'connected';
          envelope_version: 1;
          api_version: number;
          request_id: string;
          connection_handle: string;
          platform_hint?: string;
      }
    | {
          type: 'disconnected';
          envelope_version: 1;
          api_version: number;
          connection_handle: string;
          request_id?: string;
      }
    | {
          type: 'sent';
          envelope_version: 1;
          api_version: number;
          request_id: string;
          connection_handle: string;
      }
    | {
          type: 'message';
          envelope_version: 1;
          api_version: number;
          connection_handle: string;
          record_base64: string;
      }
    | {
          type: 'shutdown_complete';
          envelope_version: 1;
          api_version: number;
          request_id: string;
      }
    | {
          type: 'error';
          envelope_version: 1;
          api_version?: number;
          request_id?: string;
          code: WireErrorCode;
          recoverability: 'retryable' | 'fatal';
          stage: string;
          scan_handle?: string;
          connection_handle?: string;
      };

export interface ValidationContext {
    phase: 'pre_ready' | 'ready';
    apiVersion: number | null;
    pending: ReadonlyMap<string, { kind: PendingKind; handle?: string; generation?: number }>;
    scans: ReadonlyMap<string, { status: 'starting' | 'started' | 'stopping' | 'stopped' }>;
    connections: ReadonlyMap<string, { status: string; generation: number }>;
}

export type Validation =
    | { ok: true; event: WireEvent }
    | { ok: false; code: 'invalid_event' | 'unexpected_correlation'; stage: string };

const EVENT_TYPES: ReadonlySet<string> = new Set([
    'ready',
    'device_found',
    'scan_state',
    'connected',
    'disconnected',
    'sent',
    'message',
    'shutdown_complete',
    'error',
]);

const WIRE_ERROR_CODES: ReadonlySet<string> = new Set([
    'incompatible_api',
    'invalid_request',
    'unknown_handle',
    'unknown_request',
    'invalid_base64',
    'oversize_record',
    'cap_exceeded',
    'busy',
    'scan_failed',
    'connect_failed',
    'send_failed',
    'timeout',
    'internal',
]);

const HANDLE_PATTERN = /^[0-9a-f]{16}$/;
const REQUEST_ID_PATTERN = /^[A-Za-z0-9._-]{1,64}$/;
const STAGE_PATTERN = /^[a-z0-9_]{1,32}$/;
const SERVICE_UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const B64_SHAPE = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;
const B64_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
const B64_REVERSE: ReadonlyMap<string, number> = new Map(
    [...B64_ALPHABET].map((character, index) => [character, index]),
);

const utf8Encoder = new TextEncoder();
const utf8Decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });

export function isPlainObject(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function isHandle(value: unknown): value is string {
    return typeof value === 'string' && HANDLE_PATTERN.test(value);
}

export function isRequestId(value: unknown): value is string {
    return typeof value === 'string' && REQUEST_ID_PATTERN.test(value);
}

export function isServiceUuid(value: unknown): value is string {
    return typeof value === 'string' && SERVICE_UUID_PATTERN.test(value);
}

export function isApiVersion(value: unknown): value is number {
    return (
        typeof value === 'number' &&
        Number.isInteger(value) &&
        value >= 1 &&
        value <= API_VERSION_MAX
    );
}

export function isSupportedApiVersion(value: number): boolean {
    return (SUPPORTED_API_VERSIONS as readonly number[]).includes(value);
}

function utf8ByteLength(value: unknown): number | null {
    if (typeof value !== 'string') return null;
    const bytes = utf8Encoder.encode(value);
    try {
        // Rejects lone UTF-16 surrogates, which TextEncoder would otherwise
        // replace with U+FFFD instead of rejecting.
        if (utf8Decoder.decode(bytes) !== value) return null;
    } catch {
        return null;
    }
    return bytes.length;
}

export function isUtf8String(
    value: unknown,
    minBytes: number,
    maxBytes: number,
    noControlCharacters = false,
): value is string {
    // Cheap ceiling first: every UTF-8 encoding is at least as long as its
    // UTF-16 code-unit count, so no input above maxBytes can pass.
    if (typeof value !== 'string' || value.length > maxBytes) return false;
    const length = utf8ByteLength(value);
    if (length === null || length < minBytes || length > maxBytes) return false;
    if (!noControlCharacters) return true;
    for (const character of value as string) {
        const code = character.codePointAt(0) ?? 0;
        if (code <= 0x1f || code === 0x7f) return false;
    }
    return true;
}

export function isLabel(value: unknown): value is string {
    return isUtf8String(value, 1, LABEL_MAX_BYTES, true);
}

export function isPlatformHint(value: unknown): value is string {
    return isUtf8String(value, 1, PLATFORM_HINT_MAX_BYTES, true);
}

export function isCanonicalBase64(
    value: unknown,
    minBytes: number,
    maxBytes: number,
): value is string {
    if (typeof value !== 'string') return false;
    if (value.length > 4 * Math.ceil(maxBytes / 3)) return false;
    if (!B64_SHAPE.test(value) || value.length % 4 !== 0) return false;

    let padding = 0;
    if (value.endsWith('==')) padding = 2;
    else if (value.endsWith('=')) padding = 1;

    if (padding > 0) {
        const lastDataIndex = value.length - padding - 1;
        const kept = B64_REVERSE.get(value[lastDataIndex] ?? '');
        if (kept === undefined) return false;
        const padBits = padding === 1 ? kept & 0x03 : kept & 0x0f;
        if (padBits !== 0) return false;
    }

    const decodedLength = (value.length / 4) * 3 - padding;
    return decodedLength >= minBytes && decodedLength <= maxBytes;
}

function hasExactFields(
    value: Record<string, unknown>,
    required: readonly string[],
    optional: readonly string[] = [],
): boolean {
    const allowed = new Set([...required, ...optional]);
    const keys = Object.keys(value);
    if (keys.length < required.length || keys.length > allowed.size) return false;
    if (!required.every((key) => Object.hasOwn(value, key))) return false;
    for (const key of keys) {
        if (!allowed.has(key)) return false;
        const field = value[key];
        if (field === null || field === undefined) return false;
    }
    return true;
}

function invalid(stage: string): Validation {
    return { ok: false, code: 'invalid_event', stage };
}

function unexpected(stage: string): Validation {
    return { ok: false, code: 'unexpected_correlation', stage };
}

function accept(value: Record<string, unknown>): Validation {
    return { ok: true, event: value as unknown as WireEvent };
}

function isCapabilityList(value: unknown): value is string[] {
    if (!Array.isArray(value) || value.length > CAPABILITIES_MAX) return false;
    const seen = new Set<string>();
    for (const capability of value) {
        if (typeof capability !== 'string') return false;
        if (!DEFINED_CAPABILITIES.has(capability)) return false;
        if (seen.has(capability)) return false;
        seen.add(capability);
    }
    return true;
}

function validateReady(value: Record<string, unknown>, context: ValidationContext): Validation {
    if (
        !hasExactFields(value, [
            'envelope_version',
            'api_version',
            'type',
            'request_id',
            'bridge_version',
            'platform',
            'capabilities',
        ])
    ) {
        return invalid('schema');
    }
    if (!isRequestId(value.request_id)) return invalid('schema');
    if (!isApiVersion(value.api_version) || !isSupportedApiVersion(value.api_version)) {
        return invalid('negotiation');
    }
    if (!isUtf8String(value.bridge_version, 1, BRIDGE_VERSION_MAX_BYTES)) {
        return invalid('length');
    }
    if (value.platform !== 'windows' && value.platform !== 'macos') return invalid('schema');
    if (!isCapabilityList(value.capabilities)) return invalid('schema');

    if (context.pending.get(value.request_id)?.kind !== 'hello') {
        return unexpected('request_id');
    }
    return accept(value);
}

function validateDeviceFound(
    value: Record<string, unknown>,
    context: ValidationContext,
): Validation {
    if (
        !hasExactFields(
            value,
            ['envelope_version', 'api_version', 'type', 'scan_handle', 'device_handle', 'label'],
            ['platform_hint', 'advertisement'],
        )
    ) {
        return invalid('schema');
    }
    if (!isHandle(value.scan_handle) || !isHandle(value.device_handle)) return invalid('schema');
    if (!isLabel(value.label)) return invalid('length');
    if (value.platform_hint !== undefined && !isPlatformHint(value.platform_hint)) {
        return invalid('length');
    }
    if (
        value.advertisement !== undefined &&
        !isCanonicalBase64(value.advertisement, 1, ADVERTISEMENT_MAX_BYTES)
    ) {
        return invalid('base64');
    }

    const scan = context.scans.get(value.scan_handle);
    if (scan === undefined) return unexpected('scan_handle');
    // Local stop intent does not make the scan wire-closed: OTP2-BRD-022.1
    // puts `scan_state: stopped` after the last `device_found`, so a trailing
    // discovery is valid while the scan is started or stopping.
    if (scan.status !== 'started' && scan.status !== 'stopping') {
        return unexpected('scan_state');
    }
    return accept(value);
}

function validateScanState(value: Record<string, unknown>, context: ValidationContext): Validation {
    if (
        !hasExactFields(
            value,
            ['envelope_version', 'api_version', 'type', 'scan_handle', 'status'],
            ['request_id'],
        )
    ) {
        return invalid('schema');
    }
    if (!isHandle(value.scan_handle)) return invalid('schema');
    if (value.status !== 'started' && value.status !== 'stopped') return invalid('schema');

    if (value.request_id !== undefined) {
        if (!isRequestId(value.request_id)) return invalid('schema');
        const pending = context.pending.get(value.request_id);
        if (pending === undefined) return unexpected('request_id');
        if (value.status === 'started') {
            if (pending.kind !== 'start_scan') return unexpected('request_kind');
            if (context.scans.has(value.scan_handle)) return unexpected('scan_handle');
        } else {
            if (pending.kind !== 'stop_scan') return unexpected('request_kind');
            if (pending.handle !== value.scan_handle) return unexpected('scan_handle');
            if (!context.scans.has(value.scan_handle)) return unexpected('scan_handle');
        }
    } else {
        // A started scan is only a correlated completion; an unsolicited
        // scan_state is a stop/interruption/expiry event.
        if (value.status !== 'stopped') return unexpected('request_id');
        if (!context.scans.has(value.scan_handle)) return unexpected('scan_handle');
    }
    return accept(value);
}

function validateConnected(value: Record<string, unknown>, context: ValidationContext): Validation {
    if (
        !hasExactFields(
            value,
            ['envelope_version', 'api_version', 'type', 'request_id', 'connection_handle'],
            ['platform_hint'],
        )
    ) {
        return invalid('schema');
    }
    if (!isRequestId(value.request_id) || !isHandle(value.connection_handle)) {
        return invalid('schema');
    }
    if (value.platform_hint !== undefined && !isPlatformHint(value.platform_hint)) {
        return invalid('length');
    }
    if (context.pending.get(value.request_id)?.kind !== 'connect') {
        return unexpected('request_id');
    }
    if (context.connections.has(value.connection_handle)) return unexpected('connection_handle');
    return accept(value);
}

function validateDisconnected(
    value: Record<string, unknown>,
    context: ValidationContext,
): Validation {
    if (
        !hasExactFields(
            value,
            ['envelope_version', 'api_version', 'type', 'connection_handle'],
            ['request_id'],
        )
    ) {
        return invalid('schema');
    }
    if (!isHandle(value.connection_handle)) return invalid('schema');
    const connection = context.connections.get(value.connection_handle);
    if (connection === undefined) return unexpected('connection_handle');

    if (value.request_id !== undefined) {
        if (!isRequestId(value.request_id)) return invalid('schema');
        const pending = context.pending.get(value.request_id);
        if (pending === undefined) return unexpected('request_id');
        if (pending.kind !== 'disconnect') return unexpected('request_kind');
        if (pending.handle !== value.connection_handle) return unexpected('connection_handle');
        if (pending.generation !== undefined && pending.generation !== connection.generation) {
            return unexpected('generation');
        }
    }
    return accept(value);
}

function validateSent(value: Record<string, unknown>, context: ValidationContext): Validation {
    if (
        !hasExactFields(value, [
            'envelope_version',
            'api_version',
            'type',
            'request_id',
            'connection_handle',
        ])
    ) {
        return invalid('schema');
    }
    if (!isRequestId(value.request_id) || !isHandle(value.connection_handle)) {
        return invalid('schema');
    }
    const pending = context.pending.get(value.request_id);
    if (pending === undefined) return unexpected('request_id');
    if (pending.kind !== 'send') return unexpected('request_kind');
    if (pending.handle !== value.connection_handle) return unexpected('connection_handle');
    const connection = context.connections.get(value.connection_handle);
    if (connection === undefined) return unexpected('connection_handle');
    if (pending.generation !== undefined && pending.generation !== connection.generation) {
        return unexpected('generation');
    }
    return accept(value);
}

function validateMessage(value: Record<string, unknown>, context: ValidationContext): Validation {
    if (
        !hasExactFields(value, [
            'envelope_version',
            'api_version',
            'type',
            'connection_handle',
            'record_base64',
        ])
    ) {
        return invalid('schema');
    }
    if (!isHandle(value.connection_handle)) return invalid('schema');
    if (!isCanonicalBase64(value.record_base64, 1, RECORD_MAX_BYTES)) return invalid('base64');
    if (!context.connections.has(value.connection_handle)) return unexpected('connection_handle');
    return accept(value);
}

function validateShutdownComplete(
    value: Record<string, unknown>,
    context: ValidationContext,
): Validation {
    if (!hasExactFields(value, ['envelope_version', 'api_version', 'type', 'request_id'])) {
        return invalid('schema');
    }
    if (!isRequestId(value.request_id)) return invalid('schema');
    if (context.pending.get(value.request_id)?.kind !== 'shutdown') {
        return unexpected('request_id');
    }
    return accept(value);
}

function validateError(value: Record<string, unknown>, context: ValidationContext): Validation {
    if (
        !hasExactFields(
            value,
            ['envelope_version', 'type', 'code', 'recoverability', 'stage'],
            ['api_version', 'request_id', 'scan_handle', 'connection_handle'],
        )
    ) {
        return invalid('schema');
    }
    if (typeof value.code !== 'string' || !WIRE_ERROR_CODES.has(value.code)) {
        return invalid('schema');
    }
    if (value.recoverability !== 'retryable' && value.recoverability !== 'fatal') {
        return invalid('schema');
    }
    if (typeof value.stage !== 'string' || !STAGE_PATTERN.test(value.stage)) {
        return invalid('schema');
    }
    const hasScan = value.scan_handle !== undefined;
    const hasConnection = value.connection_handle !== undefined;
    if (hasScan && hasConnection) return invalid('schema');
    if (hasScan && !isHandle(value.scan_handle)) return invalid('schema');
    if (hasConnection && !isHandle(value.connection_handle)) return invalid('schema');

    if (value.request_id !== undefined) {
        if (!isRequestId(value.request_id)) return invalid('schema');
        const pending = context.pending.get(value.request_id);
        if (pending === undefined) return unexpected('request_id');

        if (pending.handle === undefined) {
            // hello/start_scan/connect/shutdown never own a scan or
            // connection handle, so an error naming one is cross-routed.
            if (hasScan || hasConnection) {
                return unexpected(hasScan ? 'scan_handle' : 'connection_handle');
            }
        } else if (pending.kind === 'stop_scan') {
            if (hasConnection) return unexpected('scan_handle');
            if (hasScan && value.scan_handle !== pending.handle) {
                return unexpected('scan_handle');
            }
        } else {
            if (hasScan) return unexpected('connection_handle');
            if (hasConnection && value.connection_handle !== pending.handle) {
                return unexpected('connection_handle');
            }
            // The operation handle and its captured generation remain part of
            // the request identity even when the host omits the optional
            // handle from a correlated error. A handle-less `unknown_handle`
            // is still a legitimate terminal when the connection was never
            // live (no captured generation); it must settle this request, not
            // turn into a process-level protocol violation.
            const pendingConnection = context.connections.get(pending.handle);
            if (pendingConnection === undefined) {
                // The live record may have disappeared after this request was
                // posted (unsolicited release before host admission). A
                // handle-less unknown_handle is then still the correct
                // correlated terminal even though a generation was captured.
                if (hasConnection) return unexpected('connection_handle');
                if (value.code !== 'unknown_handle') return unexpected('connection_handle');
            } else if (pending.generation !== undefined) {
                // A still-live record with a different generation is stale;
                // success paths separately require the record to exist.
                if (pendingConnection.generation !== pending.generation) {
                    return unexpected('generation');
                }
            }
        }

        if (hasScan && !context.scans.has(value.scan_handle as string)) {
            return unexpected('scan_handle');
        }
        if (hasConnection) {
            const connection = context.connections.get(value.connection_handle as string);
            if (connection === undefined) return unexpected('connection_handle');
            if (pending.generation !== undefined && pending.generation !== connection.generation) {
                return unexpected('generation');
            }
        }
        return accept(value);
    }

    if (context.phase === 'pre_ready') {
        // Before ready only a correlated error is a valid host response.
        return unexpected('request_id');
    }
    if (hasScan && !context.scans.has(value.scan_handle as string)) {
        return unexpected('scan_handle');
    }
    if (hasConnection && !context.connections.has(value.connection_handle as string)) {
        return unexpected('connection_handle');
    }
    return accept(value);
}

export function validateInbound(value: unknown, context: ValidationContext): Validation {
    if (!isPlainObject(value)) return invalid('framing');
    if (value.envelope_version !== ENVELOPE_VERSION) return invalid('version');
    if (typeof value.type !== 'string' || !EVENT_TYPES.has(value.type)) {
        return invalid('schema');
    }

    if (value.type === 'ready') {
        if (context.phase !== 'pre_ready') return invalid('phase');
    } else if (value.type === 'error') {
        if (context.phase === 'ready') {
            if (context.apiVersion === null || value.api_version !== context.apiVersion) {
                return invalid('phase');
            }
        } else if (value.api_version !== undefined) {
            return invalid('phase');
        }
    } else {
        if (context.phase !== 'ready') return invalid('phase');
        if (context.apiVersion === null || value.api_version !== context.apiVersion) {
            return invalid('phase');
        }
    }

    switch (value.type) {
        case 'ready':
            return validateReady(value, context);
        case 'device_found':
            return validateDeviceFound(value, context);
        case 'scan_state':
            return validateScanState(value, context);
        case 'connected':
            return validateConnected(value, context);
        case 'disconnected':
            return validateDisconnected(value, context);
        case 'sent':
            return validateSent(value, context);
        case 'message':
            return validateMessage(value, context);
        case 'shutdown_complete':
            return validateShutdownComplete(value, context);
        case 'error':
            return validateError(value, context);
        default:
            return invalid('schema');
    }
}
