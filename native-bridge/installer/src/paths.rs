//! Fixed per-user install layout.
//!
//! The layout is deliberately not configurable: there is no CLI root override
//! and no environment-variable lookup in this module. [`InstallPaths::for_user`]
//! receives the user home and, on Windows, the `%LOCALAPPDATA%` known folder from
//! the caller; production callers obtain those values from OS user/known-folder
//! APIs and the injected values exist for tests only.
//!
//! ```text
//! <root>/versions/<version>-<payload-id>/  immutable verified payloads
//! <root>/maintenance/                      signed maintenance/uninstall assets
//! <root>/state/                            journal, receipts, staging and lock
//! ```
//!
//! Windows keeps the Native Messaging manifest at `<root>/<host-name>.json`;
//! macOS writes the Chrome registration to
//! `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/<host-name>.json`.
//! Development and fixture channels have distinct roots *and* distinct host names,
//! so a test fixture can never register over a production or development install.

use std::path::{Component, Path, PathBuf};

use crate::model::{Channel, Digest, FailureCode, InstallFailure, InstallResult, Target, Version};

/// Production root directory name below the platform-known base.
pub const ROOT_DIR_PRODUCTION: &str = "NativeBridge";
/// Development root directory name; distinct from production.
pub const ROOT_DIR_DEVELOPMENT: &str = "NativeBridgeDev";
/// Test-only fixture root directory name; distinct from both real channels.
pub const ROOT_DIR_FIXTURE: &str = "NativeBridgeFixture";
/// Immutable payload versions.
pub const VERSIONS_DIR: &str = "versions";
/// Signed maintenance and uninstall assets.
pub const MAINTENANCE_DIR: &str = "maintenance";
/// Installer state (journal, receipts, staging, lock).
pub const STATE_DIR: &str = "state";
/// Staging directory on the destination volume.
pub const STAGING_DIR: &str = "staging";
/// Exclusive installer lock file name.
pub const LOCK_FILE: &str = "installer.lock";
/// Transaction journal file name.
pub const JOURNAL_FILE: &str = "journal.json";
/// Committed receipt file name.
pub const RECEIPT_FILE: &str = "receipt.json";
/// macOS Chrome Native Messaging registration directory below Application Support.
pub const MACOS_CHROME_HOSTS_DIR: &str = "Google/Chrome/NativeMessagingHosts";
/// Maximum rendered absolute path accepted from an injected root.
pub const MAX_ROOT_BYTES: usize = 4096;

/// Every absolute path the installer uses for one user, target and channel.
///
/// The value is a pure path calculation: `for_user` performs no IO and never
/// creates directories. The caller must acquire the lock before mutating state;
/// [`crate::platform::Platform::lock`] creates the private layout on demand.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallPaths {
    target: Target,
    channel: Channel,
    user_home: PathBuf,
    local_app_data: Option<PathBuf>,
    root: PathBuf,
    versions: PathBuf,
    maintenance: PathBuf,
    state: PathBuf,
    staging: PathBuf,
    lock: PathBuf,
    journal: PathBuf,
    receipt: PathBuf,
    registration: PathBuf,
    registration_name: &'static str,
}

impl InstallPaths {
    /// Compute the fixed per-user layout.
    ///
    /// `target` selects the platform root:
    ///
    /// - [`Target::WindowsX64`]: `<local_app_data>/Veles/<root-dir>`; a missing
    ///   `local_app_data` is an error (never a `%LOCALAPPDATA%` fallback).
    /// - [`Target::MacosArm64`]: `<user_home>/Library/Application Support/Veles/<root-dir>`;
    ///   passing `local_app_data` for macOS is rejected rather than ignored.
    ///
    /// Both supplied roots must be absolute, UTF-8 renderable, control-character
    /// free and free of `.`/`..` components. The channel selects a distinct root
    /// directory and the host/bundle name from the Task 2 channel identity.
    pub fn for_user(
        target: Target,
        channel: Channel,
        user_home: &Path,
        local_app_data: Option<&Path>,
    ) -> InstallResult<Self> {
        let user_home = normalized_absolute(user_home)?;
        let local_app_data = match target {
            Target::WindowsX64 => Some(normalized_absolute(
                local_app_data.ok_or_else(invalid_input)?,
            )?),
            Target::MacosArm64 => {
                if local_app_data.is_some() {
                    // LocalAppData has no macOS meaning. Silently ignoring an
                    // injected root is how the wrong root becomes authoritative.
                    return Err(invalid_input());
                }
                None
            }
        };

        let root_dir = root_dir_name(channel);
        let root = match target {
            Target::WindowsX64 => local_app_data
                .as_ref()
                .expect("validated Windows known folder")
                .join("Veles")
                .join(root_dir),
            Target::MacosArm64 => macos_application_support(&user_home)
                .join("Veles")
                .join(root_dir),
        };
        let registration_name = channel.identity().host_name;
        let registration = match target {
            Target::WindowsX64 => root.join(format!("{registration_name}.json")),
            Target::MacosArm64 => user_home
                .join("Library")
                .join("Application Support")
                .join(MACOS_CHROME_HOSTS_DIR)
                .join(format!("{registration_name}.json")),
        };

        let state = root.join(STATE_DIR);
        let layout = Self {
            target,
            channel,
            user_home,
            local_app_data,
            versions: root.join(VERSIONS_DIR),
            maintenance: root.join(MAINTENANCE_DIR),
            staging: state.join(STAGING_DIR),
            lock: state.join(LOCK_FILE),
            journal: state.join(JOURNAL_FILE),
            receipt: state.join(RECEIPT_FILE),
            state,
            registration,
            registration_name,
            root,
        };
        layout.validate_rendering()?;
        Ok(layout)
    }

