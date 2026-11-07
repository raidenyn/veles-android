//! Windows OS trust adapter (Task 4).
//!
//! [`WindowsPlatform::verify_trust`] is read-only and implements the Windows
//! half of the package-authentication contract:
//!
//! * the package carrier (`<package>/carrier.exe`) is opened read-only with
//!   `FILE_SHARE_READ` only, so no writer, deleter or renamer can change it while
//!   it is being assessed;
//! * `WinVerifyTrust` (`WINTRUST_ACTION_GENERIC_VERIFY_V2`) verifies the embedded
//!   Authenticode signature and certificate chain with whole-chain revocation
//!   checks. Revocation checking is never disabled, no cache-only retry policy is
//!   requested and there is no UI: an explicit denial/revocation is
//!   `TrustDenied`, an assessment that cannot be formed (for example offline
//!   revocation data unavailable) is `TrustUnavailable`;
//! * the signer is the SHA-256 fingerprint of the leaf signing certificate, so a
//!   release policy pins one exact Authenticode certificate rather than a
//!   display name;
//! * the embedded policy-carrier resource must lie completely inside section raw
//!   data of the verified image, with a terminal certificate table and no
//!   unauthenticated trailing overlay (see [`super::pe`]). The image is re-read
//!   from offset zero through the assessed handle, because WinVerifyTrust leaves
//!   the shared file pointer at an unspecified offset.
//!
//! The status table itself is pure and lives in [`super::windows_trust`], so
//! every mapping is unit-tested on any host rather than only on Windows.
//!
//! The remaining [`Platform`] methods are deliberately fail-closed here; Task 5
//! implements [`Platform::inspect_hosts`] and Task 8 completes filesystem and
//! registry mutation and cleanup.
//!
//! ## Host inspection (Task 5)
//!
//! [`WindowsPlatform::inspect_hosts`] enumerates processes with
//! `NtQuerySystemInformation(SystemProcessInformation)`, which reports every
//! process's id and its image *file name* without opening anything. That name is
//! the first decision point: a process that could not be executing a file below
//! the owned `versions/`/`maintenance/` trees is reported by that name and ruled
//! out without any handle being opened, so the protected system processes a
//! non-elevated installer cannot open (the `System` pseudo-process, `Memory
//! Compression`, PPL services) never become blocking candidates. Only a process
//! whose name could be an owned executable's is opened, for the exact image path
//! and the token user SID. The session is deliberately not an ownership filter -
//! a service-session process running as the installing user can still read `%LOCALAPPDATA%` and execute an owned image,
//! and the same user may be logged on in a second session. For a process that
//! could be ours, the token user SID decides: a matching SID is the installing
//! user's process, a different SID is skipped, and a token or SID that cannot be
//! read is unattributed (not "another user"), so it stays a potential owned host
//! and blocks. Every candidate is re-read by PID from a fresh enumeration before
//! the decision and the process now holding the PID is classified, so a recycled
//! PID is neither trusted nor able to hide a host.

use std::ffi::{OsStr, OsString};
use std::fs::TryLockError;
use std::fs::{self, File};
use std::io::{self, Write};
use std::mem::size_of;
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::os::windows::io::{AsRawHandle, FromRawHandle};
use std::path::{Path, PathBuf};
use std::process;
use std::ptr;
use std::time::Instant;

use sha2::{Digest as _, Sha256};

use windows::Wdk::Foundation::OBJECT_ATTRIBUTES;
use windows::Wdk::Storage::FileSystem::{
    FILE_DIRECTORY_FILE, FILE_NON_DIRECTORY_FILE, FILE_OPEN_FOR_BACKUP_INTENT,
    FILE_OPEN_REPARSE_POINT, FILE_RENAME_INFORMATION, FILE_RENAME_REPLACE_IF_EXISTS,
    FILE_SYNCHRONOUS_IO_NONALERT, FileRenameInformation, NTCREATEFILE_CREATE_DISPOSITION,
    NTCREATEFILE_CREATE_OPTIONS, NtCreateFile, NtSetInformationFile,
};
use windows::Wdk::System::SystemInformation::{NtQuerySystemInformation, SystemProcessInformation};
use windows::Win32::Foundation::{
    CloseHandle, ERROR_FILE_NOT_FOUND, ERROR_INVALID_PARAMETER, ERROR_PATH_NOT_FOUND, GENERIC_READ,
    HANDLE, HLOCAL, HWND, LocalFree, NTSTATUS, OBJ_CASE_INSENSITIVE, STATUS_INFO_LENGTH_MISMATCH,
    STATUS_SUCCESS, UNICODE_STRING,
};
use windows::Win32::Security::Authorization::{
    ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW, SDDL_REVISION_1,
};
use windows::Win32::Security::Cryptography::CERT_CONTEXT;
use windows::Win32::Security::WinTrust::{
    CRYPT_PROVIDER_CERT, WINTRUST_ACTION_GENERIC_VERIFY_V2, WINTRUST_DATA, WINTRUST_DATA_0,
    WINTRUST_FILE_INFO, WTD_CHOICE_FILE, WTD_DISABLE_MD2_MD4, WTD_REVOCATION_CHECK_CHAIN,
    WTD_REVOKE_WHOLECHAIN, WTD_STATEACTION_CLOSE, WTD_STATEACTION_VERIFY, WTD_UI_NONE,
    WTHelperGetProvCertFromChain, WTHelperGetProvSignerFromChain, WTHelperProvDataFromStateData,
    WinVerifyTrust,
};
use windows::Win32::Security::{
    ACCESS_ALLOWED_ACE, ACE_HEADER, ACL, EqualSid, GetTokenInformation, IsValidSid, PSID,
    SECURITY_ATTRIBUTES, TOKEN_QUERY, TOKEN_USER, TokenUser,
};
use windows::Win32::Security::{PSECURITY_DESCRIPTOR, SECURITY_DESCRIPTOR};
use windows::Win32::Storage::FileSystem::{
    CreateDirectoryW, CreateFileW, FILE_ACCESS_RIGHTS, FILE_ATTRIBUTE_ARCHIVE,
    FILE_ATTRIBUTE_HIDDEN, FILE_ATTRIBUTE_NORMAL, FILE_ATTRIBUTE_NOT_CONTENT_INDEXED,
    FILE_ATTRIBUTE_REPARSE_POINT, FILE_ATTRIBUTE_SYSTEM, FILE_ATTRIBUTE_TEMPORARY, FILE_BASIC_INFO,
    FILE_BEGIN, FILE_DISPOSITION_INFO, FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_DELETE,
    FILE_SHARE_MODE, FILE_SHARE_READ, FILE_SHARE_WRITE, FileBasicInfo, FileDispositionInfo,
    FlushFileBuffers, GETFINALPATHNAMEBYHANDLE_FLAGS, GetFileInformationByHandleEx, GetFileSizeEx,
    OPEN_EXISTING, ReadFile, SetFileInformationByHandle, SetFilePointerEx, WriteFile,
};
use windows::Win32::System::IO::IO_STATUS_BLOCK;
use windows::Win32::System::Registry::{
    HKEY, HKEY_CURRENT_USER, KEY_QUERY_VALUE, KEY_SET_VALUE, KEY_WOW64_32KEY, KEY_WOW64_64KEY,
    REG_OPTION_NON_VOLATILE, REG_SAM_FLAGS, REG_SZ, REG_VALUE_TYPE, RegCloseKey, RegCreateKeyExW,
    RegDeleteKeyW, RegDeleteValueW, RegFlushKey, RegOpenKeyExW, RegQueryValueExW, RegSetValueExW,
};
use windows::Win32::System::Threading::{
    GetCurrentProcess, OpenProcess, OpenProcessToken, PROCESS_NAME_WIN32,
    PROCESS_QUERY_LIMITED_INFORMATION, QueryFullProcessImageNameW,
};
use windows::Win32::System::WindowsProgramming::SYSTEM_PROCESS_INFORMATION;
use windows::core::{HRESULT, PCWSTR, PWSTR};

use super::native::{
    NativeFs, native_carrier_entries, native_ensure_layout, native_flush_parent,
    native_install_carrier, native_lock, native_publish_manifest, native_quarantine_state,
    native_read_registration, native_read_state, native_remove_manifest, native_remove_owned,
    native_replace_durable, native_replace_file, native_write_durable, native_write_state,
    read_back, require_published_manifest,
};
use super::pe;
use super::windows_trust as trust_status;
use super::{
    CarrierEntry, HostAssessment, HostState, ImageClass, InstallLock, MAX_MANIFEST_PATH_BYTES,
    OwnedEntry, OwnedImages, PACKAGE_WINDOWS_CARRIER, PackageKind, PackageSource, PathKind,
    Platform, ProcessIdentity, ProcessImage, ProcessObservation, ProcessScope, ProcessTable,
    Registration, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, TrustProof, ValidatedPath,
    WindowsPreimage, inspect_host_processes, path_is_within,
};
use crate::cleanup::RemoveOutcome;
use crate::model::{Digest, FailureCode, InstallFailure, InstallResult, ReleaseDescriptor, Target};
use crate::paths::InstallPaths;

/// Name of the `RT_RCDATA` resource that carries the policy section.
const CARRIER_RESOURCE_NAME: &str = "VELES_POLICY_CARRIER";
/// Bound on the signed carrier image read for resource extraction.
const MAX_CARRIER_IMAGE_BYTES: usize = 64 * 1024 * 1024;
/// Bound on the extracted carrier bytes (`TrustProof` enforces the same limit).
const MAX_CARRIER_BYTES: usize = crate::policy::MAX_CARRIER_DOCUMENT_BYTES;
/// Bound on the snapshot entry list; a larger table is untrustworthy.
const MAX_PROCESS_SNAPSHOT_ENTRIES: usize = 1 << 16;
/// First `SystemProcessInformation` buffer size requested from the kernel.
const INITIAL_PROCESS_LIST_BYTES: usize = 256 * 1024;
/// Hard cap on the `SystemProcessInformation` buffer: a larger process table is
/// untrustworthy and is reported as an inspection failure, never as "no host".
const MAX_PROCESS_LIST_BYTES: usize = 32 * 1024 * 1024;
/// Bound on `STATUS_INFO_LENGTH_MISMATCH` retries.
const MAX_PROCESS_LIST_ATTEMPTS: usize = 8;
/// Bound for one process image path (32767 WCHARs plus the terminator).
const MAX_PROCESS_IMAGE_CHARS: usize = 32_768;
/// Chunk size for the carrier data copy.
///
/// The destination is created by this adapter and filled through `WriteFile`, so
/// the copy is bounded per call instead of being handed to a path-based
/// `CopyFile2`.
const CARRIER_COPY_CHUNK: usize = 1024 * 1024;
/// Attribute bits carried from the package source to the installed carrier.
///
/// `FILE_ATTRIBUTE_READONLY` is deliberately **not** carried: every entry in the
/// installer-owned tree must stay removable by the journaled cleanup (and by the
/// install's own failure discard), so a read-only carrier image would leave an
/// uninstallable file. The namespace/filesystem bits (`FILE_ATTRIBUTE_DIRECTORY`,
/// `FILE_ATTRIBUTE_REPARSE_POINT`, compressed/encrypted/sparse) never describe the
/// regular file created here and are not settable through `FileBasicInfo`.
const COPIED_ATTRIBUTE_MASK: u32 = FILE_ATTRIBUTE_ARCHIVE.0
    | FILE_ATTRIBUTE_HIDDEN.0
    | FILE_ATTRIBUTE_SYSTEM.0
    | FILE_ATTRIBUTE_TEMPORARY.0
    | FILE_ATTRIBUTE_NOT_CONTENT_INDEXED.0;

/// The HKCU key Native Messaging publication uses in production.
///
/// Chrome resolves a host registration for the current user at exactly
/// `HKEY_CURRENT_USER\Software\Google\Chrome\NativeMessagingHosts\<name>`.
/// `HKCU\Software` is not a WOW64-redirected key: a 32-bit and a 64-bit Chrome
/// read the same view, and this 64-bit adapter therefore writes the default view
/// rather than guessing a `KEY_WOW64_*` flag. The native test reads the same value
/// back through the default, explicit-64-bit and explicit-32-bit views and
/// requires all three to agree, which is what proves the path Chrome resolves.
pub const PRODUCTION_REGISTRY_PREFIX: &str = r"Software\Google\Chrome\NativeMessagingHosts";

/// The HKCU subtree test-only registrations are confined to.
///
/// Tests may never touch the production key: a test scope can only name a
/// subkey below this prefix, so even a mistaken call in a production binary
/// cannot address Chrome's key.
pub const TEST_REGISTRY_PREFIX: &str = r"Software\Veles\InstallerTests";

/// Which HKCU key a [`WindowsPlatform`] publishes the Native Messaging manifest
/// to.
///
/// Production always uses [`PRODUCTION_REGISTRY_PREFIX`]; the test constructor
/// is confined to [`TEST_REGISTRY_PREFIX`], so a fixture never mutates a
/// person's real Chrome registration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RegistryScope {
    prefix: String,
}

impl RegistryScope {
    /// The one production scope Chrome resolves.
    pub fn production() -> Self {
        Self {
            prefix: PRODUCTION_REGISTRY_PREFIX.to_owned(),
        }
    }

