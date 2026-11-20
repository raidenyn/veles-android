// The Rust workspace is an independent Gradle build: the repository root includes it as
// `includeBuild("rust")` and, after the layout cut-over, android/ includes it as
// `includeBuild("../rust")`. Task names are unchanged and every product stays under the
// repository-root build/ (`build/rust/target`, `build/rust/host-jni`, `build/rust-tools`,
// `build/rust/jniLibs`, ...) so CI cache paths, `rust/scripts/assert-clean.sh`,
// `rust/scripts/verify-apk-jni.sh` and the docs keep working.
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.StringReader
import java.security.MessageDigest
import java.util.Properties
import javax.inject.Inject
import org.gradle.api.DefaultTask
import org.gradle.api.GradleException
import org.gradle.api.file.ConfigurableFileCollection
import org.gradle.api.file.Directory
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.file.FileSystemOperations
import org.gradle.api.file.RegularFile
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.provider.ListProperty
import org.gradle.api.provider.MapProperty
import org.gradle.api.provider.Property
import org.gradle.api.provider.Provider
import org.gradle.api.provider.ProviderFactory
import org.gradle.api.provider.ValueSource
import org.gradle.api.provider.ValueSourceParameters
import org.gradle.api.tasks.CacheableTask
import org.gradle.api.tasks.Exec
import org.gradle.api.tasks.Input
import org.gradle.api.tasks.InputFile
import org.gradle.api.tasks.InputFiles
import org.gradle.api.tasks.Internal
import org.gradle.api.tasks.Optional
import org.gradle.api.tasks.OutputDirectory
import org.gradle.api.tasks.PathSensitive
import org.gradle.api.tasks.PathSensitivity
import org.gradle.api.tasks.Sync
import org.gradle.api.tasks.TaskAction
import org.gradle.process.ExecOperations
import org.gradle.work.DisableCachingByDefault

val repoRoot = layout.settingsDirectory.dir("..")
val rustDir = layout.projectDirectory
val rustCrateDir = rustDir.dir("veles-crypto")
val rustToolsFile = rustDir.file("toolchain-tools.toml")
// The repository-root build directory. A plain Directory lets the registrations compute tool paths
// while the build is configured without resolving a provider; every output stays under build/.
val repoBuildDir: Directory = repoRoot.dir("build")
val rustTargetPath: String = repoRoot.dir("build").dir("rust/target").asFile.absolutePath
val wasmPackageDir = repoRoot.dir("web-extension/rust-wasm/pkg")
// os.name and os.arch are configuration-time provider reads: they select the executable suffix,
// the host library name and the cacheability of rustHostJni, so the entry records them as inputs.
val osName: String = providers.systemProperty("os.name").get()
val windowsExecutableSuffix = if (osName.startsWith("Windows")) ".exe" else ""
val isWindows = osName.startsWith("Windows")
// Single source of truth for the Android NDK version: android/app/build.gradle.kts reads the
// same file. The content is read through a provider; only the task actions resolve it.
val ndkVersionProvider: Provider<String> =
    providers.fileContents(rustDir.file("ndk-version")).asText.map { it.trim() }

/**
 * Helpers shared by the typed tasks below. Top-level functions in a `.kts` script are members of
 * the script class, so calling one from a task action, a `map {}` lambda or a `doNotCacheIf`
 * predicate captures the script and breaks the configuration cache. Every helper therefore lives
 * in this object and takes plain values.
 */
object RustBuildHelpers {
    fun executableSuffix(osName: String): String = if (osName.startsWith("Windows")) ".exe" else ""

    fun pinnedVersion(toml: String, name: String, file: String): String {
        val pattern = Regex("(?m)^${Regex.escape(name)}\\s*=\\s*\"([^\"]+)\"\\s*$")
        return requireNotNull(pattern.find(toml)) {
            "Missing '$name' pin in $file"
        }.groupValues[1]
    }

    /** Reads a pin from `toolchain-tools.toml` lazily; nothing is read at configuration time. */
    fun pinnedVersionProvider(
        providers: ProviderFactory,
        toolsFile: RegularFile,
        name: String,
    ): Provider<String> {
        val file = toolsFile
        return providers.fileContents(file).asText.map { pinnedVersion(it, name, file.asFile.toString()) }
    }

    /**
     * Prepends the entries to the inherited PATH in order, so the first entry wins, matching the
     * former `File.prependToPath` chain.
     */
    fun prependedPath(entries: List<String>, existing: String?): String {
        val prefix = entries.joinToString(File.pathSeparator)
        return if (prefix.isEmpty()) existing ?: "" else prefix + File.pathSeparator + (existing ?: "")
    }

