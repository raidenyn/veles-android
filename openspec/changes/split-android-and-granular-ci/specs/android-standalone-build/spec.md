## ADDED Requirements

### Requirement: Android lives in an independent build under `android/`
All Android application code and build configuration SHALL live under `android/`: the `app` module at `android/app/`, its own `settings.gradle.kts`, `build.gradle.kts`, `gradle.properties`, `gradle/libs.versions.toml`, `config/detekt/`, Spotless Kotlin configuration and `.editorconfig`. The Android build MUST NOT evaluate root build files (`build.gradle.kts`, `settings.gradle.kts`, `gradle.properties`); its only accesses outside `android/` are the `rust/` build, `protocol/` fixtures, the wrapper and `.java-version`, the shared hook script, generated outputs under the repository-root `build/`, Git metadata, and explicit signing inputs under `config/android_keys/`.

#### Scenario: Build alone
- **WHEN** a developer runs `cd android && ./gradlew assembleDebug`
- **THEN** the debug APK is built without evaluating the root build

#### Scenario: Kotlin formatting check
- **WHEN** `spotlessCheck` runs at the Android build root
- **THEN** it verifies the formatting of Kotlin sources under `android/` against the rules of `android/.editorconfig`

### Requirement: One wrapper, shimmed in `android/`
The repository SHALL contain exactly one Gradle wrapper at the root. `android/gradlew` and `android/gradlew.bat` MUST be shims that delegate to the root wrapper, keep LF line endings and the executable bit where applicable, and MUST NOT be symbolic links.

#### Scenario: Shim from the Android folder
- **WHEN** `./gradlew --version` runs from `android/`
- **THEN** it reports the Gradle version of the root wrapper

#### Scenario: No second wrapper jar
- **WHEN** the contract suite scans the repository
- **THEN** it finds exactly one `gradle-wrapper.jar`

#### Scenario: Windows shim
- **WHEN** `gradlew.bat` runs from `android/` on Windows
- **THEN** it calls `..\gradlew.bat` with the same arguments

### Requirement: Dependencies point one way
The build dependency direction SHALL be root -> android -> rust. The root build MUST include the `android` and `rust` builds and reach Android only through included-build task references, never as a project of its own build; `android/` MUST consume the Rust build and MUST NOT reference the root build; `rust/` MUST NOT reference Android or `:app`. Android MAY consume the Rust build through scripts instead of `includeBuild` only if the real-build spike shows the included build unusable, and then it MUST keep the same output directories and version inputs.

#### Scenario: Root orchestration
- **WHEN** `./gradlew assembleDebug` runs from the repository root
- **THEN** the root alias builds the debug APK through the included `android` build

#### Scenario: Rust has no Android reference
- **WHEN** the `rust/` build is evaluated alone
- **THEN** it configures without the Android build or `:app`

#### Scenario: Shared Rust build
- **WHEN** both the root build and `android/` include `rust/`
- **THEN** `rustJni` and `rustHostJni` run once per invocation and produce the same outputs

#### Scenario: Script fallback
- **WHEN** Android invokes the Rust tasks through scripts instead of `includeBuild`
- **THEN** the output directories and tool versions are identical to the included-build flow

### Requirement: Root aliases keep documented commands working
The root build SHALL provide aliases for the documented Android commands: app aliases (`assembleDebug`, `assembleRelease`, `testDebugUnitTest`, `lintDebug`, `detekt`, `connectedDebugAndroidTest`) targeting the `:app` tasks of the included build, Android-root aliases (`spotlessCheck`, `spotlessApply`) targeting the included build's root tasks, and the retained root aggregates (`protocolCheck`, `clean`) depending on included-build tasks.

#### Scenario: Spotless alias
- **WHEN** `./gradlew spotlessCheck` runs from the repository root
- **THEN** it runs the `:spotlessCheck` task of the Android build, not `:app:spotlessCheck`

#### Scenario: Clean
- **WHEN** `./gradlew clean` runs from the repository root
- **THEN** it removes the root, Rust and Android build outputs

### Requirement: Root aliases are resolved in CI
A change to the root build SHALL cause the `tooling` node to resolve every root alias with `--dry-run`, so a broken alias is caught without selecting the Android node.

#### Scenario: Alias resolution
- **WHEN** a pull request edits the root build
- **THEN** `tooling` resolves `assembleDebug assembleRelease testDebugUnitTest lintDebug spotlessCheck detekt connectedDebugAndroidTest protocolCheck` with `--dry-run`

#### Scenario: Broken alias
- **WHEN** an alias targets a task that does not exist in the included build
- **THEN** the dry run fails and `ci-ok` fails