    /// A test-only scope below [`TEST_REGISTRY_PREFIX`].
    ///
    /// `subkey` must be one bounded, non-empty path component made of ASCII
    /// alphanumerics, `-`, `_` or `.`. It cannot contain a backslash, so a caller
    /// can never escape the test subtree and address Chrome's key.
    pub fn for_tests(subkey: &str) -> InstallResult<Self> {
        if subkey.is_empty()
            || subkey.len() > 128
            || subkey
                .chars()
                .any(|c| !(c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.')))
        {
            return Err(invalid_input());
        }
        Ok(Self {
            prefix: format!(r"{TEST_REGISTRY_PREFIX}\{subkey}"),
        })
    }

    /// Whether this scope is a test subtree (never the production key).
    pub fn is_test(&self) -> bool {
        self.prefix.starts_with(TEST_REGISTRY_PREFIX)
    }

    /// The exact key below HKCU for one installation's host name.
    pub fn key_for(&self, paths: &InstallPaths) -> String {
        format!(r"{}\{}", self.prefix, paths.registration_name())
    }
}

impl Default for RegistryScope {
    fn default() -> Self {
        Self::production()
    }
}

/// Which registry view a test read uses.
///
/// Production publication never selects a view; this exists so the native test
/// can prove the value is visible in every view Chrome could read it from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RegistryView {
    /// The process's own (non-redirected) view - what production uses.
    Default,
    /// An explicit 64-bit view.
    Native64,
    /// An explicit 32-bit view.
    Native32,
}

impl RegistryView {
    fn flags(self) -> REG_SAM_FLAGS {
        match self {
            Self::Default => REG_SAM_FLAGS(0),
            Self::Native64 => KEY_WOW64_64KEY,
            Self::Native32 => KEY_WOW64_32KEY,
        }
    }
}

/// The Windows [`Platform`] adapter.
///
/// Task 4 implements [`Platform::verify_trust`] and Task 5 implements
/// [`Platform::inspect_hosts`]; Task 8 completes the native filesystem, HKCU
/// registration and cleanup surface.
#[derive(Debug, Clone, Default)]
pub struct WindowsPlatform {
    scope: RegistryScope,
}

impl WindowsPlatform {
    /// Construct the Windows adapter for the production registration key.
    pub fn new() -> Self {
        Self {
            scope: RegistryScope::production(),
        }
    }

    /// Construct a test-scoped adapter whose HKCU key is below
    /// [`TEST_REGISTRY_PREFIX`], never Chrome's production key.
    ///
    /// Test-only: production binaries must call [`WindowsPlatform::new`]. The
    /// scope cannot address `Software\Google\Chrome\NativeMessagingHosts`.
    pub fn for_tests(subkey: &str) -> InstallResult<Self> {
        Ok(Self {
            scope: RegistryScope::for_tests(subkey)?,
        })
    }

    /// The registration scope this adapter publishes to.
    pub fn registry_scope(&self) -> &RegistryScope {
        &self.scope
    }

    /// The exact HKCU key this adapter uses for `paths`.
    pub fn registry_key(&self, paths: &InstallPaths) -> String {
        self.scope.key_for(paths)
    }

    /// Read the default (manifest path) value in one registry view.
    ///
    /// Read-only and view-explicit: the native test uses it to prove the
    /// production key is not redirected, while production publication uses the
    /// default view only.
    pub fn read_registry_value(
        &self,
        paths: &InstallPaths,
        view: RegistryView,
    ) -> InstallResult<Option<String>> {
        read_scope_value(&self.scope.key_for(paths), view)
    }

    /// Test-only: write the test scope's default value.
    ///
    /// Refuses unless the scope is a test subtree, so a fixture can stage a
    /// foreign registration without ever touching Chrome's production key.
    #[doc(hidden)]
    pub fn write_registry_value_for_tests(
        &self,
        paths: &InstallPaths,
        value: &str,
    ) -> InstallResult<()> {
        if !self.scope.is_test() {
            return Err(unsafe_path());
        }
        write_scope_value(&self.scope.key_for(paths), value)
    }
}

impl Platform for WindowsPlatform {
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock> {
        native_lock(self, paths)
    }

    fn write_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        native_write_durable(self, path, bytes).map(|_| ())
    }

    fn replace_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        native_replace_durable(self, path, bytes)
    }

    fn write_state(
        &self,
        paths: &InstallPaths,
        document: super::StateDocument,
        bytes: &[u8],
    ) -> InstallResult<()> {
        native_write_state(self, paths, document, bytes)
    }

    fn read_state(
        &self,
        paths: &InstallPaths,
        document: super::StateDocument,
    ) -> InstallResult<Option<Vec<u8>>> {
        native_read_state(self, paths, document)
    }

    fn quarantine_state(
        &self,
        paths: &InstallPaths,
        document: super::StateDocument,
    ) -> InstallResult<()> {
        native_quarantine_state(self, paths, document)
    }

    fn create_directory(&self, path: &ValidatedPath) -> InstallResult<()> {
        if path.kind() != super::PathKind::NewDirectory {
            return Err(invalid_input());
        }
        path.revalidate()?;
        // Created relative to the captured parent handle, which is then flushed
        // through that same handle.
        self.create_directory_at(path)
    }

    /// Publication step one: the manifest file.
    ///
    /// Both publication halves refuse a foreign registration. The HKCU value is
    /// read **before** anything is staged: if it names a manifest this installer
    /// does not own, this is `ForeignState` and no file or registry byte changes.
    fn publish_manifest(&self, paths: &InstallPaths, value: &Registration) -> InstallResult<()> {
        let key = self.scope.key_for(paths);
        let manifest = paths
            .registration_path()
            .to_str()
            .ok_or_else(unsafe_path)?
            .to_owned();
        if let Some(current) = read_scope_value(&key, RegistryView::Default)?
            && !same_windows_value(&current, &manifest)
        {
            return Err(foreign_state());
        }
        native_publish_manifest(self, paths, value)
    }

    fn replace_manifest(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        native_replace_file(self, staged, live)
    }

    /// The manifest file **and** the current HKCU default value.
    ///
    /// The HKCU value is read **first**, before the manifest is even looked at: a
    /// value this installer does not own is foreign state even when our local
    /// manifest is missing, so returning `None` early would conceal it. The
    /// returned [`Registration`] carries a [`WindowsPreimage`] naming the exact key
    /// and the value publication would replace (or its absence), so the
    /// transaction journals a complete restore preimage before the first registry
    /// mutation.
    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>> {
        let key = self.scope.key_for(paths);
        let previous = read_scope_value(&key, RegistryView::Default)?;
        let manifest = paths
            .registration_path()
            .to_str()
            .ok_or_else(unsafe_path)?
            .to_owned();
        if let Some(current) = previous.as_ref()
            && !same_windows_value(current, &manifest)
        {
            // The value names another product's manifest (or a stale path): this
            // is not our registration, whatever our manifest file says.
            return Err(foreign_state());
        }
        let Some(live) = native_read_registration(self, paths)? else {
            return Ok(None);
        };
        Ok(Some(live.with_windows_preimage(WindowsPreimage::new(
            key, previous,
        )?)))
    }

    /// Publication step two: re-read the live manifest, then write the fixed HKCU
    /// default value that points Chrome at the absolute manifest path.
    fn publish_registration(
        &self,
        paths: &InstallPaths,
        value: &Registration,
    ) -> InstallResult<()> {
        require_published_manifest(self, paths, value)?;
        let key = self.scope.key_for(paths);
        let manifest = paths
            .registration_path()
            .to_str()
            .ok_or_else(unsafe_path)?
            .to_owned();
        // A value already present may only be this exact manifest path (our own
        // previous publication). Anything else is another owner of our host name
        // and is left untouched.
        if let Some(current) = read_scope_value(&key, RegistryView::Default)?
            && !same_windows_value(&current, &manifest)
        {
            return Err(foreign_state());
        }
        write_scope_value(&key, &manifest)
    }

    /// Remove our registration: the HKCU value first, then the manifest file.
    ///
    /// The order is deliberate. A crash between the two leaves the manifest
    /// without the value, which a republish can restore from the journal; the
    /// reverse order could leave a value pointing at an absent manifest with no
    /// preimage recorded.
    fn remove_registration(
        &self,
        paths: &InstallPaths,
        expected: &Registration,
    ) -> InstallResult<()> {
        let live = native_read_registration(self, paths)?;
        if let Some(live) = live.as_ref()
            && !expected.matches_published(live)
        {
            return Err(foreign_state());
        }
        let key = self.scope.key_for(paths);
        let manifest = paths
            .registration_path()
            .to_str()
            .ok_or_else(unsafe_path)?
            .to_owned();
        match read_scope_value(&key, RegistryView::Default)? {
            Some(current) if !same_windows_value(&current, &manifest) => {
                return Err(foreign_state());
            }
            Some(_) => delete_scope_key(&key)?,
            None => {}
        }
        if live.is_some() {
            native_remove_manifest(self, paths, expected)?;
        }
        Ok(())
    }

    fn inspect_hosts(&self, paths: &InstallPaths) -> InstallResult<HostState> {
        Ok(inspect_host_processes(paths, &WindowsProcessTable))
    }

    fn verify_trust(
        &self,
        source: &PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        verify_trust(source, expected)
    }

    /// A retained version directory holds the same signed `carrier.exe` a fresh
    /// package does, so the retained check is the identical Authenticode path.
    fn verify_retained(
        &self,
        source: &PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        verify_trust(source, expected)
    }

    /// Authenticode over the **host executable** itself (package or installed
    /// form), through the same chain/revocation path as the carrier.
    fn verify_host(
        &self,
        root: &Path,
        executable: &str,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<HostAssessment> {
        verify_host(root, executable, expected)
    }

    fn carrier_entries(&self, source: &PackageSource) -> InstallResult<Vec<CarrierEntry>> {
        native_carrier_entries(self, source)
    }

    fn install_carrier(
        &self,
        source: &PackageSource,
        destination: &ValidatedPath,
    ) -> InstallResult<()> {
        native_install_carrier(self, source, destination)
    }

    fn remove_owned(&self, entry: &OwnedEntry) -> InstallResult<()> {
        native_remove_owned(self, entry)
    }

    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()> {
        native_flush_parent(self, path)
    }
}

fn verify_trust(source: &PackageSource, expected: &ReleaseDescriptor) -> InstallResult<TrustProof> {
    if expected.target != Target::WindowsX64 {
        return Err(unsupported());
    }
    if source.kind() != PackageKind::Directory {
        return Err(invalid_package());
    }
    let carrier = open_carrier(source.path())?;
    let handle = ReadOnlyFile::open(carrier.path())?;
    let wide = wide_path(carrier.path())?;
    let signer = authenticate_file(&handle, &wide)?;

    let image = handle.read_all()?;
    let carrier_bytes = pe::extract_carrier(&image, CARRIER_RESOURCE_NAME, MAX_CARRIER_BYTES)
        .map_err(|_| {
            // A verified image without a covered carrier resource is not a
            // policy carrier, regardless of how valid its signature is.
            trust_denied()
        })?;
    TrustProof::new(
        signer,
        TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
        carrier_bytes.to_vec(),
    )
}

/// Assess one host executable with WinVerifyTrust.
///
/// The path is walked component by component with no-follow semantics before it
/// is opened (a linked ancestor or leaf is `UnsafePath`), the verification runs
/// over the handle that was opened, and revocation checking is never disabled.
fn verify_host(
    root: &Path,
    executable: &str,
    expected: &ReleaseDescriptor,
) -> InstallResult<HostAssessment> {
    if expected.target != Target::WindowsX64 {
        return Err(unsupported());
    }
    let relative = crate::inventory::normalize_relative_path(executable)?;
    // The anchored, no-follow handle the containment walk produced is the very
    // handle WinVerifyTrust assesses: the host is not reopened by pathname.
    let path = root.join(&relative);
    let handle = ReadOnlyFile::adopt(crate::inventory::open_entry_for_assessment(
        root, &relative,
    )?);
    let wide = wide_path(&path)?;
    let signer = authenticate_file(&handle, &wide)?;
    HostAssessment::new(signer, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE)
}

/// The Windows process table.
///
/// Processes are enumerated with `NtQuerySystemInformation(SystemProcessInformation)`,
/// which reports each process's id and image *file name* without opening it. That
/// name decides whether a handle is needed at all: a process whose name no
/// executable below the owned trees has is reported by that name and the
/// classifier rules it out, so the protected system processes a non-elevated
/// installer cannot open never become blocking candidates. Only a process whose
/// name could be an owned executable's is opened, for the exact image path and the
/// token user SID: the image is resolved before the token, and attribution is the
/// token user SID, never the session (a service-session process running as the
/// installing user can still read `%LOCALAPPDATA%` and execute an owned image, and
/// the same user may be logged on in more than one session).
#[derive(Debug, Default)]
struct WindowsProcessTable;

impl ProcessTable for WindowsProcessTable {
    fn observe(&self, paths: &InstallPaths) -> io::Result<Vec<ProcessObservation>> {
        self.snapshot(paths)
    }

    /// A fresh enumeration by pid.
    ///
    /// The evidence of the process that holds the pid now decides, so a recycled
    /// pid is neither trusted nor able to hide a host. `None` means the pid is no
    /// longer present, which is the only thing that may be read as "exited".
    fn recheck(&self, paths: &InstallPaths, pid: u32) -> io::Result<Option<ProcessObservation>> {
        Ok(self
            .snapshot(paths)?
            .into_iter()
            .find(|observation| observation.identity.pid == pid))
    }
}

impl WindowsProcessTable {
    /// Read the process table once and report every present process as evidence.
    ///
    /// The listed image name decides only whether a handle is needed: a process
    /// whose name no executable below the owned trees has is reported as
    /// [`ProcessImage::Name`] and never opened, and the classifier rules it out.
    /// A process that could be executing an owned image is opened for its exact
    /// path and token, and one that cannot be opened is reported by its name too -
    /// the classifier can only treat that as a potential host, so it blocks
    /// instead of being written off as another user's process.
    fn snapshot(&self, paths: &InstallPaths) -> io::Result<Vec<ProcessObservation>> {
        let owned = OwnedImages::capture(paths)?;
        let own = CurrentUser::capture()?;
        let mut observations = Vec::new();
        for listed in list_processes()? {
            if listed.pid == 0 || listed.pid == process::id() {
                continue;
            }
            let observation = if needs_inspection(&owned, &listed) {
                match open_process(listed.pid) {
                    OpenedProcess::Handle(handle) => {
                        observe_open_process(&listed, &handle, &own, &owned)
                    }
                    // It could be executing an owned image and this installer may
                    // not open it, so it cannot be attributed: it stays a
                    // potential host.
                    OpenedProcess::Denied => name_only(&listed),
                    // The process exited after the list was taken.
                    OpenedProcess::Gone => continue,
                }
            } else {
                // Nothing below the owned trees has this name, so no handle, token
                // or block is needed for it.
                name_only(&listed)
            };
            observations.push(observation);
        }
        Ok(observations)
    }
}

/// One process as `SystemProcessInformation` reported it.
struct ListedProcess {
    pid: u32,
    /// The image *file name* only: the kernel never reports the directory here,
    /// and reports an empty name for a process that executes no image file.
    image_name: OsString,
}

