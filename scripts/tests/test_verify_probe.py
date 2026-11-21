"""Requirement probes, the effective environment and the machine config (design D7, D8 and D2).

Every case injects the clock, the process runner, the git reader, the environment and the
filesystem roots: no wall clock, no sleep and no real process runs. `Config` is the one part
that touches the real filesystem, so its cases use a temporary directory (and a throwaway git
repository with a linked worktree for the shared-file case).
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

TESTS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS)
REPO = os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, TESTS)

import components  # noqa: E402
import gitfixture  # noqa: E402
import verify  # noqa: E402
from verify import (  # noqa: E402
    Config,
    ConfigError,
    Outcome,
    Step,
    classify_env_failure,
    effective_env,
    load_table,
    main,
    probe,
    sdk_dir_warning,
)
from verifyfake import FakeFs, FakeRunner, make_context  # noqa: E402

JAVA_PIN = "21.0.12\n"
NDK_PIN = "29.0.14206865"
JDK21 = "/usr/lib/jvm/java-21-openjdk-amd64"
JDK21_ALT = "/usr/lib/jvm/java-21.0.12-openjdk-amd64"
JDK17 = "/usr/lib/jvm/java-17-openjdk-amd64"
HOME = "/fakehome"
JAVA_FIX = "Install a JDK 21 and run `python3 -I scripts/verify.py config set java_home <jdk>`."
PATH_FIX = "Run `cd web-extension && npm ci`."
SDK_FIX = "Set sdk.dir in android/local.properties or ANDROID_HOME."
NDK_FIX = "Install the pinned NDK, then set ANDROID_NDK_HOME."
RUSTUP_FIX = "Run `rustup show active-toolchain` in rust/."
PLAYWRIGHT_FIX = "Run `cd web-extension && npx playwright-core install --with-deps chromium`."
PLAYWRIGHT_ARGV = ["node", "-e",
                   "console.log(require('playwright-core').chromium.executablePath())"]


def tool(resolver, args=None, fix="fix it"):
    return {"kind": "tool", "resolver": resolver, "args": dict(args or {}), "fix": fix}


def release(version):
    return ('IMPLEMENTOR="Fixture"\nJAVA_VERSION="%s"\n' % version).encode("utf-8")


def source_properties(revision):
    return ("Pkg.Desc = Android NDK\nPkg.Revision = %s\n" % revision).encode("utf-8")


def real_table():
    cmap = components.load_map(os.path.join(REPO, ".github", "components.json"))
    return load_table(os.path.join(REPO, "scripts", "verify-commands.json"), cmap)


class FixtureRepo(unittest.TestCase):
    """A temporary checkout whose files and outside roots live in one injected FakeFs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.realpath(self._tmp.name)
        self.lines = []

    def abs(self, relative):
        return os.path.join(self.repo, relative)

    def context(self, repo_files=None, outside_files=None, env=None, runner=None,
                modes=None, deadline=None, clock=None):
        files = {}
        for relative, content in (repo_files or {}).items():
            files[self.abs(relative)] = content
        files.update(outside_files or {})
        entries = dict((os.path.normpath(name), value) for name, value in files.items())
        mode_map = dict((os.path.normpath(name), value)
                        for name, value in (modes or {}).items())
        fs = FakeFs(files=entries, modes=mode_map)
        overrides = {"clock": clock} if clock is not None else {}
        ctx = make_context(repo=self.repo,
                           common_dir=os.path.join(self.repo, ".git"),
                           fs=fs,
                           runner=runner or FakeRunner(),
                           env=dict(env if env is not None else {"PATH": "", "HOME": HOME}),
                           run_dir=self.abs("build/verify/runs/test-run"),
                           deadline=deadline,
                           log=self.lines.append,
                           **overrides)
        return ctx


