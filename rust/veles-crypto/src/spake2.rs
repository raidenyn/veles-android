//! Frozen RFC 9382 P-256 profile. No secret-bearing type implements Debug or Clone.
// These operations are group/field arithmetic modulo P-256's fixed prime/order,
// not unchecked integer arithmetic. RustCrypto supplies the modular semantics.
#![allow(clippy::arithmetic_side_effects)]
use hkdf::Hkdf;
use hmac::{Hmac, Mac};
use p256::elliptic_curve::{
    Field, PrimeField,
    group::Group,
    sec1::{FromEncodedPoint, ToEncodedPoint},
};
use p256::{AffinePoint, EncodedPoint, FieldBytes, ProjectivePoint, Scalar};
use sha2::{Digest, Sha256};
use zeroize::{Zeroize, Zeroizing};

/// Cryptographic failures intentionally carry no peer-controlled detail.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    Failed,
    RandomUnavailable,
    InvalidState,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Role {
    A,
    B,
}

const M: [u8; 33] = [
    0x02, 0x88, 0x6e, 0x2f, 0x97, 0xac, 0xe4, 0x6e, 0x55, 0xba, 0x9d, 0xd7, 0x24, 0x25, 0x79, 0xf2,
    0x99, 0x3b, 0x64, 0xe1, 0x6e, 0xf3, 0xdc, 0xab, 0x95, 0xaf, 0xd4, 0x97, 0x33, 0x3d, 0x8f, 0xa1,
    0x2f,
];
const N: [u8; 33] = [
    0x03, 0xd8, 0xbb, 0xd6, 0xc6, 0x39, 0xc6, 0x29, 0x37, 0xb0, 0x4d, 0x99, 0x7f, 0x38, 0xc3, 0x77,
    0x07, 0x19, 0xc6, 0x29, 0xd7, 0x01, 0x4d, 0x49, 0xa2, 0x4b, 0x4f, 0x98, 0xba, 0xa1, 0x29, 0x2b,
    0x49,
];

fn decode(bytes: &[u8]) -> Result<ProjectivePoint, Error> {
    let encoded = EncodedPoint::from_bytes(bytes).map_err(|_| Error::Failed)?;
    let affine = Option::<AffinePoint>::from(AffinePoint::from_encoded_point(&encoded))
        .ok_or(Error::Failed)?;
    // P-256 has cofactor one: on-curve nonidentity implies subgroup membership.
    let point = ProjectivePoint::from(affine);
    if bool::from(point.is_identity()) {
        return Err(Error::Failed);
    }
    Ok(point)
}

fn encode(point: &ProjectivePoint) -> Vec<u8> {
    point
        .to_affine()
        .to_encoded_point(false)
        .as_bytes()
        .to_vec()
}

fn scalar(bytes: &[u8]) -> Result<Scalar, Error> {
    let bytes: [u8; 32] = bytes.try_into().map_err(|_| Error::Failed)?;
    Option::<Scalar>::from(Scalar::from_repr(bytes.into())).ok_or(Error::Failed)
}

/// Frozen six-ASCII-digit to scalar transform; zero is valid for the password.
/// # Errors
/// Rejects malformed code or pairing identifier.
pub fn password_scalar(code: &[u8], pairing_id: &[u8]) -> Result<Zeroizing<[u8; 32]>, Error> {
    if code.len() != 6 || !code.iter().all(u8::is_ascii_digit) || pairing_id.len() != 16 {
        return Err(Error::Failed);
    }
    let mut okm = Zeroizing::new([0u8; 40]);
    Hkdf::<Sha256>::new(Some(pairing_id), code)
        .expand(b"Veles SPAKE2 password input v1", okm.as_mut())
        .map_err(|_| Error::Failed)?;
    // Horner reduction handles all 320 bits in constant work, including w=0.
    let mut w = Zeroizing::new(Scalar::ZERO);
    for byte in okm.iter() {
        *w = *w * Scalar::from(256u64) + Scalar::from(u64::from(*byte));
    }
    let mut result = Zeroizing::new([0u8; 32]);
    result.copy_from_slice(&w.to_bytes());
    Ok(result)
}