/// Whether this installer has to open the listed process.
///
/// This decides only whether a handle is needed, never whether the process is
/// reported: a name that no executable below the owned trees has cannot be an
/// owned host, so that process is reported by its name and never opened. An empty
/// name rules nothing out on its own - the kernel reports it for a process that
/// executes no image file, but the exact path is better evidence - so such a
/// process is opened and decided exactly when that is possible. Only an empty name
/// on a process this installer cannot open falls back to the inference in
/// [`ProcessImage::Name`].
fn needs_inspection(owned: &OwnedImages, listed: &ListedProcess) -> bool {
    listed.image_name.is_empty() || owned.may_be_owned_name(&listed.image_name)
}

/// Enumerate every process and its image file name.
///
/// `SystemProcessInformation` is a linked list of variable-length entries in one
/// caller-allocated buffer. The required length is not known in advance, so a
/// `STATUS_INFO_LENGTH_MISMATCH` retries with the reported (and a geometric)
/// growth, bounded by [`MAX_PROCESS_LIST_BYTES`] and
/// [`MAX_PROCESS_LIST_ATTEMPTS`]. Every failure is an error, never an empty list:
/// a table that could not be read must not look like "no host".
///
/// The entry layout is the 64-bit one (`SYSTEM_PROCESS_INFORMATION` is 256 bytes,
/// `ImageName` at 56 and `UniqueProcessId` at 80), which is what this adapter's
/// only supported target (`Target::WindowsX64`) gets from the kernel.
fn list_processes() -> io::Result<Vec<ListedProcess>> {
    let mut bytes = INITIAL_PROCESS_LIST_BYTES;
    for _ in 0..MAX_PROCESS_LIST_ATTEMPTS {
        let words = bytes.div_ceil(size_of::<usize>());
        let mut buffer = vec![0usize; words];
        let size = words * size_of::<usize>();
        let size = u32::try_from(size).map_err(|_| io::Error::other("implausible process list"))?;
        let mut required = 0u32;
        // SAFETY: the buffer is live, word-aligned and `size` bytes long, and
        // `required` is a live out-parameter. The kernel writes at most `size`.
        let status = unsafe {
            NtQuerySystemInformation(
                SystemProcessInformation,
                buffer.as_mut_ptr().cast(),
                size,
                &mut required,
            )
        };
        if status == STATUS_SUCCESS {
            return parse_process_list(&buffer, size as usize);
        }
        if status != STATUS_INFO_LENGTH_MISMATCH {
            return Err(io::Error::other("process list unavailable"));
        }
        // The kernel reports the required length, but it can be stale or zero, so
        // the buffer also grows geometrically.
        let next = (required as usize).max(bytes.saturating_mul(2));
        if next > MAX_PROCESS_LIST_BYTES {
            return Err(io::Error::other("implausible process list"));
        }
        bytes = next;
    }
    Err(io::Error::other("process list kept changing"))
}

/// Walk one `SystemProcessInformation` list.
///
/// A success status means the kernel wrote the complete list, which ends with an
/// entry whose `NextEntryOffset` is zero; `length` is the buffer it was allowed to
/// fill and bounds every read, including the image-name buffers.
fn parse_process_list(buffer: &[usize], length: usize) -> io::Result<Vec<ListedProcess>> {
    if length == 0 || length > size_of_val(buffer) {
        return Err(io::Error::other("truncated process list"));
    }
    let base = buffer.as_ptr().cast::<u8>();
    let entry_size = size_of::<SYSTEM_PROCESS_INFORMATION>();
    let mut offset = 0usize;
    let mut processes = Vec::new();
    loop {
        if processes.len() >= MAX_PROCESS_SNAPSHOT_ENTRIES {
            return Err(io::Error::other("implausible process list"));
        }
        if length.saturating_sub(offset) < entry_size {
            return Err(io::Error::other("truncated process list"));
        }
        // SAFETY: the entry was bounds-checked above; `read_unaligned` copies it
        // out without assuming an alignment the documented list does not promise.
        let entry =
            unsafe { ptr::read_unaligned(base.add(offset).cast::<SYSTEM_PROCESS_INFORMATION>()) };
        let next = entry.NextEntryOffset as usize;
        processes.push(ListedProcess {
            pid: entry.UniqueProcessId.0 as usize as u32,
            image_name: image_name_of(&entry, base, length)?,
        });
        if next == 0 {
            return Ok(processes);
        }
        // Every entry must advance by at least one structure.
        if next < entry_size {
            return Err(io::Error::other("malformed process list"));
        }
        offset = offset
            .checked_add(next)
            .ok_or_else(|| io::Error::other("malformed process list"))?;
        if offset >= length {
            return Err(io::Error::other("truncated process list"));
        }
    }
}

/// The image file name of one entry, validated against the buffer the kernel
/// filled, so a layout surprise fails the inspection instead of being parsed.
fn image_name_of(
    entry: &SYSTEM_PROCESS_INFORMATION,
    base: *const u8,
    length: usize,
) -> io::Result<OsString> {
    let bytes = entry.ImageName.Length as usize;
    // A process that executes no image file (the idle process and other
    // kernel-created processes) has a zero-length name.
    if bytes == 0 {
        return Ok(OsString::new());
    }
    if !bytes.is_multiple_of(2) || entry.ImageName.Buffer.0.is_null() {
        return Err(io::Error::other("malformed image name"));
    }
    let start = entry.ImageName.Buffer.0 as usize;
    let offset = start
        .checked_sub(base as usize)
        .ok_or_else(|| io::Error::other("malformed image name"))?;
    if offset.checked_add(bytes).is_none_or(|end| end > length) {
        return Err(io::Error::other("malformed image name"));
    }
    // SAFETY: the name lies inside the buffer the kernel filled, in whole UTF-16
    // code units.
    let wide = unsafe { std::slice::from_raw_parts(entry.ImageName.Buffer.0, bytes / 2) };
    Ok(OsString::from_wide(wide))
}

/// The installing user's token user SID.
struct CurrentUser {
    sid: TokenUserSid,
}

impl CurrentUser {
    fn capture() -> io::Result<Self> {
        // SAFETY: `GetCurrentProcess` returns a non-owning pseudo-handle.
        let sid = TokenUserSid::of_process(unsafe { GetCurrentProcess() })
            .ok_or_else(|| io::Error::other("current process token user is unavailable"))?;
        Ok(Self { sid })
    }
}

/// An observation for a process that could be executing an owned image but that
/// this installer may not open.
fn name_only(listed: &ListedProcess) -> ProcessObservation {
    ProcessObservation {
        identity: ProcessIdentity { pid: listed.pid },
        scope: ProcessScope::Unknown,
        image: ProcessImage::Name(listed.image_name.clone()),
    }
}

/// Observe one process this installer could open.
///
/// The image is resolved first. A foreign image makes the process irrelevant
/// whatever its user, which is recorded as an unattributed scope so the token is
/// never read. Otherwise a token or SID that cannot be read keeps the process
/// unattributed, which the classifier treats as a potential host instead of
/// "another user". When the exact path cannot be queried, the listed image name
/// is the remaining evidence and stays a name-only observation.
fn observe_open_process(
    listed: &ListedProcess,
    handle: &OwnedHandle,
    own: &CurrentUser,
    owned: &OwnedImages,
) -> ProcessObservation {
    let image = match image_path(handle) {
        Some(path) => ProcessImage::Path(path),
        None => ProcessImage::Name(listed.image_name.clone()),
    };
    let scope = match &image {
        ProcessImage::Path(path) if owned.classify(Some(path)) == ImageClass::Foreign => {
            // Outside the owned trees: irrelevant whatever its user, so the
            // token is never read. `Unknown` records that it was not attributed.
            ProcessScope::Unknown
        }
        _ => match TokenUserSid::of_process(handle.0) {
            Some(token) => match same_user(own, &token) {
                Some(true) => ProcessScope::InstallingUser,
                Some(false) => ProcessScope::OtherUser,
                None => ProcessScope::Unknown,
            },
            None => ProcessScope::Unknown,
        },
    };
    ProcessObservation {
        identity: ProcessIdentity { pid: listed.pid },
        scope,
        image,
    }
}

/// Whether two token user SIDs name the same principal.
///
/// `EqualSid` reports "not equal" and "could not compare" through the same FALSE
/// return, so both SIDs are validated first: an invalid SID is unattributable,
/// never "another user" (which would skip a potential host).
fn same_user(own: &CurrentUser, token: &TokenUserSid) -> Option<bool> {
    let own_sid = own.sid.sid();
    let token_sid = token.sid();
    // SAFETY: both SIDs point into live, OS-filled `TOKEN_USER` buffers.
    unsafe {
        if !IsValidSid(own_sid).as_bool() || !IsValidSid(token_sid).as_bool() {
            return None;
        }
        Some(EqualSid(own_sid, token_sid).is_ok())
    }
}

/// The result of trying to open one process for inspection.
enum OpenedProcess {
    /// A handle with the right to read the token and the image path.
    Handle(OwnedHandle),
    /// The PID no longer belongs to any process.
    Gone,
    /// The process exists but this process may not open it.
    Denied,
}

fn open_process(pid: u32) -> OpenedProcess {
    // SAFETY: `pid` is an observed process id and `false` disables inheritance.
    match unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) } {
        Ok(handle) => match OwnedHandle::new(handle) {
            Some(handle) => OpenedProcess::Handle(handle),
            None => OpenedProcess::Denied,
        },
        // `OpenProcess` reports a PID that does not exist as
        // `ERROR_INVALID_PARAMETER`; every other failure (access denied,
        // protection) means the process may still be running.
        Err(error) if error.code() == HRESULT::from_win32(ERROR_INVALID_PARAMETER.0) => {
            OpenedProcess::Gone
        }
        Err(_) => OpenedProcess::Denied,
    }
}

fn image_path(handle: &OwnedHandle) -> Option<PathBuf> {
    let mut buffer = vec![0u16; MAX_PROCESS_IMAGE_CHARS];
    let mut size = buffer.len() as u32;
    // SAFETY: the buffer is live and `size` is its length in WCHARs on entry.
    unsafe {
        QueryFullProcessImageNameW(
            handle.0,
            PROCESS_NAME_WIN32,
            PWSTR(buffer.as_mut_ptr()),
            &mut size,
        )
    }
    .ok()?;
    let size = (size as usize).min(buffer.len());
    if size == 0 {
        return None;
    }
    // The image path is arbitrary UTF-16 (not necessarily valid Unicode), so it
    // is kept as an OS string instead of being lossily decoded.
    Some(PathBuf::from(OsString::from_wide(&buffer[..size])))
}

/// The user SID of one process token, in a word-aligned buffer the OS filled.
struct TokenUserSid {
    buffer: Vec<usize>,
}

impl TokenUserSid {
    fn of_process(process: HANDLE) -> Option<Self> {
        let mut token = HANDLE::default();
        // SAFETY: `process` is a live process handle and `token` is a live
        // out-parameter; the token handle is closed by `OwnedHandle`.
        unsafe { OpenProcessToken(process, TOKEN_QUERY, &mut token) }.ok()?;
        let token = OwnedHandle::new(token)?;

        let mut needed = 0u32;
        // SAFETY: a null buffer with length zero only asks for the size.
        let _ = unsafe { GetTokenInformation(token.0, TokenUser, None, 0, &mut needed) };
        if needed == 0 {
            return None;
        }
        let words = (needed as usize).div_ceil(size_of::<usize>());
        let mut buffer = vec![0usize; words];
        // SAFETY: the buffer is aligned and `words * size_of::<usize>()` bytes
        // long, which is at least `needed`.
        unsafe {
            GetTokenInformation(
                token.0,
                TokenUser,
                Some(buffer.as_mut_ptr().cast()),
                (words * size_of::<usize>()) as u32,
                &mut needed,
            )
        }
        .ok()?;
        Some(Self { buffer })
    }

    fn sid(&self) -> PSID {
        // SAFETY: `GetTokenInformation(TokenUser)` wrote a complete
        // `TOKEN_USER` into the aligned buffer.
        unsafe { (*self.buffer.as_ptr().cast::<TOKEN_USER>()).User.Sid }
    }
}

/// A Win32 handle this module closes exactly once.
struct OwnedHandle(HANDLE);

impl OwnedHandle {
    fn new(handle: HANDLE) -> Option<Self> {
        (!handle.is_invalid()).then_some(Self(handle))
    }
}

impl Drop for OwnedHandle {
    fn drop(&mut self) {
        // SAFETY: the handle was returned by a Win32 call that transfers
        // ownership to the caller.
        unsafe {
            let _ = CloseHandle(self.0);
        }
    }
}

fn open_carrier(package_root: &Path) -> InstallResult<PackageSource> {
    let candidate = package_root.join(PACKAGE_WINDOWS_CARRIER);
    if !path_is_within(package_root, &candidate) {
        return Err(unsafe_path());
    }
    match fs::symlink_metadata(&candidate) {
        Ok(_) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Err(invalid_package()),
        Err(_) => return Err(io_failure()),
    }
    let carrier = PackageSource::open(&candidate)?;
    if carrier.kind() != PackageKind::File {
        return Err(invalid_package());
    }
    Ok(carrier)
}

fn wide_path(path: &Path) -> InstallResult<Vec<u16>> {
    let wide: Vec<u16> = path.as_os_str().encode_wide().chain([0]).collect();
    if wide.len() > 32_767 {
        return Err(invalid_input());
    }
    Ok(wide)
}

