//! Journaled staging, activation and crash recovery.
//!
//! [`install`] runs one transaction in an explicit phase machine:
//!
//! ```text
//! Prepared  -> durable intent, before the first staging mutation
//! Staged    -> every authenticated payload file and the inventory are durable
//! ManifestActivated -> the manifest file that selects the executable was
//!                       replaced (the durable intent is the Staged journal)
//! RegistrationPublished -> the separate registry publication returned
//! ReceiptCommitted -> the receipt is durable; the active payload is verified
//! CleanupPending -> transactional cleanup, then the journal is released
//! ```
//!
//! Nothing in the immutable version directory is reachable by Chrome before the
//! manifest replacement, so a staged-but-unactivated payload cannot be executed
//! and an interrupted transaction never exposes a partial host. Every write goes
//! through the injected [`Platform`] boundary: payload staging writes all bytes,
//! flushes and reads them back, and state documents (journal, receipt) are
//! replaced atomically, so the only durable states are "step k did not happen"
//! and "step k happened".
//!
//! # The commit point
//!
//! A durable receipt naming this transaction is the commit point. Before it,
//! failure rolls back to the journaled previous state; after it, the installer
//! only moves forward - it re-publishes the manifest/registration idempotently,
//! leaves a `CleanupPending` journal for [`recover`], and reports success or
//! cleanup-pending. It never rolls a committed install back and never reports the
//! previous installation as unusable once the new host is live.
//!
//! [`recover`] never trusts a phase marker, and never trusts the journal, the
//! receipt or the files they claim as authority. It compares the journal with the
//! verified live registration and the verified staged inventory, then either
//! completes the staged activation - only after re-authenticating the installed
//! signed carrier with the OS, re-deriving the effective policy and the manifest
//! from the authenticated descriptor, re-verifying the complete installed
//! inventory and re-assessing the installed host (`authenticate_for_completion`;
//! `TrustDenied` / `TrustUnavailable` are returned unchanged and nothing is
//! mutated) - or restores the journaled preimage, and only when the previous
//! release verifies as whole. A live registration that is not ours, or live bytes
//! that match neither recorded manifest, fails closed with guidance instead of
//! guessing. A journal of a format this engine does not know is refused
//! (`UnsupportedState`) and left untouched. A journal that is merely corrupt is
//! reconciled against verified live state and quarantined, so a torn write cannot
//! block every later run - but never while transaction assets are outstanding.
//!
//! Retained inputs: the previous version's files and the previous manifest bytes
//! are verified (and journaled) before the first mutation, and the previous
//! version is never removed by this task - an upgrade retains it for repair,
//! rollback and uninstall.

use std::collections::BTreeSet;
use std::fs;
use std::io;
use std::path::Path;
use std::time::Duration;

use crate::inventory::{
    PACKAGE_INVENTORY_FILE, SEALED_INVENTORY_FILE, entry_is_signable_image, parse_inventory,
    verify_correspondence,
};
use crate::journal::{
    JOURNAL_SCHEMA_VERSION, Journal, JournalClaim, JournalState, ManifestSnapshot, Phase,
    PolicyCarrierRefs, PriorInstall, RECEIPT_SCHEMA_VERSION, Receipt, ReceiptState,
    RegistrationPreimage, transaction_id,
};
use crate::model::{
    Digest, FailureCode, InstallFailure, InstallReport, InstallResult, InventoryEntry,
    MAX_DOCUMENT_BYTES, Operation, ReleaseDescriptor, ReleasePolicy, Version, WarningCode,
    canonical_json, extension_origin, parse_version,
};
use crate::operations::STATE_DAMAGED;
use crate::paths::{InstallPaths, MAINTENANCE_DIR, VERSIONS_DIR};
use crate::platform::{
    AuthenticatedPackage, CarrierEntry, MAX_CARRIER_BYTES, MAX_CARRIER_ENTRIES, NativeManifest,
    OWNERSHIP_SCHEMA_VERSION, OwnedClaim, OwnedEntry, OwnedKind, OwnershipRecord, PackageSource,
    PathKind, Platform, Registration, StateDocument, TrustProof, ValidatedPath, WindowsPreimage,
    assess_host, claim_owned_entries, claim_owned_registration, is_accepted_assessment,
    package_carrier_name, parse_manifest, read_no_follow, registration_is_owned, require_quiescent,
    require_quiescent_within,
};
use crate::policy::{
    CarrierContents, authorize_package_policy, effective_policy, is_no_broader_than,
    read_carrier_contents,
};
use crate::retained::{MAX_RETAINED_BYTES, RETAINED_FILE_NAME, RetainedInstall};

/// The report state of a completed install.
pub const STATE_INSTALLED: &str = "installed";
/// The report state of a layout with no pending transaction.
pub const STATE_CLEAN: &str = "clean";
/// Recovery completed the authenticated staged activation.
pub const STATE_RECOVERED_ACTIVE: &str = "recovered-active";
/// Recovery restored the last verified version.
pub const STATE_RECOVERED_PREVIOUS: &str = "recovered-previous";
/// The report state of a completed rollback to a retained prior release.
pub const STATE_ROLLED_BACK: &str = "rolled-back";

/// The manifest description this installer publishes.
///
/// The manifest bytes are deterministic: the description carries no version, so
/// a same-payload reinstall publishes byte-identical bytes.
const MANIFEST_DESCRIPTION: &str = "Veles bridge native messaging host";

/// Everything one transaction needs, computed before the first mutation.
struct Plan {
    journal: Journal,
    /// The last committed receipt, when this is an upgrade.
    prior: Option<Receipt>,
    /// Directories to create, parents first.
    directories: Vec<String>,
    /// Payload files to stage, in authenticated inventory order.
    files: Vec<String>,
    /// The authenticated inventory document, staged with the payload.
    inventory_relative: String,
    inventory: Vec<u8>,
    /// The sealed signed-byte inventory, staged beside `inventory.json` when
    /// signing changed any payload file.
    sealed_relative: String,
    sealed: Option<Vec<u8>>,
    /// Authenticated carrier/API metadata retained with this version.
    retained_relative: String,
    retained: Vec<u8>,
    /// The signed carrier artifact's installed root, relative to the install
    /// root (`versions/<version>-<payload-id>/carrier.exe|carrier.app`).
    carrier_relative: String,
    /// The immutable version directory, relative to the install root.
    version_relative: String,
    /// The one executable the manifest selects, relative to the version directory.
    host_relative: String,
}

/// Install one authenticated package.
///
/// The previous installation is preserved: the payload is staged into its own
/// immutable version directory, the manifest is replaced atomically, and the
/// receipt is committed only after the registration and the staged inventory are
/// read back and verified. A pre-commit failure restores the previous state; only
/// a first-install failure removes assets, and only the ones this transaction
/// created. A post-commit failure is finished forward instead.
pub fn install(
    platform: &impl Platform,
    paths: &InstallPaths,
    package: AuthenticatedPackage,
) -> InstallResult<InstallReport> {
    let _lock = platform.lock(paths)?;

    // Never proceed on top of an interrupted transaction: reconcile first. An
    // uninterpretable journal is equally a pending transaction.
    match Journal::load(platform, paths)? {
        JournalState::None => {}
        JournalState::Pending(_) | JournalState::Unreadable => {
            return Err(recovery_required());
        }
        JournalState::Unsupported => return Err(unsupported_state()),
    }

    let plan = Plan::prepare(platform, paths, &package)?;
    // Durable intent before the first staging mutation.
    plan.journal.write(platform, paths)?;

    match run(platform, paths, &plan, &package) {
        Ok(()) => Ok(completed_report(&plan.journal)),
        Err(failure) => settle_failed_install(platform, paths, &plan.journal, failure),
    }
}

/// The success report for a transaction whose receipt is committed.
fn completed_report(journal: &Journal) -> InstallReport {
    InstallReport {
        operation: journal.operation,
        state: match journal.operation {
            Operation::Rollback => STATE_ROLLED_BACK.to_owned(),
            _ => STATE_INSTALLED.to_owned(),
        },
        prior_usable: true,
        active_payload: Some(journal.payload_id),
        failure: None,
        warnings: Vec::new(),
    }
}

/// Reconcile a failed transaction without ever rolling a committed one back.
///
/// The durable receipt naming this transaction is the commit point. Only when the
/// receipt is absent, names another transaction or cannot be read as ours may the
/// previous state be restored; after the commit point the installer finishes
/// forward and reports success or `CleanupPending`, never a rolled-back install
/// with `prior_usable == false`.
fn settle_failed_install(
    platform: &impl Platform,
    paths: &InstallPaths,
    intent: &Journal,
    failure: InstallFailure,
) -> InstallResult<InstallReport> {
    // The durable journal is authoritative for the phase that was reached; the
    // in-memory journal is the durable intent when the slot is unreadable or was
    // already released by a state write whose parent flush failed.
    let journal = match Journal::load(platform, paths) {
        Ok(JournalState::Pending(journal)) => *journal,
        _ => intent.clone(),
    };
    let prior_usable = previous_is_verified(platform, paths, &journal);
    let receipt = match Receipt::load(platform, paths) {
        Ok(state) => state,
        // A receipt read fault cannot prove the install is uncommitted: never
        // roll back on it, and leave the journal for recovery.
        Err(_) => return Err(failure.with_prior_usable(prior_usable)),
    };
    match receipt {
        ReceiptState::Committed(receipt) if receipt.transaction_id == journal.transaction_id => {
            // The commit point is durable. Finish forward; if even that cannot be
            // completed, leave the journal for recover and report cleanup-pending.
            match finish_committed(platform, paths, &journal) {
                Ok(()) => Ok(completed_report(&journal)),
                Err(_) => {
                    // Best effort: record cleanup intent so the next recover
                    // finishes forward from a journal rather than phase guessing.
                    let _ = journal
                        .with_phase(Phase::CleanupPending)
                        .write(platform, paths);
                    Err(InstallFailure::new(FailureCode::CleanupPending).with_prior_usable(true))
                }
            }
        }
        // Cannot prove this is not the commit point: do not roll back.
        ReceiptState::Unreadable | ReceiptState::Unsupported => {
            Err(failure.with_prior_usable(prior_usable))
        }
        _ => match restore_previous(platform, paths, &journal) {
            Ok(()) => {
                Err(failure.with_prior_usable(previous_is_verified(platform, paths, &journal)))
            }
            // Surface the rollback failure: it is the actionable state, and the
            // pending journal makes the next install ask for recovery.
            Err(rollback) => {
                Err(rollback.with_prior_usable(previous_is_verified(platform, paths, &journal)))
            }
        },
    }
}

/// Finish a committed transaction: durably record cleanup intent, then release.
fn finish_committed(
    platform: &impl Platform,
    paths: &InstallPaths,
    journal: &Journal,
) -> InstallResult<()> {
    let live = platform.read_registration(paths)?;
    if live.as_ref().map(Registration::manifest_digest) != Some(journal.manifest.sha256()) {
        // The receipt is committed but the live registration no longer names it:
        // ambiguous, never silently cleared.
        return Err(recovery_required());
    }
    journal
        .with_phase(Phase::CleanupPending)
        .write(platform, paths)?;
    Journal::clear(platform, paths)
}

/// Whether the previous installation is live and complete right now.
///
/// This is a live read, never an assumption from the failure: `prior_usable`
/// tells the frontend whether the user still has a working connector.
fn previous_is_verified(platform: &impl Platform, paths: &InstallPaths, journal: &Journal) -> bool {
    let Some(old) = journal.old_manifest.as_ref() else {
        return false;
    };
    let Ok(Some(live)) = platform.read_registration(paths) else {
        return false;
    };
    if live.manifest_digest() != old.sha256() {
        return false;
    }
    previous_release(platform, paths, journal)
        .is_ok_and(|prior| release_is_usable(platform, paths, &prior, Some(&live)))
}

/// The committed receipt of the release this transaction would restore.
///
/// It must be the receipt that was durable when the transaction began: not this
/// transaction's own, and bound to the journaled old identity and manifest. An
/// absent, unreadable or mismatched receipt means the previous release cannot be
/// shown to exist, and nothing may be restored on the journal's say-so.
fn previous_release(
    platform: &impl Platform,
    paths: &InstallPaths,
    journal: &Journal,
) -> InstallResult<Receipt> {
    let (Some(old), Some(old_version), Some(old_payload)) = (
        journal.old_manifest.as_ref(),
        journal.old_version.as_ref(),
        journal.old_payload_id,
    ) else {
        return Err(recovery_required());
    };
    let ReceiptState::Committed(prior) = Receipt::load(platform, paths)? else {
        return Err(recovery_required());
    };
    if prior.transaction_id == journal.transaction_id
        || prior.manifest_sha256 != old.sha256()
        || prior.payload_id != old_payload
        || &prior.version != old_version
    {
        return Err(recovery_required());
    }
    Ok(*prior)
}

