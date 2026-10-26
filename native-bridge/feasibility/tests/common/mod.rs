#![allow(dead_code)]

pub fn hex_decode(text: &str) -> Vec<u8> {
    assert!(text.len().is_multiple_of(2), "odd hex length");
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).expect("hex digit"))
        .collect()
}

/// Deterministic synthetic record bytes (never OTP data).
pub fn synthetic(len: usize, seed: u8) -> Vec<u8> {
    (0..len)
        .map(|i| seed.wrapping_add((i % 251) as u8))
        .collect()
}

/// Link budget of a link negotiating `mtu` (panics outside 185..=517).
pub fn budget(mtu: usize) -> veles_otp23_feasibility::fragment::LinkBudget {
    veles_otp23_feasibility::fragment::LinkBudget::from_att_mtu(mtu).expect("valid ATT MTU")
}