    fun requireOnPath(
        path: String,
        name: String,
        suffix: String,
        versionHint: String,
        installHint: String,
    ) {
        val found = path.split(File.pathSeparator).any { dir ->
            File(dir, name + suffix).let { it.isFile && it.canExecute() }
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

    fun registryRemapRustFlags(cargoHome: String?, userHome: String): String {
        val home: File = if (cargoHome.isNullOrBlank()) {
            File(userHome, ".cargo")
        } else {
            File(cargoHome)
        }
        val registrySrc = File(home, "registry/src").absolutePath
        return "--remap-path-prefix=$registrySrc=/cargo-registry/src"
    }

    private fun sdkDirNdkLocation(
        propertiesFile: File,
        contents: String?,
        ndkVersion: String,
    ): Pair<String, File>? {
        if (contents == null) return null
        val properties = Properties().apply { load(StringReader(contents)) }
        return properties.getProperty("sdk.dir")?.takeIf { it.isNotBlank() }?.let {
            propertiesFile.parentFile.path + " sdk.dir" to File(it, "ndk/$ndkVersion")
        }
    }

    private fun ndkSearchLocations(lookup: NdkLookup): List<Pair<String, File>> {
        val locations = mutableListOf<Pair<String, File>>()
        lookup.env["ANDROID_NDK_HOME"]?.takeIf { it.isNotBlank() }?.let {
            locations += "ANDROID_NDK_HOME" to File(it)
        }
        lookup.env["ANDROID_SDK_ROOT"]?.takeIf { it.isNotBlank() }?.let {
            locations += "ANDROID_SDK_ROOT" to File(it, "ndk/${lookup.ndkVersion}")
        }
        lookup.env["ANDROID_HOME"]?.takeIf { it.isNotBlank() }?.let {
            locations += "ANDROID_HOME" to File(it, "ndk/${lookup.ndkVersion}")
        }
        listOf(
            File(lookup.rootDir, "local.properties"),
            File(File(lookup.rootDir, "android"), "local.properties"),
        ).forEachIndexed { index, propertiesFile ->
            val contents = if (index == 0) {
                lookup.rootLocalProperties
            } else {
                lookup.androidLocalProperties
            }
            sdkDirNdkLocation(propertiesFile, contents, lookup.ndkVersion)?.let { locations += it }
        }
        return locations
    }

    /**
     * Resolves the pinned NDK directory at execution time (never at configuration time, so a
     * machine without the NDK can still configure and run the non-Android Rust tasks), in this
     * order: ANDROID_NDK_HOME, then $ANDROID_SDK_ROOT/ndk/<version>, then
     * $ANDROID_HOME/ndk/<version>, then `sdk.dir` of the repository-root local.properties, then
     * `sdk.dir` of android/local.properties (the file the independent Android build reads).
     */
    fun resolveNdkDirectory(lookup: NdkLookup): File {
        val locations = ndkSearchLocations(lookup)
        locations.firstOrNull { it.second.isDirectory }?.let { return it.second }
        val searched = locations.joinToString(separator = ", ") { it.second.absolutePath }
            .ifEmpty {
                "(no ANDROID_NDK_HOME, ANDROID_SDK_ROOT, ANDROID_HOME, " +
                    "repository-root local.properties sdk.dir or android/local.properties sdk.dir)"
            }
        throw GradleException("NDK ${lookup.ndkVersion} not found; searched: $searched")
    }

    /**
     * Returns why a cacheable Rust task must not use the build cache, or null when it may. The
     * environment is the one inherited from the Gradle process. The task sets its own RUSTFLAGS
     * and CARGO_PROFILE_RELEASE_PANIC, but an inherited `CARGO_PROFILE_*` (including
     * CARGO_PROFILE_RELEASE_PANIC), `CARGO_BUILD_*` or `CARGO_TARGET_*` still refuses caching:
     * the spec's rule is deliberately conservative because other profile and build settings can
     * change the output.
     */
    fun cacheRefusal(
        env: Map<String, String>,
        cargoHome: String?,
        userHome: String,
        rustDir: File,
    ): String? {
        val denied = setOf(
            "CARGO_ENCODED_RUSTFLAGS",
            "CARGO_BUILD_RUSTFLAGS",
            "CARGO_INCREMENTAL",
            "RUSTC",
            "RUSTC_WRAPPER",
            "RUSTC_WORKSPACE_WRAPPER",
            "RUSTC_BOOTSTRAP",
            "RUSTUP_TOOLCHAIN",
            "MACOSX_DEPLOYMENT_TARGET",
            "SDKROOT",
            "CC",
            "CXX",
            "AR",
            "CFLAGS",
            "CXXFLAGS",
            "LDFLAGS",
        )
        for ((name, value) in env) {
            if (value.isEmpty()) continue
            if (name in denied ||
                name.startsWith("CARGO_PROFILE_") ||
                name.startsWith("CARGO_BUILD_") ||
                name.startsWith("CC_") ||
                name.startsWith("CFLAGS_") ||
                name.startsWith("AR_") ||
                (name.startsWith("CARGO_TARGET_") && name != "CARGO_TARGET_DIR")
            ) {
                return "the inherited environment variable $name may change the output"
            }
        }
        val cargoHomeDir = if (cargoHome.isNullOrBlank()) File(userHome, ".cargo") else File(cargoHome)
        for (name in listOf("config", "config.toml")) {
            val candidate = File(cargoHomeDir, name)
            if (candidate.isFile) return "the cargo configuration file $candidate may change the output"
        }
        var directory: File? = rustDir.absoluteFile
        while (directory != null) {
            for (name in listOf("config", "config.toml")) {
                val candidate = File(File(directory, ".cargo"), name)
                if (candidate.isFile) return "the cargo configuration file $candidate may change the output"
            }
            directory = directory.parentFile
        }
        return null
    }

    /**
     * Builds the lazily wired NDK lookup: environment keys and the two local.properties contents
     * stay unresolved until the task action asks for the value.
     */
    fun ndkLookupProvider(
        providers: ProviderFactory,
        repoRoot: File,
        rootLocalProperties: Provider<String>,
        androidLocalProperties: Provider<String>,
        ndkVersion: Provider<String>,
    ): Provider<NdkLookup> {
        val rootDir = repoRoot
        val envValues = providers.environmentVariable("ANDROID_NDK_HOME").orElse("")
            .zip(providers.environmentVariable("ANDROID_SDK_ROOT").orElse("")) { ndk, sdk ->
                listOf(ndk, sdk)
            }
            .zip(providers.environmentVariable("ANDROID_HOME").orElse("")) { values, home ->
                values + home
            }
        return envValues.zip(rootLocalProperties) { env, root -> env to root }
            .zip(androidLocalProperties) { pair, android -> Triple(pair.first, pair.second, android) }
            .zip(ndkVersion) { (env, root, android), version ->
                NdkLookup(
                    env = mapOf(
                        "ANDROID_NDK_HOME" to env[0],
                        "ANDROID_SDK_ROOT" to env[1],
                        "ANDROID_HOME" to env[2],
                    ),
                    rootLocalProperties = root,
                    androidLocalProperties = android,
                    rootDir = rootDir,
                    ndkVersion = version,
                )
            }
    }

    /** Runs a version command and returns its trimmed standard output. */
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
     * Shared toolchain check for ToolchainCheckTask and the rustWasmSmoke preflight. When
     * `minimumMajorVersion` is set the check is `major >= minimumMajorVersion` (Node); otherwise
     * the version output must start with `expectedPrefix` (Rust and npm).
     */
    fun verifyToolchain(
        exec: ExecOperations,
        workingDir: File,
        pathEnv: String,
        requiredCommands: List<String>,
        versionCommand: List<String>,
        expectedPrefix: String,
        minimumMajorVersion: Int?,
        describeCommand: List<String>,
        mismatchMessageFormat: String,
        versionHint: String,
        installHint: String,
    ) {
        val suffix = executableSuffix(System.getProperty("os.name"))
        for (name in requiredCommands) {
            requireOnPath(pathEnv, name, suffix, versionHint, installHint)
        }
        val detail = if (describeCommand.isEmpty()) null else probe(exec, workingDir, describeCommand)
        val version = probe(exec, workingDir, versionCommand)
        val matches = if (minimumMajorVersion == null) {
            version.startsWith(expectedPrefix)
        } else {
            val major = version.removePrefix("v").substringBefore('.').toIntOrNull()
            major != null && major >= minimumMajorVersion
        }
        check(matches) {
            mismatchMessageFormat
                .replace("{actual}", version)
                .replace("{detail}", detail ?: "")
        }
    }
}

/**
 * Everything the NDK lookup needs, captured at configuration time through providers and resolved
 * inside the task action. Serializable so the configuration cache can store it.
 */
data class NdkLookup(
    val env: Map<String, String>,
    val rootLocalProperties: String?,
    val androidLocalProperties: String?,
    val rootDir: File,
    val ndkVersion: String,
) : java.io.Serializable

/** Data for the optional toolchain preflight of CargoExecTask. */
data class ToolchainProbe(
    val requiredCommands: List<String>,
    val versionCommand: List<String>,
    val expectedPrefix: String,
    val minimumMajorVersion: Int?,
    val describeCommand: List<String>,
    val mismatchMessageFormat: String,
    val versionHint: String,
    val installHint: String,
) : java.io.Serializable

/**
 * Checks that the host toolchain is installed and of the pinned version. One shape covers the
 * Rust, Node and npm checks; `describeCommand` only feeds the mismatch message.
 */
@DisableCachingByDefault(because = "probes the host toolchain")
abstract class ToolchainCheckTask : DefaultTask() {
    @get:Input
    abstract val requiredCommands: ListProperty<String>

    @get:Input
    abstract val versionCommand: ListProperty<String>

    @get:Input
    abstract val expectedPrefix: Property<String>

    @get:Input
    abstract val pathEnv: Property<String>

    @get:Input
    abstract val versionHint: Property<String>

    @get:Input
    abstract val installHint: Property<String>

    @get:Input
    @get:Optional
    abstract val mismatchMessageFormat: Property<String>

    @get:Input
    @get:Optional
    abstract val describeCommand: ListProperty<String>

    @get:Internal
    abstract val workingDir: DirectoryProperty

    @get:InputFile
    @get:Optional
    abstract val toolchainFile: RegularFileProperty

    @get:Inject
    abstract val execOps: ExecOperations

    @TaskAction
    fun checkToolchain() {
        RustBuildHelpers.verifyToolchain(
            exec = execOps,
            workingDir = workingDir.get().asFile,
            pathEnv = pathEnv.get(),
            requiredCommands = requiredCommands.get(),
            versionCommand = versionCommand.get(),
            expectedPrefix = expectedPrefix.get(),
            minimumMajorVersion = null,
            describeCommand = describeCommand.getOrElse(emptyList()),
            mismatchMessageFormat = mismatchMessageFormat.getOrElse(
                "Expected ${expectedPrefix.get()}, got: {actual}",
            ),
            versionHint = versionHint.get(),
            installHint = installHint.get(),
        )
    }
}

/**
 * Installs an exact-pinned cargo CLI into the repository build directory. The network access is
 * why the task is never cacheable.
 */
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
            // cargo-ndk refuses to run unless it detects it was launched via cargo (it checks the
            // CARGO env var); wasm-pack and wasm-bindgen ignore this, so setting it unconditionally
            // keeps this verify step tool-agnostic.
            environment("CARGO", "cargo")
            setCommandLine(listOf(binary.get().asFile.absolutePath) + versionArguments.get())
            standardOutput = output
        }
        val text = output.toString(Charsets.UTF_8).trim()
        // Extract the first whitespace-delimited token after the tool name and compare it as an
        // exact semver, not a substring. `contains` would accept 4.1.20 when expecting 4.1.2.
        val actualVersion = text.split(Regex("\\s+"))
            .firstOrNull { it.matches(Regex("\\d+\\.\\d+\\.\\d+(-\\S+)?")) }
        check(actualVersion == version.get()) {
            "Expected ${crateName.get()} ${version.get()} at ${binary.get().asFile}, got: $text"
        }
    }
}

