import contextlib
import io
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
    affected_for_node,
    changed_files,
    load_map,
    main,
    owner_nodes,
    parse_map,
    release_paths,
    selection_spec,
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


def commit_file(cwd, path, text, message):
    full = os.path.join(cwd, path)
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w") as handle:
        handle.write(text)
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", message)
    return git(cwd, "rev-parse", "HEAD")


def write_json(directory, name, data):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return path


def read_outputs(path):
    with open(path, encoding="utf-8") as handle:
        return {
            name: value == "true"
            for name, value in (line.split("=", 1) for line in handle.read().split())
        }


def summary_rows(path):
    with open(path, encoding="utf-8") as handle:
        lines = [line.rstrip("\n") for line in handle if line.strip()]
    if lines[:2] != ["| node | selection | base | reason |", "| --- | --- | --- | --- |"]:
        raise AssertionError("unexpected summary header: %r" % lines[:2])
    rows = {}
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows[cells[0]] = cells[1:]
    return rows


def reversed_json(value):
    if isinstance(value, dict):
        return {key: reversed_json(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [reversed_json(item) for item in reversed(value)]
    return value


def tmp_map(**overrides):
    base = {
        "since": "a" * 40,
        "global": [".github/"],
        "unowned": ["docs/"],
        "global-overrides": {},
        "retired-paths": {},
        "nodes": {
            "android": {
                "release": True,
                "tag-prefix": "android-v",
                "initial-version": "0.1.0",
                "paths": ["app/"],
                "release-paths": ["native-bridge/host/"],
                "jobs": ["lint"],
            },
            "rust": {"release": False, "paths": ["rust/"], "jobs": ["lint", "unit-tests"]},
            "tooling": {"release": False, "paths": ["scripts/"], "jobs": ["unit-tests"]},
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
        self.assertEqual(owner_nodes("native-bridge/host/src/lib.rs", CMAP), {"native-host", "protocol", "supply-chain"})
        self.assertEqual(owner_nodes("scripts/components.py", CMAP), {"tooling"})

    def test_components_json_selects_only_tooling(self):
        self.assertEqual(owner_nodes(".github/components.json", CMAP), {"tooling"})
        a = affected([".github/components.json"], CMAP)
        self.assertTrue(a["tooling"])
        self.assertEqual({n for n, v in a.items() if v}, {"tooling"})
        self.assertEqual(unaccounted([".github/components.json"], CMAP), [])

    def test_editorconfig_selects_prettier_and_ktlint_consumers(self):
        self.assertEqual(owner_nodes(".editorconfig", CMAP), {"android", "web-extension", "bridge-scaffold"})
        a = affected([".editorconfig"], CMAP)
        self.assertEqual({n for n, v in a.items() if v}, {"android", "web-extension", "bridge-scaffold"})
        self.assertEqual(unaccounted([".editorconfig"], CMAP), [])

    def test_protocol_change_does_not_select_installer(self):
        a = affected(["protocol/v1/limits.json"], CMAP)
        self.assertFalse(a["installer"])
        self.assertTrue(a["native-host"])

    def test_host_toolchain_selects_installer(self):
        self.assertIn("native-bridge/host/rust-toolchain.toml", CMAP["nodes"]["installer"]["ci-paths"])
        self.assertTrue(affected(["native-bridge/host/rust-toolchain.toml"], CMAP)["installer"])

    def test_toolchain_ci_path_is_not_a_release_input(self):
        toolchain = "native-bridge/host/rust-toolchain.toml"
        self.assertIn(toolchain, CMAP["nodes"]["installer"]["ci-paths"])
        for p in release_paths("installer", CMAP):
            if p.endswith("/"):
                self.assertFalse(toolchain.startswith(p), "release path %r covers %r" % (p, toolchain))
            else:
                self.assertNotEqual(toolchain, p, "release input %r is the CI-only toolchain" % p)

    def test_override_selects_only_listed_nodes(self):
        cmap = load_map(tmp_map(**{"global-overrides": {".github/components.json": ["tooling"]}}))
        self.assertEqual(owner_nodes(".github/components.json", cmap), {"tooling"})
        a = affected([".github/components.json"], cmap)
        self.assertTrue(a["tooling"])
        self.assertFalse(a["android"])
        self.assertFalse(a["rust"])
        self.assertEqual(unaccounted([".github/components.json"], cmap), [])

    def test_override_still_counts_for_release(self):
        cmap = load_map(tmp_map(**{"global-overrides": {".github/components.json": ["tooling"]}}))
        self.assertIn(".github/", release_paths("android", cmap))

    def test_retired_path_is_accounted_and_selects_listed_nodes(self):
        cmap = load_map(tmp_map(
            nodes={"android": {"release": False, "paths": ["android/"]}},
            **{"retired-paths": {"app/": ["android"]}},
        ))
        self.assertEqual(owner_nodes("app/x.kt", cmap), {"android"})
        self.assertEqual(unaccounted(["app/x.kt"], cmap), [])
        a = affected(["app/x.kt"], cmap)
        self.assertTrue(a["android"])

    def test_release_path_only_is_accounted_but_selects_nothing(self):
        cmap = load_map(tmp_map())
        self.assertEqual(owner_nodes("native-bridge/host/src/lib.rs", cmap), set())
        self.assertEqual(unaccounted(["native-bridge/host/src/lib.rs"], cmap), [])
        self.assertFalse(any(affected(["native-bridge/host/src/lib.rs"], cmap).values()))

    def test_no_base_selects_all(self):
        self.assertIsNone(changed_files("0" * 40))
        self.assertIsNone(changed_files(None))
        self.assertIsNone(changed_files(""))
        self.assertTrue(all(affected(None, CMAP).values()))
        self.assertFalse(any(affected([], CMAP).values()))
        self.assertTrue(affected_for_node("android", None, CMAP))
        self.assertTrue(affected_for_node("android", ["app/a.kt"], CMAP))
        self.assertFalse(affected_for_node("android", ["docs/ci.md"], CMAP))

    def test_release_paths(self):
        self.assertIn("build.gradle.kts", release_paths("installer", CMAP))
        self.assertIn("native-bridge/installer/", release_paths("installer", CMAP))
        self.assertIn("native-bridge/host/Cargo.toml", release_paths("installer", CMAP))
        self.assertIn("native-bridge/host/src/", release_paths("installer", CMAP))
        self.assertIn("protocol/", release_paths("installer", CMAP))
        self.assertNotIn("app/", release_paths("installer", CMAP))
        self.assertNotIn("native-bridge/host/", release_paths("installer", CMAP))
        self.assertNotIn("native-bridge/host/", CMAP["nodes"]["installer"]["paths"])
        self.assertNotIn("protocol/", CMAP["nodes"]["installer"]["paths"])

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

    def test_ci_paths_never_in_release_paths(self):
        cmap = load_map(tmp_map(
            **{"ci-global": [".editorconfig"]},
            nodes={"android": {
                "release": False,
                "paths": ["app/"],
                "ci-paths": ["verify/check.mjs"],
                "release-paths": ["native-bridge/host/"],
            }},
        ))
        rp = release_paths("android", cmap)
        self.assertIn("app/", rp)
        self.assertIn("native-bridge/host/", rp)
        self.assertNotIn("verify/check.mjs", rp)
        self.assertNotIn(".editorconfig", rp)

    def test_release_paths_excludes_legacy_and_retired(self):
        cmap = load_map(tmp_map(
            nodes={"android": {
                "release": True,
                "tag-prefix": "android-v",
                "initial-version": "0.1.0",
                "paths": ["android/"],
                "release-paths": ["native-bridge/host/"],
                "legacy-release": {"paths": ["app/", "build.gradle.kts"], "epoch-path": "android/"},
            }},
            **{"retired-paths": {"app/": ["android"], "config/detekt/": ["android"]}},
        ))
        rp = release_paths("android", cmap)
        self.assertIn("android/", rp)
        self.assertIn("native-bridge/host/", rp)
        self.assertIn(".github/", rp)
        for p in ("app/", "build.gradle.kts", "config/detekt/"):
            self.assertNotIn(p, rp)


class SelectionSpecTest(unittest.TestCase):
    def test_selection_spec_ignores_key_order_and_whitespace(self):
        path = tmp_map(**{"global-overrides": {".github/components.json": ["tooling"]}})
        cmap = load_map(path)
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        reordered = parse_map(json.dumps(reversed_json(raw), indent=4))
        for node in cmap["nodes"]:
            self.assertEqual(selection_spec(cmap, node), selection_spec(reordered, node), node)

    def test_selection_spec_changes_when_override_membership_changes(self):
        before = load_map(tmp_map(**{"global-overrides": {".github/components.json": ["tooling"]}}))
        after = load_map(tmp_map(**{"global-overrides": {".github/components.json": ["tooling", "rust"]}}))
        self.assertNotEqual(selection_spec(before, "android"), selection_spec(after, "android"))

    def test_selection_spec_changes_when_override_removed(self):
        before = load_map(tmp_map(**{"global-overrides": {".github/components.json": ["tooling"]}}))
        after = load_map(tmp_map())
        self.assertNotEqual(selection_spec(before, "android"), selection_spec(after, "android"))

    def test_selection_spec_changes_when_node_paths_expand(self):
        before = load_map(tmp_map())
        expanded = {
            "android": {
                "release": True,
                "tag-prefix": "android-v",
                "initial-version": "0.1.0",
                "paths": ["app/", "core/"],
                "jobs": ["lint"],
            },
        }
        after = load_map(tmp_map(nodes=expanded))
        self.assertNotEqual(selection_spec(before, "android"), selection_spec(after, "android"))


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

    def test_parse_map_validates_text(self):
        path = tmp_map()
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(parse_map(text), load_map(path))
        with self.assertRaises(ValueError):
            parse_map("{}")

    def test_wildcard_in_new_lists_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            load_map(tmp_map(nodes={"android": {"release": False, "paths": ["app/"], "release-paths": ["app/**"]}}))
        self.assertIn("app/**", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            load_map(tmp_map(nodes={
                "android": {"release": False, "paths": ["app/"], "legacy-release": {"paths": ["app/*"], "epoch-path": "android/"}},
            }))
        self.assertIn("app/*", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            load_map(tmp_map(**{"global-overrides": {".github/*.yml": ["android"]}}))
        self.assertIn(".github/*.yml", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            load_map(tmp_map(**{"retired-paths": {"app/*": ["android"]}}))
        self.assertIn("app/*", str(ctx.exception))

    def test_override_unknown_node_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            load_map(tmp_map(**{"global-overrides": {".github/components.json": ["nope"]}}))
        self.assertIn("nope", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            load_map(tmp_map(**{"retired-paths": {"app/": ["nope"]}}))
        self.assertIn("nope", str(ctx.exception))

    def test_unknown_key_value_types_rejected(self):
        for overrides in (
            {"global-overrides": ["android"]},
            {"global-overrides": {".github/components.json": "tooling"}},
            {"global-overrides": {".github/components.json": [1]}},
            {"global-overrides": {".github/components.json": []}},
            {"global-overrides": {".github/": ["tooling"]}},
            {"retired-paths": ["app/"]},
            {"retired-paths": {"app/": "android"}},
            {"retired-paths": {"app/": []}},
        ):
            with self.assertRaises(ValueError, msg=repr(overrides)):
                load_map(tmp_map(**overrides))
        for node in (
            {"release": False, "paths": ["app/"], "jobs": "lint"},
            {"release": False, "paths": ["app/"], "jobs": [1]},
            {"release": False, "paths": ["app/"], "release-paths": "native-bridge/host/"},
            {"release": False, "paths": ["app/"], "legacy-release": ["app/"]},
            {"release": False, "paths": ["app/"], "legacy-release": {"paths": "app/"}},
            {"release": False, "paths": ["app/"], "legacy-release": {"paths": ["app/"]}},
            {"release": False, "paths": ["app/"], "legacy-release": {"paths": ["app/"], "epoch-path": ""}},
        ):
            with self.assertRaises(ValueError, msg=repr(node)):
                load_map(tmp_map(nodes={"android": node}))

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


class AuxiliaryInputValidationTest(unittest.TestCase):
    """--node-bases and --evidence-notes are validated before anything is written."""

    def run_changed(self, extra):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            summary = os.path.join(tmp, "summary.md")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                rc = main(
                    [
                        "changed", "--base", "0" * 40,
                        "--github-output", out, "--summary-out", summary,
                    ] + extra,
                    cwd=REPO,
                )
            return rc, stderr.getvalue(), os.path.exists(out), os.path.exists(summary)

    def test_node_bases_reject_unknown_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(tmp, "bases.json", {"nope": "a" * 40})
            rc, stderr, out_exists, summary_exists = self.run_changed(["--node-bases", path])
        self.assertEqual(rc, 1)
        self.assertIn("nope", stderr)
        self.assertIn("--node-bases", stderr)
        self.assertFalse(out_exists)
        self.assertFalse(summary_exists)

    def test_node_bases_reject_invalid_values(self):
        for value in (7, "", "abc", "A" * 40, "1" * 39, "1" * 41, True):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as tmp:
                    path = write_json(tmp, "bases.json", {"android": value})
                    rc, stderr, out_exists, _ = self.run_changed(["--node-bases", path])
                self.assertEqual(rc, 1)
                self.assertIn("--node-bases", stderr)
                self.assertIn("android", stderr)
                self.assertFalse(out_exists)

    def test_node_bases_accept_null_and_full_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(tmp, "bases.json", {"android": None, "rust": "a" * 40})
            rc, stderr, out_exists, _ = self.run_changed(["--node-bases", path])
        self.assertEqual(rc, 0, stderr)
        self.assertTrue(out_exists)

    def test_evidence_notes_reject_unknown_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(tmp, "notes.json", {"nope": "never passed"})
            rc, stderr, out_exists, summary_exists = self.run_changed(
                ["--evidence-notes", path]
            )
        self.assertEqual(rc, 1)
        self.assertIn("nope", stderr)
        self.assertIn("--evidence-notes", stderr)
        self.assertFalse(out_exists)
        self.assertFalse(summary_exists)

    def test_evidence_notes_reject_invalid_values(self):
        for value in ("failed", "", None, 7, "Never passed"):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as tmp:
                    path = write_json(tmp, "notes.json", {"android": value})
                    rc, stderr, out_exists, _ = self.run_changed(
                        ["--evidence-notes", path]
                    )
                self.assertEqual(rc, 1)
                self.assertIn("--evidence-notes", stderr)
                self.assertIn("android", stderr)
                self.assertFalse(out_exists)

    def test_evidence_notes_accept_the_two_documented_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(
                tmp, "notes.json",
                {"android": "never passed", "rust": "incompatible map"},
            )
            rc, stderr, out_exists, _ = self.run_changed(["--evidence-notes", path])
        self.assertEqual(rc, 0, stderr)
        self.assertTrue(out_exists)


class PerNodeSelectionTest(unittest.TestCase):
    def test_changed_all_selects_every_node_but_still_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, "init", "-q")
            base = commit_file(tmp, "app/base.kt", "class Base\n", "base")
            commit_file(tmp, "newdir/x", "x\n", "unaccounted")
            out = os.path.join(tmp, "out")
            summary = os.path.join(tmp, "summary.md")
            rc = main(
                ["changed", "--base", base, "--all", "--github-output", out, "--summary-out", summary],
                cwd=tmp,
            )
            self.assertEqual(rc, 1)
            self.assertEqual(read_outputs(out), {n: True for n in NODES})
            rows = summary_rows(summary)
            self.assertEqual(set(rows), NODES)
            for node in NODES:
                self.assertEqual(rows[node], ["run", "none", "fallback"], node)

    def test_node_bases_skip_unchanged_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, "init", "-q")
            parent = commit_file(tmp, "app/x.kt", "class X\n", "base")
            head = commit_file(tmp, "docs/ci.md", "docs\n", "docs")
            bases = write_json(tmp, "bases.json", {n: parent for n in NODES})
            out = os.path.join(tmp, "out")
            summary = os.path.join(tmp, "summary.md")
            rc = main(
                [
                    "changed", "--base", parent, "--head", head, "--node-bases", bases,
                    "--github-output", out, "--summary-out", summary,
                ],
                cwd=tmp,
            )
            self.assertEqual(rc, 0)
            outputs = read_outputs(out)
            self.assertFalse(outputs["android"])
            self.assertFalse(any(outputs.values()))
            self.assertEqual(
                summary_rows(summary)["android"], ["skip", parent, "unchanged since " + parent]
            )

    def test_evidence_note_uses_full_diff_selection(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as state:
            git(tmp, "init", "-q")
            base = commit_file(tmp, "app/base.kt", "class Base\n", "base")
            docs_head = commit_file(tmp, "docs/ci.md", "docs\n", "docs")
            bases = write_json(state, "bases.json", {n: base for n in NODES})
            notes = write_json(
                state, "notes.json", {"android": "never passed", "web-extension": "incompatible map"}
            )

            out = os.path.join(state, "out")
            summary = os.path.join(state, "summary.md")
            rc = main(
                [
                    "changed", "--base", base, "--head", docs_head, "--node-bases", bases,
                    "--evidence-notes", notes, "--github-output", out, "--summary-out", summary,
                ],
                cwd=tmp,
            )
            self.assertEqual(rc, 0)
            self.assertFalse(any(read_outputs(out).values()))
            rows = summary_rows(summary)
            self.assertEqual(rows["android"], ["skip", "none", "never passed"])
            self.assertEqual(rows["web-extension"], ["skip", "none", "incompatible map"])

            app_head = commit_file(tmp, "app/feature.kt", "class Feature\n", "feature")
            out = os.path.join(state, "out-app")
            summary = os.path.join(state, "summary-app.md")
            rc = main(
                [
                    "changed", "--base", base, "--head", app_head, "--node-bases", bases,
                    "--evidence-notes", notes, "--github-output", out, "--summary-out", summary,
                ],
                cwd=tmp,
            )
            self.assertEqual(rc, 0)
            outputs = read_outputs(out)
            self.assertTrue(outputs["android"])
            self.assertFalse(outputs["web-extension"])
            rows = summary_rows(summary)
            self.assertEqual(rows["android"], ["run", "none", "never passed"])
            self.assertEqual(rows["web-extension"], ["skip", "none", "incompatible map"])

    def test_node_without_base_uses_full_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, "init", "-q")
            base = commit_file(tmp, "app/base.kt", "class Base\n", "base")
            commit_file(tmp, "docs/ci.md", "docs\n", "docs")
            head = commit_file(tmp, "app/x.kt", "class X\n", "feature")
            bases = write_json(tmp, "bases.json", {"rust": base})
            out = os.path.join(tmp, "out")
            summary = os.path.join(tmp, "summary.md")
            rc = main(
                [
                    "changed", "--base", base, "--head", head, "--node-bases", bases,
                    "--github-output", out, "--summary-out", summary,
                ],
                cwd=tmp,
            )
            self.assertEqual(rc, 0)
            outputs = read_outputs(out)
            self.assertTrue(outputs["android"])
            self.assertFalse(outputs["rust"])
            rows = summary_rows(summary)
            self.assertEqual(rows["android"], ["run", "none", "never passed"])
            self.assertEqual(rows["rust"], ["skip", base, "unchanged since " + base])

    def test_unusable_node_base_falls_back_to_full_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, "init", "-q")
            base = commit_file(tmp, "app/base.kt", "class Base\n", "base")
            head = commit_file(tmp, "app/x.kt", "class X\n", "feature")
            bases = write_json(tmp, "bases.json", {"android": "1" * 40, "rust": base})
            out = os.path.join(tmp, "out")
            summary = os.path.join(tmp, "summary.md")
            rc = main(
                [
                    "changed", "--base", base, "--head", head, "--node-bases", bases,
                    "--github-output", out, "--summary-out", summary,
                ],
                cwd=tmp,
            )
            self.assertEqual(rc, 0)
            outputs = read_outputs(out)
            self.assertTrue(outputs["android"])
            self.assertFalse(outputs["rust"])
            rows = summary_rows(summary)
            self.assertEqual(rows["android"], ["run", "none", "fallback"])
            self.assertEqual(rows["rust"], ["skip", base, "unchanged since " + base])

    def test_validation_uses_merge_commit_diff_after_master_advanced(self):
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, "init", "-q")
            main_branch = git(tmp, "symbolic-ref", "--short", "HEAD")
            branch_base = commit_file(tmp, "app/base.kt", "class Base\n", "base")
            git(tmp, "checkout", "-q", "-b", "pr")
            pr_head = commit_file(tmp, "app/feature.kt", "class Feature\n", "feature")
            git(tmp, "checkout", "-q", main_branch)
            commit_file(tmp, "rust/master-only.rs", "// master\n", "master rust")
            commit_file(tmp, "newdir/master-only", "master\n", "master unaccounted")
            master_tip = git(tmp, "rev-parse", "HEAD")
            git(tmp, "merge", "-q", "--no-ff", "pr", "-m", "merge pr")
            merge_head = git(tmp, "rev-parse", "HEAD")
            bases = write_json(tmp, "bases.json", {n: branch_base for n in NODES})
            out = os.path.join(tmp, "out")
            summary = os.path.join(tmp, "summary.md")
            rc = main(
                [
                    "changed", "--base", master_tip, "--head", merge_head, "--node-head", pr_head,
                    "--node-bases", bases, "--github-output", out, "--summary-out", summary,
                ],
                cwd=tmp,
            )
            self.assertEqual(rc, 0)
            outputs = read_outputs(out)
            self.assertTrue(outputs["android"])
            self.assertFalse(outputs["rust"])
            self.assertFalse(outputs["web-extension"])
            rows = summary_rows(summary)
            self.assertEqual(rows["android"], ["run", branch_base, "changed since " + branch_base])
            self.assertEqual(rows["rust"], ["skip", branch_base, "unchanged since " + branch_base])

    def test_unaccounted_path_fails_after_outputs_in_every_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, "init", "-q")
            base = commit_file(tmp, "app/base.kt", "class Base\n", "base")
            head = commit_file(tmp, "newdir/x", "x\n", "unaccounted")
            usable = write_json(tmp, "bases.json", {n: base for n in NODES})
            unusable = write_json(tmp, "unusable.json", {n: "1" * 40 for n in NODES})
            modes = [
                ("first run", []),
                ("node-bases", ["--node-bases", usable]),
                ("--all", ["--all"]),
                ("fallback", ["--node-bases", unusable]),
            ]
            for index, (label, extra) in enumerate(modes):
                with self.subTest(mode=label):
                    out = os.path.join(tmp, "out-%d" % index)
                    summary = os.path.join(tmp, "summary-%d.md" % index)
                    rc = main(
                        [
                            "changed", "--base", base, "--head", head,
                            "--github-output", out, "--summary-out", summary,
                        ] + extra,
                        cwd=tmp,
                    )
                    self.assertEqual(rc, 1, label)
                    self.assertEqual(read_outputs(out), {n: True for n in NODES}, label)
                    rows = summary_rows(summary)
                    self.assertEqual(set(rows), NODES, label)
                    for node in NODES:
                        self.assertEqual(rows[node], ["run", "none", "fallback"], (label, node))

    def test_summary_rows_for_every_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, "init", "-q")
            base = commit_file(tmp, "app/base.kt", "class Base\n", "base")
            head = commit_file(tmp, "app/feature.kt", "class Feature\n", "feature")
            out = os.path.join(tmp, "out")
            summary = os.path.join(tmp, "summary.md")

            rc = main(
                ["changed", "--base", base, "--head", head, "--github-output", out, "--summary-out", summary],
                cwd=tmp,
            )
            self.assertEqual(rc, 0)
            self.assertEqual(summary_rows(summary)["android"], ["run", "none", "never passed"])

            bases = write_json(tmp, "bases.json", {n: base for n in NODES})
            notes = write_json(
                tmp, "notes.json", {"web-extension": "incompatible map", "installer": "never passed"}
            )
            rc = main(
                [
                    "changed", "--base", base, "--head", head, "--node-bases", bases,
                    "--evidence-notes", notes, "--github-output", out, "--summary-out", summary,
                ],
                cwd=tmp,
            )
            self.assertEqual(rc, 0)
            rows = summary_rows(summary)
            self.assertEqual(rows["android"], ["run", base, "changed since " + base])
            self.assertEqual(rows["rust"], ["skip", base, "unchanged since " + base])
            self.assertEqual(rows["web-extension"], ["skip", "none", "incompatible map"])
            self.assertEqual(rows["installer"], ["skip", "none", "never passed"])

            fallback = write_json(tmp, "fallback.json", {"android": "2" * 40, "rust": base})
            rc = main(
                [
                    "changed", "--base", base, "--head", head, "--node-bases", fallback,
                    "--github-output", out, "--summary-out", summary,
                ],
                cwd=tmp,
            )
            self.assertEqual(rc, 0)
            self.assertEqual(summary_rows(summary)["android"], ["run", "none", "fallback"])

            rc = main(
                ["changed", "--base", base, "--head", head, "--all", "--github-output", out, "--summary-out", summary],
                cwd=tmp,
            )
            self.assertEqual(rc, 0)
            rows = summary_rows(summary)
            for node in NODES:
                self.assertEqual(rows[node], ["run", "none", "fallback"], node)


if __name__ == "__main__":
    unittest.main()
