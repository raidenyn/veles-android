# OTP-01 Sub-project 1b — Rust JNI/WASM Toolchain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one locked Rust placeholder core that reverses bytes through real Android JNI and browser-shaped WASM bindings, with deterministic Gradle builds, exact Android ABI packaging, and CI/reproducibility integration.

**Architecture:** A top-level `rust/` Cargo workspace contains one `veles-crypto` `rlib`/`cdylib`; its shared byte-reversal function is exported through target-gated JNI and `wasm-bindgen` surfaces. Root Gradle tasks install exact auxiliary Cargo CLIs into non-overlapping `build/rust-tools/<tool>/` roots, build JNI into `app/build/generated/jniLibs/`, and build WASM into the named `web-extension/rust-wasm/pkg/` exception. Android assembly depends on `rustJni`, while native Rust, Node/WASM, Android instrumentation, APK inspection, clean reconstruction, release jobs, and the pinned Docker verifier prove each boundary.

**Tech Stack:** Rust 1.98.0 edition 2024, Cargo, `jni` 0.22.4, `wasm-bindgen`/`wasm-bindgen-cli` 0.2.127, `wasm-pack` 0.15.0, `cargo-ndk` 4.1.2, Android NDK r29 (`29.0.14206865`), Gradle 9.5 Kotlin DSL, AGP 9.3.0, Kotlin/JNI, Node 22+.

**Spec:** `docs/superpowers/specs/2026-08-26-otp-01-reproducible-toolchains-design.md` (`Sub-project 1b: Rust core + JNI/WASM` plus Global decisions). Read both before starting.

## Global Constraints

- Keep exactly one workspace crate, `rust/veles-crypto`; do not split core/JNI/WASM crates in 1b.
- The only operation is deterministic byte reversal. Add no hash, SPAKE2, key material, randomness, protocol state, Bluetooth, OTP flow, UI, or production extension import.
- Pin Rust `1.98.0`; components are `clippy` and `rustfmt`; targets are `aarch64-linux-android`, `armv7-linux-androideabi`, `x86_64-linux-android`, and `wasm32-unknown-unknown`.
- Pin direct dependencies exactly: `jni = "=0.22.4"`, `wasm-bindgen = "=0.2.127"`; commit `rust/Cargo.lock`; every workspace build/test/lint uses `--locked`.
- Pin auxiliary CLIs in `rust/toolchain-tools.toml`: `cargo-ndk = "4.1.2"`, `wasm-pack = "0.15.0"`, `wasm-bindgen-cli = "0.2.127"`.
- Gradle may install those exact CLIs with `cargo install --locked`; Gradle must never install rustup, Rust, Node, or the NDK.
- Pin NDK r29 only in `gradle/libs.versions.toml` as `ndk = "29.0.14206865"`; propagate it to `android.ndkVersion` and derive CI provisioning from Gradle.
- `rustJni` must validate AGP's configured NDK `source.properties`, set that exact directory as `ANDROID_NDK_HOME`, and never let cargo-ndk choose another installed NDK.
- Build exactly `arm64-v8a`, `armeabi-v7a`, and `x86_64`; apply app-level ABI filters so existing dependency-provided `x86` libraries are excluded.
- JNI output is only `app/build/generated/jniLibs/<abi>/libveles_crypto.so`; never write or commit `app/src/main/jniLibs/`.
- Cargo compilation and tool-install targets stay under root `build/rust/`; auxiliary binaries stay under `build/rust-tools/<tool>/`.
- WASM output is only `web-extension/rust-wasm/pkg/`, gitignored and replaced by `rustWasm`.
- `rustJni` and other Android-scoped Rust tasks (`rustLintAndroid`) set `CARGO_PROFILE_RELEASE_PANIC=unwind`; `rustWasm` explicitly uses `CARGO_PROFILE_RELEASE_PANIC=abort`.
- `wasm-pack` runs `--mode no-install --no-opt` with local matching `wasm-bindgen-cli`, an absolute `--out-dir`, and Cargo's `--locked` only after `--`.
- Root `check` depends on `rustFormat`, `rustLint`, and `rustTest`; root `clean` depends on app clean and removes both generated web exceptions.
- The exact MV3 CSP after 1b is `script-src 'self' 'wasm-unsafe-eval'; object-src 'self'`; ordinary `unsafe-eval`, remote sources, host permissions, and new permissions remain forbidden.
- Existing worktree changes not created by this plan are never reverted or overwritten.

## File Map

| File | Responsibility |
|---|---|
| `rust/Cargo.toml` | One-crate workspace declaration and resolver. |
| `rust/Cargo.lock` | Committed locked Rust dependency graph. |
| `rust/rust-toolchain.toml` | Exact compiler, components, and Android/WASM targets. |
| `rust/toolchain-tools.toml` | Exact auxiliary Cargo CLI versions consumed by Gradle/cache keys. |
| `rust/veles-crypto/Cargo.toml` | `rlib`/`cdylib`, exact target-specific JNI/WASM dependencies. |
| `rust/veles-crypto/src/lib.rs` | Shared reverse function and target-gated JNI/WASM exports. |
| `rust/veles-crypto/tests/wasm-smoke.mjs` | Node 22 runtime proof against generated `--target web` bindings. |
| `build.gradle.kts` | Root base lifecycle and Rust validation/install/build/format/lint/test tasks. |
| `gradle/libs.versions.toml` | NDK r29 source of truth. |
| `app/build.gradle.kts` | NDK propagation, generated JNI source set, ABI filters, JNI merge dependency. |
| `app/src/main/java/me/nagaev/veles/crypto/VelesCrypto.kt` | Minimal Kotlin JNI loader/API. |
| `app/src/androidTest/java/me/nagaev/veles/crypto/VelesCryptoInstrumentedTest.kt` | Runtime JNI byte fixtures. |
| `web-extension/.gitignore` | Ignores generated `rust-wasm/pkg/`. |
| `web-extension/src/manifest.ts` | Exact 1b CSP baseline. |
| `web-extension/test/{smoke,manifest-guard}.test.ts` | Source-level exact CSP expectations. |
| `rust/scripts/verify-apk-jni.sh` | CI assertion for exact APK ABI set and exact Veles JNI entries. |
| `rust/scripts/assert-clean.sh` | CI assertion that every declared generated output was removed. |
| `.github/workflows/ci.yml` | Dedicated Rust job and Rust/NDK setup for instrumented tests. |
| `.github/workflows/release.yml` | Rust/NDK setup/cache before tagged release assembly. |
| `.github/workflows/release-build.yml` | Rust/NDK setup/cache before release-build assembly. |
| `verify/Dockerfile` | Pinned rustup/Rust targets and NDK r29 in APK reference image. |
| `verify/verify.sh` | Rust/NDK pin consistency checks and root Docker build context. |
| `CLAUDE.md`, `README.md`, `docs/reproducible-builds.md` | Developer commands, sub-project map, and pin/rebuild documentation. |

---

### Task 1: Bootstrap the locked Rust workspace and shared core

**Files:**
- Create: `rust/Cargo.toml`
- Create: `rust/Cargo.lock`
- Create: `rust/rust-toolchain.toml`
- Create: `rust/toolchain-tools.toml`
- Create: `rust/veles-crypto/Cargo.toml`
- Create: `rust/veles-crypto/src/lib.rs`

