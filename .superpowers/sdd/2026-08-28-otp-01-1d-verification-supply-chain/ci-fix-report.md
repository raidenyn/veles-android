# CI Fix Report - PR #111 Remaining CI Failures

## Status

DONE_WITH_CONCERNS

All three independently actionable failure areas in CI run 33500934265 were
corrected on `otp-01-1d-verification`. The aggregate failure is downstream of
the Android artifact and requires no separate source change.

## Changes

### Android APK comparison

The JNI and WASM release tasks now pass Cargo a mandatory
`--remap-path-prefix` from the active Cargo registry source directory to the
stable `/cargo-registry/src` prefix. Rust panic-location data had embedded the
absolute `CARGO_HOME` registry path, so CI's runner (`/home/runner/.cargo`) and
the Android reference image (`/opt/cargo`) produced different JNI bytes and
therefore different APKs. The signed-versus-signature-stripped
`apksigcopier compare` path is unchanged.

### Rust Docker Gradle pre-cache

The wrapper SHA-256 pin is now in `gradle-wrapper.properties`, where Gradle
enforces it while downloading. Docker additionally checks it against the
existing exact ARG. The pre-cache assertion locates the nested
`gradle-9.5.0-bin.zip.ok` marker and verifies its sibling extracted launcher.
This is the cache layout Gradle 9.5.0 actually creates; the prior assertion
searched for a ZIP that is removed after extraction and then addressed the
wrong parent directory.

### Native comparison

Tauri 2.6.0 NSIS/MSI/DMG/.app producer outputs carry timestamps or platform
identifiers and cannot be made byte-reproducible with supported flags. The
comparison continues to validate every slot's native manifest and product
`SHA256SUMS`, but excludes those installer payloads and their enclosing outer
package/sidecar from cross-run byte equality. Stable raw host and host-manifest
evidence remains byte-compared. The required design ruling is recorded in
`progress.md` and the design specification describes the exception.

## Commits

- `7085c00 fix(verify): exempt nondeterministic Tauri installers from cross-run bytes`
- `9a9dd4c fix(rust): remap cargo registry paths in reproducible builds`
- `4327020 fix(verify): validate Gradle cache marker at its hashed path`

Note: the pre-existing staged Gradle wrapper/Docker changes were included in
`7085c00` when the native commit was created. `4327020` corrects the Docker
cache-path logic after local and container reproduction showed the staged
assumption was incomplete.

## Verification

- `node --test verify/test/native-compare.test.mjs`: 9 passed; the new
  installer-drift regression failed before the comparison change and passed
  afterward.
- `node --test verify/test/rust-package.test.mjs verify/test/rust-verifier.test.mjs`: 16 passed.
- `JAVA_HOME="$JAVA_ROOT/zulu21" PATH="$JAVA_ROOT/zulu21/bin:$PATH" ./gradlew rustJni`: passed.
- Local Gradle wrapper cache characterization: confirmed nested `.zip.ok` and
  extracted launcher under the hashed distribution directory.
- `docker build -t veles-verify-rust-ci-fix -f verify/Dockerfile.rust .`: passed.
- `node --test verify/test/*.test.mjs`: 119 passed.
- `bash verify/test/native-exit-codes.test.sh`: passed; Windows behavioral
  portion skipped because `pwsh` is unavailable locally, structural assertions
  passed.
- `bash verify/test/verify-native.test.sh`: passed.

## Fix Round 3/5

### Status

Addressed the multiple-recognized-archive bypass.

### Correction

Package-count handling now distinguishes the only permitted synthetic case
(zero recognized archives with a host-only view) from malformed product layout.
More than one ZIP/tar.gz archive always exits 1 with `expected one package
archive`, before archive binding or cross-run comparison can be bypassed.

### Regression Evidence

- Added a host-only fixture with two recognized, byte-different archive names;
  it failed before the control-flow change and now requires exit 1.
- Extensionless installer-view rejection and real producer ZIP/tar.gz paths
  remain covered.

### Commands

- `node --test verify/test/native-compare.test.mjs verify/test/native-end-to-end.test.mjs verify/test/native-extract-view.test.mjs verify/test/native-transport.test.mjs`: 26 passed.
- `bash verify/test/verify-native.test.sh`: passed.

## Fix Round 4/5

### Status

Addressed the Windows raw-host byte mismatch without weakening its cross-run
comparison.

### Root Cause

CI run `33649898862`, job `100316397431` downloaded both producer transports
successfully and rejected only `view/veles-native-bridge.exe`:

- run-a SHA-256: `ad9bcb2cb3bcf9c99983ed22811b68fd74c524e961ca1706e9f2036db6e522ee`
- run-b SHA-256: `8f9bc191fcbdd4959e5ecd6ced9cc81fcfba9af87be6530afe0c7503ddbb5247`

