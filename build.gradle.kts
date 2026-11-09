// Top-level build file where you can add configuration options common to all sub-projects/modules.
import com.android.build.api.variant.ApplicationAndroidComponentsExtension
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest
import java.util.Properties
import org.gradle.api.tasks.Delete
import org.gradle.api.tasks.Exec
import org.gradle.api.tasks.Sync
import org.gradle.api.DefaultTask
import org.gradle.api.file.ArchiveOperations
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.file.FileSystemOperations
import org.gradle.api.provider.Property
import org.gradle.api.tasks.Input
import org.gradle.api.tasks.Internal
import org.gradle.api.tasks.OutputDirectory
import org.gradle.api.tasks.TaskAction
import org.gradle.process.ExecOperations
import javax.inject.Inject

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
val verifyCargoToolsFile = layout.projectDirectory.file("verify/cargo-tools.toml")
val verifyToolsDir = layout.buildDirectory.dir("verify-tools")
val wasmPackageDir = layout.projectDirectory.dir("web-extension/rust-wasm/pkg")
val windowsExecutableSuffix = if (System.getProperty("os.name").startsWith("Windows")) ".exe" else ""
// On Windows, Gradle's Exec task process launcher cannot exec the `npm` shell
// script directly — it needs `npm.cmd`. Node ships `node.exe` so plain `node`
// works, but `npm` does not. Resolved once at configuration time and reused by
// every bridge Exec task that invokes npm.
val npmCommand = if (windowsExecutableSuffix.isNotEmpty()) "npm.cmd" else "npm"
val protocolDir = layout.projectDirectory.dir("protocol")

fun pinnedToolVersion(name: String): String {
    val pattern = Regex("(?m)^${Regex.escape(name)}\\s*=\\s*\"([^\"]+)\"\\s*$")
    return requireNotNull(pattern.find(rustToolsFile.asFile.readText())) {
        "Missing '$name' pin in ${rustToolsFile.asFile}"
    }.groupValues[1]
}

