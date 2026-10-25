//! WinRT hint format and failure classification (pure mappings, no OS calls).

use veles_otp23_feasibility::winrt_map::{
    access_status, bluetooth_error, fallback_label, format_hint, gatt_status, parse_hint,
    radio_state,
};

#[test]
fn hint_round_trips_and_masks_to_48_bits() {
    let hint = format_hint(0x00aa_bbcc_ddee_ff01, 1);
    assert_eq!(hint, "winrt:bbccddeeff01:1");
    assert_eq!(parse_hint(&hint), Some((0xbbcc_ddee_ff01, 1)));
    assert_eq!(parse_hint("winrt:000000000001:0"), Some((1, 0)));
    assert_eq!(
        parse_hint("winrt:ffffffffffff:2"),
        Some((0xffff_ffff_ffff, 2))
    );
}

#[test]
fn malformed_hints_are_refused() {
    for hint in [
        "",
        "sim:phone-1",
        "winrt",
        "winrt:aabbccddeeff",
        "winrt:aabbccddeeff:3",
        "winrt:aabbccddeeff:10",
        "winrt:aabbccddee:1",
        "winrt:aabbccddeeffaa:1",
        "winrt:gggggggggggg:1",
        "winrt:aabbccddeeff:1:extra",
        "WINRT:aabbccddeeff:1",
    ] {
        assert_eq!(parse_hint(hint), None, "{hint:?}");
    }
}

#[test]
fn fallback_label_is_short_valid_and_not_the_full_address() {
    let label = fallback_label(0x1234_5678_9abc);
    assert_eq!(label, "BLE device ..9abc");
    assert!(label.len() <= 64 && !label.chars().any(char::is_control));
}

#[test]
fn bluetooth_errors_are_classified_with_retryability() {
    assert_eq!(bluetooth_error(0), None);
    let radio = bluetooth_error(1).unwrap();
    assert_eq!((radio.stage, radio.retryable), ("radio_off", true));
    let policy = bluetooth_error(5).unwrap();
    assert_eq!(
        (policy.stage, policy.retryable),
        ("disabled_by_policy", false)
    );
    let user = bluetooth_error(7).unwrap();
    assert_eq!((user.stage, user.retryable), ("disabled_by_user", true));
    assert_eq!(bluetooth_error(6).unwrap().stage, "unsupported");
    assert_eq!(bluetooth_error(9).unwrap().stage, "unsupported");
    assert_eq!(bluetooth_error(8).unwrap().stage, "consent_required");
    assert_eq!(bluetooth_error(4).unwrap().stage, "os_error");
    assert_eq!(
        bluetooth_error(1234).unwrap().stage,
        "os_error",
        "unknown codes fail closed"
    );
}

#[test]
fn access_and_radio_states_distinguish_denied_from_off() {
    assert_eq!(access_status(0), None); // Unspecified
    assert_eq!(access_status(1), None); // Allowed
    assert_eq!(access_status(2).unwrap().stage, "access_denied_user");
    assert!(access_status(2).unwrap().retryable);
    assert_eq!(access_status(3).unwrap().stage, "access_denied_system");
    assert!(!access_status(3).unwrap().retryable);
    assert_eq!(radio_state(1), None);
    assert_eq!(radio_state(2).unwrap().stage, "radio_off");
    assert_eq!(radio_state(3).unwrap().stage, "radio_disabled");
    assert_eq!(radio_state(0).unwrap().stage, "radio_unknown");
}

#[test]
fn gatt_statuses_map_to_stable_stage_names() {
    assert_eq!(gatt_status(0, "write"), None);
    assert_eq!(gatt_status(1, "write").unwrap().stage, "unreachable");
    assert_eq!(gatt_status(3, "write").unwrap().stage, "access_denied");
    assert_eq!(gatt_status(2, "subscribe").unwrap().stage, "subscribe");
    for code in 0..5 {
        if let Some(failure) = gatt_status(code, "gatt_connect") {
            assert!(failure.stage.len() <= 32);
            assert!(
                failure
                    .stage
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
            );
        }
    }
}
