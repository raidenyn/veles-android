//! Opaque payload ownership (design: "Runtime and ownership"). Decoded records and frame bytes
//! are owned buffers that are best-effort erased when released; their `Debug` output is
//! redacted so debug/trace formatting can never print record content.

use std::fmt;

pub struct Payload {
    bytes: Vec<u8>,
}

impl Payload {
    /// Takes ownership of `bytes` with retained allocation normalized to its length: the
    /// queue byte caps everywhere account `len()` only, so a buffer retained from a
    /// spare-capacity `Vec` is re-slotted into an exactly-sized allocation (its original
    /// bytes are erased before that oversized buffer is released) and queue caps bound the
    /// actual retained allocation, not just the logical length.
    pub fn new(mut bytes: Vec<u8>) -> Self {
        let len = bytes.len();
        if bytes.capacity() == len {
            return Self { bytes };
        }
        let mut exact = Vec::with_capacity(len);
        exact.extend_from_slice(&bytes);
        erase(&mut bytes);
        Self { bytes: exact }
    }

    pub fn as_slice(&self) -> &[u8] {
        &self.bytes
    }

    pub fn len(&self) -> usize {
        self.bytes.len()
    }

    pub fn is_empty(&self) -> bool {
        self.bytes.is_empty()
    }
}

impl Drop for Payload {
    fn drop(&mut self) {
        erase(&mut self.bytes);
    }
}

impl fmt::Debug for Payload {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Payload({} bytes)", self.bytes.len())
    }
}

impl Clone for Payload {
    fn clone(&self) -> Self {
        Self::new(self.bytes.clone())
    }
}

impl PartialEq for Payload {
    fn eq(&self, other: &Self) -> bool {
        self.bytes == other.bytes
    }
}

impl Eq for Payload {}

/// Best-effort erase of a payload buffer before it is released (the reviewed feasibility
/// `fragment.rs` implementation): volatile writes so the compiler cannot elide the clearing of
/// a buffer about to be freed, and a fence so no later optimization reorders them.
pub fn erase(bytes: &mut [u8]) {
    for byte in bytes.iter_mut() {
        unsafe { std::ptr::write_volatile(byte, 0) };
    }
    std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
}

#[cfg(test)]
mod tests {
    use super::{Payload, erase};

    #[test]
    fn erase_is_best_effort_but_total_while_alive() {
        let mut bytes = [0xAAu8; 3];
        erase(&mut bytes);
        assert!(bytes.iter().all(|byte| *byte == 0));
    }

    #[test]
    fn debug_is_redacted() {
        let payload = Payload::new(b"secret".to_vec());
        assert_eq!(format!("{payload:?}"), "Payload(6 bytes)");
    }

    #[test]
    fn clone_is_an_independent_copy() {
        let original = Payload::new(vec![1, 2, 3]);
        let copy = original.clone();
        assert_eq!(original, copy);
    }

    /// Finding 2 (retained-allocation bound): queue admission accounts `len()` only, so
    /// `Payload::new` must normalize excess `Vec` capacity — a one-byte payload retained from
    /// a huge spare-capacity buffer must not keep that spare allocation alive, or the queue
    /// byte caps would not bound actual memory.
    #[test]
    fn new_normalizes_excess_retained_capacity() {
        let mut raw = Vec::with_capacity(65_536);
        raw.push(0xAA);
        assert!(raw.capacity() >= 65_536, "precondition: excess capacity");
        let payload = Payload::new(raw);
        assert_eq!(payload.len(), 1);
        assert_eq!(payload.as_slice(), &[0xAA][..]);
        assert!(
            payload.bytes.capacity() <= payload.len(),
            "excess capacity was not shed: {} > {}",
            payload.bytes.capacity(),
            payload.len()
        );

        // An already exact-capacity buffer stays as built (content preserved, still erased on
        // drop via the same type).
        let exact = vec![0x1, 0x2, 0x3];
        assert_eq!(exact.capacity(), exact.len());
        let payload = Payload::new(exact);
        assert_eq!(payload.as_slice(), &[0x1, 0x2, 0x3]);
        assert_eq!(payload.bytes.capacity(), 3);
    }
}
