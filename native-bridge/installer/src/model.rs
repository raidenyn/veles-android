//! Shared strict model for the standalone installer.
//!
//! These types are the single definition that later installer tasks extend; no
//! frontend or platform module may declare a differently named copy.
//!
//! Parsing is strict: serde rejects unknown and duplicate fields, digests are
//! exactly 64 lowercase hex characters, versions are three unsigned numeric
//! components and every channel binds its own host/bundle identity. Documented
//! limits are enforced before untrusted data is interpreted.

use std::cmp::Ordering;
use std::collections::HashSet;
use std::fmt;
use std::str::FromStr;

use serde::de::{self, Visitor};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use sha2::{Digest as _, Sha256};

/// The only supported descriptor/journal/receipt schema generation.
pub const SCHEMA_VERSION: u32 = 1;
/// Descriptor, journal and receipt documents are each bounded to 256 KiB.
pub const MAX_DOCUMENT_BYTES: usize = 256 * 1024;
/// The complete installed payload inventory is bounded to 4096 entries.
pub const MAX_INVENTORY_ENTRIES: usize = 4096;
/// The complete installed payload inventory is bounded to 256 MiB.
pub const MAX_INVENTORY_BYTES: u64 = 256 * 1024 * 1024;
/// A normalized relative inventory path is bounded to 1024 UTF-8 bytes.
pub const MAX_PATH_BYTES: usize = 1024;
/// A policy may name at most 64 trusted publishers.
pub const MAX_PUBLISHERS: usize = 64;
/// A publisher identity string is bounded to 512 UTF-8 bytes.
pub const MAX_PUBLISHER_BYTES: usize = 512;
/// A policy may deny or permit at most 4096 payload digests in one list.
pub const MAX_POLICY_DIGESTS: usize = 4096;
/// An operation report carries at most this many warning codes.
pub const MAX_REPORT_WARNINGS: usize = 8;
/// Lowest supported host API generation.
pub const MIN_API_VERSION: u32 = 1;
/// Highest host API generation a descriptor may name (fail closed above it).
pub const MAX_API_VERSION: u32 = 1000;

/// Production Native Messaging host name and macOS bundle ID.
pub const PRODUCTION_HOST_NAME: &str = "me.nagaev.veles.bridge";
/// Development identity: same root shape, explicitly distinct from production.
pub const DEVELOPMENT_HOST_NAME: &str = "me.nagaev.veles.bridge.dev";
/// Test-only fixture identity. Kept distinct from production *and* development so
/// a fixture can never register over, or be mistaken for, either real install.
pub const FIXTURE_HOST_NAME: &str = "me.nagaev.veles.bridge.fixture";
/// The repository's existing development placeholder Chrome extension ID.
/// It is never a real Web Store item ID and is rejected in every channel.
pub const PLACEHOLDER_EXTENSION_ID: &str = "abcdefghijklmnopabcdefghijklmnop";

/// The shared installer result type.
pub type InstallResult<T> = Result<T, InstallFailure>;

/// Typed failure returned by the installer engine.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstallFailure {
    /// Stable failure classification for frontends and reports.
    pub code: FailureCode,
    /// Whether the previously installed payload is still usable after this failure.
    pub prior_usable: bool,
}

impl InstallFailure {
    /// A failure whose prior installation state is unknown/not usable.
    pub const fn new(code: FailureCode) -> Self {
        Self {
            code,
            prior_usable: false,
        }
    }

    /// Record that the prior installation remains untouched and usable.
    pub const fn with_prior_usable(self, prior_usable: bool) -> Self {
        Self {
            code: self.code,
            prior_usable,
        }
    }
}

impl From<FailureCode> for InstallFailure {
    fn from(code: FailureCode) -> Self {
        Self::new(code)
    }
}

impl fmt::Display for InstallFailure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "installer failure {} (prior usable: {})",
            self.code.as_str(),
            self.prior_usable
        )
    }
}

impl std::error::Error for InstallFailure {}

