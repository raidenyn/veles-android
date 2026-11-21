# Cache correctness experiments

Task 7 (tasks 7.1–7.6; spec *gradle-build-caching*, design D4 and D8). Every observation below is a
real run on 2026-11-20 in the development container: Linux 7.0.0-42-generic, 6 CPUs, 15 GiB RAM,
JDK Zulu 21.0.12.1 (`/home/dev/.jdk/zulu21`), Gradle 9.5.0 through the repository wrapper, Android
SDK/NDK from `ANDROID_SDK_ROOT`/`ANDROID_HOME` (`/home/dev/.android-sdk`, NDK 29.0.14206865).

Worktree: `/home/dev/.t3/worktrees/veles-android/t3-699e4a45-1711ce8ea1e8baf4`, branch
`chore/gradle-cache-safe`, HEAD `44fb7a33` plus the experiment's evidence and the `rustWasm`
fallback. Scratch directory: `/tmp/opencode/t7` for 7.1–7.2 and `/home/dev/.cache/gca-task7` for
7.3–7.6 (the first 7.3 attempt ran under `/tmp` and is described there).

## Method

Each experiment gets its own Gradle user home, seeded once and never shared with another
experiment, so `caches/build-cache-1` starts absent (empty) and no other experiment's entries can
be mistaken for a hit:

```sh
export JAVA_HOME=/home/dev/.jdk/zulu21
export PATH="$JAVA_HOME/bin:$PATH"
export ANDROID_SDK_ROOT=/home/dev/.android-sdk ANDROID_HOME=/home/dev/.android-sdk
export SCRATCH=/tmp/opencode/t7
export GRADLE_USER_HOME=$SCRATCH/guh-<n>
mkdir -p "$GRADLE_USER_HOME/caches"
cp -r ~/.gradle/wrapper "$GRADLE_USER_HOME/"
cp -r ~/.gradle/caches/modules-2 "$GRADLE_USER_HOME/caches/"
```

`~/.gradle/gradle.properties` does not exist, so the shared user home has no caching setting.
The observations are the three states defined in `tasks.md` group 7: **store** (`--info` prints
`Stored cache entry for task '…'`), **load** (the task line ends with `FROM-CACHE`), and **no load
and no store** (neither appears, and `--info` prints `Caching disabled for task '…' because: …`).

## 7.1 Input change

Cache user home `$SCRATCH/guh-1`, start empty (`caches/build-cache-1` absent).

```sh
cd /home/dev/.t3/worktrees/veles-android/t3-699e4a45-1711ce8ea1e8baf4
rm -rf build/rust/jniLibs build/rust/host-jni web-extension/rust-wasm/pkg   # force execution
./gradlew rustJni rustWasm rustHostJni --build-cache --info
```

The output directories were removed first only because the products already existed from earlier
work in this worktree; without that, the tasks are `UP-TO-DATE` and Gradle neither stores nor loads.
Tool installs under `build/rust-tools` were left in place. Observed:

```text
> Task :rust:rustHostJni
Stored cache entry for task ':rust:rustHostJni' with cache key 8aa85d054e5531bba4bbdfb7c76ad843
> Task :rust:rustJni
Stored cache entry for task ':rust:rustJni' with cache key e7045ce60392e030f833c630e24be1ce
> Task :rust:rustWasm
Stored cache entry for task ':rust:rustWasm' with cache key 53981485a506ce375b8f53650bbe0953
BUILD SUCCESSFUL in 2m 1s
```

All three products stored an entry; `caches/build-cache-1` then held 26 files (the three task
entries plus Kotlin DSL script-compilation entries).

The `cargo-ndk` pin was changed from `4.1.2` to `4.1.1` in `rust/toolchain-tools.toml`. `4.1.2` is the
latest published version of the crate (the plan said "the next published version"; there is none),
so the change uses a real earlier release. `4.1.1` is a real published version and the pin file
content changes, which is what the experiment needs.

