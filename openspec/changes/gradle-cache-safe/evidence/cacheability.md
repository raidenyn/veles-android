# Cacheability audit — every script-registered task

Task 4.5/5.4, design D5; refreshed after Task 7 for the `rustWasm` fallback (`190ceaff`). Audited files:
`build.gradle.kts`, `rust/build.gradle.kts`, `android/build.gradle.kts`,
`android/app/build.gradle.kts`, `android/git-version.gradle.kts`,
`.githooks/install-hooks.gradle.kts` (the last one is applied by both the root and the Android build;
it registers one task).

```text
$ grep -nE 'tasks\.(register|named)|abstract class \w+Task' \
    build.gradle.kts rust/build.gradle.kts android/build.gradle.kts \
    android/app/build.gradle.kts android/git-version.gradle.kts .githooks/install-hooks.gradle.kts
```

Generated names were expanded with `./gradlew tasks --all` (`/tmp/opencode/t5logs/tasks-all.log`,
Gradle 9.5.0, `BUILD SUCCESSFUL`, exit 0). The three expanded helpers are
`registerCargoTool` (`installCargoNdk`/`verifyCargoNdk`, `installWasmPack`/`verifyWasmPack`,
`installWasmBindgenCli`/`verifyWasmBindgen`), `registerVerificationCargoTool`
(`installCargoCyclonedx`/`verifyCargoCyclonedx`, `installCargoDeny`/`verifyCargoDeny`) and
`registerInstallerProducer` (three producers, table at the end).

Mechanisms are one of:

- **cacheable product** — `CargoBuildTask`, the only `@CacheableTask` in the repository. Two tasks use it
  as a product: `rustHostJni` and `rustJni` (spec *Only the Rust products are cacheable, with a
  relocatable key*). `rustWasm` uses the same class but carries an always-true `doNotCacheIf` since the
  relocation experiment failed (Task 7.3), so it is non-cacheable.
- **typed, non-cacheable** — a task class annotated `@DisableCachingByDefault(because = …)`.
- **`doNotCacheIf(<reason>)`** — a task that declares the reason inline.
- **`Sync`/`Copy`/`Delete`** — a never-cached Gradle type.

Plugin-provided tasks (AGP, Kotlin, Java, Spotless, `base` lifecycle) are not script-registered and keep
the cacheability their plugins declare (D5); `check` is the `base` lifecycle task the script only
configures, and `clean` is the `base` `Delete`.

## Root `build.gradle.kts`

