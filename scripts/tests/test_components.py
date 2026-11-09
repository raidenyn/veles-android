import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from components import (  # noqa: E402
    affected,
    changed_files,
    load_map,
    main,
    owner_nodes,
    release_paths,
    unaccounted,
)

REPO = os.path.dirname(SCRIPTS)
MAP_PATH = os.path.join(REPO, ".github", "components.json")
CMAP = load_map(MAP_PATH)
NODES = set(CMAP["nodes"])


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd, check=True, stdout=subprocess.PIPE, universal_newlines=True,
    ).stdout.strip()


def tmp_map(**overrides):
    base = {
        "since": "a" * 40,
        "global": [".github/"],
        "unowned": ["docs/"],
        "nodes": {
            "android": {"release": True, "tag-prefix": "android-v", "initial-version": "0.1.0", "paths": ["app/"]},
            "rust": {"release": False, "paths": ["rust/"]},
        },
    }
    base.update(overrides)
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(base, handle)
    handle.close()
    return handle.name


class SelectionTest(unittest.TestCase):
    def test_extension_only(self):
        a = affected(["web-extension/src/a.ts"], CMAP)
        self.assertTrue(a["web-extension"])
        self.assertFalse(a["android"])
        self.assertFalse(a["installer"])
        self.assertEqual(set(a), NODES)

    def test_shared_rust(self):
        a = affected(["rust/veles-crypto/src/lib.rs"], CMAP)
        for n in ("rust", "android", "web-extension", "protocol"):
            self.assertTrue(a[n], n)
        self.assertFalse(a["installer"])
        self.assertFalse(a["native-host"])

    def test_global_selects_all(self):
        self.assertTrue(all(affected(["build.gradle.kts"], CMAP).values()))
        self.assertTrue(all(affected([".github/workflows/check-android.yml"], CMAP).values()))
        self.assertTrue(all(affected([".java-version"], CMAP).values()))

    def test_unaccounted_selects_all(self):
        self.assertTrue(all(affected(["newdir/x"], CMAP).values()))
        self.assertEqual(unaccounted(["newdir/x"], CMAP), ["newdir/x"])
        self.assertEqual(unaccounted(["app/a.kt", "docs/ci.md", "build.gradle.kts"], CMAP), [])

    def test_known_cross_directory_dependencies(self):
        self.assertTrue(affected(["verify/lib/checksum-manifest.mjs"], CMAP)["web-extension"])
        self.assertTrue(affected(["verify/lib/checksum-manifest.mjs"], CMAP)["bridge-scaffold"])
        self.assertTrue(affected(["config/detekt/detekt.yml"], CMAP)["android"])
        self.assertTrue(affected(["protocol/v1/fixtures/crypto.json"], CMAP)["rust"])
        self.assertTrue(affected(["protocol/v1/limits.json"], CMAP)["bridge-scaffold"])
        self.assertTrue(affected(["verify/check-remote-code.mjs"], CMAP)["native-host"])
        self.assertTrue(affected(["rust/scripts/assert-clean.sh"], CMAP)["installer"])  # via ci-paths
        self.assertTrue(affected(["config/detekt/detekt.yml"], CMAP)["android"])  # via ci-paths
        self.assertTrue(affected([".license-policy.json"], CMAP)["supply-chain"])
        self.assertTrue(affected(["web-extension/src/connector.ts"], CMAP)["supply-chain"])

    def test_unowned(self):
        self.assertFalse(any(affected(["docs/ci.md"], CMAP).values()))
        self.assertFalse(any(affected(["native-bridge/feasibility/src/core.rs"], CMAP).values()))
        self.assertEqual(owner_nodes("README.md", CMAP), set())

    def test_owner_nodes_contract(self):
        self.assertIsNone(owner_nodes("build.gradle.kts", CMAP))
        self.assertIsNone(owner_nodes("newdir/x", CMAP))
        self.assertEqual(owner_nodes("native-bridge/host/src/lib.rs", CMAP), {"installer", "native-host", "protocol", "supply-chain"})
        self.assertEqual(owner_nodes("scripts/components.py", CMAP), {"tooling"})

    def test_no_base_selects_all(self):
        self.assertIsNone(changed_files("0" * 40))
        self.assertIsNone(changed_files(None))
        self.assertIsNone(changed_files(""))
        self.assertTrue(all(affected(None, CMAP).values()))
        self.assertFalse(any(affected([], CMAP).values()))

    def test_release_paths(self):
        self.assertIn("build.gradle.kts", release_paths("installer", CMAP))
        self.assertIn("native-bridge/installer/", release_paths("installer", CMAP))
        self.assertNotIn("app/", release_paths("installer", CMAP))

    def test_ci_paths_never_release(self):
        rp = release_paths("installer", CMAP)
        self.assertNotIn("rust/scripts/assert-clean.sh", rp)
        self.assertFalse([p for p in rp if p.startswith("verify/")])
        self.assertNotIn("config/detekt/", release_paths("android", CMAP))
        for n, node in CMAP["nodes"].items():
            if node["release"]:
                self.assertNotIn(".editorconfig", release_paths(n, CMAP))
                for p in node.get("ci-paths", []):
                    self.assertNotIn(p, release_paths(n, CMAP))

    def test_ci_global_selects_every_node(self):
        self.assertTrue(all(affected([".editorconfig"], CMAP).values()))
        self.assertIsNone(owner_nodes(".editorconfig", CMAP))
        self.assertEqual(unaccounted([".editorconfig"], CMAP), [])


