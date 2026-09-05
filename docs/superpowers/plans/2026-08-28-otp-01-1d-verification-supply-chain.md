# OTP-01 1d Verification And Supply Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reproducible component builds, byte comparison, checksums, SBOMs, license and remote-code enforcement, and target-native CI evidence that closes OTP-01.

**Architecture:** Existing npm, Gradle, Cargo, and Tauri commands remain artifact producers. Focused Node ESM libraries implement cross-platform manifest, metadata, policy, and comparison logic; thin shell/PowerShell wrappers own environment setup. Reusable `build-<component>.yml` workflows verify the artifacts they produce, while `release-build.yml` composes them and `verify-all.sh` remains a local-only orchestrator.

**Tech Stack:** Bash, PowerShell, Node.js 26.8.1/npm 11.19.0, Node built-in test runner, Gradle Kotlin DSL, Docker, Rust 1.98.0, cargo-deny 0.20.2, cargo-cyclonedx 0.5.9, CycloneDX npm 6.0.1, license-checker-rseidelsohn 5.0.1, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-28-otp-01-1d-verification-supply-chain-design.md`

## Global Constraints

- Keep `:app` as the only Gradle subproject.
- Keep `engines.node = ">=22.0.0"`; use exact Node 26.8.1/npm 11.19.0 only in reference and supply-chain environments.
- Use Rust 1.98.0, NDK 29.0.14206865, cargo-ndk 4.1.2, wasm-pack 0.15.0, wasm-bindgen-cli 0.2.127, and Tauri CLI 2.6.0.
- Use `windows-2025` x64 and `macos-26` ARM64; never use a `-latest` native label.
- On macOS select `/Applications/Xcode_26.6.app`, assert build `17F113`, and require SDK `macosx26.5`.
- Require non-empty `ImageOS`, `ImageVersion`, and `RUNNER_ARCH`; compare native bytes only when all three values and `SOURCE-COMMIT` match.
- Use committed lockfiles, `npm ci --ignore-scripts`, Cargo `--locked`, and offline product packaging after acquisition.
- Never use `npx`, a floating executable download, a runtime download fallback, signing credentials for native artifacts, or post-production normalization that hides byte drift.
- Keep generated product output under `build/`, except existing `web-extension/dist/` and `web-extension/rust-wasm/pkg/`.
- Preserve the exact bridge identity tuple from the spec.
- Exit 0 means match, exit 1 means policy/artifact mismatch, and exit 2 means usage/environment/pin/build/identity error.
- CI invokes component verifiers directly and never invokes `verify/verify-all.sh`.

---

### Task 1: Canonical Manifest And Native Metadata Core

**Files:**
- Create: `verify/package.json`
- Create: `verify/lib/exit-codes.mjs`
- Create: `verify/lib/checksum-manifest.mjs`
- Create: `verify/lib/filesystem-tree.mjs`
- Create: `verify/lib/native-metadata.mjs`
- Create: `verify/test/checksum-manifest.test.mjs`
- Create: `verify/test/native-metadata.test.mjs`

**Interfaces:**
- Produces: `createStandardManifest(root, paths) -> Promise<string>`
- Produces: `parseStandardManifest(text, options) -> Map<string,string>`
- Produces: `parseNativeManifest(text, options) -> { identity, checksums }`
- Produces: `verifyManifestTree(root, checksums) -> Promise<void>`
- Produces: `compareTrees(left, right, paths) -> Promise<void>`
- Produces: `createNativeMetadata(root, entries) -> Promise<string>` and `parseNativeMetadata(text) -> Array<Entry>`
- Produces: exported constants `EXIT_MATCH = 0`, `EXIT_MISMATCH = 1`, `EXIT_ERROR = 2`

- [ ] **Step 1: Add the test-only Node package entry point**

```json
{
  "name": "@veles/verification",
  "private": true,
  "type": "module",
  "engines": { "node": ">=26.8.1" },
  "scripts": { "test": "node --test test/*.test.mjs" }
}
```

- [ ] **Step 2: Write failing canonical-manifest tests**

Cover lowercase SHA-256, two-space separators, UTF-8 byte ordering, one LF, and rejection of CRLF, comments, self-reference, duplicates, unsorted input, absolute paths, empty/dot/traversal segments, missing files, and unexpected files. Use temporary directories and literal malicious paths; never normalize them before assertion.

- [ ] **Step 3: Run the tests and confirm failure**

Run: `node --test verify/test/checksum-manifest.test.mjs`

Expected: FAIL because `verify/lib/checksum-manifest.mjs` does not exist.

- [ ] **Step 4: Implement the minimal standard-manifest library**

Use `createHash('sha256')`, compare `Buffer.from(path, 'utf8')` values for byte order, validate paths before filesystem access, and throw errors carrying `.exitCode = EXIT_MISMATCH` for malformed evidence.

- [ ] **Step 5: Write failing native identity and metadata tests**

Require exactly these ordered headers:

```text
# ImageOS=...
# ImageVersion=...
# RUNNER_ARCH=...
```

Test JSONL file, directory, and symlink entries; four-digit octal modes; literal symlink target hashing; reordered/unknown/empty headers; duplicate paths; and deterministic path ordering.

- [ ] **Step 6: Implement native manifest and JSONL metadata functions**

Use `JSON.stringify` for each metadata line. Never resolve symlink targets. Reject hard links, devices, sockets, and metadata fields not allowed for the entry type.

- [ ] **Step 7: Run core tests**

Run: `node --test verify/test/checksum-manifest.test.mjs verify/test/native-metadata.test.mjs`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add verify/package.json verify/lib verify/test/checksum-manifest.test.mjs verify/test/native-metadata.test.mjs
git commit -m "feat(verify): add canonical manifest utilities"
```

