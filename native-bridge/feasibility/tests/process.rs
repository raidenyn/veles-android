//! Compiled-binary tests over real stdin/stdout pipes (simulated BLE backend): framing, exit
//! codes, shutdown with an idle stdin, EOF cleanup, independent handles and record boundaries.

mod common;

use std::io::Write;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use veles_otp23_feasibility::base64;
use veles_otp23_feasibility::framing::{read_frame, write_frame};
use veles_otp23_feasibility::identity::EXPECTED_ORIGIN;

const WAIT: Duration = Duration::from_secs(10);

/// `OTP23_HOST_EXE` / `OTP23_HOST_FLAVOUR` rerun the identical suite against the Tauri-shell host.
fn host_exe() -> String {
    std::env::var("OTP23_HOST_EXE").unwrap_or_else(|_| env!("CARGO_BIN_EXE_otp23-host").to_string())
}

fn flavour() -> String {
    std::env::var("OTP23_HOST_FLAVOUR").unwrap_or_else(|_| "plain".to_string())
}
const SCAN_UUID: &str = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51";

struct Host {
    child: Child,
    stdin: Option<ChildStdin>,
    frames: Receiver<Option<Value>>,
    stdout_closed: bool,
}

impl Host {
    fn spawn_with(args: &[&str]) -> Host {
        let mut child = Command::new(host_exe())
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn host");
        let mut stdout = child.stdout.take().unwrap();
        let (tx, frames) = mpsc::channel();
        thread::spawn(move || {
            loop {
                match read_frame(&mut stdout) {
                    Ok(Some(bytes)) => {
                        let _ = tx.send(Some(
                            serde_json::from_slice(&bytes).expect("host emits JSON"),
                        ));
                    }
                    _ => {
                        let _ = tx.send(None); // stdout closed
                        return;
                    }
                }
            }
        });
        let stdin = child.stdin.take();
        Host {
            child,
            stdin,
            frames,
            stdout_closed: false,
        }
    }

    fn spawn() -> Host {
        Self::spawn_with(&[EXPECTED_ORIGIN, "--backend", "sim"])
    }

    fn send(&mut self, value: Value) {
        let bytes = serde_json::to_vec(&value).unwrap();
        self.send_raw_frame(&bytes);
    }

    fn send_raw_frame(&mut self, payload: &[u8]) {
        let stdin = self.stdin.as_mut().expect("stdin open");
        write_frame(stdin, payload).expect("write frame");
    }

    fn send_bytes(&mut self, bytes: &[u8]) {
        let stdin = self.stdin.as_mut().expect("stdin open");
        stdin.write_all(bytes).unwrap();
        stdin.flush().unwrap();
    }

    fn req(&mut self, kind: &str, id: &str, extra: Value) {
        let mut value = json!({"envelope_version":1,"api_version":1,"type":kind,"request_id":id});
        for (key, field) in extra.as_object().unwrap() {
            value[key] = field.clone();
        }
        self.send(value);
    }

    fn hello(&mut self) -> Value {
        self.send(
            json!({"envelope_version":1,"type":"hello","request_id":"hello","api_versions":[1]}),
        );
        self.next()
    }

    /// Next framed message; panics on EOF or timeout.
    fn next(&mut self) -> Value {
        match self.frames.recv_timeout(WAIT) {
            Ok(Some(value)) => value,
            Ok(None) => panic!("host stdout closed while a message was expected"),
            Err(RecvTimeoutError::Timeout) => panic!("timed out waiting for a host message"),
            Err(RecvTimeoutError::Disconnected) => panic!("reader gone"),
        }
    }

    /// Reads messages until `pred` matches; returns all seen.
    fn until(&mut self, mut pred: impl FnMut(&Value) -> bool) -> Vec<Value> {
        let mut seen = Vec::new();
        loop {
            let value = self.next();
            let done = pred(&value);
            seen.push(value);
            if done {
                return seen;
            }
        }
    }

    /// True once stdout has hit EOF (without consuming further messages).
    fn expect_stdout_eof(&mut self) {
        match self.frames.recv_timeout(WAIT) {
            Ok(None) => self.stdout_closed = true,
            Ok(Some(extra)) => panic!("unexpected extra message before EOF: {extra}"),
            Err(_) => panic!("stdout did not close"),
        }
    }

