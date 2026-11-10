# Component Releases and Conventional Commits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Version `android`, `web-extension` and `installer` independently from Conventional Commits and git tags, publish them through one owner-only combined release, enforce Conventional Commits locally and on PR titles, and restructure CI into a change-gated component tree.

**Architecture:** One component map (`.github/components.json`) feeds stdlib Python tools: `components.py` (CI selection), `release_plan.py` (git-cliff per component → RELEASE/CARRY plan and notes), `release_assets.py` (asset sets, manifest, carry and draft verification) and `release_ready.py` (job readiness). Workflows only orchestrate: `ci.yml` calls `check-<node>.yml` behind `ci-ok`; `release-build.yml` builds by label or change; `publish-release.yml` alone signs, tags and publishes. Versions are injected at build time.

**Tech Stack:** GitHub Actions (SHA-pinned), Python 3 stdlib `unittest`, git-cliff (pinned), Gradle Kotlin DSL, androidGitVersion 0.4.14, apksigner (build-tools 36.0.0), TypeScript/Vitest, Rust, Node `--test`.

**Spec:** `openspec/changes/component-releases-and-conventional-commits/design.md` and `specs/{component-versioning,release-publishing,commit-conventions,ci-pipeline}/spec.md`

## Global Constraints

- Release tags `android-v<semver>`, `web-extension-v<semver>`, `installer-v<semver>`; initial version `0.1.0`.
- Combined release tag UTC `YYYY.MM.DD`, then `YYYY.MM.DD.2`, `.3`.
- Owner `raidenyn`; publish only by `workflow_dispatch` on `refs/heads/master` with `github.actor == github.triggering_actor == raidenyn`; `concurrency: { group: publish-release, cancel-in-progress: false }`.
- Environments: `android-release` (keystore secrets + variable `VELES_ANDROID_CERT_SHA256`), `native-release` (unchanged), `release-publish`, `native-installer-debug` (unchanged).
- Commit types `feat fix perf refactor docs test build ci chore style revert`; header ≤ 100; breaking footer requires `!`.
- Bumps: `feat` minor; `fix`/`perf` patch; `!`/`BREAKING CHANGE:`/`BREAKING-CHANGE:` major (also below 1.0); nothing else.
- Release assets exactly: `veles-android-<v>.apk`, `veles-android-<v>-mapping.txt`, `veles-web-extension-<v>.zip`, `veles-installer-windows-<v>.exe`, `veles-installer-macos-<v>.dmg`, `release-manifest.json`, `SHA256SUMS`.
- Extension version: Chrome rules (1–4 integers, each 0..65535, no leading zeros, not all zero); untagged fallback `0.0.1`.
- Python tools: stdlib only, `python3 -I`, Python 3.8 compatible. Every `uses:` pinned to a commit SHA with a version comment.
- No committed version bumps; `Cargo.toml`/`Cargo.lock` never modified at build time; `web-extension/.npmrc` `ignore-scripts=true` stays.
- **Every commit** passes all three contract suites and the remote-code scan scope check: `node --test verify/test/workflow-contracts.test.mjs`, `python3 -I -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py'`, `python3 -I -m unittest discover -s native-bridge/installer/scripts -p 'test_build_contract.py'`. A task that touches a workflow updates the affected suites in the same commit.
- Before the PR: `./gradlew spotlessCheck detekt lintDebug` plus the raw `android.util.Log` guard (CLAUDE.md).

## Review Focus

