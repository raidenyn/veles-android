# Gate transcripts

Task 11.1/11.2 evidence: every command was run in
`/home/dev/.t3/worktrees/veles-android/t3-699e4a45-1711ce8ea1e8baf4` (branch `chore/gradle-cache-safe`,
HEAD `b4f5eb3c` at the start) on 2026-11-21, Linux, JDK Azul Zulu 21.0.12.1
(`JAVA_HOME=/home/dev/.jdk/zulu21`), `ANDROID_SDK_ROOT=ANDROID_HOME=/home/dev/.android-sdk`, Gradle
9.5.0 through the wrapper.

Node on the host `PATH` is v26.13.1, but `verify/verify-supply-chain.sh` pins Node 26.8.1 / npm 11.19.0.
The exact pinned Node was unpacked to `/tmp/opencode/node/node-v26.8.1-linux-x64` and prepended to `PATH`
for those runs:

```text
$ node --version        # /tmp/opencode/node/node-v26.8.1-linux-x64/bin/node
v26.8.1
$ npm --version
11.19.0
```

The repository's documented provisioning was run first because `verify/node_modules` was absent:

```text
$ npm ci --ignore-scripts --prefix verify
added 278 packages, and audited 279 packages in 4s
$ ./gradlew installCargoDeny
BUILD SUCCESSFUL in 3m 46s
2 actionable tasks: 2 executed
```

## The four named gates

### `./gradlew scriptsTest`

```text
...
Ran 888 tests in 30.896s

OK
release_assets: installer carried assets verified
release_assets: draft assets match the recorded digests
release_ready: ready

BUILD SUCCESSFUL in 44s
2 actionable tasks: 2 executed
Configuration cache entry stored.
```

Exit 0.

### `node --test verify/test/workflow-contracts.test.mjs`

```text
ℹ tests 61
ℹ suites 0
ℹ pass 61
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
```

Exit 0. The verifier's full unit suite also passed: `node --test verify/test/*.test.mjs` → `tests 117`,
`pass 116`, `skipped 1`, `fail 0`, exit 0.

### `verify/verify-supply-chain.sh`

Exit 0, run twice. With the pinned Node on `PATH` the script generated six SBOMs and six policy reports:

```text
$ ls build/sbom
native-bridge.cdx.json  native-host.cdx.json  native-installer.cdx.json
protocol.cdx.json       rust.cdx.json         web-extension.cdx.json

$ ls build/verification/supply-chain
cargo-build-scripts.txt  cargo-licenses.txt  npm-install-scripts.txt
npm-licenses.txt         remote-code.txt     vendored-patches.txt
```