**Interfaces:**
- Consumes: no prior 1b code.
- Produces: `pub fn reverse_bytes(input: &[u8]) -> Vec<u8>`, exported to WASM only on `wasm32`; exact workspace/tool pins consumed by every later task.

- [ ] **Step 1: Create the pinned workspace/tool manifests**

`rust/Cargo.toml`:

```toml
[workspace]
members = ["veles-crypto"]
resolver = "3"
```

`rust/rust-toolchain.toml`:

```toml
[toolchain]
channel = "1.98.0"
profile = "minimal"
components = ["clippy", "rustfmt"]
targets = [
    "aarch64-linux-android",
    "armv7-linux-androideabi",
    "x86_64-linux-android",
    "wasm32-unknown-unknown",
]
```

`rust/toolchain-tools.toml`:

```toml
cargo-ndk = "4.1.2"
wasm-pack = "0.15.0"
wasm-bindgen-cli = "0.2.127"
```

`rust/veles-crypto/Cargo.toml`:

```toml
[package]
name = "veles-crypto"
version = "0.1.0"
edition = "2024"
publish = false

[lib]
crate-type = ["rlib", "cdylib"]

[target.'cfg(target_os = "android")'.dependencies]
jni = "=0.22.4"

[target.'cfg(target_arch = "wasm32")'.dependencies]
wasm-bindgen = "=0.2.127"
```

- [ ] **Step 2: Write failing unit tests before the core function**

Create `rust/veles-crypto/src/lib.rs` with tests only:

```rust
#[cfg(test)]
mod tests {
    use super::reverse_bytes;

    #[test]
    fn reverses_known_bytes() {
        assert_eq!(reverse_bytes(&[0x00, 0x80, 0xff, 0x2a]), [0x2a, 0xff, 0x80, 0x00]);
    }

    #[test]
    fn reverses_empty_input() {
        assert!(reverse_bytes(&[]).is_empty());
    }

    #[test]
    fn reversing_twice_restores_arbitrary_binary_input() {
        let input = [0x00, 0x01, 0x7f, 0x80, 0xfe, 0xff];
        assert_eq!(reverse_bytes(&reverse_bytes(&input)), input);
    }
}
```

- [ ] **Step 3: Run the unit tests and verify RED**

All direct Cargo commands in this plan must set `CARGO_TARGET_DIR` to keep compilation output under `build/rust/target/` (the global-output boundary). Run:

```bash
CARGO_TARGET_DIR=build/rust/target cargo test --workspace --locked --manifest-path rust/Cargo.toml
```

Expected: FAIL at compile time because `reverse_bytes` is undefined. If Cargo first reports that `Cargo.lock` is absent, run `CARGO_TARGET_DIR=build/rust/target cargo generate-lockfile --manifest-path rust/Cargo.toml`, then rerun and preserve the expected undefined-function failure.

- [ ] **Step 4: Implement the minimal shared function and WASM export attribute**

Prepend to `rust/veles-crypto/src/lib.rs`:

```rust
#[cfg(target_arch = "wasm32")]
use wasm_bindgen::prelude::wasm_bindgen;

#[cfg_attr(target_arch = "wasm32", wasm_bindgen)]
pub fn reverse_bytes(input: &[u8]) -> Vec<u8> {
    input.iter().rev().copied().collect()
}
```

Do not add a second implementation or any cryptographic dependency.

- [ ] **Step 5: Lock dependencies and verify native/WASM compilation**

Run each command from the repository root (not from `rust/`), so sequential execution remains correct:

```bash
export CARGO_TARGET_DIR="$PWD/build/rust/target"
cargo generate-lockfile --manifest-path rust/Cargo.toml
cargo fmt --all --manifest-path rust/Cargo.toml
cargo test --workspace --locked --manifest-path rust/Cargo.toml
cargo check --workspace --target wasm32-unknown-unknown --locked --manifest-path rust/Cargo.toml
cargo fmt --all --check --manifest-path rust/Cargo.toml
```

Expected: all commands PASS; `rust/Cargo.lock` exists and resolves exact `jni`/`wasm-bindgen` versions. `cargo fmt --all` (write mode) normalizes the source before `--check` validates it.

- [ ] **Step 6: Commit the workspace/core slice**

```bash
git add rust/Cargo.toml rust/Cargo.lock rust/rust-toolchain.toml \
  rust/toolchain-tools.toml rust/veles-crypto/Cargo.toml \
  rust/veles-crypto/src/lib.rs
git commit -m "feat(otp-01/1b): add locked Rust core workspace"
```

---

### Task 2: Build and execute the web-target WASM binding through Gradle

**Files:**
- Modify: `build.gradle.kts`
- Modify: `web-extension/.gitignore`
- Create: `rust/veles-crypto/tests/wasm-smoke.mjs`

**Interfaces:**
- Consumes: Task 1's workspace, `reverse_bytes`, `rust-toolchain.toml`, and `toolchain-tools.toml`.
- Produces: root tasks `rustInstall`, `rustWasm`, and `rustTest`; generated module `web-extension/rust-wasm/pkg/veles_crypto.js` with `reverse_bytes(Uint8Array): Uint8Array`.

- [ ] **Step 1: Write the failing Node smoke test**

Create `rust/veles-crypto/tests/wasm-smoke.mjs`:

```javascript
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import init, { reverse_bytes } from '../../../web-extension/rust-wasm/pkg/veles_crypto.js';

const wasmUrl = new URL(
    '../../../web-extension/rust-wasm/pkg/veles_crypto_bg.wasm',
    import.meta.url,
);
await init({ module_or_path: await readFile(wasmUrl) });

for (const [input, expected] of [
    [[], []],
    [[0x00, 0x80, 0xff, 0x2a], [0x2a, 0xff, 0x80, 0x00]],
]) {
    assert.deepEqual(Array.from(reverse_bytes(Uint8Array.from(input))), expected);
}
```

Add to `web-extension/.gitignore`:

```gitignore
rust-wasm/pkg/
```

- [ ] **Step 2: Run the smoke test and verify RED**

Run:

```bash
node rust/veles-crypto/tests/wasm-smoke.mjs
```

Expected: FAIL with module-not-found for `web-extension/rust-wasm/pkg/veles_crypto.js`.

- [ ] **Step 3: Add root lifecycle plus reusable pin/process helpers**

In `build.gradle.kts`, add imports for `ByteArrayOutputStream`, `File`, `Properties`, Gradle `Exec`, and Android `ApplicationAndroidComponentsExtension`, then apply the base plugin:

```kotlin
plugins {
    base
    alias(libs.plugins.android.application) apply false
    // Existing aliases remain unchanged.
}
```

Add these root values/helpers after the existing `spotless` block:

