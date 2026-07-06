# Tech: Release build hardening (R8, versioning, release CI)

**Type:** Technical improvement (build/release engineering)
**Priority:** Medium
**Effort:** Small-medium (~1 day)

## Problem

The release build type is effectively a debug build with a different name:

- `isMinifyEnabled = false` — no R8 shrinking or obfuscation; the APK ships all unused Compose /
  Glance / Room code and full symbol names.
- `versionCode = 1 / versionName = "1.0"` hard-coded — no versioning strategy, so sideloaded
  updates and future Play uploads will collide.
- CI (#3) runs lint, detekt, and unit tests but never builds (or signs) a release artifact, so
  release-only breakage (missing keep rules, resource shrinking issues) is discovered manually,
  at the worst time.
- `debuggable` release variants aside, there is also no `applicationIdSuffix` for debug, so a
  debug and release install can't coexist on one device — annoying for an app that must be the
  device's notification listener.

## Proposal

1. Enable `isMinifyEnabled = true` + `isShrinkResources = true` for release; add keep rules as
   needed (Room and Compose largely ship consumer rules already; verify the notification listener
   service and `CopyDataReceiver` survive since they're manifest-referenced and thus kept
   automatically).
2. Add `applicationIdSuffix = ".debug"` and a distinct launcher label/icon for the debug build.
3. Derive `versionCode` from CI run number or git commit count, `versionName` from the latest git
   tag (simple `providers.exec` in `build.gradle.kts` or a plugin like `gradle-git-versioning`).
4. Extend the GitHub Actions workflow with an `assembleRelease` job (unsigned is fine initially)
   so R8 problems fail PRs, and attach the APK as a workflow artifact; later, add a tag-triggered
   signed-release job using a keystore stored in GitHub secrets.

## Testing

- CI green on `assembleRelease`.
- Install the minified APK, grant notification access, run the built-in Test Screen end-to-end —
  this is the exact scenario R8 would silently break.
