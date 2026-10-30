//! Host core: negotiation, request-id correlation, scans, connect/send and independent handles.

mod common;
mod rig;

use std::collections::HashSet;
use std::time::Duration;

use rig::{Call, Rig, failure, is_handle, types};
use serde_json::json;
use veles_otp23_feasibility::backend::{BackendEvent, Target};
use veles_otp23_feasibility::core::{EXIT_INCOMPATIBLE, EXIT_REJECTED};
use veles_otp23_feasibility::fragment::fragment;

const HINT_A: &str = "winrt:a00000000001:1";
const HINT_B: &str = "winrt:b00000000002:1";

// ---- negotiation ------------------------------------------------------------------------------

#[test]
fn hello_returns_ready_and_performs_no_ble_work() {
    let mut rig = Rig::new();
    rig.send(json!({"envelope_version":1,"type":"hello","request_id":"h1","api_versions":[1,2]}));
    let events = rig.events();
    assert_eq!(
        events,
        vec![json!({
            "envelope_version":1,"api_version":1,"type":"ready","request_id":"h1",
            "bridge_version":"otp23-test/0","platform":"windows","capabilities":["veles_record_v1"]
        })]
    );
    assert!(
        rig.calls().is_empty(),
        "no BLE operation precedes negotiation"
    );
    assert_eq!(rig.exit, None);
}

#[test]
fn no_overlap_is_a_fatal_correlated_error_then_exit_3() {
    let mut rig = Rig::new();
    rig.send(json!({"envelope_version":1,"type":"hello","request_id":"h1","api_versions":[2,3]}));
    let events = rig.events();
    assert_eq!(
        events,
        vec![json!({
            "envelope_version":1,"type":"error","request_id":"h1","code":"incompatible_api",
            "recoverability":"fatal","stage":"negotiation"
        })]
    );
    assert_eq!(rig.exit, Some(EXIT_INCOMPATIBLE));
    assert!(
        !rig.calls()
            .iter()
            .any(|c| matches!(c, Call::StartScan(..) | Call::Connect(..)))
    );
}

#[test]
fn pre_ready_non_hello_is_answered_once_then_exit_2() {
    let mut rig = Rig::new();
    rig.send(json!({"envelope_version":1,"type":"start_scan","request_id":"s1","service_uuid":rig::SCAN_UUID}));
    let events = rig.events();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0]["code"], "invalid_request");
    assert_eq!(events[0]["recoverability"], "fatal");
    assert_eq!(events[0]["stage"], "pre_ready");
    assert_eq!(events[0]["request_id"], "s1");
    assert!(events[0].get("api_version").is_none());
    assert_eq!(rig.exit, Some(EXIT_REJECTED));
    assert!(
        !rig.calls().iter().any(|c| matches!(c, Call::StartScan(..))),
        "no BLE work"
    );
}

#[test]
fn uncorrelatable_pre_ready_input_is_silent_exit_2() {
    for bytes in [
        &b"garbage"[..],
        br#"{"envelope_version":2,"type":"hello","request_id":"h"}"#,
        b"{}",
    ] {
        let mut rig = Rig::new();
        rig.send_raw(bytes);
        assert!(
            rig.events().is_empty(),
            "{}",
            String::from_utf8_lossy(bytes)
        );
        assert_eq!(rig.exit, Some(EXIT_REJECTED));
    }
}

#[test]
fn repeated_hello_and_wrong_api_version_are_rejected_without_ending_the_session() {
    let mut rig = Rig::ready();
    rig.send(json!({"envelope_version":1,"api_version":1,"type":"hello","request_id":"h2","api_versions":[1]}));
    rig.send(json!({"envelope_version":1,"api_version":2,"type":"shutdown","request_id":"x"}));
    let events = rig.events();
    assert_eq!(events.len(), 2);
    assert!(
        events
            .iter()
            .all(|e| e["type"] == "error" && e["code"] == "invalid_request")
    );
    assert_eq!(events[0]["request_id"], "h2");
    assert_eq!(events[1]["request_id"], "x");
    assert_eq!(rig.exit, None);
}

