// Top-level build file where you can add configuration options common to all sub-projects/modules.
import com.android.build.api.variant.ApplicationAndroidComponentsExtension
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.Properties
import org.gradle.api.tasks.Delete
import org.gradle.api.tasks.Exec

plugins {
    base
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.compose) apply false
    alias(libs.plugins.ksp) apply false
    alias(libs.plugins.hilt) apply false
    alias(libs.plugins.protobuf) apply false
    alias(libs.plugins.spotless)
    alias(libs.plugins.detekt) apply false
}

spotless {
    kotlin {
        target("**/*.kt")
        targetExclude("**/build/**", "**/.gradle/**", "**/.kotlin/**")
        ktlint().editorConfigOverride(
            mapOf(
                // Compose @Composable functions are PascalCase by convention.
                "ktlint_standard_function-naming" to "disabled",
                // `_foo` backing properties for StateFlow are idiomatic in Compose VMs.
                "ktlint_standard_backing-property-naming" to "disabled",
                // Singleton `INSTANCE` is the idiomatic Room DB pattern.
                "ktlint_standard_property-naming" to "disabled",
                // detekt enforces MaxLineLength separately; long string literals
                // (regex patterns, notification text) would otherwise need wrapping.
                "ktlint_standard_max-line-length" to "disabled",
            ),
        )
        trimTrailingWhitespace()
        endWithNewline()
    }
}

val rustDir = layout.projectDirectory.dir("rust")
val rustCrateDir = rustDir.dir("veles-crypto")
val rustToolsFile = rustDir.file("toolchain-tools.toml")
val rustTargetDir = layout.buildDirectory.dir("rust/target")
val rustToolsDir = layout.buildDirectory.dir("rust-tools")
val wasmPackageDir = layout.projectDirectory.dir("web-extension/rust-wasm/pkg")
val windowsExecutableSuffix = if (System.getProperty("os.name").startsWith("Windows")) ".exe" else ""

fun pinnedToolVersion(name: String): String {
    val pattern = Regex("(?m)^${Regex.escape(name)}\\s*=\\s*\"([^\"]+)\"\\s*$")
    return requireNotNull(pattern.find(rustToolsFile.asFile.readText())) {
        "Missing '$name' pin in ${rustToolsFile.asFile}"
    }.groupValues[1]
}

fun File.prependToPath(existing: String?): String =
    absolutePath + File.pathSeparator + (existing ?: "")

fun requireOnPath(name: String, versionHint: String, installHint: String) {
    val path = System.getenv("PATH") ?: ""
    val found = path.split(File.pathSeparator).any { dir ->
        File(dir, name + windowsExecutableSuffix).let { it.isFile && it.canExecute() }
    }
    check(found) {
        "Could not find '$name' on PATH. $installHint. Version hint: $versionHint."
    }
}

fun requireNdkVersion(ndkDir: File, expected: String) {
    check(ndkDir.isDirectory) {
        "Android NDK $expected is not installed. " +
            "Install it with sdkmanager \"ndk;$expected\"."
    }
    val properties = Properties().apply {
        ndkDir.resolve("source.properties").inputStream().use(::load)
    }
    val actual = properties.getProperty("Pkg.Revision")
    check(actual == expected) {
        "Expected Android NDK $expected at $ndkDir, found '$actual'. " +
            "Install the pinned version with sdkmanager \"ndk;$expected\"."
    }
}

val rustToolchainCheck = tasks.register("rustToolchainCheck") {
    group = "rust"
    inputs.file(rustDir.file("rust-toolchain.toml"))
    doLast {
        requireOnPath(
            "rustup",
            "Rust 1.98.0",
            "Install rustup and run `rustup show active-toolchain` in rust/ to install the pinned toolchain",
        )
        requireOnPath(
            "cargo",
            "Rust 1.98.0",
            "Install rustup and run `rustup show active-toolchain` in rust/ to install the pinned toolchain",
        )
        val rustup = providers.exec {
            workingDir(rustDir)
            commandLine("rustup", "show", "active-toolchain")
        }.standardOutput.asText.get().trim()
        val rustc = providers.exec {
            workingDir(rustDir)
            commandLine("rustc", "--version")
        }.standardOutput.asText.get().trim()
        check(rustc.startsWith("rustc 1.98.0 ")) {
            "rust/rust-toolchain.toml requires Rust 1.98.0; active toolchain is '$rustup' ($rustc)"
        }
    }
}

