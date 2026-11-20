// Root build of the independent Android build (design D4). Plugin aliases are declared here with
// `apply false` so the app module shares one plugin classpath resolved from
// android/gradle/libs.versions.toml. Spotless is applied at this root because it owns the Kotlin
// sources of the whole build, so `cd android && ./gradlew spotlessCheck` checks android/ only.
import com.diffplug.spotless.LineEnding

plugins {
    base
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.compose) apply false
    alias(libs.plugins.ksp) apply false
    alias(libs.plugins.hilt) apply false
    alias(libs.plugins.protobuf) apply false
    alias(libs.plugins.detekt) apply false
    alias(libs.plugins.spotless)
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
        // The repository `.editorconfig` pins `end_of_line = lf` and no `.gitattributes` entry
        // overrides it for Kotlin sources. Stating it explicitly also keeps Spotless out of the
        // default JGit git-attributes lookup, whose `.git` reads otherwise change between runs
        // and invalidate the configuration cache every time (`lineEndings` defaults to
        // GIT_ATTRIBUTES).
        lineEndings = LineEnding.UNIX
    }
}

// ---- Inputs owned by the repository root ------------------------------------------------
// The Android build accesses exactly these paths outside android/: the shared protocol fixtures,
// the pinned NDK version (rust/ndk-version, also read by the Rust build) and the shared hook
// script. `layout.settingsDirectory` is android/, so the two fixture roots are published for
// app/build.gradle.kts from here; no `File` arithmetic runs through `rootProject` at execution time.
extra["protocolV1Dir"] = layout.settingsDirectory.dir("../protocol/v1").asFile
extra["protocolConformanceDir"] = layout.settingsDirectory.dir("../protocol/conformance").asFile
// Single source of truth: rust/ndk-version is read here and by the Rust build (design D5). AGP
// needs the value while the build is configured, so this is a configuration-time provider read;
// Gradle records the file's content as a configuration-cache input.
extra["ndkVersion"] = providers.fileContents(
    layout.settingsDirectory.dir("../rust").file("ndk-version"),
).asText.get().trim()

// CI provisions the NDK from the pinned version: Android's documented entry point forwards to the
// Rust build, which owns the file.
tasks.register("rustNdkVersion") {
    group = "android"
    description = "Prints the pinned Android NDK version (rust/ndk-version) for CI provisioning."
    dependsOn(gradle.includedBuild("rust").task(":rustNdkVersion"))
}

// Enables the repo's Conventional Commits commit-msg hook (.githooks) for local clones. One shared
// script, also applied by the repository-root build, so hook installation behaves identically from
// both entry points (design D4).
apply(from = layout.settingsDirectory.dir("../.githooks/install-hooks.gradle.kts").asFile)
