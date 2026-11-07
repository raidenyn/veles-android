#!/usr/bin/env python3
"""Build-contract tests for the OTP-24 production host (Task 8).

Standard library only. Run from the repository root:

    python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py'

The checks read the real repository files (Gradle graph, workflows, SBOM / licence /
supply-chain consumers, clean contract, vendored patch provenance). Each `check_*`
function returns a list of violations; the positive tests require an empty list on the
repository, and the negative tests mutate a copy of the text to prove the checker rejects
the specific regression (a missing production artifact silently replaced by the scaffold
output, a Tauri/npm dependency, an unlocked cargo call, outputs outside build/, ...).

Nothing here claims a CI run or a native build happened: these are static contract checks.
"""

import hashlib
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOST = ROOT / "native-bridge" / "host"
VENDOR = HOST / "vendor" / "bluest"

TASKS = (
    "bridgeHostToolchainCheck",
    "bridgeHostFormat",
    "bridgeHostLint",
    "bridgeHostTest",
    "bridgeHostBuild",
)
BEGIN = "// ---- OTP-24 production host (begin) ----"
END = "// ---- OTP-24 production host (end) ----"
SHA_PINNED_USE = re.compile(r"uses:\s*(?:\./|[\w.-]+/[\w./-]+@[0-9a-f]{40}\b)")


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def strip_kotlin_comments(text):
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())


def host_block(gradle):
    if BEGIN not in gradle or END not in gradle:
        return None
    return gradle.split(BEGIN, 1)[1].split(END, 1)[0]


def task_body(code, name):
    """Source of the `register(... "name")` call through the next top-level register/end."""
    match = re.search(r'tasks\.register[^\n]*\("%s"\)' % re.escape(name), code)
    if not match:
        return None
    rest = code[match.start():]
    following = re.search(r"\n(?=val |tasks\.|fun )", rest[1:])
    return rest if not following else rest[: following.start() + 1]


# --------------------------------------------------------------------------- checkers


def check_gradle(gradle, clean_script, toolchain_toml):
    errors = []
    block = host_block(gradle)
    if block is None:
        return ["build.gradle.kts has no delimited OTP-24 production host section"]
    code = strip_kotlin_comments(block)
    for name in TASKS:
        if task_body(code, name) is None:
            errors.append("task %s is not registered" % name)
    # The production graph never touches the scaffold, Node, npm or Tauri.
    for forbidden in ("tauri", "npm", "node", "src-tauri", "bridgeInstall", "bridgeBuild",
                      "bridgeBundle", "bridgePackage", "bridgeHostBinary", "veles-native-bridge",
                      "bridgeRustToolchainCheck", "bridgeNodeCheck"):
        if re.search(r"(?i)(?<![\w])%s(?![\w])" % re.escape(forbidden), code):
            errors.append("production host section references %r" % forbidden)
    # Every cargo build/lint/test invocation is locked; fmt has no lockfile semantics.
    for name in ("bridgeHostLint", "bridgeHostTest", "bridgeHostBuild"):
        body = task_body(code, name)
        if body is None:
            continue
        if "--locked" not in body:
            errors.append("%s does not pass --locked" % name)
        if 'environment("CARGO_TARGET_DIR"' not in body and "productionHostEnvironment" not in body:
            errors.append("%s does not redirect CARGO_TARGET_DIR" % name)
    fmt = task_body(code, "bridgeHostFormat")
    if fmt is not None and '"fmt"' not in fmt:
        errors.append("bridgeHostFormat does not run cargo fmt")
    # CARGO_TARGET_DIR (declared once) and every output stay under the Gradle build dir.
    target = re.search(r"val productionHostTargetDir\s*=\s*([^\n]+)", code)
    if not target or "layout.buildDirectory" not in target.group(1):
        errors.append("productionHostTargetDir is not under layout.buildDirectory")
    output = re.search(r"val productionHostOutputDir\s*=\s*([^\n]+)", code)
    if not output or "layout.buildDirectory" not in output.group(1) or "native-bridge/host" not in output.group(1):
        errors.append("productionHostOutputDir is not build/native-bridge/host")
    build = task_body(code, "bridgeHostBuild")
    if build is not None:
        for need in ('"build"', '"--release"', '"--bin"', '"veles-native-host"'):
            if need not in build:
                errors.append("bridgeHostBuild missing %s" % need)
        for forbidden in ("test-driver", "--all-features", "--features", "--examples", "--all-targets",
                          "--workspace", "host-test-driver"):
            if forbidden in build:
                errors.append("bridgeHostBuild must not use %s" % forbidden)
        if "setExecutable(true" not in build:
            errors.append("bridgeHostBuild does not keep the staged executable runnable")
        if "SHA256SUMS" not in build:
            errors.append("bridgeHostBuild does not emit SHA256SUMS")
        if "isFile" not in build and "exists()" not in build:
            errors.append("bridgeHostBuild does not reject a missing production executable")
        if "host-test-driver" in build:
            errors.append("bridgeHostBuild references the test driver")
    check = task_body(code, "bridgeHostToolchainCheck")
    if check is not None and "1.98.0" not in check:
        errors.append("bridgeHostToolchainCheck does not pin Rust 1.98.0")
    if 'channel = "1.98.0"' not in toolchain_toml:
        errors.append("native-bridge/host/rust-toolchain.toml is not pinned to 1.98.0")
    # Clean contract: Gradle removes and assert-clean asserts the source-tree leftovers.
    clean = gradle.split('tasks.named<Delete>("clean")', 1)
    if len(clean) != 2 or "native-bridge/host/target" not in clean[1] or "native-bridge/host/build" not in clean[1]:
        errors.append("root clean does not delete native-bridge/host/target and native-bridge/host/build")
    for path in ("native-bridge/host/target", "native-bridge/host/build", "build/native-bridge"):
        if path not in clean_script:
            errors.append("assert-clean.sh does not assert %s" % path)
    # Existing scaffold aggregation stays untouched: `check` may add the host, never replace.
    if "bridgeRustTest" not in gradle:
        errors.append("scaffold bridge tasks were removed")
    return errors


