# Reproducible Builds & Release Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-07-10-reproducible-builds-design.md` (issue #37)

**Goal:** Anyone can verify a released `veles-X.Y.Z.apk` corresponds to its git tag — via a one-line GitHub attestation check or a zero-trust Docker rebuild — and no unverified APK is ever published.

**Architecture:** Make `assembleRelease` deterministic (disable Play dependency metadata, pin the JDK), ship a `verify/` Docker toolkit that rebuilds a tag and compares with `apksigcopier`, and restructure `release.yml` into draft → verify → publish so an independent CI rebuild gates publication. Attestation (`actions/attest-build-provenance`) and `SHA256SUMS` bind the published asset to the verified digest.

**Tech Stack:** Gradle 8.11.1 (wrapper-pinned), AGP 8.9.0 / Kotlin 2.1.10 (catalog-pinned), Temurin JDK 17 (to be pinned exactly), Docker, `apksigcopier`, GitHub Actions + Artifact Attestations, `gh` CLI.

## Global Constraints

- Repo: `raidenyn/veles-android`; default branch `master`; release tags are plain semver, **no `v` prefix** (`[0-9]+\.[0-9]+\.[0-9]+`).
- Do NOT bump Gradle (8.11.1), AGP (8.9.0), or Kotlin (2.1.10) in this work — pins stay as-is.
- `compileSdk = 35` → the reference environment installs exactly `platforms;android-35`, `build-tools;35.0.0`, `platform-tools`.
- The unsigned release APK path is `app/build/outputs/apk/release/app-release-unsigned.apk`; signed is `app-release.apk`; mapping at `app/build/outputs/mapping/release/mapping.txt`.
- `versionName`/`versionCode` come from the androidgitversion plugin — every rebuild clone MUST have full history and tags.
- No determinism checks on master pushes or PRs (explicitly out of scope); `release-build.yml` and `ci.yml` change only their `setup-java` pin.
- The verify container must work with no GitHub-context dependencies (F-Droid constraint): plain `git clone` + `./gradlew assembleRelease`.
- Shell scripts: `bash`, `set -euo pipefail`, executable bit set (`chmod +x`, committed via git).

---

### Task 1: Deterministic build config — `dependenciesInfo` off, JDK pinned

**Files:**
- Modify: `app/build.gradle.kts` (inside the `android {}` block)
- Create: `.java-version`
- Modify: `.github/workflows/ci.yml` (3 × `setup-java`), `.github/workflows/release.yml` (1 ×), `.github/workflows/release-build.yml` (1 ×)

**Interfaces:**
- Produces: `.java-version` at repo root containing an exact Temurin 17 version (e.g. `17.0.16`) — Tasks 2, 3 and the docs depend on this exact file and value.
- Produces: all workflows resolve JDK via `java-version-file: .java-version`.

- [ ] **Step 1: Discover the current exact Temurin 17 release**

```bash
curl -s 'https://api.adoptium.net/v3/info/release_names?release_type=ga&version=%5B17%2C18%29&page_size=1&sort_order=DESC'
```

Expected: JSON like `{"release_names":["jdk-17.0.16+8"]}`. Note both the version (`17.0.16`) and the build (`+8`) — Task 2 needs the build number for the Docker tag.

- [ ] **Step 2: Create `.java-version`**

Content (single line, no whitespace — substitute the discovered version):

```
17.0.16
```

- [ ] **Step 3: Disable Play dependency metadata in `app/build.gradle.kts`**

Add inside the `android {}` block, after the `defaultConfig {}` block:

```kotlin
    // Play "dependency metadata" is encrypted with a Google key and makes the
    // APK inherently non-reproducible; F-Droid also requires it disabled.
    dependenciesInfo {
        includeInApk = false
        includeInBundle = false
    }
```

- [ ] **Step 4: Switch all workflows to the pinned JDK**

In `.github/workflows/ci.yml` (3 occurrences), `.github/workflows/release.yml` (1), `.github/workflows/release-build.yml` (1), replace every:

```yaml
      - uses: actions/setup-java@v5
        with:
          distribution: temurin
          java-version: 17
```

with:

```yaml
      - uses: actions/setup-java@v5
        with:
          distribution: temurin
          java-version-file: .java-version
```

- [ ] **Step 5: Verify the release build still works**