```sh
sed -i 's/^cargo-ndk = "4.1.2"$/cargo-ndk = "4.1.1"/' rust/toolchain-tools.toml
./gradlew rustJni rustWasm rustHostJni --build-cache --info
```

Observed:

```text
> Task :rust:installCargoNdk
  Installing cargo-ndk v4.1.1
> Task :rust:rustHostJni UP-TO-DATE
> Task :rust:rustWasm
  Input property 'toolsFile' file …/rust/toolchain-tools.toml has changed.
Stored cache entry for task ':rust:rustWasm' with cache key 130de97368238cc8eb3e679e6d85d74c
> Task :rust:rustJni
  Input property 'toolsFile' file …/rust/toolchain-tools.toml has changed.
Stored cache entry for task ':rust:rustJni' with cache key 67ef84ceb8c32940c48de08da464308b
BUILD SUCCESSFUL in 18s
```

`rustJni` and `rustWasm` executed with new keys (they declare `toolchain-tools.toml` as
`toolsFile`); neither reported `FROM-CACHE`. `rustHostJni` does not declare that file, so its key
was unchanged and the task was `UP-TO-DATE` (Gradle prefers up-to-date over a cache load in the
same worktree).

The pin was reverted and the three outputs removed again, then the same command rerun:

```sh
git checkout -- rust/toolchain-tools.toml
rm -rf build/rust/jniLibs build/rust/host-jni web-extension/rust-wasm/pkg
./gradlew rustJni rustWasm rustHostJni --build-cache --info
```

```text
> Task :rust:rustWasm FROM-CACHE
> Task :rust:rustHostJni FROM-CACHE
  Installing cargo-ndk v4.1.2
> Task :rust:rustJni FROM-CACHE
BUILD SUCCESSFUL in 3s
```

All three loaded from the cache under their original keys, and `installCargoNdk` reinstalled the
reverted `4.1.2` tool (a tool install is not cacheable). `./gradlew rustInstall` then confirmed the
tree was back on the pinned tool set: `installCargoNdk`, `installWasmPack` and
`installWasmBindgenCli` were `UP-TO-DATE`, both verify tasks passed, and
`PATH=build/rust-tools/cargo-ndk/bin:$PATH cargo ndk --version` printed `cargo-ndk 4.1.2`. (The
brief's `build/rust-tools/cargo-ndk/bin/cargo-ndk --version` exits 1 with
`This binary may only be called via `cargo ndk`.`; cargo-ndk 4.1.2 only answers through the cargo
subcommand, so the version check is made that way.)

Finally, a comment line was appended to `rust/rust-toolchain.toml` and `rustHostJni` rebuilt:

```sh
printf '\n# cache input probe\n' >> rust/rust-toolchain.toml
./gradlew rustHostJni --build-cache --info
```

```text
> Task :rust:rustHostJni
  Input property 'sources' file …/rust/rust-toolchain.toml has changed.
Stored cache entry for task ':rust:rustHostJni' with cache key 2570678c8213971fcc1e7745612722e3
BUILD SUCCESSFUL in 2s
```

The file was reverted afterwards (`git checkout -- rust/rust-toolchain.toml`).

**Conclusion:** the three product keys cover the tool pins (`toolchain-tools.toml` for `rustJni`
and `rustWasm`) and the toolchain file (`rustHostJni`), and a change is a miss, not a stale hit.

## 7.2 Configuration-time invalidation and the lazy pin

Cache user home `$SCRATCH/guh-2`, start empty; project configuration cache under `android/.gradle`.

```sh
cd android
./gradlew assembleDebug                     # first run, no cached configuration
./gradlew assembleDebug                     # unchanged
```

```text
Calculating task graph as no cached configuration is available for tasks: assembleDebug
BUILD SUCCESSFUL in 4m 48s
Reusing configuration cache.
BUILD SUCCESSFUL in 3s
```

A trailing space was then appended to `rust/ndk-version` (`od -c` shows the original
`… 8 6 5 \n` followed by a space; its trimmed value, and therefore `ndkVersion`, is unchanged) and
the build rerun:

