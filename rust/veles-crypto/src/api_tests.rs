#![allow(clippy::arithmetic_side_effects)]
use crate::api::Registry;
use std::time::Duration;

#[cfg(feature = "conformance")]
#[test]
fn conformance_start_uses_literal_rfc_scalar() {
    let mut r = Registry::default();
    let mut cmd = vec![1, 127, 1];
    cmd.extend([0; 31]);
    cmd.push(1); // w=1
    cmd.extend([0; 31]);
    cmd.push(1); // x=1
    cmd.extend([0, 0]); // nil RFC identities, no AAD
    let result = r.dispatch_at(&cmd, Duration::ZERO);
    assert_eq!(result[0], 0);
    assert_eq!(result.len(), 74);
}

#[cfg(feature = "conformance")]
#[test]
fn conformance_code_transform_matches_fixed_password_scalar() {
    let mut r = Registry::default();
    let mut cmd = vec![1, 128, 1];
    cmd.extend([0; 31]);
    cmd.push(1);
    cmd.extend(b"123456");
    cmd.extend(context());
    let from_code = r.dispatch_at(&cmd, Duration::ZERO);
    assert_eq!(from_code[0], 0);
    let mut raw = vec![1, 127, 1];
    raw.extend(
        crate::spake2::password_scalar(b"123456", &[9; 16])
            .unwrap()
            .as_slice(),
    );
    raw.extend([0; 31]);
    raw.push(1);
    raw.extend([31, 30]);
    raw.extend(b"Veles Android SPAKE2 party A v1");
    raw.extend(b"Veles client SPAKE2 party B v1");
    raw.extend(context());
    assert_eq!(from_code[9..], r.dispatch_at(&raw, Duration::ZERO)[9..]);
}

fn context() -> Vec<u8> {
    let mut ctx = b"Veles SPAKE2 public context v1".to_vec();
    ctx.extend([0, 1, 0, 1, 0, 1]);
    ctx.extend([9; 16]);
    ctx.extend([1, 2]);
    ctx.extend([8; 32]);
    ctx.extend([0, 1, 1, 0, 1, 1]);
    ctx
}
fn start(role: u8) -> Vec<u8> {
    let mut cmd = vec![1, 1, role];
    cmd.extend(b"123456");
    cmd.extend(context());
    cmd
}
fn op(op: u8, handle: &[u8], body: &[u8]) -> Vec<u8> {
    let mut cmd = vec![1, op];
    cmd.extend(handle);
    cmd.extend(body);
    cmd
}

#[test]
fn dispatcher_roundtrip_replay_expiry_and_capacity() {
    let mut r = Registry::default();
    let now = Duration::ZERO;
    let a = r.dispatch_at(&start(1), now);
    let b = r.dispatch_at(&start(2), now);
    assert_eq!(a.len(), 74);
    assert_eq!(b.len(), 74);
    assert_eq!(a[0], 0);
    let ah = &a[1..9];
    let bh = &b[1..9];
    let ca = r.dispatch_at(&op(2, ah, &b[9..]), now);
    assert_eq!(ca.len(), 33);
    assert_eq!(r.dispatch_at(&op(2, bh, &a[9..]), now), [0]);
    let cb = r.dispatch_at(&op(3, bh, &ca[1..]), now);
    assert_eq!(cb.len(), 33);
    assert_eq!(r.dispatch_at(&op(3, ah, &cb[1..]), now), [0]);
    let ka = r.dispatch_at(&op(4, ah, &[]), now);
    let kb = r.dispatch_at(&op(4, bh, &[]), now);
    assert_eq!(ka.len(), 17);
    assert_eq!(ka, kb);
    assert_eq!(r.dispatch_at(&op(4, ah, &[]), now), [1]);
    let a = r.dispatch_at(&start(1), now);
    assert_eq!(
        r.dispatch_at(&op(2, &a[1..9], &b[9..]), Duration::from_secs(300)),
        [1]
    );
    for _ in 0..8 {
        assert_eq!(r.dispatch_at(&start(1), Duration::from_secs(300))[0], 0);
    }
    assert_eq!(r.dispatch_at(&start(1), Duration::from_secs(300)), [4]);
}

#[test]
fn malformed_command_destroys_referenced_state() {
    for opcode in [2, 3, 4, 5, 255] {
        let mut r = Registry::default();
        let a = r.dispatch_at(&start(1), Duration::ZERO);
        assert_eq!(
            r.dispatch_at(&op(opcode, &a[1..9], &[0]), Duration::ZERO),
            [1]
        );
        assert_eq!(r.dispatch_at(&op(5, &a[1..9], &[]), Duration::ZERO), [1]);
    }
}

#[test]
fn malformed_input_campaign_is_bounded_and_never_panics() {
    let mut r = Registry::default();
    // Deterministic parser-fuzz smoke campaign, not a coverage-guided fuzz claim.
    let mut seed = 0x9382_2026_u64;
    for iteration in 0..20_000 {
        seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
        let len = (seed as usize) % 4200;
        let mut input = vec![0; len];
        for byte in &mut input {
            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
            *byte = (seed >> 32) as u8;
        }
        if len >= 2 {
            input[0] = 1;
            input[1] = [1, 2, 3, 4, 5, 126, 255][iteration % 7];
        }
        let out = r.dispatch_at(&input, Duration::from_millis(iteration as u64));
        assert!([1, 2, 4].contains(&out[0]));
        assert_eq!(out.len(), 1);
    }
    // Structured truncation at every valid start boundary, not just random junk.
    let cmd = start(1);
    for n in 0..cmd.len() {
        assert_eq!(r.dispatch_at(&cmd[..n], Duration::from_secs(30)), [1]);
    }
}

#[test]
#[ignore = "explicit release benchmark baseline"]
fn pairing_benchmark_baseline() {
    let begin = std::time::Instant::now();
    for _ in 0..100 {
        let mut r = Registry::default();
        let now = Duration::ZERO;
        let a = r.dispatch_at(&start(1), now);
        let b = r.dispatch_at(&start(2), now);
        let ah = &a[1..9];
        let bh = &b[1..9];
        let ca = r.dispatch_at(&op(2, ah, &b[9..]), now);
        assert_eq!(r.dispatch_at(&op(2, bh, &a[9..]), now), [0]);
        let cb = r.dispatch_at(&op(3, bh, &ca[1..]), now);
        assert_eq!(r.dispatch_at(&op(3, ah, &cb[1..]), now), [0]);
        let ke = r.dispatch_at(&op(4, ah, &[]), now);
        assert_eq!(ke.len(), 17);
        assert_eq!(ke, r.dispatch_at(&op(4, bh, &[]), now));
    }
    eprintln!(
        "100 full A+B exchanges: {:?}; mean {:?}",
        begin.elapsed(),
        begin.elapsed() / 100
    );
}