```kotlin
val rustDir = layout.projectDirectory.dir("rust")
val rustCrateDir = rustDir.dir("veles-crypto")
val rustToolsFile = rustDir.file("toolchain-tools.toml")
val rustTargetDir = layout.buildDirectory.dir("rust/target")
val rustToolsDir = layout.buildDirectory.dir("rust-tools")
val wasmPackageDir = layout.projectDirectory.dir("web-extension/rust-wasm/pkg")
val windowsExecutableSuffix = if (System.getProperty("os.name").startsWith("Windows")) ".exe" else ""

fun pinnedToolVersion(name: String): String {
    val pattern = Regex("(?m)^${Regex.escape(name)}\\s*=\\s*\"([^\"]+)\"\\s*$")
    return requireNotNull(pattern.find(rustToolsFile.asFile.readText())) {
        "Missing '$name' pin in ${rustToolsFile.asFile}"
    }.groupValues[1]
}

fun File.prependToPath(existing: String?): String =
    absolutePath + File.pathSeparator + (existing ?: "")

fun requireOnPath(name: String, versionHint: String, installHint: String) {
    val path = System.getenv("PATH") ?: ""
    val found = path.split(File.pathSeparator).any { dir ->
        File(dir, name + windowsExecutableSuffix).let { it.isFile && it.canExecute() }
    }
    check(found) {
        "Could not find '$name' on PATH. $installHint. Version hint: $versionHint."
    }
}
```

Keep all new tasks in group `rust`. Do not introduce a Gradle subproject.

- [ ] **Step 4: Add exact per-tool install and verification tasks**

Add a registration helper that creates one `Exec` install task and one verification task per CLI. The implementation must use the exact shape below; each tool has a distinct install root and install target directory:

```kotlin
fun registerCargoTool(
    taskStem: String,
    crateName: String,
    binaryName: String,
): Pair<TaskProvider<Exec>, TaskProvider<Task>> {
    val version = pinnedToolVersion(crateName)
    val installRoot = rustToolsDir.map { it.dir(crateName) }
    val binary = installRoot.map {
        it.file("bin/$binaryName$windowsExecutableSuffix")
    }
    val install = tasks.register<Exec>("install$taskStem") {
        group = "rust"
        description = "Installs $crateName $version into the build directory."
        dependsOn("rustToolchainCheck")
        inputs.property("crateName", crateName)
        inputs.property("version", version)
        inputs.file(rustDir.file("rust-toolchain.toml"))
        outputs.dir(installRoot)
        workingDir(rustDir)
        environment(
            "CARGO_TARGET_DIR",
            layout.buildDirectory.dir("rust/tool-install-target/$crateName").get().asFile,
        )
        doFirst { delete(installRoot) }
        commandLine(
            "cargo", "install", "--locked", "--version", version,
            "--root", installRoot.get().asFile, crateName,
        )
    }
    val verify = tasks.register("verify$taskStem") {
        group = "rust"
        dependsOn(install)
        inputs.property("version", version)
        inputs.file(binary)
        doLast {
            val output = providers.exec {
                commandLine(binary.get().asFile, "--version")
            }.standardOutput.asText.get().trim()
            // Extract the first whitespace-delimited token after the tool name
            // and compare it as an exact semver, not a substring. `contains`
            // would accept 4.1.20 when expecting 4.1.2.
            val actualVersion = output.split(Regex("\\s+"))
                .firstOrNull { it.matches(Regex("\\d+\\.\\d+\\.\\d+(-\\S+)?")) }
            check(actualVersion == version) {
                "Expected $crateName $version at ${binary.get().asFile}, got: $output"
            }
        }
    }
    return install to verify
}
```

Register `rustToolchainCheck` before invoking the helper:

```kotlin
val rustToolchainCheck = tasks.register("rustToolchainCheck") {
    group = "rust"
    inputs.file(rustDir.file("rust-toolchain.toml"))
    doLast {
        requireOnPath(
            "rustup",
            "Rust 1.98.0",
            "Install rustup and run `rustup show active-toolchain` in rust/ to install the pinned toolchain",
        )
        requireOnPath(
            "cargo",
            "Rust 1.98.0",
            "Install rustup and run `rustup show active-toolchain` in rust/ to install the pinned toolchain",
        )
        val rustup = providers.exec {
            workingDir(rustDir)
            commandLine("rustup", "show", "active-toolchain")
        }.standardOutput.asText.get().trim()
        val rustc = providers.exec {
            workingDir(rustDir)
            commandLine("rustc", "--version")
        }.standardOutput.asText.get().trim()
        check(rustc.startsWith("rustc 1.98.0 ")) {
            "rust/rust-toolchain.toml requires Rust 1.98.0; active toolchain is '$rustup' ($rustc)"
        }
    }
}

val (_, verifyCargoNdk) = registerCargoTool("CargoNdk", "cargo-ndk", "cargo-ndk")
val (_, verifyWasmPack) = registerCargoTool("WasmPack", "wasm-pack", "wasm-pack")
val (_, verifyWasmBindgen) = registerCargoTool(
    "WasmBindgenCli",
    "wasm-bindgen-cli",
       "wasm-bindgen",
)

val rustWasmBindgenConsistency = tasks.register("rustWasmBindgenConsistency") {
    group = "rust"
    dependsOn(verifyWasmBindgen)
    inputs.file(rustDir.file("Cargo.lock"))
    inputs.property("cliVersion", pinnedToolVersion("wasm-bindgen-cli"))
    doLast {
        // Parse the resolved wasm-bindgen version from Cargo.lock and verify
        // it exactly matches the wasm-bindgen-cli pin. The spec requires the
        // CLI and runtime to be in lock-step.
        val lock = rustDir.file("Cargo.lock").asFile.readText()
        val lockVersion = Regex(
            "(?ms)^name = \"wasm-bindgen\"$.*?^version = \"([^\"]+)\"$",
        ).find(lock)?.groupValues?.get(1)
        val cliVersion = pinnedToolVersion("wasm-bindgen-cli")
        check(lockVersion == cliVersion) {
            "wasm-bindgen ($lockVersion in Cargo.lock) must match " +
                "wasm-bindgen-cli ($cliVersion in toolchain-tools.toml)."
        }
    }
}

val rustInstall = tasks.register("rustInstall") {
    group = "rust"
    description = "Installs and verifies all exact-pinned Rust build CLIs."
    dependsOn(verifyCargoNdk, verifyWasmPack, rustWasmBindgenConsistency)
}
```

If Gradle Kotlin DSL rejects `inputs.file(binary)` because it receives a provider of `RegularFile`, use `inputs.file(binary.map { it.asFile })`; do not weaken the input or switch to a system binary.

- [ ] **Step 5: Add `rustWasm`, Node floor validation, and `rustTest`**

Add:

```kotlin
val rustWasmLockCheck = tasks.register<Exec>("rustWasmLockCheck") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    // wasm-pack 0.15 runs `cargo metadata` (unlocked) before forwarding --locked
    // to cargo build. Preflight with --locked here so lock drift fails before
    // wasm-pack can touch anything.
    commandLine("cargo", "metadata", "--locked", "--no-deps", "--manifest-path", "Cargo.toml")
}

val rustWasm = tasks.register<Exec>("rustWasm") {
    group = "rust"
    description = "Builds the locked web-target WASM package."
    dependsOn(verifyWasmPack, verifyWasmBindgen, rustWasmBindgenConsistency, rustWasmLockCheck)
    inputs.files(
        fileTree(rustCrateDir) { include("Cargo.toml", "src/**/*.rs") },
        rustDir.file("Cargo.toml"),
        rustDir.file("Cargo.lock"),
        rustDir.file("rust-toolchain.toml"),
        rustToolsFile,
    )
    outputs.dir(wasmPackageDir)
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    environment("CARGO_PROFILE_RELEASE_PANIC", "abort")
    doFirst {
        delete(wasmPackageDir)
        val wasmPackBin = rustToolsDir.get().file(
            "wasm-pack/bin/wasm-pack$windowsExecutableSuffix",
        ).asFile
        val bindgenBinDir = rustToolsDir.get().dir("wasm-bindgen-cli/bin").asFile
        environment(
            "PATH",
            wasmPackBin.parentFile.prependToPath(
                bindgenBinDir.prependToPath(System.getenv("PATH")),
            ),
        )
        executable(wasmPackBin)
        setArgs(
            listOf(
                "build", "veles-crypto", "--target", "web", "--release",
                "--mode", "no-install", "--no-opt",
                "--out-dir", wasmPackageDir.asFile.absolutePath,
                "--", "--locked",
            ),
        )
    }
}

val rustNativeTest = tasks.register<Exec>("rustNativeTest") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    commandLine("cargo", "test", "--workspace", "--locked")
}

val rustWasmSmoke = tasks.register<Exec>("rustWasmSmoke") {
    group = "rust"
    dependsOn(rustWasm)
    inputs.file(rustCrateDir.file("tests/wasm-smoke.mjs"))
    inputs.dir(wasmPackageDir)
    doFirst {
        requireOnPath(
            "node",
            "Node >=22.0.0",
            "Install Node.js >=22.0.0 (declared in web-extension/package.json engines.node)",
        )
        val version = providers.exec {
            commandLine("node", "--version")
        }.standardOutput.asText.get().trim()
        val major = version.removePrefix("v").substringBefore('.').toIntOrNull()
        check(major != null && major >= 22) {
            "Node >=22 is required by web-extension/package.json; found '$version'"
        }
    }
    commandLine("node", rustCrateDir.file("tests/wasm-smoke.mjs").asFile)
}

val rustTest = tasks.register("rustTest") {
    group = "rust"
    description = "Runs native Rust and generated web-target WASM tests."
    dependsOn(rustNativeTest, rustWasmSmoke)
}
```

- [ ] **Step 6: Build and execute the generated web-target binding**

Run:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew rustWasm rustTest
```

Expected: PASS. Confirm `web-extension/rust-wasm/pkg/veles_crypto.js`, `veles_crypto_bg.wasm`, and TypeScript declarations exist; the Node test must execute the generated `--target web` binding.

- [ ] **Step 7: Verify hidden-download prevention**

Run:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew --info rustWasm
```

Expected: logs show the local `build/rust-tools/wasm-bindgen-cli/bin/wasm-bindgen`; no Binaryen/`wasm-opt` or wasm-bindgen binary download occurs during `rustWasm`.

- [ ] **Step 8: Commit the WASM/Gradle slice**

```bash
git add build.gradle.kts web-extension/.gitignore \
  rust/veles-crypto/tests/wasm-smoke.mjs
git commit -m "feat(otp-01/1b): build and test Rust WASM binding"
```

---

### Task 3: Integrate JNI into every Android APK and prove the runtime boundary

**Files:**
- Modify: `rust/veles-crypto/src/lib.rs`
- Modify: `gradle/libs.versions.toml`
- Modify: `build.gradle.kts`
- Modify: `app/build.gradle.kts`
- Create: `app/src/main/java/me/nagaev/veles/crypto/VelesCrypto.kt`
- Create: `app/src/androidTest/java/me/nagaev/veles/crypto/VelesCryptoInstrumentedTest.kt`

**Interfaces:**
- Consumes: Task 1's `reverse_bytes`; Task 2's exact local `cargo-ndk` installation.
- Produces: `VelesCrypto.reverseBytes(ByteArray): ByteArray`, JNI symbol `Java_me_nagaev_veles_crypto_VelesCrypto_reverseBytes`, root task `rustJni`, generated three-ABI JNI tree consumed by all app variants.

- [ ] **Step 1: Pin NDK r29 and write the failing Android runtime test**

Add under `[versions]` in `gradle/libs.versions.toml`:

```toml
ndk = "29.0.14206865"
```

Create `app/src/androidTest/java/me/nagaev/veles/crypto/VelesCryptoInstrumentedTest.kt`:

```kotlin
package me.nagaev.veles.crypto

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertArrayEquals
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class VelesCryptoInstrumentedTest {
    @Test
    fun reversesBinaryBytesThroughJni() {
        val input = byteArrayOf(0x00, 0x80.toByte(), 0xff.toByte(), 0x2a)
        val expected = byteArrayOf(0x2a, 0xff.toByte(), 0x80.toByte(), 0x00)

        assertArrayEquals(expected, VelesCrypto.reverseBytes(input))
    }

    @Test
    fun reversesEmptyBytesThroughJni() {
        assertArrayEquals(byteArrayOf(), VelesCrypto.reverseBytes(byteArrayOf()))
    }
}
```

This uses `org.junit.Assert.assertArrayEquals` and `org.junit.Test`, both available through the existing `androidTestImplementation(libs.junit)` dependency; no additional androidTest test framework is required.

- [ ] **Step 2: Compile the Android test and verify RED**

Run:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew :app:compileDebugAndroidTestKotlin
```

Expected: FAIL because `VelesCrypto` is unresolved.

- [ ] **Step 3: Add the minimal Kotlin JNI wrapper**

Create `app/src/main/java/me/nagaev/veles/crypto/VelesCrypto.kt`:

```kotlin
package me.nagaev.veles.crypto

internal object VelesCrypto {
    init {
        System.loadLibrary("veles_crypto")
    }

    @JvmStatic
    external fun reverseBytes(input: ByteArray): ByteArray
}
```

- [ ] **Step 4: Add the Android-only JNI shim with a contained unwind boundary**

Append target-gated JNI code to `rust/veles-crypto/src/lib.rs`. Keep the shared `reverse_bytes` unchanged:

```rust
#[cfg(target_os = "android")]
mod android {
    use std::panic::{AssertUnwindSafe, catch_unwind};
    use std::ptr::null_mut;

    use jni::JNIEnv;
    use jni::objects::{JByteArray, JClass};
    use jni::sys::jbyteArray;

    use super::reverse_bytes;

    fn throw_if_clear(env: &mut JNIEnv<'_>, message: &str) {
        if !matches!(env.exception_check(), Ok(true)) {
            let _ = env.throw_new("java/lang/RuntimeException", message);
        }
    }

    #[unsafe(no_mangle)]
    pub extern "system" fn Java_me_nagaev_veles_crypto_VelesCrypto_reverseBytes(
        mut env: JNIEnv<'_>,
        _class: JClass<'_>,
        input: JByteArray<'_>,
    ) -> jbyteArray {
        match catch_unwind(AssertUnwindSafe(|| {
            let input = env.convert_byte_array(&input)?;
            env.byte_array_from_slice(&reverse_bytes(&input))
        })) {
            Ok(Ok(output)) => output.into_raw(),
            Ok(Err(_)) => {
                throw_if_clear(&mut env, "Veles crypto JNI conversion failed");
                null_mut()
            }
            Err(_) => {
                throw_if_clear(&mut env, "Veles crypto JNI operation failed");
                null_mut()
            }
        }
    }
}
```

If `jni` 0.22.4 reports a concrete API signature difference, preserve the exact behavior above: borrowed input conversion, new output array, pending-exception preservation, generic messages, null on failure, and `catch_unwind`; do not use unchecked raw pointer reads. After editing the Rust source, normalize formatting:

```bash
CARGO_TARGET_DIR="$PWD/build/rust/target" cargo fmt --all --manifest-path rust/Cargo.toml
```

- [ ] **Step 5: Configure the app NDK, ABI filters, generated source set, and merge dependency**

In `app/build.gradle.kts`, inside `android`:

```kotlin
ndkVersion = libs.versions.ndk.get()