#[test]
fn post_ready_uncorrelatable_input_exits_2_silently_after_full_release() {
    let mut rig = Rig::ready();
    let (_, scan) = rig.open_scan("s1");
    let (_, conn) = rig.connect_hint("c1", HINT_A, 185);
    rig.send_raw(b"{ not json");
    assert!(
        rig.events().is_empty(),
        "no JSON after an uncorrelatable input"
    );
    assert_eq!(rig.exit, Some(EXIT_REJECTED));
    let calls = rig.calls();
    assert!(calls.contains(&Call::StopScan(scan)));
    assert!(calls.contains(&Call::Disconnect(conn)));
    assert_eq!(calls.last(), Some(&Call::Shutdown));
}

// ---- request-id discipline ----------------------------------------------------------------------

#[test]
fn reusing_an_outstanding_request_id_fails_closed_without_a_response() {
    let mut rig = Rig::ready();
    rig.req("connect", "dup", json!({"platform_hint": HINT_A}));
    rig.req("connect", "dup", json!({"platform_hint": HINT_B}));
    assert!(rig.events().is_empty());
    assert_eq!(rig.exit, Some(EXIT_REJECTED));
    assert!(rig.calls().contains(&Call::Shutdown));
}

#[test]
fn reusing_an_id_whose_completion_is_still_queued_fails_closed() {
    let mut rig = Rig::ready();
    rig.req("start_scan", "x", json!({"service_uuid": rig::SCAN_UUID}));
    let token = rig.start_scan_token();
    rig.backend(BackendEvent::ScanStarted { scan: token });
    // The completion for "x" has not been drained by the writer yet.
    rig.req("start_scan", "x", json!({"service_uuid": rig::SCAN_UUID}));
    assert_eq!(rig.exit, Some(EXIT_REJECTED));
    let drained = rig.events();
    assert_eq!(
        types(&drained),
        vec!["scan_state"],
        "only the first completion was ever queued"
    );
}

#[test]
fn an_id_may_be_reused_after_its_completion_was_delivered() {
    let mut rig = Rig::ready();
    rig.open_scan("same");
    let (handle, _) = rig.open_scan("same");
    assert!(is_handle(&json!(handle)));
    assert_eq!(rig.exit, None);
}

#[test]
fn the_65th_outstanding_request_is_refused_retryably() {
    let mut rig = Rig::ready();
    let mut handles = Vec::new();
    for n in 0..8 {
        let (handle, _) =
            rig.connect_hint(&format!("c{n}"), &format!("winrt:c0000000000{n}:1"), 185);
        handles.push(handle);
    }
    let b64 = veles_otp23_feasibility::base64::encode(&common::synthetic(64, 1));
    for (n, handle) in handles.iter().enumerate() {
        for k in 0..8 {
            rig.req(
                "send",
                &format!("s{n}_{k}"),
                json!({"connection_handle": handle, "record_base64": b64}),
            );
        }
    }
    assert!(
        rig.events().is_empty(),
        "64 sends are outstanding, none completed"
    );
    rig.req(
        "send",
        "s65",
        json!({"connection_handle": handles[0], "record_base64": b64}),
    );
    let events = rig.events();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0]["code"], "cap_exceeded");
    assert_eq!(events[0]["recoverability"], "retryable");
    assert_eq!(events[0]["request_id"], "s65");
    assert_eq!(rig.exit, None);
}

// ---- scanning ----------------------------------------------------------------------------------

#[test]
fn start_scan_completes_only_when_the_backend_confirms() {
    let mut rig = Rig::ready();
    rig.req("start_scan", "s1", json!({"service_uuid": rig::SCAN_UUID}));
    assert!(
        rig.events().is_empty(),
        "no success before the watcher has started"
    );
    assert_eq!(
        rig.calls(),
        vec![Call::StartScan(
            rig.start_scan_token(),
            rig::SCAN_UUID.into()
        )]
    );
    let token = rig.start_scan_token();
    rig.backend(BackendEvent::ScanStarted { scan: token });
    let events = rig.events();
    assert_eq!(events[0]["status"], "started");
    assert!(is_handle(&events[0]["scan_handle"]));
    assert_eq!(events[0]["api_version"], 1);
}