class JavaHomeTest(FixtureRepo):
    def test_java_home_unset_message(self):
        # D7/spec "JDK unset": the message names the pin file, what was found, the single
        # matching candidate and the exact `config set` command.
        req = tool("java-home", fix=JAVA_FIX)
        context = self.context(
            repo_files={".java-version": JAVA_PIN.encode("utf-8")},
            outside_files={JDK21 + "/release": release("21.0.12"),
                           JDK21 + "/bin/java": b""},
        )
        outcome, fingerprint = probe("java-home", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "not_run")
        self.assertIsNone(fingerprint)
        for piece in (".java-version", "21.0.12", JDK21, "no java is on PATH",
                      "python3 -I scripts/verify.py config set java_home " + JDK21):
            self.assertIn(piece, outcome.error)
        self.assertEqual(outcome.fix,
                         "python3 -I scripts/verify.py config set java_home " + JDK21)
        self.assertEqual(context.runner.calls, [])

    def test_java_home_ok_fingerprint(self):
        # D6: the resolved entry is the real JDK home and the full version output.
        req = tool("java-home", fix=JAVA_FIX)
        jdk = self.abs("jdk21")
        context = self.context(
            repo_files={".java-version": JAVA_PIN.encode("utf-8"),
                        "jdk21/bin/java": b""},
            env={"PATH": "", "HOME": HOME, "JAVA_HOME": jdk},
        )
        lines = ['openjdk version "21.0.12" 2025-01-01',
                 "OpenJDK Runtime Environment (build 21.0.12+1)"]
        context.runner = FakeRunner(script={(jdk + "/bin/java", "-version"):
                                            Outcome("ok", code=0, stderr=list(lines))})
        outcome, fingerprint = probe("java-home", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "ok")
        self.assertEqual(fingerprint,
                         os.path.realpath(jdk) + "@" + "\n".join(lines))
        call = context.runner.calls[0]
        self.assertEqual(call["argv"], [jdk + "/bin/java", "-version"])
        self.assertEqual(call["cwd"], self.repo)
        self.assertTrue(call["capture"])
        self.assertTrue(call["log"].endswith(".probe.log"))

    def test_java_version_read_from_stderr(self):
        # `java -version` writes to stderr; a probe that reads only stdout cannot see it.
        req = tool("java-home", fix=JAVA_FIX)
        context = self.context(
            repo_files={".java-version": JAVA_PIN.encode("utf-8"),
                        ".jdk/bin/java": b""},
            env={"PATH": self.abs(".jdk/bin"), "HOME": HOME},
            modes={self.abs(".jdk/bin/java"): 0o100755},
        )
        version = 'openjdk version "21.0.13" 2025-02-01'
        context.runner = FakeRunner(script={(self.abs(".jdk/bin/java"), "-version"):
                                            Outcome("ok", code=0, stderr=[version])})
        outcome, fingerprint = probe("java-home", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "ok", outcome.error)
        self.assertEqual(fingerprint, os.path.realpath(self.abs(".jdk")) + "@" + version)

    def test_java_home_wrong_major_message(self):
        # D7: the pin's major is what must match, never the full version string.
        req = tool("java-home", fix=JAVA_FIX)
        context = self.context(
            repo_files={".java-version": JAVA_PIN.encode("utf-8"),
                        "jdk17/bin/java": b""},
            outside_files={JDK17 + "/release": release("17.0.10")},
            env={"PATH": "", "HOME": HOME, "JAVA_HOME": self.abs("jdk17")},
        )
        context.runner = FakeRunner(script={(self.abs("jdk17/bin/java"), "-version"):
                                            Outcome("ok", code=0,
                                                    stderr=['openjdk version "17.0.10"'])})
        outcome, fingerprint = probe("java-home", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "not_run")
        self.assertIsNone(fingerprint)
        self.assertIn(".java-version", outcome.error)
        self.assertIn("21", outcome.error)
        self.assertIn("17.0.10", outcome.error)
        self.assertIn(JDK17, outcome.error)
        # A JDK 17 candidate does not match the pin, so the requirement's own fix stands.
        self.assertEqual(outcome.fix, JAVA_FIX)
        self.assertNotIn("config set java_home " + JDK17, outcome.error)

    def test_several_candidates_ask_user(self):
        # D7: with several matching candidates the fix asks the user; no candidate is picked.
        req = tool("java-home", fix=JAVA_FIX)
        context = self.context(
            repo_files={".java-version": JAVA_PIN.encode("utf-8")},
            outside_files={JDK21 + "/release": release("21.0.12"),
                           JDK21_ALT + "/release": release("21.0.13"),
                           JDK21 + "/bin/java": b"",
                           JDK21_ALT + "/bin/java": b""},
        )
        outcome, _ = probe("java-home", req, context, context.env, context.runner.run)
        self.assertEqual(outcome.kind, "not_run")
        self.assertIn("ask the user which one this project uses", outcome.error)
        self.assertIn(JDK21, outcome.error)
        self.assertIn(JDK21_ALT, outcome.error)
        self.assertNotIn("config set java_home " + JDK21, outcome.error)
        self.assertEqual(outcome.fix, JAVA_FIX)

    def test_java_version_banner_wins_over_picked_up_options(self):
        # A container JVM prints `Picked up …` lines, often with numbers (`-Xmx2g`), before the
        # banner. The version must come from the `version "…"` banner, never those numbers.
        req = tool("java-home", fix=JAVA_FIX)
        jdk = self.abs("jdk21")
        context = self.context(
            repo_files={".java-version": JAVA_PIN.encode("utf-8"),
                        "jdk21/bin/java": b""},
            env={"PATH": "", "HOME": HOME, "JAVA_HOME": jdk},
        )
        lines = ["Picked up JAVA_TOOL_OPTIONS: -Dfile.encoding=UTF-8 -Xmx2g",
                 "Picked up _JAVA_OPTIONS: -Xss4m",
                 'openjdk version "21.0.13" 2026-11-17',
                 "OpenJDK Runtime Environment (build 21.0.13+1-Ubuntu-0ubuntu1)"]
        context.runner = FakeRunner(script={(jdk + "/bin/java", "-version"):
                                            Outcome("ok", code=0, stderr=list(lines))})
        outcome, fingerprint = probe("java-home", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "ok", outcome.error)
        self.assertEqual(fingerprint, os.path.realpath(jdk) + "@" + "\n".join(lines))

        # The same parse reads the pin, a release value and a Java 8 banner.
        self.assertEqual(verify._java_major(JAVA_PIN), 21)
        self.assertEqual(verify._java_major("21.0.13.1"), 21)
        self.assertEqual(verify._java_major('java version "1.8.0_392"'), 8)
        self.assertEqual(verify._java_major("Picked up _JAVA_OPTIONS: -Xmx2g\n"
                                            'openjdk version "21.0.13"'), 21)

    def test_probe_timeout_outcome(self):
        # D9: a probe that never starts inside the reserve is `not_reached` (never a blocking
        # timeout), while a probe the runner actually cut off is a `timeout`; neither is a
        # missing-tool `not_run`.
        req = tool("java-home", fix=JAVA_FIX)
        jdk = self.abs("jdk21")
        context = self.context(
            repo_files={".java-version": JAVA_PIN.encode("utf-8"),
                        "jdk21/bin/java": b""},
            env={"PATH": "", "HOME": HOME, "JAVA_HOME": jdk},
            deadline=1000.0,
        )
        context.clock.value = 956.0  # inside the 45-second reserve
        outcome, fingerprint = probe("java-home", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "not_reached")
        self.assertIsNone(fingerprint)
        self.assertEqual(context.runner.calls, [])

        # Outside the reserve the version command runs under min(now + 60, deadline - 45).
        context.clock.value = 100.0
        context.runner = FakeRunner(script={(jdk + "/bin/java", "-version"):
                                            Outcome("timeout", elapsed_s=60.0)})
        outcome, fingerprint = probe("java-home", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "timeout")
        self.assertIsNone(fingerprint)
        self.assertEqual(context.runner.cutoffs, [160.0])


