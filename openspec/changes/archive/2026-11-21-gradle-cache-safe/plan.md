# Gradle cache safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the root, `android/` and `rust/` Gradle builds configuration-cache clean (CC on and strict by default) and build-cache correct. The build cache stays off by default; only three Rust products may be cached, with relocatable keys. Release and verification builds never use either cache.

**Architecture:** Script-lambda tasks become typed task classes declared in the script that uses them, with injected `ExecOperations`/`FileSystemOperations` and lazily wired providers. The `androidgitversion` plugin is replaced by a `ValueSource` in an applied script, proven equal to the plugin by a harness. A contract test enforces `--no-build-cache --no-configuration-cache` on every guarded invocation.

**Tech Stack:** Gradle 9.5.0 Kotlin DSL, AGP, cargo/cargo-ndk/wasm-pack, Node `node:test`, bash, GitHub Actions YAML.

**Spec:** `openspec/changes/gradle-cache-safe/design.md` (D1–D8), `specs/gradle-build-caching/spec.md`, `specs/component-versioning/spec.md`, and the task list `tasks.md`. Plan task N.x maps to tasks.md item N.x.

## Global Constraints

- Gradle properties (all three builds): `org.gradle.configuration-cache=true`, `org.gradle.configuration-cache.problems=fail`. Never `problems=warn`. No committed file sets `org.gradle.caching=true`.
- Guard flags: `--no-build-cache --no-configuration-cache`, placed **after** the task names.
- Cacheable tasks: exactly `rustHostJni`, `rustJni`, `rustWasm`. Every other task gets a reason or is `Sync`/`Copy`/`Delete`.
- Version: prefix `android-v`, code format `MMNNPP`, `versionCode = maxOf(code, 1)`. The task `:app:androidGitVersion` prints the plugin's exact lines under `-q`.
- Error messages and the NDK lookup order (`ANDROID_NDK_HOME`, `$ANDROID_SDK_ROOT/ndk/<v>`, `$ANDROID_HOME/ndk/<v>`, root `local.properties` `sdk.dir`, `android/local.properties` `sdk.dir`) stay byte-identical.
- The NDK directory is resolved at execution time, never at configuration time.
- **Tag safety:** `android-v*` tags and test commits only in a throwaway `git clone --no-hardlinks` under the scratch directory; never pushed. `git tag -l 'android-v9*'` in the worktree prints nothing before the emulator run and before the push.
- Never regenerate or commit signing keys (`.agents/rules/repo-signing-keys.md`). Unit tests never sleep or read the wall clock (`.agents/rules/repo-no-time-based-tests.md`).
- Android runs use a user-level Temurin JDK 21.0.12 through `JAVA_HOME`, unpacked outside the repository.
- Commit subjects follow Conventional Commits: `build(gradle): …`, `build(android): …`, `ci(release): …`, `test(verify): …`, `docs(build): …`. End every commit with the attribution line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A developer with `~/.cargo/config.toml`** (for example sccache or a linker override) runs `--build-cache`. Expect no load and no store for the Rust products. Pinned in Task 7 Step 4.
2. **A worktree at a different absolute path** must hit the cache and restore identical bytes, `.gitignore` included. Pinned in Task 7 Step 3.
3. **A release run on a machine with user-level `org.gradle.caching=true`** must still build. Pinned in Task 7 Step 5 and by the contract test in Task 6.
4. **A new commit or tag between two CC runs** must change the version without `--rerun`. Pinned in Task 7 Step 6.
5. **Configuring the build on a machine without the NDK, Node or `cc`** (for example Windows `rustHostJni`) must still succeed for unrelated tasks. Pinned in Task 3 Step 6 (`../gradlew help` with `ANDROID_NDK_HOME` unset and the SDK hidden) and in the Windows probe in Task 8.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `android/git-version.gradle.kts` | create | `GitVersionValueSource`, the `extra["gitVersion"]` provider, the `androidGitVersion` task |
| `android/scripts/check-git-version-parity.sh` | create | plugin-vs-script parity harness, run manually |
| `android/app/build.gradle.kts` | modify | apply the version script, drop the plugin, providers for keystore env, CC-safe `sessionLoopbackClasspath`/`releaseHookGuard` |
| `android/build.gradle.kts`, `android/gradle/libs.versions.toml`, `android/gradle.properties` | modify | providers, plugin removed, CC properties |
| `rust/build.gradle.kts` | modify | `ToolchainCheckTask`, `CargoInstallTask`, `VerifyToolVersionTask`, `CargoExecTask`, `CargoBuildTask`, provider-based pins |
| `rust/gradle.properties` | create | `jvmargs` and the CC properties |
| `build.gradle.kts`, `gradle.properties` | modify | the same task shapes for root tasks, installer pipeline opt-outs, CC properties |
| `.githooks/install-hooks.gradle.kts` | modify | `@DisableCachingByDefault` reason |
| `.github/workflows/{build-android,build-rust,build-web-extension,build-native-windows,build-native-macos,build-supply-chain,release-build,publish-release}.yml`, `verify/generate-sboms.sh` | modify | guard flags |
| `verify/test/workflow-contracts.test.mjs` | modify | guard scanner test, mutation checks, updated assertions |
| `BUILD.md`, `CONTRIBUTING.md`, `docs/reproducible-builds.md`, `docs/releasing.md`, `docs/android.md`, `android/AGENTS.md` | modify | docs |
| `openspec/changes/gradle-cache-safe/evidence/*.md` | create | recorded evidence for the PR |

