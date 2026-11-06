//! Journaled staging, activation and crash recovery.
//!
//! [`install`] runs one transaction in an explicit phase machine:
//!
//! ```text
//! Prepared  -> durable intent, before the first staging mutation
//! Staged    -> every authenticated payload file and the inventory are durable
//! ManifestActivated -> durable activation intent, then the platform replaces
//!                       the one manifest that selects the executable
//! RegistrationPublished -> the publication call returned (fixed HKCU on Windows)
//! ReceiptCommitted -> the receipt is durable; the active payload is verified
//! CleanupPending -> transactional cleanup, then the journal is released
//! ```
//!
//! Nothing in the immutable version directory is reachable by Chrome before the
//! manifest replacement, so a staged-but-unactivated payload cannot be executed
//! and an interrupted transaction never exposes a partial host. Every write goes
//! through the injected [`Platform`] boundary, which writes all bytes, flushes,
//! reads them back and flushes the parent directory, so the only durable states
//! are "step k did not happen" and "step k happened".
//!
//! [`recover`] never trusts a phase marker. It compares the journal with the
//! verified live registration and the verified staged inventory, then either
//! completes the authenticated staged activation or restores the journaled
//! preimage. A live registration that is not ours, or live bytes that match
//! neither recorded manifest, fails closed with guidance instead of guessing.
//!
//! Retained inputs: the previous version's files and the previous manifest bytes
//! are verified (and journaled) before the first mutation, and the previous
//! version is never removed by this task - an upgrade retains it for repair,
//! rollback and uninstall.

use std::fs;
use std::path::Path;

use crate::inventory::{PACKAGE_INVENTORY_FILE, write_inventory};
use crate::journal::{
    JOURNAL_SCHEMA_VERSION, Journal, ManifestSnapshot, Phase, PolicyCarrierRefs, PriorInstall,
    RECEIPT_SCHEMA_VERSION, Receipt, RegistrationPreimage, transaction_id,
};
use crate::model::{
    Digest, FailureCode, InstallFailure, InstallReport, InstallResult, InventoryEntry, Operation,
    canonical_json, extension_origin,
};
use crate::paths::{InstallPaths, VERSIONS_DIR};
use crate::platform::{
    AuthenticatedPackage, AuthenticatedSource, NativeManifest, OWNERSHIP_SCHEMA_VERSION,
    OwnedClaim, OwnedEntry, OwnedKind, OwnershipRecord, PathKind, Platform, Registration,
    ValidatedPath, WindowsPreimage, claim_owned_entries, claim_owned_registration, parse_manifest,
    registration_is_owned, require_quiescent,
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
    payload_id: Digest,
}