### Task 2: Deterministic Web Package And Reference Verification

**Files:**
- Modify: `web-extension/scripts/package.mjs`
- Modify: `web-extension/package.json`
- Create: `web-extension/test/package-output.test.ts`
- Create: `verify/Dockerfile.web`
- Create: `verify/web-inner.sh`
- Create: `verify/verify-web.mjs`
- Create: `verify/verify-web.sh`
- Create: `verify/test/web-verifier.test.mjs`

**Interfaces:**
- Consumes: Task 1 standard manifest functions.
- Produces: `build/web-extension/{veles-extension-0.1.0.zip,veles-extension-0.1.0.zip.sha256,SHA256SUMS}`.
- Produces: `verify/verify-web.sh` with no arguments, operating on the clean current checkout.

- [ ] **Step 1: Write failing package-output tests**

Test that `npm run package` deletes a stale sentinel, emits exactly the three accepted files, hashes ZIP and sidecar in `SHA256SUMS`, excludes the manifest itself, and produces identical bytes on two runs.

- [ ] **Step 2: Confirm the producer tests fail**

Run from `web-extension/`: `npm ci --ignore-scripts && npm run build && npm test -- package-output.test.ts`

Expected: FAIL because stale output survives and `SHA256SUMS` is absent.

- [ ] **Step 3: Update `package.mjs` minimally**

Delete and recreate `build/web-extension` before ZIP creation. Await ZIP close, write the existing sidecar, then call Task 1's writer for exactly the ZIP basename and sidecar basename.

- [ ] **Step 4: Run all web tests and package checks**

Run from `web-extension/`:

```bash
npm run format:check
npm run lint
npm run typecheck
npm test
npm run build
npm run test:bundle
npm run package
```

Expected: PASS.

- [ ] **Step 5: Write failing web-verifier fixture tests**

Test candidate-manifest rejection before Docker runs, first differing path/hash output, and exit 2 for Node/npm pin drift or Docker failure.

- [ ] **Step 6: Add the pinned web image and inner build**

Use exactly:

```dockerfile
FROM node:26.8.1-bookworm-slim@sha256:367679cf9792759492a486e4aa4b421764d71a9546a6dae8aab81a99eb797b3e
```

Assert `node --version` is `v26.8.1` and `npm --version` is `11.19.0`. Copy a read-only mounted source tree into temporary storage, run locked install/quality/build checks during preparation, and execute package production in a second container with `--network=none`.

- [ ] **Step 7: Implement candidate/reference comparison**

`verify-web.mjs` validates both path sets before comparing bytes. `verify-web.sh` builds uniquely named temporary images, traps cleanup, maps byte drift to 1, and maps tool/pin/build failure to 2.

- [ ] **Step 8: Run web verification tests and integration**

Run:

```bash
node --test verify/test/web-verifier.test.mjs
verify/verify-web.sh
```

Expected: PASS with Docker available.

- [ ] **Step 9: Commit**

```bash
git add web-extension verify/Dockerfile.web verify/web-inner.sh verify/verify-web.mjs verify/verify-web.sh verify/test/web-verifier.test.mjs
git commit -m "feat(verify): add web reference comparison"
```

### Task 3: Rust JNI/WASM Package And Reference Verification

**Files:**
- Modify: `build.gradle.kts`
- Modify: `rust/scripts/assert-clean.sh`
- Create: `verify/test/rust-package.test.mjs`
- Create: `verify/Dockerfile.rust`
- Create: `verify/rust-inner.sh`
- Create: `verify/verify-rust.mjs`
- Create: `verify/verify-rust.sh`
- Create: `verify/test/rust-verifier.test.mjs`

**Interfaces:**
- Consumes: Task 1 standard manifest functions.
- Produces: Gradle task `rustPackage`.
- Produces: `build/rust-package/{jni,wasm,SHA256SUMS}` and reusable artifact shape `rust-jni-wasm`.
- Produces: `verify/verify-rust.sh` with no arguments.

- [ ] **Step 1: Write a failing package-shape test**

Require exactly three JNI paths and a complete WASM subtree copied from `web-extension/rust-wasm/pkg`. Assert an unexpected ABI, stale file, missing file, or checksum drift fails.

