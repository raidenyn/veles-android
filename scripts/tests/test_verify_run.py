"""The runner, the run lock, orphan cleanup and the report (design D9, D10).

Every case injects the clock, the wait, the process API and the git reader; the filesystem
cases use a temporary directory. Nothing sleeps or reads the wall clock, and only `GitStatusTest`
spawns a real command (`git status` in a throwaway fixture repository, to test the flags the real
call passes). Everywhere else `FakeProcesses` is the process API, and the runner's signals, grace
and reaping are recorded there instead of hitting the operating system.

The fake process API's shape proves the TERM/grace/KILL sequence: `FakeProcesses.spawn` hands
back a `FakeProcess` whose `poll` keeps returning None until it is signalled; `signal_group`
records the signal *and* the fake clock's ticks at that moment, so the test can assert the ten
seconds between SIGTERM and SIGKILL, and `FakeProcess.wait` records the reap.
"""
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

import branch_pass  # noqa: E402
import gitfixture  # noqa: E402
import verify  # noqa: E402
from verify import (  # noqa: E402
    NodeResult,
    Outcome,
    RunLock,
    Runner,
    StateLock,
    cleanup_orphans,
    exit_code,
    load_table,
    main,
    prune_runs,
    render,
    run_gate,
    step_cutoff,
    write_last_run,
)
from verifyfake import (  # noqa: E402
    FakeClock,
    FakeFs,
    FakeGit,
    FakeRunner,
    FakeWait,
    make_context,
)

RUN = "run-1"
OTHER_RUN = "run-2"


class TickClock(FakeClock):
    """A fake clock that keeps tenths exact, so a poll boundary is not a float accident."""

    def advance(self, seconds):
        self.value = round(self.value + seconds, 6)
        return self.value


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


class FakeMember:
    """A process in a group that is not its leader: its lifetime does not end the group."""

    def __init__(self, ignore=()):
        self.ignore = set(ignore)
        self.alive = True
        self.signals = []

    def signal(self, sig):
        self.signals.append(sig)
        if not self.alive or sig in self.ignore:
            return
        self.alive = False


class FakeProcess:
    """One fake process-group leader the runner can poll, signal and reap.

    `auto_exit` (the default) makes it finish by itself on the first poll, like a command that
    succeeds; `auto_exit=False` keeps it alive until a signal that is not in `ignore` arrives.
    `members` are the other processes of the group: they keep the group alive after the leader
    exits, and each has its own signals to ignore.

    `zombie` models the reaping rule: with `zombie=True` (the default, a process this runner
    started) a leader that has died but has not been `poll`ed or `wait`ed for is still a member of
    its group, exactly as a real zombie is; `zombie=False` is a group this runner did not start
    (an orphan of a dead run), whose parent reaps it, so a dead leader is gone at once.
    """

    def __init__(self, pid=4242, code=0, output=b"", stderr_output=b"", ignore=(),
                 on_poll=None, auto_exit=True, members=(), zombie=True):
        self.pid = pid
        self.code = code
        self.output = output
        self.stderr_output = stderr_output
        self.ignore = set(ignore)
        self.on_poll = on_poll
        self.auto_exit = auto_exit
        self.members = list(members)
        self.zombie = zombie
        self.running = True
        self.reaped = False
        self.signals = []

    def poll(self):
        if self.on_poll is not None:
            self.on_poll()
        if self.running and self.auto_exit:
            self.running = False
        if self.running:
            return None
        self.reaped = True  # a poll that sees the exit reaps it, as Popen.poll does
        return self.code

    def wait(self):
        self.reaped = True
        self.running = False
        return self.code

    def group_alive(self):
        """The leader or any member still exists: what `killpg(pgid, 0)` would say."""
        if self.running:
            return True
        if self.zombie and not self.reaped:
            return True
        return any(member.alive for member in self.members)

    def signal(self, sig):
        self.signals.append(sig)
        for member in self.members:
            member.signal(sig)
        if not self.running or sig in self.ignore:
            return
        self.running = False
        self.code = -int(sig)


class FakeProcesses:
    """The injected process API: scripted groups, recorded spawns, signals and clock ticks."""

    def __init__(self, spawn=None, known=None, missing=(), clock=None, wait=None):
        self.pending = list(spawn or [])
        self.by_pid = dict((process.pid, process) for process in list(known or []) + self.pending)
        self.missing = set(missing)
        self.clock = clock
        self.wait = wait
        self.spawns = []
        self.signals = []
        self.at = []
        self.alive_checks = []
        self.on_signal = None

    @staticmethod
    def _emit(handle, data):
        if handle is None or data is None or isinstance(handle, int):
            return
        handle.write(data)
        handle.flush()

    def spawn(self, argv, cwd, env, stdout, stderr):
        self.spawns.append({"argv": list(argv), "cwd": cwd, "env": dict(env)})
        if argv and argv[0] in self.missing:
            raise FileNotFoundError(argv[0])
        process = self.pending.pop(0) if self.pending else FakeProcess()
        self.by_pid[process.pid] = process
        self._emit(stdout, process.output)
        if stderr is not subprocess.STDOUT:
            self._emit(stderr, process.stderr_output)
        return process

    def _tick(self):
        return self.clock.now() if self.clock is not None else 0.0

    def signal_group(self, pgid, sig):
        self.signals.append((pgid, sig))
        self.at.append(self._tick())
        process = self.by_pid.get(pgid)
        if process is not None:
            process.signal(sig)
        if self.on_signal is not None:
            self.on_signal(pgid, sig)

    def group_alive(self, pgid):
        self.alive_checks.append(pgid)
        process = self.by_pid.get(pgid)
        return bool(process is not None and process.group_alive())