### Requirement: The Rust build owns its outputs and Android stages them
The `rust/` Gradle build SHALL own the Rust tasks (`rustToolchainCheck`, `rustInstall`, `rustWasm`, `rustHostJni`, `rustJni`, lint, format, test, package, `rustNdkVersion`) and every consumer MUST reach them through included-build task references, or through scripts with identical outputs, task dependencies and version inputs when the script fallback of "Dependencies point one way" applies. Outputs MUST stay under the repository-root `build/` (`build/rust/target`, `build/rust/host-jni`, `build/rust-tools`), and JNI libraries MUST be written to `build/rust/jniLibs/<abi>`. Android MUST add that directory as a `jniLibs` source, MUST make `merge*JniLibFolders` depend on `rustJni`, MUST depend its unit tests on `rustHostJni` and MUST put `build/rust/host-jni` on `java.library.path`.

#### Scenario: JNI libraries staged into the APK
- **WHEN** `android/` builds the debug APK
- **THEN** the packaged `lib/<abi>/` entries come from `build/rust/jniLibs/<abi>` and `rust/scripts/verify-apk-jni.sh` accepts the APK at `android/app/build/outputs/apk/debug/app-debug.apk`

#### Scenario: JNI task dependency
- **WHEN** the task graph for `mergeDebugJniLibFolders` is resolved
- **THEN** it contains `rustJni` of the Rust build

#### Scenario: Host JNI for unit tests
- **WHEN** Android unit tests run
- **THEN** `rustHostJni` has run and `java.library.path` contains `build/rust/host-jni`

#### Scenario: Fixture paths
- **WHEN** Android tests read protocol fixtures
- **THEN** they resolve `../protocol/v1` and `../protocol/conformance` from the Android build root

### Requirement: The NDK version has one source and a defined lookup
The NDK version SHALL be defined once in `rust/ndk-version`, read by the Rust build and by Android's `ndkVersion`. The NDK directory MUST be resolved from `ANDROID_NDK_HOME` first, then `$ANDROID_SDK_ROOT/ndk/<version>`, then `sdk.dir` of `local.properties`, and `rustJni` MUST fail with a message naming the version and the searched locations when none exists.

#### Scenario: Environment variable wins
- **WHEN** `ANDROID_NDK_HOME` points to an NDK and `$ANDROID_SDK_ROOT/ndk/<version>` also exists
- **THEN** `rustJni` uses the directory from `ANDROID_NDK_HOME`

#### Scenario: SDK directory lookup
- **WHEN** `ANDROID_NDK_HOME` is unset and `$ANDROID_SDK_ROOT/ndk/<version>` exists
- **THEN** `rustJni` uses that directory

#### Scenario: NDK not installed
- **WHEN** the NDK version in `rust/ndk-version` is not found in any lookup location
- **THEN** `rustJni` fails and names the version and the locations it searched

#### Scenario: Single definition
- **WHEN** `rust/ndk-version` changes
- **THEN** both `rustNdkVersion` and Android's `ndkVersion` report the new value

### Requirement: Android CI nodes run from `android/`
The Android node's Gradle commands SHALL run with `working-directory: android`, while repository-level guards (the constant-time MAC comparison guard, the raw `android.util.Log` guard and the pairing-code guard) MUST run from the repository root. The workflow contract suites MUST pin both.

#### Scenario: Gradle from the Android folder
- **WHEN** the contract suite inspects `check-android.yml`
- **THEN** every `./gradlew` step has `working-directory: android`

#### Scenario: Guards from the repository root
- **WHEN** the contract suite inspects the guard steps of `check-android.yml` and `check-web-extension.yml`
- **THEN** none sets a `working-directory`

### Requirement: The MAC comparison guard scans explicit directories
`scripts/check-mac-comparisons.mjs` SHALL accept a repeatable `--scan DIR`, MUST require every scan directory to exist and MUST fail with exit code 2 on an empty scan set. Its default scan roots MUST be `android/app/src/main` and `web-extension/src`. Android lint MUST run it with `--scan android/app/src/main` and `web-extension / lint` with `--scan web-extension/src`.

#### Scenario: Violation in Kotlin
- **WHEN** a direct comparison of MAC material is added under `android/app/src/main`
- **THEN** `android / lint` fails

#### Scenario: Violation in TypeScript
- **WHEN** a direct comparison of MAC material is added under `web-extension/src`
- **THEN** `web-extension / lint` fails

#### Scenario: Missing scan directory
- **WHEN** `--scan` names a directory that does not exist
- **THEN** the script exits with code 2

