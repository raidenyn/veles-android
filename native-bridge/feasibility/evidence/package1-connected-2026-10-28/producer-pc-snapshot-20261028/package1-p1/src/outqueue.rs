//! Bounded pending-output queue (OTP2-BRD-030a): at most 128 messages and 256 KiB. The single
//! writer thread is the only consumer; overflow is never absorbed by dropping.

use std::collections::VecDeque;
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

pub const MAX_QUEUED_MESSAGES: usize = 128;
pub const MAX_QUEUED_BYTES: usize = 256 * 1024;

#[derive(Debug, PartialEq, Eq)]
pub struct Overflow;

#[derive(Debug, PartialEq, Eq)]
pub enum Pop {
    Frame(Vec<u8>),
    Timeout,
    Closed,
}

struct Item {
    payload: Vec<u8>,
    request_id: Option<String>,
}

#[derive(Default)]
struct State {
    queue: VecDeque<Item>,
    queued_bytes: usize,
    /// The frame the writer has popped but not yet finished writing: (size, request id).
    writing: Option<(usize, Option<String>)>,
    closed: bool,
}

impl State {
    fn count(&self) -> usize {
        self.queue.len() + usize::from(self.writing.is_some())
    }

    fn bytes(&self) -> usize {
        self.queued_bytes + self.writing.as_ref().map_or(0, |(size, _)| *size)
    }
}

pub struct OutQueue {
    max_messages: usize,
    max_bytes: usize,
    state: Mutex<State>,
    ready: Condvar,
    drained: Condvar,
}

impl OutQueue {
    pub fn new(max_messages: usize, max_bytes: usize) -> Arc<Self> {
        Arc::new(Self {
            max_messages,
            max_bytes,
            state: Mutex::new(State::default()),
            ready: Condvar::new(),
            drained: Condvar::new(),
        })
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, State> {
        self.state.lock().expect("output queue lock")
    }

    /// Enqueues one serialised JSON payload (without the length prefix). `request_id` tags
    /// completions so the id stays reserved until the frame has actually been written.
    pub fn push(&self, payload: Vec<u8>, request_id: Option<String>) -> Result<(), Overflow> {
        let mut state = self.lock();
        if state.closed
            || state.count() + 1 > self.max_messages
            || state.bytes() + payload.len() > self.max_bytes
        {
            return Err(Overflow);
        }
        state.queued_bytes += payload.len();
        state.queue.push_back(Item {
            payload,
            request_id,
        });
        self.ready.notify_one();
        Ok(())
    }

    /// Blocks up to `timeout` for the next frame. The popped frame stays accounted (and its
    /// request id reserved) until [`OutQueue::finish_write`].
    pub fn pop(&self, timeout: Duration) -> Pop {
        let deadline = Instant::now() + timeout;
        let mut state = self.lock();
        loop {
            if let Some(item) = state.queue.pop_front() {
                state.queued_bytes -= item.payload.len();
                state.writing = Some((item.payload.len(), item.request_id));
                return Pop::Frame(item.payload);
            }
            if state.closed {
                return Pop::Closed;
            }
            let now = Instant::now();
            if now >= deadline {
                return Pop::Timeout;
            }
            state = self
                .ready
                .wait_timeout(state, deadline - now)
                .expect("output queue lock")
                .0;
        }
    }

    pub fn finish_write(&self) {
        let mut state = self.lock();
        state.writing = None;
        if state.queue.is_empty() {
            self.drained.notify_all();
        }
    }

    pub fn holds_request_id(&self, request_id: &str) -> bool {
        let state = self.lock();
        state
            .queue
            .iter()
            .any(|i| i.request_id.as_deref() == Some(request_id))
            || state
                .writing
                .as_ref()
                .is_some_and(|(_, id)| id.as_deref() == Some(request_id))
    }

    /// Stops accepting pushes; queued frames can still be popped, then `Pop::Closed`.
    pub fn close(&self) {
        let mut state = self.lock();
        state.closed = true;
        self.ready.notify_all();
    }

    /// Waits until every queued frame has been written (or `deadline`); true when drained.
    pub fn wait_drained(&self, deadline: Instant) -> bool {
        let mut state = self.lock();
        loop {
            if state.queue.is_empty() && state.writing.is_none() {
                return true;
            }
            let now = Instant::now();
            if now >= deadline {
                return false;
            }
            state = self
                .drained
                .wait_timeout(state, deadline - now)
                .expect("output queue lock")
                .0;
        }
    }

    pub fn len(&self) -> usize {
        self.lock().count()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn queued_bytes(&self) -> usize {
        self.lock().bytes()
    }
}