- [ ] **Step 2: Confirm failure**

Run: `node --test verify/test/rust-package.test.mjs`

Expected: FAIL because `build/rust-package` and `rustPackage` do not exist.

- [ ] **Step 3: Register `rustPackage`**

Add a root `Sync`-backed task in group `rust` depending on `rustJni` and `rustWasm`. Stage exact JNI paths plus the complete generated WASM package, reject unexpected JNI paths, replace the output tree, and emit `SHA256SUMS` after sync. Do not add an APK dependency on `rustPackage`.

- [ ] **Step 4: Run package and clean tests**

Run:

```bash
./gradlew rustPackage
node --test verify/test/rust-package.test.mjs
./gradlew clean
rust/scripts/assert-clean.sh
```

Expected: PASS.

- [ ] **Step 5: Write failing reference-verifier tests**

Cover JDK/NDK/Rust/Node/helper pin drift, invalid candidate paths, reference build failure, and byte mismatch classification.

- [ ] **Step 6: Add `Dockerfile.rust` and verifier wrappers**

Copy the exact Temurin `FROM` line and Android SDK/NDK pins from `verify/Dockerfile`. Install `https://nodejs.org/dist/v26.8.1/node-v26.8.1-linux-x64.tar.xz` only after verifying SHA-256 `3e301118d7df53d563b7e96c1617545f26e2f76f9724be668d6cab65c15dda5d`, then assert Node 26.8.1/npm 11.19.0. Consume `rust-toolchain.toml` and `toolchain-tools.toml`, run `./gradlew rustPackage` in a clean copy, and export only `build/rust-package`.

- [ ] **Step 7: Run Rust verification**

Run:

```bash
node --test verify/test/rust-verifier.test.mjs
verify/verify-rust.sh
```

Expected: PASS with Docker, Android SDK downloads, and sufficient disk space.

- [ ] **Step 8: Commit**

```bash
git add build.gradle.kts rust/scripts/assert-clean.sh verify/Dockerfile.rust verify/rust-inner.sh verify/verify-rust.mjs verify/verify-rust.sh verify/test/rust-package.test.mjs verify/test/rust-verifier.test.mjs
git commit -m "feat(rust): package and verify JNI WASM artifacts"
```

### Task 4: Pinned Supply-Chain Tools And License Policies

**Files:**
- Modify: `verify/package.json`
- Create: `verify/package-lock.json`
- Create: `verify/cargo-tools.toml`
- Create: `web-extension/.npmrc`
- Create: `native-bridge/.npmrc`
- Create: `licenses.toml`
- Create: `.license-policy.json`
- Create: `verify/install-script-policy.json`
- Create: `verify/cargo-build-script-policy.json`
- Create: `verify/lib/license-policy.mjs`
- Create: `verify/test/license-policy.test.mjs`
- Modify: `build.gradle.kts`

**Interfaces:**
- Produces: local npm binaries `cyclonedx-npm` 6.0.1 and `license-checker-rseidelsohn` 5.0.1.
- Produces: Gradle tasks `verifyCargoCyclonedx` and `verifyCargoDeny`, installing 0.5.9 and 0.20.2 under separate `build/verify-tools/` roots.
- Produces: `evaluateNpmLicense(expression, policy) -> { allowed, selectedLicense }`.

- [ ] **Step 1: Write failing policy tests**

Test every exact allow identifier from the spec, every deny family, `MIT OR CC0-1.0` allowed through MIT, `MIT AND CC0-1.0` denied, LLVM exception allowed, and unknown/missing/malformed expressions denied with package/version/text/policy diagnostics.

- [ ] **Step 2: Confirm failure**

Run: `node --test verify/test/license-policy.test.mjs`

Expected: FAIL because policy files and evaluator are absent.

- [ ] **Step 3: Add exact npm tools and lockfile**

Add exact dev dependencies, run `npm install --package-lock-only --ignore-scripts` in `verify/`, then run `npm ci --ignore-scripts`. Add `ignore-scripts=true` to both product `.npmrc` files.

- [ ] **Step 4: Add exact Cargo tool installation tasks**

Follow the existing isolated `cargo install --locked --version` pattern, using non-overlapping roots and exact restored-binary version checks. No system/global fallback is allowed.

- [ ] **Step 5: Implement root policies**

Encode the spec's exhaustive SPDX allow list, deny list, OR behavior, and zero initial LGPL exceptions. Record exact package/version plus command and referenced-file SHA-256 for web esbuild 0.25.12, bridge esbuild 0.28.2, and fsevents 2.3.3 after verifying each lockfile integrity tarball.

- [ ] **Step 6: Run policy and tool tests**

Run:

```bash
node --test verify/test/license-policy.test.mjs
./gradlew verifyCargoCyclonedx verifyCargoDeny
```

Expected: PASS with exact reported versions.

- [ ] **Step 7: Commit**

