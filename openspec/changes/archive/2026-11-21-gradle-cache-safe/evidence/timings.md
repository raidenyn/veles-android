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

### After (change commit `22e49558`)

Task 10.2 (design D8, timing matrix steps 2 and 3). Measured on 2026-11-20 in the same container,
with JDK Azul Zulu 21.0.12.1 (`/home/dev/.jdk/zulu21`, this task's documented JDK) and the same
Android SDK/NDK. Configuration cache is on by default at this commit (`org.gradle.configuration-cache=true`,
`problems=fail`); the build cache is off (no `org.gradle.caching` in the project or the user home, no
`--build-cache`). The environment variables listed in D4's refusal rule are unset and no cargo config
file exists, so the cacheable Rust tasks were eligible.

Base commit: `22e4955818699ecc5e79da2c8b6eb289dca25ed1` (`chore/gradle-cache-safe` HEAD at the time of
the run).

Scratch directories: the defaults matrix ran from `/tmp/opencode/gradle-cache-safe-task10`, like the
Before matrix. The `--build-cache` matrix was rerun from `/home/dev/.cache/gcs-task10`: the first
attempt filled `/tmp` (a 7.9 GiB tmpfs) during its first run and was discarded; it contributed no
numbers, and its build-cache entries were moved aside before the rerun. The rerun's worktrees live on
the overlay filesystem, which is the only difference from the defaults matrix's storage.

### Method

```sh
export ANDROID_SDK_ROOT=/home/dev/.android-sdk ANDROID_HOME=/home/dev/.android-sdk
export JAVA_HOME=/home/dev/.jdk/zulu21
export PATH="$JAVA_HOME/bin:$PATH"
git worktree add -b gca10-wtA "$SCRATCH/step2/wtA" "$(git rev-parse HEAD)"
git worktree add -b gca10-wtB "$SCRATCH/step2/wtB" "$(git rev-parse HEAD)"
```

Two fresh worktrees, each running the three commands in the Before matrix's order from a clean
checkout: `cd android && ./gradlew assembleDebug`, `cd android && ./gradlew testDebugUnitTest`, then
`./gradlew rustTest` from the repository root. Each command then runs once more in the same worktree
to show configuration-cache reuse. Wall clock is `date +%s%N` around the whole `./gradlew` process,
with Gradle's own line quoted beside it, exactly as Before. The worktrees were removed with
`git worktree remove --force` and their `gca10-*` branches deleted afterwards; no tag or commit was
created, and `git tag -l 'android-v9*'` in the change's worktree prints nothing.

### After, defaults (CC on, build cache off)

Worktree A: `<scratch>/step2/wtA` (branch `gca10-wtA`, deleted afterwards).

| Run | Command | Wall clock (ms) | Gradle line | Task summary |
| --- | --- | --- | --- | --- |
| 1 | `cd android && ./gradlew assembleDebug` | 233094 (3m 53.094s) | `BUILD SUCCESSFUL in 3m 52s` | 51 actionable tasks: 51 executed |
| 2 | `cd android && ./gradlew testDebugUnitTest` | 135939 (2m 15.939s) | `BUILD SUCCESSFUL in 2m 15s` | 47 actionable tasks: 18 executed, 29 up-to-date |
| 3 | `./gradlew rustTest` | 269427 (4m 29.427s) | `BUILD SUCCESSFUL in 4m 29s` | 15 actionable tasks: 15 executed |
| 4 | `cd android && ./gradlew assembleDebug` | 9755 (9.755s) | `BUILD SUCCESSFUL in 9s` | 51 actionable tasks: 3 executed, 48 up-to-date |
| 5 | `cd android && ./gradlew testDebugUnitTest` | 9354 (9.354s) | `BUILD SUCCESSFUL in 9s` | 47 actionable tasks: 2 executed, 45 up-to-date |
| 6 | `./gradlew rustTest` | 3179 (3.179s) | `BUILD SUCCESSFUL in 2s` | 15 actionable tasks: 12 executed, 3 up-to-date |

Worktree B: `<scratch>/step2/wtB` (branch `gca10-wtB`, deleted afterwards).

| Run | Command | Wall clock (ms) | Gradle line | Task summary |
| --- | --- | --- | --- | --- |
| 7 | `cd android && ./gradlew assembleDebug` | 221718 (3m 41.718s) | `BUILD SUCCESSFUL in 3m 41s` | 51 actionable tasks: 51 executed |
| 8 | `cd android && ./gradlew testDebugUnitTest` | 127831 (2m 7.831s) | `BUILD SUCCESSFUL in 2m 8s` | 47 actionable tasks: 18 executed, 29 up-to-date |
| 9 | `./gradlew rustTest` | 287262 (4m 47.262s) | `BUILD SUCCESSFUL in 4m 46s` | 15 actionable tasks: 15 executed |
| 10 | `cd android && ./gradlew assembleDebug` | 10507 (10.507s) | `BUILD SUCCESSFUL in 10s` | 51 actionable tasks: 3 executed, 48 up-to-date |
| 11 | `cd android && ./gradlew testDebugUnitTest` | 4648 (4.648s) | `BUILD SUCCESSFUL in 4s` | 47 actionable tasks: 2 executed, 45 up-to-date |
| 12 | `./gradlew rustTest` | 3020 (3.020s) | `BUILD SUCCESSFUL in 2s` | 15 actionable tasks: 12 executed, 3 up-to-date |

Configuration cache: runs 1–3 and 7–9 print `Calculating task graph as no cached configuration is
available for tasks: <task>`; runs 4–6 and 10–12 print `Reusing configuration cache.` No run stored or
discarded an entry unexpectedly.

Compared with Before, a cold run is the same order of magnitude (run 1 233 s against 203 s, run 7
222 s against 231 s, run 9 287 s against 369 s; the differences are machine noise and dependency-cache
warmth), while the second pass of each entry point drops to 3–11 s. That second pass is the change's
gain: Before had no configuration cache, so a warm rerun still cost a full task-graph calculation.

### After, `--build-cache`

Local build cache `<home>/.gradle/caches/build-cache-1`, emptied for the duration of this matrix
(pre-existing entries written by an earlier session at 14:2x were moved aside and restored
afterwards), 0 entries at the start, 46 after worktree A, 72 after worktree B. Worktree A:
`<scratch>/step2bc/bcA`; worktree B: `<scratch>/step2bc/bcB`; both at `22e49558` and deleted afterwards.

| Run | Command | Wall clock (ms) | Gradle line | Task summary |
| --- | --- | --- | --- | --- |
| 1 | `cd android && ./gradlew assembleDebug --build-cache` | 185355 (3m 5.355s) | `BUILD SUCCESSFUL in 3m 5s` | 51 actionable tasks: 51 executed |
| 2 | `cd android && ./gradlew testDebugUnitTest --build-cache` | 103975 (1m 43.975s) | `BUILD SUCCESSFUL in 1m 43s` | 47 actionable tasks: 18 executed, 29 up-to-date |
| 3 | `./gradlew rustTest --build-cache` | 270512 (4m 30.512s) | `BUILD SUCCESSFUL in 4m 30s` | 15 actionable tasks: 15 executed |
| 4 | `cd android && ./gradlew assembleDebug --build-cache` (worktree B) | 129122 (2m 9.122s) | `BUILD SUCCESSFUL in 2m 8s` | 51 actionable tasks: 34 executed, 17 from cache |
| 5 | `cd android && ./gradlew testDebugUnitTest --build-cache` (worktree B) | 96669 (1m 36.669s) | `BUILD SUCCESSFUL in 1m 36s` | 47 actionable tasks: 15 executed, 3 from cache, 29 up-to-date |
| 6 | `./gradlew rustTest --build-cache` (worktree B) | 274197 (4m 34.197s) | `BUILD SUCCESSFUL in 4m 33s` | 15 actionable tasks: 15 executed |
| 7 | `./gradlew rustPackage --build-cache` (fresh worktree C) | 295619 (4m 55.619s) | `BUILD SUCCESSFUL in 4m 55s` | 12 actionable tasks: 11 executed, 1 from cache |

Runs 1–3 populate; runs 4–7 are fresh worktrees that share only the Gradle user home, so every
configuration-cache line is `Calculating task graph as no cached configuration is available`. The
`FROM-CACHE` tasks by run:

- run 4: `:rust:rustJni` plus 16 Android tasks (`generateDebugResources`, `packageDebugResources`,
  `generateDebugGlobalSynthetics`, `desugarDebugFileDependencies`, `javaPreCompileDebug`,
  `compressDebugAssets`, `processDebugNavigationResources`, `mergeLibDexDebug`, `extractDeepLinksDebug`,
  `compileDebugNavigationResources`, `mergeDebugResources`, `parseDebugLocalResources`,
  `generateDebugRFile`, `generateDebugProto`, `mergeExtDexDebug`, `hiltAggregateDepsDebug`).
- run 5: `:rust:rustHostJni`, `:app:javaPreCompileDebugUnitTest`, `:app:hiltAggregateDepsDebugUnitTest`.
- run 7: `:rust:rustJni`.

Both cacheable Rust products that appear in these graphs, `rustJni` (run 4) and `rustHostJni` (run 5),
are restored in a fresh worktree at a different path, which confirms the path-free key on a real
matrix rather than only in the D4 relocation experiments. `rustWasm` is deliberately not cacheable
(`doNotCacheIf` in `rust/build.gradle.kts`: wasm-pack emits `pkg/.gitignore`, which Gradle excludes from
cached directory outputs), so `rustTest` and `rustPackage` still compile the WASM product and their
wall clock barely moves; run 6 (4m 34s) matches its defaults counterpart (4m 29s). That is the
documented D4 refusal, not a missing cache entry.
