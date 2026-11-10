import itertools
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from release_ready import ALWAYS, COMPONENT_JOBS, main, ready  # noqa: E402

NAMES = ("android", "web-extension", "installer")
COMBOS = [
    dict(zip(NAMES, actions))
    for actions in itertools.product(("RELEASE", "CARRY"), repeat=3)
    if "RELEASE" in actions
]


def make_plan(actions):
    return {
        "release_tag": "2026.11.20",
        "any_release": "RELEASE" in actions.values(),
        "components": {n: {"current": "0.1.0", "next": "0.1.1", "action": a, "tag": n + "-v0.1.1"} for n, a in actions.items()},
    }


def make_needs(actions):
    needs = {job: {"result": "success", "outputs": {}} for job in ALWAYS}
    for name, action in actions.items():
        for job in COMPONENT_JOBS[name]:
            needs[job] = {"result": "success" if action == "RELEASE" else "skipped", "outputs": {}}
    return needs


class ReadyTest(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(
            COMPONENT_JOBS,
            {
                "android": ["build-android", "sign-android"],
                "web-extension": ["build-web-extension"],
                "installer": ["build-windows", "verify-windows", "sign-windows", "build-macos",
                              "verify-macos", "sign-macos", "gate-installer"],
            },
        )
        self.assertEqual(ALWAYS, ["guard", "plan", "supply-chain", "carry"])

    def test_seven_combinations(self):
        self.assertEqual(len(COMBOS), 7)

    def test_ready_for_every_combination(self):
        for actions in COMBOS:
            with self.subTest(actions=actions):
                self.assertEqual(ready(make_needs(actions), make_plan(actions)), [])

    def test_release_job_not_success_blocks(self):
        for actions in COMBOS:
            for name, action in actions.items():
                if action != "RELEASE":
                    continue
                for job in COMPONENT_JOBS[name]:
                    for result in ("skipped", "failure", "cancelled"):
                        with self.subTest(actions=actions, job=job, result=result):
                            needs = make_needs(actions)
                            needs[job]["result"] = result
                            problems = ready(needs, make_plan(actions))
                            self.assertTrue(problems)
                            self.assertTrue(any(job in p for p in problems))

    def test_carry_job_failure_blocks(self):
        for actions in COMBOS:
            for name, action in actions.items():
                if action != "CARRY":
                    continue
                for job in COMPONENT_JOBS[name]:
                    for result in ("failure", "cancelled", "success"):
                        with self.subTest(actions=actions, job=job, result=result):
                            needs = make_needs(actions)
                            needs[job]["result"] = result
                            self.assertTrue(ready(needs, make_plan(actions)))

    def test_always_jobs_must_succeed(self):
        for actions in COMBOS:
            for job in ALWAYS:
                for result in ("failure", "cancelled", "skipped"):
                    with self.subTest(actions=actions, job=job, result=result):
                        needs = make_needs(actions)
                        needs[job]["result"] = result
                        self.assertTrue(ready(needs, make_plan(actions)))

    def test_carry_failed(self):
        actions = {"android": "CARRY", "web-extension": "RELEASE", "installer": "CARRY"}
        needs = make_needs(actions)
        needs["carry"]["result"] = "failure"
        self.assertIn("carry", " ".join(ready(needs, make_plan(actions))))

    def test_missing_job_or_component_blocks(self):
        actions = {"android": "RELEASE", "web-extension": "CARRY", "installer": "CARRY"}
        needs = make_needs(actions)
        del needs["sign-android"]
        self.assertTrue(ready(needs, make_plan(actions)))
        plan = make_plan(actions)
        del plan["components"]["installer"]
        self.assertTrue(ready(make_needs(actions), plan))
        plan = make_plan(actions)
        plan["components"]["android"]["action"] = "MAYBE"
        self.assertTrue(ready(make_needs(actions), plan))

    def test_nothing_to_release_blocks(self):
        actions = {n: "CARRY" for n in NAMES}
        self.assertTrue(ready(make_needs(actions), make_plan(actions)))

    def test_cli(self):
        tmp = Path(tempfile.mkdtemp(prefix="veles-ready-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        actions = {"android": "CARRY", "web-extension": "RELEASE", "installer": "CARRY"}
        (tmp / "plan.json").write_text(json.dumps(make_plan(actions)), encoding="utf-8")
        needs = make_needs(actions)
        (tmp / "needs.json").write_text(json.dumps(needs), encoding="utf-8")
        args = ["ready", "--needs-json", str(tmp / "needs.json"), "--plan-json", str(tmp / "plan.json")]
        self.assertEqual(main(args), 0)
        needs["build-web-extension"]["result"] = "skipped"
        (tmp / "needs.json").write_text(json.dumps(needs), encoding="utf-8")
        self.assertEqual(main(args), 1)


if __name__ == "__main__":
    unittest.main()
