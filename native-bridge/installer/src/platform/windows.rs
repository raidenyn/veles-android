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
//! [`WindowsPlatform::inspect_hosts`] walks the Toolhelp process snapshot, keeps
//! the current interactive session (services in session 0 can never execute the
//! user's private `%LOCALAPPDATA%` root), compares each process token's user SID
//! with the installing user's, and resolves the executable image with
//! `QueryFullProcessImageNameW` only for that user's processes. Other users'
//! images are never read. A same-user process whose image cannot be resolved is a
//! potential owned host and blocks. Process identity is the PID plus the
//! `GetProcessTimes` creation time; every candidate is re-read before the
//! decision so a recycled PID is not mistaken for the observed process.

use std::fs;
use std::io;
use std::mem::size_of;
use std::os::windows::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::process;
use std::ptr;

use windows::Win32::Foundation::{
    CloseHandle, ERROR_NO_MORE_FILES, FILETIME, GENERIC_READ, HANDLE, HWND,
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
    EqualSid, GetTokenInformation, PSID, TOKEN_QUERY, TOKEN_USER, TokenUser,
};
use windows::Win32::Storage::FileSystem::{
    CreateFileW, FILE_ATTRIBUTE_NORMAL, FILE_BEGIN, FILE_SHARE_READ, GetFileSizeEx, OPEN_EXISTING,
    ReadFile, SetFilePointerEx,
};
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, PROCESSENTRY32W, Process32FirstW, Process32NextW, TH32CS_SNAPPROCESS,
};
use windows::Win32::System::RemoteDesktop::ProcessIdToSessionId;
use windows::Win32::System::Threading::{
    GetCurrentProcess, GetProcessTimes, OpenProcess, OpenProcessToken, PROCESS_NAME_WIN32,
    PROCESS_QUERY_LIMITED_INFORMATION, QueryFullProcessImageNameW,
};
use windows::core::{HRESULT, PCWSTR, PWSTR};

use super::pe;
use super::windows_trust as trust_status;
use super::{
    HostState, InstallLock, OwnedEntry, PACKAGE_WINDOWS_CARRIER, PackageKind, PackageSource,
    Platform, ProcessIdentity, ProcessObservation, ProcessScope, ProcessTable, Registration,
    TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, TrustProof, ValidatedPath, inspect_host_processes,
    path_is_within,
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

    fn replace_manifest(
        &self,
        _staged: &ValidatedPath,
        _live: &ValidatedPath,
    ) -> InstallResult<()> {
        Err(unsupported())
    }

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
/// Services in session 0 are skipped without being opened: they run as
/// SYSTEM/Service and can never execute the interactive user's private
/// `%LOCALAPPDATA%` root, so they must not have to be readable. For the current
/// session the process token's user SID decides attribution, and only the
/// installing user's processes have their image resolved.
#[derive(Debug, Default)]
struct WindowsProcessTable;

impl ProcessTable for WindowsProcessTable {
    fn observe(&self) -> io::Result<Vec<ProcessObservation>> {
        let own = CurrentUser::capture()?;
        let snapshot = ToolhelpSnapshot::capture()?;
        let mut observations = Vec::new();
        for entry in snapshot.entries()? {
            let pid = entry.th32ProcessID;
            if pid == 0 || pid == process::id() {
                continue;
            }
            // Session 0 is the services session; another session belongs to a
            // different logon and cannot reach this user's private root.
            match session_id(pid) {
                Some(session) if session == own.session => {}
                // A PID whose session cannot be read has already exited.
                _ => continue,
            }
            observations.push(observe_process(pid, &own));
        }
        Ok(observations)
    }

    fn recheck(&self, pid: u32) -> io::Result<Option<ProcessObservation>> {
        let own = CurrentUser::capture()?;
        match session_id(pid) {
            // A PID that no longer resolves to a session is gone.
            None => Ok(None),
            // A PID recycled into the services session or another logon is not
            // the process that was observed and cannot execute this root.
            Some(session) if session != own.session => Ok(Some(other_user_observation(pid))),
            Some(_) => Ok(Some(observe_process(pid, &own))),
        }
    }
}

/// The installing user's session and token user SID.
struct CurrentUser {
    session: u32,
    sid: TokenUserSid,
}

impl CurrentUser {
    fn capture() -> io::Result<Self> {
        // SAFETY: `GetCurrentProcess` returns a non-owning pseudo-handle.
        let sid = TokenUserSid::of_process(unsafe { GetCurrentProcess() })
            .ok_or_else(|| io::Error::other("current process token user is unavailable"))?;
        let session = session_id(process::id())
            .ok_or_else(|| io::Error::other("current process session is unavailable"))?;
        Ok(Self { session, sid })
    }
}

/// A process the OS positively attributes to another user or session.
fn other_user_observation(pid: u32) -> ProcessObservation {
    ProcessObservation {
        identity: ProcessIdentity { pid, started: 0 },
        scope: ProcessScope::OtherUser,
        image: None,
    }
}

/// A process that cannot be attributed, so it cannot be ruled out.
fn unattributed(pid: u32, started: u64) -> ProcessObservation {
    ProcessObservation {
        identity: ProcessIdentity { pid, started },
        scope: ProcessScope::InstallingUser,
        image: None,
    }
}

/// Observe one process in the installing user's session.
///
/// Any failure to attribute or to resolve the image keeps the process as an
/// installing-user candidate with an unresolved image, which blocks instead of
/// claiming quiescence.
fn observe_process(pid: u32, own: &CurrentUser) -> ProcessObservation {
    let Some(handle) = open_process(pid) else {
        // An OS-protected or elevated process in this session cannot be
        // attributed by a non-elevated installer, so it is a potential host.
        return unattributed(pid, 0);
    };
    let started = process_start(&handle).unwrap_or(0);
    let Some(token) = TokenUserSid::of_process(handle.0) else {
        return unattributed(pid, started);
    };
    // SAFETY: both SIDs belong to live token buffers.
    let same_user = unsafe { EqualSid(own.sid.sid(), token.sid()) }.is_ok();
    let scope = if same_user {
        ProcessScope::InstallingUser
    } else {
        ProcessScope::OtherUser
    };
    let image = if same_user { image_path(&handle) } else { None };
    ProcessObservation {
        identity: ProcessIdentity { pid, started },
        scope,
        image,
    }
}

fn open_process(pid: u32) -> Option<OwnedHandle> {
    // SAFETY: `pid` is an observed process id and `false` disables inheritance.
    unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) }
        .ok()
        .and_then(OwnedHandle::new)
}