def check_host_workflow(source):
    errors = []
    # Explanatory full-line comments may name the scaffold; only executable YAML is checked.
    source = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    if "workflow_call:" not in source:
        errors.append("build-native-host.yml is not reusable")
    if not re.search(r"commit-sha:\s*\n\s*required:\s*true", source):
        errors.append("commit-sha input is not required")
    for runner in ("ubuntu-latest", "windows-2025", "macos-26"):
        if "runs-on: %s" % runner not in source:
            errors.append("no job on %s" % runner)
    if "DEVELOPER_DIR: /Applications/Xcode_26.6.app" not in source or "17F113" not in source:
        errors.append("macOS job does not use the repository Xcode pin")
    # `bridgeInstall` is the scaffold's npm install task; the OTP-25 `bridgeInstaller*` check tasks are not it.
    if re.search(r"(?i)tauri|npm|setup-node|node-version|bridgeInstall(?![A-Za-z])|bridgePackage|bridgeBundle|bridgeBuild\b", source):
        errors.append("host workflow uses Tauri/Node/npm or the scaffold tasks")
    if "needs:" in source:
        errors.append("host workflow must not chain jobs")
    for line in source.splitlines():
        stripped = line.strip().lstrip("- ")
        if stripped.startswith("uses:") and not SHA_PINNED_USE.search(stripped):
            errors.append("unpinned action: %s" % stripped)
    for task in ("bridgeHostToolchainCheck", "bridgeHostFormat", "bridgeHostLint", "bridgeHostTest"):
        if source.count(task) < 3:
            errors.append("%s must run on all three platforms" % task)
    if source.count("bridgeHostBuild") != 2:
        errors.append("bridgeHostBuild must run exactly on Windows and macOS")
    # Distinct artifacts; never the scaffold installer names.
    for need in ("name: native-host-windows", "name: native-host-macos"):
        if need not in source:
            errors.append("missing artifact %s" % need)
    for banned in ("native-windows", "native-macos", "veles-native-bridge", "native-bridge/windows", "native-bridge/macos"):
        if banned in source:
            errors.append("host workflow references scaffold name %s" % banned)
    # Windows uploads the bare exe + checksums; macOS uploads a mode-preserving tar (artifact upload
    # drops the executable bit), never the bare executable.
    for path in ("build/native-bridge/host/veles-native-host.exe", "build/native-bridge/host/SHA256SUMS",
                 "build/native-bridge/host-archive/veles-native-host-macos.tar.gz",
                 "build/native-bridge/host-archive/veles-native-host-macos.tar.gz.sha256"):
        if path not in source:
            errors.append("artifact path %s missing" % path)
    macos = source.split("  macos:", 1)[-1]
    if re.search(r"^\s+build/native-bridge/host/veles-native-host\s*$", macos, re.M):
        errors.append("macOS job uploads the bare executable (upload drops the exec bit); use the tar archive")
    if "package-macos-artifact.sh" not in macos:
        errors.append("macOS job does not package the tar via package-macos-artifact.sh")
    if "if-no-files-found: error" not in source:
        errors.append("artifact upload does not fail on missing files")
    if "secrets" in source:
        errors.append("host workflow must not use secrets")
    if "permissions:\n  contents: read" not in source:
        errors.append("host workflow is not default-read")
    return errors


