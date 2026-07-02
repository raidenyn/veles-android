# GitHub Actions CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GitHub Actions CI workflow that runs ktlint (via Spotless), detekt, and Android Lint, plus unit and instrumented tests, on every PR to `master`, and enforce branch protection so PRs can only merge when all checks pass.

**Architecture:** Two new Gradle plugins (Spotless for ktlint formatting, detekt for static analysis) are applied at the root level with a committed detekt config and an Android Lint baseline file to snapshot existing warnings. A single `.github/workflows/ci.yml` defines three parallel jobs (`lint-check`, `unit-tests`, `instrumented-tests`) on `ubuntu-latest`. After the workflow runs green on its own PR, branch protection is applied to `master` via `gh api` requiring all three check contexts.

**Tech Stack:** GitHub Actions, Gradle (Kotlin DSL), Spotless 7.0.2, detekt 1.23.7, AGP 8.9.0 (`lintDebug`), `reactivecircus/android-emulator-runner@v2`, `gh` CLI for branch protection.

## Global Constraints

- Java 17 (`distribution: temurin`, `java-version: 17`) — matches `compileOptions` in `app/build.gradle.kts`.
- `minSdk = 33`, `compileSdk = 35`, `targetSdk = 35` (from `app/build.gradle.kts`). Emulator API level must be 33 to match `minSdk`.
- Kotlin 2.1.10, AGP 8.9.0, KSP 2.1.10-1.0.31 (from `gradle/libs.versions.toml`).
- Runner: `ubuntu-latest` for all jobs.
- Job names are exact and must not change after branch protection is applied (protection rule references them by name): `lint-check`, `unit-tests`, `instrumented-tests`.
- No code comments in Kotlin/Gradle files unless asked (per CLAUDE.md).
- 4-space indent, follow existing `build.gradle.kts` style.
- Commit message prefixes: `feat:`, `test:`, `refactor:`, `docs:`, `chore:` (from `git log --oneline`).
- The CI workflow file lives at `.github/workflows/ci.yml`.
- Branch protection requires `gh` authenticated as a repo admin for `raidenyn/veles-android`.
- detekt config lives at `config/detekt/detekt.yml`; Android Lint baseline at `app/lint-baseline.xml`.
- This plan does NOT fix all existing lint/detekt violations — it uses baselines/suppressions to snapshot them. New violations fail CI.

---

## File Structure

| File | Responsibility | Status |
|---|---|---|
| `gradle/libs.versions.toml` | Add `spotless` and `detekt` version + plugin entries. | Modify |
| `build.gradle.kts` | Apply Spotless + detekt plugins at root; configure `spotless {}` and `detekt {}` blocks. | Modify |
| `app/build.gradle.kts` | Add `lint { baseline = ...; abortOnError = true }` inside `android {}`. | Modify |
| `config/detekt/detekt.yml` | detekt rule config (permissive, builds on defaults). | Create |
| `app/lint-baseline.xml` | Android Lint baseline snapshot of existing warnings. | Create |
| `.github/workflows/ci.yml` | CI workflow: 3 parallel jobs (lint-check, unit-tests, instrumented-tests). | Create |

---

## Task 1: Add Spotless + detekt to the Gradle version catalog

**Files:**
- Modify: `gradle/libs.versions.toml`

**Interfaces:**
- Produces: `libs.plugins.spotless` and `libs.plugins.detekt` plugin aliases — consumed by Task 2 (root `build.gradle.kts`).

- [ ] **Step 1: Add versions and plugin entries**

Edit `gradle/libs.versions.toml`. In the `[versions]` section, add these two lines (after the existing `ksp = "..."` line, keeping alphabetical-ish order):

```toml
detekt = "1.23.7"
spotless = "7.0.2"
```

In the `[plugins]` section, add these two lines (after the existing `kotlin-serialization = ...` line):

```toml
detekt = { id = "io.gitlab.arturbosch.detekt", version.ref = "detekt" }
spotless = { id = "com.diffplug.spotless", version.ref = "spotless" }
```

