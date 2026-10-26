"""Pure regression tests for scripts/poc_background.py -- no device, no Chrome, no waits.

Catches two deterministic bugs found in controller review BEFORE the Windows run:
  1. stopScan reply shape: the host replies Event::ScanState (protocol.rs:449) with
     type == "scan_state" AND status == "stopped"; asserting reply["type"] == "stopped"
     failed on the real connector.
  2. The scheduled JS never stored the connector visibility, so ph_evidence's
     "hidden at send" assertion was guaranteed to fail after the 630 s wait; the
     timer callback must capture document.visibilityState per handle at send start.
  3. The driver read window.pocBackground exactly once at 630 s although the hidden
     timer may fire anywhere in 610..670 s, so the +60000 ms clamping allowance was
     effectively unused: a legitimate in-flight timer would have failed the run.
     read_bg_until now polls boundedly (page clock up to schedule+680000 ms plus a
     wall clock guard) and records result_at_first_read and
     reattachment_may_have_woken_page; absence is never fabricated and no formal
     background acceptance is claimed. Bug 3 is regression-tested here with a fake
     clock -- read/wall/sleep are injected, so the tests never really wait.

Bug 1 is checked on the Python source via ast; bug 2 is checked semantically by
running the SCHEDULE IIFE in node with connector-like stubs: the first otp23.send
flips visibilityState to "visible", so a correct implementation must record
"hidden" for the first handle and "visible" for the second (both per-handle and
captured at send start, not at reply time).

Usage:  python -m unittest discover -s scripts -p "test_poc_background.py" -v
"""

import ast
import base64
import hashlib
import json
import pathlib
import re
import subprocess
import tempfile
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SRC = (HERE / "poc_background.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)
TIMER_MS, WAIT_S, MIN_MS, CLAMP_EXTRA_MS = 610_000, 630, 600_000, 60_000
POLL_BOUND_MS = TIMER_MS + CLAMP_EXTRA_MS + 10000


def fn(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"function {name!r} not found in poc_background.py")


def schedule(source):
    for node in ast.walk(TREE):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == source:
            return node.value.value
    raise AssertionError(f"constant {source!r} not found in poc_background.py")


class StopScanReplyShape(unittest.TestCase):
    def test_reply_checked_via_actual_fields(self):
        body = fn("ph_phones")
        self.assertIsNotNone(re.search(r"get\(['\"]type['\"]\)\s*==\s*['\"]scan_state['\"]", body),
                             "stopScan must check reply type == scan_state (protocol.rs Event::ScanState)")
        self.assertIsNotNone(re.search(r"get\(['\"]status['\"]\)\s*==\s*['\"]stopped['\"]", body),
                             "stopScan must check reply status == stopped")
        self.assertIsNone(re.search(r"get\(['\"]type['\"]\)\s*==\s*['\"]stopped['\"]", body),
                          'stopScan must not assert reply["type"] == "stopped" (real type is "scan_state")')


class ScheduleVisibilityCapture(unittest.TestCase):
    def run_schedule(self):
        data = bytearray((i + 17) & 255 for i in range(4096))
        data[:4] = b"VEL\x01"
        handles = {"phoneA": "handle-A", "phoneB": "handle-B"}
        js = schedule("SCHEDULE") % (base64.b64encode(bytes(data)).decode(), json.dumps(handles), 610000)
        harness = f"""
    const window = {{}}; let vis = "hidden";
    const document = {{ get visibilityState() {{ return vis; }} }};
    let pending = null; let sends = 0;
    const setTimeout = (cb, ms) => {{ pending = {{ cb, ms }}; }};
    const performance = {{ now: () => 610000 }};
    const otp23 = {{ send: async () => {{ sends += 1;
        if (sends === 1) {{ vis = "visible"; }}
        return {{ type: "sent" }}; }} }};
    new Function("window", "document", "performance", "otp23", "setTimeout", {json.dumps(js)})
        (window, document, performance, otp23, setTimeout);
    if (window.pocBackground !== null) throw new Error("pocBackground polluted before the timer fired");
    if (!pending || pending.ms !== 610000) throw new Error(`setTimeout not scheduled at 610000 ms: ${{pending && pending.ms}}`);
    await pending.cb();
    console.log(JSON.stringify(window.pocBackground));
    """
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "poc_schedule.mjs"
            path.write_text(f"const run = async () => {{{harness}}};\nrun().catch(e => {{ console.error(e.message); process.exit(1); }});\n", encoding="utf-8")
            done = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, f"node harness failed: {done.stderr.strip()}")
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_timer_and_replies(self):
        poc = self.run_schedule()
        self.assertEqual(set(poc["replies"]), {"phoneA", "phoneB"}, f"two sent replies required, got {poc['replies']}")
        for name, reply in poc["replies"].items():
            self.assertEqual(reply.get("type"), "sent", f"{name}: {reply}")
            self.assertIsInstance(poc["sent_at"].get(name), (int, float), f"missing sent_at for {name}: {poc['sent_at']}")

    def test_visibility_captured_per_handle_at_send_start(self):
        poc = self.run_schedule()
        at_send = poc.get("visibility_at_send")
        self.assertEqual(at_send.get("phoneA"), "hidden",
                         f"first handle must store visibility AT SEND START (was 'hidden'); got {at_send}")
        self.assertEqual(at_send.get("phoneB"), "visible",
                         f"second handle must store ITS OWN at-send-start visibility after the flip; got {at_send} "
                         f"(single shared or reply-time capture would not be per-handle)")
        self.assertEqual(poc.get("visibility_after"), "visible")


