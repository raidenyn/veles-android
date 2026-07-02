# GitHub Actions CI — Design Spec

**Date:** 2026-07-01
**Status:** Approved (pending implementation)
**Scope:** Add CI that runs linters (ktlint via Spotless, detekt, Android Lint) and all tests (unit + instrumented) on every PR, and enforce branch protection so PRs can only merge when all checks pass.

---

## Context

The Veles Android project currently has no CI. There is no `.github/` directory, no detekt config, and no linter plugin configured. The project has:

- Unit tests (`testDebugUnitTest`) — JVM-only, no device needed
- Instrumented tests (`connectedDebugAndroidTest`) — require an Android device/emulator
- Java 17, Kotlin 2.1.10, AGP 8.9.0, minSdk 33, compileSdk 35
- Remote: `git@github.com:raidenyn/veles-android.git`

## Goals

1. Run three linters on every PR: ktlint (formatting), detekt (static analysis), Android Lint (AGP built-in)
2. Run unit tests on every PR
3. Run instrumented tests on every PR via a headless emulator
4. Block merging a PR to `master` unless all checks pass
5. Get CI green on the first PR without a massive code-cleanup detour

## Non-Goals

- Fixing all existing lint/detekt violations across the codebase (use baselines/suppressions instead)
- CI for release builds, app signing, or deployment
- Coverage reporting / codecov integration
- Multiple OS matrix builds

## Architecture

### Build Tooling Changes (Gradle)

**`gradle/libs.versions.toml`** — add to `[versions]`:
```toml
spotless = "7.0.2"
detekt = "1.23.7"
```
Add to `[plugins]`:
```toml
spotless = { id = "com.diffplug.spotless", version.ref = "spotless" }
detekt = { id = "io.gitlab.arturbosch.detekt", version.ref = "detekt" }
```

**Root `build.gradle.kts`** — apply both plugins at the root level and configure:

```kotlin
plugins {
    // existing plugins...
    alias(libs.plugins.spotless)
    alias(libs.plugins.detekt)
}

spotless {
    kotlin {
        target("**/*.kt")
        ktlint()
        trimTrailingWhitespace()
        endWithNewline()
    }
}

detekt {
    buildUponDefaultConfig = true
    config.setFrom("config/detekt/detekt.yml")
}
```

**New file `config/detekt/detekt.yml`** — a minimal config that builds on detekt's defaults. Start permissive to reach green fast, tighten over time. Generated via `./gradlew detektGenerateConfig` then trimmed. Key tunings: relax rules that conflict with existing code style (e.g., `MagicNumber`, `LongMethod` thresholds) to avoid a large initial failure surface.

**Android Lint baseline** — to avoid fixing all existing lint warnings in the first PR, add to `app/build.gradle.kts` inside the `android { }` block:
```kotlin
lint {
    baseline = file("lint-baseline.xml")
    abortOnError = true
}
```
Generate the baseline with `./gradlew lintDebug --update-baseline` once, commit `app/lint-baseline.xml`. New violations will fail CI; existing ones are snapshotted.

### CI Workflow

**File:** `.github/workflows/ci.yml`

**Triggers:** `pull_request` (all branches targeting `master`) and `push` to `master`.

**Concurrency:** Cancel in-progress runs on the same ref when a new push arrives (`cancel-in-progress: true`).

**Runner:** `ubuntu-latest` for all three jobs.

**Setup (shared by all jobs):**
- `actions/checkout@v4`
- `actions/setup-java@v4` with `distribution: temurin`, `java-version: 17`
- `gradle/actions/setup-gradle` for caching (or `actions/cache` on `~/.gradle` + `**/.gradle`)

**Job 1 — `lint-check`** (sequential, all fast):
```yaml
steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-java@v4
    with:
      distribution: temurin
      java-version: 17
  - uses: gradle/actions/setup-gradle@v4
  - run: ./gradlew spotlessCheck
  - run: ./gradlew detekt
  - run: ./gradlew lintDebug
```

**Job 2 — `unit-tests`:**
```yaml
steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-java@v4
    with:
      distribution: temurin
      java-version: 17
  - uses: gradle/actions/setup-gradle@v4
  - run: ./gradlew testDebugUnitTest
  - uses: actions/upload-artifact@v4
    if: always()
    with:
      name: unit-test-results
      path: app/build/test-results/
```

