//! Task 1 contract tests (OTP-24 production crate): strict JSON intake, canonical base64,
//! opaque payload ownership, Native Messaging framing and the request/event envelope
//! vocabulary of `protocol/docs/veles-v1.md` section 10. Shared shared-fixture vectors from
//! `protocol/v1/fixtures/bridge.json` drive the size-boundary verdicts.

use std::io::{Cursor, Read, Result as IoResult, Write};

use veles_ble_host::base64::{self, Base64Error};
use veles_ble_host::buffer::Payload;
use veles_ble_host::framing::{FrameError, read_frame, write_frame};
use veles_ble_host::limits::{
    DECODED_RECORD_MAX_BYTES, FRAME_MAX_BYTES, GATT_SEND_RECORD_MAX_BYTES,
};
use veles_ble_host::protocol::{
    ErrorCode, Event, Offer, ParseFailure, Phase, Recoverability, Rejection, Request, RequestBody,
    Target, parse_request,
};
use veles_ble_host::strict_json::{JsonError, parse_strict};

const READY: Phase = Phase::Ready { api_version: 1 };
const HANDLE: &str = "0123456789abcdef";
const UUID: &str = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51";

fn uncorrelatable(text: &str, phase: Phase) {
    assert_eq!(
        parse_request(text.as_bytes(), phase),
        Err(ParseFailure::Uncorrelatable),
        "{text}"
    );
}

fn rejected(text: &str, phase: Phase, id: &str, code: ErrorCode, stage: &str) {
    match parse_request(text.as_bytes(), phase) {
        Err(ParseFailure::Rejected(Rejection {
            request_id,
            code: c,
            stage: s,
            ..
        })) => {
            assert_eq!((request_id.as_str(), c, s), (id, code, stage), "{text}");
        }
        other => panic!("{text}: expected rejection, got {other:?}"),
    }
}

fn accepted(text: &str, phase: Phase) -> Request {
    parse_request(text.as_bytes(), phase).unwrap_or_else(|e| panic!("{text}: {e:?}"))
}

// ---- strict JSON ---------------------------------------------------------------------------

