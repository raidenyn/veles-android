import java.io.File
import java.util.zip.ZipFile
import org.gradle.api.DefaultTask
import org.gradle.api.file.ConfigurableFileCollection
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.provider.ListProperty
import org.gradle.api.tasks.Input
import org.gradle.api.tasks.InputDirectory
import org.gradle.api.tasks.InputFiles
import org.gradle.api.tasks.OutputFile
import org.gradle.api.tasks.PathSensitive
import org.gradle.api.tasks.PathSensitivity
import org.gradle.api.tasks.TaskAction
import org.gradle.work.DisableCachingByDefault

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.compose)
    alias(libs.plugins.ksp)
    alias(libs.plugins.hilt)
    alias(libs.plugins.protobuf)
    alias(libs.plugins.detekt)
}

// The version derives from git through a ValueSource (design D3): nothing starts `git` while the
// build is configured, and Gradle re-obtains the value when it checks the configuration cache entry.
apply(from = layout.settingsDirectory.file("git-version.gradle.kts").asFile)
@Suppress("UNCHECKED_CAST")
val gitVersion = extra["gitVersion"] as Provider<List<String>>

detekt {
    buildUponDefaultConfig = true
    // The detekt configuration is owned by the Android build root (android/config/detekt/).
    config.setFrom(layout.settingsDirectory.dir("config/detekt/detekt.yml").asFile)
}

// Inputs owned by the repository root are published by the Android root build
// (android/build.gradle.kts), so the `../` path arithmetic lives in one file.
val protocolV1Dir = rootProject.extra["protocolV1Dir"] as File
val protocolConformanceDir = rootProject.extra["protocolConformanceDir"] as File
// The Android NDK version is defined once, in rust/ndk-version, and shared with the Rust
// build (rustJni resolves the same version from its own file).
val rustNdkVersion = rootProject.extra["ndkVersion"] as String

android {
    namespace = "me.nagaev.veles"
    compileSdk = 35
    buildToolsVersion = "36.0.0"
    ndkVersion = rustNdkVersion

    defaultConfig {
        applicationId = "me.nagaev.veles"
        minSdk = 33
        targetSdk = 35
        versionCode = maxOf(gitVersion.get()[1].toInt(), 1)
        versionName = gitVersion.get()[0]

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        ndk {
            abiFilters += setOf("arm64-v8a", "armeabi-v7a", "x86_64")
        }
    }

    // Play "dependency metadata" is encrypted with a Google key and makes the
    // APK inherently non-reproducible; F-Droid also requires it disabled.
    dependenciesInfo {
        includeInApk = false
        includeInBundle = false
    }

    // Release signing is opt-in through the environment. Reading it through providers keeps the
    // configuration cache safe, and the signing values never become task inputs.
    val releaseKeystore = providers.environmentVariable("VELES_KEYSTORE_FILE").orNull
    signingConfigs {
        if (releaseKeystore != null) {
            create("release") {
                storeFile = file(releaseKeystore)
                storePassword = providers.environmentVariable("VELES_KEYSTORE_PASSWORD").orNull
                keyAlias = providers.environmentVariable("VELES_KEY_ALIAS").orNull
                keyPassword = providers.environmentVariable("VELES_KEY_PASSWORD").orNull
            }
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            signingConfig = signingConfigs.findByName("release")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }
    kotlin {
        compilerOptions {
            jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_21
        }
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    androidResources {
        localeFilters += listOf("en")
    }
    // The JNI libraries are a Rust build product (repository-root build/rust/jniLibs, staged by
    // the included :rustJni task), not an app build output.
    sourceSets.getByName("main").jniLibs.srcDir(
        layout.settingsDirectory.dir("../build/rust/jniLibs").asFile,
    )
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
            merges += "META-INF/LICENSE.md"
            merges += "META-INF/LICENSE-notice.md"
        }
    }
    lint {
        baseline = file("lint-baseline.xml")
        abortOnError = true
        warningsAsErrors = false
    }
    testOptions {
        unitTests.isIncludeAndroidResources = true
        unitTests.isReturnDefaultValues = true
    }
}

/**
 * OTP-09 Task 10, root `sessionLoopbackCheck`: writes the JVM unit-test classpath the
 * cross-endpoint loopback reads. The classpath is a file collection, so it carries the
 * compilation dependencies without the task holding `testDebugUnitTest` (an `AndroidUnitTest`)
 * itself; the action reads only its own properties, which is what the configuration cache requires.
 */
