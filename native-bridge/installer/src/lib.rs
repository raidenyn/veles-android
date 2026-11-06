//! OTP-25 standalone per-user installer engine.
//!
//! The engine is deliberately independent of the production host crate and is
//! never linked into it. Task 2 defines the shared strict package model
//! (descriptor identity, payload inventory and the authenticated policy carrier)
//! that every later installer task extends rather than redefines. Task 3 adds
//! the fixed per-user layout and the injected OS boundary.
//!
//! This crate currently produces portable contract evidence only. It does not
//! perform native installation, signing or acceptance.

pub mod inventory;
pub mod model;
pub mod paths;
pub mod platform;
pub mod policy;

pub use inventory::{normalize_relative_path, verify_inventory};
pub use model::{
    Channel, ChannelIdentity, DEVELOPMENT_HOST_NAME, Digest, FIXTURE_HOST_NAME, FailureCode,
    InstallFailure, InstallReport, InstallResult, InventoryEntry, MAX_API_VERSION,
    MAX_DOCUMENT_BYTES, MAX_INVENTORY_BYTES, MAX_INVENTORY_ENTRIES, MAX_PATH_BYTES,
    MAX_POLICY_DIGESTS, MAX_PUBLISHER_BYTES, MAX_PUBLISHERS, MIN_API_VERSION, Operation,
    PLACEHOLDER_EXTENSION_ID, PRODUCTION_HOST_NAME, ReleaseDescriptor, ReleasePolicy,
    SCHEMA_VERSION, Target, Version, canonical_json, extension_origin, parse_version,
    validate_extension_id,
};
pub use paths::{
    InstallPaths, JOURNAL_FILE, LOCK_FILE, MACOS_CHROME_HOSTS_DIR, MAINTENANCE_DIR, MAX_ROOT_BYTES,
    RECEIPT_FILE, ROOT_DIR_DEVELOPMENT, ROOT_DIR_FIXTURE, ROOT_DIR_PRODUCTION, STAGING_DIR,
    STATE_DIR, VERSIONS_DIR, root_dir_name,
};
pub use platform::{
    FakePlatform, Faults, HostState, InstallLock, MAX_MANIFEST_PATH_BYTES, NativeManifest,
    OWNERSHIP_SCHEMA_VERSION, OwnedClaim, OwnedEntry, OwnedKind, OwnershipRecord, PackageKind,
    PackageSource, PathKind, Platform, PlatformCall, PortablePlatform, Registration, TrustProof,
    ValidatedPath, WindowsPreimage, claim_owned_entries, claim_owned_registration, parse_manifest,
    path_is_within,
};
pub use policy::{
    CARRIER_HEADER_LEN, CARRIER_MAGIC, CARRIER_VERSION, effective_policy, parse_descriptor,
    read_carrier, write_carrier,
};