/// Stable, bounded failure vocabulary shared with both native frontends.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum FailureCode {
    InvalidInput,
    OriginRejected,
    InvalidPackage,
    TrustDenied,
    TrustUnavailable,
    PolicyDenied,
    UnsupportedTarget,
    Busy,
    HostActive,
    InspectionDenied,
    ForeignState,
    UnsafePath,
    Io,
    RecoveryRequired,
    /// Installer state written by a newer engine (an unknown schema or phase):
    /// refused and left untouched, never quarantined or reinterpreted.
    UnsupportedState,
    NoRollback,
    RepairPackageRequired,
    CleanupPending,
}

impl FailureCode {
    /// Stable wire/display name; identical to the serde representation.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::InvalidInput => "invalid-input",
            Self::OriginRejected => "origin-rejected",
            Self::InvalidPackage => "invalid-package",
            Self::TrustDenied => "trust-denied",
            Self::TrustUnavailable => "trust-unavailable",
            Self::PolicyDenied => "policy-denied",
            Self::UnsupportedTarget => "unsupported-target",
            Self::Busy => "busy",
            Self::HostActive => "host-active",
            Self::InspectionDenied => "inspection-denied",
            Self::ForeignState => "foreign-state",
            Self::UnsafePath => "unsafe-path",
            Self::Io => "io",
            Self::RecoveryRequired => "recovery-required",
            Self::UnsupportedState => "unsupported-state",
            Self::NoRollback => "no-rollback",
            Self::RepairPackageRequired => "repair-package-required",
            Self::CleanupPending => "cleanup-pending",
        }
    }
}

impl fmt::Display for FailureCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Release channel. The channel selects the host name, macOS bundle ID and
/// installer root; a descriptor carrying another channel's identity is rejected.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Channel {
    Production,
    Development,
    Fixture,
}

impl Channel {
    /// Host/bundle identity bound to this channel.
    pub const fn identity(self) -> ChannelIdentity {
        let name = match self {
            Self::Production => PRODUCTION_HOST_NAME,
            Self::Development => DEVELOPMENT_HOST_NAME,
            Self::Fixture => FIXTURE_HOST_NAME,
        };
        ChannelIdentity {
            host_name: name,
            bundle_id: name,
        }
    }

    /// Whether this is the only channel allowed to register the production host.
    pub const fn is_production(self) -> bool {
        matches!(self, Self::Production)
    }
}

/// The exact host/bundle identity a channel must present.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ChannelIdentity {
    pub host_name: &'static str,
    pub bundle_id: &'static str,
}

/// Supported release targets, named as in `config/tools.lock.json`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Target {
    #[serde(rename = "windows-x86_64")]
    WindowsX64,
    #[serde(rename = "macos-arm64")]
    MacosArm64,
}

/// User-visible installer operations.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Operation {
    Inspect,
    Install,
    Repair,
    Rollback,
    Uninstall,
}

/// A SHA-256 digest, serialized as exactly 64 lowercase hex characters.
#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Digest([u8; 32]);

impl Digest {
    /// The all-zero digest, used as an explicit placeholder in tests only.
    pub const ZERO: Self = Self([0; 32]);

    pub const fn from_bytes(bytes: [u8; 32]) -> Self {
        Self(bytes)
    }

    /// SHA-256 of `bytes`.
    pub fn of(bytes: &[u8]) -> Self {
        let mut hasher = Sha256::new();
        hasher.update(bytes);
        Self(hasher.finalize().into())
    }

    /// Parse exactly 64 lowercase hex characters.
    pub fn from_hex(hex: &str) -> InstallResult<Self> {
        decode_hex(hex)
            .map(Self)
            .ok_or_else(|| InstallFailure::new(FailureCode::InvalidInput))
    }

    pub const fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }

    /// Lowercase hexadecimal rendering.
    pub fn to_hex(self) -> String {
        let mut out = String::with_capacity(64);
        for byte in self.0 {
            out.push(HEX[(byte >> 4) as usize] as char);
            out.push(HEX[(byte & 0x0f) as usize] as char);
        }
        out
    }
}

const HEX: &[u8; 16] = b"0123456789abcdef";

