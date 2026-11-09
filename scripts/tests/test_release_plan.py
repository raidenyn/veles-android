import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gitfixture  # noqa: E402
from gitfixture import cliff_path, make_repo, requires_cliff  # noqa: E402
from release_plan import (  # noqa: E402
    PlanError,
    latest_combined_release,
    main,
    make_plan,
    next_release_tag,
    plan_to_json,
    release_refs,
    render_notes,
    render_summary,
)

TODAY = date(2026, 11, 20)
COMPONENTS = ("android", "web-extension", "installer")


def manifest(versions, release="2026.11.09"):
    return {
        "schema": 1,
        "release": release,
        "commit": "0" * 40,
        "components": {name: {"version": v, "assets": {}} for name, v in versions.items()},
    }


ALL_010 = manifest({c: "0.1.0" for c in COMPONENTS})


def comps(plan):
    return {c.name: c for c in plan.components}


def tagged(test):
    repo = make_repo(test)
    for c in COMPONENTS:
        repo.tag("%s-v0.1.0" % c)
    return repo


@requires_cliff
class PlannerBumpTest(unittest.TestCase):
    def plan_after(self, message, touching):
        repo = tagged(self)
        repo.commit(message, touching=touching)
        return make_plan(repo.path, repo.cmap(), TODAY, ["2026.11.09"], ALL_010, cliff_path())

    def test_shared_rust_fix_bumps_android_and_extension_only(self):
        c = comps(self.plan_after("fix(crypto): x", touching="rust/a.rs"))
        self.assertEqual((c["android"].next, c["android"].action), ("0.1.1", "RELEASE"))
        self.assertEqual(c["web-extension"].next, "0.1.1")
        self.assertEqual(c["installer"].action, "CARRY")
        self.assertEqual(c["installer"].next, "0.1.0")
        self.assertEqual(c["android"].tag, "android-v0.1.1")
        self.assertEqual(c["android"].reasons, {"fix": 1})
        self.assertIn("### Bug Fixes", c["android"].notes)
        self.assertIn("- x (", c["android"].notes)

    def test_global_build_fix_bumps_every_component(self):
        c = comps(self.plan_after("fix(build): x", touching="build.gradle.kts"))
        self.assertEqual(c["installer"].next, "0.1.1")
        self.assertEqual(c["android"].next, "0.1.1")
        self.assertEqual(c["web-extension"].next, "0.1.1")

    def test_perf_is_a_patch(self):
        self.assertEqual(comps(self.plan_after("perf(android): x", touching="app/a.kt"))["android"].next, "0.1.1")

    def test_breaking_below_one_is_major(self):
        c = comps(self.plan_after("feat(protocol)!: x", touching="native-bridge/installer/a.rs"))
        self.assertEqual(c["installer"].next, "1.0.0")
        self.assertEqual(c["installer"].reasons, {"breaking": 1})

    def test_breaking_footer_is_major(self):
        c = comps(self.plan_after("chore: x\n\nBREAKING CHANGE: y", touching="app/a.kt"))
        self.assertEqual(c["android"].next, "1.0.0")

    def test_non_bumping_types(self):
        for t in ("docs", "chore", "refactor", "test", "build", "ci", "style", "revert"):
            with self.subTest(type=t):
                self.assertEqual(comps(self.plan_after("%s: x" % t, touching="app/a.kt"))["android"].action, "CARRY")
        self.assertEqual(comps(self.plan_after("misc fixes", touching="app/a.kt"))["android"].action, "CARRY")

    def test_ci_only_paths_do_not_release(self):
        c = comps(self.plan_after("fix: x", touching="config/detekt/detekt.yml"))
        self.assertEqual(c["android"].action, "CARRY")