| Task | Mechanism | Reason |
| --- | --- | --- |
| `rustToolchainCheck`, `installCargoNdk`, `verifyCargoNdk`, `installWasmPack`, `verifyWasmPack`, `installWasmBindgenCli`, `verifyWasmBindgen`, `rustWasmBindgenConsistency`, `rustInstall`, `rustWasmLockCheck`, `rustWasm`, `rustNativeTest`, `rustWasmSmoke`, `rustWasmPairingTest`, `rustWasmEntropyTest`, `rustWasmExpiryTest`, `rustWasmMemoryBoundTest`, `rustWasmResponseErasureTest`, `rustTest`, `rustWasmConformance`, `rustWasmConformanceTest`, `rustHostJni`, `rustJniConformance`, `rustJni`, `rustPackage`, `rustFormat`, `rustLintHost`, `rustLintHostJni`, `rustLintWasm`, `rustLintAndroid`, `rustLint`, `rustNdkVersion`, `rustNdkDir` | `doNotCacheIf` | forwards to the included rust build |
| `assembleDebug`, `assembleRelease`, `testDebugUnitTest`, `lintDebug`, `detekt`, `connectedDebugAndroidTest` | `doNotCacheIf` | forwards to the included android build |
| `spotlessCheck`, `spotlessApply` | `doNotCacheIf` | forwards to the included android build |
| `installGitCliff` | `InstallGitCliffTask`, typed non-cacheable | downloads a release archive |
| `scriptsTest` | `Exec` + `doNotCacheIf` | runs the tooling test suite |
| `installCargoCyclonedx`, `installCargoDeny` | `CargoInstallTask`, typed non-cacheable | downloads crates from the network |
| `verifyCargoCyclonedx`, `verifyCargoDeny` | `VerifyToolVersionTask`, typed non-cacheable | probes an installed tool |
| `protocolNodeCheck`, `bridgeNodeCheck`, `bridgeRustToolchainCheck`, `bridgeHostToolchainCheck`, `bridgeInstallerToolchainCheck` | `ToolchainCheckTask`, typed non-cacheable | probes the host toolchain |
| `protocolInstall` | `Exec` + `doNotCacheIf` | installs npm dependencies from the network |
| `protocolWebInstall` | `Exec` + `doNotCacheIf` | installs npm dependencies from the network |
| `protocolContractCheck` | `Exec` + `doNotCacheIf` | regenerates crypto evidence |
| `protocolFreshEvidenceCheck` | `Exec` + `doNotCacheIf` | regenerates crypto evidence |
| `protocolRustTest`, `protocolWebTest`, `protocolBridgeTest`, `bridgeNpmTest`, `bridgeRustTest`, `bridgeHostTest` | `Exec` + `doNotCacheIf` | runs a test |
| `sessionLoopbackCheck` | `SessionLoopbackTask`, typed non-cacheable | verification only, runs the cross-endpoint loopback |
| `bridgeInstall` | `Exec` + `doNotCacheIf` | installs npm dependencies from the network |
| `bridgeFormat`, `bridgeLint`, `bridgeTypecheck`, `bridgeRustFormat`, `bridgeRustLint`, `bridgeHostFormat`, `bridgeHostLint` | `Exec` + `doNotCacheIf` | verification only |
| `bridgeTest` | `DefaultTask` + `doNotCacheIf` | aggregates test tasks |
| `bridgeBuild` | `Exec` + `notCompatibleWithConfigurationCache` + `doNotCacheIf` | release packaging |
| `bridgeBundle` | `Exec` + `notCompatibleWithConfigurationCache` + `doNotCacheIf` | release packaging |
| `bridgeManifests` | `Exec` + `notCompatibleWithConfigurationCache` + `doNotCacheIf` | release packaging |
| `bridgePackage` | `Exec` + `notCompatibleWithConfigurationCache` + `doNotCacheIf` | release packaging |
| `bridgeHostBuild` | `HostBuildTask`, typed non-cacheable | builds and stages the production host binary |
| `bridgeInstallerFormat`, `bridgeInstallerLint`, `bridgeInstallerTest`, `bridgeInstallerScriptsTest` | `CargoExecTask`, typed non-cacheable | verification or test-only build |
| `bridgeInstallerToolObservation` | `ToolObservationTask`, typed non-cacheable | observes this host's tool identities |
| `bridgeInstallerToolCheck` | `ToolCheckTask`, typed non-cacheable | checks this host's tool identities against the lock |
| `bridgeInstallerSupplyChain` | `Exec` + `doNotCacheIf` | runs the supply-chain policy |
| `protocolCheck` | `DefaultTask` + `doNotCacheIf` | aggregates verification tasks |
| `check` (`tasks.named`) | `base` lifecycle task, configured with `dependsOn` only | no action and no outputs; not cacheable |
| `clean` (`tasks.named<Delete>`) | `Delete` | never cached |

## `registerInstallerProducer` — every generated task

All of them are `Exec` (or `Sync`) and call `markPackagingPipeline()`:
`notCompatibleWithConfigurationCache("release/PR packaging pipeline; always run with
--no-configuration-cache, see docs/reproducible-builds.md")` plus
`outputs.doNotCacheIf("release packaging") { true }`. The same reason string is in
`docs/reproducible-builds.md` (Task 9) and `evidence/cc-optouts.md`.