@DisableCachingByDefault(because = "writes a generated classpath file")
abstract class SessionLoopbackClasspathTask : DefaultTask() {
    @get:InputFiles
    @get:PathSensitive(PathSensitivity.ABSOLUTE)
    abstract val unitTestClasspath: ConfigurableFileCollection

    @get:OutputFile
    abstract val outputFile: RegularFileProperty

    @TaskAction
    fun write() {
        val file = outputFile.get().asFile
        file.parentFile.mkdirs()
        file.writeText(unitTestClasspath.asPath)
    }
}

// OTP-02 Plan Task 6 — the JVM protocol fixture consumer reads the SAME
// `protocol/v1/` fixture files the Node/Rust/TypeScript consumers read. The
// unit-test source set maps that shared root as an extra test-resource
// directory instead of checking in a second, copied fixture truth.
android.sourceSets.getByName("test").resources.srcDir(protocolV1Dir)
android.sourceSets.getByName("testDebug").resources.srcDir(protocolV1Dir)
// OTP-09 Task 10: the scenario inventory is owned by the `protocol` component and
// read from the shared tree, never copied into the app.
android.sourceSets.getByName("test").resources.srcDir(protocolConformanceDir)
android.sourceSets.getByName("testDebug").resources.srcDir(protocolConformanceDir)
android.sourceSets.getByName("androidTest").assets.srcDir(protocolV1Dir)

// Focused JVM consumer task for the `protocolCheck` aggregate: runs only
// me.nagaev.veles.protocol.ProtocolFixturesTest instead of the whole unit-test
// suite, reusing the full testDebugUnitTest classpath (incl. resources).
afterEvaluate {
    val unitTest = tasks.getByName<Test>("testDebugUnitTest")
    // OTP-10 D1: the real Rust core is a host-JVM cdylib built by `rustHostJni`;
    // its directory goes on java.library.path so VelesCrypto's
    // System.loadLibrary("veles_crypto") resolves in JVM tests. Robolectric tests
    // never initialize VelesCrypto (one library per class loader), so the path is
    // inert for them. The library is a Rust build product under the repository
    // root build/, staged by the included :rustHostJni task.
    val hostJniDir = layout.settingsDirectory.dir("../build/rust/host-jni").asFile
    unitTest.dependsOn(gradle.includedBuild("rust").task(":rustHostJni"))
    unitTest.systemProperty("java.library.path", hostJniDir.absolutePath)
    tasks.register<Test>("protocolJvmTest") {
        group = "verification"
        description = "Runs the focused OTP-02 protocol fixture consumer on the JVM."
        dependsOn(gradle.includedBuild("rust").task(":rustHostJni"))
        systemProperty("java.library.path", hostJniDir.absolutePath)
        // `classpath` and `testClassesDirs` are the unit-test task's file collections: they carry
        // the compilation dependencies themselves. Resolving `unitTest.taskDependencies` here
        // would resolve the AGP configurations while the root build's included-build dependency
        // graph is being created ("configuration ... attempted without an exclusive lock").
        classpath = unitTest.classpath
        testClassesDirs = unitTest.testClassesDirs
        filter {
            includeTestsMatching("me.nagaev.veles.protocol.ProtocolFixturesTest")
        }
        // The test loads the Rust host JNI library staged under build/rust/host-jni through
        // java.library.path. That directory is a dependency of :rustHostJni, not a task input, so
        // Gradle's key cannot see a changed native library with unchanged JVM classes: a build-cache
        // hit would restore a stale result without testing the new library. Never cache it.
        outputs.doNotCacheIf(
            "loads the Rust host JNI library from build/rust/host-jni, which is not a task input",
        ) { true }
    }

    // OTP-09 Task 10: `sessionLoopbackCheck` runs the TypeScript client against the real
    // Kotlin phone engine in a child JVM. This task only publishes the unit-test JVM
    // classpath; it compiles the same source sets `protocolJvmTest` depends on. The file
    // collection carries the classpath's task dependencies (see the comment above).
    tasks.register<SessionLoopbackClasspathTask>("sessionLoopbackClasspath") {
        group = "verification"
        description = "Writes the JVM unit-test classpath used by the cross-endpoint session loopback."
        unitTestClasspath.from(unitTest.classpath)
        outputFile.set(layout.buildDirectory.file("session-loopback/classpath.txt"))
    }
}

