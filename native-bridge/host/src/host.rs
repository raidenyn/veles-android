//! Process entry for the OTP-24 production native-messaging host (Task 5b): launch-argument
//! validation, the build-time extension-origin allowlist, platform-gated dispatch, raw stdio
//! ownership and bounded typed stderr diagnostics. The deterministic core and the headless
//! runtime ([`crate::core::Core`]/[`crate::runtime::run`]) stay payload-blind; this module
//! adds everything around them that is only safe to decide once, at process start.
//!
//! # Launch contract (Chrome native messaging)
//!
//! Chrome starts the host with exactly one required argument — the calling origin,
//! `chrome-extension://<32-char-id>/` — plus at most the documented platform launcher
//! argument `--parent-window=<handle>` (Windows). Anything else is refused with
//! [`EXIT_REJECTED`] and one bounded typed line on stderr: unknown flags (including every
//! conceivable simulator/backend switch — there is deliberately none), extra positionals,
//! duplicated or malformed launcher arguments. Stdout is never touched on a refusal.
//!
//! # Origin allowlist (build-time only)
//!
//! The valid origins are the ones embedded at compile time. The default is the single
//! development placeholder [`DEVELOPMENT_ORIGIN`], shared verbatim with
//! `native-bridge/src/manifest.mjs`; it is NOT an established published production origin
//! (the final published origin/registration policy remains OTP-25). `VELES_ALLOWED_ORIGINS`
//! (comma-separated) may override it — evaluated by `option_env!` at compile time, so a
//! runtime environment change can never alter the allowlist. Every embedded entry — the
//! placeholder and every override piece — is validated at compile time (`chrome-extension://`
//! scheme, 32 characters from the extension-id alphabet `a`–`p`, one trailing slash): an
//! invalid build-time allowlist fails the build. External acceptance tooling that derives a
//! fixture origin rebuilds this same production source with that recorded allowlist.
//!
//! # Platform dispatch
//!
//! [`run_process`] is platform-gated. On a platform without a production adapter
//! (Linux and every other target the Tasks 6/7 adapters do not cover), the host refuses
//! before any runtime starts — it never pretends to be another platform. On Windows the
//! production WinRT adapter (Task 6) runs the runtime; on macOS the production CoreBluetooth
//! adapter (Task 7) runs it. No gate ever starts a simulator.
//!
//! # Diagnostics
//!
//! At most one short line on stderr: `veles-native-host: stage=<token>` — typed status
//! only, no payload, no argv echo, no trace-file or environment sinks. Protocol exits stay
//! fully silent on both stderr and stdout diagnostics; stdout always carries protocol
//! envelopes only.

use std::io;

use crate::core::EXIT_REJECTED;
use crate::runtime::Io;

/// The development placeholder origin, shared verbatim with
/// `native-bridge/src/manifest.mjs` (and `web-extension/src/manifest.ts`). Development
/// only: not an established published production origin; the published origin/registration
/// policy remains OTP-25.
pub const DEVELOPMENT_ORIGIN: &str = "chrome-extension://abcdefghijklmnopabcdefghijklmnop/";

/// Maximum number of comma-separated origins in one build-time allowlist.
const MAX_ORIGINS: usize = 8;

const SCHEME: &[u8] = b"chrome-extension://";
const ID_LEN: usize = 32;
/// `chrome-extension://` (19) + 32-char extension id + one `/`.
const ORIGIN_LEN: usize = SCHEME.len() + ID_LEN + 1;

/// Compile-time override of the allowlist (`option_env!` is a compile-time read; there is
/// deliberately no runtime override path).
const ALLOWLIST_OVERRIDE: Option<&'static str> = option_env!("VELES_ALLOWED_ORIGINS");

/// Scheme/id/slash shape of one origin entry, checked over the byte range
/// `data[start..end]` (ASCII comparisons only, so no `str` boundary invariants are
/// assumed). Const-evaluable: the allowlist is validated when the program is built.
const fn valid_origin_range(data: &[u8], start: usize, end: usize) -> bool {
    let len = end.saturating_sub(start);
    if len != ORIGIN_LEN {
        return false;
    }
    let mut index = 0;
    while index < SCHEME.len() {
        if data[start + index] != SCHEME[index] {
            return false;
        }
        index += 1;
    }
    while index < SCHEME.len() + ID_LEN {
        let byte = data[start + index];
        if byte < b'a' || byte > b'p' {
            return false;
        }
        index += 1;
    }
    data[start + index] == b'/'
}

