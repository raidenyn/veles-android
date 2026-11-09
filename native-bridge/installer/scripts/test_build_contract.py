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
    "bridgeInstallerUnsigned", "bridgeInstallerPackageFixture", "bridgeInstallerDebug", "bridgeInstallerSupplyChain",
)
SHA_PINNED_USE = re.compile(r"uses:\s*(?:\./|[\w.-]+/[\w./-]+@[0-9a-f]{40}\b)")

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


def kind_declaration(code, name):
    """The argument text of ``name = InstallerBuildKind(...)``, whitespace-normalised."""
    match = re.search(r"val %s\s*=\s*InstallerBuildKind\((.*?)\n\s*\)" % re.escape(name), code, re.S)
    return re.sub(r"\s+", " ", match.group(1)) if match else None


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

    # 2. Locked cargo, an explicit build kind for every producer (release has --release and the
    #    static CRT; debug is a plain debug profile with the one reviewed fixture-trust feature),
    #    explicit production binaries only, no simulator/test driver.
    release_kind = kind_declaration(code, "installerReleaseKind")
    debug_kind = kind_declaration(code, "installerDebugKind")
    if release_kind is None or "cargoProfileArguments = listOf(\"--release\")" not in release_kind:
        errors.append("the release build kind does not pass cargo --release")
    elif "cargoBinaryDirectory = \"release\"" not in release_kind:
        errors.append("the release build kind does not read the release binary directory")
    if release_kind is not None and "slots = listOf(\"a\", \"b\")" not in release_kind:
        errors.append("the release build kind does not define the two explicit A/B slots")
    if debug_kind is None:
        errors.append("no debug build kind is declared")
    else:
        if "cargoProfileArguments = emptyList()" not in debug_kind:
            errors.append("the debug build kind must not pass cargo --release")
        if "cargoBinaryDirectory = \"debug\"" not in debug_kind:
            errors.append("the debug build kind does not read the debug binary directory")
        if "cargoFeatures = listOf(\"--features\", \"debug-fixture-trust\")" not in debug_kind:
            errors.append("the debug build kind does not enable debug-fixture-trust")
        if "slots = listOf(\"a\")" not in debug_kind:
            errors.append("the debug build kind must build exactly one slot")
        if "swiftConfiguration = \"debug\"" not in debug_kind:
            errors.append("the debug build kind does not select the Swift debug configuration")
    if release_kind is not None and "cargoFeatures = emptyList()" not in release_kind:
        errors.append("the release build kind must not enable the debug feature")
    if release_kind is not None and "swiftConfiguration = \"release\"" not in release_kind:
        errors.append("the release build kind does not select the Swift release configuration")
    if 'mutableListOf("cargo", "build", "--locked")' not in code:
        errors.append("a cargo build does not pass --locked")
    if 'listOf("cargo", "build", "--locked")' not in code:
        errors.append("the host cargo build does not pass --locked")
    if code.count("kind.cargoProfileArguments") < 2:
        errors.append("a cargo build does not use the build kind's profile arguments")
    if "kind.cargoFeatures" not in code:
        errors.append("a cargo build does not use the build kind's feature arguments")
    for need in ('"--bin", "veles-bridge-maintenance"', '"--bin", "veles-bridge-cleanup"', '"--bin", "veles-native-host"'):
        if need not in code:
            errors.append("no cargo build produces %s" % need)
    named = set(re.findall(r'"--bin",\s*"([^"]+)"', code))
    if named - set(ALLOWED_BINS):
        errors.append("a cargo build names a binary outside the production set: %s" % sorted(named - set(ALLOWED_BINS)))
    producer_section = code[code.find("fun registerInstallerProducer"):]
    if producer_section.count('"--target", installerRustTriple') != 3:
        errors.append("a cargo build does not pin its --target triple")
    for flag in FORBIDDEN_CARGO_FLAGS:
        if flag == "--features":
            if code.count('"--features"') != 1:
                errors.append("--features may only carry the one reviewed debug feature")
            elif debug_kind is None or 'listOf("--features", "debug-fixture-trust")' not in debug_kind:
                errors.append("a cargo build must not use --features")
        elif flag == "test-driver":
            if "test-driver" in producer_section:
                errors.append("a cargo build must not use test-driver")
        elif flag in producer_section:
            errors.append("a cargo build must not use %s" % flag)
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

    # 4. Independent A/B for release, one slot for debug: explicit slots, each with its own target
    #    directories (scoped by build kind, channel and slot), wiped first.
    if not re.search(r'listOf\("a",\s*"b"\)', code):
        errors.append("the release build kind does not define the two explicit A/B slots")
    if not re.search(r'slots = listOf\("a"\)', code):
        errors.append("the debug build kind does not build exactly one slot")
    slot_dirs = [d for d in re.findall(r'it\.dir\("target/([^"]*)"\)', code) if d != "check"]
    if len(slot_dirs) < 3 or any("$channel" not in d or "$slot" not in d or "$buildName" not in d for d in slot_dirs):
        errors.append("cargo target directories are not distinct per build kind, channel and slot")
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
    if "target-feature=+crt-static" not in code:
        errors.append("the Windows build does not link the static CRT (+crt-static)")
    if not re.search(r'fun Exec\.installerCargoEnvironment\(\) \{(?:(?!\n\}).)*environment\("RUSTFLAGS"', code, re.S):
        errors.append("RUSTFLAGS is not set explicitly (an ambient value could drop the remap)")

    # 4b. Both release slots must link a byte-identical Swift frontend. SwiftPM's release link
    #     records the per-slot scratch path (the object files' debug map) in the executable and
    #     `-file-prefix-map` only rewrites Swift source paths, so `ld -S` omits that debug map. The
    #     debug kind keeps its debug map for dsymutil, so the flag is release-only, and the bin-path
    #     query shares the same list so it can never rebuild a different frontend.
    if code.count("installerSwiftArguments(") < 3:
        errors.append("the Swift frontend build and the bin-path query do not share one argument list")
    swift = re.search(r"fun installerSwiftArguments\(.*?\n    \}", code, re.S)
    if swift is None:
        errors.append("no installerSwiftArguments helper")
    else:
        helper = swift.group(0)
        release = re.search(r'if \(kind\.name == "release"\) \{(.*?)\n        \}', helper, re.S)
        if release is None:
            errors.append("the release Swift frontend does not scope ld -S to the release kind")
        elif '"-Xswiftc", "-Xlinker", "-Xswiftc", "-S"' not in release.group(1):
            errors.append("the release Swift frontend does not drop the per-slot debug map (ld -S)")
        if helper.count('"-Xswiftc", "-Xlinker", "-Xswiftc", "-S"') != 1:
            errors.append("the per-slot debug map flag is not confined to the release branch")

    # 5. The exact origin is a tracked build input of the host build.
    if 'environment("VELES_ALLOWED_ORIGINS"' not in code or 'inputs.property("VELES_ALLOWED_ORIGINS"' not in code:
        errors.append("VELES_ALLOWED_ORIGINS is not both a build input and the host build environment")
    if '"origin"' not in code:
        errors.append("the origin is not derived from the tracked config via assemble_unsigned.py origin")
    if not re.search(r"inputs\.files?\(config\)", code):
        errors.append("the release config is not declared as a task input")

    # 5a. One installer version: the production channel may take VELES_INSTALLER_VERSION (validated,
    # otherwise the config's version); the fixture channel always uses its config. The host, the
    # uninstaller and both assembly passes declare it as an input and receive it.
    if 'providers.environmentVariable("VELES_INSTALLER_VERSION")' not in code:
        errors.append("installerVersion is not read from VELES_INSTALLER_VERSION")
    elif code.count('environmentVariable("VELES_INSTALLER_VERSION")') != 1:
        errors.append("VELES_INSTALLER_VERSION must be read exactly once")
    version_decl = re.search(r"val installerVersion\s*=\s*(.*?)\n    val ", code, re.S)
    if version_decl is None:
        errors.append("no installerVersion provider is declared")
    else:
        decl = version_decl.group(1)
        if not re.search(r'if \(channel == "production"\)\s*\{?\s*providers\.environmentVariable\("VELES_INSTALLER_VERSION"\)', decl):
            errors.append("only the production channel may take VELES_INSTALLER_VERSION")
        if "orElse(configVersion)" not in decl or not re.search(r"\}\s*else\s*\{\s*configVersion\s*\}", decl):
            errors.append("installerVersion does not fall back to the config version")
        if "installerVersionPattern.matches(version)" not in decl or "require(" not in decl:
            errors.append("installerVersion is not validated as MAJOR.MINOR.PATCH")
    if "releaseVersion" in code:
        errors.append("a task still reads the config version directly instead of installerVersion")
    declaration = 'inputs.property("installerVersion", installerVersion)'
    for marker, label, passes in (
        ('"${taskName}Host$suffix"', "host", 'environment("VELES_HOST_VERSION", installerVersion.get())'),
        ('"${taskName}Uninstaller$suffix"', "uninstaller", '"-Version", installerVersion.get()'),
        ('"${taskName}Assemble$suffix"', "bootstrap assembly", "assemblerArguments("),
        ('"${taskName}AssembleFinal$suffix"', "final assembly", "assemblerArguments("),
    ):
        task = object_block(code, marker)
        if task is None or declaration not in task:
            errors.append("the %s task does not declare inputs.property(\"installerVersion\", installerVersion)" % label)
        elif passes not in task:
            errors.append("the %s task does not receive installerVersion" % label)
    if '"--version", installerVersion.get()' not in code:
        errors.append("the assembler arguments do not pass --version installerVersion")
    # The release finalizer publishes tree.tar/provenance.json from both slots: it must re-run when
    # the version or either slot's final assembly changes, never stay UP-TO-DATE on a stale tree.
    finalizer = None
    position = code.find("tasks.register<Exec>(taskName)")
    while position >= 0:
        candidate = object_block(code[position:], "tasks.register<Exec>(taskName)")
        if candidate is not None and '"finalize"' in candidate:
            finalizer = candidate
            break
        position = code.find("tasks.register<Exec>(taskName)", position + 1)
    if finalizer is None or declaration not in finalizer:
        errors.append("the release finalize task does not declare inputs.property(\"installerVersion\", installerVersion)")
    elif "inputs.files(slotOutputs.values)" not in finalizer:
        errors.append("the release finalize task does not declare the slot trees it publishes as inputs")

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
    if 'it.dir("slots/$buildName/$channel/$slot")' not in code:
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
    debug = re.search(r'registerInstallerProducer\(\s*"bridgeInstallerDebug"(.*?)\n\)', code, re.S)
    if not debug or '"fixture"' not in debug.group(1) or "installerFixtureConfig" not in debug.group(1):
        errors.append("bridgeInstallerDebug is not registered as the fixture channel with the tracked fixture config")
    elif "installerReleaseConfig" in debug.group(1) or '"production"' in debug.group(1):
        errors.append("bridgeInstallerDebug references a production input")
    elif "installerDebugKind" not in debug.group(1):
        errors.append("bridgeInstallerDebug does not use the debug build kind")
    if code.count("registerInstallerProducer(") != 4:  # the definition and the three registrations
        errors.append("registerInstallerProducer must be defined once and called exactly three times")
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
    # N6: the observation must run in the pinned crate context, so `rustup`/`rustc`
    # resolve the installer's rust-toolchain.toml exactly as the Cargo build does.
    observation_block = object_block(code, '"bridgeInstallerToolObservation"')
    if observation_block is None or "workingDir(installerDir)" not in observation_block:
        errors.append("the native tool observation does not run from the pinned installer crate context")
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

    # 9. Debug packaging: a debug build produces the FINISHED installer through release_gate.py
    #    package-debug (never the A/B finalize step) and stages symbols for upload.
    debug_branch = object_block(code, 'if (kind.name == "debug")')
    if debug_branch is None:
        errors.append("the registration does not branch on the debug build kind")
    elif '"package-debug"' not in debug_branch:
        errors.append("the debug producer does not package a finished installer (package-debug)")
    elif '"finalize"' in debug_branch:
        errors.append("the debug producer must not run the release A/B finalize step")
    if '"package-debug"' not in code:
        errors.append("bridgeInstallerDebug does not package a finished installer (package-debug)")
    if "release_gate.py" not in code:
        errors.append("the debug packager is not release_gate.py")
    if 'environmentVariable("VELES_WORKFLOW_RUN_ID")' not in code or 'orElse("local")' not in code:
        errors.append("the debug producer does not bind the workflow run id with a local default")
    if 'environment("VELES_WORKFLOW_RUN_ID"' not in code:
        errors.append("the debug packager does not receive the workflow run id")
    if debug_kind is None or "outputDirectory = \"debug\"" not in debug_kind:
        errors.append("the debug build kind does not publish to out/debug")
    if '"--symbols"' not in code:
        errors.append("the debug packager is not given the staged symbols")
    if code.count('"finalize"') != 1:
        errors.append("exactly the release finalizer may call assemble_unsigned finalize")
    # Symbols: a plain, always-run task (a Sync would be NO-SOURCE on macOS and never generate
    # dSYMs), real binary inputs, the Swift frontend included on macOS, and a failing dsymutil.
    symbols = object_block(code, '"${taskName}Symbols"')
    if symbols is None:
        errors.append("no debug symbol task is registered")
    else:
        if 'tasks.register("${taskName}Symbols")' not in code:
            errors.append("the symbol task must be a plain task, not a source-skipped Sync")
        if 'tasks.register<Sync>("${taskName}Symbols")' in code:
            errors.append("the symbol task must be a plain task, not a source-skipped Sync")
        if "outputs.upToDateWhen { false }" not in symbols:
            errors.append("the debug symbol task can be skipped as up to date")
        if 'inputs.files(lastEmbeddedTargetDir!!' not in symbols or 'inputs.files(lastHostTargetDir!!' not in symbols:
            errors.append("the debug symbol task does not declare its debug image inputs")
        if "lastFrontendScratchDir!!" not in symbols:
            errors.append("the debug symbol task does not include the Swift frontend")
        if '.endsWith(".pdb")' not in symbols:
            errors.append("the debug symbol task does not collect the available PDBs")
        if '"xcrun", "dsymutil"' not in symbols or "exitValue == 0" not in symbols:
            errors.append("dSYM generation is missing or its failure is ignored")
        if "walkTopDown().any { it.isFile }" not in symbols:
            errors.append("the debug symbol task does not require at least one symbol")
    # The packaging task depends on metadata/symbol/script inputs, not only the tree.
    packager = object_block(code, 'return tasks.register<Exec>(taskName)')
    if packager is None or 'inputs.dir(lastSymbolsDir!!)' not in packager:
        errors.append("the debug packaging task does not declare the symbol directory as an input")
    if 'inputs.property("VELES_WORKFLOW_RUN_ID", runId)' not in code:
        errors.append("the debug packaging task does not declare the workflow run id as an input")
    if 'installerDir.file("windows/installer.nsi")' not in code:
        errors.append("the debug packaging task does not declare the NSIS recipe as an input")
    if '"-Channel", channel' not in code:
        errors.append("the generated uninstaller is not compiled for the producer channel")

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
        "gradle": r".\gradlew.bat",
        "installer": "setup.exe",
    },
    "macos": {
        "file": ".github/workflows/build-native-macos.yml",
        "runner": "macos-26",
        "artifact": "native-unsigned-macos",
        "gradle": "./gradlew",
        "installer": "Veles Bridge Installer.dmg",
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
    if not re.search(r"build-kind:\s*\n\s*required:\s*true\s*\n\s*type:\s*string\b", body):
        errors.append("build-kind is not a required string input")
    if re.search(r"build-kind:\s*\n\s*required:\s*true\s*\n\s*type:\s*string\s*\n\s*default:", body):
        errors.append("build-kind must not have a default (the caller states release or debug)")
    if "-latest" in body or not re.search(r"runs-on:\s*%s\b" % re.escape(spec["runner"]), body) or body.count("runs-on:") != 1:
        errors.append("exactly one job on %s is required" % spec["runner"])
    # No Tauri scaffold production path.
    if re.search(r"(?i)tauri|\bnpm\b|setup-node|node-version|bridgePackage|bridgeBundle|bridgeBuild\b|bridgeManifests|"
                 r"provision-windows-tools|NSIS_PATH|veles-native-bridge|verify/native/", body):
        errors.append("the producer still uses the Tauri scaffold packaging path")
    # The build kind is explicit and validated before anything is built: no config-file probe.
    if "build-kind must be release or debug" not in body:
        errors.append("the workflow does not validate build-kind as release or debug")
    else:
        build_positions = [body.index(task) for task in ("bridgeInstallerUnsigned", "bridgeInstallerDebug") if task in body]
        if build_positions and body.index("build-kind must be release or debug") > min(build_positions):
            errors.append("build-kind must be validated before any build runs")
    if re.search(r"release-%s\.json" % platform, body):
        errors.append("the producer must never select its channel from config-file existence")
    # Exactly one release build and one debug build, each for its own build kind.
    if body.count("bridgeInstallerUnsigned") != 1:
        errors.append("bridgeInstallerUnsigned must be invoked exactly once (found %d)" % body.count("bridgeInstallerUnsigned"))
    if body.count("bridgeInstallerDebug") != 1:
        errors.append("bridgeInstallerDebug must be invoked exactly once (found %d)" % body.count("bridgeInstallerDebug"))
    if "bridgeInstallerPackageFixture" in body:
        errors.append("the producer workflows must not fall back to a fixture input tree")
    for scaffold in ("bridgePackage", "bridgeHostBuild"):
        if scaffold in body:
            errors.append("the producer invokes %s" % scaffold)
    if "--offline" in body:
        errors.append("the producer must not run offline (no wrapper)")
    def selected_for(kind_value):
        index = body.find("bridgeInstallerDebug" if kind_value == "debug" else "bridgeInstallerUnsigned")
        if index < 0:
            return False
        condition = body.rfind("inputs.build-kind == '%s'" % kind_value, 0, index)
        return condition >= 0 and index - condition < 400

    if not selected_for("release"):
        errors.append("bridgeInstallerUnsigned must run only for build-kind release")
    if not selected_for("debug"):
        errors.append("bridgeInstallerDebug must run only for build-kind debug")
    if "VELES_WORKFLOW_RUN_ID: ${{ github.run_id }}" not in body:
        errors.append("the debug build must receive the workflow run id")
    for task in ("bridgeInstallerUnsigned", "bridgeInstallerDebug"):
        if ("%s %s" % (spec["gradle"], task)) not in body:
            errors.append("%s must invoke %s with the platform wrapper" % (spec["file"], task))
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
        # N6: the tool observation requires cl/link and VCToolsVersion,
        # WindowsSDKVersion and UCRTVersion. The producer must enter the selected
        # Visual Studio developer environment and export it to the build steps,
        # instead of relying on Rust's own MSVC discovery.
        if "Enter-VsDevShell" not in body or "vswhere" not in body:
            errors.append("the Windows producer never enters the Visual Studio developer environment")
        if "$env:GITHUB_ENV" not in body:
            errors.append("the Visual Studio developer environment is not exported to the build steps")
        for name in ("VCToolsVersion", "WindowsSDKVersion", "UCRTVersion"):
            if name not in body:
                errors.append("the Windows developer environment does not export %s" % name)
        if "Enter-VsDevShell" in body and "bridgeInstallerUnsigned" in body and body.index("Enter-VsDevShell") > body.index("bridgeInstallerUnsigned"):
            errors.append("the Visual Studio developer environment must be entered before the Gradle producer runs")
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
    # The planned installer version is an optional caller input that reaches ONLY the release build,
    # through the step environment (never interpolated into a shell line); the debug build keeps its
    # fixture config's version.
    if not re.search(r"\n      version:\s*\n\s*required:\s*false\s*\n\s*type:\s*string\s*\n\s*default:\s*''\s*\n", body):
        errors.append("the version input must be an optional string defaulting to ''")
    version_steps = [step for step in re.split(r"\n(?=      - )", body) if "VELES_INSTALLER_VERSION" in step]
    if body.count("VELES_INSTALLER_VERSION") != 1 or body.count("inputs.version") != 1 or len(version_steps) != 1:
        errors.append("VELES_INSTALLER_VERSION must be exported exactly once, from inputs.version")
    else:
        step = version_steps[0]
        if not re.search(r"\n        env:\n          VELES_INSTALLER_VERSION: \$\{\{ inputs\.version \}\}\n", step):
            errors.append("VELES_INSTALLER_VERSION must be a step env entry set from ${{ inputs.version }}")
        if "if: inputs.build-kind == 'release'" not in step or "bridgeInstallerUnsigned" not in step:
            errors.append("VELES_INSTALLER_VERSION must be exported only to the build-kind release bridgeInstallerUnsigned step")
        if "bridgeInstallerDebug" in step or "build-kind == 'debug'" in step:
            errors.append("the debug build must not receive VELES_INSTALLER_VERSION")
    if not re.search(r"permissions:\n  contents: read\n", body) or body.count("permissions:") != 1:
        errors.append("permissions must be a single workflow-level contents: read")
    # Uploads: exactly the release input tree or the finished debug installer, exact files,
    # caller-selected names, bounded.
    uploads = upload_blocks(source)
    if len(uploads) != 2:
        errors.append("expected exactly two uploads (release input tree, debug installer), found %d" % len(uploads))
    else:
        release = next((b for b in uploads if "build/native-bridge/installer/out/production/" in b), None)
        debug = next((b for b in uploads if "build/native-bridge/installer/out/debug/" in b), None)
        if release is None or debug is None:
            errors.append("uploads must be the production input tree and the finished debug installer")
        else:
            for block, label in ((release, "release"), (debug, "debug")):
                if "name: ${{ inputs.artifact-name }}" not in block:
                    errors.append("the %s upload must use the caller-selected artifact name" % label)
            for name in ("tree.tar", "provenance.json", "report-ab.json", "SHA256SUMS"):
                if "build/native-bridge/installer/out/production/%s" % name not in release:
                    errors.append("the release upload omits %s" % name)
            for name in (spec["installer"], "metadata.json", "SHA256SUMS", "symbols"):
                if "build/native-bridge/installer/out/debug/%s" % name not in debug:
                    errors.append("the debug upload omits %s (the artifact must be a finished installer with metadata and symbols)" % name)
            if "if: inputs.build-kind == 'release'" not in release.split("with:")[0]:
                errors.append("the release upload must be conditional on build-kind release")
            if "if: inputs.build-kind == 'debug'" not in debug.split("with:")[0]:
                errors.append("the debug upload must be conditional on build-kind debug")
            if "out/debug/" in release or "out/production/" in debug:
                errors.append("a debug upload must never carry the production input tree")
            for block in (release, debug):
                if "if-no-files-found: error" not in block or not re.search(r"retention-days:\s*\d+", block):
                    errors.append("every upload must fail on missing files and be retention-bounded")
                if re.search(r"path:[^\n]*\*|veles-native-host(?:\.exe)?\s*$", block, re.M):
                    errors.append("uploads must name exact files, not globs or bare executables (exec bits are dropped)")
    return errors


# --------------------------------------------------------------------------- callers


def job_block(source, job):
    match = re.search(r"\n  %s:\n(.*?)(?=\n  [\w-]+:\n|\Z)" % re.escape(job), source, re.S)
    return match.group(0) if match else ""


def check_release_build(source):
    errors = []
    executable = strip_yaml_comments(source)
    # Native installers are built only by build-pr-installers.yml (debug) and the release
    # publisher. This label/change-driven build caller must neither define native jobs nor
    # select a native build kind.
    for job in ("native-windows", "native-macos", "windows", "macos"):
        if job_block(source, job):
            errors.append("release-build must not define the %s job" % job)
    for token in (
        "build-native-windows.yml", "build-native-macos.yml",
        "native-unsigned-windows", "native-unsigned-macos", "build-kind:",
        "bridgeInstallerUnsigned", "bridgeInstallerDebug", "bridgeInstallerPackageFixture",
        "release-native-installers.yml", "native-release", "native-installer-debug",
    ):
        if token in executable:
            errors.append("release-build must not produce native installers (%s)" % token)
    # No publication: no dispatch, no write token, no secrets, no release mutation.
    if "workflow_dispatch" in executable:
        errors.append("release-build must not be manually dispatchable")
    if "contents: write" in executable or "write-all" in executable:
        errors.append("release-build must not grant a write token")
    if re.search(r"(?m)^\s*secrets:", executable) or "${{ secrets." in executable:
        errors.append("release-build must not pass or reference secrets")
    if re.search(r"(?m)^\s*environment:", executable):
        errors.append("release-build must not enter a protected environment")
    if re.search(r"gh release (?:create|delete|upload|edit)|git push|git tag ", executable):
        errors.append("release-build must not publish, tag or push")
    return errors


def check_gate_boundary(workflows):
    """The release caller authorizes one owner master dispatch and builds the same run's inputs."""
    errors = []
    gate = workflows["release-native-installers.yml"]
    body = strip_yaml_comments(gate)
    if 'github.event_name == \'workflow_dispatch\'' not in body or "github.ref == 'refs/heads/master'" not in body:
        errors.append("the release caller no longer requires a manual dispatch on master")
    for condition in re.findall(r"(?m)^    if:\s*(.+)$", gate):
        if "github.actor == 'raidenyn'" not in condition or "github.triggering_actor == 'raidenyn'" not in condition:
            errors.append("a release job condition does not re-check the owner and rerun actor")
            break
    for condition in re.findall(r"(?m)^    if:\s*(.+)$", gate):
        if "github.event_name == 'workflow_dispatch'" not in condition or "github.ref == 'refs/heads/master'" not in condition:
            errors.append("a release job condition does not re-check the master dispatch")
            break
    guard = job_block(gate, "guard")
    for check in (
        '[ "$EVENT" = workflow_dispatch ]',
        '[ "$REF" = refs/heads/master ]',
        '[ "$ACTOR" = raidenyn ]',
        '[ "$TRIGGERING_ACTOR" = raidenyn ]',
    ):
        if check not in guard:
            errors.append("the guard does not enforce %s before any build" % check)
    if "- uses: actions/checkout@" in guard:
        errors.append("the guard must reject before checkout")
    for platform in ("windows", "macos"):
        build = job_block(gate, "build-%s" % platform)
        if "build-kind: release" not in build:
            errors.append("build-%s is not a release-profile build" % platform)
        if "commit-sha: ${{ github.sha }}" not in build:
            errors.append("build-%s does not build this run's master commit" % platform)
    if "gh run view" in body or "run-id:" in body or "unsigned_run_" in body:
        errors.append("the release caller still consumes another run's artifacts")
    if ".workflowName == \"release-build\"" in body or ".headSha == $sha" in body:
        errors.append("the release caller still inspects external producer runs")
    if "VELES_APPROVED_DESCRIPTOR_SHA256" in body:
        errors.append("the obsolete manual descriptor approval variable is back")
    if "pull_request" in body:
        errors.append("the release workflow must not accept pull-request context")
    # One protected signing/packaging job per selected platform, and no debug packaging.
    for platform in ("windows", "macos"):
        if not job_block(gate, "sign-%s" % platform):
            errors.append("the release path is missing sign-%s" % platform)
    for legacy in ("sign-windows-nested", "sign-windows-outer", "assemble-windows", "assemble-macos"):
        if re.search(r"(?m)^  %s:\s*$" % re.escape(legacy), gate):
            errors.append("the release path still has a separate %s job" % legacy)
    for token in ("build-kind: debug", "bridgeInstallerDebug", "build-pr-installers"):
        if token in body:
            errors.append("the release path can invoke debug packaging (%s)" % token)
    # Every result the gate reads has a direct needs entry.
    gate_job = job_block(gate, "gate")
    needs = re.search(r"(?m)^    needs:\s*\[(.*?)\]\s*$", gate_job)
    required = {"guard", "build-windows", "build-macos", "verify-windows", "verify-macos", "sign-windows", "sign-macos"}
    declared = {value.strip() for value in needs.group(1).split(",")} if needs else set()
    if not required <= declared:
        errors.append("the gate does not directly depend on every job whose result it reads: %s" % sorted(required - declared))
    for name, source in workflows.items():
        if name == "release-native-installers.yml":
            continue
        other = strip_yaml_comments(source)
        if re.search(r"VELES_WIN_|VELES_MAC_|VELES_NOTARY_|native-release|VELES_APPROVED_", other):
            errors.append("%s references signing credentials or the protected environment" % name)
    return errors


def installer_check_jobs(body):
    """check-installer.yml top-level job key -> job body."""
    jobs = {}
    current = None
    for line in body.split("\njobs:\n", 1)[-1].splitlines():
        if line.strip() and not line.startswith(" "):
            break
        match = re.match(r"^  ([\w-]+):\s*$", line)
        if match:
            current = match.group(1)
            jobs[current] = ""
        elif current:
            jobs[current] += line + "\n"
    return jobs


def check_host_workflow_installer(source):
    """check-installer.yml: the `installer` CI node (lint, unit-tests per OS, scripts-tests)."""
    errors = []
    body = strip_yaml_comments(source)
    jobs = installer_check_jobs(body)

    def runner(job_body):
        match = re.search(r"^    runs-on:\s*(\S+)\s*$", job_body, re.M)
        return match.group(1) if match else None

    for task in ("bridgeInstallerFormat", "bridgeInstallerLint", "bridgeInstallerTest"):
        runners = sorted(runner(job) for job in jobs.values() if re.search(r"\b%s\b" % task, job))
        if runners != ["macos-26", "ubuntu-latest", "windows-2025"]:
            errors.append("%s must run once on each of the three platforms (found %s)" % (task, runners))
    if body.count("bridgeInstallerScriptsTest") != 1:
        errors.append("bridgeInstallerScriptsTest must run exactly once (installer / scripts-tests)")
    if "bridgeInstallerScriptsTest" not in jobs.get("scripts-tests", ""):
        errors.append("bridgeInstallerScriptsTest must run in the scripts-tests job")
    for task in ("bridgeInstallerUnsigned", "bridgeInstallerPackageFixture", "bridgeInstallerDebug"):
        if task in body:
            errors.append("the installer check workflow must not build installer inputs (%s)" % task)
    for name, job in jobs.items():
        if not re.search(r"working-directory:\s*native-bridge/installer\s*\n\s*run:\s*rustup show active-toolchain", job):
            errors.append("the installer toolchain is not activated in %s" % name)
    if len(jobs) < 3:
        errors.append("the installer check workflow must have its lint, unit-tests and scripts-tests jobs")
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
        block = producer_block(self.gradle)
        mutated = self.gradle.replace(block, block.replace('mutableListOf("cargo", "build", "--locked")', 'mutableListOf("cargo", "build")'))
        found = self.violations(mutated)
        self.assertTrue(any("--locked" in e for e in found), found)

    def test_a_non_production_binary_is_rejected(self):
        found = self.violations(self.mutate('"--bin", "veles-native-host"', '"--bin", "veles-host-test-driver"', 1))
        self.assertTrue(any("outside the production set" in e or "test-driver" in e for e in found), found)

    def test_all_features_and_simulator_flags_are_rejected(self):
        anchor = 'mutableListOf("cargo", "build", "--locked")'
        for flag in ('"--all-features"', '"--workspace"', '"--bins"'):
            found = self.violations(self.mutate(anchor, '%s, %s' % (anchor, flag), 1))
            self.assertTrue(any("must not use" in e for e in found), (flag, found))
        # The one reviewed feature may belong to the debug kind only.
        found = self.violations(self.mutate("cargoFeatures = emptyList()", 'cargoFeatures = listOf("--features", "test-driver")', 1))
        self.assertTrue(any("debug feature" in e or "must not use" in e or "test-driver" in e for e in found), found)
        found = self.violations(self.mutate('listOf("--features", "debug-fixture-trust")', 'listOf("--features", "debug-fixture-trust", "--all-features")', 1))
        self.assertTrue(any("--features" in e or "must not use" in e or "debug" in e for e in found), found)

    def test_a_missing_target_triple_is_rejected(self):
        block = producer_block(self.gradle)
        mutated = self.gradle.replace(block, block.replace('listOf("--target", installerRustTriple, ', 'listOf(', 2))
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
        found = self.violations(self.mutate("target/$buildName-$channel-$slot/installer", "target/$channel/installer", 1))
        self.assertTrue(any("distinct per build kind, channel and slot" in e for e in found), found)

    def test_a_build_that_keeps_a_stale_target_is_rejected(self):
        block = producer_block(self.gradle)
        mutated = self.gradle.replace(block, re.sub(r"delete\([^\n]*\)\n", "\n", block))
        self.assertTrue(any("stale cache" in e for e in self.violations(mutated)))

    def test_missing_path_remapping_is_rejected(self):
        found = self.violations(self.mutate("--remap-path-prefix", "--cfg-noop", 1))
        self.assertTrue(any("remap" in e for e in found), found)
        found = self.violations(self.mutate("/Brepro", "/noop"))
        self.assertTrue(any("deterministic" in e for e in found), found)
        found = self.violations(self.mutate("+crt-static", "+noop"))
        self.assertTrue(any("static CRT" in e for e in found), found)
        found = self.violations(self.mutate('environment("RUSTFLAGS"', 'environment("RUSTFLAGZ"'))
        self.assertTrue(any("RUSTFLAGS" in e for e in found), found)

    def test_the_release_swift_frontend_must_not_record_the_slot_path(self):
        # SwiftPM's release link records the per-slot scratch path (object files and debug map) in
        # the executable; -file-prefix-map only rewrites Swift source paths, so without `ld -S` the
        # two release slots differ and the producer refuses to publish.
        found = self.violations(self.mutate('"-Xswiftc", "-Xlinker", "-Xswiftc", "-S"', '"-Xswiftc", "-no-debug-map"'))
        self.assertTrue(any("debug map" in e for e in found), found)
        block = producer_block(self.gradle)
        mutated = self.gradle.replace(block, block.replace("installerSwiftArguments(", "installerSwiftBuild("))
        self.assertTrue(any("argument list" in e for e in self.violations(mutated)))

    def test_the_debug_map_flag_must_stay_release_only(self):
        # Moving `-S` out of an (otherwise empty) release branch would strip the debug kind's
        # symbols too; a presence-only check would accept that.
        moved = self.mutate(
            '        if (kind.name == "release") {\n'
            '            arguments += listOf("-Xswiftc", "-Xlinker", "-Xswiftc", "-S")\n'
            '        }\n',
            '        if (kind.name == "release") {\n'
            '        }\n'
            '        arguments += listOf("-Xswiftc", "-Xlinker", "-Xswiftc", "-S")\n',
        )
        found = self.violations(moved)
        self.assertTrue(any("debug map" in e or "release kind" in e for e in found), found)

    def test_the_origin_must_be_a_tracked_build_input(self):
        found = self.violations(self.mutate('inputs.property("VELES_ALLOWED_ORIGINS"', 'inputs.property("OTHER"'))
        self.assertTrue(any("VELES_ALLOWED_ORIGINS" in e for e in found), found)
        found = self.violations(self.mutate('environment("VELES_ALLOWED_ORIGINS"', 'environment("OTHER"'))
        self.assertTrue(any("VELES_ALLOWED_ORIGINS" in e for e in found), found)

    def test_the_installer_version_must_be_a_declared_input_of_every_consumer(self):
        declaration = 'inputs.property("installerVersion", installerVersion)'
        block = producer_block(self.gradle)
        self.assertEqual(5, block.count(declaration))
        # Dropping any one declaration (host, uninstaller, bootstrap or final assembly, finalize) is named.
        for index in range(5):
            start = -1
            for _ in range(index + 1):
                start = block.index(declaration, start + 1)
            mutated = self.gradle.replace(block, block[:start] + block[start + len(declaration):])
            found = self.violations(mutated)
            self.assertTrue(any("installerVersion" in e for e in found), (index, found))
        for old, new in (
            ('environment("VELES_HOST_VERSION", installerVersion.get())', 'environment("VELES_HOST_VERSION", "0.0.0")'),
            ('"-Version", installerVersion.get()', '"-Version", "0.0.0"'),
            ('"--version", installerVersion.get()', '"--version", "0.0.0"'),
            ('providers.environmentVariable("VELES_INSTALLER_VERSION")', 'providers.gradleProperty("installerVersion")'),
            ('orElse(configVersion)', 'orElse("0.0.0")'),
            ('if (channel == "production") {', 'if (true) {'),
            ('require(installerVersionPattern.matches(version))', 'check(true)'),
            ('} else {\n            configVersion\n        }', '} else {\n            providers.environmentVariable("X").orElse(configVersion)\n        }'),
        ):
            found = self.violations(self.mutate(old, new))
            self.assertTrue(any("installerVersion" in e or "VELES_INSTALLER_VERSION" in e or "version" in e for e in found), (old, found))

    def test_the_finalizer_must_track_the_slot_trees_it_publishes(self):
        found = self.violations(self.mutate("inputs.files(slotOutputs.values)", "inputs.files()"))
        self.assertTrue(any("slot trees it publishes" in e for e in found), found)

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
        found = self.violations(self.mutate('it.dir("slots/$buildName/$channel/$slot")', 'it.dir("slots/$buildName/production/$slot")', 1))
        self.assertTrue(any("carrier" in e for e in found), found)

    def test_a_fixture_config_cannot_feed_the_production_task(self):
        found = self.violations(self.mutate('"bridgeInstallerUnsigned", "production", installerReleaseConfig', '"bridgeInstallerUnsigned", "production", installerFixtureConfig'))
        self.assertTrue(any("bridgeInstallerUnsigned" in e for e in found), found)

    def test_the_fixture_task_cannot_become_production(self):
        found = self.violations(self.mutate('"bridgeInstallerPackageFixture", "fixture"', '"bridgeInstallerPackageFixture", "production"'))
        self.assertTrue(any("bridgeInstallerPackageFixture" in e for e in found), found)
        found = self.violations(self.mutate("installerFixtureConfig,", "installerReleaseConfig,", 1) if "installerFixtureConfig," in producer_block(self.gradle) else self.mutate("installerFixtureConfig", "installerReleaseConfig"))
        self.assertTrue(any("fixture" in e.lower() for e in found), found)

    def test_the_debug_task_is_fixture_only_and_uses_the_debug_kind(self):
        found = self.violations(self.mutate('"bridgeInstallerDebug", "fixture"', '"bridgeInstallerDebug", "production"'))
        self.assertTrue(any("bridgeInstallerDebug" in e for e in found), found)
        found = self.violations(self.mutate('"bridgeInstallerDebug", "fixture", installerFixtureConfig,', '"bridgeInstallerDebug", "fixture", installerReleaseConfig,'))
        self.assertTrue(any("bridgeInstallerDebug" in e for e in found), found)
        found = self.violations(self.mutate('installerDebugKind,', 'installerReleaseKind,', 1))
        self.assertTrue(any("debug build kind" in e or "bridgeInstallerDebug" in e for e in found), found)

    def test_the_debug_artifact_must_be_the_finished_installer_not_the_input_tree(self):
        found = self.violations(self.mutate('"package-debug"', '"finalize"', 1))
        self.assertTrue(any("package-debug" in e or "finalize" in e or "finished installer" in e for e in found), found)
        found = self.violations(self.mutate('environmentVariable("VELES_WORKFLOW_RUN_ID")', 'environmentVariable("OTHER_RUN_ID")'))
        self.assertTrue(any("run id" in e for e in found), found)
        found = self.violations(self.mutate('.endsWith(".pdb")', '.endsWith(".txt")', 1))
        self.assertTrue(any("PDB" in e or "symbols" in e for e in found), found)
        found = self.violations(self.mutate('"xcrun", "dsymutil"', '"xcrun", "not-dsymutil"', 1))
        self.assertTrue(any("dSYM" in e or "symbols" in e for e in found), found)
        found = self.violations(self.mutate(
            "outputs.dir(lastSymbolsDir!!)\n            outputs.upToDateWhen { false }",
            "outputs.dir(lastSymbolsDir!!)\n            outputs.upToDateWhen { true }",
        ))
        self.assertTrue(any("skipped" in e for e in found), found)
        found = self.violations(self.mutate('inputs.dir(lastSymbolsDir!!)', "", 1))
        self.assertTrue(any("symbol directory" in e for e in found), found)
        found = self.violations(self.mutate('inputs.property("VELES_WORKFLOW_RUN_ID", runId)', "", 1))
        self.assertTrue(any("run id" in e for e in found), found)

    def test_the_release_build_kind_cannot_lose_its_release_profile_or_static_crt(self):
        found = self.violations(self.mutate('cargoProfileArguments = listOf("--release")', 'cargoProfileArguments = emptyList()', 1))
        self.assertTrue(any("--release" in e for e in found), found)
        found = self.violations(self.mutate('cargoBinaryDirectory = "release"', 'cargoBinaryDirectory = "debug"', 1))
        self.assertTrue(any("release binary directory" in e for e in found), found)
        found = self.violations(self.mutate("target-feature=+crt-static", "target-feature=+noop", 1))
        self.assertTrue(any("static CRT" in e for e in found), found)


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

    def test_the_tool_observation_runs_in_the_pinned_crate_context(self):
        observation = object_block(self.gradle, '"bridgeInstallerToolObservation"')
        self.assertIsNotNone(observation)
        self.assertIn("workingDir(installerDir)", observation)
        mutated = self.gradle.replace(
            observation, observation.replace("workingDir(installerDir)\n", "", 1), 1
        )
        found = self.violations(mutated)
        self.assertTrue(any("pinned installer crate context" in e for e in found), found)

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


class DebugTrustBoundary(unittest.TestCase):
    """The debug fixture trust adapter is compile-time and fixture-only (OTP-25 PR path)."""

    def test_the_adapter_is_a_compile_time_feature_with_no_runtime_switch(self):
        manifest = read("native-bridge/installer/Cargo.toml")
        self.assertIn("[features]\ndebug-fixture-trust = []", manifest)
        build = read("native-bridge/installer/build.rs")
        self.assertIn("CARGO_FEATURE_DEBUG_FIXTURE_TRUST", build)
        self.assertIn("PROFILE", build)
        self.assertIn("production", build)
        platform = read("native-bridge/installer/src/platform.rs")
        self.assertIn('#[cfg(feature = "debug-fixture-trust")]\npub mod debug_trust;', platform)
        adapter = read("native-bridge/installer/src/platform/debug_trust.rs")
        # No runtime input may select the adapter: it is a compile-time module and takes only the
        # caller-authorized descriptor and package-derived carrier bytes.
        for forbidden in ("std::env", "env::var", "std::env::args", "std::process::Command"):
            self.assertNotIn(forbidden, adapter, forbidden)
        for name in ("src/platform/windows.rs", "src/platform/macos.rs"):
            body = read("native-bridge/installer/" + name)
            self.assertIn('#[cfg(feature = "debug-fixture-trust")]', body, name)
            self.assertIn('#[cfg(not(feature = "debug-fixture-trust"))]', body, name)
            self.assertIn("super::debug_trust::", body, name)

    def test_the_release_build_never_enables_the_feature(self):
        gradle = producer_block(read("build.gradle.kts"))
        self.assertIsNotNone(gradle)
        release = kind_declaration(gradle, "installerReleaseKind")
        debug = kind_declaration(gradle, "installerDebugKind")
        self.assertIsNotNone(release)
        self.assertIsNotNone(debug)
        self.assertIn("cargoFeatures = emptyList()", release)
        self.assertIn('cargoFeatures = listOf("--features", "debug-fixture-trust")', debug)
        self.assertIn("slots = listOf(\"a\")", debug)
        return


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
            "second-debug": source + "\n      - run: %s bridgeInstallerDebug\n" % spec["gradle"],
            "fixture-fallback": source + "\n      - run: %s bridgeInstallerPackageFixture\n" % spec["gradle"],
            "unconditional-release": source.replace("        if: inputs.build-kind == 'release'\n", ""),
            "unconditional-debug": source.replace("        if: inputs.build-kind == 'debug'\n", ""),
            "secret": source.replace("permissions:\n  contents: read", "permissions:\n  contents: read\nsecrets:\n  x: ${{ secrets.X }}", 1),
            "environment": source.replace("    steps:", "    environment: native-release\n    steps:", 1),
            "download": source.replace("    steps:", "    steps:\n      - uses: actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131 # v7\n        with:\n          name: x\n          run-id: 1\n", 1),
            "unpinned": source.replace("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "actions/checkout@v4", 1),
            "latest-runner": source.replace(spec["runner"], "%s-latest" % spec["runner"].split("-")[0], 1),
            "bare-glob-upload": source.replace("build/native-bridge/installer/out/production/tree.tar", "build/native-bridge/installer/out/production/*", 1),
            "dropped-provenance": source.replace("            build/native-bridge/installer/out/production/provenance.json\n", ""),
            "dropped-debug-metadata": source.replace("            build/native-bridge/installer/out/debug/metadata.json\n", ""),
            "dropped-debug-symbols": source.replace("            build/native-bridge/installer/out/debug/symbols\n", ""),
            "wrong-artifact-default": source.replace("default: %s" % spec["artifact"], "default: native-%s" % platform, 1),
            "fixture-mislabeled": source.replace("if: inputs.build-kind == 'debug'", "if: inputs.build-kind == 'release'", 1),
            "missing-build-kind": re.sub(r"      build-kind:\n        required: true\n        type: string\n", "", source, count=1),
            "build-kind-default": source.replace(
                "      build-kind:\n        required: true\n        type: string\n",
                "      build-kind:\n        required: true\n        type: string\n        default: release\n",
                1,
            ),
            "unvalidated-kind": source.replace("build-kind must be release or debug", "build-kind is whatever"),
            "config-probe": source.replace(
                "      - name: Validate the build kind",
                "      - name: Probe for the approved release config\n        run: test -f native-bridge/installer/config/release-%s.json\n"
                "      - name: Validate the build kind" % platform,
                1,
            ),
            "unactivated-toolchain": source.replace("working-directory: native-bridge/installer\n        run: rustup show active-toolchain", "run: true", 1),
            "carrier-env": source.replace(
                "      - name: Build the debug fixture-channel installer",
                "      - name: Build the debug fixture-channel installer\n        env:\n          VELES_EMBEDDED_CARRIER: unverified.bin",
                1,
            ),
            "version-dropped": source.replace("        env:\n          VELES_INSTALLER_VERSION: ${{ inputs.version }}\n", "", 1),
            "version-to-debug": source.replace("        env:\n          VELES_INSTALLER_VERSION: ${{ inputs.version }}\n", "", 1).replace(
                "          VELES_WORKFLOW_RUN_ID: ${{ github.run_id }}\n",
                "          VELES_WORKFLOW_RUN_ID: ${{ github.run_id }}\n          VELES_INSTALLER_VERSION: ${{ inputs.version }}\n", 1),
            "version-job-env": source.replace("        env:\n          VELES_INSTALLER_VERSION: ${{ inputs.version }}\n", "", 1).replace(
                "    steps:", "    env:\n      VELES_INSTALLER_VERSION: ${{ inputs.version }}\n    steps:", 1),
            "version-inline": source.replace("        env:\n          VELES_INSTALLER_VERSION: ${{ inputs.version }}\n", "", 1).replace(
                "bridgeInstallerUnsigned\n", "bridgeInstallerUnsigned -PinstallerVersion=${{ inputs.version }}\n", 1),
            "version-required": source.replace("      version:\n        required: false", "      version:\n        required: true", 1),
            "silent-upload": source.replace("if-no-files-found: error", "if-no-files-found: ignore"),
            "write-permission": source.replace("contents: read", "contents: write", 1),
        }

    def test_every_mutation_is_named_by_a_violation(self):
        for platform in PRODUCERS:
            for label, mutated in self.mutations(platform).items():
                if mutated == self.source(platform):
                    self.fail("%s/%s did not change the workflow" % (platform, label))
                self.assertNotEqual(check_producer_workflow(mutated, platform), [], "%s/%s" % (platform, label))

    def test_the_build_kind_is_a_required_explicit_selection(self):
        for platform in PRODUCERS:
            source = self.source(platform)
            self.assertNotEqual(
                check_producer_workflow(source.replace("      build-kind:\n        required: true", "      build-kind:\n        required: false", 1), platform),
                [],
                platform,
            )
            self.assertNotEqual(check_producer_workflow(source.replace("build-kind must be release or debug", "true"), platform), [], platform)
            self.assertNotEqual(
                check_producer_workflow(source.replace("if: inputs.build-kind == 'debug'", "if: inputs.build-kind == 'release'", 1), platform),
                [],
                platform,
            )

    def test_macos_must_pin_xcode_and_windows_must_provision_nsis(self):
        source = self.source("macos")
        self.assertNotEqual(check_producer_workflow(source.replace("17F113", "17F000"), "macos"), [])
        self.assertNotEqual(check_producer_workflow(source.replace("DEVELOPER_DIR: /Applications/Xcode_26.6.app", "DEVELOPER_DIR: /Applications/Xcode.app"), "macos"), [])
        windows = self.source("windows")
        self.assertNotEqual(check_producer_workflow(windows.replace("provision-nsis.ps1", "other.ps1"), "windows"), [])
        self.assertNotEqual(check_producer_workflow(windows.replace("build/native-bridge/installer/tools/nsis", "tools/nsis"), "windows"), [])

    def test_windows_must_enter_and_export_the_visual_studio_environment(self):
        windows = self.source("windows")
        for label, mutated in (
            ("no-dev-shell", windows.replace("Enter-VsDevShell", "Write-Host")),
            ("unexported", windows.replace("$env:GITHUB_ENV", "$null")),
            ("no-toolset", windows.replace("VCToolsVersion", "PATH")),
            ("no-sdk", windows.replace("WindowsSDKVersion", "PATH")),
            ("no-ucrt", windows.replace("UCRTVersion", "PATH")),
            ("no-vswhere", windows.replace("vswhere", "other.exe")),
        ):
            self.assertNotEqual(
                check_producer_workflow(mutated, "windows"), [], label
            )


class Callers(unittest.TestCase):
    def test_release_build_calls_no_native_producer_and_publishes_nothing(self):
        self.assertEqual(check_release_build(read(".github/workflows/release-build.yml")), [])

    def test_publication_paths_are_rejected(self):
        source = read(".github/workflows/release-build.yml")
        self.assertIn("permissions:\n  contents: read", source)
        self.assertIn("  pull_request:\n", source)
        for label, mutated in (
            ("write token", source.replace("permissions:\n  contents: read", "permissions:\n  contents: write", 1)),
            ("dispatch", source.replace("  pull_request:\n", "  workflow_dispatch:\n  pull_request:\n", 1)),
            ("secrets", source + "\n  publish:\n    uses: ./.github/workflows/build-android.yml\n    secrets: inherit\n"),
            ("secret expression", source + "\n  publish:\n    env:\n      KEY: ${{ secrets.KEY }}\n"),
            ("environment", source + "\n  publish:\n    runs-on: ubuntu-latest\n    environment: release-publish\n"),
            ("release create", source + "\n  publish:\n    steps:\n      - run: gh release create \"$TAG\"\n"),
            ("release upload", source + "\n  publish:\n    steps:\n      - run: gh release upload \"$TAG\" a.apk\n"),
        ):
            self.assertNotEqual(mutated, source, label)
            self.assertNotEqual(check_release_build(mutated), [], label)

    def test_native_installers_are_absent_and_reintroduction_is_rejected(self):
        source = read(".github/workflows/release-build.yml")
        executable = strip_yaml_comments(source)
        for token in (
            "  native-windows:", "  native-macos:", "  windows:", "  macos:",
            "build-native-windows.yml", "build-native-macos.yml",
            "native-unsigned-windows", "native-unsigned-macos", "build-kind:",
        ):
            self.assertNotIn(token, executable, token)
        for job, builder, kind in (
            ("native-windows", "build-native-windows.yml", "release"),
            ("windows", "build-native-windows.yml", "debug"),
            ("macos", "build-native-macos.yml", "debug"),
        ):
            reintroduced = source + (
                "\n  %s:\n"
                "    needs: [changes]\n"
                "    uses: ./.github/workflows/%s\n"
                "    with:\n"
                "      commit-sha: ${{ github.sha }}\n"
                "      build-kind: %s\n" % (job, builder, kind)
            )
            self.assertNotEqual(check_release_build(reintroduced), [], job)

    def workflows(self):
        directory = ROOT / ".github" / "workflows"
        return {path.name: path.read_text(encoding="utf-8") for path in directory.glob("*.yml")}

    def test_signed_candidates_never_come_from_pull_request_artifacts(self):
        self.assertEqual(check_gate_boundary(self.workflows()), [])

    def test_platform_selection_does_not_require_the_unselected_target(self) -> None:
        gate = read(".github/workflows/release-native-installers.yml")
        self.assertIn("      platform:\n", gate)
        self.assertIn("options: [windows, macos, both]", gate)
        self.assertIn("default: windows", gate)
        self.assertNotIn("windows_only:", gate)
        for platform in ("windows", "macos"):
            self.assertIn("inputs.platform == '%s' || inputs.platform == 'both'" % platform, gate)
        self.assertIn("RESULT_SIGN_WINDOWS: ${{ needs.sign-windows.result }}", gate)
        self.assertIn("RESULT_SIGN_MACOS: ${{ needs.sign-macos.result }}", gate)
        self.assertIn('for os in $selected', gate, "only the selected platforms are required")
        self.assertNotIn('or .event == "pull_request"', gate, "PR producer runs stay rejected")

    def test_gate_boundary_mutations_are_rejected(self):
        workflows = self.workflows()
        gate = workflows["release-native-installers.yml"]
        for old, new in (
            ("[ \"$ACTOR\" = raidenyn ]", "[ \"$ACTOR\" = someone-else ]"),
            ("[ \"$TRIGGERING_ACTOR\" = raidenyn ]", "[ \"$TRIGGERING_ACTOR\" = someone-else ]"),
            ("[ \"$REF\" = refs/heads/master ]", "[ \"$REF\" = refs/heads/feature ]"),
            ("commit-sha: ${{ github.sha }}", "commit-sha: ${{ inputs.commit }}"),
            ("build-kind: release", "build-kind: debug"),
            ("github.actor == 'raidenyn'", "github.actor != ''"),
            (
                "    needs: [guard, build-windows, build-macos, verify-windows, verify-macos, sign-windows, sign-macos]\n",
                "    needs: [guard, build-windows, build-macos, verify-windows, verify-macos, sign-windows]\n",
            ),
        ):
            self.assertIn(old, gate, old)
            self.assertNotEqual(check_gate_boundary({**workflows, "release-native-installers.yml": gate.replace(old, new, 1)}), [], old)
        leaked = {**workflows, "build-native-windows.yml": workflows["build-native-windows.yml"] + "\n      env:\n        VELES_WIN_PFX_BASE64: x\n"}
        self.assertNotEqual(check_gate_boundary(leaked), [])
        approved = {**workflows, "release-native-installers.yml": gate + "\n      VELES_APPROVED_DESCRIPTOR_SHA256: x\n"}
        self.assertNotEqual(check_gate_boundary(approved), [])
        external = {**workflows, "release-native-installers.yml": gate + "\n      - run: gh run view 1\n"}
        self.assertNotEqual(check_gate_boundary(external), [])

    def test_host_check_workflow_runs_the_installer_checks_on_every_platform(self):
        source = read(".github/workflows/check-installer.yml")
        self.assertEqual(check_host_workflow_installer(source), [])
        for mutated in (
            source.replace("bridgeInstallerLint", "bridgeInstallerLynt"),
            source.replace("bridgeInstallerScriptsTest", "bridgeInstallerTest", 1),
            source + "\n      - run: ./gradlew bridgeInstallerUnsigned\n",
            source.replace("working-directory: native-bridge/installer", "working-directory: native-bridge/host", 1),
            source.replace(".\\gradlew.bat bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest",
                           ".\\gradlew.bat bridgeInstallerFormat bridgeInstallerTest"),
            source.replace("  scripts-tests:", "  scripts:"),
        ):
            self.assertNotEqual(mutated, source)
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