- [ ] **Step 2: Verify the catalog parses**

Run: `./gradlew help`
Expected: Builds successfully (Gradle resolves the new plugin entries without error; no task needs to run yet). Exit code 0.

- [ ] **Step 3: Commit**

```bash
git add gradle/libs.versions.toml
git commit -m "chore: add spotless and detekt to version catalog"
```

---

## Task 2: Apply Spotless + detekt at the root build file

**Files:**
- Modify: `build.gradle.kts`

**Interfaces:**
- Consumes: `libs.plugins.spotless`, `libs.plugins.detekt` (from Task 1).
- Produces: `./gradlew spotlessCheck`, `./gradlew spotlessApply`, `./gradlew detekt` tasks — consumed by Task 5 (CI workflow) and the local verification step.

- [ ] **Step 1: Apply the plugins and add configuration blocks**

Edit `build.gradle.kts`. The current content is:

```kotlin
// Top-level build file where you can apply configuration options common to all sub-projects/modules.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.compose) apply false
    alias(libs.plugins.ksp) apply false
}
```

Replace with:

```kotlin
// Top-level build file where you can apply configuration options common to all sub-projects/modules.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.compose) apply false
    alias(libs.plugins.ksp) apply false
    alias(libs.plugins.spotless)
    alias(libs.plugins.detekt)
}

spotless {
    kotlin {
        target("**/*.kt")
        targetExclude("**/build/**", "**/.gradle/**", "**/.kotlin/**")
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

- [ ] **Step 2: Verify plugins apply**

Run: `./gradlew tasks --group=verification`
Expected: Lists `spotlessCheck`, `spotlessApply`, `detekt` among the available tasks. Exit code 0.

- [ ] **Step 3: Commit**

```bash
git add build.gradle.kts
git commit -m "chore: apply spotless and detekt plugins at root"
```

---

## Task 3: Create the detekt config file

**Files:**
- Create: `config/detekt/detekt.yml`

**Interfaces:**
- Consumes: path `config/detekt/detekt.yml` referenced by `detekt { config.setFrom(...) }` in Task 2.
- Produces: A permissive detekt config that builds on defaults but suppresses noisy rules that would otherwise fail on existing code.

- [ ] **Step 1: Create the config directory and file**

Create directory `config/detekt/` and file `config/detekt/detekt.yml` with this content. This is a minimal config that builds upon detekt's default config (`buildUponDefaultConfig = true` means any rule not listed keeps its default). The rules below are tuned permissive to reach green fast on the existing codebase:

```yaml
# detekt configuration — builds upon the default config.
# Rules not listed here keep their defaults. This file starts permissive
# to avoid a large initial failure surface; tighten over time.

complexity:
  LongMethod:
    threshold: 120
  LongParameterList:
    functionThreshold: 8
    constructorThreshold: 8
  ComplexCondition:
    threshold: 6
  CyclomaticComplexMethod:
    threshold: 20
  TooManyFunctions:
    thresholdInFiles: 15
    thresholdInClasses: 15

style:
  MagicNumber:
    ignoreNumbers: ['-1', '0', '1', '2', '1000']
    ignoreEnums: true
    ignorePropertyDeclaration: true
    ignoreConstantDeclaration: true
  ReturnCount:
    max: 4
  WildcardImport:
    active: false
  MaxLineLength:
    maxLineLength: 140
  ForbiddenComment:
    active: false

exceptions:
  SwallowedException:
    active: false

empty-blocks:
  EmptyFunctionBlock:
    active: false

potential-bugs:
  UnsafeCallOnNullableType:
    active: false

formatting:
  MaximumLineLength:
    maxLineLength: 140
