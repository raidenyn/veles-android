//! Task 4 contract tests: the deterministic request/connection/cancellation core against a
//! recording fake backend and the real ingress/outqueue seams. Every test is a controlled
//! sequence on one thread: frames, injected backend events and ticks happen in explicit order
//! at an explicit clock — no sleeps, no wall-clock scheduling.

mod support;

use std::time::Duration;

use serde_json::json;

use veles_ble_host::backend::{Backend, BackendEvent, Discovered, NormalizedTarget};
use veles_ble_host::buffer::Payload;
use veles_ble_host::core::{
    EXIT_HOST_FAILURE, EXIT_INCOMPATIBLE, EXIT_OK, EXIT_OUTPUT_OVERFLOW, EXIT_REJECTED, Input,
};
use veles_ble_host::fragment::LinkCapacity;
use veles_ble_host::ingress::{BackendPop, EmitStatus};

use support::{Call, Rig, SCAN_UUID, ZERO, alias, failure, is_handle, types};

fn b64(bytes: &[u8]) -> String {
    veles_ble_host::base64::encode(bytes)
}

fn record_of(len: usize, seed: u8) -> Vec<u8> {
    (0..len)
        .map(|index| seed.wrapping_add((index % 251) as u8))
        .collect()
}

fn input(rig: &mut Rig, input: Input) {
    rig.input(input);
}

// ---- section 0: named-ceiling conformance ----------------------------------------------------------

/// Every documented builder ceiling is a real constant of the production
/// crate: the limits inventory drives the constants, and the Rust side never
/// drifts from `protocol/v1/limits.json`.
#[test]
fn ingress_and_core_ceiling_constants_match_limits_json() {
    let manifest = env!("CARGO_MANIFEST_DIR");
    let path = std::path::Path::new(manifest).join("../../protocol/v1/limits.json");
    let doc: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(path).expect("limits.json is readable"))
            .expect("limits.json parses");
    // The one u64-extreme limit is a canonical decimal string by convention;
    // every usize-comparable ceiling is a plain integer here.
    let limits: std::collections::HashMap<String, u64> = doc["limits"]
        .as_array()
        .expect("limits array")
        .iter()
        .filter_map(|limit| {
            let name = limit["name"].as_str().expect("name");
            limit["value"]
                .as_u64()
                .map(|value| (name.to_string(), value))
        })
        .collect();
    let expect = |name: &str, value: usize| {
        assert_eq!(
            limits.get(name).copied(),
            Some(value as u64),
            "limit {name} drifts"
        );
    };

    // Envelope/framing bounds are the strict parser layer's own constants.
    expect(
        "bridge_message_max_bytes",
        veles_ble_host::limits::FRAME_MAX_BYTES,
    );
    expect(
        "bridge_record_max_bytes",
        veles_ble_host::limits::DECODED_RECORD_MAX_BYTES,
    );
    expect(
        "record_max_bytes",
        veles_ble_host::limits::GATT_SEND_RECORD_MAX_BYTES,
    );

    // Request/connection core bounds.
    expect(
        "bridge_outstanding_requests_max",
        veles_ble_host::core::MAX_OUTSTANDING_REQUESTS,
    );
    expect("bridge_open_scans_max", veles_ble_host::core::MAX_SCANS);
    expect(
        "bridge_open_connections_max",
        veles_ble_host::core::MAX_CONNECTIONS,
    );
    expect(
        "bridge_request_timeout_seconds",
        veles_ble_host::core::REQUEST_TIMEOUT.as_secs() as usize,
    );
    expect(
        "bridge_scan_max_seconds",
        veles_ble_host::core::SCAN_CAP.as_secs() as usize,
    );
    expect(
        "bridge_device_found_rate_max",
        veles_ble_host::core::DEVICE_FOUND_PER_SECOND,
    );
    expect(
        "bridge_device_found_per_scan_max",
        veles_ble_host::core::DEVICE_FOUND_PER_SCAN,
    );
    expect(
        "bridge_discovered_devices_max",
        veles_ble_host::core::DEVICE_CACHE,
    );
    expect(
        "bridge_send_storage_records_per_link",
        veles_ble_host::core::SEND_RECORDS_PER_LINK,
    );
    expect(
        "bridge_send_storage_bytes_per_link",
        veles_ble_host::core::SEND_BYTES_PER_LINK,
    );
    expect(
        "bridge_send_storage_records_global",
        veles_ble_host::core::SEND_RECORDS_GLOBAL,
    );
    expect(
        "bridge_send_storage_bytes_global",
        veles_ble_host::core::SEND_BYTES_GLOBAL,
    );

    // Ingress (Task 3) seams: the staged queue ceilings the core sits on.
    expect(
        "bridge_input_frame_queue_max",
        veles_ble_host::ingress::INPUT_FRAMES_MAX,
    );
    expect(
        "bridge_input_frame_queue_bytes_max",
        veles_ble_host::ingress::INPUT_BYTES_MAX,
    );
    expect(
        "bridge_data_events_max",
        veles_ble_host::ingress::DATA_EVENTS_GLOBAL_MAX,
    );
    expect(
        "bridge_data_events_bytes_max",
        veles_ble_host::ingress::DATA_BYTES_GLOBAL_MAX,
    );
    expect(
        "bridge_data_events_per_link_max",
        veles_ble_host::ingress::DATA_EVENTS_PER_LINK_MAX,
    );
    expect(
        "bridge_data_events_bytes_per_link_max",
        veles_ble_host::ingress::DATA_BYTES_PER_LINK_MAX,
    );
    expect(
        "bridge_gate_staged_data_events_max",
        veles_ble_host::admission::STAGED_DATA_EVENTS_MAX,
    );
    expect(
        "bridge_gate_staged_data_bytes_max",
        veles_ble_host::admission::STAGED_DATA_BYTES_MAX,
    );
    expect(
        "bridge_deferred_controls_max",
        veles_ble_host::ingress::DEFERRED_CONTROLS_MAX,
    );
    // The global ceilings are exactly 32 links x the per-link ceilings (every link at its own
    // cap at once), on both axes.
    assert_eq!(
        veles_ble_host::ingress::DATA_EVENTS_GLOBAL_MAX,
        veles_ble_host::ingress::MAX_RESERVED_CONNECTIONS
            * veles_ble_host::ingress::DATA_EVENTS_PER_LINK_MAX
    );
    assert_eq!(
        veles_ble_host::ingress::DATA_BYTES_GLOBAL_MAX,
        veles_ble_host::ingress::MAX_RESERVED_CONNECTIONS
            * veles_ble_host::ingress::DATA_BYTES_PER_LINK_MAX
    );
    expect(
        "bridge_controls_per_scan_max",
        veles_ble_host::ingress::CONTROL_EVENTS_PER_SCAN_MAX,
    );
    expect(
        "bridge_controls_per_link_max",
        veles_ble_host::ingress::CONTROL_EVENTS_PER_LINK_MAX,
    );
    expect(
        "bridge_send_completions_per_link_max",
        veles_ble_host::ingress::COMPLETIONS_PER_LINK_MAX,
    );
    expect(
        "bridge_discovery_staging_max",
        veles_ble_host::ingress::DISCOVERY_STAGING_MAX,
    );
    expect(
        "bridge_platform_hint_max_bytes",
        veles_ble_host::ingress::META_STRING_MAX,
    );

    // The physical fragment-frame ceiling is the ATT attribute maximum itself
    // (one number, two owners: the transport budget and the ingress
    // pre-copy admission bound).
    expect(
        "gatt_attribute_value_max_bytes",
        veles_ble_host::ingress::FRAGMENT_FRAME_MAX,
    );
    assert_eq!(
        veles_ble_host::ingress::FRAGMENT_FRAME_MAX,
        veles_ble_host::fragment::ATTRIBUTE_VALUE_MAX
    );

    // Output queue bounds (the writer-held reservation stays included).
    expect(
        "bridge_output_queue_messages_max",
        veles_ble_host::outqueue::MAX_QUEUED_MESSAGES,
    );
    expect(
        "bridge_output_queue_bytes_max",
        veles_ble_host::outqueue::MAX_QUEUED_BYTES,
    );
}

// ---- section A: negotiation, pre-ready and fail-closed input paths ------------------------------

#[test]
fn pre_ready_rejects_non_hello_correlated_then_exits_2_without_ble() {
    let mut rig = Rig::new();
    rig.req("start_scan", "s1", json!({"service_uuid": SCAN_UUID}));
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "s1");
    assert_eq!(events[0]["code"], "invalid_request");
    assert_eq!(events[0]["recoverability"], "fatal");
    assert_eq!(events[0]["stage"], "pre_ready");
    assert_eq!(rig.exit, Some(EXIT_REJECTED));
    assert!(
        rig.only_release_submissions(),
        "no BLE work before negotiation: {:?}",
        rig.calls()
    );
}

#[test]
fn pre_ready_unusable_input_exits_2_silently_without_output_or_ble() {
    let mut rig = Rig::new();
    rig.frame_raw(b"{\"envelope_version\":1,\"type\":\"hello\"}");
    assert!(
        rig.events().is_empty(),
        "uncorrelatable input answers nothing"
    );
    assert_eq!(rig.exit, Some(EXIT_REJECTED));
    assert!(rig.only_release_submissions());
}

#[test]
fn hello_selects_highest_overlap_api_1_only() {
    let mut rig = Rig::new();
    rig.frame(json!({
        "envelope_version":1,"type":"hello","request_id":"h1",
        "api_version_range":{"min":1,"max":42}
    }));
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    let ready = &events[0];
    assert_eq!(ready["type"], "ready");
    assert_eq!(ready["request_id"], "h1");
    assert_eq!(ready["api_version"], 1);
    assert_eq!(ready["platform"], "windows");
    assert_eq!(ready["capabilities"], json!(["veles_record_v1"]));
    assert_eq!(ready["bridge_version"], "otp24-test/0");
    assert!(
        rig.calls().is_empty(),
        "no BLE work before successful negotiation"
    );
}

#[test]
fn hello_without_overlap_emits_incompatible_api_then_exits_3() {
    let mut rig = Rig::new();
    rig.frame(json!({"envelope_version":1,"type":"hello","request_id":"h2","api_versions":[2]}));
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "h2");
    assert_eq!(events[0]["code"], "incompatible_api");
    assert_eq!(events[0]["recoverability"], "fatal");
    assert_eq!(events[0]["stage"], "negotiation");
    assert_eq!(rig.exit, Some(EXIT_INCOMPATIBLE));
    assert!(rig.only_release_submissions());
}

#[test]
fn post_ready_hello_is_rejected_correlated_and_process_survives() {
    let mut rig = Rig::ready();
    rig.frame(json!({
        "envelope_version":1,"api_version":1,"type":"hello",
        "request_id":"again","api_versions":[1]
    }));
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "again");
    assert_eq!(events[0]["code"], "invalid_request");
    assert_eq!(events[0]["stage"], "post_ready");
    assert_eq!(rig.exit, None, "the process keeps serving requests");
}