class MapValidationTest(unittest.TestCase):
    def test_wildcard_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            load_map(tmp_map(nodes={"android": {"release": False, "paths": ["app/**"]}}))
        self.assertIn("android", str(ctx.exception))
        self.assertIn("app/**", str(ctx.exception))

    def test_release_node_needs_prefix_and_version(self):
        with self.assertRaises(ValueError) as ctx:
            load_map(tmp_map(nodes={"android": {"release": True, "paths": ["app/"], "initial-version": "0.1.0"}}))
        self.assertIn("tag-prefix", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            load_map(tmp_map(nodes={"android": {"release": True, "paths": ["app/"], "tag-prefix": "android-v"}}))
        self.assertIn("initial-version", str(ctx.exception))

    def test_ci_paths_and_ci_global_reject_wildcards(self):
        with self.assertRaises(ValueError) as ctx:
            load_map(tmp_map(nodes={"android": {"release": False, "paths": ["app/"], "ci-paths": ["verify/*.mjs"]}}))
        self.assertIn("android", str(ctx.exception))
        self.assertIn("verify/*.mjs", str(ctx.exception))
        with self.assertRaises(ValueError):
            load_map(tmp_map(**{"ci-global": ["*.yml"]}))

    def test_node_must_be_object(self):
        with self.assertRaises(ValueError):
            load_map(tmp_map(nodes={"android": ["app/"]}))

    def test_since_must_be_full_sha(self):
        with self.assertRaises(ValueError):
            load_map(tmp_map(since="e98c371"))

    def test_valid_map(self):
        self.assertIn("android", load_map(tmp_map())["nodes"])

    def test_real_map_release_nodes(self):
        rel = {n: v for n, v in CMAP["nodes"].items() if v["release"]}
        self.assertEqual(set(rel), {"android", "web-extension", "installer"})
        self.assertEqual({v["tag-prefix"] for v in rel.values()}, {"android-v", "web-extension-v", "installer-v"})
        self.assertEqual({v["initial-version"] for v in rel.values()}, {"0.1.0"})


class ChangedFilesTest(unittest.TestCase):
    def test_rename_across_components_selects_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, "init", "-q")
            os.makedirs(os.path.join(tmp, "app"))
            os.makedirs(os.path.join(tmp, "web-extension"))
            with open(os.path.join(tmp, "app", "x.kt"), "w") as f:
                f.write("class X\n" * 20)
            git(tmp, "add", "-A")
            git(tmp, "commit", "-q", "-m", "init")
            base = git(tmp, "rev-parse", "HEAD")
            git(tmp, "mv", "app/x.kt", "web-extension/x.kt")
            git(tmp, "commit", "-q", "-m", "move")
            head = git(tmp, "rev-parse", "HEAD")
            files = changed_files(base, head, cwd=tmp)
            self.assertIn("app/x.kt", files)
            self.assertIn("web-extension/x.kt", files)
            a = affected(files, CMAP)
            self.assertTrue(a["android"] and a["web-extension"])
            self.assertFalse(a["installer"])
            self.assertIsNone(changed_files("1" * 40, head, cwd=tmp))


class CliTest(unittest.TestCase):
    def test_repository_is_fully_accounted_for(self):
        self.assertEqual(main(["check"]), 0)

    def test_changed_writes_all_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            self.assertEqual(main(["changed", "--base", "0" * 40, "--github-output", out]), 0)
            with open(out) as handle:
                lines = handle.read().split()
            self.assertEqual(sorted(lines), sorted(n + "=true" for n in NODES))

    def test_changed_unaccounted_exits_1_with_all_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, "init", "-q")
            with open(os.path.join(tmp, "a"), "w") as f:
                f.write("a")
            git(tmp, "add", "-A")
            git(tmp, "commit", "-q", "-m", "a")
            base = git(tmp, "rev-parse", "HEAD")
            os.makedirs(os.path.join(tmp, "newdir"))
            with open(os.path.join(tmp, "newdir", "x"), "w") as f:
                f.write("x")
            git(tmp, "add", "-A")
            git(tmp, "commit", "-q", "-m", "b")
            with tempfile.TemporaryDirectory() as outdir:
                out = os.path.join(outdir, "out")
                rc = main(["changed", "--base", base, "--github-output", out], cwd=tmp)
                self.assertEqual(rc, 1)
                with open(out) as handle:
                    self.assertEqual(sorted(handle.read().split()), sorted(n + "=true" for n in NODES))


if __name__ == "__main__":
    unittest.main()
