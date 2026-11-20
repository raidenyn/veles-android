## ADDED Requirements

### Requirement: Configuration cache is on and strict in every build
`gradle.properties`, `android/gradle.properties` and `rust/gradle.properties` SHALL set
`org.gradle.configuration-cache=true` and `org.gradle.configuration-cache.problems=fail`. The new
`rust/gradle.properties` MUST also carry the root file's `org.gradle.jvmargs` line.

#### Scenario: Properties present
- **WHEN** the three `gradle.properties` files are read
- **THEN** each sets `org.gradle.configuration-cache=true` and `org.gradle.configuration-cache.problems=fail`

#### Scenario: A problem fails the build
- **WHEN** a task action holds a Gradle script object reference and is run from any of the three builds without `--no-configuration-cache`
- **THEN** the build fails with a configuration-cache problem instead of warning

### Requirement: Common entry points reuse the configuration cache
Every unguarded Gradle invocation in `.github/workflows/*.yml`, `verify/*.sh` and `rust/scripts/*.sh`
SHALL complete with no configuration-cache problem, on each operating system CI runs it on. Two kinds of
invocation are exempt:
- the scratch-copy builds of `verify/check-root-aliases.sh` and `rust/scripts/test-ndk-lookup.sh`,
  which carry no `gradle.properties`;
- the guarded invocations of *Release and verification builds bypass both caches*.

For every such invocation that needs no device, a second identical invocation in the same checkout MUST
report that the configuration cache entry was reused. The invocations include:
- from `android/`: `assembleDebug`, `testDebugUnitTest`, `spotlessCheck detekt lintDebug`,
  `:app:releaseHookGuard`, `-q rustNdkVersion`, and `connectedDebugAndroidTest` (no problem only);
- from the root: `rustFormat rustLint rustTest`, `scriptsTest`, `protocolCheck`, `rustInstall`,
  `rustHostJni`, `rustWasm`, `assembleDebug`, `clean`, `-q rustNdkVersion`,
  `assembleDebug testDebugUnitTest rustHostJni rustJni`, and the `bridge*` check entry points
  (`bridgeFormat bridgeLint bridgeTypecheck bridgeRustFormat bridgeRustLint`, `bridgeNpmTest
  bridgeRustTest`, `bridgeHostToolchainCheck bridgeHostFormat bridgeHostLint bridgeHostTest`,
  `bridgeHostBuild`, `bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest`,
  `bridgeInstallerScriptsTest`), on Linux and, where CI runs them there, on Windows and macOS;
- from `rust/`: `../gradlew help`.

A task that cannot be made compatible SHALL be listed with its reason in `docs/reproducible-builds.md`
and MUST carry `notCompatibleWithConfigurationCache` with that reason. Only tasks that run solely in
guarded invocations (see *Release and verification builds bypass both caches*) MAY be opted out.

#### Scenario: Second run reuses the entry
- **WHEN** `cd android && ./gradlew testDebugUnitTest` runs twice in a row with no file change
- **THEN** the second run reports "Reusing configuration cache"

#### Scenario: Root Rust checks reuse the entry
- **WHEN** `./gradlew rustFormat rustLint rustTest` runs twice in a row with no file change
- **THEN** the second run reports "Reusing configuration cache" and no configuration-cache problem

#### Scenario: A configuration-time file change invalidates the entry
- **WHEN** `rust/ndk-version`, which the Android build reads at configuration time for AGP's `ndkVersion`, changes between two identical `cd android && ./gradlew assembleDebug` invocations
- **THEN** the second invocation recalculates the task graph instead of reusing the entry

#### Scenario: A lazily read pin is never stale
- **WHEN** `rust/toolchain-tools.toml` changes between two identical `./gradlew rustInstall` invocations
- **THEN** the second invocation installs and verifies the new pinned versions, whether or not the entry is reused

### Requirement: Configuration-time reads go through providers
Build logic SHALL read files, environment variables, system properties and process output at
configuration time only through Gradle providers (`providers.fileContents`,
`providers.environmentVariable`, `providers.systemProperty`, `providers.exec` or a `ValueSource`), and
MUST NOT read files directly (including `File.readText`, streams and `Properties.load` on
`local.properties`), call `System.getenv` or start a process at configuration time. A new
configuration-time file read on a code path that `rust/scripts/test-ndk-lookup.sh` exercises MUST be
copied into that script's scratch repository.

