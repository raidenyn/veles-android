## 1. Environment and baseline

- [ ] 1.1 Provision a user-level Temurin JDK 21.0.12 (the `.java-version`) outside the repository, for example under `~/.local/jdks/`. Use it through `JAVA_HOME` for every Android run in this change, and confirm `javac -version` and `cd android && ./gradlew --version`. (design D8)
- [ ] 1.2 **Tag safety rule for this whole change:** every run that creates an `android-v*` tag or a test commit happens in a throwaway `git clone --no-hardlinks` of the repository in the scratch directory, never in a worktree. Worktrees share refs with the main repository. Such a tag or commit is never pushed. Before 10.3 and before any push, `git tag -l 'android-v9*'` in the worktree must print nothing.
- [ ] 1.3 Baseline timings at the merge base (spec *Common entry points*; design D8 matrix step 1). In fresh worktree A (no tags created), run cold and record wall-clock times for `cd android && ./gradlew assembleDebug`, then `cd android && ./gradlew testDebugUnitTest`, then root `./gradlew rustTest`. Repeat in fresh worktree B, which shares only the Gradle user home. Save the results in `openspec/changes/gradle-cache-safe/evidence/timings.md`.
- [ ] 1.4 Baseline APKs at the merge base (spec *Release APK unchanged*). In a throwaway clone (1.2) checked out detached at the merge base, with local tag `android-v9.9.9` on `HEAD`, build `assembleRelease` (unsigned) and `assembleDebug` with `--no-build-cache`. Also build `rustPackage` there. Record the APK SHA-256 values, `build/rust-package/SHA256SUMS` and the `-q :app:androidGitVersion` output in `evidence/apk.md`.
- [ ] 1.5 Full CC probe at the merge base, on Linux. Run each inventory invocation that runs on Linux with `--configuration-cache --configuration-cache-problems=warn`. That is every `./gradlew` call in the workflows, `verify/*.sh` and `rust/scripts/*.sh`, except the `.\gradlew.bat` jobs and the macOS/Windows installer producers. Record every problem per task in `evidence/cc-problems-before.md`. This is the work list for groups 3–5. The Windows and macOS jobs are probed in 8.3.

## 2. Version derivation (spec component-versioning; design D3)

- [ ] 2.1 From the plugin jar in the Gradle cache (`javap -c -p`), read the rules of `com.gladed.androidgitversion` 0.4.14:
  - name format, hidden branches, dirty marking, no-tag fallback and code computation;
  - the exact lines and order of its `androidGitVersion` task;
  - whether it needs the Android plugin applied.

  Note them in `evidence/version-rules.md`.
- [ ] 2.2 Write `android/scripts/check-git-version-parity.sh` (android node; not a CI gate, because it needs the plugin portal). It creates throwaway git repositories in a temporary directory with two throwaway Gradle builds that use the repository wrapper: one applying the plugin 0.4.14 from the plugin portal (plus a minimal AGP setup only if 2.1 shows the plugin needs one), and one applying `android/git-version.gradle.kts`. For each state, it compares the full `-q androidGitVersion` output and exits non-zero on any difference. The states:
  - on a tag;
  - three commits after the tag;
  - dirty;
  - a non-`master` branch;
  - detached `HEAD`;
  - no tag;
  - two tags on one commit;
  - a pre-release-suffix tag, if 2.1 shows the plugin accepts one.
- [ ] 2.3 Write `android/git-version.gradle.kts`:
  - `GitVersionValueSource` (`ValueSource<List<String>, …>` with injected `ExecOperations`; parameters: repository directory, prefix, code format);
  - the provider exposed as an `extra` property;
  - an `androidGitVersion` task with `@DisableCachingByDefault(because = "reads git state")` that prints the plugin's exact lines through `logger.quiet`/`println`.
- [ ] 2.4 Run the harness until every state matches. Record its output in `evidence/version-parity.md`.
- [ ] 2.5 Switch `android/app/build.gradle.kts` to the applied script (`versionCode = maxOf(code, 1)`, `versionName`). Remove the plugin from `plugins {}` and from `android/gradle/libs.versions.toml`. Re-run the harness and `cd android && ./gradlew -q :app:androidGitVersion`.

## 3. Rust build: CC-safe typed tasks (spec gradle-build-caching; design D2, D4)

