//! OTP-02 Plan Task 6 shared-fixture consumer (host-runnable, no JNI/WASM).
//!
//! Reads the exact same fixture files every other language consumer reads:
//! the repo-root `protocol/v1/` directory, resolved from this crate's
//! manifest path — no copied fixture truth. Asserts byte-for-byte
//! hex/base64 decoding and canonical u64 decimal-string preservation.
//!
//! Test-only: never compiled into any product binary and never imported by
//! product code.
//!
//! OTP-03 hooks named explicitly (not implemented here): the real SPAKE2
//! exchange/confirmation (OTP2-CRY-001..006), the enrollment/reconnect HKDF
//! schedules (OTP2-CRY-007/019/REC-020b), the AES-256-GCM record codec
//! (OTP2-CRY-016) and the state machines (OTP2-STA/REC) will implement the
//! exact wire rules in the watch crate / JNI core; those implementations will
//! consume THESE committed vectors (rustFixtures tests re-check
//! veles-record-*/veles-neg-* bytes) as their conformance evidence — OTP-02
//! evidence stops at this shared-fixture byte-level surface.

#![allow(clippy::arithmetic_side_effects)]
// Test-only helpers over schema-validated fixture data: byte offsets and
// base64/hex indices stay far below any overflow bound, so the workspace's
// production arithmetic-safety lint is deliberately lifted in this consumer.

use std::fs;
use std::path::PathBuf;

use serde_json::Value;

const U64_MAX_DECIMAL: &str = "18446744073709551615";
const U64_OVERFLOW_DECIMAL: &str = "18446744073709551616";
const BASE64_ROUND_TRIP_STRING: &str = "AP+AgQ3+";
const BASE64_ROUND_TRIP_BYTES: [u8; 6] = [0x00, 0xff, 0x80, 0x81, 0x0d, 0xfe];
const BASE64_SIMPLE_VALID: &str = "AQ==";
const BASE64_SIMPLE_VALID_BYTES: [u8; 1] = [0x01];

fn protocol_root() -> PathBuf {
    // rust/veles-crypto/tests → protocol/v1 at the repository root.
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../protocol/v1")
}

fn root_json(name: &str) -> Value {
    let path = protocol_root().join(name);
    let text = fs::read_to_string(&path).unwrap_or_else(|err| {
        panic!(
            "missing protocol resource {} ({}): {}",
            path.display(),
            name,
            err
        )
    });
    serde_json::from_str(&text).unwrap_or_else(|err| panic!("malformed JSON in {}: {}", name, err))
}

fn fixture_envelope(name: &str) -> Value {
    let path = protocol_root().join("fixtures").join(name);
    let text = fs::read_to_string(&path).unwrap_or_else(|err| {
        panic!(
            "missing shared fixture {} ({}): {}",
            path.display(),
            name,
            err
        )
    });
    serde_json::from_str(&text)
        .unwrap_or_else(|err| panic!("malformed fixture JSON in {}: {}", name, err))
}

fn fixtures_of(name: &str) -> Vec<Value> {
    let envelope = fixture_envelope(name);
    assert_eq!(
        envelope["version"],
        serde_json::json!(1),
        "fixture envelope version"
    );
    envelope["fixtures"]
        .as_array()
        .unwrap_or_else(|| panic!("fixture envelope {} must carry a fixtures array", name))
        .clone()
}

fn fixture_by_id(id: &str, names: &[&str]) -> Option<Value> {
    names
        .iter()
        .flat_map(|name| fixtures_of(name))
        .find(|fixture| fixture["id"] == serde_json::json!(id))
}

fn expect_fixture(id: &str, names: &[&str]) -> Value {
    match fixture_by_id(id, names) {
        Some(fixture) => fixture,
        None => panic!("missing fixture id {}", id),
    }
}

fn field_str(value: &Value, key: &str) -> String {
    match value[key].as_str() {
        Some(text) => text.to_string(),
        None => panic!("expected required JSON string field {}", key),
    }
}

// ---------- canonical byte codecs (mirror checks/binary.mjs decodeHex) ----------

fn decode_hex(text: &str) -> Result<Vec<u8>, String> {
    if !text.len().is_multiple_of(2) {
        return Err(format!(
            "malformed hex byte string (odd length): \"{}\"",
            text
        ));
    }
    if !text
        .chars()
        .all(|character| character.is_ascii_digit() || ('a'..='f').contains(&character))
    {
        return Err(format!(
            "malformed hex byte string (not lowercase hex): \"{}\"",
            text
        ));
    }
    (0..text.len())
        .step_by(2)
        .map(|start| u8::from_str_radix(&text[start..start + 2], 16))
        .collect::<Result<Vec<u8>, _>>()
        .map_err(|err| format!("malformed hex byte string: {}", err))
}

fn encode_hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{:02x}", byte)).collect()
}

const BASE64_ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

