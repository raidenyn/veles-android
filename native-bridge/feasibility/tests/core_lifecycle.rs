//! Host core: disconnect isolation, link loss, timeouts, shutdown/EOF/port-closure cleanup,
//! output-queue overflow and deadline scheduling.

mod common;
mod rig;

use std::time::Duration;

use rig::{Call, Rig, failure, types};
use serde_json::json;
use veles_otp23_feasibility::backend::{BackendEvent, Token};
use veles_otp23_feasibility::base64;
use veles_otp23_feasibility::core::{
    EXIT_BACKEND_OVERFLOW, EXIT_IO, EXIT_OK, EXIT_OUTPUT_OVERFLOW, EXIT_REJECTED, Input,
};

const HINT_A: &str = "winrt:a00000000001:1";
const HINT_B: &str = "winrt:b00000000002:1";

fn record(n: usize) -> String {
    base64::encode(&common::synthetic(n, n as u8))
}

/// Two connected phones, each with one in-flight send. Returns handles, tokens and send tokens.
struct TwoPhones {
    rig: Rig,
    a: (String, Token),
    b: (String, Token),
}

fn two_phones() -> TwoPhones {
    let mut rig = Rig::ready();
    let a = rig.connect_hint("ca", HINT_A, 185);
    let b = rig.connect_hint("cb", HINT_B, 185);
    TwoPhones { rig, a, b }
}

// ---- disconnect ----------------------------------------------------------------------------------

#[test]
fn disconnect_releases_only_that_handle_and_fails_its_pending_sends_first() {
    let TwoPhones { mut rig, a, b } = two_phones();
    rig.req(
        "send",
        "sa1",
        json!({"connection_handle": a.0, "record_base64": record(10)}),
    );
    rig.req(
        "send",
        "sa2",
        json!({"connection_handle": a.0, "record_base64": record(11)}),
    );
    rig.req(
        "send",
        "sb1",
        json!({"connection_handle": b.0, "record_base64": record(12)}),
    );
    rig.req("disconnect", "da", json!({"connection_handle": a.0}));
    let events = rig.events();
    assert_eq!(
        types(&events),
        vec!["error", "error", "disconnected"],
        "{events:?}"
    );
    assert_eq!(events[0]["request_id"], "sa1");
    assert_eq!(events[1]["request_id"], "sa2");
    for error in &events[..2] {
        assert_eq!(
            (error["code"].as_str(), error["recoverability"].as_str()),
            (Some("send_failed"), Some("fatal"))
        );
        assert_eq!(error["connection_handle"], a.0);
    }
    assert_eq!(
        events[2]["request_id"], "da",
        "a requested disconnect emits one correlated event only"
    );
    assert_eq!(events[2]["connection_handle"], a.0);
    assert!(rig.calls().contains(&Call::Disconnect(a.1)));
    assert!(!rig.calls().contains(&Call::Disconnect(b.1)));
    // B is unaffected: its write still completes and it can still send.
    let (conn, send, _) = rig
        .calls()
        .into_iter()
        .rev()
        .find_map(|c| match c {
            Call::Write(conn, send, frames) if conn == b.1 => Some((conn, send, frames)),
            _ => None,
        })
        .unwrap();
    rig.backend(BackendEvent::WriteDone { conn, send });
    assert_eq!(rig.events()[0]["request_id"], "sb1");
}

#[test]
fn a_released_handle_is_unknown_and_late_backend_events_for_it_are_ignored() {
    let TwoPhones { mut rig, a, .. } = two_phones();
    rig.req("disconnect", "da", json!({"connection_handle": a.0}));
    rig.events();
    rig.req("disconnect", "da2", json!({"connection_handle": a.0}));
    rig.req(
        "send",
        "late",
        json!({"connection_handle": a.0, "record_base64": record(5)}),
    );
    let events = rig.events();
    assert_eq!(
        events
            .iter()
            .map(|e| e["code"].as_str().unwrap())
            .collect::<Vec<_>>(),
        vec!["unknown_handle", "unknown_handle"]
    );
    rig.backend(BackendEvent::LinkLost { conn: a.1 });
    rig.inbound(a.1, b"ghost", 185, 1);
    rig.backend(BackendEvent::WriteDone { conn: a.1, send: 1 });
    assert!(
        rig.events().is_empty(),
        "no message or lifecycle event may use a released handle"
    );
}

