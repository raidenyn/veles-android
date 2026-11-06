//! Transaction journal (v1) and committed receipt.
//!
//! The journal is the durable record of one installer transaction. It exists so
//! a process that dies mid-transaction can be reconciled afterwards: it records
//! the exact intent (operation, phase, old/new payload identities, old/new
//! manifest bytes, the owned registration preimage, the policy-carrier
//! references and the inventory of paths the transaction created) before the
//! first mutation, and the completion of each separately journaled publication
//! step afterwards.
//!
//! A journal is never authority on its own. Recovery compares it with verified
//! live state (see [`crate::transaction::recover`]); a phase marker alone never
//! proves that a registration, a receipt or a payload is complete.
//!
//! # Document rules
//!
//! * Unknown fields, unknown phases and an unknown `schema` are rejected.
//! * Manifests travel as exact bytes, lowercase hex encoded, and their recorded
//!   SHA-256 must match those bytes. They are bounded to
//!   [`MAX_JOURNAL_MANIFEST_BYTES`] so the whole document stays inside
//!   [`MAX_DOCUMENT_BYTES`]; a Native Messaging manifest is a few hundred bytes.
//! * The created-path inventory must be a well-formed owned claim set for this
//!   root and channel (the same rules a committed receipt is held to). A file
//!   claim that describes a staging write records its exact size and, while the
//!   journal is `Prepared`, an `incomplete` marker so rollback can remove a short
//!   prefix left by a process that died mid-write. A claim that only describes an
//!   owned file being removed (an uninstall journal) records its content digest
//!   and no size: there is no staged write to recover a prefix from.
//! * The release policy the transaction enforces is recorded with the
//!   authenticated carrier references that prove it, and its epoch must equal the
//!   references' epoch. Repair and rollback compose against the persisted policy,
//!   so a stale signature cannot erase a recorded denial.
//! * An empty journal file means "no transaction in flight". State documents are
//!   replaced atomically through the [`Platform`] boundary (stage, flush, rename,
//!   flush the parent), so a reader sees the previous complete document or the
//!   new complete one - never a torn JSON envelope. A finished transaction
//!   releases its journal by atomically replacing it with zero bytes; an
//!   uninterpretable document is quarantined by recovery instead of blocking
//!   forever.

use serde::de;
use serde::{Deserialize, Deserializer, Serialize, Serializer};

use crate::model::{
    Channel, Digest, FailureCode, InstallFailure, InstallResult, MAX_DOCUMENT_BYTES,
    MAX_INVENTORY_BYTES, MAX_INVENTORY_ENTRIES, MAX_PUBLISHER_BYTES, Operation, ReleasePolicy,
    Target, canonical_json, parse_version,
};
use crate::paths::{InstallPaths, MAX_ROOT_BYTES};
use crate::platform::{
    OWNERSHIP_SCHEMA_VERSION, OwnedClaim, OwnedKind, OwnershipRecord, Platform, StateDocument,
};

/// The only supported journal document schema generation.
pub const JOURNAL_SCHEMA_VERSION: u32 = 1;
/// The only supported committed-receipt schema generation.
pub const RECEIPT_SCHEMA_VERSION: u32 = 1;
/// A manifest recorded in the journal is bounded to 32 KiB.
///
/// Two recorded manifests plus a bounded created-path inventory must stay inside
/// [`MAX_DOCUMENT_BYTES`]. A Native Messaging manifest is a few hundred bytes, so
/// this is generous headroom rather than a functional limit.
pub const MAX_JOURNAL_MANIFEST_BYTES: usize = 32 * 1024;
/// Transaction identifiers are 16 bytes rendered as 32 lowercase hex characters.
pub const TRANSACTION_ID_BYTES: usize = 16;

