## Context

The facts below were observed in this checkout on 2026-11-20. The brainstorm records the probe in full.

- **Builds.** Three Gradle builds share one wrapper (Gradle 9.5.0):
  - the root orchestrator, `build.gradle.kts`, which includes `android` and `rust`;
  - the independent `android/` build (`:app`), which includes `../rust`;
  - the included `rust/` build, also run standalone by `verify/check-android-layout.sh` as
    `cd rust && ../gradlew help`.
- **Properties.** `gradle.properties` and `android/gradle.properties` set neither cache property;
  `rust/` has no `gradle.properties`.
- **Configuration cache (CC) blockers.**
  - Fifteen tasks hold script object references in their actions. Ten are in `rust/build.gradle.kts`:
    the toolchain check, the three `install*` tasks and the three `verify*` tasks of `registerCargoTool`,
    the bindgen consistency check, `rustWasm`, `rustWasmSmoke` and `rustLintAndroid`. Four are in
    `build.gradle.kts`: the `bridge*ToolchainCheck` tasks and `bridgeNodeCheck`.
  - The same pattern occurs in tasks the probe did not reach. They include `registerVerificationCargoTool`,
    `protocolNodeCheck` and `sessionLoopbackCheck` (which reads a file in `doFirst`), and the
    `bridgeBuild`, `bridgeBundle`, `bridgePackage`, `bridgeHostBuild` and installer pipeline tasks. Those
    tasks call `delete(…)` and `providers.exec` and read `System.getenv` inside `doFirst`/`doLast`. In
    `android/app/build.gradle.kts` it occurs in `sessionLoopbackClasspath` and `releaseHookGuard`.
  - The plugin `com.gladed.androidgitversion` 0.4.14 starts `git` at configuration time.
- **Configuration-time reads.** Some files are read with `File.readText()` at configuration time:
  `pinnedToolVersion` and `ndk-version` in `rust/build.gradle.kts`, `pinnedVerificationToolVersion` and
  `pinnedReleaseTool` in `build.gradle.kts`, and `ndk-version` in `android/build.gradle.kts:50`. Gradle
  records these reads as CC inputs, so they are not correctness bugs, but they are the non-provider
  style the issue asks to remove. The bindgen check reads `Cargo.lock` in `doLast`, at execution time.
  `android/app/build.gradle.kts:61-68` reads `VELES_KEYSTORE_*` with `System.getenv` at configuration
  time.
- **Build cache.** `Exec` and `DefaultTask` are never cacheable unless a task opts in, and today no
  custom task does. The existing typed tasks `InstallGitCliffTask` and `InstallGitHooksTask` show the
  target pattern.
- **Invocation inventory.** These were found by `grep` for `gradlew` in `.github/workflows/*.yml`,
  `verify/*.sh` and `rust/scripts/*.sh`:
  - the release producers `build-android.yml`, `build-rust.yml`, `build-web-extension.yml`,
    `build-native-windows.yml`, `build-native-macos.yml` and `build-supply-chain.yml`, which are also
    called on PRs by `ci.yml` and `build-pr-installers.yml`;
  - the `installGitCliff` steps of `release-build.yml` and `publish-release.yml`;
  - the check workflows `check-android.yml`, `check-rust.yml`, `check-protocol.yml`, `check-tooling.yml`,
    `check-web-extension.yml`, `check-bridge-scaffold.yml`, `check-installer.yml` and
    `check-native-host.yml`. The last two run on Linux, Windows and macOS.
  - the scripts `verify/check-android-layout.sh`, `verify/check-root-aliases.sh`,
    `verify/generate-sboms.sh` (called by `verify/verify-supply-chain.sh`),
    `rust/scripts/assert-clean.sh`, `rust/scripts/test-jni-conformance.sh` and
    `rust/scripts/test-ndk-lookup.sh`. `verify/check-root-aliases.sh:79` (`-p "$scratch"`) and
    `test-ndk-lookup.sh` build scratch copies without the repository's `gradle.properties`, so CC is off
    there. Both keep working: `test-ndk-lookup.sh` copies only a few build files into its scratch repo,
    so no new configuration-time file dependency may be added to the code paths it exercises, or it must
    copy that file too.
- **Environment.** This container has the Android SDK, NDK 29.0.14206865, rustup/cargo and Node, but
  its JDK 21 has no `javac`.

## Goals / Non-Goals