#[test]
fn disconnecting_a_connecting_link_fails_its_connect_then_reports_disconnected() {
    let mut rig = Rig::ready();
    rig.req("connect", "c1", json!({"platform_hint": HINT_A}));
    let events = rig.events();
    assert!(events.is_empty());
    // The connection handle is only revealed by `connected`; a pending connect cannot be addressed.
    rig.req(
        "disconnect",
        "d1",
        json!({"connection_handle": "0123456789abcdef"}),
    );
    assert_eq!(rig.events()[0]["code"], "unknown_handle");
}

// ---- link loss -----------------------------------------------------------------------------------

#[test]
fn link_loss_errors_in_flight_requests_then_emits_one_unsolicited_disconnected() {
    let TwoPhones { mut rig, a, b } = two_phones();
    rig.req(
        "send",
        "sa1",
        json!({"connection_handle": a.0, "record_base64": record(10)}),
    );
    rig.req(
        "send",
        "sa2",
        json!({"connection_handle": a.0, "record_base64": record(11)}),
    );
    rig.req(
        "send",
        "sb1",
        json!({"connection_handle": b.0, "record_base64": record(12)}),
    );
    rig.backend(BackendEvent::LinkLost { conn: a.1 });
    let events = rig.events();
    assert_eq!(
        types(&events),
        vec!["error", "error", "disconnected"],
        "{events:?}"
    );
    assert_eq!(events[0]["request_id"], "sa1");
    assert_eq!(events[1]["request_id"], "sa2");
    assert!(events[2].get("request_id").is_none());
    assert_eq!(events[2]["connection_handle"], a.0);
    assert!(
        rig.calls().contains(&Call::Disconnect(a.1)),
        "OS resources are released explicitly"
    );
    // A second loss notification for the same link is a duplicate and must not repeat anything.
    rig.backend(BackendEvent::LinkLost { conn: a.1 });
    assert!(rig.events().is_empty());
    assert!(!rig.calls().contains(&Call::Disconnect(b.1)));
}

#[test]
fn a_failed_gatt_write_fails_the_send_and_tears_down_only_that_link() {
    let TwoPhones { mut rig, a, b } = two_phones();
    rig.req(
        "send",
        "sa1",
        json!({"connection_handle": a.0, "record_base64": record(10)}),
    );
    rig.req(
        "send",
        "sa2",
        json!({"connection_handle": a.0, "record_base64": record(11)}),
    );
    let (conn, send, _) = rig.last_write();
    assert_eq!(conn, a.1);
    rig.backend(BackendEvent::WriteFailed {
        conn,
        send,
        failure: failure("write", false),
    });
    let events = rig.events();
    assert_eq!(
        types(&events),
        vec!["error", "error", "disconnected"],
        "{events:?}"
    );
    assert_eq!(
        (events[0]["code"].as_str(), events[0]["stage"].as_str()),
        (Some("send_failed"), Some("write"))
    );
    assert_eq!(events[1]["request_id"], "sa2");
    assert!(events[2].get("request_id").is_none());
    assert!(!rig.calls().contains(&Call::Disconnect(b.1)));
}

#[test]
fn connect_is_failed_not_disconnected_when_the_link_drops_during_setup() {
    let mut rig = Rig::ready();
    rig.req("connect", "c1", json!({"platform_hint": HINT_A}));
    let token = rig.connect_token();
    rig.backend(BackendEvent::LinkLost { conn: token });
    let events = rig.events();
    assert_eq!(
        types(&events),
        vec!["error"],
        "no connection handle was ever disclosed"
    );
    assert_eq!(events[0]["code"], "connect_failed");
    assert_eq!(events[0]["request_id"], "c1");
}

// ---- timeouts --------------------------------------------------------------------------------------

#[test]
fn connect_times_out_after_30_seconds_and_releases_the_link() {
    let mut rig = Rig::ready();
    rig.req("connect", "c1", json!({"platform_hint": HINT_A}));
    let token = rig.connect_token();
    rig.advance(Duration::from_millis(29_999));
    assert!(rig.events().is_empty());
    rig.advance(Duration::from_millis(1));
    let events = rig.events();
    assert_eq!(events.len(), 1);
    assert_eq!(
        (
            events[0]["code"].as_str(),
            events[0]["recoverability"].as_str()
        ),
        (Some("timeout"), Some("retryable"))
    );
    assert_eq!(events[0]["request_id"], "c1");
    assert!(rig.calls().contains(&Call::Disconnect(token)));
    // A late success never completes the request.
    rig.backend(BackendEvent::Connected {
        conn: token,
        capacity: veles_otp23_feasibility::fragment::LinkCapacity::AttMtu(185),
        platform_hint: None,
    });
    assert!(rig.events().is_empty());
}

