"""Tests for last_green.py: per-node evidence heads from recorded Actions API JSON.

No network: the fake Api serves JSON files under fixtures/last_green, and tests that
drive main() patch the module's Api, show_map and is_ancestor.
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import components  # noqa: E402
import last_green  # noqa: E402
from last_green import NodeEvidence, find_bases  # noqa: E402

PROJECT = os.path.dirname(SCRIPTS)
MAP_PATH = os.path.join(PROJECT, ".github", "components.json")
with open(MAP_PATH, encoding="utf-8") as _handle:
    MAP_TEXT = _handle.read()
CMAP = components.load_map(MAP_PATH)
NODES = list(CMAP["nodes"])
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "last_green")
REPO = "veles/veles"
NUMBER = 7
HEAD_REPO = "veles/veles"
HEAD_REF = "feature"


def always_ancestor(ancestor, descendant):
    return True


def current_map(sha):
    return MAP_TEXT


def find(api, show_map=current_map, is_ancestor=always_ancestor, cmap=CMAP):
    return find_bases(api, cmap, NUMBER, "head", HEAD_REPO, HEAD_REF, show_map, is_ancestor)


def write_json(directory, name, data):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    return path


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def page_name(base, page):
    return base + ".json" if page == 1 else "%s-%d.json" % (base, page)


class FakeApi(last_green.Api):
    """Api that serves recorded JSON pages from a fixture directory instead of HTTP."""

    def __init__(self, case, repo=REPO, budget=last_green.MAX_API_CALLS):
        last_green.Api.__init__(self, "https://api.github.invalid", "token", repo, budget=budget)
        self.root = case if os.path.isabs(case) else os.path.join(FIXTURES, case)
        self.requests = []

    def _load(self, base, page):
        path = os.path.join(self.root, page_name(base, page))
        if not os.path.exists(path):
            raise AssertionError("no fixture %s for %r" % (os.path.basename(path), base))
        return read_json(path)

    def get(self, path, params=None):
        self._charge()
        params = dict(params or {})
        page = int(params.get("page", 1))
        self.requests.append((path, params))
        self.last_headers = {}
        prefix = "/repos/%s/" % self.repo
        if not path.startswith(prefix):
            raise AssertionError("unexpected path %r" % path)
        rest = path[len(prefix):]
        match = re.match(r"^pulls/(\d+)/commits$", rest)
        if match:
            base = "commits"
        else:
            match = re.match(r"^actions/workflows/ci\.yml/runs$", rest)
            if match:
                base = "runs-" + params["head_sha"]
            else:
                match = re.match(r"^actions/runs/(\d+)/attempts/(\d+)/jobs$", rest)
                if not match:
                    raise AssertionError("unexpected path %r" % path)
                base = "jobs-%s-%s" % (match.group(1), match.group(2))
        if os.path.exists(os.path.join(self.root, page_name(base, page + 1))):
            self.last_headers = {"link": '<https://api.github.invalid/next>; rel="next"'}
        return self._load(base, page)


class ErrorApi(last_green.Api):
    """Api that fails every request like an HTTP error would."""

    def __init__(self, message="GET failed: HTTP 500"):
        last_green.Api.__init__(self, "https://api.github.invalid", "token", REPO)
        self.message = message

    def get(self, path, params=None):
        raise last_green.LastGreenError(self.message)


class RecordedResponse(object):
    """Minimal urlopen response for the production Api pagination regression."""

    def __init__(self, body, headers=None):
        self.body = json.dumps(body).encode("utf-8")
        self.headers = headers or {}

    def read(self):
        return self.body

    def close(self):
        pass


class Case(object):
    """A mutable copy of one recorded fixture directory."""

    def __init__(self, test, name):
        parent = tempfile.mkdtemp(prefix="last-green-case-")
        test.addCleanup(shutil.rmtree, parent, True)
        self.root = os.path.join(parent, name)
        shutil.copytree(os.path.join(FIXTURES, name), self.root)

    def read(self, name):
        return read_json(os.path.join(self.root, name))

    def write(self, name, data):
        write_json(self.root, name, data)

    def api(self, **kwargs):
        return FakeApi(self.root, **kwargs)


@contextlib.contextmanager
def chdir(path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@contextlib.contextmanager
def main_env(api, cwd=PROJECT, show_map=current_map, is_ancestor=always_ancestor, env=None):
    values = {
        "GITHUB_TOKEN": "token",
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_API_URL": "https://api.github.invalid",
        "CI_LAST_GREEN": "on",
    }
    values.update(env or {})
    with chdir(cwd), contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, values, clear=False))
        stack.enter_context(mock.patch.object(last_green, "Api", lambda *args, **kwargs: api))
        if show_map is not None:
            stack.enter_context(mock.patch.object(last_green, "show_map", show_map))
        if is_ancestor is not None:
            stack.enter_context(mock.patch.object(last_green, "is_ancestor", is_ancestor))
        yield


def main_args(head="head", out=None, notes=None):
    argv = [
        "--pr", str(NUMBER),
        "--head", head,
        "--head-repo", HEAD_REPO,
        "--head-ref", HEAD_REF,
    ]
    if out:
        argv.extend(["--out", out])
    if notes:
        argv.extend(["--notes-out", notes])
    return argv


def output_paths(test):
    root = tempfile.mkdtemp(prefix="last-green-out-")
    test.addCleanup(shutil.rmtree, root, True)
    return os.path.join(root, "bases.json"), os.path.join(root, "notes.json")


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd, check=True, stdout=subprocess.PIPE, universal_newlines=True,
    ).stdout.strip()


def write_file(root, path, text):
    target = os.path.join(root, path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(text)


def commit_file(root, path, text, message):
    write_file(root, path, text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def new_repo(test):
    """A temporary git repository that carries the real component map."""
    root = tempfile.mkdtemp(prefix="last-green-repo-")
    test.addCleanup(shutil.rmtree, root, True)
    git(root, "init", "-q", "-b", "master")
    write_file(root, ".github/components.json", MAP_TEXT)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: init")
    return root


def record(test, commits, runs=None, jobs=None):
    """Write a recorded API fixture directory for real git SHAs."""
    root = tempfile.mkdtemp(prefix="last-green-api-")
    test.addCleanup(shutil.rmtree, root, True)
    write_json(root, "commits.json", [{"sha": sha} for sha in commits])
    for sha, body in (runs or {}).items():
        write_json(root, "runs-%s.json" % sha, {"total_count": len(body), "workflow_runs": body})
    for (run_id, attempt), body in (jobs or {}).items():
        write_json(root, "jobs-%s-%s.json" % (run_id, attempt), {"total_count": len(body), "jobs": body})
    return root


def api_job(name, attempt=1, conclusion="success", job_id=11):
    return {
        "id": job_id,
        "run_attempt": attempt,
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
    }


def api_run(run_id, sha, attempt=1, conclusion="success", pull_requests=None):
    return {
        "id": run_id,
        "name": "CI",
        "event": "pull_request",
        "status": "completed",
        "conclusion": conclusion,
        "head_sha": sha,
        "head_branch": HEAD_REF,
        "head_repository": {"full_name": HEAD_REPO},
        "run_attempt": attempt,
        "pull_requests": [{"number": NUMBER}] if pull_requests is None else pull_requests,
    }


def android_jobs(attempt=1):
    return [
        api_job("android / " + name, attempt=attempt, job_id=index + 11)
        for index, name in enumerate(CMAP["nodes"]["android"]["jobs"])
    ]


def read_outputs(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class EligibilityTest(unittest.TestCase):
    def test_all_jobs_of_one_attempt_green(self):
        result = find(Case(self, "green").api())
        self.assertEqual(result["android"], NodeEvidence("c1", None))
        for node in NODES:
            if node != "android":
                self.assertEqual(result[node], NodeEvidence(None, "never passed"), node)

    def test_one_failed_job_not_green(self):
        case = Case(self, "green")
        jobs = case.read("jobs-1001-1.json")
        for job in jobs["jobs"]:
            if job["name"] == "android / unit-tests":
                job["conclusion"] = "failure"
        case.write("jobs-1001-1.json", jobs)
        self.assertEqual(find(case.api())["android"], NodeEvidence(None, "never passed"))

    def test_failed_jobs_only_rerun_not_green(self):
        result = find(Case(self, "rerun").api())
        self.assertEqual(result["android"], NodeEvidence(None, "never passed"))

    def test_results_not_combined_across_runs(self):
        result = find(Case(self, "split-runs").api())
        self.assertEqual(result["android"], NodeEvidence(None, "never passed"))

    def test_extra_job_not_green(self):
        case = Case(self, "green")
        jobs = case.read("jobs-1001-1.json")
        jobs["jobs"].append(api_job("android / docs", job_id=99))
        jobs["total_count"] = len(jobs["jobs"])
        case.write("jobs-1001-1.json", jobs)
        self.assertEqual(find(case.api())["android"], NodeEvidence(None, "never passed"))

    def test_duplicate_job_names_not_green(self):
        case = Case(self, "green")
        jobs = case.read("jobs-1001-1.json")
        jobs["jobs"][0]["conclusion"] = "failure"
        duplicate = dict(jobs["jobs"][0])
        duplicate["id"] = 999
        duplicate["conclusion"] = "success"
        jobs["jobs"].append(duplicate)
        self.assertFalse(
            last_green.node_is_green("android", CMAP["nodes"]["android"]["jobs"], jobs["jobs"])
        )
        case.write("jobs-1001-1.json", jobs)
        self.assertEqual(find(case.api())["android"], NodeEvidence(None, "never passed"))

    def test_null_head_repository_keeps_valid_evidence(self):
        case = Case(self, "green")
        runs = case.read("runs-c1.json")
        null_run = dict(runs["workflow_runs"][0])
        null_run["id"] = 9001
        null_run["head_repository"] = None
        null_run["head_branch"] = None
        runs["workflow_runs"] = [null_run] + runs["workflow_runs"]
        case.write("runs-c1.json", runs)
        case.write("jobs-9001-1.json", {"total_count": 0, "jobs": []})
        self.assertEqual(find(case.api())["android"], NodeEvidence("c1", None))

    def test_empty_pull_requests_without_repository_not_associated(self):
        case = Case(self, "green")
        runs = case.read("runs-c1.json")
        run = runs["workflow_runs"][0]
        run["pull_requests"] = []
        run["head_repository"] = None
        run["head_branch"] = None
        case.write("runs-c1.json", runs)
        self.assertEqual(find(case.api())["android"], NodeEvidence(None, "never passed"))

    def test_attempt_mismatch_rejects_run(self):
        case = Case(self, "green")
        jobs = case.read("jobs-1001-1.json")
        jobs["jobs"][0]["run_attempt"] = 2
        case.write("jobs-1001-1.json", jobs)
        self.assertEqual(find(case.api())["android"], NodeEvidence(None, "never passed"))

    def test_run_from_other_pr_ignored(self):
        case = Case(self, "green")
        runs = case.read("runs-c1.json")
        runs["workflow_runs"][0]["pull_requests"] = [{"number": NUMBER + 1}]
        case.write("runs-c1.json", runs)
        self.assertEqual(find(case.api())["android"], NodeEvidence(None, "never passed"))

    def test_run_for_another_sha_is_an_error(self):
        # The candidate's head_sha filter must never leak another commit's run into
        # the evidence; an inconsistent response fails closed (and main exits non-zero
        # so the workflow falls back).
        case = Case(self, "green")
        runs = case.read("runs-c1.json")
        runs["workflow_runs"][0]["head_sha"] = "c9"
        case.write("runs-c1.json", runs)
        with self.assertRaises(last_green.LastGreenError):
            find(case.api())
        out, notes = output_paths(self)
        with main_env(case.api()):
            code = last_green.main(main_args(out=out, notes=notes))
        self.assertNotEqual(code, 0)
        self.assertFalse(os.path.exists(out))
        self.assertFalse(os.path.exists(notes))

    def test_in_progress_job_with_success_conclusion_is_an_error(self):
        mutations = {
            "in progress": lambda job: job.update(status="in_progress", conclusion="success"),
            "missing status": lambda job: job.pop("status"),
        }
        for label, mutate in mutations.items():
            with self.subTest(job=label):
                case = Case(self, "green")
                jobs = case.read("jobs-1001-1.json")
                mutate(jobs["jobs"][0])
                case.write("jobs-1001-1.json", jobs)
                self.assertFalse(
                    last_green.node_is_green(
                        "android", CMAP["nodes"]["android"]["jobs"], jobs["jobs"]
                    )
                )
                with self.assertRaises(last_green.LastGreenError):
                    find(case.api())
                out, notes = output_paths(self)
                with main_env(case.api()):
                    code = last_green.main(main_args(out=out, notes=notes))
                self.assertNotEqual(code, 0)
                self.assertFalse(os.path.exists(out))

    def test_completed_run_with_completed_success_jobs_is_green(self):
        case = Case(self, "green")
        jobs = case.read("jobs-1001-1.json")
        self.assertTrue(
            all(job["status"] == "completed" for job in jobs["jobs"]),
            "the green fixture must report completed jobs",
        )
        self.assertEqual(find(case.api())["android"], NodeEvidence("c1", None))

    def test_fork_pr_with_empty_pull_requests_matches_by_repo_and_ref(self):
        case = Case(self, "green")
        runs = case.read("runs-c1.json")
        runs["workflow_runs"][0]["pull_requests"] = []
        case.write("runs-c1.json", runs)
        self.assertEqual(find(case.api())["android"].base, "c1")
        for field, value in (
            ("head_branch", "other"),
            ("head_repository", {"full_name": "fork/veles"}),
        ):
            with self.subTest(field=field):
                case = Case(self, "green")
                runs = case.read("runs-c1.json")
                runs["workflow_runs"][0]["pull_requests"] = []
                runs["workflow_runs"][0][field] = value
                case.write("runs-c1.json", runs)
                self.assertEqual(find(case.api())["android"], NodeEvidence(None, "never passed"))

    def test_cancelled_superseded_run_not_green(self):
        case = Case(self, "green")
        runs = case.read("runs-c1.json")
        runs["workflow_runs"][0]["conclusion"] = "cancelled"
        case.write("runs-c1.json", runs)
        jobs = case.read("jobs-1001-1.json")
        for job in jobs["jobs"]:
            job["conclusion"] = "cancelled"
        case.write("jobs-1001-1.json", jobs)
        self.assertEqual(find(case.api())["android"], NodeEvidence(None, "never passed"))

    def test_non_success_conclusions_not_green(self):
        for conclusion in ("failure", "cancelled", "skipped", "neutral", "timed_out",
                           "action_required", None):
            with self.subTest(conclusion=conclusion):
                case = Case(self, "green")
                jobs = case.read("jobs-1001-1.json")
                for job in jobs["jobs"]:
                    if job["name"] == "android / lint":
                        job["conclusion"] = conclusion
                case.write("jobs-1001-1.json", jobs)
                self.assertEqual(find(case.api())["android"], NodeEvidence(None, "never passed"))

    def test_commits_and_jobs_are_paginated(self):
        api = Case(self, "paged").api()
        self.assertEqual(find(api)["android"].base, "c1")
        # commits page 1/2, runs for the newest head, jobs page 1/2, then the older
        # candidate's runs (nodes without evidence keep looking).
        self.assertEqual(len(api.requests), 6)
        self.assertEqual(
            [params.get("page") for _, params in api.requests], [1, 2, 1, 1, 2, 1]
        )


class ApiPaginationTest(unittest.TestCase):
    """The production Api (not the fake) must treat header names case-insensitively."""

    def test_lowercase_link_header_reads_every_job_page(self):
        run = api_run(1001, "c1")
        urls = []

        def handle(request, timeout=None):
            url = request.full_url
            urls.append(url)
            if "/pulls/7/commits" in url:
                return RecordedResponse([{"sha": "c1"}])
            if "/actions/workflows/ci.yml/runs" in url:
                return RecordedResponse({"workflow_runs": [run]})
            if "/actions/runs/1001/attempts/1/jobs" in url:
                if "page=2" in url:
                    return RecordedResponse({"jobs": [api_job("android / docs", job_id=99)]})
                return RecordedResponse(
                    {"jobs": android_jobs()},
                    {"link": '<https://api.github.invalid/next>; rel="next"'},
                )
            raise AssertionError("unexpected request %r" % url)

        api = last_green.Api("https://api.github.invalid", "token", REPO)
        with mock.patch("last_green.urlopen", side_effect=handle):
            result = find(api)
        self.assertEqual(result["android"], NodeEvidence(None, "never passed"))
        self.assertEqual(len(urls), 4)
        self.assertTrue(any("page=2" in url for url in urls))


class AncestryTest(unittest.TestCase):
    def test_normal_push_uses_previous_head(self):
        result = find(Case(self, "ancestors").api(), is_ancestor=always_ancestor)
        self.assertEqual(result["android"], NodeEvidence("c1", None))

    def test_force_push_without_green_ancestor_gives_none(self):
        result = find(Case(self, "ancestors").api(), is_ancestor=lambda a, b: False)
        self.assertEqual(result["android"], NodeEvidence(None, "never passed"))

    def test_force_push_with_surviving_green_ancestor(self):
        result = find(
            Case(self, "ancestors").api(),
            is_ancestor=lambda ancestor, descendant: ancestor == "c0",
        )
        self.assertEqual(result["android"], NodeEvidence("c0", None))


class CompatibilityTest(unittest.TestCase):
    def map_text(self, mutate):
        data = json.loads(MAP_TEXT)
        mutate(data)
        return json.dumps(data, indent=2)

    def test_equivalent_map_keeps_evidence(self):
        text = json.dumps(json.loads(MAP_TEXT), indent=4, sort_keys=True)
        self.assertEqual(find(Case(self, "green").api(), show_map=lambda sha: text)["android"],
                         NodeEvidence("c1", None))

    def test_expanded_node_paths_invalidates_evidence(self):
        def mutate(data):
            data["nodes"]["android"]["paths"].append("docs/")

        text = self.map_text(mutate)
        result = find(Case(self, "green").api(), show_map=lambda sha: text)
        self.assertEqual(result["android"], NodeEvidence(None, "incompatible map"))

    def test_removed_override_invalidates_all_nodes(self):
        def mutate(data):
            data["global-overrides"] = {}

        text = self.map_text(mutate)
        result = find(Case(self, "mixed").api(), show_map=lambda sha: text)
        self.assertEqual(result["android"], NodeEvidence(None, "incompatible map"))
        self.assertEqual(result["web-extension"], NodeEvidence(None, "incompatible map"))

    def test_changed_override_membership_invalidates(self):
        def mutate(data):
            data["global-overrides"][".github/components.json"] = ["rust"]

        text = self.map_text(mutate)
        result = find(Case(self, "green").api(), show_map=lambda sha: text)
        self.assertEqual(result["android"], NodeEvidence(None, "incompatible map"))

    def test_non_object_map_root_is_incompatible(self):
        for text in ("[]", "null"):
            with self.subTest(map_text=text):
                result = find(Case(self, "green").api(), show_map=lambda sha: text)
                self.assertEqual(result["android"], NodeEvidence(None, "incompatible map"))

    def test_renamed_job_invalidates(self):
        data = json.loads(MAP_TEXT)
        data["nodes"]["android"]["jobs"] = [
            "lint", "unit-tests", "release-hook-guard", "instrumented-tests-v2"
        ]
        text = json.dumps(data, indent=2)
        renamed = components.parse_map(text)
        self.assertFalse(
            last_green.node_is_green(
                "android", data["nodes"]["android"]["jobs"],
                Case(self, "green").read("jobs-1001-1.json")["jobs"],
            )
        )
        result = find(
            Case(self, "green").api(), show_map=lambda sha: text, cmap=renamed,
        )
        self.assertEqual(result["android"], NodeEvidence(None, "never passed"))


class OutcomeTest(unittest.TestCase):
    def test_never_passed_node_gives_none_without_touching_others(self):
        result = find(Case(self, "green").api())
        self.assertEqual(result["android"], NodeEvidence("c1", None))
        for node in NODES:
            if node != "android":
                self.assertEqual(result[node], NodeEvidence(None, "never passed"), node)

    def test_failed_then_fixed_node_uses_fixed_head(self):
        result = find(Case(self, "fixed").api())
        self.assertEqual(result["android"], NodeEvidence("c1", None))

    def test_candidate_bound(self):
        commits = ["c%d" % index for index in range(last_green.MAX_CANDIDATES + 5)]
        heads = last_green.candidate_heads(commits, "head", always_ancestor)
        self.assertEqual(len(heads), last_green.MAX_CANDIDATES)
        self.assertEqual(heads, list(reversed(commits))[:last_green.MAX_CANDIDATES])
        self.assertEqual(
            last_green.candidate_heads(["c0", "c1", "head"], "head", lambda a, b: a != "c1"),
            ["c0"],
        )

    def test_call_bound_leaves_remaining_nodes_without_evidence(self):
        self.assertFalse(issubclass(last_green.BudgetExhausted, last_green.LastGreenError))
        api = Case(self, "mixed").api(budget=3)
        out, notes = output_paths(self)
        with main_env(api):
            code = last_green.main(main_args(out=out, notes=notes))
        self.assertEqual(code, 0)
        self.assertEqual(api.calls, 3)
        bases = read_outputs(out)
        self.assertEqual(bases["android"], "c1")
        self.assertIsNone(bases["web-extension"])
        self.assertEqual(set(bases), set(NODES))
        written_notes = read_outputs(notes)
        self.assertNotIn("android", written_notes)
        self.assertEqual(written_notes["web-extension"], "never passed")

    def test_budget_exhausted_during_job_pagination_never_greens_run(self):
        api = Case(self, "paged").api(budget=4)
        result = find(api)
        self.assertEqual(api.calls, 4)
        self.assertEqual(result["android"], NodeEvidence(None, "never passed"))

    def test_budget_exhaustion_marks_unresolved_never_passed(self):
        data = json.loads(MAP_TEXT)
        data["global-overrides"] = {}
        text = json.dumps(data, indent=2)
        api = Case(self, "green").api(budget=3)
        result = find(api, show_map=lambda sha: text)
        self.assertEqual(api.calls, 3)
        # android was green only under an incompatible map; exhaustion discards
        # that note and the mandated outcome is never passed.
        self.assertEqual(result["android"], NodeEvidence(None, "never passed"))
        self.assertEqual(result["web-extension"], NodeEvidence(None, "never passed"))

    def test_api_error_exits_nonzero(self):
        out, notes = output_paths(self)
        with main_env(ErrorApi()):
            code = last_green.main(main_args(out=out, notes=notes))
        self.assertNotEqual(code, 0)
        self.assertFalse(os.path.exists(out))

    def test_rate_limit_and_missing_permission_exit_nonzero(self):
        variants = (
            ("rate limit", b'{"message": "API rate limit exceeded"}'),
            ("missing permission", b'{"message": "Resource not accessible by integration"}'),
        )
        for label, body in variants:
            with self.subTest(problem=label):
                out, notes = output_paths(self)
                error = HTTPError(
                    "https://api.github.invalid/repos/veles/veles/pulls/7/commits",
                    403, "Forbidden", {}, io.BytesIO(body),
                )
                values = {
                    "GITHUB_TOKEN": "token",
                    "GITHUB_REPOSITORY": REPO,
                    "GITHUB_API_URL": "https://api.github.invalid",
                    "CI_LAST_GREEN": "on",
                }
                with chdir(PROJECT), \
                        mock.patch.dict(os.environ, values, clear=False), \
                        mock.patch("last_green.urlopen", side_effect=error):
                    code = last_green.main(main_args(out=out, notes=notes))
                self.assertNotEqual(code, 0)
                self.assertFalse(os.path.exists(out))

    def test_malformed_structure_exits_nonzero(self):
        def missing_list(case):
            case.write("runs-c1.json", {"total_count": 1})

        def missing_attempt(case):
            runs = case.read("runs-c1.json")
            del runs["workflow_runs"][0]["run_attempt"]
            case.write("runs-c1.json", runs)

        def missing_event(case):
            runs = case.read("runs-c1.json")
            del runs["workflow_runs"][0]["event"]
            case.write("runs-c1.json", runs)

        def malformed_pull_request(case):
            runs = case.read("runs-c1.json")
            runs["workflow_runs"][0]["pull_requests"] = [None]
            case.write("runs-c1.json", runs)

        def missing_conclusion(case):
            jobs = case.read("jobs-1001-1.json")
            del jobs["jobs"][0]["conclusion"]
            case.write("jobs-1001-1.json", jobs)

        def missing_job_attempt(case):
            jobs = case.read("jobs-1001-1.json")
            del jobs["jobs"][0]["run_attempt"]
            case.write("jobs-1001-1.json", jobs)

        def malformed_repository(case):
            runs = case.read("runs-c1.json")
            runs["workflow_runs"][0]["head_repository"] = "veles/veles"
            case.write("runs-c1.json", runs)

        def absent_repository(case):
            runs = case.read("runs-c1.json")
            del runs["workflow_runs"][0]["head_repository"]
            case.write("runs-c1.json", runs)

        for label, mutate in (
            ("missing workflow_runs", missing_list),
            ("missing run_attempt", missing_attempt),
            ("missing event", missing_event),
            ("pull_requests entry is null", malformed_pull_request),
            ("missing job conclusion", missing_conclusion),
            ("missing job run_attempt", missing_job_attempt),
            ("head_repository is a string", malformed_repository),
            ("missing head_repository", absent_repository),
        ):
            with self.subTest(problem=label):
                case = Case(self, "green")
                mutate(case)
                out, notes = output_paths(self)
                with main_env(case.api()):
                    code = last_green.main(main_args(out=out, notes=notes))
                self.assertNotEqual(code, 0)
                self.assertFalse(os.path.exists(out))

    def test_notes_distinguish_never_passed_from_incompatible_map(self):
        data = json.loads(MAP_TEXT)
        data["global-overrides"] = {}
        text = json.dumps(data, indent=2)
        out, notes = output_paths(self)
        with main_env(Case(self, "green").api(), show_map=lambda sha: text):
            code = last_green.main(main_args(out=out, notes=notes))
        self.assertEqual(code, 0)
        self.assertEqual(set(read_outputs(out).values()), {None})
        written_notes = read_outputs(notes)
        self.assertEqual(written_notes["android"], "incompatible map")
        self.assertEqual(written_notes["web-extension"], "never passed")

    def test_kill_switch_returns_all_none_without_api_calls(self):
        out, notes = output_paths(self)
        values = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_API_URL": "https://api.github.invalid",
            "CI_LAST_GREEN": "off",
        }
        with chdir(PROJECT), \
                mock.patch.dict(os.environ, values, clear=False), \
                mock.patch.object(
                    last_green, "Api",
                    side_effect=AssertionError("the kill switch must not call the API"),
                ):
            code = last_green.main(main_args(out=out, notes=notes))
        self.assertEqual(code, 0)
        self.assertEqual(read_outputs(out), {node: None for node in NODES})
        self.assertEqual(read_outputs(notes), {node: "never passed" for node in NODES})


class IntegrationTest(unittest.TestCase):
    """last_green output consumed unchanged by components.py changed --node-bases."""

    def select(self, root, base, head, bases, notes):
        out = os.path.join(root, "outputs.txt")
        code = components.main(
            [
                "--map", os.path.join(root, ".github", "components.json"),
                "changed",
                "--base", base,
                "--head", head,
                "--node-head", head,
                "--node-bases", bases,
                "--evidence-notes", notes,
                "--github-output", out,
            ],
            cwd=root,
        )
        self.assertEqual(code, 0)
        with open(out, encoding="utf-8") as handle:
            return {
                name: value == "true"
                for name, value in (line.split("=", 1) for line in handle.read().split())
            }

    def test_last_green_output_files_drive_selection(self):
        root = new_repo(self)
        base = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "-b", "pr")
        green = commit_file(root, "protocol/v1/limits.json", "{}\n", "feature")
        head = commit_file(root, "docs/ci.md", "docs\n", "docs only")
        api_root = record(
            self,
            [green, head],
            runs={green: [api_run(1001, green)], head: []},
            jobs={(1001, 1): android_jobs()},
        )
        out, notes = output_paths(self)
        with main_env(FakeApi(api_root), cwd=root, show_map=None, is_ancestor=None):
            code = last_green.main(main_args(head=head, out=out, notes=notes))
        self.assertEqual(code, 0)
        bases = read_outputs(out)
        self.assertEqual(bases["android"], green)
        self.assertIsNone(bases["protocol"])
        self.assertEqual(set(bases), set(NODES))
        written_notes = read_outputs(notes)
        self.assertNotIn("android", written_notes)
        self.assertEqual(written_notes["protocol"], "never passed")
        outputs = self.select(root, base, head, out, notes)
        self.assertFalse(outputs["android"])
        self.assertTrue(outputs["protocol"])

    def test_master_movement_only_does_not_select(self):
        root = new_repo(self)
        base = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "-b", "pr")
        green = commit_file(root, "web-extension/src/feature.ts", "export\n", "web extension")
        git(root, "checkout", "-q", "master")
        commit_file(root, "app/master.kt", "class Master\n", "master movement")
        git(root, "checkout", "-q", "pr")
        head = commit_file(root, "docs/ci.md", "docs\n", "docs only")
        self.assertEqual(git(root, "merge-base", "master", "pr"), base)
        api_root = record(
            self,
            [green, head],
            runs={green: [api_run(1001, green)], head: []},
            jobs={(1001, 1): [api_job("web-extension / " + name, job_id=index + 11)
                              for index, name in enumerate(CMAP["nodes"]["web-extension"]["jobs"])]},
        )
        out, notes = output_paths(self)
        with main_env(FakeApi(api_root), cwd=root, show_map=None, is_ancestor=None):
            code = last_green.main(main_args(head=head, out=out, notes=notes))
        self.assertEqual(code, 0)
        self.assertEqual(read_outputs(out)["web-extension"], green)
        outputs = self.select(root, base, head, out, notes)
        self.assertFalse(outputs["web-extension"])
        self.assertFalse(outputs["android"])

    def test_master_merged_into_branch_selects_touched_nodes(self):
        root = new_repo(self)
        base = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "-b", "pr")
        green = commit_file(root, "web-extension/src/feature.ts", "export\n", "web extension")
        git(root, "checkout", "-q", "master")
        master_tip = commit_file(root, "app/master.kt", "class Master\n", "master movement")
        git(root, "checkout", "-q", "pr")
        git(root, "merge", "-q", "--no-ff", "master", "-m", "merge master")
        head = git(root, "rev-parse", "HEAD")
        self.assertEqual(git(root, "merge-base", "master", "pr"), master_tip)
        api_root = record(
            self,
            [green, master_tip, head],
            runs={green: [api_run(1001, green)], master_tip: []},
            jobs={(1001, 1): android_jobs()},
        )
        out, notes = output_paths(self)
        with main_env(FakeApi(api_root), cwd=root, show_map=None, is_ancestor=None):
            code = last_green.main(main_args(head=head, out=out, notes=notes))
        self.assertEqual(code, 0)
        self.assertEqual(read_outputs(out)["android"], green)
        outputs = self.select(root, base, head, out, notes)
        self.assertTrue(outputs["android"])
        self.assertTrue(outputs["web-extension"])

    def test_failed_node_then_docs_only_push_runs_node(self):
        root = new_repo(self)
        base = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "-b", "pr")
        failed = commit_file(root, "app/feature.kt", "class Feature\n", "feature")
        head = commit_file(root, "docs/ci.md", "docs\n", "docs only")
        failing_jobs = [
            api_job("android / " + name,
                    conclusion="failure" if name == "lint" else "success", job_id=index + 11)
            for index, name in enumerate(CMAP["nodes"]["android"]["jobs"])
        ]
        api_root = record(
            self,
            [failed, head],
            runs={failed: [api_run(1001, failed, conclusion="failure")], head: []},
            jobs={(1001, 1): failing_jobs},
        )
        out, notes = output_paths(self)
        with main_env(FakeApi(api_root), cwd=root, show_map=None, is_ancestor=None):
            code = last_green.main(main_args(head=head, out=out, notes=notes))
        self.assertEqual(code, 0)
        self.assertIsNone(read_outputs(out)["android"])
        outputs = self.select(root, base, head, out, notes)
        self.assertTrue(outputs["android"])
        self.assertFalse(outputs["web-extension"])


if __name__ == "__main__":
    unittest.main()
