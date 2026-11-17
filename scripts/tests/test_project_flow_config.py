"""Tests for project_flow.py config loading, validation and error mapping. No network."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_flow  # noqa: E402
from project_flow import GhError, Stop  # noqa: E402
from project_flow_fake import FakeTransport  # noqa: E402

PROJECT = os.path.dirname(SCRIPTS)
DEFAULTS = os.path.join(PROJECT, ".github", "project-flow.json")
WORKFLOW = os.path.join(PROJECT, ".github", "workflows", "project-flow.yml")


def default_raw():
    with open(DEFAULTS, encoding="utf-8") as handle:
        return json.load(handle)


class ConfigTest(unittest.TestCase):
    def write(self, raw):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.addCleanup(os.unlink, handle.name)
        json.dump(raw, handle)
        handle.close()
        return handle.name

    def test_defaults_file_loads(self):
        config = project_flow.load_config(DEFAULTS)
        self.assertEqual(config.roles["in_review"], "In Review")
        self.assertEqual(config.status_field, "Status")
        self.assertEqual(config.order_field, "Implementation order")
        self.assertEqual(config.blocked_label, "blocked")

    def test_unknown_role_rejected(self):
        raw = default_raw()
        raw["roles"]["wip"] = "WIP"
        errors = project_flow.validate_config(raw)
        self.assertTrue(any("roles.wip" in e for e in errors), errors)

    def test_missing_role_rejected(self):
        raw = default_raw()
        del raw["roles"]["done"]
        errors = project_flow.validate_config(raw)
        self.assertTrue(any("roles.done" in e for e in errors), errors)

    def test_identifier_rejected(self):
        for value in ("https://github.com/users/x/projects/3", "PVT_kwHOAbc12345", "#12", "3"):
            raw = default_raw()
            raw["status_field"] = value
            self.assertEqual(len(project_flow.validate_config(raw)), 1, value)

    def test_surrounding_whitespace_rejected(self):
        for value in (" Status", "Status ", "Status\n"):
            raw = default_raw()
            raw["status_field"] = value
            self.assertEqual(len(project_flow.validate_config(raw)), 1, repr(value))
            raw = default_raw()
            raw["roles"]["ready"] = value
            self.assertEqual(len(project_flow.validate_config(raw)), 1, repr(value))

    def test_identifier_checks_match_whole_value(self):
        for value in ("PVT_kwHOAbc12345 board", "12 items", "In Review 2"):
            raw = default_raw()
            raw["status_field"] = value
            self.assertEqual(project_flow.validate_config(raw), [], value)

    def test_committed_config_names_the_environment(self):
        self.assertEqual(project_flow.load_config(DEFAULTS).environment, "project-actions")

    def test_environment_optional(self):
        raw = default_raw()
        raw.pop("environment", None)
        self.assertEqual(project_flow.validate_config(raw), [])
        self.assertIsNone(project_flow.load_config(self.write(raw)).environment)

    def test_environment_valid(self):
        for value in ("project-actions", "prod_1", "a.b", "Env-2.x"):
            raw = default_raw()
            raw["environment"] = value
            self.assertEqual(project_flow.validate_config(raw), [], value)
            self.assertEqual(project_flow.load_config(self.write(raw)).environment, value)

    def test_environment_invalid_characters_rejected(self):
        for value in ("project actions", "a/b", "a?b", "a%2Fb", "env\n", "ok#", ".", "..", "", " x"):
            raw = default_raw()
            raw["environment"] = value
            errors = project_flow.validate_config(raw)
            self.assertEqual(len(errors), 1, (value, errors))
            self.assertTrue(errors[0].startswith("environment: "), errors)

    def test_environment_wrong_type_rejected(self):
        for value in (None, 3, ["project-actions"], {"name": "project-actions"}, True):
            raw = default_raw()
            raw["environment"] = value
            errors = project_flow.validate_config(raw)
            self.assertEqual(len(errors), 1, (value, errors))
            self.assertTrue(errors[0].startswith("environment: "), errors)

    def test_environment_identifier_rejected(self):
        for value in ("PVT_kwHOAbc12345", "12345", "https://github.com/users/x/projects/3"):
            raw = default_raw()
            raw["environment"] = value
            errors = project_flow.validate_config(raw)
            self.assertEqual(len(errors), 1, (value, errors))
            self.assertTrue(errors[0].startswith("environment: "), errors)

    def test_unknown_top_level_key_rejected(self):
        raw = default_raw()
        raw["project"] = "x"
        self.assertTrue(project_flow.validate_config(raw))

    def test_load_invalid_raises_stop(self):
        raw = default_raw()
        raw["roles"]["wip"] = "WIP"
        with self.assertRaises(Stop) as ctx:
            project_flow.load_config(self.write(raw))
        self.assertEqual(ctx.exception.reason, "config-invalid")

    def test_main_config_invalid_exit(self):
        raw = default_raw()
        raw["blocked_label"] = "#12"
        bad = self.write(raw)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = project_flow.main(["check", "--config", bad])
        self.assertEqual(code, 3)
        self.assertIn("stop: config-invalid", out.getvalue())

    def test_main_check_valid_exit(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = project_flow.main(["check", "--config", DEFAULTS])
        self.assertEqual(code, 0)

    def test_missing_scope_maps_to_stop(self):
        stop = project_flow.map_gh_error(GhError("gh: missing required scopes [project]"))
        self.assertEqual((stop.reason, stop.detail), ("missing-scope", "run gh auth refresh -s project"))
        self.assertIsNone(project_flow.map_gh_error(GhError("boom")))

    def test_stop_equality_used_by_map_gh_error(self):
        stop = project_flow.map_gh_error(GhError("gh: missing required scopes [project]"))
        self.assertEqual(stop, Stop("missing-scope", "run gh auth refresh -s project"))
        self.assertEqual(hash(stop), hash(Stop("missing-scope", "run gh auth refresh -s project")))
        self.assertNotEqual(stop, Stop("missing-scope", "other"))
        self.assertNotEqual(stop, Stop("incomplete-read", "run gh auth refresh -s project"))
        self.assertNotEqual(stop, ("missing-scope", "run gh auth refresh -s project"))

    def test_stop_rejects_unknown_reason(self):
        for reason in project_flow.STOP_REASONS:
            self.assertEqual(Stop(reason).reason, reason)
        with self.assertRaises(ValueError):
            Stop("not-a-reason")

    def test_main_gh_error_exits_one(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(project_flow, "_check", side_effect=GhError("HTTP 502 from api")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = project_flow.main(["check", "--config", DEFAULTS])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 502 from api", err.getvalue())
        self.assertEqual(out.getvalue(), "")

    def test_main_scope_gh_error_is_a_stop(self):
        out = io.StringIO()
        with mock.patch.object(project_flow, "_check",
                               side_effect=GhError("missing required scopes [project]")), \
                contextlib.redirect_stdout(out):
            code = project_flow.main(["check", "--config", DEFAULTS])
        self.assertEqual(code, 3)
        self.assertIn("stop: missing-scope: run gh auth refresh -s project", out.getvalue())

    def test_gh_transport_graphql_arguments(self):
        seen = []
        with mock.patch.object(project_flow.GhTransport, "_run",
                               side_effect=lambda args, stdin=None: seen.append((args, stdin)) or "{}"):
            project_flow.GhTransport().graphql("query { a }", {"n": 3, "flag": True, "s": "x",
                                                               "after": None})
        args, stdin = seen[0]
        self.assertEqual(args, ["api", "graphql", "--input", "-"])
        self.assertEqual(json.loads(stdin), {"query": "query { a }",
                                             "variables": {"n": 3, "flag": True, "s": "x", "after": None}})

    def test_unimplemented_subcommand_exits_usage(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = project_flow.main(["sync"])
        self.assertEqual(code, 2)

    def test_fake_transport_records_and_serves(self):
        fake = FakeTransport([("mutation", {"ok": 1}), (None, GhError("x"))])
        self.assertEqual(fake.graphql("mutation { a }", {}), {"ok": 1})
        with self.assertRaises(GhError):
            fake.rest("GET", "/x")
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(len(fake.mutations), 1)


if __name__ == "__main__":
    unittest.main()


class WorkflowEnvironmentTest(unittest.TestCase):
    """The agent commands and the Action must read PROJECT_URL from the same place: the job-level
    environment of project-flow.yml is the environment the committed config names."""

    def test_workflow_job_environment_matches_the_config(self):
        with open(WORKFLOW, encoding="utf-8") as handle:
            workflow = handle.read()
        jobs = workflow.split("\njobs:\n", 1)[1]
        found = [value.strip() for value in re.findall(r"(?m)^    environment:(.*)$", jobs)]
        self.assertEqual(len(found), 1, "project-flow.yml must declare one job-level environment")
        self.assertEqual(found[0], project_flow.load_config(DEFAULTS).environment)