defaultConfig {
    // Existing values remain.
    ndk {
        abiFilters += setOf("arm64-v8a", "armeabi-v7a", "x86_64")
    }
}

sourceSets.getByName("main").jniLibs.srcDir(
    layout.buildDirectory.dir("generated/jniLibs"),
)
```

After the `android` block:

```kotlin
tasks.matching { it.name.matches(Regex("merge.*JniLibFolders")) }.configureEach {
    dependsOn(rootProject.tasks.named("rustJni"))
}
```

- [ ] **Step 6: Add exact AGP NDK handoff and `rustJni`**

In root `build.gradle.kts`, obtain AGP's provider without resolving it during root configuration:

```kotlin
val appNdkDirectory = objects.directoryProperty()
project(":app").plugins.withId("com.android.application") {
    val androidComponents = project(":app").extensions
        .getByType(ApplicationAndroidComponentsExtension::class.java)
    appNdkDirectory.set(androidComponents.sdkComponents.ndkDirectory)
}
```

Register `rustJni`:

```kotlin
val rustJniOutput = project(":app").layout.buildDirectory.dir("generated/jniLibs")
val rustJni = tasks.register<Exec>("rustJni") {
    group = "rust"
    description = "Builds locked release JNI libraries for the approved Android ABIs."
    dependsOn(verifyCargoNdk)
    inputs.files(
        fileTree(rustCrateDir) { include("Cargo.toml", "src/**/*.rs") },
        rustDir.file("Cargo.toml"),
        rustDir.file("Cargo.lock"),
        rustDir.file("rust-toolchain.toml"),
        rustToolsFile,
    )
    inputs.property("ndkVersion", libs.versions.ndk.get())
    outputs.dir(rustJniOutput)
    workingDir(rustDir)
    doFirst {
        val ndkDir = appNdkDirectory.get().asFile
        val expected = libs.versions.ndk.get()
        check(ndkDir.isDirectory) {
            "Android NDK $expected is not installed. " +
                "Install it with sdkmanager \"ndk;$expected\"."
        }
        val properties = Properties().apply {
            ndkDir.resolve("source.properties").inputStream().use(::load)
        }
        val actual = properties.getProperty("Pkg.Revision")
        check(actual == expected) {
            "Expected Android NDK $expected at $ndkDir, found '$actual'. " +
                "Install the pinned version with sdkmanager \"ndk;$expected\"."
        }
        delete(rustJniOutput)
        val cargoNdkBinDir = rustToolsDir.get().dir("cargo-ndk/bin").asFile
        environment("PATH", cargoNdkBinDir.prependToPath(System.getenv("PATH")))
        environment("ANDROID_NDK_HOME", ndkDir)
        environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
        environment("CARGO_PROFILE_RELEASE_PANIC", "unwind")
    }
    commandLine(
        "cargo", "ndk", "--platform", "33",
        "--target", "arm64-v8a",
        "--target", "armeabi-v7a",
        "--target", "x86_64",
        "--output-dir", rustJniOutput.get().asFile,
        "build", "--workspace", "--release", "--locked",
    )
}
```

Use cargo-ndk 4.1.2's actual long option names from `cargo ndk --help`; if it prints `-p/-t/-o` as the canonical accepted form, substitute those exact aliases without changing platform 33, targets, output directory, release, or locked semantics.

- [ ] **Step 7: Install NDK r29 locally and build all JNI artifacts**

Run once if r29 is absent:

```bash
"$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager" "ndk;29.0.14206865"
```

Then run:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew rustJni :app:assembleDebug :app:assembleDebugAndroidTest
```

Expected: PASS. Verify only these generated files exist:

```text
app/build/generated/jniLibs/arm64-v8a/libveles_crypto.so
app/build/generated/jniLibs/armeabi-v7a/libveles_crypto.so
app/build/generated/jniLibs/x86_64/libveles_crypto.so
```

- [ ] **Step 8: Execute the JNI test on an emulator/device**

Run:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew connectedDebugAndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.crypto.VelesCryptoInstrumentedTest
```

Expected: PASS on an API 33+ x86_64 emulator/device. If no device is available locally, the compile/package checks in Step 7 must pass and Task 6's instrumented CI job remains the mandatory runtime gate; do not delete or downgrade the test.

- [ ] **Step 9: Commit the JNI/APK slice**

```bash
git add rust/veles-crypto/src/lib.rs gradle/libs.versions.toml \
  build.gradle.kts app/build.gradle.kts \
  app/src/main/java/me/nagaev/veles/crypto/VelesCrypto.kt \
  app/src/androidTest/java/me/nagaev/veles/crypto/VelesCryptoInstrumentedTest.kt
git commit -m "feat(otp-01/1b): package Rust JNI libraries in Android"
```

---

### Task 4: Complete Rust quality tasks and clean lifecycle

**Files:**
- Modify: `build.gradle.kts`

**Interfaces:**
- Consumes: Tasks 1–3 workspace, exact tool installers, `rustWasm`, `rustJni`, and app NDK provider.
- Produces: public `rustFormat`, `rustLint`, `rustTest`, `rustNdkVersion`; root `check`/`clean` integration with target-complete lint and generated-output removal.

- [ ] **Step 1: Demonstrate missing lifecycle behavior**

Run:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew tasks --group rust
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew clean
```

Expected before this task: `rustFormat`, `rustLint`, or `rustNdkVersion` is absent; after generating WASM, `web-extension/rust-wasm/pkg/` survives root clean, proving the lifecycle is incomplete.

- [ ] **Step 2: Add format and target-complete lint tasks**

Add to `build.gradle.kts`:

```kotlin
val rustFormat = tasks.register<Exec>("rustFormat") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    commandLine("cargo", "fmt", "--all", "--", "--check")
}

val rustLintHost = tasks.register<Exec>("rustLintHost") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    commandLine(
        "cargo", "clippy", "--workspace", "--all-targets", "--locked",
        "--", "-D", "warnings",
    )
}

val rustLintWasm = tasks.register<Exec>("rustLintWasm") {
    group = "rust"
    dependsOn(rustToolchainCheck)
    workingDir(rustDir)
    environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
    environment("CARGO_PROFILE_RELEASE_PANIC", "abort")
    commandLine(
        "cargo", "clippy", "--workspace", "--target", "wasm32-unknown-unknown",
        "--locked", "--", "-D", "warnings",
    )
}

val rustLintAndroid = tasks.register<Exec>("rustLintAndroid") {
    group = "rust"
    dependsOn(verifyCargoNdk)
    workingDir(rustDir)
    doFirst {
        val ndkDir = appNdkDirectory.get().asFile
        val cargoNdkBinDir = rustToolsDir.get().dir("cargo-ndk/bin").asFile
        environment("PATH", cargoNdkBinDir.prependToPath(System.getenv("PATH")))
        environment("ANDROID_NDK_HOME", ndkDir)
        environment("CARGO_TARGET_DIR", rustTargetDir.get().asFile)
        environment("CARGO_PROFILE_RELEASE_PANIC", "unwind")
    }
    commandLine(
        "cargo", "ndk", "--platform", "33", "--target", "x86_64",
        "clippy", "--workspace", "--all-targets", "--locked",
        "--", "-D", "warnings",
    )
}

val rustLint = tasks.register("rustLint") {
    group = "rust"
    description = "Runs denied-warning Clippy checks for host, WASM, and Android JNI."
    dependsOn(rustLintHost, rustLintWasm, rustLintAndroid)
}
```