/// Whether the release a committed receipt names is complete and launchable now.
///
/// This is the one definition of "usable" shared by recovery reports, the
/// `prior_usable` flag and the restore path. It requires, all together: the
/// installed canonical inventory (and the sealed one when the receipt claims it)
/// to be the claimed documents; every inventory entry to be a receipt claim with
/// its exact digest and to verify on disk through validated handles (a missing
/// dependency or an altered executable is damage); the single inventory
/// executable to be launchable; and, when a live registration is supplied, that
/// registration to be the committed manifest selecting exactly that executable.
/// It performs no OS trust assessment: it never claims a release was newly
/// security-certified, only that what the committed installation recorded is
/// still whole.
pub(crate) fn release_is_usable(
    platform: &impl Platform,
    paths: &InstallPaths,
    receipt: &Receipt,
    live: Option<&Registration>,
) -> bool {
    release_is_usable_inner(platform, paths, receipt, live).is_ok()
}

fn release_is_usable_inner(
    platform: &impl Platform,
    paths: &InstallPaths,
    receipt: &Receipt,
    live: Option<&Registration>,
) -> InstallResult<()> {
    let version = parse_version(&receipt.version)?;
    let version_relative = paths.version_dir_relative(&version, receipt.payload_id);
    let ownership = &receipt.ownership;
    let canonical = retained_document(
        paths,
        ownership,
        &format!("{version_relative}/{PACKAGE_INVENTORY_FILE}"),
        receipt.payload_id,
    )?;
    let sealed_relative = format!("{version_relative}/{SEALED_INVENTORY_FILE}");
    let entries = match ownership
        .owned
        .iter()
        .find(|claim| claim.kind == OwnedKind::File && claim.path == sealed_relative)
    {
        None => parse_inventory(&canonical)?,
        Some(claim) => {
            let digest = claim.sha256.ok_or_else(no_rollback)?;
            parse_inventory(&retained_document(
                paths,
                ownership,
                &sealed_relative,
                digest,
            )?)?
        }
    };
    let mut executables = entries.iter().filter(|entry| entry.executable);
    let (Some(executable), None) = (executables.next(), executables.next()) else {
        return Err(no_rollback());
    };
    // Every claim of the version, re-verified by content through validated handles.
    retained_claims(
        paths,
        &entries,
        &version_relative,
        receipt.payload_id,
        ownership,
    )?;
    let executable_relative = format!("{version_relative}/{}", executable.path);
    let handle = ValidatedPath::new(paths, Path::new(&executable_relative), PathKind::File)?;
    platform.check_launch_permissions(&handle, true)?;
    if let Some(live) = live {
        if live.manifest_digest() != receipt.manifest_sha256 {
            return Err(recovery_required());
        }
        let selected = fs::canonicalize(parse_manifest(live.manifest())?.executable()?)
            .map_err(|_| unsafe_path())?;
        let expected =
            fs::canonicalize(paths.root().join(&executable_relative)).map_err(|_| unsafe_path())?;
        if selected != expected {
            return Err(recovery_required());
        }
    }
    Ok(())
}

/// Re-activate an already retained, verified immutable version (Task 7 rollback).
///
/// The target version directory must already exist, verify file by file against
/// the release metadata retained inside it, and be claimed by the committed
/// receipt. The retained metadata carries the exact authenticated policy carrier
/// and the API window captured when that version was installed; a package the
/// caller supplies now may only *select* the retained prior release, never
/// replace its carrier authority. This is what makes rollback independent of the
/// original download.
///
/// The transaction creates no payload: the only mutations are the two ordered
/// registration publications and the committed receipt, so a failed activation
/// restores the previous registration and never touches the retained payload.
///
/// Policy is composed here, under the lock, from the retained policy and the
/// policy the committed installation enforces: denials accumulate, publisher and
/// rollback sets are intersected, and the retained signer must be authorized by
/// both policies, so a stale signature cannot relax the current policy.
pub(crate) fn activate_retained(
    platform: &impl Platform,
    paths: &InstallPaths,
    package: Option<&AuthenticatedPackage>,
    quiescence: Duration,
) -> InstallResult<InstallReport> {
    let _lock = platform.lock(paths)?;

    match Journal::load(platform, paths)? {
        JournalState::None => {}
        JournalState::Pending(_) | JournalState::Unreadable => {
            return Err(recovery_required());
        }
        JournalState::Unsupported => return Err(unsupported_state()),
    }

    let current = match Receipt::load(platform, paths)? {
        ReceiptState::None => return Err(no_rollback()),
        ReceiptState::Committed(receipt) => *receipt,
        ReceiptState::Unreadable => return Err(recovery_required()),
        ReceiptState::Unsupported => return Err(unsupported_state()),
    };
    // Exactly one prior install is retained, so that is the only rollback target.
    let Some(prior) = current.prior.as_ref() else {
        return Err(no_rollback());
    };
    let version = parse_version(&prior.version)?;

    // A supplied package may select the retained prior release but is never the
    // carrier authority: identity only, and it must be the recorded prior.
    if let Some(package) = package {
        let descriptor = package.descriptor();
        descriptor.validate()?;
        if descriptor.target != paths.target() {
            return Err(InstallFailure::new(FailureCode::UnsupportedTarget));
        }
        if descriptor.channel != paths.channel() {
            return Err(origin_rejected());
        }
        if descriptor.version != prior.version
            || descriptor.payload_id != prior.payload_id
            || descriptor.parsed_version()? >= parse_version(&current.version)?
        {
            return Err(no_rollback());
        }
    }

    let version_relative = paths.version_dir_relative(&version, prior.payload_id);
    // The policy this rollback composes against is the committed installation's,
    // stored in an editable receipt: prove it is not weaker than what the installed
    // release's own signed carrier requires before composing anything with it.
    authenticate_enforced_policy(platform, paths, &current)?;
    // Retained content is authenticated exactly like a fresh install (see
    // `authenticate_release_in_place`): the OS verifies the signed carrier
    // already installed inside the immutable version directory, and neither the
    // locally stored metadata nor the receipt can supply a signer, an assessment
    // or a policy of its own.
    let ReleaseEvidence {
        retained,
        proof,
        contents: os_contents,
    } = authenticate_release_in_place(
        platform,
        paths,
        &current.ownership,
        &prior.version,
        prior.payload_id,
        Some(prior.carrier_sha256),
        Some(&current.release_policy),
        Some(&parse_version(&current.version)?),
        FailureCode::NoRollback,
    )?;
    let descriptor = &retained.descriptor;

    let policy = effective_policy(
        &current.release_policy,
        &descriptor.policy,
        Operation::Rollback,
    )?;
    if policy.denies(&prior.payload_id) {
        return Err(InstallFailure::new(FailureCode::PolicyDenied));
    }
    if !policy.permits_rollback(&prior.payload_id) {
        return Err(no_rollback());
    }

    let Some(live) = platform.read_registration(paths)? else {
        return Err(no_rollback());
    };
    if !registration_is_owned(paths, &live) {
        return Err(foreign_state());
    }
    if live.manifest_digest() != current.manifest_sha256 {
        return Err(recovery_required());
    }

    let entries = retained_inventory(
        paths,
        &current.ownership,
        &version_relative,
        prior.payload_id,
        os_contents.sealed_inventory,
    )?;
    let mut executables = entries.iter().filter(|entry| entry.executable);
    let executable = executables.next().ok_or_else(invalid_package)?;
    if executables.next().is_some() {
        return Err(invalid_package());
    }

    let created = retained_claims(
        paths,
        &entries,
        &version_relative,
        prior.payload_id,
        &current.ownership,
    )?;
    let manifest = manifest_for(
        paths,
        &paths
            .version_dir(&version, prior.payload_id)
            .join(&executable.path),
        &descriptor.extension_id,
    )?;
    if prior
        .manifest_sha256
        .is_some_and(|digest| digest != manifest.sha256())
    {
        // The rebuilt manifest is not the one that was live: no honest rollback.
        return Err(no_rollback());
    }

    let warnings = compatibility_warnings(paths, &current, &retained);
    let old_manifest = ManifestSnapshot::new(live.manifest().to_vec())?;
    let preimage = registration_preimage(&live, old_manifest.sha256())?;
    let journal = Journal {
        schema: JOURNAL_SCHEMA_VERSION,
        transaction_id: transaction_id(
            Operation::Rollback,
            paths.target(),
            paths.channel(),
            &descriptor.version,
            prior.payload_id,
            manifest.sha256(),
            Some(old_manifest.sha256()),
        ),
        operation: Operation::Rollback,
        phase: Phase::Prepared,
        target: paths.target(),
        channel: paths.channel(),
        root: root_rendering(paths)?,
        version: descriptor.version.clone(),
        payload_id: prior.payload_id,
        old_version: Some(current.version.clone()),
        old_payload_id: Some(current.payload_id),
        old_carrier_sha256: Some(current.policy.carrier_sha256),
        manifest,
        old_manifest: Some(old_manifest),
        preimage: Some(preimage),
        policy: PolicyCarrierRefs::new(
            Digest::of(&canonical_json(descriptor)?),
            Digest::of(proof.carrier()),
            proof.signer(),
            proof.assessment(),
            policy.epoch,
        )?,
        release_policy: policy,
        created,
    };
    journal.validate(paths)?;
    // Durable intent before the activation mutation.
    journal.write(platform, paths)?;

    let host = InstalledHost {
        version_relative: &version_relative,
        executable_relative: &executable.path,
        descriptor,
    };
    match run_retained(platform, paths, &journal, &current, quiescence, &host) {
        Ok(()) => Ok(completed_report(&journal).with_warnings(warnings)),
        Err(failure) => settle_failed_install(platform, paths, &journal, failure),
    }
}

/// Authenticate the release a committed receipt names and require the policy the
/// receipt records to be no broader than the policy that release's signed carrier
/// carries.
///
/// The recorded policy is installer state, never evidence: it is the operand every
/// rollback, upgrade and recovery composes against. The release's own signed
/// descriptor (covered by the OS-verified carrier) is the evidence that bounds it,
/// because every composition of a committed policy only tightens what the installed
/// release signed; a rollback-produced receipt is bounded by the replaced release as
/// well (see [`bound_by_replaced_release`]). State that has lost a signed
/// restriction, or whose release cannot be authenticated, fails closed with
/// `RecoveryRequired` (an explicit OS denial or unavailability is reported as such).
/// A receipt policy that passes may still be TIGHTER than the signed evidence - that
/// only ever refuses more - but never looser.
pub(crate) fn authenticate_enforced_policy(
    platform: &impl Platform,
    paths: &InstallPaths,
    receipt: &Receipt,
) -> InstallResult<()> {
    let evidence = authenticate_release_in_place(
        platform,
        paths,
        &receipt.ownership,
        &receipt.version,
        receipt.payload_id,
        Some(receipt.policy.carrier_sha256),
        None,
        None,
        FailureCode::RecoveryRequired,
    )?;
    if !is_no_broader_than(
        &receipt.release_policy,
        &evidence.contents.descriptor.policy,
    ) {
        return Err(recovery_required());
    }
    bound_by_replaced_release(platform, paths, receipt)
}