@requires_cliff
class PlannerTest(unittest.TestCase):
    def test_independent_previous_tags(self):
        repo = make_repo(self)
        repo.tag("android-v0.4.0")
        repo.tag("installer-v0.1.0")
        repo.commit("feat(app): app feature", touching="app/a.kt")
        repo.tag("web-extension-v0.2.3")
        repo.commit("fix(web): web fix", touching="web-extension/a.ts")
        repo.commit("feat(web): web feature", touching="web-extension/b.ts")
        previous = manifest({"android": "0.4.0", "web-extension": "0.2.3", "installer": "0.1.0"})
        c = comps(make_plan(repo.path, repo.cmap(), TODAY, ["2026.11.09"], previous, cliff_path()))
        self.assertEqual((c["android"].current, c["android"].next), ("0.4.0", "0.5.0"))
        self.assertEqual((c["web-extension"].current, c["web-extension"].next), ("0.2.3", "0.3.0"))
        self.assertEqual(c["web-extension"].reasons, {"feat": 1, "fix": 1})
        self.assertNotIn("app feature", c["web-extension"].notes)
        self.assertIn("app feature", c["android"].notes)
        self.assertEqual(c["installer"].action, "CARRY")

    def test_initial_versions_and_since_notes(self):
        repo = make_repo(self)
        repo.commit("feat(app): before onboarding", touching="app/a.kt")
        since = repo.commit("chore: onboard", touching="app/a.kt")
        repo.commit("fix(app): after onboarding", touching="app/a.kt")
        repo.tag("0.0.3", at=repo.init)  # legacy plain tags never count
        plan = make_plan(repo.path, repo.cmap(since=since), TODAY, [], None, cliff_path())
        c = comps(plan)
        for name in COMPONENTS:
            self.assertEqual((c[name].current, c[name].next, c[name].action), (None, "0.1.0", "RELEASE"))
        self.assertIn("after onboarding", c["android"].notes)
        self.assertNotIn("before onboarding", c["android"].notes)
        self.assertEqual(c["installer"].notes, "")
        self.assertIsNone(plan.previous_release)
        self.assertEqual(plan.release_tag, "2026.11.20")
        self.assertTrue(plan.any_release)

    def test_bootstrap_fill_releases_missing_component_at_current_version(self):
        repo = tagged(self)
        repo.commit("docs: x", touching="web-extension/a.ts")
        previous = manifest({"android": "0.1.0", "web-extension": "0.1.0"})
        c = comps(make_plan(repo.path, repo.cmap(), TODAY, ["2026.11.09"], previous, cliff_path()))
        self.assertEqual((c["installer"].current, c["installer"].next, c["installer"].action), ("0.1.0", "0.1.0", "RELEASE"))
        self.assertEqual(c["android"].action, "CARRY")

    def test_nothing_to_release(self):
        repo = tagged(self)
        repo.commit("docs: x", touching="app/a.kt")
        plan = make_plan(repo.path, repo.cmap(), TODAY, ["2026.11.09"], ALL_010, cliff_path())
        self.assertFalse(plan.any_release)
        self.assertEqual({c.action for c in plan.components}, {"CARRY"})

    def test_orphaned_tag_fails(self):
        repo = tagged(self)
        repo.commit("feat: x", touching="app/a.kt")
        repo.tag("android-v0.2.0")
        with self.assertRaises(PlanError) as ctx:
            make_plan(repo.path, repo.cmap(), TODAY, ["2026.11.09"], ALL_010, cliff_path())
        self.assertIn("android-v0.2.0", str(ctx.exception))
        self.assertIn("docs/releasing.md", str(ctx.exception))

    def test_manifest_version_without_tag_fails(self):
        repo = make_repo(self)
        repo.tag("web-extension-v0.1.0")
        repo.tag("installer-v0.1.0")
        with self.assertRaises(PlanError) as ctx:
            make_plan(repo.path, repo.cmap(), TODAY, ["2026.11.09"], ALL_010, cliff_path())
        self.assertIn("android", str(ctx.exception))

    def test_unrelated_and_prerelease_tags_are_ignored(self):
        repo = tagged(self)
        repo.commit("fix: x", touching="app/a.kt")
        repo.tag("android-v9.9.9-rc1")
        repo.tag("android-vX")
        repo.tag("0.0.3")
        c = comps(make_plan(repo.path, repo.cmap(), TODAY, ["2026.11.09"], ALL_010, cliff_path()))
        self.assertEqual((c["android"].current, c["android"].next), ("0.1.0", "0.1.1"))

    def test_git_only_date_tag_is_not_reused(self):
        repo = tagged(self)
        old = repo.head()
        repo.commit("fix: x", touching="app/a.kt")
        repo.tag("2026.11.20", at=old)
        plan = make_plan(repo.path, repo.cmap(), date(2026, 11, 20), [], ALL_010, cliff_path())
        self.assertEqual(plan.release_tag, "2026.11.20.2")

    def test_extension_only_publish_lists_only_its_tag(self):
        repo = tagged(self)
        repo.commit("feat(web): x", touching="web-extension/a.ts")
        plan = make_plan(repo.path, repo.cmap(), TODAY, ["2026.11.09"], ALL_010, cliff_path())
        ext_only_plan = plan_to_json(plan)
        self.assertEqual(ext_only_plan["commit"], repo.head())
        self.assertEqual(release_refs(ext_only_plan), ["refs/tags/web-extension-v0.2.0"])

    def test_render_notes_and_summary(self):
        repo = tagged(self)
        repo.commit("feat(app): shiny", touching="app/a.kt")
        plan = make_plan(repo.path, repo.cmap(), TODAY, ["2026.11.09"], ALL_010, cliff_path())
        notes = render_notes(plan, ALL_010)
        self.assertIn("| Android | 0.2.0 | new |", notes)
        self.assertIn("| Web extension | 0.1.0 | unchanged since 2026.11.09 |", notes)
        self.assertIn("| Installer | 0.1.0 | unchanged since 2026.11.09 |", notes)
        self.assertIn("## Android 0.2.0", notes)
        self.assertIn("shiny", notes)
        self.assertNotIn("## Web extension 0.1.0", notes)
        self.assertIn("SHA256SUMS", notes)
        self.assertIn("gh attestation verify", notes)
        summary = render_summary(plan)
        self.assertIn("2026.11.20", summary)
        self.assertIn("| Android | 0.1.0 | 0.2.0 | RELEASE | feat: 1 |", summary)
        self.assertIn("| Web extension | 0.1.0 | 0.1.0 | CARRY |", summary)

    def test_cli_plan_writes_plan_outputs_and_notes(self):
        repo = tagged(self)
        repo.commit("feat(web): x", touching="web-extension/a.ts")
        cmap_path = Path(repo.tmp) / "components.json"
        repo.cmap()
        work = Path(tempfile.mkdtemp(prefix="veles-plan-cli-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(work), ignore_errors=True))
        rows = [{"tagName": "2026.11.09", "isDraft": False, "isPrerelease": False, "publishedAt": "2026-11-09T10:00:00Z"}]
        (work / "releases.json").write_text(json.dumps(rows), encoding="utf-8")
        (work / "manifest.json").write_text(json.dumps(ALL_010), encoding="utf-8")
        out = work / "plan.json"
        gh_out = work / "github-output"
        code = main([
            "--map", str(cmap_path), "--repo", str(repo.path), "--git-cliff", str(cliff_path()),
            "plan", "--releases-json", str(work / "releases.json"), "--manifest-json", str(work / "manifest.json"),
            "--out", str(out), "--summary", str(work / "summary.md"), "--notes", str(work / "notes.md"),
            "--today", "2026-11-20", "--github-output", str(gh_out),
        ])
        self.assertEqual(code, 0)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["release_tag"], "2026.11.20")
        self.assertEqual(data["previous_release"], "2026.11.09")
        self.assertTrue(data["any_release"])
        self.assertEqual(
            data["components"]["web-extension"],
            {"current": "0.1.0", "next": "0.2.0", "action": "RELEASE", "tag": "web-extension-v0.2.0"},
        )
        outputs = gh_out.read_text(encoding="utf-8").splitlines()
        for line in ("android=CARRY", "android-version=0.1.0", "web-extension=RELEASE",
                     "web-extension-version=0.2.0", "installer=CARRY", "any-release=true",
                     "release-tag=2026.11.20"):
            self.assertIn(line, outputs)
        self.assertIn("| Web extension | 0.2.0 | new |", (work / "notes.md").read_text(encoding="utf-8"))
        self.assertIn("RELEASE", (work / "summary.md").read_text(encoding="utf-8"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["tags", "--plan-json", str(out)]), 0)
        self.assertEqual(buf.getvalue(), "refs/tags/web-extension-v0.2.0\n")

    def test_cli_require_release_fails_when_nothing_changed(self):
        repo = tagged(self)
        repo.commit("docs: x", touching="app/a.kt")
        cmap_path = Path(repo.tmp) / "components.json"
        repo.cmap()
        work = Path(repo.tmp)
        (work / "releases.json").write_text(json.dumps([
            {"tagName": "2026.11.09", "isDraft": False, "isPrerelease": False, "publishedAt": "2026-11-09T10:00:00Z"}
        ]), encoding="utf-8")
        (work / "manifest.json").write_text(json.dumps(ALL_010), encoding="utf-8")
        args = [
            "--map", str(cmap_path), "--repo", str(repo.path), "--git-cliff", str(cliff_path()),
            "plan", "--releases-json", str(work / "releases.json"), "--manifest-json", str(work / "manifest.json"),
            "--out", str(work / "plan.json"), "--today", "2026-11-20", "--github-output", str(work / "o"),
        ]
        self.assertEqual(main(args), 0)
        self.assertIn("any-release=false", (work / "o").read_text(encoding="utf-8").splitlines())
        self.assertEqual(main(args + ["--require-release"]), 1)

    def test_cli_rejects_manifest_from_another_release(self):
        repo = tagged(self)
        cmap_path = Path(repo.tmp) / "components.json"
        repo.cmap()
        work = Path(repo.tmp)
        (work / "releases.json").write_text(json.dumps([
            {"tagName": "2026.11.10", "isDraft": False, "isPrerelease": False, "publishedAt": "2026-11-10T10:00:00Z"}
        ]), encoding="utf-8")
        (work / "manifest.json").write_text(json.dumps(ALL_010), encoding="utf-8")
        code = main([
            "--map", str(cmap_path), "--repo", str(repo.path), "--git-cliff", str(cliff_path()),
            "plan", "--releases-json", str(work / "releases.json"), "--manifest-json", str(work / "manifest.json"),
            "--out", str(work / "plan.json"), "--today", "2026-11-20", "--github-output", str(work / "o"),
        ])
        self.assertEqual(code, 1)


class PlannerWithoutCliffTest(unittest.TestCase):
    def test_shallow_repository_is_refused(self):
        repo = tagged(self)
        repo.commit("fix: x", touching="app/a.kt")
        shallow_clone = repo.shallow_clone()
        with self.assertRaises(PlanError) as ctx:
            make_plan(shallow_clone, repo.cmap(), TODAY, [], ALL_010, Path("/nonexistent/git-cliff"))
        self.assertIn("shallow", str(ctx.exception))

    def test_next_release_tag(self):
        self.assertEqual(next_release_tag(date(2026, 11, 20), []), "2026.11.20")
        self.assertEqual(next_release_tag(date(2026, 11, 20), ["2026.11.19", "0.0.3"]), "2026.11.20")
        self.assertEqual(next_release_tag(date(2026, 11, 20), ["2026.11.20"]), "2026.11.20.2")
        self.assertEqual(next_release_tag(date(2026, 11, 20), ["2026.11.20", "2026.11.20.2"]), "2026.11.20.3")
        self.assertEqual(next_release_tag(date(2026, 11, 20), ["2026.11.20.3"]), "2026.11.20.4")

    def test_latest_combined_release(self):
        rows = [
            {"tagName": "0.0.3", "isDraft": False, "isPrerelease": False, "publishedAt": "2026-11-30T00:00:00Z"},
            {"tagName": "2026.11.09", "isDraft": True, "isPrerelease": False, "publishedAt": None},
            {"tagName": "2026.11.01", "isDraft": False, "isPrerelease": True, "publishedAt": "2026-11-02T00:00:00Z"},
            {"tagName": "2026.10.30", "isDraft": False, "isPrerelease": False, "publishedAt": "2026-10-30T00:00:00Z"},
            {"tagName": "2026.10.29.2", "isDraft": False, "isPrerelease": False, "publishedAt": "2026-10-29T12:00:00Z"},
        ]
        self.assertEqual(latest_combined_release(rows), "2026.10.30")
        self.assertIsNone(latest_combined_release(rows[:3]))
        self.assertIsNone(latest_combined_release([]))

    def test_release_refs_only_new_release_tags(self):
        plan = {
            "release_tag": "2026.11.20",
            "components": {
                "android": {"current": "0.1.0", "next": "0.1.0", "action": "CARRY", "tag": "android-v0.1.0"},
                "web-extension": {"current": "0.1.0", "next": "0.2.0", "action": "RELEASE", "tag": "web-extension-v0.2.0"},
                # bootstrap fill: re-released at an existing tag, which stays where it is
                "installer": {"current": "0.1.0", "next": "0.1.0", "action": "RELEASE", "tag": "installer-v0.1.0"},
            },
        }
        self.assertEqual(release_refs(plan), ["refs/tags/web-extension-v0.2.0"])

    def test_skip_message_names_the_gradle_task(self):
        self.assertIn("scriptsTest", gitfixture.SKIP_REASON)


if __name__ == "__main__":
    unittest.main()
