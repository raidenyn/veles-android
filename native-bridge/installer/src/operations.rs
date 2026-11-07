//! Maintenance and removal operations (Task 7).
//!
//! [`run_operation`] is the single engine entry point the native frontends call:
//! `inspect`, `install`, `repair`, `rollback` and `uninstall`. Every operation
//! goes through the injected [`Platform`] boundary and the committed receipt and
//! journal documents; no operation reaches into a Chrome profile, a browser
//! database, the OS trust store or the network.
//!
//! # Operation rules
//!
//! * **Inspect** is read-only and lock-free. It reports bounded metadata only
//!   (state, the payload identity and a guidance code) and never a path, a
//!   publisher verdict or a local file listing.
//! * **Install** delegates to Task 6's journaled transaction. A same-version
//!   rerun repairs (the specification's "same-version reruns repair"); an older
//!   release is refused there rather than silently downgrading an installed one,
//!   and the incoming policy is composed against the persisted one.
//! * **Repair** restores missing or damaged managed files of the *installed*
//!   payload from an authenticated same-version package and restores the exact
//!   committed registration. A registration this installer does not own is
//!   reported (`ForeignState`) and never overwritten; repair never re-assesses
//!   trust of local content and never trusts the damaged tree.
//! * **Rollback** re-activates the one retained, verified prior version after
//!   authenticating the signed carrier artifact installed *in place* inside that
//!   immutable version directory, after composing its policy with the persisted
//!   one. Denials accumulate: a correctly signed stale package cannot erase a
//!   recorded denial, and its signer must be authorized under both policies. A
//!   supplied package may only select the recorded prior release; it is never
//!   the retained carrier authority.
//! * **Uninstall** removes only paths the committed receipt owns: the
//!   registration first (after ownership and quiescence validation), then files
//!   and finally the emptied directories, then the receipt. A partially removed
//!   installation is journaled, retried idempotently, and reported
//!   `CleanupPending` - never as a completed uninstall while an owned host or a
//!   deferred deletion remains. A terminal quiescence check runs after deletion
//!   and before success; unowned files are preserved and reported as warnings.
//!
//! Nothing here is trust: a receipt authorizes removal of the exact paths it
//! claims and nothing else, and every claim is re-normalized, scope-checked and
//! content-checked before it is touched.

use std::fs;
use std::io;
use std::path::Path;
use std::time::Duration;

use crate::inventory::{PACKAGE_INVENTORY_FILE, SEALED_INVENTORY_FILE};
use crate::journal::{
    JOURNAL_SCHEMA_VERSION, Journal, JournalClaim, JournalState, ManifestSnapshot, Phase,
    PolicyCarrierRefs, Receipt, ReceiptState,
};
use crate::model::{
    Digest, FailureCode, InstallFailure, InstallReport, InstallResult, Operation, WarningCode,
    canonical_json, select_host,
};
use crate::paths::{InstallPaths, JOURNAL_FILE, LOCK_FILE, RECEIPT_FILE, STAGING_DIR, STATE_DIR};
use crate::platform::{
    AuthenticatedPackage, AuthenticatedSource, OWNERSHIP_SCHEMA_VERSION, OwnedEntry, OwnedKind,
    OwnershipRecord, PathKind, Platform, QUIESCENCE_TIMEOUT, Registration, StateDocument,
    ValidatedPath, read_no_follow, registration_is_owned, require_quiescent_within,
};
use crate::policy::{authorize_package_policy, is_no_broader_than};
use crate::retained::{RETAINED_FILE_NAME, RetainedInstall};
use crate::transaction::{
    STATE_INSTALLED, activate_retained, bound_by_replaced_release, install, manifest_for, recover,
    release_is_usable, verify_installed_host,
};

/// Nothing of this product is installed.
pub const STATE_ABSENT: &str = "absent";
/// An installation exists but its managed state no longer verifies.
pub const STATE_DAMAGED: &str = "damaged";
/// The fixed registration is owned by another product.
pub const STATE_FOREIGN: &str = "foreign";
/// A repair restored the managed tree.
pub const STATE_REPAIRED: &str = "repaired";
/// The installation was removed.
pub const STATE_UNINSTALLED: &str = "uninstalled";
/// A pending transaction must be reconciled before another operation runs.
pub const STATE_RECOVERY_REQUIRED: &str = "recovery-required";

/// Run one installer operation.
///
/// `package` is required by install and repair, and ignored by inspect and
/// uninstall. Rollback does not require it: the authenticated carrier and API
/// metadata of the prior release are retained inside its immutable version
/// directory, and a supplied package may only select that already retained
/// prior release. A package never becomes the prior carrier authority.
pub fn run_operation(
    platform: &impl Platform,
    paths: &InstallPaths,
    operation: Operation,
    package: Option<AuthenticatedPackage>,
) -> InstallResult<InstallReport> {
    run_operation_within(platform, paths, operation, package, QUIESCENCE_TIMEOUT)
}

