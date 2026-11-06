//! macOS OS trust adapter (Task 4).
//!
//! [`MacosPlatform::verify_trust`] is read-only and implements the macOS half of
//! the package-authentication contract with documented, public interfaces:
//!
//! * `SecStaticCodeCreateWithPath` + `SecStaticCodeCheckValidity` validate the
//!   carrier app bundle with all architectures, nested code, strict bundle
//!   structure, symlink/sideband restrictions, certificate expiration and
//!   enforced certificate revocation checks. Revocation checks are never
//!   disabled and no network-access opt-out is requested. The **resource
//!   envelope is validated too**: `SecStaticCodeCheckValidity` validates the
//!   sealed resources unless the caller passes `kSecCSDoNotValidateResources`,
//!   and this adapter never passes it.
//! * The Team ID from `SecCodeCopySigningInformation` must be a well-formed
//!   Apple team identifier **and** one of the `team:<ID>` publishers the
//!   caller-authorized release descriptor allows. The designated requirement is
//!   built from that authorized Team ID (`anchor apple generic` plus
//!   `certificate leaf[subject.OU]`; production additionally requires the
//!   Developer ID intermediate/leaf markers). A requirement pinned only to the
//!   *observed* Team ID would accept any Apple-anchored Developer ID bundle, so
//!   the policy check happens first and a non-authorized team is denied.
//! * Gatekeeper's execution assessment is the documented `spctl --assess
//!   --type exec` entry point (the tool the plan lists), executed with a fixed
//!   absolute tool path, structured argv (no shell), a timeout and captured
//!   output. A rejection is `TrustDenied`; a spawn failure, timeout or error
//!   status is `TrustUnavailable`. A bare `codesign` exit status is never
//!   accepted as evidence: Gatekeeper's acceptance is the execution-policy and
//!   notarization verdict for Developer ID code.
//! * The policy carrier resource under `Contents/Resources` is read after
//!   validation and the bundle is validated a second time, so a bundle that
//!   changed before or during the read fails closed even though the app
//!   signature still verifies.
//!
//! The private `SecAssessment*` SPI and `SecCodeValidateFileResource` SPI are
//! deliberately not used: only documented public API is called. What therefore
//! remains unverified without a native runner and a public byte-level resource
//! accessor is recorded in the Task 4 report: the exact bytes read from the
//! resource are not cryptographically proven to be the sealed blob (only the
//! bundle's resource envelope is validated before and after the read), and the
//! carrier descriptor equality check in the decision layer is what bounds the
//! content that can be accepted.
//!
//! The remaining [`Platform`] methods fail closed; Tasks 5 and 8 complete them.

use std::ffi::{CStr, c_char, c_void};
use std::fs;
use std::io;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::ptr;
use std::thread;
use std::time::{Duration, Instant};

use super::macos_trust as policy;
use super::{
    HostState, InstallLock, OwnedEntry, PACKAGE_MACOS_CARRIER, PackageKind, PackageSource,
    Platform, Registration, TRUST_ASSESSMENT_MACOS_GATEKEEPER, TrustProof, ValidatedPath,
    path_is_within,
};
use crate::model::{FailureCode, InstallFailure, InstallResult, ReleaseDescriptor, Target};
use crate::paths::InstallPaths;

/// Sealed carrier resource, relative to the bundle's `Contents` directory.
const CARRIER_RESOURCE_RELATIVE: &str = "Resources/veles-policy-carrier";
/// Bound on the sealed carrier bytes (`TrustProof` enforces the same limit).
const MAX_CARRIER_BYTES: usize =
    crate::policy::CARRIER_HEADER_LEN + crate::model::MAX_DOCUMENT_BYTES;
/// Poll interval while waiting for the bounded Gatekeeper assessment.
const SPCTL_POLL_INTERVAL: Duration = Duration::from_millis(25);

const K_CF_STRING_ENCODING_UTF8: u32 = 0x0800_0100;

// SecCSFlags values from SecStaticCode.h. `kSecCSStrictValidate` (1 << 4) shares
// its numeric value with `kSecCSContentInformation`, which belongs to the
// information-request flag set; this adapter uses the validation option only.
const K_SEC_CS_SIGNING_INFORMATION: u32 = 1 << 1;
const K_SEC_CS_CHECK_ALL_ARCHITECTURES: u32 = 1 << 0;
const K_SEC_CS_CHECK_NESTED_CODE: u32 = 1 << 3;
const K_SEC_CS_STRICT_VALIDATE: u32 = 1 << 4;
const K_SEC_CS_RESTRICT_SYMLINKS: u32 = 1 << 7;
const K_SEC_CS_RESTRICT_SIDEBAND_DATA: u32 = 1 << 9;
// Global flags assigned from the high bit down (CSCommon.h).
const K_SEC_CS_CONSIDER_EXPIRATION: u32 = 1 << 31;
const K_SEC_CS_ENFORCE_REVOCATION_CHECKS: u32 = 1 << 30;