// OTP-09 D5 release guard: the minified release APK must not carry any debug
// hook class, receiver or synthetic probe string. Debug-only sources live under
// app/src/debug and the debug application handler never enters a release build;
// this task proves it against the actual R8 output in both UTF-8 and UTF-16LE.
val releaseHookForbidden = listOf(
    "me/nagaev/veles/debugprobe",
    "VelesProbe",
    "Veles synthetic",
    "me.nagaev.veles.debug.",
    // OTP-12 Gate B: lint requires the platform wake-lock `prefix:tag` form, so the scheduler's
    // tag is outside the dotted prefix and is named here (and in GateBCommandsTest).
    "me.nagaev.veles.debug:GateBPush",
    "INJECT_PEER",
    "REVOKE_PEER",
    // OTP-10 D5 debug pairing surface: the package path survives R8 renaming in the DEX
    // descriptor, and the action/screen strings survive in the manifest and resources.
    "me/nagaev/veles/debugpairing",
    "me.nagaev.veles.debugpairing.START",
    "me.nagaev.veles.debugpairing.CANCEL",
    "VelesPairingDebug",
    "Veles pairing debug",
    "Pairing code",
    "Start pairing window",
    "Cancel pairing window",
    "Clear incomplete record",
    "Peer installation id",
    "Record cleared",
    "Clear failed or refused",
    "Pairing id:",
    "Pairing fingerprint",
    "Start Bluetooth sharing first",
    // OTP-11 D9 debug peer-management surface: the peer list, its actions, the confirmed
    // endpoint-wide reset and the one trust status. Same rule as above: every value of every
    // new `pairing_debug_*` string is named here, and `ReleaseGuardStringsTest` keeps the two
    // lists in sync.
    "Pairing peers",
    "Peer fingerprint: %1\$s",
    "Peer connected",
    "Peer disconnected",
    "Revoke peer",
    "Mutual remove",
    "Peer revoked",
    "Revoke failed: trust store unavailable",
    "Peer removed on both devices",
    "No open full session: only local removal is possible. Nothing was deleted.",
    "No response: the other device may keep its record",
    "Mutual remove failed",
    "Reset identity",
    "Reset is destructive: it deletes every trust record and the phone identity.",
    "Confirm reset",
    "Cancel reset",
    "Identity reset; pairing again creates new trust",
    "Reset failed",
    "Nothing deleted: confirm the destructive reset first",
    "Trust status: %1\$s",
    "Trust OK",
    "Re-pair required",
    "Peers unavailable: trust store unreadable",
    "No trusted peers",
    "Sharing service is not running; the coordinator is unavailable",
)

/**
 * OTP-09 D5 release guard: the minified release APK must not carry any debug hook class, receiver
 * or synthetic probe string. Debug-only sources live under app/src/debug and the debug application
 * handler never enters a release build; this task proves it against the actual R8 output in both
 * UTF-8 and UTF-16LE.
 *
 * The action reads only the task's own properties, never a script-level `val` or `project`, which is
 * what the configuration cache requires.
 */
@DisableCachingByDefault(because = "verification only: inspects the release APK and writes no output")
abstract class ReleaseHookGuardTask : DefaultTask() {
    @get:Input
    abstract val forbiddenStrings: ListProperty<String>

    // Declares the real input so the APK directory is part of the task's state; `dependsOn`
    // keeps assembleRelease in the graph that produces it. The path is relative so it never
    // leaks the worktree location into the key, and the task is never cached anyway.
    @get:InputDirectory
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val apkDirectory: DirectoryProperty