#[test]
fn strict_json_rejects_duplicate_keys_at_any_depth() {
    assert_eq!(
        parse_strict(br#"{"a":1,"a":2}"#),
        Err(JsonError::DuplicateKey)
    );
    assert_eq!(
        parse_strict(br#"{"a":{"b":1,"b":1}}"#),
        Err(JsonError::DuplicateKey)
    );
    assert_eq!(
        parse_strict(br#"{"a":[{"b":1,"b":1}]}"#),
        Err(JsonError::DuplicateKey)
    );
}

#[test]
fn strict_json_rejects_trailing_bytes_including_whitespace() {
    assert_eq!(parse_strict(b"{} "), Err(JsonError::TrailingBytes));
    assert_eq!(parse_strict(b"{}\n"), Err(JsonError::TrailingBytes));
    assert_eq!(parse_strict(b"{}{}"), Err(JsonError::TrailingBytes));
    assert_eq!(parse_strict(b"{}x"), Err(JsonError::TrailingBytes));
}

#[test]
fn strict_json_rejects_non_integers_and_null() {
    for text in [
        &br#"{"a":1.0}"#[..],
        br#"{"a":1e2}"#,
        br#"{"a":-0.5}"#,
        br#"{"a":18446744073709551616}"#,
    ] {
        assert_eq!(
            parse_strict(text),
            Err(JsonError::NonInteger),
            "{}",
            String::from_utf8_lossy(text)
        );
    }
    assert_eq!(parse_strict(br#"{"a":null}"#), Err(JsonError::Null));
    assert_eq!(parse_strict(br#"{"a":[1,null]}"#), Err(JsonError::Null));
}

#[test]
fn strict_json_rejects_syntax_and_invalid_utf8() {
    for text in [
        &b""[..],
        b"{",
        b"{'a':1}",
        b"[1,]",
        b"\xff\xfe",
        b"{\"a\":\"\xc3\x28\"}",
    ] {
        assert_eq!(parse_strict(text), Err(JsonError::Syntax), "{text:?}");
    }
}

#[test]
fn strict_json_accepts_plain_documents_and_unicode() {
    let value =
        parse_strict("{\"a\":[1,-2,18446744073709551615],\"b\":\"é😀\"}".as_bytes()).unwrap();
    assert_eq!(value["b"], "é😀");
    assert_eq!(value["a"][2], 18446744073709551615u64);
}

// ---- base64 --------------------------------------------------------------------------------

#[test]
fn base64_round_trips_and_matches_rfc4648_vectors() {
    for (raw, text) in [
        ("f", "Zg=="),
        ("fo", "Zm8="),
        ("foo", "Zm9v"),
        ("foob", "Zm9vYg=="),
        ("fooba", "Zm9vYmE="),
    ] {
        assert_eq!(base64::encode(raw.as_bytes()), text);
        assert_eq!(base64::decode(text, 16).unwrap(), raw.as_bytes());
    }
    let all: Vec<u8> = (0..=255u8).collect();
    assert_eq!(base64::decode(&base64::encode(&all), 256).unwrap(), all);
}

#[test]
fn base64_rejects_non_canonical_and_malformed_text() {
    assert_eq!(base64::decode("Zh==", 8), Err(Base64Error::NonCanonical));
    assert_eq!(base64::decode("Zm9=", 8), Err(Base64Error::NonCanonical));
    for text in [
        "Zg", "Zg=", "Z===", "Zm9v\n", " Zm9v", "Zm 9v", "Zm9v====", "Z=g=", "Zm9*", "Zm9v-_",
        "=Zm9",
    ] {
        assert_eq!(
            base64::decode(text, 16),
            Err(Base64Error::Malformed),
            "{text:?}"
        );
    }
}

#[test]
fn base64_enforces_decoded_size_bounds_zero_and_max() {
    assert_eq!(base64::decode("", 8), Err(Base64Error::Size));
    assert!(base64::decode(&base64::encode(&[7u8; 5120]), 5120).is_ok());
    assert_eq!(
        base64::decode(&base64::encode(&[7u8; 5121]), 5120),
        Err(Base64Error::Size)
    );
}

// ---- hello / negotiation --------------------------------------------------------------------

#[test]
fn hello_accepts_version_lists_and_ranges() {
    let list = accepted(
        r#"{"envelope_version":1,"type":"hello","request_id":"h1","api_versions":[1,2]}"#,
        Phase::PreReady,
    );
    assert_eq!(list.body, RequestBody::Hello(Offer::Versions(vec![1, 2])));
    let range = accepted(
        r#"{"envelope_version":1,"type":"hello","request_id":"h1","api_version_range":{"min":1,"max":3}}"#,
        Phase::PreReady,
    );
    assert_eq!(
        range.body,
        RequestBody::Hello(Offer::Range { min: 1, max: 3 })
    );
}

#[test]
fn offer_selects_highest_mutual_version() {
    assert_eq!(Offer::Versions(vec![1, 2, 7]).select(&[1, 2]), Some(2));
    assert_eq!(Offer::Range { min: 2, max: 9 }.select(&[1]), None);
    assert_eq!(Offer::Range { min: 1, max: 9 }.select(&[1]), Some(1));
    assert_eq!(Offer::Versions(vec![5]).select(&[1]), None);
}

#[test]
fn hello_offer_shape_violations_are_rejected_as_negotiation_errors() {
    let id = "h1";
    for body in [
        r#""api_versions":[1],"api_version_range":{"min":1,"max":1}"#, // both
        r#""x":1"#, // neither (and unknown field)
        r#""api_versions":[]"#,
        r#""api_versions":[2,1]"#,     // not ascending
        r#""api_versions":[1,1]"#,     // duplicate
        r#""api_versions":[0]"#,       // below range
        r#""api_versions":[1048577]"#, // above range
        r#""api_versions":[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17]"#, // 17 > 16
        r#""api_version_range":{"min":3,"max":2}"#,
        r#""api_version_range":{"min":1}"#,
        r#""api_version_range":{"min":1,"max":2,"extra":1}"#,
        r#""api_versions":"1""#,
    ] {
        let text = format!(r#"{{"envelope_version":1,"type":"hello","request_id":"{id}",{body}}}"#);
        rejected(
            &text,
            Phase::PreReady,
            id,
            ErrorCode::InvalidRequest,
            "negotiation",
        );
    }
}

#[test]
fn pre_ready_non_hello_with_usable_id_is_correlated_fatal_pre_ready() {
    let text = format!(
        r#"{{"envelope_version":1,"type":"start_scan","request_id":"s1","api_version":1,"service_uuid":"{UUID}"}}"#
    );
    match parse_request(text.as_bytes(), Phase::PreReady) {
        Err(ParseFailure::Rejected(r)) => {
            assert_eq!(r.request_id, "s1");
            assert_eq!(
                (r.code, r.recoverability, r.stage),
                (
                    ErrorCode::InvalidRequest,
                    Recoverability::Fatal,
                    "pre_ready"
                )
            );
        }
        other => panic!("{other:?}"),
    }
}

#[test]
fn unusable_envelopes_are_uncorrelatable_in_both_phases() {
    for phase in [Phase::PreReady, READY] {
        for text in [
            "not json",
            "{}",
            r#"[]"#,
            r#"{"envelope_version":2,"type":"hello","request_id":"h"}"#, // wrong envelope version
            r#"{"envelope_version":"1","type":"hello","request_id":"h"}"#,
            r#"{"type":"hello","request_id":"h"}"#,
            r#"{"envelope_version":1,"request_id":"h"}"#, // no type
            r#"{"envelope_version":1,"type":"hello"}"#,   // no request id
            r#"{"envelope_version":1,"type":"hello","request_id":""}"#,
            r#"{"envelope_version":1,"type":"hello","request_id":"has space"}"#,
            r#"{"envelope_version":1,"type":"hello","request_id":"é"}"#,
            r#"{"envelope_version":1,"type":7,"request_id":"h"}"#,
            r#"{"envelope_version":1,"type":"hello","type":"hello","request_id":"h"}"#, // duplicate key
            r#"{"envelope_version":1,"type":"hello","request_id":"h"} "#,               // trailing
            r#"{"envelope_version":1,"type":"hello","request_id":null}"#,
        ] {
            uncorrelatable(text, phase);
        }
        let too_long = format!(
            r#"{{"envelope_version":1,"type":"hello","request_id":"{}"}}"#,
            "a".repeat(65)
        );
        uncorrelatable(&too_long, phase);
    }
}

#[test]
fn request_id_accepts_64_chars_of_the_allowed_alphabet() {
    let id = format!("{}._-Z9", "a".repeat(59));
    assert_eq!(id.len(), 64);
    let text = format!(
        r#"{{"envelope_version":1,"type":"shutdown","request_id":"{id}","api_version":1}}"#
    );
    assert_eq!(accepted(&text, READY).request_id, id);
}

// ---- post-ready requests ----------------------------------------------------------------------

#[test]
fn accepts_every_request_type_with_exact_fields() {
    let cases: Vec<(String, RequestBody)> = vec![
        (
            format!(
                r#"{{"envelope_version":1,"api_version":1,"type":"start_scan","request_id":"r","service_uuid":"{UUID}"}}"#
            ),
            RequestBody::StartScan { service_uuid: UUID.into() },
        ),
        (
            format!(
                r#"{{"envelope_version":1,"api_version":1,"type":"stop_scan","request_id":"r","scan_handle":"{HANDLE}"}}"#,
            ),
            RequestBody::StopScan { scan_handle: HANDLE.into() },
        ),
        (
            format!(
                r#"{{"envelope_version":1,"api_version":1,"type":"connect","request_id":"r","device_handle":"{HANDLE}"}}"#
            ),
            RequestBody::Connect(Target::Device(HANDLE.into())),
        ),
        (
            r#"{"envelope_version":1,"api_version":1,"type":"connect","request_id":"r","platform_hint":"winrt:aabbccddeeff:1"}"#
                .into(),
            RequestBody::Connect(Target::Hint("winrt:aabbccddeeff:1".into())),
        ),
        (
            format!(
                r#"{{"envelope_version":1,"api_version":1,"type":"disconnect","request_id":"r","connection_handle":"{HANDLE}"}}"#
            ),
            RequestBody::Disconnect { connection_handle: HANDLE.into() },
        ),
        (
            format!(
                r#"{{"envelope_version":1,"api_version":1,"type":"send","request_id":"r","connection_handle":"{HANDLE}","record_base64":"VkVMAQ=="}}"#
            ),
            RequestBody::Send { connection_handle: HANDLE.into(), record: Payload::new(vec![0x56, 0x45, 0x4c, 0x01]) },
        ),
        (
            r#"{"envelope_version":1,"api_version":1,"type":"shutdown","request_id":"r"}"#.into(),
            RequestBody::Shutdown,
        ),
    ];
    for (text, body) in cases {
        assert_eq!(
            accepted(&text, READY),
            Request {
                request_id: "r".into(),
                body
            },
            "{text}"
        );
    }
}

#[test]
fn post_ready_requires_the_negotiated_api_version() {
    let missing = r#"{"envelope_version":1,"type":"shutdown","request_id":"r"}"#;
    rejected(
        missing,
        READY,
        "r",
        ErrorCode::InvalidRequest,
        "api_version",
    );
    let other = r#"{"envelope_version":1,"api_version":2,"type":"shutdown","request_id":"r"}"#;
    rejected(other, READY, "r", ErrorCode::InvalidRequest, "api_version");
}

#[test]
fn schema_violations_with_a_usable_id_are_correlated_invalid_request() {
    let bad_handles = [
        "",
        "0123456789ABCDEF",
        "0123456789abcde",
        "0123456789abcdef0",
        "gggggggggggggggg",
    ];
    for handle in bad_handles {
        let text = format!(
            r#"{{"envelope_version":1,"api_version":1,"type":"stop_scan","request_id":"r","scan_handle":"{handle}"}}"#
        );
        rejected(&text, READY, "r", ErrorCode::InvalidRequest, "schema");
    }
    for uuid in [
        "8E400000-8F3C-4D1A-B64A-8E3C9B0D2A51",
        "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a5",
        "not-a-uuid",
        "",
    ] {
        let text = format!(
            r#"{{"envelope_version":1,"api_version":1,"type":"start_scan","request_id":"r","service_uuid":"{uuid}"}}"#
        );
        rejected(&text, READY, "r", ErrorCode::InvalidRequest, "schema");
    }
    for extra in [
        r#"{"envelope_version":1,"api_version":1,"type":"shutdown","request_id":"r","extra":1}"#,
        r#"{"envelope_version":1,"api_version":1,"type":"stop_scan","request_id":"r"}"#,
        r#"{"envelope_version":1,"api_version":1,"type":"connect","request_id":"r"}"#,
        r#"{"envelope_version":1,"api_version":1,"type":"connect","request_id":"r","device_handle":"0123456789abcdef","platform_hint":"x"}"#,
        r#"{"envelope_version":1,"api_version":1,"type":"connect","request_id":"r","platform_hint":""}"#,
        r#"{"envelope_version":1,"api_version":1,"type":"connect","request_id":"r","platform_hint":"bad\u0001ctl"}"#,
        r#"{"envelope_version":1,"api_version":1,"type":"hello","request_id":"r","api_versions":[1]}"#, // repeated hello
    ] {
        match parse_request(extra.as_bytes(), READY) {
            Err(ParseFailure::Rejected(r)) => {
                assert_eq!(
                    (r.request_id.as_str(), r.code),
                    ("r", ErrorCode::InvalidRequest),
                    "{extra}"
                )
            }
            other => panic!("{extra}: {other:?}"),
        }
    }
}

#[test]
fn platform_hint_bounds_are_utf8_byte_lengths() {
    let build = |hint: &str| {
        format!(
            r#"{{"envelope_version":1,"api_version":1,"type":"connect","request_id":"r","platform_hint":"{hint}"}}"#
        )
    };
    assert!(parse_request(build(&"a".repeat(256)).as_bytes(), READY).is_ok());
    rejected(
        &build(&"a".repeat(257)),
        READY,
        "r",
        ErrorCode::InvalidRequest,
        "schema",
    );
    // 129 two-byte characters = 258 bytes > 256 even though only 129 code points.
    rejected(
        &build(&"é".repeat(129)),
        READY,
        "r",
        ErrorCode::InvalidRequest,
        "schema",
    );
}

#[test]
fn unknown_request_types_are_correlated_unknown_request() {
    let text = r#"{"envelope_version":1,"api_version":1,"type":"reboot","request_id":"r"}"#;
    rejected(text, READY, "r", ErrorCode::UnknownRequest, "schema");
}

#[test]
fn send_record_validation_maps_to_specific_codes() {
    let base = |record: &str| {
        format!(
            r#"{{"envelope_version":1,"api_version":1,"type":"send","request_id":"r","connection_handle":"{HANDLE}","record_base64":"{record}"}}"#
        )
    };
    rejected(&base(""), READY, "r", ErrorCode::InvalidBase64, "record");
    rejected(
        &base("Zh=="),
        READY,
        "r",
        ErrorCode::InvalidBase64,
        "record",
    );
    rejected(
        &base("Zm9v\\n"),
        READY,
        "r",
        ErrorCode::InvalidBase64,
        "record",
    );
    let max = base64::encode(&[1u8; 5120]);
    assert!(
        parse_request(base(&max).as_bytes(), READY).is_ok(),
        "5120 decoded bytes are accepted by the envelope"
    );
    let over = base64::encode(&[1u8; 5121]);
    rejected(
        &base(&over),
        READY,
        "r",
        ErrorCode::OversizeRecord,
        "record",
    );
}

// ---- decoded-record size distinction (the 4,096-byte GATT send limit is NOT a parser limit) ----

#[test]
fn envelope_admits_records_at_and_above_the_gatt_send_limit() {
    let build = |len: usize| {
        format!(
            r#"{{"envelope_version":1,"api_version":1,"type":"send","request_id":"r","connection_handle":"{HANDLE}","record_base64":"{}"}}"#,
            base64::encode(&vec![7u8; len])
        )
    };
    for len in [
        GATT_SEND_RECORD_MAX_BYTES - 1,
        GATT_SEND_RECORD_MAX_BYTES,
        GATT_SEND_RECORD_MAX_BYTES + 1,
        5119,
        5120,
    ] {
        let ok = match parse_request(build(len).as_bytes(), READY) {
            Ok(Request {
                body:
                    RequestBody::Send {
                        connection_handle,
                        record,
                    },
                ..
            }) if connection_handle == HANDLE => record,
            other => panic!("len {len}: expected Send, got {other:?}"),
        };
        assert_eq!(ok.len(), len, "decoded payload keeps every byte");
    }
    rejected(
        &build(5121),
        READY,
        "r",
        ErrorCode::OversizeRecord,
        "record",
    );
    rejected(&build(0), READY, "r", ErrorCode::InvalidBase64, "record");
}

// ---- shared fixture vectors (protocol/v1/fixtures/bridge.json) ---------------------------------

#[test]
fn shared_bridge_fixture_send_sizes_drive_parse_verdicts() {
    let root = std::env::current_dir().expect("cargo sets the test cwd to the crate root");
    let path = root
        .join("..")
        .join("..")
        .join("protocol")
        .join("v1")
        .join("fixtures")
        .join("bridge.json");
    let raw = std::fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "read {} relative to {}: {e}",
            path.display(),
            root.display()
        )
    });
    let envelope: serde_json::Value = serde_json::from_str(&raw).expect("fixture file is JSON");
    let fixture = |wanted: &str| -> serde_json::Value {
        envelope["fixtures"]
            .as_array()
            .expect("fixtures array")
            .iter()
            .find(|entry| entry["id"].as_str() == Some(wanted))
            .cloned()
            .unwrap_or_else(|| panic!("missing fixture id {wanted}"))
    };
    for (id, accepted, decoded_len) in [
        ("brd-req-send-record-5119-below-limit", true, 5119),
        ("brd-req-send-record-5120-at-limit", true, 5120),
        ("brd-req-send-record-5121-over-limit", false, 5121),
    ] {
        let fixture = fixture(id);
        assert_eq!(fixture["expected"]["accepted"], accepted, "{id}");
        let text = fixture["input"]["text"].as_str().expect("fixture text");
        match parse_request(text.as_bytes(), READY) {
            Ok(Request {
                body: RequestBody::Send { record, .. },
                ..
            }) => {
                assert!(accepted, "{id} must be accepted");
                assert_eq!(record.len(), decoded_len, "{id}");
            }
            Err(_) => assert!(!accepted, "{id} must be rejected"),
            other => panic!("{id}: unexpected verdict {other:?}"),
        }
    }
    let empty = fixture("brd-req-send-record-empty");
    assert_eq!(empty["expected"]["accepted"], false);
    let text = empty["input"]["text"].as_str().expect("fixture text");
    assert!(
        parse_request(text.as_bytes(), READY).is_err(),
        "empty record rejects"
    );
}

// ---- payload ownership -----------------------------------------------------------------------

#[test]
fn payload_owner_semantics_are_lossless() {
    let bytes: Vec<u8> = (0..=255u8).collect();
    let payload = Payload::new(bytes);
    assert_eq!(payload.len(), 256);
    assert_eq!(payload.as_slice()[0], 0);
    assert_eq!(payload.as_slice()[255], 255);
    let other = Payload::new(vec![0u8; 256]);
    assert_ne!(payload, other);
    assert_eq!(payload, Payload::new((0..=255u8).collect()));
}

#[test]
fn payload_debug_output_never_carries_record_content() {
    let secret = b"SECRET-RECORD-BYTES";
    let payload = Payload::new(secret.to_vec());
    let message = Event::Message {
        connection_handle: HANDLE.into(),
        record: Payload::new(secret.to_vec()),
    };
    let request = Request {
        request_id: "r".into(),
        body: RequestBody::Send {
            connection_handle: HANDLE.into(),
            record: Payload::new(secret.to_vec()),
        },
    };
    for debug in [
        format!("{payload:?}"),
        format!("{message:?}"),
        format!("{request:?}"),
    ] {
        assert!(
            !debug.contains("SECRET"),
            "Debug leaked record content: {debug}"
        );
        assert!(
            !debug.contains("RECORD"),
            "Debug leaked record content: {debug}"
        );
    }
}

// ---- Native Messaging framing (OTP2-BRD-001) ----------------------------------------------------

fn frame(len_header: u32, body: &[u8]) -> Vec<u8> {
    let mut out = len_header.to_le_bytes().to_vec();
    out.extend_from_slice(body);
    out
}

/// Yields one byte per `read` call, like a slow pipe.
struct Drip(Cursor<Vec<u8>>);
impl Read for Drip {
    fn read(&mut self, buf: &mut [u8]) -> IoResult<usize> {
        let n = buf.len().min(1);
        self.0.read(&mut buf[..n])
    }
}

/// Panics if asked for more bytes than the 4-byte header: proves oversize is rejected unread.
struct HeaderOnly(Cursor<Vec<u8>>);
impl Read for HeaderOnly {
    fn read(&mut self, buf: &mut [u8]) -> IoResult<usize> {
        assert!(
            self.0.position() < 4,
            "payload was read for an oversize frame"
        );
        let allowed = (4 - self.0.position() as usize).min(buf.len());
        self.0.read(&mut buf[..allowed])
    }
}

#[derive(Default)]
struct CountingWriter {
    writes: usize,
    bytes: Vec<u8>,
    flushes: usize,
}
impl Write for CountingWriter {
    fn write(&mut self, buf: &[u8]) -> IoResult<usize> {
        self.writes += 1;
        self.bytes.extend_from_slice(buf);
        Ok(buf.len())
    }
    fn flush(&mut self) -> IoResult<()> {
        self.flushes += 1;
        Ok(())
    }
}

#[test]
fn reads_consecutive_frames_then_clean_eof() {
    let mut bytes = frame(2, b"{}");
    bytes.extend(frame(3, b"abc"));
    let mut cursor = Cursor::new(bytes);
    assert_eq!(read_frame(&mut cursor).unwrap().unwrap().as_slice(), b"{}");
    assert_eq!(read_frame(&mut cursor).unwrap().unwrap().as_slice(), b"abc");
    assert!(read_frame(&mut cursor).unwrap().is_none());
}

#[test]
fn accepts_exactly_the_maximum_frame() {
    let body = vec![b'x'; FRAME_MAX_BYTES];
    let mut cursor = Cursor::new(frame(FRAME_MAX_BYTES as u32, &body));
    assert_eq!(
        read_frame(&mut cursor).unwrap().unwrap().len(),
        FRAME_MAX_BYTES
    );
}

#[test]
fn rejects_one_byte_over_the_maximum_without_reading_the_payload() {
    let declared = FRAME_MAX_BYTES as u32 + 1;
    let mut reader = HeaderOnly(Cursor::new(frame(declared, &vec![b'x'; 70_000])));
    assert!(
        matches!(
            read_frame(&mut reader),
            Err(FrameError::Oversize { declared: d }) if d == u64::from(declared)
        ),
        "declared 65537 rejects before the payload buffer is allocated or read"
    );
}

#[test]
fn rejects_four_gigabyte_declaration_without_allocating() {
    let mut reader = HeaderOnly(Cursor::new(frame(u32::MAX, b"")));
    assert!(matches!(
        read_frame(&mut reader),
        Err(FrameError::Oversize { .. })
    ));
}

#[test]
fn rejects_zero_length_frame_after_exactly_the_header() {
    let mut probe = Probe {
        inner: Cursor::new(frame(0, b"abcdefgh")),
        consumed: 0,
    };
    assert!(matches!(
        read_frame(&mut probe),
        Err(FrameError::ZeroLength)
    ));
    assert_eq!(probe.consumed, 4, "only the header is read");
}

#[test]
fn truncated_header_is_distinguished_from_clean_eof() {
    let mut clean = probe_of(vec![]);
    assert!(
        matches!(read_frame(&mut clean), Ok(None)),
        "empty input is a clean EOF"
    );
    for got in 1..=3usize {
        let mut cursor = probe_of(vec![1u8; got]);
        assert!(
            matches!(read_frame(&mut cursor), Err(FrameError::TruncatedHeader { got: g }) if g == got),
            "{got} header bytes"
        );
        assert_eq!(cursor.consumed, got);
    }
}

fn probe_of(bytes: Vec<u8>) -> Probe<Cursor<Vec<u8>>> {
    Probe {
        inner: Cursor::new(bytes),
        consumed: 0,
    }
}

struct Probe<R> {
    inner: R,
    consumed: usize,
}

impl<R: Read> Read for Probe<R> {
    fn read(&mut self, buf: &mut [u8]) -> IoResult<usize> {
        let got = self.inner.read(buf)?;
        self.consumed += got;
        Ok(got)
    }
}

#[test]
fn truncated_payload_reports_progress() {
    let mut probe = probe_of(frame(10, b"abcd"));
    assert!(matches!(
        read_frame(&mut probe),
        Err(FrameError::TruncatedPayload {
            got: 4,
            expected: 10
        })
    ));
    assert_eq!(
        probe.consumed, 8,
        "header and the partial payload were consumed"
    );
}

#[test]
fn survives_one_byte_reads() {
    let mut drip = Drip(Cursor::new(frame(5, b"hello")));
    assert_eq!(read_frame(&mut drip).unwrap().unwrap().as_slice(), b"hello");
    assert!(read_frame(&mut drip).unwrap().is_none());
}

#[test]
fn writes_header_and_payload_in_one_write_then_flushes() {
    let mut writer = CountingWriter::default();
    write_frame(&mut writer, b"{\"a\":1}").unwrap();
    assert_eq!(
        writer.writes, 1,
        "a split write could interleave with another writer"
    );
    assert_eq!(writer.flushes, 1);
    assert_eq!(writer.bytes, frame(7, b"{\"a\":1}"));
}

#[test]
fn write_enforces_one_to_maximum_bytes() {
    let mut writer = CountingWriter::default();
    assert!(matches!(
        write_frame(&mut writer, b""),
        Err(FrameError::ZeroLength)
    ));
    assert!(write_frame(&mut writer, &vec![b'x'; FRAME_MAX_BYTES]).is_ok());
    let before = writer.bytes.len();
    assert!(matches!(
        write_frame(&mut writer, &vec![b'x'; FRAME_MAX_BYTES + 1]),
        Err(FrameError::Oversize { .. })
    ));
    assert_eq!(
        writer.bytes.len(),
        before,
        "nothing written for a rejected frame"
    );
}

#[test]
fn framed_hello_parses_after_read() {
    let json = r#"{"envelope_version":1,"type":"hello","request_id":"h1","api_versions":[1]}"#;
    let mut wire = Vec::new();
    write_frame(&mut wire, json.as_bytes()).expect("write within bounds");
    let payload = read_frame(&mut &wire[..]).expect("exact round trip");
    let payload = payload.expect("frame present");
    assert_eq!(payload.as_slice(), json.as_bytes());
    let parsed = parse_request(payload.as_slice(), Phase::PreReady).expect("framed hello parses");
    assert_eq!(parsed.body, RequestBody::Hello(Offer::Versions(vec![1])));
    let payload_again = payload;
    parse_request(payload_again.as_slice(), Phase::PreReady)
        .expect("payload stays readable until drop");
}

// ---- every serialized Event variant and all error-code names (exact semantic JSON) --------------

#[test]
fn every_event_variant_serializes_to_exact_semantic_json() {
    let record_bytes = [0x00, 0xff, 0x80, 0x81, 0x0d, 0xfe];
    let advert_bytes = [0x03u8, 0x0d, 0x03, 0x03];
    let cases: Vec<(Event, Option<u64>, Option<&str>, serde_json::Value)> = vec![
        (
            Event::Ready {
                request_id: "h1".into(),
                bridge_version: "veles-bridge-test-1.0.0".into(),
                platform: "windows",
                capabilities: vec!["veles_record_v1".into()],
            },
            Some(1),
            Some("h1"),
            serde_json::json!({"envelope_version":1,"api_version":1,"type":"ready","request_id":"h1","bridge_version":"veles-bridge-test-1.0.0","platform":"windows","capabilities":["veles_record_v1"]}),
        ),
        (
            Event::DeviceFound {
                scan_handle: HANDLE.into(),
                device_handle: "a000000000000002".into(),
                label: "Veles Phone".into(),
                platform_hint: Some("Veles Phone".into()),
                advertisement: Some(advert_bytes.to_vec()),
            },
            Some(1),
            None,
            serde_json::json!({"envelope_version":1,"api_version":1,"type":"device_found","scan_handle":HANDLE,"device_handle":"a000000000000002","label":"Veles Phone","platform_hint":"Veles Phone","advertisement":base64::encode(&advert_bytes)}),
        ),
        (
            Event::DeviceFound {
                scan_handle: HANDLE.into(),
                device_handle: "a000000000000002".into(),
                label: "Veles Phone".into(),
                platform_hint: None,
                advertisement: None,
            },
            Some(1),
            None,
            serde_json::json!({"envelope_version":1,"api_version":1,"type":"device_found","scan_handle":HANDLE,"device_handle":"a000000000000002","label":"Veles Phone"}),
        ),
        (
            Event::ScanState {
                request_id: Some("r-scan".into()),
                scan_handle: HANDLE.into(),
                started: true,
            },
            Some(1),
            Some("r-scan"),
            serde_json::json!({"envelope_version":1,"api_version":1,"type":"scan_state","request_id":"r-scan","scan_handle":HANDLE,"status":"started"}),
        ),
        (
            Event::ScanState {
                request_id: None,
                scan_handle: HANDLE.into(),
                started: false,
            },
            Some(1),
            None,
            serde_json::json!({"envelope_version":1,"api_version":1,"type":"scan_state","scan_handle":HANDLE,"status":"stopped"}),
        ),
        (
            Event::Connected {
                request_id: "r-conn".into(),
                connection_handle: HANDLE.into(),
                platform_hint: Some("Veles Phone".into()),
            },
            Some(1),
            Some("r-conn"),
            serde_json::json!({"envelope_version":1,"api_version":1,"type":"connected","request_id":"r-conn","connection_handle":HANDLE,"platform_hint":"Veles Phone"}),
        ),
        (
            Event::Connected {
                request_id: "r-conn".into(),
                connection_handle: HANDLE.into(),
                platform_hint: None,
            },
            Some(1),
            Some("r-conn"),
            serde_json::json!({"envelope_version":1,"api_version":1,"type":"connected","request_id":"r-conn","connection_handle":HANDLE}),
        ),
        (
            Event::Disconnected {
                request_id: Some("r-disc".into()),
                connection_handle: HANDLE.into(),
            },
            Some(1),
            Some("r-disc"),
            serde_json::json!({"envelope_version":1,"api_version":1,"type":"disconnected","request_id":"r-disc","connection_handle":HANDLE}),
        ),
        (
            Event::Disconnected {
                request_id: None,
                connection_handle: HANDLE.into(),
            },
            Some(1),
            None,
            serde_json::json!({"envelope_version":1,"api_version":1,"type":"disconnected","connection_handle":HANDLE}),
        ),
        (
            Event::Sent {
                request_id: "r-send".into(),
                connection_handle: HANDLE.into(),
            },
            Some(1),
            Some("r-send"),
            serde_json::json!({"envelope_version":1,"api_version":1,"type":"sent","request_id":"r-send","connection_handle":HANDLE}),
        ),
        (
            Event::Message {
                connection_handle: HANDLE.into(),
                record: Payload::new(record_bytes.to_vec()),
            },
            Some(1),
            None,
            serde_json::json!({"envelope_version":1,"api_version":1,"type":"message","connection_handle":HANDLE,"record_base64":base64::encode(&record_bytes)}),
        ),
        (
            Event::ShutdownComplete {
                request_id: "r-shut".into(),
            },
            Some(1),
            Some("r-shut"),
            serde_json::json!({"envelope_version":1,"api_version":1,"type":"shutdown_complete","request_id":"r-shut"}),
        ),
        (
            Event::Error {
                request_id: Some("r1".into()),
                code: ErrorCode::InvalidRequest,
                recoverability: Recoverability::Fatal,
                stage: "pre_ready".into(),
                scan_handle: None,
                connection_handle: Some(HANDLE.into()),
            },
            Some(1),
            Some("r1"),
            serde_json::json!({"envelope_version":1,"api_version":1,"type":"error","request_id":"r1","code":"invalid_request","recoverability":"fatal","stage":"pre_ready","connection_handle":HANDLE}),
        ),
        (
            Event::Error {
                request_id: None,
                code: ErrorCode::ScanFailed,
                recoverability: Recoverability::Fatal,
                stage: "scan".into(),
                scan_handle: Some(HANDLE.into()),
                connection_handle: None,
            },
            Some(1),
            None,
            serde_json::json!({"envelope_version":1,"api_version":1,"type":"error","code":"scan_failed","recoverability":"fatal","stage":"scan","scan_handle":HANDLE}),
        ),
        (
            Event::Error {
                request_id: None,
                code: ErrorCode::Internal,
                recoverability: Recoverability::Fatal,
                stage: "port_closed".into(),
                scan_handle: None,
                connection_handle: None,
            },
            None,
            None,
            serde_json::json!({"envelope_version":1,"type":"error","code":"internal","recoverability":"fatal","stage":"port_closed"}),
        ),
    ];
    for (event, api, correlated_id, expected) in cases {
        assert_eq!(event.request_id(), correlated_id);
        let wire = event.to_json(api);
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&wire).expect("wire is JSON"),
            expected
        );
        assert!(
            !String::from_utf8(wire)
                .expect("wire is UTF-8")
                .contains("null"),
            "optional fields are omitted, never null"
        );
    }
}

#[test]
fn every_error_code_has_its_wire_name() {
    let names = [
        (ErrorCode::IncompatibleApi, "incompatible_api"),
        (ErrorCode::InvalidRequest, "invalid_request"),
        (ErrorCode::UnknownHandle, "unknown_handle"),
        (ErrorCode::UnknownRequest, "unknown_request"),
        (ErrorCode::InvalidBase64, "invalid_base64"),
        (ErrorCode::OversizeRecord, "oversize_record"),
        (ErrorCode::CapExceeded, "cap_exceeded"),
        (ErrorCode::Busy, "busy"),
        (ErrorCode::ScanFailed, "scan_failed"),
        (ErrorCode::ConnectFailed, "connect_failed"),
        (ErrorCode::SendFailed, "send_failed"),
        (ErrorCode::Timeout, "timeout"),
        (ErrorCode::Internal, "internal"),
    ];
    for (code, name) in names {
        assert_eq!(code.as_str(), name);
        let error = Event::Error {
            request_id: Some("r".into()),
            code,
            recoverability: Recoverability::Fatal,
            stage: "stage".into(),
            scan_handle: None,
            connection_handle: None,
        };
        let wire = serde_json::from_slice::<serde_json::Value>(&error.to_json(Some(1)))
            .expect("wire is JSON");
        assert_eq!(wire["code"], name, "{name}");
    }
}

// ---- shared fixture vectors (protocol/v1/fixtures/bridge.json) ---------------------------------

fn bridge_fixture_envelope() -> serde_json::Value {
    let root = std::env::current_dir().expect("cargo sets the test cwd to the crate root");
    let path = root
        .join("..")
        .join("..")
        .join("protocol")
        .join("v1")
        .join("fixtures")
        .join("bridge.json");
    let raw = std::fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "read {} relative to {}: {e}",
            path.display(),
            root.display()
        )
    });
    serde_json::from_str(&raw).expect("fixture file is JSON")
}