#[test]
fn scan_start_failure_is_a_correlated_classified_error() {
    let mut rig = Rig::ready();
    rig.req("start_scan", "s1", json!({"service_uuid": rig::SCAN_UUID}));
    let token = rig.start_scan_token();
    rig.backend(BackendEvent::ScanFailed {
        scan: token,
        failure: failure("radio_off", true),
    });
    let events = rig.events();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0]["type"], "error");
    assert_eq!(events[0]["request_id"], "s1");
    assert_eq!(events[0]["code"], "scan_failed");
    assert_eq!(events[0]["stage"], "radio_off");
    assert_eq!(events[0]["recoverability"], "retryable");
    // The failed scan holds no capacity: two more can start.
    rig.open_scan("s2");
    rig.open_scan("s3");
}

#[test]
fn at_most_two_scans_may_be_open() {
    let mut rig = Rig::ready();
    rig.open_scan("s1");
    rig.open_scan("s2");
    rig.req("start_scan", "s3", json!({"service_uuid": rig::SCAN_UUID}));
    let events = rig.events();
    assert_eq!(events[0]["code"], "cap_exceeded");
    assert_eq!(events[0]["recoverability"], "retryable");
}

#[test]
fn device_found_carries_scan_and_device_handles_and_is_deduplicated_per_scan() {
    let mut rig = Rig::ready();
    let (scan_handle, scan) = rig.open_scan("s1");
    rig.advertise(scan, HINT_A, "Phone A");
    rig.advertise(scan, HINT_A, "Phone A");
    rig.advertise(scan, HINT_B, "Phone B");
    let events = rig.events();
    assert_eq!(types(&events), vec!["device_found", "device_found"]);
    assert_eq!(events[0]["scan_handle"], scan_handle);
    assert_eq!(events[0]["label"], "Phone A");
    assert_eq!(events[0]["platform_hint"], HINT_A);
    assert!(events[0].get("request_id").is_none(), "unsolicited");
    assert_ne!(events[0]["device_handle"], events[1]["device_handle"]);
    assert!(is_handle(&events[0]["device_handle"]));
}

#[test]
fn device_labels_and_hints_are_sanitised_to_envelope_bounds() {
    let mut rig = Rig::ready();
    let (_, scan) = rig.open_scan("s1");
    rig.advertise(scan, HINT_A, &format!("{}\u{7}x", "é".repeat(60)));
    rig.advertise(scan, HINT_B, "");
    let events = rig.events();
    assert_eq!(events.len(), 2);
    for event in &events {
        let label = event["label"].as_str().unwrap();
        assert!(
            (1..=64).contains(&label.len()),
            "label bytes {}",
            label.len()
        );
        assert!(!label.chars().any(char::is_control));
    }
}

#[test]
fn device_found_is_rate_limited_to_64_per_second_and_1024_per_scan() {
    let mut rig = Rig::ready();
    let (_, scan) = rig.open_scan("s1");
    for n in 0..100 {
        rig.advertise(scan, &format!("winrt:{n:012x}:1"), "Phone");
    }
    assert_eq!(
        rig.events().len(),
        64,
        "excess within one rolling second is dropped, not queued"
    );
    rig.advance(Duration::from_millis(1001));
    for n in 100..110 {
        rig.advertise(scan, &format!("winrt:{n:012x}:1"), "Phone");
    }
    assert_eq!(rig.events().len(), 10);
    // Drive the per-scan ceiling: 64 new devices per second, far past the 1024th.
    let mut total = 74;
    let mut n = 110;
    for _ in 0..25 {
        rig.advance(Duration::from_millis(1001));
        for _ in 0..64 {
            rig.advertise(scan, &format!("winrt:{n:012x}:1"), "Phone");
            n += 1;
        }
        total += rig.events().len();
    }
    assert_eq!(total, 1024, "no more than 1024 device_found per scan");
}

#[test]
fn stop_scan_is_correlated_and_follows_the_last_device_found() {
    let mut rig = Rig::ready();
    let (scan_handle, scan) = rig.open_scan("s1");
    rig.advertise(scan, HINT_A, "Phone");
    rig.req("stop_scan", "s2", json!({"scan_handle": scan_handle}));
    rig.advertise(scan, HINT_B, "Late");
    let events = rig.events();
    assert_eq!(types(&events), vec!["device_found", "scan_state"]);
    assert_eq!(events[1]["status"], "stopped");
    assert_eq!(events[1]["request_id"], "s2");
    assert_eq!(events[1]["scan_handle"], scan_handle);
    assert!(rig.calls().contains(&Call::StopScan(scan)));
    // A repeated stop addresses a released handle.
    rig.req("stop_scan", "s3", json!({"scan_handle": scan_handle}));
    assert_eq!(rig.events()[0]["code"], "unknown_handle");
}

