// Top-level build file where you can add configuration options common to all sub-projects/modules.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
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

// OTP-01 sub-project 1a — web-extension toolchain task group.
// Tasks skip with a descriptive message when web-extension/package-lock.json is absent
// or -Pveles.skipWebExt=true is passed — Android-only and verify/ workflows keep
// working without node on PATH.
val webExtDir = rootDir.resolve("web-extension")
val webExtLockfile = webExtDir.resolve("package-lock.json")
val webExtPkg = webExtDir.resolve("package.json")
val webExtDist = webExtDir.resolve("dist")

// Presence skip is value-based: -Pveles.skipWebExt=false leaves tasks enabled.
val skipWebExt = providers.gradleProperty("veles.skipWebExt")
    .map { it.toBooleanStrict() }
    .orElse(false)

// Toolchain check — verifies node and npm exist on PATH and node meets the
// declared Node floor. npm is bundled with node, so only its presence and
// version are logged (no separate npm floor is enforced).
val extensionToolchainCheck = tasks.register("extensionToolchainCheck") {
    group = "extension"
    description = "Verify node and npm are on PATH and node major >= 22 (the declared floor). npm version is logged only."
    onlyIf {
        when {
            skipWebExt.get() -> {
                logger.lifecycle("Skipping extensionToolchainCheck: -Pveles.skipWebExt=true.")
                false
            }
            !webExtLockfile.isFile -> {
                logger.lifecycle("Skipping extensionToolchainCheck: web-extension/package-lock.json absent.")
                false
            }
            else -> true
        }
    }
    doLast {
        fun probe(tool: String, vararg args: String): String {
            return try {
                val proc = ProcessBuilder(tool, *args)
                    .redirectErrorStream(true)
                    .start()
                val out = proc.inputStream.bufferedReader().readText().trim()
                check(proc.waitFor() == 0) { "$tool ${args.joinToString(" ")} exited non-zero" }
                out
            } catch (e: java.io.IOException) {
                throw GradleException(
                    "Could not find '$tool' on PATH; " +
                        "install Node.js >= 22.0.0 (declared in web-extension/package.json engines.node)",
                    e,
                )
            }
        }

        val nodeVersion = probe("node", "--version").removePrefix("v")
        val npmVersion = probe("npm", "--version")

        // Floor check: node major >= 22. npm is bundled with node, so no separate floor.
        val major = nodeVersion.substringBefore('.').toIntOrNull()
            ?: throw GradleException("Unparseable `node --version` output: '$nodeVersion'")
        if (major < 22) {
            throw GradleException(
                "Node.js $nodeVersion is below the declared floor >= 22.0.0 " +
                    "(see web-extension/package.json engines.node).",
            )
        }
        logger.lifecycle("web-extension toolchain: node $nodeVersion, npm $npmVersion — OK.")
    }
}

// npm exec helper — every task that runs npm goes through this.
fun registerWebExtExecTask(taskName: String, taskDescription: String, script: String): TaskProvider<Exec> =
    tasks.register<Exec>(taskName) {
        group = "extension"
        description = taskDescription
        onlyIf {
            when {
                skipWebExt.get() -> {
                    logger.lifecycle("Skipping $taskName: -Pveles.skipWebExt=true.")
                    false
                }
                !webExtLockfile.isFile -> {
                    logger.lifecycle("Skipping $taskName: web-extension/package-lock.json absent.")
                    false
                }
                else -> true
            }
        }
        workingDir = webExtDir
        commandLine = listOf("npm", "run", script)
    }

val extensionInstall = tasks.register<Exec>("extensionInstall") {
    group = "extension"
    description = "Run `npm ci` in web-extension/ to hydrate node_modules per the committed lockfile."
    onlyIf {
        when {
            skipWebExt.get() -> {
                logger.lifecycle("Skipping extensionInstall: -Pveles.skipWebExt=true.")
                false
            }
            !webExtLockfile.isFile -> {
                logger.lifecycle("Skipping extensionInstall: web-extension/package-lock.json absent.")
                false
            }
            else -> true
        }
    }
    dependsOn(extensionToolchainCheck)
    workingDir = webExtDir
    commandLine = listOf("npm", "ci")
    // npm ci is driven by both package.json (dep spec) and lockfile (exact resolution).
    // Also track .npmrc (registry/proxy overrides) — it's optional.
    inputs.file(webExtPkg)
    inputs.file(webExtLockfile)
    inputs.files(webExtDir.resolve(".npmrc")).optional(true)
    outputs.dir(webExtDir.resolve("node_modules"))
}

val extensionFormat = registerWebExtExecTask(
    taskName = "extensionFormat",
    taskDescription = "Run prettier --check over web-extension/.",
    script = "format:check",
)
extensionFormat.configure { dependsOn(extensionInstall) }

val extensionLint = registerWebExtExecTask(
    taskName = "extensionLint",
    taskDescription = "Run eslint over web-extension/.",
    script = "lint",
)
extensionLint.configure { dependsOn(extensionInstall) }

val extensionTypecheck = registerWebExtExecTask(
    taskName = "extensionTypecheck",
    taskDescription = "Run tsc --noEmit over web-extension/.",
    script = "typecheck",
)
extensionTypecheck.configure { dependsOn(extensionInstall) }

val extensionTest = registerWebExtExecTask(
    taskName = "extensionTest",
    taskDescription = "Run vitest run in web-extension/ (source-only; bundle assertions live in extensionArtifactTest).",
    script = "test",
)
extensionTest.configure { dependsOn(extensionInstall) }

// Wire source-level checks into root `check` so CI quality gates ride along.
tasks.named("check") {
    dependsOn(extensionFormat, extensionLint, extensionTypecheck, extensionTest)
}

// Root clean also removes web-extension/dist/ (the one generated-artifact exception
// allowed inside a source tree — see spec Global decision 3).
tasks.named("clean") {
    doLast {
        delete(webExtDist)
    }
}