---

### Task 1: Environment and baseline (tasks 1.1–1.5)

**Files:** Create `openspec/changes/gradle-cache-safe/evidence/{timings,apk,cc-problems-before}.md`

**Interfaces:**
- Produces:
  - `$JAVA_HOME`, pointing at the JDK;
  - `$SCRATCH`, the session scratch directory;
  - baseline SHA-256 values in `evidence/apk.md`, which Task 10 compares against;
  - the probe problem list in `evidence/cc-problems-before.md`, which Tasks 3–5 work through.

- [ ] **Step 1:** Download and unpack Temurin 21.0.12 to `~/.local/jdks/temurin-21.0.12`, and export `JAVA_HOME` and `PATH`. Run `javac -version`; expected `javac 21.0.12`.
- [ ] **Step 2:** Record the merge base: `BASE=$(git merge-base HEAD origin/master)`.
- [ ] **Step 3:** Create `git worktree add $SCRATCH/wtA $BASE` and `$SCRATCH/wtB`. In each, run `time (cd android && ./gradlew assembleDebug)`, `time (cd android && ./gradlew testDebugUnitTest)` and `time ./gradlew rustTest`, in order. Write the wall-clock times to `evidence/timings.md` under "Before (merge base)". Remove both worktrees.
- [ ] **Step 4:** Create the clone `git clone --no-hardlinks . $SCRATCH/apk-base && cd $SCRATCH/apk-base && git checkout --detach $BASE && git tag android-v9.9.9`. Then run `(cd android && ./gradlew assembleRelease assembleDebug --no-build-cache)` and `./gradlew rustPackage --no-build-cache` from the clone root. Record in `evidence/apk.md` the `sha256sum` of `android/app/build/outputs/apk/release/app-release-unsigned.apk` and of the debug APK, `build/rust-package/SHA256SUMS`, and `(cd android && ./gradlew -q :app:androidGitVersion)`.
- [ ] **Step 5:** In a fresh `git worktree add $SCRATCH/wtP $BASE`, run each Linux inventory invocation from design *Context* with `--configuration-cache --configuration-cache-problems=warn`, and append the `- Task …` problem lines to `evidence/cc-problems-before.md`, grouped by invocation.
- [ ] **Step 6:** Commit the evidence: `git add openspec/changes/gradle-cache-safe/evidence && git commit -m "docs(openspec): record gradle cache baseline"`.

### Task 2: Version derivation (tasks 2.1–2.5; spec component-versioning)

**Files:**
- Create: `android/git-version.gradle.kts`, `android/scripts/check-git-version-parity.sh`, `evidence/version-rules.md`, `evidence/version-parity.md`
- Modify: `android/app/build.gradle.kts:6,15-18,44-45`, `android/gradle/libs.versions.toml:80`

**Interfaces:**
- Produces:
  - `abstract class GitVersionValueSource : ValueSource<List<String>, GitVersionValueSource.Params>`, with `interface Params : ValueSourceParameters { val repoDir: DirectoryProperty; val prefix: Property<String>; val codeFormat: Property<String> }`. It returns `listOf(name, code.toString())`.
  - `extra["gitVersion"]: Provider<List<String>>`, set on the project that applies the script.
  - A task `androidGitVersion` (`@DisableCachingByDefault(because = "reads git state")`).
- Consumes: nothing.

