//! Native Messaging envelope v1 (docs/protocol/veles-v1.md section 10): strict request parsing
//! and deterministic event serialisation. Records stay opaque bytes; nothing here interprets them.

use serde_json::{Map, Value, json};

use crate::base64::{self, Base64Error};
use crate::strict_json::parse_strict;

pub const ENVELOPE_VERSION: u64 = 1;
pub const SUPPORTED_API_VERSIONS: &[u64] = &[1];
pub const CAPABILITY_RECORD_V1: &str = "veles_record_v1";
/// Decoded `record_base64` bound at the bridge boundary (OTP2-BRD-031).
pub const MAX_RECORD_BASE64_DECODED: usize = 5120;
const MAX_API_VERSION: u64 = 1_048_576;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ErrorCode {
    IncompatibleApi,
    InvalidRequest,
    UnknownHandle,
    UnknownRequest,
    InvalidBase64,
    OversizeRecord,
    CapExceeded,
    Busy,
    ScanFailed,
    ConnectFailed,
    SendFailed,
    Timeout,
    Internal,
}

impl ErrorCode {
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorCode::IncompatibleApi => "incompatible_api",
            ErrorCode::InvalidRequest => "invalid_request",
            ErrorCode::UnknownHandle => "unknown_handle",
            ErrorCode::UnknownRequest => "unknown_request",
            ErrorCode::InvalidBase64 => "invalid_base64",
            ErrorCode::OversizeRecord => "oversize_record",
            ErrorCode::CapExceeded => "cap_exceeded",
            ErrorCode::Busy => "busy",
            ErrorCode::ScanFailed => "scan_failed",
            ErrorCode::ConnectFailed => "connect_failed",
            ErrorCode::SendFailed => "send_failed",
            ErrorCode::Timeout => "timeout",
            ErrorCode::Internal => "internal",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Recoverability {
    Retryable,
    Fatal,
}

impl Recoverability {
    fn as_str(self) -> &'static str {
        match self {
            Recoverability::Retryable => "retryable",
            Recoverability::Fatal => "fatal",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    PreReady,
    Ready { api_version: u64 },
}

#[derive(Debug, PartialEq, Eq)]
pub enum Offer {
    Versions(Vec<u64>),
    Range { min: u64, max: u64 },
}

impl Offer {
    /// Highest version present in both the offer and `supported`.
    pub fn select(&self, supported: &[u64]) -> Option<u64> {
        supported
            .iter()
            .copied()
            .filter(|version| match self {
                Offer::Versions(list) => list.contains(version),
                Offer::Range { min, max } => (*min..=*max).contains(version),
            })
            .max()
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Target {
    Device(String),
    Hint(String),
}

#[derive(Debug, PartialEq, Eq)]
pub enum RequestBody {
    Hello(Offer),
    StartScan {
        service_uuid: String,
    },
    StopScan {
        scan_handle: String,
    },
    Connect(Target),
    Disconnect {
        connection_handle: String,
    },
    Send {
        connection_handle: String,
        record: Vec<u8>,
    },
    Shutdown,
}

#[derive(Debug, PartialEq, Eq)]
pub struct Request {
    pub request_id: String,
    pub body: RequestBody,
}

/// A request that is recognisable (valid envelope version, request id and type) but invalid.
#[derive(Debug, PartialEq, Eq)]
pub struct Rejection {
    pub request_id: String,
    pub code: ErrorCode,
    pub recoverability: Recoverability,
    pub stage: &'static str,
}

#[derive(Debug, PartialEq, Eq)]
pub enum ParseFailure {
    /// No usable request id / type / envelope version: no JSON response, orderly fail-closed exit.
    Uncorrelatable,
    Rejected(Rejection),
}

// ---- field validators -----------------------------------------------------------------------------

fn valid_request_id(id: &str) -> bool {
    (1..=64).contains(&id.len())
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
}

fn valid_handle(text: &str) -> bool {
    text.len() == 16 && text.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

fn valid_uuid(text: &str) -> bool {
    let b = text.as_bytes();
    b.len() == 36
        && b.iter().enumerate().all(|(i, c)| match i {
            8 | 13 | 18 | 23 => *c == b'-',
            _ => matches!(c, b'0'..=b'9' | b'a'..=b'f'),
        })
}

fn has_control(text: &str) -> bool {
    text.chars().any(|c| c <= '\u{1f}' || c == '\u{7f}')
}

fn valid_hint(text: &str) -> bool {
    (1..=256).contains(&text.len()) && !has_control(text)
}

const COMMON: [&str; 4] = ["envelope_version", "type", "request_id", "api_version"];

/// `Some(())` when every key is allowed and every required key is present.
fn fields_ok(object: &Map<String, Value>, required: &[&str], optional: &[&str]) -> bool {
    object.keys().all(|k| {
        COMMON.contains(&k.as_str())
            || required.contains(&k.as_str())
            || optional.contains(&k.as_str())
    }) && required.iter().all(|k| object.contains_key(*k))
}

fn str_field<'a>(object: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    object.get(key).and_then(Value::as_str)
}

fn api_int(value: &Value) -> Option<u64> {
    value.as_u64().filter(|v| (1..=MAX_API_VERSION).contains(v))
}

fn parse_offer(object: &Map<String, Value>) -> Option<Offer> {
    let allowed = [
        "envelope_version",
        "type",
        "request_id",
        "api_versions",
        "api_version_range",
    ];
    if !object.keys().all(|k| allowed.contains(&k.as_str())) {
        return None;
    }
    match (object.get("api_versions"), object.get("api_version_range")) {
        (Some(Value::Array(list)), None) => {
            if list.is_empty() || list.len() > 16 {
                return None;
            }
            let versions: Option<Vec<u64>> = list.iter().map(api_int).collect();
            let versions = versions?;
            versions
                .windows(2)
                .all(|w| w[0] < w[1])
                .then_some(Offer::Versions(versions))
        }
        (None, Some(Value::Object(range))) => {
            if range.len() != 2 {
                return None;
            }
            let min = api_int(range.get("min")?)?;
            let max = api_int(range.get("max")?)?;
            (min <= max).then_some(Offer::Range { min, max })
        }
        _ => None,
    }
}

pub fn parse_request(bytes: &[u8], phase: Phase) -> Result<Request, ParseFailure> {
    let value = parse_strict(bytes).map_err(|_| ParseFailure::Uncorrelatable)?;
    let object = value.as_object().ok_or(ParseFailure::Uncorrelatable)?;
    if object.get("envelope_version").and_then(Value::as_u64) != Some(ENVELOPE_VERSION) {
        return Err(ParseFailure::Uncorrelatable);
    }
    let request_id = str_field(object, "request_id")
        .filter(|id| valid_request_id(id))
        .ok_or(ParseFailure::Uncorrelatable)?
        .to_string();
    let kind = str_field(object, "type").ok_or(ParseFailure::Uncorrelatable)?;

    let reject = |code, stage| {
        ParseFailure::Rejected(Rejection {
            request_id: request_id.clone(),
            code,
            recoverability: Recoverability::Fatal,
            stage,
        })
    };
    let invalid = |stage| reject(ErrorCode::InvalidRequest, stage);
    let ok = |body| {
        Ok(Request {
            request_id: request_id.clone(),
            body,
        })
    };

    match phase {
        Phase::PreReady => {
            if kind != "hello" {
                return Err(invalid("pre_ready"));
            }
            match parse_offer(object) {
                Some(offer) => ok(RequestBody::Hello(offer)),
                None => Err(invalid("negotiation")),
            }
        }
        Phase::Ready { api_version } => {
            if object.get("api_version").and_then(Value::as_u64) != Some(api_version) {
                return Err(invalid("api_version"));
            }
            let schema = || invalid("schema");
            let handle_of = |key: &str| {
                str_field(object, key)
                    .filter(|h| valid_handle(h))
                    .map(str::to_string)
            };
            match kind {
                "hello" => Err(invalid("post_ready")),
                "start_scan" => {
                    if !fields_ok(object, &["service_uuid"], &[]) {
                        return Err(schema());
                    }
                    match str_field(object, "service_uuid").filter(|u| valid_uuid(u)) {
                        Some(uuid) => ok(RequestBody::StartScan {
                            service_uuid: uuid.to_string(),
                        }),
                        None => Err(schema()),
                    }
                }
                "stop_scan" => {
                    if !fields_ok(object, &["scan_handle"], &[]) {
                        return Err(schema());
                    }
                    handle_of("scan_handle")
                        .map(|scan_handle| RequestBody::StopScan { scan_handle })
                        .map_or_else(|| Err(schema()), ok)
                }
                "connect" => {
                    if !fields_ok(object, &[], &["device_handle", "platform_hint"]) {
                        return Err(schema());
                    }
                    match (
                        object.contains_key("device_handle"),
                        object.contains_key("platform_hint"),
                    ) {
                        (true, false) => handle_of("device_handle")
                            .map(|h| RequestBody::Connect(Target::Device(h)))
                            .map_or_else(|| Err(schema()), ok),
                        (false, true) => str_field(object, "platform_hint")
                            .filter(|h| valid_hint(h))
                            .map(|h| RequestBody::Connect(Target::Hint(h.to_string())))
                            .map_or_else(|| Err(schema()), ok),
                        _ => Err(schema()),
                    }
                }
                "disconnect" => {
                    if !fields_ok(object, &["connection_handle"], &[]) {
                        return Err(schema());
                    }
                    handle_of("connection_handle")
                        .map(|connection_handle| RequestBody::Disconnect { connection_handle })
                        .map_or_else(|| Err(schema()), ok)
                }
                "send" => {
                    if !fields_ok(object, &["connection_handle", "record_base64"], &[]) {
                        return Err(schema());
                    }
                    let Some(connection_handle) = handle_of("connection_handle") else {
                        return Err(schema());
                    };
                    let Some(text) = str_field(object, "record_base64") else {
                        return Err(schema());
                    };
                    match base64::decode(text, MAX_RECORD_BASE64_DECODED) {
                        Ok(record) => ok(RequestBody::Send {
                            connection_handle,
                            record,
                        }),
                        Err(Base64Error::Size) if !text.is_empty() => {
                            Err(reject(ErrorCode::OversizeRecord, "record"))
                        }
                        Err(_) => Err(reject(ErrorCode::InvalidBase64, "record")),
                    }
                }
                "shutdown" => {
                    if fields_ok(object, &[], &[]) {
                        ok(RequestBody::Shutdown)
                    } else {
                        Err(schema())
                    }
                }
                _ => Err(reject(ErrorCode::UnknownRequest, "schema")),
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Event {
    Ready {
        request_id: String,
        bridge_version: String,
        platform: &'static str,
        capabilities: Vec<String>,
    },
    DeviceFound {
        scan_handle: String,
        device_handle: String,
        label: String,
        platform_hint: Option<String>,
        advertisement: Option<Vec<u8>>,
    },
    ScanState {
        request_id: Option<String>,
        scan_handle: String,
        started: bool,
    },
    Connected {
        request_id: String,
        connection_handle: String,
        platform_hint: Option<String>,
    },
    Disconnected {
        request_id: Option<String>,
        connection_handle: String,
    },
    Sent {
        request_id: String,
        connection_handle: String,
    },
    Message {
        connection_handle: String,
        record: Vec<u8>,
    },
    ShutdownComplete {
        request_id: String,
    },
    Error {
        request_id: Option<String>,
        code: ErrorCode,
        recoverability: Recoverability,
        stage: String,
        scan_handle: Option<String>,
        connection_handle: Option<String>,
    },
}

impl Event {
    /// `api_version` is `None` only before negotiation completes (pre-`ready` errors).
    pub fn to_json(&self, api_version: Option<u64>) -> Vec<u8> {
        let mut object = Map::new();
        let mut put = |key: &str, value: Value| {
            object.insert(key.to_string(), value);
        };
        put("envelope_version", json!(ENVELOPE_VERSION));
        if let Some(api) = api_version {
            put("api_version", json!(api));
        }
        if let Some(id) = self.request_id() {
            put("request_id", json!(id));
        }
        match self {
            Event::Ready {
                bridge_version,
                platform,
                capabilities,
                ..
            } => {
                put("type", json!("ready"));
                put("bridge_version", json!(bridge_version));
                put("platform", json!(platform));
                put("capabilities", json!(capabilities));
            }
            Event::DeviceFound {
                scan_handle,
                device_handle,
                label,
                platform_hint,
                advertisement,
            } => {
                put("type", json!("device_found"));
                put("scan_handle", json!(scan_handle));
                put("device_handle", json!(device_handle));
                put("label", json!(label));
                if let Some(hint) = platform_hint {
                    put("platform_hint", json!(hint));
                }
                if let Some(bytes) = advertisement {
                    put("advertisement", json!(base64::encode(bytes)));
                }
            }
            Event::ScanState {
                scan_handle,
                started,
                ..
            } => {
                put("type", json!("scan_state"));
                put("scan_handle", json!(scan_handle));
                put(
                    "status",
                    json!(if *started { "started" } else { "stopped" }),
                );
            }
            Event::Connected {
                connection_handle,
                platform_hint,
                ..
            } => {
                put("type", json!("connected"));
                put("connection_handle", json!(connection_handle));
                if let Some(hint) = platform_hint {
                    put("platform_hint", json!(hint));
                }
            }
            Event::Disconnected {
                connection_handle, ..
            } => {
                put("type", json!("disconnected"));
                put("connection_handle", json!(connection_handle));
            }
            Event::Sent {
                connection_handle, ..
            } => {
                put("type", json!("sent"));
                put("connection_handle", json!(connection_handle));
            }
            Event::Message {
                connection_handle,
                record,
            } => {
                put("type", json!("message"));
                put("connection_handle", json!(connection_handle));
                put("record_base64", json!(base64::encode(record)));
            }
            Event::ShutdownComplete { .. } => {
                put("type", json!("shutdown_complete"));
            }
            Event::Error {
                code,
                recoverability,
                stage,
                scan_handle,
                connection_handle,
                ..
            } => {
                put("type", json!("error"));
                put("code", json!(code.as_str()));
                put("recoverability", json!(recoverability.as_str()));
                put("stage", json!(stage));
                if let Some(handle) = scan_handle {
                    put("scan_handle", json!(handle));
                } else if let Some(handle) = connection_handle {
                    put("connection_handle", json!(handle));
                }
            }
        }
        serde_json::to_vec(&Value::Object(object)).expect("event serialises")
    }

    pub fn request_id(&self) -> Option<&str> {
        match self {
            Event::Ready { request_id, .. }
            | Event::Connected { request_id, .. }
            | Event::Sent { request_id, .. }
            | Event::ShutdownComplete { request_id } => Some(request_id),
            Event::ScanState { request_id, .. }
            | Event::Disconnected { request_id, .. }
            | Event::Error { request_id, .. } => request_id.as_deref(),
            Event::DeviceFound { .. } | Event::Message { .. } => None,
        }
    }
}