def check_ci(source):
    errors = []
    job = re.search(r"\n  native-host:\n(.*?)(?=\n  [\w-]+:\n|\Z)", source, re.S)
    if not job:
        return ["ci.yml has no native-host job"]
    if "uses: ./.github/workflows/build-native-host.yml" not in job.group(1):
        errors.append("ci native-host job does not reuse build-native-host.yml")
    if "commit-sha: ${{ github.sha }}" not in job.group(1):
        errors.append("ci native-host job does not pass the commit")
    return errors


def check_sbom_consumers(files):
    errors = []
    if "native-bridge/host/Cargo.toml" not in files["generate"] or "native-host.cdx.json" not in files["generate"]:
        errors.append("generate-sboms.sh does not emit native-host.cdx.json from the host manifest")
    if "normalize-sbom-paths.mjs" not in files["generate"]:
        errors.append("generate-sboms.sh does not normalize the checkout path out of the SBOMs")
    verify = files["verify-sboms"]
    if "absolute checkout path" not in verify:
        errors.append("verify-sboms.mjs does not reject SBOMs containing the checkout path")
    if "'native-host': 'veles-ble-host@0.1.0'" not in verify or "native-bridge', 'host', 'Cargo.lock'" not in verify:
        errors.append("verify-sboms.mjs does not require the native-host root and production lockfile")
    if "native-bridge/host/Cargo.toml" not in files["licenses"]:
        errors.append("cargo-deny licence check omits the host manifest")
    if "native-bridge/host/Cargo.toml" not in files["build-scripts"]:
        errors.append("cargo build-script scan omits the host manifest")
    if "native-bridge/host/src" not in files["remote-code"]:
        errors.append("remote-code scan omits native-bridge/host/src")
    if "check-vendored-patches.mjs" not in files["supply-chain"]:
        errors.append("verify-supply-chain.sh does not run check-vendored-patches.mjs")
    for need in ("build/sbom/native-host.cdx.json", "build/verification/supply-chain/vendored-patches.txt"):
        if need not in files["supply-chain-workflow"]:
            errors.append("build-supply-chain.yml does not publish %s" % need)
    return errors


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


MANIFEST_EXCLUDED = {"VELES-MANIFEST.sha256", "VELES-PATCH.diff", "VELES-PATCH.md"}


def check_vendor_manifest(vendor):
    """Complete reviewed file-and-hash manifest: no edit, addition or deletion anywhere."""
    errors = []
    listing = Path(vendor) / "VELES-MANIFEST.sha256"
    if not listing.is_file():
        return ["VELES-MANIFEST.sha256 is missing"]
    expected = {}
    for line in listing.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or not name or name in expected or name in MANIFEST_EXCLUDED:
            errors.append("invalid manifest entry: %r" % line)
        expected[name] = digest
    actual = {}
    for path in sorted(Path(vendor).rglob("*")):
        if path.is_symlink() or (path.exists() and not path.is_file() and not path.is_dir()):
            errors.append("non-regular vendored entry %s" % path)
        elif path.is_file() and path.relative_to(vendor).as_posix() not in MANIFEST_EXCLUDED:
            actual[path.relative_to(vendor).as_posix()] = sha256(path)
    for name in sorted(set(actual) - set(expected)):
        errors.append("unexpected vendored file %s" % name)
    for name in sorted(set(expected) - set(actual)):
        errors.append("missing vendored file %s" % name)
    for name in sorted(set(expected) & set(actual)):
        if expected[name] != actual[name]:
            errors.append("vendored file %s differs from the reviewed manifest" % name)
    return errors


