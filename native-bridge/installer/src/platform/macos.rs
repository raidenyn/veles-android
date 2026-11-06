//! macOS OS trust adapter (Task 4).
//!
//! [`MacosPlatform::verify_trust`] is read-only and implements the macOS half of
//! the package-authentication contract with Security.framework:
//!
//! * `SecStaticCodeCreateWithPath` + `SecStaticCodeCheckValidity` validate the
//!   carrier app bundle with all architectures, nested code, the **resource
//!   seal**, strict bundle structure, symlink/sideband restrictions, certificate
//!   expiration and enforced certificate revocation checks. Revocation checks are
//!   never disabled and no network-access opt-out is requested;
//! * the Team ID from `SecCodeCopySigningInformation` is required to be a
//!   well-formed Apple team identifier, and the bundle must satisfy a
//!   Team-ID-pinned designated requirement (`anchor apple generic` plus
//!   `certificate leaf[subject.OU]`; production additionally requires the
//!   Developer ID intermediate/leaf markers);
//! * the policy carrier resource under `Contents/Resources` must be the exact
//!   sealed resource (`SecCodeValidateFileResource`), so a replaced carrier fails
//!   closed even though the app signature still verifies;
//! * Gatekeeper's own assessment (`SecAssessmentCreate` for the *execute*
//!   operation) must allow the bundle, and a production package must also report
//!   a notarization date and a stapled notarization ticket.
//!
//! A non-zero `codesign`-style exit code is never accepted as evidence: every
//! verdict above is the OS trust engine's, not a command exit status. The
//! remaining [`Platform`] methods fail closed; Tasks 5 and 8 complete them.

use std::ffi::{CStr, c_char, c_void};
use std::fs;
use std::io;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::ptr;

use super::{
    HostState, InstallLock, OwnedEntry, PACKAGE_MACOS_CARRIER, PackageKind, PackageSource,
    Platform, Registration, TRUST_ASSESSMENT_MACOS_GATEKEEPER, TrustProof, ValidatedPath,
    path_is_within,
};
use crate::model::{
    Channel, FailureCode, InstallFailure, InstallResult, ReleaseDescriptor, Target,
};
use crate::paths::InstallPaths;

/// Sealed carrier resource, relative to the bundle's `Contents` directory.
const CARRIER_RESOURCE_RELATIVE: &str = "Resources/veles-policy-carrier";
/// Bound on the sealed carrier bytes (`TrustProof` enforces the same limit).
const MAX_CARRIER_BYTES: usize =
    crate::policy::CARRIER_HEADER_LEN + crate::model::MAX_DOCUMENT_BYTES;

const K_CF_STRING_ENCODING_UTF8: u32 = 0x0800_0100;

// SecCSFlags values from SecStaticCode.h. `kSecCSContentInformation` and
// `kSecCSStrictValidate` intentionally share a numeric value: they belong to
// different flag sets (information request vs validation option).
const K_SEC_CS_SIGNING_INFORMATION: u32 = 1 << 1;
const K_SEC_CS_CONTENT_INFORMATION: u32 = 1 << 4;
const K_SEC_CS_CHECK_ALL_ARCHITECTURES: u32 = 1 << 0;
const K_SEC_CS_CHECK_NESTED_CODE: u32 = 1 << 3;
const K_SEC_CS_STRICT_VALIDATE: u32 = 1 << 4;
const K_SEC_CS_RESTRICT_SYMLINKS: u32 = 1 << 7;
const K_SEC_CS_RESTRICT_SIDEBAND_DATA: u32 = 1 << 9;
// Global flags assigned from the high bit down (CSCommon.h).
const K_SEC_CS_CONSIDER_EXPIRATION: u32 = 1 << 31;
const K_SEC_CS_ENFORCE_REVOCATION_CHECKS: u32 = 1 << 30;