/// Walks a comma-separated allowlist and validates every entry at compile time. Panics in
/// const evaluation → an invalid [`ALLOWLIST_OVERRIDE`] or a corrupted placeholder fails
/// the build instead of shipping.
const fn assert_allowlist_valid(raw: &str) {
    let bytes = raw.as_bytes();
    let mut count = 0;
    let mut start = 0;
    let mut index = 0;
    while index <= bytes.len() {
        if index == bytes.len() || bytes[index] == b',' {
            if index == start {
                panic!("veles-host allowlist: empty origin entry");
            }
            if count >= MAX_ORIGINS {
                panic!("veles-host allowlist: more than 8 origins");
            }
            if !valid_origin_range(bytes, start, index) {
                panic!("veles-host allowlist: invalid origin entry");
            }
            count += 1;
            start = index + 1;
        }
        index += 1;
    }
}

const _: () = assert_allowlist_valid(DEVELOPMENT_ORIGIN);
const _: () = if let Some(raw) = ALLOWLIST_OVERRIDE {
    assert_allowlist_valid(raw)
};

/// The effective allowlist: the compile-time override when one was embedded, otherwise the
/// development placeholder. The values are compile-time data only — this split walks
/// already-validated bytes, it is not a runtime configuration input.
fn allowlist_pieces() -> impl Iterator<Item = &'static str> {
    let raw = ALLOWLIST_OVERRIDE.unwrap_or(DEVELOPMENT_ORIGIN);
    raw.split(',').filter(|piece| !piece.is_empty())
}

/// Whether `origin` is on the embedded build-time allowlist.
pub fn origin_allowed(origin: &str) -> bool {
    allowlist_pieces().any(|allowed| allowed == origin)
}

/// The launch contract: the matched allowlist origin plus the documented platform launcher
/// argument (Windows `--parent-window=<handle>`), if present.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Launch {
    pub origin: &'static str,
    pub parent_window: Option<u64>,
}

/// A pre-runtime launch refusal; always [`EXIT_REJECTED`], distinguished only by the typed
/// stderr diagnostic.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LaunchRefusal {
    /// argv did not consist of one allowlisted origin plus at most one documented
    /// `--parent-window` argument: unknown flags (no simulator switch exists), extra or
    /// repeated arguments, malformed handles.
    Arguments,
    /// No allowlisted origin argument was supplied.
    Origin,
}

impl LaunchRefusal {
    /// The bounded typed diagnostic stage for [`write_diagnostic`].
    pub fn stage(self) -> &'static str {
        match self {
            Self::Arguments => "launch_arguments",
            Self::Origin => "launch_origin",
        }
    }

    /// The protocol-vocabulary exit code: rejected input, fail-closed, no correlation
    /// (OTP2-BRD-032, code `2`).
    pub fn exit_code(self) -> i32 {
        let _ = self;
        EXIT_REJECTED
    }
}

const PARENT_WINDOW_PREFIX: &str = "--parent-window=";

/// Strict Chrome launcher validation; shared verbatim by the production binary and the
/// test driver, so the process tests exercise the production argument policy.
pub fn validate_launch(args: &[String]) -> Result<Launch, LaunchRefusal> {
    let mut origin: Option<&'static str> = None;
    let mut parent_window: Option<u64> = None;
    for arg in args.get(1..).unwrap_or_default() {
        if let Some(handle) = arg.strip_prefix(PARENT_WINDOW_PREFIX) {
            if parent_window.is_some() || handle.is_empty() {
                return Err(LaunchRefusal::Arguments);
            }
            let Ok(handle) = handle.parse::<u64>() else {
                return Err(LaunchRefusal::Arguments);
            };
            parent_window = Some(handle);
        } else if arg.starts_with("--") || origin.is_some() {
            // Any other flag is refused (there is deliberately no simulator switch), and
            // Chrome never sends a second positional.
            return Err(LaunchRefusal::Arguments);
        } else if let Some(allowed) = allowlist_pieces().find(|allowed| *allowed == arg) {
            origin = Some(allowed);
        } else {
            return Err(LaunchRefusal::Origin);
        }
    }
    let Some(origin) = origin else {
        return Err(LaunchRefusal::Origin);
    };
    Ok(Launch {
        origin,
        parent_window,
    })
}