fn decode_hex(hex: &str) -> Option<[u8; 32]> {
    if hex.len() != 64 {
        return None;
    }
    let mut out = [0u8; 32];
    let bytes = hex.as_bytes();
    for (index, slot) in out.iter_mut().enumerate() {
        let high = hex_value(bytes[index * 2])?;
        let low = hex_value(bytes[index * 2 + 1])?;
        *slot = (high << 4) | low;
    }
    Some(out)
}

const fn hex_value(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        _ => None,
    }
}

impl FromStr for Digest {
    type Err = InstallFailure;

    fn from_str(value: &str) -> InstallResult<Self> {
        Self::from_hex(value)
    }
}

impl fmt::Debug for Digest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Digest({})", self.to_hex())
    }
}

impl fmt::Display for Digest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.to_hex())
    }
}

impl Serialize for Digest {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.to_hex())
    }
}

impl<'de> Deserialize<'de> for Digest {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct HexVisitor;

        impl Visitor<'_> for HexVisitor {
            type Value = Digest;

            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("64 lowercase hexadecimal characters")
            }

            fn visit_str<E: de::Error>(self, value: &str) -> Result<Digest, E> {
                decode_hex(value)
                    .map(Digest)
                    .ok_or_else(|| E::custom("expected 64 lowercase hexadecimal characters"))
            }
        }

        deserializer.deserialize_str(HexVisitor)
    }
}

/// A package version: exactly three unsigned numeric components compared
/// numerically, never lexicographically.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Version {
    pub major: u64,
    pub minor: u64,
    pub patch: u64,
}

impl Version {
    /// Parse a strict `major.minor.patch` version.
    pub fn parse(value: &str) -> InstallResult<Self> {
        version_parts(value)
            .map(|(major, minor, patch)| Self {
                major,
                minor,
                patch,
            })
            .ok_or_else(|| InstallFailure::new(FailureCode::InvalidInput))
    }
}

impl fmt::Display for Version {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}.{}.{}", self.major, self.minor, self.patch)
    }
}

/// Parse a strict three-component package version.
pub fn parse_version(value: &str) -> InstallResult<Version> {
    Version::parse(value)
}

fn version_parts(value: &str) -> Option<(u64, u64, u64)> {
    let mut parts = value.split('.');
    let major = numeric_component(parts.next()?)?;
    let minor = numeric_component(parts.next()?)?;
    let patch = numeric_component(parts.next()?)?;
    if parts.next().is_some() {
        return None;
    }
    Some((major, minor, patch))
}

/// One canonical unsigned decimal component: digits only, no empty part, no
/// leading zero unless the value itself is zero.
fn numeric_component(part: &str) -> Option<u64> {
    if part.is_empty() || !part.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    if part.len() > 1 && part.starts_with('0') {
        return None;
    }
    part.parse().ok()
}

/// Validate a bare Chrome extension ID for any channel: exactly 32 characters
/// from `a` to `p`, and never the repository placeholder.
pub fn validate_extension_id(extension_id: &str) -> InstallResult<()> {
    let shaped =
        extension_id.len() == 32 && extension_id.bytes().all(|byte| matches!(byte, b'a'..=b'p'));
    if !shaped || extension_id == PLACEHOLDER_EXTENSION_ID {
        return Err(InstallFailure::new(FailureCode::OriginRejected));
    }
    Ok(())
}

/// The single literal origin derived from a validated extension ID.
pub fn extension_origin(extension_id: &str) -> InstallResult<String> {
    validate_extension_id(extension_id)?;
    Ok(format!("chrome-extension://{extension_id}/"))
}

/// The release policy carried by the authenticated descriptor and persisted with
/// the last committed policy proof.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReleasePolicy {
    /// Trusted publisher/signer identities. Both live and incoming policy must
    /// allow a publisher for repair/rollback eligibility.
    pub publishers: Vec<String>,
    /// Payload digests that may never be activated again. Denials accumulate.
    pub denied_payloads: Vec<Digest>,
    /// Payload digests eligible for an explicit rollback; eligibility is the
    /// intersection of the live and incoming policies.
    pub rollback_payloads: Vec<Digest>,
    /// Monotonic policy generation. Forward relaxation requires a higher epoch.
    pub epoch: u64,
}

