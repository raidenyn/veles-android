# Configuration-cache reuse — root build

Task 4 steps 6–7 evidence. Host: Linux container, Gradle 9.5.0 through the repository wrapper, Temurin
21.0.12 (`JAVA_HOME=$HOME/.local/jdks/temurin-21.0.12`), `ANDROID_SDK_ROOT=/home/dev/.android-sdk`,
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

[Incubating] Problems report is available at: file:///home/dev/.t3/worktrees/veles-android/t3-699e4a45-1711ce8ea1e8baf4/build/reports/problems/problems-report.html

FAILURE: Build failed with an exception.

* What went wrong:
Configuration cache problems found in this build.

1 problem was found storing the configuration cache.
- Task `:ccProbe` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
  See https://docs.gradle.org/9.5.0/userguide/configuration_cache_requirements.html#config_cache:requirements:disallowed_types

See the complete report at file:///home/dev/.t3/worktrees/veles-android/t3-699e4a45-1711ce8ea1e8baf4/build/reports/configuration-cache/ab84jckcw490vso7brgvp2gpj/n9vrdwsbhz3i07wnvqr14r88/configuration-cache-report.html
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

## Installer build contract

`python3 -I native-bridge/installer/scripts/test_build_contract.py` after the port:

```text
.................................................................
----------------------------------------------------------------------
Ran 65 tests in 9.254s

OK
```