/// [`run_operation`] with an explicit, clamped quiescence bound.
///
/// The bound is clamped to [`QUIESCENCE_TIMEOUT`] by
/// [`require_quiescent_within`], so no caller can extend the installer's wait;
/// the explicit form exists so portable tests exercise the bounded wait without a
/// ten-second stall, exactly like Task 5's tests.
pub fn run_operation_within(
    platform: &impl Platform,
    paths: &InstallPaths,
    operation: Operation,
    package: Option<AuthenticatedPackage>,
    quiescence: Duration,
) -> InstallResult<InstallReport> {
    let result = dispatch(platform, paths, operation, package, quiescence);
    match result {
        // A failure the operation did not classify still owes the frontend the
        // truth about the prior installation: it is read live, never assumed.
        Err(failure) if !failure.prior_usable => {
            Err(failure.with_prior_usable(installation_is_usable(platform, paths)))
        }
        other => other,
    }
}

fn dispatch(
    platform: &impl Platform,
    paths: &InstallPaths,
    operation: Operation,
    package: Option<AuthenticatedPackage>,
    quiescence: Duration,
) -> InstallResult<InstallReport> {
    // A transaction an earlier process left pending is settled before the requested
    // operation runs, from the same authenticated evidence recovery always uses. This
    // is what makes "Retry" true: without it install, repair and rollback refuse the
    // pending journal and nothing else in the product would ever settle it.
    // Inspection stays read-only, and an interrupted removal is finished only by
    // uninstall itself, whose resumable semantics recovery deliberately does not own.
    if matches!(
        operation,
        Operation::Install | Operation::Repair | Operation::Rollback
    ) && !matches!(Journal::load(platform, paths)?, JournalState::None)
    {
        // Foreign, unsupported and ambiguous states are refused exactly as recovery
        // reports them; the requested operation does not run over them.
        recover(platform, paths)?;
    }
    match operation {
        Operation::Inspect => inspect(platform, paths),
        Operation::Install => {
            let package = package.ok_or_else(|| InstallFailure::new(FailureCode::InvalidInput))?;
            // "Same-version reruns repair": the package is the repair source for
            // the release that is installed. A different version is an ordinary
            // install, which itself refuses a downgrade.
            if same_release(platform, paths, &package)? {
                repair(platform, paths, &package, quiescence)
            } else {
                install(platform, paths, package)
            }
        }
        Operation::Repair => {
            let package =
                package.ok_or_else(|| InstallFailure::new(FailureCode::RepairPackageRequired))?;
            repair(platform, paths, &package, quiescence)
        }
        Operation::Rollback => activate_retained(platform, paths, package.as_ref(), quiescence),
        Operation::Uninstall => uninstall(platform, paths, quiescence),
    }
}

/// Whether `package` is exactly the release the committed receipt names.
fn same_release(
    platform: &impl Platform,
    paths: &InstallPaths,
    package: &AuthenticatedPackage,
) -> InstallResult<bool> {
    let descriptor = package.descriptor();
    let Ok(state) = Receipt::load(platform, paths) else {
        return Ok(false);
    };
    let ReceiptState::Committed(receipt) = state else {
        return Ok(false);
    };
    Ok(receipt.version == descriptor.version && receipt.payload_id == descriptor.payload_id)
}

/// Read-only inspection of the installed state.
///
/// It acquires no lock and mutates nothing: inspection must be safe on a machine
/// where another installer process is running and on a layout that does not exist
/// yet.
fn inspect(platform: &impl Platform, paths: &InstallPaths) -> InstallResult<InstallReport> {
    let journal = Journal::load(platform, paths)?;
    let receipt = match Receipt::load(platform, paths)? {
        ReceiptState::None => None,
        ReceiptState::Committed(receipt) => Some(*receipt),
        ReceiptState::Unreadable => {
            return Ok(report(
                Operation::Inspect,
                STATE_RECOVERY_REQUIRED,
                None,
                false,
                Some(FailureCode::RecoveryRequired),
            ));
        }
        ReceiptState::Unsupported => {
            return Ok(report(
                Operation::Inspect,
                STATE_RECOVERY_REQUIRED,
                None,
                false,
                Some(FailureCode::UnsupportedState),
            ));
        }
    };
    // A pending transaction is reported as guidance, never guessed at. A
    // partially removed installation is "damaged, cleanup pending": the frontend
    // resumes the uninstall, which is the only operation that owns removal.
    if matches!(journal, JournalState::Unsupported) {
        return Ok(report(
            Operation::Inspect,
            STATE_RECOVERY_REQUIRED,
            receipt.as_ref().map(|receipt| receipt.payload_id),
            false,
            Some(FailureCode::UnsupportedState),
        ));
    }
    let journal_guidance = match &journal {
        JournalState::None | JournalState::Unsupported => None,
        JournalState::Unreadable => Some(None),
        JournalState::Pending(journal) if journal.operation == Operation::Uninstall => {
            Some(Some(FailureCode::CleanupPending))
        }
        JournalState::Pending(_) => Some(None),
    };
    if let Some(guidance) = journal_guidance {
        return Ok(report(
            Operation::Inspect,
            if guidance == Some(FailureCode::CleanupPending) {
                STATE_DAMAGED
            } else {
                STATE_RECOVERY_REQUIRED
            },
            receipt.as_ref().map(|receipt| receipt.payload_id),
            false,
            guidance.or(Some(FailureCode::RecoveryRequired)),
        ));
    }

    let live = platform.read_registration(paths)?;
    let Some(receipt) = receipt else {
        return Ok(match live {
            Some(live) if !registration_is_owned(paths, &live) => report(
                Operation::Inspect,
                STATE_FOREIGN,
                None,
                false,
                Some(FailureCode::ForeignState),
            ),
            // An owned registration without a receipt is ambiguous state.
            Some(_) => report(
                Operation::Inspect,
                STATE_RECOVERY_REQUIRED,
                None,
                false,
                Some(FailureCode::RecoveryRequired),
            ),
            None => report(Operation::Inspect, STATE_ABSENT, None, false, None),
        });
    };

    let active = Some(receipt.payload_id);
    match live {
        None => Ok(report(
            Operation::Inspect,
            STATE_DAMAGED,
            active,
            false,
            Some(FailureCode::RepairPackageRequired),
        )),
        Some(live) if !registration_is_owned(paths, &live) => Ok(report(
            Operation::Inspect,
            STATE_FOREIGN,
            active,
            false,
            Some(FailureCode::ForeignState),
        )),
        Some(live) if live.manifest_digest() != receipt.manifest_sha256 => Ok(report(
            Operation::Inspect,
            STATE_DAMAGED,
            active,
            false,
            Some(FailureCode::RecoveryRequired),
        )),
        Some(live) => {
            // The one shared predicate: claims, resources, launch permission and
            // the executable the manifest actually selects.
            let usable = release_is_usable(platform, paths, &receipt, Some(&live));
            Ok(report(
                Operation::Inspect,
                if usable {
                    STATE_INSTALLED
                } else {
                    STATE_DAMAGED
                },
                active,
                usable,
                if usable {
                    None
                } else {
                    Some(FailureCode::RepairPackageRequired)
                },
            ))
        }
    }
}