fun registerCargoTool(
    taskStem: String,
    crateName: String,
    binaryName: String,
): Pair<TaskProvider<Exec>, TaskProvider<Task>> {
    val version = pinnedToolVersion(crateName)
    val installRoot = rustToolsDir.map { it.dir(crateName) }
    val binary = installRoot.map {
        it.file("bin/$binaryName$windowsExecutableSuffix")
    }
    val install = tasks.register<Exec>("install$taskStem") {
        group = "rust"
        description = "Installs $crateName $version into the build directory."
        dependsOn("rustToolchainCheck")
        inputs.property("crateName", crateName)
        inputs.property("version", version)
        inputs.file(rustDir.file("rust-toolchain.toml"))
        outputs.dir(installRoot)
        workingDir(rustDir)
        environment(
            "CARGO_TARGET_DIR",
            layout.buildDirectory.dir("rust/tool-install-target/$crateName").get().asFile,
        )
        doFirst { delete(installRoot) }
        commandLine(
            "cargo", "install", "--locked", "--version", version,
            "--root", installRoot.get().asFile, crateName,
        )
    }
    val verify = tasks.register("verify$taskStem") {
        group = "rust"
        dependsOn(install)
        inputs.property("version", version)
        inputs.file(binary.map { it.asFile })
        doLast {
            val output = providers.exec {
                // cargo-ndk refuses to run unless it detects it was launched
                // via cargo (it checks the CARGO env var); wasm-pack and
                // wasm-bindgen ignore this, so setting it unconditionally
                // keeps this verify step tool-agnostic.
                environment("CARGO", "cargo")
                commandLine(binary.get().asFile, "--version")
            }.standardOutput.asText.get().trim()
            // Extract the first whitespace-delimited token after the tool name
            // and compare it as an exact semver, not a substring. `contains`
            // would accept 4.1.20 when expecting 4.1.2.
            val actualVersion = output.split(Regex("\\s+"))
                .firstOrNull { it.matches(Regex("\\d+\\.\\d+\\.\\d+(-\\S+)?")) }
            check(actualVersion == version) {
                "Expected $crateName $version at ${binary.get().asFile}, got: $output"
            }
        }
    }
    return install to verify
}

val (_, verifyCargoNdk) = registerCargoTool("CargoNdk", "cargo-ndk", "cargo-ndk")
val (_, verifyWasmPack) = registerCargoTool("WasmPack", "wasm-pack", "wasm-pack")
val (_, verifyWasmBindgen) = registerCargoTool(
    "WasmBindgenCli",
    "wasm-bindgen-cli",
    "wasm-bindgen",
)

val rustWasmBindgenConsistency = tasks.register("rustWasmBindgenConsistency") {
    group = "rust"
    dependsOn(verifyWasmBindgen)
    inputs.file(rustDir.file("Cargo.lock"))
    inputs.property("cliVersion", pinnedToolVersion("wasm-bindgen-cli"))
    doLast {
        // Parse the resolved wasm-bindgen version from Cargo.lock and verify
        // it exactly matches the wasm-bindgen-cli pin. The spec requires the
        // CLI and runtime to be in lock-step.
        val lock = rustDir.file("Cargo.lock").asFile.readText()
        val lockVersion = Regex(
            "(?ms)^name = \"wasm-bindgen\"$.*?^version = \"([^\"]+)\"$",
        ).find(lock)?.groupValues?.get(1)
        val cliVersion = pinnedToolVersion("wasm-bindgen-cli")
        check(lockVersion == cliVersion) {
            "wasm-bindgen ($lockVersion in Cargo.lock) must match " +
                "wasm-bindgen-cli ($cliVersion in toolchain-tools.toml)."
        }
    }
}

val rustInstall = tasks.register("rustInstall") {
    group = "rust"
    description = "Installs and verifies all exact-pinned Rust build CLIs."
    dependsOn(verifyCargoNdk, verifyWasmPack, rustWasmBindgenConsistency)
}