/// Host-side rejection detail for the `kind: envelope, direction: request` vectors in
/// bridge.json whose `expected.accepted` is false. The fixtures' `stage` labels follow the Node
/// validator's taxonomy, not the host's stage vocabulary, so this table may only refine WHAT the
/// shared fixture cannot say: strict-intake failures (unparseable JSON, duplicate keys, wrong
/// envelope version, non-integer numbers) are uncorrelatable silent rejections, everything else
/// is a correlated `Rejection` per OTP2-BRD-010/010a/011 and the reviewed parser. The
/// accepted-versus-rejected class itself is never tabulated: it is asserted from each fixture's
/// `expected.accepted` directly against the parser outcome.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FixtureHostRejection {
    Uncorrelatable,
    Correlated(ErrorCode, &'static str),
}

fn shared_request_host_rejections() -> Vec<(&'static str, FixtureHostRejection)> {
    vec![
        (
            "brd-req-hello-both-offers",
            FixtureHostRejection::Correlated(ErrorCode::InvalidRequest, "negotiation"),
        ),
        (
            "brd-req-hello-no-offer",
            FixtureHostRejection::Correlated(ErrorCode::InvalidRequest, "negotiation"),
        ),
        (
            "brd-req-hello-api-versions-count-17-above-limit",
            FixtureHostRejection::Correlated(ErrorCode::InvalidRequest, "negotiation"),
        ),
        (
            "brd-req-hello-api-versions-unsorted",
            FixtureHostRejection::Correlated(ErrorCode::InvalidRequest, "negotiation"),
        ),
        (
            "brd-req-hello-api-versions-duplicate",
            FixtureHostRejection::Correlated(ErrorCode::InvalidRequest, "negotiation"),
        ),
        (
            "brd-req-hello-api-versions-noninteger",
            FixtureHostRejection::Uncorrelatable,
        ),
        (
            "brd-req-hello-range-inverted",
            FixtureHostRejection::Correlated(ErrorCode::InvalidRequest, "negotiation"),
        ),
        (
            "brd-req-pre-ready-start-scan-rejected",
            FixtureHostRejection::Correlated(ErrorCode::InvalidRequest, "pre_ready"),
        ),
        (
            "brd-req-pre-ready-unparseable-json-silent",
            FixtureHostRejection::Uncorrelatable,
        ),
        (
            "brd-req-pre-ready-duplicate-keys-raw-text",
            FixtureHostRejection::Uncorrelatable,
        ),
        (
            "brd-req-pre-ready-envelope-version-2",
            FixtureHostRejection::Uncorrelatable,
        ),
        (
            "brd-req-hello-nested-duplicate-keys",
            FixtureHostRejection::Uncorrelatable,
        ),
        (
            "brd-req-start-scan-bad-uuid",
            FixtureHostRejection::Correlated(ErrorCode::InvalidRequest, "schema"),
        ),
        (
            "brd-req-post-ready-api-version-mismatch",
            FixtureHostRejection::Correlated(ErrorCode::InvalidRequest, "api_version"),
        ),
        (
            "brd-req-post-ready-envelope-version-2",
            FixtureHostRejection::Uncorrelatable,
        ),
        (
            "brd-req-post-ready-unknown-type",
            FixtureHostRejection::Correlated(ErrorCode::UnknownRequest, "schema"),
        ),
        (
            "brd-req-post-ready-missing-request-id",
            FixtureHostRejection::Uncorrelatable,
        ),
        (
            "brd-req-post-ready-hello-duplicate",
            FixtureHostRejection::Correlated(ErrorCode::InvalidRequest, "post_ready"),
        ),
        (
            "brd-req-connect-both-handles",
            FixtureHostRejection::Correlated(ErrorCode::InvalidRequest, "schema"),
        ),
        (
            "brd-req-connect-neither-handle",
            FixtureHostRejection::Correlated(ErrorCode::InvalidRequest, "schema"),
        ),
        (
            "brd-req-request-id-65-over-limit",
            FixtureHostRejection::Uncorrelatable,
        ),
        (
            "brd-req-connect-platform-hint-257-over-limit",
            FixtureHostRejection::Correlated(ErrorCode::InvalidRequest, "schema"),
        ),
        (
            "brd-req-send-record-5121-over-limit",
            FixtureHostRejection::Correlated(ErrorCode::OversizeRecord, "record"),
        ),
        (
            "brd-req-send-record-empty",
            FixtureHostRejection::Correlated(ErrorCode::InvalidBase64, "record"),
        ),
        (
            "brd-req-send-base64-nonzero-pad-bits",
            FixtureHostRejection::Correlated(ErrorCode::InvalidBase64, "record"),
        ),
        (
            "brd-req-send-base64-whitespace",
            FixtureHostRejection::Correlated(ErrorCode::InvalidBase64, "record"),
        ),
        (
            "brd-req-send-base64-embedded-padding",
            FixtureHostRejection::Correlated(ErrorCode::InvalidBase64, "record"),
        ),
        (
            "brd-req-send-base64-odd-length",
            FixtureHostRejection::Correlated(ErrorCode::InvalidBase64, "record"),
        ),
        (
            "brd-req-send-base64-url-alphabet",
            FixtureHostRejection::Correlated(ErrorCode::InvalidBase64, "record"),
        ),
    ]
}

