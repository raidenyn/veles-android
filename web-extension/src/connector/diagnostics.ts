// Bounded, payload-free connector diagnostics.
//
// Diagnostics are never a record/OTP sink. The entry shape deliberately has
// no label, platform hint, advertisement, record byte, or base64 field. The
// ring copies only an explicit allow-list so an object carrying extra fields
// cannot leak them into the disclosure.

import { CONNECTOR_DIAGNOSTICS_MAX, BRIDGE_ERROR_STAGE_MAX_BYTES } from './limits';

export type DiagnosticDirection = 'inbound' | 'outbound' | 'local';

export interface DiagnosticEntry {
    at: number;
    direction: DiagnosticDirection;
    type: string;
    code?: string;
    stage?: string;
    scan_handle?: string;
    device_handle?: string;
    connection_handle?: string;
    bytes?: number;
}

export type DiagnosticInput = Omit<DiagnosticEntry, 'at'> & { at?: number };

const TYPE_MAX_CHARS = 64;
const CODE_MAX_CHARS = 32;
const HANDLE_MAX_CHARS = 16;

function bounded(value: string | undefined, max: number): string | undefined {
    if (value === undefined) return undefined;
    return value.slice(0, max);
}

export class DiagnosticRing {
    private readonly entriesBuffer: DiagnosticEntry[] = [];

    constructor(
        private readonly capacity: number = CONNECTOR_DIAGNOSTICS_MAX,
        private readonly now: () => number = Date.now,
    ) {}

    get size(): number {
        return this.entriesBuffer.length;
    }

    get capacityLimit(): number {
        return this.capacity;
    }

    push(input: DiagnosticInput): void {
        if (this.capacity <= 0) return;
        const entry: DiagnosticEntry = {
            at: input.at ?? this.now(),
            direction: input.direction,
            type: bounded(input.type, TYPE_MAX_CHARS) ?? 'unknown',
        };
        const code = bounded(input.code, CODE_MAX_CHARS);
        if (code !== undefined) entry.code = code;
        const stage = bounded(input.stage, BRIDGE_ERROR_STAGE_MAX_BYTES);
        if (stage !== undefined) entry.stage = stage;
        const scanHandle = bounded(input.scan_handle, HANDLE_MAX_CHARS);
        if (scanHandle !== undefined) entry.scan_handle = scanHandle;
        const deviceHandle = bounded(input.device_handle, HANDLE_MAX_CHARS);
        if (deviceHandle !== undefined) entry.device_handle = deviceHandle;
        const connectionHandle = bounded(input.connection_handle, HANDLE_MAX_CHARS);
        if (connectionHandle !== undefined) entry.connection_handle = connectionHandle;
        if (input.bytes !== undefined && Number.isInteger(input.bytes) && input.bytes >= 0) {
            entry.bytes = input.bytes;
        }

        this.entriesBuffer.push(entry);
        while (this.entriesBuffer.length > this.capacity) this.entriesBuffer.shift();
    }

    entries(): DiagnosticEntry[] {
        return this.entriesBuffer.map((entry) => ({ ...entry }));
    }

    clear(): void {
        this.entriesBuffer.length = 0;
    }
}
