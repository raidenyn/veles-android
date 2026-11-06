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
//! The remaining [`Platform`] methods fail closed; Task 5 implements
//! [`Platform::inspect_hosts`] and Task 8 completes filesystem, registration and
//! cleanup.
//!
//! ## Host inspection (Task 5)
//!
//! [`MacosPlatform::inspect_hosts`] reads the process table with `libproc`
//! (`proc_listpids`, `proc_pidpath`, `proc_pidinfo(PROC_PIDTBSDINFO)`) and hands
//! the observations to the portable classifier. The image is resolved **first**:
//! a process whose image is outside the owned trees cannot be executing an owned
//! host, whatever user it runs as, so its uid is never needed and an unrelated
//! system process cannot block the installer merely because its user information
//! is unreadable. Only a process with an image inside the owned trees (or with no
//! resolvable image at all) needs attribution; there `proc_pidinfo` is tried
//! first and a `sysctl(KERN_PROC_PID)`/`kinfo_proc` lookup is the fallback, so one
//! failing channel does not make the installer permanently unusable. A process
//! whose image is owned and whose uid still cannot be determined is
//! [`super::HostState::Unknown`] and blocks. An empty PID list is an error, never
//! "no host", and every candidate is re-read by PID before the decision so a
//! recycled PID is not mistaken for the observed process.

use std::ffi::{CStr, CString, OsStr, c_char, c_int, c_void};
use std::fs;
use std::fs::File;
use std::io;
use std::mem::size_of;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::io::{AsRawFd, FromRawFd};
use std::path::{Path, PathBuf};
use std::process::{self, Child, Command, ExitStatus, Stdio};
use std::ptr;
use std::thread;
use std::time::{Duration, Instant};

use super::macos_trust as policy;
use super::native::{
    NativeFs, native_carrier_entries, native_ensure_layout, native_flush_parent,
    native_install_carrier, native_lock, native_publish_manifest, native_quarantine_state,
    native_read_registration, native_read_state, native_remove_manifest, native_remove_owned,
    native_replace_durable, native_replace_file, native_write_durable, native_write_state,
    read_back, require_published_manifest,
};
use super::{
    CarrierEntry, HostAssessment, HostState, ImageClass, InstallLock, OwnedEntry, OwnedImages,
    PACKAGE_MACOS_CARRIER, PackageKind, PackageSource, PathKind, Platform, ProcessIdentity,
    ProcessImage, ProcessObservation, ProcessScope, ProcessTable, Registration,
    TRUST_ASSESSMENT_MACOS_GATEKEEPER, TrustProof, ValidatedPath, inspect_host_processes,
    path_is_within,
};
use sha2::{Digest as _, Sha256};

use crate::cleanup::RemoveOutcome;
use crate::model::{Digest, FailureCode, InstallFailure, InstallResult, ReleaseDescriptor, Target};
use crate::paths::InstallPaths;

/// Sealed carrier resource, relative to the bundle's `Contents` directory.
const CARRIER_RESOURCE_RELATIVE: &str = "Resources/veles-policy-carrier";
/// Bound on the sealed carrier bytes (`TrustProof` enforces the same limit).
const MAX_CARRIER_BYTES: usize = crate::policy::MAX_CARRIER_DOCUMENT_BYTES;
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

// `libproc` and `libc` are part of libSystem, which every macOS binary links.
unsafe extern "C" {
    fn proc_listpids(kind: u32, typeinfo: u32, buffer: *mut c_void, buffersize: i32) -> i32;
    fn proc_pidinfo(pid: i32, flavor: i32, arg: u64, buffer: *mut c_void, buffersize: i32) -> i32;
    fn proc_pidpath(pid: i32, buffer: *mut c_void, buffersize: u32) -> i32;
    fn geteuid() -> u32;
    fn sysctl(
        name: *mut i32,
        namelen: u32,
        oldp: *mut c_void,
        oldlenp: *mut usize,
        newp: *mut c_void,
        newlen: usize,
    ) -> i32;
}

/// `proc_listpids` selector for every process.
const PROC_ALL_PIDS: u32 = 1;
/// `proc_pidinfo` flavor returning a [`ProcBsdInfo`].
const PROC_PIDTBSDINFO: i32 = 3;
/// `PATH_MAX` is 1024 on macOS; one page is a safe upper bound for an image path.
const MAX_PROCESS_PATH_BYTES: usize = 4096;
/// `MAXCOMLEN` from `sys/proc_info.h`: `pbi_comm` is `MAXCOMLEN` bytes and
/// `pbi_name` is `2 * MAXCOMLEN` bytes.
const MAXCOMLEN: usize = 16;

/// `sys/sysctl.h`: `CTL_KERN`, `KERN_PROC` and the `KERN_PROC_PID` selector.
const CTL_KERN: i32 = 1;
const KERN_PROC: i32 = 14;
const KERN_PROC_PID: i32 = 1;
/// `sizeof(struct kinfo_proc)` on 64-bit macOS (LP64 `extern_proc` + `eproc`).
///
/// Only the fixed prefix declared in [`KinfoProcPrefix`] is read; the full size
/// is what a `sysctl(KERN_PROC_PID)` call writes into the buffer.
const KINFO_PROC_BYTES: usize = 648;

/// `struct proc_bsdinfo` from `sys/proc_info.h`, used only through
/// `proc_pidinfo(PROC_PIDTBSDINFO)`.
///
/// The layout is asserted at compile time for the macOS target: if the SDK
/// changes it, the adapter fails to build instead of silently reading the wrong
/// uid.
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct ProcBsdInfo {
    flags: u32,
    status: u32,
    xstatus: u32,
    pid: u32,
    ppid: u32,
    uid: u32,
    gid: u32,
    ruid: u32,
    rgid: u32,
    svuid: u32,
    svgid: u32,
    reserved: u32,
    comm: [u8; MAXCOMLEN],
    name: [u8; 2 * MAXCOMLEN],
    files: u32,
    pgid: u32,
    jobc: u32,
    tdev: u32,
    tpgid: u32,
    nice: i32,
    start_sec: u64,
    start_usec: u64,
}

const _: () = assert!(size_of::<ProcBsdInfo>() == 136);

/// The fixed prefix of `struct kinfo_proc` that `sysctl(KERN_PROC_PID)` returns.
///
/// Only `kp_proc.p_pid` and `kp_eproc.e_ucred.cr_uid` are read; the
/// version-dependent tail (`e_vm` and later) is deliberately not declared. The
/// byte layout is `struct extern_proc` (296 bytes on LP64, `p_pid` at offset 40)
/// followed by `e_paddr`, `e_sess`, `struct _pcred` (104 bytes) and
/// `struct _ucred` (`cr_ref` then `cr_uid`) from `bsd/sys/sysctl.h`. The size and
/// field offsets are asserted, and [`sysctl_uid`] re-checks the returned pid, so a
/// layout change can only leave a process unattributed - it can never report a
/// wrong user.
#[repr(C)]
struct KinfoProcPrefix {
    /// `struct extern_proc` bytes before `p_pid`.
    extern_proc: [u8; 40],
    /// `p_pid`.
    pid: i32,
    /// `struct extern_proc` bytes after `p_pid` (296 - 44).
    extern_proc_tail: [u8; 252],
    /// `kp_eproc.e_paddr`.
    e_paddr: *mut c_void,
    /// `kp_eproc.e_sess`.
    e_sess: *mut c_void,
    /// `kp_eproc.e_pcred`.
    pcred: [u8; 104],
    /// `kp_eproc.e_ucred.cr_ref`.
    cr_ref: i32,
    /// `kp_eproc.e_ucred.cr_uid`.
    cr_uid: u32,
}

const _: () = assert!(size_of::<KinfoProcPrefix>() == 424);
const _: () = assert!(core::mem::offset_of!(KinfoProcPrefix, pid) == 40);
const _: () = assert!(core::mem::offset_of!(KinfoProcPrefix, cr_uid) == 420);

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
/// Task 4 implements [`Platform::verify_trust`] and Task 5 implements
/// [`Platform::inspect_hosts`]; Task 8 completes the native filesystem and
/// Chrome registration surface.
#[derive(Debug, Default)]
pub struct MacosPlatform;

impl MacosPlatform {
    /// Construct the macOS adapter.
    pub fn new() -> Self {
        Self
    }
}