// CSCommon.h OSStatus values. The SecBase certificate values (-67818/-67820/
// -67843/-67844) are the long-standing Security.framework certificate codes.
const ERR_SEC_CS_UNIMPLEMENTED: i32 = -67072;
const ERR_SEC_CS_INVALID_OBJECT_REF: i32 = -67071;
const ERR_SEC_CS_INVALID_FLAGS: i32 = -67070;
const ERR_SEC_CS_OBJECT_REQUIRED: i32 = -67069;
const ERR_SEC_CS_STATIC_CODE_NOT_FOUND: i32 = -67068;
const ERR_SEC_CS_UNSIGNED: i32 = -67062;
const ERR_SEC_CS_SIGNATURE_FAILED: i32 = -67061;
const ERR_SEC_CS_SIGNATURE_NOT_VERIFIABLE: i32 = -67060;
const ERR_SEC_CS_SIGNATURE_UNSUPPORTED: i32 = -67059;
const ERR_SEC_CS_BAD_DICTIONARY_FORMAT: i32 = -67058;
const ERR_SEC_CS_RESOURCES_NOT_SEALED: i32 = -67057;
const ERR_SEC_CS_RESOURCES_NOT_FOUND: i32 = -67056;
const ERR_SEC_CS_RESOURCES_INVALID: i32 = -67055;
const ERR_SEC_CS_BAD_RESOURCE: i32 = -67054;
const ERR_SEC_CS_RESOURCE_RULES_INVALID: i32 = -67053;
const ERR_SEC_CS_REQ_INVALID: i32 = -67052;
const ERR_SEC_CS_REQ_UNSUPPORTED: i32 = -67051;
const ERR_SEC_CS_REQ_FAILED: i32 = -67050;
const ERR_SEC_CS_BAD_OBJECT_FORMAT: i32 = -67049;
const ERR_SEC_CS_INTERNAL_ERROR: i32 = -67048;
const ERR_SEC_CS_SIGNATURE_INVALID: i32 = -67045;
const ERR_SEC_CS_HELPER_FAILED: i32 = -67019;
const ERR_SEC_CS_BAD_BUNDLE_FORMAT: i32 = -67028;
const ERR_SEC_CS_BAD_MAIN_EXECUTABLE: i32 = -67010;
const ERR_SEC_CS_UNSEALED_APP_ROOT: i32 = -67014;
const ERR_SEC_CS_RESOURCE_NOT_SUPPORTED: i32 = -67016;
const ERR_SEC_CS_UNSEALED_FRAMEWORK_ROOT: i32 = -67008;
const ERR_SEC_CS_WEAK_RESOURCE_ENVELOPE: i32 = -67007;
const ERR_SEC_CS_CANCELLED: i32 = -67006;
const ERR_SEC_CS_INVALID_SYMLINK: i32 = -67003;
const ERR_SEC_CS_NOT_APP_LIKE: i32 = -67002;
const ERR_SEC_CS_SIGNATURE_UNTRUSTED: i32 = -66996;
const ERR_SEC_CS_INVALID_TEAM_IDENTIFIER: i32 = -66998;
const ERR_SEC_CS_BAD_TEAM_IDENTIFIER: i32 = -66997;
const ERR_SEC_CS_REVOKED_NOTARIZATION: i32 = -66992;
const ERR_SEC_CS_UNSIGNED_NESTED_CODE: i32 = -67022;
const ERR_SEC_CS_BAD_NESTED_CODE: i32 = -67021;
const ERR_SEC_CS_STATIC_CODE_CHANGED: i32 = -67034;
const ERR_SEC_CERTIFICATE_EXPIRED: i32 = -67818;
const ERR_SEC_CERTIFICATE_REVOKED: i32 = -67820;
const ERR_SEC_NOT_TRUSTED: i32 = -67843;
const ERR_SEC_TRUST_SETTING_DENY: i32 = -67844;

type CFTypeRef = *const c_void;
type CFStringRef = *const c_void;
type CFURLRef = *const c_void;
type CFDictionaryRef = *const c_void;
type CFDataRef = *const c_void;
type CFErrorRef = *mut c_void;
type SecStaticCodeRef = *const c_void;
type SecRequirementRef = *const c_void;
type SecAssessmentRef = *const c_void;
type OSStatus = i32;
type Boolean = u8;

type CFDictionaryRetainCallBack =
    Option<unsafe extern "C" fn(*const c_void, *const c_void) -> *const c_void>;
type CFDictionaryReleaseCallBack = Option<unsafe extern "C" fn(*const c_void, *const c_void)>;
type CFDictionaryCopyDescriptionCallBack =
    Option<unsafe extern "C" fn(*const c_void) -> *const c_void>;
type CFDictionaryEqualCallBack =
    Option<unsafe extern "C" fn(*const c_void, *const c_void) -> Boolean>;
