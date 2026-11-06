//! Bounded cleanup helper entry point (OTP-25 Task 8).
//!
//! The installer stages this signed executable into a private temporary
//! directory outside the removable root, hands it inherited pipe handles as its
//! standard input (one bounded request) and standard output (one authenticated
//! completion), and exits. See [`veles_bridge_installer::cleanup`] for the
//! capability contract: exact owned identities only, no recursive delete, no
//! shell, no process termination and no reboot-time deletion fallback.
//!
//! Exit status is 0 only for an authenticated `complete`; every other outcome is
//! a refused or pending cleanup and exits 1, so a caller can never read silence
//! as success.

fn main() {
    std::process::exit(veles_bridge_installer::cleanup::run_cleanup_process());
}