impl ReleasePolicy {
    /// Validate bounds and canonical list contents.
    pub fn validate(&self) -> InstallResult<()> {
        if self.publishers.is_empty() || self.publishers.len() > MAX_PUBLISHERS {
            return Err(invalid_package());
        }
        let mut publishers = HashSet::with_capacity(self.publishers.len());
        for publisher in &self.publishers {
            if !is_valid_publisher(publisher) || !publishers.insert(publisher.as_str()) {
                return Err(invalid_package());
            }
        }
        validate_digest_list(&self.denied_payloads)?;
        validate_digest_list(&self.rollback_payloads)?;
        Ok(())
    }

    /// Whether this policy permanently denies `payload`.
    pub fn denies(&self, payload: &Digest) -> bool {
        self.denied_payloads.contains(payload)
    }

    /// Whether this policy permits `payload` for an explicit rollback.
    pub fn permits_rollback(&self, payload: &Digest) -> bool {
        self.rollback_payloads.contains(payload)
    }
}

fn is_valid_publisher(publisher: &str) -> bool {
    !publisher.is_empty()
        && publisher.len() <= MAX_PUBLISHER_BYTES
        && publisher.trim() == publisher
        && !publisher.chars().any(char::is_control)
}

fn validate_digest_list(digests: &[Digest]) -> InstallResult<()> {
    if digests.len() > MAX_POLICY_DIGESTS {
        return Err(invalid_package());
    }
    let mut seen = HashSet::with_capacity(digests.len());
    for digest in digests {
        if !seen.insert(*digest) {
            return Err(invalid_package());
        }
    }
    Ok(())
}

/// The signed release identity binding source, target, origin and policy.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReleaseDescriptor {
    pub schema: u32,
    pub channel: Channel,
    /// Three unsigned numeric components; compared numerically.
    pub version: String,
    /// Full lowercase Git commit object ID (SHA-1 or SHA-256).
    pub source_commit: String,
    pub target: Target,
    /// Lowest OS version the release is tested and supported on.
    pub minimum_os: String,
    /// Bare 32-character Chrome Web Store extension ID.
    pub extension_id: String,
    /// Native Messaging host name; selected by channel.
    pub host_name: String,
    /// macOS bundle ID; selected by channel.
    pub bundle_id: String,
    pub api_min: u32,
    pub api_max: u32,
    /// Digest of the unsigned canonical payload inventory.
    pub payload_id: Digest,
    pub policy: ReleasePolicy,
}

impl ReleaseDescriptor {
    /// Validate every descriptor field. `parse_descriptor` calls this after strict
    /// JSON parsing; callers that construct a descriptor programmatically must
    /// call it before treating the descriptor as package identity.
    pub fn validate(&self) -> InstallResult<()> {
        if self.schema != SCHEMA_VERSION
            || version_parts(&self.version).is_none()
            || !is_commit_id(&self.source_commit)
            || !(MIN_API_VERSION..=MAX_API_VERSION).contains(&self.api_min)
            || self.api_max < self.api_min
            || self.api_max > MAX_API_VERSION
        {
            return Err(invalid_package());
        }
        validate_minimum_os(self.target, self.channel, &self.minimum_os)?;
        validate_extension_id(&self.extension_id)?;
        let identity = self.channel.identity();
        if self.host_name != identity.host_name || self.bundle_id != identity.bundle_id {
            return Err(InstallFailure::new(FailureCode::OriginRejected));
        }
        self.policy.validate()
    }

    /// The parsed package version (validated on construction/parse).
    pub fn parsed_version(&self) -> InstallResult<Version> {
        Version::parse(&self.version)
    }
}

fn is_commit_id(value: &str) -> bool {
    matches!(value.len(), 40 | 64)
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
}

fn validate_minimum_os(target: Target, channel: Channel, value: &str) -> InstallResult<()> {
    let parts = os_parts(value).ok_or_else(invalid_package)?;
    if channel.is_production() {
        let baseline: &[u64] = match target {
            Target::WindowsX64 => &[11],
            Target::MacosArm64 => &[26, 0],
        };
        if compare_components(&parts, baseline) == Ordering::Less {
            return Err(InstallFailure::new(FailureCode::UnsupportedTarget));
        }
    }
    Ok(())
}