/// Run WinVerifyTrust over one already-open file and return its signer.
///
/// Revocation checking is never disabled, no cache-only retry is requested and
/// there is no UI, so an explicit denial/revocation and an assessment that could
/// not be formed stay distinguishable through [`classify`].
fn authenticate_file(handle: &ReadOnlyFile, wide: &[u16]) -> InstallResult<String> {
    let mut file_info = WINTRUST_FILE_INFO {
        cbStruct: size_of::<WINTRUST_FILE_INFO>() as u32,
        pcwszFilePath: PCWSTR(wide.as_ptr()),
        hFile: handle.0,
        pgKnownSubject: ptr::null_mut(),
    };
    let mut data = WINTRUST_DATA {
        cbStruct: size_of::<WINTRUST_DATA>() as u32,
        dwUIChoice: WTD_UI_NONE,
        fdwRevocationChecks: WTD_REVOKE_WHOLECHAIN,
        dwUnionChoice: WTD_CHOICE_FILE,
        Anonymous: WINTRUST_DATA_0 {
            pFile: &mut file_info,
        },
        dwStateAction: WTD_STATEACTION_VERIFY,
        dwProvFlags: WTD_REVOCATION_CHECK_CHAIN | WTD_DISABLE_MD2_MD4,
        ..Default::default()
    };
    let mut action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
    // SAFETY: `data` is a fully initialized structure and `handle` is a live
    // read-only file handle.
    let status = unsafe {
        WinVerifyTrust(
            HWND::default(),
            &mut action,
            (&mut data as *mut WINTRUST_DATA).cast(),
        )
    };
    let signer = if status == 0 {
        // State data lives only until WTD_STATEACTION_CLOSE.
        signer_fingerprint(&data)
    } else {
        Err(classify(status))
    };
    close_state(&mut data);
    signer
}

/// Verify the cleanup helper's own Authenticode signature and return its signer.
///
/// The cleanup helper is installed outside the removable root, so it must prove
/// it is still the exact signed image before it deletes anything: a replaced
/// helper is denied by the same whole-chain Authenticode path the package
/// assessment uses.
pub(crate) fn windows_verify_own_signature(path: &Path) -> InstallResult<String> {
    let handle = ReadOnlyFile::open(path)?;
    let wide = wide_path(path)?;
    authenticate_file(&handle, &wide)
}

/// The signer fingerprint from the live WinVerifyTrust state.
fn signer_fingerprint(data: &WINTRUST_DATA) -> InstallResult<String> {
    if data.hWVTStateData.is_invalid() {
        return Err(trust_denied());
    }
    // SAFETY: `hWVTStateData` belongs to the successful verify call above and is
    // valid until the matching WTD_STATEACTION_CLOSE. Every returned pointer is
    // null-checked before it is dereferenced, and the certificate bytes are
    // copied out immediately.
    unsafe {
        let provider = WTHelperProvDataFromStateData(data.hWVTStateData);
        if provider.is_null() {
            return Err(trust_denied());
        }
        let signer = WTHelperGetProvSignerFromChain(provider, 0, false, 0);
        if signer.is_null() || (*signer).dwError != 0 {
            return Err(trust_denied());
        }
        let certificate = WTHelperGetProvCertFromChain(signer, 0);
        if certificate.is_null() {
            return Err(trust_denied());
        }
        let certificate: &CRYPT_PROVIDER_CERT = &*certificate;
        if certificate.fTestCert.0 != 0 || certificate.pCert.is_null() {
            return Err(trust_denied());
        }
        let context: &CERT_CONTEXT = &*certificate.pCert;
        if context.pbCertEncoded.is_null() || context.cbCertEncoded == 0 {
            return Err(trust_denied());
        }
        let encoded =
            std::slice::from_raw_parts(context.pbCertEncoded, context.cbCertEncoded as usize);
        Ok(format!("sha256:{}", Digest::of(encoded).to_hex()))
    }
}

fn close_state(data: &mut WINTRUST_DATA) {
    data.dwStateAction = WTD_STATEACTION_CLOSE;
    let mut action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
    // SAFETY: `data` is the same initialized structure used for the verify call;
    // closing releases the provider state. A close error is not actionable.
    unsafe {
        let _ = WinVerifyTrust(
            HWND::default(),
            &mut action,
            (data as *mut WINTRUST_DATA).cast(),
        );
    }
}

/// A carrier handle opened for read only.
///
/// `FILE_SHARE_READ` alone denies every write/delete/rename sharing mode, so the
/// exact bytes WinVerifyTrust hashes cannot change before the resource is read
/// through the same handle.
///
/// WinVerifyTrust may read through the supplied handle itself, so the file
/// pointer is at an unspecified offset afterwards - possibly end of file. The
/// image read therefore rewinds to offset zero explicitly; otherwise the PE
/// reader would see a truncated or empty image and could mis-report a valid
/// carrier.
struct ReadOnlyFile(HANDLE);

impl ReadOnlyFile {
    fn open(path: &Path) -> InstallResult<Self> {
        let wide = wide_path(path)?;
        // SAFETY: `wide` is a NUL-terminated UTF-16 path that outlives the call.
        let handle = unsafe {
            CreateFileW(
                PCWSTR(wide.as_ptr()),
                GENERIC_READ.0,
                FILE_SHARE_READ,
                None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                None,
            )
        }
        .map_err(|_| io_failure())?;
        Ok(Self(handle))
    }

    /// Take ownership of an already-opened, already-checked handle.
    fn adopt(file: File) -> Self {
        use std::os::windows::io::IntoRawHandle;

        Self(HANDLE(file.into_raw_handle()))
    }

    fn read_all(&self) -> InstallResult<Vec<u8>> {
        // `WinVerifyTrust` reads through the handle it is given, so the shared
        // file pointer is at an unknown offset (in practice often EOF) when it
        // returns. Rewind explicitly: `ReadFile` would otherwise report zero
        // bytes and the carrier resource could not be located.
        let mut position = 0i64;
        // SAFETY: the handle is valid and `position` is a live out-parameter.
        unsafe { SetFilePointerEx(self.0, 0, Some(&mut position), FILE_BEGIN) }
            .map_err(|_| io_failure())?;
        if position != 0 {
            return Err(io_failure());
        }
        let mut size = 0i64;
        // SAFETY: the handle is valid and `size` is a live out-parameter.
        unsafe { GetFileSizeEx(self.0, &mut size) }.map_err(|_| io_failure())?;
        if size <= 0 || size as u64 > MAX_CARRIER_IMAGE_BYTES as u64 {
            return Err(invalid_package());
        }
        let mut bytes = vec![0u8; size as usize];
        let mut filled = 0usize;
        while filled < bytes.len() {
            let mut read = 0u32;
            // SAFETY: the destination slice and out-parameter are live for the
            // duration of the call, and the handle is open for reading.
            unsafe { ReadFile(self.0, Some(&mut bytes[filled..]), Some(&mut read), None) }
                .map_err(|_| io_failure())?;
            if read == 0 {
                return Err(io_failure());
            }
            filled += read as usize;
        }
        Ok(bytes)
    }
}

impl Drop for ReadOnlyFile {
    fn drop(&mut self) {
        // SAFETY: the handle was returned by CreateFileW and is closed once.
        unsafe {
            let _ = CloseHandle(self.0);
        }
    }
}

/// Classify a WinVerifyTrust status into the installer vocabulary.
fn classify(status: i32) -> InstallFailure {
    trust_status::classify_winverifytrust_status(status).failure()
}

fn foreign_state() -> InstallFailure {
    InstallFailure::new(FailureCode::ForeignState)
}

fn unsupported() -> InstallFailure {
    InstallFailure::new(FailureCode::UnsupportedTarget)
}

fn invalid_input() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidInput)
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

fn trust_denied() -> InstallFailure {
    InstallFailure::new(FailureCode::TrustDenied)
}

// ---------------------------------------------------------------------------
// Task 8: native filesystem and HKCU primitives.
// ---------------------------------------------------------------------------

/// HKCU value names are the unnamed/default value of the host key.
const REGISTRY_DEFAULT_VALUE: PCWSTR = PCWSTR::null();

/// Open one registry key, or `None` when it does not exist.
fn reg_open(key: &str, view: RegistryView, access: REG_SAM_FLAGS) -> InstallResult<Option<RegKey>> {
    let wide = registry_wide(key)?;
    let mut hkey = HKEY::default();
    // SAFETY: `wide` is NUL-terminated and outlives the call; `hkey` is a live
    // out-parameter and is closed by `RegKey`.
    let status = unsafe {
        RegOpenKeyExW(
            HKEY_CURRENT_USER,
            PCWSTR(wide.as_ptr()),
            None,
            access | view.flags(),
            &mut hkey,
        )
    };
    if status == ERROR_FILE_NOT_FOUND || status == ERROR_PATH_NOT_FOUND {
        return Ok(None);
    }
    if status.0 != 0 {
        return Err(io_failure());
    }
    Ok(Some(RegKey(hkey)))
}

/// One open HKCU key, closed exactly once.
struct RegKey(HKEY);

impl Drop for RegKey {
    fn drop(&mut self) {
        // SAFETY: the handle was returned by a successful registry open/create
        // and is owned by this value.
        unsafe {
            let _ = RegCloseKey(self.0);
        }
    }
}

/// The default value of `key` in one view, or `None` when absent.
fn read_scope_value(key: &str, view: RegistryView) -> InstallResult<Option<String>> {
    let Some(key) = reg_open(key, view, KEY_QUERY_VALUE)? else {
        return Ok(None);
    };
    let mut value_type = REG_VALUE_TYPE(0);
    let mut size = 0u32;
    // SAFETY: a null data buffer with the size out-parameter only asks for the
    // required length.
    let status = unsafe {
        RegQueryValueExW(
            key.0,
            REGISTRY_DEFAULT_VALUE,
            None,
            Some(&mut value_type),
            None,
            Some(&mut size),
        )
    };
    if status == ERROR_FILE_NOT_FOUND {
        return Ok(None);
    }
    if status.0 != 0 {
        return Err(io_failure());
    }
    if value_type != REG_SZ || size == 0 || size as usize > MAX_MANIFEST_PATH_BYTES * 2 + 2 {
        // A non-string or implausibly large value is not a Native Messaging
        // registration we can interpret; never guess.
        return Err(foreign_state());
    }
    let mut buffer = vec![0u8; size as usize];
    let mut written = size;
    // SAFETY: `buffer` is live and `written` is its length in bytes on entry.
    let status = unsafe {
        RegQueryValueExW(
            key.0,
            REGISTRY_DEFAULT_VALUE,
            None,
            Some(&mut value_type),
            Some(buffer.as_mut_ptr()),
            Some(&mut written),
        )
    };
    if status.0 != 0 || written > size || value_type != REG_SZ {
        return Err(io_failure());
    }
    buffer.truncate(written as usize);
    decode_registry_string(&buffer).map(Some)
}

/// Create the fixed key and write its default value durably.
fn write_scope_value(key: &str, value: &str) -> InstallResult<()> {
    let wide = registry_wide(key)?;
    let mut hkey = HKEY::default();
    // SAFETY: `wide` is NUL-terminated and outlives the call; `hkey` is a live
    // out-parameter and is closed by `RegKey`.
    let status = unsafe {
        RegCreateKeyExW(
            HKEY_CURRENT_USER,
            PCWSTR(wide.as_ptr()),
            None,
            PCWSTR::null(),
            REG_OPTION_NON_VOLATILE,
            KEY_SET_VALUE | KEY_QUERY_VALUE,
            None,
            &mut hkey,
            None,
        )
    };
    if status.0 != 0 {
        return Err(io_failure());
    }
    let key = RegKey(hkey);
    // REG_SZ is UTF-16LE with one terminating NUL; the length is in bytes.
    let mut data: Vec<u8> = value.encode_utf16().flat_map(u16::to_le_bytes).collect();
    if data.len() > MAX_MANIFEST_PATH_BYTES * 2 {
        return Err(unsafe_path());
    }
    data.extend_from_slice(&[0, 0]);
    // SAFETY: `data` is live for the call and is a well-formed REG_SZ image.
    let status =
        unsafe { RegSetValueExW(key.0, REGISTRY_DEFAULT_VALUE, None, REG_SZ, Some(&data)) };
    if status.0 != 0 {
        return Err(io_failure());
    }
    // Make the value durable before the caller records the commit.
    // SAFETY: `key` is a live writable key handle.
    let status = unsafe { RegFlushKey(key.0) };
    if status.0 != 0 {
        return Err(io_failure());
    }
    Ok(())
}

/// Delete the default value and then the fixed key itself.
///
/// An absent value or key is already uninstalled. `RegDeleteKeyW` never removes
/// a key with subkeys, so this can never become a recursive registry delete.
fn delete_scope_key(key: &str) -> InstallResult<()> {
    if let Some(open) = reg_open(key, RegistryView::Default, KEY_SET_VALUE | KEY_QUERY_VALUE)? {
        // SAFETY: `open` is a live key handle with KEY_SET_VALUE.
        let status = unsafe { RegDeleteValueW(open.0, REGISTRY_DEFAULT_VALUE) };
        if status.0 != 0 && status != ERROR_FILE_NOT_FOUND {
            return Err(io_failure());
        }
    }
    let wide = registry_wide(key)?;
    // SAFETY: `wide` is NUL-terminated and outlives the call.
    let status = unsafe { RegDeleteKeyW(HKEY_CURRENT_USER, PCWSTR(wide.as_ptr())) };
    if status.0 != 0 && status != ERROR_FILE_NOT_FOUND && status != ERROR_PATH_NOT_FOUND {
        return Err(io_failure());
    }
    Ok(())
}

/// Decode a `REG_SZ` payload without lossy conversion.
fn decode_registry_string(bytes: &[u8]) -> InstallResult<String> {
    if !bytes.len().is_multiple_of(2) {
        return Err(foreign_state());
    }
    let mut units: Vec<u16> = Vec::with_capacity(bytes.len() / 2);
    let mut index = 0;
    while index < bytes.len() {
        units.push(u16::from_le_bytes([bytes[index], bytes[index + 1]]));
        index += 2;
    }
    while units.last() == Some(&0) {
        units.pop();
    }
    let value = String::from_utf16(&units).map_err(|_| foreign_state())?;
    if value.chars().any(char::is_control) {
        return Err(foreign_state());
    }
    Ok(value)
}

/// Whether two Windows manifest paths name the same value (case-insensitive).
fn same_windows_value(left: &str, right: &str) -> bool {
    left.eq_ignore_ascii_case(right)
}

/// A bounded, NUL-terminated UTF-16 registry key.
fn registry_wide(key: &str) -> InstallResult<Vec<u16>> {
    if key.is_empty() || key.len() > 1024 || key.chars().any(char::is_control) {
        return Err(unsafe_path());
    }
    Ok(key.encode_utf16().chain([0]).collect())
}

