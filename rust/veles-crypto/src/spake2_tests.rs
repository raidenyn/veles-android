//! Literal expected bytes come from the independently checked OTP-02 fixtures.
#![allow(clippy::arithmetic_side_effects)]

use crate::spake2::{Pairing, Role};
use serde_json::Value;

fn bytes(value: &Value) -> Vec<u8> {
    let s = value.as_str().unwrap().strip_prefix("0x").unwrap();
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}

#[test]
fn frozen_rfc_and_veles_vectors_match_real_core() {
    let root: Value =
        serde_json::from_str(include_str!("../../../protocol/v1/fixtures/crypto.json")).unwrap();
    let mut tested = 0;
    for f in root["fixtures"].as_array().unwrap() {
        let input = &f["input"];
        let kind = input["kind"].as_str().unwrap_or("");
        if kind != "rfc9382_anchor" && kind != "spake2_pairing" {
            continue;
        }
        let out = &f["expected"]["outputs"];
        if out.is_null() {
            continue;
        }
        let ctx = if kind == "spake2_pairing" {
            bytes(&out["ctx"])
        } else {
            vec![]
        };
        let w = bytes(if kind == "spake2_pairing" {
            &out["w"]
        } else {
            &input["w"]
        });
        let ids = if kind == "spake2_pairing" {
            (
                b"Veles Android SPAKE2 party A v1".as_slice(),
                b"Veles client SPAKE2 party B v1".as_slice(),
            )
        } else {
            (
                input["id_a"].as_str().unwrap().as_bytes(),
                input["id_b"].as_str().unwrap().as_bytes(),
            )
        };
        if kind == "spake2_pairing" {
            assert_eq!(
                crate::spake2::password_scalar(
                    input["code"].as_str().unwrap().as_bytes(),
                    &bytes(&input["pairing_id"])
                )
                .unwrap()
                .as_slice(),
                w
            );
        }
        let mut a = Pairing::deterministic(Role::A, &w, &bytes(&input["x"]), &ctx, ids).unwrap();
        let mut b = Pairing::deterministic(Role::B, &w, &bytes(&input["y"]), &ctx, ids).unwrap();
        assert_eq!(a.public_point(), bytes(&out["p_a"]));
        assert_eq!(b.public_point(), bytes(&out["p_b"]));
        let ca = a.exchange(&b.public_point()).unwrap();
        assert_eq!(ca, bytes(&out["c_a"]));
        assert!(b.exchange(&a.public_point()).unwrap().is_empty());
        let cb = b.confirm(&ca).unwrap();
        assert_eq!(cb, bytes(&out["c_b"]));
        assert!(a.confirm(&cb).unwrap().is_empty());
        assert_eq!(a.finish().unwrap().as_slice(), bytes(&out["ke"]));
        assert_eq!(b.finish().unwrap().as_slice(), bytes(&out["ke"]));
        assert!(a.finish().is_err());
        tested += 1;
    }
    assert!(tested >= 4);
}

fn context() -> Vec<u8> {
    let mut ctx = b"Veles SPAKE2 public context v1".to_vec();
    ctx.extend_from_slice(&[0, 1, 0, 1, 0, 1]);
    ctx.extend_from_slice(&[9; 16]);
    ctx.extend_from_slice(&[1, 2]);
    ctx.extend_from_slice(&[8; 32]);
    ctx.extend_from_slice(&[0, 2, 1, 2, 0, 2, 1, 3]);
    ctx
}

#[test]
fn fresh_attempts_confirm_and_release_only_once() {
    let mut a = Pairing::start(Role::A, b"000000", &context()).unwrap();
    let mut b = Pairing::start(Role::B, b"000000", &context()).unwrap();
    let other = Pairing::start(Role::A, b"000000", &context()).unwrap();
    assert_ne!(a.public_point(), other.public_point());
    let ca = a.exchange(&b.public_point()).unwrap();
    b.exchange(&a.public_point()).unwrap();
    let cb = b.confirm(&ca).unwrap();
    a.confirm(&cb).unwrap();
    assert_eq!(a.finish().unwrap(), b.finish().unwrap());
    assert!(a.finish().is_err());
}

#[test]
fn bad_inputs_wrong_codes_and_out_of_order_calls_fail_closed() {
    for code in [b"12345".as_slice(), b"1234567", b"12a456", &[255; 6]] {
        assert!(Pairing::start(Role::A, code, &context()).is_err());
    }
    for i in 0..context().len() {
        assert!(Pairing::start(Role::A, b"123456", &context()[..i]).is_err());
    }
    for point in [vec![], vec![0; 65], vec![4; 65], vec![2; 33], vec![4; 66]] {
        let mut a = Pairing::start(Role::A, b"123456", &context()).unwrap();
        assert!(a.exchange(&point).is_err());
        assert!(a.finish().is_err());
    }
    let mut a = Pairing::start(Role::A, b"123456", &context()).unwrap();
    let mut b = Pairing::start(Role::B, b"654321", &context()).unwrap();
    let ca = a.exchange(&b.public_point()).unwrap();
    b.exchange(&a.public_point()).unwrap();
    assert!(b.confirm(&ca).is_err());
    assert!(b.finish().is_err());
    let mut early = Pairing::start(Role::A, b"123456", &context()).unwrap();
    assert!(early.finish().is_err());
    assert!(early.exchange(&a.public_point()).is_err());
}

#[test]
fn identity_unmasking_and_reflection_never_release_keys() {
    let root: Value =
        serde_json::from_str(include_str!("../../../protocol/v1/fixtures/crypto.json")).unwrap();
    let f = root["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .find(|f| f["id"] == "veles-neg-shared-secret-identity")
        .unwrap();
    // Check the fixture's forced pA/pB rather than deriving expected points here.
    let input = &f["input"];
    let base = root["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .find(|f| f["id"] == "veles-pairing-code-000000")
        .unwrap();
    for (role, scalar_name, point_name) in [(Role::A, "x", "value"), (Role::B, "y", "value2")] {
        let mut p = Pairing::deterministic(
            role,
            &bytes(&base["expected"]["outputs"]["w"]),
            &bytes(&input[scalar_name]),
            &bytes(&base["expected"]["outputs"]["ctx"]),
            (
                b"Veles Android SPAKE2 party A v1",
                b"Veles client SPAKE2 party B v1",
            ),
        )
        .unwrap();
        assert!(p.exchange(&bytes(&input["deviation"][point_name])).is_err());
        assert!(p.finish().is_err());
    }
    for role in [Role::A, Role::B] {
        let mut a = Pairing::start(role, b"123456", &context()).unwrap();
        let reflected = a.public_point();
        let _ = a.exchange(&reflected);
        assert!(a.confirm(&[0; 32]).is_err());
        assert!(a.finish().is_err());
    }
    let mut ctx = context();
    ctx[60] ^= 1;
    let mut a = Pairing::start(Role::A, b"123456", &context()).unwrap();
    let mut b = Pairing::start(Role::B, b"123456", &ctx).unwrap();
    let ca = a.exchange(&b.public_point()).unwrap();
    b.exchange(&a.public_point()).unwrap();
    assert!(b.confirm(&ca).is_err());
    assert!(b.finish().is_err());
}
