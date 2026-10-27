"""Pure/mocked tests for scenario_matrix.py and scenario_matrix_core.py (no Chrome, no Bluetooth, no device).

    python3 -m unittest scripts/test_scenario_matrix.py      (from native-bridge/feasibility)
    python3 -m unittest test_scenario_matrix                 (from scripts/)

The direct-process stdin cases run against a tiny fake host script that speaks the real native
framing, so the pipe, EOF and trace logic is exercised for real on any POSIX machine.
"""

import argparse
import base64
import io
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock

import scenario_matrix as sm
import scenario_matrix_core as core

IDENTITY = {"extension_id": "gmekhgkipjkikmaadaajljinkgfielei",
            "origin": "chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/"}


def trace(*lines):
    return "\n".join(f"[{i * 10:>5}ms pid=7] {line}" for i, line in enumerate(lines)) + "\n"


SHUTDOWN_TRACE = trace("start pid=7", "in  len=60 type=shutdown request_id=r9",
                       "out len=70 type=scan_state request_id=r3 status=stopped",
                       "out len=50 type=disconnected connection_handle=aa",
                       "out len=40 type=shutdown_complete request_id=r9",
                       "exit code=0 stdout_drained=true")
EOF_TRACE = trace("start pid=7", "in  len=10 type=hello", "exit code=0 stdout_drained=true")


class Framing(unittest.TestCase):
    def test_roundtrip_and_split_feed(self):
        frame = core.encode_frame({"type": "hello", "n": 1})
        decoder = core.FrameDecoder()
        self.assertEqual(decoder.feed(frame[:3]), [])
        self.assertEqual(decoder.feed(frame[3:]), [{"type": "hello", "n": 1}])
        self.assertEqual(decoder.eof(), "clean")

    def test_truncated_header_and_payload_and_bad_length(self):
        self.assertEqual(self._eof(b"\x05\x00"), "truncated_header")
        self.assertEqual(self._eof(b"\x05\x00\x00\x00ab"), "truncated_payload")
        self.assertEqual(self._eof(b"\x00\x00\x00\x00"), "bad_length")
        self.assertEqual(self._eof(b"\xff\xff\xff\x7f"), "bad_length")

    @staticmethod
    def _eof(data):
        decoder = core.FrameDecoder()
        decoder.feed(data)
        return decoder.eof()

    def test_request_frame_has_hello_versions_and_api_version(self):
        body = json.loads(core.request_frame("hello", "d1")[4:])
        self.assertEqual(body["api_versions"], [1])
        self.assertNotIn("api_version", body)
        body = json.loads(core.request_frame("send", "d2", 1, connection_handle="h")[4:])
        self.assertEqual((body["api_version"], body["connection_handle"]), (1, "h"))


class TraceAndTeardown(unittest.TestCase):
    def test_shutdown_path_ok_and_distinct(self):
        parsed = core.parse_trace(SHUTDOWN_TRACE)
        self.assertEqual(core.trace_mechanism(parsed), "shutdown")
        self.assertEqual(core.check_teardown(parsed, "shutdown"), [])
        self.assertTrue(core.check_teardown(parsed, "disconnect"))  # shutdown must not count as disconnect
        self.assertTrue(core.check_teardown(parsed, "stdin_closed"))

    def test_shutdown_complete_must_be_last_output(self):
        late = trace("in  len=1 type=shutdown", "out len=1 type=shutdown_complete",
                     "out len=1 type=scan_state status=stopped", "exit code=0 stdout_drained=true")
        problems = core.check_teardown(core.parse_trace(late), "shutdown")
        self.assertTrue(any("last" in p for p in problems))

    def test_stdin_closed_path_and_rejects_shutdown_credit(self):
        parsed = core.parse_trace(EOF_TRACE)
        self.assertEqual(core.trace_mechanism(parsed), "stdin_closed")
        self.assertEqual(core.check_teardown(parsed, "stdin_closed", require_drained=True), [])
        self.assertTrue(core.check_teardown(parsed, "shutdown"))

    def test_disconnect_allows_killed_host_without_exit_line_but_records_it(self):
        parsed = core.parse_trace(trace("start pid=7", "in  len=10 type=hello"))
        self.assertEqual(core.trace_mechanism(parsed), "no_exit")
        self.assertEqual(core.check_teardown(parsed, "disconnect"), [])
        self.assertTrue(core.check_teardown(parsed, "stdin_closed"))  # direct EOF requires the exit line

    def test_nonzero_exit_and_undrained_stdout_fail(self):
        bad = core.parse_trace(trace("in  len=1 type=hello", "exit code=1 stdout_drained=false"))
        problems = core.check_teardown(bad, "stdin_closed", require_drained=True)
        self.assertEqual(len(problems), 2)

    def test_truncated_eof_checks(self):
        text = trace("start pid=7", "in  framing violation: TruncatedHeader { got: 2 }",
                     "exit code=2 stdout_drained=true")
        parsed = core.parse_trace(text)
        self.assertEqual(core.check_truncated_eof(parsed, 2, "clean"), [])
        self.assertTrue(core.check_truncated_eof(parsed, 0, "clean"))
        self.assertTrue(core.check_truncated_eof(parsed, 2, "truncated_header"))
        self.assertTrue(core.check_truncated_eof(core.parse_trace(EOF_TRACE), 2, "clean"))