// Canonical RFC 4648 decoding only (mirrors the envelope validator's policy):
// multiples of 4, standard alphabet, trailing padding, and zero pad bits.
fn decode_base64(text: &str) -> Result<Vec<u8>, String> {
    let bytes = text.as_bytes();
    let malformed = |reason: String| format!("malformed base64 record: {} (\"{}\")", reason, text);
    if !bytes.len().is_multiple_of(4) {
        return Err(malformed("length is not a multiple of 4".to_string()));
    }
    let pad_count = bytes.iter().rev().take_while(|&&byte| byte == b'=').count();
    if pad_count < bytes.iter().filter(|&&byte| byte == b'=').count() {
        return Err(malformed("padding must only be at the end".to_string()));
    }
    if pad_count > 2 {
        return Err(malformed("more than two pad characters".to_string()));
    }
    if !bytes
        .iter()
        .all(|&byte| byte == b'=' || BASE64_ALPHABET.contains(&byte))
    {
        return Err(malformed("non-canonical alphabet".to_string()));
    }
    let mut out = Vec::with_capacity(bytes.len() / 4 * 3 - pad_count);
    for group in bytes.chunks(4) {
        let mut indexes = [0usize; 4];
        for (index, &byte) in group.iter().enumerate() {
            indexes[index] = if byte == b'=' {
                0
            } else {
                match BASE64_ALPHABET.iter().position(|&entry| entry == byte) {
                    Some(position) => position,
                    None => return Err(malformed("non-canonical alphabet".to_string())),
                }
            };
        }
        let grouped = ((indexes[0] as u32) << 18)
            | ((indexes[1] as u32) << 12)
            | ((indexes[2] as u32) << 6)
            | (indexes[3] as u32);
        let group_pads = group.iter().filter(|&&byte| byte == b'=').count();
        for byte in grouped.to_be_bytes()[1..4 - group_pads].iter() {
            out.push(*byte);
        }
    }
    if encode_base64(&out) != text {
        return Err(malformed("non-canonical pad bits".to_string()));
    }
    Ok(out)
}

fn encode_base64(bytes: &[u8]) -> String {
    let mut text = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for group in bytes.chunks(3) {
        let value = ((group[0] as u32) << 16)
            | (u32::from(*group.get(1).unwrap_or(&0)) << 8)
            | u32::from(*group.get(2).unwrap_or(&0));
        text.push(BASE64_ALPHABET[((value >> 18) & 0x3f) as usize] as char);
        text.push(BASE64_ALPHABET[((value >> 12) & 0x3f) as usize] as char);
        text.push(if group.len() > 1 {
            BASE64_ALPHABET[((value >> 6) & 0x3f) as usize] as char
        } else {
            '='
        });
        text.push(if group.len() > 2 {
            BASE64_ALPHABET[(value & 0x3f) as usize] as char
        } else {
            '='
        });
    }
    text
}

// ---------- helpers over fixture navigation ----------

fn accepted_true(fixture: &Value) -> bool {
    fixture["expected"]["accepted"].as_bool() == Some(true)
}

fn rejected_true(fixture: &Value) -> bool {
    fixture["expected"]["rejected"].as_bool() == Some(true)
}

// ---------- tests ----------

#[test]
fn encoding_and_crypto_fixture_ids_load_from_the_shared_protocol_root() {
    let encoding = fixtures_of("encoding.json");
    assert!(!encoding.is_empty());
    let mut ids: Vec<String> = encoding
        .iter()
        .map(|fixture| field_str(fixture, "id"))
        .collect();
    ids.sort();
    let duplicates = ids.len();
    ids.dedup();
    assert_eq!(ids.len(), duplicates, "duplicate encoding fixture ids");
    assert!(ids.iter().any(|id| id == "enc-record-push-max-seq-u64"));
    let crypto = fixtures_of("crypto.json");
    assert!(!crypto.is_empty());
    let crypto_ids: Vec<String> = crypto
        .iter()
        .map(|fixture| field_str(fixture, "id"))
        .collect();
    assert!(
        crypto_ids
            .iter()
            .any(|id| id == "rfc9382-p256-sha256-hkdf-hmac-server-client")
    );
    assert!(crypto_ids.iter().any(|id| id == "veles-record-push-event"));
}

#[test]
fn accepted_hex_fixtures_decode_byte_for_byte_and_re_encode_canonically() {
    for fixture in fixtures_of("encoding.json") {
        if !accepted_true(&fixture) {
            continue;
        }
        let decoded = decode_hex(&field_str(&fixture["input"], "bytes")).expect("hex decoding");
        assert_eq!(field_str(&fixture["input"], "bytes"), encode_hex(&decoded));
    }
}

