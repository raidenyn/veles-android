//! OTP-25 standalone per-user installer engine.
//!
//! The engine is deliberately independent of the production host crate and is
//! never linked into it. Task 2 defines the shared strict package model
//! (descriptor identity, payload inventory and the authenticated policy carrier)
//! that every later installer task extends rather than redefines. Task 3 adds
//! the fixed per-user layout and the injected OS boundary. Task 7 adds the
//! operation layer ([`operations::run_operation`]) for inspect, install, repair,
//! rollback and uninstall over the journaled transaction.
//!
//! Task 10 adds the closed command-line surface ([`cli`]) the native frontends
//! drive, and the short-lived cleanup launcher.
//!
//! This crate currently produces portable contract evidence only. It does not
//! perform native installation, signing or acceptance.

pub mod cleanup;
pub mod cli;
pub mod embedded;
pub mod host;
pub mod inventory;
pub mod journal;
pub mod model;
pub mod operations;
pub mod paths;
pub mod platform;
pub mod policy;
pub mod removal;
pub mod retained;
pub mod transaction;

/// The channel this build installs - compiled in, never a runtime choice.
///
/// The producers build one engine per channel: production by default, and the fixture or development
/// engine with `--cfg veles_channel="fixture"` / `"development"` in RUSTFLAGS. A fixture or development
/// engine therefore resolves only its own root, registration name and identity (`InstallPaths`), and a
/// production engine can never be steered to either by an argument, a package or the environment.
#[cfg(all(veles_channel = "fixture", veles_channel = "development"))]
compile_error!("at most one veles_channel may be selected");
#[cfg(not(any(veles_channel = "fixture", veles_channel = "development")))]
pub const COMPILED_CHANNEL: Channel = Channel::Production;
#[cfg(veles_channel = "fixture")]
pub const COMPILED_CHANNEL: Channel = Channel::Fixture;
#[cfg(veles_channel = "development")]
pub const COMPILED_CHANNEL: Channel = Channel::Development;

