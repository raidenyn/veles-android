//! OTP-24 whole-change review findings I1 and I2, driven through the *real* `runtime::run`
//! loop with live threads: stdin is an idle channel-fed pipe (the normal state), the "radio" is
//! a test thread that emits through the real `BackendEvents` producer, and the Chrome side is a
//! frame-parsing channel writer. Nothing is mocked inside the host.
//!
//! - I2: an idle core must wake on backend events immediately (not after the 100 ms stdin
//!   poll), so a full legal 4,096-byte record arriving at radio pace at MTU 185 (24 fragments)
//!   or MTU 247 (18) stays inside the 16-event per-link ingress bound and is delivered.
//! - I1: monotonic deadlines are serviced while input and backend traffic never go idle — a
//!   stalled send on one phone times out although another phone floods garbage forever.

use std::io::{self, Read, Write};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use veles_ble_host::backend::{Backend, BackendEvent, Discovered, NormalizedTarget, Target, Token};
use veles_ble_host::buffer::Payload;
use veles_ble_host::fragment::{LinkBudget, LinkCapacity, fragment};
use veles_ble_host::ingress::BackendEvents;
use veles_ble_host::runtime::{Config, HangWatchdog, Io, run};

const SCAN_UUID: &str = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51";

struct NoWatchdog;
impl HangWatchdog for NoWatchdog {
    fn arm(&self, _deadline: Instant) {}
    fn disarm(&self) {}
}

#[derive(Debug)]
enum Call {
    StartScan(Token),
    Connect(Token),
    Write(Token, Token),
}

/// A deterministic core stall: the next `write_fragments` call blocks *inside*
/// `Core::handle` (the core thread cannot drain the ingress) until the test releases it.
struct Stall {
    state: Mutex<(bool, bool, bool)>, // (armed, entered, released)
    wake: Condvar,
}

impl Stall {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            state: Mutex::new((false, false, false)),
            wake: Condvar::new(),
        })
    }

    fn arm(&self) {
        *self.state.lock().unwrap() = (true, false, false);
    }

    /// Called from the backend on the core thread.
    fn block_if_armed(&self) {
        let mut state = self.state.lock().unwrap();
        if !state.0 {
            return;
        }
        state.1 = true;
        self.wake.notify_all();
        while !state.2 {
            state = self.wake.wait(state).unwrap();
        }
        state.0 = false;
    }

    fn wait_entered(&self) {
        let mut state = self.state.lock().unwrap();
        let deadline = Instant::now() + Duration::from_secs(10);
        while !state.1 {
            let left = deadline
                .checked_duration_since(Instant::now())
                .expect("the core never entered the stalled backend call");
            state = self.wake.wait_timeout(state, left).unwrap().0;
        }
    }

    fn release(&self) {
        self.state.lock().unwrap().2 = true;
        self.wake.notify_all();
    }
}

struct LiveBackend {
    events: BackendEvents,
    calls: Sender<Call>,
    stall: Arc<Stall>,
}

impl Backend for LiveBackend {
    fn normalize_target(&self, hint: &str) -> Option<NormalizedTarget> {
        Some(NormalizedTarget::new(hint.to_string()))
    }
    fn start_scan(&mut self, scan: Token, _uuid: &str) {
        let _ = self.calls.send(Call::StartScan(scan));
    }
    fn stop_scan(&mut self, _scan: Token) {}
    fn connect(&mut self, conn: Token, _target: &Target) {
        let _ = self.calls.send(Call::Connect(conn));
    }
    fn write_fragments(&mut self, conn: Token, send: Token, _frames: Vec<Payload>) {
        let _ = self.calls.send(Call::Write(conn, send));
        self.stall.block_if_armed();
    }
    fn disconnect(&mut self, _conn: Token) {}
    fn shutdown(&mut self) {
        self.events.emit(BackendEvent::ShutdownComplete);
    }
}

struct ChanRead {
    rx: Receiver<Vec<u8>>,
    buf: Vec<u8>,
    pos: usize,
}

impl Read for ChanRead {
    fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
        if self.pos == self.buf.len() {
            match self.rx.recv() {
                Ok(bytes) => {
                    self.buf = bytes;
                    self.pos = 0;
                }
                Err(_) => return Ok(0),
            }
        }
        let n = out.len().min(self.buf.len() - self.pos);
        out[..n].copy_from_slice(&self.buf[self.pos..self.pos + n]);
        self.pos += n;
        Ok(n)
    }
}