/** Verifies that the wasm-bindgen crate in Cargo.lock matches the wasm-bindgen-cli pin. */
@DisableCachingByDefault(because = "verifies that two pinned tool versions agree")
abstract class BindgenConsistencyTask : DefaultTask() {
    @get:InputFile
    abstract val lockFile: RegularFileProperty

    @get:Input
    abstract val cliVersion: Property<String>

    @TaskAction
    fun verify() {
        // Parse the resolved wasm-bindgen version from Cargo.lock and verify it exactly matches
        // the wasm-bindgen-cli pin. The spec requires the CLI and runtime to be in lock-step.
        val lock = lockFile.get().asFile.readText()
        val lockVersion = Regex(
            "(?ms)^name = \"wasm-bindgen\"$.*?^version = \"([^\"]+)\"$",
        ).find(lock)?.groupValues?.get(1)
        val pinnedCliVersion = cliVersion.get()
        check(lockVersion == pinnedCliVersion) {
            "wasm-bindgen ($lockVersion in Cargo.lock) must match " +
                "wasm-bindgen-cli ($pinnedCliVersion in toolchain-tools.toml)."
        }
    }
}

/**
 * An `Exec` whose environment is resolved inside the action from plain values and providers,
 * replacing the former `doFirst { environment(...) }` blocks. Never cacheable: these tasks only
 * verify, test or build conformance artifacts outside the product outputs.
 */
@DisableCachingByDefault(because = "verification or test-only build")
abstract class CargoExecTask : Exec() {
    @get:Internal
    abstract val ndkLookup: Property<NdkLookup>

    @get:Internal
    abstract val prependPath: ListProperty<String>

    @get:Internal
    abstract val extraEnv: MapProperty<String, String>

    @get:Internal
    abstract val toolchainCheck: Property<ToolchainProbe>

