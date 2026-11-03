//! WinRT hint format, target identity and failure classification (pure mappings, no OS calls).
//! Ported from the reviewed feasibility fixtures; these are portable FIXTURES — they do not
//! establish that the Windows adapter compiles or behaves on a real radio.

use veles_ble_host::backend::NormalizedTarget;
use veles_ble_host::winrt_map::{
    AdapterProbe, access_status, bluetooth_error, fallback_label, format_hint, gatt_status,
    normalize_hint, parse_hint, radio_state, scan_precheck,
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
        // The std radix parser would accept a sign or reject whitespace only by accident:
        "winrt:+abbccddeeff:1",
        "winrt:-abbccddeeff:1",
        "winrt: abbccddeef:1",
        "winrt:aabbccddeeff:+",
        "winrt:aabbccddeeff:a",
        "winrt:aabbccddeeff:",
        "winrt::1",
        "macos:aabbccddeeff:1",
    ] {
        assert_eq!(parse_hint(hint), None, "{hint:?}");
        assert_eq!(normalize_hint(hint), None, "{hint:?}");
    }
}

#[test]
fn identity_is_the_address_alone_not_kind_or_hex_case() {
    let canonical = NormalizedTarget::new("winrt:aabbccddeeff");
    for hint in [
        "winrt:aabbccddeeff:0",
        "winrt:aabbccddeeff:1",
        "winrt:aabbccddeeff:2",
        "winrt:AABBCCDDEEFF:1",
        "winrt:AaBbCcDdEeFf:2",
    ] {
        assert_eq!(normalize_hint(hint), Some(canonical.clone()), "{hint:?}");
    }
    // Distinct addresses never collapse, including a one-digit difference and leading zeros.
    assert_ne!(
        normalize_hint("winrt:aabbccddeeff:1"),
        normalize_hint("winrt:aabbccddeefe:1")
    );
    assert_eq!(
        normalize_hint("winrt:000000000001:0"),
        Some(NormalizedTarget::new("winrt:000000000001"))
    );
}

#[test]
fn discovered_hints_normalize_to_the_same_identity_as_their_aliases() {
    let address = 0x1234_5678_9abc;
    for kind in 0..=2 {
        assert_eq!(
            normalize_hint(&format_hint(address, kind)),
            Some(NormalizedTarget::new("winrt:123456789abc"))
        );
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
    let system = access_status(3).unwrap();
    assert_eq!(system.stage, "access_denied_system");
    assert!(!system.retryable, "controlled system denial is fatal");
    assert_eq!(radio_state(1), None);
    assert_eq!(radio_state(2).unwrap().stage, "radio_off");
    assert_eq!(radio_state(3).unwrap().stage, "radio_disabled");
    assert_eq!(radio_state(0).unwrap().stage, "radio_unknown");
}

#[test]
fn gatt_statuses_map_to_stable_stage_names() {
    assert_eq!(gatt_status(0, "write"), None);
    assert_eq!(gatt_status(1, "write").unwrap().stage, "unreachable");
    let denied = gatt_status(3, "write").unwrap();
    assert_eq!(denied.stage, "access_denied");
    assert!(!denied.retryable, "GATT access denial is fatal");
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

fn present(le_supported: bool, radio: Option<i32>) -> AdapterProbe {
    AdapterProbe::Present {
        le_supported,
        radio,
    }
}

#[test]
fn no_adapter_is_fatal_and_radio_off_is_retryable() {
    let none = scan_precheck(AdapterProbe::Missing).expect("no adapter fails");
    assert_eq!((none.stage, none.retryable), ("no_adapter", false));
    let off = scan_precheck(present(true, Some(2))).expect("radio off fails");
    assert_eq!((off.stage, off.retryable), ("radio_off", true));
    let disabled = scan_precheck(present(true, Some(3))).expect("radio disabled fails");
    assert_eq!(
        (disabled.stage, disabled.retryable),
        ("radio_disabled", false)
    );
    let unsupported = scan_precheck(present(false, Some(1))).expect("no LE fails");
    assert_eq!(
        (unsupported.stage, unsupported.retryable),
        ("le_unsupported", false)
    );
    let unknown = scan_precheck(present(true, None)).expect("unobtainable radio fails");
    assert_eq!((unknown.stage, unknown.retryable), ("radio_unknown", true));
}

#[test]
fn restoration_is_a_fresh_request_not_a_retry_loop() {
    // Radio off then restored: the same classification function accepts the restored state;
    // nothing in the mapping retains the earlier failure (no latch, no counter).
    assert!(scan_precheck(present(true, Some(2))).is_some());
    assert_eq!(scan_precheck(present(true, Some(1))), None);
    assert!(scan_precheck(present(true, Some(2))).is_some());
    assert_eq!(scan_precheck(present(true, Some(1))), None);
}

#[test]
fn every_classified_stage_is_a_valid_envelope_token() {
    let mut stages = Vec::new();
    for code in 0..12 {
        stages.extend(bluetooth_error(code));
        stages.extend(access_status(code));
        stages.extend(gatt_status(code, "gatt_profile"));
        stages.extend(radio_state(code));
    }
    stages.extend(scan_precheck(AdapterProbe::Missing));
    stages.extend(scan_precheck(present(false, None)));
    stages.extend(scan_precheck(present(true, None)));
    for failure in stages {
        assert!((1..=32).contains(&failure.stage.len()), "{}", failure.stage);
        assert!(
            failure
                .stage
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_'),
            "{}",
            failure.stage
        );
    }
}

#[test]
fn watcher_is_observably_stopped_only_in_a_terminal_state() {
    use veles_ble_host::winrt_map::watcher_status_is_terminal;
    // 0 Created, 1 Started, 2 Stopping, 3 Stopped, 4 Aborted.
    assert!(watcher_status_is_terminal(0));
    assert!(!watcher_status_is_terminal(1));
    assert!(
        !watcher_status_is_terminal(2),
        "Stopping is not yet stopped"
    );
    assert!(watcher_status_is_terminal(3));
    assert!(watcher_status_is_terminal(4));
    assert!(
        !watcher_status_is_terminal(99),
        "unknown states never count"
    );
}