```bash
./gradlew assembleRelease
ls -la app/build/outputs/apk/release/app-release-unsigned.apk
```

Expected: `BUILD SUCCESSFUL`; the unsigned APK exists.

- [ ] **Step 6: Commit**

```bash
git add .java-version app/build.gradle.kts .github/workflows/ci.yml .github/workflows/release.yml .github/workflows/release-build.yml
git commit -m "build: disable dependency metadata and pin exact JDK for reproducibility (#37)"
```

---

### Task 2: Reference build environment — `verify/Dockerfile` + `verify/verify-inner.sh`

**Files:**
- Create: `verify/Dockerfile`
- Create: `verify/verify-inner.sh`

**Interfaces:**
- Consumes: `.java-version` from Task 1 (the Dockerfile's `FROM` tag must match it; Task 3's script enforces this with a grep).
- Produces: image entrypoint `verify-inner.sh <tag-or-ref>` with contract:
  - clones `$VELES_REPO_URL` (default `https://github.com/raidenyn/veles-android`) with full history, checks out `<tag-or-ref>`, runs `./gradlew --no-daemon assembleRelease` unsigned;
  - if a directory `/out` is mounted → copies `app-release-unsigned.apk` there (build-only / audit mode);
  - if a file `/apk/released.apk` is mounted → compares released vs rebuilt (byte-identical, else `apksigcopier compare`), exit 0 = verified, exit 1 = mismatch;
  - at least one of `/out`, `/apk/released.apk` must be present, else exit 2.

- [ ] **Step 1: Discover the digest-pinned base image**

Using the version and build from Task 1 Step 1 (example `17.0.16` build `8` → tag `17.0.16_8-jdk-noble`):

```bash
docker buildx imagetools inspect eclipse-temurin:17.0.16_8-jdk-noble | sed -n '1,3p'
```

Expected output includes a line `Digest: sha256:<64 hex chars>`. Record `<tag>@<digest>`.

- [ ] **Step 2: Write `verify/Dockerfile`**

Substitute the real tag + digest from Step 1 in the `JAVA_IMAGE` default:

```dockerfile
# Reference build environment for reproducible-build verification.
# The JDK tag below MUST match .java-version at the repo root
# (verify.sh enforces this). See docs/reproducible-builds.md for the
# toolchain upgrade procedure.
ARG JAVA_IMAGE=eclipse-temurin:17.0.16_8-jdk-noble@sha256:REPLACE_WITH_REAL_DIGEST
FROM ${JAVA_IMAGE}

ENV TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8
ENV ANDROID_HOME=/opt/android-sdk
ENV PATH=${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${PATH}

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl unzip ca-certificates python3-pip \
 && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages apksigcopier==1.1.1

ARG CMDLINE_TOOLS=11076708
RUN mkdir -p ${ANDROID_HOME}/cmdline-tools \
 && curl -fsSL -o /tmp/cmdline-tools.zip "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS}_latest.zip" \
 && unzip -q /tmp/cmdline-tools.zip -d ${ANDROID_HOME}/cmdline-tools \
 && mv ${ANDROID_HOME}/cmdline-tools/cmdline-tools ${ANDROID_HOME}/cmdline-tools/latest \
 && rm /tmp/cmdline-tools.zip

RUN yes | sdkmanager --licenses > /dev/null \
 && sdkmanager "platform-tools" "platforms;android-35" "build-tools;35.0.0"

COPY verify-inner.sh /usr/local/bin/verify-inner.sh
RUN chmod +x /usr/local/bin/verify-inner.sh
WORKDIR /build
ENTRYPOINT ["/usr/local/bin/verify-inner.sh"]
```

`REPLACE_WITH_REAL_DIGEST` must not survive this step — paste the actual digest.

- [ ] **Step 3: Write `verify/verify-inner.sh`**

