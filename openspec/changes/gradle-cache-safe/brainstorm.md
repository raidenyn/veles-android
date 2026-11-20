## Design Summary

Source: GitHub issue `raidenyn/veles-android#181` ("Gradle configuration cache and build cache: make the
build safe to cache"). The issue is in no GitHub Project, so this change is untracked (no `tracker.md`,
no claim; the PR may carry a non-closing `Refs` line).

**Why.** Many agent worktrees build this repository in parallel. Each one pays the full Gradle
configuration and the full Rust/Android build cold. Gradle's configuration cache (CC) and build cache
could cut that, but the build has many custom `Exec` tasks and strict reproducible-build rules
(`docs/reproducible-builds.md`, `verify/`). Enabling either blindly could produce wrong or silently
restored artifacts. Release builds must **build** their artifacts, never restore them.

**What exists today (observed on 2026-11-20 in this checkout).**

- Two entry builds: the root build (orchestrator: `includeBuild("android")`, `includeBuild("rust")`, plus
  the protocol, bridge, installer, verification-tool and release-tool tasks in `build.gradle.kts`) and
  the independent `android/` build (`:app` plus `includeBuild("../rust")`). `rust/` is an included build
  with no `gradle.properties` of its own. Gradle 9.5.0 (`gradle/wrapper/gradle-wrapper.properties`).
- Neither `gradle.properties` nor `android/gradle.properties` sets `org.gradle.configuration-cache` or
  `org.gradle.caching`.
- Probe: `./gradlew --configuration-cache --configuration-cache-problems=warn rustFormat rustLint
  rustTest scriptsTest bridgeTest bridgeHostTest bridgeInstallerTest --dry-run` stored the entry with
  30 problems. The failing tasks are `:rust:rustToolchainCheck`, `:rust:installCargoNdk`,
  `:rust:installWasmPack`, `:rust:installWasmBindgenCli`, `:rust:verifyCargoNdk`,
  `:rust:verifyWasmPack`, `:rust:verifyWasmBindgenCli`, `:rust:rustWasmBindgenConsistency`,
  `:rust:rustWasm`, `:rust:rustWasmSmoke`, `:rust:rustLintAndroid`, `:bridgeNodeCheck`,
  `:bridgeRustToolchainCheck`, `:bridgeHostToolchainCheck` and `:bridgeInstallerToolchainCheck`. Every
  failure is "cannot serialize/deserialize Gradle script object references": their `doFirst`/`doLast`
  lambdas call script-level functions and accessors (`resolveNdkDirectory()`,
  `cargoRegistryRemapRustFlags()`, `requireOnPath()`, `pinnedToolVersion()`, `delete(...)`,
  `providers.exec`).
- The same probe reported, for `android/app/build.gradle.kts`, external processes started at
  configuration time (`git --version`, `git config --system --show-origin --list -z`). They come from the
  `com.gladed.androidgitversion` plugin 0.4.14 (JGit), which computes `versionName`/`versionCode` from
  `android-v*` tags (`prefix = "android-v"`, `codeFormat = "MMNNPP"`). Besides being a CC problem, this
  is a **correctness risk**: a reused CC entry could keep a stale version after a new tag.
  `build-android.yml` creates exactly such a local tag right before `./gradlew assembleRelease` and then
  reads `./gradlew -q :app:androidGitVersion` (`androidGitVersion.name<TAB><value>`).
- A dry run that included Android tasks failed here: the container's JDK 21 has no `javac`
  ("does not provide the required capabilities: [JAVA_COMPILER]"). Android builds and tests need another
  machine (the home PC through the `homepc` skill) or a JDK provisioned for the run.
- Gradle's `Exec` and `DefaultTask` types are not cacheable unless a task opts in (`@CacheableTask` or
  `outputs.cacheIf`). Today no custom task opts in, so turning the build cache on would cache only
  AGP/Kotlin/Java plugin tasks. The work is to decide which custom tasks should opt in, and to declare
  their inputs exactly.
- The repository already has the CC-safe pattern: `InstallGitCliffTask` in `build.gradle.kts` and
  `InstallGitHooksTask` in `.githooks/install-hooks.gradle.kts` are typed tasks with injected
  `ExecOperations`/`FileSystemOperations` and `@Input`/`@OutputDirectory` properties.