fun pinnedVerificationToolVersion(name: String): String {
    val pattern = Regex("(?m)^${Regex.escape(name)}\\s*=\\s*\"([^\"]+)\"\\s*$")
    return requireNotNull(pattern.find(verifyCargoToolsFile.asFile.readText())) {
        "Missing '$name' pin in ${verifyCargoToolsFile.asFile}"
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

// rustc embeds the absolute source paths of panic locations (function, file,
// line) into release binaries whenever panic is not fully stripped
// (CARGO_PROFILE_RELEASE_PANIC=unwind keeps the panic machinery for JNI).
// Dependency crates come from ${CARGO_HOME}/registry/src/<registry-hash>/..., so
// two builds with different CARGO_HOME (CI runner /home/runner/.cargo vs the
// verification Docker image /opt/cargo) embed different byte strings, shifting
// every ELF section offset and producing a .so (and APK) that differs between
// builds of identical source. Remapping the registry prefix to a stable
// "/cargo-registry" path makes the embedded strings identical regardless of
// where cargo stores its registry (proven locally: two builds with different
// CARGO_HOME produce byte-identical .so with this flag). The workspace crate's
// own path is NOT embedded (no panic locations from the workspace crate carry
// an absolute path into the binary), so only the registry prefix needs
// remapping. RUSTFLAGS is set as a task environment (overriding any inherited
// RUSTFLAGS) so the remap is not optional: a verification-relevant build must
// not silently pick up an ambient RUSTFLAGS that omits it.
fun cargoRegistryRemapRustFlags(): String {
    val cargoHomeEnv = System.getenv("CARGO_HOME")
    val cargoHome: File = if (cargoHomeEnv.isNullOrBlank()) {
        File(System.getProperty("user.home"), ".cargo")
    } else {
        File(cargoHomeEnv)
    }
    val registrySrc = File(cargoHome, "registry/src").absolutePath
    return "--remap-path-prefix=$registrySrc=/cargo-registry/src"
}

// Enables the repo's Conventional Commits commit-msg hook (.githooks) for local clones.
// Never overrides a hooksPath the developer already configured; skipped on CI.
abstract class InstallGitHooksTask @Inject constructor(
    private val execOps: ExecOperations,
) : DefaultTask() {
    @get:Internal
    abstract val repoDir: DirectoryProperty

    @get:Input
    abstract val onCi: Property<Boolean>

    @TaskAction
    fun install() {
        if (onCi.get()) {
            logger.info("CI=true: skipping git hook installation")
            return
        }
        val dir = repoDir.get().asFile
        val out = ByteArrayOutputStream()
        val read = execOps.exec {
            workingDir = dir
            commandLine("git", "config", "--get", "core.hooksPath")
            standardOutput = out
            isIgnoreExitValue = true
        }
        val current = out.toString(Charsets.UTF_8).trim()
        when {
            read.exitValue != 0 || current.isEmpty() -> execOps.exec {
                workingDir = dir
                commandLine("git", "config", "core.hooksPath", ".githooks")
            }
            current == ".githooks" -> Unit
            else -> logger.warn(
                "git core.hooksPath is already set to '$current'; leaving it unchanged, " +
                    "so the Conventional Commits commit-msg hook in .githooks is not active.",
            )
        }
    }
}

tasks.register<InstallGitHooksTask>("installGitHooks") {
    group = "build setup"
    description = "Points git core.hooksPath at .githooks unless already configured (skipped on CI)."
    repoDir.set(layout.projectDirectory)
    onCi.set(providers.environmentVariable("CI").map { it == "true" }.orElse(false))
}

// ---- Release tooling: pinned git-cliff (scripts/release-tools.toml) and the scripts/ test suite ----
val releaseToolsFile = layout.projectDirectory.file("scripts/release-tools.toml")
val releaseToolsDir = layout.buildDirectory.dir("release-tools")

fun pinnedReleaseTool(name: String): String {
    val pattern = Regex("(?m)^${Regex.escape(name)}\\s*=\\s*\"([^\"]+)\"\\s*$")
    return requireNotNull(pattern.find(releaseToolsFile.asFile.readText())) {
        "Missing '$name' pin in ${releaseToolsFile.asFile}"
    }.groupValues[1]
}

val gitCliffTarget: String? = run {
    val os = System.getProperty("os.name").lowercase()
    val arch = when (System.getProperty("os.arch").lowercase()) {
        "amd64", "x86_64" -> "x86_64"
        "aarch64", "arm64" -> "aarch64"
        else -> null
    }
    when {
        arch == null -> null
        os.startsWith("linux") -> "$arch-unknown-linux-gnu"
        os.startsWith("mac") -> "$arch-apple-darwin"
        os.startsWith("windows") && arch == "x86_64" -> "x86_64-pc-windows-msvc"
        else -> null
    }
}

// Downloads the pinned git-cliff archive, verifies its SHA-256 BEFORE extracting anything, then
// extracts only the executable to build/release-tools/git-cliff/ and checks `--version`.
abstract class InstallGitCliffTask @Inject constructor(
    private val execOps: ExecOperations,
    private val archiveOps: ArchiveOperations,
    private val fsOps: FileSystemOperations,
) : DefaultTask() {
    @get:Input
    abstract val version: Property<String>

    @get:Input
    abstract val target: Property<String>

    @get:Input
    abstract val sha256: Property<String>

    @get:OutputDirectory
    abstract val installDir: DirectoryProperty

    @get:Internal
    abstract val downloadDir: DirectoryProperty

    @TaskAction
    fun install() {
        val windows = target.get().contains("windows")
        val extension = if (windows) "zip" else "tar.gz"
        val archiveName = "git-cliff-${version.get()}-${target.get()}.$extension"
        val url = "https://github.com/orhun/git-cliff/releases/download/v${version.get()}/$archiveName"
        val download = downloadDir.get().asFile.apply { mkdirs() }.resolve(archiveName)
        val client = java.net.http.HttpClient.newBuilder()
            .followRedirects(java.net.http.HttpClient.Redirect.NORMAL)
            .build()
        val response = client.send(
            java.net.http.HttpRequest.newBuilder(java.net.URI(url)).GET().build(),
            java.net.http.HttpResponse.BodyHandlers.ofFile(download.toPath()),
        )
        check(response.statusCode() == 200) { "Downloading $url failed with HTTP ${response.statusCode()}" }
        val actual = MessageDigest.getInstance("SHA-256").digest(download.readBytes())
            .joinToString("") { "%02x".format(it) }
        if (actual != sha256.get()) {
            download.delete()
            error("SHA-256 mismatch for $archiveName: expected ${sha256.get()}, got $actual")
        }
        val binaryName = if (windows) "git-cliff.exe" else "git-cliff"
        val out = installDir.get().asFile
        fsOps.delete { delete(out) }
        fsOps.copy {
            from(if (windows) archiveOps.zipTree(download) else archiveOps.tarTree(download)) {
                include("git-cliff-${version.get()}/$binaryName")
                eachFile { path = name }
            }
            includeEmptyDirs = false
            into(out)
        }
        val binary = out.resolve(binaryName)
        check(binary.isFile) { "$archiveName does not contain git-cliff-${version.get()}/$binaryName" }
        binary.setExecutable(true)
        val stdout = ByteArrayOutputStream()
        execOps.exec {
            commandLine(binary.absolutePath, "--version")
            standardOutput = stdout
        }
        val reported = stdout.toString(Charsets.UTF_8).trim()
        check(reported == "git-cliff ${version.get()}") {
            "Expected git-cliff ${version.get()} at $binary, got: $reported"
        }
    }
}

val installGitCliff = tasks.register<InstallGitCliffTask>("installGitCliff") {
    group = "build setup"
    description = "Installs the pinned, SHA-256-verified git-cliff into build/release-tools/git-cliff."
    val pinnedVersion = pinnedReleaseTool("git-cliff")
    version.set(pinnedVersion)
    target.set(
        providers.provider {
            requireNotNull(gitCliffTarget) {
                "No pinned git-cliff archive for ${System.getProperty("os.name")}/${System.getProperty("os.arch")}"
            }
        },
    )
    sha256.set(target.map { pinnedReleaseTool("git-cliff-$it") })
    installDir.set(releaseToolsDir.map { it.dir("git-cliff") })
    downloadDir.set(releaseToolsDir.map { it.dir("downloads") })
}

// Python tooling tests (scripts/tests: commitlint, component map, release planner/assets/readiness).
val scriptsTest = tasks.register<Exec>("scriptsTest") {
    group = "verification"
    description = "Runs the scripts/ unit tests (stdlib Python) against the pinned git-cliff."
    dependsOn(installGitCliff)
    workingDir(layout.projectDirectory)
    environment(
        "VELES_GIT_CLIFF",
        releaseToolsDir.get().file("git-cliff/git-cliff$windowsExecutableSuffix").asFile.absolutePath,
    )
    commandLine(
        if (windowsExecutableSuffix.isNotEmpty()) "python" else "python3",
        "-I", "-m", "unittest", "discover", "-s", "scripts/tests", "-v",
    )
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

fun registerVerificationCargoTool(
    taskStem: String,
    crateName: String,
    binaryName: String,
    versionArguments: List<String> = listOf("--version"),
): Pair<TaskProvider<Exec>, TaskProvider<Task>> {
    val version = pinnedVerificationToolVersion(crateName)
    val installRoot = verifyToolsDir.map { it.dir(crateName) }
    val binary = installRoot.map {
        it.file("bin/$binaryName$windowsExecutableSuffix")
    }
    val install = tasks.register<Exec>("install$taskStem") {
        group = "verification"
        description = "Installs $crateName $version into the verification tool directory."
        dependsOn("rustToolchainCheck")
        inputs.property("crateName", crateName)
        inputs.property("version", version)
        inputs.file(verifyCargoToolsFile)
        inputs.file(rustDir.file("rust-toolchain.toml"))
        outputs.dir(installRoot)
        workingDir(rustDir)
        environment(
            "CARGO_TARGET_DIR",
            layout.buildDirectory.dir("verify-tools/tool-install-target/$crateName").get().asFile,
        )
        doFirst { delete(installRoot) }
        commandLine(
            "cargo", "install", "--locked", "--version", version,
            "--root", installRoot.get().asFile, crateName,
        )
    }
    val verify = tasks.register("verify$taskStem") {
        group = "verification"
        description = "Verifies the isolated $crateName tool version."
        dependsOn(install)
        inputs.property("version", version)
        inputs.file(binary.map { it.asFile })
        doLast {
            val output = providers.exec {
                commandLine(binary.get().asFile, *versionArguments.toTypedArray())
            }.standardOutput.asText.get().trim()
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
val (_, verifyCargoCyclonedx) = registerVerificationCargoTool(
    "CargoCyclonedx",
    "cargo-cyclonedx",
    "cargo-cyclonedx",
    listOf("cyclonedx", "--version"),
)
val (_, verifyCargoDeny) = registerVerificationCargoTool("CargoDeny", "cargo-deny", "cargo-deny")

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
        // Registry-path remap for a byte-reproducible WASM binary (see
        // cargoRegistryRemapRustFlags): wasm-bindgen panic locations embed
        // ${CARGO_HOME}/registry/src/... into veles_crypto_bg.wasm, so builds
        // with different CARGO_HOME produce different bytes without it.
        environment("RUSTFLAGS", cargoRegistryRemapRustFlags())
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

val protocolNodeCheck = tasks.register("protocolNodeCheck") {
    group = "verification"
    doLast {
        requireNpm("Install Node.js >=22.0.0 to run the OTP-02 contract checks.")
        requireOnPath("python3", "Python 3.8+", "Install Python 3.8 or newer")
        val nodeVersion = providers.exec { commandLine("node", "--version") }.standardOutput.asText.get().trim()
        val pythonVersion = providers.exec { commandLine("python3", "--version") }.standardOutput.asText.get().trim()
        fun atLeast(version: String, major: Int, minor: Int): Boolean {
            val match = Regex("(\\d+)\\.(\\d+)").find(version) ?: return false
            val foundMajor = match.groupValues[1].toInt()
            val foundMinor = match.groupValues[2].toInt()
            return foundMajor > major || (foundMajor == major && foundMinor >= minor)
        }
        check(atLeast(nodeVersion, 22, 0)) { "Node >=22.0.0 is required; found '$nodeVersion'" }
        check(atLeast(pythonVersion, 3, 8)) { "Python >=3.8 is required; found '$pythonVersion'" }
    }
}

val protocolInstall = tasks.register<Exec>("protocolInstall") {
    group = "verification"
    description = "Installs exact-pinned, script-free npm dependencies for protocol checks."
    dependsOn(protocolNodeCheck)
    inputs.file(protocolDir.file("package.json"))
    inputs.file(protocolDir.file("package-lock.json"))
    outputs.dir(protocolDir.dir("node_modules"))
    workingDir(protocolDir)
    commandLine(npmCommand, "ci", "--ignore-scripts")
}

val protocolContractCheck = tasks.register<Exec>("protocolContractCheck") {
    group = "verification"
    description = "Runs OTP-02 Node contract checks with fresh crypto evidence."
    dependsOn(protocolInstall)
    inputs.files(
        fileTree(protocolDir.dir("checks")) { include("**/*.mjs") },
        fileTree(protocolDir.dir("tools")) { include("**/*.mjs", "**/*.py") },
        fileTree(protocolDir.dir("v1")) { include("**/*.json") },
        layout.projectDirectory.file("docs/protocol/veles-v1.md"),
    )
    // The command deletes and recreates evidence, so Gradle must never reuse it.
    outputs.upToDateWhen { false }
    workingDir(layout.projectDirectory)
    commandLine("node", "protocol/checks/check.mjs")
}

val protocolRustTest = tasks.register<Exec>("protocolRustTest") {
    group = "verification"
    description = "Runs the host-only Rust OTP-02 shared-fixture consumer."
    dependsOn(rustToolchainCheck)
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    commandLine("cargo", "test", "--locked", "--test", "protocol_fixtures")
}

val protocolWebInstall = tasks.register<Exec>("protocolWebInstall") {
    group = "verification"
    description = "Installs script-free dependencies for the web OTP-02 consumer."
    dependsOn(protocolNodeCheck)
    workingDir(layout.projectDirectory.dir("web-extension"))
    commandLine(npmCommand, "ci", "--ignore-scripts")
}

val protocolWebTest = tasks.register<Exec>("protocolWebTest") {
    group = "verification"
    description = "Runs the web extension OTP-02 shared-fixture consumer."
    dependsOn(protocolWebInstall)
    workingDir(layout.projectDirectory.dir("web-extension"))
    commandLine(npmCommand, "exec", "vitest", "run", "test/protocol-fixtures.test.ts")
}

val protocolFreshEvidenceCheck = tasks.register<Exec>("protocolFreshEvidenceCheck") {
    group = "verification"
    description = "Regenerates and independently checks fresh OTP-02 crypto evidence only."
    dependsOn(protocolInstall)
    inputs.files(protocolContractCheck.get().inputs.files)
    outputs.upToDateWhen { false }
    workingDir(layout.projectDirectory)
    commandLine("node", "protocol/checks/check.mjs", "--evidence-only")
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
    dependsOn(rustNativeTest, rustWasmSmoke, "rustWasmPairingTest")
}

tasks.register<Exec>("rustWasmPairingTest") {
    group = "verification"
    dependsOn(rustWasm)
    workingDir(layout.projectDirectory)
    commandLine("node", "rust/veles-crypto/tests/pairing-wasm.mjs")
}

for ((suffix, flag) in listOf("Entropy" to "--entropy-failure", "Expiry" to "--clock-tests",
    "MemoryBound" to "--memory-boundary", "ResponseErasure" to "--response-erasure")) {
    val test = tasks.register<Exec>("rustWasm${suffix}Test") {
        group = "verification"
        dependsOn(rustWasm)
        workingDir(layout.projectDirectory)
        commandLine("node", "rust/veles-crypto/tests/pairing-wasm.mjs", "web-extension/rust-wasm/pkg", flag)
    }
    rustTest.configure { dependsOn(test) }
}

val appNdkDirectory = objects.directoryProperty()
val conformanceWasmDir = layout.buildDirectory.dir("rust/conformance-wasm")
val rustWasmConformance = tasks.register<Exec>("rustWasmConformance") {
    group = "verification"
    description = "Builds a test-only fixed-scalar WASM package outside product outputs."
    dependsOn(verifyWasmPack, verifyWasmBindgen, rustWasmBindgenConsistency, rustWasmLockCheck)
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    environment("CARGO_PROFILE_RELEASE_PANIC", "abort")
    doFirst {
        val bin = rustToolsDir.get().file("wasm-pack/bin/wasm-pack$windowsExecutableSuffix").asFile
        val bindgen = rustToolsDir.get().dir("wasm-bindgen-cli/bin").asFile
        environment("PATH", bin.parentFile.prependToPath(bindgen.prependToPath(System.getenv("PATH"))))
        executable(bin)
        setArgs(listOf("build", "veles-crypto", "--target", "web", "--release", "--mode", "no-install", "--no-opt",
            "--out-dir", conformanceWasmDir.get().asFile.absolutePath, "--", "--locked", "--features", "conformance"))
    }
}

tasks.register<Exec>("rustWasmConformanceTest") {
    group = "verification"
    dependsOn(rustWasmConformance)
    workingDir(layout.projectDirectory)
    commandLine("node", "rust/veles-crypto/tests/pairing-wasm.mjs", conformanceWasmDir.get().asFile, "--conformance")
}

// Separate output and target directories keep test-only native exports out of
// the app's generated/jniLibs directory, release APK and rustPackage.
tasks.register<Exec>("rustJniConformance") {
    group = "verification"
    dependsOn(verifyCargoNdk)
    workingDir(rustDir)
    doFirst {
        val ndk = appNdkDirectory.get().asFile
        requireNdkVersion(ndk, libs.versions.ndk.get())
        environment("ANDROID_NDK_HOME", ndk)
        environment("PATH", rustToolsDir.get().dir("cargo-ndk/bin").asFile.prependToPath(System.getenv("PATH")))
        environment("CARGO_TARGET_DIR", layout.buildDirectory.dir("rust/conformance-target").get().asFile)
        environment("CARGO_PROFILE_RELEASE_PANIC", "unwind")
    }
    commandLine("cargo", "ndk", "--platform", "33", "--target", "arm64-v8a", "--target", "x86_64",
        "--output-dir", layout.buildDirectory.dir("rust/conformance-jni").get().asFile,
        "build", "--release", "--locked", "--features", "conformance")
}

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
        // Registry-path remap for byte-reproducible JNI libraries (see
        // cargoRegistryRemapRustFlags): without it, CI runner vs Docker
        // rebuilds embed different CARGO_HOME paths and the APK compare fails.
        environment("RUSTFLAGS", cargoRegistryRemapRustFlags())
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

val rustPackageDir = layout.buildDirectory.dir("rust-package")
val rustJniPaths = listOf(
    "arm64-v8a/libveles_crypto.so",
    "armeabi-v7a/libveles_crypto.so",
    "x86_64/libveles_crypto.so",
)

val rustPackage = tasks.register<Sync>("rustPackage") {
    group = "rust"
    description = "Stages the JNI and WASM artifacts as the rust-jni-wasm package."
    dependsOn(rustJni, rustWasm)
    from(rustJniOutput) {
        include(rustJniPaths)
        into("jni")
    }
    from(wasmPackageDir) { into("wasm") }
    into(rustPackageDir)
    doFirst {
        val actual = rustJniOutput.get().asFile.walkTopDown()
            .filter { it.isFile }
            .map { it.relativeTo(rustJniOutput.get().asFile).invariantSeparatorsPath }
            .sorted()
            .toList()
        check(actual == rustJniPaths.sorted()) {
            "Unexpected JNI artifact paths: $actual"
        }
    }
    doLast {
        val root = rustPackageDir.get().asFile
        // Gradle's Sync excludes dotfiles, but wasm-pack emits .gitignore as
        // part of the package contract, so copy it before recording checksums.
        wasmPackageDir.file(".gitignore").asFile.copyTo(root.resolve("wasm/.gitignore"), overwrite = true)
        val paths = root.walkTopDown()
            .filter { it.isFile && it.name != "SHA256SUMS" }
            .map { it.relativeTo(root).invariantSeparatorsPath }
            .sorted()
            .toList()
        check(paths.filter { it.startsWith("jni/") } == rustJniPaths.map { "jni/$it" }.sorted()) {
            "Rust package JNI paths are invalid: $paths"
        }
        check(paths.any { it.startsWith("wasm/") }) { "Rust package has no WASM files" }
        val sums = paths.joinToString(separator = "\n", postfix = "\n") { path ->
            val digest = MessageDigest.getInstance("SHA-256")
                .digest(root.resolve(path).readBytes())
                .joinToString("") { byte -> "%02x".format(byte) }
            "$digest  $path"
        }
        root.resolve("SHA256SUMS").writeText(sums)
    }
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
    commandLine(npmCommand, "ci", "--ignore-scripts")
}

val protocolBridgeTest = tasks.register<Exec>("protocolBridgeTest") {
    group = "verification"
    description = "Runs the native-bridge OTP-02 shared-fixture consumer without Tauri."
    dependsOn(bridgeInstall)
    workingDir(bridgeDir)
    commandLine(npmCommand, "exec", "vitest", "run", "test/protocol-fixtures.test.ts")
}

val bridgeFormat = tasks.register<Exec>("bridgeFormat") {
    group = "native-bridge"
    description = "Checks Prettier formatting for native-bridge/."
    dependsOn(bridgeInstall)
    workingDir(bridgeDir)
    commandLine(npmCommand, "run", "format:check")
}

val bridgeLint = tasks.register<Exec>("bridgeLint") {
    group = "native-bridge"
    description = "Runs ESLint for native-bridge/."
    dependsOn(bridgeInstall)
    workingDir(bridgeDir)
    commandLine(npmCommand, "run", "lint")
}

val bridgeTypecheck = tasks.register<Exec>("bridgeTypecheck") {
    group = "native-bridge"
    description = "Runs TypeScript type-checking for native-bridge/."
    dependsOn(bridgeInstall)
    workingDir(bridgeDir)
    commandLine(npmCommand, "run", "typecheck")
}

val bridgeNpmTest = tasks.register<Exec>("bridgeNpmTest") {
    group = "native-bridge"
    description = "Runs vitest source-level tests for native-bridge/."
    dependsOn(bridgeInstall)
    workingDir(bridgeDir)
    commandLine(npmCommand, "test")
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

// OTP-01 sub-project 1c — build-orchestration lifecycle (review findings 4 & 5).
//
// The bridge build chain is a single serialized line, not a diamond:
//   bridgeBuild (raw host binary) → bridgeBundle (installer artifacts) →
//   bridgePackage (deterministic archive)
//
// cargo owns `src-tauri/target/` as its own incremental cache; no Gradle task
// declares the bare `target` tree as an output (that made bridgeBuild and
// bridgeBundle sibling producers of the same dir, racing over `target/` when
// bridgePackage depended on both). Each task instead declares only the narrow
// artifact it produces, and bridgeBundle dependsOn(bridgeBuild) so the raw
// build runs first and the bundle build runs after — never in parallel over
// the shared target dir. bridgePackage reaches bridgeBuild only transitively
// through bridgeBundle.
//
// The bundle output is replaced before each bundle build and the archive
// output before each package run, so stale installer artifacts / archives from
// a previous run cannot survive into publication.
//
// Every signing and notarization credential/identity env var the Tauri/macOS
// toolchain respects is removed for both builds, not just the
// Tauri updater key — an inherited `APPLE_SIGNING_IDENTITY` / `APPLE_CERTIFICATE`
// / `APPLE_*` notarization credential could otherwise sign or notarize the
// artifacts. Output is unsigned by construction, on every host.
val bridgeHostBinary = bridgeSrcTauriDir.dir("target/release")
    .file("veles-native-bridge$windowsExecutableSuffix")
val bridgeBundleOutput = bridgeSrcTauriDir.dir("target/release/bundle")
val bridgeWindowsPackageOutput = bridgeBuildOutput.map { it.dir("windows") }
val bridgeMacosPackageOutput = bridgeBuildOutput.map { it.dir("macos") }

// Strip every env var that could sign or notarize Tauri/macOS artifacts.
// Clearing only TAURI_SIGNING_PRIVATE_KEY* is insufficient: an inherited macOS
// code-signing identity or notarization credential would still sign. Remove
// the inherited variables entirely: Tauri treats an empty credential as a
// configured credential and attempts to use it.
fun stripSigningEnv(exec: Exec) {
    listOf(
        "TAURI_SIGNING_PRIVATE_KEY",
        "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
        "APPLE_SIGNING_IDENTITY",
        "APPLE_CERTIFICATE",
        "APPLE_CERTIFICATE_PASSWORD",
        "APPLE_ID",
        "APPLE_PASSWORD",
        "APPLE_TEAM_ID",
        "APPLE_PROVIDER_SHORT_NAME",
        "APPLE_API_KEY",
        "APPLE_API_ISSUER",
        "APPLE_API_KEY_PATH",
    ).forEach { key -> exec.environment.remove(key) }
}

val bridgeBuild = tasks.register<Exec>("bridgeBuild") {
    group = "native-bridge"
    description = "Builds the unsigned native-bridge host (tauri build --no-bundle -- --locked --features tauri-runtime)."
    dependsOn(bridgeInstall, bridgeRustToolchainCheck)
    inputs.files(
        fileTree(bridgeSrcTauriDir) { include("Cargo.toml", "Cargo.lock", "src/**/*.rs", "build.rs") },
        bridgeSrcTauriDir.file("tauri.conf.json"),
        bridgeSrcTauriDir.file("icons/veles-native-bridge.ico"),
        bridgeDir.file("index.html"),
        bridgeDir.file("package.json"),
        bridgeDir.file("package-lock.json"),
    )
    // Narrow output: only the raw host binary the --no-bundle build emits
    // (veles-native-bridge[.exe]). The shared cargo `target/` tree is owned by
    // cargo, not declared as a Gradle output here.
    outputs.file(bridgeHostBinary)
    workingDir(bridgeDir)
    doFirst {
        // Hard-code unsigned output: no updater signature, no macOS codesign,
        // no notarization, even if the calling environment inherited them.
        stripSigningEnv(this as Exec)
        val osName = System.getProperty("os.name")
        if (osName.startsWith("Windows")) {
            // MSVC otherwise writes wall-clock PE/debug timestamps and a random
            // PDB GUID. /Brepro derives both from link inputs instead.
            environment("RUSTFLAGS", "-C link-arg=/Brepro")
        }
    }
    commandLine(npmCommand, "run", "build")
}

// OTP-01 sub-project 1c — separate unsigned Tauri *bundle* build.
//
// `bridgeBuild` above only runs `tauri build --no-bundle`, which emits the raw
// host binary but no installer artifacts. The deterministic archive script
// (scripts/package.mjs) needs real unsigned installer inputs — WiX (.msi) and
// NSIS (.exe) on Windows, the `.app` bundle plus a `.dmg` on macOS — so this
// task runs the locked bundle build and is a prerequisite of `bridgePackage`.
//
// This task serializes behind `bridgeBuild` (dependsOn it): the raw host build
// runs first, then the bundle build runs after. They are never sibling
// producers of the same `src-tauri/target` tree.
//
// Platform-specific Tauri `--bundles` targets. Windows gets WiX (.msi) plus
// NSIS (.exe) installer inputs; macOS gets the .app bundle plus a .dmg
// installer. Returns null on unsupported hosts so the failure is raised at
// *execution* time (in the task's doFirst) rather than at configuration time
// — otherwise merely configuring this task on Linux would break unrelated
// Gradle invocations such as `./gradlew tasks`, `bridgeFormat`, or `check`.
fun bridgeBundleTargetsFor(osName: String): String? = when {
    osName.startsWith("Windows") -> "nsis,msi"
    osName.startsWith("Mac") -> "app,dmg"
    else -> null
}

val bridgeBundle = tasks.register<Exec>("bridgeBundle") {
    group = "native-bridge"
    description = "Builds unsigned Tauri installer bundles (WiX/NSIS on Windows, .app/.dmg on macOS) for packaging inputs."
    // Serialize behind the raw host build so bundle and build are never sibling
    // producers of the same `src-tauri/target` tree; bundle runs after build.
    dependsOn(bridgeBuild, bridgeInstall, bridgeRustToolchainCheck)
    inputs.files(
        fileTree(bridgeSrcTauriDir) { include("Cargo.toml", "Cargo.lock", "src/**/*.rs", "build.rs") },
        bridgeSrcTauriDir.file("tauri.conf.json"),
        bridgeSrcTauriDir.file("icons/veles-native-bridge.ico"),
        bridgeDir.file("index.html"),
        bridgeDir.file("package.json"),
        bridgeDir.file("package-lock.json"),
    )
    // Narrow output: only the installer artifacts Tauri emits under
    // `target/release/bundle/`. The shared cargo `target/` tree is owned by
    // cargo, not declared as a Gradle output here.
    outputs.dir(bridgeBundleOutput)
    workingDir(bridgeDir)
    // The command line is finalized in doFirst so the platform check runs at
    // execution time (fails fast on Linux) without breaking task configuration
    // for unrelated Gradle invocations.
    doFirst {
        // Replace any prior bundle output before building so stale installer
        // artifacts from a previous run cannot survive into the archive.
        delete(bridgeBundleOutput)
        // Hard-code unsigned output: no updater signature, no macOS codesign,
        // no notarization, even if the calling environment inherited them.
        stripSigningEnv(this as Exec)
        // Tauri's DMG bundler checks specifically for CI=true before enabling
        // its headless --skip-jenkins path.
        environment("CI", "true")
        // Tauri 2.6.0 locates WiX under <cargo target dir>/.tauri/WixTools314
        // and NSIS under <cargo target dir>/.tauri/NSIS when
        // bundle.useLocalToolsDir is true (see native-bridge/src-tauri/
        // tauri.conf.json). The toolset must be provisioned into
        // native-bridge/src-tauri/target/.tauri/ before bridgePackage runs.
        // TAURI_WIX_PATH / TAURI_NSIS_PATH env vars
        // are NOT read by Tauri 2.6.0 (the NSIS bundler reads NSIS_PATH, not
        // TAURI_NSIS_PATH; WiX has no env override). Forward NSIS_PATH from
        // the provisioned cache as a belt-and-suspenders override so a stale
        // user NSIS install can never be selected even if the local-tools
        // directory resolution were somehow bypassed.
        listOf("NSIS_PATH").forEach { key ->
            System.getenv(key)?.takeIf(String::isNotBlank)?.let { environment(key, it) }
        }

        val osName = System.getProperty("os.name")
        if (osName.startsWith("Windows")) {
            // Keep the bundle's Cargo link invocation consistent with bridgeBuild.
            environment("RUSTFLAGS", "-C link-arg=/Brepro")
        }
        val targets = bridgeBundleTargetsFor(osName)
            ?: throw GradleException(
                "bridgeBundle: unsupported host platform '$osName'. " +
                    "Tauri bundle targets are Windows (nsis,msi) or macOS (app,dmg) only; " +
                    "Linux is not a target for native-bridge packaging. " +
                    "Use a Windows or macOS runner.",
            )
        // `npm run bundle -- <args>` appends <args> to the `bundle` script
        // (`tauri build --ci`). The first `--` below is npm's separator; the
        // second `--` is the tauri→cargo separator consumed by the appended
        // command. Resulting invocation:
        //   tauri build --ci --bundles <targets> -- --locked --features tauri-runtime
        commandLine(
            npmCommand,
            "run",
            "bundle",
            "--",
            "--bundles",
            targets,
            "--",
            "--locked",
            "--features",
            "tauri-runtime",
        )
    }
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
    // bridgeBuild is reached transitively through bridgeBundle (which dependsOn
    // it), keeping a single serialized build → bundle → package line instead
    // of a diamond with two producers of `src-tauri/target`.
    dependsOn(bridgeBundle)
    inputs.file(bridgeDir.file("scripts/package.mjs"))
    inputs.file(bridgeDir.file("src/manifest.mjs"))
    inputs.file(bridgeDir.file("package.json"))
    inputs.file(bridgeHostBinary)
    // Installer inputs produced by bridgeBundle (WiX/NSIS on Windows,
    // .app/.dmg on macOS) live under src-tauri/target/release/bundle/.
    inputs.dir(bridgeBundleOutput)
    outputs.dir(bridgeWindowsPackageOutput)
    outputs.dir(bridgeMacosPackageOutput)
    workingDir(bridgeDir)
    doFirst {
        // Replace any prior archive output before packaging so stale archives
        // from a previous run cannot survive into publication.
        delete(bridgeWindowsPackageOutput, bridgeMacosPackageOutput)
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

// ---- OTP-24 production host (begin) ----
// OTP-24 production Native Messaging host (`native-bridge/host`, crate veles-ble-host).
//
// This is NOT the Tauri scaffold above. It is a plain Rust executable (`veles-native-host`)
// built by a locked cargo invocation with the host's own pinned 1.98.0 toolchain
// (native-bridge/host/rust-toolchain.toml). Nothing here uses Node, npm or Tauri, and the
// graph is deliberately disjoint from bridgeInstall/bridgeBuild/bridgeBundle/bridgePackage:
// the old scaffold installers keep their `native-windows`/`native-macos` ownership and are not
// relabeled. Every product lands under build/ (never in the source tree):
//   build/native-bridge/host-target/        cargo target dir (CARGO_TARGET_DIR)
//   build/native-bridge/host/               veles-native-host[.exe] + SHA256SUMS
// The test-driver binary is gated behind the non-default `test-driver` feature and is never
// built here: bridgeHostBuild names `--bin veles-native-host` and enables no feature.
val productionHostDir = layout.projectDirectory.dir("native-bridge/host")
val productionHostTargetDir = layout.buildDirectory.dir("native-bridge/host-target")
val productionHostOutputDir = layout.buildDirectory.dir("native-bridge/host")
val productionHostExecutable = "veles-native-host$windowsExecutableSuffix"

fun Exec.productionHostEnvironment() {
    workingDir(productionHostDir)
    environment("CARGO_TARGET_DIR", productionHostTargetDir.get().asFile)
    // The shipped host is self-contained for the same reason as the installer binaries:
    // a clean Windows has no vcruntime140.dll.
    if (System.getProperty("os.name").startsWith("Windows")) {
        environment("RUSTFLAGS", "-C target-feature=+crt-static")
    }
    // The origin allowlist is compiled in through option_env!("VELES_ALLOWED_ORIGINS"), so the variable is a
    // real build input: changing only it must re-run cargo instead of leaving a stale staged binary.
    inputs.property("VELES_ALLOWED_ORIGINS", providers.environmentVariable("VELES_ALLOWED_ORIGINS").orElse(""))
}

val bridgeHostSources = productionHostDir.let { host ->
    fileTree(host) {
        include("Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "rustfmt.toml", "src/**", "tests/**", "examples/**", "vendor/**")
    }
}

val bridgeHostToolchainCheck = tasks.register("bridgeHostToolchainCheck") {
    group = "native-bridge"
    description = "Asserts the native-bridge/host crate resolves to its pinned Rust 1.98.0 toolchain."
    inputs.file(productionHostDir.file("rust-toolchain.toml"))
    doLast {
        val hint = "Install rustup and run `rustup show active-toolchain` in native-bridge/host/ to install the pinned toolchain"
        requireOnPath("rustup", "Rust 1.98.0", hint)
        requireOnPath("cargo", "Rust 1.98.0", hint)
        val rustc = providers.exec {
            workingDir(productionHostDir)
            commandLine("rustc", "--version")
        }.standardOutput.asText.get().trim()
        check(rustc.startsWith("rustc 1.98.0 ")) {
            "native-bridge/host/rust-toolchain.toml requires Rust 1.98.0; active toolchain is '$rustc'"
        }
    }
}

val bridgeHostFormat = tasks.register<Exec>("bridgeHostFormat") {
    group = "native-bridge"
    description = "Checks cargo fmt formatting for the production host crate."
    dependsOn(bridgeHostToolchainCheck)
    inputs.files(bridgeHostSources)
    productionHostEnvironment()
    commandLine("cargo", "fmt", "--all", "--", "--check")
}

val bridgeHostLint = tasks.register<Exec>("bridgeHostLint") {
    group = "native-bridge"
    description = "Runs locked cargo clippy with -D warnings for the production host crate."
    dependsOn(bridgeHostToolchainCheck)
    inputs.files(bridgeHostSources)
    productionHostEnvironment()
    commandLine("cargo", "clippy", "--all-targets", "--locked", "--", "-D", "warnings")
}

val bridgeHostTest = tasks.register<Exec>("bridgeHostTest") {
    group = "native-bridge"
    description = "Runs locked cargo test for the production host crate (portable plus current-OS adapter tests)."
    dependsOn(bridgeHostToolchainCheck)
    inputs.files(bridgeHostSources)
    productionHostEnvironment()
    commandLine("cargo", "test", "--locked")
}

val bridgeHostBuild = tasks.register<Exec>("bridgeHostBuild") {
    group = "native-bridge"
    description = "Builds only veles-native-host (locked, release, no features) and writes build/native-bridge/host/{exe,SHA256SUMS}."
    dependsOn(bridgeHostToolchainCheck)
    inputs.files(bridgeHostSources)
    outputs.dir(productionHostOutputDir)
    productionHostEnvironment()
    doFirst {
        // A stale executable or checksum must never be mistaken for this build's product.
        delete(productionHostOutputDir)
    }
    commandLine("cargo", "build", "--locked", "--release", "--bin", "veles-native-host")
    doLast {
        val built = productionHostTargetDir.get().asFile.resolve("release/$productionHostExecutable")
        // Only the production executable is accepted: no fallback to the Tauri scaffold output.
        check(built.isFile) { "bridgeHostBuild: production executable missing at $built" }
        val outputDir = productionHostOutputDir.get().asFile
        outputDir.mkdirs()
        val staged = outputDir.resolve(productionHostExecutable)
        built.copyTo(staged, overwrite = true)
        staged.setExecutable(true, false)
        val digest = java.security.MessageDigest.getInstance("SHA-256").digest(staged.readBytes())
            .joinToString("") { "%02x".format(it) }
        outputDir.resolve("SHA256SUMS").writeText("$digest  $productionHostExecutable\n")
    }
}
// ---- OTP-24 production host (end) ----

// ---- OTP-25 installer producers (begin) ----
// OTP-25 standalone per-user installers (`native-bridge/installer`, crate veles-bridge-installer, the
// NSIS frontend in windows/ and the SwiftPM frontend in macos/).
//
// This graph builds the UNSIGNED installer input trees that Task 13's protected release workflow signs
// and gates. It is deliberately disjoint from the Tauri scaffold (bridgeInstall/bridgeBuild/bridgeBundle/
// bridgePackage) and from the OTP-24 host graph above, uses no Node, and never signs, notarizes or
// publishes anything. Every product lands under build/native-bridge/installer/:
//   target/<channel>-<slot>/{installer,installer-embedded,host,frontend}   cargo/swift outputs, one tree per channel and slot
//   slots/<channel>/<slot>/                              staged binaries and the assembled input trees
//   out/<channel>/                                       tree.tar, provenance.json, report-ab.json, SHA256SUMS
//   tools/nsis/                                          the pinned NSIS (provision-nsis.ps1)
//
// Carrier ordering: the assembler is the only producer of the policy carrier, and it needs the
// payload (host, cleanup helper, uninstaller) but not the engine, so each slot builds a bootstrap
// engine first, assembles, verifies the carrier against the exact descriptor and channel, and only
// then rebuilds the engine with `VELES_EMBEDDED_CARRIER` pointing at the assembler's verified
// `embedded-carrier.bin`. The published tree is re-assembled from that engine (`assembled-final`).
// A production build declares `VELES_BUILD_CHANNEL=production`, so `build.rs` fails closed if the
// carrier is ever absent; a fixture build embeds its own channel's carrier or none.
//
// Public tasks:
//   bridgeInstallerFormat / bridgeInstallerLint / bridgeInstallerTest   cargo fmt/clippy/test (locked)
//   bridgeInstallerScriptsTest                                          the Python script suites (not on Windows)
//   bridgeInstallerUnsigned          PRODUCTION channel input tree; requires a tracked approved release config
//   bridgeInstallerPackageFixture    FIXTURE channel input tree from the tracked fixture config
//   bridgeInstallerSupplyChain       the full supply-chain policy run (SBOMs, licences, build scripts, remote code)
// The channel is fixed by the registration below, never by the config or a property: a config of the
// other channel is refused, so a fixture config cannot produce a production artifact.
//
// Independent A/B: each producer builds the tree twice, as slot a and slot b, with SEPARATE explicit
// target directories that are deleted first (a warm cache is never reused) and with the checkout and
// cargo-registry paths remapped to fixed prefixes plus deterministic link settings. Two slots built on
// one runner show that the build is reproducible there; they are not a cross-machine claim, which the
// release workflow makes by comparing two separate producer runs.
val installerDir = layout.projectDirectory.dir("native-bridge/installer")
val installerBuildRoot = layout.buildDirectory.dir("native-bridge/installer")
val installerAssembler = installerDir.file("scripts/assemble_unsigned.py")
val installerReleaseGate = installerDir.file("scripts/release_gate.py")
val installerOsName = System.getProperty("os.name")
val installerPlatform = when {
    installerOsName.startsWith("Windows") -> "windows"
    installerOsName.startsWith("Mac") -> "macos"
    else -> "unsupported"
}
val installerRustTriple = if (installerPlatform == "windows") "x86_64-pc-windows-msvc" else "aarch64-apple-darwin"
val installerPython = if (installerPlatform == "windows") "python" else "python3"
val installerExecutableSuffix = if (installerPlatform == "windows") ".exe" else ""
// MAJOR.MINOR.PATCH, unsigned numeric components without leading zeros (the release-config rule).
val installerVersionPattern = Regex("""(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)""")
val installerSources = fileTree(installerDir) {
    include("Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "src/**", "tests/**", "config/**", "macos/**", "windows/**", "scripts/**")
    exclude("target/**", "macos/.build/**", "scripts/__pycache__/**")
}
// Tracked build inputs. The approved production config has no default content in this repository: until
// the owner approves one (and the production extension ID), bridgeInstallerUnsigned fails closed.
val installerReleaseConfig = providers.gradleProperty("installerReleaseConfig")
    .map { layout.projectDirectory.file(it) }
    .orElse(layout.projectDirectory.file("native-bridge/installer/config/release-$installerPlatform.json"))
val installerFixtureConfig = providers.provider { layout.projectDirectory.file("native-bridge/installer/config/fixture-$installerPlatform.json") }
// No carrier path is an operator input: the authenticated carrier is produced by the assembler in
// the same producer graph and compiled in only after `verify-carrier` accepts it, so a build can
// never embed a file that was not assembled from this commit's payload.
val installerMakensis = providers.gradleProperty("installerMakensis")
    .orElse(installerBuildRoot.map { it.file("tools/nsis/nsis-3.08/makensis.exe").asFile.absolutePath })
// Build-tool identity: observed on this machine, held to config/tools.lock.json, bound into provenance.
val installerToolTarget = if (installerPlatform == "windows") "windows-x86_64" else "macos-arm64"
val installerToolsScript = installerDir.file("scripts/tool_observation.py")
val installerToolObservation = installerBuildRoot.map { it.file("tools/observed-$installerToolTarget.json") }
// The macOS deployment target comes from the reviewed lock, never from a literal here.
val installerMinimumOs = providers.provider {
    @Suppress("UNCHECKED_CAST")
    val lock = groovy.json.JsonSlurper().parse(installerDir.file("config/tools.lock.json").asFile) as Map<String, Any?>
    @Suppress("UNCHECKED_CAST")
    (lock["targets"] as Map<String, Map<String, Any?>>).getValue("macos-arm64")["minimum_os"] as String
}
// The SDK is selected explicitly (the one the pinned Xcode reports), not whatever a tool defaults to.
val installerMacosSdkPath = providers.exec {
    commandLine("xcrun", "--sdk", "macosx", "--show-sdk-path")
}.standardOutput.asText.map { it.trim() }

fun requireInstallerHost() {
    check(installerPlatform != "unsupported") {
        "OTP-25 installer inputs are built on a Windows or macOS runner; Linux is not a producer target."
    }
}

// One installer build kind. The release kind is the two-slot A/B unsigned input tree Task 13 signs.
// The debug kind is the PR path: one slot, no --release, the compile-time fixture trust adapter, a
// debug Swift frontend, and a finished installer (setup.exe / DMG) packaged by release_gate.py.
// Nothing selects a kind at run time: the registration names it.
class InstallerBuildKind(
    val name: String,
    val slots: List<String>,
    val outputDirectory: String,
    val cargoProfileArguments: List<String>,
    val cargoBinaryDirectory: String,
    val cargoFeatures: List<String>,
    val swiftConfiguration: String,
)

val installerReleaseKind = InstallerBuildKind(
    name = "release",
    slots = listOf("a", "b"),
    outputDirectory = "",
    cargoProfileArguments = listOf("--release"),
    cargoBinaryDirectory = "release",
    cargoFeatures = emptyList(),
    swiftConfiguration = "release",
)

// The feature is compiled in only here; build.rs refuses it outside a debug profile or for the
// production channel.
val installerDebugKind = InstallerBuildKind(
    name = "debug",
    slots = listOf("a"),
    outputDirectory = "debug",
    cargoProfileArguments = emptyList(),
    cargoBinaryDirectory = "debug",
    cargoFeatures = listOf("--features", "debug-fixture-trust"),
    swiftConfiguration = "debug",
)

fun installerRustFlags(): String {
    val checkout = layout.projectDirectory.asFile.absolutePath
    check(checkout.none { it.isWhitespace() }) { "RUSTFLAGS cannot carry a checkout path containing whitespace: $checkout" }
    val cargoHome = System.getenv("CARGO_HOME")?.takeIf { it.isNotBlank() } ?: "${System.getProperty("user.home")}/.cargo"
    val flags = mutableListOf(
        "--remap-path-prefix=$checkout=/veles-src",
        cargoRegistryRemapRustFlags(),
        "--remap-path-prefix=$cargoHome/git/checkouts=/cargo-git",
    )
    if (installerPlatform == "windows") {
        // Self-contained binaries. A clean Windows has no vcruntime140.dll, and the
        // outside-root cleanup helper is staged into a temp directory by itself, so an
        // adjacent DLL copy would not follow it. The static CRT needs no redistributable.
        flags += listOf(
            "-C", "target-feature=+crt-static",
            "-C", "link-arg=/Brepro",
            "-C", "link-arg=/PDBALTPATH:veles.pdb",
        )
    }
    return flags.joinToString(" ")
}

fun installerSourceCommit(): String {
    val head = providers.exec {
        workingDir(layout.projectDirectory)
        commandLine("git", "rev-parse", "HEAD")
    }.standardOutput.asText.get().trim()
    val dirty = providers.exec {
        workingDir(layout.projectDirectory)
        commandLine("git", "status", "--porcelain", "--untracked-files=no")
    }.standardOutput.asText.get().trim()
    check(dirty.isEmpty()) { "Installer inputs are built from a clean commit; tracked changes exist:\n$dirty" }
    check(Regex("[0-9a-f]{40}").matches(head)) { "unexpected git HEAD '$head'" }
    return head
}

fun Exec.installerCargoEnvironment() {
    if (installerPlatform == "macos") {
        environment("MACOSX_DEPLOYMENT_TARGET", installerMinimumOs.get())
        environment("SDKROOT", installerMacosSdkPath.get())
    }
    environment("RUSTFLAGS", installerRustFlags())
    environment("SOURCE_DATE_EPOCH", "946684800")
    environment("ZERO_AR_DATE", "1")
}

// The channel is compiled into the engine. Production is the default and never carries a cfg; a
// fixture or development engine resolves only its own root, registration and identity, so its
// package-less commands cannot address production state. Called after `installerCargoEnvironment`
// because it replaces RUSTFLAGS with the remapped flags plus the channel.
fun Exec.installerChannelEnvironment(channel: String) {
    if (channel != "production") {
        environment("RUSTFLAGS", installerRustFlags() + " --cfg veles_channel=\"$channel\"")
    }
}

val bridgeInstallerToolchainCheck = tasks.register("bridgeInstallerToolchainCheck") {
    group = "native-bridge"
    description = "Asserts the native-bridge/installer crate resolves to its pinned Rust 1.98.0 toolchain."
    inputs.file(installerDir.file("rust-toolchain.toml"))
    doLast {
        val hint = "Install rustup and run `rustup show active-toolchain` in native-bridge/installer/ to install the pinned toolchain"
        requireOnPath("rustup", "Rust 1.98.0", hint)
        requireOnPath("cargo", "Rust 1.98.0", hint)
        val rustc = providers.exec {
            workingDir(installerDir)
            commandLine("rustc", "--version")
        }.standardOutput.asText.get().trim()
        check(rustc.startsWith("rustc 1.98.0 ")) {
            "native-bridge/installer/rust-toolchain.toml requires Rust 1.98.0; active toolchain is '$rustc'"
        }
    }
}

val installerCheckTargetDir = installerBuildRoot.map { it.dir("target/check") }

val bridgeInstallerFormat = tasks.register<Exec>("bridgeInstallerFormat") {
    group = "native-bridge"
    description = "Checks cargo fmt formatting for the installer crate."
    dependsOn(bridgeInstallerToolchainCheck)
    inputs.files(installerSources)
    workingDir(installerDir)
    environment("CARGO_TARGET_DIR", installerCheckTargetDir.get().asFile)
    commandLine("cargo", "fmt", "--all", "--", "--check")
}

val bridgeInstallerLint = tasks.register<Exec>("bridgeInstallerLint") {
    group = "native-bridge"
    description = "Runs locked cargo clippy with -D warnings for the installer crate."
    dependsOn(bridgeInstallerToolchainCheck)
    inputs.files(installerSources)
    workingDir(installerDir)
    environment("CARGO_TARGET_DIR", installerCheckTargetDir.get().asFile)
    commandLine("cargo", "clippy", "--all-targets", "--locked", "--", "-D", "warnings")
}

val bridgeInstallerTest = tasks.register<Exec>("bridgeInstallerTest") {
    group = "native-bridge"
    description = "Runs locked cargo test for the installer crate (portable plus current-OS adapter tests)."
    dependsOn(bridgeInstallerToolchainCheck)
    inputs.files(installerSources)
    workingDir(installerDir)
    environment("CARGO_TARGET_DIR", installerCheckTargetDir.get().asFile)
    commandLine("cargo", "test", "--locked")
}

val bridgeInstallerScriptsTest = tasks.register<Exec>("bridgeInstallerScriptsTest") {
    group = "native-bridge"
    description = "Runs the installer Python script suites (contracts, gate, correspondence, assembler); not run on Windows."
    enabled = installerPlatform != "windows"
    inputs.files(installerSources)
    workingDir(layout.projectDirectory)
    environment("PYTHONDONTWRITEBYTECODE", "1")
    commandLine(installerPython, "-I", "-m", "unittest", "discover", "-s", "native-bridge/installer/scripts", "-p", "test_*.py")
}

val bridgeInstallerToolObservation = tasks.register<Exec>("bridgeInstallerToolObservation") {
    group = "native-bridge"
    description = "Observes the native compiler/SDK identities of THIS machine into build/ (records only; does not judge)."
    inputs.files(installerSources)
    outputs.file(installerToolObservation)
    outputs.upToDateWhen { false }
    doFirst {
        requireInstallerHost()
        delete(installerToolObservation)
    }
    // The crate's rust-toolchain.toml must select the toolchain the build uses,
    // not the caller's global default (N6).
    workingDir(installerDir)
    commandLine(
        installerPython, "-I", installerToolsScript.asFile, "observe", "--target", installerToolTarget,
        "--output", installerToolObservation.get().asFile,
    )
}

val bridgeInstallerToolCheck = tasks.register<Exec>("bridgeInstallerToolCheck") {
    group = "native-bridge"
    description = "Fails unless the observed tools equal config/tools.lock.json; unresolved Windows identities always fail."
    dependsOn(bridgeInstallerToolObservation)
    inputs.files(installerSources)
    commandLine(
        installerPython, "-I", installerToolsScript.asFile, "check", "--target", installerToolTarget,
        "--observed", installerToolObservation.get().asFile,
    )
}

// Registers the two-pass producer for one channel and one build kind: config check, then per slot
// (bootstrap engine, host, optional Swift frontend, optional NSIS uninstaller, staging, assembly and
// carrier verification, carrier-bearing engine, staging, final assembly, optional Windows resource
// embedding, carrier-bytes verification). The release kind then runs the A/B finalization that
// publishes tree.tar, provenance.json, report-ab.json and SHA256SUMS; the debug kind packages the
// finished installer (setup.exe / DMG) with metadata and symbols.
fun registerInstallerProducer(
    taskName: String,
    channel: String,
    config: Provider<RegularFile>,
    summary: String,
    kind: InstallerBuildKind,
): TaskProvider<Exec> {
    val buildName = kind.name
    val configCheck = tasks.register<Exec>("${taskName}Config") {
        group = "native-bridge"
        description = "Refuses a release config that is not tracked, unmodified and of the $channel channel."
        // A production build starts only on the locked toolchain; the fixture records what it was built with.
        dependsOn(if (channel == "production") bridgeInstallerToolCheck else bridgeInstallerToolObservation)
        inputs.files(config)
        doFirst { requireInstallerHost() }
        commandLine(
            installerPython, "-I", installerAssembler.asFile, "check-config", "--config", config.get().asFile,
            "--channel", channel, "--platform", installerPlatform, "--require-tracked",
        )
    }
    // The single literal origin of the config's extension ID: compiled into the host, so a changed ID
    // must rebuild it. Evaluated lazily, only for a graph that actually builds the host.
    val allowedOrigin = providers.exec {
        commandLine(installerPython, "-I", installerAssembler.asFile, "origin", "--config", config.get().asFile)
    }.standardOutput.asText.map { it.trim() }
    val configVersion = providers.provider {
        @Suppress("UNCHECKED_CAST")
        (groovy.json.JsonSlurper().parse(config.get().asFile) as Map<String, Any?>)["version"] as String
    }
    // The one installer version of this producer: the descriptor (both assembly passes), the macOS
    // bundle VERSION, the NSIS uninstaller and the host's user agent all receive it. Only the
    // production channel may take the planned version from VELES_INSTALLER_VERSION (an empty value
    // keeps the config's); a fixture build always carries its tracked config's version.
    val installerVersion = (
        if (channel == "production") {
            providers.environmentVariable("VELES_INSTALLER_VERSION").filter { it.isNotEmpty() }.orElse(configVersion)
        } else {
            configVersion
        }
    ).map { version ->
        require(installerVersionPattern.matches(version)) {
            "installer version must be MAJOR.MINOR.PATCH without leading zeros (got '$version')"
        }
        version
    }
    val slotFinishers = mutableListOf<TaskProvider<*>>()
    // One Swift argument list for the frontend build and the bin-path query, so the second can
    // never rebuild a different executable. SwiftPM's release link records the per-slot scratch
    // path (the object files' debug map) in the linked frontend, and `-file-prefix-map` only
    // rewrites Swift source paths, so slot a and slot b differ by that path alone and the A/B
    // producer refuses to publish. `ld -S` omits the debug map; the release kind publishes no
    // symbols, so nothing is lost, while the debug kind keeps its map for `dsymutil`.
    fun installerSwiftArguments(scratch: File, configuration: String, binPath: Boolean): MutableList<String> {
        val arguments = mutableListOf(
            "xcrun", "swift", "build", "--configuration", configuration, "--arch", "arm64",
            "--sdk", installerMacosSdkPath.get(), "--scratch-path", scratch.absolutePath,
            "-Xswiftc", "-file-prefix-map", "-Xswiftc", "${layout.projectDirectory.asFile.absolutePath}=/veles-src",
        )
        if (kind.name == "release") {
            arguments += listOf("-Xswiftc", "-Xlinker", "-Xswiftc", "-S")
        }
        if (binPath) {
            arguments += "--show-bin-path"
        }
        return arguments
    }
    val slotOutputs = mutableMapOf<String, Provider<Directory>>()
    var previousFinisher: TaskProvider<*>? = null
    var lastEmbeddedTargetDir: Provider<Directory>? = null
    var lastHostTargetDir: Provider<Directory>? = null
    var lastFrontendScratchDir: Provider<Directory>? = null
    var lastSymbolsDir: Provider<Directory>? = null
    kind.slots.forEach { slot ->
        val suffix = slot.uppercase()
        val installerTargetDir = installerBuildRoot.map { it.dir("target/$buildName-$channel-$slot/installer") }
        val embeddedTargetDir = installerBuildRoot.map { it.dir("target/$buildName-$channel-$slot/installer-embedded") }
        val hostTargetDir = installerBuildRoot.map { it.dir("target/$buildName-$channel-$slot/host") }
        val frontendScratchDir = installerBuildRoot.map { it.dir("target/$buildName-$channel-$slot/frontend") }
        val slotRoot = installerBuildRoot.map { it.dir("slots/$buildName/$channel/$slot") }
        val binDir = slotRoot.map { it.dir("bin") }
        val embeddedBinDir = slotRoot.map { it.dir("bin-embedded") }
        val assembledDir = slotRoot.map { it.dir("assembled") }
        val assembledFinalDir = slotRoot.map { it.dir("assembled-final") }
        val uninstallerDir = slotRoot.map { it.dir("uninstall") }
        val symbolsDir = installerBuildRoot.map { it.dir("target/$buildName-$channel-$slot/symbols") }
        lastEmbeddedTargetDir = embeddedTargetDir
        lastHostTargetDir = hostTargetDir
        lastFrontendScratchDir = frontendScratchDir
        lastSymbolsDir = symbolsDir
        // The published tree is the one re-assembled from the carrier-bearing engine; the bootstrap
        // tree only exists to let the assembler compute the carrier it then hands to that build.
        slotOutputs[slot] = assembledFinalDir

        // The assembler arguments are identical for both passes; only the staged binaries and the
        // output directory differ.
        fun assemblerArguments(binaries: Directory, output: Directory): MutableList<String> {
            val arguments = mutableListOf(
                installerPython, "-I", installerAssembler.asFile.absolutePath, "assemble",
                "--platform", installerPlatform, "--channel", channel, "--config", config.get().asFile.absolutePath,
                "--commit", installerSourceCommit(), "--binaries", binaries.asFile.absolutePath,
                "--output", output.asFile.absolutePath, "--version", installerVersion.get(),
            )
            if (installerPlatform == "macos") {
                val binPath = providers.exec {
                    workingDir(installerDir.dir("macos"))
                    commandLine(installerSwiftArguments(frontendScratchDir.get().asFile, kind.swiftConfiguration, true))
                }.standardOutput.asText.get().trim()
                arguments += listOf(
                    "--frontend", "$binPath/VelesBridgeInstaller",
                    "--packager", installerDir.file("scripts/package-macos.sh").asFile.absolutePath,
                )
            }
            return arguments
        }

        // Pass 1: a bootstrap engine with no release identity. It is only a member of the assembler's
        // closed binary set (the engine is not part of the payload inventory), never published.
        val engine = tasks.register<Exec>("${taskName}Engine$suffix") {
            group = "native-bridge"
            description = "Builds the bootstrap installer engine and cleanup helper (slot $slot, $channel) the assembler consumes."
            dependsOn(configCheck, bridgeInstallerToolchainCheck)
            inputs.files(installerSources)
            inputs.files(config)
            outputs.dir(installerTargetDir)
            workingDir(installerDir)
            doFirst {
                requireInstallerHost()
                delete(installerTargetDir)
                // The bootstrap pass must never inherit an ambient carrier: it exists only to
                // satisfy the assembler's closed binary set; the published tree is rebuilt from
                // the embedded pass below.
                environment.remove("VELES_EMBEDDED_CARRIER")
                environment.remove("VELES_BUILD_CHANNEL")
                environment("CARGO_TARGET_DIR", installerTargetDir.get().asFile)
                installerCargoEnvironment()
                installerChannelEnvironment(channel)
            }
            val cargoArguments = mutableListOf("cargo", "build", "--locked")
            cargoArguments.addAll(kind.cargoProfileArguments)
            if (kind.cargoFeatures.isNotEmpty()) cargoArguments.addAll(kind.cargoFeatures)
            cargoArguments.addAll(listOf("--target", installerRustTriple, "--bin", "veles-bridge-maintenance", "--bin", "veles-bridge-cleanup"))
            commandLine(cargoArguments)
        }
        val host = tasks.register<Exec>("${taskName}Host$suffix") {
            group = "native-bridge"
            description = "Builds the production Native Messaging host (slot $slot, $channel) with the config's exact origin."
            dependsOn(configCheck, bridgeHostToolchainCheck)
            inputs.files(bridgeHostSources)
            inputs.files(config)
            // The allowlist is compiled in through option_env!, so the exact origin is a tracked build input.
            inputs.property("VELES_ALLOWED_ORIGINS", allowedOrigin)
            // The user-agent version is compiled in through option_env!("VELES_HOST_VERSION").
            inputs.property("installerVersion", installerVersion)
            outputs.dir(hostTargetDir)
            workingDir(productionHostDir)
            doFirst {
                requireInstallerHost()
                delete(hostTargetDir)
                environment("CARGO_TARGET_DIR", hostTargetDir.get().asFile)
                environment("VELES_ALLOWED_ORIGINS", allowedOrigin.get())
                environment("VELES_HOST_VERSION", installerVersion.get())
                installerCargoEnvironment()
            }
            commandLine(
                listOf("cargo", "build", "--locked") + kind.cargoProfileArguments +
                    listOf("--target", installerRustTriple, "--bin", "veles-native-host"),
            )
        }
        val frontend = tasks.register<Exec>("${taskName}Frontend$suffix") {
            group = "native-bridge"
            description = "Builds the SwiftUI installer frontend (macOS only, slot $slot, $channel) with a private scratch path."
            enabled = installerPlatform == "macos"
            dependsOn(configCheck)
            inputs.files(installerSources)
            outputs.dir(frontendScratchDir)
            workingDir(installerDir.dir("macos"))
            doFirst {
                delete(frontendScratchDir)
                // Resolved at execution: the SDK path exists only on a macOS runner.
                commandLine(installerSwiftArguments(frontendScratchDir.get().asFile, kind.swiftConfiguration, false))
            }
        }
        val uninstaller = tasks.register<Exec>("${taskName}Uninstaller$suffix") {
            group = "native-bridge"
            description = "Generates the unsigned NSIS uninstaller (Windows only, slot $slot, $channel)."
            enabled = installerPlatform == "windows"
            dependsOn(configCheck)
            inputs.files(installerSources)
            inputs.property("installerVersion", installerVersion)
            outputs.dir(uninstallerDir)
            doFirst {
                delete(uninstallerDir)
                uninstallerDir.get().asFile.mkdirs()
                commandLine(
                    "pwsh", "-NoProfile", "-File", installerDir.file("scripts/generate-uninstaller.ps1").asFile,
                    "-Makensis", installerMakensis.get(), "-Version", installerVersion.get(),
                    "-Channel", channel,
                    "-Output", uninstallerDir.get().file("uninstall.exe").asFile,
                )
            }
        }
        val stage = tasks.register<Sync>("${taskName}Stage$suffix") {
            group = "native-bridge"
            description = "Stages exactly the bootstrap executables the assembler accepts (slot $slot, $channel)."
            dependsOn(engine, host, uninstaller)
            into(binDir)
            from(installerTargetDir.map { it.dir("$installerRustTriple/${kind.cargoBinaryDirectory}") }) {
                include("veles-bridge-maintenance$installerExecutableSuffix")
                if (installerPlatform == "windows") include("veles-bridge-cleanup$installerExecutableSuffix")
            }
            from(hostTargetDir.map { it.dir("$installerRustTriple/${kind.cargoBinaryDirectory}") }) {
                include("veles-native-host$installerExecutableSuffix")
            }
            from(uninstallerDir) { include("uninstall.exe") }
        }
        val assemble = tasks.register<Exec>("${taskName}Assemble$suffix") {
            group = "native-bridge"
            description = "Assembles the bootstrap unsigned input tree and publishes the carrier (slot $slot, $channel)."
            dependsOn(stage, frontend)
            inputs.files(config)
            inputs.files(installerSources)
            inputs.property("installerVersion", installerVersion)
            outputs.dir(assembledDir)
            doFirst {
                delete(assembledDir)
                commandLine(assemblerArguments(binDir.get(), assembledDir.get()))
            }
        }
        // The engine build consumes an already-verified artifact: the carrier the assembler just wrote
        // is checked against this tree's exact descriptor and the registered channel before compiling.
        val carrierCheck = tasks.register<Exec>("${taskName}CarrierCheck$suffix") {
            group = "native-bridge"
            description = "Verifies the assembled carrier against the exact descriptor and channel (slot $slot, $channel)."
            dependsOn(assemble)
            inputs.files(assembledDir)
            outputs.upToDateWhen { false }
            doFirst {
                val descriptor = if (installerPlatform == "windows") {
                    assembledDir.get().file("tree/package/descriptor.json")
                } else {
                    assembledDir.get().file("tree/Veles Bridge Installer.app/Contents/Resources/package/descriptor.json")
                }
                commandLine(
                    installerPython, "-I", installerAssembler.asFile.absolutePath, "verify-carrier",
                    "--carrier", assembledDir.get().file("embedded-carrier.bin").asFile.absolutePath,
                    "--descriptor", descriptor.asFile.absolutePath,
                    "--channel", channel,
                )
            }
        }
        // Pass 2: the engine that is published. It compiles the exact verified carrier bytes in;
        // `build.rs` refuses a production build that reaches this point without one.
        val engineEmbedded = tasks.register<Exec>("${taskName}EngineEmbedded$suffix") {
            group = "native-bridge"
            description = "Rebuilds the engine with the verified carrier compiled in (slot $slot, $channel)."
            dependsOn(carrierCheck, bridgeInstallerToolchainCheck)
            inputs.files(installerSources)
            inputs.files(config)
            val carrierDocument = assembledDir.map { it.file("embedded-carrier.bin") }
            inputs.file(carrierDocument)
            outputs.dir(embeddedTargetDir)
            workingDir(installerDir)
            doFirst {
                requireInstallerHost()
                delete(embeddedTargetDir)
                environment("CARGO_TARGET_DIR", embeddedTargetDir.get().asFile)
                installerCargoEnvironment()
                installerChannelEnvironment(channel)
                // The channel is declared so `build.rs` fails closed when a production build has no
                // carrier; the document is the assembler's verified artifact, never read at run time.
                environment("VELES_BUILD_CHANNEL", channel)
                environment("VELES_EMBEDDED_CARRIER", carrierDocument.get().asFile.absolutePath)
            }
            val cargoArguments = mutableListOf("cargo", "build", "--locked")
            cargoArguments.addAll(kind.cargoProfileArguments)
            if (kind.cargoFeatures.isNotEmpty()) cargoArguments.addAll(kind.cargoFeatures)
            cargoArguments.addAll(listOf("--target", installerRustTriple, "--bin", "veles-bridge-maintenance", "--bin", "veles-bridge-cleanup"))
            commandLine(cargoArguments)
        }
        val stageEmbedded = tasks.register<Sync>("${taskName}StageEmbedded$suffix") {
            group = "native-bridge"
            description = "Stages the carrier-bearing executables for the published tree (slot $slot, $channel)."
            dependsOn(engineEmbedded, host, uninstaller)
            into(embeddedBinDir)
            from(embeddedTargetDir.map { it.dir("$installerRustTriple/${kind.cargoBinaryDirectory}") }) {
                include("veles-bridge-maintenance$installerExecutableSuffix")
                if (installerPlatform == "windows") include("veles-bridge-cleanup$installerExecutableSuffix")
            }
            from(hostTargetDir.map { it.dir("$installerRustTriple/${kind.cargoBinaryDirectory}") }) {
                include("veles-native-host$installerExecutableSuffix")
            }
            from(uninstallerDir) { include("uninstall.exe") }
        }
        val assembleFinal = tasks.register<Exec>("${taskName}AssembleFinal$suffix") {
            group = "native-bridge"
            description = "Assembles the published unsigned input tree from the carrier-bearing engine (slot $slot, $channel)."
            dependsOn(stageEmbedded, frontend)
            inputs.files(config)
            inputs.files(installerSources)
            inputs.property("installerVersion", installerVersion)
            outputs.dir(assembledFinalDir)
            doFirst {
                delete(assembledFinalDir)
                commandLine(assemblerArguments(embeddedBinDir.get(), assembledFinalDir.get()))
            }
        }
        val embedCarrier = tasks.register<Exec>("${taskName}EmbedCarrier$suffix") {
            group = "native-bridge"
            description = "Embeds the policy carrier resource into the published engine image (Windows only, slot $slot, $channel)."
            enabled = installerPlatform == "windows"
            dependsOn(assembleFinal)
            doFirst {
                commandLine(
                    "pwsh", "-NoProfile", "-File", installerDir.file("scripts/embed-carrier.ps1").asFile,
                    "-Image", assembledFinalDir.get().file("tree/package/carrier.exe").asFile,
                    "-Carrier", assembledFinalDir.get().file("carrier.bin").asFile,
                )
            }
        }
        val verifyCarrier = tasks.register<Exec>("${taskName}VerifyCarrier$suffix") {
            group = "native-bridge"
            description = "Requires the built engine image to contain exactly the assembler's carrier bytes (slot $slot, $channel)."
            dependsOn(if (installerPlatform == "windows") embedCarrier else assembleFinal)
            inputs.files(assembledFinalDir)
            outputs.upToDateWhen { false }
            doFirst {
                val descriptor: File
                val image: File
                if (installerPlatform == "windows") {
                    descriptor = assembledFinalDir.get().file("tree/package/descriptor.json").asFile
                    image = assembledFinalDir.get().file("tree/package/carrier.exe").asFile
                } else {
                    val packageDir = assembledFinalDir.get().dir("tree/Veles Bridge Installer.app/Contents/Resources/package")
                    descriptor = packageDir.file("descriptor.json").asFile
                    image = packageDir.file("carrier.app/Contents/MacOS/veles-bridge-maintenance").asFile
                }
                commandLine(
                    installerPython, "-I", installerAssembler.asFile.absolutePath, "verify-carrier",
                    "--carrier", assembledFinalDir.get().file("embedded-carrier.bin").asFile.absolutePath,
                    "--descriptor", descriptor.absolutePath,
                    "--channel", channel,
                    "--image", image.absolutePath,
                )
            }
        }
        previousFinisher?.let { earlier ->
            engine.configure { mustRunAfter(earlier) }
            engineEmbedded.configure { mustRunAfter(earlier) }
            host.configure { mustRunAfter(earlier) }
        }
        previousFinisher = verifyCarrier
        slotFinishers += verifyCarrier
    }
    val outDir = installerBuildRoot.map {
        it.dir(if (kind.outputDirectory.isEmpty()) "out/$channel" else "out/${kind.outputDirectory}")
    }
    if (kind.name == "debug") {
        // Symbols travel beside the finished installer; both are uploaded, never mixed into a tree.
        // A plain task (not Sync): on macOS there are no PDB sources, and a Sync would be skipped as
        // NO-SOURCE, which would skip the dSYM generation too.
        val symbols = tasks.register("${taskName}Symbols") {
            group = "native-bridge"
            description = "Generates and stages the debug symbols (PDBs on Windows, dSYMs on macOS)."
            dependsOn(slotFinishers)
            inputs.files(lastEmbeddedTargetDir!!.map { it.dir("$installerRustTriple/${kind.cargoBinaryDirectory}") })
            inputs.files(lastHostTargetDir!!.map { it.dir("$installerRustTriple/${kind.cargoBinaryDirectory}") })
            if (installerPlatform == "macos") {
                inputs.files(lastFrontendScratchDir!!)
            }
            outputs.dir(lastSymbolsDir!!)
            outputs.upToDateWhen { false }
            doFirst {
                val destination = lastSymbolsDir!!.get().asFile
                delete(destination)
                destination.mkdirs()
            }
            doLast {
                val destination = lastSymbolsDir!!.get().asFile
                // Windows: PDBs written beside both debug target images.
                for (directory in listOf(
                    lastEmbeddedTargetDir!!.get().asFile.resolve("$installerRustTriple/${kind.cargoBinaryDirectory}"),
                    lastHostTargetDir!!.get().asFile.resolve("$installerRustTriple/${kind.cargoBinaryDirectory}"),
                )) {
                    if (!directory.isDirectory) continue
                    directory.listFiles { file -> file.isFile && file.name.endsWith(".pdb") }?.forEach { source ->
                        source.copyTo(destination.resolve(source.name), overwrite = true)
                    }
                }
                if (installerPlatform == "macos") {
                    val frontend = lastFrontendScratchDir!!.get().asFile.walkTopDown()
                        .firstOrNull { it.isFile && it.name == "VelesBridgeInstaller" }
                    val images = listOfNotNull(
                        lastEmbeddedTargetDir!!.get().asFile.resolve("$installerRustTriple/${kind.cargoBinaryDirectory}/veles-bridge-maintenance"),
                        lastEmbeddedTargetDir!!.get().asFile.resolve("$installerRustTriple/${kind.cargoBinaryDirectory}/veles-bridge-cleanup"),
                        lastHostTargetDir!!.get().asFile.resolve("$installerRustTriple/${kind.cargoBinaryDirectory}/veles-native-host"),
                        frontend,
                    )
                    for (binary in images) {
                        if (!binary.isFile) {
                            logger.warn("${taskName}Symbols: no debug image at $binary")
                            continue
                        }
                        val dSYM = destination.resolve("${binary.name}.dSYM")
                        val result = providers.exec {
                            commandLine("xcrun", "dsymutil", binary.absolutePath, "-o", dSYM.absolutePath)
                        }.result.get()
                        check(result.exitValue == 0) {
                            "dsymutil failed for ${binary.name} with exit code ${result.exitValue}"
                        }
                    }
                }
                check(destination.walkTopDown().any { it.isFile }) {
                    "the debug build produced no PDB/dSYM symbols for the debug installer"
                }
            }
        }
        return tasks.register<Exec>(taskName) {
            group = "native-bridge"
            description = summary
            dependsOn(slotFinishers)
            dependsOn(symbols)
            inputs.files(config, installerReleaseGate, installerDir.file("windows/installer.nsi"),
                installerDir.file("scripts/generate-uninstaller.ps1"))
            inputs.files(slotOutputs.getValue("a"))
            inputs.dir(lastSymbolsDir!!)
            val runId = providers.environmentVariable("VELES_WORKFLOW_RUN_ID").orElse("local")
            inputs.property("VELES_WORKFLOW_RUN_ID", runId)
            outputs.dir(outDir)
            doFirst {
                requireInstallerHost()
                delete(outDir)
                outDir.get().asFile.parentFile.mkdirs()
                environment("VELES_WORKFLOW_RUN_ID", runId.get())
                val arguments = mutableListOf(
                    installerPython, "-I", installerReleaseGate.asFile.absolutePath, "package-debug",
                    "--platform", installerPlatform,
                    "--tree", slotOutputs.getValue("a").get().file("tree").asFile.absolutePath,
                    "--config", config.get().asFile.absolutePath,
                    "--output", outDir.get().asFile.absolutePath,
                    "--commit", installerSourceCommit(),
                    "--symbols", lastSymbolsDir!!.get().asFile.absolutePath,
                )
                if (installerPlatform == "windows") {
                    arguments += listOf("--nsis-path", installerMakensis.get())
                }
                commandLine(arguments)
            }
        }
    }
    return tasks.register<Exec>(taskName) {
        group = "native-bridge"
        description = summary
        dependsOn(slotFinishers)
        inputs.files(config)
        outputs.dir(outDir)
        doFirst {
            requireInstallerHost()
            delete(outDir)
            outDir.get().asFile.parentFile.mkdirs()
            commandLine(
                installerPython, "-I", installerAssembler.asFile.absolutePath, "finalize",
                "--platform", installerPlatform,
                "--slot-a", slotOutputs.getValue("a").get().asFile.absolutePath,
                "--slot-b", slotOutputs.getValue("b").get().asFile.absolutePath,
                "--output", outDir.get().asFile.absolutePath, "--commit", installerSourceCommit(),
                "--tool-observation", installerToolObservation.get().asFile.absolutePath,
            )
        }
    }
}

val bridgeInstallerUnsigned = registerInstallerProducer(
    "bridgeInstallerUnsigned", "production", installerReleaseConfig,
    "Builds the UNSIGNED production-channel installer input tree twice and publishes tree.tar, provenance and the A/B report; requires a tracked approved release config.",
    installerReleaseKind,
)

val bridgeInstallerPackageFixture = registerInstallerProducer(
    "bridgeInstallerPackageFixture", "fixture", installerFixtureConfig,
    "Builds the FIXTURE-channel installer input tree twice from the tracked fixture config; never a release candidate.",
    installerReleaseKind,
)

val bridgeInstallerDebug = registerInstallerProducer(
    "bridgeInstallerDebug", "fixture", installerFixtureConfig,
    "Builds the finished FIXTURE-channel DEBUG installer once (setup.exe or ad-hoc-signed DMG) with metadata and symbols; never a release candidate.",
    installerDebugKind,
)

val bridgeInstallerSupplyChain = tasks.register<Exec>("bridgeInstallerSupplyChain") {
    group = "verification"
    description = "Runs the whole supply-chain policy (SBOMs incl. native-installer, licences, build scripts, remote code); exit 0 or fail."
    // Prerequisites (CI provisions them): npm ci --ignore-scripts --prefix verify; ./gradlew installCargoDeny.
    workingDir(layout.projectDirectory)
    commandLine("bash", "verify/verify-supply-chain.sh")
}
// ---- OTP-25 installer producers (end) ----

tasks.named("check") {
    dependsOn(
        rustFormat,
        rustLint,
        rustTest,
        bridgeFormat,
        bridgeLint,
        bridgeTypecheck,
        bridgeTest,
        bridgeHostFormat,
        bridgeHostLint,
        bridgeHostTest,
        bridgeInstallerFormat,
        bridgeInstallerLint,
        bridgeInstallerTest,
        bridgeInstallerScriptsTest,
        "protocolCheck",
    )
}

tasks.register("protocolCheck") {
    group = "verification"
    description = "Runs all host-runnable OTP-02 fixture consumers and contract checks."
    dependsOn(protocolContractCheck, protocolRustTest, protocolWebTest, protocolBridgeTest, ":app:protocolJvmTest")
}

tasks.named<Delete>("clean") {
    dependsOn(":app:clean")
    // Root `clean` removes every declared generated product, verification,
    // SBOM, checksum, and tool output under `build/` plus the two named
    // source-tree exceptions (`web-extension/dist` and the WASM package).
    // `rust/scripts/assert-clean.sh` asserts none of these survive, so a stale
    // artifact can never be mistaken for freshly built evidence.
    delete(
        layout.projectDirectory.dir("web-extension/dist"),
        wasmPackageDir,
        layout.projectDirectory.dir("native-bridge/src-tauri/target"),
        layout.projectDirectory.dir("native-bridge/dist"),
        // OTP-24 production host: cargo targets live under build/native-bridge; these
        // source-tree paths are removed defensively in case a cargo run ignored CARGO_TARGET_DIR.
        layout.projectDirectory.dir("native-bridge/host/target"),
        layout.projectDirectory.dir("native-bridge/host/build"),
        // OTP-25 installer: cargo/swift targets live under build/native-bridge/installer; these source-tree
        // paths are removed defensively in case a developer build ignored the redirect.
        layout.projectDirectory.dir("native-bridge/installer/target"),
        layout.projectDirectory.dir("native-bridge/installer/macos/.build"),
        layout.buildDirectory.dir("native-bridge"),
        // Rust JNI/WASM staged package (rustPackage producer).
        rustPackageDir,
        // Aggregate verification evidence and toolchain manifest.
        layout.buildDirectory.dir("verification"),
        // CycloneDX SBOM outputs.
        layout.buildDirectory.dir("sbom"),
        // Verification cargo-cyclonedx / cargo-deny install roots.
        layout.buildDirectory.dir("verify-tools"),
        // Web-extension deterministic package (npm run package producer).
        layout.buildDirectory.dir("web-extension"),
        // Rust build cache and auxiliary CLI install cache (local, gitignored).
        layout.buildDirectory.dir("rust"),
        layout.buildDirectory.dir("rust-tools"),
        layout.buildDirectory.dir("protocol"),
        // Pinned release tooling (git-cliff install root and verified download cache).
        releaseToolsDir,
    )
}