```bash
#!/usr/bin/env bash
# Runs INSIDE the verify container. Rebuilds <tag-or-ref> from source.
# Modes (at least one required):
#   - /apk/released.apk mounted -> compare released vs rebuilt (verify mode)
#   - /out directory mounted    -> copy rebuilt unsigned APK there (audit mode)
set -euo pipefail

REF="${1:?usage: verify-inner.sh <tag-or-ref>}"
RELEASED=/apk/released.apk
REPO_URL="${VELES_REPO_URL:-https://github.com/raidenyn/veles-android}"

if [ ! -f "$RELEASED" ] && [ ! -d /out ]; then
  echo "ERROR: mount an APK at $RELEASED (verify) and/or a directory at /out (audit)." >&2
  exit 2
fi

echo "==> Cloning $REPO_URL at $REF (full history so androidgitversion resolves)"
git clone --quiet "$REPO_URL" /build/src
cd /build/src
git checkout --quiet "$REF"

echo "==> Building unsigned release APK (no VELES_KEYSTORE_* in this environment)"
./gradlew --no-daemon assembleRelease

REBUILT=app/build/outputs/apk/release/app-release-unsigned.apk
if [ ! -f "$REBUILT" ]; then
  echo "ERROR: $REBUILT not found after build." >&2
  exit 2
fi

if [ -d /out ]; then
  cp "$REBUILT" /out/
  echo "==> Copied rebuilt APK to /out"
fi

if [ -f "$RELEASED" ]; then
  echo "==> SHA-256 of released and rebuilt APKs:"
  sha256sum "$RELEASED" "$REBUILT"
  if cmp -s "$RELEASED" "$REBUILT"; then
    echo "VERIFIED: released APK is byte-identical to the rebuild (unsigned release)."
  elif apksigcopier compare "$RELEASED" --unsigned "$REBUILT"; then
    echo "VERIFIED: released APK matches the rebuild after signature stripping."
  else
    echo "MISMATCH: released APK does NOT correspond to source at $REF." >&2
    exit 1
  fi
fi
```

- [ ] **Step 4: Make it executable and build the image**

```bash
chmod +x verify/verify-inner.sh
docker build -t veles-verify verify/
```

Expected: image builds successfully; `sdkmanager` output shows android-35 and build-tools;35.0.0 installed.

- [ ] **Step 5: Smoke-test audit mode against the current commit**

Everything must be committed first (`git status` clean, or at least all build-relevant files committed) — the container clones committed state only.

```bash
git status --short   # must show no changes to app/, gradle/, *.kts
mkdir -p /tmp/veles-audit
docker run --rm \
  -v "$PWD":/host-repo:ro -e VELES_REPO_URL=/host-repo \
  -v /tmp/veles-audit:/out \
  veles-verify "$(git rev-parse HEAD)"
ls -la /tmp/veles-audit/app-release-unsigned.apk
```

Expected: build succeeds inside the container; the APK appears in `/tmp/veles-audit/`.

- [ ] **Step 6: Commit**

```bash
git add verify/Dockerfile verify/verify-inner.sh
git commit -m "feat: Docker reference build environment for release verification (#37)"
```

---

### Task 3: Host entrypoint — `verify/verify.sh`

**Files:**
- Create: `verify/verify.sh`

**Interfaces:**
- Consumes: `verify/Dockerfile`, `verify/verify-inner.sh` (Task 2), `.java-version` (Task 1).
- Produces: `verify/verify.sh <path-to-released-apk> <tag>` — the command documented everywhere (docs, release notes, CI). Env override `VELES_REPO_URL`: an https URL, or a local directory (mounted into the container for pre-merge testing). Exit 0 = verified, 1 = mismatch, 2 = usage/pin error.

- [ ] **Step 1: Write `verify/verify.sh`**