- [ ] 3.1 Providers and helpers in `rust/build.gradle.kts`:
  - replace the `readText` reads of the pins and of `ndk-version` with `providers.fileContents(…).asText.map {…}`, wired lazily (except `ndkVersion` where a value is needed at configuration time);
  - turn the NDK lookup, `requireOnPath`, `requireNdkVersion` and the registry remap into functions of plain parameters, fed from `providers.environmentVariable`/`systemProperty` and a `local.properties` contents provider;
  - add `ToolchainCheckTask` (`@DisableCachingByDefault`) and port `rustToolchainCheck`.

  Keep every error message and the NDK lookup order.
- [ ] 3.2 Add `CargoInstallTask` and `VerifyToolVersionTask` (`@DisableCachingByDefault`: network, host probe) and port the three `registerCargoTool` pairs and `rustWasmBindgenConsistency` (`Cargo.lock` as `@InputFile`, read in the action).
- [ ] 3.3 Add `CargoExecTask` (an `Exec` subclass with `@DisableCachingByDefault`: verification only, or a conformance build, with its environment resolved from providers in the action) and port these: `rustWasmLockCheck`, `rustNativeTest`, `rustWasmSmoke`, `rustWasmPairingTest` and the `rustWasm*Test` loop, `rustWasmConformance(Test)`, `rustJniConformance`, `rustFormat` and `rustLintHost`/`HostJni`/`Wasm`/`Android`.
- [ ] 3.4 Add `CargoBuildTask` (`@CacheableTask`, a `DefaultTask` using `ExecOperations`) and port `rustHostJni`, `rustJni` and `rustWasm` to it:
  - `@Input preArguments`/`postArguments`, path-free; the action inserts the absolute `--output-dir` (cargo-ndk, before `build`) or `--out-dir` (wasm-pack, before `--`) between them;
  - `@OutputDirectory`;
  - `@Internal` binary, working directory, `CARGO_TARGET_DIR` and the remap `RUSTFLAGS`. The action always sets `RUSTFLAGS` and `CARGO_PROFILE_RELEASE_PANIC` itself, and the latter is also an `@Input`;
  - one `fileTree(rust/)` input with `PathSensitivity.RELATIVE`;
  - `toolchain-tools.toml` (JNI and WASM);
  - a `ValueSource` for `rustc -vV`;
  - for `rustHostJni`, a `cc --version` `ValueSource` wired only when not on Windows (on Windows the input is a constant and caching is disabled), plus the OS name and architecture;
  - the NDK version (JNI);
  - `doNotCacheIf` for the inherited-environment denylist and the cargo config files.
- [ ] 3.5 Mark `rustPackage` (a `Sync`, never cached) and the ad-hoc `rustNdkVersion`/`rustNdkDir` non-cacheable per D5. Create `rust/gradle.properties` with `org.gradle.jvmargs` (copied from the root file), `org.gradle.configuration-cache=true` and `org.gradle.configuration-cache.problems=fail`.
- [ ] 3.6 Rust-only regression checks, run **from `rust/`** (the root is not fixed yet):
  - `../gradlew help`, `../gradlew rustFormat rustLint rustTest rustInstall rustHostJni rustWasm rustJni rustPackage`, twice each with CC on, with "Reusing configuration cache" on the second run;
  - `rust/scripts/test-ndk-lookup.sh` (copy any new configuration-time file into its scratch repo).

## 4. Root build: CC-safe typed tasks (spec gradle-build-caching; design D2, D5)

