// Test-only independent oracle for outgoing connector envelopes.
//
// This deliberately does not call the production request validator. Its field
// sets, patterns and byte bounds are transcribed from the frozen
// `protocol/v1/envelope.schema.json` plus the normative UTF-8 byte rules in
// `protocol/docs/veles-v1.md`, so a production serializer regression cannot
// validate itself.

const REQUEST_ID = /^[A-Za-z0-9._-]{1,64}$/;
const HANDLE = /^[0-9a-f]{16}$/;
const SERVICE_UUID = '8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51';

type Message = Record<string, unknown>;

function fail(message: string): never {
    throw new Error(`outgoing envelope: ${message}`);
}

function exactKeys(message: Message, required: string[], optional: string[] = []): void {
    const allowed = new Set([...required, ...optional]);
    for (const key of required) {
        if (!Object.hasOwn(message, key)) fail(`missing ${key}`);
    }
    for (const key of Object.keys(message)) {
        if (!allowed.has(key)) fail(`unexpected field ${key}`);
        if (message[key] === null || message[key] === undefined) fail(`null/undefined ${key}`);
    }
}

function requestId(message: Message): void {
    if (typeof message.request_id !== 'string' || !REQUEST_ID.test(message.request_id)) {
        fail('request_id pattern');
    }
}

function handle(value: unknown, field: string): void {
    if (typeof value !== 'string' || !HANDLE.test(value)) fail(`${field} pattern`);
}

function platformHint(value: unknown): void {
    if (typeof value !== 'string') fail('platform_hint type');
    const bytes = Buffer.byteLength(value, 'utf8');
    if (bytes < 1 || bytes > 256) fail('platform_hint byte length');
    if (
        [...value].some((character) => {
            const code = character.codePointAt(0) ?? 0;
            return code <= 0x1f || code === 0x7f;
        })
    ) {
        fail('platform_hint control character');
    }
}

function canonicalBase64(value: unknown, min: number, max: number): void {
    if (typeof value !== 'string') fail('record_base64 type');
    if (value.length % 4 !== 0) fail('record_base64 padding shape');
    const decoded = Buffer.from(value, 'base64');
    if (decoded.toString('base64') !== value) fail('record_base64 non-canonical');
    if (decoded.length < min || decoded.length > max) fail('record_base64 decoded length');
}

function apiVersion(message: Message, postReady: boolean): void {
    if (!postReady) {
        if (Object.hasOwn(message, 'api_version')) fail('hello must not carry api_version');
        return;
    }
    if (message.api_version !== 1) fail('post-ready api_version must be 1');
}

export function assertOutgoingEnvelope(value: unknown, options: { postReady: boolean }): void {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
        fail('not an object');
    }
    const message = value as Message;
    if (message.envelope_version !== 1) fail('envelope_version');
    const type = message.type;
    if (typeof type !== 'string') fail('type');

    switch (type) {
        case 'hello': {
            exactKeys(
                message,
                ['envelope_version', 'type', 'request_id'],
                ['api_versions', 'api_version_range'],
            );
            requestId(message);
            apiVersion(message, false);
            const versions = message.api_versions;
            const range = message.api_version_range;
            if ((versions === undefined) === (range === undefined)) {
                fail('hello must carry exactly one API offer');
            }
            if (versions !== undefined) {
                if (!Array.isArray(versions) || versions.length !== 1 || versions[0] !== 1) {
                    fail('hello api_versions');
                }
            } else if (typeof range !== 'object' || range === null || Array.isArray(range)) {
                fail('hello api_version_range');
            } else {
                const { min, max, ...rest } = range as Record<string, unknown>;
                if (Object.keys(rest).length !== 0 || min !== 1 || max !== 1) {
                    fail('hello api_version_range values');
                }
            }
            return;
        }
        case 'start_scan':
            exactKeys(message, [
                'envelope_version',
                'type',
                'request_id',
                'api_version',
                'service_uuid',
            ]);
            requestId(message);
            apiVersion(message, options.postReady);
            if (message.service_uuid !== SERVICE_UUID) fail('service_uuid');
            return;
        case 'stop_scan':
            exactKeys(message, [
                'envelope_version',
                'type',
                'request_id',
                'api_version',
                'scan_handle',
            ]);
            requestId(message);
            apiVersion(message, options.postReady);
            handle(message.scan_handle, 'scan_handle');
            return;
        case 'connect': {
            exactKeys(
                message,
                ['envelope_version', 'type', 'request_id', 'api_version'],
                ['device_handle', 'platform_hint'],
            );
            requestId(message);
            apiVersion(message, options.postReady);
            const hasDevice = Object.hasOwn(message, 'device_handle');
            const hasHint = Object.hasOwn(message, 'platform_hint');
            if (hasDevice === hasHint) fail('connect must carry exactly one target');
            if (hasDevice) handle(message.device_handle, 'device_handle');
            else platformHint(message.platform_hint);
            return;
        }
        case 'disconnect':
            exactKeys(message, [
                'envelope_version',
                'type',
                'request_id',
                'api_version',
                'connection_handle',
            ]);
            requestId(message);
            apiVersion(message, options.postReady);
            handle(message.connection_handle, 'connection_handle');
            return;
        case 'send':
            exactKeys(message, [
                'envelope_version',
                'type',
                'request_id',
                'api_version',
                'connection_handle',
                'record_base64',
            ]);
            requestId(message);
            apiVersion(message, options.postReady);
            handle(message.connection_handle, 'connection_handle');
            canonicalBase64(message.record_base64, 1, 5120);
            return;
        case 'shutdown':
            exactKeys(message, ['envelope_version', 'type', 'request_id', 'api_version']);
            requestId(message);
            apiVersion(message, options.postReady);
            return;
        default:
            fail(`unexpected outgoing type ${type}`);
    }
}