The individual checkers print nothing on success; the script exited 0. `./gradlew
bridgeInstallerSupplyChain` (the same script under the installer node's task) also passed:
`BUILD SUCCESSFUL in 2m 49s`.

### `openspec validate gradle-cache-safe`

```text
Change 'gradle-cache-safe' is valid
⚠ [WARNING] component-versioning/spec.md: ADDED "Android version equals the previous plugin": Requirement text is very long (>500 characters). ...
⚠ [WARNING] gradle-build-caching/spec.md: ADDED "Common entry points reuse the configuration cache": ...
⚠ [WARNING] gradle-build-caching/spec.md: ADDED "Configuration-time reads go through providers": ...
⚠ [WARNING] gradle-build-caching/spec.md: ADDED "Only the Rust products are cacheable, with a relocatable key": ...
⚠ [WARNING] gradle-build-caching/spec.md: ADDED "Ambient toolchain overrides refuse caching": ...
⚠ [WARNING] gradle-build-caching/spec.md: ADDED "Every other custom task is explicitly non-cacheable": ...
⚠ [WARNING] gradle-build-caching/spec.md: ADDED "Release and verification builds bypass both caches": ...
```

Exit 0; warnings only.

## Per-node checks

`python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` selected all ten
nodes: `android web-extension installer rust protocol native-host bridge-scaffold supply-chain tooling
docs` (base `597ee2c691f2d4c2dec1c4450072eca86a76054e`).

| Node | Command | Result |
| --- | --- | --- |
| tooling | `node --test scripts/check-mac-comparisons.test.mjs` | 11 tests, 11 pass |
| tooling | `node --test scripts/opencode-hooked-rules/index.test.mjs` | 148 tests, 148 pass |
| tooling | `python3 -I scripts/components.py check` | exit 0 |
| tooling | `python3 -I scripts/commitlint.py --message "feat(app): add thing"` | exit 0 |
| tooling | `python3 -I scripts/project_flow.py --help` | exit 0 |
| tooling | `sh verify/check-root-aliases.sh` | `PASS: every root alias resolves with --dry-run (assembleDebug assembleRelease testDebugUnitTest lintDebug spotlessCheck detekt connectedDebugAndroidTest protocolCheck)` |
| tooling | `sh verify/check-root-aliases.sh --self-test` | `PASS: an alias to a missing task exits non-zero` |
| docs | `python3 docs/checks/check_offline.py --root .` | exit 0 |
| docs | `python3 docs/checks/check_agent_files.py --root .` | exit 0 |
| docs | `python3 docs/checks/check_gate_b_matrix.py` | exit 0 (validator pass is not Gate B acceptance) |
| docs | `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v` | `Ran 300 tests … OK` |
| docs | `python3 docs/checks/check_site.py --root .` | exit 0 |
| supply-chain | `node --test verify/test/*.test.mjs` | 117 tests, 116 pass, 1 skipped |
| rust | `./gradlew rustInstall rustFormat rustLint rustTest rustJni rustWasm rustHostJni` | pass in the root batch |
| rust | `rust/scripts/verify-apk-jni.sh android/app/build/outputs/apk/debug/app-debug.apk` | exit 0 |
| protocol | `./gradlew protocolCheck` | pass (Node contract, Rust consumer, both TS consumers, `:app:protocolJvmTest`, loopback; loopback vitest 4 files / 11 tests) |
| native-host | `./gradlew bridgeHostToolchainCheck bridgeHostFormat bridgeHostLint bridgeHostTest` | pass |
| native-host | `./gradlew bridgeHostBuild` | pass (also driven three times by the contract tests) |
| native-host | `python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py' -v` | final: `Ran 26 tests … OK` (first run failed; see below) |
| bridge-scaffold | `./gradlew bridgeInstall bridgeFormat bridgeLint bridgeTypecheck bridgeNpmTest bridgeRustFormat bridgeRustLint bridgeRustTest bridgeTest` | pass |
| bridge-scaffold | `./gradlew bridgeManifests --no-build-cache --no-configuration-cache` | `BUILD SUCCESSFUL`, wrote `build/native-bridge/manifests/{windows,macos}/app.veles.native_bridge.json` |
| installer | `./gradlew bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest bridgeInstallerScriptsTest` | pass |
| installer | `python3 -I native-bridge/installer/scripts/test_build_contract.py` | `Ran 65 tests … OK` |
| installer | `./gradlew bridgeInstallerSupplyChain` | `BUILD SUCCESSFUL in 2m 49s` |
| web-extension | `npm run format:check` / `lint` / `typecheck` | pass |
| web-extension | `npm test` | 55 files + 1 skipped; `691 passed | 2 skipped` |
| web-extension | `npm run build` then `npm run test:bundle` | build OK; 8 bundle tests pass |
| web-extension | `npm run package` | zip + sidecar + `SHA256SUMS` in `build/web-extension/` |
| web-extension | `node scripts/check-mac-comparisons.mjs --scan web-extension/src` | exit 0 |
| web-extension | pairing-code debug-surface grep from `check-web-extension.yml` | exit 0 |
| android | `./gradlew assembleDebug assembleRelease testDebugUnitTest spotlessCheck detekt lintDebug :app:releaseHookGuard` | `BUILD SUCCESSFUL in 5m 17s`, 134 tasks: 74 executed, 60 up-to-date; `releaseHookGuard: app-release-unsigned.apk contain no debug hook classes, receivers or probe strings` |
| android | `node scripts/check-mac-comparisons.mjs --scan android/app/src/main` | exit 0 |
| android | raw `android.util.Log` guard (`.agents/rules/android-lint-before-push.md`) | exit 0 |
| android | pairing-code debug-surface grep from `check-android.yml` | exit 0 |

The root batch above was:

```text
$ ./gradlew --console=plain rustFormat rustLint rustTest rustJni rustWasm rustHostJni rustInstall \
    protocolCheck scriptsTest bridgeInstall bridgeFormat bridgeLint bridgeTypecheck bridgeNpmTest \
    bridgeRustFormat bridgeRustLint bridgeRustTest bridgeTest bridgeHostToolchainCheck bridgeHostFormat \
    bridgeHostLint bridgeHostTest bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest \
    bridgeInstallerScriptsTest
BUILD SUCCESSFUL in 7m 52s
99 actionable tasks: 77 executed, 22 up-to-date
```

### First-run contract-test failure (fixed by `69973ddc`)

`bridgeHostBuild` became `HostBuildTask`, so its cargo command line and staging rules moved from the
`Exec` registration into the class body. The static checker still parsed only the registration:

```text
FAIL: test_dedicated_task_graph_is_locked_pinned_and_under_build
AssertionError: Lists differ: ['bridgeHostBuild does not pass --locked',[336 chars]ble'] != []
- ['bridgeHostBuild does not pass --locked',
-  'bridgeHostBuild does not redirect CARGO_TARGET_DIR',
-  'bridgeHostBuild missing "build"',
-  'bridgeHostBuild missing "--release"',
-  'bridgeHostBuild missing "--bin"',
-  'bridgeHostBuild missing "veles-native-host"',
-  'bridgeHostBuild does not keep the staged executable runnable',
-  'bridgeHostBuild does not reject a missing production executable']

FAIL: test_test_driver_in_production_build_is_rejected
ERROR: test_gradle_declares_the_environment_input
IndexError: list index out of range    # split on `fun Exec.productionHostEnvironment()`

FAILED (failures=2, errors=1)
```

The implementation already satisfies every contract property: `commandLine("cargo", "build", "--locked",
"--release", "--bin", "veles-native-host")`, `CARGO_TARGET_DIR` from `cargoTargetDir`,
`setExecutable(true, false)`, `SHA256SUMS`, and `check(built.isFile) { "bridgeHostBuild: production
executable missing at $built" }`. Commit `69973ddc test(native-host): read the typed host build task in
the contract test` made the checker read the instantiated class (`task_source`), assert the typed
`allowedOrigins`/`windows` inputs, and accept `productionHostEnvironment(isWindows)`; it also adds a
negative control that removes `--locked` from the `HostBuildTask` class body. The original negative
controls still fail as intended. Final run:

```text
Ran 26 tests in 30.621s

OK
```

## Not runnable in this container

### `bridgeBuild` and `bridgePackage` (Tauri scaffold packaging)

`bridgeBuild` exits 1 because the container has no GTK/glib development libraries:

```text
$ ./gradlew --console=plain bridgeBuild --no-build-cache --no-configuration-cache
> Task :bridgeBuild FAILED
...
error: failed to run custom build command for `glib-sys v0.18.1`
  pkg-config exited with status code 1
  > PKG_CONFIG_ALLOW_SYSTEM_CFLAGS=1 pkg-config --libs --cflags glib-2.0 'glib-2.0 >= 2.70'
  Package glib-2.0 was not found in the pkg-config search path.
  ...
  The system library `glib-2.0` required by crate `glib-sys` was not found.
Error failed to build app: failed to build app
> Process 'command 'npm'' finished with non-zero exit value 1
BUILD FAILED in 33s
```

`bridgePackage` fails in its `bridgeBuild` dependency for the same reason (it never reaches its own
Linux refusal). Both are Windows/macOS packaging steps; `check-bridge-scaffold.yml` runs only
`bridgeFormat bridgeLint bridgeTypecheck bridgeRustFormat bridgeRustLint`, `bridgeNpmTest bridgeRustTest`
and `bridgeHost*` — not `bridgeBuild`/`bridgePackage` — so CI is unaffected. `bridgeManifests` passed.

### Installer producers

```text
$ ./gradlew --console=plain bridgeInstallerPackageFixture
* What went wrong:
Execution failed for task ':bridgeInstallerToolObservation' (registered in build file 'build.gradle.kts').
> OTP-25 installer inputs are built on a Windows or macOS runner; Linux is not a producer target.

BUILD FAILED in 2s
Configuration cache entry discarded with 37 problems.
```

`bridgeInstallerUnsigned` is behind the same platform guard. Windows NSIS/`signtool`, macOS
Swift/Xcode/`codesign`/notarization and the physical matrix do not run on Linux.

## `connectedDebugAndroidTest` — not green on either attached phone

The change modifies no `android/app/src/**` file (`git diff --name-only <merge-base>..HEAD --
android/app/src` prints nothing); the test code, manifest and permissions are unchanged.

### `864a3db0` (OnePlus NE2215, API 35)

Both full runs: `Starting 77 tests` → `Finished 83 tests`, 6 skipped, **1 failed**:

```text
me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandlerTest > testNotificationCreation[NE2215 - 15] FAILED
	java.lang.SecurityException: Error granting runtime permission
	at android.app.UiAutomation.grantRuntimePermissionAsUser(UiAutomation.java:1574)
...
NE2215 - 15 Tests 83/77 completed. (6 skipped) (1 failed)
Finished 83 tests on NE2215 - 15

> Task :app:connectedDebugAndroidTest FAILED
BUILD FAILED in 7m 38s
```

The class alone reproduces it, with the underlying cause:

```text
$ ./gradlew ... connectedDebugAndroidTest \
    -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandlerTest
Finished 1 tests on NE2215 - 15
java.lang.SecurityException: Error granting runtime permission
Caused by: java.lang.SecurityException: grantRuntimePermission: Neither user 2000 nor current
process has android.permission.GRANT_RUNTIME_PERMISSIONS.
BUILD FAILED in 5m 15s
```

The `GrantPermissionRule` fails before the test body. `ExportImportFlowTest` alone on this phone passes
8/8 (`BUILD SUCCESSFUL in 5m 10s`).

### `2be02e41` (OnePlus A6013, API 35)

Run 1, animations on: `Finished 82 tests`, 5 skipped, **3 failed** — `ExportImportFlowTest.cancel import
stops everything and writes nothing`, `… import review dialog lists new and overwrite names`,
`… rejected import list scrolls to reveal the last invalid entry`:

```text
androidx.compose.ui.test.ComposeTimeoutException: Condition still not satisfied after 5000 ms
	at androidx.compose.ui.test.AndroidComposeUiTestImpl.waitUntil(ComposeUiTest.android.kt:441)
Finished 82 tests on ONEPLUS A6013 - 15
BUILD FAILED in 15m 18s
```

Run 2, animations disabled (the CI setting; restored to 1 afterwards): `Finished 83 tests`, 6 skipped,
**3 failed**, a different set — `… import of malformed file shows message and no review dialog`,
`… import review dialog lists new and overwrite names`, `… invalid import shows every invalid entry and no
import action`. `ExportImportFlowTest` alone on this phone passes 8/8 (`BUILD SUCCESSFUL in 4m 59s`).

### Count reconciliation with the recorded Task 5 run

Task 5 recorded `Starting 77 tests` → `82/77 completed. (5 skipped) (0 failed)` on `864a3db0`. The gate
runs also start 77 tests but finish 83 with 6 skipped: the extra skip is
`SharingLifecycleTest.visibleStartAdvertisesAndExplicitStopCleansUp`, which did not skip in Task 5. The
device's runtime state (Bluetooth/permission/app data) has changed since Task 5; and the permission grant
that worked then now fails. The exact cause is not established — it is a device-state change, not a code
or build-configuration difference.

## Clean contract and constraints

```text
$ ./gradlew clean
BUILD SUCCESSFUL in 8s
3 actionable tasks: 3 executed
$ rust/scripts/assert-clean.sh
# exit 0
```

`git tag -l 'android-v*'` → `android-v0.1.0` only (no `android-v9*`). No `org.gradle.caching=true` is
committed; no signing key is committed.
