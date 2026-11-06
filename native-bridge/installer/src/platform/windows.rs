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
//!   unauthenticated trailing overlay (see [`super::pe`]).
//!
//! The remaining [`Platform`] methods are deliberately fail-closed here; Tasks 5
//! and 8 complete process inspection, filesystem/registry mutation and cleanup.

use std::fs;
use std::io;
use std::os::windows::ffi::OsStrExt;
use std::path::Path;
use std::ptr;

use windows::Win32::Foundation::{CloseHandle, GENERIC_READ, HANDLE, HWND};
use windows::Win32::Security::Cryptography::CERT_CONTEXT;
use windows::Win32::Security::WinTrust::{
    CRYPT_PROVIDER_CERT, WINTRUST_ACTION_GENERIC_VERIFY_V2, WINTRUST_DATA, WINTRUST_DATA_0,
    WINTRUST_FILE_INFO, WTD_CHOICE_FILE, WTD_DISABLE_MD2_MD4, WTD_REVOCATION_CHECK_CHAIN,
    WTD_REVOKE_WHOLECHAIN, WTD_STATEACTION_CLOSE, WTD_STATEACTION_VERIFY, WTD_UI_NONE,
    WTHelperGetProvCertFromChain, WTHelperGetProvSignerFromChain, WTHelperProvDataFromStateData,
    WinVerifyTrust,
};
use windows::Win32::Storage::FileSystem::{
    CreateFileW, FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ, GetFileSizeEx, OPEN_EXISTING, ReadFile,
};
use windows::core::PCWSTR;

use super::pe;
use super::{
    HostState, InstallLock, OwnedEntry, PACKAGE_WINDOWS_CARRIER, PackageKind, PackageSource,
    Platform, Registration, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE, TrustProof, ValidatedPath,
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

/// The Windows [`Platform`] adapter.
///
/// Task 4 implements [`Platform::verify_trust`]; the remaining boundary methods
/// fail closed until Tasks 5 (host inspection) and 8 (native filesystem,
/// registry and cleanup) implement them.
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

    fn inspect_hosts(&self, _paths: &InstallPaths) -> InstallResult<HostState> {
        // Task 5 implements real process inspection. `Unknown` blocks every
        // mutation instead of claiming quiescence.
        Ok(HostState::Unknown)
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

/// Classify a WinVerifyTrust status.
///
/// Known signature/chain/policy denials and explicit revocation are
/// `TrustDenied`. An assessment that cannot be formed - offline revocation data,
/// a broken trust provider, or an unknown status - blocks activation as
/// `TrustUnavailable` rather than being guessed as an explicit denial.
fn classify(status: i32) -> InstallFailure {
    match status as u32 {
        TRUST_E_NOSIGNATURE
        | TRUST_E_EXPLICIT_DISTRUST
        | TRUST_E_SUBJECT_NOT_TRUSTED
        | TRUST_E_SUBJECT_FORM_UNKNOWN
        | TRUST_E_BAD_DIGEST
        | TRUST_E_CERT_SIGNATURE
        | TRUST_E_TIME_STAMP
        | TRUST_E_FAIL
        | CERT_E_REVOKED
        | CERT_E_UNTRUSTEDROOT
        | CERT_E_UNTRUSTEDTESTROOT
        | CERT_E_CHAINING
        | CERT_E_EXPIRED
        | CERT_E_WRONG_USAGE
        | CERT_E_CRITICAL
        | CERT_E_PURPOSE
        | CERT_E_ISSUERCHAINING
        | CERT_E_PATHLENCONST
        | CERT_E_UNTRUSTEDCA
        | CERT_E_INVALID_POLICY
        | CERT_E_INVALID_NAME
        | CERT_E_CN_NO_MATCH => trust_denied(),
        CERT_E_REVOCATION_FAILURE
        | CRYPT_E_REVOCATION_OFFLINE
        | TRUST_E_PROVIDER_UNKNOWN
        | TRUST_E_ACTION_UNKNOWN
        | TRUST_E_SYSTEM_ERROR => unavailable(),
        _ => unavailable(),
    }
}

const TRUST_E_PROVIDER_UNKNOWN: u32 = 0x800B_0001;
const TRUST_E_ACTION_UNKNOWN: u32 = 0x800B_0002;
const TRUST_E_SUBJECT_FORM_UNKNOWN: u32 = 0x800B_0003;
const TRUST_E_SUBJECT_NOT_TRUSTED: u32 = 0x800B_0004;
const TRUST_E_NOSIGNATURE: u32 = 0x800B_0100;
const CERT_E_EXPIRED: u32 = 0x800B_0101;
const CERT_E_PATHLENCONST: u32 = 0x800B_0104;
const CERT_E_CRITICAL: u32 = 0x800B_0105;
const CERT_E_PURPOSE: u32 = 0x800B_0106;
const CERT_E_ISSUERCHAINING: u32 = 0x800B_0107;
const CERT_E_UNTRUSTEDROOT: u32 = 0x800B_0109;
const CERT_E_CHAINING: u32 = 0x800B_010A;
const TRUST_E_FAIL: u32 = 0x800B_010B;
const CERT_E_REVOKED: u32 = 0x800B_010C;
const CERT_E_UNTRUSTEDTESTROOT: u32 = 0x800B_010D;
const CERT_E_REVOCATION_FAILURE: u32 = 0x800B_010E;
const CERT_E_CN_NO_MATCH: u32 = 0x800B_010F;
const CERT_E_WRONG_USAGE: u32 = 0x800B_0110;
const TRUST_E_EXPLICIT_DISTRUST: u32 = 0x800B_0111;
const CERT_E_UNTRUSTEDCA: u32 = 0x800B_0112;
const CERT_E_INVALID_POLICY: u32 = 0x800B_0113;
const CERT_E_INVALID_NAME: u32 = 0x800B_0114;
const CRYPT_E_REVOCATION_OFFLINE: u32 = 0x8009_2013;
const TRUST_E_SYSTEM_ERROR: u32 = 0x8009_6001;
const TRUST_E_CERT_SIGNATURE: u32 = 0x8009_6004;
const TRUST_E_TIME_STAMP: u32 = 0x8009_6005;
const TRUST_E_BAD_DIGEST: u32 = 0x8009_6010;

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

fn unavailable() -> InstallFailure {
    InstallFailure::new(FailureCode::TrustUnavailable)
}