class EvidenceExpectsStoredVisibility(unittest.TestCase):
    def test_ph_evidence_reads_stored_at_send_visibility(self):
        body = fn("ph_evidence")
        self.assertIn("visibility_at_send", body,
                      "ph_evidence must require the per-handle stored at-send-start visibility from window.pocBackground")
        self.assertIn("hidden", body, "ph_evidence must require the stored visibility to be 'hidden'")


class BoundedPollHelper(unittest.TestCase):
    """Fake-clock regression for read_bg_until: no real waiting, injected read/wall/sleep."""
    DUMMY = {"replies": {"phoneA": {"type": "sent"}, "phoneB": {"type": "sent"}}}

    def helper(self):
        namespace = {"time": time}
        exec(compile(fn("read_bg_until"), "<read_bg_until>", "exec"), namespace)
        return namespace["read_bg_until"]

    def scenario(self, reads, box=None):
        reads, box = list(reads), box if box is not None else {"wall": 0.0, "sleeps": [], "reads": 0}

        def read():
            if not reads:
                raise AssertionError("out of scripted reads before the scenario finished")
            box["reads"] += 1
            return reads.pop(0)

        def fake_sleep(seconds):
            box["sleeps"].append(seconds)
            box["wall"] += seconds

        return self.helper()(read, 1000.0, POLL_BOUND_MS, sleep=fake_sleep, wall=lambda: box["wall"]), box

    def test_result_at_first_read(self):
        (result, info), box = self.scenario([(self.DUMMY, 632000.0)])
        self.assertIs(result, self.DUMMY)
        self.assertTrue(info["result_at_first_read"])
        self.assertFalse(info["reattachment_may_have_woken_page"])
        self.assertEqual(info["poll_reads"], 1)
        self.assertEqual(box["sleeps"], [])  # never slept

    def test_polls_within_allowance_when_absent_first(self):
        (result, info), box = self.scenario([(None, 632000.0), (None, 634500.0), (self.DUMMY, 662000.0)])
        self.assertIs(result, self.DUMMY)
        self.assertFalse(info["result_at_first_read"])
        self.assertTrue(info["reattachment_may_have_woken_page"])
        self.assertEqual(info["poll_reads"], 3)
        self.assertEqual(box["sleeps"], [0.25, 0.25])  # bounded, fake clock

    def test_fails_at_page_clock_bound_without_fabrication(self):
        with self.assertRaisesRegex(AssertionError, "bounded hidden-timer allowance"):
            self.scenario([(None, 1000.0 + POLL_BOUND_MS + 1000.0)])

    def test_fails_via_wall_guard_when_page_clock_stalls(self):
        box = {"wall": 0.0, "sleeps": [], "reads": 0}
        with self.assertRaisesRegex(AssertionError, "wall-clock guard"):
            self.scenario([(None, 632000.0)] * 4000, box)
        self.assertLessEqual(box["reads"], 3000)
        self.assertLessEqual(sum(box["sleeps"]), POLL_BOUND_MS / 1000 + 30)  # bounded even with a stalled page clock