/// Validation option every check in this adapter shares.
///
/// `kSecCSDoNotValidateResources` is deliberately absent: resource (seal)
/// validation is part of the default `SecStaticCodeCheckValidity` behaviour, so
/// the carrier bundle's resource envelope - including the policy carrier - is
/// covered by the signature check.
const SEC_STATIC_CODE_FLAGS: u32 = K_SEC_CS_CHECK_ALL_ARCHITECTURES
    | K_SEC_CS_CHECK_NESTED_CODE
    | K_SEC_CS_STRICT_VALIDATE
    | K_SEC_CS_RESTRICT_SYMLINKS
    | K_SEC_CS_RESTRICT_SIDEBAND_DATA
    | K_SEC_CS_CONSIDER_EXPIRATION
    | K_SEC_CS_ENFORCE_REVOCATION_CHECKS;

type CFTypeRef = *const c_void;
type CFStringRef = *const c_void;
type CFURLRef = *const c_void;
type CFDictionaryRef = *const c_void;
type SecStaticCodeRef = *const c_void;
type SecRequirementRef = *const c_void;
type OSStatus = i32;
type Boolean = u8;

#[link(name = "CoreFoundation", kind = "framework")]
unsafe extern "C" {
    fn CFURLCreateFromFileSystemRepresentation(
        allocator: CFTypeRef,
        buffer: *const u8,
        buffer_length: isize,
        is_directory: Boolean,
    ) -> CFURLRef;
    fn CFStringCreateWithBytes(
        allocator: CFTypeRef,
        bytes: *const u8,
        num_bytes: isize,
        encoding: u32,
        is_external_representation: Boolean,
    ) -> CFStringRef;
    fn CFStringGetCString(
        string: CFStringRef,
        buffer: *mut c_char,
        buffer_size: isize,
        encoding: u32,
    ) -> Boolean;
    fn CFStringGetTypeID() -> usize;
    fn CFGetTypeID(value: CFTypeRef) -> usize;
    fn CFDictionaryGetValue(dict: CFDictionaryRef, key: *const c_void) -> CFTypeRef;
    fn CFRelease(value: CFTypeRef);
}

#[link(name = "Security", kind = "framework")]
unsafe extern "C" {
    fn SecStaticCodeCreateWithPath(
        path: CFURLRef,
        flags: u32,
        static_code: *mut SecStaticCodeRef,
    ) -> OSStatus;
    fn SecStaticCodeCheckValidity(
        static_code: SecStaticCodeRef,
        flags: u32,
        requirement: SecRequirementRef,
    ) -> OSStatus;
    fn SecRequirementCreateWithString(
        text: CFStringRef,
        flags: u32,
        requirement: *mut SecRequirementRef,
    ) -> OSStatus;
    fn SecCodeCopySigningInformation(
        static_code: SecStaticCodeRef,
        flags: u32,
        information: *mut CFDictionaryRef,
    ) -> OSStatus;
    static kSecCodeInfoTeamIdentifier: CFStringRef;
}

/// A retained/invalidated CoreFoundation object.
struct Cf(CFTypeRef);

impl Cf {
    fn new(value: CFTypeRef) -> Option<Self> {
        (!value.is_null()).then_some(Self(value))
    }
}

impl Drop for Cf {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: `self` owns one retained reference to a CF object.
            unsafe { CFRelease(self.0) };
        }
    }
}

/// The macOS [`Platform`] adapter.
///
/// Task 4 implements [`Platform::verify_trust`]; the remaining boundary methods
/// fail closed until Tasks 5 (host inspection) and 8 (native filesystem,
/// registration and cleanup) implement them.
#[derive(Debug, Default)]
pub struct MacosPlatform;

impl MacosPlatform {
    /// Construct the macOS adapter.
    pub fn new() -> Self {
        Self
    }
}

impl Platform for MacosPlatform {
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
    if expected.target != Target::MacosArm64 {
        return Err(unsupported());
    }
    if source.kind() != PackageKind::Directory {
        return Err(invalid_package());
    }

    let carrier = open_carrier(source.path())?;
    let url = file_url(carrier.path())?;

    let mut static_code: SecStaticCodeRef = ptr::null();
    // SAFETY: `url` is a live CFURL for the carrier directory.
    let status = unsafe { SecStaticCodeCreateWithPath(url.0, 0, &mut static_code) };
    if status != 0 {
        return Err(classify(status));
    }
    let static_code = Cf::new(static_code).ok_or_else(unavailable)?;

