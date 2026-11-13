// The Rust workspace is an independent Gradle build: the repository root includes it as
// `includeBuild("rust")` and, after the layout cut-over, android/ includes it as
// `includeBuild("../rust")`. Task names are unchanged and every product stays under the
// repository-root build/ (`build/rust/target`, `build/rust/host-jni`, `build/rust-tools`,
// `build/rust/jniLibs`, ...) so CI cache paths, `rust/scripts/assert-clean.sh`,
// `rust/scripts/verify-apk-jni.sh` and the docs keep working.
import java.io.File
import java.security.MessageDigest
import java.util.Properties
import org.gradle.api.tasks.Exec
import org.gradle.api.tasks.Sync

val repoRoot = layout.settingsDirectory.dir("..")
val rustDir = layout.projectDirectory
val rustCrateDir = rustDir.dir("veles-crypto")
val rustToolsFile = rustDir.file("toolchain-tools.toml")
// The repository-root build directory, typed like layout.buildDirectory so the moved task
// bodies keep their provider expressions and every output stays under build/.
val buildRoot: DirectoryProperty = objects.directoryProperty().apply { set(repoRoot.dir("build")) }
val rustTargetDir = buildRoot.dir("rust/target")
val rustToolsDir = buildRoot.dir("rust-tools")
val wasmPackageDir = repoRoot.dir("web-extension/rust-wasm/pkg")
// Single source of truth for the Android NDK version: android/app/build.gradle.kts reads the
// same file.
val ndkVersion = rustDir.file("ndk-version").asFile.readText().trim()
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

// The NDK version is defined once in rust/ndk-version. The directory is resolved
// at execution time (never at configuration time, so a machine without the NDK can
// still configure and run the non-Android Rust tasks), in this order:
// ANDROID_NDK_HOME, then $ANDROID_SDK_ROOT/ndk/<version>, then `sdk.dir` of the
// repository-root local.properties.
fun ndkSearchLocations(): List<Pair<String, File>> {
    val locations = mutableListOf<Pair<String, File>>()
    System.getenv("ANDROID_NDK_HOME")?.takeIf { it.isNotBlank() }?.let {
        locations += "ANDROID_NDK_HOME" to File(it)
    }
    System.getenv("ANDROID_SDK_ROOT")?.takeIf { it.isNotBlank() }?.let {
        locations += "ANDROID_SDK_ROOT" to File(it, "ndk/$ndkVersion")
    }
    val localProperties = repoRoot.file("local.properties").asFile
    if (localProperties.isFile) {
        val properties = Properties().apply { localProperties.inputStream().use(::load) }
        properties.getProperty("sdk.dir")?.takeIf { it.isNotBlank() }?.let {
            locations += "local.properties sdk.dir" to File(it, "ndk/$ndkVersion")
        }
    }
    return locations
}

fun resolveNdkDirectory(): File {
    val locations = ndkSearchLocations()
    locations.firstOrNull { it.second.isDirectory }?.let { return it.second }
    val searched = locations.joinToString(separator = ", ") { it.second.absolutePath }
        .ifEmpty { "(no ANDROID_NDK_HOME, ANDROID_SDK_ROOT or local.properties sdk.dir)" }
    throw GradleException("NDK $ndkVersion not found; searched: $searched")
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
            buildRoot.dir("rust/tool-install-target/$crateName").get().asFile,
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
    workingDir(repoRoot)
    commandLine("node", "rust/veles-crypto/tests/pairing-wasm.mjs")
}

for ((suffix, flag) in listOf("Entropy" to "--entropy-failure", "Expiry" to "--clock-tests",
    "MemoryBound" to "--memory-boundary", "ResponseErasure" to "--response-erasure")) {
    val test = tasks.register<Exec>("rustWasm${suffix}Test") {
        group = "verification"
        dependsOn(rustWasm)
        workingDir(repoRoot)
        commandLine("node", "rust/veles-crypto/tests/pairing-wasm.mjs", "web-extension/rust-wasm/pkg", flag)
    }
    rustTest.configure { dependsOn(test) }
}

val conformanceWasmDir = buildRoot.dir("rust/conformance-wasm")
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
    workingDir(repoRoot)
    commandLine("node", "rust/veles-crypto/tests/pairing-wasm.mjs", conformanceWasmDir.get().asFile, "--conformance")
}

// Separate output and target directories keep test-only native exports out of
// the build/rust/jniLibs staging directory, release APK and rustPackage.
// OTP-10: the same applies to the host-JVM cdylib. `host-jni` is a non-default
// feature, so this task is the only producer of a host library; it writes under
// build/rust/ (covered by `clean` and rust/scripts/assert-clean.sh) and never
// into build/rust/jniLibs, rustPackage or any release artifact.
val rustHostJniDir = buildRoot.dir("rust/host-jni")
val rustHostJniTargetDir = buildRoot.dir("rust/host-jni-target")
val hostJniLibraryName = when {
    windowsExecutableSuffix.isNotEmpty() -> "veles_crypto.dll"
    System.getProperty("os.name").startsWith("Mac") -> "libveles_crypto.dylib"
    else -> "libveles_crypto.so"
}