/// The Win32 error inside one encoded `HRESULT`.
fn win32_code(code: HRESULT) -> i32 {
    // `HRESULT_FROM_WIN32(err)` is `0x8007_0000 | err`; the low 16 bits are the
    // Win32 code. Anything that is not such an encoding is passed through.
    let raw = code.0 as u32;
    if raw & 0xFFFF_0000 == 0x8007_0000 {
        (raw & 0xFFFF) as i32
    } else {
        code.0
    }
}

/// Whether an open handle names a reparse point instead of the intended object.
fn is_reparse(file: &File) -> io::Result<bool> {
    use std::os::windows::fs::MetadataExt;

    Ok(file.metadata()?.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT.0 != 0)
}

// ---------------------------------------------------------------------------
// Anchored, handle-relative primitives and the Windows cleanup traversal root.
// ---------------------------------------------------------------------------

/// `NtCreateFile` dispositions used by the anchored opens.
const NT_FILE_OPEN: NTCREATEFILE_CREATE_DISPOSITION = NTCREATEFILE_CREATE_DISPOSITION(1);
const NT_FILE_CREATE: NTCREATEFILE_CREATE_DISPOSITION = NTCREATEFILE_CREATE_DISPOSITION(2);

/// Explicit `FILE_GENERIC_READ` (`READ_CONTROL | FILE_READ_DATA |
/// FILE_READ_ATTRIBUTES | FILE_READ_EA | SYNCHRONIZE`).
///
/// `NtCreateFile` is never handed the generic `GENERIC_READ` alias here: on a
/// real Windows runner a relative open with `RootDirectory` fails it with
/// `STATUS_INVALID_PARAMETER` (0xC000000D), while the explicit file rights open
/// the same object. Wine accepts the alias, which is why this only showed up on
/// the Windows runner.
const ACCESS_READ: u32 = 0x0012_0089;
/// `ACCESS_READ | DELETE`: enough to read and delete the object a handle names.
const ACCESS_RD: u32 = ACCESS_READ | 0x0001_0000;
/// `FILE_GENERIC_READ | FILE_GENERIC_WRITE | DELETE`: enough to read, write,
/// rename or delete the object a handle names.
const ACCESS_RWD: u32 = 0x0013_019f;

/// Open `name` relative to one already-opened directory handle.
///
/// The single component is resolved against `RootDirectory`, so no ancestor is
/// re-walked, and `FILE_OPEN_REPARSE_POINT` opens a link itself (the caller then
/// refuses its attribute) instead of following it.
fn open_below(
    parent: HANDLE,
    name: &OsStr,
    access: u32,
    disposition: NTCREATEFILE_CREATE_DISPOSITION,
    options: NTCREATEFILE_CREATE_OPTIONS,
) -> InstallResult<File> {
    match open_below_status(parent, name, access, disposition, options, ptr::null()) {
        Ok(file) => Ok(file),
        Err(status) => Err(map_open_status(status)),
    }
}

/// [`open_below`] with an optional `SECURITY_DESCRIPTOR` applied on creation and
/// the raw `NTSTATUS` returned, so a caller can distinguish "absent" from
/// "refused" (an access failure is never an absent entry).
fn open_below_status(
    parent: HANDLE,
    name: &OsStr,
    access: u32,
    disposition: NTCREATEFILE_CREATE_DISPOSITION,
    options: NTCREATEFILE_CREATE_OPTIONS,
    security: *const SECURITY_DESCRIPTOR,
) -> Result<File, NTSTATUS> {
    open_below_shared(
        parent,
        name,
        access,
        disposition,
        options,
        security,
        FILE_SHARE_DELETE | FILE_SHARE_READ | FILE_SHARE_WRITE,
    )
}

/// [`open_below_status`] with an explicit share mode. The permissive default lets
/// the installer walk and mutate its own trees; a handle whose byte-stability is
/// part of a trust claim passes `FILE_SHARE_READ` alone.
fn open_below_shared(
    parent: HANDLE,
    name: &OsStr,
    access: u32,
    disposition: NTCREATEFILE_CREATE_DISPOSITION,
    options: NTCREATEFILE_CREATE_OPTIONS,
    security: *const SECURITY_DESCRIPTOR,
    share: FILE_SHARE_MODE,
) -> Result<File, NTSTATUS> {
    let wide: Vec<u16> = name.encode_wide().collect();
    if wide.is_empty() || wide.contains(&0) {
        return Err(NTSTATUS(STATUS_OBJECT_NAME_INVALID as i32));
    }
    let unicode = UNICODE_STRING {
        Length: (wide.len() * 2) as u16,
        MaximumLength: (wide.len() * 2) as u16,
        Buffer: PWSTR(wide.as_ptr() as *mut u16),
    };
    let attributes = OBJECT_ATTRIBUTES {
        Length: size_of::<OBJECT_ATTRIBUTES>() as u32,
        RootDirectory: parent,
        ObjectName: &unicode,
        Attributes: OBJ_CASE_INSENSITIVE,
        SecurityDescriptor: security,
        SecurityQualityOfService: ptr::null(),
    };
    let mut status_block = IO_STATUS_BLOCK::default();
    let mut handle = HANDLE::default();
    // SAFETY: every pointer is live for the call, the name/attributes outlive it
    // and no EA buffer is supplied.
    let status = unsafe {
        NtCreateFile(
            &mut handle,
            FILE_ACCESS_RIGHTS(access),
            &attributes,
            &mut status_block,
            None,
            Default::default(),
            share,
            disposition,
            options | FILE_OPEN_REPARSE_POINT | FILE_SYNCHRONOUS_IO_NONALERT,
            None,
            0,
        )
    };
    if status.0 < 0 || handle.is_invalid() {
        return Err(status);
    }
    // SAFETY: the handle was just opened and ownership is transferred here.
    Ok(unsafe { File::from_raw_handle(handle.0) })
}

/// Anchored read-only traversal of `parts` below `root` (package inventory reads).
///
/// The root is opened once with `FILE_FLAG_OPEN_REPARSE_POINT` (a reparse point
/// there is refused), then each component is opened relative to its parent's handle
/// with `NtCreateFile` + `RootDirectory` and `FILE_OPEN_REPARSE_POINT`, and every
/// handle is checked not to be a reparse point nor the wrong kind of object before
/// it becomes the next anchor. A missing component is `InvalidPackage`. `between`
/// runs after the last ancestor is open and before the leaf is.
///
/// NOT RUN on this host: it compiles for `x86_64-pc-windows-msvc` only.
pub(crate) fn anchored_open_regular_below(
    root: &Path,
    parts: &[&str],
    between: &mut dyn FnMut(),
) -> InstallResult<File> {
    anchored_open_below(
        root,
        parts,
        between,
        FILE_SHARE_DELETE | FILE_SHARE_READ | FILE_SHARE_WRITE,
    )
}

/// [`anchored_open_regular_below`] for a file WinVerifyTrust is about to assess.
///
/// The lookup is the same anchored, no-follow walk, but the final file is opened
/// with `FILE_SHARE_READ` only - the sharing the pre-anchoring assessment handle
/// ([`ReadOnlyFile::open`]) had - so no writer, deleter or renamer can change the
/// assessed bytes while the handle is held, and a concurrently writable host is
/// refused. Ancestor directories stay permissive: they are anchors, not content.
///
/// NOT RUN on this host: it compiles for `x86_64-pc-windows-msvc` only.
pub(crate) fn anchored_open_assessment_below(
    root: &Path,
    parts: &[&str],
    between: &mut dyn FnMut(),
) -> InstallResult<File> {
    anchored_open_below(root, parts, between, FILE_SHARE_READ)
}

fn anchored_open_below(
    root: &Path,
    parts: &[&str],
    between: &mut dyn FnMut(),
    leaf_share: FILE_SHARE_MODE,
) -> InstallResult<File> {
    use std::os::windows::fs::OpenOptionsExt;

    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
    let mut current = fs::OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ.0 | FILE_SHARE_WRITE.0 | FILE_SHARE_DELETE.0)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT.0)
        .open(root)
        .map_err(|_| io_failure())?;
    if is_reparse(&current).map_err(|_| io_failure())?
        || !current.metadata().is_ok_and(|m| m.is_dir())
    {
        return Err(unsafe_path());
    }
    let Some((leaf, ancestors)) = parts.split_last() else {
        return Err(unsafe_path());
    };
    let open = |parent: &File, name: &str, directory: bool| -> InstallResult<File> {
        let kind = if directory {
            FILE_DIRECTORY_FILE
        } else {
            FILE_NON_DIRECTORY_FILE
        };
        let share = if directory {
            FILE_SHARE_DELETE | FILE_SHARE_READ | FILE_SHARE_WRITE
        } else {
            leaf_share
        };
        let file = open_below_shared(
            HANDLE(parent.as_raw_handle()),
            OsStr::new(name),
            ACCESS_READ,
            NT_FILE_OPEN,
            kind | FILE_OPEN_FOR_BACKUP_INTENT,
            ptr::null(),
            share,
        )
        .map_err(|status| {
            if nt_status_is_absent(status) {
                InstallFailure::new(FailureCode::InvalidPackage)
            } else {
                // A directory open of a file (or the reverse) is a kind mismatch,
                // not an absent entry.
                match status.0 as u32 {
                    // STATUS_NOT_A_DIRECTORY / STATUS_FILE_IS_A_DIRECTORY.
                    0xC000_0103 | 0xC000_00BA => unsafe_path(),
                    _ => io_failure(),
                }
            }
        })?;
        if is_reparse(&file).map_err(|_| io_failure())? {
            return Err(unsafe_path());
        }
        Ok(file)
    };
    for name in ancestors {
        current = open(&current, name, true)?;
    }
    between();
    let file = open(&current, leaf, false)?;
    if !file.metadata().map_err(|_| io_failure())?.is_file() {
        return Err(unsafe_path());
    }
    Ok(file)
}

/// `STATUS_OBJECT_NAME_INVALID`.
const STATUS_OBJECT_NAME_INVALID: u32 = 0xC000_0033;

/// Whether one `NTSTATUS` means the name simply does not exist.
///
/// Only these statuses may be reported as an absent entry: an access or sharing
/// failure means the entry could not be *looked at*, which is never "already
/// gone" (reporting it as absent would let a cleanup claim completion while owned
/// entries remain).
fn nt_status_is_absent(status: NTSTATUS) -> bool {
    matches!(
        status.0 as u32,
        // STATUS_OBJECT_NAME_NOT_FOUND / STATUS_OBJECT_PATH_NOT_FOUND / no such file.
        0xC000_0034 | 0xC000_003A | 0xC000_000F
    )
}

/// Map the raw status of a traversal open into the installer vocabulary.
///
/// An access failure maps to `CleanupPending` — the entry exists but could not be
/// examined, so nothing may be claimed about it — while only a genuine not-found
/// status maps to `UnsafePath` (the code the traversal reads as "absent").
fn map_open_status(status: NTSTATUS) -> InstallFailure {
    if nt_status_is_absent(status) {
        return unsafe_path();
    }
    match status.0 as u32 {
        // STATUS_ACCESS_DENIED / STATUS_SHARING_VIOLATION / STATUS_DELETE_PENDING.
        0xC000_0022 | 0xC000_0043 | 0xC000_0056 => InstallFailure::new(FailureCode::CleanupPending),
        _ => map_nt_status(status),
    }
}

/// Map one `NTSTATUS` to the installer vocabulary.
fn map_nt_status(status: NTSTATUS) -> InstallFailure {
    match status.0 as u32 {
        // STATUS_OBJECT_NAME_NOT_FOUND / STATUS_OBJECT_PATH_NOT_FOUND / no such file.
        0xC000_0034 | 0xC000_003A | 0xC000_000F => unsafe_path(),
        // STATUS_OBJECT_NAME_COLLISION (create-new on an existing name).
        0xC000_0035 => unsafe_path(),
        // STATUS_ACCESS_DENIED.
        0xC000_0022 => unsafe_path(),
        // STATUS_DIRECTORY_NOT_EMPTY (removing a non-empty directory).
        0xC000_0101 => InstallFailure::new(FailureCode::CleanupPending),
        _ => io_failure(),
    }
}

/// Rename one opened entry relative to two directory handles.
///
/// The staged object's **handle** is renamed, so the destination name is resolved
/// against `live_parent` and the source object cannot be swapped between the
/// identity check and the rename.
fn rename_below(staged: &File, live_parent: HANDLE, live_name: &OsStr) -> InstallResult<()> {
    let wide: Vec<u16> = live_name.encode_wide().collect();
    if wide.is_empty() {
        return Err(unsafe_path());
    }
    let header = size_of::<FILE_RENAME_INFORMATION>();
    let bytes = header + wide.len() * 2;
    let words = bytes.div_ceil(size_of::<usize>());
    let mut buffer = vec![0usize; words];
    let information = buffer.as_mut_ptr().cast::<FILE_RENAME_INFORMATION>();
    // SAFETY: `buffer` is at least `bytes` long and word-aligned; the structure
    // followed by the name fits inside it.
    unsafe {
        (*information).Anonymous.Flags = FILE_RENAME_REPLACE_IF_EXISTS;
        (*information).RootDirectory = live_parent;
        (*information).FileNameLength = (wide.len() * 2) as u32;
        ptr::copy_nonoverlapping(
            wide.as_ptr(),
            (*information).FileName.as_mut_ptr(),
            wide.len(),
        );
    }
    let mut status_block = IO_STATUS_BLOCK::default();
    // SAFETY: `staged` is a live handle with DELETE access and the information
    // buffer is correctly sized and aligned.
    let status = unsafe {
        NtSetInformationFile(
            HANDLE(staged.as_raw_handle()),
            &mut status_block,
            information.cast(),
            bytes as u32,
            FileRenameInformation,
        )
    };
    if status.0 < 0 {
        return Err(map_nt_status(status));
    }
    Ok(())
}

