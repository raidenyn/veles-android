## Configuration cache problems at the merge base (Linux)

Task 1.5 probe. Base commit `597ee2c691f2d4c2dec1c4450072eca86a76054e` (`git merge-base HEAD origin/master`),
2026-11-20, Linux container. JDK: Temurin 21.0.12 at `~/.local/jdks/temurin-21.0.12`; Android SDK/NDK
from `ANDROID_SDK_ROOT=/home/dev/.android-sdk` (NDK 29.0.14206865); Gradle 9.5.0 through the repository wrapper.

Every invocation below ran in one fresh worktree at the merge base, with
`--configuration-cache --configuration-cache-problems=warn --console=plain`, in the directory and with the
command the inventory names. `-q` is dropped where the workflow uses it, because `-q` hides the problem summary.
A build that fails while *executing* a task still stores its configuration cache entry (that is what `warn`
means); the failure is recorded with the invocation. Each `- Task …` line below is followed in Gradle's own
output by the same two-line requirement link, which is omitted here.

The observed problem is always the same family: a task action or input holds a Gradle script object reference,
an `org.gradle.api.Project`, or another `Task` (`AndroidUnitTest`), so the task cannot be serialized into the
cache entry. A few tasks then fail at execution time because the script reference is null after deserialization.

`cd android && ./gradlew --version` in the acceptance check is not a Gradle configuration and is not probed.
The `--self-test` build of `verify/check-root-aliases.sh` is probed; it fails on a missing task before the
configuration cache matters, and stores a clean entry. The Windows `gradlew.bat` jobs and the
macOS/Windows installer producers are out of scope for 1.5.

### Tasks with problems, at a glance

| Task | Type | Problem kinds |
| --- | --- | --- |
| `:android:app:sessionLoopbackClasspath` | `org.gradle.api.DefaultTask` | Task object |
| `:app:androidGitVersion` | `org.gradle.api.DefaultTask` | Project object |
| `:app:releaseHookGuard` | `org.gradle.api.DefaultTask` | script object reference, Project object |
| `:bridgeHostBuild` | `org.gradle.api.tasks.Exec` | script object reference |
| `:bridgeHostToolchainCheck` | `org.gradle.api.DefaultTask` | script object reference |
| `:bridgeInstallerToolchainCheck` | `org.gradle.api.DefaultTask` | script object reference |
| `:bridgeNodeCheck` | `org.gradle.api.DefaultTask` | script object reference |
| `:bridgeRustToolchainCheck` | `org.gradle.api.DefaultTask` | script object reference |
| `:installCargoCyclonedx` | `org.gradle.api.tasks.Exec` | script object reference |
| `:installCargoDeny` | `org.gradle.api.tasks.Exec` | script object reference |
| `:protocolNodeCheck` | `org.gradle.api.DefaultTask` | script object reference |
| `:rust:installCargoNdk` | `org.gradle.api.tasks.Exec` | script object reference |
| `:rust:installWasmBindgenCli` | `org.gradle.api.tasks.Exec` | script object reference |
| `:rust:installWasmPack` | `org.gradle.api.tasks.Exec` | script object reference |
| `:rust:rustHostJni` | `org.gradle.api.tasks.Exec` | script object reference |
| `:rust:rustJni` | `org.gradle.api.tasks.Exec` | script object reference |
| `:rust:rustJniConformance` | `org.gradle.api.tasks.Exec` | script object reference |
| `:rust:rustLintAndroid` | `org.gradle.api.tasks.Exec` | script object reference |
| `:rust:rustNdkVersion` | `org.gradle.api.DefaultTask` | script object reference |
| `:rust:rustPackage` | `org.gradle.api.tasks.Sync` | script object reference |
| `:rust:rustToolchainCheck` | `org.gradle.api.DefaultTask` | script object reference |
| `:rust:rustWasm` | `org.gradle.api.tasks.Exec` | script object reference |
| `:rust:rustWasmBindgenConsistency` | `org.gradle.api.DefaultTask` | script object reference |
| `:rust:rustWasmSmoke` | `org.gradle.api.tasks.Exec` | script object reference |
| `:rust:verifyCargoNdk` | `org.gradle.api.DefaultTask` | script object reference |
| `:rust:verifyWasmBindgenCli` | `org.gradle.api.DefaultTask` | script object reference |
| `:rust:verifyWasmPack` | `org.gradle.api.DefaultTask` | script object reference |
| `:rustNdkDir` | `org.gradle.api.DefaultTask` | script object reference |
| `:verifyCargoCyclonedx` | `org.gradle.api.DefaultTask` | script object reference |