```bash
git add verify/package.json verify/package-lock.json verify/cargo-tools.toml verify/lib/license-policy.mjs verify/test/license-policy.test.mjs verify/*policy.json web-extension/.npmrc native-bridge/.npmrc licenses.toml .license-policy.json build.gradle.kts
git commit -m "build(supply-chain): pin audit tools and policies"
```

### Task 5: SBOM, License, Install-Script, And Remote-Code Enforcement

**Files:**
- Create: `verify/generate-sboms.sh`
- Create: `verify/verify-sboms.mjs`
- Create: `verify/check-npm-licenses.mjs`
- Create: `verify/check-npm-install-scripts.mjs`
- Create: `verify/check-cargo-build-scripts.mjs`
- Create: `verify/check-remote-code.mjs`
- Create: `verify/verify-supply-chain.sh`
- Create: `verify/test/sbom.test.mjs`
- Create: `verify/test/install-scripts.test.mjs`
- Create: `verify/test/remote-code.test.mjs`

**Interfaces:**
- Consumes: Task 4 tools and policy files.
- Produces: `build/sbom/{web-extension,rust,native-bridge}.cdx.json`.
- Produces: named reports under `build/verification/supply-chain/`.
- Produces: `verify/verify-supply-chain.sh` with no arguments.

- [ ] **Step 1: Write failing SBOM validator tests**

Test expected root identity, non-empty graph, resolvable `dependsOn`, lockfile component coverage, malformed JSON, wrong root, and rejection of an unrequested fourth SBOM.

- [ ] **Step 2: Write failing script and remote-code scanner tests**

Use integrity-verified tarball fixtures for optional fsevents. Test exact allowlist match, changed lifecycle command, changed referenced bytes, unlisted package, Cargo checksum mismatch, suspicious `build.rs`, remote CSP source, updater wiring, `npx`, shell-pipe download, and legitimate documentation/lockfile URLs.

- [ ] **Step 3: Confirm failures**

Run: `node --test verify/test/sbom.test.mjs verify/test/install-scripts.test.mjs verify/test/remote-code.test.mjs`

Expected: FAIL because scanners are absent.

- [ ] **Step 4: Implement SBOM generation and validation**

Invoke only pinned local binaries. Generate the web SBOM including development dependencies, Rust SBOM from `rust/Cargo.lock`, and native bridge SBOM from `native-bridge/src-tauri/Cargo.lock`. Move final JSON atomically into the exact output paths.

- [ ] **Step 5: Implement license and script enforcement**

Use license-checker JSON for both npm locks and `cargo deny licenses` for both Cargo locks. Fetch locked crate sources, verify Cargo checksums, inventory/scan build scripts, then run analysis with Cargo offline. Print reviewable license/script text and policy paths on failure.

- [ ] **Step 6: Implement context-aware remote-code scanning**

Scan only declared source/config/generated scopes with syntax-aware allow rules. Do not blanket-reject URLs in docs or lockfiles.

- [ ] **Step 7: Run all supply-chain checks**

Run:

```bash
node --test verify/test/sbom.test.mjs verify/test/install-scripts.test.mjs verify/test/license-policy.test.mjs verify/test/remote-code.test.mjs
verify/verify-supply-chain.sh
```

Expected: PASS and exactly three SBOM files.

- [ ] **Step 8: Commit**

```bash
git add verify build.gradle.kts
git commit -m "feat(supply-chain): generate and enforce audit evidence"
```

### Task 6: Native Producer Hardening

**Files:**
- Modify: `native-bridge/src-tauri/tauri.conf.json`
- Modify: `native-bridge/scripts/package.mjs`
- Modify: `native-bridge/test/tauri-config-guard.test.ts`
- Modify: `native-bridge/test/manifest-guard.test.ts`
- Modify: `native-bridge/test/package-extraction.test.ts`
- Modify: `native-bridge/test/bundle-orchestration.test.ts`

**Interfaces:**
- Consumes: Task 1 standard manifest writer.
- Produces: per-platform package, sidecar, and standard `SHA256SUMS`.
- Preserves: all exact identity values in the spec.

- [ ] **Step 1: Add failing Tauri and identity guard tests**

Require Windows WebView install mode `skip` and separate exact assertions for npm package, product name, Tauri identifier, host name, binary names, app bundle, extension ID, and allowed origin.

- [ ] **Step 2: Add failing package-manifest tests**

Require stale output replacement and `SHA256SUMS` covering exactly package and sidecar for Windows and macOS fixtures.

- [ ] **Step 3: Confirm failures**

Run from `native-bridge/`: `npm ci --ignore-scripts && npm test`

Expected: FAIL for missing WebView policy and checksum manifests.

- [ ] **Step 4: Implement minimal producer changes**

Set the exact Tauri config field, replace each platform output directory, await archive/sidecar completion, and use Task 1's canonical writer.

- [ ] **Step 5: Run bridge quality and fixture tests**

Run from `native-bridge/`:

```bash
npm run format:check
npm run lint
npm run typecheck
npm test
```