type CFDictionaryHashCallBack = Option<unsafe extern "C" fn(*const c_void) -> usize>;

#[repr(C)]
struct CFDictionaryKeyCallBacks {
    version: isize,
    retain: CFDictionaryRetainCallBack,
    release: CFDictionaryReleaseCallBack,
    copy_description: CFDictionaryCopyDescriptionCallBack,
    equal: CFDictionaryEqualCallBack,
    hash: CFDictionaryHashCallBack,
}

#[repr(C)]
struct CFDictionaryValueCallBacks {
    version: isize,
    retain: CFDictionaryRetainCallBack,
    release: CFDictionaryReleaseCallBack,
    copy_description: CFDictionaryCopyDescriptionCallBack,
    equal: CFDictionaryEqualCallBack,
}

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
    fn CFBooleanGetTypeID() -> usize;
    fn CFBooleanGetValue(value: CFTypeRef) -> Boolean;
    fn CFGetTypeID(value: CFTypeRef) -> usize;
    fn CFDictionaryGetValue(dict: CFDictionaryRef, key: *const c_void) -> CFTypeRef;
    fn CFDictionaryCreateMutable(
        allocator: CFTypeRef,
        capacity: isize,
        key_callbacks: *const CFDictionaryKeyCallBacks,
        value_callbacks: *const CFDictionaryValueCallBacks,
    ) -> CFDictionaryRef;
    fn CFDictionarySetValue(dict: CFDictionaryRef, key: *const c_void, value: *const c_void);
    fn CFDataCreate(allocator: CFTypeRef, bytes: *const u8, length: isize) -> CFDataRef;
    fn CFRelease(value: CFTypeRef);
    static kCFTypeDictionaryKeyCallBacks: CFDictionaryKeyCallBacks;
    static kCFTypeDictionaryValueCallBacks: CFDictionaryValueCallBacks;
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
    fn SecCodeValidateFileResource(
        static_code: SecStaticCodeRef,
        relative_path: CFStringRef,
        file_data: CFDataRef,
        flags: u32,
    ) -> OSStatus;
    // Gatekeeper's own assessment API (the one `spctl` uses). It is exported by
    // Security.framework but not declared in the public SDK headers, so native
    // linkage must be confirmed on a macOS runner before acceptance.
    fn SecAssessmentCreate(
        path: CFURLRef,
        flags: u64,
        context: CFDictionaryRef,
        errors: *mut CFErrorRef,
    ) -> SecAssessmentRef;
    fn SecAssessmentCopyResult(
        assessment: SecAssessmentRef,
        flags: u64,
        errors: *mut CFErrorRef,
    ) -> CFDictionaryRef;
    static kSecAssessmentContextKeyOperation: CFStringRef;
    static kSecAssessmentOperationTypeExecute: CFStringRef;
    static kSecAssessmentAssessmentVerdict: CFStringRef;
    static kSecAssessmentAssessmentNotarizationDate: CFStringRef;
    static kSecCodeInfoTeamIdentifier: CFStringRef;
    static kSecCodeInfoStapledNotarizationTicket: CFStringRef;
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

    let mut information: CFDictionaryRef = ptr::null();
    // SAFETY: `static_code` is a live static code reference and `information` is
    // a live out-parameter.
    let status = unsafe {
        SecCodeCopySigningInformation(
            static_code.0,
            K_SEC_CS_SIGNING_INFORMATION | K_SEC_CS_CONTENT_INFORMATION,
            &mut information,
        )
    };
    if status != 0 {
        return Err(classify(status));
    }
    let information = Cf::new(information).ok_or_else(unavailable)?;

    // SAFETY: the dictionary is a live retained signing-information dictionary;
    // keys are the exported CFString constants.
    let team_id = unsafe {
        cf_string(CFDictionaryGetValue(
            information.0,
            kSecCodeInfoTeamIdentifier,
        ))
    }
    .ok_or_else(trust_denied)?;
    if !is_valid_team_id(&team_id) {
        return Err(trust_denied());
    }

    let requirement_text = publisher_requirement(expected.channel, &team_id);
    let requirement_text = cf_string_value(&requirement_text)?;
    let mut requirement: SecRequirementRef = ptr::null();
    // SAFETY: the CFString is live and `requirement` is a live out-parameter.
    let status = unsafe { SecRequirementCreateWithString(requirement_text.0, 0, &mut requirement) };
    if status != 0 {
        return Err(classify(status));
    }
    let requirement = Cf::new(requirement).ok_or_else(trust_denied)?;

    // Strict signature + resource-envelope validation against the publisher
    // requirement. Revocation and expiration checks are enabled, never bypassed.
    let flags = K_SEC_CS_CHECK_ALL_ARCHITECTURES
        | K_SEC_CS_CHECK_NESTED_CODE
        | K_SEC_CS_STRICT_VALIDATE
        | K_SEC_CS_RESTRICT_SYMLINKS
        | K_SEC_CS_RESTRICT_SIDEBAND_DATA
        | K_SEC_CS_CONSIDER_EXPIRATION
        | K_SEC_CS_ENFORCE_REVOCATION_CHECKS;
    // SAFETY: both references are live retained code-signing objects.
    let status = unsafe { SecStaticCodeCheckValidity(static_code.0, flags, requirement.0) };
    if status != 0 {
        return Err(classify(status));
    }

    if expected.channel.is_production() {
        // SAFETY: `information` is a live signing-information dictionary.
        let ticket =
            unsafe { CFDictionaryGetValue(information.0, kSecCodeInfoStapledNotarizationTicket) };
        if ticket.is_null() {
            return Err(trust_denied());
        }
    }

    let carrier_bytes = read_carrier_resource(&carrier)?;
    let relative = cf_string_value(CARRIER_RESOURCE_RELATIVE)?;
    let data = Cf::new(unsafe {
        CFDataCreate(
            ptr::null(),
            carrier_bytes.as_ptr(),
            carrier_bytes.len() as isize,
        )
    })
    .ok_or_else(unavailable)?;
    // The exact bytes read from disk must be the sealed resource; a replaced
    // carrier fails closed even though the app's executable is still signed.
    // SAFETY: `static_code`, `relative` and `data` are live CF objects.
    let status = unsafe { SecCodeValidateFileResource(static_code.0, relative.0, data.0, 0) };
    if status != 0 {
        return Err(classify(status));
    }

    check_gatekeeper(url.0, expected.channel)?;

    TrustProof::new(
        format!("team:{team_id}"),
        TRUST_ASSESSMENT_MACOS_GATEKEEPER,
        carrier_bytes,
    )
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

