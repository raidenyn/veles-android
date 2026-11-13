// Enables the repo's Conventional Commits commit-msg hook (.githooks) for local clones.
// Never overrides a hooksPath the developer already configured; skipped on CI.
//
// One shared script: the root build and the independent Android build both apply it, so
// `:app:preBuild` installs the hook whichever entry point runs the build.
import java.io.ByteArrayOutputStream
import java.io.File
import org.gradle.api.DefaultTask
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.provider.Property
import org.gradle.api.tasks.Input
import org.gradle.api.tasks.Internal
import org.gradle.api.tasks.TaskAction
import org.gradle.process.ExecOperations
import javax.inject.Inject

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
        try {
            configureHooks(repoDir.get().asFile)
        } catch (e: Exception) {
            logger.warn("Could not configure git hooks (${e.message}); skipping.")
        }
    }

    private fun configureHooks(dir: File) {
        val probe = ByteArrayOutputStream()
        val inside = execOps.exec {
            workingDir = dir
            commandLine("git", "rev-parse", "--is-inside-work-tree")
            standardOutput = probe
            errorOutput = ByteArrayOutputStream()
            isIgnoreExitValue = true
        }
        if (inside.exitValue != 0 || probe.toString(Charsets.UTF_8).trim() != "true") {
            logger.info("Not inside a git work tree: skipping git hook installation")
            return
        }
        val out = ByteArrayOutputStream()
        val read = execOps.exec {
            workingDir = dir
            commandLine("git", "config", "--get", "core.hooksPath")
            standardOutput = out
            isIgnoreExitValue = true
        }
        val current = out.toString(Charsets.UTF_8).trim()
        when {
            read.exitValue != 0 || current.isEmpty() -> {
                val write = execOps.exec {
                    workingDir = dir
                    commandLine("git", "config", "core.hooksPath", ".githooks")
                    isIgnoreExitValue = true
                }
                if (write.exitValue != 0) {
                    logger.warn("git config core.hooksPath failed (exit ${write.exitValue}); hook not installed.")
                }
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
    // The repository owns the hooks directory: the build sits inside the work tree that
    // .githooks belongs to, so git resolves `core.hooksPath = .githooks` from there.
    repoDir.set(layout.projectDirectory)
    onCi.set(providers.environmentVariable("CI").map { it == "true" }.orElse(false))
}