/// Bound a rollback-produced receipt policy by the signed policy of the release that
/// rollback replaced.
///
/// A rollback composes the target's policy with the replaced release's, so the
/// committed policy carries denials the now-installed (older) release never signed.
/// They are backed only by the replaced release, which stays retained as the
/// receipt's prior, and the receipt's own policy is no evidence of them.
///
/// Whether a receipt is rollback-produced is NOT read from its `operation` tag: the
/// tag is as editable as the policy it would excuse. The history decides, from
/// version ordering that the receipt's own claims fix: a receipt whose recorded prior
/// is strictly newer than the installed release, or that still owns the version
/// directory of a strictly newer release, replaced a newer release - whatever its tag
/// says. Such a receipt MUST carry the `Rollback` tag (anything else is a
/// contradictory history and fails closed), and it is then bounded by the replaced
/// release, which must be strictly newer, be the only newer release the receipt
/// owns, and be authenticated in place. Only a receipt with no newer release in its
/// history has nothing to add: an install adopts the installed release's policy.
///
/// Release versions order the two releases; payload ids do not, because distinct
/// signed releases may carry byte-identical payloads. The authenticated carrier
/// (which fixes the version) and the retained descriptor bind the identity.
pub(crate) fn bound_by_replaced_release(
    platform: &impl Platform,
    paths: &InstallPaths,
    receipt: &Receipt,
) -> InstallResult<()> {
    let installed = parse_version(&receipt.version)?;
    let prior_is_newer = match receipt.prior.as_ref() {
        Some(prior) => parse_version(&prior.version)? > installed,
        None => false,
    };
    let newer_owned = owned_releases_newer_than(&receipt.ownership, &installed);
    if receipt.operation != Operation::Rollback {
        return if receipt_replaced_a_newer_release(receipt)? {
            // A newer release was replaced, yet the tag says nothing was rolled
            // back: the tag or the history was edited.
            Err(recovery_required())
        } else {
            Ok(())
        };
    }
    let replaced = receipt.prior.as_ref().ok_or_else(recovery_required)?;
    if !prior_is_newer || replaced.carrier_sha256 == receipt.policy.carrier_sha256 {
        return Err(recovery_required());
    }
    let replaced_directory =
        paths.version_dir_relative(&parse_version(&replaced.version)?, replaced.payload_id);
    if newer_owned
        .iter()
        .any(|directory| *directory != replaced_directory)
    {
        return Err(recovery_required());
    }
    let evidence = authenticate_release_in_place(
        platform,
        paths,
        &receipt.ownership,
        &replaced.version,
        replaced.payload_id,
        Some(replaced.carrier_sha256),
        None,
        None,
        FailureCode::RecoveryRequired,
    )?;
    if !is_no_broader_than(
        &receipt.release_policy,
        &evidence.contents.descriptor.policy,
    ) {
        return Err(recovery_required());
    }
    Ok(())
}

/// Whether a receipt's own history shows that it replaced a strictly newer release.
fn receipt_replaced_a_newer_release(receipt: &Receipt) -> InstallResult<bool> {
    let installed = parse_version(&receipt.version)?;
    let prior_is_newer = match receipt.prior.as_ref() {
        Some(prior) => parse_version(&prior.version)? > installed,
        None => false,
    };
    Ok(prior_is_newer || !owned_releases_newer_than(&receipt.ownership, &installed).is_empty())
}

/// The version directories a receipt owns for releases strictly newer than `version`.
///
/// Directory names are `versions/<version>-<payload id>`, fixed by the layout and
/// checked against the disk whenever a claim is used, so a newer release the receipt
/// still owns is evidence of a downgrade that no tag can hide.
fn owned_releases_newer_than(ownership: &OwnershipRecord, version: &Version) -> BTreeSet<String> {
    let prefix = format!("{VERSIONS_DIR}/");
    ownership
        .owned
        .iter()
        .filter_map(|claim| {
            let directory = claim.path.strip_prefix(&prefix)?.split('/').next()?;
            let (text, identity) = directory.rsplit_once('-')?;
            (identity.len() == 64 && Version::parse(text).is_ok_and(|owned| owned > *version))
                .then(|| format!("{prefix}{directory}"))
        })
        .collect()
}

/// What the OS authenticated about one installed release.
struct ReleaseEvidence {
    /// The receipt/journal-claimed retained metadata (a reference, never trust).
    retained: RetainedInstall,
    /// The OS-verified signer and assessment of the installed carrier artifact.
    proof: TrustProof,
    /// The carrier document the OS path extracted from that artifact.
    contents: CarrierContents,
}

/// Authenticate an installed release **in place**, as data.
///
/// This is the single authentication used by rollback and by crash recovery. The
/// release is identified by the claimed `version`/`payload_id`; its retained
/// metadata must be a claim in `ownership`; and the signed carrier artifact
/// installed inside the immutable version directory is handed to the platform's
/// OS verification ([`Platform::verify_retained`]) - the stored metadata can never
/// supply a signer, assessment or policy. The extracted descriptor must equal the
/// retained one, the carrier digest must equal the digest the caller recorded, and
/// the OS-verified signer must be listed by BOTH `anchor_policy` and the
/// authenticated descriptor's own policy.
///
/// `unprovable` is reported when the installed form cannot be established
/// (missing or changed claims, an artifact the adapter cannot verify in place);
/// an explicit OS denial or an unavailable assessment is always returned as
/// `TrustDenied` / `TrustUnavailable`, and a policy refusal as `PolicyDenied`.
/// `must_precede` rejects a release that is not older than the given version.
#[allow(clippy::too_many_arguments)]
fn authenticate_release_in_place(
    platform: &impl Platform,
    paths: &InstallPaths,
    ownership: &OwnershipRecord,
    version_text: &str,
    payload_id: Digest,
    carrier_sha256: Option<Digest>,
    anchor_policy: Option<&ReleasePolicy>,
    must_precede: Option<&Version>,
    unprovable: FailureCode,
) -> InstallResult<ReleaseEvidence> {
    let version = parse_version(version_text)?;
    let unprovable = || InstallFailure::new(unprovable);
    let version_relative = paths.version_dir_relative(&version, payload_id);
    let retained_relative = format!("{version_relative}/{RETAINED_FILE_NAME}");
    // The retained metadata is a claim of the immutable version. A missing,
    // changed or unparsable document means the release cannot be proven.
    let retained = load_retained(paths, ownership, &retained_relative).map_err(|_| unprovable())?;
    let descriptor = &retained.descriptor;
    if descriptor.version != version_text
        || descriptor.payload_id != payload_id
        || descriptor.target != paths.target()
        || descriptor.channel != paths.channel()
        || must_precede.is_some_and(|limit| &version >= limit)
    {
        return Err(unprovable());
    }
    let carrier_relative = retained.carrier_relative(paths).map_err(|_| unprovable())?;
    if carrier_relative
        != format!(
            "{version_relative}/{}",
            package_carrier_name(paths.target())
        )
    {
        return Err(unprovable());
    }
    let carrier_source =
        PackageSource::open(&paths.root().join(&version_relative)).map_err(|_| unprovable())?;
    // `None` anchors the release to the policy its own signed descriptor carries:
    // the descriptor is covered by the OS-verified carrier (equality is enforced
    // below), so that is signed evidence, unlike any policy read back from state.
    let anchor_policy = anchor_policy.unwrap_or(&descriptor.policy);
    let anchor = ReleaseDescriptor {
        target: paths.target(),
        channel: paths.channel(),
        policy: anchor_policy.clone(),
        ..descriptor.clone()
    };
    let proof = match platform.verify_retained(&carrier_source, &anchor) {
        Ok(proof) => proof,
        // The OS refused the installed artifact, or no assessment could be
        // formed: report that verdict, never a generic unverifiable release.
        Err(failure)
            if matches!(
                failure.code,
                FailureCode::TrustDenied | FailureCode::TrustUnavailable
            ) =>
        {
            return Err(failure);
        }
        // The installed form cannot be verified in place (missing, wrong kind,
        // links, an adapter that cannot verify it).
        Err(_) => return Err(unprovable()),
    };
    if !is_accepted_assessment(proof.assessment(), paths.target()) {
        return Err(InstallFailure::new(FailureCode::TrustDenied));
    }
    let contents = read_carrier_contents(proof.carrier())
        .map_err(|_| InstallFailure::new(FailureCode::TrustDenied))?;
    if &contents.descriptor != descriptor {
        return Err(InstallFailure::new(FailureCode::TrustDenied));
    }
    if Digest::of(proof.carrier()) != retained.carrier_sha256
        || carrier_sha256.is_some_and(|expected| retained.carrier_sha256 != expected)
    {
        return Err(InstallFailure::new(FailureCode::TrustDenied));
    }
    // The OS-verified signer must be authorized under BOTH policies, not merely
    // belong to a non-empty publisher intersection. A retained artifact signed by
    // a publisher the persisted policy has revoked must stay denied.
    let signer = proof.signer();
    if !anchor_policy
        .publishers
        .iter()
        .any(|publisher| publisher == signer)
        || !contents
            .descriptor
            .policy
            .publishers
            .iter()
            .any(|publisher| publisher == signer)
    {
        return Err(InstallFailure::new(FailureCode::PolicyDenied));
    }
    Ok(ReleaseEvidence {
        retained,
        proof,
        contents,
    })
}

/// Read the receipt-claimed retained metadata of one immutable version.
///
/// Every failure is `NoRollback`: a missing, unclaimed, changed or unparsable
/// document cannot prove the retained target, and repair guidance is the honest
/// answer. The current installation is never modified by this read.
fn load_retained(
    paths: &InstallPaths,
    ownership: &OwnershipRecord,
    relative: &str,
) -> InstallResult<RetainedInstall> {
    let claim = ownership
        .owned
        .iter()
        .find(|claim| claim.kind == OwnedKind::File && claim.path == relative)
        .ok_or_else(no_rollback)?;
    let expected = claim.sha256.ok_or_else(no_rollback)?;
    let absolute = paths.root().join(relative);
    match fs::symlink_metadata(&absolute) {
        Ok(metadata) if metadata.is_file() => {}
        _ => return Err(no_rollback()),
    }
    let bytes = read_no_follow(&absolute, MAX_RETAINED_BYTES as u64).map_err(|_| no_rollback())?;
    if Digest::of(&bytes) != expected {
        return Err(no_rollback());
    }
    RetainedInstall::parse(&bytes).map_err(|_| no_rollback())
}

/// Read and verify the retained inventory of one immutable version.
///
/// Returns the **installed-byte** entries: the canonical (unsigned) inventory
/// when the OS-authenticated carrier asserts no sealed digest, otherwise the
/// sealed inventory that digest authenticates, after proving it corresponds to
/// the canonical identity the descriptor binds.
fn retained_inventory(
    paths: &InstallPaths,
    ownership: &OwnershipRecord,
    version_relative: &str,
    payload_id: Digest,
    sealed_digest: Option<Digest>,
) -> InstallResult<Vec<InventoryEntry>> {
    let canonical = retained_document(
        paths,
        ownership,
        &format!("{version_relative}/{PACKAGE_INVENTORY_FILE}"),
        payload_id,
    )?;
    let canonical = parse_inventory(&canonical).map_err(|_| no_rollback())?;
    let entries = match sealed_digest {
        None => canonical,
        Some(asserted) => {
            let sealed = retained_document(
                paths,
                ownership,
                &format!("{version_relative}/{SEALED_INVENTORY_FILE}"),
                asserted,
            )?;
            let sealed = parse_inventory(&sealed).map_err(|_| no_rollback())?;
            let root = paths.root().join(version_relative);
            verify_correspondence(&canonical, &sealed, |relative| {
                entry_is_signable_image(&root, relative)
            })
            .map_err(|_| no_rollback())?;
            sealed
        }
    };
    if entries.iter().filter(|entry| entry.executable).count() != 1 {
        return Err(no_rollback());
    }
    Ok(entries)
}

/// One receipt-claimed document of a retained version, bound to `expected`.
fn retained_document(
    paths: &InstallPaths,
    ownership: &OwnershipRecord,
    relative: &str,
    expected: Digest,
) -> InstallResult<Vec<u8>> {
    if !ownership.owned.iter().any(|claim| {
        claim.kind == OwnedKind::File && claim.path == relative && claim.sha256 == Some(expected)
    }) {
        return Err(no_rollback());
    }
    let absolute = paths.root().join(relative);
    match fs::symlink_metadata(&absolute) {
        Ok(metadata) if metadata.is_file() => {}
        _ => return Err(no_rollback()),
    }
    let bytes = read_no_follow(&absolute, MAX_DOCUMENT_BYTES as u64).map_err(|_| no_rollback())?;
    if Digest::of(&bytes) != expected {
        return Err(no_rollback());
    }
    Ok(bytes)
}

/// The API-window warning for a rollback, when the retained release does not
/// overlap the window of the release it is replacing.
fn compatibility_warnings(
    paths: &InstallPaths,
    current: &Receipt,
    target: &RetainedInstall,
) -> Vec<WarningCode> {
    let Some(current_window) = current_api_window(paths, current) else {
        return Vec::new();
    };
    let (target_min, target_max) = target.api_window();
    let (current_min, current_max) = current_window;
    if target_max < current_min || current_max < target_min {
        vec![WarningCode::CompatibilityWindow]
    } else {
        Vec::new()
    }
}

