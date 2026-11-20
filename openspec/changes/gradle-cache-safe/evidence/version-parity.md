# Version parity: plugin 0.4.14 vs android/git-version.gradle.kts

Task 2, Steps 3, 5 and 6. Environment for every run: `JAVA_HOME=/home/dev/.local/jdks/temurin-21.0.12`,
`ANDROID_SDK_ROOT=ANDROID_HOME=/home/dev/.android-sdk`, Gradle 9.5.0 through the repository wrapper,
2026-11-20, Linux container.

## Step 3 — RED (harness before the script exists)

```sh
bash android/scripts/check-git-version-parity.sh
# exit 1
```

```text
* What went wrong:
Could not read script
'/home/dev/.t3/worktrees/veles-android/t3-699e4a45-1711ce8ea1e8baf4/android/git-version.gradle.kts'
as it does not exist.
BUILD FAILED in 3s
FAIL on-tag (script build failed)
...
FAIL no-tag (script build failed)
```

All eight states failed for the same reason (`android/git-version.gradle.kts` missing) and the
harness exited `1`; the plugin side already ran, so the harness itself was exercised.

## Step 5 — GREEN

```sh
bash android/scripts/check-git-version-parity.sh
# exit 0
```

```text
PASS on-tag
PASS after-tag
PASS dirty
PASS branch
PASS detached
PASS two-tags
PASS prerelease
PASS no-tag
```

The harness only prints `PASS <state>`; to record the compared values the same script was copied
unchanged to `/tmp/opencode/parity-verbose.sh` with two extra `printf`s before each `diff` (that copy is
not committed). Its transcript, one line per build, `;` = end of the two output lines:

```text
  plugin: androidGitVersion.name 1.2.3                                                    androidGitVersion.code 10203;
  script: androidGitVersion.name 1.2.3                                                    androidGitVersion.code 10203;
PASS on-tag
  plugin: androidGitVersion.name 1.2.3-3-3660c05                                          androidGitVersion.code 10203;
  script: androidGitVersion.name 1.2.3-3-3660c05                                          androidGitVersion.code 10203;
PASS after-tag
  plugin: androidGitVersion.name 1.2.3-3-3660c05-dirty                                    androidGitVersion.code 10203;
  script: androidGitVersion.name 1.2.3-3-3660c05-dirty                                    androidGitVersion.code 10203;
PASS dirty
  plugin: androidGitVersion.name 1.2.3-3-3660c05-feature_x                                androidGitVersion.code 10203;
  script: androidGitVersion.name 1.2.3-3-3660c05-feature_x                                androidGitVersion.code 10203;
PASS branch
  plugin: androidGitVersion.name 1.2.3-3-3660c05-3660c05f1f3d11aca9ec69a2cf4f8aea65276cd6   androidGitVersion.code 10203;
  script: androidGitVersion.name 1.2.3-3-3660c05-3660c05f1f3d11aca9ec69a2cf4f8aea65276cd6   androidGitVersion.code 10203;
PASS detached
  plugin: androidGitVersion.name 1.2.4-3-3660c05                                          androidGitVersion.code 10204;
  script: androidGitVersion.name 1.2.4-3-3660c05                                          androidGitVersion.code 10204;
PASS two-tags
  plugin: androidGitVersion.name 1.2.3-rc1-3-3660c05                                      androidGitVersion.code 10203;
  script: androidGitVersion.name 1.2.3-rc1-3-3660c05                                      androidGitVersion.code 10203;
PASS prerelease
  plugin: androidGitVersion.name untagged-4-3660c05                                       androidGitVersion.code 0;
  script: androidGitVersion.name untagged-4-3660c05                                       androidGitVersion.code 0;
PASS no-tag
```

(Columns are separated by tabs in the real output; the transcript above collapses the tab so the two
fields fit one line. The commit ids are this run's throwaway commits — the harness fixes author and
committer dates, so they are reproducible.)

Hygiene after the run: the temporary directory is removed by `trap`, `/tmp` holds no
`git-version-parity.*`, the worktree has no `.gv-plugin/`, and `git tag -l 'android-v9*'` prints nothing
before and after.

## Step 6 — the app build uses the same derivation

Captured before editing `android/app/build.gradle.kts`, with the plugin still applied:

```sh
cd android && ./gradlew -q :app:androidGitVersion > $SCRATCH/plugin-out.txt
# androidGitVersion.name	unknown
# androidGitVersion.code	0
```

That is plugin 0.4.14's linked-worktree limitation recorded in `task-1-report.md`: JGit cannot resolve
`HEAD` through a linked worktree's `commondir`, so the plugin prints `unknown` / `0` here although
`git describe --tags` in the same worktree resolves `android-v0.1.0`. It is a pre-existing environment
fact of the plugin, not a rule, and no derivation can match it without copying the bug.

The same commit's values were therefore captured from plugin 0.4.14 in an ordinary clone of this
worktree (an ordinary repository, clean tree, no uncommitted change):

```sh
git clone --no-hardlinks <worktree> $SCRATCH/repo-copy
(cd $SCRATCH/repo-copy/android && ./gradlew -q :app:androidGitVersion)
# androidGitVersion.name	0.1.0-37-912d515-chore_gradle-cache-safe
# androidGitVersion.code	100
```

`android/git-version.gradle.kts` pointed at that clone (`-PgitRepoDir=$SCRATCH/repo-copy`, a throwaway
build applying only the script) prints exactly:

```text
androidGitVersion.name	0.1.0-37-912d515-chore_gradle-cache-safe
androidGitVersion.code	100
```

So the script reproduces the plugin on the real repository state as well: the real `android-v0.1.0`
tag, 37 commits since it, the 7-hex `HEAD` prefix, the sanitized `chore/gradle-cache-safe` branch, and
`versionCode` 100 (`android-v0.1.0` under `MMNNPP`).

In the worktree, after the change:

```sh
cd android && ./gradlew -q :app:androidGitVersion
# androidGitVersion.name	0.1.0-37-912d515-chore_gradle-cache-safe-dirty
# androidGitVersion.code	100
```

`-dirty` is correct: the derivation runs on a tree that carries this task's uncommitted change. The
clone's capture above is the same state minus that change, and it matches.

## Configuration cache

```sh
cd android && ./gradlew --configuration-cache --configuration-cache-problems=warn --console=plain :app:androidGitVersion
# Calculating task graph ... / androidGitVersion.name 0.1.0-37-...-dirty / androidGitVersion.code 100
# Configuration cache entry stored.
# (same command again)
# Configuration cache entry reused.
```

The configuration-cache problems report for that invocation and for `help` contains zero occurrences of
`external process` and of `app/build.gradle.kts`; the `external process started '/usr/bin/git …'`
problem that `evidence/cc-problems-before.md` recorded for this build file is gone with the plugin.