- Release artifacts are produced by the reusable workflows `build-android.yml` (`assembleRelease`),
  `build-rust.yml` (`rustPackage`), `build-web-extension.yml` (`rustWasm`), `build-native-windows.yml`
  and `build-native-macos.yml` (`bridgeInstallerUnsigned`/`bridgeInstallerDebug`) and
  `build-supply-chain.yml` (`installCargoDeny`). `release-build.yml` and `publish-release.yml` call them
  and run `./gradlew installGitCliff` themselves. All of them use `gradle/actions/setup-gradle` on fresh
  runners.
- `docs/reproducible-builds.md` names local verifiers (`verify/verify.sh`, `verify/verify-rust.sh`,
  `verify/verify-web.sh`, `verify/verify-native.sh`) that are not in the tree today. This change does not
  touch that staleness. Its statement about caches covers any verification build, wherever its script
  lives.

**Goals.**

1. Both entry builds run their common entry points with `--configuration-cache`. The second run of the
   same invocation reuses the entry, with no CC problem left (`problems=fail`). Then CC is on by default
   in both `gradle.properties` files.
2. Every custom task is either correctly cacheable, with all result-affecting inputs declared, or
   explicitly not cacheable, with a stated reason. The build cache stays **off** by default.
3. Release and verification builds never use the build cache or the configuration cache. A contract
   test enforces this.
4. Developers and agent hosts can opt in to the build cache locally, documented in `BUILD.md`.
5. Measured before/after timings and the cache-correctness experiments are recorded in the PR.

**Non-goals.** A remote build cache (Develocity), Bazel/Nx/Moon, and enabling the cache in a machine's
Gradle user home: a separate server-side issue does that once this change lands. Fixing the stale
verifier-script references in `docs/reproducible-builds.md` is also out of scope.

**Anti-goals** (failure even if the build is faster):

- a release or verification artifact restored from a cache;
- a wrong `versionName`/`versionCode` after a tag change;
- a cached Rust library that no longer matches its pinned toolchain, NDK, lockfile or host;
- `problems=warn` used to hide CC problems.

## Alternatives Considered

### Alternative A: Typed CC-safe tasks and an explicit, narrow cache opt-in

- **Approach**: Move each failing script-lambda task into a small typed task class. The class takes
  injected `ExecOperations`/`FileSystemOperations`, its inputs are `Property`/`Provider` values, and its
  file reads go through `providers.fileContents`. Configuration-time environment and process reads go
  through `providers.environmentVariable`/`ValueSource`. Replace `androidgitversion` with a `ValueSource`
  that runs `git` and yields the same name and code. Enable CC with `problems=fail`. Opt in only
  `rustHostJni`, `rustJni` and `rustWasm` to the build cache, with exact inputs. Mark every other custom
  task `@DisableCachingByDefault(because = …)`.
- **Pros**: CC actually takes effect on every entry point. The cache covers the expensive Rust products
  that `assembleDebug`/`testDebugUnitTest` wait on. CI catches CC regressions through `problems=fail`.
  The pattern already exists in the repo.
- **Cons**: The most code: many tasks are rewritten, and the version plugin is replaced. The version
  replacement touches release versioning and needs an equivalence proof.
- **Why chosen**: See Agreed Approach.

### Alternative B: Mark the failing tasks `notCompatibleWithConfigurationCache()`

- **Approach**: Leave the task bodies as they are and declare them CC-incompatible. Opt `android/` out
  of CC.
- **Pros**: Small diff, low risk.
- **Cons**: Gradle silently disables CC for any invocation whose graph contains such a task. The
  toolchain checks sit under almost every entry point (`rustToolchainCheck` precedes `rustHostJni`, which
  `testDebugUnitTest` needs), so CC would almost never take effect.
- **Why not chosen**: It delivers no speed-up, which is the reason for the change.

### Alternative C: Turn `org.gradle.caching=true` on by default and fix what breaks

- **Approach**: Enable the build cache repository-wide and chase failures.
- **Pros**: Maximum cache hits immediately.
- **Cons**: Correctness is proven by absence of failures, not by declared inputs. Release builds would
  restore artifacts unless every workflow overrides the default.
- **Why not chosen**: The issue explicitly rules it out, and it contradicts the reproducibility
  contract.

### Sub-decision: the Android version derivation (user's choice)

- **Replace the plugin with a `ValueSource` (chosen by the user)**: CC can be enabled in `android/`, and
  a new tag correctly invalidates the entry, because Gradle re-runs value sources to check the
  fingerprint.
