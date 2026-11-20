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