#[test]
fn send_times_out_after_30_seconds_and_the_half_written_link_is_dropped() {
    let TwoPhones { mut rig, a, b } = two_phones();
    rig.req(
        "send",
        "sa1",
        json!({"connection_handle": a.0, "record_base64": record(10)}),
    );
    rig.advance(Duration::from_secs(10));
    rig.req(
        "send",
        "sb1",
        json!({"connection_handle": b.0, "record_base64": record(10)}),
    );
    rig.advance(Duration::from_secs(20));
    let events = rig.events();
    assert_eq!(types(&events), vec!["error", "disconnected"], "{events:?}");
    assert_eq!(
        (events[0]["code"].as_str(), events[0]["request_id"].as_str()),
        (Some("timeout"), Some("sa1"))
    );
    assert_eq!(events[1]["connection_handle"], a.0);
    // sb1 started 20 s ago: still pending, and B is still connected.
    rig.advance(Duration::from_secs(10));
    assert_eq!(rig.events()[0]["request_id"], "sb1");
}

#[test]
fn inbound_partial_records_expire_after_five_seconds_of_inactivity() {
    let mut rig = Rig::ready();
    let (_, conn) = rig.connect_hint("c1", HINT_A, 185);
    let frames = veles_otp23_feasibility::fragment::fragment(
        &common::synthetic(400, 1),
        common::budget(185),
        8,
    )
    .unwrap();
    rig.backend(BackendEvent::Fragment {
        conn,
        bytes: frames[0].clone(),
    });
    let deadline = rig
        .core
        .next_deadline()
        .expect("a reassembly deadline is pending");
    assert_eq!(deadline, rig.now + Duration::from_secs(5));
    rig.advance(Duration::from_secs(5));
    rig.backend(BackendEvent::Fragment {
        conn,
        bytes: frames[1].clone(),
    });
    rig.backend(BackendEvent::Fragment {
        conn,
        bytes: frames[2].clone(),
    });
    assert!(
        rig.events().is_empty(),
        "the expired transaction can no longer complete"
    );
}

#[test]
fn next_deadline_tracks_the_earliest_pending_work() {
    let mut rig = Rig::ready();
    assert_eq!(rig.core.next_deadline(), None);
    let start = rig.now;
    rig.req("connect", "c1", json!({"platform_hint": HINT_A}));
    assert_eq!(
        rig.core.next_deadline(),
        Some(start + Duration::from_secs(30))
    );
    let token = rig.connect_token();
    rig.backend(BackendEvent::Connected {
        conn: token,
        capacity: veles_otp23_feasibility::fragment::LinkCapacity::AttMtu(185),
        platform_hint: None,
    });
    rig.events();
    assert_eq!(
        rig.core.next_deadline(),
        None,
        "an idle connection needs no timer"
    );
    rig.open_scan("s1");
    assert_eq!(
        rig.core.next_deadline(),
        Some(start + Duration::from_secs(300))
    );
}

// ---- shutdown ------------------------------------------------------------------------------------

