//! Thread wiring around [`Core`]: one stdin reader, one stdout writer (the only code that writes
//! to the Chrome pipe), and the core loop. Shutdown never waits on the reader thread: the process
//! exits while it may still be blocked on an idle stdin.

use std::io::{Read, Write};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError, SyncSender, TrySendError};
use std::thread;
use std::time::{Duration, Instant};

use crate::backend::{Backend, BackendEvent};
use crate::core::{Config, Core, Input};
use crate::framing::{FrameError, read_frame, write_frame};
use crate::outqueue::{MAX_QUEUED_BYTES, MAX_QUEUED_MESSAGES, OutQueue, Pop};
use crate::{trace, trace::summarize_frame};

const CHANNEL_CAPACITY: usize = 4096;
const MAX_TICK: Duration = Duration::from_millis(250);
const DRAIN_GRACE: Duration = Duration::from_secs(2);

/// Cloneable, non-blocking handle backends use to report OS callbacks to the core loop.
#[derive(Clone)]
pub struct BackendEvents {
    tx: SyncSender<Input>,
    overflow: Arc<AtomicBool>,
}

impl BackendEvents {
    /// Never blocks (it may run on an OS callback thread). A full channel poisons the process:
    /// the core fails closed rather than lose a notification and corrupt a fragment stream.
    pub fn emit(&self, event: BackendEvent) {
        match self.tx.try_send(Input::Backend(event)) {
            Ok(()) | Err(TrySendError::Disconnected(_)) => {}
            Err(TrySendError::Full(_)) => self.overflow.store(true, Ordering::SeqCst),
        }
    }
}

pub struct Io {
    pub stdin: Box<dyn Read + Send>,
    pub stdout: Box<dyn Write + Send>,
}

fn spawn_reader(mut stdin: Box<dyn Read + Send>, tx: SyncSender<Input>) {
    thread::Builder::new()
        .name("stdin-reader".into())
        .spawn(move || {
            loop {
                let input = match read_frame(&mut stdin) {
                    Ok(Some(frame)) => {
                        trace!("in  {}", summarize_frame(&frame));
                        Input::Frame(frame)
                    }
                    Ok(None) => Input::StdinEof,
                    Err(FrameError::Io(_)) => Input::StdinIoError,
                    Err(other) => {
                        trace!("in  framing violation: {other:?}");
                        Input::StdinFraming
                    }
                };
                let terminal = !matches!(input, Input::Frame(_));
                // A blocking send applies backpressure to Chrome; it fails once the core is gone.
                if tx.send(input).is_err() || terminal {
                    return;
                }
            }
        })
        .expect("spawn stdin reader");
}

fn spawn_writer(mut stdout: Box<dyn Write + Send>, out: Arc<OutQueue>, tx: SyncSender<Input>) {
    thread::Builder::new()
        .name("stdout-writer".into())
        .spawn(move || {
            loop {
                match out.pop(Duration::from_millis(100)) {
                    Pop::Frame(payload) => {
                        trace!("out {}", summarize_frame(&payload));
                        let result = write_frame(&mut stdout, &payload);
                        out.finish_write();
                        if result.is_err() {
                            let _ = tx.try_send(Input::StdoutClosed);
                            out.close();
                            return;
                        }
                    }
                    Pop::Timeout => {}
                    Pop::Closed => return,
                }
            }
            // `stdout` is dropped here, closing the pipe so Chrome observes EOF.
        })
        .expect("spawn stdout writer");
}

/// Runs the host until it has fully released every resource; returns the process exit code.
pub fn run<B, F>(make_backend: F, io: Io, config: Config) -> i32
where
    B: Backend + 'static,
    F: FnOnce(BackendEvents) -> B,
{
    crate::trace::init_from_env();
    let (tx, rx) = mpsc::sync_channel::<Input>(CHANNEL_CAPACITY);
    let overflow = Arc::new(AtomicBool::new(false));
    let events = BackendEvents {
        tx: tx.clone(),
        overflow: overflow.clone(),
    };
    let out = OutQueue::new(MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES);
    let mut core = Core::new(make_backend(events), out.clone(), config);
    spawn_reader(io.stdin, tx.clone());
    spawn_writer(io.stdout, out.clone(), tx);

    let code = loop {
        let now = Instant::now();
        let wait = core.next_deadline().map_or(MAX_TICK, |deadline| {
            deadline.saturating_duration_since(now).min(MAX_TICK)
        });
        let input = match rx.recv_timeout(wait) {
            Ok(input) => input,
            Err(RecvTimeoutError::Timeout) => Input::Tick,
            Err(RecvTimeoutError::Disconnected) => Input::StdinEof,
        };
        let input = if overflow.swap(false, Ordering::SeqCst) {
            Input::BackendOverflow
        } else {
            input
        };
        if let Some(code) = core.handle(input, Instant::now()) {
            break code;
        }
    };
    // Flush what the writer already holds (e.g. `shutdown_complete`), but never wait for stdin.
    out.close();
    let drained = out.wait_drained(Instant::now() + DRAIN_GRACE);
    trace!("exit code={code} stdout_drained={drained}");
    code
}