/// The committed phase of a transaction.
///
/// The order is the transition order. `Prepared` and `Staged` are durable intent
/// written *before* the mutation they authorize; `ManifestActivated` and
/// `RegistrationPublished` record that the matching publication step returned,
/// so the journal separates the manifest replacement from the registry
/// publication. Recovery never trusts a phase and always inspects verified live
/// state instead.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Phase {
    /// Intent recorded; nothing but the journal may be durable yet.
    Prepared,
    /// Every authenticated payload file and the inventory are staged.
    Staged,
    /// The manifest file that selects the executable may be old or new.
    ManifestActivated,
    /// The OS registration publication returned.
    RegistrationPublished,
    /// The committed receipt is durable.
    ReceiptCommitted,
    /// The receipt is durable and transactional cleanup is pending.
    CleanupPending,
}

impl Phase {
    /// Stable wire/display name; identical to the serde representation.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Prepared => "prepared",
            Self::Staged => "staged",
            Self::ManifestActivated => "manifest-activated",
            Self::RegistrationPublished => "registration-published",
            Self::ReceiptCommitted => "receipt-committed",
            Self::CleanupPending => "cleanup-pending",
        }
    }
}

impl std::fmt::Display for Phase {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Exact manifest bytes plus the digest that binds them.
///
/// Serialized as `{"bytes_hex": "...", "sha256": "..."}`; deserialization
/// rejects a digest that does not match the bytes or a size outside
/// [`MAX_JOURNAL_MANIFEST_BYTES`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ManifestSnapshot {
    bytes: Vec<u8>,
    sha256: Digest,
}

impl ManifestSnapshot {
    /// Wrap exact manifest bytes and bind them to their digest.
    pub fn new(bytes: Vec<u8>) -> InstallResult<Self> {
        if bytes.is_empty() || bytes.len() > MAX_JOURNAL_MANIFEST_BYTES {
            return Err(invalid_package());
        }
        let sha256 = Digest::of(&bytes);
        Ok(Self { bytes, sha256 })
    }

    /// Exact manifest bytes.
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    /// SHA-256 of the exact manifest bytes.
    pub fn sha256(&self) -> Digest {
        self.sha256
    }
}

impl Serialize for ManifestSnapshot {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeStruct;

        let mut state = serializer.serialize_struct("ManifestSnapshot", 2)?;
        state.serialize_field("bytes_hex", &encode_hex(&self.bytes))?;
        state.serialize_field("sha256", &self.sha256)?;
        state.end()
    }
}

impl<'de> Deserialize<'de> for ManifestSnapshot {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Wire {
            bytes_hex: String,
            sha256: Digest,
        }

        let wire = Wire::deserialize(deserializer)?;
        let bytes = decode_hex(&wire.bytes_hex)
            .ok_or_else(|| de::Error::custom("expected lowercase hexadecimal manifest bytes"))?;
        let snapshot = ManifestSnapshot::new(bytes).map_err(de::Error::custom)?;
        if snapshot.sha256 != wire.sha256 {
            return Err(de::Error::custom(
                "manifest digest does not match its bytes",
            ));
        }
        Ok(snapshot)
    }
}

/// References to the authenticated policy carrier that authorized a transaction.
///
/// The carrier bytes themselves are not journaled; the digests bind the journal
/// to the descriptor and carrier the package authenticated, and the signer and
/// assessment record the OS verdict that was accepted.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PolicyCarrierRefs {
    /// SHA-256 of the canonical authenticated descriptor JSON.
    pub descriptor_sha256: Digest,
    /// SHA-256 of the exact authenticated carrier bytes.
    pub carrier_sha256: Digest,
    /// The OS-verified signer identity (an exact publisher-policy string).
    pub signer: String,
    /// The OS assessment verdict.
    pub assessment: String,
    /// The authenticated policy generation.
    pub epoch: u64,
}

impl PolicyCarrierRefs {
    /// Build and validate one reference set.
    pub fn new(
        descriptor_sha256: Digest,
        carrier_sha256: Digest,
        signer: impl Into<String>,
        assessment: impl Into<String>,
        epoch: u64,
    ) -> InstallResult<Self> {
        let value = Self {
            descriptor_sha256,
            carrier_sha256,
            signer: signer.into(),
            assessment: assessment.into(),
            epoch,
        };
        value.validate()?;
        Ok(value)
    }

