# gradle-cache-safe — evidence summary

Makes both Gradle entry builds configuration-cache safe and strict, replaces the
`com.gladed.androidgitversion` 0.4.14 plugin with a `ValueSource` derivation, gives the Rust products a
correct build-cache contract, and guards every release/verification build against both caches. Build
cache stays off by default. Full transcripts and method: `openspec/changes/gradle-cache-safe/evidence/`.

## Timings

Measured in the development container (Linux, 6 CPUs, 15 GiB, JDK 21, Gradle 9.5.0, same SDK/NDK).
Before = merge base `597ee2c6`, no configuration cache (CC), no build cache. After = change at
`22e49558`, CC on, build cache off. Two fresh worktrees per matrix, three commands in order.

| Entry point | Before cold (A / B) | After cold (A / B) | After, same command again (A / B) |
| --- | --- | --- | --- |
| `cd android && ./gradlew assembleDebug` | 3m 22.9s / 3m 50.7s | 3m 53.1s / 3m 41.7s | **9.8s / 10.5s** |
| `cd android && ./gradlew testDebugUnitTest` | 4m 5.7s / 3m 0.8s | 2m 15.9s / 2m 7.8s | **9.4s / 4.6s** |
| `./gradlew rustTest` | 5m 40.3s / 6m 9.5s | 4m 29.4s / 4m 47.3s | **3.2s / 3.0s** |

Cold cost is the same order as before (machine noise and dependency-cache warmth); the gain is the
second invocation of each entry point, which falls from a full task-graph calculation to 3–11 s with
`Reusing configuration cache.`

With `--build-cache` (fresh worktrees; local cache 0 → 46 → 72 entries): `assembleDebug` 3m 5s cold,
then **2m 9.1s** in a fresh worktree with 17 tasks `FROM-CACHE` (`:rust:rustJni` + 16 AGP tasks);
`testDebugUnitTest` **1m 36.7s** with 3 from cache (`:rust:rustHostJni`,
`:app:javaPreCompileDebugUnitTest`, `:app:hiltAggregateDepsDebugUnitTest`); `rustTest` stays 4m 34.2s
because `rustWasm` is deliberately non-cacheable; `rustPackage` in a fresh worktree restores
`:rust:rustJni` but is still 4m 55.6s.

## Configuration cache reuse and strictness

`org.gradle.configuration-cache=true` and `problems=fail` are committed in the root,
`android/` and `rust/` `gradle.properties`. Every unguarded invocation probed stores once and then
prints `Reusing configuration cache.` with zero problems:

- root gate `./gradlew rustFormat rustLint rustTest scriptsTest protocolCheck`: stored 43 s → reused
  35 s (after the fix round: 1m 40s → 51s); `:sessionLoopbackCheck` and its classpath task pass through
  the reused entry.
- all six Android entry points (`assembleDebug`, `testDebugUnitTest`, `spotlessCheck detekt lintDebug`,
  `:app:releaseHookGuard`, `rustNdkVersion`, `:app:protocolJvmTest`), the Linux inventories and the split
  workflow invocations, all `check-*` task sets, and the Windows PC / MacBook probes of
  `check-native-host.yml` and `check-installer.yml`: store then reuse, no problem.
- Strictness proved by injecting a task that captures a script value: under `problems=fail` the build
  failed with `1 problem was found storing the configuration cache` / `Configuration cache entry
  discarded with 1 problem.` and exit 1 naming `:ccProbe`. The probe was removed afterwards.

At the merge base the same probes found 15 root/Rust task action problems plus plugin-started `git` at
configuration time (`cc-problems-before.md`); the change removes all of them without a CC opt-out except
the documented release/PR installer packaging pipeline.

## Cache experiments

- **Input change:** a cargo-ndk pin bump (`4.1.2` → `4.1.1`, a real published version) changed the keys of
  `rustJni` and `rustWasm` (`toolsFile`), which executed and stored new entries — no stale
  `FROM-CACHE`. Reverting loaded all three products `FROM-CACHE` under their original keys. A comment in
  `rust/rust-toolchain.toml` changed `rustHostJni`'s key the same way.
- **Relocation:** worktree A stored at one path; fresh worktree B at another path loaded
  `rustJni` and `rustHostJni` `FROM-CACHE`, byte-identical to a fresh `--no-build-cache --rerun-tasks`
  build (same `find` lists, same SHA-256). Keys are path-free (sources RELATIVE, tool pins, host
  identity).