#[test]
fn post_ready_unusable_frame_fails_closed_silently_exit_2() {
    let mut rig = Rig::ready();
    rig.frame_raw(b"{no usable correlation");
    assert!(rig.events().is_empty(), "nothing further is emitted");
    assert_eq!(rig.exit, Some(EXIT_REJECTED));
    assert!(rig.calls().contains(&Call::Shutdown), "resources released");
}

// ---- section B: correlation and admission caps ---------------------------------------------------

#[test]
fn duplicate_live_request_id_fails_closed_without_a_second_response() {
    let mut rig = Rig::ready();
    let (handle, _) = rig.connect_hint("c1", "phone-a", 517);
    let send = |handle: &str, seed: u8| json!({"connection_handle": handle, "record_base64": b64(&record_of(8, seed))});
    rig.req("send", "dup", send(&handle, 1));
    assert_eq!(rig.write_calls().len(), 1, "the first send went in flight");
    rig.req("send", "dup", send(&handle, 2));
    assert_eq!(
        rig.exit,
        Some(EXIT_REJECTED),
        "duplicate live request ID fails closed"
    );
    assert!(rig.events().is_empty(), "no second response is emitted");
    assert!(rig.calls().contains(&Call::Shutdown), "resources released");
}

#[test]
fn writer_held_request_id_cannot_be_reused() {
    let mut rig = Rig::ready();
    let (handle, _) = rig.connect_hint("c2", "phone-b", 517);
    rig.req(
        "send",
        "held",
        json!({"connection_handle": handle, "record_base64": b64(&record_of(8, 3))}),
    );
    let (conn, send, _) = rig.last_write();
    rig.inject(BackendEvent::WriteDone { conn, send });
    // The completion is queued: pop it the way a frozen writer holds it
    // (never finish_write), so its request ID reservation stays with the writer.
    let held_frame = rig.pop_held();
    assert_eq!(rig.exit, None);
    rig.frame(json!({
        "envelope_version":1,"api_version":1,"type":"send","request_id":"held",
        "connection_handle": handle, "record_base64": b64(&record_of(8, 4))
    }));
    assert_eq!(
        rig.exit,
        Some(EXIT_REJECTED),
        "an id whose completion is writer-held fails closed"
    );
    assert!(
        rig.calls().contains(&Call::Shutdown),
        "resources released ({held_frame:?})"
    );
}

#[test]
fn request_id_is_reusable_once_its_terminal_was_delivered() {
    let mut rig = Rig::ready();
    let (handle, _) = rig.connect_hint("c3", "phone-c", 517);
    for attempt in 0..2u8 {
        rig.req(
            "send",
            "reuse",
            json!({"connection_handle": handle, "record_base64": b64(&record_of(8, attempt))}),
        );
        assert_eq!(
            rig.exit, None,
            "attempt {attempt}: delivered terminal first"
        );
        let (conn, send, _) = rig.last_write();
        rig.inject(BackendEvent::WriteDone { conn, send });
        let events = rig.events();
        assert_eq!(events[0]["type"], "sent");
        assert_eq!(events[0]["request_id"], "reuse");
    }
    assert_eq!(rig.write_calls().len(), 2, "one accepted write per attempt");
}

#[test]
fn shutdown_at_the_full_request_cap_is_cap_exceeded_without_teardown() {
    // The outstanding-request bound has no shutdown exception (OTP2-BRD-030):
    // with 64 live requests the `shutdown` request itself is refused with the
    // correlated retryable `cap_exceeded` and NOTHING is torn down (earlier
    // sends stay pending; the process keeps serving).
    let mut rig = Rig::ready();
    let mut tokens = Vec::new();
    for link in 0..32 {
        tokens.push(rig.connect_hint(&format!("c{link}"), &format!("phone-{link}"), 517));
    }
    for (link, (handle, _)) in tokens.iter().enumerate() {
        rig.req(
            "send",
            &format!("r{link}-0"),
            json!({"connection_handle": handle, "record_base64": b64(&record_of(8, 1))}),
        );
        rig.req(
            "send",
            &format!("r{link}-1"),
            json!({"connection_handle": handle, "record_base64": b64(&record_of(8, 2))}),
        );
    }
    assert_eq!(rig.write_calls().len(), 32);

    rig.req("shutdown", "off", json!({}));
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "off");
    assert_eq!(events[0]["code"], "cap_exceeded");
    assert_eq!(events[0]["recoverability"], "retryable");
    assert_eq!(events[0]["stage"], "requests");
    assert_eq!(rig.exit, None, "no exit: the process keeps serving");
    assert!(
        !rig.calls().contains(&Call::Shutdown),
        "the refused shutdown tears nothing down: {:?}",
        rig.calls()
    );

    // The 64 outstanding requests are intact; one completes normally.
    let (conn, send, _) = rig.write_calls()[0];
    rig.inject(BackendEvent::WriteDone { conn, send });
    let events = rig.events();
    assert_eq!(events[0]["type"], "sent");
    assert_eq!(events[0]["request_id"], "r0-0");
    assert_eq!(rig.exit, None);

    // Now the cap has room: the shutdown handshake works again.
    rig.req("shutdown", "bye", json!({}));
    rig.inject(BackendEvent::ShutdownComplete);
    let events = rig.events();
    assert_eq!(events[events.len() - 1]["type"], "shutdown_complete");
    assert_eq!(events[events.len() - 1]["request_id"], "bye");
    assert_eq!(rig.exit, Some(EXIT_OK));
}

#[test]
fn the_65th_outstanding_request_is_cap_exceeded_and_survivors_continue() {
    let mut rig = Rig::ready();
    let mut handles = Vec::new();
    for link in 0..32 {
        let (handle, _) = rig.connect_hint(&format!("c{link}"), &format!("phone-{link}"), 517);
        handles.push(handle);
    }
    // 64 outstanding requests: one in-flight + one queued send per link.
    for (link, handle) in handles.iter().enumerate() {
        rig.req(
            "send",
            &format!("r{link}-0"),
            json!({"connection_handle": handle, "record_base64": b64(&record_of(8, 1))}),
        );
        rig.req(
            "send",
            &format!("r{link}-1"),
            json!({"connection_handle": handle, "record_base64": b64(&record_of(8, 2))}),
        );
    }
    assert_eq!(rig.write_calls().len(), 32, "one in-flight batch per link");
    rig.req(
        "send",
        "r65",
        json!({"connection_handle": handles[0], "record_base64": b64(&record_of(8, 3))}),
    );
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "r65");
    assert_eq!(events[0]["code"], "cap_exceeded");
    assert_eq!(events[0]["recoverability"], "retryable");
    assert_eq!(
        rig.exit, None,
        "the 65th request is rejected without an exit"
    );

    // Survivors continue: completing link 0's send admits fresh work again.
    let (conn, send, _) = rig.write_calls()[0];
    rig.inject(BackendEvent::WriteDone { conn, send });
    let events = rig.events();
    assert_eq!(events[0]["type"], "sent");
    assert_eq!(events[0]["request_id"], "r0-0");
    rig.req(
        "send",
        "r65b",
        json!({"connection_handle": handles[0], "record_base64": b64(&record_of(8, 4))}),
    );
    assert_eq!(rig.exit, None, "freed capacity admits the request again");
}

#[test]
fn third_scan_is_cap_exceeded_retryably() {
    let mut rig = Rig::ready();
    let (_h1, _t1) = rig.open_scan("s1");
    let (_h2, _t2) = rig.open_scan("s2");
    rig.req("start_scan", "s3", json!({"service_uuid": SCAN_UUID}));
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "s3");
    assert_eq!(events[0]["code"], "cap_exceeded");
    assert_eq!(events[0]["recoverability"], "retryable");
    assert_eq!(rig.exit, None);
    assert_eq!(
        rig.calls().len(),
        2,
        "no backend start_scan for the rejected request"
    );
}

#[test]
fn thirty_third_connection_is_cap_exceeded_and_closing_links_stay_counted() {
    let mut rig = Rig::ready();
    let mut tokens = Vec::new();
    for link in 0..32 {
        rig.req(
            "connect",
            &format!("c{link}"),
            json!({"platform_hint": format!("p{link}")}),
        );
        tokens.push(rig.connect_token());
    }
    rig.req("connect", "c33", json!({"platform_hint": "other"}));
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "c33");
    assert_eq!(events[0]["code"], "cap_exceeded");
    assert_eq!(events[0]["recoverability"], "retryable");
    assert_eq!(rig.exit, None);
    assert_eq!(
        rig.calls().len(),
        32,
        "no Connect submission for the rejected request"
    );

    // 31 pending + 1 open: disconnect that link and re-check with it closing.
    let connected = rig.connected(tokens[0], 517);
    let handle0 = connected[0]["connection_handle"]
        .as_str()
        .unwrap()
        .to_string();
    rig.req("disconnect", "d0", json!({"connection_handle": handle0}));
    let events = rig.events();
    assert_eq!(events[0]["type"], "disconnected");
    assert_eq!(events[0]["request_id"], "d0");

    // 31 live + 1 closing = 32: a new connection is still refused while the
    // closing link owns its slot.
    rig.req("connect", "c33b", json!({"platform_hint": "other"}));
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["code"], "cap_exceeded");
    assert_eq!(rig.exit, None);

    // The observed OS release frees the closing slot.
    rig.inject(BackendEvent::LinkReleased { conn: tokens[0] });
    rig.req("connect", "c33c", json!({"platform_hint": "other"}));
    assert_eq!(rig.exit, None, "the released slot admits the connection");
}

// ---- section C: scan lifecycle --------------------------------------------------------------------

#[test]
fn scan_lifecycle_start_discover_stop_is_exact() {
    let mut rig = Rig::ready();
    let (scan_handle, token) = rig.open_scan("s1");
    let first = rig.advertise(token, "hint-1", "First");
    let second = rig.advertise(token, "hint-2", "Second");
    let events = [first, second];
    for event in &events {
        assert_eq!(event["type"], "device_found");
        assert!(event.get("request_id").is_none(), "{event:?}");
        assert_eq!(event["scan_handle"], scan_handle);
        assert!(is_handle(&event["device_handle"]));
    }
    assert_eq!(events[0]["label"], "First");
    assert_eq!(events[1]["label"], "Second");

    rig.req("stop_scan", "stop", json!({"scan_handle": scan_handle}));
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    assert_eq!(events[0]["type"], "scan_state");
    assert_eq!(events[0]["status"], "stopped");
    assert_eq!(events[0]["request_id"], "stop");
    assert_eq!(events[0]["scan_handle"], scan_handle);
    assert!(rig.calls().contains(&Call::StopScan(token)));

    // The stopped scan is only CLOSING: its reservation stays (so the release
    // can be delivered) and a late advertisement is staged by the ingress but
    // never disclosed by the core.
    rig.inject(BackendEvent::Advertisement {
        scan: token,
        device: Discovered {
            platform_hint: "hint-3".into(),
            label: "Third".into(),
            advertisement: None,
        },
    });
    assert!(
        rig.events().is_empty(),
        "no device_found for a closing scan"
    );

    // Once the OS release is observed the reservation is gone: later stale
    // callbacks are dropped at the ingress.
    rig.inject(BackendEvent::ScanReleased { scan: token });
    assert_eq!(
        rig.inject_status(BackendEvent::Advertisement {
            scan: token,
            device: Discovered {
                platform_hint: "hint-4".into(),
                label: "Fourth".into(),
                advertisement: None,
            },
        }),
        EmitStatus::AdvertisementDropped,
        "the released scan's reservation is gone"
    );
    assert!(matches!(
        rig.receiver.pop_event(Some(ZERO)),
        BackendPop::Timeout
    ));
    assert!(
        rig.events().is_empty(),
        "no later event for released handles"
    );
    assert_eq!(rig.exit, None);
}