    /// Check the bounded identity strings.
    pub fn validate(&self) -> InstallResult<()> {
        for value in [&self.signer, &self.assessment] {
            if value.is_empty()
                || value.len() > MAX_PUBLISHER_BYTES
                || value.chars().any(char::is_control)
            {
                return Err(invalid_input());
            }
        }
        Ok(())
    }
}

/// The exact registration state a transaction replaced.
///
/// Carried so an interrupted activation can be undone without trusting the
/// live filesystem to still hold the previous manifest. On Windows it also
/// records the HKCU key and the default value the previous publication managed,
/// so the restore is the same owned-preimage rule the manifest uses.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RegistrationPreimage {
    /// SHA-256 of the exact previous manifest bytes (equal to the journaled
    /// `old_manifest.sha256`).
    pub manifest_sha256: Digest,
    /// The exact HKCU key below `HKCU` the previous publication managed, if any.
    pub windows_key: Option<String>,
    /// The previous default value, or `None` when the key/value was absent.
    pub windows_value: Option<String>,
}

impl RegistrationPreimage {
    /// An owned preimage with no Windows registry record.
    pub fn new(manifest_sha256: Digest) -> Self {
        Self {
            manifest_sha256,
            windows_key: None,
            windows_value: None,
        }
    }

    /// Attach the exact HKCU key and previous default value.
    pub fn with_windows(
        mut self,
        key: impl Into<String>,
        previous_value: Option<String>,
    ) -> InstallResult<Self> {
        let key = key.into();
        if key.is_empty()
            || key.len() > MAX_ROOT_BYTES
            || key.chars().any(char::is_control)
            || previous_value
                .as_deref()
                .is_some_and(|value| value.contains('\0'))
        {
            return Err(invalid_input());
        }
        self.windows_key = Some(key);
        self.windows_value = previous_value;
        Ok(self)
    }

    /// Check the bounded registry record.
    pub fn validate(&self) -> InstallResult<()> {
        if let Some(key) = &self.windows_key {
            if key.is_empty() || key.len() > MAX_ROOT_BYTES || key.chars().any(char::is_control) {
                return Err(invalid_input());
            }
        } else if self.windows_value.is_some() {
            return Err(invalid_input());
        }
        if self
            .windows_value
            .as_deref()
            .is_some_and(|value| value.contains('\0'))
        {
            return Err(invalid_input());
        }
        Ok(())
    }
}

/// One path a transaction created, with the staging metadata recovery needs.
///
/// The claim set is journaled before the first staging write. While the journal
/// is still `Prepared` every file claim is `incomplete`: staging writes the bytes
/// sequentially, so a process death can leave a strict prefix of the recorded
/// file, and rollback must be able to remove that prefix without a digest match
/// (a complete file is still held to its exact digest). The `Staged` journal
/// clears the marker once every file has been written and read back.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JournalClaim {
    /// Normalized relative path below the install root.
    pub path: String,
    pub kind: OwnedKind,
    /// Exact content digest for files; `None` for directories.
    pub sha256: Option<Digest>,
    /// The exact byte size a complete staging write produces, when this claim
    /// describes a staging write. Absent for a file an operation only removes.
    pub size: Option<u64>,
    /// Whether a process may have died part-way through writing this file.
    pub incomplete: bool,
}

impl JournalClaim {
    /// A claim for a file this transaction is about to write.
    pub fn file(path: impl Into<String>, sha256: Digest, size: u64, incomplete: bool) -> Self {
        Self {
            path: path.into(),
            kind: OwnedKind::File,
            sha256: Some(sha256),
            size: Some(size),
            incomplete,
        }
    }