val rustWasmLockCheck = tasks.register<Exec>("rustWasmLockCheck") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    // wasm-pack 0.15 runs `cargo metadata` (unlocked) before forwarding --locked
    // to cargo build. Preflight with --locked here so lock drift fails before
    // wasm-pack can touch anything.
    commandLine("cargo", "metadata", "--locked", "--no-deps", "--manifest-path", "Cargo.toml")
}

val rustWasm = tasks.register<Exec>("rustWasm") {
    group = "rust"
    description = "Builds the locked web-target WASM package."
    dependsOn(verifyWasmPack, verifyWasmBindgen, rustWasmBindgenConsistency, rustWasmLockCheck)
    inputs.files(
        fileTree(rustCrateDir) { include("Cargo.toml", "src/**/*.rs") },
        rustDir.file("Cargo.toml"),
        rustDir.file("Cargo.lock"),
        rustDir.file("rust-toolchain.toml"),
        rustToolsFile,
    )
    outputs.dir(wasmPackageDir)
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    environment("CARGO_PROFILE_RELEASE_PANIC", "abort")
    doFirst {
        delete(wasmPackageDir)
        val wasmPackBin = rustToolsDir.get().file(
            "wasm-pack/bin/wasm-pack$windowsExecutableSuffix",
        ).asFile
        val bindgenBinDir = rustToolsDir.get().dir("wasm-bindgen-cli/bin").asFile
        environment(
            "PATH",
            wasmPackBin.parentFile.prependToPath(
                bindgenBinDir.prependToPath(System.getenv("PATH")),
            ),
        )
        executable(wasmPackBin)
        setArgs(
            listOf(
                "build", "veles-crypto", "--target", "web", "--release",
                "--mode", "no-install", "--no-opt",
                "--out-dir", wasmPackageDir.asFile.absolutePath,
                "--", "--locked",
            ),
        )
    }
}

val rustNativeTest = tasks.register<Exec>("rustNativeTest") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    commandLine("cargo", "test", "--workspace", "--locked")
}

val rustWasmSmoke = tasks.register<Exec>("rustWasmSmoke") {
    group = "rust"
    dependsOn(rustWasm)
    inputs.file(rustCrateDir.file("tests/wasm-smoke.mjs"))
    inputs.dir(wasmPackageDir)
    doFirst {
        requireOnPath(
            "node",
            "Node >=22.0.0",
            "Install Node.js >=22.0.0 (declared in web-extension/package.json engines.node)",
        )
        val version = providers.exec {
            commandLine("node", "--version")
        }.standardOutput.asText.get().trim()
        val major = version.removePrefix("v").substringBefore('.').toIntOrNull()
        check(major != null && major >= 22) {
            "Node >=22 is required by web-extension/package.json; found '$version'"
        }
    }
    commandLine("node", rustCrateDir.file("tests/wasm-smoke.mjs").asFile)
}

val rustTest = tasks.register("rustTest") {
    group = "rust"
    description = "Runs native Rust and generated web-target WASM tests."
    dependsOn(rustNativeTest, rustWasmSmoke)
}

val appNdkDirectory = objects.directoryProperty()
project(":app").plugins.withId("com.android.application") {
    val androidComponents = project(":app").extensions
        .getByType(ApplicationAndroidComponentsExtension::class.java)
    appNdkDirectory.set(androidComponents.sdkComponents.ndkDirectory)
}

val rustJniOutput = project(":app").layout.buildDirectory.dir("generated/jniLibs")
val rustJni = tasks.register<Exec>("rustJni") {
    group = "rust"
    description = "Builds locked release JNI libraries for the approved Android ABIs."
    dependsOn(verifyCargoNdk)
    inputs.files(
        fileTree(rustCrateDir) { include("Cargo.toml", "src/**/*.rs") },
        rustDir.file("Cargo.toml"),
        rustDir.file("Cargo.lock"),
        rustDir.file("rust-toolchain.toml"),
        rustToolsFile,
    )
    inputs.property("ndkVersion", libs.versions.ndk.get())
    outputs.dir(rustJniOutput)
    workingDir(rustDir)
    doFirst {
        val ndkDir = appNdkDirectory.get().asFile
        val expected = libs.versions.ndk.get()
        requireNdkVersion(ndkDir, expected)
        delete(rustJniOutput)
        val cargoNdkBinDir = rustToolsDir.get().dir("cargo-ndk/bin").asFile
        environment("PATH", cargoNdkBinDir.prependToPath(System.getenv("PATH")))
        environment("ANDROID_NDK_HOME", ndkDir)
        environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
        environment("CARGO_PROFILE_RELEASE_PANIC", "unwind")
    }
    commandLine(
        "cargo", "ndk", "--platform", "33",
        "--target", "arm64-v8a",
        "--target", "armeabi-v7a",
        "--target", "x86_64",
        "--output-dir", rustJniOutput.get().asFile,
        "build", "--workspace", "--release", "--locked",
    )
}