class AndroidSdkTest(FixtureRepo):
    def test_android_sdk_prefers_local_properties(self):
        # D8/D7: the resolver reports the SDK Gradle actually uses: local.properties first.
        local = self.abs("local-sdk")
        home_sdk = self.abs("home-sdk")
        root_sdk = self.abs("root-sdk")
        req = tool("android-sdk", fix=SDK_FIX)
        files = {
            "local-sdk/platform-tools/adb": b"",
            "home-sdk/platform-tools/adb": b"",
            "root-sdk/cmdline-tools/bin/sdkmanager": b"",
            "android/local.properties": ("sdk.dir=%s\n" % local).encode("utf-8"),
        }
        context = self.context(repo_files=files,
                               env={"PATH": "", "HOME": HOME})
        outcome, fingerprint = probe("android-sdk", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "ok", outcome.error)
        self.assertEqual(fingerprint, os.path.realpath(local) + "@")

        # Without local.properties, ANDROID_HOME wins over ANDROID_SDK_ROOT.
        context = self.context(repo_files={"home-sdk/platform-tools/adb": b"",
                                           "root-sdk/cmdline-tools/bin/sdkmanager": b""},
                               env={"PATH": "", "HOME": HOME,
                                    "ANDROID_HOME": home_sdk,
                                    "ANDROID_SDK_ROOT": root_sdk})
        outcome, fingerprint = probe("android-sdk", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "ok", outcome.error)
        self.assertEqual(fingerprint, os.path.realpath(home_sdk) + "@")

        # With only ANDROID_SDK_ROOT set, that one is used.
        context = self.context(repo_files={"root-sdk/cmdline-tools/bin/sdkmanager": b""},
                               env={"PATH": "", "HOME": HOME,
                                    "ANDROID_SDK_ROOT": root_sdk})
        outcome, fingerprint = probe("android-sdk", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "ok", outcome.error)
        self.assertEqual(fingerprint, os.path.realpath(root_sdk) + "@")

    def test_android_sdk_candidates_message(self):
        # D7: the bounded scan names the SDK found under $HOME/Android/Sdk and the exact fix.
        req = tool("android-sdk", fix=SDK_FIX)
        sdk = HOME + "/Android/Sdk"
        context = self.context(
            outside_files={sdk + "/platform-tools/adb": b""},
            env={"PATH": "", "HOME": HOME},
        )
        outcome, fingerprint = probe("android-sdk", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "not_run")
        self.assertIsNone(fingerprint)
        self.assertIn(sdk, outcome.error)
        self.assertEqual(outcome.fix,
                         "python3 -I scripts/verify.py config set android_sdk " + sdk)