#[test]
fn stop_scan_unknown_handle_errors_correlated() {
    let mut rig = Rig::ready();
    rig.req(
        "stop_scan",
        "u1",
        json!({"scan_handle": "0000000000000000"}),
    );
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "u1");
    assert_eq!(events[0]["code"], "unknown_handle");
    assert_eq!(events[0]["stage"], "scan_handle");
    assert_eq!(rig.exit, None);
    assert!(rig.calls().is_empty());
}

#[test]
fn scan_failed_is_correlated_with_backend_failure() {
    let mut rig = Rig::ready();
    rig.req("start_scan", "s1", json!({"service_uuid": SCAN_UUID}));
    let token = rig.start_scan_token();
    rig.inject(BackendEvent::ScanFailed {
        scan: token,
        failure: failure("radio_off", true),
    });
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "s1");
    assert_eq!(events[0]["code"], "scan_failed");
    assert_eq!(events[0]["recoverability"], "retryable");
    assert_eq!(events[0]["stage"], "radio_off");
    assert_eq!(rig.exit, None);
}

#[test]
fn scan_ended_with_failure_emits_error_then_scan_state() {
    let mut rig = Rig::ready();
    let (scan_handle, token) = rig.open_scan("s1");
    rig.inject(BackendEvent::ScanEnded {
        scan: token,
        failure: Some(failure("internal_ble", false)),
    });
    let events = rig.events();
    assert_eq!(events.len(), 2, "{events:?}");
    assert_eq!(events[0]["type"], "error");
    assert!(events[0].get("request_id").is_none(), "unsolicited");
    assert_eq!(events[0]["code"], "scan_failed");
    assert_eq!(events[0]["scan_handle"], scan_handle);
    assert_eq!(events[0]["stage"], "internal_ble");
    assert_eq!(events[1]["type"], "scan_state");
    assert_eq!(events[1]["status"], "stopped");
    assert!(events[1].get("request_id").is_none());
    assert_eq!(rig.exit, None);
}

#[test]
fn scan_300s_cap_expires_unsolicited_and_stops_backend() {
    let mut rig = Rig::ready();
    let (scan_handle, token) = rig.open_scan("s1");
    rig.advance(Duration::from_secs(300));
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    assert_eq!(events[0]["type"], "scan_state");
    assert_eq!(events[0]["status"], "stopped");
    assert!(events[0].get("request_id").is_none(), "unsolicited expiry");
    assert_eq!(events[0]["scan_handle"], scan_handle);
    assert!(rig.calls().contains(&Call::StopScan(token)));
    assert_eq!(rig.exit, None);
}

#[test]
fn scan_299s_is_still_alive() {
    let mut rig = Rig::ready();
    let (_scan_handle, _token) = rig.open_scan("s1");
    rig.advance(Duration::from_secs(299));
    assert!(rig.events().is_empty(), "no expiry before 300 s");
    assert_eq!(rig.exit, None);
}

#[test]
fn hostile_fragment_conflict_disconnects_the_link() {
    let mut rig = Rig::ready();
    let (handle, conn) = rig.connect_hint("c1", "phone-1", 517);
    // A live partial transaction first (a valid non-final index-0 fragment),
    // then repeated index-0 starts of the same transaction id within the 60 s
    // conflict window. Conflict one erases the live partial and is rejected
    // (no event); a fresh start of the id is live again; conflict two reports
    // a peer disconnect and the core tears the link down.
    let start = Payload::new({
        let mut frame = vec![0x00, 7, 0x02, 0x58];
        frame.extend_from_slice(&[0x55; 508]);
        frame
    });
    rig.inject(BackendEvent::Fragment {
        conn,
        bytes: start.clone(),
    });
    rig.inject(BackendEvent::Fragment {
        conn,
        bytes: start.clone(),
    });
    assert!(rig.events().is_empty(), "the first conflict is rejected");
    rig.inject(BackendEvent::Fragment {
        conn,
        bytes: start.clone(),
    });
    assert!(
        rig.events().is_empty(),
        "a fresh start after the erase is live"
    );
    rig.inject(BackendEvent::Fragment { conn, bytes: start });
    let events = rig.events();
    assert_eq!(types(&events), vec!["disconnected"], "{events:?}");
    assert!(events[0].get("request_id").is_none(), "unsolicited");
    assert_eq!(events[0]["connection_handle"], handle);
    assert!(rig.calls().contains(&Call::Disconnect(conn)));
    assert_eq!(rig.exit, None);
}

#[test]
fn discovered_1024_per_scan_cap_drops_further_observations() {
    let mut rig = Rig::ready();
    let (scan_handle, token) = rig.open_scan("s1");
    // 1024 accepted discoveries in batches of 64 per rolling second.
    for second in 0..16 {
        for device in 0..64 {
            rig.advertise(
                token,
                &format!("hint-{second:04}-{device:02}"),
                &format!("Label {second}"),
            );
        }
        if second < 15 {
            rig.advance(Duration::from_secs(1));
        }
    }
    assert_eq!(rig.calls().len(), 1, "one start_scan submission");
    rig.req("stop_scan", "stop", json!({"scan_handle": scan_handle}));
    let _ = rig.events();
}

// ---- section D: connections -----------------------------------------------------------------------

#[test]
fn connect_by_cached_device_handle_connects() {
    let mut rig = Rig::ready();
    let (scan_handle, token) = rig.open_scan("s1");
    let found = rig.advertise(token, "hint-1", "First");
    let device_handle = found["device_handle"].as_str().unwrap().to_string();
    rig.req("stop_scan", "stop", json!({"scan_handle": scan_handle}));
    let _ = rig.events();
    rig.req("connect", "c1", json!({"device_handle": device_handle}));
    let token = rig.connect_token();
    assert!(
        rig.calls()
            .contains(&Call::Connect(token, support_hint("hint-1"))),
        "{:?}",
        rig.calls()
    );
    let connected = rig.connected(token, 517);
    assert_eq!(connected.len(), 1, "{connected:?}");
    assert_eq!(connected[0]["type"], "connected");
    assert_eq!(connected[0]["request_id"], "c1");
    assert!(is_handle(&connected[0]["connection_handle"]));
}

fn support_hint(hint: &str) -> veles_ble_host::backend::Target {
    veles_ble_host::backend::Target::Hint(hint.to_string())
}

#[test]
fn connect_with_unknown_device_handle_errors() {
    let mut rig = Rig::ready();
    rig.req(
        "connect",
        "c1",
        json!({"device_handle": "ffffffffffffffff"}),
    );
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "c1");
    assert_eq!(events[0]["code"], "unknown_handle");
    assert_eq!(events[0]["stage"], "device_handle");
    assert_eq!(rig.exit, None);
    assert!(rig.calls().is_empty(), "no Connect for an unknown device");
}

#[test]
fn disconnect_of_unknown_or_released_handle_errors() {
    // An unknown handle is refused correlated, with no backend call.
    let mut rig = Rig::ready();
    rig.req(
        "disconnect",
        "d0",
        json!({"connection_handle": "ffffffffffffffff"}),
    );
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "d0");
    assert_eq!(events[0]["code"], "unknown_handle");
    assert_eq!(events[0]["stage"], "connection_handle");
    assert_eq!(rig.exit, None);
    assert!(rig.calls().is_empty());

    // A handle de-registered at teardown refuses further tear-down as well.
    let (handle, conn) = rig.connect_hint("c1", "phone-1", 517);
    rig.req("disconnect", "bye", json!({"connection_handle": handle}));
    let events = rig.events();
    assert_eq!(events[0]["request_id"], "bye");
    assert!(rig.calls().contains(&Call::Disconnect(conn)));
    rig.req("disconnect", "again", json!({"connection_handle": handle}));
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "again");
    assert_eq!(events[0]["code"], "unknown_handle");
    assert_eq!(events[0]["stage"], "connection_handle");
    assert_eq!(rig.exit, None);
}

#[test]
fn pending_duplicate_target_connect_is_busy_until_link_released() {
    let mut rig = Rig::ready();
    rig.req("connect", "c1", json!({"platform_hint": "phone-1"}));
    let token = rig.connect_token();

    // While the first connect is pending, the same target is refused (busy).
    rig.req("connect", "c2", json!({"platform_hint": "phone-1"}));
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "c2");
    assert_eq!(events[0]["code"], "busy");
    assert_eq!(events[0]["recoverability"], "retryable");
    assert_eq!(events[0]["stage"], "already_connected");
    assert_eq!(
        rig.calls().len(),
        1,
        "no Connect submission for the busy request"
    );

    // Setup failure closes the link: the target stays fenced while closing.
    // (Radio-off is the reviewed retryable classification; retryability
    // follows the backend's failure verbatim.)
    rig.inject(BackendEvent::ConnectFailed {
        conn: token,
        failure: failure("radio_off", true),
    });
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "c1");
    assert_eq!(events[0]["code"], "connect_failed");
    assert_eq!(events[0]["recoverability"], "retryable");
    assert_eq!(events[0]["stage"], "radio_off");
    assert!(
        events[0].get("connection_handle").is_none(),
        "never-disclosed handle"
    );
    assert_eq!(rig.exit, None);

    rig.req("connect", "c3", json!({"platform_hint": "phone-1"}));
    let events = rig.events();
    assert_eq!(types(&events), vec!["error".to_string()], "{events:?}");
    assert_eq!(events[0]["request_id"], "c3");
    assert_eq!(
        events[0]["code"], "busy",
        "the closing link still fences the target"
    );

    // Only the observed OS release lifts the fence.
    rig.inject(BackendEvent::LinkReleased { conn: token });
    rig.req("connect", "c4", json!({"platform_hint": "phone-1"}));
    assert_eq!(rig.exit, None, "a fresh connect is accepted after release");
    let token = rig.connect_token();
    let connected = rig.connected(token, 517);
    assert_eq!(connected[0]["type"], "connected");
    assert_eq!(connected[0]["request_id"], "c4");
}

