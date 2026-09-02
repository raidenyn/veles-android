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