    /// The fixed install root for this target and channel.
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// The immutable payload root: `<root>/versions`.
    pub fn versions(&self) -> &Path {
        &self.versions
    }

    /// The maintenance assets root: `<root>/maintenance`.
    pub fn maintenance(&self) -> &Path {
        &self.maintenance
    }

    /// Installer state root: `<root>/state`.
    pub fn state(&self) -> &Path {
        &self.state
    }

    /// Staging directory inside the fixed root (same volume as activation).
    pub fn staging(&self) -> &Path {
        &self.staging
    }

    /// Exclusive installer lock path.
    pub fn lock_path(&self) -> &Path {
        &self.lock
    }

    /// Transaction journal path.
    pub fn journal_path(&self) -> &Path {
        &self.journal
    }

    /// Committed receipt path.
    pub fn receipt_path(&self) -> &Path {
        &self.receipt
    }

    /// Exact Native Messaging registration path for this target and channel.
    pub fn registration_path(&self) -> &Path {
        &self.registration
    }

    /// Exact host/bundle name this layout owns.
    pub fn registration_name(&self) -> &'static str {
        self.registration_name
    }

    /// The selected target.
    pub fn target(&self) -> Target {
        self.target
    }

    /// The selected release channel.
    pub fn channel(&self) -> Channel {
        self.channel
    }

    /// The injected (or OS-provided) user home.
    pub fn user_home(&self) -> &Path {
        &self.user_home
    }

    /// The injected (or OS-provided) Windows LocalAppData known folder.
    pub fn local_app_data(&self) -> Option<&Path> {
        self.local_app_data.as_deref()
    }

    /// `<root>/versions/<version>-<payload-id>` for an immutable payload.
    pub fn version_dir(&self, version: &Version, payload_id: Digest) -> PathBuf {
        self.versions
            .join(format!("{version}-{}", payload_id.to_hex()))
    }

    /// `<root>/versions/<version>-<payload-id>` as the normalized relative form
    /// every owned claim and journal record uses.
    pub fn version_dir_relative(&self, version: &Version, payload_id: Digest) -> String {
        format!("{VERSIONS_DIR}/{version}-{}", payload_id.to_hex())
    }

    fn validate_rendering(&self) -> InstallResult<()> {
        for path in [
            &self.root,
            &self.versions,
            &self.maintenance,
            &self.state,
            &self.staging,
            &self.lock,
            &self.journal,
            &self.receipt,
            &self.registration,
        ] {
            let rendered = path.to_str().ok_or_else(invalid_input)?;
            if rendered.is_empty() || rendered.len() > MAX_ROOT_BYTES {
                return Err(invalid_input());
            }
        }
        Ok(())
    }
}

/// The fixed root directory name for a channel.
pub const fn root_dir_name(channel: Channel) -> &'static str {
    match channel {
        Channel::Production => ROOT_DIR_PRODUCTION,
        Channel::Development => ROOT_DIR_DEVELOPMENT,
        Channel::Fixture => ROOT_DIR_FIXTURE,
    }
}

fn macos_application_support(user_home: &Path) -> PathBuf {
    user_home.join("Library").join("Application Support")
}

/// Normalize an injected absolute root: components are kept, `.`/`..` are
/// rejected, and the rendered value must be UTF-8 with no control characters.
fn normalized_absolute(path: &Path) -> InstallResult<PathBuf> {
    if !path.is_absolute() {
        return Err(invalid_input());
    }
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Prefix(prefix) => normalized.push(prefix.as_os_str()),
            Component::RootDir => normalized.push(component.as_os_str()),
            Component::CurDir | Component::ParentDir => return Err(invalid_input()),
            Component::Normal(part) => {
                let text = part.to_str().ok_or_else(invalid_input)?;
                if text.is_empty() || text.chars().any(char::is_control) {
                    return Err(invalid_input());
                }
                normalized.push(part);
            }
        }
    }
    let rendered = normalized.to_str().ok_or_else(invalid_input)?;
    if rendered.is_empty() || rendered.len() > MAX_ROOT_BYTES {
        return Err(invalid_input());
    }
    Ok(normalized)
}

fn invalid_input() -> InstallFailure {
    InstallFailure::new(FailureCode::InvalidInput)
}
