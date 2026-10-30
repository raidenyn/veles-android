//! macOS CoreBluetooth hint format, effective-MTU policy and scan bookkeeping
//! (pure mappings, no OS calls; runs on every host).

use veles_otp23_feasibility::fragment::{ATTRIBUTE_LIMIT, MAX_MTU, MIN_MTU};
use veles_otp23_feasibility::macos_map::{
    Authorization, BoundedSet, GateState, MtuError, ScanGate, effective_mtu, fallback_label,
    format_hint, parse_hint, unavailable_timeout, valid_uuid,
};

const UUID: &str = "a11b52c3-7f3c-4d1a-b64a-8e3c9b0d2a51";

#[test]
fn hint_round_trips_the_peripheral_identifier() {
    let hint = format_hint(UUID).unwrap();
    assert_eq!(hint, format!("macos:{UUID}"));
    assert_eq!(parse_hint(&hint), Some(UUID));
    assert_eq!(parse_hint(&format!("macos:{UUID}")), Some(UUID));
    // The hint stays within the core's hint bounds and carries no control bytes.
    assert!(hint.len() <= 256 && !hint.chars().any(char::is_control));
}

#[test]
fn malformed_hints_are_refused() {
    for hint in [
        "",
        "sim:phone-1",
        "winrt:aabbccddeeff:1",
        "macos",
        "macos:",
        "MACOS:a11b52c3-7f3c-4d1a-b64a-8e3c9b0d2a51",
        "a11b52c3-7f3c-4d1a-b64a-8e3c9b0d2a51", // missing prefix
        "macos:a11b52c-37f3c-4d1a-b64a-8e3c9b0d2a51", // hyphens shifted by one digit
        "macos:a11b52c3-7f3c-4d1a-g64a-8e3c9b0d2a51", // non-hex digit
        "macos:a11b52c3-7f3c 4d1a-b64a-8e3c9b0d2a51", // space instead of hyphen
        "macos:a11b52c3-7f3c-4d1a-b64a-8e3c9b0d2a51:x", // trailing garbage
    ] {
        assert_eq!(parse_hint(hint), None, "{hint:?}");
    }
}

#[test]
fn fallback_label_is_short_valid_and_not_the_full_identifier() {
    let label = fallback_label(UUID);
    assert_eq!(label, "BLE device ..2a51");
    let empty = fallback_label("");
    assert_eq!(empty, "BLE device ..");
    for label in [label, empty] {
        assert!(label.len() <= 64 && !label.chars().any(char::is_control));
    }
}

#[test]
fn valid_uuid_accepts_only_canonical_lowercase() {
    assert!(valid_uuid(UUID));
    assert!(!valid_uuid(&UUID.to_uppercase()));
    assert!(!valid_uuid(&UUID[..35]));
    assert!(!valid_uuid(&format!("{UUID}0")));
    assert!(!valid_uuid("a11b52c30f3c4d1ab64a8e3c9b0d2a51"));
}

// ---- inferred effective MTU policy --------------------------------------------------------------

#[test]
fn reported_max_write_yields_the_task_trace_values() {
    // A phone reporting 512 attribute bytes for write-without-response infers an effective
    // ATT MTU of 515 and a capped fragment payload of 508, matching an Android phone at a
    // negotiated MTU of 517 (517 - 3 = 514, capped at 512, minus the 4-byte header).
    let mtu = effective_mtu(512).unwrap();
    assert_eq!(mtu, 515);
    let chunk_payload = (mtu - 3).min(ATTRIBUTE_LIMIT) - 4;
    assert_eq!(chunk_payload, 508);
}

#[test]
fn capped_fragment_rule_matches_the_android_negotiated_cap() {
    // MAX_MTU (517) negotiated exactly: the payload rule equals the Android phone's.
    let mtu = effective_mtu(514).unwrap();
    assert_eq!(mtu, MAX_MTU);
    assert_eq!((mtu - 3).min(ATTRIBUTE_LIMIT) - 4, 508);
}