**Goals:**
- Every unguarded Gradle invocation in the inventory, except the two scratch-copy builds named above,
  runs with CC on and no CC problem. A second identical run reuses the CC entry.
- Every custom task is either cacheable with a complete, relocatable cache key, or explicitly
  non-cacheable with a reason.
- Guarded invocations, which produce or verify artifacts, never read or write the build cache and never
  reuse a CC entry. A contract test enforces this.
- The Android version stays exactly what plugin 0.4.14 produces, and it follows the git state under CC.
- Developers can opt in to the build cache locally. The PR records timings and correctness evidence.

**Non-Goals:**
- A remote cache, other build systems, or a cache enabled in a machine's Gradle user home (that is a
  separate server-side issue).
- Fixing the stale verifier-script names in `docs/reproducible-builds.md`.
- A shared `build-logic` included build. Classes stay in the scripts that use them, and consolidating
  them is a possible follow-up.

## Decisions

### D1. CC on by default, strict, in all three builds
Add `org.gradle.configuration-cache=true` and `org.gradle.configuration-cache.problems=fail` to
`gradle.properties` and `android/gradle.properties`, and create `rust/gradle.properties` with the same
two lines plus the daemon `jvmargs` of the root file.

`problems=fail` was chosen over `warn` because a warning lets a regression in a new task go unnoticed.
Turning CC on only through CI flags was rejected: local runs, which are the point of the change, would
not benefit.

### D2. Typed tasks replace script lambdas
Each blocked task becomes an abstract task class declared in the same script, like
`InstallGitCliffTask`:
- Services are injected (`ExecOperations`, `FileSystemOperations`).
- Configuration is passed as `Property`/`ListProperty`/`DirectoryProperty`/`RegularFileProperty`.
- Every helper the action needs (`requireOnPath`, `requireNdkVersion`, the NDK lookup, the registry
  remap) moves to a top-level function or companion that takes only plain values. Its environment
  comes from parameters such as `PATH`, `ANDROID_NDK_HOME`, `ANDROID_SDK_ROOT`, `ANDROID_HOME`,
  `CARGO_HOME` and `user.home`, populated from `providers.environmentVariable`/`systemProperty` and the
  `local.properties` contents.

Classes are reused where tasks share a shape:
- `ToolchainCheckTask`: a list of required commands and a version probe. It covers the rust, bridge,
  bridge host and installer checks and `bridgeNodeCheck`/`protocolNodeCheck`.
- `CargoInstallTask` and `VerifyToolVersionTask`: used by both `registerCargoTool` and
  `registerVerificationCargoTool`.
- `CargoExecTask`: a non-cacheable `Exec` subclass for the cargo, cargo-ndk and wasm-pack
  invocations that are not cached (lints, tests, conformance builds). Its environment is resolved from
  providers in its action, which replaces the `doFirst` environment mutation.
- `CargoBuildTask`: a `@CacheableTask` `DefaultTask`, not an `Exec`, used only for the three cacheable
  products (D4). It runs the process through `ExecOperations`.
- **Installer producer pipeline.** The tasks registered by `registerInstallerProducer`, the native
  bundle/package tasks and `bridgeInstallerSymbols` run only in guarded graphs (D6), so they always run
  with `--no-configuration-cache`. Rather than being rewritten, each gets
  `notCompatibleWithConfigurationCache("release/PR packaging pipeline; always run with
  --no-configuration-cache, see docs/reproducible-builds.md")` and is non-cacheable. The
  installer/host/bridge check tasks used by the check workflows (`bridgeInstallerFormat`, `Lint`,
  `Test`, `ScriptsTest`, `ToolObservation`, `bridgeHost*`, `bridge*` scaffold tasks) are made CC-clean
  with the shapes above.

The NDK directory is still resolved at execution time, as `rust/build.gradle.kts` requires, so a
machine without the NDK can configure. The lookup runs inside the task action from its injected
parameters.

Configuration-time file reads (`toolchain-tools.toml`, `cargo-tools.toml`, `release-tools.toml`,
`ndk-version`) go through `providers.fileContents(…).asText`. Regex extraction happens in `map {}`, so
the value is wired lazily into task properties. The bindgen check takes `Cargo.lock` as an
`@InputFile` and reads it in its action.

