// Numeric ceilings for the bridge envelope and the connector's own stores.
//
// The `BRIDGE_*` values mirror `protocol/v1/limits.json` and the RFC-fixed
// ceilings in `protocol/docs/veles-v1.md` section 10.4. They describe the
// native host process. The `CONNECTOR_*` values are the connector's own
// admission bounds; they are equal to or stricter than the host bounds and
// must never be widened without a contract change.

export const BRIDGE_OUTSTANDING_REQUESTS_MAX = 64;
export const BRIDGE_OPEN_SCANS_MAX = 2;
export const BRIDGE_OPEN_CONNECTIONS_MAX = 32;
export const BRIDGE_REQUEST_TIMEOUT_SECONDS = 30;
export const BRIDGE_REQUEST_TIMEOUT_MS = BRIDGE_REQUEST_TIMEOUT_SECONDS * 1_000;
export const BRIDGE_SCAN_MAX_SECONDS = 300;
export const BRIDGE_SCAN_MAX_MS = BRIDGE_SCAN_MAX_SECONDS * 1_000;
export const BRIDGE_DEVICE_FOUND_RATE_MAX = 64;
export const BRIDGE_DEVICE_FOUND_PER_SCAN_MAX = 1024;
export const BRIDGE_DISCOVERED_DEVICES_MAX = 1024;
export const BRIDGE_OUTPUT_QUEUE_MESSAGES_MAX = 128;
export const BRIDGE_OUTPUT_QUEUE_BYTES_MAX = 262_144;
export const BRIDGE_SEND_STORAGE_RECORDS_PER_LINK = 16;
export const BRIDGE_SEND_STORAGE_BYTES_PER_LINK = 16_384;
export const BRIDGE_SEND_STORAGE_RECORDS_GLOBAL = 128;
export const BRIDGE_SEND_STORAGE_BYTES_GLOBAL = 131_072;
export const BRIDGE_PLATFORM_HINT_MAX_BYTES = 256;
export const BRIDGE_ADVERTISEMENT_MAX_BYTES = 256;
export const BRIDGE_ERROR_STAGE_MAX_BYTES = 32;
export const BRIDGE_RECORD_MAX_BYTES = 5_120;
export const RECORD_MAX_BYTES = 4_096;

// Connector-owned stores. The send queue ceiling intentionally includes the
// operation that is currently in flight, exactly like the host's
// "pending + in-flight" send storage accounting.
export const CONNECTOR_SEND_RECORDS_PER_CONNECTION = 16;
export const CONNECTOR_SEND_BYTES_PER_CONNECTION = BRIDGE_SEND_STORAGE_BYTES_PER_LINK;
export const CONNECTOR_SEND_RECORDS_GLOBAL = 128;
export const CONNECTOR_SEND_BYTES_GLOBAL = BRIDGE_SEND_STORAGE_BYTES_GLOBAL;
export const CONNECTOR_DISCOVERED_DEVICES_MAX = BRIDGE_DISCOVERED_DEVICES_MAX;
export const CONNECTOR_SCAN_TOMBSTONE_MAX = 256;
export const CONNECTOR_CONNECTION_TOMBSTONE_MAX = 4_096;
export const CONNECTOR_DIAGNOSTICS_MAX = 256;

export const PROTOCOL_LIMITS = Object.freeze({
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
});
