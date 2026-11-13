"""Throwaway git repositories for release-planner tests (always under a tempfile directory)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
CLIFF_ENV = "VELES_GIT_CLIFF"
SKIP_REASON = "%s is not set; run ./gradlew scriptsTest to install the pinned git-cliff" % CLIFF_ENV


def cliff_path():
    value = os.environ.get(CLIFF_ENV)
    return Path(value) if value else None


def requires_cliff(cls):
    return unittest.skipUnless(cliff_path(), SKIP_REASON)(cls)


class Repo:
    """A fixture repository shaped like the project: app/, web-extension/, rust/,
    native-bridge/installer/ and a root build.gradle.kts."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="veles-fixture-")
        self.path = Path(self.tmp) / "repo"
        self.path.mkdir()
        empty = Path(self.tmp) / "gitconfig"
        empty.write_text("", encoding="utf-8")
        self.env = dict(os.environ)
        self.env.update(
            {
                "GIT_CONFIG_GLOBAL": str(empty),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "Fixture",
                "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                "GIT_COMMITTER_NAME": "Fixture",
                "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            }
        )
        self._tick = 0
        self.git("init", "-q", "-b", "master")
        for rel in ("app/a.kt", "web-extension/a.ts", "rust/a.rs", "native-bridge/installer/a.rs"):
            self.write(rel, "init\n")
        self.write("build.gradle.kts", "// init\n")
        self.git("add", "-A")
        self.init = self._commit("chore: init")

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args, cwd=None):
        env = dict(self.env)
        # Strictly increasing dates keep git-cliff's ordering deterministic.
        stamp = "2026-01-01T00:00:%02d+00:00" % min(self._tick, 59)
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp
        result = subprocess.run(
            ["git"] + list(args), cwd=str(cwd or self.path), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        )
        if result.returncode != 0:
            raise AssertionError("git %s failed: %s" % (" ".join(args), result.stderr))
        return result.stdout.strip()

    def write(self, rel, text):
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(str(target), "a", encoding="utf-8") as handle:
            handle.write(text)

    def _commit(self, message):
        self._tick += 1
        self.git("commit", "-q", "--no-verify", "-m", message)
        return self.head()

    def commit(self, message, touching="app/a.kt"):
        paths = [touching] if isinstance(touching, str) else list(touching)
        for rel in paths:
            self.write(rel, "%d %s\n" % (self._tick, message.splitlines()[0]))
        self.git("add", "-A")
        return self._commit(message)

    def tag(self, name, at="HEAD"):
        self.git("tag", name, at)

    def migrate(self, message="refactor(android): move the app to android/"):
        """The migration commit: adds android/ and, like the real move, deletes app/.

        Every commit after it is in the new layout epoch (its tree contains android/).
        """
        self.write("android/settings.gradle.kts", "// android root\n")
        self.write("android/app/build.gradle.kts", "// app\n")
        self.git("rm", "-r", "-q", "app")
        self.git("add", "-A")
        return self._commit(message)

    def head(self):
        return self.git("rev-parse", "HEAD")

    def cmap(self, since=None):
        with open(str(PROJECT / ".github" / "components.json"), encoding="utf-8") as handle:
            data = json.load(handle)
        data["since"] = since or self.init
        path = Path(self.tmp) / "components.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        import components  # scripts/ is on sys.path (inserted by the test modules)

        return components.load_map(path)

    def shallow_clone(self):
        target = Path(self.tmp) / "shallow"
        self.git("clone", "-q", "--depth", "1", "file://" + str(self.path), str(target), cwd=self.tmp)
        return target


def make_repo(test):
    repo = Repo()
    test.addCleanup(repo.cleanup)
    return repo