def check_vendored_patch():
    errors = []
    notes = (VENDOR / "VELES-PATCH.md").read_text(encoding="utf-8")
    rows = re.findall(r"`(src/[\w/.]+)` \| `([0-9a-f]{64})` \| `([0-9a-f]{64})`", notes)
    recorded = {path: patched for path, _, patched in rows}
    pristine = {path: original for path, original, _ in rows}
    if set(recorded) != {"src/lib.rs", "src/corebluetooth/characteristic.rs"}:
        errors.append("VELES-PATCH.md does not record exactly the two patched files")
    for path, digest in recorded.items():
        if sha256(VENDOR / path) != digest:
            errors.append("%s differs from the recorded patched hash" % path)
    # Reverse-applying the recorded diff must reproduce the recorded pristine hashes.
    with tempfile.TemporaryDirectory() as work:
        for path in recorded:
            target = Path(work) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((VENDOR / path).read_bytes())
        result = subprocess.run(["git", "apply", "-R", str(VENDOR / "VELES-PATCH.diff")], cwd=work,
                                capture_output=True, text=True)
        if result.returncode != 0:
            errors.append("VELES-PATCH.diff does not reverse-apply: %s" % result.stderr.strip())
        else:
            for path, digest in pristine.items():
                if sha256(Path(work) / path) != digest:
                    errors.append("reverse-applied %s does not match the recorded pristine hash" % path)
    upstream = re.search(r"upstream crate sha256 `([0-9a-f]{64})`", notes)
    feasibility = (ROOT / "native-bridge" / "feasibility" / "Cargo.lock").read_text(encoding="utf-8")
    locked = re.search(r'name = "bluest"\nversion = "0\.6\.9"\nsource = "registry[^"]*"\nchecksum = "([0-9a-f]{64})"', feasibility)
    if not upstream or not locked or upstream.group(1) != locked.group(1):
        errors.append("VELES-PATCH.md does not record the crates.io checksum that the reviewed lockfile pins")
    errors += check_vendor_manifest(VENDOR)
    manifest = (VENDOR / "Cargo.toml").read_text(encoding="utf-8")
    if 'license = "BSD-2-Clause OR Apache-2.0"' not in manifest:
        errors.append("vendored bluest licence expression changed")
    for licence in ("LICENSE-APACHE", "LICENSE-BSD"):
        if not (VENDOR / licence).is_file():
            errors.append("vendored bluest is missing %s" % licence)
    if re.search(r"(?m)^build\s*=\s*(?!false)\S", manifest):
        errors.append("vendored bluest declares a build script")
    host_manifest = (HOST / "Cargo.toml").read_text(encoding="utf-8")
    if 'bluest = { path = "vendor/bluest" }' not in host_manifest:
        errors.append("host Cargo.toml does not patch bluest from vendor/bluest")
    lock = (HOST / "Cargo.lock").read_text(encoding="utf-8")
    block = re.search(r'\[\[package\]\]\nname = "bluest"\nversion = "0\.6\.9"\n(.*?)\n\n', lock, re.S)
    if not block or block.group(1).lstrip().startswith(("source", "checksum")) or "source =" in block.group(1).split("dependencies")[0]:
        errors.append("host Cargo.lock bluest is not the path-vendored (source-less) package")
    return errors


def check_docs(claude_md, repro_md, readme):
    errors = []
    for name, text in (("CLAUDE.md", claude_md), ("docs/reproducible-builds.md", repro_md)):
        for task in TASKS:
            if task not in text:
                errors.append("%s does not document %s" % (name, task))
        if "native-host-windows" not in text or "native-host-macos" not in text:
            errors.append("%s does not name the native-host-<platform> artifacts" % name)
        if "native-host.cdx.json" not in text:
            errors.append("%s does not name the native-host SBOM" % name)
    if "vendor/bluest" not in repro_md:
        errors.append("docs/reproducible-builds.md does not document the vendored bluest patch")
    if "build/native-bridge/host" not in readme:
        errors.append("host README does not document build/native-bridge/host outputs")
    return errors


