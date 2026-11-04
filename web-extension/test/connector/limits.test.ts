import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import {
    BRIDGE_ADVERTISEMENT_MAX_BYTES,
    BRIDGE_DEVICE_FOUND_PER_SCAN_MAX,
    BRIDGE_DEVICE_FOUND_RATE_MAX,
    BRIDGE_DISCOVERED_DEVICES_MAX,
    BRIDGE_ERROR_STAGE_MAX_BYTES,
    BRIDGE_OPEN_CONNECTIONS_MAX,
    BRIDGE_OPEN_SCANS_MAX,
    BRIDGE_OUTPUT_QUEUE_BYTES_MAX,
    BRIDGE_OUTPUT_QUEUE_MESSAGES_MAX,
    BRIDGE_OUTSTANDING_REQUESTS_MAX,
    BRIDGE_PLATFORM_HINT_MAX_BYTES,
    BRIDGE_RECORD_MAX_BYTES,
    BRIDGE_REQUEST_TIMEOUT_SECONDS,
    BRIDGE_SCAN_MAX_SECONDS,
    BRIDGE_SEND_STORAGE_BYTES_GLOBAL,
    BRIDGE_SEND_STORAGE_BYTES_PER_LINK,
    BRIDGE_SEND_STORAGE_RECORDS_GLOBAL,
    BRIDGE_SEND_STORAGE_RECORDS_PER_LINK,
    CONNECTOR_CONNECTION_TOMBSTONE_MAX,
    CONNECTOR_DIAGNOSTICS_MAX,
    CONNECTOR_DISCOVERED_DEVICES_MAX,
    CONNECTOR_SCAN_TOMBSTONE_MAX,
    CONNECTOR_SEND_BYTES_GLOBAL,
    CONNECTOR_SEND_BYTES_PER_CONNECTION,
    CONNECTOR_SEND_RECORDS_GLOBAL,
    CONNECTOR_SEND_RECORDS_PER_CONNECTION,
    PROTOCOL_LIMITS,
    RECORD_MAX_BYTES,
} from '../../src/connector/limits';

interface LimitsDocument {
    limits: { name: string; value: number | string }[];
}

const document = JSON.parse(
    readFileSync(new URL('../../../protocol/v1/limits.json', import.meta.url), 'utf8'),
) as LimitsDocument;
const authoritative = new Map(document.limits.map((entry) => [entry.name, entry.value]));

describe('limits — protocol/v1/limits.json conformance', () => {
    it('keeps every Task 3 bridge ceiling byte-for-byte equal to the contract', () => {
        const mirrors: Record<string, number> = {
            bridge_outstanding_requests_max: BRIDGE_OUTSTANDING_REQUESTS_MAX,
            bridge_open_scans_max: BRIDGE_OPEN_SCANS_MAX,
            bridge_open_connections_max: BRIDGE_OPEN_CONNECTIONS_MAX,
            bridge_request_timeout_seconds: BRIDGE_REQUEST_TIMEOUT_SECONDS,
            bridge_scan_max_seconds: BRIDGE_SCAN_MAX_SECONDS,
            bridge_device_found_rate_max: BRIDGE_DEVICE_FOUND_RATE_MAX,
            bridge_device_found_per_scan_max: BRIDGE_DEVICE_FOUND_PER_SCAN_MAX,
            bridge_discovered_devices_max: BRIDGE_DISCOVERED_DEVICES_MAX,
            bridge_output_queue_messages_max: BRIDGE_OUTPUT_QUEUE_MESSAGES_MAX,
            bridge_output_queue_bytes_max: BRIDGE_OUTPUT_QUEUE_BYTES_MAX,
            bridge_send_storage_records_per_link: BRIDGE_SEND_STORAGE_RECORDS_PER_LINK,
            bridge_send_storage_bytes_per_link: BRIDGE_SEND_STORAGE_BYTES_PER_LINK,
            bridge_send_storage_records_global: BRIDGE_SEND_STORAGE_RECORDS_GLOBAL,
            bridge_send_storage_bytes_global: BRIDGE_SEND_STORAGE_BYTES_GLOBAL,
            bridge_platform_hint_max_bytes: BRIDGE_PLATFORM_HINT_MAX_BYTES,
            bridge_advertisement_max_bytes: BRIDGE_ADVERTISEMENT_MAX_BYTES,
            bridge_error_stage_max_bytes: BRIDGE_ERROR_STAGE_MAX_BYTES,
            bridge_record_max_bytes: BRIDGE_RECORD_MAX_BYTES,
            record_max_bytes: RECORD_MAX_BYTES,
        };
        for (const [name, mirror] of Object.entries(mirrors)) {
            expect(authoritative.get(name), name).toBe(mirror);
        }
    });

    it('exposes the full host-facing object and keeps the connector queue no wider than the host', () => {
        expect(PROTOCOL_LIMITS.bridge_outstanding_requests_max).toBe(64);
        expect(PROTOCOL_LIMITS.bridge_scan_max_seconds).toBe(300);
        expect(PROTOCOL_LIMITS.bridge_request_timeout_seconds).toBe(30);

        expect(CONNECTOR_SEND_RECORDS_PER_CONNECTION).toBeLessThanOrEqual(
            BRIDGE_SEND_STORAGE_RECORDS_PER_LINK,
        );
        expect(CONNECTOR_SEND_BYTES_PER_CONNECTION).toBeLessThanOrEqual(
            BRIDGE_SEND_STORAGE_BYTES_PER_LINK,
        );
        expect(CONNECTOR_SEND_RECORDS_GLOBAL).toBeLessThanOrEqual(
            BRIDGE_SEND_STORAGE_RECORDS_GLOBAL,
        );
        expect(CONNECTOR_SEND_BYTES_GLOBAL).toBeLessThanOrEqual(BRIDGE_SEND_STORAGE_BYTES_GLOBAL);
        expect(CONNECTOR_DISCOVERED_DEVICES_MAX).toBeLessThanOrEqual(BRIDGE_DISCOVERED_DEVICES_MAX);
    });

    it('keeps the scan lifetime distinct from the 30-second request timeout', () => {
        expect(BRIDGE_SCAN_MAX_SECONDS).toBe(300);
        expect(BRIDGE_REQUEST_TIMEOUT_SECONDS).toBe(30);
        expect(BRIDGE_SCAN_MAX_SECONDS).not.toBe(BRIDGE_REQUEST_TIMEOUT_SECONDS);
    });

    it('bounds connector-owned tombstones and diagnostics', () => {
        expect(CONNECTOR_SCAN_TOMBSTONE_MAX).toBeGreaterThan(0);
        expect(CONNECTOR_CONNECTION_TOMBSTONE_MAX).toBeGreaterThan(0);
        expect(CONNECTOR_DIAGNOSTICS_MAX).toBeGreaterThan(0);
    });
});