fn offer_from_fixture(value: &serde_json::Value) -> Offer {
    if let Some(list) = value["api_versions"].as_array() {
        Offer::Versions(
            list.iter()
                .map(|version| version.as_u64().expect("fixture api version"))
                .collect(),
        )
    } else {
        let range = &value["api_version_range"];
        Offer::Range {
            min: range["min"].as_u64().expect("fixture range min"),
            max: range["max"].as_u64().expect("fixture range max"),
        }
    }
}

fn fixture_body(value: &serde_json::Value) -> RequestBody {
    let str_field = |key: &str| {
        value[key]
            .as_str()
            .unwrap_or_else(|| panic!("fixture field {key}"))
            .to_string()
    };
    match value["type"].as_str().expect("fixture type") {
        "hello" => RequestBody::Hello(offer_from_fixture(value)),
        "start_scan" => RequestBody::StartScan {
            service_uuid: str_field("service_uuid"),
        },
        "stop_scan" => RequestBody::StopScan {
            scan_handle: str_field("scan_handle"),
        },
        "connect" => {
            if value["device_handle"].is_string() {
                RequestBody::Connect(Target::Device(str_field("device_handle")))
            } else {
                RequestBody::Connect(Target::Hint(str_field("platform_hint")))
            }
        }
        "disconnect" => RequestBody::Disconnect {
            connection_handle: str_field("connection_handle"),
        },
        "send" => {
            let record_text = str_field("record_base64");
            let record = base64::decode(&record_text, DECODED_RECORD_MAX_BYTES)
                .expect("fixture record decodes");
            RequestBody::Send {
                connection_handle: str_field("connection_handle"),
                record: Payload::new(record),
            }
        }
        "shutdown" => RequestBody::Shutdown,
        other => panic!("unknown fixture request type {other}"),
    }
}