/// Repair the installed release from an authenticated same-version package.
///
/// A repair creates no path, moves no activation point and never changes the
/// installation identity, so it journals nothing: the committed receipt remains
/// authoritative throughout and a crash leaves the same repairable state (each
/// restored file is written, flushed and read back through the platform, and the
/// registration - if it had to be restored - is published last). Every write is
/// still preceded by claim validation and a quiescence check, so a repair never
/// touches a path the receipt does not own.
fn repair(
    platform: &impl Platform,
    paths: &InstallPaths,
    package: &AuthenticatedPackage,
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

    let mut receipt = match Receipt::load(platform, paths)? {
        ReceiptState::None => {
            // Nothing is installed: there is nothing to repair, and no local
            // content may be trusted. Ask for the signed installer.
            return Err(InstallFailure::new(FailureCode::RepairPackageRequired));
        }
        ReceiptState::Committed(receipt) => *receipt,
        ReceiptState::Unreadable => return Err(recovery_required()),
        ReceiptState::Unsupported => return Err(unsupported_state()),
    };

    let descriptor = package.descriptor();
    descriptor.validate()?;
    if descriptor.target != paths.target() {
        return Err(InstallFailure::new(FailureCode::UnsupportedTarget));
    }
    if descriptor.channel != paths.channel() {
        return Err(origin_rejected());
    }
    // A repair source is the installed release, not "some signed package".
    if descriptor.version != receipt.version || descriptor.payload_id != receipt.payload_id {
        return Err(InstallFailure::new(FailureCode::RepairPackageRequired));
    }
    let version = descriptor.parsed_version()?;

    // The composed policy may only tighten, and a package that denies the
    // installed payload cannot repair it.
    let policy = authorize_package_policy(package, &receipt.release_policy, Operation::Repair)?;
    if policy.denies(&receipt.payload_id) {
        return Err(InstallFailure::new(FailureCode::PolicyDenied));
    }

    let executable = select_host(
        descriptor.target,
        package.payload().iter(),
        |source| source.relative(),
        |source| source.executable(),
    )?;

    // A live registration that is foreign, or ours but not the committed one, is
    // a conflict: report it and touch nothing. The damaged managed tree is
    // repaired only on top of unambiguous ownership.
    let live = platform.read_registration(paths)?;
    if let Some(live) = live.as_ref() {
        check_committed_registration(paths, live, receipt.manifest_sha256)?;
    }

    let version_relative = paths.version_dir_relative(&version, receipt.payload_id);
    // Validate every claim this repair may touch before the first mutation, so a
    // package path the receipt does not own can never be written.
    let plan = RepairPlan::prepare(paths, package, &receipt, &version_relative)?;

    // The policy this repair composed against is an editable receipt field. The
    // plan has just proven the package carries the installed release's own
    // descriptor (it must match the retained document the receipt claims and the
    // carrier digest it records), and that descriptor was authenticated through the
    // package: it is the installed release's signed policy, so it bounds the
    // receipt's. A rollback-produced receipt is additionally bounded by the replaced
    // release, authenticated in place. Neither being shown is `RecoveryRequired`
    // with nothing written; repair never persists a policy it composed against
    // unauthenticated state.
    if !is_no_broader_than(&receipt.release_policy, &descriptor.policy) {
        return Err(recovery_required());
    }
    bound_by_replaced_release(platform, paths, &receipt)?;

    // A repair mutates the installed tree, so it requires quiescence first.
    require_quiescent_within(platform, paths, quiescence)?;
    plan.restore(platform, paths)?;

    // The restored host is what Chrome will launch. Package-form approval was an
    // assessment of the *package* copy; the installed form is assessed here, under
    // the composed policy, before any registration is published and before success
    // can be reported - including a repair that publishes nothing.
    verify_installed_host(
        platform,
        paths,
        &version_relative,
        executable.relative(),
        descriptor,
        &policy,
    )?;

    // Publication is conditional on the observed preimage. A registration that
    // appeared, disappeared or changed during the quiescence wait is a conflict:
    // it is reported and never published over on the strength of a stale
    // observation.
    let observed = live.as_ref().map(Registration::manifest_digest);
    match (observed, platform.read_registration(paths)?) {
        (Some(expected), Some(current)) if current.manifest_digest() == expected => {
            // The exact committed registration is still live; nothing to publish.
        }
        (None, None) => {
            let manifest = manifest_for(
                paths,
                &paths
                    .version_dir(&version, receipt.payload_id)
                    .join(executable.relative()),
                &descriptor.extension_id,
            )?;
            if manifest.sha256() != receipt.manifest_sha256 {
                return Err(foreign_state());
            }
            let registration = Registration::new(manifest.bytes().to_vec())?;
            platform.publish_manifest(paths, &registration)?;
            platform.publish_registration(paths, &registration)?;
            let restored = platform.read_registration(paths)?.ok_or_else(io_failure)?;
            if restored.manifest_digest() != receipt.manifest_sha256 {
                return Err(io_failure());
            }
        }
        _ => return Err(foreign_state()),
    }

    // Persist a strictly tighter composed policy: denials accumulate even across
    // a repair, so a later rollback cannot claim the erased eligibility. The
    // installation identity and transaction are unchanged.
    if policy != receipt.release_policy {
        let refs = PolicyCarrierRefs::new(
            Digest::of(&canonical_json(descriptor)?),
            Digest::of(package.proof().carrier()),
            package.signer(),
            package.assessment(),
            policy.epoch,
        )?;
        receipt.policy = refs;
        receipt.release_policy = policy;
        receipt.write(platform, paths)?;
    }

    Ok(report(
        Operation::Repair,
        STATE_REPAIRED,
        Some(receipt.payload_id),
        true,
        None,
    ))
}

