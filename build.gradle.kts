// Top-level build file where you can add configuration options common to all sub-projects/modules.
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest
import org.gradle.api.tasks.Delete
import org.gradle.api.tasks.Exec
import org.gradle.api.tasks.Sync
import org.gradle.api.DefaultTask
import org.gradle.api.file.ArchiveOperations
import org.gradle.api.file.ConfigurableFileCollection
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.file.FileSystemOperations
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.provider.ListProperty
import org.gradle.api.provider.MapProperty
import org.gradle.api.provider.Property
import org.gradle.api.provider.Provider
import org.gradle.api.provider.ProviderFactory
import org.gradle.api.tasks.Input
import org.gradle.api.tasks.InputFile
import org.gradle.api.tasks.InputFiles
import org.gradle.api.tasks.Internal
import org.gradle.api.tasks.Optional
import org.gradle.api.tasks.OutputDirectory
import org.gradle.api.tasks.OutputFile
import org.gradle.api.tasks.PathSensitive
import org.gradle.api.tasks.PathSensitivity
import org.gradle.api.tasks.TaskAction
import org.gradle.process.ExecOperations
import org.gradle.work.DisableCachingByDefault
import javax.inject.Inject

plugins {
    base
}

// ---- Included builds ------------------------------------------------------------------
// Android and Rust are independent builds (design D4). The root only orchestrates: no project of
// this build is an Android module, so every Android entry point is an included-build task.
val androidBuild = gradle.includedBuild("android")
// Every Rust task is defined once, in the included `rust` build. The root tasks below
// keep the historical names working from the repository root (CI, rust/scripts/*, docs)
// and only depend on the included task, so a root invocation executes each shared Rust
// task exactly once.
val rustBuild = gradle.includedBuild("rust")

listOf(
    "rustToolchainCheck",
    "installCargoNdk",
    "verifyCargoNdk",
    "installWasmPack",
    "verifyWasmPack",
    "installWasmBindgenCli",
    "verifyWasmBindgen",
    "rustWasmBindgenConsistency",
    "rustInstall",
    "rustWasmLockCheck",
    "rustWasm",
    "rustNativeTest",
    "rustWasmSmoke",
    "rustWasmPairingTest",
    "rustWasmEntropyTest",
    "rustWasmExpiryTest",
    "rustWasmMemoryBoundTest",
    "rustWasmResponseErasureTest",
    "rustTest",
    "rustWasmConformance",
    "rustWasmConformanceTest",
    "rustHostJni",
    "rustJniConformance",
    "rustJni",
    "rustPackage",
    "rustFormat",
    "rustLintHost",
    "rustLintHostJni",
    "rustLintWasm",
    "rustLintAndroid",
    "rustLint",
    "rustNdkVersion",
    "rustNdkDir",
).forEach { rustTaskName ->
    tasks.register(rustTaskName) {
        group = "rust"
        description = "Runs :$rustTaskName of the included rust build."
        dependsOn(rustBuild.task(":$rustTaskName"))
        outputs.doNotCacheIf("forwards to the included rust build") { true }
    }
}

// ---- Android build bridge --------------------------------------------------------------
// `android/` is an independent build (design D4); the documented Android commands keep working
// from the repository root through thin aliases. App tasks target `:app:<task>`, Spotless targets
// the Android build root because it is applied there, and the retained aggregates below depend on
// included-build tasks directly.
listOf(
    "assembleDebug",
    "assembleRelease",
    "testDebugUnitTest",
    "lintDebug",
    "detekt",
    "connectedDebugAndroidTest",
).forEach { androidTaskName ->
    tasks.register(androidTaskName) {
        group = "android"
        description = "Runs :app:$androidTaskName of the included android build."
        dependsOn(androidBuild.task(":app:$androidTaskName"))
        outputs.doNotCacheIf("forwards to the included android build") { true }
    }
}

listOf("spotlessCheck", "spotlessApply").forEach { androidRootTaskName ->
    tasks.register(androidRootTaskName) {
        group = "android"
        description = "Runs :$androidRootTaskName of the included android build."
        dependsOn(androidBuild.task(":$androidRootTaskName"))
        outputs.doNotCacheIf("forwards to the included android build") { true }
    }
}

// Only the verification-tool installers and protocolRustTest still reach into rust/:
// every Rust build task lives in the included `rust` build.
val rustDir = layout.projectDirectory.dir("rust")
val verifyCargoToolsFile = layout.projectDirectory.file("verify/cargo-tools.toml")
val verifyToolsDir = layout.buildDirectory.dir("verify-tools")
val wasmPackageDir = layout.projectDirectory.dir("web-extension/rust-wasm/pkg")
// The only configuration-time system-property reads: they select the executable suffix, the npm
// launcher, the pinned git-cliff asset and the installer platform, all of which Gradle must know
// while configuring. Everything else is resolved from providers lazily.
val osName: String = providers.systemProperty("os.name").get()
val osArch: String = providers.systemProperty("os.arch").get()
val isWindows: Boolean = osName.startsWith("Windows")
val windowsExecutableSuffix = if (isWindows) ".exe" else ""
// On Windows, Gradle's Exec task process launcher cannot exec the `npm` shell
// script directly — it needs `npm.cmd`. Node ships `node.exe` so plain `node`
// works, but `npm` does not. Resolved once at configuration time and reused by
// every bridge Exec task that invokes npm.
val npmCommand = if (windowsExecutableSuffix.isNotEmpty()) "npm.cmd" else "npm"
val protocolDir = layout.projectDirectory.dir("protocol")

/**
 * Helpers shared by the typed tasks below. Top-level functions in a `.kts` script are members of
 * the script class, so calling one from a task action, a `map {}` lambda or a `doNotCacheIf`
 * predicate captures the script and breaks the configuration cache. Every helper therefore lives
 * in this object and takes plain values.
 */
object RootBuildHelpers {
    /** The executable suffix for the host, used when probing for a command on PATH. */
    fun executableSuffix(osName: String): String = if (osName.startsWith("Windows")) ".exe" else ""

    /** Reads one `name = "version"` pin out of a TOML file's text. */
    fun pinnedVersion(toml: String, name: String, file: String): String {
        val pattern = Regex("(?m)^${Regex.escape(name)}\\s*=\\s*\"([^\"]+)\"\\s*$")
        return requireNotNull(pattern.find(toml)) {
            "Missing '$name' pin in $file"
        }.groupValues[1]
    }

    /** Reads a pin through the file-contents provider, so nothing is read at configuration time. */
    fun pinnedVersionProvider(
        providers: ProviderFactory,
        toolsFile: org.gradle.api.file.RegularFile,
        name: String,
    ): Provider<String> {
        val file = toolsFile
        return providers.fileContents(file).asText.map { pinnedVersion(it, name, file.asFile.toString()) }
    }

