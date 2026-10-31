"""Offline regression tests for the M11-1 fix (no Chrome, no TCC, no registration, no device).

1. watch_prompt() with a fake clock: the host's pre-init authorization line comes FIRST, the host's
   10 s permission_pending reply next, the owner's nonce answer later — the loop must keep waiting,
   bind the answer, rescan at most a bounded number of times, and classify from the post-answer reply.
2. main() end-to-end with a stub Chrome Popen + stub CDP + scripted host trace: the same timeline must
   land in m11-capture.json (answer bound to the nonce, waited past the pre-init line, shutdown only
   after the answer).
Usage: python3 test_m11_prompt_watch.py
"""
import functools
import hashlib
import importlib.util
import json
import pathlib
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("m11", HERE / "m11_dev_capture.py")
m11 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m11)

PRE = "[  100ms pid=7] bluetooth authorization before adapter init: not_determined"
INIT_ALLOWED = "[ 31000ms pid=7] adapter init: created=true authorization=allowed_always"
PENDING = {"type": "error", "code": "scan_failed", "recoverability": "retryable", "stage": "permission_pending"}
DENIED = {"type": "error", "code": "scan_failed", "recoverability": "retryable", "stage": "access_denied"}
STARTED = {"type": "scan_state", "status": "started", "scan_handle": "h1"}