#[test]
fn unverified_capacity_closes_the_connecting_link() {
    let mut rig = Rig::ready();
    rig.req("connect", "c1", json!({"platform_hint": "phone-1"}));
    let token = rig.connect_token();
    rig.inject(BackendEvent::Connected {
        conn: token,
        capacity: LinkCapacity::AttMtu(100),
        platform_hint: None,
    });
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "c1");
    assert_eq!(events[0]["code"], "connect_failed");
    assert_eq!(events[0]["recoverability"], "fatal");
    assert_eq!(events[0]["stage"], "mtu_range");
    assert_eq!(rig.exit, None);

    // Undetermined write capacity (macOS backend policy) refuses the link too.
    rig.inject(BackendEvent::LinkReleased { conn: token });
    rig.req("connect", "c2", json!({"platform_hint": "phone-2"}));
    let token = rig.connect_token();
    rig.inject(BackendEvent::Connected {
        conn: token,
        capacity: LinkCapacity::MaxWriteWithoutResponse(399),
        platform_hint: None,
    });
    let events = rig.events();
    assert_eq!(events[0]["request_id"], "c2");
    assert_eq!(events[0]["stage"], "write_capacity");
    assert_eq!(rig.exit, None);
}

#[test]
fn connect_setup_failure_is_correlated_connect_failed() {
    let mut rig = Rig::ready();
    rig.req("connect", "c1", json!({"platform_hint": "phone-1"}));
    let token = rig.connect_token();
    rig.inject(BackendEvent::ConnectFailed {
        conn: token,
        failure: failure("access_denied", false),
    });
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "c1");
    assert_eq!(events[0]["code"], "connect_failed");
    assert_eq!(events[0]["recoverability"], "fatal");
    assert_eq!(events[0]["stage"], "access_denied");
    assert_eq!(rig.exit, None);
}

#[test]
fn fully_pinned_discovery_cache_drops_the_discovery_and_preserves_actives() {
    // The reachable fully-pinned cache: every cache entry carries the same
    // connected target's hint (repeated rediscovery), so `hint_in_use` pins
    // ALL of them and no eviction candidate exists. The cache must stay at
    // exactly 1,024: further discoveries are dropped whole (no device handle
    // is emitted that cannot be resolved; no active entry is evicted), and
    // entries become evictable again once the target's release is observed.
    let mut rig = Rig::ready();
    let (initial_handle, initial_token) = rig.open_scan("s0");
    let found = rig.advertise(initial_token, "phone-1", "Phone One");
    let active_handle = found["device_handle"].as_str().unwrap().to_string();

    rig.req(
        "connect",
        "c1",
        json!({"device_handle": active_handle.clone()}),
    );
    let connect_token = rig.connect_token();
    let connected = rig.connected(connect_token, 517);
    assert_eq!(types(&connected), vec!["connected"], "{connected:?}");
    let conn_handle = connected[0]["connection_handle"]
        .as_str()
        .unwrap()
        .to_string();

    // 1,023 further rediscoveries of the connected hint, one per fresh scan
    // (each scan's dedupe allows one entry and stop_scan precedes each open
    // to keep the 2-scan cap). Every rediscovery still emits its fresh
    // handle, so the cache grows exactly to its 1,024-entry bound.
    let mut current_handle = initial_handle;
    let mut current_token = initial_token;
    for cycle in 0..1023usize {
        rig.req(
            "stop_scan",
            &format!("stop{cycle}"),
            json!({"scan_handle": current_handle}),
        );
        let _ = rig.events();
        // A stopped scan stays counted until its OS release is observed.
        rig.inject(BackendEvent::ScanReleased {
            scan: current_token,
        });
        rig.req(
            "start_scan",
            &format!("s{cycle}"),
            json!({"service_uuid": SCAN_UUID}),
        );
        let token = rig.start_scan_token();
        rig.inject(BackendEvent::ScanStarted { scan: token });
        let started = rig.events();
        assert_eq!(started[0]["status"], "started", "{started:?}");
        current_handle = started[0]["scan_handle"].as_str().unwrap().to_string();
        current_token = token;
        rig.advertise(token, "phone-1", "Phone One");
    }
    let last_started = rig.events().is_empty();
    assert!(last_started, "harness consistency");
    let live_scan = rig.start_scan_token();

    // The cache is at its 1,024-entry cap and every entry is pinned by the
    // connected target: a new discovery is dropped whole (no device_found,
    // no unresolvable handle, no evicted active entry).
    rig.inject(BackendEvent::Advertisement {
        scan: live_scan,
        device: Discovered {
            platform_hint: "phone-2".into(),
            label: "Phone Two".into(),
            advertisement: None,
        },
    });
    assert!(
        rig.events().is_empty(),
        "no device_found for the fully-pinned cache"
    );
    assert_eq!(rig.exit, None);

    // The active entries are preserved while the link lives: a previously
    // disclosed handle of the connected target still resolves (refused only
    // by the target fence, never by an unknown handle).
    rig.req(
        "connect",
        "again",
        json!({"device_handle": active_handle.clone()}),
    );
    let events = rig.events();
    assert_eq!(types(&events), vec!["error"], "{events:?}");
    assert_eq!(events[0]["request_id"], "again");
    assert_eq!(
        events[0]["code"], "busy",
        "the active entry resolves: {events:?}"
    );
    assert_eq!(rig.exit, None);

    // Only the observed release un-pins: while the link is closing the cache
    // stays pinned; after LinkReleased the next discovery evicts the oldest
    // non-connected entry and is admitted again.
    rig.req(
        "disconnect",
        "bye",
        json!({"connection_handle": conn_handle}),
    );
    let events = rig.events();
    assert_eq!(types(&events), vec!["disconnected"], "{events:?}");
    assert_eq!(events[0]["request_id"], "bye");
    rig.inject(BackendEvent::Advertisement {
        scan: live_scan,
        device: Discovered {
            platform_hint: "phone-2".into(),
            label: "Phone Two".into(),
            advertisement: None,
        },
    });
    assert!(
        rig.events().is_empty(),
        "the closing link still pins its cache entries"
    );
    rig.inject(BackendEvent::LinkReleased {
        conn: connect_token,
    });
    rig.inject(BackendEvent::Advertisement {
        scan: live_scan,
        device: Discovered {
            platform_hint: "phone-2".into(),
            label: "Phone Two".into(),
            advertisement: None,
        },
    });
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    assert_eq!(
        events[0]["type"], "device_found",
        "the cache is evictable again"
    );
    assert_eq!(rig.exit, None);
}

#[test]
fn discovery_cache_eviction_fresh_handle_after_eviction() {
    let mut rig = Rig::ready();
    let (scan_a, token_a) = rig.open_scan("sa");
    let (scan_b, token_b) = rig.open_scan("sb");
    let _ = (scan_a, scan_b);

    // 1024 distinct devices in batches of 64 per rolling second (rate cap).
    let mut handles = Vec::new();
    for second in 0..16 {
        for device in 0..64u32 {
            let hint = format!("d-{second:04}-{device:03}");
            let found = rig.advertise(token_a, &hint, &hint);
            handles.push(found["device_handle"].as_str().unwrap().to_string());
        }
        if second < 15 {
            rig.advance(Duration::from_secs(1));
        }
    }
    let first = handles[0].clone();

    // Scan A is at its per-scan cap: further observations are dropped there.
    rig.inject(BackendEvent::Advertisement {
        scan: token_a,
        device: Discovered {
            platform_hint: "d-cap".into(),
            label: "late-cap".into(),
            advertisement: None,
        },
    });
    assert!(
        rig.events().is_empty(),
        "the capped scan admits no more devices"
    );

    // A device on scan B evicts the oldest non-connected cache entry.
    let found = rig.advertise(token_b, "d-new", "Extra");
    let new_handle = found["device_handle"].as_str().unwrap().to_string();
    assert_ne!(new_handle, first, "every handle is fresh");

    // The evicted (oldest) device handle is now unknown; connect fails.
    rig.req("connect", "evicted", json!({"device_handle": first}));
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["code"], "unknown_handle");
    assert_eq!(events[0]["stage"], "device_handle");
    assert_eq!(rig.exit, None);

    // A later rediscovery yields a fresh handle that works.
    let rediscovered = rig.advertise(token_b, "d-0000-000", "First again");
    let fresh = rediscovered["device_handle"].as_str().unwrap().to_string();
    rig.req("connect", "fresh", json!({"device_handle": fresh}));
    assert_eq!(
        rig.exit, None,
        "a rediscovered handle is cached and connectable"
    );
    let token = rig.connect_token();
    let connected = rig.connected(token, 517);
    assert_eq!(connected[0]["type"], "connected");
    assert_eq!(connected[0]["request_id"], "fresh");
}

// ---- section E: sends ------------------------------------------------------------------------------

#[test]
fn oversize_send_never_calls_backend() {
    let mut rig = Rig::ready();
    let (handle, _) = rig.connect_hint("c1", "phone-1", 517);
    for size in [4097usize, 5120usize] {
        rig.req(
            "send",
            &format!("big{size}"),
            json!({"connection_handle": handle, "record_base64": b64(&record_of(size, 9))}),
        );
        let events = rig.events();
        assert_eq!(events.len(), 1, "size {size}: {events:?}");
        assert_eq!(events[0]["type"], "error");
        assert_eq!(events[0]["request_id"], format!("big{size}"));
        assert_eq!(events[0]["code"], "oversize_record");
        assert_eq!(events[0]["recoverability"], "fatal");
        assert_eq!(events[0]["stage"], "record");
        assert_eq!(events[0]["connection_handle"], handle);
        assert_eq!(rig.exit, None);
        assert!(
            rig.write_calls().is_empty(),
            "no fragments/writes for {size} bytes"
        );
    }
    // The live handle remains usable.
    rig.req(
        "send",
        "small",
        json!({"connection_handle": handle, "record_base64": b64(&record_of(100, 3))}),
    );
    assert_eq!(
        rig.write_calls().len(),
        1,
        "exactly the small send went to the backend"
    );
    assert_eq!(rig.exit, None);
}

#[test]
fn full_per_link_send_storage_rejects_retryably_and_keeps_earlier_work() {
    let mut rig = Rig::ready();
    let (handle, _) = rig.connect_hint("c1", "phone-1", 517);
    for send in 0..16 {
        rig.req(
            "send",
            &format!("r{send}"),
            json!({"connection_handle": handle, "record_base64": b64(&record_of(8, send))}),
        );
    }
    assert_eq!(rig.write_calls().len(), 1, "one in-flight; 15 queued");
    rig.req(
        "send",
        "r17",
        json!({"connection_handle": handle, "record_base64": b64(&record_of(8, 90))}),
    );
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "r17");
    assert_eq!(events[0]["code"], "cap_exceeded");
    assert_eq!(events[0]["recoverability"], "retryable");
    assert_eq!(events[0]["stage"], "send_queue");
    assert_eq!(events[0]["connection_handle"], handle);
    assert_eq!(rig.exit, None);
    assert_eq!(
        rig.write_calls().len(),
        1,
        "the rejected record was never submitted"
    );

    // Earlier work is not cancelled: in-order completion keeps streaming, one
    // in-flight send at a time.
    let (conn, first, _) = rig.write_calls()[0];
    rig.inject(BackendEvent::WriteDone { conn, send: first });
    let events = rig.events();
    assert_eq!(events[0]["type"], "sent");
    assert_eq!(events[0]["request_id"], "r0");
    let (conn, second, _) = rig.write_calls()[1];
    rig.inject(BackendEvent::WriteDone { conn, send: second });
    let events = rig.events();
    assert_eq!(events[0]["request_id"], "r1");
    // One freed slot admits new work (16 records accounted again).
    rig.req(
        "send",
        "r17b",
        json!({"connection_handle": handle, "record_base64": b64(&record_of(8, 5))}),
    );
    assert_eq!(rig.exit, None, "freed send storage admits the request");
}

