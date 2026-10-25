//! Strict JSON intake, canonical base64 and the request/event envelope (OTP2-BRD-002/003/010/011).

use veles_otp23_feasibility::base64::{self, Base64Error};
use veles_otp23_feasibility::protocol::{
    ErrorCode, Event, Offer, ParseFailure, Phase, Recoverability, Rejection, Request, RequestBody,
    Target, parse_request,
};
use veles_otp23_feasibility::strict_json::{JsonError, parse_strict};

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
    assert_eq!(base64::decode("Zh==", 8), Err(Base64Error::NonCanonical)); // trailing bits set
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
                r#"{{"envelope_version":1,"api_version":1,"type":"stop_scan","request_id":"r","scan_handle":"{HANDLE}"}}"#
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
            RequestBody::Send { connection_handle: HANDLE.into(), record: vec![0x56, 0x45, 0x4c, 0x01] },
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

// ---- events -----------------------------------------------------------------------------------

fn json(event: &Event, api: Option<u64>) -> serde_json::Value {
    serde_json::from_slice(&event.to_json(api)).unwrap()
}

#[test]
fn correlated_events_carry_request_id_and_unsolicited_events_do_not() {
    let sent = Event::Sent {
        request_id: "r".into(),
        connection_handle: HANDLE.into(),
    };
    assert_eq!(sent.request_id(), Some("r"));
    assert_eq!(
        json(&sent, Some(1)),
        serde_json::json!({"envelope_version":1,"api_version":1,"type":"sent","request_id":"r","connection_handle":HANDLE})
    );
    let message = Event::Message {
        connection_handle: HANDLE.into(),
        record: vec![0x56, 0x45, 0x4c, 0x02],
    };
    assert_eq!(message.request_id(), None);
    let value = json(&message, Some(1));
    assert_eq!(value["record_base64"], "VkVMAg==");
    assert!(value.get("request_id").is_none());
    let lost = Event::Disconnected {
        request_id: None,
        connection_handle: HANDLE.into(),
    };
    assert!(json(&lost, Some(1)).get("request_id").is_none());
    let asked = Event::Disconnected {
        request_id: Some("d".into()),
        connection_handle: HANDLE.into(),
    };
    assert_eq!(json(&asked, Some(1))["request_id"], "d");
}

#[test]
fn optional_fields_are_omitted_never_null() {
    let device = Event::DeviceFound {
        scan_handle: HANDLE.into(),
        device_handle: "fedcba9876543210".into(),
        label: "Phone".into(),
        platform_hint: None,
        advertisement: None,
    };
    let value = json(&device, Some(1));
    assert!(value.get("platform_hint").is_none() && value.get("advertisement").is_none());
    let error = Event::Error {
        request_id: None,
        code: ErrorCode::ScanFailed,
        recoverability: Recoverability::Retryable,
        stage: "radio_off".into(),
        scan_handle: Some(HANDLE.into()),
        connection_handle: None,
    };
    let value = json(&error, Some(1));
    assert_eq!(value["code"], "scan_failed");
    assert_eq!(value["recoverability"], "retryable");
    assert!(value.get("connection_handle").is_none() && value.get("request_id").is_none());
    assert!(
        !String::from_utf8(error.to_json(Some(1)))
            .unwrap()
            .contains("null")
    );
}

#[test]
fn pre_negotiation_error_has_no_api_version() {
    let error = Event::Error {
        request_id: Some("h".into()),
        code: ErrorCode::IncompatibleApi,
        recoverability: Recoverability::Fatal,
        stage: "negotiation".into(),
        scan_handle: None,
        connection_handle: None,
    };
    let value = json(&error, None);
    assert_eq!(value["envelope_version"], 1);
    assert!(value.get("api_version").is_none());
    assert_eq!(value["code"], "incompatible_api");
}

#[test]
fn ready_reports_selected_version_and_capabilities() {
    let ready = Event::Ready {
        request_id: "h".into(),
        bridge_version: "x/1".into(),
        platform: "windows",
        capabilities: vec!["veles_record_v1".into()],
    };
    assert_eq!(
        json(&ready, Some(1)),
        serde_json::json!({"envelope_version":1,"api_version":1,"type":"ready","request_id":"h","bridge_version":"x/1","platform":"windows","capabilities":["veles_record_v1"]})
    );
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
    }
}