class FakeTimeline:
    """Fake clock; scripted trace lines, scan replies (per scan issue) and the answer file write."""

    def __init__(self, tmp, nonce, trace=(), replies=(), answer=None, answer_at=None):
        self.t = 0.0
        self.trace = list(trace)          # [(t, line)]
        self.replies = list(replies)      # [(scan_index, reply_delay_s, reply)]
        self.answer, self.answer_at = answer, answer_at
        self.answer_path = pathlib.Path(tmp) / f"m11-prompt-{nonce}.answer.json"
        self.scans = [0.0]                # scan issue times (the first scan is issued before the watch)
        self.pending = None

    def clock(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds
        if self.answer is not None and self.answer_at is not None and self.t >= self.answer_at \
                and not self.answer_path.exists():
            self.answer_path.write_text(json.dumps(self.answer), encoding="utf-8")

    def read_trace(self):
        return "\n".join(line for at, line in self.trace if at <= self.t)

    def take(self):
        for index, (scan, delay, reply) in enumerate(self.replies):
            if scan < len(self.scans) and self.t >= self.scans[scan] + delay:
                self.replies.pop(index)
                return reply
        return None

    def restart(self):
        self.scans.append(self.t)


def watch(tl, nonce, wait_s=720):
    return m11.watch_prompt(tl.take, tl.read_trace, tl.answer_path, nonce, wait_s, tl.restart,
                            clock=tl.clock, sleep=tl.sleep)


class WatchPrompt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m11watch-")
        self.nonce = "n-1"

    def test_preinit_line_first_then_pending_then_allow_waits_and_binds(self):
        answer = {"nonce": self.nonce, "answered_by": "owner", "dialog_observed": True,
                  "prompt_name_shown": "VelesOtp23Dev", "choice": "allow"}
        tl = FakeTimeline(self.tmp, self.nonce, trace=[(0.0, PRE), (31.0, INIT_ALLOWED)],
                          replies=[(0, 11, PENDING), (1, 2, STARTED)], answer=answer, answer_at=30)
        result = watch(tl, self.nonce)
        self.assertEqual(result["ended_by"], "answer_and_scan_reply")
        self.assertGreaterEqual(result["waited_s"], 30, "must not end on the pre-init trace line")
        self.assertEqual(result["answer"], answer)
        self.assertEqual(result["rescans"], 1)
        self.assertEqual([r["reply"] for r in result["replies"]], [PENDING, STARTED])
        self.assertEqual([r["after_answer"] for r in result["replies"]], [False, True])
        self.assertEqual(result["observations"][0]["line"], PRE.strip())
        verdict = m11.classify(result)
        self.assertEqual(verdict["state"], "authorized_started")
        self.assertEqual(verdict["post_init_authorization"], "allowed_always", "never from the pre-init line")
        self.assertIs(verdict["owner_dialog_observed"], True)

    def test_already_authorized_still_waits_for_the_owner_no_dialog_answer(self):
        answer = {"nonce": self.nonce, "answered_by": "owner", "dialog_observed": False}
        tl = FakeTimeline(self.tmp, self.nonce, trace=[(0.0, PRE.replace("not_determined", "allowed_always"))],
                          replies=[(0, 2, STARTED)], answer=answer, answer_at=20)
        result = watch(tl, self.nonce)
        self.assertGreaterEqual(result["waited_s"], 20)
        self.assertEqual(result["rescans"], 0)
        verdict = m11.classify(result)
        self.assertEqual(verdict["state"], "authorized_started")
        self.assertIs(verdict["owner_dialog_observed"], False)
        self.assertIsNone(verdict["post_init_authorization"], "pre-init line is never the classification source")

    def test_deny_rescans_and_records_the_actual_post_answer_result(self):
        answer = {"nonce": self.nonce, "answered_by": "owner", "dialog_observed": True,
                  "prompt_name_shown": "otp23-host", "choice": "deny"}
        tl = FakeTimeline(self.tmp, self.nonce, trace=[(0.0, PRE)],
                          replies=[(0, 11, PENDING), (1, 1, DENIED)], answer=answer, answer_at=25)
        result = watch(tl, self.nonce)
        self.assertEqual(result["ended_by"], "answer_and_scan_reply")
        self.assertEqual(m11.classify(result)["state"], "access_denied")
        self.assertEqual(result["answer"]["choice"], "deny")

    def test_unanswered_pending_is_never_a_denial_and_hits_the_deadline(self):
        tl = FakeTimeline(self.tmp, self.nonce, trace=[(0.0, PRE)], replies=[(0, 11, PENDING)])
        result = watch(tl, self.nonce, wait_s=120)
        self.assertEqual(result["ended_by"], "deadline")
        self.assertGreaterEqual(result["waited_s"], 120)
        self.assertEqual(result["rescans"], 0, "no rescan without the owner's answer")
        self.assertEqual(m11.classify(result)["state"], "pending_at_host_budget_not_denial")

    def test_wrong_nonce_answer_is_ignored_and_recorded(self):
        bad = {"nonce": "other", "answered_by": "owner", "dialog_observed": False}
        tl = FakeTimeline(self.tmp, self.nonce, trace=[(0.0, PRE)], replies=[(0, 11, PENDING)],
                          answer=bad, answer_at=15)
        result = watch(tl, self.nonce, wait_s=60)
        self.assertIsNone(result["answer"])
        self.assertEqual(result["ended_by"], "deadline")
        self.assertTrue(any("nonce" in p for p in result["answer_problems"]))

    def test_rescans_are_bounded_when_still_pending_after_the_answer(self):
        answer = {"nonce": self.nonce, "answered_by": "owner", "dialog_observed": True,
                  "prompt_name_shown": "VelesOtp23Dev", "choice": "allow"}
        tl = FakeTimeline(self.tmp, self.nonce, trace=[(0.0, PRE)],
                          replies=[(0, 11, PENDING), (1, 11, PENDING), (2, 11, PENDING)],
                          answer=answer, answer_at=12)
        result = watch(tl, self.nonce)
        self.assertEqual(result["rescans"], 2)
        self.assertEqual(result["ended_by"], "answer_and_scan_reply")
        self.assertEqual(m11.classify(result)["state"], "pending_at_host_budget_not_denial")


class MainWiring(unittest.TestCase):
    """main() with a stub Chrome + stub CDP: nothing real is launched (Popen of Chrome is replaced)."""

    def test_capture_binds_answer_and_shuts_down_only_after_it(self):
        root = pathlib.Path(tempfile.mkdtemp(prefix="m11main-"))
        stage = root / "stage"; app = stage / "target-dev" / "VelesOtp23Dev.app"
        (app / "Contents/MacOS").mkdir(parents=True); (app / "Contents/MacOS/otp23-host").write_bytes(b"dev")
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(
            {"CFBundleIdentifier": "me.nagaev.veles.bridge.dev", "CFBundleName": "VelesOtp23Dev",
             "CFBundleExecutable": "otp23-host"}))
        (stage / "extension").mkdir(); (stage / "extension/manifest.json").write_text("{}")
        approved = root / "Approved.app"; (approved / "Contents/MacOS").mkdir(parents=True)
        (approved / "Contents/MacOS/otp23-host").write_bytes(b"approved")
        home = root / "home"; home.mkdir()
        out = root / "capture" / "a1"
        clock = {"t": 0.0}
        events = []
        state = {"trace": None, "scans": [], "nonce": None}

        def fake_sleep(seconds):
            clock["t"] += seconds
            if state["trace"] and clock["t"] >= 0 and not state["trace"].exists():
                state["trace"].write_text(PRE + "\n")
            if clock["t"] >= 30 and state["nonce"]:
                answer = out / f"m11-prompt-{state['nonce']}.answer.json"
                if not answer.exists():
                    answer.write_text(json.dumps({"nonce": state["nonce"], "answered_by": "owner",
                                                  "dialog_observed": True, "prompt_name_shown": "VelesOtp23Dev",
                                                  "choice": "allow"}))
                    events.append(("answer", clock["t"]))

        class FakeProc:
            pid = 4242

            def __init__(self, argv, env):
                profile = [a for a in argv if a.startswith("--user-data-dir=")][0].split("=", 1)[1]
                pathlib.Path(profile, "DevToolsActivePort").write_text("9222\n/devtools/browser/x\n")
                state["trace"] = pathlib.Path(env["VELES_OTP23_TRACE"])

            def wait(self, timeout=None):
                return 0

            terminate = kill = lambda self: None

        class FakeCdp:
            def __init__(self, port, path):
                pass

            def call(self, method, params=None, session=None, timeout=30):
                expr = (params or {}).get("expression", "")
                if method == "Browser.getVersion":
                    return {"product": "Chrome/stub"}
                if method == "Extensions.loadUnpacked":
                    return {"id": m11.EXTENSION_ID}
                if method == "Target.createTarget":
                    return {"targetId": "t"}
                if method == "Target.attachToTarget":
                    return {"sessionId": "s"}
                if method != "Runtime.evaluate":
                    return {}
                if "typeof window.otp23" in expr:
                    return {"result": {"value": True}}
                if "otp23.hello()" in expr:
                    return {"result": {"value": {"type": "ready"}}}
                if "otp23.startScan" in expr:
                    state["scans"].append(clock["t"])
                    events.append(("scan", clock["t"]))
                    if state["nonce"] is None:
                        state["nonce"] = json.loads(next(out.glob("m11-prompt-*.request.json")).read_text())["nonce"]
                    return {"result": {"value": 1}}
                if "window.__scan = null; return r" in expr:
                    n = len(state["scans"])
                    if n == 1 and clock["t"] >= 11 and not state.get("r1"):
                        state["r1"] = True
                        return {"result": {"value": PENDING}}
                    if n == 2 and clock["t"] >= state["scans"][1] + 1 and not state.get("r2"):
                        state["r2"] = True
                        state["trace"].write_text(PRE + "\n" + INIT_ALLOWED + "\n")
                        return {"result": {"value": STARTED}}
                    return {"result": {"value": None}}
                if "otp23.stopScan" in expr:
                    events.append(("stop", clock["t"]))
                    return {"result": {"value": 1}}
                if "otp23.shutdown()" in expr:
                    events.append(("shutdown", clock["t"]))
                    return {"result": {"value": {"type": "shutdown_complete"}}}
                return {"result": {"value": 1}}

        m11.APPROVED_APP = str(approved)
        m11.APPROVED_SHA = hashlib.sha256(b"approved").hexdigest()
        argv = ["m11", "--stage", str(stage), "--out-dir", str(out), "--user-scope-manifest"]
        fake_watch = functools.partial(m11.watch_prompt.__wrapped__ if hasattr(m11.watch_prompt, "__wrapped__")
                                       else m11.watch_prompt, clock=lambda: clock["t"], sleep=fake_sleep)
        with mock.patch.object(sys, "argv", argv), mock.patch.object(m11, "verify_strict", lambda p: True), \
                mock.patch.object(m11, "codesign_info", lambda p: {"rc": 0, "stub": True}), \
                mock.patch("pathlib.Path.home", lambda: home), mock.patch("subprocess.Popen", FakeProc), \
                mock.patch.object(m11, "CdpSession", FakeCdp), mock.patch.object(m11, "watch_prompt", fake_watch), \
                mock.patch.object(m11.time, "sleep", fake_sleep), \
                mock.patch.object(m11.platform, "platform", lambda: "stub-os"):
            rc = m11.main()
        doc = json.loads((out / "m11-capture.json").read_text())
        self.assertEqual(rc, 0, doc["problems"])
        self.assertEqual(doc["human_prompt_answer"]["nonce"], state["nonce"])
        self.assertEqual(doc["human_prompt_answer"]["prompt_name_shown"], "VelesOtp23Dev")
        self.assertEqual(doc["authorized_state_classified"]["state"], "authorized_started")
        self.assertEqual(doc["authorized_state_classified"]["post_init_authorization"], "allowed_always")
        self.assertEqual(doc["prompt_watch"]["observations"][0]["line"], PRE)
        answer_t = dict(events)["answer"]
        shutdown_t = dict(events)["shutdown"]
        self.assertGreater(shutdown_t, answer_t, "the host/port stays alive until the owner's answer")
        self.assertEqual([k for k, _ in events].count("scan"), 2, "one bounded post-answer rescan")
        self.assertIn("h1", doc["scans_stopped_after_capture"])
        self.assertTrue(doc["user_scope_manifest_removed"])
        self.assertFalse((home / "Library/Application Support/Google/Chrome/NativeMessagingHosts/"
                                 "me.nagaev.veles.otp23.dev.json").exists())

    def test_refusal_is_recorded_in_problems_and_missing_approved_app_still_writes_capture(self):
        root = pathlib.Path(tempfile.mkdtemp(prefix="m11ref-"))
        stage = root / "stage"; app = stage / "target-dev" / "VelesOtp23Dev.app"
        (app / "Contents/MacOS").mkdir(parents=True); (app / "Contents/MacOS/otp23-host").write_bytes(b"dev")
        (stage / "extension").mkdir()
        out = root / "capture" / "a1"
        m11.APPROVED_APP = str(root / "missing.app")
        argv = ["m11", "--stage", str(stage), "--out-dir", str(out), "--user-scope-manifest"]
        with mock.patch.object(sys, "argv", argv), mock.patch("pathlib.Path.home", lambda: root), \
                mock.patch.object(m11, "verify_strict", lambda p: False), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("must not launch")), \
                mock.patch.object(m11.platform, "platform", lambda: "stub-os"):
            result = m11.main()
        doc = json.loads((out / "m11-capture.json").read_text())
        self.assertEqual(result, 1)
        self.assertTrue(doc["problems"], "a refused/failed run must never read as clean (N1)")
        self.assertIn("unavailable", doc["approved_fixture_after"], "N3: missing approved app recorded")


if __name__ == "__main__":
    unittest.main()