```bash
#!/usr/bin/env bash
# Verify a released Veles APK against its source tag, using only Docker.
#
#   verify/verify.sh <path-to-released-apk> <tag>
#
# Rebuilds <tag> from source inside a pinned reference environment and
# compares the result with the released APK (signature-stripped via
# apksigcopier). Exit 0 = verified.
#
# Run this script from a checkout of the SAME tag you are verifying, so the
# reference environment pins match that release (see docs/reproducible-builds.md).
#
# VELES_REPO_URL overrides the source repo: an https URL, or a local
# directory (useful pre-merge; it is mounted read-only into the container).
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "usage: $0 <path-to-released-apk> <tag>" >&2
  exit 2
fi
APK="$(realpath "$1")"
TAG="$2"
if [ ! -f "$APK" ]; then
  echo "ERROR: no such file: $APK" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Pin-consistency check: Dockerfile JDK must match .java-version.
JAVA_VERSION="$(tr -d '[:space:]' < "$REPO_ROOT/.java-version")"
if ! grep -Eq "eclipse-temurin:${JAVA_VERSION}_" "$SCRIPT_DIR/Dockerfile"; then
  echo "ERROR: verify/Dockerfile base image does not match .java-version ($JAVA_VERSION)." >&2
  echo "       Update them together — see docs/reproducible-builds.md." >&2
  exit 2
fi

echo "==> Building reference environment image (this can take several minutes)"
docker build -t veles-verify "$SCRIPT_DIR"

DOCKER_ARGS=(--rm -v "$APK":/apk/released.apk:ro)
if [ -n "${VELES_REPO_URL:-}" ] && [ -d "$VELES_REPO_URL" ]; then
  DOCKER_ARGS+=(-v "$(realpath "$VELES_REPO_URL")":/host-repo:ro -e VELES_REPO_URL=/host-repo)
elif [ -n "${VELES_REPO_URL:-}" ]; then
  DOCKER_ARGS+=(-e VELES_REPO_URL)
fi

echo "==> Rebuilding $TAG and comparing against $APK"
docker run "${DOCKER_ARGS[@]}" veles-verify "$TAG"
```

```bash
chmod +x verify/verify.sh
```

- [ ] **Step 2: Negative test — pin-consistency check fires**

```bash
sed -i 's/^17/99/' .java-version
verify/verify.sh /tmp/veles-audit/app-release-unsigned.apk HEAD; echo "exit=$?"
git checkout .java-version
```

Expected: `ERROR: verify/Dockerfile base image does not match .java-version` and `exit=2`.

- [ ] **Step 3: Positive test — verify a container-built APK against the same commit**

Uses the audit-mode APK from Task 2 Step 5 as the "released" APK; the rebuild of the same commit in the same reference environment must match byte-for-byte:

```bash
VELES_REPO_URL="$PWD" verify/verify.sh /tmp/veles-audit/app-release-unsigned.apk "$(git rev-parse HEAD)"
echo "exit=$?"
```

Expected: `VERIFIED: released APK is byte-identical to the rebuild (unsigned release).` and `exit=0`. **If this fails, STOP — that is a determinism bug; jump to Task 4's diffoscope contingency before proceeding.**

- [ ] **Step 4: Commit**

```bash
git add verify/verify.sh
git commit -m "feat: one-command Docker verification script for released APKs (#37)"
```

---

### Task 4: Determinism audit — two independent clean builds must be identical

**Files:** none created (audit only; contingent fix modifies `app/build.gradle.kts`)

**Interfaces:**
- Consumes: the `veles-verify` image and audit mode from Task 2.
- Produces: evidence (two identical SHA-256 digests) that the build is deterministic; a `buildToolsVersion` pin only if the audit fails.

- [ ] **Step 1: Run two fully independent container builds of the same commit**

Fresh output dirs, two separate `docker run` invocations (each clones fresh and builds from scratch):

```bash
rm -rf /tmp/repro-a /tmp/repro-b && mkdir -p /tmp/repro-a /tmp/repro-b
REF="$(git rev-parse HEAD)"
docker run --rm -v "$PWD":/host-repo:ro -e VELES_REPO_URL=/host-repo -v /tmp/repro-a:/out veles-verify "$REF"
docker run --rm -v "$PWD":/host-repo:ro -e VELES_REPO_URL=/host-repo -v /tmp/repro-b:/out veles-verify "$REF"
```

- [ ] **Step 2: Compare**

```bash
sha256sum /tmp/repro-a/app-release-unsigned.apk /tmp/repro-b/app-release-unsigned.apk
cmp /tmp/repro-a/app-release-unsigned.apk /tmp/repro-b/app-release-unsigned.apk && echo "DETERMINISTIC"
```

Expected: identical digests, `DETERMINISTIC`.

- [ ] **Step 3 (contingency — only if Step 2 shows a mismatch): diagnose with diffoscope**

```bash
docker run --rm -v /tmp/repro-a:/a:ro -v /tmp/repro-b:/b:ro \
  registry.salsa.debian.org/reproducible-builds/diffoscope \
  /a/app-release-unsigned.apk /b/app-release-unsigned.apk | head -100
```