#### Scenario: Pinned tool versions
- **WHEN** a build script needs a pin from `rust/toolchain-tools.toml`, `verify/cargo-tools.toml`, `scripts/release-tools.toml` or `rust/ndk-version`
- **THEN** it obtains the contents through `providers.fileContents`

#### Scenario: Signing environment
- **WHEN** `android/app/build.gradle.kts` reads `VELES_KEYSTORE_FILE`, `VELES_KEYSTORE_PASSWORD`, `VELES_KEY_ALIAS` or `VELES_KEY_PASSWORD`
- **THEN** it uses `providers.environmentVariable`

### Requirement: Only the Rust products are cacheable, with a relocatable key
`rustHostJni`, `rustJni` and `rustWasm` SHALL be cacheable tasks whose cache key contains:
- one file tree rooted at `rust/` with `veles-crypto/Cargo.toml`, `veles-crypto/src/**/*.rs`,
  `Cargo.toml`, `Cargo.lock` and `rust-toolchain.toml`;
- `rust/toolchain-tools.toml`, for `rustJni` and `rustWasm`;
- the output of `rustc -vV` run in `rust/`;
- the task's path-free arguments and its fixed cargo profile environment;
- the NDK version, for `rustJni`;
- the host OS name, the OS architecture and the first line of `cc --version`, for `rustHostJni`.

File inputs MUST use relative path sensitivity, and no absolute path MUST be part of the key.
`rustHostJni` MUST NOT be cached on Windows. A product whose relocation scenario below fails (different
bytes or a missing file) MUST instead be made non-cacheable with that reason, and the failure MUST be
recorded in the change's PR.

#### Scenario: Pinned input change reruns
- **WHEN** `rustJni` and `rustWasm` were built with `--build-cache` and `rust/toolchain-tools.toml` then changes
- **THEN** the next `--build-cache` build executes `rustJni` and `rustWasm` instead of reporting `FROM-CACHE`

#### Scenario: Toolchain file change reruns the host library
- **WHEN** `rustHostJni` was built with `--build-cache` and `rust/rust-toolchain.toml` then changes
- **THEN** the next `--build-cache` build executes `rustHostJni`

#### Scenario: Another worktree hits the cache
- **WHEN** worktree A built `rustJni` with `--build-cache`, and worktree B at a different path with identical inputs and the same Gradle user home runs `rustJni --build-cache`
- **THEN** B reports `rustJni` `FROM-CACHE`, and the restored `build/rust/jniLibs` equals a fresh `--no-build-cache` build of B byte for byte with the same file list, or `rustJni` is non-cacheable with the failure recorded

#### Scenario: Restored WASM package is complete
- **WHEN** `rustWasm` is restored `FROM-CACHE`
- **THEN** `web-extension/rust-wasm/pkg/` has the same files, dotfiles included, as a fresh build, or `rustWasm` is non-cacheable with the failure recorded

### Requirement: Ambient toolchain overrides refuse caching
A cacheable Rust task SHALL NOT be loaded from or stored to the build cache when any of these holds,
judged on the environment inherited from the Gradle process, not on the variables the task sets itself:
- a non-empty environment variable from this list is set: `CARGO_ENCODED_RUSTFLAGS`,
  `CARGO_BUILD_RUSTFLAGS`, `CARGO_INCREMENTAL`, `RUSTC`, `RUSTC_WRAPPER`, `RUSTC_WORKSPACE_WRAPPER`,
  `RUSTC_BOOTSTRAP`, `RUSTUP_TOOLCHAIN`, `MACOSX_DEPLOYMENT_TARGET`, `SDKROOT`, `CC`, `CXX`, `AR`,
  `CFLAGS`, `CXXFLAGS`, `LDFLAGS`;
- a non-empty environment variable matches `CARGO_PROFILE_*`, `CARGO_BUILD_*`, `CC_*`, `CFLAGS_*`,
  `AR_*`, or `CARGO_TARGET_*` other than `CARGO_TARGET_DIR`;
- a cargo configuration file exists: `config` or `config.toml` under `CARGO_HOME` (default `~/.cargo`),
  or `.cargo/config` or `.cargo/config.toml` in `rust/` or any of its ancestor directories.

The task MUST set its own `RUSTFLAGS` (the registry remap) and `CARGO_PROFILE_RELEASE_PANIC`,
overriding any inherited value of either.

#### Scenario: Wrapper set
- **WHEN** `RUSTC_WRAPPER=sccache ./gradlew rustHostJni --build-cache` runs
- **THEN** `rustHostJni` executes and its output is not stored in the build cache

