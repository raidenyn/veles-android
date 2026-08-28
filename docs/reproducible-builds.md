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

## Path 2 — zero-trust rebuild (minutes, needs Docker + bash)

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

Veles' Android code calls into a shared Rust core (`rust/veles-crypto`) via JNI, and the
same crate compiles to WASM for the browser extension. `rust/rust-toolchain.toml` pins the
exact `rustc` channel, components (`clippy`, `rustfmt`), and cross-compilation targets
(three Android ABIs plus `wasm32-unknown-unknown`); rustup auto-selects it for any command
run under `rust/`. `rust/toolchain-tools.toml` pins exact versions of the auxiliary CLIs
(`cargo-ndk`, `wasm-pack`, `wasm-bindgen-cli`) that aren't part of the toolchain itself;
`./gradlew rustInstall` installs/verifies them with `cargo install --locked` into
`build/rust-tools/`, a local, gitignored cache — not committed, not shared across machines.

Generated output boundaries are intentionally narrow:

- **Android JNI**: `./gradlew rustJni` builds exactly the three ABI filters declared in
  `app/build.gradle.kts` (`arm64-v8a`, `armeabi-v7a`, `x86_64`) into
  `app/build/generated/jniLibs/<abi>/libveles_crypto.so`. This is the *only* JNI output
  path; `:app:assembleDebug`/`assembleRelease` depend on `rustJni` so a clean build always
  regenerates it.
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
is a floor, and CI runs Node 22 LTS. The zero-trust reference environment with an
exact Node runtime pin lands in OTP-01 sub-project 1d (`verify/Dockerfile.web`).

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
Windows and macOS builds run on pinned GitHub-hosted runners (`windows-2022`,
`macos-15`); the Linux-side byte-compare harness lands in 1d.

The Play "dependency metadata" block (`dependenciesInfo`) is disabled — it is
encrypted with a Google key and inherently non-reproducible.

## Upgrading any pinned tool

1. Update **all** pin locations for that tool in a single commit (see the table
   above; a JDK bump touches `.java-version` and the `verify/Dockerfile` base
   image tag + digest together).
2. Re-verify determinism locally before merging: build a release APK from the
   branch and run `verify/verify.sh` from the same checkout against it
   (`VELES_REPO_URL=$PWD verify/verify.sh <apk> $(git rev-parse HEAD)`).
   Toolchain bumps (new R8, new resource packer) are the most likely source of
   reproducibility breakage.
3. Merge. The release workflow's verify job is the enforcement gate: if the bump
   broke determinism, the next release stays an unpublished draft until fixed.
4. If a tool is nondeterministic at the new version, pin or configure around it
   and record the finding here.

## Caveats

- Verification targets **tags**, not arbitrary commits.
- Verify a release with the `verify/` toolkit **from that release's tag** — each
  tag carries the exact pins used to build it, so old releases stay verifiable
  after later toolchain upgrades.
- The rebuild clones with full history: `versionCode`/`versionName` come from git
  tags via the androidgitversion plugin.
- Dependencies are downloaded during the rebuild, so verification needs network
  access (the build itself embeds nothing environment-specific).
