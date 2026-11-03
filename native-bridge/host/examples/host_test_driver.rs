// Test-only driver binary for the OTP-24 process-contract tests (tests/process.rs) and
// direct manual diagnostics. An example is never a production entry point and carries no
// production authority: the production host is src/bin/veles-native-host.rs, which has no
// backend simulation switch by design. (Plain comments rather than doc comments so the
// same source can also be compiled as the `host-test-driver` bin via a one-line include.)
//
// Test-only modes (stripped before the strict production-style launch-argument
// validation, so production argument policy is still exercised):
//
// - `--test-stuck-cleanup` — the fake backend never reports `ShutdownComplete`, making the
//   bounded five-second fail-closed release observation observable end-to-end;
// - `--test-block-shutdown` — the fake backend blocks forever *inside* the `shutdown`
//   release submission (a regressed adapter stuck in a synchronous call): the independent
//   process-exit watchdog must terminate the process with exit 1 by the terminal deadline,
//   because the cooperative runtime cannot come back;
// - `--test-block-then-release=<ms>` — `shutdown` blocks the calling thread that long, then
//   reports the release synchronously (a blocking adapter call returning across the deadline);
// - `--test-freeze-stdout` — the stdout writer parks inside its first frame write (a
//   blocked Chrome pipe), so bounded exits can be shown independent of the writer;
// - `--test-delayed-release=<ms>` — the backend reports `ShutdownComplete` from a detached
//   thread that many milliseconds after the `shutdown` submission (an async OS release),
//   non-blocking, so delayed-cleanup scenarios can share the deadline with a frozen writer.
//
// The fake backend implements the runtime's `Backend` seam against in-memory normalized
// targets (typed case-folded identity, unresolved for hints the driver cannot resolve),
// reports scan/connection/write lifecycle events and emits the process-wide release
// synchronously on `shutdown`. Every mode runs with the production
// [`runtime::process_exit_watchdog`] — the same enforcement the production entries use — so
// valid bounded exits are also verified never to be killed, and a wedged runtime really is.

use std::io::{self, Write};
use std::time::Duration;

use veles_ble_host::backend::{Backend, BackendEvent, Failure, NormalizedTarget, Target, Token};
use veles_ble_host::buffer::Payload;
use veles_ble_host::core::EXIT_HOST_FAILURE;
use veles_ble_host::fragment::LinkCapacity;
use veles_ble_host::host::{process_io, validate_launch, write_diagnostic};
use veles_ble_host::ingress::BackendEvents;
use veles_ble_host::runtime::{self, Config, Io};

const TEST_STUCK_CLEANUP: &str = "--test-stuck-cleanup";
const TEST_BLOCK_SHUTDOWN: &str = "--test-block-shutdown";
const TEST_FREEZE_STDOUT: &str = "--test-freeze-stdout";
const TEST_DELAYED_RELEASE_PREFIX: &str = "--test-delayed-release=";
const TEST_BLOCK_THEN_RELEASE_PREFIX: &str = "--test-block-then-release=";

/// Parks the runtime's writer inside its first frame write — the blocked-pipe scenario.
struct FreezeWrite {
    inner: Box<dyn Write + Send>,
}