```sh
printf ' ' >> rust/ndk-version
cd android && ./gradlew assembleDebug
```

```text
Calculating task graph as configuration cache cannot be reused because file '../rust/ndk-version' has changed.
BUILD SUCCESSFUL in 21s
```

The file was reverted (`git checkout -- rust/ndk-version`). **Conclusion:** the configuration cache
tracks the file content through the provider, so a content-only change that trims to the same
value still invalidates the configuration-time entry; no stale NDK version is reused.

Lazy pin (from the repository root, same cache user home, `build/rust-tools` holding `cargo-ndk`
4.1.2):

```sh
./gradlew rustInstall
sed -i 's/^cargo-ndk = "4.1.2"$/cargo-ndk = "4.1.1"/' rust/toolchain-tools.toml
./gradlew rustInstall
PATH="$PWD/build/rust-tools/cargo-ndk/bin:$PATH" cargo ndk --version
```

First run: `installCargoNdk UP-TO-DATE`, `verifyCargoNdk` passes, `BUILD SUCCESSFUL in 32s`.
After the change:

```text
> Task :rust:installCargoNdk
  Installing cargo-ndk v4.1.1
   Installed package `cargo-ndk v4.1.1` (executables `cargo-ndk`, `cargo-ndk-env`, `cargo-ndk-runner`, `cargo-ndk-test`)
> Task :rust:verifyCargoNdk
BUILD SUCCESSFUL in 5s
cargo-ndk 4.1.1
```

The pin was reverted and `rustInstall` run once more; `installCargoNdk` executed again
(`Installing cargo-ndk v4.1.2`, `BUILD SUCCESSFUL in 6s`) and `cargo ndk --version` printed
`cargo-ndk 4.1.2`, so `build/rust-tools` holds the committed pins again.

**Conclusion:** the pin is read lazily inside the install/verify task graph; editing
`toolchain-tools.toml` alone makes `rustInstall` install and verify the new version without any
configuration-time read.

## 7.3 Relocation