- **`rustWasm` is non-cacheable after the relocation fallback:** wasm-pack writes
  `web-extension/rust-wasm/pkg/.gitignore`, which Gradle excludes from cached directory outputs, so a
  restored package was missing a file the fresh build writes. Instead of shipping a stale-output cache,
  commit `190ceaff` made it `doNotCacheIf`: `Caching disabled for task ':rust:rustWasm' because:
  'wasm-pack emits pkg/.gitignore, which Gradle excludes from cached directory outputs'`. The
  path-free proof was repeated for the two remaining products on that commit.
- **Refusal:** with `RUSTC_WRAPPER` or a cargo `config.toml` present, the products print
  `Caching disabled … the inherited environment or a cargo configuration file may change the output`
  and neither load nor store (a control run without `config.toml` stores, so the refusal is the file,
  not the home). Toolchain probes and `installGitCliff` (`downloads a release archive`) never cache.
- **Default off:** with no cache configuration the products report `Build cache is disabled` and store
  nothing (0 entries). A user-level `org.gradle.caching=true` is still overridden by the guarded
  invocation's `--no-build-cache --no-configuration-cache`.
- **Git state:** in a throwaway clone, no tag printed `untagged-144-…` / code `0`; adding
  `android-v9.9.9` invalidated the entry (`GitVersionValueSource` changed) and printed `9.9.9` / `90909`;
  a new commit printed `9.9.9-1-6068dd5-…` / `90909`; an unchanged rerun reused the entry and re-derived
  the current value. A reused entry never reports a stale version.

## Version parity

`android/git-version.gradle.kts` reproduces plugin 0.4.14, which was first read from its bytecode
(`javap`, `version-rules.md`): eight repository states match the plugin exactly — on a tag, commits after
it, dirty tracked file, another branch, detached `HEAD`, two tags on one commit, pre-release tag, no tag
(`PASS` for each, verbose transcript shows equal name and code, e.g. `1.2.3`/`10203`,
`1.2.4`/`10204`, `untagged-4-<7 hex>`/`0`). On the real repository an ordinary clone prints plugin
`0.1.0-37-912d515-chore_gradle-cache-safe` / `100`, and the script pointed at the same clone prints the
identical pair; the worktree's own value adds `-dirty` because the change is uncommitted. The plugin's
`unknown` / `0` in linked worktrees is a pre-existing JGit limitation, not a rule; the script's
`GitVersionValueSource` handles the worktree correctly. The plugin dependency is removed.

## APK and `rustPackage` checksums

Built from throwaway clones at the merge base and at the change, both detached at the recorded commit
with the local tag `android-v9.9.9` (never pushed), `--no-build-cache`.

| Artifact | After | Baseline | Result |
| --- | --- | --- | --- |
| `app-release-unsigned.apk` | `b2460b493d830893d3cb812280aa43a5586206a0cfcb94f24aaa7a870cf50333` (5,042,207 B) | same | **equal** |
| `build/rust-package/SHA256SUMS` | `67267b1cc392e80d5a67b89566ecfdaecb9d564644c36db391296a2c65f475c1` | same | **equal** |
| `app-debug.apk` | `9df61e182c0284236d74b002748958c9226efccd7f94705eacc201c5b5a60923` (69,531,578 B) | `9bc86264…` (same size) | differs, explained |