#[test]
fn u64_decimal_strings_are_preserved_exactly_by_the_fixture_json() {
    let max_sequence = field_str(
        &expect_fixture("enc-record-push-max-seq-u64", &["encoding.json"])["expected"]["fields"],
        "sequence_number",
    );
    assert_eq!(U64_MAX_DECIMAL, max_sequence);
    match max_sequence.parse::<u64>() {
        Ok(value) => assert_eq!(value, u64::MAX),
        Err(_) => panic!("u64 decimal string no longer parses"),
    }
    assert!(
        U64_OVERFLOW_DECIMAL.parse::<u64>().is_err(),
        "the value one beyond the u64 maximum must not parse"
    );
    let max_receipt = field_str(
        &expect_fixture("enc-body-otp-event-max-bounds", &["encoding.json"])["expected"]["fields"],
        "receipt_epoch_seconds",
    );
    assert_eq!(U64_MAX_DECIMAL, max_receipt);
    // limits.json carries sequence_max as the canonical decimal string.
    let limits = root_json("limits.json");
    let sequence_max = limits["limits"]
        .as_array()
        .unwrap()
        .iter()
        .find(|limit| limit["name"] == "sequence_max")
        .unwrap();
    assert_eq!(U64_MAX_DECIMAL, field_str(sequence_max, "value"));
    // The u64-maximum sequence state trace keeps the full decimal string.
    let state = expect_fixture("st-rec-max-u64-sequence-then-terminated", &["records.json"]);
    assert!(
        serde_json::to_string(&state)
            .unwrap()
            .contains(U64_MAX_DECIMAL),
        "state trace lost the canonical u64 decimal string"
    );
}

#[test]
fn malformed_hex_byte_strings_fail_to_decode() {
    for text in ["AB", "abc", "0g00", "dead bee", "0xzz"] {
        let decoded = decode_hex(text);
        assert!(
            decoded.is_err(),
            "decode_hex accepted malformed hex: \"{}\"",
            text
        );
    }
}

#[test]
fn canonical_base64_records_decode_byte_for_byte() {
    assert_eq!(
        BASE64_ROUND_TRIP_BYTES.to_vec(),
        decode_base64(BASE64_ROUND_TRIP_STRING).unwrap()
    );
    assert_eq!(
        BASE64_SIMPLE_VALID_BYTES.to_vec(),
        decode_base64(BASE64_SIMPLE_VALID).unwrap()
    );
    let round_trip = expect_fixture(
        "brd-ev-message-binary-round-trip-preserved",
        &["bridge.json"],
    );
    let embedded = field_str(&round_trip["input"], "text");
    let envelope: Value = serde_json::from_str(&embedded).expect("valid envelope JSON");
    assert_eq!(
        BASE64_ROUND_TRIP_STRING,
        envelope["record_base64"].as_str().unwrap()
    );
}

#[test]
fn malformed_base64_records_taken_from_the_shared_fixtures_fail_to_decode() {
    for id in [
        "brd-req-send-base64-odd-length",
        "brd-req-send-base64-whitespace",
        "brd-req-send-base64-url-alphabet",
    ] {
        let fixture = expect_fixture(id, &["bridge.json"]);
        let text = field_str(&fixture["input"], "text");
        let envelope: Value = serde_json::from_str(&text).expect("valid envelope JSON");
        let record_base64 = field_str(&envelope, "record_base64");
        assert!(
            decode_base64(&record_base64).is_err(),
            "decode_base64 accepted malformed base64 from {}: \"{}\"",
            id,
            record_base64
        );
    }
}

#[test]
fn crypto_fixture_outputs_decode_byte_for_byte_and_negatives_bind_to_existing_positives() {
    let crypto = fixtures_of("crypto.json");
    let positive_ids: Vec<String> = crypto
        .iter()
        .filter(|fixture| !rejected_true(fixture))
        .map(|fixture| field_str(fixture, "id"))
        .collect();
    assert!(
        positive_ids
            .iter()
            .any(|id| id.starts_with("rfc9382-p256-sha256-hkdf-hmac-")),
        "the RFC 9382 published anchors must be present"
    );
    let mut hex_byte_outputs = 0usize;
    for fixture in &crypto {
        let id = field_str(fixture, "id");
        if rejected_true(fixture) {
            let base = field_str(&fixture["input"]["deviation"], "negates");
            assert!(
                positive_ids.iter().any(|positive| positive == &base),
                "negative \"{}\" references missing positive \"{}\"",
                id,
                base
            );
        } else {
            for (name, output) in fixture["expected"]["outputs"]
                .as_object()
                .expect("outputs object")
            {
                let value = match output.as_str() {
                    Some(text) => text,
                    None => panic!("crypto output {}.{} must be a JSON string", id, name),
                };
                if let Some(hex) = value.strip_prefix("0x") {
                    assert_eq!(
                        *value,
                        format!("0x{}", encode_hex(&decode_hex(hex).expect("hex output")))
                    );
                    hex_byte_outputs += 1;
                }
                // Other committed outputs are decimal lengths or display
                // strings — they are not byte strings and pass through.
            }
        }
    }
    assert!(
        hex_byte_outputs > 0,
        "crypto fixtures must carry 0x-prefixed hex byte outputs"
    );
}

#[test]
fn missing_fixture_ids_fail_for_every_consumer_file() {
    for file in ["encoding.json", "crypto.json", "bridge.json"] {
        assert!(
            fixture_by_id("this-fixture-must-not-exist", &[file]).is_none(),
            "lookup must not find fixtures that do not exist"
        );
        let panic =
            std::panic::catch_unwind(|| expect_fixture("this-fixture-must-not-exist", &[file]));
        assert!(
            panic.is_err(),
            "missing fixture lookup in {file} must fail loudly"
        );
    }
}