**Lazy wiring.** Every provider (`providers.environmentVariable("PATH")`, `ValueSource`s such as
`rustc -vV`/`cc --version`, file contents) is passed to task properties unresolved and is never
`.get()` at configuration time. So configuring the build runs no `rustc`, and a `PATH` change does not
invalidate the CC entry unless a configuration-time value depends on it. Values Gradle must know at
configuration time are the exception: the version (D3) and `ndkVersion` for AGP.

**Signing environment.** The `VELES_KEYSTORE_*` reads move to `providers.environmentVariable`. Under CC
they become entry inputs, and AGP stores the resolved signing values in the CC entry, which Gradle
encrypts by default. Signed release builds happen only in guarded CI steps (CC off). `BUILD.md` tells
developers to add `--no-configuration-cache` to a local signed `assembleRelease`.

Marking the tasks `notCompatibleWithConfigurationCache()` was rejected: CC would be disabled for almost
every entry point.

### D3. The Android version comes from a `ValueSource`
The derivation lives in an applied script, `android/git-version.gradle.kts`, applied by
`android/app/build.gradle.kts` the way `.githooks/install-hooks.gradle.kts` is applied. It declares
`abstract class GitVersionValueSource : ValueSource<List<String>, …>`, which returns the
plugin-equivalent `[name, code]`. It takes the repository directory, the prefix `android-v` and the code format `MMNNPP`
as parameters, and runs `git` through an injected `ExecOperations`. The script exposes the provider as
an `extra` property and registers the `androidGitVersion` task. `versionName` and `versionCode` (still
`maxOf(code, 1)`) are read from that provider.

Gradle re-obtains a value source's value whenever it checks a CC entry, so any git change that alters
the result invalidates the entry: a new tag, a new commit, a dirty tree or another branch.

The plugin is removed from `plugins {}` and from `android/gradle/libs.versions.toml`.

A repository task `androidGitVersion` replaces the plugin's task. It prints exactly what 0.4.14 prints,
in the same order, including the `androidGitVersion.name<TAB><value>` and
`androidGitVersion.code<TAB><value>` lines. `build-android.yml` and `workflow-contracts.test.mjs:330`
depend on that output, and it must still appear under `-q` (written with `println`/`logger.quiet`).

**Equivalence is proven, not assumed.** A harness, `android/scripts/check-git-version-parity.sh`
(android node), creates a throwaway git repository with two minimal throwaway Gradle builds that use
the repository's wrapper:
- one applying `id("com.gladed.androidgitversion") version "0.4.14"` from the plugin portal;
- one applying the repository's `android/git-version.gradle.kts`.

For each of the states below, it runs `androidGitVersion` in both builds:
- exactly on `android-v1.2.3`;
- three commits after it;
- dirty;
- on a non-`master` branch;
- with no `android-v*` tag;
- with two tags on one commit;
- on a tag with a pre-release suffix, if the plugin accepts one.

It asserts the outputs are equal. The harness needs network access for the plugin portal, so it is not
a CI gate. It runs during apply, before and after the plugin is removed, with its output recorded in the
PR. It stays in the repository, and `docs/android.md` describes it as the re-proof for any later change
to `git-version.gradle.kts`. The plugin's exact rules (name format, hidden branches, dirty marking, the no-tag fallback) are
taken from this harness and from the 0.4.14 bytecode, never from documentation.

`component-versioning`'s "Initial versions without a tag" (0.1.0, `versionCode` 100) describes the
release planner's first release version, not the APK's no-tag fallback. The fallback stays whatever
0.4.14 yields.

Alternatives were rejected by the user: keeping the plugin and opting `android/` out of CC, or keeping
CC on and off only for releases.

### D4. Narrow, relocatable build-cache opt-in
Only `rustHostJni`, `rustJni` and `rustWasm` are cacheable. Each becomes a `CargoBuildTask`
(`@CacheableTask`, D2).

**No absolute path is in the key.** An `Exec`'s `executable` and `args` are inputs, and today they
contain absolute paths (`--output-dir`/`--out-dir`, the wasm-pack binary). `CargoBuildTask` therefore
has these properties:
- `@Input arguments: ListProperty<String>`, holding only path-free arguments: subcommand, targets,
  ABIs, `--platform 33`, features, `--release`, `--locked`;
- `@OutputDirectory outputDir`;
- `@Internal` for the tool binary, the working directory, `CARGO_TARGET_DIR` and the remap `RUSTFLAGS`.