/// Build the publisher designated requirement.
///
/// `team_id` is OS-verified signing information and is validated as ten
/// uppercase alphanumeric characters before it is interpolated, so it cannot
/// inject requirement syntax.
fn publisher_requirement(channel: Channel, team_id: &str) -> String {
    if channel.is_production() {
        format!(
            "anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */ \
             and certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */ \
             and certificate leaf[subject.OU] = \"{team_id}\""
        )
    } else {
        format!("anchor apple generic and certificate leaf[subject.OU] = \"{team_id}\"")
    }
}

fn is_valid_team_id(team_id: &str) -> bool {
    team_id.len() == 10
        && team_id
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit())
}

fn check_gatekeeper(url: CFURLRef, channel: Channel) -> InstallResult<()> {
    // SAFETY: the CF callback statics are the CoreFoundation exported constants.
    let context = Cf::new(unsafe {
        CFDictionaryCreateMutable(
            ptr::null(),
            1,
            ptr::addr_of!(kCFTypeDictionaryKeyCallBacks),
            ptr::addr_of!(kCFTypeDictionaryValueCallBacks),
        )
    })
    .ok_or_else(unavailable)?;
    // SAFETY: the context is a live mutable dictionary and the keys/values are
    // the exported CFString constants for the execute operation.
    unsafe {
        CFDictionarySetValue(
            context.0,
            kSecAssessmentContextKeyOperation,
            kSecAssessmentOperationTypeExecute,
        );
    }

    let mut error: CFErrorRef = ptr::null_mut();
    // SAFETY: `url` is live, `context` is a live dictionary and `error` is a
    // live out-parameter.
    let assessment = unsafe { SecAssessmentCreate(url, 0, context.0, &mut error) };
    release_error(error);
    let assessment = Cf::new(assessment).ok_or_else(unavailable)?;

    let mut error: CFErrorRef = ptr::null_mut();
    // SAFETY: the assessment is live and `error` is a live out-parameter.
    let result = unsafe { SecAssessmentCopyResult(assessment.0, 0, &mut error) };
    release_error(error);
    let result = Cf::new(result).ok_or_else(unavailable)?;

    // SAFETY: `result` is a live assessment-result dictionary.
    let verdict = unsafe { CFDictionaryGetValue(result.0, kSecAssessmentAssessmentVerdict) };
    if verdict.is_null()
        // SAFETY: the values are live CF objects.
        || unsafe { CFGetTypeID(verdict) } != unsafe { CFBooleanGetTypeID() }
        || unsafe { CFBooleanGetValue(verdict) } == 0
    {
        return Err(trust_denied());
    }

    if channel.is_production() {
        // SAFETY: `result` is a live dictionary.
        let notarized =
            unsafe { CFDictionaryGetValue(result.0, kSecAssessmentAssessmentNotarizationDate) };
        if notarized.is_null() {
            return Err(trust_denied());
        }
    }
    Ok(())
}