    /// A claim for a directory this transaction creates.
    pub fn directory(path: impl Into<String>) -> Self {
        Self {
            path: path.into(),
            kind: OwnedKind::Directory,
            sha256: None,
            size: None,
            incomplete: false,
        }
    }

    /// A claim for a symbolic link this transaction installs.
    ///
    /// `sha256` is the digest of the exact target bytes and `size` their length.
    pub fn link(path: impl Into<String>, target_sha256: Digest, target_size: u64) -> Self {
        Self {
            path: path.into(),
            kind: OwnedKind::Symlink,
            sha256: Some(target_sha256),
            size: Some(target_size),
            incomplete: false,
        }
    }

    /// The claim as the ownership record represents it.
    pub fn as_owned(&self) -> OwnedClaim {
        OwnedClaim {
            path: self.path.clone(),
            kind: self.kind,
            sha256: self.sha256,
        }
    }

    /// The same claim with staging complete.
    pub fn completed(&self) -> Self {
        Self {
            incomplete: false,
            ..self.clone()
        }
    }

    fn validate(&self) -> InstallResult<()> {
        if self.path.is_empty() {
            return Err(invalid_input());
        }
        match self.kind {
            OwnedKind::File => {
                if self.sha256.is_none() {
                    return Err(invalid_input());
                }
                // A claim that describes a staging write records its exact size
                // (so a partial prefix can be removed). A claim that describes
                // an existing owned file being removed records only the content
                // digest, which is what the removal verifies.
                if self.size.is_some_and(|size| size > MAX_INVENTORY_BYTES) {
                    return Err(invalid_input());
                }
            }
            OwnedKind::Directory => {
                if self.sha256.is_some() || self.size.is_some() || self.incomplete {
                    return Err(invalid_input());
                }
            }
            OwnedKind::Symlink => {
                // A link is bound to its exact target bytes; it is never staged,
                // so a size or incomplete marker would describe a file write.
                if self.sha256.is_none() || self.incomplete {
                    return Err(invalid_input());
                }
                if self
                    .size
                    .is_some_and(|size| size > crate::model::MAX_PATH_BYTES as u64)
                {
                    return Err(invalid_input());
                }
            }
        }
        Ok(())
    }
}

/// The durable journal slot, as found by [`Journal::load`].
#[derive(Debug, Clone)]
pub enum JournalState {
    /// No transaction is in flight (absent or durably released).
    None,
    /// A complete, valid pending journal.
    Pending(Box<Journal>),
    /// Present bytes that cannot be interpreted (a torn write or foreign bytes).
    ///
    /// Recovery reconciles from verified live state and quarantines the bytes
    /// instead of failing forever; an install refuses to start.
    Unreadable,
}

/// The durable receipt slot, as found by [`Receipt::load`].
#[derive(Debug, Clone)]
pub enum ReceiptState {
    /// No receipt is committed.
    None,
    /// A complete committed receipt.
    Committed(Box<Receipt>),
    /// Present bytes that cannot be interpreted.
    Unreadable,
}

/// The journal v1 document.
///
/// Fields are public so a test or a later task can inspect the durable record;
/// every writer must still pass [`Journal::validate`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Journal {
    pub schema: u32,
    /// Deterministic 32-character transaction identifier.
    pub transaction_id: String,
    pub operation: Operation,
    pub phase: Phase,
    pub target: Target,
    pub channel: Channel,
    /// Exact absolute install root this transaction targets.
    pub root: String,
    /// New package version.
    pub version: String,
    /// New payload identity.
    pub payload_id: Digest,
    /// Previous package version, when a committed receipt names one.
    pub old_version: Option<String>,
    /// Previous payload identity, when a committed receipt names one.
    pub old_payload_id: Option<Digest>,
    /// SHA-256 of the previous release's authenticated policy carrier.
    ///
    /// Journaled with the previous identity so recovery and receipt rebuilding
    /// can bind the retained carrier to evidence even if the replaced receipt is
    /// gone or unreadable.
    pub old_carrier_sha256: Option<Digest>,
    /// Exact new manifest bytes this transaction activates.
    pub manifest: ManifestSnapshot,
    /// Exact previous manifest bytes, when one was live.
    pub old_manifest: Option<ManifestSnapshot>,
    /// The owned registration preimage, when one was live.
    pub preimage: Option<RegistrationPreimage>,
    pub policy: PolicyCarrierRefs,
    /// The release policy this transaction actually enforces.
    ///
    /// Persisted with its authenticated proof so repair and rollback can compose
    /// against it after the fact: a later, correctly signed but stale package
    /// can never erase a denial recorded here. `epoch` equals
    /// `policy.epoch`.
    pub release_policy: ReleasePolicy,
    /// Paths this transaction created, relative to the root.
    pub created: Vec<JournalClaim>,
}

