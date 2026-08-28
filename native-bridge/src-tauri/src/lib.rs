// OTP-01 sub-project 1c — Veles Native Messaging host (headless).
//
// This is the protocol layer: frame-decode from stdin, dispatch, frame-encode
// to stdout. The runtime (Tauri app shell) lives in `main.rs` and is gated
// behind the `tauri-runtime` feature.
//
// Native Messaging framing (Chrome spec): each message is preceded by a
// 4-byte little-endian length header giving the JSON payload length in bytes.
// On stdin EOF the host returns immediately.

use std::io::{Read, Write};

/// Read a single framed Native Messaging message from `reader`.
/// Returns `Ok(None)` on clean EOF (no bytes read), `Ok(Some(bytes))` on a
/// complete message, or `Err` on a truncated frame.
pub fn read_message<R: Read>(reader: &mut R) -> Result<Option<Vec<u8>>, FrameError> {
    let mut header = [0u8; 4];
    // Read the first byte separately so a clean EOF (no bytes at all) is
    // distinguished from a truncated frame. `Read::read` may legally return
    // fewer than four bytes while more data remains (e.g. on a pipe), so the
    // remaining three header bytes are filled with an explicit loop rather
    // than a single `read`.
    let n = reader.read(&mut header[..1])?;
    if n == 0 {
        return Ok(None);
    }
    // First byte is present; from here a short read is a truncated frame.
    let mut filled = 1;
    while filled < 4 {
        let r = reader.read(&mut header[filled..4])?;
        if r == 0 {
            return Err(FrameError::TruncatedHeader(filled));
        }
        filled += r;
    }
    let len = u32::from_le_bytes(header) as usize;
    if len == 0 {
        return Ok(Some(Vec::new()));
    }
    let mut payload = vec![0u8; len];
    let mut read = 0;
    while read < len {
        let r = reader.read(&mut payload[read..])?;
        if r == 0 {
            return Err(FrameError::TruncatedPayload(read, len));
        }
        read += r;
    }
    Ok(Some(payload))
}

/// Write a single framed Native Messaging message to `writer`.
pub fn write_message<W: Write>(writer: &mut W, payload: &[u8]) -> Result<(), FrameError> {
    let len =
        u32::try_from(payload.len()).map_err(|_| FrameError::PayloadTooLong(payload.len()))?;
    writer.write_all(&len.to_le_bytes())?;
    writer.write_all(payload)?;
    writer.flush()?;
    Ok(())
}

/// Run the host loop: read framed messages until EOF, dispatch each, write
/// responses. An empty/dispatchable payload is ignored; an EOF at any point
/// ends the loop cleanly. The placeholder implementation echoes nothing;
/// OTP-07 wires the real protocol.
pub fn run<R: Read, W: Write>(reader: R, writer: &mut W) -> Result<(), FrameError> {
    let mut reader = reader;
    loop {
        match read_message(&mut reader)? {
            None => return Ok(()),
            Some(payload) => {
                let _ = payload;
                let _ = writer;
            }
        }
    }
}

#[derive(Debug)]
pub enum FrameError {
    TruncatedHeader(usize),
    TruncatedPayload(usize, usize),
    PayloadTooLong(usize),
    Io(std::io::Error),
}

impl From<std::io::Error> for FrameError {
    fn from(e: std::io::Error) -> Self {
        FrameError::Io(e)
    }
}
