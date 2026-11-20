"""The Claude Code Stop hook: dispatch, watchdog, admission, bounds and output (design D11/D12).

Every case injects the clock, wait, git reader, runner, filesystem and the launcher's process
spawner; the launcher's fake child uses real pipes but no process, so the selector loop, the
non-blocking event write and the kill times are exercised without sleeping or reading the wall
clock (`.agents/rules/repo-no-time-based-tests.md`).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TESTS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS)
REPO = os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, TESTS)

import verify  # noqa: E402
from test_verify_run import FakeProcess, FakeProcesses  # noqa: E402
from verifyfake import FakeClock, FakeFs, FakeGit, FakeRunner, FakeWait, make_context  # noqa: E402

BASE = "a" * 40
EXT = "web-extension/src/background.ts"
SESSION = "session-1"
EVENT_CAP = 1048576


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def write_json(path, document):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle)


def iso(seconds):
    return verify._iso(seconds)


def state_path(common_dir):
    return os.path.join(common_dir, "veles-verify", "hook-state.json")


def reservation(reservation_id="run-reservation", budget_s=1740, started=0.0, pid=0):
    return {"id": reservation_id, "budget_s": budget_s, "started": iso(started), "pid": pid}


def session_doc(blocks=0, spent_s=0.0, reserved=None, repeat=None, synopsis=None,
                last_write=0.0):
    return {
        "blocks": blocks,
        "spent_s": spent_s,
        "reserved": list(reserved or []),
        "repeat": dict(repeat or {"id": None, "n": 0}),
        "synopsis": synopsis,
        "last_write": iso(last_write),
    }


def write_state(common_dir, sessions):
    write_json(state_path(common_dir), {"v": 1, "sessions": sessions})


def read_sessions(common_dir):
    document = read_json(state_path(common_dir)) or {}
    return document.get("sessions") or {}


class Stdin:
    """A `sys.stdin` stand-in: the hook reads `sys.stdin.buffer`, as at the real terminal."""

    def __init__(self, data):
        self.buffer = io.BytesIO(data)


class EventTest(unittest.TestCase):
    def test_reads_a_valid_event(self):
        event = verify.read_event(io.BytesIO(
            json.dumps({"session_id": "s", "cwd": "/repo"}).encode("utf-8")))
        self.assertEqual(event, {"session_id": "s", "cwd": "/repo"})

    def test_more_than_the_cap_is_none(self):
        data = b"{" + b"x" * EVENT_CAP
        self.assertEqual(len(data), EVENT_CAP + 1)
        self.assertIsNone(verify.read_event(io.BytesIO(data)))

    def test_exactly_the_cap_is_read(self):
        padding = "x" * (EVENT_CAP - len(json.dumps(
            {"session_id": "s", "cwd": "/repo", "padding": ""})))
        data = json.dumps({"session_id": "s", "cwd": "/repo", "padding": padding}).encode()
        self.assertEqual(len(data), EVENT_CAP)
        self.assertEqual(verify.read_event(io.BytesIO(data))["session_id"], "s")

    def test_invalid_json_is_none(self):
        self.assertIsNone(verify.read_event(io.BytesIO(b"{not json")))

    def test_missing_or_empty_fields_are_none(self):
        for event in ({"cwd": "/repo"}, {"session_id": "s"},
                      {"session_id": "", "cwd": "/repo"},
                      {"session_id": "s", "cwd": ""},
                      {"session_id": 4, "cwd": "/repo"}):
            with self.subTest(event=event):
                self.assertIsNone(verify.read_event(io.BytesIO(json.dumps(event).encode())))

    def test_a_non_object_is_none(self):
        self.assertIsNone(verify.read_event(io.BytesIO(b"[]")))


class FakeChild:
    """A fake direct child: real pipes, scripted output, report, stdin reading and lifetime.

    No process is started. `poll` is the only place the fake acts, so bytes arrive exactly while
    the launcher is in its selector loop; `never_exits` keeps it alive until the launcher kills
    it, and `reads_stdin=False` leaves the event pipe full (the pipe buffer is never drained).
    """

    def __init__(self, clock, pid=900, report=None, output=b"", reads_stdin=True,
                 never_exits=False, chunk=16384):
        self.clock = clock
        self.pid = pid
        self.stdin_r, self.stdin_w = os.pipe()
        self.stdout_r, self.stdout_w = os.pipe()
        os.set_blocking(self.stdin_r, False)
        os.set_blocking(self.stdout_w, False)
        self.stdin = os.fdopen(self.stdin_w, "wb", buffering=0)
        self.stdout = os.fdopen(self.stdout_r, "rb", buffering=0)
        self.report = report
        self.report_fd = None
        self.report_value = None
        self.pending_output = output
        self.received = b""
        self.reads_stdin = reads_stdin
        self.never_exits = never_exits
        self.chunk = chunk
        self.running = True
        self.killed = False
        self.reaped = False
        self.exit_code = 0
        self.stdout_open = True
        self.stdin_open = True
        self.argv = []
        self.kwargs = {}
        self.cwd = None

    # -- the Popen surface the launcher uses ------------------------------------------

    def poll(self):
        self._report_once()
        if self.reads_stdin:
            self._drain_stdin()
        self._emit_output()
        if self.never_exits:
            return None
        if self.running and not self.pending_output:
            self.running = False
            self.reaped = True  # a poll that sees the exit reaps it, as Popen.poll does
        return None if self.running else self.exit_code

    def kill(self):
        self.killed = True
        self.running = False

    def wait(self):
        self.reaped = True
        self.running = False
        return self.exit_code

    # -- the fake's behaviour ----------------------------------------------------------

    def _report_once(self):
        fd, self.report_fd = self.report_fd, None
        if fd is None:
            return
        if self.report:
            os.write(fd, self.report)
        _close_quietly(fd)

    def _drain_stdin(self):
        if not self.stdin_open:
            return
        try:
            data = os.read(self.stdin_r, 65536)
        except BlockingIOError:
            return
        except OSError:
            self.stdin_open = False
            return
        if data:
            self.received += data
        else:
            self.stdin_open = False

    def _emit_output(self):
        if not self.pending_output:
            self._close_stdout_write()
            return
        try:
            written = os.write(self.stdout_w, self.pending_output[:self.chunk])
        except BlockingIOError:
            return
        except OSError:
            self.pending_output = b""
            self._close_stdout_write()
            return
        self.pending_output = self.pending_output[written:]
        if not self.pending_output:
            self._close_stdout_write()

    def _close_stdout_write(self):
        if self.stdout_open:
            self.stdout_open = False
            _close_quietly(self.stdout_w)

    def close(self):
        for fd in (self.stdin_r, self.stdout_r, self.stdin_w, self.stdout_w):
            _close_quietly(fd)
        for handle in (self.stdin, self.stdout):
            try:
                handle.close()
            except (OSError, ValueError):
                pass


def _close_quietly(fd):
    try:
        os.close(fd)
    except OSError:
        pass


class LauncherFixture(unittest.TestCase):
    """A launcher, a checkout map and a fake spawner; nothing touches a real process or git."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.repo = os.path.join(self.root, "repo")
        self.common = os.path.join(self.root, ".git")
        self.worktree = os.path.join(self.root, "worktree")
        self.script = os.path.join(self.repo, "scripts", "verify.py")
        self.worktree_script = os.path.join(self.worktree, "scripts", "verify.py")
        self.clock = FakeClock(0.0)
        self.wait = FakeWait(self.clock)
        self.checkouts = {}
        self.children = []
        self.spawn_error = None
        self.report = None
        self.output = b""
        self.reads_stdin = True
        self.never_exits = False
        self.files = {}
        self.lines = []

    def checkout(self, cwd, top, common=None):
        self.checkouts[os.path.realpath(cwd)] = (top, common or self.common)

    def discover(self, cwd):
        return self.checkouts.get(os.path.realpath(cwd))

    def context(self, repo=None):
        return make_context(
            repo=repo or self.repo,
            common_dir=self.common,
            clock=self.clock,
            wait=self.wait,
            fs=FakeFs(files=self.files),
            discover=self.discover,
            log=self.lines.append,
        )

    def spawn(self, argv, **kwargs):
        if self.spawn_error is not None:
            raise self.spawn_error
        child = FakeChild(self.clock, pid=900 + len(self.children), report=self.report,
                          output=self.output, reads_stdin=self.reads_stdin,
                          never_exits=self.never_exits)
        child.argv = list(argv)
        child.kwargs = dict(kwargs)
        child.cwd = kwargs.get("cwd")
        write_fd = kwargs["pass_fds"][0]
        # The child inherits a duplicate of the write end; this one process's copy stays here.
        child.report_fd = os.dup(write_fd)
        child.report_value = write_fd
        self.children.append(child)
        self.addCleanup(child.close)
        return child

    def run_launcher(self, event=None):
        data = json.dumps(event or {"session_id": SESSION, "cwd": self.repo}).encode()
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = verify.launcher(data, self.context(), self.spawn)
        self.stderr = err.getvalue()
        return code, out.getvalue()


