//! Canonical standard base64 (RFC 4648, padded, no whitespace) for `record_base64`.

#[derive(Debug, PartialEq, Eq)]
pub enum Base64Error {
    /// Length not a multiple of four, bad alphabet, whitespace or misplaced padding.
    Malformed,
    /// Non-zero trailing bits in the final quantum.
    NonCanonical,
    /// Decoded size is not within `1..=max`.
    Size,
}

const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

pub fn encode(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let b = [
            chunk[0],
            *chunk.get(1).unwrap_or(&0),
            *chunk.get(2).unwrap_or(&0),
        ];
        let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        out.push(ALPHABET[(n >> 18) as usize & 63] as char);
        out.push(ALPHABET[(n >> 12) as usize & 63] as char);
        out.push(if chunk.len() > 1 {
            ALPHABET[(n >> 6) as usize & 63] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            ALPHABET[n as usize & 63] as char
        } else {
            '='
        });
    }
    out
}

fn value_of(byte: u8) -> Option<u8> {
    match byte {
        b'A'..=b'Z' => Some(byte - b'A'),
        b'a'..=b'z' => Some(byte - b'a' + 26),
        b'0'..=b'9' => Some(byte - b'0' + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    }
}

/// Decodes canonically; the decoded size must be `1..=max_decoded`, checked from the encoded
/// shape before any decoded allocation.
pub fn decode(text: &str, max_decoded: usize) -> Result<Vec<u8>, Base64Error> {
    let bytes = text.as_bytes();
    if !bytes.len().is_multiple_of(4) {
        return Err(Base64Error::Malformed);
    }
    let padding = bytes.iter().rev().take_while(|b| **b == b'=').count();
    if padding > 2 {
        return Err(Base64Error::Malformed);
    }
    let body = &bytes[..bytes.len() - padding];
    if body.iter().any(|b| value_of(*b).is_none()) {
        return Err(Base64Error::Malformed);
    }
    let decoded_len = bytes.len() / 4 * 3 - padding;
    if decoded_len == 0 || decoded_len > max_decoded {
        return Err(Base64Error::Size);
    }
    let mut out = Vec::with_capacity(decoded_len);
    for quantum in body.chunks(4) {
        let mut n = 0u32;
        for byte in quantum {
            n = (n << 6) | u32::from(value_of(*byte).expect("validated alphabet"));
        }
        n <<= 6 * (4 - quantum.len() as u32);
        let produced = quantum.len() * 6 / 8;
        let three = [(n >> 16) as u8, (n >> 8) as u8, n as u8];
        if produced < 3 && three[produced..].iter().any(|b| *b != 0) {
            return Err(Base64Error::NonCanonical);
        }
        out.extend_from_slice(&three[..produced]);
    }
    Ok(out)
}