/// The validated set of authenticated files a repair may restore.
struct RepairedFile<'a> {
    relative: String,
    source: &'a AuthenticatedSource,
}

/// The exact paths and bytes a repair may write, validated before any mutation.
struct RepairPlan<'a> {
    /// Claimed directories of the active version, parents first.
    directories: Vec<String>,
    files: Vec<RepairedFile<'a>>,
    inventory_relative: String,
    inventory: Vec<u8>,
    sealed_relative: String,
    sealed: Option<Vec<u8>>,
    retained_relative: String,
    retained: Vec<u8>,
}

impl<'a> RepairPlan<'a> {
    fn prepare(
        paths: &InstallPaths,
        package: &'a AuthenticatedPackage,
        receipt: &Receipt,
        version_relative: &str,
    ) -> InstallResult<Self> {
        receipt.ownership.validate(paths)?;
        let claimed =
            |relative: &str, kind: OwnedKind, sha256: Option<Digest>| {
                receipt.ownership.owned.iter().any(|claim| {
                    claim.kind == kind && claim.path == relative && claim.sha256 == sha256
                })
            };

        // The installed `inventory.json` is the unsigned canonical payload
        // identity: a package whose canonical inventory is not the committed
        // payload is not a repair source for this installation.
        let inventory = package.canonical_inventory().to_vec();
        if Digest::of(&inventory) != receipt.payload_id {
            return Err(foreign_state());
        }
        let sealed = package.sealed_inventory().map(<[u8]>::to_vec);
        let sealed_relative = format!("{version_relative}/{SEALED_INVENTORY_FILE}");
        if let Some(sealed) = sealed.as_ref()
            && !claimed(&sealed_relative, OwnedKind::File, Some(Digest::of(sealed)))
        {
            return Err(foreign_state());
        }
        let inventory_relative = format!("{version_relative}/{PACKAGE_INVENTORY_FILE}");
        if !claimed(
            &inventory_relative,
            OwnedKind::File,
            Some(receipt.payload_id),
        ) {
            return Err(foreign_state());
        }

        let mut directories = vec![version_relative.to_owned()];
        for source in package.payload() {
            let parts: Vec<&str> = source.relative().split('/').collect();
            let mut prefix = version_relative.to_owned();
            for part in &parts[..parts.len() - 1] {
                prefix.push('/');
                prefix.push_str(part);
                if !directories.contains(&prefix) {
                    directories.push(prefix.clone());
                }
            }
        }
        directories.sort_by(|left, right| {
            path_depth(left)
                .cmp(&path_depth(right))
                .then_with(|| left.cmp(right))
        });
        for directory in &directories {
            if !claimed(directory, OwnedKind::Directory, None) {
                return Err(foreign_state());
            }
        }

        let retained_relative = format!("{version_relative}/{RETAINED_FILE_NAME}");
        let retained =
            RetainedInstall::from_package(paths, version_relative, package)?.to_bytes()?;
        if !claimed(
            &retained_relative,
            OwnedKind::File,
            Some(Digest::of(&retained)),
        ) {
            return Err(foreign_state());
        }

        // The installed carrier artifact is not restored by a repair: it is
        // installed once, in place, by the platform boundary, and only that
        // boundary can reinstall it with its signature-bearing metadata intact.
        // A replaced carrier therefore stays unverifiable and rollback reports
        // `NoRollback` instead of a repair silently claiming success.
        if Digest::of(package.proof().carrier()) != receipt.policy.carrier_sha256 {
            return Err(foreign_state());
        }

        let mut files = Vec::with_capacity(package.payload().len());
        for source in package.payload() {
            let relative = format!("{version_relative}/{}", source.relative());
            if !claimed(&relative, OwnedKind::File, Some(source.sha256())) {
                return Err(foreign_state());
            }
            files.push(RepairedFile { relative, source });
        }

        Ok(Self {
            directories,
            files,
            inventory_relative,
            inventory,
            sealed_relative,
            sealed,
            retained_relative,
            retained,
        })
    }