Factor the exact NDK `source.properties` validation from `rustJni` into a shared helper and call it from `rustLintAndroid`; do not duplicate weaker validation. `rustLintAndroid` sets `CARGO_PROFILE_RELEASE_PANIC=unwind` intentionally, matching `rustJni`, because it lints Android-only code under the same panic strategy the Android release build uses; this does not violate the constraint that only Android-scoped tasks set unwind.

- [ ] **Step 3: Add lifecycle and NDK print wiring**

Add:

```kotlin
tasks.register("rustNdkVersion") {
    group = "rust"
    description = "Prints the pinned Android NDK version for CI provisioning."
    doLast { println(libs.versions.ndk.get()) }
}

tasks.named("check") {
    dependsOn(rustFormat, rustLint, rustTest)
}

tasks.named<Delete>("clean") {
    dependsOn(":app:clean")
    delete(
        layout.projectDirectory.dir("web-extension/dist"),
        wasmPackageDir,
    )
}
```

- [ ] **Step 4: Run the complete quality gate**

Run:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew rustFormat rustLint rustTest
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew check
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew -q rustNdkVersion
```

Expected: all quality tasks PASS; NDK print output is exactly `29.0.14206865` (apart from unavoidable Gradle warnings on stderr).

- [ ] **Step 5: Prove clean removes all generated paths and APK assembly reconstructs JNI**

Run:

```bash
npm ci --prefix web-extension && npm run build --prefix web-extension
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew rustInstall rustWasm :app:assembleDebug
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew clean
```

Expected: `build/`, `app/build/`, `web-extension/dist/`, and `web-extension/rust-wasm/pkg/` are absent. Then run:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew :app:assembleDebug
```

Expected: PASS from no generated JNI state; the three generated `libveles_crypto.so` files reappear under `app/build/generated/jniLibs/`.

- [ ] **Step 6: Commit the lifecycle slice**

```bash
git add build.gradle.kts
git commit -m "build(otp-01/1b): add Rust quality and clean lifecycle"
```

---

### Task 5: Adopt and guard the exact MV3 WASM CSP

**Files:**
- Modify: `web-extension/test/smoke.test.ts`
- Modify: `web-extension/test/manifest-guard.test.ts`
- Modify: `web-extension/src/manifest.ts`

**Interfaces:**
- Consumes: merged 1a manifest generator and exact guard.
- Produces: exact CSP `script-src 'self' 'wasm-unsafe-eval'; object-src 'self'` in source and built manifest, without importing the WASM package.

- [ ] **Step 1: Change tests first to the approved exact CSP**

In both CSP assertions, use exactly:

```typescript
expect(buildExtensionManifest().content_security_policy).toEqual({
    extension_pages: "script-src 'self' 'wasm-unsafe-eval'; object-src 'self'",
});
```

Rename the smoke test description so it says 1b permits only `wasm-unsafe-eval`; retain assertions that ordinary `'unsafe-eval'` and remote sources are absent through exact map equality.

- [ ] **Step 2: Run extension tests and verify RED**

Run:

```bash
npm test --prefix web-extension
```

Expected: FAIL because `src/manifest.ts` still emits the 1a CSP.

- [ ] **Step 3: Update the manifest generator minimally**

Change only `content_security_policy.extension_pages` in `web-extension/src/manifest.ts`:

```typescript
content_security_policy: {
    extension_pages: "script-src 'self' 'wasm-unsafe-eval'; object-src 'self'",
},
```

Do not add a WASM import, permission, host permission, or any other CSP directive.

- [ ] **Step 4: Verify source and built manifest guards**

Run:

```bash
npm run format:check --prefix web-extension
npm run lint --prefix web-extension
npm run typecheck --prefix web-extension
npm test --prefix web-extension
npm run build --prefix web-extension && npm run test:bundle --prefix web-extension
```

Expected: all commands PASS and `dist/manifest.json` contains the exact 1b CSP.

- [ ] **Step 5: Commit the CSP slice**

```bash
git add web-extension/src/manifest.ts web-extension/test/smoke.test.ts \
  web-extension/test/manifest-guard.test.ts
git commit -m "build(otp-01/1b): permit local WASM in extension CSP"
```

---

### Task 6: Add APK assertions and wire Rust into CI/release jobs

**Files:**
- Create: `rust/scripts/verify-apk-jni.sh`
- Create: `rust/scripts/assert-clean.sh`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/release.yml`
- Modify: `.github/workflows/release-build.yml`

**Interfaces:**
- Consumes: all Gradle tasks and outputs from Tasks 1–5.
- Produces: exact APK ABI/Veles-JNI assertions; dedicated Rust CI; Rust/NDK provisioning for instrumented and release APK builds.

- [ ] **Step 1: Write an APK assertion that fails against the pre-1b shape**

Create executable `rust/scripts/verify-apk-jni.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

APK="${1:?usage: $0 <apk>}"
if [ ! -f "$APK" ]; then
  echo "ERROR: APK not found: $APK" >&2
  exit 2
fi

JAR="${JAVA_HOME:?JAVA_HOME must point to JDK 21}/bin/jar"
mapfile -t ACTUAL_ABIS < <("$JAR" tf "$APK" | awk -F/ '$1 == "lib" {print $2}' | sort -u)
EXPECTED_ABIS=(arm64-v8a armeabi-v7a x86_64)
if ! diff -u <(printf '%s\n' "${EXPECTED_ABIS[@]}") <(printf '%s\n' "${ACTUAL_ABIS[@]}"); then
  echo "ERROR: APK native ABI set differs from the approved set." >&2
  exit 1
fi

mapfile -t ACTUAL_VELES < <("$JAR" tf "$APK" | awk '/^lib\/[^/]+\/libveles_crypto\.so$/ {print}' | sort)
EXPECTED_VELES=(
  lib/arm64-v8a/libveles_crypto.so
  lib/armeabi-v7a/libveles_crypto.so
  lib/x86_64/libveles_crypto.so
)
if ! diff -u <(printf '%s\n' "${EXPECTED_VELES[@]}") <(printf '%s\n' "${ACTUAL_VELES[@]}"); then
  echo "ERROR: APK Veles JNI entry set differs from the approved set." >&2
  exit 1
fi
```

Mark it executable. Before rebuilding with Task 3 changes, run it against any baseline APK and expect failure due to `x86` and missing Veles entries. After Task 3, run:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" rust/scripts/verify-apk-jni.sh \
  app/build/outputs/apk/debug/app-debug.apk
```

Expected: PASS.

- [ ] **Step 2: Add the generated-output absence assertion**