#[test]
fn global_send_storage_bytes_cap_rejects_retryably_without_cancelling() {
    let mut rig = Rig::ready();
    let mut handles = Vec::new();
    for link in 0..8 {
        let (handle, _) = rig.connect_hint(&format!("c{link}"), &format!("p{link}"), 517);
        handles.push(handle);
    }
    // 8 links x 4 records of 4096 bytes = 32 records / 131,072 bytes (the
    // global send-storage byte cap exactly).
    for (link, handle) in handles.iter().enumerate() {
        for send in 0..4 {
            rig.req(
                "send",
                &format!("b{link}-{send}"),
                json!({"connection_handle": handle, "record_base64": b64(&record_of(4096, 1))}),
            );
        }
    }
    assert_eq!(rig.write_calls().len(), 8, "one in-flight per link");
    // A ninth link has free per-link budget but the global byte cap is full.
    let (fresh, fresh_token) = rig.connect_hint("c9", "p9", 517);
    let _ = fresh_token;
    rig.req(
        "send",
        "one-more",
        json!({"connection_handle": fresh, "record_base64": b64(&record_of(8, 2))}),
    );
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "one-more");
    assert_eq!(events[0]["code"], "cap_exceeded");
    assert_eq!(events[0]["recoverability"], "retryable");
    assert_eq!(events[0]["stage"], "send_queue");
    assert_eq!(rig.exit, None);
    assert_eq!(
        rig.write_calls().len(),
        8,
        "the 32 accepted sends are untouched"
    );

    // One completed send frees 4096 global bytes; the small send is admitted.
    let (conn, send, _) = rig.last_write();
    rig.inject(BackendEvent::WriteDone { conn, send });
    let _ = rig.events();
    rig.req(
        "send",
        "one-more",
        json!({"connection_handle": fresh, "record_base64": b64(&record_of(8, 2))}),
    );
    assert_eq!(rig.exit, None, "freed global bytes admit the send");
}

#[test]
fn send_times_out_after_30s_correlated_then_fenced() {
    let mut rig = Rig::ready();
    let (handle, _) = rig.connect_hint("c1", "phone-1", 517);
    rig.req(
        "send",
        "slow",
        json!({"connection_handle": handle, "record_base64": b64(&record_of(8, 1))}),
    );
    let (conn, send, _) = rig.last_write();
    rig.advance(Duration::from_secs(29));
    assert!(rig.events().is_empty(), "no expiry before 30 s");
    rig.advance(Duration::from_secs(1));
    let events = rig.events();
    assert_eq!(events.len(), 2, "{events:?}");
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "slow");
    assert_eq!(events[0]["code"], "timeout");
    assert_eq!(events[0]["recoverability"], "retryable");
    assert_eq!(events[0]["connection_handle"], handle);
    assert_eq!(events[1]["type"], "disconnected");
    assert!(events[1].get("request_id").is_none(), "unsolicited");
    assert_eq!(events[1]["connection_handle"], handle);
    assert!(rig.calls().contains(&Call::Disconnect(conn)));
    assert_eq!(rig.exit, None);

    // The timed-out batch never reports sent later: the token was released at
    // cancellation, so the late completion is dropped by the ingress itself.
    assert_eq!(
        rig.inject_status(BackendEvent::WriteDone { conn, send }),
        EmitStatus::StaleToken,
        "the cancelled completion was already released"
    );
    assert!(matches!(
        rig.receiver.pop_event(Some(ZERO)),
        BackendPop::Timeout
    ));
    assert!(rig.events().is_empty(), "no late sent after cancellation");
    assert_eq!(rig.exit, None);
}

// ---- section F: cancellation, fences and capacity changes ------------------------------------------

#[test]
fn cancel_partial_send_fences_reconnect() {
    let mut rig = Rig::ready();
    let (handle, conn) = rig.connect_hint("c1", "phone-1", 517);
    rig.req(
        "send",
        "first",
        json!({"connection_handle": handle, "record_base64": b64(&record_of(8, 1))}),
    );
    rig.req(
        "send",
        "second",
        json!({"connection_handle": handle, "record_base64": b64(&record_of(8, 2))}),
    );
    let (write_conn, _, _) = rig.last_write();
    let _ = write_conn;

    // Requested disconnect while a send is in flight and another is queued.
    rig.req("disconnect", "bye", json!({"connection_handle": handle}));
    let events = rig.events();
    // Affected request errors precede the correlated disconnected event.
    assert_eq!(
        types(&events),
        vec!["error", "error", "disconnected"],
        "{events:?}"
    );
    assert_eq!(events[0]["request_id"], "first");
    assert_eq!(events[0]["code"], "send_failed");
    assert_eq!(events[0]["recoverability"], "fatal");
    assert_eq!(events[1]["request_id"], "second");
    assert_eq!(events[1]["code"], "send_failed");
    assert_eq!(events[2]["request_id"], "bye");
    assert_eq!(events[2]["connection_handle"], handle);
    assert!(rig.calls().contains(&Call::Disconnect(conn)));
    assert_eq!(rig.exit, None);

    // Late completion of the cancelled in-flight batch cannot report sent.
    let write = rig.last_write();
    assert_eq!(
        rig.inject_status(BackendEvent::WriteDone {
            conn: write.0,
            send: write.1,
        }),
        EmitStatus::StaleToken,
        "the cancelled send token was released with its queued completions"
    );
    assert!(rig.events().is_empty(), "no late sent");
    assert_eq!(rig.exit, None);

    // Same target cannot reconnect until the observed release.
    rig.req("connect", "again", json!({"platform_hint": "phone-1"}));
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(
        events[0]["code"], "busy",
        "the closing link still owns the target"
    );
    assert_eq!(rig.exit, None);

    rig.inject(BackendEvent::LinkReleased { conn });
    rig.req("connect", "again", json!({"platform_hint": "phone-1"}));
    assert_eq!(rig.exit, None, "reconnect allowed after LinkReleased");
}

#[test]
fn capacity_change_same_derived_budget_is_harmless() {
    let mut rig = Rig::ready();
    // MTU 515 and 517 both clamp to the 512-byte attribute budget.
    let (handle, _) = rig.connect_hint("c1", "phone-1", 515);
    rig.req(
        "send",
        "s1",
        json!({"connection_handle": handle, "record_base64": b64(&record_of(8, 1))}),
    );
    let (conn, send, _) = rig.last_write();
    let calls_before = rig.calls().len();

    rig.inject(BackendEvent::CapacityChanged {
        conn,
        capacity: LinkCapacity::AttMtu(517),
    });
    assert!(
        rig.events().is_empty(),
        "an equivalent clamped capacity changes nothing"
    );
    assert_eq!(rig.calls().len(), calls_before, "no teardown submission");
    assert_eq!(rig.exit, None);

    rig.inject(BackendEvent::WriteDone { conn, send });
    let events = rig.events();
    assert_eq!(events[0]["type"], "sent");
    assert_eq!(events[0]["request_id"], "s1");
    assert_eq!(rig.exit, None);
}

#[test]
fn capacity_change_new_budget_cancels_work_and_never_refragments() {
    let mut rig = Rig::ready();
    let (handle, _) = rig.connect_hint("c1", "phone-1", 185);
    for (name, seed) in [("s1", 1u8), ("s2", 2u8), ("s3", 3u8)] {
        rig.req(
            "send",
            name,
            json!({"connection_handle": handle, "record_base64": b64(&record_of(400, seed))}),
        );
    }
    let (conn, _send, frames) = rig.last_write();
    assert_eq!(
        frames.len(),
        3,
        "400 bytes at MTU 185: 3 frames (178+178+44)"
    );
    let calls_before = rig.calls().len();

    // A different derived budget invalidates the frozen one: cancel affected
    // work and close the connection; never refragment an in-flight record.
    rig.inject(BackendEvent::CapacityChanged {
        conn,
        capacity: LinkCapacity::AttMtu(517),
    });
    let events = rig.events();
    assert_eq!(
        types(&events),
        vec!["error", "error", "error", "disconnected"],
        "{events:?}"
    );
    for event in &events[..3] {
        assert_eq!(event["type"], "error");
        assert_eq!(event["code"], "send_failed");
        assert_eq!(event["recoverability"], "fatal");
        assert_eq!(event["stage"], "capacity_changed");
    }
    assert_eq!(events[0]["request_id"], "s1");
    assert!(
        events[3].get("request_id").is_none(),
        "unsolicited disconnected"
    );
    assert_eq!(events[3]["connection_handle"], handle);
    assert!(
        rig.calls().contains(&Call::Disconnect(conn)),
        "the link is closed"
    );
    assert_eq!(
        rig.write_calls().len(),
        1,
        "exactly the original fragment batch: no re-fragmentation, no new writes"
    );
    assert_eq!(
        rig.calls().len(),
        calls_before + 1,
        "only the teardown submission added"
    );
    assert_eq!(rig.exit, None);
}

// ---- section G: inbound records and two-phone isolation ---------------------------------------------

#[test]
fn inbound_record_reassembles_to_exactly_one_message() {
    let mut rig = Rig::ready();
    let (handle, conn) = rig.connect_hint("c1", "phone-1", 517);
    let record = record_of(1000, 7);
    rig.inbound(conn, &record, 517, 7);
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    assert_eq!(events[0]["type"], "message");
    assert!(events[0].get("request_id").is_none(), "unsolicited");
    assert_eq!(events[0]["connection_handle"], handle);
    assert_eq!(events[0]["record_base64"], b64(&record));
    assert_eq!(rig.exit, None);
}

#[test]
fn partial_record_expires_after_5s_without_state_leak() {
    let mut rig = Rig::ready();
    let (handle, conn) = rig.connect_hint("c1", "phone-1", 517);
    let _ = handle;
    let record = record_of(1000, 7);
    let frames = veles_ble_host::fragment::fragment(&record, support::budget(517), 7).unwrap();
    rig.inject(BackendEvent::Fragment {
        conn,
        bytes: frames[0].clone(),
    });
    assert!(
        rig.events().is_empty(),
        "an incomplete transaction delivers nothing"
    );
    rig.advance(Duration::from_secs(5));
    assert!(rig.events().is_empty(), "the expired partial stays silent");
    // The budget is released: a fresh transaction still completes.
    rig.inbound(conn, &record, 517, 8);
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    assert_eq!(events[0]["type"], "message");
    assert_eq!(events[0]["record_base64"], b64(&record));
}