    /// Create a missing claimed directory, verify an existing one, then restore
    /// only the files whose exact bytes are missing or damaged.
    fn restore(&self, platform: &impl Platform, paths: &InstallPaths) -> InstallResult<()> {
        for directory in &self.directories {
            match fs::symlink_metadata(paths.root().join(directory)) {
                Ok(_) => {
                    ValidatedPath::new(paths, Path::new(directory), PathKind::Directory)?;
                }
                Err(error) if error.kind() == io::ErrorKind::NotFound => {
                    let handle =
                        ValidatedPath::new(paths, Path::new(directory), PathKind::NewDirectory)?;
                    platform.create_directory(&handle)?;
                }
                Err(_) => return Err(io_failure()),
            }
        }
        for file in &self.files {
            let bytes = read_source(file.source)?;
            restore_bytes(
                platform,
                paths,
                &file.relative,
                file.source.size(),
                file.source.sha256(),
                &bytes,
            )?;
            // Intact bytes with a lost launch permission are damage too: the
            // authenticated inventory's `executable` bit is re-applied through
            // the file's own handle.
            let handle = ValidatedPath::new(paths, Path::new(&file.relative), PathKind::File)?;
            if platform
                .check_launch_permissions(&handle, file.source.executable())
                .is_err()
            {
                platform.apply_launch_permissions(&handle, file.source.executable())?;
            }
        }
        restore_bytes(
            platform,
            paths,
            &self.inventory_relative,
            self.inventory.len() as u64,
            Digest::of(&self.inventory),
            &self.inventory,
        )?;
        if let Some(sealed) = self.sealed.as_ref() {
            restore_bytes(
                platform,
                paths,
                &self.sealed_relative,
                sealed.len() as u64,
                Digest::of(sealed),
                sealed,
            )?;
        }
        restore_bytes(
            platform,
            paths,
            &self.retained_relative,
            self.retained.len() as u64,
            Digest::of(&self.retained),
            &self.retained,
        )?;
        Ok(())
    }
}

/// Write `bytes` durably unless the managed path already holds exactly them.
fn restore_bytes(
    platform: &impl Platform,
    paths: &InstallPaths,
    relative: &str,
    size: u64,
    sha256: Digest,
    bytes: &[u8],
) -> InstallResult<()> {
    let absolute = paths.root().join(relative);
    let handle = match fs::symlink_metadata(&absolute) {
        Ok(metadata) => {
            if !metadata.is_file() {
                // A directory, link or special file at an owned file path is
                // never replaced by a repair.
                return Err(unsafe_path());
            }
            let intact = metadata.len() == size
                && read_no_follow(&absolute, size)
                    .map(|current| Digest::of(&current) == sha256)
                    .unwrap_or(false);
            if intact {
                return Ok(());
            }
            ValidatedPath::new(paths, Path::new(relative), PathKind::File)?
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            ValidatedPath::new(paths, Path::new(relative), PathKind::NewFile)?
        }
        Err(_) => return Err(io_failure()),
    };
    // Never truncate an existing inode: a managed path replaced by a hard link
    // must not rewrite the linked data. `replace_durable` stages new bytes into a
    // fresh sibling and renames it over the target.
    platform.replace_durable(&handle, bytes)
}

/// Re-read an authenticated package source immediately before restoring it.
///
/// A repair source that disappeared or no longer matches its authenticated
/// identity is not damaged local content and is not trust: the correct guidance
/// is a fresh signed download, with the prior installation untouched.
fn read_source(source: &AuthenticatedSource) -> InstallResult<Vec<u8>> {
    // One bounded handle read bound to the authenticated identity.
    let bytes = source
        .read_verified()
        .map_err(|_| InstallFailure::new(FailureCode::RepairPackageRequired))?;
    Ok(bytes)
}