#[test]
fn above_cap_max_write_still_caps_at_max_mtu() {
    // A stack that negotiated more than 517 attribute bytes still reports the capped policy.
    let mtu = effective_mtu(usize::MAX).unwrap();
    assert_eq!(mtu, MAX_MTU);
    assert_eq!((mtu - 3).min(ATTRIBUTE_LIMIT) - 4, 508);
}

#[test]
fn floor_is_the_minimum_usable_value() {
    assert_eq!(effective_mtu(MIN_MTU - 3).unwrap(), MIN_MTU);
    assert_eq!(effective_mtu(MIN_MTU - 3 - 1), Err(MtuError::TooSmall));
    for reported in [0usize, 1, 3, 100, MIN_MTU - 3 - 1] {
        assert_eq!(
            effective_mtu(reported),
            Err(MtuError::TooSmall),
            "{reported}"
        );
    }
}

#[test]
fn every_accepted_mtu_wraps_every_frame_in_one_os_write() {
    // The policy invariant that makes the fragment rule safe on macOS: for every reported
    // max write value whose inferred MTU is accepted, the core chunk
    // (min(mtu - 3, 512) - 4) plus its 4-byte header is <= the measured OS write length.
    for reported in 0..=MAX_MTU {
        let Ok(mtu) = effective_mtu(reported) else {
            continue;
        };
        let chunk = (mtu - 3).min(ATTRIBUTE_LIMIT) - 4;
        assert!(
            chunk + 4 <= reported,
            "reported {reported} -> mtu {mtu} -> chunk {chunk}"
        );
    }
}

// ---- bounded scan state ----------------------------------------------------------------------

#[test]
fn bounded_set_rejects_duplicates_and_capacity() {
    let mut seen = BoundedSet::new(3);
    assert!(seen.insert("a"));
    assert!(!seen.insert("a"), "duplicate refused");
    assert!(seen.insert("b"));
    assert!(seen.insert("c"));
    assert!(!seen.insert("d"), "capacity reached");
    assert!(!seen.insert("a"), "existing entries cannot be re-inserted");
    assert_eq!(seen.len(), 3);
    assert!(!seen.is_empty());
    assert!(BoundedSet::new(3).is_empty());
}

#[test]
fn scan_gate_admits_exactly_one_start_and_one_end() {
    let mut gate = ScanGate::new();
    assert_eq!(gate.state(), GateState::Pending);
    assert!(gate.try_run());
    assert_eq!(gate.state(), GateState::Running);
    assert!(!gate.try_run(), "start is confirmed only once");
    assert!(gate.stop());
    assert_eq!(gate.state(), GateState::Done);
    assert!(!gate.try_run());
    assert!(!gate.stop(), "double stop is a no-op");
    assert!(
        !gate.finish_naturally(),
        "external stop wins over natural end"
    );
}

#[test]
fn scan_gate_natural_end_wins_without_external_stop() {
    let mut gate = ScanGate::new();
    assert!(gate.try_run());
    assert!(gate.finish_naturally());
    assert_eq!(gate.state(), GateState::Done);
    assert!(!gate.finish_naturally(), "one end only");
    // A gate stopped while pending never confirms a natural end.
    let mut early = ScanGate::new();
    assert!(early.stop());
    assert!(!early.finish_naturally());
}

#[test]
fn authorization_raw_values_map_to_fixed_names() {
    let names: Vec<_> = (0..=3)
        .map(|raw| Authorization::from_raw(raw).name())
        .collect();
    assert_eq!(
        names,
        ["not_determined", "restricted", "denied", "allowed_always"]
    );
    assert_eq!(Authorization::from_raw(4), Authorization::Unknown);
    assert_eq!(Authorization::from_raw(-1), Authorization::Unknown);
}

#[test]
fn unavailable_timeout_only_blames_the_radio_when_authorized() {
    let stage = |raw| unavailable_timeout(Authorization::from_raw(raw));
    assert_eq!(stage(1), ("access_denied", true));
    assert_eq!(stage(2), ("access_denied", true));
    assert_eq!(stage(0), ("permission_pending", true));
    assert_eq!(stage(3), ("radio_off", true));
    assert_eq!(stage(9), ("adapter_unavailable", true));
}