class AndroidNdkTest(FixtureRepo):
    def test_ndk_first_existing_no_fallthrough(self):
        # D7: the first existing candidate is selected and a revision mismatch there is
        # NOT RUN, never a fall-through to the next candidate.
        req = tool("android-ndk", fix=NDK_FIX)
        installed = self.abs("ndk-wrong")
        sdk_root = self.abs("sdk-root")
        correct = sdk_root + "/ndk/" + NDK_PIN
        context = self.context(
            repo_files={"rust/ndk-version": (NDK_PIN + "\n").encode("utf-8"),
                        "ndk-wrong/source.properties": source_properties("28.0.1"),
                        "sdk-root/ndk/%s/source.properties" % NDK_PIN:
                            source_properties(NDK_PIN)},
            env={"PATH": "", "HOME": HOME,
                 "ANDROID_NDK_HOME": installed,
                 "ANDROID_SDK_ROOT": sdk_root},
        )
        outcome, fingerprint = probe("android-ndk", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "not_run")
        self.assertIsNone(fingerprint)
        self.assertIn(installed, outcome.error)
        self.assertIn(NDK_PIN, outcome.error)
        self.assertIn("28.0.1", outcome.error)
        self.assertIn("rust/ndk-version", outcome.error)

        # ANDROID_NDK_HOME absent: $ANDROID_SDK_ROOT/ndk/<version> is the first existing one.
        context = self.context(
            repo_files={"rust/ndk-version": (NDK_PIN + "\n").encode("utf-8"),
                        "sdk-root/ndk/%s/source.properties" % NDK_PIN:
                            source_properties(NDK_PIN),
                        "sdk-root/ndk/28.0.1/source.properties":
                            source_properties("28.0.1")},
            env={"PATH": "", "HOME": HOME, "ANDROID_SDK_ROOT": sdk_root},
        )
        outcome, fingerprint = probe("android-ndk", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "ok", outcome.error)
        self.assertEqual(fingerprint, os.path.realpath(correct) + "@" + NDK_PIN)


