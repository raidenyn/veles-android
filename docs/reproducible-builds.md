# Reproducible builds & release verification

Veles reads your notifications — the most sensitive permission on Android. "Audit
the source" only means something if you can prove the APK you installed was built
from that source. Every release can be verified two ways.

## What each path proves

| Path | Proves | You trust |
|---|---|---|
| Attestation (one-liner) | This exact file was built by this repo's release workflow at this tagged commit | GitHub (runner + Sigstore) |
| Reproducible rebuild | This exact file is byte-identical (ignoring the signature) to what the tagged source produces | Nothing but the source tree and your own machine |

CI builds each release **once** and publishes it after an integrity check: the
draft release's APK asset must match the SHA-256 digest the build job
computed, and every release is attested and ships `SHA256SUMS`. Publication
does **not** require a successful reproducibility comparison — the rebuild
paths below are opt-in diagnostics you can run yourself.

## Path 1 — attestation (seconds)

```bash
gh attestation verify veles-X.Y.Z.apk --repo raidenyn/veles-android
```

Requires the [GitHub CLI](https://cli.github.com/). Output ends with
"Verification succeeded!" and names the workflow and commit.

## Path 2 — zero-trust APK rebuild (minutes, needs Docker + bash)

```bash
git clone https://github.com/raidenyn/veles-android
cd veles-android
git checkout X.Y.Z         # IMPORTANT: verify from the tag you are verifying
verify/verify.sh /path/to/veles-X.Y.Z.apk X.Y.Z
```

The script builds a pinned reference environment (exact JDK, Android SDK,
build-tools), clones and rebuilds the tag inside it, and compares the result to
your APK with [apksigcopier](https://github.com/obfusk/apksigcopier) — the same
signature-stripping comparison F-Droid uses. Exit codes:

- **0** — verified (byte-identical or signature-stripped match)
- **1** — mismatch (the released APK does not correspond to the source)
- **2** — usage or pin error (bad arguments, missing file, Dockerfile/JDK pin mismatch, image build failure)

It prints both SHA-256 digests on success or mismatch.

Requirements: Docker, plus `bash`, `realpath`, `grep`, and `tr` on the host
(`realpath` is absent on older macOS — install `coreutils` via Homebrew, or use
`readlink -f` as a fallback).

`SHA256SUMS` on each release lists the digests of the published artifacts.

## Path 3 — full toolchain verification (OTP-01 sub-project 1d, optional local tools)

1d closes OTP-01 by making the Android APK, web-extension, shared Rust
JNI/WASM, and unsigned native-bridge artifacts **verifiable in their
documented build environments** with small optional component verifiers, and
by adding auditable SBOM, license, install-script, and remote-code
enforcement. It does **not** replace npm, Gradle, Cargo, Vite, wasm-pack, or
Tauri as artifact producers. The reproducibility verifiers are **opt-in local
diagnostics**: CI builds each component exactly once in its own
`build-<component>.yml` reusable workflow and uploads its product with
component checksums — it runs no reference rebuilds, A/B comparisons, or
aggregation, and publication requires none of them. The one CI-side
exception is `verify/verify-supply-chain.sh`: `build-supply-chain.yml` runs it
as its supply-chain **policy** job (SBOM/license/script/remote-code
enforcement — not a reproducibility comparison), and it remains runnable
locally.

### Local aggregate entry point

The exact synopsis is:

```
verify/verify-all.sh <apk> <git-ref> <native-run-a-dir> <native-run-b-dir>
```

Before invoking any component, the script resolves `<git-ref>` to a commit,
requires it to equal `HEAD`, and requires an empty porcelain status including
untracked files. This binds all locally built web, Rust, and supply-chain
evidence to one clean source tree. It passes the resolved commit to native
verification and aggregation.

The native input layout (two independent runs per platform, transferred as
deterministic transport tars with SHA-256 sidecars) is produced **locally**
with the committed helper scripts — CI no longer creates run transports:

```
<native-run-a-dir>/windows/native-windows-run.tar
<native-run-a-dir>/windows/native-windows-run.tar.sha256
<native-run-a-dir>/macos/native-macos-run.tar
<native-run-a-dir>/macos/native-macos-run.tar.sha256
<native-run-b-dir>/windows/native-windows-run.tar
<native-run-b-dir>/windows/native-windows-run.tar.sha256
<native-run-b-dir>/macos/native-macos-run.tar
<native-run-b-dir>/macos/native-macos-run.tar.sha256
```

Each run must come from its own **isolated, clean build of the exact commit**
— a separate fresh checkout (or worktree, or a fresh target runner) per slot.
Two `bridgePackage` invocations in the same Gradle workspace are **not**
independent: `bridgeBuild`/`bridgeBundle` declare outputs, so on the second
invocation Gradle may mark the host and bundle tasks up to date and repackage
the first build's executable and installers — yielding two transports of one
underlying compilation, which proves nothing about cross-run stability.

Per slot, from a **clean checkout of the resolved commit** on the target
platform (run all three steps before any `./gradlew clean` or output move —
they read `native-bridge/src-tauri/target/release` and
`build/native-bridge/<platform>/`):

1. Build and package once, optionally under the committed offline wrappers
   (`verify/native/network-deny-macos.sh`, or on Windows
   `pwsh -NoProfile -File verify/native/network-deny-windows.ps1 -TauriCachePath "native-bridge/src-tauri/target/.tauri"`):
   `./gradlew bridgePackage` (Windows: `.\gradlew.bat bridgePackage`).
2. Assemble the extracted verification view (replace `<platform>` with
   `windows` or `macos`):
   ```
   node verify/native/extract-view.mjs <platform> native-bridge/src-tauri/target/release build/native-bridge/<platform> <run-dir>/view
   ```
3. Create the transport (macOS/Linux shell, or Windows PowerShell — same four
   arguments, matching the layout block above):
   ```
   verify/native/prepare-run.sh    <run-dir>/<platform>/native-<platform>-run.tar <resolved-commit> build/native-bridge/<platform> <run-dir>/view
   pwsh -File verify/native/prepare-run.ps1 <run-dir>/<platform>/native-<platform>-run.tar <resolved-commit> build/native-bridge/<platform> <run-dir>/view   # Windows
   ```

`prepare-run.*` requires the environment variables `ImageOS`, `ImageVersion`,
and `RUNNER_ARCH` to be set and records them into the transport manifest.
Source them truthfully from the machine that produced the build: GitHub
runners provide all three natively; on any other host, set them to values
that honestly describe that machine. The identity gate exists to detect that
the two runs did not come from the same image — it is an environment check,
not a requirement to use GitHub runners.

From the repository root, `verify-all.sh` invokes, in order, and stops on the
first failure:

1. `verify/verify.sh <apk> <resolved-commit>` — Android rebuild + signature-stripped compare; also exports the canonical unsigned reference APK to `build/verification/android/app-release-unsigned.apk`.
2. `verify/verify-web.sh` — web-extension candidate/reference byte compare.
3. `verify/verify-rust.sh` — Rust JNI/WASM candidate/reference byte compare.
4. `verify/verify-native.sh <resolved-commit> <native-run-a-dir> <native-run-b-dir>` — two independent Windows and two macOS runs, identity gate, then stable-payload compare; output under `build/verification/native-bridge/windows/` and `build/verification/native-bridge/macos/`.
5. `verify/verify-supply-chain.sh` — SBOM, license, install-script, and remote-code enforcement; reports under `build/verification/supply-chain/`.
6. `verify/aggregate-checksums.sh <resolved-commit>` — assembles `build/verification/SHA256SUMS.toolchains` (a local convenience manifest; CI publishes per-component checksums inside each artifact instead).

`verify-all.sh` is intentionally small: it validates arguments, calls the
component verifiers in a fixed documented order, stops on the first failure,
normalizes and propagates the component exit status, and prints a component
summary. It contains no build implementation, checksum parser, runner-identity
parser, artifact allow-list, SBOM generator, license decision, or remote-code
scanner. It fails with exit 2 rather than silently skipping an input or
component. **CI never invokes `verify-all.sh` or any reproducibility-comparison
verifier.** The single CI-side exception is `verify/verify-supply-chain.sh`,
which `build-supply-chain.yml` runs as its policy job (see the component
table); it is a policy check, not a reproducibility comparison.

### Component commands

Each component has one CI producer (builds the product once and uploads it
with component checksums) and one optional local verifier:

| Component | CI producer (single build) | Optional local verifier | Verifier output |
|---|---|---|---|
| Android APK | `./gradlew assembleRelease` in `build-android.yml`; artifact `android-release` = actual APK + `mapping.txt` + `SHA256SUMS` | `verify/verify.sh <apk> <ref>` (Docker rebuild + signature-stripped compare) | `build/verification/android/app-release-unsigned.apk` |
| Web-extension | `npm run package` in `build-web-extension.yml`; artifact `web-extension` = ZIP + sidecar + `SHA256SUMS` | `verify/verify-web.sh` (candidate/reference byte compare) | `build/web-extension/{veles-extension-<version>.zip,.zip.sha256,SHA256SUMS}` |
| Rust JNI/WASM | `./gradlew rustPackage` in `build-rust.yml`; artifact `rust-jni-wasm` | `verify/verify-rust.sh` (candidate/reference byte compare) | `build/rust-package/{jni,wasm,SHA256SUMS}` |
| Native Windows | `./gradlew bridgePackage` **once** on `windows-2025` in `build-native-windows.yml`; artifact `native-windows` = archive + sidecar + `SHA256SUMS` | `verify/verify-native.sh` with two locally produced run dirs | `build/native-bridge/windows/`, `build/verification/native-bridge/windows/` |
| Native macOS | `./gradlew bridgePackage` **once** on `macos-26` in `build-native-macos.yml`; artifact `native-macos` = archive + sidecar + `SHA256SUMS` | `verify/verify-native.sh` with two locally produced run dirs | `build/native-bridge/macos/`, `build/verification/native-bridge/macos/` |
| Supply chain | `verify/verify-supply-chain.sh` in `build-supply-chain.yml` (policy enforcement runs in CI); artifact `supply-chain-reports` | same script locally | `build/sbom/*.cdx.json`, `build/verification/supply-chain/*.txt` |

#### What the native comparison actually proves

`verify/verify-native.sh` is a **stable-payload comparison**, not
installer-byte reproducibility. After its runner-identity gate, it compares
byte-for-byte across the two runs only the stable raw evidence — the raw host
executable (`veles-native-bridge`, exposed inside the `.app` bundle on macOS)
and the Chrome native-messaging manifest files. Tauri's platform installers
(NSIS `-setup.exe`, `.msi`, `.dmg`, and `.app` bundle metadata) embed
producer-controlled timestamps and image identifiers, so they — and the
enclosing deterministic archive plus its `.sha256` sidecar, which necessarily
drift with those bytes — are **exempt** from cross-run byte equality. Within
each single run the verifier still binds the packaged archive byte-for-byte
to the extracted view, so a run cannot ship a payload that differs from its
packaging.

#### Supply-chain verifier local prerequisites

`verify/verify-supply-chain.sh` assumes the verification tooling is already
provisioned (CI does this in `build-supply-chain.yml`; locally you must do it
first). Exact prerequisites:

1. **Node 26.8.1 + npm 11.19.0** on PATH (the script asserts both).
2. **npm verification tools** — install once:
   ```
   npm ci --ignore-scripts --prefix verify
   ```
   This populates `verify/node_modules/.bin/` with `cyclonedx-npm` and
   `license-checker-rseidelsohn` from `verify/package-lock.json`.
3. **Cargo verification tools** — `verify/generate-sboms.sh` installs
   `cargo-cyclonedx` via `./gradlew verifyCargoCyclonedx`, but `cargo-deny` must
   be provisioned beforehand:
   ```
   ./gradlew installCargoDeny
   ```
   (Both land under `build/verify-tools/`; `cargo-deny` is also installed by
   this Gradle task from `verify/cargo-tools.toml`.)

Only then run `verify/verify-supply-chain.sh`. The Rust toolchain
(`rust/rust-toolchain.toml`) must also be active because `generate-sboms.sh`
shells out to `./gradlew verifyCargoCyclonedx`.

### Output and evidence paths

CI uploads each component's product artifact under its neutral name (see the
component table above); the paths below are the **optional local verifier**
outputs (plus the ordinary producer trees every build creates):

| Path | Contents |
|---|---|
| `build/verification/SHA256SUMS.toolchains` | aggregate manifest: `android/`, `web-extension/`, `rust/`, `native-bridge/windows/`, `native-bridge/macos/` namespaces |
| `build/verification/android/app-release-unsigned.apk` | canonical unsigned reference APK |
| `build/verification/native-bridge/{windows,macos}/` | verified native product bytes, `METADATA.native-bridge.jsonl`, `SHA256SUMS.native-bridge` |
| `build/sbom/web-extension.cdx.json` | CycloneDX SBOM (web, incl. dev deps) |
| `build/sbom/rust.cdx.json` | CycloneDX SBOM (`rust/Cargo.lock`) |
| `build/sbom/native-bridge.cdx.json` | CycloneDX SBOM (`native-bridge/src-tauri/Cargo.lock`) |
| `build/verification/supply-chain/npm-licenses.txt` | npm license decisions (both trees) |
| `build/verification/supply-chain/cargo-licenses.txt` | cargo-deny license decisions (both lockfiles) |
| `build/verification/supply-chain/npm-install-scripts.txt` | npm lifecycle-script audit |
| `build/verification/supply-chain/cargo-build-scripts.txt` | cargo build-script network/remote-code scan |
| `build/verification/supply-chain/remote-code.txt` | repository remote-origin/CSP/updater scan |
| `build/rust-package/` | staged `jni/<abi>/libveles_crypto.so`, `wasm/`, `SHA256SUMS` |
| `build/web-extension/` | extension ZIP + sidecar + `SHA256SUMS` |
| `build/native-bridge/{windows,macos}/` | packaged product + sidecar + `SHA256SUMS` |
| `build/verify-tools/` | `cargo-cyclonedx` and `cargo-deny` install roots (verification-only) |

SBOMs, license/script/remote-code reports, component checksum manifests, signed
APK bytes, mapping files, run-slot transport archives, and runner identity are
evidence only — they are **not** included in `SHA256SUMS.toolchains`.

### Policy files

| File | Scope |
|---|---|
| `licenses.toml` | single cargo-deny policy used against both Rust lockfiles |
| `.license-policy.json` | single npm policy used by the license wrapper for both npm lockfiles |
| `verify/install-script-policy.json` | exact reviewed npm lifecycle command/content hashes |
| `verify/cargo-build-script-policy.json` | exact reviewed cargo build-script exceptions |
| `verify/cargo-tools.toml` | pins `cargo-cyclonedx` 0.5.9 and `cargo-deny` 0.20.2 |
| `verify/package.json` + `verify/package-lock.json` | pins `@cyclonedx/cyclonedx-npm` 6.0.1 and `license-checker-rseidelsohn` 5.0.1 |

### Exit codes

All component verifiers and `verify-all.sh` use the same three-status contract:

- **0** — every requested check matched.
- **1** — artifact bytes, artifact allow-list, license policy, lifecycle policy,
  remote-code policy, or generated evidence did not match its accepted contract.
- **2** — usage, missing input/tool, unsupported environment, pin mismatch,
  build failure preventing comparison, missing native identity, or unmatched
  native environment identity.

A component never emits a success manifest after failure. Build failure is not
misreported as artifact mismatch. Native identity mismatch prints both complete
identity triples and the instruction `re-run on matched image`; it exits 2 and
is not classified as artifact drift.

### Node: floor versus exact reference

The developer-facing package contract is a **floor**: `engines.node = ">=22.0.0"`
in `web-extension/package.json` and `native-bridge/package.json`. Any Node ≥ 22
runs ordinary development, unit tests, and packaging locally.

Reproducibility and supply-chain environments require an **exact** Node pin:
Node **26.8.1** with bundled npm **11.19.0**. The web reference image
(`verify/Dockerfile.web`) and the Rust reference image (`verify/Dockerfile.rust`)
both assert `node --version` = `v26.8.1` and `npm --version` = `11.19.0`, and
`verify/verify-supply-chain.sh` requires the same exact versions before running
any SBOM/license/script scan. `verify/package.json` pins
`@cyclonedx/cyclonedx-npm` 6.0.1 and `license-checker-rseidelsohn` 5.0.1 and
installs with `npm ci --ignore-scripts`, executing only from
`verify/node_modules/.bin/` — no `npx` or global fallback.

> **Known upstream discrepancy.** `license-checker-rseidelsohn` is pinned to
> **5.0.1** in `verify/package-lock.json` (the authoritative dependency contract),
> but its CLI banner prints `4.4.2`. This is a stale version string shipped by
> the upstream package itself; the lockfile pin is authoritative and the
> discrepancy is documented here, not "fixed" by drifting the pin.

### Reference environments

| Image | Pinned by | Asserts |
|---|---|---|
| `verify/Dockerfile` (Android) | `.java-version`, `gradle/libs.versions.toml` (NDK), `rust/rust-toolchain.toml` | JDK 21, Android SDK/NDK, Rust 1.98.0 |
| `verify/Dockerfile.web` | digest-pinned `node:26.8.1-bookworm-slim` | Node 26.8.1, npm 11.19.0 |
| `verify/Dockerfile.rust` | digest-pinned base + committed archive hashes | JDK 21, Android SDK/NDK, Rust 1.98.0, cargo-ndk 4.1.2, wasm-pack 0.15.0, wasm-bindgen-cli 0.2.127, Node 26.8.1 |

### Runner identities (native, optional local comparison only)

Native CI builds run on **versioned** GitHub-hosted runner labels, not
immutable image identifiers. The project makes **no claim** that
`windows-2025` or `macos-26` pins an underlying image SHA; GitHub-hosted
runner images update weekly. Production jobs build once per platform and
perform **no** identity comparison. The three identity fields — `ImageOS`,
`ImageVersion`, and `RUNNER_ARCH` — are recorded and compared only by the
optional local `verify-native.sh` two-run comparison, which compares stable
payloads only when all three match across both runs.

| Platform | Runner label | Toolchain pins |
|---|---|---|
| Windows | `windows-2025` | Node 26.8.1, Rust 1.98.0, Tauri CLI 2.6.0, isolated WiX/NSIS cache (provisioned by `verify/native/provision-windows-tools.ps1`, with its absolute `NSIS_PATH` exported) |
| macOS | `macos-26` | Node 26.8.1, Rust 1.98.0, Tauri CLI 2.6.0, job-level `DEVELOPER_DIR=/Applications/Xcode_26.6.app` (Xcode build `17F113`, SDK `macosx26.5` asserted) |

If identities differ between the two local runs, the optional verifier exits
2 and prints both triples plus `re-run on matched image`. Identity mismatch is
an environment error, not artifact drift.

### Identity-mismatch recovery (local tool)

When the two local runs of a platform record different `ImageOS`,
`ImageVersion`, or `RUNNER_ARCH` triples, `verify-native.sh` does **not**
compare bytes. It exits 2, prints both triples, and instructs the operator to
**re-run on a matched image** — produce the two run directories again (e.g.
from fresh builds after the weekly runner image rolls) until both record the
same triple. The verifier never normalizes or ignores a differing identity
field. Production CI is unaffected: it performs no such comparison and needs
no re-running.

### Offline acquisition and package boundary

The trust boundary differs per component. The **candidate** (your local
checkout) always builds with normal host networking. The **reference** boundary
is what each verifier actually enforces:

- **Web** — the candidate runs `npm ci` + `npm run package` on the host
  (`verify/verify-web.sh:36-44`); the reference image installs with
  `npm ci --ignore-scripts` then runs `npm run package` inside a container with
  `--network=none` (`verify/verify-web.sh:58`). Byte comparison proves the
  host-built candidate equals the offline-built reference.
- **Rust** — the candidate runs `./gradlew rustPackage` on the host
  (`verify/verify-rust.sh:28`); the reference image builds the package in a
  copied worktree with `--network=none` (`verify/verify-rust.sh:38`).
- **Android** — the reference rebuild runs inside the pinned `verify/Dockerfile`
  image **with networking**: `verify/verify.sh:67-68` invokes `docker run`
  without `--network=none`, and `verify/verify-inner.sh:21-28` clones the
  repository and runs `./gradlew assembleRelease` with network access so Gradle
  can download dependencies. Reproducibility here rests on the pinned image,
  committed lockfiles (`--locked`), and `apksigcopier` byte/signature
  comparison — not on an offline package step. The APK is the one component
  whose packaging is not network-denied.
- **Native** — production CI acquires and builds with normal host networking:
  `bridgeInstall`, `bridgeBuild`, and `bridgePackage` run online on the target
  platform runner, and the pinned WiX/NSIS cache is provisioned before
  packaging. The committed offline wrappers (`verify/native/network-deny-windows.ps1`,
  `verify/native/network-deny-macos.sh`) remain **optional local diagnostics**:
  they provision the same pinned cache, prove connectivity with a fixed HTTPS
  probe, deny outbound network **only for `bridgePackage`** (Windows via a
  `NetFirewallRule` outbound deny; macOS via a `sandbox-exec` process-tree
  profile instead of host-wide PF mutation), require the probe to fail during
  denial, set Cargo offline mode, and restore host networking in cleanup.
- Failure of the pre-denial probe is an environment error (exit 2), not proof of
  isolation. Static scanning alone is not accepted as evidence that packaging
  was offline.

### Clean contract

Root `./gradlew clean` removes every declared generated product, verification,
SBOM, checksum, and tool output under `build/` plus the two named source-tree
exceptions (`web-extension/dist` and `web-extension/rust-wasm/pkg`) and the
native-bridge source-tree outputs. `rust/scripts/assert-clean.sh` asserts none
of these survive, so a stale artifact can never be mistaken for freshly built
evidence. Gradle recreates `build/reports/problems/` after clean; that is the
only known exception and is excluded from the assertion.

Asserted paths (must be absent after `./gradlew clean`):

```
build/rust-package          build/verification        build/sbom
build/verify-tools          build/web-extension       build/native-bridge
build/rust                  build/rust-tools
web-extension/dist          web-extension/rust-wasm/pkg
native-bridge/src-tauri/target   native-bridge/dist
app/build/generated/jniLibs      app/build/outputs/apk
```

## Pinned toolchain

| Tool | Pinned in |
|---|---|
| Gradle | `gradle/wrapper/gradle-wrapper.properties` |
| AGP, Kotlin, KSP, plugins, libraries | `gradle/libs.versions.toml` |
| JDK (Temurin) | `.java-version` — must match the base image in `verify/Dockerfile` (`verify.sh` enforces this) |
| Android cmdline-tools, platform, build-tools | `verify/Dockerfile` |
| Docker base image | `verify/Dockerfile` (digest-pinned) |
| apksigcopier | `verify/Dockerfile` |
| Rust compiler/components/targets | `rust/rust-toolchain.toml` |
| Rust dependencies | `rust/Cargo.lock` plus exact direct pins in crate manifests |
| Rust auxiliary CLIs | `rust/toolchain-tools.toml` (`cargo install --locked`) |
| Android NDK | `gradle/libs.versions.toml` `ndk`, propagated to AGP and `verify/Dockerfile` |
| Native-bridge Tauri CLI & TS tooling | `native-bridge/package.json` (exact-pinned) + committed `package-lock.json` |
| Native-bridge Rust crate & deps | `native-bridge/src-tauri/Cargo.toml` + committed `Cargo.lock` |
| Verification cargo tools (cargo-cyclonedx, cargo-deny) | `verify/cargo-tools.toml` (`cargo install --locked --version`) |
| Verification npm tools (cyclonedx-npm, license-checker) | `verify/package.json` + committed `verify/package-lock.json` |
| Reference Node runtime (web, rust, supply-chain) | `verify/Dockerfile.web`, `verify/Dockerfile.rust` (Node 26.8.1 / npm 11.19.0) |

Veles' Android code calls into a shared Rust core (`rust/veles-crypto`) via JNI, and the
same crate compiles to WASM for the browser extension. `rust/rust-toolchain.toml` pins the
exact `rustc` channel, components (`clippy`, `rustfmt`), and cross-compilation targets
(three Android ABIs plus `wasm32-unknown-unknown`); rustup auto-selects it for any command
run under `rust/`. `rust/toolchain-tools.toml` pins exact versions of the auxiliary CLIs
(`cargo-ndk`, `wasm-pack`, `wasm-bindgen-cli`) that aren't part of the toolchain itself;
`./gradlew rustInstall` installs/verifies them with `cargo install --locked` into
`build/rust-tools/`, a local, gitignored cache — not committed, not shared across machines.
The 1d verification cargo tools (`cargo-cyclonedx`, `cargo-deny`) install the same way into
`build/verify-tools/`.

Generated output boundaries are intentionally narrow:

- **Android JNI**: `./gradlew rustJni` builds exactly the three ABI filters declared in
  `app/build.gradle.kts` (`arm64-v8a`, `armeabi-v7a`, `x86_64`) into
  `app/build/generated/jniLibs/<abi>/libveles_crypto.so`. This is the *only* JNI output
  path; `:app:assembleDebug`/`assembleRelease` depend on `rustJni` so a clean build always
  regenerates it.
- **Rust package**: `./gradlew rustPackage` stages the three JNI libraries and the
  complete WASM package into `build/rust-package/` with a `SHA256SUMS` manifest.
- **WASM**: `./gradlew rustWasm` builds the web target into `web-extension/rust-wasm/pkg/`.
  This is the one path in the repo that is *not* traceable to source in git — it's build
  output, and `web-extension/rust-wasm/pkg/` is gitignored like `web-extension/dist/`.
- **Local caches**: `build/rust/target` (the Cargo build cache) and `build/rust-tools`
  (the auxiliary-CLI install cache) live under the top-level `build/` directory, which is
  already gitignored; `rust/scripts/assert-clean.sh` asserts none of these generated paths
  (nor `app/build/generated/jniLibs`, nor `app/build/outputs/apk`) survive `./gradlew clean`.

The web-extension's npm dependencies are exact-pinned in `web-extension/package.json`
(no `^`/`~`) and resolved via the committed `package-lock.json`. The deterministic
packaging (zip + sha256 sidecar) and manifest/CSP guard run as npm scripts
(`npm run package`, `npm test`) — no Gradle wrapping needed for the extension since
it never touches the APK. Developer Node/npm are not pinned — `engines.node >= 22.0.0`
is a floor; the exact Node 26.8.1 reference pin lives in `verify/Dockerfile.web` and
`verify/Dockerfile.rust`.

> **Scope note (2026-10-26).** The Tauri-based native-bridge described here is OTP-01 build/packaging
> scaffolding kept as tooling. It is not the approved production runtime: the RFC amendment makes
> the host a plain Rust executable that never starts the Tauri event loop, and retaining Tauri for
> packaging only is not approved and not justified by the current cost evaluation
> (`docs/otp-23-tauri-packaging-evaluation.md`, matrix row D-01); OTP-25 chooses another pipeline or
> brings a separately reviewed justification. The commands and reproducibility checks below remain valid for the scaffold.

The native-bridge (`native-bridge/`) is a Tauri 2.x headless Native Messaging
host. Its npm dependencies (Tauri CLI, ESLint, Prettier, TypeScript, vitest)
are exact-pinned in `native-bridge/package.json` with a committed
`package-lock.json`, following the same pattern as the web-extension. The Rust
bridge crate (`native-bridge/src-tauri/`) pins exact direct dependency versions
and commits `Cargo.lock`. Gradle wraps the npm and cargo entry points as
`bridge*` tasks (group `native-bridge`): `bridgeInstall`, `bridgeFormat`,
`bridgeLint`, `bridgeTypecheck`, `bridgeTest`, `bridgeBuild`
(`cargo tauri build --no-bundle -- --locked`), `bridgePackage`
(platform-specific deterministic zip/tar + sha256 sidecar into
`build/native-bridge/`), and `bridgeManifests` (Chrome native-messaging host
manifests). The bridge is headless (`windows = []`, no tray/menu/autostart),
exits on stdin EOF, and produces unsigned artifacts only — Tauri signing env
vars are documented as absent and `tauri.conf.json` hard-codes unsigned output.
Windows and macOS builds run on pinned GitHub-hosted runners (`windows-2025`,
`macos-26`); production CI builds and packages each platform exactly once
(`bridgePackage`, with the executable and its installers created on the same
target runner rather than via a Linux cross-toolchain), and the optional local
`verify-native.sh` two-run comparison is what checks the stable payload across
independent runs.

The Play "dependency metadata" block (`dependenciesInfo`) is disabled — it is
encrypted with a Google key and inherently non-reproducible.

## Upgrading any pinned tool

The upgrade procedure is a single six-step process. Every step is mandatory;
skipping any step can silently break reproducibility.

1. **Change the human-readable version and immutable digest/hash together.** A
   pin bump touches every pin location for that tool in a single commit (see the
   table above; e.g. a JDK bump touches `.java-version` and the `verify/Dockerfile`
   base image tag + digest together; a Node reference bump touches
   `verify/Dockerfile.web`, `verify/Dockerfile.rust`, and any asserted
   `node --version`/`npm --version` checks together).
2. **Regenerate and review the affected committed lockfile.** Use the
   least-drift regeneration flags so unrelated pins do not move:
   - npm: `npm install --package-lock-only` in the affected tree (`web-extension`,
     `native-bridge`, or `verify/`), then review and commit `package-lock.json`.
   - Cargo: `cargo update -p <crate> --precise <new-version>` (or `cargo update
     -p <crate>` for a latest-compatible bump) with `--locked` removed for the
     update command only, then review and commit `Cargo.lock`.
   - Verification cargo tools: bump the version in `verify/cargo-tools.toml` and
     let `./gradlew verifyCargoCyclonedx verifyCargoDeny` reinstall.
   CI uses `npm ci` and Cargo `--locked`; it never repairs drift.
3. **Review tool and runner-image release notes.** Toolchain bumps (new R8, new
   resource packer, new Tauri bundler) are the most likely source of
   reproducibility breakage; runner-image bumps can change WiX/NSIS/Xcode
   versions under a stable label.
4. **Update lifecycle/license exceptions only with package text and rationale.**
   A new npm lifecycle script, cargo build-script exception, or license
   expression requires the exact package/version and the reviewed content/hash
   recorded in the relevant policy file (`verify/install-script-policy.json`,
   `verify/cargo-build-script-policy.json`, `licenses.toml`, or
   `.license-policy.json`) — never a floating name-only exception.
5. **Re-verify the affected component locally before merging.** Build the
   affected artifact from the branch and run the matching component verifier
   from the same checkout:
   | Affected component | Build | Verify |
   |---|---|---|
   | Android APK | `./gradlew assembleRelease` | `verify/verify.sh <apk> <ref>` |
   | Web-extension | `npm run package --prefix web-extension` | `verify/verify-web.sh` |
   | Rust JNI/WASM | `./gradlew rustPackage` | `verify/verify-rust.sh` |
   | Native Windows/macOS | `./gradlew bridgePackage` (on the target platform) | `verify/verify-native.sh <commit> <run-a> <run-b>` with two independent run dirs |
   | Supply chain | (no separate build) | `verify/verify-supply-chain.sh` |
   The Docker-based verifiers (`verify.sh`, `verify-web.sh`, `verify-rust.sh`)
   require Docker; the native verifier requires two pre-built run directories.
6. **Run the complete labeled or manually dispatched graph before merging.** A
   pull request carrying the `release-build` label, or a manual
   `workflow_dispatch` from `master`, runs the complete single-build component
   graph and the supply-chain policy checks. These producers plus the optional
   local verifiers from step 5 are the determinism evidence: publication itself
   no longer gates on a reproducibility comparison, so a tool that breaks
   determinism shows up in step 5's local comparison rather than blocking a
   release. If a tool is nondeterministic at the new version, pin or configure
   around it and record the finding here.

Runner documentation states that image labels are versioned but images update
weekly. Recorded identity permits post-hoc equality only (in the optional local
native comparison). If stable labels or installed Xcode/tool versions
disappear, the pin update follows this procedure rather than silently
selecting a default.

## Caveats

- Verification targets **tags**, not arbitrary commits (except pre-merge local
  verification, which may pass `HEAD`).
- Verify a release with the `verify/` toolkit **from that release's tag** — each
  tag carries the exact pins used to build it, so old releases stay verifiable
  after later toolchain upgrades.
- The rebuild clones with full history: `versionCode`/`versionName` come from git
  tags via the androidgitversion plugin.
- Dependencies are downloaded during the rebuild, so Android verification needs
  network access (its reference build clones and `./gradlew assembleRelease` with
  networking — see "Offline acquisition and package boundary" above). Web and
  Rust references package with `--network=none`; their candidate builds need
  network for acquisition. Production native `bridgePackage` runs online; the
  network-deny wrappers are optional local offline diagnostics.
- Native `verify-all.sh` requires two independent run directories per platform
  as local inputs. CI no longer produces run transports, so produce both runs
  yourself — each from its own **clean checkout** of the exact commit on the
  target platform (two `bridgePackage` invocations in one Gradle workspace are
  not independent; Gradle may reuse the first build's outputs). Assemble each
  transport with `verify/native/extract-view.mjs` and
  `verify/native/prepare-run.sh` / `prepare-run.ps1` as described above.
- Publication makes **no reproducibility claim**: releases are attested and
  checksummed, but a passing build is not evidence that a second build would
  be byte-identical. Only the optional local verifiers provide that evidence.