/// Uninstall this product's registration, payload and receipts.
fn uninstall(
    platform: &impl Platform,
    paths: &InstallPaths,
    quiescence: Duration,
) -> InstallResult<InstallReport> {
    let _lock = platform.lock(paths)?;

    // A partial uninstall is resumable; every other pending transaction must be
    // reconciled first.
    let pending = match Journal::load(platform, paths)? {
        JournalState::None => None,
        JournalState::Pending(journal) if journal.operation == Operation::Uninstall => {
            Some(*journal)
        }
        JournalState::Pending(_) | JournalState::Unreadable => return Err(recovery_required()),
        JournalState::Unsupported => return Err(unsupported_state()),
    };
    let receipt = match Receipt::load(platform, paths)? {
        ReceiptState::None => None,
        ReceiptState::Committed(receipt) => Some(*receipt),
        ReceiptState::Unreadable => return Err(recovery_required()),
        ReceiptState::Unsupported => return Err(unsupported_state()),
    };

    // The durable removal intent: the journal of an interrupted uninstall, or
    // the committed receipt. A receipt that no longer names the journaled
    // transaction means another installation appeared: reconcile first.
    if let (Some(journal), Some(receipt)) = (&pending, &receipt)
        && receipt.transaction_id != journal.transaction_id
    {
        return Err(recovery_required());
    }
    let (manifest_sha256, root, claims) = match (&pending, &receipt) {
        (Some(journal), _) => (
            journal.manifest.sha256(),
            journal.root.clone(),
            journal.created.iter().map(JournalClaim::as_owned).collect(),
        ),
        (None, Some(receipt)) => (
            receipt.manifest_sha256,
            receipt.root.clone(),
            receipt.ownership.owned.clone(),
        ),
        (None, None) => {
            return match platform.read_registration(paths)? {
                Some(live) if !registration_is_owned(paths, &live) => {
                    // Another product's registration is preserved and reported.
                    Err(foreign_state())
                }
                // An owned registration without a receipt is ambiguous state.
                Some(_) => Err(recovery_required()),
                None => {
                    // Nothing is installed, but a removal is only established
                    // after the terminal inspection: an owned host may still be
                    // executing a payload that no receipt can claim.
                    require_quiescent_within(platform, paths, quiescence).map_err(deferred)?;
                    let leftover = final_layout_cleanup(platform, paths)?;
                    Ok(report_with_warnings(
                        Operation::Uninstall,
                        STATE_UNINSTALLED,
                        None,
                        false,
                        None,
                        if leftover {
                            vec![WarningCode::LeftoverFiles]
                        } else {
                            Vec::new()
                        },
                    ))
                }
            };
        }
    };

    // The claim set is untrusted input: re-normalize, scope-check and bound it
    // before a single path is removed. A claim that escapes the layout is
    // `RecoveryRequired` (the receipt is uninterpretable), never a delete.
    let record = OwnershipRecord {
        schema: OWNERSHIP_SCHEMA_VERSION,
        target: paths.target(),
        channel: paths.channel(),
        root,
        manifest_sha256,
        owned: claims,
    };
    record.validate(paths)?;

    // Validate every owned path before the first mutation: a present path whose
    // kind or content no longer matches is left alone and reported.
    let mut entries = Vec::with_capacity(record.owned.len());
    for claim in &record.owned {
        if let Some(entry) = OwnedEntry::from_claim(paths, claim)? {
            entries.push(entry);
        }
    }

    // Ownership is rechecked immediately before removal.
    let live = platform.read_registration(paths)?;
    if let Some(live) = live.as_ref() {
        check_committed_registration(paths, live, manifest_sha256)?;
    }

    // An initially active host defers the whole removal: the receipt and the
    // committed state stay durable and the frontend is told cleanup is pending,
    // never a bare `HostActive` that reads like a different failure.
    require_quiescent_within(platform, paths, quiescence).map_err(deferred)?;

    // Journal the removal before the first mutation whenever removing the
    // registration changes observable state. Without a live registration there
    // is no pre-payload mutation and the receipt - kept until the end - is the
    // durable intent.
    let journal = match (pending, live.as_ref()) {
        (Some(journal), _) => Some(journal),
        (None, Some(live)) => {
            let receipt = receipt.as_ref().ok_or_else(recovery_required)?;
            let journal = uninstall_journal(paths, receipt, live)?;
            journal.write(platform, paths)?;
            Some(journal)
        }
        (None, None) => None,
    };

    // Remove the registration first, only after ownership and quiescence were
    // validated.
    if let Some(live) = live.as_ref() {
        platform.remove_registration(paths, live)?;
    }
    if let Some(journal) = journal.as_ref() {
        journal
            .with_phase(Phase::CleanupPending)
            .write(platform, paths)?;
    }

    // Recheck immediately before deleting payload a late host may be executing.
    if let Err(failure) = require_quiescent_within(platform, paths, quiescence) {
        return Err(deferred(failure));
    }
    let mut leftover = remove_owned_entries(platform, &entries)?;

    // Every owned *file* claim must be gone. A claimed directory that still
    // exists can only retain foreign content at this point (every owned child
    // was removed above): that is preserved and reported, not treated as a
    // pending deletion.
    for claim in &record.owned {
        match claim.kind {
            // An owned leaf (file or link) that is still there was not removed:
            // keep the durable intent and report the deletion as pending.
            OwnedKind::File | OwnedKind::Symlink => match OwnedEntry::from_claim(paths, claim) {
                Ok(Some(_)) | Err(_) => {
                    return Err(InstallFailure::new(FailureCode::CleanupPending));
                }
                Ok(None) => {}
            },
            // A claimed directory that still exists (or was replaced by a link
            // or another kind) keeps only foreign content now: preserve and
            // report it rather than claiming a pending owned deletion.
            OwnedKind::Directory => {
                if !matches!(OwnedEntry::from_claim(paths, claim), Ok(None)) {
                    leftover = true;
                }
            }
        }
    }

    // Terminal quiescence: a host may have (re)launched while the payload was
    // being deleted. Removal is established only after the last inspection, and
    // a late launch defers the completion with the durable intent retained.
    if let Err(failure) = require_quiescent_within(platform, paths, quiescence) {
        return Err(deferred(failure));
    }

    // Final private-layout cleanup happens *before* the durable intent is
    // released, so an owned artifact that cannot be removed still reports
    // `CleanupPending` with the receipt/journal intact (never a false success).
    // Every deletion goes through a validated handle that revalidates its full
    // ancestry immediately before the unlink; a swapped ancestor fails closed and
    // its content is preserved as foreign leftovers.
    leftover |= final_layout_cleanup(platform, paths)?;

    // Release the durable intent only after removal is proven. The receipt is
    // released before the journal: a crash in between leaves a journal whose
    // claims are all absent, so the retry finishes idempotently.
    platform.write_state(paths, StateDocument::Receipt, &[])?;
    if journal.is_some() {
        Journal::clear(platform, paths)?;
    }

    Ok(report_with_warnings(
        Operation::Uninstall,
        STATE_UNINSTALLED,
        None,
        false,
        None,
        if leftover {
            vec![WarningCode::LeftoverFiles]
        } else {
            Vec::new()
        },
    ))
}