Create executable `rust/scripts/assert-clean.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Gradle recreates build/reports/problems/ after clean; that is the only
# known exception and is excluded from this assertion.
for relative in build/rust build/rust-tools web-extension/dist web-extension/rust-wasm/pkg; do
  if [ -e "$ROOT/$relative" ]; then
    echo "ERROR: generated path survived clean: $relative" >&2
    exit 1
  fi
done
# app/build is allowed to contain reports/problems only after clean; assert
# no generated JNI or APK outputs remain.
for relative in app/build/generated/jniLibs app/build/outputs/apk; do
  if [ -e "$ROOT/$relative" ]; then
    echo "ERROR: generated path survived clean: $relative" >&2
    exit 1
  fi
done
```

Run it before clean and expect failure, then run `./gradlew clean` and rerun it expecting PASS.

- [ ] **Step 3: Add common Rust/NDK/cache setup steps to every APK-producing job**

After Java/Gradle setup in the CI instrumented job and both release build jobs, add:

```yaml
- name: Activate pinned Rust toolchain
  working-directory: rust
  run: rustup show active-toolchain
- name: Install pinned Android NDK
  run: |
    NDK_VERSION="$(./gradlew -q rustNdkVersion)"
    sdkmanager "ndk;$NDK_VERSION"
- uses: actions/cache@v5
  with:
    path: build/rust-tools
    key: rust-tools-${{ runner.os }}-${{ runner.arch }}-${{ hashFiles('rust/rust-toolchain.toml', 'rust/toolchain-tools.toml', 'rust/Cargo.lock') }}
```

Do not add Node to release or instrumented jobs. Their APK tasks depend only on cargo-ndk, not WASM tools.

- [ ] **Step 4: Add the dedicated `rust` CI job**

Append a `rust` job to `.github/workflows/ci.yml` with this sequence:

```yaml
rust:
  name: rust
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v7
    - uses: actions/setup-java@v5
      with:
        distribution: temurin
        java-version-file: .java-version
    - uses: actions/setup-node@v6
      with:
        node-version: '22'
        cache: npm
        cache-dependency-path: web-extension/package-lock.json
    - uses: gradle/actions/setup-gradle@v6
    - name: Activate pinned Rust toolchain
      working-directory: rust
      run: rustup show active-toolchain
    - name: Install pinned Android NDK
      run: |
        NDK_VERSION="$(./gradlew -q rustNdkVersion)"
        sdkmanager "ndk;$NDK_VERSION"
    - uses: actions/cache@v5
      with:
        path: build/rust-tools
        key: rust-tools-${{ runner.os }}-${{ runner.arch }}-${{ hashFiles('rust/rust-toolchain.toml', 'rust/toolchain-tools.toml', 'rust/Cargo.lock') }}
    - run: ./gradlew rustFormat rustLint rustTest :app:assembleDebug
    - run: rust/scripts/verify-apk-jni.sh app/build/outputs/apk/debug/app-debug.apk
    - run: npm ci && npm run build
      working-directory: web-extension
    - run: ./gradlew clean
    - run: rust/scripts/assert-clean.sh
    - run: ./gradlew :app:assembleDebug
    - run: rust/scripts/verify-apk-jni.sh app/build/outputs/apk/debug/app-debug.apk
    - name: Refill complete Rust tool cache
      run: ./gradlew rustInstall
```

The existing instrumented job remains the runtime JNI gate because it runs `connectedDebugAndroidTest`; do not create a second emulator job.

- [ ] **Step 5: Execute the complete CI sequence locally**

Run:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew rustFormat rustLint rustTest :app:assembleDebug
JAVA_HOME="$HOME/.jdk/zulu21" rust/scripts/verify-apk-jni.sh \
  app/build/outputs/apk/debug/app-debug.apk
npm ci --prefix web-extension && npm run build --prefix web-extension
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew clean
rust/scripts/assert-clean.sh
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew :app:assembleDebug
JAVA_HOME="$HOME/.jdk/zulu21" rust/scripts/verify-apk-jni.sh \
  app/build/outputs/apk/debug/app-debug.apk
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew rustInstall
```

Expected: all commands PASS in order.

- [ ] **Step 6: Commit CI/release integration**

```bash
git add rust/scripts/verify-apk-jni.sh rust/scripts/assert-clean.sh \
  .github/workflows/ci.yml .github/workflows/release.yml \
  .github/workflows/release-build.yml
git commit -m "ci(otp-01/1b): build and verify Rust JNI and WASM"
```

---

### Task 7: Extend the pinned Docker APK verifier for Rust and NDK r29

**Files:**
- Modify: `verify/Dockerfile`
- Modify: `verify/verify.sh`
- Create: `.dockerignore`

**Interfaces:**
- Consumes: `rust/rust-toolchain.toml`, NDK version catalog pin, and automatic `assembleRelease -> rustJni` dependency.
- Produces: reference APK image containing exact rustup/Rust targets/NDK; host-side pin consistency checks and root Docker context.

- [ ] **Step 1: Add pin checks that fail against the current Dockerfile**

In `verify/verify.sh`, after the existing JDK consistency check, parse the NDK pin and require the Dockerfile to carry it:

```bash
NDK_VERSION="$(awk -F'"' '/^ndk = / {print $2}' "$REPO_ROOT/gradle/libs.versions.toml")"
if [ -z "$NDK_VERSION" ] || ! grep -Fq "ARG NDK_VERSION=$NDK_VERSION" "$SCRIPT_DIR/Dockerfile"; then
  echo "ERROR: verify/Dockerfile NDK does not match libs.versions.toml ($NDK_VERSION)." >&2
  exit 2
fi
if ! grep -Fq 'COPY rust/rust-toolchain.toml' "$SCRIPT_DIR/Dockerfile"; then
  echo "ERROR: verify/Dockerfile does not consume rust/rust-toolchain.toml." >&2
  exit 2