/// The process creation time: PID reuse always changes it.
fn process_start(handle: &OwnedHandle) -> Option<u64> {
    let mut creation = FILETIME::default();
    let mut exit = FILETIME::default();
    let mut kernel = FILETIME::default();
    let mut user = FILETIME::default();
    // SAFETY: the handle is live and every out-parameter is live.
    unsafe { GetProcessTimes(handle.0, &mut creation, &mut exit, &mut kernel, &mut user) }.ok()?;
    Some(((creation.dwHighDateTime as u64) << 32) | creation.dwLowDateTime as u64)
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
    Some(PathBuf::from(String::from_utf16_lossy(&buffer[..size])))
}

fn session_id(pid: u32) -> Option<u32> {
    let mut session = 0u32;
    // SAFETY: `session` is a live out-parameter.
    unsafe { ProcessIdToSessionId(pid, &mut session) }.ok()?;
    Some(session)
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

/// A Toolhelp process snapshot, closed on drop.
struct ToolhelpSnapshot(OwnedHandle);

impl ToolhelpSnapshot {
    fn capture() -> io::Result<Self> {
        // SAFETY: `TH32CS_SNAPPROCESS` takes no target process id.
        let handle = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) }
            .map_err(|error| io::Error::other(error.message()))?;
        Ok(Self(OwnedHandle::new(handle).ok_or_else(|| {
            io::Error::other("invalid Toolhelp snapshot handle")
        })?))
    }

    fn entries(&self) -> io::Result<Vec<PROCESSENTRY32W>> {
        let mut entry = PROCESSENTRY32W {
            dwSize: size_of::<PROCESSENTRY32W>() as u32,
            ..PROCESSENTRY32W::default()
        };
        let mut entries = Vec::new();
        // SAFETY: `entry` is a live, correctly sized out-parameter.
        match unsafe { Process32FirstW(self.0.0, &mut entry) } {
            Ok(()) => {}
            // An empty snapshot on a running system means the table could not be
            // read, which must not look like "no owned host".
            Err(error) if error.code() == HRESULT::from_win32(ERROR_NO_MORE_FILES.0) => {
                return Err(io::Error::other("empty process snapshot"));
            }
            Err(error) => return Err(io::Error::other(error.message())),
        }
        loop {
            entries.push(entry);
            if entries.len() > MAX_PROCESS_SNAPSHOT_ENTRIES {
                return Err(io::Error::other("implausible process table"));
            }
            entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
            // SAFETY: as above; the final call reports ERROR_NO_MORE_FILES.
            match unsafe { Process32NextW(self.0.0, &mut entry) } {
                Ok(()) => {}
                Err(error) if error.code() == HRESULT::from_win32(ERROR_NO_MORE_FILES.0) => {
                    return Ok(entries);
                }
                Err(error) => return Err(io::Error::other(error.message())),
            }
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