#[test]
fn unsolicited_scan_end_has_no_request_id_and_releases_the_scan() {
    let mut rig = Rig::ready();
    let (scan_handle, scan) = rig.open_scan("s1");
    rig.backend(BackendEvent::ScanEnded {
        scan,
        failure: Some(failure("radio_off", true)),
    });
    let events = rig.events();
    assert_eq!(types(&events), vec!["error", "scan_state"]);
    assert!(events[0].get("request_id").is_none());
    assert_eq!(events[0]["scan_handle"], scan_handle);
    assert_eq!(events[0]["code"], "scan_failed");
    assert_eq!(events[1]["status"], "stopped");
    assert!(events[1].get("request_id").is_none());
    rig.open_scan("s2");
    rig.open_scan("s3");
}

#[test]
fn a_scan_stops_itself_after_300_seconds() {
    let mut rig = Rig::ready();
    let (scan_handle, scan) = rig.open_scan("s1");
    rig.advance(Duration::from_secs(299));
    assert!(rig.events().is_empty());
    rig.advance(Duration::from_secs(1));
    let events = rig.events();
    assert_eq!(types(&events), vec!["scan_state"]);
    assert_eq!(events[0]["status"], "stopped");
    assert_eq!(events[0]["scan_handle"], scan_handle);
    assert!(events[0].get("request_id").is_none());
    assert!(rig.calls().contains(&Call::StopScan(scan)));
}

// ---- connect -----------------------------------------------------------------------------------

#[test]
fn connect_by_device_handle_resolves_the_hint_and_completes_after_setup() {
    let mut rig = Rig::ready();
    let (_, scan) = rig.open_scan("s1");
    rig.advertise(scan, HINT_A, "Phone A");
    let device = rig.events()[0]["device_handle"]
        .as_str()
        .unwrap()
        .to_string();
    rig.req("connect", "c1", json!({"device_handle": device}));
    assert!(
        rig.events().is_empty(),
        "connected only after GATT discovery, MTU and subscription"
    );
    let token = rig.connect_token();
    assert_eq!(
        rig.calls().last(),
        Some(&Call::Connect(token, Target::Hint(HINT_A.into())))
    );
    rig.backend(BackendEvent::Connected {
        conn: token,
        mtu: 247,
        platform_hint: Some("refreshed".into()),
    });
    let events = rig.events();
    assert_eq!(events[0]["type"], "connected");
    assert_eq!(events[0]["request_id"], "c1");
    assert_eq!(events[0]["platform_hint"], "refreshed");
    assert!(is_handle(&events[0]["connection_handle"]));
}

#[test]
fn connect_to_an_unknown_or_malformed_target_fails_closed() {
    let mut rig = Rig::ready();
    rig.req(
        "connect",
        "c1",
        json!({"device_handle": "0123456789abcdef"}),
    );
    let events = rig.events();
    assert_eq!(events[0]["code"], "unknown_handle");
    assert_eq!(events[0]["request_id"], "c1");
    assert!(!rig.calls().iter().any(|c| matches!(c, Call::Connect(..))));
}

#[test]
fn a_device_already_connected_or_connecting_is_busy_and_retryable() {
    let mut rig = Rig::ready();
    rig.req("connect", "c1", json!({"platform_hint": HINT_A}));
    rig.req("connect", "c2", json!({"platform_hint": HINT_A}));
    let events = rig.events();
    assert_eq!(events.len(), 1);
    assert_eq!(
        (events[0]["code"].as_str(), events[0]["request_id"].as_str()),
        (Some("busy"), Some("c2"))
    );
    assert_eq!(events[0]["recoverability"], "retryable");
    let token = rig.connect_token();
    rig.backend(BackendEvent::Connected {
        conn: token,
        mtu: 185,
        platform_hint: None,
    });
    rig.events();
    rig.req("connect", "c3", json!({"platform_hint": HINT_A}));
    assert_eq!(rig.events()[0]["code"], "busy");
}