class Selection(unittest.TestCase):
    def devices(self, *labels):
        return [{"label": label, "device_handle": f"h{i}"} for i, label in enumerate(labels)]

    def test_lifecycle_needs_two_phones(self):
        for count in (0, 1):
            pair, status, error_class, _ = core.pick_two(self.devices(*["a"] * count))
            self.assertEqual((pair, status, error_class), (None, "BLOCKED", "insufficient_phones"))
        pair, status, _, _ = core.pick_two(self.devices("a", "b", "c"))
        self.assertEqual((len(pair), status), (2, "PASS"))

    def test_m6_never_silently_picks(self):
        two = self.devices("OnePlus", "Pixel")
        self.assertEqual(core.select_target(two)[1:3], ("BLOCKED", "ambiguous_target"))
        self.assertEqual(core.select_target(two, sole=True)[1:3], ("BLOCKED", "ambiguous_target"))
        self.assertEqual(core.select_target(two, label="x", sole=True)[1], "BLOCKED")
        index, status, _, _ = core.select_target(two, label="Pixel")
        self.assertEqual((index, status), (1, "PASS"))
        self.assertEqual(core.select_target(two, label="missing")[2], "target_not_found")
        self.assertEqual(core.select_target(self.devices("same", "same"), label="same")[2], "ambiguous_target")
        self.assertEqual(core.select_target([], sole=True)[2], "target_not_found")
        self.assertEqual(core.select_target(self.devices("only"), sole=True)[:2], (0, "PASS"))


class Exchanges(unittest.TestCase):
    def test_seeds_are_independent_per_platform_and_run(self):
        win, mac = core.seeds_for("windows", 40), core.seeds_for("mac", 40)
        self.assertEqual(len(set(win)), 40)
        self.assertEqual(len(set(mac)), 40)
        self.assertNotEqual(win[:20], mac[:20])
        self.assertEqual(core.seeds_for("windows", 3, base=5), [5, 18, 31])

    def test_plan_records_are_4096_and_distinct(self):
        plan = core.plan_exchanges("windows", 20)
        self.assertTrue(all(len(p["request"]) == 4096 and p["request"][:4] == b"VEL\x01" for p in plan))
        self.assertEqual(len({p["request_sha256"] for p in plan}), 20)
        self.assertEqual(plan[0]["expected"][3], 2)

    def test_verify_reply_exact_bytes_handle_routing(self):
        item = core.plan_exchanges("mac", 1)[0]
        good = {"type": "message", "connection_handle": "h1",
                "record_base64": base64.b64encode(item["expected"]).decode()}
        self.assertIsNone(core.verify_reply(item, "h1", good))
        self.assertIn("different connection", core.verify_reply(item, "h2", good))
        flipped = dict(good, record_base64=base64.b64encode(item["request"]).decode())
        self.assertIn("differ", core.verify_reply(item, "h1", flipped))
        self.assertIn("no message", core.verify_reply(item, "h1", None))

    def test_stage_document_is_hash_only(self):
        stage = core.stage_document("run1", "windows", "OnePlus 6T", [1, 2], "abc", 5)
        text = json.dumps(stage)
        self.assertEqual(stage["stage"], "READY")
        self.assertEqual(len(stage["plan_sha256"]), 64)
        self.assertNotIn("record", text)


def exchange(index, start, end, seed=None, status="PASS", size=4096):
    seed = index if seed is None else seed
    digest = f"{seed:064x}"
    return {"index": index, "seed": seed, "size": size, "request_sha256": f"{seed + 1000:064x}",
            "expected_sha256": digest, "received_sha256": digest, "start_wall_ms": start, "end_wall_ms": end,
            "status": status}


CLOCK = dict(clock_offset_ms=0, clock_uncertainty_ms=50, clock_provenance="parent: 7-sample midpoint, half-RTT")


def central(role, start=10_000, count=20, step=300, run="RUN1", label="OnePlus 6T", seed_shift=0, nonce="n1",
            mode="nonce-file", **extra):
    result = {"scenario": "m6-multi-central", "role": role, "pair_run_id": run, "phone_label": label,
              "links_on_this_central": 1, "status": "PARTIAL", "target_label_sha256": "aa11",
              "system_errors": [], "unexpected_disconnects": [],
              "barrier": {"mode": mode, "nonce_sha256": core.nonce_hash(nonce) if mode == "nonce-file" else None,
                          "plan_sha256": "p" * 64, "stage_wall_ms": start - 500, "released_at_wall_ms": start - 5},
              "exchanges": [exchange(i, start + i * step, start + i * step + 200, seed=i + seed_shift)
                            for i in range(count)]}
    result.update(extra)
    return result


def adjudicate(first, second, **over):
    options = dict(CLOCK)
    options.update(over)
    return core.adjudicate_m6(first, second, **options)


def failing(verdict):
    return [c["name"] for c in verdict["checks"] if c["status"] == "FAIL"]


def blocked(verdict):
    return [c["name"] for c in verdict["checks"] if c["status"] == "BLOCKED"]