# ----------------------------------------------------------------------------- tests


class GradleGraph(unittest.TestCase):
    def setUp(self):
        self.gradle = read("build.gradle.kts")
        self.clean = read("rust/scripts/assert-clean.sh")
        self.toolchain = (HOST / "rust-toolchain.toml").read_text(encoding="utf-8")

    def violations(self, gradle=None, clean=None):
        return check_gradle(gradle or self.gradle, clean or self.clean, self.toolchain)

    def test_dedicated_task_graph_is_locked_pinned_and_under_build(self):
        self.assertEqual(self.violations(), [])

    def test_missing_production_artifact_is_rejected_not_replaced_by_scaffold(self):
        block = host_block(self.gradle)
        self.assertIsNotNone(block)
        mutated = self.gradle.replace(
            block, block.replace("veles-native-host", "veles-native-bridge")
        )
        found = "\n".join(self.violations(mutated))
        self.assertIn("veles-native-bridge", found)
        self.assertIn('bridgeHostBuild missing "veles-native-host"', found)

    def test_unlocked_cargo_call_is_rejected(self):
        block = host_block(self.gradle)
        mutated = self.gradle.replace(block, block.replace('"--locked", ', "", 1) if '"--locked", ' in block
                                      else block.replace('"--locked"', '"--frozen"', 1))
        self.assertTrue(any("--locked" in e for e in self.violations(mutated)))

    def test_tauri_or_npm_dependency_is_rejected(self):
        for injected in ('dependsOn(bridgeInstall)', 'commandLine("npm", "ci")', 'inputs.file("native-bridge/src-tauri/Cargo.toml")'):
            block = host_block(self.gradle)
            mutated = self.gradle.replace(block, block + "\n" + injected + "\n")
            self.assertNotEqual(self.violations(mutated), [], injected)

    def test_test_driver_in_production_build_is_rejected(self):
        block = host_block(self.gradle)
        mutated = self.gradle.replace(block, block.replace('"--release",', '"--release", "--features", "test-driver",', 1))
        self.assertTrue(any("test-driver" in e or "--features" in e for e in self.violations(mutated)))

    def test_target_dir_outside_build_is_rejected(self):
        mutated = re.sub(r"(val productionHostTargetDir\s*=\s*)[^\n]+",
                         r'\1layout.projectDirectory.dir("native-bridge/host/target")', self.gradle)
        self.assertTrue(any("productionHostTargetDir" in e for e in self.violations(mutated)))

    def test_clean_contract_requires_source_tree_leftovers(self):
        mutated = self.clean.replace("native-bridge/host/target", "")
        self.assertTrue(any("native-bridge/host/target" in e for e in self.violations(clean=mutated)))