| Tasks | Mechanism | Reason |
| --- | --- | --- |
| `bridgeInstallerUnsignedConfig`, `bridgeInstallerPackageFixtureConfig`, `bridgeInstallerDebugConfig` | `Exec` + pipeline opt-out | release packaging |
| `bridgeInstallerUnsigned`, `bridgeInstallerPackageFixture`, `bridgeInstallerDebug` (summary tasks) | `Exec` + pipeline opt-out | release packaging |
| `bridgeInstallerUnsignedEngineA`, `bridgeInstallerUnsignedEngineB`, `bridgeInstallerPackageFixtureEngineA`, `bridgeInstallerPackageFixtureEngineB`, `bridgeInstallerDebugEngineA` | `Exec` + pipeline opt-out | release packaging |
| `bridgeInstallerUnsignedHostA`, `bridgeInstallerUnsignedHostB`, `bridgeInstallerPackageFixtureHostA`, `bridgeInstallerPackageFixtureHostB`, `bridgeInstallerDebugHostA` | `Exec` + pipeline opt-out | release packaging |
| `bridgeInstallerUnsignedFrontendA`, `bridgeInstallerUnsignedFrontendB`, `bridgeInstallerPackageFixtureFrontendA`, `bridgeInstallerPackageFixtureFrontendB`, `bridgeInstallerDebugFrontendA` | `Exec` + pipeline opt-out | release packaging |
| `bridgeInstallerUnsignedUninstallerA`, `bridgeInstallerUnsignedUninstallerB`, `bridgeInstallerPackageFixtureUninstallerA`, `bridgeInstallerPackageFixtureUninstallerB`, `bridgeInstallerDebugUninstallerA` | `Exec` + pipeline opt-out | release packaging |
| `bridgeInstallerUnsignedStageA`, `bridgeInstallerUnsignedStageB`, `bridgeInstallerPackageFixtureStageA`, `bridgeInstallerPackageFixtureStageB`, `bridgeInstallerDebugStageA` | `Sync` + pipeline opt-out | release packaging; `Sync` is never cached |
| `bridgeInstallerUnsignedAssembleA`, `bridgeInstallerUnsignedAssembleB`, `bridgeInstallerPackageFixtureAssembleA`, `bridgeInstallerPackageFixtureAssembleB`, `bridgeInstallerDebugAssembleA` | `Exec` + pipeline opt-out | release packaging |
| `bridgeInstallerUnsignedCarrierCheckA`, `bridgeInstallerUnsignedCarrierCheckB`, `bridgeInstallerPackageFixtureCarrierCheckA`, `bridgeInstallerPackageFixtureCarrierCheckB`, `bridgeInstallerDebugCarrierCheckA` | `Exec` + pipeline opt-out | release packaging |
| `bridgeInstallerUnsignedEngineEmbeddedA`, `bridgeInstallerUnsignedEngineEmbeddedB`, `bridgeInstallerPackageFixtureEngineEmbeddedA`, `bridgeInstallerPackageFixtureEngineEmbeddedB`, `bridgeInstallerDebugEngineEmbeddedA` | `Exec` + pipeline opt-out | release packaging |
| `bridgeInstallerUnsignedStageEmbeddedA`, `bridgeInstallerUnsignedStageEmbeddedB`, `bridgeInstallerPackageFixtureStageEmbeddedA`, `bridgeInstallerPackageFixtureStageEmbeddedB`, `bridgeInstallerDebugStageEmbeddedA` | `Sync` + pipeline opt-out | release packaging; `Sync` is never cached |
| `bridgeInstallerUnsignedAssembleFinalA`, `bridgeInstallerUnsignedAssembleFinalB`, `bridgeInstallerPackageFixtureAssembleFinalA`, `bridgeInstallerPackageFixtureAssembleFinalB`, `bridgeInstallerDebugAssembleFinalA` | `Exec` + pipeline opt-out | release packaging |
| `bridgeInstallerUnsignedEmbedCarrierA`, `bridgeInstallerUnsignedEmbedCarrierB`, `bridgeInstallerPackageFixtureEmbedCarrierA`, `bridgeInstallerPackageFixtureEmbedCarrierB`, `bridgeInstallerDebugEmbedCarrierA` | `Exec` + pipeline opt-out | release packaging |
| `bridgeInstallerUnsignedVerifyCarrierA`, `bridgeInstallerUnsignedVerifyCarrierB`, `bridgeInstallerPackageFixtureVerifyCarrierA`, `bridgeInstallerPackageFixtureVerifyCarrierB`, `bridgeInstallerDebugVerifyCarrierA` | `Exec` + pipeline opt-out | release packaging |
| `bridgeInstallerDebugSymbols` | `DefaultTask` + pipeline opt-out | release packaging; debug producer only |

`bridgeInstallerUnsigned` and `bridgeInstallerPackageFixture` register slots `A` and `B`;
`bridgeInstallerDebug` registers only slot `A` (and the symbols task). `bridgeInstallerSymbols` from the
plan is registered as `bridgeInstallerDebugSymbols`; no `bridgeInstallerPackageFixtureSymbols` exists.
The full list matches `./gradlew tasks --all`.

## `rust/build.gradle.kts`