#[test]
fn connect_failure_is_classified_and_frees_the_device() {
    let mut rig = Rig::ready();
    rig.req("connect", "c1", json!({"platform_hint": HINT_A}));
    let token = rig.connect_token();
    rig.backend(BackendEvent::ConnectFailed {
        conn: token,
        failure: failure("gatt_profile", false),
    });
    let events = rig.events();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0]["code"], "connect_failed");
    assert_eq!(events[0]["stage"], "gatt_profile");
    assert_eq!(events[0]["recoverability"], "fatal");
    assert!(
        rig.calls().contains(&Call::Disconnect(token)),
        "failed setup releases the OS link"
    );
    rig.req("connect", "c2", json!({"platform_hint": HINT_A}));
    assert!(rig.events().is_empty(), "the device can be retried");
}

#[test]
fn a_negotiated_mtu_below_185_or_above_517_is_refused() {
    for mtu in [23, 184, 518, 65535] {
        let mut rig = Rig::ready();
        rig.req("connect", "c1", json!({"platform_hint": HINT_A}));
        let token = rig.connect_token();
        rig.backend(BackendEvent::Connected {
            conn: token,
            mtu,
            platform_hint: None,
        });
        let events = rig.events();
        assert_eq!(events.len(), 1, "mtu {mtu}");
        assert_eq!(
            (events[0]["code"].as_str(), events[0]["stage"].as_str()),
            (Some("connect_failed"), Some("mtu_range"))
        );
        assert!(rig.calls().contains(&Call::Disconnect(token)));
    }
}

#[test]
fn at_most_32_connections() {
    let mut rig = Rig::ready();
    for n in 0..32 {
        rig.req(
            "connect",
            &format!("c{n}"),
            json!({"platform_hint": format!("winrt:{n:012x}:1")}),
        );
    }
    rig.req(
        "connect",
        "c32",
        json!({"platform_hint": "winrt:ffffffffffff:1"}),
    );
    let events = rig.events();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0]["code"], "cap_exceeded");
    assert_eq!(events[0]["recoverability"], "retryable");
}

#[test]
fn evicted_device_handles_fail_with_unknown_handle() {
    let mut rig = Rig::ready();
    let (_, scan_a) = rig.open_scan("sa");
    let mut first = None;
    let mut seen = 0;
    let mut n = 0;
    while seen < 1024 {
        rig.advance(Duration::from_millis(1001));
        for _ in 0..64 {
            rig.advertise(scan_a, &format!("winrt:{n:012x}:1"), "Phone");
            n += 1;
        }
        for event in rig.events() {
            if first.is_none() {
                first = Some(event["device_handle"].as_str().unwrap().to_string());
            }
            seen += 1;
        }
    }
    let (_, scan_b) = rig.open_scan("sb");
    rig.advertise(scan_b, "winrt:eeeeeeeeeeee:1", "Newest");
    let newest = rig.events()[0]["device_handle"]
        .as_str()
        .unwrap()
        .to_string();
    rig.req("connect", "old", json!({"device_handle": first.unwrap()}));
    assert_eq!(
        rig.events()[0]["code"],
        "unknown_handle",
        "oldest handle was evicted (FIFO, 1024 max)"
    );
    rig.req("connect", "new", json!({"device_handle": newest}));
    assert!(rig.events().is_empty());
    assert!(
        rig.calls()
            .iter()
            .any(|c| matches!(c, Call::Connect(_, Target::Hint(h)) if h == "winrt:eeeeeeeeeeee:1"))
    );
}

// ---- send --------------------------------------------------------------------------------------