Expected: PASS on Linux fixtures; do not claim real native bundle acceptance.

- [ ] **Step 6: Commit**

```bash
git add native-bridge
git commit -m "feat(native): harden package and installer policy"
```

### Task 7: Native Run Transport And Identity-Gated Comparison

**Files:**
- Create: `verify/native/deterministic-tar.mjs`
- Create: `verify/native/create-run.mjs`
- Create: `verify/native/compare-runs.mjs`
- Create: `verify/native/prepare-run.ps1`
- Create: `verify/native/prepare-run.sh`
- Create: `verify/verify-native.sh`
- Create: `verify/test/native-transport.test.mjs`
- Create: `verify/test/native-compare.test.mjs`

**Interfaces:**
- Consumes: Task 1 manifest and metadata APIs; Task 6 package trees.
- Produces: `native-{windows,macos}-run.tar` and `.sha256`.
- Produces: `verify/verify-native.sh <resolved-commit> <run-a-dir> <run-b-dir>`.
- Produces: verified native platform trees only after comparison.

- [ ] **Step 1: Write failing deterministic transport tests**

Require exact entries `SOURCE-COMMIT`, `SHA256SUMS.native-bridge`, `METADATA.native-bridge.jsonl`, `product/`, and `view/`; fixed tar metadata; file/mode/symlink preservation; sidecar validation; and rejection of traversal, duplicates, hard links, devices, and unexpected entries.

- [ ] **Step 2: Write failing comparison tests**

Test missing/empty/reordered identity, source mismatch, identity mismatch with exact `re-run on matched image`, metadata mode/target drift, byte drift with first path and hashes, and successful emission of only a verified tree.

- [ ] **Step 3: Confirm failures**

Run: `node --test verify/test/native-transport.test.mjs verify/test/native-compare.test.mjs`

Expected: FAIL because native transport modules are absent.

- [ ] **Step 4: Implement deterministic transport creation**

Reuse or extract the reviewed USTAR principles from native packaging, but keep verification transport responsibility in `verify/native/`. Hash `SOURCE-COMMIT`; keep identity metadata out of product checksum records.

- [ ] **Step 5: Implement comparison in the required order**

Validate sidecar and tar allow-list before extraction, then source commit, identity, standard/native manifests, metadata, path set, and bytes. Map identity/source/environment errors to 2 and product drift to 1.

- [ ] **Step 6: Run synthetic cross-platform tests**

Run: `node --test verify/test/native-transport.test.mjs verify/test/native-compare.test.mjs`

Expected: PASS on Linux synthetic Windows/macOS trees.

- [ ] **Step 7: Commit**

```bash
git add verify/native verify/verify-native.sh verify/test/native-transport.test.mjs verify/test/native-compare.test.mjs
git commit -m "feat(verify): compare native run transports"
```

### Task 8: Target-Native Tool Provisioning And Offline Packaging

**Files:**
- Create: `verify/native/windows-tools.json`
- Create: `verify/native/provision-windows-tools.ps1`
- Create: `verify/native/network-deny-windows.ps1`
- Create: `verify/native/network-deny-macos.sh`
- Create: `verify/test/native-environment-contract.test.mjs`
- Modify: `build.gradle.kts` only where the isolated Tauri cache environment must be forwarded.

**Interfaces:**
- Consumes: Task 7 run preparation wrappers.
- Produces: verified isolated Tauri tool cache and platform network-deny commands.

- [ ] **Step 1: Write failing static environment contract tests**

Require Windows 2025, macOS 26, exact Node/npm, exact Xcode/SDK, non-empty identity checks, acquisition-before-denial ordering, pre-probe success, active-rule inspection, same-probe failure, Cargo offline mode, and unconditional cleanup.

- [ ] **Step 2: Commit the exact Windows tool manifest**

Use these reviewed Tauri 2.6.0 inputs:

```json
{
  "wix": {
    "url": "https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip",
    "sha256": "6ac824e1642d6f7277d0ed7ea09411a508f6116ba6fae0aa5f2c7daa2ff43d31"
  },
  "nsis": {
    "url": "https://github.com/tauri-apps/binary-releases/releases/download/nsis-3/nsis-3.zip",
    "sha256": "1bb9fc85ee5b220d3869325dbb9d191dfe6537070f641c30fbb275c97051fd0c"
  },
  "nsisTauriUtils": {
    "url": "https://github.com/tauri-apps/nsis-tauri-utils/releases/download/nsis_tauri_utils-v0.5.1/nsis_tauri_utils.dll",
    "sha256": "3697d11bdbe1e34daa26b1e89d84276d9ff28148906943d0fe888354c3b13620"
  }
}
```

Add and test these exact Tauri 2.6.0 required-file allow-lists:

```text
WixTools314/candle.exe
WixTools314/candle.exe.config
WixTools314/darice.cub
WixTools314/light.exe
WixTools314/light.exe.config
WixTools314/wconsole.dll
WixTools314/winterop.dll
WixTools314/wix.dll
WixTools314/WixUIExtension.dll
WixTools314/WixUtilExtension.dll
NSIS/makensis.exe
NSIS/Bin/makensis.exe
NSIS/Stubs/lzma-x86-unicode
NSIS/Stubs/lzma_solid-x86-unicode
NSIS/Plugins/x86-unicode/nsis_tauri_utils.dll
NSIS/Plugins/x86-unicode/additional/nsis_tauri_utils.dll
NSIS/Include/MUI2.nsh
NSIS/Include/FileFunc.nsh
NSIS/Include/x64.nsh
NSIS/Include/nsDialogs.nsh
NSIS/Include/WinMessages.nsh
```

- [ ] **Step 3: Implement Windows cache provisioning**

Download to temporary files, verify SHA-256 before extraction, reject unexpected/missing files, and populate only the isolated cache path passed to Tauri.

- [ ] **Step 4: Implement platform network-deny wrappers**

Use `https://github.com/` as the fixed HTTPS probe endpoint. Require success immediately before denial, inspect a host-wide outbound deny rule, require the same endpoint to fail, execute the provided package command with Cargo offline, and restore networking in `finally`/`trap`.

- [ ] **Step 5: Run static and fixture tests**

Run: `node --test verify/test/native-environment-contract.test.mjs`

Expected: PASS locally. Real firewall behavior remains a target-runner acceptance item.

- [ ] **Step 6: Commit**

```bash
git add verify/native verify/test/native-environment-contract.test.mjs build.gradle.kts
git commit -m "feat(native): provision offline bundle environments"
```

### Task 9: APK Export, Aggregate Checksums, And Tiny Local Orchestrator

**Files:**
- Modify: `verify/verify.sh`
- Modify: `verify/verify-inner.sh`
- Create: `verify/aggregate-checksums.mjs`
- Create: `verify/aggregate-checksums.sh`
- Create: `verify/verify-all.sh`
- Create: `verify/test/aggregate-checksums.test.mjs`
- Create: `verify/test/verify-all.test.sh`

**Interfaces:**
- Consumes: all component outputs from Tasks 2, 3, 5, and 7.
- Produces: `build/verification/android/app-release-unsigned.apk`.
- Produces: `build/verification/SHA256SUMS.toolchains`.
- Produces: exact `verify-all.sh <apk> <git-ref> <native-run-a-dir> <native-run-b-dir>` interface.

- [ ] **Step 1: Write failing APK export and aggregate tests**

Require only canonical unsigned APK, web ZIP/sidecar, all Rust files, native package/sidecar/raw files/metadata JSONL; reject signed APK, mapping, manifests, identities, transport archives, reports, missing namespaces, and duplicate names.

- [ ] **Step 2: Write failing orchestrator shell fixtures**

Use fake component executables to test clean tracked/untracked status, ref/HEAD mismatch, resolved commit passed to Android/native/aggregate, exact order, stop on first failure, no skipped component, and status normalization to 0/1/2.

- [ ] **Step 3: Confirm failures**

Run:

```bash
node --test verify/test/aggregate-checksums.test.mjs
bash verify/test/verify-all.test.sh
```

Expected: FAIL because aggregate/orchestrator scripts are absent.

- [ ] **Step 4: Extend APK verification without changing signed semantics**

Mount `/out`, export the unsigned reference APK, and run `rust/scripts/verify-apk-jni.sh` against it. Keep the existing signed-versus-signature-stripped check.

- [ ] **Step 5: Implement exact aggregation and orchestration**

Keep all parsing in component/aggregate modules. `verify-all.sh` only validates args/source state, invokes the six documented commands, traps temporary state, and prints a summary.

- [ ] **Step 6: Run tests and a local APK verification**

Run:

```bash
node --test verify/test/aggregate-checksums.test.mjs
bash verify/test/verify-all.test.sh
./gradlew assembleRelease
VELES_REPO_URL="$PWD" verify/verify.sh app/build/outputs/apk/release/app-release-unsigned.apk "$(git rev-parse HEAD)"
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add verify
git commit -m "feat(verify): aggregate toolchain verification"
```

### Task 10: Reusable Linux Component Workflows

**Files:**
- Create: `.github/workflows/build-android.yml`
- Create: `.github/workflows/build-web-extension.yml`
- Create: `.github/workflows/build-rust.yml`
- Create: `.github/workflows/build-supply-chain.yml`
- Create: `verify/test/workflow-contracts.test.mjs`

**Interfaces:**
- Produces: verified Android, web-extension, `rust-jni-wasm`, and supply-chain artifacts.
- Accepts: `workflow_call` commit SHA and explicit artifact-name inputs; only Android accepts signing secrets.

- [ ] **Step 1: Write failing workflow contract tests**

Parse YAML as text/structured data and require `workflow_call`, exact Node 26.8.1, explicit upload paths/retention, component verifier invocation, stable artifact names, minimum permissions, and absence of `verify-all.sh`.

- [ ] **Step 2: Confirm failure**