fn validate_context(ctx: &[u8]) -> Result<&[u8], Error> {
    let prefix = b"Veles SPAKE2 public context v1";
    if ctx.len() > 4096 || !ctx.starts_with(prefix) {
        return Err(Error::Failed);
    }
    let mut rest = &ctx[prefix.len()..];
    if rest.get(..6) != Some(&[0, 1, 0, 1, 0, 1]) {
        return Err(Error::Failed);
    }
    let id = rest.get(6..22).ok_or(Error::Failed)?;
    if rest.get(22..24) != Some(&[1, 2]) {
        return Err(Error::Failed);
    }
    rest = rest.get(56..).ok_or(Error::Failed)?;
    for _ in 0..2 {
        let n = rest.get(..2).ok_or(Error::Failed)?;
        let n = usize::from(u16::from_be_bytes([n[0], n[1]]));
        rest = &rest[2..];
        let caps = rest.get(..n).ok_or(Error::Failed)?;
        if n == 0
            || n > 16
            || caps[0] != 1
            || !caps.iter().all(|c| (1..=3).contains(c))
            || caps.windows(2).any(|w| w[0] >= w[1])
        {
            return Err(Error::Failed);
        }
        rest = &rest[n..];
    }
    if !rest.is_empty() {
        return Err(Error::Failed);
    }
    Ok(id)
}

fn fresh_scalar(
    mut random: impl FnMut(&mut [u8]) -> Result<(), Error>,
) -> Result<Zeroizing<Scalar>, Error> {
    let mut bytes = Zeroizing::new([0u8; 32]);
    for _ in 0..128 {
        random(bytes.as_mut())?;
        if let Ok(s) = scalar(bytes.as_ref())
            && !bool::from(s.is_zero())
        {
            return Ok(Zeroizing::new(s));
        }
    }
    Err(Error::RandomUnavailable)
}

enum State {
    Initial {
        w: Zeroizing<Scalar>,
        secret: Zeroizing<Scalar>,
    },
    Awaiting {
        ke: Zeroizing<[u8; 16]>,
        expected: Zeroizing<[u8; 32]>,
        outbound: Zeroizing<[u8; 32]>,
    },
    Confirmed(Zeroizing<[u8; 16]>),
    Closed,
}

/// One ephemeral execution; every failed or out-of-order operation destroys it.
pub struct Pairing {
    role: Role,
    public: Vec<u8>,
    context: Vec<u8>,
    ids: (Vec<u8>, Vec<u8>),
    state: State,
}

impl Pairing {
    /// Starts a fresh attempt using OS/browser cryptographic entropy.
    /// # Errors
    /// Invalid code/context or unavailable entropy.
    pub fn start(role: Role, code: &[u8], context: &[u8]) -> Result<Self, Error> {
        let id = validate_context(context)?;
        let w = password_scalar(code, id)?;
        let secret =
            fresh_scalar(|out| getrandom::getrandom(out).map_err(|_| Error::RandomUnavailable))?;
        Self::new(
            role,
            Zeroizing::new(scalar(w.as_ref())?),
            secret,
            context,
            (
                b"Veles Android SPAKE2 party A v1",
                b"Veles client SPAKE2 party B v1",
            ),
        )
    }

    #[cfg(feature = "conformance")]
    pub(crate) fn deterministic_code(
        role: Role,
        code: &[u8],
        secret: &[u8],
        context: &[u8],
    ) -> Result<Self, Error> {
        let w = password_scalar(code, validate_context(context)?)?;
        Self::deterministic(
            role,
            w.as_ref(),
            secret,
            context,
            (
                b"Veles Android SPAKE2 party A v1",
                b"Veles client SPAKE2 party B v1",
            ),
        )
    }

    fn new(
        role: Role,
        w: Zeroizing<Scalar>,
        secret: Zeroizing<Scalar>,
        context: &[u8],
        ids: (&[u8], &[u8]),
    ) -> Result<Self, Error> {
        let mask = decode(if role == Role::A { &M } else { &N })?;
        let public = ProjectivePoint::GENERATOR * *secret + mask * *w;
        if bool::from(public.is_identity()) {
            return Err(Error::Failed);
        }
        Ok(Self {
            role,
            public: encode(&public),
            context: context.to_vec(),
            ids: (ids.0.to_vec(), ids.1.to_vec()),
            state: State::Initial { w, secret },
        })
    }

    #[cfg(any(test, feature = "conformance"))]
    pub(crate) fn deterministic(
        role: Role,
        w: &[u8],
        secret: &[u8],
        context: &[u8],
        ids: (&[u8], &[u8]),
    ) -> Result<Self, Error> {
        let secret = Zeroizing::new(scalar(secret)?);
        if bool::from(secret.is_zero()) {
            return Err(Error::Failed);
        }
        Self::new(role, Zeroizing::new(scalar(w)?), secret, context, ids)
    }

    pub fn public_point(&self) -> Vec<u8> {
        self.public.clone()
    }