/// The retained API window of the active release, when its metadata verifies.
fn current_api_window(paths: &InstallPaths, current: &Receipt) -> Option<(u32, u32)> {
    let version = parse_version(&current.version).ok()?;
    let relative = format!(
        "{}/{}",
        paths.version_dir_relative(&version, current.payload_id),
        RETAINED_FILE_NAME
    );
    let claim = current
        .ownership
        .owned
        .iter()
        .find(|claim| claim.kind == OwnedKind::File && claim.path == relative)?;
    let expected = claim.sha256?;
    let bytes = read_no_follow(&paths.root().join(&relative), MAX_RETAINED_BYTES as u64).ok()?;
    if Digest::of(&bytes) != expected {
        return None;
    }
    RetainedInstall::parse(&bytes)
        .ok()
        .map(|retained| retained.api_window())
}

/// Where the installed host of a retained release lives and which release it is.
struct InstalledHost<'a> {
    version_relative: &'a str,
    executable_relative: &'a str,
    descriptor: &'a ReleaseDescriptor,
}

/// The rollback transaction body, after the Prepared journal is durable.
fn run_retained(
    platform: &impl Platform,
    paths: &InstallPaths,
    journal: &Journal,
    current: &Receipt,
    quiescence: Duration,
    host: &InstalledHost<'_>,
) -> InstallResult<()> {
    // The retained host is re-assessed in its installed form like a fresh one.
    verify_installed_host(
        platform,
        paths,
        host.version_relative,
        host.executable_relative,
        host.descriptor,
        &journal.release_policy,
    )?;
    require_quiescent_within(platform, paths, quiescence)?;
    let registration = registration_from(&journal.manifest, journal.preimage.as_ref())?;
    platform.publish_manifest(paths, &registration)?;
    journal
        .with_phase(Phase::ManifestActivated)
        .write(platform, paths)?;
    platform.publish_registration(paths, &registration)?;
    journal
        .with_phase(Phase::RegistrationPublished)
        .write(platform, paths)?;

    let live = platform.read_registration(paths)?.ok_or_else(io_failure)?;
    if live.manifest_digest() != journal.manifest.sha256() {
        return Err(io_failure());
    }
    verify_created_assets(paths, journal)?;

    // The receipt is the commit point. It carries both versions' claims forward,
    // so a later uninstall removes the retained target and the version it came
    // from only.
    let receipt = build_receipt(paths, journal, Some(current))?;
    receipt.write(platform, paths)?;
    journal
        .with_phase(Phase::ReceiptCommitted)
        .write(platform, paths)?;
    journal
        .with_phase(Phase::CleanupPending)
        .write(platform, paths)?;
    Journal::clear(platform, paths)
}

/// Verify a retained version directory against the retained inventory and
/// return the claim set the transaction journals.
///
/// Every retained path must be claimed by the committed receipt (a receipt never
/// authorizes an arbitrary path), must exist with the authenticated identity and
/// must belong to this immutable version. A missing or changed retained file is
/// `NoRollback`: the target cannot be proven and repair guidance is the answer.
fn retained_claims(
    paths: &InstallPaths,
    entries: &[InventoryEntry],
    version_relative: &str,
    payload_id: Digest,
    ownership: &OwnershipRecord,
) -> InstallResult<Vec<JournalClaim>> {
    ownership.validate(paths)?;
    let contains = |relative: &str| {
        relative == version_relative || relative.starts_with(&format!("{version_relative}/"))
    };
    let claimed = |relative: &str, kind: OwnedKind, sha256: Option<Digest>| {
        ownership
            .owned
            .iter()
            .any(|claim| claim.kind == kind && claim.path == relative && claim.sha256 == sha256)
    };
    let claim_exists = |relative: &str, kind: OwnedKind| {
        ownership
            .owned
            .iter()
            .any(|claim| claim.kind == kind && claim.path == relative)
    };

    // The immutable version directory itself must be owned by this receipt.
    if !claimed(version_relative, OwnedKind::Directory, None) {
        return Err(no_rollback());
    }
    // Every authenticated payload entry is a claimed file with its exact digest.
    for entry in entries {
        let relative = format!("{version_relative}/{}", entry.path);
        if !claimed(&relative, OwnedKind::File, Some(entry.sha256)) {
            return Err(no_rollback());
        }
    }
    // The installed inventory document is the retained payload identity; it is a
    // file claim of the receipt, not an authenticated package member.
    let inventory_relative = format!("{version_relative}/{PACKAGE_INVENTORY_FILE}");
    if !claimed(&inventory_relative, OwnedKind::File, Some(payload_id)) {
        return Err(no_rollback());
    }
    // The retained metadata and the signed carrier artifact are claims of the
    // immutable version too: a rollback must be able to prove and later remove
    // exactly what it re-activated (receipt generation carries them forward).
    if !claim_exists(
        &format!("{version_relative}/{RETAINED_FILE_NAME}"),
        OwnedKind::File,
    ) {
        return Err(no_rollback());
    }
    let carrier_relative = format!(
        "{version_relative}/{}",
        crate::platform::package_carrier_name(paths.target())
    );
    let carrier_kind = match paths.target() {
        crate::model::Target::WindowsX64 => OwnedKind::File,
        crate::model::Target::MacosArm64 => OwnedKind::Directory,
    };
    if !claim_exists(&carrier_relative, carrier_kind) {
        return Err(no_rollback());
    }

    // Every claim inside the version directory is re-verified through a validated
    // handle and journaled, so the rebuilt receipt keeps ownership of the whole
    // tree it re-activated (metadata and carrier included) and an interrupted
    // rollback can verify it again.
    let mut claims = Vec::with_capacity(ownership.owned.len());
    for claim in &ownership.owned {
        if !contains(&claim.path) {
            continue;
        }
        match claim.kind {
            OwnedKind::File => {
                let sha256 = claim.sha256.ok_or_else(no_rollback)?;
                // Damaged retained content is guidance, not `ForeignState`: the
                // prior installation stays exactly as it is.
                let entry = match OwnedEntry::from_claim(paths, claim) {
                    Ok(Some(entry)) => entry,
                    Ok(None) | Err(_) => return Err(no_rollback()),
                };
                let size = fs::symlink_metadata(entry.path().rendered())
                    .map_err(|_| no_rollback())?
                    .len();
                claims.push(JournalClaim::file(claim.path.clone(), sha256, size, false));
            }
            // A retained bundle link is re-verified against its exact target
            // bytes and carried forward the same way.
            OwnedKind::Symlink => {
                let sha256 = claim.sha256.ok_or_else(no_rollback)?;
                let entry = match OwnedEntry::from_claim(paths, claim) {
                    Ok(Some(entry)) => entry,
                    Ok(None) | Err(_) => return Err(no_rollback()),
                };
                let size = fs::symlink_metadata(entry.path().rendered())
                    .map_err(|_| no_rollback())?
                    .len();
                claims.push(JournalClaim::link(claim.path.clone(), sha256, size));
            }
            OwnedKind::Directory => {
                match fs::symlink_metadata(paths.root().join(&claim.path)) {
                    Ok(metadata) if metadata.is_dir() => {
                        ValidatedPath::new(paths, Path::new(&claim.path), PathKind::Directory)
                            .map_err(|_| no_rollback())?;
                    }
                    _ => return Err(no_rollback()),
                }
                claims.push(JournalClaim::directory(claim.path.clone()));
            }
        }
    }
    Ok(claims)
}

/// Reconcile a pending transaction with verified live state.
///
/// * A committed receipt is finished forward, whatever the journal phase says.
/// * A live, complete activation is finished (publication, receipt, cleanup).
/// * A live previous state is restored and the transaction's own assets removed.
/// * A live registration this installer does not own is `ForeignState`; live
///   bytes that match neither recorded manifest are `RecoveryRequired`. Neither
///   mutates anything.
pub fn recover(platform: &impl Platform, paths: &InstallPaths) -> InstallResult<InstallReport> {
    let _lock = platform.lock(paths)?;

    let journal = match Journal::load(platform, paths)? {
        JournalState::None => return clean_report(platform, paths),
        JournalState::Unreadable => return recover_unreadable_journal(platform, paths),
        // A newer engine's pending transaction: refused, never reinterpreted,
        // quarantined or released by this one.
        JournalState::Unsupported => return Err(unsupported_state()),
        JournalState::Pending(journal) => *journal,
    };
    if journal.operation == Operation::Uninstall {
        // A partially removed installation is finished only by the uninstall
        // operation, which owns removal semantics. Recovery must neither
        // re-activate a version for it nor guess how far removal got.
        return Err(InstallFailure::new(FailureCode::RecoveryRequired));
    }

    let live = platform.read_registration(paths)?;
    if let Some(live) = live.as_ref()
        && !registration_is_owned(paths, live)
    {
        // Another product owns the fixed registration path: nothing here is
        // ours to complete or to remove.
        return Err(InstallFailure::new(FailureCode::ForeignState));
    }
    let live_digest = live.as_ref().map(Registration::manifest_digest);
    let new_digest = journal.manifest.sha256();
    let old_digest = journal.old_manifest.as_ref().map(ManifestSnapshot::sha256);
    // Verify the created inventory once; completion must not repeat the walk.
    let activation_verified =
        live_digest == Some(new_digest) && verify_created_assets(paths, &journal).is_ok();

    let receipt = Receipt::load(platform, paths)?;
    if matches!(receipt, ReceiptState::Unsupported) {
        return Err(unsupported_state());
    }
    let committed = matches!(
        &receipt,
        ReceiptState::Committed(receipt) if receipt.transaction_id == journal.transaction_id
    );
    if committed || matches!(receipt, ReceiptState::Unreadable) {
        // The receipt is (or may be) the commit point: never restore.
        return if activation_verified {
            authenticate_for_completion(platform, paths, &journal, &receipt)?;
            complete_forward(platform, paths, &journal)
        } else {
            Err(InstallFailure::new(FailureCode::RecoveryRequired))
        };
    }

    // Anything that is neither the recorded previous state nor the recorded new
    // state is ambiguous.
    if live_digest.is_some() && live_digest != old_digest && live_digest != Some(new_digest) {
        return Err(InstallFailure::new(FailureCode::RecoveryRequired));
    }

    if activation_verified {
        authenticate_for_completion(platform, paths, &journal, &receipt)?;
        return complete_forward(platform, paths, &journal);
    }

    // The activation did not happen, or its staged payload no longer verifies:
    // restore the recorded previous state and remove only this transaction's
    // own assets.
    restore_previous(platform, paths, &journal)?;
    let had_previous = journal.old_payload_id.is_some();
    Ok(InstallReport {
        operation: journal.operation,
        state: if had_previous {
            STATE_RECOVERED_PREVIOUS.to_owned()
        } else {
            STATE_CLEAN.to_owned()
        },
        // The same live read `install` uses, never an assumption from the phase.
        prior_usable: previous_is_verified(platform, paths, &journal),
        active_payload: journal.old_payload_id,
        failure: None,
        warnings: Vec::new(),
    })
}

