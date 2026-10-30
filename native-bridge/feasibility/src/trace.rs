//! Opt-in safe trace: one line per framed message or lifecycle step, never record bytes, labels or
//! full platform hints. Enabled only by `VELES_OTP23_TRACE=<file>` in the host's environment.

use std::fmt;
use std::fs::OpenOptions;
use std::io::Write;
use std::sync::{Mutex, OnceLock};
use std::time::Instant;

use serde_json::Value;

const MAX_TRACE_BYTES: usize = 1024 * 1024;

struct Sink {
    file: std::fs::File,
    start: Instant,
    written: usize,
}

static SINK: OnceLock<Mutex<Sink>> = OnceLock::new();

/// Opens the trace file named by `VELES_OTP23_TRACE`, if any. Idempotent.
pub fn init_from_env() {
    let Some(path) = std::env::var_os("VELES_OTP23_TRACE") else {
        return;
    };
    if let Ok(file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = SINK.set(Mutex::new(Sink {
            file,
            start: Instant::now(),
            written: 0,
        }));
        log(format_args!("start pid={}", std::process::id()));
    }
}

pub fn enabled() -> bool {
    SINK.get().is_some()
}

pub fn log(args: fmt::Arguments) {
    let Some(sink) = SINK.get() else {
        return;
    };
    let Ok(mut sink) = sink.lock() else {
        return;
    };
    let line = format!(
        "[{:>8}ms pid={}] {}\n",
        sink.start.elapsed().as_millis(),
        std::process::id(),
        args
    );
    if sink.written + line.len() > MAX_TRACE_BYTES {
        return;
    }
    sink.written += line.len();
    let _ = sink.file.write_all(line.as_bytes());
}

#[macro_export]
macro_rules! trace {
    ($($arg:tt)*) => { $crate::trace::log(format_args!($($arg)*)) };
}

/// `winrt:aabbccddeeff:1` -> `winrt:..ddeeff:1`: enough to correlate, not enough to identify.
pub fn redact_hint(hint: &str) -> String {
    let mut parts: Vec<String> = hint.split(':').map(str::to_string).collect();
    if parts.len() >= 2 && parts[1].len() > 6 {
        let tail = parts[1][parts[1].len() - 4..].to_string();
        parts[1] = format!("..{tail}");
    }
    parts.join(":")
}

/// Whitelisted fields of one framed JSON message; payload-bearing and identifying fields
/// (`record_base64`, `advertisement`, `label`, `platform_hint`) are reduced to their lengths.
pub fn summarize_frame(bytes: &[u8]) -> String {
    let Ok(Value::Object(object)) = serde_json::from_slice::<Value>(bytes) else {
        return format!("len={} unparsed", bytes.len());
    };
    let mut parts = vec![format!("len={}", bytes.len())];
    for key in [
        "type",
        "request_id",
        "api_version",
        "code",
        "recoverability",
        "stage",
        "status",
        "scan_handle",
        "device_handle",
        "connection_handle",
    ] {
        if let Some(value) = object.get(key) {
            let text = match value {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            };
            let text: String = text.chars().take(64).collect();
            parts.push(format!("{key}={text}"));
        }
    }
    for key in ["record_base64", "advertisement", "label", "platform_hint"] {
        if let Some(Value::String(s)) = object.get(key) {
            parts.push(format!("{key}.len={}", s.len()));
        }
    }
    parts.join(" ")
}
