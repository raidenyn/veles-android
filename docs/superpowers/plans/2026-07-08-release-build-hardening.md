# Release Build Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the release build a real release build — R8-minified, git-tag-versioned, built (and optionally signed) by CI — and let debug and release installs coexist on one device.

**Architecture:** All build-type and versioning changes live in `app/build.gradle.kts` plus debug resource overlays in `app/src/debug/res/`. Versioning is derived from git tags by the `com.gladed.androidgitversion` plugin. CI gets a `release-build` job in `ci.yml` (unsigned, every PR) and a new tag-triggered `release.yml` that signs when keystore secrets exist and gracefully ships unsigned otherwise.

**Tech Stack:** AGP 8.9.0, Kotlin 2.1.10, Gradle Kotlin DSL, `com.gladed.androidgitversion` 0.4.14, GitHub Actions, `gh` CLI.

**Spec:** `docs/superpowers/specs/2026-07-08-release-build-hardening-design.md`

## Global Constraints

- JDK 17, AGP 8.9.0, Kotlin 2.1.10, minSdk 33, targetSdk/compileSdk 35 — do not change.
- Version tags are plain semver with **no `v` prefix**: `0.0.1`. Initial released version is `0.0.1`.
- Debug applicationId is `me.nagaev.veles.debug`; debug launcher label is exactly `Veles (debug)`.
- Signing env var names (Gradle side): `VELES_KEYSTORE_FILE`, `VELES_KEYSTORE_PASSWORD`, `VELES_KEY_ALIAS`, `VELES_KEY_PASSWORD`. GitHub secret names: `KEYSTORE_BASE64`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`.
- `./gradlew spotlessCheck detekt lintDebug testDebugUnitTest` must stay green after every task (spotless targets only `.kt`, not `.kts`, but run it anyway).
- No raw `android.util.Log` usage (CI greps for it); this plan touches no Kotlin source, so this should never come up.
- Work on a feature branch (e.g. `feature/16-release-hardening`), not `master`.

---

### Task 1: Debug build distinctness

Make debug installs coexist with release installs and look distinct in the launcher.

**Files:**
- Modify: `app/build.gradle.kts` (lines 1, 32–40)
- Create: `app/src/debug/res/values/strings.xml`
- Create: `app/src/debug/res/drawable/ic_launcher_background.xml`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: a `debug { }` block inside `android.buildTypes` in `app/build.gradle.kts` — Task 2 edits the sibling `release { }` block; both must coexist.

- [ ] **Step 1: Create the feature branch**

```bash
git checkout -b feature/16-release-hardening master
```

- [ ] **Step 2: Remove the bogus import and add the debug build type**

In `app/build.gradle.kts`, delete line 1 (an IDE auto-import accident):

```kotlin
import org.jetbrains.kotlin.gradle.internal.resolve.sam.SamConstructorDescriptorKindExclude.excludes
```

Then replace the existing `buildTypes` block:

```kotlin
    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
```

with:

```kotlin
    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
        }
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
```

(`isMinifyEnabled` flips to `true` in Task 2, not here — one behavior change per task.)

- [ ] **Step 3: Create the debug label overlay**

Create `app/src/debug/res/values/strings.xml`:

```xml
<resources>
    <string name="app_name">Veles (debug)</string>
</resources>
```

- [ ] **Step 4: Create the debug icon-background overlay**

The adaptive icon (`app/src/main/res/mipmap-anydpi/ic_launcher.xml`) draws its background from `@drawable/ic_launcher_background` (teal `#03A3AA` in main). Overriding that drawable in the debug source set recolors the debug icon without touching the foreground art.

Create `app/src/debug/res/drawable/ic_launcher_background.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<vector
    android:height="108dp"
    android:width="108dp"
    android:viewportHeight="108"
    android:viewportWidth="108"
    xmlns:android="http://schemas.android.com/apk/res/android">
    <path android:fillColor="#E65100"
          android:pathData="M0,0h108v108h-108z"/>
</vector>
```

- [ ] **Step 5: Build and verify the debug applicationId and label**

```bash
./gradlew :app:assembleDebug
grep -o 'package="[^"]*"' app/build/intermediates/packaged_manifests/debug/AndroidManifest.xml | head -1
grep 'app_name' app/build/intermediates/packaged_res/debug/packageDebugResources/values/values.xml
```