/// Writes the one bounded typed status line to stderr. Never payload, never argv echo,
/// never stdout; protocol exits do not call this.
pub fn write_diagnostic(stage: &'static str) {
    use std::io::Write;
    let mut stderr = io::stderr().lock();
    let _ = writeln!(stderr, "veles-native-host: stage={stage}");
    let _ = stderr.flush();
}

/// The platform this binary was built for — always the true target, reported by the
/// Tasks 6/7 adapters in `ready.platform`. There is no stand-in value for another platform.
#[cfg(windows)]
pub const HOST_PLATFORM: &str = "windows";
#[cfg(target_os = "macos")]
pub const HOST_PLATFORM: &str = "macos";
#[cfg(not(any(windows, target_os = "macos")))]
pub const HOST_PLATFORM: &str = "unsupported";

/// Runs one host process and returns its exit code. `args` is the full argv. The dispatch
/// is platform-gated: Tasks 6/7 wire the validated launch into the Windows/macOS adapters;
/// everything else refuses without starting a runtime or pretending to be another platform.
/// A wired runtime must always be configured through [`crate::runtime::Config::production`]
/// — the independent process-exit watchdog is what enforces the five-second terminal exit
/// contract when an adapter blocks inside a release submission.
pub fn run_process(args: &[String]) -> i32 {
    match validate_launch(args) {
        Ok(_launch) => platform_entry(),
        Err(refusal) => {
            write_diagnostic(refusal.stage());
            refusal.exit_code()
        }
    }
}

/// Windows: the production WinRT adapter (Task 6). The runtime is always configured through
/// [`crate::runtime::Config::production`] (real process-exit watchdog). There is no simulator
/// or backend-selection path: this is the only backend the Windows binary can run. Raw stdio
/// that cannot be duplicated is a host failure reported by one typed stderr line, before any
/// BLE work.
#[cfg(windows)]
fn platform_entry() -> i32 {
    let stdio = match process_io() {
        Ok(stdio) => stdio,
        Err(_) => {
            write_diagnostic("launch_stdio");
            return crate::core::EXIT_HOST_FAILURE;
        }
    };
    crate::runtime::run(
        crate::winrt::WinrtBackend::new,
        stdio,
        crate::runtime::Config::production(
            format!("veles-native-host/{}", crate::version::HOST_VERSION),
            HOST_PLATFORM,
        ),
    )
}

/// macOS: the production CoreBluetooth adapter (Task 7). Same runtime contract as Windows:
/// always configured through [`crate::runtime::Config::production`], no simulator or
/// backend-selection path, raw stdio that cannot be duplicated is a host failure reported by
/// one typed stderr line before any BLE work.
#[cfg(target_os = "macos")]
fn platform_entry() -> i32 {
    let stdio = match process_io() {
        Ok(stdio) => stdio,
        Err(_) => {
            write_diagnostic("launch_stdio");
            return crate::core::EXIT_HOST_FAILURE;
        }
    };
    crate::runtime::run(
        crate::macos::MacosBackend::new,
        stdio,
        crate::runtime::Config::production(
            format!("veles-native-host/{}", crate::version::HOST_VERSION),
            HOST_PLATFORM,
        ),
    )
}

/// Unsupported platforms refuse without starting any BLE operation (OTP2: never pretend
/// to be Windows).
#[cfg(not(any(windows, target_os = "macos")))]
fn platform_entry() -> i32 {
    write_diagnostic("platform_unsupported");
    EXIT_REJECTED
}

/// Stdio as raw duplicated handles: no std-level locks or line buffering can block process
/// exit; the runtime owns the duplicates exclusively (the reader thread owns `stdin`, the
/// writer thread owns `stdout`). Used by the platform adapters (Tasks 6/7) and by the
/// test driver.
#[cfg(any(windows, unix))]
pub fn process_io() -> io::Result<Io> {
    use std::fs::File;
    #[cfg(unix)]
    use std::os::fd::AsFd;
    #[cfg(windows)]
    use std::os::windows::io::AsHandle;
    #[cfg(windows)]
    let (stdin, stdout) = (
        File::from(std::io::stdin().as_handle().try_clone_to_owned()?),
        File::from(std::io::stdout().as_handle().try_clone_to_owned()?),
    );
    #[cfg(unix)]
    let (stdin, stdout) = (
        File::from(std::io::stdin().as_fd().try_clone_to_owned()?),
        File::from(std::io::stdout().as_fd().try_clone_to_owned()?),
    );
    Ok(Io {
        stdin: Box::new(stdin),
        stdout: Box::new(stdout),
    })
}