    /** Fails with the historical message when a required command is not executable on PATH. */
    fun requireOnPath(
        path: String,
        name: String,
        suffix: String,
        versionHint: String,
        installHint: String,
        missingMessageFormat: String,
    ) {
        val found = path.split(File.pathSeparator).any { dir ->
            File(dir, name + suffix).let { it.isFile && it.canExecute() }
        }
        check(found) {
            missingMessageFormat
                .replace("{name}", name)
                .replace("{installHint}", installHint)
                .replace("{versionHint}", versionHint)
        }
    }

    /** Runs a command and returns its trimmed standard output. */
    fun probe(exec: ExecOperations, directory: File, command: List<String>): String {
        val output = ByteArrayOutputStream()
        exec.exec {
            workingDir(directory)
            commandLine(command)
            standardOutput = output
        }
        return output.toString(Charsets.UTF_8).trim()
    }

    /**
     * Runs one host-toolchain probe: the required commands must be on PATH, the version output
     * must start with `expectedPrefix` (Rust) or reach the minimum major/minor (Node, Python),
     * and a mismatch fails with the probe's own message.
     */
    fun verifyToolchain(exec: ExecOperations, workingDir: File, pathEnv: String, spec: ToolchainProbe) {
        val suffix = executableSuffix(System.getProperty("os.name"))
        for (name in spec.requiredCommands) {
            requireOnPath(
                pathEnv,
                name,
                suffix,
                spec.versionHint,
                spec.installHint,
                spec.missingCommandMessageFormat,
            )
        }
        val detail = if (spec.describeCommand.isEmpty()) null else probe(exec, workingDir, spec.describeCommand)
        val version = probe(exec, workingDir, spec.versionCommand)
        val matches = when {
            spec.expectedPrefix != null -> version.startsWith(spec.expectedPrefix)
            spec.minimumMajorVersion != null -> {
                val match = Regex("(\\d+)\\.(\\d+)").find(version)
                if (match == null) {
                    false
                } else {
                    val major = match.groupValues[1].toInt()
                    val minor = match.groupValues[2].toInt()
                    val minimumMajor = spec.minimumMajorVersion
                    val minimumMinor = spec.minimumMinorVersion ?: 0
                    major > minimumMajor || (major == minimumMajor && minor >= minimumMinor)
                }
            }
            else -> false
        }
        check(matches) {
            spec.mismatchMessageFormat
                .replace("{actual}", version)
                .replace("{detail}", detail ?: "")
        }
    }

    /**
     * The registry-path remap for a byte-reproducible build.
     *
     * rustc embeds the absolute source paths of panic locations (function, file, line) into release
     * binaries whenever panic is not fully stripped (CARGO_PROFILE_RELEASE_PANIC=unwind keeps the
     * panic machinery for JNI). Dependency crates come from
     * ${CARGO_HOME}/registry/src/<registry-hash>/..., so two builds with different CARGO_HOME (CI
     * runner /home/runner/.cargo vs the verification Docker image /opt/cargo) embed different byte
     * strings, shifting every ELF section offset and producing a .so (and APK) that differs between
     * builds of identical source. Remapping the registry prefix to a stable "/cargo-registry" path
     * makes the embedded strings identical regardless of where cargo stores its registry (proven
     * locally: two builds with different CARGO_HOME produce byte-identical .so with this flag). The
     * workspace crate's own path is NOT embedded (no panic locations from the workspace crate carry
     * an absolute path into the binary), so only the registry prefix needs remapping. RUSTFLAGS is
     * set as a task environment (overriding any inherited RUSTFLAGS) so the remap is not optional: a
     * verification-relevant build must not silently pick up an ambient RUSTFLAGS that omits it.
     */
    fun registryRemapRustFlags(cargoHome: String?, userHome: String): String {
        val home: File = if (cargoHome.isNullOrBlank()) {
            File(userHome, ".cargo")
        } else {
            File(cargoHome)
        }
        val registrySrc = File(home, "registry/src").absolutePath
        return "--remap-path-prefix=$registrySrc=/cargo-registry/src"
    }

    /** Tauri bundle targets for a host OS; null on unsupported hosts. */
    fun bundleTargetsFor(osName: String): String? = when {
        osName.startsWith("Windows") -> "nsis,msi"
        osName.startsWith("Mac") -> "app,dmg"
        else -> null
    }
}

/**
 * One host-toolchain probe. Serializable so the configuration cache can store it; the task action
 * resolves it and never touches the build script.
 */
data class ToolchainProbe(
    val requiredCommands: List<String>,
    val versionCommand: List<String>,
    val expectedPrefix: String? = null,
    val minimumMajorVersion: Int? = null,
    val minimumMinorVersion: Int? = null,
    val describeCommand: List<String> = emptyList(),
    val mismatchMessageFormat: String,
    val versionHint: String,
    val installHint: String,
    val missingCommandMessageFormat: String =
        "Could not find '{name}' on PATH. {installHint}. Version hint: {versionHint}.",
) : java.io.Serializable

/**
 * Checks that the host toolchain is installed and of the pinned version. One shape covers the
 * Rust, Node, npm and Python checks; the probes run in list order, as the former single action did.
 */
@DisableCachingByDefault(because = "probes the host toolchain")
abstract class ToolchainCheckTask : DefaultTask() {
    @get:Input
    abstract val probes: ListProperty<ToolchainProbe>

    @get:Input
    abstract val pathEnv: Property<String>

    @get:Internal
    abstract val workingDir: DirectoryProperty

    @get:InputFile
    @get:Optional
    abstract val toolchainFile: RegularFileProperty

    @get:Inject
    abstract val execOps: ExecOperations

    @TaskAction
    fun checkToolchain() {
        val directory = workingDir.get().asFile
        val path = pathEnv.get()
        for (probe in probes.get()) {
            RootBuildHelpers.verifyToolchain(execOps, directory, path, probe)
        }
    }
}

/** Installs an exact-pinned cargo CLI into the repository build directory. */
@DisableCachingByDefault(because = "downloads crates from the network")
abstract class CargoInstallTask : DefaultTask() {
    @get:Input
    abstract val crateName: Property<String>

    @get:Input
    abstract val version: Property<String>

    @get:InputFile
    abstract val toolchainFile: RegularFileProperty

    @get:OutputDirectory
    abstract val installRoot: DirectoryProperty

    @get:Internal
    abstract val targetDir: DirectoryProperty

    @get:Internal
    abstract val workingDir: DirectoryProperty

    @get:Inject
    abstract val execOps: ExecOperations

    @get:Inject
    abstract val fsOps: FileSystemOperations

    @TaskAction
    fun install() {
        val root = installRoot.get().asFile
        fsOps.delete { delete(root) }
        execOps.exec {
            workingDir(this@CargoInstallTask.workingDir.get().asFile)
            environment("CARGO_TARGET_DIR", targetDir.get().asFile.absolutePath)
            commandLine(
                "cargo", "install", "--locked", "--version", version.get(),
                "--root", root.absolutePath, crateName.get(),
            )
        }
    }
}

/** Probes an installed CLI's version and compares it as an exact semver. */
@DisableCachingByDefault(because = "probes an installed tool")
abstract class VerifyToolVersionTask : DefaultTask() {
    @get:Input
    abstract val crateName: Property<String>