/// Mark one opened object for deletion when its last handle closes.
///
/// Deleting through the handle is bound to the object that was opened and
/// verified: no name is resolved for the unlink, so a leaf swapped after
/// validation is simply not the object that gets deleted.
fn delete_opened(file: &File) -> InstallResult<()> {
    let mut information = FILE_DISPOSITION_INFO { DeleteFile: true };
    // SAFETY: `file` is a live handle with DELETE access and `information` is a
    // correctly sized FILE_DISPOSITION_INFO.
    let result = unsafe {
        SetFileInformationByHandle(
            HANDLE(file.as_raw_handle()),
            FileDispositionInfo,
            (&mut information as *mut FILE_DISPOSITION_INFO).cast(),
            size_of::<FILE_DISPOSITION_INFO>() as u32,
        )
    };
    match result {
        Ok(()) => Ok(()),
        Err(error) if win32_code(error.code()) == 145 => {
            // ERROR_DIR_NOT_EMPTY: preserved, never a recursive delete.
            Err(InstallFailure::new(FailureCode::CleanupPending))
        }
        Err(_) => Err(io_failure()),
    }
}

/// Mark one opened object for deletion when its handle closes.
///
/// The lock is a byte-range-locked file on Windows; deleting it by name while the
/// lock is held fails with `ERROR_LOCK_VIOLATION`. Marking the opened handle
/// delete-on-close removes the entry atomically with the close, without a window
/// in which the name is free and the exclusive lock is no longer held.
pub(crate) fn mark_delete_on_close(file: &File) -> InstallResult<()> {
    delete_opened(file)
}

/// Open, verify and delete one leaf relative to its validated parent handle.
fn remove_below(path: &ValidatedPath) -> InstallResult<()> {
    let handle = path.dir_handle()?;
    let options = match path.kind() {
        PathKind::File | PathKind::Symlink => FILE_NON_DIRECTORY_FILE,
        PathKind::Directory => FILE_DIRECTORY_FILE,
        _ => return Err(unsafe_path()),
    };
    let file = open_below(
        handle.handle(),
        path.leaf_name()?,
        ACCESS_RD,
        NT_FILE_OPEN,
        options | FILE_OPEN_FOR_BACKUP_INTENT,
    )?;
    let metadata = file.metadata().map_err(|_| io_failure())?;
    path.verify_open(&metadata, &crate::platform::identity_of_file(&file)?)?;
    delete_opened(&file)
}

/// An opened install root the cleanup helper deletes exact identities through.
///
/// The root is opened by walking its **whole** ancestor chain from its volume
/// root, one component at a time, with `FILE_OPEN_REPARSE_POINT`, so a reparse
/// point above the root cannot redirect the traversal; every entry is then
/// resolved with `NtCreateFile` + `RootDirectory` from this handle. Deletion is
/// the handle-bound `FileDispositionInfo` on the object whose identity was
/// checked, so no name is re-resolved for the unlink.
pub struct CleanupRoot {
    directory: File,
}

impl CleanupRoot {
    /// Open `root` as an anchored directory handle.
    ///
    /// Only an installer-owned layout directory can be a cleanup root, and it is
    /// opened from its volume root component by component without traversing a
    /// reparse point.
    pub fn open(root: &Path) -> io::Result<Self> {
        if !super::is_layout_root(root) {
            return Err(io::Error::from(io::ErrorKind::InvalidInput));
        }
        let handle = super::native::anchor::open_anchored(root)
            .map_err(|_| io::Error::from(io::ErrorKind::InvalidInput))?;
        Ok(Self {
            directory: handle.into_file(),
        })
    }

    /// Resolve one relative entry from the anchored root, one component at a time.
    fn open_entry(&self, relative: &str) -> InstallResult<Option<File>> {
        let normalized =
            crate::inventory::normalize_relative_path(relative).map_err(|_| unsafe_path())?;
        let parts: Vec<&str> = normalized.split('/').collect();
        let mut current = self.directory.try_clone().map_err(|_| io_failure())?;
        for (index, part) in parts.iter().enumerate() {
            let last = index + 1 == parts.len();
            let name = OsStr::new(part);
            let options = if last {
                FILE_OPEN_FOR_BACKUP_INTENT
            } else {
                FILE_DIRECTORY_FILE | FILE_OPEN_FOR_BACKUP_INTENT
            };
            match open_below_status(
                HANDLE(current.as_raw_handle()),
                name,
                ACCESS_RD,
                NT_FILE_OPEN,
                options,
                ptr::null(),
            ) {
                Ok(next) => {
                    // A reparse point is opened as itself (never followed). An
                    // intermediate one would otherwise be traversed by the next
                    // component, so it is refused here.
                    if !last && is_reparse(&next).map_err(|_| io_failure())? {
                        return Err(unsafe_path());
                    }
                    current = next;
                }
                // Only a genuine not-found status is "already gone" — for the
                // leaf and for an intermediate component (a retried request whose
                // directory was already removed). An access denial, a sharing
                // violation or any other traversal failure means the entry could
                // not be examined: it is reported as preserved (never as absent),
                // so the run can never claim completion while an owned entry may
                // still be there.
                Err(status) if nt_status_is_absent(status) => return Ok(None),
                Err(status) => return Err(map_open_status(status)),
            }
        }
        Ok(Some(current))
    }

    /// Remove one regular file with exactly this size and content digest.
    pub fn remove_file(
        &mut self,
        relative: &str,
        size: u64,
        sha256: Digest,
        deadline: Instant,
    ) -> RemoveOutcome {
        let file = match self.open_entry(relative) {
            Ok(Some(file)) => file,
            Ok(None) => return RemoveOutcome::Absent,
            Err(_) => return RemoveOutcome::Preserved,
        };
        let Ok(metadata) = file.metadata() else {
            return RemoveOutcome::Preserved;
        };
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT.0 != 0
            || !metadata.is_file()
            || metadata.len() != size
        {
            return RemoveOutcome::Preserved;
        }
        match hash_open_file(&file, deadline) {
            Ok(Some(digest)) if digest == sha256 => {}
            Ok(Some(_)) => return RemoveOutcome::Preserved,
            Ok(None) => return RemoveOutcome::Deferred,
            Err(_) => return RemoveOutcome::Preserved,
        }
        match delete_opened(&file) {
            Ok(()) => RemoveOutcome::Removed,
            Err(_) => RemoveOutcome::Preserved,
        }
    }

    /// Windows carriers are single signed files, so an owned link entry can only
    /// be foreign content: it is preserved rather than guessed at.
    pub fn remove_link(
        &mut self,
        _relative: &str,
        _size: u64,
        _sha256: Digest,
        _deadline: Instant,
    ) -> RemoveOutcome {
        RemoveOutcome::Preserved
    }

    /// Remove one *empty* directory.
    pub fn remove_directory(&mut self, relative: &str) -> RemoveOutcome {
        let directory = match self.open_entry(relative) {
            Ok(Some(directory)) => directory,
            // Absent is idempotent; a directory that could not be opened is
            // preserved and reported, never claimed as removed.
            Ok(None) => return RemoveOutcome::Absent,
            Err(_) => return RemoveOutcome::Preserved,
        };
        let Ok(metadata) = directory.metadata() else {
            return RemoveOutcome::Preserved;
        };
        if !metadata.is_dir() || is_reparse(&directory).unwrap_or(true) {
            return RemoveOutcome::Preserved;
        }
        match delete_opened(&directory) {
            Ok(()) => RemoveOutcome::Removed,
            Err(failure) if failure.code == FailureCode::CleanupPending => RemoveOutcome::Preserved,
            Err(_) => RemoveOutcome::Preserved,
        }
    }
}

/// SHA-256 of one already-opened regular file; `Ok(None)` means the deadline
/// elapsed before the whole file was read.
///
/// The reads happen on a worker thread in bounded chunks that check the deadline
/// between them, and the caller waits with `recv_timeout`: a read that blocks
/// inside the OS (a stalled volume, a filter driver) therefore cannot extend the
/// cleanup run past its bound, because the caller stops waiting at the deadline
/// and reports the entry as deferred. The worker is abandoned when that happens;
/// the helper process exits immediately afterwards, so it cannot outlive the run.
fn hash_open_file(file: &File, deadline: Instant) -> InstallResult<Option<Digest>> {
    use std::io::{Read, Seek, SeekFrom};
    use std::sync::mpsc;

    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return Ok(None);
    }
    let mut file = file.try_clone().map_err(|_| io_failure())?;
    file.seek(SeekFrom::Start(0)).map_err(|_| io_failure())?;
    let (sender, receiver) = mpsc::channel();
    let spawned = std::thread::Builder::new()
        .name("cleanup-hash".to_owned())
        .spawn(move || {
            let mut hasher = Sha256::new();
            let mut buffer = [0u8; 64 * 1024];
            let outcome = loop {
                if Instant::now() >= deadline {
                    break Ok(None);
                }
                match file.read(&mut buffer) {
                    Ok(0) => break Ok(Some(Digest::from_bytes(hasher.finalize().into()))),
                    Ok(read) => hasher.update(&buffer[..read]),
                    Err(_) => break Err(io_failure()),
                }
            };
            let _ = sender.send(outcome);
        });
    if spawned.is_err() {
        return Err(io_failure());
    }
    match receiver.recv_timeout(remaining) {
        Ok(outcome) => outcome,
        // The deadline elapsed inside the worker (or the worker stalled): nothing
        // is claimed about this entry.
        Err(_) => Ok(None),
    }
}

/// The SID of the current process's user token.
fn current_user_sid() -> InstallResult<Vec<usize>> {
    let mut token = HANDLE::default();
    // SAFETY: `GetCurrentProcess` is a pseudo-handle and `token` is a live
    // out-parameter.
    unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) }
        .map_err(|_| io_failure())?;
    let token = OwnedHandle::new(token).ok_or_else(io_failure)?;
    let mut needed = 0u32;
    // SAFETY: a null buffer with length zero only asks for the size.
    let _ = unsafe { GetTokenInformation(token.0, TokenUser, None, 0, &mut needed) };
    if needed == 0 {
        return Err(io_failure());
    }
    let words = (needed as usize).div_ceil(size_of::<usize>());
    let mut buffer = vec![0usize; words];
    // SAFETY: the buffer is aligned and at least `needed` bytes long.
    unsafe {
        GetTokenInformation(
            token.0,
            TokenUser,
            Some(buffer.as_mut_ptr().cast()),
            (words * size_of::<usize>()) as u32,
            &mut needed,
        )
    }
    .map_err(|_| io_failure())?;
    Ok(buffer)
}

fn token_sid(buffer: &[usize]) -> PSID {
    // SAFETY: `GetTokenInformation(TokenUser)` wrote a complete `TOKEN_USER`.
    unsafe { (*buffer.as_ptr().cast::<TOKEN_USER>()).User.Sid }
}

/// A protected DACL granting full control to the creating user and nothing to
/// everybody else.
///
/// The owner is set **explicitly** to the current user's SID. A process running
/// under a full administrator token creates objects owned by
/// `BUILTIN\Administrators` by default; relying on the token's default owner would
/// therefore make `OW` grant every administrator, and the owner check below
/// would (correctly) reject a directory this installer just created. Naming the
/// user keeps the layout owner-only regardless of how the process token was
/// obtained. `(OI)(CI)` makes the DACL the inherited DACL of everything created
/// below the private root. The returned allocation is released with `LocalFree`.
fn owner_private_descriptor() -> InstallResult<PSECURITY_DESCRIPTOR> {
    let token = current_user_sid()?;
    let mut rendered = PWSTR::null();
    // SAFETY: the SID points into the live token buffer and `rendered` is a live
    // out-parameter; the returned string is a `LocalAlloc` allocation.
    unsafe { ConvertSidToStringSidW(token_sid(&token), &mut rendered) }
        .map_err(|_| io_failure())?;
    // SAFETY: `ConvertSidToStringSidW` wrote a NUL-terminated string.
    let sid = unsafe { rendered.to_string() };
    // SAFETY: `rendered` is the allocation returned by `ConvertSidToStringSidW`.
    unsafe {
        let _ = LocalFree(Some(HLOCAL(rendered.0.cast())));
    }
    let sid = sid.map_err(|_| io_failure())?;
    let sddl: Vec<u16> = format!("O:{sid}D:P(A;OICI;FA;;;OW)")
        .encode_utf16()
        .chain([0])
        .collect();
    let mut descriptor = PSECURITY_DESCRIPTOR::default();
    // SAFETY: `sddl` is NUL-terminated and `descriptor` is a live out-parameter
    // whose allocation is released by the caller.
    let converted = unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            PCWSTR(sddl.as_ptr()),
            SDDL_REVISION_1,
            &mut descriptor,
            None,
        )
    };
    if converted.is_err() {
        return Err(io_failure());
    }
    Ok(descriptor)
}

/// ACE type byte for a plain access-allowed ACE (`ACCESS_ALLOWED_ACE_TYPE`).
const ACCESS_ALLOWED_ACE_TYPE_LOCAL: u8 = 0;
/// ACE type byte for a plain access-denied ACE.
const ACCESS_DENIED_ACE_TYPE_LOCAL: u8 = 1;