Known suspects and fixes, in order of likelihood:
- **Build-tools drift** (different `aapt2`/`zipalign` between environments — cannot differ between two runs of the same image, but pin anyway if diffoscope implicates resource packing): add to the `android {}` block in `app/build.gradle.kts`:

  ```kotlin
      buildToolsVersion = "35.0.0"
  ```
- **KSP/Room/Hilt generated-source ordering** (differing dex content across runs): check diffoscope for differing `classes*.dex`; if Room's `schemaLocation` or KSP output ordering is implicated, rerun the audit to confirm nondeterminism (vs. one-off), and record findings in the doc (Task 6).
- **Baseline profiles** (`assets/dexopt/baseline.prof*` differ): derived from dex — fix the dex nondeterminism first; the profiles follow.

After any fix: commit it, rebuild the image if `verify/` changed, and repeat Steps 1–2 until `DETERMINISTIC`.

- [ ] **Step 4: Record the audit result**

No commit if Step 2 passed clean and nothing changed. If a fix was applied:

```bash
git add app/build.gradle.kts
git commit -m "build: pin buildToolsVersion for deterministic release output (#37)"
```

---

### Task 5: Release workflow — draft → verify → publish, with attestation and SHA256SUMS

**Files:**
- Modify: `.github/workflows/release.yml` (full rewrite of the `jobs:` section and `permissions:`)

**Interfaces:**
- Consumes: `verify/verify.sh` contract from Task 3 (CI runs it literally); `.java-version` from Task 1.
- Produces: workflow artifact `release-apk` containing `veles-<tag>.apk`; a draft GitHub Release with `veles-<tag>.apk`, `mapping.txt`, `SHA256SUMS`; attestations for the APK and `mapping.txt`; publication only after the verify job passes.

- [ ] **Step 1: Rewrite `.github/workflows/release.yml`**

Replace the entire file with (the `Decode keystore` and `Verify built version matches tag` steps are unchanged from the current file):