| Task | Mechanism | Reason |
| --- | --- | --- |
| `rustHostJni` | `CargoBuildTask` (`@CacheableTask`) | **cacheable product**: path-free key (sources RELATIVE, cargo args, rustc version, host identity, `cc --version`), outputs `build/rust/host-jni`; refused when the inherited environment or a cargo config can change the output, and on Windows where the MSVC linker is not probed |
| `rustWasm` | `doNotCacheIf` on `CargoBuildTask` | **non-cacheable** since `190ceaff`: wasm-pack's `pkg/.gitignore` is lost on cache restore, so a restored package lacks a file a fresh build writes; relocation experiment in `evidence/cache-experiments.md` (7.3) |
| `rustJni` | `CargoBuildTask` (`@CacheableTask`) | **cacheable product**: path-free key (sources RELATIVE, cargo-ndk args, rustc version, NDK version, `toolchain-tools.toml`), outputs `build/rust/jniLibs`; same inherited-environment/cargo-config refusal |
| `rustToolchainCheck` | `ToolchainCheckTask`, typed non-cacheable | probes the host toolchain |
| `installCargoNdk`, `installWasmPack`, `installWasmBindgenCli` | `CargoInstallTask`, typed non-cacheable | downloads crates from the network |
| `verifyCargoNdk`, `verifyWasmPack`, `verifyWasmBindgen` | `VerifyToolVersionTask`, typed non-cacheable | probes an installed tool |
| `rustWasmBindgenConsistency` | `BindgenConsistencyTask`, typed non-cacheable | verifies that two pinned tool versions agree |
| `rustInstall` | `DefaultTask` + `doNotCacheIf` | aggregates install and verify tasks |
| `rustWasmLockCheck` | `CargoExecTask`, typed non-cacheable | verification or test-only build |
| `rustNativeTest`, `rustWasmSmoke`, `rustWasmPairingTest`, `rustWasmEntropyTest`, `rustWasmExpiryTest`, `rustWasmMemoryBoundTest`, `rustWasmResponseErasureTest`, `rustWasmConformance`, `rustWasmConformanceTest`, `rustJniConformance`, `rustFormat`, `rustLintHost`, `rustLintHostJni`, `rustLintWasm`, `rustLintAndroid` | `CargoExecTask`, typed non-cacheable | verification or test-only build |
| `rustTest` | `DefaultTask` + `doNotCacheIf` | aggregates native and WASM test tasks |
| `rustLint` | `DefaultTask` + `doNotCacheIf` | aggregates lint tasks |
| `rustPackage` | `Sync` | never cached |
| `rustNdkVersion` | `PrintNdkVersionTask`, typed non-cacheable | prints the pinned NDK version |
| `rustNdkDir` | `PrintNdkDirectoryTask`, typed non-cacheable | prints the resolved NDK directory |

## `android/build.gradle.kts`

| Task | Mechanism | Reason |
| --- | --- | --- |
| `rustNdkVersion` | plain `DefaultTask` with `dependsOn(gradle.includedBuild("rust").task(":rustNdkVersion"))` + `doNotCacheIf` | forwards to the included rust build |
| `installGitHooks` (applied from `.githooks/install-hooks.gradle.kts`, below) | `InstallGitHooksTask`, typed non-cacheable | writes local git config |

## `android/app/build.gradle.kts`

| Task | Mechanism | Reason |
| --- | --- | --- |
| `sessionLoopbackClasspath` | `SessionLoopbackClasspathTask`, typed non-cacheable | writes a generated classpath file |
| `protocolJvmTest` | `Test` + `doNotCacheIf` | loads the Rust host JNI library from build/rust/host-jni, which is not a task input; a cache hit could skip testing a changed native library, so it is never cached |
| `releaseHookGuard` | `ReleaseHookGuardTask`, typed non-cacheable | verification only: inspects the release APK and writes no output |

AGP's own tasks are not script-registered. The two tasks the script reconfigures,
`merge*JniLibFolders` (via `tasks.matching { … }.configureEach`) and `testDebugUnitTest`, keep AGP's
declared cacheability; `connectedDebugAndroidTest` is device-bound and belongs to AGP (D5).

## `android/git-version.gradle.kts`

| Task | Mechanism | Reason |
| --- | --- | --- |
| `androidGitVersion` | `AndroidGitVersionTask`, typed non-cacheable | reads git state |

## `.githooks/install-hooks.gradle.kts`

| Task | Mechanism | Reason |
| --- | --- | --- |
| `installGitHooks` | `InstallGitHooksTask`, typed non-cacheable | writes local git config |

## Result

Every script-registered task is accounted for: the two cacheable `CargoBuildTask` products (`rustHostJni`,
`rustJni`), a typed class or `doNotCacheIf` reason carrying its `because`/reason string, or a
`Sync`/`Delete`. `rustWasm` is a `CargoBuildTask` made non-cacheable by its `doNotCacheIf` after the
relocation experiment (`evidence/cache-experiments.md` 7.3). The only script-configured task without its
own marker is the `base` lifecycle `check`, which has no action and no outputs and is not cacheable.
Every `Test` the script registers (`protocolJvmTest`) is explicitly excluded from the build cache because
it loads a native library Gradle cannot key. No task relies on the implicit "has outputs" default without
a stated reason.