impl Journal {
    /// The same transaction at another phase.
    pub fn with_phase(&self, phase: Phase) -> Self {
        let mut next = self.clone();
        next.phase = phase;
        next
    }

    /// The same transaction with every staging write complete.
    pub fn with_completed_claims(&self) -> Self {
        let mut next = self.clone();
        next.created = next.created.iter().map(JournalClaim::completed).collect();
        next
    }

    /// The created-path inventory as an ownership record.
    ///
    /// This is what lets cancellation and recovery remove *only* paths this
    /// transaction created, with the same content checks a committed receipt is
    /// held to.
    pub fn ownership_record(&self) -> OwnershipRecord {
        OwnershipRecord {
            schema: OWNERSHIP_SCHEMA_VERSION,
            target: self.target,
            channel: self.channel,
            root: self.root.clone(),
            manifest_sha256: self.manifest.sha256(),
            owned: self.created.iter().map(JournalClaim::as_owned).collect(),
        }
    }

    /// Validate every field, every bound and the created-path inventory.
    pub fn validate(&self, paths: &InstallPaths) -> InstallResult<()> {
        if self.schema != JOURNAL_SCHEMA_VERSION || !is_transaction_id(&self.transaction_id) {
            return Err(invalid_input());
        }
        parse_version(&self.version)?;
        if let Some(version) = &self.old_version {
            parse_version(version)?;
        }
        if self.old_version.is_some() != self.old_payload_id.is_some() {
            return Err(invalid_input());
        }
        if self.old_payload_id.is_some() != self.old_carrier_sha256.is_some() {
            return Err(invalid_input());
        }
        if self.created.len() > MAX_INVENTORY_ENTRIES {
            return Err(invalid_input());
        }
        if self.old_manifest.is_some() != self.preimage.is_some() {
            return Err(invalid_input());
        }
        if let Some(preimage) = &self.preimage {
            let old = self.old_manifest.as_ref().ok_or_else(invalid_input)?;
            if preimage.manifest_sha256 != old.sha256() {
                return Err(invalid_input());
            }
            preimage.validate()?;
        }
        self.policy.validate()?;
        self.release_policy.validate()?;
        if self.release_policy.epoch != self.policy.epoch {
            return Err(invalid_input());
        }
        for claim in &self.created {
            claim.validate()?;
        }
        // The created inventory is validated exactly like a receipt's owned set:
        // in-root, in-scope (`versions/`/`maintenance/`), bounded, duplicate-free
        // and kind/digest consistent.
        self.ownership_record().validate(paths)?;
        // The claim set contributes to the document bound: a journal whose
        // inventory would not fit is invalid, not merely unwritable.
        if self.to_bytes()?.len() > MAX_DOCUMENT_BYTES {
            return Err(invalid_input());
        }
        Ok(())
    }

    /// Strictly parse a journal document and validate it against `paths`.
    pub fn parse(bytes: &[u8], paths: &InstallPaths) -> InstallResult<Self> {
        if bytes.is_empty() || bytes.len() > MAX_DOCUMENT_BYTES {
            return Err(invalid_input());
        }
        let journal: Self = serde_json::from_slice(bytes).map_err(|_| invalid_input())?;
        journal.validate(paths)?;
        Ok(journal)
    }