#[test]
fn per_link_data_overflow_closes_only_that_link_and_survivors_continue() {
    let mut rig = Rig::ready();
    let (handle_a, conn_a) = rig.connect_hint("ca", "phone-a", 517);
    let (handle_b, conn_b) = rig.connect_hint("cb", "phone-b", 517);

    // Flood link A's per-link data queue: 48 admitted stream fragments (stray
    // payloads the reassembler ignores), the 49th rejected with exactly one
    // retained failure marker.
    let stray = |chunk: u8| Payload::new(vec![1, 5 + chunk, 0, 0, chunk]);
    for chunk in 0..48u8 {
        assert_eq!(
            rig.inject_status(BackendEvent::Fragment {
                conn: conn_a,
                bytes: stray(chunk),
            }),
            EmitStatus::Accepted
        );
    }
    assert_eq!(
        rig.inject_status(BackendEvent::Fragment {
            conn: conn_a,
            bytes: stray(90),
        }),
        EmitStatus::DataOverflow,
        "the 49th of the link rejects with exactly one retained failure marker"
    );

    // The retained A data delivers first, then the marker closes ONLY link A
    // (unsolicited disconnected for the disclosed handle); B is untouched.
    rig.pump_ingress();
    let events = rig.events();
    assert_eq!(types(&events), vec!["disconnected"], "{events:?}");
    assert!(events[0].get("request_id").is_none(), "unsolicited");
    assert_eq!(events[0]["connection_handle"], handle_a);

    // Link B keeps working: its own send terminal and inbound records come.
    rig.req(
        "send",
        "rb",
        json!({"connection_handle": handle_b, "record_base64": b64(&record_of(8, 1))}),
    );
    let (conn, send, _) = rig.last_write();
    rig.inject(BackendEvent::WriteDone { conn, send });
    rig.inbound(conn_b, b"tiny", 517, 9);
    let events = rig.events();
    assert_eq!(types(&events), vec!["sent", "message"], "{events:?}");
    assert_eq!(events[0]["request_id"], "rb");
    assert_eq!(events[1]["record_base64"], b64(b"tiny"));
    assert_eq!(rig.exit, None);
}

#[test]
fn survivor_send_message_progress_continues_while_peer_stalls() {
    let mut rig = Rig::ready();
    let (handle_a, conn_a) = rig.connect_hint("ca", "phone-a", 517);
    let (handle_b, conn_b) = rig.connect_hint("cb", "phone-b", 517);

    rig.req(
        "send",
        "ra",
        json!({"connection_handle": handle_a, "record_base64": b64(&record_of(8, 1))}),
    );
    rig.req(
        "send",
        "rb1",
        json!({"connection_handle": handle_b, "record_base64": b64(&record_of(8, 2))}),
    );
    rig.req(
        "send",
        "rb2",
        json!({"connection_handle": handle_b, "record_base64": b64(&record_of(8, 3))}),
    );
    let mut writes = rig.write_calls();
    assert_eq!(
        writes.len(),
        2,
        "A and B each started their own in-flight batch"
    );
    // A stalls (its send never completes). B's queue keeps streaming.
    rig.inject(BackendEvent::WriteDone {
        conn: writes[1].0,
        send: writes[1].1,
    });
    writes = rig.write_calls();
    assert_eq!(
        writes.len(),
        3,
        "B's second record was started while A still stalls"
    );
    assert_eq!(writes[0].0, conn_a, "A's untouched batch first");
    assert_eq!(writes[1].0, conn_b, "B's second batch after its first");
    assert_eq!(rig.exit, None);
}

// ---- section H: shutdown ----------------------------------------------------------------------------

#[test]
fn shutdown_orders_errors_lifecycle_then_final_completion_after_shutdown_complete() {
    let mut rig = Rig::ready();
    let (handle_a, conn_a) = rig.connect_hint("ca", "phone-a", 517);
    let (handle_b, conn_b) = rig.connect_hint("cb", "phone-b", 517);
    let (scan_handle, scan) = rig.open_scan("sc");
    rig.req(
        "send",
        "sa",
        json!({"connection_handle": handle_a, "record_base64": b64(&record_of(8, 1))}),
    );
    rig.req(
        "send",
        "sb",
        json!({"connection_handle": handle_b, "record_base64": b64(&record_of(8, 2))}),
    );
    assert!(rig.events().is_empty());

    rig.req("shutdown", "bye", json!({}));
    let events = rig.events();
    // Correlated errors (both sends, admission order), then unsolicited
    // lifecycle events, and no shutdown_complete before the backend reports it.
    assert_eq!(types(&events[..2]), vec!["error", "error"], "{events:?}");
    assert_eq!(events[0]["request_id"], "sa");
    assert_eq!(events[0]["code"], "internal");
    assert_eq!(events[0]["recoverability"], "fatal");
    assert_eq!(events[0]["stage"], "shutdown");
    assert_eq!(events[0]["connection_handle"], handle_a);
    assert_eq!(events[1]["request_id"], "sb");
    assert_eq!(events[2]["type"], "scan_state");
    assert_eq!(events[2]["status"], "stopped");
    assert_eq!(events[2]["scan_handle"], scan_handle);
    assert!(events[2].get("request_id").is_none());
    assert_eq!(events[3]["type"], "disconnected");
    assert_eq!(events[3]["connection_handle"], handle_a);
    assert!(events[3].get("request_id").is_none());
    assert_eq!(events[4]["type"], "disconnected");
    assert_eq!(events[4]["connection_handle"], handle_b);
    assert_eq!(events.len(), 5, "no shutdown_complete yet: {events:?}");
    assert_eq!(
        rig.exit, None,
        "the core waits for the observed backend release"
    );
    assert!(rig.calls().contains(&Call::StopScan(scan)));
    assert!(rig.calls().contains(&Call::Disconnect(conn_a)));
    assert!(rig.calls().contains(&Call::Disconnect(conn_b)));
    assert!(
        rig.calls().contains(&Call::Shutdown),
        "the backend was asked to release"
    );

    // Only the backend's ShutdownComplete produces the final completion.
    rig.inject(BackendEvent::ShutdownComplete);
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    assert_eq!(events[0]["type"], "shutdown_complete");
    assert_eq!(events[0]["request_id"], "bye");
    assert_eq!(events[0]["api_version"], 1);
    assert_eq!(rig.exit, Some(EXIT_OK));
}

#[test]
fn shutdown_does_not_repeat_disconnect_for_a_requested_closing_link() {
    // A requested disconnect has already emitted the link's one `disconnected`
    // while the connection waits in its closing state for `LinkReleased`.
    // Shutdown before that release must not emit the same handle again.
    let mut rig = Rig::ready();
    let (handle, conn) = rig.connect_hint("c1", "phone-1", 517);
    let _ = rig.events();

    rig.req("disconnect", "bye", json!({"connection_handle": handle}));
    let events = rig.events();
    assert_eq!(types(&events), vec!["disconnected"], "{events:?}");
    assert_eq!(events[0]["request_id"], "bye");
    assert_eq!(events[0]["connection_handle"], handle);

    rig.req("shutdown", "sd", json!({}));
    assert!(
        rig.events().is_empty(),
        "a closing link is not disclosed again"
    );
    assert!(rig.calls().contains(&Call::Disconnect(conn)));
    assert!(rig.calls().contains(&Call::Shutdown));

    rig.inject(BackendEvent::ShutdownComplete);
    let events = rig.events();
    assert_eq!(
        types(&events),
        vec!["shutdown_complete"],
        "exactly one completion, no duplicate disconnect: {events:?}"
    );
    assert_eq!(events[0]["request_id"], "sd");
    assert_eq!(rig.exit, Some(EXIT_OK));
}

#[test]
fn shutdown_does_not_repeat_disconnect_for_a_lost_closing_link() {
    // Link loss follows the same single teardown: its unsolicited
    // `disconnected` is the only one, even if shutdown arrives before the
    // backend reports `LinkReleased`.
    let mut rig = Rig::ready();
    let (handle, conn) = rig.connect_hint("c1", "phone-1", 517);
    let _ = rig.events();

    rig.inject(BackendEvent::LinkLost { conn });
    let events = rig.events();
    assert_eq!(types(&events), vec!["disconnected"], "{events:?}");
    assert!(events[0].get("request_id").is_none());
    assert_eq!(events[0]["connection_handle"], handle);

    rig.req("shutdown", "sd", json!({}));
    assert!(
        rig.events().is_empty(),
        "a closing link is not disclosed again"
    );
    assert!(rig.calls().contains(&Call::Disconnect(conn)));
    assert!(rig.calls().contains(&Call::Shutdown));

    rig.inject(BackendEvent::ShutdownComplete);
    let events = rig.events();
    assert_eq!(
        types(&events),
        vec!["shutdown_complete"],
        "exactly one completion, no duplicate disconnect: {events:?}"
    );
    assert_eq!(events[0]["request_id"], "sd");
    assert_eq!(rig.exit, Some(EXIT_OK));
}

#[test]
fn stray_shutdown_complete_fails_the_host_closed_without_stranding_requests() {
    // A stale/early backend terminal with no shutdown handshake armed: the
    // ingress has already closed its admission (everything further emitted is
    // rejected), so the host must fail closed NOW — full cleanup — and never
    // strand an already-accepted request behind the closed ingress.
    let mut rig = Rig::ready();
    let (scan_handle, scan) = rig.open_scan("s1");
    let (handle, conn) = rig.connect_hint("ca", "phone-a", 517);
    rig.req(
        "send",
        "ra",
        json!({"connection_handle": handle, "record_base64": b64(&record_of(8, 1))}),
    );
    assert_eq!(rig.write_calls().len(), 1, "an accepted send in flight");
    let pending_before = rig.out.len();

    rig.inject(BackendEvent::ShutdownComplete);

    assert_eq!(
        rig.exit,
        Some(1),
        "the unsolicited process-wide terminal fails the host closed"
    );
    // Full cleanup of every accepted request/scan/link: the release
    // submissions of the teardown, and no fabricated further output (the
    // connector resolves every request locally on the port closure).
    assert!(
        rig.calls().contains(&Call::StopScan(scan)),
        "{:?}",
        rig.calls()
    );
    assert!(rig.calls().contains(&Call::Disconnect(conn)));
    assert!(rig.calls().contains(&Call::Shutdown));
    assert_eq!(rig.out.len(), pending_before, "nothing further was emitted");
    let _ = scan_handle;

    // The ingress admission is closed for good: nothing further can complete.
    assert_eq!(
        rig.inject_status(BackendEvent::ScanStarted { scan }),
        EmitStatus::Shutdown,
        "the ingress rejects everything after the process-wide terminal"
    );
    assert_eq!(rig.out.len(), pending_before);
}

