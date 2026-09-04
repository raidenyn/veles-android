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

## Windows host binary drift

CI run `33649898862`, job `100316397431`, rejected only the raw host:
`view/veles-native-bridge.exe` was
`ad9bcb2cb3bcf9c99983ed22811b68fd74c524e961ca1706e9f2036db6e522ee`
in run-a and
`8f9bc191fcbdd4959e5ecd6ced9cc81fcfba9af87be6530afe0c7503ddbb5247`
in run-b. The recovered 239104-byte binaries differ only in the PE COFF
timestamp, three debug-directory timestamps, and the 16-byte CodeView PDB GUID.

The Windows workflow invokes `network-deny-windows.ps1`, which runs Gradle
`bridgeBuild` and then `bridgePackage`/`bridgeBundle`; both Tauri commands invoke
Cargo and MSVC. This excludes source, package transport, and cache-path drift.
MSVC `/Brepro` derives those PE/debug metadata fields from link inputs, making it
the confirmed corrective flag.

`build.gradle.kts` sets `RUSTFLAGS="-C link-arg=/Brepro"` for Windows in both
`bridgeBuild` and `bridgeBundle`. Regression test `Windows native bridge builds
enable MSVC reproducible-linker metadata` in
`verify/test/native-environment-contract.test.mjs` passed via
`node --test verify/test/native-environment-contract.test.mjs` (6 passed).

`pwsh` is unavailable locally, so the Windows build remains CI-only behavioral
verification on the pinned runner image.

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

## Fix Round 5/5 - Android and Rust Docker Boundaries

### Status

Addressed both remaining component failures from PR #111 CI run `33652769090`.
The aggregate job is downstream only and needs no source change.

### Android Root Cause and Correction

Job `100323668361` completed the Docker byte comparison successfully, including
the signed-input `apksigcopier compare <released> --unsigned <rebuilt>` path.
The container then copied the rebuilt unsigned APK to the `/out` bind mount as
container root. Docker preserved that root ownership on
`build/verification/android/app-release-unsigned.apk`, so the following host
workflow `cp` failed with permission denied.

`verify/verify.sh` now passes the invoking host UID and GID to the container.
After the rebuilt APK passes the JNI allow-list, `verify-inner.sh` validates
those numeric values and changes only `/out/app-release-unsigned.apk` to the
host ownership. Invalid ownership handoff or `chown` is an environment error
with exit 2. The signed-versus-signature-stripped comparison is unchanged.

### Rust Root Cause and Correction

Job `100323667995` shows `cargo fetch --locked` downloading `wasm-bindgen`
after the online `rustInstall`. The online `prepare` and offline `package`
containers shared `/work` only, but Cargo used image-local `/opt/cargo`; its
registry index/cache disappeared with the prepare container's writable layer.
The offline package container therefore could not resolve `wasm-bindgen` under
`CARGO_NET_OFFLINE=true` and Docker `--network=none`.

`rust-inner.sh` now uses `/work/cargo-home` as `CARGO_HOME` and fetches the
locked workspace graph before `rustInstall`. The package invocation remains
`CARGO_NET_OFFLINE=true ./gradlew --offline rustPackage`; no pin, allow-list,
or network-denial behavior changed.

### Regression Tests and Verification

- New `verify/test/android-verifier.test.mjs` failed before the change because
  neither UID/GID handoff nor output ownership restoration existed; it now
  verifies both while retaining the `apksigcopier` assertion.
- New Rust cache-persistence assertion in `verify/test/rust-verifier.test.mjs`
  failed before the change because `CARGO_HOME` was image-local and fetch ran
  after tool installation; it now verifies volume persistence, locked-fetch
  ordering, and the unchanged offline invocation.
- `node --test verify/test/android-verifier.test.mjs verify/test/rust-verifier.test.mjs`: 11 passed after implementation; 2 expected failures before it.
- `bash -n verify/verify.sh verify/verify-inner.sh verify/rust-inner.sh && node --test verify/test/workflow-contracts.test.mjs verify/test/rust-package.test.mjs verify/test/rust-verifier.test.mjs verify/test/android-verifier.test.mjs`: 34 passed.
- `docker build -t veles-verify-rust-ci-fix -f verify/Dockerfile.rust .`: passed.
- Online `prepare` followed by `docker run --rm --network=none ... package`:
  passed; all three JNI ABIs and the WASM package were produced offline, with
  `wasm-bindgen` resolved from the shared Cargo home.

### Commits

- `cc09ec0 fix(verify): persist Docker verifier boundaries`

### Concerns