class PathToolTest(FixtureRepo):
    def test_path_tool_min_and_exact(self):
        # D7: `min` compares version components and `exact` demands the pinned string; both
        # record the resolved executable and its version output for the key.
        bindir = self.abs("node/bin")
        node = bindir + "/node"
        npm = bindir + "/npm"
        context = self.context(
            repo_files={"node/bin/node": b"", "node/bin/npm": b""},
            env={"PATH": bindir, "HOME": HOME},
            modes={node: 0o100755, npm: 0o100755},
            runner=FakeRunner(stdout={("node", "--version"): ["v22.14.0"],
                                      ("npm", "--version"): ["11.19.0"]}),
        )
        minimum = tool("path-tool", {"program": "node", "min": "22",
                                     "version_cmd": ["node", "--version"]})
        outcome, fingerprint = probe("node", minimum, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "ok", outcome.error)
        self.assertEqual(fingerprint, os.path.realpath(node) + "@v22.14.0")

        too_old = tool("path-tool", {"program": "node", "min": "23",
                                     "version_cmd": ["node", "--version"]})
        outcome, fingerprint = probe("node", too_old, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "not_run")
        self.assertIsNone(fingerprint)
        self.assertIn("23", outcome.error)
        self.assertIn("v22.14.0", outcome.error)
        self.assertIn("$HOME/.nvm/versions/node/*/bin", outcome.error)

        exact = tool("path-tool", {"program": "npm", "exact": "11.19.0",
                                   "version_cmd": ["npm", "--version"]})
        outcome, fingerprint = probe("npm", exact, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "ok", outcome.error)
        self.assertEqual(fingerprint, os.path.realpath(npm) + "@11.19.0")

        wrong_exact = tool("path-tool", {"program": "npm", "exact": "11.20.0",
                                         "version_cmd": ["npm", "--version"]})
        outcome, _ = probe("npm", wrong_exact, context, context.env, context.runner.run)
        self.assertEqual(outcome.kind, "not_run")
        self.assertIn("11.20.0", outcome.error)

    def test_path_tool_missing_message(self):
        # D7: a missing tool reports what was found on the candidate roots and the fix.
        context = self.context(env={"PATH": "", "HOME": HOME})
        req = tool("path-tool", {"program": "node", "min": "22",
                                 "version_cmd": ["node", "--version"]})
        outcome, fingerprint = probe("node", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "not_run")
        self.assertIsNone(fingerprint)
        self.assertIn("node", outcome.error)
        self.assertIn("$HOME/.nvm/versions/node/*/bin", outcome.error)
        self.assertEqual(outcome.fix, req["fix"])

    def test_path_requirement_message(self):
        # D7/spec: a missing path names the path and the requirement's fix text, and it never
        # suggests a `config set` (there is no config key for an installed directory).
        req = {"kind": "path", "path": "web-extension/node_modules", "fix": PATH_FIX}
        context = self.context()
        outcome, fingerprint = probe("web-extension.node-modules", req, context,
                                     context.env, context.runner.run)
        self.assertEqual(outcome.kind, "not_run")
        self.assertIsNone(fingerprint)
        self.assertIn("web-extension/node_modules", outcome.error)
        self.assertIn(PATH_FIX, outcome.error)
        self.assertEqual(outcome.fix, PATH_FIX)
        self.assertNotIn("config set", outcome.error + str(outcome.fix))

        context = self.context(repo_files={"web-extension/node_modules/a/index.js": b""})
        outcome, fingerprint = probe("web-extension.node-modules", req, context,
                                     context.env, context.runner.run)
        self.assertEqual(outcome.kind, "ok", outcome.error)
        self.assertIsNone(fingerprint)


class RustupToolchainTest(FixtureRepo):
    def test_rustup_toolchain_in_file_dir(self):
        # D7: `rustup show active-toolchain` runs in the toolchain file's directory and the
        # pinned channel must be the active one.
        rustup = self.abs("cargo/bin/rustup")
        req = tool("rustup-toolchain", {"toolchain_file": "rust/rust-toolchain.toml"},
                   fix=RUSTUP_FIX)
        script = ('[toolchain]\nchannel = "1.98.0"\nprofile = "minimal"\n')
        context = self.context(
            repo_files={"rust/rust-toolchain.toml": script.encode("utf-8"),
                        "cargo/bin/rustup": b""},
            env={"PATH": self.abs("cargo/bin"), "HOME": HOME},
            modes={rustup: 0o100755},
            runner=FakeRunner(stdout={(rustup, "show", "active-toolchain"):
                                      ["1.98.0-x86_64-unknown-linux-gnu (default)"]}),
        )
        outcome, fingerprint = probe("rustup", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "ok", outcome.error)
        self.assertEqual(context.runner.calls[0]["argv"],
                         [rustup, "show", "active-toolchain"])
        self.assertEqual(context.runner.calls[0]["cwd"], self.abs("rust"))
        self.assertEqual(fingerprint,
                         os.path.realpath(rustup)
                         + "@1.98.0-x86_64-unknown-linux-gnu (default)")

        context = self.context(
            repo_files={"rust/rust-toolchain.toml": script.encode("utf-8"),
                        "cargo/bin/rustup": b""},
            env={"PATH": self.abs("cargo/bin"), "HOME": HOME},
            modes={rustup: 0o100755},
            runner=FakeRunner(stdout={(rustup, "show", "active-toolchain"):
                                      ["1.97.0-x86_64-unknown-linux-gnu (default)"]}),
        )
        outcome, fingerprint = probe("rustup", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "not_run")
        self.assertIsNone(fingerprint)
        self.assertIn("rust/rust-toolchain.toml", outcome.error)
        self.assertIn("1.98.0", outcome.error)
        self.assertIn("1.97.0", outcome.error)
        self.assertEqual(outcome.fix, RUSTUP_FIX)


