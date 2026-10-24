//! Bounded process-local FFI command dispatcher. See docs/protocol/otp-03-api.md.
use crate::spake2::{Error, Pairing, Role};
use std::{
    collections::BTreeMap,
    sync::{Mutex, OnceLock},
    time::Duration,
};
use zeroize::Zeroizing;

#[derive(Default)]
pub(crate) struct Registry {
    next: u64,
    entries: BTreeMap<u64, (Duration, Pairing)>,
}

impl Registry {
    pub(crate) fn dispatch_at(&mut self, input: &[u8], now: Duration) -> Vec<u8> {
        self.entries.retain(|_, (created, _)| {
            now.checked_sub(*created)
                .is_some_and(|age| age < Duration::from_secs(300))
        });
        let handle = input
            .get(2..10)
            .and_then(|s| <[u8; 8]>::try_from(s).ok())
            .map(u64::from_be_bytes);
        if input.len() > 4105 || input.first() != Some(&1) || input.len() < 2 {
            if input.get(1) != Some(&1)
                && let Some(h) = handle
            {
                self.entries.remove(&h);
            }
            return vec![1];
        }
        #[cfg(feature = "conformance")]
        if matches!(input[1], 127 | 128) {
            let pairing = match conformance_start(input) {
                Ok(p) => p,
                Err(e) => return error(e),
            };
            if self.entries.len() >= 8 {
                return vec![4];
            }
            let Some(next) = self.next.checked_add(1) else {
                return vec![4];
            };
            self.next = next;
            let mut out = vec![0];
            out.extend(next.to_be_bytes());
            out.extend(pairing.public_point());
            self.entries.insert(next, (now, pairing));
            return out;
        }
        if input[1] == 1 {
            if input.len() < 9 {
                return vec![1];
            }
            let role = match input[2] {
                1 => Role::A,
                2 => Role::B,
                _ => return vec![1],
            };
            if self.entries.len() >= 8 {
                return vec![4];
            }
            let Some(next) = self.next.checked_add(1) else {
                return vec![4];
            };
            let pairing = match Pairing::start(role, &input[3..9], &input[9..]) {
                Ok(p) => p,
                Err(e) => return error(e),
            };
            self.next = next;
            let mut out = vec![0];
            out.extend(next.to_be_bytes());
            out.extend(pairing.public_point());
            self.entries.insert(next, (now, pairing));
            return out;
        }
        let Some(h) = handle else {
            return vec![1];
        };
        let Some((created, mut pairing)) = self.entries.remove(&h) else {
            return vec![1];
        };
        let body = &input[10..];
        let result = match input[1] {
            2 => pairing.exchange(body).map(Zeroizing::new),
            3 => pairing.confirm(body).map(Zeroizing::new),
            4 if body.is_empty() => pairing.finish().map(|ke| Zeroizing::new(ke.to_vec())),
            5 if body.is_empty() => Ok(Zeroizing::new(vec![])),
            _ => Err(Error::Failed),
        };
        match result {
            Ok(bytes) => {
                if matches!(input[1], 2 | 3) {
                    self.entries.insert(h, (created, pairing));
                }
                let mut out = vec![0];
                out.extend_from_slice(&bytes);
                out
            }
            Err(e) => error(e),
        }
    }
}

// Compiled ONLY in separate test artifacts. Not part of the product ABI.
#[cfg(feature = "conformance")]
fn conformance_start(input: &[u8]) -> Result<Pairing, Error> {
    let role = match input.get(2) {
        Some(1) => Role::A,
        Some(2) => Role::B,
        _ => return Err(Error::Failed),
    };
    if input[1] == 128 {
        let secret = input.get(3..35).ok_or(Error::Failed)?;
        let code = input.get(35..41).ok_or(Error::Failed)?;
        return Pairing::deterministic_code(role, code, secret, &input[41..]);
    }
    let w = input.get(3..35).ok_or(Error::Failed)?;
    let scalar = input.get(35..67).ok_or(Error::Failed)?;
    let lengths = input.get(67..69).ok_or(Error::Failed)?;
    let mut rest = &input[69..];
    let a = rest.get(..usize::from(lengths[0])).ok_or(Error::Failed)?;
    rest = &rest[a.len()..];
    let b = rest.get(..usize::from(lengths[1])).ok_or(Error::Failed)?;
    rest = &rest[b.len()..];
    Pairing::deterministic(role, w, scalar, rest, (a, b))
}

fn error(e: Error) -> Vec<u8> {
    vec![match e {
        Error::RandomUnavailable => 2,
        _ => 1,
    }]
}

#[cfg(not(target_arch = "wasm32"))]
fn now() -> Option<Duration> {
    use std::time::Instant;
    static START: OnceLock<Instant> = OnceLock::new();
    Some(START.get_or_init(Instant::now).elapsed())
}

#[cfg(target_arch = "wasm32")]
fn now() -> Option<Duration> {
    use wasm_bindgen::prelude::*;
    #[wasm_bindgen]
    extern "C" {
        #[wasm_bindgen(js_namespace=performance,js_name=now,catch)]
        fn performance_now() -> Result<f64, JsValue>;
    }
    Duration::try_from_secs_f64(performance_now().ok()? / 1000.0).ok()
}

/// Versioned FFI entry point; input ceiling 4105 bytes, eight live handles.
pub fn pairing_command(input: &[u8]) -> Zeroizing<Vec<u8>> {
    static REGISTRY: OnceLock<Mutex<Registry>> = OnceLock::new();
    let Ok(mut registry) = REGISTRY
        .get_or_init(|| Mutex::new(Registry::default()))
        .lock()
    else {
        return Zeroizing::new(vec![1]);
    };
    let Some(now) = now() else {
        registry.entries.clear();
        return Zeroizing::new(vec![1]);
    };
    Zeroizing::new(registry.dispatch_at(input, now))
}