class LauncherTest(LauncherFixture):
    def test_same_checkout_spawns_child(self):
        self.files[self.script] = b""
        self.checkout(self.repo, self.repo)
        code, out = self.run_launcher()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.children), 1)
        child = self.children[0]
        self.assertEqual(child.argv[:7], [sys.executable, "-I", self.script, "--hook",
                                          "claude", "--dispatched", "--deadline"])
        self.assertEqual(child.argv[7], "1740.0")
        self.assertEqual(child.cwd, self.repo)
        self.assertTrue(child.kwargs["start_new_session"])
        self.assertIs(child.kwargs["stdin"], subprocess.PIPE)
        self.assertIs(child.kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(out, "")

    def test_report_fd_passed_on_argv(self):
        self.files[self.script] = b""
        self.checkout(self.repo, self.repo)
        self.run_launcher()
        child = self.children[0]
        self.assertEqual(child.argv[8], "--report-fd")
        self.assertEqual(child.argv[9], str(child.report_value))
        self.assertEqual(child.kwargs["pass_fds"], (child.report_value,))
        self.assertEqual(child.argv[3:5], ["--hook", "claude"])

    def test_linked_worktree_runs_its_verifier(self):
        self.files[self.worktree_script] = b""
        self.checkout(self.repo, self.repo)
        self.checkout(self.worktree, self.worktree)
        code, _out = self.run_launcher({"session_id": SESSION, "cwd": self.worktree})
        self.assertEqual(code, 0)
        self.assertEqual(len(self.children), 1)
        child = self.children[0]
        self.assertEqual(child.argv[2], self.worktree_script)
        self.assertEqual(child.cwd, self.worktree)

    def test_separate_clone_silent(self):
        self.files[self.script] = b""
        self.checkout(self.repo, self.repo)
        clone = os.path.join(self.root, "clone")
        self.checkout(clone, clone, common=os.path.join(self.root, "other.git"))
        code, out = self.run_launcher({"session_id": SESSION, "cwd": clone})
        self.assertEqual((code, out, self.stderr), (0, "", ""))
        self.assertEqual(self.children, [])

    def test_non_checkout_silent(self):
        self.checkout(self.repo, self.repo)
        code, out = self.run_launcher({"session_id": SESSION,
                                       "cwd": os.path.join(self.root, "outside")})
        self.assertEqual((code, out, self.stderr), (0, "", ""))
        self.assertEqual(self.children, [])

    def test_missing_verifier_silent(self):
        self.checkout(self.repo, self.repo)
        code, out = self.run_launcher()
        self.assertEqual((code, out, self.stderr), (0, "", ""))
        self.assertEqual(self.children, [])

    def test_spawn_failure_fails_open_with_one_stderr_line(self):
        self.files[self.script] = b""
        self.checkout(self.repo, self.repo)
        self.spawn_error = OSError("cannot spawn")
        code, out = self.run_launcher()
        self.assertEqual((code, out), (0, ""))
        lines = [line for line in self.stderr.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("cannot spawn", lines[0])

    def test_invalid_json_fails_open(self):
        context = self.context()
        with mock.patch("sys.stdin", Stdin(b"{not json")), \
                mock.patch.object(verify.subprocess, "Popen") as popen:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = verify.main(["--hook", "claude"], context)
        self.assertEqual((code, out.getvalue()), (0, ""))
        self.assertFalse(popen.called)

    def test_oversized_stdin_fails_open(self):
        context = self.context()
        with mock.patch("sys.stdin", Stdin(b"{" + b"x" * EVENT_CAP)), \
                mock.patch.object(verify.subprocess, "Popen") as popen:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = verify.main(["--hook", "claude"], context)
        self.assertEqual((code, out.getvalue()), (0, ""))
        self.assertFalse(popen.called)

    def test_hook_missing_session_id_fails_open(self):
        context = self.context()
        event = json.dumps({"cwd": self.repo}).encode()
        with mock.patch("sys.stdin", Stdin(event)), \
                mock.patch.object(verify.subprocess, "Popen") as popen:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = verify.main(["--hook", "claude"], context)
        self.assertEqual((code, out.getvalue()), (0, ""))
        self.assertFalse(popen.called)

    def test_child_never_reports_killed_at_1770(self):
        self.files[self.script] = b""
        self.checkout(self.repo, self.repo)
        self.never_exits = True
        code, out = self.run_launcher()
        child = self.children[0]
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertTrue(child.killed)
        self.assertTrue(child.reaped)
        self.assertGreaterEqual(self.clock.now(), 1770.0)
        self.assertLess(self.clock.now(), 1771.0)

    def test_child_reports_deadline_killed_30s_after(self):
        self.files[self.script] = b""
        self.checkout(self.repo, self.repo)
        self.report = b"100.0\n"
        self.never_exits = True
        code, out = self.run_launcher()
        child = self.children[0]
        self.assertEqual((code, out), (0, ""))
        self.assertTrue(child.killed)
        self.assertTrue(child.reaped)
        self.assertGreaterEqual(self.clock.now(), 130.0)
        self.assertLess(self.clock.now(), 131.0)

    def test_launcher_never_blocks_on_child_output(self):
        self.files[self.script] = b""
        self.checkout(self.repo, self.repo)
        self.report = b"200.0\n"
        self.output = b"".join(("line %04d\n" % number).encode() for number in range(20000))
        code, out = self.run_launcher()
        child = self.children[0]
        self.assertEqual(code, 0)
        self.assertEqual(out.encode(), self.output)
        self.assertFalse(child.killed)
        self.assertTrue(child.reaped)
        # The child exited at once; the launcher never ran to its kill time.
        self.assertLess(self.clock.now(), 5.0)

    def test_child_never_reads_large_event_killed_at_1770(self):
        self.files[self.script] = b""
        self.checkout(self.repo, self.repo)
        self.reads_stdin = False
        self.never_exits = True
        template = json.dumps({"session_id": SESSION, "cwd": self.repo, "padding": ""})
        padding = "x" * (EVENT_CAP - len(template))
        data = json.dumps({"session_id": SESSION, "cwd": self.repo,
                           "padding": padding}).encode()
        self.assertEqual(len(data), EVENT_CAP)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = verify.launcher(data, self.context(), self.spawn)
        child = self.children[0]
        self.assertEqual((code, out.getvalue()), (0, ""))
        self.assertTrue(child.killed)
        self.assertTrue(child.reaped)
        self.assertEqual(child.received, b"")
        self.assertGreaterEqual(self.clock.now(), 1770.0)
        self.assertLess(self.clock.now(), 1771.0)


class StateFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.common = os.path.join(self.root, ".git")
        self.clock = FakeClock(0.0)
        self.wait = FakeWait(self.clock)

    def state(self, deadline=None):
        return verify.HookState(self.common, self.clock, wait=self.wait, deadline=deadline)

    def seed(self, session_id=SESSION, **fields):
        write_state(self.common, {session_id: session_doc(**fields)})

    def sessions(self):
        return read_sessions(self.common)


class AdmissionTest(StateFixture):
    def test_expiry_charged_budget_plus_120(self):
        # D12: a reservation past `started + budget + 120 s` is charged that whole amount.
        self.seed(reserved=[reservation(reservation_id="stale", budget_s=200, started=0.0)])
        self.clock.advance(320.0)
        admission = self.state().admit(SESSION, 10000.0)
        self.assertTrue(admission.ok)
        session = self.sessions()[SESSION]
        self.assertEqual(session["spent_s"], 320.0)
        ids = [entry["id"] for entry in session["reserved"]]
        self.assertNotIn("stale", ids)
        self.assertEqual(len(ids), 1)
        self.assertAlmostEqual(admission.deadline, 320.0 + 1740.0)

    def test_slots_count_live_reservations(self):
        # D12: a new run holds one block slot, so three blocks plus two live runs leave none.
        self.seed(blocks=3)
        first = self.state().admit(SESSION, 100000.0)
        second = self.state().admit(SESSION, 100000.0)
        third = self.state().admit(SESSION, 100000.0)
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertFalse(third.ok)
        self.assertIn("verify:", third.notice or "")
        self.assertEqual(len(self.sessions()[SESSION]["reserved"]), 2)

    def test_budget_three_way_minimum(self):
        # D12: min(1740, the session's time left, the seconds to the passed --deadline).
        fresh = self.state().admit(SESSION, 100000.0)
        self.assertEqual(fresh.deadline - self.clock.now(), 1740.0)
        self.seed(spent_s=2000.0)
        limited = self.state().admit(SESSION, 100000.0)
        self.assertEqual(limited.deadline - self.clock.now(), 1600.0)
        self.seed()
        near = self.state().admit(SESSION, 500.0)
        self.assertEqual(near.deadline - self.clock.now(), 500.0)

    def test_session_limit_notice_with_synopsis(self):
        synopsis = "verify: the task gate found problems; fix them and stop again (attempt 3 of 3)"
        self.seed(blocks=5, synopsis=synopsis)
        admission = self.state().admit(SESSION, 100000.0)
        self.assertFalse(admission.ok)
        self.assertIn(synopsis, admission.notice)
        self.assertEqual(self.sessions()[SESSION]["reserved"], [])
        self.assertEqual(self.sessions()[SESSION]["blocks"], 5)

    def test_less_than_60s_no_run(self):
        self.seed(spent_s=3541.0)
        admission = self.state().admit(SESSION, 100000.0)
        self.assertFalse(admission.ok)
        self.assertEqual(self.sessions()[SESSION]["reserved"], [])

    def test_worktree_move_keeps_allowance(self):
        # A worktree move keeps the common dir, so the session's allowance moves with it.
        self.seed(blocks=4, spent_s=600.0)
        worktree_common = self.common  # both worktrees report the main checkout's `.git`
        admission = verify.HookState(worktree_common, self.clock, wait=self.wait).admit(
            SESSION, 100000.0)
        self.assertTrue(admission.ok)
        self.assertEqual(self.sessions()[SESSION]["blocks"], 4)

    def test_expired_reservation_is_dropped_only_after_the_grace(self):
        self.seed(reserved=[reservation(budget_s=200, started=0.0)])
        self.clock.advance(319.0)
        admission = self.state().admit(SESSION, 100000.0)
        self.assertTrue(admission.ok)
        self.assertEqual(self.sessions()[SESSION]["spent_s"], 0.0)
        self.assertEqual(len(self.sessions()[SESSION]["reserved"]), 2)


class SettlementTest(StateFixture):
    IDENTITY = ("web-extension", "web-extension.lint", "fail")

    def settle(self, outcome, elapsed_s=0.0, identity=None, summary="summary",
               reservation_id="run-reservation"):
        return self.state().settle(SESSION, reservation_id, outcome, elapsed_s,
                                   identity, summary)

    def test_no_work_run_charged_counters_kept(self):
        repeat = {"id": list(self.IDENTITY), "n": 2}
        self.seed(blocks=2, spent_s=10.0, reserved=[reservation()],
                  repeat=repeat, synopsis="old synopsis")
        answer = self.settle("no_work", elapsed_s=3.0)
        self.assertEqual(answer.kind, "silent")
        session = self.sessions()[SESSION]
        self.assertEqual(session["spent_s"], 13.0)
        self.assertEqual(session["blocks"], 2)
        self.assertEqual(session["repeat"], repeat)
        self.assertEqual(session["synopsis"], "old synopsis")
        self.assertEqual(session["reserved"], [])

    def test_internal_error_charged(self):
        self.seed(blocks=2, spent_s=10.0, reserved=[reservation()], synopsis="old")
        answer = self.settle("internal_error", elapsed_s=2.0)
        self.assertEqual(answer.kind, "silent")
        session = self.sessions()[SESSION]
        self.assertEqual(session["spent_s"], 12.0)
        self.assertEqual(session["blocks"], 2)
        self.assertEqual(session["synopsis"], "old")

    def test_clean_resets_all_and_synopsis(self):
        other = reservation(reservation_id="other", budget_s=100, started=0.0)
        self.seed(blocks=2, spent_s=100.0, reserved=[reservation(), other],
                  repeat={"id": list(self.IDENTITY), "n": 2}, synopsis="old")
        answer = self.settle("clean", elapsed_s=5.0)
        self.assertEqual(answer.kind, "silent")
        session = self.sessions()[SESSION]
        self.assertEqual(session["blocks"], 0)
        self.assertEqual(session["spent_s"], 0.0)
        self.assertEqual(session["repeat"], {"id": None, "n": 0})
        self.assertIsNone(session["synopsis"])
        self.assertEqual([entry["id"] for entry in session["reserved"]], ["other"])

    def test_block_charges_and_sets_repeat(self):
        self.seed(blocks=1, spent_s=10.0, reserved=[reservation()])
        answer = self.settle("block", elapsed_s=7.5, identity=self.IDENTITY,
                             summary="web-extension: FAIL")
        self.assertEqual(answer.kind, "block")
        self.assertTrue(answer.text.splitlines()[0].startswith(
            "verify: the task gate found problems; fix them and stop again (attempt 1 of 3)"))
        self.assertIn("web-extension: FAIL", answer.text)
        session = self.sessions()[SESSION]
        self.assertEqual(session["blocks"], 2)
        self.assertEqual(session["spent_s"], 17.5)
        self.assertEqual(session["repeat"], {"id": list(self.IDENTITY), "n": 1})
        self.assertIn("web-extension: FAIL", session["synopsis"])

    def test_contention_cancels_uncharged(self):
        self.seed(blocks=2, spent_s=5.0, reserved=[reservation()])
        answer = self.settle("contention", elapsed_s=10.0)
        self.assertEqual(answer.kind, "notice")
        self.assertEqual(answer.text, "verification already running; nothing was checked")
        session = self.sessions()[SESSION]
        self.assertEqual(session["spent_s"], 5.0)
        self.assertEqual(session["blocks"], 2)
        self.assertEqual(session["reserved"], [])

    def test_incomplete_notice_keeps_counters(self):
        self.seed(blocks=2, spent_s=10.0, reserved=[reservation()], synopsis="old")
        answer = self.settle("incomplete", elapsed_s=4.0,
                             summary="steps: web-extension.unit not reached")
        self.assertEqual(answer.kind, "notice")
        self.assertIn("web-extension.unit", answer.text)
        session = self.sessions()[SESSION]
        self.assertEqual(session["spent_s"], 14.0)
        self.assertEqual(session["blocks"], 2)
        self.assertEqual(session["synopsis"], "old")

    def test_settling_an_expired_reservation_does_not_charge_twice(self):
        # The expiry already charged budget + 120; a late settle must not charge again.
        self.seed(spent_s=100.0)
        self.settle("no_work", elapsed_s=9.0)
        self.assertEqual(self.sessions()[SESSION]["spent_s"], 100.0)


class RepeatTest(StateFixture):
    IDENTITY = ("web-extension", "web-extension.lint", "fail")

    def block_once(self, identity, summary="still red"):
        admission = self.state().admit(SESSION, 100000.0)
        self.assertTrue(admission.ok)
        return self.state().settle(SESSION, admission.reservation, "block", 1.0,
                                   identity, summary)

    def test_fourth_repeat_passes(self):
        answers = [self.block_once(self.IDENTITY) for _ in range(4)]
        self.assertEqual([answer.kind for answer in answers],
                         ["block", "block", "block", "notice"])
        self.assertIn("attempt 4 of 3", answers[3].text)
        session = self.sessions()[SESSION]
        self.assertEqual(session["blocks"], 3)
        self.assertEqual(session["repeat"], {"id": list(self.IDENTITY), "n": 4})

    def test_timeout_after_failures_is_new_problem(self):
        for _ in range(3):
            self.block_once(self.IDENTITY)
        answer = self.block_once(("web-extension", "web-extension.lint", "timeout"))
        self.assertEqual(answer.kind, "block")
        session = self.sessions()[SESSION]
        self.assertEqual(session["blocks"], 4)
        self.assertEqual(session["repeat"], {
            "id": ["web-extension", "web-extension.lint", "timeout"], "n": 1})

    def test_selection_error_identity(self):
        first = verify.SelectionError("unaccounted", paths=["b", "a"]).identity
        second = verify.SelectionError("unaccounted", paths=["a", "b"]).identity
        other = verify.SelectionError("unaccounted", paths=["c"]).identity
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(first[0], "selection")
        self.assertEqual(first[1], "unaccounted")
        self.assertEqual(verify.SelectionError("base", base="origin/master").identity,
                         ("selection", "base", "origin/master"))

    def test_prune_keeps_live(self):
        sessions = {}
        live = session_doc(reserved=[reservation(reservation_id="live", budget_s=100, started=0.0)],
                           last_write=0.0)
        sessions["live"] = live
        for number in range(51):
            sessions["s%02d" % number] = session_doc(last_write=1.0 + number)
        write_state(self.common, sessions)
        admission = self.state().admit("new", 100000.0)
        self.assertTrue(admission.ok)
        stored = self.sessions()
        self.assertIn("live", stored)
        self.assertIn("new", stored)
        self.assertNotIn("s00", stored)
        self.assertEqual(len(stored), 52)


class GateFixture(unittest.TestCase):
    """The real table, the real gate and a fake machine, all in a temporary checkout."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = os.path.realpath(temporary.name)
        self.repo = os.path.join(self.root, "repo")
        self.common = os.path.join(self.root, ".git")
        self.verify_dir = os.path.join(self.repo, "build", "verify")
        self.run_dir = os.path.join(self.verify_dir, "runs", "run-1")
        os.makedirs(os.path.join(self.repo, "scripts"))
        os.makedirs(os.path.join(self.repo, ".github"))
        os.makedirs(self.run_dir)
        self.clock = FakeClock(0.0)
        self.wait = FakeWait(self.clock)
        self.lines = []
        self.extra_files = {}
        self.table_commands = []

    def command(self, step_id, run, **extra):
        record = {"id": step_id, "kind": "check", "cwd": ".", "doc": "scripts/AGENTS.md",
                  "reuse": False, "run": list(run)}
        record.update(extra)
        return record

    def write_table(self, commands=None, requirements=None, node="web-extension",
                    paths=None):
        commands = commands if commands is not None else self.table_commands
        cmap = {
            "since": "0" * 40,
            "nodes": {node: {"paths": paths or ["web-extension/"], "release": False}},
            "global": [],
            "ci-global": [],
            "unowned": [],
        }
        write_json(os.path.join(self.repo, ".github", "components.json"), cmap)
        table = {"version": 1, "requirements": requirements or {}, "producers": [],
                 "nodes": {node: {"commands": commands}}}
        write_json(os.path.join(self.repo, "scripts", "verify-commands.json"), table)

    def files(self):
        files = {os.path.join(self.repo, EXT): b""}
        files.update(self.extra_files)
        return files

    def context(self, runner=None, git=None, deadline=None):
        return make_context(
            repo=self.repo,
            common_dir=self.common,
            git=git or FakeGit(diff=[EXT], base=BASE),
            runner=runner or HookRunner(self.clock),
            clock=self.clock,
            wait=self.wait,
            fs=FakeFs(files=self.files()),
            env={"PATH": "/usr/bin"},
            run_dir=self.run_dir,
            deadline=deadline,
            log=self.lines.append,
        )

    def call_hook(self, ctx, argv=None, event=None):
        data = json.dumps(event or {"session_id": SESSION, "cwd": self.repo}).encode()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", Stdin(data)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = verify.main(argv or ["--hook", "claude", "--dispatched",
                                        "--deadline", "10000"], ctx)
        return code, out.getvalue(), err.getvalue()

    def sessions(self):
        return read_sessions(self.common)

    def last_run(self):
        return read_json(os.path.join(self.verify_dir, "last-run.json"))


class HookRunner(FakeRunner):
    """A fake runner that can advance the injected clock on a chosen call."""

    def __init__(self, clock, script=None, on_call=None):
        super().__init__(script)
        self.clock = clock
        self.on_call = on_call

    def run(self, argv, cwd, env, log_path, cutoff, capture):
        outcome = super().run(argv, cwd, env, log_path, cutoff, capture)
        if self.on_call is not None:
            replacement = self.on_call(self.clock, tuple(argv), outcome, cutoff, log_path)
            if replacement is not None:
                outcome = replacement
        return outcome


class HookEndToEndTest(GateFixture):
    def test_lint_failure_blocks(self):
        self.write_table([self.command("web-extension.lint", ["npm", "run", "lint"])])
        log = os.path.join(self.run_dir, "web-extension.lint.1.log")
        runner = HookRunner(self.clock, script={
            ("npm", "run", "lint"): verify.Outcome(
                "fail", code=1, tail=["eslint: no-unused-vars"], log=log),
        })
        code, out, err = self.call_hook(self.context(runner=runner))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        document = json.loads(out)
        self.assertEqual(document["decision"], "block")
        reason = document["reason"]
        self.assertTrue(reason.splitlines()[0].startswith(
            "verify: the task gate found problems; fix them and stop again (attempt 1 of 3)"))
        self.assertIn("web-extension.lint: FAIL (exit 1)", reason)
        self.assertIn("eslint: no-unused-vars", reason)
        self.assertIn("cd . && npm run lint", reason)
        self.assertIn(log, reason)

    def test_missing_tool_blocks_with_fix(self):
        fix = "run `npm ci` in web-extension"
        self.write_table(
            [self.command("web-extension.lint", ["npm", "run", "lint"],
                          requires=["web-extension.node_modules"])],
            requirements={"web-extension.node_modules": {
                "kind": "path", "path": "web-extension/node_modules", "fix": fix}})
        code, out, _err = self.call_hook(self.context())
        self.assertEqual(code, 0)
        document = json.loads(out)
        self.assertEqual(document["decision"], "block")
        self.assertIn("npm ci", document["reason"])

    def test_command_exceeds_budget(self):
        deadline = 1000.0
        self.write_table([
            self.command("web-extension.first", ["first"]),
            self.command("web-extension.slow", ["slow"]),
            self.command("web-extension.after", ["after"]),
        ])
        log = os.path.join(self.run_dir, "web-extension.slow.1.log")

        def on_call(clock, argv, outcome, cutoff, log_path):
            if argv == ("slow",):
                clock.value = deadline - 45.0 + 1.0
                return verify.Outcome("timeout", tail=["still running"], log=log)
            return None

        runner = HookRunner(self.clock, on_call=on_call)
        code, out, _err = self.call_hook(
            self.context(runner=runner), ["--hook", "claude", "--dispatched",
                                          "--deadline", str(deadline)])
        self.assertEqual(code, 0)
        reason = json.loads(out)["reason"]
        self.assertIn("web-extension.slow: TIMEOUT", reason)
        self.assertIn("still running", reason)
        self.assertIn("web-extension.first OK", reason)
        self.assertIn("web-extension.after not reached", reason)

    def test_deadline_between_steps_does_not_block(self):
        deadline = 100.0
        self.write_table([
            self.command("web-extension.first", ["first"]),
            self.command("web-extension.after", ["after"]),
        ])
        write_state(self.common, {SESSION: session_doc(
            blocks=2, spent_s=10.0, repeat={"id": ["x"], "n": 2}, synopsis="kept")})

        def on_call(clock, argv, outcome, cutoff, log_path):
            if argv == ("first",):
                clock.value = deadline - 45.0 + 1.0
            return None

        code, out, _err = self.call_hook(
            self.context(runner=HookRunner(self.clock, on_call=on_call)),
            ["--hook", "claude", "--dispatched", "--deadline", str(deadline)])
        self.assertEqual(code, 0)
        document = json.loads(out)
        self.assertIn("systemMessage", document)
        self.assertIn("web-extension.after not reached", document["systemMessage"])
        session = self.sessions()[SESSION]
        self.assertEqual(session["blocks"], 2)
        self.assertEqual(session["repeat"], {"id": ["x"], "n": 2})
        self.assertEqual(session["synopsis"], "kept")
        self.assertGreater(session["spent_s"], 10.0)

    def test_blocking_wins_over_incomplete(self):
        deadline = 100.0
        self.write_table([
            self.command("web-extension.first", ["first"]),
            self.command("web-extension.slow", ["slow"]),
            self.command("web-extension.after", ["after"]),
        ])

        def on_call(clock, argv, outcome, cutoff, log_path):
            if argv == ("slow",):
                clock.value = deadline - 45.0 + 1.0
                return verify.Outcome("fail", code=1, tail=["boom"])
            return None

        code, out, _err = self.call_hook(
            self.context(runner=HookRunner(self.clock, on_call=on_call)),
            ["--hook", "claude", "--dispatched", "--deadline", str(deadline)])
        self.assertEqual(code, 0)
        document = json.loads(out)
        self.assertEqual(document["decision"], "block")
        self.assertIn("web-extension.slow: FAIL (exit 1)", document["reason"])
        self.assertIn("web-extension.after not reached", document["reason"])

    def test_orphan_cleanup_before_planning(self):
        self.write_table([self.command("web-extension.lint", ["npm", "run", "lint"])])
        write_json(os.path.join(self.verify_dir, "running.json"),
                   {"version": 1, "groups": [{"run": "dead-run", "pgid": 5003}]})
        orphan = FakeProcess(pid=5003, auto_exit=False, zombie=False)
        processes = FakeProcesses(known=[orphan], spawn=[FakeProcess(pid=700)],
                                  clock=self.clock, wait=self.wait)
        runner = verify.Runner(self.clock, self.wait, processes)
        code, out, err = self.call_hook(self.context(runner=runner))
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(processes.signals, [(5003, signal.SIGTERM)])
        self.assertIn(5003, processes.alive_checks)
        self.assertEqual(self.last_run()["nodes"]["web-extension"]["status"], "PASS")

    def test_concurrent_run_notice_no_pass(self):
        self.write_table([self.command("web-extension.lint", ["npm", "run", "lint"])])
        lock = verify.RunLock(os.path.join(self.verify_dir, "lock"))
        self.assertTrue(lock.try_acquire())
        self.addCleanup(lock.release)
        code, out, err = self.call_hook(self.context())
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(json.loads(out),
                         {"systemMessage": "verification already running; nothing was checked"})
        self.assertFalse(os.path.exists(os.path.join(self.verify_dir, "passes.json")))
        self.assertIsNone(read_json(os.path.join(self.verify_dir, "last-run.json")))
        session = self.sessions()[SESSION]
        self.assertEqual(session["spent_s"], 0.0)
        self.assertEqual(session["reserved"], [])


class HookOutputTest(GateFixture):
    """Gate output and settlement driven by crafted gate outcomes (no commands run)."""

    def setUp(self):
        super().setUp()
        self.write_table([self.command("web-extension.lint", ["npm", "run", "lint"])])
        self.selection = verify.Selection(base=BASE, files=[EXT],
                                          reasons={"web-extension": EXT})
        self.ctx = self.context()

    def outcome(self, results, errors=None, problems=None, incomplete=False,
                selection=None):
        return verify.RunOutcome(results=results, errors=list(errors or []),
                                 selection=selection or self.selection,
                                 problems=list(problems or []), incomplete=incomplete)

    def run_with(self, run_outcome, argv=None, event=None):
        with mock.patch.object(verify, "run_gate", return_value=run_outcome):
            return self.call_hook(self.ctx, argv, event)

    def test_stdout_json_only_exit_0(self):
        failed = verify.NodeResult("FAIL", None, [], [
            ("web-extension.lint", verify.Outcome("fail", code=1), "cd . && npm run lint")])
        code, out, _err = self.run_with(self.outcome(
            {"web-extension": failed}, problems=[("web-extension", "web-extension.lint",
                                                  "fail")]))
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_reason_first_line(self):
        failed = verify.NodeResult("FAIL", None, [], [
            ("web-extension.lint", verify.Outcome("fail", code=1), "cd . && npm run lint")])
        _code, out, _err = self.run_with(self.outcome(
            {"web-extension": failed}, problems=[("web-extension", "web-extension.lint",
                                                  "fail")]))
        reason = json.loads(out)["reason"]
        self.assertTrue(reason.splitlines()[0].startswith(
            "verify: the task gate found problems; fix them and stop again (attempt 1 of 3)"))

    def test_internal_error_one_stderr_line(self):
        with mock.patch.object(verify, "run_gate", side_effect=RuntimeError("boom")):
            code, out, err = self.call_hook(self.ctx)
        self.assertEqual((code, out), (0, ""))
        lines = [line for line in err.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("boom", lines[0])

    def test_stop_hook_active_ignored(self):
        # Spec "Hook output contract": `stop_hook_active` MUST NOT be consulted, so a blocking
        # gate still blocks (and prints nothing for an implementation that honours the flag).
        failed = verify.NodeResult("FAIL", None, [], [
            ("web-extension.lint", verify.Outcome("fail", code=1), "cd . && npm run lint")])
        code, out, _err = self.run_with(
            self.outcome({"web-extension": failed},
                         problems=[("web-extension", "web-extension.lint", "fail")]),
            event={"session_id": SESSION, "cwd": self.repo, "stop_hook_active": True})
        self.assertEqual(code, 0)
        document = json.loads(out)
        self.assertEqual(document["decision"], "block")
        self.assertIn("web-extension.lint: FAIL (exit 1)", document["reason"])

    def test_all_remembered_stop_silent(self):
        # Spec "Unchanged node is skipped": every selected node remembered is a silent clean pass.
        remembered = verify.NodeResult("PASS (remembered)")
        write_state(self.common, {SESSION: session_doc(
            blocks=2, spent_s=100.0, repeat={"id": ["x"], "n": 2}, synopsis="old")})
        code, out, _err = self.run_with(self.outcome({"web-extension": remembered}))
        self.assertEqual((code, out), (0, ""))
        session = self.sessions()[SESSION]
        self.assertEqual(session["blocks"], 0)
        self.assertEqual(session["spent_s"], 0.0)
        self.assertEqual(session["repeat"], {"id": None, "n": 0})
        self.assertIsNone(session["synopsis"])

    def test_mixed_block_lists_remembered(self):
        remembered = verify.NodeResult("PASS (remembered)")
        failed = verify.NodeResult("FAIL", None, [], [
            ("android.lint", verify.Outcome("fail", code=1), "cd android && ./gradlew lint")])
        _code, out, _err = self.run_with(self.outcome(
            {"android": failed, "web-extension": remembered},
            problems=[("android", "android.lint", "fail")]))
        reason = json.loads(out)["reason"]
        self.assertIn("web-extension: PASS (remembered)", reason)
        self.assertIn("android.lint: FAIL (exit 1)", reason)

    def test_incomplete_notice_lists_remembered(self):
        # The same formatter as a block: the notice names the remembered node and what was not
        # reached, and does not reset the counters.
        remembered = verify.NodeResult("PASS (remembered)")
        stopped = verify.NodeResult("NOT RUN", verify.NOT_REACHED_REASON)
        outcome = self.outcome({"android": stopped, "web-extension": remembered},
                               incomplete=True)
        outcome.steps = [{"id": "android.lint", "status": "not_reached"}]
        write_state(self.common, {SESSION: session_doc(blocks=2, spent_s=10.0)})
        with mock.patch.object(verify, "run_gate") as run_gate:
            run_gate.side_effect = lambda *args, **kwargs: (
                self.clock.advance(4.0), outcome)[1]
            code, out, _err = self.call_hook(self.ctx)
        self.assertEqual(code, 0)
        notice = json.loads(out)["systemMessage"]
        self.assertIn("web-extension: PASS (remembered)", notice)
        self.assertIn("android.lint not reached", notice)
        session = self.sessions()[SESSION]
        self.assertEqual(session["blocks"], 2)
        self.assertEqual(session["spent_s"], 14.0)

    def test_repeat_bound_notice_lists_remembered(self):
        identity = ("android", "android.lint", "fail")
        write_state(self.common, {SESSION: session_doc(
            blocks=3, spent_s=20.0, repeat={"id": list(identity), "n": 3},
            synopsis="verify: still red\n\nandroid.lint: FAIL")})
        remembered = verify.NodeResult("PASS (remembered)")
        failed = verify.NodeResult("FAIL", None, [], [
            ("android.lint", verify.Outcome("fail", code=1), "cd android && ./gradlew lint")])
        _code, out, _err = self.run_with(self.outcome(
            {"android": failed, "web-extension": remembered},
            problems=[identity]))
        document = json.loads(out)
        self.assertNotIn("decision", document)
        notice = document["systemMessage"]
        self.assertIn("web-extension: PASS (remembered)", notice)
        self.assertIn("attempt 4 of 3", notice)
        self.assertEqual(self.sessions()[SESSION]["blocks"], 3)

    def test_limit_at_admission_does_not_run(self):
        write_state(self.common, {SESSION: session_doc(blocks=5, synopsis="still red")})
        with mock.patch.object(verify, "run_gate") as run_gate:
            code, out, _err = self.call_hook(self.ctx)
        self.assertEqual(code, 0)
        self.assertFalse(run_gate.called)
        self.assertIn("still red", json.loads(out)["systemMessage"])

    def test_nothing_changed_silent_and_charged(self):
        write_state(self.common, {SESSION: session_doc(blocks=2, spent_s=10.0,
                                                       synopsis="old")})
        empty = verify.Selection(base=BASE, files=[], reasons={})
        code, out, _err = self.run_with(self.outcome({}, selection=empty))
        self.assertEqual((code, out), (0, ""))
        session = self.sessions()[SESSION]
        self.assertEqual(session["blocks"], 2)
        self.assertEqual(session["synopsis"], "old")


if __name__ == "__main__":
    unittest.main()