    let team_id = observed_team_id(&static_code)?;
    // The requirement may only be built from a Team ID the caller-authorized
    // release descriptor already allows. Pinning the requirement to a merely
    // *observed* Team ID would accept any Apple-anchored Developer ID bundle.
    if !policy::is_authorized_team(&expected.policy.publishers, &team_id) {
        return Err(trust_denied());
    }
    let requirement_text =
        policy::publisher_requirement(expected.channel, &team_id).ok_or_else(trust_denied)?;
    let requirement_text = cf_string_value(&requirement_text)?;
    let mut requirement: SecRequirementRef = ptr::null();
    // SAFETY: the CFString is live and `requirement` is a live out-parameter.
    let status = unsafe { SecRequirementCreateWithString(requirement_text.0, 0, &mut requirement) };
    if status != 0 {
        return Err(classify(status));
    }
    let requirement = Cf::new(requirement).ok_or_else(trust_denied)?;

    // Strict signature, designated-requirement and resource-envelope validation.
    // Revocation and expiration checks are enabled, never bypassed.
    validate(&static_code, &requirement)?;

    let carrier_bytes = read_carrier_resource(&carrier)?;
    // Re-validate after reading the resource: any change to the bundle that
    // happened before or during the read (including a replaced carrier) fails
    // here. The sealed resource bytes cannot be fetched through a public API, so
    // this second check is the documented, best-effort binding; the descriptor
    // equality check in the decision layer is what actually bounds the content.
    validate(&static_code, &requirement)?;

    check_gatekeeper(carrier.path())?;

    TrustProof::new(
        format!("team:{team_id}"),
        TRUST_ASSESSMENT_MACOS_GATEKEEPER,
        carrier_bytes,
    )
}

/// The OS-observed Team ID, shape-checked.
fn observed_team_id(static_code: &Cf) -> InstallResult<String> {
    let mut information: CFDictionaryRef = ptr::null();
    // SAFETY: `static_code` is a live static code reference and `information` is
    // a live out-parameter.
    let status = unsafe {
        SecCodeCopySigningInformation(
            static_code.0,
            K_SEC_CS_SIGNING_INFORMATION,
            &mut information,
        )
    };
    if status != 0 {
        return Err(classify(status));
    }
    let information = Cf::new(information).ok_or_else(unavailable)?;
    // SAFETY: the dictionary is a live retained signing-information dictionary
    // and the key is the exported CFString constant.
    let team_id = unsafe {
        cf_string(CFDictionaryGetValue(
            information.0,
            kSecCodeInfoTeamIdentifier,
        ))
    }
    .ok_or_else(trust_denied)?;
    if !policy::is_valid_team_id(&team_id) {
        return Err(trust_denied());
    }
    Ok(team_id)
}

/// Validate the carrier bundle against the policy-derived requirement.
fn validate(static_code: &Cf, requirement: &Cf) -> InstallResult<()> {
    // SAFETY: both references are live retained code-signing objects.
    let status =
        unsafe { SecStaticCodeCheckValidity(static_code.0, SEC_STATIC_CODE_FLAGS, requirement.0) };
    if status != 0 {
        return Err(classify(status));
    }
    Ok(())
}

/// Run the bounded, documented Gatekeeper execution assessment.
///
/// `spctl` is invoked through its fixed absolute path with structured argv (no
/// shell). The child cannot outlive the decision: the wait is bounded by
/// [`policy::SPCTL_TIMEOUT`] and a timeout kills it. Output is fully drained but
/// retained only up to [`policy::MAX_SPCTL_OUTPUT_BYTES`] per stream.
fn check_gatekeeper(carrier: &Path) -> InstallResult<()> {
    let mut command = Command::new(policy::SPCTL_PATH);
    command
        .args(policy::assessment_argv(carrier))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // Loader injection cannot change which program runs (the path is
        // absolute), but the variables are removed anyway; any assessment the
        // tool cannot form is a fail-closed `TrustUnavailable`.
        .env_remove("DYLD_INSERT_LIBRARIES")
        .env_remove("DYLD_LIBRARY_PATH")
        .env_remove("DYLD_FRAMEWORK_PATH")
        .env_remove("DYLD_PRINT_TO_FILE")
        .env("LC_ALL", "C");

    let mut child = command.spawn().map_err(|_| unavailable())?;
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let status = wait_bounded(&mut child);

    let mut output = Vec::new();
    if let Some(mut stdout) = stdout {
        output.extend(policy::read_bounded(
            &mut stdout,
            policy::MAX_SPCTL_OUTPUT_BYTES,
        ));
    }
    if let Some(mut stderr) = stderr {
        output.extend(policy::read_bounded(
            &mut stderr,
            policy::MAX_SPCTL_OUTPUT_BYTES,
        ));
    }

    let code = status.and_then(|status| status.code());
    match policy::classify_assessment(code, &output) {
        policy::SpctlOutcome::Accepted => Ok(()),
        policy::SpctlOutcome::Denied => Err(trust_denied()),
        policy::SpctlOutcome::Unavailable => Err(unavailable()),
    }
}