- The local checkout is a Git worktree, whose `.git` pointer lies outside the
  mounted source. The real Docker validation mounted that parent Git metadata
  read-only to emulate CI's ordinary checkout; this is unrelated to the Cargo
  cache fix. CI remains the behavioral authority for the Android signed APK
  path because a signed release artifact was not produced locally.
- `verify/node_modules/` was not modified or removed.

## Fix Round 1/5 - Android Ownership Exit Precedence

### Root Cause and Correction

The previous ownership repair copied, validated, and attempted to `chown` the
`/out` artifact before comparing the released and rebuilt APKs. A failed
handoff therefore returned exit 2 before a genuine byte/signature mismatch
could return exit 1.

`verify-inner.sh` now performs the APK comparison first. Only after a successful
comparison does it restore output ownership. Handoff is conditional: an audit
caller that supplies neither `VELES_OUTPUT_UID` nor `VELES_OUTPUT_GID` retains
the documented `/out` audit mode; a partial or nonnumeric explicit handoff and
a failed `chown` remain environment errors (exit 2). `verify.sh` continues to
supply both host IDs for workflow staging.

### Regression Evidence

- Replaced the source-text-only Android verifier check with an executable
  harness that runs `verify-inner.sh` against controlled APKs and commands.
- Before the correction, a byte mismatch plus numeric handoff and failing
  `chown` exited 2 with the ownership error. It now exits 1 and emits the
  mismatch result before attempting ownership restoration.
- The same harness proves audit mode copies the rebuilt APK and exits 0 without
  ownership variables.
- `node --test verify/test/android-verifier.test.mjs && bash -n verify/verify-inner.sh`: 2 passed.
- `bash -n verify/verify.sh verify/verify-inner.sh verify/rust-inner.sh && node --test verify/test/*.test.mjs && bash verify/test/verify-all.test.sh && git diff --check`: 126 Node tests passed; `verify-all.test.sh` passed its Android, web, Rust, native, supply-chain, aggregate, clean-checkout, and exit-code contract cases. Windows behavioral wrapper subtests were skipped because `pwsh` is unavailable.

### Commit

- `e049822 fix(verify): preserve APK mismatch exit status`

### Concerns

- The executable regression isolates the verifier using controlled command
  doubles; signed release behavior remains CI-authoritative.

## Fix Round 6/5 - Rust Offline wasm-pack Update Check

### Status

Addressed the Rust reference package failure from PR #111 CI run `33783218187`,
job `100741672124`. The macOS native comparison is intentionally unchanged:
its run-a/run-b `ImageVersion` values (`20260831.0337.3` and
`20260728.0273.1`) differ, so its existing environment gate correctly exits 2.

### Root Cause

`verify/rust-inner.sh` exports `CARGO_HOME=/work/cargo-home` so the online
prepare container's fetched registry survives to the separate
`--network=none` package container. Docker's image-level `PATH`, however, was
expanded when the image was built and still begins with `/opt/cargo/bin`; the
`CARGO_HOME` export does not rewrite it. This is valid because Rustup's cargo
shim remains available there.

During `rustWasm`, Gradle's `doFirst` executes the pinned absolute binary
`/work/src/build/rust-tools/wasm-pack/bin/wasm-pack`. It constructs the child
`PATH` as that binary directory, then the pinned local
`wasm-bindgen-cli/bin`, then Gradle's inherited image PATH; the task also
inherits `CARGO_HOME=/work/cargo-home`. Thus Cargo and the pinned tools are
available and the JNI ABIs plus WASM package build successfully offline.

`verifyWasmPack` is not the failing validation: it invokes that same absolute
binary with `--version` and checks exact `0.15.0` output. wasm-pack 0.15.0
also starts a detached crates.io update check on *every* invocation. Its
short-lived `--version` process need not leave its sibling update stamp before
it exits. The longer `rustWasm` invocation gives that check time to reach
`https://crates.io/api/v1/crates/wasm-pack`; under the required network denial
it emits `[WARN]: failed to get wasm-pack version`. The warning follows the
successful package output and was the observed offline reference boundary
failure.

### Correction

After online `rustInstall` has created and exact-version-validated the pinned
binary, `seed_wasm_pack_update_stamp` writes its normal sibling
`wasm-pack.stamp` with a current ISO-8601 `created` value. wasm-pack treats the
fresh stamp as its documented 24-hour update-check cache and makes no optional
crates.io request in the network-denied package invocation. The stamp does not
change binary pin validation, Rust `1.98.0`, `CARGO_NET_OFFLINE=true`, Docker
`--network=none`, or the verifier's 0/1/2 exit semantics.

### Red/Green Evidence