### Workflow invocations

#### `w01-rustNdkVersion`: `./gradlew rustNdkVersion` in `android/`

Call site: build-android.yml:40, build-rust.yml:37, check-android.yml:115, check-android.yml:143, check-rust.yml:36, check-rust.yml:69, check-rust.yml:102.

The workflows pass `-q`; the probe omits it so the problem summary is visible.

Result: `BUILD FAILED in 2s`.
Execution failure: `Execution failed for task ':rust:rustNdkVersion'` — `Cannot invoke "Build_gradle.getNdkVersion()" because "this.this$0" is null`.
Configuration cache: 1 problem(s) reusing the cache.

- Task `:rust:rustNdkVersion` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.

#### `w02-:app:androidGitVersion`: `./gradlew :app:androidGitVersion` in `android/`

Call site: build-android.yml:62.

The workflow pipes into `awk` and passes `-q`; the probe omits `-q`.

Result: `BUILD FAILED in 3s`.
Execution failure: `Execution failed for task ':app:androidGitVersion'` — `Cannot get property 'extensions' on null object`.
Configuration cache: 2 problem(s) storing the cache.

- Task `:app:androidGitVersion` of type `org.gradle.api.DefaultTask`: cannot deserialize object of type 'org.gradle.api.Project' as these are not supported with the configuration cache.
- Task `:app:androidGitVersion` of type `org.gradle.api.DefaultTask`: cannot serialize object of type 'org.gradle.api.internal.project.DefaultProject', a subtype of 'org.gradle.api.Project', as these are not supported with the configuration cache.

#### `w02b-assembleRelease`: `./gradlew assembleRelease` in `android/`

Call site: build-android.yml:54.

Result: `BUILD FAILED in 4s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 8 problem(s) storing the cache.

- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w04-rustPackage`: `./gradlew rustPackage` in `repository root`

Call site: build-rust.yml:40.

Result: `BUILD FAILED in 8s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 24 problem(s) storing the cache, 22 unique.

- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmBindgenCli` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmBindgenCli` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmPack` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmPack` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustPackage` of type `org.gradle.api.tasks.Sync`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustPackage` of type `org.gradle.api.tasks.Sync`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasmBindgenConsistency` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasmBindgenConsistency` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasm` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.

#### `w05-rustWasm`: `./gradlew rustWasm` in `repository root`

Call site: build-web-extension.yml:47, check-web-extension.yml:46, check-web-extension.yml:89, check-web-extension.yml:123, check-rust.yml:116.

Result: `BUILD FAILED in 5s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 14 problem(s) storing the cache.

- Task `:rust:installWasmBindgenCli` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmBindgenCli` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmPack` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmPack` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasmBindgenConsistency` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasmBindgenConsistency` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasm` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasm` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyWasmBindgenCli` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyWasmBindgenCli` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyWasmPack` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyWasmPack` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w06-installCargoDeny`: `./gradlew installCargoDeny` in `repository root`

Call site: build-supply-chain.yml:33.

Result: `BUILD FAILED in 5s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 4 problem(s) storing the cache.

- Task `:installCargoDeny` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:installCargoDeny` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w07-spotlessCheck`: `./gradlew spotlessCheck` in `android/`

Call site: check-android.yml:32.

Result: `BUILD SUCCESSFUL in 34s`.
Configuration cache: entry stored with no problems.

No `- Task …` problem lines.

#### `w08-detekt`: `./gradlew detekt` in `android/`

Call site: check-android.yml:34.

Result: `BUILD SUCCESSFUL in 25s`.
Configuration cache: entry stored with no problems.

No `- Task …` problem lines.

