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
use std::io;
use std::mem::size_of;
use std::os::unix::ffi::OsStrExt;
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
    CarrierEntry, HostState, ImageClass, InstallLock, OwnedEntry, OwnedImages,
    PACKAGE_MACOS_CARRIER, PackageKind, PackageSource, Platform, ProcessIdentity, ProcessImage,
    ProcessObservation, ProcessScope, ProcessTable, Registration,
    TRUST_ASSESSMENT_MACOS_GATEKEEPER, TrustProof, ValidatedPath, inspect_host_processes,
    path_is_within,
};
use crate::model::{Digest, FailureCode, InstallFailure, InstallResult, ReleaseDescriptor, Target};
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
        native_write_durable(self, path, bytes)
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
        NativeFs::create_directory(self, path.rendered())
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
/// Refuse to overwrite an existing carrier entry.
const COPYFILE_EXCL: u32 = 1 << 16;

unsafe extern "C" {
    fn copyfile(from: *const c_char, to: *const c_char, state: *mut c_void, flags: u32) -> c_int;
    fn fcopyfile(from: c_int, to: c_int, state: *mut c_void, flags: u32) -> c_int;
}

/// One absolute path as a NUL-terminated C string.
fn c_path(path: &Path) -> InstallResult<CString> {
    CString::new(path.as_os_str().as_bytes()).map_err(|_| unsafe_path())
}

/// Copy one regular file with every metadata class the seal covers.
fn copy_file_exact(from: &Path, to: &Path) -> InstallResult<()> {
    let from = c_path(from)?;
    let to = c_path(to)?;
    // SAFETY: both paths are NUL-terminated live buffers; no copy state or
    // callback is requested.
    let status = unsafe {
        copyfile(
            from.as_ptr(),
            to.as_ptr(),
            ptr::null_mut(),
            COPYFILE_ALL | COPYFILE_EXCL,
        )
    };
    if status != 0 {
        return Err(io_failure());
    }
    Ok(())
}

/// Copy one directory's ACL, stat and extended attributes onto an existing
/// destination directory.
fn copy_directory_metadata(from: &Path, to: &Path) -> InstallResult<()> {
    use std::os::unix::io::AsRawFd;

    let from_file = fs::File::open(from).map_err(|_| io_failure())?;
    let to_file = fs::File::open(to).map_err(|_| io_failure())?;
    // SAFETY: both descriptors are live for the call; only metadata classes are
    // requested, so no directory data is read or written as a file.
    let status = unsafe {
        fcopyfile(
            from_file.as_raw_fd(),
            to_file.as_raw_fd(),
            ptr::null_mut(),
            COPYFILE_METADATA,
        )
    };
    if status != 0 {
        return Err(io_failure());
    }
    Ok(())
}

/// Copy one carrier app bundle entry by entry.
///
/// Bounds are checked before any copy and again per entry, so a package that
/// grew after enumeration still cannot install an implausible tree. Links are
/// recreated from their target (never followed) and special files are refused.
fn copy_carrier_tree(
    from: &Path,
    to: &Path,
    entries: &mut usize,
    total: &mut u64,
) -> InstallResult<()> {
    let mut children: Vec<_> = fs::read_dir(from)
        .map_err(|_| io_failure())?
        .map(|entry| entry.map_err(|_| io_failure()))
        .collect::<Result<_, _>>()?;
    children.sort_by_key(|entry| entry.file_name());
    for child in children {
        *entries += 1;
        if *entries > super::MAX_CARRIER_ENTRIES {
            return Err(invalid_package());
        }
        let file_type = child.file_type().map_err(|_| io_failure())?;
        let source = child.path();
        let destination = to.join(child.file_name());
        if !path_is_within(to, &destination) {
            return Err(unsafe_path());
        }
        if file_type.is_symlink() {
            let target = fs::read_link(&source).map_err(|_| io_failure())?;
            std::os::unix::fs::symlink(target, &destination).map_err(|_| io_failure())?;
        } else if file_type.is_dir() {
            fs::create_dir(&destination).map_err(|_| io_failure())?;
            set_owner_only(&destination)?;
            copy_carrier_tree(&source, &destination, entries, total)?;
            copy_directory_metadata(&source, &destination)?;
        } else if file_type.is_file() {
            let length = fs::symlink_metadata(&source)
                .map_err(|_| io_failure())?
                .len();
            *total = total.checked_add(length).ok_or_else(invalid_package)?;
            if *total > super::MAX_CARRIER_BYTES as u64 {
                return Err(invalid_package());
            }
            copy_file_exact(&source, &destination)?;
        } else {
            return Err(unsafe_path());
        }
    }
    Ok(())
}