impl Write for FreezeWrite {
    fn write(&mut self, _buf: &[u8]) -> io::Result<usize> {
        let _ = &self.inner;
        loop {
            std::thread::park();
        }
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

/// Deterministic fake backend: normalized in-memory targets, lifecycle events, and the
/// process-wide release — synchronously, unless a test mode withholds, delays or blocks it.
struct TestBackend {
    events: BackendEvents,
    /// Never report `ShutdownComplete` (stuck-cleanup mode).
    stuck: bool,
    /// Report `ShutdownComplete` this many milliseconds after the `shutdown` submission,
    /// from a detached thread (delayed-release mode).
    release_delay: Option<Duration>,
    /// Block forever inside the `shutdown` submission (block-shutdown mode): the runtime
    /// thread never returns, so only the process-exit watchdog can bound the exit.
    block_shutdown: bool,
    /// Block the calling thread inside `shutdown` for this long, then report the release
    /// synchronously (a blocking adapter call that returns late, e.g. across the deadline).
    block_then_release: Option<Duration>,
}

impl TestBackend {
    fn resolved(&self, hint: &str) -> Option<NormalizedTarget> {
        let identity = hint.trim();
        (!identity.is_empty()).then(|| NormalizedTarget::new(identity.to_ascii_lowercase()))
    }
}

impl Backend for TestBackend {
    fn normalize_target(&self, hint: &str) -> Option<NormalizedTarget> {
        self.resolved(hint)
    }

    fn start_scan(&mut self, scan: Token, _service_uuid: &str) {
        self.events.emit(BackendEvent::ScanStarted { scan });
    }

    fn stop_scan(&mut self, scan: Token) {
        // The scan contract: the OS release is observed exactly once per `start_scan`, after
        // the stop. (The core derives the protocol end of the scan from the release, so the
        // 2-scan cap frees up again.)
        self.events.emit(BackendEvent::ScanReleased { scan });
    }

    fn connect(&mut self, conn: Token, target: &Target) {
        // The portable seam carries hints only; resolution happens here.
        let Target::Hint(hint) = target;
        match self.resolved(hint) {
            Some(_) => {
                self.events.emit(BackendEvent::Connected {
                    conn,
                    // The protocol MTU floor: a minimal but budget-capable link.
                    capacity: LinkCapacity::AttMtu(185),
                    platform_hint: Some(hint.clone()),
                });
            }
            None => {
                self.events.emit(BackendEvent::ConnectFailed {
                    conn,
                    failure: Failure::new("unsupported_target", false),
                });
            }
        }
    }

    fn write_fragments(&mut self, conn: Token, send: Token, _frames: Vec<Payload>) {
        self.events.emit(BackendEvent::WriteDone { conn, send });
    }

    fn disconnect(&mut self, conn: Token) {
        self.events.emit(BackendEvent::LinkReleased { conn });
    }

    fn shutdown(&mut self) {
        if self.block_shutdown {
            loop {
                std::thread::park();
            }
        }
        if let Some(delay) = self.block_then_release {
            std::thread::sleep(delay);
            self.events.emit(BackendEvent::ShutdownComplete);
            return;
        }
        if let Some(delay) = self.release_delay {
            let events = self.events.clone();
            std::thread::spawn(move || {
                std::thread::sleep(delay);
                events.emit(BackendEvent::ShutdownComplete);
            });
            return;
        }
        if !self.stuck {
            self.events.emit(BackendEvent::ShutdownComplete);
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let stuck = args.iter().any(|arg| arg == TEST_STUCK_CLEANUP);
    let block = args.iter().any(|arg| arg == TEST_BLOCK_SHUTDOWN);
    let release_delay = args.iter().find_map(|arg| {
        arg.strip_prefix(TEST_DELAYED_RELEASE_PREFIX)
            .and_then(|ms| ms.parse::<u64>().ok())
            .map(Duration::from_millis)
    });
    let block_then_release = args.iter().find_map(|arg| {
        arg.strip_prefix(TEST_BLOCK_THEN_RELEASE_PREFIX)
            .and_then(|ms| ms.parse::<u64>().ok())
            .map(Duration::from_millis)
    });
    let freeze = args.iter().any(|arg| arg == TEST_FREEZE_STDOUT);
    let launch_args: Vec<String> = args
        .iter()
        .filter(|arg| {
            arg.as_str() != TEST_STUCK_CLEANUP
                && arg.as_str() != TEST_BLOCK_SHUTDOWN
                && arg.as_str() != TEST_FREEZE_STDOUT
                && !arg.starts_with(TEST_DELAYED_RELEASE_PREFIX)
                && !arg.starts_with(TEST_BLOCK_THEN_RELEASE_PREFIX)
        })
        .cloned()
        .collect();

    let code = match validate_launch(&launch_args) {
        Err(refusal) => {
            write_diagnostic(refusal.stage());
            refusal.exit_code()
        }
        Ok(_launch) => match process_io() {
            Err(_) => {
                write_diagnostic("launch_stdio");
                EXIT_HOST_FAILURE
            }
            Ok(stdio) => {
                let stdout: Box<dyn Write + Send> = if freeze {
                    Box::new(FreezeWrite {
                        inner: stdio.stdout,
                    })
                } else {
                    stdio.stdout
                };
                runtime::run(
                    |events| TestBackend {
                        events,
                        stuck,
                        block_shutdown: block,
                        release_delay,
                        block_then_release,
                    },
                    Io {
                        stdin: stdio.stdin,
                        stdout,
                    },
                    // The real production watchdog: every driver child enforces the terminal
                    // deadline exactly as the shipped host would, including the blocked
                    // submission whose kill this binary exists to verify.
                    Config::production(
                        format!(
                            "veles-native-host/{}+test-driver",
                            env!("CARGO_PKG_VERSION")
                        ),
                        "test",
                    ),
                )
            }
        },
    };
    // `run` returning never terminates a parked writer thread; the process exits once.
    std::process::exit(code);
}