    @get:Internal
    abstract val deleteBefore: ConfigurableFileCollection

    @get:Inject
    abstract val fs: FileSystemOperations

    @get:Inject
    abstract val execOps: ExecOperations

    @TaskAction
    override fun exec() {
        fs.delete { delete(deleteBefore) }
        if (ndkLookup.isPresent) {
            val lookup = ndkLookup.get()
            val ndkDir = RustBuildHelpers.resolveNdkDirectory(lookup)
            RustBuildHelpers.requireNdkVersion(ndkDir, lookup.ndkVersion)
            environment("ANDROID_NDK_HOME", ndkDir.absolutePath)
        }
        val prepended = prependPath.get()
        if (prepended.isNotEmpty()) {
            environment("PATH", RustBuildHelpers.prependedPath(prepended, System.getenv("PATH")))
        }
        environment(extraEnv.get())
        if (toolchainCheck.isPresent) {
            val probe = toolchainCheck.get()
            RustBuildHelpers.verifyToolchain(
                exec = execOps,
                workingDir = workingDir,
                pathEnv = System.getenv("PATH") ?: "",
                requiredCommands = probe.requiredCommands,
                versionCommand = probe.versionCommand,
                expectedPrefix = probe.expectedPrefix,
                minimumMajorVersion = probe.minimumMajorVersion,
                describeCommand = probe.describeCommand,
                mismatchMessageFormat = probe.mismatchMessageFormat,
                versionHint = probe.versionHint,
                installHint = probe.installHint,
            )
        }
        super.exec()
    }
}

/** `rustc -vV` in rust/: the exact toolchain build is part of the cache key. */
abstract class RustcVersionValueSource : ValueSource<String, RustcVersionValueSource.Params> {
    interface Params : ValueSourceParameters {
        val workingDir: DirectoryProperty
    }

    @get:Inject
    abstract val execOperations: ExecOperations

    override fun obtain(): String {
        val output = ByteArrayOutputStream()
        execOperations.exec {
            workingDir(parameters.workingDir.get().asFile)
            commandLine("rustc", "-vV")
            standardOutput = output
        }
        return output.toString(Charsets.UTF_8).trim()
    }
}

/** First line of `cc --version`: the host linker identity for the rustHostJni key. */
abstract class HostLinkerValueSource : ValueSource<String, HostLinkerValueSource.Params> {
    interface Params : ValueSourceParameters {
        val workingDir: DirectoryProperty
    }

    @get:Inject
    abstract val execOperations: ExecOperations

    override fun obtain(): String {
        val output = ByteArrayOutputStream()
        execOperations.exec {
            workingDir(parameters.workingDir.get().asFile)
            commandLine("cc", "--version")
            standardOutput = output
        }
        return output.toString(Charsets.UTF_8).trim().lineSequence().firstOrNull() ?: ""
    }
}

/**
 * The only cacheable shape: a product build whose key is path-free. Absolute tool paths, the
 * working directory, CARGO_TARGET_DIR and the registry-remap RUSTFLAGS are `@Internal`, while the
 * arguments, the file tree and the toolchain identities are inputs.
 */
@CacheableTask
abstract class CargoBuildTask : DefaultTask() {
    @get:Input
    abstract val preArguments: ListProperty<String>

    @get:Input
    abstract val postArguments: ListProperty<String>

    @get:Input
    abstract val outputFlag: Property<String>

    @get:Input
    abstract val panicStrategy: Property<String>

    @get:Input
    abstract val rustcVersion: Property<String>

    @get:Input
    @get:Optional
    abstract val ndkVersion: Property<String>

    @get:Input
    @get:Optional
    abstract val hostIdentity: Property<String>

    @get:InputFiles
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val sources: ConfigurableFileCollection

    @get:InputFile
    @get:PathSensitive(PathSensitivity.RELATIVE)
    @get:Optional
    abstract val toolsFile: RegularFileProperty

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @get:Internal
    abstract val executable: Property<String>

    @get:Internal
    abstract val workingDir: DirectoryProperty

    @get:Internal
    abstract val targetDir: DirectoryProperty

    @get:Internal
    abstract val remapRustFlags: Property<String>

    @get:Internal
    abstract val prependPath: ListProperty<String>

    @get:Internal
    abstract val ndkLookup: Property<NdkLookup>

    @get:Internal
    abstract val inheritedEnv: MapProperty<String, String>

    @get:Internal
    abstract val cargoHome: Property<String>

    @get:Internal
    abstract val userHome: Property<String>

    @get:Internal
    abstract val copyFrom: RegularFileProperty

    @get:Inject
    abstract val execOps: ExecOperations

    @get:Inject
    abstract val fsOps: FileSystemOperations

    @TaskAction
    fun build() {
        val output = outputDir.get().asFile
        fsOps.delete { delete(output) }
        var ndkHome: String? = null
        if (ndkLookup.isPresent) {
            val lookup = ndkLookup.get()
            val ndkDir = RustBuildHelpers.resolveNdkDirectory(lookup)
            RustBuildHelpers.requireNdkVersion(ndkDir, lookup.ndkVersion)
            ndkHome = ndkDir.absolutePath
        }
        val arguments = buildList {
            add(executable.get())
            addAll(preArguments.get())
            val flag = outputFlag.get()
            if (flag.isNotEmpty()) {
                add(flag)
                add(output.absolutePath)
            }
            addAll(postArguments.get())
        }
        val env = mutableMapOf<String, String>()
        env["CARGO_TARGET_DIR"] = targetDir.get().asFile.absolutePath
        env["CARGO_PROFILE_RELEASE_PANIC"] = panicStrategy.get()
        env["RUSTFLAGS"] = remapRustFlags.get()
        val prepended = prependPath.get()
        if (prepended.isNotEmpty()) {
            env["PATH"] = RustBuildHelpers.prependedPath(prepended, System.getenv("PATH"))
        }
        if (ndkHome != null) {
            env["ANDROID_NDK_HOME"] = ndkHome
        }
        execOps.exec {
            workingDir(this@CargoBuildTask.workingDir.get().asFile)
            environment(env)
            setCommandLine(arguments)
        }
        if (copyFrom.isPresent) {
            val built = copyFrom.get().asFile
            check(built.isFile) { "rustHostJni: expected the host library at $built" }
            output.mkdirs()
            built.copyTo(File(output, built.name), overwrite = true)
        }
    }
}