val rustHostJni = tasks.register<Exec>("rustHostJni") {
    group = "rust"
    description = "Builds the locked host-JVM cdylib used by JVM tests and the loopback child JVM."
    dependsOn("rustToolchainCheck")
    inputs.files(
        fileTree(rustCrateDir) { include("Cargo.toml", "src/**/*.rs") },
        rustDir.file("Cargo.toml"),
        rustDir.file("Cargo.lock"),
        rustDir.file("rust-toolchain.toml"),
    )
    outputs.dir(rustHostJniDir)
    workingDir(rustDir)
    doFirst {
        delete(rustHostJniDir)
        environment("CARGO_TARGET_DIR", rustHostJniTargetDir.get().asFile)
        // The JNI boundary keeps the panic machinery; a test-only build must
        // match the release product's unwind behavior so panics surface as
        // Java exceptions instead of aborting the test JVM.
        environment("CARGO_PROFILE_RELEASE_PANIC", "unwind")
        // Explicit registry-path remap (rustJniConformance sets none): the host
        // library embeds panic locations like the product .so, so a build must
        // not depend on where CARGO_HOME happens to store the registry.
        environment("RUSTFLAGS", cargoRegistryRemapRustFlags())
    }
    commandLine(
        "cargo", "build", "--locked", "--release",
        "--package", "veles-crypto", "--features", "host-jni",
    )
    doLast {
        val built = rustHostJniTargetDir.get().asFile.resolve("release/$hostJniLibraryName")
        check(built.isFile) { "rustHostJni: expected the host library at $built" }
        val output = rustHostJniDir.get().asFile
        output.mkdirs()
        built.copyTo(output.resolve(hostJniLibraryName), overwrite = true)
    }
}

tasks.register<Exec>("rustJniConformance") {
    group = "verification"
    dependsOn(verifyCargoNdk)
    workingDir(rustDir)
    doFirst {
        val ndk = resolveNdkDirectory()
        requireNdkVersion(ndk, ndkVersion)
        environment("ANDROID_NDK_HOME", ndk)
        environment("PATH", rustToolsDir.get().dir("cargo-ndk/bin").asFile.prependToPath(System.getenv("PATH")))
        environment("CARGO_TARGET_DIR", buildRoot.dir("rust/conformance-target").get().asFile)
        environment("CARGO_PROFILE_RELEASE_PANIC", "unwind")
    }
    commandLine("cargo", "ndk", "--platform", "33", "--target", "arm64-v8a", "--target", "x86_64",
        "--output-dir", buildRoot.dir("rust/conformance-jni").get().asFile,
        "build", "--release", "--locked", "--features", "conformance")
}

val rustJniOutput = buildRoot.dir("rust/jniLibs")
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
    inputs.property("ndkVersion", ndkVersion)
    outputs.dir(rustJniOutput)
    workingDir(rustDir)
    doFirst {
        val ndkDir = resolveNdkDirectory()
        val expected = ndkVersion
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

val rustPackageDir = buildRoot.dir("rust-package")
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

val rustLintHostJni = tasks.register<Exec>("rustLintHostJni") {
    group = "rust"
    description = "Runs denied-warning Clippy on the host-JVM JNI exports (non-default host-jni feature)."
    dependsOn("rustToolchainCheck")
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    commandLine(
        "cargo", "clippy", "--package", "veles-crypto", "--all-targets", "--locked",
        "--features", "host-jni", "--", "-D", "warnings",
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
        val ndkDir = resolveNdkDirectory()
        val expected = ndkVersion
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
    description = "Runs denied-warning Clippy checks for host, host-JNI, WASM, and Android JNI."
    dependsOn(rustLintHost, rustLintHostJni, rustLintWasm, rustLintAndroid)
}

tasks.register("rustNdkVersion") {
    group = "rust"
    description = "Prints the pinned Android NDK version (rust/ndk-version) for CI provisioning."
    doLast { println(ndkVersion) }
}

// Diagnostic entry point for rust/scripts/test-ndk-lookup.sh: prints the directory
// the JNI tasks would use without running cargo.
tasks.register("rustNdkDir") {
    group = "rust"
    description = "Prints the resolved Android NDK directory (ANDROID_NDK_HOME, SDK ndk/<version>, local.properties sdk.dir)."
    doLast { println(resolveNdkDirectory().absolutePath) }
}