class TracePathAbsolute(unittest.TestCase):
    def test_relative_out_in_missing_nested_dir(self):
        import os
        namespace = {"pathlib": pathlib}
        exec(compile(fn("prepare_trace_path"), "<prepare_trace_path>", "exec"), namespace)
        prepare = namespace["prepare_trace_path"]
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp).resolve()
            os.chdir(tmp)
            try:
                trace = prepare("a/b/poc.json")
                self.assertTrue(trace.is_absolute())
                self.assertEqual(trace, tmp / "a" / "b" / "poc.trace.log")
                self.assertTrue(trace.parent.is_dir())
                trace.write_text("old")
                neighbor = trace.parent / "other.trace.log"
                neighbor.write_text("keep")
                self.assertEqual(prepare("a/b/poc.json"), trace)
                self.assertFalse(trace.exists())
                self.assertEqual(neighbor.read_text(), "keep")
            finally:
                os.chdir(old)


class UntouchedContract(unittest.TestCase):
    def test_constants_and_630s_detached_wait(self):
        assigns = {}
        for node in TREE.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigns[target.id] = node.value.value if isinstance(node.value, ast.Constant) else None
                    elif isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple) \
                            and len(target.elts) == len(node.value.elts):
                        for name, value in zip(target.elts, node.value.elts):
                            assigns[name.id] = value.value
        self.assertEqual([assigns[k] for k in ("TIMER_MS", "WAIT_S", "MIN_MS", "CLAMP_EXTRA_MS")],
                         [TIMER_MS, WAIT_S, MIN_MS, CLAMP_EXTRA_MS])
        detach = fn("ph_detach_sleep")
        self.assertIsNotNone(re.search(r"time\.sleep\(st\[['\"]wait_s['\"]\]\)", detach),
                             "detached wait must come from state (diagnostic-aware) with default WAIT_S=630 unchanged")
        self.assertIn("Target.detachFromTarget", detach)
        run_src = fn("run")
        self.assertIn("diag * 1000 if diag else TIMER_MS", run_src)
        self.assertIn("diag + 5 if diag else WAIT_S", run_src)

    def test_ph_evidence_uses_bounded_poll_and_records_caveats(self):
        body = fn("ph_evidence")
        self.assertIn("read_bg_until", body)
        self.assertIsNotNone(re.search(r"st\[['\"]timer_ms['\"]\]\s*\+\s*CLAMP_EXTRA_MS\s*\+\s*10000", body),
                             "poll bound must be timer_ms + CLAMP_EXTRA_MS + 10000")
        self.assertIn("reattachment_may_have_woken_page", fn("run"))


class DiagnosticsForensicsWarmup(unittest.TestCase):
    def test_host_trace_log_wired_into_chrome_run(self):
        run_src = fn("run")
        self.assertIn("prepare_trace_path(args.out)", run_src)
        self.assertIn(".trace.log", fn("prepare_trace_path"))
        self.assertIn("unlink(missing_ok=True)", fn("prepare_trace_path"))
        self.assertIn("VELES_OTP23_TRACE", fn("ph_env"))

    def test_send_replies_keep_safe_fields_never_payload(self):
        js = schedule("SCHEDULE")
        self.assertIn("stage:", js)
        self.assertIn("recoverability:", js)
        self.assertNotIn("record_base64", js)
        evidence = fn("ph_evidence")
        self.assertIn("full safe reply", evidence)
        self.assertIn("send_replies", evidence)
        self.assertIn("message_events", evidence)

    def test_warmup_distinct_seeds_byte_verified_before_schedule(self):
        seeds = [node.value for node in TREE.body
                 if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "WARMUP_SEEDS"]
        self.assertEqual(len(seeds), 1)
        values = [elt.value for elt in seeds[0].elts]
        self.assertEqual(values, [41, 53])
        warmup = fn("ph_warmup")
        self.assertIn("WARMUP_SEEDS", warmup)
        self.assertIn("collect_warmup_response", warmup)
        self.assertIn("setup path broken", warmup)  # send failures still fail fast
        run_src = fn("run")
        self.assertLess(run_src.index("'PH-3'"), run_src.index("'BG-1'"))

    def test_diagnostic_flag_wiring(self):
        self.assertIn("--diagnostic-delay-seconds", SRC)
        self.assertIn("type=positive_int", SRC)
        self.assertIn("def positive_int", SRC)
        run_src = fn("run")
        self.assertIn("diagnostic_short_run", run_src)
        self.assertIn("ten-minute", run_src)