- [ ] **Step 1:** Run `javap -c -p` on the classes of `~/.gradle/caches/modules-2/files-2.1/com.gladed.androidgitversion/gradle-android-git-version/0.4.14/*/*.jar` (`AndroidGitVersionExtension`, `$Results`, `$CodePart`, `AndroidGitVersion`). Write to `evidence/version-rules.md`: the default format, hidden branches, dirty and untracked rules, commit-hash length, the no-tag name and code, the `MMNNPP` arithmetic, the exact task output lines and order, and whether the Android plugin is required.
- [ ] **Step 2: Write the failing harness `android/scripts/check-git-version-parity.sh`.** It uses `set -euo pipefail`, a temporary directory and `trap` cleanup, and the repository `gradlew` through `--project-dir`. It builds two throwaway projects:
  - `plugin/`: `plugins { id("com.gladed.androidgitversion") version "0.4.14" }` with `androidGitVersion { prefix = "android-v"; codeFormat = "MMNNPP" }`, plus a minimal AGP only if Step 1 requires one;
  - `script/`: `apply(from = "<repo>/android/git-version.gradle.kts")`.

  Each project has its own `settings.gradle.kts`. Where they live:
  - The plugin build lives **inside** the throwaway repository as `.gv-plugin/`, because the plugin finds the repository from its project directory. `.gv-plugin/` is listed in `.git/info/exclude`, so it never makes the tree dirty.
  - The `script/` build lives outside it, under `$TMP/builds/script/`, and passes the throwaway repository as `repoDir` through `-PgitRepoDir=<path>`. The script reads that property and otherwise defaults to `layout.settingsDirectory.dir("..")`, which is the repository root when applied from `android/`. For each state it runs `-q androidGitVersion` in both projects and `diff`s the outputs, then prints `PASS <state>` or `FAIL <state>` and the diff. It exits 1 on any failure. The states: `on-tag` (`android-v1.2.3`), `after-tag` (3 commits), `dirty`, `branch` (`feature/x`), `detached`, `no-tag`, `two-tags` (`android-v1.2.3` and `android-v1.2.4` on one commit), and `prerelease` (`android-v1.2.3-rc1`, only if Step 1 shows support).
- [ ] **Step 3:** Run `android/scripts/check-git-version-parity.sh`. Expected: FAIL (`android/git-version.gradle.kts` is missing).
- [ ] **Step 4:** Implement `android/git-version.gradle.kts` with the interfaces above. `obtain()` runs `git describe`/`git status --porcelain`/`git rev-parse` through injected `ExecOperations` with `isIgnoreExitValue = true`, and applies the Step 1 rules. The task's action captures only the provider, never the script, and prints the plugin's lines with `println`.
- [ ] **Step 5:** Re-run the harness until every state passes. Expected: all `PASS`, exit 0. Paste the output into `evidence/version-parity.md`.
- [ ] **Step 6:** Before editing, capture `cd android && ./gradlew -q :app:androidGitVersion > $SCRATCH/plugin-out.txt` with the plugin still applied. In `android/app/build.gradle.kts`:
  - remove `alias(libs.plugins.androidgitversion)` and the `androidGitVersion {}` block;
  - add `apply(from = layout.settingsDirectory.file("git-version.gradle.kts").asFile)`;
  - after that, add `@Suppress("UNCHECKED_CAST") val gitVersion = extra["gitVersion"] as Provider<List<String>>`;
  - set `versionCode = maxOf(gitVersion.get()[1].toInt(), 1)` and `versionName = gitVersion.get()[0]`.

  Delete line 80 of `libs.versions.toml`. Run `cd android && ./gradlew -q :app:androidGitVersion | diff - $SCRATCH/plugin-out.txt`. Expected: no difference. Re-run the harness. Expected: all `PASS`.
- [ ] **Step 7:** Commit with `build(android): derive the app version without the androidgitversion plugin`.

### Task 3: Rust build typed tasks (tasks 3.1–3.6; design D2, D4)

**Files:** Modify `rust/build.gradle.kts`; create `rust/gradle.properties`.

