//! Build-target selection of the platform adapter (OTP-25 F1).
//!
//! The engine must run the adapter its target needs: `WindowsPlatform` on
//! Windows, `MacosPlatform` on macOS and the portable reference adapter only on a
//! host that has no product backend. Before this module the binary selected the
//! portable adapter on every non-Windows host, so the shipped macOS engine
//! answered `unsupported-target` and could never install anything.
//!
//! The mapping is a pure function of the target ([`platform_kind_for`]) so the
//! selection is testable on any host, and [`host_platform`] is the single place
//! the shipped binary constructs its adapter.

use std::path::Path;

use crate::model::{InstallResult, ReleaseDescriptor};
use crate::paths::InstallPaths;
#[cfg(not(any(windows, target_os = "macos")))]
use crate::platform::PortablePlatform;
use crate::platform::{
    CarrierEntry, HostAssessment, HostState, InstallLock, OwnedEntry, PackageSource, Platform,
    Registration, StateDocument, TrustProof, ValidatedPath,
};

#[cfg(target_os = "macos")]
use crate::platform::MacosPlatform;
#[cfg(windows)]
use crate::platform::WindowsPlatform;

/// The operating-system family one build targets.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HostOs {
    Windows,
    Macos,
    Other,
}

impl HostOs {
    /// The family this build targets.
    pub const CURRENT: Self = {
        #[cfg(windows)]
        {
            Self::Windows
        }
        #[cfg(target_os = "macos")]
        {
            Self::Macos
        }
        #[cfg(not(any(windows, target_os = "macos")))]
        {
            Self::Other
        }
    };
}

/// The platform backend a target selects.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PlatformKind {
    Windows,
    Macos,
    Portable,
}

/// The adapter family a target must instantiate.
pub const fn platform_kind_for(os: HostOs) -> PlatformKind {
    match os {
        HostOs::Windows => PlatformKind::Windows,
        HostOs::Macos => PlatformKind::Macos,
        HostOs::Other => PlatformKind::Portable,
    }
}

/// One concrete platform adapter, selected by the build target.
///
/// Only the variant the build target provides exists: a Linux build cannot
/// construct the macOS adapter and a macOS build cannot fall through to the
/// portable one by accident.
#[derive(Debug)]
pub enum HostPlatform {
    #[cfg(windows)]
    Windows(WindowsPlatform),
    #[cfg(target_os = "macos")]
    Macos(MacosPlatform),
    #[cfg(not(any(windows, target_os = "macos")))]
    Portable(PortablePlatform),
}

impl HostPlatform {
    /// The family of the selected adapter.
    pub const fn kind(&self) -> PlatformKind {
        match self {
            #[cfg(windows)]
            Self::Windows(_) => PlatformKind::Windows,
            #[cfg(target_os = "macos")]
            Self::Macos(_) => PlatformKind::Macos,
            #[cfg(not(any(windows, target_os = "macos")))]
            Self::Portable(_) => PlatformKind::Portable,
        }
    }

    fn inner(&self) -> &dyn Platform {
        match self {
            #[cfg(windows)]
            Self::Windows(platform) => platform,
            #[cfg(target_os = "macos")]
            Self::Macos(platform) => platform,
            #[cfg(not(any(windows, target_os = "macos")))]
            Self::Portable(platform) => platform,
        }
    }
}

/// The adapter this build must run.
pub fn host_platform() -> HostPlatform {
    #[cfg(windows)]
    {
        HostPlatform::Windows(WindowsPlatform::new())
    }
    #[cfg(target_os = "macos")]
    {
        HostPlatform::Macos(MacosPlatform::new())
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    {
        HostPlatform::Portable(PortablePlatform::new())
    }
}

impl Default for HostPlatform {
    fn default() -> Self {
        host_platform()
    }
}

impl Platform for HostPlatform {
    fn lock(&self, paths: &InstallPaths) -> InstallResult<InstallLock> {
        self.inner().lock(paths)
    }

    fn write_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        self.inner().write_durable(path, bytes)
    }

    fn apply_launch_permissions(
        &self,
        path: &ValidatedPath,
        executable: bool,
    ) -> InstallResult<()> {
        self.inner().apply_launch_permissions(path, executable)
    }

    fn check_launch_permissions(
        &self,
        path: &ValidatedPath,
        executable: bool,
    ) -> InstallResult<()> {
        self.inner().check_launch_permissions(path, executable)
    }

    fn verify_host(
        &self,
        root: &Path,
        executable: &str,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<HostAssessment> {
        self.inner().verify_host(root, executable, expected)
    }

    fn replace_durable(&self, path: &ValidatedPath, bytes: &[u8]) -> InstallResult<()> {
        self.inner().replace_durable(path, bytes)
    }

    fn write_state(
        &self,
        paths: &InstallPaths,
        document: StateDocument,
        bytes: &[u8],
    ) -> InstallResult<()> {
        self.inner().write_state(paths, document, bytes)
    }

    fn read_state(
        &self,
        paths: &InstallPaths,
        document: StateDocument,
    ) -> InstallResult<Option<Vec<u8>>> {
        self.inner().read_state(paths, document)
    }

    fn quarantine_state(&self, paths: &InstallPaths, document: StateDocument) -> InstallResult<()> {
        self.inner().quarantine_state(paths, document)
    }

    fn create_directory(&self, path: &ValidatedPath) -> InstallResult<()> {
        self.inner().create_directory(path)
    }

    fn replace_manifest(&self, staged: &ValidatedPath, live: &ValidatedPath) -> InstallResult<()> {
        self.inner().replace_manifest(staged, live)
    }

    fn read_registration(&self, paths: &InstallPaths) -> InstallResult<Option<Registration>> {
        self.inner().read_registration(paths)
    }

    fn publish_manifest(&self, paths: &InstallPaths, value: &Registration) -> InstallResult<()> {
        self.inner().publish_manifest(paths, value)
    }

    fn publish_registration(
        &self,
        paths: &InstallPaths,
        value: &Registration,
    ) -> InstallResult<()> {
        self.inner().publish_registration(paths, value)
    }

    fn remove_registration(
        &self,
        paths: &InstallPaths,
        expected: &Registration,
    ) -> InstallResult<()> {
        self.inner().remove_registration(paths, expected)
    }

    fn inspect_hosts(&self, paths: &InstallPaths) -> InstallResult<HostState> {
        self.inner().inspect_hosts(paths)
    }

    fn verify_trust(
        &self,
        source: &PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        self.inner().verify_trust(source, expected)
    }

    fn verify_retained(
        &self,
        source: &PackageSource,
        expected: &ReleaseDescriptor,
    ) -> InstallResult<TrustProof> {
        self.inner().verify_retained(source, expected)
    }

    fn carrier_entries(&self, source: &PackageSource) -> InstallResult<Vec<CarrierEntry>> {
        self.inner().carrier_entries(source)
    }

    fn install_carrier(
        &self,
        source: &PackageSource,
        destination: &ValidatedPath,
    ) -> InstallResult<()> {
        self.inner().install_carrier(source, destination)
    }

    fn remove_owned(&self, entry: &OwnedEntry) -> InstallResult<()> {
        self.inner().remove_owned(entry)
    }

    fn flush_parent(&self, path: &ValidatedPath) -> InstallResult<()> {
        self.inner().flush_parent(path)
    }
}