1. **PR titles/bodies with shell syntax** (`` ` ``, `$(…)`, quotes, newlines): no `github.event.pull_request.title`/`.body` inside any `run:`. Pinned in Task 9 (contract test).
2. **CRLF and `#` lines in hook messages** from Windows editors. Pinned in Task 2.
3. **Files moved across components** must select both nodes (`--no-renames`). Pinned in Task 3.
4. **Shallow checkouts** make every component look unreleased: `release_plan.py` refuses shallow repositories. Pinned in Task 4.
5. **Legacy releases, drafts and prereleases** (`0.0.3`, a stale draft, a prerelease) must never be taken as the previous combined release. Pinned in Task 4.

---

### Task 1: Spike — confirm git-cliff and androidGitVersion (throwaway)

**Files:**
- Modify: `openspec/changes/component-releases-and-conventional-commits/design.md` (D2, D3, Risks); the scratch repo is not committed.

**Interfaces:**
- Produces: exact git-cliff invocations and `[bump]` settings for Task 4; the Android mechanism (`prefix` or fallback) for Task 6.

- [ ] **Step 1:** In a scratch repo under the session scratchpad, create `app/`, `web-extension/`, `rust/`. Tag `android-v0.1.0` on an old commit and `web-extension-v0.2.0` on a newer one, then add conventional commits around them.
- [ ] **Step 2:** With the pinned git-cliff and the candidate `cliff.toml` (`[bump] features_always_bump_minor = true`, `breaking_always_bump_major = true`), run `--include-path "app/**" --include-path "rust/**" --tag-pattern '^android-v[0-9]+\.[0-9]+\.[0-9]+$' --bumped-version`. Expected: the range starts at `android-v0.1.0`, the web tag has no effect, and a `rust/`-only fix counts. Record whether the output carries the prefix.
- [ ] **Step 3:** Verify the bump table on 0.x and 1.x tags: `feat` → minor, `fix`/`perf` → patch; `feat!`, `BREAKING CHANGE:` and `BREAKING-CHANGE:` → major (0.3.2 → 1.0.0); `docs`, `chore`, `refactor`, `test`, `build`, `ci`, `style`, `revert` and non-conventional → no change.
- [ ] **Step 4:** Verify `--unreleased --strip all` lists only the component's commits, and that with no tag the range `<since>..HEAD` limits the notes.
- [ ] **Step 5:** In a throwaway clone, set `androidGitVersion { prefix = "android-v"; codeFormat = "MMNNPP" }`, tag `android-v1.2.3` and run `./gradlew -q :app:androidGitVersion`. Expected: `1.2.3` / `10203`, with `0.0.3` ignored when untagged. Otherwise adopt the fallback (`git describe --tags --match 'android-v*'` parsed in `app/build.gradle.kts`).
- [ ] **Step 6:** Record the commands, `[bump]` block and chosen mechanisms in design.md D2/D3, and resolve the matching Risk lines.
- [ ] **Step 7: Commit:** `git commit -m "docs(openspec): record git-cliff and androidGitVersion spike results"`

### Task 2: Commit message validator and local hook

**Files:**
- Create: `scripts/commitlint.py`, `scripts/tests/test_commitlint.py`, `.githooks/commit-msg`
- Modify: `build.gradle.kts` (task `installGitHooks`), `app/build.gradle.kts` (`preBuild` dependency)

**Interfaces:**
- Produces: `validate(message: str, *, allow_git_generated: bool = False) -> list[str]` (empty = valid); CLI `python3 -I scripts/commitlint.py (--file PATH | --message TEXT) [--allow-git-generated]` → exit 0 valid, 1 invalid (reasons on stderr), 2 usage. Used by Task 9.

- [ ] **Step 1: Write failing tests** (`unittest`; tests prepend `<repo>/scripts` to `sys.path` because `-I` drops the cwd):

```python
self.assertEqual(validate("feat(android): add auto-copy toggle"), [])
self.assertTrue(any("type" in r for r in validate("OTP-26: add auto-copy toggle")))
self.assertTrue(any("feat(protocol)!:" in r for r in validate("feat(protocol): v2\n\nBREAKING CHANGE: x")))
self.assertTrue(validate("feat(protocol): v2\n\nBREAKING-CHANGE: x"))
self.assertEqual(validate("feat(protocol)!: v2\n\nBREAKING CHANGE: x"), [])
self.assertTrue(any("101" in r for r in validate("fix: " + "x" * 96)))
self.assertEqual(validate("fix: " + "x" * 95), [])
self.assertTrue(validate("fix: a\nbody"))
self.assertTrue(validate("fix(Android): a"))
self.assertTrue(validate("fix: "))
self.assertEqual(validate("fix: a\r\n\r\nbody\r\n# Please enter the commit message\r\n"), [])   # Review Focus 2
for s in ('Merge branch "x"', 'Revert "feat: a"', "fixup! feat: a", "squash! feat: a"):
    self.assertTrue(validate(s)); self.assertEqual(validate(s, allow_git_generated=True), [])
```

  Add CLI tests: `--message` valid → 0; invalid → 1 with the reason on stderr; no flag → 2; `--file` with a CRLF file → 0.
- [ ] **Step 2:** `python3 -I -m unittest discover -s scripts/tests -p 'test_commitlint.py' -v` → FAIL (module missing).
- [ ] **Step 3: Implement** `validate` and `main(argv: list[str]) -> int`. Normalise `\r\n`, drop `#` lines, strip trailing blank lines. Header regex: `^(feat|fix|perf|refactor|docs|test|build|ci|chore|style|revert)(\([a-z0-9][a-z0-9./-]*\))?(!)?: \S.*$`.
- [ ] **Step 4:** Same command → PASS.
- [ ] **Step 5:** `.githooks/commit-msg`: `#!/bin/sh` + `exec python3 -I "$(git rev-parse --show-toplevel)/scripts/commitlint.py" --allow-git-generated --file "$1"`; then `git update-index --chmod=+x .githooks/commit-msg`.
- [ ] **Step 6:** Root `installGitHooks`:
  - Inject `ExecOperations` and read `CI` via `providers.environmentVariable`, so the task is configuration-cache safe.
  - Read `git config --get core.hooksPath` and act on the result: unset → set `.githooks`; already `.githooks` → nothing; any other value → `logger.warn` naming it, unchanged.
  - Skip when `CI=true`; no outputs.
  - `:app:preBuild` dependsOn `":installGitHooks"`.
- [ ] **Step 7: Commit:** `git commit -m "build: enforce conventional commits with a local commit-msg hook"`
- [ ] **Step 8: Verify the committed candidate in a disposable clone** (never in the working checkout; all hook-config changes stay in the clone), with the configuration cache enabled. If anything fails, fix it and `git commit --amend`, then re-clone:
  - `git clone --no-local . "$SCRATCH/hookcheck" && cd "$SCRATCH/hookcheck" && git log -1 --format=%s` → the Step 7 subject
  - `./gradlew -q --configuration-cache installGitHooks && git config --get core.hooksPath` → `.githooks`. Run it a second time: it reuses the configuration cache and changes nothing.
  - `git commit --allow-empty -m wip` → rejected.
  - `git config core.hooksPath /tmp/x && ./gradlew -q installGitHooks` → warning, value still `/tmp/x`.
  - `git config --unset core.hooksPath && CI=true ./gradlew -q installGitHooks && git config --get core.hooksPath` → empty.

### Task 3: Component map and change selection

**Files:**
- Create: `.github/components.json`, `scripts/components.py`, `scripts/tests/test_components.py`

**Interfaces:**
- Produces:
  - `components.json` (schema and node table exactly as design.md D1; `bridge-scaffold` lists `native-bridge/` children explicitly; `supply-chain` lists each manifest/lockfile path).
  - `load_map(path: Path) -> dict` (raises `ValueError` naming node+path for wildcards, missing `tag-prefix`/`initial-version` on release nodes).
  - `owner_nodes(file: str, cmap: dict) -> set[str] | None`: `None` = global or unaccounted (select all); an empty set = unowned.
  - `affected(files: list[str] | None, cmap: dict) -> dict[str, bool]`.
  - `changed_files(base: str | None, head: str = "HEAD") -> list[str] | None`: `None` for empty, all-zero or unknown base; uses `git diff --name-only --no-renames`.
  - `unaccounted(files: list[str], cmap: dict) -> list[str]`.
  - `release_paths(node: str, cmap: dict) -> list[str]` (the node's paths plus `global`).
  - CLI `components.py check` (every `git ls-files` top-level entry accounted for; map valid) and `components.py changed --base SHA [--head SHA] [--github-output PATH]` (writes `<node>=true|false` for every node; exit 1 if any changed file is unaccounted, after writing all-true outputs).

- [ ] **Step 1: Write failing tests** against the real map, using a temporary git repo for `changed_files`:

```python
a = affected(["web-extension/src/a.ts"], CMAP); self.assertTrue(a["web-extension"]); self.assertFalse(a["android"]); self.assertFalse(a["installer"])
a = affected(["rust/veles-crypto/src/lib.rs"], CMAP)
for n in ("rust", "android", "web-extension", "protocol"): self.assertTrue(a[n])
self.assertFalse(a["installer"])
self.assertTrue(all(affected(["build.gradle.kts"], CMAP).values()))
self.assertTrue(all(affected([".github/workflows/check-android.yml"], CMAP).values()))
self.assertTrue(all(affected(["newdir/x"], CMAP).values())); self.assertEqual(unaccounted(["newdir/x"], CMAP), ["newdir/x"])
self.assertTrue(affected(["verify/lib/checksum-manifest.mjs"], CMAP)["web-extension"])
self.assertTrue(affected(["config/detekt/detekt.yml"], CMAP)["android"])
self.assertFalse(any(affected(["docs/ci.md"], CMAP).values()))
self.assertIsNone(changed_files("0" * 40)); self.assertTrue(all(affected(None, CMAP).values()))
self.assertIn("build.gradle.kts", release_paths("installer", CMAP))
# rename across components (Review Focus 3): temp repo, git mv app/x.kt web-extension/x.kt
files = changed_files(base, head); self.assertIn("app/x.kt", files); self.assertIn("web-extension/x.kt", files)
with self.assertRaises(ValueError): load_map(tmp_map_with("app/**"))
self.assertEqual(main(["check"]), 0)   # this repository is fully accounted for
```

- [ ] **Step 2:** `python3 -I -m unittest discover -s scripts/tests -p 'test_components.py' -v` → FAIL.
- [ ] **Step 3:** Audit cross-directory dependencies, not only source imports:
  - relative imports: `grep -rn "\.\./" --include=*.mjs --include=*.ts --include=*.js --include=*.kts --include=*.toml` outside `node_modules`;
  - build and config references: `grep -rn "rootProject.projectDir\|rootDir\|\${{ github.workspace }}\|config/\|verify/\|protocol/" --include=*.kts --include=*.mjs --include=*.json --include=*.toml --include=*.py` in each component directory.
  Every dependency crossing a top-level directory becomes a path entry (known: `web-extension` ← `verify/lib/`, `android` ← `config/detekt/`). Write `components.json`, then implement the functions. Matching uses `startswith(prefix)` for prefixes and equality for files.
- [ ] **Step 4:** Tests → PASS; `python3 -I scripts/components.py check` → 0.
- [ ] **Step 5: Commit:** `git commit -m "ci: add component map and change selection"`

### Task 4: Release tooling (git-cliff, planner, assets, readiness)

**Files:**
- Create: `cliff.toml`, `scripts/release_plan.py`, `scripts/release_assets.py`, `scripts/release_ready.py`, `scripts/tests/{gitfixture.py,test_release_plan.py,test_release_assets.py,test_release_ready.py}`
- Modify: `build.gradle.kts` (tasks `installGitCliff`, `scriptsTest`; clean contract outputs), pinned-tool and supply-chain registration under `verify/` as the existing tool pins require, `rust/scripts/assert-clean.sh` if it enumerates tool dirs

**Interfaces:**
- Consumes: `load_map`, `release_paths` (Task 3); the Task 1 commands.
- Produces:
  - `installGitCliff` → `build/release-tools/git-cliff/git-cliff` (pinned version + sha256 per OS/arch, verified before extraction). `scriptsTest` runs `python3 -I -m unittest discover -s scripts/tests` with `VELES_GIT_CLIFF` set.
  - `release_plan.py`:
    - `latest_combined_release(rows: list[dict]) -> str | None` (accepts only `^\d{4}\.\d{2}\.\d{2}(\.\d+)?$`, not draft, not prerelease; newest by `publishedAt`).
    - `next_release_tag(today: date, existing: list[str]) -> str`, where `existing` is the union of release tag names (drafts included) and remote git tags matching `^\d{4}\.\d{2}\.\d{2}(\.\d+)?$` (`git ls-remote --tags origin`), so a stray date tag is never reused.
    - `make_plan(repo: Path, cmap: dict, today: date, release_tags: list[str], previous_manifest: dict | None, cliff: Path) -> Plan`, with `Plan(release_tag, previous_release, components: list[Component])` and `Component(name, display, current: str | None, next, action: "RELEASE"|"CARRY", tag, notes, reasons: dict[str, int])`.
    - Raises `PlanError` on a shallow repo or orphaned tags.
    - `render_notes(plan, previous_manifest) -> str`, `render_summary(plan) -> str`.
    - `release_refs(plan: dict) -> list[str]`: `refs/tags/<prefix><next>` for RELEASE components only; CLI `tags --plan-json F` prints one per line (used by `publish`).
    - CLI `latest --releases-json F` (prints a tag or nothing); `plan --releases-json F --manifest-json F --out plan.json [--summary F] [--notes F]`. `plan.json` = `{"release_tag","previous_release","any_release","components":{"android":{"current","next","action","tag"},…}}` and GitHub outputs `<component>=RELEASE|CARRY`, `<component>-version`, `any-release`.
  - `release_assets.py`:
    - `expected_assets(component: str, version: str) -> list[str]` (android: APK + mapping; web-extension: zip; installer: windows `.exe` + macos `.dmg`).
    - `verify_carry(manifest: dict, component: str, directory: Path) -> None`: exactly the listed files, each digest equal, else `AssetError`.
    - `stage(plan: dict, inputs: dict[str, Path], carried: Path, out: Path) -> dict`: copies and renames, writes `release-manifest.json` + `SHA256SUMS`, returns `{name: sha256}`.
    - `verify_draft(expected: dict[str, str], directory: Path) -> None`.
    - CLI subcommands `verify-carry`, `stage`, `verify-draft`.
  - `release_ready.py`: `ready(needs: dict, plan: dict) -> list[str]` (empty = ready), `COMPONENT_JOBS = {"android": ["build-android", "sign-android"], "web-extension": ["build-web-extension"], "installer": ["build-windows", "verify-windows", "sign-windows", "build-macos", "verify-macos", "sign-macos", "gate-installer"]}`, `ALWAYS = ["guard", "plan", "supply-chain", "carry"]`; CLI `ready --needs-json F --plan-json F`.

- [ ] **Step 1:** Add `installGitCliff` and `cliff.toml` (Task 1's `[bump]` block, `filter_unconventional = true`, parsers grouping feat/fix/perf, body `### {{ group }}` / `- {{ message }} ({{ id | truncate(length=7, end="") }})`). `./gradlew installGitCliff && build/release-tools/git-cliff/git-cliff --version` prints the pinned version. Then `./gradlew clean && rust/scripts/assert-clean.sh` still passes.
- [ ] **Step 2: Write failing planner tests** using `gitfixture.py` repos (dirs `app/`, `web-extension/`, `rust/`, `native-bridge/installer/`, file `build.gradle.kts`):

```python
# tags android-v0.1.0, web-extension-v0.1.0, installer-v0.1.0 + manifest listing all three at 0.1.0
c = comps(plan_after("fix(crypto): x", touching="rust/a.rs"))
self.assertEqual((c["android"].next, c["android"].action), ("0.1.1", "RELEASE"))
self.assertEqual(c["web-extension"].next, "0.1.1"); self.assertEqual(c["installer"].action, "CARRY")
self.assertEqual(comps(plan_after("fix(build): x", touching="build.gradle.kts"))["installer"].next, "0.1.1")
self.assertEqual(comps(plan_after("perf(android): x", touching="app/a.kt"))["android"].next, "0.1.1")
self.assertEqual(comps(plan_after("feat(protocol)!: x", touching="native-bridge/installer/a.rs"))["installer"].next, "1.0.0")
for t in ("docs", "chore", "refactor", "test", "build", "ci", "style", "revert"):
    self.assertEqual(comps(plan_after(f"{t}: x", touching="app/a.kt"))["android"].action, "CARRY")
self.assertEqual(comps(plan_after("misc fixes", touching="app/a.kt"))["android"].action, "CARRY")
# independent previous tags; feat+fix -> 0.3.0 from web-extension-v0.2.3; initial versions + `since` notes
# bootstrap fill: manifest lacks "installer" -> installer RELEASE at its current version
# nothing to release -> any_release False
with self.assertRaises(PlanError): make_plan(...)   # android-v0.2.0 tag, manifest says android 0.1.0 (orphaned)
with self.assertRaises(PlanError): make_plan(shallow_clone, ...)        # Review Focus 4
self.assertEqual(next_release_tag(date(2026, 11, 20), ["2026.11.20", "2026.11.20.2"]), "2026.11.20.3")
self.assertEqual(make_plan(fixture_with_git_tag("2026.11.20", at=old_sha), today=date(2026, 11, 20), ...).release_tag, "2026.11.20.2")  # git-only date tag
# extension-only publish: carried tags (android-v0.1.0, installer-v0.1.0 at older commits) are never listed
self.assertEqual(release_refs(ext_only_plan), ["refs/tags/web-extension-v0.2.0"])
rows = [{"tagName": "0.0.3", "isDraft": False, "isPrerelease": False, "publishedAt": "2026-11-30T00:00:00Z"},
        {"tagName": "2026.11.09", "isDraft": True, ...}, {"tagName": "2026.11.01", "isPrerelease": True, ...},
        {"tagName": "2026.10.30", "isDraft": False, "isPrerelease": False, ...}]
self.assertEqual(latest_combined_release(rows), "2026.10.30")                 # Review Focus 5
notes = render_notes(plan, manifest)
self.assertIn("| Android | 0.2.0 | new |", notes)
self.assertIn("| Web extension | 0.1.0 | unchanged since 2026.11.09 |", notes)
```

- [ ] **Step 3: Write failing asset tests:**
  - `expected_assets("installer", "0.2.0") == ["veles-installer-windows-0.2.0.exe", "veles-installer-macos-0.2.0.dmg"]`.
  - `verify_carry` raises on: a missing mapping, a missing macOS dmg, an extra file, a digest mismatch, and a manifest entry whose name doesn't match `expected_assets`.
  - `stage` output has exactly the seven names, and `SHA256SUMS` matches the files.
  - `verify_draft` raises when one byte changes.
- [ ] **Step 4: Write failing readiness tests:** build `needs` dicts for all seven non-empty RELEASE/CARRY combinations of the three components.
  - Ready when each RELEASE component's jobs are `success`, each CARRY component's are `skipped`, and `ALWAYS` are `success`.
  - Not ready when a RELEASE job is `skipped`, `failure` or `cancelled`, when a CARRY job is `failure`, or when `carry` failed.
- [ ] **Step 5:** `./gradlew scriptsTest` → FAIL (modules missing).
- [ ] **Step 6: Implement** the three modules.
  - `make_plan`: per release node, find the latest `^<prefix>\d+\.\d+\.\d+$` tag in `git tag --merged HEAD` (SemVer sort), then run git-cliff over `release_paths` using the Task 1 commands. With no tag, use `initial-version` and range `since..HEAD`.
  - RELEASE when the next version differs, or when `previous_manifest` lacks the component.
  - Orphaned tags: the latest tag is newer than the manifest version for that component.
  - Shallow repo: `git rev-parse --is-shallow-repository` prints `true`.
- [ ] **Step 7:** `./gradlew scriptsTest` → PASS (also covers Tasks 2–3).
- [ ] **Step 8: Commit:** `git commit -m "build: add git-cliff release planner, asset and readiness tooling"`

### Task 5: Unsigned Android producer

**Files:**
- Modify: `.github/workflows/build-android.yml` (drop `secrets:`, job `env`, decode/signed steps; always `assembleRelease` → `app-release-unsigned.apk`; add optional input `version-tag`: when set, `git tag "$VERSION_TAG" "$COMMIT"` locally before building and assert `androidGitVersion` name equals the tag's version), `.github/workflows/release-build.yml` (stop passing `secrets:`; the surviving manual prerelease `publish` job, retired in Task 10, drops `HAS_KEYSTORE` and always publishes `android-release/app-release-unsigned.apk` with an "(unsigned)" title), `verify/test/workflow-contracts.test.mjs` (Android test at `:310-357`; publisher assertions at `:1320-1330`)

**Interfaces:**
- Produces: artifact `<artifact-name>` = `app-release-unsigned.apk`, `mapping.txt`, `SHA256SUMS`; input `version-tag` (string, optional). Consumed by Tasks 10 and 11.

- [ ] **Step 1: Rewrite the Android contract test.**
  - The workflow has no `secrets:` block and no `KEYSTORE`/`VELES_KEY` text anywhere.
  - It stages exactly `app-release-unsigned.apk`, `mapping.txt` and `SHA256SUMS`.
  - `verify-apk-jni.sh` runs on the unsigned APK.
  - When `version-tag` is set, the tag is created only locally (no `git push`) and the version is asserted.
  - `release-build.yml` passes no secrets to it.
  - The manual publisher (`:1320-1330`) selects only `android-release/app-release-unsigned.apk`, and `HAS_KEYSTORE` appears nowhere.
- [ ] **Step 2:** `node --test verify/test/workflow-contracts.test.mjs` → FAIL.
- [ ] **Step 3:** Edit both workflows.
- [ ] **Step 4:** All three contract suites → PASS.
- [ ] **Step 5: Commit:** `git commit -m "ci(android): make the reusable Android producer unsigned only"`

### Task 6: Android version from `android-v` tags

**Files:**
- Modify: `app/build.gradle.kts:12-14` (or the Task 1 fallback)

- [ ] **Step 1:** Set `androidGitVersion { prefix = "android-v"; codeFormat = "MMNNPP" }`.
- [ ] **Step 2: Verify in a disposable clone:** `git tag android-v1.2.3 && ./gradlew -q :app:androidGitVersion`. Expected: `1.2.3`, code `10203`. Without the tag the name is not `0.0.3`, and `versionCode` falls back to `maxOf(code, 1)`.
- [ ] **Step 3:** `./gradlew testDebugUnitTest` → PASS.
- [ ] **Step 4: Commit:** `git commit -m "build(android): derive version from android-v component tags"`

### Task 7: Web extension version injection

**Files:**
- Create: `web-extension/src/version.ts`, `web-extension/test/version.test.ts`
- Modify:
  - `web-extension/src/manifest.ts:1,94`
  - `web-extension/scripts/package.mjs:98-118`: version from `dist/manifest.json`.
  - `web-extension/test/smoke.test.ts:12`: assert `version` against `resolveExtensionVersion`, not `pkg.version`.
  - `web-extension/test/package-output.test.ts:9`: zip name from the built manifest.
  - `web-extension/package.json`: append `test/version.test.ts` to the explicit `test` list.
  - `.github/workflows/build-web-extension.yml`: optional input `version` mapped to `VELES_EXTENSION_VERSION` on the build/package steps; checkout `fetch-depth: 0`.
  - `verify/test/workflow-contracts.test.mjs`: web test at `:357`.

**Interfaces:**
- Produces: `resolveExtensionVersion(env: Record<string, string | undefined>, describe: () => string | undefined): { version: string; versionName?: string }`, `isChromeVersion(v: string): boolean`; workflow input `version`.

- [ ] **Step 1: Write failing tests:**

```ts
expect(resolveExtensionVersion({ VELES_EXTENSION_VERSION: '0.4.0' }, () => undefined)).toEqual({ version: '0.4.0' });
expect(resolveExtensionVersion({}, () => 'web-extension-v0.2.0-3-gabc1234')).toEqual({ version: '0.2.0', versionName: '0.2.0-3-gabc1234' });
expect(resolveExtensionVersion({}, () => 'web-extension-v0.2.0')).toEqual({ version: '0.2.0' });
expect(resolveExtensionVersion({}, () => undefined)).toEqual({ version: '0.0.1' });
for (const bad of ['0.0.0', '01.2.3', '1.2.x', '1.65536.0', '1.2.3.4.5', ''])
  expect(() => resolveExtensionVersion({ VELES_EXTENSION_VERSION: bad }, () => undefined)).toThrow();
expect(isChromeVersion('65535.0.1')).toBe(true);
```

- [ ] **Step 2:** `cd web-extension && npx vitest run test/version.test.ts` → FAIL.
- [ ] **Step 3: Implement.** `describe` runs `git describe --tags --match 'web-extension-v*'` and returns `undefined` on error. `manifest.ts` sets `version` and, when present, `version_name`. `package.mjs` reads `dist/manifest.json`.
- [ ] **Step 4: Verify:**
  - `npm test && npm run build && npm run test:bundle` → PASS.
  - `VELES_EXTENSION_VERSION=0.4.0 npm run build && VELES_EXTENSION_VERSION=0.4.0 npm run package` → `build/web-extension/veles-extension-0.4.0.zip`, whose manifest has `"version": "0.4.0"`.
  - `npm run format:check && npm run lint && npm run typecheck` → PASS.
- [ ] **Step 5:** Update the web workflow contract test (input, env mapping, `fetch-depth: 0`). All three suites → PASS.
- [ ] **Step 6: Commit:** `git commit -m "feat(web-extension): take manifest version from component tag or injected value"`

### Task 8: Installer and host version injection

**Files:**
- Create: `native-bridge/host/src/version.rs`
- Modify:
  - `native-bridge/host/src/{lib.rs,host.rs:260,283}`
  - `native-bridge/installer/scripts/assemble_unsigned.py`: `descriptor_document`, the `VERSION` write at `:462`, the `assemble` CLI.
  - `native-bridge/installer/scripts/test_assemble_unsigned.py`
  - `build.gradle.kts:1496-1499`: `releaseVersion` → effective `installerVersion`. Pass it to the host task env (`:1585-1595`), uninstaller `-Version` (`:1631-1635`) and the assemble invocation, each with `inputs.property("installerVersion", installerVersion)`.
  - `native-bridge/installer/config/release.schema.json`: description only.
  - `.github/workflows/build-native-{windows,macos}.yml`: optional input `version` → `VELES_INSTALLER_VERSION`, release kind only.
  - `native-bridge/installer/scripts/test_build_contract.py` (`check_producer_workflow`) and `verify/test/workflow-contracts.test.mjs`.

**Interfaces:**
- Produces:
  - `pub const HOST_VERSION: &str`: `option_env!("VELES_HOST_VERSION")`, else `env!("CARGO_PKG_VERSION")`.
  - `descriptor_document(config: dict, commit: str, payload_id: str, version: str | None = None) -> bytes`.
  - `assemble … [--version X.Y.Z]`, used for the descriptor and `VERSION` in both passes.
  - The producers' `version` input (consumed by Task 11).

- [ ] **Step 1: Write failing tests.**
  - Rust: `host_version_defaults_to_package_version`.
  - Python:
    - `descriptor_document(cfg, C, P, version="0.2.0")` has `"version": "0.2.0"`.
    - `version="1.2"` raises `ValueError`.
    - Omitting `version` keeps the config value.
    - An `assemble --version 0.2.0` run writes `0.2.0` to both `descriptor.json` and macOS `VERSION`.
    - The provenance `descriptor_digest` equals the injected descriptor's sha256.
  - Contract: the producers export `VELES_INSTALLER_VERSION` only under `build-kind == 'release'`.
- [ ] **Step 2:** `./gradlew bridgeHostTest bridgeInstallerScriptsTest` → FAIL.
- [ ] **Step 3: Implement.** In Gradle: `val installerVersion = providers.environmentVariable("VELES_INSTALLER_VERSION").orElse(configVersion)` for the production channel; the fixture channel uses `configVersion`.
- [ ] **Step 4: Verify.**
  - `./gradlew bridgeHostFormat bridgeHostLint bridgeHostTest bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest bridgeInstallerScriptsTest` → PASS.
  - On Linux, the installer build-contract test asserts statically that the host, uninstaller and assemble tasks declare `inputs.property("installerVersion", …)` and pass it through (negative mutation: dropping the declaration fails).
  - Release-artifact check (executable, no native machine needed now): `publish-release.yml`'s `draft` job reads `descriptor.json` from each `verified-<os>` artifact (the same file `release_gate.py bundle` consumes, whose digest the signing stages authorize) and asserts its `version` equals the planned installer version (Task 11 adds a contract test for this step). Together with the static input declarations, this proves the shipped installers carry the injected version.
  - `git diff --exit-code -- '*Cargo.toml' '*Cargo.lock'`.
- [ ] **Step 5:** All three contract suites → PASS.
- [ ] **Step 6: Commit:** `git commit -m "feat(installer): inject one installer version into descriptor, bundle, uninstaller and host"`

### Task 9: CI component tree, PR title check, commit guard

**Files:**
- Create: `.github/workflows/check-{android,web-extension,rust,protocol,native-host,installer,bridge-scaffold,tooling}.yml`, `.github/workflows/pr-title.yml`, `docs/ci.md`
- Modify:
  - `.github/workflows/ci.yml` (rewrite)
  - `verify/test/workflow-contracts.test.mjs`
  - `native-bridge/host/scripts/test_build_contract.py`: `check_host_workflow` targets `check-native-host.yml`; `check_ci` requires the `native-host` caller.
  - `native-bridge/installer/scripts/test_build_contract.py`: `check_host_workflow_installer` targets `check-installer.yml`.
  - `verify/check-remote-code.mjs:97-106` scopes: replace `build-native-host.yml` with the two check workflows; add `ci.yml`, `pr-title.yml`, `scripts`, `.githooks`, `web-extension/vite.config.ts`; remove the obsolete `web-extension/manifest.json` (generated by `vite.config.ts`; its source `web-extension/src/manifest.ts` stays covered by `web-extension/src`).
- Delete: `.github/workflows/build-native-host.yml`

**Interfaces:**
- Consumes: `components.py` (Task 3), `commitlint.py` (Task 2), `scriptsTest` (Task 4).
- Produces: check names per specs/ci-pipeline; required names `ci-ok` and `pr-title`; CI artifacts `native-host-windows`/`native-host-macos`.

- [ ] **Step 1: Write failing contract tests.**
  - `ci.yml` has one caller per node from `components.json`, each `if: needs.changes.outputs.<node> == 'true'`. `changes` runs `components.py check` and `changed`. `ci-ok` has `if: always()`, `needs` covering every node and `changes`, and fails on `failure`/`cancelled`.
  - **Command inventory**: a literal list in the test of every pre-change command (`spotlessCheck`, `detekt`, the raw-Log grep, `lintDebug`, `testDebugUnitTest`, `connectedDebugAndroidTest`, `protocolCheck`, `npm run format:check`, `npm run lint`, `npm run typecheck`, `npm test`, `npm run test:bundle`, `npm run test:browser`, `rustFormat`, `rustLint`, `rustTest`, `verify-apk-jni.sh`, `assert-clean.sh`, `bridgeHostToolchainCheck`, `bridgeHostFormat`, `bridgeHostLint`, `bridgeHostTest`, `bridgeHostBuild`, `test_build_contract.py`, `bridgeInstallerFormat`, `bridgeInstallerLint`, `bridgeInstallerTest`, `bridgeInstallerScriptsTest`, `bridgeFormat`, `bridgeLint`, `bridgeTypecheck`, `bridgeNpmTest`, `bridgeRustFormat`, `bridgeRustLint`, `bridgeRustTest`, `workflow-contracts.test.mjs`). Each appears in exactly one `check-*.yml`.
  - **Clean-contract order** in `check-rust.yml` `jni-integration`: `assembleDebug` < `verify-apk-jni.sh` < `npm run build` < `./gradlew clean` < `assert-clean.sh` < second `assembleDebug` < second `verify-apk-jni.sh` (by step index).
  - **`pr-title.yml`**:
    - Types exactly `[opened, edited, reopened, synchronize]`; `contents: read`.
    - Checks out `github.event.pull_request.base.sha`.
    - No `run:` contains `github.event.pull_request.title` or `.body` (Review Focus 1).
    - Has the head fallback with `::warning::`.
    - No other workflow lists `edited`.
  - **Python suites**: the host suite's `test_host_workflow_*` read `check-native-host.yml` with the same mutations. The installer suite's `test_host_check_workflow_runs_the_installer_checks_on_every_platform` reads `check-installer.yml`.
  - **Remote-code scopes**: no scope names a missing file (add an assertion that every scope exists).
- [ ] **Step 2:** The three suites → FAIL.
- [ ] **Step 3: Create the `check-*.yml` workflows** (`on: workflow_call`, `contents: read`), moving steps verbatim.
  - `check-rust.yml` `jni-integration` keeps the whole sequence from `ci.yml:193-202` in one job.
  - `check-native-host.yml` and `check-installer.yml` take their matrices and toolchain setup from `build-native-host.yml`.
  - `check-tooling.yml` `unit-tests` runs the Node contracts and `./gradlew scriptsTest`. Each Python `test_build_contract.py` suite keeps exactly one owner: the host suite in `native-host / build-contract`, the installer suite inside `installer / scripts-tests` (`bridgeInstallerScriptsTest`). Workflow edits are global paths, so both nodes run whenever a workflow changes. The command inventory counts commands per owner, so each still appears exactly once.
- [ ] **Step 4: Rewrite `ci.yml`.**
  - `changes` (`fetch-depth: 0`): `BASE` from env, either `git merge-base "origin/$BASE_REF" HEAD` (PR) or `$BEFORE` (push).
  - One caller per node; `supply-chain` uses `build-supply-chain.yml`.
  - `commit-guard` (push only): `git rev-list "$BEFORE..$AFTER"` → `commitlint.py --message`.
  - `ci-ok`.
- [ ] **Step 5: Create `pr-title.yml`.**
  - Check out the base SHA.
  - If `scripts/commitlint.py` is absent, `git fetch` the head SHA and `git show "$HEAD_SHA:scripts/commitlint.py" > "$RUNNER_TEMP/commitlint.py"`, then emit `::warning::bootstrap: validator taken from the PR head`.
  - Run with `PR_TITLE`/`PR_BODY` from `env:`.
- [ ] **Step 6:** Delete `build-native-host.yml`, update the remote-code scopes, and write `docs/ci.md` (tree, selection rules, future-work gaps).
- [ ] **Step 7:** The three suites → PASS; `python3 -I scripts/components.py check` → 0; `node verify/check-remote-code.mjs` scope assertion → PASS.
- [ ] **Step 8: Commit:** `git commit -m "ci: split CI into change-gated component checks with ci-ok and pr-title gates"`

### Task 10: Label-driven PR builds and changed-only master builds

**Files:**
- Modify:
  - `.github/workflows/release-build.yml` (rewrite)
  - `.github/workflows/build-pr-installers.yml`: labels and header comment.
  - `verify/test/workflow-contracts.test.mjs`: replace `release-build caller graph …` at `:1206`; rewrite `reusable component workflows referenced by the caller graph` (`:1457`) so `release-build.yml` must call `build-android.yml`, `build-web-extension.yml` and `build-rust.yml`, `ci.yml` must call `build-supply-chain.yml`, and every referenced file stays `workflow_call`; remove the manual-publisher assertions (`:1320-1370`).
  - `native-bridge/installer/scripts/test_build_contract.py`: rewrite `check_release_build` (`:615`) and `test_release_build_*`/`test_changed_publication_conditions_are_rejected` (`:1187-1230`) to the new invariants.

**Interfaces:**
- Consumes: `components.py changed`, `release_plan.py latest|plan`, `installGitCliff`, producers `build-android.yml` (no secrets), `build-web-extension.yml` (`version` unset), `build-rust.yml`.

- [ ] **Step 1: Write failing tests.**
  - `pull_request` types exactly `[opened, reopened, synchronize, labeled, unlabeled]`.
  - No `workflow_dispatch`, no `contents: write`, no `secrets:`.
  - The android job condition is `always() && ((pull_request && contains(labels, 'build-android')) || (push && needs.changes.outputs.android == 'true'))`; the same shape holds for web; rust is push-only.
  - `preview` is push-only, runs `release_plan.py plan` with `--summary "$GITHUB_STEP_SUMMARY"`, and has no secrets.
  - The strings `'release-build'`, `windows-installer-build` and `mac-installer-build` appear in no workflow.
  - The installer suite: release-build calls no native producer and has no publication.
  - `build-pr-installers.yml` uses the new labels and keeps `native-installer-debug`.
- [ ] **Step 2:** The suites → FAIL.
- [ ] **Step 3: Rewrite `release-build.yml`.**
  - Jobs: `changes` (push), `android`, `web-extension`, `rust`.
  - `preview` (push): checkout `fetch-depth: 0`; `./gradlew installGitCliff`; `gh release list --limit 100 --json tagName,isDraft,isPrerelease,publishedAt > releases.json`; `TAG=$(release_plan.py latest --releases-json releases.json)`; download `release-manifest.json` from `$TAG` if present, else `echo '{}'`; then `release_plan.py plan --releases-json releases.json --manifest-json manifest.json --out plan.json --summary "$GITHUB_STEP_SUMMARY"`.
  - `GH_TOKEN: ${{ github.token }}`, `contents: read`.
  - Rename the labels in `build-pr-installers.yml`.
- [ ] **Step 4:** The suites → PASS.
- [ ] **Step 5: Commit:** `git commit -m "ci: label-driven PR builds and changed-only master builds with release preview"`

### Task 11: Owner-only publish-release workflow

**Files:**
- Create: `.github/workflows/publish-release.yml`, `docs/releasing.md`
- Modify:
  - `verify/test/workflow-contracts.test.mjs`: the signing-boundary tests (`:599`, `:607`, `:825`) retarget to `publish-release.yml`; the per-platform gate test (`:864`) becomes "installer RELEASE runs both platforms and the gate with `PLATFORM: both`"; the tag-release test (`:1372`, reads `release.yml`) is ported to `publish-release.yml` keeping its integrity assertions (never-cancel concurrency, single build per component, provenance attestation, `SHA256SUMS`, draft digest comparison before publication); the reusable-callers test additionally requires `publish-release.yml` to call `build-android.yml`, `build-web-extension.yml`, `build-native-windows.yml`, `build-native-macos.yml` and `build-supply-chain.yml`.
  - `native-bridge/installer/scripts/test_build_contract.py`: `check_gate_boundary` (`:645`) and its tests (`:1231-1270`) retarget to `publish-release.yml` with `sign-windows`, `sign-macos` and `sign-android` as the only credentialed jobs; `check_supply_chain` (`:733-756`, requiring `release-native-installers.yml` in the remote-code scan at `:749`) and its negative mutation (`:1307`) switch to `publish-release.yml`; `test_platform_selection_does_not_require_the_unselected_target` (`:1234`) becomes a test that no job references `inputs.platform` and every installer job's `if:` uses `needs.plan.outputs.installer == 'RELEASE'`.
  - `verify/check-remote-code.mjs` scopes: `release-native-installers.yml` → `publish-release.yml`.
- Delete: `.github/workflows/release.yml`, `.github/workflows/release-native-installers.yml`

**Interfaces:**
- Consumes: `release_plan.py`, `release_assets.py`, `release_ready.py` (Task 4); `build-android.yml` with `version-tag` (Task 5); `build-web-extension.yml` with `version` (Task 7); native producers with `version` (Task 8); `release_gate.py` stages (unchanged).

- [ ] **Step 1: Write failing tests.**
  - **Triggers and concurrency:** the only trigger is `workflow_dispatch`; `concurrency.group == publish-release` with `cancel-in-progress: false`.
  - **Guard:** every job transitively needs `guard` and repeats the actor/ref/SHA guard.
  - **Permissions:** workflow default `contents: read`; `draft` has exactly `contents: write`, `id-token: write`, `attestations: write`; `publish` has `contents: write`; no other job has any write permission.
  - **Installer platforms:** no `inputs.platform` anywhere; every installer job gates on `needs.plan.outputs.installer == 'RELEASE'`; the ported jobs and `gate-installer` set `PLATFORM: both`.
  - **Secrets:**
    - The `KEYSTORE_*` names appear only in `sign-android`, which has `environment: android-release`, is not a `uses:` caller, and compares the `apksigner verify --print-certs` SHA-256 with `vars.VELES_ANDROID_CERT_SHA256`.
    - The native credential names appear only in `sign-windows`/`sign-macos` with `environment: native-release` (ported invariants).
  - **Gate:** `gate-installer` runs `release_gate.py bundle` and `validate`. `ready` runs with `if: always()` and calls `release_ready.py` with `toJSON(needs)`.
  - **Ordering:** `draft` needs `ready`, runs no `git push`, and checks the embedded installer descriptor and extension manifest versions against the plan.
  - **Publishing:**
    - `publish` has `environment: release-publish`.
    - It runs `release_assets.py verify-draft` before `git push --atomic`, and `git push --atomic` before the draft=false PATCH.
    - It re-checks the combined date tag before publishing (absent or at `github.sha`) and verifies it resolves to `github.sha` afterwards.
    - It computes the tag list only from RELEASE components in `plan.json`, treats a RELEASE tag already at `github.sha` as success, fails on a RELEASE tag elsewhere, and never references CARRY components' tags.
  - **Isolation:** no other workflow contains `gh release create`, `gh release edit`, `git push --tags` or `git push … refs/tags`.
  - **Retired workflows:** `release.yml` and `release-native-installers.yml` are absent.
- [ ] **Step 2:** The suites → FAIL.
- [ ] **Step 3: Implement the workflow** (design.md D4):
  - `guard`.
  - `plan`: `fetch-depth: 0`, `installGitCliff`, `gh release list`, previous manifest download. Outputs per component; fails on `PlanError` or `any_release == false`.
  - `supply-chain`: `build-supply-chain.yml`.
  - `build-android`: `build-android.yml` with `version-tag: android-v<next>`, when RELEASE.
  - `sign-android` (`environment: android-release`, build-tools 36.0.0):
    1. Download the artifact and run `sha256sum -c`.
    2. Decode the keystore into `$RUNNER_TEMP`.
    3. `apksigner sign --v1-signing-enabled false --v2-signing-enabled true --v3-signing-enabled true`.
    4. `apksigner verify --print-certs`, then compare the SHA-256 digest with the variable.
    5. `verify-apk-jni.sh` on the signed APK, write new `SHA256SUMS`, upload `android-signed`.
  - `build-web-extension`: `build-web-extension.yml` with `version`.
  - `build-/verify-/sign-windows|macos`: moved from `release-native-installers.yml` with `build-kind: release` and `version`. Replace every `inputs.platform` predicate with `needs.plan.outputs.installer == 'RELEASE'` (both platforms always) and every `PLATFORM: ${{ inputs.platform }}` with `PLATFORM: both`.
  - `gate-installer`: the ported `gate` with `PLATFORM: both` and both descriptor downloads unconditional.
  - `carry`: for each CARRY component, `gh release download "$PREV" --pattern <name>` per manifest entry, then `release_assets.py verify-carry`.
  - `ready`.
  - `draft` (`permissions: contents: write, id-token: write, attestations: write`): download the signed and built artifacts; for installer RELEASE, assert each `verified-<os>/descriptor.json` `version` (the descriptor the gate and signers authorized) equals the planned installer version and the extension zip's `manifest.json` `version` equals the planned extension version; extract `setup.exe`/the dmg from the signed trees, run `release_assets.py stage`, then `actions/attest-build-provenance` on the new assets. `gh release create "$TAG" --draft --target "$GITHUB_SHA" --title "Veles $TAG" --notes-file notes.md <assets>`. Upload `expected-digests.json` as an artifact.
  - `publish` (`environment: release-publish`):
    1. Resolve the draft by tag via `gh release list` (including drafts) and download its assets by release id.
    2. `release_assets.py verify-draft`.
    3. For each ref from `release_plan.py tags --plan-json plan.json` (RELEASE components only; CARRY tags are left untouched): `git ls-remote --tags origin refs/tags/<c>-v<next>`. Absent → `git tag <c>-v<next> "$GITHUB_SHA"` locally and add it to the push list. Present at `$GITHUB_SHA` → skip. Present elsewhere → fail.
    4. `git push --atomic origin <refs>` (skipped when the list is empty on a re-run).
    5. Re-check the combined tag: `git ls-remote --tags origin "refs/tags/$TAG"` must be absent or at `$GITHUB_SHA`; otherwise fail.
    6. PATCH `draft=false`, `tag_name=$TAG`, `target_commitish=$GITHUB_SHA` (no-op if already published), then assert `git ls-remote` resolves `refs/tags/$TAG` to `$GITHUB_SHA`.
- [ ] **Step 4:** Delete `release.yml` and `release-native-installers.yml`, and update the remote-code scopes.
- [ ] **Step 5: Write `docs/releasing.md`:**
  - Commit rules and the preview.
  - Owner runbook: dispatch, approve `android-release`/`native-release`, review the draft, approve `release-publish`.
  - Bootstrap.
  - Recovery: re-run the failed `publish` job in the same run, or delete orphaned tags plus the draft.
  - Rollback, plus the owner settings.
- [ ] **Step 6:** The three suites → PASS; `python3 -I -m unittest discover -s native-bridge/installer/scripts` → PASS; `./gradlew scriptsTest` → PASS.
- [ ] **Step 7: Commit:** `git commit -m "ci: add owner-only publish-release workflow and retire legacy release paths"`

### Task 12: Project documentation

**Files:**
- Modify: `CLAUDE.md` (Build & Test Commands: `scriptsTest`, `installGitCliff`, `installGitHooks`; workflow ownership table; Versioning & Releases), `README.md`

- [ ] **Step 1:** Update both. Then `grep -rn "release-native-installers\|build-native-host.yml\|release-build label\|androidgitversion.*X.Y.Z" CLAUDE.md README.md docs/` → no stale hits.
- [ ] **Step 2: Commit:** `git commit -m "docs: describe component versioning, CI tree and owner release flow"`

### Task 13: Final verification and owner setup

- [ ] **Step 1:** `./gradlew spotlessCheck detekt lintDebug` plus the raw-Log guard from `check-android.yml` → all succeed (`spotlessApply` and recheck if needed).
- [ ] **Step 2:** `./gradlew scriptsTest testDebugUnitTest bridgeHostTest bridgeInstallerTest bridgeInstallerScriptsTest`, the three contract suites, and `cd web-extension && npm test` → PASS.
- [ ] **Step 3:** PR description checklist for `raidenyn`:
  1. Squash title `PR_TITLE`, body `PR_BODY`.
  2. Environment `android-release`: reviewer `raidenyn`, branch policy `master`. Move `KEYSTORE_BASE64`, `KEYSTORE_PASSWORD`, `KEY_ALIAS` and `KEY_PASSWORD` there and delete them from repository secrets. Add variable `VELES_ANDROID_CERT_SHA256` from the real keystore certificate.
  3. Environment `release-publish` (reviewer `raidenyn`).
  4. Labels: create `build-android`, `build-web-extension`, `build-windows-installer`, `build-macos-installer`; delete `release-build`, `windows-installer-build`, `mac-installer-build`.
  5. After this PR merges, the `master` ruleset: PR required, `pr-title` + `ci-ok`, linear history, no force push or deletion.
- [ ] **Step 4:** After merge, the owner dispatches `publish-release`. Expected:
  - The plan shows all three components RELEASE at `0.1.0`.
  - After the approvals: tags `android-v0.1.0`, `web-extension-v0.1.0`, `installer-v0.1.0` and release `YYYY.MM.DD` with the seven assets and the notes table.