#### `w09-lintDebug`: `./gradlew lintDebug` in `android/`

Call site: check-android.yml:64.

The first attempt was killed when another session sharing the Gradle user home ran `./gradlew --stop`; the entry was reused on the rerun, so the store-time result (0 problems) comes from the killed attempt.

Result: `BUILD SUCCESSFUL in 3m 1s`.
Configuration cache: entry reused with no problems.

No `- Task …` problem lines.

#### `w10-testDebugUnitTest`: `./gradlew testDebugUnitTest` in `android/`

Call site: check-android.yml:88.

Result: `BUILD FAILED in 37s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 8 problem(s) storing the cache, 6 unique.

- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w11-releaseHookGuard`: `./gradlew :app:releaseHookGuard` in `android/`

Call site: check-android.yml:122.

Result: `BUILD FAILED in 14s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 12 problem(s) storing the cache.

- Task `:app:releaseHookGuard` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:app:releaseHookGuard` of type `org.gradle.api.DefaultTask`: cannot deserialize object of type 'org.gradle.api.Project' as these are not supported with the configuration cache.
- Task `:app:releaseHookGuard` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:app:releaseHookGuard` of type `org.gradle.api.DefaultTask`: cannot serialize object of type 'org.gradle.api.internal.project.DefaultProject', a subtype of 'org.gradle.api.Project', as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w12-connectedDebugAndroidTest`: `./gradlew connectedDebugAndroidTest` in `android/`

Call site: check-android.yml:166.

Probed with `--dry-run`: no device or emulator is available to this task. The task graph and its CC entry are still built and stored.

Result: `BUILD SUCCESSFUL in 7s`.
Configuration cache: 8 problem(s) storing the cache.

- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w13-rustFormat-rustLint`: `./gradlew rustFormat rustLint` in `repository root`

Call site: check-rust.yml:43.

Result: `BUILD FAILED in 3s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 8 problem(s) storing the cache.

- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustLintAndroid` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustLintAndroid` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w14-rustInstall`: `./gradlew rustInstall` in `repository root`

Call site: check-rust.yml:46, check-rust.yml:78, check-rust.yml:124.

Result: `BUILD FAILED in 2s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 16 problem(s) storing the cache.

- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmBindgenCli` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmBindgenCli` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmPack` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmPack` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasmBindgenConsistency` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasmBindgenConsistency` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyWasmBindgenCli` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyWasmBindgenCli` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyWasmPack` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.

#### `w15-rustTest`: `./gradlew rustTest` in `repository root`

Call site: check-rust.yml:76.

Result: `BUILD FAILED in 2s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 16 problem(s) storing the cache.

- Task `:rust:installWasmBindgenCli` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmBindgenCli` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmPack` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmPack` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasmBindgenConsistency` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasmBindgenConsistency` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasmSmoke` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasmSmoke` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasm` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasm` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyWasmBindgenCli` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyWasmBindgenCli` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyWasmPack` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.

#### `w17-assembleDebug`: `./gradlew assembleDebug` in `repository root`

Call site: check-rust.yml:109, check-rust.yml:121.

Result: `BUILD FAILED in 5s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 8 problem(s) storing the cache.

- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w18-rustHostJni`: `./gradlew rustHostJni` in `repository root`

Call site: check-rust.yml:114, check-protocol.yml:46.

Result: `BUILD FAILED in 3s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 6 problem(s) storing the cache, 4 unique.

- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w20-clean`: `./gradlew clean` in `repository root`

Call site: check-rust.yml:119.

Result: `BUILD SUCCESSFUL in 2s`.
Configuration cache: entry stored with no problems.

No `- Task …` problem lines.

#### `w21-assembleDebug-after-clean`: `./gradlew assembleDebug` in `repository root`

Call site: check-rust.yml:121.

The same command as `w17`, run after `clean` as the workflow does. It reused the entry stored by `w17`.