#[test]
fn send_fragments_the_whole_record_and_reports_sent_after_the_writes_complete() {
    let mut rig = Rig::ready();
    let (handle, conn) = rig.connect_hint("c1", HINT_A, 185);
    let record = common::synthetic(400, 4);
    rig.req("send", "m1", json!({"connection_handle": handle, "record_base64": veles_otp23_feasibility::base64::encode(&record)}));
    assert!(
        rig.events().is_empty(),
        "sent only after every fragment was written"
    );
    let (c, send, frames) = rig.last_write();
    assert_eq!(c, conn);
    assert_eq!(frames.len(), 3);
    assert_eq!(
        frames,
        fragment(&record, 185, frames[0][1]).unwrap(),
        "uniform GATT framing"
    );
    assert_ne!(frames[0][1], 0, "transaction id 0 is invalid");
    rig.backend(BackendEvent::WriteDone { conn, send });
    let events = rig.events();
    assert_eq!(events.len(), 1);
    assert_eq!(
        (events[0]["type"].as_str(), events[0]["request_id"].as_str()),
        (Some("sent"), Some("m1"))
    );
    assert_eq!(events[0]["connection_handle"], handle);
}

#[test]
fn sends_on_one_connection_are_serialised_and_use_fresh_transaction_ids() {
    let mut rig = Rig::ready();
    let (handle, conn) = rig.connect_hint("c1", HINT_A, 185);
    let b64 = |n: usize| veles_otp23_feasibility::base64::encode(&common::synthetic(n, n as u8));
    rig.req(
        "send",
        "m1",
        json!({"connection_handle": handle, "record_base64": b64(10)}),
    );
    rig.req(
        "send",
        "m2",
        json!({"connection_handle": handle, "record_base64": b64(20)}),
    );
    let writes: Vec<_> = rig
        .calls()
        .into_iter()
        .filter(|c| matches!(c, Call::Write(..)))
        .collect();
    assert_eq!(
        writes.len(),
        1,
        "one transaction per characteristic at a time"
    );
    let (_, first_send, first_frames) = rig.last_write();
    rig.backend(BackendEvent::WriteDone {
        conn,
        send: first_send,
    });
    let events = rig.events();
    assert_eq!(events[0]["request_id"], "m1");
    let (_, second_send, second_frames) = rig.last_write();
    assert_ne!(second_send, first_send);
    assert_ne!(
        first_frames[0][1], second_frames[0][1],
        "transaction ids advance per record"
    );
    rig.backend(BackendEvent::WriteDone {
        conn,
        send: second_send,
    });
    assert_eq!(rig.events()[0]["request_id"], "m2");
}

#[test]
fn send_validation_uses_the_specific_error_codes() {
    let mut rig = Rig::ready();
    let (handle, _) = rig.connect_hint("c1", HINT_A, 185);
    let b64 = |n: usize| veles_otp23_feasibility::base64::encode(&vec![7u8; n]);
    rig.req(
        "send",
        "big",
        json!({"connection_handle": handle, "record_base64": b64(4097)}),
    );
    rig.req(
        "send",
        "huge",
        json!({"connection_handle": handle, "record_base64": b64(5120)}),
    );
    rig.req(
        "send",
        "ghost",
        json!({"connection_handle": "0123456789abcdef", "record_base64": b64(4)}),
    );
    let events = rig.events();
    assert_eq!(
        events
            .iter()
            .map(|e| e["code"].as_str().unwrap())
            .collect::<Vec<_>>(),
        vec!["oversize_record", "oversize_record", "unknown_handle"]
    );
    rig.req(
        "send",
        "max",
        json!({"connection_handle": handle, "record_base64": b64(4096)}),
    );
    assert!(
        rig.events().is_empty(),
        "4096 bytes is accepted and in flight"
    );
}

#[test]
fn send_queue_is_bounded_per_peer_by_records_and_bytes() {
    let mut rig = Rig::ready();
    let (handle, _) = rig.connect_hint("c1", HINT_A, 185);
    let small = veles_otp23_feasibility::base64::encode(&[1u8; 8]);
    for n in 0..16 {
        rig.req(
            "send",
            &format!("a{n}"),
            json!({"connection_handle": handle, "record_base64": small}),
        );
    }
    rig.req(
        "send",
        "a16",
        json!({"connection_handle": handle, "record_base64": small}),
    );
    let events = rig.events();
    assert_eq!(
        (events[0]["code"].as_str(), events[0]["request_id"].as_str()),
        (Some("cap_exceeded"), Some("a16"))
    );
    // Bytes: four 4096-byte records fill 16 KiB; a fifth does not fit.
    let mut rig = Rig::ready();
    let (handle, _) = rig.connect_hint("c1", HINT_A, 185);
    let big = veles_otp23_feasibility::base64::encode(&vec![2u8; 4096]);
    for n in 0..4 {
        rig.req(
            "send",
            &format!("b{n}"),
            json!({"connection_handle": handle, "record_base64": big}),
        );
    }
    assert!(rig.events().is_empty());
    rig.req(
        "send",
        "b4",
        json!({"connection_handle": handle, "record_base64": big}),
    );
    assert_eq!(rig.events()[0]["code"], "cap_exceeded");
}