- Added `seeds wasm-pack update metadata before the network-denied package run`
  in `verify/test/rust-verifier.test.mjs`. It asserts the post-`rustInstall`
  preparation ordering, the exact sibling stamp path, and the unchanged offline
  package command.
- Red: `node --test --test-name-pattern='seeds wasm-pack update metadata'
  verify/test/rust-verifier.test.mjs` failed before production changes with
  `AssertionError`: the prepare body lacked `seed_wasm_pack_update_stamp`.
- Green: `bash -n verify/rust-inner.sh && node --test
  verify/test/rust-verifier.test.mjs` passed 11/11.

### Commands and Output

- `node --test verify/test/rust-package.test.mjs
  verify/test/rust-verifier.test.mjs && bash -n verify/rust-inner.sh && git
  diff --check`: 18/18 Node tests passed; shell syntax and whitespace checks
  passed.
- `docker build -t veles-verify-rust-pr111 -f verify/Dockerfile.rust .`:
  passed.
- A local online prepare attempt reproduced a separate existing Docker/Android
  configuration failure at `app/build.gradle.kts:31` (`Cannot invoke method
  getTarget() on null object`) before `rustInstall`; it cannot exercise this
  CI-only package boundary locally and is unrelated to the supplied CI trace.

### Commit

- `53e46dd fix(verify): suppress wasm-pack update check offline`

### Concerns

- wasm-pack's update check is upstream behavior and its `.stamp` cache is
  timestamped. It is outside the staged Rust package and does not affect
  compared JNI/WASM bytes. The next CI run remains the end-to-end authority for
  the offline reference container.

## Reviewer Fix Round 1 - Rust Output Ownership

### Root Cause

The actual PR #111 Rust failure occurred after the verified package comparison:
the root-run package container copied reference files into the host bind mount,
then `verify-rust.sh`'s EXIT cleanup could not remove those root-owned entries.
The resulting cleanup failure changed a successful verification into exit 1.

The prior wasm-pack stamp fix was removed. `rustInstall` declares its output
directory, and the subsequent `rustPackage` invocation may replace that output,
so the stamp was not a reliable cache. Upstream's update request is a warning,
not a reproduced fatal error.

### Correction and Evidence

`verify-rust.sh` now passes its numeric caller UID/GID to the offline package
container. After the offline package has copied the reference output,
`rust-inner.sh` validates both values and recursively restores `/out`
ownership. Invalid handoff or failed ownership restoration remains an
environment failure (exit 2); byte mismatch behavior remains exit 1.

- Red: the new behavioral fake-Docker test produced a matching reference tree,
  made it non-removable when no ownership handoff was present, and failed after
  verified comparison with `rm: cannot remove ... Permission denied` and exit
  1.
- Green: `restores reference-output ownership so cleanup removes it after a
  verified match` passes by requiring the output handoff and observing that the
  temporary reference directory no longer exists after the verifier exits.
- `node --test verify/test/rust-package.test.mjs
  verify/test/rust-verifier.test.mjs`: 18/18 passed.
- `bash -n verify/verify-rust.sh verify/rust-inner.sh` and `git diff --check`:
  passed.

### Commit

- `5ff0396 fix(verify): restore Rust reference output ownership`

## Reviewer Fix Round 2 - Rust Exit Precedence

### Root Cause and Correction

The EXIT trap's unguarded `rm -rf` could replace any requested verifier status
with cleanup exit 1. Ownership restoration also ran in `package`, before the
outer verifier compared artifacts, so a `chown` failure could mask a genuine
byte mismatch.

Cleanup now ignores reference-directory removal failures. The package command
only creates the reference output; after a successful byte comparison,
`verify-rust.sh` invokes a separate `restore-output-ownership` container
command. That command validates numeric UID/GID and performs `chown -R`.
Consequently mismatch remains exit 1, invalid or failed ownership restoration
is exit 2, and successful restoration keeps host cleanup working.

### Red/Green Evidence

- Red: executable fake-Docker coverage made the reference directory
  non-removable; the previous flow exited 1 after a verified comparison with
  `rm: cannot remove ... Permission denied`.
- Green: the same test covers successful UID/GID restoration and cleanup, a
  mismatched package plus forced restore failure (exit 1), and a forced restore
  failure after a match (exit 2).
- `node --test verify/test/rust-package.test.mjs
  verify/test/rust-verifier.test.mjs`: 18/18 passed.
- `bash -n verify/verify-rust.sh verify/rust-inner.sh` and `git diff --check`:
  passed.

### Commit

- `749c1a5 fix(verify): preserve Rust comparison exit status`