// The lazily wired NDK lookup shared by every task that needs the NDK. Only a task action resolves
// it, so configuring the build reads no local.properties and checks no directory.
val rustNdkLookup: Provider<NdkLookup> = RustBuildHelpers.ndkLookupProvider(
    providers = providers,
    repoRoot = repoRoot.asFile,
    rootLocalProperties = providers.fileContents(repoRoot.file("local.properties")).asText.orElse(""),
    androidLocalProperties = providers.fileContents(repoRoot.dir("android").file("local.properties")).asText
        .orElse(""),
    ndkVersion = ndkVersionProvider,
)

// One file tree for the three cacheable products, with relative path sensitivity so worktrees at
// different paths share cache keys. rust/Cargo.toml and veles-crypto/Cargo.toml keep distinct
// relative paths.
val rustSources = fileTree(rustDir) {
    include(
        "Cargo.toml",
        "Cargo.lock",
        "rust-toolchain.toml",
        "veles-crypto/Cargo.toml",
        "veles-crypto/src/**/*.rs",
    )
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
// The registry remap is what makes the output independent of the absolute CARGO_HOME, so it is
// @Internal on the task and never part of the cache key.
val registryRemapFlags: Provider<String> = providers.environmentVariable("CARGO_HOME").orElse("")
    .zip(providers.systemProperty("user.home").orElse("")) { cargoHome, userHome ->
        RustBuildHelpers.registryRemapRustFlags(cargoHome, userHome)
    }

val rustcVersionProvider: Provider<String> = providers.of(RustcVersionValueSource::class.java) {
    parameters.workingDir.set(rustDir)
}

val hostLinkerProvider: Provider<String> = providers.of(HostLinkerValueSource::class.java) {
    parameters.workingDir.set(rustDir)
}

// "os|architecture|linker" off Windows; on Windows the MSVC linker identity is not probed and
// rustHostJni is not cacheable at all.
val hostIdentityProvider: Provider<String> = if (isWindows) {
    objects.property(String::class.java).apply { set("windows") }
} else {
    providers.systemProperty("os.name")
        .zip(providers.systemProperty("os.arch")) { name, arch -> "$name|$arch" }
        .zip(hostLinkerProvider) { base, linker -> "$base|$linker" }
}

val rustToolchainCheck = tasks.register<ToolchainCheckTask>("rustToolchainCheck") {
    group = "rust"
    val rustToolchainFile = rustDir.file("rust-toolchain.toml")
    requiredCommands.set(listOf("rustup", "cargo"))
    versionCommand.set(listOf("rustc", "--version"))
    expectedPrefix.set("rustc 1.98.0 ")
    pathEnv.set(providers.environmentVariable("PATH").orElse(""))
    versionHint.set("Rust 1.98.0")
    installHint.set(
        "Install rustup and run `rustup show active-toolchain` in rust/ to install the pinned toolchain",
    )
    toolchainFile.set(rustToolchainFile)
    workingDir.set(rustDir)
    describeCommand.set(listOf("rustup", "show", "active-toolchain"))
    mismatchMessageFormat.set(
        "rust/rust-toolchain.toml requires Rust 1.98.0; active toolchain is '{detail}' ({actual})",
    )
}

fun registerCargoTool(
    taskStem: String,
    toolName: String,
    binaryName: String,
): Pair<TaskProvider<CargoInstallTask>, TaskProvider<VerifyToolVersionTask>> {
    val toolCrate = toolName
    val toolVersion = RustBuildHelpers.pinnedVersionProvider(providers, rustToolsFile, toolCrate)
    val installRootDir = repoBuildDir.dir("rust-tools/$toolCrate")
    val toolTargetDir = repoBuildDir.dir("rust/tool-install-target/$toolCrate")
    val binaryFile = installRootDir.file("bin/$binaryName$windowsExecutableSuffix")
    val install = tasks.register<CargoInstallTask>("install$taskStem") {
        group = "rust"
        description = "Installs $toolCrate into the build directory."
        dependsOn("rustToolchainCheck")
        val toolchain = rustDir.file("rust-toolchain.toml")
        crateName.set(toolCrate)
        version.set(toolVersion)
        toolchainFile.set(toolchain)
        installRoot.set(installRootDir)
        targetDir.set(toolTargetDir)
        workingDir.set(rustDir)
    }
    val verify = tasks.register<VerifyToolVersionTask>("verify$taskStem") {
        group = "rust"
        dependsOn(install)
        crateName.set(toolCrate)
        version.set(toolVersion)
        binary.set(binaryFile)
        versionArguments.set(listOf("--version"))
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

val rustWasmBindgenConsistency = tasks.register<BindgenConsistencyTask>("rustWasmBindgenConsistency") {
    group = "rust"
    dependsOn(verifyWasmBindgen)
    val cargoLock = rustDir.file("Cargo.lock")
    val cliPin = RustBuildHelpers.pinnedVersionProvider(
        providers,
        rustToolsFile,
        "wasm-bindgen-cli",
    )
    lockFile.set(cargoLock)
    cliVersion.set(cliPin)
}

val rustInstall = tasks.register("rustInstall") {
    group = "rust"
    description = "Installs and verifies all exact-pinned Rust build CLIs."
    dependsOn(verifyCargoNdk, verifyWasmPack, rustWasmBindgenConsistency)
    outputs.doNotCacheIf("aggregates install and verify tasks") { true }
}

val rustWasmLockCheck = tasks.register<CargoExecTask>("rustWasmLockCheck") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir.asFile)
    extraEnv.set(mapOf("CARGO_TARGET_DIR" to rustTargetPath))
    // wasm-pack 0.15 runs `cargo metadata` (unlocked) before forwarding --locked
    // to cargo build. Preflight with --locked here so lock drift fails before
    // wasm-pack can touch anything.
    commandLine("cargo", "metadata", "--locked", "--no-deps", "--manifest-path", "Cargo.toml")
}

// The registry-path remap for a byte-reproducible WASM binary (see registryRemapFlags):
// wasm-bindgen panic locations embed ${CARGO_HOME}/registry/src/... into veles_crypto_bg.wasm, so
// builds with different CARGO_HOME produce different bytes without it.
val rustWasm = tasks.register<CargoBuildTask>("rustWasm") {
    group = "rust"
    description = "Builds the locked web-target WASM package."
    dependsOn(verifyWasmPack, verifyWasmBindgen, rustWasmBindgenConsistency, rustWasmLockCheck)
    val wasmPackBin = repoBuildDir.dir("rust-tools/wasm-pack/bin").file("wasm-pack$windowsExecutableSuffix")
    val bindgenBin = repoBuildDir.dir("rust-tools/wasm-bindgen-cli/bin")
    sources.from(rustSources)
    toolsFile.set(rustToolsFile)
    outputDir.set(wasmPackageDir)
    executable.set(wasmPackBin.asFile.absolutePath)
    workingDir.set(rustDir)
    targetDir.set(repoBuildDir.dir("rust/target"))
    prependPath.set(listOf(wasmPackBin.asFile.parentFile.absolutePath, bindgenBin.asFile.absolutePath))
    panicStrategy.set("abort")
    remapRustFlags.set(registryRemapFlags)
    preArguments.set(
        listOf("build", "veles-crypto", "--target", "web", "--release", "--mode", "no-install", "--no-opt"),
    )
    postArguments.set(listOf("--", "--locked"))
    outputFlag.set("--out-dir")
    rustcVersion.set(rustcVersionProvider)
    inheritedEnv.set(providers.environmentVariablesPrefixedBy(""))
    cargoHome.set(providers.environmentVariable("CARGO_HOME").orElse(""))
    userHome.set(providers.systemProperty("user.home").orElse(""))
    outputs.doNotCacheIf(
        "the inherited environment or a cargo configuration file may change the output",
    ) { task ->
        val build = task as CargoBuildTask
        RustBuildHelpers.cacheRefusal(
            build.inheritedEnv.get(),
            build.cargoHome.orNull,
            build.userHome.get(),
            build.workingDir.get().asFile,
        ) != null
    }
    // wasm-pack emits pkg/.gitignore, Gradle excludes .gitignore from cached directory outputs,
    // and a cache load then restores the package without it. rustPackage copies that file as part
    // of the package contract, so the cached output is not the fresh output (cache experiment 7.3).
    outputs.doNotCacheIf(
        "wasm-pack emits pkg/.gitignore, which Gradle excludes from cached directory outputs",
    ) { true }
}

val rustNativeTest = tasks.register<CargoExecTask>("rustNativeTest") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir.asFile)
    extraEnv.set(mapOf("CARGO_TARGET_DIR" to rustTargetPath))
    commandLine("cargo", "test", "--workspace", "--locked")
}