The recovered 239104-byte producer binaries differ in only 21 byte positions:
the PE COFF timestamp, three debug-directory timestamps, and the 16-byte
CodeView PDB GUID. All source, host payload, and transport bytes otherwise
match. This is MSVC linker metadata, not a Cargo path/cache, Tauri package, or
artifact transport issue.

### Correction

Both serialized Windows Tauri Cargo invocations, `bridgeBuild` and
`bridgeBundle`, now pass `RUSTFLAGS="-C link-arg=/Brepro"`. MSVC `/Brepro`
derives PE/debug timestamps and the PDB GUID from link inputs. The flag is
limited to Windows, so macOS continues using its existing toolchain behavior.

### Regression Evidence

- Added a Gradle environment contract test requiring the Windows-only `/Brepro`
  flag on both build phases.
- The test failed before the implementation with `bridgeBuild must pass /Brepro
  to the MSVC linker`, then passed after the correction.
- The raw `view/veles-native-bridge.exe` comparison remains unchanged; no
  exemption or normalization was added.

### Commands

- `node --test verify/test/native-environment-contract.test.mjs`: 6 passed.
- `node --test verify/test/native-environment-contract.test.mjs verify/test/native-compare.test.mjs verify/test/native-end-to-end.test.mjs verify/test/native-extract-view.test.mjs`: 26 passed.
- `JAVA_HOME="$JAVA_ROOT/zulu21" ./gradlew help`: passed.
- `git diff --check`: passed.

### Remaining Concerns

- `pwsh` is unavailable locally, so a Windows build cannot be exercised in this
  workspace. The structural regression is tied to byte-level evidence from the
  actual CI producers; the next Windows CI comparison remains the behavioral
  authority for `/Brepro` on the pinned runner image.
- `bash verify/test/verify-all.test.sh`: passed.
- `git diff --check`: passed.

## Concerns

- The native installer exception intentionally no longer detects cross-run
  installer-content drift when each slot's own checksum evidence is valid. It
  must be revisited when Tauri/bundler reproducibility support becomes
  available.
- The Android signed reference Docker comparison and GitHub-hosted Windows/macOS
  acceptance remain CI-environment checks. Local Docker availability was used
  to validate the Rust image only; no CI run was pushed or triggered.
- Windows behavioral wrapper tests could not run locally because `pwsh` is not
  installed.

## Fix Round 1/5

### Status

Addressed all three blocking review findings.

### Corrections

- The macOS app host path
  `Veles Native Bridge.app/Contents/MacOS/veles-native-bridge` is no longer
  exempt from cross-run comparison. Other `.app` payload entries remain within
  the documented Tauri nondeterminism exception.
- `compare-runs.mjs` now opens each real ZIP or tar.gz package and requires its
  payload files and symlinks to match the transported verification view before
  comparison can succeed. A malformed package, extra view payload, or changed
  payload now exits 1 as a package/view binding mismatch.
- Aggregate documentation and implementation comments now distinguish stable
  host/manifest records (cross-run compared) from package, installer, and
  non-host app evidence (package-bound and self-validated per run only).

### Regression Evidence

- Added a test that differing macOS app-host bytes are rejected even while an
  unrelated `.app` payload differs.
- Added a test that arbitrary text named `.zip` cannot pass as a package with
  an unrelated view.
- Existing real-producer end-to-end coverage passes for Windows ZIP and macOS
  tar.gz package/view binding, including macOS app symlinks.

### Commands

- `node --test verify/test/native-compare.test.mjs verify/test/native-end-to-end.test.mjs verify/test/native-extract-view.test.mjs verify/test/aggregate-checksums.test.mjs`: 21 passed.

### Remaining Concerns

- The documented Tauri installer/package exception remains: matching runner
  slots do not cross-compare NSIS/MSI/DMG/non-host-app bytes. The new archive
  binding prevents a package from claiming unrelated view payloads, and each
  slot still validates its manifests and checksums.

## Fix Round 2/5

### Status

Addressed the extensionless-product package/view binding bypass.

### Correction

`compare-runs.mjs` now requires exactly one recognized ZIP or tar.gz package
whenever a view contains an exempt NSIS/MSI/DMG installer or non-host `.app`
payload. An unknown or extensionless product can no longer carry installer/app
bytes around archive-to-view binding. Host-only synthetic fixtures remain
permitted because their bytes are always cross-run compared and do not exercise
the Tauri nondeterminism exception.

### Regression Evidence

- Replaced the prior accepted extensionless installer fixture with a regression
  that requires an exit-1 `expected one package archive` binding error.
- Retained real producer end-to-end ZIP/tar.gz binding coverage and the macOS
  raw-host comparison regression.

### Commands

- `node --test verify/test/native-compare.test.mjs verify/test/native-end-to-end.test.mjs verify/test/native-extract-view.test.mjs`: 19 passed.
- `bash verify/test/verify-native.test.sh`: passed.