```

- [ ] **Step 2: Verify detekt config loads (expect violations, not config errors)**

Run: `./gradlew detekt`
Expected: Either succeeds, OR fails with *rule violations* (not "config file not found" or YAML parse errors). A config/parse error means the YAML is malformed — fix it. Rule violations are expected and handled in Task 7.

- [ ] **Step 3: Commit**

```bash
git add config/detekt/detekt.yml
git commit -m "chore: add permissive detekt config"
```

---

## Task 4: Add Android Lint baseline to app module

**Files:**
- Modify: `app/build.gradle.kts`
- Create: `app/lint-baseline.xml`

**Interfaces:**
- Produces: `./gradlew lintDebug` that snapshots existing warnings into `app/lint-baseline.xml` and fails only on *new* warnings — consumed by Task 5 (CI workflow).

- [ ] **Step 1: Add the lint block to app/build.gradle.kts**

Edit `app/build.gradle.kts`. Inside the existing `android { }` block, add a `lint { }` block. Place it after the `packaging { }` block (before the closing brace of `android { }`):

```kotlin
    lint {
        baseline = file("lint-baseline.xml")
        abortOnError = true
        warningsAsErrors = false
    }
```

The end of the `android { }` block should now read:

```kotlin
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
            merges += "META-INF/LICENSE.md"
            merges += "META-INF/LICENSE-notice.md"
        }
    }
    lint {
        baseline = file("lint-baseline.xml")
        abortOnError = true
        warningsAsErrors = false
    }
}
```

- [ ] **Step 2: Generate the lint baseline**

Run: `./gradlew lintDebug --update-baseline`
Expected: Creates `app/lint-baseline.xml` containing the current lint findings (may be empty if no warnings). Exit code 0.

- [ ] **Step 3: Verify the baseline file exists**

Run: `ls -la app/lint-baseline.xml`
Expected: File exists. (If empty/no warnings, the file may still be a minimal XML wrapper — that's fine.)

- [ ] **Step 4: Verify lint passes with the baseline**

Run: `./gradlew lintDebug`
Expected: Exit code 0 (existing warnings are snapshotted; no new warnings).

- [ ] **Step 5: Commit**

```bash
git add app/build.gradle.kts app/lint-baseline.xml
git commit -m "chore: add android lint baseline"
```

---

## Task 5: Run linters locally and fix formatting

**Files:**
- Modify: all `**/*.kt` files (auto-formatted by Spotless)
- Modify: possibly `config/detekt/detekt.yml` (add suppressions for unfixable detekt violations)

**Interfaces:**
- Produces: A codebase where `./gradlew spotlessCheck`, `./gradlew detekt`, and `./gradlew lintDebug` all pass locally — the prerequisite for the CI workflow to go green.

- [ ] **Step 1: Run spotlessApply to auto-fix formatting**

Run: `./gradlew spotlessApply`
Expected: Modifies any `**/*.kt` files with formatting issues (trailing whitespace, missing final newline, ktlint style violations). Exit code 0.

- [ ] **Step 2: Verify spotlessCheck passes**

Run: `./gradlew spotlessCheck`
Expected: Exit code 0. If it fails, run `./gradlew spotlessApply` again and re-check.

- [ ] **Step 3: Run detekt and inspect failures**

Run: `./gradlew detekt`
Expected: Either exit code 0 (pass), OR a report of violations in `build/reports/detekt/detekt.txt` (or console). If it passes, skip to Step 5.

- [ ] **Step 4: Fix or suppress detekt violations**

For each violation reported:
- If it's a genuine code smell that's easy to fix (unused import, explicit it, etc.), fix it in the source file.
- If it's noisy or would require a large refactor, add a suppression to `config/detekt/detekt.yml` under the relevant rule (set `active: false` or raise the threshold).

Re-run `./gradlew detekt` after each change. Repeat until exit code 0.

- [ ] **Step 5: Verify all three linters pass**

Run these in sequence:
```bash
./gradlew spotlessCheck
./gradlew detekt
./gradlew lintDebug
```
Expected: All three exit code 0.

- [ ] **Step 6: Verify unit tests still pass**

Run: `./gradlew testDebugUnitTest`
Expected: Exit code 0 (formatting changes should not break tests).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: apply spotless formatting and detekt suppressions"
```

