# Configuration-cache reuse — root build

Task 4 steps 6–7 evidence, plus the fix-round runs of the tasks the first round never executed. Host:
Linux container, Gradle 9.5.0 through the repository wrapper, JDK 21 (Temurin 21.0.12 for the first
round, Zulu 21.0.12 after the container restarted), `ANDROID_SDK_ROOT` from the environment,
2026-11-20. `gradle.properties` carries, committed:

```properties
org.gradle.configuration-cache=true
org.gradle.configuration-cache.problems=fail
```

## Step 6 — the root gate stores once, then reuses

Same command twice, `--console=plain`, nothing else on the command line:

```text
$ ./gradlew --console=plain rustFormat rustLint rustTest scriptsTest protocolCheck
Calculating task graph as no cached configuration is available for tasks: rustFormat rustLint rustTest scriptsTest protocolCheck
...
BUILD SUCCESSFUL in 43s
81 actionable tasks: 29 executed, 52 up-to-date
Configuration cache entry stored.

$ ./gradlew --console=plain rustFormat rustLint rustTest scriptsTest protocolCheck
Reusing configuration cache.
...
BUILD SUCCESSFUL in 35s
81 actionable tasks: 28 executed, 53 up-to-date
Configuration cache entry reused.
```

Neither run printed a `Configuration cache problems found` block, and the second run reached every task
through the reused entry (`> Task :rustFormat`, `> Task :rustLint`, `> Task :rustTest`,
`> Task :scriptsTest`, `> Task :protocolCheck`). `protocolCheck` still traverses the loopback path:

```text
> Task :android:app:sessionLoopbackClasspath
> Task :android:app:protocolJvmTest UP-TO-DATE
> Task :sessionLoopbackCheck
> Task :protocolCheck
```

The classpath file the root loopback reads is written by the run
(`android/app/build/session-loopback/classpath.txt`, 38472 bytes); on the second run the producing task is
`UP-TO-DATE`, so the reuse is real and not a reconfiguration.

Re-run after the fix round (toolchain command names, messages, probe order), same pair:

```text
$ ./gradlew --no-daemon --console=plain rustFormat rustLint rustTest scriptsTest protocolCheck
BUILD SUCCESSFUL in 1m 40s
Configuration cache entry stored.

$ ./gradlew --no-daemon --console=plain rustFormat rustLint rustTest scriptsTest protocolCheck
Reusing configuration cache.
BUILD SUCCESSFUL in 51s
Configuration cache entry reused.
```

### Why this gate needed one Android task first

The first attempt failed with exactly one problem: `protocolCheck` → `sessionLoopbackCheck` →
`:android:app:sessionLoopbackClasspath`, which was an ad-hoc `DefaultTask` closing over the
`testDebugUnitTest` (`AndroidUnitTest`) task.

```text
FAILURE: Build failed with an exception.
* What went wrong:
Configuration cache problems found in this build.

1 problem was found storing the configuration cache.
- Task `:android:app:sessionLoopbackClasspath` of type `org.gradle.api.DefaultTask`: cannot serialize object of type 'com.android.build.gradle.tasks.factory.AndroidUnitTest', a subtype of 'org.gradle.api.Task', as these are not supported with the configuration cache.
...
Configuration cache entry discarded with 1 problem.
```

That task is Task 5 Step 1's item; it was ported early (`SessionLoopbackClasspathTask`, classpath as an
`@InputFiles` file collection, file written in the action) because the root gate cannot pass without it.
Every other root task in the gate was already clean.

## Fix round — the Step 3 task sets

Every command below ran twice from the repository root with the configuration cache on; each pair ends
with the same two lines (store, then reuse) and no problem. `grep -c 'Configuration cache problems'` was
`0` in every log.

| Command | Run 1 | Run 2 |
| --- | --- | --- |
| `check` | `BUILD SUCCESSFUL in 2m 55s` · `Configuration cache entry stored.` | `BUILD SUCCESSFUL in 2m 31s` · `Reusing configuration cache.` · `Configuration cache entry reused.` |
| `bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest` | `BUILD SUCCESSFUL in 1m 9s` · stored | `BUILD SUCCESSFUL in 50s` · reused |
| `bridgeHostBuild` | `BUILD SUCCESSFUL in 30s` · stored | `BUILD SUCCESSFUL in 17s` · reused |
| `verifyCargoDeny verifyCargoCyclonedx` | `BUILD SUCCESSFUL in 23s` · stored | `BUILD SUCCESSFUL in 13s` · reused |
| `bridgeNodeCheck` | `BUILD SUCCESSFUL in 16s` · reused | `BUILD SUCCESSFUL in 13s` · reused |
| `bridgeInstallerToolObservation` | `BUILD FAILED in 24s` · stored | `BUILD FAILED in 9s` · reused |
| `bridgeInstallerToolCheck` | `BUILD FAILED in 23s` · stored | `BUILD FAILED in 10s` · reused |