class RunFixture(unittest.TestCase):
    """A temporary checkout with the `build/verify/` layout of design D10."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.realpath(self._tmp.name)
        self.verify_dir = os.path.join(self.repo, "build", "verify")
        self.runs_dir = os.path.join(self.verify_dir, "runs")
        self.run_dir = os.path.join(self.runs_dir, RUN)
        self.running_path = os.path.join(self.verify_dir, "running.json")
        self.last_run_path = os.path.join(self.verify_dir, "last-run.json")
        self.clock = TickClock(0.0)
        self.wait = FakeWait(self.clock)
        self.lines = []

    def context(self, processes=None, deadline=None, run_dir=None, git=None, files=None,
                runner=None):
        runner = runner or Runner(self.clock, self.wait,
                                  processes or FakeProcesses(clock=self.clock,
                                                             wait=self.wait))
        return make_context(
            repo=self.repo,
            common_dir=os.path.join(self.repo, ".git"),
            git=git or FakeGit(),
            runner=runner,
            clock=self.clock,
            wait=self.wait,
            fs=FakeFs(files=files),
            run_dir=run_dir or self.run_dir,
            deadline=deadline,
            log=self.lines.append,
        )

    def log_path(self, step="web-extension.lint", number=1):
        return os.path.join(self.run_dir, "%s.%d.log" % (step, number))

    def runner(self, processes):
        return Runner(self.clock, self.wait, processes)


class TailTest(RunFixture):
    def test_tail_60_lines_300_chars(self):
        # D9: the last 60 lines of the log, each cut at 300 characters.
        output = b"".join(("%04d-" % number).encode("utf-8") + b"x" * 395 + b"\n"
                          for number in range(1, 501))
        processes = FakeProcesses(spawn=[FakeProcess(pid=700, output=output)],
                                  clock=self.clock, wait=self.wait)
        outcome = self.runner(processes).run(["/bin/true"], self.repo, {"PATH": ""},
                                             self.log_path(), 100.0, False)
        self.assertEqual(outcome.kind, "ok")
        self.assertEqual(len(outcome.tail), 60)
        for line in outcome.tail:
            self.assertEqual(len(line), 300)
        self.assertTrue(outcome.tail[0].startswith("0441-"))
        self.assertTrue(outcome.tail[-1].startswith("0500-"))
        self.assertEqual(outcome.log, self.log_path())


class CutoffTest(RunFixture):
    def test_cutoff_45s_before_deadline(self):
        # D9: min(step limit, deadline - 45 s); no deadline means the step's own limit.
        context = make_context(clock=TickClock(100.0), wait=self.wait, deadline=1000.0)
        self.assertEqual(step_cutoff(context, 600), 700.0)
        self.assertEqual(step_cutoff(context, 1000), 955.0)
        self.assertEqual(step_cutoff(context, 10), 110.0)
        context.deadline = None
        self.assertEqual(step_cutoff(context, 1000), 1100.0)
        context.deadline = 1000.0
        context.clock.value = 956.0
        self.assertTrue(verify._past_cutoff(context))


class TerminateTest(RunFixture):
    def test_term_grace_kill_sequence(self):
        # D9: SIGTERM, a 10-second grace, SIGKILL, reap; the fake ignores SIGTERM on purpose.
        process = FakeProcess(pid=800, ignore={signal.SIGTERM}, auto_exit=False)
        processes = FakeProcesses(spawn=[process], clock=self.clock, wait=self.wait)
        outcome = self.runner(processes).run(["slow"], self.repo, {"PATH": ""},
                                             self.log_path(), 5.0, False)
        self.assertEqual(outcome.kind, "timeout")
        self.assertEqual(process.signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(processes.signals, [(800, signal.SIGTERM), (800, signal.SIGKILL)])
        # The signals were ten seconds apart: the grace, not a second poll at the cutoff.
        self.assertEqual(processes.at, [5.0, 15.0])
        self.assertTrue(process.reaped)
        self.assertEqual(outcome.code, -int(signal.SIGKILL))

    def test_leader_that_honours_term_ends_promptly(self):
        # A leader that dies on SIGTERM must be reaped before the group is judged alive: its
        # zombie would otherwise keep the grace running for ten seconds and earn a SIGKILL.
        process = FakeProcess(pid=809, auto_exit=False)
        processes = FakeProcesses(spawn=[process], clock=self.clock, wait=self.wait)
        outcome = self.runner(processes).run(["sleep", "30"], self.repo, {"PATH": ""},
                                             self.log_path(), 1.0, False)
        self.assertEqual(outcome.kind, "timeout")
        self.assertEqual(outcome.code, -int(signal.SIGTERM))
        self.assertEqual(processes.signals, [(809, signal.SIGTERM)])
        self.assertEqual(processes.at, [1.0])
        self.assertEqual(outcome.elapsed_s, 1.0)  # the cutoff, not cutoff + the grace
        self.assertTrue(process.reaped)
        self.assertIsNone(read_json(self.running_path))


class GroupModelTest(unittest.TestCase):
    def test_fake_distinguishes_running_zombie_and_empty(self):
        # The model the runner tests rely on: a dead but unreaped leader is still a group member.
        member = FakeMember(ignore={signal.SIGTERM})
        process = FakeProcess(pid=1, auto_exit=False, members=[member])
        self.assertTrue(process.group_alive())          # (a) a running leader
        process.signal(signal.SIGTERM)
        self.assertFalse(process.running)
        self.assertTrue(process.group_alive())          # (b) a zombie, plus a live member
        self.assertEqual(process.poll(), -int(signal.SIGTERM))
        self.assertTrue(process.group_alive())          # the member still keeps the group alive
        member.signal(signal.SIGKILL)
        self.assertFalse(process.group_alive())         # (c) an empty group
        orphan = FakeProcess(pid=2, zombie=False)
        orphan.signal(signal.SIGTERM)
        self.assertFalse(orphan.group_alive())          # a group we did not start: its parent reaps


class GroupLifetimeTest(RunFixture):
    def test_leader_exit_keeps_the_group_until_the_kill(self):
        # Important 1: the grace and the SIGKILL belong to the whole group. A leader that exits
        # on SIGTERM must not end the grace while a member ignores it, and the running.json entry
        # stays until the group is confirmed gone.
        member = FakeMember(ignore={signal.SIGTERM})
        process = FakeProcess(pid=810, auto_exit=False, members=[member])
        processes = FakeProcesses(spawn=[process], clock=self.clock, wait=self.wait)
        seen = []
        processes.on_signal = lambda pgid, sig: seen.append((sig, read_json(self.running_path)))
        outcome = self.runner(processes).run(["slow"], self.repo, {"PATH": ""},
                                             self.log_path(), 1.0, False)
        self.assertEqual(outcome.kind, "timeout")
        self.assertEqual(outcome.code, -int(signal.SIGTERM))  # the leader itself died on TERM
        self.assertEqual(processes.signals, [(810, signal.SIGTERM), (810, signal.SIGKILL)])
        self.assertEqual(processes.at, [1.0, 11.0])
        self.assertFalse(member.alive)
        self.assertEqual(member.signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertTrue(process.reaped)
        self.assertEqual([entry[1]["groups"] for entry in seen],
                         [[{"run": RUN, "pgid": 810}], [{"run": RUN, "pgid": 810}]])
        self.assertIsNone(read_json(self.running_path))

    def test_surviving_member_keeps_the_running_entry(self):
        # D9: an entry is forgotten only once the group is confirmed gone; a member that survives
        # the SIGKILL keeps the record so the next lock holder can clean it up.
        member = FakeMember(ignore={signal.SIGTERM, signal.SIGKILL})
        process = FakeProcess(pid=811, auto_exit=False, members=[member])
        processes = FakeProcesses(spawn=[process], clock=self.clock, wait=self.wait)
        outcome = self.runner(processes).run(["slow"], self.repo, {"PATH": ""},
                                             self.log_path(), 1.0, False)
        self.assertEqual(outcome.kind, "timeout")
        self.assertEqual(processes.signals, [(811, signal.SIGTERM), (811, signal.SIGKILL)])
        self.assertTrue(member.alive)
        self.assertEqual(read_json(self.running_path)["groups"],
                         [{"run": RUN, "pgid": 811}])

    def test_normal_exit_with_a_live_member_keeps_the_entry(self):
        # The same rule after a normal exit: the leader is reaped but the group is not gone.
        member = FakeMember()
        process = FakeProcess(pid=812, members=[member])
        processes = FakeProcesses(spawn=[process], clock=self.clock, wait=self.wait)
        outcome = self.runner(processes).run(["/bin/true"], self.repo, {"PATH": ""},
                                             self.log_path(), 100.0, False)
        self.assertEqual(outcome.kind, "ok")
        self.assertEqual(outcome.code, 0)
        self.assertTrue(member.alive)
        self.assertEqual(read_json(self.running_path)["groups"],
                         [{"run": RUN, "pgid": 812}])


class SpawnTest(RunFixture):
    def test_capture_separates_stdout_and_stderr(self):
        # The Task-6 contract: with capture=True both streams come back separately and in the log.
        process = FakeProcess(pid=778, output=b"1.2.3\n",
                              stderr_output=b'openjdk version "21.0.12"\n')
        processes = FakeProcesses(spawn=[process], clock=self.clock, wait=self.wait)
        outcome = self.runner(processes).run(["java", "-version"], self.repo, {"PATH": ""},
                                             self.log_path("java"), 100.0, True)
        self.assertEqual(outcome.kind, "ok")
        self.assertEqual(outcome.stdout, ["1.2.3"])
        self.assertEqual(outcome.stderr, ['openjdk version "21.0.12"'])
        with open(self.log_path("java"), "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("1.2.3", text)
        self.assertIn('openjdk version "21.0.12"', text)
        self.assertLess(text.index("1.2.3"), text.index("openjdk"))

    def test_spawn_failure_is_not_run(self):
        processes = FakeProcesses(missing={"/nope"}, clock=self.clock, wait=self.wait)
        outcome = self.runner(processes).run(["/nope", "arg"], self.repo, {"PATH": ""},
                                             self.log_path("nope"), 100.0, False)
        self.assertEqual(outcome.kind, "not_run")
        self.assertIn("/nope", outcome.error)
        self.assertIsNone(read_json(self.running_path))


class RunningJsonTest(RunFixture):
    def test_running_json_recorded_and_cleared(self):
        # D9: the group is in running.json under the run id while it runs, gone once reaped.
        seen = []
        process = FakeProcess(pid=777, on_poll=lambda: seen.append(read_json(self.running_path)))
        processes = FakeProcesses(spawn=[process], clock=self.clock, wait=self.wait)
        outcome = self.runner(processes).run(["/bin/true"], self.repo, {"PATH": ""},
                                             self.log_path(), 100.0, False)
        self.assertEqual(outcome.kind, "ok")
        self.assertEqual(seen[0], {"version": 1, "groups": [{"run": RUN, "pgid": 777}]})
        self.assertIsNone(read_json(self.running_path))


class LockTest(RunFixture):
    def test_lock_tried_once(self):
        # D9: the run lock is a non-blocking flock tried exactly once; a held lock is contention.
        path = os.path.join(self.verify_dir, "lock")
        first = RunLock(path)
        self.assertTrue(first.try_acquire())
        self.assertIsNotNone(first.fd)
        second = RunLock(path)
        self.assertFalse(second.try_acquire())
        self.assertIsNone(second.fd)
        first.release()
        self.assertIsNone(first.fd)
        self.assertTrue(second.try_acquire())
        second.release()
        with mock.patch.object(verify, "_lock_fd", side_effect=OSError("busy")) as attempt:
            self.assertFalse(RunLock(path).try_acquire())
        self.assertEqual(attempt.call_count, 1)


class OrphanTest(RunFixture):
    def record(self, *groups):
        write_json(self.running_path, {"version": 1, "groups": [
            {"run": run, "pgid": pgid} for run, pgid in groups]})

    def test_orphans_killed_and_confirmed(self):
        # D9: a group recorded under another run id gets SIGTERM, then SIGKILL after the grace,
        # and is confirmed gone with killpg(pgid, 0) before the entries are cleared.
        orphan = FakeProcess(pid=5001, ignore={signal.SIGTERM}, auto_exit=False, zombie=False)
        own = FakeProcess(pid=5002, auto_exit=False, zombie=False)
        self.record((OTHER_RUN, 5001), (RUN, 5002))
        processes = FakeProcesses(known=[orphan, own], clock=self.clock, wait=self.wait)
        context = self.context(processes=processes)
        self.assertTrue(cleanup_orphans(context, RUN))
        self.assertEqual(processes.signals,
                         [(5001, signal.SIGTERM), (5001, signal.SIGKILL)])
        self.assertEqual(processes.at, [0.0, 10.0])
        self.assertIn(5001, processes.alive_checks)
        self.assertEqual(own.signals, [])
        self.assertEqual(read_json(self.running_path)["groups"],
                         [{"run": RUN, "pgid": 5002}])

    def test_orphan_alive_after_15s_is_contention(self):
        # D9: a group still alive after 15 seconds makes this run contention; entries stay.
        orphan = FakeProcess(pid=5003, ignore={signal.SIGTERM, signal.SIGKILL},
                             auto_exit=False, zombie=False)
        self.record((OTHER_RUN, 5003))
        processes = FakeProcesses(known=[orphan], clock=self.clock, wait=self.wait)
        context = self.context(processes=processes)
        self.assertFalse(cleanup_orphans(context, RUN))
        self.assertEqual(self.clock.now(), 15.0)
        self.assertEqual(processes.signals,
                         [(5003, signal.SIGTERM), (5003, signal.SIGKILL)])
        self.assertEqual(read_json(self.running_path)["groups"],
                         [{"run": OTHER_RUN, "pgid": 5003}])


class StateLockTest(RunFixture):
    def test_state_lock_retry_10s(self):
        # D9: a state lock is retried through the injected wait for at most 10 seconds and never
        # past the run's deadline; failing that is the caller's internal error.
        path = os.path.join(self.verify_dir, "hook-state.json.lock")
        holder = RunLock(path)
        self.assertTrue(holder.try_acquire())
        self.addCleanup(holder.release)
        context = self.context()
        self.assertFalse(StateLock(path, context).acquire())
        self.assertAlmostEqual(sum(self.wait.slept), 10.0, places=6)
        self.assertEqual(self.clock.now(), 10.0)
        self.clock.value = 0.0
        del self.wait.slept[:]
        context.deadline = 4.0
        self.assertFalse(StateLock(path, context).acquire())
        self.assertAlmostEqual(sum(self.wait.slept), 4.0, places=6)
        self.assertEqual(self.clock.now(), 4.0)
        holder.release()
        lock = StateLock(path, context)
        self.assertTrue(lock.acquire())
        lock.release()


def failure(step_id, outcome, rerun):
    return (step_id, outcome, rerun)


class ReportTest(RunFixture):
    def test_failure_summary(self):
        # D10/spec "Failure summary": the last 60 lines, the exact re-run command, the log, exit 1.
        log = self.log_path()
        tail = ["npm error line %d" % number for number in range(1, 501)]
        results = {"web-extension": NodeResult("FAIL", None, [], [
            failure("web-extension.lint",
                    Outcome("fail", code=1, tail=tail, log=log),
                    "cd web-extension && npm run lint")])}
        text = render(results, [])
        self.assertIn("web-extension: FAIL", text)
        self.assertIn("web-extension.lint: FAIL (exit 1)", text)
        self.assertIn("cd web-extension && npm run lint", text)
        self.assertIn(log, text)
        self.assertIn("npm error line 500", text)
        self.assertIn("npm error line 441", text)
        self.assertNotIn("npm error line 440", text)
        self.assertEqual(exit_code(results, []), 1)

    def test_failure_summary_trims_a_long_tail(self):
        # Important 2: one failure with 60 × 300-character lines must keep the step, the re-run
        # command, the log path and the newest tail line inside the 8 KiB cap.
        tail = [("%03d " % number) + "y" * 296 for number in range(1, 61)]
        log = self.log_path()
        results = {"web-extension": NodeResult("FAIL", None, [], [
            failure("web-extension.lint",
                    Outcome("fail", code=1, tail=tail, log=log),
                    "cd web-extension && npm run lint")])}
        text = render(results, [])
        self.assertLessEqual(len(text.encode("utf-8")), 8192)
        self.assertIn("web-extension.lint: FAIL (exit 1)", text)
        self.assertIn("cd web-extension && npm run lint", text)
        self.assertIn(log, text)
        self.assertIn(tail[-1], text)
        self.assertNotIn(tail[0], text)
        self.assertTrue(any("build/verify/last-run.json" in line
                            for line in text.splitlines()))
        self.assertEqual(exit_code(results, []), 1)

    def test_summary_overflow_keeps_status_lines(self):
        # D10/spec "Summary overflow": at most 8 KiB, every status line, and the pointer.
        nodes = ["android", "web-extension", "rust"]
        results = {}
        for node in nodes:
            tail = [("%s-%03d " % (node, number)) + "x" * 280 for number in range(1, 61)]
            results[node] = NodeResult("FAIL", None, [], [
                failure("%s.lint" % node,
                        Outcome("fail", code=1, tail=tail, log=self.log_path("%s.lint" % node)),
                        "cd %s && ./gradlew lint" % node)])
        text = render(results, [])
        self.assertLessEqual(len(text.encode("utf-8")), 8192)
        for node in nodes:
            self.assertIn("%s: FAIL" % node, text)
        self.assertTrue(any("build/verify/last-run.json" in line
                            for line in text.splitlines()))
        self.assertEqual(exit_code(results, []), 1)

    def test_summary_overflow_from_notes_keeps_status_lines_and_pointer(self):
        # Phase A: the "tests: none related" note lists every changed path, and on a wide branch
        # the notes alone pass 8 KiB. Every node keeps its status line, the missing-tool fix still
        # prints, and the summary stays at most 8 KiB and points at last-run.json.
        note = "tests: none related (%s)" % " ".join(
            "some/deep/path/file_%03d.py" % number for number in range(1, 151))
        fix = "run `./gradlew installCargoDeny` and put its bin directory on PATH"
        outcome = Outcome("not_run", fix=fix, log=self.log_path("supply-chain.supply-chain"),
                          error="path-tool: cargo-deny is required (any version)\nfix: " + fix)
        results = {
            "android": NodeResult("PASS", None, [note], []),
            "web-extension": NodeResult("PASS", None, [note], []),
            "supply-chain": NodeResult("NOT RUN", "cargo-deny is required", [], [
                failure("supply-chain.supply-chain", outcome,
                        "cd . && verify/verify-supply-chain.sh")]),
        }
        text = render(results, [])
        self.assertLessEqual(len(text.encode("utf-8")), 8192)
        lines = text.splitlines()
        for node in ("android", "web-extension", "supply-chain"):
            self.assertTrue(any(line.startswith("%s:" % node) for line in lines), node)
        self.assertIn(fix, text)
        self.assertTrue(any("build/verify/last-run.json" in line for line in lines))
        self.assertEqual(exit_code(results, []), 2)

    def test_failure_details_win_the_budget_over_notes(self):
        # Important 1: many small notes can fill the cap on their own. Failure details are what the
        # spec requires, so the fix and the re-run command survive and the notes are dropped.
        notes = ["tests: none related (path/%04d/source.py)" % number
                 for number in range(1, 401)]
        fix = "run `./gradlew installCargoDeny` and put its bin directory on PATH"
        results = {
            "android": NodeResult("PASS", None, notes, []),
            "supply-chain": NodeResult("NOT RUN", "cargo-deny is required", [], [
                failure("supply-chain.supply-chain",
                        Outcome("not_run", fix=fix,
                                log=self.log_path("supply-chain.supply-chain"),
                                error="path-tool: cargo-deny is required (any version)\nfix: " + fix),
                        "cd . && verify/verify-supply-chain.sh")]),
            "web-extension": NodeResult("FAIL", None, [], [
                failure("web-extension.lint",
                        Outcome("fail", code=1, tail=["npm error lint"],
                                log=self.log_path("web-extension.lint")),
                        "cd web-extension && npm run lint")]),
        }
        text = render(results, [])
        self.assertLessEqual(len(text.encode("utf-8")), 8192)
        lines = text.splitlines()
        for node in ("android", "supply-chain", "web-extension"):
            self.assertTrue(any(line.startswith("%s:" % node) for line in lines), node)
        self.assertIn(fix, text)
        self.assertIn("cd web-extension && npm run lint", text)
        self.assertTrue(any("build/verify/last-run.json" in line for line in lines))
        self.assertNotIn(notes[-1], text)
        self.assertEqual(exit_code(results, []), 1)

    def test_missing_tool_exit_2(self):
        # D10: an unexpected NOT RUN is exit 2, and its fix is in the summary.
        fix = "install a JDK 21 and run config set java_home"
        outcome = Outcome("not_run", fix=fix, log=self.log_path("android.lint"),
                          error="java-home: .java-version pins 21 (major 21)\nfix: " + fix)
        results = {
            "android": NodeResult("NOT RUN", "no JDK 21", [],
                                  [failure("android.lint", outcome,
                                           "cd android && ./gradlew lintDebug")]),
            "tooling": NodeResult("PASS"),
        }
        text = render(results, [])
        self.assertIn("android: NOT RUN (no JDK 21)", text)
        self.assertIn("android.lint: NOT RUN", text)
        self.assertIn(fix, text)
        self.assertIn(".java-version", text)
        self.assertEqual(exit_code(results, []), 2)

    def test_nothing_planned_not_pass(self):
        reason = "nothing planned: no check and no related test for the changed files"
        results = {"protocol": NodeResult("NOT RUN", reason)}
        text = render(results, [])
        self.assertEqual(text.strip(), "protocol: NOT RUN (%s)" % reason)
        self.assertEqual(exit_code(results, []), 0)

    def test_fail_precedence_over_not_run(self):
        planned = NodeResult("NOT RUN", "nothing planned: no check")
        failed = NodeResult("FAIL", None, [], [failure(
            "web-extension.lint", Outcome("fail", code=1),
            "cd web-extension && npm run lint")])
        self.assertEqual(exit_code({"protocol": planned, "web-extension": failed}, []), 1)
        self.assertEqual(exit_code({"protocol": planned, "web-extension": failed},
                                   ["the base cannot be resolved"]), 1)
        self.assertEqual(exit_code({"protocol": planned}, ["the base cannot be resolved"]), 2)
        timed_out = NodeResult("NOT RUN", "cut off", [], [failure(
            "protocol.python", Outcome("timeout"), "cd protocol && npm test")])
        self.assertEqual(exit_code({"protocol": timed_out}, []), 1)

    def test_runs_pruned_to_10(self):
        # D10: the newest 10 run directories are kept.
        for index in range(12):
            os.makedirs(os.path.join(self.runs_dir, "run-%02d" % index))
        context = self.context(run_dir=os.path.join(self.runs_dir, "run-11"))
        write_last_run(context, {"tooling": NodeResult("PASS")}, [])
        kept = sorted(name for name in os.listdir(self.runs_dir)
                      if os.path.isdir(os.path.join(self.runs_dir, name)))
        self.assertEqual(kept, ["run-%02d" % index for index in range(2, 12)])
        self.assertTrue(os.path.isfile(self.last_run_path))

    def test_last_run_json_shape(self):
        # D10: version 1, the node statuses and one step record with its own elapsed_s.
        context = self.context()
        step = {"id": "web-extension.lint", "status": "fail", "elapsed_s": 1.25,
                "rerun": "cd web-extension && npm run lint", "log": self.log_path()}
        results = {"web-extension": NodeResult("FAIL", None,
                                               ["tests: none related (web-extension/x.js)"], [])}
        write_last_run(context, results, [step])
        document = read_json(self.last_run_path)
        self.assertEqual(document["version"], 1)
        self.assertEqual(document["run"], RUN)
        self.assertEqual(document["nodes"]["web-extension"]["status"], "FAIL")
        self.assertEqual(document["nodes"]["web-extension"]["notes"],
                         ["tests: none related (web-extension/x.js)"])
        self.assertEqual(document["steps"], [step])
        self.assertIsInstance(document["steps"][0]["elapsed_s"], float)

    def test_prune_runs_keeps_ten(self):
        for index in range(11):
            os.makedirs(os.path.join(self.runs_dir, "run-%02d" % index))
        context = self.context(run_dir=os.path.join(self.runs_dir, "run-10"))
        self.assertEqual(prune_runs(context), ["run-00"])
        self.assertEqual(len(os.listdir(self.runs_dir)), 10)


EXT = "web-extension/src/background.ts"
BASE = "a" * 40
HEAD = "c" * 40
DIRTY = " M web-extension/src/background.ts"


def fixture_cmap():
    return {"since": "0" * 40,
            "nodes": {"web-extension": {"paths": ["web-extension/"], "release": False}},
            "global": [], "ci-global": [], "unowned": []}


def fixture_table():
    """One node with two steps, so a run can leave its second step not reached."""
    command = {"kind": "check", "cwd": "web-extension", "reuse": False}
    return {
        "version": 1,
        "requirements": {},
        "producers": [],
        "nodes": {"web-extension": {"commands": [
            dict(command, id="web-extension.lint", run=["npm", "run", "lint"]),
            dict(command, id="web-extension.typecheck", run=["npm", "run", "typecheck"]),
        ]}},
    }


class MutatingRunner(FakeRunner):
    """A fake runner whose `on_call(argv)` can dirty the tree or move HEAD during the run."""

    def __init__(self, on_call=None):
        super().__init__()
        self.on_call = on_call

    def run(self, argv, cwd, env, log_path, cutoff, capture):
        outcome = super().run(argv, cwd, env, log_path, cutoff, capture)
        if self.on_call is not None:
            self.on_call(tuple(argv))
        return outcome


class Stdin:
    """A `sys.stdin` stand-in: the hook reads `sys.stdin.buffer`, as at the real terminal."""

    def __init__(self, data):
        self.buffer = io.BytesIO(data)


class BranchFixture(RunFixture):
    """A one-node table, a seeded branch-pass record and the module's path patched to the run dir.

    `branch_pass` resolves its record through git's worktree root; the run's context reads git
    through the injected `FakeGit`, so the record's path is pinned to this temporary checkout and
    no test touches the repository it runs from.
    """

    def setUp(self):
        super().setUp()
        self.record_path = os.path.join(self.verify_dir, "branch-pass.json")
        patcher = mock.patch.object(branch_pass, "record_path", return_value=self.record_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.makedirs(os.path.join(self.repo, ".github"))
        os.makedirs(os.path.join(self.repo, "scripts"))
        write_json(os.path.join(self.repo, ".github", "components.json"), fixture_cmap())
        write_json(os.path.join(self.repo, "scripts", "verify-commands.json"), fixture_table())
        self.cmap = fixture_cmap()
        self.table = load_table(os.path.join(self.repo, "scripts", "verify-commands.json"),
                                self.cmap)

    def git(self, diff=(EXT,), head=HEAD, porcelain=""):
        return FakeGit(diff=list(diff), base=BASE, head=head, porcelain=porcelain)

    def seed_record(self):
        write_json(self.record_path, {"version": 1, "head": "d" * 40, "base": "e" * 40,
                                      "nodes": ["web-extension"]})
        return self.record_bytes()

    def record_bytes(self):
        try:
            with open(self.record_path, "rb") as handle:
                return handle.read()
        except OSError:
            return None


class GitStatusTest(unittest.TestCase):
    """`Git.status_porcelain` lists untracked files whatever the user's status config says."""

    def test_untracked_file_listed_despite_show_untracked_files_no(self):
        # A branch pass compares this text for completeness (design D2): with the user's
        # `status.showUntrackedFiles=no`, a bare `git status --porcelain` hides an untracked file,
        # so a tree that still has one would look clean.
        repo = gitfixture.make_repo(self)
        repo.git("config", "status.showUntrackedFiles", "no")
        repo.write("scratch.md", "untracked\n")

        status = verify.Git(str(repo.path)).status_porcelain()

        self.assertIn("scratch.md", status)
        self.assertEqual("?? scratch.md\0", status)