val rustWasmSmoke = tasks.register<CargoExecTask>("rustWasmSmoke") {
    group = "rust"
    dependsOn(rustWasm)
    val smokeScript = rustCrateDir.file("tests/wasm-smoke.mjs").asFile
    inputs.file(smokeScript)
    inputs.dir(wasmPackageDir)
    workingDir(rustDir.asFile)
    toolchainCheck.set(
        ToolchainProbe(
            requiredCommands = listOf("node"),
            versionCommand = listOf("node", "--version"),
            expectedPrefix = "",
            minimumMajorVersion = 22,
            describeCommand = emptyList(),
            mismatchMessageFormat =
                "Node >=22 is required by web-extension/package.json; found '{actual}'",
            versionHint = "Node >=22.0.0",
            installHint =
                "Install Node.js >=22.0.0 (declared in web-extension/package.json engines.node)",
        ),
    )
    commandLine("node", smokeScript.absolutePath)
}

val rustTest = tasks.register("rustTest") {
    group = "rust"
    description = "Runs native Rust and generated web-target WASM tests."
    dependsOn(rustNativeTest, rustWasmSmoke, "rustWasmPairingTest")
    outputs.doNotCacheIf("aggregates native and WASM test tasks") { true }
}

tasks.register<CargoExecTask>("rustWasmPairingTest") {
    group = "verification"
    dependsOn(rustWasm)
    workingDir(repoRoot.asFile)
    commandLine("node", "rust/veles-crypto/tests/pairing-wasm.mjs")
}

for ((suffix, flag) in listOf("Entropy" to "--entropy-failure", "Expiry" to "--clock-tests",
    "MemoryBound" to "--memory-boundary", "ResponseErasure" to "--response-erasure")) {
    val test = tasks.register<CargoExecTask>("rustWasm${suffix}Test") {
        group = "verification"
        dependsOn(rustWasm)
        workingDir(repoRoot.asFile)
        commandLine("node", "rust/veles-crypto/tests/pairing-wasm.mjs", "web-extension/rust-wasm/pkg", flag)
    }
    rustTest.configure { dependsOn(test) }
}

val conformanceWasmDir = repoBuildDir.dir("rust/conformance-wasm")
val rustWasmConformance = tasks.register<CargoExecTask>("rustWasmConformance") {
    group = "verification"
    description = "Builds a test-only fixed-scalar WASM package outside product outputs."
    dependsOn(verifyWasmPack, verifyWasmBindgen, rustWasmBindgenConsistency, rustWasmLockCheck)
    val wasmPackBin = repoBuildDir.dir("rust-tools/wasm-pack/bin").file("wasm-pack$windowsExecutableSuffix").asFile
    val bindgenBin = repoBuildDir.dir("rust-tools/wasm-bindgen-cli/bin").asFile
    workingDir(rustDir.asFile)
    prependPath.set(listOf(wasmPackBin.parentFile.absolutePath, bindgenBin.absolutePath))
    extraEnv.set(
        mapOf(
            "CARGO_TARGET_DIR" to rustTargetPath,
            "CARGO_PROFILE_RELEASE_PANIC" to "abort",
        ),
    )
    deleteBefore.from(conformanceWasmDir)
    setCommandLine(
        listOf(
            wasmPackBin.absolutePath,
            "build", "veles-crypto", "--target", "web", "--release", "--mode", "no-install", "--no-opt",
            "--out-dir", conformanceWasmDir.asFile.absolutePath, "--", "--locked", "--features", "conformance",
        ),
    )
}