- [ ] 4.1 Replace the `pinnedVerificationToolVersion` and `pinnedReleaseTool` reads with `providers.fileContents`. Move the `requireOnPath`/`cargoRegistryRemapRustFlags` callers to plain-parameter functions. Port `registerVerificationCargoTool` to the install/verify task shapes, and give `InstallGitCliffTask` its `@DisableCachingByDefault(because = "network download")`.
- [ ] 4.2 Port the toolchain and Node checks to `ToolchainCheckTask`: `protocolNodeCheck`, `bridgeNodeCheck`, `bridgeRustToolchainCheck`, `bridgeHostToolchainCheck` and `bridgeInstallerToolchainCheck`. Port `sessionLoopbackCheck` (its classpath file as `@InputFile`, read in the action).
- [ ] 4.3 Port `bridgeHostBuild` and the check tasks of `check-installer.yml` (`bridgeInstallerFormat`, `Lint`, `Test`, `ScriptsTest`, `ToolObservation`, `ToolCheck`) to CC-safe typed or `CargoExecTask`-style tasks with `@DisableCachingByDefault` reasons, keeping messages and behaviour. Port any other `doFirst`/`doLast` the 1.5 probe reports in unguarded graphs.
- [ ] 4.4 Give the installer producer pipeline `notCompatibleWithConfigurationCache("release/PR packaging pipeline; always run with --no-configuration-cache, see docs/reproducible-builds.md")` and `doNotCacheIf`. The pipeline is the `registerInstallerProducer` tasks, `bridgeBuild`, `bridgeBundle`, `bridgeManifests`, `bridgePackage` and `bridgeInstallerSymbols`. Keep a list of the opted-out tasks for 7.2.
- [ ] 4.5 Mark every remaining ad-hoc root task non-cacheable per D5 (aliases, `scriptsTest`, `protocol*`, `bridge*`). Give the `.githooks/install-hooks.gradle.kts` `InstallGitHooksTask` its `@DisableCachingByDefault` reason. Set `org.gradle.configuration-cache=true` and `org.gradle.configuration-cache.problems=fail` in `gradle.properties`.
- [ ] 4.6 Strictness check (spec scenario *A problem fails the build*): temporarily add a task whose `doLast` calls a script-level function, run it from the root, and confirm the build fails with a configuration-cache problem. Remove the task and record the output in `evidence/cc-reuse.md`.

## 5. Android build: CC-safe (spec gradle-build-caching; design D2)

- [ ] 5.1 In `android/build.gradle.kts`, read `rust/ndk-version` and the protocol paths through providers. In `android/app/build.gradle.kts`:
  - move `VELES_KEYSTORE_*` to `providers.environmentVariable`;
  - port `sessionLoopbackClasspath` and `releaseHookGuard` to CC-safe actions (no script references, no `project` at execution time), each with a `@DisableCachingByDefault`/`doNotCacheIf` reason;
  - if `.githooks/install-hooks.gradle.kts` reports a CC problem from `android/`, fix it the same way.
- [ ] 5.2 Set `org.gradle.configuration-cache=true` and `org.gradle.configuration-cache.problems=fail` in `android/gradle.properties`.
- [ ] 5.3 With CC on, run each of these twice from `android/`, confirming no problem and "Reusing configuration cache" on the second run: `assembleDebug`, `testDebugUnitTest`, `spotlessCheck detekt lintDebug`, `:app:releaseHookGuard`, `rustNdkVersion` (second call without `-q`) and `:app:protocolJvmTest`. Run `connectedDebugAndroidTest` once on the emulator (no problem only). Record the reuse lines in `evidence/cc-reuse.md`.
- [ ] 5.4 Cacheability audit: `grep -n 'tasks.register\|tasks.named\|abstract class .*Task'` over `build.gradle.kts`, `rust/build.gradle.kts`, `android/build.gradle.kts`, `android/app/build.gradle.kts`, `android/git-version.gradle.kts` and `.githooks/install-hooks.gradle.kts`. Every registered task must be one of: the three cacheable products, a task with a `@DisableCachingByDefault`/`doNotCacheIf` reason, or a never-cached type (`Sync`, `Copy`, `Delete`). Record the table in `evidence/cacheability.md`.

## 6. Release and verification guard (spec gradle-build-caching; design D6)

- [ ] 6.1 Append `--no-build-cache --no-configuration-cache` after the task names of every `./gradlew`/`.\gradlew.bat` call in these places:
  - `build-android.yml`, `build-rust.yml`, `build-web-extension.yml`, `build-native-windows.yml`, `build-native-macos.yml` and `build-supply-chain.yml`, including the `$(./gradlew -q rustNdkVersion)` substitutions;
  - the `installGitCliff` steps of `release-build.yml` and `publish-release.yml`;
  - `verify/generate-sboms.sh`.
- [ ] 6.2 Add the guard test to `verify/test/workflow-contracts.test.mjs`:
  - **Scanner:** reuse `steps()`/`runCommands()`. Match `(?:\./|\.\\|\$root/|"\$root"/)gradlew(?:\.bat)?\b`, including inside `$(…)`. A command ends at an unquoted `|`, `)`, `;`, `&&`, `||` or a line end without a continuation backslash.
  - **Scope:** the guarded workflows and `verify/generate-sboms.sh`.
  - **Failure message:** names the file and the command.
  - **Mutation checks:** remove a flag from the `rustPackage` step of `build-rust.yml`, and from a `$(…)` substitution. Each must fail.