impl Platform for MacosPlatform {
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
        // Created relative to the captured parent handle; the parent is flushed
        // through that same handle.
        self.create_directory_at(path)
    }

    fn publish_manifest(&self, paths: &InstallPaths, value: &Registration) -> InstallResult<()> {
        native_publish_manifest(self, paths, value)
    }

    fn replace_manifest(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        native_replace_file(self, staged, live)
    }

    /// The live manifest file. macOS registers a Native Messaging host by the
    /// manifest file itself, so there is no separate registry value and no
    /// [`WindowsPreimage`](super::WindowsPreimage) to capture.
    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>> {
        native_read_registration(self, paths)
    }

    /// Publication step two on macOS is the ordered verification of step one:
    /// the live manifest must still be exactly the bytes just published, and a
    /// manifest another product owns is refused before anything is written.
    fn publish_registration(
        &self,
        paths: &InstallPaths,
        value: &Registration,
    ) -> InstallResult<()> {
        require_published_manifest(self, paths, value)
    }

    fn remove_registration(
        &self,
        paths: &InstallPaths,
        expected: &Registration,
    ) -> InstallResult<()> {
        native_remove_manifest(self, paths, expected)
    }

    fn inspect_hosts(&self, paths: &InstallPaths) -> InstallResult<HostState> {
        Ok(inspect_host_processes(paths, &MacosProcessTable))
    }

    fn verify_trust(
        &self,
        source: &PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        verify_trust(source, expected)
    }

    /// A retained version directory holds the same sealed `carrier.app` a fresh
    /// package does, so the retained check is the identical Gatekeeper path.
    fn verify_retained(
        &self,
        source: &PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        verify_trust(source, expected)
    }

    /// Strict signature, designated requirement, resource seal and Gatekeeper
    /// assessment of the **host** code: the enclosing `.app` bundle with all
    /// nested code (or the bare Mach-O), never only the carrier.
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

/// The code object the OS must assess for a host executable: the outermost
/// `*.app` bundle containing it (validated as a whole, nested code included), or
/// the executable itself when it is not inside a bundle.
fn host_subject(root: &Path, relative: &str) -> std::path::PathBuf {
    let mut subject = root.to_path_buf();
    for component in relative.split('/') {
        subject.push(component);
        if component.len() > 4 && component.ends_with(".app") {
            return subject;
        }
    }
    root.join(relative)
}