    @TaskAction
    fun guard() {
        val apkDir = apkDirectory.get().asFile
        val apks = apkDir.listFiles { file -> file.isFile && file.extension == "apk" }
            .orEmpty()
            .sortedBy { it.name }
        check(apks.isNotEmpty()) {
            "releaseHookGuard: no release APK under ${apkDir.path}; run :app:assembleRelease"
        }
        val encodings = listOf(Charsets.UTF_8, Charsets.UTF_16LE)
        val forbidden = forbiddenStrings.get()
        apks.forEach { apk ->
            ZipFile(apk).use { zip ->
                val entries = zip.entries().asSequence()
                    .filter { entry ->
                        !entry.isDirectory && (
                            entry.name == "AndroidManifest.xml" ||
                                entry.name == "resources.arsc" ||
                                (entry.name.startsWith("classes") && entry.name.endsWith(".dex"))
                            )
                    }
                    .toList()
                check(entries.isNotEmpty()) {
                    "releaseHookGuard: ${apk.name} has no DEX, manifest or resources.arsc entries"
                }
                entries.forEach { entry ->
                    val bytes = zip.getInputStream(entry).use { it.readBytes() }
                    forbidden.forEach { needle ->
                        encodings.forEach { charset ->
                            val offset = indexOf(bytes, needle.toByteArray(charset))
                            if (offset >= 0) {
                                throw GradleException(
                                    "releaseHookGuard: ${apk.name}!${entry.name} contains forbidden " +
                                        "release string '$needle' (${charset.name()}, byte $offset)",
                                )
                            }
                        }
                    }
                }
            }
        }
        logger.lifecycle(
            "releaseHookGuard: ${apks.joinToString { it.name }} contain no debug hook classes, " +
                "receivers or probe strings",
        )
    }

    /** First byte offset of [needle] in this array, or -1. */
    private fun indexOf(haystack: ByteArray, needle: ByteArray): Int {
        if (needle.isEmpty() || haystack.size < needle.size) return -1
        outer@ for (start in 0..(haystack.size - needle.size)) {
            for (index in needle.indices) {
                if (haystack[start + index] != needle[index]) continue@outer
            }
            return start
        }
        return -1
    }
}

tasks.register<ReleaseHookGuardTask>("releaseHookGuard") {
    group = "verification"
    description = "Fails when the minified release APK contains a debug hook class, receiver or probe string."
    dependsOn(":app:assembleRelease")
    forbiddenStrings.set(releaseHookForbidden)
    apkDirectory.set(layout.buildDirectory.dir("outputs/apk/release"))
}

tasks.matching { it.name.matches(Regex("merge.*JniLibFolders")) }.configureEach {
    dependsOn(gradle.includedBuild("rust").task(":rustJni"))
}

dependencies {
    val composeBom = platform(libs.androidx.compose.bom)
    implementation(composeBom)
    androidTestImplementation(composeBom)

    implementation(libs.kotlin.stdlib)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.serialization.json)

    implementation(libs.androidx.compose.animation)
    implementation(libs.androidx.compose.foundation.layout)
    implementation(libs.androidx.compose.material.iconsExtended)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.materialWindow)
    implementation(libs.androidx.compose.runtime.livedata)
    implementation(libs.androidx.compose.ui.tooling.preview)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    debugImplementation(libs.androidx.compose.ui.tooling)

    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.activity.ktx)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)

    implementation(libs.androidx.glance)
    implementation(libs.androidx.glance.appwidget)
    implementation(libs.androidx.glance.material3)

    implementation(libs.androidx.lifecycle.viewmodel.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.savedstate)
    implementation(libs.androidx.lifecycle.viewModelCompose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.window)

    implementation(libs.androidx.room.runtime)
    implementation(libs.androidx.room.ktx)
    ksp(libs.androidx.room.compiler)

    implementation(libs.androidx.datastore)
    implementation(libs.protobuf.javalite)

    implementation(libs.hilt.android)
    ksp(libs.hilt.compiler)
    implementation(libs.androidx.hilt.navigation.compose)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.mockk.android)
    testImplementation(libs.mockk.agent)
    testImplementation(libs.robolectric)
    testImplementation(libs.androidx.test.core)
    androidTestImplementation(libs.junit)
    androidTestImplementation(libs.androidx.test.core)
    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.test.espresso.core)
    androidTestImplementation(libs.androidx.test.rules)
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.kotlinx.coroutines.test)
    androidTestImplementation(libs.androidx.compose.ui.test)
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.mockk.android)
    androidTestImplementation(libs.mockk.agent)
    testImplementation(kotlin("test"))
}

protobuf {
    protoc { artifact = "com.google.protobuf:protoc:4.29.3" }
    generateProtoTasks {
        all().configureEach {
            builtins {
                create("java") { option("lite") }
            }
        }
    }
}

tasks.matching { it.name == "preBuild" }.configureEach {
    dependsOn(":installGitHooks")
}