/// Authenticate the staged (or committed) release before recovery completes it.
///
/// A journal, a receipt and the files they claim are installer state: they are
/// never authority to publish a registration or commit a receipt. Completion
/// therefore re-establishes, from authenticated inputs only:
///
/// 1. the OS verdict over the signed carrier installed in the version directory
///    (denial and unavailability are returned unchanged, with nothing mutated);
/// 2. the journal's identity, carrier digest and descriptor against that carrier;
/// 3. the effective policy, recomputed from the policy the committed installation
///    enforced and the authenticated descriptor's policy, and compared with the
///    journal's. The committed policy is itself receipt state, so it is first bounded
///    by the signed carrier of the release it belongs to (before the receipt: the
///    release still installed; after it: the release the rollback replaced, which
///    stays retained as the receipt's prior);
/// 4. the complete installed inventory (canonical, and sealed when the carrier
///    asserts one) against every claim and every file on disk;
/// 5. the exact manifest the journal publishes, rebuilt from the authenticated
///    descriptor and the inventory's executable; and
/// 6. the installed host's launch permission and OS assessment.
fn authenticate_for_completion(
    platform: &impl Platform,
    paths: &InstallPaths,
    journal: &Journal,
    receipt: &ReceiptState,
) -> InstallResult<()> {
    // The claims describing the release: the receipt that names this very
    // transaction, otherwise the journal's created set.
    let committed_here = match receipt {
        ReceiptState::Committed(receipt) if receipt.transaction_id == journal.transaction_id => {
            Some(receipt)
        }
        _ => None,
    };
    let ownership = match committed_here {
        Some(receipt) => receipt.ownership.clone(),
        None => journal.ownership_record(),
    };

    let evidence = authenticate_release_in_place(
        platform,
        paths,
        &ownership,
        &journal.version,
        journal.payload_id,
        Some(journal.policy.carrier_sha256),
        Some(&journal.release_policy),
        None,
        FailureCode::RecoveryRequired,
    )?;
    let descriptor = &evidence.retained.descriptor;
    if journal.policy.descriptor_sha256 != Digest::of(&canonical_json(descriptor)?) {
        return Err(recovery_required());
    }

    // The effective policy is recomputed from authenticated evidence, never read
    // back from state. Journal and receipt are both installer state: that they agree
    // with each other proves nothing about what the signature covers.
    //
    // The operation tags select which evidence a branch demands, so they are held to
    // the history rather than trusted: the committed receipt of this very transaction
    // carries the transaction's own operation, and a non-rollback transaction never
    // completes over a history in which a newer release was replaced. Without both,
    // retagging a rollback as an install would skip every replaced-release proof.
    if let Some(committed) = committed_here {
        if committed.operation != journal.operation {
            return Err(recovery_required());
        }
        if journal.operation != Operation::Rollback && receipt_replaced_a_newer_release(committed)?
        {
            return Err(recovery_required());
        }
    }
    let expected_policy = match (journal.operation, committed_here, receipt) {
        // An install enforces exactly the incoming signed policy, whatever it was
        // composed against (`effective_policy(Install)` returns the incoming
        // policy), so this holds before and after the receipt is committed. When
        // the previous receipt is still present, the epoch rules of the ordinary
        // install are re-applied as well.
        (Operation::Install, Some(_), _) => descriptor.policy.clone(),
        (Operation::Install, None, ReceiptState::Committed(prior)) => {
            authenticate_enforced_policy(platform, paths, prior)?;
            effective_policy(
                &prior.release_policy,
                &descriptor.policy,
                Operation::Install,
            )?
        }
        (Operation::Install, None, ReceiptState::None | ReceiptState::Unreadable)
            if journal.old_payload_id.is_none() =>
        {
            descriptor.policy.clone()
        }
        // A rollback before the receipt: recomposed from the policy the committed
        // installation enforces, and the retained target must be the recorded prior
        // release of that installation, older than the one it replaces.
        (Operation::Rollback, None, ReceiptState::Committed(current)) => {
            let target = current.prior.as_ref().ok_or_else(recovery_required)?;
            if target.version != journal.version
                || target.payload_id != journal.payload_id
                || target.carrier_sha256 != journal.policy.carrier_sha256
                || journal.old_payload_id != Some(current.payload_id)
                || journal.old_version.as_deref() != Some(current.version.as_str())
                || descriptor.parsed_version()? >= parse_version(&current.version)?
            {
                return Err(recovery_required());
            }
            // The operand is receipt state. Authenticate it against the signed
            // carrier of the release still installed, so an edit that removes
            // a restriction that release signs is not recomposed into a "valid"
            // policy.
            authenticate_enforced_policy(platform, paths, current)?;
            effective_policy(
                &current.release_policy,
                &descriptor.policy,
                Operation::Rollback,
            )?
        }
        // A rollback whose receipt is already committed: the policy it was composed
        // against was replaced by the commit, so it cannot be recomposed. What the
        // authenticated descriptor of the activated release does bound is how broad
        // the committed policy may be; anything broader, or missing a denial that
        // descriptor carries, is not a policy any rollback of this release could
        // have produced. State that cannot be bounded fails closed.
        (Operation::Rollback, Some(committed), _) => {
            if !is_no_broader_than(&committed.release_policy, &descriptor.policy) {
                return Err(recovery_required());
            }
            // Composition evidence survives the commitment in the release the
            // rollback replaced: it is the receipt's prior, its signed carrier stays
            // retained, and the operand the composition started from was bounded by
            // it. A restriction only that release signs therefore still binds.
            let replaced = committed.prior.as_ref().ok_or_else(recovery_required)?;
            if journal.old_version.as_deref() != Some(replaced.version.as_str())
                || journal.old_payload_id != Some(replaced.payload_id)
                || journal.old_carrier_sha256 != Some(replaced.carrier_sha256)
            {
                return Err(recovery_required());
            }
            // Two editable references agreeing with each other is not evidence. A
            // rollback replaces a STRICTLY NEWER release, so a "replaced" release that
            // is the activated target (same or older version, or the same carrier)
            // can never have been replaced by this transaction: it would make the
            // target its own policy evidence and leave the real replaced release's
            // restrictions unestablished. Payload ids are deliberately not compared:
            // distinct signed releases may carry byte-identical payloads, and the
            // version ordering plus the carrier binding already separate them.
            if parse_version(&replaced.version)? <= descriptor.parsed_version()?
                || replaced.carrier_sha256 == journal.policy.carrier_sha256
            {
                return Err(recovery_required());
            }
            let replaced_evidence = authenticate_release_in_place(
                platform,
                paths,
                &committed.ownership,
                &replaced.version,
                replaced.payload_id,
                Some(replaced.carrier_sha256),
                None,
                None,
                FailureCode::RecoveryRequired,
            )?;
            // The authenticated identity must be the release the journalled old
            // registration manifest actually selected, and the receipt's recorded
            // digest of that manifest must be the journal's.
            if !old_manifest_selects_release(
                paths,
                journal.old_manifest.as_ref(),
                replaced.manifest_sha256,
                &replaced_evidence,
            ) {
                return Err(recovery_required());
            }
            if !is_no_broader_than(
                &committed.release_policy,
                &replaced_evidence.contents.descriptor.policy,
            ) {
                return Err(recovery_required());
            }
            committed.release_policy.clone()
        }
        // An upgrade whose previous receipt is gone, unreadable or unsupported
        // has nothing to compose the policy against.
        _ => return Err(recovery_required()),
    };
    if expected_policy != journal.release_policy {
        return Err(recovery_required());
    }
    // The committed receipt, when this transaction's, must carry that same policy.
    if let Some(committed) = committed_here
        && committed.release_policy != expected_policy
    {
        return Err(recovery_required());
    }
    if journal.release_policy.denies(&journal.payload_id) {
        return Err(InstallFailure::new(FailureCode::PolicyDenied));
    }
    // The ordinary rollback also requires the target to be on the allow-list.
    if journal.operation == Operation::Rollback
        && !journal.release_policy.permits_rollback(&journal.payload_id)
    {
        return Err(recovery_required());
    }

    let version = parse_version(&journal.version)?;
    let version_relative = paths.version_dir_relative(&version, journal.payload_id);
    let entries = retained_inventory(
        paths,
        &ownership,
        &version_relative,
        journal.payload_id,
        evidence.contents.sealed_inventory,
    )
    .map_err(unprovable_as_recovery)?;
    retained_claims(
        paths,
        &entries,
        &version_relative,
        journal.payload_id,
        &ownership,
    )
    .map_err(unprovable_as_recovery)?;
    let executable = entries
        .iter()
        .find(|entry| entry.executable)
        .ok_or_else(recovery_required)?;
    let manifest = manifest_for(
        paths,
        &paths.root().join(&version_relative).join(&executable.path),
        &descriptor.extension_id,
    )?;
    if manifest.sha256() != journal.manifest.sha256() {
        return Err(recovery_required());
    }
    verify_installed_host(
        platform,
        paths,
        &version_relative,
        &executable.path,
        descriptor,
        &journal.release_policy,
    )
}

/// Whether the registration manifest a rollback journalled as the one it replaced
/// is the registration of the authenticated `evidence` release: its host executable
/// lies inside that release's immutable version directory, its single allowed
/// origin is the origin of that release's extension, and the digest the receipt
/// recorded for the replaced registration is the journalled manifest's.
///
/// This ties the authenticated identity to bytes that were live while that release
/// was active. It cannot make rewritten history trustworthy on its own: an editor
/// who rewrites the manifest too is stopped only by the version ordering that
/// callers apply first.
fn old_manifest_selects_release(
    paths: &InstallPaths,
    old_manifest: Option<&ManifestSnapshot>,
    recorded_digest: Option<Digest>,
    evidence: &ReleaseEvidence,
) -> bool {
    let Some(old_manifest) = old_manifest else {
        return false;
    };
    if recorded_digest != Some(old_manifest.sha256()) {
        return false;
    }
    let descriptor = &evidence.contents.descriptor;
    let (Ok(manifest), Ok(version), Ok(origin)) = (
        parse_manifest(old_manifest.bytes()),
        descriptor.parsed_version(),
        extension_origin(&descriptor.extension_id),
    ) else {
        return false;
    };
    let Ok(executable) = manifest.executable() else {
        return false;
    };
    manifest.allowed_origins == [origin]
        && executable.starts_with(paths.version_dir(&version, descriptor.payload_id))
        && executable != paths.version_dir(&version, descriptor.payload_id)
}

/// `NoRollback` is the rollback operation's word for "cannot be proven"; recovery
/// reports the same condition as `RecoveryRequired`.
fn unprovable_as_recovery(failure: InstallFailure) -> InstallFailure {
    if failure.code == FailureCode::NoRollback {
        recovery_required()
    } else {
        failure
    }
}

/// Reconcile an unparsable journal against verified live state.
///
/// A torn journal must not block every later run. When the live registration and
/// the committed receipt agree (or both are absent), the live state is complete
/// and unambiguous: the rejected bytes are quarantined and the reported state is
/// the verified one. Anything else stays `RecoveryRequired` with zero mutations,
/// because there is no honest rollback target.
fn recover_unreadable_journal(
    platform: &impl Platform,
    paths: &InstallPaths,
) -> InstallResult<InstallReport> {
    let live = platform.read_registration(paths)?;
    if let Some(live) = live.as_ref()
        && !registration_is_owned(paths, live)
    {
        return Err(InstallFailure::new(FailureCode::ForeignState));
    }
    let receipt = Receipt::load(platform, paths)?;
    let reconciled = match (&live, &receipt) {
        (None, ReceiptState::None) => true,
        (None, ReceiptState::Committed(_)) => true,
        (Some(live), ReceiptState::Committed(receipt)) => {
            receipt.manifest_sha256 == live.manifest_digest()
        }
        _ => false,
    };
    if !reconciled {
        return Err(InstallFailure::new(FailureCode::RecoveryRequired));
    }
    // When the purported reconciled state is a committed installation, it must be
    // shown to be whole *before* its recovery intent is released: the journal bytes
    // are the only remaining trace of whatever was in flight, so a damaged release
    // keeps them (RecoveryRequired, nothing mutated) instead of being quarantined
    // first and only then reported damaged.
    if let ReceiptState::Committed(committed) = &receipt
        && !release_is_usable(platform, paths, committed, live.as_ref())
    {
        return Err(InstallFailure::new(FailureCode::RecoveryRequired));
    }
    // Absence of a registration is not proof that no transaction assets exist: a
    // torn first-install journal can sit beside a fully staged version directory.
    // The journal is the only record of what that transaction created, so while
    // anything outside the committed receipt's claims remains, releasing it would
    // orphan the assets (and block a retry on the immutable directory). Fail
    // closed, mutate nothing, and keep the journal for a human to reconcile.
    let committed = match &receipt {
        ReceiptState::Committed(receipt) => Some(receipt.as_ref()),
        _ => None,
    };
    if has_outstanding_assets(paths, committed)? {
        return Err(InstallFailure::new(FailureCode::RecoveryRequired));
    }
    platform.quarantine_state(paths, StateDocument::Journal)?;
    clean_report(platform, paths)
}