#[test]
fn shutdown_orders_errors_then_lifecycle_then_shutdown_complete_last() {
    let mut rig = Rig::ready();
    let (scan_handle, scan) = rig.open_scan("s1");
    let (handle_a, conn_a) = rig.connect_hint("ca", HINT_A, 185);
    let (handle_b, conn_b) = rig.connect_hint("cb", HINT_B, 185);
    rig.req(
        "send",
        "sa1",
        json!({"connection_handle": handle_a, "record_base64": record(10)}),
    );
    rig.req(
        "connect",
        "pending",
        json!({"platform_hint": "winrt:c00000000003:1"}),
    );
    let pending = rig.connect_token();
    rig.req("shutdown", "bye", json!({}));
    let events = rig.events();
    let kinds = types(&events);
    assert_eq!(
        kinds.last().map(String::as_str),
        Some("shutdown_complete"),
        "{events:?}"
    );
    assert_eq!(events.last().unwrap()["request_id"], "bye");
    // 1. every other pending request gets a correlated error (internal, fatal)
    let errors: Vec<_> = events.iter().filter(|e| e["type"] == "error").collect();
    assert_eq!(errors.len(), 2);
    for e in &errors {
        assert_eq!(
            (e["code"].as_str(), e["recoverability"].as_str()),
            (Some("internal"), Some("fatal"))
        );
    }
    let ids: Vec<_> = errors
        .iter()
        .map(|e| e["request_id"].as_str().unwrap())
        .collect();
    assert!(ids.contains(&"sa1") && ids.contains(&"pending"));
    // 2. then the remaining unsolicited lifecycle events: scan stop, disconnects of open links
    let first_lifecycle = kinds
        .iter()
        .position(|k| k == "scan_state" || k == "disconnected")
        .unwrap();
    assert!(
        kinds[..first_lifecycle].iter().all(|k| k == "error"),
        "errors precede lifecycle events: {kinds:?}"
    );
    let lifecycle: Vec<_> = events
        .iter()
        .filter(|e| e["type"] != "error" && e["type"] != "shutdown_complete")
        .collect();
    assert_eq!(lifecycle.len(), 3, "{events:?}");
    assert!(lifecycle.iter().any(|e| e["type"] == "scan_state"
        && e["scan_handle"] == scan_handle
        && e["status"] == "stopped"));
    for handle in [&handle_a, &handle_b] {
        assert!(
            lifecycle
                .iter()
                .any(|e| e["type"] == "disconnected" && e["connection_handle"] == *handle)
        );
    }
    assert!(lifecycle.iter().all(|e| e.get("request_id").is_none()));
    // 3. every OS resource released, then exit 0
    let calls = rig.calls();
    for expected in [
        Call::StopScan(scan),
        Call::Disconnect(conn_a),
        Call::Disconnect(conn_b),
        Call::Disconnect(pending),
    ] {
        assert!(calls.contains(&expected), "{expected:?} in {calls:?}");
    }
    assert_eq!(calls.last(), Some(&Call::Shutdown));
    assert_eq!(rig.exit, Some(EXIT_OK));
}

#[test]
fn nothing_is_accepted_or_emitted_after_shutdown() {
    let mut rig = Rig::ready();
    rig.req("shutdown", "bye", json!({}));
    rig.events();
    let calls_after_exit = rig.calls().len();
    rig.req(
        "start_scan",
        "late",
        json!({"service_uuid": rig::SCAN_UUID}),
    );
    rig.backend(BackendEvent::ScanStarted { scan: 1 });
    rig.input(Input::Tick);
    assert!(rig.events().is_empty());
    assert_eq!(rig.calls().len(), calls_after_exit);
}

// ---- stdin EOF / Chrome port closure / stdout closure -------------------------------------------------

fn busy_rig() -> (Rig, Token, Token, Token) {
    let mut rig = Rig::ready();
    let (_, scan) = rig.open_scan("s1");
    let (handle_a, conn_a) = rig.connect_hint("ca", HINT_A, 185);
    let (_, conn_b) = rig.connect_hint("cb", HINT_B, 185);
    rig.req(
        "send",
        "sa1",
        json!({"connection_handle": handle_a, "record_base64": record(10)}),
    );
    (rig, scan, conn_a, conn_b)
}

#[test]
fn stdin_eof_stops_scans_disconnects_links_and_exits_without_output() {
    let (mut rig, scan, conn_a, conn_b) = busy_rig();
    rig.input(Input::StdinEof);
    assert_eq!(rig.exit, Some(EXIT_OK));
    assert!(
        rig.events().is_empty(),
        "the port is gone; nothing is written"
    );
    let calls = rig.calls();
    for expected in [
        Call::StopScan(scan),
        Call::Disconnect(conn_a),
        Call::Disconnect(conn_b),
    ] {
        assert!(calls.contains(&expected), "{expected:?}");
    }
    assert_eq!(calls.last(), Some(&Call::Shutdown));
}

#[test]
fn eof_before_hello_also_exits_cleanly() {
    let mut rig = Rig::new();
    rig.input(Input::StdinEof);
    assert_eq!(rig.exit, Some(EXIT_OK));
    assert_eq!(rig.calls(), vec![Call::Shutdown]);
}