class WarmupAsyncCollect(unittest.TestCase):
    """Fake-clock regression for collect_warmup_response: async notify delayed across multiple polls; no real waiting."""
    TIMEOUT = 12
    HANDLE = "handle-A"

    def helper(self):
        namespace = {"time": time, "hashlib": hashlib, "base64": base64}
        exec(compile(fn("collect_warmup_response"), "<collect_warmup_response>", "exec"), namespace)
        return namespace["collect_warmup_response"]

    def fixture(self):
        data = bytearray((i + 41) & 255 for i in range(4096))
        data[:4] = b"VEL\x01"
        record = bytes(data)
        return record[:3] + b"\x02" + record[4:], base64.b64encode(record[:3] + b"\x02" + record[4:]).decode()

    def event(self, b64, handle="handle-A", etype="message"):
        return {"type": etype, "connection_handle": handle, "record_base64": b64}

    def scenario(self, entries, timeout_s=TIMEOUT):
        _, expected_b64 = self.fixture()
        box = {"wall": 0.0, "sleeps": []}
        sequence = list(entries)

        def read_poll():
            if not sequence:
                raise AssertionError("script exhausted before scenario finished")
            entry = sequence.pop(0)
            if isinstance(entry, dict):
                return entry
            snapshot, events = entry
            return {"snapshot": snapshot, "events": events}

        def fake_sleep(seconds):
            box["sleeps"].append(seconds)
            box["wall"] += seconds

        message, info = self.helper()(read_poll, expected_b64, self.HANDLE, timeout_s, sleep=fake_sleep, clock=lambda: box["wall"])
        return info, box

    def ok_snapshot(self):
        return {"portOpen": True, "disconnect": None}

    def test_delayed_message_across_multiple_polls(self):
        actual_b64 = base64.b64encode(self.fixture()[0]).decode()
        foreign = self.event(base64.b64encode(b"short").decode(), handle="handle-B")
        info, box = self.scenario([(self.ok_snapshot(), []), (self.ok_snapshot(), [foreign]),
                                   (self.ok_snapshot(), [self.event(actual_b64)])])
        self.assertTrue(info["byte_exact"])
        self.assertEqual(info["polls"], 3)
        self.assertEqual(info["notify_wait_s"], 1.0)  # fake clock advanced by the two 0.5 s polls only
        self.assertEqual(box["sleeps"], [0.5, 0.5])
        self.assertEqual(info["sha256"], hashlib.sha256(base64.b64decode(actual_b64)).hexdigest())

    def test_empty_first_drain_is_not_a_failure(self):
        actual_b64 = base64.b64encode(self.fixture()[0]).decode()
        info, box = self.scenario([(self.ok_snapshot(), []), (self.ok_snapshot(), []), (self.ok_snapshot(), [self.event(actual_b64)])])
        self.assertTrue(info["byte_exact"])
        self.assertEqual(info["polls"], 3)
        self.assertEqual(len(box["sleeps"]), 2)

    def test_error_event_fails_fast(self):
        with self.assertRaisesRegex(AssertionError, "error event"):
            self.scenario([(self.ok_snapshot(), [self.event(base64.b64encode(b"x").decode(), etype="error")])])

    def test_duplicate_responses_fail(self):
        actual_b64 = base64.b64encode(self.fixture()[0]).decode()
        with self.assertRaisesRegex(AssertionError, "duplicate"):
            self.scenario([(self.ok_snapshot(), [self.event(actual_b64), self.event(actual_b64)])])

    def test_wrong_bytes_fail(self):
        tampered = base64.b64encode(self.fixture()[0][:-1] + bytes([self.fixture()[0][-1] ^ 1])).decode()
        with self.assertRaisesRegex(AssertionError, "wrong bytes"):
            self.scenario([(self.ok_snapshot(), [self.event(tampered)])])

    def test_disconnect_fails_fast(self):
        with self.assertRaisesRegex(AssertionError, "disconnected"):
            self.scenario([{"snapshot": {"portOpen": False, "disconnect": "gatt closed"}, "events": []}])

    def test_timeout_fails_bounded_without_real_waiting(self):
        with self.assertRaisesRegex(AssertionError, "not delivered within 12 s"):
            self.scenario([(self.ok_snapshot(), [])] * 60)

    def test_timeout_bounded_polls_and_sleeps(self):
        _, expected_b64 = self.fixture()
        box = {"wall": 0.0, "sleeps": [], "reads": 0}
        sequence = [(self.ok_snapshot(), [])] * 60

        def read_poll():
            if not sequence:
                raise AssertionError("script exhausted")
            box["reads"] += 1
            snapshot, events = sequence.pop(0)
            return {"snapshot": snapshot, "events": events}

        def fake_sleep(seconds):
            box["sleeps"].append(seconds)
            box["wall"] += seconds

        with self.assertRaisesRegex(AssertionError, "not delivered within 12 s"):
            self.helper()(read_poll, expected_b64, self.HANDLE, self.TIMEOUT, sleep=fake_sleep, clock=lambda: box["wall"])
        self.assertLessEqual(box["reads"], 25)  # 12 s / 0.5 s cadence + 1
        self.assertLessEqual(sum(box["sleeps"]), self.TIMEOUT + 1)  # fake, never real