class PlaywrightTest(FixtureRepo):
    def test_playwright_honours_env(self):
        # D7: VELES_TEST_CHROMIUM wins and needs no node query; otherwise the pinned
        # playwright-core in web-extension/ reports the executable, which must be executable.
        req = tool("playwright-chromium", fix=PLAYWRIGHT_FIX)
        chromium = HOME + "/chromium"
        context = self.context(
            outside_files={chromium: b""},
            env={"PATH": "", "HOME": HOME, "VELES_TEST_CHROMIUM": chromium},
        )
        outcome, fingerprint = probe("playwright-chromium", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "ok", outcome.error)
        self.assertEqual(context.runner.calls, [])
        self.assertEqual(fingerprint, os.path.realpath(chromium) + "@")

        context = self.context(
            repo_files={"node/bin/node": b""},
            outside_files={HOME + "/ms-playwright/chrome-linux/chrome": b""},
            env={"PATH": self.abs("node/bin"), "HOME": HOME},
            modes={self.abs("node/bin/node"): 0o100755},
            runner=FakeRunner(stdout={tuple(PLAYWRIGHT_ARGV):
                                      [HOME + "/ms-playwright/chrome-linux/chrome"]}),
        )
        outcome, fingerprint = probe("playwright-chromium", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "ok", outcome.error)
        self.assertEqual(context.runner.calls[0]["cwd"], self.abs("web-extension"))
        self.assertTrue(context.runner.calls[0]["capture"])
        self.assertEqual(fingerprint,
                         os.path.realpath(HOME + "/ms-playwright/chrome-linux/chrome") + "@")

        context = self.context(
            repo_files={"node/bin/node": b""},
            env={"PATH": self.abs("node/bin"), "HOME": HOME},
            modes={self.abs("node/bin/node"): 0o100755},
            runner=FakeRunner(stdout={tuple(PLAYWRIGHT_ARGV):
                                      [HOME + "/gone/chrome"]}))
        outcome, fingerprint = probe("playwright-chromium", req, context, context.env,
                                     context.runner.run)
        self.assertEqual(outcome.kind, "not_run")
        self.assertIsNone(fingerprint)
        self.assertIn(HOME + "/gone/chrome", outcome.error)
        self.assertEqual(outcome.fix, PLAYWRIGHT_FIX)


class EffectiveEnvironmentTest(unittest.TestCase):
    def test_effective_env_from_config(self):
        # D8: config values become the effective variables; PATH is path_prepend, then
        # $JAVA_HOME/bin, then the inherited PATH.
        inherited = {"PATH": "/usr/bin:/bin", "HOME": "/home/fixture",
                     "JAVA_HOME": "/old/jdk", "KEEP": "yes"}
        config = {"java_home": "/new/jdk", "android_sdk": "/sdk",
                  "android_ndk": "/sdk/ndk/29", "path_prepend": "/pre"}
        env = effective_env(inherited, config)
        self.assertEqual(env["JAVA_HOME"], "/new/jdk")
        self.assertEqual(env["ANDROID_HOME"], "/sdk")
        self.assertEqual(env["ANDROID_SDK_ROOT"], "/sdk")
        self.assertEqual(env["ANDROID_NDK_HOME"], "/sdk/ndk/29")
        self.assertEqual(env["PATH"],
                         os.pathsep.join(["/pre", "/new/jdk/bin", "/usr/bin:/bin"]))
        self.assertEqual(env["KEEP"], "yes")
        self.assertEqual(inherited["JAVA_HOME"], "/old/jdk")

        # An inherited JAVA_HOME still puts its bin directory on the path; no config means
        # the inherited environment apart from that.
        env = effective_env(inherited, {})
        self.assertEqual(env["JAVA_HOME"], "/old/jdk")
        self.assertEqual(env["PATH"], os.pathsep.join(["/old/jdk/bin", "/usr/bin:/bin"]))
        self.assertNotIn("ANDROID_HOME", env)