**Job 3 — `instrumented-tests`:**
```yaml
steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-java@v4
    with:
      distribution: temurin
      java-version: 17
  - uses: gradle/actions/setup-gradle@v4
  - name: Enable KVM
    run: |
      echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' | sudo tee /etc/udev/rules.d/99-kvm4all.rules
      sudo udevadm control --reload-rules
      sudo udevadm trigger --name-match=kvm
      sudo udevadm settle
      ls -l /dev/kvm
  - uses: reactivecircus/android-emulator-runner@v2
    with:
      api-level: 33
      arch: x86_64
      target: google_apis
      force-avd-creation: false
      emulator-options: -no-window -gpu swiftshader_indirect -no-snapshot -noaudio -no-boot-anim
      disable-animations: true
      script: ./gradlew connectedDebugAndroidTest
  - uses: actions/upload-artifact@v4
    if: always()
    with:
      name: instrumented-test-results
      path: app/build/outputs/androidTest-results/
```

All three jobs run in parallel (no `needs:` dependencies between them). This gives the fastest wall time and independent failure signals.

### Branch Protection (Enforcement)

Configured via `gh api` **after** the CI workflow has run at least once (GitHub requires check contexts to have reported before they can be required).

```bash
gh api -X PUT repos/raidenyn/veles-android/branches/master/protection \
  --input - <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["lint-check", "unit-tests", "instrumented-tests"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```

- `strict: true` — require branch to be up-to-date with master before merge
- `enforce_admins: true` — admins are also blocked from merging on failing checks
- `dismiss_stale_reviews: true` — new pushes dismiss prior approvals
- `required_approving_review_count: 1` — at least one human review

**Prerequisite:** `gh` must be authenticated as a repo admin. If not, this step fails and must be done via the GitHub web UI (Settings → Branches → master → Add rule).

## Verification & Rollout

1. Create branch `ci/add-github-actions` from current branch
2. Implement all changes: `libs.versions.toml`, root `build.gradle.kts`, `config/detekt/detekt.yml`, `app/build.gradle.kts` (lint baseline block), `app/lint-baseline.xml`, `.github/workflows/ci.yml`
3. Run locally first to reduce CI iteration count:
   - `./gradlew spotlessApply` then `./gradlew spotlessCheck`
   - `./gradlew detekt` (fix/suppress violations)
   - `./gradlew lintDebug --update-baseline` (snapshot existing warnings)
   - `./gradlew testDebugUnitTest`
4. Push branch, open PR to `master`
5. Watch CI run on the PR. Fix failures until all three jobs are green:
   - ktlint: run `spotlessApply` locally, commit
   - detekt: fix or add targeted suppressions in `config/detekt/detekt.yml`
   - Android Lint: regenerate baseline if new warnings surface
   - instrumented tests: may be flaky on first run — retry, tune emulator options if needed
6. Once all green, apply branch protection via `gh api` (above)
7. Merge the PR

## Error Handling

- **Emulator boot failures:** `android-emulator-runner` has a built-in retry. If consistently failing, the `Enable KVM` step ensures hardware acceleration is available on Ubuntu runners. Fallback: drop `force-avd-creation: false` to let it create a fresh AVD each time.
- **Flaky instrumented tests:** Use `disable-animations: true` and `-no-snapshot`. If a specific test is flaky, mark it with `@FlakyTest` and configure `connectedDebugAndroidTest` to retry flaky tests.
- **detekt config drift:** If detekt fails after a version bump, regenerate config with `./gradlew detektGenerateConfig` and diff/merge.
- **First-run check context registration:** Branch protection `required_status_checks.contexts` must exactly match the job names (`lint-check`, `unit-tests`, `instrumented-tests`). If job names change, update protection too.

## Testing

The CI workflow itself is verified by:
1. It runs on its own PR (meta-verification)
2. All three jobs reach green status
3. Branch protection blocks a test PR with a failing check (manual confirmation: push a commit that breaks a test, confirm the merge button is disabled)

No separate test suite is needed for the CI configuration — the workflow *is* the test that the build tooling works.