    /// The canonical bounded document bytes.
    pub fn to_bytes(&self) -> InstallResult<Vec<u8>> {
        let bytes = canonical_json(self)?;
        if bytes.len() > MAX_DOCUMENT_BYTES {
            return Err(invalid_package());
        }
        Ok(bytes)
    }

    /// Durably record this journal (validated first) as one atomic replacement.
    pub fn write(&self, platform: &impl Platform, paths: &InstallPaths) -> InstallResult<()> {
        self.validate(paths)?;
        let bytes = self.to_bytes()?;
        platform.write_state(paths, StateDocument::Journal, &bytes)
    }

    /// Read the pending journal slot.
    ///
    /// An absent or empty journal means no transaction is in flight. A present
    /// document that cannot be parsed is reported as [`JournalState::Unreadable`]
    /// so recovery can reconcile it against verified live state instead of
    /// blocking forever; an install refuses to proceed on either.
    pub fn load(platform: &impl Platform, paths: &InstallPaths) -> InstallResult<JournalState> {
        let Some(bytes) = platform.read_state(paths, StateDocument::Journal)? else {
            return Ok(JournalState::None);
        };
        if bytes.is_empty() {
            return Ok(JournalState::None);
        }
        Ok(match Self::parse(&bytes, paths) {
            Ok(journal) => JournalState::Pending(Box::new(journal)),
            Err(_) => JournalState::Unreadable,
        })
    }

    /// Durably release the transaction by atomically replacing it with nothing.
    pub fn clear(platform: &impl Platform, paths: &InstallPaths) -> InstallResult<()> {
        platform.write_state(paths, StateDocument::Journal, &[])
    }
}

/// The previous installation named by a committed receipt.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PriorInstall {
    pub version: String,
    pub payload_id: Digest,
    /// Exact manifest digest of the prior registration, when it was live.
    pub manifest_sha256: Option<Digest>,
    /// SHA-256 of the authenticated policy carrier bytes of the prior release.
    ///
    /// A rollback re-derives the carrier through the OS trust path and requires
    /// those bytes to match this receipt-claimed digest; the retained metadata
    /// can never substitute a carrier (or signer) of its own.
    pub carrier_sha256: Digest,
}

/// The committed receipt for this installation.
///
/// A receipt records owned paths, hashes and active/prior identities only: it is
/// installer state, never trust, and never authority to execute anything.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Receipt {
    pub schema: u32,
    pub transaction_id: String,
    pub operation: Operation,
    pub target: Target,
    pub channel: Channel,
    pub root: String,
    pub version: String,
    pub payload_id: Digest,
    pub manifest_sha256: Digest,
    pub prior: Option<PriorInstall>,
    pub policy: PolicyCarrierRefs,
    /// The release policy the committed installation enforces.
    ///
    /// This is the composed policy the transaction applied (the incoming policy
    /// for an install, the conservative intersection/union for a repair or
    /// rollback). Repair and rollback compose against it, so a later stale
    /// signature cannot erase a recorded denial. `epoch` equals `policy.epoch`.
    pub release_policy: ReleasePolicy,
    pub ownership: OwnershipRecord,
}

impl Receipt {
    /// Validate this receipt against the fixed layout it claims.
    pub fn validate(&self, paths: &InstallPaths) -> InstallResult<()> {
        if self.schema != RECEIPT_SCHEMA_VERSION || !is_transaction_id(&self.transaction_id) {
            return Err(invalid_input());
        }
        parse_version(&self.version)?;
        if let Some(prior) = &self.prior {
            parse_version(&prior.version)?;
        }
        self.policy.validate()?;
        self.release_policy.validate()?;
        if self.release_policy.epoch != self.policy.epoch {
            return Err(invalid_input());
        }
        self.ownership.validate(paths)?;
        if self.ownership.manifest_sha256 != self.manifest_sha256 {
            return Err(invalid_input());
        }
        Ok(())
    }