val rustFormat = tasks.register<Exec>("rustFormat") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    commandLine("cargo", "fmt", "--all", "--", "--check")
}

val rustLintHost = tasks.register<Exec>("rustLintHost") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    commandLine(
        "cargo", "clippy", "--workspace", "--all-targets", "--locked",
        "--", "-D", "warnings",
    )
}

val rustLintWasm = tasks.register<Exec>("rustLintWasm") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    environment("CARGO_PROFILE_RELEASE_PANIC", "abort")
    commandLine(
        "cargo", "clippy", "--workspace", "--target", "wasm32-unknown-unknown",
        "--locked", "--", "-D", "warnings",
    )
}

val rustLintAndroid = tasks.register<Exec>("rustLintAndroid") {
    group = "rust"
    dependsOn(verifyCargoNdk)
    workingDir(rustDir)
    doFirst {
        val ndkDir = appNdkDirectory.get().asFile
        val expected = libs.versions.ndk.get()
        requireNdkVersion(ndkDir, expected)
        val cargoNdkBinDir = rustToolsDir.get().dir("cargo-ndk/bin").asFile
        environment("PATH", cargoNdkBinDir.prependToPath(System.getenv("PATH")))
        environment("ANDROID_NDK_HOME", ndkDir)
        environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
        environment("CARGO_PROFILE_RELEASE_PANIC", "unwind")
    }
    commandLine(
        "cargo", "ndk", "--platform", "33", "--target", "x86_64",
        "clippy", "--workspace", "--all-targets", "--locked",
        "--", "-D", "warnings",
    )
}

val rustLint = tasks.register("rustLint") {
    group = "rust"
    description = "Runs denied-warning Clippy checks for host, WASM, and Android JNI."
    dependsOn(rustLintHost, rustLintWasm, rustLintAndroid)
}

tasks.register("rustNdkVersion") {
    group = "rust"
    description = "Prints the pinned Android NDK version for CI provisioning."
    doLast { println(libs.versions.ndk.get()) }
}

// ---------------------------------------------------------------------------
// OTP-01 sub-project 1c — native-bridge (Tauri 2.x headless Native Messaging host)
// ---------------------------------------------------------------------------
//
// Layout: native-bridge/ at repo root. npm manages the Tauri CLI and TS tooling;
// cargo manages the Rust bridge crate. Gradle wraps both for CI-safe entry
// points, mirroring the rust task group pattern.
//
// The bridge is headless (windows = []), exits on stdin EOF, and produces
// unsigned artifacts only. No BLE, no crypto, no signing.

val bridgeDir = layout.projectDirectory.dir("native-bridge")
val bridgeSrcTauriDir = bridgeDir.dir("src-tauri")
val bridgeBuildOutput = layout.buildDirectory.dir("native-bridge")

fun requireNpm(installHint: String) {
    val path = System.getenv("PATH") ?: ""
    val found = path.split(File.pathSeparator).any { dir ->
        File(dir, if (windowsExecutableSuffix.isNotEmpty()) "npm.cmd" else "npm").let { it.isFile && it.canExecute() }
    }
    check(found) {
        "Could not find 'npm' on PATH. $installHint."
    }
}