#[test]
fn stdout_closure_is_treated_as_port_closure() {
    let (mut rig, scan, conn_a, conn_b) = busy_rig();
    rig.input(Input::StdoutClosed);
    assert_eq!(rig.exit, Some(EXIT_OK));
    let calls = rig.calls();
    for expected in [
        Call::StopScan(scan),
        Call::Disconnect(conn_a),
        Call::Disconnect(conn_b),
        Call::Shutdown,
    ] {
        assert!(calls.contains(&expected), "{expected:?}");
    }
}

#[test]
fn framing_violations_fail_closed_with_exit_2_and_io_errors_with_exit_1() {
    let (mut rig, scan, conn_a, _) = busy_rig();
    rig.input(Input::StdinFraming);
    assert_eq!(rig.exit, Some(EXIT_REJECTED));
    assert!(rig.events().is_empty());
    assert!(
        rig.calls().contains(&Call::StopScan(scan))
            && rig.calls().contains(&Call::Disconnect(conn_a))
    );

    let (mut rig, ..) = busy_rig();
    rig.input(Input::StdinIoError);
    assert_eq!(rig.exit, Some(EXIT_IO));
    assert_eq!(rig.calls().last(), Some(&Call::Shutdown));
}

#[test]
fn backend_callback_overflow_fails_closed() {
    let (mut rig, scan, conn_a, _) = busy_rig();
    rig.input(Input::BackendOverflow);
    assert_eq!(rig.exit, Some(EXIT_BACKEND_OVERFLOW));
    assert!(
        rig.calls().contains(&Call::StopScan(scan))
            && rig.calls().contains(&Call::Disconnect(conn_a))
    );
}

// ---- output queue overflow -------------------------------------------------------------------------------

#[test]
fn output_queue_message_overflow_fails_closed_without_dropping_or_reordering() {
    let mut rig = Rig::with_queue(4, 256 * 1024);
    rig.hello();
    let (_, scan) = rig.open_scan("s1");
    let (_, conn_a) = rig.connect_hint("ca", HINT_A, 185);
    // Renderer frozen: nothing drains. Five unsolicited device events exceed four slots.
    for n in 0..8 {
        rig.advertise(scan, &format!("winrt:{n:012x}:1"), "P");
    }
    assert_eq!(rig.exit, Some(EXIT_OUTPUT_OVERFLOW));
    let calls = rig.calls();
    assert!(calls.contains(&Call::StopScan(scan)) && calls.contains(&Call::Disconnect(conn_a)));
    assert_eq!(calls.last(), Some(&Call::Shutdown));
    let delivered = rig.events();
    assert_eq!(delivered.len(), 4, "exactly the queued prefix survives");
    assert!(
        delivered.iter().all(|e| e["type"] == "device_found"),
        "{delivered:?}"
    );
    let hints: Vec<_> = delivered
        .iter()
        .map(|e| e["platform_hint"].as_str().unwrap().to_string())
        .collect();
    let mut sorted = hints.clone();
    sorted.sort();
    assert_eq!(hints, sorted, "order preserved");
}

#[test]
fn output_queue_byte_overflow_also_fails_closed() {
    let mut rig = Rig::with_queue(128, 700);
    rig.hello();
    let (_, scan) = rig.open_scan("s1");
    for n in 0..50 {
        rig.advertise(scan, &format!("winrt:{n:012x}:1"), "P");
        if rig.exit.is_some() {
            break;
        }
    }
    assert_eq!(rig.exit, Some(EXIT_OUTPUT_OVERFLOW));
    assert!(rig.out.queued_bytes() <= 700);
}

#[test]
fn an_overflowing_message_is_not_accepted_and_no_request_resurrects() {
    let mut rig = Rig::with_queue(1, 256 * 1024);
    rig.hello();
    rig.req("start_scan", "s1", json!({"service_uuid": rig::SCAN_UUID}));
    let token = rig.start_scan_token();
    rig.backend(BackendEvent::ScanStarted { scan: token }); // fills the single slot
    rig.req(
        "stop_scan",
        "s2",
        json!({"scan_handle": "0123456789abcdef"}),
    ); // error completion overflows
    assert_eq!(rig.exit, Some(EXIT_OUTPUT_OVERFLOW));
    let delivered = rig.events();
    assert_eq!(delivered.len(), 1);
    assert_eq!(delivered[0]["request_id"], "s1");
}
