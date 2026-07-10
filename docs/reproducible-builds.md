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