#[test]
fn shared_bridge_request_fixtures_drive_phase_and_verdict() {
    let envelope = bridge_fixture_envelope();
    let host_rejections = shared_request_host_rejections();
    assert_eq!(
        host_rejections.len(),
        29,
        "every rejected request fixture is classified"
    );
    let mut accepted_checked = 0;
    let mut rejected_checked = 0;
    for entry in envelope["fixtures"].as_array().expect("fixtures array") {
        if entry["input"]["kind"] != serde_json::json!("envelope") {
            continue;
        }
        if entry["input"]["direction"] != serde_json::json!("request") {
            continue;
        }
        let id = entry["id"].as_str().expect("fixture id");
        let text = entry["input"]["text"].as_str().expect("fixture text");
        let phase = match entry["input"]["phase"].as_str().expect("fixture phase") {
            "pre" => Phase::PreReady,
            "ready" => Phase::Ready {
                api_version: entry["input"]["api_version"].as_u64().expect("api version"),
            },
            other => panic!("{id}: unknown phase {other}"),
        };
        let result = parse_request(text.as_bytes(), phase);
        let accepted_by_parser = result.is_ok();
        let expected_accepted = entry["expected"]["accepted"].as_bool().unwrap_or_else(|| {
            panic!("{id}: expected.accepted is required on every request fixture")
        });
        assert_eq!(
            accepted_by_parser, expected_accepted,
            "{id}: the parser's accepted/rejected class must follow expected.accepted"
        );
        let parsed_text: Option<serde_json::Value> = serde_json::from_str(text).ok();
        let request_id_in_text = parsed_text
            .as_ref()
            .and_then(|value| value["request_id"].as_str().map(str::to_string));
        if expected_accepted {
            accepted_checked += 1;
            assert!(
                !host_rejections
                    .iter()
                    .any(|(rejected_id, _)| *rejected_id == id),
                "{id}: accepted fixtures must never be tabulated as host rejections"
            );
            let request = result.unwrap_or_else(|failure| {
                panic!("{id}: expected accepted by expected.accepted, got {failure:?}")
            });
            assert_eq!(
                request.body,
                fixture_body(parsed_text.as_ref().expect("accepted fixtures are JSON")),
                "{id}"
            );
            assert_eq!(Some(request.request_id), request_id_in_text, "{id}");
        } else {
            rejected_checked += 1;
            let &(rejected_id, host_rejection) = host_rejections
                .iter()
                .find(|(rejected_id, _)| *rejected_id == id)
                .unwrap_or_else(|| panic!("rejected fixture {id} is not classified"));
            assert_eq!(rejected_id, id);
            match (host_rejection, result) {
                (FixtureHostRejection::Uncorrelatable, Err(ParseFailure::Uncorrelatable)) => {}
                (
                    FixtureHostRejection::Correlated(code, stage),
                    Err(ParseFailure::Rejected(rejection)),
                ) => {
                    assert_eq!(
                        (
                            rejection.request_id.as_str(),
                            rejection.code,
                            rejection.stage
                        ),
                        (
                            request_id_in_text.as_deref().expect("correlated id"),
                            code,
                            stage
                        ),
                        "{id}"
                    );
                    assert_eq!(rejection.recoverability, Recoverability::Fatal, "{id}");
                }
                (expected_rejection, other_result) => {
                    panic!("{id}: expected {expected_rejection:?}, got {other_result:?}")
                }
            }
        }
    }
    assert_eq!(
        accepted_checked, 12,
        "the accepted vectors drove the parser class"
    );
    assert_eq!(
        rejected_checked, 29,
        "the rejected vectors drove the parser class"
    );
}

