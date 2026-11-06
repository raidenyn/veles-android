//! Portable contract tests for the strict package identity, payload inventory and
//! conservative repair/rollback policy merge (OTP-25 installer Task 2).
//!
//! Every fixture is synthetic. A passing test here is portable parser/policy
//! evidence only; it is never native signing, installation or acceptance proof.

use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicU64, Ordering};

use veles_bridge_installer::{
    Channel, DEVELOPMENT_HOST_NAME, Digest, FailureCode, InstallResult, InventoryEntry,
    MAX_DOCUMENT_BYTES, MAX_INVENTORY_BYTES, MAX_INVENTORY_ENTRIES, MAX_POLICY_DIGESTS, Operation,
    PLACEHOLDER_EXTENSION_ID, PRODUCTION_HOST_NAME, ROOT_DIR_DEVELOPMENT, ROOT_DIR_PRODUCTION,
    ReleaseDescriptor, ReleasePolicy, Target, canonical_json, effective_policy, extension_origin,
    parse_descriptor, parse_version, read_carrier, verify_inventory, write_carrier,
};

const PROD_EXTENSION_ID: &str = "mnopabcdefghijklmnopabcdefghijkl";

type DescriptorMutation = fn(&mut ReleaseDescriptor);
type DescriptorCase = (&'static str, DescriptorMutation, FailureCode);

fn digest(byte: u8) -> Digest {
    Digest::from_bytes([byte; 32])
}

fn descriptor() -> ReleaseDescriptor {
    ReleaseDescriptor {
        schema: 1,
        channel: Channel::Production,
        version: "1.2.3".to_owned(),
        source_commit: "0123456789abcdef0123456789abcdef01234567".to_owned(),
        target: Target::WindowsX64,
        minimum_os: "11".to_owned(),
        extension_id: PROD_EXTENSION_ID.to_owned(),
        host_name: PRODUCTION_HOST_NAME.to_owned(),
        bundle_id: PRODUCTION_HOST_NAME.to_owned(),
        api_min: 1,
        api_max: 2,
        payload_id: digest(0x11),
        policy: ReleasePolicy {
            publishers: vec!["Publisher One".to_owned()],
            denied_payloads: Vec::new(),
            rollback_payloads: Vec::new(),
            epoch: 3,
        },
    }
}

fn descriptor_bytes(descriptor: &ReleaseDescriptor) -> Vec<u8> {
    canonical_json(descriptor).expect("test descriptors serialize canonically")
}

fn failure_code<T: std::fmt::Debug>(result: InstallResult<T>) -> FailureCode {
    result.expect_err("expected a failure").code
}

#[test]
fn production_rejects_placeholder_multiple_and_dev_origins() {
    let valid = descriptor();
    assert_eq!(
        parse_descriptor(&descriptor_bytes(&valid)).expect("baseline production descriptor parses"),
        valid
    );

    // A production descriptor must carry one bare, non-placeholder Chrome ID and
    // the production host/bundle identity. The development identity is
    // `me.nagaev.veles.bridge.dev`: a descriptor that claims the production
    // channel but carries those names is a development package mislabelled as a
    // production release (there is no fixed development *extension* ID to match,
    // because the dev workflow takes an explicit unpacked-extension ID).
    let cases: &[(&str, DescriptorMutation)] = &[
        ("existing placeholder id", |d| {
            d.extension_id = PLACEHOLDER_EXTENSION_ID.to_owned();
        }),
        ("wildcard id", |d| d.extension_id = "*".to_owned()),
        ("wildcard origin", |d| {
            d.extension_id = "chrome-extension://*/".to_owned();
        }),
        ("comma-separated id list", |d| {
            d.extension_id = format!("{PROD_EXTENSION_ID},{}", "p".repeat(32));
        }),
        ("wrong scheme", |d| {
            d.extension_id = format!("https://{PROD_EXTENSION_ID}/");
        }),
        ("full chrome-extension origin instead of bare id", |d| {
            d.extension_id = format!("chrome-extension://{PROD_EXTENSION_ID}/");
        }),
        ("development host name", |d| {
            d.host_name = DEVELOPMENT_HOST_NAME.to_owned();
        }),
        ("development bundle id", |d| {
            d.bundle_id = DEVELOPMENT_HOST_NAME.to_owned();
        }),
    ];

    for (name, mutate) in cases {
        let mut candidate = valid.clone();
        mutate(&mut candidate);
        assert_eq!(
            failure_code(parse_descriptor(&descriptor_bytes(&candidate))),
            FailureCode::OriginRejected,
            "case: {name}"
        );
    }

    // The inverse direction is rejected too: a development descriptor may not
    // claim the production host/bundle identity.
    let mut mislabelled = valid.clone();
    mislabelled.channel = Channel::Development;
    assert_eq!(
        failure_code(parse_descriptor(&descriptor_bytes(&mislabelled))),
        FailureCode::OriginRejected
    );
}

#[test]
fn descriptor_identity_is_strict_and_versions_compare_numerically() {
    assert!(parse_version("1.10.0").expect("numeric version") > parse_version("1.9.9").unwrap());
    assert_eq!(parse_version("2.0.0").unwrap().to_string(), "2.0.0");
    for bad in [
        "", "1", "1.2", "1.2.3.4", "01.2.3", "1.02.3", "1.2.03", "-1.2.3", "1.2.x", " 1.2.3",
        "1.2.3 ",
    ] {
        assert!(
            parse_version(bad).is_err(),
            "version {bad:?} must be rejected"
        );
    }

    let valid = descriptor();
    assert_eq!(valid.parsed_version().unwrap().to_string(), "1.2.3");
    assert_eq!(
        extension_origin(PROD_EXTENSION_ID).unwrap(),
        format!("chrome-extension://{PROD_EXTENSION_ID}/")
    );
    assert_eq!(
        failure_code(extension_origin(PLACEHOLDER_EXTENSION_ID)),
        FailureCode::OriginRejected
    );
    assert_eq!(
        Channel::Development.identity().bundle_id,
        DEVELOPMENT_HOST_NAME
    );
    assert_eq!(
        Channel::Production.identity().host_name,
        PRODUCTION_HOST_NAME
    );

    let cases: &[DescriptorCase] = &[
        (
            "unsupported schema",
            |d| d.schema = 2,
            FailureCode::InvalidPackage,
        ),
        (
            "two-component version",
            |d| d.version = "1.2".to_owned(),
            FailureCode::InvalidPackage,
        ),
        (
            "short source commit",
            |d| d.source_commit = "deadbeef".to_owned(),
            FailureCode::InvalidPackage,
        ),
        (
            "uppercase source commit",
            |d| d.source_commit = "0123456789ABCDEF0123456789ABCDEF01234567".to_owned(),
            FailureCode::InvalidPackage,
        ),
        (
            "minimum OS below the tested baseline",
            |d| d.minimum_os = "10".to_owned(),
            FailureCode::UnsupportedTarget,
        ),
        (
            "malformed minimum OS",
            |d| d.minimum_os = "eleven".to_owned(),
            FailureCode::InvalidPackage,
        ),
        (
            "zero api floor",
            |d| d.api_min = 0,
            FailureCode::InvalidPackage,
        ),
        (
            "inverted api range",
            |d| {
                d.api_min = 3;
            },
            FailureCode::InvalidPackage,
        ),
        (
            "no trusted publisher",
            |d| d.policy.publishers.clear(),
            FailureCode::InvalidPackage,
        ),
        (
            "duplicate denied digest",
            |d| d.policy.denied_payloads = vec![digest(1), digest(1)],
            FailureCode::InvalidPackage,
        ),
        (
            "publisher with surrounding whitespace",
            |d| d.policy.publishers = vec![" Publisher One ".to_owned()],
            FailureCode::InvalidPackage,
        ),
    ];

    for (name, mutate, expected) in cases {
        let mut candidate = valid.clone();
        mutate(&mut candidate);
        assert_eq!(
            failure_code(parse_descriptor(&descriptor_bytes(&candidate))),
            *expected,
            "case: {name}"
        );
    }
}

#[test]
fn inventory_rejects_escape_duplicate_case_alias_and_partial_content() {
    let root = TempRoot::new();
    root.write("ok.bin", b"abc");
    root.write("dir/ok2.bin", b"defg");

    verify_inventory(&root.path, &[]).expect("an empty inventory is structurally valid");
    verify_inventory(
        &root.path,
        &[entry("ok.bin", b"abc"), entry("dir/ok2.bin", b"defg")],
    )
    .expect("matching inventory verifies");

    let bad_paths: &[&str] = &[
        "../x",
        "/etc/passwd",
        "C:/Windows/x",
        "C:x",
        "file.txt:stream",
        "nul\u{0}.txt",
        "..\\x",
        "",
        "a//b",
        "trailing./x",
        "trailing /x",
        "./x",
    ];
    for path in bad_paths {
        let entries = [InventoryEntry {
            path: (*path).to_owned(),
            size: 0,
            sha256: Digest::ZERO,
            executable: false,
        }];
        assert_eq!(
            failure_code(verify_inventory(&root.path, &entries)),
            FailureCode::UnsafePath,
            "path: {path:?}"
        );
    }

    let long_path = "a".repeat(1025);
    assert_eq!(
        failure_code(verify_inventory(
            &root.path,
            &[InventoryEntry {
                path: long_path,
                size: 0,
                sha256: Digest::ZERO,
                executable: false,
            }]
        )),
        FailureCode::UnsafePath
    );

    root.write("a", b"same");
    root.write("A", b"same");
    assert_eq!(
        failure_code(verify_inventory(
            &root.path,
            &[entry("a", b"same"), entry("A", b"same")]
        )),
        FailureCode::UnsafePath,
        "case-folded alias"
    );

    assert_eq!(
        failure_code(verify_inventory(
            &root.path,
            &[entry("ok.bin", b"abc"), entry("ok.bin", b"abc")]
        )),
        FailureCode::UnsafePath,
        "duplicate archive member"
    );

    #[cfg(unix)]
    {
        use std::os::unix::fs::symlink;

        symlink(root.path.join("ok.bin"), root.path.join("link.bin")).expect("symlink fixture");
        assert_eq!(
            failure_code(verify_inventory(&root.path, &[entry("link.bin", b"abc")])),
            FailureCode::UnsafePath,
            "an inventory entry may never be a symlink"
        );
    }

    root.write("wrong.bin", b"abcd");
    assert_eq!(
        failure_code(verify_inventory(
            &root.path,
            &[InventoryEntry {
                path: "wrong.bin".to_owned(),
                size: 4,
                sha256: Digest::ZERO,
                executable: false,
            }]
        )),
        FailureCode::InvalidPackage,
        "correct size with the wrong hash"
    );

    assert_eq!(
        failure_code(verify_inventory(
            &root.path,
            &[InventoryEntry {
                path: "wrong.bin".to_owned(),
                size: 5,
                sha256: Digest::of(b"abcd"),
                executable: false,
            }]
        )),
        FailureCode::InvalidPackage,
        "declared size differs from the file"
    );

    assert_eq!(
        failure_code(verify_inventory(&root.path, &[entry("absent.bin", b"x")])),
        FailureCode::InvalidPackage,
        "inventoried file is missing"
    );

    let too_many: Vec<InventoryEntry> = (0..=MAX_INVENTORY_ENTRIES)
        .map(|index| InventoryEntry {
            path: format!("member-{index}"),
            size: 0,
            sha256: Digest::ZERO,
            executable: false,
        })
        .collect();
    assert_eq!(
        failure_code(verify_inventory(&root.path, &too_many)),
        FailureCode::InvalidInput,
        "inventory entry count limit"
    );

    let oversized = [
        InventoryEntry {
            path: "large-1".to_owned(),
            size: MAX_INVENTORY_BYTES / 2 + 1,
            sha256: Digest::ZERO,
            executable: false,
        },
        InventoryEntry {
            path: "large-2".to_owned(),
            size: MAX_INVENTORY_BYTES / 2 + 1,
            sha256: Digest::ZERO,
            executable: false,
        },
    ];
    assert_eq!(
        failure_code(verify_inventory(&root.path, &oversized)),
        FailureCode::InvalidInput,
        "inventory total size limit"
    );
}

/// `verify_inventory` checks the declared entries and never enumerates the root.
///
/// An unreferenced extra file - the Windows DLL-sideload shape - is therefore
/// accepted, and an empty inventory verifies against any directory. This test
/// pins that documented boundary: callers requiring a closed tree must walk
/// `root` themselves.
#[test]
fn inventory_verification_does_not_enumerate_the_root() {
    let root = TempRoot::new();
    root.write("host.exe", b"host");
    root.write("planted.dll", b"never inventoried");

    verify_inventory(&root.path, &[entry("host.exe", b"host")])
        .expect("an unreferenced file does not fail declared-entry verification");
    verify_inventory(&root.path, &[]).expect("an empty inventory does not enumerate the root");
}

fn policy(
    epoch: u64,
    publishers: &[&str],
    denied: &[Digest],
    rollback: &[Digest],
) -> ReleasePolicy {
    ReleasePolicy {
        publishers: publishers.iter().map(|value| (*value).to_owned()).collect(),
        denied_payloads: denied.to_vec(),
        rollback_payloads: rollback.to_vec(),
        epoch,
    }
}

fn digest_set(values: &[Digest]) -> HashSet<Digest> {
    values.iter().copied().collect()
}

#[test]
fn rollback_cannot_erase_known_denial() {
    let d1 = digest(1);
    let d2 = digest(2);
    let r1 = digest(10);
    let r2 = digest(11);
    let r3 = digest(12);

    let current = policy(7, &["Publisher A", "Publisher B"], &[d1], &[r1, r2]);
    // A retained prior release: older epoch, a denial the live policy does not
    // know, a publisher only it trusts and a rollback payload only it allows.
    let incoming = policy(6, &["Publisher B", "Publisher C"], &[d2], &[r2, r3]);

    let merged =
        effective_policy(&current, &incoming, Operation::Rollback).expect("conservative rollback");
    assert_eq!(
        merged.epoch, current.epoch,
        "an older policy cannot lower the epoch"
    );
    assert_eq!(
        digest_set(&merged.denied_payloads),
        digest_set(&[d1, d2]),
        "denials accumulate"
    );
    assert_eq!(
        merged.publishers,
        vec!["Publisher B".to_owned()],
        "publisher eligibility is the intersection"
    );
    assert_eq!(
        merged.rollback_payloads,
        vec![r2],
        "rollback eligibility is the intersection"
    );

    let erases_denial = policy(6, &["Publisher A"], &[], &[r2]);
    let merged = effective_policy(&current, &erases_denial, Operation::Rollback).unwrap();
    assert!(
        digest_set(&merged.denied_payloads).contains(&d1),
        "a known denial survives an incoming policy that omits it"
    );

    // Forward changes only through Install with a higher epoch; authenticated
    // release authorization is Task 4's job, not this function's.
    assert_eq!(
        failure_code(effective_policy(
            &current,
            &policy(6, &["Publisher A", "Publisher B"], &[], &[r1, r2]),
            Operation::Install
        )),
        FailureCode::PolicyDenied,
        "an older installer cannot install with a stale policy"
    );
    assert_eq!(
        failure_code(effective_policy(&current, &incoming, Operation::Install)),
        FailureCode::PolicyDenied,
        "an older installer cannot relax policy on install"
    );

    let relaxed_forward = policy(8, &["Publisher D"], &[], &[r1]);
    assert_eq!(
        effective_policy(&current, &relaxed_forward, Operation::Install).unwrap(),
        relaxed_forward,
        "a higher epoch is an explicitly forward policy, authorized by Task 4"
    );
    assert_eq!(
        failure_code(effective_policy(
            &current,
            &policy(7, &["Publisher D"], &[], &[r1]),
            Operation::Install
        )),
        FailureCode::PolicyDenied,
        "same-epoch policy changes are not silently accepted"
    );

    assert_eq!(
        failure_code(effective_policy(&current, &incoming, Operation::Repair)),
        FailureCode::PolicyDenied,
        "repair cannot consume a stale policy"
    );
    assert_eq!(
        failure_code(effective_policy(
            &current,
            &policy(7, &["Publisher A", "Publisher B"], &[], &[r1, r2]),
            Operation::Repair
        )),
        FailureCode::PolicyDenied,
        "repair cannot drop a known denial"
    );
    assert_eq!(
        failure_code(effective_policy(
            &current,
            &policy(
                7,
                &["Publisher A", "Publisher B", "Publisher C"],
                &[d1],
                &[r1, r2]
            ),
            Operation::Repair
        )),
        FailureCode::PolicyDenied,
        "repair cannot add a publisher"
    );

    let tighter_repair = policy(7, &["Publisher A"], &[d1, d2], &[r1]);
    let merged = effective_policy(&current, &tighter_repair, Operation::Repair)
        .expect("a tighter same-epoch repair policy is acceptable");
    assert_eq!(digest_set(&merged.denied_payloads), digest_set(&[d1, d2]));
    assert_eq!(merged.publishers, vec!["Publisher A".to_owned()]);
    assert_eq!(merged.rollback_payloads, vec![r1]);

    assert_eq!(
        failure_code(effective_policy(
            &current,
            &policy(7, &["Publisher Z"], &[d1], &[r1, r2]),
            Operation::Rollback
        )),
        FailureCode::PolicyDenied,
        "disjoint publisher sets leave nothing trusted"
    );
    assert_eq!(
        failure_code(effective_policy(
            &current,
            &policy(8, &["Publisher A"], &[d1], &[r1, r2]),
            Operation::Rollback
        )),
        FailureCode::PolicyDenied,
        "a rollback cannot carry a forward policy relaxation"
    );
    assert_eq!(
        effective_policy(&current, &incoming, Operation::Inspect).unwrap(),
        current
    );
    assert_eq!(
        effective_policy(&current, &incoming, Operation::Uninstall).unwrap(),
        current
    );

    let malformed = policy(7, &[], &[d1], &[r1, r2]);
    assert_eq!(
        failure_code(effective_policy(&current, &malformed, Operation::Rollback)),
        FailureCode::InvalidPackage,
        "policies without any trusted publisher are malformed"
    );
}

fn carrier_with(
    magic: &[u8; 8],
    version: u16,
    flags: u16,
    header_len: u32,
    offset: u64,
    length: u64,
    body: &[u8],
) -> Vec<u8> {
    let mut carrier = Vec::new();
    carrier.extend_from_slice(magic);
    carrier.extend_from_slice(&version.to_le_bytes());
    carrier.extend_from_slice(&flags.to_le_bytes());
    carrier.extend_from_slice(&header_len.to_le_bytes());
    carrier.extend_from_slice(&offset.to_le_bytes());
    carrier.extend_from_slice(&length.to_le_bytes());
    carrier.extend_from_slice(body);
    carrier
}

#[test]
fn carrier_bounds_and_strict_json() {
    let descriptor = descriptor();
    let bytes = descriptor_bytes(&descriptor);
    let carrier = write_carrier(&bytes).expect("descriptor fits the carrier");
    assert_eq!(read_carrier(&carrier).expect("valid carrier"), descriptor);

    let text = String::from_utf8(bytes.clone()).unwrap();
    let duplicate = format!("{{\"schema\":1,{}", &text[1..]);
    assert_eq!(
        failure_code(read_carrier(
            &write_carrier(duplicate.as_bytes()).expect("fits")
        )),
        FailureCode::InvalidPackage,
        "duplicate descriptor keys"
    );

    let unknown = format!("{{\"unexpected\":true,{}", &text[1..]);
    assert_eq!(
        failure_code(read_carrier(
            &write_carrier(unknown.as_bytes()).expect("fits")
        )),
        FailureCode::InvalidPackage,
        "unknown descriptor keys"
    );

    let nested_duplicate = text.replace("\"epoch\":3", "\"epoch\":3,\"epoch\":4");
    assert_ne!(
        nested_duplicate, text,
        "fixture must contain the policy epoch"
    );
    assert_eq!(
        failure_code(read_carrier(
            &write_carrier(nested_duplicate.as_bytes()).expect("fits")
        )),
        FailureCode::InvalidPackage,
        "duplicate keys inside the nested policy"
    );

    let mut trailing = bytes.clone();
    trailing.extend_from_slice(b"{}");
    assert_eq!(
        failure_code(read_carrier(&write_carrier(&trailing).unwrap())),
        FailureCode::InvalidPackage,
        "trailing bytes after the descriptor"
    );

    let mut huge = bytes.clone();
    huge.extend(std::iter::repeat_n(b' ', MAX_DOCUMENT_BYTES));
    assert_eq!(
        failure_code(parse_descriptor(&huge)),
        FailureCode::InvalidPackage,
        "a descriptor larger than 256 KiB"
    );
    assert_eq!(
        failure_code(write_carrier(&huge)),
        FailureCode::InvalidInput,
        "the carrier writer refuses an oversized descriptor"
    );
    assert_eq!(
        failure_code(write_carrier(&[])),
        FailureCode::InvalidInput,
        "the carrier writer refuses an empty descriptor"
    );

    let magic = *b"VELESPC1";
    let body = bytes.clone();
    let offset = 32u64;
    let length = body.len() as u64;
    let ok = carrier_with(&magic, 1, 0, 32, offset, length, &body);
    assert_eq!(read_carrier(&ok).unwrap(), descriptor);

    let padded = {
        let mut body = vec![0u8; 8];
        body.extend_from_slice(&bytes);
        let mut carrier = carrier_with(&magic, 1, 0, 32, 40, length, &body);
        assert_eq!(read_carrier(&carrier).unwrap(), descriptor);
        carrier.extend_from_slice(&vec![0xAA; MAX_DOCUMENT_BYTES]);
        carrier
    };
    assert_eq!(
        read_carrier(&padded).unwrap(),
        descriptor,
        "bytes outside the bounded descriptor section are ignored"
    );

    let bad_carriers: &[(&str, Vec<u8>, FailureCode)] = &[
        (
            "truncated header",
            vec![0u8; 31],
            FailureCode::InvalidPackage,
        ),
        (
            "wrong magic",
            carrier_with(b"VELESPC2", 1, 0, 32, offset, length, &body),
            FailureCode::InvalidPackage,
        ),
        (
            "unsupported version",
            carrier_with(&magic, 2, 0, 32, offset, length, &body),
            FailureCode::InvalidPackage,
        ),
        (
            // Bit 0 is the sealed-inventory flag (F7); every other bit is unknown.
            "unknown flags",
            carrier_with(&magic, 1, 2, 32, offset, length, &body),
            FailureCode::InvalidPackage,
        ),
        (
            "unexpected header length",
            carrier_with(&magic, 1, 0, 16, offset, length, &body),
            FailureCode::InvalidPackage,
        ),
        (
            "offset inside the header",
            carrier_with(&magic, 1, 0, 32, 16, length, &body),
            FailureCode::InvalidPackage,
        ),
        (
            "offset past the carrier",
            carrier_with(&magic, 1, 0, 32, offset + length + 1, length, &body),
            FailureCode::InvalidPackage,
        ),
        (
            "offset plus length overflows",
            carrier_with(&magic, 1, 0, 32, u64::MAX, 8, &body),
            FailureCode::InvalidPackage,
        ),
        (
            "zero length",
            carrier_with(&magic, 1, 0, 32, offset, 0, &body),
            FailureCode::InvalidPackage,
        ),
        (
            "length beyond 256 KiB",
            {
                let mut padded_body = bytes.clone();
                padded_body.resize(MAX_DOCUMENT_BYTES + 1, b' ');
                carrier_with(
                    &magic,
                    1,
                    0,
                    32,
                    offset,
                    padded_body.len() as u64,
                    &padded_body,
                )
            },
            FailureCode::InvalidPackage,
        ),
        (
            "descriptor section is not JSON",
            carrier_with(&magic, 1, 0, 32, offset, 3, b"nope"),
            FailureCode::InvalidPackage,
        ),
        (
            "placeholder identity inside the carrier",
            {
                let mut invalid = descriptor.clone();
                invalid.extension_id = PLACEHOLDER_EXTENSION_ID.to_owned();
                let invalid = descriptor_bytes(&invalid);
                carrier_with(&magic, 1, 0, 32, offset, invalid.len() as u64, &invalid)
            },
            FailureCode::OriginRejected,
        ),
    ];

    for (name, carrier, expected) in bad_carriers {
        assert_eq!(
            failure_code(read_carrier(carrier)),
            *expected,
            "case: {name}"
        );
    }
}

#[test]
fn release_config_example_and_schema_agree_with_strict_model() {
    let config = Path::new(env!("CARGO_MANIFEST_DIR")).join("config");
    let schema: serde_json::Value =
        serde_json::from_slice(&fs::read(config.join("release.schema.json")).unwrap())
            .expect("release schema is JSON");
    assert_eq!(schema["additionalProperties"], false);
    assert_eq!(schema["properties"]["schema"]["const"], 1);

    let example = fs::read(config.join("release.example.json")).unwrap();
    let descriptor = parse_descriptor(&example).expect("the example descriptor is valid");
    assert_eq!(descriptor.channel, Channel::Development);
    assert_eq!(descriptor.host_name, DEVELOPMENT_HOST_NAME);
    assert_eq!(descriptor.bundle_id, DEVELOPMENT_HOST_NAME);

    let model = serde_json::to_value(&descriptor).unwrap();
    let model_fields: HashSet<String> = model
        .as_object()
        .expect("descriptor object")
        .keys()
        .cloned()
        .collect();
    let schema_fields: HashSet<String> = schema["properties"]
        .as_object()
        .expect("schema properties")
        .keys()
        .cloned()
        .collect();
    assert_eq!(
        model_fields, schema_fields,
        "the schema must describe exactly the strict descriptor fields"
    );
    let required: HashSet<String> = schema["required"]
        .as_array()
        .expect("schema required list")
        .iter()
        .map(|value| value.as_str().expect("required name").to_owned())
        .collect();
    assert_eq!(required, model_fields, "every descriptor field is required");
}

#[test]
fn canonical_json_is_compact_sorted_and_newline_terminated() {
    let canonical = canonical_json(&descriptor()).unwrap();
    assert!(canonical.ends_with(b"\n"));
    let text = String::from_utf8(canonical).unwrap();
    assert!(text.starts_with("{\"api_max\":"));
    assert!(text.find("\"api_max\"").unwrap() < text.find("\"api_min\"").unwrap());
    assert!(text.find("\"host_name\"").unwrap() < text.find("\"minimum_os\"").unwrap());
    assert!(!text.contains("\n "), "compact, single-line JSON");
}

/// `FailureCode::as_str()` is documented as identical to the serde wire name;
/// keep the hand-written match and the derive from drifting apart.
#[test]
fn failure_code_as_str_matches_its_serde_representation() {
    let codes = [
        FailureCode::InvalidInput,
        FailureCode::OriginRejected,
        FailureCode::InvalidPackage,
        FailureCode::TrustDenied,
        FailureCode::TrustUnavailable,
        FailureCode::PolicyDenied,
        FailureCode::UnsupportedTarget,
        FailureCode::Busy,
        FailureCode::HostActive,
        FailureCode::InspectionDenied,
        FailureCode::ForeignState,
        FailureCode::UnsafePath,
        FailureCode::Io,
        FailureCode::RecoveryRequired,
        FailureCode::NoRollback,
        FailureCode::RepairPackageRequired,
        FailureCode::CleanupPending,
    ];
    let mut wire_names = HashSet::new();
    for code in codes {
        let serialized = serde_json::to_value(code).expect("failure codes serialize");
        assert_eq!(
            serialized,
            serde_json::Value::String(code.as_str().to_owned()),
            "as_str drift for {code:?}"
        );
        assert!(
            wire_names.insert(code.as_str()),
            "duplicate wire name for {code:?}"
        );
    }
}

fn entry(path: &str, content: &[u8]) -> InventoryEntry {
    InventoryEntry {
        path: path.to_owned(),
        size: content.len() as u64,
        sha256: Digest::of(content),
        executable: false,
    }
}

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

struct TempRoot {
    path: PathBuf,
}

impl TempRoot {
    fn new() -> Self {
        let unique = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
        let path =
            std::env::temp_dir().join(format!("veles-installer-task2-{}-{unique}", process::id()));
        if path.exists() {
            fs::remove_dir_all(&path).expect("stale temp root");
        }
        fs::create_dir_all(&path).expect("temp root");
        Self { path }
    }

    fn write(&self, relative: &str, content: &[u8]) {
        let path = self.path.join(relative);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).expect("parent directory");
        }
        fs::write(path, content).expect("fixture file");
    }
}