    fn wait_exit(&mut self, within: Duration) -> i32 {
        let deadline = Instant::now() + within;
        loop {
            if let Some(status) = self.child.try_wait().unwrap() {
                return status.code().expect("exit code");
            }
            if Instant::now() > deadline {
                let _ = self.child.kill();
                panic!("host did not exit within {within:?}");
            }
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn running(&mut self) -> bool {
        self.child.try_wait().unwrap().is_none()
    }

    fn close_stdin(&mut self) {
        self.stdin = None;
    }
}

impl Drop for Host {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn b64(len: usize, seed: u8) -> String {
    base64::encode(&common::synthetic(len, seed))
}

// ---- launch, framing and exit codes -------------------------------------------------------------

#[test]
fn hello_returns_ready_and_stdin_eof_exits_zero_closing_stdout() {
    let mut host = Host::spawn();
    let ready = host.hello();
    assert_eq!(ready["type"], "ready");
    assert_eq!(ready["api_version"], 1);
    assert_eq!(ready["platform"], "windows");
    assert_eq!(ready["capabilities"], json!(["veles_record_v1"]));
    assert!(
        ready["bridge_version"]
            .as_str()
            .unwrap()
            .contains(flavour().as_str())
    );
    assert!(host.running());
    host.close_stdin(); // Chrome port closure
    host.expect_stdout_eof();
    assert_eq!(host.wait_exit(WAIT), 0);
}

#[test]
fn wrong_or_missing_origin_argument_refuses_to_start() {
    for args in [
        &[][..],
        &["chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/"][..],
        &["--backend", "sim"][..],
    ] {
        let mut host = Host::spawn_with(args);
        host.expect_stdout_eof();
        assert_eq!(host.wait_exit(WAIT), 2, "{args:?}");
    }
}

#[test]
fn shutdown_completes_and_exits_while_stdin_stays_open_and_idle() {
    let mut host = Host::spawn();
    host.hello();
    host.req("shutdown", "bye", json!({}));
    let done = host.next();
    assert_eq!(
        (done["type"].as_str(), done["request_id"].as_str()),
        (Some("shutdown_complete"), Some("bye"))
    );
    host.expect_stdout_eof();
    // stdin is still open and idle here: the exit must not depend on the reader thread.
    assert!(host.stdin.is_some());
    assert_eq!(host.wait_exit(Duration::from_secs(5)), 0);
}

#[test]
fn oversize_zero_and_truncated_frames_fail_closed_with_exit_2_and_no_output() {
    let cases: Vec<(&str, Vec<u8>, bool)> = vec![
        ("oversize header", 65_537u32.to_le_bytes().to_vec(), false),
        ("4 GiB header", u32::MAX.to_le_bytes().to_vec(), false),
        ("zero length", 0u32.to_le_bytes().to_vec(), false),
        ("truncated header then EOF", vec![5, 0], true),
        (
            "truncated payload then EOF",
            [10u32.to_le_bytes().to_vec(), b"abcd".to_vec()].concat(),
            true,
        ),
    ];
    for (name, bytes, close) in cases {
        let mut host = Host::spawn();
        host.hello();
        host.send_bytes(&bytes);
        if close {
            host.close_stdin();
        }
        host.expect_stdout_eof();
        assert_eq!(host.wait_exit(WAIT), 2, "{name}");
    }
}

#[test]
fn malformed_input_before_and_after_ready_exits_2_silently() {
    for payload in [
        &b"not json"[..],
        br#"{"envelope_version":2,"type":"hello","request_id":"h"}"#,
        b"{}",
    ] {
        let mut host = Host::spawn();
        host.send_raw_frame(payload);
        host.expect_stdout_eof();
        assert_eq!(
            host.wait_exit(WAIT),
            2,
            "pre-ready {}",
            String::from_utf8_lossy(payload)
        );
    }
    let mut host = Host::spawn();
    host.hello();
    host.send_raw_frame(b"{ broken");
    host.expect_stdout_eof();
    assert_eq!(host.wait_exit(WAIT), 2, "post-ready");
}

#[test]
fn pre_ready_request_gets_one_correlated_error_then_exit_2() {
    let mut host = Host::spawn();
    host.send(json!({"envelope_version":1,"type":"start_scan","request_id":"s1","service_uuid":SCAN_UUID}));
    let error = host.next();
    assert_eq!(
        (error["code"].as_str(), error["stage"].as_str()),
        (Some("invalid_request"), Some("pre_ready"))
    );
    host.expect_stdout_eof();
    assert_eq!(host.wait_exit(WAIT), 2);
}

#[test]
fn incompatible_api_flushes_the_error_then_exits_3() {
    let mut host = Host::spawn();
    host.send(json!({"envelope_version":1,"type":"hello","request_id":"h","api_versions":[2]}));
    let error = host.next();
    assert_eq!(error["code"], "incompatible_api");
    assert!(error.get("api_version").is_none());
    host.expect_stdout_eof();
    assert_eq!(host.wait_exit(WAIT), 3);
}

#[test]
fn a_frame_delivered_in_single_byte_writes_is_reassembled() {
    let mut host = Host::spawn();
    let payload = serde_json::to_vec(
        &json!({"envelope_version":1,"type":"hello","request_id":"slow","api_versions":[1]}),
    )
    .unwrap();
    let mut frame = (payload.len() as u32).to_le_bytes().to_vec();
    frame.extend(&payload);
    for byte in frame {
        host.send_bytes(&[byte]);
    }
    assert_eq!(host.next()["request_id"], "slow");
}

// ---- simulated phones: scan, connect, records, isolation --------------------------------------------

fn connect_both(host: &mut Host) -> (String, String) {
    host.hello();
    host.req("start_scan", "scan", json!({"service_uuid": SCAN_UUID}));
    let seen = host.until({
        let mut devices = 0;
        move |v| {
            devices += usize::from(v["type"] == "device_found");
            devices == 2
        }
    });
    assert_eq!(
        seen[0]["type"], "scan_state",
        "started precedes every device_found"
    );
    let devices: Vec<&Value> = seen
        .iter()
        .filter(|v| v["type"] == "device_found")
        .collect();
    let mut handles = Vec::new();
    for (index, device) in devices.iter().enumerate() {
        host.req(
            "connect",
            &format!("c{index}"),
            json!({"device_handle": device["device_handle"]}),
        );
        let connected = host.until(|v| v["type"] == "connected" || v["type"] == "error");
        let connected = connected.last().unwrap().clone();
        assert_eq!(connected["type"], "connected", "{connected}");
        handles.push(connected["connection_handle"].as_str().unwrap().to_string());
    }
    (handles[0].clone(), handles[1].clone())
}

#[test]
fn two_independent_phones_exchange_complete_records_and_never_cross() {
    let mut host = Host::spawn();
    let (a, b) = connect_both(&mut host);
    assert_ne!(a, b);
    // The synthetic OTP-06 fixture: VEL\x01 -> VEL\x02, on both phones at once.
    let fixture = base64::encode(&[0x56, 0x45, 0x4c, 0x01]);
    host.req(
        "send",
        "fa",
        json!({"connection_handle": a, "record_base64": fixture}),
    );
    host.req(
        "send",
        "fb",
        json!({"connection_handle": b, "record_base64": fixture}),
    );
    let mut sent = 0;
    let mut messages = Vec::new();
    while sent < 2 || messages.len() < 2 {
        let event = host.next();
        match event["type"].as_str().unwrap() {
            "sent" => sent += 1,
            "message" => messages.push(event),
            other => panic!("unexpected {other}"),
        }
    }
    assert!(messages.iter().all(|m| m["record_base64"] == "VkVMAg=="));
    let mut handles: Vec<_> = messages
        .iter()
        .map(|m| m["connection_handle"].as_str().unwrap().to_string())
        .collect();
    handles.sort();
    let mut expected = vec![a.clone(), b.clone()];
    expected.sort();
    assert_eq!(
        handles, expected,
        "one response per phone, each on its own handle"
    );

    // Distinct large payloads at the boundaries, echoed per phone: bytes never cross.
    for (len, seed) in [(4usize, 1u8), (178, 2), (179, 3), (4096, 4)] {
        let mut want = std::collections::HashMap::new();
        for (handle, tag) in [(&a, 0x10u8), (&b, 0x80u8)] {
            let mut record = b"ECHO".to_vec();
            record.extend(common::synthetic(len - 4, seed.wrapping_add(tag)));
            want.insert(handle.clone(), record.clone());
            host.req(
                "send",
                &format!("e{len}{tag}"),
                json!({"connection_handle": handle, "record_base64": base64::encode(&record)}),
            );
        }
        let mut got = std::collections::HashMap::new();
        let mut sent = 0;
        while got.len() < 2 || sent < 2 {
            let event = host.next();
            match event["type"].as_str().unwrap() {
                "sent" => sent += 1,
                "message" => {
                    let bytes =
                        base64::decode(event["record_base64"].as_str().unwrap(), 5120).unwrap();
                    got.insert(
                        event["connection_handle"].as_str().unwrap().to_string(),
                        bytes,
                    );
                }
                other => panic!("unexpected {other}: {event}"),
            }
        }
        assert_eq!(got, want, "length {len}");
    }
}

#[test]
fn record_size_boundaries_over_the_pipe() {
    let mut host = Host::spawn();
    let (a, _) = connect_both(&mut host);
    host.req(
        "send",
        "empty",
        json!({"connection_handle": a, "record_base64": ""}),
    );
    host.req(
        "send",
        "big",
        json!({"connection_handle": a, "record_base64": b64(4097, 1)}),
    );
    host.req(
        "send",
        "huge",
        json!({"connection_handle": a, "record_base64": b64(5121, 1)}),
    );
    host.req(
        "send",
        "noncanonical",
        json!({"connection_handle": a, "record_base64": "Zh=="}),
    );
    let codes: Vec<(String, String)> = (0..4)
        .map(|_| {
            let e = host.next();
            (
                e["request_id"].as_str().unwrap().to_string(),
                e["code"].as_str().unwrap().to_string(),
            )
        })
        .collect();
    assert_eq!(
        codes,
        vec![
            ("empty".into(), "invalid_base64".into()),
            ("big".into(), "oversize_record".into()),
            ("huge".into(), "oversize_record".into()),
            ("noncanonical".into(), "invalid_base64".into()),
        ]
    );
    // Exactly 4096 goes through (covered by the ECHO round trip above); 1 byte also works.
    host.req(
        "send",
        "one",
        json!({"connection_handle": a, "record_base64": b64(1, 9)}),
    );
    assert_eq!(host.next()["type"], "sent");
}

#[test]
fn disconnect_releases_one_phone_while_the_other_keeps_working() {
    let mut host = Host::spawn();
    let (a, b) = connect_both(&mut host);
    host.req("disconnect", "da", json!({"connection_handle": a}));
    let event = host.next();
    assert_eq!(
        (event["type"].as_str(), event["request_id"].as_str()),
        (Some("disconnected"), Some("da"))
    );
    host.req(
        "send",
        "late",
        json!({"connection_handle": a, "record_base64": b64(8, 1)}),
    );
    assert_eq!(host.next()["code"], "unknown_handle");
    host.req(
        "send",
        "fb",
        json!({"connection_handle": b, "record_base64": base64::encode(&[0x56, 0x45, 0x4c, 0x01])}),
    );
    let events = host.until(|v| v["type"] == "message");
    assert!(events.iter().any(|v| v["type"] == "sent"));
    assert_eq!(events.last().unwrap()["connection_handle"], b);
}

#[test]
fn link_loss_is_reported_for_one_handle_only() {
    let mut host = Host::spawn();
    let (a, b) = connect_both(&mut host);
    host.req(
        "send",
        "drop",
        json!({"connection_handle": a, "record_base64": base64::encode(b"DROP")}),
    );
    let events = host.until(|v| v["type"] == "disconnected");
    assert_eq!(events.last().unwrap()["connection_handle"], a);
    assert!(events.last().unwrap().get("request_id").is_none());
    host.req(
        "send",
        "fb",
        json!({"connection_handle": b, "record_base64": base64::encode(&[0x56, 0x45, 0x4c, 0x01])}),
    );
    host.until(|v| v["type"] == "message");
}

#[test]
fn eof_with_active_scan_and_connections_exits_zero_and_leaves_no_process() {
    let mut host = Host::spawn();
    let (_a, _b) = connect_both(&mut host);
    host.close_stdin();
    host.expect_stdout_eof();
    assert_eq!(host.wait_exit(WAIT), 0);
}

#[test]
fn shutdown_with_open_links_orders_lifecycle_before_shutdown_complete() {
    let mut host = Host::spawn();
    let (a, b) = connect_both(&mut host);
    host.req("shutdown", "bye", json!({}));
    let events = host.until(|v| v["type"] == "shutdown_complete");
    let kinds: Vec<_> = events.iter().map(|e| e["type"].as_str().unwrap()).collect();
    assert_eq!(
        kinds.iter().filter(|k| **k == "disconnected").count(),
        2,
        "{kinds:?}"
    );
    assert!(kinds.contains(&"scan_state"));
    let closed: Vec<_> = events
        .iter()
        .filter(|e| e["type"] == "disconnected")
        .map(|e| e["connection_handle"].as_str().unwrap())
        .collect();
    assert!(closed.contains(&a.as_str()) && closed.contains(&b.as_str()));
    host.expect_stdout_eof();
    assert_eq!(host.wait_exit(WAIT), 0);
}