class Adjudicator(unittest.TestCase):
    def pair(self, **over):
        return adjudicate(central("windows"), central("mac", seed_shift=100), **over)

    def test_clean_overlap_is_partial_and_never_discharges_m6(self):
        verdict = self.pair()
        self.assertEqual((verdict["verdict"], verdict["desktop_concurrency"]), ("PARTIAL", "PASS"))
        self.assertFalse(verdict["m6_discharged"])
        self.assertEqual(verdict["requirements"][0]["status"], "BLOCKED")
        self.assertEqual(failing(verdict) + blocked(verdict), [])

    def test_no_escape_hatch_for_notify_codes(self):  # IMPORTANT 7: any "evidence" string used to give PASS
        with self.assertRaises(TypeError):
            adjudicate(central("windows"), central("mac", seed_shift=100), notify_codes_evidence="anything")
        self.assertNotEqual(self.pair()["verdict"], "PASS")

    def test_clock_never_defaults_to_zero(self):  # IMPORTANT 4
        win, mac = central("windows"), central("mac", seed_shift=100)
        for kwargs in ({}, dict(clock_offset_ms=0), dict(clock_offset_ms=0, clock_uncertainty_ms=50),
                       dict(clock_offset_ms=0, clock_uncertainty_ms=50, clock_provenance="")):
            verdict = core.adjudicate_m6(win, mac, **kwargs)
            self.assertEqual(verdict["verdict"], "BLOCKED", kwargs)
            self.assertIn("clock_measured", blocked(verdict))

    def test_uncertainty_tightens_overlap_requirements(self):
        win, mac = central("windows", start=10_000), central("mac", start=13_000, seed_shift=100)
        # windows 10_000..15_900, mac 13_000..18_900: overlap 2900 ms
        self.assertEqual(adjudicate(win, mac, min_overlap_ms=2800, barrier_tolerance_ms=10_000)["verdict"], "PARTIAL")
        wide = adjudicate(win, mac, min_overlap_ms=2800, clock_uncertainty_ms=150, barrier_tolerance_ms=10_000)  # needs 2800 + 300 = 3100
        self.assertIn("real_io_intervals_overlap", failing(wide))
        # pairs overlapping by less than 2*uncertainty do not count
        def retimed(role, offset, seed_shift):
            result = central(role, seed_shift=seed_shift)
            for e in result["exchanges"]:
                e["start_wall_ms"] = 10_000 + e["index"] * 1000 + offset
                e["end_wall_ms"] = e["start_wall_ms"] + 600
            result["barrier"]["released_at_wall_ms"] = 9_995
            return result

        pairs_win, pairs_mac = retimed("windows", 0, 0), retimed("mac", 350, 100)  # every pair overlaps by 250 ms
        self.assertNotIn("real_io_intervals_overlap", failing(adjudicate(pairs_win, pairs_mac, min_overlap_ms=500)))
        tight = adjudicate(pairs_win, pairs_mac, min_overlap_ms=500, clock_uncertainty_ms=200)  # needs > 2x200 = 400 per pair
        self.assertIn("real_io_intervals_overlap", failing(tight))

    def test_barrier_needs_shared_nonce_and_staging(self):  # IMPORTANT 5
        diag = adjudicate(central("windows", mode="epoch"), central("mac", mode="epoch", seed_shift=100))
        self.assertEqual(diag["verdict"], "BLOCKED")
        self.assertIn("windows_barrier_staged_and_released", blocked(diag))
        other = adjudicate(central("windows"), central("mac", nonce="n2", seed_shift=100))
        self.assertIn("same_barrier_nonce", failing(other))
        late_stage = central("windows")
        late_stage["barrier"]["stage_wall_ms"] = late_stage["barrier"]["released_at_wall_ms"]
        self.assertIn("windows_barrier_staged_and_released", failing(adjudicate(late_stage, central("mac", seed_shift=100))))
        missing = central("mac", seed_shift=100)
        del missing["barrier"]
        self.assertNotEqual(adjudicate(central("windows"), missing)["verdict"], "PARTIAL")

    def test_release_times_must_agree_within_uncertainty_plus_tolerance(self):
        win, mac = central("windows"), central("mac", seed_shift=100)
        mac["barrier"]["released_at_wall_ms"] += 5000
        verdict = adjudicate(win, mac)
        self.assertIn("barrier_release_simultaneous", failing(verdict))
        self.assertEqual(adjudicate(win, mac, barrier_tolerance_ms=6000)["verdict"], "PARTIAL")
        # a measured offset realigns genuinely offset clocks
        shifted = central("mac", seed_shift=100)
        shifted["barrier"]["released_at_wall_ms"] += 90_000
        shifted["exchanges"] = [dict(e, start_wall_ms=e["start_wall_ms"] + 90_000, end_wall_ms=e["end_wall_ms"] + 90_000)
                                for e in shifted["exchanges"]]
        self.assertEqual(adjudicate(central("windows"), shifted, clock_offset_ms=-90_000)["verdict"], "PARTIAL")
        self.assertEqual(adjudicate(central("windows"), shifted)["verdict"], "FAIL")

    def test_label_alone_cannot_prove_same_phone(self):  # IMPORTANT 6
        for win_hash, mac_hash in ((None, None), ("aa11", None), ("", "aa11")):
            win, mac = central("windows", target_label_sha256=win_hash), central("mac", seed_shift=100,
                                                                                 target_label_sha256=mac_hash)
            verdict = adjudicate(win, mac)
            self.assertEqual(verdict["verdict"], "BLOCKED", (win_hash, mac_hash))
            self.assertIn("same_phone_identity", blocked(verdict))
        differ = adjudicate(central("windows"), central("mac", seed_shift=100, target_label_sha256="bb22"))
        self.assertIn("same_phone_identity", failing(differ))

    def test_single_central_is_blocked_not_pass(self):
        for first, second in ((central("windows"), None), (central("windows"), central("windows"))):
            verdict = adjudicate(first, second)
            self.assertEqual(verdict["verdict"], "BLOCKED")
            self.assertFalse(verdict["m6_discharged"])

    def test_two_phones_on_one_central_is_not_m6(self):
        double = central("windows", links_on_this_central=2)
        self.assertEqual(adjudicate(double, central("mac", seed_shift=100))["verdict"], "FAIL")
        lifecycle = {"scenario": "lc-two-phones", "role": "windows", "links_on_this_central": 2, "status": "PASS"}
        self.assertEqual(adjudicate(lifecycle, central("mac"))["verdict"], "FAIL")

    def test_wrong_phone_label_and_pair_id_fail(self):
        wrong = adjudicate(central("windows"), central("mac", label="Pixel 8", seed_shift=100))
        self.assertIn("same_phone_identity", failing(wrong))
        pair = adjudicate(central("windows"), central("mac", run="OTHER", seed_shift=100))
        self.assertIn("same_pair_run_id", failing(pair))

    def test_non_overlapping_and_adjacent_intervals_fail(self):
        far = adjudicate(central("windows", start=10_000), central("mac", start=100_000, seed_shift=100))
        self.assertEqual(far["verdict"], "FAIL")
        self.assertIn("real_io_intervals_overlap", failing(far))
        adjacent = adjudicate(central("windows", count=20, step=300), central("mac", start=15_850, seed_shift=100),
                              min_overlap_ms=100, barrier_tolerance_ms=10_000)
        self.assertIn("real_io_intervals_overlap", failing(adjacent))

    def test_one_sided_failure_fails(self):
        bad = central("mac", seed_shift=100)
        bad["exchanges"][7]["status"] = "FAIL"
        self.assertEqual(adjudicate(central("windows"), bad)["verdict"], "FAIL")
        short = central("mac", count=19, seed_shift=100)
        self.assertEqual(adjudicate(central("windows"), short)["verdict"], "FAIL")
        errors = central("windows", system_errors=[{"problem": "x"}])
        self.assertEqual(adjudicate(errors, central("mac", seed_shift=100))["verdict"], "FAIL")
        dropped = central("windows", unexpected_disconnects=[{"type": "disconnected"}])
        self.assertEqual(adjudicate(dropped, central("mac", seed_shift=100))["verdict"], "FAIL")

    def test_digest_mismatch_and_duplicate_seed_fail(self):
        bad = central("windows")
        bad["exchanges"][3]["received_sha256"] = "0" * 64
        self.assertEqual(adjudicate(bad, central("mac", seed_shift=100))["verdict"], "FAIL")
        dup = central("windows")
        dup["exchanges"][4]["seed"] = dup["exchanges"][3]["seed"]
        self.assertEqual(adjudicate(dup, central("mac", seed_shift=100))["verdict"], "FAIL")

    def test_blocked_central_blocks_pair(self):
        verdict = adjudicate(central("windows"), central("mac", status="BLOCKED", exchanges=[]))
        self.assertNotEqual(verdict["verdict"], "PARTIAL")

    def test_peer_survives_deliberate_disconnect(self):
        win = central("windows", deliberate_disconnect={"at_wall_ms": 12_000})
        mac = central("mac", count=30, seed_shift=100)
        self.assertEqual(adjudicate(win, mac)["verdict"], "PARTIAL")
        mac_short = central("mac", count=20, step=50, seed_shift=100)  # finishes before the disconnect
        self.assertEqual(adjudicate(win, mac_short)["verdict"], "FAIL")

    def test_disconnect_without_time_cannot_count_everything_as_surviving(self):  # minor: at_wall_ms defaulted to 0
        win = central("windows", deliberate_disconnect={"reply": {"type": "disconnected"}})
        verdict = adjudicate(win, central("mac", count=30, seed_shift=100))
        self.assertIn("mac_survives_windows_disconnect", failing(verdict))

    def test_barrier_file_parsing(self):
        self.assertEqual(core.parse_barrier_file('{"nonce": "x", "epoch_ms": 5}')[1], None)
        self.assertIsNotNone(core.parse_barrier_file("go")[1])
        self.assertIsNotNone(core.parse_barrier_file('{"epoch_ms": 5}')[1])
        self.assertIsNotNone(core.parse_barrier_file('{"nonce": ""}')[1])