#### Scenario: User cargo config present
- **WHEN** `~/.cargo/config.toml` exists and `./gradlew rustJni --build-cache` runs
- **THEN** `rustJni` is neither loaded from nor stored to the build cache

### Requirement: Every other custom task is explicitly non-cacheable
Every task the repository's build scripts register, other than the three cacheable Rust products,
SHALL be non-cacheable. Each is either declared non-cacheable with a stated reason, through
`@DisableCachingByDefault(because = …)` or `outputs.doNotCacheIf(<reason>)`, or is of a type Gradle never
caches (`Sync`, `Copy`, `Delete`). Tasks that use the network, probe the host toolchain, sign, depend on
device state or git state, or package native installers MUST be among them. Tasks of AGP, Kotlin and
Java plugins keep their own declarations.

#### Scenario: Toolchain check
- **WHEN** `./gradlew rustToolchainCheck --build-cache` runs twice
- **THEN** the task executes both times and never reports `FROM-CACHE`

#### Scenario: Network install
- **WHEN** `./gradlew installGitCliff --build-cache` runs on a clean build directory
- **THEN** the task executes instead of reporting `FROM-CACHE`

### Requirement: The build cache stays off by default
No committed `gradle.properties` SHALL set `org.gradle.caching=true`.

#### Scenario: Default build
- **WHEN** `./gradlew rustJni` runs with no user-level `org.gradle.caching` setting and no `--build-cache`
- **THEN** no task is loaded from or stored to the build cache

### Requirement: Release and verification builds bypass both caches
Every Gradle invocation in these places SHALL pass `--no-build-cache` and `--no-configuration-cache`,
placed after the task names:
- `build-android.yml`, `build-rust.yml`, `build-web-extension.yml`, `build-native-windows.yml`,
  `build-native-macos.yml` and `build-supply-chain.yml`, including invocations inside command
  substitutions and `gradlew.bat` invocations;
- the `installGitCliff` steps of `release-build.yml` and `publish-release.yml`;
- `verify/generate-sboms.sh`.

`docs/reproducible-builds.md` MUST state this rule, which is equivalent to `-Dorg.gradle.caching=false`
for the build cache, and name these files. `verify/check-android-layout.sh` and
`verify/check-root-aliases.sh` are layout checks and MUST NOT be guarded.

#### Scenario: Release APK build
- **WHEN** `build-android.yml` builds the release APK
- **THEN** its step runs `./gradlew assembleRelease --no-build-cache --no-configuration-cache`

#### Scenario: Substituted invocation
- **WHEN** `build-rust.yml` reads the NDK version with `$(./gradlew -q rustNdkVersion …)`
- **THEN** that invocation also carries `--no-build-cache --no-configuration-cache`

#### Scenario: User opt-in does not reach releases
- **WHEN** a machine's `~/.gradle/gradle.properties` sets `org.gradle.caching=true` and a guarded invocation runs
- **THEN** no task of that invocation is loaded from or stored to the build cache

### Requirement: A contract test enforces the guard
`verify/test/workflow-contracts.test.mjs` SHALL find every `gradlew`/`gradlew.bat` command in the
guarded workflows' `run:` blocks, including inside `$(…)`, and in `verify/generate-sboms.sh`, and MUST
fail when any of them lacks `--no-build-cache` or `--no-configuration-cache`. A mutation check MUST show
that the assertion fails when a flag is removed.

#### Scenario: Flag removed
- **WHEN** `--no-build-cache` is removed from the `rustPackage` step of `build-rust.yml`
- **THEN** `node --test verify/test/workflow-contracts.test.mjs` fails naming that file and command

#### Scenario: Command inside a substitution
- **WHEN** a guarded workflow contains `NDK_VERSION="$(./gradlew -q rustNdkVersion)"` without the flags
- **THEN** the contract test fails

### Requirement: Developers can opt in to the build cache locally
`BUILD.md` SHALL document that the configuration cache is on by default, and how to enable the build
cache with `--build-cache` or a user-level `org.gradle.caching=true` in `~/.gradle/gradle.properties`.
It MUST also document what is cached, when Rust caching is refused, that CI and release builds are
unaffected, and that a local signed `assembleRelease` adds `--no-configuration-cache`.
`CONTRIBUTING.md` MUST link to that section.

#### Scenario: Developer reads the opt-in
- **WHEN** a developer looks up build caching in `BUILD.md`
- **THEN** they find the `--build-cache` flag, the user-level property, the refusal conditions and the release exception