#[test]
fn shared_bridge_negotiation_fixtures_select_the_highest_overlap() {
    let envelope = bridge_fixture_envelope();
    let mut selected = 0;
    for entry in envelope["fixtures"].as_array().expect("fixtures array") {
        if entry["input"]["kind"] != serde_json::json!("negotiation") {
            continue;
        }
        selected += 1;
        let id = entry["id"].as_str().expect("fixture id");
        assert_eq!(entry["expected"]["accepted"], true, "{id}");
        let offer = offer_from_fixture(&entry["input"]["offer"]);
        let supported: Vec<u64> = entry["input"]["host_supported_api"]
            .as_array()
            .expect("host_supported_api")
            .iter()
            .map(|version| version.as_u64().expect("fixture api version"))
            .collect();
        let selected_api = entry["input"]["selected_api"].as_u64();
        assert_eq!(
            offer.select(&supported),
            selected_api,
            "{id}: highest-overlap selection"
        );
    }
    assert_eq!(
        selected, 7,
        "the negotiation vectors drove the selection rule"
    );
}

#[test]
fn shared_bridge_event_fixtures_drive_exact_serialization() {
    let envelope = bridge_fixture_envelope();
    let mut serialized = 0;
    for entry in envelope["fixtures"].as_array().expect("fixtures array") {
        if entry["input"]["kind"] != serde_json::json!("envelope") {
            continue;
        }
        if entry["input"]["direction"] != serde_json::json!("event") {
            continue;
        }
        if entry["expected"]["accepted"] != serde_json::json!(true) {
            continue;
        }
        serialized += 1;
        let id = entry["id"].as_str().expect("fixture id");
        let text = entry["input"]["text"].as_str().expect("fixture text");
        let expected: serde_json::Value = serde_json::from_str(text).expect("accepted event JSON");
        let api = entry["input"]["api_version"].as_u64().expect("api version");
        let event = event_from_fixture(id, &expected);
        let wire = event.to_json(Some(api));
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&wire).expect("wire is JSON"),
            expected,
            "{id} serializes to the exact shared fixture JSON"
        );
        assert!(
            !String::from_utf8(wire)
                .expect("wire is UTF-8")
                .contains("null"),
            "{id}: no nulls on the wire"
        );
    }
    assert_eq!(
        serialized, 15,
        "every accepted shared event vector drove the serializer"
    );
}