pub use cleanup::{
    CLEANUP_LAUNCHER_ARG, CLEANUP_MAGIC, CLEANUP_SCHEMA_VERSION, CleanupChannel, CleanupCompletion,
    CleanupEntry, CleanupFailure, CleanupFileSystem, CleanupReason, CleanupRequest, CleanupStatus,
    CleanupTree, HelperImage, HelperRun, MAX_CLEANUP_ENTRIES, MAX_CLEANUP_FRAME_BYTES,
    MAX_CLEANUP_TIMEOUT, ParentEvidence, ParentIdentity, ParentState, RemoveOutcome, StdCleanupFs,
    StdHelperImage, encode_hex, execute_cleanup, hmac_sha256, parent_identity, parse_request,
    remove_helper_after_exit, run_cleanup_helper, run_cleanup_helper_detailed,
    run_cleanup_helper_via_launcher, run_cleanup_launcher,
};
pub use embedded::{EMBEDDED_CARRIER_ENV, decode_embedded_carrier};
pub use host::{HostOs, HostPlatform, PlatformKind, host_platform, platform_kind_for};
pub use inventory::{
    INVENTORY_SCHEMA_VERSION, PACKAGE_INVENTORY_FILE, PACKAGE_PAYLOAD_DIR, SEALED_INVENTORY_FILE,
    is_signable_image, normalize_relative_path, parse_inventory, verify_correspondence,
    verify_inventory, write_inventory,
};
pub use journal::{
    JOURNAL_SCHEMA_VERSION, Journal, JournalClaim, JournalState, MAX_JOURNAL_MANIFEST_BYTES,
    ManifestSnapshot, Phase, PolicyCarrierRefs, PriorInstall, RECEIPT_SCHEMA_VERSION, Receipt,
    ReceiptState, RegistrationPreimage, TRANSACTION_ID_BYTES, transaction_id,
};
pub use model::{
    Channel, ChannelIdentity, DEVELOPMENT_HOST_NAME, Digest, FIXTURE_HOST_NAME, FailureCode,
    InstallFailure, InstallReport, InstallResult, InventoryEntry, MAX_API_VERSION,
    MAX_DOCUMENT_BYTES, MAX_INVENTORY_BYTES, MAX_INVENTORY_ENTRIES, MAX_PATH_BYTES,
    MAX_POLICY_DIGESTS, MAX_PUBLISHER_BYTES, MAX_PUBLISHERS, MAX_REPORT_WARNINGS, MIN_API_VERSION,
    Operation, PLACEHOLDER_EXTENSION_ID, PRODUCTION_HOST_NAME, ReleaseDescriptor, ReleasePolicy,
    SCHEMA_VERSION, Target, Version, WarningCode, canonical_json, extension_origin, parse_version,
    select_host, validate_extension_id,
};
pub use operations::{
    STATE_ABSENT, STATE_DAMAGED, STATE_FOREIGN, STATE_REPAIRED, STATE_UNINSTALLED, run_operation,
    run_operation_within,
};
pub use paths::{
    CLEANUP_HELPER_FILE, CLEANUP_HELPER_FILE_MACOS, InstallPaths, JOURNAL_FILE, LOCK_FILE,
    MACOS_CHROME_HOSTS_DIR, MAINTENANCE_DIR, MAINTENANCE_ENGINE_FILE,
    MAINTENANCE_ENGINE_FILE_MACOS, MAINTENANCE_UNINSTALLER_FILE, MAX_ROOT_BYTES, RECEIPT_FILE,
    ROOT_DIR_DEVELOPMENT, ROOT_DIR_FIXTURE, ROOT_DIR_PRODUCTION, STAGING_DIR, STATE_DIR,
    VERSIONS_DIR, cleanup_helper_file, maintenance_engine_file, maintenance_engine_relative,
    maintenance_relative_is_executable, maintenance_uninstaller_relative, root_dir_name,
};
#[cfg(target_os = "macos")]
#[doc(hidden)]
pub use platform::MacosCleanupRoot;
#[cfg(target_os = "macos")]
pub use platform::MacosPlatform;
#[cfg(windows)]
#[doc(hidden)]
pub use platform::WindowsCleanupRoot;
#[cfg(windows)]
pub use platform::WindowsPlatform;
pub use platform::{
    AuthenticatedPackage, AuthenticatedSource, BoundaryCrossing, CarrierEntry, CleanupBehavior,
    FakePlatform, Faults, HostAssessment, HostFault, HostState, InstallLock, MAX_CARRIER_BYTES,
    MAX_CARRIER_ENTRIES, MAX_MANIFEST_PATH_BYTES, NativeManifest, OWNERSHIP_SCHEMA_VERSION,
    OwnedClaim, OwnedEntry, OwnedKind, OwnershipRecord, PACKAGE_MACOS_CARRIER,
    PACKAGE_WINDOWS_CARRIER, PackageKind, PackageSource, PathKind, Platform, PlatformCall,
    PortablePlatform, ProcessIdentity, ProcessImage, ProcessObservation, ProcessScope,
    ProcessTable, QUIESCENCE_TIMEOUT, Registration, StateDocument,
    TRUST_ASSESSMENT_MACOS_GATEKEEPER, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, TrustProof,
    ValidatedPath, WindowsPreimage, assess_host, assessment_target, authenticate_package,
    claim_owned_entries, claim_owned_registration, inspect_host_processes, is_accepted_assessment,
    package_carrier_name, parse_manifest, path_is_within, registration_is_owned, require_quiescent,
    require_quiescent_within,
};
#[cfg(windows)]
pub use platform::{PRODUCTION_REGISTRY_PREFIX, RegistryScope, RegistryView, TEST_REGISTRY_PREFIX};
pub use policy::{
    CARRIER_FLAG_SEALED_INVENTORY, CARRIER_HEADER_LEN, CARRIER_MAGIC, CARRIER_VERSION,
    CarrierContents, MAX_CARRIER_DOCUMENT_BYTES, SEALED_INVENTORY_DIGEST_LEN,
    authorize_package_policy, effective_policy, parse_descriptor, read_carrier,
    read_carrier_contents, write_carrier, write_sealed_carrier,
};
pub use removal::{RemovalCoordinator, RemovalRequest, cleanup_helper_source};
pub use retained::{
    MAX_RETAINED_BYTES, RETAINED_FILE_NAME, RETAINED_SCHEMA_VERSION, RetainedInstall,
};
pub use transaction::{
    STATE_CLEAN, STATE_INSTALLED, STATE_RECOVERED_ACTIVE, STATE_RECOVERED_PREVIOUS,
    STATE_ROLLED_BACK, install, recover,
};