```yaml
name: Release

on:
  push:
    tags:
      - "[0-9]+\\.[0-9]+\\.[0-9]+"

permissions:
  contents: write
  id-token: write
  attestations: write

jobs:
  build:
    name: build
    runs-on: ubuntu-latest
    env:
      HAS_KEYSTORE: ${{ secrets.KEYSTORE_BASE64 != '' }}
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: actions/setup-java@v5
        with:
          distribution: temurin
          java-version-file: .java-version
      - uses: gradle/actions/setup-gradle@v6
      - name: Decode keystore
        if: env.HAS_KEYSTORE == 'true'
        run: |
          if [ -z "${{ secrets.KEYSTORE_PASSWORD }}" ] || [ -z "${{ secrets.KEY_ALIAS }}" ] || [ -z "${{ secrets.KEY_PASSWORD }}" ]; then
            echo "::error::KEYSTORE_BASE64 is set but one of KEYSTORE_PASSWORD, KEY_ALIAS, KEY_PASSWORD is missing."
            exit 1
          fi
          echo "${{ secrets.KEYSTORE_BASE64 }}" | base64 -d > "$RUNNER_TEMP/keystore.jks"
          {
            echo "VELES_KEYSTORE_FILE=$RUNNER_TEMP/keystore.jks"
            echo "VELES_KEYSTORE_PASSWORD=${{ secrets.KEYSTORE_PASSWORD }}"
            echo "VELES_KEY_ALIAS=${{ secrets.KEY_ALIAS }}"
            echo "VELES_KEY_PASSWORD=${{ secrets.KEY_PASSWORD }}"
          } >> "$GITHUB_ENV"
      - run: ./gradlew assembleRelease
      - name: Verify built version matches tag
        run: |
          BUILT_NAME=$(./gradlew -q :app:androidGitVersion | awk -F'\t' '/androidGitVersion.name/ {print $2}')
          if [ "$BUILT_NAME" != "$GITHUB_REF_NAME" ]; then
            echo "::error::Built versionName '$BUILT_NAME' does not match tag '$GITHUB_REF_NAME'. The androidgitversion plugin may not see the tag (shallow checkout?)."
            exit 1
          fi
          echo "Version match OK: $BUILT_NAME"
      - name: Collect artifacts and checksums
        id: apk
        run: |
          if [ "$HAS_KEYSTORE" = "true" ]; then
            cp app/build/outputs/apk/release/app-release.apk "veles-$GITHUB_REF_NAME.apk"
            echo "suffix=" >> "$GITHUB_OUTPUT"
          else
            cp app/build/outputs/apk/release/app-release-unsigned.apk "veles-$GITHUB_REF_NAME.apk"
            echo "suffix= (unsigned)" >> "$GITHUB_OUTPUT"
          fi
          cp app/build/outputs/mapping/release/mapping.txt mapping.txt
          sha256sum "veles-$GITHUB_REF_NAME.apk" mapping.txt > SHA256SUMS
          cat SHA256SUMS
      - name: Attest build provenance
        uses: actions/attest-build-provenance@v3
        with:
          subject-path: |
            veles-${{ github.ref_name }}.apk
            mapping.txt
      - name: Upload APK for the verify job
        uses: actions/upload-artifact@v7
        with:
          name: release-apk
          path: veles-${{ github.ref_name }}.apk
          if-no-files-found: error
      - name: Create draft GitHub Release
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh release create "$GITHUB_REF_NAME" \
            "veles-$GITHUB_REF_NAME.apk" \
            mapping.txt \
            SHA256SUMS \
            --title "Veles $GITHUB_REF_NAME${{ steps.apk.outputs.suffix }}" \
            --draft \
            --generate-notes
      - name: Append verification instructions to release notes
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          {
            gh release view "$GITHUB_REF_NAME" --json body -q .body
            echo
            echo "## Verify this download"
            echo
            echo "One-liner (trusts GitHub):"
            echo '```'
            echo "gh attestation verify veles-$GITHUB_REF_NAME.apk --repo $GITHUB_REPOSITORY"
            echo '```'
            echo "Zero-trust rebuild (needs only Docker; run from a checkout of tag $GITHUB_REF_NAME):"
            echo '```'
            echo "verify/verify.sh veles-$GITHUB_REF_NAME.apk $GITHUB_REF_NAME"
            echo '```'
            echo "Details: docs/reproducible-builds.md in this repo."
          } > notes.md
          gh release edit "$GITHUB_REF_NAME" --notes-file notes.md

  verify:
    name: verify reproducibility
    needs: build
    runs-on: ubuntu-latest
    steps:
      # Default checkout of the tag ref — brings the TAG'S OWN verify/ toolkit and pins.
      - uses: actions/checkout@v7
      - uses: actions/download-artifact@v7
        with:
          name: release-apk
      - name: Rebuild from source and compare
        run: |
          VELES_REPO_URL="https://github.com/$GITHUB_REPOSITORY" \
            verify/verify.sh "veles-$GITHUB_REF_NAME.apk" "$GITHUB_REF_NAME"

  publish:
    name: publish
    needs: verify
    runs-on: ubuntu-latest
    steps:
      - name: Publish the verified draft release
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh release edit "$GITHUB_REF_NAME" --repo "$GITHUB_REPOSITORY" --draft=false
```

- [ ] **Step 2: Lint the workflow**

```bash
command -v actionlint >/dev/null && actionlint .github/workflows/release.yml || \
  python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml')); print('YAML OK')"
```

Expected: no errors / `YAML OK`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: gate releases on independent reproducibility verification, attest artifacts (#37)"
```

---

### Task 6: Documentation — `docs/reproducible-builds.md` + README section

**Files:**
- Create: `docs/reproducible-builds.md`
- Modify: `README.md` (add a subsection at the end of the `## Privacy` section, before `## Requirements`)

**Interfaces:**
- Consumes: exact commands and file names from Tasks 1–5 (must match verbatim: `verify/verify.sh <apk> <tag>`, `.java-version`, `gh attestation verify ... --repo raidenyn/veles-android`).

- [ ] **Step 1: Write `docs/reproducible-builds.md`**

```markdown
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

## Path 2 — zero-trust rebuild (minutes, needs only Docker)

```bash
git clone https://github.com/raidenyn/veles-android
cd veles-android
git checkout X.Y.Z         # IMPORTANT: verify from the tag you are verifying
verify/verify.sh /path/to/veles-X.Y.Z.apk X.Y.Z
```

The script builds a pinned reference environment (exact JDK, Android SDK,
build-tools), clones and rebuilds the tag inside it, and compares the result to
your APK with [apksigcopier](https://github.com/obfusk/apksigcopier) — the same
signature-stripping comparison F-Droid uses. Exit code 0 means verified; it
prints both SHA-256 digests.

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
```