/// Whether anything below `versions/` or `maintenance/` is unclaimed by the
/// committed receipt (or by nothing, when there is none).
fn has_outstanding_assets(paths: &InstallPaths, receipt: Option<&Receipt>) -> InstallResult<bool> {
    for directory in [VERSIONS_DIR, MAINTENANCE_DIR] {
        let entries = match fs::read_dir(paths.root().join(directory)) {
            Ok(entries) => entries,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(_) => return Err(io_failure()),
        };
        for entry in entries {
            let entry = entry.map_err(|_| io_failure())?;
            let Some(name) = entry.file_name().to_str().map(str::to_owned) else {
                return Ok(true);
            };
            let relative = format!("{directory}/{name}");
            let claimed = receipt.is_some_and(|receipt| {
                receipt
                    .ownership
                    .owned
                    .iter()
                    .any(|claim| claim.path == relative)
            });
            if !claimed {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

impl Plan {
    fn prepare(
        platform: &impl Platform,
        paths: &InstallPaths,
        package: &AuthenticatedPackage,
    ) -> InstallResult<Self> {
        let descriptor = package.descriptor();
        descriptor.validate()?;
        if descriptor.target != paths.target() {
            return Err(InstallFailure::new(FailureCode::UnsupportedTarget));
        }
        if descriptor.channel != paths.channel() {
            // The package names another channel's root and host identity.
            return Err(InstallFailure::new(FailureCode::OriginRejected));
        }
        if package.payload().is_empty() {
            return Err(invalid_package());
        }
        let version = descriptor.parsed_version()?;
        let payload_id = descriptor.payload_id;

        // The manifest selects exactly one authenticated executable. A payload
        // that marks none or several is ambiguous, never a guess.
        let mut executables = package
            .payload()
            .iter()
            .filter(|source| source.executable());
        let executable = executables.next().ok_or_else(invalid_package)?;
        if executables.next().is_some() {
            return Err(invalid_package());
        }

        // The installed `inventory.json` is the unsigned canonical identity the
        // descriptor authenticates; the files are the shipped (possibly signed)
        // bytes, listed by the sealed inventory when signing changed any. Both
        // documents were authenticated against the signature-covered carrier.
        let inventory = package.canonical_inventory().to_vec();
        if Digest::of(&inventory) != payload_id {
            return Err(invalid_package());
        }
        let sealed = package.sealed_inventory().map(<[u8]>::to_vec);

        // An immutable version directory is created once and never reused; a
        // same-identity directory is Task 7's repair decision, not an overwrite.
        let version_relative = paths.version_dir_relative(&version, payload_id);
        let version_directory = paths.root().join(&version_relative);
        match fs::symlink_metadata(&version_directory) {
            Ok(_) => return Err(foreign_state()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            // Permission or IO failures are not "absent": never stage over them.
            Err(_) => return Err(io_failure()),
        }

        let mut directories = vec![version_relative.clone()];
        let mut files = Vec::with_capacity(package.payload().len());
        let mut file_claims = Vec::with_capacity(package.payload().len());
        for source in package.payload() {
            let parts: Vec<&str> = source.relative().split('/').collect();
            let mut prefix = version_relative.clone();
            for part in &parts[..parts.len() - 1] {
                prefix.push('/');
                prefix.push_str(part);
                if !directories.contains(&prefix) {
                    directories.push(prefix.clone());
                }
            }
            let relative = format!("{version_relative}/{}", source.relative());
            files.push(relative.clone());
            file_claims.push(JournalClaim::file(
                relative,
                source.sha256(),
                source.size(),
                true,
            ));
        }
        // The signed carrier artifact is installed once, in place, inside the
        // immutable version directory. The engine never copies or walks its
        // bytes: the platform boundary enumerates the exact entries (journalled
        // before the first mutation) and installs them with the metadata the
        // platform signature covers intact.
        let carrier_name = package_carrier_name(paths.target());
        let carrier_relative = format!("{version_relative}/{carrier_name}");
        let carrier_source = PackageSource::open(&package.package().path().join(carrier_name))
            .map_err(|_| invalid_package())?;
        let carrier_entries = carrier_entries_for(platform, &carrier_source, paths.target())?;
        let mut carrier_claims = Vec::with_capacity(carrier_entries.len());
        for entry in &carrier_entries {
            let relative = carrier_entry_path(&carrier_relative, entry);
            carrier_claims.push(match entry.kind() {
                OwnedKind::File => JournalClaim::file(
                    relative,
                    entry.sha256().ok_or_else(invalid_package)?,
                    entry.size(),
                    true,
                ),
                OwnedKind::Directory => JournalClaim::directory(relative),
                OwnedKind::Symlink => JournalClaim::link(
                    relative,
                    entry.sha256().ok_or_else(invalid_package)?,
                    entry.size(),
                ),
            });
        }
        directories.sort_by(|left, right| {
            path_depth(left)
                .cmp(&path_depth(right))
                .then_with(|| left.cmp(right))
        });
        let inventory_relative = format!("{version_relative}/{PACKAGE_INVENTORY_FILE}");
        let sealed_relative = format!("{version_relative}/{SEALED_INVENTORY_FILE}");
        // Retain the authenticated carrier reference and API metadata inside the
        // immutable version itself, so a later rollback never needs the original
        // download and never has to copy the signed artifact.
        let retained = RetainedInstall::from_package(paths, &version_relative, package)?;
        let retained = retained.to_bytes()?;
        let retained_relative = format!("{version_relative}/{RETAINED_FILE_NAME}");

        let manifest = manifest_for(
            paths,
            &version_directory.join(executable.relative()),
            &descriptor.extension_id,
        )?;

        // Retained inputs: the last committed receipt and the live registration
        // must still describe the installation we are about to upgrade.
        let prior = match Receipt::load(platform, paths)? {
            ReceiptState::None => None,
            ReceiptState::Committed(receipt) => Some(*receipt),
            ReceiptState::Unreadable => return Err(recovery_required()),
            ReceiptState::Unsupported => return Err(unsupported_state()),
        };
        let live = platform.read_registration(paths)?;
        let mut old_manifest = None;
        let mut preimage = None;
        if let Some(live) = live.as_ref() {
            // No receipt means we never installed here; a receipt must still
            // claim the live bytes, the host name, the owned scope and the
            // listed file claim. Read-only.
            let prior = prior.as_ref().ok_or_else(foreign_state)?;
            claim_owned_registration(platform, paths, &prior.ownership)?;
            let snapshot = ManifestSnapshot::new(live.manifest().to_vec())?;
            if snapshot.sha256() != prior.manifest_sha256 {
                return Err(foreign_state());
            }
            preimage = Some(registration_preimage(live, snapshot.sha256())?);
            old_manifest = Some(snapshot);
        }
        if let Some(prior) = prior.as_ref() {
            // Every path the last receipt owns must still verify before this
            // transaction carries it forward.
            claim_owned_entries(paths, &prior.ownership)?;
        }

        // Release ordering and policy. An installed release is never replaced by
        // an older one here: an explicit rollback is the only downgrade path and
        // it has its own operation. The incoming policy is then composed against
        // the policy the committed installation actually enforces, so a
        // correctly signed but stale package cannot relax a recorded policy.
        let release_policy = match prior.as_ref() {
            Some(prior) => {
                if descriptor.parsed_version()? < parse_version(&prior.version)? {
                    return Err(InstallFailure::new(FailureCode::PolicyDenied));
                }
                // The operand is installer state in an editable receipt: it only
                // counts once the installed release's own signed carrier (and, for
                // a rollback-produced receipt, the replaced release's) has been
                // authenticated and shown to bound it. A prior that cannot be
                // authenticated is `RecoveryRequired`, never composed against.
                authenticate_enforced_policy(platform, paths, prior)?;
                authorize_package_policy(package, &prior.release_policy, Operation::Install)?
            }
            None => descriptor.policy.clone(),
        };

        let policy = PolicyCarrierRefs::new(
            Digest::of(&canonical_json(descriptor)?),
            Digest::of(package.proof().carrier()),
            package.signer(),
            package.assessment(),
            release_policy.epoch,
        )?;

        let mut created: Vec<JournalClaim> =
            directories.iter().map(JournalClaim::directory).collect();
        created.extend(file_claims);
        created.push(JournalClaim::file(
            &inventory_relative,
            payload_id,
            inventory.len() as u64,
            true,
        ));
        if let Some(sealed) = sealed.as_ref() {
            created.push(JournalClaim::file(
                &sealed_relative,
                Digest::of(sealed),
                sealed.len() as u64,
                true,
            ));
        }
        created.push(JournalClaim::file(
            &retained_relative,
            Digest::of(&retained),
            retained.len() as u64,
            true,
        ));
        created.extend(carrier_claims);

        let journal = Journal {
            schema: JOURNAL_SCHEMA_VERSION,
            transaction_id: transaction_id(
                Operation::Install,
                paths.target(),
                paths.channel(),
                &descriptor.version,
                payload_id,
                manifest.sha256(),
                old_manifest.as_ref().map(ManifestSnapshot::sha256),
            ),
            operation: Operation::Install,
            phase: Phase::Prepared,
            target: paths.target(),
            channel: paths.channel(),
            root: root_rendering(paths)?,
            version: descriptor.version.clone(),
            payload_id,
            old_version: prior.as_ref().map(|receipt| receipt.version.clone()),
            old_payload_id: prior.as_ref().map(|receipt| receipt.payload_id),
            old_carrier_sha256: prior.as_ref().map(|receipt| receipt.policy.carrier_sha256),
            manifest,
            old_manifest,
            preimage,
            policy,
            release_policy,
            created,
        };
        journal.validate(paths)?;

        Ok(Self {
            journal,
            prior,
            directories,
            files,
            inventory_relative,
            inventory,
            sealed_relative,
            sealed,
            retained_relative,
            retained,
            carrier_relative,
            version_relative,
            host_relative: executable.relative().to_owned(),
        })
    }
}

/// The transaction body, after the Prepared journal is durable.
fn run(
    platform: &impl Platform,
    paths: &InstallPaths,
    plan: &Plan,
    package: &AuthenticatedPackage,
) -> InstallResult<()> {
    stage_payload(platform, paths, plan, package)?;
    // Every staging write completed and read back: no file may have been left as
    // a short prefix any more.
    let journal = plan
        .journal
        .with_completed_claims()
        .with_phase(Phase::Staged);
    journal.write(platform, paths)?;

    // The installed host, not just the package copy, must be launchable and
    // assessable by the OS before the manifest may select it.
    verify_installed_host(
        platform,
        paths,
        &plan.version_relative,
        &plan.host_relative,
        package.descriptor(),
        &journal.release_policy,
    )?;

    // Recheck quiescence immediately before the activation mutation.
    require_quiescent(platform, paths)?;

    let registration = registration_from(&journal.manifest, journal.preimage.as_ref())?;
    // Publication is two separately journaled steps, manifest first. The Staged
    // journal is the durable intent for the manifest replacement.
    platform.publish_manifest(paths, &registration)?;
    journal
        .with_phase(Phase::ManifestActivated)
        .write(platform, paths)?;
    // Then the registry/manifest-registration publication. A crash between the
    // two leaves the new manifest with the old registry value; recover completes
    // forward from verified live state.
    platform.publish_registration(paths, &registration)?;
    journal
        .with_phase(Phase::RegistrationPublished)
        .write(platform, paths)?;

    // Read the registration back exactly and verify the staged payload before
    // committing anything.
    let live = platform.read_registration(paths)?.ok_or_else(io_failure)?;
    if live.manifest_digest() != journal.manifest.sha256() {
        return Err(io_failure());
    }
    verify_created_assets(paths, &journal)?;

    // Retention is bounded: only the active version and its immediately prior
    // verified version survive. Superseded versions are removed before the
    // commit point, so a failure here still restores the previous live release
    // and never leaves a committed receipt naming an unowned directory.
    if let Some(prior) = plan.prior.as_ref() {
        remove_superseded_versions(platform, paths, prior, &journal)?;
    }

    let receipt = build_receipt(paths, &journal, plan.prior.as_ref())?;
    // The receipt is the commit point.
    receipt.write(platform, paths)?;
    journal
        .with_phase(Phase::ReceiptCommitted)
        .write(platform, paths)?;
    journal
        .with_phase(Phase::CleanupPending)
        .write(platform, paths)?;
    // Transactional cleanup removes no payload: the previous version is retained
    // for repair, rollback and uninstall, so only the journal is released here.
    // Every asset removal rechecks quiescence first (`remove_created_assets`).
    Journal::clear(platform, paths)
}

/// Prove the **installed** host is launchable and OS-assessed before activation.
///
/// Two independent facts, both checked on the installed object: the executable
/// carries the owner-only launch permission the authenticated inventory recorded,
/// and the OS assesses the installed host code (and, on macOS, its bundle and
/// nested code) as acceptable for a publisher the effective policy lists. A
/// denial or an unavailable assessment is returned unchanged and nothing has been
/// activated. Install, rollback, recovery **and repair** call this before they
/// publish a registration or report success.
pub(crate) fn verify_installed_host(
    platform: &impl Platform,
    paths: &InstallPaths,
    version_relative: &str,
    executable_relative: &str,
    descriptor: &ReleaseDescriptor,
    policy: &ReleasePolicy,
) -> InstallResult<()> {
    let relative = format!("{version_relative}/{executable_relative}");
    let handle = ValidatedPath::new(paths, Path::new(&relative), PathKind::File)?;
    platform.check_launch_permissions(&handle, true)?;
    let anchor = ReleaseDescriptor {
        policy: policy.clone(),
        ..descriptor.clone()
    };
    assess_host(
        platform,
        &paths.root().join(version_relative),
        executable_relative,
        &anchor,
        &policy.publishers,
    )
}

/// Stage every authenticated payload file into the immutable version directory.
///
/// The directory is on the destination volume (inside the fixed root) and is
/// unreachable until the manifest names it, which is what makes the manifest
/// replacement the activation point. Each source is re-authenticated
/// immediately before its bytes are read, so a package that changed after
/// authentication is never staged.
fn stage_payload(
    platform: &impl Platform,
    paths: &InstallPaths,
    plan: &Plan,
    package: &AuthenticatedPackage,
) -> InstallResult<()> {
    for directory in &plan.directories {
        let target = ValidatedPath::new(paths, Path::new(directory), PathKind::NewDirectory)?;
        platform.create_directory(&target)?;
    }
    for (source, relative) in package.payload().iter().zip(plan.files.iter()) {
        // One handle, bounded by the authenticated size: the identity check and
        // the read cannot be separated by a swap or a growth.
        let bytes = source.read_verified()?;
        let target = ValidatedPath::new(paths, Path::new(relative), PathKind::NewFile)?;
        platform.write_durable(&target, &bytes)?;
        // The authenticated inventory's `executable` bit crosses the platform
        // boundary here: applied and verified through the written file's own
        // handle, flushed, before anything can select it.
        let written = target.adopt(PathKind::File)?;
        platform.apply_launch_permissions(&written, source.executable())?;
    }
    let target = ValidatedPath::new(
        paths,
        Path::new(&plan.inventory_relative),
        PathKind::NewFile,
    )?;
    platform.write_durable(&target, &plan.inventory)?;
    if let Some(sealed) = plan.sealed.as_ref() {
        let target =
            ValidatedPath::new(paths, Path::new(&plan.sealed_relative), PathKind::NewFile)?;
        platform.write_durable(&target, sealed)?;
    }
    let target = ValidatedPath::new(paths, Path::new(&plan.retained_relative), PathKind::NewFile)?;
    platform.write_durable(&target, &plan.retained)?;
    // Install the signed carrier artifact at its final in-place path. The
    // platform preserves the metadata the OS signature covers; the engine never
    // copies or walks those bytes itself.
    let carrier_source = PackageSource::open(
        &package
            .package()
            .path()
            .join(package_carrier_name(paths.target())),
    )
    .map_err(|_| invalid_package())?;
    let carrier_kind = match paths.target() {
        crate::model::Target::WindowsX64 => PathKind::NewFile,
        crate::model::Target::MacosArm64 => PathKind::NewDirectory,
    };
    let target = ValidatedPath::new(paths, Path::new(&plan.carrier_relative), carrier_kind)?;
    platform.install_carrier(&carrier_source, &target)?;
    Ok(())
}

/// Validate the platform-reported entries of one signed carrier artifact.
///
/// The root entry must be the artifact itself, the set must be bounded, and the
/// paths must not alias each other case-insensitively (the same rule every owned
/// path follows).
fn carrier_entries_for(
    platform: &impl Platform,
    source: &PackageSource,
    target: crate::model::Target,
) -> InstallResult<Vec<CarrierEntry>> {
    let entries = platform.carrier_entries(source)?;
    let expected_root = match target {
        crate::model::Target::WindowsX64 => OwnedKind::File,
        crate::model::Target::MacosArm64 => OwnedKind::Directory,
    };
    let root = entries.first().ok_or_else(invalid_package)?;
    if !root.relative().is_empty() || root.kind() != expected_root {
        return Err(invalid_package());
    }
    if source.kind() == crate::platform::PackageKind::File && entries.len() != 1 {
        return Err(invalid_package());
    }
    if entries.len() > MAX_CARRIER_ENTRIES {
        return Err(invalid_package());
    }
    let mut total = 0u64;
    let mut seen = std::collections::HashSet::with_capacity(entries.len());
    for entry in &entries {
        if !seen.insert(entry.relative().to_lowercase()) {
            return Err(unsafe_path());
        }
        total = total
            .checked_add(entry.size())
            .ok_or_else(invalid_package)?;
        if total > MAX_CARRIER_BYTES as u64 {
            return Err(invalid_package());
        }
    }
    Ok(entries)
}

/// The full install-root path of one carrier entry.
fn carrier_entry_path(root_relative: &str, entry: &CarrierEntry) -> String {
    if entry.relative().is_empty() {
        root_relative.to_owned()
    } else {
        format!("{root_relative}/{}", entry.relative())
    }
}

/// Restore the state the journal recorded, then remove only created assets.
fn restore_previous(
    platform: &impl Platform,
    paths: &InstallPaths,
    journal: &Journal,
) -> InstallResult<()> {
    let live = platform.read_registration(paths)?;
    let live_digest = live.as_ref().map(Registration::manifest_digest);

    // Never claim a restoration, republish a registration or clear recovery
    // intent for a previous release that cannot be shown to be whole: a live old
    // manifest is no proof that its executable, resources or inventory survive.
    if journal.old_manifest.is_some() {
        let prior = previous_release(platform, paths, journal)?;
        if !release_is_usable(platform, paths, &prior, None) {
            return Err(recovery_required());
        }
    }

    match (journal.old_manifest.as_ref(), live_digest) {
        (Some(old), found) if found == Some(old.sha256()) => {
            // The previous registration is already live.
        }
        (Some(old), _) => {
            if let Some(live) = live.as_ref()
                && (live.manifest_digest() != journal.manifest.sha256()
                    || !registration_is_owned(paths, live))
            {
                return Err(foreign_state());
            }
            // Re-publish the exact journaled bytes through the same two ordered
            // platform calls the activation used. The manifest file is restored
            // first, then the registry/manifest-registration publication (which
            // carries the journaled Windows preimage for a native adapter).
            let value = registration_from(old, journal.preimage.as_ref())?;
            platform.publish_manifest(paths, &value)?;
            platform.publish_registration(paths, &value)?;
        }
        (None, Some(found)) if found == journal.manifest.sha256() => {
            // First install: remove the registration this transaction created.
            platform.remove_registration(
                paths,
                &registration_from(&journal.manifest, journal.preimage.as_ref())?,
            )?;
        }
        (None, Some(_)) => return Err(foreign_state()),
        (None, None) => {}
    }

    remove_created_assets(platform, paths, journal)?;
    Journal::clear(platform, paths)
}

/// Remove versions that are neither the active one nor its retained prior.
///
/// Only receipts claims are removed; every entry is re-verified by content, so a
/// replaced or foreign file is never deleted. Run before the receipt commit: a
/// failure leaves the previous installation live and only removes versions that
/// are already superseded by it.
fn remove_superseded_versions(
    platform: &impl Platform,
    paths: &InstallPaths,
    prior: &Receipt,
    journal: &Journal,
) -> InstallResult<()> {
    let keep = [
        paths.version_dir_relative(&parse_version(&journal.version)?, journal.payload_id),
        paths.version_dir_relative(&parse_version(&prior.version)?, prior.payload_id),
    ];
    let mut claims = Vec::new();
    for claim in &prior.ownership.owned {
        if !claim.path.starts_with(&format!("{VERSIONS_DIR}/")) {
            continue;
        }
        if keep.iter().any(|directory| {
            claim.path == *directory || claim.path.starts_with(&format!("{directory}/"))
        }) {
            continue;
        }
        claims.push(claim.clone());
    }
    if claims.is_empty() {
        return Ok(());
    }
    require_quiescent(platform, paths)?;
    remove_claims(platform, paths, &claims, journal.manifest.sha256())
}

/// Remove a set of receipt claims, files first and directories deepest-first.
///
/// The live registration is re-read and compared with the activation this prune
/// belongs to immediately before **every** deletion. Pruning runs after the
/// quiescence wait, and that wait is exactly when a retained version could become
/// live again (for example a launcher that re-activates the old release): deleting
/// it then would remove the executable the browser is about to start. A changed
/// registration aborts the prune, keeping every version for a later pass.
fn remove_claims(
    platform: &impl Platform,
    paths: &InstallPaths,
    claims: &[OwnedClaim],
    expected_live: Digest,
) -> InstallResult<()> {
    let mut entries = Vec::with_capacity(claims.len());
    for claim in claims {
        if let Some(entry) = OwnedEntry::from_claim(paths, claim)? {
            entries.push(entry);
        }
    }
    let mut files: Vec<&OwnedEntry> = entries
        .iter()
        .filter(|entry| !entry.claim().kind.is_directory())
        .collect();
    files.sort_by(|left, right| left.claim().path.cmp(&right.claim().path));
    for entry in files {
        require_live_registration_unchanged(platform, paths, expected_live)?;
        platform.remove_owned(entry)?;
    }
    let mut directories: Vec<&OwnedEntry> = entries
        .iter()
        .filter(|entry| entry.claim().kind.is_directory())
        .collect();
    directories.sort_by(|left, right| {
        path_depth(&right.claim().path)
            .cmp(&path_depth(&left.claim().path))
            .then_with(|| right.claim().path.cmp(&left.claim().path))
    });
    for entry in directories {
        require_live_registration_unchanged(platform, paths, expected_live)?;
        platform.remove_owned(entry)?;
    }
    Ok(())
}

/// Abort a prune unless the live registration still names `expected_live`.
fn require_live_registration_unchanged(
    platform: &impl Platform,
    paths: &InstallPaths,
    expected_live: Digest,
) -> InstallResult<()> {
    match platform.read_registration(paths)? {
        Some(live)
            if registration_is_owned(paths, &live) && live.manifest_digest() == expected_live =>
        {
            Ok(())
        }
        // The activation this prune belongs to is no longer live: keep the
        // version and report the deletion as deferred rather than guessing.
        _ => Err(InstallFailure::new(FailureCode::CleanupPending)),
    }
}

/// Remove every path this transaction created, after rechecking quiescence.
///
/// Files go first and directories deepest-first, so each directory is empty when
/// its own removal is attempted. A file claim the journal marked incomplete may
/// be a short prefix of this transaction's own staging write; it is removable
/// without a digest match, but only inside this transaction's own version
/// directory and only when it is genuinely shorter than the recorded size.
fn remove_created_assets(
    platform: &impl Platform,
    paths: &InstallPaths,
    journal: &Journal,
) -> InstallResult<()> {
    if journal.operation == Operation::Rollback {
        // A rollback creates nothing. Its claim set describes the retained target
        // version, which a failed or interrupted activation restores and never
        // deletes: the rollback target must survive a rejected rollback.
        return Ok(());
    }
    require_quiescent(platform, paths)?;
    let own_prefix = format!(
        "{}/",
        paths.version_dir_relative(&parse_version(&journal.version)?, journal.payload_id)
    );

    let mut entries = Vec::with_capacity(journal.created.len());
    for claim in &journal.created {
        let entry = if claim.incomplete {
            if claim.kind != OwnedKind::File || !claim.path.starts_with(&own_prefix) {
                // Only this transaction's own files may be removed without a
                // digest match.
                return Err(foreign_state());
            }
            let size = claim.size.ok_or_else(foreign_state)?;
            OwnedEntry::incomplete_file(paths, &claim.as_owned(), size)?
        } else {
            OwnedEntry::from_claim(paths, &claim.as_owned())?
        };
        if let Some(entry) = entry {
            entries.push(entry);
        }
    }

    let mut files: Vec<&OwnedEntry> = entries
        .iter()
        .filter(|entry| !entry.claim().kind.is_directory())
        .collect();
    files.sort_by(|left, right| left.claim().path.cmp(&right.claim().path));
    for entry in files {
        platform.remove_owned(entry)?;
    }

    let mut directories: Vec<&OwnedEntry> = entries
        .iter()
        .filter(|entry| entry.claim().kind.is_directory())
        .collect();
    directories.sort_by(|left, right| {
        path_depth(&right.claim().path)
            .cmp(&path_depth(&left.claim().path))
            .then_with(|| right.claim().path.cmp(&left.claim().path))
    });
    for entry in directories {
        platform.remove_owned(entry)?;
    }
    Ok(())
}

/// Finish an activation whose staged payload and live registration verify.
///
/// The caller already verified the created inventory exactly once.
fn complete_forward(
    platform: &impl Platform,
    paths: &InstallPaths,
    journal: &Journal,
) -> InstallResult<InstallReport> {
    let registration = registration_from(&journal.manifest, journal.preimage.as_ref())?;
    let live = platform.read_registration(paths)?;
    if live.as_ref().map(Registration::manifest_digest) != Some(journal.manifest.sha256()) {
        // Only republish the manifest when it is not already the live one.
        platform.publish_manifest(paths, &registration)?;
    }
    // The registry half is always republished idempotently so a crash between
    // the two publication calls is healed.
    platform.publish_registration(paths, &registration)?;
    let live = platform.read_registration(paths)?.ok_or_else(io_failure)?;
    if live.manifest_digest() != journal.manifest.sha256() {
        return Err(io_failure());
    }

    // Retention: the normal path prunes superseded versions before the receipt
    // commit. A crash between activation and that prune leaves the old receipt
    // still claiming them, and the new receipt is about to drop those claims. Run
    // the same guarded prune pass first, from the receipt being replaced, so the
    // committed state still names exactly one prior version and no directory is
    // orphaned without an owner.
    if let ReceiptState::Committed(existing) = Receipt::load(platform, paths)?
        && existing.transaction_id != journal.transaction_id
    {
        remove_superseded_versions(platform, paths, &existing, journal)?;
    }
    ensure_receipt(platform, paths, journal)?;
    journal
        .with_phase(Phase::ReceiptCommitted)
        .write(platform, paths)?;
    journal
        .with_phase(Phase::CleanupPending)
        .write(platform, paths)?;
    Journal::clear(platform, paths)?;

    Ok(InstallReport {
        operation: journal.operation,
        state: STATE_RECOVERED_ACTIVE.to_owned(),
        prior_usable: true,
        active_payload: Some(journal.payload_id),
        failure: None,
        warnings: Vec::new(),
    })
}

/// The report for a layout with no pending transaction.
fn clean_report(platform: &impl Platform, paths: &InstallPaths) -> InstallResult<InstallReport> {
    let receipt = match Receipt::load(platform, paths)? {
        ReceiptState::None => None,
        ReceiptState::Committed(receipt) => Some(*receipt),
        // No journal is available to reconstruct what was installed.
        ReceiptState::Unreadable => return Err(recovery_required()),
        ReceiptState::Unsupported => return Err(unsupported_state()),
    };
    let live = platform.read_registration(paths)?;
    let (state, prior_usable, active_payload) = match (receipt, live) {
        (Some(receipt), Some(live)) => {
            if !registration_is_owned(paths, &live) {
                return Err(foreign_state());
            }
            if live.manifest_digest() != receipt.manifest_sha256 {
                // Our receipt, but another manifest is live: ambiguous.
                return Err(recovery_required());
            }
            // "Clean" and "usable" are claims about the installed tree, not about
            // the receipt: a deleted host or missing dependency is damage.
            if !release_is_usable(platform, paths, &receipt, Some(&live)) {
                return Ok(InstallReport {
                    operation: Operation::Install,
                    state: STATE_DAMAGED.to_owned(),
                    prior_usable: false,
                    active_payload: Some(receipt.payload_id),
                    failure: Some(FailureCode::RepairPackageRequired),
                    warnings: Vec::new(),
                });
            }
            (STATE_CLEAN, true, Some(receipt.payload_id))
        }
        // A receipt without a registration is a damaged install (Task 7 repair).
        (Some(_), None) => (STATE_CLEAN, false, None),
        (None, Some(live)) => {
            if !registration_is_owned(paths, &live) {
                return Err(foreign_state());
            }
            // An owned registration without a receipt is ambiguous state.
            return Err(recovery_required());
        }
        (None, None) => (STATE_CLEAN, false, None),
    };
    Ok(InstallReport {
        operation: Operation::Install,
        state: state.to_owned(),
        prior_usable,
        active_payload,
        failure: None,
        warnings: Vec::new(),
    })
}

/// Write the receipt for `journal` unless the matching one is already durable.
///
/// An uninterpretable receipt is quarantined and rebuilt from the pending
/// journal, which is authoritative for this transaction. (Atomic replacement
/// means this only arises from external corruption.)
fn ensure_receipt(
    platform: &impl Platform,
    paths: &InstallPaths,
    journal: &Journal,
) -> InstallResult<()> {
    let existing = match Receipt::load(platform, paths)? {
        ReceiptState::None => None,
        ReceiptState::Committed(receipt) => Some(*receipt),
        ReceiptState::Unreadable => {
            platform.quarantine_state(paths, StateDocument::Receipt)?;
            None
        }
        // A newer engine's receipt is never quarantined and rebuilt.
        ReceiptState::Unsupported => return Err(unsupported_state()),
    };
    if let Some(existing) = existing.as_ref()
        && existing.transaction_id == journal.transaction_id
    {
        if existing.payload_id != journal.payload_id
            || existing.manifest_sha256 != journal.manifest.sha256()
        {
            return Err(recovery_required());
        }
        return Ok(());
    }
    let receipt = build_receipt(paths, journal, existing.as_ref())?;
    receipt.write(platform, paths)
}

/// Build the committed receipt, carrying verified retained claims forward.
fn build_receipt(
    paths: &InstallPaths,
    journal: &Journal,
    prior: Option<&Receipt>,
) -> InstallResult<Receipt> {
    let mut owned: Vec<OwnedClaim> = Vec::new();
    if let Some(prior) = prior
        && prior.transaction_id != journal.transaction_id
    {
        // The retained previous version stays owned by this installation, so a
        // later uninstall can remove it with the same content checks. Retention
        // is bounded: exactly one prior version is carried forward, and every
        // superseded version claim is dropped here (the directories themselves
        // are removed during transactional cleanup).
        //
        // The version this transaction activates is kept too: after a rollback
        // the target was the *previous* receipt's claim set, and the rebuilt
        // receipt must keep owning its retained metadata and signed carrier
        // artifact, not only the payload files the journal happened to journal.
        let mut keep = Vec::new();
        if let (Some(version), Some(payload_id)) = (&journal.old_version, journal.old_payload_id) {
            keep.push(paths.version_dir_relative(&parse_version(version)?, payload_id));
        }
        keep.push(
            paths.version_dir_relative(&parse_version(&journal.version)?, journal.payload_id),
        );
        for entry in claim_owned_entries(paths, &prior.ownership)? {
            let claim = entry.claim();
            let in_versions = claim.path.starts_with(&format!("{VERSIONS_DIR}/"));
            let retained_version = keep.iter().any(|retained| {
                claim.path == *retained || claim.path.starts_with(&format!("{retained}/"))
            });
            if !in_versions || retained_version {
                owned.push(claim.clone());
            }
        }
    }
    owned.extend(journal.created.iter().map(JournalClaim::as_owned));
    owned.sort_by(|left, right| left.path.cmp(&right.path));
    owned.dedup_by(|left, right| left.path == right.path);

    let prior_install = match (&journal.old_version, journal.old_payload_id) {
        (Some(version), Some(payload_id)) => Some(PriorInstall {
            version: version.clone(),
            payload_id,
            manifest_sha256: journal.old_manifest.as_ref().map(ManifestSnapshot::sha256),
            carrier_sha256: journal.old_carrier_sha256.ok_or_else(recovery_required)?,
        }),
        (None, None) => None,
        _ => return Err(recovery_required()),
    };

    Ok(Receipt {
        schema: RECEIPT_SCHEMA_VERSION,
        transaction_id: journal.transaction_id.clone(),
        operation: journal.operation,
        target: journal.target,
        channel: journal.channel,
        root: journal.root.clone(),
        version: journal.version.clone(),
        payload_id: journal.payload_id,
        manifest_sha256: journal.manifest.sha256(),
        prior: prior_install,
        policy: journal.policy.clone(),
        release_policy: journal.release_policy.clone(),
        ownership: OwnershipRecord {
            schema: OWNERSHIP_SCHEMA_VERSION,
            target: journal.target,
            channel: journal.channel,
            root: journal.root.clone(),
            manifest_sha256: journal.manifest.sha256(),
            owned,
        },
    })
}

/// Verify every created path is still present with its recorded identity, and
/// that the recorded manifest selects one of them.
fn verify_created_assets(
    paths: &InstallPaths,
    journal: &Journal,
) -> InstallResult<Vec<OwnedEntry>> {
    let entries = claim_owned_entries(paths, &journal.ownership_record())?;
    if entries.len() != journal.created.len() {
        return Err(io_failure());
    }
    let manifest = parse_manifest(journal.manifest.bytes())?;
    let executable = fs::canonicalize(manifest.executable()?).map_err(|_| unsafe_path())?;
    let selected = journal.created.iter().any(|claim| {
        claim.kind == OwnedKind::File
            && fs::canonicalize(paths.root().join(&claim.path))
                .is_ok_and(|candidate| candidate == executable)
    });
    if !selected {
        return Err(invalid_package());
    }
    Ok(entries)
}

/// A [`Registration`] carrying the journaled Windows preimage, when there is one.
fn registration_from(
    manifest: &ManifestSnapshot,
    preimage: Option<&RegistrationPreimage>,
) -> InstallResult<Registration> {
    let registration = Registration::new(manifest.bytes().to_vec())?;
    match preimage.and_then(|preimage| preimage.windows_key.as_ref()) {
        Some(key) => {
            let windows = WindowsPreimage::new(
                key.clone(),
                preimage.and_then(|preimage| preimage.windows_value.clone()),
            )?;
            Ok(registration.with_windows_preimage(windows))
        }
        None => Ok(registration),
    }
}

fn registration_preimage(
    live: &Registration,
    manifest_sha256: Digest,
) -> InstallResult<RegistrationPreimage> {
    let preimage = RegistrationPreimage::new(manifest_sha256);
    match live.windows_preimage() {
        Some(windows) => {
            preimage.with_windows(windows.key(), windows.previous_value().map(str::to_owned))
        }
        None => Ok(preimage),
    }
}

/// Build the exact manifest bytes this layout must publish for `executable`.
///
/// This is the single definition of the manifest the installer publishes: an
/// install, a repair that has to restore a missing registration, and a rollback
/// that re-activates a retained version all publish exactly these bytes.
pub(crate) fn manifest_for(
    paths: &InstallPaths,
    executable: &Path,
    extension_id: &str,
) -> InstallResult<ManifestSnapshot> {
    let manifest = NativeManifest {
        name: paths.registration_name().to_owned(),
        description: MANIFEST_DESCRIPTION.to_owned(),
        path: executable.to_str().ok_or_else(unsafe_path)?.to_owned(),
        kind: "stdio".to_owned(),
        allowed_origins: vec![extension_origin(extension_id)?],
    };
    let bytes = canonical_json(&manifest)?;
    // Never journal a document that publication would refuse.
    let parsed = parse_manifest(&bytes)?;
    if parsed.name != paths.registration_name() {
        return Err(origin_rejected());
    }
    parsed.executable()?;
    ManifestSnapshot::new(bytes)
}

fn root_rendering(paths: &InstallPaths) -> InstallResult<String> {
    paths
        .root()
        .to_str()
        .map(str::to_owned)
        .ok_or_else(unsafe_path)
}

fn path_depth(path: &str) -> usize {
    path.matches('/').count()
}

fn invalid_package() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidPackage)
}

fn unsafe_path() -> InstallFailure {
    InstallFailure::new(FailureCode::UnsafePath)
}

fn io_failure() -> InstallFailure {
    InstallFailure::new(FailureCode::Io)
}

fn foreign_state() -> InstallFailure {
    InstallFailure::new(FailureCode::ForeignState)
}

fn unsupported_state() -> InstallFailure {
    InstallFailure::new(FailureCode::UnsupportedState)
}

fn recovery_required() -> InstallFailure {
    InstallFailure::new(FailureCode::RecoveryRequired)
}

fn origin_rejected() -> InstallFailure {
    InstallFailure::new(FailureCode::OriginRejected)
}

fn no_rollback() -> InstallFailure {
    InstallFailure::new(FailureCode::NoRollback)
}