fn event_from_fixture(id: &str, value: &serde_json::Value) -> Event {
    let string = |key: &str| {
        value[key]
            .as_str()
            .unwrap_or_else(|| panic!("{id}: event field {key}"))
            .to_string()
    };
    let opt_string = |key: &str| value[key].as_str().map(str::to_string);
    let base64_bytes = |key: &str| -> Option<Vec<u8>> {
        value[key]
            .as_str()
            .map(|text| base64::decode(text, DECODED_RECORD_MAX_BYTES).expect("fixture base64"))
    };
    match id {
        "brd-ev-ready-valid" => Event::Ready {
            request_id: string("request_id"),
            bridge_version: string("bridge_version"),
            platform: if value["platform"] == serde_json::json!("windows") {
                "windows"
            } else {
                "macos"
            },
            capabilities: value["capabilities"]
                .as_array()
                .expect("capabilities")
                .iter()
                .map(|capability| capability.as_str().expect("capability").to_string())
                .collect(),
        },
        "brd-ev-device-found-valid"
        | "brd-ev-device-found-advert-256-at-limit"
        | "brd-ev-device-found-label-64-at-limit" => Event::DeviceFound {
            scan_handle: string("scan_handle"),
            device_handle: string("device_handle"),
            label: string("label"),
            platform_hint: opt_string("platform_hint"),
            advertisement: base64_bytes("advertisement"),
        },
        "brd-ev-message-binary-record-valid" | "brd-ev-message-binary-round-trip-preserved" => {
            let record_text = string("record_base64");
            Event::Message {
                connection_handle: string("connection_handle"),
                record: Payload::new(
                    base64::decode(&record_text, DECODED_RECORD_MAX_BYTES).expect("record"),
                ),
            }
        }
        "brd-ev-scan-state-started-correlated" | "brd-ev-scan-state-stopped-unsolicited" => {
            Event::ScanState {
                request_id: opt_string("request_id"),
                scan_handle: string("scan_handle"),
                started: value["status"] == serde_json::json!("started"),
            }
        }
        "brd-ev-connected-valid" => Event::Connected {
            request_id: string("request_id"),
            connection_handle: string("connection_handle"),
            platform_hint: opt_string("platform_hint"),
        },
        "brd-ev-disconnected-correlated" | "brd-ev-disconnected-unsolicited" => {
            Event::Disconnected {
                request_id: opt_string("request_id"),
                connection_handle: string("connection_handle"),
            }
        }
        "brd-ev-shutdown-complete-valid" => Event::ShutdownComplete {
            request_id: string("request_id"),
        },
        "brd-ev-error-correlated-valid"
        | "brd-ev-error-unsolicited-valid"
        | "brd-ev-error-stage-32-at-limit" => Event::Error {
            request_id: opt_string("request_id"),
            code: error_code_from_wire(
                id,
                value["code"]
                    .as_str()
                    .unwrap_or_else(|| panic!("{id}: code")),
            ),
            recoverability: if value["recoverability"] == serde_json::json!("retryable") {
                Recoverability::Retryable
            } else {
                Recoverability::Fatal
            },
            stage: string("stage"),
            scan_handle: opt_string("scan_handle"),
            connection_handle: opt_string("connection_handle"),
        },
        other => panic!("{other} is not an accepted shared event vector this suite builds"),
    }
}

fn error_code_from_wire(id: &str, name: &str) -> ErrorCode {
    match name {
        "incompatible_api" => ErrorCode::IncompatibleApi,
        "invalid_request" => ErrorCode::InvalidRequest,
        "unknown_handle" => ErrorCode::UnknownHandle,
        "unknown_request" => ErrorCode::UnknownRequest,
        "invalid_base64" => ErrorCode::InvalidBase64,
        "oversize_record" => ErrorCode::OversizeRecord,
        "cap_exceeded" => ErrorCode::CapExceeded,
        "busy" => ErrorCode::Busy,
        "scan_failed" => ErrorCode::ScanFailed,
        "connect_failed" => ErrorCode::ConnectFailed,
        "send_failed" => ErrorCode::SendFailed,
        "timeout" => ErrorCode::Timeout,
        "internal" => ErrorCode::Internal,
        other => panic!("{id}: unknown error code {other}"),
    }
}