Run: `node --test verify/test/workflow-contracts.test.mjs`

Expected: FAIL because reusable workflows are absent.

- [ ] **Step 3: Add `build-android.yml` and `build-web-extension.yml`**

Android builds candidate APK, runs Docker verification and ABI/JNI guard, then uploads the explicit release evidence tree. Web installs with scripts disabled, builds/packages, runs reference comparison, then uploads exactly its three package files.

- [ ] **Step 4: Add `build-rust.yml` and `build-supply-chain.yml`**

Rust runs `rustPackage` and reference comparison and publishes stable `rust-jni-wasm`. Supply-chain generates exactly three SBOMs and named reports and publishes only after enforcement.

- [ ] **Step 5: Run workflow contract tests**

Run: `node --test verify/test/workflow-contracts.test.mjs`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/build-android.yml .github/workflows/build-web-extension.yml .github/workflows/build-rust.yml .github/workflows/build-supply-chain.yml verify/test/workflow-contracts.test.mjs
git commit -m "ci: add verified Linux component workflows"
```

### Task 11: Reusable Native And Aggregate Workflows

**Files:**
- Create: `.github/workflows/build-native-windows.yml`
- Create: `.github/workflows/build-native-macos.yml`
- Create: `.github/workflows/build-toolchain-manifest.yml`
- Modify: `verify/test/workflow-contracts.test.mjs`

**Interfaces:**
- Consumes: Tasks 7 and 8 native scripts.
- Produces: internal `unverified-{windows,macos}-run-{a,b}` transports.
- Produces: exposed `verified-native-{windows,macos}` only after comparison.
- Produces: aggregate `SHA256SUMS.toolchains` artifact.

- [ ] **Step 1: Add failing native workflow tests**

Require two independent jobs and one compare job per platform; exact runner labels; exact Xcode/SDK; exact Node; non-empty identities; source commit; shortest retention for unverified slots; only verified reusable output; no signing secrets; and offline wrapper use.

- [ ] **Step 2: Add failing aggregate workflow tests**

Require all five verified component inputs, forbid `unverified-` download names, invoke aggregate component directly, and upload only `build/verification/SHA256SUMS.toolchains`.

- [ ] **Step 3: Implement native reusable workflows**

Each slot performs acquisition, verifies pins, denies networking for package production, creates transport, and uploads it. Compare jobs download both slots and run Task 7 comparison. Use `windows-2025` and `macos-26` literally.

- [ ] **Step 4: Implement aggregate reusable workflow**

Download only verified artifacts into exact expected directories and rerun manifest validation before aggregation.

- [ ] **Step 5: Run static workflow tests**

Run: `node --test verify/test/workflow-contracts.test.mjs`

Expected: PASS. Record that real native acceptance requires GitHub execution.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/build-native-windows.yml .github/workflows/build-native-macos.yml .github/workflows/build-toolchain-manifest.yml verify/test/workflow-contracts.test.mjs
git commit -m "ci: add verified native component workflows"
```

### Task 12: Thin Release Caller And Immutable Action Pins

**Files:**
- Modify: `.github/workflows/release-build.yml`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/release.yml`
- Modify: `.github/workflows/pages.yml`
- Modify: all workflows created in Tasks 10-11
- Modify: `verify/test/workflow-contracts.test.mjs`

**Interfaces:**
- Consumes: all reusable workflows.
- Produces: Android-only master push, full labeled PR graph, guarded full manual-master graph, and gated manual prerelease publication.

- [ ] **Step 1: Add failing trigger, permission, and action-pin tests**

Require non-master dispatch failure before build, Android-only ordinary master push, full `release-build` labeled PR, manual publication dependencies on Android/supply-chain/aggregate, default read permissions, write only in publisher, and 40-hex SHA plus version comment for every non-local `uses:`.

- [ ] **Step 2: Replace `release-build.yml` with the thin graph**

Preserve current prerelease collision/replacement behavior in a downstream publisher that downloads verified Android output. Reusable workflow caller jobs contain only `uses`, `with`, `secrets`, `needs`, `if`, and `permissions` fields.

- [ ] **Step 3: Pin all external actions to this reviewed snapshot**

```text
actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
actions/setup-java@b6effb05e454b25005698d916606bdc6ffcbf961 # v5
gradle/actions/setup-gradle@4733eaac7c1b0da527e4206b7671e0061de1ce37 # v6
actions/cache@caa296126883cff596d87d8935842f9db880ef25 # v5
actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7
actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131 # v7
actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38 # v6
actions/attest-build-provenance@43d14bc2b83dec42d39ecae14e916627a18bb661 # v3
reactivecircus/android-emulator-runner@4c44018e59b437e86cdfc41da381398f93ed8808 # v2
actions/configure-pages@983d7736d9b0ae728b81ab479565c72886d7745b # v5
actions/upload-pages-artifact@7b1f4a764d45c48632c6b24a0339c27f5614fb0b # v4
actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e # v4
```

Allow repository-local `uses: ./.github/workflows/...` without a SHA.

- [ ] **Step 4: Run workflow contract tests**

Run: `node --test verify/test/workflow-contracts.test.mjs`

Expected: PASS across every workflow file.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows verify/test/workflow-contracts.test.mjs
git commit -m "ci: compose and pin verified release workflows"
```