# ---- fake host script (real framing over real pipes) ------------------------------------------------

FAKE_HOST = r'''#!{python}
import base64, json, os, struct, sys
trace = os.environ.get("VELES_OTP23_TRACE")
phones = int(os.environ.get("FAKE_PHONES", "2"))
def log(text):
    if trace:
        with open(trace, "a") as f:
            f.write("[%5dms pid=%d] %s\n" % (0, os.getpid(), text))
def out(message):
    body = json.dumps(message).encode()
    sys.stdout.buffer.write(struct.pack("<I", len(body)) + body); sys.stdout.buffer.flush()
def read(n):
    data = b""
    while len(data) < n:
        chunk = sys.stdin.buffer.read(n - len(data))
        if not chunk: break
        data += chunk
    return data
if len(sys.argv) < 2 or sys.argv[1] != "chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/":
    sys.exit(2)
log("start pid=%d" % os.getpid())
while True:
    header = read(4)
    if not header:
        log("exit code=0 stdout_drained=true"); sys.exit(0)
    if len(header) < 4:
        log("in  framing violation: TruncatedHeader"); log("exit code=2 stdout_drained=true"); sys.exit(2)
    message = json.loads(read(struct.unpack("<I", header)[0]))
    kind, rid = message["type"], message["request_id"]
    log("in  len=1 type=%s request_id=%s" % (kind, rid))
    if kind == "hello":
        out({{"type": "ready", "request_id": rid, "api_version": 1, "platform": "fake"}})
    elif kind == "start_scan":
        out({{"type": "scan_state", "request_id": rid, "scan_handle": "s1", "status": "started"}})
        for i in range(phones):
            out({{"type": "device_found", "scan_handle": "s1", "device_handle": "d%d" % i, "label": "phone%d" % i}})
    elif kind == "stop_scan":
        out({{"type": "scan_state", "request_id": rid, "scan_handle": "s1", "status": "stopped"}})
    elif kind == "connect":
        out({{"type": "connected", "request_id": rid, "connection_handle": "c-" + message["device_handle"]}})
    elif kind == "send":
        record = base64.b64decode(message["record_base64"])
        out({{"type": "sent", "request_id": rid, "connection_handle": message["connection_handle"]}})
        out({{"type": "message", "connection_handle": message["connection_handle"],
             "record_base64": base64.b64encode(record[:3] + b"\x02" + record[4:]).decode()}})
    elif kind == "shutdown":
        out({{"type": "shutdown_complete", "request_id": rid}}); log("exit code=0 stdout_drained=true"); sys.exit(0)
'''