Result: `BUILD FAILED in 3s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 4 problem(s) reusing the cache.

- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.

#### `w23-scriptsTest`: `./gradlew scriptsTest` in `repository root`

Call site: check-tooling.yml:45.

Result: `BUILD SUCCESSFUL in 47s`.
Configuration cache: entry stored with no problems.

No `- Task …` problem lines.

#### `w24-bridgeFormat-group`: `./gradlew bridgeFormat bridgeLint bridgeTypecheck bridgeRustFormat bridgeRustLint` in `repository root`

Call site: check-bridge-scaffold.yml:38.

Result: `BUILD FAILED in 2s`.
Execution failure: `Execution failed for task ':bridgeRustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 4 problem(s) storing the cache.

- Task `:bridgeNodeCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeNodeCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeRustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeRustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w25-bridgeNpmTest-bridgeRustTest`: `./gradlew bridgeNpmTest bridgeRustTest` in `repository root`

Call site: check-bridge-scaffold.yml:60.

Result: `BUILD FAILED in 2s`.
Execution failure: `Execution failed for task ':bridgeNodeCheck'` — `Cannot invoke "Build_gradle.requireNpm(String)" because "this.this$0" is null`.
Configuration cache: 4 problem(s) storing the cache.

- Task `:bridgeNodeCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeNodeCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeRustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeRustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w26-bridgeInstallerFormat-Lint`: `./gradlew bridgeInstallerFormat bridgeInstallerLint` in `repository root`

Call site: check-installer.yml:40.

Result: `BUILD FAILED in 2s`.
Execution failure: `Execution failed for task ':bridgeInstallerToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 2 problem(s) storing the cache.

- Task `:bridgeInstallerToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeInstallerToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w27-bridgeInstallerTest`: `./gradlew bridgeInstallerTest` in `repository root`

Call site: check-installer.yml:60.

Result: `BUILD FAILED in 2s`.
Execution failure: `Execution failed for task ':bridgeInstallerToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 2 problem(s) storing the cache.

- Task `:bridgeInstallerToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeInstallerToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w27b-bridgeInstaller-macos-graph`: `./gradlew bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest` in `repository root`

Call site: check-installer.yml:105 (the macOS job, probed on Linux).

Result: `BUILD FAILED in 1m 4s`.
Execution failure: `Execution failed for task ':bridgeInstallerToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 4 problem(s) storing the cache.

- Task `:bridgeInstallerToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeInstallerToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w28-bridgeInstallerScriptsTest`: `./gradlew bridgeInstallerScriptsTest` in `repository root`

Call site: check-installer.yml:125.

Result: `BUILD SUCCESSFUL in 2m 12s`.
Configuration cache: entry stored with no problems.

No `- Task …` problem lines.

#### `w29-bridgeHostCheck-group`: `./gradlew bridgeHostToolchainCheck bridgeHostFormat bridgeHostLint` in `repository root`

Call site: check-native-host.yml:42.

Result: `BUILD FAILED in 4s`.
Execution failure: `Execution failed for task ':bridgeHostToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 2 problem(s) storing the cache.

- Task `:bridgeHostToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeHostToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w30-bridgeHostTest`: `./gradlew bridgeHostTest` in `repository root`

Call site: check-native-host.yml:59.

Result: `BUILD FAILED in 3s`.
Execution failure: `Execution failed for task ':bridgeHostToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 2 problem(s) storing the cache.

- Task `:bridgeHostToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeHostToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w31-bridgeHostCheck-group-tests`: `./gradlew bridgeHostToolchainCheck bridgeHostFormat bridgeHostLint bridgeHostTest` in `repository root`

Call site: check-native-host.yml:98 (the macOS job, probed on Linux).

Result: `BUILD FAILED in 2s`.
Execution failure: `Execution failed for task ':bridgeHostToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 2 problem(s) storing the cache.

- Task `:bridgeHostToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeHostToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w32-bridgeHostBuild`: `./gradlew bridgeHostBuild` in `repository root`

Call site: check-native-host.yml:164 (the macOS job, probed on Linux).

Result: `BUILD FAILED in 2s`.
Execution failure: `Execution failed for task ':bridgeHostToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 6 problem(s) storing the cache, 4 unique.

- Task `:bridgeHostBuild` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeHostBuild` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeHostToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeHostToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `w33-rustHostJni`: `./gradlew rustHostJni` in `repository root`