fi
```

Run:

```bash
NDK_VERSION="$(awk -F'"' '/^ndk = / {print $2}' gradle/libs.versions.toml)"
grep -Fq "ARG NDK_VERSION=$NDK_VERSION" verify/Dockerfile && echo "NDK pin present" || echo "NDK pin missing (expected RED)"
grep -Fq 'COPY rust/rust-toolchain.toml' verify/Dockerfile && echo "Rust pin present" || echo "Rust pin missing (expected RED)"
```

Expected before Dockerfile changes: both print "missing (expected RED)". After Task 7 changes, both print "present".

- [ ] **Step 2: Pin rustup and install the exact Rust toolchain in the image**

In `verify/Dockerfile`, extend the existing apt-get install line to include `build-essential` (needed for `cargo install` of auxiliary CLIs from source), then add the Rust toolchain:

```dockerfile
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl unzip ca-certificates python3-pip build-essential \
 && rm -rf /var/lib/apt/lists/*
```

Then add the Rust toolchain:

```dockerfile
ENV RUSTUP_HOME=/opt/rustup
ENV CARGO_HOME=/opt/cargo
ENV PATH=${CARGO_HOME}/bin:${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${ANDROID_HOME}/build-tools/36.0.0:${PATH}

ARG RUSTUP_VERSION=1.29.0
ARG RUSTUP_SHA256=4acc9acc76d5079515b46346a485974457b5a79893cfb01112423c89aeb5aa10
RUN curl -fsSL -o /tmp/rustup-init \
      "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/x86_64-unknown-linux-gnu/rustup-init" \
 && echo "${RUSTUP_SHA256}  /tmp/rustup-init" | sha256sum -c - \
 && chmod +x /tmp/rustup-init \
 && /tmp/rustup-init -y --no-modify-path --profile minimal --default-toolchain none \
 && rm /tmp/rustup-init

COPY rust/rust-toolchain.toml /tmp/veles-rust/rust-toolchain.toml
RUN cd /tmp/veles-rust \
 && rustup show active-toolchain \
 && rustc --version \
 && rm -rf /tmp/veles-rust
```

Replace the earlier `ENV PATH=...` rather than adding a conflicting second value.

- [ ] **Step 3: Install NDK r29 in the reference image**

Change the SDK package step to:

```dockerfile
ARG NDK_VERSION=29.0.14206865
RUN yes | sdkmanager --licenses > /dev/null \
 && sdkmanager \
      "platform-tools" \
      "platforms;android-35" \
      "build-tools;36.0.0" \
      "ndk;${NDK_VERSION}"
```

Change the script copy because Task 7 will make the repository root the Docker context:

```dockerfile
COPY verify/verify-inner.sh /usr/local/bin/verify-inner.sh
```

Create `.dockerignore` at the repository root to keep the Docker build context small and deterministic:

```dockerignore
.git
.gradle
.idea
build
app/build
captures
*.iml
node_modules
web-extension/node_modules
web-extension/dist
web-extension/rust-wasm/pkg
.cxx
.externalNativeBuild
.kotlin
local.properties
```

- [ ] **Step 4: Build from repository root context**

In `verify/verify.sh`, replace the image build command with:

```bash
docker build -t veles-verify -f "$SCRIPT_DIR/Dockerfile" "$REPO_ROOT" || {
  echo "ERROR: failed to build reference image." >&2
  exit 2
}
```

- [ ] **Step 5: Verify pin consistency and build the image**

Run:

```bash
verify/verify.sh 2>/dev/null || test "$?" -eq 2
docker build -t veles-verify-1b -f verify/Dockerfile .
```

Expected: usage exits 2 without a pin-consistency error; Docker build succeeds and logs Rust 1.98.0 plus NDK r29 installation. If Docker is unavailable, record that exact environment limitation; CI/release verification remains required before completion.

- [ ] **Step 6: Run the existing zero-trust APK comparison when Docker is available**

Build a release APK, then run:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew assembleRelease
VELES_REPO_URL="$PWD" verify/verify.sh \
  app/build/outputs/apk/release/app-release-unsigned.apk \
  "$(git rev-parse HEAD)"
```

Expected: `VERIFIED` after rebuilding JNI with the pinned toolchain in Docker. If this reveals native path/timestamp drift, fix reproducibility in this task; do not defer a broken existing verifier to 1d.

- [ ] **Step 7: Commit verifier integration**

```bash
git add verify/Dockerfile verify/verify.sh .dockerignore
git commit -m "build(otp-01/1b): pin Rust and NDK in APK verifier"
```

---

### Task 8: Document entry points and run final acceptance

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `docs/reproducible-builds.md`

**Interfaces:**
- Consumes: every implemented 1b command and pin location.
- Produces: accurate developer/release documentation and final acceptance evidence.

- [ ] **Step 1: Add exact developer commands to `CLAUDE.md`**

Under Build & Test Commands, add:

```bash
# Install/verify exact auxiliary Rust build CLIs under build/rust-tools/
./gradlew rustInstall

# Format, lint host+WASM+Android targets, and run native+WASM tests
./gradlew rustFormat rustLint rustTest

# Build three Android JNI ABIs into app/build/generated/jniLibs/
./gradlew rustJni

# Build web-target WASM into web-extension/rust-wasm/pkg/
./gradlew rustWasm
```

State that clean APK assembly now requires rustup/cargo and NDK r29, while Node 22 is required only for `rustTest`/WASM and extension commands.

- [ ] **Step 2: Add the sub-project map to `README.md`**

Add a concise `OTP external-device toolchains` section listing:

```markdown
- `web-extension/` — npm/Vite MV3 extension and deterministic package.
- `rust/` — shared Rust core with Android JNI and browser WASM bindings.
- `native-bridge/` — reserved for OTP-01 sub-project 1c.
- `verify/` — reproducible APK verifier; broader toolchain verification lands in 1d.
```

Do not claim 1c/1d are implemented.

- [ ] **Step 3: Extend the reproducible-build pin table**

In `docs/reproducible-builds.md`, add rows:

```markdown
| Rust compiler/components/targets | `rust/rust-toolchain.toml` |
| Rust dependencies | `rust/Cargo.lock` plus exact direct pins in crate manifests |
| Rust auxiliary CLIs | `rust/toolchain-tools.toml` (`cargo install --locked`) |
| Android NDK | `gradle/libs.versions.toml` `ndk`, propagated to AGP and `verify/Dockerfile` |
```

Explain generated output boundaries, the three ABI filters, local CLI caches under `build/`, and the named WASM exception.

- [ ] **Step 4: Run all source-level quality gates**

Run:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew spotlessCheck detekt testDebugUnitTest
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew rustFormat rustLint rustTest
npm ci --prefix web-extension
npm run format:check --prefix web-extension
npm run lint --prefix web-extension
npm run typecheck --prefix web-extension
npm test --prefix web-extension
npm run build --prefix web-extension && npm run test:bundle --prefix web-extension
```

Expected: all commands PASS.

- [ ] **Step 5: Run clean reconstruction and APK artifact acceptance**

Run:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew rustInstall rustWasm :app:assembleDebug
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew clean
rust/scripts/assert-clean.sh
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew :app:assembleDebug
JAVA_HOME="$HOME/.jdk/zulu21" rust/scripts/verify-apk-jni.sh \
  app/build/outputs/apk/debug/app-debug.apk
```

Expected: clean assertion and exact ABI/JNI assertions PASS.

- [ ] **Step 6: Run Android JNI runtime acceptance**

Run on an API 33+ x86_64 emulator/device:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew connectedDebugAndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.crypto.VelesCryptoInstrumentedTest
```

Expected: PASS. This is mandatory CI evidence even if no local device exists.

- [ ] **Step 7: Run release and reproducibility acceptance**

Run:

```bash
JAVA_HOME="$HOME/.jdk/zulu21" ./gradlew assembleRelease
JAVA_HOME="$HOME/.jdk/zulu21" rust/scripts/verify-apk-jni.sh \
  app/build/outputs/apk/release/app-release-unsigned.apk
VELES_REPO_URL="$PWD" verify/verify.sh \
  app/build/outputs/apk/release/app-release-unsigned.apk \
  "$(git rev-parse HEAD)"
```

Expected: release build and APK assertions PASS; Docker verifier reports `VERIFIED` when Docker is available.

- [ ] **Step 8: Commit documentation**

```bash
git add CLAUDE.md README.md docs/reproducible-builds.md
git commit -m "docs(otp-01/1b): document Rust JNI and WASM builds"
```

- [ ] **Step 9: Inspect final scope and status**

Run:

```bash
git status --short
git diff --check HEAD~8..HEAD
git log --oneline -10
```

Expected: only intentional changes exist; no generated `.so`, WASM package, `target/`, tool binary, APK, secret, or unrelated source file is tracked.