/// Wait for the assessment, bounded by [`policy::SPCTL_TIMEOUT`].
///
/// Returns `None` for a timeout or wait failure after killing and reaping the
/// child, so no process is left behind and no verdict is invented.
fn wait_bounded(child: &mut Child) -> Option<ExitStatus> {
    let deadline = Instant::now() + policy::SPCTL_TIMEOUT;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Some(status),
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return None;
                }
                thread::sleep(SPCTL_POLL_INTERVAL);
            }
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
}

fn open_carrier(package_root: &Path) -> InstallResult<PackageSource> {
    let candidate = package_root.join(PACKAGE_MACOS_CARRIER);
    if !path_is_within(package_root, &candidate) {
        return Err(unsafe_path());
    }
    match fs::symlink_metadata(&candidate) {
        Ok(_) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Err(invalid_package()),
        Err(_) => return Err(io_failure()),
    }
    let carrier = PackageSource::open(&candidate)?;
    if carrier.kind() != PackageKind::Directory {
        return Err(invalid_package());
    }
    Ok(carrier)
}

fn read_carrier_resource(carrier: &PackageSource) -> InstallResult<Vec<u8>> {
    let relative = PathBuf::from(CARRIER_RESOURCE_RELATIVE);
    let resource = carrier.path().join("Contents").join(relative);
    if !path_is_within(carrier.path(), &resource) {
        return Err(unsafe_path());
    }
    let canonical = fs::canonicalize(&resource).map_err(|_| invalid_package())?;
    if !path_is_within(carrier.path(), &canonical) {
        return Err(unsafe_path());
    }
    let metadata = fs::symlink_metadata(&canonical).map_err(|_| invalid_package())?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(unsafe_path());
    }
    if metadata.len() == 0 || metadata.len() > MAX_CARRIER_BYTES as u64 {
        return Err(invalid_package());
    }
    let bytes = fs::read(&canonical).map_err(|_| io_failure())?;
    if bytes.len() as u64 != metadata.len() {
        return Err(io_failure());
    }
    Ok(bytes)
}

fn file_url(path: &Path) -> InstallResult<Cf> {
    let bytes = path.as_os_str().as_bytes();
    // SAFETY: `bytes` is a live filesystem representation of the path.
    let url = unsafe {
        CFURLCreateFromFileSystemRepresentation(
            ptr::null(),
            bytes.as_ptr(),
            bytes.len() as isize,
            1,
        )
    };
    Cf::new(url).ok_or_else(invalid_input)
}

fn cf_string_value(value: &str) -> InstallResult<Cf> {
    let bytes = value.as_bytes();
    // SAFETY: `bytes` is a live UTF-8 buffer.
    let string = unsafe {
        CFStringCreateWithBytes(
            ptr::null(),
            bytes.as_ptr(),
            bytes.len() as isize,
            K_CF_STRING_ENCODING_UTF8,
            0,
        )
    };
    Cf::new(string).ok_or_else(invalid_input)
}

fn cf_string(value: CFTypeRef) -> Option<String> {
    if value.is_null() {
        return None;
    }
    // SAFETY: `value` is a live CF object; its type ID is checked before it is
    // used as a string.
    if unsafe { CFGetTypeID(value) } != unsafe { CFStringGetTypeID() } {
        return None;
    }
    let mut buffer = [0 as c_char; 1024];
    // SAFETY: the buffer is live for the call and large enough for a team ID.
    let written = unsafe {
        CFStringGetCString(
            value,
            buffer.as_mut_ptr(),
            buffer.len() as isize,
            K_CF_STRING_ENCODING_UTF8,
        )
    };
    if written == 0 {
        return None;
    }
    // SAFETY: CFStringGetCString NUL-terminated the buffer.
    Some(
        unsafe { CStr::from_ptr(buffer.as_ptr()) }
            .to_str()
            .ok()?
            .to_owned(),
    )
}

/// Classify a Security.framework status into the installer vocabulary.
fn classify(status: i32) -> InstallFailure {
    policy::classify_security_status(status).failure()
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

fn unavailable() -> InstallFailure {
    InstallFailure::new(FailureCode::TrustUnavailable)
}