Call site: check-protocol.yml:46.

The same command as `w18`, run before `protocolCheck`. It reused the entry stored by `w18`.

Result: `BUILD FAILED in 2s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 3 problem(s) reusing the cache, 2 unique.

- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.

#### `w34-protocolCheck`: `./gradlew protocolCheck` in `repository root`

Call site: check-protocol.yml:49.

Result: `BUILD FAILED in 3m 53s`.
Execution failure: `Execution failed for task ':android:app:sessionLoopbackClasspath'` — `Cannot invoke "org.gradle.api.tasks.testing.Test.getClasspath()" because "this.$unitTest" is null`.
Configuration cache: 26 problem(s) storing the cache, 24 unique.

- Task `:android:app:sessionLoopbackClasspath` of type `org.gradle.api.DefaultTask`: cannot deserialize object of type 'org.gradle.api.Task' as these are not supported with the configuration cache.
- Task `:android:app:sessionLoopbackClasspath` of type `org.gradle.api.DefaultTask`: cannot serialize object of type 'com.android.build.gradle.tasks.factory.AndroidUnitTest', a subtype of 'org.gradle.api.Task', as these are not supported with the configuration cache.
- Task `:bridgeNodeCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeNodeCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:protocolNodeCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:protocolNodeCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmBindgenCli` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmBindgenCli` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmPack` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmPack` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustWasmBindgenConsistency` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.

#### `w35-installGitCliff`: `./gradlew installGitCliff` in `repository root`

Call site: release-build.yml:125, publish-release.yml:103.

Result: `BUILD SUCCESSFUL in 7s`.
Configuration cache: entry stored with no problems.

No `- Task …` problem lines.

### Script invocations

#### `s01-rust-help`: `../gradlew help` in `rust/`

Call site: verify/check-android-layout.sh:147.

Result: `BUILD SUCCESSFUL in 20s`.
Configuration cache: entry stored with no problems.

No `- Task …` problem lines.

#### `s02-layout-rerun-group`: `./gradlew assembleDebug testDebugUnitTest rustHostJni rustJni --no-parallel --rerun-tasks` in `repository root`

Call site: verify/check-android-layout.sh:162.

Result: `BUILD FAILED in 10s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 12 problem(s) storing the cache, 10 unique.

- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `s04-layout-alias-dry-run`: `./gradlew --dry-run assembleDebug assembleRelease testDebugUnitTest lintDebug spotlessCheck spotlessApply detekt clean connectedDebugAndroidTest protocolCheck` in `repository root`

Call site: verify/check-android-layout.sh:203.

Result: `BUILD SUCCESSFUL in 16s`.
Configuration cache: 32 problem(s) storing the cache, 30 unique.

- Task `:android:app:sessionLoopbackClasspath` of type `org.gradle.api.DefaultTask`: cannot deserialize object of type 'org.gradle.api.Task' as these are not supported with the configuration cache.
- Task `:android:app:sessionLoopbackClasspath` of type `org.gradle.api.DefaultTask`: cannot serialize object of type 'com.android.build.gradle.tasks.factory.AndroidUnitTest', a subtype of 'org.gradle.api.Task', as these are not supported with the configuration cache.
- Task `:bridgeNodeCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeNodeCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:protocolNodeCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:protocolNodeCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmBindgenCli` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmBindgenCli` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmPack` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmPack` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.

#### `s05-filtered-tests-dry-run`: `./gradlew testDebugUnitTest --dry-run --tests me.nagaev.veles.otp.handlers.RegexMessageHandlerTest` in `android/`

Call site: verify/check-android-layout.sh:218.

Result: `BUILD SUCCESSFUL in 6s`.
Configuration cache: 6 problem(s) storing the cache, 4 unique.

- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `s06-layout-scratch-assembleDebug`: `./gradlew assembleDebug` in `android/`

Call site: verify/check-android-layout.sh:128 (in a copy of the tree with root build.gradle.kts, settings.gradle.kts and gradle.properties renamed).

The copy, tag and root-file removal follow the script exactly.

Result: `BUILD FAILED in 1m`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 8 problem(s) storing the cache.

- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `s07-root-aliases-dry-run`: `./gradlew --dry-run assembleDebug assembleRelease testDebugUnitTest lintDebug spotlessCheck detekt connectedDebugAndroidTest protocolCheck` in `repository root`

Call site: verify/check-root-aliases.sh:41.

Result: `BUILD SUCCESSFUL in 8s`.
Configuration cache: 32 problem(s) storing the cache, 30 unique.

- Task `:android:app:sessionLoopbackClasspath` of type `org.gradle.api.DefaultTask`: cannot deserialize object of type 'org.gradle.api.Task' as these are not supported with the configuration cache.
- Task `:android:app:sessionLoopbackClasspath` of type `org.gradle.api.DefaultTask`: cannot serialize object of type 'com.android.build.gradle.tasks.factory.AndroidUnitTest', a subtype of 'org.gradle.api.Task', as these are not supported with the configuration cache.
- Task `:bridgeNodeCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:bridgeNodeCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:protocolNodeCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:protocolNodeCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmBindgenCli` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmBindgenCli` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmPack` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installWasmPack` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustHostJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.

