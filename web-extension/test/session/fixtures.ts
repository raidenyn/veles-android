import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// Test-only reader for the shared `protocol/v1/fixtures/crypto.json`.
//
// The unit tests read the repository's single fixture root — never a
// package-local copy — so the TypeScript consumer asserts the same committed
// bytes as the Kotlin phone engine.
const PROTOCOL_ROOT = resolve(import.meta.dirname, '../../../protocol/v1');

export const RECONNECT = 'veles-reconnect-authentication';
export const RECORD_CONFIRM = 'veles-record-reconnect-confirm';
export const RECORD_PUSH = 'veles-record-push-event';

export type JsonObject = Record<string, unknown>;

interface Fixture {
    id: string;
    fixture_version: number;
    category: string;
    input: JsonObject;
    expected: JsonObject;
}

interface FixtureEnvelope {
    version: number;
    fixtures: Fixture[];
}

function load(): Fixture[] {
    const path = resolve(PROTOCOL_ROOT, 'fixtures', 'crypto.json');
    const parsed: unknown = JSON.parse(readFileSync(path, 'utf8'));
    return (parsed as FixtureEnvelope).fixtures;
}

const all = load();

export function ids(prefix: string): string[] {
    return all.map((fixture) => fixture.id).filter((id) => id.startsWith(prefix));
}

export function byId(id: string): Fixture {
    const fixture = all.find((candidate) => candidate.id === id);
    if (fixture === undefined) throw new Error(`missing fixture id ${id}`);
    return fixture;
}

export function input(id: string): JsonObject {
    return byId(id).input;
}

export function outputs(id: string): JsonObject {
    return obj(byId(id).expected, 'outputs');
}

export function deviation(id: string): JsonObject {
    return obj(input(id), 'deviation');
}

export function str(json: JsonObject, key: string): string {
    const value = json[key];
    if (typeof value !== 'string') throw new Error(`missing fixture string field ${key}`);
    return value;
}

export function obj(json: JsonObject, key: string): JsonObject {
    const value = json[key];
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
        throw new Error(`missing fixture object ${key}`);
    }
    return value as JsonObject;
}

export function bytes(json: JsonObject, key: string): Uint8Array {
    return hex(str(json, key));
}

export function caps(json: JsonObject, key: string): Uint8Array {
    const value = json[key];
    if (!Array.isArray(value)) throw new Error(`missing fixture caps array ${key}`);
    return Uint8Array.from(value, (entry: unknown) => {
        if (typeof entry !== 'string') throw new Error(`malformed fixture capability ${key}`);
        return wire(entry);
    });
}

export function wire(value: string): number {
    return parseInt(value.replace(/^0x/, ''), 16);
}

export function hex(value: string): Uint8Array {
    const text = value.replace(/^0x/, '');
    if (text.length % 2 !== 0 || !/^[0-9a-f]*$/.test(text)) {
        throw new Error('malformed fixture hex');
    }
    return Uint8Array.from(text.match(/../g) ?? [], (pair) => parseInt(pair, 16));
}

export function hexOf(value: Uint8Array): string {
    return Array.from(value, (byte) => byte.toString(16).padStart(2, '0')).join('');
}
