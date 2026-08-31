# Reproducible builds & release verification

Veles reads your notifications — the most sensitive permission on Android. "Audit
the source" only means something if you can prove the APK you installed was built
from that source. Every release can be verified two ways.

## What each path proves

| Path | Proves | You trust |
|---|---|---|
| Attestation (one-liner) | This exact file was built by this repo's release workflow at this tagged commit | GitHub (runner + Sigstore) |
| Reproducible rebuild | This exact file is byte-identical (ignoring the signature) to what the tagged source produces | Nothing but the source tree and your own machine |

Additionally, CI rebuilds every release independently **before** it is published:
the release is created as a draft, verified by a second job, and only published if
the rebuild matches. A release that cannot be reproduced never goes public.

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

## Path 3 — full toolchain verification (OTP-01 sub-project 1d)

1d closes OTP-01 by proving the Android APK, web-extension, shared Rust JNI/WASM,
and unsigned native-bridge artifacts are reproducible in their documented build
environments, and by adding auditable SBOM, license, install-script, and
remote-code enforcement. It does **not** replace npm, Gradle, Cargo, Vite,
wasm-pack, or Tauri as artifact producers; it adds small component verifiers,
reusable component workflows, and a tiny local aggregate entry point around
those existing producers.

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
deterministic transport tars with SHA-256 sidecars) is:

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

From the repository root, `verify-all.sh` invokes, in order, and stops on the
first failure:

1. `verify/verify.sh <apk> <resolved-commit>` — Android rebuild + signature-stripped compare; also exports the canonical unsigned reference APK to `build/verification/android/app-release-unsigned.apk`.
2. `verify/verify-web.sh` — web-extension candidate/reference byte compare.
3. `verify/verify-rust.sh` — Rust JNI/WASM candidate/reference byte compare.
4. `verify/verify-native.sh <resolved-commit> <native-run-a-dir> <native-run-b-dir>` — two independent Windows and two macOS runs, identity gate, then byte compare; output under `build/verification/native-bridge/windows/` and `build/verification/native-bridge/macos/`. (CI calls `verify-native.sh` once per platform with a single-tar run dir, uploading the flat `build/verification/native-bridge/` tree per platform; the toolchain-manifest workflow then downloads each into its platform subdirectory. The local `verify-all.sh` two-platform layout drives both platforms in one call.)
5. `verify/verify-supply-chain.sh` — SBOM, license, install-script, and remote-code enforcement; reports under `build/verification/supply-chain/`.
6. `verify/aggregate-checksums.sh <resolved-commit>` — assembles `build/verification/SHA256SUMS.toolchains`.

`verify-all.sh` is intentionally small: it validates arguments, calls the
component verifiers in a fixed documented order, stops on the first failure,
normalizes and propagates the component exit status, and prints a component
summary. It contains no build implementation, checksum parser, runner-identity
parser, artifact allow-list, SBOM generator, license decision, or remote-code
scanner. It fails with exit 2 rather than silently skipping an input or
component. **CI never invokes `verify-all.sh`**; CI builds and verifies each
component in its own `build-<component>.yml` reusable workflow.

### Component commands

| Component | Command | Builds | Verifier output |
|---|---|---|---|
| Android APK | `verify/verify.sh <apk> <ref>` | `./gradlew assembleRelease` in `verify/Dockerfile` | `build/verification/android/app-release-unsigned.apk` |
| Web-extension | `npm run package --prefix web-extension` then `verify/verify-web.sh` | Vite build + deterministic zip | `build/web-extension/{veles-extension-<version>.zip,.zip.sha256,SHA256SUMS}` |
| Rust JNI/WASM | `./gradlew rustPackage` then `verify/verify-rust.sh` | three ABIs + WASM package | `build/rust-package/{jni,wasm,SHA256SUMS}` |
| Native Windows | `./gradlew bridgePackage` on `windows-2025` (two runs) | unsigned WiX/NSIS installers | `build/native-bridge/windows/`, `build/verification/native-bridge/windows/` |
| Native macOS | `./gradlew bridgePackage` on `macos-26` (two runs) | unsigned `.app`/`.dmg` | `build/native-bridge/macos/`, `build/verification/native-bridge/macos/` |
| Supply chain | `verify/verify-supply-chain.sh` | SBOM + license + script + remote-code reports | `build/sbom/*.cdx.json`, `build/verification/supply-chain/*.txt` |
| Aggregate | `verify/aggregate-checksums.sh <commit>` | namespaced checksum manifest | `build/verification/SHA256SUMS.toolchains` |

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

### Runner identities (native)

