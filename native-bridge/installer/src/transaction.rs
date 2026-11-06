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
//! [`recover`] never trusts a phase marker. It compares the journal with the
//! verified live registration and the verified staged inventory, then either
//! completes the authenticated staged activation or restores the journaled
//! preimage. A live registration that is not ours, or live bytes that match
//! neither recorded manifest, fails closed with guidance instead of guessing. A
//! journal that cannot be interpreted is reconciled against verified live state
//! and quarantined, so a torn write cannot block every later run.
//!
//! Retained inputs: the previous version's files and the previous manifest bytes
//! are verified (and journaled) before the first mutation, and the previous
//! version is never removed by this task - an upgrade retains it for repair,
//! rollback and uninstall.

use std::fs;
use std::io;
use std::path::Path;

use crate::inventory::{PACKAGE_INVENTORY_FILE, write_inventory};
use crate::journal::{
    JOURNAL_SCHEMA_VERSION, Journal, JournalClaim, JournalState, ManifestSnapshot, Phase,
    PolicyCarrierRefs, PriorInstall, RECEIPT_SCHEMA_VERSION, Receipt, ReceiptState,
    RegistrationPreimage, transaction_id,
};
use crate::model::{
    Digest, FailureCode, InstallFailure, InstallReport, InstallResult, InventoryEntry, Operation,
    canonical_json, extension_origin, parse_version,
};
use crate::paths::InstallPaths;
use crate::platform::{
    AuthenticatedPackage, AuthenticatedSource, NativeManifest, OWNERSHIP_SCHEMA_VERSION,
    OwnedClaim, OwnedEntry, OwnedKind, OwnershipRecord, PathKind, Platform, Registration,
    StateDocument, ValidatedPath, WindowsPreimage, claim_owned_entries, claim_owned_registration,
    parse_manifest, registration_is_owned, require_quiescent,
};

/// The report state of a completed install.
pub const STATE_INSTALLED: &str = "installed";
/// The report state of a layout with no pending transaction.
pub const STATE_CLEAN: &str = "clean";
/// Recovery completed the authenticated staged activation.
pub const STATE_RECOVERED_ACTIVE: &str = "recovered-active";
/// Recovery restored the last verified version.
pub const STATE_RECOVERED_PREVIOUS: &str = "recovered-previous";

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
    }

    let plan = Plan::prepare(platform, paths, &package)?;
    // Durable intent before the first staging mutation.
    plan.journal.write(platform, paths)?;

    match run(platform, paths, &plan, &package) {
        Ok(()) => Ok(installed_report(&plan.journal)),
        Err(failure) => settle_failed_install(platform, paths, &plan, failure),
    }
}