/// Assess the host code with the same strict path the carrier takes.
///
/// The requirement is built only from a Team ID the authenticated release policy
/// already lists, `SecStaticCodeCheckValidity` runs with the shared strict flags
/// (nested code and the resource seal included, revocation never bypassed) and
/// the Gatekeeper execution assessment must accept the same subject.
fn verify_host(
    root: &Path,
    executable: &str,
    expected: &ReleaseDescriptor,
) -> InstallResult<HostAssessment> {
    if expected.target != Target::MacosArm64 {
        return Err(unsupported());
    }
    let relative = crate::inventory::normalize_relative_path(executable)?;
    // Containment (no linked ancestor or leaf) and regular-file proof.
    drop(crate::inventory::open_entry(root, &relative)?);
    let subject = host_subject(root, &relative);
    let url = file_url(&subject)?;

    let mut static_code: SecStaticCodeRef = ptr::null();
    // SAFETY: `url` is a live CFURL for the host code path.
    let status = unsafe { SecStaticCodeCreateWithPath(url.0, 0, &mut static_code) };
    if status != 0 {
        return Err(classify(status));
    }
    let static_code = Cf::new(static_code).ok_or_else(unavailable)?;

    let team_id = observed_team_id(&static_code)?;
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

    validate(&static_code, &requirement)?;
    check_gatekeeper(&subject)?;

    HostAssessment::new(format!("team:{team_id}"), TRUST_ASSESSMENT_MACOS_GATEKEEPER)
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

/// The macOS process table, read through `libproc`.
///
/// The image is resolved before the user: a process whose `proc_pidpath` is
/// outside the owned trees is irrelevant whatever its uid, and it is not even
/// reported. Only a process that could be executing an owned image - or whose
/// image cannot be resolved at all - needs attribution, which
/// `proc_pidinfo(PROC_PIDTBSDINFO)` provides with a `sysctl(KERN_PROC_PID)`
/// fallback.
#[derive(Debug, Default)]
struct MacosProcessTable;

impl ProcessTable for MacosProcessTable {
    fn observe(&self, paths: &InstallPaths) -> io::Result<Vec<ProcessObservation>> {
        let owned = OwnedImages::capture(paths)?;
        let pids = process_ids()?;
        let mut observations = Vec::new();
        for pid in pids {
            // The kernel idle task and `launchd` run as root/system and cannot
            // execute a user-private payload; neither exposes user information.
            if pid <= 1 || pid as u32 == process::id() {
                continue;
            }
            let image = image_path(pid);
            if let Some(path) = image.as_deref()
                && owned.classify(Some(path)) == ImageClass::Foreign
            {
                // Outside the owned trees: irrelevant regardless of its user, so
                // the uid is never read (and an unreadable uid cannot block).
                continue;
            }
            observations.push(candidate_observation(pid, image));
        }
        Ok(observations)
    }

    fn recheck(&self, paths: &InstallPaths, pid: u32) -> io::Result<Option<ProcessObservation>> {
        let owned = OwnedImages::capture(paths)?;
        let image = image_path(pid as i32);
        if let Some(path) = image.as_deref()
            && owned.classify(Some(path)) == ImageClass::Foreign
        {
            // The PID now belongs to a process outside the owned trees. The
            // classifier discards a foreign image whatever the attribution, so
            // the uid is not read; `Unknown` records that it was not attributed.
            return Ok(Some(ProcessObservation {
                identity: ProcessIdentity { pid },
                scope: ProcessScope::Unknown,
                image: process_image(image),
            }));
        }
        let scope = owner_scope(pid as i32);
        if scope == ProcessScope::Unknown {
            // Distinguish "exited" from "present but unreadable": only a PID that
            // is gone is harmless, anything else cannot be ruled out. A partial
            // or empty list is an error, so it can never look like an exit.
            if !process_ids()?.contains(&(pid as i32)) {
                return Ok(None);
            }
        }
        Ok(Some(ProcessObservation {
            identity: ProcessIdentity { pid },
            scope,
            image: process_image(image),
        }))
    }
}

/// `proc_pidpath` resolved the image path, or the process has no readable one.
fn process_image(image: Option<PathBuf>) -> ProcessImage {
    match image {
        Some(path) => ProcessImage::Path(path),
        None => ProcessImage::Unresolved,
    }
}

/// One observation for a process whose image is not demonstrably foreign.
fn candidate_observation(pid: i32, image: Option<PathBuf>) -> ProcessObservation {
    ProcessObservation {
        identity: ProcessIdentity { pid: pid as u32 },
        scope: owner_scope(pid),
        image: process_image(image),
    }
}

/// The attribution of one process, from `proc_pidinfo` or the `sysctl` fallback.
fn owner_scope(pid: i32) -> ProcessScope {
    match owner_uid(pid) {
        Some(uid) if uid == current_uid() => ProcessScope::InstallingUser,
        Some(_) => ProcessScope::OtherUser,
        None => ProcessScope::Unknown,
    }
}

/// The process's effective uid, through two independent OS channels.
fn owner_uid(pid: i32) -> Option<u32> {
    bsd_info(pid)
        .map(|info| info.uid)
        .or_else(|| sysctl_uid(pid))
}

fn current_uid() -> u32 {
    // SAFETY: `geteuid` has no preconditions and cannot fail.
    unsafe { geteuid() }
}

/// Every PID the OS reports, bounded by the caller's snapshot size.
///
/// A zero-length result or a result that filled the buffer is an error: a
/// running system always has processes, and a truncated list would make a live
/// candidate look exited.
fn process_ids() -> io::Result<Vec<i32>> {
    // SAFETY: a null buffer with a zero size asks only for the required size.
    let needed = unsafe { proc_listpids(PROC_ALL_PIDS, 0, ptr::null_mut(), 0) };
    if needed <= 0 {
        return Err(io::Error::last_os_error());
    }
    // Leave room for processes that appeared between the two calls.
    let capacity = needed as usize / size_of::<i32>() + 16;
    let mut buffer = vec![0i32; capacity];
    let bytes = size_of_val(buffer.as_slice());
    // SAFETY: the buffer is live, word-aligned and `bytes` long.
    let written =
        unsafe { proc_listpids(PROC_ALL_PIDS, 0, buffer.as_mut_ptr().cast(), bytes as i32) };
    if written < 0 {
        return Err(io::Error::last_os_error());
    }
    let written = written as usize;
    if written == 0 {
        return Err(io::Error::other("empty process list"));
    }
    if written >= bytes {
        return Err(io::Error::other("process list was truncated"));
    }
    let count = (written / size_of::<i32>()).min(buffer.len());
    Ok(buffer[..count]
        .iter()
        .copied()
        .filter(|pid| *pid > 0)
        .collect())
}

/// The process's BSD info, or `None` when the OS did not write a complete one.
///
/// `proc_pidinfo` returns the number of bytes written; a complete struct is
/// accepted even when the kernel reports trailing bytes, but a short read is
/// never parsed.
fn bsd_info(pid: i32) -> Option<ProcBsdInfo> {
    let mut info = ProcBsdInfo::default();
    let size = size_of::<ProcBsdInfo>();
    // SAFETY: `info` is a live, correctly sized and aligned out-buffer.
    let written = unsafe {
        proc_pidinfo(
            pid,
            PROC_PIDTBSDINFO,
            0,
            (&mut info as *mut ProcBsdInfo).cast(),
            size as i32,
        )
    };
    if written < 0 || (written as usize) < size {
        return None;
    }
    Some(info)
}

/// The process's effective uid from `sysctl(KERN_PROC_PID)`.
///
/// This is the fallback when `proc_pidinfo` cannot attribute a process, so a
/// single failing channel does not leave the installer permanently blocked. The
/// returned pid is re-checked against the requested one: a struct-layout mismatch
/// then leaves the process unattributed instead of reporting a wrong user.
fn sysctl_uid(pid: i32) -> Option<u32> {
    let mut mib = [CTL_KERN, KERN_PROC, KERN_PROC_PID, pid];
    let mut buffer = [0u8; KINFO_PROC_BYTES];
    let mut length = buffer.len();
    // SAFETY: `mib` is a live four-element MIB, `buffer` is live and `length` is
    // its length in bytes on entry and the written length on return.
    let status = unsafe {
        sysctl(
            mib.as_mut_ptr(),
            mib.len() as u32,
            buffer.as_mut_ptr().cast(),
            &mut length,
            ptr::null_mut(),
            0,
        )
    };
    if status != 0 || length < size_of::<KinfoProcPrefix>() {
        return None;
    }
    // SAFETY: the kernel wrote at least the declared prefix into the initialized
    // buffer; `read_unaligned` copies it out without assuming the buffer's
    // alignment.
    let info = unsafe { ptr::read_unaligned(buffer.as_ptr().cast::<KinfoProcPrefix>()) };
    (info.pid == pid).then_some(info.cr_uid)
}

fn image_path(pid: i32) -> Option<PathBuf> {
    let mut buffer = vec![0u8; MAX_PROCESS_PATH_BYTES];
    // SAFETY: the buffer is live for the call.
    let written = unsafe { proc_pidpath(pid, buffer.as_mut_ptr().cast(), buffer.len() as u32) };
    if written <= 0 {
        return None;
    }
    let written = (written as usize).min(buffer.len());
    // `proc_pidpath` NUL-terminates the path; a non-UTF-8 image is still a path.
    let end = buffer[..written]
        .iter()
        .position(|byte| *byte == 0)
        .unwrap_or(written);
    Some(PathBuf::from(OsStr::from_bytes(&buffer[..end])))
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
    // One bounded read through the no-follow boundary: the pre-read metadata is
    // advisory only, the limit is what bounds the buffer.
    let bytes =
        super::read_no_follow(&canonical, MAX_CARRIER_BYTES as u64).map_err(|_| io_failure())?;
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

/// The start marker of one process, from its BSD info.
pub(crate) fn macos_process_start(pid: u32) -> Option<u64> {
    let info = bsd_info(pid as i32)?;
    Some(
        info.start_sec
            .wrapping_mul(1_000_000)
            .wrapping_add(info.start_usec),
    )
}

/// Verify the cleanup helper's own code signature and return its Team ID.
///
/// This is the same strict static-code validation the package assessment uses
/// (all architectures, nested code, strict bundle, symlink/sideband restrictions
/// and enforced revocation), with no designated requirement because the helper
/// verifies its own image and its publisher is pinned by the request. Gatekeeper
/// assessment is not repeated here: it judges policy, not whether this exact
/// image is intact.
pub(crate) fn macos_verify_own_signature(path: &Path) -> InstallResult<String> {
    let url = file_url(path)?;
    let mut static_code: SecStaticCodeRef = ptr::null();
    // SAFETY: `url` is a live CFURL for this executable.
    let status = unsafe { SecStaticCodeCreateWithPath(url.0, 0, &mut static_code) };
    if status != 0 {
        return Err(classify(status));
    }
    let static_code = Cf::new(static_code).ok_or_else(unavailable)?;
    // SAFETY: `static_code` is live; a null requirement requests no designated
    // requirement, and every strict validation flag still applies.
    let status =
        unsafe { SecStaticCodeCheckValidity(static_code.0, SEC_STATIC_CODE_FLAGS, ptr::null()) };
    if status != 0 {
        return Err(classify(status));
    }
    let team = observed_team_id(&static_code)?;
    Ok(format!("team:{team}"))
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

// ---------------------------------------------------------------------------
// Task 8: native filesystem and metadata-preserving carrier install.

/// Darwin `struct stat`, as the SDK defines it for both architectures.
///
/// Only the fields this adapter reads are named; the layout is asserted below so
/// a wrong definition cannot silently shift `st_size`/`st_ino`.
#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct Stat {
    pub(crate) st_dev: i32,
    pub(crate) st_mode: u16,
    pub(crate) st_nlink: u16,
    pub(crate) st_ino: u64,
    st_uid: u32,
    st_gid: u32,
    st_rdev: i32,
    st_atime: i64,
    st_atime_nsec: i64,
    st_mtime: i64,
    st_mtime_nsec: i64,
    st_ctime: i64,
    st_ctime_nsec: i64,
    st_birthtime: i64,
    st_birthtime_nsec: i64,
    pub(crate) st_size: i64,
    st_blocks: i64,
    st_blksize: i32,
    st_flags: u32,
    st_gen: u32,
    st_lspare: i32,
    st_qspare: [i64; 2],
}

const _: () = assert!(std::mem::size_of::<Stat>() == 144);

const S_IFMT: u32 = 0o170000;
const S_IFREG: u32 = 0o100000;
const S_IFDIR: u32 = 0o040000;
const S_IFLNK: u32 = 0o120000;

const T8_O_RDONLY: c_int = 0;
const T8_O_RDWR: c_int = 2;
const T8_O_CREAT: c_int = 0o200;
const T8_O_EXCL: c_int = 0o2000;
const T8_O_DIRECTORY: c_int = 0x10_0000;
const T8_O_NOFOLLOW: c_int = 0x0100;
const T8_O_CLOEXEC: c_int = 0x100_0000;
const AT_SYMLINK_NOFOLLOW: c_int = 0x0020;
const AT_REMOVEDIR: c_int = 0x0080;
/// Darwin `renameatx_np(2)` flag: fail with `EEXIST` instead of replacing the
/// destination, so an unexpected entry is never destroyed.
const RENAME_EXCL: u32 = 0x0000_0004;
const ENOENT: i32 = 2;
const EEXIST: i32 = 17;
const ENOTEMPTY: i32 = 66;

unsafe extern "C" {
    fn openat(directory: c_int, path: *const c_char, flags: c_int, ...) -> c_int;
    fn mkdirat(directory: c_int, path: *const c_char, mode: u32) -> c_int;
    fn renameat(
        from_directory: c_int,
        from: *const c_char,
        to_directory: c_int,
        to: *const c_char,
    ) -> c_int;
    /// `renameatx_np` is the Darwin variant that can refuse to replace an
    /// existing destination (`RENAME_EXCL`).
    fn renameatx_np(
        from_directory: c_int,
        from: *const c_char,
        to_directory: c_int,
        to: *const c_char,
        flags: u32,
    ) -> c_int;
    fn unlinkat(directory: c_int, path: *const c_char, flags: c_int) -> c_int;
    fn symlinkat(target: *const c_char, directory: c_int, path: *const c_char) -> c_int;
    fn readlinkat(directory: c_int, path: *const c_char, buffer: *mut c_char, size: usize)
    -> isize;
    fn fchmod(fd: c_int, mode: u32) -> c_int;
    fn fstat(fd: c_int, status: *mut Stat) -> c_int;
    fn fstatat(directory: c_int, path: *const c_char, status: *mut Stat, flags: c_int) -> c_int;
    fn fsync(fd: c_int) -> c_int;
    fn close(fd: c_int) -> c_int;
    fn dup(fd: c_int) -> c_int;
    fn fcopyfile(from: c_int, to: c_int, state: *mut c_void, flags: u32) -> c_int;
}

/// One single component as a NUL-terminated C string.
fn c_component(name: &OsStr) -> InstallResult<CString> {
    CString::new(name.as_bytes()).map_err(|_| unsafe_path())
}

fn stat_of(fd: c_int) -> InstallResult<Stat> {
    let mut status = std::mem::MaybeUninit::<Stat>::zeroed();
    // SAFETY: the out-buffer is live and correctly sized.
    if unsafe { fstat(fd, status.as_mut_ptr()) } != 0 {
        return Err(io_failure());
    }
    // SAFETY: fstat initialized the structure on success.
    Ok(unsafe { status.assume_init() })
}

fn stat_at(directory: c_int, name: &CString) -> io::Result<Stat> {
    let mut status = std::mem::MaybeUninit::<Stat>::zeroed();
    // SAFETY: the out-buffer is live, `name` is NUL-terminated and the flag makes
    // the call lstat-like.
    if unsafe {
        fstatat(
            directory,
            name.as_ptr(),
            status.as_mut_ptr(),
            AT_SYMLINK_NOFOLLOW,
        )
    } != 0
    {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: fstatat initialized the structure on success.
    Ok(unsafe { status.assume_init() })
}

fn mode_of(status: &Stat) -> u32 {
    u32::from(status.st_mode) & S_IFMT
}

fn same_object(left: &Stat, right: &Stat) -> bool {
    left.st_dev == right.st_dev && left.st_ino == right.st_ino
}

fn fsync_fd(fd: c_int) -> InstallResult<()> {
    // SAFETY: the descriptor is live for this call.
    if unsafe { fsync(fd) } != 0 {
        return Err(io_failure());
    }
    Ok(())
}

/// Open one directory below `parent` without following a final link.
fn open_dir_below(parent: c_int, name: &OsStr) -> InstallResult<c_int> {
    let name = c_component(name)?;
    // SAFETY: `parent` is a live directory descriptor and `name` outlives the
    // call; no mode argument is read by `open`.
    let fd = unsafe {
        openat(
            parent,
            name.as_ptr(),
            T8_O_RDONLY | T8_O_DIRECTORY | T8_O_NOFOLLOW | T8_O_CLOEXEC,
        )
    };
    if fd < 0 {
        return Err(unsafe_path());
    }
    Ok(fd)
}

/// A duplicated descriptor of a [`DirectoryHandle`](super::native::anchor).
fn dup_dir(handle: &super::native::anchor::DirectoryHandle) -> InstallResult<c_int> {
    // SAFETY: the descriptor is live for this call.
    let fd = unsafe { dup(handle.fd()) };
    if fd < 0 {
        return Err(io_failure());
    }
    Ok(fd)
}

/// Open the leaf of one validated path relative to its captured parent.
///
/// `flags` must not follow links; the caller verifies the opened object's
/// identity with [`ValidatedPath::verify_open`] before mutating it.
fn open_leaf(
    handle: &super::native::anchor::DirectoryHandle,
    name: &OsStr,
    flags: c_int,
) -> InstallResult<File> {
    let c_name = c_component(name)?;
    // SAFETY: the descriptor is live, `c_name` is NUL-terminated and the mode is
    // only read when the caller sets `O_CREAT`.
    let fd = unsafe {
        openat(
            handle.fd(),
            c_name.as_ptr(),
            flags | T8_O_NOFOLLOW | T8_O_CLOEXEC,
            0o600u32,
        )
    };
    if fd < 0 {
        return match io::Error::last_os_error().raw_os_error() {
            Some(ENOENT) => Err(unsafe_path()),
            _ => Err(io_failure()),
        };
    }
    // SAFETY: the descriptor is freshly opened and owned here.
    Ok(unsafe { File::from_raw_fd(fd) })
}

// ---------------------------------------------------------------------------
// Task 8: native filesystem and metadata-preserving carrier install.
// ---------------------------------------------------------------------------

/// `copyfile(3)` metadata classes. `COPYFILE_ALL` covers the ACL, stat,
/// extended-attribute and data classes, which is what the code seal covers
/// (including the resource fork, stored as `com.apple.ResourceFork`).
const COPYFILE_ACL: u32 = 1 << 0;
const COPYFILE_STAT: u32 = 1 << 1;
const COPYFILE_XATTR: u32 = 1 << 2;
const COPYFILE_DATA: u32 = 1 << 3;
const COPYFILE_METADATA: u32 = COPYFILE_ACL | COPYFILE_STAT | COPYFILE_XATTR;
const COPYFILE_ALL: u32 = COPYFILE_METADATA | COPYFILE_DATA;
/// `copyfile(3)` flag: report what the copy would do and copy nothing.
const COPYFILE_CHECK: u32 = 1 << 16;
/// `copyfile(3)` flag: fail if the destination already exists.
///
/// From `<copyfile.h>`: `COPYFILE_CHECK` is `1<<16` and `COPYFILE_EXCL` is
/// `1<<17`. The checked-in binding used `1<<16`, which silently skipped the copy
/// this adapter claims to make. The value is asserted below, and the copies
/// themselves are performed with `fcopyfile(3)` on descriptors that this adapter
/// created with an exclusive anchored `openat(2)` — so the destination can never
/// exist and no path is re-walked for the copy.
const COPYFILE_EXCL: u32 = 1 << 17;
const _: () = assert!(COPYFILE_EXCL == 1 << 17 && COPYFILE_EXCL != COPYFILE_CHECK);
const _: () = assert!(COPYFILE_ALL == COPYFILE_METADATA | COPYFILE_DATA);

/// Copy one regular file into `directory` under `name`, with every metadata class
/// the seal covers.
///
/// The destination is created relative to the anchored directory descriptor with
/// `O_CREAT | O_EXCL | O_NOFOLLOW`, so an existing entry is never adopted,
/// truncated or overwritten — the exclusive-create half of `COPYFILE_EXCL`, bound
/// to a handle instead of a path. `fcopyfile(3)` then copies the data and the
/// metadata classes from descriptor to descriptor.
fn copy_file_into(source: &Path, directory: c_int, name: &OsStr) -> InstallResult<()> {
    let from = fs::File::open(source).map_err(|_| io_failure())?;
    let metadata = from.metadata().map_err(|_| io_failure())?;
    if !metadata.is_file() {
        return Err(unsafe_path());
    }
    let destination = open_file_new(directory, name)?;
    // SAFETY: both descriptors are live for the call and no copy state or
    // callback is requested; `COPYFILE_ALL` is data plus the metadata the seal
    // covers (ACL, stat, extended attributes, including the resource fork).
    let status = unsafe {
        fcopyfile(
            from.as_raw_fd(),
            destination.as_raw_fd(),
            ptr::null_mut(),
            COPYFILE_ALL,
        )
    };
    if status != 0 {
        return Err(io_failure());
    }
    // The copied bytes are durable before the caller journals the entry.
    destination.sync_all().map_err(|_| io_failure())
}

/// Create one new regular file relative to an anchored directory descriptor.
fn open_file_new(directory: c_int, name: &OsStr) -> InstallResult<File> {
    let name = c_component(name)?;
    // SAFETY: the descriptor is live, `name` is NUL-terminated and the mode is
    // read because `O_CREAT` is set.
    let fd = unsafe {
        openat(
            directory,
            name.as_ptr(),
            T8_O_RDWR | T8_O_CREAT | T8_O_EXCL | T8_O_NOFOLLOW | T8_O_CLOEXEC,
            0o600u32,
        )
    };
    if fd < 0 {
        return Err(io_failure());
    }
    // SAFETY: the descriptor is freshly opened and owned here.
    Ok(unsafe { File::from_raw_fd(fd) })
}

/// Copy one directory's ACL, stat and extended attributes between descriptors.
fn copy_directory_metadata(from: c_int, to: c_int) -> InstallResult<()> {
    // SAFETY: both descriptors are live for the call; only metadata classes are
    // requested, so no directory data is read or written as a file.
    let status = unsafe { fcopyfile(from, to, ptr::null_mut(), COPYFILE_METADATA) };
    if status != 0 {
        return Err(io_failure());
    }
    Ok(())
}

/// Flush one opened regular file.
fn sync_opened_file(fd: c_int) -> InstallResult<()> {
    // SAFETY: the descriptor is live for this call.
    if unsafe { fsync(fd) } != 0 {
        return Err(io_failure());
    }
    Ok(())
}

/// Copy one carrier app bundle, entry by entry, anchored at the destination
/// directory descriptor.
///
/// * directories are created with `mkdirat(2)` from their parent descriptor, so
///   no component is re-walked by path;
/// * symbolic links are recreated from their **recorded target** (never
///   followed); the target was already proven to stay inside the artifact;
/// * every copied regular file is flushed, and every directory is flushed
///   bottom-up after its children and before its own metadata is applied;
/// * entry/byte bounds are re-checked per entry.
fn copy_carrier_tree(
    source: &Path,
    destination: &Path,
    relative: &str,
    destination_dir: c_int,
    entries: &mut usize,
    total: &mut u64,
) -> InstallResult<()> {
    let mut children: Vec<_> = fs::read_dir(source)
        .map_err(|_| io_failure())?
        .map(|entry| entry.map_err(|_| io_failure()))
        .collect::<Result<_, _>>()?;
    children.sort_by_key(|entry| entry.file_name());
    for child in children {
        *entries += 1;
        if *entries > super::MAX_CARRIER_ENTRIES {
            return Err(invalid_package());
        }
        let name = child.file_name();
        let path = child.path();
        let file_type = child.file_type().map_err(|_| io_failure())?;
        if file_type.is_symlink() {
            let target = fs::read_link(&path).map_err(|_| io_failure())?;
            if target.to_str().is_none() {
                return Err(unsafe_path());
            }
            let target_text = target.to_str().ok_or_else(unsafe_path)?;
            let child_relative = if relative.is_empty() {
                name.to_string_lossy().into_owned()
            } else {
                format!("{relative}/{}", name.to_string_lossy())
            };
            // Re-prove confinement at install time as well: enumeration and the
            // copy must agree about what stays inside the bundle.
            super::confine_link_target(&child_relative, target_text)?;
            *total = total
                .checked_add(target_text.len() as u64)
                .ok_or_else(invalid_package)?;
            if *total > super::MAX_CARRIER_BYTES as u64 {
                return Err(invalid_package());
            }
            let name = c_component(&name)?;
            let target = c_component(target.as_os_str())?;
            // SAFETY: `destination_dir` is a live directory descriptor and both
            // names are NUL-terminated.
            if unsafe { symlinkat(target.as_ptr(), destination_dir, name.as_ptr()) } != 0 {
                return Err(io_failure());
            }
            fsync_fd(destination_dir)?;
        } else if file_type.is_dir() {
            let name_c = c_component(&name)?;
            let child_relative = if relative.is_empty() {
                name.to_string_lossy().into_owned()
            } else {
                format!("{relative}/{}", name.to_string_lossy())
            };
            // SAFETY: `destination_dir` is live and `name_c` outlives the call.
            if unsafe { mkdirat(destination_dir, name_c.as_ptr(), 0o700) } != 0 {
                return Err(io_failure());
            }
            let child_dir = open_dir_below(destination_dir, &name)?;
            let child_destination = destination.join(&name);
            let source_dir = fs::File::open(&path).map_err(|_| io_failure())?;
            copy_carrier_tree(
                &path,
                &child_destination,
                &child_relative,
                child_dir,
                entries,
                total,
            )?;
            copy_directory_metadata(source_dir.as_raw_fd(), child_dir)?;
            // Bottom-up flush: the directory's own entries are durable before the
            // parent records it.
            fsync_fd(child_dir)?;
            // SAFETY: `child_dir` was opened here and is owned.
            unsafe {
                let _ = close(child_dir);
            }
        } else if file_type.is_file() {
            let length = fs::symlink_metadata(&path).map_err(|_| io_failure())?.len();
            *total = total.checked_add(length).ok_or_else(invalid_package)?;
            if *total > super::MAX_CARRIER_BYTES as u64 {
                return Err(invalid_package());
            }
            // The copy is anchored: the destination is created relative to this
            // directory's descriptor and flushed before the directory is.
            copy_file_into(&path, destination_dir, &name)?;
            let opened = open_leaf_fd(destination_dir, &name, T8_O_RDONLY)?;
            if mode_of(&stat_of(opened)?) != S_IFREG {
                // SAFETY: `opened` was opened here and is owned.
                unsafe {
                    let _ = close(opened);
                }
                return Err(unsafe_path());
            }
            sync_opened_file(opened)?;
            // SAFETY: `opened` was opened here and is owned.
            unsafe {
                let _ = close(opened);
            }
            fsync_fd(destination_dir)?;
        } else {
            return Err(unsafe_path());
        }
    }
    Ok(())
}

fn open_leaf_fd(directory: c_int, name: &OsStr, flags: c_int) -> InstallResult<c_int> {
    let name = c_component(name)?;
    // SAFETY: `directory` is live and `name` is NUL-terminated.
    let fd = unsafe {
        openat(
            directory,
            name.as_ptr(),
            flags | T8_O_NOFOLLOW | T8_O_CLOEXEC,
        )
    };
    if fd < 0 {
        return Err(io_failure());
    }
    Ok(fd)
}

// ---------------------------------------------------------------------------
// Cleanup helper support: an anchored traversal root.
// ---------------------------------------------------------------------------

/// The install root opened once as the anchor for every helper removal.
///
/// The root is opened by walking its **whole** ancestor chain with `openat(2)` +
/// `O_NOFOLLOW` from `/` (the path is not canonicalised first, so a link anywhere
/// above the root is refused rather than followed), and each relative entry is
/// walked from this descriptor the same way. Each deletion is bound to the
/// identity of the object that was opened and hashed: the verified entry is
/// renamed to a fresh one-shot name inside its own directory, re-opened without
/// following a link, compared with the hashed object and only then unlinked, so a
/// leaf swapped for a replacement is put back and preserved instead of deleted.
/// macOS `unlink(2)` is name-based (there is no `funlinkat`), so this
/// rename-verify-delete sequence is what keeps the unlink bound to the verified
/// object rather than the name.
///
/// Scope: a same-user attacker who can already write inside the private root is
/// out of scope (Task 2 policy). That attacker can still win a rename between the
/// re-read and the unlink; the helper verifies identity immediately before the
/// unlink, refuses to overwrite an unexpected entry (`RENAME_EXCL` in both
/// directions) and reports the mismatch instead of claiming an unbreakable
/// sequence.
pub struct CleanupRoot {
    fd: c_int,
}

impl CleanupRoot {
    /// Open `root` as an anchored directory.
    ///
    /// Only an installer-owned layout directory can be a cleanup root, and it is
    /// opened from the file system root component by component without following
    /// any link.
    pub fn open(root: &Path) -> io::Result<Self> {
        if !super::is_layout_root(root) {
            return Err(io::Error::from(io::ErrorKind::InvalidInput));
        }
        let handle = super::native::anchor::open_anchored(root)
            .map_err(|_| io::Error::from(io::ErrorKind::InvalidInput))?;
        Ok(Self {
            fd: handle.into_fd(),
        })
    }

    /// Open every intermediate component and return the leaf's parent descriptor
    /// plus the leaf name. `None` means the entry is absent.
    fn open_parent(&self, relative: &str) -> io::Result<Option<(c_int, CString)>> {
        let normalized = crate::inventory::normalize_relative_path(relative)
            .map_err(|_| io::Error::from(io::ErrorKind::InvalidInput))?;
        let parts: Vec<&str> = normalized.split('/').collect();
        // SAFETY: the descriptor is live for this call.
        let mut current = unsafe { dup(self.fd) };
        if current < 0 {
            return Err(io::Error::last_os_error());
        }
        for (index, part) in parts.iter().enumerate() {
            let last = index + 1 == parts.len();
            if last {
                let name = CString::new(part.as_bytes())
                    .map_err(|_| io::Error::from(io::ErrorKind::InvalidInput))?;
                return Ok(Some((current, name)));
            }
            let name = CString::new(part.as_bytes())
                .map_err(|_| io::Error::from(io::ErrorKind::InvalidInput))?;
            // SAFETY: `current` is live and `name` is NUL-terminated.
            let next = unsafe {
                openat(
                    current,
                    name.as_ptr(),
                    T8_O_RDONLY | T8_O_DIRECTORY | T8_O_NOFOLLOW | T8_O_CLOEXEC,
                )
            };
            // SAFETY: `current` was opened (or duplicated) here and is owned.
            unsafe {
                let _ = close(current);
            }
            if next < 0 {
                let error = io::Error::last_os_error();
                return match error.raw_os_error() {
                    // An absent component means the entry is already gone (an
                    // idempotent retry). A component that is a link or not a
                    // directory is *blocked*, never "absent": it must be
                    // preserved and reported.
                    Some(ENOENT) => Ok(None),
                    _ => Err(error),
                };
            }
            current = next;
        }
        // The root itself is not an entry.
        // SAFETY: `current` was duplicated here and is owned.
        unsafe {
            let _ = close(current);
        }
        Err(io::Error::from(io::ErrorKind::InvalidInput))
    }

    /// Remove one regular file with exactly this size and content.
    pub fn remove_file(
        &self,
        relative: &str,
        size: u64,
        sha256: Digest,
        deadline: Instant,
    ) -> RemoveOutcome {
        let (parent, name) = match self.open_parent(relative) {
            Ok(Some(entry)) => entry,
            Ok(None) => return RemoveOutcome::Absent,
            Err(_) => return RemoveOutcome::Preserved,
        };
        let outcome = self.remove_regular(parent, &name, size, sha256, deadline);
        // SAFETY: `parent` was opened here and is owned.
        unsafe {
            let _ = close(parent);
        }
        outcome
    }

    fn remove_regular(
        &self,
        parent: c_int,
        name: &CString,
        size: u64,
        sha256: Digest,
        deadline: Instant,
    ) -> RemoveOutcome {
        // SAFETY: `parent` is live and `name` is NUL-terminated.
        let fd = unsafe {
            openat(
                parent,
                name.as_ptr(),
                T8_O_RDONLY | T8_O_NOFOLLOW | T8_O_CLOEXEC,
            )
        };
        if fd < 0 {
            return match io::Error::last_os_error().raw_os_error() {
                Some(ENOENT) => RemoveOutcome::Absent,
                _ => RemoveOutcome::Preserved,
            };
        }
        let outcome = (|| {
            let Ok(status) = stat_of(fd) else {
                return RemoveOutcome::Preserved;
            };
            if mode_of(&status) != S_IFREG || status.st_size < 0 || status.st_size as u64 != size {
                return RemoveOutcome::Preserved;
            }
            // Hash the descriptor that was opened, not the name.
            let mut hasher = Sha256::new();
            let mut buffer = [0u8; 64 * 1024];
            use std::io::Read as _;
            let mut reader = std::mem::ManuallyDrop::new(unsafe { File::from_raw_fd(fd) });
            loop {
                if Instant::now() >= deadline {
                    return RemoveOutcome::Deferred;
                }
                match reader.read(&mut buffer) {
                    Ok(0) => break,
                    Ok(read) => hasher.update(&buffer[..read]),
                    Err(_) => return RemoveOutcome::Preserved,
                }
            }
            if Digest::from_bytes(hasher.finalize().into()) != sha256 {
                return RemoveOutcome::Preserved;
            }
            // Bind the unlink to the object that was hashed, not to its name.
            bind_and_unlink(parent, name, &status, false)
        })();
        // SAFETY: `fd` was opened here and is owned.
        unsafe {
            let _ = close(fd);
        }
        outcome
    }

    /// Remove one symbolic link whose target bytes have this size and digest.
    ///
    /// The link is addressed with `AT_SYMLINK_NOFOLLOW` throughout and its target
    /// is read with `readlinkat(2)`: it is never resolved, so the target is never
    /// deleted or even opened.
    pub fn remove_link(
        &self,
        relative: &str,
        size: u64,
        sha256: Digest,
        _deadline: Instant,
    ) -> RemoveOutcome {
        let (parent, name) = match self.open_parent(relative) {
            Ok(Some(entry)) => entry,
            Ok(None) => return RemoveOutcome::Absent,
            Err(_) => return RemoveOutcome::Preserved,
        };
        let outcome = (|| {
            let opened = match stat_at(parent, &name) {
                Ok(opened) => opened,
                Err(error) if error.raw_os_error() == Some(ENOENT) => {
                    return RemoveOutcome::Absent;
                }
                Err(_) => return RemoveOutcome::Preserved,
            };
            if mode_of(&opened) != S_IFLNK || opened.st_size < 0 || opened.st_size as u64 != size {
                return RemoveOutcome::Preserved;
            }
            let mut buffer = vec![0u8; size as usize];
            // SAFETY: `parent` is live, `name` is NUL-terminated and `buffer` is a
            // live output buffer of exactly the target's recorded length.
            let read = unsafe {
                readlinkat(
                    parent,
                    name.as_ptr(),
                    buffer.as_mut_ptr().cast(),
                    buffer.len(),
                )
            };
            if read < 0 || read as usize != buffer.len() {
                return RemoveOutcome::Preserved;
            }
            if Digest::of(&buffer) != sha256 {
                return RemoveOutcome::Preserved;
            }
            // The link object we read is the one that is unlinked, never the
            // target and never a replacement that appeared at the same name.
            bind_and_unlink(parent, &name, &opened, false)
        })();
        // SAFETY: `parent` was opened here and is owned.
        unsafe {
            let _ = close(parent);
        }
        outcome
    }

    /// Remove one *empty* directory.
    pub fn remove_directory(&self, relative: &str) -> RemoveOutcome {
        let (parent, name) = match self.open_parent(relative) {
            Ok(Some(entry)) => entry,
            Ok(None) => return RemoveOutcome::Absent,
            Err(_) => return RemoveOutcome::Preserved,
        };
        let outcome = (|| {
            let opened = match stat_at(parent, &name) {
                Ok(opened) => opened,
                Err(error) if error.raw_os_error() == Some(ENOENT) => {
                    return RemoveOutcome::Absent;
                }
                Err(_) => return RemoveOutcome::Preserved,
            };
            if mode_of(&opened) != S_IFDIR {
                return RemoveOutcome::Preserved;
            }
            // The name must be the directory that was opened, not a link that
            // replaced it.
            // SAFETY: `parent` is live and `name` is NUL-terminated.
            let fd = unsafe {
                openat(
                    parent,
                    name.as_ptr(),
                    T8_O_RDONLY | T8_O_DIRECTORY | T8_O_NOFOLLOW | T8_O_CLOEXEC,
                )
            };
            if fd < 0 {
                return RemoveOutcome::Preserved;
            }
            let bound = stat_of(fd).map(|status| same_object(&status, &opened));
            // SAFETY: `fd` was opened here and is owned.
            unsafe {
                let _ = close(fd);
            }
            if !matches!(bound, Ok(true)) {
                return RemoveOutcome::Preserved;
            }
            // Directories are only ever removed when empty: a non-empty one is
            // renamed back and preserved, never recursively deleted.
            bind_and_unlink(parent, &name, &opened, true)
        })();
        // SAFETY: `parent` was opened here and is owned.
        unsafe {
            let _ = close(parent);
        }
        outcome
    }
}

/// A fresh, one-shot name inside the same directory as `original`.
fn fresh_entry_name(original: &CString) -> InstallResult<CString> {
    let original = original.to_str().map_err(|_| unsafe_path())?;
    let stem: String = original.chars().take(48).collect();
    CString::new(format!(".{stem}.{}.cleanup", super::native::unique_token()))
        .map_err(|_| unsafe_path())
}

/// Rename one directory entry without ever replacing the destination.
///
/// `renameat(2)` replaces an existing destination, so a racing entry (in either
/// direction of [`bind_and_unlink`]) would be silently destroyed. The Darwin
/// `renameatx_np(RENAME_EXCL)` variant fails with `EEXIST` instead, which the
/// caller handles by leaving the source where it is and reporting it preserved.
fn rename_exclusive(parent: c_int, from: &CString, to: &CString) -> io::Result<()> {
    // SAFETY: `parent` is live and both names are NUL-terminated.
    let result = unsafe { renameatx_np(parent, from.as_ptr(), parent, to.as_ptr(), RENAME_EXCL) };
    if result == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

/// Unlink exactly the object that was verified, by rebinding the name first.
///
/// `unlink(2)`/`unlinkat(2)` delete a *directory entry*, so checking the name's
/// identity and then unlinking it leaves a window in which a replacement is
/// deleted. Instead the verified entry is renamed to a fresh one-shot name in the
/// same (already anchored) directory, the renamed entry is re-read without
/// following a link, and only an identity match is unlinked — a mismatch renames
/// the entry back, so the replacement is preserved rather than destroyed.
///
/// Both renames use `RENAME_EXCL`, so neither the move to the private name nor
/// the restore can overwrite an entry that appeared meanwhile:
///
/// * the move fails with `EEXIST` if the one-shot name is occupied (the entry is
///   left untouched and reported `Preserved`);
/// * the restore fails with `EEXIST` if something took the original name while
///   the entry was private — the verified object then stays under its private
///   `.name.token.cleanup` name and is reported `Preserved`. That name is not in
///   any cleanup request, so a later run does not sweep it: it is a reported
///   leftover, never foreign content that was destroyed.
///
/// The remaining same-user window between the identity re-read and the unlink is
/// out of scope (see the module docs and the Task 2 policy); closing it would
/// need `funlinkat`, which macOS does not provide.
fn bind_and_unlink(
    parent: c_int,
    name: &CString,
    expected: &Stat,
    remove_dir: bool,
) -> RemoveOutcome {
    let fresh = match fresh_entry_name(name) {
        Ok(fresh) => fresh,
        Err(_) => return RemoveOutcome::Preserved,
    };
    if let Err(error) = rename_exclusive(parent, name, &fresh) {
        return match error.raw_os_error() {
            Some(ENOENT) => RemoveOutcome::Absent,
            // The one-shot private name was already taken: never overwrite it.
            Some(EEXIST) => RemoveOutcome::Preserved,
            _ => RemoveOutcome::Preserved,
        };
    }
    let observed = stat_at(parent, &fresh).ok();
    if observed
        .as_ref()
        .is_none_or(|status| !same_object(expected, status))
    {
        // Whatever was moved is not the object that was verified: put it back
        // exactly where it was (never over an entry that appeared meanwhile)
        // and report it preserved.
        let _ = rename_exclusive(parent, &fresh, name);
        return RemoveOutcome::Preserved;
    }
    let flags = if remove_dir { AT_REMOVEDIR } else { 0 };
    // SAFETY: `parent` is live and `fresh` is NUL-terminated.
    if unsafe { unlinkat(parent, fresh.as_ptr(), flags) } == 0 {
        return RemoveOutcome::Removed;
    }
    let error = io::Error::last_os_error();
    // The unlink failed (a non-empty directory, a permission problem): restore
    // the entry under its real name when that name is still free. If it is not,
    // the verified object stays under the private one-shot name and is reported
    // preserved rather than replacing the entry that took the name.
    let _ = rename_exclusive(parent, &fresh, name);
    match error.raw_os_error() {
        Some(ENOENT) => RemoveOutcome::Absent,
        _ => RemoveOutcome::Preserved,
    }
}

impl Drop for CleanupRoot {
    fn drop(&mut self) {
        // SAFETY: the descriptor is owned by this value.
        unsafe {
            let _ = close(self.fd);
        }
    }
}

impl NativeFs for MacosPlatform {
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock> {
        native_ensure_layout(self, paths)?;
        // The lock file is created **relative to the anchored root**, so its
        // ancestors are opened and verified as a chain and cannot be redirected
        // between validation and the open.
        let root = super::native::anchor::open_anchored(paths.root())?;
        let name = paths
            .lock_path()
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(unsafe_path)?;
        let c_name = c_component(OsStr::new(name))?;
        // SAFETY: the root descriptor is live, `c_name` is NUL-terminated and the
        // mode is only read for the creating open.
        let fd = unsafe {
            openat(
                root.fd(),
                c_name.as_ptr(),
                T8_O_RDWR | T8_O_CREAT | T8_O_NOFOLLOW | T8_O_CLOEXEC,
                0o600,
            )
        };
        if fd < 0 {
            return match io::Error::last_os_error().raw_os_error() {
                // ELOOP: the fixed lock name is a symbolic link.
                Some(62) => Err(unsafe_path()),
                _ => Err(io_failure()),
            };
        }
        // SAFETY: `fd` was just opened and is owned here.
        let file = unsafe { File::from_raw_fd(fd) };
        if !file.metadata().map_err(|_| io_failure())?.is_file() {
            return Err(unsafe_path());
        }
        match file.try_lock() {
            Ok(()) => Ok(InstallLock {
                path: paths.lock_path().to_path_buf(),
                file,
            }),
            Err(super::TryLockError::WouldBlock) => Err(InstallFailure::new(FailureCode::Busy)),
            Err(_) => Err(io_failure()),
        }
    }

    fn validate_private_directory(&self, path: &Path) -> InstallResult<()> {
        use std::os::unix::fs::MetadataExt;

        // Open `<parent>/<leaf>` through an anchored parent handle: a link at the
        // leaf fails the `O_DIRECTORY | O_NOFOLLOW` open, and a swapped ancestor
        // fails the no-follow walk instead of redirecting the check.
        let parent = path.parent().ok_or_else(unsafe_path)?;
        let name = path.file_name().ok_or_else(unsafe_path)?;
        let handle = super::native::anchor::open_anchored(parent)?;
        let fd = open_leaf_fd(handle.fd(), name, T8_O_RDONLY | T8_O_DIRECTORY)
            .map_err(|_| unsafe_path())?;
        // SAFETY: `fd` was opened here and is owned.
        let metadata = unsafe { File::from_raw_fd(fd) }
            .metadata()
            .map_err(|_| io_failure())?;
        if !metadata.is_dir() {
            return Err(unsafe_path());
        }
        if metadata.uid() != super::effective_user_id() {
            return Err(unsafe_path());
        }
        // Group/other access to the install root would let another principal
        // plant or replace entries the installer later trusts.
        if metadata.mode() & 0o077 != 0 {
            return Err(unsafe_path());
        }
        Ok(())
    }

    fn write_durable(
        &self,
        path: &ValidatedPath,
        bytes: &[u8],
    ) -> InstallResult<super::FileIdentity> {
        let handle = path.dir_handle()?;
        let create = match path.kind() {
            PathKind::NewFile => true,
            // An existing file is opened **without** `O_TRUNC`: truncation is a
            // destructive mutation and may only happen through the handle whose
            // identity has been verified against the captured one.
            PathKind::File => false,
            _ => return Err(unsafe_path()),
        };
        let name = c_component(path.leaf_name()?)?;
        let flags = if create {
            T8_O_RDWR | T8_O_CREAT | T8_O_EXCL
        } else {
            T8_O_RDWR
        };
        // SAFETY: the parent descriptor is live, `name` is NUL-terminated and the
        // mode is only read for the creating open.
        let fd = unsafe {
            openat(
                handle.fd(),
                name.as_ptr(),
                flags | T8_O_NOFOLLOW | T8_O_CLOEXEC,
                0o600,
            )
        };
        if fd < 0 {
            return match io::Error::last_os_error().raw_os_error() {
                Some(17) => Err(unsafe_path()),
                _ => Err(io_failure()),
            };
        }
        let mut file = unsafe { File::from_raw_fd(fd) };
        let outcome = (|| {
            let metadata = file.metadata().map_err(|_| io_failure())?;
            let identity = super::identity_of_file(&file)?;
            if create {
                if !metadata.is_file() {
                    return Err(unsafe_path());
                }
            } else {
                // Identity before mutation: only the object validation captured
                // may be truncated and written.
                path.verify_open(&metadata, &identity)?;
                file.set_len(0).map_err(|_| io_failure())?;
            }
            use std::io::Write as _;
            file.write_all(bytes).map_err(|_| io_failure())?;
            file.sync_all().map_err(|_| io_failure())?;
            read_back(&mut file, bytes).map_err(|_| io_failure())?;
            Ok(identity)
        })();
        if outcome.is_err() && create {
            // Remove only the object this call created, bound to its descriptor:
            // a file somebody else planted at the same name is not ours to delete.
            //
            // The name here is the fresh one-shot staging name this call created
            // exclusively, so the `stat`/`unlinkat` pair addresses a private name,
            // not a caller-supplied one. The residual window between the identity
            // comparison and the name-based `unlinkat` is a same-user race inside
            // the private `0700` root and is out of scope (Task 2 policy: the
            // installer does not defend against the account's own administrator).
            if let Ok(created) = stat_of(fd)
                && let Ok(named) = stat_at(handle.fd(), &name)
                && same_object(&created, &named)
            {
                // SAFETY: the parent descriptor is live and `name` is
                // NUL-terminated.
                unsafe {
                    let _ = unlinkat(handle.fd(), name.as_ptr(), 0);
                }
            }
        }
        outcome
    }

    fn replace_file(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        let staged_parent = dup_dir(&staged.dir_handle()?)?;
        let live_parent = dup_dir(&live.dir_handle()?)?;
        let outcome = (|| {
            // Verify the staged object is the one validation captured before the
            // entry is moved.
            let handle = staged.dir_handle()?;
            let opened = open_leaf(&handle, staged.leaf_name()?, T8_O_RDONLY)?;
            let metadata = opened.metadata().map_err(|_| io_failure())?;
            let identity = super::identity_of_file(&opened)?;
            drop(opened);
            staged.verify_open(&metadata, &identity)?;
            let from = c_component(staged.leaf_name()?)?;
            let to = c_component(live.leaf_name()?)?;
            // SAFETY: both descriptors are live and both names are NUL-terminated.
            if unsafe { renameat(staged_parent, from.as_ptr(), live_parent, to.as_ptr()) } != 0 {
                return Err(io_failure());
            }
            Ok(())
        })();
        // SAFETY: both descriptors were duplicated here and are owned.
        unsafe {
            let _ = close(staged_parent);
            let _ = close(live_parent);
        }
        outcome
    }

    fn read_file(&self, path: &Path, limit: u64) -> InstallResult<Vec<u8>> {
        super::read_no_follow_capped(path, limit).map_err(|_| io_failure())
    }

    fn hash_file(&self, path: &Path) -> InstallResult<Digest> {
        super::hash_file(path)
    }

    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()> {
        fsync_fd(path.dir_handle()?.fd())
    }

    fn create_directory(&self, path: &Path) -> InstallResult<()> {
        // Create the fixed component **relative to an anchored parent handle**:
        // the parent chain is opened without following any link, so a swapped
        // ancestor cannot redirect where the directory is created.
        let parent = path.parent().ok_or_else(unsafe_path)?;
        let name = path.file_name().ok_or_else(unsafe_path)?;
        let handle = super::native::anchor::open_anchored(parent)?;
        let c_name = c_component(name)?;
        // SAFETY: the parent descriptor is live and `c_name` is NUL-terminated.
        if unsafe { mkdirat(handle.fd(), c_name.as_ptr(), 0o700) } != 0 {
            return match io::Error::last_os_error().raw_os_error() {
                Some(17) => Err(unsafe_path()),
                _ => Err(io_failure()),
            };
        }
        // The mode is applied to the descriptor that was just opened, never to the
        // name (a name-addressed `chmod` could follow a substituted link).
        let created = open_leaf_fd(handle.fd(), name, T8_O_RDONLY | T8_O_DIRECTORY)?;
        // SAFETY: `created` was opened here and is owned.
        let outcome = unsafe {
            let result = if fchmod(created, 0o700) == 0 {
                fsync_fd(created)
            } else {
                Err(io_failure())
            };
            let _ = close(created);
            result
        };
        outcome?;
        fsync_fd(handle.fd())
    }

    fn create_directory_at(&self, path: &ValidatedPath) -> InstallResult<()> {
        if path.kind() != PathKind::NewDirectory {
            return Err(invalid_input());
        }
        path.revalidate()?;
        let handle = path.dir_handle()?;
        let name = c_component(path.leaf_name()?)?;
        // SAFETY: the parent descriptor is live and `name` is NUL-terminated.
        if unsafe { mkdirat(handle.fd(), name.as_ptr(), 0o700) } != 0 {
            return match io::Error::last_os_error().raw_os_error() {
                Some(17) => Err(unsafe_path()),
                _ => Err(io_failure()),
            };
        }
        // SAFETY: `openat` resolves the name we just created beneath the same
        // descriptor, with no-follow semantics.
        let created = open_leaf_fd(handle.fd(), path.leaf_name()?, T8_O_RDONLY | T8_O_DIRECTORY)?;
        // The permission change is applied to the **descriptor** that was just
        // opened, never to the name: `fchmodat` with flags 0 would resolve the
        // name again and could follow a substituted symlink.
        // SAFETY: `created` was opened here and is owned.
        let chmod = unsafe {
            let result = fchmod(created, 0o700);
            close(created);
            result
        };
        if chmod != 0 {
            return Err(io_failure());
        }
        fsync_fd(handle.fd())
    }

    fn remove_entry(&self, path: &ValidatedPath) -> InstallResult<()> {
        let handle = path.dir_handle()?;
        let name = c_component(path.leaf_name()?)?;
        let parent = dup_dir(&handle)?;
        let outcome = (|| {
            match path.kind() {
                PathKind::File => {
                    let opened = open_leaf(&handle, path.leaf_name()?, T8_O_RDONLY)?;
                    let metadata = opened.metadata().map_err(|_| io_failure())?;
                    let identity = super::identity_of_file(&opened)?;
                    drop(opened);
                    path.verify_open(&metadata, &identity)?;
                }
                PathKind::Symlink => {
                    let opened = stat_at(parent, &name).map_err(|_| unsafe_path())?;
                    if mode_of(&opened) != S_IFLNK {
                        return Err(unsafe_path());
                    }
                    let expected = path.captured_identity().ok_or_else(unsafe_path)?;
                    if !identity_matches(&opened, expected) {
                        return Err(unsafe_path());
                    }
                }
                _ => return Err(unsafe_path()),
            }
            // Identity binding for a regular file: the name must still resolve to
            // the object that was opened and verified.
            //
            // The window between this `fstatat` and the name-based `unlinkat` is
            // the same-user in-root rename race: out of scope by policy (the
            // `0700` root already excludes other principals, and macOS has no
            // `funlinkat`). Identity is checked immediately before the unlink and
            // a mismatch aborts; the installer does not claim atomicity it cannot
            // obtain.
            if path.kind() == PathKind::File {
                let observed = stat_at(parent, &name).map_err(|_| unsafe_path())?;
                if let Some(expected) = path.captured_identity()
                    && !identity_matches(&observed, expected)
                {
                    return Err(unsafe_path());
                }
            }
            // SAFETY: `parent` is live and `name` is NUL-terminated.
            if unsafe { unlinkat(parent, name.as_ptr(), 0) } != 0 {
                return Err(io_failure());
            }
            Ok(())
        })();
        // SAFETY: `parent` was duplicated here and is owned.
        unsafe {
            let _ = close(parent);
        }
        outcome
    }

    fn remove_directory(&self, path: &ValidatedPath) -> InstallResult<()> {
        let handle = path.dir_handle()?;
        let name = c_component(path.leaf_name()?)?;
        let parent = dup_dir(&handle)?;
        let outcome = (|| {
            let observed = stat_at(parent, &name).map_err(|_| unsafe_path())?;
            if mode_of(&observed) != S_IFDIR {
                return Err(unsafe_path());
            }
            // The name must still be the directory validation captured.
            if let Some(expected) = path.captured_identity()
                && !identity_matches(&observed, expected)
            {
                return Err(unsafe_path());
            }
            // SAFETY: `parent` is live and `name` is NUL-terminated.
            if unsafe { unlinkat(parent, name.as_ptr(), AT_REMOVEDIR) } != 0 {
                return match io::Error::last_os_error().raw_os_error() {
                    Some(ENOENT) => Ok(()),
                    Some(ENOTEMPTY) => Err(InstallFailure::new(FailureCode::CleanupPending)),
                    _ => Err(io_failure()),
                };
            }
            Ok(())
        })();
        // SAFETY: `parent` was duplicated here and is owned.
        unsafe {
            let _ = close(parent);
        }
        outcome
    }

    fn install_carrier(
        &self,
        source: &PackageSource,
        destination: &ValidatedPath,
    ) -> InstallResult<()> {
        let handle = destination.dir_handle()?;
        match (source.kind(), destination.kind()) {
            (PackageKind::File, PathKind::NewFile) => {
                copy_file_into(source.path(), handle.fd(), destination.leaf_name()?)?;
                // Flush the copied image through the anchored parent, then the
                // directory that now holds it.
                let opened = open_leaf(&handle, destination.leaf_name()?, T8_O_RDONLY)?;
                let metadata = opened.metadata().map_err(|_| io_failure())?;
                if !metadata.is_file() {
                    return Err(unsafe_path());
                }
                opened.sync_all().map_err(|_| io_failure())?;
                fsync_fd(handle.fd())
            }
            (PackageKind::Directory, PathKind::NewDirectory) => {
                let name = c_component(destination.leaf_name()?)?;
                // SAFETY: the parent descriptor is live and `name` is
                // NUL-terminated.
                if unsafe { mkdirat(handle.fd(), name.as_ptr(), 0o700) } != 0 {
                    return Err(io_failure());
                }
                let root = open_leaf_fd(
                    handle.fd(),
                    destination.leaf_name()?,
                    T8_O_RDONLY | T8_O_DIRECTORY,
                )?;
                let outcome: InstallResult<()> = (|| {
                    let mut entries = 0usize;
                    let mut total = 0u64;
                    copy_carrier_tree(
                        source.path(),
                        destination.rendered(),
                        "",
                        root,
                        &mut entries,
                        &mut total,
                    )?;
                    // The bundle root's ACL/stat/xattrs are part of the seal too.
                    let source_root = fs::File::open(source.path()).map_err(|_| io_failure())?;
                    use std::os::unix::io::AsRawFd;
                    copy_directory_metadata(source_root.as_raw_fd(), root)?;
                    fsync_fd(root)?;
                    Ok(())
                })();
                // SAFETY: `root` was opened here and is owned.
                unsafe {
                    let _ = close(root);
                }
                outcome?;
                fsync_fd(handle.fd())
            }
            _ => Err(invalid_package()),
        }
    }
}

/// Compare one `struct stat` identity with the captured [`FileIdentity`].
fn identity_matches(status: &Stat, expected: &super::FileIdentity) -> bool {
    let super::FileIdentity { device, inode } = expected;
    i64::from(status.st_dev) as u64 == *device && status.st_ino == *inode
}
