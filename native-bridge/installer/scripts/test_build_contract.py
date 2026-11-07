#!/usr/bin/env python3
"""Static build-contract tests for the OTP-25 installer producers (Task 14).

Standard library only. Run from the repository root:

    python3 -m unittest discover -s native-bridge/installer/scripts -p test_build_contract.py

The checks read the real repository files (the Gradle producer graph, the native producer and caller
workflows, the supply-chain consumers, the clean contract and the release schema). Each ``check_*``
function returns a list of violations. Every positive test requires an empty list on the repository;
every invariant ALSO has a negative test that mutates a copy of the text and proves the checker names
that specific regression. A checker that cannot fail would be a snapshot, not a contract.

Nothing here claims that a build, a CI run or a native producer executed: these are static checks.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INSTALLER = ROOT / "native-bridge" / "installer"

BEGIN = "// ---- OTP-25 installer producers (begin) ----"
END = "// ---- OTP-25 installer producers (end) ----"

# The only executables a producer may build, and where each belongs.
ALLOWED_BINS = ("veles-bridge-maintenance", "veles-bridge-cleanup", "veles-native-host")
FORBIDDEN_CARGO_FLAGS = (
    "--all-features", "--features", "--no-default-features", "--workspace", "--all-targets", "--examples",
    "--bins", "--tests", "--benches", "--all", "test-driver", "host-test-driver", "--frozen", "--offline",
)
ONE_PUBLIC_TASKS = (
    "bridgeInstallerFormat", "bridgeInstallerLint", "bridgeInstallerTest", "bridgeInstallerScriptsTest",
    "bridgeInstallerUnsigned", "bridgeInstallerPackageFixture", "bridgeInstallerSupplyChain",
)
SHA_PINNED_USE = re.compile(r"uses:\s*(?:\./|[\w.-]+/[\w./-]+@[0-9a-f]{40}\b)")

# The unchanged Android publication conditions (Task 14 must not touch them).
PUBLISH_NEEDS = "needs: [guard, android, web-extension, rust, supply-chain, native-windows, native-macos]"
PUBLISH_IF = (
    "if: |\n"
    "      always() &&\n"
    "      github.event_name == 'workflow_dispatch' &&\n"
    "      github.ref == 'refs/heads/master' &&\n"
    "      needs.guard.result == 'success' &&\n"
    "      needs.android.result == 'success' &&\n"
    "      needs.web-extension.result == 'success' &&\n"
    "      needs.rust.result == 'success' &&\n"
    "      needs.supply-chain.result == 'success' &&\n"
    "      needs.native-windows.result == 'success' &&\n"
    "      needs.native-macos.result == 'success'"
)
ANDROID_IF = (
    "    if: |\n"
    "      always() &&\n"
    "      (github.event_name == 'push' || github.event_name == 'workflow_dispatch' ||\n"
    "      (github.event_name == 'pull_request' && (\n"
    "        (github.event.action == 'labeled' && github.event.label.name == 'release-build') ||\n"
    "        (github.event.action == 'synchronize' && contains(github.event.pull_request.labels.*.name, 'release-build'))\n"
    "      ))) &&\n"
    "      (github.event_name != 'workflow_dispatch' || needs.guard.result == 'success')"
)


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def strip_kotlin_comments(text):
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())


def strip_yaml_comments(text):
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def producer_block(gradle):
    if BEGIN not in gradle or END not in gradle:
        return None
    return gradle.split(BEGIN, 1)[1].split(END, 1)[0]


def command_lines(code):
    """Every ``commandLine(...)`` argument list in the code, as lists of string literals."""
    calls = []
    for match in re.finditer(r"commandLine\(", code):
        depth, index = 1, match.end()
        while index < len(code) and depth:
            depth += {"(": 1, ")": -1}.get(code[index], 0)
            index += 1
        calls.append(code[match.end(): index - 1])
    return calls


def literals(call):
    return re.findall(r'"([^"]*)"', call)


def object_block(code, marker):
    """The balanced ``{...}`` body of the first registration whose text contains ``marker``."""
    index = code.find(marker)
    if index < 0:
        return None
    start = code.find("{", index + len(marker))
    if start < 0:
        return None
    depth = 0
    for position in range(start, len(code)):
        if code[position] == "{":
            depth += 1
        elif code[position] == "}":
            depth -= 1
            if depth == 0:
                return code[start : position + 1]
    return None


# --------------------------------------------------------------------------- Gradle


def check_gradle(gradle, clean_script):
    errors = []
    block = producer_block(gradle)
    if block is None:
        return ["build.gradle.kts has no delimited OTP-25 installer producer section"]
    code = strip_kotlin_comments(block)

    # 1. The graph registers every public task, and never touches the Tauri scaffold or Node.
    for name in ONE_PUBLIC_TASKS:
        if not re.search(r'["\s(]%s["\s)]' % re.escape(name), code):
            errors.append("task %s is not registered" % name)
    for forbidden in ("tauri", "npm", "node", "src-tauri", "bridgeInstall", "bridgeBuild", "bridgeBundle",
                      "bridgePackage", "bridgeManifests", "bridgeHostBinary", "veles-native-bridge",
                      "bridgeRustToolchainCheck", "bridgeNodeCheck"):
        if re.search(r"(?i)(?<![\w])%s(?![\w])" % re.escape(forbidden), code):
            errors.append("installer producer section references the scaffold/Node/Tauri path %r" % forbidden)

    # 2. Locked cargo, release, explicit production binaries only, no all-features/simulator/test driver.
    builds = [call for call in command_lines(code) if '"cargo"' in call and '"build"' in call]
    if len(builds) < 2:
        errors.append("expected an installer-engine and a host cargo build, found %d" % len(builds))
    seen = set()
    for call in builds:
        words = literals(call)
        if "--locked" not in words:
            errors.append("a cargo build does not pass --locked")
        if "--release" not in words:
            errors.append("a cargo build is not --release")
        bins = [words[i + 1] for i, word in enumerate(words) if word == "--bin" and i + 1 < len(words)]
        if not bins:
            errors.append("a cargo build does not name its binaries with --bin")
        for name in bins:
            seen.add(name)
            if name not in ALLOWED_BINS:
                errors.append("a cargo build names a binary outside the production set: %s" % name)
        for flag in FORBIDDEN_CARGO_FLAGS:
            if flag in words or any(flag in word for word in words if word.startswith("-") or "driver" in word):
                errors.append("a cargo build must not use %s" % flag)
        if "--target" not in words:
            errors.append("a cargo build does not pin its --target triple")
    for need in ALLOWED_BINS[:1] + ALLOWED_BINS[2:]:
        if need not in seen:
            errors.append("no cargo build produces %s" % need)
    if re.search(r'"cargo",\s*"install"', code):
        errors.append("the producer graph must not cargo install anything")
    lint = [call for call in command_lines(code) if '"clippy"' in call]
    if not lint or any("--locked" not in call or '"-D"' not in call or '"warnings"' not in call for call in lint):
        errors.append("bridgeInstallerLint must run locked clippy with -D warnings")
    test = [call for call in command_lines(code) if '"cargo"' in call and '"test"' in call]
    if not test or any("--locked" not in call for call in test):
        errors.append("bridgeInstallerTest must run locked cargo test")
    fmt = [call for call in command_lines(code) if '"fmt"' in call]
    if not fmt or any('"--check"' not in call for call in fmt):
        errors.append("bridgeInstallerFormat must run cargo fmt --check")

    # 3. All output under build/: every cargo target, swift scratch and output root derives from it.
    for variable in ("installerBuildRoot",):
        match = re.search(r"val %s\s*=\s*([^\n]+)" % variable, code)
        if not match or "layout.buildDirectory" not in match.group(1) or "native-bridge/installer" not in match.group(1):
            errors.append("%s is not build/native-bridge/installer under layout.buildDirectory" % variable)
    for env in re.finditer(r'environment\("CARGO_TARGET_DIR",([^\n]*)', code):
        if "installerBuildRoot" not in env.group(1) and "TargetDir" not in env.group(1):
            errors.append("a CARGO_TARGET_DIR is not derived from the build root: %s" % env.group(1).strip())
    if re.search(r'projectDirectory\.dir\("native-bridge/installer/(?:target|macos/\.build)"', code):
        errors.append("a producer writes into the source tree (target or .build)")
    if "--scratch-path" not in code and "swift" in code:
        errors.append("the Swift build does not redirect its scratch path under build/")

    # 4. Independent A/B: two explicit slots, each with its own target directories, wiped first.
    if not re.search(r'listOf\("a",\s*"b"\)', code):
        errors.append("the producer does not define the two explicit A/B slots")
    slot_dirs = [d for d in re.findall(r'dir\("target/([^"]*)"\)', code) if d != "check"]
    if len(slot_dirs) < 3 or any("$channel" not in d or "$slot" not in d for d in slot_dirs):
        errors.append("cargo target directories are not distinct per channel and slot")
    for task in re.split(r"(?=tasks\.register)", code):
        if not task.startswith("tasks.register"):
            continue
        if ('"cargo"' in task and '"build"' in task) or "\"swift\"" in task and "--scratch-path" in task and "--show-bin-path" not in task:
            if "delete(" not in task:
                errors.append("a build task does not delete its target directory first (stale cache reuse)")
    if code.count("--remap-path-prefix") < 2 or "cargoRegistryRemapRustFlags()" not in code:
        errors.append("RUSTFLAGS do not remap both the checkout and the cargo registry paths")
    if "projectDirectory" not in code or "/veles-src" not in code:
        errors.append("the checkout path is not remapped to a fixed prefix")
    if "/Brepro" not in code:
        errors.append("the Windows link does not request deterministic output (/Brepro)")
    if not re.search(r'fun Exec\.installerCargoEnvironment\(\) \{(?:(?!\n\}).)*environment\("RUSTFLAGS"', code, re.S):
        errors.append("RUSTFLAGS is not set explicitly (an ambient value could drop the remap)")

    # 5. The exact origin is a tracked build input of the host build.
    if 'environment("VELES_ALLOWED_ORIGINS"' not in code or 'inputs.property("VELES_ALLOWED_ORIGINS"' not in code:
        errors.append("VELES_ALLOWED_ORIGINS is not both a build input and the host build environment")
    if '"origin"' not in code:
        errors.append("the origin is not derived from the tracked config via assemble_unsigned.py origin")
    if not re.search(r"inputs\.files?\(config\)", code):
        errors.append("the release config is not declared as a task input")

    # 5b. The authenticated carrier is assembled FIRST, verified against the exact descriptor and
    # channel, and only then compiled into the engine that is published. A production build cannot
    # build without it; a fixture build consumes its own channel's assembly; no property may point
    # the engine at an unverified file.
    if 'gradleProperty("installerEmbeddedCarrier")' in code:
        errors.append("the embedded carrier must come from the assembler, never from a gradle property")
    embedded = object_block(code, '"${taskName}EngineEmbedded$suffix"')
    if embedded is None:
        errors.append("no engine build compiles the assembled carrier in")
    else:
        if 'environment("VELES_BUILD_CHANNEL", channel)' not in embedded:
            errors.append("the carrier-bearing engine build does not declare its channel")
        if 'environment("VELES_EMBEDDED_CARRIER"' not in embedded:
            errors.append("the carrier-bearing engine build does not pass VELES_EMBEDDED_CARRIER")
        if "embedded-carrier.bin" not in embedded:
            errors.append("the embedded carrier is not the assembler's embedded-carrier.bin")
        if "carrierCheck" not in embedded:
            errors.append("the carrier-bearing engine build does not consume a verified carrier")
    if code.count('environment("VELES_EMBEDDED_CARRIER"') != 1:
        errors.append("exactly one engine build may compile a carrier in")
    if 'it.dir("slots/$channel/$slot")' not in code:
        errors.append("the assembled carrier is not scoped to the registered channel")
    bootstrap = object_block(code, '"${taskName}Engine$suffix"')
    if bootstrap is not None and 'environment("VELES_EMBEDDED_CARRIER"' in bootstrap:
        errors.append("the bootstrap engine build must embed no carrier")
    elif bootstrap is None or 'environment.remove("VELES_EMBEDDED_CARRIER")' not in bootstrap:
        errors.append("the bootstrap engine build does not clear an ambient carrier")
    assembled_final = object_block(code, '"${taskName}AssembleFinal$suffix"')
    if assembled_final is None:
        errors.append("the published tree is not re-assembled from the carrier-bearing engine")
    if "slotOutputs[slot] = assembledFinalDir" not in code:
        errors.append("finalize does not publish the carrier-bearing assembly")
    check = object_block(code, '"${taskName}CarrierCheck$suffix"')
    if check is None or '"verify-carrier"' not in check:
        errors.append("the assembled carrier is not verified against the descriptor and channel before the build")
    elif '"--channel", channel' not in check or '"--descriptor"' not in check:
        errors.append("the carrier check does not bind the exact descriptor and channel")
    verify = object_block(code, '"${taskName}VerifyCarrier$suffix"')
    if verify is None or '"--image"' not in verify or '"verify-carrier"' not in verify:
        errors.append("the built engine image is not checked to contain the assembler's carrier bytes")

    # 6. Channel is hard-selected by the registration, never by the config or a property override.
    unsigned = re.search(r'registerInstallerProducer\(\s*"bridgeInstallerUnsigned"([^)]*)\)', code)
    fixture = re.search(r'registerInstallerProducer\(\s*"bridgeInstallerPackageFixture"([^)]*)\)', code)
    if not unsigned or '"production"' not in unsigned.group(1) or "installerReleaseConfig" not in unsigned.group(1):
        errors.append("bridgeInstallerUnsigned is not registered as the production channel with the approved release config")
    elif "fixture" in unsigned.group(1).lower().replace("installerreleaseconfig", ""):
        errors.append("bridgeInstallerUnsigned references a fixture input")
    if not fixture or '"fixture"' not in fixture.group(1) or "installerFixtureConfig" not in fixture.group(1):
        errors.append("bridgeInstallerPackageFixture is not registered as the fixture channel with the tracked fixture config")
    elif "installerReleaseConfig" in fixture.group(1) or '"production"' in fixture.group(1):
        errors.append("bridgeInstallerPackageFixture references a production input")
    if code.count("registerInstallerProducer(") != 3:  # the definition and the two registrations
        errors.append("registerInstallerProducer must be defined once and called exactly twice")
    fixture_config = re.search(r"val installerFixtureConfig\s*=\s*([^\n]+)", code)
    release_config = re.search(r"val installerReleaseConfig\s*=\s*([^\n]*(?:\n\s+\.[^\n]+)*)", code)
    if not fixture_config or "gradleProperty" in fixture_config.group(1) or "fixture-" not in fixture_config.group(1):
        errors.append("the fixture config must be the tracked fixture-<platform>.json with no property override")
    if not release_config or "release-" not in release_config.group(1) or "fixture" in release_config.group(1):
        errors.append("the production config must be the approved release-<platform>.json, never a fixture file")
    check_config = [call for call in command_lines(code) if "check-config" in call]
    if not check_config or any('"--channel", channel' not in call.replace("\n", " ").replace("  ", " ") or "--require-tracked" not in call for call in check_config):
        errors.append("the producer does not check its config with --channel <registered channel> --require-tracked")
    if 'dependsOn' in code and 'check-config' not in code:
        errors.append("the config check is missing")

    # 7. Native-only, unsigned, and not wired to signing or publication.
    if "Windows or macOS runner" not in code:
        errors.append("the producers do not refuse a Linux host")
    for forbidden in ("signtool", "codesign", "notarytool", "stapler", "KEYSTORE", "secrets", "gh release", "release.yml",
                      "native-release", "VELES_WIN_", "VELES_MAC_", "VELES_NOTARY_"):
        if forbidden in code:
            errors.append("the producer graph references signing/publication: %s" % forbidden)
    if re.search(r'"gh"\s*,\s*"(?:release|workflow|run)"', code):
        errors.append("the producer graph references signing/publication: gh")
    if "bridgeInstallerFormat" not in gradle.split('tasks.named("check")', 1)[-1].split(")", 1)[0]:
        errors.append("`check` does not depend on the installer format/lint/test tasks")
    supply = re.search(r'bridgeInstallerSupplyChain[^\n]*\{(.*?)\n\}', code, re.S)
    if not supply or "verify-supply-chain.sh" not in supply.group(1):
        errors.append("bridgeInstallerSupplyChain does not run verify/verify-supply-chain.sh")

    # 7b. Build-tool identity (F8): observed, checked against the lock before a production build, bound into
    # provenance, and the macOS SDK / deployment target selected explicitly from the lock.
    observe = [call for call in command_lines(code) if "tool_observation" in call or "installerToolsScript" in call]
    if not any('"observe"' in call and '"--target"' in call and '"--output"' in call for call in observe):
        errors.append("no task observes the native tool identities (tool_observation.py observe)")
    if not any('"check"' in call and '"--observed"' in call and '"--target"' in call for call in observe):
        errors.append("no task checks the observed tools against tools.lock.json (tool_observation.py check)")
    if not re.search(r'dependsOn\(\s*if \(channel == "production"\) bridgeInstallerToolCheck else bridgeInstallerToolObservation\s*\)', code):
        errors.append("the production producer does not depend on the tool check (the fixture only on the observation)")
    finalize = [call for call in command_lines(code) if '"finalize"' in call]
    if not finalize or any('"--tool-observation"' not in call for call in finalize):
        errors.append("finalize does not bind the tool observation into provenance")
    if "MACOSX_DEPLOYMENT_TARGET" not in code or "SDKROOT" not in code or '"--sdk"' not in code:
        errors.append("the macOS SDK and deployment target are not selected explicitly")
    if re.search(r'MACOSX_DEPLOYMENT_TARGET",\s*"', code):
        errors.append("the macOS deployment target is hard-coded instead of read from tools.lock.json")

    # 7c. Channel identity (F2): a non-production engine is COMPILED for its channel; production never is.
    channel_block = re.search(r'if \(channel != "production"\) \{(.*?)\n\s*\}', code, re.S)
    if not channel_block or 'veles_channel=' not in channel_block.group(1) or 'environment("RUSTFLAGS"' not in channel_block.group(1):
        errors.append("a non-production engine is not built with --cfg veles_channel=<channel> in RUSTFLAGS")
    if code.count("veles_channel") != 1:
        errors.append("veles_channel must be set exactly once, for the non-production engine build only")
    if re.search(r'(?i)veles_channel=\\"production|veles_channel.*production', code.replace('if (channel != "production")', "")):
        errors.append("production must never carry a veles_channel cfg")

    # 8. Clean contract.
    clean = gradle.split('tasks.named<Delete>("clean")', 1)
    for path in ("native-bridge/installer/target", "native-bridge/installer/macos/.build"):
        if len(clean) != 2 or path not in clean[1]:
            errors.append("root clean does not delete %s" % path)
        if path not in clean_script:
            errors.append("assert-clean.sh does not assert %s" % path)
    if "build/native-bridge" not in clean_script:
        errors.append("assert-clean.sh no longer asserts build/native-bridge")
    return errors


# --------------------------------------------------------------------------- producer workflows

PRODUCERS = {
    "windows": {
        "file": ".github/workflows/build-native-windows.yml",
        "runner": "windows-2025",
        "artifact": "native-unsigned-windows",
        "fixture": "native-fixture-windows",
        "gradle": r".\gradlew.bat",
    },
    "macos": {
        "file": ".github/workflows/build-native-macos.yml",
        "runner": "macos-26",
        "artifact": "native-unsigned-macos",
        "fixture": "native-fixture-macos",
        "gradle": "./gradlew",
    },
}


def upload_blocks(source):
    blocks = []
    for match in re.finditer(r"- uses: actions/upload-artifact@[^\n]*\n(?:(?:        [^\n]*|)\n)*?(?=      - |\Z)", source):
        blocks.append(match.group(0))
    return blocks


def check_producer_workflow(source, platform):
    spec = PRODUCERS[platform]
    errors = []
    body = strip_yaml_comments(source)
    if "workflow_call:" not in body:
        errors.append("%s is not reusable" % spec["file"])
    if not re.search(r"commit-sha:\s*\n\s*required:\s*true", body):
        errors.append("commit-sha input is not required")
    if not re.search(r"artifact-name:\s*\n\s*required:\s*true\s*\n\s*type:\s*string\s*\n\s*default:\s*%s\b" % spec["artifact"], body):
        errors.append("artifact-name must default to the neutral %s" % spec["artifact"])
    if "-latest" in body or not re.search(r"runs-on:\s*%s\b" % re.escape(spec["runner"]), body) or body.count("runs-on:") != 1:
        errors.append("exactly one job on %s is required" % spec["runner"])
    # No Tauri scaffold production path.
    if re.search(r"(?i)tauri|\bnpm\b|setup-node|node-version|bridgePackage|bridgeBundle|bridgeBuild\b|bridgeManifests|"
                 r"provision-windows-tools|NSIS_PATH|veles-native-bridge|verify/native/", body):
        errors.append("the producer still uses the Tauri scaffold packaging path")
    # Channel-specific Gradle tasks, each called once for its selected input tree.
    for task in ("bridgeInstallerUnsigned", "bridgeInstallerPackageFixture"):
        if body.count(task) != 1:
            errors.append("%s must be invoked exactly once (found %d)" % (task, body.count(task)))
    for scaffold in ("bridgePackage", "bridgeHostBuild"):
        if scaffold in body:
            errors.append("the producer invokes %s" % scaffold)
    if "--offline" in body:
        errors.append("the producer must not run offline (no wrapper)")
    unsigned_if = re.search(r"if:[^\n]*\n[^\n]*bridgeInstallerUnsigned", body)
    fixture_if = re.search(r"if:[^\n]*\n[^\n]*bridgeInstallerPackageFixture", body)
    if not unsigned_if or "== 'true'" not in unsigned_if.group(0) or "!=" in unsigned_if.group(0):
        errors.append("bridgeInstallerUnsigned must run only when an approved release config exists")
    if not fixture_if or "!= 'true'" not in fixture_if.group(0):
        errors.append("bridgeInstallerPackageFixture must run only when no approved release config exists")
    if not re.search(r"release-%s\.json" % platform, body):
        errors.append("the approved-config probe does not name release-%s.json" % platform)
    # Pinned toolchains and runtimes.
    for line in body.splitlines():
        stripped = line.strip().lstrip("- ")
        if stripped.startswith("uses:") and not SHA_PINNED_USE.search(stripped):
            errors.append("unpinned action: %s" % stripped)
    if platform == "macos":
        if not re.search(r"^    env:\n      DEVELOPER_DIR: /Applications/Xcode_26\.6\.app$", body, re.M):
            errors.append("macOS job must select the pinned Xcode at job level")
        for pin in ("17F113", "macosx26.5"):
            if pin not in body:
                errors.append("macOS job does not enforce %s" % pin)
    else:
        if "provision-nsis.ps1" not in body or "build/native-bridge/installer/tools/nsis" not in body:
            errors.append("Windows job does not provision the pinned NSIS under build/native-bridge/installer/tools/nsis")
        if "provision-nsis.ps1" in body and "bridgeInstaller" in body and body.index("provision-nsis.ps1") > body.index("bridgeInstaller"):
            errors.append("NSIS must be provisioned before the Gradle producer runs")
    for directory in ("native-bridge/installer", "native-bridge/host"):
        if not re.search(r"working-directory:\s*%s\s*\n\s*run:\s*rustup show active-toolchain" % re.escape(directory), body):
            errors.append("the pinned Rust toolchain of %s is not activated" % directory)
    # Producers hold no credential and take no artifact from another run.
    if re.search(r"secrets[.:]|environment:|\$\{\{\s*secrets", body):
        errors.append("a producer must not accept secrets or enter a protected environment")
    if "actions/download-artifact" in body or re.search(r"\bgh run\b|run-id", body):
        errors.append("a producer must not source any artifact from another run (no PR artifact substitution)")
    # The carrier exists only after the producer's own assembler has run, so the Gradle graph is the
    # only coherent supply point. The workflow must not point the engine at a file it did not assemble.
    if "VELES_EMBEDDED_CARRIER" in body or "VELES_BUILD_CHANNEL" in body:
        errors.append("the producer workflow must not set the carrier channel itself; the Gradle producer supplies the assembled carrier")
    if not re.search(r"permissions:\n  contents: read\n", body) or body.count("permissions:") != 1:
        errors.append("permissions must be a single workflow-level contents: read")
    # Uploads: mode- and hash-preserving, explicit, named, bounded.
    uploads = upload_blocks(source)
    if len(uploads) != 2:
        errors.append("expected exactly two uploads (approved production tree, fixture tree), found %d" % len(uploads))
    else:
        production = next((b for b in uploads if "build/native-bridge/installer/out/production/" in b), None)
        fixture = next((b for b in uploads if "build/native-bridge/installer/out/fixture/" in b), None)
        if production is None or fixture is None:
            errors.append("uploads must be the production and the fixture output directories")
        else:
            if "name: ${{ inputs.artifact-name }}" not in production:
                errors.append("the production upload must use the caller-selected artifact name")
            if "name: %s" % spec["fixture"] not in fixture:
                errors.append("the fixture upload must be named %s" % spec["fixture"])
            for block, channel in ((production, "production"), (fixture, "fixture")):
                for name in ("tree.tar", "provenance.json", "report-ab.json", "SHA256SUMS"):
                    if "build/native-bridge/installer/out/%s/%s" % (channel, name) not in block:
                        errors.append("the %s upload omits %s (hashes and modes travel inside tree.tar)" % (channel, name))
                if "if-no-files-found: error" not in block or not re.search(r"retention-days:\s*\d+", block):
                    errors.append("every upload must fail on missing files and be retention-bounded")
                if re.search(r"path:[^\n]*\*|veles-native-host(?:\.exe)?\s*$", block, re.M):
                    errors.append("uploads must name exact files, not globs or bare executables (exec bits are dropped)")
            if "if:" not in production or "== 'true'" not in production.split("with:")[0]:
                errors.append("the production upload must be conditional on the approved config")
            if "!= 'true'" not in fixture.split("with:")[0]:
                errors.append("the fixture upload must be conditional on the absence of an approved config")
            if spec["fixture"] == spec["artifact"] or "native-unsigned" in fixture:
                errors.append("a fixture artifact must never carry the production artifact name")
    return errors


# --------------------------------------------------------------------------- callers


def job_block(source, job):
    match = re.search(r"\n  %s:\n(.*?)(?=\n  [\w-]+:\n|\Z)" % re.escape(job), source, re.S)
    return match.group(0) if match else ""


def check_release_build(source):
    errors = []
    for job, artifact in (("native-windows", "native-unsigned-windows"), ("native-macos", "native-unsigned-macos")):
        block = job_block(source, job)
        if "artifact-name: %s\n" % artifact not in block + "\n":
            errors.append("release-build %s must pass artifact-name %s" % (job, artifact))
        if "uses: ./.github/workflows/build-%s.yml" % job not in block:
            errors.append("release-build %s must reuse its producer workflow" % job)
    # The Android-only publication conditions are untouched.
    publish = source.split("\n  publish:", 1)[-1]
    if PUBLISH_NEEDS not in publish:
        errors.append("the publish job's needs changed")
    if PUBLISH_IF not in publish:
        errors.append("the publish job's if condition changed")
    if ANDROID_IF not in job_block(source, "android"):
        errors.append("the android job's condition changed")
    if source.count("contents: write") != 1 or "contents: write" not in publish:
        errors.append("contents: write must appear only in the publish job")
    if "native-release" in source or "native-unsigned" in publish or "gh release create \"$VERSION\" \\\n            \"$APK\" \\\n            \"android-release/mapping.txt\" \\\n            \"android-release/SHA256SUMS\" \\" not in publish:
        errors.append("the publish job must keep publishing only the Android artifacts")
    return errors


def check_gate_boundary(workflows):
    """Signed candidates come only from trusted producer runs; nothing else touches signing."""
    errors = []
    gate = workflows["release-native-installers.yml"]
    if '.workflowName == "release-build"' not in gate or '(.event == "push" or .event == "workflow_dispatch")' not in gate:
        errors.append("the gate no longer restricts producer runs to release-build push/workflow_dispatch (PR artifacts would qualify)")
    if '.headSha == $sha' not in gate or '.conclusion == "success"' not in gate:
        errors.append("the gate no longer binds producer runs to the commit and a successful conclusion")
    if "pull_request" in strip_yaml_comments(gate).replace('.event == "push" or .event == "workflow_dispatch"', ""):
        errors.append("the gate workflow must not be triggered by or accept pull_request context")
    for name, source in workflows.items():
        if name == "release-native-installers.yml":
            continue
        body = strip_yaml_comments(source)
        if re.search(r"VELES_WIN_|VELES_MAC_|VELES_NOTARY_|native-release|VELES_APPROVED_", body):
            errors.append("%s references signing credentials or the protected environment" % name)
    return errors


def check_host_workflow_installer(source):
    errors = []
    body = strip_yaml_comments(source)
    for task in ("bridgeInstallerFormat", "bridgeInstallerLint", "bridgeInstallerTest"):
        if body.count(task) < 3:
            errors.append("%s must run on all three platforms" % task)
    if body.count("bridgeInstallerScriptsTest") != 1:
        errors.append("bridgeInstallerScriptsTest must run exactly once (the portable job)")
    portable = body.split("  windows:", 1)[0]
    if "bridgeInstallerScriptsTest" not in portable:
        errors.append("bridgeInstallerScriptsTest must run in the portable job")
    for task in ("bridgeInstallerUnsigned", "bridgeInstallerPackageFixture"):
        if task in body:
            errors.append("the host check workflow must not build installer inputs (%s)" % task)
    if len(re.findall(r"working-directory:\s*native-bridge/installer\s*\n\s*run:\s*rustup show active-toolchain", body)) < 3:
        errors.append("the installer toolchain is not activated in each of the three host check jobs")
    return errors


# --------------------------------------------------------------------------- supply chain


def check_supply_chain(files):
    errors = []
    if "native-bridge/installer/Cargo.toml" not in files["generate"] or "native-installer.cdx.json" not in files["generate"]:
        errors.append("generate-sboms.sh does not emit native-installer.cdx.json from the installer manifest")
    verify = files["verify-sboms"]
    if "'native-installer': 'veles-bridge-installer@0.1.0'" not in verify or "native-bridge', 'installer', 'Cargo.lock'" not in verify:
        errors.append("verify-sboms.mjs does not require the native-installer root and its lockfile")
    if "verifyInstallerToolchainCoverage" not in verify:
        errors.append("verify-sboms.mjs does not cover the installer's Swift and NSIS toolchain")
    if "native-bridge/installer/Cargo.toml" not in files["build-scripts"]:
        errors.append("the cargo build-script scan omits the installer manifest")
    if "native-bridge/installer/Cargo.toml" not in files["licenses"]:
        errors.append("the cargo-deny licence check omits the installer manifest")
    for scope in ("native-bridge/installer/src", "native-bridge/installer/scripts", "native-bridge/installer/windows",
                  "native-bridge/installer/macos/Package.swift", "native-bridge/installer/macos/Sources",
                  ".github/workflows/build-native-windows.yml", ".github/workflows/build-native-macos.yml",
                  ".github/workflows/release-native-installers.yml"):
        if scope not in files["remote-code"]:
            errors.append("the remote-code scan omits %s" % scope)
    if "build/sbom/native-installer.cdx.json" not in files["supply-chain-workflow"]:
        errors.append("build-supply-chain.yml does not publish native-installer.cdx.json")
    return errors


def check_schema(schema_text):
    """The Rust validator is authoritative; the schema must not claim a second, divergent rule."""
    errors = []
    schema = json.loads(schema_text)
    publisher = schema["$defs"]["publisher"]
    if "pattern" in publisher or "maxLength" in publisher:
        errors.append("the publisher schema carries a regex/length rule the Rust validator does not share")
    if "authoritative" not in publisher.get("description", "").lower() or "rust" not in publisher.get("description", "").lower():
        errors.append("the publisher schema does not state that the Rust validator is authoritative")
    version = schema["properties"]["version"]
    if "authoritative" not in version.get("description", "").lower():
        errors.append("the version schema does not state that the Rust validator is authoritative")
    if version.get("pattern") != r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$":
        errors.append("the version pattern drifted from the Rust grammar")
    return errors


# --------------------------------------------------------------------------- tests


class GradleGraph(unittest.TestCase):
    def setUp(self):
        self.gradle = read("build.gradle.kts")
        self.clean = read("rust/scripts/assert-clean.sh")

    def violations(self, gradle=None, clean=None):
        return check_gradle(gradle or self.gradle, clean or self.clean)

    def mutate(self, old, new, count=1):
        block = producer_block(self.gradle)
        self.assertIsNotNone(block)
        self.assertTrue(old in block, "mutation anchor missing: " + old[:70])
        return self.gradle.replace(block, block.replace(old, new, count))

    def test_repository_graph_satisfies_the_contract(self):
        self.assertEqual(self.violations(), [])

    def test_a_scaffold_or_node_dependency_is_rejected(self):
        for injected in ('dependsOn("bridgePackage")', 'commandLine("npm", "ci")', 'inputs.file("native-bridge/src-tauri/Cargo.toml")',
                         'dependsOn(bridgeBuild)', 'inputs.file("build/native-bridge/windows/veles-native-bridge.exe")'):
            block = producer_block(self.gradle)
            mutated = self.gradle.replace(block, block + "\n" + injected + "\n")
            self.assertNotEqual(self.violations(mutated), [], injected)

    def test_an_unlocked_cargo_build_is_rejected(self):
        found = self.violations(self.mutate('"cargo", "build", "--locked", ', '"cargo", "build", ', 1))
        self.assertTrue(any("--locked" in e for e in found), found)

    def test_a_non_production_binary_is_rejected(self):
        found = self.violations(self.mutate('"veles-native-host"', '"veles-host-test-driver"', 1))
        self.assertTrue(any("outside the production set" in e or "test-driver" in e for e in found), found)

    def test_all_features_and_simulator_flags_are_rejected(self):
        for flag in ('"--all-features"', '"--features", "test-driver"', '"--workspace"', '"--bins"'):
            found = self.violations(self.mutate('"--release", ', '"--release", %s, ' % flag, 1))
            self.assertTrue(any("must not use" in e or "test-driver" in e for e in found), (flag, found))

    def test_a_missing_target_triple_is_rejected(self):
        block = producer_block(self.gradle)
        mutated = self.gradle.replace(block, re.sub(r'("--release",\s*)"--target",\s*[^,\n]+,\s*', r"\1", block, count=1))
        self.assertTrue(any("--target" in e for e in self.violations(mutated)))

    def test_output_outside_build_is_rejected(self):
        mutated = re.sub(r"(val installerBuildRoot\s*=\s*)[^\n]+", r'\1layout.projectDirectory.dir("native-bridge/installer/target")', self.gradle)
        self.assertTrue(any("installerBuildRoot" in e for e in self.violations(mutated)))
        block = producer_block(self.gradle)
        mutated = self.gradle.replace(block, block + '\nenvironment("CARGO_TARGET_DIR", file("native-bridge/installer/target"))\n')
        self.assertTrue(any("CARGO_TARGET_DIR" in e for e in self.violations(mutated)))

    def test_a_single_slot_is_not_two_builds(self):
        found = self.violations(self.mutate('listOf("a", "b")', 'listOf("a")'))
        self.assertTrue(any("A/B slots" in e for e in found), found)

    def test_shared_target_directories_are_rejected(self):
        found = self.violations(self.mutate("target/$channel-$slot/installer", "target/$channel/installer", 1))
        self.assertTrue(any("distinct per channel and slot" in e for e in found), found)

    def test_a_build_that_keeps_a_stale_target_is_rejected(self):
        block = producer_block(self.gradle)
        mutated = self.gradle.replace(block, re.sub(r"delete\([^\n]*\)\n", "\n", block))
        self.assertTrue(any("stale cache" in e for e in self.violations(mutated)))

    def test_missing_path_remapping_is_rejected(self):
        found = self.violations(self.mutate("--remap-path-prefix", "--cfg-noop", 1))
        self.assertTrue(any("remap" in e for e in found), found)
        found = self.violations(self.mutate("/Brepro", "/noop"))
        self.assertTrue(any("deterministic" in e for e in found), found)
        found = self.violations(self.mutate('environment("RUSTFLAGS"', 'environment("RUSTFLAGZ"'))
        self.assertTrue(any("RUSTFLAGS" in e for e in found), found)

    def test_the_origin_must_be_a_tracked_build_input(self):
        found = self.violations(self.mutate('inputs.property("VELES_ALLOWED_ORIGINS"', 'inputs.property("OTHER"'))
        self.assertTrue(any("VELES_ALLOWED_ORIGINS" in e for e in found), found)
        found = self.violations(self.mutate('environment("VELES_ALLOWED_ORIGINS"', 'environment("OTHER"'))
        self.assertTrue(any("VELES_ALLOWED_ORIGINS" in e for e in found), found)

    def test_the_engine_must_compile_in_the_assembled_and_verified_carrier(self):
        for old, new in (
            ('environment("VELES_EMBEDDED_CARRIER"', 'environment("VELES_CARRIER_IGNORED"'),
            ('environment("VELES_BUILD_CHANNEL", channel)', 'environment("VELES_CHANNEL_IGNORED", channel)'),
            ('val carrierDocument = assembledDir.map { it.file("embedded-carrier.bin") }',
             'val carrierDocument = providers.provider { null }'),
            ('"${taskName}EngineEmbedded$suffix"', '"${taskName}Engine$suffix"'),
            ('slotOutputs[slot] = assembledFinalDir', 'slotOutputs[slot] = assembledDir'),
        ):
            found = self.violations(self.mutate(old, new, 1))
            self.assertTrue(any("carrier" in e or "assembly" in e for e in found), (old, found))

    def test_the_carrier_check_must_bind_the_channel_and_the_image(self):
        anchor = ('                    "--descriptor", descriptor.asFile.absolutePath,\n'
                  '                    "--channel", channel,')
        found = self.violations(self.mutate(anchor, anchor.replace('"--channel", channel', '"--channel", "production"'), 1))
        self.assertTrue(any("carrier" in e for e in found), found)
        found = self.violations(self.mutate('"--image", image.absolutePath', '"--no-image", image.absolutePath', 1))
        self.assertTrue(any("carrier" in e for e in found), found)

    def test_the_carrier_must_be_scoped_to_the_registered_channel(self):
        found = self.violations(self.mutate('it.dir("slots/$channel/$slot")', 'it.dir("slots/production/$slot")', 1))
        self.assertTrue(any("carrier" in e for e in found), found)

    def test_a_fixture_config_cannot_feed_the_production_task(self):
        found = self.violations(self.mutate('"bridgeInstallerUnsigned", "production", installerReleaseConfig', '"bridgeInstallerUnsigned", "production", installerFixtureConfig'))
        self.assertTrue(any("bridgeInstallerUnsigned" in e for e in found), found)

    def test_the_fixture_task_cannot_become_production(self):
        found = self.violations(self.mutate('"bridgeInstallerPackageFixture", "fixture"', '"bridgeInstallerPackageFixture", "production"'))
        self.assertTrue(any("bridgeInstallerPackageFixture" in e for e in found), found)
        found = self.violations(self.mutate("installerFixtureConfig,", "installerReleaseConfig,", 1) if "installerFixtureConfig," in producer_block(self.gradle) else self.mutate("installerFixtureConfig", "installerReleaseConfig"))
        self.assertTrue(any("fixture" in e.lower() for e in found), found)

    def test_a_property_override_of_the_fixture_config_is_rejected(self):
        block = producer_block(self.gradle)
        mutated = re.sub(r"(val installerFixtureConfig\s*=\s*)", r'\1providers.gradleProperty("installerReleaseConfig").map { layout.projectDirectory.file(it) }.orElse(', block)
        mutated = re.sub(r"(val installerFixtureConfig[^\n]*)", r"\1)", mutated, count=1)
        self.assertTrue(any("no property override" in e for e in self.violations(self.gradle.replace(block, mutated))))

    def test_the_production_config_must_be_tracked_and_checked_for_its_channel(self):
        found = self.violations(self.mutate("--require-tracked", "--noop"))
        self.assertTrue(any("require-tracked" in e for e in found), found)

    def test_the_production_graph_cannot_skip_the_tool_check(self):
        found = self.violations(self.mutate("if (channel == \"production\") bridgeInstallerToolCheck else bridgeInstallerToolObservation", "bridgeInstallerToolObservation"))
        self.assertTrue(any("depend on the tool check" in e for e in found), found)

    def test_provenance_must_carry_the_tool_observation(self):
        found = self.violations(self.mutate('"--tool-observation", installerToolObservation.get().asFile.absolutePath,', ""))
        self.assertTrue(any("bind the tool observation" in e for e in found), found)

    def test_the_tool_tasks_must_exist(self):
        found = self.violations(self.mutate('"observe", "--target"', '"noop", "--target"'))
        self.assertTrue(any("observes the native tool identities" in e for e in found), found)
        found = self.violations(self.mutate('"check", "--target"', '"noop", "--target"'))
        self.assertTrue(any("checks the observed tools" in e for e in found), found)

    def test_the_sdk_and_deployment_target_are_explicit_and_from_the_lock(self):
        found = self.violations(self.mutate("MACOSX_DEPLOYMENT_TARGET", "OTHER_TARGET"))
        self.assertTrue(any("deployment target" in e for e in found), found)
        found = self.violations(self.mutate('environment("MACOSX_DEPLOYMENT_TARGET", installerMinimumOs.get())', 'environment("MACOSX_DEPLOYMENT_TARGET", "26.0")'))
        self.assertTrue(any("hard-coded" in e for e in found), found)

    def test_a_fixture_engine_must_be_compiled_for_its_channel(self):
        found = self.violations(self.mutate(' --cfg veles_channel=\\"$channel\\"', ""))
        self.assertTrue(any("veles_channel" in e for e in found), found)
        found = self.violations(self.mutate('if (channel != "production") {', 'if (true) {'))
        self.assertTrue(any("veles_channel" in e or "production" in e for e in found), found)
        found = self.violations(self.mutate('if (channel != "production") {', 'if (channel == "production") {'))
        self.assertTrue(any("production" in e for e in found), found)

    def test_a_linux_host_must_be_refused(self):
        found = self.violations(self.mutate("Windows or macOS runner", "any runner"))
        self.assertTrue(any("Linux" in e for e in found), found)

    def test_signing_and_publication_are_not_reachable_from_the_graph(self):
        for injected in ('commandLine("signtool", "sign")', 'environment("VELES_WIN_PFX_BASE64", "x")', 'commandLine("gh", "release", "create")',
                         'val x = "native-release"'):
            block = producer_block(self.gradle)
            mutated = self.gradle.replace(block, block + "\n" + injected + "\n")
            self.assertTrue(any("signing/publication" in e for e in self.violations(mutated)), injected)

    def test_the_supply_chain_task_runs_the_policy_not_just_an_sbom(self):
        found = self.violations(self.mutate("verify-supply-chain.sh", "generate-sboms.sh"))
        self.assertTrue(any("bridgeInstallerSupplyChain" in e for e in found), found)

    def test_clean_contract_covers_source_tree_leftovers(self):
        for path in ("native-bridge/installer/target", "native-bridge/installer/macos/.build"):
            self.assertTrue(any(path in e for e in self.violations(clean=self.clean.replace(path, ""))), path)
            clean_tail = self.gradle.split('tasks.named<Delete>("clean")', 1)
            mutated = clean_tail[0] + 'tasks.named<Delete>("clean")' + clean_tail[1].replace(path, "")
            self.assertTrue(any(path in e for e in self.violations(mutated)), path)


class CompiledChannel(unittest.TestCase):
    """F2, executed: the engine is really built per channel, with the exact RUSTFLAGS the producer passes."""

    def run_channel_tests(self, label, rustflags):
        import os
        import shutil
        import subprocess

        if shutil.which("cargo") is None:
            self.skipTest("cargo is not on PATH; the compiled-channel build cannot run here")
        target = ROOT / "build" / "native-bridge" / "installer" / "channel-test" / label
        target.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, CARGO_TARGET_DIR=str(target))
        env.pop("CARGO_ENCODED_RUSTFLAGS", None)
        env.pop("RUSTFLAGS", None)
        if rustflags:
            env["RUSTFLAGS"] = rustflags
        done = subprocess.run(["cargo", "test", "--locked", "--test", "channel"], cwd=INSTALLER, env=env,
                              capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stdout[-1500:] + done.stderr[-1500:])
        return done.stdout

    def test_the_default_build_is_the_production_channel(self):
        out = self.run_channel_tests("production", None)
        self.assertIn("compiled_channel_is_production_by_default ... ok", out)
        self.assertNotIn("compiled_channel_is_fixture", out)

    def test_the_fixture_producer_flags_build_the_fixture_channel(self):
        out = self.run_channel_tests("fixture", '--cfg veles_channel="fixture"')
        self.assertIn("compiled_channel_is_fixture_when_built_for_it ... ok", out)
        self.assertNotIn("compiled_channel_is_production", out)
        self.assertIn("a_fixture_channel_engine_cannot_address_a_production_installation ... ok", out)

    def test_two_channels_in_one_build_do_not_compile(self):
        import os
        import shutil
        import subprocess

        if shutil.which("cargo") is None:
            self.skipTest("cargo is not on PATH")
        target = ROOT / "build" / "native-bridge" / "installer" / "channel-test" / "both"
        target.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, CARGO_TARGET_DIR=str(target), RUSTFLAGS='--cfg veles_channel="fixture" --cfg veles_channel="development"')
        env.pop("CARGO_ENCODED_RUSTFLAGS", None)
        done = subprocess.run(["cargo", "build", "--locked", "--lib"], cwd=INSTALLER, env=env, capture_output=True, text=True, check=False)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("at most one veles_channel", done.stderr)


class ProducerWorkflows(unittest.TestCase):
    def source(self, platform):
        return read(PRODUCERS[platform]["file"])

    def test_each_producer_satisfies_the_contract(self):
        for platform in PRODUCERS:
            self.assertEqual(check_producer_workflow(self.source(platform), platform), [], platform)

    def mutations(self, platform):
        source = self.source(platform)
        spec = PRODUCERS[platform]
        return {
            "scaffold-package": source + "\n      - run: %s bridgePackage\n" % spec["gradle"],
            "tauri": source + "\n      - run: npm ci --prefix native-bridge\n",
            "second-unsigned": source + "\n      - run: %s bridgeInstallerUnsigned\n" % spec["gradle"],
            "second-fixture": source + "\n      - run: %s bridgeInstallerPackageFixture\n" % spec["gradle"],
            "unconditional-unsigned": re.sub(r"(if:[^\n]*== 'true'[^\n]*\n)(\s*run:[^\n]*bridgeInstallerUnsigned)", r"\2", source, count=1),
            "secret": source.replace("permissions:\n  contents: read", "permissions:\n  contents: read\nsecrets:\n  x: ${{ secrets.X }}", 1),
            "environment": source.replace("    steps:", "    environment: native-release\n    steps:", 1),
            "download": source.replace("    steps:", "    steps:\n      - uses: actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131 # v7\n        with:\n          name: x\n          run-id: 1\n", 1),
            "unpinned": source.replace("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "actions/checkout@v4", 1),
            "latest-runner": source.replace(spec["runner"], "%s-latest" % spec["runner"].split("-")[0], 1),
            "bare-glob-upload": source.replace("build/native-bridge/installer/out/production/tree.tar", "build/native-bridge/installer/out/production/*", 1),
            "dropped-provenance": source.replace("build/native-bridge/installer/out/production/provenance.json", "", 1),
            "dropped-checksums": source.replace("build/native-bridge/installer/out/fixture/SHA256SUMS", "", 1),
            "wrong-artifact-default": source.replace("default: %s" % spec["artifact"], "default: native-%s" % platform, 1),
            "fixture-as-production": source.replace("name: %s" % spec["fixture"], "name: %s" % spec["artifact"], 1),
            "unactivated-toolchain": source.replace("working-directory: native-bridge/installer\n        run: rustup show active-toolchain", "run: true", 1),
            "carrier-env": source.replace(
                "      - name: Build the production-channel unsigned input tree",
                "      - name: Build the production-channel unsigned input tree\n        env:\n          VELES_EMBEDDED_CARRIER: unverified.bin",
                1,
            ),
            "silent-upload": source.replace("if-no-files-found: error", "if-no-files-found: ignore"),
            "write-permission": source.replace("contents: read", "contents: write", 1),
        }

    def test_every_mutation_is_named_by_a_violation(self):
        for platform in PRODUCERS:
            for label, mutated in self.mutations(platform).items():
                if mutated == self.source(platform):
                    self.fail("%s/%s did not change the workflow" % (platform, label))
                self.assertNotEqual(check_producer_workflow(mutated, platform), [], "%s/%s" % (platform, label))

    def test_macos_must_pin_xcode_and_windows_must_provision_nsis(self):
        source = self.source("macos")
        self.assertNotEqual(check_producer_workflow(source.replace("17F113", "17F000"), "macos"), [])
        self.assertNotEqual(check_producer_workflow(source.replace("DEVELOPER_DIR: /Applications/Xcode_26.6.app", "DEVELOPER_DIR: /Applications/Xcode.app"), "macos"), [])
        windows = self.source("windows")
        self.assertNotEqual(check_producer_workflow(windows.replace("provision-nsis.ps1", "other.ps1"), "windows"), [])
        self.assertNotEqual(check_producer_workflow(windows.replace("build/native-bridge/installer/tools/nsis", "tools/nsis"), "windows"), [])


class Callers(unittest.TestCase):
    def test_release_build_passes_the_unsigned_names_and_keeps_android_publication(self):
        self.assertEqual(check_release_build(read(".github/workflows/release-build.yml")), [])

    def test_changed_publication_conditions_are_rejected(self):
        source = read(".github/workflows/release-build.yml")
        for old, new in (
            (PUBLISH_NEEDS, PUBLISH_NEEDS.replace("native-macos]", "native-macos, native-unsigned]")),
            ("needs.native-macos.result == 'success'", "needs.native-macos.result != 'failure'"),
            ("github.ref == 'refs/heads/master' &&", "github.ref != '' &&"),
            ('"android-release/SHA256SUMS" \\', '"android-release/SHA256SUMS" "native-unsigned.tar" \\'),
        ):
            self.assertIn(old, source, old)
            self.assertNotEqual(check_release_build(source.replace(old, new, 1)), [], old)
        self.assertNotEqual(check_release_build(source.replace("contents: read", "contents: write", 1)), [])

    def test_native_callers_must_pass_the_new_artifact_names(self):
        source = read(".github/workflows/release-build.yml")
        self.assertNotEqual(check_release_build(source.replace("artifact-name: native-unsigned-windows", "artifact-name: native-windows")), [])
        self.assertNotEqual(check_release_build(source.replace("artifact-name: native-unsigned-macos", "artifact-name: native-macos")), [])

    def workflows(self):
        directory = ROOT / ".github" / "workflows"
        return {path.name: path.read_text(encoding="utf-8") for path in directory.glob("*.yml")}

    def test_signed_candidates_never_come_from_pull_request_artifacts(self):
        self.assertEqual(check_gate_boundary(self.workflows()), [])

    def test_gate_boundary_mutations_are_rejected(self):
        workflows = self.workflows()
        gate = workflows["release-native-installers.yml"]
        for old, new in (
            ('(.event == "push" or .event == "workflow_dispatch")', '(.event == "push" or .event == "workflow_dispatch" or .event == "pull_request")'),
            ('.workflowName == "release-build"', '.workflowName != ""'),
            ('.headSha == $sha', 'true'),
        ):
            self.assertIn(old, gate, old)
            self.assertNotEqual(check_gate_boundary({**workflows, "release-native-installers.yml": gate.replace(old, new, 1)}), [], old)
        leaked = {**workflows, "build-native-windows.yml": workflows["build-native-windows.yml"] + "\n      env:\n        VELES_WIN_PFX_BASE64: x\n"}
        self.assertNotEqual(check_gate_boundary(leaked), [])

    def test_host_check_workflow_runs_the_installer_checks_on_every_platform(self):
        source = read(".github/workflows/build-native-host.yml")
        self.assertEqual(check_host_workflow_installer(source), [])
        for mutated in (
            source.replace("bridgeInstallerLint", "bridgeInstallerLynt"),
            source.replace("bridgeInstallerScriptsTest", "bridgeInstallerTest", 1),
            source + "\n      - run: ./gradlew bridgeInstallerUnsigned\n",
            source.replace("working-directory: native-bridge/installer", "working-directory: native-bridge/host", 1),
        ):
            self.assertNotEqual(check_host_workflow_installer(mutated), [])


class SupplyChain(unittest.TestCase):
    FILES = {
        "generate": "verify/generate-sboms.sh",
        "verify-sboms": "verify/verify-sboms.mjs",
        "licenses": "verify/check-npm-licenses.mjs",
        "build-scripts": "verify/check-cargo-build-scripts.mjs",
        "remote-code": "verify/check-remote-code.mjs",
        "supply-chain-workflow": ".github/workflows/build-supply-chain.yml",
    }

    def files(self):
        return {key: read(path) for key, path in self.FILES.items()}

    def test_installer_components_are_covered_by_every_consumer(self):
        self.assertEqual(check_supply_chain(self.files()), [])

    def test_dropping_a_consumer_is_rejected(self):
        for key, needle in (
            ("generate", "native-installer.cdx.json"),
            ("verify-sboms", "'native-installer': 'veles-bridge-installer@0.1.0'"),
            ("verify-sboms", "verifyInstallerToolchainCoverage"),
            ("build-scripts", "native-bridge/installer/Cargo.toml"),
            ("licenses", "native-bridge/installer/Cargo.toml"),
            ("remote-code", "native-bridge/installer/scripts"),
            ("remote-code", ".github/workflows/release-native-installers.yml"),
            ("supply-chain-workflow", "build/sbom/native-installer.cdx.json"),
        ):
            files = self.files()
            self.assertIn(needle, files[key], (key, needle))
            files[key] = files[key].replace(needle, "")
            self.assertNotEqual(check_supply_chain(files), [], (key, needle))

    def test_the_lock_pins_the_installer_toolchain_the_supply_chain_check_reads(self):
        lock = json.loads(read("native-bridge/installer/config/tools.lock.json"))
        self.assertEqual(["nsis"], list(lock["archives"]))
        self.assertRegex(lock["archives"]["nsis"]["sha256"], r"^[0-9a-f]{64}$")
        package = read("native-bridge/installer/macos/Package.swift")
        self.assertNotRegex(strip_kotlin_comments(package), r"\.package\(")


class ReleaseSchema(unittest.TestCase):
    def test_the_rust_validator_is_authoritative(self):
        self.assertEqual(check_schema(read("native-bridge/installer/config/release.schema.json")), [])

    def test_a_second_divergent_rule_is_rejected(self):
        schema = json.loads(read("native-bridge/installer/config/release.schema.json"))
        schema["$defs"]["publisher"]["pattern"] = r"^\S+$"
        self.assertNotEqual(check_schema(json.dumps(schema)), [])
        schema = json.loads(read("native-bridge/installer/config/release.schema.json"))
        schema["$defs"]["publisher"]["maxLength"] = 512
        self.assertNotEqual(check_schema(json.dumps(schema)), [])
        schema = json.loads(read("native-bridge/installer/config/release.schema.json"))
        schema["$defs"]["publisher"]["description"] = "A publisher."
        self.assertNotEqual(check_schema(json.dumps(schema)), [])


class TrackedConfigs(unittest.TestCase):
    def test_fixture_configs_are_fixture_only_and_complete(self):
        import sys
        sys.path.insert(0, str(INSTALLER / "scripts"))
        import assemble_unsigned as au

        for platform in ("windows", "macos"):
            config = au.load_config(INSTALLER / "config" / ("fixture-%s.json" % platform))
            self.assertEqual([], au.check_config(config, channel="fixture", platform=platform))
            self.assertTrue(au.check_config(config, channel="production", platform=platform))

    def test_no_unapproved_production_config_is_tracked_by_accident(self):
        """If a release-<platform>.json exists it must satisfy the production contract."""
        import sys
        sys.path.insert(0, str(INSTALLER / "scripts"))
        import assemble_unsigned as au

        for platform in ("windows", "macos"):
            path = INSTALLER / "config" / ("release-%s.json" % platform)
            if path.exists():
                self.assertEqual([], au.check_config(au.load_config(path), channel="production", platform=platform))


class WindowsHelperScripts(unittest.TestCase):
    """The PowerShell helpers are NOT RUN here; these checks pin their closed, offline shape."""

    def test_helpers_are_closed_offline_and_fail_closed(self):
        for name in ("generate-uninstaller.ps1", "embed-carrier.ps1"):
            text = read("native-bridge/installer/scripts/" + name)
            body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
            self.assertRegex(text, r"(?s)^\s*(?:<#.*?#>\s*)?\[CmdletBinding\(\)\]\s*param\(", name)
            self.assertIn("$ErrorActionPreference = 'Stop'", body, name)
            self.assertIn("Set-StrictMode -Version Latest", body, name)
            for forbidden in ("Invoke-WebRequest", "Invoke-RestMethod", "curl", "wget", "Invoke-Expression", "iex ", "Start-BitsTransfer", "http://", "https://"):
                self.assertNotIn(forbidden, body, "%s uses %s" % (name, forbidden))
            self.assertNotRegex(body, r"\|\|\s*true|-ErrorAction\s+SilentlyContinue", name)


if __name__ == "__main__":
    unittest.main()