---

## Task 6: Create the CI workflow file

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `./gradlew spotlessCheck`, `./gradlew detekt`, `./gradlew lintDebug`, `./gradlew testDebugUnitTest`, `./gradlew connectedDebugAndroidTest` (from Tasks 2, 4, 5).
- Produces: Three GitHub Actions check contexts named `lint-check`, `unit-tests`, `instrumented-tests` — consumed by Task 8 (branch protection).

- [ ] **Step 1: Create the workflow directory and file**

Create `.github/workflows/ci.yml` with this exact content:

```yaml
name: CI

on:
  pull_request:
    branches: [master]
  push:
    branches: [master]

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint-check:
    name: lint-check
    runs-on: ubuntu-latest
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

  unit-tests:
    name: unit-tests
    runs-on: ubuntu-latest
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
          if-no-files-found: ignore

  instrumented-tests:
    name: instrumented-tests
    runs-on: ubuntu-latest
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
          if-no-files-found: ignore
```

- [ ] **Step 2: Validate YAML syntax locally**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo OK`
Expected: Prints `OK` (valid YAML). If it errors, fix indentation.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "feat: add GitHub Actions CI workflow"
```

---

## Task 7: Push the branch and open the PR

**Files:**
- None (git operations only)

**Interfaces:**
- Produces: A PR on GitHub where the CI workflow runs against its own changes — the meta-verification step.

- [ ] **Step 1: Create the CI feature branch**

Run: `git checkout -b ci/add-github-actions`
Expected: On the new branch (carrying all commits from Tasks 1–6).

- [ ] **Step 2: Push the branch to origin**

Run: `git push -u origin ci/add-github-actions`
Expected: Branch pushed to `origin`.

- [ ] **Step 3: Open the PR**

Run:
```bash
gh pr create \
  --base master \
  --head ci/add-github-actions \
  --title "feat: add GitHub Actions CI (lint, detekt, tests)" \
  --body "Adds Spotless (ktlint), detekt, and Android Lint to the build, plus a CI workflow with three parallel jobs (lint-check, unit-tests, instrumented-tests) running on every PR. Includes detekt config and lint baseline to snapshot existing warnings. Branch protection to be applied after this PR goes green."
```
Expected: PR created; URL printed.

- [ ] **Step 4: Record the PR URL**

Note the PR URL from the previous command output. It will be used in Task 8.

---

## Task 8: Watch CI and fix until all jobs are green

**Files:**
- Possibly modify: `config/detekt/detekt.yml`, `app/lint-baseline.xml`, source `.kt` files, `.github/workflows/ci.yml` (depends on what fails)

**Interfaces:**
- Produces: All three check contexts (`lint-check`, `unit-tests`, `instrumented-tests`) in a success state on the PR — the prerequisite for Task 9 (branch protection).

- [ ] **Step 1: Wait for CI to start and check status**

Run: `gh pr checks <PR_URL>` (or `gh run list --branch ci/add-github-actions`)
Expected: Three runs appear (one per job). Wait until they complete.

- [ ] **Step 2: Inspect any failures**

Run: `gh run view --log-failed` (for the failing run), or view logs in the GitHub Actions UI.
Expected: Console output showing which step failed and why.

- [ ] **Step 3: Fix failures iteratively**

For each failing job, fix the root cause:

- **`lint-check` / `spotlessCheck` fails:** Run `./gradlew spotlessApply` locally, commit, push.
- **`lint-check` / `detekt` fails:** Either fix the violation in source, or add a suppression to `config/detekt/detekt.yml`. Commit, push.
- **`lint-check` / `lintDebug` fails:** A *new* lint warning (not in baseline). Either fix it in source, or regenerate the baseline with `./gradlew lintDebug --update-baseline`. Commit, push.
- **`unit-tests` fails:** A real test failure. Debug and fix the source or test. Commit, push.
- **`instrumented-tests` fails:**
  - If it's an emulator boot failure: retry the job (`gh run rerun --failed`). If it persists, set `force-avd-creation: true` in `.github/workflows/ci.yml` (lets the action create a fresh AVD each run).
  - If it's a real test failure: debug and fix. Commit, push.

