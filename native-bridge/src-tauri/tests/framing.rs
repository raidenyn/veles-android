// OTP-01 sub-project 1c — Native Messaging framing protocol tests.
//
// Chrome's Native Messaging spec uses 4-byte little-endian length headers.
// These tests assert the framing layer round-trips correctly and handles
// truncated frames and zero-length messages.

use std::io::Cursor;
use veles_native_bridge::{FrameError, read_message, write_message};

#[test]
fn round_trips_a_single_message() {
    let mut buf = Vec::new();
    write_message(&mut buf, b"{\"hello\":\"world\"}").unwrap();
    let mut reader = Cursor::new(buf);
    let msg = read_message(&mut reader).unwrap();
    assert_eq!(msg, Some(b"{\"hello\":\"world\"}".to_vec()));
    let next = read_message(&mut reader).unwrap();
    assert_eq!(next, None);
}

#[test]
fn round_trips_multiple_messages_in_order() {
    let mut buf = Vec::new();
    write_message(&mut buf, b"first").unwrap();
    write_message(&mut buf, b"second").unwrap();
    write_message(&mut buf, b"third").unwrap();
    let mut reader = Cursor::new(buf);
    assert_eq!(read_message(&mut reader).unwrap(), Some(b"first".to_vec()));
    assert_eq!(read_message(&mut reader).unwrap(), Some(b"second".to_vec()));
    assert_eq!(read_message(&mut reader).unwrap(), Some(b"third".to_vec()));
    assert_eq!(read_message(&mut reader).unwrap(), None);
}

#[test]
fn zero_length_message_round_trips() {
    let mut buf = Vec::new();
    write_message(&mut buf, b"").unwrap();
    let mut reader = Cursor::new(buf);
    assert_eq!(read_message(&mut reader).unwrap(), Some(Vec::new()));
    assert_eq!(read_message(&mut reader).unwrap(), None);
}

#[test]
fn truncated_header_returns_error() {
    let mut reader = Cursor::new([0x01, 0x02]);
    let err = read_message(&mut reader).unwrap_err();
    assert!(matches!(err, FrameError::TruncatedHeader(2)));
}

#[test]
fn truncated_payload_returns_error() {
    let mut buf = Vec::new();
    buf.extend_from_slice(&100u32.to_le_bytes());
    buf.extend_from_slice(b"short");
    let mut reader = Cursor::new(buf);
    let err = read_message(&mut reader).unwrap_err();
    assert!(matches!(err, FrameError::TruncatedPayload(read, 100) if read == 5));
}