struct ChanWrite {
    tx: Sender<Value>,
    pending: Vec<u8>,
}

impl Write for ChanWrite {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.pending.extend_from_slice(buf);
        while self.pending.len() >= 4 {
            let len = u32::from_le_bytes(self.pending[..4].try_into().unwrap()) as usize;
            if self.pending.len() < 4 + len {
                break;
            }
            let value: Value = serde_json::from_slice(&self.pending[4..4 + len]).unwrap();
            self.pending.drain(..4 + len);
            let _ = self.tx.send(value);
        }
        Ok(buf.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

struct Live {
    stdin: Option<Sender<Vec<u8>>>,
    out: Receiver<Value>,
    calls: Receiver<Call>,
    events: BackendEvents,
    done: Receiver<i32>,
    scan: Token,
    stall: Arc<Stall>,
}

fn framed(value: &Value) -> Vec<u8> {
    let payload = serde_json::to_vec(value).unwrap();
    let mut frame = (payload.len() as u32).to_le_bytes().to_vec();
    frame.extend_from_slice(&payload);
    frame
}

impl Live {
    fn start() -> Self {
        let (stdin_tx, stdin_rx) = mpsc::channel();
        let (out_tx, out_rx) = mpsc::channel();
        let (call_tx, call_rx) = mpsc::channel();
        let (events_tx, events_rx) = mpsc::channel();
        let (done_tx, done_rx) = mpsc::channel();
        let stall = Stall::new();
        let backend_stall = Arc::clone(&stall);
        thread::spawn(move || {
            let code = run(
                move |events| {
                    events_tx.send(events.clone()).unwrap();
                    LiveBackend {
                        events,
                        calls: call_tx,
                        stall: backend_stall,
                    }
                },
                Io {
                    stdin: Box::new(ChanRead {
                        rx: stdin_rx,
                        buf: Vec::new(),
                        pos: 0,
                    }),
                    stdout: Box::new(ChanWrite {
                        tx: out_tx,
                        pending: Vec::new(),
                    }),
                },
                Config {
                    bridge_version: "live/0".into(),
                    platform: "test",
                    watchdog: Arc::new(NoWatchdog),
                },
            );
            let _ = done_tx.send(code);
        });
        let events = events_rx.recv().unwrap();
        let mut live = Self {
            stdin: Some(stdin_tx),
            out: out_rx,
            calls: call_rx,
            events,
            done: done_rx,
            scan: 0,
            stall,
        };
        live.send(
            &json!({"envelope_version":1,"type":"hello","request_id":"hello","api_versions":[1]}),
        );
        assert_eq!(live.next_type("ready")["type"], "ready");
        live.send(
            &json!({"envelope_version":1,"api_version":1,"type":"start_scan",
            "request_id":"scan","service_uuid":SCAN_UUID}),
        );
        match live.calls.recv_timeout(Duration::from_secs(5)).unwrap() {
            Call::StartScan(scan) => live.scan = scan,
            other => panic!("{other:?}"),
        }
        live.events
            .emit(BackendEvent::ScanStarted { scan: live.scan });
        live.next_type("scan_state");
        live
    }

    fn send(&self, value: &Value) {
        self.stdin.as_ref().unwrap().send(framed(value)).unwrap();
    }

    /// The next frame of `kind`, skipping none: any other frame is a test failure.
    fn next_type(&self, kind: &str) -> Value {
        let frame = self
            .out
            .recv_timeout(Duration::from_secs(10))
            .unwrap_or_else(|_| panic!("timed out waiting for {kind}"));
        assert_eq!(frame["type"], kind, "unexpected frame {frame}");
        frame
    }

    /// Discover, then connect one phone at the given ATT MTU; returns (handle, conn token).
    fn connect_phone(&self, hint: &str, mtu: usize) -> (String, Token) {
        self.events.emit(BackendEvent::Advertisement {
            scan: self.scan,
            device: Discovered {
                platform_hint: hint.into(),
                label: hint.into(),
                advertisement: None,
            },
        });
        let found = self.next_type("device_found");
        self.send(
            &json!({"envelope_version":1,"api_version":1,"type":"connect",
            "request_id":format!("c-{hint}"),"device_handle":found["device_handle"]}),
        );
        let conn = match self.calls.recv_timeout(Duration::from_secs(5)).unwrap() {
            Call::Connect(conn) => conn,
            other => panic!("{other:?}"),
        };
        self.events.emit(BackendEvent::Connected {
            conn,
            capacity: LinkCapacity::AttMtu(mtu),
            platform_hint: None,
        });
        let connected = self.next_type("connected");
        (
            connected["connection_handle"].as_str().unwrap().to_string(),
            conn,
        )
    }
}

fn record_of(len: usize, seed: u8) -> Vec<u8> {
    (0..len)
        .map(|i| (i as u8).wrapping_mul(31).wrapping_add(seed))
        .collect()
}

/// Emits `record` as the radio would: groups of `per_burst` notifications every `gap`.
fn radio_deliver(
    events: &BackendEvents,
    conn: Token,
    record: &[u8],
    mtu: usize,
    id: u8,
    per_burst: usize,
    gap: Duration,
) {
    let budget = LinkBudget::from_att_mtu(mtu).unwrap();
    let frames = fragment(record, budget, id).unwrap();
    for (n, frame) in frames.into_iter().enumerate() {
        events.emit(BackendEvent::Fragment { conn, bytes: frame });
        if (n + 1) % per_burst == 0 {
            thread::sleep(gap);
        }
    }
}

fn expect_record(live: &Live, handle: &str, record: &[u8]) {
    let frame = live
        .out
        .recv_timeout(Duration::from_secs(10))
        .expect("record delivery");
    assert_eq!(
        frame["type"], "message",
        "the link must survive a legal record, got {frame}"
    );
    assert_eq!(frame["connection_handle"], handle);
    assert_eq!(
        frame["record_base64"],
        veles_ble_host::base64::encode(record)
    );
}

fn finish(live: Live) {
    drop(live.stdin);
    let code = live.done.recv_timeout(Duration::from_secs(10)).unwrap();
    assert_eq!(code, 0);
}

/// I2: a legal full 4,096-byte record at radio pace, stdin idle, at the smallest MTU.
#[test]
fn full_record_at_mtu_185_is_delivered_with_an_idle_stdin() {
    let live = Live::start();
    let (handle, conn) = live.connect_phone("phone-a", 185);
    let record = record_of(4096, 1);
    // 24 fragments; the radio delivers four notifications per ~2 ms connection event.
    radio_deliver(
        &live.events,
        conn,
        &record,
        185,
        1,
        4,
        Duration::from_millis(2),
    );
    expect_record(&live, &handle, &record);
    finish(live);
}

#[test]
fn full_records_at_mtu_247_back_to_back_are_delivered_in_order() {
    let live = Live::start();
    let (handle, conn) = live.connect_phone("phone-a", 247);
    let first = record_of(4096, 2);
    let second = record_of(4096, 3);
    radio_deliver(
        &live.events,
        conn,
        &first,
        247,
        1,
        4,
        Duration::from_millis(2),
    );
    radio_deliver(
        &live.events,
        conn,
        &second,
        247,
        2,
        4,
        Duration::from_millis(2),
    );
    expect_record(&live, &handle, &first);
    expect_record(&live, &handle, &second);
    finish(live);
}

/// I2 (isolation): two phones receive full records at the same time; neither is disturbed.
#[test]
fn two_phones_receive_full_records_concurrently_without_loss() {
    let live = Live::start();
    let (handle_a, conn_a) = live.connect_phone("phone-a", 185);
    let (handle_b, conn_b) = live.connect_phone("phone-b", 247);
    let rec_a = record_of(4096, 4);
    let rec_b = record_of(4096, 5);
    let ev_a = live.events.clone();
    let ev_b = live.events.clone();
    let (ra, rb) = (rec_a.clone(), rec_b.clone());
    let ta = thread::spawn(move || {
        radio_deliver(&ev_a, conn_a, &ra, 185, 1, 3, Duration::from_millis(2))
    });
    let tb = thread::spawn(move || {
        radio_deliver(&ev_b, conn_b, &rb, 247, 1, 3, Duration::from_millis(2))
    });
    ta.join().unwrap();
    tb.join().unwrap();
    let mut seen = Vec::new();
    for _ in 0..2 {
        let frame = live.out.recv_timeout(Duration::from_secs(10)).unwrap();
        assert_eq!(frame["type"], "message", "{frame}");
        seen.push((
            frame["connection_handle"].as_str().unwrap().to_string(),
            frame["record_base64"].as_str().unwrap().to_string(),
        ));
    }
    seen.sort();
    let mut expected = vec![
        (handle_a, veles_ble_host::base64::encode(&rec_a)),
        (handle_b, veles_ble_host::base64::encode(&rec_b)),
    ];
    expected.sort();
    assert_eq!(seen, expected);
    finish(live);
}

/// I2 (wake latency): an idle core must react to a backend event in far less than the 100 ms
/// poll bound — a write completion must not wait for it either.
#[test]
fn idle_core_wakes_on_backend_events_without_waiting_for_stdin_polling() {
    let live = Live::start();
    let (handle, conn) = live.connect_phone("phone-a", 185);
    let mut worst = Duration::ZERO;
    for round in 0..16u8 {
        live.send(&json!({"envelope_version":1,"api_version":1,"type":"send",
            "request_id":format!("s{round}"),"connection_handle":handle,
            "record_base64":veles_ble_host::base64::encode(&record_of(8, round))}));
        let send = match live.calls.recv_timeout(Duration::from_secs(5)).unwrap() {
            Call::Write(c, s) => {
                assert_eq!(c, conn);
                s
            }
            other => panic!("{other:?}"),
        };
        // Let the core go fully idle, at a different phase of its (formerly 100 ms) poll
        // cycle each round, so a stdin-only wait cannot hide behind a lucky phase.
        thread::sleep(Duration::from_millis(120 + u64::from(round) * 7));
        let t = Instant::now();
        live.events.emit(BackendEvent::WriteDone { conn, send });
        let sent = live.next_type("sent");
        assert_eq!(sent["request_id"], format!("s{round}"));
        worst = worst.max(t.elapsed());
    }
    eprintln!("worst idle backend latency: {worst:?}");
    assert!(
        worst < Duration::from_millis(60),
        "backend event latency while idle: {worst:?}"
    );
    finish(live);
}

/// I1: phone A floods garbage (a fragment every ~2 ms, never idle for a whole poll cycle) while
/// phone B's send stalls with no completion. B's request must still time out (~30 s), exactly
/// once, and phone A's link is unaffected by B's timeout.
#[test]
fn stalled_send_times_out_under_continuous_backend_traffic() {
    let live = Live::start();
    let (handle_a, conn_a) = live.connect_phone("phone-a", 185);
    let (handle_b, conn_b) = live.connect_phone("phone-b", 185);
    live.send(&json!({"envelope_version":1,"api_version":1,"type":"send",
        "request_id":"stalled","connection_handle":handle_b,
        "record_base64":veles_ble_host::base64::encode(&record_of(8, 9))}));
    match live.calls.recv_timeout(Duration::from_secs(5)).unwrap() {
        Call::Write(c, _) => assert_eq!(c, conn_b),
        other => panic!("{other:?}"),
    }
    let started = Instant::now();
    let stop = Arc::new(Mutex::new(false));
    let flood = {
        let events = live.events.clone();
        let stop = Arc::clone(&stop);
        thread::spawn(move || {
            // Garbage: a continuation fragment with no live transaction (Ignored/Discarded).
            while !*stop.lock().unwrap() {
                events.emit(BackendEvent::Fragment {
                    conn: conn_a,
                    bytes: Payload::new(vec![0x01, 0x07, 0x00, 0x00, 0xEE]),
                });
                thread::sleep(Duration::from_millis(2));
            }
        })
    };
    // Frames of phone A's garbage produce no output; B's timeout does.
    let timeout = loop {
        let frame = live
            .out
            .recv_timeout(Duration::from_secs(45))
            .expect("the stalled send never timed out under continuous traffic");
        if frame["type"] == "error" && frame["code"] == "timeout" {
            break frame;
        }
    };
    let elapsed = started.elapsed();
    *stop.lock().unwrap() = true;
    flood.join().unwrap();
    assert_eq!(timeout["request_id"], "stalled");
    assert!(
        elapsed >= Duration::from_secs(29) && elapsed < Duration::from_secs(33),
        "timeout fired after {elapsed:?}"
    );
    // Phone A is isolated from B's timeout teardown.
    let record = record_of(64, 7);
    radio_deliver(
        &live.events,
        conn_a,
        &record,
        185,
        3,
        4,
        Duration::from_millis(1),
    );
    loop {
        let frame = live.out.recv_timeout(Duration::from_secs(10)).unwrap();
        if frame["type"] == "message" {
            assert_eq!(frame["connection_handle"], handle_a);
            assert_eq!(
                frame["record_base64"],
                veles_ble_host::base64::encode(&record)
            );
            break;
        }
        assert_ne!(
            frame["connection_handle"], handle_a,
            "phone A must not be torn down: {frame}"
        );
    }
    finish(live);
}

// ---- review follow-up: approved 48-event / 16 KiB per-link ingress bound -----------------------

/// Everything the "other link" and the stalled link need: connect three phones at MTU 185.
fn three_phones(live: &Live) -> [(String, Token); 3] {
    [
        live.connect_phone("phone-a", 185),
        live.connect_phone("phone-b", 185),
        live.connect_phone("phone-stall", 185),
    ]
}

/// Parks the core thread inside the backend (a `send` whose `write_fragments` blocks) so no
/// ingress event can be drained until [`Stall::release`].
fn stall_core(live: &Live, handle: &str, conn: Token) {
    live.stall.arm();
    live.send(&json!({"envelope_version":1,"api_version":1,"type":"send",
        "request_id":"stall","connection_handle":handle,
        "record_base64":veles_ble_host::base64::encode(&record_of(8, 0))}));
    match live.calls.recv_timeout(Duration::from_secs(5)).unwrap() {
        Call::Write(c, _) => assert_eq!(c, conn),
        other => panic!("{other:?}"),
    }
    live.stall.wait_entered();
}

/// The core is stalled; two full legal 4,096-byte records arrive *zero-gap* at MTU 185 on link A
/// (2 x 24 = 48 fragments: exactly the per-link bound) while link B receives the same
/// concurrently. Nothing may be lost, crossed over or reordered, and the bytes are exact.
#[test]
fn stalled_core_then_zero_gap_two_max_records_at_mtu_185_on_two_links_concurrently() {
    let live = Live::start();
    let [(handle_a, conn_a), (handle_b, conn_b), (handle_s, conn_s)] = three_phones(&live);
    stall_core(&live, &handle_s, conn_s);

    let rec_a = [record_of(4096, 11), record_of(4096, 12)];
    let rec_b = [record_of(4096, 21), record_of(4096, 22)];
    let senders: Vec<_> = [(conn_a, rec_a.clone()), (conn_b, rec_b.clone())]
        .into_iter()
        .map(|(conn, records)| {
            let events = live.events.clone();
            let barrier = Arc::new(());
            let _ = barrier;
            thread::spawn(move || {
                for (n, record) in records.iter().enumerate() {
                    radio_deliver(
                        &events,
                        conn,
                        record,
                        185,
                        n as u8 + 1,
                        usize::MAX,
                        Duration::ZERO,
                    );
                }
            })
        })
        .collect();
    for sender in senders {
        sender.join().unwrap();
    }
    live.stall.release();

    let mut got: std::collections::HashMap<String, Vec<String>> = Default::default();
    for _ in 0..4 {
        let frame = live.out.recv_timeout(Duration::from_secs(10)).unwrap();
        assert_eq!(frame["type"], "message", "no link may fail: {frame}");
        got.entry(frame["connection_handle"].as_str().unwrap().to_string())
            .or_default()
            .push(frame["record_base64"].as_str().unwrap().to_string());
    }
    let enc = |records: &[Vec<u8>; 2]| -> Vec<String> {
        records
            .iter()
            .map(|r| veles_ble_host::base64::encode(r))
            .collect()
    };
    assert_eq!(
        got[&handle_a],
        enc(&rec_a),
        "link A: exact bytes, exact order"
    );
    assert_eq!(
        got[&handle_b],
        enc(&rec_b),
        "link B: exact bytes, exact order"
    );
    assert_eq!(got.len(), 2, "no cross-over to any other link");
    finish(live);
}

/// Real cap overflow end to end: during the stall link A receives its two records and then the
/// first fragment of a third (the 49th event). Link A fails closed — its 48 retained fragments
/// still deliver both records before the single `disconnected` — while link B, which stayed
/// within its own bound, is completely unaffected.
#[test]
fn stalled_core_beyond_the_per_link_bound_fails_that_link_closed_and_isolates_the_other() {
    let live = Live::start();
    let [(handle_a, conn_a), (handle_b, conn_b), (handle_s, conn_s)] = three_phones(&live);
    stall_core(&live, &handle_s, conn_s);

    let rec_a = [record_of(4096, 31), record_of(4096, 32)];
    let rec_b = [record_of(4096, 41), record_of(4096, 42)];
    for (n, record) in rec_a.iter().enumerate() {
        radio_deliver(
            &live.events,
            conn_a,
            record,
            185,
            n as u8 + 1,
            usize::MAX,
            Duration::ZERO,
        );
    }
    // The 49th unconsumed fragment of link A: a third record's first fragment.
    let third = fragment(
        &record_of(4096, 33),
        LinkBudget::from_att_mtu(185).unwrap(),
        3,
    )
    .unwrap();
    live.events.emit(BackendEvent::Fragment {
        conn: conn_a,
        bytes: third.into_iter().next().unwrap(),
    });
    // Further fragments of the failed link are refused, never queued behind the gap.
    live.events.emit(BackendEvent::Fragment {
        conn: conn_a,
        bytes: Payload::new(vec![0x01, 0x03, 0, 0, 0xEE]),
    });
    for (n, record) in rec_b.iter().enumerate() {
        radio_deliver(
            &live.events,
            conn_b,
            record,
            185,
            n as u8 + 1,
            usize::MAX,
            Duration::ZERO,
        );
    }
    live.stall.release();

    let mut a_frames = Vec::new();
    let mut b_frames = Vec::new();
    let started = Instant::now();
    while a_frames.len() < 3 || b_frames.len() < 2 {
        assert!(
            started.elapsed() < Duration::from_secs(10),
            "{a_frames:?} {b_frames:?}"
        );
        let frame = live.out.recv_timeout(Duration::from_secs(10)).unwrap();
        let handle = frame["connection_handle"]
            .as_str()
            .unwrap_or("")
            .to_string();
        if handle == handle_a {
            a_frames.push(frame);
        } else if handle == handle_b {
            b_frames.push(frame);
        }
    }
    let kinds = |frames: &[Value]| -> Vec<String> {
        frames
            .iter()
            .map(|f| f["type"].as_str().unwrap().to_string())
            .collect()
    };
    assert_eq!(kinds(&a_frames), ["message", "message", "disconnected"]);
    assert_eq!(
        a_frames[0]["record_base64"],
        veles_ble_host::base64::encode(&rec_a[0])
    );
    assert_eq!(
        a_frames[1]["record_base64"],
        veles_ble_host::base64::encode(&rec_a[1])
    );
    assert_eq!(kinds(&b_frames), ["message", "message"], "link B untouched");
    assert_eq!(
        b_frames[0]["record_base64"],
        veles_ble_host::base64::encode(&rec_b[0])
    );
    assert_eq!(
        b_frames[1]["record_base64"],
        veles_ble_host::base64::encode(&rec_b[1])
    );
    finish(live);
}

/// The guarantee is the bound, not "two records at every MTU": at MTU 247 a max record is 18
/// fragments, so two (36) fit with room to spare during a stall, with exact bytes.
#[test]
fn stalled_core_two_max_records_at_mtu_247_fit_the_same_bound() {
    let live = Live::start();
    let (handle_a, conn_a) = live.connect_phone("phone-a", 247);
    let (handle_s, conn_s) = live.connect_phone("phone-stall", 185);
    stall_core(&live, &handle_s, conn_s);
    let records = [record_of(4096, 51), record_of(4096, 52)];
    for (n, record) in records.iter().enumerate() {
        radio_deliver(
            &live.events,
            conn_a,
            record,
            247,
            n as u8 + 1,
            usize::MAX,
            Duration::ZERO,
        );
    }
    live.stall.release();
    for record in &records {
        expect_record(&live, &handle_a, record);
    }
    finish(live);
}