#### Scenario: Default roots
- **WHEN** the script runs without arguments from the repository root
- **THEN** it scans `android/app/src/main` and `web-extension/src`

### Requirement: The pairing-code guard is split by component
The Kotlin half of the pairing-code guard SHALL run in `android / lint`, and its `web-extension/src/debug` half MUST run in `web-extension / lint`.

#### Scenario: Pairing code in the Chrome debug surface
- **WHEN** a pairing code reaches a log under `web-extension/src/debug`
- **THEN** `web-extension / lint` fails

#### Scenario: Pairing code in the Android debug surface
- **WHEN** a pairing code reaches a logger under `android/app/src/debug`
- **THEN** `android / lint` fails

### Requirement: Cross-component checks stay with the protocol node
The Kotlin protocol-fixture consumer and the session loopback check SHALL remain contracts of the `protocol` node, run through `./gradlew protocolCheck` using included-build tasks of `android`. The loopback classpath file MUST be written to `android/app/build/session-loopback/classpath.txt`. The `rust / jni-integration` job MUST keep the clean contract order and call the root aliases.

#### Scenario: Android change selects the protocol contract
- **WHEN** a pull request changes `android/app/src/main/java/`
- **THEN** `android` and `protocol` are selected

#### Scenario: Clean contract
- **WHEN** `rust / jni-integration` runs
- **THEN** `assembleDebug`, the extension build, `./gradlew clean`, `assert-clean.sh`, the second `assembleDebug` and JNI verification run in that order

### Requirement: Build identity is preserved by the move
Building from `android/` SHALL produce the same `versionName` and `versionCode` and the same APK signing certificate as the previous layout on the same commit.

#### Scenario: Version parity
- **WHEN** `cd android && ./gradlew -q :app:androidGitVersion` runs on a commit that the previous layout built as 0.4.1
- **THEN** it reports the same version name and code

#### Scenario: Certificate parity
- **WHEN** the debug and release APKs are built before and after the move with the same signing configuration
- **THEN** their signing certificate fingerprints are identical

### Requirement: Signing keys stay in place
`config/android_keys/` MUST NOT be moved, copied or deleted, and key material MUST NOT be committed. The documented fixture build SHALL use `--init-script ../config/android_keys/fixture-signing.gradle` from `android/`, and the owner of the local init script MUST confirm that it works with the new layout before the move merges.

#### Scenario: Fixture signing
- **WHEN** a developer builds the fixture with the documented command from `android/`
- **THEN** the key material is read from the repository-root `config/android_keys/` and is never committed

#### Scenario: Owner confirmation
- **WHEN** the migration pull request is ready to merge
- **THEN** the owner's confirmation that the local init script works with the new layout is recorded in the pull request

### Requirement: Android builds keep installing git hooks
Every Android build SHALL keep running `installGitHooks` before `:app:preBuild` with the behaviour required by `commit-conventions` (set `core.hooksPath` only when unset, warn and leave other values unchanged, do nothing under `CI=true`). The task MUST come from one shared script applied by both the root build and the Android build.

#### Scenario: Android folder build
- **WHEN** a contributor with no `core.hooksPath` runs `cd android && ./gradlew assembleDebug`
- **THEN** `core.hooksPath` becomes `.githooks`

#### Scenario: Root alias build
- **WHEN** a contributor with no `core.hooksPath` runs `./gradlew assembleDebug` from the repository root
- **THEN** `core.hooksPath` becomes `.githooks`

#### Scenario: Custom hooks path
- **WHEN** `core.hooksPath` is already set to another value
- **THEN** the build prints a warning and leaves it unchanged

### Requirement: Live files no longer refer to the old layout
Live build files, scripts, workflows, documentation and ignore files SHALL refer to `android/app/...` instead of `app/...`, as shown by a reproducible audit that excludes frozen evidence and archived changes. Historical records MUST be left as written.

#### Scenario: Audit is clean
- **WHEN** the audit searches live files for references to the old module path
- **THEN** none remain outside the excluded historical records

### Requirement: Release workflows follow the relocated APK
The Android producer workflow SHALL locate and upload the release APK from `android/app/build/outputs/...`, and `publish-release.yml` MUST keep downloading the reusable build's artifact into its unsigned directory and signing it without rebuilding.

#### Scenario: Producer output
- **WHEN** `build-android.yml` produces the unsigned release APK
- **THEN** it reads the APK from `android/app/build/outputs/...` and uploads it as before

#### Scenario: Publish signing
- **WHEN** `publish-release.yml` signs the Android release
- **THEN** it signs the downloaded artifact and does not rebuild Android