/// Install one authenticated package.
///
/// The previous installation is preserved: the payload is staged into its own
/// immutable version directory, the manifest is replaced atomically, and the
/// receipt is committed only after the registration and the staged inventory are
/// read back and verified. A failure restores the previous state; only a
/// first-install failure removes assets, and only the ones this transaction
/// created.
pub fn install(
    platform: &impl Platform,
    paths: &InstallPaths,
    package: AuthenticatedPackage,
) -> InstallResult<InstallReport> {
    let _lock = platform.lock(paths)?;

    // Never proceed on top of an interrupted transaction: reconcile first.
    if Journal::load(paths)?.is_some() {
        return Err(InstallFailure::new(FailureCode::RecoveryRequired));
    }

    let plan = Plan::prepare(platform, paths, &package)?;
    // Durable intent before the first staging mutation.
    plan.journal.write(platform, paths)?;

    match run(platform, paths, &plan, &package) {
        Ok(()) => Ok(InstallReport {
            operation: Operation::Install,
            state: STATE_INSTALLED.to_owned(),
            prior_usable: true,
            active_payload: Some(plan.payload_id),
            failure: None,
        }),
        Err(failure) => {
            // Report the primary failure unchanged, but first put the previous
            // state back so the user is never left without a complete
            // registration. If that also fails, the journal stays pending and the
            // next install asks for recovery instead of stacking another
            // transaction on top of this one.
            let _ = abort_transaction(platform, paths);
            let prior_usable = previous_is_verified(platform, paths, &plan.journal);
            Err(failure.with_prior_usable(prior_usable))
        }
    }
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
/// * A live, complete activation is finished (publication, receipt, cleanup).
/// * A live previous state is restored and the transaction's own assets removed.
/// * A live registration this installer does not own is `ForeignState`; live
///   bytes that match neither recorded manifest are `RecoveryRequired`. Neither
///   mutates anything.
pub fn recover(platform: &impl Platform, paths: &InstallPaths) -> InstallResult<InstallReport> {
    let _lock = platform.lock(paths)?;

    let Some(journal) = Journal::load(paths)? else {
        return clean_report(platform, paths);
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

    // A complete activation is finished, whichever phase was recorded.
    if live_digest == Some(new_digest) && created_assets_verified(paths, &journal) {
        return complete_forward(platform, paths, &journal);
    }
    // Anything that is neither the recorded previous state nor the recorded new
    // state is ambiguous.
    if live_digest.is_some() && live_digest != old_digest && live_digest != Some(new_digest) {
        return Err(InstallFailure::new(FailureCode::RecoveryRequired));
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
        prior_usable: journal.old_manifest.is_some(),
        active_payload: journal.old_payload_id,
        failure: None,
    })
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
        let version_directory = paths.version_dir(&version, payload_id);
        if fs::symlink_metadata(&version_directory).is_ok() {
            return Err(foreign_state());
        }
        let version_relative = format!("{VERSIONS_DIR}/{version}-{}", payload_id.to_hex());

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
            file_claims.push(OwnedClaim::file(relative, source.sha256()));
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
        let prior = Receipt::load(paths)?;
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

        let mut created: Vec<OwnedClaim> = directories
            .iter()
            .map(|directory| OwnedClaim::directory(directory.clone()))
            .collect();
        created.extend(file_claims);
        created.push(OwnedClaim::file(&inventory_relative, payload_id));

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
            payload_id,
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
    plan.journal
        .with_phase(Phase::Staged)
        .write(platform, paths)?;

    // Recheck quiescence immediately before the activation mutation.
    require_quiescent(platform, paths)?;

    // Durable activation intent, then the single atomic replacement of the
    // manifest that selects the executable (and the fixed HKCU publication on
    // Windows).
    plan.journal
        .with_phase(Phase::ManifestActivated)
        .write(platform, paths)?;
    let registration = registration_from(&plan.journal.manifest, plan.journal.preimage.as_ref())?;
    platform.publish_registration(paths, &registration)?;
    plan.journal
        .with_phase(Phase::RegistrationPublished)
        .write(platform, paths)?;

    // Read the registration back exactly and verify the staged payload before
    // committing anything.
    let live = platform.read_registration(paths)?.ok_or_else(io_failure)?;
    if live.manifest_digest() != plan.journal.manifest.sha256() {
        return Err(io_failure());
    }
    verify_created_assets(paths, &plan.journal)?;

    let receipt = build_receipt(paths, &plan.journal, plan.prior.as_ref())?;
    receipt.write(platform, paths)?;
    plan.journal
        .with_phase(Phase::ReceiptCommitted)
        .write(platform, paths)?;
    plan.journal
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
/// unreachable until the manifest names it, which is what makes the single
/// manifest replacement the activation point. Each source is re-authenticated
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
            // Re-publish the exact journaled bytes; publication applies the same
            // owned-preimage rule as the activation did, so a journal that claims
            // a manifest this layout would refuse cannot restore anything.
            platform
                .publish_registration(paths, &registration_from(old, journal.preimage.as_ref())?)?;
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
    Ok(fs::symlink_metadata(executable).is_ok_and(|metadata| metadata.is_file()))
}

/// Remove every path this transaction created, after rechecking quiescence.
///
/// Files go first and directories deepest-first, so each directory is empty when
/// its own removal is attempted. `claim_owned_entries` drops claims whose path is
/// already absent (idempotent) and refuses a present path whose content changed.
fn remove_created_assets(
    platform: &impl Platform,
    paths: &InstallPaths,
    journal: &Journal,
) -> InstallResult<()> {
    require_quiescent(platform, paths)?;
    let entries = claim_owned_entries(paths, &journal.ownership_record())?;

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

/// Abort the pending transaction recorded on disk, if any.
fn abort_transaction(platform: &impl Platform, paths: &InstallPaths) -> InstallResult<()> {
    let Some(journal) = Journal::load(paths)? else {
        return Ok(());
    };
    restore_previous(platform, paths, &journal)
}

/// Finish an activation whose staged payload and live registration verify.
fn complete_forward(
    platform: &impl Platform,
    paths: &InstallPaths,
    journal: &Journal,
) -> InstallResult<InstallReport> {
    verify_created_assets(paths, journal)?;
    platform.publish_registration(
        paths,
        &registration_from(&journal.manifest, journal.preimage.as_ref())?,
    )?;
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
    let receipt = Receipt::load(paths)?;
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
fn ensure_receipt(
    platform: &impl Platform,
    paths: &InstallPaths,
    journal: &Journal,
) -> InstallResult<()> {
    let existing = Receipt::load(paths)?;
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
    owned.extend(journal.created.iter().cloned());
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

/// Whether every path the journal created is still present and verified.
fn created_assets_verified(paths: &InstallPaths, journal: &Journal) -> bool {
    verify_created_assets(paths, journal).is_ok()
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