/// The success report for a transaction whose receipt is committed.
fn installed_report(journal: &Journal) -> InstallReport {
    InstallReport {
        operation: Operation::Install,
        state: STATE_INSTALLED.to_owned(),
        prior_usable: true,
        active_payload: Some(journal.payload_id),
        failure: None,
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
    plan: &Plan,
    failure: InstallFailure,
) -> InstallResult<InstallReport> {
    // The durable journal is authoritative for the phase that was reached; the
    // in-memory plan is the durable intent when the slot is unreadable or was
    // already released by a state write whose parent flush failed.
    let journal = match Journal::load(platform, paths) {
        Ok(JournalState::Pending(journal)) => *journal,
        _ => plan.journal.clone(),
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
                Ok(()) => Ok(installed_report(&journal)),
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
        ReceiptState::Unreadable => Err(failure.with_prior_usable(prior_usable)),
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
    live.manifest_digest() == old.sha256() && manifest_selects_a_file(old).unwrap_or(false)
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
        JournalState::Pending(journal) => *journal,
    };

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
    let committed = matches!(
        &receipt,
        ReceiptState::Committed(receipt) if receipt.transaction_id == journal.transaction_id
    );
    if committed || matches!(receipt, ReceiptState::Unreadable) {
        // The receipt is (or may be) the commit point: never restore.
        return if activation_verified {
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
    })
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
    platform.quarantine_state(paths, StateDocument::Journal)?;
    clean_report(platform, paths)
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

        // The installed inventory document must reproduce the authenticated
        // payload identity; otherwise the staged tree is not the released one.
        let entries: Vec<InventoryEntry> = package
            .payload()
            .iter()
            .map(|source| InventoryEntry {
                path: source.relative().to_owned(),
                size: source.size(),
                sha256: source.sha256(),
                executable: source.executable(),
            })
            .collect();
        let inventory = write_inventory(&entries)?;
        if Digest::of(&inventory) != payload_id {
            return Err(invalid_package());
        }

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
        directories.sort_by(|left, right| {
            path_depth(left)
                .cmp(&path_depth(right))
                .then_with(|| left.cmp(right))
        });
        let inventory_relative = format!("{version_relative}/{PACKAGE_INVENTORY_FILE}");

        let manifest = build_manifest(
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

        let policy = PolicyCarrierRefs::new(
            Digest::of(&canonical_json(descriptor)?),
            Digest::of(package.proof().carrier()),
            package.signer(),
            package.assessment(),
            descriptor.policy.epoch,
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
            manifest,
            old_manifest,
            preimage,
            policy,
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
        source.revalidate()?;
        let bytes = read_staged(source)?;
        let target = ValidatedPath::new(paths, Path::new(relative), PathKind::NewFile)?;
        platform.write_durable(&target, &bytes)?;
    }
    let target = ValidatedPath::new(
        paths,
        Path::new(&plan.inventory_relative),
        PathKind::NewFile,
    )?;
    platform.write_durable(&target, &plan.inventory)
}

/// Read authenticated source bytes and re-check the authenticated identity.
fn read_staged(source: &AuthenticatedSource) -> InstallResult<Vec<u8>> {
    let bytes = fs::read(source.path()).map_err(|_| io_failure())?;
    if bytes.len() as u64 != source.size() || Digest::of(&bytes) != source.sha256() {
        return Err(foreign_state());
    }
    Ok(bytes)
}

/// Restore the state the journal recorded, then remove only created assets.
fn restore_previous(
    platform: &impl Platform,
    paths: &InstallPaths,
    journal: &Journal,
) -> InstallResult<()> {
    let live = platform.read_registration(paths)?;
    let live_digest = live.as_ref().map(Registration::manifest_digest);

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
            if !manifest_selects_a_file(old)? {
                // The journaled preimage would name a missing executable: never
                // publish a registration that cannot work. The journal stays for
                // recovery guidance (repair needs a fresh signed package).
                return Err(recovery_required());
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

/// Whether the manifest selects an executable that is present as a regular file.
fn manifest_selects_a_file(manifest: &ManifestSnapshot) -> InstallResult<bool> {
    let parsed = parse_manifest(manifest.bytes())?;
    let executable = parsed.executable()?;
    match fs::symlink_metadata(executable) {
        Ok(metadata) => Ok(metadata.is_file()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        // A permission or IO failure is not "missing": it cannot prove the
        // preimage is unusable, but it also cannot prove it is usable.
        Err(_) => Err(io_failure()),
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
        .filter(|entry| entry.claim().kind == OwnedKind::File)
        .collect();
    files.sort_by(|left, right| left.claim().path.cmp(&right.claim().path));
    for entry in files {
        platform.remove_owned(entry)?;
    }

    let mut directories: Vec<&OwnedEntry> = entries
        .iter()
        .filter(|entry| entry.claim().kind == OwnedKind::Directory)
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
    })
}

/// The report for a layout with no pending transaction.
fn clean_report(platform: &impl Platform, paths: &InstallPaths) -> InstallResult<InstallReport> {
    let receipt = match Receipt::load(platform, paths)? {
        ReceiptState::None => None,
        ReceiptState::Committed(receipt) => Some(*receipt),
        // No journal is available to reconstruct what was installed.
        ReceiptState::Unreadable => return Err(recovery_required()),
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
        // later uninstall can remove it with the same content checks.
        for entry in claim_owned_entries(paths, &prior.ownership)? {
            owned.push(entry.claim().clone());
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
fn build_manifest(
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

fn recovery_required() -> InstallFailure {
    InstallFailure::new(FailureCode::RecoveryRequired)
}

fn origin_rejected() -> InstallFailure {
    InstallFailure::new(FailureCode::OriginRejected)
}