The arguments are split into `@Input preArguments` and `@Input postArguments`. At execution time the
action inserts the absolute `--output-dir` (cargo-ndk, before the `build` subcommand) or `--out-dir`
(wasm-pack, before `--`) between them. The tool's identity is in
the key through `toolchain-tools.toml`, never through its path.

**Cache key** (all file inputs use `PathSensitivity.RELATIVE`, so worktrees at different paths share
keys; the Rust files are one `fileTree(rust/)` with includes, so `rust/Cargo.toml` and
`veles-crypto/Cargo.toml` keep distinct relative paths):
- the crate sources (`veles-crypto/Cargo.toml`, `veles-crypto/src/**/*.rs`), `rust/Cargo.toml`,
  `rust/Cargo.lock` and `rust/rust-toolchain.toml`;
- `rust/toolchain-tools.toml`, for `rustJni` and `rustWasm`, whose cargo-ndk/wasm-pack versions shape
  the output;
- the output of `rustc -vV`, run in `rust/` through a `ValueSource`, so the exact toolchain build
  (commit hash, host triple, LLVM version) is in the key;
- task constants as `@Input`: the path-free `arguments` above and the fixed
  `CARGO_PROFILE_RELEASE_PANIC`;
- for `rustJni`, the NDK version;
- for `rustHostJni`, `os.name`, `os.arch` and the first line of `cc --version` (the host linker).

`rustHostJni` caching is disabled on Windows, where the MSVC linker identity is not probed.

The registry-remap `RUSTFLAGS` value is `@Internal`. It contains the absolute `CARGO_HOME`, but the
remap exists precisely so the output does not depend on it.

**Refusal rule** (`outputs.doNotCacheIf`, evaluated at execution time). Caching is refused if any of
these is true:
- One of these environment variables is set and non-empty: `CARGO_ENCODED_RUSTFLAGS`,
  `CARGO_BUILD_RUSTFLAGS`, `CARGO_INCREMENTAL`, `RUSTC`, `RUSTC_WRAPPER`, `RUSTC_WORKSPACE_WRAPPER`,
  `RUSTC_BOOTSTRAP`, `RUSTUP_TOOLCHAIN`, `MACOSX_DEPLOYMENT_TARGET`, `SDKROOT`, `CC`, `CXX`, `AR`,
  `CFLAGS`, `CXXFLAGS`, `LDFLAGS`; a variable matching `CARGO_PROFILE_*`, `CARGO_BUILD_*` or
  `CARGO_TARGET_*` other than `CARGO_TARGET_DIR`; or a variable matching `CC_*`, `CFLAGS_*` or `AR_*`. The task itself still sets `CARGO_PROFILE_RELEASE_PANIC` and `RUSTFLAGS`, which
  override any inherited value.
- A cargo configuration file exists that cargo would read: `config` or `config.toml` under
  `$CARGO_HOME` (default `~/.cargo`), or `.cargo/config` or `.cargo/config.toml` in `rust/` or any
  ancestor directory.

Refusing caching is always safe: the task just runs. An environment allowlist (clearing everything
else) was rejected. These tasks also produce the release artifacts in guarded builds, and clearing
their environment would change release behaviour beyond this change's scope.

**Relocation proof.** For each of the three tasks:
1. Populate the cache from worktree A.
2. In worktree B, at a different path, the task must report `FROM-CACHE`.
3. Compare the restored output directory with a fresh `--no-build-cache` build of B, byte for byte and
   with the full file list, dotfiles included. wasm-pack emits `web-extension/rust-wasm/pkg/.gitignore`,
   which `rustPackage` copies.

This confirms that no worktree path is embedded, as the remap comment in `rust/build.gradle.kts`
claims, and that the restored file set is complete. If a dotfile is not restored, that task is not
cacheable (`doNotCacheIf`), with the finding recorded.

### D5. Every other custom task is explicitly non-cacheable
- **Typed task classes** carry `@DisableCachingByDefault(because = "…")`.
- **Remaining ad-hoc `Exec`/`Sync`/`DefaultTask` registrations** get
  `outputs.doNotCacheIf("<reason>") { true }`.
- **Reasons**, by kind of task:
  - network: cargo/npm installs, the git-cliff download;
  - host toolchain probes: the toolchain and Node checks;
  - signing and the native installer pipeline: host toolchains, WiX/NSIS, codesign;
  - device state: `connectedDebugAndroidTest`, which belongs to AGP and is not touched;
  - verification only, without outputs: format, lint, tests, smoke and contract checks;
  - git state: `androidGitVersion`.
