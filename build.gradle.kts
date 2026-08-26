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

val extensionBuild = registerWebExtExecTask(
    taskName = "extensionBuild",
    taskDescription = "Run vite build in web-extension/ to produce dist/. Always re-runs (no outputs.dir declared).",
    script = "build",
)
extensionBuild.configure {
    dependsOn(extensionInstall, extensionTypecheck)
    // Deliberately no outputs.dir: declaring only webExtDir/"dist" without modeling
    // all src inputs (src/**, public/**, vite.config.ts, tsconfig.json, package.json)
    // risks stale-artifact re-use. The build is fast (<10s); always-run is the safer
    // contract for a deterministic-artifact pipeline.
}

val extensionPackage = tasks.register<Zip>("extensionPackage") {
    group = "extension"
    description = "Package web-extension/dist/ into a deterministic zip under build/web-extension/ plus a .sha256 sidecar."
    onlyIf {
        when {
            skipWebExt.get() -> {
                logger.lifecycle("Skipping extensionPackage: -Pveles.skipWebExt=true.")
                false
            }
            !webExtLockfile.isFile -> {
                logger.lifecycle("Skipping extensionPackage: web-extension/package-lock.json absent.")
                false
            }
            else -> true
        }
    }
    dependsOn(extensionBuild)

    // Extension version comes from package.json — same source manifest.ts reads,
    // keeping zip name and manifest content in sync.
    @Suppress("UNCHECKED_CAST")
    val pkg = groovy.json.JsonSlurper().parse(webExtPkg) as Map<String, Any?>
    val extVersion = pkg["version"]?.toString()
        ?: throw GradleException("web-extension/package.json missing 'version'.")

    archiveFileName.set("veles-extension-$extVersion.zip")
    destinationDirectory.set(layout.buildDirectory.dir("web-extension"))

    from(webExtDist)

    // Deterministic-zip recipe — see spec "Deterministic packaging recipe".
    isReproducibleFileOrder = true
    isPreserveFileTimestamps = false
    dirPermissions { unix("0755") }
    filePermissions { unix("0644") }
    entryCompression = ZipEntryCompression.DEFLATED
    metadataCharset = "UTF-8"
    includeEmptyDirs = false

    // sha256 sidecar is a declared output (not just a side-effect file).
    val sidecar = layout.buildDirectory.file("web-extension/veles-extension-$extVersion.zip.sha256")
    outputs.file(sidecar)

    doLast {
        val zip = archiveFile.get().asFile
        val digest = java.security.MessageDigest.getInstance("SHA-256")
            .digest(zip.readBytes())
            .joinToString("") { "%02x".format(it) }
        val side = sidecar.get().asFile
        side.parentFile.mkdirs()
        side.writeText("$digest  ${zip.name}\n")
        logger.lifecycle("Wrote ${side.name}: $digest")
    }
}

// Strict MV3 baseline guard — exact match against the locked-down manifest.
// Any drift in manifest_version, permissions, host_permissions, or CSP fails
// the package step. Exact-match (not substring) so weakening CSP with extra
// directives, adding host_permissions, or non-empty permissions is caught.
val validateExtensionManifest = tasks.register("validateExtensionManifest") {
    group = "extension"
    description = "Validate web-extension/dist/manifest.json against the locked-down MV3 baseline (exact match)."
    onlyIf {
        when {
            skipWebExt.get() -> {
                logger.lifecycle("Skipping validateExtensionManifest: -Pveles.skipWebExt=true.")
                false
            }
            !webExtLockfile.isFile -> {
                logger.lifecycle("Skipping validateExtensionManifest: web-extension/package-lock.json absent.")
                false
            }
            else -> true
        }
    }
    dependsOn(extensionBuild)
    inputs.file(webExtDist.resolve("manifest.json"))
    doLast {
        val manifestFile = webExtDist.resolve("manifest.json")
        check(manifestFile.isFile) {
            "Expected $manifestFile after extensionBuild; not found. Run `./gradlew extensionBuild`."
        }
        @Suppress("UNCHECKED_CAST")
        val m = groovy.json.JsonSlurper().parse(manifestFile) as Map<String, Any?>

        fun fail(reason: String): Nothing =
            throw GradleException("web-extension manifest guard failed: $reason")

        if (m["manifest_version"] != 3) {
            fail("manifest_version must be 3, got ${m["manifest_version"]}")
        }

        // `permissions` must be exactly the empty list — not absent, not a non-list.
        if (!m.containsKey("permissions")) {
            fail("permissions key must be present and equal to []")
        }
        if (m["permissions"] != emptyList<Any>()) {
            fail("permissions must be exactly [], got ${m["permissions"]}")
        }

        // `host_permissions` must be absent entirely.
        if (m.containsKey("host_permissions")) {
            fail("host_permissions must be absent in 1a, got ${m["host_permissions"]}")
        }

        // CSP must be exactly the locked-down map.
        val expectedCsp = mapOf(
            "extension_pages" to "script-src 'self'; object-src 'self'",
        )
        val actualCsp = m["content_security_policy"]
        if (actualCsp != expectedCsp) {
            fail(
                "content_security_policy must be exactly $expectedCsp, got $actualCsp",
            )
        }

        logger.lifecycle("web-extension manifest guard: MV3 baseline (exact match) OK.")
    }
}

extensionPackage.configure {
    dependsOn(validateExtensionManifest)
}

// npm-side artifact test: asserts the dist/ file set and that manifest.json
// round-trips through buildExtensionManifest(). Runs vitest against the built
// dist/, so a missing build or an unexpected emitted chunk fails packaging.
val extensionArtifactTest = registerWebExtExecTask(
    taskName = "extensionArtifactTest",
    taskDescription = "Run vitest bundle tests against web-extension/dist/ (asserts file set and manifest round-trip).",
    script = "test:bundle",
)
extensionArtifactTest.configure {
    dependsOn(extensionBuild)
}

extensionPackage.configure {
    dependsOn(extensionArtifactTest)
}