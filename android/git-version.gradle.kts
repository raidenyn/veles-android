// Android version derivation without the androidgitversion plugin (gradle-cache-safe, design D3).
//
// Applied by app/build.gradle.kts. `git` only runs inside the ValueSource, so nothing starts a
// process while the build is configured; Gradle re-obtains the value whenever it checks the
// configuration cache entry, which makes a new tag, a new commit, a dirty tree or another branch
// change the derived version. The plugin's rules were read from its 0.4.14 bytecode and confirmed by
// android/scripts/check-git-version-parity.sh; openspec/changes/gradle-cache-safe/evidence/ records
// both.
import java.io.ByteArrayOutputStream
import java.io.File
import javax.inject.Inject
import org.gradle.api.DefaultTask
import org.gradle.api.GradleException
import org.gradle.api.file.Directory
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.provider.ListProperty
import org.gradle.api.provider.Property
import org.gradle.api.provider.Provider
import org.gradle.api.provider.ValueSource
import org.gradle.api.provider.ValueSourceParameters
import org.gradle.api.tasks.Internal
import org.gradle.api.tasks.TaskAction
import org.gradle.process.ExecOperations
import org.gradle.work.DisableCachingByDefault

/**
 * Derives `[versionName, versionCode]` exactly as `com.gladed.androidgitversion` 0.4.14 did with
 * `prefix = "android-v"` and `codeFormat = "MMNNPP"`.
 */
abstract class GitVersionValueSource : ValueSource<List<String>, GitVersionValueSource.Params> {
    interface Params : ValueSourceParameters {
        val repoDir: DirectoryProperty
        val prefix: Property<String>
        val codeFormat: Property<String>
    }

    @get:Inject
    abstract val execOperations: ExecOperations

    override fun obtain(): List<String> {
        val repo = parameters.repoDir.get().asFile
        val prefix = parameters.prefix.get()
        val codeFormat = parameters.codeFormat.get()

        // The plugin returns this default when no repository, or no HEAD commit, is reachable.
        if (git(repo, "rev-parse", "--git-dir") == null) return UNKNOWN
        val headId = git(repo, "rev-parse", "--verify", "HEAD") ?: return UNKNOWN

        // JGit's Repository.branch is the short branch name, or the full commit id when HEAD is
        // detached; the plugin then hides it only when it is listed in hideBranches.
        val branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD") ?: headId
        // JGit's hasUncommittedChanges covers tracked additions, modifications and deletions;
        // untrackedIsDirty is false, so untracked files never count.
        val status = git(repo, "status", "--porcelain") ?: ""
        val dirty = status.lineSequence().any { it.isNotEmpty() && !it.startsWith("??") }

        val tags = matchingTags(repo, prefix)
        val byCommit = tags.groupBy { it.second }
        // The plugin walks commits from HEAD and stops at the first commit that carries a tag.
        val walk = git(repo, "rev-list", "HEAD")?.lineSequence()?.filter { it.isNotEmpty() }?.toList()
            ?: emptyList()
        var revCount = 0
        var lastVersion: String? = null
        for ((index, commit) in walk.withIndex()) {
            val names = byCommit[commit] ?: continue
            revCount = index
            lastVersion = names.map { it.first.removePrefix(prefix) }.sortedWith(versionOrder).last()
            break
        }
        if (lastVersion == null) {
            // No tag anywhere: the plugin still counts every commit it walked.
            lastVersion = "untagged"
            revCount = walk.size
        }

        return listOf<String>(
            versionName(lastVersion, revCount, headId, branch, dirty),
            versionCode(lastVersion, revCount, codeFormat).toString(),
        )
    }

    /**
     * Tags the plugin would accept: the short name must match `^<prefix>[0-9]+.*` and start with the
     * prefix. Annotated and lightweight tags are both peeled to their commit.
     */
    private fun matchingTags(repo: File, prefix: String): List<Pair<String, String>> {
        val pattern = Regex("^" + Regex.escape(prefix) + "[0-9]+.*")
        val names = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/tags")
            ?.lineSequence()?.filter { it.isNotEmpty() }?.toList()
            ?: return emptyList()
        return names.filter { it.startsWith(prefix) && pattern.matches(it) }
            .mapNotNull { name -> git(repo, "rev-parse", "--verify", "$name^{commit}")?.let { name to it } }
    }

    /** The plugin omits a branch listed in `hideBranches` and sanitizes every other one. */
    private fun versionName(
        lastVersion: String,
        revCount: Int,
        headId: String,
        branch: String,
        dirty: Boolean,
    ): String {
        // 0.4.14's default format is "%tag%%-count%%-commit%%-branch%%-dirty%". Every placeholder
        // except %tag% carries its own separator inside the percent signs ("%-count%"), so a
        // part that is not used also drops its separator; the plugin then deletes any placeholder
        // it did not substitute with `%[^%]+%`. The net effect is a plain "-"-joined name.
        val segments = mutableListOf(lastVersion)
        if (revCount > 0) {
            segments += revCount.toString()
            segments += headId.take(COMMIT_HASH_LENGTH)
            if (branch !in HIDDEN_BRANCHES) {
                segments += branch.replace(Regex("[^a-zA-Z0-9.-]"), "_")
            }
        }
        if (dirty) segments += "dirty"
        return segments.joinToString("-")
    }

