import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
    HANDSHAKE_TIMEOUT_MS,
    HEARTBEAT_MIN_INTERVAL_MS,
    HEARTBEAT_SILENCE_MS,
    HEARTBEAT_TERMINATE_THRESHOLD,
    HEARTBEAT_WINDOW_MS,
    IDLE_TIMEOUT_MS,
    SEQUENCE_MAX,
} from '../../src/session/limits';

// The engine's named limits must be the protocol's named limits, not copies
// that can drift: `protocol/v1/limits.json` is the single source of truth.

interface LimitsFile {
    limits: { name: string; value: number | string }[];
}

const limits = JSON.parse(
    readFileSync(resolve(import.meta.dirname, '../../../protocol/v1/limits.json'), 'utf8'),
) as LimitsFile;

function limit(name: string): number | string {
    const entry = limits.limits.find((candidate) => candidate.name === name);
    if (entry === undefined) throw new Error(`missing limits.json entry ${name}`);
    return entry.value;
}

describe('client session limits', () => {
    it('matches limits.json', () => {
        expect(limit('reconnect_idle_timeout_seconds')).toBe(IDLE_TIMEOUT_MS / 1_000);
        expect(limit('heartbeat_min_interval_seconds')).toBe(HEARTBEAT_MIN_INTERVAL_MS / 1_000);
        expect(limit('sequence_max')).toBe(SEQUENCE_MAX.toString());
    });

    it('anchors the §11.3 handshake wait and the OTP2-REC-034 heartbeat window', () => {
        expect(HANDSHAKE_TIMEOUT_MS).toBe(30_000);
        expect(HEARTBEAT_WINDOW_MS).toBe(60_000);
        expect(HEARTBEAT_TERMINATE_THRESHOLD).toBe(4);
        expect(HEARTBEAT_SILENCE_MS).toBe(60_000);
    });
});