- **Sync tasks.** `rustPackage` and the installer stages are not cacheable by Gradle's default and stay
  so.
- **AGP, Kotlin and Java tasks** keep their own cacheability: those plugins declare it, and the guard
  covers releases.

### D6. Release and verification guard
**Flags.** Every guarded invocation adds `--no-build-cache --no-configuration-cache`. That covers every
`./gradlew`/`.\gradlew.bat` call in the six release-producer workflows, including the
`$(./gradlew -q rustNdkVersion)` substitutions; the `installGitCliff` steps; and `verify/generate-sboms.sh`.
The flags go after the task names, as in `./gradlew assembleRelease --no-build-cache
--no-configuration-cache`. That keeps the `./gradlew <task>` prefixes, which existing assertions and
readers match, unchanged.

**Explicit flags, not an environment default.** Flags were chosen over `GRADLE_OPTS=-Dorg.gradle.caching=false`
because they override `~/.gradle/gradle.properties` and are visible per step, which makes them testable.
`docs/reproducible-builds.md` gets a short "Gradle caches" section. It says that release and
verification builds run with `--no-build-cache` (equivalent to `-Dorg.gradle.caching=false`) and
`--no-configuration-cache`, names the guarded files, and says why.

**Contract test.** In `verify/test/workflow-contracts.test.mjs`, the test collects every `gradlew` or
`gradlew.bat` command from the `run:` blocks of the guarded workflows. It uses a regex over each run
block's text that matches `(?:\./|\.\\|\$root/|"\$root"/)gradlew(?:\.bat)?\b`, including inside `$(…)`.
A command ends at the first unquoted `|`, `)`, `;`, `&&`, `||` or line end without a continuation
backslash.
It also covers the guarded `verify/` scripts. It asserts each command carries both flags, and a mutation
check proves the assertion fails when a flag is removed. The scan reuses the suite's existing
`steps()`/`runCommands()` helpers. Some existing assertions may still break even with the flags after
the task names, such as the exact-line replacements near lines 400–405 (`- run: ./gradlew rustWasm\n`)
and the replacement near line 404 on `build-web-extension.yml`. Those are updated in the same change.
The mutation near line 400 targets the unguarded `check-web-extension.yml`, and the inventory test near
line 1243 uses synthetic fixtures, so neither is affected. The assertions near lines 320 and 373 match
prefixes and are expected to keep passing. `native-bridge/installer/scripts/test_build_contract.py:571`
also matches workflow strings (`<gradle> bridgeInstaller*`). It is run as a gate and updated if it
breaks.

**CC in the guarded graphs.** CI never exercises CC there, so `assembleRelease`, `rustPackage`,
`rustWasm` and `installCargoDeny` are probed with CC in this container (with a JDK, see D8).
The installer producer graphs (`bridgeInstallerUnsigned`, `bridgeInstallerDebug`) are opted out (D2)
and are not probed under CC. The proposal names `assembleRelease` and `rustPackage` as its probed
examples.

**Unguarded Windows and macOS jobs.** These are the `check-installer.yml` and `check-native-host.yml`
jobs (`bridgeInstallerFormat`/`Lint`/`Test`, `bridgeHostToolchainCheck`/`Format`/`Lint`/`Test`,
`bridgeHostBuild`).
They are probed with CC on, twice each, on the Windows PC and the MacBook through the `homepc` skill
before the push. CI is the second check, not the only one. A further guarded-graph task that cannot be
made CC-clean is recorded as a documented opt-out with its reason
(`notCompatibleWithConfigurationCache("…")` on that task).

### D7. Developer opt-in documentation
`BUILD.md` gets a "Build caches" section:
- CC is on by default;
- `--build-cache` or `org.gradle.caching=true` in `~/.gradle/gradle.properties` turns the build cache on
  per user;
- what is cached (AGP/Kotlin outputs and the three Rust products) and when Rust caching is refused (D4);
- that CI and release builds are unaffected, because the guard overrides any user setting.

`CONTRIBUTING.md` links to the section from its setup steps. `docs/releasing.md`, `docs/android.md` and
`android/AGENTS.md` replace their plugin references with the repository's derivation (D3).