    /**
     * `codeFormat` is a run-length list of parts (`MMNNPP` is MAJOR, MINOR, PATCH at width 2 each).
     * A part is filled from that numeric group of the version, `0` when the version has no such
     * group; `X` is always zero and `R` is the commit count since the tag.
     */
    private fun versionCode(lastVersion: String, revCount: Int, codeFormat: String): Int {
        if (codeFormat.length > 9) {
            throw GradleException("codeFormat $codeFormat is too long (version codes must be < 2100000000")
        }
        // `unknown` has no numeric group, and neither has `untagged`; every part is then zero.
        val parts = versionParts(lastVersion)
        var code = 0
        for (run in codeRuns(codeFormat)) {
            val value = when (run.first) {
                'M' -> parts.getOrElse(0) { 0 }
                'N' -> parts.getOrElse(1) { 0 }
                'P' -> parts.getOrElse(2) { 0 }
                'R' -> revCount
                'X' -> 0
                else -> throw GradleException("codeFormat part '${run.first}' is not supported")
            }
            var room = 1
            repeat(run.second) { room *= 10 }
            if (room <= value) {
                throw GradleException("Not enough room for ${run.first} in $codeFormat width=${run.second}")
            }
            code = code * room + value
        }
        return code
    }

    private fun codeRuns(codeFormat: String): List<Pair<Char, Int>> {
        val runs = mutableListOf<Pair<Char, Int>>()
        for (letter in codeFormat) {
            if (letter !in "MNPBAXR") throw GradleException("Unrecognized char $letter in codeFormat")
            val last = runs.lastOrNull()
            if (last != null && last.first == letter) {
                runs[runs.size - 1] = letter to (last.second + 1)
            } else {
                runs += letter to 1
            }
        }
        return runs
    }

    /** Runs one read-only `git` command; `null` means git exited non-zero. */
    private fun git(repo: File, vararg args: String): String? {
        val stdout = ByteArrayOutputStream()
        val result = execOperations.exec {
            commandLine(listOf("git", "-C", repo.absolutePath) + args.toList())
            isIgnoreExitValue = true
            standardOutput = stdout
            errorOutput = ByteArrayOutputStream()
        }
        return if (result.exitValue == 0) stdout.toString(Charsets.UTF_8).trim() else null
    }

    private companion object {
        val UNKNOWN = listOf("unknown", "0")

        /** 0.4.14's defaults for `hideBranches` and `commitHashLength`. */
        const val COMMIT_HASH_LENGTH = 7
        val HIDDEN_BRANCHES = setOf("master", "release")

        /**
         * 0.4.14's tag comparator: numeric groups in ascending order, and on a tie a *longer*
         * name sorts first, so the plugin's `.last()` keeps the shortest name (0.4.14 compares
         * `b.size() <=> a.size()`). This is why `1.2.3` wins over `1.2.3-rc1` on the same commit.
         */
        val versionOrder = Comparator<String> { left, right ->
            val a = versionParts(left)
            val b = versionParts(right)
            var result = 0
            for (index in 0 until minOf(a.size, b.size)) {
                if (a[index] != b[index]) {
                    result = a[index].compareTo(b[index])
                    break
                }
            }
            if (result != 0) result else right.length.compareTo(left.length)
        }

        fun versionParts(version: String): List<Int> =
            version.split(Regex("[^0-9]+")).filter { it.isNotEmpty() }.map { it.toInt() }
    }
}

@DisableCachingByDefault(because = "reads git state")
abstract class AndroidGitVersionTask : DefaultTask() {
    /** Only the provider is captured, never the script that registered the task. */
    @get:Internal
    abstract val gitVersion: ListProperty<String>

    @TaskAction
    fun printVersion() {
        val values = gitVersion.get()
        println("androidGitVersion.name\t${values[0]}")
        println("androidGitVersion.code\t${values[1]}")
    }
}

// `gitRepoDir` is the harness's way to point the derivation at a throwaway repository; the Android
// build itself leaves it unset, so the repository root is android/.. (layout.settingsDirectory).
val gitVersionRepoDir: Provider<Directory> = providers.gradleProperty("gitRepoDir")
    .map { layout.projectDirectory.dir(it) }
    .orElse(layout.settingsDirectory.dir(".."))
val gitVersionProvider: Provider<List<String>> = providers.of(GitVersionValueSource::class.java) {
    parameters.repoDir.set(gitVersionRepoDir)
    parameters.prefix.set("android-v")
    parameters.codeFormat.set("MMNNPP")
}
extra["gitVersion"] = gitVersionProvider

tasks.register<AndroidGitVersionTask>("androidGitVersion") {
    group = "Android"
    description = "Print version name and version code"
    gitVersion.set(gitVersionProvider)
}