    @get:Input
    abstract val version: Property<String>

    @get:InputFile
    abstract val binary: RegularFileProperty

    @get:Input
    abstract val versionArguments: ListProperty<String>

    @get:Inject
    abstract val execOps: ExecOperations

    @TaskAction
    fun verify() {
        val output = ByteArrayOutputStream()
        execOps.exec {
            // cargo-cyclonedx and cargo-deny accept the CARGO variable harmlessly; keeping it
            // unconditional matches the Rust build's verify shape.
            environment("CARGO", "cargo")
            setCommandLine(listOf(binary.get().asFile.absolutePath) + versionArguments.get())
            standardOutput = output
        }
        val text = output.toString(Charsets.UTF_8).trim()
        val actualVersion = text.split(Regex("\\s+"))
            .firstOrNull { it.matches(Regex("\\d+\\.\\d+\\.\\d+(-\\S+)?")) }
        check(actualVersion == version.get()) {
            "Expected ${crateName.get()} ${version.get()} at ${binary.get().asFile}, got: $text"
        }
    }
}

/**
 * An `Exec` whose extra environment is resolved inside the action from a provider, replacing the
 * former `doFirst { environment(...) }` blocks. Never cacheable: these tasks only verify or test.
 */
@DisableCachingByDefault(because = "verification or test-only build")
abstract class CargoExecTask : Exec() {
    @get:Internal
    abstract val extraEnv: MapProperty<String, String>

    @TaskAction
    override fun exec() {
        environment(extraEnv.get())
        super.exec()
    }
}


// Enables the repo's Conventional Commits commit-msg hook (.githooks) for local clones.
// One shared script, also applied by the independent Android build (android/build.gradle.kts),
// so `installGitHooks` has identical behaviour from both entry points.
apply(from = file(".githooks/install-hooks.gradle.kts"))

// ---- Release tooling: pinned git-cliff and gitleaks (scripts/release-tools.toml) and the scripts/ test suite ----
val releaseToolsFile = layout.projectDirectory.file("scripts/release-tools.toml")
val releaseToolsDir = layout.buildDirectory.dir("release-tools")

