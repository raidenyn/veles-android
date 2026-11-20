## Baseline APK and Rust package at the merge base

Task 1.4. Built on 2026-11-20 from a throwaway clone at the merge base, with the local tag
`android-v9.9.9` on `HEAD`, so the version derivation has a deterministic input. JDK Temurin 21.0.12,
Gradle 9.5.0, Android SDK/NDK from `ANDROID_SDK_ROOT`/`ANDROID_HOME`, no configuration cache.

Base commit: `597ee2c691f2d4c2dec1c4450072eca86a76054e` (`git merge-base HEAD origin/master`).

### The clone

```sh
export ANDROID_SDK_ROOT=/home/dev/.android-sdk ANDROID_HOME=/home/dev/.android-sdk
export JAVA_HOME=$HOME/.local/jdks/temurin-21.0.12
export PATH="$JAVA_HOME/bin:$PATH"
SCRATCH=/tmp/opencode/gradle-cache-safe-task1
git clone --no-hardlinks . "$SCRATCH/apk-base"
cd "$SCRATCH/apk-base"
git checkout --detach 597ee2c691f2d4c2dec1c4450072eca86a76054e
git tag android-v9.9.9
```

The tag exists only in this throwaway clone. It is never pushed, and
`git tag -l 'android-v9*'` in the change's worktree (which shares refs with the main repository, not
with the clone) prints nothing.

### Builds

```sh
# from $SCRATCH/apk-base/android
./gradlew assembleRelease assembleDebug --no-build-cache
# from $SCRATCH/apk-base
./gradlew rustPackage --no-build-cache
```

| Run | Wall clock (ms) | Gradle line | Task summary |
| --- | --- | --- | --- |
| `assembleRelease assembleDebug --no-build-cache` | 560340 (9m 20.340s) | `BUILD SUCCESSFUL in 9m 19s` | 105 actionable tasks: 105 executed |
| `rustPackage --no-build-cache` | 345480 (5m 45.480s) | `BUILD SUCCESSFUL in 5m 44s` | 12 actionable tasks: 10 executed, 2 up-to-date |

`app-release-unsigned.apk` is the unsigned release APK the reproducible-build contract compares; the
release signing step in `publish-release.yml` runs `apksigner` on it outside Gradle.

### SHA-256 values

```text
b2460b493d830893d3cb812280aa43a5586206a0cfcb94f24aaa7a870cf50333  android/app/build/outputs/apk/release/app-release-unsigned.apk
9bc8626465d4e1457f94ccc495b6ba6178f09c53e39b59466203de5a03662033  android/app/build/outputs/apk/debug/app-debug.apk
67267b1cc392e80d5a67b89566ecfdaecb9d564644c36db391296a2c65f475c1  build/rust-package/SHA256SUMS
```

Sizes: release APK 5042207 bytes, debug APK 69531578 bytes.

Release APK SHA-256 is the equality Task 10 step 1 must reproduce. The debug APK SHA-256 is recorded
for comparison; it is expected to be reported as equal or as differing, with the version fields proven
equal either way.

### `build/rust-package/SHA256SUMS`

Its own SHA-256 is `67267b1cc392e80d5a67b89566ecfdaecb9d564644c36db391296a2c65f475c1`; its contents are:

```text
ecf6f9fe71c1cd85cf5bdd2a13e8d973da7d6379ecd81a396dcf9cfc3172e705  jni/arm64-v8a/libveles_crypto.so
df079bb7c253959ecb96e47999ab474a9933206155352dd2a4f252458c4a683c  jni/armeabi-v7a/libveles_crypto.so
ca067fa2ff750cb916d066a58324584d680f09ae63a0574ea94952d6883b1ba4  jni/x86_64/libveles_crypto.so
684888c0ebb17f374298b65ee2807526c066094c701bcc7ebbe1c1095f494fc1  wasm/.gitignore
ef7a8936dac1f3402a47a62b381fac2dd0128710e518f05e7e8a8fd4030d638f  wasm/package.json
53a289e31006a4a90d2c52a639be33cb60ca2c781f6501c3fcb6b7b95c269632  wasm/veles_crypto.d.ts
b2d8710a231af93cdb9a22ca1f7a7f93e4ae09fba1ef8e02f752877383b1b392  wasm/veles_crypto.js
efca1b71a4151c1161a7fadcdab771106e41036c2df90e1ff03a987d2a25079c  wasm/veles_crypto_bg.wasm
b3661e76423884cac3e9e7f576ea6c395ed5e4af22f11281b93fc0f1c6d39db0  wasm/veles_crypto_bg.wasm.d.ts
```

### Derived version

```text
cd android && ./gradlew -q :app:androidGitVersion
androidGitVersion.name	9.9.9
androidGitVersion.code	90909
```

### Reproduction

