## Baseline timings at the merge base

Task 1.3 (design D8, timing matrix step 1). Measured on 2026-11-20 in the development container:
Linux 7.0.0-42-generic, 6 CPUs, 15 GiB RAM, JDK Temurin 21.0.12 (`~/.local/jdks/temurin-21.0.12`),
Gradle 9.5.0 through the repository wrapper, Android SDK/NDK from `ANDROID_SDK_ROOT`/`ANDROID_HOME`
(`/home/dev/.android-sdk`, NDK 29.0.14206865). No build cache and no configuration cache is configured
at this commit, so all six runs are the plain defaults.

Base commit: `597ee2c691f2d4c2dec1c4450072eca86a76054e` (`git merge-base HEAD origin/master`).

Scratch directory for this task: `/tmp/opencode/gradle-cache-safe-task1`.

### Method

Each run is a fresh worktree at the base commit, so no build output or `.gradle` directory is reused
between A and B. The two worktrees share only the Gradle user home, including the Gradle daemons.
Wall-clock time is `date +%s%N` taken around the whole `./gradlew` process, and Gradle's own
`BUILD SUCCESSFUL in …` line is quoted beside it. Commands and order are exactly the plan's:
`assembleDebug`, then `testDebugUnitTest` from `android/`, then `rustTest` from the repository root.

```sh
export ANDROID_SDK_ROOT=/home/dev/.android-sdk ANDROID_HOME=/home/dev/.android-sdk
export JAVA_HOME=$HOME/.local/jdks/temurin-21.0.12
export PATH="$JAVA_HOME/bin:$PATH"
BASE=$(git merge-base HEAD origin/master)

git worktree add -b gca-wtA "$SCRATCH/wtA" "$BASE"
git worktree add -b gca-wtB "$SCRATCH/wtB" "$BASE"
```

`-b` (a temporary branch) is a deviation from the detached `git worktree add "$SCRATCH/wtA" "$BASE"`
in the plan, forced by the environment: `com.gladed.androidgitversion` 0.4.14 reads git state through
JGit 5.9, which fails with `Bare Repository has neither a working tree, nor an index` on a **detached**
linked worktree (`android/app/build.gradle.kts:44`, stack `AndroidGitVersionExtension.scan` →
`StatusCommand.call` → `FileTreeIterator.<init>`). A worktree on a branch configures instead. See
"Worktree limitation" below for the version consequence. The temporary branches were deleted after the
runs and no tag or commit was created.

### Before (merge base)

Worktree A: `/tmp/opencode/gradle-cache-safe-task1/wtA` (branch `gca-wtA`, deleted afterwards).

| Run | Command | Wall clock (ms) | Gradle line | Task summary |
| --- | --- | --- | --- | --- |
| 1 | `cd android && ./gradlew assembleDebug` | 202870 (3m 22.870s) | `BUILD SUCCESSFUL in 3m 22s` | 51 actionable tasks: 51 executed |
| 2 | `cd android && ./gradlew testDebugUnitTest` | 245713 (4m 5.713s) | `BUILD SUCCESSFUL in 4m 5s` | 47 actionable tasks: 18 executed, 29 up-to-date |
| 3 | `./gradlew rustTest` | 340269 (5m 40.269s) | `BUILD SUCCESSFUL in 5m 40s` | 15 actionable tasks: 15 executed |

Worktree B: `/tmp/opencode/gradle-cache-safe-task1/wtB` (branch `gca-wtB`, deleted afterwards).

| Run | Command | Wall clock (ms) | Gradle line | Task summary |
| --- | --- | --- | --- | --- |
| 4 | `cd android && ./gradlew assembleDebug` | 230660 (3m 50.660s) | `BUILD SUCCESSFUL in 3m 50s` | 51 actionable tasks: 51 executed |
| 5 | `cd android && ./gradlew testDebugUnitTest` | 180825 (3m 0.825s) | `BUILD SUCCESSFUL in 3m` | 47 actionable tasks: 18 executed, 29 up-to-date |
| 6 | `./gradlew rustTest` | 369475 (6m 9.475s) | `BUILD SUCCESSFUL in 6m 9s` | 15 actionable tasks: 15 executed |

Both worktrees were removed with `git worktree remove --force` after the runs, so no build output from
them reaches the change's own checkout.

### Worktree limitation observed while measuring

In a linked worktree on a branch, the plugin's JGit cannot see the repository's tag refs through the
worktree's `commondir`; `cd android && ./gradlew -q :app:androidGitVersion` printed
`androidGitVersion.name	unknown` and `androidGitVersion.code	0`, although
`git describe --tags --long --match 'android-v*'` in the same worktree prints
`android-v0.1.0-34-g597ee2c6`. In a detached linked worktree the build fails outright as quoted above.
The plugin also fails in the main checkout's other linked worktrees on this machine for the same reason.
This is a pre-existing environment fact at the merge base, not a result of this change; it is one more
reason the version derivation is replaced in Task 2 (design D3). The timings above are unaffected,
because the version string does not change the amount of work each task performs. A normal clone at the
same commit, used for the APK baseline, does see the tags: `:app:androidGitVersion` there prints
`9.9.9` / `90909` (evidence/apk.md).

### After

To be appended by Task 10, timing matrix steps 2 and 3.