After each fix: `git push` and re-check with `gh pr checks <PR_URL>`.

- [ ] **Step 4: Confirm all three jobs are green**

Run: `gh pr checks <PR_URL>`
Expected: All three checks show `pass` / `success`. No failing checks.

- [ ] **Step 5: Commit any final fixes**

If fixes were made in Step 3, ensure they're all committed and pushed. Run `git status` — expected: clean working tree, branch up to date with `origin/ci/add-github-actions`.

---

## Task 9: Apply branch protection to master

**Files:**
- None (GitHub API operation)

**Interfaces:**
- Consumes: The three check contexts `lint-check`, `unit-tests`, `instrumented-tests` (now registered with GitHub after Task 8 ran them successfully).
- Produces: A branch protection rule on `master` that blocks merging PRs unless all three checks pass.

- [ ] **Step 1: Verify gh is authenticated as a repo admin**

Run: `gh auth status`
Expected: Shows authenticated account with access to `raidenyn/veles-android`. If not authenticated or lacks admin, stop and inform the user that branch protection must be applied via the GitHub web UI (Settings → Branches → master → Add rule) with the settings below:
- Require status checks to pass: `lint-check`, `unit-tests`, `instrumented-tests`
- Require branches up to date before merging: yes
- Require pull request reviews: 1 approving review
- Dismiss stale reviews on push: yes
- Enforce for administrators: yes
- Allow force pushes: no
- Allow deletions: no

- [ ] **Step 2: Apply branch protection via gh api**

Run:
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
Expected: HTTP 200 with the protection rule JSON. If HTTP 403/404, the account lacks admin — fall back to the web UI instructions in Step 1.

- [ ] **Step 3: Verify the protection rule is in place**

Run:
```bash
gh api repos/raidenyn/veles-android/branches/master/protection
```
Expected: JSON showing `required_status_checks.contexts` includes all three job names, `enforce_admins.enabled = true`.

- [ ] **Step 4: Confirm the PR is mergeable**

Run: `gh pr view <PR_URL> --json mergeable,mergeStateStatus`
Expected: `mergeable: true` and a merge state indicating checks are required and passing.

---

## Task 10: Merge the PR

**Files:**
- None (git/GitHub operation)

- [ ] **Step 1: Merge the PR**

Run:
```bash
gh pr merge <PR_URL> --squash --delete-branch
```
Expected: PR merged into `master`, branch `ci/add-github-actions` deleted. If merge is blocked because a review is required (branch protection requires 1 review), either request a review from another admin, or temporarily adjust the `required_approving_review_count` to 0 via `gh api` before merging — then restore it to 1.

- [ ] **Step 2: Verify CI runs on master**

Run: `gh run list --branch master --limit 3`
Expected: A new CI run triggered by the merge push to `master`. Wait for it to go green (confirms the workflow runs on `push: [master]` too).

- [ ] **Step 3: Final verification**

Confirm all of the following:
1. `.github/workflows/ci.yml` exists on `master`
2. `gh pr checks` on the merged commit shows all three jobs green
3. `gh api repos/raidenyn/veles-android/branches/master/protection` shows the protection rule with all three contexts required
4. Branch `ci/add-github-actions` is deleted

If all four hold, the task is complete.

---

## Self-Review Notes

- **Spec coverage:** Every section of the spec maps to a task — build tooling (Tasks 1–4), local fix (Task 5), CI workflow (Task 6), PR creation (Task 7), verification/fix loop (Task 8), branch protection (Task 9), merge (Task 10).
- **No placeholders:** All steps contain concrete commands, file contents, or exact edits.
- **Type/name consistency:** Job names `lint-check`, `unit-tests`, `instrumented-tests` are used identically in the workflow file (Task 6), the fix loop (Task 8), branch protection (Task 9), and verification (Task 10).