    /// Processes the peer element; A returns cA, B waits for cA.
    /// # Errors
    /// Invalid state/point or identity after unmasking. Failure closes the attempt.
    pub fn exchange(&mut self, peer: &[u8]) -> Result<Vec<u8>, Error> {
        let state = std::mem::replace(&mut self.state, State::Closed);
        let State::Initial { w, secret } = state else {
            return Err(Error::InvalidState);
        };
        if peer.len() != 65 || peer.first() != Some(&4) {
            return Err(Error::Failed);
        }
        let point = decode(peer)?;
        let mask = decode(if self.role == Role::A { &N } else { &M })?;
        let mut k = (point - mask * *w) * *secret;
        if bool::from(k.is_identity()) {
            k.zeroize();
            return Err(Error::Failed);
        }
        let mut tt = Zeroizing::new(Vec::new());
        let k_bytes = Zeroizing::new(encode(&k));
        k.zeroize();
        let w_bytes: Zeroizing<FieldBytes> = Zeroizing::new(w.to_bytes());
        let (pa, pb) = if self.role == Role::A {
            (self.public.as_slice(), peer)
        } else {
            (peer, self.public.as_slice())
        };
        for part in [
            self.ids.0.as_slice(),
            self.ids.1.as_slice(),
            pa,
            pb,
            k_bytes.as_slice(),
            &w_bytes[..],
        ] {
            tt.extend_from_slice(&(part.len() as u64).to_le_bytes());
            tt.extend_from_slice(part);
        }
        let digest = Zeroizing::new(<[u8; 32]>::from(Sha256::digest(tt.as_slice())));
        let mut keys = Zeroizing::new([0u8; 32]);
        let mut info = b"ConfirmationKeys".to_vec();
        info.extend_from_slice(&self.context);
        Hkdf::<Sha256>::new(None, &digest[16..])
            .expand(&info, keys.as_mut())
            .map_err(|_| Error::Failed)?;
        let tag = |key: &[u8]| -> Result<Zeroizing<[u8; 32]>, Error> {
            let mut mac = Hmac::<Sha256>::new_from_slice(key).map_err(|_| Error::Failed)?;
            mac.update(tt.as_slice());
            Ok(Zeroizing::new(mac.finalize().into_bytes().into()))
        };
        let ca = tag(&keys[..16])?;
        let cb = tag(&keys[16..])?;
        let mut ke = Zeroizing::new([0; 16]);
        ke.copy_from_slice(&digest[..16]);
        let result = if self.role == Role::A {
            ca.to_vec()
        } else {
            vec![]
        };
        let (expected, outbound) = if self.role == Role::A {
            (cb, ca)
        } else {
            (ca, cb)
        };
        self.state = State::Awaiting {
            ke,
            expected,
            outbound,
        };
        Ok(result)
    }

    /// Verifies peer confirmation in constant time; B returns cB only after cA.
    /// # Errors
    /// Wrong confirmation/length/order; failure closes the attempt.
    pub fn confirm(&mut self, peer: &[u8]) -> Result<Vec<u8>, Error> {
        use p256::elliptic_curve::subtle::ConstantTimeEq;
        let state = std::mem::replace(&mut self.state, State::Closed);
        let State::Awaiting {
            ke,
            expected,
            outbound,
        } = state
        else {
            return Err(Error::InvalidState);
        };
        if !bool::from(expected.as_slice().ct_eq(peer)) {
            return Err(Error::Failed);
        }
        let result = if self.role == Role::B {
            outbound.to_vec()
        } else {
            vec![]
        };
        self.state = State::Confirmed(ke);
        Ok(result)
    }

    /// Transfers Ke exactly once to the enrollment schedule (never a traffic key).
    /// # Errors
    /// Confirmation has not succeeded, or output was already consumed.
    pub fn finish(&mut self) -> Result<Zeroizing<[u8; 16]>, Error> {
        match std::mem::replace(&mut self.state, State::Closed) {
            State::Confirmed(ke) => Ok(ke),
            _ => Err(Error::InvalidState),
        }
    }
}

#[cfg(test)]
mod rng_tests {
    use super::*;
    #[test]
    fn entropy_failure_and_degenerate_scalar_are_bounded() {
        assert!(matches!(
            fresh_scalar(|_| Err(Error::RandomUnavailable)),
            Err(Error::RandomUnavailable)
        ));
        let mut calls = 0;
        assert!(
            fresh_scalar(|bytes| {
                calls += 1;
                bytes.fill(0);
                Ok(())
            })
            .is_err()
        );
        assert_eq!(calls, 128);
        assert!(
            fresh_scalar(|bytes| {
                bytes.fill(255);
                Ok(())
            })
            .is_err()
        );
    }
}
