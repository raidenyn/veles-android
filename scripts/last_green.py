#!/usr/bin/env python3
"""Find each component node's last green pull-request head from Actions evidence.

Stdlib only; Python 3.8 compatible. For every node in `.github/components.json` it
reports the newest ancestor of the pull request head whose `ci.yml` `pull_request`
run had one complete attempt with all of the node's jobs green and no extra job,
provided the map's normalised selection definition at that head still matches the
current one. `components.py changed --node-bases/--evidence-notes` consumes the two
output files unchanged.

Bounds: at most `MAX_CANDIDATES` evidence heads and `MAX_API_CALLS` requests.
Running out of budget is not an error (nodes without evidence fall back to the merge
base); an API error, rate limit, malformed response or missing permission is.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from typing import Callable, Dict, Iterator, List, NamedTuple, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # `python3 -I` does not add it
import components  # noqa: E402

MAX_CANDIDATES = 20
MAX_API_CALLS = 100
PAGE_SIZE = 100
MAX_PAGES = 100
MAP_PATH = os.path.join(".github", "components.json")
LINK_NEXT_RE = re.compile(r'rel="next"')
USER_AGENT = "veles-last-green"


class LastGreenError(Exception):
    """An API error, rate limit, malformed response or missing permission."""


class BudgetExhausted(Exception):
    """The API call budget ran out; deliberately not a LastGreenError (exit 0)."""


class NodeEvidence(NamedTuple):
    base: Optional[str]
    note: Optional[str]


class Api(object):
    """Minimal GitHub REST client: counts calls and fails closed on bad responses."""

    def __init__(self, base_url: str, token: str, repo: str, budget: int = MAX_API_CALLS):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.repo = repo
        self.budget = budget
        self.calls = 0
        self.last_headers = {}

    def _charge(self):
        if self.calls >= self.budget:
            raise BudgetExhausted("GitHub API call budget of %d exhausted" % self.budget)
        self.calls += 1

    def get(self, path: str, params: Optional[dict] = None) -> object:
        self._charge()
        url = path if path.startswith("http") else self.base_url + path
        if params:
            url += "?" + urlencode(sorted(params.items()))
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + self.token,
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            response = urlopen(request, timeout=30)
            try:
                body = response.read()
                headers = dict(response.headers)
            finally:
                response.close()
        except HTTPError as error:
            detail = ""
            try:
                detail = " (%s)" % error.read().decode("utf-8", "replace")[:200]
            except OSError:
                pass
            raise LastGreenError(
                "GET %s failed: HTTP %d %s%s" % (path, error.code, error.reason, detail)
            )
        except URLError as error:
            raise LastGreenError("GET %s failed: %s" % (path, error.reason))
        self.last_headers = headers
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise LastGreenError("GET %s returned invalid JSON" % path)
        if not isinstance(data, (dict, list)):
            raise LastGreenError("GET %s returned an unexpected structure" % path)
        return data

    def pages(
        self, path: str, params: Optional[dict] = None, key: Optional[str] = None
    ) -> Iterator[dict]:
        page = 1
        while True:
            query = dict(params or {})
            query["per_page"] = PAGE_SIZE
            query["page"] = page
            data = self.get(path, query)
            if key:
                if not isinstance(data, dict):
                    raise LastGreenError("GET %s returned an unexpected structure" % path)
                items = data.get(key)
            else:
                items = data
            if not isinstance(items, list):
                raise LastGreenError("GET %s returned an unexpected structure" % path)
            for item in items:
                yield item
            if not LINK_NEXT_RE.search(self.last_headers.get("Link", "")):
                return
            page += 1
            if page > MAX_PAGES:
                raise LastGreenError("GET %s exceeded %d pages" % (path, MAX_PAGES))


def candidate_heads(
    commits_oldest_first: List[str], head: str, is_ancestor: Callable[[str, str], bool]
) -> List[str]:
    """Evidence heads newest first: the head itself and non-ancestors are skipped."""
    candidates = []
    for sha in reversed(list(commits_oldest_first)):
        if not sha or sha == head:
            continue
        if len(candidates) >= MAX_CANDIDATES:
            break
        if is_ancestor(sha, head):
            candidates.append(sha)
    return candidates


def run_belongs_to_pr(run: dict, number: int, head_repo: str, head_ref: str) -> bool:
    """Whether a run belongs to the PR, including the fork rule (empty pull_requests)."""
    pulls = run.get("pull_requests")
    if not isinstance(pulls, list):
        return False
    if pulls:
        return any(isinstance(pr, dict) and pr.get("number") == number for pr in pulls)
    repo = run.get("head_repository")
    full_name = repo.get("full_name") if isinstance(repo, dict) else None
    return full_name == head_repo and run.get("head_branch") == head_ref


def node_is_green(node: str, expected: List[str], jobs: List[dict]) -> bool:
    """All expected `<node> / ...` jobs succeeded in the attempt and no extra exists."""
    if not expected:
        return False
    prefix = node + " / "
    actual = {}
    for job in jobs:
        if not isinstance(job, dict):
            continue
        name = job.get("name")
        if not isinstance(name, str) or not name.startswith(prefix):
            continue
        actual[name[len(prefix):]] = job.get("conclusion")
    if set(actual) != set(expected):
        return False
    return all(actual[name] == "success" for name in expected)


def _validate_run(run):
    if not isinstance(run, dict):
        raise LastGreenError("workflow run is not an object")
    if not isinstance(run.get("id"), int) or not isinstance(run.get("run_attempt"), int):
        raise LastGreenError("workflow run lacks id or run_attempt")
    if not isinstance(run.get("status"), str) or not isinstance(run.get("pull_requests"), list):
        raise LastGreenError("workflow run lacks status or pull_requests")


def _map_specs(text, nodes):
    """Normalised specs at the evidence head; None means unusable for every node."""
    if text is None:
        return None
    try:
        cmap = components.parse_map(text)
    except (ValueError, TypeError):
        return None
    specs = {}
    for node in nodes:
        try:
            specs[node] = components.selection_spec(cmap, node)
        except (KeyError, TypeError, ValueError):
            specs[node] = None
    return specs


def _jobs_path(repo: str, run_id: int, attempt: int) -> str:
    return "/repos/%s/actions/runs/%d/attempts/%d/jobs" % (repo, run_id, attempt)


def find_bases(
    api: Api,
    cmap: dict,
    number: int,
    head: str,
    head_repo: str,
    head_ref: str,
    show_map: Callable[[str], Optional[str]],
    is_ancestor: Callable[[str, str], bool],
) -> Dict[str, NodeEvidence]:
    """Last usable green head per node; never combines runs or attempts."""
    nodes = list(cmap["nodes"])
    current = {node: components.selection_spec(cmap, node) for node in nodes}
    evidence = {}
    incompatible = set()
    try:
        commits = []
        for item in api.pages("/repos/%s/pulls/%d/commits" % (api.repo, number)):
            if not isinstance(item, dict) or not isinstance(item.get("sha"), str):
                raise LastGreenError("pull request %d has a malformed commit" % number)
            commits.append(item["sha"])
        for sha in candidate_heads(commits, head, is_ancestor):
            if len(evidence) == len(nodes):
                break
            runs = list(api.pages(
                "/repos/%s/actions/workflows/ci.yml/runs" % api.repo,
                {"event": "pull_request", "head_sha": sha},
                key="workflow_runs",
            ))
            accepted = []
            for run in runs:
                _validate_run(run)
                if run["status"] != "completed" or run.get("event") != "pull_request":
                    continue
                if not run_belongs_to_pr(run, number, head_repo, head_ref):
                    continue
                accepted.append(run)
            if not accepted:
                continue
            specs = _map_specs(show_map(sha), nodes)
            for run in accepted:
                jobs = list(
                    api.pages(_jobs_path(api.repo, run["id"], run["run_attempt"]), key="jobs")
                )
                if not all(
                    isinstance(job, dict) and isinstance(job.get("name"), str) for job in jobs
                ):
                    raise LastGreenError("run %d has a malformed job" % run["id"])
                if any(job.get("run_attempt") != run["run_attempt"] for job in jobs):
                    continue
                for node in nodes:
                    if node in evidence:
                        continue
                    if not node_is_green(node, cmap["nodes"][node].get("jobs", []), jobs):
                        continue
                    if specs is not None and specs.get(node) == current[node]:
                        evidence[node] = NodeEvidence(sha, None)
                    else:
                        incompatible.add(node)
    except BudgetExhausted:
        pass
    return {
        node: evidence[node]
        if node in evidence
        else NodeEvidence(None, "incompatible map" if node in incompatible else "never passed")
        for node in nodes
    }


def _git(args):
    return subprocess.run(
        ["git"] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )


def show_map(sha: str) -> Optional[str]:
    """The component map text at a commit, or None when it is unavailable."""
    if not isinstance(sha, str) or not sha or sha.startswith("-"):
        return None
    result = _git(["show", "%s:.github/components.json" % sha])
    return result.stdout if result.returncode == 0 else None


def is_ancestor(ancestor: str, descendant: str) -> bool:
    """True when git confirms the commit relationship; unfetched commits are not ancestors."""
    if not ancestor or not descendant or ancestor.startswith("-") or descendant.startswith("-"):
        return False
    return _git(["merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0


def _write_json(path, data, stdout_when_missing=False):
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if path:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    elif stdout_when_missing:
        sys.stdout.write(text)


def _report(evidence, out_path, notes_path):
    _write_json(
        out_path, {node: value.base for node, value in evidence.items()}, stdout_when_missing=True
    )
    if notes_path:
        _write_json(
            notes_path, {node: value.note for node, value in evidence.items() if value.note}
        )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="last_green.py")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--head-repo", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--notes-out", default=None)
    args = parser.parse_args(argv)
    try:
        cmap = components.load_map(MAP_PATH)
    except (ValueError, OSError) as error:
        print("last_green: invalid component map: %s" % error, file=sys.stderr)
        return 1
    nodes = list(cmap["nodes"])
    if os.environ.get("CI_LAST_GREEN", "").strip().lower() == "off":
        return _report(
            {node: NodeEvidence(None, "never passed") for node in nodes},
            args.out,
            args.notes_out,
        )
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    base_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    if not token or not repo:
        print(
            "last_green: GITHUB_TOKEN and GITHUB_REPOSITORY are required", file=sys.stderr
        )
        return 1
    try:
        api = Api(base_url, token, repo, budget=MAX_API_CALLS)
        evidence = find_bases(
            api, cmap, args.pr, args.head, args.head_repo, args.head_ref,
            show_map, is_ancestor,
        )
    except LastGreenError as error:
        print("last_green: %s" % error, file=sys.stderr)
        return 2
    except BudgetExhausted:
        evidence = {node: NodeEvidence(None, "never passed") for node in nodes}
    return _report(evidence, args.out, args.notes_out)


if __name__ == "__main__":
    sys.exit(main())