### Task 13: Clean Contract And Documentation

**Files:**
- Modify: `build.gradle.kts`
- Modify: `rust/scripts/assert-clean.sh`
- Modify: `docs/reproducible-builds.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-08-28-otp-01-1d-verification-supply-chain-design.md`

**Interfaces:**
- Consumes: every public command/output established above.
- Produces: documented local/CI usage and explicit clean assertions.

- [ ] **Step 1: Extend clean assertions before implementation**

Add fixture paths for `build/rust-package`, `build/verification`, `build/sbom`, `build/verify-tools`, `build/web-extension`, `build/native-bridge`, existing generated JNI/WASM/dist, and native target output. Confirm the assertion fails before `clean` and passes after it.

- [ ] **Step 2: Run clean verification**

Run:

```bash
./gradlew rustPackage
npm run package --prefix web-extension
mkdir -p build/verification build/sbom build/native-bridge/windows build/native-bridge/macos
./gradlew clean
rust/scripts/assert-clean.sh
```

Expected: PASS and all declared generated paths absent.

- [ ] **Step 3: Update command and trust-boundary documentation**

Document every component command, exact `verify-all.sh` synopsis/layout, output/SBOM/policy paths, Node floor versus exact reference version, exit codes, runner identities, no image-SHA claim, Xcode/SDK pins, native rerun instruction, offline acquisition/package boundary, and six-step pin upgrade process.

- [ ] **Step 4: Mark the approved spec implemented only after acceptance**

At this task, change status from `Draft pending written-spec review` to `Approved; implementation in progress`. Do not claim 1d accepted until Task 14's GitHub gates pass.

- [ ] **Step 5: Check docs and diff**

Run: `git diff --check`

Expected: no whitespace errors and no stale Node 22 reference presented as the exact verification runtime.

- [ ] **Step 6: Commit**

```bash
git add build.gradle.kts rust/scripts/assert-clean.sh docs/reproducible-builds.md README.md CLAUDE.md docs/superpowers/specs/2026-08-28-otp-01-1d-verification-supply-chain-design.md
git commit -m "docs: document verification supply chain"
```

### Task 14: Full Local And GitHub Acceptance

**Files:**
- Modify only files required by concrete failures; every fix receives its own focused test and commit.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: final OTP-01 1d verification evidence.

- [ ] **Step 1: Run all local verification-unit tests**

Run: `node --test verify/test/*.test.mjs`

Expected: PASS.

- [ ] **Step 2: Run web-extension quality and packaging**

Run from `web-extension/`:

```bash
npm ci --ignore-scripts
npm run format:check
npm run lint
npm run typecheck
npm test
npm run build
npm run test:bundle
npm run package
```

Expected: PASS.

- [ ] **Step 3: Run bridge quality checks**

Run from repository root:

```bash
./gradlew bridgeInstall bridgeFormat bridgeLint bridgeTypecheck bridgeTest
```

Expected: PASS without lifecycle scripts.

- [ ] **Step 4: Run Rust, supply-chain, and Android verification**

Run:

```bash
./gradlew rustFormat rustLint rustTest rustPackage
verify/verify-web.sh
verify/verify-rust.sh
verify/verify-supply-chain.sh
./gradlew testDebugUnitTest
./gradlew assembleRelease
VELES_REPO_URL="$PWD" verify/verify.sh app/build/outputs/apk/release/app-release-unsigned.apk "$(git rev-parse HEAD)"
```

Expected: PASS.

- [ ] **Step 5: Verify clean state**

Run: `./gradlew clean && rust/scripts/assert-clean.sh`

Expected: PASS.

- [ ] **Step 6: Run a `release-build` labeled PR**

Require all Linux components, both Windows slots, both macOS slots, both native comparison jobs, supply-chain enforcement, and aggregate workflow to pass. Confirm native logs record matching identity triples and source commit, and only verified native outputs are exposed.

- [ ] **Step 7: Test mismatch/error paths on GitHub**

Run a non-master manual dispatch and require the guard to fail before builds. Use fixture workflow inputs to demonstrate an identity mismatch exits 2 with `re-run on matched image`; do not wait for accidental runner rollout mismatch.

- [ ] **Step 8: Run a manual dispatch on `master`**

Require the complete graph before prerelease publication. Confirm 1d evidence remains CI-only and public release assets retain existing APK semantics.

- [ ] **Step 9: Record final status**

After all target-native byte comparisons pass, change the design status to `Implemented` and commit only that status/evidence link:

```bash
git add docs/superpowers/specs/2026-08-28-otp-01-1d-verification-supply-chain-design.md
git commit -m "docs(otp-01/1d): record verification acceptance"
```