The debug APK's version fields are equal (`aapt2 dump badging`: `versionCode='90909'
versionName='9.9.9'`). The hash difference is not caused by the change: two fresh merge-base clones
rebuild the byte-identical `9df61e18…` under both JDKs available here (Zulu 21.0.12.1 and Temurin
21.0.12+8), from three different clone paths. The remaining input is the local debug signing key:
`~/.android/debug.keystore` was regenerated at 17:37 on 2026-11-20, four hours after the baseline record
(13:32); the APK is signed v2-only, so only the signing block can change at an identical size. The
recorded baseline debug APK no longer exists on this machine, so a literal entry-level diff was not
possible — stated as such in `evidence/apk.md`.

## Device, Chrome and end-to-end runs

- **Instrumentation (handset `864a3db0`, OnePlus NE2215, API 35):** the recorded Task 5 run was
  `82/77 completed. (5 skipped) (0 failed)`, `BUILD SUCCESSFUL`, CC entry stored with no problem; two
  `PairingInstrumentedTest` cases drove the Rust JNI through `VelesCrypto.pairingCommand`; the five skips
  are opt-in cases whose arguments were not passed, not BLE-only tests. **Gate re-run on 2026-11-21 did
  not reproduce a fully green suite on either attached phone** (the change modifies no
  `android/app/src/**` file): on `864a3db0`, both runs finished 83 tests with 6 skipped and 1 failed —
  `UserNotifierOtpMessageHandlerTest.testNotificationCreation` fails before its body with
  `SecurityException: grantRuntimePermission: Neither user 2000 nor current process has
  android.permission.GRANT_RUNTIME_PERMISSIONS` (it reproduces with the class alone). On `2be02e41`
  (OnePlus 6), 82–83 tests finished with 3 failed — three `ExportImportFlowTest` 5 s `waitUntil` timeouts,
  a different set of three each full run, although the same class passes 8/8 in isolation; disabling
  animations (as CI does) did not change it. Both are device/test-environment failures, not
  build-configuration regressions. The count also differs from the Task 5 record (it starts 77 tests but
  finishes 82 there and 83 here, adding one skip: `SharingLifecycleTest.visibleStartAdvertisesAndExplicitStopCleansUp`),
  so the device's runtime state has changed since Task 5; the exact cause of the permission failure is not
  established. The CI emulator job remains the gate for this suite.
- **Handset install/launch (`2be02e41`):** fixture-signed debug APK installed and launched
  `PermissionsActivity`. `dumpsys package` reported `versionCode=100`,
  `versionName=0.1.0-53-792a082-chore_gradle-cache-safe`, equal to `:app:androidGitVersion` at that
  commit. The device-sharing card was toggled to "Available over Bluetooth. OTP sharing is not enabled."
  and stopped again. The app has no pairing-code screen (`PairingWindow` has no Compose consumer), so
  there is no in-app code to tap; JNI coverage comes from the instrumentation suite above.
- **Chrome (MacBook, Chrome 157):** the extension was built, loaded unpacked via CDP
  (`Extensions.loadUnpacked`), popup renders "Veles OTP" with a clean console, connector page shows
  "Native bridge not installed" (expected, no host registered on that Mac), and the WASM glue initialized
  under the extension CSP with `reverse_bytes([1,2,3,4])` → `[4,3,2,1]`. Limit: this exercised the
  generated glue and the same wasm bytes, not Vite's bundled copy inside `dist/chunks/connector-*.js`
  (covered indirectly by the HTTP 200 fetch and clean console); `pairing_command` was not called because
  no native host was registered. Deviation: the build ran on Node 20.10.0, outside the declared
  `>=22` engine range (the Mac only had Node 18/20).
- **End-to-end pairing (`10.5`) is unavailable, not tested:** the handset is attached to the home PC
  while the extension runs on the Mac with no Native Messaging host registered; the pairing transport is
  the phone's Bluetooth advertising service, so the phone must be in Bluetooth range of the browser's
  machine. The two machines are not one pairing domain and the app exposes no code-entry UI. Recorded as
  environment-unavailable; the user was asked and chose this option.
- **Emulator:** only a `--dry-run` probe was made (Task 1); no emulator APK run exists.

## Cacheability

Every script-registered task carries an explicit contract (`evidence/cacheability.md`):

- **Cacheable:** only `rustJni` and `rustHostJni` (`CargoBuildTask`, the repository's only
  `@CacheableTask`), with every result-affecting input declared, path-free keys, and refusal on ambient
  cargo/rustc overrides.
- **Non-cacheable with a stated reason:** `rustWasm` (`doNotCacheIf`, the `.gitignore` fallback above),
  every other custom task via `@DisableCachingByDefault(because = …)` or
  `doNotCacheIf(<reason>)`, and the `Sync`/`Delete` types. `protocolJvmTest` is explicitly excluded
  because the spec makes every non-Rust script task non-cacheable, and it now declares
  `build/rust/host-jni` as a `hostJniLibrary` input (RELATIVE sensitivity) so a changed native library
  changes its key; `testDebugUnitTest` declares the same input, so a Rust-only change can no longer
  serve a cached or UP-TO-DATE JVM test pass.
- Planning-text note: `proposal.md`, `design.md`, `plan.md` and the spec's requirement line still name
  `rustWasm` as cacheable, and the spec's own fallback clause (the "Restored WASM package is complete"
  scenario) is what governs the non-cacheable outcome.
- **Plugin tasks** (AGP, Kotlin, Java, Spotless, `base`) keep the cacheability their plugins declare.
  `check` is a lifecycle task with no action or outputs.
- **Guarded graphs:** every release/verification Gradle invocation passes
  `--no-build-cache --no-configuration-cache`; the installer packaging pipeline declares itself
  incompatible with the configuration cache with a reason and is never cached.

## What did not run, and why

- **Windows and macOS native jobs** (host compile/tests, installer `signtool`/NSIS/Swift/Xcode,
  `codesign`, notarization, the physical installer matrix): these run only on their runners. The
  configuration-cache probes for the four `check-native-host.yml`/`check-installer.yml` task sets were
  run on the home Windows PC and the MacBook; the CI jobs themselves still have to pass after the push.
- **Bridge-scaffold packaging (`bridgeBuild`, `bridgePackage`):** NOT RUNNABLE on Linux — both fail on
  `Package 'glib-2.0' not found` (Tauri's GTK/glib development libraries are absent), so neither result is
  a pass. `bridgeManifests` passed (it wrote the Windows and macOS manifests). `check-bridge-scaffold.yml`
  does not run the packaging step, so CI is unaffected.
- **Installer unsigned/fixture producers** (`bridgeInstallerUnsigned`, `bridgeInstallerPackageFixture`):
  they refuse Linux by design (`OTP-25 installer inputs are built on a Windows or macOS runner`).
- **Real macOS Bluetooth E2E:** requires the signed canonical test bundle on macOS; not run here.
- **Emulator APK run and end-to-end pairing `10.5`:** unavailable as described above.
- `verify/aggregate-checksums.sh` (optional local checksum manifest) was not run.

## Local gates

All run in the worktree on Linux; exact transcripts, per-node results and failure output are in
`evidence/gates.md`.

- `./gradlew scriptsTest` — pass (888 Python tests).
- `node --test verify/test/workflow-contracts.test.mjs` — pass (61 tests).
- `verify/verify-supply-chain.sh` — exit 0 (with the pinned Node 26.8.1 / npm 11.19.0; `npm ci
  --ignore-scripts --prefix verify` and `./gradlew installCargoDeny` provisioned first).
- `openspec validate gradle-cache-safe` — valid (exit 0; long-requirement warnings only).
- Per-node checks that ran and passed: tooling (11 + 148 node tests, `components.py check`, commitlint,
  `project_flow.py --help`, the root-alias check and its self-test); docs (the four checks, 300 unit
  tests, site check); supply-chain (117 unit tests); rust (`rustInstall`, `rustFormat rustLint rustTest`,
  `rustJni`, `rustWasm`, `rustHostJni`, `verify-apk-jni.sh`); protocol (`protocolCheck`); native-host
  (`bridgeHost*` tasks, `bridgeHostBuild`, 26 contract tests); bridge-scaffold
  (`bridgeInstall bridgeFormat bridgeLint bridgeTypecheck bridgeNpmTest bridgeRust*`, `bridgeManifests`);
  installer (`bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest bridgeInstallerScriptsTest`,
  65 Python contract tests, `bridgeInstallerSupplyChain` — `BUILD SUCCESSFUL in 2m 49s`); web-extension
  (format, lint, typecheck, 691 tests, build, 8 bundle tests, package); and
  Android (`assembleDebug assembleRelease testDebugUnitTest spotlessCheck detekt lintDebug
  :app:releaseHookGuard`, plus the three CI guard greps).
- **Not a pass:** `connectedDebugAndroidTest` did not reach a green full suite on either attached phone
  (device/test-environment reasons above; the failing tests are outside the files this change touches).
  `bridgeBuild` and `bridgePackage` are not runnable on Linux (`Package 'glib-2.0' not found`), and the
  installer producers refuse Linux; they are listed as not run in "What did not run, and why" and are
  never counted as passes.
- The first `native-bridge/host` contract run failed because its static checker still parsed the old
  inline `Exec` block; the checker was updated to read the typed `HostBuildTask` class (same asserted
  properties, negative controls intact, plus a negative control that removes `--locked` from the class
  body) and the suite then passed (26 tests).

Refs raidenyn/veles-android#181
