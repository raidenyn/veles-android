# Reproducible Builds & Release Verification — Design

**Issue:** [#37](https://github.com/raidenyn/veles-android/issues/37)
**Date:** 2026-07-10
**Status:** Approved

## Goal

Anybody, with no trust in the maintainer, can take a released `veles-X.Y.Z.apk` and a git tag
and verify they correspond — ideally in one command. This backs the README's core privacy
promise ("audit the entire source code"): auditing source means nothing if the installed APK
can't be tied to that source.

## Approach (hybrid, per issue #37)

Two complementary trust paths:

1. **Build provenance attestation (easy path).** The release workflow attests the APK via
   GitHub Artifact Attestations (Sigstore). Verification:
   `gh attestation verify veles-X.Y.Z.apk --repo raidenyn/veles-android`.
   Proves this exact file was built by this workflow at this commit. Trust anchor: GitHub.
2. **Bit-for-bit reproducibility (zero-trust path).** The release build is deterministic; a
   Docker-based script rebuilds the tag locally and compares against the released APK with
   signature stripping (`apksigcopier compare` — the F-Droid technique). Trust anchor: nothing
   but the source tree.

Alternatives (attestation-only, reproducibility-only, publishing a parallel unsigned APK) were
considered and rejected in the issue; the hybrid keeps a near-free one-liner for casual users
while the apksigcopier-based pipeline satisfies F-Droid's verifier later with no extra work.

## Decisions made during design review

| Decision | Choice |
|---|---|
| Release verification ordering | **Draft → verify → publish.** The release is created as a draft; an independent CI job rebuilds and compares; the release is published only if verification passes. An unverified APK is never public. |
| End-to-end testing | **Throwaway/real patch tag.** The first patch release after this lands (e.g. `0.0.2`) doubles as the E2E test; no rc-tag machinery, no fork rehearsal. |
| Determinism check on master pushes | **No.** Release time is the sole enforcement point; `release-build.yml` is untouched. |

## Components

### 1. Build determinism (`app/build.gradle.kts` + toolchain pinning)

- Add to the `android {}` block (required for reproducibility and for F-Droid):

  ```kotlin
  dependenciesInfo {
      includeInApk = false
      includeInBundle = false
  }
  ```

- **JDK pin, single source of truth:** a `.java-version` file at repo root with an exact
  Temurin version (the latest Temurin 17 patch release at implementation time). Consumed by `actions/setup-java` via `java-version-file:`
  in `release.yml` (and `ci.yml`/`release-build.yml` for consistency) and by the verify
  Dockerfile. Gradle is already pinned by the wrapper (8.11.1); AGP/Kotlin by the version
  catalog.
- **Determinism audit (implementation step, not a shipped artifact):** build the same commit
  twice in fresh containers, `diffoscope` the unsigned APKs, and fix what surfaces. Expected
  suspects: `buildToolsVersion` drift (pin explicitly only if the audit shows drift), R8
  output, baseline profiles (`assets/dexopt/baseline.prof*`), KSP/Room/Hilt generated-source
  ordering, ZIP entry timestamps/ordering (AGP normalizes; verify).
- **Environment independence:** the build must not absorb hostname, user, absolute paths,
  locale, or TZ. The Docker reference environment makes this moot for the supported verify
  path; the release-time double build catches accidental regressions.
- `versionName`/`versionCode` come from androidgitversion and are deterministic when building
  an exact tag checkout with full history and tags present; the verify flow clones accordingly.

### 2. `verify/` toolkit (zero-trust local verification)

- **`verify/Dockerfile`** — the reference build environment: base image pinned by digest,
  Temurin JDK matching `.java-version`, pinned Android cmdline-tools + platform + build-tools,
  pinned `apksigcopier`.
- **`verify/verify.sh <path-to-released-apk> <tag>`**:
  1. Builds the Docker image.
  2. Inside the container: clones the repo at `<tag>` (full history so androidgitversion
     resolves), runs `./gradlew assembleRelease` with **no** `VELES_KEYSTORE_*` env vars →
     `app-release-unsigned.apk`.
  3. Runs `apksigcopier compare <released.apk> --unsigned <rebuilt-unsigned.apk>`.
  4. Prints SHA-256 of both artifacts and a clear verdict. Exit code 0 = verified.
- Runs on any machine with only Docker installed; no host JDK/Android SDK.
- The script must not depend on GitHub-only context (works from a bare clone) and must not
  require network state at build time beyond dependency downloads — F-Droid constraint.

### 3. Release flow: draft → verify → publish (`.github/workflows/release.yml`)

The current single `release` job becomes three jobs:

1. **build** — everything the current job does (full-history checkout, pinned JDK via
   `.java-version`, optional keystore signing, `assembleRelease`, tag-vs-versionName check),
   plus:
   - Generate `SHA256SUMS` covering the APK and `mapping.txt`.
   - Attest the APK and `mapping.txt` with `actions/attest-build-provenance`
     (workflow permissions gain `id-token: write` and `attestations: write`).
   - Create the GitHub Release as a **draft** with APK, `mapping.txt`, `SHA256SUMS`, and
     release notes that include a short "Verify this download" blurb (both verification
     commands, link to the doc).
   - Upload the APK as a workflow artifact for the verify job.
2. **verify** — `needs: build`, independent runner: downloads the APK artifact and runs
   **literally `verify/verify.sh`** (single source of truth with the local path). A mismatch
   fails the workflow; the release stays draft and nothing unverified is ever public.
3. **publish** — `needs: verify`: `gh release edit "$TAG" --draft=false`.

Design nuance: the verify job compares the APK from the **workflow artifact**, not the draft
release asset (draft-asset downloads are unreliable via `gh`). The bytes are identical — the
same job uploads both — and the attestation plus `SHA256SUMS` bind the published asset to that
digest, so external verifiers can independently confirm the published file.

`release-build.yml` and `ci.yml` are unchanged apart from adopting `.java-version`.

### 4. Documentation

- **`docs/reproducible-builds.md`**: threat model (what each path proves and what it trusts),
  the `gh attestation verify` one-liner, the Docker rebuild path, the pinned toolchain, and
  caveats (verification targets tags, not arbitrary commits; the rebuild clone needs full
  history and tags).
- **README**: short "Verify your download" section under Privacy, linking to the doc.

## Error handling

- **Verification mismatch at release time:** verify job fails, workflow goes red, release
  remains a draft. The draft's APK and the CI rebuild are both available for `diffoscope`
  investigation.
- **Tampered APK at user verify time:** both paths fail — attestation digest mismatch, and
  `apksigcopier compare` non-zero exit.
- **Missing keystore secrets:** unchanged behavior — unsigned release; `apksigcopier compare`
  handles a released unsigned APK the same way.

## Testing

- **Pre-merge (local):** double clean-container build of the same commit, byte-compare
  unsigned APKs; run `verify/verify.sh` against a locally built "released" APK; tamper check
  (flip one byte, expect failure).
- **End-to-end:** the first real patch tag after landing (e.g. `0.0.2`) exercises the full
  draft → verify → publish flow. Then, against the published release:
  `gh attestation verify` succeeds, `verify/verify.sh` succeeds on a Docker-only machine, and
  a one-byte tamper makes both fail.

## Acceptance criteria (from issue #37, adjusted for decisions)

- [ ] Release workflow rebuilds the tag independently and byte-compares (signature-stripped)
      before publishing; mismatch leaves the release as a draft.
- [ ] `verify/verify.sh veles-X.Y.Z.apk X.Y.Z` succeeds on a fresh machine with only Docker,
      for a real published release.
- [ ] `gh attestation verify veles-X.Y.Z.apk --repo raidenyn/veles-android` succeeds for new
      releases.
- [ ] Tampering with one byte of the APK makes both verification paths fail.
- [ ] `SHA256SUMS` attached to releases; `docs/reproducible-builds.md` published; README
      updated; release notes carry verification instructions.

## Out of scope

- Determinism checks on master pushes or PRs (explicitly declined).
- rc/pre-release tag support in `release.yml`.
- F-Droid submission itself (this design only preserves forward-compatibility: apksigcopier
  comparison, `dependenciesInfo` disabled, no GitHub-only or network-state build inputs).