/// Apply owner-only permissions to a freshly created directory or file.
fn set_owner_only(path: &Path) -> InstallResult<()> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(|_| io_failure())
}

impl NativeFs for MacosPlatform {
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock> {
        native_ensure_layout(self, paths)?;
        let mut options = fs::OpenOptions::new();
        options.read(true).write(true).create(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;

            options.mode(0o600).custom_flags(super::NO_FOLLOW);
        }
        let file = options.open(paths.lock_path()).map_err(|error| {
            if error.raw_os_error() == Some(62) {
                // ELOOP: the fixed lock name is a symbolic link.
                unsafe_path()
            } else {
                io_failure()
            }
        })?;
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

    fn write_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        use std::os::unix::fs::OpenOptionsExt;

        let mut options = fs::OpenOptions::new();
        options.read(true).write(true);
        match path.kind() {
            super::PathKind::NewFile => {
                options.create_new(true);
            }
            super::PathKind::File => {
                options.truncate(true);
            }
            _ => return Err(unsafe_path()),
        }
        // O_NOFOLLOW makes the final component an error instead of following a
        // link planted after revalidation.
        options.mode(0o600).custom_flags(super::NO_FOLLOW);
        let mut file = options.open(path.rendered()).map_err(|error| {
            if error.kind() == io::ErrorKind::AlreadyExists || error.raw_os_error() == Some(62) {
                unsafe_path()
            } else {
                io_failure()
            }
        })?;
        if !file.metadata().map_err(|_| io_failure())?.is_file() {
            return Err(unsafe_path());
        }
        use std::io::Write as _;
        file.write_all(bytes).map_err(|_| io_failure())?;
        file.sync_all().map_err(|_| io_failure())?;
        read_back(&mut file, bytes).map_err(|_| io_failure())
    }

    fn replace_file(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        // Both siblings are below the same validated anchor, so the rename is a
        // same-volume atomic directory-entry replacement.
        fs::rename(staged.rendered(), live.rendered()).map_err(|_| io_failure())
    }

    fn read_file(&self, path: &Path) -> InstallResult<Vec<u8>> {
        super::read_no_follow(path).map_err(|_| io_failure())
    }

    fn hash_file(&self, path: &Path) -> InstallResult<Digest> {
        super::hash_file(path)
    }

    fn flush_directory(&self, path: &Path) -> InstallResult<()> {
        super::flush_directory(path)
    }

    fn create_directory(&self, path: &Path) -> InstallResult<()> {
        use std::os::unix::fs::DirBuilderExt;

        let mut builder = fs::DirBuilder::new();
        builder.mode(0o700);
        match builder.create(path) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
                return Err(unsafe_path());
            }
            Err(_) => return Err(io_failure()),
        }
        set_owner_only(path)?;
        let parent = path.parent().ok_or_else(unsafe_path)?;
        super::flush_directory(parent)
    }

    fn remove_file(&self, path: &Path) -> InstallResult<()> {
        fs::remove_file(path).map_err(|_| io_failure())
    }

    fn remove_directory(&self, path: &Path) -> InstallResult<()> {
        match fs::remove_dir(path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) if error.kind() == io::ErrorKind::DirectoryNotEmpty => {
                Err(InstallFailure::new(FailureCode::CleanupPending))
            }
            Err(_) => Err(io_failure()),
        }
    }

    fn install_carrier(
        &self,
        source: &PackageSource,
        destination: &ValidatedPath,
    ) -> InstallResult<()> {
        match (source.kind(), destination.kind()) {
            (PackageKind::File, super::PathKind::NewFile) => {
                copy_file_exact(source.path(), destination.rendered())?;
                // Flush the freshly copied file and its directory before the
                // caller journals the created entry.
                let file = fs::File::open(destination.rendered()).map_err(|_| io_failure())?;
                file.sync_all().map_err(|_| io_failure())?;
                let parent = destination.rendered().parent().ok_or_else(unsafe_path)?;
                super::flush_directory(parent)
            }
            (PackageKind::Directory, super::PathKind::NewDirectory) => {
                fs::create_dir(destination.rendered()).map_err(|_| io_failure())?;
                set_owner_only(destination.rendered())?;
                let mut entries = 0usize;
                let mut total = 0u64;
                copy_carrier_tree(
                    source.path(),
                    destination.rendered(),
                    &mut entries,
                    &mut total,
                )?;
                // The bundle root's ACL/stat/xattrs are part of the seal too.
                copy_directory_metadata(source.path(), destination.rendered())?;
                super::flush_directory(destination.rendered())?;
                let parent = destination.rendered().parent().ok_or_else(unsafe_path)?;
                super::flush_directory(parent)
            }
            _ => Err(invalid_package()),
        }
    }
}
