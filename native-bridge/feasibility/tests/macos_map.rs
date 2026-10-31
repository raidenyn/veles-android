//! macOS CoreBluetooth hint format, link-budget policy and scan bookkeeping
//! (pure mappings, no OS calls; runs on every host).

use veles_otp23_feasibility::fragment::{
    ATTRIBUTE_VALUE_MAX, BudgetError, LinkBudget, LinkCapacity, MAX_MTU, MIN_MTU,
};
use veles_otp23_feasibility::macos_map::{
    Authorization, BoundedSet, GateState, MtuError, ScanGate, fallback_label, format_hint,
    link_budget, parse_hint, unavailable_timeout, valid_uuid,
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

// ---- link budget from the measured write length ------------------------------------------------

#[test]
fn reported_512_is_the_clamped_budget_without_inventing_an_mtu() {
    // CoreBluetooth reports a maximum write-without-response length of 512 on the measured Macs.
    // That is the attribute budget directly (payload 508), identical to an Android phone that
    // negotiated ATT MTU 515, 516 or 517 - no "515" is ever synthesized.
    let budget = link_budget(512).unwrap();
    assert_eq!((budget.attribute(), budget.payload()), (512, 508));
    for mtu in [515, 516, MAX_MTU] {
        assert_eq!(budget, LinkBudget::from_att_mtu(mtu).unwrap(), "mtu {mtu}");
    }
}

#[test]
fn write_lengths_below_512_cannot_establish_a_common_budget_and_fail_closed() {
    // W is a backend limit (<= MTU - 3, possibly platform-capped); equality is never assumed.
    for reported in [0usize, 1, 100, MIN_MTU - 3, 244, 300, 511] {
        assert_eq!(
            link_budget(reported),
            Err(MtuError::Undetermined),
            "{reported}"
        );
        assert_eq!(
            LinkBudget::from_capacity(LinkCapacity::MaxWriteWithoutResponse(reported)),
            Err(BudgetError::Undetermined),
            "{reported}"
        );
    }
    // The ATT MTU path is unaffected: every legal MTU still yields a budget.
    for mtu in MIN_MTU..=MAX_MTU {
        assert!(LinkBudget::from_att_mtu(mtu).is_ok(), "mtu {mtu}");
    }
}

#[test]
fn write_lengths_at_or_above_512_give_the_clamped_budget() {
    for reported in [512usize, 513, 514, 1000, usize::MAX] {
        let budget = link_budget(reported).unwrap();
        assert_eq!(
            (budget.attribute(), budget.payload()),
            (512, 508),
            "{reported}"
        );
        assert_eq!(budget, LinkBudget::from_att_mtu(MAX_MTU).unwrap());
    }
}

#[test]
fn every_accepted_write_length_wraps_every_frame_in_one_os_write() {
    for reported in 0..=MAX_MTU + 10 {
        let Ok(budget) = link_budget(reported) else {
            continue;
        };
        assert!(
            budget.attribute() <= reported && budget.attribute() <= ATTRIBUTE_VALUE_MAX,
            "reported {reported} -> attribute {}",
            budget.attribute()
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
