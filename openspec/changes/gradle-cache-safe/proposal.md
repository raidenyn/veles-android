## Why

Parallel agent worktrees build every change cold, paying full Gradle configuration and full Rust/Android
builds each time. The configuration cache and the build cache could cut that. But many custom tasks
cannot be serialized, and the Android version plugin starts git at configuration time, so a cached entry
could keep a stale version. Release builds must also never restore artifacts. So the build has to be
made safe to cache before either cache is used. Refs raidenyn/veles-android#181.

## What Changes

**Configuration cache**
- From: off in both entry builds. A probe finds 15 root/rust tasks whose actions hold script references,
  and the `androidgitversion` plugin starts `git` at configuration time.
- To: `org.gradle.configuration-cache=true` and `org.gradle.configuration-cache.problems=fail` are set
  in `gradle.properties`, `android/gradle.properties` and a new `rust/gradle.properties` (for the
  standalone `cd rust && ../gradlew` build). So every Gradle invocation runs with the configuration
  cache, without problems, except the guarded artifact-producing invocations below. The failing tasks
  become typed tasks with injected services; configuration-time file, environment and process reads go
  through providers. CC compatibility of the guarded graphs (for example `assembleRelease` and
  `rustPackage`) is proven by local probes, because CI runs them with CC off. The release/PR installer
  packaging pipeline is a documented CC opt-out instead (design D2).
- Reason: a speed-up that holds on every entry point, and a CI failure on any regression in the
  unguarded invocations.
- Impact: non-breaking for commands. Every unguarded CI invocation runs with CC on.

**Android version derivation**
- From: the `com.gladed.androidgitversion` 0.4.14 plugin computes `versionName`/`versionCode`.
- To: a configuration-cache-safe `ValueSource` running `git` computes the same name and code. It is
  proven equal to the plugin on a set of repository states (on a tag, commits after it, dirty, another
  branch, no tag). Any git state that changes the computed name or code invalidates the cached entry. A
  repository task named `:app:androidGitVersion` replaces the plugin's task and still prints
  `androidGitVersion.name<TAB><value>` (and the code line) under `-q`, as `build-android.yml` parses it.
- Reason: the plugin blocks CC, and a reused entry could carry a stale version.
- Impact: non-breaking. Same version, same APK; the plugin dependency is removed.

**Build cache correctness**
- From: no custom task declares itself cacheable or non-cacheable; caching is unset.
- To: `rustHostJni`, `rustJni` and `rustWasm` are cacheable, with every result-affecting input declared,
  and they refuse caching under ambient cargo/rustc overrides. Every other custom task is explicitly
  non-cacheable with a reason. The build cache stays off by default.
- Reason: a local opt-in must never restore a wrong artifact.
- Impact: non-breaking; nothing changes unless a developer opts in.

**Release and verification guard**
- From: release-producing workflows and `verify/` scripts run Gradle with the repository defaults.
- To: every Gradle invocation in the release-producing reusable workflows (`build-android.yml`,
  `build-rust.yml`, `build-web-extension.yml`, `build-native-windows.yml`, `build-native-macos.yml`,
  `build-supply-chain.yml`), the `installGitCliff` steps of `release-build.yml`/`publish-release.yml`,
  and the `verify/` scripts that build or verify artifacts (`verify/generate-sboms.sh`) passes
  `--no-build-cache --no-configuration-cache`. These workflows also run on PRs (`ci.yml`,
  `build-pr-installers.yml`) with the same flags. Layout checks such as `verify/check-android-layout.sh`
  are not guarded: they keep testing the standalone builds with CC on. `docs/reproducible-builds.md`
  states the rule, and a contract test enforces it.
- Reason: released and verified artifacts are built, never restored.
- Impact: non-breaking; release builds stay as slow as today.

**Developer opt-in**
- `BUILD.md` documents `--build-cache` and per-user `org.gradle.caching=true`.

**Evidence in the PR**
- Cold and warm timings before and after. Two runs per entry point with the CC entry reused.
- The input-change experiment (a pinned input reruns the task instead of `FROM-CACHE`) and the git-state
  experiment for the version.
- Version equivalence with the old plugin, and the debug APK checksum before and after.
- `scriptsTest`, `node --test verify/test/workflow-contracts.test.mjs` and
  `verify/verify-supply-chain.sh` passing, and an emulator run of the debug APK.

## Capabilities

### New Capabilities
- `gradle-build-caching`: configuration-cache compatibility of both entry builds, the cacheability
  contract of custom tasks, the release/verification cache guard and the developer opt-in.

### Modified Capabilities
- `component-versioning`: *Android version from tags* gains requirements. The derivation runs without a
  configuration-time plugin, equals the previous plugin 0.4.14 for the repository's configuration, and
  follows any git state change through the configuration cache. `:app:androidGitVersion` keeps its
  output. `android-standalone-build`'s *Build identity is preserved by the move* keeps holding and is
  not modified.

## Impact

- Build logic: `build.gradle.kts`, `rust/build.gradle.kts`, `android/app/build.gradle.kts`,
  `android/build.gradle.kts`, `gradle.properties`, `android/gradle.properties`, new
  `rust/gradle.properties`,
  `android/gradle/libs.versions.toml` (plugin removed).
- CI: `build-android.yml`, `build-rust.yml`, `build-web-extension.yml`, `build-native-windows.yml`,
  `build-native-macos.yml`, `build-supply-chain.yml`, `release-build.yml`, `publish-release.yml`; every
  other Gradle invocation in CI now runs with CC on.
- Verification: `verify/generate-sboms.sh` (guarded), `verify/check-android-layout.sh` and
  `verify/check-root-aliases.sh` (CC on);
  `verify/test/workflow-contracts.test.mjs` (a new assertion, plus updates to existing exact-string
  assertions).
- Docs: `docs/reproducible-builds.md`, `BUILD.md`, `docs/releasing.md`, `docs/android.md`,
  `android/AGENTS.md`.
- Release artifacts: none change. The APK version fields and the reproducible-build comparison stay the
  same.