impl Drop for TempRoot {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

/// F8: the inventory primitive itself must refuse a path that reaches a regular
/// file *through* a linked ancestor, and must do so before reading the target.
#[test]
fn inventory_verification_rejects_an_escaping_ancestor_link() {
    let temp = TempRoot::new();
    let outside = temp.path.join("outside");
    fs::create_dir_all(&outside).expect("outside directory");
    fs::write(outside.join("host"), b"outside host").expect("outside file");
    let package = temp.path.join("package");
    fs::create_dir_all(&package).expect("package root");

    #[cfg(unix)]
    std::os::unix::fs::symlink(&outside, package.join("dir")).expect("ancestor symlink");
    #[cfg(windows)]
    std::os::windows::fs::symlink_dir(&outside, package.join("dir")).expect("ancestor link");

    let entries = [InventoryEntry {
        path: "dir/host".to_owned(),
        size: b"outside host".len() as u64,
        sha256: Digest::of(b"outside host"),
        executable: true,
    }];
    assert_eq!(
        failure_code(verify_inventory(&package, &entries)),
        FailureCode::UnsafePath,
        "an inventory path through a linked ancestor escapes the package root"
    );

    // A contained directory chain still verifies.
    fs::create_dir_all(package.join("real/inner")).expect("real chain");
    fs::write(package.join("real/inner/host"), b"outside host").expect("contained file");
    verify_inventory(
        &package,
        &[InventoryEntry {
            path: "real/inner/host".to_owned(),
            ..entries[0].clone()
        }],
    )
    .expect("a contained path verifies");
}

/// F10: a merge that accumulates denials beyond the representable bound must fail
/// with a deliberate policy code, never return a policy its own validator rejects.
#[test]
fn merged_policy_that_cannot_be_represented_is_a_policy_denial() {
    let denials = |range: std::ops::Range<u32>| -> Vec<Digest> {
        range.map(|n| Digest::of(&n.to_le_bytes())).collect()
    };
    let policy = |denied: Vec<Digest>| ReleasePolicy {
        publishers: vec!["Publisher One".to_owned()],
        denied_payloads: denied,
        rollback_payloads: Vec::new(),
        epoch: 3,
    };

    // Two individually valid policies denying 3000 *distinct* digests each.
    let current = policy(denials(0..3000));
    let incoming = policy(denials(3000..6000));
    current.validate().expect("valid on its own");
    incoming.validate().expect("valid on its own");
    let outcome = effective_policy(&current, &incoming, Operation::Rollback);
    match outcome {
        Err(failure) => assert_eq!(failure.code, FailureCode::PolicyDenied),
        Ok(merged) => panic!(
            "merge returned a policy with {} denials that its own validator rejects: {:?}",
            merged.denied_payloads.len(),
            merged.validate().expect_err("over the bound").code
        ),
    }

    // Exactly at the bound is still representable and still accumulates.
    let half = u32::try_from(MAX_POLICY_DIGESTS / 2).expect("bound fits");
    let merged = effective_policy(
        &policy(denials(0..half)),
        &policy(denials(half..2 * half)),
        Operation::Rollback,
    )
    .expect("a merge at the bound is representable");
    assert_eq!(merged.denied_payloads.len(), MAX_POLICY_DIGESTS);
    merged.validate().expect("and valid");
}

/// A development descriptor carries an explicit unpacked-extension ID and the
/// development identity; it can neither borrow the production identity nor use
/// the placeholder, and its root and host name stay distinct from production
/// (OTP-25 Task 12).
#[test]
fn development_descriptor_binds_the_dev_identity_to_an_explicit_id() {
    const DEV_EXTENSION_ID: &str = "hgfedcbahgfedcbahgfedcbahgfedcba";
    let mut dev = descriptor();
    dev.channel = Channel::Development;
    dev.extension_id = DEV_EXTENSION_ID.to_owned();
    dev.host_name = DEVELOPMENT_HOST_NAME.to_owned();
    dev.bundle_id = DEVELOPMENT_HOST_NAME.to_owned();
    let parsed = parse_descriptor(&descriptor_bytes(&dev)).expect("explicit dev descriptor parses");
    assert_eq!(parsed.channel, Channel::Development);
    assert_eq!(
        extension_origin(&parsed.extension_id).unwrap(),
        format!("chrome-extension://{DEV_EXTENSION_ID}/")
    );
    assert_ne!(
        extension_origin(&parsed.extension_id).unwrap(),
        extension_origin(PROD_EXTENSION_ID).unwrap(),
        "dev and production origins must differ"
    );

    let cases: &[(&str, DescriptorMutation)] = &[
        ("placeholder id", |d| {
            d.extension_id = PLACEHOLDER_EXTENSION_ID.to_owned();
        }),
        ("wildcard id", |d| d.extension_id = "*".to_owned()),
        ("production host name", |d| {
            d.host_name = PRODUCTION_HOST_NAME.to_owned();
        }),
        ("production bundle id", |d| {
            d.bundle_id = PRODUCTION_HOST_NAME.to_owned();
        }),
        ("fixture host name", |d| {
            d.host_name = "me.nagaev.veles.bridge.fixture".to_owned();
        }),
    ];
    for (name, mutate) in cases {
        let mut candidate = dev.clone();
        mutate(&mut candidate);
        assert_eq!(
            failure_code(parse_descriptor(&descriptor_bytes(&candidate))),
            FailureCode::OriginRejected,
            "case: {name}"
        );
    }

    // Distinct identities and roots; the production connector name is untouched.
    assert_eq!(ROOT_DIR_DEVELOPMENT, "NativeBridgeDev");
    assert_eq!(ROOT_DIR_PRODUCTION, "NativeBridge");
    assert_ne!(DEVELOPMENT_HOST_NAME, PRODUCTION_HOST_NAME);
    assert_eq!(
        DEVELOPMENT_HOST_NAME,
        format!("{PRODUCTION_HOST_NAME}.dev"),
        "the development name is the production name plus `.dev`, never the reverse"
    );
}

/// The Python development wrappers hard-code the identities they refuse and select; keep them
/// from drifting away from the engine's constants (OTP-25 Task 12).
#[test]
fn development_wrapper_constants_match_the_engine() {
    let source = fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("scripts")
            .join("development.py"),
    )
    .expect("development.py is part of the installer tree");
    let constant = |name: &str| -> String {
        let prefix = format!("{name} = \"");
        let line = source
            .lines()
            .find(|line| line.starts_with(&prefix))
            .unwrap_or_else(|| panic!("development.py defines {name}"));
        line[prefix.len()..line.len() - 1].to_owned()
    };
    assert_eq!(constant("PRODUCTION_HOST_NAME"), PRODUCTION_HOST_NAME);
    assert_eq!(constant("DEV_HOST_NAME"), DEVELOPMENT_HOST_NAME);
    assert_eq!(constant("PRODUCTION_ROOT_DIR"), ROOT_DIR_PRODUCTION);
    assert_eq!(constant("DEV_ROOT_DIR"), ROOT_DIR_DEVELOPMENT);
    assert_eq!(
        constant("PLACEHOLDER_EXTENSION_ID"),
        PLACEHOLDER_EXTENSION_ID
    );
}
