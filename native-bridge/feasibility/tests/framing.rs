//! Chrome Native Messaging framing: caps, truncation, partial reads, single-write framing.

use std::io::{self, Cursor, Read, Write};

use veles_otp23_feasibility::framing::{FrameError, MAX_FRAME_BYTES, read_frame, write_frame};

fn frame(len_header: u32, body: &[u8]) -> Vec<u8> {
    let mut out = len_header.to_le_bytes().to_vec();
    out.extend_from_slice(body);
    out
}

/// Yields one byte per `read` call, like a slow pipe.
struct Drip(Cursor<Vec<u8>>);
impl Read for Drip {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let n = buf.len().min(1);
        self.0.read(&mut buf[..n])
    }
}

/// Panics if asked for more bytes than the 4-byte header: proves oversize is rejected unread.
struct HeaderOnly(Cursor<Vec<u8>>);
impl Read for HeaderOnly {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
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
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.writes += 1;
        self.bytes.extend_from_slice(buf);
        Ok(buf.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        self.flushes += 1;
        Ok(())
    }
}

#[test]
fn reads_consecutive_frames_then_clean_eof() {
    let mut bytes = frame(2, b"{}");
    bytes.extend(frame(3, b"abc"));
    let mut cursor = Cursor::new(bytes);
    assert_eq!(read_frame(&mut cursor).unwrap().unwrap(), b"{}");
    assert_eq!(read_frame(&mut cursor).unwrap().unwrap(), b"abc");
    assert!(read_frame(&mut cursor).unwrap().is_none());
}

#[test]
fn accepts_exactly_the_maximum_frame() {
    let body = vec![b'x'; MAX_FRAME_BYTES];
    let mut cursor = Cursor::new(frame(MAX_FRAME_BYTES as u32, &body));
    assert_eq!(
        read_frame(&mut cursor).unwrap().unwrap().len(),
        MAX_FRAME_BYTES
    );
}

#[test]
fn rejects_one_byte_over_the_maximum_without_reading_the_payload() {
    let declared = MAX_FRAME_BYTES as u32 + 1;
    let mut reader = HeaderOnly(Cursor::new(frame(declared, &vec![b'x'; 70_000])));
    assert!(matches!(
        read_frame(&mut reader),
        Err(FrameError::Oversize { declared: d }) if d == u64::from(declared)
    ));
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
fn rejects_zero_length_frame() {
    let mut cursor = Cursor::new(frame(0, b""));
    assert!(matches!(
        read_frame(&mut cursor),
        Err(FrameError::ZeroLength)
    ));
}

#[test]
fn truncated_header_is_distinguished_from_clean_eof() {
    for got in 1..=3usize {
        let mut cursor = Cursor::new(vec![1u8; got]);
        assert!(
            matches!(read_frame(&mut cursor), Err(FrameError::TruncatedHeader { got: g }) if g == got),
            "{got} header bytes"
        );
    }
}

#[test]
fn truncated_payload_reports_progress() {
    let mut cursor = Cursor::new(frame(10, b"abcd"));
    assert!(matches!(
        read_frame(&mut cursor),
        Err(FrameError::TruncatedPayload {
            got: 4,
            expected: 10
        })
    ));
}

#[test]
fn survives_one_byte_reads() {
    let mut drip = Drip(Cursor::new(frame(5, b"hello")));
    assert_eq!(read_frame(&mut drip).unwrap().unwrap(), b"hello");
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
    assert!(write_frame(&mut writer, &vec![b'x'; MAX_FRAME_BYTES]).is_ok());
    let before = writer.bytes.len();
    assert!(matches!(
        write_frame(&mut writer, &vec![b'x'; MAX_FRAME_BYTES + 1]),
        Err(FrameError::Oversize { .. })
    ));
    assert_eq!(
        writer.bytes.len(),
        before,
        "nothing written for a rejected frame"
    );
}