#[test]
fn staged_terminal_before_the_shutdown_handshake_fails_closed() {
    // A process-wide terminal already STAGED (not yet drained) must never be
    // consumed as the completion of a handshake that starts afterwards: the
    // core arms the handshake atomically under the ingress state lock, so a
    // terminal observed before the arm permanently marks the handshake
    // unexpected. The drain then fails the host closed — never
    // `shutdown_complete`.
    let mut rig = Rig::ready();
    let (handle, conn) = rig.connect_hint("c1", "phone-1", 517);
    rig.req(
        "send",
        "ra",
        json!({"connection_handle": handle, "record_base64": b64(&record_of(8, 1))}),
    );
    assert_eq!(rig.write_calls().len(), 1, "an accepted send in flight");

    // The stray terminal is staged (admission closes at the ingress) but NOT
    // drained; the shutdown request is processed BEFORE the drain.
    assert_eq!(
        rig.inject_status(BackendEvent::ShutdownComplete),
        EmitStatus::Accepted
    );
    rig.req("shutdown", "bye", json!({}));

    // The handshake was processed (correlated shutdown error for the send,
    // then the lifecycle disconnect), but must never complete:
    let events = rig.events();
    assert_eq!(types(&events), vec!["error", "disconnected"], "{events:?}");
    assert_eq!(events[0]["request_id"], "ra");
    assert_eq!(events[0]["code"], "internal");
    assert_eq!(events[0]["stage"], "shutdown");

    // Now drain the staged terminal: the host fails closed with no completion.
    rig.pump_ingress();
    assert_eq!(rig.exit, Some(EXIT_HOST_FAILURE));
    assert_eq!(
        rig.events().len(),
        0,
        "no shutdown_complete for the staged terminal"
    );
    assert!(
        rig.calls().contains(&Call::Disconnect(conn)),
        "{:?}",
        rig.calls()
    );
    assert!(rig.calls().contains(&Call::Shutdown));
}

#[test]
fn synchronous_release_inside_backend_shutdown_completes_the_handshake() {
    // The legitimate synchronous case: an adapter with no outstanding OS work
    // reports the process-wide release FROM INSIDE its `shutdown()`
    // submission. The core arms the handshake before the submission, so this
    // terminal is attributed to the handshake and completes it normally.
    let mut rig = support::Rig::with_synchronous_release();
    rig.hello();
    let (_handle, _conn) = rig.connect_hint("c1", "phone-1", 517);
    let _ = rig.events();

    rig.req("shutdown", "bye", json!({}));
    assert_eq!(rig.exit, None, "nothing was drained yet");

    // The terminal sat staged (emitted synchronously inside the submission);
    // draining it completes the handshake after the open link's own
    // unsolicited lifecycle disconnect.
    rig.pump_ingress();
    let events = rig.events();
    assert_eq!(
        types(&events),
        vec!["disconnected", "shutdown_complete"],
        "{events:?}"
    );
    assert!(events[0].get("request_id").is_none());
    assert_eq!(events[1]["request_id"], "bye");
    assert_eq!(rig.exit, Some(EXIT_OK));
    assert!(rig.calls().contains(&Call::Shutdown));
}

#[test]
fn no_shutdown_complete_without_an_observed_release() {
    // An armed handshake whose release was never observed stays open: no
    // completion is fabricated and the handshake is still live (the Task-5
    // runtime owns the bounded close deadline for this state).
    let mut rig = Rig::ready();
    rig.req("shutdown", "bye", json!({}));
    rig.tick();
    rig.pump_ingress();
    assert_eq!(rig.exit, None, "the handshake waits");
    assert!(rig.events().is_empty(), "no fabricated completion");
    assert!(
        rig.calls().contains(&Call::Shutdown),
        "the release was submitted"
    );

    // Only the observed release completes it.
    rig.inject(BackendEvent::ShutdownComplete);
    let events = rig.events();
    assert_eq!(events.len(), 1, "{events:?}");
    assert_eq!(events[0]["type"], "shutdown_complete");
    assert_eq!(events[0]["request_id"], "bye");
    assert_eq!(rig.exit, Some(EXIT_OK));
}

#[test]
fn eof_releases_everything_silently_without_shutdown_acknowledgement() {
    let mut rig = Rig::ready();
    let (scan_handle, scan) = rig.open_scan("s1");
    assert_eq!(rig.out.len(), 0);
    input(&mut rig, Input::StdinEof);
    assert_eq!(rig.exit, Some(EXIT_OK));
    assert!(rig.events().is_empty(), "no shutdown_complete on EOF");
    assert!(rig.calls().contains(&Call::StopScan(scan)));
    assert!(rig.calls().contains(&Call::Shutdown));
    let _ = scan_handle;
    // Further inputs are refused quietly (the exit code is stable).
    input(&mut rig, Input::Tick);
    assert_eq!(rig.exit, Some(EXIT_OK));
    assert_eq!(rig.write_calls().len(), 0);
}

#[test]
fn eof_release_is_pending_until_the_terminal_observed_even_after_exit() {
    // EOF releases everything fire-and-forget (backend.shutdown submitted, no
    // acknowledgement awaited) and exits 0. The backend's process-wide
    // terminal may still arrive afterwards: observing it is pure bookkeeping —
    // nothing new is emitted and the original exit code stays — so the Task-5
    // runtime can watch the release realign without the core saying anything.
    let mut rig = Rig::ready();
    let (scan_handle, scan) = rig.open_scan("s1");
    let pending_out = rig.out.len();

    input(&mut rig, Input::StdinEof);

    assert_eq!(rig.exit, Some(EXIT_OK));
    assert!(rig.calls().contains(&Call::StopScan(scan)));
    assert!(
        rig.calls().contains(&Call::Shutdown),
        "EOF submitted the backend release: {:?}",
        rig.calls()
    );
    assert_eq!(rig.out.len(), pending_out, "EOF emits nothing");
    assert!(
        !rig.core.shutdown_complete_received(),
        "the release is still pending: the terminal has not been observed"
    );

    // The terminal observed AFTER the exit: bookkeeping only.
    rig.inject(BackendEvent::ShutdownComplete);

    assert!(
        rig.core.shutdown_complete_received(),
        "the backend terminal is now observed"
    );
    assert_eq!(
        rig.exit,
        Some(EXIT_OK),
        "the original exit code is preserved"
    );
    assert_eq!(rig.out.len(), pending_out, "no new JSON after the exit");
    assert!(rig.events().is_empty(), "{:?}", rig.events());
    let _ = scan_handle;
}

#[test]
fn stale_terminal_staged_before_eof_never_confirms_eof_cleanup() {
    // A stray terminal STAGED before the EOF release carries a pre-claim
    // generation: drained post-exit it confirms NOTHING — the post-exit
    // observation joins only the terminal emitted at the generation claimed
    // for the genuine release (the review's false-cleanup regression).
    let mut rig = Rig::ready();
    let (scan_handle, _scan) = rig.open_scan("s1");
    let pending_out = rig.out.len();

    // The stray is staged (admission closes at the ingress) but NOT drained.
    assert_eq!(
        rig.inject_status(BackendEvent::ShutdownComplete),
        EmitStatus::Accepted
    );

    // EOF releases fire-and-forget (the claim happens immediately before the
    // submission) and exits 0.
    input(&mut rig, Input::StdinEof);
    assert_eq!(rig.exit, Some(EXIT_OK));
    assert!(
        !rig.core.shutdown_complete_received(),
        "nothing was observed yet"
    );

    // The stale staged terminal delivers first: not attributed to the
    // (post-claim) release, it can confirm nothing.
    // It is popped and handed to the (exited) core exactly as the runtime's bounded exit
    // does; merely popping it would not exercise the core's attribution at all.
    match rig.receiver.pop_event(Some(ZERO)) {
        BackendPop::Event(event @ BackendEvent::ShutdownComplete) => {
            input(&mut rig, Input::Backend(event));
        }
        other => panic!("the stray was the first deliverable terminal: {other:?}"),
    }
    assert!(
        !rig.core.shutdown_complete_received(),
        "a stale terminal never fabricates the cleanup confirmation"
    );

    // The genuine post-claim terminal (an async OS release reporting late):
    // emitted after the claim, so its generation equals the claimed one.
    assert_eq!(
        rig.inject_status(BackendEvent::ShutdownComplete),
        EmitStatus::Accepted
    );
    rig.pump_ingress();

    assert!(
        rig.core.shutdown_complete_received(),
        "the genuine release's terminal is the confirmation"
    );
    assert_eq!(rig.exit, Some(EXIT_OK), "the exit code is preserved");
    assert_eq!(rig.out.len(), pending_out, "EOF emits nothing further");
    assert!(rig.events().is_empty(), "{:?}", rig.events());
    let _ = scan_handle;
}

#[test]
fn sync_release_inside_eof_submission_confirms_exactly_one_generation() {
    // The synchronous genuine path: the release is claimed immediately
    // BEFORE `Backend::shutdown`, so the terminal the adapter reports from
    // inside the submission is stamped with the claimed generation and the
    // post-exit drain attributes it — cleanup is confirmed, the protocol
    // exit stands and no shutdown_complete JSON follows EOF.
    let mut rig = support::Rig::with_synchronous_release();
    let pending_out = rig.out.len();

    input(&mut rig, Input::StdinEof);
    assert_eq!(rig.exit, Some(EXIT_OK));

    // The submission's own synchronous terminal is already staged.
    rig.pump_ingress();
    assert!(
        rig.core.shutdown_complete_received(),
        "the synchronous genuine release confirms the cleanup"
    );
    assert_eq!(rig.exit, Some(EXIT_OK), "the exit code is preserved");
    assert_eq!(rig.out.len(), pending_out, "EOF emits nothing");
    assert!(rig.events().is_empty(), "{:?}", rig.events());
}

#[test]
fn eof_superseding_an_armed_handshake_still_confirms_the_genuine_release() {
    // An armed handshake whose release is the claimed one; stdin EOF
    // supersedes it (exit 0, second fire-and-forget submission at the same
    // claimed generation). The genuine terminal drained afterwards is still
    // attributed —.
    let mut rig = Rig::ready();
    let (handle, _conn) = rig.connect_hint("c1", "phone-1", 517);
    let _ = rig.events();

    rig.req("shutdown", "bye", json!({}));
    assert_eq!(rig.exit, None, "the handshake waits for the release");
    assert!(rig.calls().contains(&Call::Shutdown));

    // The handshake's lifecycle events (the open link's disconnect) precede
    // EOF; nothing new is emitted by the EOF path.
    assert!(
        !types(&rig.events()).contains(&"shutdown_complete".to_string()),
        "no shutdown_complete before EOF"
    );

    input(&mut rig, Input::StdinEof);
    assert_eq!(rig.exit, Some(EXIT_OK), "EOF finishes the core");
    assert!(
        !rig.core.shutdown_complete_received(),
        "nothing was observed yet"
    );
    assert!(rig.events().is_empty(), "EOF emits nothing further");

    // The genuine post-claim terminal confirms; nothing new is emitted.
    rig.inject(BackendEvent::ShutdownComplete);
    assert!(
        rig.core.shutdown_complete_received(),
        "the genuine release confirms post-exit"
    );
    assert_eq!(rig.exit, Some(EXIT_OK));
    assert!(rig.events().is_empty(), "no shutdown_complete after EOF");
    let _ = handle;
}