Expected: build `BUILD SUCCESSFUL`; package line prints `package="me.nagaev.veles.debug"`; the values grep shows `Veles (debug)`. (Intermediate paths can shift between AGP versions — if a path doesn't exist, find the merged manifest with `find app/build/intermediates -name AndroidManifest.xml -path '*debug*'` and the merged values with `find app/build/intermediates -name 'values*.xml' -path '*debug*'`.)

- [ ] **Step 6: Verify existing tests still pass**

```bash
./gradlew testDebugUnitTest lintDebug
```

Expected: `BUILD SUCCESSFUL`. (Instrumented tests use `testApplicationId` = `me.nagaev.veles.debug.test` automatically; nothing to change.)

- [ ] **Step 7: Commit**

```bash
git add app/build.gradle.kts app/src/debug/res/
git commit -m "feat: distinct debug build (.debug suffix, label, icon color) (#16)"
```

---

### Task 2: Enable R8 minification and resource shrinking

**Files:**
- Modify: `app/build.gradle.kts` (the `release { }` block from Task 1)
- Modify: `app/proguard-rules.pro`

**Interfaces:**
- Consumes: the `buildTypes { debug { } release { } }` structure from Task 1.
- Produces: a working `./gradlew assembleRelease` that outputs `app/build/outputs/apk/release/app-release-unsigned.apk` — Tasks 4 and 5 build and upload exactly that path.

- [ ] **Step 1: Flip minification on**

In `app/build.gradle.kts`, change the release block to:

```kotlin
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
```

- [ ] **Step 2: Add readable-stack-trace keep rules**

Replace the entire commented-boilerplate content of `app/proguard-rules.pro` with:

```
# Keep file/line info so release crash traces are mappable via mapping.txt.
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile
```

Do **not** add kotlinx.serialization, Room, Compose, Hilt, or Glance rules speculatively — they ship consumer rules. `NotificationListener` and `CopyDataReceiver` are manifest-referenced and kept automatically by AGP.

- [ ] **Step 3: Build release and verify R8 ran**

```bash
./gradlew :app:assembleRelease
ls -la app/build/outputs/apk/release/app-release-unsigned.apk
ls app/build/outputs/mapping/release/mapping.txt
```

Expected: `BUILD SUCCESSFUL`; both files exist. The APK should be noticeably smaller than a debug build (Compose apps typically shrink by several MB).

**If R8 fails with "Missing classes" errors:** it writes suggested rules to `app/build/outputs/mapping/release/missing_rules.txt`. Inspect each entry — for classes referenced only from unused optional code paths, copy the suggested `-dontwarn` lines into `app/proguard-rules.pro` with a comment naming the library that references them. Do not add blanket `-keep` rules.

- [ ] **Step 4: Verify debug is unaffected**

```bash
./gradlew testDebugUnitTest
```

Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 5: Commit**

```bash
git add app/build.gradle.kts app/proguard-rules.pro
git commit -m "feat: enable R8 minification and resource shrinking for release (#16)"
```

---

### Task 3: Git-tag-derived versioning

**Files:**
- Modify: `gradle/libs.versions.toml` (`[plugins]` section)
- Modify: `app/build.gradle.kts` (plugins block, `defaultConfig`, new `androidGitVersion` block)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `versionCode`/`versionName` computed from git tags at configuration time. Tasks 4 and 5 must check out with `fetch-depth: 0` so the plugin can see tags; on a tagless/shallow checkout the plugin yields name `"unknown"` and code `0` but the build still succeeds.

- [ ] **Step 1: Add the plugin to the version catalog**

In `gradle/libs.versions.toml`, add to the `[plugins]` section (alphabetical, after `android-application`):

```toml
androidgitversion = { id = "com.gladed.androidgitversion", version = "0.4.14" }
```

- [ ] **Step 2: Apply and configure the plugin**

In `app/build.gradle.kts`, add to the `plugins` block (after `alias(libs.plugins.android.application)`):

```kotlin
    alias(libs.plugins.androidgitversion)
```

Add this block immediately after the `plugins { }` block (before `detekt { }`):

```kotlin
androidGitVersion {
    codeFormat = "MMNNPP"
}
```

Then in `defaultConfig`, replace:

```kotlin
        versionCode = 1
        versionName = "1.0"
```

with:

```kotlin
        versionCode = androidGitVersion.code()
        versionName = androidGitVersion.name()
```

With `codeFormat = "MMNNPP"`, tag `0.0.1` → versionCode `1`, which matches the currently shipped hard-coded `versionCode = 1`, so sideloaded updates increment cleanly from existing installs.

- [ ] **Step 3: Verify with a temporary local tag**

The real `0.0.1` tag is created on `master` after merge (Task 6) — a tag on this feature branch would become unreachable after a squash-merge. Verify with a throwaway tag:

```bash
git tag 0.0.1
./gradlew -q :app:androidGitVersion
```

Expected output (exact numbers vary):

```
androidGitVersion.name	0.0.1
androidGitVersion.code	1
```

Then remove the throwaway tag and verify the tagless fallback doesn't break the build:

```bash
git tag -d 0.0.1
./gradlew :app:assembleDebug
```

Expected: `BUILD SUCCESSFUL` (versionName is `unknown`, code `0` — acceptable for untagged CI builds of PRs).

**Fallback (only if the plugin fails to apply or crashes under Gradle 8 / config cache):** the spec's designated fallback is `me.qoomon.git-versioning` plus a hand-written versionCode mapping — stop and flag this rather than improvising, since it changes the spec's versioning section.

- [ ] **Step 4: Run the full local gate**

```bash
./gradlew spotlessCheck detekt lintDebug testDebugUnitTest
```

Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 5: Commit**

```bash
git add gradle/libs.versions.toml app/build.gradle.kts
git commit -m "feat: derive versionCode/versionName from git tags (#16)"
```

---

### Task 4: CI release-build workflow

**Files:**
- Create: `.github/workflows/release-build.yml`

**Interfaces:**
- Consumes: `./gradlew assembleRelease` producing `app/build/outputs/apk/release/app-release-unsigned.apk` (Task 2); `fetch-depth: 0` requirement (Task 3).
- Produces: a release-build workflow that catches R8 breakage on master pushes, runs as an opt-in on PRs (label `release-build`), and supports manual dispatch from master; a `release-apk` workflow artifact.

- [ ] **Step 1: Create the release-build workflow**

Create `.github/workflows/release-build.yml`:

```yaml
name: release-build

on:
  workflow_dispatch:
  pull_request:
    types: [labeled, synchronize]
  push:
    branches: [master]

concurrency:
  group: release-build-${{ github.ref }}
  cancel-in-progress: true

jobs:
  release-build:
    name: release-build
    if: |
      github.event_name == 'workflow_dispatch' ||
      github.event_name == 'push' ||
      (github.event_name == 'pull_request' && github.event.action == 'labeled' && github.event.label.name == 'release-build') ||
      (github.event_name == 'pull_request' && github.event.action == 'synchronize' && contains(github.event.pull_request.labels.*.name, 'release-build'))
    runs-on: ubuntu-latest
    env:
      HAS_KEYSTORE: ${{ github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/master' && secrets.KEYSTORE_BASE64 != '' }}
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: actions/setup-java@v5
        with:
          distribution: temurin
          java-version: 17
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
      - name: Rename APK
        run: |
          APK=$(ls app/build/outputs/apk/release/app-release*.apk 2>/dev/null | head -1)
          if [ "${{ github.event_name }}" = "pull_request" ]; then
            SHORT_SHA=$(echo "${{ github.event.pull_request.head.sha }}" | cut -c1-7)
            NAME="Veles-PR${{ github.event.pull_request.number }}-${SHORT_SHA}.apk"
          else
            VERSION=$(./gradlew -q :app:androidGitVersion | awk -F'\t' '/androidGitVersion.name/ {print $2}')
            NAME="Veles-${VERSION}-release.apk"
          fi
          mv "$APK" "app/build/outputs/apk/release/$NAME"
          echo "Renamed to $NAME"
      - uses: actions/upload-artifact@v7
        with:
          name: release-apk
          path: |
            app/build/outputs/apk/release/Veles-*.apk
            app/build/outputs/mapping/release/mapping.txt
          if-no-files-found: error
```

Design notes:
- `push: branches: [master]` ensures R8 breakage is caught on every merge to master.
- PR-label opt-in: add the `release-build` label to run the release build on a PR; `synchronize` re-runs on new pushes while the label is present.
- PR builds are always unsigned (secrets not available on `pull_request` events); signing is gated on `workflow_dispatch` + `github.ref == 'refs/heads/master'`.
- APK naming: `Veles-PR{N}-{head-sha}.apk` on PRs (uses the PR head SHA, not the ephemeral merge commit); `Veles-{version}-release.apk` on master.

- [ ] **Step 2: Validate the YAML**

```bash
python3 -c "import yaml, sys; yaml.safe_load(open('.github/workflows/release-build.yml')); print('OK')"
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release-build.yml
git commit -m "ci: opt-in release-build workflow with master push trigger (#16)"
```

Real verification happens when the PR opens (Task 6): the `release-build` job runs on master push after merge.

---

### Task 5: Signing config and tag-triggered release workflow

**Files:**
- Modify: `app/build.gradle.kts` (signing config)
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: Gradle env var names from Global Constraints; `app-release.apk` (signed) / `app-release-unsigned.apk` (unsigned) output paths from Task 2.
- Produces: a GitHub Release with an attached APK for every pushed semver tag; signing activates automatically once the four GitHub secrets exist.

- [ ] **Step 1: Add the env-driven signing config**

In `app/build.gradle.kts`, inside the `android { }` block, add **before** `buildTypes`:

```kotlin
    val releaseKeystore = System.getenv("VELES_KEYSTORE_FILE")
    signingConfigs {
        if (releaseKeystore != null) {
            create("release") {
                storeFile = file(releaseKeystore)
                storePassword = System.getenv("VELES_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("VELES_KEY_ALIAS")
                keyPassword = System.getenv("VELES_KEY_PASSWORD")
            }
        }
    }
```

and add one line to the `release` build type:

```kotlin
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            signingConfig = signingConfigs.findByName("release")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
```

`findByName` returns `null` when the env var is absent, which leaves the release build unsigned — exactly today's behavior.

- [ ] **Step 2: Verify both signing modes locally**

```bash
# Unsigned path (no env vars):
./gradlew :app:assembleRelease
ls app/build/outputs/apk/release/app-release-unsigned.apk

# Signed path, with a throwaway keystore:
keytool -genkeypair -keystore /tmp/test-keystore.jks -alias test -storepass testpass -keypass testpass \
  -keyalg RSA -keysize 2048 -validity 1 -dname "CN=test"
VELES_KEYSTORE_FILE=/tmp/test-keystore.jks VELES_KEYSTORE_PASSWORD=testpass \
  VELES_KEY_ALIAS=test VELES_KEY_PASSWORD=testpass ./gradlew :app:assembleRelease
ls app/build/outputs/apk/release/app-release.apk
rm /tmp/test-keystore.jks
```

Expected: both builds `BUILD SUCCESSFUL`; the unsigned run produces `app-release-unsigned.apk`, the signed run produces `app-release.apk`.

- [ ] **Step 3: Create the release workflow**

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags:
      - "[0-9]+.[0-9]+.[0-9]+"

permissions:
  contents: write

jobs:
  release:
    name: release
    runs-on: ubuntu-latest
    env:
      HAS_KEYSTORE: ${{ secrets.KEYSTORE_BASE64 != '' }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: 17
      - uses: gradle/actions/setup-gradle@v4
      - name: Decode keystore
        if: env.HAS_KEYSTORE == 'true'
        run: |
          echo "${{ secrets.KEYSTORE_BASE64 }}" | base64 -d > "$RUNNER_TEMP/keystore.jks"
          {
            echo "VELES_KEYSTORE_FILE=$RUNNER_TEMP/keystore.jks"
            echo "VELES_KEYSTORE_PASSWORD=${{ secrets.KEYSTORE_PASSWORD }}"
            echo "VELES_KEY_ALIAS=${{ secrets.KEY_ALIAS }}"
            echo "VELES_KEY_PASSWORD=${{ secrets.KEY_PASSWORD }}"
          } >> "$GITHUB_ENV"
      - run: ./gradlew assembleRelease
      - name: Collect APK
        id: apk
        run: |
          if [ "$HAS_KEYSTORE" = "true" ]; then
            cp app/build/outputs/apk/release/app-release.apk "veles-$GITHUB_REF_NAME.apk"
            echo "suffix=" >> "$GITHUB_OUTPUT"
          else
            cp app/build/outputs/apk/release/app-release-unsigned.apk "veles-$GITHUB_REF_NAME.apk"
            echo "suffix= (unsigned)" >> "$GITHUB_OUTPUT"
          fi
      - name: Create GitHub Release
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh release create "$GITHUB_REF_NAME" "veles-$GITHUB_REF_NAME.apk" \
            --title "Veles $GITHUB_REF_NAME${{ steps.apk.outputs.suffix }}" \
            --generate-notes
```

Notes for the implementer: `secrets` cannot be read inside `if:` on some contexts, which is why presence is materialized into the job-level `HAS_KEYSTORE` env var. Until the user adds the four secrets, `HAS_KEYSTORE` is `false`, the decode step skips, and the release ships the unsigned APK with an "(unsigned)" title suffix — this is the designed behavior, not an error.

- [ ] **Step 4: Validate the YAML**

```bash
python3 -c "import yaml, sys; yaml.safe_load(open('.github/workflows/release.yml')); print('OK')"
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add app/build.gradle.kts .github/workflows/release.yml
git commit -m "ci: tag-triggered release workflow with optional signing (#16)"
```

---

### Task 6: Docs, PR, and post-merge release

**Files:**
- Modify: `CLAUDE.md` (Build & Test Commands section)

**Interfaces:**
- Consumes: everything above; the green CI run is the gate for the whole branch.
- Produces: merged PR, pushed `0.0.1` tag, first GitHub Release.

- [ ] **Step 1: Document the release build in CLAUDE.md**

In `CLAUDE.md`, in the `## Build & Test Commands` code block, add after the `assembleDebug` entry:

```bash
# Build release APK (R8-minified; signed only if VELES_KEYSTORE_* env vars are set)
./gradlew assembleRelease
```

And append a short section at the end of the file:

```markdown
## Versioning & Releases

`versionCode`/`versionName` are derived from git tags by the `com.gladed.androidgitversion`
plugin (`codeFormat = "MMNNPP"`; tag `0.0.1` → code 1). Tags are plain semver with no `v`
prefix. Pushing a `X.Y.Z` tag triggers `.github/workflows/release.yml`, which builds the
release APK and creates a GitHub Release — signed if the `KEYSTORE_BASE64`,
`KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD` secrets exist, unsigned otherwise.
```

- [ ] **Step 2: Commit and push the branch**

```bash
git add CLAUDE.md
git commit -m "docs: document release build and tag-based versioning (#16)"
git push -u origin feature/16-release-hardening
```

- [ ] **Step 3: Open the PR and verify CI**

```bash
gh pr create --title "feat: release build hardening (R8, versioning, release CI) (#16)" \
  --body "Implements #16 per docs/superpowers/specs/2026-07-08-release-build-hardening-design.md: R8 + resource shrinking, .debug variant coexistence, git-tag versioning, release-build CI job, tag-triggered release workflow with optional signing. Closes #16."
gh pr checks --watch
```

Expected: all jobs green, including the new `release-build`. If `release-build` fails on R8, follow the missing-rules contingency in Task 2 Step 3.

- [ ] **Step 4: After merge — tag master and verify the release workflow**

(Requires the user to merge the PR first; stop and hand off if not authorized to merge.)

```bash
git checkout master && git pull
git tag 0.0.1
git push origin 0.0.1
gh run watch --workflow=release.yml || gh run list --workflow=release.yml --limit 1
gh release view 0.0.1
```

Expected: the `Release` workflow succeeds and `gh release view 0.0.1` shows a release titled `Veles 0.0.1 (unsigned)` with `veles-0.0.1.apk` attached (unsigned until the user adds keystore secrets).

- [ ] **Step 5: Manual smoke test (user-assisted, needs a device)**

Install the minified APK, grant notification access, run the built-in Test Screen end-to-end, and exercise bank config export/import (the kotlinx.serialization path R8 could silently break). This is the spec's release-verification gate; report the result rather than skipping it silently.