Native builds run on **versioned** GitHub-hosted runner labels, not immutable
image identifiers. The project makes **no claim** that `windows-2025` or
`macos-26` pins an underlying image SHA; GitHub-hosted runner images update
weekly. Each run records and compares three identity fields — `ImageOS`,
`ImageVersion`, and `RUNNER_ARCH` — and bytes are compared only when all three
match across both independent runs of a platform.

| Platform | Runner label | Architecture | Toolchain pins |
|---|---|---|---|
| Windows | `windows-2025` | `X64` (asserted) | Node 26.8.1, Rust 1.98.0, Tauri CLI 2.6.0, isolated WiX/NSIS cache |
| macOS | `macos-26` | `ARM64` (asserted) | Node 26.8.1, Rust 1.98.0, Tauri CLI 2.6.0 |

macOS additionally sets `DEVELOPER_DIR=/Applications/Xcode_26.6.app`, asserts Xcode
build **17F113**, and asserts SDK **macosx26.5**. Windows asserts `RUNNER_ARCH`
is `X64`. If identities differ between the two runs, verification exits 2 and
prints both triples plus `re-run on matched image`. Identity mismatch is an
environment error, not artifact drift.

### Native re-run instruction

When the two independent runs of a platform record different `ImageOS`,
`ImageVersion`, or `RUNNER_ARCH` triples, the verifier does **not** compare bytes.
It exits 2, prints both triples, and instructs the operator to **re-run on a
matched image** (re-dispatch the workflow until both land on the same weekly
image). This is the only recovery path for an identity mismatch; the verifier
never normalizes or ignores a differing identity field.

### Offline acquisition and package boundary

Every product packaging flow separates **acquisition** from **execution**. The
**candidate** (your local checkout) builds with normal host networking; the
**reference** (inside the pinned Docker image) packages with `--network=none`
so a reproducible reference can never fetch undisclosed inputs. Byte comparison
then proves the host-built candidate equals the offline-built reference.

- **Web** — the candidate runs `npm ci` + `npm run package` on the host
  (`verify/verify-web.sh:36-44`); the reference image installs with
  `npm ci --ignore-scripts` then runs `npm run package` inside a container with
  `--network=none` (`verify/verify-web.sh:58`).
- **Rust** — the candidate runs `./gradlew rustPackage` on the host
  (`verify/verify-rust.sh:28`); the reference image builds the package in a
  copied worktree with `--network=none` (`verify/verify-rust.sh:38`).
- **Native** — each CI job completes `npm ci`, `cargo fetch --locked`, Rust
  toolchain setup, and (Windows) the pinned Tauri WiX/NSIS cache provisioning
  **before** activating a platform outbound-network deny. The job proves
  connectivity by reaching one fixed HTTPS probe immediately before denial,
  activates and inspects the deny rule, requires the same probe to fail during
  denial, sets Cargo offline mode, runs `bridgePackage`, and restores host
  networking in an unconditional cleanup step. The candidate `bridgeBuild`/`bridgePackage` runs with host networking during acquisition; only the final
  `bridgePackage` runs under the deny.
  - **macOS** uses `sandbox-exec` **process-tree** isolation
    (`(deny network-outbound)`) instead of host-wide PF mutation. This is an
    accepted exception to the platform-wide-deny rule: it confines the deny to
    the packaging process tree and avoids mutating shared host firewall state on
    a multi-tenant runner.
  - **Windows** uses a `NetFirewallRule` outbound deny.
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
`macos-26`); two independent runs per platform are compared after an identity
gate, and the verified output lands under `build/verification/native-bridge/`.

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
6. **Run the complete labeled or manually dispatched graph before accepting the
   new reference environment.** A pull request carrying the `release-build`
   label, or a manual `workflow_dispatch` from `master`, runs the complete
   component graph. The release workflow's verify job is the enforcement gate:
   if the bump broke determinism, the next release stays an unpublished draft
   until fixed. If a tool is nondeterministic at the new version, pin or
   configure around it and record the finding here.

Runner documentation states that image labels are versioned but images update
weekly. Recorded identity permits post-hoc equality only. If stable labels or
installed Xcode/tool versions disappear, the pin update follows this procedure
rather than silently selecting a default.

## Caveats

- Verification targets **tags**, not arbitrary commits (except pre-merge local
  verification, which may pass `HEAD`).
- Verify a release with the `verify/` toolkit **from that release's tag** — each
  tag carries the exact pins used to build it, so old releases stay verifiable
  after later toolchain upgrades.
- The rebuild clones with full history: `versionCode`/`versionName` come from git
  tags via the androidgitversion plugin.
- Dependencies are downloaded during the rebuild, so APK/Rust verification needs
  network access for acquisition; packaging itself runs offline (the build
  embeds nothing environment-specific).
- Native `verify-all.sh` requires two independent run directories per platform
  as local inputs; the runs are produced by the reusable native workflows, not
  by the local orchestrator.