The two installer tool tasks are Windows/macOS-only: their action refuses Linux with
`OTP-25 installer inputs are built on a Windows or macOS runner; Linux is not a producer target.` after
the entry is stored, so the execution failure is the task's own guard, not the configuration cache.

`check` ran with `GRADLE_USER_HOME` pointing at an isolated home whose `caches`, `wrapper` and `native`
directories symlink the shared ones. Another session on this machine repeatedly issued `gradlew --stop`,
which killed three attempts mid-build (`Gradle build daemon has been stopped: stop command received`);
the isolated home only protects the daemon and changes none of the build's inputs.

## Step 7 — the strict gate fails an unsafe task

Temporarily appended to `build.gradle.kts` (`windowsExecutableSuffix` is a script-level `val`, so the
action captures the script):

```kotlin
tasks.register("ccProbe") {
    doLast { println(windowsExecutableSuffix) }
}
```

```text
$ ./gradlew --console=plain ccProbe
Calculating task graph as no cached configuration is available for tasks: ccProbe

> Task :ccProbe

[Incubating] Problems report is available at: <worktree>/build/reports/problems/problems-report.html

FAILURE: Build failed with an exception.

* What went wrong:
Configuration cache problems found in this build.

1 problem was found storing the configuration cache.
- Task `:ccProbe` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
  See https://docs.gradle.org/9.5.0/userguide/configuration_cache_requirements.html#config_cache:requirements:disallowed_types

See the complete report at <worktree>/build/reports/configuration-cache/<entry>/configuration-cache-report.html
...
BUILD FAILED in 8s
1 actionable task: 1 executed
Configuration cache entry discarded with 1 problem.
```

Exit status 1: `org.gradle.configuration-cache.problems=fail` turns the single problem into a failure
naming `:ccProbe`, exactly as intended. The task was then removed from `build.gradle.kts` (`grep -c ccProbe
build.gradle.kts` → `0`) and the build is clean again:

```text
$ ./gradlew --console=plain help
BUILD SUCCESSFUL in 1s
1 actionable task: 1 executed
Configuration cache entry stored.

$ ./gradlew --console=plain help
BUILD SUCCESSFUL in 1s
1 actionable task: 1 executed
Configuration cache entry reused.
```

## Host-toolchain probe messages (fix round)

The npm messages had to stay byte-identical, including `requireNpm`'s trailing period. Run against a
`PATH` without npm, and with a stand-in `node` reporting `v18.2.0`:

```text
$ PATH=<no npm> ./gradlew protocolNodeCheck
> Could not find 'npm' on PATH. Install Node.js >=22.0.0 to run the OTP-02 contract checks..

$ PATH=<no npm> ./gradlew bridgeNodeCheck
> Could not find 'npm' on PATH. Install Node.js >=22.0.0 (declared in native-bridge/package.json engines.node).

$ PATH=<npm present, node v18.2.0, no python3> ./gradlew protocolNodeCheck
> Could not find 'python3' on PATH. Install Python 3.8 or newer. Version hint: Python 3.8+.

$ PATH=<npm present, node v18.2.0, python3 present> ./gradlew protocolNodeCheck
> Node >=22.0.0 is required; found 'v18.2.0'
```

The third run is the order proof: with an old Node present, a missing python3 is still reported first,
exactly as the former `doLast` did (npm, then python3, then the Node and Python versions).

## Installer build contract

`python3 -I native-bridge/installer/scripts/test_build_contract.py` after the port:

```text
.................................................................
----------------------------------------------------------------------
Ran 65 tests in 9.254s

OK
```

## Task 5 — the Android build (design D1, D2, D5)

Every command below ran from `android/` with `--console=plain`, nothing else on the command line, so
the committed `org.gradle.configuration-cache=true` / `org.gradle.configuration-cache.problems=fail`
applied. Host: Linux container, Gradle 9.5.0 through the repository wrapper, Zulu 21.0.12
(`JAVA_HOME=/home/dev/.jdk/zulu21`), `ANDROID_SDK_ROOT=ANDROID_HOME=/home/dev/.android-sdk`,
`GRADLE_USER_HOME=/tmp/opencode/gradle-home-t5` (another session repeatedly issues `gradlew --stop`;
the isolated home changes no build input). Logs: `/tmp/opencode/t5logs/`. `grep -c
'Configuration cache problems'` is `0` in every log quoted here.

