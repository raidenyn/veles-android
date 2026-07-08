# Release Build Hardening — Design

**Issue:** [#16](https://github.com/raidenyn/veles-android/issues/16)
**Date:** 2026-07-08
**Status:** Approved

## Problem

The release build type is effectively a debug build with a different name:

- `isMinifyEnabled = false` — no R8 shrinking or obfuscation; the APK ships all unused
  Compose/Glance/Room code and full symbol names.
- `versionCode = 1` / `versionName = "1.0"` are hard-coded, so sideloaded updates and future
  Play uploads will collide.
- CI never builds a release artifact, so release-only breakage (missing keep rules, resource
  shrinking issues) is discovered manually.
- No `applicationIdSuffix` for debug, so debug and release installs can't coexist on one
  device.

## Decisions Made

| Decision | Choice |
|---|---|
| Signing scope | Tag-triggered signed workflow written now; skips signing gracefully until keystore secrets are added |
| Versioning strategy | Gradle plugin (`com.gladed.androidgitversion`), versions derived from git tags |
| Initial version | `0.0.1` (initial tag to be created and pushed as part of this work) |

## 1. Build Types (`app/build.gradle.kts`)

- **release**: `isMinifyEnabled = true`, `isShrinkResources = true`, keeping the existing
  `proguard-android-optimize.txt` + `proguard-rules.pro` pair.
- **debug** (new explicit block): `applicationIdSuffix = ".debug"` so debug and release
  installs coexist.
- Debug launcher distinctness via resource overlays in `app/src/debug/res/` (no code):
  - `values/strings.xml` overrides `app_name` to `Veles (debug)`.
  - `drawable/ic_launcher_background.xml` with a different background color. The adaptive
    icon (`mipmap-anydpi/ic_launcher.xml`) already references
    `@drawable/ic_launcher_background`, so the overlay recolors the debug icon without
    touching the foreground art.
- Cleanup while in the file: remove the bogus line-1 import
  (`SamConstructorDescriptorKindExclude.excludes`), an IDE auto-import accident.

## 2. R8 Keep Rules (`app/proguard-rules.pro`)

Room, Compose, Hilt, and Glance ship consumer proguard rules. `NotificationListener` and
`CopyDataReceiver` are manifest-referenced, so AGP keeps them automatically.

Additions:

- `-keepattributes SourceFile,LineNumberTable` and
  `-renamesourcefileattribute SourceFile` so release crash traces stay readable.
- kotlinx.serialization rules **only if verification shows they're needed** — since 1.5.x
  the runtime ships consumer rules covering `@Serializable` classes. The config
  export/import feature is the code path to exercise to confirm. No speculative rules.

## 3. Versioning

Use the **`com.gladed.androidgitversion`** Gradle plugin (v0.4.14):

- `versionName = androidGitVersion.name()` — from git tags; untagged commits get
  `0.0.1-3-gabc123`-style names.
- `versionCode = androidGitVersion.code()` — default scheme maps `0.0.1` → `1`, matching
  the currently shipped versionCode so sideloaded updates increment cleanly.
- Tag scheme: plain semver, no `v` prefix (plugin default), e.g. `0.0.1`.
- Create and push the initial `0.0.1` tag as part of this work.
- CI checkouts need `fetch-depth: 0` so tags and history are visible to the plugin.

**Fallback:** the plugin's last release was ~2020. If it misbehaves with Gradle 8 or the
configuration cache, fall back to `me.qoomon.git-versioning` (actively maintained) plus a
small hand-written versionCode mapping.

## 4. CI

### `ci.yml` — new `release-build` job

- Runs on every PR/push alongside the existing jobs, so R8 breakage fails PRs.
- `./gradlew assembleRelease` (unsigned), upload the APK as a workflow artifact.
- Checkout with `fetch-depth: 0` (also added to jobs whose builds need version info).

### New `release.yml` — tag-triggered release

- Trigger: tag push matching `[0-9]*` (plain semver tags).
- Builds `assembleRelease`. A signing config in Gradle reads the keystore path and
  passwords from environment variables.
- The workflow decodes a `KEYSTORE_BASE64` secret to a keystore file; other secrets:
  `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`.
- **Graceful skip:** the workflow checks secret presence first. If absent, it builds
  unsigned, still creates the GitHub Release with the APK attached (release notes marked
  as unsigned), and succeeds. Once secrets are added, the same workflow signs with no
  changes.
- Release creation via `gh release create` with the APK attached.

## 5. Testing & Verification

- Automated gate: CI green on the new `release-build` job.
- Manual smoke test (the scenario R8 silently breaks): install the minified APK on a
  device, grant notification access, run the built-in Test Screen end-to-end, and exercise
  bank config export/import (the kotlinx.serialization path).
- Local checks before pushing: `./gradlew assembleRelease` succeeds; versioning plugin
  resolves code/name correctly both with and without a reachable tag.

## Out of Scope

- Play Store publishing.
- Generating the keystore / adding GitHub secrets (user action; the workflow tolerates
  their absence).
- Running instrumented tests against the minified build.