class Workflows(unittest.TestCase):
    def test_host_workflow_builds_each_supported_os_with_distinct_artifacts(self):
        self.assertEqual(check_host_workflow(read(".github/workflows/build-native-host.yml")), [])

    def test_host_workflow_rejects_scaffold_relabeling(self):
        source = read(".github/workflows/build-native-host.yml")
        for mutated in (
            source.replace("name: native-host-windows", "name: native-windows"),
            source.replace("build/native-bridge/host/SHA256SUMS", "build/native-bridge/windows/SHA256SUMS"),
            source + "\n      - run: ./gradlew bridgePackage\n",
            source.replace("DEVELOPER_DIR: /Applications/Xcode_26.6.app", "DEVELOPER_DIR: /Applications/Xcode.app"),
            source.replace("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "actions/checkout@v4"),
        ):
            self.assertNotEqual(check_host_workflow(mutated), [])

    def test_ci_runs_the_host_workflow(self):
        self.assertEqual(check_ci(read(".github/workflows/ci.yml")), [])
        self.assertNotEqual(check_ci("jobs:\n  native-bridge:\n    runs-on: ubuntu-latest\n"), [])

    def test_native_producers_are_the_installer_producers_not_the_host_and_not_the_scaffold(self):
        # OTP-25 Task 14 deliberately replaced the scaffold's native-windows / native-macos packaging with the
        # unsigned installer producers. The production host workflow still owns native-host-* and is not
        # relabeled as, nor mixed into, either of them.
        for name, artifact in (("build-native-windows.yml", "native-unsigned-windows"), ("build-native-macos.yml", "native-unsigned-macos")):
            # Explanatory full-line comments may name the scaffold they replaced; only executable YAML counts.
            source = "\n".join(line for line in read(".github/workflows/" + name).splitlines() if not line.lstrip().startswith("#"))
            self.assertIn("default: " + artifact, source)
            self.assertNotIn("native-host", source)
            self.assertNotIn("bridgeHost", source)
            self.assertNotIn("bridgePackage", source)
            self.assertNotIn("tauri", source.lower())


class SupplyChain(unittest.TestCase):
    FILES = {
        "generate": "verify/generate-sboms.sh",
        "verify-sboms": "verify/verify-sboms.mjs",
        "licenses": "verify/check-npm-licenses.mjs",
        "build-scripts": "verify/check-cargo-build-scripts.mjs",
        "remote-code": "verify/check-remote-code.mjs",
        "supply-chain": "verify/verify-supply-chain.sh",
        "supply-chain-workflow": ".github/workflows/build-supply-chain.yml",
    }

    def files(self):
        return {key: read(path) for key, path in self.FILES.items()}

    def test_sbom_licence_and_policy_consumers_cover_the_production_lockfile(self):
        self.assertEqual(check_sbom_consumers(self.files()), [])

    def test_dropping_the_production_sbom_is_rejected(self):
        files = self.files()
        files["generate"] = files["generate"].replace("native-host.cdx.json", "")
        files["licenses"] = files["licenses"].replace("native-bridge/host/Cargo.toml", "")
        found = check_sbom_consumers(files)
        self.assertGreaterEqual(len(found), 2)

    def test_vendored_bluest_patch_provenance_and_licence(self):
        self.assertEqual(check_vendored_patch(), [])


class MacosArtifact(unittest.TestCase):
    SCRIPT = HOST / "scripts" / "package-macos-artifact.sh"

    def package(self, files):
        import os
        import stat
        work = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(work, ignore_errors=True))
        host, out = work / "host", work / "archive"
        host.mkdir()
        for name, data in files.items():
            (host / name).write_bytes(data)
        if "veles-native-host" in files:
            (host / "veles-native-host").chmod(0o755)
        result = subprocess.run(["bash", str(self.SCRIPT), str(host), str(out)], capture_output=True, text=True)
        return work, out, result, stat, os

    def test_extraction_contract_preserves_mode_and_contains_only_exe_and_checksums(self):
        digest = hashlib.sha256(b"binary").hexdigest()
        work, out, result, stat, _ = self.package({
            "veles-native-host": b"binary",
            "SHA256SUMS": ("%s  veles-native-host\n" % digest).encode(),
            "host-test-driver": b"must not ship",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        archive = out / "veles-native-host-macos.tar.gz"
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["veles-native-host-macos.tar.gz", "veles-native-host-macos.tar.gz.sha256"])
        sidecar = (out / "veles-native-host-macos.tar.gz.sha256").read_text().split()
        self.assertEqual(sidecar, [sha256(archive), "veles-native-host-macos.tar.gz"])
        listing = subprocess.run(["tar", "-tzf", str(archive)], capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(listing), ["SHA256SUMS", "veles-native-host"])  # no test driver, no extras
        extracted = work / "extracted"
        extracted.mkdir()
        subprocess.run(["tar", "-xzf", str(archive), "-C", str(extracted)], check=True)
        mode = (extracted / "veles-native-host").stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR and mode & stat.S_IXGRP and mode & stat.S_IXOTH, oct(mode))
        # The extracted pair still satisfies the recorded checksum.
        self.assertEqual(sha256(extracted / "veles-native-host"), (extracted / "SHA256SUMS").read_text().split()[0])

    def test_missing_executable_or_checksum_is_rejected_not_substituted(self):
        _, out, result, *_ = self.package({"SHA256SUMS": b"x  veles-native-host\n"})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(out.exists() and any(out.iterdir()))
        _, out, result, *_ = self.package({"veles-native-host": b"binary"})
        self.assertNotEqual(result.returncode, 0)

    def test_checksum_that_does_not_match_the_executable_is_rejected(self):
        _, _, result, *_ = self.package({"veles-native-host": b"binary", "SHA256SUMS": ("%s  veles-native-host\n" % ("0" * 64)).encode()})
        self.assertNotEqual(result.returncode, 0)