fn os_parts(value: &str) -> Option<Vec<u64>> {
    let parts: Vec<&str> = value.split('.').collect();
    if parts.is_empty() || parts.len() > 3 {
        return None;
    }
    parts.iter().map(|part| numeric_component(part)).collect()
}

fn compare_components(left: &[u64], right: &[u64]) -> Ordering {
    let length = left.len().max(right.len());
    for index in 0..length {
        let left = left.get(index).copied().unwrap_or(0);
        let right = right.get(index).copied().unwrap_or(0);
        match left.cmp(&right) {
            Ordering::Equal => {}
            other => return other,
        }
    }
    Ordering::Equal
}

/// One payload file in the complete unsigned input inventory.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InventoryEntry {
    /// Normalized relative path below the install root.
    pub path: String,
    pub size: u64,
    /// SHA-256 of the exact file bytes.
    pub sha256: Digest,
    pub executable: bool,
}

/// Additive, bounded warnings attached to an operation report.
///
/// Warnings never change a failure classification: a warning describes something
/// the user must know about a successful (or explicitly pending) operation. The
/// vocabulary is closed so a frontend can render guidance deterministically.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum WarningCode {
    /// The installed or re-activated host API window does not overlap the
    /// release this installation was tested against.
    CompatibilityWindow,
    /// Files this product does not own remain inside its private layout; they
    /// were preserved and reported, never deleted.
    LeftoverFiles,
    /// Removal or cleanup could not be completed; retry the operation.
    CleanupPending,
}

impl WarningCode {
    /// Stable wire/display name; identical to the serde representation.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::CompatibilityWindow => "compatibility-window",
            Self::LeftoverFiles => "leftover-files",
            Self::CleanupPending => "cleanup-pending",
        }
    }
}

impl fmt::Display for WarningCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Safe operation report emitted by the engine (one bounded JSON document).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstallReport {
    pub operation: Operation,
    pub state: String,
    pub prior_usable: bool,
    pub active_payload: Option<Digest>,
    pub failure: Option<FailureCode>,
    /// Additive, bounded guidance for a report that is not a plain success.
    #[serde(default, deserialize_with = "deserialize_warnings")]
    pub warnings: Vec<WarningCode>,
}

impl InstallReport {
    /// Attach warnings, preserving order but removing duplicates and enforcing
    /// the documented bound. A caller cannot make the report document unbounded.
    pub fn with_warnings(mut self, warnings: impl IntoIterator<Item = WarningCode>) -> Self {
        for warning in warnings {
            if !self.warnings.contains(&warning) && self.warnings.len() < MAX_REPORT_WARNINGS {
                self.warnings.push(warning);
            }
        }
        self
    }

    /// Whether the report carries `warning`.
    pub fn warns(&self, warning: WarningCode) -> bool {
        self.warnings.contains(&warning)
    }
}

fn deserialize_warnings<'de, D>(deserializer: D) -> Result<Vec<WarningCode>, D::Error>
where
    D: Deserializer<'de>,
{
    let warnings = Vec::<WarningCode>::deserialize(deserializer)?;
    if warnings.len() > MAX_REPORT_WARNINGS {
        return Err(de::Error::custom("too many installer report warnings"));
    }
    let mut seen = HashSet::with_capacity(warnings.len());
    for warning in &warnings {
        if !seen.insert(*warning) {
            return Err(de::Error::custom("duplicate installer report warning"));
        }
    }
    Ok(warnings)
}

/// Deterministic compact UTF-8 JSON: sorted object keys and a trailing newline.
pub fn canonical_json<T: Serialize>(value: &T) -> InstallResult<Vec<u8>> {
    let value =
        serde_json::to_value(value).map_err(|_| InstallFailure::new(FailureCode::InvalidInput))?;
    let mut bytes =
        serde_json::to_vec(&value).map_err(|_| InstallFailure::new(FailureCode::InvalidInput))?;
    bytes.push(b'\n');
    Ok(bytes)
}

fn invalid_package() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidPackage)
}