class ConfigTest(unittest.TestCase):
    """`Config` reads and writes the real file, so these cases use temporary directories."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.realpath(self._tmp.name)
        self.repo = os.path.join(self.root, "repo")
        self.common = os.path.join(self.repo, ".git")
        os.makedirs(self.common)
        self.jdk21 = self.make_jdk(os.path.join(self.root, "jdk21"), "21.0.12")
        self.jdk17 = self.make_jdk(os.path.join(self.root, "jdk17"), "17.0.10")
        self.sdk = self.make_sdk(os.path.join(self.root, "sdk"))
        self.other_sdk = self.make_sdk(os.path.join(self.root, "other-sdk"))
        self.write(".java-version", JAVA_PIN)

    @staticmethod
    def make_jdk(path, version):
        os.makedirs(os.path.join(path, "bin"))
        with open(os.path.join(path, "bin", "java"), "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit 0\n")
        with open(os.path.join(path, "release"), "w", encoding="utf-8") as handle:
            handle.write('JAVA_VERSION="%s"\n' % version)
        return path

    @staticmethod
    def make_sdk(path):
        os.makedirs(os.path.join(path, "platform-tools"))
        with open(os.path.join(path, "platform-tools", "adb"), "w", encoding="utf-8"):
            pass
        return path

    def write(self, relative, text):
        path = os.path.join(self.repo, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def config(self):
        return Config(self.common, repo=self.repo)

    def test_config_refuses_jdk17(self):
        # D8/spec "Wrong version refused": nothing is written and the CLI exits 2 naming the pin.
        self.write(".java-version", JAVA_PIN)
        config = self.config()
        config.set("android_sdk", self.sdk)
        before = self.read_tools()
        context = make_context(repo=self.repo, common_dir=self.common,
                               env={"PATH": "", "HOME": self.root})
        code = main(["config", "set", "java_home", self.jdk17], context)
        self.assertEqual(code, 2)
        self.assertTrue(any(".java-version" in line for line in context.log_lines),
                        context.log_lines)
        self.assertTrue(any("17.0.10" in line for line in context.log_lines),
                        context.log_lines)
        self.assertEqual(self.read_tools(), before)

    def test_config_atomic_write(self):
        # D8: the write goes through a temporary file and os.replace, so a failed replace
        # leaves the previous file intact and no temporary behind.
        config = self.config()
        config.set("android_sdk", self.sdk)
        self.assertEqual(json.loads(self.read_tools()),
                         {"version": 1, "android_sdk": os.path.realpath(self.sdk)})
        before = self.read_tools()

        with mock.patch.object(verify.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(ConfigError):
                config.set("path_prepend", self.sdk)
        self.assertEqual(self.read_tools(), before)
        leftovers = [name for name in os.listdir(os.path.join(self.common, "veles-verify"))
                     if name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

        config.set("path_prepend", self.sdk)
        saved = json.loads(self.read_tools())
        self.assertEqual(saved["path_prepend"], os.path.realpath(self.sdk))
        self.assertEqual(saved["android_sdk"], os.path.realpath(self.sdk))

    def test_config_show_lists_keys_and_versions(self):
        # D8: show prints the path, every key, its value and the resolved version.
        self.config().set("java_home", self.jdk21)
        text = self.config().show(env={"PATH": "", "HOME": self.root})
        self.assertIn(os.path.join(self.common, "veles-verify", "tools.json"), text)
        self.assertIn("java_home: " + os.path.realpath(self.jdk21), text)
        self.assertIn("21.0.12", text)
        self.assertIn("android_sdk: (not set)", text)
        self.assertIn("android_ndk: (not set)", text)
        self.assertIn("path_prepend: (not set)", text)

    def test_sdk_dir_warning_in_show_and_run(self):
        # D8: a local.properties sdk.dir that differs from the effective SDK is reported by
        # `config show` and by the same text a run's android node uses.
        self.write("android/local.properties",
                   "sdk.dir=%s\n" % self.other_sdk)
        self.config().set("android_sdk", self.sdk)
        show = self.config().show(env={"PATH": "", "HOME": self.root})
        self.assertIn("local.properties", show)
        self.assertIn(os.path.realpath(self.other_sdk), show)
        self.assertIn(os.path.realpath(self.sdk), show)

        context = make_context(
            repo=self.repo,
            common_dir=self.common,
            fs=FakeFs(files={
                os.path.join(self.repo, "android", "local.properties"):
                    ("sdk.dir=%s\n" % self.other_sdk).encode("utf-8"),
            }),
            env={"PATH": "", "HOME": self.root},
        )
        warning = sdk_dir_warning(
            context, effective_env(context.env, {"android_sdk": self.sdk}))
        self.assertIsNotNone(warning)
        self.assertIn("android/local.properties", warning)
        self.assertIn(os.path.realpath(self.other_sdk), warning)
        self.assertIn(os.path.realpath(self.sdk), warning)
        self.assertIn(warning, show)

        # The same sdk.dir on both sides is not a warning.
        context.fs = FakeFs(files={
            os.path.join(self.repo, "android", "local.properties"):
                ("sdk.dir=%s\n" % self.sdk).encode("utf-8"),
        })
        self.assertIsNone(sdk_dir_warning(
            context, effective_env(context.env, {"android_sdk": self.sdk})))

    def test_config_shared_across_worktrees(self):
        # D8/spec "Shared across worktrees": one file under the git common dir serves the main
        # checkout and every linked worktree.
        fixture = gitfixture.Repo()
        self.addCleanup(fixture.cleanup)
        fixture.write(".java-version", JAVA_PIN)
        fixture.git("add", "-A")
        fixture.git("commit", "-q", "--no-verify", "-m", "chore: pin java")
        linked = os.path.join(fixture.tmp, "linked")
        fixture.git("worktree", "add", "-q", "-b", "linked", linked)
        common = fixture.git("rev-parse", "--git-common-dir")
        if not os.path.isabs(common):
            common = os.path.join(str(fixture.path), common)
        common = os.path.realpath(common)

        written = Config(common, repo=linked)
        written.set("java_home", self.jdk21)
        self.assertEqual(
            written.path,
            os.path.join(common, "veles-verify", "tools.json"))
        for checkout in (str(fixture.path), linked):
            loaded = Config(common, repo=checkout)
            self.assertEqual(loaded.load()["java_home"], os.path.realpath(self.jdk21))

    def read_tools(self):
        with open(os.path.join(self.common, "veles-verify", "tools.json"),
                  encoding="utf-8") as handle:
            return handle.read()

    @staticmethod
    def lines():
        return []


class EnvFailureTest(FixtureRepo):
    def write_log(self, lines):
        path = self.abs("build/verify/runs/test/step.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return path

    def step(self, env_exit=None, env_patterns=None):
        return Step(id="tooling.check", kind="check", run=["check"], doc="scripts/AGENTS.md",
                    env_exit=list(env_exit or []), env_patterns=list(env_patterns or []))

    def test_env_exit_supply_chain(self):
        # D7/spec "Supply-chain pin reported by the script": exit 2 is NOT RUN with its fix,
        # not FAIL.
        table = real_table()
        commands = table.nodes["supply-chain"].commands
        supply = [step for step in commands if step.id == "supply-chain.supply-chain"][0]
        log = self.write_log(["ERROR: npm 11.19.0 required, found 11.20.0"])
        outcome = classify_env_failure(supply, Outcome("fail", code=2, tail=["ERROR: npm"]),
                                       log)
        self.assertEqual(outcome.kind, "not_run")
        self.assertEqual(outcome.fix, supply.env_exit[0]["fix"])
        self.assertEqual(outcome.code, 2)

        unchanged = classify_env_failure(supply, Outcome("fail", code=1, tail=["x"]), log)
        self.assertEqual(unchanged.kind, "fail")
        self.assertIsNone(unchanged.fix)

    def test_env_pattern_match(self):
        # D7: a declared output pattern turns a failure into NOT RUN with its fix.
        fix = "Install npm 11.19.0 beside node."
        step = self.step(env_patterns=[{"regex": r"npm 11\.19\.0 required",
                                        "fix": fix}])
        log = self.write_log(["build starting", "ERROR: npm 11.19.0 required"])
        outcome = classify_env_failure(step, Outcome("fail", code=1, tail=["ERROR: npm"]), log)
        self.assertEqual(outcome.kind, "not_run")
        self.assertEqual(outcome.fix, fix)

        log = self.write_log(["build starting", "everything fine"])
        outcome = classify_env_failure(step, Outcome("fail", code=1, tail=["fine"]), log)
        self.assertEqual(outcome.kind, "fail")
        self.assertIsNone(outcome.fix)

    def test_env_pattern_before_long_output(self):
        # D7/D9: patterns are matched against the whole log, not only the reported tail, so a
        # diagnostic followed by hundreds of lines still classifies the failure.
        fix = "Provision the toolchain."
        step = self.step(env_patterns=[{"regex": "toolchain is not provisioned",
                                        "fix": fix}])
        lines = ["ERROR: the toolchain is not provisioned"] + [
            "compiling unit %03d" % number for number in range(500)]
        log = self.write_log(lines)
        outcome = classify_env_failure(
            step, Outcome("fail", code=1, tail=lines[-60:]), log)
        self.assertEqual(outcome.kind, "not_run")
        self.assertEqual(outcome.fix, fix)
        self.assertNotIn("toolchain is not provisioned", "\n".join(outcome.tail))

    def test_missing_log_is_not_a_match(self):
        # A log the runner never wrote cannot prove an environment failure.
        step = self.step(env_patterns=[{"regex": "anything", "fix": "fix"}])
        outcome = classify_env_failure(step, Outcome("fail", code=1), self.abs("gone.log"))
        self.assertEqual(outcome.kind, "fail")


if __name__ == "__main__":
    unittest.main()
