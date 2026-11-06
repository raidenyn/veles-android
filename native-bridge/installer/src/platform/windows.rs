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

use std::ffi::OsString;
use std::fs;
use std::io;
use std::mem::size_of;
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::path::{Path, PathBuf};
use std::process;
use std::ptr;

use windows::Wdk::System::SystemInformation::{NtQuerySystemInformation, SystemProcessInformation};
use windows::Win32::Foundation::{
    CloseHandle, ERROR_INVALID_PARAMETER, GENERIC_READ, HANDLE, HWND, STATUS_INFO_LENGTH_MISMATCH,
    STATUS_SUCCESS,
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
    EqualSid, GetTokenInformation, IsValidSid, PSID, TOKEN_QUERY, TOKEN_USER, TokenUser,
};
use windows::Win32::Storage::FileSystem::{
    CreateFileW, FILE_ATTRIBUTE_NORMAL, FILE_BEGIN, FILE_SHARE_READ, GetFileSizeEx, OPEN_EXISTING,
    ReadFile, SetFilePointerEx,
};
use windows::Win32::System::Threading::{
    GetCurrentProcess, OpenProcess, OpenProcessToken, PROCESS_NAME_WIN32,
    PROCESS_QUERY_LIMITED_INFORMATION, QueryFullProcessImageNameW,
};
use windows::Win32::System::WindowsProgramming::SYSTEM_PROCESS_INFORMATION;
use windows::core::{HRESULT, PCWSTR, PWSTR};

use super::pe;
use super::windows_trust as trust_status;
use super::{
    HostState, ImageClass, InstallLock, OwnedEntry, OwnedImages, PACKAGE_WINDOWS_CARRIER,
    PackageKind, PackageSource, Platform, ProcessIdentity, ProcessImage, ProcessObservation,
    ProcessScope, ProcessTable, Registration, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, TrustProof,
    ValidatedPath, inspect_host_processes, path_is_within,
};
use crate::model::{Digest, FailureCode, InstallFailure, InstallResult, ReleaseDescriptor, Target};
use crate::paths::InstallPaths;

/// Name of the `RT_RCDATA` resource that carries the policy section.
const CARRIER_RESOURCE_NAME: &str = "VELES_POLICY_CARRIER";
/// Bound on the signed carrier image read for resource extraction.
const MAX_CARRIER_IMAGE_BYTES: usize = 64 * 1024 * 1024;
/// Bound on the extracted carrier bytes (`TrustProof` enforces the same limit).
const MAX_CARRIER_BYTES: usize =
    crate::policy::CARRIER_HEADER_LEN + crate::model::MAX_DOCUMENT_BYTES;
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

/// The Windows [`Platform`] adapter.
///
/// Task 4 implements [`Platform::verify_trust`] and Task 5 implements
/// [`Platform::inspect_hosts`]; the remaining boundary methods fail closed until
/// Task 8 (native filesystem, registry and cleanup) implements them.
#[derive(Debug, Default)]
pub struct WindowsPlatform;

impl WindowsPlatform {
    /// Construct the Windows adapter.
    pub fn new() -> Self {
        Self
    }
}

impl Platform for WindowsPlatform {
    fn lock(&self, _paths: &InstallPaths) -> InstallResult<InstallLock> {
        Err(unsupported())
    }

    fn write_durable(&self, _path: &ValidatedPath, _bytes: &[u8]) -> InstallResult<()> {
        Err(unsupported())
    }

    fn replace_durable(&self, _path: &ValidatedPath, _bytes: &[u8]) -> InstallResult<()> {
        // The native atomic replacement belongs to the Task 8 adapter.
        Err(unsupported())
    }

    fn write_state(
        &self,
        _paths: &InstallPaths,
        _document: super::StateDocument,
        _bytes: &[u8],
    ) -> InstallResult<()> {
        Err(unsupported())
    }
    fn read_state(
        &self,
        _paths: &InstallPaths,
        _document: super::StateDocument,
    ) -> InstallResult<Option<Vec<u8>>> {
        Err(unsupported())
    }

    fn quarantine_state(
        &self,
        _paths: &InstallPaths,
        _document: super::StateDocument,
    ) -> InstallResult<()> {
        Err(unsupported())
    }

    fn publish_manifest(&self, _paths: &InstallPaths, _value: &Registration) -> InstallResult<()> {
        Err(unsupported())
    }

    fn replace_manifest(
        &self,
        _staged: &ValidatedPath,
        _live: &ValidatedPath,
    ) -> InstallResult<()> {
        Err(unsupported())
    }

    /// Task 8 reads the manifest file and the fixed HKCU value together and
    /// returns a [`Registration`] carrying the exact HKCU key and previous
    /// default value as its [`WindowsPreimage`](super::WindowsPreimage), so the
    /// transaction can journal a complete restore preimage.
    fn read_registration(&self, _paths: &InstallPaths) -> InstallResult<Option<Registration>> {
        Err(unsupported())
    }

    fn publish_registration(
        &self,
        _paths: &InstallPaths,
        _value: &Registration,
    ) -> InstallResult<()> {
        Err(unsupported())
    }

    fn remove_registration(
        &self,
        _paths: &InstallPaths,
        _expected: &Registration,
    ) -> InstallResult<()> {
        Err(unsupported())
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

    fn remove_owned(&self, _entry: &OwnedEntry) -> InstallResult<()> {
        Err(unsupported())
    }

    fn flush_parent(&self, _path: &ValidatedPath) -> InstallResult<()> {
        Err(unsupported())
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
    let signer = signer?;

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