Two throwaway worktrees at HEAD, `$SCRATCH/relA` and `$SCRATCH/relB` (temporary branches
`t7-relA`/`t7-relB`, deleted afterwards), sharing one cache user home `$SCRATCH/guh-3`, seeded
empty. Only `build/rust/tool-install-target` (cargo's own artifact cache for the CLI tools) was
copied into both trees; every product was compiled from scratch in A. The first attempt ran under
`/tmp`, whose tmpfs (7.9 GiB) filled up during the CLI installs; rustc failed with ENOSPC and that
attempt was discarded (its `build-cache-1` never held a product entry). The results below are from
the disk-backed scratch `/home/dev/.cache/gca-task7` after that rerun; the cache user home was
fresh and held only this experiment's entries.

At `44fb7a33`, worktree A (populate):

```sh
cd $SCRATCH/relA
./gradlew rustJni rustWasm rustHostJni --build-cache --info
```

```text
> Task :rust:rustHostJni
Stored cache entry for task ':rust:rustHostJni' with cache key 8aa85d054e5531bba4bbdfb7c76ad843
> Task :rust:rustWasm
Stored cache entry for task ':rust:rustWasm' with cache key 53981485a506ce375b8f53650bbe0953
> Task :rust:rustJni
Stored cache entry for task ':rust:rustJni' with cache key e7045ce60392e030f833c630e24be1ce
BUILD SUCCESSFUL in 4m 6s
```

These are byte-for-byte the same keys the main worktree produced in 7.1 at a third path, which is
the first sign the keys are path-free; `caches/build-cache-1` held 26 files.

Worktree B at the other path, same cache user home, the same command without `--info`:

```sh
cd $SCRATCH/relB
./gradlew rustJni rustWasm rustHostJni --build-cache
```

```text
> Task :rust:rustHostJni FROM-CACHE
> Task :rust:rustJni FROM-CACHE
> Task :rust:rustWasm FROM-CACHE
BUILD SUCCESSFUL in 8s
```

All three products loaded from A's entries at a different absolute path.

B's restored outputs were copied aside (`restored/`), then B was rebuilt from scratch with the
build cache off:

```sh
./gradlew rustJni rustWasm rustHostJni --no-build-cache --rerun-tasks
```

```text
BUILD SUCCESSFUL in 2m 11s
```

All three tasks in the fresh run executed (no `FROM-CACHE`, no `Stored cache entry`). Comparison with
`diff -r` and with full `find <dir> -type f | sort` lists:

```text
== jniLibs
diff: no difference
find lists: identical
== host-jni
diff: no difference
find lists: identical
== pkg
Only in relB/web-extension/rust-wasm/pkg: .gitignore
--- restored.lst
+++ fresh.lst
@@ -1,3 +1,4 @@
+./.gitignore
 ./package.json
 …
```

SHA-256 of the restored (cache) and fresh files:

```text
ecf6f9fe…2e705  arm64-v8a/libveles_crypto.so          (restored = fresh)
df079bb7…a683c  armeabi-v7a/libveles_crypto.so        (restored = fresh)
ca067fa2…b1ba4  x86_64/libveles_crypto.so              (restored = fresh)
d5702b67…64b44  host-jni/libveles_crypto.so            (restored = fresh)
ef7a8936…0638f  pkg/package.json, 53a289e3…99632 pkg/veles_crypto.d.ts,
b2d8710a…b392 pkg/veles_crypto.js, efca1b71…5079c pkg/veles_crypto_bg.wasm,
b3661e76…39db0 pkg/veles_crypto_bg.wasm.d.ts          (restored = fresh)
```

**Failure and fallback.** `rustJni` and `rustHostJni` relocate byte-identically. `rustWasm` does
not: wasm-pack writes `web-extension/rust-wasm/pkg/.gitignore` (content `*`), Gradle's default
excludes drop `.gitignore` from a cached directory output, and the cache load therefore restores
the package without it. That file is part of the package contract — `rustPackage` copies it
explicitly (`rust/build.gradle.kts`, "wasm-pack emits .gitignore as part of the package
contract") — so the cached output is not the fresh output. Per the task rule the product was made
non-cacheable instead of shipping a stale-output cache:

```kotlin
    outputs.doNotCacheIf(
        "wasm-pack emits pkg/.gitignore, which Gradle excludes from cached directory outputs",
    ) { true }
```

committed as `190ceaff build(rust): stop caching rustWasm (wasm-pack .gitignore is lost)`.
Afterwards, with the output removed so the task runs:

```text
> Task :rust:rustWasm
Caching disabled for task ':rust:rustWasm' because:
  'wasm-pack emits pkg/.gitignore, which Gradle excludes from cached directory outputs' satisfied
BUILD SUCCESSFUL in 13s
```

and the fresh `pkg/` again contains `.gitignore` next to the five package files.

Because that commit changes the build-script class path, which is part of every task's cache key,
the relocation proof was repeated on the final tree (`190ceaff`) for the two cacheable products:
A stored `rustHostJni` `6f991a1369e3805a7d30b99062836a3e` and `rustJni`
`53758f653ac9f7a924dcf869f4a2d7f9` (`BUILD SUCCESSFUL in 15s`); B reported
`rustHostJni FROM-CACHE` and `rustJni FROM-CACHE` (`BUILD SUCCESSFUL in 5s`); and B's
`--no-build-cache --rerun-tasks` rebuild executed both tasks with
`diff -r` reporting no difference and identical `find` lists for `build/rust/jniLibs` and
`build/rust/host-jni`. The four binaries keep the SHA-256 values listed above, so the fallback
commit did not change product bytes.

**Conclusion:** the cache keys of `rustJni` and `rustHostJni` are location-independent and their
restored outputs are byte-identical to a fresh build, dotfiles included. `rustWasm` is not
cacheable: Gradle's cached-output snapshot loses wasm-pack's `.gitignore`.

## 7.4 Refusal

Cache user home `$SCRATCH/guh-4`, seeded empty.

**Inherited-environment denylist.** A pass-through `RUSTC_WRAPPER` was created and
`build/rust/host-jni` removed first so the task would run (with the output in place it is
`UP-TO-DATE` and Gradle prints no caching line):

```sh
printf '#!/bin/sh\nexec "$@"\n' > $SCRATCH/wrap && chmod +x $SCRATCH/wrap
rm -rf build/rust/host-jni
RUSTC_WRAPPER=$SCRATCH/wrap ./gradlew rustHostJni --build-cache --info
```

```text
> Task :rust:rustHostJni
Caching disabled for task ':rust:rustHostJni' because:
  'the inherited environment or a cargo configuration file may change the output' satisfied
BUILD SUCCESSFUL in 2m 1s
2 actionable tasks: 2 executed
```

The predicate is the one registered for the inherited-environment/cargo-config denylist; no
`Stored cache entry` and no `FROM-CACHE` appeared. (The specific variable name is evaluated inside
`RustBuildHelpers.cacheRefusal`; the registered `doNotCacheIf` message is the one Gradle prints.)

**Cargo configuration file.** A temporary `CARGO_HOME` was populated with the registry and an
empty `config.toml` (`cp -r ~/.cargo/registry $SCRATCH/cargo-home/`, `: > $SCRATCH/cargo-home/config.toml`),
`build/rust/jniLibs` removed, and:

```sh
CARGO_HOME=$SCRATCH/cargo-home ./gradlew rustJni --build-cache --info
```

```text
> Task :rust:rustJni
Caching disabled for task ':rust:rustJni' because:
  'the inherited environment or a cargo configuration file may change the output' satisfied
BUILD SUCCESSFUL in 1m 54s
```

No load and no store. A control run with the same `CARGO_HOME` after deleting only `config.toml`
stored an entry (`Stored cache entry for task ':rust:rustJni' with cache key
53758f653ac9f7a924dcf869f4a2d7f9`, `BUILD SUCCESSFUL in 5s`), so the refusal is the cargo
configuration file, not the changed home.

**Toolchain check.** `./gradlew rustToolchainCheck --build-cache` twice:

```text
> Task :rust:rustToolchainCheck
BUILD SUCCESSFUL in 3s
> Task :rust:rustToolchainCheck
BUILD SUCCESSFUL in 1s
```

The task executed both times and never reported `FROM-CACHE` (`@DisableCachingByDefault`).

**Network download.** `./gradlew clean installGitCliff --build-cache --info` (the clean removes
earlier outputs, so the install is genuinely fresh):

```text
> Task :clean
> Task :installGitCliff
Caching disabled for task ':installGitCliff' because:
  downloads a release archive
BUILD SUCCESSFUL in 11s
```

`installGitCliff` executed the download, SHA-256 verification and `--version` check; no load and no
store.

**Conclusion:** the refusal rules are observable as "no load and no store" with the registered
`Caching disabled … because:` message, and a cached entry is never produced for a denylisted
environment, a cargo config file, a host/toolchain probe or a network install.

## 7.5 Default off

Cache user home `$SCRATCH/guh-5`, seeded empty, no `gradle.properties` (no `org.gradle.caching`).
This ran after 7.4's `clean`, so the products were absent and every task really executed:

```sh
./gradlew rustJni --info
```

```text
> Task :rust:rustJni
Caching disabled for task ':rust:rustJni' because:
  Build cache is disabled
BUILD SUCCESSFUL in 3m 32s
```

No `Stored cache entry`, no `FROM-CACHE`, and `caches/build-cache-1` held no entry (0 files).

Then a user-level opt-in was written into the private user home and the guarded command run:

```sh
printf 'org.gradle.caching=true\n' > $GRADLE_USER_HOME/gradle.properties
./gradlew rustPackage --no-build-cache --no-configuration-cache --info
```

```text
> Task :rust:rustWasm
Caching disabled for task ':rust:rustWasm' because:
  Build cache is disabled
> Task :rust:rustPackage
BUILD SUCCESSFUL in 3m 50s
```

Again no load and no store, and `caches/build-cache-1` held no entry (0 files). `--no-build-cache`
overrides the user-level `org.gradle.caching=true`; the `--no-configuration-cache` half is the
guarded pipeline's flag as well as a check that the flag is accepted.

**Conclusion:** caching is off by default in both entry points, and the release/verification guard
is effective even when a developer has opted in at user level.

## 7.6 Git state under the configuration cache

All tag and commit work happened in a throwaway `git clone --no-hardlinks` of the worktree at
`$SCRATCH/clone`, checked out detached at `44fb7a33`, with the inherited `android-v0.1.0` tag
deleted there. Nothing was pushed; the clone was deleted afterwards and the worktree never had an
`android-v9*` tag (`git tag -l 'android-v9*'` prints nothing). Cache user home `$SCRATCH/guh-6`,
seeded empty.

```sh
cd $SCRATCH/clone/android
./gradlew -q :app:androidGitVersion          # no tag
```

```text
androidGitVersion.name	untagged-144-44fb7a3-44fb7a332fda3414defe27ab0ac2d2967f644b73
androidGitVersion.code	0
```

Then `git tag android-v9.9.9` in the clone and the same command:

```text
Calculating task graph as configuration cache cannot be reused because a build logic input of type 'GitVersionValueSource' has changed.
androidGitVersion.name	9.9.9
androidGitVersion.code	90909
```

Then `git commit --allow-empty -m x` (new HEAD `6068dd5`) and the same command:

```text
Calculating task graph as configuration cache cannot be reused because a build logic input of type 'GitVersionValueSource' has changed.
androidGitVersion.name	9.9.9-1-6068dd5-6068dd59397080a247408d3876ddac24b37eaaf0
androidGitVersion.code	90909
```

The `-q` runs print the values; a non-`-q` run in the same state was also made to see the
configuration-cache line, and a further run with no state change reported:

```text
Reusing configuration cache.
androidGitVersion.name	9.9.9-1-6068dd5-6068dd59397080a247408d3876ddac24b37eaaf0
androidGitVersion.code	90909
```

**Conclusion:** the configuration cache invalidates when the `GitVersionValueSource` value changes,
and when it is reused it re-derives the current value, so a reused entry never reports a stale
version for a new tag or commit.

## Summary

| Experiment | Observation |
| --- | --- |
| 7.1 input change | 3 stores on a cold run; pin/toolchain edits execute with new keys; reverting loads all three `FROM-CACHE` |
| 7.2 configuration-time invalidation | trailing space in `rust/ndk-version` invalidates the configuration cache; lazy pin installs/verifies 4.1.1 then 4.1.2 |
| 7.3 relocation | A stores, B at another path loads; `rustJni` + `rustHostJni` byte-identical to a fresh build; `rustWasm` lost `pkg/.gitignore` and is now `doNotCacheIf` (`190ceaff`) |
| 7.4 refusal | denylisted env, cargo `config.toml`, toolchain probe and git-cliff download: no load and no store (config file proved by a control store) |
| 7.5 default off | no load/store by default; user-level `org.gradle.caching=true` still overridden by `--no-build-cache` |
| 7.6 git state | detached clone: untagged → 9.9.9/90909 → 9.9.9-1-6068dd5-…/90909; reuse re-derives, never stale |

Final state of the pins: `rust/toolchain-tools.toml` is `cargo-ndk = "4.1.2"`,
`wasm-pack = "0.15.0"`, `wasm-bindgen-cli = "0.2.127"`; `rust/rust-toolchain.toml` and
`rust/ndk-version` are unchanged (no probe comment, no trailing space). No
`org.gradle.caching=true` is committed; the only product-behaviour change from this task is the
`rustWasm` cacheability fallback.