/// Remove the installer's own staging artifact and emptied private directories.
///
/// Every removal uses a validated handle ([`OwnedEntry::private_layout`]) that
/// captures the ancestry and content identity and revalidates them immediately
/// before the unlink: a `state/staging` directory swapped for a symlink during a
/// quiescence inspection can therefore never turn cleanup into a delete outside
/// the install root. Foreign content is preserved and reported; an owned artifact
/// that cannot be removed is `CleanupPending` (the caller keeps its receipt and
/// journal until removal is established).
///
/// The held lock file, the `state/` directory and the fixed root cannot be
/// removed while this process still holds the lock; they are the fixed layout,
/// not leftovers. The released (empty) journal/receipt slots are fixed state
/// slots too and are not reported as leftovers.
fn final_layout_cleanup(platform: &impl Platform, paths: &InstallPaths) -> InstallResult<bool> {
    remove_private_file(
        platform,
        paths,
        &format!("{STATE_DIR}/{STAGING_DIR}/registration.json"),
    )?;
    // `versions/`, `maintenance/` and `state/staging/` are part of the fixed
    // private layout the lock recreates on every run: an empty one is not a
    // leftover and removing it would mutate on an idempotent retry. Non-empty
    // ones hold foreign content and are reported below.
    Ok(layout_has_leftovers(paths))
}

/// Remove one fixed installer-private file, if it is still ours.
fn remove_private_file(
    platform: &impl Platform,
    paths: &InstallPaths,
    relative: &str,
) -> InstallResult<()> {
    match OwnedEntry::private_layout(paths, relative, OwnedKind::File) {
        Ok(None) => Ok(()),
        Ok(Some(entry)) => platform.remove_owned(&entry).map_err(deferred_removal),
        // The path (or its ancestry) is no longer the installer's own file: it
        // is foreign content now, preserved and reported rather than deleted.
        Err(failure) if failure.code == FailureCode::UnsafePath => Ok(()),
        Err(failure) => {
            // A staging path replaced by a regular file makes the artifact
            // unreachable (`ENOTDIR`) rather than absent. It is foreign content
            // the leftover report describes, not a deferred owned deletion.
            let unreachable = matches!(
                fs::symlink_metadata(paths.root().join(relative)),
                Err(error)
                    if error.kind() == io::ErrorKind::NotFound
                        || error.kind() == io::ErrorKind::NotADirectory
            );
            if unreachable {
                Ok(())
            } else {
                Err(deferred_removal(failure))
            }
        }
    }
}

/// Whether anything this product does not own remains inside its private layout.
fn layout_has_leftovers(paths: &InstallPaths) -> bool {
    for directory in [paths.versions(), paths.maintenance(), paths.staging()] {
        if private_path_has_entries(directory) {
            return true;
        }
    }
    let entries = match fs::read_dir(paths.state()) {
        Ok(entries) => entries,
        // A state directory that cannot be read is not provably clean: report a
        // leftover rather than claim a completed removal.
        Err(_) => return true,
    };
    for entry in entries {
        let entry = match entry {
            Ok(entry) => entry,
            Err(_) => return true,
        };
        let name = entry.file_name();
        // The lock and the released (empty) journal/receipt slots are the fixed
        // state layout, not leftovers. A quarantined or any other document is
        // foreign and is reported.
        if name != LOCK_FILE && name != JOURNAL_FILE && name != RECEIPT_FILE && name != STAGING_DIR
        {
            return true;
        }
    }
    false
}

/// Whether `path` is absent-or-an-empty directory, and provably so.
///
/// A path replaced by a regular file, a symlink (even one pointing at an empty
/// directory) or a special file is foreign content and is reported. So is a
/// directory whose listing fails: "cannot read" is never "empty".
fn private_path_has_entries(path: &Path) -> bool {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_dir() => match fs::read_dir(path) {
            Ok(mut entries) => entries.next().is_some(),
            Err(_) => true,
        },
        // A symlink to an empty directory is still a symlink, not an empty
        // installer-owned directory.
        Ok(_) => true,
        Err(error) if error.kind() == io::ErrorKind::NotFound => false,
        Err(_) => true,
    }
}