- **Opt `android/` out of CC**: smallest risk, but the largest local builds get no CC benefit.
- **CC on, only release builds off**: a developer could see a stale version after tagging, and
  `problems=fail` could not be used.

## Agreed Approach

Alternative A, confirmed by the user on 2026-11-20, together with the `ValueSource` replacement of
`androidgitversion`, also the user's choice. It is the only option that makes CC take effect on the
common entry points while keeping every cached result tied to declared inputs. The release guard keeps
the reproducibility contract independent of whatever a developer enables locally.

## Key Decisions

1. **CC on by default, strict.** Set `org.gradle.configuration-cache=true` and
   `org.gradle.configuration-cache.problems=fail` in `gradle.properties` and `android/gradle.properties`.
   (User-approved approach; `problems=fail` is the agent's call, so CI fails on any regression.)
2. **Fix, don't opt out.** Every CC problem on the listed entry points is fixed. An opt-out
   (`notCompatibleWithConfigurationCache` with a reason) is allowed only for a task outside the common
   entry points, and it is documented. (Agent's call.)
3. **Version derivation.** A `ValueSource` replaces `com.gladed.androidgitversion`. It yields the same
   `versionName` and `versionCode` as plugin 0.4.14 under the repository's configuration. This is proven
   on a set of repository states before the plugin is removed, and the `:app:androidGitVersion` task and
   its `androidGitVersion.name<TAB><value>` output line are kept for `build-android.yml`. (User's choice;
   the equivalence proof and the kept task are the agent's calls.)
4. **Build cache opt-in is narrow.** Only `rustHostJni`, `rustJni` and `rustWasm` become cacheable. Their
   declared inputs are the crate sources, `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`,
   `toolchain-tools.toml`, the NDK version (JNI only), the fixed cargo profile environment, the target
   ABIs or target triple, and the host OS and architecture (host JNI only). Caching is refused (`doNotCacheIf`) when any
   ambient cargo or rustc environment variable that could change the output is set. The remap
   `RUSTFLAGS` value is an internal detail: the remap makes the output independent of `CARGO_HOME`, so it
   is not a cache key. (Agent's call within the approved approach.)
5. **Everything else is explicitly non-cacheable.** Tasks that use the network (cargo/npm installs,
   git-cliff download), the host toolchain version (toolchain checks), signing, device state,
   timestamps or the native installer pipeline are marked `@DisableCachingByDefault(because = …)` or
   `outputs.doNotCacheIf(…)`. Verification-only tasks without outputs stay non-cacheable. (Agent's call.)
6. **Release guard.** Every `./gradlew`/`gradlew.bat` invocation in the release-producing workflows
   (`build-android.yml`, `build-rust.yml`, `build-web-extension.yml`, `build-native-windows.yml`,
   `build-native-macos.yml`, `build-supply-chain.yml`, and the `installGitCliff` steps in
   `release-build.yml` and `publish-release.yml`) passes `--no-build-cache --no-configuration-cache`.
   `docs/reproducible-builds.md` states the rule (`--no-build-cache`, equivalently
   `-Dorg.gradle.caching=false`). `verify/test/workflow-contracts.test.mjs` asserts it for every such
   invocation. (User-approved.)
7. **Developer opt-in.** `BUILD.md` documents `--build-cache` and the per-user
   `~/.gradle/gradle.properties` `org.gradle.caching=true`. It states that CI defaults are unchanged and
   that release builds override any user setting. (User-approved.)
8. **Evidence.** The PR records: cold and warm timings before and after (Android entry points from
   `android/`, `rustTest` at the root, then a second fresh worktree sharing only the Gradle user home);
   two runs of each entry point with the CC entry reused; the input-change experiment (edit
   `rust/toolchain-tools.toml` and see `rustJni`/`rustWasm` rerun instead of `FROM-CACHE`); a tag
   experiment (with CC on, a new local `android-v*` tag changes the computed version); version
   equivalence with the old plugin; and an emulator install and launch of the debug APK with its version
   fields checked. (Agent's call, satisfying the issue and the real-device rule.)

## Open Questions

1. **Where the Android runs happen.** This container cannot compile Java. The plan has to choose between
   the home PC (`homepc` skill) and provisioning a JDK 21 with `javac` for the runs. This is settled
   during planning; it does not change what gets built.
2. **The exact plugin semantics.** The name format, branch hiding, dirty marking and the no-tag fallback
   of 0.4.14 are pinned by the equivalence harness, which runs against the plugin itself before removal,
   not by reading documentation.