class BranchPassRunTest(BranchFixture):
    def test_branch_complete_writes_record(self):
        # Spec "Complete run": exit 0, clean tree, same HEAD -> the record names HEAD, base, nodes.
        context = self.context(git=self.git())
        outcome = run_gate("branch", None, context, self.table, self.cmap)
        self.assertEqual(exit_code(outcome.results, outcome.errors), 0)
        self.assertEqual(read_json(self.record_path),
                         {"version": 1, "head": HEAD, "base": BASE,
                          "nodes": ["web-extension"]})

    def test_branch_nothing_changed_writes_empty_nodes(self):
        # A nothing-changed branch run is still complete, and its selected nodes are the empty list.
        context = self.context(git=self.git(diff=()))
        outcome = run_gate("branch", None, context, self.table, self.cmap)
        self.assertEqual(exit_code(outcome.results, outcome.errors), 0)
        document = read_json(self.record_path)
        self.assertEqual(document["nodes"], [])
        self.assertEqual((document["head"], document["base"]), (HEAD, BASE))

    def test_task_complete_writes_record(self):
        # Spec "Complete run": the manual task gate writes the same record `--branch` writes.
        expected = {"version": 1, "head": HEAD, "base": BASE, "nodes": ["web-extension"]}
        self.assertEqual(main([], self.context(git=self.git())), 0)
        self.assertEqual(read_json(self.record_path), expected)
        # A second run through `run_gate` replaces an older record as well.
        self.seed_record()
        context = self.context(git=self.git())
        outcome = run_gate("task", None, context, self.table, self.cmap)
        self.assertEqual(exit_code(outcome.results, outcome.errors), 0)
        self.assertEqual(read_json(self.record_path), expected)

    def test_task_nothing_changed_writes_empty_nodes(self):
        # A nothing-changed manual task run is still complete, with an empty node list.
        context = self.context(git=self.git(diff=()))
        outcome = run_gate("task", None, context, self.table, self.cmap)
        self.assertEqual(exit_code(outcome.results, outcome.errors), 0)
        document = read_json(self.record_path)
        self.assertEqual(document["nodes"], [])
        self.assertEqual((document["head"], document["base"]), (HEAD, BASE))

    def _incomplete_cases(self):
        # The one incompleteness fact each case carries, built fresh so a subtest cannot leak
        # its mutated git or clock into the next one.
        def reached_reserve(clock):
            def on_call(argv):
                if argv == ("npm", "run", "lint"):
                    clock.value = 1000.0 - 45.0
            return on_call

        def dirty_at_end(git):
            def on_call(argv):
                if argv == ("npm", "run", "lint"):
                    git.porcelain = DIRTY
            return on_call

        def head_changed(git):
            def on_call(argv):
                if argv == ("npm", "run", "lint"):
                    git.head_sha = "f" * 40
            return on_call

        exit_1 = FakeRunner(script={("npm", "run", "lint"): Outcome("fail", code=1)})
        exit_2 = FakeRunner(script={("npm", "run", "lint"): Outcome(
            "not_run", error="node is missing", fix="install node")})
        late = self.git()
        moved = self.git()
        rewritten = self.git()
        cases = [
            ("exit 1", lambda: (self.context(git=self.git(), runner=exit_1), None), 1),
            ("exit 2 unexpected NOT RUN",
             lambda: (self.context(git=self.git(), runner=exit_2), None), 2),
            ("--deadline",
             lambda: (self.context(git=self.git(), deadline=1000.0), None), 0),
            ("--base", lambda: (self.context(git=self.git()), BASE), 0),
            ("not_reached",
             lambda: (self.context(
                 git=late, deadline=1000.0,
                 runner=MutatingRunner(on_call=reached_reserve(self.clock))), None), 0),
            ("dirty at start",
             lambda: (self.context(git=self.git(porcelain=DIRTY)), None), 0),
            ("dirty at end",
             lambda: (self.context(
                 git=moved, runner=MutatingRunner(on_call=dirty_at_end(moved))), None), 0),
            ("HEAD changed",
             lambda: (self.context(
                 git=rewritten, runner=MutatingRunner(on_call=head_changed(rewritten))),
              None), 0),
        ]
        return cases

    def _assert_incomplete_leaves_no_record(self, gate):
        for name, build, expected in self._incomplete_cases():
            with self.subTest(gate=gate, case=name):
                self.clock.value = 0.0
                self.seed_record()
                context, base = build()
                outcome = run_gate(gate, base, context, self.table, self.cmap)
                self.assertEqual(exit_code(outcome.results, outcome.errors), expected)
                self.assertFalse(os.path.exists(self.record_path), name)
                if name == "not_reached":
                    # The not-reached fact on its own: everything else about this run is complete.
                    self.assertTrue(outcome.incomplete)
                    self.assertFalse(verify._branch_pass_complete(
                        outcome, None, None, (HEAD, ""), (HEAD, "")))

    def test_branch_incomplete_no_record(self):
        # Spec "Incomplete run": every incompleteness fact leaves no record, even one that existed.
        self._assert_incomplete_leaves_no_record("branch")

    def test_task_incomplete_no_record(self):
        # Spec "Incomplete run" and "Failing task-gate run drops an old pass": the task gate too.
        self._assert_incomplete_leaves_no_record("task")

    def test_branch_contended_keeps_record(self):
        # Spec "Contended run keeps a pass": no lock, no delete, exit 2.
        content = self.seed_record()
        holder = RunLock(os.path.join(self.verify_dir, "lock"))
        self.assertTrue(holder.try_acquire())
        self.addCleanup(holder.release)
        context = self.context(git=self.git())
        outcome = run_gate("branch", None, context, self.table, self.cmap)
        self.assertTrue(outcome.contended)
        self.assertEqual(exit_code(outcome.results, outcome.errors), 2)
        self.assertEqual(self.record_bytes(), content)

    def test_task_contended_keeps_record(self):
        # Spec "Contended run keeps a pass": no lock, no delete, exit 2, for the task gate too.
        content = self.seed_record()
        holder = RunLock(os.path.join(self.verify_dir, "lock"))
        self.assertTrue(holder.try_acquire())
        self.addCleanup(holder.release)
        context = self.context(git=self.git())
        outcome = run_gate("task", None, context, self.table, self.cmap)
        self.assertTrue(outcome.contended)
        self.assertEqual(exit_code(outcome.results, outcome.errors), 2)
        self.assertEqual(self.record_bytes(), content)

    def test_task_list_keeps_record(self):
        # `--list` plans only: a listing never writes or deletes the record.
        content = self.seed_record()
        self.assertEqual(main(["--list"], self.context(git=self.git())), 0)
        self.assertEqual(self.record_bytes(), content)

    def test_hook_never_touches_record(self):
        # Spec "Hook run leaves the record alone": the hook's task gate never writes or deletes.
        content = self.seed_record()
        event = json.dumps({"session_id": "session-1", "cwd": self.repo}).encode("utf-8")
        # A failing step makes the hook block, which proves the gate really ran.
        failing = FakeRunner(script={("npm", "run", "lint"): Outcome("fail", code=1)})
        out = io.StringIO()
        with mock.patch("sys.stdin", Stdin(event)), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            code = main(["--hook", "claude", "--dispatched", "--deadline", "10000"],
                        self.context(git=self.git(), runner=failing))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["decision"], "block")
        self.assertEqual(self.record_bytes(), content)
        # A passing hook run emits nothing, and its runner calls prove the gate ran again.
        runner = FakeRunner()
        out = io.StringIO()
        with mock.patch("sys.stdin", Stdin(event)), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            code = main(["--hook", "claude", "--dispatched", "--deadline", "10000"],
                        self.context(git=self.git(), runner=runner))
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "")
        self.assertIn(("npm", "run", "lint"),
                      [tuple(call["argv"]) for call in runner.calls])
        self.assertEqual(self.record_bytes(), content)

    def test_task_branch_only_note_writes_record(self):
        # Spec "Task-gate pass with skipped tests": a test with no `related` strategy is a
        # branch-only note, not a failure, so the complete task run still writes the record.
        table = fixture_table()
        table["nodes"]["web-extension"]["commands"].append({
            "kind": "test", "id": "web-extension.integration", "cwd": "web-extension",
            "reuse": False, "run": ["npm", "run", "integration"]})
        table_path = os.path.join(self.repo, "scripts", "verify-commands.json")
        write_json(table_path, table)
        loaded = load_table(table_path, self.cmap)
        self.seed_record()
        context = self.context(git=self.git())
        outcome = run_gate("task", None, context, loaded, self.cmap)
        self.assertEqual(exit_code(outcome.results, outcome.errors), 0)
        self.assertIn(verify.BRANCH_ONLY_NOTE % "web-extension.integration",
                      outcome.results["web-extension"].notes)
        self.assertEqual(read_json(self.record_path),
                         {"version": 1, "head": HEAD, "base": BASE,
                          "nodes": ["web-extension"]})


if __name__ == "__main__":
    unittest.main()