// Each tool maps the host pair to its own platform key: git-cliff keeps its Rust triples and
// gitleaks uses its release asset names. The host values come from the configuration-time
// system properties, so no process or file is read here.
val gitCliffTarget: String? = run {
    val os = osName.lowercase()
    val arch = when (osArch.lowercase()) {
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

val gitleaksTarget: String? = run {
    val os = osName.lowercase()
    val arch = when (osArch.lowercase()) {
        "amd64", "x86_64" -> "x86_64"
        "aarch64", "arm64" -> "aarch64"
        else -> null
    }
    when {
        arch == null -> null
        os.startsWith("linux") -> "linux_$arch"
        os.startsWith("mac") -> if (arch == "aarch64") "darwin_arm64" else "darwin_x64"
        os.startsWith("windows") && arch == "x86_64" -> "windows_x64"
        else -> null
    }
}

@DisableCachingByDefault(because = "downloads a release archive")
abstract class InstallPinnedToolTask @Inject constructor(
    private val execOps: ExecOperations,
    private val archiveOps: ArchiveOperations,
    private val fsOps: FileSystemOperations,
) : DefaultTask() {
    @get:Input
    abstract val toolName: Property<String>

    @get:Input
    abstract val version: Property<String>

    @get:Input
    abstract val downloadUrl: Property<String>

    @get:Input
    abstract val archiveName: Property<String>

    @get:Input
    abstract val archiveEntry: Property<String>

    @get:Input
    abstract val binaryName: Property<String>

    @get:Input
    abstract val sha256: Property<String>

    @get:Input
    abstract val versionArgs: ListProperty<String>

    @get:Input
    abstract val expectedVersionOutput: Property<String>

    @get:OutputDirectory
    abstract val installDir: DirectoryProperty

    @get:Internal
    abstract val downloadDir: DirectoryProperty

    @TaskAction
    fun install() {
        val archiveName = archiveName.get()
        val download = downloadDir.get().asFile.apply { mkdirs() }.resolve(archiveName)
        val client = java.net.http.HttpClient.newBuilder()
            .followRedirects(java.net.http.HttpClient.Redirect.NORMAL)
            .build()
        val response = client.send(
            java.net.http.HttpRequest.newBuilder(java.net.URI(downloadUrl.get())).GET().build(),
            java.net.http.HttpResponse.BodyHandlers.ofFile(download.toPath()),
        )
        check(response.statusCode() == 200) {
            "Downloading ${downloadUrl.get()} failed with HTTP ${response.statusCode()}"
        }
        val actual = MessageDigest.getInstance("SHA-256").digest(download.readBytes())
            .joinToString("") { "%02x".format(it) }
        if (actual != sha256.get()) {
            download.delete()
            error("SHA-256 mismatch for $archiveName: expected ${sha256.get()}, got $actual")
        }
        val out = installDir.get().asFile
        fsOps.delete { delete(out) }
        fsOps.copy {
            from(if (archiveName.endsWith(".zip")) archiveOps.zipTree(download) else archiveOps.tarTree(download)) {
                include(archiveEntry.get())
                eachFile { path = name }
            }
            includeEmptyDirs = false
            into(out)
        }
        val binary = out.resolve(binaryName.get())
        check(binary.isFile) { "$archiveName does not contain ${archiveEntry.get()}" }
        binary.setExecutable(true)
        val stdout = ByteArrayOutputStream()
        execOps.exec {
            commandLine(binary.absolutePath, *versionArgs.get().toTypedArray())
            standardOutput = stdout
        }
        val reported = stdout.toString(Charsets.UTF_8).trim()
        check(reported == expectedVersionOutput.get()) {
            "Expected '${expectedVersionOutput.get()}' from ${toolName.get()} at $binary, got: '$reported'"
        }
    }
}

val installGitCliff = tasks.register<InstallPinnedToolTask>("installGitCliff") {
    group = "build setup"
    description = "Installs the pinned, SHA-256-verified git-cliff into build/release-tools/git-cliff."
    // The pin is read lazily: only the task action resolves it.
    val toolsFile = releaseToolsFile
    val pinnedVersion = RootBuildHelpers.pinnedVersionProvider(providers, toolsFile, "git-cliff")
    val hostTarget = gitCliffTarget
    val hostDescription = "$osName/$osArch"
    val target = providers.provider {
        requireNotNull(hostTarget) { "No pinned git-cliff archive for $hostDescription" }
    }
    val archiveName = target.zip(pinnedVersion) { asset, version ->
        "git-cliff-$version-$asset.${if (asset.contains("windows")) "zip" else "tar.gz"}"
    }
    toolName.set("git-cliff")
    version.set(pinnedVersion)
    downloadUrl.set(
        archiveName.zip(pinnedVersion) { name, version ->
            "https://github.com/orhun/git-cliff/releases/download/v$version/$name"
        },
    )
    this.archiveName.set(archiveName)
    archiveEntry.set(target.zip(pinnedVersion) { asset, version ->
        "git-cliff-$version/git-cliff${if (asset.contains("windows")) ".exe" else ""}"
    })
    binaryName.set(target.map { "git-cliff${if (it.contains("windows")) ".exe" else ""}" })
    sha256.set(
        target.zip(providers.fileContents(toolsFile).asText) { asset, text ->
            RootBuildHelpers.pinnedVersion(text, "git-cliff-$asset", toolsFile.asFile.toString())
        },
    )
    versionArgs.set(listOf("--version"))
    expectedVersionOutput.set(pinnedVersion.map { "git-cliff $it" })
    installDir.set(releaseToolsDir.map { it.dir("git-cliff") })
    downloadDir.set(releaseToolsDir.map { it.dir("downloads") })
}

val installGitleaks = tasks.register<InstallPinnedToolTask>("installGitleaks") {
    group = "build setup"
    description = "Installs the pinned, SHA-256-verified gitleaks into build/release-tools/gitleaks."
    val toolsFile = releaseToolsFile
    val pinnedVersion = RootBuildHelpers.pinnedVersionProvider(providers, toolsFile, "gitleaks")
    val hostTarget = gitleaksTarget
    val hostDescription = "$osName/$osArch"
    val target = providers.provider {
        requireNotNull(hostTarget) { "No pinned gitleaks archive for $hostDescription" }
    }
    val archiveName = target.zip(pinnedVersion) { asset, version ->
        "gitleaks_${version}_$asset.${if (asset.contains("windows")) "zip" else "tar.gz"}"
    }
    toolName.set("gitleaks")
    version.set(pinnedVersion)
    downloadUrl.set(
        archiveName.zip(pinnedVersion) { name, version ->
            "https://github.com/gitleaks/gitleaks/releases/download/v$version/$name"
        },
    )
    this.archiveName.set(archiveName)
    // Both archives hold the executable at their root, and `gitleaks version` prints the bare version.
    archiveEntry.set(target.map { "gitleaks${if (it.contains("windows")) ".exe" else ""}" })
    binaryName.set(target.map { "gitleaks${if (it.contains("windows")) ".exe" else ""}" })
    sha256.set(
        target.zip(providers.fileContents(toolsFile).asText) { asset, text ->
            RootBuildHelpers.pinnedVersion(text, "gitleaks-$asset", toolsFile.asFile.toString())
        },
    )
    versionArgs.set(listOf("version"))
    expectedVersionOutput.set(pinnedVersion)
    installDir.set(releaseToolsDir.map { it.dir("gitleaks") })
    downloadDir.set(releaseToolsDir.map { it.dir("downloads") })
}

// Python tooling tests (scripts/tests: commitlint, component map, release planner/assets/readiness).
val scriptsTest = tasks.register<Exec>("scriptsTest") {
    group = "verification"
    description = "Runs the scripts/ unit tests (stdlib Python) against the pinned git-cliff and gitleaks."
    dependsOn(installGitCliff, installGitleaks)
    workingDir(layout.projectDirectory)
    environment(
        "VELES_GIT_CLIFF",
        releaseToolsDir.get().file("git-cliff/git-cliff$windowsExecutableSuffix").asFile.absolutePath,
    )
    environment(
        "VELES_GITLEAKS",
        releaseToolsDir.get().file("gitleaks/gitleaks$windowsExecutableSuffix").asFile.absolutePath,
    )
    commandLine(
        if (windowsExecutableSuffix.isNotEmpty()) "python" else "python3",
        "-I", "-m", "unittest", "discover", "-s", "scripts/tests", "-v",
    )
    outputs.doNotCacheIf("runs the tooling test suite") { true }
}

fun registerVerificationCargoTool(
    taskStem: String,
    crateName: String,
    binaryName: String,
    versionArguments: List<String> = listOf("--version"),
): Pair<TaskProvider<CargoInstallTask>, TaskProvider<VerifyToolVersionTask>> {
    // Every execution-time lambda below reads these block-local values only; the script-level
    // vals would capture the script.
    val toolsFile = verifyCargoToolsFile
    val toolsRoot = verifyToolsDir
    val installTargetRoot = layout.buildDirectory
    val rustProjectDir = rustDir
    val rustToolchainFile = rustDir.file("rust-toolchain.toml")
    val suffix = windowsExecutableSuffix
    val pinnedVersion = RootBuildHelpers.pinnedVersionProvider(providers, toolsFile, crateName)
    val installRootProvider = toolsRoot.map { it.dir(crateName) }
    val binaryProvider = installRootProvider.map { it.file("bin/$binaryName$suffix") }
    val install = tasks.register<CargoInstallTask>("install$taskStem") {
        group = "verification"
        description = "Installs $crateName into the verification tool directory."
        dependsOn(rustBuild.task(":rustToolchainCheck"))
        this.crateName.set(crateName)
        this.version.set(pinnedVersion)
        toolchainFile.set(rustToolchainFile)
        this.installRoot.set(installRootProvider)
        targetDir.set(installTargetRoot.map { it.dir("verify-tools/tool-install-target/$crateName") })
        workingDir.set(rustProjectDir)
    }
    val verify = tasks.register<VerifyToolVersionTask>("verify$taskStem") {
        group = "verification"
        description = "Verifies the isolated $crateName tool version."
        dependsOn(install)
        this.crateName.set(crateName)
        this.version.set(pinnedVersion)
        this.binary.set(binaryProvider)
        this.versionArguments.set(versionArguments)
    }
    return install to verify
}

val (_, verifyCargoCyclonedx) = registerVerificationCargoTool(
    "CargoCyclonedx",
    "cargo-cyclonedx",
    "cargo-cyclonedx",
    listOf("cyclonedx", "--version"),
)
val (_, verifyCargoDeny) = registerVerificationCargoTool("CargoDeny", "cargo-deny", "cargo-deny")

val protocolNodeCheck = tasks.register<ToolchainCheckTask>("protocolNodeCheck") {
    group = "verification"
    workingDir.set(layout.projectDirectory)
    pathEnv.set(providers.environmentVariable("PATH").orElse(""))
    probes.set(
        listOf(
            ToolchainProbe(
                requiredCommands = listOf("npm"),
                versionCommand = listOf("node", "--version"),
                minimumMajorVersion = 22,
                minimumMinorVersion = 0,
                mismatchMessageFormat = "Node >=22.0.0 is required; found '{actual}'",
                versionHint = "Node >=22.0.0",
                installHint = "Install Node.js >=22.0.0 to run the OTP-02 contract checks.",
                missingCommandMessageFormat = "Could not find '{name}' on PATH. {installHint}",
            ),
            ToolchainProbe(
                requiredCommands = listOf("python3"),
                versionCommand = listOf("python3", "--version"),
                minimumMajorVersion = 3,
                minimumMinorVersion = 8,
                mismatchMessageFormat = "Python >=3.8 is required; found '{actual}'",
                versionHint = "Python 3.8+",
                installHint = "Install Python 3.8 or newer",
            ),
        ),
    )
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
    outputs.doNotCacheIf("installs npm dependencies from the network") { true }
}

val protocolContractCheck = tasks.register<Exec>("protocolContractCheck") {
    group = "verification"
    description = "Runs OTP-02 Node contract checks with fresh crypto evidence."
    dependsOn(protocolInstall)
    inputs.files(
        fileTree(protocolDir.dir("checks")) { include("**/*.mjs") },
        fileTree(protocolDir.dir("tools")) { include("**/*.mjs", "**/*.py") },
        fileTree(protocolDir.dir("v1")) { include("**/*.json") },
        layout.projectDirectory.file("protocol/docs/veles-v1.md"),
    )
    // The command deletes and recreates evidence, so Gradle must never reuse it.
    outputs.upToDateWhen { false }
    workingDir(layout.projectDirectory)
    commandLine("node", "protocol/checks/check.mjs")
    outputs.doNotCacheIf("regenerates crypto evidence") { true }
}

val protocolRustTest = tasks.register<Exec>("protocolRustTest") {
    group = "verification"
    description = "Runs the host-only Rust OTP-02 shared-fixture consumer."
    dependsOn(rustBuild.task(":rustToolchainCheck"))
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", layout.buildDirectory.dir("rust/target").get().asFile)
    commandLine("cargo", "test", "--locked", "--test", "protocol_fixtures")
    outputs.doNotCacheIf("runs a test") { true }
}

val protocolWebInstall = tasks.register<Exec>("protocolWebInstall") {
    group = "verification"
    description = "Installs script-free dependencies for the web OTP-02 consumer."
    dependsOn(protocolNodeCheck)
    workingDir(layout.projectDirectory.dir("web-extension"))
    commandLine(npmCommand, "ci", "--ignore-scripts")
    outputs.doNotCacheIf("installs npm dependencies from the network") { true }
}

val protocolWebTest = tasks.register<Exec>("protocolWebTest") {
    group = "verification"
    description = "Runs the web extension OTP-02 shared-fixture consumer."
    dependsOn(protocolWebInstall, rustBuild.task(":rustWasm"))
    workingDir(layout.projectDirectory.dir("web-extension"))
    commandLine(npmCommand, "exec", "vitest", "run", "test/protocol-fixtures.test.ts")
    outputs.doNotCacheIf("runs a test") { true }
}

// OTP-09 Task 10 cross-endpoint loopback: the real Kotlin phone engine runs in a child
// JVM while the production TypeScript client engine plays the peer over the stdin/stdout
// protocol. `VELES_LOOPBACK_REQUIRED=1` makes the Vitest suite fail instead of skipping
// when the classpath is unavailable, so CI can never silently lose the check.
val sessionLoopbackClasspathFile = layout.projectDirectory.file("android/app/build/session-loopback/classpath.txt")
// The host-JVM cdylib and the WASM package come from the included Rust build; their
// outputs stay under the repository-root build/.
val rustHostJniDir = layout.buildDirectory.dir("rust/host-jni")

/**
 * The cross-endpoint loopback. The classpath the Android build writes is a declared input, read
 * in the action; the child JVM's environment is resolved there too, so no `doFirst` captures the
 * script. The file is optional at validation time so a missing classpath keeps the historical
 * message instead of Gradle's "input file does not exist".
 */
@DisableCachingByDefault(because = "verification only, runs the cross-endpoint loopback")
abstract class SessionLoopbackTask : Exec() {
    @get:InputFile
    @get:Optional
    abstract val classpathFile: RegularFileProperty

    @get:Internal
    abstract val hostJniDir: DirectoryProperty

    @TaskAction
    override fun exec() {
        val classpath = classpathFile.get().asFile
        check(classpath.isFile) {
            "sessionLoopbackCheck: missing ${classpath.path}; run ./gradlew :android:app:sessionLoopbackClasspath"
        }
        environment("VELES_LOOPBACK_CLASSPATH", classpath.readText().trim())
        environment("VELES_LOOPBACK_REQUIRED", "1")
        // The child JVM is the Gradle toolchain JVM (Java 21 per .java-version), not whatever
        // `java` happens to be first on the PATH of the npm process.
        environment(
            "VELES_LOOPBACK_JAVA",
            File(System.getProperty("java.home"), "bin/java").absolutePath,
        )
        // OTP-10: the child JVM loads the real host-JVM Rust core through
        // System.loadLibrary("veles_crypto"); the spawn turns this into
        // -Djava.library.path so the test-only `rustHostJni` output resolves.
        environment("VELES_HOST_JNI_DIR", hostJniDir.get().asFile.absolutePath)
        super.exec()
    }
}

val sessionLoopbackCheck = tasks.register<SessionLoopbackTask>("sessionLoopbackCheck") {
    group = "verification"
    description = "Runs the Kotlin phone / TypeScript client cross-endpoint session loopback."
    dependsOn(
        rustBuild.task(":rustHostJni"),
        rustBuild.task(":rustWasm"),
        androidBuild.task(":app:sessionLoopbackClasspath"),
        protocolWebInstall,
    )
    classpathFile.set(sessionLoopbackClasspathFile)
    hostJniDir.set(rustHostJniDir)
    workingDir(layout.projectDirectory.dir("web-extension"))
    commandLine(
        npmCommand,
        "exec",
        "vitest",
        "run",
        "test/session/loopback.test.ts",
        "test/gate-b/import-guard.test.ts",
        "test/gate-b/enrollment-journey.test.ts",
        "test/gate-b/records-journey.test.ts",
    )
}

val protocolFreshEvidenceCheck = tasks.register<Exec>("protocolFreshEvidenceCheck") {
    group = "verification"
    description = "Regenerates and independently checks fresh OTP-02 crypto evidence only."
    dependsOn(protocolInstall)
    inputs.files(protocolContractCheck.get().inputs.files)
    outputs.upToDateWhen { false }
    workingDir(layout.projectDirectory)
    commandLine("node", "protocol/checks/check.mjs", "--evidence-only")
    outputs.doNotCacheIf("regenerates crypto evidence") { true }
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

val bridgeNodeCheck = tasks.register<ToolchainCheckTask>("bridgeNodeCheck") {
    group = "native-bridge"
    workingDir.set(layout.projectDirectory)
    pathEnv.set(providers.environmentVariable("PATH").orElse(""))
    probes.set(
        listOf(
            ToolchainProbe(
                requiredCommands = listOf("npm"),
                versionCommand = listOf("node", "--version"),
                minimumMajorVersion = 22,
                minimumMinorVersion = 0,
                mismatchMessageFormat =
                    "Node >=22 is required by native-bridge/package.json; found '{actual}'",
                versionHint = "Node >=22.0.0",
                installHint =
                    "Install Node.js >=22.0.0 (declared in native-bridge/package.json engines.node)",
                missingCommandMessageFormat = "Could not find '{name}' on PATH. {installHint}",
            ),
        ),
    )
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
    outputs.doNotCacheIf("installs npm dependencies from the network") { true }
}

val protocolBridgeTest = tasks.register<Exec>("protocolBridgeTest") {
    group = "verification"
    description = "Runs the native-bridge OTP-02 shared-fixture consumer without Tauri."
    dependsOn(bridgeInstall)
    workingDir(bridgeDir)
    commandLine(npmCommand, "exec", "vitest", "run", "test/protocol-fixtures.test.ts")
    outputs.doNotCacheIf("runs a test") { true }
}

val bridgeFormat = tasks.register<Exec>("bridgeFormat") {
    group = "native-bridge"
    description = "Checks Prettier formatting for native-bridge/."
    dependsOn(bridgeInstall)
    workingDir(bridgeDir)
    commandLine(npmCommand, "run", "format:check")
    outputs.doNotCacheIf("verification only") { true }
}

val bridgeLint = tasks.register<Exec>("bridgeLint") {
    group = "native-bridge"
    description = "Runs ESLint for native-bridge/."
    dependsOn(bridgeInstall)
    workingDir(bridgeDir)
    commandLine(npmCommand, "run", "lint")
    outputs.doNotCacheIf("verification only") { true }
}

val bridgeTypecheck = tasks.register<Exec>("bridgeTypecheck") {
    group = "native-bridge"
    description = "Runs TypeScript type-checking for native-bridge/."
    dependsOn(bridgeInstall)
    workingDir(bridgeDir)
    commandLine(npmCommand, "run", "typecheck")
    outputs.doNotCacheIf("verification only") { true }
}

val bridgeNpmTest = tasks.register<Exec>("bridgeNpmTest") {
    group = "native-bridge"
    description = "Runs vitest source-level tests for native-bridge/."
    dependsOn(bridgeInstall)
    workingDir(bridgeDir)
    commandLine(npmCommand, "test")
    outputs.doNotCacheIf("runs a test") { true }
}

val bridgeRustToolchainCheck = tasks.register<ToolchainCheckTask>("bridgeRustToolchainCheck") {
    group = "native-bridge"
    workingDir.set(bridgeSrcTauriDir)
    pathEnv.set(providers.environmentVariable("PATH").orElse(""))
    toolchainFile.set(bridgeSrcTauriDir.file("rust-toolchain.toml"))
    probes.set(
        listOf(
            ToolchainProbe(
                requiredCommands = listOf("rustup", "cargo"),
                versionCommand = listOf("rustc", "--version"),
                expectedPrefix = "rustc 1.98.0 ",
                mismatchMessageFormat =
                    "native-bridge/src-tauri/rust-toolchain.toml requires Rust 1.98.0; " +
                        "active toolchain is '{actual}'",
                versionHint = "Rust 1.98.0",
                installHint =
                    "Install rustup and run `rustup show active-toolchain` in native-bridge/src-tauri/ " +
                        "to install the pinned toolchain",
            ),
        ),
    )
}

val bridgeRustFormat = tasks.register<Exec>("bridgeRustFormat") {
    group = "native-bridge"
    description = "Checks cargo fmt formatting for the native-bridge Rust crate."
    dependsOn(bridgeRustToolchainCheck)
    inputs.files(fileTree(bridgeSrcTauriDir) { include("src/**/*.rs", "tests/**/*.rs") })
    workingDir(bridgeSrcTauriDir)
    environment("CARGO_TARGET_DIR", layout.buildDirectory.dir("native-bridge/rust-target").get().asFile)
    commandLine("cargo", "fmt", "--all", "--", "--check")
    outputs.doNotCacheIf("verification only") { true }
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
    outputs.doNotCacheIf("verification only") { true }
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
    outputs.doNotCacheIf("runs a test") { true }
}

val bridgeTest = tasks.register("bridgeTest") {
    group = "native-bridge"
    description = "Runs npm and cargo tests for native-bridge/."
    dependsOn(bridgeNpmTest, bridgeRustFormat, bridgeRustLint, bridgeRustTest)
    outputs.doNotCacheIf("aggregates test tasks") { true }
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
    notCompatibleWithConfigurationCache(
        "release/PR packaging pipeline; always run with --no-configuration-cache, see docs/reproducible-builds.md",
    )
    outputs.doNotCacheIf("release packaging") { true }
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
// Platform-specific Tauri `--bundles` targets live in RootBuildHelpers.bundleTargetsFor:
// Windows gets WiX (.msi) plus NSIS (.exe) installer inputs; macOS gets the .app bundle plus a
// .dmg installer. It returns null on unsupported hosts so the failure is raised at *execution*
// time (in the task's doFirst) rather than at configuration time — otherwise merely configuring
// this task on Linux would break unrelated Gradle invocations such as `./gradlew tasks`,
// `bridgeFormat`, or `check`.

val bridgeBundle = tasks.register<Exec>("bridgeBundle") {
    group = "native-bridge"
    description = "Builds unsigned Tauri installer bundles (WiX/NSIS on Windows, .app/.dmg on macOS) for packaging inputs."
    // Serialize behind the raw host build so bundle and build are never sibling
    // producers of the same `src-tauri/target` tree; bundle runs after build.
    dependsOn(bridgeBuild, bridgeInstall, bridgeRustToolchainCheck)
    notCompatibleWithConfigurationCache(
        "release/PR packaging pipeline; always run with --no-configuration-cache, see docs/reproducible-builds.md",
    )
    outputs.doNotCacheIf("release packaging") { true }
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
        val targets = RootBuildHelpers.bundleTargetsFor(osName)
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
    notCompatibleWithConfigurationCache(
        "release/PR packaging pipeline; always run with --no-configuration-cache, see docs/reproducible-builds.md",
    )
    outputs.doNotCacheIf("release packaging") { true }
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
    notCompatibleWithConfigurationCache(
        "release/PR packaging pipeline; always run with --no-configuration-cache, see docs/reproducible-builds.md",
    )
    outputs.doNotCacheIf("release packaging") { true }
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

fun Exec.productionHostEnvironment(windows: Boolean) {
    workingDir(productionHostDir)
    environment("CARGO_TARGET_DIR", productionHostTargetDir.get().asFile)
    // The shipped host is self-contained for the same reason as the installer binaries:
    // a clean Windows has no vcruntime140.dll.
    if (windows) {
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

val bridgeHostToolchainCheck = tasks.register<ToolchainCheckTask>("bridgeHostToolchainCheck") {
    group = "native-bridge"
    description = "Asserts the native-bridge/host crate resolves to its pinned Rust 1.98.0 toolchain."
    workingDir.set(productionHostDir)
    pathEnv.set(providers.environmentVariable("PATH").orElse(""))
    toolchainFile.set(productionHostDir.file("rust-toolchain.toml"))
    probes.set(
        listOf(
            ToolchainProbe(
                requiredCommands = listOf("rustup", "cargo"),
                versionCommand = listOf("rustc", "--version"),
                expectedPrefix = "rustc 1.98.0 ",
                mismatchMessageFormat =
                    "native-bridge/host/rust-toolchain.toml requires Rust 1.98.0; active toolchain is '{actual}'",
                versionHint = "Rust 1.98.0",
                installHint =
                    "Install rustup and run `rustup show active-toolchain` in native-bridge/host/ " +
                        "to install the pinned toolchain",
            ),
        ),
    )
}

val bridgeHostFormat = tasks.register<Exec>("bridgeHostFormat") {
    group = "native-bridge"
    description = "Checks cargo fmt formatting for the production host crate."
    dependsOn(bridgeHostToolchainCheck)
    inputs.files(bridgeHostSources)
    productionHostEnvironment(isWindows)
    commandLine("cargo", "fmt", "--all", "--", "--check")
    outputs.doNotCacheIf("verification only") { true }
}

val bridgeHostLint = tasks.register<Exec>("bridgeHostLint") {
    group = "native-bridge"
    description = "Runs locked cargo clippy with -D warnings for the production host crate."
    dependsOn(bridgeHostToolchainCheck)
    inputs.files(bridgeHostSources)
    productionHostEnvironment(isWindows)
    commandLine("cargo", "clippy", "--all-targets", "--locked", "--", "-D", "warnings")
    outputs.doNotCacheIf("verification only") { true }
}

val bridgeHostTest = tasks.register<Exec>("bridgeHostTest") {
    group = "native-bridge"
    description = "Runs locked cargo test for the production host crate (portable plus current-OS adapter tests)."
    dependsOn(bridgeHostToolchainCheck)
    inputs.files(bridgeHostSources)
    productionHostEnvironment(isWindows)
    commandLine("cargo", "test", "--locked")
    outputs.doNotCacheIf("runs a test") { true }
}

/**
 * Builds `veles-native-host` and stages it with a checksum. The action reads only its own
 * properties, so the former `doFirst`/`doLast` pair captured nothing from the script.
 */
@DisableCachingByDefault(because = "builds and stages the production host binary")
abstract class HostBuildTask : DefaultTask() {
    @get:InputFiles
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val sources: ConfigurableFileCollection

    @get:Input
    abstract val allowedOrigins: Property<String>

    @get:Input
    abstract val executableName: Property<String>

    @get:Input
    abstract val windows: Property<Boolean>

    @get:Internal
    abstract val workingDirectory: DirectoryProperty

    @get:Internal
    abstract val cargoTargetDir: DirectoryProperty

    @get:OutputDirectory
    abstract val outputDirectory: DirectoryProperty

    @get:Inject
    abstract val execOps: ExecOperations

    @get:Inject
    abstract val fsOps: FileSystemOperations

    @TaskAction
    fun build() {
        val output = outputDirectory.get().asFile
        // A stale executable or checksum must never be mistaken for this build's product.
        fsOps.delete { delete(output) }
        execOps.exec {
            workingDir(this@HostBuildTask.workingDirectory.get().asFile)
            environment("CARGO_TARGET_DIR", cargoTargetDir.get().asFile.absolutePath)
            if (windows.get()) {
                environment("RUSTFLAGS", "-C target-feature=+crt-static")
            }
            commandLine("cargo", "build", "--locked", "--release", "--bin", "veles-native-host")
        }
        val executable = executableName.get()
        val built = cargoTargetDir.get().asFile.resolve("release/$executable")
        // Only the production executable is accepted: no fallback to the Tauri scaffold output.
        check(built.isFile) { "bridgeHostBuild: production executable missing at $built" }
        output.mkdirs()
        val staged = output.resolve(executable)
        built.copyTo(staged, overwrite = true)
        staged.setExecutable(true, false)
        val digest = MessageDigest.getInstance("SHA-256").digest(staged.readBytes())
            .joinToString("") { "%02x".format(it) }
        output.resolve("SHA256SUMS").writeText("$digest  $executable\n")
    }
}

val bridgeHostBuild = tasks.register<HostBuildTask>("bridgeHostBuild") {
    group = "native-bridge"
    description = "Builds only veles-native-host (locked, release, no features) and writes build/native-bridge/host/{exe,SHA256SUMS}."
    dependsOn(bridgeHostToolchainCheck)
    sources.from(bridgeHostSources)
    allowedOrigins.set(providers.environmentVariable("VELES_ALLOWED_ORIGINS").orElse(""))
    executableName.set(productionHostExecutable)
    windows.set(isWindows)
    workingDirectory.set(productionHostDir)
    cargoTargetDir.set(productionHostTargetDir)
    outputDirectory.set(productionHostOutputDir)
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
val installerOsName = osName
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
    val userHome = System.getProperty("user.home")
    val cargoHome = System.getenv("CARGO_HOME")?.takeIf { it.isNotBlank() } ?: "$userHome/.cargo"
    val flags = mutableListOf(
        "--remap-path-prefix=$checkout=/veles-src",
        RootBuildHelpers.registryRemapRustFlags(cargoHome, userHome),
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

val bridgeInstallerToolchainCheck = tasks.register<ToolchainCheckTask>("bridgeInstallerToolchainCheck") {
    group = "native-bridge"
    description = "Asserts the native-bridge/installer crate resolves to its pinned Rust 1.98.0 toolchain."
    workingDir.set(installerDir)
    pathEnv.set(providers.environmentVariable("PATH").orElse(""))
    toolchainFile.set(installerDir.file("rust-toolchain.toml"))
    probes.set(
        listOf(
            ToolchainProbe(
                requiredCommands = listOf("rustup", "cargo"),
                versionCommand = listOf("rustc", "--version"),
                expectedPrefix = "rustc 1.98.0 ",
                mismatchMessageFormat =
                    "native-bridge/installer/rust-toolchain.toml requires Rust 1.98.0; " +
                        "active toolchain is '{actual}'",
                versionHint = "Rust 1.98.0",
                installHint =
                    "Install rustup and run `rustup show active-toolchain` in native-bridge/installer/ " +
                        "to install the pinned toolchain",
            ),
        ),
    )
}

val installerCheckTargetDir = installerBuildRoot.map { it.dir("target/check") }

val bridgeInstallerFormat = tasks.register<CargoExecTask>("bridgeInstallerFormat") {
    group = "native-bridge"
    description = "Checks cargo fmt formatting for the installer crate."
    dependsOn(bridgeInstallerToolchainCheck)
    inputs.files(installerSources)
    workingDir(installerDir)
    extraEnv.set(installerCheckTargetDir.map { mapOf("CARGO_TARGET_DIR" to it.asFile.absolutePath) })
    commandLine("cargo", "fmt", "--all", "--", "--check")
}

val bridgeInstallerLint = tasks.register<CargoExecTask>("bridgeInstallerLint") {
    group = "native-bridge"
    description = "Runs locked cargo clippy with -D warnings for the installer crate."
    dependsOn(bridgeInstallerToolchainCheck)
    inputs.files(installerSources)
    workingDir(installerDir)
    extraEnv.set(installerCheckTargetDir.map { mapOf("CARGO_TARGET_DIR" to it.asFile.absolutePath) })
    commandLine("cargo", "clippy", "--all-targets", "--locked", "--", "-D", "warnings")
}

val bridgeInstallerTest = tasks.register<CargoExecTask>("bridgeInstallerTest") {
    group = "native-bridge"
    description = "Runs locked cargo test for the installer crate (portable plus current-OS adapter tests)."
    dependsOn(bridgeInstallerToolchainCheck)
    inputs.files(installerSources)
    workingDir(installerDir)
    extraEnv.set(installerCheckTargetDir.map { mapOf("CARGO_TARGET_DIR" to it.asFile.absolutePath) })
    commandLine("cargo", "test", "--locked")
}

val bridgeInstallerScriptsTest = tasks.register<CargoExecTask>("bridgeInstallerScriptsTest") {
    group = "native-bridge"
    description = "Runs the installer Python script suites (contracts, gate, correspondence, assembler); not run on Windows."
    enabled = installerPlatform != "windows"
    inputs.files(installerSources)
    workingDir(layout.projectDirectory)
    extraEnv.set(mapOf("PYTHONDONTWRITEBYTECODE" to "1"))
    commandLine(installerPython, "-I", "-m", "unittest", "discover", "-s", "native-bridge/installer/scripts", "-p", "test_*.py")
}

/** Records the native compiler/SDK identities of this machine; it only records, never judges. */
@DisableCachingByDefault(because = "observes this host's tool identities")
abstract class ToolObservationTask : DefaultTask() {
    @get:InputFiles
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val sources: ConfigurableFileCollection

    @get:Input
    abstract val supported: Property<Boolean>

    @get:Input
    abstract val python: Property<String>

    @get:InputFile
    abstract val script: RegularFileProperty

    @get:Input
    abstract val target: Property<String>

    @get:OutputFile
    abstract val observation: RegularFileProperty

    @get:Internal
    abstract val workingDirectory: DirectoryProperty

    @get:Inject
    abstract val execOps: ExecOperations

    @get:Inject
    abstract val fsOps: FileSystemOperations

    @TaskAction
    fun observe() {
        check(supported.get()) {
            "OTP-25 installer inputs are built on a Windows or macOS runner; Linux is not a producer target."
        }
        val output = observation.get().asFile
        fsOps.delete { delete(output) }
        execOps.exec {
            workingDir(workingDirectory.get().asFile)
            commandLine(
                python.get(), "-I", script.get().asFile.absolutePath, "observe",
                "--target", target.get(), "--output", output.absolutePath,
            )
        }
    }
}

val bridgeInstallerToolObservation = tasks.register<ToolObservationTask>("bridgeInstallerToolObservation") {
    group = "native-bridge"
    description = "Observes the native compiler/SDK identities of THIS machine into build/ (records only; does not judge)."
    sources.from(installerSources)
    supported.set(installerPlatform != "unsupported")
    python.set(installerPython)
    script.set(installerToolsScript)
    target.set(installerToolTarget)
    observation.set(installerToolObservation)
    outputs.upToDateWhen { false }
    // The crate's rust-toolchain.toml must select the toolchain the build uses,
    // not the caller's global default (N6).
    workingDirectory.set(installerDir)
}

/** Fails unless the observed tools equal config/tools.lock.json. */
@DisableCachingByDefault(because = "checks this host's tool identities against the lock")
abstract class ToolCheckTask : DefaultTask() {
    @get:InputFiles
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val sources: ConfigurableFileCollection

    @get:Input
    abstract val python: Property<String>

    @get:InputFile
    abstract val script: RegularFileProperty

    @get:Input
    abstract val target: Property<String>

    @get:InputFile
    abstract val observed: RegularFileProperty

    @get:Internal
    abstract val workingDirectory: DirectoryProperty

    @get:Inject
    abstract val execOps: ExecOperations

    @TaskAction
    fun checkTools() {
        execOps.exec {
            workingDir(workingDirectory.get().asFile)
            commandLine(
                python.get(), "-I", script.get().asFile.absolutePath, "check",
                "--target", target.get(), "--observed", observed.get().asFile.absolutePath,
            )
        }
    }
}

val bridgeInstallerToolCheck = tasks.register<ToolCheckTask>("bridgeInstallerToolCheck") {
    group = "native-bridge"
    description = "Fails unless the observed tools equal config/tools.lock.json; unresolved Windows identities always fail."
    dependsOn(bridgeInstallerToolObservation)
    sources.from(installerSources)
    python.set(installerPython)
    script.set(installerToolsScript)
    target.set(installerToolTarget)
    observed.set(installerToolObservation)
    workingDirectory.set(layout.projectDirectory)
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
    // The whole producer graph runs only in guarded release/PR invocations (design D2/D6), so it is
    // opted out of the configuration cache as a unit instead of being rewritten. The reason string is
    // the one docs/reproducible-builds.md and evidence/cc-optouts.md carry.
    fun Task.markPackagingPipeline() {
        notCompatibleWithConfigurationCache(
            "release/PR packaging pipeline; always run with --no-configuration-cache, see docs/reproducible-builds.md",
        )
        outputs.doNotCacheIf("release packaging") { true }
    }
    val configCheck = tasks.register<Exec>("${taskName}Config") {
        group = "native-bridge"
        markPackagingPipeline()
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
            markPackagingPipeline()
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
            markPackagingPipeline()
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
            markPackagingPipeline()
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
            markPackagingPipeline()
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
            markPackagingPipeline()
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
            markPackagingPipeline()
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
            markPackagingPipeline()
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
            markPackagingPipeline()
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
            markPackagingPipeline()
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
            markPackagingPipeline()
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
            markPackagingPipeline()
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
            markPackagingPipeline()
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
            markPackagingPipeline()
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
            markPackagingPipeline()
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
        markPackagingPipeline()
        description = summary
        dependsOn(slotFinishers)
        inputs.files(config)
        // The published tree.tar/provenance.json are made from both slots' final assemblies and carry
        // the installer version: a changed version or slot tree must re-run the finalizer.
        inputs.property("installerVersion", installerVersion)
        inputs.files(slotOutputs.values)
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
    outputs.doNotCacheIf("runs the supply-chain policy") { true }
}
// ---- OTP-25 installer producers (end) ----

tasks.named("check") {
    dependsOn(
        "rustFormat",
        "rustLint",
        "rustTest",
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
    dependsOn(
        protocolContractCheck,
        protocolRustTest,
        protocolWebTest,
        protocolBridgeTest,
        androidBuild.task(":app:protocolJvmTest"),
        sessionLoopbackCheck,
    )
    outputs.doNotCacheIf("aggregates verification tasks") { true }
}

tasks.named<Delete>("clean") {
    // Android's app output and the Android build root are owned by the included android build.
    dependsOn(androidBuild.task(":app:clean"), androidBuild.task(":clean"))
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
        // Rust JNI/WASM staged package (the included Rust build's rustPackage producer).
        layout.buildDirectory.dir("rust-package"),
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
