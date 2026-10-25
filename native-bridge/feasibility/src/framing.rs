//! Chrome Native Messaging framing: `u32le length || UTF-8 JSON` (OTP2-BRD-001).

use std::io::{self, ErrorKind, Read, Write};

/// Both directions accept `1..=MAX_FRAME_BYTES` payload bytes.
pub const MAX_FRAME_BYTES: usize = 65_536;

#[derive(Debug)]
pub enum FrameError {
    Io(io::Error),
    /// EOF after 1..=3 header bytes.
    TruncatedHeader {
        got: usize,
    },
    /// EOF inside a declared payload.
    TruncatedPayload {
        got: usize,
        expected: usize,
    },
    ZeroLength,
    /// Declared length above [`MAX_FRAME_BYTES`]; rejected before any payload allocation.
    Oversize {
        declared: u64,
    },
}

impl From<io::Error> for FrameError {
    fn from(error: io::Error) -> Self {
        FrameError::Io(error)
    }
}

/// Fills `buf` as far as possible; returns the number of bytes read (short only at EOF).
fn read_full<R: Read>(reader: &mut R, buf: &mut [u8]) -> Result<usize, FrameError> {
    let mut filled = 0;
    while filled < buf.len() {
        match reader.read(&mut buf[filled..]) {
            Ok(0) => break,
            Ok(n) => filled += n,
            Err(e) if e.kind() == ErrorKind::Interrupted => {}
            Err(e) => return Err(FrameError::Io(e)),
        }
    }
    Ok(filled)
}

/// Reads one frame. `Ok(None)` is a clean EOF exactly on a frame boundary.
pub fn read_frame<R: Read>(reader: &mut R) -> Result<Option<Vec<u8>>, FrameError> {
    let mut header = [0u8; 4];
    match read_full(reader, &mut header)? {
        0 => return Ok(None),
        4 => {}
        got => return Err(FrameError::TruncatedHeader { got }),
    }
    let declared = u64::from(u32::from_le_bytes(header));
    if declared == 0 {
        return Err(FrameError::ZeroLength);
    }
    if declared > MAX_FRAME_BYTES as u64 {
        return Err(FrameError::Oversize { declared });
    }
    let expected = declared as usize;
    let mut payload = vec![0u8; expected];
    let got = read_full(reader, &mut payload)?;
    if got != expected {
        return Err(FrameError::TruncatedPayload { got, expected });
    }
    Ok(Some(payload))
}

/// Writes one frame as a single `write_all` followed by a flush.
pub fn write_frame<W: Write>(writer: &mut W, payload: &[u8]) -> Result<(), FrameError> {
    if payload.is_empty() {
        return Err(FrameError::ZeroLength);
    }
    if payload.len() > MAX_FRAME_BYTES {
        return Err(FrameError::Oversize {
            declared: payload.len() as u64,
        });
    }
    let mut frame = Vec::with_capacity(4 + payload.len());
    frame.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    frame.extend_from_slice(payload);
    writer.write_all(&frame)?;
    writer.flush()?;
    Ok(())
}