// ---- section I: exit codes and process-level failures -----------------------------------------------

#[test]
fn framing_input_exits_2_io_and_overflow_exit_1_eof_and_stdout_exit_0() {
    let mut rig = Rig::ready();
    input(&mut rig, Input::StdinFraming);
    assert_eq!(rig.exit, Some(EXIT_REJECTED));
    assert!(rig.calls().contains(&Call::Shutdown));

    let mut rig = Rig::ready();
    input(&mut rig, Input::StdinIoError);
    assert_eq!(rig.exit, Some(EXIT_HOST_FAILURE));
    assert!(rig.calls().contains(&Call::Shutdown));

    let mut rig = Rig::ready();
    input(&mut rig, Input::StdoutClosed);
    assert_eq!(rig.exit, Some(EXIT_OK));
    assert!(rig.calls().contains(&Call::Shutdown));

    let mut rig = Rig::ready();
    input(&mut rig, Input::BackendOverflow);
    assert_eq!(rig.exit, Some(EXIT_HOST_FAILURE));
    assert!(rig.calls().contains(&Call::Shutdown));
}

#[test]
fn an_ingress_control_overflow_latch_fails_the_core_closed() {
    let mut rig = Rig::ready();
    // Saturate reserved per-link control slots (32) of one open link through
    // the real ingress producer with a harmless (same-budget) capacity change,
    // then trip the overflow latch.
    let (handle, token) = rig.connect_hint("c1", "p", 517);
    let _ = handle;
    // Same-budget capacity notices are harmless; stage them queued (not
    // pumped) so the link's bounded control slots saturate.
    for _ in 0..veles_ble_host::ingress::CONTROL_EVENTS_PER_LINK_MAX {
        assert_eq!(
            rig.inject_status(BackendEvent::CapacityChanged {
                conn: token,
                capacity: LinkCapacity::AttMtu(517),
            }),
            EmitStatus::Accepted,
            "same-budget capacity notices are routed"
        );
    }
    assert_eq!(
        rig.inject_status(BackendEvent::CapacityChanged {
            conn: token,
            capacity: LinkCapacity::AttMtu(517),
        }),
        EmitStatus::ControlOverflow,
        "the 33rd control latches the process-level overflow"
    );
    assert_eq!(rig.exit, None, "no latch observation yet");
    assert_eq!(rig.out.len(), 0);

    // Without even feeding Input::BackendOverflow, the next core input fails
    // closed on the sticky latch (the core polls it at every input).
    input(&mut rig, Input::Tick);
    assert_eq!(
        rig.exit,
        Some(EXIT_HOST_FAILURE),
        "the core observed the sticky latch"
    );
    assert!(rig.calls().contains(&Call::Shutdown));
}

#[test]
fn output_overflow_exits_4_and_releases_everything() {
    let mut rig = support::rig_with_queue(1);
    rig.frame(json!({
        "envelope_version":1,"type":"hello","request_id":"hello","api_versions":[1]
    }));
    assert_eq!(rig.out.len(), 1, "ready fills the one-slot queue");
    rig.req("start_scan", "s1", json!({"service_uuid": SCAN_UUID}));
    let token = rig.start_scan_token();
    rig.inject(BackendEvent::ScanStarted { scan: token });
    assert_eq!(
        rig.exit,
        Some(EXIT_OUTPUT_OVERFLOW),
        "the scan_state emission overflowed the bounded output queue"
    );
    assert!(
        rig.calls().contains(&Call::StopScan(token)),
        "release happened"
    );
    assert!(rig.calls().contains(&Call::Shutdown));
    input(&mut rig, Input::Tick);
    assert_eq!(rig.exit, Some(EXIT_OUTPUT_OVERFLOW), "the exit is stable");
}

// ---- section J: normalized-target fence ------------------------------------------------------------

#[test]
fn aliased_targets_fence_as_one_device_until_release() {
    // The fake's deliberate alias rule folds an observation variant (`X#s`)
    // into the identity of `X`, the way the real seam folds the WinRT
    // address-type digit: every variant below names ONE OS device, and only
    // the normalized identity may ever fence a connect.
    let mut rig = Rig::ready();
    rig.req("connect", "c1", json!({"platform_hint": "phone-1"}));
    let token = rig.connect_token();
    assert!(
        rig.calls()
            .contains(&Call::Connect(token, support_hint("phone-1"))),
        "{:?}",
        rig.calls()
    );
    assert_eq!(rig.calls().len(), 1);

    // Pending: the alias is refused as the SAME device — no second connect
    // submission, no fence bypass.
    rig.req(
        "connect",
        "c2",
        json!({"platform_hint": alias("phone-1", "kind2")}),
    );
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "c2");
    assert_eq!(events[0]["code"], "busy");
    assert_eq!(events[0]["recoverability"], "retryable");
    assert_eq!(events[0]["stage"], "already_connected");
    assert_eq!(
        rig.calls().len(),
        1,
        "no Connect for the aliased busy request"
    );

    // Open: the alias stays fenced while the link is open.
    let connected = rig.connected(token, 517);
    assert_eq!(types(&connected), vec!["connected"], "{connected:?}");
    let conn_handle = connected[0]["connection_handle"]
        .as_str()
        .unwrap()
        .to_string();
    rig.req(
        "connect",
        "c3",
        json!({"platform_hint": alias("phone-1", "observed")}),
    );
    let events = rig.events();
    assert_eq!(events[0]["code"], "busy", "{events:?}");
    assert_eq!(rig.calls().len(), 1);

    // Closing: the teardown changes the state, not the identity fence.
    rig.req(
        "disconnect",
        "bye",
        json!({"connection_handle": conn_handle}),
    );
    let events = rig.events();
    assert_eq!(types(&events), vec!["disconnected"], "{events:?}");
    assert_eq!(events[0]["request_id"], "bye");
    assert_eq!(rig.calls().len(), 2);
    rig.req(
        "connect",
        "c4",
        json!({"platform_hint": alias("phone-1", "observed")}),
    );
    let events = rig.events();
    assert_eq!(
        events[0]["code"], "busy",
        "the closing link fences its identity: {events:?}"
    );
    assert_eq!(rig.calls().len(), 2);

    // Only the observed OS release lifts the fence; the alias connects with
    // its hint VERBATIM (hints stay the opaque routing input).
    rig.inject(BackendEvent::LinkReleased { conn: token });
    rig.req(
        "connect",
        "c5",
        json!({"platform_hint": alias("phone-1", "observed")}),
    );
    assert_eq!(rig.exit, None, "the released identity accepts a reconnect");
    let token = rig.connect_token();
    assert!(
        rig.calls().contains(&Call::Connect(
            token,
            support_hint(&alias("phone-1", "observed"))
        )),
        "the alias routes verbatim: {:?}",
        rig.calls()
    );
    assert_eq!(rig.calls().len(), 3);

    // The fold works both ways: the plain hint now fences against the
    // alias-owned identity too (one identity, at most one connection).
    rig.req("connect", "c6", json!({"platform_hint": "phone-1"}));
    let events = rig.events();
    assert_eq!(events[0]["code"], "busy", "{events:?}");
    assert_eq!(rig.calls().len(), 3);

    // A cached alias advertisement resolves through its own fresh device
    // handle and fences on the same identity as well.
    let (_, scan) = rig.open_scan("s1");
    let found = rig.advertise(scan, &alias("phone-1", "cached"), "Aliased");
    assert_eq!(
        found["platform_hint"],
        alias("phone-1", "cached"),
        "{found:?}"
    );
    rig.req(
        "connect",
        "c7",
        json!({"device_handle": found["device_handle"]}),
    );
    let events = rig.events();
    assert_eq!(events[0]["code"], "busy", "{events:?}");
    assert_eq!(
        rig.calls().len(),
        4,
        "no further backend submission (the aliased busy)"
    );
}

#[test]
fn unresolvable_target_is_refused_before_any_backend_side_effect() {
    // The fake seam contract the fence tests rely on, pinned directly:
    // deliberate aliases fold, unresolvable hints refuse, resolvable hints
    // keep their full text as identity.
    let mut rig = Rig::ready();
    assert_eq!(
        rig.core.backend().normalize_target("phone-1"),
        Some(NormalizedTarget::new("phone-1"))
    );
    assert_eq!(
        rig.core
            .backend()
            .normalize_target(&alias("phone-1", "kind2")),
        rig.core.backend().normalize_target("phone-1"),
        "the observation suffix is not identity (the deliberate alias case)"
    );
    assert!(
        rig.core
            .backend()
            .normalize_target("#suffix-only")
            .is_none(),
        "no identity text resolves to no OS device"
    );
    assert_eq!(
        rig.core.backend().normalize_target("sim:phone-1"),
        Some(NormalizedTarget::new("sim:phone-1"))
    );

    // A hint the backend cannot resolve (passing the protocol's own hint
    // validation: 1..=256 bytes, no control characters) is refused with a
    // correlated request-level error and zero backend submissions — no fence
    // slot, no connection state, no process failure.
    rig.req("connect", "m1", json!({"platform_hint": "#suffix-only"}));
    let events = rig.events();
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "m1");
    assert_eq!(events[0]["code"], "invalid_request");
    assert_eq!(events[0]["recoverability"], "fatal");
    assert_eq!(events[0]["stage"], "unsupported_target");
    assert!(
        events[0].get("connection_handle").is_none() && events[0].get("scan_handle").is_none(),
        "{events:?}"
    );
    assert!(
        rig.calls().is_empty(),
        "no backend side effect: {:?}",
        rig.calls()
    );
    assert_eq!(rig.exit, None, "the request was refused, not the process");

    // The core keeps serving: a resolvable target admits normally.
    rig.req("connect", "ok", json!({"platform_hint": "phone-1"}));
    let token = rig.connect_token();
    let connected = rig.connected(token, 517);
    assert_eq!(types(&connected), vec!["connected"], "{connected:?}");

    // An advertised hint the backend cannot resolve is disclosed and cached
    // (the advertisement path is unchanged), and its connect is refused too.
    let (_, scan) = rig.open_scan("s1");
    let found = rig.advertise(scan, "#not-a-device", "Broken");
    rig.req(
        "connect",
        "m2",
        json!({"device_handle": found["device_handle"]}),
    );
    let events = rig.events();
    assert_eq!(events[0]["request_id"], "m2");
    assert_eq!(events[0]["code"], "invalid_request", "{events:?}");
    assert_eq!(events[0]["stage"], "unsupported_target");
    assert_eq!(
        rig.calls().len(),
        2,
        "StartScan + the accepted connect only"
    );

    // The refused request left no state behind: a fresh connect of the
    // resolvable target works after its link's release is observed.
    rig.inject(BackendEvent::LinkReleased { conn: token });
    rig.req("connect", "ok2", json!({"platform_hint": "phone-1"}));
    let _ = rig.connect_token();
    assert_eq!(rig.exit, None);
}