Any later run must use the same clone path or its own equivalent, the same `HEAD` plus tag, the same
JDK, and `--no-build-cache`. The release APK and `SHA256SUMS` hashes above are the ones Task 10
compares.

## Equality at the change's HEAD

Task 10.1. Built on 2026-11-20 from a throwaway clone of the change's final commit, with the same
local tag `android-v9.9.9` on `HEAD`, so the derived version has the same deterministic input.
JDK Azul Zulu 21.0.12.1 (the JDK this task's environment selects; the merge-base baseline above was
built with Temurin 21.0.12, which is no longer installed on this machine), Gradle 9.5.0, the same
Android SDK/NDK. The tag exists only in the throwaway clone (its refs are cloned, not shared, and the
clone is never pushed), so `git tag -l 'android-v9*'` in the change's worktree prints nothing.

Build commit: `22e4955818699ecc5e79da2c8b6eb289dca25ed1` (`chore/gradle-cache-safe` HEAD).

### The clone

```sh
export ANDROID_SDK_ROOT=/home/dev/.android-sdk ANDROID_HOME=/home/dev/.android-sdk
export JAVA_HOME=/home/dev/.jdk/zulu21
export PATH="$JAVA_HOME/bin:$PATH"
SCRATCH=/tmp/opencode/gradle-cache-safe-task10
git clone --no-hardlinks /home/dev/.t3/worktrees/veles-android/t3-699e4a45-1711ce8ea1e8baf4 "$SCRATCH/apk-after"
cd "$SCRATCH/apk-after"
git checkout --detach 22e4955818699ecc5e79da2c8b6eb289dca25ed1
git tag android-v9.9.9
# from android/: ./gradlew assembleRelease assembleDebug --no-build-cache
# from the root:  ./gradlew rustPackage --no-build-cache
```

| Run | Wall clock (ms) | Gradle line | Task summary |
| --- | --- | --- | --- |
| `assembleRelease assembleDebug --no-build-cache` | 417505 (6m 57.505s) | `BUILD SUCCESSFUL in 6m 56s` | 105 actionable tasks: 105 executed |
| `rustPackage --no-build-cache` | 260316 (4m 20.316s) | `BUILD SUCCESSFUL in 4m 19s` | 12 actionable tasks: 10 executed, 2 up-to-date |

Derived version in the clone: `androidGitVersion.name 9.9.9`, `androidGitVersion.code 90909`.

### SHA-256 values against the baseline

```text
b2460b493d830893d3cb812280aa43a5586206a0cfcb94f24aaa7a870cf50333  android/app/build/outputs/apk/release/app-release-unsigned.apk
9df61e182c0284236d74b002748958c9226efccd7f94705eacc201c5b5a60923  android/app/build/outputs/apk/debug/app-debug.apk
67267b1cc392e80d5a67b89566ecfdaecb9d564644c36db391296a2c65f475c1  build/rust-package/SHA256SUMS
```

| Artifact | After | Baseline | Equal |
| --- | --- | --- | --- |
| `app-release-unsigned.apk` | `b2460b49…` (5042207 bytes) | `b2460b49…` (5042207 bytes) | yes |
| `build/rust-package/SHA256SUMS` | `67267b1c…` | `67267b1c…` | yes |
| `app-debug.apk` | `9df61e18…` (69531578 bytes) | `9bc86264…` (69531578 bytes) | no, see below |

The release APK and the Rust package are byte-identical to the merge-base artifacts, so the change
does not alter the reproducible release output.

### Debug APK

The debug hash differs from the recorded one. The version fields are equal:
`aapt2 dump badging` prints `versionCode='90909' versionName='9.9.9'` (package
`me.nagaev.veles.debug`), matching the baseline record.

The difference is not caused by the change. Rebuilding the debug APK in a fresh clone at the merge
base (`597ee2c6…`, tag `android-v9.9.9`, `--no-build-cache`) reproduces `9df61e18…` byte for byte
under both JDKs available here — Zulu 21.0.12.1 and a freshly installed Temurin 21.0.12+8 — and in a
different clone path. The change's APK and the merge-base rebuild are the same file.

The candidate environment input is the debug signing key: the APK is signed with v2 only
(`apksigner verify`: v1 false, v2 true), so its `unzip -l` entries are untouched by signing and the
only inputs that can change the bytes without changing the size or the entry list are the certificate
and signature in the APK signing block. `~/.android/debug.keystore` was regenerated at 17:37 on
2026-11-20, four hours after the baseline record (13:32); re-signing the same APK with a throwaway key
changes the SHA-256 while the `unzip -l` entry list stays identical apart from `apksigner`'s own v1
entries. The recorded baseline debug APK itself was not retained on this machine, so no artifact-level
`unzip -l` diff against `9bc86264…` could be produced; the 69531578-byte size is unchanged and no
product-code difference exists between the two builds.
