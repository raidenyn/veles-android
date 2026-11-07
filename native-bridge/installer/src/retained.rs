//! Retained release metadata for rollback.
//!
//! A rollback must not depend on the user still having the older signed
//! download, and it must not authenticate a *copy* of the signed carrier: a
//! portable byte copy drops the metadata the platform signature covers
//! (extended attributes, resource forks, link targets), so a carrier that
//! authenticates at its source can fail verification after being copied.
//!
//! The carrier artifact is therefore installed once, in place, inside the
//! immutable version directory by [`crate::platform::Platform::install_carrier`],
//! and this document *references* that installed artifact: its absolute path
//! inside the version directory and the digest of the authenticated carrier
//! document the OS path produced. The committed receipt claims both this
//! document and the installed carrier, so a later rollback can verify the exact
//! artifact already on disk against the installation it committed.
//!
//! The document is data, never code, and deliberately carries **no** signer or
//! assessment string: those are trust verdicts, and a rollback re-establishes
//! them by running the same OS signature/assessment path over the installed
//! carrier artifact (see [`crate::platform::Platform::verify_retained`]).

use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::model::{
    Digest, FailureCode, InstallFailure, InstallResult, MAX_PATH_BYTES, ReleaseDescriptor,
    canonical_json, parse_version,
};
use crate::paths::InstallPaths;
use crate::platform::{AuthenticatedPackage, package_carrier_name};

/// The only supported retained-metadata schema generation.
///
/// Generation 2 removed the stored signer/assessment strings. Generation 3
/// replaced the embedded carrier *bytes* with a reference to the carrier
/// artifact installed inside the version directory, so no copy is ever required
/// and the artifact keeps the metadata its platform signature covers.
pub const RETAINED_SCHEMA_VERSION: u32 = 3;
/// File name of the retained release metadata inside a version directory.
pub const RETAINED_FILE_NAME: &str = "installer-retained.json";
/// The retained document is bounded to 1 MiB (a descriptor and a path).
pub const MAX_RETAINED_BYTES: usize = 1024 * 1024;

/// Authenticated release metadata retained with an immutable version.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetainedInstall {
    pub schema: u32,
    /// The strict authenticated release descriptor.
    pub descriptor: ReleaseDescriptor,
    /// Absolute path of the signed carrier artifact installed inside this
    /// release's immutable version directory.
    pub carrier_path: String,
    /// SHA-256 of the authenticated policy-carrier document the OS path
    /// extracted from that artifact. It binds the in-place artifact to the
    /// policy evidence the installation committed.
    pub carrier_sha256: Digest,
}

impl RetainedInstall {
    /// Capture the authenticated metadata of `package`, referencing the carrier
    /// artifact installed below `version_relative`.
    pub fn from_package(
        paths: &InstallPaths,
        version_relative: &str,
        package: &AuthenticatedPackage,
    ) -> InstallResult<Self> {
        let version = parse_version(&package.descriptor().version)?;
        if version_relative != paths.version_dir_relative(&version, package.descriptor().payload_id)
        {
            return Err(invalid_package());
        }
        let absolute = paths
            .version_dir(&version, package.descriptor().payload_id)
            .join(package_carrier_name(paths.target()));
        let carrier_path = absolute.to_str().ok_or_else(invalid_package)?.to_owned();
        let value = Self {
            schema: RETAINED_SCHEMA_VERSION,
            descriptor: package.descriptor().clone(),
            carrier_path,
            carrier_sha256: Digest::of(package.proof().carrier()),
        };
        value.validate()?;
        Ok(value)
    }

    /// The installed carrier path this document names, relative to the root.
    ///
    /// A reference is accepted only when it is exactly *this* release's
    /// installed carrier artifact: a document naming any other file, or a
    /// relative/escaping path, is not a rollback authority.
    pub fn carrier_relative(&self, paths: &InstallPaths) -> InstallResult<String> {
        let version = parse_version(&self.descriptor.version)?;
        let relative = format!(
            "{}/{}",
            paths.version_dir_relative(&version, self.descriptor.payload_id),
            package_carrier_name(paths.target())
        );
        if Path::new(&self.carrier_path)
            != paths
                .version_dir(&version, self.descriptor.payload_id)
                .join(package_carrier_name(paths.target()))
        {
            return Err(invalid_package());
        }
        Ok(relative)
    }

    /// The host API window this retained release supports.
    pub fn api_window(&self) -> (u32, u32) {
        (self.descriptor.api_min, self.descriptor.api_max)
    }

    /// Validate bounds and the shape of the in-place carrier reference.
    pub fn validate(&self) -> InstallResult<()> {
        if self.schema != RETAINED_SCHEMA_VERSION {
            return Err(invalid_package());
        }
        self.descriptor.validate()?;
        if self.carrier_path.is_empty()
            || self.carrier_path.len() > MAX_PATH_BYTES
            || self.carrier_path.chars().any(char::is_control)
            || !Path::new(&self.carrier_path).is_absolute()
        {
            return Err(invalid_package());
        }
        if self.to_bytes()?.len() > MAX_RETAINED_BYTES {
            return Err(invalid_package());
        }
        Ok(())
    }

    /// Strictly parse a retained document.
    pub fn parse(bytes: &[u8]) -> InstallResult<Self> {
        if bytes.is_empty() || bytes.len() > MAX_RETAINED_BYTES {
            return Err(invalid_package());
        }
        let value: Self = serde_json::from_slice(bytes).map_err(|_| invalid_package())?;
        value.validate()?;
        Ok(value)
    }

    /// The canonical bounded document bytes.
    pub fn to_bytes(&self) -> InstallResult<Vec<u8>> {
        let bytes = canonical_json(self)?;
        if bytes.len() > MAX_RETAINED_BYTES {
            return Err(invalid_package());
        }
        Ok(bytes)
    }
}

fn invalid_package() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidPackage)
}