class GradleIncremental(unittest.TestCase):
    """Real Gradle runs: the compile-time origin allowlist is a build input, not an invisible env read.

    Skipped when no Gradle wrapper/cargo is usable. Extra Gradle flags (e.g. --offline) come from
    VELES_GRADLE_ARGS. The final run restores the default-environment build.
    """

    A = "chrome-extension://" + "a" * 32 + "/"
    B = "chrome-extension://" + "b" * 32 + "/"

    def gradle(self, origin):
        import os
        import shlex
        import shutil
        if shutil.which("cargo") is None or not (ROOT / "gradlew").is_file():
            self.skipTest("gradle wrapper or cargo unavailable")
        env = {k: v for k, v in os.environ.items() if k != "VELES_ALLOWED_ORIGINS"}
        if origin is not None:
            env["VELES_ALLOWED_ORIGINS"] = origin
        command = ["./gradlew", "--console=plain", *shlex.split(os.environ.get("VELES_GRADLE_ARGS", "")), "bridgeHostBuild"]
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        line = next(l for l in result.stdout.splitlines() if l.startswith("> Task :bridgeHostBuild"))
        return line, (ROOT / "build/native-bridge/host/veles-native-host").read_bytes()

    def test_changing_only_the_allowlist_env_reruns_the_build_and_changes_the_binary(self):
        self.addCleanup(self.gradle, None)
        line_a, binary_a = self.gradle(self.A)
        self.assertNotIn("UP-TO-DATE", line_a)
        line_a2, _ = self.gradle(self.A)
        self.assertIn("UP-TO-DATE", line_a2)  # unchanged inputs stay incremental
        line_b, binary_b = self.gradle(self.B)
        self.assertNotIn("UP-TO-DATE", line_b, "allowlist change must not be hidden by Gradle up-to-date checks")
        self.assertIn(self.B.encode(), binary_b)
        self.assertNotIn(self.A.encode(), binary_b)
        self.assertIn(self.A.encode(), binary_a)

    def test_gradle_declares_the_environment_input(self):
        code = strip_kotlin_comments(host_block(read("build.gradle.kts")))
        helper = code.split("fun Exec.productionHostEnvironment()", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('inputs.property("VELES_ALLOWED_ORIGINS", providers.environmentVariable("VELES_ALLOWED_ORIGINS").orElse(""))', helper)
        for task in ("bridgeHostLint", "bridgeHostTest", "bridgeHostBuild"):
            self.assertIn("productionHostEnvironment()", task_body(code, task), task)


class VendorManifest(unittest.TestCase):
    def copy(self):
        work = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(work, ignore_errors=True))
        target = Path(work) / "bluest"
        __import__("shutil").copytree(VENDOR, target)
        return target

    def test_repository_tree_matches_manifest(self):
        self.assertEqual(check_vendor_manifest(VENDOR), [])

    def test_unpatched_source_edit_is_rejected(self):
        target = self.copy()
        with open(target / "src" / "util.rs", "a", encoding="utf-8") as handle:
            handle.write("// not upstream\n")
        self.assertEqual(check_vendor_manifest(target), ["vendored file src/util.rs differs from the reviewed manifest"])

    def test_added_non_build_file_is_rejected(self):
        for relative in ("src/extra.rs", "payload.bin", "scripts/run.sh"):
            target = self.copy()
            (target / relative).parent.mkdir(parents=True, exist_ok=True)
            (target / relative).write_text("x\n", encoding="utf-8")
            self.assertEqual(check_vendor_manifest(target), ["unexpected vendored file %s" % relative])

    def test_deleted_file_is_rejected(self):
        target = self.copy()
        (target / "src" / "util.rs").unlink()
        self.assertEqual(check_vendor_manifest(target), ["missing vendored file src/util.rs"])


class Documentation(unittest.TestCase):
    def test_docs_distinguish_production_host_from_scaffold(self):
        self.assertEqual(
            check_docs(read("CLAUDE.md"), read("docs/reproducible-builds.md"),
                       (HOST / "README.md").read_text(encoding="utf-8")),
            [],
        )


if __name__ == "__main__":
    unittest.main()
