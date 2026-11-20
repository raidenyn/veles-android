## ADDED Requirements

### Requirement: Android version derivation is configuration-cache safe
The Android build SHALL derive `versionName` and `versionCode` through a Gradle `ValueSource` that runs
`git`, declared in the repository (`android/git-version.gradle.kts`), and MUST NOT apply a plugin that
starts processes at configuration time. Any git state that changes the derived name or code (a new tag,
a new commit, a dirty tree, another branch) MUST change the values seen by the next build, even when
the configuration cache is on.

#### Scenario: New tag with the configuration cache on
- **WHEN** in a clean checkout on a detached `HEAD` or on `master`, with no `android-v*` tag on `HEAD`, `cd android && ./gradlew -q :app:androidGitVersion` runs, a local tag `android-v9.9.9` is then created on `HEAD`, and the command runs again
- **THEN** the second run reports `versionName` 9.9.9 and `versionCode` 90909

#### Scenario: New commit after the tag
- **WHEN** a commit is added after `android-v9.9.9` and the same command runs again
- **THEN** the reported name changes to the value the parity harness pins for the "commits after a tag" state

#### Scenario: No version plugin
- **WHEN** `android/gradle/libs.versions.toml` and `android/app/build.gradle.kts` are read
- **THEN** neither references `com.gladed.androidgitversion`

### Requirement: Android version equals the previous plugin
For the repository's configuration (prefix `android-v`, code format `MMNNPP`), the derived
`versionName` and `versionCode` SHALL equal what `com.gladed.androidgitversion` 0.4.14 produced, in each
of these repository states:
- exactly on a tag;
- commits after a tag;
- a dirty tree;
- a non-`master` branch;
- no `android-v*` tag;
- two tags on one commit.

The comparison is on the raw values, before the build applies `maxOf(code, 1)`. With no tag, the APK's
fallback is the plugin's value; the 0.1.0 / `versionCode` 100 of *Initial versions without a tag* is
the release planner's first release version, not this fallback. The repository MUST keep a harness,
`android/scripts/check-git-version-parity.sh`, that proves the equality by running both derivations in
throwaway repositories and comparing the full `-q` output of their `androidGitVersion` tasks.

#### Scenario: Parity harness
- **WHEN** `android/scripts/check-git-version-parity.sh` runs with network access to the Gradle plugin portal
- **THEN** it reports equal `androidGitVersion` output from the plugin and from `android/git-version.gradle.kts` for every listed state, and exits 0

#### Scenario: Release APK unchanged
- **WHEN** the unsigned release APK is built with `--no-build-cache` in clean detached-`HEAD` checkouts of the merge base and of the change's final commit, each with the same local tag `android-v9.9.9` on `HEAD` and no other `android-v*` tag there
- **THEN** the two APKs are byte-identical

### Requirement: The androidGitVersion task keeps its output
The Android build SHALL register a task `:app:androidGitVersion` that prints, under `-q`, the same lines
in the same order as plugin 0.4.14's task of that name, including `androidGitVersion.name<TAB><name>`
and `androidGitVersion.code<TAB><code>`.

#### Scenario: Workflow parser
- **WHEN** `build-android.yml` runs `./gradlew -q :app:androidGitVersion` and extracts the `androidGitVersion.name` field with `awk -F'\t'`
- **THEN** it obtains the derived version name