- [ ] 6.3 Update the existing assertions the flags break, such as the exact-line replacement near line 404 on `build-web-extension.yml`, and any others the run shows. Run `node --test verify/test/workflow-contracts.test.mjs` and `python3 -I native-bridge/installer/scripts/test_build_contract.py`, and fix the latter if it breaks.

## 7. Cache correctness experiments (spec gradle-build-caching; design D4, D8)

How to observe the cache, used throughout this group:
- Each experiment uses a temporary `GRADLE_USER_HOME` (seeded by copying the shared one's `caches/modules-2` and `wrapper`), so the build cache in `caches/build-cache-1` starts empty and is private.
- "**Store**" means `--info` shows `Stored cache entry for task '<task>'` and the entry count in `caches/build-cache-1` grows.
- "**Load**" means the task reports `FROM-CACHE`.
- "**No load and no store**" means neither appears, and `--info` shows `Caching disabled for task '<task>' because: …`.

- [ ] 7.1 Input change. With `--build-cache`, build `rustJni rustWasm rustHostJni`. Change `rust/toolchain-tools.toml` by bumping the `cargo-ndk` pin to a real published version (its `install*` runs). Show that `rustJni` and `rustWasm` execute and do not report `FROM-CACHE`. Revert, and show `FROM-CACHE` again. Repeat with a `rust/rust-toolchain.toml` edit for `rustHostJni`. Record the results in `evidence/cache-experiments.md`.
- [ ] 7.2 Configuration-time invalidation: change `rust/ndk-version` between two `cd android && ./gradlew assembleDebug` runs, and show "Calculating task graph" on the second (the build may then fail). Lazily read pin: after the 7.1 pin change, show that `rustInstall` installs the new version. Revert both, then run `./gradlew rustInstall` again so `build/rust-tools/` holds the original pinned versions.
- [ ] 7.3 Relocation, for each of the three products:
  1. populate the cache from worktree A;
  2. run in worktree B at another path, and show `FROM-CACHE`;
  3. compare the restored output with a fresh `--no-build-cache` build of B, byte for byte, using a full file list including dotfiles (`web-extension/rust-wasm/pkg/.gitignore`).

  A failure makes that product non-cacheable (`doNotCacheIf`), and the failure is recorded.
- [ ] 7.4 Refusal rule:
  - `RUSTC_WRAPPER` pointing to a pass-through wrapper script (`#!/bin/sh` plus `exec "$@"`) gives no load and no store for `rustHostJni`;
  - a temporary `CARGO_HOME` holding a `config.toml` (registry content copied, so builds stay offline) gives no load and no store for `rustJni`;
  - `rustToolchainCheck --build-cache`, run twice, never reports `FROM-CACHE`;
  - `installGitCliff --build-cache` on a clean build directory executes.
- [ ] 7.5 Default off:
  - `./gradlew rustJni` without `--build-cache` and without a user setting gives no load and no store;
  - a guarded command (`./gradlew rustPackage --no-build-cache --no-configuration-cache`), with `org.gradle.caching=true` in the temporary `GRADLE_USER_HOME/gradle.properties`, gives no load and no store.
- [ ] 7.6 Git state under CC, in a throwaway clone (1.2) checked out detached: run `cd android && ./gradlew -q :app:androidGitVersion`, then add local tag `android-v9.9.9` (expect 9.9.9 / 90909), then add a new commit (expect the value the harness pins), each without `--rerun`.

## 8. Full CC coverage and cross-platform probes (spec *Common entry points*; design D6)

- [ ] 8.1 On Linux with CC on, run every unguarded inventory invocation twice (the second without `-q` where the first used it), and confirm no problem and the reuse on the second run. This covers:
  - from the root: `rustFormat rustLint rustTest`, `scriptsTest`, `protocolCheck`, `rustInstall`, `rustHostJni`, `rustWasm`, `assembleDebug`, `clean`, `rustNdkVersion`, `assembleDebug testDebugUnitTest rustHostJni rustJni`, and the `bridge*` check entry points (`bridgeFormat bridgeLint bridgeTypecheck bridgeRustFormat bridgeRustLint`, `bridgeNpmTest bridgeRustTest`, `bridgeHostToolchainCheck bridgeHostFormat bridgeHostLint bridgeHostTest`, `bridgeHostBuild`, `bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest`, `bridgeInstallerScriptsTest`);
  - the scripts `rust/scripts/test-jni-conformance.sh`, `rust/scripts/assert-clean.sh`, `verify/check-root-aliases.sh` (its scratch build is exempt) and `verify/check-android-layout.sh` (its fresh-copy build at line 128 is checked for no problem only).
- [ ] 8.2 With CC on in this container, probe the guarded graphs `assembleRelease`, `rustPackage`, `rustWasm`, `installCargoDeny` and `installGitCliff`, and show they are CC-clean, though CI runs them with CC off.
- [ ] 8.3 Through the `homepc` skill, sync the branch without pushing, using `git bundle create` of the branch copied over by `homepc`, or the skill's code sync. Then run each `check-native-host.yml` and `check-installer.yml` invocation twice with CC on, on the Windows PC with `.\gradlew.bat` and on the MacBook. Confirm no problem and the reuse. Fix any problem, or add a documented opt-out to the 4.4 list.

## 9. Docs (after groups 7–8, so they reflect what is actually cached; spec *Developers can opt in*; design D6, D7)

- [ ] 9.1 Add a "Build caches" section to `BUILD.md`, covering:
  - CC is on by default;
  - the `--build-cache` and `~/.gradle/gradle.properties` `org.gradle.caching=true` opt-in;
  - what is cached, after any 7.3 fallback;
  - the Rust refusal conditions;
  - CI and release builds are unaffected;
  - `--no-configuration-cache` for a local signed `assembleRelease`.

  Link it from `CONTRIBUTING.md`.
- [ ] 9.2 Add a "Gradle caches" section to `docs/reproducible-builds.md`. It states that release and verification builds run with `--no-build-cache` (equivalent to `-Dorg.gradle.caching=false`) and `--no-configuration-cache`, names the guarded files and the opted-out installer tasks (the final 4.4/8.3 list, with the reason), and explains why.
- [ ] 9.3 Replace the plugin references in `docs/releasing.md`, `docs/android.md` (also describing the parity harness as the re-proof) and `android/AGENTS.md`. Keep `android/AGENTS.md` within its 40-line cap, and run the agent-file check from `docs/`.

## 10. Equality, timings and real-environment runs (design D8; repo real-device rule)

- [ ] 10.1 Rebuild the 1.4 artifacts in a throwaway clone (1.2) at the change's final commit, with the same tag and `--no-build-cache`.
  - The unsigned release APK SHA-256 must equal the baseline, and `rustPackage`'s `SHA256SUMS` must equal the baseline.
  - Compare the debug APK SHA-256 and report the result. If it differs, show with `aapt2 dump badging` that the version fields are equal, and name the differing entries.
- [ ] 10.2 Repeat the timing matrix (design D8 steps 2 and 3) and add the results to `evidence/timings.md`: defaults in fresh worktrees plus a warm rerun, then `--build-cache` with worktree A populating and fresh worktree B reusing.
- [ ] 10.3 Emulator, through the device tools, after `git tag -l 'android-v9*'` in the worktree prints nothing: install the change's debug APK and launch it. Record `dumpsys package me.nagaev.veles` `versionName`/`versionCode` against `:app:androidGitVersion`. Then run an in-app action that calls into the native library, for example showing the pairing code screen, which uses the Rust JNI crypto, and confirm it works.
- [ ] 10.4 Chrome, through the `homepc` skill on the MacBook: from this change's `rustWasm` output, build the extension (`cd web-extension && npm ci --ignore-scripts && npm run build`), load it unpacked, open its popup, and confirm the WASM module initialises with no console error.
- [ ] 10.5 End to end: pair the emulator app with the loaded extension through the existing pairing flow, and confirm a test OTP reaches the extension. If that setup is unavailable, say so and ask the user before reporting the change as tested.

## 11. Gates

- [ ] 11.1 Run `./gradlew scriptsTest`, `node --test verify/test/workflow-contracts.test.mjs`, `verify/verify-supply-chain.sh` and `openspec validate gradle-cache-safe`, plus the checks of every node `python3 -I scripts/components.py changed` selects (`.agents/hooked-rules/repo-pre-push-checks.md`, `.agents/rules/android-lint-before-push.md`). Each must pass. After the push, every CI job on the PR must be green.
- [ ] 11.2 Write the PR evidence summary from `evidence/`. It covers:
  - timings before and after;
  - CC reuse and the strictness check;
  - the input-change, relocation, refusal, default-off and git-state experiments;
  - version parity;
  - APK and `rustPackage` checksums;
  - the emulator, Chrome and end-to-end runs (what ran and on what);
  - the cacheability table.