val bridgeNodeCheck = tasks.register("bridgeNodeCheck") {
    group = "native-bridge"
    doLast {
        requireNpm("Install Node.js >=22.0.0 (declared in native-bridge/package.json engines.node)")
        val version = providers.exec {
            commandLine("node", "--version")
        }.standardOutput.asText.get().trim()
        val major = version.removePrefix("v").substringBefore('.').toIntOrNull()
        check(major != null && major >= 22) {
            "Node >=22 is required by native-bridge/package.json; found '$version'"
        }
    }
}

val bridgeInstall = tasks.register<Exec>("bridgeInstall") {
    group = "native-bridge"
    description = "Installs exact-pinned npm dependencies for native-bridge/."
    dependsOn(bridgeNodeCheck)
    inputs.file(bridgeDir.file("package.json"))
    inputs.file(bridgeDir.file("package-lock.json"))
    outputs.dir(bridgeDir.dir("node_modules"))
    workingDir(bridgeDir)
    commandLine("npm", "ci")
}

val bridgeFormat = tasks.register<Exec>("bridgeFormat") {
    group = "native-bridge"
    description = "Checks Prettier formatting for native-bridge/."
    dependsOn(bridgeInstall)
    workingDir(bridgeDir)
    commandLine("npm", "run", "format:check")
}

val bridgeLint = tasks.register<Exec>("bridgeLint") {
    group = "native-bridge"
    description = "Runs ESLint for native-bridge/."
    dependsOn(bridgeInstall)
    workingDir(bridgeDir)
    commandLine("npm", "run", "lint")
}

val bridgeTypecheck = tasks.register<Exec>("bridgeTypecheck") {
    group = "native-bridge"
    description = "Runs TypeScript type-checking for native-bridge/."
    dependsOn(bridgeInstall)
    workingDir(bridgeDir)
    commandLine("npm", "run", "typecheck")
}

val bridgeNpmTest = tasks.register<Exec>("bridgeNpmTest") {
    group = "native-bridge"
    description = "Runs vitest source-level tests for native-bridge/."
    dependsOn(bridgeInstall)
    workingDir(bridgeDir)
    commandLine("npm", "test")
}

val bridgeRustToolchainCheck = tasks.register("bridgeRustToolchainCheck") {
    group = "native-bridge"
    inputs.file(bridgeSrcTauriDir.file("rust-toolchain.toml"))
    doLast {
        requireOnPath(
            "rustup",
            "Rust 1.98.0",
            "Install rustup and run `rustup show active-toolchain` in native-bridge/src-tauri/ to install the pinned toolchain",
        )
        requireOnPath(
            "cargo",
            "Rust 1.98.0",
            "Install rustup and run `rustup show active-toolchain` in native-bridge/src-tauri/ to install the pinned toolchain",
        )
        val rustc = providers.exec {
            workingDir(bridgeSrcTauriDir)
            commandLine("rustc", "--version")
        }.standardOutput.asText.get().trim()
        check(rustc.startsWith("rustc 1.98.0 ")) {
            "native-bridge/src-tauri/rust-toolchain.toml requires Rust 1.98.0; active toolchain is '$rustc'"
        }
    }
}

val bridgeRustFormat = tasks.register<Exec>("bridgeRustFormat") {
    group = "native-bridge"
    description = "Checks cargo fmt formatting for the native-bridge Rust crate."
    dependsOn(bridgeRustToolchainCheck)
    inputs.files(fileTree(bridgeSrcTauriDir) { include("src/**/*.rs", "tests/**/*.rs") })
    workingDir(bridgeSrcTauriDir)
    environment("CARGO_TARGET_DIR", layout.buildDirectory.dir("native-bridge/rust-target").get().asFile)
    commandLine("cargo", "fmt", "--all", "--", "--check")
}

val bridgeRustLint = tasks.register<Exec>("bridgeRustLint") {
    group = "native-bridge"
    description = "Runs cargo clippy with -D warnings for the native-bridge Rust crate."
    dependsOn(bridgeRustToolchainCheck)
    inputs.files(
        fileTree(bridgeSrcTauriDir) { include("Cargo.toml", "Cargo.lock", "src/**/*.rs", "tests/**/*.rs", "build.rs") },
    )
    workingDir(bridgeSrcTauriDir)
    environment("CARGO_TARGET_DIR", layout.buildDirectory.dir("native-bridge/rust-target").get().asFile)
    commandLine("cargo", "clippy", "--all-targets", "--locked", "--", "-D", "warnings")
}