### D8. Evidence and where it runs
**Where.** Root and Android runs happen in this container with a user-level Temurin JDK 21.0.12 (the
`.java-version`), unpacked outside the repository and selected with `JAVA_HOME`. The SDK and NDK are
already present. Windows and macOS installer probes go through `homepc`. The emulator run goes through
the device tools.

**Timing matrix**, recorded in the PR as wall-clock times from `--profile` or `time`:
1. **Before** (the merge base): in fresh worktree A, run cold `cd android && ./gradlew assembleDebug`,
   then `testDebugUnitTest`, then root `./gradlew rustTest`. Repeat in fresh worktree B, which shares
   only the Gradle user home.
2. **After, defaults** (CC on, build cache off): the same sequence in fresh worktrees, then each command
   once more in the same worktree to show "Reusing configuration cache".
3. **After, `--build-cache`**: worktree A populates the cache, and a fresh worktree B runs the same
   commands with `--build-cache`, showing `FROM-CACHE` for the Rust products and AGP/Kotlin tasks.

**CC reuse.** Every entry point listed in the issue, and every unguarded inventory invocation runnable
on Linux, runs twice. The second run must report the entry reused.

**Experiments:**
- **Input change**: with `--build-cache`, build, then edit `rust/toolchain-tools.toml` (for example, bump
  a comment-free pin to a fake value and revert), and show that `rustJni` and `rustWasm` re-execute and
  do not report `FROM-CACHE`. Repeat with `rust/rust-toolchain.toml` for `rustHostJni`.
- **Git state**: with CC on, `:app:androidGitVersion` before and after creating a local `android-v*`
  tag, and after a new commit, shows the changed output without `--rerun`.
- **Relocation**: see D4.
- **Version equivalence**: the D3 harness output.
- **APK**: the version comes from git state, so both sides are pinned to it. In a clean worktree at the
  merge base, and in a clean worktree at the change's final commit, create the same local tag
  `android-v9.9.9` on `HEAD`. Then build `assembleRelease` (unsigned) and `assembleDebug` in each, with
  `--no-build-cache`. Compare the unsigned release APK checksums, which are reproducible by contract and
  must be equal. Compare the debug APK checksums and report the result. If the debug APKs differ, show
  with `aapt2 dump badging` that the version fields are equal, and name the differing entries. Then
  install the change's debug APK (without the fake tag) on an emulator, launch it, and record
  `dumpsys package me.nagaev.veles` `versionName`/`versionCode` against `:app:androidGitVersion`.

**Gates:**
- `./gradlew scriptsTest`;
- `node --test verify/test/workflow-contracts.test.mjs`;
- `verify/verify-supply-chain.sh`;
- the checks of every node `scripts/components.py changed` selects;
- CI green.

## Risks / Trade-offs

- **Equivalence gaps.** The harness may miss a plugin rule, such as unusual tag shapes. → The harness
  covers every state CI and releases reach (tag on commit, commits after tag, fetch-depth 0, `master`),
  and any mismatch blocks removal of the plugin.
- **A cache key misses an input**, for example an unknown cargo environment variable. → The refusal rule
  is conservative, `rustc -vV` pins the toolchain, and the input-change and relocation experiments test
  the key. Release builds never use the cache, so the residual risk is local only.
- **`problems=fail` breaks a CI job nobody probed.** → The inventory sets the probe list, and any
  unprobed job that fails gets a fix or a documented opt-out in the same change before merge.
- **CC hides configuration-time logic behind a stale entry.** → All configuration-time environment,
  file and process reads go through providers, and Gradle tracks those as CC inputs.
- **Rewrites change task behaviour**, for example error messages or the NDK lookup order. → Each typed
  task keeps the existing messages and order. `rust/scripts/test-ndk-lookup.sh`, `assert-clean.sh`,
  `check-android-layout.sh` and `check-root-aliases.sh` run as regression gates.
- **Guarded graphs are CC-probed only manually.** → D6 probes them, and a later CI change could add a
  CC-on dry run of them.
- **Trade-off.** Release builds get no speed-up, by design.

## Migration Plan

The change lands in one PR. Rollback means reverting it, or a developer can set
`org.gradle.configuration-cache=false` locally. Nothing persistent changes: no artifact format, no
version, no cache in CI. The server-side Gradle-home cache issue starts only after this merges.

## Open Questions

None that block planning. The plugin's exact rules are pinned by the harness (D3). Platform opt-outs, if
any, are recorded in the PR (D6).