tasks.register<CargoExecTask>("rustWasmConformanceTest") {
    group = "verification"
    dependsOn(rustWasmConformance)
    workingDir(repoRoot.asFile)
    commandLine("node", "rust/veles-crypto/tests/pairing-wasm.mjs", conformanceWasmDir.asFile, "--conformance")
}

// Separate output and target directories keep test-only native exports out of
// the build/rust/jniLibs staging directory, release APK and rustPackage.
// OTP-10: the same applies to the host-JVM cdylib. `host-jni` is a non-default
// feature, so this task is the only producer of a host library; it writes under
// build/rust/ (covered by `clean` and rust/scripts/assert-clean.sh) and never
// into build/rust/jniLibs, rustPackage or any release artifact.
val rustHostJniDir = repoBuildDir.dir("rust/host-jni")
val rustHostJniTargetDir = repoBuildDir.dir("rust/host-jni-target")
val hostJniLibraryName = when {
    isWindows -> "veles_crypto.dll"
    osName.startsWith("Mac") -> "libveles_crypto.dylib"
    else -> "libveles_crypto.so"
}

val rustHostJni = tasks.register<CargoBuildTask>("rustHostJni") {
    group = "rust"
    description = "Builds the locked host-JVM cdylib used by JVM tests and the loopback child JVM."
    dependsOn("rustToolchainCheck")
    val builtForWindows = isWindows
    val hostTarget = rustHostJniTargetDir
    val hostLibrary = hostTarget.dir("release").file(hostJniLibraryName)
    sources.from(rustSources)
    outputDir.set(rustHostJniDir)
    executable.set("cargo")
    workingDir.set(rustDir)
    targetDir.set(hostTarget)
    prependPath.set(emptyList())
    // The JNI boundary keeps the panic machinery; a test-only build must match
    // the release product's unwind behavior so panics surface as Java exceptions
    // instead of aborting the test JVM.
    panicStrategy.set("unwind")
    // Explicit registry-path remap (rustJniConformance sets none): the host
    // library embeds panic locations like the product .so, so a build must not
    // depend on where CARGO_HOME happens to store the registry.
    remapRustFlags.set(registryRemapFlags)
    preArguments.set(emptyList())
    postArguments.set(
        listOf("build", "--locked", "--release", "--package", "veles-crypto", "--features", "host-jni"),
    )
    outputFlag.set("")
    rustcVersion.set(rustcVersionProvider)
    hostIdentity.set(hostIdentityProvider)
    inheritedEnv.set(providers.environmentVariablesPrefixedBy(""))
    cargoHome.set(providers.environmentVariable("CARGO_HOME").orElse(""))
    userHome.set(providers.systemProperty("user.home").orElse(""))
    copyFrom.set(hostLibrary)
    outputs.doNotCacheIf(
        "the inherited environment or a cargo configuration file may change the output",
    ) { task ->
        val build = task as CargoBuildTask
        RustBuildHelpers.cacheRefusal(
            build.inheritedEnv.get(),
            build.cargoHome.orNull,
            build.userHome.get(),
            build.workingDir.get().asFile,
        ) != null
    }
    // The MSVC linker identity is not probed on Windows, so the key cannot see it.
    outputs.doNotCacheIf("the MSVC linker identity is not probed on Windows") { builtForWindows }
}

tasks.register<CargoExecTask>("rustJniConformance") {
    group = "verification"
    dependsOn(verifyCargoNdk)
    workingDir(rustDir.asFile)
    ndkLookup.set(rustNdkLookup)
    prependPath.set(listOf(repoBuildDir.dir("rust-tools/cargo-ndk/bin").asFile.absolutePath))
    extraEnv.set(
        mapOf(
            "CARGO_TARGET_DIR" to repoBuildDir.dir("rust/conformance-target").asFile.absolutePath,
            "CARGO_PROFILE_RELEASE_PANIC" to "unwind",
        ),
    )
    commandLine(
        "cargo", "ndk", "--platform", "33", "--target", "arm64-v8a", "--target", "x86_64",
        "--output-dir", repoBuildDir.dir("rust/conformance-jni").asFile.absolutePath,
        "build", "--release", "--locked", "--features", "conformance",
    )
}

val rustJniOutput = repoBuildDir.dir("rust/jniLibs")
val rustJni = tasks.register<CargoBuildTask>("rustJni") {
    group = "rust"
    description = "Builds locked release JNI libraries for the approved Android ABIs."
    dependsOn(verifyCargoNdk)
    sources.from(rustSources)
    toolsFile.set(rustToolsFile)
    ndkVersion.set(ndkVersionProvider)
    ndkLookup.set(rustNdkLookup)
    outputDir.set(rustJniOutput)
    executable.set("cargo")
    workingDir.set(rustDir)
    targetDir.set(repoBuildDir.dir("rust/target"))
    prependPath.set(listOf(repoBuildDir.dir("rust-tools/cargo-ndk/bin").asFile.absolutePath))
    panicStrategy.set("unwind")
    // Registry-path remap for byte-reproducible JNI libraries (see
    // registryRemapFlags): without it, CI runner vs Docker rebuilds embed
    // different CARGO_HOME paths and the APK compare fails.
    remapRustFlags.set(registryRemapFlags)
    preArguments.set(
        listOf(
            "ndk", "--platform", "33",
            "--target", "arm64-v8a",
            "--target", "armeabi-v7a",
            "--target", "x86_64",
        ),
    )
    postArguments.set(listOf("build", "--workspace", "--release", "--locked"))
    outputFlag.set("--output-dir")
    rustcVersion.set(rustcVersionProvider)
    inheritedEnv.set(providers.environmentVariablesPrefixedBy(""))
    cargoHome.set(providers.environmentVariable("CARGO_HOME").orElse(""))
    userHome.set(providers.systemProperty("user.home").orElse(""))
    outputs.doNotCacheIf(
        "the inherited environment or a cargo configuration file may change the output",
    ) { task ->
        val build = task as CargoBuildTask
        RustBuildHelpers.cacheRefusal(
            build.inheritedEnv.get(),
            build.cargoHome.orNull,
            build.userHome.get(),
            build.workingDir.get().asFile,
        ) != null
    }
}