#### `s08-root-alias-selftest`: `./gradlew -p <scratch> --dry-run brokenAlias` in `repository root`

Call site: verify/check-root-aliases.sh:79.

The self-test of the alias check: a scratch build whose alias targets a missing task. It must fail.

Result: `BUILD FAILED in 2s` — the expected failure of the self-test (a missing task), before the
configuration cache is relevant. `Configuration cache entry stored.` with no problems.

No `- Task …` problem lines.

#### `s09-verifyCargoCyclonedx`: `./gradlew verifyCargoCyclonedx` in `repository root`

Call site: verify/generate-sboms.sh:20.

Result: `BUILD FAILED in 3s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 6 problem(s) storing the cache.

- Task `:installCargoCyclonedx` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:installCargoCyclonedx` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:verifyCargoCyclonedx` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:verifyCargoCyclonedx` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `s10-rustJniConformance-assembleDebug`: `./gradlew rustJniConformance assembleDebug` in `repository root`

Call site: rust/scripts/test-jni-conformance.sh:6.

The `adb` steps after the Gradle builds are not Gradle invocations and were not run.

Result: `BUILD FAILED in 4s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 10 problem(s) storing the cache.

- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJniConformance` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJniConformance` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `s11-assembleDebugAndroidTest`: `./gradlew :app:assembleDebugAndroidTest` in `android/`

Call site: rust/scripts/test-jni-conformance.sh:8.

Result: `BUILD FAILED in 5s`.
Execution failure: `Execution failed for task ':rust:rustToolchainCheck'` — `Cannot invoke "Build_gradle.requireOnPath(String, String, String)" because "this.this$0" is null`.
Configuration cache: 8 problem(s) storing the cache.

- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:installCargoNdk` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustJni` of type `org.gradle.api.tasks.Exec`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:rustToolchainCheck` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rust:verifyCargoNdk` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

#### `s12-ndk-lookup-scratch`: `./gradlew -p <scratch>/rust rustNdkDir` in `scratch rust build`

Call site: rust/scripts/test-ndk-lookup.sh (the `run_lookup` helper).

The scratch build carries only rust/build.gradle.kts, rust/settings.gradle.kts, rust/ndk-version and rust/toolchain-tools.toml, exactly as the script copies them, so it has no repository gradle.properties; CC and problems=warn come only from the flags. One of the script's identical lookup invocations was probed.

Result: `BUILD FAILED in 11s`.
Execution failure: `Execution failed for task ':rustNdkDir'` — `Cannot invoke "Build_gradle.resolveNdkDirectory()" because "this.this$0" is null`.
Configuration cache: 2 problem(s) storing the cache.

- Task `:rustNdkDir` of type `org.gradle.api.DefaultTask`: cannot deserialize Gradle script object references as these are not supported with the configuration cache.
- Task `:rustNdkDir` of type `org.gradle.api.DefaultTask`: cannot serialize Gradle script object references as these are not supported with the configuration cache.