val bridgeRustTest = tasks.register<Exec>("bridgeRustTest") {
    group = "native-bridge"
    description = "Runs cargo test --locked for the native-bridge Rust crate."
    dependsOn(bridgeRustToolchainCheck)
    inputs.files(
        fileTree(bridgeSrcTauriDir) { include("Cargo.toml", "Cargo.lock", "src/**/*.rs", "tests/**/*.rs") },
    )
    workingDir(bridgeSrcTauriDir)
    environment("CARGO_TARGET_DIR", layout.buildDirectory.dir("native-bridge/rust-target").get().asFile)
    commandLine("cargo", "test", "--locked")
}

val bridgeTest = tasks.register("bridgeTest") {
    group = "native-bridge"
    description = "Runs npm and cargo tests for native-bridge/."
    dependsOn(bridgeNpmTest, bridgeRustFormat, bridgeRustLint, bridgeRustTest)
}

val bridgeBuild = tasks.register<Exec>("bridgeBuild") {
    group = "native-bridge"
    description = "Builds the unsigned native-bridge host (tauri build --no-bundle -- --locked --features tauri-runtime)."
    dependsOn(bridgeInstall, bridgeRustToolchainCheck)
    inputs.files(
        fileTree(bridgeSrcTauriDir) { include("Cargo.toml", "Cargo.lock", "src/**/*.rs", "build.rs") },
        bridgeSrcTauriDir.file("tauri.conf.json"),
        bridgeDir.file("index.html"),
        bridgeDir.file("package.json"),
        bridgeDir.file("package-lock.json"),
    )
    outputs.dir(bridgeSrcTauriDir.dir("target"))
    workingDir(bridgeDir)
    doFirst {
        // Tauri signing env vars are documented as absent; hard-code unsigned.
        environment("TAURI_SIGNING_PRIVATE_KEY", "")
        environment("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", "")
    }
    commandLine("npm", "run", "build")
}

val bridgeManifests = tasks.register<Exec>("bridgeManifests") {
    group = "native-bridge"
    description = "Emits Chrome native-messaging host manifests for each OS."
    dependsOn(bridgeInstall)
    inputs.file(bridgeDir.file("src/manifest.mjs"))
    inputs.file(bridgeDir.file("scripts/manifests.mjs"))
    outputs.dir(bridgeBuildOutput.map { it.dir("manifests") })
    workingDir(bridgeDir)
    commandLine("node", "scripts/manifests.mjs")
}

val bridgePackage = tasks.register<Exec>("bridgePackage") {
    group = "native-bridge"
    description = "Packages the native-bridge deterministically (platform-specific)."
    dependsOn(bridgeBuild, bridgeManifests)
    inputs.file(bridgeDir.file("scripts/package.mjs"))
    inputs.file(bridgeDir.file("src/manifest.mjs"))
    inputs.file(bridgeDir.file("package.json"))
    outputs.dir(bridgeBuildOutput)
    workingDir(bridgeDir)
    doFirst {
        val osName = System.getProperty("os.name")
        when {
            osName.startsWith("Windows") -> environment("VELES_BRIDGE_PLATFORM", "windows")
            osName.startsWith("Mac") -> environment("VELES_BRIDGE_PLATFORM", "macos")
            else -> {
                throw GradleException(
                    "bridgePackage: unsupported host platform '$osName'. " +
                        "Use a Windows or macOS runner. Linux is not a target for native-bridge packaging.",
                )
            }
        }
    }
    commandLine("node", "scripts/package.mjs")
}

tasks.named("check") {
    dependsOn(
        rustFormat,
        rustLint,
        rustTest,
        bridgeFormat,
        bridgeLint,
        bridgeTypecheck,
        bridgeTest,
    )
}

tasks.named<Delete>("clean") {
    dependsOn(":app:clean")
    delete(
        layout.projectDirectory.dir("web-extension/dist"),
        wasmPackageDir,
        layout.projectDirectory.dir("native-bridge/src-tauri/target"),
        layout.projectDirectory.dir("native-bridge/dist"),
        layout.buildDirectory.dir("native-bridge"),
    )
}
