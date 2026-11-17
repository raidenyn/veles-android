"""Tests for project_flow.py config loading, validation and error mapping. No network."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_flow  # noqa: E402
from project_flow import GhError, Stop  # noqa: E402
from project_flow_fake import FakeTransport  # noqa: E402

PROJECT = os.path.dirname(SCRIPTS)
DEFAULTS = os.path.join(PROJECT, ".github", "project-flow.json")


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