| # | Command | Run 1 | Run 2 |
| --- | --- | --- | --- |
| 1 | `assembleDebug` | `BUILD SUCCESSFUL in 5m 27s` · `51 actionable tasks: 31 executed, 20 up-to-date` · `Configuration cache entry stored.` | `Reusing configuration cache.` · `BUILD SUCCESSFUL in 10s` · `Configuration cache entry reused.` |
| 2 | `testDebugUnitTest` | `BUILD SUCCESSFUL in 2m 48s` · `47 actionable tasks: 12 executed, 35 up-to-date` · `Configuration cache entry stored.` | `Reusing configuration cache.` · `BUILD SUCCESSFUL in 6s` · `Configuration cache entry reused.` |
| 3 | `spotlessCheck detekt lintDebug` | `BUILD SUCCESSFUL in 22s` · `41 actionable tasks: 2 executed, 39 up-to-date` · `Configuration cache entry stored.` | `Reusing configuration cache.` · `BUILD SUCCESSFUL in 7s` · `Configuration cache entry reused.` |
| 4 | `:app:releaseHookGuard` | `BUILD SUCCESSFUL in 7m 2s` · `61 actionable tasks: 57 executed, 4 up-to-date` · `Configuration cache entry stored.` | `Reusing configuration cache.` · `BUILD SUCCESSFUL in 7s` · `Configuration cache entry reused.` |
| 5 | `rustNdkVersion` | `29.0.14206865` · `BUILD SUCCESSFUL in 4s` · `Configuration cache entry stored.` | `Reusing configuration cache.` · `29.0.14206865` · `BUILD SUCCESSFUL in 2s` · `Configuration cache entry reused.` |
| 6 | `:app:protocolJvmTest` | `BUILD SUCCESSFUL in 13s` · `47 actionable tasks: 3 executed, 44 up-to-date` · `Configuration cache entry stored.` | `Reusing configuration cache.` · `BUILD SUCCESSFUL in 2s` · `Configuration cache entry reused.` |

Run 1 of gate 3 was itself a store after the last script edit:

```text
Calculating task graph as configuration cache cannot be reused because file 'build.gradle.kts' has changed.
...
BUILD SUCCESSFUL in 22s
41 actionable tasks: 2 executed, 39 up-to-date
Configuration cache entry stored.
```

The second-run headers are literal, one line each, in all six logs:

```text
Reusing configuration cache.
```

No log contains `Configuration cache problems found`, `problems were found` or `discarded with`; with
`org.gradle.configuration-cache.problems=fail` a single problem would have failed the build. Gate 4
executed `:app:releaseHookGuard` (it reads the minified release APK; it declares no outputs, so it
executes on both runs), gate 6 executed `:app:protocolJvmTest`, gate 3 executed `:app:detekt`,
`:app:spotlessCheck` and `:app:lintDebug`, and gates 1, 2 and 5 executed their own tasks.

### Device run — `connectedDebugAndroidTest`

One attached device, `ANDROID_SERIAL=864a3db0` (OnePlus `NE2215`, API 35):

```text
$ cd android && ./gradlew --console=plain \
    --init-script config/android_keys/fixture-signing.gradle connectedDebugAndroidTest
...
Starting 77 tests on NE2215 - 15
...
NE2215 - 15 Tests 82/77 completed. (5 skipped) (0 failed)
Finished 82 tests on NE2215 - 15
...
BUILD SUCCESSFUL in 10m 49s
92 actionable tasks: 34 executed, 58 up-to-date
Configuration cache entry stored.
```

`grep -c 'Configuration cache problems'` is `0`; the build stored a fresh entry because the init
script changes the signing configuration, and no problem block was printed. Zero tests failed. The five
`SKIPPED` tests are opt-in cases whose instrumentation argument was not passed by this plain run, not
BLE-only tests:

- `crypto.PairingInstrumentedTest.separateConformanceLibraryMatchesEveryFrozenPairingVector` requires
  the `velesConformanceLibrary` argument (the path of the separate conformance `.so`);
- `crypto.PairingInstrumentedTest.productionJniStateExpiresOnNativeMonotonicClock` requires
  `velesRunExpiry=true`;