#[test]
fn global_send_queue_ceiling_is_128_records_or_128_kib() {
    let mut rig = Rig::ready();
    let mut handles = Vec::new();
    for n in 0..9 {
        handles.push(
            rig.connect_hint(&format!("c{n}"), &format!("winrt:{n:012x}:1"), 185)
                .0,
        );
    }
    let big = veles_otp23_feasibility::base64::encode(&vec![2u8; 4096]);
    // Eight peers x 16 KiB = 128 KiB exactly (but 4 outstanding sends each stays under 64 requests).
    for (n, handle) in handles.iter().take(8).enumerate() {
        for k in 0..4 {
            rig.req(
                "send",
                &format!("s{n}_{k}"),
                json!({"connection_handle": handle, "record_base64": big}),
            );
        }
    }
    assert!(rig.events().is_empty());
    rig.req("send", "over", json!({"connection_handle": handles[8], "record_base64": veles_otp23_feasibility::base64::encode(&[1u8])}));
    let events = rig.events();
    assert_eq!(
        (events[0]["code"].as_str(), events[0]["request_id"].as_str()),
        (Some("cap_exceeded"), Some("over"))
    );
}

// ---- independent handles ----------------------------------------------------------------------------

#[test]
fn two_connects_complete_independently_and_out_of_order() {
    let mut rig = Rig::ready();
    rig.req("connect", "ca", json!({"platform_hint": HINT_A}));
    let token_a = rig.connect_token();
    rig.req("connect", "cb", json!({"platform_hint": HINT_B}));
    let token_b = rig.connect_token();
    assert_ne!(token_a, token_b);
    rig.backend(BackendEvent::Connected {
        conn: token_b,
        mtu: 185,
        platform_hint: None,
    });
    rig.backend(BackendEvent::Connected {
        conn: token_a,
        mtu: 247,
        platform_hint: None,
    });
    let events = rig.events();
    assert_eq!(
        events
            .iter()
            .map(|e| e["request_id"].as_str().unwrap())
            .collect::<Vec<_>>(),
        vec!["cb", "ca"]
    );
    assert_ne!(
        events[0]["connection_handle"],
        events[1]["connection_handle"]
    );
}

#[test]
fn inbound_records_never_cross_connections_even_when_fragments_interleave() {
    let mut rig = Rig::ready();
    let (handle_a, conn_a) = rig.connect_hint("ca", HINT_A, 185);
    let (handle_b, conn_b) = rig.connect_hint("cb", HINT_B, 247);
    let record_a = common::synthetic(900, 0xa0);
    let record_b = common::synthetic(1500, 0xb0);
    let frames_a = fragment(&record_a, 185, 9).unwrap();
    let frames_b = fragment(&record_b, 247, 9).unwrap(); // same transaction id on a different link
    let (mut ia, mut ib) = (0, 0);
    while ia < frames_a.len() || ib < frames_b.len() {
        if ia < frames_a.len() {
            rig.backend(BackendEvent::Fragment {
                conn: conn_a,
                bytes: frames_a[ia].clone(),
            });
            ia += 1;
        }
        if ib < frames_b.len() {
            rig.backend(BackendEvent::Fragment {
                conn: conn_b,
                bytes: frames_b[ib].clone(),
            });
            ib += 1;
        }
    }
    let events = rig.events();
    assert_eq!(events.len(), 2);
    let decode = |e: &serde_json::Value| {
        veles_otp23_feasibility::base64::decode(e["record_base64"].as_str().unwrap(), 5120).unwrap()
    };
    let by_handle = |h: &str| {
        events
            .iter()
            .find(|e| e["connection_handle"] == h)
            .map(decode)
            .unwrap()
    };
    assert_eq!(by_handle(&handle_a), record_a);
    assert_eq!(by_handle(&handle_b), record_b);
    assert!(
        events
            .iter()
            .all(|e| e["type"] == "message" && e.get("request_id").is_none())
    );
}

