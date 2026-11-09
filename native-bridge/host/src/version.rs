//! The host's release version, reported in the `hello` user agent (`veles-native-host/<version>`).
//!
//! The installer producer injects its one installer version at build time through
//! `VELES_HOST_VERSION` (compiled in with `option_env!`, so Cargo rebuilds when it changes);
//! `Cargo.toml` is never rewritten. A plain `cargo build` without the variable reports the
//! crate's package version.

/// The injected installer version, or the crate's package version when none was injected.
pub const HOST_VERSION: &str = match option_env!("VELES_HOST_VERSION") {
    Some(version) => version,
    None => env!("CARGO_PKG_VERSION"),
};

#[cfg(test)]
mod tests {
    use super::HOST_VERSION;

    #[test]
    fn host_version_defaults_to_package_version() {
        if option_env!("VELES_HOST_VERSION").is_none() {
            assert_eq!(HOST_VERSION, env!("CARGO_PKG_VERSION"));
        } else {
            assert_eq!(Some(HOST_VERSION), option_env!("VELES_HOST_VERSION"));
        }
    }
}