class WarmupExpressionSemantics(unittest.TestCase):
    """Semantic Node test for the exact EMITTED warmup send expression -- cannot pass by reading strings."""
    HANDLE = "handle-A"

    def helper(self):
        namespace = {"json": json, "base64": base64}
        exec(compile(fn("warmup_send_expression"), "<warmup_send_expression>", "exec"), namespace)
        return namespace["warmup_send_expression"]

    def build_record(self, seed=41):
        data = bytearray((i + seed) & 255 for i in range(4096))
        data[:4] = b"VEL\x01"
        return bytes(data)

    def eval_expression(self, expression):
        harness = "const otp23 = { send: async (handle, bytes) => ({ sent: true, handle, bytes: Array.from(bytes) }) };\n" \
                  "const out = await (new Function(\"otp23\", \"return (\" + process.argv[2] + \")\")(otp23));\n" \
                  "console.log(JSON.stringify(out));\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "warmup_expr.mjs"
            path.write_text(harness, encoding="utf-8")
            done = subprocess.run(["node", str(path), expression], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, f"node harness failed: {done.stderr.strip()[:400]}")
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_emitted_expression_round_trips_non_ascii_bytes(self):
        record = self.build_record()
        self.assertTrue(any(b > 127 for b in record), "fixture guard: record must contain non-ASCII bytes")
        out = self.eval_expression(self.helper()(self.HANDLE, record))
        self.assertTrue(out["sent"], out)
        self.assertEqual(out["handle"], self.HANDLE)
        self.assertEqual(out["bytes"][:4], [86, 69, 76, 1])  # VEL\x01 prefix intact
        self.assertEqual(bytes(out["bytes"]), record)  # actual byte truth, non-ASCII bytes included

    def test_emitted_expression_binds_map_as_uint8array_from_argument(self):
        expression = self.helper()(self.HANDLE, self.build_record())
        self.assertTrue(expression.endswith('"), c => c.charCodeAt(0)))'),
                        f"decoder map must be the Uint8Array.from argument, not an atob argument: {expression}")

    def test_atob_argument_position_bug_is_reproduced(self):
        record = self.build_record()
        buggy = (f"otp23.send({json.dumps(self.HANDLE)}, Uint8Array.from(atob({json.dumps(base64.b64encode(record).decode())},"
                 f" c => c.charCodeAt(0))))")
        out = self.eval_expression(buggy)
        self.assertNotEqual(bytes(out["bytes"]), record,
                            "regression guard: atob(b64, fn) must mangle the bytes (this is the RED state)")


if __name__ == "__main__":
    unittest.main()