**Interfaces:**
- Produces:
  - **`object RustBuildHelpers`**, a script-nested `object` that references nothing else in the script. Top-level `fun`s in a `.kts` file are members of the script class, so calling them from a task class, a `map {}` lambda or a `doNotCacheIf` lambda captures the script, which is the CC problem being fixed. Every helper therefore lives here and takes plain values:
    - `fun pinnedVersion(toml: String, name: String, file: String): String`;
    - `fun resolveNdkDirectory(lookup: NdkLookup): File`, keeping the existing lookup order and error message;
    - `fun requireNdkVersion(ndkDir: File, expected: String)`;
    - `fun requireOnPath(path: String, name: String, suffix: String, versionHint: String, installHint: String)`;
    - `fun registryRemapRustFlags(cargoHome: String?, userHome: String): String`;
    - `fun cacheRefusal(env: Map<String, String>, cargoHome: String?, userHome: String, rustDir: File): String?`, which returns the reason or null (denylist plus cargo config files, spec *Ambient toolchain overrides*).
  - **`data class NdkLookup(val env: Map<String, String>, val rootLocalProperties: String?, val androidLocalProperties: String?, val rootDir: File, val ndkVersion: String) : java.io.Serializable`**: the environment keys `ANDROID_NDK_HOME`, `ANDROID_SDK_ROOT` and `ANDROID_HOME`; the `local.properties` contents (via `providers.fileContents(...).asText`, wired into the task's `Property<NdkLookup>` with `map`/`zip`, so it is read only when the property is resolved inside the action), parsed in the helper.
  - **`abstract class ToolchainCheckTask : DefaultTask`** (`@DisableCachingByDefault(because = "probes the host toolchain")`):
    - `@Input requiredCommands: ListProperty<String>`, `@Input versionCommand: ListProperty<String>`, `@Input expectedPrefix: Property<String>`;
    - `@Input pathEnv: Property<String>`, `@Input versionHint: Property<String>`, `@Input installHint: Property<String>`;
    - `@Internal workingDir: DirectoryProperty`, `@InputFile @Optional toolchainFile: RegularFileProperty`;
    - an injected `ExecOperations`.
  - **`abstract class CargoInstallTask : DefaultTask`** (`@DisableCachingByDefault(because = "downloads crates from the network")`): `@Input crateName`, `@Input version`, `@InputFile toolchainFile`, `@OutputDirectory installRoot`, `@Internal targetDir`, `@Internal workingDir`.
  - **`abstract class VerifyToolVersionTask : DefaultTask`** (`@DisableCachingByDefault(because = "probes an installed tool")`): `@Input crateName`, `@Input version`, `@InputFile binary`, `@Input versionArguments: ListProperty<String>`.
  - **`abstract class CargoExecTask : Exec`** (`@DisableCachingByDefault(because = "verification or test-only build")`):
    - `@Internal ndkLookup: Property<NdkLookup>` (optional); when present, `exec()` resolves and checks the NDK and sets `ANDROID_NDK_HOME`;
    - `@Internal prependPath: ListProperty<String>`;
    - `@Internal extraEnv: MapProperty<String, String>`;
    - `@get:Inject abstract val fs: FileSystemOperations`, with `@Internal deleteBefore: ConfigurableFileCollection`.

    It overrides `@TaskAction exec()`, applies these, then calls `super.exec()`.
  - **`@CacheableTask abstract class CargoBuildTask : DefaultTask`**:
    - `@Input preArguments`, `@Input postArguments: ListProperty<String>`, `@Input outputFlag: Property<String>` (`--output-dir` or `--out-dir`);
    - `@Internal executable: Property<String>`, `@Internal workingDir`, `@Internal targetDir`, `@Internal remapRustFlags: Property<String>`, `@Internal prependPath: ListProperty<String>`, `@Internal ndkLookup: Property<NdkLookup>` (JNI only), `@Internal inheritedEnv: MapProperty<String, String>` (from `providers.environmentVariablesPrefixedBy("")`, used only by the refusal predicate);
    - `@Internal copyFrom: RegularFileProperty` (optional; `rustHostJni`'s `release/<lib>` in its own target directory, copied into `outputDir` after the process);
    - injected `ExecOperations` and `FileSystemOperations`;
    - `@Input panicStrategy: Property<String>`;
    - `@InputFiles @PathSensitive(RELATIVE) sources: ConfigurableFileCollection` (one `fileTree(rust/)`);
    - `@InputFile @PathSensitive(RELATIVE) @Optional toolsFile`;
    - `@Input rustcVersion: Property<String>`, `@Input @Optional ndkVersion`, `@Input @Optional hostIdentity: Property<String>`. `hostIdentity` is `"${os.name}|${os.arch}|${ccLine}"` off Windows and the constant `"windows"` on Windows;
    - `@OutputDirectory outputDir`;
    - an action that:
      1. deletes `outputDir`;
      2. resolves and checks the NDK when `ndkLookup` is set;
      3. builds the environment (`PATH` prepended; `CARGO_TARGET_DIR`; `CARGO_PROFILE_RELEASE_PANIC` = `panicStrategy`; `RUSTFLAGS` = `remapRustFlags`; `ANDROID_NDK_HOME`);
      4. runs `executable + preArguments + (if outputFlag non-empty: [outputFlag, outputDir]) + postArguments`;
      5. performs the `copyFrom` copy;
    - `outputs.doNotCacheIf` predicates in the registration:
      - `RustBuildHelpers.cacheRefusal(...) != null`;
      - for `rustHostJni`, `os.name` starting with `Windows`, from a `providers.systemProperty` value captured as a plain `Boolean`.
    - **Capture rule for every registration in Tasks 3–4:** an execution-time lambda (`doNotCacheIf`, `doFirst`/`doLast`, `map {}` evaluated at execution time) reads only locals declared inside its `register {}` block or the task's own properties. It never names a script-level `val` (`rustDir`, `ndkVersion`, `windowsExecutableSuffix`, `repoRoot`, `buildRoot`, …), because that captures the script. Copy the value into a block-local `val` first.
  - **ValueSources:** `RustcVersionValueSource` (`rustc -vV` in `rust/`) and `HostLinkerValueSource` (first line of `cc --version`, only wired off Windows).
- Consumes: nothing.

- [ ] **Step 1:** Add the helper functions and `ToolchainCheckTask`. Port `rustToolchainCheck`, with pins and `ndk-version` read via `providers.fileContents(rustDir.file(...)).asText`.
- [ ] **Step 2:** Run `cd rust && ../gradlew --configuration-cache rustToolchainCheck` twice. Expected: no problem, and the second run says `Reusing configuration cache.`
- [ ] **Step 3:** Add `CargoInstallTask` and `VerifyToolVersionTask`. Port `registerCargoTool` (all three tools) and `rustWasmBindgenConsistency` (`Cargo.lock` as `@InputFile`, read in the action). Run `cd rust && ../gradlew --configuration-cache rustInstall` twice. Expected: no problem and reuse.
- [ ] **Step 4:** Add `CargoExecTask` and port `rustWasmLockCheck`, `rustNativeTest`, `rustWasmSmoke`, `rustWasmPairingTest`, the `rustWasm*Test` loop, `rustWasmConformance`, `rustWasmConformanceTest`, `rustJniConformance`, `rustFormat` and `rustLintHost`, `rustLintHostJni`, `rustLintWasm` and `rustLintAndroid`. Run `cd rust && ../gradlew --configuration-cache rustFormat rustLint rustTest` twice. Expected: pass, no problem, reuse.
- [ ] **Step 5:** Add `CargoBuildTask` and the two ValueSources, and port `rustHostJni`, `rustJni` and `rustWasm`:
  - `rustJni`: `pre = ["ndk","--platform","33","--target","arm64-v8a","--target","armeabi-v7a","--target","x86_64"]`, `post = ["build","--workspace","--release","--locked"]`, `outputFlag = "--output-dir"`;
  - `rustWasm`: `pre = ["build","veles-crypto","--target","web","--release","--mode","no-install","--no-opt"]`, `post = ["--","--locked"]`, `outputFlag = "--out-dir"`;
  - `rustHostJni`: keep the copy into `build/rust/host-jni`; `outputFlag` is unused, so allow it empty.

  Mark `rustPackage`, `rustNdkVersion` and `rustNdkDir` per D5. Create `rust/gradle.properties` (the root `org.gradle.jvmargs` line and the two CC lines).
- [ ] **Step 6:** Run from `rust/`: `env -u ANDROID_NDK_HOME -u ANDROID_SDK_ROOT ANDROID_HOME=/nonexistent ../gradlew help`, with both `local.properties` files temporarily moved aside; expected to succeed. Then run `../gradlew rustHostJni rustWasm rustJni rustPackage` twice; expected: pass and reuse. Run `rust/scripts/test-ndk-lookup.sh`; expected: pass, after copying any new configuration-time file into its scratch repo.
- [ ] **Step 7:** Commit with `build(rust): make the Rust Gradle build configuration-cache safe`.

### Task 4: Root build typed tasks (tasks 4.1–4.6)

**Files:** Modify `build.gradle.kts`, `.githooks/install-hooks.gradle.kts`, `gradle.properties`.

**Interfaces:**
- Consumes: the Task 3 class shapes. They are re-declared in `build.gradle.kts`, because classes do not cross script files; the root needs only `ToolchainCheckTask`, `CargoInstallTask`, `VerifyToolVersionTask` and `CargoExecTask`.
- Produces: the opt-out list in `evidence/cc-optouts.md`, used by Task 9.

- [ ] **Step 1:** Make `pinnedVerificationToolVersion` and `pinnedReleaseTool` provider-based. Port `registerVerificationCargoTool`. Add `@DisableCachingByDefault(because = "downloads a release archive")` to `InstallGitCliffTask`.
- [ ] **Step 2:** Port `protocolNodeCheck`, `bridgeNodeCheck`, `bridgeRustToolchainCheck`, `bridgeHostToolchainCheck` and `bridgeInstallerToolchainCheck` to `ToolchainCheckTask`. Port `sessionLoopbackCheck`, with the classpath file as `@InputFile`, read in the action.
- [ ] **Step 3:** Port `bridgeHostBuild` and `bridgeInstallerFormat`, `Lint`, `Test`, `ScriptsTest`, `ToolObservation` and `ToolCheck`, plus any remaining unguarded problem from `evidence/cc-problems-before.md`.
- [ ] **Step 4:** Add `notCompatibleWithConfigurationCache("release/PR packaging pipeline; always run with --no-configuration-cache, see docs/reproducible-builds.md")` and `outputs.doNotCacheIf("release packaging") { true }` to `bridgeBuild`, `bridgeBundle`, `bridgeManifests`, `bridgePackage`, `bridgeInstallerSymbols` and every task `registerInstallerProducer` registers. Write the list to `evidence/cc-optouts.md`.
- [ ] **Step 5:** Mark the remaining ad-hoc root tasks per D5, and `InstallGitHooksTask` (`@DisableCachingByDefault(because = "writes local git config")`). Add the two CC lines to `gradle.properties`.
- [ ] **Step 6:** Run `./gradlew rustFormat rustLint rustTest scriptsTest protocolCheck` twice. Expected: pass, no problem, `Reusing configuration cache.` on the second run.
- [ ] **Step 7: Strictness check.** Temporarily add `tasks.register("ccProbe") { doLast { println(windowsExecutableSuffix) } }`, where `windowsExecutableSuffix` is a script-level `val`, so the action captures the script. Run `./gradlew ccProbe`. Expected: the build FAILS with a configuration-cache problem naming `:ccProbe`. Remove the task, and paste the output into `evidence/cc-reuse.md`.
- [ ] **Step 8:** Commit with `build(gradle): make the root build configuration-cache safe`.

### Task 5: Android build CC-safe and cacheability audit (tasks 5.1–5.4)

**Files:**
- Modify: `android/build.gradle.kts:50`, `android/app/build.gradle.kts:61-68,142-180,260-275`, `android/gradle.properties`
- Create: `evidence/cacheability.md`

- [ ] **Step 1:** `android/build.gradle.kts`: read `ndk-version` via `providers.fileContents(...).asText.get().trim()`; a configuration-time value is required here. Publish `protocolV1Dir`/`protocolConformanceDir` from `layout.settingsDirectory` (no `File` arithmetic through `rootProject` at execution time). `android/app/build.gradle.kts`:
  - read `VELES_KEYSTORE_*` with `providers.environmentVariable(...).orNull`;
  - port `sessionLoopbackClasspath` and `releaseHookGuard` so their actions capture only properties and providers, never `project`, each with a D5 reason.
- [ ] **Step 2:** Add the two CC lines to `android/gradle.properties`.
- [ ] **Step 3:** From `android/`, run each of `assembleDebug`, `testDebugUnitTest`, `spotlessCheck detekt lintDebug`, `:app:releaseHookGuard`, `rustNdkVersion` and `:app:protocolJvmTest` twice. Run `connectedDebugAndroidTest` once on the emulator opened with the device tools; expected: no configuration-cache problem. Expected: no problem, and `Reusing configuration cache.` on the second run. Append the results to `evidence/cc-reuse.md`. Fix any `.githooks` problem here.
- [ ] **Step 4: Audit.** Run `grep -nE 'tasks\.(register|named)|abstract class \w+Task' build.gradle.kts rust/build.gradle.kts android/build.gradle.kts android/app/build.gradle.kts android/git-version.gradle.kts .githooks/install-hooks.gradle.kts`. Write `evidence/cacheability.md` as a table with columns task | mechanism | reason, expanding the generated names of `registerCargoTool`, `registerVerificationCargoTool` and `registerInstallerProducer` (`./gradlew tasks --all` lists them). Expected: every row is cacheable (only the three), a reason, or `Sync`/`Copy`/`Delete`.
- [ ] **Step 5:** Commit with `build(android): make the Android build configuration-cache safe`.

### Task 6: Release guard and contract test (tasks 6.1–6.3)

**Files:** Modify `verify/test/workflow-contracts.test.mjs`, the eight workflows, `verify/generate-sboms.sh`.

**Interfaces:**
- Produces:
  - `const GUARDED = ['build-android.yml','build-rust.yml','build-web-extension.yml','build-native-windows.yml','build-native-macos.yml','build-supply-chain.yml']`;
  - `const GUARDED_STEPS = { 'release-build.yml': /installGitCliff/, 'publish-release.yml': /installGitCliff/ }`;
  - `function gradleCommands(text: string): string[]`;
  - `function assertCacheGuard(name: string, commands: string[])`.

- [ ] **Step 1: Write the failing test.** Add `test('release and verification Gradle invocations bypass both caches', …)`.
  - For each guarded file, take `runCommands(step.join('\n'))` over `steps(...)`, plus the text of `verify/generate-sboms.sh`.
  - `gradleCommands` matches `/(?:\.\/|\.\\|\$root\/|"\$root"\/)gradlew(?:\.bat)?\b/`, and ends each command at an unquoted `|`, `)`, `;`, `&&`, `||` or a non-continued newline. Quote state starts fresh at the `gradlew` match, and a `"` immediately after the match closes the quote that opened before it, as in `"$root/gradlew"`.
  - `assertCacheGuard` runs `assert.match(cmd, /--no-build-cache\b/, \`${name}: ${cmd} must pass --no-build-cache\`)` and the same for `--no-configuration-cache`.
  - `const rust = await workflow('build-rust.yml')`; commands are `.trim()`ed.
  - Mutation checks: `assert.throws(() => assertCacheGuard('build-rust.yml', gradleCommands(rust.replace('rustPackage --no-build-cache', 'rustPackage'))), /build-rust\.yml/)` and `assert.throws(() => assertCacheGuard('x', gradleCommands('NDK_VERSION="$(./gradlew -q rustNdkVersion)"')), /--no-build-cache/)`.
  - `assert.ok(gradleCommands('X="$(./gradlew -q rustNdkVersion | tail -1)"')[0] === './gradlew -q rustNdkVersion')`.
- [ ] **Step 2:** Run `node --test verify/test/workflow-contracts.test.mjs`. Expected: FAIL, naming `build-android.yml` and `./gradlew -q rustNdkVersion`.
- [ ] **Step 3:** Append ` --no-build-cache --no-configuration-cache` after the task names of every guarded invocation, as in design D6 and tasks.md 6.1. In `verify/generate-sboms.sh:20`, apply it to `"$root/gradlew" verifyCargoCyclonedx`.
- [ ] **Step 4:** Run `node --test verify/test/workflow-contracts.test.mjs`. Expected: the new test passes. Fix the existing assertions it breaks, such as the line-404 replacement string on `build-web-extension.yml`, until the whole suite passes. Run `python3 -I native-bridge/installer/scripts/test_build_contract.py`. Expected: pass, or fixed.
- [ ] **Step 5:** Commit with `ci(release): run release and verification builds without Gradle caches`.

### Task 7: Cache correctness experiments (tasks 7.1–7.6)

**Files:** Create `evidence/cache-experiments.md`.

For each experiment, set `export GRADLE_USER_HOME=$SCRATCH/guh-<n>`, seeded with `mkdir -p $GRADLE_USER_HOME/caches && cp -r ~/.gradle/wrapper $GRADLE_USER_HOME/ && cp -r ~/.gradle/caches/modules-2 $GRADLE_USER_HOME/caches/`; `caches/build-cache-1` starts absent, so it is empty. Store, load and "no load and no store" are the observations defined in tasks.md group 7.

- [ ] **Step 1: Input change.** Run `./gradlew rustJni rustWasm rustHostJni --build-cache --info` and expect `Stored cache entry` ×3. Bump `cargo-ndk` in `rust/toolchain-tools.toml` to the next published version, rerun, and expect `rustJni` and `rustWasm` executed (no `FROM-CACHE`). Revert, rerun, and expect `FROM-CACHE`. Then `./gradlew rustInstall`. Repeat with a `rust/rust-toolchain.toml` comment edit for `rustHostJni`; expect it executed.
- [ ] **Step 2: Configuration-time invalidation.** Run `cd android && ./gradlew assembleDebug`. Then append a trailing space to `rust/ndk-version`: the trimmed value is unchanged, but the file content changes. Run it again. Expect a `Calculating task graph as configuration cache cannot be reused because file '…ndk-version' has changed` line (match on "configuration cache cannot be reused"). Revert.
  - Lazy pin: run `./gradlew rustInstall`, bump the `cargo-ndk` pin to the next published version, run `./gradlew rustInstall` again (expect `installCargoNdk` executed and `build/rust-tools/cargo-ndk/bin/cargo-ndk --version` reporting the new version), then revert and run `./gradlew rustInstall` once more.
- [ ] **Step 3: Relocation.** Commit the work so far. Create `git worktree add $SCRATCH/relA HEAD` and `$SCRATCH/relB HEAD`. In A, run `./gradlew rustJni rustWasm rustHostJni --build-cache`. In B (another path, same `GRADLE_USER_HOME`), run the same; expect `FROM-CACHE` ×3. Copy `build/rust/jniLibs`, `build/rust/host-jni` and `web-extension/rust-wasm/pkg` aside, then run `./gradlew rustJni rustWasm rustHostJni --no-build-cache --rerun-tasks` in B. Compare with `diff -r` and with `find <dir> -type f | sort` lists, `.gitignore` included. Expect no difference. On a difference, add the `doNotCacheIf` for that task, commit it as `build(rust): stop caching <task> (<reason>)`, and record it.
- [ ] **Step 4: Refusal.**
  - Run `printf '#!/bin/sh\nexec "$@"\n' > $SCRATCH/wrap && chmod +x $SCRATCH/wrap`, then `RUSTC_WRAPPER=$SCRATCH/wrap ./gradlew rustHostJni --build-cache --info`. Expect `Caching disabled for task ':rust:rustHostJni' because:` with the denylist reason, and no store.
  - Use a temporary `CARGO_HOME` with a copied registry and an empty `config.toml`, and run `./gradlew rustJni --build-cache --info`. Expect the same message with the cargo-config reason.
  - Run `./gradlew rustToolchainCheck --build-cache` twice. Expect no `FROM-CACHE`.
  - Run `./gradlew clean installGitCliff --build-cache`. Expect `installGitCliff` executed.
- [ ] **Step 5: Default off.** Run `./gradlew rustJni --info` and expect no `Stored cache entry` and no `FROM-CACHE`. Write `org.gradle.caching=true` into `$GRADLE_USER_HOME/gradle.properties`, run `./gradlew rustPackage --no-build-cache --no-configuration-cache --info`, and expect the same.
- [ ] **Step 6: Git state.** In a throwaway clone (detached), run `cd android && ./gradlew -q :app:androidGitVersion` three times: before the tag, after `git tag android-v9.9.9` (expect `9.9.9` and `90909`), and after `git commit --allow-empty -m x` (expect the harness's `after-tag` form). Expect `Reusing configuration cache` never to show a stale value. Record everything in `evidence/cache-experiments.md`.
- [ ] **Step 7:** Commit with `docs(openspec): record gradle cache experiments`.

### Task 8: CC coverage and cross-platform probes (tasks 8.1–8.3)

- [ ] **Step 1:** Run every invocation listed in tasks.md 8.1 twice, the second without `-q`. Expected: no problem and reuse. For the `check-android-layout.sh:128` fresh copy, expect no problem only. Append to `evidence/cc-reuse.md`.
- [ ] **Step 2:** Run `./gradlew assembleRelease rustPackage rustWasm installCargoDeny installGitCliff` (CC on, no guard). Expected: no problem.
- [ ] **Step 3:** Run `git bundle create $SCRATCH/branch.bundle origin/master..HEAD`. Through the `homepc` skill, copy and fetch the bundle on the Windows PC and the MacBook. Run twice each: `.\gradlew.bat bridgeHostToolchainCheck bridgeHostFormat bridgeHostLint bridgeHostTest`, `.\gradlew.bat bridgeHostBuild` and `.\gradlew.bat bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest` (Windows), and the `./gradlew` equivalents (macOS). Expected: no problem and reuse. Fix problems in Task 4's files, or add them to `evidence/cc-optouts.md`.
- [ ] **Step 4:** If code changed, commit with `build(gradle): fix configuration-cache problems found on Windows and macOS`.

### Task 9: Docs (tasks 9.1–9.3)

**Files:** Modify `BUILD.md`, `CONTRIBUTING.md`, `docs/reproducible-builds.md`, `docs/releasing.md:46-47`, `docs/android.md:149`, `android/AGENTS.md:32`.

- [ ] **Step 1:** Add `## Build caches` to `BUILD.md`, with the content of tasks.md 9.1, stating the final cacheable set from `evidence/cache-experiments.md`. Add a one-line link to it from `CONTRIBUTING.md`'s setup section.
- [ ] **Step 2:** Add `### Gradle caches` to `docs/reproducible-builds.md` before *Clean contract*. Copy in the guarded file list (design D6), the flag rule (`--no-build-cache`, equivalent to `-Dorg.gradle.caching=false`, and `--no-configuration-cache`) and the opt-out list from `evidence/cc-optouts.md`, each with its reason.
- [ ] **Step 3:** Replace the plugin wording with "derived by `android/git-version.gradle.kts` (prefix `android-v`, code format `MMNNPP`)". In `docs/android.md`, add the harness command as the re-proof. Run `wc -l android/AGENTS.md` (expect ≤ 40) and the agent-file check from `docs/`.
- [ ] **Step 4:** Commit with `docs(build): document Gradle caches and the release guard`.

### Task 10: Equality, timings and real-environment runs (tasks 10.1–10.5)

- [ ] **Step 1:** Clone to `$SCRATCH/apk-after` at `HEAD`, detached, with `git tag android-v9.9.9`. Build as in Task 1 Step 4. Expected: the release APK SHA-256 and `SHA256SUMS` equal `evidence/apk.md`. Compare the debug SHA and record it; if it differs, record `aapt2 dump badging` version fields (expected equal) and `unzip -l` diff entries.
- [ ] **Step 2:** Run the timing matrix steps 2–3 (design D8) and append them to `evidence/timings.md`.
- [ ] **Step 3:** Run `git tag -l 'android-v9*'`; expected: empty. Use the device tools (`device_list`, `device_open`): install `android/app/build/outputs/apk/debug/app-debug.apk` and launch the app. Record `dumpsys package me.nagaev.veles | grep -E 'versionName|versionCode'` against `./gradlew -q :app:androidGitVersion`; they must be equal. Open the pairing-code screen, which uses the Rust JNI, and confirm the code renders.
- [ ] **Step 4:** Through `homepc` on the MacBook, build the extension from this branch (`./gradlew rustWasm`, then `cd web-extension && npm ci --ignore-scripts && npm run build`). Load it unpacked in Chrome, open the popup, and confirm no console error and that the WASM module initialises.
- [ ] **Step 5:** Pair the emulator app with the extension through the pairing flow. Post a test OTP with the app's test-notification action (`TestNotificationSender`, `docs/android.md` step 2), tapped through `agent-device`. Expected: the code appears in the extension. If this is unavailable, stop and ask the user.
- [ ] **Step 6:** Commit with `docs(openspec): record gradle cache equality and device runs`.

### Task 11: Gates (tasks 11.1–11.2)

- [ ] **Step 1:** Run `./gradlew scriptsTest`, `node --test verify/test/workflow-contracts.test.mjs`, `verify/verify-supply-chain.sh` and `openspec validate gradle-cache-safe`. Expected: all pass.
- [ ] **Step 2:** Run the checks of every node `python3 -I scripts/components.py changed --base origin/master` selects, per `.agents/hooked-rules/repo-pre-push-checks.md` and `.agents/rules/android-lint-before-push.md`. Expected: all pass.
- [ ] **Step 3:** Write the PR evidence summary, the 11.2 list, from `evidence/`, for finalize to put in the PR body together with `Refs raidenyn/veles-android#181`.
- [ ] **Step 4:** After finalize pushes, every CI job on the PR is green.
