//! Compile-time-only fixture trust for DEBUG installers (OTP-25 PR path).
//!
//! A PR debug installer is built from the isolated fixture channel. Its payload is not signed
//! with a release identity, so the unchanged trust decision layer would refuse it. This module is
//! the narrow, deliberately non-authenticating adapter that makes exactly one package acceptable:
//! the embedded fixture descriptor, read by the platform adapter from the package carrier without
//! OS signature validation (there is no signature to validate).
//!
//! It is not an authentication boundary, and it cannot be reached by a release binary:
//!
//! * the module is compiled only with `--features debug-fixture-trust`, and `build.rs` refuses
//!   that feature outside a debug profile or for the production channel;
//! * no CLI argument, environment variable, descriptor field or runtime flag selects it;
//! * even when compiled in, it accepts only [`Channel::Fixture`] with the fixture host/bundle
//!   identity and a policy that lists [`FIXTURE_PUBLISHER`];
//! * the decision layer is unchanged: the carrier document the adapter returns must equal the
//!   compiled-in expected descriptor, and the signer it reports must be listed in that
//!   authenticated policy.
//!
//! The Windows and macOS adapters read the carrier bytes from the package and call
//! [`trust_proof`]; the fixture-only host path calls [`host_assessment`].

use crate::model::{
    Channel, FailureCode, InstallFailure, InstallResult, ReleaseDescriptor, Target,
};
use crate::platform::{
    HostAssessment, TRUST_ASSESSMENT_MACOS_GATEKEEPER, TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
    TrustProof,
};
use std::path::{Path, PathBuf};

/// The publisher identity the tracked fixture config lists.
///
/// The adapter reports this exact string, and requires the expected descriptor's policy to list
/// it, so the unchanged `authenticate_package`/`assess_host` publisher check can only pass for the
/// fixture policy. It is not a release identity and never names a real signer.
pub const FIXTURE_PUBLISHER: &str = "Veles Fixture (not a release signer)";

fn denied() -> InstallFailure {
    InstallFailure::new(FailureCode::TrustDenied)
}

/// Why this descriptor is not a fixture descriptor the debug adapter may trust.
///
/// The caller-authorized `expected` descriptor is already validated by the decision layer; this
/// repeats the boundary explicitly so a production descriptor passed to a debug build is refused
/// here rather than falling through to an OS assessment of unsigned code.
pub fn require_fixture(expected: &ReleaseDescriptor) -> InstallResult<()> {
    let identity = Channel::Fixture.identity();
    if expected.channel != Channel::Fixture
        || expected.host_name != identity.host_name
        || expected.bundle_id != identity.bundle_id
        || !expected
            .policy
            .publishers
            .iter()
            .any(|publisher| publisher == FIXTURE_PUBLISHER)
    {
        return Err(denied());
    }
    Ok(())
}

/// The accepted assessment verdict for a target.
fn assessment(target: Target) -> &'static str {
    match target {
        Target::WindowsX64 => TRUST_ASSESSMENT_WINDOWS_AUTHENTICODE,
        Target::MacosArm64 => TRUST_ASSESSMENT_MACOS_GATEKEEPER,
    }
}

/// Require both the fixture identity and that the adapter is used for its own target.
fn require_fixture_for(target: Target, expected: &ReleaseDescriptor) -> InstallResult<()> {
    require_fixture(expected)?;
    if expected.target != target {
        return Err(denied());
    }
    Ok(())
}

/// The carrier proof for a fixture debug package.
///
/// `carrier` must be the exact policy carrier document read from the package. The returned proof
/// reports [`FIXTURE_PUBLISHER`] and the target's accepted assessment; the decision layer still
/// compares the carrier's descriptor against the compiled-in expected descriptor and the signer
/// against the authenticated policy, so no other package is accepted.
pub fn trust_proof(
    target: Target,
    expected: &ReleaseDescriptor,
    carrier: Vec<u8>,
) -> InstallResult<TrustProof> {
    require_fixture_for(target, expected)?;
    TrustProof::new(FIXTURE_PUBLISHER, assessment(target), carrier)
}

/// The host assessment for a fixture debug package.
///
/// The debug installer is never a boundary for production code: the host is accepted because the
/// package is the fixture package, not because the OS assessed its unsigned bytes.
pub fn host_assessment(
    target: Target,
    expected: &ReleaseDescriptor,
) -> InstallResult<HostAssessment> {
    require_fixture_for(target, expected)?;
    HostAssessment::new(FIXTURE_PUBLISHER, assessment(target))
}

/// The fixture install root directory name (`%LOCALAPPDATA%\Veles\NativeBridgeFixture`).
const FIXTURE_ROOT_DIR: &str = "NativeBridgeFixture";
/// The parent of the fixture install root.
const FIXTURE_ROOT_PARENT: &str = "Veles";

/// Why `root` is not the fixture install root the debug helper may trust.
fn is_fixture_root(root: &Path) -> bool {
    root.file_name().and_then(|name| name.to_str()) == Some(FIXTURE_ROOT_DIR)
        && root
            .parent()
            .and_then(Path::file_name)
            .and_then(|name| name.to_str())
            == Some(FIXTURE_ROOT_PARENT)
}

/// The exact staged helper image the debug installer places for one fixture transaction.
///
/// This is the same deterministic outside-root staging path `RemovalCoordinator` uses; `None`
/// means this is not a fixture install root or the transaction is not a 32-byte hex value.
pub fn fixture_cleanup_helper_path(root: &Path, transaction: &str) -> Option<PathBuf> {
    if !is_fixture_root(root)
        || transaction.len() != 64
        || !transaction
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
    {
        return None;
    }
    let directory =
        crate::removal::cleanup_staging_directory_for_root(&root.to_string_lossy(), transaction);
    let helper = if cfg!(windows) {
        "helper.exe"
    } else {
        "helper"
    };
    Some(directory.join(helper))
}

/// The fixture publisher for the cleanup helper's own image, or `None`.
///
/// The helper accepts its own unsigned image only when **all** of the following hold, so a
/// production install (or an arbitrary executable) can never use the debug path:
///
/// * `root` is exactly `<...>/Veles/NativeBridgeFixture`;
/// * `transaction` is a full 32-byte hex transaction id;
/// * `helper` is exactly the deterministic staged image the removal coordinator creates for that
///   root and transaction.
pub fn fixture_helper_publisher(
    helper: &Path,
    root: &Path,
    transaction: &str,
) -> Option<&'static str> {
    let expected = fixture_cleanup_helper_path(root, transaction)?;
    // Compare the files, not the spelling: Windows canonicalisation may change case or short
    // names while still naming the same staged image.
    let helper = std::fs::canonicalize(helper).ok()?;
    let expected = std::fs::canonicalize(expected).ok()?;
    super::same_path(&helper, &expected).then_some(FIXTURE_PUBLISHER)
}
