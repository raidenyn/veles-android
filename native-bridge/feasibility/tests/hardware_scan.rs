//! Real WinRT backend through the compiled host over pipes (no Chrome). Needs a Bluetooth LE
//! radio, so these are `#[ignore]`d: run with `cargo test --test hardware_scan -- --ignored`.
//! They assert protocol behaviour and cleanup; whether nearby phones exist is only reported.

use std::io::Write;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use veles_otp23_feasibility::framing::{read_frame, write_frame};
use veles_otp23_feasibility::identity::EXPECTED_ORIGIN;

const VELES_SERVICE: &str = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51";

struct Real {
    child: Child,
    stdin: Option<ChildStdin>,
    frames: Receiver<Option<Value>>,
}

impl Real {
    fn spawn(diagnostic_any: bool) -> Real {
        let mut command = Command::new(env!("CARGO_BIN_EXE_otp23-host"));
        command
            .arg(EXPECTED_ORIGIN)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        if diagnostic_any {
            command.env("VELES_OTP23_DIAG_SCAN_ANY", "1");
        }
        let mut child = command.spawn().expect("spawn host");
        let mut stdout = child.stdout.take().unwrap();
        let (tx, frames) = mpsc::channel();
        thread::spawn(move || {
            while let Ok(Some(bytes)) = read_frame(&mut stdout) {
                let _ = tx.send(Some(serde_json::from_slice(&bytes).unwrap()));
            }
            let _ = tx.send(None);
        });
        let stdin = child.stdin.take();
        Real {
            child,
            stdin,
            frames,
        }
    }

    fn send(&mut self, value: Value) {
        let stdin = self.stdin.as_mut().unwrap();
        write_frame(stdin, &serde_json::to_vec(&value).unwrap()).unwrap();
        stdin.flush().unwrap();
    }

    fn req(&mut self, kind: &str, id: &str, extra: Value) {
        let mut value = json!({"envelope_version":1,"api_version":1,"type":kind,"request_id":id});
        for (k, v) in extra.as_object().unwrap() {
            value[k] = v.clone();
        }
        self.send(value);
    }

    fn next(&mut self, within: Duration) -> Option<Value> {
        self.frames.recv_timeout(within).ok().flatten()
    }

    fn hello(&mut self) {
        self.send(
            json!({"envelope_version":1,"type":"hello","request_id":"hello","api_versions":[1]}),
        );
        let ready = self.next(Duration::from_secs(10)).expect("ready");
        assert_eq!(ready["type"], "ready");
        assert_eq!(ready["platform"], "windows");
    }

    fn wait_exit(&mut self, within: Duration) -> i32 {
        let deadline = Instant::now() + within;
        loop {
            if let Some(status) = self.child.try_wait().unwrap() {
                return status.code().unwrap();
            }
            assert!(Instant::now() < deadline, "host did not exit");
            thread::sleep(Duration::from_millis(20));
        }
    }
}

impl Drop for Real {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn start(real: &mut Real, id: &str) -> Value {
    real.req("start_scan", id, json!({"service_uuid": VELES_SERVICE}));
    real.next(Duration::from_secs(15))
        .expect("a scan completion")
}

#[test]
#[ignore = "needs a Bluetooth LE radio"]
fn real_scan_starts_stops_and_shutdown_exits_with_idle_stdin() {
    let mut host = Real::spawn(false);
    host.hello();
    let started = start(&mut host, "scan");
    assert_eq!(
        (started["type"].as_str(), started["status"].as_str()),
        (Some("scan_state"), Some("started")),
        "{started}"
    );
    let scan = started["scan_handle"].as_str().unwrap().to_string();
    let mut found = 0;
    let until = Instant::now() + Duration::from_secs(4);
    while Instant::now() < until {
        if let Some(event) = host.next(Duration::from_millis(250)) {
            assert_eq!(event["type"], "device_found", "{event}");
            assert_eq!(event["scan_handle"], scan);
            found += 1;
        }
    }
    println!("REAL-SCAN veles_service_devices_seen={found}");
    host.req("stop_scan", "stop", json!({"scan_handle": scan}));
    let stopped = host.next(Duration::from_secs(5)).unwrap();
    assert_eq!(
        (stopped["status"].as_str(), stopped["request_id"].as_str()),
        (Some("stopped"), Some("stop"))
    );
    host.req("shutdown", "bye", json!({}));
    assert_eq!(
        host.next(Duration::from_secs(5)).unwrap()["type"],
        "shutdown_complete"
    );
    assert!(
        host.stdin.is_some(),
        "stdin intentionally left open and idle"
    );
    assert_eq!(host.wait_exit(Duration::from_secs(5)), 0);
}

#[test]
#[ignore = "needs a Bluetooth LE radio"]
fn real_scan_is_released_when_stdin_closes() {
    let mut host = Real::spawn(false);
    host.hello();
    assert_eq!(start(&mut host, "scan")["status"], "started");
    host.stdin = None; // Chrome port closure
    assert_eq!(host.wait_exit(Duration::from_secs(5)), 0);
}

#[test]
#[ignore = "needs a Bluetooth LE radio"]
fn real_diagnostic_scan_reports_bounded_device_events() {
    let mut host = Real::spawn(true);
    host.hello();
    assert_eq!(start(&mut host, "scan")["status"], "started");
    let mut found = Vec::new();
    let until = Instant::now() + Duration::from_secs(8);
    while Instant::now() < until {
        if let Some(event) = host.next(Duration::from_millis(250)) {
            assert_eq!(event["type"], "device_found", "{event}");
            let label = event["label"].as_str().unwrap();
            assert!((1..=64).contains(&label.len()));
            let hint = event["platform_hint"].as_str().unwrap();
            assert!(hint.starts_with("winrt:") && hint.len() <= 256);
            assert!(event["device_handle"].as_str().unwrap().len() == 16);
            found.push(event);
        }
    }
    println!("REAL-SCAN diagnostic_any devices_seen={}", found.len());
    assert!(
        found.len() <= 64 * 9,
        "rate bound: at most 64 per rolling second"
    );
    let unique: std::collections::HashSet<_> = found
        .iter()
        .map(|e| e["platform_hint"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(
        unique.len(),
        found.len(),
        "one device_found per device per scan"
    );
    host.stdin = None;
    assert_eq!(host.wait_exit(Duration::from_secs(5)), 0);
}

#[test]
#[ignore = "needs a Bluetooth LE radio"]
fn connecting_to_an_absent_device_fails_closed_and_classified() {
    let mut host = Real::spawn(false);
    host.hello();
    host.req(
        "connect",
        "c1",
        json!({"platform_hint": "winrt:020000000001:1"}),
    );
    let event = host.next(Duration::from_secs(40)).expect("a completion");
    println!("REAL-CONNECT absent device -> {event}");
    assert_eq!(event["type"], "error");
    assert_eq!(event["request_id"], "c1");
    assert_eq!(event["code"], "connect_failed");
    host.req("shutdown", "bye", json!({}));
    assert_eq!(
        host.next(Duration::from_secs(5)).unwrap()["type"],
        "shutdown_complete"
    );
}