- [ ] **Step 2: Add the README section**

In `README.md`, at the end of the `## Privacy` section (immediately before the `## Requirements` heading), add:

```markdown
### Verify your download

Every release can be verified against its source — either in one command
(trusting GitHub):

```
gh attestation verify veles-X.Y.Z.apk --repo raidenyn/veles-android
```

or by rebuilding it bit-for-bit yourself with nothing but Docker. CI refuses to
publish a release whose APK it cannot independently reproduce. See
[docs/reproducible-builds.md](docs/reproducible-builds.md).
```

(Note: the inner code fence inside README needs to not terminate the outer one — in the actual README there is no outer fence; write it as a normal fenced block.)

- [ ] **Step 3: Check cross-references**

```bash
grep -n "verify/verify.sh" docs/reproducible-builds.md README.md .github/workflows/release.yml
grep -n "reproducible-builds.md" README.md .github/workflows/release.yml
```

Expected: hits in all listed files; command spelling identical everywhere.

- [ ] **Step 4: Commit**

```bash
git add docs/reproducible-builds.md README.md
git commit -m "docs: reproducible-builds verification guide and README section (#37)"
```

---

### Task 7: End-to-end release validation (after merge to master)

**Files:** none — this validates the released pipeline. Runs only after the PR is merged.

**Interfaces:**
- Consumes: everything above, merged to `master`.

- [ ] **Step 1: Tag and push the next patch release**

The current release is `0.0.1`, so:

```bash
git checkout master && git pull
git tag 0.0.2
git push origin 0.0.2
```

- [ ] **Step 2: Watch the workflow — expect build → verify → publish, in order**

```bash
gh run watch --repo raidenyn/veles-android $(gh run list --repo raidenyn/veles-android --workflow=release.yml --limit 1 --json databaseId -q '.[0].databaseId')
```

Expected: all three jobs green. Confirm the release was a draft until `verify` passed and is now published:

```bash
gh release view 0.0.2 --repo raidenyn/veles-android --json isDraft,assets -q '{draft: .isDraft, assets: [.assets[].name]}'
```

Expected: `draft: false`; assets include `veles-0.0.2.apk`, `mapping.txt`, `SHA256SUMS`.

- [ ] **Step 3: Verify via attestation**

```bash
cd "$(mktemp -d)"
gh release download 0.0.2 --repo raidenyn/veles-android --pattern 'veles-0.0.2.apk'
gh attestation verify veles-0.0.2.apk --repo raidenyn/veles-android
```

Expected: `Verification succeeded!` naming `.github/workflows/release.yml` and the tagged commit.

- [ ] **Step 4: Verify via Docker rebuild (from the tag's checkout)**

```bash
git clone https://github.com/raidenyn/veles-android /tmp/veles-verify-test
cd /tmp/veles-verify-test && git checkout 0.0.2
verify/verify.sh "$OLDPWD/veles-0.0.2.apk" 0.0.2
echo "exit=$?"
```

Expected: `VERIFIED` and `exit=0`.

- [ ] **Step 5: Tamper check — both paths must fail**

```bash
cd "$OLDPWD"
cp veles-0.0.2.apk tampered.apk
printf '\xff' | dd of=tampered.apk bs=1 seek=1000 count=1 conv=notrunc
gh attestation verify tampered.apk --repo raidenyn/veles-android; echo "attestation exit=$?"
cd /tmp/veles-verify-test
verify/verify.sh "$OLDPWD/tampered.apk" 0.0.2; echo "verify exit=$?"
```

Expected: attestation fails (non-zero exit, digest mismatch); `verify/verify.sh` prints `MISMATCH` with `verify exit=1`.

- [ ] **Step 6: Close the loop**

Tick the acceptance criteria in `docs/superpowers/specs/2026-07-10-reproducible-builds-design.md`, commit, and close issue #37 with a comment linking the verified release:

```bash
gh issue close 37 --repo raidenyn/veles-android --comment "Shipped and validated end-to-end on release 0.0.2: draft→verify→publish gating, attestation verify, Docker rebuild verify, and tamper checks all pass. See docs/reproducible-builds.md."
```