    /// Strictly parse a receipt document and validate it against `paths`.
    pub fn parse(bytes: &[u8], paths: &InstallPaths) -> InstallResult<Self> {
        if bytes.is_empty() || bytes.len() > MAX_DOCUMENT_BYTES {
            return Err(invalid_input());
        }
        let receipt: Self = serde_json::from_slice(bytes).map_err(|_| invalid_input())?;
        receipt.validate(paths)?;
        Ok(receipt)
    }

    /// The canonical bounded document bytes.
    pub fn to_bytes(&self) -> InstallResult<Vec<u8>> {
        let bytes = canonical_json(self)?;
        if bytes.len() > MAX_DOCUMENT_BYTES {
            return Err(invalid_package());
        }
        Ok(bytes)
    }

    /// Durably write the committed receipt (validated first) atomically.
    pub fn write(&self, platform: &impl Platform, paths: &InstallPaths) -> InstallResult<()> {
        self.validate(paths)?;
        let bytes = self.to_bytes()?;
        platform.write_state(paths, StateDocument::Receipt, &bytes)
    }

    /// Read the committed receipt slot.
    ///
    /// A present but uninterpretable receipt is reported as
    /// [`ReceiptState::Unreadable`], never as a licence to guess what was
    /// installed. Atomic replacement means this only arises from external
    /// corruption; recovery can quarantine it when a pending journal is
    /// authoritative for the same transaction.
    pub fn load(platform: &impl Platform, paths: &InstallPaths) -> InstallResult<ReceiptState> {
        let Some(bytes) = platform.read_state(paths, StateDocument::Receipt)? else {
            return Ok(ReceiptState::None);
        };
        if bytes.is_empty() {
            return Ok(ReceiptState::None);
        }
        Ok(match Self::parse(&bytes, paths) {
            Ok(receipt) => ReceiptState::Committed(Box::new(receipt)),
            Err(_) => ReceiptState::Unreadable,
        })
    }
}

/// A deterministic transaction identifier for one transaction intent.
///
/// Deterministic on purpose: a retried identical transaction (same operation,
/// release, manifest and preimage) is recognisable, and no clock or randomness
/// enters portable evidence.
pub fn transaction_id(
    operation: Operation,
    target: Target,
    channel: Channel,
    version: &str,
    payload_id: Digest,
    manifest_sha256: Digest,
    old_manifest_sha256: Option<Digest>,
) -> String {
    let mut material = format!(
        "{}|{:?}|{:?}|{version}|{payload_id}|{manifest_sha256}|",
        operation_name(operation),
        target,
        channel
    );
    match old_manifest_sha256 {
        Some(digest) => material.push_str(&digest.to_hex()),
        None => material.push_str("none"),
    }
    let digest = Digest::of(material.as_bytes());
    digest.to_hex()[..TRANSACTION_ID_BYTES * 2].to_owned()
}

const fn operation_name(operation: Operation) -> &'static str {
    match operation {
        Operation::Inspect => "inspect",
        Operation::Install => "install",
        Operation::Repair => "repair",
        Operation::Rollback => "rollback",
        Operation::Uninstall => "uninstall",
    }
}

fn is_transaction_id(value: &str) -> bool {
    value.len() == TRANSACTION_ID_BYTES * 2
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
}

fn encode_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}

fn decode_hex(text: &str) -> Option<Vec<u8>> {
    if !text.len().is_multiple_of(2) {
        return None;
    }
    let bytes = text.as_bytes();
    let (pairs, remainder) = bytes.as_chunks::<2>();
    if !remainder.is_empty() {
        return None;
    }
    let mut out = Vec::with_capacity(pairs.len());
    for pair in pairs {
        let high = hex_value(pair[0])?;
        let low = hex_value(pair[1])?;
        out.push((high << 4) | low);
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

fn invalid_input() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidInput)
}

fn invalid_package() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidPackage)
}