class FakeAdapter(sm.Adapter):
    platform = role = "windows"
    direct_spawn_flags = 0

    def integrity(self, pid):
        return {"integrity": "medium", "pid": pid}

    def tcc_note(self):
        return "fake direct launch"


class DirectStdin(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.binary = self.tmp / "fake-host"
        self.binary.write_text(FAKE_HOST.format(python=sys.executable))
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.args = argparse.Namespace(discover_s=3.0)

    def _launch_fake_via_interpreter(self):
        """Test-only launch boundary: a shebang script is not a Windows executable (WinError 193), so run the
        fake host as `sys.executable <script>` on every OS. Still a real Popen with real pipes, so the EOF
        and cleanup assertions exercise the actual process path; production DirectHost is unchanged."""
        real_popen, fake = subprocess.Popen, str(self.binary)

        def popen(argv, *a, **kw):
            if argv and argv[0] == fake:
                argv = [sys.executable, *argv]
            return real_popen(argv, *a, **kw)

        patcher = unittest.mock.patch.object(sm.subprocess, "Popen", popen)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_case(self, name, phones="2"):
        os.environ["FAKE_PHONES"] = phones
        self.addCleanup(os.environ.pop, "FAKE_PHONES", None)
        self._launch_fake_via_interpreter()
        return sm.scenario_stdin(FakeAdapter(), name, self.args, IDENTITY, self.tmp, binary=str(self.binary))

    def test_stdin_eof_with_scan_and_two_links_passes_and_is_not_chrome_provenance(self):
        result = self.run_case("stdin-eof")
        self.assertEqual(result["status"], "PASS", result["actual"])
        detail = result["detail"]
        self.assertEqual(detail["exit_code"], 0)
        self.assertEqual(detail["stdout_eof_state"], "clean")
        self.assertEqual(detail["trace_mechanism"], "stdin_closed")
        self.assertNotIn("shutdown_complete", detail["frames_after_close"])
        self.assertFalse(detail["provenance"]["chrome_provenance"])
        self.assertEqual(len(detail["link_proof"]), 2)
        for key in ("id", "title", "expected", "actual", "status", "error_class"):
            self.assertIn(key, result)

    def test_one_phone_is_blocked_with_safe_class(self):
        result = self.run_case("stdin-eof", phones="1")
        self.assertEqual((result["status"], result["error_class"]), ("BLOCKED", "insufficient_phones"))

    def test_truncated_header_eof_is_rejected_with_exit_2(self):
        result = self.run_case("stdin-eof-truncated")
        self.assertEqual(result["status"], "PASS", result["actual"])
        self.assertEqual(result["detail"]["exit_code"], 2)
        self.assertEqual(result["detail"]["stdout_eof_state"], "clean")

    def test_missing_binary_is_not_a_pass(self):
        result = sm.scenario_stdin(FakeAdapter(), "stdin-eof", self.args, IDENTITY, self.tmp,
                                   binary=str(self.tmp / "nope"))
        self.assertNotEqual(result["status"], "PASS")
        self.assertEqual(result["error_class"], "environment")

    def test_result_contains_no_payload_or_labels(self):
        text = json.dumps(self.run_case("stdin-eof"))
        self.assertNotIn("phone0", text)
        self.assertNotIn("record_base64", text)


# ---- fake Chrome session for the chrome-driven scenarios and M6 ------------------------------------


class FakeChrome:
    def __init__(self):
        self.proc = type("P", (), {"pid": 100})()
        self.cdp = self
        self.calls = []

    def call(self, method, params=None, **_):
        self.calls.append(method)
        return {}

    def close(self):
        pass


class FakeSession:
    """Scripted Session replacement (same public surface run_m6 uses)."""

    def __init__(self, adapter, trace_path, identity, labels=("OnePlus 6T", "Pixel 8"), fail_at=None):
        self.adapter, self.trace_path, self.chrome = adapter, trace_path, FakeChrome()
        self.labels, self.fail_at = labels, fail_at
        self.unexpected, self.request_ids, self.log = [], [], []
        self.host_pid, self.wrappers, self.before = 55, [], set()

    def start(self):
        self.log.append("start")

    def owned_pids(self):
        return [self.host_pid]

    def discover(self, window_s, want=2, keep_scan=False):
        self.log.append(("discover", keep_scan))
        return [{"label": label, "device_handle": f"d{i}"} for i, label in enumerate(self.labels)]

    def stop_scan(self):
        self.log.append("stop_scan")

    def connect(self, devices):
        self.log.append(("connect", len(devices), devices[0]["label"]))
        return ["handle-1"]

    def exchange(self, handle, item, timeout=12.0):
        index = item["index"]
        self.request_ids.append(f"r{index}")
        status = "FAIL" if self.fail_at == index else "PASS"
        base = {"index": index, "seed": item["seed"], "size": item["size"], "request_sha256": item["request_sha256"],
                "expected_sha256": item["expected_sha256"], "received_sha256": item["expected_sha256"],
                "start_wall_ms": 1000 + index * 100, "end_wall_ms": 1050 + index * 100, "status": status}
        if status == "FAIL":
            base["problem"] = "no reply"
        return base

    def js(self, expression, timeout=70):
        return {"type": "shutdown_complete"} if "shutdown" in expression else {"type": "disconnected"}

    def close(self):
        return []


class M6Run(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.barrier = self.tmp / "go"
        self.out = self.tmp / "windows.json"
        self.patches = [unittest.mock.patch.object(sm, "verify_gone", lambda *a, **k: {"ok": True}),
                        unittest.mock.patch.object(sm, "meta_for", lambda *a, **k: {"platform": "windows"})]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def args(self, **over):
        values = dict(role="windows", pair_run_id="RUN1", phone_label="OnePlus 6T", target_label="OnePlus 6T",
                      sole_advertiser=False, barrier_file=str(self.barrier), start_at_epoch=None,
                      barrier_timeout_s=5.0, exchanges=20, seed_base=None, run_until_epoch=None,
                      disconnect_at_epoch=None, discover_s=0.1, stage_file=None, out=str(self.out),
                      connect_order="scan-active", isolation_action="parent isolated one phone", overwrite=False)
        values.update(over)
        return argparse.Namespace(**values)

    def release_later(self):
        stage = self.out.with_suffix(".stage.json")
        seen = {}

        def release():
            for _ in range(1000):
                try:
                    seen["stage"] = json.loads(stage.read_text())  # may be absent or half-written: retry
                except (OSError, ValueError):
                    threading.Event().wait(0.01)
                    continue
                self.barrier.write_text(json.dumps({"nonce": "parent-nonce", "epoch_ms": 123}))
                return

        thread = threading.Thread(target=release)
        thread.start()
        self.addCleanup(thread.join)
        return seen

    def run_m6(self, args, **factory):
        return sm.run_m6(args, adapter=FakeAdapter(), identity=IDENTITY,
                         session_factory=lambda a, t, i: FakeSession(a, t, i, **factory))

    def test_label_selected_phone_runs_20_exchanges_then_partial_and_stage_precedes_release(self):
        seen = self.release_later()
        code = self.run_m6(self.args())
        result = json.loads(self.out.read_text())
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual((result["links_on_this_central"], len(result["exchanges"])), (1, 20))
        self.assertEqual(result["discovered_phones"], 2)
        self.assertEqual(seen["stage"]["stage"], "READY")
        self.assertEqual(seen["stage"]["links_connected"], 1)
        self.assertLessEqual(seen["stage"]["at_wall_ms"], result["released_at_wall_ms"])
        barrier = result["barrier"]
        self.assertEqual((barrier["mode"], barrier["nonce_sha256"]), ("nonce-file", core.nonce_hash("parent-nonce")))
        self.assertEqual(barrier["plan_sha256"], seen["stage"]["plan_sha256"])
        self.assertLess(barrier["stage_wall_ms"], barrier["released_at_wall_ms"])
        self.assertEqual(result["isolation_action"], "parent isolated one phone")
        self.assertNotIn("parent-nonce", json.dumps(result))
        self.assertIsNotNone(result["target_label_sha256"])
        self.assertNotIn("OnePlus", json.dumps(result["exchanges"]))

    def test_two_phones_without_explicit_target_never_choose(self):
        code = self.run_m6(self.args(target_label=None, sole_advertiser=True))
        result = json.loads(self.out.read_text())
        self.assertEqual((code, result["status"], result["error_class"]), (1, "BLOCKED", "ambiguous_target"))
        self.assertEqual(result["exchanges"], [])
        self.assertEqual(result["links_on_this_central"], 0)

    def test_missing_label_blocks(self):
        code = self.run_m6(self.args(target_label="Galaxy"))
        self.assertEqual(json.loads(self.out.read_text())["error_class"], "target_not_found")
        self.assertEqual(code, 1)

    def test_exchange_failure_is_fail_and_never_partial(self):
        self.release_later()
        code = self.run_m6(self.args(), fail_at=5)
        result = json.loads(self.out.read_text())
        self.assertEqual((code, result["status"]), (1, "FAIL"))
        self.assertEqual(len(result["system_errors"]), 1)

    def test_role_must_match_platform(self):
        with self.assertRaises(SystemExit):
            self.run_m6(self.args(role="mac"))

    def test_start_epoch_barrier_is_diagnostic_mode_without_nonce(self):
        clock = {"now": 100.0}
        args = argparse.Namespace(barrier_file=None, start_at_epoch=100.5, barrier_timeout_s=10.0)

        def sleep(seconds):
            clock["now"] += max(seconds, 0.1)

        released = sm.wait_for_barrier(args, clock=lambda: clock["now"], sleep=sleep)
        self.assertGreaterEqual(released["released_at_wall_ms"], 100_500)
        self.assertEqual((released["mode"], released["nonce_sha256"]), ("epoch", None))

    def test_barrier_file_without_nonce_never_releases(self):
        clock = {"now": 0.0}
        self.barrier.write_text('{"epoch_ms": 5}')  # JSON but no nonce (the old plain-text "go" is not JSON either)
        args = argparse.Namespace(barrier_file=str(self.barrier), start_at_epoch=None, barrier_timeout_s=1.0)
        with self.assertRaises(sm.Blocked) as caught:
            sm.wait_for_barrier(args, clock=lambda: clock["now"],
                                sleep=lambda s: clock.__setitem__("now", clock["now"] + 0.5))
        self.assertIn("nonce", caught.exception.reason)

    def test_half_written_barrier_is_retried_then_released(self):
        clock = {"now": 0.0}
        self.barrier.write_text('{"nonce": "ab')
        args = argparse.Namespace(barrier_file=str(self.barrier), start_at_epoch=None, barrier_timeout_s=5.0)

        def sleep(seconds):
            clock["now"] += 0.5
            self.barrier.write_text('{"nonce": "abc"}')

        released = sm.wait_for_barrier(args, clock=lambda: clock["now"], sleep=sleep)
        self.assertEqual(released["nonce_sha256"], core.nonce_hash("abc"))

    def test_barrier_timeout_is_blocked(self):
        clock = {"now": 0.0}
        args = argparse.Namespace(barrier_file=str(self.tmp / "never"), start_at_epoch=None, barrier_timeout_s=1.0)
        with self.assertRaises(sm.Blocked):
            sm.wait_for_barrier(args, clock=lambda: clock["now"],
                                sleep=lambda s: clock.__setitem__("now", clock["now"] + 0.5))

    def test_selected_advertisement_without_label_cannot_prove_identity(self):
        code = self.run_m6(self.args(target_label=None, sole_advertiser=True), labels=(None,))
        result = json.loads(self.out.read_text())
        self.assertEqual((code, result["status"], result["error_class"]), (1, "BLOCKED", "ambiguous_target"))
        self.assertEqual(result["links_on_this_central"], 0)

    def test_connect_order_controls_scan_state_during_connect(self):
        logs = []
        factory = lambda a, t, i: logs.append(FakeSession(a, t, i, labels=("only",))) or logs[-1]  # noqa: E731
        for order, flag in (("scan-active", True), ("scan-stopped", False)):
            out = self.tmp / f"{order}.json"
            sm.run_m6(self.args(out=str(out), connect_order=order, target_label=None, sole_advertiser=True,
                                barrier_timeout_s=0.05), adapter=FakeAdapter(), identity=IDENTITY, session_factory=factory)
            self.assertIn(("discover", flag), logs[-1].log)
            self.assertIn("stop_scan", logs[-1].log)

    def test_existing_result_is_never_overwritten(self):
        self.out.write_text('{"first": "failure"}')
        with self.assertRaises(SystemExit):
            self.run_m6(self.args())
        self.assertEqual(self.out.read_text(), '{"first": "failure"}')
        self.release_later()
        self.run_m6(self.args(overwrite=True))
        self.assertNotIn("first", self.out.read_text())

    def test_adjudicate_cli_merges_two_result_files_and_requires_clock(self):
        win, mac = self.tmp / "w.json", self.tmp / "m.json"
        win.write_text(json.dumps(central("windows")))
        mac.write_text(json.dumps(central("mac", seed_shift=100)))

        def adjudicate_cli(name, **over):
            values = dict(windows=str(win), mac=str(mac), out=str(self.tmp / name), min_exchanges=20,
                          min_overlap_ms=1000, min_overlapping_pairs=3, clock_offset_ms=0.0, clock_uncertainty_ms=50.0,
                          clock_provenance="midpoint", barrier_tolerance_ms=2000.0, overwrite=False)
            values.update(over)
            code = sm.run_adjudicate(argparse.Namespace(**values))
            return code, json.loads((self.tmp / name).read_text())

        self.assertEqual(adjudicate_cli("a.json")[0], 0)
        code, verdict = adjudicate_cli("b.json", clock_offset_ms=None)
        self.assertEqual((code, verdict["verdict"]), (1, "BLOCKED"))
        win.write_text(json.dumps(central("windows", links_on_this_central=2)))
        self.assertEqual(adjudicate_cli("c.json")[0], 1)
        with self.assertRaises(SystemExit):  # an existing adjudication is not silently replaced
            adjudicate_cli("c.json")


class Cli(unittest.TestCase):
    def parse(self, *argv):
        parser = sm.build_parser()
        args = parser.parse_args(list(argv))
        if args.command == "m6":
            sm.validate_m6_args(parser, args)
        return args

    BASE = ["m6", "--role", "mac", "--pair-run-id", "r", "--phone-label", "p", "--out", "o.json"]

    def test_m6_requires_barrier_selector_and_20_exchanges(self):
        good = self.BASE + ["--sole-advertiser", "--barrier-file", "b"]
        self.assertEqual(self.parse(*good).exchanges, 20)
        for bad in (self.BASE + ["--sole-advertiser"],                                  # no barrier
                    self.BASE + ["--barrier-file", "b"],                                # no phone selector
                    good + ["--target-label", "x"],                                    # both selectors
                    good + ["--exchanges", "19"],
                    good + ["--start-at-epoch", "5"],                                  # both barriers
                    good + ["--seed-base", "300"]):
            with self.assertRaises(SystemExit):
                with unittest.mock.patch("sys.stderr", io.StringIO()):
                    self.parse(*bad)

    def test_record_helper_rejects_unknown_status_and_class(self):
        with self.assertRaises(ValueError):
            core.record("x", "t", "e", "a", "MAYBE")
        with self.assertRaises(ValueError):
            core.record("x", "t", "e", "a", "FAIL", "weird")
        self.assertEqual(core.classify_failure({"stage": "permission_denied"}), "permission_denied")
        self.assertEqual(core.classify_failure({"code": "timeout"}), "timeout")
        self.assertEqual(core.classify_failure({"code": "connect_failed"}), "host_error")

    def test_source_revision_never_invents_a_commit(self):
        info = core.source_revision(repo=tempfile.gettempdir())
        self.assertFalse(info["available"])
        self.assertTrue(info["reason"])
        self.assertNotIn("commit", info)


class LifecycleBlocked(unittest.TestCase):
    """A chrome-driven scenario with fewer than two advertising phones is BLOCKED, never PASS."""

    def test_not_enough_phones_and_cleanup_still_runs(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        closed = []

        class OnePhone(FakeSession):
            def __init__(self, *a, **k):
                super().__init__(*a, labels=("only",), **k)

            def close(self):
                closed.append(True)
                return []

        class Adapter(FakeAdapter):
            host_name = "me.nagaev.veles.otp23.plain"

            def hygiene(self, pids):
                return {"visible_windows": 0, "top_level_windows": 0, "network_endpoints": 0}

        with unittest.mock.patch.object(sm, "Session", OnePhone):
            result = sm.scenario_chrome(Adapter(), "shutdown", argparse.Namespace(discover_s=0.1), IDENTITY, tmp)
        self.assertEqual((result["status"], result["error_class"]), ("BLOCKED", "insufficient_phones"))
        self.assertEqual(closed, [True])

    def test_link_proofs_are_distinct_16_byte_records_with_a_scan_kept_active(self):
        calls = []

        class TwoPhones(FakeSession):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.sent = []

            def connect(self, devices):
                calls.append("connect")
                return ["h1", "h2"]

            def exchange(self, handle, item, timeout=12.0):
                self.sent.append((handle, len(item["request"]), item["request_sha256"]))
                return dict(super().exchange(handle, item), size=item["size"])

            def start_scan(self):
                calls.append("rescan")

        session = TwoPhones(FakeAdapter(), None, IDENTITY)
        handles, proofs = sm.link_pair(session, argparse.Namespace(discover_s=0.1, connect_order="scan-active"))
        self.assertEqual(handles, ["h1", "h2"])
        self.assertTrue(all(length >= 16 for _, length, _ in session.sent))
        self.assertEqual(len({digest for _, _, digest in session.sent}), 2)
        self.assertEqual(calls, ["connect", "rescan"])
        self.assertLess(session.log.index(("discover", True)), session.log.index("stop_scan"))

    def test_hygiene_reports_host_and_wrapper_separately(self):
        calls = []

        class Wrapped(FakeSession):
            def __init__(self, *a, **k):
                super().__init__(*a, labels=("only",), **k)
                self.wrappers = [77]

        class Adapter(FakeAdapter):
            host_name = "x"

            def hygiene(self, pids):
                calls.append(list(pids))
                return {"visible_windows": 0, "top_level_windows": 1 if pids == [77] else 0, "network_endpoints": 0}

        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        with unittest.mock.patch.object(sm, "Session", Wrapped):
            result = sm.scenario_chrome(Adapter(), "shutdown", argparse.Namespace(discover_s=0.1), IDENTITY, tmp)
        self.assertEqual(calls[:2], [[55], [77]])
        self.assertEqual(result["detail"]["hygiene_before"]["top_level_windows"], 0)
        self.assertEqual(result["detail"]["wrapper_hygiene_before"]["top_level_windows"], 1)
        self.assertEqual(result["detail"]["integrity"]["host"], {"integrity": "medium", "pid": 55})

    def _wrapper_case(self, wrapper_hygiene):
        class Wrapped(FakeSession):
            def __init__(self, *a, **k):
                super().__init__(*a, labels=("only",), **k)
                self.wrappers = [77]

        class Adapter(FakeAdapter):
            host_name = "x"

            def hygiene(self, pids):
                if pids == [77]:
                    return wrapper_hygiene
                return {"visible_windows": 0, "top_level_windows": 0, "network_endpoints": 0}

        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        with unittest.mock.patch.object(sm, "Session", Wrapped):
            return sm.scenario_chrome(Adapter(), "shutdown", argparse.Namespace(discover_s=0.1), IDENTITY, tmp)

    def test_wrapper_visible_window_or_network_endpoint_fails(self):  # was silently dropped
        for bad in ({"visible_windows": 1, "top_level_windows": 1, "network_endpoints": 0},
                    {"visible_windows": 0, "top_level_windows": 1, "network_endpoints": 2}):
            result = self._wrapper_case(bad)
            self.assertEqual(result["status"], "FAIL", bad)
            self.assertIn("wrapper", result["actual"])

    def test_hidden_wrapper_console_is_allowed_and_unavailable_is_not_asserted(self):
        for ok in ({"visible_windows": 0, "top_level_windows": 1, "network_endpoints": 0},
                   {"visible_windows": "unavailable", "top_level_windows": "unavailable",
                    "network_endpoints": "unavailable"}):
            result = self._wrapper_case(ok)
            self.assertEqual((result["status"], result["error_class"]), ("BLOCKED", "insufficient_phones"))  # got past hygiene
            self.assertEqual(result["detail"]["wrapper_hygiene_before"], ok)

    def test_adapter_toggle_is_operator_gated_not_fabricated(self):
        result = sm.scenario_adapter_toggle()
        self.assertEqual((result["status"], result["error_class"]), ("BLOCKED", "requires_operator"))


if __name__ == "__main__":
    unittest.main()
