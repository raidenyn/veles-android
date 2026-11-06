//! OTP-25 standalone per-user installer engine.
//!
//! The engine is deliberately independent of the production host crate and is
//! never linked into it. Task 2 defines the shared strict package model
//! (descriptor identity, payload inventory and the authenticated policy carrier)
//! that every later installer task extends rather than redefines.
//!
//! This crate currently produces portable contract evidence only. It does not
//! perform native installation, signing or acceptance.

pub mod inventory;
pub mod model;
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
pub use policy::{
    CARRIER_HEADER_LEN, CARRIER_MAGIC, CARRIER_VERSION, effective_policy, parse_descriptor,
    read_carrier, write_carrier,
};