/// Require the live registration to be exactly the committed one.
///
/// A registration another product owns is `ForeignState`; one of ours that is not
/// the committed manifest is ambiguous state and `RecoveryRequired`. Neither is
/// overwritten or removed.
fn check_committed_registration(
    paths: &InstallPaths,
    live: &Registration,
    expected: Digest,
) -> InstallResult<()> {
    if !registration_is_owned(paths, live) {
        return Err(foreign_state());
    }
    if live.manifest_digest() != expected {
        return Err(recovery_required());
    }
    Ok(())
}

/// The durable intent of an uninstall, before the registration is removed.
fn uninstall_journal(
    paths: &InstallPaths,
    receipt: &Receipt,
    live: &Registration,
) -> InstallResult<Journal> {
    Ok(Journal {
        schema: JOURNAL_SCHEMA_VERSION,
        // The uninstall is identified by the transaction it undoes.
        transaction_id: receipt.transaction_id.clone(),
        operation: Operation::Uninstall,
        phase: Phase::Prepared,
        target: paths.target(),
        channel: paths.channel(),
        root: receipt.root.clone(),
        version: receipt.version.clone(),
        payload_id: receipt.payload_id,
        old_version: None,
        old_payload_id: None,
        old_carrier_sha256: None,
        manifest: ManifestSnapshot::new(live.manifest().to_vec())?,
        old_manifest: None,
        preimage: None,
        policy: receipt.policy.clone(),
        release_policy: receipt.release_policy.clone(),
        created: receipt.ownership.owned.iter().map(removal_claim).collect(),
    })
}

/// The journal claim of one receipt claim, for a removal that needs no size.
fn removal_claim(claim: &crate::platform::OwnedClaim) -> JournalClaim {
    JournalClaim {
        path: claim.path.clone(),
        kind: claim.kind,
        sha256: claim.sha256,
        size: None,
        incomplete: false,
    }
}

/// Remove owned files first and directories deepest-first.
fn remove_owned_entries(platform: &impl Platform, entries: &[OwnedEntry]) -> InstallResult<bool> {
    let mut leftover = false;
    let mut files: Vec<&OwnedEntry> = entries
        .iter()
        .filter(|entry| !entry.claim().kind.is_directory())
        .collect();
    files.sort_by(|left, right| left.claim().path.cmp(&right.claim().path));
    for entry in files {
        // Any error removing an owned file is a deletion that has not been
        // established: never report a completed uninstall.
        platform.remove_owned(entry).map_err(deferred_removal)?;
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
        match platform.remove_owned(entry) {
            Ok(()) => {}
            // Non-empty through foreign content: preserve it and report it.
            Err(failure) if failure.code == FailureCode::CleanupPending => leftover = true,
            // Replaced by a link/other kind: not ours to delete any more.
            Err(failure) if failure.code == FailureCode::UnsafePath => leftover = true,
            Err(failure) => return Err(deferred_removal(failure)),
        }
    }
    Ok(leftover)
}

/// Classify a failed removal of an owned path.
///
/// An owned path that cannot be removed means cleanup is not established: it is
/// reported as `CleanupPending` with the durable receipt/journal retained, never
/// as a completed uninstall. Paths that are no longer the owned content are
/// classified separately by the caller as preserved foreign leftovers.
fn deferred_removal(failure: InstallFailure) -> InstallFailure {
    match failure.code {
        FailureCode::UnsafePath | FailureCode::RecoveryRequired => failure,
        _ => InstallFailure::new(FailureCode::CleanupPending),
    }
}

/// A removal that could not complete is deferred visibly, never reported as a
/// completed uninstall.
fn deferred(failure: InstallFailure) -> InstallFailure {
    match failure.code {
        FailureCode::HostActive | FailureCode::InspectionDenied | FailureCode::CleanupPending => {
            InstallFailure::new(FailureCode::CleanupPending)
        }
        _ => failure,
    }
}

/// Whether the committed installation is live, whole and launchable.
///
/// A live read, never an assumption: this is what `prior_usable` means for a
/// failure that happened before the operation could classify it. It is the same
/// predicate recovery and `inspect` use ([`release_is_usable`]): the registration
/// is ours and is the committed manifest, every claimed file verifies by content,
/// the executable carries its launch permission and is the one the manifest
/// selects. It performs no OS trust assessment (see that function).
fn installation_is_usable(platform: &impl Platform, paths: &InstallPaths) -> bool {
    let Ok(ReceiptState::Committed(receipt)) = Receipt::load(platform, paths) else {
        return false;
    };
    let Ok(Some(live)) = platform.read_registration(paths) else {
        return false;
    };
    registration_is_owned(paths, &live)
        && live.manifest_digest() == receipt.manifest_sha256
        && release_is_usable(platform, paths, &receipt, Some(&live))
}

fn report(
    operation: Operation,
    state: &str,
    active_payload: Option<Digest>,
    prior_usable: bool,
    failure: Option<FailureCode>,
) -> InstallReport {
    InstallReport {
        operation,
        state: state.to_owned(),
        prior_usable,
        active_payload,
        failure,
        warnings: Vec::new(),
    }
}

fn report_with_warnings(
    operation: Operation,
    state: &str,
    active_payload: Option<Digest>,
    prior_usable: bool,
    failure: Option<FailureCode>,
    warnings: Vec<WarningCode>,
) -> InstallReport {
    report(operation, state, active_payload, prior_usable, failure).with_warnings(warnings)
}

fn path_depth(path: &str) -> usize {
    path.matches('/').count()
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