/// Prove one existing directory is this user's and nobody else's.
///
/// * the owner must be the current user's SID;
/// * the DACL must exist (a `NULL` DACL grants everyone full control) and every
///   access-allowed ACE must name only the current user, the dynamic
///   `OWNER RIGHTS` SID (`S-1-3-4`) or `CREATOR OWNER` (`S-1-3-0`) — the three
///   principals the installer's own protected DACL can produce;
/// * an object ACE or an unreadable ACE is refused: it cannot be proved.
///
/// The descriptor is read from the **opened directory handle**, never from the
/// path: a name (or an ancestor) swapped after it was resolved cannot lend a
/// different object's security descriptor to this check.
fn validate_private_directory_handle(directory: &File) -> InstallResult<()> {
    use std::os::windows::io::AsRawHandle;
    use windows::Win32::Security::Authorization::{GetSecurityInfo, SE_FILE_OBJECT};
    use windows::Win32::Security::{
        ACL_SIZE_INFORMATION, AclSizeInformation, DACL_SECURITY_INFORMATION, GetAce,
        GetAclInformation, OWNER_SECURITY_INFORMATION,
    };

    const ACCESS_ALLOWED_OBJECT_ACE_TYPE: u8 = 5;
    const ACCESS_ALLOWED_CALLBACK_ACE_TYPE: u8 = 9;
    const ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE: u8 = 11;
    let mut owner = PSID::default();
    let mut dacl: *mut ACL = ptr::null_mut();
    let mut descriptor = PSECURITY_DESCRIPTOR::default();
    // SAFETY: the handle is live and owns the directory; the out-parameters are
    // live and the returned descriptor is released below.
    let status = unsafe {
        GetSecurityInfo(
            HANDLE(directory.as_raw_handle()),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            Some(&mut owner),
            None,
            Some(&mut dacl),
            None,
            Some(&mut descriptor),
        )
    };
    if status.0 != 0 {
        return Err(io_failure());
    }
    let result = (|| {
        let own = current_user_sid()?;
        let own_sid = token_sid(&own);
        // SAFETY: every SID points into a live, OS-filled buffer.
        unsafe {
            if !IsValidSid(owner).as_bool()
                || !IsValidSid(own_sid).as_bool()
                || EqualSid(owner, own_sid).is_err()
            {
                return Err(unsafe_path());
            }
        }
        if dacl.is_null() {
            // A NULL DACL grants full control to everyone.
            return Err(unsafe_path());
        }
        let mut information = ACL_SIZE_INFORMATION::default();
        // SAFETY: `dacl` is a live ACL and `information` is correctly sized.
        unsafe {
            GetAclInformation(
                dacl,
                (&mut information as *mut ACL_SIZE_INFORMATION).cast(),
                size_of::<ACL_SIZE_INFORMATION>() as u32,
                AclSizeInformation,
            )
        }
        .map_err(|_| io_failure())?;
        for index in 0..information.AceCount {
            let mut ace = ptr::null_mut::<core::ffi::c_void>();
            // SAFETY: `index` is below the ACE count and `ace` is a live
            // out-parameter.
            unsafe { GetAce(dacl, index, &mut ace) }.map_err(|_| io_failure())?;
            if ace.is_null() {
                return Err(io_failure());
            }
            // SAFETY: `ace` points at a complete ACE_HEADER inside the ACL.
            let header = unsafe { ptr::read_unaligned(ace.cast::<ACE_HEADER>()) };
            match header.AceType {
                ACCESS_ALLOWED_ACE_TYPE_LOCAL => {
                    // The SID is stored inline *inside the ACL*, not inside a
                    // copied struct: take its address from the ACE itself.
                    let sid = PSID(
                        // SAFETY: the ACE header says this is an access-allowed
                        // ACE, so an inline SID follows the mask.
                        unsafe {
                            ace.cast::<u8>()
                                .add(std::mem::offset_of!(ACCESS_ALLOWED_ACE, SidStart))
                        }
                        .cast(),
                    );
                    // The installer's own protected DACL grants access to `OW`
                    // (the dynamic `OWNER RIGHTS` SID), so that SID and
                    // `CREATOR OWNER` are accepted alongside this user's SID.
                    // SAFETY: the ACE declares an inline SID; both helpers
                    // validate it before reading.
                    let permitted = unsafe {
                        IsValidSid(sid).as_bool()
                            && (EqualSid(sid, own_sid).is_ok() || is_dynamic_owner_sid(sid))
                    };
                    if !permitted {
                        return Err(unsafe_path());
                    }
                }
                // Audit/alarm ACEs grant no access; deny ACEs only restrict.
                ACCESS_DENIED_ACE_TYPE_LOCAL => {}
                ACCESS_ALLOWED_OBJECT_ACE_TYPE
                | ACCESS_ALLOWED_CALLBACK_ACE_TYPE
                | ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE => return Err(unsafe_path()),
                _ => {}
            }
        }
        Ok(())
    })();
    // SAFETY: `descriptor` is the allocation returned by GetNamedSecurityInfoW.
    unsafe {
        let _ = LocalFree(Some(HLOCAL(descriptor.0)));
    }
    result
}

/// Create an outside-root staging directory with the installer's protected
/// owner-only DACL applied by the creation call itself.
///
/// The stage lives in the shared temporary directory, outside the install root, so
/// it cannot inherit the private layout's descriptor. `CreateDirectoryW` takes the
/// owner-only descriptor atomically; [`validate_private_staging_directory`] then
/// proves the owner and DACL from an opened, no-follow handle before any image is
/// copied. NOT RUN on this Linux host: it compiles for `x86_64-pc-windows-msvc`.
pub(crate) fn create_private_staging_directory(path: &Path) -> InstallResult<()> {
    let descriptor = owner_private_descriptor()?;
    let attributes = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor.0,
        bInheritHandle: windows::core::BOOL(0),
    };
    let wide = wide_path(path)?;
    // SAFETY: `wide` is NUL-terminated and outlives the call; `attributes` holds a
    // live descriptor for the duration of the call.
    let created = unsafe { CreateDirectoryW(PCWSTR(wide.as_ptr()), Some(&attributes)) };
    // SAFETY: `descriptor` is the allocation returned by the conversion.
    unsafe {
        let _ = LocalFree(Some(HLOCAL(descriptor.0)));
    }
    created.map_err(|_| io_failure())?;
    if let Err(failure) = validate_private_staging_directory(path) {
        // We just created this directory, so removing the (still empty) name is
        // our own cleanup, never a foreign delete.
        let _ = fs::remove_dir(path);
        return Err(failure);
    }
    Ok(())
}

/// Prove an outside-root staging directory is this user's and nobody else's.
///
/// The descriptor is read from the opened directory handle, never from the path,
/// so a name swapped after resolution cannot lend another object's security
/// descriptor. NOT RUN on this Linux host.
pub(crate) fn validate_private_staging_directory(path: &Path) -> InstallResult<()> {
    use std::os::windows::fs::OpenOptionsExt;

    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
    let directory = fs::OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ.0 | FILE_SHARE_WRITE.0 | FILE_SHARE_DELETE.0)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT.0)
        .open(path)
        .map_err(|_| io_failure())?;
    if is_reparse(&directory).map_err(|_| io_failure())?
        || !directory.metadata().map_err(|_| io_failure())?.is_dir()
    {
        return Err(unsafe_path());
    }
    validate_private_directory_handle(&directory)
}

/// The raw bytes of `S-1-3-4` (`OWNER RIGHTS`) and `S-1-3-0` (`CREATOR OWNER`).
///
/// Both are *dynamic* SIDs: they name the object owner rather than a particular
/// account, so they cannot be compared with the current user's SID. A
/// one-subauthority SID is 12 bytes (`S-1-3-4` / `S-1-3-0`), compared directly
/// instead of relying on a well-known-SID table this crate does not expose.
const OWNER_RIGHTS_SID_BYTES: [u8; 12] = [1, 1, 0, 0, 0, 0, 0, 3, 4, 0, 0, 0];
const CREATOR_OWNER_SID_BYTES: [u8; 12] = [1, 1, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0];

/// Whether `sid` is the dynamic `OWNER RIGHTS` or `CREATOR OWNER` SID.
fn is_dynamic_owner_sid(sid: PSID) -> bool {
    if sid.0.is_null() {
        return false;
    }
    // SAFETY: the caller obtained `sid` from a parsed ACE; `IsValidSid` bounds
    // the read below to a well-formed one-subauthority SID (12 bytes), and any
    // longer SID differs in its subauthority count byte.
    unsafe {
        if !IsValidSid(sid).as_bool() {
            return false;
        }
        let bytes = std::slice::from_raw_parts(sid.0.cast::<u8>(), 12);
        bytes == OWNER_RIGHTS_SID_BYTES || bytes == CREATOR_OWNER_SID_BYTES
    }
}

impl NativeFs for WindowsPlatform {
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock> {
        native_ensure_layout(self, paths)?;
        // The lock file is opened **relative to the anchored state directory**:
        // its whole ancestor chain is opened component by component with
        // `FILE_OPEN_REPARSE_POINT`, so neither the state directory nor the lock
        // name can be redirected between validation and the open. This parents the
        // lock at the same `state/installer.lock` path teardown removes.
        let state = super::native::anchor::open_anchored(paths.state())?;
        let name = paths
            .lock_path()
            .file_name()
            .ok_or_else(unsafe_path)?
            .to_os_string();
        // Exclusive sharing makes a second installer a `Busy`; `OPEN_ALWAYS`
        // semantics are `open`, then `create` when the fixed name is absent.
        let file = match open_below(
            state.handle(),
            &name,
            ACCESS_RWD,
            NT_FILE_OPEN,
            FILE_NON_DIRECTORY_FILE | FILE_OPEN_FOR_BACKUP_INTENT,
        ) {
            Ok(file) => file,
            Err(failure) if failure.code == FailureCode::UnsafePath => open_below(
                state.handle(),
                &name,
                ACCESS_RWD,
                NT_FILE_CREATE,
                FILE_NON_DIRECTORY_FILE | FILE_OPEN_FOR_BACKUP_INTENT,
            )?,
            Err(failure) => return Err(failure),
        };
        if is_reparse(&file).map_err(|_| io_failure())? {
            return Err(unsafe_path());
        }
        if !file.metadata().map_err(|_| io_failure())?.is_file() {
            return Err(unsafe_path());
        }
        match file.try_lock() {
            Ok(()) => Ok(InstallLock {
                path: paths.lock_path().to_path_buf(),
                file,
            }),
            Err(TryLockError::WouldBlock) => Err(InstallFailure::new(FailureCode::Busy)),
            Err(_) => Err(io_failure()),
        }
    }

    fn validate_private_directory(&self, path: &Path) -> InstallResult<()> {
        // The directory is opened relative to an anchored parent handle and the
        // owner/DACL are read from **that handle**, so a path swapped after the
        // check cannot lend its security descriptor to what is validated.
        let parent = path.parent().ok_or_else(unsafe_path)?;
        let name = path.file_name().ok_or_else(unsafe_path)?.to_os_string();
        let handle = super::native::anchor::open_anchored(parent)?;
        let directory = open_below(
            handle.handle(),
            &name,
            ACCESS_RD,
            NT_FILE_OPEN,
            FILE_DIRECTORY_FILE | FILE_OPEN_FOR_BACKUP_INTENT,
        )?;
        validate_private_directory_handle(&directory)
    }

    fn write_durable(
        &self,
        path: &ValidatedPath,
        bytes: &[u8],
    ) -> InstallResult<crate::platform::FileIdentity> {
        let (disposition, truncate) = match path.kind() {
            PathKind::NewFile => (NT_FILE_CREATE, false),
            PathKind::File => (NT_FILE_OPEN, true),
            _ => return Err(unsafe_path()),
        };
        let handle = path.dir_handle()?;
        let mut file = open_below(
            handle.handle(),
            path.leaf_name()?,
            ACCESS_RWD,
            disposition,
            FILE_NON_DIRECTORY_FILE | FILE_OPEN_FOR_BACKUP_INTENT,
        )?;
        let outcome = (|| {
            if is_reparse(&file).map_err(|_| io_failure())? {
                return Err(unsafe_path());
            }
            let metadata = file.metadata().map_err(|_| io_failure())?;
            let identity = crate::platform::identity_of_file(&file)?;
            if path.kind() == PathKind::File {
                // Identity before mutation: the truncated/written object is the
                // one validation captured.
                path.verify_open(&metadata, &identity)?;
            } else if !metadata.is_file() {
                return Err(unsafe_path());
            }
            if truncate {
                file.set_len(0).map_err(|_| io_failure())?;
            }
            file.write_all(bytes).map_err(|_| io_failure())?;
            file.sync_all().map_err(|_| io_failure())?;
            read_back(&mut file, bytes).map_err(|_| io_failure())?;
            Ok(identity)
        })();
        if outcome.is_err() && !truncate {
            // Remove only the object this call created, through its own handle: a
            // file somebody else planted at the same name is not ours to delete.
            let _ = delete_opened(&file);
        }
        outcome
    }

    fn replace_file(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        let staged_handle = staged.dir_handle()?;
        let live_handle = live.dir_handle()?;
        let staged_file = open_below(
            staged_handle.handle(),
            staged.leaf_name()?,
            ACCESS_RWD,
            NT_FILE_OPEN,
            FILE_NON_DIRECTORY_FILE | FILE_OPEN_FOR_BACKUP_INTENT,
        )?;
        let metadata = staged_file.metadata().map_err(|_| io_failure())?;
        staged.verify_open(&metadata, &crate::platform::identity_of_file(&staged_file)?)?;
        rename_below(&staged_file, live_handle.handle(), live.leaf_name()?)
    }

    fn read_file(&self, path: &Path, limit: u64) -> InstallResult<Vec<u8>> {
        crate::platform::read_no_follow_capped(path, limit).map_err(|_| io_failure())
    }

    fn hash_file(&self, path: &Path, limit: u64) -> InstallResult<crate::platform::HashedFile> {
        crate::platform::hash_file_bounded(path, limit)
    }

    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()> {
        let handle = path.dir_handle()?;
        // Windows has **no documented API that flushes a directory entry**:
        // `FlushFileBuffers` is documented for files. Directory durability is
        // therefore not part of this adapter's contract and no caller may infer
        // it. The barrier is issued only as an ordering step; a refusal is
        // `CleanupPending`, never a silent success, and the journal/receipt
        // re-examines the entry on the next run instead of assuming it survived.
        // SAFETY: the handle is a live directory handle opened with write access.
        unsafe { FlushFileBuffers(handle.handle()) }
            .map_err(|_| InstallFailure::new(FailureCode::CleanupPending))
    }

    fn create_directory(&self, path: &Path) -> InstallResult<()> {
        // Create the fixed component **relative to an anchored parent handle**:
        // the parent chain is opened component by component without traversing a
        // reparse point, so a swapped ancestor cannot redirect where the directory
        // is created.
        let parent = path.parent().ok_or_else(unsafe_path)?;
        let name = path.file_name().ok_or_else(unsafe_path)?.to_os_string();
        let handle = super::native::anchor::open_anchored(parent)?;
        let descriptor = owner_private_descriptor()?;
        let created = open_below_status(
            handle.handle(),
            &name,
            ACCESS_RWD,
            NT_FILE_CREATE,
            FILE_DIRECTORY_FILE | FILE_OPEN_FOR_BACKUP_INTENT,
            descriptor.0.cast(),
        )
        .map_err(|status| {
            if status.0 as u32 == 0xC000_0035 {
                // STATUS_OBJECT_NAME_COLLISION: an existing directory is never
                // adopted.
                unsafe_path()
            } else {
                map_open_status(status)
            }
        })?;
        // SAFETY: `descriptor` is the allocation returned by the conversion.
        unsafe {
            let _ = LocalFree(Some(HLOCAL(descriptor.0)));
        }
        validate_private_directory_handle(&created)?;
        drop(created);
        // The barrier on the anchored parent follows the same contract as
        // `flush_parent`: it is an ordering step only, never a claimed
        // directory-entry durability guarantee on Windows.
        // SAFETY: the handle is a live directory handle opened with write access.
        unsafe { FlushFileBuffers(handle.handle()) }
            .map_err(|_| InstallFailure::new(FailureCode::CleanupPending))
    }