fn release_error(error: CFErrorRef) {
    if !error.is_null() {
        // SAFETY: the caller owns the returned CFError reference.
        unsafe { CFRelease(error.cast()) };
    }
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
    match status {
        ERR_SEC_CS_UNSIGNED
        | ERR_SEC_CS_SIGNATURE_FAILED
        | ERR_SEC_CS_SIGNATURE_UNSUPPORTED
        | ERR_SEC_CS_BAD_DICTIONARY_FORMAT
        | ERR_SEC_CS_RESOURCES_NOT_SEALED
        | ERR_SEC_CS_RESOURCES_NOT_FOUND
        | ERR_SEC_CS_RESOURCES_INVALID
        | ERR_SEC_CS_BAD_RESOURCE
        | ERR_SEC_CS_RESOURCE_RULES_INVALID
        | ERR_SEC_CS_REQ_INVALID
        | ERR_SEC_CS_REQ_UNSUPPORTED
        | ERR_SEC_CS_REQ_FAILED
        | ERR_SEC_CS_BAD_OBJECT_FORMAT
        | ERR_SEC_CS_SIGNATURE_INVALID
        | ERR_SEC_CS_BAD_BUNDLE_FORMAT
        | ERR_SEC_CS_BAD_MAIN_EXECUTABLE
        | ERR_SEC_CS_UNSEALED_APP_ROOT
        | ERR_SEC_CS_UNSEALED_FRAMEWORK_ROOT
        | ERR_SEC_CS_RESOURCE_NOT_SUPPORTED
        | ERR_SEC_CS_WEAK_RESOURCE_ENVELOPE
        | ERR_SEC_CS_INVALID_SYMLINK
        | ERR_SEC_CS_NOT_APP_LIKE
        | ERR_SEC_CS_SIGNATURE_UNTRUSTED
        | ERR_SEC_CS_INVALID_TEAM_IDENTIFIER
        | ERR_SEC_CS_BAD_TEAM_IDENTIFIER
        | ERR_SEC_CS_REVOKED_NOTARIZATION
        | ERR_SEC_CS_UNSIGNED_NESTED_CODE
        | ERR_SEC_CS_BAD_NESTED_CODE
        | ERR_SEC_CS_STATIC_CODE_CHANGED
        | ERR_SEC_CERTIFICATE_EXPIRED
        | ERR_SEC_CERTIFICATE_REVOKED
        | ERR_SEC_NOT_TRUSTED
        | ERR_SEC_TRUST_SETTING_DENY => trust_denied(),
        // An assessment that could not be formed (permissions, missing code,
        // broken trust database, API misuse) is retryable, never a success.
        ERR_SEC_CS_UNIMPLEMENTED
        | ERR_SEC_CS_INVALID_OBJECT_REF
        | ERR_SEC_CS_INVALID_FLAGS
        | ERR_SEC_CS_OBJECT_REQUIRED
        | ERR_SEC_CS_STATIC_CODE_NOT_FOUND
        | ERR_SEC_CS_SIGNATURE_NOT_VERIFIABLE
        | ERR_SEC_CS_INTERNAL_ERROR
        | ERR_SEC_CS_HELPER_FAILED
        | ERR_SEC_CS_CANCELLED => unavailable(),
        _ => unavailable(),
    }
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