val rustPackageDir = repoBuildDir.dir("rust-package")
val rustJniPaths = listOf(
    "arm64-v8a/libveles_crypto.so",
    "armeabi-v7a/libveles_crypto.so",
    "x86_64/libveles_crypto.so",
)

// A Sync is never cached by Gradle. The doFirst/doLast lambdas read only the block-local values,
// never a script-level val, so the configuration cache can serialize them.
val rustPackage = tasks.register<Sync>("rustPackage") {
    group = "rust"
    description = "Stages the JNI and WASM artifacts as the rust-jni-wasm package."
    dependsOn(rustJni, rustWasm)
    val jniDir = rustJniOutput.asFile
    val packageDir = rustPackageDir.asFile
    val wasmPkgDir = wasmPackageDir.asFile
    val expectedJniPaths = rustJniPaths
    from(jniDir) {
        include(expectedJniPaths)
        into("jni")
    }
    from(wasmPkgDir) { into("wasm") }
    into(packageDir)
    doFirst {
        val actual = jniDir.walkTopDown()
            .filter { it.isFile }
            .map { it.relativeTo(jniDir).invariantSeparatorsPath }
            .sorted()
            .toList()
        check(actual == expectedJniPaths.sorted()) {
            "Unexpected JNI artifact paths: $actual"
        }
    }
    doLast {
        val root = packageDir
        // Gradle's Sync excludes dotfiles, but wasm-pack emits .gitignore as
        // part of the package contract, so copy it before recording checksums.
        File(wasmPkgDir, ".gitignore").copyTo(File(root, "wasm/.gitignore"), overwrite = true)
        val paths = root.walkTopDown()
            .filter { it.isFile && it.name != "SHA256SUMS" }
            .map { it.relativeTo(root).invariantSeparatorsPath }
            .sorted()
            .toList()
        check(paths.filter { it.startsWith("jni/") } == expectedJniPaths.map { "jni/$it" }.sorted()) {
            "Rust package JNI paths are invalid: $paths"
        }
        check(paths.any { it.startsWith("wasm/") }) { "Rust package has no WASM files" }
        val sums = paths.joinToString(separator = "\n", postfix = "\n") { path ->
            val digest = MessageDigest.getInstance("SHA-256")
                .digest(File(root, path).readBytes())
                .joinToString("") { byte -> "%02x".format(byte) }
            "$digest  $path"
        }
        File(root, "SHA256SUMS").writeText(sums)
    }
}

val rustFormat = tasks.register<CargoExecTask>("rustFormat") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir.asFile)
    extraEnv.set(mapOf("CARGO_TARGET_DIR" to rustTargetPath))
    commandLine("cargo", "fmt", "--all", "--", "--check")
}

val rustLintHost = tasks.register<CargoExecTask>("rustLintHost") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir.asFile)
    extraEnv.set(mapOf("CARGO_TARGET_DIR" to rustTargetPath))
    commandLine(
        "cargo", "clippy", "--workspace", "--all-targets", "--locked",
        "--", "-D", "warnings",
    )
}

val rustLintHostJni = tasks.register<CargoExecTask>("rustLintHostJni") {
    group = "rust"
    description = "Runs denied-warning Clippy on the host-JVM JNI exports (non-default host-jni feature)."
    dependsOn("rustToolchainCheck")
    workingDir(rustDir.asFile)
    extraEnv.set(mapOf("CARGO_TARGET_DIR" to rustTargetPath))
    commandLine(
        "cargo", "clippy", "--package", "veles-crypto", "--all-targets", "--locked",
        "--features", "host-jni", "--", "-D", "warnings",
    )
}

val rustLintWasm = tasks.register<CargoExecTask>("rustLintWasm") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir.asFile)
    extraEnv.set(
        mapOf(
            "CARGO_TARGET_DIR" to rustTargetPath,
            "CARGO_PROFILE_RELEASE_PANIC" to "abort",
        ),
    )
    commandLine(
        "cargo", "clippy", "--workspace", "--target", "wasm32-unknown-unknown",
        "--locked", "--", "-D", "warnings",
    )
}

val rustLintAndroid = tasks.register<CargoExecTask>("rustLintAndroid") {
    group = "rust"
    dependsOn(verifyCargoNdk)
    workingDir(rustDir.asFile)
    ndkLookup.set(rustNdkLookup)
    prependPath.set(listOf(repoBuildDir.dir("rust-tools/cargo-ndk/bin").asFile.absolutePath))
    extraEnv.set(
        mapOf(
            "CARGO_TARGET_DIR" to rustTargetPath,
            "CARGO_PROFILE_RELEASE_PANIC" to "unwind",
        ),
    )
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
    outputs.doNotCacheIf("aggregates lint tasks") { true }
}

// Diagnostic entry point for CI provisioning. A typed task keeps the configuration-time read in a
// provider and the print in an action, so it is configuration-cache clean.
@DisableCachingByDefault(because = "prints the pinned NDK version")
abstract class PrintNdkVersionTask : DefaultTask() {
    @get:Input
    abstract val ndkVersion: Property<String>

    @TaskAction
    fun printVersion() {
        println(ndkVersion.get())
    }
}

// Diagnostic entry point for rust/scripts/test-ndk-lookup.sh: prints the directory the JNI tasks
// would use without running cargo.
@DisableCachingByDefault(because = "prints the resolved NDK directory")
abstract class PrintNdkDirectoryTask : DefaultTask() {
    @get:Internal
    abstract val ndkLookup: Property<NdkLookup>

    @TaskAction
    fun printDirectory() {
        println(RustBuildHelpers.resolveNdkDirectory(ndkLookup.get()).absolutePath)
    }
}

tasks.register<PrintNdkVersionTask>("rustNdkVersion") {
    group = "rust"
    description = "Prints the pinned Android NDK version (rust/ndk-version) for CI provisioning."
    ndkVersion.set(ndkVersionProvider)
}

tasks.register<PrintNdkDirectoryTask>("rustNdkDir") {
    group = "rust"
    description = "Prints the resolved Android NDK directory (ANDROID_NDK_HOME, ANDROID_SDK_ROOT/ndk/<version>, ANDROID_HOME/ndk/<version>, local.properties sdk.dir)."
    ndkLookup.set(rustNdkLookup)
}