    fn create_directory_at(&self, path: &ValidatedPath) -> InstallResult<()> {
        if path.kind() != PathKind::NewDirectory {
            return Err(invalid_input());
        }
        path.revalidate()?;
        let handle = path.dir_handle()?;
        let created = open_below(
            handle.handle(),
            path.leaf_name()?,
            ACCESS_RD,
            NT_FILE_CREATE,
            FILE_DIRECTORY_FILE | FILE_OPEN_FOR_BACKUP_INTENT,
        )?;
        let metadata = created.metadata().map_err(|_| io_failure())?;
        if !metadata.is_dir() || is_reparse(&created).map_err(|_| io_failure())? {
            return Err(unsafe_path());
        }
        drop(created);
        NativeFs::flush_parent(self, path)
    }

    fn remove_entry(&self, path: &ValidatedPath) -> InstallResult<()> {
        remove_below(path)
    }

    fn remove_directory(&self, path: &ValidatedPath) -> InstallResult<()> {
        match remove_below(path) {
            Ok(()) => Ok(()),
            Err(failure) if failure.code == FailureCode::CleanupPending => Err(failure),
            Err(failure) => Err(failure),
        }
    }

    fn install_carrier(
        &self,
        source: &PackageSource,
        destination: &ValidatedPath,
    ) -> InstallResult<()> {
        if source.kind() != PackageKind::File || destination.kind() != PathKind::NewFile {
            return Err(invalid_package());
        }
        // The destination's whole parent chain is opened and identity-checked
        // **first**, and the leaf is then created **relative to that handle** with
        // `FILE_CREATE` (exclusive, no follow). The handle returned by the create
        // *is* the object this install owns, from the first instant: no name is
        // resolved again to learn what was created, so a redirected path can never
        // make the installer label somebody else's occupant "the copy's file".
        let handle = destination.dir_handle()?;
        let anchor_path = final_path_of(handle.handle())?;
        let leaf = destination.leaf_name()?.to_os_string();
        // The anchored parent must still render to the validated path, so the
        // path the journal records is the one the object is created at. This is a
        // consistency assertion, not the mutation: the create below is relative to
        // `handle` and no path is handed to the OS for it.
        if !crate::platform::same_path(&anchor_path.join(&leaf), destination.rendered()) {
            return Err(foreign_state());
        }
        let mut created_file = open_below(
            handle.handle(),
            &leaf,
            ACCESS_RWD,
            NT_FILE_CREATE,
            FILE_NON_DIRECTORY_FILE | FILE_OPEN_FOR_BACKUP_INTENT,
        )?;
        // The identity is read from **that created handle**, not from a later open
        // of the name.
        let created = crate::platform::identity_of_file(&created_file)?;
        let outcome = (|| {
            let metadata = created_file.metadata().map_err(|_| io_failure())?;
            if !metadata.is_file()
                || metadata.len() != 0
                || is_reparse(&created_file).map_err(|_| io_failure())?
            {
                return Err(unsafe_path());
            }
            // The created object is populated through its own handle: the source
            // image is opened separately (no link followed, no writer admitted)
            // and the bytes are transferred with `WriteFile` in bounded chunks,
            // so no path is resolved for the copy.
            let mut source_file = open_carrier_source(source.path())?;
            let expected = source_file.metadata().map_err(|_| io_failure())?.len();
            if expected == 0 || expected > crate::platform::MAX_CARRIER_BYTES as u64 {
                return Err(invalid_package());
            }
            if copy_carrier_stream(&mut source_file, &mut created_file)? != expected {
                return Err(io_failure());
            }
            // Basic metadata (attributes and timestamps) is applied to the same
            // created handle; `CopyFile2` used to carry it implicitly.
            copy_carrier_metadata(&source_file, &created_file)?;
            // The copied bytes and the applied metadata are durable before the
            // destination may be journalled.
            created_file.sync_all().map_err(|_| io_failure())?;
            Ok(())
        })();
        if let Err(failure) = outcome {
            // Remove only the object this call created, through the name the
            // identity recorded is checked against. A refused removal leaves a
            // leftover that the journaled cleanup reports.
            drop(created_file);
            match discard_created_leaf(&handle, &leaf, created) {
                Err(discard) if discard.code == FailureCode::CleanupPending => return Err(discard),
                _ => return Err(failure),
            }
        }
        // The install may only claim the destination if the captured ancestor
        // chain still names the validated anchor **and** the destination name
        // still names the object this call created. A mismatch is never success:
        // the object is discarded only when the name still holds it, and the
        // install reports `ForeignState` (or `CleanupPending` when it cannot be
        // removed).
        if !anchor_still_names(destination, &handle) || !leaf_still_names(&handle, &leaf, created) {
            drop(created_file);
            return discard_created_leaf(&handle, &leaf, created);
        }
        drop(created_file);
        NativeFs::flush_parent(self, destination)
    }
}

/// Re-check the carrier install's anchor after populating the destination.
///
/// The captured ancestor chain is opened a second time; `dir_handle` compares
/// every opened ancestor with the identity validation captured, and the anchor
/// must still be the same object the destination leaf was created under. `false`
/// means the destination is no longer anchored to the validated tree. No
/// rendered-path comparison is needed any more: the creation was relative to this
/// handle, so no pathname was ever handed to the OS for the mutation.
fn anchor_still_names(
    destination: &ValidatedPath,
    before: &super::native::anchor::DirectoryHandle,
) -> bool {
    match destination.dir_handle() {
        Ok(after) => after.identity() == before.identity(),
        Err(_) => false,
    }
}

/// Whether the destination name still holds exactly the object this install
/// created.
///
/// The leaf is opened relative to the anchored parent with no-follow semantics and
/// as a plain regular file, so a link, a directory or a replaced file all answer
/// `false` and are never adopted.
fn leaf_still_names(
    handle: &super::native::anchor::DirectoryHandle,
    leaf: &OsStr,
    created: crate::platform::FileIdentity,
) -> bool {
    match open_created_leaf(handle, leaf) {
        Ok(current) => matches!(
            crate::platform::identity_of_file(&current),
            Ok(identity) if identity == created
        ),
        Err(_) => false,
    }
}

/// Open the package carrier image for reading.
///
/// `FILE_SHARE_READ` only, so no writer can change the bytes while they are
/// copied; `FILE_FLAG_OPEN_REPARSE_POINT` opens a link as itself and the caller
/// refuses it rather than following it.
fn open_carrier_source(path: &Path) -> InstallResult<File> {
    let wide = wide_path(path)?;
    // SAFETY: `wide` is NUL-terminated and outlives the call.
    let handle = unsafe {
        CreateFileW(
            PCWSTR(wide.as_ptr()),
            GENERIC_READ.0,
            FILE_SHARE_READ,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
    }
    .map_err(|_| io_failure())?;
    // SAFETY: CreateFileW transferred ownership of the handle.
    let file = unsafe { File::from_raw_handle(handle.0) };
    if is_reparse(&file).map_err(|_| io_failure())? {
        return Err(unsafe_path());
    }
    if !file.metadata().map_err(|_| io_failure())?.is_file() {
        return Err(unsafe_path());
    }
    Ok(file)
}

/// Copy one opened source stream into the opened destination handle.
///
/// Fixed-size chunks, both handles already opened: the byte count is bounded by
/// the length the caller checked, and a short write is an I/O failure rather than
/// a silently truncated image.
fn copy_carrier_stream(source: &mut File, destination: &mut File) -> InstallResult<u64> {
    let mut buffer = vec![0u8; CARRIER_COPY_CHUNK];
    let mut total = 0u64;
    loop {
        let mut read = 0u32;
        // SAFETY: both handles are live for the call and the buffer and
        // out-parameter outlive it.
        unsafe {
            ReadFile(
                HANDLE(source.as_raw_handle()),
                Some(buffer.as_mut_slice()),
                Some(&mut read),
                None,
            )
        }
        .map_err(|_| io_failure())?;
        if read == 0 {
            return Ok(total);
        }
        total = total.saturating_add(u64::from(read));
        if total > crate::platform::MAX_CARRIER_BYTES as u64 {
            return Err(invalid_package());
        }
        let mut written = 0u32;
        // SAFETY: both handles are live for the call and the slice and
        // out-parameter outlive it.
        unsafe {
            WriteFile(
                HANDLE(destination.as_raw_handle()),
                Some(&buffer[..read as usize]),
                Some(&mut written),
                None,
            )
        }
        .map_err(|_| io_failure())?;
        if written != read {
            return Err(io_failure());
        }
    }
}

/// Apply the source image's attributes and timestamps to the created destination.
///
/// `SetFileInformationByHandle(FileBasicInfo)` is called on the very handle this
/// install created. `ChangeTime` is zero (leave unchanged: it is not settable
/// through this information class) and `FILE_ATTRIBUTE_READONLY` is not carried
/// (see [`COPIED_ATTRIBUTE_MASK`]); everything else that can describe the created
/// regular file is copied.
fn copy_carrier_metadata(source: &File, destination: &File) -> InstallResult<()> {
    let mut source_info = FILE_BASIC_INFO::default();
    // SAFETY: `source` is a live handle and the out-structure is correctly sized.
    unsafe {
        GetFileInformationByHandleEx(
            HANDLE(source.as_raw_handle()),
            FileBasicInfo,
            (&mut source_info as *mut FILE_BASIC_INFO).cast(),
            size_of::<FILE_BASIC_INFO>() as u32,
        )
    }
    .map_err(|_| io_failure())?;
    let mut applied = FILE_BASIC_INFO {
        CreationTime: source_info.CreationTime,
        LastAccessTime: source_info.LastAccessTime,
        LastWriteTime: source_info.LastWriteTime,
        ChangeTime: 0,
        FileAttributes: source_info.FileAttributes & COPIED_ATTRIBUTE_MASK,
    };
    // SAFETY: `destination` is the live handle this install created with write
    // access and `applied` is a correctly sized FILE_BASIC_INFO.
    unsafe {
        SetFileInformationByHandle(
            HANDLE(destination.as_raw_handle()),
            FileBasicInfo,
            (&mut applied as *mut FILE_BASIC_INFO).cast(),
            size_of::<FILE_BASIC_INFO>() as u32,
        )
    }
    .map_err(|_| io_failure())
}

/// Remove the leaf this call created, **only** when it is still the object whose
/// identity was recorded from the creation handle.
///
/// The entry is opened with no-follow semantics relative to the anchored
/// directory handle, must be a plain regular file, and its **current identity**
/// must equal the identity read from the handle this install created; only then
/// is it deleted with the handle-bound `FileDispositionInfo`. An occupant that is
/// not that object is left untouched and reported as `ForeignState` (the journaled
/// cleanup then reports the leftover), and a refused deletion of the proven object
/// is `CleanupPending`. The portable helper always returns an error: this is the
/// failure path of a carrier install, so a successful removal still must not be
/// reported as a successful install.
fn discard_created_leaf(
    handle: &super::native::anchor::DirectoryHandle,
    leaf: &OsStr,
    created: crate::platform::FileIdentity,
) -> InstallResult<()> {
    crate::platform::discard_created_leaf(
        created,
        || {
            let file = open_created_leaf(handle, leaf)?;
            Ok(Some(crate::platform::identity_of_file(&file)?))
        },
        || {
            let file = open_created_leaf(handle, leaf)?;
            delete_opened(&file)
        },
    )
}

/// Open one leaf below an anchored parent handle as a plain regular file.
///
/// A link, reparse point or directory is refused, so the identity that is read
/// (and the object that is deleted) can only be a regular file.
fn open_created_leaf(
    handle: &super::native::anchor::DirectoryHandle,
    leaf: &OsStr,
) -> InstallResult<File> {
    let file = open_below(
        handle.handle(),
        leaf,
        ACCESS_RD,
        NT_FILE_OPEN,
        FILE_NON_DIRECTORY_FILE | FILE_OPEN_FOR_BACKUP_INTENT,
    )?;
    let metadata = file.metadata().map_err(|_| io_failure())?;
    if !metadata.is_file() || is_reparse(&file).map_err(|_| io_failure())? {
        return Err(unsafe_path());
    }
    Ok(file)
}

/// The volume path Windows reports for one opened handle.
///
/// Used to assert that the anchored parent handle names the validated path the
/// journal records. The mutation itself is performed relative to the handle, so
/// this path is never handed to the OS for the create or the write.
fn final_path_of(handle: HANDLE) -> InstallResult<PathBuf> {
    use windows::Win32::Storage::FileSystem::{
        FILE_NAME_NORMALIZED, GetFinalPathNameByHandleW, VOLUME_NAME_DOS,
    };

    let mut buffer = vec![0u16; 1024];
    loop {
        // SAFETY: the handle is live and `buffer` is a live output buffer.
        let needed = unsafe {
            GetFinalPathNameByHandleW(
                handle,
                &mut buffer,
                GETFINALPATHNAMEBYHANDLE_FLAGS(FILE_NAME_NORMALIZED.0 | VOLUME_NAME_DOS.0),
            )
        } as usize;
        if needed == 0 {
            return Err(io_failure());
        }
        if needed < buffer.len() {
            buffer.truncate(needed);
            let path = String::from_utf16(&buffer).map_err(|_| unsafe_path())?;
            // `VOLUME_NAME_DOS` yields a `\\?\`-prefixed path; the verbatim
            // prefix is stripped so it compares with the validated path.
            let path = path.strip_prefix(r"\\?\").unwrap_or(&path);
            return Ok(PathBuf::from(path));
        }
        buffer.resize(needed + 1, 0);
    }
}