- `sharing.SharingFeasibilityTest.holdSyntheticPeripheralForWindowsCentral` requires `otp23=true`;
- `sharing.SharingInteropPhysicalTest.holdObservedPeripheral` and
  `sharing.SharingInteropPhysicalTest.cccdBeforeMtuIsRejectedOnRealRadio` both require
  `otp23_a01=true` (the second also `otp23_run_id`).

### Warnings in these runs (all pre-existing)

The runs are green, but they are not warning-free. Every run-1 log (and the device run) ends with:

```text
Deprecated Gradle features were used in this build, making it incompatible with Gradle 10.

You can use '--warning-mode all' to show the individual deprecation warnings and determine if they come from your own scripts or plugins.
```

This change adds no deprecated Gradle API (`--warning-mode all` is not enabled in these logs, so the
summary does not name the source). The sources visible in the tree are pre-existing and untouched by this
change: the five `srcDir(...)` calls at `android/app/build.gradle.kts:120,168-174`, which AGP 9 reports as
deprecated, and the same pattern elsewhere in that file. The commit changes the imports,
`releaseHookGuard` and the `protocolJvmTest` registration, not those lines.

The release gate also prints a pre-existing lint-baseline warning, quoted from
`releaseHookGuard-1.log`:

```text
79 errors/warnings were listed in the baseline file (/home/dev/.t3/worktrees/veles-android/t3-699e4a45-1711ce8ea1e8baf4/android/app/lint-baseline.xml) but not found in the project; perhaps they have been fixed?

Note: The baseline was created using a different target/variant than it was checked against.
Creation variant: lint
Current variant: lintVitalRelease
```

`android/app/lint-baseline.xml` is not touched by this change; the stale entries predate it.

The first attempt (kept in `/tmp/opencode/t5logs/connectedDebugAndroidTest.log`) failed at install with
`INSTALL_FAILED_UPDATE_INCOMPATIBLE: Existing package me.nagaev.veles.debug signatures do not match
newer version` because `android/config/android_keys/` is git-ignored and was absent in this worktree,
so the build signed with the default debug key while the installed app carried the repository fixture
identity. The ignored fixture signing script and keystore were copied from the main
checkout into `android/config/android_keys/` (still ignored; never committed, never printed). No app
was uninstalled, no data cleared and no key was generated; the rerun installed over the existing
package and executed the tests above.

## Fix round — review findings

Commit `fix(android): keep protocolJvmTest out of the build cache`. Two configuration-cache-relevant
changes and one input declaration:

- `protocolJvmTest` gained
  `outputs.doNotCacheIf("loads the Rust host JNI library from build/rust/host-jni, which is not a task input") { true }`.
  It is a `Test`, and its key (classpath, test classes, filter) does not cover the native library the
  task loads through `java.library.path`; with `--build-cache` a changed library with unchanged JVM
  classes would restore `FROM-CACHE` without testing it.
- Android's `rustNdkVersion` forwarder gained
  `outputs.doNotCacheIf("forwards to the included rust build") { true }`, matching the root
  forwarders.
- `ReleaseHookGuardTask.apkDirectory` moved from `@Internal` to
  `@InputDirectory @PathSensitive(PathSensitivity.RELATIVE)`; the `dependsOn(":app:assembleRelease")`
  stays.

Re-ran the affected pair sets from `android/` with the configuration cache on (logs `fix-*.log`);
`grep -c 'Configuration cache problems'` is `0` in all eight logs:

| Command | Run 1 | Run 2 |
| --- | --- | --- |
| `:app:protocolJvmTest` | 44s · stored · `problems=0` | `Reusing configuration cache.` · 3s · reused |
| `rustNdkVersion` | 2s · stored · `problems=0` | `Reusing configuration cache.` · 1s · reused |
| `spotlessCheck detekt lintDebug` | 2m 7s · stored · `problems=0` | `Reusing configuration cache.` · 5s · reused |
| `:app:releaseHookGuard` | 3m 20s · stored · `:app:releaseHookGuard` executed | `Reusing configuration cache.` · 4s · reused · executed again |

Run 1 of each pair reports `Calculating task graph as configuration cache cannot be reused because
Gradle user home directory has changed.` — the previous entry was stored under an earlier
`GRADLE_USER_HOME` path; it is an invalidation reason, not a problem. The `@InputDirectory` change is
exercised by both `releaseHookGuard` runs, which still execute and pass with no configuration-cache
problem. The device run was not repeated, as instructed.
