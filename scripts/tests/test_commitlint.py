import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from commitlint import main, validate  # noqa: E402

CLI = os.path.join(SCRIPTS, "commitlint.py")


class ValidateTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate("feat(android): add auto-copy toggle"), [])

    def test_bad_type(self):
        self.assertTrue(any("type" in r for r in validate("OTP-26: add auto-copy toggle")))

    def test_breaking_footer_requires_bang(self):
        self.assertTrue(
            any("feat(protocol)!:" in r for r in validate("feat(protocol): v2\n\nBREAKING CHANGE: x"))
        )
        self.assertTrue(validate("feat(protocol): v2\n\nBREAKING-CHANGE: x"))
        self.assertEqual(validate("feat(protocol)!: v2\n\nBREAKING CHANGE: x"), [])

    def test_header_length(self):
        self.assertTrue(any("101" in r for r in validate("fix: " + "x" * 96)))
        self.assertEqual(validate("fix: " + "x" * 95), [])

    def test_header_length_ignores_the_squash_pr_suffix(self):
        # GitHub appends " (#N)" to a squash commit title taken from the PR title.
        self.assertEqual(validate("fix: " + "x" * 95 + " (#123)"), [])
        reasons = validate("fix: " + "x" * 96 + " (#123)")
        self.assertTrue(any("101" in r for r in reasons), reasons)
        self.assertEqual(validate("feat: a (#12)"), [])
        # Only one trailing suffix is ignored, and it must follow a valid header.
        self.assertTrue(any("105" in r for r in validate("fix: " + "x" * 95 + " (#1) (#123)")))
        self.assertTrue(validate("feat: (#12)"))
        self.assertTrue(validate("Update installer (#12)"))

    def test_misc_invalid(self):
        self.assertTrue(validate("fix: a\nbody"))
        self.assertTrue(validate("fix(Android): a"))
        self.assertTrue(validate("fix: "))
        self.assertTrue(validate(""))

    def test_crlf_and_comments(self):
        msg = "fix: a\r\n\r\nbody\r\n# Please enter the commit message\r\n"
        self.assertEqual(validate(msg), [])

    def test_git_generated(self):
        for s in ('Merge branch "x"', 'Revert "feat: a"', "fixup! feat: a", "squash! feat: a"):
            self.assertTrue(validate(s))
            self.assertEqual(validate(s, allow_git_generated=True), [])


class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-I", CLI] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )

    def test_message_valid(self):
        self.assertEqual(self.run_cli("--message", "fix: a").returncode, 0)

    def test_message_invalid(self):
        res = self.run_cli("--message", "nope")
        self.assertEqual(res.returncode, 1)
        self.assertIn("header", res.stderr)

    def test_usage(self):
        self.assertEqual(self.run_cli().returncode, 2)
        self.assertEqual(main([]), 2)

    def test_file_crlf(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "MSG")
            with open(path, "wb") as f:
                f.write(b"fix: a\r\n\r\nbody\r\n# comment\r\n")
            self.assertEqual(self.run_cli("--file", path).returncode, 0)

    def test_allow_git_generated_flag(self):
        self.assertEqual(self.run_cli("--message", "fixup! x").returncode, 1)
        self.assertEqual(self.run_cli("--allow-git-generated", "--message", "fixup! x").returncode, 0)


if __name__ == "__main__":
    unittest.main()