#[test]
fn malformed_inbound_fragments_are_dropped_silently_and_do_not_affect_the_peer_state() {
    let mut rig = Rig::ready();
    let (_, conn) = rig.connect_hint("c1", HINT_A, 185);
    rig.backend(BackendEvent::Fragment {
        conn,
        bytes: vec![0x80, 0, 0, 1, 0xaa],
    }); // transaction id 0
    rig.backend(BackendEvent::Fragment {
        conn,
        bytes: vec![0x01],
    }); // short header
    assert!(rig.events().is_empty());
    assert_eq!(rig.exit, None);
    rig.inbound(conn, &[0x56, 0x45, 0x4c, 0x02], 185, 3);
    let events = rig.events();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0]["record_base64"], "VkVMAg==");
}

#[test]
fn repeated_conflicting_transaction_starts_disconnect_only_that_peer() {
    let mut rig = Rig::ready();
    let (handle_a, conn_a) = rig.connect_hint("ca", HINT_A, 185);
    let (handle_b, conn_b) = rig.connect_hint("cb", HINT_B, 185);
    let mut start = vec![0x00, 5, 0x01, 0x90];
    start.extend(common::synthetic(178, 1));
    for _ in 0..2 {
        rig.backend(BackendEvent::Fragment {
            conn: conn_a,
            bytes: start.clone(),
        });
    }
    rig.backend(BackendEvent::Fragment {
        conn: conn_a,
        bytes: start.clone(),
    });
    let events = rig.events();
    assert_eq!(types(&events), vec!["disconnected"], "{events:?}");
    assert_eq!(events[0]["connection_handle"], handle_a);
    assert!(events[0].get("request_id").is_none(), "unsolicited");
    assert!(rig.calls().contains(&Call::Disconnect(conn_a)));
    assert!(!rig.calls().contains(&Call::Disconnect(conn_b)));
    // B is untouched and keeps working.
    rig.inbound(conn_b, b"synthetic", 185, 1);
    let events = rig.events();
    assert_eq!(events[0]["connection_handle"], handle_b);
}

#[test]
fn handles_are_unique_random_looking_and_never_reused() {
    let mut rig = Rig::ready();
    let mut seen = HashSet::new();
    let (scan_handle, scan) = rig.open_scan("s1");
    seen.insert(scan_handle);
    let mut devices = 0;
    let mut n = 0;
    while devices < 300 {
        rig.advance(Duration::from_millis(1001));
        for _ in 0..64 {
            rig.advertise(scan, &format!("winrt:{n:012x}:1"), "P");
            n += 1;
        }
        for event in rig.events() {
            assert!(is_handle(&event["device_handle"]));
            assert!(
                seen.insert(event["device_handle"].as_str().unwrap().to_string()),
                "device handle reused"
            );
            devices += 1;
        }
    }
    for k in 0..10 {
        let (handle, conn) =
            rig.connect_hint(&format!("c{k}"), &format!("winrt:{:012x}:9", 1000 + k), 185);
        assert!(seen.insert(handle.clone()), "connection handle reused");
        rig.req(
            "disconnect",
            &format!("d{k}"),
            json!({"connection_handle": handle}),
        );
        rig.events();
        let _ = conn;
    }
}

#[test]
fn unknown_backend_tokens_and_late_events_are_ignored() {
    let mut rig = Rig::ready();
    rig.backend(BackendEvent::WriteDone {
        conn: 999,
        send: 998,
    });
    rig.backend(BackendEvent::Fragment {
        conn: 999,
        bytes: vec![0x80, 1, 0, 1, 0],
    });
    rig.backend(BackendEvent::LinkLost { conn: 999 });
    rig.backend(BackendEvent::Connected {
        conn: 999,
        mtu: 185,
        platform_hint: None,
    });
    rig.backend(BackendEvent::ScanStarted { scan: 999 });
    assert!(rig.events().is_empty());
    assert_eq!(rig.exit, None);
}
